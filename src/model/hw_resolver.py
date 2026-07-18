"""
HW Resolver - DVFS & Voltage Domain resolution.

Determines clock frequency and voltage for each IP based on:
1. Scenario workload (pixels, fps, ppc) → required_clock
2. sw_margin → required_clock = pixels×fps / (1-sw_margin) / ppc
3. DVFS table → set_clock (minimum level meeting required_clock)
4. Same DVFS group → highest required_clock wins
5. Same VDD domain → highest voltage wins

Power Calculation:
    Active Power = Σ(Unit_Power[i] × resolution_MP[i] × (V/0.71)² × (FPS/30))
    Idle Power   = IDC × (V/0.71)²
    
    where Unit_Power is in [mW/MP@30fps], V is set_voltage in [V]
"""

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .hw_info import HWInfoDB, IPInfo, DVFSTable, DVFSLevel


# Reference voltage for power scaling (0.71V in mV)
REFERENCE_VOLTAGE_MV = 710.0
REFERENCE_FPS = 30.0


@dataclass
class ResolvedIPConfig:
    """Final resolved configuration for a single IP.
    
    Attributes:
        ip_name: IP name (matching hw.yaml name)
        mode: Operating mode
        required_clock: Required clock with sw_margin [MHz]
        set_clock: Actual clock from DVFS table [MHz]
        dvfs_level: Selected DVFS level number
        dvfs_group: DVFS table name
        required_voltage: Voltage for set_clock level from DVFS table [mV]
        set_voltage: Final voltage after VDD domain alignment [mV]
        vdd: Voltage domain name
        unit_power: Power coefficient [mW/MP@30fps]
        ppc: Pixels Per Clock
        idc: Idle power coefficient
        input_resolution_mp: Input resolution in megapixels
        fps: Scenario FPS
        req_volt_power: Dynamic power at required_voltage [mW]
        set_volt_power: Dynamic power at set_voltage (VDD-aligned) [mW]
        vdd_leader: IP name(s) that determine this VDD domain's voltage (comma-separated)
    """
    ip_name: str
    mode: str = "Normal"
    required_clock: float = 0.0
    set_clock: float = 0.0
    dvfs_level: int = -1
    dvfs_group: str = ""
    required_voltage: float = 0.0
    set_voltage: float = 0.0
    vdd: str = ""
    unit_power: float = 0.0
    ppc: int = 1
    idc: float = 0.0
    input_resolution_mp: float = 0.0
    fps: float = 30.0
    req_volt_power: float = 0.0
    set_volt_power: float = 0.0
    vdd_leader: str = ""
    manual_clock: float = 0.0            # manual clock override [MHz] (0 = auto)
    level_voltage: float = 0.0           # voltage needed for the actual set_clock
                                         # level [mV] (≥ required_voltage when the
                                         # clock was raised by group alignment)
    
    def _calc_dynamic_power(self, voltage_mv: float) -> float:
        """Calculate dynamic power at a given voltage [mW].
        
        Formula: unit_power × resolution_MP × (V/0.71V)² × (FPS/30)
        V in Volts (voltage_mv / 1000)
        """
        if self.unit_power <= 0 or self.input_resolution_mp <= 0 or voltage_mv <= 0:
            return 0.0
        v_scale = (voltage_mv / REFERENCE_VOLTAGE_MV) ** 2
        fps_scale = self.fps / REFERENCE_FPS
        return self.unit_power * self.input_resolution_mp * v_scale * fps_scale
    
    def get_active_power(self) -> float:
        """Calculate active power using set_voltage (VDD-aligned) [mW]."""
        return self._calc_dynamic_power(self.set_voltage)
    
    def get_idle_power(self) -> float:
        """Calculate idle power [mW]. (static_power = 0 for now)"""
        return 0.0
    
    def get_total_power(self) -> float:
        """Calculate total power [mW] = dynamic + static."""
        return self.get_active_power() + self.get_idle_power()


class HWResolver:
    """DVFS & Voltage domain resolver.
    
    Usage:
        hw_info_db = create_hw_info_db(info_path, dvfs_path)
        resolver = HWResolver(hw_info_db, asv_group=4)
        configs = resolver.resolve_scenario(hw_registry, scenario, scenario_config)
        resolver.apply_to_hw(hw_registry, configs)
        print(resolver.get_exploration_report(configs))
    
    Extension Points:
        # === Custom Power Calculation ===
        # Override per-IP power calculation:
        #
        # def custom_power(ip: IPNode, duration: float, fps: float) -> float:
        #     resolution_mp = ip.workload_pixels / 1e6
        #     v_scale = (ip.set_voltage / 710.0) ** 2
        #     active = ip.unit_power * resolution_mp * v_scale * (fps / 30.0)
        #     idle = ip.idc * v_scale
        #     return (active + idle) * duration  # mJ
        #
        # ip_node._power_calculator = custom_power
        
        # === Custom Runtime Calculation ===
        # Override per-IP runtime calculation:
        #
        # def custom_runtime(ip: IPNode, workload: Dict) -> float:
        #     pixels = workload['width'] * workload['height']
        #     return pixels / (ip.set_clock * 1e6 * ip.ppc)
        #
        # ip_node._runtime_calculator = custom_runtime
    """
    
    def __init__(self, hw_info_db: HWInfoDB, asv_group: int = 4):
        """Initialize resolver.
        
        Args:
            hw_info_db: Loaded HW info database
            asv_group: ASV group for voltage lookup (default: 4)
        """
        self.db = hw_info_db
        self.asv_group = asv_group
    
    def resolve_scenario(
        self,
        hw_registry: Dict[str, Any],
        scenario: Any,
        scenario_config: dict
    ) -> Dict[str, ResolvedIPConfig]:
        """Resolve clock/voltage for all IPs based on scenario workload.
        
        Steps:
            1. For each IP, get mode → IPInfo (unit_power, ppc, dvfs_group)
            2. Calculate required_clock = pixels×fps / (1-sw_margin) / ppc
            3. Same DVFS group → align to highest required_clock
            4. DVFS table → select set_clock (min level ≥ required)
            5. Same VDD domain → align to highest voltage
        
        Args:
            hw_registry: Dict of hw_name → HWNode
            scenario: ScenarioGraph instance
            scenario_config: Raw scenario config dict (for fps, sw_margin, asv_group)
            
        Returns:
            Dict of ip_name → ResolvedIPConfig
        """
        from .hw_nodes import IPNode, SensorNode
        
        # Extract scenario parameters
        scenario_data = scenario_config.get('scenario', scenario_config)
        sw_margin = float(scenario_data.get('sw_margin', 0.15))
        fps = self._get_fps(hw_registry, scenario_data)
        
        resolved: Dict[str, ResolvedIPConfig] = {}
        
        # Step 1: Calculate required_clock for each IP
        for hw_name, hw_node in hw_registry.items():
            if not isinstance(hw_node, IPNode):
                continue
            
            # Get IP info from CSV
            ip_info = self._get_ip_info_for_node(hw_name, hw_node, scenario)
            if ip_info is None:
                continue
            
            # Calculate input resolution
            input_pixels = self._get_input_pixels(hw_name, scenario)
            input_resolution_mp = input_pixels / 1e6
            
            # Calculate required clock
            if ip_info.ppc > 0 and fps > 0 and input_pixels > 0:
                # req_freq = resolution × FPS / (1 - SW_MARGIN) / PPC
                required_clock = (input_pixels * fps) / (1.0 - sw_margin) / (ip_info.ppc * 1e6)  # MHz
            else:
                required_clock = 0.0
            
            resolved[hw_name] = ResolvedIPConfig(
                ip_name=hw_name,
                mode=ip_info.mode,
                required_clock=required_clock,
                dvfs_group=ip_info.dvfs_group,
                vdd=ip_info.vdd,
                unit_power=ip_info.unit_power,
                ppc=ip_info.ppc,
                idc=ip_info.idc,
                input_resolution_mp=input_resolution_mp,
                fps=fps
            )
        # Step 1.5: CSIS clock correction for sensor-OTF-connected IPs
        #   req_csis_clock depends on sensor phy_type, sbwc, mipi_speed, bitwidth
        #   Apply: req_freq = max(req_freq, req_csis_clock) for OTF-connected IPs
        resolved_sensor = getattr(scenario, '_resolved_sensor', {})
        sensor_mipi_speed = resolved_sensor.get('sensor_mipi_speed', 0)
        sensor_phy_type = resolved_sensor.get('sensor_phy_type', 'DPHY')
        sensor_sbwc = resolved_sensor.get('sensor_sbwc', 'disable')
        sensor_bitwidth = resolved_sensor.get('sensor_bitwidth', 12)
        
        if sensor_mipi_speed > 0:
            # Find all IPs connected to sensor via OTF chain
            otf_connected_hws = self._get_sensor_otf_connected_hws(scenario)
            
            for hw_name in otf_connected_hws:
                if hw_name not in resolved:
                    continue
                config = resolved[hw_name]
                ppc = config.ppc
                if ppc <= 0:
                    continue
                
                # Calculate req_csis_clock (MHz)
                if sensor_phy_type.upper() == "CPHY":
                    if sensor_sbwc == "enable":
                        req_csis_clock = sensor_mipi_speed * 16 / 7 * 3 / ppc * 1000 / sensor_bitwidth
                    else:
                        req_csis_clock = sensor_mipi_speed * 16 / 7 * 3 / (sensor_bitwidth * ppc) * 1000
                else:  # DPHY
                    if sensor_sbwc == "enable":
                        req_csis_clock = sensor_mipi_speed * 4 / ppc * 1000 / sensor_bitwidth
                    else:
                        req_csis_clock = sensor_mipi_speed * 4 / (sensor_bitwidth * ppc) * 1000
                
                # Apply correction: max(req_freq, req_csis_clock)
                if req_csis_clock > config.required_clock:
                    config.required_clock = req_csis_clock
        
        # Step 2: Align same DVFS group to highest required_clock
        dvfs_groups: Dict[str, List[str]] = defaultdict(list)
        for ip_name, config in resolved.items():
            if config.dvfs_group:
                dvfs_groups[config.dvfs_group].append(ip_name)
        
        for group_name, ip_names in dvfs_groups.items():
            max_required = max(resolved[n].required_clock for n in ip_names)
            for ip_name in ip_names:
                resolved[ip_name].required_clock = max_required
        
        # Step 2.5: Apply manual_clock overrides from scenario ip_settings
        #   Only the specific IP's required_clock is raised — peers keep their
        #   natural required_clock so the report clearly shows the penalty.
        manual_clocks = getattr(scenario, '_manual_clocks', {})
        for ip_name, manual_mhz in manual_clocks.items():
            if ip_name in resolved and manual_mhz > 0:
                resolved[ip_name].manual_clock = manual_mhz
                if manual_mhz > resolved[ip_name].required_clock:
                    resolved[ip_name].required_clock = manual_mhz
        
        # Step 3: Resolve set_clock from DVFS tables (per-IP)
        for ip_name, config in resolved.items():
            if not config.dvfs_group:
                continue
            
            dvfs_table = self.db.get_dvfs_table(config.dvfs_group)
            if dvfs_table is None:
                print(f"[Warning] DVFS table '{config.dvfs_group}' not found for '{ip_name}'")
                continue
            
            level = dvfs_table.find_min_level_for_speed(config.required_clock, asv_group=self.asv_group)
            if level is None:
                # Use highest available speed
                if dvfs_table.levels:
                    level = max(dvfs_table.levels, key=lambda l: l.speed)
                    print(f"[Warning] Required clock {config.required_clock:.1f} MHz exceeds "
                          f"max DVFS speed {level.speed:.1f} MHz for '{ip_name}' "
                          f"(group: {config.dvfs_group}). Using max speed.")
                else:
                    continue
            
            config.set_clock = level.speed
            config.dvfs_level = level.level
            config.required_voltage = dvfs_table.get_voltage(level, self.asv_group)
            config.level_voltage = config.required_voltage
        
        # Step 3.5: Align set_clock within DVFS groups (shared clock domain)
        #   All IPs in the same DVFS group must share one clock.
        #   Use the highest set_clock (e.g. from manual_clock) for all peers.
        for group_name, ip_names in dvfs_groups.items():
            max_set_clock = max(resolved[n].set_clock for n in ip_names)
            if max_set_clock <= 0:
                continue
            # Find the level corresponding to max_set_clock
            dvfs_table = self.db.get_dvfs_table(group_name)
            if dvfs_table is None:
                continue
            target_level = dvfs_table.find_min_level_for_speed(max_set_clock, asv_group=self.asv_group)
            if target_level is None:
                continue
            target_voltage = dvfs_table.get_voltage(target_level, self.asv_group)
            for ip_name in ip_names:
                c = resolved[ip_name]
                if c.set_clock < max_set_clock:
                    c.set_clock = max_set_clock
                    c.dvfs_level = target_level.level
                    # Keep c.required_voltage from its own req_clock (Step 3)
                    # so reports show the penalty; level_voltage tracks the
                    # voltage actually needed to run the aligned clock and
                    # is what VDD alignment (Step 4) must satisfy.
                    c.level_voltage = target_voltage
        
        # Step 4: Align same VDD domain to highest voltage
        vdd_groups: Dict[str, List[str]] = defaultdict(list)
        for ip_name, config in resolved.items():
            if config.vdd:
                vdd_groups[config.vdd].append(ip_name)
        
        def _effective_voltage(c: ResolvedIPConfig) -> float:
            # The voltage a config actually needs: its own requirement, or
            # the (possibly higher) voltage of the group-aligned set_clock.
            return max(c.required_voltage, c.level_voltage)

        for vdd_name, ip_names in vdd_groups.items():
            # Find all IPs at highest effective voltage (VDD leaders)
            max_voltage = max(_effective_voltage(resolved[n]) for n in ip_names)
            leader_ips = sorted([n for n in ip_names
                                 if _effective_voltage(resolved[n]) == max_voltage])
            leader_str = ','.join(leader_ips)
            for ip_name in ip_names:
                resolved[ip_name].set_voltage = max_voltage
                resolved[ip_name].vdd_leader = leader_str

        # IPs with no VDD group: use their own effective voltage
        for ip_name, config in resolved.items():
            if not config.vdd:
                config.set_voltage = _effective_voltage(config)
                config.vdd_leader = ip_name

        # Safety check: every IP must end up at a voltage sufficient for its
        # actual set_clock level (guards against alignment corner cases)
        for ip_name, config in resolved.items():
            if config.level_voltage > 0 and config.set_voltage < config.level_voltage:
                print(f"[Warning] {ip_name}: set_voltage {config.set_voltage:.1f}mV "
                      f"< level voltage {config.level_voltage:.1f}mV for "
                      f"set_clock {config.set_clock:.1f}MHz — raising.")
                config.set_voltage = config.level_voltage
        
        # Step 5: Calculate req_volt_power and set_volt_power
        for ip_name, config in resolved.items():
            config.req_volt_power = config._calc_dynamic_power(config.required_voltage)
            config.set_volt_power = config._calc_dynamic_power(config.set_voltage)
        
        return resolved
    
    def apply_to_hw(
        self,
        hw_registry: Dict[str, Any],
        resolved_configs: Dict[str, ResolvedIPConfig]
    ) -> None:
        """Apply resolved configuration to HW nodes.
        
        Updates IPNode fields:
            - clock_freq → set_clock (in Hz)
            - set_clock, set_voltage, required_clock, required_voltage
            - unit_power, idc, vdd, dvfs_group, active_mode, ppc, dvfs_level
        
        Args:
            hw_registry: Dict of hw_name → HWNode
            resolved_configs: Output from resolve_scenario()
        """
        from .hw_nodes import IPNode
        
        for ip_name, config in resolved_configs.items():
            hw = hw_registry.get(ip_name)
            if hw is None or not isinstance(hw, IPNode):
                continue
            
            # Apply resolved values
            if config.set_clock > 0:
                hw.clock_freq = config.set_clock * 1e6  # MHz → Hz
            hw.ppc = config.ppc
            hw.unit_power = config.unit_power
            hw.idc = config.idc
            hw.vdd = config.vdd
            hw.dvfs_group = config.dvfs_group
            hw.active_mode = config.mode
            hw.set_clock = config.set_clock
            hw.set_voltage = config.set_voltage
            hw.required_clock = config.required_clock  # Overrides existing required_freq
            hw.required_voltage = config.required_voltage
            hw.dvfs_level = config.dvfs_level
            
            # Update supported_modes from CSV (replaces hw.yaml's supported_modes)
            csv_modes = self.db.get_ip_modes(ip_name)
            if csv_modes:
                hw.supported_modes = csv_modes

            # Unify the power model: make the simulator's per-task energy use
            # the same formula as the report/exploration path
            # (unit_power × MP × (V/0.71)² × fps/30), instead of IPNode's
            # dimensionally-inconsistent unit_power × set_clock approximation.
            hw._power_calculator = self._make_power_calculator(config)

    @staticmethod
    def _make_power_calculator(config: ResolvedIPConfig):
        """Build a per-IP power callback bound to its resolved config.

        Energy [mJ] = (active + idle power [mW]) × duration [s], with the
        active power formula shared with ResolvedIPConfig (report path).
        """
        def _calc(ip: Any, duration: float) -> float:
            return config.get_total_power() * duration
        return _calc
    
    def get_exploration_report(
        self,
        resolved_configs: Dict[str, ResolvedIPConfig]
    ) -> str:
        """Generate exploration report comparing required vs set values.
        
        Args:
            resolved_configs: Output from resolve_scenario()
            
        Returns:
            Formatted report string
        """
        lines = []
        lines.append("=" * 120)
        lines.append("HW Configuration Report (CSV-based DVFS Resolution)")
        lines.append("=" * 120)
        lines.append("")
        
        # Group by DVFS group
        dvfs_groups: Dict[str, List[ResolvedIPConfig]] = defaultdict(list)
        for config in resolved_configs.values():
            dvfs_groups[config.dvfs_group or "(none)"].append(config)
        
        for group_name, configs in sorted(dvfs_groups.items()):
            lines.append(f"  DVFS Group: {group_name}")
            lines.append(f"  {'IP Name':<15} {'Mode':<10} {'Req.Clk':>10} {'Set.Clk':>10} "
                         f"{'DVFS Lv':>8} {'Req.Volt':>10} {'Set.Volt':>10} {'VDD':<12} "
                         f"{'ReqV.Pwr':>10} {'SetV.Pwr':>10} {'Leader':<12}")
            lines.append(f"  {'-'*15} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*10} {'-'*10} {'-'*12} "
                         f"{'-'*10} {'-'*10} {'-'*12}")
            
            for c in sorted(configs, key=lambda x: x.ip_name):
                # Mark VDD leader with '*'
                leader_mark = "*" if c.ip_name in c.vdd_leader.split(',') else ""
                volt_delta = c.set_voltage - c.required_voltage
                volt_info = f"{c.set_voltage:>7.1f}mV" if volt_delta == 0 else f"{c.set_voltage:>7.1f}mV(+{volt_delta:.1f})"
                lines.append(
                    f"  {c.ip_name:<15} {c.mode:<10} "
                    f"{c.required_clock:>8.1f}M {c.set_clock:>8.1f}M "
                    f"{c.dvfs_level:>8d} "
                    f"{c.required_voltage:>8.1f}mV {volt_info:<14} "
                    f"{c.vdd:<12} "
                    f"{c.req_volt_power:>8.2f}mW {c.set_volt_power:>8.2f}mW "
                    f"{c.vdd_leader}{leader_mark}"
                )
            lines.append("")
        
        # VDD Domain Summary
        lines.append("-" * 120)
        lines.append("  VDD Domain Summary:")
        vdd_groups: Dict[str, List[ResolvedIPConfig]] = defaultdict(list)
        for c in resolved_configs.values():
            if c.vdd:
                vdd_groups[c.vdd].append(c)
        for vdd_name, configs in sorted(vdd_groups.items()):
            leader = configs[0].vdd_leader
            voltage = configs[0].set_voltage
            lines.append(f"    {vdd_name:<12}: {voltage:>7.1f}mV (determined by {leader})")
        lines.append("")
        
        # Power summary
        lines.append("-" * 120)
        lines.append("  Power Summary:")
        total_req = sum(c.req_volt_power for c in resolved_configs.values())
        total_set = sum(c.set_volt_power for c in resolved_configs.values())
        lines.append(f"    Req.Volt Power (pre-VDD align):  {total_req:>10.2f} mW")
        lines.append(f"    Set.Volt Power (post-VDD align): {total_set:>10.2f} mW")
        lines.append(f"    VDD Alignment Overhead:          {total_set - total_req:>10.2f} mW (+{((total_set/total_req - 1)*100) if total_req > 0 else 0:.1f}%)")
        lines.append("=" * 120)
        
        return "\n".join(lines)
    
    def _get_fps(self, hw_registry: Dict[str, Any], scenario_data: dict) -> float:
        """Get FPS from sensor node or scenario config."""
        from .hw_nodes import SensorNode
        
        # Try sensor node first
        for hw in hw_registry.values():
            if isinstance(hw, SensorNode):
                return hw.fps
        
        # Try scenario config
        fps = scenario_data.get('fps', 30.0)
        return float(fps)
    
    def _get_ip_info_for_node(
        self, 
        hw_name: str, 
        hw_node: Any,
        scenario: Any
    ) -> Optional[IPInfo]:
        """Get IPInfo for a HW node, considering mode from scenario tasks.
        
        Args:
            hw_name: HW name
            hw_node: HWNode instance
            scenario: ScenarioGraph (to find task mode)
        """
        # Find mode from scenario tasks mapped to this HW
        # (first task in scenario order wins; warn if tasks disagree)
        mode = "Normal"
        if scenario:
            task_modes = [task.ip_mode for task in scenario.get_tasks()
                          if task.mapped_hw == hw_name and task.ip_mode]
            if task_modes:
                mode = task_modes[0]
                if len(set(task_modes)) > 1:
                    print(f"[Warning] HW '{hw_name}' is mapped by tasks with "
                          f"different modes {sorted(set(task_modes))}; using "
                          f"'{mode}' for DVFS/power resolution.")

        return self.db.get_ip_info(hw_name, mode)
    
    def _get_input_pixels(self, hw_name: str, scenario: Any) -> int:
        """Get input pixel count for an IP from scenario tasks.
        
        Args:
            hw_name: HW name
            scenario: ScenarioGraph
            
        Returns:
            Total input pixels (width × height)
        """
        if scenario is None:
            return 0
        
        max_pixels = 0
        for task in scenario.get_tasks():
            if task.mapped_hw == hw_name:
                w = task.workload.get('width', 0)
                h = task.workload.get('height', 0)
                pixels = w * h
                if pixels <= 0:
                    pixels = task.workload.get('pixels', 0)
                max_pixels = max(max_pixels, pixels)
        
        return max_pixels
    
    def _get_sensor_otf_connected_hws(self, scenario: Any) -> set:
        """Find all HW names connected to sensor via OTF chain.
        
        Traverses from sensor tasks following OTF edges to collect
        all downstream IP names that need CSIS clock correction.
        
        Returns:
            Set of HW names connected to sensor via OTF
        """
        from .hw_nodes import SensorNode
        if scenario is None:
            return set()
        
        # Find sensor task IDs
        sensor_tasks = set()
        for task in scenario.get_tasks():
            if hasattr(scenario, '_resolved_sensor'):
                sensor_hw = getattr(scenario, '_resolved_sensor', {}).get('hw', '')
                if task.mapped_hw == sensor_hw:
                    sensor_tasks.add(task.task_id)
        
        if not sensor_tasks:
            return set()
        
        # BFS: follow OTF edges from sensor tasks
        from collections import deque
        from .scenario import ConnectionType
        visited = set()
        queue = deque(sensor_tasks)
        otf_hws = set()

        while queue:
            tid = queue.popleft()
            if tid in visited:
                continue
            visited.add(tid)
            
            task = scenario.get_task(tid)
            if task and task.task_id not in sensor_tasks:
                otf_hws.add(task.mapped_hw)
            
            # Follow OTF successors
            for succ_id in scenario.get_successors(tid):
                edge_data = scenario.graph[tid][succ_id]
                if edge_data.get('conn_type') == ConnectionType.OTF:
                    queue.append(succ_id)
        
        return otf_hws

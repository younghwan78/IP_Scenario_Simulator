"""
HW Resolver - DVFS & Voltage Domain resolution.

Determines clock frequency and voltage for each IP based on:
1. Scenario workload (pixels, fps, ppc) → required_clock
2. sw_margin → required_clock * (1 + sw_margin)
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
        required_voltage: Voltage for set_clock level [mV]
        set_voltage: Final voltage after VDD domain alignment [mV]
        vdd: Voltage domain name
        unit_power: Power coefficient [mW/MP@30fps]
        ppc: Pixels Per Clock
        idc: Idle power coefficient
        input_resolution_mp: Input resolution in megapixels
        fps: Scenario FPS
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
    
    def get_active_power(self) -> float:
        """Calculate active power [mW].
        
        Formula: unit_power × resolution_MP × (V/0.71)² × (FPS/30)
        """
        if self.unit_power <= 0 or self.input_resolution_mp <= 0:
            return 0.0
        v_scale = (self.set_voltage / REFERENCE_VOLTAGE_MV) ** 2
        fps_scale = self.fps / REFERENCE_FPS
        return self.unit_power * self.input_resolution_mp * v_scale * fps_scale
    
    def get_idle_power(self) -> float:
        """Calculate idle power [mW].
        
        Formula: IDC × (V/0.71)²
        """
        if self.idc <= 0:
            return 0.0
        v_scale = (self.set_voltage / REFERENCE_VOLTAGE_MV) ** 2
        return self.idc * v_scale
    
    def get_total_power(self) -> float:
        """Calculate total power [mW]."""
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
            2. Calculate required_clock = pixels×fps/ppc × (1+sw_margin)
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
                base_clock = (input_pixels * fps) / (ip_info.ppc * 1e6)  # MHz
                required_clock = base_clock * (1.0 + sw_margin)
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
        
        # Step 2: Align same DVFS group to highest required_clock
        dvfs_groups: Dict[str, List[str]] = defaultdict(list)
        for ip_name, config in resolved.items():
            if config.dvfs_group:
                dvfs_groups[config.dvfs_group].append(ip_name)
        
        for group_name, ip_names in dvfs_groups.items():
            max_required = max(resolved[n].required_clock for n in ip_names)
            for ip_name in ip_names:
                resolved[ip_name].required_clock = max_required
        
        # Step 3: Resolve set_clock from DVFS tables
        for ip_name, config in resolved.items():
            if not config.dvfs_group:
                continue
            
            dvfs_table = self.db.get_dvfs_table(config.dvfs_group)
            if dvfs_table is None:
                print(f"[Warning] DVFS table '{config.dvfs_group}' not found for '{ip_name}'")
                continue
            
            level = dvfs_table.find_min_level_for_speed(config.required_clock)
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
        
        # Step 4: Align same VDD domain to highest voltage
        vdd_groups: Dict[str, List[str]] = defaultdict(list)
        for ip_name, config in resolved.items():
            if config.vdd:
                vdd_groups[config.vdd].append(ip_name)
        
        for vdd_name, ip_names in vdd_groups.items():
            max_voltage = max(resolved[n].required_voltage for n in ip_names)
            for ip_name in ip_names:
                resolved[ip_name].set_voltage = max_voltage
        
        # IPs with no VDD group: use their own required_voltage
        for ip_name, config in resolved.items():
            if not config.vdd:
                config.set_voltage = config.required_voltage
        
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
        lines.append("=" * 90)
        lines.append("HW Configuration Report (CSV-based DVFS Resolution)")
        lines.append("=" * 90)
        lines.append("")
        
        # Group by DVFS group
        dvfs_groups: Dict[str, List[ResolvedIPConfig]] = defaultdict(list)
        for config in resolved_configs.values():
            dvfs_groups[config.dvfs_group or "(none)"].append(config)
        
        for group_name, configs in sorted(dvfs_groups.items()):
            lines.append(f"  DVFS Group: {group_name}")
            lines.append(f"  {'IP Name':<15} {'Mode':<10} {'Req.Clk':>10} {'Set.Clk':>10} "
                         f"{'DVFS Lv':>8} {'Req.Volt':>10} {'Set.Volt':>10} {'VDD':<12}")
            lines.append(f"  {'-'*15} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*10} {'-'*10} {'-'*12}")
            
            for c in sorted(configs, key=lambda x: x.ip_name):
                lines.append(
                    f"  {c.ip_name:<15} {c.mode:<10} "
                    f"{c.required_clock:>8.1f}M {c.set_clock:>8.1f}M "
                    f"{c.dvfs_level:>8d} "
                    f"{c.required_voltage:>8.1f}mV {c.set_voltage:>8.1f}mV "
                    f"{c.vdd:<12}"
                )
            lines.append("")
        
        # Power summary
        lines.append("-" * 90)
        lines.append("  Power Summary:")
        total_active = sum(c.get_active_power() for c in resolved_configs.values())
        total_idle = sum(c.get_idle_power() for c in resolved_configs.values())
        lines.append(f"    Active Power: {total_active:>10.2f} mW")
        lines.append(f"    Idle Power:   {total_idle:>10.2f} mW")
        lines.append(f"    Total Power:  {total_active + total_idle:>10.2f} mW")
        lines.append("=" * 90)
        
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
        mode = "Normal"
        if scenario:
            for task in scenario.get_tasks():
                if task.mapped_hw == hw_name and task.ip_mode:
                    mode = task.ip_mode
                    break
        
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

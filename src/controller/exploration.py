"""
Architecture Exploration Engine — Parameter Sweep.

Sweeps DVFS levels, IP modes, and IP features (size/LLC/comp)
to find power-optimal configurations under timing constraints.

Usage:
    engine = ExplorationEngine(hw_info_db, scenario, scenario_config, asv_group=4)
    engine.load_config(exploration_yaml_path)
    results = engine.run()
    # results.baseline, results.candidates (Top-K sorted by savings)
"""

from __future__ import annotations

import copy
import itertools
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..model.hw_info import HWInfoDB
from ..model.hw_resolver import HWResolver, ResolvedIPConfig


from ..model.bw import calc_port_bw, is_dma_port_name


@dataclass
class CandidateResult:
    """Result for a single parameter combination."""
    rank: int = 0
    label: str = ""

    # Sweep parameters applied
    dvfs_overrides: Dict[str, int] = field(default_factory=dict)      # domain → level
    mode_overrides: Dict[str, str] = field(default_factory=dict)      # ip → mode
    feature_overrides: Dict[str, List[dict]] = field(default_factory=dict)  # ip → [{port, param, value}]

    # Resolved configs
    resolved: Dict[str, ResolvedIPConfig] = field(default_factory=dict)

    # Power breakdown
    core_power_mw: float = 0.0
    bw_power_mw: float = 0.0
    total_power_mw: float = 0.0
    total_power_ma: float = 0.0  # total_power_mw / vBat / pmic_eff
    total_bw_mbs: float = 0.0

    # HW execution time (max across all IPs, ms)
    hw_time_ms: float = 0.0
    ip_exec_times: Dict[str, float] = field(default_factory=dict)  # ip → time_ms

    # Per-VDD breakdown
    vdd_power: Dict[str, dict] = field(default_factory=dict)

    # DMA records
    dma_records: List[dict] = field(default_factory=list)

    # Feasibility
    feasible: bool = True
    infeasible_reason: str = ""


@dataclass
class ExplorationResult:
    """Complete exploration output."""
    baseline: Optional[CandidateResult] = None
    candidates: List[CandidateResult] = field(default_factory=list)
    all_candidates: List[CandidateResult] = field(default_factory=list)
    total_combinations: int = 0
    feasible_count: int = 0
    elapsed_sec: float = 0.0
    minimize_target: str = "total_power"


def _is_dma_port(port_name: str) -> bool:
    return is_dma_port_name(port_name)


def _calc_bw_for_port(port_info: dict, fps: float,
                      bw_power_coeff: float = 80.0,
                      vBat: float = 4.0, pmic_eff: float = 0.85) -> dict:
    """Calculate BW and BW power for a single DMA port.

    Delegates to the shared formula (src/model/bw.py) and adds
    exploration-specific record fields (hw/port/direction).
    """
    rec = calc_port_bw(port_info, fps, bw_power_coeff, vBat, pmic_eff)
    rec['hw'] = port_info.get('_hw', '')
    rec['port'] = port_info.get('port', '')
    rec['direction'] = port_info.get('_direction', 'Read')
    return rec


class ExplorationEngine:
    """Architecture exploration via parameter sweep."""

    def __init__(
        self,
        hw_info_db: HWInfoDB,
        scenario: Any,
        scenario_config: dict,
        hw_registry: Dict[str, Any],
        asv_group: int = 4,
    ):
        self.db = hw_info_db
        self.scenario = scenario
        self.scenario_config = scenario_config
        self.hw_registry = hw_registry
        self.asv_group = asv_group

        sc = scenario_config.get('scenario', scenario_config)
        self.fps = float(sc.get('fps', 30.0))
        resolved_sensor = getattr(scenario, '_resolved_sensor', {})
        if self.fps <= 0 and resolved_sensor:
            self.fps = float(resolved_sensor.get('fps', 30.0))
        self.sw_margin = float(sc.get('sw_margin', 0.15))
        self.bw_power_coeff = float(sc.get('bw_power', 80.0))
        self.vBat = float(sc.get('vBat', 4.0))
        self.pmic_eff = float(sc.get('pmic_efficiency', 0.85))

        # Exploration config
        self.sweep_dvfs: Dict[str, list] = {}
        self.sweep_modes: Dict[str, list] = {}
        self.sweep_features: Dict[str, list] = {}  # ip → list of port-param combos
        self.timing_budget_ms: Optional[float] = None
        self.minimize_target: str = "total_power"
        self.top_k: int = 5

    def load_config(self, path: str) -> None:
        """Load exploration YAML config."""
        with open(path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        sweep = cfg.get('sweep', {})

        # 1. DVFS levels
        for domain, levels in sweep.get('dvfs_levels', {}).items():
            if levels == 'all':
                table = self.db.get_dvfs_table(domain)
                if table:
                    valid = [l.level for l in table.levels
                             if l.speed > 0 and l.voltages.get(self.asv_group, 0) > 0]
                    self.sweep_dvfs[domain] = sorted(valid)
            else:
                self.sweep_dvfs[domain] = list(levels)

        # 2. IP modes
        for ip_name, modes in sweep.get('ip_modes', {}).items():
            if modes == 'all':
                self.sweep_modes[ip_name] = self.db.get_ip_modes(ip_name)
            else:
                self.sweep_modes[ip_name] = list(modes)

        # 3. IP features — per port: output_size × comp × llc_enable
        ip_features_sweep = sweep.get('ip_features', {})
        if 'all' in ip_features_sweep:
            scenario_ips = set()
            for task_id, settings in getattr(self.scenario, '_ip_settings', {}).items():
                if 'hw' in settings:
                    scenario_ips.add(settings['hw'])
            port_list = ip_features_sweep.pop('all')
            for ip in scenario_ips:
                if ip not in ip_features_sweep:
                    ip_features_sweep[ip] = copy.deepcopy(port_list)

        for ip_name, port_list in ip_features_sweep.items():
            port_combos = []
            for port_cfg in port_list:
                port_name = port_cfg.get('port', '')
                # Build per-param value lists
                sizes = port_cfg.get('output_size', [None])
                if not isinstance(sizes[0], list) and sizes[0] is not None:
                    sizes = [sizes]  # Single value → wrap
                comps = port_cfg.get('comp', [None])
                if isinstance(comps, str):
                    comps = [comps]
                llcs = port_cfg.get('llc_enable', [None])
                if isinstance(llcs, str):
                    llcs = [llcs]

                for sz, comp, llc in itertools.product(sizes, comps, llcs):
                    combo = {'port': port_name}
                    if sz is not None:
                        combo['output_size'] = sz
                    if comp is not None:
                        combo['comp'] = comp
                    if llc is not None:
                        combo['llc_enable'] = llc
                    port_combos.append(combo)

            self.sweep_features[ip_name] = port_combos

        # Constraints
        constraints = cfg.get('constraints', {})
        timing = constraints.get('timing', 'auto')
        if timing == 'auto':
            frame_ms = 1000.0 / self.fps if self.fps > 0 else 33.33
            self.timing_budget_ms = frame_ms * (1.0 - self.sw_margin)
        else:
            self.timing_budget_ms = float(timing)

        # Evaluation
        evaluation = cfg.get('evaluation', {})
        self.minimize_target = evaluation.get('minimize', 'total_power')
        self.top_k = int(evaluation.get('top_k', 5))

    def _generate_combinations(self) -> List[dict]:
        """Generate all parameter combinations (Cartesian product)."""
        axes = []
        axis_names = []

        # DVFS axes
        for domain, levels in self.sweep_dvfs.items():
            axes.append(levels)
            axis_names.append(('dvfs', domain))

        # Mode axes
        for ip_name, modes in self.sweep_modes.items():
            axes.append(modes)
            axis_names.append(('mode', ip_name))

        # Feature axes (per-IP: list of port-param combos)
        for ip_name, combos in self.sweep_features.items():
            axes.append(combos)
            axis_names.append(('feature', ip_name))

        if not axes:
            return [{}]

        combinations = []
        for values in itertools.product(*axes):
            combo = {
                'dvfs': {},
                'modes': {},
                'features': {},
            }
            for (atype, aname), val in zip(axis_names, values):
                if atype == 'dvfs':
                    combo['dvfs'][aname] = val
                elif atype == 'mode':
                    combo['modes'][aname] = val
                elif atype == 'feature':
                    combo['features'][aname] = val
            combinations.append(combo)

        return combinations

    def _apply_ip_settings_overrides(self, ip_settings: dict, combo: dict) -> dict:
        """Create modified ip_settings with feature overrides applied."""
        ip_settings = copy.deepcopy(ip_settings)
        features = combo.get('features', {})
        combo['feature_applied'] = {}

        for task_id, settings in ip_settings.items():
            hw = settings.get('hw', '')
            if hw not in features:
                continue

            feat = features[hw]
            port_name = feat.get('port', '')

            if hw not in combo['feature_applied']:
                combo['feature_applied'][hw] = False

            # Apply to matching port in inputs or outputs
            for port_list_key in ('inputs', 'outputs'):
                for port_info in settings.get(port_list_key, []):
                    actual_port = port_info.get('port', '')
                    match = False
                    if port_name == '*':
                        match = True
                    elif port_name.upper() == 'ALL_DMA' and _is_dma_port(actual_port):
                        match = True
                    elif port_name.startswith('*') and port_name.endswith('*'):
                        # e.g. *DMA*
                        if port_name[1:-1].upper() in actual_port.upper():
                            match = True
                    elif actual_port == port_name:
                        match = True

                    if not match:
                        continue
                    
                    if 'output_size' in feat:
                        sz = feat['output_size']
                        port_info['size'] = [0, 0, sz[0], sz[1]]
                        combo['feature_applied'][hw] = True
                    if 'comp' in feat:
                        comp_val = feat['comp']
                        if comp_val in ('disable', 'enable'):
                            port_info['comp'] = comp_val
                            combo['feature_applied'][hw] = True
                        else:
                            # Specific compression type like SBWC, AFBC
                            hw_node = self.hw_registry.get(hw)
                            if hw_node:
                                module = hw_node.get_module(actual_port)
                                if module and hasattr(module, 'supported_compressions'):
                                    if comp_val in module.supported_compressions:
                                        port_info['comp'] = comp_val
                                        port_info['comp_ratio'] = module.compression_ratios.get(comp_val, 1.0)
                                        combo['feature_applied'][hw] = True
                    if 'llc_enable' in feat:
                        port_info['llc_enable'] = feat['llc_enable']
                        combo['feature_applied'][hw] = True

        return ip_settings

    def _collect_bw(self, ip_settings: dict) -> Tuple[List[dict], float, float]:
        """Calculate BW power from ip_settings (like report_generator)."""
        records = []
        for task_id, settings in ip_settings.items():
            hw = settings.get('hw', '')
            for port_info in settings.get('inputs', []):
                port_name = port_info.get('port', '')
                if not _is_dma_port(port_name):
                    continue
                enriched = {**port_info, '_hw': hw, '_direction': 'Read'}
                rec = _calc_bw_for_port(enriched, self.fps,
                                        self.bw_power_coeff, self.vBat, self.pmic_eff)
                records.append(rec)

            for port_info in settings.get('outputs', []):
                port_name = port_info.get('port', '')
                if not _is_dma_port(port_name):
                    continue
                enriched = {**port_info, '_hw': hw, '_direction': 'Write'}
                rec = _calc_bw_for_port(enriched, self.fps,
                                        self.bw_power_coeff, self.vBat, self.pmic_eff)
                records.append(rec)

        total_bw = sum(r['bw_mbs'] for r in records)
        total_bw_power = sum(r['bw_power_mw'] for r in records)
        return records, total_bw, total_bw_power

    def _evaluate(self, combo: dict) -> CandidateResult:
        """Evaluate a single parameter combination."""
        result = CandidateResult()
        result.dvfs_overrides = combo.get('dvfs', {})
        result.mode_overrides = combo.get('modes', {})
        if combo.get('features'):
            result.feature_overrides = {k: [v] if isinstance(v, dict) else v
                                         for k, v in combo['features'].items()}

        # --- Build modified scenario for this combo ---
        # 1. Apply mode overrides to scenario (temporary)
        orig_modes = {}
        mode_overrides = combo.get('modes', {})
        for task_id, settings in getattr(self.scenario, '_ip_settings', {}).items():
            hw = settings.get('hw', '')
            if hw in mode_overrides:
                orig_modes[task_id] = settings.get('mode')
                settings['mode'] = mode_overrides[hw]

        # Also update task ip_mode in scenario graph
        orig_task_modes = {}
        for task_id in self.scenario._tasks:
            task = self.scenario._tasks[task_id]
            hw = getattr(task, 'mapped_hw', '')
            if hw in mode_overrides:
                orig_task_modes[task_id] = getattr(task, 'ip_mode', None)
                task.ip_mode = mode_overrides[hw]

        # 2. Evaluate with modes applied; modes are ALWAYS restored
        #    (try/finally so an exception can't leak mutated scenario state)
        try:
            return self._evaluate_with_modes(combo, result)
        finally:
            self._restore_modes(orig_modes, orig_task_modes)

    def _evaluate_with_modes(self, combo: dict, result: CandidateResult) -> CandidateResult:
        """Evaluate a combination after mode overrides are applied.

        The caller (_evaluate) restores the scenario's original modes.
        """
        resolver = HWResolver(self.db, asv_group=self.asv_group)
        try:
            resolved = resolver.resolve_scenario(
                self.hw_registry, self.scenario, self.scenario_config
            )
        except Exception as e:
            result.feasible = False
            result.infeasible_reason = f"Resolver error: {e}"
            return result

        # 3. Apply DVFS level overrides (force specific levels)
        dvfs_overrides = combo.get('dvfs', {})
        for ip_name, config in resolved.items():
            if config.dvfs_group in dvfs_overrides:
                target_level_num = dvfs_overrides[config.dvfs_group]
                table = self.db.get_dvfs_table(config.dvfs_group)
                if table:
                    level = table.get_level(target_level_num)
                    if level and level.speed > 0:
                        voltage = table.get_voltage(level, self.asv_group)
                        if voltage > 0:
                            config.set_clock = level.speed
                            config.dvfs_level = level.level
                            config.required_voltage = voltage
                            config.level_voltage = voltage

        # 4. Re-align VDD domains after DVFS overrides
        vdd_groups: Dict[str, List[str]] = defaultdict(list)
        for ip_name, config in resolved.items():
            if config.vdd:
                vdd_groups[config.vdd].append(ip_name)

        def _effective_voltage(c) -> float:
            return max(c.required_voltage, c.level_voltage)

        for vdd_name, ip_names in vdd_groups.items():
            max_voltage = max(_effective_voltage(resolved[n]) for n in ip_names)
            leader_ips = sorted([n for n in ip_names
                                 if _effective_voltage(resolved[n]) == max_voltage])
            leader_str = ','.join(leader_ips)
            for ip_name in ip_names:
                resolved[ip_name].set_voltage = max_voltage
                resolved[ip_name].vdd_leader = leader_str

        # 5. Recalculate power
        for ip_name, config in resolved.items():
            config.req_volt_power = config._calc_dynamic_power(config.required_voltage)
            config.set_volt_power = config._calc_dynamic_power(config.set_voltage)

        # 6. Check timing feasibility (set_clock >= req_clock for forced DVFS)
        for ip_name, config in resolved.items():
            if config.dvfs_group in dvfs_overrides:
                if config.set_clock < config.required_clock:
                    result.feasible = False
                    result.infeasible_reason = (
                        f"{ip_name}: set_clock {config.set_clock:.1f} < "
                        f"req_clock {config.required_clock:.1f} MHz"
                    )
                    return result

        result.resolved = resolved

        # 7. BW power calculation with feature overrides
        #    (_apply_ip_settings_overrides deepcopies internally; combos
        #    without feature overrides read the shared dict directly)
        if combo.get('features'):
            ip_settings = self._apply_ip_settings_overrides(
                self.scenario._ip_settings, combo)
        else:
            ip_settings = self.scenario._ip_settings

        # Check if any feature override was unsupported and had no effect
        for hw, applied in combo.get('feature_applied', {}).items():
            if not applied:
                result.feasible = False
                result.infeasible_reason = f"Feature override for {hw} had no effect (unsupported)"
                return result
        dma_records, total_bw, total_bw_power = self._collect_bw(ip_settings)
        result.dma_records = dma_records
        result.total_bw_mbs = total_bw

        # 8. Core power
        core_power = sum(c.set_volt_power for c in resolved.values())
        result.core_power_mw = core_power
        result.bw_power_mw = total_bw_power
        result.total_power_mw = core_power + total_bw_power
        if self.vBat > 0 and self.pmic_eff > 0:
            result.total_power_ma = result.total_power_mw / self.vBat / self.pmic_eff

        # 8.5. Per-IP execution time (ms)
        for ip_name, config in resolved.items():
            if config.set_clock > 0 and config.ppc > 0 and config.input_resolution_mp > 0:
                pixels = config.input_resolution_mp * 1e6
                exec_time_s = pixels / (config.set_clock * 1e6 * config.ppc)
                result.ip_exec_times[ip_name] = exec_time_s * 1000  # ms
            else:
                result.ip_exec_times[ip_name] = 0.0
        result.hw_time_ms = max(result.ip_exec_times.values()) if result.ip_exec_times else 0.0

        # 8.6. Timing feasibility against budget (constraints.timing)
        if self.timing_budget_ms is not None and result.hw_time_ms > self.timing_budget_ms:
            result.feasible = False
            result.infeasible_reason = (
                f"hw_time {result.hw_time_ms:.2f}ms > "
                f"budget {self.timing_budget_ms:.2f}ms"
            )
            return result

        # 9. Per-VDD breakdown
        for vdd_name, ip_names in vdd_groups.items():
            vdd_core = sum(resolved[n].set_volt_power for n in ip_names)
            vdd_bw = sum(r['bw_power_mw'] for r in dma_records
                         if r.get('hw', '') in ip_names)
            result.vdd_power[vdd_name] = {
                'core_mw': vdd_core,
                'bw_mw': vdd_bw,
                'total_mw': vdd_core + vdd_bw,
                'set_volt_v': max(resolved[n].set_voltage for n in ip_names) / 1000,
            }

        return result

    def _restore_modes(self, orig_settings_modes: dict, orig_task_modes: dict):
        """Restore original mode values after evaluation."""
        for task_id, orig_mode in orig_settings_modes.items():
            ip_settings = getattr(self.scenario, '_ip_settings', {})
            if task_id in ip_settings:
                if orig_mode is not None:
                    ip_settings[task_id]['mode'] = orig_mode
                else:
                    ip_settings[task_id].pop('mode', None)
        for task_id, orig_mode in orig_task_modes.items():
            if task_id in self.scenario._tasks:
                self.scenario._tasks[task_id].ip_mode = orig_mode

    def _prune_infeasible_dvfs_levels(self, baseline: CandidateResult) -> None:
        """Drop swept DVFS levels that can never meet the required clock.

        A level whose speed is below the baseline required_clock of its DVFS
        group is guaranteed infeasible (set_clock < req_clock check), so
        evaluating it is wasted work. Domains whose IPs are also mode-swept
        are NOT pruned — a mode change alters ppc and thus required_clock.
        """
        if not baseline.resolved:
            return
        for domain, levels in list(self.sweep_dvfs.items()):
            group_ips = [c for c in baseline.resolved.values()
                         if c.dvfs_group == domain]
            if not group_ips:
                continue
            # Mode sweep on any IP of this group can change required_clock
            if any(c.ip_name in self.sweep_modes for c in group_ips):
                continue
            req = max(c.required_clock for c in group_ips)
            if req <= 0:
                continue
            table = self.db.get_dvfs_table(domain)
            if not table:
                continue
            valid = []
            for lv in levels:
                level = table.get_level(lv)
                if level and level.speed >= req:
                    valid.append(lv)
            if valid and len(valid) < len(levels):
                print(f"[Exploration] Pruned {len(levels) - len(valid)} "
                      f"infeasible DVFS level(s) from '{domain}' "
                      f"(speed < required {req:.1f} MHz)")
                self.sweep_dvfs[domain] = valid

    def run(self) -> ExplorationResult:
        """Run the full exploration sweep."""
        t0 = time.time()

        # Evaluate baseline (no overrides) first — its resolved configs
        # drive DVFS-level pruning before the sweep
        baseline = self._evaluate({})
        baseline.rank = 0
        baseline.label = "Baseline"

        self._prune_infeasible_dvfs_levels(baseline)

        combinations = self._generate_combinations()
        total = len(combinations)
        print(f"[Exploration] Total combinations: {total}")

        # Evaluate all combinations
        all_results = []
        for i, combo in enumerate(combinations):
            if (i + 1) % 500 == 0:
                print(f"  Evaluating {i + 1}/{total}...")
            r = self._evaluate(combo)
            if r.feasible:
                all_results.append(r)

        # Sort by minimize target
        def _get_key(r: CandidateResult) -> float:
            if self.minimize_target == 'core_power':
                return r.core_power_mw
            elif self.minimize_target == 'bw_power':
                return r.bw_power_mw
            return r.total_power_mw

        all_results.sort(key=_get_key)

        # Top-K (most savings first, i.e., lowest power first)
        top_k = all_results[:self.top_k]
        for i, r in enumerate(top_k):
            r.rank = i + 1
            r.label = f"Top-{i + 1}"

        elapsed = time.time() - t0

        result = ExplorationResult(
            baseline=baseline,
            candidates=top_k,
            all_candidates=all_results,
            total_combinations=total,
            feasible_count=len(all_results),
            elapsed_sec=elapsed,
            minimize_target=self.minimize_target,
        )

        print(f"[Exploration] Feasible: {len(all_results)}/{total}, "
              f"elapsed: {elapsed:.2f}s")

        return result

"""
Exploration Report Generator.

Generates HTML and Markdown reports for architecture exploration results,
showing baseline vs Top-K candidates sorted by power savings.

Features:
  - Power comparison: red=increase, blue=decrease
  - DVFS levels in separate columns per domain
  - Diff highlighting vs baseline
  - Per-IP execution time (ms) and Power (mA)
  - Bar chart (SVG) for power/BW delta in summary
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Set

from ..controller.exploration import CandidateResult, ExplorationResult


class ExplorationReportGenerator:
    """Generate exploration result reports (HTML + Markdown)."""

    def __init__(self, exploration_result: ExplorationResult,
                 scenario_name: str = "Exploration",
                 vBat: float = 4.0, pmic_eff: float = 0.85):
        self.result = exploration_result
        self.scenario_name = scenario_name
        self.vBat = vBat
        self.pmic_eff = pmic_eff
        self.generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _power_key(self, c: CandidateResult) -> float:
        if self.result.minimize_target == 'core_power':
            return c.core_power_mw
        elif self.result.minimize_target == 'bw_power':
            return c.bw_power_mw
        return c.total_power_mw

    def _get_dvfs_domains(self) -> list:
        """Get sorted list of all DVFS domains that appear in results."""
        domains: Set[str] = set()
        all_c = ([self.result.baseline] if self.result.baseline else []) + self.result.candidates
        for c in all_c:
            for cfg in c.resolved.values():
                if cfg.dvfs_group:
                    domains.add(cfg.dvfs_group)
        return sorted(domains)

    def _get_domain_level(self, candidate: CandidateResult, domain: str) -> tuple:
        """Get (level, speed) for a domain in a candidate. Returns first match."""
        for cfg in candidate.resolved.values():
            if cfg.dvfs_group == domain:
                return (cfg.dvfs_level, cfg.set_clock)
        return (-1, 0)

    def _get_ip_modes(self, candidate: CandidateResult) -> Dict[str, str]:
        """Get ip_name → mode dict."""
        return {n: c.mode for n, c in candidate.resolved.items()}

    def _mw_to_ma(self, mw: float) -> float:
        if self.vBat > 0 and self.pmic_eff > 0:
            return mw / self.vBat / self.pmic_eff
        return 0.0

    # ------------------------------------------------------------------
    # Analysis Helpers
    # ------------------------------------------------------------------
    def _get_bottom_candidates(self) -> List[CandidateResult]:
        """Get the worst up to 5 configurations that are not in the top candidates."""
        if not self.result.all_candidates:
            return []
        import copy
        bottom = []
        worst = list(reversed(self.result.all_candidates))
        cand_ids = {id(c) for c in self.result.candidates}
        for c in worst:
            if id(c) not in cand_ids:
                bottom.append(c)
            if len(bottom) >= 5:
                break
        
        copies = []
        for i, c in enumerate(bottom):
            c_copy = copy.copy(c)
            c_copy.label = f"Worst-{i+1}"
            c_copy.rank = -1
            copies.append(c_copy)
        return copies

    def _calculate_parameter_impact(self, all_c: List[CandidateResult]) -> List[dict]:
        """Calculate impact of each parameter on the target objective."""
        if not all_c: return []

        param_groups: Dict[str, Dict[str, List[float]]] = {}
        for c in all_c:
            val = self._power_key(c)
            # DVFS
            for d, lv in c.dvfs_overrides.items():
                k = f"DVFS: {d}"
                v = f"Lv{lv}"
                param_groups.setdefault(k, {}).setdefault(v, []).append(val)
            # Modes
            for ip, mode in c.mode_overrides.items():
                k = f"Mode: {ip}"
                v = mode
                param_groups.setdefault(k, {}).setdefault(v, []).append(val)
            # Features
            for ip, feats in c.feature_overrides.items():
                for feat in feats:
                    port = feat.get('port', 'unknown')
                    parts = []
                    if 'output_size' in feat: parts.append(f"sz={feat['output_size']}")
                    if 'comp' in feat: parts.append(f"comp={feat['comp']}")
                    if 'llc_enable' in feat: parts.append(f"llc={feat['llc_enable']}")
                    k = f"Feature: {ip} ({port})"
                    v = ", ".join(parts)
                    param_groups.setdefault(k, {}).setdefault(v, []).append(val)

        impacts = []
        for param, value_groups in param_groups.items():
            if len(value_groups) < 2:
                continue
            avgs = [sum(scores)/len(scores) for scores in value_groups.values() if scores]
            if avgs:
                impact = max(avgs) - min(avgs)
                impacts.append({
                    'parameter': param,
                    'impact': impact,
                    'values': {v: sum(sc)/len(sc) for v, sc in value_groups.items() if sc}
                })

        impacts.sort(key=lambda x: x['impact'], reverse=True)
        return impacts[:3]

    def _generate_variance_box_plots_svg(self, all_c: List[CandidateResult]) -> str:
        """Generate horizontal box plots for variance visualization."""
        if not all_c: return ""

        def _get_quartiles(data: List[float]) -> dict:
            if not data: return {'min': 0, 'q1': 0, 'med': 0, 'q3': 0, 'max': 0}
            s = sorted(data)
            n = len(s)
            def pct(p):
                k = (n - 1) * p
                f = int(k)
                c = min(n - 1, f + 1)
                return s[f] + (s[c] - s[f]) * (k - f)
            return {'min': s[0], 'q1': pct(0.25), 'med': pct(0.5), 'q3': pct(0.75), 'max': s[-1]}

        if self.result.minimize_target == "core_power":
            target_name = "Core Power"
        elif self.result.minimize_target == "bw_power":
            target_name = "BW Power"
        else:
            target_name = "Total Power"
        
        metrics = [
            (target_name, [self._power_key(c) for c in all_c], "mW"),
            ("Performance", [c.hw_time_ms for c in all_c], "ms"),
            ("Bandwidth", [c.total_bw_mbs for c in all_c], "MB/s")
        ]

        chart_w = 600
        row_h = 60
        chart_h = row_h * len(metrics) + 40
        margin_x = 120
        plot_w = chart_w - margin_x - 40

        svg = []
        svg.append(f"<svg width='{chart_w}' height='{chart_h}' xmlns='http://www.w3.org/2000/svg' style='background:#fafafa;border:1px solid #ddd;margin:10px 0;'>")
        
        for i, (name, data, unit) in enumerate(metrics):
            if not data: continue
            q = _get_quartiles(data)
            y_center = 40 + i * row_h
            
            svg.append(f"<text x='{margin_x - 10}' y='{y_center + 4}' text-anchor='end' font-size='12' fill='#333' font-weight='bold'>{name}</text>")
            svg.append(f"<text x='{margin_x - 10}' y='{y_center + 18}' text-anchor='end' font-size='10' fill='#777'>({unit})</text>")

            min_v, max_v = q['min'], q['max']
            val_range = max_v - min_v
            if val_range == 0: val_range = 1.0
            scale_min = max(0, min_v - val_range * 0.1)
            scale_max = max_v + val_range * 0.1
            scale_range = scale_max - scale_min

            def x_pos(val):
                return margin_x + ((val - scale_min) / scale_range) * plot_w

            x_min, x_max = x_pos(q['min']), x_pos(q['max'])
            svg.append(f"<line x1='{margin_x}' y1='{y_center}' x2='{margin_x + plot_w}' y2='{y_center}' stroke='#ccc' stroke-width='1'/>")
            svg.append(f"<line x1='{x_min}' y1='{y_center}' x2='{x_max}' y2='{y_center}' stroke='#666' stroke-width='1' stroke-dasharray='4,4'/>")
            svg.append(f"<line x1='{x_min}' y1='{y_center - 5}' x2='{x_min}' y2='{y_center + 5}' stroke='#333' stroke-width='2'/>")
            svg.append(f"<line x1='{x_max}' y1='{y_center - 5}' x2='{x_max}' y2='{y_center + 5}' stroke='#333' stroke-width='2'/>")

            x_q1, x_q3 = x_pos(q['q1']), x_pos(q['q3'])
            box_w = max(2, x_q3 - x_q1)
            svg.append(f"<rect x='{x_q1}' y='{y_center - 10}' width='{box_w}' height='{20}' fill='#90CAF9' stroke='#1565C0' stroke-width='1' rx='2'/>")

            x_med = x_pos(q['med'])
            svg.append(f"<line x1='{x_med}' y1='{y_center - 10}' x2='{x_med}' y2='{y_center + 10}' stroke='#D32F2F' stroke-width='2'/>")

            svg.append(f"<text x='{x_min}' y='{y_center - 14}' text-anchor='middle' font-size='9' fill='#555'>{q['min']:.1f}</text>")
            svg.append(f"<text x='{x_max}' y='{y_center - 14}' text-anchor='middle' font-size='9' fill='#555'>{q['max']:.1f}</text>")
            svg.append(f"<text x='{x_med}' y='{y_center + 22}' text-anchor='middle' font-size='9' fill='#D32F2F'>{q['med']:.1f}</text>")

        svg.append("</svg>")
        return "\\n".join(svg)

    # ==================================================================
    # HTML Report (primary output)
    # ==================================================================
    def generate_html(self) -> str:
        base = self.result.baseline
        candidates = self.result.candidates
        domains = self._get_dvfs_domains()
        base_modes = self._get_ip_modes(base) if base else {}

        html = []
        html.append("<!DOCTYPE html>")
        html.append("<html lang='en'>")
        html.append("<head>")
        html.append("<meta charset='UTF-8'>")
        html.append(f"<title>{self.scenario_name} — Exploration Report</title>")
        html.append("<style>")
        html.append(self._css())
        html.append("</style>")
        html.append("</head>")
        html.append("<body>")
        html.append(f"<h1>🔍 {self.scenario_name} — Exploration Report</h1>")
        html.append(f"<p class='gen-time'>Generated: {self.generated_at}</p>")

        # ── Summary ──
        html.append("<h2>1. Summary</h2>")
        html.append("<table class='summary'><tbody>")
        html.append(f"<tr><td>Total Combinations</td><td><b>{self.result.total_combinations:,}</b></td></tr>")
        html.append(f"<tr><td>Feasible</td><td>{self.result.feasible_count:,}</td></tr>")
        html.append(f"<tr><td>Minimize Target</td><td>{self.result.minimize_target}</td></tr>")
        html.append(f"<tr><td>Top-K</td><td>{len(candidates)}</td></tr>")
        html.append(f"<tr><td>Elapsed</td><td>{self.result.elapsed_sec:.2f}s</td></tr>")
        html.append("</tbody></table>")

        # Check if any candidate is better than baseline
        if base and candidates:
            base_val = self._power_key(base)
            has_better = any(self._power_key(c) < base_val for c in candidates)
            if not has_better:
                html.append("<p style='color:#D32F2F;font-weight:bold;font-size:1.05em;margin:12px 0;'>"
                            "⚠ No better configuration found — "
                            "all candidates have equal or higher power than baseline.</p>")

        # ── Bar Chart ──
        if base and candidates:
            html.append(self._generate_bar_chart_svg(base, candidates))

        # ── Parameter Impact & Variance ──
        if self.result.all_candidates:
            html.append("<h2>2. Parameter Impact & Exploration Variance</h2>")
            impacts = self._calculate_parameter_impact(self.result.all_candidates)
            if impacts:
                html.append("<h3>Top 3 Influential Parameters</h3>")
                html.append(f"<p style='font-size:0.9em;color:#555;'>* Impact is max diff in avg {self.result.minimize_target} among parameter distinct values</p>")
                html.append("<table><thead><tr><th>Parameter</th><th>Impact</th><th>Average Values</th></tr></thead><tbody>")
                for imp in impacts:
                    val_str = "<br>".join([f"<b>{k}</b>: {v:.1f}" for k, v in imp['values'].items()])
                    html.append(f"<tr><td>{imp['parameter']}</td><td><b>{imp['impact']:.2f}</b></td><td style='text-align:left'>{val_str}</td></tr>")
                html.append("</tbody></table>")
            
            html.append("<h3>Variance Distributions</h3>")
            html.append(self._generate_variance_box_plots_svg(self.result.all_candidates))

        # ── Power Comparison ──
        html.append("<h2>3. Power Comparison</h2>")
        # Build dynamic headers: Rank | DVFS domain cols | Mode cols | HW Time | Core | BW | Total | Δ | Δ%
        mode_ips = sorted(set(ip for c in candidates for ip in c.mode_overrides.keys()))
        html.append("<table class='comparison'><thead><tr>")
        html.append("<th rowspan='2'>Rank</th>")
        if domains:
            html.append(f"<th colspan='{len(domains)}'>DVFS Level (Speed MHz)</th>")
        if mode_ips:
            html.append(f"<th colspan='{len(mode_ips)}'>IP Mode</th>")
        html.append("<th rowspan='2'>HW Time<br>(ms)</th>")
        html.append("<th rowspan='2'>Core<br>(mW)</th>")
        html.append("<th rowspan='2'>BW<br>(mW)</th>")
        html.append("<th rowspan='2'>Total<br>(mW)</th>")
        html.append("<th rowspan='2'>Total<br>(mA)</th>")
        html.append("<th rowspan='2'>Δ Total<br>(mW)</th>")
        html.append("<th rowspan='2'>Δ (%)</th>")
        html.append("<th rowspan='2'>Δ HW Time<br>(ms)</th>")
        html.append("</tr><tr>")
        for d in domains:
            html.append(f"<th>{d}</th>")
        for ip in mode_ips:
            html.append(f"<th>{ip}</th>")
        html.append("</tr></thead><tbody>")

        # Baseline row
        if base:
            html.append("<tr class='baseline'>")
            html.append("<td><b>Baseline</b></td>")
            for d in domains:
                lv, spd = self._get_domain_level(base, d)
                html.append(f"<td>Lv{lv} ({spd:.0f})</td>")
            for ip in mode_ips:
                mode = base_modes.get(ip, '—')
                html.append(f"<td>{mode}</td>")
            html.append(f"<td>{base.hw_time_ms:.3f}</td>")
            html.append(f"<td>{base.core_power_mw:.2f}</td>")
            html.append(f"<td>{base.bw_power_mw:.2f}</td>")
            html.append(f"<td><b>{base.total_power_mw:.2f}</b></td>")
            html.append(f"<td>{base.total_power_ma:.2f}</td>")
            html.append("<td>—</td><td>—</td><td>—</td>")
            html.append("</tr>")

        # Top-K and Bottom-K rows
        base_domain_levels = {}
        if base:
            for d in domains:
                base_domain_levels[d] = self._get_domain_level(base, d)

        bottom_copies = self._get_bottom_candidates()
        groups = [("Top Configurations", candidates)]
        if bottom_copies:
            groups.append(("Bottom 5 Configurations", bottom_copies))

        for group_name, group_cands in groups:
            if group_name == "Bottom 5 Configurations":
                html.append(f"<tr><td colspan='100%' style='background:#ddd;font-weight:bold;'>{group_name}</td></tr>")
                
            for c in group_cands:
                html.append("<tr class='worst'>" if group_name == "Bottom 5 Configurations" else "<tr>")
                html.append(f"<td><b>{c.label}</b></td>")
                # DVFS columns with diff highlighting
                for d in domains:
                    lv, spd = self._get_domain_level(c, d)
                    base_lv, _ = base_domain_levels.get(d, (-1, 0))
                    cls = " class='diff'" if lv != base_lv else ""
                    html.append(f"<td{cls}>Lv{lv} ({spd:.0f})</td>")
                # Mode columns with diff highlighting
                cand_modes = self._get_ip_modes(c)
                for ip in mode_ips:
                    mode = cand_modes.get(ip, '—')
                    base_mode = base_modes.get(ip, '—')
                    cls = " class='diff'" if mode != base_mode else ""
                    html.append(f"<td{cls}>{mode}</td>")
                # HW time
                html.append(f"<td>{c.hw_time_ms:.3f}</td>")
                # Power
                html.append(f"<td>{c.core_power_mw:.2f}</td>")
                html.append(f"<td>{c.bw_power_mw:.2f}</td>")
                html.append(f"<td><b>{c.total_power_mw:.2f}</b></td>")
                html.append(f"<td>{c.total_power_ma:.2f}</td>")
                # Delta
                if base:
                    delta = c.total_power_mw - base.total_power_mw
                    pct = (delta / base.total_power_mw * 100) if base.total_power_mw > 0 else 0
                    delta_time = c.hw_time_ms - base.hw_time_ms
                    cls = 'inc' if delta > 0 else ('dec' if delta < 0 else '')
                    cls_t = 'inc' if delta_time > 0 else ('dec' if delta_time < 0 else '')
                    html.append(f"<td class='{cls}'>{delta:+.2f}</td>")
                    html.append(f"<td class='{cls}'>{pct:+.1f}%</td>")
                    html.append(f"<td class='{cls_t}'>{delta_time:+.3f}</td>")
                else:
                    html.append("<td>—</td><td>—</td><td>—</td>")
                html.append("</tr>")


        html.append("</tbody></table>")

        # ── Detailed per-candidate ──
        html.append("<h2>4. Detailed Results</h2>")
        all_candidates = ([base] if base else []) + candidates + bottom_copies
        for c in all_candidates:
            is_base = (c.rank == 0)
            cls = " class='baseline-section'" if is_base else ""
            html.append(f"<h3{cls}>{c.label}</h3>")

            if not is_base and base:
                delta = c.total_power_mw - base.total_power_mw
                pct = (delta / base.total_power_mw * 100) if base.total_power_mw > 0 else 0
                cls = 'inc' if delta > 0 else 'dec'
                html.append(f"<p class='delta-summary {cls}'>Δ Total Power: {delta:+.2f} mW ({pct:+.1f}%)</p>")

            # IP detail table
            if c.resolved:
                html.append("<table><thead><tr>"
                            "<th>IP</th><th>Mode</th>"
                            "<th>Req.Clk<br>(MHz)</th><th>Set.Clk<br>(MHz)</th>"
                            "<th>DVFS Lv</th>"
                            "<th>Req.Volt<br>(mV)</th><th>Set.Volt<br>(mV)</th>"
                            "<th>VDD</th>"
                            "<th>Exec Time<br>(ms)</th>"
                            "<th>Power<br>(mW)</th><th>Power<br>(mA)</th>"
                            "</tr></thead><tbody>")
                total_core_mw = 0
                total_core_ma = 0
                for ip_name in sorted(c.resolved.keys()):
                    cfg = c.resolved[ip_name]
                    exec_t = c.ip_exec_times.get(ip_name, 0.0)
                    power_ma = self._mw_to_ma(cfg.set_volt_power)
                    total_core_mw += cfg.set_volt_power
                    total_core_ma += power_ma
                    # Highlight diffs from baseline
                    mode_cls = ""
                    if not is_base and base and ip_name in base.resolved:
                        if cfg.mode != base.resolved[ip_name].mode:
                            mode_cls = " class='diff'"
                    lv_cls = ""
                    if not is_base and base and ip_name in base.resolved:
                        if cfg.dvfs_level != base.resolved[ip_name].dvfs_level:
                            lv_cls = " class='diff'"
                    html.append(
                        f"<tr><td>{ip_name}</td><td{mode_cls}>{cfg.mode}</td>"
                        f"<td>{cfg.required_clock:.1f}</td>"
                        f"<td>{cfg.set_clock:.1f}</td>"
                        f"<td{lv_cls}>{cfg.dvfs_level}</td>"
                        f"<td>{cfg.required_voltage:.1f}</td>"
                        f"<td>{cfg.set_voltage:.1f}</td>"
                        f"<td>{cfg.vdd}</td>"
                        f"<td>{exec_t:.3f}</td>"
                        f"<td>{cfg.set_volt_power:.2f}</td>"
                        f"<td>{power_ma:.2f}</td></tr>"
                    )
                html.append(
                    f"<tr class='total'><td colspan='8'><b>Core Total</b></td>"
                    f"<td>{c.hw_time_ms:.3f}</td>"
                    f"<td><b>{total_core_mw:.2f}</b></td>"
                    f"<td><b>{total_core_ma:.2f}</b></td></tr>"
                )
                html.append("</tbody></table>")

            # VDD power breakdown
            if c.vdd_power:
                html.append("<table><thead><tr>"
                            "<th>VDD</th><th>Set Volt (V)</th>"
                            "<th>Core (mW)</th><th>Core (mA)</th>"
                            "<th>BW (mW)</th><th>BW (mA)</th>"
                            "<th>Total (mW)</th><th>Total (mA)</th>"
                            "</tr></thead><tbody>")
                for vdd in sorted(c.vdd_power.keys()):
                    v = c.vdd_power[vdd]
                    core_ma = self._mw_to_ma(v['core_mw'])
                    bw_ma = self._mw_to_ma(v['bw_mw'])
                    total_ma = self._mw_to_ma(v['total_mw'])
                    html.append(
                        f"<tr><td>{vdd}</td><td>{v['set_volt_v']:.4f}</td>"
                        f"<td>{v['core_mw']:.2f}</td><td>{core_ma:.2f}</td>"
                        f"<td>{v['bw_mw']:.2f}</td><td>{bw_ma:.2f}</td>"
                        f"<td>{v['total_mw']:.2f}</td><td>{total_ma:.2f}</td></tr>"
                    )
                total_ma = self._mw_to_ma(c.total_power_mw)
                html.append(
                    f"<tr class='total'><td><b>Total</b></td><td>—</td>"
                    f"<td><b>{c.core_power_mw:.2f}</b></td><td><b>{self._mw_to_ma(c.core_power_mw):.2f}</b></td>"
                    f"<td><b>{c.bw_power_mw:.2f}</b></td><td><b>{self._mw_to_ma(c.bw_power_mw):.2f}</b></td>"
                    f"<td><b>{c.total_power_mw:.2f}</b></td><td><b>{total_ma:.2f}</b></td></tr>"
                )
                html.append("</tbody></table>")

        html.append("</body></html>")
        return "\n".join(html)

    # ------------------------------------------------------------------
    # SVG Bar Chart
    # ------------------------------------------------------------------
    def _generate_bar_chart_svg(self, base: CandidateResult,
                                 candidates: List[CandidateResult]) -> str:
        """Generate inline SVG bar chart comparing Core/BW power per candidate."""
        all_c = [base] + candidates
        labels = [c.label for c in all_c]
        n = len(all_c)

        bar_w = 50
        gap = 30
        group_w = bar_w * 2 + gap
        chart_w = group_w * n + gap * (n + 1) + 60  # extra margin for Y-axis
        chart_h = 320
        margin_top = 60
        margin_bottom = 50
        y_axis_x = 55
        plot_h = chart_h - margin_top - margin_bottom

        # Find max value for scaling
        max_val = max(c.total_power_mw for c in all_c)
        if max_val <= 0:
            max_val = 1.0

        svg = []
        svg.append("<h3>Power Comparison Chart</h3>")
        svg.append(f"<svg width='{chart_w}' height='{chart_h}' "
                   f"xmlns='http://www.w3.org/2000/svg' style='background:#fafafa;border:1px solid #ddd;margin:10px 0;'>")

        # Grid lines
        for i in range(5):
            y = margin_top + plot_h * i / 4
            val = max_val * (1 - i / 4)
            svg.append(f"<line x1='{y_axis_x}' y1='{y:.0f}' x2='{chart_w - 10}' y2='{y:.0f}' "
                       f"stroke='#e0e0e0' stroke-width='1'/>")
            svg.append(f"<text x='{y_axis_x - 5}' y='{y + 4:.0f}' text-anchor='end' "
                       f"font-size='10' fill='#666'>{val:.0f}</text>")

        # Y-axis label
        svg.append(f"<text x='12' y='{margin_top + plot_h / 2}' text-anchor='middle' "
                   f"font-size='11' fill='#666' transform='rotate(-90 12 {margin_top + plot_h / 2})'>Power (mW)</text>")

        for i, c in enumerate(all_c):
            x_start = y_axis_x + gap + i * (group_w + gap)
            core_h = (c.core_power_mw / max_val) * plot_h
            bw_h = (c.bw_power_mw / max_val) * plot_h

            # Core bar (stacked bottom)
            core_y = margin_top + plot_h - core_h
            svg.append(f"<rect x='{x_start}' y='{core_y:.1f}' width='{bar_w}' height='{core_h:.1f}' "
                       f"fill='#5C6BC0' rx='2'/>")

            # BW bar
            bw_y = margin_top + plot_h - bw_h
            svg.append(f"<rect x='{x_start + bar_w + 4}' y='{bw_y:.1f}' width='{bar_w}' height='{bw_h:.1f}' "
                       f"fill='#26A69A' rx='2'/>")

            # Values on top of bars
            svg.append(f"<text x='{x_start + bar_w / 2}' y='{core_y - 3:.0f}' "
                       f"text-anchor='middle' font-size='10' fill='#333' font-weight='bold'>"
                       f"{c.core_power_mw:.1f}</text>")
            svg.append(f"<text x='{x_start + bar_w + 4 + bar_w / 2}' y='{bw_y - 3:.0f}' "
                       f"text-anchor='middle' font-size='10' fill='#333' font-weight='bold'>"
                       f"{c.bw_power_mw:.1f}</text>")

            # Delta % annotations (skip baseline)
            if i > 0 and base:
                core_delta = c.core_power_mw - base.core_power_mw
                bw_delta = c.bw_power_mw - base.bw_power_mw
                core_pct = (core_delta / base.core_power_mw * 100) if base.core_power_mw > 0 else 0
                bw_pct = (bw_delta / base.bw_power_mw * 100) if base.bw_power_mw > 0 else 0
                core_color = '#D32F2F' if core_pct > 0 else '#1565C0'
                bw_color = '#D32F2F' if bw_pct > 0 else '#1565C0'
                svg.append(f"<text x='{x_start + bar_w / 2}' y='{core_y - 14:.0f}' "
                           f"text-anchor='middle' font-size='9' fill='{core_color}'>"
                           f"{core_pct:+.1f}%</text>")
                svg.append(f"<text x='{x_start + bar_w + 4 + bar_w / 2}' y='{bw_y - 14:.0f}' "
                           f"text-anchor='middle' font-size='9' fill='{bw_color}'>"
                           f"{bw_pct:+.1f}%</text>")

            # Label
            svg.append(f"<text x='{x_start + group_w / 2}' y='{chart_h - margin_bottom + 18}' "
                       f"text-anchor='middle' font-size='11' fill='#333' font-weight='bold'>"
                       f"{labels[i]}</text>")
            # Total value below label
            total_ma = self._mw_to_ma(c.total_power_mw)
            svg.append(f"<text x='{x_start + group_w / 2}' y='{chart_h - margin_bottom + 32}' "
                       f"text-anchor='middle' font-size='9' fill='#666'>"
                       f"{c.total_power_mw:.1f}mW / {total_ma:.1f}mA</text>")

        # Legend
        lx = y_axis_x + 10
        svg.append(f"<rect x='{lx}' y='8' width='14' height='14' fill='#5C6BC0' rx='2'/>")
        svg.append(f"<text x='{lx + 18}' y='19' font-size='11' fill='#333'>Core Power</text>")
        svg.append(f"<rect x='{lx + 100}' y='8' width='14' height='14' fill='#26A69A' rx='2'/>")
        svg.append(f"<text x='{lx + 118}' y='19' font-size='11' fill='#333'>BW Power</text>")

        svg.append("</svg>")
        return "\n".join(svg)

    # ------------------------------------------------------------------
    # CSS
    # ------------------------------------------------------------------
    def _css(self) -> str:
        return """
body { font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; background: #fafafa; color: #212121; }
h1 { color: #1a237e; border-bottom: 2px solid #1a237e; padding-bottom: 8px; }
h2 { color: #283593; margin-top: 30px; }
h3 { color: #3949ab; margin-top: 24px; border-left: 4px solid #3949ab; padding-left: 8px; }
h3.baseline-section { border-left-color: #2E7D32; color: #2E7D32; }
.gen-time { color: #888; font-size: 0.9em; }
table { border-collapse: collapse; margin: 10px 0; width: auto; }
table.summary { width: 320px; }
th, td { border: 1px solid #ccc; padding: 5px 8px; text-align: center; font-size: 0.85em; }
th { background: #e8eaf6; font-weight: bold; white-space: nowrap; }
tr:nth-child(even) { background: #f5f5f5; }
tr.total td { background: #e0e0e0; font-weight: bold; }
tr.baseline td { background: #e8f5e9; }
.inc { color: #D32F2F; font-weight: bold; }
.dec { color: #1565C0; font-weight: bold; }
.diff { background: #FFF9C4 !important; font-weight: bold; }
.delta-summary { font-size: 0.95em; margin: 4px 0; padding: 4px 8px; border-radius: 4px; display: inline-block; }
.delta-summary.inc { background: #FFEBEE; }
.delta-summary.dec { background: #E3F2FD; }
table.comparison th { position: sticky; top: 0; z-index: 1; }
"""

    # ==================================================================
    # Markdown Report
    # ==================================================================
    def generate_markdown(self) -> str:
        lines = []
        base = self.result.baseline
        candidates = self.result.candidates
        domains = self._get_dvfs_domains()

        lines.append(f"# {self.scenario_name} — Exploration Report")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append("| Item | Value |")
        lines.append("|------|-------|")
        lines.append(f"| Total Combinations | {self.result.total_combinations:,} |")
        lines.append(f"| Feasible | {self.result.feasible_count:,} |")
        lines.append(f"| Minimize Target | {self.result.minimize_target} |")
        lines.append(f"| Top-K | {len(candidates)} |")
        lines.append(f"| Elapsed | {self.result.elapsed_sec:.2f}s |")
        lines.append("")

        # Check if any candidate is better than baseline
        if base and candidates:
            base_val = self._power_key(base)
            has_better = any(self._power_key(c) < base_val for c in candidates)
            if not has_better:
                lines.append("> ⚠ **No better configuration found** — "
                             "all candidates have equal or higher power than baseline.")
                lines.append("")

        # Variance and Impact
        if self.result.all_candidates:
            impacts = self._calculate_parameter_impact(self.result.all_candidates)
            lines.append("## Parameter Impact & Exploration Variance")
            lines.append("")
            if impacts:
                lines.append("### Top 3 Influential Parameters")
                lines.append(f"*(Impact is maximum difference in average {self.result.minimize_target} among parameter distinct values)*")
                lines.append("")
                lines.append("| Parameter | Impact | Values (Avg) |")
                lines.append("|-----------|--------|--------------|")
                for imp in impacts:
                    val_str = "<br>".join([f"**{k}**: {v:.1f}" for k, v in imp['values'].items()])
                    lines.append(f"| {imp['parameter']} | **{imp['impact']:.2f}** | {val_str} |")
                lines.append("")
            
            lines.append("### Variance Distributions (Box Plot Stats)")
            lines.append("")
            def _get_quartiles(data: List[float]) -> dict:
                if not data: return {'min': 0, 'q1': 0, 'med': 0, 'q3': 0, 'max': 0}
                s = sorted(data)
                n = len(s)
                def pct(p):
                    k = (n - 1) * p
                    f = int(k)
                    c = min(n - 1, f + 1)
                    return s[f] + (s[c] - s[f]) * (k - f)
                return {'min': s[0], 'q1': pct(0.25), 'med': pct(0.5), 'q3': pct(0.75), 'max': s[-1]}
            
            if self.result.minimize_target == "core_power":
                target_name = "Core Power"
            elif self.result.minimize_target == "bw_power":
                target_name = "BW Power"
            else:
                target_name = "Total Power"
            
            metrics = [
                (target_name, [self._power_key(c) for c in self.result.all_candidates], "mW"),
                ("Performance", [c.hw_time_ms for c in self.result.all_candidates], "ms"),
                ("Bandwidth", [c.total_bw_mbs for c in self.result.all_candidates], "MB/s")
            ]
            lines.append("| Metric | Min | 25% | Median | 75% | Max |")
            lines.append("|--------|-----|-----|--------|-----|-----|")
            for name, data, unit in metrics:
                if not data: continue
                q = _get_quartiles(data)
                lines.append(f"| {name} ({unit}) | {q['min']:.1f} | {q['q1']:.1f} | {q['med']:.1f} | {q['q3']:.1f} | {q['max']:.1f} |")
            lines.append("")

        # Power comparison with DVFS split columns
        lines.append("## Power Comparison")
        lines.append("")
        hdr = "| Rank |"
        sep = "|:----:|"
        for d in domains:
            hdr += f" {d} |"
            sep += ":------:|"
        hdr += " HW Time (ms) | Core (mW) | BW (mW) | Total (mW) | Total (mA) | Δ (mW) | Δ (%) |"
        sep += ":------:|:---------:|:-------:|:----------:|:----------:|:------:|:-----:|"
        lines.append(hdr)
        lines.append(sep)

        if base:
            row = "| **Baseline** |"
            for d in domains:
                lv, spd = self._get_domain_level(base, d)
                row += f" Lv{lv}({spd:.0f}) |"
            row += (f" {base.hw_time_ms:.3f} | {base.core_power_mw:.2f} | {base.bw_power_mw:.2f} "
                    f"| {base.total_power_mw:.2f} | {base.total_power_ma:.2f} | — | — |")
            lines.append(row)

        bottom_copies = self._get_bottom_candidates()
        groups = [("Top Configurations", candidates)]
        if bottom_copies:
            groups.append(("Bottom 5 Configurations", bottom_copies))

        for group_name, group_cands in groups:
            if group_name == "Bottom 5 Configurations":
                # Add a separator row for bottom 5
                # Columns after the label: one per domain + hw_time, core,
                # bw, total, mA, delta, delta% (7 fixed columns)
                sep_row = "| **--- Bottom 5 ---** |" + " --- |" * (len(domains) + 7)
                lines.append(sep_row)
            
            for c in group_cands:
                row = f"| **{c.label}** |"
                for d in domains:
                    lv, spd = self._get_domain_level(c, d)
                    row += f" Lv{lv}({spd:.0f}) |"
                if base:
                    delta = c.total_power_mw - base.total_power_mw
                    pct = (delta / base.total_power_mw * 100) if base.total_power_mw > 0 else 0
                    delta_str = f"{delta:+.2f}"
                    pct_str = f"{pct:+.1f}%"
                else:
                    delta_str, pct_str = "—", "—"
                row += (f" {c.hw_time_ms:.3f} | {c.core_power_mw:.2f} | {c.bw_power_mw:.2f} "
                        f"| {c.total_power_mw:.2f} | {c.total_power_ma:.2f} "
                        f"| {delta_str} | {pct_str} |")
                lines.append(row)
        lines.append("")

        # Detailed per-candidate
        all_candidates = ([base] if base else []) + candidates + bottom_copies
        for c in all_candidates:
            lines.append("---")
            lines.append(f"### {c.label}")
            lines.append("")

            if c.resolved:
                lines.append("| IP | Mode | Req.Clk (MHz) | Set.Clk (MHz) | DVFS Lv | "
                             "Req.Volt (mV) | Set.Volt (mV) | VDD | Exec Time (ms) | Power (mW) | Power (mA) |")
                lines.append("|----|------|:-------------:|:-------------:|:-------:|"
                             ":-------------:|:-------------:|-----|:--------------:|:----------:|:----------:|")
                for ip_name in sorted(c.resolved.keys()):
                    cfg = c.resolved[ip_name]
                    exec_t = c.ip_exec_times.get(ip_name, 0.0)
                    power_ma = self._mw_to_ma(cfg.set_volt_power)
                    lines.append(
                        f"| {ip_name} | {cfg.mode} | {cfg.required_clock:.1f} "
                        f"| {cfg.set_clock:.1f} | {cfg.dvfs_level} "
                        f"| {cfg.required_voltage:.1f} | {cfg.set_voltage:.1f} "
                        f"| {cfg.vdd} | {exec_t:.3f} "
                        f"| {cfg.set_volt_power:.2f} | {power_ma:.2f} |"
                    )
                lines.append("")

            if c.vdd_power:
                lines.append("| VDD | Set Volt (V) | Core (mW) | Core (mA) | BW (mW) | BW (mA) | Total (mW) | Total (mA) |")
                lines.append("|-----|:------------:|:---------:|:---------:|:-------:|:-------:|:----------:|:----------:|")
                for vdd in sorted(c.vdd_power.keys()):
                    v = c.vdd_power[vdd]
                    lines.append(
                        f"| {vdd} | {v['set_volt_v']:.4f} "
                        f"| {v['core_mw']:.2f} | {self._mw_to_ma(v['core_mw']):.2f} "
                        f"| {v['bw_mw']:.2f} | {self._mw_to_ma(v['bw_mw']):.2f} "
                        f"| {v['total_mw']:.2f} | {self._mw_to_ma(v['total_mw']):.2f} |"
                    )
                total_ma = self._mw_to_ma(c.total_power_mw)
                lines.append(
                    f"| **Total** | — "
                    f"| **{c.core_power_mw:.2f}** | **{self._mw_to_ma(c.core_power_mw):.2f}** "
                    f"| **{c.bw_power_mw:.2f}** | **{self._mw_to_ma(c.bw_power_mw):.2f}** "
                    f"| **{c.total_power_mw:.2f}** | **{total_ma:.2f}** |"
                )
                lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Save helpers
    # ------------------------------------------------------------------
    def save(self, output_dir: str, base_name: str,
             formats: list = None) -> dict:
        """Save reports. Returns dict of output paths.

        Args:
            output_dir: Directory to save reports to.
            base_name: Base filename prefix.
            formats: List of formats to generate, e.g. ['html', 'md'].
                     Defaults to ['html', 'md'] if not specified.
        """
        if formats is None:
            formats = ['html', 'md']
        os.makedirs(output_dir, exist_ok=True)
        paths = {}

        if 'html' in formats:
            html_path = os.path.join(output_dir, f"{base_name}_exploration_result.html")
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(self.generate_html())
            paths['html'] = html_path

        if 'md' in formats:
            md_path = os.path.join(output_dir, f"{base_name}_exploration_result.md")
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(self.generate_markdown())
            paths['md'] = md_path

        return paths

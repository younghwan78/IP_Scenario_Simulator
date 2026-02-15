"""
Report Generator — HTML & Markdown reports for simulation results.

Generates structured reports with 7 sections:
1. Scenario Description (sensor, size, fps, scenario name)
2. Basic Conditions (project, DVFS table, temperature, SW margin, etc.)
3. DVFS Guide (per DVFS domain, set clock & level)
4. Power Results (VDD domain summary with dynamic power & BW power)
5. Clock Results (per IP: mode, req/set clock, voltage, VDD)
6. IP Details (sensor detail table + general IP detail table)
7. DMA Results (per IP: port, format, BW, BW power, etc.)
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..model.hw_resolver import ResolvedIPConfig, REFERENCE_VOLTAGE_MV


# BPP map for BW calculation (matches visualizer.py)
BPP_MAP = {
    "YUV420": 1.5, "NV12": 1.5, "NV21": 1.5,
    "YUV422": 2.0, "YUYV": 2.0, "UYVY": 2.0,
    "YUV444": 3.0, "Y8": 1.0,
    "RGB": 3.0, "RGB888": 3.0, "RGBA": 4.0, "ARGB": 4.0,
    "RAW8": 1.0, "RAW10": 1.25, "RAW12": 1.5,
    "RAW14": 1.75, "RAW16": 2.0, "P010": 2.0, "P210": 3.2,
    "BAYER_PACKED": 1.0, "BAYER_UNPACKED": 2.0,
    "STAT": 1.0, "UV8": 2.0,
}
BPP_DEFAULT = 1.0


def _is_dma_port(port_name: str) -> bool:
    """Check if a port is a DMA port (RDMA/WDMA)."""
    upper = port_name.upper()
    return 'RDMA' in upper or 'WDMA' in upper


def _calc_bw(port_info: dict, fps: float) -> dict:
    """Calculate BW and related info for a single port.

    Returns dict with bw_mbs, bw_power_mw, bw_power_ma, and port details.
    """
    sz = port_info.get('size', [])
    if len(sz) < 4 or sz[2] <= 0 or sz[3] <= 0:
        return {'bw_mbs': 0, 'bw_power_mw': 0, 'bw_power_ma': 0,
                'width': 0, 'height': 0}

    width, height = sz[2], sz[3]
    bitwidth = port_info.get('bitwidth', 8)
    fmt = port_info.get('format', '')
    bpp = BPP_MAP.get(fmt, BPP_DEFAULT)
    r_w_rate = port_info.get('r_w_rate', 1.0)

    comp = port_info.get('comp', 'disable')
    comp_ratio = port_info.get('comp_ratio', 1.0) if comp == 'enable' else 1.0

    llc_enable = port_info.get('llc_enable', 'disable')
    llc_weight = port_info.get('llc_weight', 1.0) if llc_enable == 'enable' else 1.0
    llc_hit_ratio = port_info.get('llc_hit_ratio', 0.0) if llc_enable == 'enable' else 0.0

    bw_mbs = comp_ratio * fps * width * height * (bitwidth / 8) * bpp * r_w_rate / 1e6

    bw_power_coeff = port_info.get('_bw_power_coeff', 80.0)
    vBat = port_info.get('_vBat', 4.0)
    pmic_eff = port_info.get('_pmic_eff', 0.85)

    bw_power_mw = bw_mbs * bw_power_coeff / 1000 * llc_weight
    bw_power_ma = bw_power_mw / vBat / pmic_eff if (vBat > 0 and pmic_eff > 0) else 0.0

    return {
        'bw_mbs': bw_mbs,
        'bw_power_mw': bw_power_mw,
        'bw_power_ma': bw_power_ma,
        'width': width,
        'height': height,
        'bitwidth': bitwidth,
        'format': fmt,
        'comp': comp,
        'comp_ratio': comp_ratio,
        'llc_enable': llc_enable,
        'llc_hit_ratio': llc_hit_ratio,
        'r_w_rate': r_w_rate,
    }


class ReportGenerator:
    """Generates HTML and Markdown reports for simulation results."""

    def __init__(
        self,
        scenario_config: dict,
        resolved_configs: Dict[str, ResolvedIPConfig],
        scenario: Any = None,
        hw_registry: Dict[str, Any] = None,
        resolved_sensor: dict = None,
        link_files: Dict[str, str] = None,
        hw_info_db: Any = None,
    ):
        self.config = scenario_config
        self.resolved = resolved_configs
        self.scenario = scenario
        self.hw_registry = hw_registry or {}
        self.resolved_sensor = resolved_sensor or getattr(scenario, '_resolved_sensor', {})
        self.link_files = link_files or {}
        self.hw_info_db = hw_info_db
        self.generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Extract basic params
        sc = scenario_config.get('scenario', scenario_config)
        self.scenario_name = sc.get('name', 'Unknown')
        self.fps = float(sc.get('fps', 30.0))
        if self.fps <= 0 and self.resolved_sensor:
            self.fps = float(self.resolved_sensor.get('fps', 30.0))
        self.sw_margin = float(sc.get('sw_margin', 0.15))
        self.h_blank_margin = float(sc.get('h_blank_margin', 0.05))
        self.bw_power_coeff = float(sc.get('bw_power', 80.0))
        self.vBat = float(sc.get('vBat', 4.0))
        self.pmic_eff = float(sc.get('pmic_efficiency', 0.85))
        self.asv_group = int(sc.get('asv_group', 4))
        self.temperature = sc.get('temperature', '-')

        # MIF level determination params
        self.bw_margin = float(sc.get('bw_margin', 1.25))
        self.mem_util = float(sc.get('mem_util', 0.55))
        self.mif_channel_width = int(sc.get('mif_channel_width', 16))

        # Config file paths
        self.config_paths = sc.get('config_paths', {})

    # ------------------------------------------------------------------
    # Section 1: Scenario Description
    # ------------------------------------------------------------------
    def _section_scenario(self) -> dict:
        rs = self.resolved_sensor
        output_size = rs.get('output_size', [0, 0, 0, 0])
        width = output_size[2] if len(output_size) >= 3 else 0
        height = output_size[3] if len(output_size) >= 4 else 0
        return {
            'sensor': rs.get('hw', '-'),
            'sensor_mode': rs.get('sensor_mode', rs.get('mode', '-')),
            'width': width,
            'height': height,
            'fps': self.fps,
            'scenario_name': self.scenario_name,
        }

    # ------------------------------------------------------------------
    # Section 2: Basic Conditions
    # ------------------------------------------------------------------
    def _section_conditions(self) -> dict:
        return {
            'project': self.config_paths.get('hw_info', '-'),
            'dvfs_table': self.config_paths.get('hw_dvfs', '-'),
            'info_file': self.config_paths.get('hw_info', '-'),
            'temperature': self.temperature,
            'sw_margin': self.sw_margin,
            'fps': self.fps,
            'bw_power': self.bw_power_coeff,
            'h_blank_margin': self.h_blank_margin,
            'asv_group': self.asv_group,
            'vBat': self.vBat,
            'pmic_efficiency': self.pmic_eff,
            'bw_margin': self.bw_margin,
            'mem_util': self.mem_util,
            'mif_channel_width': self.mif_channel_width,
        }

    # ------------------------------------------------------------------
    # Section 3: DVFS Guide
    # ------------------------------------------------------------------
    def _section_dvfs_guide(self) -> List[dict]:
        dvfs_groups: Dict[str, dict] = {}
        for c in self.resolved.values():
            if c.dvfs_group and c.dvfs_group not in dvfs_groups:
                dvfs_groups[c.dvfs_group] = {
                    'group': c.dvfs_group,
                    'set_clock': c.set_clock,
                    'dvfs_level': c.dvfs_level,
                    'has_manual': c.manual_clock > 0,
                }
            elif c.dvfs_group:
                existing = dvfs_groups[c.dvfs_group]
                if c.set_clock > existing['set_clock']:
                    existing['set_clock'] = c.set_clock
                    existing['dvfs_level'] = c.dvfs_level
                if c.manual_clock > 0:
                    existing['has_manual'] = True
        return list(dvfs_groups.values())

    # ------------------------------------------------------------------
    # Section 4: Power Results
    # ------------------------------------------------------------------
    def _section_power(self) -> dict:
        # Collect DMA BW records for BW power
        dma_records = self._collect_dma_records()
        total_bw_mbs = sum(r['bw_mbs'] for r in dma_records)
        total_bw_power_mw = sum(r['bw_power_mw'] for r in dma_records)
        total_bw_power_ma = sum(r['bw_power_ma'] for r in dma_records)

        # Core power per VDD domain
        vdd_groups: Dict[str, List[ResolvedIPConfig]] = defaultdict(list)
        for c in self.resolved.values():
            if c.vdd:
                vdd_groups[c.vdd].append(c)

        vdd_results = []
        total_core_power_mw = 0
        for vdd_name, configs in sorted(vdd_groups.items()):
            set_volt_v = configs[0].set_voltage / 1000.0
            domain_power_mw = sum(c.set_volt_power for c in configs)
            domain_current_ma = domain_power_mw / self.vBat / self.pmic_eff if self.vBat > 0 else 0
            total_core_power_mw += domain_power_mw

            # BW power for this VDD domain's IPs
            domain_ip_names = {c.ip_name for c in configs}
            domain_bw_mbs = sum(r['bw_mbs'] for r in dma_records if r['hw'] in domain_ip_names)
            domain_bw_power_mw = sum(r['bw_power_mw'] for r in dma_records if r['hw'] in domain_ip_names)
            domain_bw_power_ma = sum(r['bw_power_ma'] for r in dma_records if r['hw'] in domain_ip_names)

            vdd_results.append({
                'vdd': vdd_name,
                'set_volt_v': set_volt_v,
                'core_power_mw': domain_power_mw,
                'core_current_ma': domain_current_ma,
                'bw_mbs': domain_bw_mbs,
                'bw_power_mw': domain_bw_power_mw,
                'bw_current_ma': domain_bw_power_ma,
                'total_power_mw': domain_power_mw + domain_bw_power_mw,
                'total_current_ma': domain_current_ma + domain_bw_power_ma,
            })

        total_power_mw = total_core_power_mw + total_bw_power_mw
        total_current_ma = (total_core_power_mw / self.vBat / self.pmic_eff if self.vBat > 0 else 0) + total_bw_power_ma

        return {
            'total': {
                'core_power_mw': total_core_power_mw,
                'bw_mbs': total_bw_mbs,
                'bw_power_mw': total_bw_power_mw,
                'total_power_mw': total_power_mw,
                'total_current_ma': total_current_ma,
            },
            'vdd_domains': vdd_results,
        }

    # ------------------------------------------------------------------
    # Section 5: Clock Results
    # ------------------------------------------------------------------
    def _section_clock(self) -> Dict[str, List[dict]]:
        dvfs_groups: Dict[str, List[dict]] = defaultdict(list)
        for c in sorted(self.resolved.values(), key=lambda x: x.ip_name):
            dvfs_groups[c.dvfs_group or '(none)'].append({
                'ip': c.ip_name,
                'mode': c.mode,
                'req_clock': c.required_clock,
                'set_clock': c.set_clock,
                'dvfs_level': c.dvfs_level,
                'req_volt': c.required_voltage,
                'set_volt': c.set_voltage,
                'volt_delta': c.set_voltage - c.required_voltage,
                'vdd': c.vdd,
                'vdd_leader': c.vdd_leader,
                'ppc': c.ppc,
                'req_volt_power': c.req_volt_power,
                'set_volt_power': c.set_volt_power,
                'manual_clock': c.manual_clock,
            })
        return dict(sorted(dvfs_groups.items()))

    # ------------------------------------------------------------------
    # Section 6: IP Details
    # ------------------------------------------------------------------
    def _section_ip_details(self) -> dict:
        """Collect sensor detail and per-IP detail records.

        Returns dict with 'sensor_records' and 'ip_records' lists.
        """
        from src.model.hw_nodes import IPNode, SensorNode

        # ── Sensor records ──────────────────────────────────────
        sensor_records = []
        rs = self.resolved_sensor
        if rs and rs.get('hw'):
            # Get vvalid from SensorNode in hw_registry
            vvalid_ms = 0.0
            sensor_hw = self.hw_registry.get(rs.get('hw'))
            if sensor_hw and isinstance(sensor_hw, SensorNode):
                vvalid_ms = sensor_hw.effective_v_valid_time * 1000

            sensor_records.append({
                'sensor_name': rs.get('sensor_name', '-'),
                'sensor_mode': rs.get('sensor_mode', rs.get('mode', '-')),
                'format': rs.get('sensor_format', '-'),
                'bit_width': rs.get('sensor_bitwidth', 0),
                'mipi_type': rs.get('sensor_phy_type', '-'),
                'mipi_speed': rs.get('sensor_mipi_speed', 0),
                'vvalid_time_ms': vvalid_ms,
            })

        # ── General IP records ──────────────────────────────────
        ip_records = []
        dma_records = self._collect_dma_records()
        ip_settings = getattr(self.scenario, '_ip_settings', {})

        # Pre-aggregate DMA per IP
        ip_bw: dict = {}  # hw_name -> {bw, bw_power, read_bw, write_bw}
        for r in dma_records:
            hw = r['hw']
            if hw not in ip_bw:
                ip_bw[hw] = {'bw': 0.0, 'bw_power': 0.0, 'read_bw': 0.0, 'write_bw': 0.0}
            ip_bw[hw]['bw'] += r['bw_mbs']
            ip_bw[hw]['bw_power'] += r['bw_power_mw']
            if r.get('direction') == 'Read':
                ip_bw[hw]['read_bw'] += r['bw_mbs']
            else:
                ip_bw[hw]['write_bw'] += r['bw_mbs']

        for ip_name, cfg in sorted(self.resolved.items(), key=lambda x: x[0]):
            hw = self.hw_registry.get(ip_name)
            ip_group = ''
            line_buffer = 0.0
            strip_overhead = 0.0
            v_blank_ratio = 0.0
            if hw and isinstance(hw, IPNode):
                ip_group = hw.ip_group
                line_buffer = getattr(hw, 'line_buffer', 0.0)
                strip_overhead = getattr(hw, 'strip_overhead', 0.0)
                v_blank_ratio = getattr(hw, 'v_blank_ratio', 0.0)

            # Color format & input resolution from ip_settings (first input)
            color_format = '-'
            input_w, input_h = 0, 0
            num_strips = 1
            for _tid, sett in ip_settings.items():
                if sett.get('hw') == ip_name:
                    inputs = sett.get('inputs', [])
                    if inputs:
                        first_in = inputs[0]
                        color_format = first_in.get('format', '-')
                        sz = first_in.get('size', [])
                        if len(sz) >= 4:
                            input_w, input_h = sz[2], sz[3]
                    num_strips = int(sett.get('num_strips', 1))
                    break

            # HW time from processing_time
            hw_time_ms = 0.0
            if hw and isinstance(hw, IPNode):
                workload = {'width': input_w, 'height': input_h,
                            'h_blank_margin': self.h_blank_margin}
                hw_time_ms = hw.get_processing_time(workload) * 1000

            current_ma = cfg.set_volt_power / self.vBat / self.pmic_eff if self.vBat > 0 else 0.0
            bw_info = ip_bw.get(ip_name, {'bw': 0, 'bw_power': 0, 'read_bw': 0, 'write_bw': 0})

            ip_records.append({
                'ip_group': ip_group,
                'ip_name': ip_name,
                'ppc': cfg.ppc,
                'mode': cfg.mode,
                'unit_power': cfg.unit_power,
                'idle_power': cfg.idc,
                'asv_group': self.asv_group,
                'color_format': color_format,
                'input_resolution': f"{input_w}×{input_h}",
                'vdd': cfg.vdd,
                'dvfs': cfg.dvfs_group,
                'line_buffer': line_buffer,
                'strip_overhead': strip_overhead,
                'v_blank_ratio': v_blank_ratio,
                'req_freq': cfg.required_clock,
                'req_voltage': cfg.required_voltage,
                'power_req': cfg.req_volt_power,
                'set_freq': cfg.set_clock,
                'set_voltage': cfg.set_voltage,
                'power_set': cfg.set_volt_power,
                'current_ma': current_ma,
                'hw_time_ms': hw_time_ms,
                'num_strips': num_strips,
                'bw': bw_info['bw'],
                'bw_power': bw_info['bw_power'],
                'read_bw': bw_info['read_bw'],
                'write_bw': bw_info['write_bw'],
            })

        return {'sensor_records': sensor_records, 'ip_records': ip_records}

    # ------------------------------------------------------------------
    # MIF Level Determination
    # ------------------------------------------------------------------
    def _determine_mif_level(self, total_bw_mbs: float) -> dict:
        """Determine MIF DVFS level based on total BW requirement.

        mif_bw (MB/s) = freq_mhz * mif_channel_width * mem_util
        Find highest level (lowest freq) where mif_bw >= total_bw * bw_margin.

        Returns dict with level, freq, mif_bw, required_bw, voltage, or None values.
        """
        required_bw = total_bw_mbs * self.bw_margin
        result = {
            'required_bw_mbs': required_bw,
            'total_bw_mbs': total_bw_mbs,
            'bw_margin': self.bw_margin,
            'mem_util': self.mem_util,
            'mif_channel_width': self.mif_channel_width,
            'mif_level': None,
            'mif_freq': None,
            'mif_bw': None,
            'mif_voltage': None,
        }

        if not self.hw_info_db:
            return result

        mif_table = self.hw_info_db.get_dvfs_table('MIF')
        if not mif_table or not mif_table.levels:
            return result

        # Sort levels by speed descending (level 0 typically has highest speed)
        sorted_levels = sorted(mif_table.levels, key=lambda l: l.speed, reverse=True)

        # Find the highest level number (lowest freq) that still satisfies the BW
        selected = None
        for lvl in sorted_levels:
            if lvl.speed <= 0:
                continue
            mif_bw = lvl.speed * self.mif_channel_width * self.mem_util
            if mif_bw >= required_bw:
                selected = lvl
            else:
                break  # Freq too low, stop searching

        if selected is None and sorted_levels:
            # All levels too slow — use highest speed
            for lvl in sorted_levels:
                if lvl.speed > 0:
                    selected = lvl
                    break

        if selected:
            result['mif_level'] = selected.level
            result['mif_freq'] = selected.speed
            result['mif_bw'] = selected.speed * self.mif_channel_width * self.mem_util
            result['mif_voltage'] = mif_table.get_voltage(selected, self.asv_group)

        return result

    # ------------------------------------------------------------------
    # Section 6: DMA Results
    # ------------------------------------------------------------------
    def _collect_dma_records(self) -> List[dict]:
        """Collect DMA port records from ip_settings."""
        records = []
        ip_settings = getattr(self.scenario, '_ip_settings', {})
        if not ip_settings:
            return records

        for task_id, settings in ip_settings.items():
            hw = settings.get('hw', '')
            for port_info in settings.get('inputs', []):
                port_name = port_info.get('port', '')
                if not _is_dma_port(port_name):
                    continue
                enriched = {**port_info,
                            '_bw_power_coeff': self.bw_power_coeff,
                            '_vBat': self.vBat,
                            '_pmic_eff': self.pmic_eff}
                bw = _calc_bw(enriched, self.fps)
                bw['hw'] = hw
                bw['port'] = port_name
                bw['direction'] = 'Read'
                records.append(bw)

            for port_info in settings.get('outputs', []):
                port_name = port_info.get('port', '')
                if not _is_dma_port(port_name):
                    continue
                enriched = {**port_info,
                            '_bw_power_coeff': self.bw_power_coeff,
                            '_vBat': self.vBat,
                            '_pmic_eff': self.pmic_eff}
                bw = _calc_bw(enriched, self.fps)
                bw['hw'] = hw
                bw['port'] = port_name
                bw['direction'] = 'Write'
                records.append(bw)

        return records

    def _section_dma(self) -> Dict[str, List[dict]]:
        records = self._collect_dma_records()
        grouped: Dict[str, List[dict]] = defaultdict(list)
        for r in records:
            grouped[r['hw']].append(r)
        return dict(sorted(grouped.items()))

    # ==================================================================
    # Markdown Report
    # ==================================================================
    def generate_markdown(self) -> str:
        lines = []

        # Section 1
        s1 = self._section_scenario()
        lines.append(f"# {s1['scenario_name']} — Simulation Report")
        lines.append("")
        lines.append("## 1. Scenario Description")
        lines.append("")
        lines.append(f"| Item | Value |")
        lines.append(f"|------|-------|")
        lines.append(f"| Sensor | {s1['sensor']} |")
        lines.append(f"| Sensor Mode | {s1['sensor_mode']} |")
        lines.append(f"| Resolution | {s1['width']}×{s1['height']} |")
        lines.append(f"| FPS | {s1['fps']} |")
        lines.append(f"| Scenario | {s1['scenario_name']} |")
        lines.append("")

        # Section 2
        s2 = self._section_conditions()
        lines.append("## 2. Basic Conditions")
        lines.append("")
        lines.append("| Parameter | Value |")
        lines.append("|-----------|-------|")
        lines.append(f"| Project Info | {os.path.basename(str(s2['info_file']))} |")
        lines.append(f"| DVFS Table | {os.path.basename(str(s2['dvfs_table']))} |")
        lines.append(f"| ASV Group | {s2['asv_group']} |")
        lines.append(f"| Temperature | {s2['temperature']} |")
        lines.append(f"| SW Margin | {s2['sw_margin']} |")
        lines.append(f"| FPS | {s2['fps']} |")
        lines.append(f"| BW Power [mW/GB/s] | {s2['bw_power']} |")
        lines.append(f"| H_Blank Margin | {s2['h_blank_margin']} |")
        lines.append(f"| vBat [V] | {s2['vBat']} |")
        lines.append(f"| PMIC Efficiency | {s2['pmic_efficiency']} |")
        lines.append(f"| BW Margin | {s2['bw_margin']} |")
        lines.append(f"| MIF Mem Util | {s2['mem_util']} |")
        lines.append(f"| MIF Channel Width [B] | {s2['mif_channel_width']} |")
        lines.append("")

        # Section 3
        s3 = self._section_dvfs_guide()
        lines.append("## 3. DVFS Guide")
        lines.append("")
        lines.append("| DVFS Domain | Set Clock (MHz) | DVFS Level |")
        lines.append("|-------------|:---------------:|:----------:|")
        for d in sorted(s3, key=lambda x: x['group']):
            manual_tag = " 🟢" if d.get('has_manual') else ""
            lines.append(f"| {d['group']}{manual_tag} | {d['set_clock']:.1f} | {d['dvfs_level']} |")
        lines.append("")

        # Section 4
        s4 = self._section_power()
        lines.append("## 4. Power Results")
        lines.append("")
        lines.append("| VDD | Set Volt (V) | Core Power (mW) | Current (mA@Vbat) | BW (MB/s) | BW Power (mW) | BW Current (mA@Vbat) | Total Power (mW) | Total Current (mA@Vbat) |")
        lines.append("|-----|:------------:|:---------------:|:-----------------:|:---------:|:-------------:|:--------------------:|:----------------:|:-----------------------:|")
        for v in s4['vdd_domains']:
            lines.append(
                f"| {v['vdd']} | {v['set_volt_v']:.4f} | {v['core_power_mw']:.2f} | {v['core_current_ma']:.2f} "
                f"| {v['bw_mbs']:.1f} | {v['bw_power_mw']:.2f} | {v['bw_current_ma']:.2f} "
                f"| {v['total_power_mw']:.2f} | {v['total_current_ma']:.2f} |"
            )
        t = s4['total']
        total_core_ma = t['core_power_mw'] / self.vBat / self.pmic_eff if self.vBat > 0 else 0
        lines.append(
            f"| **Total** | - | **{t['core_power_mw']:.2f}** | **{total_core_ma:.2f}** "
            f"| **{t['bw_mbs']:.1f}** | **{t['bw_power_mw']:.2f}** | **{t['total_current_ma'] - total_core_ma:.2f}** "
            f"| **{t['total_power_mw']:.2f}** | **{t['total_current_ma']:.2f}** |"
        )
        lines.append("")

        # Section 5
        s5 = self._section_clock()
        lines.append("## 5. Clock Results")
        lines.append("")
        for group, ips in s5.items():
            lines.append(f"### DVFS Group: {group}")
            lines.append("")
            lines.append("| IP | Mode | Req.Clk (MHz) | Set.Clk (MHz) | DVFS Lv | Req.Volt (mV) | Set.Volt (mV) | Δ Volt | VDD | ReqV Power (mW) | SetV Power (mW) |")
            lines.append("|----|----- |:-------------:|:-------------:|:-------:|:-------------:|:-------------:|:------:|-----|:---------------:|:---------------:|")
            for ip in ips:
                delta = f"+{ip['volt_delta']:.1f}" if ip['volt_delta'] > 0 else f"{ip['volt_delta']:.1f}"
                leader = " ★" if ip['ip'] in ip['vdd_leader'].split(',') else ""
                manual_tag = " 🟢" if ip.get('manual_clock', 0) > 0 else ""
                lines.append(
                    f"| {ip['ip']}{leader}{manual_tag} | {ip['mode']} | {ip['req_clock']:.1f} | {ip['set_clock']:.1f} "
                    f"| {ip['dvfs_level']} | {ip['req_volt']:.1f} | {ip['set_volt']:.1f} "
                    f"| {delta} | {ip['vdd']} | {ip['req_volt_power']:.2f} | {ip['set_volt_power']:.2f} |"
                )
            lines.append("")

        # MIF Level determination
        dma_records = self._collect_dma_records()
        total_bw_mbs = sum(r['bw_mbs'] for r in dma_records)
        mif = self._determine_mif_level(total_bw_mbs)
        lines.append("### DVFS Group: MIF")
        lines.append("")
        lines.append("| Parameter | Value |")
        lines.append("|-----------|-------|")
        lines.append(f"| Total DMA BW | {mif['total_bw_mbs']:.1f} MB/s |")
        lines.append(f"| BW Margin | ×{mif['bw_margin']} |")
        lines.append(f"| Required BW | {mif['required_bw_mbs']:.1f} MB/s |")
        if mif['mif_level'] is not None:
            lines.append(f"| MIF Level | **{mif['mif_level']}** |")
            lines.append(f"| MIF Freq | {mif['mif_freq']:.1f} MHz |")
            lines.append(f"| MIF BW | {mif['mif_bw']:.1f} MB/s |")
            lines.append(f"| MIF Voltage (ASV{self.asv_group}) | {mif['mif_voltage']:.2f} mV |")
        else:
            lines.append("| MIF Level | N/A (no MIF DVFS table) |")
        lines.append("")

        # Section 6: IP Details
        s6_ip = self._section_ip_details()
        lines.append("## 6. IP Details")
        lines.append("")

        # 6-1. Sensor Detail
        if s6_ip['sensor_records']:
            lines.append("### 6-1. Sensor Detail")
            lines.append("")
            lines.append("| Sensor Name | Sensor Mode | Format | Bit Width | Mipi Type | Mipi Speed (Gbps) | Vvalid Time (ms) |")
            lines.append("|-------------|-------------|--------|:---------:|-----------|:-----------------:|:----------------:|")
            for sr in s6_ip['sensor_records']:
                lines.append(
                    f"| {sr['sensor_name']} | {sr['sensor_mode']} | {sr['format']} "
                    f"| {sr['bit_width']} | {sr['mipi_type']} | {sr['mipi_speed']} "
                    f"| {sr['vvalid_time_ms']:.3f} |"
                )
            lines.append("")

        # 6-2. IP Detail
        lines.append("### 6-2. IP Detail")
        lines.append("")
        lines.append(
            "| IP Group | IP Name | PPC | Mode | Unit Power | Idle Power | ASV | Color Fmt "
            "| Input Res | VDD | DVFS "
            "| Req Freq | Req Volt | Pwr@Req | Set Freq | Set Volt | Pwr@Set "
            "| mA@Vbat | HW Time(ms) | Line Buf | Strip OH | VB Ratio | Strips "
            "| BW(MB/s) | BW Pwr(mW) | RdBW | WrBW |"
        )
        lines.append(
            "|----------|---------|:---:|------|:----------:|:----------:|:---:|---------- "
            "|:---------:|-----|------"
            "|:--------:|:--------:|:-------:|:--------:|:--------:|:-------:"
            "|:-------:|:-----------:|:--------:|:--------:|:--------:|:------:"
            "|:--------:|:----------:|:----:|:----:|"
        )
        for ip in s6_ip['ip_records']:
            lines.append(
                f"| {ip['ip_group']} | {ip['ip_name']} | {ip['ppc']} | {ip['mode']} "
                f"| {ip['unit_power']:.3f} | {ip['idle_power']:.3f} | {ip['asv_group']} | {ip['color_format']} "
                f"| {ip['input_resolution']} | {ip['vdd']} | {ip['dvfs']} "
                f"| {ip['req_freq']:.1f} | {ip['req_voltage']:.1f} | {ip['power_req']:.2f} "
                f"| {ip['set_freq']:.1f} | {ip['set_voltage']:.1f} | {ip['power_set']:.2f} "
                f"| {ip['current_ma']:.2f} | {ip['hw_time_ms']:.3f} "
                f"| {ip['line_buffer']:.1f} | {ip['strip_overhead']:.1f} | {ip['v_blank_ratio']:.2f} "
                f"| {ip['num_strips']} "
                f"| {ip['bw']:.1f} | {ip['bw_power']:.2f} | {ip['read_bw']:.1f} | {ip['write_bw']:.1f} |"
            )
        lines.append("")

        # Section 7
        s7 = self._section_dma()
        lines.append("## 7. DMA Results")
        lines.append("")
        lines.append("| IP Group | Name | In/Out | Format | Bitwidth | LLC | LLC Hit | Comp | Comp Ratio | R/W Rate | W×H | BW (MB/s) | BW Power (mW) |")
        lines.append("|----------|------|:------:|--------|:--------:|:---:|:-------:|:----:|:----------:|:--------:|:---:|:---------:|:-------------:|")
        for hw, ports in s7.items():
            for p in ports:
                lines.append(
                    f"| {hw} | {p['port']} | {p['direction']} | {p.get('format', '-')} "
                    f"| {p.get('bitwidth', 0)} | {p.get('llc_enable', 'disable')} | {p.get('llc_hit_ratio', 0):.2f} "
                    f"| {p.get('comp', 'disable')} | {p.get('comp_ratio', 1.0):.2f} "
                    f"| {p.get('r_w_rate', 1.0):.1f} | {p.get('width', 0)}×{p.get('height', 0)} "
                    f"| {p['bw_mbs']:.1f} | {p['bw_power_mw']:.2f} |"
                )
        lines.append("")

        total_bw = sum(p['bw_mbs'] for ports in s7.values() for p in ports)
        total_bw_pwr = sum(p['bw_power_mw'] for ports in s7.values() for p in ports)
        lines.append(f"**Total DMA BW: {total_bw:.1f} MB/s | Total BW Power: {total_bw_pwr:.2f} mW**")
        lines.append("")

        return "\n".join(lines)

    # ==================================================================
    # HTML Report
    # ==================================================================
    def generate_html(self) -> str:
        s1 = self._section_scenario()
        s2 = self._section_conditions()
        s3 = self._section_dvfs_guide()
        s4 = self._section_power()
        s5 = self._section_clock()

        html = []
        html.append("<!DOCTYPE html>")
        html.append("<html lang='en'>")
        html.append("<head>")
        html.append("<meta charset='UTF-8'>")
        html.append(f"<title>{s1['scenario_name']} — Simulation Report</title>")
        html.append("<style>")
        html.append(self._css())
        html.append("</style>")
        html.append("</head>")
        html.append("<body>")

        # Header with timestamp
        html.append(f"<h1>{s1['scenario_name']} — Simulation Report"
                    f"<span class='timestamp'>{self.generated_at}</span></h1>")

        # Chart links
        if self.link_files:
            links = []
            for label, filepath in self.link_files.items():
                fname = os.path.basename(filepath)
                links.append(f"<a href='{fname}'>{label}</a>")
            html.append("<div class='chart-links'>📊 " + " | ".join(links) + "</div>")

        # Sections 1 & 2 side-by-side
        html.append("<div class='two-col'>")
        html.append("<div class='col'>")
        html.append("<h2>1. Scenario Description</h2>")
        html.append("<table class='info'>")
        for k, v in [('Sensor', s1['sensor']), ('Mode', s1['sensor_mode']),
                      ('Resolution', f"{s1['width']}×{s1['height']}"),
                      ('FPS', s1['fps']), ('Scenario', s1['scenario_name'])]:
            html.append(f"<tr><th>{k}</th><td>{v}</td></tr>")
        html.append("</table>")
        html.append("</div>")
        html.append("<div class='col'>")
        html.append("<h2>2. Basic Conditions</h2>")
        html.append("<table class='info'>")
        for k, v in [('Project Info', os.path.basename(str(s2['info_file']))),
                      ('DVFS Table', os.path.basename(str(s2['dvfs_table']))),
                      ('ASV Group', s2['asv_group']),
                      ('Temperature', s2['temperature']),
                      ('SW Margin', s2['sw_margin']),
                      ('FPS', s2['fps']),
                      ('BW Power [mW/GB/s]', s2['bw_power']),
                      ('H_Blank Margin', s2['h_blank_margin']),
                      ('vBat [V]', s2['vBat']),
                      ('PMIC Efficiency', s2['pmic_efficiency']),
                      ('BW Margin', s2['bw_margin']),
                      ('MIF Mem Util', s2['mem_util']),
                      ('MIF Channel Width [B]', s2['mif_channel_width'])]:
            html.append(f"<tr><th>{k}</th><td>{v}</td></tr>")
        html.append("</table>")
        html.append("</div>")
        html.append("</div>")

        # Section 3 — transposed: domains as columns
        html.append("<h2>3. DVFS Guide</h2>")
        sorted_s3 = sorted(s3, key=lambda x: x['group'])
        html.append("<table>")
        # Header row: domain names
        html.append("<thead><tr><th></th>")
        for d in sorted_s3:
            html.append(f"<th>{d['group']}</th>")
        html.append("</tr></thead><tbody>")
        # Row 1: Set Clock
        html.append("<tr><th>Set Clock (MHz)</th>")
        for d in sorted_s3:
            if d.get('has_manual'):
                html.append(f"<td style='color:#1B7A1B;font-weight:bold'>{d['set_clock']:.1f}</td>")
            else:
                html.append(f"<td>{d['set_clock']:.1f}</td>")
        html.append("</tr>")
        # Row 2: Level
        html.append("<tr><th>DVFS Level</th>")
        for d in sorted_s3:
            html.append(f"<td>{d['dvfs_level']}</td>")
        html.append("</tr>")
        html.append("</tbody></table>")

        # Section 4
        html.append("<h2>4. Power Results</h2>")
        html.append("<table><thead><tr>"
                     "<th>VDD</th><th>Set Volt (V)</th>"
                     "<th>Core Power (mW)</th><th>Current (mA@Vbat)</th>"
                     "<th>BW (MB/s)</th><th>BW Power (mW)</th><th>BW Current (mA)</th>"
                     "<th>Total Power (mW)</th><th>Total Current (mA)</th>"
                     "</tr></thead><tbody>")
        for v in s4['vdd_domains']:
            html.append(
                f"<tr><td>{v['vdd']}</td><td>{v['set_volt_v']:.4f}</td>"
                f"<td>{v['core_power_mw']:.2f}</td><td>{v['core_current_ma']:.2f}</td>"
                f"<td>{v['bw_mbs']:.1f}</td><td>{v['bw_power_mw']:.2f}</td><td>{v['bw_current_ma']:.2f}</td>"
                f"<td>{v['total_power_mw']:.2f}</td><td>{v['total_current_ma']:.2f}</td></tr>"
            )
        t = s4['total']
        total_core_ma = t['core_power_mw'] / self.vBat / self.pmic_eff if self.vBat > 0 else 0
        html.append(
            f"<tr class='total'><td><b>Total</b></td><td>-</td>"
            f"<td><b>{t['core_power_mw']:.2f}</b></td><td><b>{total_core_ma:.2f}</b></td>"
            f"<td><b>{t['bw_mbs']:.1f}</b></td><td><b>{t['bw_power_mw']:.2f}</b></td>"
            f"<td><b>{t['total_current_ma'] - total_core_ma:.2f}</b></td>"
            f"<td><b>{t['total_power_mw']:.2f}</b></td><td><b>{t['total_current_ma']:.2f}</b></td></tr>"
        )
        html.append("</tbody></table>")

        # Section 5
        html.append("<h2>5. Clock Results</h2>")
        for group, ips in s5.items():
            html.append(f"<h3>DVFS Group: {group}</h3>")
            html.append("<table><thead><tr>"
                         "<th>IP</th><th>Mode</th><th>Req.Clk</th><th>Set.Clk</th>"
                         "<th>DVFS Lv</th><th>Req.Volt</th><th>Set.Volt</th>"
                         "<th>Δ Volt</th><th>VDD</th>"
                         "<th>ReqV Pwr</th><th>SetV Pwr</th>"
                         "</tr></thead><tbody>")
            for ip in ips:
                delta = ip['volt_delta']
                delta_cls = ' class="volt-up"' if delta > 0 else ''
                delta_str = f"+{delta:.1f}" if delta > 0 else f"{delta:.1f}"
                is_leader = ip['ip'] in ip['vdd_leader'].split(',')
                leader_mark = " ★" if is_leader else ""
                ip_cls = ' class="vdd-leader"' if is_leader else ''
                # Manual clock indicators
                is_manual = ip.get('manual_clock', 0) > 0
                manual_star = ' <span style="color:#1B7A1B">★</span>' if is_manual else ''
                set_clk_td = (f"<td style='color:#1B7A1B;font-weight:bold'>{ip['set_clock']:.1f}</td>"
                              if is_manual else f"<td>{ip['set_clock']:.1f}</td>")
                html.append(
                    f"<tr><td{ip_cls}>{ip['ip']}{leader_mark}{manual_star}</td><td>{ip['mode']}</td>"
                    f"<td>{ip['req_clock']:.1f}</td>{set_clk_td}"
                    f"<td>{ip['dvfs_level']}</td>"
                    f"<td>{ip['req_volt']:.1f}</td><td>{ip['set_volt']:.1f}</td>"
                    f"<td{delta_cls}>{delta_str}</td>"
                    f"<td>{ip['vdd']}</td>"
                    f"<td>{ip['req_volt_power']:.2f}</td><td>{ip['set_volt_power']:.2f}</td></tr>"
                )
            html.append("</tbody></table>")

        # MIF Level determination
        dma_records_html = self._collect_dma_records()
        total_bw_html = sum(r['bw_mbs'] for r in dma_records_html)
        mif_html = self._determine_mif_level(total_bw_html)
        html.append("<h3>DVFS Group: MIF</h3>")
        html.append("<table class='info'>")
        html.append(f"<tr><th>Total DMA BW</th><td>{mif_html['total_bw_mbs']:.1f} MB/s</td></tr>")
        html.append(f"<tr><th>BW Margin</th><td>×{mif_html['bw_margin']}</td></tr>")
        html.append(f"<tr><th>Required BW</th><td>{mif_html['required_bw_mbs']:.1f} MB/s</td></tr>")
        if mif_html['mif_level'] is not None:
            html.append(f"<tr><th>MIF Level</th><td><b>{mif_html['mif_level']}</b></td></tr>")
            html.append(f"<tr><th>MIF Freq</th><td>{mif_html['mif_freq']:.1f} MHz</td></tr>")
            html.append(f"<tr><th>MIF BW</th><td>{mif_html['mif_bw']:.1f} MB/s</td></tr>")
            html.append(f"<tr><th>MIF Voltage (ASV{self.asv_group})</th><td>{mif_html['mif_voltage']:.2f} mV</td></tr>")
        else:
            html.append("<tr><th>MIF Level</th><td>N/A (no MIF DVFS table)</td></tr>")
        html.append("</table>")

        # Section 6: IP Details
        s6_ip_html = self._section_ip_details()
        html.append("<h2>6. IP Details</h2>")

        # 6-1. Sensor Detail
        if s6_ip_html['sensor_records']:
            html.append("<h3>6-1. Sensor Detail</h3>")
            html.append("<table><thead><tr>"
                         "<th>Sensor Name</th><th>Sensor Mode</th><th>Format</th>"
                         "<th>Bit Width</th><th>Mipi Type</th>"
                         "<th>Mipi Speed (Gbps)</th><th>Vvalid Time (ms)</th>"
                         "</tr></thead><tbody>")
            for sr in s6_ip_html['sensor_records']:
                html.append(
                    f"<tr><td>{sr['sensor_name']}</td><td>{sr['sensor_mode']}</td>"
                    f"<td>{sr['format']}</td><td>{sr['bit_width']}</td>"
                    f"<td>{sr['mipi_type']}</td><td>{sr['mipi_speed']}</td>"
                    f"<td>{sr['vvalid_time_ms']:.3f}</td></tr>"
                )
            html.append("</tbody></table>")

        # 6-2. IP Detail
        html.append("<h3>6-2. IP Detail</h3>")
        html.append(self._filter_bar_html('ip-detail'))
        html.append("<div style='overflow-x:auto'>")
        html.append("<table id='ip-detail'><thead><tr>"
                     "<th>IP Group</th><th>IP Name</th><th>PPC</th><th>Mode</th>"
                     "<th>Unit Power</th><th>Idle Power</th><th>ASV</th>"
                     "<th>Color Fmt</th><th>Input Res</th><th>VDD</th><th>DVFS</th>"
                     "<th>Req Freq</th><th>Req Volt</th><th>Pwr@Req</th>"
                     "<th>Set Freq</th><th>Set Volt</th><th>Pwr@Set</th>"
                     "<th>mA@Vbat</th><th>HW Time(ms)</th>"
                     "<th>Line Buf</th><th>Strip OH</th><th>VB Ratio</th>"
                     "<th>Strips</th>"
                     "<th>BW(MB/s)</th><th>BW Pwr(mW)</th><th>RdBW</th><th>WrBW</th>"
                     "</tr></thead><tbody>")
        for ip in s6_ip_html['ip_records']:
            html.append(
                f"<tr><td>{ip['ip_group']}</td><td>{ip['ip_name']}</td>"
                f"<td>{ip['ppc']}</td><td>{ip['mode']}</td>"
                f"<td>{ip['unit_power']:.3f}</td><td>{ip['idle_power']:.3f}</td>"
                f"<td>{ip['asv_group']}</td><td>{ip['color_format']}</td>"
                f"<td>{ip['input_resolution']}</td><td>{ip['vdd']}</td><td>{ip['dvfs']}</td>"
                f"<td>{ip['req_freq']:.1f}</td><td>{ip['req_voltage']:.1f}</td>"
                f"<td>{ip['power_req']:.2f}</td>"
                f"<td>{ip['set_freq']:.1f}</td><td>{ip['set_voltage']:.1f}</td>"
                f"<td>{ip['power_set']:.2f}</td>"
                f"<td>{ip['current_ma']:.2f}</td><td>{ip['hw_time_ms']:.3f}</td>"
                f"<td>{ip['line_buffer']:.1f}</td><td>{ip['strip_overhead']:.1f}</td>"
                f"<td>{ip['v_blank_ratio']:.2f}</td>"
                f"<td>{ip['num_strips']}</td>"
                f"<td>{ip['bw']:.1f}</td><td>{ip['bw_power']:.2f}</td>"
                f"<td>{ip['read_bw']:.1f}</td><td>{ip['write_bw']:.1f}</td></tr>"
            )
        html.append("</tbody></table>")
        html.append("</div>")

        # Section 7: DMA Results
        s7_html = self._section_dma()
        html.append("<h2>7. DMA Results</h2>")
        html.append(self._filter_bar_html('dma-detail'))
        html.append("<table id='dma-detail'><thead><tr>"
                     "<th>IP Group</th><th>Name</th><th>In/Out</th>"
                     "<th>Format</th><th>Bitwidth</th>"
                     "<th>LLC</th><th>LLC Hit</th>"
                     "<th>Comp</th><th>Comp Ratio</th>"
                     "<th>R/W Rate</th><th>W×H</th>"
                     "<th>BW (MB/s)</th><th>BW Power (mW)</th>"
                     "</tr></thead><tbody>")
        total_bw = 0
        total_bw_pwr = 0
        for hw, ports in s7_html.items():
            for p in ports:
                total_bw += p['bw_mbs']
                total_bw_pwr += p['bw_power_mw']
                # Highlight Comp/LLC enable cells
                llc_val = p.get('llc_enable', 'disable')
                comp_val = p.get('comp', 'disable')
                llc_cls = ' class="highlight-on"' if llc_val == 'enable' else ''
                comp_cls = ' class="highlight-on"' if comp_val == 'enable' else ''
                html.append(
                    f"<tr><td>{hw}</td><td>{p['port']}</td><td>{p['direction']}</td>"
                    f"<td>{p.get('format', '-')}</td><td>{p.get('bitwidth', 0)}</td>"
                    f"<td{llc_cls}>{llc_val}</td><td{llc_cls}>{p.get('llc_hit_ratio', 0):.2f}</td>"
                    f"<td{comp_cls}>{comp_val}</td><td{comp_cls}>{p.get('comp_ratio', 1.0):.2f}</td>"
                    f"<td>{p.get('r_w_rate', 1.0):.1f}</td>"
                    f"<td>{p.get('width', 0)}×{p.get('height', 0)}</td>"
                    f"<td>{p['bw_mbs']:.1f}</td><td>{p['bw_power_mw']:.2f}</td></tr>"
                )
        html.append(
            f"<tr class='total'><td colspan='11'><b>Total</b></td>"
            f"<td><b>{total_bw:.1f}</b></td><td><b>{total_bw_pwr:.2f}</b></td></tr>"
        )
        html.append("</tbody></table>")

        html.append("<script>")
        html.append(self._filter_js())
        html.append("</script>")
        html.append("</body></html>")
        return "\n".join(html)

    # ------------------------------------------------------------------
    # Filter UI helpers (HTML)
    # ------------------------------------------------------------------
    @staticmethod
    def _filter_bar_html(table_id: str) -> str:
        """Generate a filter bar (IP Group dropdown + IP Name search) for a table."""
        return (
            f"<div class='filter-bar' data-table='{table_id}'>"
            f"<label>🔍 IP Group:</label>"
            f"<select class='filter-group' onchange=\"filterTable('{table_id}')\">"
            f"<option value=''>All</option></select>"
            f"<label>IP Name:</label>"
            f"<input class='filter-name' type='text' placeholder='Search...' "
            f"oninput=\"filterTable('{table_id}')\">"
            f"<span class='filter-count'></span>"
            f"</div>"
        )

    @staticmethod
    def _filter_js() -> str:
        """JavaScript for Excel-like table filtering."""
        return """
// Auto-populate IP Group dropdown options from table data
document.querySelectorAll('.filter-bar').forEach(function(bar) {
    var tid = bar.getAttribute('data-table');
    var tbl = document.getElementById(tid);
    if (!tbl) return;
    var rows = tbl.querySelectorAll('tbody tr');
    var groups = new Set();
    rows.forEach(function(r) {
        var g = r.cells[0] ? r.cells[0].textContent.trim() : '';
        if (g) groups.add(g);
    });
    var sel = bar.querySelector('.filter-group');
    Array.from(groups).sort().forEach(function(g) {
        var opt = document.createElement('option');
        opt.value = g; opt.textContent = g;
        sel.appendChild(opt);
    });
    // Initial count
    filterTable(tid);
});

function filterTable(tableId) {
    var bar = document.querySelector(".filter-bar[data-table='" + tableId + "']");
    var tbl = document.getElementById(tableId);
    if (!bar || !tbl) return;
    var groupVal = bar.querySelector('.filter-group').value.toLowerCase();
    var nameVal = bar.querySelector('.filter-name').value.toLowerCase();
    var rows = tbl.querySelectorAll('tbody tr');
    var shown = 0;
    rows.forEach(function(row) {
        if (row.classList.contains('total')) { row.style.display = ''; return; }
        var group = row.cells[0] ? row.cells[0].textContent.trim().toLowerCase() : '';
        var name = row.cells[1] ? row.cells[1].textContent.trim().toLowerCase() : '';
        var matchGroup = !groupVal || group === groupVal;
        var matchName = !nameVal || name.indexOf(nameVal) >= 0;
        if (matchGroup && matchName) { row.style.display = ''; shown++; }
        else { row.style.display = 'none'; }
    });
    var countEl = bar.querySelector('.filter-count');
    countEl.textContent = shown + ' / ' + (rows.length) + ' rows';
}
"""

    # ------------------------------------------------------------------
    # CSS
    # ------------------------------------------------------------------
    def _css(self) -> str:
        return """
@import url('https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;600&display=swap');
body {
    font-family: 'Google Sans', 'Segoe UI', -apple-system, sans-serif;
    max-width: 1400px; margin: 32px auto; padding: 0 24px;
    background: #f8fafb; color: #202124;
}
h1 {
    color: #1a73e8; font-size: 1.6em; font-weight: 500;
    border-bottom: 2px solid #e8eaed; padding-bottom: 10px;
    margin-bottom: 20px;
}
h2 {
    color: #1967d2; margin-top: 28px; font-size: 1.15em; font-weight: 500;
    border-left: 4px solid #a8dab5; padding-left: 10px;
}
h3 {
    color: #5f6368; font-size: 0.98em; font-weight: 500;
    margin: 14px 0 6px; padding-left: 8px;
    border-left: 3px solid #d2e3fc;
}
.two-col {
    display: flex; gap: 24px; flex-wrap: wrap;
}
.two-col .col { flex: 1; min-width: 300px; }
.two-col h2 { margin-top: 8px; }
table {
    border-collapse: collapse; width: 100%; margin: 8px 0 20px;
    background: #fff; border-radius: 8px;
    box-shadow: 0 1px 3px rgba(60,64,67,0.1);
    font-size: 0.85em; overflow: hidden;
}
table.info { width: 100%; }
table.info th { text-align: left; min-width: 140px; }
th {
    background: #e8f0fe; color: #1967d2; padding: 8px 10px;
    font-weight: 500; text-align: center; white-space: nowrap;
    border-bottom: 2px solid #d2e3fc;
}
td {
    padding: 6px 10px; border-bottom: 1px solid #f1f3f4;
    text-align: center; white-space: nowrap; color: #3c4043;
}
table.info th {
    background: #e6f4ea; color: #137333; border-bottom: 1px solid #ceead6;
}
table.info td { text-align: left; }
tr:nth-child(even) { background: #fafbfc; }
tr:hover { background: #e8f0fe; }
tr.total {
    background: linear-gradient(90deg, #e6f4ea, #d2e3fc);
    font-weight: 600;
}
.volt-up { color: #d93025; font-weight: 600; }
.vdd-leader { color: #d93025; font-weight: 600; }
.highlight-on {
    background: #fef7cd !important; color: #d93025; font-weight: 600;
}
.timestamp {
    float: right; font-size: 0.55em; font-weight: 400;
    color: #5f6368; background: #e8eaed; padding: 4px 12px;
    border-radius: 12px; vertical-align: middle;
}
.chart-links {
    margin: -8px 0 16px; padding: 8px 14px;
    background: #e8f0fe; border-radius: 8px;
    font-size: 0.9em; color: #1967d2;
}
.chart-links a {
    color: #1a73e8; text-decoration: none; font-weight: 500;
    margin: 0 4px;
}
.chart-links a:hover { text-decoration: underline; }
.filter-bar {
    display: flex; gap: 12px; align-items: center;
    margin: 8px 0 4px; padding: 8px 14px;
    background: #e8f0fe; border-radius: 8px;
    font-size: 0.85em; flex-wrap: wrap;
}
.filter-bar label { color: #1967d2; font-weight: 500; white-space: nowrap; }
.filter-bar select, .filter-bar input {
    padding: 4px 8px; border: 1px solid #d2e3fc; border-radius: 4px;
    font-size: 0.95em; font-family: inherit; background: #fff;
}
.filter-bar input { min-width: 160px; }
.filter-bar .filter-count {
    margin-left: auto; color: #5f6368; font-size: 0.9em;
}
"""

    # ------------------------------------------------------------------
    # Save helpers
    # ------------------------------------------------------------------
    def save_html(self, path: str) -> None:
        """Save HTML report to file."""
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.generate_html())

    def save_markdown(self, path: str) -> None:
        """Save Markdown report to file."""
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.generate_markdown())

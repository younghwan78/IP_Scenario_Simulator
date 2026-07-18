"""
Regression tests for LLC (Last Level Cache) modeling
(internal_docs/llc_design_20260718.md).

Model:
    dram_bw  = raw_bw × (1 − hit_ratio)   ← 'bw_mbs' (DRAM-effective)
    llc_bw   = raw_bw × hit_ratio
    bw_power = dram_bw × bw_power + llc_bw × llc_power

Covers:
- calc_port_bw hit-ratio model, priority (port → default), legacy llc_weight
- 'llc' / 'llc_enable' key normalization
- apply_llc_settings: llc_paths resolution (port / from-to), errors,
  capacity warning, zero-hit warning
- MIF/total BW consumers see the DRAM-effective BW
- No-LLC scenarios are bit-identical to the pre-LLC behavior
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.bw import (
    calc_port_bw, llc_enabled, port_buffer_bytes,
)
from src.model.scenario import ScenarioGraph
from main import apply_llc_settings


FHD = {'size': [0, 0, 1920, 1080], 'format': 'NV12', 'bitwidth': 8}
FHD_RAW_BW = 30 * 1920 * 1080 * 1.5 / 1e6  # MB/s @30fps


# ============================================================
# Core calculation model
# ============================================================

class TestLlcBwModel:
    def test_hit_ratio_splits_dram_and_llc(self):
        port = {**FHD, 'llc_enable': 'enable', 'llc_hit_ratio': 0.7}
        rec = calc_port_bw(port, fps=30.0)
        assert rec['raw_bw_mbs'] == pytest.approx(FHD_RAW_BW)
        assert rec['bw_mbs'] == pytest.approx(FHD_RAW_BW * 0.3)      # DRAM
        assert rec['llc_bw_mbs'] == pytest.approx(FHD_RAW_BW * 0.7)  # LLC

    def test_power_combines_dram_and_llc_coeffs(self):
        port = {**FHD, 'llc_enable': 'enable', 'llc_hit_ratio': 0.5}
        rec = calc_port_bw(port, fps=30.0, bw_power_coeff=80.0,
                           llc_power_coeff=8.0)
        expected = (FHD_RAW_BW * 0.5 * 80.0 / 1000    # DRAM half
                    + FHD_RAW_BW * 0.5 * 8.0 / 1000)  # LLC half
        assert rec['bw_power_mw'] == pytest.approx(expected)

    def test_default_hit_ratio_fallback(self):
        # Port enables LLC without its own ratio → default applies
        port = {**FHD, 'llc_enable': 'enable'}
        rec = calc_port_bw(port, fps=30.0, llc_default_hit_ratio=0.6)
        assert rec['bw_mbs'] == pytest.approx(FHD_RAW_BW * 0.4)
        assert rec['llc_hit_ratio'] == pytest.approx(0.6)

    def test_port_ratio_overrides_default(self):
        port = {**FHD, 'llc_enable': 'enable', 'llc_hit_ratio': 0.9}
        rec = calc_port_bw(port, fps=30.0, llc_default_hit_ratio=0.6)
        assert rec['bw_mbs'] == pytest.approx(FHD_RAW_BW * 0.1)

    def test_disabled_port_unaffected(self):
        rec = calc_port_bw(dict(FHD), fps=30.0, llc_default_hit_ratio=0.7)
        assert rec['bw_mbs'] == pytest.approx(FHD_RAW_BW)
        assert rec['llc_bw_mbs'] == 0.0
        assert rec['bw_power_mw'] == pytest.approx(FHD_RAW_BW * 80.0 / 1000)

    def test_legacy_llc_weight_when_no_hit_ratio(self):
        # No hit ratio anywhere but legacy llc_weight → old power-weight
        # model (no DRAM BW reduction)
        port = {**FHD, 'llc_enable': 'enable', 'llc_weight': 0.6}
        rec = calc_port_bw(port, fps=30.0)
        assert rec['bw_mbs'] == pytest.approx(FHD_RAW_BW)  # BW unchanged
        assert rec['bw_power_mw'] == pytest.approx(
            FHD_RAW_BW * 80.0 / 1000 * 0.6)

    def test_legacy_llc_key_accepted(self):
        # Scenario may use 'llc: enable' instead of 'llc_enable: enable'
        port = {**FHD, 'llc': 'enable', 'llc_hit_ratio': 0.5}
        rec = calc_port_bw(port, fps=30.0)
        assert rec['bw_mbs'] == pytest.approx(FHD_RAW_BW * 0.5)
        assert llc_enabled(port)

    def test_comp_and_llc_combine(self):
        port = {**FHD, 'comp': 'enable', 'comp_ratio': 0.5,
                'llc_enable': 'enable', 'llc_hit_ratio': 0.5}
        rec = calc_port_bw(port, fps=30.0)
        assert rec['raw_bw_mbs'] == pytest.approx(FHD_RAW_BW * 0.5)
        assert rec['bw_mbs'] == pytest.approx(FHD_RAW_BW * 0.25)

    def test_port_buffer_bytes(self):
        assert port_buffer_bytes(dict(FHD)) == pytest.approx(
            1920 * 1080 * 1.5)
        comp = {**FHD, 'comp': 'enable', 'comp_ratio': 0.5}
        assert port_buffer_bytes(comp) == pytest.approx(1920 * 1080 * 0.75)


# ============================================================
# apply_llc_settings: llc_paths resolution
# ============================================================

def _scenario_with_ports():
    sc = ScenarioGraph("llc_test")
    sc.add_task("t_a", "IP_A", width=1920, height=1080)
    sc.add_task("t_b", "IP_B", width=1920, height=1080)
    sc._ip_settings = {
        't_a': {'hw': 'IP_A',
                'outputs': [dict(FHD, port='WDMA0')]},
        't_b': {'hw': 'IP_B',
                'inputs': [dict(FHD, port='RDMA0')]},
    }
    return sc


class TestApplyLlcSettings:
    HW_LLC = {'capacity_mb': 8, 'default_hit_ratio': 0.7, 'power_coeff': 8}

    def test_single_port_path(self):
        sc = _scenario_with_ports()
        cfg = {'llc_paths': [{'port': 'IP_A.WDMA0', 'hit_ratio': 0.65}]}
        errors, warnings = apply_llc_settings(sc, cfg, self.HW_LLC)
        assert errors == []
        out = sc._ip_settings['t_a']['outputs'][0]
        assert out['llc_enable'] == 'enable'
        assert out['llc_hit_ratio'] == pytest.approx(0.65)
        assert sc._llc_default_hit_ratio == pytest.approx(0.7)
        assert sc._llc_power_coeff == pytest.approx(8.0)
        assert len(sc._llc_paths) == 1

    def test_from_to_path_applies_both_ends(self):
        sc = _scenario_with_ports()
        cfg = {'llc_paths': [{'from': 'IP_A.WDMA0', 'to': 'IP_B.RDMA0'}]}
        errors, _ = apply_llc_settings(sc, cfg, self.HW_LLC)
        assert errors == []
        assert sc._ip_settings['t_a']['outputs'][0]['llc_enable'] == 'enable'
        assert sc._ip_settings['t_b']['inputs'][0]['llc_enable'] == 'enable'

    def test_scenario_hit_ratio_overrides_hw_default(self):
        sc = _scenario_with_ports()
        cfg = {'llc_hit_ratio': 0.5,
               'llc_paths': [{'port': 'IP_A.WDMA0'}]}
        apply_llc_settings(sc, cfg, self.HW_LLC)
        assert sc._llc_default_hit_ratio == pytest.approx(0.5)

    def test_unknown_port_is_error_with_hint(self):
        sc = _scenario_with_ports()
        cfg = {'llc_paths': [{'port': 'IP_A.NO_SUCH_PORT'}]}
        errors, _ = apply_llc_settings(sc, cfg, self.HW_LLC)
        assert len(errors) == 1
        assert 'IP_A.WDMA0' in errors[0]  # hint lists available ports

    def test_malformed_entry_is_error(self):
        sc = _scenario_with_ports()
        cfg = {'llc_paths': [{'hit_ratio': 0.5}]}
        errors, _ = apply_llc_settings(sc, cfg, self.HW_LLC)
        assert len(errors) == 1

    def test_zero_hit_ratio_warns(self):
        sc = _scenario_with_ports()
        cfg = {'llc_paths': [{'port': 'IP_A.WDMA0'}]}
        _, warnings = apply_llc_settings(sc, cfg, {})  # no hw defaults
        assert any('hit ratio is 0' in w for w in warnings)

    def test_capacity_exceeded_warns(self):
        sc = _scenario_with_ports()
        cfg = {'llc_paths': [{'port': 'IP_A.WDMA0', 'hit_ratio': 0.7}]}
        # FHD NV12 buffer ≈ 2.97 MB > 1 MB capacity
        small = {'capacity_mb': 1, 'default_hit_ratio': 0.7}
        _, warnings = apply_llc_settings(sc, cfg, small)
        assert any('exceeds' in w for w in warnings)
        assert sc._llc_config['footprint_mb'] == pytest.approx(
            1920 * 1080 * 1.5 / (1024 * 1024))

    def test_legacy_llc_key_normalized(self):
        sc = _scenario_with_ports()
        sc._ip_settings['t_a']['outputs'][0]['llc'] = 'enable'
        errors, _ = apply_llc_settings(sc, {}, self.HW_LLC)
        assert errors == []
        assert sc._ip_settings['t_a']['outputs'][0]['llc_enable'] == 'enable'

    def test_no_llc_config_is_noop(self):
        sc = _scenario_with_ports()
        errors, warnings = apply_llc_settings(sc, {}, {})
        assert errors == [] and warnings == []
        out = sc._ip_settings['t_a']['outputs'][0]
        assert 'llc_enable' not in out
        rec = calc_port_bw(out, 30.0,
                           llc_default_hit_ratio=sc._llc_default_hit_ratio)
        assert rec['bw_mbs'] == pytest.approx(FHD_RAW_BW)


# ============================================================
# Report integration: totals / MIF see DRAM-effective BW
# ============================================================

class TestLlcReportIntegration:
    def _make_report(self, with_llc: bool):
        from src.view.report_generator import ReportGenerator
        sc = _scenario_with_ports()
        cfg = {'name': 'llc_test', 'fps': 30.0}
        if with_llc:
            cfg['llc_paths'] = [
                {'from': 'IP_A.WDMA0', 'to': 'IP_B.RDMA0', 'hit_ratio': 0.7}]
        errors, _ = apply_llc_settings(
            sc, cfg, {'capacity_mb': 8, 'default_hit_ratio': 0.7})
        assert errors == []
        return ReportGenerator(scenario_config=cfg, resolved_configs={},
                               scenario=sc)

    def test_dma_totals_use_dram_effective_bw(self):
        base = self._make_report(with_llc=False)
        llc = self._make_report(with_llc=True)
        base_total = sum(r['bw_mbs'] for r in base._collect_dma_records())
        llc_total = sum(r['bw_mbs'] for r in llc._collect_dma_records())
        # WDMA0 + RDMA0, both at 0.7 hit → 30% DRAM traffic remains
        assert base_total == pytest.approx(FHD_RAW_BW * 2)
        assert llc_total == pytest.approx(FHD_RAW_BW * 2 * 0.3)

    def test_llc_summary_present_only_when_used(self):
        base = self._make_report(with_llc=False)
        llc = self._make_report(with_llc=True)
        assert base._llc_summary(base._collect_dma_records()) is None
        summary = llc._llc_summary(llc._collect_dma_records())
        assert summary is not None
        assert summary['saved_bw'] == pytest.approx(FHD_RAW_BW * 2 * 0.7)
        assert len(summary['paths']) == 1

    def test_markdown_contains_llc_section(self):
        llc = self._make_report(with_llc=True)
        md = llc.generate_markdown()
        assert 'LLC Summary' in md

    def test_markdown_no_llc_section_without_llc(self):
        base = self._make_report(with_llc=False)
        assert 'LLC Summary' not in base.generate_markdown()

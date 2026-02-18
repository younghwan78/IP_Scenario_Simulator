"""
Tests for ReportGenerator.

Verifies section data correctness, DMA record collection,
Markdown/HTML generation smoke tests, and MIF level.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode
from src.model.hw_resolver import ResolvedIPConfig
from src.model.scenario import ScenarioGraph
from src.view.report_generator import ReportGenerator, _calc_bw, _is_dma_port


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def resolved_configs():
    """Minimal ResolvedIPConfig set for report generation."""
    return {
        "ISP_FE": ResolvedIPConfig(
            ip_name="ISP_FE", mode="Normal", ppc=4,
            unit_power=0.5, idc=0.1,
            vdd="VDD_CAM", dvfs_group="CAM",
            required_clock=300.0, set_clock=400.0,
            dvfs_level=1,
            required_voltage=700.0, set_voltage=750.0,
            vdd_leader="ISP_FE",
            input_resolution_mp=8.3,
            fps=30.0,
            req_volt_power=3.27,
            set_volt_power=3.72,
        ),
        "VENC": ResolvedIPConfig(
            ip_name="VENC", mode="Normal", ppc=1,
            unit_power=1.2, idc=0.3,
            vdd="VDD_MFC", dvfs_group="MFC",
            required_clock=250.0, set_clock=300.0,
            dvfs_level=1,
            required_voltage=650.0, set_voltage=700.0,
            vdd_leader="VENC",
            input_resolution_mp=8.3,
            fps=30.0,
            req_volt_power=8.36,
            set_volt_power=9.75,
        ),
    }


@pytest.fixture
def scenario_config():
    """Minimal scenario config dict."""
    return {
        'scenario': {
            'name': 'TestScenario',
            'fps': 30,
            'sw_margin': 0.15,
            'h_blank_margin': 0.05,
            'bw_power': 80.0,
            'vBat': 4.0,
            'pmic_efficiency': 0.85,
            'bw_margin': 1.25,
            'mem_util': 0.55,
            'mif_channel_width': 16,
        }
    }


@pytest.fixture
def scenario_with_ip_settings():
    """Scenario with _ip_settings attribute for DMA record collection."""
    scenario = ScenarioGraph(name="TestScenario")
    scenario.add_task("t_isp", "ISP_FE", width=3840, height=2160)
    scenario.add_task("t_venc", "VENC", width=3840, height=2160)
    scenario.add_dependency("t_isp", "t_venc", "M2M")

    # Simulate ip_settings
    scenario._ip_settings = {
        "t_isp": {
            "hw": "ISP_FE",
            "inputs": [
                {"port": "RDMA0", "size": [0, 0, 3840, 2160],
                 "format": "NV12", "bitwidth": 8, "r_w_rate": 1.0,
                 "comp": "disable"},
            ],
            "outputs": [
                {"port": "WDMA0", "size": [0, 0, 3840, 2160],
                 "format": "NV12", "bitwidth": 8, "r_w_rate": 1.0,
                 "comp": "disable"},
            ],
        },
    }
    return scenario


@pytest.fixture
def hw_registry():
    return {
        "ISP_FE": IPNode(name="ISP_FE", clock_freq=600e6, ppc=4),
        "VENC": IPNode(name="VENC", clock_freq=400e6, ppc=1),
    }


@pytest.fixture
def report_gen(scenario_config, resolved_configs, scenario_with_ip_settings, hw_registry):
    return ReportGenerator(
        scenario_config=scenario_config,
        resolved_configs=resolved_configs,
        scenario=scenario_with_ip_settings,
        hw_registry=hw_registry,
    )


# ============================================================
# Tests: Section Data
# ============================================================

class TestSectionScenario:
    def test_scenario_name(self, report_gen):
        s1 = report_gen._section_scenario()
        assert s1['scenario_name'] == "TestScenario"
        assert s1['fps'] == 30

    def test_scenario_sensor_field(self, report_gen):
        s1 = report_gen._section_scenario()
        assert 'sensor' in s1


class TestSectionPower:
    def test_power_total_positive(self, report_gen):
        power = report_gen._section_power()
        assert power['total']['core_power_mw'] > 0

    def test_vdd_domains_present(self, report_gen):
        power = report_gen._section_power()
        vdd_names = [v['vdd'] for v in power['vdd_domains']]
        assert "VDD_CAM" in vdd_names
        assert "VDD_MFC" in vdd_names


class TestSectionClock:
    def test_clock_groups(self, report_gen):
        clocks = report_gen._section_clock()
        assert "CAM" in clocks
        assert "MFC" in clocks

    def test_clock_ip_data(self, report_gen):
        clocks = report_gen._section_clock()
        cam_ips = clocks["CAM"]
        assert len(cam_ips) == 1
        assert cam_ips[0]['ip'] == "ISP_FE"
        assert cam_ips[0]['set_clock'] == 400.0


# ============================================================
# Tests: DMA Records
# ============================================================

class TestDMARecordCollection:
    def test_dma_records_from_ip_settings(self, report_gen):
        records = report_gen._collect_dma_records()
        assert len(records) == 2  # RDMA0 + WDMA0

    def test_dma_direction(self, report_gen):
        records = report_gen._collect_dma_records()
        directions = {r['direction'] for r in records}
        assert 'Read' in directions
        assert 'Write' in directions

    def test_dma_bw_positive(self, report_gen):
        records = report_gen._collect_dma_records()
        for r in records:
            assert r['bw_mbs'] > 0


# ============================================================
# Tests: Utility Functions
# ============================================================

class TestUtilities:
    def test_is_dma_port(self):
        assert _is_dma_port("RDMA0") is True
        assert _is_dma_port("WDMA1") is True
        assert _is_dma_port("Scaler") is False

    def test_calc_bw_valid(self):
        port = {
            'size': [0, 0, 3840, 2160],
            'format': 'NV12',
            'bitwidth': 8,
            'r_w_rate': 1.0,
            'comp': 'disable',
        }
        result = _calc_bw(port, fps=30.0)
        assert result['bw_mbs'] > 0

    def test_calc_bw_empty_size(self):
        port = {'size': [], 'format': 'NV12'}
        result = _calc_bw(port, fps=30.0)
        assert result['bw_mbs'] == 0

    def test_calc_bw_zero_size(self):
        port = {'size': [0, 0, 0, 0], 'format': 'NV12'}
        result = _calc_bw(port, fps=30.0)
        assert result['bw_mbs'] == 0


# ============================================================
# Tests: Report Generation (Smoke Tests)
# ============================================================

class TestMarkdownGeneration:
    def test_markdown_contains_sections(self, report_gen):
        md = report_gen.generate_markdown()
        assert "Scenario" in md
        assert "Power" in md
        assert "Clock" in md

    def test_markdown_scenario_name(self, report_gen):
        md = report_gen.generate_markdown()
        assert "TestScenario" in md

    def test_markdown_not_empty(self, report_gen):
        md = report_gen.generate_markdown()
        assert len(md) > 500


class TestHTMLGeneration:
    def test_html_valid_structure(self, report_gen):
        html = report_gen.generate_html()
        assert "<html" in html.lower()
        assert "TestScenario" in html

    def test_html_not_empty(self, report_gen):
        html = report_gen.generate_html()
        assert len(html) > 1000


class TestSaveReport:
    def test_save_markdown(self, report_gen, tmp_path):
        md_path = tmp_path / "report.md"
        report_gen.save_markdown(str(md_path))
        assert md_path.exists()
        assert md_path.stat().st_size > 0

    def test_save_html(self, report_gen, tmp_path):
        html_path = tmp_path / "report.html"
        report_gen.save_html(str(html_path))
        assert html_path.exists()
        assert html_path.stat().st_size > 0


# ============================================================
# Tests: MIF Level
# ============================================================

class TestMIFLevelDetermination:
    def test_mif_without_db(self, report_gen):
        result = report_gen._determine_mif_level(1000.0)
        assert result['mif_level'] is None
        assert result['required_bw_mbs'] == 1000.0 * 1.25

    def test_mif_required_bw_calculation(self, report_gen):
        result = report_gen._determine_mif_level(500.0)
        assert result['required_bw_mbs'] == 500.0 * 1.25
        assert result['bw_margin'] == 1.25


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

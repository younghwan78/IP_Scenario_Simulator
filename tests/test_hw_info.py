"""
Unit tests for CSV-based HW info loading and DVFS/voltage resolution.
"""

import pytest
import sys
import os
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_info import (
    IPInfo, DVFSLevel, DVFSTable, HWInfoDB,
    load_info_csv, load_dvfs_csv, create_hw_info_db
)
from src.model.hw_resolver import (
    ResolvedIPConfig, HWResolver, REFERENCE_VOLTAGE_MV, REFERENCE_FPS
)
from src.model.hw_nodes import IPNode, ProcessorNode, MemoryNode


# ============================================================
# Fixtures: Sample CSV content
# ============================================================

SAMPLE_INFO_CSV = """\
Project,TestProject,,,,,
Name,Mode,Unit Power,IDC,PPC,VDD,DVFS
IP_A,Normal,10.5,2.0,4,VDD_CAM,CAM
IP_A,LowPower,5.0,1.0,2,VDD_CAM,CAM
IP_B,Normal,8.0,1.5,2,VDD_CAM,CAM
IP_C,Normal,15.0,3.0,1,VDD_INT,INT
"""

SAMPLE_DVFS_CSV = """\
TestProject,v1.0,,,,,,,,,,
CAM,,,,,,,,,,
LEVEL,SPEED,ASV0,ASV1,ASV2,ASV3,ASV4,ASV5,ASV6,ASV7,ASV8
0,800.0,900,875,850,825,800,775,750,725,700
1,600.0,850,825,800,775,750,725,700,675,650
2,400.0,750,725,700,675,650,625,600,575,550
3,200.0,650,625,600,575,550,525,500,475,450

INT,,,,,,,,,,
LEVEL,SPEED,ASV0,ASV1,ASV2,ASV3,ASV4,ASV5,ASV6,ASV7,ASV8
0,1200.0,950,925,900,875,850,825,800,775,750
1,800.0,850,825,800,775,750,725,700,675,650
2,400.0,700,675,650,625,600,575,550,525,500
"""


@pytest.fixture
def info_csv_path(tmp_path):
    """Create a temporary info CSV file."""
    p = tmp_path / "test_info.csv"
    p.write_text(SAMPLE_INFO_CSV, encoding='utf-8')
    return str(p)


@pytest.fixture
def dvfs_csv_path(tmp_path):
    """Create a temporary DVFS CSV file."""
    p = tmp_path / "test_dvfs.csv"
    p.write_text(SAMPLE_DVFS_CSV, encoding='utf-8')
    return str(p)


@pytest.fixture
def hw_info_db(info_csv_path, dvfs_csv_path):
    """Create a complete HWInfoDB."""
    return create_hw_info_db(info_csv_path, dvfs_csv_path)


@pytest.fixture
def hw_registry():
    """Create a sample HW registry."""
    return {
        "IP_A": IPNode(name="IP_A", clock_freq=1e9, ppc=4),
        "IP_B": IPNode(name="IP_B", clock_freq=1e9, ppc=2),
        "IP_C": IPNode(name="IP_C", clock_freq=1e9, ppc=1),
    }


# ============================================================
# Tests: CSV Parsing
# ============================================================

class TestLoadInfoCSV:
    """Tests for info.csv parsing."""

    def test_load_basic(self, info_csv_path):
        """Test basic info.csv loading."""
        project, infos = load_info_csv(info_csv_path)
        assert project == "TestProject"
        assert "IP_A" in infos
        assert "IP_B" in infos
        assert "IP_C" in infos

    def test_multiple_modes(self, info_csv_path):
        """Test IP with multiple modes (IP_A has Normal and LowPower)."""
        _, infos = load_info_csv(info_csv_path)
        assert len(infos["IP_A"]) == 2
        modes = [info.mode for info in infos["IP_A"]]
        assert "Normal" in modes
        assert "LowPower" in modes

    def test_ip_info_fields(self, info_csv_path):
        """Test that all fields are parsed correctly."""
        _, infos = load_info_csv(info_csv_path)
        ip_a_normal = [i for i in infos["IP_A"] if i.mode == "Normal"][0]
        assert ip_a_normal.unit_power == 10.5
        assert ip_a_normal.idc == 2.0
        assert ip_a_normal.ppc == 4
        assert ip_a_normal.vdd == "VDD_CAM"
        assert ip_a_normal.dvfs_group == "CAM"

    def test_file_not_found(self):
        """Test FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            load_info_csv("/nonexistent/path.csv")


class TestLoadDVFSCSV:
    """Tests for dvfs.csv parsing."""

    def test_load_basic(self, dvfs_csv_path):
        """Test basic dvfs.csv loading."""
        tables = load_dvfs_csv(dvfs_csv_path)
        assert "CAM" in tables
        assert "INT" in tables

    def test_dvfs_levels(self, dvfs_csv_path):
        """Test that DVFS levels are parsed correctly."""
        tables = load_dvfs_csv(dvfs_csv_path)
        cam = tables["CAM"]
        assert len(cam.levels) == 4
        
        level0 = cam.get_level(0)
        assert level0 is not None
        assert level0.speed == 800.0
        assert level0.voltages[4] == 800.0  # ASV4

    def test_dvfs_voltage_lookup(self, dvfs_csv_path):
        """Test voltage lookup by level and ASV group."""
        tables = load_dvfs_csv(dvfs_csv_path)
        cam = tables["CAM"]
        level1 = cam.get_level(1)
        assert cam.get_voltage(level1, 4) == 750.0

    def test_file_not_found(self):
        """Test FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            load_dvfs_csv("/nonexistent/path.csv")


# ============================================================
# Tests: HWInfoDB
# ============================================================

class TestHWInfoDB:
    """Tests for HWInfoDB."""

    def test_get_ip_info_by_mode(self, hw_info_db):
        """Test getting IPInfo by name and mode."""
        info = hw_info_db.get_ip_info("IP_A", "Normal")
        assert info is not None
        assert info.unit_power == 10.5

        info_lp = hw_info_db.get_ip_info("IP_A", "LowPower")
        assert info_lp is not None
        assert info_lp.unit_power == 5.0

    def test_get_ip_info_default_mode(self, hw_info_db):
        """Test fallback to first mode when requested mode not found."""
        info = hw_info_db.get_ip_info("IP_A", "NonexistentMode")
        assert info is not None  # Falls back to first mode

    def test_get_ip_info_missing(self, hw_info_db):
        """Test None return for non-existent IP."""
        info = hw_info_db.get_ip_info("NonexistentIP")
        assert info is None

    def test_validate_against_hw_valid(self, hw_info_db, hw_registry):
        """Test validation passes for matching HW registry."""
        errors = hw_info_db.validate_against_hw(hw_registry)
        assert len(errors) == 0

    def test_validate_against_hw_missing_ip(self, hw_info_db):
        """Test validation fails for IP not in info.csv."""
        registry = {
            "IP_A": IPNode(name="IP_A", clock_freq=1e9),
            "UNKNOWN_IP": IPNode(name="UNKNOWN_IP", clock_freq=1e9),
        }
        errors = hw_info_db.validate_against_hw(registry)
        assert len(errors) == 1
        assert "UNKNOWN_IP" in errors[0]

    def test_validate_skips_non_ip(self, hw_info_db):
        """Test validation skips Processor/Memory nodes."""
        registry = {
            "IP_A": IPNode(name="IP_A", clock_freq=1e9),
            "CPU": ProcessorNode(name="CPU", clock_freq=2e9),
            "DRAM": MemoryNode(name="DRAM"),
        }
        errors = hw_info_db.validate_against_hw(registry)
        assert len(errors) == 0


# ============================================================
# Tests: DVFS Table Resolution
# ============================================================

class TestDVFSTableResolution:
    """Tests for DVFS table clock/voltage selection."""

    def test_find_min_level_for_speed(self, dvfs_csv_path):
        """Test finding minimum level meeting speed requirement."""
        tables = load_dvfs_csv(dvfs_csv_path)
        cam = tables["CAM"]

        # Require 500 MHz → should get level 1 (600 MHz)
        level = cam.find_min_level_for_speed(500.0)
        assert level is not None
        assert level.level == 1
        assert level.speed == 600.0

    def test_find_exact_speed(self, dvfs_csv_path):
        """Test finding level for exact speed match."""
        tables = load_dvfs_csv(dvfs_csv_path)
        cam = tables["CAM"]

        # Require exactly 400 MHz → should get level 2
        level = cam.find_min_level_for_speed(400.0)
        assert level.level == 2

    def test_find_highest_speed(self, dvfs_csv_path):
        """Test when required speed exceeds all levels."""
        tables = load_dvfs_csv(dvfs_csv_path)
        cam = tables["CAM"]

        # Require 1000 MHz → no level can satisfy
        level = cam.find_min_level_for_speed(1000.0)
        assert level is None

    def test_find_lowest_speed(self, dvfs_csv_path):
        """Test finding minimum speed requirement."""
        tables = load_dvfs_csv(dvfs_csv_path)
        cam = tables["CAM"]

        # Require 100 MHz → should get level 3 (200 MHz, lowest sufficient)
        level = cam.find_min_level_for_speed(100.0)
        assert level.level == 3
        assert level.speed == 200.0


# ============================================================
# Tests: HW Resolver
# ============================================================

class TestHWResolver:
    """Tests for DVFS & Voltage domain resolution."""

    def _make_scenario_mock(self, tasks_config):
        """Create a minimal scenario mock with tasks."""
        from src.model.scenario import ScenarioGraph
        scenario = ScenarioGraph(name="test")
        for t in tasks_config:
            scenario.add_task(t['id'], t['hw'],
                              width=t.get('width', 0),
                              height=t.get('height', 0),
                              ip_mode=t.get('mode'))
        return scenario

    def test_clock_resolution_with_margin(self, hw_info_db, hw_registry):
        """Test that sw_margin increases required_clock."""
        scenario = self._make_scenario_mock([
            {'id': 't1', 'hw': 'IP_A', 'width': 1920, 'height': 1080},
        ])
        config = {'sw_margin': 0.15}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, config)

        # IP_A: ppc=4, 1920*1080=2073600 pixels, fps=30
        # base_clock = 2073600 * 30 / (4 * 1e6) = 15.552 MHz
        # required_clock = 15.552 * 1.15 = 17.8848 MHz
        cfg = resolved['IP_A']
        expected_base = 1920 * 1080 * 30 / (4 * 1e6)
        expected_req = expected_base * 1.15
        assert abs(cfg.required_clock - expected_req) < 0.01

        # DVFS level: 200 MHz is lowest >= 17.88 → level 3
        assert cfg.set_clock == 200.0
        assert cfg.dvfs_level == 3

    def test_same_dvfs_group_alignment(self, hw_info_db, hw_registry):
        """Test that same DVFS group IPs align to highest required_clock."""
        # IP_A (ppc=4) and IP_B (ppc=2) are both in CAM group
        # IP_B with same resolution needs higher clock due to lower ppc
        scenario = self._make_scenario_mock([
            {'id': 't1', 'hw': 'IP_A', 'width': 1920, 'height': 1080},
            {'id': 't2', 'hw': 'IP_B', 'width': 1920, 'height': 1080},
        ])
        config = {'sw_margin': 0.0}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, config)

        # Both should have the same required_clock (the higher one)
        assert resolved['IP_A'].required_clock == resolved['IP_B'].required_clock

        # IP_B has ppc=2, so its base is higher:
        # IP_B base = 1920*1080*30 / (2*1e6) = 31.104 MHz
        # IP_A base = 1920*1080*30 / (4*1e6) = 15.552 MHz
        # Aligned = 31.104 MHz
        expected = 1920 * 1080 * 30 / (2 * 1e6)
        assert abs(resolved['IP_A'].required_clock - expected) < 0.01

    def test_voltage_domain_alignment(self, hw_info_db, hw_registry):
        """Test that same VDD domain IPs get highest voltage."""
        scenario = self._make_scenario_mock([
            {'id': 't1', 'hw': 'IP_A', 'width': 3840, 'height': 2160},
            {'id': 't2', 'hw': 'IP_B', 'width': 1920, 'height': 1080},
            {'id': 't3', 'hw': 'IP_C', 'width': 1920, 'height': 1080},
        ])
        config = {'sw_margin': 0.0}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, config)

        # IP_A and IP_B are in VDD_CAM → same set_voltage
        assert resolved['IP_A'].set_voltage == resolved['IP_B'].set_voltage

        # IP_C is in VDD_INT → different voltage
        # They might coincidentally be the same value, but VDD domain is different
        assert resolved['IP_C'].vdd == "VDD_INT"
        assert resolved['IP_A'].vdd == "VDD_CAM"

    def test_power_calculation(self, hw_info_db, hw_registry):
        """Test power calculation formula."""
        scenario = self._make_scenario_mock([
            {'id': 't1', 'hw': 'IP_A', 'width': 1920, 'height': 1080},
        ])
        config = {'sw_margin': 0.0}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, config)

        cfg = resolved['IP_A']
        resolution_mp = 1920 * 1080 / 1e6  # ~2.0736 MP

        # Active Power = unit_power × resolution_mp × (V/710)² × (fps/30)
        v_scale = (cfg.set_voltage / 710.0) ** 2
        expected_active = 10.5 * resolution_mp * v_scale * (30.0 / 30.0)
        assert abs(cfg.get_active_power() - expected_active) < 0.001

        # Idle Power = IDC × (V/710)²
        expected_idle = 2.0 * v_scale
        assert abs(cfg.get_idle_power() - expected_idle) < 0.001

    def test_apply_to_hw(self, hw_info_db, hw_registry):
        """Test that resolved configs are applied to HW nodes."""
        scenario = self._make_scenario_mock([
            {'id': 't1', 'hw': 'IP_A', 'width': 1920, 'height': 1080},
        ])
        config = {'sw_margin': 0.0}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, config)
        resolver.apply_to_hw(hw_registry, resolved)

        ip_a = hw_registry['IP_A']
        assert ip_a.set_clock > 0
        assert ip_a.set_voltage > 0
        assert ip_a.unit_power == 10.5
        assert ip_a.dvfs_group == "CAM"
        assert ip_a.vdd == "VDD_CAM"
        # clock_freq should be updated to set_clock in Hz
        assert ip_a.clock_freq == ip_a.set_clock * 1e6

    def test_backward_compatibility_no_csv(self):
        """Test that IPNode works without CSV data (legacy mode)."""
        ip = IPNode(name="legacy_ip", clock_freq=600e6, ppc=4, efficiency=0.95,
                    power_static=10.0, power_dynamic=50.0)

        # Processing time should use legacy formula
        pixels = 1920 * 1080
        time = ip.get_processing_time({'width': 1920, 'height': 1080, 'h_blank_margin': 0})
        expected = pixels / (600e6 * 4 * 0.95)
        assert abs(time - expected) < 1e-12

        # Power should use legacy formula
        ip.utilization = 1.0
        energy = ip.get_power_consumption(0.01)  # 10ms
        expected_energy = (10.0 + 50.0 * 1.0) * 0.01
        assert abs(energy - expected_energy) < 1e-6

    def test_csv_mode_processing_time(self):
        """Test that IPNode uses set_clock for processing time when CSV loaded."""
        ip = IPNode(name="csv_ip", clock_freq=600e6, ppc=4)
        ip.set_clock = 400.0  # 400 MHz

        pixels = 1920 * 1080
        time = ip.get_processing_time({'width': 1920, 'height': 1080, 'h_blank_margin': 0})
        expected = pixels / (400e6 * 4)  # Uses set_clock, no efficiency
        assert abs(time - expected) < 1e-12

    def test_exploration_report(self, hw_info_db, hw_registry):
        """Test that exploration report is generated without error."""
        scenario = self._make_scenario_mock([
            {'id': 't1', 'hw': 'IP_A', 'width': 1920, 'height': 1080},
            {'id': 't2', 'hw': 'IP_B', 'width': 1920, 'height': 1080},
        ])
        config = {'sw_margin': 0.15}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, config)
        report = resolver.get_exploration_report(resolved)

        assert "CAM" in report
        assert "IP_A" in report
        assert "IP_B" in report
        assert "Power Summary" in report


class TestDVFSMismatchErrors:
    """Tests for DVFS/name mismatch error detection."""

    def test_missing_dvfs_group(self, tmp_path):
        """Test error when DVFS group in info.csv not found in dvfs.csv."""
        info_csv = tmp_path / "info.csv"
        info_csv.write_text("""\
Project,Test,,,,,
Name,Mode,Unit Power,IDC,PPC,VDD,DVFS
IP_X,Normal,10.0,1.0,4,VDD_X,MISSING_GROUP
""", encoding='utf-8')

        dvfs_csv = tmp_path / "dvfs.csv"
        dvfs_csv.write_text("""\
Test,v1,,,,,,,,,,
OTHER_GROUP,,,,,,,,,,
LEVEL,SPEED,ASV0,ASV1,ASV2,ASV3,ASV4,ASV5,ASV6,ASV7,ASV8
0,800,900,875,850,825,800,775,750,725,700
""", encoding='utf-8')

        db = create_hw_info_db(str(info_csv), str(dvfs_csv))
        registry = {"IP_X": IPNode(name="IP_X", clock_freq=1e9)}
        errors = db.validate_against_hw(registry)
        assert len(errors) == 1
        assert "MISSING_GROUP" in errors[0]

    def test_missing_ip_name(self, tmp_path):
        """Test error when IP in hw_registry not found in info.csv."""
        info_csv = tmp_path / "info.csv"
        info_csv.write_text("""\
Project,Test,,,,,
Name,Mode,Unit Power,IDC,PPC,VDD,DVFS
IP_X,Normal,10.0,1.0,4,VDD_X,GRP
""", encoding='utf-8')

        dvfs_csv = tmp_path / "dvfs.csv"
        dvfs_csv.write_text("""\
Test,v1,,,,,,,,,,
GRP,,,,,,,,,,
LEVEL,SPEED,ASV0,ASV1,ASV2,ASV3,ASV4,ASV5,ASV6,ASV7,ASV8
0,800,900,875,850,825,800,775,750,725,700
""", encoding='utf-8')

        db = create_hw_info_db(str(info_csv), str(dvfs_csv))
        registry = {
            "IP_X": IPNode(name="IP_X", clock_freq=1e9),
            "NOT_IN_CSV": IPNode(name="NOT_IN_CSV", clock_freq=1e9),
        }
        errors = db.validate_against_hw(registry)
        assert len(errors) == 1
        assert "NOT_IN_CSV" in errors[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

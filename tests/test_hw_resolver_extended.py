"""
Extended tests for HWResolver edge cases.

Covers VDD leader logic, apply_to_hw, SW margin effect.
Uses create_hw_info_db() with correct CSV format.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode, SensorNode
from src.model.hw_info import HWInfoDB, create_hw_info_db
from src.model.hw_resolver import HWResolver, ResolvedIPConfig
from src.model.scenario import ScenarioGraph


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def hw_info_db(tmp_path):
    """Create a minimal HWInfoDB with two DVFS groups and VDD domains."""
    info_csv = tmp_path / "test_info.csv"
    info_csv.write_text(
        "Project,TestProject,,,,,\n"
        "Name,Mode,Unit Power,IDC,PPC,VDD,DVFS\n"
        "ISP_A,Normal,0.5,0.1,4,VDD_CAM,CAM\n"
        "ISP_B,Normal,0.8,0.2,2,VDD_CAM,CAM\n"
        "CODEC,Normal,1.2,0.3,1,VDD_MFC,MFC\n"
    )
    dvfs_csv = tmp_path / "test_dvfs.csv"
    dvfs_csv.write_text(
        "TestProject,v1,,,,,,,,,\n"
        "CAM,,,,,,,,,,,\n"
        "LEVEL,SPEED,ASV0,ASV1,ASV2,ASV3,ASV4,ASV5,ASV6,ASV7\n"
        "0,600,800,790,780,770,760,750,740,730\n"
        "1,400,700,690,680,670,660,650,640,630\n"
        "2,200,600,590,580,570,560,550,540,530\n"
        "\n"
        "MFC,,,,,,,,,,,\n"
        "LEVEL,SPEED,ASV0,ASV1,ASV2,ASV3,ASV4,ASV5,ASV6,ASV7\n"
        "0,500,750,740,730,720,710,700,690,680\n"
        "1,300,650,640,630,620,610,600,590,580\n"
    )
    return create_hw_info_db(str(info_csv), str(dvfs_csv))


@pytest.fixture
def hw_registry():
    """HW registry with two ISPs in same VDD and one codec in different VDD."""
    return {
        "ISP_A": IPNode(name="ISP_A", clock_freq=600e6, ppc=4, efficiency=1.0),
        "ISP_B": IPNode(name="ISP_B", clock_freq=400e6, ppc=2, efficiency=1.0),
        "CODEC": IPNode(name="CODEC", clock_freq=500e6, ppc=1, efficiency=1.0),
    }


def _make_scenario(tasks_config):
    """Helper to create a scenario from task configs."""
    scenario = ScenarioGraph(name="TestScenario")
    for tid, hw, pixels in tasks_config:
        scenario.add_task(tid, hw, pixels=pixels, h_blank_margin=0)
    return scenario


# ============================================================
# Tests
# ============================================================

class TestVDDLeaderLogic:
    """Test VDD domain voltage alignment (leader selection)."""

    def test_same_vdd_gets_same_voltage(self, hw_info_db, hw_registry):
        """ISP_A and ISP_B share VDD_CAM.  They should get the same set_voltage."""
        scenario = _make_scenario([
            ("t_a", "ISP_A", 1_000_000),
            ("t_b", "ISP_B", 1_000_000),
            ("t_c", "CODEC", 1_000_000),
        ])
        scenario_config = {'scenario': {'fps': 30, 'sw_margin': 0.15}}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, scenario_config)

        # Both ISP_A and ISP_B share VDD_CAM — same set_voltage
        assert resolved["ISP_A"].set_voltage == resolved["ISP_B"].set_voltage
        # CODEC is in VDD_MFC — could differ
        assert resolved["CODEC"].vdd == "VDD_MFC"

    def test_vdd_leader_non_empty(self, hw_info_db, hw_registry):
        """VDD leader should be set for all IPs in a shared VDD group."""
        scenario = _make_scenario([
            ("t_a", "ISP_A", 2_000_000),
            ("t_b", "ISP_B", 500_000),
        ])
        scenario_config = {'scenario': {'fps': 30, 'sw_margin': 0.15}}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, scenario_config)

        assert resolved["ISP_A"].vdd_leader != ""
        assert resolved["ISP_B"].vdd_leader != ""

    def test_different_vdd_independent(self, hw_info_db, hw_registry):
        """IPs in different VDD domains should have independent voltages."""
        scenario = _make_scenario([
            ("t_a", "ISP_A", 1_000_000),
            ("t_c", "CODEC", 1_000_000),
        ])
        scenario_config = {'scenario': {'fps': 30, 'sw_margin': 0.15}}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, scenario_config)

        assert resolved["ISP_A"].vdd != resolved["CODEC"].vdd


class TestApplyToHW:
    """Test that resolved configs are applied to HW nodes."""

    def test_apply_updates_ipnode_fields(self, hw_info_db, hw_registry):
        """After apply_to_hw, IPNode should have set_clock and set_voltage."""
        scenario = _make_scenario([
            ("t_a", "ISP_A", 1_000_000),
        ])
        scenario_config = {'scenario': {'fps': 30, 'sw_margin': 0.15}}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, scenario_config)
        resolver.apply_to_hw(hw_registry, resolved)

        ip = hw_registry["ISP_A"]
        assert ip.set_clock > 0, "set_clock should be set after apply_to_hw"
        assert ip.set_voltage > 0, "set_voltage should be set after apply_to_hw"
        assert ip.dvfs_level >= 0, "dvfs_level should be set"


class TestSWMarginEffect:
    """Test that sw_margin affects required_clock calculation."""

    def test_higher_margin_higher_clock(self, hw_info_db, hw_registry):
        """Higher sw_margin should result in higher required_clock."""
        scenario = _make_scenario([
            ("t_a", "ISP_A", 1_000_000),
        ])

        resolver = HWResolver(hw_info_db, asv_group=4)

        resolved_low = resolver.resolve_scenario(
            hw_registry, scenario,
            {'scenario': {'fps': 30, 'sw_margin': 0.10}}
        )

        resolved_high = resolver.resolve_scenario(
            hw_registry, scenario,
            {'scenario': {'fps': 30, 'sw_margin': 0.30}}
        )

        assert resolved_high["ISP_A"].required_clock > resolved_low["ISP_A"].required_clock


class TestExplorationReport:
    """Test HWResolver.get_exploration_report."""

    def test_exploration_report_returns_string(self, hw_info_db, hw_registry):
        """Exploration report should return a string."""
        scenario = _make_scenario([
            ("t_a", "ISP_A", 1_000_000),
            ("t_c", "CODEC", 1_000_000),
        ])
        scenario_config = {'scenario': {'fps': 30, 'sw_margin': 0.15}}

        resolver = HWResolver(hw_info_db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, scenario, scenario_config)

        report = resolver.get_exploration_report(resolved)
        assert isinstance(report, str)
        assert len(report) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

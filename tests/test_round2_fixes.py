"""
Regression tests for the round-2 review fixes
(internal_docs/code_review_round2_20260718.md).

Covers:
- B-1: publish_report renames raw output files to the canonical
       (index-parseable) form; parse_report_filename handles both forms
- B-2: main.py --publish shares the naming rule (build_publish_name)
- B-3: simulator HW capability validation delegates to
       ScenarioGraph.validate_constraints (scale check now applies)
- B-4: view comp badge uses the shared comp_enabled() semantics
- B-5: multi-sensor frame interval warning (first sensor wins)
- C-2: exploration prunes DVFS levels that can never meet required clock
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from publish_report import (
    parse_report_filename, build_publish_name, find_report_files, publish_files,
)
from src.model.bw import comp_enabled
from src.model.hw_nodes import IPNode, SensorNode
from src.model.modules import ScalerModule
from src.model.scenario import ScenarioGraph
from src.model.hw_info import HWInfoDB, IPInfo, DVFSTable, DVFSLevel
from src.controller.simulator import SoCSimulator
from src.controller.exploration import ExplorationEngine


# ============================================================
# B-1: publish filename parsing / renaming
# ============================================================

class TestParseReportFilename:
    def test_published_form(self):
        info = parse_report_filename(
            "projectA-FHD30_Recording-20260218-014100-YHJOO_simulation_result.html")
        assert info == {
            'project': 'projectA',
            'scenario': 'FHD30_Recording',
            'timestamp': '20260218-014100',
            'writer': 'YHJOO',
            'suffix': 'simulation_result.html',
        }

    def test_raw_output_form(self):
        info = parse_report_filename(
            "projectA-FHD30_Recording_simulation_result.html")
        assert info['project'] == 'projectA'
        assert info['scenario'] == 'FHD30_Recording'
        assert info['timestamp'] is None
        assert info['writer'] is None
        assert info['suffix'] == 'simulation_result.html'

    def test_raw_form_with_underscore_suffix(self):
        # 'timing_chart.html' contains an underscore — the split must not
        # leak 'timing' into the scenario name
        info = parse_report_filename(
            "projectA-FHD30_Recording_timing_chart.html")
        assert info['scenario'] == 'FHD30_Recording'
        assert info['suffix'] == 'timing_chart.html'

    def test_unknown_file_returns_none(self):
        assert parse_report_filename("random_notes.html") is None

    def test_build_publish_name_roundtrip(self):
        name = build_publish_name('projectA', 'FHD30_Recording',
                                  '20260718-120000', 'tester',
                                  'bw_chart.html')
        info = parse_report_filename(name)
        assert info['timestamp'] == '20260718-120000'
        assert info['writer'] == 'tester'
        assert info['suffix'] == 'bw_chart.html'


class TestPublishRename(object):
    def test_raw_files_are_renamed_on_publish(self, tmp_path):
        src = tmp_path / "output_simulation"
        src.mkdir()
        raw = src / "projectA-FHD30_Recording_results.csv"
        raw.write_text("a,b\n1,2\n")

        files = find_report_files([str(src)])
        assert len(files) == 1

        dest = tmp_path / "reports"
        published = publish_files(files, str(dest), default_writer='tester')
        assert len(published) == 1
        published_name = os.path.basename(published[0])
        # Must now be parseable by the index generator (strict form)
        info = parse_report_filename(published_name)
        assert info['timestamp'] is not None
        assert info['writer'] == 'tester'
        assert info['suffix'] == 'results.csv'
        assert os.path.isfile(published[0])

    def test_published_files_keep_their_name(self, tmp_path):
        src = tmp_path / "output_simulation"
        src.mkdir()
        name = "projectA-FHD30_Recording-20260218-014100-YHJOO_results.csv"
        (src / name).write_text("a,b\n")

        files = find_report_files([str(src)])
        dest = tmp_path / "reports"
        published = publish_files(files, str(dest))
        assert os.path.basename(published[0]) == name


# ============================================================
# B-3: unified capability validation (scale check at run())
# ============================================================

class TestUnifiedValidation:
    def _sim_with_scaling_ip(self, supports_scale: bool) -> SoCSimulator:
        ip = IPNode(name="IP_S", clock_freq=1e9, ppc=1.0,
                    supports_scale=supports_scale)
        ip.add_module(ScalerModule(name="SC0", scale_factor=(0.5, 0.5)))
        sim = SoCSimulator()
        sim.register_hw(ip)
        sc = ScenarioGraph("scale_check")
        sc.add_task("t1", "IP_S", width=100, height=100)
        sim.load_scenario(sc)
        return sim

    def test_scaling_without_support_fails_at_run(self):
        sim = self._sim_with_scaling_ip(supports_scale=False)
        with pytest.raises(ValueError, match="supports_scale"):
            sim.run()

    def test_scaling_with_support_runs(self):
        sim = self._sim_with_scaling_ip(supports_scale=True)
        results = sim.run()
        assert len(results.task_results) == 1

    def test_unknown_hw_still_allowed_via_placeholder(self):
        # Simulator must keep its placeholder behavior for unregistered HW
        sim = SoCSimulator()
        sc = ScenarioGraph("placeholder")
        sc.add_task("t1", "UNREGISTERED_HW", width=10, height=10)
        sim.load_scenario(sc)
        results = sim.run()
        assert results.get_by_task("t1") is not None


# ============================================================
# B-4: shared comp semantics
# ============================================================

class TestCompEnabled:
    def test_semantics(self):
        assert comp_enabled('enable')
        assert comp_enabled('SBWC')
        assert comp_enabled('AFBC')
        assert not comp_enabled('disable')
        assert not comp_enabled('')
        assert not comp_enabled(None)


# ============================================================
# B-5: multi-sensor frame interval
# ============================================================

class TestMultiSensorFrameInterval:
    def test_first_sensor_wins_with_warning(self, capsys):
        sim = SoCSimulator()
        sim.register_hw(SensorNode(name="CAM0", fps=30.0))
        sim.register_hw(SensorNode(name="CAM1", fps=60.0))
        interval = sim._get_frame_interval()
        assert interval == pytest.approx(1.0 / 30.0)
        assert "Multiple sensors" in capsys.readouterr().out

    def test_single_sensor_no_warning(self, capsys):
        sim = SoCSimulator()
        sim.register_hw(SensorNode(name="CAM0", fps=60.0))
        assert sim._get_frame_interval() == pytest.approx(1.0 / 60.0)
        assert "Multiple sensors" not in capsys.readouterr().out


# ============================================================
# C-2: exploration DVFS level pruning
# ============================================================

def _make_engine_with_levels():
    """One IP requiring ~73 MHz; DVFS table has 100 MHz and 50 MHz levels."""
    db = HWInfoDB(
        project_name="mini",
        ip_infos={
            "IPA": [IPInfo(name="IPA", mode="Normal", unit_power=1.0,
                           idc=0.0, ppc=1.0, vdd="V1", dvfs_group="G")],
        },
        dvfs_tables={
            "G": DVFSTable(name="G", levels=[
                DVFSLevel(level=0, speed=100.0, voltages={4: 700.0}),
                DVFSLevel(level=1, speed=50.0, voltages={4: 600.0}),
            ]),
        },
    )
    sc = ScenarioGraph("mini")
    sc.add_task("t1", "IPA", width=1920, height=1080)
    engine = ExplorationEngine(
        hw_info_db=db, scenario=sc,
        scenario_config={'fps': 30.0, 'sw_margin': 0.15},
        hw_registry={"IPA": IPNode(name="IPA", ppc=1.0)}, asv_group=4,
    )
    return engine


class TestExplorationPruning:
    def test_infeasible_levels_are_pruned(self):
        engine = _make_engine_with_levels()
        engine.sweep_dvfs = {"G": [0, 1]}
        result = engine.run()
        # Level 1 (50 MHz < ~73 MHz required) pruned before the sweep
        assert engine.sweep_dvfs["G"] == [0]
        assert result.total_combinations == 1
        assert result.feasible_count == 1

    def test_no_pruning_when_mode_swept(self):
        engine = _make_engine_with_levels()
        engine.sweep_dvfs = {"G": [0, 1]}
        # Mode sweep on the same IP can change ppc → required_clock,
        # so pruning must be skipped
        engine.sweep_modes = {"IPA": ["Normal"]}
        result = engine.run()
        assert engine.sweep_dvfs["G"] == [0, 1]
        assert result.total_combinations == 2

"""
Tests for ExplorationEngine — parameter sweep.

Covers combination generation, single evaluation, ranking,
and utility functions.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode
from src.model.hw_info import create_hw_info_db
from src.model.scenario import ScenarioGraph
from src.controller.exploration import (
    ExplorationEngine, _is_dma_port, _calc_bw_for_port,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def hw_info_db(tmp_path):
    """HW info database with 3 DVFS levels."""
    info_csv = tmp_path / "test_info.csv"
    info_csv.write_text(
        "Project,TestProject,,,,,\n"
        "Name,Mode,Unit Power,IDC,PPC,VDD,DVFS\n"
        "IP_A,Normal,0.5,0.1,4,VDD_A,DVFS_A\n"
        "IP_B,Normal,0.8,0.2,2,VDD_A,DVFS_A\n"
    )
    dvfs_csv = tmp_path / "test_dvfs.csv"
    dvfs_csv.write_text(
        "TestProject,v1,,,,,,,,,\n"
        "DVFS_A,,,,,,,,,,,\n"
        "LEVEL,SPEED,ASV0,ASV1,ASV2,ASV3,ASV4,ASV5,ASV6,ASV7\n"
        "0,600,800,790,780,770,760,750,740,730\n"
        "1,400,700,690,680,670,660,650,640,630\n"
        "2,200,600,590,580,570,560,550,540,530\n"
    )
    return create_hw_info_db(str(info_csv), str(dvfs_csv))


@pytest.fixture
def hw_registry():
    return {
        "IP_A": IPNode(name="IP_A", clock_freq=600e6, ppc=4, efficiency=1.0),
        "IP_B": IPNode(name="IP_B", clock_freq=400e6, ppc=2, efficiency=1.0),
    }


@pytest.fixture
def scenario():
    s = ScenarioGraph(name="ExplorationTest")
    s.add_task("t_a", "IP_A", pixels=1_000_000, h_blank_margin=0)
    s.add_task("t_b", "IP_B", pixels=1_000_000, h_blank_margin=0)
    s.add_dependency("t_a", "t_b", "M2M")
    return s


@pytest.fixture
def scenario_config():
    return {
        'scenario': {
            'name': 'ExplorationTest',
            'fps': 30,
            'sw_margin': 0.15,
            'bw_power': 80.0,
            'vBat': 4.0,
            'pmic_efficiency': 0.85,
        }
    }


@pytest.fixture
def engine(hw_info_db, scenario, scenario_config, hw_registry):
    return ExplorationEngine(
        hw_info_db=hw_info_db,
        scenario=scenario,
        scenario_config=scenario_config,
        hw_registry=hw_registry,
        asv_group=4,
    )


# ============================================================
# Tests: Combination Generation
# ============================================================

class TestCombinationGeneration:
    def test_no_sweep_returns_single_combo(self, engine):
        combos = engine._generate_combinations()
        assert len(combos) == 1

    def test_dvfs_sweep_combinations(self, engine):
        engine.sweep_dvfs = {"DVFS_A": [0, 1, 2]}
        combos = engine._generate_combinations()
        assert len(combos) == 3

    def test_multi_axis_cartesian(self, engine):
        engine.sweep_dvfs = {"DVFS_A": [0, 1]}
        engine.sweep_modes = {"IP_A": ["Normal"]}
        combos = engine._generate_combinations()
        assert len(combos) >= 2


# ============================================================
# Tests: Evaluation
# ============================================================

class TestEvaluation:
    def test_baseline_evaluation(self, engine):
        result = engine._evaluate({})
        assert result.feasible is True
        assert result.total_power_mw >= 0

    def test_high_dvfs_feasible(self, engine):
        result = engine._evaluate({'dvfs': {'DVFS_A': 0}})
        assert result.feasible is True


# ============================================================
# Tests: Full Run
# ============================================================

class TestExplorationRun:
    def test_run_produces_baseline(self, engine):
        engine.sweep_dvfs = {"DVFS_A": [0, 1]}
        engine.timing_budget_ms = 33.33
        engine.top_k = 3

        result = engine.run()
        assert result.baseline is not None

    def test_run_candidates_sorted(self, engine):
        engine.sweep_dvfs = {"DVFS_A": [0, 1]}
        engine.timing_budget_ms = 33.33
        engine.top_k = 5

        result = engine.run()
        if len(result.candidates) >= 2:
            for i in range(len(result.candidates) - 1):
                assert result.candidates[i].total_power_mw <= result.candidates[i+1].total_power_mw

    def test_run_respects_top_k(self, engine):
        engine.sweep_dvfs = {"DVFS_A": [0, 1, 2]}
        engine.timing_budget_ms = 33.33
        engine.top_k = 2

        result = engine.run()
        assert len(result.candidates) <= 2

    def test_run_counts(self, engine):
        engine.sweep_dvfs = {"DVFS_A": [0, 1]}
        engine.timing_budget_ms = 33.33

        result = engine.run()
        assert result.total_combinations == 2
        assert result.feasible_count >= 0
        assert result.elapsed_sec >= 0


# ============================================================
# Tests: Utility Functions
# ============================================================

class TestExplorationUtilities:
    def test_is_dma_port(self):
        assert _is_dma_port("RDMA0") is True
        assert _is_dma_port("wdma_out") is True
        assert _is_dma_port("Scaler") is False

    def test_calc_bw_for_port_valid(self):
        port = {
            'size': [0, 0, 1920, 1080],
            'format': 'NV12',
            'bitwidth': 8,
            'r_w_rate': 1.0,
            'comp': 'disable',
        }
        result = _calc_bw_for_port(port, fps=30.0)
        assert result['bw_mbs'] > 0

    def test_calc_bw_for_port_empty(self):
        port = {'size': [], 'format': 'NV12'}
        result = _calc_bw_for_port(port, fps=30.0)
        assert result['bw_mbs'] == 0

    def test_calc_bw_compression(self):
        port_no_comp = {
            'size': [0, 0, 1920, 1080],
            'format': 'NV12', 'bitwidth': 8,
            'r_w_rate': 1.0, 'comp': 'disable',
        }
        port_with_comp = {
            'size': [0, 0, 1920, 1080],
            'format': 'NV12', 'bitwidth': 8,
            'r_w_rate': 1.0, 'comp': 'enable', 'comp_ratio': 0.5,
        }
        bw_no = _calc_bw_for_port(port_no_comp, fps=30.0)
        bw_with = _calc_bw_for_port(port_with_comp, fps=30.0)
        assert bw_with['bw_mbs'] < bw_no['bw_mbs']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

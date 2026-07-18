"""
Regression tests for the 2026-07 code review fixes
(internal_docs/code_review_20260717.md).

Covers:
- H-3: DMA results carry the correct frame_id in multi-frame runs
- H-4: M2M edges entering an OTF group get their DMA simulated
- H-5: OTF groups occupy HW resources (frame pipelining contention)
- H-6: OR_JOIN must not lose tokens from non-selected queues
- H-7: parallel edges keep per-channel data/transfer; conn_type conflicts raise
- H-8: simulator power unified with the report power formula
- H-9: exploration enforces the timing budget constraint
- M-7: VDD alignment respects the group-aligned set_clock's voltage
- M-9: TimingAnalyzer computes the true DAG critical path
- M-3: shared BW formula (src/model/bw.py)
- L-3: add_task does not mutate the caller's workload dict
- L-12: get_by_task frame_id filtering
"""

import pytest
import simpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode
from src.model.modules import DMAModule
from src.model.scenario import ScenarioGraph
from src.model.tokens import (
    TokenQueue, TokenJoin, JoinPolicy, create_source_token
)
from src.model.hw_info import HWInfoDB, IPInfo, DVFSTable, DVFSLevel
from src.model.hw_resolver import HWResolver, ResolvedIPConfig
from src.model.bw import calc_port_bw, is_dma_port_name
from src.controller.simulator import SoCSimulator
from src.controller.timing_analyzer import TimingAnalyzer
from src.controller.exploration import ExplorationEngine


# ============================================================
# Helpers
# ============================================================

def _make_dma_pair_sim():
    """Simulator with two IPs (writer WDMA0 / reader RDMA0) and an M2M
    edge carrying a transfer config."""
    sim = SoCSimulator()

    ip_a = IPNode(name="IP_A", clock_freq=1e9, ppc=1.0)
    ip_a.add_module(DMAModule(name="WDMA0", max_bandwidth=1e6, direction='write'))
    ip_b = IPNode(name="IP_B", clock_freq=1e9, ppc=1.0)
    ip_b.add_module(DMAModule(name="RDMA0", max_bandwidth=1e6, direction='read'))

    sim.register_hw(ip_a).register_hw(ip_b)

    sc = ScenarioGraph("dma_test")
    sc.add_task("tA", "IP_A", width=100, height=100)
    sc.add_task("tB", "IP_B", width=100, height=100)
    sc.add_dependency(
        "tA", "tB", conn_type="M2M",
        transfer={'write_dma': 'WDMA0', 'read_dma': 'RDMA0'},
        data={'format': 'NV12', 'compression': 'Linear'},
    )
    sim.load_scenario(sc)
    return sim


# ============================================================
# H-3: DMA frame_id correctness
# ============================================================

class TestDmaFrameId:
    def test_dma_results_have_correct_frame_ids(self):
        sim = _make_dma_pair_sim()
        results = sim.run(num_frames=3)

        dma_results = [r for r in results.task_results
                       if r.task_id.startswith('dma_')]
        assert dma_results, "expected DMA results"

        # Every frame must have its own Write+Read DMA records
        frames = {r.frame_id for r in dma_results}
        assert frames == {0, 1, 2}
        for fid in (0, 1, 2):
            per_frame = [r for r in dma_results if r.frame_id == fid]
            assert len(per_frame) == 2  # Write + Read


# ============================================================
# H-4: M2M DMA into an OTF group
# ============================================================

class TestOtfIncomingDma:
    def test_m2m_transfer_into_otf_group_is_simulated(self):
        sim = SoCSimulator()

        ip_a = IPNode(name="IP_A", clock_freq=1e9, ppc=1.0)
        ip_a.add_module(DMAModule(name="WDMA0", max_bandwidth=1e6, direction='write'))
        ip_b = IPNode(name="IP_B", clock_freq=1e9, ppc=1.0)
        ip_b.add_module(DMAModule(name="RDMA0", max_bandwidth=1e6, direction='read'))
        ip_c = IPNode(name="IP_C", clock_freq=1e9, ppc=1.0)
        sim.register_hw(ip_a).register_hw(ip_b).register_hw(ip_c)

        sc = ScenarioGraph("otf_dma_test")
        sc.add_task("tA", "IP_A", width=100, height=100)
        sc.add_task("tB", "IP_B", width=100, height=100)
        sc.add_task("tC", "IP_C", width=100, height=100)
        # tB—tC form an OTF group; tA→tB is M2M with a transfer config
        sc.add_dependency("tA", "tB", conn_type="M2M",
                          transfer={'write_dma': 'WDMA0', 'read_dma': 'RDMA0'},
                          data={'format': 'NV12'})
        sc.add_dependency("tB", "tC", conn_type="OTF")
        sim.load_scenario(sc)

        results = sim.run(num_frames=1)
        dma_results = [r for r in results.task_results
                       if r.task_id.startswith('dma_')]
        assert len(dma_results) == 2, \
            "M2M transfer into an OTF group member must be DMA-simulated"

        # OTF group must start only after the DMA completed
        dma_end = max(r.end_time for r in dma_results)
        tb = results.get_by_task("tB")
        assert tb.start_time >= dma_end - 1e-9


# ============================================================
# H-5: OTF group resource contention across frames
# ============================================================

class TestOtfResourceContention:
    def test_otf_group_frames_do_not_overlap_on_same_hw(self):
        sim = SoCSimulator()
        # 10000 px / (200 kHz × 1 ppc) = 50 ms  (> default 33.3ms frame interval)
        ip_x = IPNode(name="IP_X", clock_freq=200e3, ppc=1.0)
        ip_y = IPNode(name="IP_Y", clock_freq=1e9, ppc=1.0)
        sim.register_hw(ip_x).register_hw(ip_y)

        sc = ScenarioGraph("otf_contention")
        sc.add_task("t1", "IP_X", width=100, height=100, h_blank_margin=0.0)
        sc.add_task("t2", "IP_Y", width=100, height=100, h_blank_margin=0.0)
        sc.add_dependency("t1", "t2", conn_type="OTF")
        sim.load_scenario(sc)

        results = sim.run(num_frames=2)

        f0 = results.get_by_task("t1", frame_id=0)
        f1 = results.get_by_task("t1", frame_id=1)
        assert f0 is not None and f1 is not None
        # Frame 1 (nominal start 33.3ms) must wait until frame 0
        # releases IP_X at 50ms — no overlapped execution on one IP
        assert f0.duration == pytest.approx(0.05, rel=1e-6)
        assert f1.start_time >= f0.end_time - 1e-9


# ============================================================
# H-6: OR_JOIN token loss
# ============================================================

class TestOrJoinTokenLoss:
    def test_or_join_does_not_lose_late_tokens(self):
        env = simpy.Environment()
        qa = TokenQueue.create(env, 'a')
        qb = TokenQueue.create(env, 'b')
        join = TokenJoin(input_queues={'a': qa, 'b': qb},
                         policy=JoinPolicy.OR_JOIN, _env=env)

        received = []

        def consumer():
            tokens = yield from join.wait_for_tokens()
            received.append(tokens)
            tokens = yield from join.wait_for_tokens()
            received.append(tokens)

        def producer():
            yield qa.store.put(create_source_token(1, 10, 10))
            yield env.timeout(1.0)
            # This token previously got swallowed by the stale pending
            # get() left over from the first OR_JOIN wait
            yield qb.store.put(create_source_token(2, 20, 20))

        env.process(consumer())
        env.process(producer())
        env.run(until=10)

        assert len(received) == 2, "second OR_JOIN wait never completed (token lost)"
        assert 'a' in received[0] and received[0]['a'].frame_id == 1
        assert 'b' in received[1] and received[1]['b'].frame_id == 2


# ============================================================
# H-7: parallel edges keep per-channel configs
# ============================================================

class TestParallelEdgeChannels:
    def _base_scenario(self):
        sc = ScenarioGraph("parallel_edges")
        sc.add_task("tA", "IP_A", width=100, height=100)
        sc.add_task("tB", "IP_B", width=100, height=100)
        return sc

    def test_second_channel_data_is_preserved(self):
        sc = self._base_scenario()
        sc.add_dependency("tA", "tB", conn_type="M2M",
                          src_port="WDMA0", dst_port="RDMA0",
                          data={'format': 'NV12'},
                          transfer={'write_dma': 'WDMA0'})
        sc.add_dependency("tA", "tB", conn_type="M2M",
                          src_port="WDMA1", dst_port="RDMA1",
                          data={'format': 'STAT'},
                          transfer={'write_dma': 'WDMA1'})

        edge = sc.graph.edges["tA", "tB"]
        assert len(edge['port_pairs']) == 2
        channels = edge['channels']
        assert len(channels) == 2
        assert channels[0]['data'] == {'format': 'NV12'}
        assert channels[1]['data'] == {'format': 'STAT'}
        assert channels[1]['transfer'] == {'write_dma': 'WDMA1'}

    def test_conflicting_conn_type_raises(self):
        sc = self._base_scenario()
        sc.add_dependency("tA", "tB", conn_type="M2M")
        with pytest.raises(ValueError, match="[Cc]onflicting"):
            sc.add_dependency("tA", "tB", conn_type="OTF")

    def test_all_channels_are_dma_simulated(self):
        sim = SoCSimulator()
        ip_a = IPNode(name="IP_A", clock_freq=1e9, ppc=1.0)
        ip_a.add_module(DMAModule(name="WDMA0", max_bandwidth=1e6, direction='write'))
        ip_a.add_module(DMAModule(name="WDMA1", max_bandwidth=1e6, direction='write'))
        ip_b = IPNode(name="IP_B", clock_freq=1e9, ppc=1.0)
        sim.register_hw(ip_a).register_hw(ip_b)

        sc = self._base_scenario()
        sc.add_dependency("tA", "tB", conn_type="M2M",
                          transfer={'write_dma': 'WDMA0'}, data={'format': 'NV12'})
        sc.add_dependency("tA", "tB", conn_type="M2M",
                          transfer={'write_dma': 'WDMA1'}, data={'format': 'NV12'})
        sim.load_scenario(sc)

        results = sim.run(num_frames=1)
        dma_names = {r.hw_name for r in results.task_results
                     if r.task_id.startswith('dma_')}
        assert dma_names == {'WDMA0(Write)', 'WDMA1(Write)'}


# ============================================================
# H-8: unified power model
# ============================================================

class TestUnifiedPowerModel:
    def test_simulator_power_matches_report_formula(self):
        config = ResolvedIPConfig(
            ip_name="X", unit_power=10.0, input_resolution_mp=2.0,
            fps=30.0, set_voltage=710.0, set_clock=500.0, ppc=1,
        )
        ip = IPNode(name="X")
        resolver = HWResolver(HWInfoDB(), asv_group=4)
        resolver.apply_to_hw({"X": ip}, {"X": config})

        duration = 0.5  # seconds
        energy = ip.get_power_consumption(duration)
        # Report formula: 10 mW/MP × 2 MP × (710/710)² × (30/30) = 20 mW
        assert energy == pytest.approx(config.get_total_power() * duration)
        assert energy == pytest.approx(20.0 * duration)

    def test_voltage_scaling_consistency(self):
        config = ResolvedIPConfig(
            ip_name="X", unit_power=10.0, input_resolution_mp=1.0,
            fps=60.0, set_voltage=1000.0, set_clock=500.0, ppc=1,
        )
        ip = IPNode(name="X")
        HWResolver(HWInfoDB(), asv_group=4).apply_to_hw({"X": ip}, {"X": config})
        assert ip.get_power_consumption(1.0) == pytest.approx(
            config.get_total_power())


# ============================================================
# H-9: exploration timing budget
# ============================================================

def _make_mini_engine():
    """Minimal exploration engine: one IP, one DVFS level (100 MHz)."""
    db = HWInfoDB(
        project_name="mini",
        ip_infos={
            "IPA": [IPInfo(name="IPA", mode="Normal", unit_power=1.0,
                           idc=0.0, ppc=1.0, vdd="V1", dvfs_group="G")],
        },
        dvfs_tables={
            "G": DVFSTable(name="G", levels=[
                DVFSLevel(level=0, speed=100.0, voltages={4: 700.0}),
            ]),
        },
    )
    sc = ScenarioGraph("mini")
    sc.add_task("t1", "IPA", width=1920, height=1080)
    hw_registry = {"IPA": IPNode(name="IPA", ppc=1.0)}
    engine = ExplorationEngine(
        hw_info_db=db, scenario=sc,
        scenario_config={'fps': 30.0, 'sw_margin': 0.15},
        hw_registry=hw_registry, asv_group=4,
    )
    return engine


class TestExplorationTimingBudget:
    def test_budget_violation_marks_infeasible(self):
        engine = _make_mini_engine()
        # exec time = 1920×1080 / (100 MHz × 1 ppc) ≈ 20.7 ms
        engine.timing_budget_ms = 10.0
        result = engine._evaluate({})
        assert not result.feasible
        assert 'budget' in result.infeasible_reason

    def test_no_budget_stays_feasible(self):
        engine = _make_mini_engine()
        engine.timing_budget_ms = None
        result = engine._evaluate({})
        assert result.feasible
        assert result.hw_time_ms == pytest.approx(1920 * 1080 / 100e6 * 1000)

    def test_generous_budget_stays_feasible(self):
        engine = _make_mini_engine()
        engine.timing_budget_ms = 33.3
        result = engine._evaluate({})
        assert result.feasible


# ============================================================
# M-7: VDD alignment honors group-aligned set_clock voltage
# ============================================================

class TestVddAlignmentWithClockAlignment:
    def test_peer_in_other_vdd_gets_level_voltage(self):
        # A and B share DVFS group 'G' but live in different VDD domains.
        # A's manual clock forces the group to the 1000 MHz / 800 mV level;
        # B's own requirement is tiny (100 MHz / 600 mV level).
        db = HWInfoDB(
            project_name="t",
            ip_infos={
                "A": [IPInfo("A", "Normal", 1.0, 0.0, 1.0, "V1", "G")],
                "B": [IPInfo("B", "Normal", 1.0, 0.0, 1.0, "V2", "G")],
            },
            dvfs_tables={
                "G": DVFSTable(name="G", levels=[
                    DVFSLevel(level=0, speed=1000.0, voltages={4: 800.0}),
                    DVFSLevel(level=1, speed=500.0, voltages={4: 700.0}),
                    DVFSLevel(level=2, speed=100.0, voltages={4: 600.0}),
                ]),
            },
        )
        sc = ScenarioGraph("vdd_align")
        sc.add_task("tA", "A", width=100, height=100)
        sc.add_task("tB", "B", width=100, height=100)
        sc._manual_clocks = {"A": 900.0}
        hw_registry = {"A": IPNode(name="A"), "B": IPNode(name="B")}

        resolver = HWResolver(db, asv_group=4)
        resolved = resolver.resolve_scenario(hw_registry, sc, {'fps': 30.0})

        # Both aligned to the 1000 MHz level
        assert resolved["A"].set_clock == pytest.approx(1000.0)
        assert resolved["B"].set_clock == pytest.approx(1000.0)
        # B is alone in VDD 'V2', but must still get the voltage its
        # aligned clock needs (800 mV), not its own tiny requirement
        assert resolved["B"].set_voltage == pytest.approx(800.0)


# ============================================================
# M-9: DAG-based critical path
# ============================================================

class TestDagCriticalPath:
    def test_critical_path_follows_dag_not_end_time(self):
        from src.controller.simulator import SimulationResults, TaskResult

        sc = ScenarioGraph("cp")
        for tid in ("t1", "t2", "t3", "t4"):
            sc.add_task(tid, "HW_" + tid)
        sc.add_dependency("t1", "t2", conn_type="M2M")
        sc.add_dependency("t2", "t3", conn_type="M2M")
        # t4 is independent but ends near the total end time
        results = SimulationResults(scenario_name="cp", total_time=0.030)
        for tid, s, e in (("t1", 0.0, 0.010), ("t2", 0.010, 0.020),
                          ("t3", 0.020, 0.030), ("t4", 0.0, 0.0299)):
            results.add_result(TaskResult(
                task_id=tid, hw_name="HW_" + tid, start_time=s, end_time=e,
                duration=e - s, power_consumed=0.0))

        analyzer = TimingAnalyzer(scenario=sc)
        report = analyzer.analyze(results)
        assert report['critical_path'] == ["t1", "t2", "t3"]

    def test_fallback_heuristic_without_scenario(self):
        from src.controller.simulator import SimulationResults, TaskResult
        results = SimulationResults(scenario_name="cp", total_time=0.030)
        results.add_result(TaskResult(
            task_id="t1", hw_name="HW", start_time=0.0, end_time=0.030,
            duration=0.030, power_consumed=0.0))
        report = TimingAnalyzer().analyze(results)
        assert report['critical_path'] == ["t1"]


# ============================================================
# M-3: shared BW formula
# ============================================================

class TestSharedBwFormula:
    def test_basic_bw(self):
        port = {'size': [0, 0, 1920, 1080], 'format': 'NV12', 'bitwidth': 8}
        rec = calc_port_bw(port, fps=30.0)
        # 30 × 1920 × 1080 × 1 × 1.5 / 1e6 MB/s
        assert rec['bw_mbs'] == pytest.approx(30 * 1920 * 1080 * 1.5 / 1e6)
        assert rec['bw_power_mw'] == pytest.approx(rec['bw_mbs'] * 80.0 / 1000)

    def test_comp_specific_type_applies_ratio(self):
        # 'SBWC' (not just 'enable') must apply comp_ratio — the unified
        # behavior across report/chart/exploration
        port = {'size': [0, 0, 100, 100], 'format': 'NV12',
                'comp': 'SBWC', 'comp_ratio': 0.5}
        rec = calc_port_bw(port, fps=30.0)
        base = 30 * 100 * 100 * 1.5 / 1e6
        assert rec['bw_mbs'] == pytest.approx(base * 0.5)

    def test_comp_disable_ignores_ratio(self):
        port = {'size': [0, 0, 100, 100], 'format': 'NV12',
                'comp': 'disable', 'comp_ratio': 0.5}
        rec = calc_port_bw(port, fps=30.0)
        assert rec['bw_mbs'] == pytest.approx(30 * 100 * 100 * 1.5 / 1e6)

    def test_invalid_size_returns_zero(self):
        assert calc_port_bw({'size': [0, 0, 0, 0]}, 30.0)['bw_mbs'] == 0.0

    def test_is_dma_port_name(self):
        assert is_dma_port_name("L0_RDMA")
        assert is_dma_port_name("wdma1")
        assert not is_dma_port_name("CINFIFO")


# ============================================================
# L-3 / L-12: small API fixes
# ============================================================

class TestSmallApiFixes:
    def test_add_task_does_not_mutate_caller_workload(self):
        sc = ScenarioGraph("wl")
        workload = {'width': 100}
        sc.add_task("t1", "HW", workload=workload, height=200)
        assert workload == {'width': 100}, "caller's dict must not be mutated"
        assert sc.get_task("t1").workload == {'width': 100, 'height': 200}

    def test_get_by_task_frame_filter(self):
        from src.controller.simulator import SimulationResults, TaskResult
        results = SimulationResults(scenario_name="x", total_time=1.0)
        for fid in (0, 1):
            results.add_result(TaskResult(
                task_id="t1", hw_name="HW", start_time=fid * 0.1,
                end_time=fid * 0.1 + 0.05, duration=0.05,
                power_consumed=0.0, frame_id=fid))
        assert results.get_by_task("t1").frame_id == 0
        assert results.get_by_task("t1", frame_id=1).frame_id == 1
        assert results.get_by_task("t1", frame_id=9) is None

"""
Unit tests for Controller layer (simulator, analyzers).
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode, DMANode, ProcessorNode
from src.model.scenario import ScenarioGraph, ConnectionType
from src.controller.simulator import SoCSimulator, SimulationResults, TaskResult
from src.controller.performance_analyzer import PerformanceAnalyzer
from src.controller.power_analyzer import PowerAnalyzer
from src.controller.timing_analyzer import TimingAnalyzer


class TestSoCSimulator:
    """Tests for SoCSimulator class."""

    def test_m2m_timing(self):
        """
        Test M2M (sequential) timing.

        A(1 sec) -> B(2 sec) M2M connection
        Expected: B starts at 1s, ends at 3s
        """
        # Create HW nodes with fixed processing times
        # Using pixels calculation: time = pixels / (clock * ppc)
        # For 1 second: pixels = clock * ppc = 1e9 * 1 = 1e9
        hw_a = IPNode(name="HW_A", clock_freq=1e9, ppc=1, efficiency=1.0)
        hw_b = IPNode(name="HW_B", clock_freq=0.5e9, ppc=1, efficiency=1.0)  # Half speed = 2x time

        # Create scenario
        scenario = ScenarioGraph(name="M2M_Test")
        scenario.add_task("task_a", "HW_A", pixels=int(1e9))  # 1 second
        scenario.add_task("task_b", "HW_B", pixels=int(1e9))  # 2 seconds (half clock)
        scenario.add_dependency("task_a", "task_b", "M2M")

        # Run simulation
        simulator = SoCSimulator()
        simulator.register_hw(hw_a)
        simulator.register_hw(hw_b)
        simulator.load_scenario(scenario)

        results = simulator.run()

        # Verify timing
        result_a = results.get_by_task("task_a")
        result_b = results.get_by_task("task_b")

        assert result_a is not None
        assert result_b is not None

        # Task A should end at ~1 second
        assert abs(result_a.end_time - 1.0) < 0.01, f"Task A end time: {result_a.end_time}"

        # Task B should start after A ends, at ~1 second
        assert abs(result_b.start_time - 1.0) < 0.01, f"Task B start time: {result_b.start_time}"

        # Task B should end at ~3 seconds
        assert abs(result_b.end_time - 3.0) < 0.01, f"Task B end time: {result_b.end_time}"

    def test_otf_timing_bottleneck(self):
        """
        Test OTF (pipelined) timing with bottleneck.

        Fast IP (100 fps) -> Slow IP (30 fps) OTF connection
        Expected: Both run at 30 fps (bottleneck limited)

        For 1 frame: 100fps = 10ms, 30fps = 33.33ms
        With OTF, both should complete at max(10ms, 33.33ms) = 33.33ms
        """
        # Fast IP: 100 fps = 0.01 second per frame
        # clock * ppc = pixels / time => 10e6 * 1 = 10M pixels for 1 frame at 1GHz/100ppc
        hw_fast = IPNode(name="HW_Fast", clock_freq=1e9, ppc=100, efficiency=1.0)

        # Slow IP: 30 fps = 0.0333 second per frame
        hw_slow = IPNode(name="HW_Slow", clock_freq=1e9, ppc=30, efficiency=1.0)

        # 1 million pixels per frame
        pixels_per_frame = 1_000_000

        scenario = ScenarioGraph(name="OTF_Test")
        scenario.add_task("t_fast", "HW_Fast", pixels=pixels_per_frame)
        scenario.add_task("t_slow", "HW_Slow", pixels=pixels_per_frame)
        scenario.add_dependency("t_fast", "t_slow", "OTF")

        simulator = SoCSimulator()
        simulator.register_hw(hw_fast)
        simulator.register_hw(hw_slow)
        simulator.load_scenario(scenario)

        results = simulator.run()

        result_fast = results.get_by_task("t_fast")
        result_slow = results.get_by_task("t_slow")

        assert result_fast is not None
        assert result_slow is not None

        # Both should start at same time (OTF)
        assert abs(result_fast.start_time - result_slow.start_time) < 0.0001

        # Both should end at same time (synchronized to slowest)
        assert abs(result_fast.end_time - result_slow.end_time) < 0.0001

        # End time should be determined by slow IP
        # 1M pixels / (1e9 * 30) = 0.0333 seconds = 33.33 ms
        expected_time = pixels_per_frame / (1e9 * 30)
        assert abs(result_slow.end_time - expected_time) < 0.0001, \
            f"Expected end time {expected_time}, got {result_slow.end_time}"

    def test_parallel_execution(self):
        """
        Test: Two independent scenarios on different HW run in parallel.
        """
        hw_a = IPNode(name="HW_A", clock_freq=1e9, ppc=1)
        hw_b = IPNode(name="HW_B", clock_freq=1e9, ppc=1)

        # Two independent tasks
        scenario = ScenarioGraph(name="Parallel_Test")
        scenario.add_task("t_a", "HW_A", pixels=int(1e9))  # 1 second
        scenario.add_task("t_b", "HW_B", pixels=int(1e9))  # 1 second
        # No dependencies - they should run in parallel

        simulator = SoCSimulator()
        simulator.register_hw(hw_a)
        simulator.register_hw(hw_b)
        simulator.load_scenario(scenario)

        results = simulator.run()

        result_a = results.get_by_task("t_a")
        result_b = results.get_by_task("t_b")

        # Both should start at time 0
        assert result_a.start_time == 0.0
        assert result_b.start_time == 0.0

        # Both should end at 1 second (parallel)
        assert abs(result_a.end_time - 1.0) < 0.01
        assert abs(result_b.end_time - 1.0) < 0.01

        # Total time should be 1 second (not 2)
        assert abs(results.total_time - 1.0) < 0.01

    def test_resource_contention(self):
        """
        Test: Two tasks on same HW should queue (sequential on same resource).
        """
        hw = IPNode(name="HW_Shared", clock_freq=1e9, ppc=1)

        scenario = ScenarioGraph(name="Contention_Test")
        scenario.add_task("t_1", "HW_Shared", pixels=int(1e9))  # 1 second
        scenario.add_task("t_2", "HW_Shared", pixels=int(1e9))  # 1 second
        # No explicit dependency, but same HW

        simulator = SoCSimulator()
        simulator.register_hw(hw)
        simulator.load_scenario(scenario)

        results = simulator.run()

        result_1 = results.get_by_task("t_1")
        result_2 = results.get_by_task("t_2")

        # Due to resource contention, one should wait for the other
        # Total time should be ~2 seconds
        assert abs(results.total_time - 2.0) < 0.01


class TestPerformanceAnalyzer:
    """Tests for PerformanceAnalyzer."""

    def test_throughput_calculation(self):
        """Test throughput metrics calculation."""
        results = SimulationResults(
            scenario_name="Test",
            total_time=1.0,  # 1 second
            task_results=[
                TaskResult("t1", "HW1", 0.0, 0.5, 0.5, 10.0, {'pixels': 1000000}),
                TaskResult("t2", "HW2", 0.5, 1.0, 0.5, 10.0, {'pixels': 1000000}),
            ]
        )

        analyzer = PerformanceAnalyzer()
        report = analyzer.analyze(results)

        assert report['total_tasks'] == 2
        assert report['throughput']['tasks_per_sec'] == 2.0

    def test_utilization_calculation(self):
        """Test HW utilization calculation."""
        results = SimulationResults(
            scenario_name="Test",
            total_time=2.0,
            task_results=[
                TaskResult("t1", "HW1", 0.0, 1.0, 1.0, 10.0, {}),  # 50% utilization
            ]
        )

        analyzer = PerformanceAnalyzer()
        report = analyzer.analyze(results)

        assert report['utilization']['HW1'] == 0.5  # 1s active / 2s total


class TestPowerAnalyzer:
    """Tests for PowerAnalyzer."""

    def test_energy_calculation(self):
        """Test total energy calculation."""
        results = SimulationResults(
            scenario_name="Test",
            total_time=1.0,
            task_results=[
                TaskResult("t1", "HW1", 0.0, 0.5, 0.5, 10.0, {}),
                TaskResult("t2", "HW2", 0.5, 1.0, 0.5, 20.0, {}),
            ]
        )

        analyzer = PowerAnalyzer()
        report = analyzer.analyze(results)

        assert report['total_energy_mj'] == 30.0
        assert report['per_hw_energy']['HW1'] == 10.0
        assert report['per_hw_energy']['HW2'] == 20.0


class TestTimingAnalyzer:
    """Tests for TimingAnalyzer."""

    def test_latency_calculation(self):
        """Test end-to-end latency calculation."""
        results = SimulationResults(
            scenario_name="Test",
            total_time=0.01,  # 10ms
            task_results=[
                TaskResult("t1", "HW1", 0.0, 0.005, 0.005, 10.0, {}),
                TaskResult("t2", "HW2", 0.005, 0.01, 0.005, 10.0, {}),
            ]
        )

        analyzer = TimingAnalyzer()
        report = analyzer.analyze(results)

        assert report['total_latency_ms'] == 10.0
        assert len(report['task_timings']) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

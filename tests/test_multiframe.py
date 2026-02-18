"""
Tests for multi-frame pipelining simulation.

Verifies that frames overlap correctly, frame intervals are derived
from sensor FPS, and results contain proper frame_id tagging.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode, SensorNode
from src.model.scenario import ScenarioGraph
from src.controller.simulator import SoCSimulator


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sensor_30fps():
    """Sensor running at 30 FPS (frame interval = 33.33ms)."""
    return SensorNode(
        name="Sensor",
        frame_width=1920,
        frame_height=1080,
        fps=30.0,
    )


@pytest.fixture
def fast_ip():
    """Fast IP: 1M pixels in ~1ms."""
    return IPNode(name="FastIP", clock_freq=1e9, ppc=1)


@pytest.fixture
def slow_ip():
    """Slower IP: 1M pixels in 10ms."""
    return IPNode(name="SlowIP", clock_freq=100e6, ppc=1)


def _build_simple_pipeline(sensor, downstream_ip, conn_type="M2M"):
    """Helper: Sensor → IP with given connection type."""
    pixels = sensor.frame_width * sensor.frame_height
    scenario = ScenarioGraph(name="MultiFrame_Test")
    scenario.add_task("t_sensor", "Sensor", pixels=pixels, h_blank_margin=0)
    scenario.add_task("t_proc", downstream_ip.name, pixels=pixels, h_blank_margin=0)
    scenario.add_dependency("t_sensor", "t_proc", conn_type)
    return scenario


# ============================================================
# Tests
# ============================================================

class TestSingleFrameBackwardCompat:
    """Ensure num_frames=1 still works identically to the legacy path."""

    def test_single_frame_default(self, sensor_30fps, fast_ip):
        """Default run() should produce 1 frame's worth of results."""
        scenario = _build_simple_pipeline(sensor_30fps, fast_ip, "M2M")

        sim = SoCSimulator()
        sim.register_hw(sensor_30fps)
        sim.register_hw(fast_ip)
        sim.load_scenario(scenario)

        results = sim.run()  # default num_frames=1

        assert results.num_frames == 1
        # Each frame produces 2 task results (sensor + proc)
        assert len(results.task_results) == 2
        # All results should have frame_id=0
        assert all(r.frame_id == 0 for r in results.task_results)

    def test_single_frame_explicit(self, sensor_30fps, fast_ip):
        """Explicit num_frames=1 should be identical."""
        scenario = _build_simple_pipeline(sensor_30fps, fast_ip, "M2M")

        sim = SoCSimulator()
        sim.register_hw(sensor_30fps)
        sim.register_hw(fast_ip)
        sim.load_scenario(scenario)

        results = sim.run(num_frames=1)
        assert results.num_frames == 1
        assert len(results.task_results) == 2


class TestMultiFramePipelining:
    """Core multi-frame pipelining tests."""

    def test_multiframe_results_tagged(self, sensor_30fps, fast_ip):
        """Each frame's results should carry the correct frame_id."""
        scenario = _build_simple_pipeline(sensor_30fps, fast_ip, "M2M")

        sim = SoCSimulator()
        sim.register_hw(sensor_30fps)
        sim.register_hw(fast_ip)
        sim.load_scenario(scenario)

        num_frames = 3
        results = sim.run(num_frames=num_frames)

        assert results.num_frames == num_frames
        # 2 tasks × 3 frames = 6 results
        assert len(results.task_results) == 2 * num_frames

        # Check frame_id distribution
        frame_ids = {r.frame_id for r in results.task_results}
        assert frame_ids == {0, 1, 2}

        # Each frame should have exactly 2 results
        for fid in range(num_frames):
            frame_results = results.get_by_frame(fid)
            assert len(frame_results) == 2

    def test_frame_offset_spacing(self, sensor_30fps, fast_ip):
        """Frame starts should be spaced by 1/fps = 33.33ms."""
        scenario = _build_simple_pipeline(sensor_30fps, fast_ip, "M2M")

        sim = SoCSimulator()
        sim.register_hw(sensor_30fps)
        sim.register_hw(fast_ip)
        sim.load_scenario(scenario)

        results = sim.run(num_frames=3)
        frame_interval = 1.0 / 30.0  # 33.33ms

        # Get sensor start times per frame (sensor is root task)
        for fid in range(3):
            frame_results = results.get_by_frame(fid)
            sensor_r = [r for r in frame_results if r.task_id == "t_sensor"][0]
            expected_start = fid * frame_interval
            assert abs(sensor_r.start_time - expected_start) < 1e-6, \
                f"Frame {fid}: sensor started at {sensor_r.start_time}, expected {expected_start}"

    def test_pipeline_overlap_reduces_total_time(self, sensor_30fps, fast_ip):
        """Multi-frame pipelining should take less time than sequential."""
        scenario = _build_simple_pipeline(sensor_30fps, fast_ip, "M2M")

        sim = SoCSimulator()
        sim.register_hw(sensor_30fps)
        sim.register_hw(fast_ip)
        sim.load_scenario(scenario)

        # Single frame time
        results_1 = sim.run(num_frames=1)
        single_time = results_1.total_time

        # Re-run with 3 frames (must re-init simulator)
        sim2 = SoCSimulator()
        sim2.register_hw(sensor_30fps)
        sim2.register_hw(fast_ip)
        sim2.load_scenario(scenario)
        results_3 = sim2.run(num_frames=3)

        # With pipelining, 3 frames should take < 3 × single frame time
        assert results_3.total_time < 3 * single_time, \
            f"Pipelined time {results_3.total_time} should be < 3 × single {single_time}"


class TestFrameInterval:
    """Tests for _get_frame_interval()."""

    def test_frame_interval_from_sensor(self, sensor_30fps, fast_ip):
        """Frame interval should be derived from SensorNode fps."""
        scenario = _build_simple_pipeline(sensor_30fps, fast_ip, "M2M")

        sim = SoCSimulator()
        sim.register_hw(sensor_30fps)
        sim.register_hw(fast_ip)
        sim.load_scenario(scenario)

        interval = sim._get_frame_interval()
        expected = 1.0 / 30.0  # 33.33ms
        assert abs(interval - expected) < 1e-9

    def test_frame_interval_default_no_sensor(self, fast_ip, slow_ip):
        """Without SensorNode, default to 30fps."""
        scenario = ScenarioGraph(name="NoSensor")
        scenario.add_task("t_a", "FastIP", pixels=1_000_000, h_blank_margin=0)
        scenario.add_task("t_b", "SlowIP", pixels=1_000_000, h_blank_margin=0)
        scenario.add_dependency("t_a", "t_b", "M2M")

        sim = SoCSimulator()
        sim.register_hw(fast_ip)
        sim.register_hw(slow_ip)
        sim.load_scenario(scenario)

        interval = sim._get_frame_interval()
        expected = 1.0 / 30.0
        assert abs(interval - expected) < 1e-9

    def test_sensor_60fps_interval(self, fast_ip):
        """60fps sensor should give 16.67ms interval."""
        sensor = SensorNode(
            name="Sensor60",
            frame_width=1920, frame_height=1080,
            fps=60.0,
        )
        scenario = ScenarioGraph(name="60fps_Test")
        scenario.add_task("t_sensor", "Sensor60", pixels=1920*1080, h_blank_margin=0)
        scenario.add_task("t_proc", "FastIP", pixels=1920*1080, h_blank_margin=0)
        scenario.add_dependency("t_sensor", "t_proc", "M2M")

        sim = SoCSimulator()
        sim.register_hw(sensor)
        sim.register_hw(fast_ip)
        sim.load_scenario(scenario)

        interval = sim._get_frame_interval()
        expected = 1.0 / 60.0
        assert abs(interval - expected) < 1e-9


class TestMultiFrameOTF:
    """Multi-frame tests with OTF connections."""

    def test_otf_multiframe(self, sensor_30fps, fast_ip):
        """OTF pipeline with multiple frames should work correctly."""
        scenario = _build_simple_pipeline(sensor_30fps, fast_ip, "OTF")

        sim = SoCSimulator()
        sim.register_hw(sensor_30fps)
        sim.register_hw(fast_ip)
        sim.load_scenario(scenario)

        results = sim.run(num_frames=2)

        assert results.num_frames == 2
        # OTF groups produce results for both tasks per frame
        assert len(results.task_results) == 4  # 2 tasks × 2 frames

        # Both frames should have sensor + proc results
        for fid in range(2):
            frame_results = results.get_by_frame(fid)
            task_ids = {r.task_id for r in frame_results}
            assert "t_sensor" in task_ids
            assert "t_proc" in task_ids


class TestMultiFrameMixedPipeline:
    """Tests for pipelines with both OTF and M2M connections across frames."""

    def test_otf_plus_m2m_multiframe(self, sensor_30fps, isp_fe_ip, isp_be_ip):
        """Sensor→ISP_FE(OTF) + ISP_FE→ISP_BE(M2M), multi-frame."""
        pixels = 1920 * 1080
        scenario = ScenarioGraph(name="Mixed_Pipeline")
        scenario.add_task("t_sensor", "Sensor", pixels=pixels, h_blank_margin=0)
        scenario.add_task("t_isp_fe", "ISP_FE", pixels=pixels, h_blank_margin=0)
        scenario.add_task("t_isp_be", "ISP_BE", pixels=pixels, h_blank_margin=0)

        scenario.add_dependency("t_sensor", "t_isp_fe", "OTF")
        scenario.add_dependency("t_isp_fe", "t_isp_be", "M2M")

        sim = SoCSimulator()
        sim.register_hw(sensor_30fps)
        sim.register_hw(isp_fe_ip)
        sim.register_hw(isp_be_ip)
        sim.load_scenario(scenario)

        results = sim.run(num_frames=2)

        assert results.num_frames == 2
        # 3 tasks × 2 frames = 6
        assert len(results.task_results) == 6

        # M2M sequencing: ISP_BE start >= ISP_FE end within each frame
        for fid in range(2):
            frame_results = results.get_by_frame(fid)
            fe = [r for r in frame_results if r.task_id == "t_isp_fe"][0]
            be = [r for r in frame_results if r.task_id == "t_isp_be"][0]
            assert be.start_time >= fe.end_time - 1e-9, \
                f"Frame {fid}: M2M violated — ISP_BE started before ISP_FE ended"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Tests for BW chart creation and related utilities.

Tests BPP_MAP, Monitor.from_simulation_results,
and Visualizer.create_bw_chart (smoke test).
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode
from src.model.modules import DMAModule
from src.model.scenario import ScenarioGraph
from src.controller.simulator import (
    SoCSimulator, SimulationResults, TaskResult, BPP_MAP, BPP_DEFAULT,
)
from src.view.visualizer import Monitor, Visualizer


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sim_results():
    """Minimal SimulationResults for BW chart."""
    return SimulationResults(
        scenario_name="BWTest",
        total_time=0.033,
        num_frames=1,
        task_results=[
            TaskResult(
                task_id="t_isp",
                hw_name="ISP_FE",
                start_time=0.0,
                end_time=0.010,
                duration=0.010,
                power_consumed=1.5,
                frame_id=0,
                workload={'width': 1920, 'height': 1080},
            ),
        ],
    )


@pytest.fixture
def scenario_for_bw():
    """Scenario with _ip_settings for BW derivation."""
    s = ScenarioGraph(name="BWTest")
    s.add_task("t_isp", "ISP_FE", width=1920, height=1080)
    s._ip_settings = {
        "t_isp": {
            "hw": "ISP_FE",
            "inputs": [
                {"port": "RDMA0", "size": [0, 0, 1920, 1080],
                 "format": "NV12", "bitwidth": 8, "r_w_rate": 1.0,
                 "comp": "disable"},
            ],
            "outputs": [
                {"port": "WDMA0", "size": [0, 0, 1920, 1080],
                 "format": "NV12", "bitwidth": 8, "r_w_rate": 1.0,
                 "comp": "disable"},
            ],
        },
    }
    return s


@pytest.fixture
def hw_registry_with_dma():
    """HW registry with DMA modules for BW chart."""
    ip = IPNode(name="ISP_FE", clock_freq=600e6, ppc=4)
    ip.add_module(DMAModule(name="RDMA0", max_bandwidth=6.4e9, direction="read"))
    ip.add_module(DMAModule(name="WDMA0", max_bandwidth=6.4e9, direction="write"))
    return {"ISP_FE": ip}


# ============================================================
# Tests: BPP Map
# ============================================================

class TestBPPMap:
    def test_nv12_bpp(self):
        assert BPP_MAP["NV12"] == 1.5

    def test_yuv422_bpp(self):
        assert BPP_MAP["YUV422"] == 2.0

    def test_rgb_bpp(self):
        assert BPP_MAP["RGB888"] == 3.0

    def test_rgba_bpp(self):
        assert BPP_MAP["RGBA"] == 4.0

    def test_raw10_bpp(self):
        assert BPP_MAP["RAW10"] == 1.25

    def test_default_bpp(self):
        assert BPP_DEFAULT == 1.0


# ============================================================
# Tests: Monitor from SimulationResults
# ============================================================

class TestMonitorBW:
    def test_from_simulation_results(self, sim_results):
        monitor = Monitor()
        monitor.from_simulation_results(sim_results)
        df = monitor.to_dataframe()
        assert len(df) == 1
        assert df.iloc[0]['TaskID'] == "t_isp"


# ============================================================
# Tests: Visualizer BW Chart
# ============================================================

class TestBWChart:
    def test_create_bw_chart_smoke(self, sim_results, scenario_for_bw,
                                    hw_registry_with_dma):
        """BW chart creation should not crash."""
        viz = Visualizer()
        try:
            fig = viz.create_bw_chart(
                sim_results,
                scenario=scenario_for_bw,
                hw_registry=hw_registry_with_dma,
            )
            if fig is not None:
                assert hasattr(fig, 'data')
        except ImportError:
            pytest.skip("plotly not installed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

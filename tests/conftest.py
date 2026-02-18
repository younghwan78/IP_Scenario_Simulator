"""
Shared pytest fixtures for regression test suite.
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode, SensorNode, DisplayNode
from src.model.modules import ScalerModule, CropModule, DMAModule
from src.model.scenario import ScenarioGraph


# ============================================================
# HW Registry Fixtures
# ============================================================

@pytest.fixture
def sensor_4k_30fps():
    """4K 30fps sensor with vValid timing."""
    return SensorNode(
        name="Sensor",
        frame_width=3840,
        frame_height=2160,
        fps=30.0,
        v_valid_time=0.030,   # 30ms vValid
    )


@pytest.fixture
def isp_fe_ip():
    """ISP Front-End IP with scaler module."""
    ip = IPNode(
        name="ISP_FE",
        clock_freq=600e6,
        ppc=4,
        efficiency=0.95,
        power_static=15.0,
        power_dynamic=80.0,
        supports_scale=True,
    )
    ip.add_module(ScalerModule(name="Scaler0", scale_factor=(0.5, 0.5)))
    return ip


@pytest.fixture
def isp_be_ip():
    """ISP Back-End IP."""
    return IPNode(
        name="ISP_BE",
        clock_freq=600e6,
        ppc=2,
        efficiency=0.90,
        power_static=12.0,
        power_dynamic=60.0,
    )


@pytest.fixture
def codec_ip():
    """Video encoder IP with DMA modules."""
    ip = IPNode(
        name="VENC",
        clock_freq=400e6,
        ppc=1,
        efficiency=0.85,
        power_static=20.0,
        power_dynamic=100.0,
    )
    ip.add_module(DMAModule(
        name="RDMA0",
        bandwidth=6.4e9,
        direction="read",
    ))
    ip.add_module(DMAModule(
        name="WDMA0",
        bandwidth=6.4e9,
        direction="write",
    ))
    return ip


@pytest.fixture
def sample_hw_registry(sensor_4k_30fps, isp_fe_ip, isp_be_ip, codec_ip):
    """Complete HW registry with sensor, two ISPs, and codec."""
    return {
        "Sensor": sensor_4k_30fps,
        "ISP_FE": isp_fe_ip,
        "ISP_BE": isp_be_ip,
        "VENC": codec_ip,
    }


# ============================================================
# Scenario Fixtures
# ============================================================

@pytest.fixture
def simple_pipeline_scenario():
    """Simple 4-task pipeline: Sensor→ISP_FE(OTF)→ISP_BE(M2M)→VENC(M2M)."""
    pixels_4k = 3840 * 2160
    scenario = ScenarioGraph(name="4K_Recording")
    scenario.add_task("t_sensor", "Sensor", pixels=pixels_4k, h_blank_margin=0)
    scenario.add_task("t_isp_fe", "ISP_FE", pixels=pixels_4k, h_blank_margin=0)
    scenario.add_task("t_isp_be", "ISP_BE", pixels=pixels_4k, h_blank_margin=0)
    scenario.add_task("t_venc", "VENC", pixels=pixels_4k, h_blank_margin=0)

    scenario.add_dependency("t_sensor", "t_isp_fe", "OTF")
    scenario.add_dependency("t_isp_fe", "t_isp_be", "M2M")
    scenario.add_dependency("t_isp_be", "t_venc", "M2M")
    return scenario


@pytest.fixture
def m2m_only_scenario():
    """M2M-only pipeline (no OTF), two sequential tasks."""
    scenario = ScenarioGraph(name="M2M_Pipeline")
    scenario.add_task("t_a", "ISP_FE", pixels=1_000_000, h_blank_margin=0)
    scenario.add_task("t_b", "ISP_BE", pixels=1_000_000, h_blank_margin=0)
    scenario.add_dependency("t_a", "t_b", "M2M")
    return scenario

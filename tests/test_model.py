"""
Unit tests for Model layer (hw_nodes, modules, scenario).
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode, ProcessorNode
from src.model.modules import ScalerModule, CropModule, DMAModule
from src.model.scenario import ScenarioGraph


class TestIPNode:
    """Tests for IPNode class."""

    def test_processing_time_calculation(self):
        """
        Test: 600MHz, 4PPC IP processing 4K frame (8,294,400 pixels)
        Expected: ~3.456ms

        Formula: pixels / (clock * ppc * efficiency)
                = 8294400 / (600e6 * 4 * 1.0)
                = 8294400 / 2400000000
                = 0.003456 seconds = 3.456 ms
        """
        ip = IPNode(
            name="ISP_Test",
            clock_freq=600e6,  # 600 MHz
            ppc=4,
            efficiency=1.0
        )

        pixels_4k = 3840 * 2160  # 8,294,400
        processing_time = ip.get_processing_time({'pixels': pixels_4k, 'h_blank_margin': 0})

        expected_time = 0.003456  # 3.456 ms
        assert abs(processing_time - expected_time) < 1e-9, \
            f"Expected {expected_time}, got {processing_time}"

    def test_processing_time_with_efficiency(self):
        """Test processing time with efficiency < 1.0."""
        ip = IPNode(
            name="ISP_Test",
            clock_freq=600e6,
            ppc=4,
            efficiency=0.8  # 80% efficiency
        )

        pixels = 8294400
        processing_time = ip.get_processing_time({'pixels': pixels, 'h_blank_margin': 0})

        # With 80% efficiency, time should be 25% longer
        expected_time = 0.003456 / 0.8  # ~4.32 ms
        assert abs(processing_time - expected_time) < 1e-9

    def test_add_module(self):
        """Test adding modules to IP."""
        ip = IPNode(name="ISP", clock_freq=600e6, ppc=4)
        scaler = ScalerModule(name="Scaler0", scale_factor=(0.5, 0.5))

        ip.add_module(scaler)

        assert len(ip.modules) == 1
        assert ip.modules[0].name == "Scaler0"
        assert ip.modules[0].parent_ip == ip

    def test_module_inherits_clock(self):
        """Test that module inherits clock from parent IP."""
        ip = IPNode(name="ISP", clock_freq=600e6, ppc=4)
        scaler = ScalerModule(name="Scaler0", scale_factor=(0.5, 0.5))

        ip.add_module(scaler)

        assert scaler.get_clock_freq() == 600e6


class TestDMAModule:
    """Tests for DMAModule class."""

    def test_transfer_time_calculation(self):
        """Test DMA transfer time calculation with MO."""
        dma = DMAModule(
            name="DMA_Read",
            max_bandwidth=25.6e9,  # 25.6 GB/s
            multiple_outstanding=16
        )

        data_size = 100 * 1024 * 1024  # 100 MB
        transfer_time = dma.get_transfer_time(data_size)

        # 100MB / 25.6GB/s (Efficiency 1.0)
        expected = (100 * 1024 * 1024) / 25.6e9
        assert abs(transfer_time - expected) < 1e-9


class TestProcessorNode:
    """Tests for ProcessorNode class."""

    def test_processing_time(self):
        """Test processor execution time calculation."""
        cpu = ProcessorNode(
            name="CPU",
            clock_freq=2e9,  # 2 GHz
            cycles_per_op=1.0,
            num_cores=4
        )

        ops = 8e9  # 8 billion operations
        time = cpu.get_processing_time({'ops': ops})

        # 8e9 ops * 1 cycle/op / (2e9 Hz * 4 cores) = 1 second
        expected = 1.0
        assert abs(time - expected) < 1e-9


class TestScalerModule:
    """Tests for ScalerModule class."""

    def test_output_size_calculation(self):
        """
        Test: Input 1920x1080, scale 0.5x0.5
        Expected output: 960x540
        """
        scaler = ScalerModule(
            name="Scaler0",
            scale_factor=(0.5, 0.5)
        )

        input_size = (1920, 1080)
        output_size = scaler.calculate_output_size(input_size)

        assert output_size == (960, 540), f"Expected (960, 540), got {output_size}"

    def test_output_size_upscale(self):
        """Test upscaling output size."""
        scaler = ScalerModule(
            name="Scaler0",
            scale_factor=(2.0, 2.0)
        )

        input_size = (1920, 1080)
        output_size = scaler.calculate_output_size(input_size)

        assert output_size == (3840, 2160)

    def test_set_input_updates_output(self):
        """Test that set_input_size updates output_size."""
        scaler = ScalerModule(
            name="Scaler0",
            scale_factor=(0.5, 0.5)
        )

        scaler.set_input_size(1920, 1080)

        assert scaler.input_size == (1920, 1080)
        assert scaler.output_size == (960, 540)


class TestCropModule:
    """Tests for CropModule class."""

    def test_crop_output_size(self):
        """Test crop region output size."""
        crop = CropModule(
            name="Crop0",
            crop_region=(100, 100, 800, 600)  # x, y, w, h
        )

        input_size = (1920, 1080)
        output_size = crop.calculate_output_size(input_size)

        assert output_size == (800, 600)

    def test_crop_exceeds_bounds(self):
        """Test crop region exceeding input bounds."""
        crop = CropModule(
            name="Crop0",
            crop_region=(1800, 1000, 500, 200)  # Exceeds bounds
        )

        input_size = (1920, 1080)
        output_size = crop.calculate_output_size(input_size)

        # Should be clamped to valid region
        assert output_size[0] <= input_size[0] - 1800
        assert output_size[1] <= input_size[1] - 1000


class TestScenarioGraph:
    """Tests for ScenarioGraph class."""

    def test_add_tasks_and_dependencies(self):
        """Test adding tasks and dependencies."""
        scenario = ScenarioGraph(name="Test")

        scenario.add_task("t1", "HW1", pixels=1000)
        scenario.add_task("t2", "HW2", pixels=2000)
        scenario.add_dependency("t1", "t2", "M2M")

        assert len(scenario) == 2
        assert "t1" in scenario
        assert "t2" in scenario

        preds = scenario.get_predecessors("t2")
        assert "t1" in preds

    def test_otf_groups(self):
        """Test OTF group detection."""
        scenario = ScenarioGraph(name="Test")

        scenario.add_task("t1", "HW1")
        scenario.add_task("t2", "HW2")
        scenario.add_task("t3", "HW3")
        scenario.add_task("t4", "HW4")

        # t1 -> t2 OTF, t3 -> t4 M2M
        scenario.add_dependency("t1", "t2", "OTF")
        scenario.add_dependency("t3", "t4", "M2M")

        otf_groups = scenario.get_otf_groups()

        assert len(otf_groups) == 1
        assert set(otf_groups[0]) == {"t1", "t2"}

    def test_topological_order(self):
        """Test topological sorting."""
        scenario = ScenarioGraph(name="Test")

        scenario.add_task("t1", "HW1")
        scenario.add_task("t2", "HW2")
        scenario.add_task("t3", "HW3")

        scenario.add_dependency("t1", "t2", "M2M")
        scenario.add_dependency("t2", "t3", "M2M")

        order = scenario.topological_order()

        assert order.index("t1") < order.index("t2")
        assert order.index("t2") < order.index("t3")

    def test_validation(self):
        """Test scenario validation."""
        scenario = ScenarioGraph(name="Test")

        scenario.add_task("t1", "HW1")
        scenario.add_task("t2", "HW2")
        scenario.add_dependency("t1", "t2", "M2M")

        is_valid, errors = scenario.validate()
        assert is_valid
        assert len(errors) == 0

    def test_root_and_leaf_tasks(self):
        """Test finding root and leaf tasks."""
        scenario = ScenarioGraph(name="Test")

        scenario.add_task("t1", "HW1")
        scenario.add_task("t2", "HW2")
        scenario.add_task("t3", "HW3")

        scenario.add_dependency("t1", "t2", "M2M")
        scenario.add_dependency("t2", "t3", "M2M")

        roots = scenario.get_root_tasks()
        leaves = scenario.get_leaf_tasks()

        assert roots == ["t1"]
        assert leaves == ["t3"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

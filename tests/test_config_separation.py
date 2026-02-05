"""
Tests for config separation (static HW config vs dynamic scenario config).
"""

import pytest
from src.model.hw_nodes import (
    HWNode, IPNode, SensorNode
)
from src.model.modules import ScalerModule, CropModule


class TestIPNodeConstraints:
    """Test IPNode HW capability constraints."""

    def test_ipnode_max_clock(self):
        """Test max_clock field."""
        ip = IPNode(
            name="TestIP",
            clock_freq=400e6,
            max_clock=600e6
        )

        assert ip.clock_freq == 400e6
        assert ip.max_clock == 600e6

    def test_ipnode_clock_table(self):
        """Test clock_table field."""
        ip = IPNode(
            name="TestIP",
            clock_freq=600e6,
            clock_table=[600e6, 400e6, 200e6]
        )

        assert len(ip.clock_table) == 3
        assert 400e6 in ip.clock_table

    def test_ipnode_size_constraints(self):
        """Test min_size and max_size fields."""
        ip = IPNode(
            name="TestIP",
            min_size=(64, 64),
            max_size=(8192, 8192)
        )

        assert ip.min_size == (64, 64)
        assert ip.max_size == (8192, 8192)

    def test_ipnode_default_size_constraints(self):
        """Test default size constraints."""
        ip = IPNode(name="TestIP")

        assert ip.min_size == (1, 1)
        assert ip.max_size == (65535, 65535)

    def test_ipnode_supports_scale(self):
        """Test supports_scale field."""
        ip = IPNode(
            name="TestIP",
            supports_scale=True
        )

        assert ip.supports_scale is True

        ip2 = IPNode(name="TestIP2")
        assert ip2.supports_scale is False


class TestSensorNodeModes:
    """Test SensorNode supported_sensor_modes field."""

    def test_supported_sensor_modes(self):
        """Test supported_sensor_modes list."""
        sensor = SensorNode(
            name="TestSensor",
            supported_sensor_modes=["4K_30fps", "4K_60fps", "1080p_120fps"]
        )

        assert len(sensor.supported_sensor_modes) == 3
        assert "4K_30fps" in sensor.supported_sensor_modes

    def test_empty_sensor_mode_by_default(self):
        """Test sensor_mode default is empty (set by scenario)."""
        sensor = SensorNode(name="TestSensor")

        assert sensor.sensor_mode == ""


class TestScalerModuleConstraints:
    """Test ScalerModule HW constraints and set_sizes method."""

    def test_scaler_min_max_scale(self):
        """Test min_scale and max_scale fields."""
        scaler = ScalerModule(
            name="TestScaler",
            min_scale=(0.25, 0.25),
            max_scale=(4.0, 4.0)
        )

        assert scaler.min_scale == (0.25, 0.25)
        assert scaler.max_scale == (4.0, 4.0)

    def test_scaler_set_sizes_downscale(self):
        """Test set_sizes for downscaling."""
        scaler = ScalerModule(name="TestScaler")

        scaler.set_sizes(
            input_size=(3840, 2160),
            output_size=(1920, 1080)
        )

        assert scaler.input_size == (3840, 2160)
        assert scaler.output_size == (1920, 1080)
        assert scaler.scale_factor == pytest.approx((0.5, 0.5))

    def test_scaler_set_sizes_upscale(self):
        """Test set_sizes for upscaling."""
        scaler = ScalerModule(name="TestScaler")

        scaler.set_sizes(
            input_size=(1920, 1080),
            output_size=(3840, 2160)
        )

        assert scaler.scale_factor == pytest.approx((2.0, 2.0))

    def test_scaler_set_sizes_asymmetric(self):
        """Test set_sizes with asymmetric scaling."""
        scaler = ScalerModule(name="TestScaler")

        scaler.set_sizes(
            input_size=(1920, 1080),
            output_size=(1280, 720)
        )

        expected_x = 1280 / 1920
        expected_y = 720 / 1080
        assert scaler.scale_factor[0] == pytest.approx(expected_x)
        assert scaler.scale_factor[1] == pytest.approx(expected_y)

    def test_scaler_set_sizes_method_chaining(self):
        """Test set_sizes returns self for method chaining."""
        scaler = ScalerModule(name="TestScaler")

        result = scaler.set_sizes(
            input_size=(3840, 2160),
            output_size=(1920, 1080)
        )

        assert result is scaler


class TestApplyScenarioSettings:
    """Test apply_scenario_settings function."""

    def test_apply_sensor_settings(self):
        """Test applying sensor settings from scenario."""
        # Create sensor with defaults
        sensor = SensorNode(
            name="Sensor_Ext",
            frame_width=1920,
            frame_height=1080,
            fps=60.0,
            supported_sensor_modes=["4K_30fps", "1080p_60fps"]
        )

        hw_nodes = {"Sensor_Ext": sensor}

        scenario_config = {
            'sensor': {
                'hw': 'Sensor_Ext',
                'frame_width': 3840,
                'frame_height': 2160,
                'fps': 30.0,
                'sensor_mode': '4K_30fps',
                'v_valid_time': 0.0118
            }
        }

        # Import and apply
        import sys
        sys.path.insert(0, '.')
        from main import apply_scenario_settings

        apply_scenario_settings(hw_nodes, scenario_config)

        # Verify sensor was updated
        assert sensor.frame_width == 3840
        assert sensor.frame_height == 2160
        assert sensor.fps == 30.0
        assert sensor.sensor_mode == "4K_30fps"
        assert sensor.v_valid_time == 0.0118

    def test_apply_scaler_settings_with_sizes(self):
        """Test applying scaler input/output sizes from scenario."""
        scaler = ScalerModule(name="Scaler0", ppc=4)

        ip = IPNode(name="ISP_FE", clock_freq=600e6)
        ip.add_module(scaler)

        hw_nodes = {"ISP_FE": ip}

        scenario_config = {
            'module_settings': [
                {
                    'hw': 'ISP_FE',
                    'module': 'Scaler0',
                    'input_size': [3840, 2160],
                    'output_size': [1920, 1080]
                }
            ]
        }

        from main import apply_scenario_settings
        apply_scenario_settings(hw_nodes, scenario_config)

        # Verify scaler was updated
        assert scaler.input_size == (3840, 2160)
        assert scaler.output_size == (1920, 1080)
        assert scaler.scale_factor == pytest.approx((0.5, 0.5))

    def test_apply_crop_settings(self):
        """Test applying crop region from scenario."""
        crop = CropModule(name="Crop0", ppc=2)

        ip = IPNode(name="ISP_BE", clock_freq=600e6, supports_crop=True)
        ip.add_module(crop)

        hw_nodes = {"ISP_BE": ip}

        scenario_config = {
            'module_settings': [
                {
                    'hw': 'ISP_BE',
                    'module': 'Crop0',
                    'crop_region': [100, 50, 1920, 1080]
                }
            ]
        }

        from main import apply_scenario_settings
        apply_scenario_settings(hw_nodes, scenario_config)

        # Verify crop was updated
        assert crop.crop_region == (100, 50, 1920, 1080)

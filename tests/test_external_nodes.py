"""
Tests for ExternalNode hierarchy (SensorNode, DisplayNode).
"""

import pytest
from src.model.hw_nodes import (
    HWNode, ExternalNode, SensorNode, DisplayNode,
    IPNode
)
from src.model.scenario import ScenarioGraph


class TestSensorNode:
    """Test SensorNode with vValid/vBlank timing."""
    
    def test_sensor_node_creation(self):
        """Test SensorNode basic creation."""
        sensor = SensorNode(
            name="TestSensor",
            frame_width=3840,
            frame_height=2160,
            fps=30.0,
            sensor_mode="4K_30fps"
        )
        
        assert sensor.name == "TestSensor"
        assert sensor.frame_width == 3840
        assert sensor.frame_height == 2160
        assert sensor.fps == 30.0
        assert sensor.sensor_mode == "4K_30fps"
        assert sensor.is_external is True
    
    def test_frame_size_calculation(self):
        """Test frame size calculation."""
        sensor = SensorNode(
            name="TestSensor",
            frame_width=3840,
            frame_height=2160,
            fps=30.0
        )
        
        assert sensor.frame_size == 3840 * 2160  # 8,294,400 pixels
    
    def test_frame_interval_calculation(self):
        """Test frame interval calculation."""
        sensor = SensorNode(
            name="TestSensor",
            frame_width=1920,
            frame_height=1080,
            fps=60.0
        )
        
        assert sensor.frame_interval == pytest.approx(1.0 / 60.0)
    
    def test_v_valid_time_default(self):
        """Test vValid time defaults to frame_interval when not set."""
        sensor = SensorNode(
            name="TestSensor",
            fps=30.0
        )
        
        # When v_valid_time is None, effective_v_valid_time equals frame_interval
        assert sensor.v_valid_time is None
        assert sensor.effective_v_valid_time == pytest.approx(1.0 / 30.0)
        assert sensor.v_blank_time == pytest.approx(0.0)
    
    def test_v_valid_time_explicit(self):
        """Test explicit vValid time setting."""
        sensor = SensorNode(
            name="TestSensor",
            fps=30.0,
            v_valid_time=0.0118  # 11.8ms
        )
        
        # 30fps = 33.33ms frame interval
        # vValid = 11.8ms, vBlank = 33.33 - 11.8 = 21.53ms
        assert sensor.effective_v_valid_time == pytest.approx(0.0118)
        assert sensor.v_blank_time == pytest.approx(1.0/30.0 - 0.0118, rel=1e-3)
    
    def test_required_throughput_calculation(self):
        """Test required throughput calculation."""
        sensor = SensorNode(
            name="TestSensor",
            frame_width=3840,
            frame_height=2160,
            fps=30.0,
            v_valid_time=0.0118  # 11.8ms
        )
        
        # 4K frame = 8,294,400 pixels
        # Required throughput = 8,294,400 / 0.0118 = ~703 Mpps
        expected_throughput = (3840 * 2160) / 0.0118
        assert sensor.get_required_throughput() == pytest.approx(expected_throughput)
    
    def test_get_frame_timing(self):
        """Test frame timing dictionary."""
        sensor = SensorNode(
            name="TestSensor",
            frame_width=3840,
            frame_height=2160,
            fps=30.0,
            v_valid_time=0.0118
        )
        
        timing = sensor.get_frame_timing()
        
        assert 'frame_interval_ms' in timing
        assert 'v_valid_time_ms' in timing
        assert 'v_blank_time_ms' in timing
        assert 'fps' in timing
        assert 'pixels_per_frame' in timing
        assert 'required_throughput_mpps' in timing
        
        assert timing['v_valid_time_ms'] == pytest.approx(11.8)
        assert timing['fps'] == 30.0
    
    def test_sensor_node_inheritance(self):
        """Test SensorNode inherits from ExternalNode."""
        sensor = SensorNode(name="TestSensor")
        
        assert isinstance(sensor, ExternalNode)
        assert isinstance(sensor, HWNode)


class TestDisplayNode:
    """Test DisplayNode with display timing parameters."""
    
    def test_display_node_creation(self):
        """Test DisplayNode basic creation."""
        display = DisplayNode(
            name="TestDisplay",
            frame_width=1920,
            frame_height=1080,
            fps=60.0,
            display_mode="FHD_60Hz"
        )
        
        assert display.name == "TestDisplay"
        assert display.frame_width == 1920
        assert display.frame_height == 1080
        assert display.fps == 60.0
        assert display.display_mode == "FHD_60Hz"
        assert display.is_external is True
    
    def test_pixel_clock_without_blanking(self):
        """Test pixel clock calculation without blanking."""
        display = DisplayNode(
            name="TestDisplay",
            frame_width=1920,
            frame_height=1080,
            fps=60.0
        )
        
        # Without h_total/v_total, uses active resolution
        expected_pclk = 1920 * 1080 * 60.0
        assert display.pixel_clock == pytest.approx(expected_pclk)
    
    def test_pixel_clock_with_blanking(self):
        """Test pixel clock calculation with blanking."""
        display = DisplayNode(
            name="TestDisplay",
            frame_width=1920,
            frame_height=1080,
            fps=60.0,
            h_total=2200,
            v_total=1125
        )
        
        # With blanking included
        expected_pclk = 2200 * 1125 * 60.0  # ~148.5 MHz
        assert display.pixel_clock == pytest.approx(expected_pclk)
    
    def test_blanking_calculations(self):
        """Test horizontal/vertical blanking calculations."""
        display = DisplayNode(
            name="TestDisplay",
            frame_width=1920,
            frame_height=1080,
            h_total=2200,
            v_total=1125
        )
        
        assert display.h_blank == 2200 - 1920  # 280 pixels
        assert display.v_blank == 1125 - 1080  # 45 lines
    
    def test_blanking_default_zero(self):
        """Test blanking defaults to 0 when totals not set."""
        display = DisplayNode(
            name="TestDisplay",
            frame_width=1920,
            frame_height=1080
        )
        
        assert display.h_blank == 0
        assert display.v_blank == 0
    
    def test_display_node_inheritance(self):
        """Test DisplayNode inherits from ExternalNode."""
        display = DisplayNode(name="TestDisplay")
        
        assert isinstance(display, ExternalNode)
        assert isinstance(display, HWNode)


class TestOTFTimingValidation:
    """Test OTF timing validation functionality."""
    
    def test_validate_otf_timing_pass(self):
        """Test OTF timing validation passes for capable IPs."""
        # Create sensor with vValid constraint
        sensor = SensorNode(
            name="Sensor_Ext",
            frame_width=3840,
            frame_height=2160,
            fps=30.0,
            v_valid_time=0.0118  # 11.8ms -> ~703 Mpps required
        )
        
        # Create IP that can meet the requirement
        # 600MHz * 4 PPC * 0.95 efficiency = 2280 Mpps
        isp_fe = IPNode(
            name="ISP_FE",
            clock_freq=600e6,
            ppc=4,
            efficiency=0.95
        )
        
        hw_nodes = {
            "Sensor_Ext": sensor,
            "ISP_FE": isp_fe
        }
        
        # Create scenario with OTF connection
        scenario = ScenarioGraph(name="Test")
        scenario.add_task("t_sensor", "Sensor_Ext", pixels=3840*2160)
        scenario.add_task("t_isp", "ISP_FE", pixels=3840*2160)
        scenario.add_dependency("t_sensor", "t_isp", "OTF")
        
        # Validate
        is_valid, messages = scenario.validate_otf_timing(hw_nodes)
        
        assert is_valid is True
        assert any("[OK]" in msg for msg in messages)
    
    def test_validate_otf_timing_fail(self):
        """Test OTF timing validation fails for incapable IPs."""
        # Create sensor with very tight vValid constraint
        sensor = SensorNode(
            name="Sensor_Ext",
            frame_width=3840,
            frame_height=2160,
            fps=30.0,
            v_valid_time=0.001  # 1ms -> ~8294 Mpps required (extreme)
        )
        
        # Create IP that cannot meet the requirement
        # 100MHz * 1 PPC * 1.0 efficiency = 100 Mpps
        slow_ip = IPNode(
            name="SlowIP",
            clock_freq=100e6,
            ppc=1,
            efficiency=1.0
        )
        
        hw_nodes = {
            "Sensor_Ext": sensor,
            "SlowIP": slow_ip
        }
        
        # Create scenario with OTF connection
        scenario = ScenarioGraph(name="Test")
        scenario.add_task("t_sensor", "Sensor_Ext", pixels=3840*2160)
        scenario.add_task("t_slow", "SlowIP", pixels=3840*2160)
        scenario.add_dependency("t_sensor", "t_slow", "OTF")
        
        # Validate
        is_valid, messages = scenario.validate_otf_timing(hw_nodes)
        
        assert is_valid is False
        assert any("[FAIL]" in msg for msg in messages)
    
    def test_validate_otf_timing_no_sensor(self):
        """Test validation with no sensor in OTF group."""
        ip1 = IPNode(name="IP1", clock_freq=600e6, ppc=4)
        ip2 = IPNode(name="IP2", clock_freq=600e6, ppc=2)
        
        hw_nodes = {"IP1": ip1, "IP2": ip2}
        
        scenario = ScenarioGraph(name="Test")
        scenario.add_task("t1", "IP1", pixels=1000000)
        scenario.add_task("t2", "IP2", pixels=1000000)
        scenario.add_dependency("t1", "t2", "OTF")
        
        # Should not fail - no sensor means no vValid constraint
        is_valid, messages = scenario.validate_otf_timing(hw_nodes)
        
        assert is_valid is True

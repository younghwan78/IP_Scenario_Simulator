"""
Tests for OTF clock optimization logic.
"""

from src.model.hw_nodes import IPNode, SensorNode
from src.model.scenario import ScenarioGraph

class TestOTFClockOptimization:
    """Test automatic clock optimization for OTF paths."""

    def test_calculate_required_freq(self):
        """Test calculation of required specific frequency."""
        scenario = ScenarioGraph("Test")

        # IP: PPC=4, Eff=1.0
        # Required Throughput: 400 Mpps
        # Expected Freq: 100 MHz
        ip = IPNode(name="TestIP", ppc=4, efficiency=1.0)
        req_throughput = 400e6

        req_freq = scenario._calculate_required_freq(req_throughput, ip)
        assert req_freq == 100e6

    def test_optimize_clocks_with_table(self):
        """Test finding optimal clock from table."""
        scenario = ScenarioGraph("Test")

        # Sensor: 3000x2000 (6MP), vValid=20ms (0.02s)
        # Throughput = 6MP / 0.02s = 300 Mpps
        sensor = SensorNode(name="Sensor", frame_width=3000, frame_height=2000,
                            v_valid_time=0.02)

        # IP: PPC=1, Eff=1.0, ClockTable=[200, 300, 400] MHz
        # Required Freq = 300 Mpps / 1 = 300 MHz
        ip = IPNode(name="IP", ppc=1, efficiency=1.0,
                    clock_table=[200e6, 300e6, 400e6],
                    max_clock=400e6)

        scenario.add_task("t_sensor", "Sensor", pixels=6e6)
        scenario.add_task("t_ip", "IP", pixels=6e6)
        scenario.add_dependency("t_sensor", "t_ip", "OTF")

        hw_nodes = {"Sensor": sensor, "IP": ip}

        scenario.optimize_otf_clocks(hw_nodes)

        assert ip.required_freq == 300e6
        assert ip.target_freq == 300e6
        assert ip.clock_freq == 300e6

    def test_optimize_clocks_fallback_max(self):
        """Test fallback to max clock when required > max."""
        scenario = ScenarioGraph("Test")

        # Sensor: 100 Mpps required
        sensor = SensorNode(name="Sensor", frame_width=1000, frame_height=1000,
                            v_valid_time=0.01) # 1MP / 0.01s = 100 Mpps

        # IP: PPC=1, MaxClock=50MHz
        # Required = 100 MHz > Max 50 MHz
        ip = IPNode(name="IP", ppc=1, efficiency=1.0, max_clock=50e6)

        scenario.add_task("t_sensor", "Sensor", pixels=1e6)
        scenario.add_task("t_ip", "IP", pixels=1e6)
        scenario.add_dependency("t_sensor", "t_ip", "OTF")

        hw_nodes = {"Sensor": sensor, "IP": ip}
        messages = scenario.optimize_otf_clocks(hw_nodes)

        assert ip.required_freq == 100e6
        # Start of selection
        assert ip.target_freq == 50e6  # Clamped to max
        assert any("WARN" in m for m in messages)

    def test_optimize_clocks_manual_override(self):
        """Test that manual clock setting is respected."""
        scenario = ScenarioGraph("Test")

        sensor = SensorNode(name="Sensor", frame_width=1000, frame_height=1000,
                            v_valid_time=1.0) # Low requirement

        # IP manually set to 500MHz via apply_scenario_settings (simulated here)
        ip = IPNode(name="IP", ppc=1, efficiency=1.0, clock_freq=100e6)
        ip.target_freq = 500e6 # Manually set flag

        scenario.add_task("t_sensor", "Sensor", pixels=1e6)
        scenario.add_task("t_ip", "IP", pixels=1e6)
        scenario.add_dependency("t_sensor", "t_ip", "OTF")

        hw_nodes = {"Sensor": sensor, "IP": ip}
        messages = scenario.optimize_otf_clocks(hw_nodes)

        # Should stay at 500MHz even though required is low
        assert ip.target_freq == 500e6
        assert ip.clock_freq == 500e6
        assert any("Using Manual Clock" in m for m in messages)

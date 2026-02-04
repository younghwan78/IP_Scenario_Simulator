"""
Integration tests for complete scenario simulation.
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode, DMANode
from src.model.modules import ScalerModule, CropModule
from src.model.scenario import ScenarioGraph
from src.controller.simulator import SoCSimulator
from src.controller.performance_analyzer import PerformanceAnalyzer
from src.controller.power_analyzer import PowerAnalyzer
from src.controller.timing_analyzer import TimingAnalyzer
from src.view.text_view import TextViewer
from src.view.visualizer import Monitor


class TestIntegration4KRecording:
    """Integration tests for 4K recording scenario."""
    
    def setup_method(self):
        """Set up test fixtures."""
        # Create hardware nodes
        self.hw_nodes = [
            IPNode(
                name="Sensor_Ext",
                clock_freq=600e6,
                ppc=4,
                efficiency=1.0,
                power_static=10.0,
                power_dynamic=50.0
            ),
            IPNode(
                name="ISP_FE",
                clock_freq=600e6,
                ppc=4,
                efficiency=0.95,
                power_static=15.0,
                power_dynamic=80.0
            ),
            IPNode(
                name="ISP_BE",
                clock_freq=600e6,
                ppc=2,
                efficiency=0.90,
                power_static=12.0,
                power_dynamic=60.0
            ),
            IPNode(
                name="VENC",
                clock_freq=400e6,
                ppc=1,
                efficiency=0.85,
                power_static=20.0,
                power_dynamic=100.0
            ),
        ]
        
        # Add modules
        self.hw_nodes[1].add_module(ScalerModule(name="Scaler0", scale_factor=(0.5, 0.5)))
        
        # Create scenario
        self.pixels_4k = 3840 * 2160  # 8,294,400
        
        self.scenario = ScenarioGraph(name="4K_Recording")
        self.scenario.add_task("t_sensor", "Sensor_Ext", pixels=self.pixels_4k)
        self.scenario.add_task("t_isp_fe", "ISP_FE", pixels=self.pixels_4k)
        self.scenario.add_task("t_isp_be", "ISP_BE", pixels=self.pixels_4k)
        self.scenario.add_task("t_venc", "VENC", pixels=self.pixels_4k)
        
        self.scenario.add_dependency("t_sensor", "t_isp_fe", "OTF")
        self.scenario.add_dependency("t_isp_fe", "t_isp_be", "M2M")
        self.scenario.add_dependency("t_isp_be", "t_venc", "M2M")
    
    def test_4k_recording_pipeline(self):
        """Test complete 4K recording pipeline simulation."""
        simulator = SoCSimulator()
        for hw in self.hw_nodes:
            simulator.register_hw(hw)
        simulator.load_scenario(self.scenario)
        
        results = simulator.run()
        
        # Verify all tasks completed
        assert len(results.task_results) == 4
        
        # Verify tasks by name
        task_names = [r.task_id for r in results.task_results]
        assert "t_sensor" in task_names
        assert "t_isp_fe" in task_names
        assert "t_isp_be" in task_names
        assert "t_venc" in task_names
    
    def test_isp_fe_processing_time(self):
        """
        Test ISP_FE processing time for 4K.
        600MHz, 4PPC, 95% efficiency, 8.3M pixels
        Expected: 8294400 / (600e6 * 4 * 0.95) ≈ 3.638ms
        """
        ip = self.hw_nodes[1]  # ISP_FE
        time = ip.get_processing_time({'pixels': self.pixels_4k})
        
        expected = self.pixels_4k / (600e6 * 4 * 0.95)
        assert abs(time - expected) < 1e-9
        
        # Should be around 3.64ms
        assert abs(time * 1000 - 3.638) < 0.01
    
    def test_otf_synchronization(self):
        """Test that OTF-connected tasks are synchronized."""
        simulator = SoCSimulator()
        for hw in self.hw_nodes:
            simulator.register_hw(hw)
        simulator.load_scenario(self.scenario)
        
        results = simulator.run()
        
        sensor_result = results.get_by_task("t_sensor")
        isp_fe_result = results.get_by_task("t_isp_fe")
        
        # OTF: should start at same time
        assert sensor_result.start_time == isp_fe_result.start_time
        
        # OTF: should end at same time
        assert sensor_result.end_time == isp_fe_result.end_time
    
    def test_m2m_sequencing(self):
        """Test that M2M-connected tasks are sequential."""
        simulator = SoCSimulator()
        for hw in self.hw_nodes:
            simulator.register_hw(hw)
        simulator.load_scenario(self.scenario)
        
        results = simulator.run()
        
        isp_fe_result = results.get_by_task("t_isp_fe")
        isp_be_result = results.get_by_task("t_isp_be")
        venc_result = results.get_by_task("t_venc")
        
        # ISP_BE should start after ISP_FE ends (M2M)
        assert isp_be_result.start_time >= isp_fe_result.end_time - 0.0001
        
        # VENC should start after ISP_BE ends (M2M)
        assert venc_result.start_time >= isp_be_result.end_time - 0.0001
    
    def test_analyzers_integration(self):
        """Test all analyzers with complete simulation."""
        simulator = SoCSimulator()
        for hw in self.hw_nodes:
            simulator.register_hw(hw)
        simulator.load_scenario(self.scenario)
        
        perf_analyzer = PerformanceAnalyzer()
        power_analyzer = PowerAnalyzer()
        timing_analyzer = TimingAnalyzer()
        
        simulator.add_analyzer(perf_analyzer)
        simulator.add_analyzer(power_analyzer)
        simulator.add_analyzer(timing_analyzer)
        
        output = simulator.run_with_analysis()
        
        # Verify all analyses present
        assert 'PerformanceAnalyzer' in output['analysis']
        assert 'PowerAnalyzer' in output['analysis']
        assert 'TimingAnalyzer' in output['analysis']
        
        # Verify performance metrics
        perf_report = output['analysis']['PerformanceAnalyzer']
        assert perf_report['total_tasks'] == 4
        assert 'utilization' in perf_report
        
        # Verify power metrics
        power_report = output['analysis']['PowerAnalyzer']
        assert power_report['total_energy_mj'] >= 0
        
        # Verify timing metrics
        timing_report = output['analysis']['TimingAnalyzer']
        assert len(timing_report['task_timings']) == 4
    
    def test_text_view_output(self):
        """Test text view generates complete output."""
        simulator = SoCSimulator()
        for hw in self.hw_nodes:
            simulator.register_hw(hw)
        simulator.load_scenario(self.scenario)
        
        results = simulator.run()
        
        viewer = TextViewer()
        hw_output = viewer.print_hw_hierarchy(simulator.hw_registry)
        scenario_output = viewer.print_scenario_graph(self.scenario)
        results_output = viewer.print_simulation_summary(results)
        
        # Verify outputs contain expected content
        assert "ISP_FE" in hw_output
        assert "Scaler0" in hw_output
        assert "4K_Recording" in scenario_output
        assert "OTF" in scenario_output
        assert "M2M" in scenario_output
        assert "t_sensor" in results_output
        assert "t_venc" in results_output
    
    def test_monitor_export(self, tmp_path):
        """Test monitor records and exports results."""
        simulator = SoCSimulator()
        for hw in self.hw_nodes:
            simulator.register_hw(hw)
        simulator.load_scenario(self.scenario)
        
        results = simulator.run()
        
        monitor = Monitor()
        monitor.from_simulation_results(results)
        
        # Verify records
        df = monitor.to_dataframe()
        assert len(df) == 4
        
        # Export to CSV
        csv_path = tmp_path / "4k_recording_results.csv"
        monitor.export_csv(str(csv_path))
        
        assert csv_path.exists()


class TestIntegrationOTFPipeline:
    """Integration tests specifically for OTF pipeline behavior."""
    
    def test_fps_bottleneck(self):
        """
        Test: 100fps IP + 30fps IP with OTF = 30fps pipeline
        
        This verifies the OTF throughput limiting behavior.
        """
        # 100 fps = 10ms per frame, 30 fps = 33.33ms per frame
        # Using 1M pixels as reference
        pixels = 1_000_000
        
        # HW designed for 100 fps: process 1M pixels in 10ms
        # clock * ppc = 1M / 0.01 = 100M pixels/sec
        hw_100fps = IPNode(name="HW_100fps", clock_freq=100e6, ppc=1)
        
        # HW designed for 30 fps: process 1M pixels in 33.33ms
        # clock * ppc = 1M / 0.0333 = 30M pixels/sec
        hw_30fps = IPNode(name="HW_30fps", clock_freq=30e6, ppc=1)
        
        scenario = ScenarioGraph(name="OTF_FPS_Test")
        scenario.add_task("t_fast", "HW_100fps", pixels=pixels)
        scenario.add_task("t_slow", "HW_30fps", pixels=pixels)
        scenario.add_dependency("t_fast", "t_slow", "OTF")
        
        simulator = SoCSimulator()
        simulator.register_hw(hw_100fps)
        simulator.register_hw(hw_30fps)
        simulator.load_scenario(scenario)
        
        results = simulator.run()
        
        # Pipeline should complete in time of slowest = 33.33ms
        expected_time = pixels / (30e6 * 1)  # 0.0333 sec
        assert abs(results.total_time - expected_time) < 0.0001
        
        # Calculate effective FPS
        fps = 1.0 / results.total_time
        assert abs(fps - 30.0) < 0.1, f"Expected 30 FPS, got {fps}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

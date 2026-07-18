"""
Unit tests for View layer (text_view, visualizer).
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode
from src.model.modules import ScalerModule, DMAModule
from src.model.scenario import ScenarioGraph
from src.controller.simulator import SimulationResults, TaskResult
from src.view.text_view import TextViewer
from src.view.visualizer import Monitor, Visualizer


class TestTextViewer:
    """Tests for TextViewer class."""

    def test_hw_hierarchy_output(self):
        """Test hardware hierarchy text output."""
        hw_registry = {
            "ISP_FE": IPNode(name="ISP_FE", clock_freq=600e6, ppc=4),
            "VENC": IPNode(name="VENC", clock_freq=400e6, ppc=1),
        }

        # Add module to ISP_FE
        hw_registry["ISP_FE"].add_module(
            ScalerModule(name="Scaler0", scale_factor=(0.5, 0.5))
        )
        hw_registry["ISP_FE"].add_module(
            DMAModule(name="DMA_Read", max_bandwidth=25.6e9, multiple_outstanding=16)
        )

        viewer = TextViewer()
        output = viewer.print_hw_hierarchy(hw_registry)

        # Verify output contains expected elements
        assert "[SoC Hardware Hierarchy]" in output
        assert "ISP_FE" in output
        assert "600MHz" in output or "600" in output
        assert "VENC" in output
        assert "DMA_Read" in output
        assert "MO=16" in output
        assert "Scaler0" in output

    def test_scenario_graph_output(self):
        """Test scenario graph text output."""
        scenario = ScenarioGraph(name="Test_Scenario")
        scenario.add_task("t1", "HW1", pixels=1000000)
        scenario.add_task("t2", "HW2", pixels=2000000)
        scenario.add_dependency("t1", "t2", "OTF")

        viewer = TextViewer()
        output = viewer.print_scenario_graph(scenario)

        assert "[Scenario: Test_Scenario]" in output
        assert "t1" in output
        assert "t2" in output
        assert "HW1" in output
        assert "HW2" in output
        assert "OTF" in output

    def test_simulation_summary_output(self):
        """Test simulation summary text output."""
        results = SimulationResults(
            scenario_name="Test",
            total_time=0.01,
            task_results=[
                TaskResult("t1", "HW1", 0.0, 0.005, 0.005, 10.0, {}),
                TaskResult("t2", "HW2", 0.005, 0.01, 0.005, 20.0, {}),
            ]
        )

        viewer = TextViewer()
        output = viewer.print_simulation_summary(results)

        assert "[Simulation Results: Test]" in output
        assert "Total Time:" in output
        assert "t1" in output
        assert "t2" in output
        assert "HW1" in output
        assert "HW2" in output

    def test_dma_module_formatting(self):
        """Test DMA module text formatting with MO and bandwidth."""
        ip = IPNode(name="Test_IP")
        ip.add_module(DMAModule(
            name="DMA_Write",
            max_bandwidth=25.6e9,
            multiple_outstanding=32
        ))
        
        hw_registry = {"Test_IP": ip}

        viewer = TextViewer()
        output = viewer.print_hw_hierarchy(hw_registry)

        assert "DMA_Write" in output
        assert "MO=32" in output
        assert "25.6GB/s" in output or "25.6" in output


class TestMonitor:
    """Tests for Monitor class."""

    def test_record_and_dataframe(self):
        """Test recording and DataFrame conversion."""
        monitor = Monitor()

        monitor.record("t1", "HW1", 0.0, 0.5, 10.0)
        monitor.record("t2", "HW2", 0.5, 1.0, 20.0)

        df = monitor.to_dataframe()

        assert len(df) == 2
        assert list(df.columns) == ['TaskID', 'HW', 'StartTime', 'EndTime', 'Duration', 'PowerConsumed', 'FrameID']
        assert df.iloc[0]['TaskID'] == 't1'
        assert df.iloc[1]['TaskID'] == 't2'

    def test_from_simulation_results(self):
        """Test populating monitor from simulation results."""
        results = SimulationResults(
            scenario_name="Test",
            total_time=1.0,
            task_results=[
                TaskResult("t1", "HW1", 0.0, 0.5, 0.5, 10.0, {}),
                TaskResult("t2", "HW2", 0.5, 1.0, 0.5, 20.0, {}),
            ]
        )

        monitor = Monitor()
        monitor.from_simulation_results(results)

        df = monitor.to_dataframe()
        assert len(df) == 2

    def test_export_csv(self, tmp_path):
        """Test CSV export."""
        monitor = Monitor()
        monitor.record("t1", "HW1", 0.0, 0.5, 10.0)

        csv_path = tmp_path / "test_output.csv"
        monitor.export_csv(str(csv_path))

        assert csv_path.exists()

        # Verify content
        import pandas as pd
        df = pd.read_csv(csv_path)
        assert len(df) == 1
        assert df.iloc[0]['TaskID'] == 't1'


class TestVisualizer:
    """Tests for Visualizer class."""

    def test_create_gantt_chart(self):
        """Test Gantt chart creation (if plotly available)."""
        import pandas as pd

        df = pd.DataFrame({
            'TaskID': ['t1', 't2'],
            'HW': ['HW1', 'HW2'],
            'StartTime': [0.0, 0.005],
            'EndTime': [0.005, 0.01],
            'Duration': [0.005, 0.005],
            'PowerConsumed': [10.0, 20.0]
        })

        visualizer = Visualizer()
        fig = visualizer.create_gantt_chart_ms(df, title="Test Chart")

        # If plotly is available, fig should not be None
        # If not available, this is expected to return None
        if fig is not None:
            assert hasattr(fig, 'data')
            assert len(fig.data) > 0

    def test_empty_dataframe_handling(self):
        """Test handling of empty DataFrame."""
        import pandas as pd

        df = pd.DataFrame(columns=['TaskID', 'HW', 'StartTime', 'EndTime', 'Duration', 'PowerConsumed'])

        visualizer = Visualizer()
        fig = visualizer.create_gantt_chart_ms(df)

        # Should handle empty data gracefully
        assert fig is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

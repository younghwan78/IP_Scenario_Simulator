"""
Visualizer and Monitor for simulation output.

Provides:
- Monitor: Records task execution data
- Visualizer: Generates Gantt charts and exports CSV
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import pandas as pd

try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False


@dataclass
class TaskRecord:
    """Record of a single task execution."""
    task_id: str
    hw_name: str
    start_time: float
    end_time: float
    duration: float
    power_consumed: float


class Monitor:
    """
    Monitors and records simulation execution.
    
    Collects task execution data for analysis and visualization.
    """
    
    def __init__(self):
        """Initialize monitor."""
        self.records: List[TaskRecord] = []
    
    def record(self, task_id: str, hw_name: str, 
               start_time: float, end_time: float, 
               power: float = 0.0) -> None:
        """
        Record a task execution.
        
        Args:
            task_id: Task identifier
            hw_name: Hardware name
            start_time: Start timestamp (seconds)
            end_time: End timestamp (seconds)
            power: Power consumed (mJ)
        """
        record = TaskRecord(
            task_id=task_id,
            hw_name=hw_name,
            start_time=start_time,
            end_time=end_time,
            duration=end_time - start_time,
            power_consumed=power
        )
        self.records.append(record)
    
    def clear(self) -> None:
        """Clear all records."""
        self.records = []
    
    def to_dataframe(self) -> pd.DataFrame:
        """
        Convert records to pandas DataFrame.
        
        Returns:
            DataFrame with columns: TaskID, HW, StartTime, EndTime, Duration, PowerConsumed
        """
        if not self.records:
            return pd.DataFrame(columns=[
                'TaskID', 'HW', 'StartTime', 'EndTime', 'Duration', 'PowerConsumed'
            ])
        
        data = [{
            'TaskID': r.task_id,
            'HW': r.hw_name,
            'StartTime': r.start_time,
            'EndTime': r.end_time,
            'Duration': r.duration,
            'PowerConsumed': r.power_consumed
        } for r in self.records]
        
        return pd.DataFrame(data)
    
    def from_simulation_results(self, results: 'SimulationResults') -> 'Monitor':
        """
        Populate monitor from simulation results.
        
        Args:
            results: SimulationResults object
            
        Returns:
            self for method chaining
        """
        from ..controller.simulator import SimulationResults
        
        self.clear()
        for task_result in results.task_results:
            self.record(
                task_id=task_result.task_id,
                hw_name=task_result.hw_name,
                start_time=task_result.start_time,
                end_time=task_result.end_time,
                power=task_result.power_consumed
            )
        return self
    
    def export_csv(self, path: str) -> None:
        """
        Export records to CSV file.
        
        Args:
            path: Output file path
        """
        df = self.to_dataframe()
        df.to_csv(path, index=False)


class Visualizer:
    """
    Creates visualizations from simulation data.
    
    Supports:
    - Gantt charts (via Plotly)
    - Timeline views
    """
    
    def __init__(self):
        """Initialize visualizer."""
        if not PLOTLY_AVAILABLE:
            print("Warning: Plotly not available. Visualization features limited.")
    
    def create_gantt_chart(self, df: pd.DataFrame, 
                           title: str = "Simulation Timeline") -> Optional['go.Figure']:
        """
        Create a Gantt chart from task data.
        
        Args:
            df: DataFrame with TaskID, HW, StartTime, EndTime columns
            title: Chart title
            
        Returns:
            Plotly Figure object, or None if Plotly unavailable
        """
        if not PLOTLY_AVAILABLE:
            print("Plotly not available. Cannot create Gantt chart.")
            return None
        
        if df.empty:
            print("No data to visualize.")
            return None
        
        # Convert times to datetime-like for Plotly timeline
        # Use milliseconds for better readability
        df_plot = df.copy()
        df_plot['Start'] = pd.to_datetime(df_plot['StartTime'] * 1000, unit='ms')
        df_plot['End'] = pd.to_datetime(df_plot['EndTime'] * 1000, unit='ms')
        
        # Create timeline
        fig = px.timeline(
            df_plot,
            x_start='Start',
            x_end='End',
            y='HW',
            color='TaskID',
            title=title,
            labels={'HW': 'Hardware', 'TaskID': 'Task'}
        )
        
        # Improve layout
        fig.update_yaxes(categoryorder='category ascending')
        fig.update_layout(
            xaxis_title='Time',
            yaxis_title='Hardware',
            showlegend=True,
            height=400 + len(df['HW'].unique()) * 30
        )
        
        return fig
    
    def create_gantt_chart_ms(self, df: pd.DataFrame,
                               title: str = "Simulation Timeline") -> Optional['go.Figure']:
        """
        Create a Gantt chart with time in milliseconds (numeric axis).
        
        Args:
            df: DataFrame with TaskID, HW, StartTime, EndTime columns
            title: Chart title
            
        Returns:
            Plotly Figure object
        """
        if not PLOTLY_AVAILABLE:
            print("Plotly not available.")
            return None
        
        if df.empty:
            return None
        
        fig = go.Figure()
        
        # Get unique HW names for Y-axis
        hw_names = df['HW'].unique().tolist()
        hw_indices = {hw: i for i, hw in enumerate(hw_names)}
        
        # Color palette
        colors = px.colors.qualitative.Set2
        task_colors = {}
        
        for idx, row in df.iterrows():
            task_id = row['TaskID']
            hw = row['HW']
            start = row['StartTime'] * 1000  # Convert to ms
            end = row['EndTime'] * 1000
            
            if task_id not in task_colors:
                task_colors[task_id] = colors[len(task_colors) % len(colors)]
            
            fig.add_trace(go.Bar(
                x=[end - start],
                y=[hw],
                base=start,
                orientation='h',
                name=task_id,
                marker_color=task_colors[task_id],
                text=task_id,
                textposition='inside',
                showlegend=task_id not in [t.name for t in fig.data[:-1]] if fig.data else True
            ))
        
        fig.update_layout(
            title=title,
            xaxis_title='Time (ms)',
            yaxis_title='Hardware',
            barmode='overlay',
            height=300 + len(hw_names) * 40
        )
        
        return fig
    
    def save_gantt(self, fig: 'go.Figure', path: str) -> None:
        """
        Save Gantt chart to file.
        
        Args:
            fig: Plotly Figure
            path: Output path (supports .html, .png, .pdf)
        """
        if fig is None:
            print("No figure to save.")
            return
        
        if path.endswith('.html'):
            fig.write_html(path)
        else:
            try:
                fig.write_image(path)
            except Exception as e:
                print(f"Error saving image: {e}")
                # Fallback to HTML
                html_path = path.rsplit('.', 1)[0] + '.html'
                fig.write_html(html_path)
                print(f"Saved as HTML instead: {html_path}")
    
    def show(self, fig: 'go.Figure') -> None:
        """
        Display figure in browser or notebook.
        
        Args:
            fig: Plotly Figure
        """
        if fig is not None:
            fig.show()

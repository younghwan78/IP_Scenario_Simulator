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
    frame_id: int = 0


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
               power: float = 0.0, frame_id: int = 0) -> None:
        """
        Record a task execution.

        Args:
            task_id: Task identifier
            hw_name: Hardware name
            start_time: Start timestamp (seconds)
            end_time: End timestamp (seconds)
            power: Power consumed (mJ)
            frame_id: Frame index for multi-frame simulation
        """
        record = TaskRecord(
            task_id=task_id,
            hw_name=hw_name,
            start_time=start_time,
            end_time=end_time,
            duration=end_time - start_time,
            power_consumed=power,
            frame_id=frame_id
        )
        self.records.append(record)

    def clear(self) -> None:
        """Clear all records."""
        self.records = []

    def to_dataframe(self) -> pd.DataFrame:
        """
        Convert records to pandas DataFrame.

        Returns:
            DataFrame with columns: TaskID, HW, StartTime, EndTime, Duration, PowerConsumed, FrameID
        """
        if not self.records:
            return pd.DataFrame(columns=[
                'TaskID', 'HW', 'StartTime', 'EndTime', 'Duration', 'PowerConsumed', 'FrameID'
            ])

        data = [{
            'TaskID': r.task_id,
            'HW': r.hw_name,
            'StartTime': r.start_time,
            'EndTime': r.end_time,
            'Duration': r.duration,
            'PowerConsumed': r.power_consumed,
            'FrameID': r.frame_id
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
                power=task_result.power_consumed,
                frame_id=task_result.frame_id
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

        # Improve layout - order by start time (scenario execution order)
        # Reversed so first task appears at the top
        hw_order = df_plot.sort_values('StartTime')['HW'].unique().tolist()
        fig.update_yaxes(categoryorder='array', categoryarray=hw_order[::-1])
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

        # Get unique HW names ordered by start time (scenario execution order)
        df_sorted = df.sort_values('StartTime')
        hw_names = df_sorted['HW'].unique().tolist()

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

        # Add frame interval annotations for the last task (periodicity check)
        if 'FrameID' in df.columns:
            self._add_frame_interval_annotations(fig, df, hw_names)

        # Order Y-axis by start time (first task at top)
        fig.update_layout(
            title=title,
            xaxis_title='Time (ms)',
            yaxis_title='Hardware',
            barmode='overlay',
            height=300 + len(hw_names) * 40,
            yaxis={'categoryorder': 'array', 'categoryarray': hw_names[::-1]}
        )

        return fig

    def _add_frame_interval_annotations(self, fig: 'go.Figure', df: pd.DataFrame, hw_names: list) -> None:
        """
        Add annotations showing frame-to-frame intervals for the last task.
        
        This helps verify periodicity in multi-frame simulations.
        """
        num_frames = df['FrameID'].nunique()
        if num_frames < 2:
            return

        # Find the last task in execution order (latest end time in frame 0)
        frame0_df = df[df['FrameID'] == 0]
        if frame0_df.empty:
            return
        
        last_task_row = frame0_df.loc[frame0_df['EndTime'].idxmax()]
        last_task_id = last_task_row['TaskID']
        last_task_hw = last_task_row['HW']

        # Get all instances of this task across frames
        task_df = df[df['TaskID'] == last_task_id].sort_values('FrameID')
        
        if len(task_df) < 2:
            return

        # Calculate and display intervals between consecutive frames
        end_times = task_df['EndTime'].values * 1000  # Convert to ms
        
        annotations = []
        shapes = []
        
        for i in range(len(end_times) - 1):
            interval = end_times[i + 1] - end_times[i]
            mid_x = (end_times[i] + end_times[i + 1]) / 2
            
            # Add annotation
            annotations.append(dict(
                x=mid_x,
                y=last_task_hw,
                text=f"Δ{i}→{i+1}: {interval:.1f}ms",
                showarrow=True,
                arrowhead=0,
                arrowwidth=1,
                arrowcolor='rgba(100,100,100,0.6)',
                ax=0,
                ay=-30 - (i * 15),  # Stagger annotations vertically
                font=dict(size=10, color='darkblue'),
                bgcolor='rgba(255,255,255,0.8)',
                bordercolor='rgba(100,100,100,0.5)',
                borderwidth=1,
                borderpad=2
            ))
            
            # Add connecting line between frames
            shapes.append(dict(
                type='line',
                x0=end_times[i],
                x1=end_times[i + 1],
                y0=last_task_hw,
                y1=last_task_hw,
                line=dict(color='rgba(0,100,200,0.4)', width=2, dash='dot'),
                yref='y',
                xref='x'
            ))

        fig.update_layout(annotations=annotations, shapes=shapes)

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

    def export_perfetto_json(self, results: 'SimulationResults', path: str) -> None:
        """
        Export simulation results to Perfetto JSON format.
        
        Args:
            results: SimulationResults object
            path: Output file path (.json)
        """
        import json
        
        trace_events = []
        
        # Map unique HW names to Thread IDs
        hw_names = sorted(list(set(r.hw_name for r in results.task_results)))
        hw_to_tid = {name: i+1 for i, name in enumerate(hw_names)}
        
        # Metadata: Set Thread Names
        for name, tid in hw_to_tid.items():
            trace_events.append({
                "name": "thread_name",
                "ph": "M",
                "pid": 1,
                "tid": tid,
                "args": {"name": name}
            })
            
        # Task Events
        for r in results.task_results:
            trace_events.append({
                "name": r.task_id,
                "cat": "task",
                "ph": "X", # Complete event
                "ts": int(r.start_time * 1e6), # us
                "dur": int(r.duration * 1e6),  # us
                "pid": 1,
                "tid": hw_to_tid[r.hw_name],
                "args": {
                    "hw": r.hw_name,
                    "power": r.power_consumed
                }
            })
            
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({"traceEvents": trace_events}, f, indent=2)
        print(f"Exported Perfetto trace to {path}")

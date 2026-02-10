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
    from plotly.subplots import make_subplots
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

    def create_bw_chart(self, results: 'SimulationResults',
                        scenario: 'ScenarioGraph',
                        title: str = "Bandwidth Timeline") -> Optional['go.Figure']:
        """
        Create a bandwidth timeline chart from simulation results.

        Shows read/write bandwidth over time:
        - Top row: Total Read BW + Total Write BW
        - Subsequent rows: Per-IP Read/Write BW

        Args:
            results: SimulationResults containing DMA task results
            scenario: ScenarioGraph for mapping DMA tasks to parent IPs
            title: Chart title

        Returns:
            Plotly Figure object, or None if Plotly unavailable
        """
        if not PLOTLY_AVAILABLE:
            print("Plotly not available. Cannot create BW chart.")
            return None

        # 1. Extract DMA transfers and compute BW
        dma_records = []
        for r in results.task_results:
            if not r.task_id.startswith('dma_'):
                continue
            size = r.workload.get('size', 0)
            if r.duration <= 0 or size <= 0:
                continue

            bw_gbps = (size / r.duration) / 1e9  # GB/s

            # Determine direction from hw_name: e.g. "WDMA_FE(Write)" or "RDMA_BE(Read)"
            if '(Write)' in r.hw_name:
                direction = 'Write'
            elif '(Read)' in r.hw_name:
                direction = 'Read'
            else:
                direction = 'Unknown'

            # Find parent IP: task_id = "dma_{owner_task_id}_{hw_name}"
            # Parse owner_task_id from task_id
            # Format: dma_{owner_task_id}_{DMAName}(Direction)
            parts = r.task_id.split('_', 1)  # ['dma', '{owner_task_id}_{DMAName}(Dir)']
            if len(parts) > 1:
                remainder = parts[1]
                # Try to find the owner task by matching known task IDs
                parent_ip = 'Unknown'
                for task in scenario.get_tasks():
                    if remainder.startswith(task.task_id + '_'):
                        parent_ip = task.mapped_hw
                        break
            else:
                parent_ip = 'Unknown'

            dma_records.append({
                'start': r.start_time,
                'end': r.end_time,
                'bw_gbps': bw_gbps,
                'direction': direction,
                'parent_ip': parent_ip,
                'dma_name': r.hw_name,
                'frame_id': r.frame_id,
            })

        if not dma_records:
            # Fallback: derive BW from M2M edge port sizes and task timing
            dma_records = self._derive_bw_from_m2m(results, scenario)

        if not dma_records:
            print("No DMA transfer data found for BW chart.")
            return None

        # 2. Identify unique IPs that have DMA transfers (ordered by first appearance)
        seen_ips = []
        for rec in sorted(dma_records, key=lambda x: x['start']):
            if rec['parent_ip'] not in seen_ips:
                seen_ips.append(rec['parent_ip'])

        # 3. Create subplots: 1 for total + 1 per IP
        num_rows = 1 + len(seen_ips)
        subplot_titles = ["Total BW"] + [f"{ip} BW" for ip in seen_ips]
        fig = make_subplots(
            rows=num_rows, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            subplot_titles=subplot_titles
        )

        # Color scheme
        read_color = 'rgba(55, 126, 184, 0.7)'   # Blue for Read
        write_color = 'rgba(228, 26, 28, 0.7)'    # Red for Write
        read_line = 'rgba(55, 126, 184, 1.0)'
        write_line = 'rgba(228, 26, 28, 1.0)'

        def _add_bw_traces(fig, records, row, show_legend=False):
            """Add read/write BW bar traces for a set of DMA records."""
            read_recs = [r for r in records if r['direction'] == 'Read']
            write_recs = [r for r in records if r['direction'] == 'Write']

            # Add rectangular shapes for each DMA transfer
            for rec in read_recs:
                start_ms = rec['start'] * 1000
                end_ms = rec['end'] * 1000
                fig.add_trace(go.Bar(
                    x=[(start_ms + end_ms) / 2],
                    y=[rec['bw_gbps']],
                    width=[end_ms - start_ms],
                    name='Read BW',
                    marker_color=read_color,
                    marker_line=dict(color=read_line, width=1),
                    showlegend=show_legend and rec == read_recs[0],
                    legendgroup='read',
                    hovertemplate=(
                        f"{rec['dma_name']}<br>"
                        f"BW: {rec['bw_gbps']:.2f} GB/s<br>"
                        f"Time: {start_ms:.3f} ~ {end_ms:.3f} ms<br>"
                        f"Frame: {rec['frame_id']}"
                        "<extra></extra>"
                    ),
                ), row=row, col=1)

            for rec in write_recs:
                start_ms = rec['start'] * 1000
                end_ms = rec['end'] * 1000
                fig.add_trace(go.Bar(
                    x=[(start_ms + end_ms) / 2],
                    y=[rec['bw_gbps']],
                    width=[end_ms - start_ms],
                    name='Write BW',
                    marker_color=write_color,
                    marker_line=dict(color=write_line, width=1),
                    showlegend=show_legend and rec == write_recs[0],
                    legendgroup='write',
                    hovertemplate=(
                        f"{rec['dma_name']}<br>"
                        f"BW: {rec['bw_gbps']:.2f} GB/s<br>"
                        f"Time: {start_ms:.3f} ~ {end_ms:.3f} ms<br>"
                        f"Frame: {rec['frame_id']}"
                        "<extra></extra>"
                    ),
                ), row=row, col=1)

        # 4. Add total BW traces (row 1)
        _add_bw_traces(fig, dma_records, row=1, show_legend=True)

        # 5. Add per-IP BW traces
        for i, ip_name in enumerate(seen_ips):
            ip_records = [r for r in dma_records if r['parent_ip'] == ip_name]
            _add_bw_traces(fig, ip_records, row=2 + i, show_legend=False)

        # 6. Layout
        fig.update_layout(
            title=title,
            barmode='stack',
            height=200 + num_rows * 150,
            showlegend=True,
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        )

        # Set y-axis labels
        fig.update_yaxes(title_text='GB/s', row=1, col=1)
        for i in range(len(seen_ips)):
            fig.update_yaxes(title_text='GB/s', row=2 + i, col=1)

        # Set x-axis label on bottom subplot only
        fig.update_xaxes(title_text='Time (ms)', row=num_rows, col=1)

        return fig

    def _derive_bw_from_m2m(self, results, scenario) -> list:
        """Derive BW records from M2M edges using port sizes and task timing.
        
        For each M2M edge, compute data size from port info (width × height × bitwidth/8)
        and duration from the source task's processing time.
        """
        from ..model.scenario import ConnectionType
        
        # Build lookup: task_id → TaskResult (use frame 0)
        task_map = {}
        for r in results.task_results:
            if r.frame_id == 0:
                task_map[r.task_id] = r
        
        ip_settings = getattr(scenario, '_ip_settings', {})
        records = []
        
        for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
            conn_type = edge_data.get('conn_type', ConnectionType.M2M)
            if conn_type != ConnectionType.M2M:
                continue
            
            port_pairs = edge_data.get('port_pairs', [])
            src_result = task_map.get(src_id)
            dst_result = task_map.get(dst_id)
            
            if not src_result or not dst_result:
                continue
            
            # Get port info from ip_settings
            src_settings = ip_settings.get(src_id, {})
            dst_settings = ip_settings.get(dst_id, {})
            src_outputs = {o.get('port', ''): o for o in src_settings.get('outputs', [])}
            dst_inputs = {i.get('port', ''): i for i in dst_settings.get('inputs', [])}
            
            for sp, dp in (port_pairs if port_pairs and port_pairs[0][0] != 'output' 
                           else [('output', 'input')]):
                # Find port info to compute data size
                port_info = src_outputs.get(sp) or dst_inputs.get(dp) or {}
                sz = port_info.get('size', [])
                bitwidth = port_info.get('bitwidth', 8)
                comp_ratio = port_info.get('comp_ratio', 1.0)
                if port_info.get('comp') != 'enable':
                    comp_ratio = 1.0
                
                if len(sz) >= 4 and sz[2] > 0 and sz[3] > 0:
                    # Data size in bytes
                    data_size = sz[2] * sz[3] * (bitwidth / 8) * comp_ratio
                else:
                    continue
                
                # Write: src task writes at the end of its processing
                w_duration = src_result.duration
                if w_duration > 0:
                    w_bw = (data_size / w_duration) / 1e9  # GB/s
                    port_label = sp if sp != 'output' else f"{src_result.hw_name}_WDMA"
                    records.append({
                        'start': src_result.start_time,
                        'end': src_result.end_time,
                        'bw_gbps': w_bw,
                        'direction': 'Write',
                        'parent_ip': src_result.hw_name,
                        'dma_name': port_label,
                        'frame_id': 0,
                    })
                
                # Read: dst task reads at the start of its processing
                r_duration = dst_result.duration
                if r_duration > 0:
                    r_bw = (data_size / r_duration) / 1e9  # GB/s
                    port_label = dp if dp != 'input' else f"{dst_result.hw_name}_RDMA"
                    records.append({
                        'start': dst_result.start_time,
                        'end': dst_result.end_time,
                        'bw_gbps': r_bw,
                        'direction': 'Read',
                        'parent_ip': dst_result.hw_name,
                        'dma_name': port_label,
                        'frame_id': 0,
                    })
        
        # Add multi-frame data if available
        if records:
            num_frames = max(r.frame_id for r in results.task_results) + 1
            if num_frames > 1:
                base_records = list(records)
                for frame_id in range(1, num_frames):
                    for r in results.task_results:
                        if r.frame_id != frame_id:
                            continue
                    # Re-derive for other frames
                    for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
                        if edge_data.get('conn_type', ConnectionType.M2M) != ConnectionType.M2M:
                            continue
                        port_pairs = edge_data.get('port_pairs', [])
                        # Find frame-specific results
                        src_r = next((r for r in results.task_results 
                                      if r.task_id == src_id and r.frame_id == frame_id), None)
                        dst_r = next((r for r in results.task_results 
                                      if r.task_id == dst_id and r.frame_id == frame_id), None)
                        if not src_r or not dst_r:
                            continue
                        src_settings = ip_settings.get(src_id, {})
                        dst_settings = ip_settings.get(dst_id, {})
                        src_outputs = {o.get('port', ''): o for o in src_settings.get('outputs', [])}
                        dst_inputs = {i.get('port', ''): i for i in dst_settings.get('inputs', [])}
                        for sp, dp in (port_pairs if port_pairs and port_pairs[0][0] != 'output' 
                                       else [('output', 'input')]):
                            port_info = src_outputs.get(sp) or dst_inputs.get(dp) or {}
                            sz = port_info.get('size', [])
                            bitwidth = port_info.get('bitwidth', 8)
                            comp_ratio = port_info.get('comp_ratio', 1.0)
                            if port_info.get('comp') != 'enable':
                                comp_ratio = 1.0
                            if len(sz) >= 4 and sz[2] > 0 and sz[3] > 0:
                                data_size = sz[2] * sz[3] * (bitwidth / 8) * comp_ratio
                            else:
                                continue
                            if src_r.duration > 0:
                                records.append({
                                    'start': src_r.start_time, 'end': src_r.end_time,
                                    'bw_gbps': (data_size / src_r.duration) / 1e9,
                                    'direction': 'Write', 'parent_ip': src_r.hw_name,
                                    'dma_name': sp if sp != 'output' else f"{src_r.hw_name}_WDMA",
                                    'frame_id': frame_id,
                                })
                            if dst_r.duration > 0:
                                records.append({
                                    'start': dst_r.start_time, 'end': dst_r.end_time,
                                    'bw_gbps': (data_size / dst_r.duration) / 1e9,
                                    'direction': 'Read', 'parent_ip': dst_r.hw_name,
                                    'dma_name': dp if dp != 'input' else f"{dst_r.hw_name}_RDMA",
                                    'frame_id': frame_id,
                                })
        
        return records

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
            fig.write_html(path, include_plotlyjs='cdn')
        else:
            try:
                fig.write_image(path)
            except Exception as e:
                print(f"Error saving image: {e}")
                # Fallback to HTML
                html_path = path.rsplit('.', 1)[0] + '.html'
                fig.write_html(html_path, include_plotlyjs='cdn')
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

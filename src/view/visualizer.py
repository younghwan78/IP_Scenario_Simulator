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
                               title: str = "Simulation Timeline",
                               hw_order: list = None) -> Optional['go.Figure']:
        """
        Create a Gantt chart with time in milliseconds (numeric axis).

        Args:
            df: DataFrame with TaskID, HW, StartTime, EndTime columns
            title: Chart title
            hw_order: Optional list of HW names in desired Y-axis order
                      (scenario definition order). If None, ordered by start time.

        Returns:
            Plotly Figure object
        """
        if not PLOTLY_AVAILABLE:
            print("Plotly not available.")
            return None

        if df.empty:
            return None

        fig = go.Figure()

        # Get unique HW names in desired order
        if hw_order:
            # Use provided scenario order, append any HW not in the list
            seen = set()
            hw_names = []
            for hw in hw_order:
                if hw in df['HW'].values and hw not in seen:
                    hw_names.append(hw)
                    seen.add(hw)
            # Append any remaining HW not in hw_order
            for hw in df['HW'].unique():
                if hw not in seen:
                    hw_names.append(hw)
        else:
            # Default: order by first start time
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
                        title: str = "Bandwidth Timeline",
                        hw_registry: dict = None) -> Optional['go.Figure']:
        """
        Create a bandwidth timeline chart from simulation results.

        Shows read/write bandwidth over time:
        - Top row: Total Read BW + Total Write BW
        - Subsequent rows: Per-IP Read/Write BW

        BW is derived from ip_settings: each DMA port's data size is computed
        from its format/bitwidth/comp/comp_ratio, and the IP's total BW is
        the sum of all its DMA ports' BW.

        Args:
            results: SimulationResults containing DMA task results
            scenario: ScenarioGraph for mapping DMA tasks to parent IPs
            title: Chart title
            hw_registry: HW registry for determining port types (DMA vs CIN/COUT)

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
            # Derive BW from ip_settings (all scenario-defined DMA ports)
            dma_records = self._derive_bw_from_ip_settings(results, scenario, hw_registry)

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
            vertical_spacing=0.03,
            subplot_titles=subplot_titles
        )

        # Color palettes: blue/green family for Read, red/yellow family for Write
        # Each DMA port gets a distinct shade within its direction family
        _READ_PALETTE = [
            'rgba(33, 113, 181, 0.75)',   # dark blue
            'rgba(35, 139, 69, 0.75)',    # forest green
            'rgba(66, 146, 198, 0.75)',   # medium blue
            'rgba(49, 163, 84, 0.75)',    # green
            'rgba(8, 81, 156, 0.75)',     # deep blue
            'rgba(0, 109, 44, 0.75)',     # dark green
            'rgba(107, 174, 214, 0.75)',  # light blue
            'rgba(116, 196, 118, 0.75)',  # light green
            'rgba(37, 52, 148, 0.75)',    # navy
            'rgba(0, 68, 27, 0.75)',      # deep green
            'rgba(43, 140, 190, 0.75)',   # cerulean
            'rgba(102, 194, 164, 0.75)',  # teal
            'rgba(49, 130, 189, 0.75)',   # steel blue
            'rgba(65, 171, 93, 0.75)',    # emerald
            'rgba(116, 169, 207, 0.75)',  # periwinkle
            'rgba(161, 217, 155, 0.75)',  # sage
        ]
        _WRITE_PALETTE = [
            'rgba(228, 26, 28, 0.75)',    # red
            'rgba(255, 191, 0, 0.75)',    # gold
            'rgba(227, 74, 51, 0.75)',    # vermillion
            'rgba(255, 127, 0, 0.75)',    # orange
            'rgba(189, 0, 38, 0.75)',     # crimson
            'rgba(253, 218, 13, 0.75)',   # yellow
            'rgba(240, 59, 32, 0.75)',    # scarlet
            'rgba(253, 141, 60, 0.75)',   # tangerine
            'rgba(179, 0, 0, 0.75)',      # dark red
            'rgba(254, 217, 118, 0.75)',  # light gold
            'rgba(215, 48, 39, 0.75)',    # flame
            'rgba(204, 76, 2, 0.75)',     # burnt orange
            'rgba(244, 109, 67, 0.75)',   # salmon
            'rgba(236, 112, 20, 0.75)',   # amber
            'rgba(252, 78, 42, 0.75)',    # coral
            'rgba(254, 178, 76, 0.75)',   # marigold
        ]

        def _get_dma_color(dma_name, direction, dma_color_map):
            """Get a distinct color for a DMA port within its Read/Write family."""
            key = (dma_name, direction)
            if key not in dma_color_map:
                palette = _READ_PALETTE if direction == 'Read' else _WRITE_PALETTE
                # Count existing entries for this direction
                count = sum(1 for k in dma_color_map if k[1] == direction)
                dma_color_map[key] = palette[count % len(palette)]
            return dma_color_map[key]

        # Track which DMA legend entries already shown
        legend_shown = set()
        dma_color_map = {}  # (dma_name, direction) → color

        def _add_bw_traces(fig, records, row, show_legend=False):
            """Add per-DMA colored BW bar traces."""
            for rec in records:
                dma_name = rec['dma_name']
                direction = rec['direction']
                color = _get_dma_color(dma_name, direction, dma_color_map)
                
                # Darken for border line
                border = color.replace('0.75)', '1.0)')
                
                start_ms = rec['start'] * 1000
                end_ms = rec['end'] * 1000
                
                # Legend: show each DMA port once globally
                legend_key = f"{dma_name}({direction})"
                show = show_legend and legend_key not in legend_shown
                if show:
                    legend_shown.add(legend_key)
                
                fig.add_trace(go.Bar(
                    x=[(start_ms + end_ms) / 2],
                    y=[rec['bw_gbps']],
                    width=[end_ms - start_ms],
                    name=f"{dma_name} ({direction[0]})",
                    marker_color=color,
                    marker_line=dict(color=border, width=1),
                    showlegend=show,
                    legendgroup=legend_key,
                    hovertemplate=(
                        f"{rec['parent_ip']} / {dma_name}<br>"
                        f"Direction: {direction}<br>"
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

        # 6. Compute unified Y-axis range from total BW max
        max_bw = max(rec['bw_gbps'] for rec in dma_records) if dma_records else 1.0
        # For stacked bars, compute max stacked BW per time window
        # Simple approach: sum all concurrent BWs at any time point
        # Use the total row's max stacked value
        total_stacked = {}
        for rec in dma_records:
            key = (round(rec['start'], 6), round(rec['end'], 6))
            total_stacked[key] = total_stacked.get(key, 0) + rec['bw_gbps']
        if total_stacked:
            max_bw = max(total_stacked.values())
        y_max = max_bw * 1.1  # 10% headroom

        # 7. Layout
        fig.update_layout(
            title=title,
            barmode='stack',
            height=200 + num_rows * 500,  # taller subplots
            showlegend=True,
            legend=dict(
                orientation='v',       # vertical legend on the right
                yanchor='top', y=1.0,
                xanchor='left', x=1.02,
                font=dict(size=9),
                bgcolor='rgba(255,255,255,0.8)',
                bordercolor='rgba(200,200,200,0.5)',
                borderwidth=1,
            ),
            margin=dict(t=60, r=200),  # room for title + right legend
        )

        # Set unified y-axis range for all subplots
        for row_idx in range(1, num_rows + 1):
            fig.update_yaxes(title_text='GB/s', range=[0, y_max], row=row_idx, col=1)

        # Set x-axis label on bottom subplot only
        fig.update_xaxes(title_text='Time (ms)', row=num_rows, col=1)

        return fig

    def _derive_bw_from_ip_settings(self, results, scenario, hw_registry=None) -> list:
        """Derive BW records from all scenario-defined DMA ports using ip_settings.
        
        For each task, inspect all input/output ports in ip_settings.
        Use hw_registry module type to determine if a port is DMA (generates BW)
        or CIN/COUT (OTF, no memory BW).
        
        Data size per DMA port:
            size[2] × size[3] × (bitwidth / 8) × comp_ratio
        BW = data_size / task_duration
        IP total BW = sum of all its DMA ports' BW
        """
        from ..model.scenario import ConnectionType
        from ..model.modules import DMAModule
        
        ip_settings = getattr(scenario, '_ip_settings', {})
        records = []
        
        # Build lookup: task_id → TaskResult per frame
        frame_results = {}  # {frame_id: {task_id: TaskResult}}
        for r in results.task_results:
            if r.task_id.startswith('dma_'):
                continue
            frame_results.setdefault(r.frame_id, {})[r.task_id] = r
        
        if not frame_results:
            return records
        
        def _is_dma_port(hw_name, port_name):
            """Check if a port is a DMA module by looking up hw_registry."""
            if hw_registry and hw_name in hw_registry:
                hw = hw_registry[hw_name]
                from ..model.hw_nodes import IPNode
                if isinstance(hw, IPNode):
                    for module in hw.modules:
                        if module.name == port_name:
                            return isinstance(module, DMAModule)
            # Fallback: name-based heuristic
            upper = port_name.upper()
            return 'RDMA' in upper or 'WDMA' in upper
        
        def _get_dma_direction(hw_name, port_name):
            """Get DMA direction from hw_registry module definition."""
            if hw_registry and hw_name in hw_registry:
                hw = hw_registry[hw_name]
                from ..model.hw_nodes import IPNode
                if isinstance(hw, IPNode):
                    for module in hw.modules:
                        if module.name == port_name and isinstance(module, DMAModule):
                            return getattr(module, 'direction', None)
            return None
        
        def _calc_data_size(port_info):
            """Calculate data size in bytes from port info."""
            sz = port_info.get('size', [])
            if len(sz) < 4 or sz[2] <= 0 or sz[3] <= 0:
                return 0
            bitwidth = port_info.get('bitwidth', 8)
            comp_ratio = port_info.get('comp_ratio', 1.0)
            if port_info.get('comp') != 'enable':
                comp_ratio = 1.0
            return sz[2] * sz[3] * (bitwidth / 8) * comp_ratio
        
        # Process each frame
        for frame_id, task_map in sorted(frame_results.items()):
            for task_id, task_result in task_map.items():
                settings = ip_settings.get(task_id, {})
                if not settings:
                    continue
                
                hw_name = settings.get('hw', task_result.hw_name)
                duration = task_result.duration
                if duration <= 0:
                    continue
                
                # Process input ports (potential Read DMA)
                for port_info in settings.get('inputs', []):
                    port_name = port_info.get('port', '')
                    if not _is_dma_port(hw_name, port_name):
                        continue  # CIN/OTF port — no memory BW
                    
                    data_size = _calc_data_size(port_info)
                    if data_size <= 0:
                        continue
                    
                    # Determine direction from hw.yaml or default to Read for inputs
                    direction = _get_dma_direction(hw_name, port_name)
                    if direction is None:
                        direction = 'read'
                    
                    bw_gbps = (data_size / duration) / 1e9
                    records.append({
                        'start': task_result.start_time,
                        'end': task_result.end_time,
                        'bw_gbps': bw_gbps,
                        'direction': 'Read' if direction == 'read' else 'Write',
                        'parent_ip': hw_name,
                        'dma_name': port_name,
                        'frame_id': frame_id,
                    })
                
                # Process output ports (potential Write DMA)
                for port_info in settings.get('outputs', []):
                    port_name = port_info.get('port', '')
                    if not _is_dma_port(hw_name, port_name):
                        continue  # COUT/OTF port — no memory BW
                    
                    data_size = _calc_data_size(port_info)
                    if data_size <= 0:
                        continue
                    
                    # Determine direction from hw.yaml or default to Write for outputs
                    direction = _get_dma_direction(hw_name, port_name)
                    if direction is None:
                        direction = 'write'
                    
                    bw_gbps = (data_size / duration) / 1e9
                    records.append({
                        'start': task_result.start_time,
                        'end': task_result.end_time,
                        'bw_gbps': bw_gbps,
                        'direction': 'Write' if direction == 'write' else 'Read',
                        'parent_ip': hw_name,
                        'dma_name': port_name,
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

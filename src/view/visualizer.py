"""
Visualizer and Monitor for simulation output.

Provides:
- Monitor: Records task execution data
- Visualizer: Generates Gantt charts and exports CSV
"""

from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING
import pandas as pd

if TYPE_CHECKING:
    from ..controller.simulator import SimulationResults
    from ..model.scenario import ScenarioGraph

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
                               hw_order: list = None,
                               scenario: 'ScenarioGraph' = None) -> Optional['go.Figure']:
        """
        Create a Gantt chart with time in milliseconds (numeric axis).

        Args:
            df: DataFrame with TaskID, HW, StartTime, EndTime columns
            title: Chart title
            hw_order: Optional list of HW names in desired Y-axis order
                      (scenario definition order). If None, ordered by start time.
            scenario: Optional ScenarioGraph for OTF group merging.
                      OTF-connected tasks are drawn on a single row with
                      combined label (e.g., 'HP2~CSIS~PDP').

        Returns:
            Plotly Figure object
        """
        if not PLOTLY_AVAILABLE:
            print("Plotly not available.")
            return None

        if df.empty:
            return None

        fig = go.Figure()

        # ── Build OTF group mapping: hw_name → merged label ──
        hw_to_group_label = {}  # HW name → group label (e.g. "HP2~CSIS_LINK~CSIS")
        group_member_order = {}  # group_label → [hw_names in order]

        if scenario:
            otf_groups = scenario.get_otf_groups()
            for group_tasks in otf_groups:
                # Get HW names for each task in the group, in hw_order sequence
                group_hw_names = []
                seen = set()
                for tid in group_tasks:
                    task = scenario.get_task(tid)
                    if task and task.mapped_hw not in seen:
                        group_hw_names.append(task.mapped_hw)
                        seen.add(task.mapped_hw)

                # Sort by hw_order if available, to get consistent ordering
                if hw_order:
                    group_hw_names.sort(
                        key=lambda h: hw_order.index(h) if h in hw_order else 999
                    )

                group_label = "~".join(group_hw_names)
                group_member_order[group_label] = group_hw_names
                for hw in group_hw_names:
                    hw_to_group_label[hw] = group_label

        # ── Build Y-axis labels ──
        if hw_order:
            seen = set()
            y_labels = []
            for hw in hw_order:
                label = hw_to_group_label.get(hw, hw)
                if label not in seen:
                    y_labels.append(label)
                    seen.add(label)
            # Append any remaining
            for hw in df['HW'].unique():
                label = hw_to_group_label.get(hw, hw)
                if label not in seen:
                    y_labels.append(label)
                    seen.add(label)
        else:
            df_sorted = df.sort_values('StartTime')
            seen = set()
            y_labels = []
            for hw in df_sorted['HW'].unique():
                label = hw_to_group_label.get(hw, hw)
                if label not in seen:
                    y_labels.append(label)
                    seen.add(label)

        # Color palette
        colors = px.colors.qualitative.Set2
        task_colors = {}

        # ── Identify SW tasks for visual differentiation ──
        sw_task_ids = set()
        sw_hw_names = set()   # kept for back-compat
        sw_task_to_group = {}  # task_id → group label (for Y-axis)
        sw_group_labels = set()  # all unique SW group labels
        if scenario:
            for task in scenario.get_tasks():
                if task.is_sw_task:
                    sw_task_ids.add(task.task_id)
                    grp = task.sw_group or task.mapped_hw
                    sw_task_to_group[task.task_id] = grp
                    sw_group_labels.add(grp)
                    sw_hw_names.add(grp)

        # Build task timing lookup for M2M arrows
        task_timing = {}  # task_id → {start_ms, end_ms, y_label, frame_id}

        # ── Pre-build OTF group task lists (for combined tooltip) ──
        otf_task_sets = {}  # group_label → [task_id list in hw_order]
        if scenario:
            for group_tasks in scenario.get_otf_groups():
                group_info = []
                for tid in group_tasks:
                    t = scenario.get_task(tid)
                    if t:
                        group_info.append({'task_id': tid, 'hw': t.mapped_hw})
                if not group_info:
                    continue
                if hw_order:
                    group_info.sort(
                        key=lambda g: hw_order.index(g['hw']) if g['hw'] in hw_order else 999
                    )
                first_hw = group_info[0]['hw']
                label = hw_to_group_label.get(first_hw, first_hw)
                otf_task_sets[label] = group_info

        # ── Pass 1: Collect per-task timing from simulation data (frame 0) ──
        task_actual_timing = {}  # task_id → {start_ms, end_ms, runtime_ms}
        for idx, row in df.iterrows():
            tid = row['TaskID']
            fid = row.get('FrameID', 0)
            if fid == 0 or tid not in task_actual_timing:
                s = row['StartTime'] * 1000
                e = row['EndTime'] * 1000
                task_actual_timing[tid] = {
                    'start_ms': s, 'end_ms': e, 'runtime_ms': e - s
                }

        # ── Pre-build OTF group combined hover text ──
        otf_hover_cache = {}  # group_label → hover_text
        for label, group_info in otf_task_sets.items():
            lines = []
            for g in group_info:
                t = task_actual_timing.get(g['task_id'])
                if t:
                    lines.append(
                        f"  • {g['task_id']} → {g['hw']}  "
                        f"({t['start_ms']:.3f} ~ {t['end_ms']:.3f} ms, "
                        f"Δ{t['runtime_ms']:.3f} ms)"
                    )
                else:
                    lines.append(f"  • {g['task_id']} → {g['hw']}")
            otf_hover_cache[label] = (
                "<b>[OTF Group]</b><br>"
                + "<br>".join(lines)
                + "<extra></extra>"
            )

        # ── Pass 2: Draw bars ──
        for idx, row in df.iterrows():
            task_id = row['TaskID']
            hw = row['HW']
            start = row['StartTime'] * 1000
            end = row['EndTime'] * 1000
            runtime = end - start
            frame_id = row.get('FrameID', 0)

            y_label = hw_to_group_label.get(hw, hw)
            # SW tasks → use sw_group as Y-axis label
            if task_id in sw_task_to_group:
                y_label = sw_task_to_group[task_id]

            if task_id not in task_colors:
                task_colors[task_id] = colors[len(task_colors) % len(colors)]

            # OTF group → combined tooltip with per-task timing
            if y_label in otf_hover_cache:
                hover_text = otf_hover_cache[y_label]
            else:
                hover_text = (
                    f"<b>{task_id}</b> ({hw})<br>"
                    f"Start: {start:.3f} ms<br>"
                    f"End: {end:.3f} ms<br>"
                    f"Runtime: {runtime:.3f} ms"
                    "<extra></extra>"
                )

            # SW task → subtle dot pattern + per-task color
            is_sw = task_id in sw_task_ids
            if is_sw:
                bar_color = task_colors[task_id]
                pattern_cfg = dict(
                    shape='.',
                    size=6,
                    solidity=0.15,
                    fgcolor='rgba(60, 90, 140, 0.5)',
                )
            else:
                bar_color = task_colors[task_id]
                pattern_cfg = None

            bar_kwargs = dict(
                x=[runtime],
                y=[y_label],
                base=start,
                orientation='h',
                name=task_id,
                marker_color=bar_color,
                text=task_id,
                textposition='inside',
                hovertemplate=hover_text,
                showlegend=task_id not in [t.name for t in fig.data[:-1]] if fig.data else True,
            )
            if pattern_cfg:
                bar_kwargs['marker_pattern'] = pattern_cfg

            fig.add_trace(go.Bar(**bar_kwargs))

            # Store timing for M2M arrow drawing (use frame 0 only)
            if frame_id == 0 or task_id not in task_timing:
                task_timing[task_id] = {
                    'start_ms': start,
                    'end_ms': end,
                    'y_label': y_label,
                }

        # Add frame interval annotations for the last task (periodicity check)
        if 'FrameID' in df.columns:
            self._add_frame_interval_annotations(fig, df, y_labels)

        # ── M2M dependency arrows ──
        m2m_annotations = []
        if scenario:
            m2m_edges = scenario.get_m2m_dependencies()
            for src_tid, dst_tid in m2m_edges:
                src_info = task_timing.get(src_tid)
                dst_info = task_timing.get(dst_tid)
                if not src_info or not dst_info:
                    continue
                # Arrow from end of source to start of destination
                m2m_annotations.append(dict(
                    x=dst_info['start_ms'],
                    y=dst_info['y_label'],
                    ax=src_info['end_ms'],
                    ay=src_info['y_label'],
                    xref='x', yref='y',
                    axref='x', ayref='y',
                    showarrow=True,
                    arrowhead=3,
                    arrowsize=1.2,
                    arrowwidth=1.5,
                    arrowcolor='rgba(80, 80, 80, 0.7)',
                ))

        # ── SW / HW divider line ──
        divider_shapes = []
        if sw_hw_names and y_labels:
            # Plotly positions: bottom=0, top=len-1.  SW labels are at the top.
            reversed_labels = y_labels[::-1]  # matches categoryarray
            sw_count = sum(1 for lbl in reversed_labels if lbl in sw_hw_names)
            if 0 < sw_count < len(reversed_labels):
                # Divider between last HW row and first SW row
                divider_y = len(reversed_labels) - sw_count - 0.5
                divider_shapes.append(dict(
                    type='line',
                    x0=0, x1=1,
                    y0=divider_y, y1=divider_y,
                    xref='paper', yref='y',
                    line=dict(color='rgba(100,100,100,0.6)', width=2, dash='dashdot'),
                ))

        # Order Y-axis
        fig.update_layout(
            title=title,
            xaxis_title='Time (ms)',
            yaxis_title='Hardware',
            barmode='overlay',
            height=300 + len(y_labels) * 40,
            yaxis={'categoryorder': 'array', 'categoryarray': y_labels[::-1]},
            annotations=m2m_annotations,
            shapes=divider_shapes,
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
        #    Calculate scenario average total BW and power (frame 0)
        frame0_records = [r for r in dma_records if r.get('frame_id', 0) == 0]
        avg_total_gbps = sum(r['bw_gbps'] for r in frame0_records)
        total_power_mw = sum(r.get('bw_power_mw', 0) for r in frame0_records)
        total_power_ma = sum(r.get('bw_power_ma', 0) for r in frame0_records)
        
        num_rows = 1 + len(seen_ips)
        total_title = f"Total BW (Avg: {avg_total_gbps:.2f} GB/s, Power: {total_power_mw:.1f} mW / {total_power_ma:.1f} mA)"
        
        # Per-IP power summaries
        ip_titles = []
        for ip in seen_ips:
            ip_recs = [r for r in frame0_records if r['parent_ip'] == ip]
            ip_pwr_mw = sum(r.get('bw_power_mw', 0) for r in ip_recs)
            ip_pwr_ma = sum(r.get('bw_power_ma', 0) for r in ip_recs)
            ip_bw = sum(r['bw_gbps'] for r in ip_recs)
            ip_titles.append(f"{ip} BW ({ip_bw:.2f} GB/s, {ip_pwr_mw:.1f} mW / {ip_pwr_ma:.1f} mA)")
        
        subplot_titles = [total_title] + ip_titles
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
                
                duration_ms = end_ms - start_ms
                # Total data transferred (GB) = BW (GB/s) × duration (s)
                total_gb = rec['bw_gbps'] * (duration_ms / 1000)
                # BW power fields
                pwr_mw = rec.get('bw_power_mw', 0.0)
                pwr_ma = rec.get('bw_power_ma', 0.0)
                
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
                        f"BW: {rec['bw_gbps']:.2f} GB/s ({rec.get('bw_mbs', 0):.1f} MB/s)<br>"
                        f"Start: {start_ms:.3f} ms<br>"
                        f"End: {end_ms:.3f} ms<br>"
                        f"Duration: {duration_ms:.3f} ms<br>"
                        f"Total: {total_gb:.4f} GB<br>"
                        f"Power: {pwr_mw:.2f} mW / {pwr_ma:.2f} mA<br>"
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
        
        BW formula per DMA port (MB/s):
            bw = comp_ratio × fps × W × H × (bitwidth / 8) × BPP_MAP[fmt] × r_w_rate / 1e6
        
        BW Power formula per DMA port:
            bw_power_mw = bw (MB/s) × bw_power_coeff (mW/GB/s) / 1000 × llc_weight
            bw_power_ma = bw_power_mw / vBat / pmic_efficiency
        
        BW is assumed uniform during task duration.
        """
        from ..model.modules import DMAModule
        from ..model.hw_nodes import SensorNode
        from ..model.bw import calc_port_bw, is_dma_port_name
        
        ip_settings = getattr(scenario, '_ip_settings', {})
        records = []
        
        # Get fps from sensor node
        fps = 30.0  # default
        if hw_registry:
            for hw in hw_registry.values():
                if isinstance(hw, SensorNode):
                    fps = hw.fps
                    break
        
        # BW power parameters from scenario
        bw_power_coeff = getattr(scenario, '_bw_power_coeff', 80.0)   # mW/GB/s
        vBat = getattr(scenario, '_vBat', 4.0)                        # V
        pmic_eff = getattr(scenario, '_pmic_efficiency', 0.85)
        llc_power_coeff = getattr(scenario, '_llc_power_coeff', 8.0)
        llc_default_hit = getattr(scenario, '_llc_default_hit_ratio', 0.0)
        
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
            return is_dma_port_name(port_name)
        
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
        
        def _calc_bw_and_power(port_info):
            """Calculate DMA bandwidth (MB/s) and power (mW, mA).

            Delegates to the shared formula in src/model/bw.py.
            bw_mbs is the DRAM-effective BW (LLC hits excluded), so the
            BW timeline chart shows actual DRAM traffic.

            Returns:
                (bw_mbs, bw_power_mw, bw_power_ma) or (0, 0, 0) if invalid
            """
            rec = calc_port_bw(port_info, fps, bw_power_coeff, vBat, pmic_eff,
                               llc_power_coeff=llc_power_coeff,
                               llc_default_hit_ratio=llc_default_hit)
            return rec['bw_mbs'], rec['bw_power_mw'], rec['bw_power_ma']
        
        def _append_record(records, task_result, port_info, hw_name, direction, frame_id):
            """Calculate BW/power and append record."""
            port_name = port_info.get('port', '')
            if not _is_dma_port(hw_name, port_name):
                return  # CIN/OTF port — no memory BW
            
            bw_mbs, bw_power_mw, bw_power_ma = _calc_bw_and_power(port_info)
            if bw_mbs <= 0:
                return
            
            # Determine direction from hw.yaml or use default
            hw_direction = _get_dma_direction(hw_name, port_name)
            if hw_direction is not None:
                direction = 'Read' if hw_direction == 'read' else 'Write'
            
            bw_gbps = bw_mbs / 1e3  # MB/s → GB/s for chart
            records.append({
                'start': task_result.start_time,
                'end': task_result.end_time,
                'bw_gbps': bw_gbps,
                'bw_mbs': bw_mbs,
                'bw_power_mw': bw_power_mw,
                'bw_power_ma': bw_power_ma,
                'direction': direction,
                'parent_ip': hw_name,
                'dma_name': port_name,
                'frame_id': frame_id,
            })
        
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
                    _append_record(records, task_result, port_info, hw_name, 'Read', frame_id)
                
                # Process output ports (potential Write DMA)
                for port_info in settings.get('outputs', []):
                    _append_record(records, task_result, port_info, hw_name, 'Write', frame_id)
        
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

"""
Text-based viewer for hardware and scenario visualization.

Provides human-readable text output for:
- Hardware hierarchy
- Scenario graph structure
- Simulation results summary
"""

from typing import Any, Dict, List, Optional

from ..model.hw_nodes import (
    HWNode, ExternalNode, SensorNode, DisplayNode,
    IPNode, ProcessorNode, MemoryNode
)
from ..model.modules import Module, ScalerModule, CropModule, DMAModule
from ..model.scenario import ScenarioGraph, ConnectionType


class TextViewer:
    """
    Text-based visualization for SoC structures.

    Outputs formatted text representations of:
    - Hardware node hierarchies
    - Scenario task graphs
    - Simulation results
    """

    # Box drawing characters
    BRANCH = "├── "
    LAST = "└── "
    PIPE = "│   "
    SPACE = "    "

    def print_hw_hierarchy(self, hw_registry: Dict[str, HWNode],
                           indent: str = "") -> str:
        """
        Generate text representation of hardware hierarchy.

        Args:
            hw_registry: Dictionary of HW nodes
            indent: Current indentation level

        Returns:
            Formatted string representation
        """
        lines = ["[SoC Hardware Hierarchy]"]

        nodes = list(hw_registry.values())
        for i, node in enumerate(nodes):
            is_last = (i == len(nodes) - 1)
            prefix = self.LAST if is_last else self.BRANCH
            child_indent = self.SPACE if is_last else self.PIPE

            # Format node info
            node_info = self._format_node(node)
            lines.append(f"{indent}{prefix}{node_info}")

            # Show modules for IPNode
            if isinstance(node, IPNode) and node.modules:
                for j, module in enumerate(node.modules):
                    mod_is_last = (j == len(node.modules) - 1)
                    mod_prefix = self.LAST if mod_is_last else self.BRANCH
                    mod_info = self._format_module(module)
                    lines.append(f"{indent}{child_indent}{mod_prefix}{mod_info}")

        return "\n".join(lines)

    def _format_node(self, node: HWNode) -> str:
        """Format a single HW node for display."""
        node_type = node.__class__.__name__
        clock_mhz = node.clock_freq / 1e6

        if isinstance(node, SensorNode):
            # SensorNode with vValid timing info
            v_valid_ms = node.effective_v_valid_time * 1000
            return (f"{node.name} ({node_type}, {node.frame_width}x{node.frame_height}@{node.fps:.0f}fps, "
                    f"mode={node.sensor_mode}, vValid={v_valid_ms:.2f}ms)")
        elif isinstance(node, DisplayNode):
            # DisplayNode with display timing info
            pclk_mhz = node.pixel_clock / 1e6
            return (f"{node.name} ({node_type}, {node.frame_width}x{node.frame_height}@{node.fps:.0f}Hz, "
                    f"mode={node.display_mode}, pclk={pclk_mhz:.1f}MHz)")
        elif isinstance(node, ExternalNode):
            # Generic ExternalNode
            return (f"{node.name} ({node_type}, {node.frame_width}x{node.frame_height}@{node.fps:.0f}fps)")
        elif isinstance(node, IPNode):
            clock_info = f"{clock_mhz:.0f}MHz"
            # Use explicit Tar/Req format if optimization ran (target_freq set)
            if hasattr(node, 'target_freq') and node.target_freq > 0:
                clock_info = f"Tar: {node.target_freq/1e6:.0f}MHz"
                if hasattr(node, 'required_freq') and node.required_freq > 0:
                     clock_info += f" [Req: {node.required_freq/1e6:.0f}MHz]"
            return f"{node.name} ({node_type}, {clock_info}, PPC={node.ppc})"

        elif isinstance(node, ProcessorNode):
            return f"{node.name} ({node_type}, {clock_mhz:.0f}MHz, Cores={node.num_cores})"
        elif isinstance(node, MemoryNode):
            bw_gbps = node.bandwidth / 1e9
            cap_gb = node.capacity / (1024**3)
            return f"{node.name} ({node_type}, {cap_gb:.1f}GB, BW={bw_gbps:.1f}GB/s)"
        else:
            return f"{node.name} ({node_type}, {clock_mhz:.0f}MHz)"

    def _format_module(self, module: Module) -> str:
        """Format a module for display."""
        mod_type = module.__class__.__name__

        if isinstance(module, ScalerModule):
            scale = f"{module.scale_factor[0]:.2f}x{module.scale_factor[1]:.2f}"
            in_size = f"{module.input_size[0]}x{module.input_size[1]}"
            out_size = f"{module.output_size[0]}x{module.output_size[1]}"
            if module.input_size != (0, 0):
                return f"{module.name} ({mod_type}, scale={scale}, {in_size} → {out_size})"
            return f"{module.name} ({mod_type}, scale={scale})"

        elif isinstance(module, CropModule):
            x, y, w, h = module.crop_region
            return f"{module.name} ({mod_type}, region=({x},{y},{w},{h}))"

        elif isinstance(module, DMAModule):
            bw_gbps = module.max_bandwidth / 1e9
            return f"{module.name} ({mod_type}, MO={module.multiple_outstanding}, BW={bw_gbps:.1f}GB/s)"

        else:
            return f"{module.name} ({mod_type})"

    def print_scenario_graph(self, scenario: ScenarioGraph, 
                           hw_registry: Optional[Dict[str, HWNode]] = None) -> str:
        """
        Generate text representation of scenario graph.

        Args:
            scenario: ScenarioGraph instance
            hw_registry: Optional HW registry to show module info

        Returns:
            Formatted string representation
        """
        lines = [f"[Scenario: {scenario.name}]"]

        # Tasks section
        lines.append("")
        lines.append("Tasks:")
        for task in scenario.get_tasks():
            workload_str = ", ".join(f"{k}={v}" for k, v in task.workload.items())
            lines.append(f"  {self.BRANCH}{task.task_id} → {task.mapped_hw} ({workload_str})")

        # Dependencies section
        lines.append("")
        lines.append("Dependencies:")

        m2m_deps = scenario.get_m2m_dependencies()
        otf_deps = scenario.get_otf_dependencies()

        if m2m_deps:
            lines.append("  M2M (Sequential):")
            for src, dst in m2m_deps:
                lines.append(f"    {src} ──→ {dst}")

        if otf_deps:
            lines.append("  OTF (Pipelined):")
            for src, dst in otf_deps:
                lines.append(f"    {src} ═══► {dst}")

        # OTF Groups
        otf_groups = scenario.get_otf_groups()
        if otf_groups:
            lines.append("")
            lines.append("OTF Groups (Synchronized):")
            for i, group in enumerate(otf_groups):
                lines.append(f"  Group {i+1}: [{', '.join(group)}]")

        # Execution order
        lines.append("")
        lines.append("Topological Order:")
        order = scenario.topological_order()
        
        path_str = ""
        for i, task_id in enumerate(order):
            # Task ID
            task_str = task_id
            
            # Module Info (if hw_registry provided)
            if hw_registry:
                task = scenario.get_task(task_id)
                if task and task.mapped_hw in hw_registry:
                    hw = hw_registry[task.mapped_hw]
                    if isinstance(hw, IPNode) and hw.modules:
                        mod_names = ",".join(m.name for m in hw.modules)
                        task_str += f"[{mod_names}]"
            
            path_str += task_str
            
            # Connection arrow to next task
            if i < len(order) - 1:
                next_task = order[i+1]
                edge_type = scenario.get_edge_type(task_id, next_task)
                
                if edge_type == ConnectionType.OTF:
                    path_str += " ══► "
                else: # M2M or None (default to M2M arrow)
                    path_str += " ──→ "
                    
        lines.append(f"  {path_str}")

        return "\n".join(lines)

    def print_simulation_summary(self, results: 'SimulationResults') -> str:
        """
        Generate text summary of simulation results.

        Args:
            results: SimulationResults from simulation

        Returns:
            Formatted summary string
        """
        from ..controller.simulator import SimulationResults

        lines = [
            f"[Simulation Results: {results.scenario_name}]",
            f"",
            f"Total Time: {results.total_time * 1000:.3f} ms",
            f"Total Tasks: {len(results.task_results)}",
            f"Total Energy: {results.get_total_power():.3f} mJ",
            "",
            "Task Execution Timeline:",
            "-" * 80
        ]

        # Header
        lines.append(f"{'Task ID':<20} {'Hardware':<15} {'Start (ms)':<12} {'End (ms)':<12} {'Duration (ms)':<12}")
        lines.append("-" * 80)

        # Sort by start time
        sorted_results = sorted(results.task_results, key=lambda r: r.start_time)

        for result in sorted_results:
            lines.append(
                f"{result.task_id:<20} {result.hw_name:<15} "
                f"{result.start_time*1000:<12.3f} {result.end_time*1000:<12.3f} "
                f"{result.duration*1000:<12.3f}"
            )

        lines.append("-" * 80)
        
        # ASCII Gantt Chart
        lines.append("")
        lines.append("Timing Diagram (ASCII Gantt):")
        lines.append("-" * 80)
        
        if results.total_time > 0:
            chart_width = 80
            # Reserve space for HW name (e.g. 15 chars)
            MAX_HW_LEN = 15
            bar_width = chart_width - MAX_HW_LEN - 3 # 3 for " | "
            time_scale = bar_width / results.total_time
            
            # Time Scale
            total_ms = results.total_time * 1000
            scale_label = f"Scale: {bar_width} chars = {total_ms:.1f} ms ({total_ms/bar_width:.2f} ms/char)"
            lines.append(scale_label)
            
            # Ruler
            ruler = " " * (MAX_HW_LEN + 3)
            tick_vals = [0.0, total_ms * 0.25, total_ms * 0.5, total_ms * 0.75, total_ms]
            tick_pos  = [0, int(bar_width * 0.25), int(bar_width * 0.5), int(bar_width * 0.75), bar_width-1]
            
            # Simple fixed ruler line
            lines.append(f"{'':<{MAX_HW_LEN}} | 0" + "." * (bar_width-2) + f"{total_ms:.1f}ms")
            
            # Group tasks by HW
            hw_tasks: Dict[str, List[Any]] = {}
            for r in results.task_results:
                if r.hw_name not in hw_tasks:
                    hw_tasks[r.hw_name] = []
                hw_tasks[r.hw_name].append(r)
            
            # Print chart
            for hw_name, tasks in hw_tasks.items():
                # HW Name Header
                lines.append(f"{hw_name:<{MAX_HW_LEN}} |")
                
                for task in tasks:
                    start_pos = int(task.start_time * time_scale)
                    duration_len = max(1, int(task.duration * time_scale))
                    
                    # Create bar line
                    bar = " " * start_pos + "#" * duration_len
                    
                    # Add task info
                    info = f"{task.task_id} ({task.start_time*1000:.1f}-{task.end_time*1000:.1f}ms)"
                    
                    lines.append(f"{'':<{MAX_HW_LEN}} | {bar} {info}")
            
            lines.append("-" * 80)
        else:
             lines.append("No simulation data to display.")
        
        lines.append("-" * 80)

        return "\n".join(lines)

    def print_all(self, hw_registry: Dict[str, HWNode],
                  scenario: ScenarioGraph,
                  results: Optional['SimulationResults'] = None) -> str:
        """
        Print complete visualization of HW, scenario, and results.

        Args:
            hw_registry: Dictionary of HW nodes
            scenario: ScenarioGraph instance
            results: Optional simulation results

        Returns:
            Complete formatted output
        """
        sections = [
            self.print_hw_hierarchy(hw_registry),
            "",
            self.print_scenario_graph(scenario)
        ]

        if results is not None:
            sections.extend(["", self.print_simulation_summary(results)])

        return "\n".join(sections)

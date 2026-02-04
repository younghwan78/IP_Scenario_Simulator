"""
Text-based viewer for hardware and scenario visualization.

Provides human-readable text output for:
- Hardware hierarchy
- Scenario graph structure
- Simulation results summary
"""

from typing import Any, Dict, List, Optional

from ..model.hw_nodes import HWNode, IPNode, DMANode, ProcessorNode, MemoryNode, ExternalNode
from ..model.modules import Module, ScalerModule, CropModule
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
        
        if isinstance(node, ExternalNode):
            return (f"{node.name} ({node_type}, {node.frame_width}x{node.frame_height}@{node.fps:.0f}fps, "
                    f"mode={node.sensor_mode})")
        elif isinstance(node, IPNode):
            return f"{node.name} ({node_type}, {clock_mhz:.0f}MHz, PPC={node.ppc})"
        elif isinstance(node, DMANode):
            bw_gbps = node.bandwidth / 1e9
            return f"{node.name} ({node_type}, MO={node.multiple_outstanding}, BW={bw_gbps:.1f}GB/s)"
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
        
        else:
            return f"{module.name} ({mod_type})"
    
    def print_scenario_graph(self, scenario: ScenarioGraph) -> str:
        """
        Generate text representation of scenario graph.
        
        Args:
            scenario: ScenarioGraph instance
            
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
        lines.append(f"  {' → '.join(order)}")
        
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

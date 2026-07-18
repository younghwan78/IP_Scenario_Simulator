"""
Scenario Graph for modeling task flows.

Uses NetworkX DiGraph to represent:
- Tasks: Processing units mapped to hardware
- Dependencies: M2M (sequential) or OTF (pipelined) connections
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

import networkx as nx

from .hw_nodes import HWNode, IPNode
from .modules import ScalerModule, CropModule
from .tokens import JoinPolicy


class ConnectionType(Enum):
    """Types of connections between tasks."""
    M2M = "M2M"  # Memory-to-Memory: Sequential execution
    OTF = "OTF"  # On-The-Fly: Pipelined/synchronized execution


@dataclass
class Task:
    """
    Represents a processing task in the scenario.

    Attributes:
        task_id: Unique task identifier
        mapped_hw: Name of the hardware node to execute on
        workload: Workload parameters (pixels, width, height, ops, data_size, etc.)
        task_type: 'hw' (default) or 'sw' — SW tasks use fixed duration, no power
        duration_ms: Fixed execution time in ms (used when task_type='sw')
        manual_hw_time_ms: Manual processing time in ms for HW tasks (timing only, not BW/power)
        description: Human-readable description (shown in Gantt chart)
        ip_mode: Optional IP operating mode (e.g., 'power_saving', 'high_performance')
        crop_size: Optional crop region (width, height) - requires HW crop support
        join_policy: Multi-input join policy (AND/OR/WINDOW) - default AND_JOIN
        window_size: Window size for WINDOW_BASED join policy
        input_ports: Named input ports for token queues
        output_ports: Named output ports for token distribution
    """
    task_id: str
    mapped_hw: str
    workload: Dict[str, Any] = field(default_factory=dict)
    task_type: str = "hw"            # 'hw' or 'sw'
    duration_ms: Optional[float] = None  # fixed duration for SW tasks
    manual_hw_time_ms: Optional[float] = None  # manual HW time override (timing only)
    latency_ms: float = 0.0              # latency before task execution (SW tasks)
    description: str = ""
    sw_group: Optional[str] = None       # group name for SW task Gantt grouping
    ip_mode: Optional[str] = None
    crop_size: Optional[Tuple[int, int]] = None
    h_blank_margin: float = 0.05  # H-blank margin for IP runtime (default 5%)
    # Token-based additions
    join_policy: JoinPolicy = JoinPolicy.AND_JOIN
    window_size: int = 1
    input_ports: List[str] = field(default_factory=list)
    output_ports: List[str] = field(default_factory=list)

    def get_pixels(self) -> int:
        """Get pixel count from workload (width * height or direct pixels)."""
        if 'pixels' in self.workload:
            return self.workload['pixels']
        width = self.workload.get('width', 0)
        height = self.workload.get('height', 0)
        return width * height

    def get_width(self) -> int:
        """Get frame width."""
        return self.workload.get('width', 0)

    def get_height(self) -> int:
        """Get frame height."""
        return self.workload.get('height', 0)

    def get_size(self) -> Tuple[int, int]:
        """Get frame size as (width, height)."""
        return (self.get_width(), self.get_height())

    def get_crop_size(self) -> Optional[Tuple[int, int]]:
        """Get crop output size if specified."""
        return self.crop_size

    def requires_crop(self) -> bool:
        """Check if task requires crop functionality."""
        return self.crop_size is not None

    def get_ops(self) -> int:
        """Get operation count from workload."""
        return self.workload.get('ops', 0)

    def get_data_size(self) -> int:
        """Get data size from workload."""
        return self.workload.get('data_size', 0)

    @property
    def is_sw_task(self) -> bool:
        """Check if this is a software task (runs on CPU/Processor)."""
        return self.task_type == "sw"


@dataclass
class Dependency:
    """
    Represents a dependency edge between tasks.

    Attributes:
        src: Source task ID
        dst: Destination task ID
        conn_type: Connection type (M2M or OTF)
        buffer_size: Optional buffer size for M2M token queue
        src_port: Source task output port name (default: "output")
        dst_port: Destination task input port name (default: "input")
    """
    src: str
    dst: str
    conn_type: ConnectionType = ConnectionType.M2M
    buffer_size: Optional[int] = None
    # Token port specification
    src_port: str = "output"
    dst_port: str = "input"


class ScenarioGraph:
    """
    Manages the scenario as a directed acyclic graph (DAG).

    Tasks are nodes, dependencies are edges.
    """

    def __init__(self, name: str = "Scenario"):
        """
        Initialize scenario graph.

        Args:
            name: Scenario name
        """
        self.name = name
        self.graph = nx.DiGraph()
        self._tasks: Dict[str, Task] = {}

        # ── Scenario-level metadata (populated by the YAML loader) ──
        # Formal fields so consumers don't rely on getattr defaults.
        self._ip_settings: Dict[str, dict] = {}    # task_id → ip_settings dict
        self._manual_clocks: Dict[str, float] = {}  # hw_name → manual clock [MHz]
        self._resolved_sensor: Dict[str, Any] = {}  # resolved sensor config
        self._bw_power_coeff: float = 80.0          # mW/GB/s
        self._vBat: float = 4.0                     # battery voltage [V]
        self._pmic_efficiency: float = 0.85

        # ── LLC (Last Level Cache) configuration ──
        # _llc_config comes from hw.yaml 'llc:' (project-fixed properties);
        # scenario globals (llc_hit_ratio / llc_power) may override defaults.
        self._llc_config: Dict[str, Any] = {}       # capacity_mb, default_hit_ratio, power_coeff
        self._llc_power_coeff: float = 8.0          # mW/GB/s (LLC access power)
        self._llc_default_hit_ratio: float = 0.0    # 0 = no reduction unless configured
        self._llc_paths: List[dict] = []            # resolved llc_paths entries (for report)

    def add_task(self, task_id: str, mapped_hw: str,
                 workload: Optional[Dict[str, Any]] = None,
                 task_type: str = "hw",
                 duration_ms: Optional[float] = None,
                 latency_ms: float = 0.0,
                 description: str = "",
                 sw_group: Optional[str] = None,
                 ip_mode: Optional[str] = None,
                 crop_size: Optional[Tuple[int, int]] = None,
                 h_blank_margin: float = 0.05,
                 manual_hw_time_ms: Optional[float] = None,
                 join_policy: JoinPolicy = JoinPolicy.AND_JOIN,
                 window_size: int = 1,
                 input_ports: Optional[List[str]] = None,
                 output_ports: Optional[List[str]] = None,
                 **kwargs) -> 'ScenarioGraph':
        """
        Add a task to the scenario.

        Args:
            task_id: Unique task identifier
            mapped_hw: Hardware node name to execute on
            workload: Workload parameters dict
            task_type: 'hw' (default) or 'sw' for CPU/Processor tasks
            duration_ms: Fixed execution time in ms (for SW tasks)
            description: Human-readable description
            ip_mode: Optional IP mode (e.g., 'power_saving', 'high_performance')
            crop_size: Optional crop output size (width, height)
            h_blank_margin: H-blank margin for IP runtime calculation (default: 0.05 = 5%)
            manual_hw_time_ms: Manual HW processing time in ms (timing diagram only, not BW/power)
            join_policy: Multi-input join policy (AND/OR/WINDOW)
            window_size: Window size for WINDOW_BASED join
            input_ports: Named input ports for token queues
            output_ports: Named output ports for token distribution
            **kwargs: Additional workload parameters (width, height, pixels, etc.)

        Returns:
            self for method chaining
        """
        # Copy so the caller's dict is never mutated
        workload = dict(workload) if workload else {}
        workload.update(kwargs)

        task = Task(
            task_id=task_id,
            mapped_hw=mapped_hw,
            workload=workload,
            task_type=task_type,
            duration_ms=duration_ms,
            latency_ms=latency_ms,
            description=description,
            sw_group=sw_group,
            ip_mode=ip_mode,
            crop_size=crop_size,
            h_blank_margin=h_blank_margin,
            manual_hw_time_ms=manual_hw_time_ms,
            join_policy=join_policy,
            window_size=window_size,
            input_ports=input_ports if input_ports else [],
            output_ports=output_ports if output_ports else []
        )
        self._tasks[task_id] = task
        self.graph.add_node(task_id, task=task)
        return self

    def add_dependency(self, src: str, dst: str,
                       conn_type: str | ConnectionType = ConnectionType.M2M,
                       buffer_size: Optional[int] = None,
                       data: Optional[Dict[str, Any]] = None,
                       transfer: Optional[Dict[str, Any]] = None,
                       src_port: str = "output",
                       dst_port: str = "input") -> 'ScenarioGraph':
        """
        Add a dependency between tasks.

        If an edge already exists between src and dst, the new port pair
        is appended to the existing edge's port_pairs list (supporting
        multiple DMA channels between the same two tasks).

        Args:
            src: Source task ID
            dst: Destination task ID
            conn_type: 'M2M' or 'OTF' or ConnectionType enum
            buffer_size: Optional buffer size for M2M token queue
            data: Optional data attributes (format, compression, resolution)
            transfer: Optional transfer attributes (dma nodes, memory)
            src_port: Source task output port name (default: "output")
            dst_port: Destination task input port name (default: "input")

        Returns:
            self for method chaining

        Raises:
            ValueError: If source or destination task doesn't exist
        """
        if src not in self._tasks:
            raise ValueError(f"Source task '{src}' not found in scenario")
        if dst not in self._tasks:
            raise ValueError(f"Destination task '{dst}' not found in scenario")

        if isinstance(conn_type, str):
            conn_type = ConnectionType(conn_type.upper())

        channel = {
            'src_port': src_port,
            'dst_port': dst_port,
            'data': data,
            'transfer': transfer,
            'buffer_size': buffer_size,
        }

        # Check if edge already exists (parallel edges for different ports)
        if self.graph.has_edge(src, dst):
            existing = self.graph[src][dst]
            if existing.get('conn_type') != conn_type:
                raise ValueError(
                    f"Conflicting connection types for edge '{src}'→'{dst}': "
                    f"existing {existing.get('conn_type')}, new {conn_type}. "
                    f"Parallel channels must share the same connection type."
                )
            # Append port pair + full channel config to the existing edge
            # (each channel keeps its own data/transfer — nothing is dropped)
            existing['port_pairs'].append((src_port, dst_port))
            existing['channels'].append(channel)
        else:
            self.graph.add_edge(
                src, dst,
                conn_type=conn_type,
                buffer_size=buffer_size,
                data=data,
                transfer=transfer,
                src_port=src_port,
                dst_port=dst_port,
                port_pairs=[(src_port, dst_port)],
                channels=[channel]
            )
        return self

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def get_tasks(self) -> List[Task]:
        """Get all tasks in the scenario."""
        return list(self._tasks.values())

    def validate_constraints(self, hw_registry: Dict[str, HWNode],
                             allow_unknown_hw: bool = False) -> List[str]:
        """
        Validate scenario requirements against hardware capabilities.

        Single source of truth for HW capability validation — the simulator
        (SoCSimulator._validate_hw_capabilities) delegates here.

        Checks:
        1. IP Mode support (defaults to 'default' if unspecified)
        2. Scaling support (if ScalerModule used with scaling)
        3. Cropping support (if CropModule used or crop_size set)

        Args:
            hw_registry: Dictionary of available hardware nodes
            allow_unknown_hw: If True, tasks mapped to unregistered HW are
                skipped instead of reported (the simulator creates
                placeholder nodes for them).

        Returns:
            List of error messages. Empty list if valid.
        """
        errors = []

        for task in self._tasks.values():
            # Skip validation for SW tasks (they don't need IP modes/crop/scale)
            if task.is_sw_task:
                continue

            if task.mapped_hw not in hw_registry:
                if not allow_unknown_hw:
                    errors.append(f"Task '{task.task_id}' maps to unknown HW '{task.mapped_hw}'")
                continue

            hw = hw_registry[task.mapped_hw]

            # Only IPNodes have specific constraints like modes/scale/crop
            if not isinstance(hw, IPNode):
                # Crop requires an IPNode with crop capability
                if task.requires_crop():
                    errors.append(f"Task '{task.task_id}' requires crop but HW "
                                  f"'{hw.name}' is not an IPNode")
                continue

            # 1. IP Mode Validation
            # Default to "default" mode if not specified in task
            mode = task.ip_mode if task.ip_mode else "default"

            if mode not in hw.supported_modes:
                errors.append(f"Task '{task.task_id}': IP '{hw.name}' does not support mode '{mode}'. "
                              f"Supported: {hw.supported_modes}")

            # 2. Scaling Validation
            # Check if any ScalerModule in this IP is effectively scaling
            is_scaling = False
            for module in hw.modules:
                if isinstance(module, ScalerModule):
                    sx, sy = module.scale_factor
                    # Check if scale factor is significantly different from 1.0
                    if abs(sx - 1.0) > 1e-6 or abs(sy - 1.0) > 1e-6:
                        is_scaling = True
                        break
            
            if is_scaling and not hw.supports_scale:
                errors.append(f"Task '{task.task_id}': IP '{hw.name}' performs scaling "
                              f"(Scaler used) but 'supports_scale' is False.")

            # 3. Cropping Validation
            is_cropping = False
            # Condition A: Task explicitly requests crop output size
            if task.crop_size is not None:
                is_cropping = True
            # Condition B: CropModule is present (implies capability usage)
            else:
                for module in hw.modules:
                    if isinstance(module, CropModule):
                        is_cropping = True
                        break
            
            if is_cropping and not hw.supports_crop:
                errors.append(f"Task '{task.task_id}': IP '{hw.name}' requires cropping "
                              f"but 'supports_crop' is False.")

        return errors

    def get_predecessors(self, task_id: str) -> List[str]:
        """
        Get predecessor task IDs.

        Args:
            task_id: Task to find predecessors for

        Returns:
            List of predecessor task IDs
        """
        return list(self.graph.predecessors(task_id))

    def get_successors(self, task_id: str) -> List[str]:
        """
        Get successor task IDs.

        Args:
            task_id: Task to find successors for

        Returns:
            List of successor task IDs
        """
        return list(self.graph.successors(task_id))

    def get_dependency(self, src: str, dst: str) -> Optional[Dict[str, Any]]:
        """
        Get dependency edge attributes.

        Args:
            src: Source task ID
            dst: Destination task ID

        Returns:
            Edge attributes dict or None
        """
        if self.graph.has_edge(src, dst):
            return dict(self.graph.edges[src, dst])
        return None

    def get_edge_type(self, src: str, dst: str) -> Optional[ConnectionType]:
        """Get connection type for an edge."""
        dep = self.get_dependency(src, dst)
        if dep:
            return dep.get('conn_type')
        return None

    def get_root_tasks(self) -> List[str]:
        """
        Get tasks with no predecessors (entry points).

        Returns:
            List of root task IDs
        """
        return [n for n in self.graph.nodes() if self.graph.in_degree(n) == 0]

    def get_leaf_tasks(self) -> List[str]:
        """
        Get tasks with no successors (exit points).

        Returns:
            List of leaf task IDs
        """
        return [n for n in self.graph.nodes() if self.graph.out_degree(n) == 0]

    def get_otf_groups(self) -> List[List[str]]:
        """
        Find groups of tasks connected by OTF edges.

        OTF-connected tasks execute synchronously with shared timing.

        Returns:
            List of task ID lists (each list is an OTF group)
        """
        # Build subgraph with only OTF edges
        otf_edges = [
            (u, v) for u, v, data in self.graph.edges(data=True)
            if data.get('conn_type') == ConnectionType.OTF
        ]

        if not otf_edges:
            return []

        otf_subgraph = nx.DiGraph()
        otf_subgraph.add_edges_from(otf_edges)

        # Find weakly connected components (groups)
        groups = []
        for component in nx.weakly_connected_components(otf_subgraph):
            groups.append(list(component))

        return groups

    def get_m2m_dependencies(self) -> List[Tuple[str, str]]:
        """
        Get all M2M dependency edges.

        Returns:
            List of (src, dst) tuples
        """
        return [
            (u, v) for u, v, data in self.graph.edges(data=True)
            if data.get('conn_type') == ConnectionType.M2M
        ]

    def get_otf_dependencies(self) -> List[Tuple[str, str]]:
        """
        Get all OTF dependency edges.

        Returns:
            List of (src, dst) tuples
        """
        return [
            (u, v) for u, v, data in self.graph.edges(data=True)
            if data.get('conn_type') == ConnectionType.OTF
        ]

    def validate(self) -> Tuple[bool, List[str]]:
        """
        Validate the scenario graph.

        Checks:
        - Graph is a DAG (no cycles)
        - All edges reference valid tasks

        Returns:
            (is_valid, list of error messages)
        """
        errors = []

        # Check for cycles
        if not nx.is_directed_acyclic_graph(self.graph):
            errors.append("Scenario graph contains cycles")

        # Check that all tasks have mappings
        for task_id, task in self._tasks.items():
            if not task.mapped_hw:
                errors.append(f"Task '{task_id}' has no hardware mapping")

        return len(errors) == 0, errors

    def validate_otf_timing(self, hw_nodes: Dict[str, 'HWNode']) -> Tuple[bool, List[str]]:
        """
        Validate that OTF-connected HW can process pixels within vValid time.

        For each OTF group starting from a SensorNode:
        - Calculate required throughput from vValid constraint
        - Check if all connected IPs can meet the throughput

        Args:
            hw_nodes: Dictionary mapping HW names to HWNode instances

        Returns:
            (is_valid, list of warning/error messages)
        """
        from .hw_nodes import SensorNode, IPNode

        messages = []
        is_valid = True

        # Find OTF groups
        otf_groups = self.get_otf_groups()

        for group in otf_groups:
            # Find the sensor node in this group (entry point)
            sensor_node = None

            for task_id in group:
                task = self._tasks.get(task_id)
                if task:
                    hw = hw_nodes.get(task.mapped_hw)
                    if isinstance(hw, SensorNode):
                        sensor_node = hw
                        break

            if not sensor_node:
                # No sensor in this OTF group, skip
                continue

            # Get required throughput from sensor
            required_throughput = sensor_node.get_required_throughput()
            v_valid_ms = sensor_node.effective_v_valid_time * 1000

            messages.append(
                f"OTF Group [{', '.join(group)}]: "
                f"vValid={v_valid_ms:.2f}ms, "
                f"Required throughput={required_throughput/1e6:.2f} Mpps"
            )

            # Check each IP in the group
            for task_id in group:
                task = self._tasks.get(task_id)
                if not task:
                    continue

                hw = hw_nodes.get(task.mapped_hw)
                if not isinstance(hw, IPNode):
                    continue

                # Calculate IP throughput: clock * ppc * efficiency
                ip_throughput = hw.clock_freq * hw.ppc * hw.efficiency

                # Check if IP can meet the requirement
                if ip_throughput < required_throughput:
                    is_valid = False
                    messages.append(
                        f"  [FAIL] {hw.name}: {ip_throughput/1e6:.2f} Mpps < "
                        f"{required_throughput/1e6:.2f} Mpps required"
                    )
                else:
                    margin = (ip_throughput / required_throughput - 1) * 100
                    messages.append(
                        f"  [OK] {hw.name}: {ip_throughput/1e6:.2f} Mpps "
                        f"(+{margin:.1f}% margin)"
                    )

        return is_valid, messages

    def _calculate_required_freq(self, required_throughput: float,
                               ip_node: 'IPNode') -> float:
        """
        Calculate required frequency for an IP to meet throughput.

        Args:
            required_throughput: Required throughput in pixels/sec
            ip_node: IPNode instance

        Returns:
            Required frequency in Hz
        """
        # Frequency = Throughput / (PPC * Efficiency)
        # TODO: Add margin or other factors here for scalability
        if ip_node.ppc <= 0 or ip_node.efficiency <= 0:
            return float('inf')

        return required_throughput / (ip_node.ppc * ip_node.efficiency)

    def optimize_otf_clocks(self, hw_nodes: Dict[str, 'HWNode']) -> List[str]:
        """
        Optimize clock frequencies for OTF-connected IPs.

        Finds optimal clock from clock_table to meet sensor vValid constraint.
        Prioritizes scenario-configured clock if set.

        Args:
            hw_nodes: Dictionary mapping HW names to HWNode instances

        Returns:
            List of optimization messages
        """
        from .hw_nodes import SensorNode, IPNode

        messages = []
        otf_groups = self.get_otf_groups()

        for group in otf_groups:
            # 1. Find Sensor (Source of constraint)
            sensor_node = None
            for task_id in group:
                task = self._tasks.get(task_id)
                if task:
                    hw = hw_nodes.get(task.mapped_hw)
                    if isinstance(hw, SensorNode):
                        sensor_node = hw
                        break

            if not sensor_node:
                continue

            # 2. Calculate Required Throughput
            # Effective vValid time handles default case (no blanking)
            required_throughput = sensor_node.get_required_throughput()
            v_valid_ms = sensor_node.effective_v_valid_time * 1000

            messages.append(f"Optimizing OTF Group [{', '.join(group)}]")
            messages.append(f"  Constraint: vValid={v_valid_ms:.2f}ms, "
                            f"Req Throughput={required_throughput/1e6:.2f}Mpps")

            # 3. Optimize IOs in group
            for task_id in group:
                task = self._tasks.get(task_id)
                if not task: continue

                hw = hw_nodes.get(task.mapped_hw)
                if not isinstance(hw, IPNode): continue

                # Calculate required freq
                req_freq = self._calculate_required_freq(required_throughput, hw)
                hw.required_freq = req_freq

                # Check if clock was already set by scenario (target_freq > 0)
                # In main.py, apply_scenario_settings sets target_freq if clock is specified
                if hw.target_freq > 0:
                     messages.append(f"  [INFO] {hw.name}: Using Manual Clock "
                                     f"{hw.target_freq/1e6:.1f}MHz (Req: {req_freq/1e6:.1f}MHz)")
                     # Ensure current clock is reflected
                     hw.clock_freq = hw.target_freq
                     continue

                # Find minimum clock in table >= req_freq
                target_freq = hw.max_clock if hw.max_clock else req_freq

                if hw.clock_table:
                    # Sort table ascending
                    sorted_clocks = sorted(hw.clock_table)
                    found = False
                    for clk in sorted_clocks:
                        if clk >= req_freq:
                            target_freq = clk
                            found = True
                            break

                    if not found:
                        messages.append(f"  [WARN] {hw.name}: Req {req_freq/1e6:.1f}MHz "
                                        f"> Max {sorted_clocks[-1]/1e6:.1f}MHz")
                        target_freq = sorted_clocks[-1] # Use max available
                else:
                    # No table, use max_clock if available and less than req
                    if hw.max_clock and req_freq > hw.max_clock:
                         messages.append(f"  [WARN] {hw.name}: Req {req_freq/1e6:.1f}MHz "
                                        f"> Max {hw.max_clock/1e6:.1f}MHz")
                         target_freq = hw.max_clock
                    elif hw.max_clock:
                         # If max_clock exists but req is lower, optimize to req?
                         # Or stick to max? Logic: "Find minimum clock... based on table"
                         # Without table, continuous.
                         target_freq = req_freq
                    else:
                         target_freq = req_freq

                # Update HW
                hw.target_freq = target_freq
                hw.clock_freq = target_freq

                messages.append(f"  {hw.name}: Req={req_freq/1e6:.1f}MHz "
                                f"-> Set={target_freq/1e6:.1f}MHz")

        return messages

    def topological_order(self) -> List[str]:
        """
        Get tasks in topological order.

        Returns:
            List of task IDs in execution order
        """
        return list(nx.topological_sort(self.graph))

    def __len__(self) -> int:
        """Return number of tasks."""
        return len(self._tasks)

    def __contains__(self, task_id: str) -> bool:
        """Check if task exists."""
        return task_id in self._tasks

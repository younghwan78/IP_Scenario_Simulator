"""
SoC Simulator - Main simulation engine.

Orchestrates SimPy-based discrete event simulation using:
- Hardware registry for HW nodes
- Scenario graph for task dependencies
- Analyzers for result processing
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Optional, Set
from abc import ABC, abstractmethod

import simpy

from ..model.hw_nodes import HWNode, IPNode, ProcessorNode, MemoryNode
from ..model.modules import DMAModule
from ..model.scenario import ScenarioGraph, ConnectionType, Task
from ..model.tokens import (
    FrameToken, TokenQueue, TokenJoin, TokenFork, TokenTransform,
    JoinPolicy, DEFAULT_QUEUE_CAPACITY, DMA_QUEUE_CAPACITY, create_source_token
)


BPP_MAP = {
    "NV12": 1.5,
    "YUV420": 1.5,
    "YUV422": 2.0,
    "YUV444": 3.0,
    "RGB888": 3.0,
    "RGB": 3.0,
    "ARGB": 4.0,
    "RGBA": 4.0,
    "RAW10": 1.25,
    "RAW12": 1.5,
    "RAW14": 1.75,
    "RAW16": 2.0,
    "P010": 2.0,
    "P210": 3.2,
    "BAYER_PACKED": 1.0,
    "BAYER_UNPACKED": 2.0,
    "STAT": 1.0,
    "UV8": 2.0,
}
BPP_DEFAULT = 1.0  # Default BPP for unknown formats


@dataclass
class TaskResult:
    """Result of a single task execution."""
    task_id: str
    hw_name: str
    start_time: float
    end_time: float
    duration: float
    power_consumed: float  # in mJ
    frame_id: int = 0  # Frame index for multi-frame simulation
    workload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationResults:
    """Complete simulation results."""
    scenario_name: str
    total_time: float
    num_frames: int = 1  # Total frames simulated
    task_results: List[TaskResult] = field(default_factory=list)

    def add_result(self, result: TaskResult) -> None:
        """Add a task result."""
        self.task_results.append(result)

    def get_by_hw(self, hw_name: str) -> List[TaskResult]:
        """Get results for specific hardware."""
        return [r for r in self.task_results if r.hw_name == hw_name]

    def get_by_task(self, task_id: str) -> Optional[TaskResult]:
        """Get result for specific task."""
        for r in self.task_results:
            if r.task_id == task_id:
                return r
        return None
    
    def get_by_frame(self, frame_id: int) -> List[TaskResult]:
        """Get results for specific frame."""
        return [r for r in self.task_results if r.frame_id == frame_id]

    def get_total_power(self) -> float:
        """Get total power consumed (mJ)."""
        return sum(r.power_consumed for r in self.task_results)


class BaseAnalyzer(ABC):
    """Base class for result analyzers."""

    @abstractmethod
    def analyze(self, results: SimulationResults) -> Dict[str, Any]:
        """
        Analyze simulation results.

        Args:
            results: SimulationResults object

        Returns:
            Analysis report as dictionary
        """
        pass


class SoCSimulator:
    """
    Main SoC simulation engine using SimPy.

    Handles:
    - Hardware registration
    - Scenario loading
    - M2M/OTF dependency resolution
    - Event-driven task execution
    """

    def __init__(self):
        """Initialize simulator."""
        self.env: Optional[simpy.Environment] = None
        self.hw_registry: Dict[str, HWNode] = {}
        self.scenario: Optional[ScenarioGraph] = None
        self.analyzers: List[BaseAnalyzer] = []

        # Internal state for simulation
        self._task_events: Dict[str, simpy.Event] = {}
        self._task_results: List[TaskResult] = []
        self._otf_groups: List[Set[str]] = []
        
        # Token infrastructure (Principle #1: separate queues per input)
        self._token_joins: Dict[str, TokenJoin] = {}  # task_id -> TokenJoin
        self._token_forks: Dict[str, TokenFork] = {}  # task_id -> TokenFork
        self._token_enabled: bool = False  # Enable token mode for multi-input scenarios

    def register_hw(self, node: HWNode) -> 'SoCSimulator':
        """
        Register a hardware node.

        Args:
            node: HWNode instance to register

        Returns:
            self for method chaining
        """
        self.hw_registry[node.name] = node
        return self

    def register_hw_list(self, nodes: List[HWNode]) -> 'SoCSimulator':
        """
        Register multiple hardware nodes.

        Args:
            nodes: List of HWNode instances

        Returns:
            self for method chaining
        """
        for node in nodes:
            self.register_hw(node)
        return self

    def load_scenario(self, scenario: ScenarioGraph) -> 'SoCSimulator':
        """
        Load a scenario for simulation.

        Args:
            scenario: ScenarioGraph instance

        Returns:
            self for method chaining
        """
        self.scenario = scenario
        return self

    def add_analyzer(self, analyzer: BaseAnalyzer) -> 'SoCSimulator':
        """
        Add an analyzer for post-processing results.

        Args:
            analyzer: BaseAnalyzer instance

        Returns:
            self for method chaining
        """
        self.analyzers.append(analyzer)
        return self

    def _get_hw(self, name: str) -> HWNode:
        """Get hardware node by name, or create a placeholder."""
        if name in self.hw_registry:
            return self.hw_registry[name]

        # Create placeholder IP node for unknown hardware
        placeholder = IPNode(name=name, clock_freq=1e9, ppc=1.0)
        self.hw_registry[name] = placeholder
        return placeholder

    def _validate_hw_capabilities(self) -> None:
        """
        Validate that hardware supports required task features.

        Checks:
        - Crop support if task requires crop
        - IP mode compatibility

        Raises:
            ValueError: If validation fails
        """
        errors = []

        for task in self.scenario.get_tasks():
            # Skip SW tasks — they don't require HW capability validation
            if task.is_sw_task:
                continue

            hw = self._get_hw(task.mapped_hw)

            # Check crop support
            if task.requires_crop():
                if isinstance(hw, IPNode):
                    if not hw.supports_crop:
                        errors.append(
                            f"Task '{task.task_id}' requires crop but HW '{hw.name}' "
                            f"does not support crop (set supports_crop=True)"
                        )
                else:
                    errors.append(
                        f"Task '{task.task_id}' requires crop but HW '{hw.name}' "
                        f"is not an IPNode"
                    )

            # Check IP mode support (treat None/missing as 'default')
            ip_mode = task.ip_mode if task.ip_mode else 'default'
            if isinstance(hw, IPNode):
                if ip_mode not in hw.supported_modes:
                    errors.append(
                        f"Task '{task.task_id}' uses mode '{ip_mode}' but HW "
                        f"'{hw.name}' only supports: {hw.supported_modes}"
                    )

        if errors:
            raise ValueError("HW capability validation failed:\n  " + "\n  ".join(errors))

    def _init_resources(self) -> None:
        """Initialize SimPy resources for all HW nodes."""
        for hw in self.hw_registry.values():
            hw.resource = simpy.Resource(self.env, capacity=1)
            # Initialize resources for DMA modules in IP
            if isinstance(hw, IPNode):
                for module in hw.modules:
                    if isinstance(module, DMAModule):
                        module.resource = simpy.Resource(self.env, capacity=1)

    def _calculate_transfer_size(self, width: int, height: int, fmt: str, 
                               compression: str, dma_module: DMAModule) -> int:
        """Calculate transfer size based on resolution, format, and compression."""
        bpp = BPP_MAP.get(fmt, BPP_DEFAULT)
        base_size = width * height * bpp
        
        # Apply compression ratio if supported
        if compression in dma_module.supported_compressions:
            ratio = dma_module.compression_ratios.get(compression, 1.0)
            return int(base_size * ratio)
        return int(base_size)

    def _simulate_dma_transfer(self, src_task_id: str, dst_task_id: str, 
                             transfer_config: Dict, data_config: Dict) -> Generator:
        """Simulate Write DMA -> Memory -> Read DMA chain."""
        # 1. Parse config
        write_dma_name = transfer_config.get('write_dma')
        read_dma_name = transfer_config.get('read_dma')
        
        # Resolve Data Size
        fmt = data_config.get('format', 'NV12')
        comp = data_config.get('compression', 'Linear')
        
        width = 0
        height = 0
        src_task = self.scenario.get_task(src_task_id)
        if src_task:
             if src_task.crop_size:
                 width, height = src_task.crop_size
             else:
                 width = src_task.workload.get('width', 0)
                 height = src_task.workload.get('height', 0)
        
        # 2. Write DMA (Resolve from IP)
        if write_dma_name:
            write_dma = self._resolve_dma_module(src_task_id, write_dma_name)
            size = self._calculate_transfer_size(width, height, fmt, comp, write_dma)
            
            dma_time = write_dma.get_transfer_time(size)
            
            # Request resource (DMAModule._resource)
            # Note: We initialized module.resource in _init_resources
            with write_dma.resource.request() as req:
                yield req
                yield self.env.timeout(dma_time)
            
            self._record_dma_result(src_task_id, f"{write_dma.name}(Write)", dma_time, size, fmt)

        # 3. Read DMA (Resolve from IP)
        if read_dma_name:
            read_dma = self._resolve_dma_module(dst_task_id, read_dma_name)
            size = self._calculate_transfer_size(width, height, fmt, comp, read_dma)
            
            dma_time = read_dma.get_transfer_time(size)
            
            with read_dma.resource.request() as req:
                yield req
                yield self.env.timeout(dma_time)
                
            self._record_dma_result(dst_task_id, f"{read_dma.name}(Read)", dma_time, size, fmt)

    def _resolve_dma_module(self, task_id: str, module_name: str) -> DMAModule:
        """Find DMAModule within the IP mapped to the task."""
        task = self.scenario.get_task(task_id)
        if not task:
             raise ValueError(f"Task {task_id} not found")
        
        hw = self._get_hw(task.mapped_hw)
        if hasattr(hw, 'get_module'):
            module = hw.get_module(module_name)
            if module and isinstance(module, DMAModule):
                return module
        
        raise ValueError(f"DMA Module '{module_name}' not found in HW '{hw.name}' (mapped to '{task_id}')")

    def _record_dma_result(self, owner_task_id: str, hw_name: str, duration: float, size: int, fmt: str):
        """Helper to record DMA task result."""
        start = self.env.now - duration
        frame_id = getattr(self, '_current_frame_id', 0)
        result = TaskResult(
            task_id=f"dma_{owner_task_id}_{hw_name}", 
            hw_name=hw_name,
            start_time=start,
            end_time=self.env.now,
            duration=duration,
            power_consumed=0.0,
            frame_id=frame_id,
            workload={'size': size, 'fmt': fmt}
        )
        self._task_results.append(result)

    def _simulate_dma_transfer_process(self, src_task_id: str, dst_task_id: str,
                                       transfer_config: Dict, data_config: Dict) -> Generator:
        """SimPy process wrapper for _simulate_dma_transfer (for parallel execution)."""
        yield from self._simulate_dma_transfer(src_task_id, dst_task_id, transfer_config, data_config)

    def _run_task_process(self, task: Task, frame_start_offset: float = 0.0) -> Generator:
        """
        SimPy process for executing a single task.

        Args:
            task: Task to execute
            frame_start_offset: Time offset for this frame (for multi-frame)
        """
        task_id = task.task_id
        hw = self._get_hw(task.mapped_hw)
        frame_id = getattr(self, '_current_frame_id', 0)

        # Wait until frame start time
        if frame_start_offset > self.env.now:
            yield self.env.timeout(frame_start_offset - self.env.now)

        # Wait for predecessor events (M2M dependencies)
        predecessors = self.scenario.get_predecessors(task_id)
        m2m_preds = []
        otf_preds = []

        for pred_id in predecessors:
            edge_type = self.scenario.get_edge_type(pred_id, task_id)
            if edge_type == ConnectionType.M2M:
                m2m_preds.append(pred_id)
            else:  # OTF
                otf_preds.append(pred_id)

        # Wait for ALL M2M predecessors to complete in parallel
        if m2m_preds:
            pred_events = [self._task_events[p] for p in m2m_preds if p in self._task_events]
            if pred_events:
                yield self.env.all_of(pred_events)

        # Run DMA transfers in parallel (all predecessors already completed)
        dma_processes = []
        for pred_id in m2m_preds:
            edge_transfer = self.scenario.graph.edges[pred_id, task_id].get('transfer')
            if edge_transfer:
                edge_data = self.scenario.graph.edges[pred_id, task_id].get('data', {})
                dma_processes.append(
                    self.env.process(self._simulate_dma_transfer_process(
                        pred_id, task_id, edge_transfer, edge_data)))
        if dma_processes:
            yield self.env.all_of(dma_processes)

        # For OTF: wait for all OTF group members to be ready
        # (handled by _run_otf_group separately)

        # Record start time
        start_time = self.env.now

        # SW task: apply latency then use fixed duration, skip HW processing
        if task.is_sw_task:
            # Latency before task execution begins
            if task.latency_ms > 0:
                yield self.env.timeout(task.latency_ms / 1000.0)
            start_time = self.env.now
            processing_time = (task.duration_ms or 0.0) / 1000.0
            yield self.env.timeout(processing_time)
            end_time = self.env.now
            result = TaskResult(
                task_id=task_id,
                hw_name=task.mapped_hw,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                power_consumed=0.0,  # SW tasks excluded from power
                frame_id=frame_id,
                workload=task.workload
            )
            self._task_results.append(result)
            self._task_events[task_id].succeed()
            return

        # Calculate processing time (inject h_blank_margin into workload)
        workload = {**task.workload, 'h_blank_margin': task.h_blank_margin}
        processing_time = hw.get_processing_time(workload)

        # Request hardware resource (for contention)
        with hw.resource.request() as req:
            yield req

            # Simulate processing
            yield self.env.timeout(processing_time)

        # Record end time
        end_time = self.env.now
        duration = end_time - start_time

        # Calculate power
        hw.utilization = 1.0  # Active during processing
        power = hw.get_power_consumption(duration)

        # Store result
        result = TaskResult(
            task_id=task_id,
            hw_name=hw.name,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            power_consumed=power,
            frame_id=frame_id,
            workload=task.workload
        )
        self._task_results.append(result)

        # Signal task completion
        self._task_events[task_id].succeed()

    def _run_otf_group_process(self, group: List[str], frame_start_offset: float = 0.0) -> Generator:
        """Legacy OTF group process for single-frame (backward compatibility)."""
        yield from self._run_otf_group_process_framed(group, 0, frame_start_offset)

    def _run_task_process_framed(self, task: Task, frame_id: int, frame_start_offset: float = 0.0) -> Generator:
        """
        SimPy process for executing a single task with frame tracking.

        Args:
            task: Task to execute
            frame_id: Frame index
            frame_start_offset: Time offset for this frame
        """
        task_id = task.task_id
        hw = self._get_hw(task.mapped_hw)
        task_events = self._frame_task_events[frame_id]

        # Wait until frame start time
        if frame_start_offset > self.env.now:
            yield self.env.timeout(frame_start_offset - self.env.now)

        # Wait for predecessor events (M2M dependencies) from SAME frame
        predecessors = self.scenario.get_predecessors(task_id)
        m2m_preds = []

        for pred_id in predecessors:
            edge_type = self.scenario.get_edge_type(pred_id, task_id)
            if edge_type == ConnectionType.M2M:
                m2m_preds.append(pred_id)

        # Wait for ALL M2M predecessors in this frame to complete in parallel
        if m2m_preds:
            pred_events = [task_events[p] for p in m2m_preds if p in task_events]
            if pred_events:
                yield self.env.all_of(pred_events)

        # Run DMA transfers in parallel (all predecessors already completed)
        dma_processes = []
        for pred_id in m2m_preds:
            edge_transfer = self.scenario.graph.edges[pred_id, task_id].get('transfer')
            if edge_transfer:
                edge_data = self.scenario.graph.edges[pred_id, task_id].get('data', {})
                self._current_frame_id = frame_id
                dma_processes.append(
                    self.env.process(self._simulate_dma_transfer_process(
                        pred_id, task_id, edge_transfer, edge_data)))
        if dma_processes:
            yield self.env.all_of(dma_processes)

        # Record start time
        start_time = self.env.now

        # SW task: apply latency then use fixed duration, skip HW processing
        if task.is_sw_task:
            # Latency before task execution begins
            if task.latency_ms > 0:
                yield self.env.timeout(task.latency_ms / 1000.0)
            start_time = self.env.now
            processing_time = (task.duration_ms or 0.0) / 1000.0
            yield self.env.timeout(processing_time)
            end_time = self.env.now
            result = TaskResult(
                task_id=task_id,
                hw_name=task.mapped_hw,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                power_consumed=0.0,  # SW tasks excluded from power
                frame_id=frame_id,
                workload=task.workload
            )
            self._task_results.append(result)
            task_events[task_id].succeed()
            return

        # Calculate processing time (inject h_blank_margin into workload)
        workload = {**task.workload, 'h_blank_margin': task.h_blank_margin}
        processing_time = hw.get_processing_time(workload)

        # Request hardware resource (for contention)
        with hw.resource.request() as req:
            yield req
            yield self.env.timeout(processing_time)

        # Record end time
        end_time = self.env.now
        duration = end_time - start_time

        # Calculate power
        hw.utilization = 1.0
        power = hw.get_power_consumption(duration)

        # Store result
        result = TaskResult(
            task_id=task_id,
            hw_name=hw.name,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            power_consumed=power,
            frame_id=frame_id,
            workload=task.workload
        )
        self._task_results.append(result)

        # Signal task completion for this frame
        task_events[task_id].succeed()

    def _run_otf_group_process_framed(self, group: List[str], frame_id: int, frame_start_offset: float = 0.0) -> Generator:
        """
        SimPy process for executing OTF-connected tasks with frame tracking.

        Args:
            group: List of task IDs in the OTF group
            frame_id: Frame index
            frame_start_offset: Time offset for this frame
        """
        if not group:
            return

        task_events = self._frame_task_events.get(frame_id, {})

        # Get all tasks in the group
        tasks = [self.scenario.get_task(tid) for tid in group]
        tasks = [t for t in tasks if t is not None]

        if not tasks:
            return

        # Wait until frame start time
        if frame_start_offset > self.env.now:
            yield self.env.timeout(frame_start_offset - self.env.now)

        # Wait for any M2M predecessors of the group (from same frame)
        all_preds = set()
        for task in tasks:
            for pred_id in self.scenario.get_predecessors(task.task_id):
                edge_type = self.scenario.get_edge_type(pred_id, task.task_id)
                if edge_type == ConnectionType.M2M:
                    all_preds.add(pred_id)

        for pred_id in all_preds:
            if pred_id in task_events:
                yield task_events[pred_id]

        # Record start time (same for all)
        start_time = self.env.now

        # Calculate processing times for all tasks (inject h_blank_margin)
        processing_times = []
        for task in tasks:
            hw = self._get_hw(task.mapped_hw)
            workload = {**task.workload, 'h_blank_margin': task.h_blank_margin}
            pt = hw.get_processing_time(workload)
            processing_times.append((task, hw, pt))

        # OTF: throughput limited by slowest (bottleneck)
        max_time = max(pt for _, _, pt in processing_times)
        
        base_start = start_time
        yield self.env.timeout(max_time)
        base_end = self.env.now

        # Store results with Latency Offsets
        for task, hw, individual_time in processing_times:
            latency_us = hw.latency if hasattr(hw, 'latency') else 0.0
            latency_s = latency_us / 1e6
            
            adjusted_start = base_start + latency_s
            adjusted_end = base_end + latency_s
            duration = adjusted_end - adjusted_start
            
            hw.utilization = individual_time / max_time if max_time > 0 else 1.0
            power = hw.get_power_consumption(duration)

            result = TaskResult(
                task_id=task.task_id,
                hw_name=hw.name,
                start_time=adjusted_start,
                end_time=adjusted_end,
                duration=duration,
                power_consumed=power,
                frame_id=frame_id,
                workload=task.workload
            )
            self._task_results.append(result)

            # Signal task completion for this frame
            if task.task_id in task_events:
                task_events[task.task_id].succeed()

    def run(self, num_frames: int = 1) -> SimulationResults:
        """
        Execute the simulation for specified number of frames.

        Frames are pipelined - new frame starts every 1/fps seconds,
        allowing overlap between processing stages.

        Args:
            num_frames: Number of frames to simulate (default: 1)

        Returns:
            SimulationResults containing all task execution data
        """
        if self.scenario is None:
            raise ValueError("No scenario loaded")

        # Validate scenario structure
        is_valid, errors = self.scenario.validate()
        if not is_valid:
            raise ValueError(f"Invalid scenario: {errors}")

        # Validate HW capabilities for tasks
        self._validate_hw_capabilities()

        # Initialize SimPy environment
        self.env = simpy.Environment()
        self._init_resources()
        self._task_results = []

        # Calculate frame interval from sensor fps (if available)
        frame_interval = self._get_frame_interval()

        # Create per-frame task events (each frame has independent completion tracking)
        self._frame_task_events = {}
        
        # Schedule ALL frames at once - they will run in parallel/pipelined
        for frame_id in range(num_frames):
            # Create task events for this frame
            self._frame_task_events[frame_id] = {
                task.task_id: self.env.event()
                for task in self.scenario.get_tasks()
            }
            
            # Calculate frame start time offset
            frame_start_offset = frame_id * frame_interval

            # Find OTF groups
            otf_groups = self.scenario.get_otf_groups()
            otf_task_ids = set()
            for group in otf_groups:
                otf_task_ids.update(group)

            # Schedule OTF groups for this frame
            for group in otf_groups:
                self.env.process(self._run_otf_group_process_framed(
                    group, frame_id, frame_start_offset))

            # Schedule non-OTF tasks for this frame
            for task in self.scenario.get_tasks():
                if task.task_id not in otf_task_ids:
                    self.env.process(self._run_task_process_framed(
                        task, frame_id, frame_start_offset))

        # Run ALL frames together - this enables proper pipelining
        self.env.run()

        # Build results
        results = SimulationResults(
            scenario_name=self.scenario.name,
            total_time=self.env.now,
            num_frames=num_frames,
            task_results=self._task_results
        )

        return results
    
    def _get_frame_interval(self) -> float:
        """Get frame interval in seconds based on sensor fps."""
        from ..model.hw_nodes import SensorNode
        
        for hw in self.hw_registry.values():
            if isinstance(hw, SensorNode):
                return 1.0 / hw.fps
        
        # Default: 30fps = 33.3ms
        return 1.0 / 30.0

    def run_with_analysis(self, num_frames: int = 1) -> Dict[str, Any]:
        """
        Run simulation and apply all analyzers.

        Args:
            num_frames: Number of frames to simulate (default: 1)

        Returns:
            Dictionary with 'results' and analyzer outputs
        """
        results = self.run(num_frames=num_frames)

        output = {
            'results': results,
            'analysis': {}
        }

        for analyzer in self.analyzers:
            name = analyzer.__class__.__name__
            output['analysis'][name] = analyzer.analyze(results)

        return output

    # ============================================================
    # Token Infrastructure Methods
    # ============================================================
    
    def _init_token_infrastructure(self) -> None:
        """
        Initialize token queues for each task's input ports.
        
        Implements Principle #1: Each input port has a separate TokenQueue.
        """
        self._token_joins = {}
        self._token_forks = {}
        
        for task in self.scenario.get_tasks():
            # Create input queues based on incoming edges
            input_queues: Dict[str, TokenQueue] = {}
            
            for pred_id in self.scenario.get_predecessors(task.task_id):
                edge = self.scenario.graph.edges[pred_id, task.task_id]
                dst_port = edge.get('dst_port', 'input')
                buffer_size = edge.get('buffer_size', None)
                
                # Principle #1: Separate queue per input port
                if dst_port not in input_queues:
                    input_queues[dst_port] = TokenQueue.create(
                        self.env, 
                        name=dst_port,
                        capacity=buffer_size
                    )
            
            # Create TokenJoin with task's join policy (Principle #2)
            if input_queues:
                self._token_joins[task.task_id] = TokenJoin(
                    input_queues=input_queues,
                    policy=task.join_policy,
                    window_size=task.window_size,
                    _env=self.env
                )
            
            # Create TokenFork for output distribution
            # Will be populated when processing successors
            self._token_forks[task.task_id] = TokenFork()
    
    def _detect_token_mode(self) -> bool:
        """
        Detect if token mode should be enabled.
        
        Token mode is enabled when any task has:
        - Multiple input connections (multi-input join)
        - Multiple output connections (multi-output fork)
        - Non-default join policy
        """
        for task in self.scenario.get_tasks():
            # Check for multi-input
            preds = self.scenario.get_predecessors(task.task_id)
            if len(preds) > 1:
                return True
            
            # Check for multi-output
            succs = self.scenario.get_successors(task.task_id)
            if len(succs) > 1:
                return True
            
            # Check for non-default join policy
            if task.join_policy != JoinPolicy.AND_JOIN:
                return True
            
            # Check for explicit input/output ports
            if task.input_ports or task.output_ports:
                return True
        
        return False
    
    def _create_output_token(self, task: Task, input_tokens: Dict[str, FrameToken],
                             timestamp: float) -> FrameToken:
        """
        Create output token based on task processing.
        
        Handles size changes from Scaler/Crop modules.
        """
        # Start with first input token or create new
        if input_tokens:
            first_key = next(iter(input_tokens))
            base_token = input_tokens[first_key]
            frame_id = base_token.frame_id
        else:
            frame_id = 0
            base_token = None
        
        # Get output size (may be modified by crop_size)
        width = task.get_width()
        height = task.get_height()
        
        if task.crop_size:
            width, height = task.crop_size
        elif base_token:
            width = width or base_token.width
            height = height or base_token.height
        
        # Create output token
        output_token = FrameToken(
            frame_id=frame_id,
            timestamp=timestamp,
            width=width,
            height=height,
            format=task.workload.get('format', 'NV12'),
            metadata={'source_task': task.task_id}
        )
        
        return output_token
    
    def _distribute_token(self, task: Task, token: FrameToken) -> Generator:
        """
        Distribute output token to all successors.
        
        Implements Principle #3: Token is COPIED for each output (never shared).
        """
        for succ_id in self.scenario.get_successors(task.task_id):
            edge = self.scenario.graph.edges[task.task_id, succ_id]
            dst_port = edge.get('dst_port', 'input')
            
            succ_join = self._token_joins.get(succ_id)
            if succ_join and dst_port in succ_join.input_queues:
                # Principle #3: Create NEW token (deep copy)
                copied_token = token.copy()
                yield succ_join.input_queues[dst_port].store.put(copied_token)

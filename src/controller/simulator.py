"""
SoC Simulator - Main simulation engine.

Orchestrates SimPy-based discrete event simulation using:
- Hardware registry for HW nodes
- Scenario graph for task dependencies
- Analyzers for result processing
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional, Set
from abc import ABC, abstractmethod

import simpy

from ..model.hw_nodes import HWNode, IPNode, DMANode, ProcessorNode, MemoryNode
from ..model.scenario import ScenarioGraph, ConnectionType, Task


BPP_MAP = {
    "NV12": 1.5,
    "YUV420": 1.5,
    "RGB888": 3.0,
    "RGB": 3.0,
    "RAW10": 1.25,
    "RAW12": 1.5,
    "RAW14": 1.75,
    "RAW16": 2.0,
    "P010": 2.0
}


@dataclass
class TaskResult:
    """Result of a single task execution."""
    task_id: str
    hw_name: str
    start_time: float
    end_time: float
    duration: float
    power_consumed: float  # in mJ
    workload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationResults:
    """Complete simulation results."""
    scenario_name: str
    total_time: float
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

    def _calculate_transfer_size(self, width: int, height: int, fmt: str, 
                               compression: str, dma_node: DMANode) -> int:
        """Calculate transfer size based on resolution, format, and compression."""
        bpp = BPP_MAP.get(fmt, 1.0)
        base_size = width * height * bpp
        
        # Apply compression ratio if supported
        if compression in dma_node.supported_compressions:
            ratio = dma_node.compression_ratios.get(compression, 1.0)
            return int(base_size * ratio)
        return int(base_size)

    def _simulate_dma_transfer(self, src_task_id: str, dst_task_id: str, 
                             transfer_config: Dict, data_config: Dict) -> Generator:
        """Simulate Write DMA -> Memory -> Read DMA chain."""
        # 1. Parse config
        write_dma_name = transfer_config.get('write_dma')
        read_dma_name = transfer_config.get('read_dma')
        
        # Resolve Data Size
        # Try to get from data config, else defaults
        fmt = data_config.get('format', 'NV12')
        comp = data_config.get('compression', 'Linear')
        # Resolution: try data config first, then try to get from src task output?
        # For simplicity, if not in data, try to query task workload
        width = 0
        height = 0
        src_task = self.scenario.get_task(src_task_id)
        if src_task:
             # Try task workload or output crop size
             if src_task.crop_size:
                 width, height = src_task.crop_size
             else:
                 width = src_task.workload.get('width', 0)
                 height = src_task.workload.get('height', 0)
        
        # If still 0, try data config
        if width == 0:
            # Fallback or error? defaulting to 0 means 0 transfer time
            pass 

        # 2. Write DMA
        if write_dma_name:
            dma = self._get_hw(write_dma_name)
            if isinstance(dma, DMANode):
                size = self._calculate_transfer_size(width, height, fmt, comp, dma)
                
                # Create dynamic task result entry
                dma_task_id = f"dma_w_{src_task_id}_{dst_task_id}"
                start = self.env.now
                dma_time = dma.get_transfer_time(size)
                
                with dma.resource.request() as req:
                    yield req
                    yield self.env.timeout(dma_time)
                
                end = self.env.now
                dma.utilization = 1.0
                power = dma.get_power_consumption(end - start)
                
                self._task_results.append(TaskResult(
                    task_id=dma_task_id, hw_name=dma.name,
                    start_time=start, end_time=end, duration=end-start,
                    power_consumed=power, workload={'size': size, 'fmt': fmt}
                ))

        # 3. Read DMA
        if read_dma_name:
            dma = self._get_hw(read_dma_name)
            if isinstance(dma, DMANode):
                size = self._calculate_transfer_size(width, height, fmt, comp, dma)
                
                dma_task_id = f"dma_r_{src_task_id}_{dst_task_id}"
                start = self.env.now
                dma_time = dma.get_transfer_time(size)
                
                with dma.resource.request() as req:
                    yield req
                    yield self.env.timeout(dma_time)
                    
                end = self.env.now
                dma.utilization = 1.0
                power = dma.get_power_consumption(end - start)
                
                self._task_results.append(TaskResult(
                    task_id=dma_task_id, hw_name=dma.name,
                    start_time=start, end_time=end, duration=end-start,
                    power_consumed=power, workload={'size': size, 'fmt': fmt}
                ))

    def _run_task_process(self, task: Task) -> Generator:
        """
        SimPy process for executing a single task.

        Handles M2M and OTF dependencies.
        """
        task_id = task.task_id
        hw = self._get_hw(task.mapped_hw)

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

        # Wait for M2M predecessors to complete
        for pred_id in m2m_preds:
            if pred_id in self._task_events:
                yield self._task_events[pred_id]
                
            # Check for Explicit DMA Transfer
            edge_transfer = self.scenario.graph.edges[pred_id, task_id].get('transfer')
            if edge_transfer:
                edge_data = self.scenario.graph.edges[pred_id, task_id].get('data', {})
                yield from self._simulate_dma_transfer(pred_id, task_id, edge_transfer, edge_data)

        # For OTF: wait for all OTF group members to be ready
        # (handled by _run_otf_group separately)

        # Record start time
        start_time = self.env.now

        # Calculate processing time
        processing_time = hw.get_processing_time(task.workload)

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
            workload=task.workload
        )
        self._task_results.append(result)

        # Signal task completion
        self._task_events[task_id].succeed()

    def _run_otf_group_process(self, group: List[str]) -> Generator:
        """
        SimPy process for executing OTF-connected tasks synchronously.

        All tasks in the group start together and complete at max(times).
        """
        if not group:
            return

        # Get all tasks in the group
        tasks = [self.scenario.get_task(tid) for tid in group]
        tasks = [t for t in tasks if t is not None]

        if not tasks:
            return

        # Wait for any M2M predecessors of the group
        all_preds = set()
        for task in tasks:
            for pred_id in self.scenario.get_predecessors(task.task_id):
                edge_type = self.scenario.get_edge_type(pred_id, task.task_id)
                if edge_type == ConnectionType.M2M:
                    all_preds.add(pred_id)

        for pred_id in all_preds:
            if pred_id in self._task_events:
                yield self._task_events[pred_id]

        # Record start time (same for all)
        start_time = self.env.now

        # Calculate processing times for all tasks
        processing_times = []
        for task in tasks:
            hw = self._get_hw(task.mapped_hw)
            pt = hw.get_processing_time(task.workload)
            processing_times.append((task, hw, pt))

        # OTF: throughput limited by slowest (bottleneck)
        max_time = max(pt for _, _, pt in processing_times)
        
        # Calculate Latency Staggering
        # To determine start offsets, we conceptually need the path order.
        # Simplification: Assume tasks are listed in pipeline order or just use latency of each relative to start.
        # But 'tasks' list order is arbitrary. dependency graph is truth.
        # For simulation simplicty in 'OTF Group' (which implies synchronized running):
        # We'll use the IP's 'latency' attribute as an offset from the group start.
        
        # 1. Determine base start time (when all inputs are ready)
        base_start = start_time
        
        # 2. Execute
        yield self.env.timeout(max_time)
        
        # 3. Determine base end time
        base_end = self.env.now

        # Store results with Latency Offsets
        for task, hw, individual_time in processing_times:
            # Get IP latency
            latency_us = hw.latency if hasattr(hw, 'latency') else 0.0
            latency_s = latency_us / 1e6
            
            # Apply offset
            # Note: In a real pipeline, the downstream IP starts 'latency' after upstream.
            # But here we treat them as a group. We'll simply shift the Gantt bar by latency.
            # If there are multiple stages, this simple 'hw.latency' lookup might not capture cumulative latency.
            # But for "Sensor -> ISP_FE", Sensor has 0 latency, ISP_FE has 5us.
            # So Sensor: 0~10ms, ISP_FE: 5us~10ms+5us. Correct.
            
            adjusted_start = base_start + latency_s
            adjusted_end = base_end + latency_s
            duration = adjusted_end - adjusted_start
            
            hw.utilization = individual_time / max_time if max_time > 0 else 1.0
            power = hw.get_power_consumption(duration)  # Active power for duration

            result = TaskResult(
                task_id=task.task_id,
                hw_name=hw.name,
                start_time=adjusted_start,
                end_time=adjusted_end,
                duration=duration,
                power_consumed=power,
                workload=task.workload
            )
            self._task_results.append(result)

            # Signal task completion
            self._task_events[task.task_id].succeed()

    def run(self) -> SimulationResults:
        """
        Execute the simulation.

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

        # Create completion events for all tasks
        self._task_events = {
            task.task_id: self.env.event()
            for task in self.scenario.get_tasks()
        }
        self._task_results = []

        # Find OTF groups
        otf_groups = self.scenario.get_otf_groups()
        otf_task_ids = set()
        for group in otf_groups:
            otf_task_ids.update(group)

        # Schedule OTF groups
        for group in otf_groups:
            self.env.process(self._run_otf_group_process(group))

        # Schedule non-OTF tasks
        for task in self.scenario.get_tasks():
            if task.task_id not in otf_task_ids:
                self.env.process(self._run_task_process(task))

        # Run simulation
        self.env.run()

        # Build results
        results = SimulationResults(
            scenario_name=self.scenario.name,
            total_time=self.env.now,
            task_results=self._task_results
        )

        return results

    def run_with_analysis(self) -> Dict[str, Any]:
        """
        Run simulation and apply all analyzers.

        Returns:
            Dictionary with 'results' and analyzer outputs
        """
        results = self.run()

        output = {
            'results': results,
            'analysis': {}
        }

        for analyzer in self.analyzers:
            name = analyzer.__class__.__name__
            output['analysis'][name] = analyzer.analyze(results)

        return output

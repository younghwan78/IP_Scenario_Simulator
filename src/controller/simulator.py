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
    
    def _init_resources(self) -> None:
        """Initialize SimPy resources for all HW nodes."""
        for hw in self.hw_registry.values():
            hw.resource = simpy.Resource(self.env, capacity=1)
    
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
        
        # Simulate processing (all complete at same time)
        yield self.env.timeout(max_time)
        
        # Record end time
        end_time = self.env.now
        
        # Store results for all tasks
        for task, hw, individual_time in processing_times:
            duration = end_time - start_time
            hw.utilization = individual_time / max_time if max_time > 0 else 1.0
            power = hw.get_power_consumption(duration)
            
            result = TaskResult(
                task_id=task.task_id,
                hw_name=hw.name,
                start_time=start_time,
                end_time=end_time,
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
        
        # Validate scenario
        is_valid, errors = self.scenario.validate()
        if not is_valid:
            raise ValueError(f"Invalid scenario: {errors}")
        
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

"""
Scenario Graph for modeling task flows.

Uses NetworkX DiGraph to represent:
- Tasks: Processing units mapped to hardware
- Dependencies: M2M (sequential) or OTF (pipelined) connections
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum

import networkx as nx


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
        ip_mode: Optional IP operating mode (e.g., 'power_saving', 'high_performance')
        crop_size: Optional crop region (width, height) - requires HW crop support
    """
    task_id: str
    mapped_hw: str
    workload: Dict[str, Any] = field(default_factory=dict)
    ip_mode: Optional[str] = None
    crop_size: Optional[Tuple[int, int]] = None
    
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


@dataclass
class Dependency:
    """
    Represents a dependency edge between tasks.
    
    Attributes:
        src: Source task ID
        dst: Destination task ID
        conn_type: Connection type (M2M or OTF)
        buffer_size: Optional buffer size for M2M connections
    """
    src: str
    dst: str
    conn_type: ConnectionType = ConnectionType.M2M
    buffer_size: Optional[int] = None


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
    
    def add_task(self, task_id: str, mapped_hw: str, 
                 workload: Optional[Dict[str, Any]] = None,
                 ip_mode: Optional[str] = None,
                 crop_size: Optional[Tuple[int, int]] = None,
                 **kwargs) -> 'ScenarioGraph':
        """
        Add a task to the scenario.
        
        Args:
            task_id: Unique task identifier
            mapped_hw: Hardware node name to execute on
            workload: Workload parameters dict
            ip_mode: Optional IP mode (e.g., 'power_saving', 'high_performance')
            crop_size: Optional crop output size (width, height)
            **kwargs: Additional workload parameters (width, height, pixels, etc.)
            
        Returns:
            self for method chaining
        """
        if workload is None:
            workload = {}
        workload.update(kwargs)
        
        task = Task(
            task_id=task_id, 
            mapped_hw=mapped_hw, 
            workload=workload,
            ip_mode=ip_mode,
            crop_size=crop_size
        )
        self._tasks[task_id] = task
        self.graph.add_node(task_id, task=task)
        return self
    
    def add_dependency(self, src: str, dst: str, 
                       conn_type: str | ConnectionType = ConnectionType.M2M,
                       buffer_size: Optional[int] = None) -> 'ScenarioGraph':
        """
        Add a dependency between tasks.
        
        Args:
            src: Source task ID
            dst: Destination task ID
            conn_type: 'M2M' or 'OTF' or ConnectionType enum
            buffer_size: Optional buffer size for M2M
            
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
        
        self.graph.add_edge(
            src, dst,
            conn_type=conn_type,
            buffer_size=buffer_size
        )
        return self
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        return self._tasks.get(task_id)
    
    def get_tasks(self) -> List[Task]:
        """Get all tasks in the scenario."""
        return list(self._tasks.values())
    
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

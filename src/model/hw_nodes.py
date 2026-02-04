"""
Hardware Node classes for SoC Multimedia Architecture Simulator.

Provides base classes for modeling hardware components:
- HWNode: Base class with extensible attributes
- ExternalNode: External module (Sensor, PHY) excluded from timing simulation
- IPNode: Pixel-based processing (ISP, Codec, DPU)
- DMANode: Memory access with Multiple Outstanding support
- ProcessorNode: Cycle-based processing (CPU, DSP, NPU)
- MemoryNode: Memory/DRAM modeling
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import simpy


@dataclass
class HWNode(ABC):
    """
    Base class for all hardware nodes.
    
    Attributes:
        name: Component identifier
        clock_freq: Operating frequency in Hz
        power_static: Leakage power in mW
        power_dynamic: Active power coefficient in mW
        utilization: Current usage (0.0 ~ 1.0)
        extra_attrs: Extensible attributes dictionary
    """
    name: str
    clock_freq: float = 1e9  # 1 GHz default
    power_static: float = 0.0
    power_dynamic: float = 0.0
    utilization: float = 0.0
    extra_attrs: Dict[str, Any] = field(default_factory=dict)
    
    # SimPy resource for contention modeling (set during simulation)
    _resource: Optional['simpy.Resource'] = field(default=None, repr=False)
    
    def get_attr(self, key: str, default: Any = None) -> Any:
        """Get an extensible attribute by key."""
        return self.extra_attrs.get(key, default)
    
    def set_attr(self, key: str, value: Any) -> None:
        """Set an extensible attribute."""
        self.extra_attrs[key] = value
    
    @abstractmethod
    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """
        Calculate processing time for given workload.
        
        Args:
            workload: Dictionary containing workload parameters
            
        Returns:
            Processing time in seconds
        """
        pass
    
    def get_power_consumption(self, duration: float) -> float:
        """
        Calculate power consumption for given duration.
        
        Args:
            duration: Processing duration in seconds
            
        Returns:
            Energy consumed in mJ
        """
        active_power = self.power_static + (self.power_dynamic * self.utilization)
        return active_power * duration  # mW * s = mJ (assuming continuous operation)
    
    @property
    def resource(self) -> Optional['simpy.Resource']:
        """Get SimPy resource for contention modeling."""
        return self._resource
    
    @resource.setter
    def resource(self, res: 'simpy.Resource') -> None:
        """Set SimPy resource."""
        self._resource = res


@dataclass
class ExternalNode(HWNode):
    """
    External Node for SoC external interfaces (Sensor, PHY, etc.).
    
    External modules provide input to the SoC but are not part of the
    SoC itself. They are excluded from timing/performance calculations.
    
    Attributes:
        frame_width: Frame width in pixels
        frame_height: Frame height in pixels
        fps: Frames per second
        sensor_mode: Sensor operating mode string
        is_external: Always True for external nodes
    """
    frame_width: int = 3840
    frame_height: int = 2160
    fps: float = 30.0
    sensor_mode: str = "4K_30fps"
    is_external: bool = True
    
    @property
    def frame_size(self) -> int:
        """Total pixels per frame."""
        return self.frame_width * self.frame_height
    
    @property
    def frame_interval(self) -> float:
        """Time interval between frames in seconds."""
        if self.fps > 0:
            return 1.0 / self.fps
        return 0.0
    
    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """
        External nodes have zero processing time within SoC simulation.
        
        The actual timing comes from frame_interval (1/fps) if needed
        for multi-frame simulation.
        
        Returns:
            Always 0.0 (excluded from SoC timing calculation)
        """
        return 0.0
    
    def get_frame_timing(self) -> Dict[str, float]:
        """Get timing information for external interface."""
        return {
            'frame_interval_ms': self.frame_interval * 1000,
            'fps': self.fps,
            'pixels_per_frame': self.frame_size,
            'throughput_mpps': self.frame_size * self.fps / 1e6  # Megapixels per second
        }


@dataclass
class IPNode(HWNode):
    """
    IP Node for pixel-based processing (ISP, Codec, DPU).
    
    Clock is set at IP level and inherited by child modules.
    
    Attributes:
        ppc: Pixels Per Clock
        efficiency: Processing efficiency (0.0 ~ 1.0)
        modules: List of child modules (optional)
    """
    ppc: float = 1.0
    efficiency: float = 1.0
    modules: List[Any] = field(default_factory=list)  # List[Module]
    
    def add_module(self, module: 'Module') -> 'IPNode':
        """
        Add a child module to this IP.
        The module will inherit clock_freq from this IP.
        
        Args:
            module: Module instance to add
            
        Returns:
            self for method chaining
        """
        module.parent_ip = self
        self.modules.append(module)
        return self
    
    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """
        Calculate processing time for pixel workload.
        
        Formula: pixels / (clock_freq * ppc * efficiency)
        
        Args:
            workload: Dict with 'pixels' key
            
        Returns:
            Processing time in seconds
        """
        pixels = workload.get('pixels', 0)
        if pixels <= 0 or self.clock_freq <= 0:
            return 0.0
        return pixels / (self.clock_freq * self.ppc * self.efficiency)
    
    def get_module(self, name: str) -> Optional['Module']:
        """Get a module by name."""
        for module in self.modules:
            if module.name == name:
                return module
        return None


@dataclass
class DMANode(HWNode):
    """
    DMA Node for memory access with advanced attributes.
    
    Supports Multiple Outstanding (MO) and other DMA-specific parameters.
    
    Attributes:
        bandwidth: Maximum bandwidth in bytes/second
        multiple_outstanding: Number of outstanding transactions (MO)
        burst_length: Burst length in bytes
        latency: Base latency in seconds
    """
    bandwidth: float = 25.6e9  # 25.6 GB/s default
    multiple_outstanding: int = 16
    burst_length: int = 256
    latency: float = 0.0
    
    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """
        Calculate transfer time for data workload.
        
        Considers bandwidth and MO for effective throughput.
        
        Args:
            workload: Dict with 'data_size' key (in bytes)
            
        Returns:
            Transfer time in seconds
        """
        data_size = workload.get('data_size', 0)
        if data_size <= 0 or self.bandwidth <= 0:
            return 0.0
        
        # Effective bandwidth considering MO and burst efficiency
        effective_bw = self.bandwidth * min(1.0, self.multiple_outstanding / 16.0)
        transfer_time = data_size / effective_bw
        
        return self.latency + transfer_time
    
    def get_transfer_time(self, data_size: int) -> float:
        """Convenience method for transfer time calculation."""
        return self.get_processing_time({'data_size': data_size})


@dataclass
class ProcessorNode(HWNode):
    """
    Processor Node for cycle-based processing (CPU, DSP, NPU).
    
    Attributes:
        cycles_per_op: Cycles required per operation
        num_cores: Number of processing cores
    """
    cycles_per_op: float = 1.0
    num_cores: int = 1
    
    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """
        Calculate processing time for operation workload.
        
        Formula: (num_ops * cycles_per_op) / (clock_freq * num_cores)
        
        Args:
            workload: Dict with 'ops' key (number of operations)
            
        Returns:
            Processing time in seconds
        """
        num_ops = workload.get('ops', 0)
        if num_ops <= 0 or self.clock_freq <= 0:
            return 0.0
        return (num_ops * self.cycles_per_op) / (self.clock_freq * self.num_cores)


@dataclass
class MemoryNode(HWNode):
    """
    Memory Node for DRAM/SRAM modeling.
    
    Attributes:
        bandwidth: Memory bandwidth in bytes/second
        capacity: Memory capacity in bytes
        access_latency: Access latency in seconds
    """
    bandwidth: float = 51.2e9  # 51.2 GB/s default (LPDDR5)
    capacity: int = 8 * 1024 * 1024 * 1024  # 8 GB default
    access_latency: float = 100e-9  # 100ns default
    
    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """
        Calculate memory access time.
        
        Args:
            workload: Dict with 'data_size' key (in bytes)
            
        Returns:
            Access time in seconds
        """
        data_size = workload.get('data_size', 0)
        if data_size <= 0 or self.bandwidth <= 0:
            return 0.0
        return self.access_latency + (data_size / self.bandwidth)


# Forward reference for Module type
from .modules import Module

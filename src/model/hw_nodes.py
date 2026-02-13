"""
Hardware Node classes for SoC Multimedia Architecture Simulator.

Provides base classes for modeling hardware components:
- HWNode: Base class with extensible attributes
- ExternalNode: External module (Sensor, PHY) excluded from timing simulation
- IPNode: Pixel-based processing (ISP, Codec, DPU)
- ProcessorNode: Cycle-based processing (CPU, DSP, NPU)
- MemoryNode: Memory/DRAM modeling
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

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
    Base class for SoC external interfaces (Sensor, Display, PHY, etc.).

    External modules provide input to or receive output from the SoC
    but are not part of the SoC itself. They are excluded from
    timing/performance calculations.

    Attributes:
        frame_width: Frame width in pixels
        frame_height: Frame height in pixels
        fps: Frames per second
        is_external: Always True for external nodes
    """
    frame_width: int = 3840
    frame_height: int = 2160
    fps: float = 30.0
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
class SensorNode(ExternalNode):
    """
    Sensor Node for camera/image sensor interfaces.

    Includes vValid/vBlank timing for accurate frame timing simulation.
    Sensor outputs data during vValid period and is idle during vBlank.

    Attributes:
        supported_sensor_modes: List of supported sensor modes (HW capability)
        sensor_mode: Sensor operating mode string (e.g., "4K_30fps") - set by scenario
        v_valid_time: Vertical valid time in seconds (pixel data transfer period)
                      None = auto (equals frame_interval, i.e., no vBlank)
    """
    supported_sensor_modes: List[str] = field(default_factory=list)
    sensor_mode: str = ""
    v_valid_time: Optional[float] = None

    @property
    def effective_v_valid_time(self) -> float:
        """
        Get effective vValid time.

        If v_valid_time is not set, defaults to frame_interval (no vBlank).
        """
        if self.v_valid_time is not None:
            return self.v_valid_time
        return self.frame_interval

    @property
    def v_blank_time(self) -> float:
        """Calculate vBlank time from frame interval and vValid."""
        return max(0.0, self.frame_interval - self.effective_v_valid_time)

    def get_required_throughput(self) -> float:
        """
        Get required throughput (pixels/sec) to process within vValid time.

        OTF-connected IPs must be able to process at this rate.

        Returns:
            Required throughput in pixels per second
        """
        if self.effective_v_valid_time <= 0:
            return float('inf')
        return self.frame_size / self.effective_v_valid_time

    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """
        Sensor processing time equals vValid time.

        Sensor outputs pixel data during the vValid period. OTF-connected
        IPs are bound by this data delivery rate - even if their clock is
        fast enough for quicker processing, they must wait for sensor data.

        Returns:
            effective_v_valid_time in seconds
        """
        return self.effective_v_valid_time

    def get_frame_timing(self) -> Dict[str, float]:
        """Get detailed timing information for sensor interface."""
        return {
            'frame_interval_ms': self.frame_interval * 1000,
            'v_valid_time_ms': self.effective_v_valid_time * 1000,
            'v_blank_time_ms': self.v_blank_time * 1000,
            'fps': self.fps,
            'pixels_per_frame': self.frame_size,
            'required_throughput_mpps': self.get_required_throughput() / 1e6,
        }


@dataclass
class DisplayNode(ExternalNode):
    """
    Display Node for display panel interfaces.

    Includes display-specific timing parameters such as blanking intervals.

    Attributes:
        display_mode: Display operating mode (e.g., "FHD_60Hz")
        h_total: Horizontal total pixels (active + blanking)
        v_total: Vertical total lines (active + blanking)
    """
    display_mode: str = "FHD_60Hz"
    h_total: Optional[int] = None
    v_total: Optional[int] = None

    @property
    def pixel_clock(self) -> float:
        """
        Calculate pixel clock in Hz.

        Uses h_total/v_total if set, otherwise uses active resolution.
        """
        h = self.h_total if self.h_total else self.frame_width
        v = self.v_total if self.v_total else self.frame_height
        return h * v * self.fps

    @property
    def h_blank(self) -> int:
        """Horizontal blanking pixels."""
        if self.h_total:
            return max(0, self.h_total - self.frame_width)
        return 0

    @property
    def v_blank(self) -> int:
        """Vertical blanking lines."""
        if self.v_total:
            return max(0, self.v_total - self.frame_height)
        return 0

    def get_frame_timing(self) -> Dict[str, float]:
        """Get timing information for display interface."""
        return {
            'frame_interval_ms': self.frame_interval * 1000,
            'fps': self.fps,
            'pixels_per_frame': self.frame_size,
            'pixel_clock_mhz': self.pixel_clock / 1e6,
            'h_blank': self.h_blank,
            'v_blank': self.v_blank,
        }


@dataclass
class IPNode(HWNode):
    """
    IP Node for pixel-based processing (ISP, Codec, DPU).

    Clock is set at IP level and inherited by child modules.

    Attributes:
        ppc: Pixels Per Clock
        efficiency: Processing efficiency (0.0 ~ 1.0)
        max_clock: Maximum clock frequency (for exploration)
        clock_table: Available clock frequencies
        min_size: Minimum supported resolution (width, height)
        max_size: Maximum supported resolution (width, height)
        modules: List of child modules (optional)
        supported_modes: List of supported IP modes (e.g., ['default', 'power_saving'])
        supports_crop: Whether this IP supports crop functionality
        supports_scale: Whether this IP supports scaling functionality
    """
    ppc: float = 1.0
    efficiency: float = 1.0
    max_clock: Optional[float] = None
    clock_table: List[float] = field(default_factory=list)
    min_size: Tuple[int, int] = (1, 1)
    max_size: Tuple[int, int] = (65535, 65535)
    modules: List[Any] = field(default_factory=list)  # List[Module]
    supported_modes: List[str] = field(default_factory=lambda: ['default'])
    supports_crop: bool = False
    supports_scale: bool = False
    
    # Latency for OTF pipeline (microseconds)
    latency: float = 0.0

    # Domain/hierarchy grouping
    ip_group: str = ""           # HW block group (for view/diagram)
    hierarchy_group: str = ""    # Hierarchy-level diagram grouping

    # Intra-IP module connectivity
    module_edges: List[Tuple[str, str]] = field(default_factory=list)

    # Clock optimization results
    required_freq: float = 0.0  # Calculated required frequency
    target_freq: float = 0.0    # Actual configured frequency

    # ── CSV-based attributes (set by HWResolver) ──────────────
    unit_power: float = 0.0      # mW/MP@30fps (from info.csv)
    idc: float = 0.0             # idle power coefficient (from info.csv)
    vdd: str = ""                # voltage domain (from info.csv)
    dvfs_group: str = ""         # DVFS table reference (from info.csv DVFS column)
    active_mode: str = "Normal"  # current operating mode

    # Resolved values (set by HWResolver)
    set_clock: float = 0.0       # MHz, actual clock from DVFS table
    set_voltage: float = 0.0     # mV, final voltage after VDD alignment
    required_clock: float = 0.0  # MHz, required clock with sw_margin
    required_voltage: float = 0.0  # mV
    dvfs_level: int = -1         # selected DVFS level

    # Strategy callbacks for extensible calculation
    # See hw_resolver.py docstring for extension examples
    _power_calculator: Optional[Any] = field(default=None, repr=False)
    _runtime_calculator: Optional[Any] = field(default=None, repr=False)
    _bw_calculator: Optional[Any] = field(default=None, repr=False)

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

        When CSV data is loaded (set_clock > 0):
            pixels / (set_clock_hz * ppc)
        Fallback (legacy):
            pixels / (clock_freq * ppc * efficiency)

        Custom runtime calculation can be set via _runtime_calculator.

        Args:
            workload: Dict with 'pixels' key or 'width'/'height' keys

        Returns:
            Processing time in seconds
        """
        # Custom calculator override
        if self._runtime_calculator is not None:
            return self._runtime_calculator(self, workload)

        # Get pixels from workload
        pixels = workload.get('pixels', 0)
        if pixels <= 0:
            width = workload.get('width', 0)
            height = workload.get('height', 0)
            pixels = width * height

        if pixels <= 0:
            return 0.0

        # H-blank margin: HW_TIME = base_time * (1 + h_blank_margin)
        h_blank_margin = workload.get('h_blank_margin', 0.05)

        # CSV-based: use set_clock (MHz → Hz)
        if self.set_clock > 0:
            clock_hz = self.set_clock * 1e6
            if clock_hz <= 0 or self.ppc <= 0:
                return 0.0
            base_time = pixels / (clock_hz * self.ppc)
            return base_time * (1 + h_blank_margin)

        # Legacy fallback
        if self.clock_freq <= 0:
            return 0.0
        base_time = pixels / (self.clock_freq * self.ppc * self.efficiency)
        return base_time * (1 + h_blank_margin)

    def get_power_consumption(self, duration: float) -> float:
        """
        Calculate power consumption (energy) for given duration.

        When CSV data is loaded (unit_power > 0):
            Uses _power_calculator or default CSV formula.
        Fallback (legacy):
            (power_static + power_dynamic * utilization) * duration

        Args:
            duration: Processing duration in seconds

        Returns:
            Energy consumed in mJ
        """
        # Custom calculator override
        if self._power_calculator is not None:
            return self._power_calculator(self, duration)

        # CSV-based power (simple estimate using unit_power * clock * duration)
        if self.unit_power > 0 and self.set_clock > 0:
            # Approximate: unit_power [mW/MP@30fps] is already factored in
            # by HWResolver via ResolvedIPConfig.get_active_power()
            # Here we just use active_power_mW * duration_s = energy_mJ
            # For accurate power, use HWResolver.get_exploration_report()
            from .hw_resolver import REFERENCE_VOLTAGE_MV, REFERENCE_FPS
            v_scale = (self.set_voltage / REFERENCE_VOLTAGE_MV) ** 2 if self.set_voltage > 0 else 1.0
            active_mw = self.unit_power * self.set_clock * v_scale  # approximate
            idle_mw = self.idc * v_scale if self.idc > 0 else 0.0
            return (active_mw + idle_mw) * duration

        # Legacy fallback
        active_power = self.power_static + (self.power_dynamic * self.utilization)
        return active_power * duration

    def get_module(self, name: str) -> Optional['Module']:
        """Get a module by name."""
        for module in self.modules:
            if module.name == name:
                return module
        return None





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

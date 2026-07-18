"""
Module classes for IP internal components.

Modules represent functional units within an IP that can:
- Inherit clock frequency from parent IP
- Transform input/output sizes (Scaler, Crop)
- Process specific workloads
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .hw_nodes import IPNode


@dataclass
class Module(ABC):
    """
    Base class for IP internal modules.

    Modules inherit clock_freq from their parent IP.

    Attributes:
        name: Module identifier
        parent_ip: Reference to parent IPNode (for clock inheritance)
        input_size: Input dimensions (width, height)
        output_size: Output dimensions (width, height) - may differ from input
        ppc: Pixels per clock for this module
        efficiency: Processing efficiency (0.0 ~ 1.0)
    """
    name: str
    parent_ip: Optional['IPNode'] = field(default=None, repr=False)
    input_size: Tuple[int, int] = (0, 0)
    output_size: Tuple[int, int] = (0, 0)
    ppc: float = 1.0
    efficiency: float = 1.0

    def get_clock_freq(self) -> float:
        """
        Get clock frequency from parent IP.

        Returns:
            Clock frequency in Hz, or 1 GHz if no parent
        """
        if self.parent_ip is not None:
            return self.parent_ip.clock_freq
        return 1e9  # Default 1 GHz

    @abstractmethod
    def calculate_output_size(self, input_size: Tuple[int, int]) -> Tuple[int, int]:
        """
        Calculate output size based on input size.

        Args:
            input_size: (width, height) tuple

        Returns:
            (output_width, output_height) tuple
        """
        pass

    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """
        Calculate processing time for given workload.

        Default implementation uses pixel count and module's ppc.

        Args:
            workload: Dictionary with 'pixels' or input dimensions

        Returns:
            Processing time in seconds
        """
        pixels = workload.get('pixels', 0)
        if pixels <= 0:
            # Try to calculate from input_size if available
            w, h = workload.get('input_size', self.input_size)
            pixels = w * h

        if pixels <= 0:
            return 0.0

        clock = self.get_clock_freq()
        if clock <= 0:
            return 0.0

        return pixels / (clock * self.ppc * self.efficiency)

    def set_input_size(self, width: int, height: int) -> 'Module':
        """
        Set input size and auto-calculate output size.

        Args:
            width: Input width in pixels
            height: Input height in pixels

        Returns:
            self for method chaining
        """
        self.input_size = (width, height)
        self.output_size = self.calculate_output_size(self.input_size)
        return self

    def get_input_pixels(self) -> int:
        """Get total input pixel count."""
        return self.input_size[0] * self.input_size[1]

    def get_output_pixels(self) -> int:
        """Get total output pixel count."""
        return self.output_size[0] * self.output_size[1]


@dataclass
class ScalerModule(Module):
    """
    Scaler module for image resizing.

    Supports arbitrary scale factors for width and height.

    Attributes:
        scale_factor: (x_scale, y_scale) tuple - can be set via set_sizes()
        min_scale: Minimum scale ratio constraint (HW capability)
        max_scale: Maximum scale ratio constraint (HW capability)
    """
    scale_factor: Tuple[float, float] = (1.0, 1.0)
    min_scale: Tuple[float, float] = (0.0625, 0.0625)  # 1/16x
    max_scale: Tuple[float, float] = (16.0, 16.0)       # 16x

    def set_sizes(self, input_size: Tuple[int, int],
                  output_size: Tuple[int, int]) -> 'ScalerModule':
        """
        Set input/output size and auto-calculate scale_factor.

        Args:
            input_size: (width, height) input dimensions
            output_size: (width, height) output dimensions

        Returns:
            self for method chaining
        """
        self.input_size = input_size
        self.output_size = output_size

        # Calculate scale factor from sizes
        scale_x = output_size[0] / input_size[0] if input_size[0] > 0 else 1.0
        scale_y = output_size[1] / input_size[1] if input_size[1] > 0 else 1.0
        self.scale_factor = (scale_x, scale_y)

        return self

    def calculate_output_size(self, input_size: Tuple[int, int]) -> Tuple[int, int]:
        """
        Calculate scaled output size.

        Args:
            input_size: (width, height) tuple

        Returns:
            Scaled (width, height) tuple
        """
        in_w, in_h = input_size
        out_w = int(in_w * self.scale_factor[0])
        out_h = int(in_h * self.scale_factor[1])
        return (max(1, out_w), max(1, out_h))

    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """
        Calculate processing time considering both input and output pixels.

        Scaler processing time is typically based on output pixels.
        Pure calculation — module state (input/output size) is not mutated.
        """
        # Derive output pixels from workload input size if provided
        if 'input_size' in workload:
            out_w, out_h = self.calculate_output_size(tuple(workload['input_size']))
            output_pixels = out_w * out_h
        else:
            output_pixels = self.get_output_pixels()
        if output_pixels <= 0:
            output_pixels = workload.get('pixels', 0)

        if output_pixels <= 0:
            return 0.0

        clock = self.get_clock_freq()
        if clock <= 0:
            return 0.0

        return output_pixels / (clock * self.ppc * self.efficiency)


@dataclass
class CropModule(Module):
    """
    Crop module for extracting image regions.

    Attributes:
        crop_region: (x, y, width, height) defining the crop area
    """
    crop_region: Tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h

    def calculate_output_size(self, input_size: Tuple[int, int]) -> Tuple[int, int]:
        """
        Calculate cropped output size.

        Output size is limited by crop_region and input boundaries.

        Args:
            input_size: (width, height) tuple

        Returns:
            Cropped (width, height) tuple
        """
        in_w, in_h = input_size
        x, y, crop_w, crop_h = self.crop_region

        # Ensure crop doesn't exceed input bounds
        actual_w = min(crop_w, in_w - x) if x < in_w else 0
        actual_h = min(crop_h, in_h - y) if y < in_h else 0

        return (max(0, actual_w), max(0, actual_h))

    def set_crop_region(self, x: int, y: int, width: int, height: int) -> 'CropModule':
        """
        Set crop region and recalculate output size.

        Returns:
            self for method chaining
        """
        self.crop_region = (x, y, width, height)
        if self.input_size != (0, 0):
            self.output_size = self.calculate_output_size(self.input_size)
        return self


@dataclass
class GenericModule(Module):
    """
    Generic processing module for custom operations.

    Uses standard pixel-based processing time calculation.
    """

    def calculate_output_size(self, input_size: Tuple[int, int]) -> Tuple[int, int]:
        """
        Generic modules don't change size by default.

        Args:
            input_size: (width, height) tuple

        Returns:
            Same as input size
        """
        return input_size


@dataclass
class BypassModule(Module):
    """
    Bypass module that passes through without processing.

    Used for modeling pass-through paths in IP blocks.
    """

    def calculate_output_size(self, input_size: Tuple[int, int]) -> Tuple[int, int]:
        """Output equals input (pass-through)."""
        return input_size

    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        """Bypass has zero processing time."""
        return 0.0


@dataclass
class DMAModule(Module):
    """
    DMA module for intra-IP memory access.

    Attributes:
        max_bandwidth: Maximum bandwidth in bytes/second (Capability)
        direction: 'read' or 'write'
        multiple_outstanding: Number of outstanding transactions (MO)
        supported_compressions: List of supported compression modes
        compression_ratios: Dict mapping compression mode to size ratio
    """
    max_bandwidth: float = 25.6e9
    direction: str = 'read'
    multiple_outstanding: int = 16
    supported_compressions: List[str] = field(default_factory=list)
    compression_ratios: Dict[str, float] = field(default_factory=dict)

    # SimPy resource for contention modeling
    _resource: Optional[Any] = field(default=None, repr=False) # valid only during simulation

    @property
    def resource(self) -> Optional[Any]:
        return self._resource

    @resource.setter
    def resource(self, res: Any) -> None:
        self._resource = res

    def calculate_output_size(self, input_size: Tuple[int, int]) -> Tuple[int, int]:
        """DMA doesn't change resolution."""
        return input_size

    def get_transfer_time(self, data_size: int) -> float:
        """
        Calculate transfer time for data workload.
        Considers bandwidth and MO for effective throughput.
        """
        if data_size <= 0 or self.max_bandwidth <= 0:
            return 0.0

        # TODO: Simplified linear model — not realistic.
        #   Actual DMA efficiency depends on MO, burst length, DRAM latency,
        #   bus contention, etc. Replace with table-based or analytical model
        #   when implementing module-level simulation.
        efficiency = min(1.0, self.multiple_outstanding / 16.0)
        effective_bw = self.max_bandwidth * efficiency
        
        return data_size / effective_bw

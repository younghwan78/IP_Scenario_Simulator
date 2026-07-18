"""
Token-based flow control for multimedia pipeline simulation.

Implements:
- FrameToken: Data unit flowing through the pipeline
- TokenQueue: Per-input port buffer (SimPy Store wrapper)
- TokenJoin: Multi-input synchronization with AND/OR/Window policies
- TokenFork: Multi-output distribution with token copying
- TokenTransform: Size transformation for Scaler/Crop nodes
"""

from __future__ import annotations
import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import simpy


# ============================================================
# Queue Capacity Defaults (node-specific override supported)
# ============================================================
DEFAULT_QUEUE_CAPACITY = 32  # Generous default for general use
DMA_QUEUE_CAPACITY = 64      # DMA nodes with memory constraints


class JoinPolicy(Enum):
    """Multi-Input Join policies."""
    AND_JOIN = "and"        # All inputs must arrive (default)
    OR_JOIN = "or"          # Any single input triggers processing
    WINDOW_BASED = "window"  # N tokens accumulate before processing


@dataclass
class FrameToken:
    """
    Represents a frame data unit flowing through the pipeline.
    
    Attributes:
        frame_id: Unique frame identifier
        timestamp: Creation/processing timestamp
        width: Frame width in pixels
        height: Frame height in pixels
        format: Pixel format (NV12, RGB888, etc.)
        metadata: Extensible metadata dictionary
            Reserved keys: 'compression', 'bit_depth', 'color_space', 'roi'
            Custom extensions: metadata['custom_xxx'] = value
    """
    frame_id: int
    timestamp: float
    width: int
    height: int
    format: str = "NV12"
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def copy(self, **overrides) -> 'FrameToken':
        """
        Create a deep copy of this token (for Fork distribution).
        
        Args:
            **overrides: Attributes to override in the copy
            
        Returns:
            New independent FrameToken instance
        """
        new_token = copy.deepcopy(self)
        for key, value in overrides.items():
            if hasattr(new_token, key):
                setattr(new_token, key, value)
            else:
                new_token.metadata[key] = value
        return new_token
    
    def with_size(self, width: int, height: int) -> 'FrameToken':
        """
        Create a copy with new dimensions (for Scaler/Crop output).
        
        Args:
            width: New width
            height: New height
            
        Returns:
            New token with updated size
        """
        return self.copy(width=width, height=height)
    
    @property
    def pixels(self) -> int:
        """Total pixel count."""
        return self.width * self.height


class TokenTransform:
    """
    Static methods for token transformation (Scaler, Crop, etc.).
    
    Transformation history is recorded in token metadata for traceability.
    """
    
    @staticmethod
    def scale(token: FrameToken, scale_x: float, scale_y: float) -> FrameToken:
        """
        Apply scaling transformation.
        
        Args:
            token: Input token
            scale_x: Horizontal scale factor
            scale_y: Vertical scale factor
            
        Returns:
            New token with scaled dimensions
        """
        new_w = int(token.width * scale_x)
        new_h = int(token.height * scale_y)
        new_metadata = {**token.metadata, 'scaled_from': (token.width, token.height)}
        return token.copy(width=new_w, height=new_h, metadata=new_metadata)
    
    @staticmethod
    def crop(token: FrameToken, x: int, y: int, w: int, h: int) -> FrameToken:
        """
        Apply crop transformation.
        
        Args:
            token: Input token
            x, y: Top-left corner of crop region
            w, h: Width and height of crop region
            
        Returns:
            New token with cropped dimensions
        """
        new_metadata = {**token.metadata, 'crop_roi': (x, y, w, h)}
        return token.copy(width=w, height=h, metadata=new_metadata)
    
    @staticmethod
    def passthrough(token: FrameToken) -> FrameToken:
        """Identity transform - just copies the token."""
        return token.copy()


@dataclass
class TokenQueue:
    """
    Single input port token queue (SimPy Store wrapper).
    
    Each input port has its own independent queue (Principle #1).
    
    Attributes:
        name: Port name (e.g., "input_main", "input_aux")
        store: SimPy Store for token buffering
        capacity: Maximum tokens in queue
    """
    name: str
    store: 'simpy.Store'
    capacity: int = DEFAULT_QUEUE_CAPACITY
    
    @classmethod
    def create(cls, env: 'simpy.Environment', name: str, 
               capacity: Optional[int] = None) -> 'TokenQueue':
        """
        Factory method to create a TokenQueue.
        
        Args:
            env: SimPy environment
            name: Port name
            capacity: Optional custom capacity (uses DEFAULT if None)
            
        Returns:
            New TokenQueue instance
        """
        import simpy
        cap = capacity if capacity is not None else DEFAULT_QUEUE_CAPACITY
        return cls(name=name, store=simpy.Store(env, capacity=cap), capacity=cap)
    
    def put(self, token: FrameToken) -> Generator:
        """Put a token into the queue (yields if full)."""
        yield self.store.put(token)
    
    def get(self) -> Generator:
        """Get a token from the queue (yields if empty)."""
        return (yield self.store.get())
    
    @property
    def level(self) -> int:
        """Current number of tokens in queue."""
        return len(self.store.items)
    
    @property
    def is_full(self) -> bool:
        """Check if queue is at capacity."""
        return self.level >= self.capacity
    
    @property
    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return self.level == 0


@dataclass
class TokenJoin:
    """
    Multi-input token synchronization (Principle #2).
    
    Implements join policies:
    - AND_JOIN: Wait for all inputs (default)
    - OR_JOIN: Proceed when any input arrives
    - WINDOW_BASED: Collect N tokens before processing
    
    Attributes:
        input_queues: Dict mapping port names to TokenQueues
        policy: Join policy to use
        window_size: Number of tokens for WINDOW_BASED policy
    """
    input_queues: Dict[str, TokenQueue] = field(default_factory=dict)
    policy: JoinPolicy = JoinPolicy.AND_JOIN
    window_size: int = 1
    _env: Optional['simpy.Environment'] = field(default=None, repr=False)
    
    def wait_for_tokens(self) -> Generator[Any, Any, Dict[str, FrameToken]]:
        """
        Wait for tokens according to join policy.
        
        Returns:
            Dict mapping port names to received tokens
        """
        import simpy
        
        if not self.input_queues:
            return {}
        
        tokens: Dict[str, FrameToken] = {}
        
        if self.policy == JoinPolicy.AND_JOIN:
            # Wait for all inputs
            for port_name, queue in self.input_queues.items():
                token = yield queue.store.get()
                tokens[port_name] = token
                
        elif self.policy == JoinPolicy.OR_JOIN:
            # Wait for any single input (first to arrive)
            events = {
                port_name: queue.store.get()
                for port_name, queue in self.input_queues.items()
            }
            # Use simpy.AnyOf to wait for first completion
            if self._env:
                result = yield simpy.AnyOf(self._env, list(events.values()))
                for port_name, event in events.items():
                    if event.triggered and not tokens:
                        # Take the first triggered token
                        tokens[port_name] = event.value
                    elif event.triggered:
                        # Simultaneously triggered but not selected:
                        # return the consumed token to the queue front
                        self.input_queues[port_name].store.items.insert(0, event.value)
                    else:
                        # Cancel pending get so it doesn't silently consume
                        # a token that arrives later (token-loss bug)
                        event.cancel()
            else:
                # Fallback: just get from first queue
                first_port = next(iter(self.input_queues))
                token = yield self.input_queues[first_port].store.get()
                tokens[first_port] = token
                
        elif self.policy == JoinPolicy.WINDOW_BASED:
            # Collect window_size tokens from first queue
            first_port = next(iter(self.input_queues))
            queue = self.input_queues[first_port]
            collected = []
            for _ in range(self.window_size):
                token = yield queue.store.get()
                collected.append(token)
            tokens[first_port] = collected[-1]  # Return last token
            tokens['_window'] = collected  # Store all for processing
        
        return tokens


@dataclass 
class TokenFork:
    """
    Multi-output token distribution (Principle #3).
    
    Distributes tokens to multiple outputs by COPYING (never sharing).
    Supports per-port transformation functions for Scaler/Crop outputs.
    
    Attributes:
        output_queues: Dict mapping port names to destination TokenQueues
        transforms: Optional per-port transformation functions
    """
    output_queues: Dict[str, TokenQueue] = field(default_factory=dict)
    transforms: Dict[str, Callable[[FrameToken], FrameToken]] = field(default_factory=dict)
    
    def distribute(self, token: FrameToken) -> Generator:
        """
        Distribute token to all outputs (with copying).
        
        Each output receives an independent copy of the token.
        Per-port transforms are applied if configured.
        
        Args:
            token: Token to distribute
            
        Yields:
            SimPy put events
        """
        for port_name, queue in self.output_queues.items():
            # Principle #3: Always create a NEW token (never share)
            copied = token.copy()
            
            # Apply per-port transform if configured
            if port_name in self.transforms:
                copied = self.transforms[port_name](copied)
            
            yield queue.store.put(copied)
    
    def add_output(self, port_name: str, queue: TokenQueue, 
                   transform: Optional[Callable[[FrameToken], FrameToken]] = None) -> 'TokenFork':
        """
        Add an output port.
        
        Args:
            port_name: Output port name
            queue: Destination TokenQueue
            transform: Optional transformation function
            
        Returns:
            self for method chaining
        """
        self.output_queues[port_name] = queue
        if transform:
            self.transforms[port_name] = transform
        return self


# ============================================================
# Helper Functions
# ============================================================

def create_source_token(frame_id: int, width: int, height: int, 
                        format: str = "NV12", timestamp: float = 0.0,
                        **metadata) -> FrameToken:
    """
    Create a new source token (for sensor/input nodes).
    
    Args:
        frame_id: Unique frame identifier
        width: Frame width
        height: Frame height
        format: Pixel format
        timestamp: Creation timestamp
        **metadata: Additional metadata
        
    Returns:
        New FrameToken
    """
    return FrameToken(
        frame_id=frame_id,
        timestamp=timestamp,
        width=width,
        height=height,
        format=format,
        metadata=metadata
    )

"""
Tests for token-based flow control in simulation.

Validates the 3 core principles:
1. Separate token queue per input port
2. Explicit join policies (AND/OR/WINDOW)
3. Token copy for fork outputs (no sharing)
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.tokens import (
    FrameToken, TokenQueue, TokenJoin, TokenFork, TokenTransform,
    JoinPolicy, DEFAULT_QUEUE_CAPACITY, DMA_QUEUE_CAPACITY, create_source_token
)
from src.model.scenario import ScenarioGraph, ConnectionType, Task
from src.model.hw_nodes import IPNode
from src.controller.simulator import SoCSimulator


class TestFrameToken:
    """Test FrameToken class."""
    
    def test_token_creation(self):
        """Test basic token creation."""
        token = FrameToken(
            frame_id=1,
            timestamp=0.0,
            width=1920,
            height=1080,
            format="NV12"
        )
        assert token.frame_id == 1
        assert token.width == 1920
        assert token.height == 1080
        assert token.format == "NV12"
        assert token.pixels == 1920 * 1080
    
    def test_token_copy_creates_independent_object(self):
        """Principle #3: Fork creates NEW tokens (not shared)."""
        original = FrameToken(
            frame_id=1,
            timestamp=0.0,
            width=1920,
            height=1080,
            metadata={'key': 'value'}
        )
        
        copied = original.copy()
        
        # Different object IDs (independent)
        assert id(original) != id(copied)
        assert id(original.metadata) != id(copied.metadata)
        
        # Same values
        assert copied.frame_id == original.frame_id
        assert copied.width == original.width
        assert copied.metadata['key'] == 'value'
        
        # Modifying copy doesn't affect original
        copied.metadata['new_key'] = 'new_value'
        assert 'new_key' not in original.metadata
    
    def test_token_copy_with_overrides(self):
        """Test token copy with attribute overrides."""
        original = FrameToken(
            frame_id=1, timestamp=0.0, width=1920, height=1080
        )
        
        resized = original.copy(width=3840, height=2160)
        
        assert resized.width == 3840
        assert resized.height == 2160
        assert resized.frame_id == 1  # Unchanged
    
    def test_token_with_size(self):
        """Test with_size helper for Scaler/Crop."""
        original = FrameToken(
            frame_id=1, timestamp=0.0, width=3840, height=2160
        )
        
        scaled = original.with_size(1920, 1080)
        
        assert scaled.width == 1920
        assert scaled.height == 1080
        assert id(scaled) != id(original)


class TestTokenTransform:
    """Test TokenTransform for Scaler/Crop."""
    
    def test_scale_transform(self):
        """Test scaling transformation."""
        token = FrameToken(
            frame_id=1, timestamp=0.0, width=1920, height=1080
        )
        
        scaled = TokenTransform.scale(token, 2.0, 2.0)
        
        assert scaled.width == 3840
        assert scaled.height == 2160
        assert scaled.metadata['scaled_from'] == (1920, 1080)
    
    def test_crop_transform(self):
        """Test crop transformation."""
        token = FrameToken(
            frame_id=1, timestamp=0.0, width=1920, height=1080
        )
        
        cropped = TokenTransform.crop(token, 100, 100, 640, 480)
        
        assert cropped.width == 640
        assert cropped.height == 480
        assert cropped.metadata['crop_roi'] == (100, 100, 640, 480)


class TestTokenQueue:
    """Test TokenQueue with configurable capacity."""
    
    def test_default_capacity(self):
        """Test default queue capacity is 32."""
        assert DEFAULT_QUEUE_CAPACITY == 32
    
    def test_dma_capacity(self):
        """Test DMA queue capacity is 64."""
        assert DMA_QUEUE_CAPACITY == 64
    
    def test_queue_creation_with_simpy(self):
        """Test TokenQueue creation with SimPy environment."""
        import simpy
        env = simpy.Environment()
        
        queue = TokenQueue.create(env, "input_main")
        
        assert queue.name == "input_main"
        assert queue.capacity == DEFAULT_QUEUE_CAPACITY
        assert queue.is_empty
        assert not queue.is_full
    
    def test_queue_custom_capacity(self):
        """Test queue with custom capacity."""
        import simpy
        env = simpy.Environment()
        
        queue = TokenQueue.create(env, "dma_input", capacity=DMA_QUEUE_CAPACITY)
        
        assert queue.capacity == DMA_QUEUE_CAPACITY


class TestTokenJoin:
    """Test TokenJoin with different policies."""
    
    def test_join_policy_enum(self):
        """Test JoinPolicy enum values."""
        assert JoinPolicy.AND_JOIN.value == "and"
        assert JoinPolicy.OR_JOIN.value == "or"
        assert JoinPolicy.WINDOW_BASED.value == "window"
    
    def test_and_join_requires_all_inputs(self):
        """Principle #2: AND_JOIN waits for all inputs."""
        import simpy
        env = simpy.Environment()
        
        q1 = TokenQueue.create(env, "input_a")
        q2 = TokenQueue.create(env, "input_b")
        
        join = TokenJoin(
            input_queues={"input_a": q1, "input_b": q2},
            policy=JoinPolicy.AND_JOIN,
            _env=env
        )
        
        # Queue both tokens
        token_a = FrameToken(1, 0.0, 1920, 1080)
        token_b = FrameToken(1, 0.0, 1920, 1080)
        
        def producer():
            yield q1.store.put(token_a)
            yield q2.store.put(token_b)
        
        def consumer():
            tokens = yield from join.wait_for_tokens()
            assert 'input_a' in tokens
            assert 'input_b' in tokens
            assert len(tokens) == 2
        
        env.process(producer())
        env.process(consumer())
        env.run()


class TestTokenFork:
    """Test TokenFork for multi-output distribution."""
    
    def test_fork_creates_copies(self):
        """Principle #3: Fork creates NEW tokens for each output."""
        import simpy
        env = simpy.Environment()
        
        q1 = TokenQueue.create(env, "to_encoder")
        q2 = TokenQueue.create(env, "to_display")
        
        fork = TokenFork(output_queues={"to_encoder": q1, "to_display": q2})
        
        original = FrameToken(1, 0.0, 1920, 1080)
        
        def distribute():
            yield from fork.distribute(original)
        
        env.process(distribute())
        env.run()
        
        # Both queues should have tokens
        assert q1.level == 1
        assert q2.level == 1
        
        # Tokens should be independent copies
        token1 = q1.store.items[0]
        token2 = q2.store.items[0]
        
        assert id(token1) != id(original)
        assert id(token2) != id(original)
        assert id(token1) != id(token2)


class TestScenarioTokenExtensions:
    """Test scenario model token extensions."""
    
    def test_task_join_policy_default(self):
        """Test Task default join policy is AND_JOIN."""
        task = Task(task_id="t1", mapped_hw="ISP")
        assert task.join_policy == JoinPolicy.AND_JOIN
        assert task.window_size == 1
        assert task.input_ports == []
        assert task.output_ports == []
    
    def test_add_task_with_join_policy(self):
        """Test adding task with join policy."""
        scenario = ScenarioGraph("test")
        scenario.add_task(
            "fusion",
            "ISP_Fusion",
            pixels=8294400,
            join_policy=JoinPolicy.AND_JOIN,
            input_ports=["main_sensor", "aux_sensor"]
        )
        
        task = scenario.get_task("fusion")
        assert task.join_policy == JoinPolicy.AND_JOIN
        assert task.input_ports == ["main_sensor", "aux_sensor"]
    
    def test_add_dependency_with_ports(self):
        """Test adding dependency with port specification."""
        scenario = ScenarioGraph("test")
        scenario.add_task("sensor_main", "Sensor", pixels=8294400)
        scenario.add_task("fusion", "ISP_Fusion", pixels=8294400)
        
        scenario.add_dependency(
            "sensor_main", "fusion",
            conn_type="OTF",
            dst_port="main_sensor"
        )
        
        edge = scenario.get_dependency("sensor_main", "fusion")
        assert edge['dst_port'] == "main_sensor"
        assert edge['src_port'] == "output"  # Default


class TestSimulatorTokenInfrastructure:
    """Test SoCSimulator token infrastructure."""
    
    def test_detect_token_mode_single_path(self):
        """Single-path scenario doesn't need token mode."""
        sim = SoCSimulator()
        
        scenario = ScenarioGraph("simple")
        scenario.add_task("t1", "ISP", pixels=1000000)
        scenario.add_task("t2", "VENC", pixels=1000000)
        scenario.add_dependency("t1", "t2", "M2M")
        
        sim.load_scenario(scenario)
        
        assert not sim._detect_token_mode()
    
    def test_detect_token_mode_multi_input(self):
        """Multi-input scenario enables token mode."""
        sim = SoCSimulator()
        
        scenario = ScenarioGraph("multi_input")
        scenario.add_task("sensor_a", "Sensor_A", pixels=1000000)
        scenario.add_task("sensor_b", "Sensor_B", pixels=1000000)
        scenario.add_task("fusion", "ISP_Fusion", pixels=1000000,
                         join_policy=JoinPolicy.AND_JOIN)
        
        scenario.add_dependency("sensor_a", "fusion", "OTF", dst_port="main")
        scenario.add_dependency("sensor_b", "fusion", "OTF", dst_port="aux")
        
        sim.load_scenario(scenario)
        
        assert sim._detect_token_mode()
    
    def test_detect_token_mode_multi_output(self):
        """Multi-output scenario enables token mode."""
        sim = SoCSimulator()
        
        scenario = ScenarioGraph("multi_output")
        scenario.add_task("isp", "ISP", pixels=1000000)
        scenario.add_task("encoder", "VENC", pixels=1000000)
        scenario.add_task("display", "Display", pixels=1000000)
        
        scenario.add_dependency("isp", "encoder", "M2M")
        scenario.add_dependency("isp", "display", "M2M")
        
        sim.load_scenario(scenario)
        
        assert sim._detect_token_mode()


class TestBackwardCompatibility:
    """Ensure token extensions don't break existing functionality."""
    
    def test_simple_pipeline_still_works(self):
        """Original simple pipeline should work unchanged."""
        hw_nodes = [
            IPNode(name="Sensor", clock_freq=600e6, ppc=4),
            IPNode(name="ISP", clock_freq=600e6, ppc=4),
            IPNode(name="VENC", clock_freq=400e6, ppc=1),
        ]
        
        scenario = ScenarioGraph("4K_Recording")
        scenario.add_task("t_sensor", "Sensor", pixels=8294400)
        scenario.add_task("t_isp", "ISP", pixels=8294400)
        scenario.add_task("t_venc", "VENC", pixels=8294400)
        
        scenario.add_dependency("t_sensor", "t_isp", "OTF")
        scenario.add_dependency("t_isp", "t_venc", "M2M")
        
        sim = SoCSimulator()
        for hw in hw_nodes:
            sim.register_hw(hw)
        sim.load_scenario(scenario)
        
        results = sim.run()
        
        assert len(results.task_results) == 3
        assert results.total_time > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

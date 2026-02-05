"""
Unit tests for Explicit DMA Modeling features.
"""

import pytest
import sys
import simpy
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import DMANode, IPNode, MemoryNode
from src.model.scenario import ScenarioGraph, Task, ConnectionType
from src.controller.simulator import SoCSimulator, BPP_MAP


class TestExplicitDMA:
    """Tests for Explicit DMA modeling features."""

    def test_bpp_map(self):
        """Test BPP mapping constants."""
        assert BPP_MAP["NV12"] == 1.5
        assert BPP_MAP["RGB888"] == 3.0
        assert BPP_MAP["RAW10"] == 1.25

    def test_dma_compression_ratio(self):
        """Test size calculation with compression."""
        simulator = SoCSimulator()
        
        # Setup DMA with compression
        dma = DMANode(name="DMA_W", bandwidth=1e9)
        dma.supported_compressions = ["AFBC", "Linear"]
        dma.compression_ratios = {"AFBC": 0.5, "Linear": 1.0}
        
        # 100 pixels, 1 byte/pixel (hypothetically)
        width, height = 10, 10
        
        # Case 1: Linear (Ratio 1.0)
        # BPP_MAP.get(fmt, 1.0) so let's use known fmt like NV12 (1.5)
        # Base size = 10*10 * 1.5 = 150 bytes
        size_linear = simulator._calculate_transfer_size(width, height, "NV12", "Linear", dma)
        assert size_linear == 150
        
        # Case 2: AFBC (Ratio 0.5)
        # Size = 150 * 0.5 = 75 bytes
        size_afbc = simulator._calculate_transfer_size(width, height, "NV12", "AFBC", dma)
        assert size_afbc == 75
        
        # Case 3: Unsupported compression (Default 1.0)
        size_none = simulator._calculate_transfer_size(width, height, "NV12", "None", dma)
        assert size_none == 150

    def test_simulate_dma_transfer(self):
        """Test full DMA transfer simulation (Write->Read)."""
        simulator = SoCSimulator()
        simulator.env = simpy.Environment()
        
        # Setup HW Registry
        dma_w = DMANode(name="DMA_Write", bandwidth=1000) # 1000 bytes/sec
        dma_r = DMANode(name="DMA_Read", bandwidth=1000)
        ip = IPNode(name="ISP", clock_freq=1e6)
        
        simulator.register_hw(dma_w)
        simulator.register_hw(dma_r)
        simulator.register_hw(ip)
        
        # Initialize resources
        simulator.env = simpy.Environment()
        simulator._init_resources()
        
        # Setup Scenario (Mock)
        scenario = ScenarioGraph("Test")
        simulator.scenario = scenario
        
        # Mock Task (src)
        scenario.add_task("t_src", "ISP", width=10, height=10)
        
        # Transfer Config
        transfer_config = {
            'write_dma': 'DMA_Write',
            'read_dma': 'DMA_Read'
        }
        data_config = {
            'format': 'NV12', # 1.5 BPP
            'compression': 'Linear' # 1.0 Ratio
        }
        
        # Base Size = 10*10 * 1.5 = 150 bytes
        # Transfer Time = 150 bytes / 1000 Bps = 0.15 sec
        # Total Time = Write(0.15) + Read(0.15) = 0.3 sec
        
        # Run Generator
        gen = simulator._simulate_dma_transfer("t_src", "t_dst", transfer_config, data_config)
        
        
        # Simple environment usage 
        # simulator.env is already set above
        
        def drive_sim():
            yield from gen
            
        simulator.env.process(drive_sim())
        simulator.env.run()
        
        # Verify Results
        results = simulator._task_results
        assert len(results) == 2
        
        # Check DMA Write
        res_w = next(r for r in results if r.hw_name == "DMA_Write")
        assert res_w.duration == 0.15
        assert res_w.workload['size'] == 150
        
        # Check DMA Read
        res_r = next(r for r in results if r.hw_name == "DMA_Read")
        assert res_r.duration == 0.15
        
        assert 0.29 < (results[1].end_time - results[0].start_time) < 0.31


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

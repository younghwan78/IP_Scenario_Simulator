"""
Unit tests for Explicit DMA Modeling features.
"""

import pytest
import sys
import simpy
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.hw_nodes import IPNode
from src.model.modules import DMAModule
from src.model.scenario import ScenarioGraph
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
        
        # Setup DMA Module
        dma = DMAModule(name="DMA_W", max_bandwidth=1e9)
        dma.supported_compressions = ["AFBC", "Linear"]
        dma.compression_ratios = {"AFBC": 0.5, "Linear": 1.0}
        
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
        
        # Setup HW Registry (IPs with DMA Modules)
        ip_src = IPNode(name="ISP_Src", clock_freq=1e6)
        dma_w = DMAModule(name="DMA_Write", max_bandwidth=1000) # 1000 bytes/sec
        ip_src.add_module(dma_w)
        
        ip_dst = IPNode(name="ISP_Dst", clock_freq=1e6)
        dma_r = DMAModule(name="DMA_Read", max_bandwidth=1000)
        ip_dst.add_module(dma_r)
        
        simulator.register_hw(ip_src)
        simulator.register_hw(ip_dst)
        
        # Initialize resources (important: initializes module resources)
        simulator.env = simpy.Environment()
        simulator._init_resources()
        
        # Setup Scenario (Mock)
        scenario = ScenarioGraph("Test")
        simulator.scenario = scenario
        
        # Mock Tasks
        # Task must be mapped to the IP containing the DMA module
        scenario.add_task("t_src", "ISP_Src", width=10, height=10) 
        scenario.add_task("t_dst", "ISP_Dst", width=10, height=10)
        
        # Transfer Config
        transfer_config = {
            'write_dma': 'DMA_Write', # Module name
            'read_dma': 'DMA_Read'    # Module name
        }
        data_config = {
            'format': 'NV12', # 1.5 BPP
            'compression': 'Linear' # 1.0 Ratio
        }
        
        # Base Size = 10*10 * 1.5 = 150 bytes
        # Transfer Time = 150 bytes / 1000 Bps = 0.15 sec
        # Total Time = Write(0.15) + Read(0.15) = 0.3 sec (Simplified, sequential)
        
        # Run Generator
        gen = simulator._simulate_dma_transfer("t_src", "t_dst", transfer_config, data_config)
        
        def drive_sim():
            yield from gen
            
        simulator.env.process(drive_sim())
        simulator.env.run()
        
        # Verify Results
        results = simulator._task_results
        assert len(results) == 2
        
        # Check DMA Write
        # Name record format: f"{write_dma.name}(Write)"
        res_w = next(r for r in results if "DMA_Write" in r.hw_name)
        assert res_w.duration == 0.15
        assert res_w.workload['size'] == 150
        
        # Check DMA Read
        res_r = next(r for r in results if "DMA_Read" in r.hw_name)
        assert res_r.duration == 0.15
        
        # Check Sequence
        assert res_r.start_time >= res_w.end_time 
        assert 0.29 < (res_r.end_time - res_w.start_time) < 0.31


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

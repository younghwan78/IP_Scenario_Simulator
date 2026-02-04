"""
Main entry point for SoC Multimedia Architecture Simulator.

Usage:
    python main.py [--hw-config PATH] [--scenario-config PATH]
"""

import argparse
import sys
from pathlib import Path

import yaml

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from src.model.hw_nodes import HWNode, IPNode, DMANode, ProcessorNode, MemoryNode, ExternalNode
from src.model.modules import Module, ScalerModule, CropModule, GenericModule
from src.model.scenario import ScenarioGraph, ConnectionType
from src.controller.simulator import SoCSimulator
from src.controller.performance_analyzer import PerformanceAnalyzer
from src.controller.power_analyzer import PowerAnalyzer
from src.controller.timing_analyzer import TimingAnalyzer
from src.view.text_view import TextViewer
from src.view.visualizer import Monitor, Visualizer


def load_hw_config(path: str) -> dict:
    """Load hardware configuration from YAML file."""
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_scenario_config(path: str) -> dict:
    """Load scenario configuration from YAML file."""
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def create_hw_node(config: dict) -> HWNode:
    """Create HWNode from configuration dictionary."""
    node_type = config.get('type', 'IP')
    name = config['name']
    clock = config.get('clock', 1e9)
    power_static = config.get('power_static', 0.0)
    power_dynamic = config.get('power_dynamic', 0.0)
    
    if node_type == 'External':
        # External node (Sensor, PHY) - excluded from SoC timing simulation
        return ExternalNode(
            name=name,
            frame_width=config.get('frame_width', 3840),
            frame_height=config.get('frame_height', 2160),
            fps=config.get('fps', 30.0),
            sensor_mode=config.get('sensor_mode', ''),
            power_static=power_static,
            power_dynamic=power_dynamic
        )
    
    elif node_type == 'IP':
        # Parse supported_modes from config (default to ['default'])
        supported_modes = config.get('supported_modes', ['default'])
        supports_crop = config.get('supports_crop', False)
        
        node = IPNode(
            name=name,
            clock_freq=clock,
            ppc=config.get('ppc', 1.0),
            efficiency=config.get('efficiency', 1.0),
            power_static=power_static,
            power_dynamic=power_dynamic,
            supported_modes=supported_modes,
            supports_crop=supports_crop
        )
        
        # Add modules if present
        for mod_config in config.get('modules', []):
            module = create_module(mod_config)
            node.add_module(module)
        
        return node
    
    elif node_type == 'DMA':
        return DMANode(
            name=name,
            clock_freq=clock,
            bandwidth=config.get('bandwidth', 25.6e9),
            multiple_outstanding=config.get('multiple_outstanding', 16),
            burst_length=config.get('burst_length', 256),
            latency=config.get('latency', 0.0),
            power_static=power_static,
            power_dynamic=power_dynamic
        )
    
    elif node_type == 'Processor':
        return ProcessorNode(
            name=name,
            clock_freq=clock,
            cycles_per_op=config.get('cycles_per_op', 1.0),
            num_cores=config.get('num_cores', 1),
            power_static=power_static,
            power_dynamic=power_dynamic
        )
    
    elif node_type == 'Memory':
        return MemoryNode(
            name=name,
            clock_freq=clock,
            bandwidth=config.get('bandwidth', 51.2e9),
            capacity=config.get('capacity', 8 * 1024**3),
            access_latency=config.get('access_latency', 100e-9),
            power_static=power_static,
            power_dynamic=power_dynamic
        )
    
    else:
        # Default to IPNode
        return IPNode(
            name=name,
            clock_freq=clock,
            ppc=config.get('ppc', 1.0),
            power_static=power_static,
            power_dynamic=power_dynamic
        )


def create_module(config: dict) -> Module:
    """Create Module from configuration dictionary."""
    mod_type = config.get('type', 'Generic')
    name = config['name']
    
    if mod_type == 'Scaler':
        scale = config.get('scale_factor', [1.0, 1.0])
        return ScalerModule(
            name=name,
            scale_factor=tuple(scale),
            ppc=config.get('ppc', 1.0),
            efficiency=config.get('efficiency', 1.0)
        )
    
    elif mod_type == 'Crop':
        region = config.get('crop_region', [0, 0, 0, 0])
        return CropModule(
            name=name,
            crop_region=tuple(region),
            ppc=config.get('ppc', 1.0),
            efficiency=config.get('efficiency', 1.0)
        )
    
    else:
        return GenericModule(
            name=name,
            ppc=config.get('ppc', 1.0),
            efficiency=config.get('efficiency', 1.0)
        )


def create_scenario(config: dict) -> ScenarioGraph:
    """Create ScenarioGraph from configuration dictionary."""
    scenario_data = config.get('scenario', config)
    name = scenario_data.get('name', 'Unnamed')
    
    scenario = ScenarioGraph(name=name)
    
    # Add tasks
    for task_config in scenario_data.get('tasks', []):
        workload = {}
        
        # Support both 'pixels' and 'width/height' formats
        if 'pixels' in task_config:
            workload['pixels'] = task_config['pixels']
        if 'width' in task_config:
            workload['width'] = task_config['width']
        if 'height' in task_config:
            workload['height'] = task_config['height']
        if 'ops' in task_config:
            workload['ops'] = task_config['ops']
        if 'data_size' in task_config:
            workload['data_size'] = task_config['data_size']
        
        # Parse crop_size from separate width/height fields
        crop_size = None
        if 'crop_width' in task_config and 'crop_height' in task_config:
            crop_size = (task_config['crop_width'], task_config['crop_height'])
        
        # Get optional ip_mode
        ip_mode = task_config.get('ip_mode', None)
        
        scenario.add_task(
            task_id=task_config['id'],
            mapped_hw=task_config['hw'],
            workload=workload,
            ip_mode=ip_mode,
            crop_size=crop_size
        )
    
    # Add edges
    for edge_config in scenario_data.get('edges', []):
        scenario.add_dependency(
            src=edge_config['src'],
            dst=edge_config['dst'],
            conn_type=edge_config.get('type', 'M2M'),
            buffer_size=edge_config.get('buffer_size')
        )
    
    return scenario


def run_demo():
    """Run a demonstration with sample configuration."""
    print("=" * 60)
    print("SoC Multimedia Architecture Simulator - Demo")
    print("=" * 60)
    
    # Create hardware nodes manually for demo
    hw_nodes = [
        # External node (Sensor) - excluded from SoC timing calculation
        ExternalNode(name="Sensor_Ext", frame_width=3840, frame_height=2160,
                     fps=30.0, sensor_mode="4K_30fps",
                     power_static=10.0, power_dynamic=50.0),
        IPNode(name="ISP_FE", clock_freq=600e6, ppc=4, efficiency=0.95,
               power_static=15.0, power_dynamic=80.0),
        IPNode(name="ISP_BE", clock_freq=600e6, ppc=2, efficiency=0.90,
               power_static=12.0, power_dynamic=60.0),
        IPNode(name="VENC", clock_freq=400e6, ppc=1, efficiency=0.85,
               power_static=20.0, power_dynamic=100.0),
    ]
    
    # Add modules to ISP_FE
    hw_nodes[1].add_module(ScalerModule(name="Scaler0", scale_factor=(0.5, 0.5), ppc=4))
    
    # Create scenario
    scenario = ScenarioGraph(name="4K_Recording")
    pixels_4k = 3840 * 2160  # 8,294,400
    
    scenario.add_task("t_sensor", "Sensor_Ext", pixels=pixels_4k)
    scenario.add_task("t_isp_fe", "ISP_FE", pixels=pixels_4k)
    scenario.add_task("t_isp_be", "ISP_BE", pixels=pixels_4k)
    scenario.add_task("t_venc", "VENC", pixels=pixels_4k)
    
    scenario.add_dependency("t_sensor", "t_isp_fe", "OTF")
    scenario.add_dependency("t_isp_fe", "t_isp_be", "M2M")
    scenario.add_dependency("t_isp_be", "t_venc", "M2M")
    
    # Create simulator
    simulator = SoCSimulator()
    simulator.register_hw_list(hw_nodes)
    simulator.load_scenario(scenario)
    
    # Add analyzers
    perf_analyzer = PerformanceAnalyzer()
    power_analyzer = PowerAnalyzer()
    timing_analyzer = TimingAnalyzer()
    
    simulator.add_analyzer(perf_analyzer)
    simulator.add_analyzer(power_analyzer)
    simulator.add_analyzer(timing_analyzer)
    
    # Text view before simulation
    text_viewer = TextViewer()
    print()
    print(text_viewer.print_hw_hierarchy(simulator.hw_registry))
    print()
    print(text_viewer.print_scenario_graph(scenario))
    print()
    
    # Run simulation
    print("Running simulation...")
    output = simulator.run_with_analysis()
    results = output['results']
    
    # Print results
    print()
    print(text_viewer.print_simulation_summary(results))
    print()
    
    # Print analysis reports
    print(perf_analyzer.format_report(output['analysis']['PerformanceAnalyzer']))
    print()
    print(power_analyzer.format_report(output['analysis']['PowerAnalyzer']))
    print()
    print(timing_analyzer.format_report(output['analysis']['TimingAnalyzer']))
    
    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='SoC Multimedia Architecture Simulator'
    )
    parser.add_argument(
        '--hw-config', '-hw',
        type=str,
        default=None,
        help='Path to hardware configuration YAML file'
    )
    parser.add_argument(
        '--scenario-config', '-sc',
        type=str,
        default=None,
        help='Path to scenario configuration YAML file'
    )
    parser.add_argument(
        '--demo',
        action='store_true',
        help='Run demonstration with sample configuration'
    )
    parser.add_argument(
        '--output-csv',
        type=str,
        default=None,
        help='Output path for CSV results'
    )
    parser.add_argument(
        '--output-gantt',
        type=str,
        default=None,
        help='Output path for Gantt chart (HTML)'
    )
    
    args = parser.parse_args()
    
    if args.demo or (args.hw_config is None and args.scenario_config is None):
        # Run demo mode
        results = run_demo()
        
        # Export if requested
        if args.output_csv or args.output_gantt:
            monitor = Monitor()
            monitor.from_simulation_results(results)
            
            if args.output_csv:
                monitor.export_csv(args.output_csv)
                print(f"\nResults exported to: {args.output_csv}")
            
            if args.output_gantt:
                visualizer = Visualizer()
                df = monitor.to_dataframe()
                fig = visualizer.create_gantt_chart_ms(df, title=results.scenario_name)
                if fig:
                    visualizer.save_gantt(fig, args.output_gantt)
                    print(f"Gantt chart saved to: {args.output_gantt}")
        
        return
    
    # Load configurations
    if args.hw_config:
        hw_config = load_hw_config(args.hw_config)
        hw_nodes = [create_hw_node(cfg) for cfg in hw_config.get('hardware', [])]
    else:
        print("Error: --hw-config required when not in demo mode")
        return
    
    if args.scenario_config:
        scenario_config = load_scenario_config(args.scenario_config)
        scenario = create_scenario(scenario_config)
    else:
        print("Error: --scenario-config required when not in demo mode")
        return
    
    # Run simulation
    simulator = SoCSimulator()
    simulator.register_hw_list(hw_nodes)
    simulator.load_scenario(scenario)
    simulator.add_analyzer(PerformanceAnalyzer())
    simulator.add_analyzer(PowerAnalyzer())
    simulator.add_analyzer(TimingAnalyzer())
    
    text_viewer = TextViewer()
    print(text_viewer.print_hw_hierarchy(simulator.hw_registry))
    print()
    print(text_viewer.print_scenario_graph(scenario))
    print()
    
    output = simulator.run_with_analysis()
    results = output['results']
    
    print(text_viewer.print_simulation_summary(results))
    
    # Export results
    if args.output_csv or args.output_gantt:
        monitor = Monitor()
        monitor.from_simulation_results(results)
        
        if args.output_csv:
            monitor.export_csv(args.output_csv)
            print(f"\nResults exported to: {args.output_csv}")
        
        if args.output_gantt:
            visualizer = Visualizer()
            df = monitor.to_dataframe()
            fig = visualizer.create_gantt_chart_ms(df, title=results.scenario_name)
            if fig:
                visualizer.save_gantt(fig, args.output_gantt)
                print(f"Gantt chart saved to: {args.output_gantt}")


if __name__ == "__main__":
    main()

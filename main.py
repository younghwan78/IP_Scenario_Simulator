"""
Main entry point for SoC Multimedia Architecture Simulator.

Usage:
    python main.py [--hw-config PATH] [--scenario-config PATH]
"""

import argparse
import sys
from pathlib import Path
from typing import Dict

import yaml

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from src.model.hw_nodes import (
    HWNode, ExternalNode, SensorNode, DisplayNode,
    IPNode, ProcessorNode, MemoryNode
)
from src.model.modules import Module, ScalerModule, CropModule, GenericModule, DMAModule
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
    clock = config.get('clock', config.get('max_clock', 1e9))  # Use max_clock if clock not set
    power_static = config.get('power_static', 0.0)
    power_dynamic = config.get('power_dynamic', 0.0)

    if node_type == 'Sensor':
        # SensorNode - static HW config only, runtime values set by scenario
        supported_modes = config.get('supported_sensor_modes', [])
        return SensorNode(
            name=name,
            # Default values, overridden by scenario config
            frame_width=config.get('frame_width', 1920),
            frame_height=config.get('frame_height', 1080),
            fps=config.get('fps', 30.0),
            supported_sensor_modes=supported_modes,
            sensor_mode=config.get('sensor_mode', ''),
            v_valid_time=config.get('v_valid_time', None),
            power_static=power_static,
            power_dynamic=power_dynamic
        )

    elif node_type == 'Display':
        # DisplayNode with display timing parameters
        return DisplayNode(
            name=name,
            frame_width=config.get('frame_width', 1920),
            frame_height=config.get('frame_height', 1080),
            fps=config.get('fps', 60.0),
            display_mode=config.get('display_mode', ''),
            h_total=config.get('h_total', None),
            v_total=config.get('v_total', None),
            power_static=power_static,
            power_dynamic=power_dynamic
        )

    elif node_type == 'External':
        # Generic ExternalNode (backward compatibility)
        return ExternalNode(
            name=name,
            frame_width=config.get('frame_width', 3840),
            frame_height=config.get('frame_height', 2160),
            fps=config.get('fps', 30.0),
            power_static=power_static,
            power_dynamic=power_dynamic
        )

    elif node_type == 'IP':
        # Parse new fields for HW constraints
        supported_modes = config.get('supported_modes', ['default'])
        supports_crop = config.get('supports_crop', False)
        supports_scale = config.get('supports_scale', False)
        max_clock = config.get('max_clock', None)
        clock_table = config.get('clock_table', [])
        min_size = tuple(config.get('min_size', [1, 1]))
        max_size = tuple(config.get('max_size', [65535, 65535]))

        node = IPNode(
            name=name,
            clock_freq=clock,
            ppc=config.get('ppc', 1.0),
            efficiency=config.get('efficiency', 1.0),
            max_clock=max_clock,
            clock_table=clock_table,
            min_size=min_size,
            max_size=max_size,
            power_static=power_static,
            power_dynamic=power_dynamic,
            supported_modes=supported_modes,
            supports_crop=supports_crop,
            supports_scale=supports_scale,
            latency=config.get('latency', 0.0)
        )

        # Add modules if present
        for mod_config in config.get('modules', []):
            module = create_module(mod_config)
            node.add_module(module)

        return node

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
            power_dynamic=power_dynamic,
            latency=config.get('latency', 0.0)
        )


def create_module(config: dict) -> Module:
    """Create Module from configuration dictionary."""
    mod_type = config.get('type', 'Generic')
    name = config['name']

    if mod_type == 'Scaler':
        scale = config.get('scale_factor', [1.0, 1.0])
        min_scale = config.get('min_scale', [0.0625, 0.0625])
        max_scale = config.get('max_scale', [16.0, 16.0])
        return ScalerModule(
            name=name,
            scale_factor=tuple(scale),
            min_scale=tuple(min_scale),
            max_scale=tuple(max_scale),
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

    elif mod_type == 'DMA':
        return DMAModule(
            name=name,
            max_bandwidth=config.get('max_bandwidth', 25.6e9),
            direction=config.get('direction', 'read'),
            multiple_outstanding=config.get('multiple_outstanding', 16),
            supported_compressions=config.get('supported_compressions', []),
            compression_ratios=config.get('compression_ratios', {})
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
            buffer_size=edge_config.get('buffer_size'),
            data=edge_config.get('data'),
            transfer=edge_config.get('transfer')
        )

    return scenario


def apply_scenario_settings(hw_nodes: Dict[str, HWNode],
                            scenario_config: dict) -> None:
    """
    Apply scenario-specific settings to HW nodes.

    This function applies runtime configuration from the scenario to HW nodes:
    - Sensor settings: frame_width, frame_height, fps, sensor_mode, v_valid_time
    - Module settings: scaler input/output sizes, crop regions

    Args:
        hw_nodes: Dictionary mapping HW names to HWNode instances
        scenario_config: Scenario configuration dictionary
    """
    scenario_data = scenario_config.get('scenario', scenario_config)

    # Apply sensor settings
    sensor_cfg = scenario_data.get('sensor', {})
    if sensor_cfg:
        hw_name = sensor_cfg.get('hw')
        if hw_name and hw_name in hw_nodes:
            sensor = hw_nodes[hw_name]
            if isinstance(sensor, SensorNode):
                if 'frame_width' in sensor_cfg:
                    sensor.frame_width = sensor_cfg['frame_width']
                if 'frame_height' in sensor_cfg:
                    sensor.frame_height = sensor_cfg['frame_height']
                if 'fps' in sensor_cfg:
                    sensor.fps = sensor_cfg['fps']
                if 'sensor_mode' in sensor_cfg:
                    sensor.sensor_mode = sensor_cfg['sensor_mode']
                if 'v_valid_time' in sensor_cfg:
                    sensor.v_valid_time = sensor_cfg['v_valid_time']

    # Apply module settings
    for mod_setting in scenario_data.get('module_settings', []):
        hw_name = mod_setting.get('hw')
        mod_name = mod_setting.get('module')

        if not hw_name or not mod_name or hw_name not in hw_nodes:
            continue

        hw = hw_nodes[hw_name]
        if not isinstance(hw, IPNode):
            continue

        module = hw.get_module(mod_name)
        if module is None:
            continue

        # Scaler settings: input/output size -> auto-calculate scale_factor
        if isinstance(module, ScalerModule):
            input_size = mod_setting.get('input_size')
            output_size = mod_setting.get('output_size')
            if input_size and output_size:
                module.set_sizes(tuple(input_size), tuple(output_size))
            elif 'scale_factor' in mod_setting:
                module.scale_factor = tuple(mod_setting['scale_factor'])

        # Crop settings: crop_region
        elif isinstance(module, CropModule):
            if 'crop_region' in mod_setting:
                module.crop_region = tuple(mod_setting['crop_region'])

    # Apply IP settings (e.g. manual clock override)
    for ip_setting in scenario_data.get('ip_settings', []):
        hw_name = ip_setting.get('hw')
        clock = ip_setting.get('clock')

        if hw_name and hw_name in hw_nodes:
            hw = hw_nodes[hw_name]
            if isinstance(hw, IPNode) and clock:
                hw.clock_freq = float(clock)
                hw.target_freq = float(clock)  # Mark as manually set
                print(f"[Config] Manual clock set for {hw_name}: {clock/1e6:.1f} MHz")


def run_demo():
    """Run a demonstration with sample configuration."""
    print("=" * 60)
    print("SoC Multimedia Architecture Simulator - Demo")
    print("=" * 60)

    # Create hardware nodes with STATIC HW config only (no sensor settings)
    hw_nodes = [
        # SensorNode - static config only, runtime values applied from scenario
        SensorNode(name="Sensor_Ext",
                   supported_sensor_modes=["4K_30fps", "4K_60fps", "1080p_120fps"],
                   power_static=10.0, power_dynamic=50.0),
        IPNode(name="ISP_FE", clock_freq=600e6, ppc=4, efficiency=0.95,
               max_clock=600e6, min_size=(64, 64), max_size=(8192, 8192),
               supports_scale=True,
               power_static=15.0, power_dynamic=80.0),
        IPNode(name="ISP_BE", clock_freq=600e6, ppc=2, efficiency=0.90,
               max_clock=600e6, supports_crop=True,
               power_static=12.0, power_dynamic=60.0),
        IPNode(name="VENC", clock_freq=400e6, ppc=1, efficiency=0.85,
               max_clock=400e6, min_size=(128, 128), max_size=(4096, 2160),
               power_static=20.0, power_dynamic=100.0),
    ]

    # Add modules to ISP_FE (HW config: constraints only)
    hw_nodes[1].add_module(ScalerModule(name="Scaler0", ppc=4,
                                        min_scale=(0.25, 0.25), max_scale=(4.0, 4.0)))

    # Build HW registry
    hw_registry = {node.name: node for node in hw_nodes}

    # Simulate scenario config (normally loaded from YAML)
    scenario_config = {
        'sensor': {
            'hw': 'Sensor_Ext',
            'frame_width': 3840,
            'frame_height': 2160,
            'fps': 30.0,
            'sensor_mode': '4K_30fps',
            'v_valid_time': 0.0118
        },
        'module_settings': [
            {
                'hw': 'ISP_FE',
                'module': 'Scaler0',
                'input_size': [3840, 2160],
                'output_size': [1920, 1080]
            }
        ]
    }

    # Apply scenario settings to HW nodes
    apply_scenario_settings(hw_registry, scenario_config)

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

    # Check and align OTF/Sensor timing (Clock Optimization)
    print("\n[Clock Optimization]")
    opt_messages = scenario.optimize_otf_clocks(hw_registry)
    for msg in opt_messages:
        print(msg)
    print()

    # Constraint Validation
    print("[Validation]")
    errors = scenario.validate_constraints(hw_registry)
    if errors:
        print("Error: Scenario validation failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    # Create simulator
    simulator = SoCSimulator()
    for node in hw_registry.values():
        simulator.register_hw(node)
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
    print(text_viewer.print_scenario_graph(scenario, hw_registry=hw_registry))
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
        help='Path to hardware configuration YAML file (e.g., hw_config/sample_hw.yaml)'
    )
    parser.add_argument(
        '--scenario-config', '-sc',
        type=str,
        default=None,
        help='Path to scenario configuration YAML file (e.g., scenario_config/sample_scenario.yaml)'
    )
    parser.add_argument(
        '--demo',
        action='store_true',
        help='Run demonstration with built-in sample configuration'
    )
    parser.add_argument(
        '--output-csv',
        type=str,
        default=None,
        help='Export simulation results to CSV file'
    )
    parser.add_argument(
        '--output-gantt',
        type=str,
        default=None,
        help='Export Gantt chart visualization to HTML file (requires Plotly)'
    )
    parser.add_argument(
        '--output-json',
        type=str,
        default=None,
        help='Export trace data to Perfetto JSON format for detailed analysis'
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

        if args.output_json:
            visualizer = Visualizer()
            visualizer.export_perfetto_json(results, args.output_json)

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

    # Build HW registry and apply scenario settings
    hw_registry = {node.name: node for node in hw_nodes}
    apply_scenario_settings(hw_registry, scenario_config)

    # Check and align OTF/Sensor timing (Clock Optimization)
    print("\n[Clock Optimization]")
    opt_messages = scenario.optimize_otf_clocks(hw_registry)
    for msg in opt_messages:
        print(msg)
    print()

    # Constraint Validation
    print("[Validation]")
    errors = scenario.validate_constraints(hw_registry)
    if errors:
        print("Error: Scenario validation failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    # Run simulation
    simulator = SoCSimulator()
    for node in hw_registry.values():
        simulator.register_hw(node)
    simulator.load_scenario(scenario)
    simulator.add_analyzer(PerformanceAnalyzer())
    simulator.add_analyzer(PowerAnalyzer())
    simulator.add_analyzer(TimingAnalyzer())

    text_viewer = TextViewer()
    print(text_viewer.print_hw_hierarchy(simulator.hw_registry))
    print()
    print(text_viewer.print_scenario_graph(scenario, hw_registry=simulator.hw_registry))
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

    if args.output_json:
        visualizer = Visualizer()
        visualizer.export_perfetto_json(results, args.output_json)


if __name__ == "__main__":
    main()

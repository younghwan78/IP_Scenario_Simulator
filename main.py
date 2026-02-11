"""
Main entry point for SoC Multimedia Architecture Simulator.

Usage:
    python main.py [--hw-config PATH] [--scenario-config PATH]
                   [--hw-info PATH] [--hw-dvfs PATH] [--asv-group N]
"""

import argparse
import io
import os
import sys
from pathlib import Path
from typing import Dict

import yaml

# Fix encoding for Windows console (cp949/Korean locale can't handle Unicode box-drawing chars)
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

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
            latency=config.get('latency', 0.0),
            ip_group=config.get('ip_group', ''),
            hierarchy_group=config.get('hierarchy_group', ''),
        )

        # Add modules if present
        for mod_config in config.get('modules', []):
            module = create_module(mod_config)
            node.add_module(module)

        # Parse intra-IP module edges
        for edge_cfg in config.get('edges', []):
            node.module_edges.append((edge_cfg['src'], edge_cfg['dst']))

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

    elif mod_type in ('CIN', 'COUT'):
        # CIN (OTF input) and COUT (OTF output) are treated as Generic modules
        return GenericModule(
            name=name,
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
    """Create ScenarioGraph from configuration dictionary.
    
    Supports both old format (scenario.tasks/edges) and new format (ip_blocks).
    """
    # Auto-detect format
    if 'ip_blocks' in config or ('ip_blocks' not in config and 'scenario' not in config and 'tasks' in config):
        return create_scenario_from_blocks(config)
    
    # Old format
    scenario_data = config.get('scenario', config)
    name = scenario_data.get('name', 'Unnamed')
    h_blank_margin = float(scenario_data.get('h_blank_margin', 0.05))

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
            crop_size=crop_size,
            h_blank_margin=h_blank_margin
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


def _build_workload_from_ip_settings(ip_settings: dict) -> dict:
    """
    Build workload dict from ip_settings inputs.
    
    Uses the first (primary) input's size as the workload dimensions.
    Size format: [x, y, width, height] (crop-aware).
    """
    workload = {}
    inputs = ip_settings.get('inputs', [])
    if inputs:
        primary_input = inputs[0]
        size = primary_input.get('size', [0, 0, 0, 0])
        if len(size) == 4:
            workload['width'] = size[2]   # width from [x, y, w, h]
            workload['height'] = size[3]  # height from [x, y, w, h]
        elif len(size) == 2:
            workload['width'] = size[0]
            workload['height'] = size[1]
    return workload


def create_scenario_from_blocks(config: dict) -> ScenarioGraph:
    """
    Create ScenarioGraph from new ip_blocks-based configuration.
    
    Format:
        name: "..."
        sensor: { hw, output_size, fps, ... }
        tasks: [{ id, hw, description }]  # sensor task
        ip_blocks:
          - ip_settings: { hw, mode, inputs, outputs }
            tasks: [{ id, hw, description }]
            edges: [{ src, dst, type, src_port, dst_port }]
    """
    name = config.get('name', 'Unnamed')
    h_blank_margin = float(config.get('h_blank_margin', 0.05))
    scenario = ScenarioGraph(name=name)
    
    # Store ip_settings per task for later reference (text view etc.)
    scenario._ip_settings = {}  # task_id -> ip_settings dict
    
    # Add sensor task(s)
    sensor_cfg = config.get('sensor', {})
    for task_cfg in config.get('tasks', []):
        workload = {}
        if sensor_cfg and sensor_cfg.get('hw') == task_cfg.get('hw'):
            output_size = sensor_cfg.get('output_size', [0, 0, 0, 0])
            if len(output_size) == 4:
                workload['width'] = output_size[2]
                workload['height'] = output_size[3]
        
        scenario.add_task(
            task_id=task_cfg['id'],
            mapped_hw=task_cfg['hw'],
            workload=workload,
            h_blank_margin=h_blank_margin
        )
    
    # Process each IP block
    for block in config.get('ip_blocks', []):
        ip_settings = block.get('ip_settings', {})
        hw_name = ip_settings.get('hw', '')
        
        # Build workload from primary input size
        workload = _build_workload_from_ip_settings(ip_settings)
        
        # Collect input/output port names for the task
        input_ports = [inp.get('port', '') for inp in ip_settings.get('inputs', [])]
        output_ports = [out.get('port', '') for out in ip_settings.get('outputs', [])]
        
        # Add tasks
        for task_cfg in block.get('tasks', []):
            task_id = task_cfg['id']
            task_hw = task_cfg.get('hw', hw_name)
            ip_mode = ip_settings.get('mode', None)
            
            scenario.add_task(
                task_id=task_id,
                mapped_hw=task_hw,
                workload=workload.copy(),
                ip_mode=ip_mode,
                h_blank_margin=h_blank_margin,
                input_ports=input_ports,
                output_ports=output_ports
            )
            
            # Store ip_settings for this task (for text view)
            scenario._ip_settings[task_id] = ip_settings
        
        # Add edges
        for edge_cfg in block.get('edges', []):
            scenario.add_dependency(
                src=edge_cfg['src'],
                dst=edge_cfg['dst'],
                conn_type=edge_cfg.get('type', 'M2M'),
                src_port=edge_cfg.get('src_port', 'output'),
                dst_port=edge_cfg.get('dst_port', 'input'),
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
                # Support output_size format: [x, y, width, height]
                if 'output_size' in sensor_cfg:
                    output_size = sensor_cfg['output_size']
                    if len(output_size) == 4:
                        sensor.frame_width = output_size[2]
                        sensor.frame_height = output_size[3]
                    elif len(output_size) == 2:
                        sensor.frame_width = output_size[0]
                        sensor.frame_height = output_size[1]
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

    # Apply module settings (old format)
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

    # Apply IP settings (old format: list of ip_settings for clock override)
    if isinstance(scenario_data.get('ip_settings'), list):
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
        help='Path to hardware configuration YAML file (e.g., hw_config/projectA_hw.yaml)'
    )
    parser.add_argument(
        '--scenario-config', '-sc',
        type=str,
        default=None,
        help='Path to scenario configuration YAML file (e.g., scenario_config/projectA_FHD30_recording_scenario.yaml)'
    )
    parser.add_argument(
        '--demo',
        action='store_true',
        help='Run demonstration with built-in sample configuration'
    )
    parser.add_argument(
        '--graph-only',
        action='store_true',
        help='Only build and display graph structure (no simulation)'
    )
    parser.add_argument(
        '--num-frames', '-n',
        type=int,
        default=None,
        help='Number of frames to simulate (overrides scenario config, default: 1)'
    )
    parser.add_argument(
        '--hw-info',
        type=str,
        default=None,
        help='Path to project info CSV (e.g., hw_config/projectA_info.csv)'
    )
    parser.add_argument(
        '--hw-dvfs',
        type=str,
        default=None,
        help='Path to project DVFS CSV (e.g., hw_config/projectA_dvfs.csv)'
    )
    parser.add_argument(
        '--asv-group',
        type=int,
        default=None,
        help='ASV group for DVFS voltage lookup (overrides scenario config, default: 4)'
    )
    # ── Output selection flags ──
    # Default (no flags) = generate ALL outputs
    # If any flag specified, generate only those outputs
    parser.add_argument(
        '--view', action='store_true',
        help='Generate HTML view files (Top/Level1/Level2)'
    )
    parser.add_argument(
        '--gantt', action='store_true',
        help='Generate Gantt chart HTML'
    )
    parser.add_argument(
        '--bw', action='store_true',
        help='Generate Bandwidth timeline chart HTML'
    )
    parser.add_argument(
        '--csv', action='store_true',
        help='Export simulation results to CSV'
    )
    parser.add_argument(
        '--json', action='store_true',
        help='Export trace data to Perfetto JSON format'
    )
    parser.add_argument(
        '--output-view-dir',
        type=str,
        default='output_view',
        help='Directory for HTML view outputs (default: output_view)'
    )
    parser.add_argument(
        '--output-sim-dir',
        type=str,
        default='output_simulation',
        help='Directory for simulation outputs (default: output_simulation)'
    )

    args = parser.parse_args()

    # Determine output flags: default (no flags) = all outputs
    any_flag = args.view or args.gantt or args.bw or args.csv or args.json
    do_view = args.view or (not any_flag)
    do_gantt = args.gantt or (not any_flag)
    do_bw = args.bw or (not any_flag)
    do_csv = args.csv or (not any_flag)
    do_json = args.json or (not any_flag)

    if args.demo or (args.hw_config is None and args.scenario_config is None):
        # Run demo mode
        results = run_demo()

        # Export if requested
        monitor = Monitor()
        monitor.from_simulation_results(results)

        os.makedirs(args.output_sim_dir, exist_ok=True)

        if do_csv:
            csv_path = os.path.join(args.output_sim_dir, 'demo_results.csv')
            monitor.export_csv(csv_path)
            print(f"\nResults exported to: {csv_path}")

        if do_gantt:
            visualizer = Visualizer()
            df = monitor.to_dataframe()
            fig = visualizer.create_gantt_chart_ms(df, title=results.scenario_name)
            if fig:
                gantt_path = os.path.join(args.output_sim_dir, 'demo_gantt.html')
                visualizer.save_gantt(fig, gantt_path)
                print(f"Gantt chart saved to: {gantt_path}")

        if do_json:
            visualizer = Visualizer()
            json_path = os.path.join(args.output_sim_dir, 'demo_trace.json')
            visualizer.export_perfetto_json(results, json_path)

        return

    # Load configurations
    if args.hw_config:
        hw_config = load_hw_config(args.hw_config)
        # Support both formats: direct list or wrapped in 'hardware' key
        if isinstance(hw_config, list):
            hw_list = hw_config
        else:
            hw_list = hw_config.get('hardware', [])
        hw_nodes = [create_hw_node(cfg) for cfg in hw_list]
        # Keep raw config for Level2 HTML view (module info)
        hw_raw = {item['name']: item for item in hw_list}
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

    # ── Derive output prefix: {project}_{scenario}_ ──
    # Project name from hw_config filename (e.g., projectA_hw.yaml → projectA)
    hw_basename = os.path.splitext(os.path.basename(args.hw_config))[0]
    project_name = hw_basename.replace('_hw', '')
    scenario_name = scenario.name.replace(' ', '_')
    output_prefix = f"{project_name}_{scenario_name}_"

    # ── CSV-based HW Info Integration ──────────────────────────
    resolved_configs = None
    if args.hw_info and args.hw_dvfs:
        from src.model.hw_info import create_hw_info_db
        from src.model.hw_resolver import HWResolver

        print("\n[CSV HW Config Loading]")
        hw_info_db = create_hw_info_db(args.hw_info, args.hw_dvfs)
        print(f"  Project: {hw_info_db.project_name}")
        print(f"  IPs loaded: {len(hw_info_db.ip_infos)}")
        print(f"  DVFS tables loaded: {len(hw_info_db.dvfs_tables)} "
              f"({', '.join(hw_info_db.dvfs_tables.keys())})")

        # Validate: all IPs in hw_registry must exist in info.csv
        validation_errors = hw_info_db.validate_against_hw(hw_registry)
        if validation_errors:
            print("\n[Error] CSV validation failed:")
            for err in validation_errors:
                print(f"  - {err}")
            sys.exit(1)
        print("  Validation: PASSED")

        # Resolve DVFS/Voltage
        scenario_data = scenario_config.get('scenario', scenario_config)
        asv_group = args.asv_group or scenario_data.get('asv_group', 4)
        resolver = HWResolver(hw_info_db, asv_group=asv_group)
        resolved_configs = resolver.resolve_scenario(
            hw_registry, scenario, scenario_config
        )
        resolver.apply_to_hw(hw_registry, resolved_configs)

        # Print exploration report
        print(resolver.get_exploration_report(resolved_configs))
    elif args.hw_info or args.hw_dvfs:
        print("Warning: Both --hw-info and --hw-dvfs must be specified together. "
              "Skipping CSV-based HW config.")

    text_viewer = TextViewer()

    # ── Generate HTML views ──
    if do_view:
        from src.view.html_view import (
            generate_top_html, generate_level1_html, generate_level2_html,
            generate_task_topology_html
        )
        from src.view.plantuml_view import (
            generate_top_view, generate_level1, generate_level2,
            generate_task_topology
        )
        os.makedirs(args.output_view_dir, exist_ok=True)
        # HTML views
        top_path = os.path.join(args.output_view_dir, f"{output_prefix}top.html")
        l1_path = os.path.join(args.output_view_dir, f"{output_prefix}level1.html")
        l2_path = os.path.join(args.output_view_dir, f"{output_prefix}level2.html")
        topo_html = os.path.join(args.output_view_dir, f"{output_prefix}task_topology.html")
        generate_top_html(hw_registry, scenario, top_path)
        generate_level1_html(hw_registry, scenario, l1_path)
        generate_level2_html(hw_registry, scenario, hw_raw, l2_path)
        generate_task_topology_html(hw_registry, scenario, topo_html)
        # PlantUML views
        puml_top = os.path.join(args.output_view_dir, f"{output_prefix}top.puml")
        puml_l1 = os.path.join(args.output_view_dir, f"{output_prefix}level1.puml")
        puml_l2 = os.path.join(args.output_view_dir, f"{output_prefix}level2.puml")
        puml_topo = os.path.join(args.output_view_dir, f"{output_prefix}task_topology.puml")
        generate_top_view(hw_registry, scenario, puml_top)
        generate_level1(hw_registry, scenario, puml_l1)
        generate_level2(hw_registry, scenario, hw_raw, puml_l2)
        generate_task_topology(hw_registry, scenario, puml_topo)

    # Graph-only mode: show structure and exit
    if args.graph_only:
        print("=" * 70)
        print(f"  Graph Structure: {scenario.name}")
        print("=" * 70)
        print()
        print(text_viewer.print_hw_hierarchy(hw_registry))
        print()
        print(text_viewer.print_scenario_graph(scenario, hw_registry=hw_registry))
        print()
        print(text_viewer.print_scenario_flow(scenario, hw_registry=hw_registry))
        return

    # Check and align OTF/Sensor timing (Clock Optimization)
    # Skip if CSV-based resolution already set clocks
    if resolved_configs is None:
        print("\n[Clock Optimization]")
        opt_messages = scenario.optimize_otf_clocks(hw_registry)
        for msg in opt_messages:
            print(msg)
        print()
    else:
        print("\n[Clock Optimization] Skipped (CSV-based DVFS resolution active)")
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

    print(text_viewer.print_hw_hierarchy(simulator.hw_registry))
    print()
    print(text_viewer.print_scenario_graph(scenario, hw_registry=simulator.hw_registry))
    print()

    # Determine num_frames: CLI arg > scenario config > default (1)
    scenario_data = scenario_config.get('scenario', scenario_config)
    num_frames = args.num_frames or scenario_data.get('num_frames', 1)
    
    if num_frames > 1:
        print(f"[Multi-Frame Simulation] Running {num_frames} frames...")
    
    output = simulator.run_with_analysis(num_frames=num_frames)
    results = output['results']

    print(text_viewer.print_simulation_summary(results))

    # ── Export simulation results ──
    os.makedirs(args.output_sim_dir, exist_ok=True)
    monitor = Monitor()
    monitor.from_simulation_results(results)

    if do_csv:
        csv_path = os.path.join(args.output_sim_dir, f"{output_prefix}results.csv")
        monitor.export_csv(csv_path)
        print(f"Results exported to: {csv_path}")

    if do_gantt:
        visualizer = Visualizer()
        df = monitor.to_dataframe()
        # Build HW order from scenario config (ip_blocks definition order)
        hw_order = []
        seen_hw = set()
        # Sensor task first
        sensor_cfg = scenario_config.get('sensor', {})
        if sensor_cfg.get('hw'):
            hw_order.append(sensor_cfg['hw'])
            seen_hw.add(sensor_cfg['hw'])
        # Then ip_blocks in YAML definition order
        for block in scenario_config.get('ip_blocks', []):
            ip_set = block.get('ip_settings', {})
            hw_name = ip_set.get('hw', '')
            if hw_name and hw_name not in seen_hw:
                hw_order.append(hw_name)
                seen_hw.add(hw_name)
        # Fallback: append any HW from tasks not in ip_blocks
        for tid in scenario.topological_order():
            task = scenario.get_task(tid)
            if task and task.mapped_hw not in seen_hw:
                hw_order.append(task.mapped_hw)
                seen_hw.add(task.mapped_hw)
        fig = visualizer.create_gantt_chart_ms(df, title=results.scenario_name, hw_order=hw_order)
        if fig:
            gantt_path = os.path.join(args.output_sim_dir, f"{output_prefix}gantt.html")
            visualizer.save_gantt(fig, gantt_path)
            print(f"Gantt chart saved to: {gantt_path}")

    if do_json:
        visualizer = Visualizer()
        json_path = os.path.join(args.output_sim_dir, f"{output_prefix}trace.json")
        visualizer.export_perfetto_json(results, json_path)
        print(f"Trace exported to: {json_path}")

    if do_bw:
        visualizer = Visualizer()
        bw_fig = visualizer.create_bw_chart(results, scenario,
                                            title=f"{scenario.name} - Bandwidth Timeline")
        if bw_fig:
            bw_path = os.path.join(args.output_sim_dir, f"{output_prefix}bw.html")
            visualizer.save_gantt(bw_fig, bw_path)
            print(f"BW chart saved to: {bw_path}")


if __name__ == "__main__":
    main()

"""
Tests for YAML configuration loading round-trip.

Validates that hw.yaml and scenario.yaml files are parsed correctly
into HW registry and ScenarioGraph, and that cross-validation works.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from src.model.hw_nodes import IPNode, SensorNode, DisplayNode
from src.model.modules import DMAModule
from src.model.scenario import ScenarioGraph, ConnectionType


# ============================================================
# HW YAML Loading
# ============================================================

SAMPLE_HW_YAML = """
hardware:
  - name: Sensor_0
    type: sensor
    frame_width: 3840
    frame_height: 2160
    fps: 30
    sensor_mode: "4K_30"

  - name: ISP_FE
    type: ip
    clock_freq: 600000000
    ppc: 4
    efficiency: 0.95
    supports_scale: true
    supported_modes:
      - default
      - power_saving
    modules:
      - name: Scaler0
        type: scaler
        scale_factor: [0.5, 0.5]
      - name: WDMA0
        type: dma
        bandwidth: 6400000000
        direction: write

  - name: VENC
    type: ip
    clock_freq: 400000000
    ppc: 1
    efficiency: 0.85
"""

SAMPLE_SCENARIO_YAML = """
scenario:
  name: "4K_Recording"
  fps: 30

tasks:
  - task_id: t_sensor
    mapped_hw: Sensor_0
    width: 3840
    height: 2160

  - task_id: t_isp_fe
    mapped_hw: ISP_FE
    width: 3840
    height: 2160
    ip_mode: default

  - task_id: t_venc
    mapped_hw: VENC
    width: 3840
    height: 2160

dependencies:
  - src: t_sensor
    dst: t_isp_fe
    type: OTF

  - src: t_isp_fe
    dst: t_venc
    type: M2M
"""


def _parse_hw_yaml(yaml_text: str) -> dict:
    """Parse hardware YAML into a registry dict.

    This replicates the logic in main.py that loads hw.yaml.
    """
    from src.model.modules import ScalerModule

    data = yaml.safe_load(yaml_text)
    registry = {}

    for hw_def in data.get('hardware', []):
        name = hw_def['name']
        hw_type = hw_def.get('type', 'ip')

        if hw_type == 'sensor':
            node = SensorNode(
                name=name,
                frame_width=hw_def.get('frame_width', 3840),
                frame_height=hw_def.get('frame_height', 2160),
                fps=hw_def.get('fps', 30.0),
                sensor_mode=hw_def.get('sensor_mode', ''),
            )
        elif hw_type == 'display':
            node = DisplayNode(
                name=name,
                frame_width=hw_def.get('frame_width', 1920),
                frame_height=hw_def.get('frame_height', 1080),
                fps=hw_def.get('fps', 60.0),
            )
        else:
            node = IPNode(
                name=name,
                clock_freq=hw_def.get('clock_freq', 1e9),
                ppc=hw_def.get('ppc', 1),
                efficiency=hw_def.get('efficiency', 1.0),
                supports_scale=hw_def.get('supports_scale', False),
                supports_crop=hw_def.get('supports_crop', False),
                supported_modes=hw_def.get('supported_modes', ['default']),
            )
            # Add modules
            for mod_def in hw_def.get('modules', []):
                mod_type = mod_def.get('type', '')
                if mod_type == 'scaler':
                    node.add_module(ScalerModule(
                        name=mod_def['name'],
                        scale_factor=tuple(mod_def.get('scale_factor', [1.0, 1.0])),
                    ))
                elif mod_type == 'dma':
                    node.add_module(DMAModule(
                        name=mod_def['name'],
                        max_bandwidth=mod_def.get('bandwidth', 6.4e9),
                        direction=mod_def.get('direction', 'read'),
                    ))

        registry[name] = node

    return registry


def _parse_scenario_yaml(yaml_text: str) -> ScenarioGraph:
    """Parse scenario YAML into a ScenarioGraph.

    This replicates the logic in main.py that loads scenario.yaml.
    """
    data = yaml.safe_load(yaml_text)
    sc = data.get('scenario', {})
    scenario = ScenarioGraph(name=sc.get('name', 'Scenario'))

    for task_def in data.get('tasks', []):
        kwargs = {}
        if 'width' in task_def:
            kwargs['width'] = task_def['width']
        if 'height' in task_def:
            kwargs['height'] = task_def['height']
        if 'pixels' in task_def:
            kwargs['pixels'] = task_def['pixels']

        scenario.add_task(
            task_id=task_def['task_id'],
            mapped_hw=task_def['mapped_hw'],
            ip_mode=task_def.get('ip_mode'),
            h_blank_margin=task_def.get('h_blank_margin', 0.05),
            **kwargs,
        )

    for dep_def in data.get('dependencies', []):
        scenario.add_dependency(
            src=dep_def['src'],
            dst=dep_def['dst'],
            conn_type=dep_def.get('type', 'M2M'),
        )

    return scenario


# ============================================================
# Tests: HW YAML Loading
# ============================================================

class TestHWYAMLLoading:
    """Test parsing of hardware YAML into HW registry."""

    def test_load_sensor(self):
        """Sensor node should have correct attributes."""
        registry = _parse_hw_yaml(SAMPLE_HW_YAML)

        assert "Sensor_0" in registry
        sensor = registry["Sensor_0"]
        assert isinstance(sensor, SensorNode)
        assert sensor.frame_width == 3840
        assert sensor.frame_height == 2160
        assert sensor.fps == 30.0
        assert sensor.sensor_mode == "4K_30"

    def test_load_ip_with_modules(self):
        """IPNode should have correct attributes and child modules."""
        registry = _parse_hw_yaml(SAMPLE_HW_YAML)

        assert "ISP_FE" in registry
        isp = registry["ISP_FE"]
        assert isinstance(isp, IPNode)
        assert isp.clock_freq == 600e6
        assert isp.ppc == 4
        assert isp.efficiency == 0.95
        assert isp.supports_scale is True
        assert 'default' in isp.supported_modes
        assert 'power_saving' in isp.supported_modes

        # Check modules
        assert len(isp.modules) == 2
        scaler = isp.get_module("Scaler0")
        assert scaler is not None
        dma = isp.get_module("WDMA0")
        assert dma is not None
        assert isinstance(dma, DMAModule)
        assert dma.direction == "write"

    def test_load_simple_ip(self):
        """Simple IP without modules should load correctly."""
        registry = _parse_hw_yaml(SAMPLE_HW_YAML)

        assert "VENC" in registry
        venc = registry["VENC"]
        assert isinstance(venc, IPNode)
        assert venc.ppc == 1
        assert len(venc.modules) == 0

    def test_hw_count(self):
        """Registry should contain all defined hardware."""
        registry = _parse_hw_yaml(SAMPLE_HW_YAML)
        assert len(registry) == 3


# ============================================================
# Tests: Scenario YAML Loading
# ============================================================

class TestScenarioYAMLLoading:
    """Test parsing of scenario YAML into ScenarioGraph."""

    def test_scenario_name(self):
        """Scenario should have correct name."""
        scenario = _parse_scenario_yaml(SAMPLE_SCENARIO_YAML)
        assert scenario.name == "4K_Recording"

    def test_task_count(self):
        """All tasks should be loaded."""
        scenario = _parse_scenario_yaml(SAMPLE_SCENARIO_YAML)
        assert len(scenario) == 3

    def test_task_workloads(self):
        """Task workloads should contain width/height."""
        scenario = _parse_scenario_yaml(SAMPLE_SCENARIO_YAML)
        task = scenario.get_task("t_isp_fe")
        assert task is not None
        assert task.workload.get('width') == 3840
        assert task.workload.get('height') == 2160
        assert task.mapped_hw == "ISP_FE"
        assert task.ip_mode == "default"

    def test_dependency_types(self):
        """Dependencies should have correct connection types."""
        scenario = _parse_scenario_yaml(SAMPLE_SCENARIO_YAML)

        dep_otf = scenario.get_dependency("t_sensor", "t_isp_fe")
        assert dep_otf is not None
        assert dep_otf['conn_type'] == ConnectionType.OTF

        dep_m2m = scenario.get_dependency("t_isp_fe", "t_venc")
        assert dep_m2m is not None
        assert dep_m2m['conn_type'] == ConnectionType.M2M

    def test_graph_is_valid(self):
        """Loaded scenario should pass validation."""
        scenario = _parse_scenario_yaml(SAMPLE_SCENARIO_YAML)
        is_valid, errors = scenario.validate()
        assert is_valid, f"Scenario validation failed: {errors}"

    def test_topological_order(self):
        """Tasks should have valid topological order."""
        scenario = _parse_scenario_yaml(SAMPLE_SCENARIO_YAML)
        order = scenario.topological_order()
        assert len(order) == 3
        # Sensor must come before ISP_FE, ISP_FE before VENC
        assert order.index("t_sensor") < order.index("t_isp_fe")
        assert order.index("t_isp_fe") < order.index("t_venc")


# ============================================================
# Tests: Cross-Validation
# ============================================================

class TestCrossValidation:
    """Test HW+Scenario cross-validation."""

    def test_all_tasks_map_to_valid_hw(self):
        """Every task's mapped_hw should exist in the HW registry."""
        registry = _parse_hw_yaml(SAMPLE_HW_YAML)
        scenario = _parse_scenario_yaml(SAMPLE_SCENARIO_YAML)

        for task in scenario.get_tasks():
            assert task.mapped_hw in registry, \
                f"Task '{task.task_id}' mapped to unknown HW '{task.mapped_hw}'"

    def test_constraint_validation_passes(self):
        """Valid scenario should pass constraint validation."""
        registry = _parse_hw_yaml(SAMPLE_HW_YAML)
        scenario = _parse_scenario_yaml(SAMPLE_SCENARIO_YAML)

        errors = scenario.validate_constraints(registry)
        assert len(errors) == 0, f"Validation errors: {errors}"

    def test_invalid_hw_mapping_detected(self):
        """Task mapped to non-existent HW should be flagged."""
        registry = _parse_hw_yaml(SAMPLE_HW_YAML)

        scenario = ScenarioGraph(name="Bad_Scenario")
        scenario.add_task("t_bad", "NONEXISTENT_IP", pixels=100)

        errors = scenario.validate_constraints(registry)
        assert len(errors) > 0
        assert "NONEXISTENT_IP" in errors[0]

    def test_invalid_ip_mode_detected(self):
        """Task using unsupported IP mode should be flagged."""
        registry = _parse_hw_yaml(SAMPLE_HW_YAML)

        scenario = ScenarioGraph(name="Bad_Mode")
        scenario.add_task("t_bad", "ISP_FE", pixels=100, ip_mode="turbo")

        errors = scenario.validate_constraints(registry)
        assert len(errors) > 0
        assert "turbo" in errors[0]


# ============================================================
# Tests: Error Handling
# ============================================================

class TestInvalidYAMLHandling:
    """Test that invalid configurations are handled gracefully."""

    def test_empty_hardware_list(self):
        """Empty hardware list should produce empty registry."""
        registry = _parse_hw_yaml("hardware: []")
        assert len(registry) == 0

    def test_empty_tasks(self):
        """Empty task list should produce empty scenario."""
        scenario = _parse_scenario_yaml("scenario:\n  name: Empty\ntasks: []\ndependencies: []")
        assert len(scenario) == 0

    def test_dependency_missing_task_raises(self):
        """Dependency referencing non-existent task should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            scenario = _parse_scenario_yaml(
                "scenario:\n  name: Bad\n"
                "tasks:\n  - task_id: t_a\n    mapped_hw: IP\n"
                "dependencies:\n  - src: t_a\n    dst: t_nonexistent\n    type: M2M"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

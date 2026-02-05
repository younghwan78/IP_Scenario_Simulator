# SoC Multimedia Architecture Simulator

SimPy 기반의 Discrete Event Simulator로 Android SoC의 Multimedia IP 성능/지연/전력을 시뮬레이션합니다.

## Features

- **SimPy 기반 Event-Driven Simulation**: 이벤트 중심의 정확한 타이밍 시뮬레이션
- **NetworkX DAG Modeling**: Task 의존성 그래프 기반 시나리오 모델링
- **OTF/M2M 데이터 흐름**: 파이프라인(OTF)과 메모리 기반(M2M) 연결 지원
- **MVC Architecture**: Model-View-Controller 패턴으로 확장성 확보
- **Multiple Analyzers**: Performance, Power, Timing 분석 분리
- **Flexible Configuration**: HW와 Scenario YAML 파일 분리

## Quick Start

### Installation

```bash
# Clone repository
cd e:\10_Codes\23_MMIP_Scenario_simulation2

# Create virtual environment
python -m venv venv

# Activate (Windows)
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Run Demo

```bash
python main.py --demo
```

### Run with Config Files

```bash
python main.py --hw-config hw_config/sample_hw.yaml \
               --scenario-config scenario_config/sample_scenario.yaml
```

### Export Results

```bash
```bash
python main.py --demo --output-csv results.csv --output-gantt gantt.html --output-json trace.json
```

## Command Line Options

| Option | Shorthand | Description |
|--------|-----------|-------------|
| `--hw-config` | `-hw` | **Required.** Path to hardware configuration YAML file. |
| `--scenario-config` | `-sc` | **Required.** Path to scenario configuration YAML file. |
| `--demo` | | Run demonstration with built-in sample configuration. |
| `--output-csv` | | Export simulation results to CSV file. |
| `--output-gantt` | | Export Gantt chart to HTML (requires Plotly). |
| `--output-json` | | Export trace data to Perfetto JSON format for detailed analysis. |
| `--help` | `-h` | Show help message and exit. |

## Project Structure

```
├── src/
│   ├── model/           # HW nodes, Modules, Scenario graph
│   ├── controller/      # Simulator, Analyzers
│   └── view/            # TextViewer, Visualizer
├── hw_config/           # Hardware configurations
├── scenario_config/     # Scenario configurations
├── tests/               # Unit & Integration tests
├── main.py              # Entry point
├── DESIGN.md            # Design document
└── requirements.txt     # Dependencies
```

## HW Node Types

| Type | Description | Key Parameters |
|------|-------------|----------------|
| **IPNode** | Pixel processing (ISP, Codec) | `ppc`, `efficiency` |
| **DMANode** | Memory access | `bandwidth`, `multiple_outstanding` |
| **ProcessorNode** | CPU/DSP/NPU | `cycles_per_op`, `num_cores` |
| **MemoryNode** | DRAM/SRAM | `bandwidth`, `capacity` |

## Connection Types

| Type | Description | Timing |
|------|-------------|--------|
| **M2M** | Memory-to-Memory (Sequential) | `Time(A) + Time(B)` |
| **OTF** | On-The-Fly (Pipelined) | `max(Time(A), Time(B))` |

## Example Output

```text
[Clock Optimization]
Optimizing OTF Group [t_isp_fe, t_sensor]
  Constraint: vValid=11.80ms, Req Throughput=702.92Mpps
  ISP_FE: Req=185.0MHz -> Set=200.0MHz

[SoC Hardware Hierarchy]
├── Sensor_Ext (SensorNode, 3840x2160@30fps, mode=4K_30fps, vValid=11.80ms)
├── ISP_FE (IPNode, Tar: 200MHz [Req: 185MHz], PPC=4)
│   ├── Scaler0 (ScalerModule, scale=0.50x0.50, 3840x2160 → 1920x1080)
│   └── ...
├── VENC (IPNode, 400MHz, PPC=1)

[Scenario: 4K_Recording]
Topological Order:
  t_sensor ══► t_isp_fe[Scaler0] ──→ t_isp_be[Crop0] ──→ t_venc

[Simulation Results: 4K_Recording]
Total Time: 24.693 ms
Total Tasks: 4

Task Execution Timeline:
--------------------------------------------------------------------------------
Task ID              Hardware        Start (ms)   End (ms)     Duration (ms)
--------------------------------------------------------------------------------
t_isp_fe             ISP_FE          0.000        10.914       10.914
...

Timing Diagram (ASCII Gantt):
--------------------------------------------------------------------------------
Scale: 62 chars = 24.7 ms (0.40 ms/char)
                | 0.............................................24.7ms
ISP_FE          | ####################### t_isp_fe (0.0-10.9ms)
Sensor_Ext      | ####################### t_sensor (0.0-10.9ms)
...
--------------------------------------------------------------------------------
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_model.py -v
```

## Documentation

- [DESIGN.md](DESIGN.md) - Detailed design document
- [hw_config/sample_hw.yaml](hw_config/sample_hw.yaml) - Sample HW configuration
- [scenario_config/sample_scenario.yaml](scenario_config/sample_scenario.yaml) - Sample scenario

## Dependencies

- Python 3.10+
- SimPy >= 4.0
- NetworkX >= 3.0
- Pandas >= 2.0
- Plotly >= 5.0
- PyYAML >= 6.0

## License

MIT License

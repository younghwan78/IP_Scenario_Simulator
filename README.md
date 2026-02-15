# SoC Multimedia Architecture Simulator

SimPy 기반의 Discrete Event Simulator로 Android SoC의 Multimedia IP 성능/지연/전력을 시뮬레이션합니다.

## Features

- **SimPy 기반 Event-Driven Simulation**: 이벤트 중심의 정확한 타이밍 시뮬레이션
- **NetworkX DAG Modeling**: Task 의존성 그래프 기반 시나리오 모델링
- **OTF/M2M 데이터 흐름**: 파이프라인(OTF)과 메모리 기반(M2M) 연결 지원
- **Multi-Frame Pipelined Simulation**: FPS 기반 프레임 간격으로 파이프라인 중첩 지원
- **CSV-based HW Config**: IP 성능 정보와 DVFS 테이블을 CSV로 관리
- **DVFS Voltage Resolution**: ASV 그룹 기반 동적 전압/주파수 최적화
- **MIF DVFS Level Determination**: Total DMA BW 기반 MIF 레벨 자동 결정 (`mif_bw = freq × channel_width × mem_util`)
- **Power Calculation**: VDD 도메인 전압 정렬, req/set voltage 기반 동적 전력 계산
- **Simulation Report**: HTML/Markdown 리포트 자동 생성 (6개 섹션 + MIF Level)
- **PNG Chart Export**: Gantt/BW 차트를 PNG 이미지로 자동 저장 (kaleido)
- **Multi-Level Views**: Top/Level1/Level2/Level3 뷰를 HTML(ELK.js) 및 PlantUML로 생성
- **Level 2 Module Coloring**: RDMA/WDMA/CIN/COUT 및 SBWC/LLC 상태별 색상 구분
- **BW Timeline Chart**: M2M 연결의 Read/Write Bandwidth 시각화
- **CDN-based HTML**: Plotly CDN으로 경량 HTML 출력 (4.8MB → 11KB)
- **Verbose Mode**: `-v` 플래그로 상세 로그 출력 제어 (기본: 파일 저장 메시지만)
- **Architecture Exploration**: DVFS/Mode 파라미터 스윕으로 최적 전력 구성 탐색 (SVG 차트 포함)
- **MVC Architecture**: Model-View-Controller 패턴으로 확장성 확보
- **Multiple Analyzers**: Performance, Power, Timing 분석 분리

## Quick Start

### Installation

```bash
cd e:\10_Codes\23_MMIP_Scenario_simulation2

python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Run Simulation (all outputs)

```bash
python main.py -hw hw_config/projectA_hw.yaml \
               -sc scenario_config/projectA_FHD30_recording_scenario.yaml \
               --hw-info hw_config/projectA_info.csv \
               --hw-dvfs hw_config/projectA_dvfs.csv
```

### Selective Output

```bash
# View only (HTML + PlantUML, no simulation)
python main.py -hw ... -sc ... --hw-info ... --hw-dvfs ... --view --graph-only

# Gantt + CSV only
python main.py -hw ... -sc ... --hw-info ... --hw-dvfs ... --gantt --csv

# BW chart only
python main.py -hw ... -sc ... --hw-info ... --hw-dvfs ... --bw
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `-hw`, `--hw-config` | **Required.** Hardware configuration YAML |
| `-sc`, `--scenario-config` | **Required.** Scenario configuration YAML |
| `--hw-info` | CSV file with IP performance info (PPC, clock, etc.) |
| `--hw-dvfs` | CSV file with DVFS voltage tables |
| `--asv-group` | ASV group for DVFS lookup (default: from scenario) |
| `--num-frames` | Number of frames for multi-frame simulation |
| `--explore` | Path to exploration YAML config for architecture sweep |
| `--graph-only` | Show graph structure and exit (no simulation) |
| `--demo` | Run built-in demo |
| `-v`, `--verbose` | Enable verbose output (show all diagnostic info) |

### Output Flags

| Flag | Description |
|------|-------------|
| `--view` | Generate HTML + PlantUML view files (Top/Level1/Level2) |
| `--gantt` | Generate Gantt chart HTML |
| `--bw` | Generate Bandwidth timeline chart HTML |
| `--csv` | Export simulation results to CSV |
| `--json` | Export trace data to Perfetto JSON format |
| `--output-view-dir` | Output directory for views (default: `output_view`) |
| `--output-sim-dir` | Output directory for simulation (default: `output_simulation`) |

> **Verbose mode**: By default, only file-save messages are printed. Use `-v` for full diagnostic output.

> **Default behavior**: No flags specified → generate ALL outputs.

## Output Structure

```
output_view/
  {project}_{scenario}_top.html             # Top-level block diagram (ELK.js)
  {project}_{scenario}_level1.html          # IP-level detail (ELK.js)
  {project}_{scenario}_level2.html          # Module-level detail (ELK.js, color-coded)
  {project}_{scenario}_level3.html          # Connection detail (ELK.js)
  {project}_{scenario}_task_topology.html    # Task DAG topology (ELK.js)
  {project}_{scenario}_top.puml             # PlantUML top view
  {project}_{scenario}_level1.puml          # PlantUML level 1
  {project}_{scenario}_level2.puml          # PlantUML level 2
  {project}_{scenario}_level3.puml          # PlantUML level 3
  {project}_{scenario}_task_topology.puml   # PlantUML task topology

output_simulation/
  {project}_{scenario}_gantt.html           # Gantt chart (Plotly CDN, ~11KB)
  {project}_{scenario}_gantt.png            # Gantt chart PNG (kaleido)
  {project}_{scenario}_bw.html              # BW timeline chart (Plotly CDN)
  {project}_{scenario}_bw.png               # BW chart PNG (kaleido)
  {project}_{scenario}_report.html          # Simulation report (pastel style)
  {project}_{scenario}_report.md            # Simulation report (Markdown)
  {project}_{scenario}_results.csv          # Simulation results
  {project}_{scenario}_trace.json           # Perfetto trace format

output_exploration/                           # --explore flag
  {project}_{scenario}_exploration.html     # Exploration report (HTML, SVG chart)
  {project}_{scenario}_exploration.md       # Exploration report (Markdown)
```

## Project Structure

```
├── src/
│   ├── model/
│   │   ├── hw_nodes.py       # HWNode hierarchy (Sensor, IP, Processor, Memory)
│   │   ├── modules.py        # Module system (Scaler, Crop, DMA, Generic)
│   │   ├── scenario.py       # ScenarioGraph (DAG, tasks, edges)
│   │   ├── hw_info.py        # CSV-based HW info & DVFS database
│   │   ├── hw_resolver.py    # DVFS voltage/clock resolution & power calculation
│   │   └── tokens.py         # Token-based dataflow model
│   ├── controller/
│   │   ├── simulator.py      # SoCSimulator (SimPy engine)
│   │   ├── exploration.py    # ExplorationEngine (DVFS/Mode sweep)
│   │   ├── performance_analyzer.py
│   │   ├── power_analyzer.py
│   │   └── timing_analyzer.py
│   └── view/
│       ├── text_view.py      # TextViewer (terminal output)
│       ├── visualizer.py     # Gantt/BW chart (Plotly) + PNG export
│       ├── report_generator.py # HTML/Markdown simulation reports
│       ├── exploration_report.py # Exploration HTML/MD reports (SVG chart)
│       ├── html_view.py      # HTML views (ELK.js)
│       └── plantuml_view.py  # PlantUML views
├── hw_config/
│   ├── projectA_hw.yaml      # Hardware configuration
│   ├── projectA_info.csv     # IP performance info
│   └── projectA_dvfs.csv     # DVFS voltage tables
├── scenario_config/
│   ├── projectA_FHD30_recording_scenario.yaml
│   └── exploration_FHD30.yaml  # Exploration sweep config
├── tests/                    # Unit & Integration tests (131 tests)
├── main.py                   # Entry point
├── DESIGN.md                 # Design document
└── requirements.txt          # Dependencies
```

## HW Node Types

| Type | Description | Key Parameters |
|------|-------------|----------------|
| **SensorNode** | Image sensor | `fps`, `v_valid_time`, `sensor_mode` |
| **IPNode** | Pixel processing (ISP, Codec) | `ppc`, `efficiency`, `clock_freq` |
| **ProcessorNode** | CPU/DSP/NPU | `cycles_per_op`, `num_cores` |
| **MemoryNode** | DRAM/SRAM | `bandwidth`, `capacity` |

## Connection Types

| Type | Description | Timing |
|------|-------------|--------|
| **M2M** | Memory-to-Memory (Sequential) | `Time(A) + Time(B)` |
| **OTF** | On-The-Fly (Pipelined) | `max(Time(A), Time(B))` |

## CSV-based HW Configuration

Performance info and DVFS tables are managed via CSV files:

```bash
# projectA_info.csv — IP performance parameters
IP_Name,Type,PPC,Efficiency,Max_Clock,...

# projectA_dvfs.csv — DVFS voltage/frequency tables
Domain,Level,Frequency,Voltage_ASV0,...,Voltage_ASV15
```

## Testing

```bash
# Run all tests (131 tests)
pytest tests/ -v

# Quick run
pytest tests/ -q
```

## Dependencies

- Python 3.10+
- SimPy >= 4.0
- NetworkX >= 3.0
- Pandas >= 2.0
- Plotly >= 5.0
- PyYAML >= 6.0
- Kaleido >= 1.0 (PNG export)

## Documentation

- [DESIGN.md](DESIGN.md) - Detailed design document

## License

MIT License

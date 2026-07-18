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

### Run Simulation (HTML outputs only — default)

```bash
python main.py -hw hw_config/projectA_hw.yaml \
               -sc scenario_config/projectA_FHD30_recording_scenario.yaml \
               --hw-info hw_config/projectA_info.csv \
               --hw-dvfs hw_config/projectA_dvfs.csv
```

### Selective Output

```bash
# View only (HTML, no simulation)
python main.py -hw ... -sc ... --hw-info ... --hw-dvfs ... --view --graph-only

# Gantt + CSV only
python main.py -hw ... -sc ... --hw-info ... --hw-dvfs ... --gantt --csv

# All outputs with all formats (HTML + PlantUML + PNG + MD)
python main.py -hw ... -sc ... --hw-info ... --hw-dvfs ... --all-formats
```

### Generate Scenario YAML from CSV

작성하기 어려운 500줄 이상의 복잡한 YAML 파일 대신 사람이 읽기 쉽고 관리하기 쉬운 **CSV 테이블로부터 YAML 파일을 생성**할 수 있습니다. `meta`, `ports`(포트 및 연결 통합), `sw_tasks` (선택) 등 2~3개의 단일화된 형식 파일 단위로 관리할 수 있습니다.

```bash
# 가장 간편한 방식 (FHD30_recording 단어로 시작하는 모든 CSV 자동 탐색)
python main.py --generate-scenario \
    --csv-prefix scenario_config/csv_examples/FHD30_recording \
    --format both

# 낱개 파일별로 명시하는 방식 (compact 모드만 출력)
python main.py --generate-scenario \
    --csv-meta my_scenario_meta.csv \
    --csv-ports my_scenario_ports.csv \
    --csv-sw-tasks my_sw_tasks.csv \
    --output custom_out.yaml \
    --format compact
```

* 제공되는 예제 파일 경로: `scenario_config/csv_examples/`

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

### CSV to YAML Generation Flags

| Flag | Description |
|------|-------------|
| `--generate-scenario` | Generate scenario YAML from CSV files |
| `--csv-prefix` | Prefix to auto-discover CSV files (e.g., `scenario_config/csv_examples/FHD30_recording`) |
| `--csv-meta` | Path to explicit meta CSV file |
| `--csv-ports` | Path to explicit ports CSV file |
| `--csv-sw-tasks` | Path to explicit SW tasks CSV file |
| `--format` | Output format: `compact`, `normal`, `both` (default) |
| `--output-yaml` | Output yaml path. Derived from meta name if empty |

### Output Selection Flags

| Flag | Description |
|------|-------------|
| `--view` | Generate HTML view files (Top/Level1/Level2/Level3) |
| `--gantt` | Generate Gantt chart HTML |
| `--bw` | Generate Bandwidth timeline chart HTML |
| `--csv` | Export simulation results to CSV |
| `--json` | Export trace data to Perfetto JSON format |
| `--output-view-dir` | Output directory for views (default: `output_view`) |
| `--output-sim-dir` | Output directory for simulation (default: `output_simulation`) |

> **Default behavior**: No flags specified → generate ALL outputs (HTML only).

### Format Flags (opt-in additional formats)

| Flag | Description |
|------|-------------|
| `--puml` | Also generate PlantUML (.puml) view files |
| `--png` | Also export charts as PNG (requires kaleido) |
| `--md` | Also generate Markdown reports |
| `--all-formats` | Generate all formats (HTML + PlantUML + PNG + MD) |

> **Verbose mode**: By default, only file-save messages are printed. Use `-v` for full diagnostic output.

## Output Structure

파일명 규칙: `{project}-{scenario}-{YYYYMMDD-HHMMSS}-{writer}_suffix.ext`
- `writer`: scenario YAML의 `writer` 필드 (기본값: `anonymous`)
- 알파벳순 정렬 시 **프로젝트 → 시나리오 → 날짜순** 자동 정렬

```
output_view/
  {prefix}top_view.html              # Top-level block diagram (ELK.js)
  {prefix}level1_view.html           # IP-level detail (ELK.js)
  {prefix}level2_view.html           # Module-level detail (ELK.js, color-coded)
  {prefix}level3_view.html           # Connection detail (ELK.js)
  {prefix}task_topology_view.html    # Task DAG topology (ELK.js)
  {prefix}*.puml                     # PlantUML views (--puml or --all-formats)

output_simulation/
  {prefix}timing_chart.html          # Gantt chart (Plotly CDN)
  {prefix}bw_chart.html              # BW timeline chart (Plotly CDN)
  {prefix}simulation_result.html     # Simulation report (pastel style)
  {prefix}results.csv                # Simulation results
  {prefix}trace.json                 # Perfetto trace format
  {prefix}*.png                      # Chart PNGs (--png or --all-formats)
  {prefix}simulation_result.md       # Report Markdown (--md or --all-formats)

output_exploration/                    # --explore flag
  {prefix}exploration_result.html    # Exploration report (HTML, SVG chart)
  {prefix}exploration_result.md      # Exploration Markdown (--md or --all-formats)
```

> `{prefix}` = `projectA-FHD30_Recording-20260218-014100-YHJOO_`

## GitHub Pages Report Publishing

시뮬레이션 리포트를 GitHub Pages에 **선택적으로** 누적 배포하는 워크플로우입니다.
각자 PC에서 리포트를 push하면 **GitHub Action이 index.html을 자동 생성**합니다.

> **Jekyll 불필요** — 순수 HTML 배포 (`docs/.nojekyll` 포함)

### Workflow (각 사용자)

```
1. 시뮬레이션 실행
   python main.py -hw ... -sc ...

2. 결과 확인 후 docs/reports/에 복사
   python publish_report.py                          # 전체 publish
   python publish_report.py --filter "20260218"      # 특정 날짜만
   python publish_report.py --dry-run                # 미리보기

3. 리포트만 commit & push (index.html은 커밋하지 않음!)
   git add docs/reports/
   git commit -m "Add simulation reports"
   git push

4. GitHub Action 자동 실행
   → index.html 재생성 → GitHub Pages 배포
```

> ⚠ **`docs/index.html`은 로컬에서 커밋하지 마세요!** GitHub Action이 자동 생성합니다.  
> 여러 사람이 동시에 push해도 index.html 충돌 없이 안전합니다.

### Scenario YAML에 Writer 추가

```yaml
name: "FHD30_Recording"
writer: "YHJOO"          # 리포트 파일명에 포함 (기본값: anonymous)
```

### Report Publishing 도구

| Script | 설명 |
|--------|------|
| `publish_report.py` | output_*/에서 docs/reports/{project}/로 리포트 복사 |
| `generate_index.py` | docs/reports/ 스캔 후 docs/index.html 생성 (Action용) |

```bash
# 전체 publish
python publish_report.py

# 특정 실행만 publish
python publish_report.py --filter "YHJOO"

# 미리보기
python publish_report.py --dry-run

# 로컬에서 index 미리 확인하고 싶을 때 (커밋하지 않음)
python publish_report.py --local-index
```

### GitHub Pages 디렉토리 구조

```
docs/
├── .nojekyll                      ← Jekyll 비활성화
├── index.html                     ← GitHub Action이 자동 생성 (dark theme)
└── reports/
    └── projectA/                  ← 각 사용자가 push
        ├── projectA-FHD30_Recording-20260218-014100-YHJOO_simulation_result.html
        ├── projectA-FHD30_Recording-20260217-100000-hanjun_simulation_result.html
        └── ...
```

### GitHub Pages 설정

1. Repository Settings → Pages → Source: **GitHub Actions**
2. `.github/workflows/deploy-reports.yml`이 `docs/reports/` 변경 시 자동 배포
3. Jekyll 설정 불필요 (`docs/.nojekyll` 포함)

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
├── tests/                    # Unit & Integration tests
├── main.py                   # Entry point
├── generate_index.py         # GitHub Pages index.html generator
├── publish_report.py         # Report publishing helper
├── .github/workflows/
│   └── deploy-reports.yml    # GitHub Pages auto-deploy
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

## Scenario Configuration

시나리오 YAML에서 시뮬레이션 동작을 세밀하게 제어할 수 있습니다.

### Global Parameters

시나리오 최상위에 정의하며 전체 시뮬레이션에 적용됩니다.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `name` | — | 시나리오 이름 (출력 파일명에 사용) |
| `writer` | `anonymous` | 작성자 (publish 파일명에 포함) |
| `num_frames` | `1` | 멀티프레임 시뮬레이션 프레임 수 |
| `asv_group` | `4` | DVFS 전압 룩업용 ASV 그룹 |
| `sw_margin` | `0.15` | Clock 계산 시 SW 마진 (`req_clock = pixels×fps / (1-sw_margin) / ppc`) |
| `h_blank_margin` | `0.05` | Horizontal blanking 마진 |
| `bw_power` | `80` | BW 전력 계수 (mW/GB/s) |
| `vBat` | `4.0` | 배터리 전압 (V), mA 환산에 사용 |
| `pmic_efficiency` | `0.85` | PMIC 효율, 전류 환산에 사용 |
| `bw_margin` | `1.25` | MIF 레벨 결정 시 BW 마진 |
| `mem_util` | `0.55` | MIF BW 계산용 메모리 활용률 |
| `mif_channel_width` | `16` | MIF 채널 폭 (bytes) |

```yaml
# 예시
name: "FHD30_Recording"
writer: "YHJOO"
num_frames: 1
asv_group: 4
sw_margin: 0.25
h_blank_margin: 0.05
bw_power: 80
vBat: 4.0
pmic_efficiency: 0.85
bw_margin: 1.25
mem_util: 0.55
mif_channel_width: 16
```

### Per-IP Overrides (`ip_settings`)

각 IP 블록의 `ip_settings`에서 개별 IP 동작을 오버라이드합니다.

| Field | Description |
|-------|-------------|
| `mode` | IP 동작 모드 (예: `Normal`, `LowPower`). 성능/전력 계산에 반영 |
| `manual_clock` | **수동 클럭 오버라이드 (MHz)**. DVFS 자동 계산보다 우선 적용. 리포트에 🟢 표시 |
| `manual_hw_time` | **수동 처리 시간 (ms)**. PPC 기반 계산 대신 사용 (**타이밍 다이어그램에만** 적용, BW/전력 미반영) |
| `inputs` / `outputs` | 포트별 size, format, bitwidth, comp, comp_ratio 정의 → BW 계산에 사용 |

```yaml
ip_blocks:
  - ip_settings:
      hw: "MTNR0"
      mode: "Normal"
      manual_clock: 533       # DVFS 자동 계산 무시, 533MHz 강제 적용
      manual_hw_time: 8.5     # PPC 기반 계산 무시, 8.5ms로 Gantt에 표시
      inputs:
        - port: "L0_RDMA"
          size: [0, 0, 1920, 1080]
          format: "YUV444"
          bitwidth: 14
```

> **`manual_clock` 동작**: 설정 시 `required_clock` 계산을 건너뛰고 해당 값을 `set_clock`으로 사용합니다. 같은 VDD 도메인의 다른 IP가 더 높은 클럭을 요구하면 도메인 내 최대값이 적용됩니다. Simulation Report에서 수동 클럭이 적용된 IP는 🟢 아이콘으로 구분됩니다.

> **`manual_hw_time` 동작**: PPC 기반 `processing_time` 계산을 무시하고 지정된 시간으로 Gantt 차트에 표시합니다. **BW/전력 계산에는 영향을 주지 않으며**, 프로그래밍 가능한 IP처럼 PPC로 시간을 추정할 수 없는 HW에 사용합니다.

**예시: MFC (Video Encoder)처럼 PPC 기반 추정이 불가능한 IP**

```yaml
ip_blocks:
  # MFC: 프로그래밍 기반 IP → PPC 계산 불가, 실측 시간 사용
  - ip_settings:
      hw: "MFC"
      mode: "Normal"
      manual_hw_time: 8.5       # 실측 기반 8.5ms → Gantt에 반영
      inputs:
        - port: "MFC_RDMA"
          size: [0, 0, 1920, 1080]
          format: "YUV420"
          bitwidth: 10
      outputs:
        - port: "MFC_WDMA"
          size: [0, 0, 40000, 1000]
          format: "STAT"
          bitwidth: 10

  # MCSC: 일반 IP → PPC 기반 자동 계산 (manual_hw_time 불필요)
  - ip_settings:
      hw: "MCSC"
      mode: "Normal"
      inputs:
        - port: "CINFIFO"
          size: [0, 0, 1920, 1080]
```

### Sensor Configuration

센서 설정은 두 가지 방식을 지원합니다:

```yaml
# 방법 1: sensor_config.yaml 참조 (권장)
sensor:
  hw: "HP2"
  mode: "mode1"        # sensor_config.yaml에서 size/fps/v_valid 자동 결정

# 방법 2: 인라인 직접 지정
sensor:
  hw: "Sensor_Ext"
  output_size: [0, 0, 3840, 2160]
  fps: 30.0
  sensor_mode: "4K_30fps"
  v_valid_time: 0.0118
```

### Config Paths (자동 경로 해석)

`-sc`만 지정하면 나머지 설정 파일 경로를 자동으로 찾습니다. CLI 인자가 우선합니다.

```yaml
config_paths:
  hw_config: "../hw_config/projectA_hw.yaml"
  sensor_config: "../hw_config/sensor_config.yaml"
  hw_info: "../hw_config/projectA_info.csv"
  hw_dvfs: "../hw_config/projectA_dvfs.csv"
```

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
# Run all tests
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

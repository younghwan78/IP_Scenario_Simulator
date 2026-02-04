# SoC Multimedia Architecture Simulator - Design Document

## 1. Overview

### 1.1 Purpose

Android 기반 SoC의 Multimedia IP(Camera, Video, Display, GPU, NPU 등)들의 동작을 시뮬레이션하여 **Performance, Latency, Power**를 예측하는 Discrete Event Simulator입니다.

### 1.2 Key Design Goals

| Goal | Description |
|------|-------------|
| **Event-Driven** | SimPy 기반의 이벤트 중심 시뮬레이션 |
| **DAG-Based Modeling** | NetworkX를 활용한 Task 의존성 그래프 |
| **OTF/M2M Support** | 파이프라인(OTF)과 메모리 기반(M2M) 데이터 흐름 지원 |
| **Extensibility** | MVC 패턴으로 확장성 확보 |
| **Modularity** | HW Config와 Scenario Config 분리 |

### 1.3 Technology Stack

```
┌─────────────────────────────────────────────────────────┐
│                    Application Layer                     │
├─────────────────────────────────────────────────────────┤
│  main.py │ YAML Loader │ CLI Interface                  │
├─────────────────────────────────────────────────────────┤
│                      MVC Architecture                    │
├──────────────┬──────────────────┬───────────────────────┤
│    Model     │    Controller    │         View          │
│  (hw_nodes)  │   (simulator)    │    (text_view)        │
│  (modules)   │   (analyzers)    │    (visualizer)       │
│  (scenario)  │                  │                       │
├──────────────┴──────────────────┴───────────────────────┤
│                    Core Libraries                        │
│  SimPy (Events) │ NetworkX (Graph) │ Pandas (Data)      │
│  Plotly (Viz)   │ PyYAML (Config)  │ NumPy (Compute)    │
└─────────────────────────────────────────────────────────┘
```

---

## 2. Architecture

### 2.1 MVC Pattern

```mermaid
flowchart TB
    subgraph Model["Model Layer"]
        HW[HWNode Hierarchy]
        MOD[Module System]
        SC[ScenarioGraph]
    end
    
    subgraph Controller["Controller Layer"]
        SIM[SoCSimulator]
        PERF[PerformanceAnalyzer]
        PWR[PowerAnalyzer]
        TIM[TimingAnalyzer]
    end
    
    subgraph View["View Layer"]
        TXT[TextViewer]
        MON[Monitor]
        VIZ[Visualizer]
    end
    
    HW --> SIM
    MOD --> HW
    SC --> SIM
    SIM --> PERF
    SIM --> PWR
    SIM --> TIM
    SIM --> MON
    MON --> VIZ
    MON --> TXT
```

### 2.2 Data Flow

```mermaid
sequenceDiagram
    participant User
    participant Main
    participant Loader
    participant Simulator
    participant SimPy
    participant Analyzer
    participant View
    
    User->>Main: Run with config
    Main->>Loader: Load YAML files
    Loader->>Main: HWNodes + Scenario
    Main->>Simulator: register_hw() + load_scenario()
    Main->>Simulator: run()
    
    loop For each Task
        Simulator->>SimPy: Create process
        SimPy->>SimPy: Wait for dependencies
        SimPy->>SimPy: Execute (timeout)
        SimPy->>Simulator: Record result
    end
    
    Simulator->>Main: SimulationResults
    Main->>Analyzer: analyze()
    Analyzer->>Main: Report
    Main->>View: Display/Export
    View->>User: Output (Text/CSV/Gantt)
```

---

## 3. Model Layer

### 3.1 Hardware Node Hierarchy

```mermaid
classDiagram
    class HWNode {
        <<abstract>>
        +name: str
        +clock_freq: float
        +power_static: float
        +power_dynamic: float
        +utilization: float
        +extra_attrs: Dict
        +get_processing_time(workload)* float
        +get_power_consumption(duration) float
        +get_attr(key) Any
        +set_attr(key, value)
    }
    
    class IPNode {
        +ppc: float
        +efficiency: float
        +modules: List~Module~
        +add_module(module) IPNode
        +get_processing_time(workload) float
    }
    
    class DMANode {
        +bandwidth: float
        +multiple_outstanding: int
        +burst_length: int
        +latency: float
        +get_transfer_time(data_size) float
    }
    
    class ProcessorNode {
        +cycles_per_op: float
        +num_cores: int
        +get_processing_time(workload) float
    }
    
    class MemoryNode {
        +bandwidth: float
        +capacity: int
        +access_latency: float
    }
    
    HWNode <|-- IPNode
    HWNode <|-- DMANode
    HWNode <|-- ProcessorNode
    HWNode <|-- MemoryNode
```

#### Processing Time Formulas

| Node Type | Formula | Units |
|-----------|---------|-------|
| **IPNode** | `pixels / (clock_freq × ppc × efficiency)` | seconds |
| **DMANode** | `latency + (data_size / effective_bandwidth)` | seconds |
| **ProcessorNode** | `(ops × cycles_per_op) / (clock_freq × cores)` | seconds |
| **MemoryNode** | `access_latency + (data_size / bandwidth)` | seconds |

#### Extensible Attributes

`extra_attrs` 딕셔너리를 통해 확장 속성을 지원합니다:

```python
dma = DMANode(name="DMA_Read", bandwidth=25.6e9)
dma.set_attr('qos_level', 'high')
dma.set_attr('priority', 1)
dma.set_attr('arbiter_weight', 0.5)
```

### 3.2 Module System

IP 내부의 functional unit을 모델링합니다. 모듈은 parent IP로부터 clock을 상속받습니다.

```mermaid
classDiagram
    class Module {
        <<abstract>>
        +name: str
        +parent_ip: IPNode
        +input_size: Tuple
        +output_size: Tuple
        +ppc: float
        +efficiency: float
        +get_clock_freq() float
        +calculate_output_size(input)* Tuple
        +get_processing_time(workload) float
    }
    
    class ScalerModule {
        +scale_factor: Tuple~float, float~
        +calculate_output_size(input) Tuple
    }
    
    class CropModule {
        +crop_region: Tuple~int, int, int, int~
        +calculate_output_size(input) Tuple
        +set_crop_region(x, y, w, h)
    }
    
    class GenericModule {
        +calculate_output_size(input) Tuple
    }
    
    Module <|-- ScalerModule
    Module <|-- CropModule
    Module <|-- GenericModule
    
    IPNode "1" *-- "*" Module : contains
```

#### Input/Output Size Transformation

```python
# Scaler: 입력 크기에 scale_factor 적용
scaler = ScalerModule(name="Scaler0", scale_factor=(0.5, 0.5))
scaler.set_input_size(1920, 1080)
# output_size = (960, 540)

# Crop: 지정된 영역 추출
crop = CropModule(name="Crop0", crop_region=(100, 100, 800, 600))
crop.set_input_size(1920, 1080)
# output_size = (800, 600)
```

### 3.3 Scenario Graph

NetworkX DiGraph를 사용하여 Task 의존성을 모델링합니다.

```mermaid
classDiagram
    class ScenarioGraph {
        +name: str
        +graph: nx.DiGraph
        -_tasks: Dict~str, Task~
        +add_task(task_id, mapped_hw, workload)
        +add_dependency(src, dst, conn_type)
        +get_predecessors(task_id) List
        +get_otf_groups() List~List~
        +get_m2m_dependencies() List
        +validate() Tuple~bool, List~
        +topological_order() List
    }
    
    class Task {
        +task_id: str
        +mapped_hw: str
        +workload: Dict
        +get_pixels() int
        +get_ops() int
    }
    
    class ConnectionType {
        <<enumeration>>
        M2M
        OTF
    }
    
    ScenarioGraph "1" *-- "*" Task
    ScenarioGraph ..> ConnectionType
```

#### Connection Types

| Type | Description | Start Condition | End Timing |
|------|-------------|-----------------|------------|
| **M2M** | Memory-to-Memory (Sequential) | Predecessor 완료 후 | 개별 처리 시간 |
| **OTF** | On-The-Fly (Pipelined) | 동시 시작 | `max(Time_A, Time_B)` |

---

## 4. Controller Layer

### 4.1 SoCSimulator

SimPy 기반의 메인 시뮬레이션 엔진입니다.

```mermaid
classDiagram
    class SoCSimulator {
        +env: simpy.Environment
        +hw_registry: Dict~str, HWNode~
        +scenario: ScenarioGraph
        +analyzers: List~BaseAnalyzer~
        +register_hw(node) SoCSimulator
        +load_scenario(scenario) SoCSimulator
        +add_analyzer(analyzer) SoCSimulator
        +run() SimulationResults
        +run_with_analysis() Dict
        -_run_task_process(task) Generator
        -_run_otf_group_process(group) Generator
    }
    
    class SimulationResults {
        +scenario_name: str
        +total_time: float
        +task_results: List~TaskResult~
        +get_by_hw(name) List
        +get_by_task(id) TaskResult
        +get_total_power() float
    }
    
    class TaskResult {
        +task_id: str
        +hw_name: str
        +start_time: float
        +end_time: float
        +duration: float
        +power_consumed: float
        +workload: Dict
    }
    
    SoCSimulator --> SimulationResults
    SimulationResults *-- TaskResult
```

#### Simulation Process Flow

```mermaid
flowchart TD
    START([run()]) --> VALIDATE[Validate Scenario]
    VALIDATE --> INIT[Initialize SimPy Environment]
    INIT --> EVENTS[Create Task Events]
    EVENTS --> FIND_OTF[Find OTF Groups]
    
    FIND_OTF --> SCHEDULE_OTF[Schedule OTF Groups]
    FIND_OTF --> SCHEDULE_TASKS[Schedule Non-OTF Tasks]
    
    SCHEDULE_OTF --> RUN[env.run()]
    SCHEDULE_TASKS --> RUN
    
    RUN --> COLLECT[Collect Results]
    COLLECT --> RETURN([Return SimulationResults])
    
    subgraph "OTF Group Process"
        OTF_WAIT[Wait for M2M predecessors]
        OTF_START[Start all tasks simultaneously]
        OTF_CALC[Calculate max processing time]
        OTF_TIMEOUT[Timeout for max time]
        OTF_RECORD[Record all results]
        
        OTF_WAIT --> OTF_START --> OTF_CALC --> OTF_TIMEOUT --> OTF_RECORD
    end
    
    subgraph "Task Process"
        TASK_WAIT[Wait for predecessor events]
        TASK_REQUEST[Request HW resource]
        TASK_PROCESS[Process workload]
        TASK_RELEASE[Release resource]
        TASK_SIGNAL[Signal completion]
        
        TASK_WAIT --> TASK_REQUEST --> TASK_PROCESS --> TASK_RELEASE --> TASK_SIGNAL
    end
```

### 4.2 Analyzers

분석 기능은 플러그인 패턴으로 분리되어 있습니다.

```mermaid
classDiagram
    class BaseAnalyzer {
        <<abstract>>
        +analyze(results)* Dict
    }
    
    class PerformanceAnalyzer {
        +analyze(results) Dict
        +format_report(report) str
    }
    
    class PowerAnalyzer {
        +analyze(results) Dict
        +format_report(report) str
    }
    
    class TimingAnalyzer {
        +analyze(results) Dict
        +format_report(report) str
    }
    
    BaseAnalyzer <|-- PerformanceAnalyzer
    BaseAnalyzer <|-- PowerAnalyzer
    BaseAnalyzer <|-- TimingAnalyzer
```

#### Analysis Metrics

| Analyzer | Metrics |
|----------|---------|
| **PerformanceAnalyzer** | Throughput (tasks/sec), FPS, Utilization, Bottleneck |
| **PowerAnalyzer** | Total Energy (mJ), Per-HW breakdown, Average Power |
| **TimingAnalyzer** | Latency, Critical Path, Task timing breakdown |

---

## 5. View Layer

### 5.1 TextViewer

텍스트 기반의 구조 출력을 담당합니다.

```python
viewer = TextViewer()

# Hardware Hierarchy 출력
print(viewer.print_hw_hierarchy(hw_registry))

# Scenario Graph 출력
print(viewer.print_scenario_graph(scenario))

# Simulation Results 출력
print(viewer.print_simulation_summary(results))
```

**출력 예시:**

```
[SoC Hardware Hierarchy]
├── ISP_FE (IPNode, 600MHz, PPC=4)
│   ├── Scaler0 (ScalerModule, scale=0.50x0.50)
│   └── Crop0 (CropModule, region=(0,0,1920,1080))
├── VENC (IPNode, 400MHz, PPC=1)
└── DMA_Read (DMANode, MO=16, BW=25.6GB/s)
```

### 5.2 Monitor & Visualizer

```mermaid
classDiagram
    class Monitor {
        +records: List~TaskRecord~
        +record(task_id, hw, start, end, power)
        +to_dataframe() DataFrame
        +from_simulation_results(results)
        +export_csv(path)
    }
    
    class Visualizer {
        +create_gantt_chart(df) Figure
        +create_gantt_chart_ms(df) Figure
        +save_gantt(fig, path)
        +show(fig)
    }
    
    Monitor --> Visualizer : provides data
```

---

## 6. Configuration

### 6.1 Hardware Configuration (hw_config/)

```yaml
hardware:
  - name: "ISP_FE"
    type: "IP"
    clock: 600000000      # 600 MHz
    ppc: 4                # Pixels Per Clock
    efficiency: 0.95
    power_static: 15.0    # mW
    power_dynamic: 80.0   # mW
    modules:
      - name: "Scaler0"
        type: "Scaler"
        scale_factor: [0.5, 0.5]

  - name: "DMA_Read"
    type: "DMA"
    bandwidth: 25600000000    # 25.6 GB/s
    multiple_outstanding: 16  # MO
    burst_length: 256
    latency: 0.0000001        # 100ns
```

### 6.2 Scenario Configuration (scenario_config/)

```yaml
scenario:
  name: "4K_Recording"
  
  tasks:
    - id: "t_sensor"
      hw: "Sensor_Ext"
      pixels: 8294400     # 3840 × 2160
      
    - id: "t_isp_fe"
      hw: "ISP_FE"
      pixels: 8294400

  edges:
    - src: "t_sensor"
      dst: "t_isp_fe"
      type: "OTF"         # Pipelined
      
    - src: "t_isp_fe"
      dst: "t_isp_be"
      type: "M2M"         # Sequential
      buffer_size: 33177600
```

---

## 7. Key Algorithms

### 7.1 OTF Group Detection

OTF로 연결된 task들을 그룹으로 묶어 동기화 실행합니다.

```python
def get_otf_groups(self) -> List[List[str]]:
    # 1. OTF 엣지만 추출
    otf_edges = [(u, v) for u, v, d in self.graph.edges(data=True)
                 if d.get('conn_type') == ConnectionType.OTF]
    
    # 2. OTF 서브그래프 생성
    otf_subgraph = nx.DiGraph()
    otf_subgraph.add_edges_from(otf_edges)
    
    # 3. Weakly Connected Components 찾기
    groups = list(nx.weakly_connected_components(otf_subgraph))
    
    return [list(g) for g in groups]
```

### 7.2 OTF Pipeline Simulation

```python
def _run_otf_group_process(self, group: List[str]) -> Generator:
    # 1. M2M predecessors 대기
    for pred_id in all_m2m_predecessors:
        yield self._task_events[pred_id]
    
    # 2. 모든 task의 처리 시간 계산
    processing_times = []
    for task in tasks:
        hw = self._get_hw(task.mapped_hw)
        pt = hw.get_processing_time(task.workload)
        processing_times.append(pt)
    
    # 3. Bottleneck: 가장 느린 시간으로 동기화
    max_time = max(processing_times)
    
    # 4. 동시에 종료
    yield self.env.timeout(max_time)
    
    # 5. 결과 기록 (모두 같은 start/end time)
    for task in tasks:
        self._task_events[task.task_id].succeed()
```

### 7.3 Resource Contention

SimPy Resource를 사용하여 동일 HW에 대한 동시 접근을 제어합니다.

```python
# HW별 Resource 생성
hw.resource = simpy.Resource(self.env, capacity=1)

# Task 실행 시 리소스 요청
with hw.resource.request() as req:
    yield req  # 리소스 사용 가능할 때까지 대기
    yield self.env.timeout(processing_time)
# 자동 해제
```

---

## 8. Extension Points

### 8.1 Custom HW Node 추가

```python
@dataclass
class GPUNode(HWNode):
    shader_units: int = 128
    texture_units: int = 8
    
    def get_processing_time(self, workload: Dict[str, Any]) -> float:
        triangles = workload.get('triangles', 0)
        texels = workload.get('texels', 0)
        
        shader_time = triangles / (self.clock_freq * self.shader_units)
        texture_time = texels / (self.clock_freq * self.texture_units)
        
        return max(shader_time, texture_time)
```

### 8.2 Custom Analyzer 추가

```python
class BandwidthAnalyzer(BaseAnalyzer):
    def analyze(self, results: SimulationResults) -> Dict[str, Any]:
        # DMA task들의 bandwidth 사용량 분석
        ...
        return {
            'peak_bandwidth': peak_bw,
            'average_bandwidth': avg_bw,
            'utilization': bw_util
        }
```

### 8.3 Custom Module 추가

```python
@dataclass
class RotatorModule(Module):
    rotation_angle: int = 0  # 0, 90, 180, 270
    
    def calculate_output_size(self, input_size: Tuple[int, int]) -> Tuple[int, int]:
        w, h = input_size
        if self.rotation_angle in [90, 270]:
            return (h, w)  # Swap width/height
        return (w, h)
```

---

## 9. Testing Strategy

### 9.1 Test Categories

| Category | Files | Count | Focus |
|----------|-------|-------|-------|
| Model | `test_model.py` | 15 | HW nodes, Modules, Scenario |
| Controller | `test_controller.py` | 8 | Simulator, Analyzers |
| View | `test_view.py` | 8 | Text output, Export |
| Integration | `test_integration.py` | 8 | Full pipeline |

### 9.2 Key Test Cases

```python
# 1. Processing Time Verification
def test_processing_time_calculation():
    # 600MHz, 4PPC, 4K → 3.456ms
    ip = IPNode(clock_freq=600e6, ppc=4)
    time = ip.get_processing_time({'pixels': 8294400})
    assert abs(time - 0.003456) < 1e-9

# 2. OTF Bottleneck
def test_fps_bottleneck():
    # 100fps + 30fps OTF → 30fps
    ...
    assert abs(fps - 30.0) < 0.1

# 3. M2M Sequential
def test_m2m_timing():
    # A(1s) → B(2s) → Total 3s
    ...
    assert abs(result_b.end_time - 3.0) < 0.01
```

---

## 10. Future Enhancements

| Feature | Description | Priority |
|---------|-------------|----------|
| **Multi-Frame Simulation** | 연속 프레임 시뮬레이션 | High |
| **DVFS Modeling** | Dynamic Voltage/Frequency Scaling | High |
| **Memory BW Contention** | DRAM BW 경쟁 모델링 | Medium |
| **Power State Transitions** | Clock gating, Power gating | Medium |
| **GUI Dashboard** | Interactive visualization | Low |
| **Batch Simulation** | Multiple scenario 자동 실행 | Low |

---

## Appendix: File Reference

| File | Description |
|------|-------------|
| [hw_nodes.py](file:///e:/10_Codes/23_MMIP_Scenario_simulation2/src/model/hw_nodes.py) | HWNode hierarchy |
| [modules.py](file:///e:/10_Codes/23_MMIP_Scenario_simulation2/src/model/modules.py) | Module system |
| [scenario.py](file:///e:/10_Codes/23_MMIP_Scenario_simulation2/src/model/scenario.py) | ScenarioGraph |
| [simulator.py](file:///e:/10_Codes/23_MMIP_Scenario_simulation2/src/controller/simulator.py) | SoCSimulator |
| [text_view.py](file:///e:/10_Codes/23_MMIP_Scenario_simulation2/src/view/text_view.py) | TextViewer |
| [main.py](file:///e:/10_Codes/23_MMIP_Scenario_simulation2/main.py) | Entry point |

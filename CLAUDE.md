# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SimPy 기반 Discrete Event Simulator로 Android SoC의 Multimedia IP(Camera/ISP, Codec, Display 등)의 Performance / Latency / Power를 예측한다. NetworkX DAG로 시나리오(Task 의존성)를 모델링하고, YAML(HW/시나리오) + CSV(IP 성능/DVFS 테이블) 설정을 입력으로 받아 HTML 리포트/차트/뷰를 생성한다. 원본 사양은 `작업지시서.md`, 상세 설계는 `DESIGN.md` 참조.

## Commands

가상환경은 `./venv` (Windows). 활성화: `.\venv\Scripts\activate` 또는 직접 `venv/Scripts/python` 사용.

```bash
# 전체 테스트
python -m pytest tests/ -q

# 단일 파일 / 클래스 / 함수
python -m pytest tests/test_model.py -v
python -m pytest tests/test_bw_chart.py::TestBPPMap::test_nv12_bpp -v

# 키워드 필터
python -m pytest tests/ -k "power or clock" -v

# 시뮬레이션 실행 (플래그 없음 = 모든 HTML 출력 생성)
python main.py -hw hw_config/projectA_hw.yaml \
               -sc scenario_config/projectA_FHD30_recording_scenario.yaml \
               --hw-info hw_config/projectA_info.csv \
               --hw-dvfs hw_config/projectA_dvfs.csv

# 시나리오 YAML에 config_paths가 있으면 -sc만으로 나머지 경로 자동 해석
python main.py -sc scenario_config/projectA_FHD30_recording_scenario.yaml

# 그래프 구조만 확인 (시뮬레이션 없음)
python main.py -sc ... --graph-only

# CSV → 시나리오 YAML 생성
python main.py --generate-scenario --csv-prefix scenario_config/csv_examples/FHD30_recording --format both

# 아키텍처 탐색 (DVFS/Mode 파라미터 스윕)
python main.py -sc ... --explore scenario_config/exploration_FHD30.yaml
```

출력 선택 플래그(`--view --gantt --bw --csv --json`)는 하나라도 지정하면 그것만 생성. 포맷 플래그(`--puml --png --md --all-formats`)는 HTML 외 추가 포맷 opt-in. `-v`로 상세 로그.

## Architecture

MVC 구조: `src/model` (HW/시나리오 모델), `src/controller` (SimPy 시뮬레이터 + 분석기), `src/view` (HTML/PlantUML/차트/리포트). 패키지 설치 없이 `sys.path`에 프로젝트 루트를 추가해 `from src.xxx import ...`로 임포트한다 (main.py, tests/conftest.py 모두 이 방식).

### main.py 파이프라인 (실행 순서)

1. YAML 로드 — compact 문법이면 `compact_scenario.expand_compact()`로 확장
2. `create_hw_node()` / `create_module()` — hw YAML에서 HWNode 계층(Sensor/IP/Processor/Memory)과 Module(Scaler/Crop/DMA/Generic) 생성
3. `create_scenario()` — 시나리오 YAML의 `ip_blocks`에서 Task DAG(`ScenarioGraph`) 구성
4. `apply_scenario_settings()` — `ip_settings`(mode, manual_clock, manual_hw_time, port별 size/format) 를 HW 노드에 반영
5. `sanity_check_config()` — 포트/모듈 참조 무결성 검증 (실패 메시지에 fix 힌트 포함)
6. `HWResolver.resolve_scenario()` → `apply_to_hw()` — DVFS clock/voltage 결정 (아래 참조)
7. `SoCSimulator.register_hw()` → `load_scenario()` → `run_with_analysis(num_frames)` — SimPy 실행 + Performance/Power/Timing 분석기
8. View 레이어 출력 — `html_view.py`(ELK.js 뷰), `visualizer.py`(Plotly Gantt/BW 차트), `report_generator.py`(HTML/MD 리포트)

### 핵심 도메인 개념

- **OTF vs M2M 연결** (`ScenarioGraph` edge type): OTF는 파이프라인 — `get_otf_groups()`로 묶여 그룹 단위로 시뮬레이션되며 시간은 `max(A, B)`. M2M은 순차(`A + B`)이며 DMA 전송(`_simulate_dma_transfer`)이 별도로 시뮬레이션되어 BW 차트에 반영된다.
- **HWResolver** (`src/model/hw_resolver.py`): `req_clock = pixels×fps / (1-sw_margin) / ppc`로 요구 클럭 계산 → DVFS 테이블(ASV group별 전압)에서 레벨 선택 → 같은 VDD 도메인 내 최대 클럭으로 정렬 → 전력 계산. MIF 레벨은 total DMA BW 기반 자동 결정(`mif_bw = freq × channel_width × mem_util`).
- **수동 오버라이드**: `manual_clock`은 DVFS 자동 계산을 무시하고 클럭 강제(리포트에 🟢 표시). `manual_hw_time`은 PPC 기반 시간 계산 대신 Gantt에만 반영 — BW/전력에는 영향 없음 (MFC처럼 PPC 추정 불가능한 IP용).
- **Token 모델** (`src/model/tokens.py`): FrameToken/TokenQueue/TokenJoin(AND/OR/WINDOW) 라이브러리. 시뮬레이터 실행 경로에는 아직 통합되지 않음 (`_detect_token_mode()`는 감지만 수행). 멀티프레임은 FPS 기반 frame interval로 파이프라인 중첩하며, OTF 그룹은 HW 자원을 점유해 프레임 간 경합이 모델링됨.
- **공용 BW 공식** (`src/model/bw.py`): 리포트/BW 차트/exploration이 모두 `calc_port_bw()` 하나를 공유. BW 공식 수정은 반드시 이 파일에서. `bw_mbs`는 **DRAM 유효 BW** (LLC hit 제외).
- **LLC 모델**: hw.yaml `- llc:` (capacity_mb/default_hit_ratio/power_coeff, 과제별 고정) + 시나리오 `llc_paths` (상황별 사용 여부). `main.apply_llc_settings()`가 path를 포트별 `llc_enable`/`llc_hit_ratio`로 해석·주입. `DRAM_BW = raw×(1−hit)`이 MIF 레벨/총 BW에 자동 반영.
- **시나리오 전역 파라미터** (sw_margin, bw_margin, mem_util, vBat 등)는 시나리오 YAML 최상위에 정의 — 기본값과 의미는 README의 Global Parameters 표 참조.

### 설정 파일 체계

| 파일 | 역할 |
|------|------|
| `hw_config/*_hw.yaml` | HW 노드 + 모듈 토폴로지 |
| `hw_config/*_info.csv` | IP 성능 (PPC, efficiency, max clock) — `HWInfoDB`가 파싱 |
| `hw_config/*_dvfs.csv` | Domain/Level/Frequency + ASV별 전압 테이블 |
| `hw_config/sensor_config.yaml` | 센서 모드 정의 — 시나리오에서 `hw`+`mode`로 참조하면 v_valid 자동 계산 |
| `scenario_config/*_scenario.yaml` | Task 그래프 + ip_settings + 전역 파라미터 (compact/normal 두 문법 지원) |

## Conventions & Gotchas

- 출력 파일명 규칙: `{project}-{scenario}-{YYYYMMDD-HHMMSS}-{writer}_suffix.ext` — `writer`는 시나리오 YAML 필드 (기본 `anonymous`).
- **`docs/index.html`은 커밋 금지** — GitHub Action(`deploy-reports.yml`)이 자동 생성. 리포트 배포는 `python publish_report.py`로 `docs/reports/`에 복사 후 그 디렉토리만 커밋.
- main.py는 Windows cp949 콘솔 대응으로 stdout/stderr를 UTF-8로 래핑함 — 콘솔 출력에 유니코드 박스 문자 사용 가능.
- HTML 출력은 Plotly CDN / ELK.js CDN 기반 경량 파일 — 오프라인 환경에서는 렌더링되지 않음.
- 문제 발생 시 `TROUBLESHOOTING.md`에 publish/sanity-check 관련 알려진 케이스가 정리되어 있음.

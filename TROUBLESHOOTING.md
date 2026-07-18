# Troubleshooting Guide

## Case 1: GitHub Pages Index Shows "No reports found"

### Symptom

`generate_index.py` 실행 후 (또는 GitHub Action 자동 실행 후) `index.html`이 생성되지만 **"No reports found"** 메시지만 표시됨.

### Root Cause

`generate_index.py`는 아래 정규식으로 파일명을 파싱합니다:

```
{project}-{scenario}-{YYYYMMDD-HHMMSS}-{writer}_suffix.ext
```

`main.py`가 생성하는 **원본 파일**에는 timestamp/writer가 포함되지 않습니다:

```
# ❌ 원본 파일 (generate_index에서 인식 불가)
projectA-FHD30_Recording_simulation_result.html

# ✅ 퍼블리시된 파일 (generate_index에서 인식 가능)
projectA-FHD30_Recording-20260218-022057-YHJOO_simulation_result.html
                         ^^^^^^^^^^^^^^^^ ^^^^^
                         timestamp        writer
```

원본 output 파일을 `docs/reports/{project}/`에 직접 복사하면 regex가 매칭되지 않아 "No reports found"가 발생합니다.

> **Note (2026-07 업데이트)**: `publish_report.py`가 timestamp 없는 원본 파일도
> **자동으로 timestamp(파일 mtime)·writer(`--writer`, 기본 anonymous)를 붙여 복사**하도록
> 개선되었습니다. 이제 이 케이스는 `docs/reports/`에 **수동으로** 파일을 복사한 경우에만 발생합니다.

### Solution

**방법 A: `publish_report.py` 사용 (권장)**

```bash
# output_simulation/, output_view/ 에서 자동으로 timestamp/writer를 붙여 복사
python publish_report.py --source output_simulation output_view

# 특정 실행만 필터
python publish_report.py --filter "20260218-022057"

# 미리보기
python publish_report.py --dry-run
```

**방법 B: `main.py --publish` 사용**

```bash
# 시뮬레이션 실행과 동시에 docs/reports/에 퍼블리시
python main.py -hw hw_config/projectA_hw.yaml -sc scenario_config/my_scenario.yaml --publish
```

> **Note:** `--publish` 사용 시 `scenario.yaml`에 `writer` 필드가 설정되어 있어야 합니다.
> 없으면 `anonymous`로 기본 설정됩니다.

### File Naming Convention

| Field | Source | Example |
|---|---|---|
| `project` | `hw.yaml` 파일명에서 `_hw` 제거 | `projectA` |
| `scenario` | `scenario.yaml`의 `name` 필드 | `FHD30_Recording` |
| `timestamp` | 퍼블리시 시점 자동 생성 | `20260218-022057` |
| `writer` | `scenario.yaml`의 `writer` 필드 | `YHJOO` |
| `suffix` | 출력 유형별 고정 | `simulation_result.html` |

### Directory Structure

```
docs/reports/
└── projectA/                          ← project별 서브디렉토리 필수
    ├── projectA-FHD30_Recording-20260218-022057-YHJOO_simulation_result.html
    ├── projectA-FHD30_Recording-20260218-022057-YHJOO_top_view.html
    └── ...
```

---

## Case 2: `publish_report.py`에서 "No matching report files found"

### Symptom

```
Scanning source directories: output_simulation, output_view
No matching report files found.
```

### Root Cause

`output_simulation/`, `output_view/` 디렉토리에 파일이 없거나, 시뮬레이션을 아직 실행하지 않은 상태.

### Solution

먼저 시뮬레이션을 실행하여 output 파일을 생성:

```bash
python main.py -hw hw_config/projectA_hw.yaml -sc scenario_config/my_scenario.yaml
```

그 후 퍼블리시:

```bash
python publish_report.py
```

---

## Case 3: Sanity Check 실패 — "Referenced shape does not exist"

### Symptom

시뮬레이션 실행 시 sanity check 단계에서 에러 발생, 또는 Level 3 HTML view에서 ELK.js 레이아웃 오류.

### Root Cause

`scenario.yaml`의 `ip_blocks`에서 참조하는 HW 이름이나 포트가 `hw.yaml`에 정의되어 있지 않음.

### Solution

에러 메시지를 확인하고 `hw.yaml`과 `scenario.yaml`의 이름을 맞춤:

```bash
# 에러 예시:
#   [ip_settings] Task 'ISP_FE': input port 'RDMA0' not found in 'ISP_FE' modules ['WDMA0', 'WDMA1']
#   → Fix: Check scenario.yaml ip_blocks.ip_settings.inputs 'port: RDMA0',
#          or add module 'RDMA0' to hw.yaml 'ISP_FE.modules'
```

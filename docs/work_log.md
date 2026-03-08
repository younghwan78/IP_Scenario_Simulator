# 작업일지 (Work Log)

## 2026-03-08 (토)

### 1. Level 2 View 개선 — DMA Enable/Disable 구분 + Tooltip 추가

**요구:** Level 2에서 모든 DMA를 표시하지만, 사용/미사용 DMA가 구분되지 않아 연결관계가 복잡. Scenario에 정의되지 않은 DMA는 disable로 시각 구분 필요. Level 1처럼 DMA/CIN/COUT 클릭 시 tooltip 필요.

**수정 내용:**
- **[MODIFY]** `src/view/html_view.py`
  - `_build_l2_module_detail()` 함수 추가: DMA는 direction/bandwidth/MO/SBWC/LLC/size/format, CIN/COUT은 type/size 정보 포함
  - `_l2_mod_color()`: `is_disabled` 파라미터 추가 → disabled면 회색(`#E8E8E8`) 반환
  - `generate_level2_html()`: 모든 I/O 모듈 표시 (기존: `used_ports` 필터링 → 변경: 전체 표시, `disabled` 플래그 부여)
  - `_build_cross_edges_level2()`: 모든 I/O 모듈을 mod_node_map에 포함
  - HTML JS `drawNode()`: disabled 모듈에 점선 border(`stroke-dasharray: 4,2`), 반투명(`fill-opacity: 0.5`), 회색 텍스트 적용
  - HTML JS `showTooltip()`: module-level detail(DMA/CIN/COUT)과 IP-level detail(Level 1) 이중 포맷 지원

**시각적 구분:**
| 상태 | 색상 | 테두리 | 텍스트 |
|------|------|--------|--------|
| Enabled DMA | 파란/노란/오렌지/보라 | 실선 | 진한색 |
| Disabled DMA | 회색 (#E8E8E8) | 점선 | 연한 회색 |

**DMA Tooltip 내용:** Direction, Bandwidth, MO, Size, Format, Bitwidth, Comp, SBWC, LLC, Status
**CIN/COUT Tooltip 내용:** Type, Size, Status

**검증:** 4개 smoke test 통과, Level 2 HTML 생성 확인

---

## 2026-03-04 (화)

### 1. GitHub Pages Index "No reports found" 원인 분석

**문제:** 여러 시나리오 파일을 `docs/reports/projectA/`에 직접 복사하고 GitHub Action으로 `generate_index.py`를 실행했는데 "No reports found" 표시됨.

**원인:** `main.py`가 생성하는 원본 파일은 timestamp/writer가 없는 형식: `projectA-FHD30_Recording_simulation_result.html`. 하지만 `generate_index.py`의 regex는 `{project}-{scenario}-{YYYYMMDD-HHMMSS}-{writer}_suffix.ext` 형식을 필수로 요구함.

**해결:** `publish_report.py`를 사용하면 자동으로 timestamp/writer가 붙은 파일명으로 변환됨.

**산출물:** `TROUBLESHOOTING.md` 생성 — 자주 발생하는 문제와 해결 방법 정리 (3개 케이스)

---

### 2. BPP_MAP 중복 선언 통합

**문제:** `BPP_MAP`과 `BPP_DEFAULT`가 3개 파일에 각각 다른 내용으로 중복 선언:
- `src/controller/simulator.py` (18개 format, DEFAULT=1.0)
- `src/controller/exploration.py` (16개 format, DEFAULT=1.5 ⚠️, P010=1.5 ⚠️)
- `src/view/report_generator.py` (22개 format, DEFAULT=1.0)

**수정 내용:**
- **[NEW]** `src/model/constants.py` — 3개 파일의 모든 format을 병합 (30개), `simulator.py` 값 기준
- **[MODIFY]** `simulator.py` — 로컬 선언 → `from ..model.constants import BPP_MAP, BPP_DEFAULT`
- **[MODIFY]** `exploration.py` — 로컬 선언 → import (P010: 1.5→2.0, DEFAULT: 1.5→1.0 수정됨)
- **[MODIFY]** `report_generator.py` — 로컬 선언 → import
- `visualizer.py`는 이미 `simulator.py`에서 import하므로 변경 불필요

**검증:** 46개 테스트 전부 통과

**패치:** `patch_20260304.patch` (5 files, +178 -43)

---

### 3. Hierarchy/IP Group Border Color 추가

**요구:** `HIERARCHY_COLORS`, `IP_GROUP_COLORS` 팔레트에 border color를 추가해서 HW 설계 다이어그램처럼 보이게 하기. Border는 fill보다 15% 더 어둡게.

**수정 내용:**
- **[MODIFY]** `src/view/plantuml_view.py`
  - `_darken_hex()` 유틸리티 함수 추가 (hex color를 factor만큼 darkening)
  - `HIERARCHY_BORDER_COLORS`, `IP_GROUP_BORDER_COLORS` 자동 생성 (dict comprehension)
  - `_get_hierarchy_border()`, `_get_ip_group_border()` 헬퍼 함수 추가
  - 모든 PlantUML view (Top, Level1~3, Task Topology)에서 `#fill/#border` 구문 적용
  - `Display` color 오타 수정 (`#8246F04` → `#8246F0`)
- **[MODIFY]** `src/view/html_view.py`
  - 새 border 함수 import (`_get_hierarchy_border`, `_get_ip_group_border`, `_darken_hex`)
  - 모든 meta에 `"border"` 필드 추가 (group, ip, leaf, group_box, mod)
  - SVG JS 렌더링: `m.border`가 있으면 stroke로 사용 (없으면 기존 fallback)

**검증:** 211개 테스트 전부 통과

---

### 4. SW Task 연결 시각적 분리 (M2M vs SW)

**요구:** CPU에서 동작하는 SW task (postIRTA, Codec2, HWcomposer 등)의 연결을 HW M2M과 다르게 표시. 현재 모두 "M2M"(빨간색)으로 동일하게 표시됨.

**수정 내용:**
- **[MODIFY]** `src/view/plantuml_view.py`
  - `_is_sw_edge(scenario, src_id, dst_id)` 헬퍼 추가 — edge 양 끝 중 하나라도 `is_sw_task`이면 True
  - `_SW_LINE_COLOR = "#00897B"` (teal), `_SW_DB_COLOR = "#E0F2F1"` (light teal) 상수 추가
  - 5개 edge emitter 모두 업데이트: SW edge → teal dashed arrow, "SW" 라벨, 연한 teal DB cylinder
- **[MODIFY]** `src/view/html_view.py`
  - `_is_sw_edge` import
  - 3개 edge builder 모두 업데이트: `meta[eid] = {"type": "sw", ...}`
  - JS `drawEdge`: SW → `#00897B` (teal), dashed, M2M → `#E65100` (red), dashed, OTF → `#1565C0` (blue), solid

**색상 체계:**
| 연결 | 라벨 | 색상 | 스타일 |
|------|------|------|--------|
| OTF | OTF | 파란색 (#1565C0) | 굵은 실선 |
| M2M (HW) | M2M | 빨간색 (#E65100) | 점선 |
| SW (CPU) | SW | 앰버/골드 (#F9A825) | 점선 |

**검증:** 211개 테스트 통과, 시뮬레이션 정상 실행

---

### 5. Level 1 HTML View: IP 클릭 시 DMA 포트 상세정보 팝업

**요구:** Level 1 view에서 IP의 입력 size만 표시되어 DMA별 세부 정보를 알 수 없음. IP를 클릭하면 상세 정보를 popup으로 표시.

**수정 내용:**
- **[MODIFY]** `src/view/html_view.py`
  - `_build_port_detail()` 함수 추가: ip_settings에서 inputs/outputs 포트 정보 추출 (port, size, format, bitwidth, comp)
  - Level 1 node meta에 `"detail"` 필드 추가 (tooltip 데이터)
  - CSS: 부유형 tooltip 스타일 (rounded border, shadow, 포트 테이블, 닫기 버튼)
  - JS: `showTooltip()` / `hideTooltip()` — 클릭 위치 근처에 팝업 표시
  - `_dragDist` 글로벌 변수로 드래그/클릭 구분 (마우스 이동 3회 이상이면 드래그로 판단)
  - Sensor 노드: fps 포함 센서 스펙 표시
  - SW task: CPU 프로세서 정보 표시

**팝업 내용:**
| 구분 | 표시 항목 |
|------|----------|
| ▼ Inputs | Port, Size, Format, Bitwidth, Compression |
| ▲ Outputs | Port, Size, Format, Bitwidth, Compression |

**검증:** 211개 테스트 통과, 브라우저 JS 테스트로 팝업 동작 확인

---

### 6. Report FPS Fallback 버그 수정

**문제:** `report_generator.py`에서 `sc.get('fps', 30.0)` — scenario YAML에 `fps`를 명시하지 않으면 항상 default 30.0이 사용됨. Sensor가 60fps여도 리포트에 30fps로 표시.

**원인:** `fps <= 0` 조건(fallback)이 default=30이면 절대 True가 되지 않아 sensor fps가 무시됨.

**수정:**
- **[MODIFY]** `src/view/report_generator.py`
  - `sc.get('fps')` → None 체크로 변경
  - fps 우선순위: ① scenario에 명시 → ② sensor fps → ③ 30.0 (최종 fallback)

```python
# Before
self.fps = float(sc.get('fps', 30.0))  # 항상 30
if self.fps <= 0: ...                   # 절대 실행 안 됨

# After
raw_fps = sc.get('fps')                 # None if not set
if raw_fps is not None and float(raw_fps) > 0:
    self.fps = float(raw_fps)
elif self.resolved_sensor:
    self.fps = float(self.resolved_sensor.get('fps', 30.0))
```

**참고:** `hw_resolver.py`의 `_get_fps()`는 이미 sensor 우선 로직 정상 동작 (target frequency 계산 정확)

**검증:** 211개 테스트 통과

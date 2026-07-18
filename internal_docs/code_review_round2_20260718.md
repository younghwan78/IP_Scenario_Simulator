# Code Review Round 2 — 추가 개선 후보

> **적용 현황 (2026-07-18)**: 전 항목 적용 완료. 회귀 테스트 15건을
> `tests/test_round2_fixes.py`로 추가해 **264 passed**, `ruff check` 클린.
> 세부 사항:
> - **A-1**: `.github/workflows/ci.yml` — push/PR 시 ruff + pytest (ubuntu, py3.12)
> - **A-2/C-6**: `ruff.toml` 추가, `ruff check --fix`로 87건 자동 정리 + 수동 정리
>   (E701 한 줄 idiom은 repo 관례로 ignore)
> - **B-1**: `publish_report.py`가 timestamp 없는 원본 파일을 mtime 기반
>   timestamp + `--writer`로 자동 개명 — generate_index가 항상 파싱 가능
> - **B-2**: 파일명 규칙을 `build_publish_name()` 단일 정의로 공유 (main.py --publish가 import)
> - **B-3**: `_validate_hw_capabilities`가 `validate_constraints(allow_unknown_hw=True)`로
>   위임 — scale 검증이 시뮬레이터 실행 시에도 적용됨. 이 과정에서
>   (1) 통합 테스트 fixture의 모순(supports_scale=False + 스케일링 Scaler) 수정,
>   (2) 늦게 생성되는 placeholder HW에 SimPy resource가 없던 잠재 버그 수정
> - **B-4**: html_view/plantuml_view의 COMP 표기가 `model.bw.comp_enabled()` 공유
> - **B-5**: 멀티 센서 fps 불일치 시 경고 (첫 센서 사용)
> - **C-2(경량)**: 이중 deepcopy 제거 + feature 없는 combo는 복사 생략,
>   baseline required_clock 기반 DVFS 레벨 가지치기(모드 스윕 도메인은 안전상 제외).
>   resolver 재사용(전면 캐싱)은 여전히 보류.
> - **C-1/C-3~C-5**: dead 조건식/변수 정리, TYPE_CHECKING import 정리.
>   `main()` 전면 분리(C-3)는 보류 유지.

- **점검 일자**: 2026-07-18 (1차 리뷰 적용 커밋 `d4085ee` 기준)
- **범위**: 1차에서 깊게 다루지 않은 영역 — view 레이어 내부(html/plantuml/text/exploration_report),
  `csv_to_scenario.py`, `publish_report.py`, `generate_index.py`, CI/린트 인프라
- **도구**: 코드 정독 + pyflakes 정적 분석 + 실행 경로 검증(CSV→YAML 생성 round-trip 확인)

## 요약

1차 리뷰 수준의 **정확성 버그는 발견되지 않았다.** 남은 개선점은
**(A) 인프라 공백(CI/린트), (B) 문서-코드 불일치 및 로직 이원화, (C) 정리성 항목**으로 분류된다.
가장 투자 대비 효과가 큰 것은 A-1(테스트 CI)이다.

---

## A. 인프라 (권장 우선)

### A-1. 테스트 CI 부재 ★
`.github/workflows/`에는 `deploy-reports.yml`(Pages 배포)만 있고 **pytest를 돌리는 워크플로가 없다.**
여러 사람이 리포트를 push하는 협업 구조인데 회귀 감지가 로컬 수동 실행에 의존한다.
push/PR 시 `pytest tests/ -q` + 정적 분석을 실행하는 `ci.yml` 추가 권장 (Windows/Linux 매트릭스는 선택).

### A-2. 린트 도구 미도입
pyflakes 1회 실행으로 **미사용 import 19건, 죽은 지역변수 7건, 죽은 조건식 1건**이 나왔다
(전부 무해하지만 코드 위생 신호). `ruff` 도입 + `ruff check --fix` 일괄 정리 + CI 연동 권장.
- 대표: `hw_resolver.py`의 `field`/`Callable`/`DVFSTable` 미사용 import,
  `exploration.py`의 `os`/`Path`, `tokens.py:248` 미사용 `result` 등

### A-3. `requirements.txt`에 `kaleido` 누락
README 의존성 목록에는 Kaleido ≥ 1.0이 있으나 requirements.txt에 없다.
새 환경에서 `--png` 사용 시 경고 후 PNG 누락. `kaleido>=1.0  # optional: PNG export` 명시 권장.

---

## B. 정합성 / 로직 이원화

### B-1. `publish_report.py`가 README 설명과 다르게 동작 ★
README: "output_*에서 **자동으로 timestamp/writer를 붙여** 복사".
실제 코드(`publish_files`)는 **파일명을 그대로 복사**한다. 기본 출력 파일명(`{project}-{scenario}_suffix`,
timestamp 없음)도 `REPORT_PATTERN`(timestamp/writer가 optional)에 매칭되어 복사되는데,
`generate_index.py`의 정규식은 timestamp/writer를 **필수**로 요구하므로 인덱스에 잡히지 않는다
— TROUBLESHOOTING Case 1("No reports found")의 근본 원인.
**개선**: publish 시 timestamp(파일 mtime 기반)와 writer(scenario YAML 또는 `--writer` 인자)를
자동 부여해 이름을 변환하거나, timestamp 없는 파일은 경고와 함께 제외.

### B-2. Publish 로직 이원화
`main.py --publish`(`publish_outputs()`)와 `publish_report.py`가 거의 같은 기능을 서로 다른
이름 규칙으로 구현. 한쪽(스크립트)으로 통합하고 main.py는 이를 호출하는 형태 권장.

### B-3. HW 능력 검증 이원화
`SoCSimulator._validate_hw_capabilities()`(simulator.py:187)와
`ScenarioGraph.validate_constraints()`(scenario.py:302)가 mode/crop 검증을 중복 구현
(scale 검증은 후자에만 있음). 규칙이 갈라질 위험 — `validate_constraints()`로 단일화하고
시뮬레이터는 이를 호출만 하도록 권장.

### B-4. comp 표기 판정 불일치 (view 레이어)
1차 리뷰에서 BW **계산**은 `model/bw.py`로 통일했으나(‘SBWC’ 등 구체 타입도 압축 인정),
**표시 로직**은 여전히 `== 'enable'`만 인정:
- `html_view.py:143, 273, 632` — COMP 배지
- `html_view.py:514` — Level2 comp(ratio) 표기
- `plantuml_view.py:297` 부근
→ exploration이 생성한 `comp: SBWC` 설정에서 리포트 숫자는 압축 반영, 뷰 배지는 미표시로 불일치.
`model.bw.comp_enabled()` 재사용으로 통일 권장.

### B-5. 멀티 센서 시나리오 한계
`SoCSimulator._get_frame_interval()`은 **첫 번째 SensorNode의 fps만** 사용.
듀얼 카메라(이종 fps) 시나리오에서 프레임 간격이 부정확해진다.
당장 지원이 없더라도 센서 2개 이상 감지 시 경고 출력 권장.

---

## C. 정리성 / 성능 (낮은 우선순위)

| # | 위치 | 내용 |
|---|------|------|
| C-1 | `exploration_report.py:708` | `'mode_ips' in locals()` 죽은 조건식 — `mode_ips`는 어디에도 정의되지 않아 항상 else. 컬럼 수 상수로 정리 |
| C-2 | `controller/exploration.py` | (1차에서 보류) resolver 재사용·조건부 deepcopy·조합 가지치기 — `exploration_FHD30.yaml`이 실측 5분+ 소요 확인. 대형 sweep 상시 사용 시 착수 |
| C-3 | `main.py` | `main()` 여전히 약 450줄 — load/resolve/simulate/export 단계 함수 분리 여지 |
| C-4 | `text_view.py:393, 491` | `'SimulationResults'` 문자열 annotation — `TYPE_CHECKING` import로 정리 |
| C-5 | `scenario.py:547` | `optimize_otf_clocks` 인근 미사용 `sensor_task` 지역변수 |
| C-6 | 전역 | pyflakes 미사용 import 19건 일괄 정리 (A-2와 함께 ruff로) |

---

## 확인했으나 문제 없음

- `csv_to_scenario.py`: 구조 양호, CSV→YAML 생성이 기존 커밋 파일과 **바이트 동일**하게 재생성됨(결정적 동작 확인)
- `generate_index.py`: 파싱/그룹핑 로직 정상 (B-1의 입력 파일명 문제만 존재)
- `report_generator._determine_mif_level()`: 레벨 선택 로직(최저 주파수 만족 레벨) 정상
- `html_view.py`: ELK JSON 기반으로 구조화되어 있어 문자열 조립 문제 없음
- 1차 수정 사항 전체: 249 tests passed 유지

## 권장 처리 순서

1. **A-2 + C-6** — ruff 도입 후 자동 정리 (10분 작업, 이후 모든 정리 항목의 안전판)
2. **A-1** — 테스트 CI 워크플로 추가
3. **B-1** — publish 파일명 자동 변환 (사용자 체감 버그)
4. **B-4** — view comp 판정 통일
5. **B-3, B-2** — 검증/publish 로직 단일화
6. **A-3, B-5, C-1~C-5** — 순차 처리

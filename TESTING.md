# Regression Test Guide

## 환경 준비

```bash
pip install -r requirements.txt
```

> `pytest>=7.0`이 `requirements.txt`에 포함되어 있습니다.

---

## 전체 테스트 실행

```bash
python -m pytest tests/ -v
```

| 옵션 | 설명 |
|------|------|
| `-v` | 테스트 이름과 PASS/FAIL 상세 출력 |
| `--tb=short` | 실패 시 짧은 트레이스백 |
| `--tb=line` | 한 줄 요약만 출력 |
| `-q` | 최소 출력 (진행바 + 요약) |
| `-x` | 첫 번째 실패에서 중단 |

---

## 개별 파일 / 클래스 / 함수 실행

```bash
# 특정 파일
python -m pytest tests/test_model.py -v

# 특정 클래스
python -m pytest tests/test_report_generator.py::TestSectionPower -v

# 특정 테스트 함수
python -m pytest tests/test_bw_chart.py::TestBPPMap::test_nv12_bpp -v
```

---

## 키워드 필터링

```bash
# 이름에 "dma"가 포함된 테스트만
python -m pytest tests/ -v -k "dma"

# "power" 또는 "clock" 포함
python -m pytest tests/ -v -k "power or clock"
```

---

## 테스트 파일 구성 (211 tests)

| 파일 | 테스트 수 | 영역 |
|------|:---------:|------|
| `test_model.py` | 다수 | HWNode, Module, ScenarioGraph 기본 |
| `test_controller.py` | 다수 | SoCSimulator 코어 시뮬레이션 |
| `test_view.py` | 다수 | Monitor, Visualizer 기본 |
| `test_integration.py` | 다수 | 통합 (YAML → 시뮬 → 결과) |
| `test_token_flow.py` | 다수 | Token-based dataflow |
| `test_otf_optimization.py` | 다수 | OTF 최적화 |
| `test_hw_info.py` | 다수 | HWInfoDB, CSV 파싱 |
| `test_external_nodes.py` | 다수 | SensorNode, DisplayNode |
| `test_explicit_dma.py` | 다수 | DMAModule 내장 |
| `test_config_separation.py` | 다수 | config 분리 |
| `test_multiframe.py` | 10 | 멀티프레임 파이프라이닝 |
| `test_yaml_config.py` | 14 | YAML 로딩 라운드트립 |
| `test_hw_resolver_extended.py` | 7 | VDD 리더, apply_to_hw, SW margin |
| `test_report_generator.py` | 16 | 리포트 생성 (섹션, DMA, HTML/MD) |
| `test_exploration.py` | 13 | Exploration sweep |
| `test_bw_chart.py` | 8 | BPP_MAP, BW 차트 |
| `test_html_view_smoke.py` | 4 | HTML 뷰 스모크 테스트 |

---

## Regression 체크리스트

코드 변경 후 다음 순서로 검증합니다:

1. **전체 실행** — `python -m pytest tests/ -v`
2. **실패 확인** — 실패가 있으면 `--tb=short`로 원인 파악
3. **관련 파일만 재실행** — 수정 후 해당 파일만 빠르게 확인
4. **전체 재실행** — 최종 확인 (211 passed, 0 failed)

> **Tip**: 코드 push 전에 항상 `python -m pytest tests/ -v`로 전체 회귀 테스트를 실행하세요.

# Code Review — 개선/최적화 사항 정리

- **리뷰 일자**: 2026-07-17
- **대상**: `src/` 전체 (model / controller / view), `main.py`
- **기준 커밋**: `49c6738` (master, clean)
- **테스트 상태**: `pytest tests/ -q` → **227 passed** (1.93s) — 문서상 211개와 불일치 (→ L-13)

> **적용 현황 (2026-07-18)**: 아래 전 항목 적용 완료. 회귀 테스트 22건을
> `tests/test_review_fixes.py`로 추가해 **249 passed**.
> 예외/변경 사항:
> - **M-1**: 토큰 인프라는 삭제 대신 "모델 라이브러리로 유지" 결정 —
>   `tokens.py`는 테스트되는 독립 컴포넌트이므로 보존하고, 시뮬레이터 쪽
>   미사용 통합 메서드(`_init_token_infrastructure`/`_create_output_token`/
>   `_distribute_token`)만 제거. `_detect_token_mode`는 유지(테스트 존재).
>   H-6(OR_JOIN)은 tokens.py에서 직접 수정.
> - **M-4**: 동적 속성은 이름 변경 없이 `ScenarioGraph.__init__`의 정식
>   초기화 필드로 승격 (소비처 diff 최소화).
> - **L-7**: `main()` 전체 재구성 대신 응집 블록 2개(`build_gantt_hw_order`,
>   `publish_outputs`) 추출 + hw_nodes/hw_raw 확장 루프 통합(L-8)의
>   중간 수준 리팩토링으로 적용.
> - **성능 최적화 3건**(resolver 재사용/조건부 deepcopy/조합 가지치기)은
>   H/M 대비 우선순위가 낮아 이번 배치에서 제외 — 추후 대형 sweep 사용 시 적용.

## 요약

MVC 분리, 도메인 모델링(DAG/OTF/DVFS), 풍부한 sanity check 메시지 등 전반 구조는 견고하다.
다만 **(1) 에러가 verbose 모드에서만 보이는 CLI 버그, (2) 시뮬레이터와 리포트의 전력 공식 이원화, (3) OTF 그룹의 자원 경합/DMA 미모델링, (4) 미사용 토큰 인프라와 레거시 dead code, (5) BW 공식 4중 중복** 이 다섯 가지가 우선 해결 대상이다.

| 등급 | 건수 | 성격 |
|------|:---:|------|
| **High** (H) | 8 | 잘못된 결과/동작을 만들 수 있는 버그 |
| **Medium** (M) | 10 | 설계 정합성·유지보수성 문제 |
| **Low** (L) | 13 | 스타일·사소한 개선·성능 미세조정 |

---

## High — 정확성 버그

### H-1. 에러 메시지가 `-v` 없이는 출력되지 않음 — `main.py`

비-verbose 실행(기본값)에서 아래 에러들이 **이유 없이 조용히 종료**된다.

| 위치 | 증상 |
|------|------|
| `main.py:1316-1319` | `print("[Error] CSV validation failed:")` 후 개별 에러는 `vprint` → 목록이 안 보임 |
| `main.py:1446-1449` | `vprint("Error: Scenario validation failed:")` 후 `sys.exit(1)` → **아무 출력 없이 종료** |
| `main.py:1169`, `1226` | `--scenario-config`/`--hw-config` 누락 에러가 `vprint` 후 `return` |

**개선**: 에러/경고는 무조건 `print`(stderr 권장), `vprint`는 진단 정보에만 사용. 종료는 `sys.exit(1)`로 통일 (`return`은 exit code 0이 되어 스크립트 연동 시 성공으로 오인).

### H-2. `--asv-group 0`이 무시됨 — `main.py:1324`

```python
asv_group = args.asv_group or scenario_data.get('asv_group', 4)
```

ASV 그룹 0은 유효한 값인데 falsy라서 scenario 기본값으로 대체된다. `args.num_frames or ...`(`main.py:1467`)도 같은 패턴.

**개선**: `asv_group = args.asv_group if args.asv_group is not None else scenario_data.get('asv_group', 4)`

### H-3. DMA 결과의 frame_id 오염 가능 — `simulator.py:318, 488`

`_current_frame_id`가 **인스턴스 속성**으로 쓰인다. 멀티프레임 파이프라이닝에서 프레임 N+1의 태스크 프로세스가 이 값을 덮어쓴 뒤 프레임 N의 DMA 프로세스가 완료되면(`_record_dma_result`는 완료 시점에 읽음) 잘못된 `frame_id`가 기록된다.

**개선**: `frame_id`를 `_simulate_dma_transfer(..., frame_id)` 파라미터로 전달. 인스턴스 상태 제거.

### H-4. OTF 그룹으로 들어오는 M2M edge의 DMA 전송 미시뮬레이션 — `simulator.py:585-595`

`_run_task_process_framed`(483-493)는 M2M predecessor의 `transfer` 설정으로 Write/Read DMA를 시뮬레이션하지만, `_run_otf_group_process_framed`는 pred 이벤트 완료만 기다리고 **transfer를 무시**한다. M2M→(OTF 그룹 멤버) edge에 `transfer`가 있으면 해당 DMA 시간이 타임라인/BW 차트에서 누락된다.

### H-5. OTF 그룹이 HW 자원을 점유하지 않음 — `simulator.py:613-617`

`_run_otf_group_process_framed`는 `hw.resource.request()` 없이 `timeout(max_time)`만 수행한다. 멀티프레임 파이프라이닝에서 프레임 간격 < OTF 처리시간이면 **같은 IP가 두 프레임을 동시에 처리**하는 비현실적 결과가 나온다 (non-OTF 태스크는 자원 경합이 모델링됨 → 비대칭).

**개선**: 그룹 멤버 각각의 HW 자원을 request한 뒤 timeout하거나, 최소한 프레임 겹침 감지 시 경고 출력.

### H-6. OR_JOIN에서 토큰 유실 — `tokens.py:240-257`

`simpy.AnyOf` 이후 **선택되지 않은 `store.get()` 이벤트가 취소되지 않고 pending 상태로 남는다**. 이후 그 큐에 도착하는 토큰을 몰래 소비해 downstream이 영원히 기다리게 된다 (SimPy의 전형적 함정).

**개선**: 미선택 get 이벤트를 `event.cancel()` 처리하거나, 이미 트리거된 이벤트의 토큰은 재주입.

### H-7. 병렬 edge 추가 시 data/transfer/conn_type 유실 — `scenario.py:251-259`

같은 (src, dst) 쌍에 `add_dependency`를 두 번 호출하면 `port_pairs`만 append되고 두 번째 호출의 `data`/`transfer`/`buffer_size`/`conn_type`은 **조용히 버려진다**. 같은 IP 쌍 사이에 포맷이 다른 DMA 채널이 2개인 경우 BW 계산이 틀어진다.

**개선**: edge attribute를 port_pair별 리스트로 저장하거나(`channels: [{ports, data, transfer}]`), 충돌 시 명시적 에러.

### H-8. 시뮬레이터와 리포트의 전력 공식 이원화 — `hw_nodes.py:420-429` vs `hw_resolver.py:73-83`

| 경로 | 공식 | 소비처 |
|------|------|--------|
| `IPNode.get_power_consumption` | `unit_power × set_clock(MHz) × (V/0.71)² × duration` | 시뮬레이션 `TaskResult.power_consumed`, PowerAnalyzer |
| `ResolvedIPConfig._calc_dynamic_power` | `unit_power × resolution_MP × (V/0.71)² × (fps/30)` | Simulation Report, Exploration |

`unit_power`의 단위는 `mW/MP@30fps`이므로 전자는 **차원이 맞지 않는 근사식**이다(주석으로 인정됨). 같은 실행의 CSV 결과와 HTML 리포트가 서로 다른 전력 값을 보인다.

**개선**: `apply_to_hw()`에서 `ResolvedIPConfig` 기반 `_power_calculator` 콜백을 IPNode에 주입해 공식을 단일화 (이미 strategy 콜백 훅이 존재하므로 구조 변경 불필요).

### H-9. Exploration의 timing constraint가 적용되지 않음 — `exploration.py`

`load_config`가 `constraints.timing`으로 `timing_budget_ms`를 계산하지만(222-227행) `_evaluate`는 이를 **한 번도 참조하지 않는다**. feasibility는 `set_clock ≥ required_clock`만 확인. `hw_time_ms`는 이미 계산되므로(483행) 비교 한 줄이면 된다:

```python
if self.timing_budget_ms and result.hw_time_ms > self.timing_budget_ms:
    result.feasible = False
    result.infeasible_reason = f"hw_time {result.hw_time_ms:.2f}ms > budget {self.timing_budget_ms:.2f}ms"
```

---

## Medium — 설계/정합성

### M-1. 토큰 인프라가 시뮬레이션 경로에 통합되지 않음 — `simulator.py:763-885`, `tokens.py`

`_init_token_infrastructure` / `_create_output_token` / `_distribute_token` / `_token_enabled`는 `run()`에서 **절대 호출되지 않는다**. `_detect_token_mode`도 테스트에서만 직접 호출된다. README의 "Token-based dataflow" 서술과 실제 동작이 다르다.

**개선**: 통합 계획이 없으면 삭제(약 350줄 + tokens.py 상당 부분), 있으면 `run()`에서 `_detect_token_mode()` 분기를 실제로 연결.

### M-2. 레거시 단일 프레임 경로 dead code — `simulator.py:336-448`

`_run_task_process` / `_run_otf_group_process`는 `run()`에서 호출되지 않으며, 전제인 `_task_events`도 초기화되지 않아 **실행되면 KeyError**다. 게다가 framed 버전과 달리 `manual_hw_time_ms`를 반영하지 않아 로직이 이미 분기됐다. → 삭제하고 framed 버전만 유지.

### M-3. BW 계산 공식 3중 중복 + `_is_dma_port` 3중 중복

`comp_ratio × fps × W × H × (bitwidth/8) × bpp × r_w_rate / 1e6`:

| 파일 | 함수 |
|------|------|
| `report_generator.py:33-56` | `_calc_bw` |
| `visualizer.py:877-908` | `_calc_bw_and_power` |
| `exploration.py:87-118` | `_calc_bw_for_port` |

`_is_dma_port`도 세 곳(`report_generator.py:27`, `visualizer.py:853`, `exploration.py:82`)에 있다. 공식 수정 시 세 곳을 동기화해야 하는 상태.

**개선**: `src/model/bw.py`로 추출해 세 소비처가 공유. `constants.py`의 BPP_MAP과 같은 위치가 자연스럽다.

### M-4. ScenarioGraph의 비공식 동적 속성 프로토콜

`_ip_settings`, `_manual_clocks`, `_resolved_sensor`, `_bw_power_coeff`, `_vBat`, `_pmic_efficiency`가 `main.py:465-474`에서 동적으로 부착되고 `hw_resolver`/`exploration`/view가 `getattr(scenario, '_xxx', {})`로 소비한다. 타입 체커/IDE에 보이지 않고, 빠뜨리면 조용히 기본값으로 동작한다.

**개선**: `ScenarioGraph`의 정식 필드로 승격 (`ip_settings: Dict[str, dict]`, `sim_params: SimParams` dataclass 등).

### M-5. `_get_hw`의 silent placeholder 생성 — `simulator.py:172-180`

미등록 HW 이름을 1GHz/ppc=1 가짜 IPNode로 조용히 대체한다. CLI 경로는 sanity check가 먼저 잡지만, 시뮬레이터를 라이브러리로 직접 쓰는 경우(테스트 포함) 오타가 **그럴듯한 숫자**로 은폐된다. 최소한 warning 출력, 이상적으로는 opt-in 플래그.

### M-6. View→Controller 역방향 import — `visualizer.py:825`

`from ..controller.simulator import BPP_MAP` — simulator의 backward-compat re-export(`simulator.py:26`)를 경유한다. `model.constants`에서 직접 import하고, simulator의 re-export는 제거 시점을 정해 삭제.

### M-7. VDD 정렬이 클럭 정렬 이후의 전압 요구를 반영하지 못할 수 있음 — `hw_resolver.py:289-332`

Step 3.5에서 DVFS 그룹 peer의 `set_clock`이 상향되어도 `required_voltage`는 원래 값이 유지되고, Step 4의 VDD 정렬은 `required_voltage` 기준이다. **DVFS 그룹 peer들이 서로 다른 VDD에 속하는 구성**이면 상향된 클럭으로 동작하는 IP의 `set_voltage`가 해당 클럭의 필요 전압보다 낮게 결정될 수 있다. 현재 projectA 구성에서는 발생하지 않더라도 방어 로직(정렬 후 `set_voltage ≥ 자기 레벨 전압` 검증) 추가 권장.

### M-8. Exploration의 상태 복원이 try/finally가 아님 — `exploration.py:363-500`

`_evaluate`는 공유 `scenario._ip_settings` / `task.ip_mode`를 **mutate 후 복원**하는 패턴인데, 복원 호출이 각 return 경로에 수동 배치되어 있다. Step 7(`_collect_bw`) 등에서 예외가 나면 scenario가 오염된 채 다음 combo가 평가된다. → `try/finally`로 감싸거나, mutate 대신 복사본 전달.

### M-9. TimingAnalyzer의 critical path가 실제 경로가 아님 — `timing_analyzer.py:68-75`

"종료 시각이 전체 끝의 95% 이내인 태스크 나열"은 병렬로 늦게 끝난 무관한 태스크도 포함한다. `ScenarioGraph`가 NetworkX DAG이므로 duration을 weight로 한 `nx.dag_longest_path`로 정확한 critical path 계산 가능 (analyzer에 scenario 주입 필요).

### M-10. `HWResolver._get_ip_info_for_node`의 모드 선택이 자의적 — `hw_resolver.py:482-488`

같은 HW에 매핑된 태스크가 여러 개고 모드가 다르면 **iteration 순서상 첫 태스크의 모드**가 IP 전체를 대표한다. 최소한 모드 불일치 감지 시 경고 출력.

---

## Low — 스타일 / 사소한 개선

| # | 위치 | 내용 |
|---|------|------|
| L-1 | `modules.py:13` | `List` typing import 누락 (`DMAModule.supported_compressions: List[str]`). `__future__` annotations 덕에 런타임 무해하나 `get_type_hints` 호출 시 NameError |
| L-2 | `scenario.py:16-18` | `from src.model...` 절대 import — 나머지 코드베이스는 상대 import. 일관성 유지 |
| L-3 | `scenario.py:186-188` | `add_task`가 caller의 `workload` dict를 `update(kwargs)`로 mutate — 복사 후 갱신 권장 |
| L-4 | `modules.py:169-192` | `ScalerModule.get_processing_time`이 내부 상태(`set_input_size`) mutate — getter의 side effect |
| L-5 | `simulator.py:696` | `get_otf_groups()`를 프레임 루프 안에서 매번 재계산 — 루프 밖으로 (num_frames × 그래프 순회 절약) |
| L-6 | `hw_resolver.py:547` | BFS에 `list.pop(0)` — `collections.deque.popleft()` |
| L-7 | `main.py:907-1614` | `main()` 약 700줄 — load/resolve/simulate/export 단계별 함수 분리 권장 |
| L-8 | `main.py:1204-1224` | `hw_nodes`/`hw_raw`의 instances 확장 로직 중복 — 단일 루프로 통합 |
| L-9 | `hw_nodes.py:425` | `REFERENCE_FPS` import 미사용 |
| L-10 | `hw_info.py:315` | `rows[0][0]` — 첫 행이 빈 리스트면 IndexError. `rows[0] and rows[0][0].strip()` |
| L-11 | `hw_info.py:173-181` | `validate_against_hw`의 Processor/Memory 분기가 두 경로 모두 `continue` — no-op, 의도 확인 후 정리 |
| L-12 | `simulator.py:58-63` | `get_by_task`가 멀티프레임에서 첫 매치만 반환 — frame_id 파라미터 추가 또는 명명 변경. `get_by_*` 전반이 O(n) 선형 탐색 — 인덱스 dict 캐시 고려 |
| L-13 | `README.md`, `TESTING.md` | "211 tests" → 실제 227개. 개수 하드코딩 대신 "전체 테스트" 표현 권장 |
| + | `performance_analyzer.py:76-79` | `estimated_fps`가 4K(8,294,400px) 기준 하드코딩 — 센서 해상도 기반으로 |

---

## 성능 최적화 관점

시뮬레이션 자체는 이벤트 수가 작아(수백 태스크) 병목이 아니다. 실측 대상은 **Exploration sweep**:

1. **Resolver 중복 실행** (`exploration.py:392-396`): combo마다 `resolve_scenario` 전체 재실행. DVFS/mode override와 무관한 Step 1(required_clock 계산)은 baseline에서 1회 계산 후 재사용 가능.
2. **combo마다 `copy.deepcopy(ip_settings)`** (453행): feature sweep이 없는 combo는 deepcopy 불필요 — 조건부 복사로 전환.
3. **Cartesian product 무가지치기**: DVFS 레벨이 required_clock 미달인 조합은 product 생성 전에 도메인별로 필터 가능 — 조합 수 자체를 줄임.

세 가지 모두 적용하면 대형 sweep(수천 combo)에서 체감 개선 예상. 단, 현재 규모(초 단위)에서는 H/M 항목보다 우선순위 낮음.

---

## 권장 처리 순서

1. **H-1, H-2** — 몇 줄 수정으로 끝나는 CLI 버그. 즉시.
2. **H-8** (전력 공식 단일화) — 리포트 신뢰성의 핵심. `_power_calculator` 주입으로 해결.
3. **H-3, H-4, H-5** — 시뮬레이터 타이밍 정확성 세트. 함께 수정하고 멀티프레임 회귀 테스트 추가.
4. **H-9** (exploration timing constraint) — 한 줄 추가 + 테스트.
5. **M-1, M-2** — dead code 정리 (토큰 인프라 결정 필요: 통합 or 삭제).
6. **M-3** (BW 공식 통합) — 이후 BW 관련 수정의 안전판.
7. **H-6, H-7, M-4~M-10, L 항목** — 순차적으로.

> H-6(OR_JOIN)은 M-1에서 토큰 인프라를 삭제하기로 결정하면 자동 소멸된다. M-1 결정을 먼저 내리는 것이 효율적.

# AI① ↔ Backend 공식 코드 매핑 후보 (2026-11-10)

Backend 회신(Q3)으로 공식 시드 2종을 받았다. 이 문서는 **AI① taxonomy(26 skill / 19
misconception)를 Backend Seed 코드(SKL/MIS)에 대응시키는 후보표**다.

- 원본: `STEPi_skills.csv`(94행, 개념 42개), `STEPi_misconceptions.csv`(23행) — 저장소 밖 보관
- 원칙: **taxonomy ID는 바꾸지 않는다.** 매핑 계층에서만 변환한다(`backend_crosswalk.py`).
- 이 문서는 **검토용**이다. 코드 반영은 Backend 승인 후. 승인 전에는 `provisional_*`
  필드로만 노출하고 `official_*` 는 현행(승인 1건) 유지가 안전하다.
- 신뢰도: **상** = 이름·설명이 사실상 동일, **중** = 상위/하위 관계 또는 부분 포함,
  **하** = 의미 인접(승인 필요), **없음** = 시드에 대응 없음.

## 1. Skill 매핑 후보 (26 → SKL)

| AI① skill_id | 이름 | 후보 SKL | 신뢰도 | 비고 |
|---|---|---|---|---|
| `arithmetic` | 사칙연산과 부호 처리 | — | 없음 | 기초 선행. 시드의 단원별 skill 이전 단계 |
| `distribution` | 분배법칙과 괄호 전개 | `SKL-1-004` | **상(승인됨)** | 기존 승인 1건 유지 |
| `transposition` | 이항과 등식의 성질 | — | 없음 | 시드에 등식 성질 skill 없음 |
| `linear_equation` | 일차방정식의 풀이 | — | 없음 | 시드 방정식 단원은 복소수·이차부터 |
| `polynomial_arithmetic` | 다항식의 연산 | `SKL-1-002` `SKL-1-003` `SKL-1-004` `SKL-1-005` | 중 | 상위(그룹) skill, bkt_eligible=false |
| `polynomial_addition` | 다항식의 덧셈과 뺄셈 | `SKL-1-002` `SKL-1-003` | 상 | 덧셈/뺄셈 2개 |
| `polynomial_multiplication` | 다항식의 곱셈 | `SKL-1-004` `SKL-1-005` | 상 | 전개·정리 2개 |
| `polynomial_division` | 다항식의 나눗셈 | `SKL-1-009` `SKL-1-010` | 상 | 몫/나머지·검산 |
| `factoring` | 인수분해 | `SKL-1-020` `SKL-1-021` | 중 | 상위(그룹) skill, bkt_eligible=false |
| `factoring_common` | 공통인수 인수분해 | `SKL-1-022` `SKL-1-023` | 상 | 찾기·묶기 |
| `factoring_formula` | 인수분해 공식 | `SKL-1-024` `SKL-1-025` `SKL-1-026` | 상 | 선택·적용·복합 |
| `factoring_application` | 인수분해의 활용 | `SKL-1-026` | 중 | 복합 인수분해와 중복 |
| `complex_number` | 복소수와 허수 단위 | `SKL-2-001` `SKL-2-002` | 상 | 허수단위·표현 |
| `quadratic_equation` | 이차방정식의 근 | `SKL-2-010` `SKL-2-011` | 상 | 실근·복소범위 근 |
| `quadratic_discriminant` | 판별식과 근의 개수 | `SKL-2-012` `SKL-2-013` `SKL-2-014` | 상 | 계산·판별·조건 |
| `root_coefficient_relation` | 계수와 근의 관계 | — | 없음 | 시드에 근과 계수 skill 없음 |
| `quadratic_function` | 이차함수와 실근 | `SKL-2-015` `SKL-2-016` `SKL-2-017` `SKL-2-018` | 중 | 근↔그래프·x축 관계 |
| `higher_equation` | 고차방정식과 연립이차방정식 | `SKL-2-022` `SKL-2-023` `SKL-2-024` `SKL-2-025` `SKL-2-026` `SKL-2-027` | 중 | 삼차·사차·연립 묶음 |
| `quadratic_inequality` | 이차부등식 | `SKL-2-032` `SKL-2-033` | 상 | 부호 판단·해 |
| `absolute_inequality` | 절댓값 부등식 | `SKL-2-030` `SKL-2-031` | 상 | 경우 나누기·풀이 |
| `set_operation` | 집합과 집합의 연산 | — | 없음 | 시드(4단원)에 집합 없음 |
| `proposition` | 명제와 조건 | — | 없음 | 시드에 명제 없음 |
| `proof_method` | 증명의 방법 | — | 없음 | 시드에 증명 없음 |
| `function_concept` | 함수와 그 그래프 | — | 없음 | 시드에 함수 단원 없음 |
| `rational_function` | 유리함수 | — | 없음 | 시드 범위 밖 |
| `irrational_function` | 무리함수 | — | 없음 | 시드 범위 밖 |

**커버리지: 26개 중 16개 후보 있음(승인 1 + 후보 15), 10개 대응 없음.**

## 2. Misconception 매핑 후보 (19 → MIS)

Backend 시드의 MIS 23개는 **전부 다항식(STP-1-001~011)** 이다.

| AI① misconception_id | 이름 | 후보 MIS | 신뢰도 | 비고 |
|---|---|---|---|---|
| `1.1` | 음수 부호 처리 오해 | — | 없음 | 시드의 `MIS-1-003`(뺄셈 부호)은 다항식 뺄셈 맥락 — 동일시 불가 |
| `1.2` | 분수의 통분·약분 오해 | — | 없음 | 시드에 분수 오개념 없음 |
| `2.1` | 분배법칙 누락 | `MIS-1-004` | **상(승인됨)** | 기존 승인 1건 유지 |
| `2.2` | 동류항 오판단 | `MIS-1-001` | 상 | 동류항 구분 오류. `MIS-1-005`(전개 후 정리)는 하 |
| `2.3` | 인수분해 공식의 조건 오해 | `MIS-1-022` | 중 | 공식 선택 오류와 부분 일치 |
| `2.4` | 인수분해와 전개의 혼동 | — | 없음 | 시드에 대응 없음(`MIS-1-023`은 불완전 인수분해) |
| `3.1` | 이항 시 부호 미변경 | — | 없음 | 시드에 이항 오개념 없음 |
| `3.2` | 등식 양변 조작 비대칭 오해 | — | 없음 | 〃 |
| `3.3` | 근의 공식 부호·분모 오해 | — | 없음 | 〃 |
| `3.4` | 판별식의 의미 오해 | — | 없음 | 〃 |
| `3.5` | 계수와 근의 관계 오해 | — | 없음 | 〃 |
| `3.6` | 양변 나눗셈에서 0 가능성 미고려 | — | 없음 | 〃 |
| `3.7` | 부등호 방향 미반전 오해 | — | 없음 | 〃 |
| `3.8` | 이차부등식 해의 구간 오해 | — | 없음 | 〃 |
| `4.1` | 집합의 원소와 부분집합 혼동 | — | 없음 | 시드에 집합 없음 |
| `4.2` | 명제의 부정·역·이·대우 혼동 | — | 없음 | 시드에 명제 없음 |
| `5.1` | 유리함수의 수직점근선 오해 | — | 없음 | 시드 범위 밖 |
| `5.2` | 무리함수의 정의역 미고려 | — | 없음 | 〃 |
| `5.3` | 이차함수 계수와 그래프 방향 혼동 | — | 없음 | 시드에 이차함수 오개념 없음 |

**커버리지: 19개 중 3개 후보(승인 1 + 후보 2), 16개 대응 없음.
역방향으로는 시드 MIS 23개 중 20개가 AI① taxonomy에 대응이 없다.**

## 3. 구조적 격차 (결정 필요)

1. **Skill 세분도**: Backend 94 vs AI① 26. 1:N 매핑은 `official_skill_codes: list`로
   표현 가능하지만, 한 분석에서 코드가 4~6개까지 늘어난다. **대표 1개만 보낼지,
   전부 보낼지** 정책 확인이 필요하다.
2. **Misconception 커버리지**: 시드 MIS가 다항식에만 있어, AI①이 검출하는
   사칙연산·방정식·부등식·집합·명제 오개념은 **공식 코드가 없다**. `None`(unmapped)
   으로 나가며 Backend FK에는 잡히지 않는다. 선택지:
   - (a) 현행 유지 — 미매핑은 `None`, Backend는 `error_type`/`error_subtype`로 처리
   - (b) Backend가 비-다항식 MIS 코드를 추가 시드
   - (c) AI①이 `misconception_ids` 대신 오류 유형만 보내고 Backend가 매핑
3. **Skill 커버리지 공백**: AI①의 `set_operation`·`proposition`·`proof_method`·
   `function_concept`·`rational_function`·`irrational_function`은 시드에 없다.
   해당 skill의 `official_skill_codes`는 항상 빈 배열이 된다.

## 4. 반영 계획 (승인 후)

1. `app/integration/backend_crosswalk.py` 에 `CANDIDATE_SKILL_CODES` /
   `CANDIDATE_MISCONCEPTION_CODES` 추가 (문서의 "상·중"만).
2. `provisional_skill_codes` / `provisional_misconception_codes` 가 이 후보표를 읽도록
   연결 (기존 스키마 필드, 신규 필드 없음).
3. Backend가 승인한 항목만 `APPROVED_*` 로 승격 → `official_*` 로 출력.
4. taxonomy JSON·기존 응답 필드는 변경하지 않는다(하위호환 유지).
5. 테스트: 후보/승인 분리, 승격 후 `official_*` 이동, 미매핑 `None` 유지.

### 4.1 현재 상태 (2026-11-10 A2 반영)

- 관계자료 xlsx 전용 코드(`SKL-2-037`, `MIS-2-001`, `MIS-2-002`)는 **공식 Seed 에
  없으므로 `provisional_*` 에서 제거**했다. 역방향(`problem_skills`) 수용도 막았다.
- 따라서 `provisional_skill_codes` / `provisional_misconception_codes` 는 **현재 빈 배열**이다.
- 위 후보표에서 Backend·기획 승인이 끝난 항목만 `APPROVED_*` 로 승격해 `official_*`
  에 넣는다. 승인 전에는 빈 배열이 정상이다.
- `transposition` 은 시드에 대응 Skill 이 없어 후보가 "없음"으로 남는다.
  `SKL-2-036~040` 의 Seed 편입은 기획 결정 사항이다.

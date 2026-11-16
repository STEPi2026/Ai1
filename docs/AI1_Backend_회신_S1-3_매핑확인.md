# AI① → Backend 회신 — S1-3 결과 확인 및 매핑 3문항 답변

- 작성: AI① Learning Intelligence
- 작성일: 2026-11-10
- 성격: **전송용 회신 본문** (키 값·토큰·인증정보는 이 문서에 없다)
- 근거: 2026-11-10 11:10 Backend 실호출 8건의 ngrok 요청/응답 이력 + AI① 코드
  (`app/pipeline/analyzer.py`, `app/pipeline/session.py`, `app/integration/backend_adapter.py`,
  `app/integration/backend_crosswalk.py`) + 시드 CSV 2종(94 skill / 23 misconception)

---

## 0. 요약

| 항목 | AI① 답 |
|---|---|
| API Key 1회 교체 | **동의.** 절차는 §1. 교체는 서버 재시작이 필요하다 |
| S1-3 8건 | **전부 재현·원인 확인.** 인증 문제 아님(§2) |
| Q1 다항식 지원 | 현재 엔진은 **등식 기반**. 표현식 전용(전개·인수분해) 문제는 **미지원**이며 일정 미확정(§5) |
| Q2 crosswalk 전체 목록 | 승인 1건 + 잠정 3건 + **후보 15건(승인 요청)**(§3) |
| Q3 `official_skill_codes` 공백 | **관측 Skill 기준 생성** + **`problem_id` 재사용 시 저장본 우선**, 두 원인(§4) |
| 남은 매핑 2건 | **둘 다 확정**(§6) |
| O-06 로그 | 확인. 서버 경유 로그는 재시작 후 재공유 요청(§7) |

---

## 1. API Key 교체 — 동의

교체 제안을 수용한다. 값은 이 문서·채팅·깃에 남기지 않고 R-03 방식(대면 직접 입력
또는 비밀번호 관리자 1회 열람 링크)으로만 전달한다.

**절차 제안 (무중단에 가깝게)**

1. AI①이 새 43자 키를 발급한다(영문 대소문자·숫자·`-`·`_`).
2. AI① 서버를 `AI1_INTERNAL_API_KEYS=<기존>,<신규>`로 재기동한다 — 교체 기간에는
   **구·신 키를 동시에 수용**한다.
3. Backend가 새 키로 `/health`(인증 불필요) + 분석 1건을 호출해 200을 확인한다.
4. Backend가 구 키를 폐기하면, AI①이 구 키를 제거하고 재기동한다.
5. 양쪽에서 구 키 401 / 신 키 200을 재확인한다.

> **주의:** `AI1_INTERNAL_API_KEYS`는 프로세스 환경변수다. 코드는 요청마다
> `os.environ`을 다시 읽지만, 실행 중인 프로세스의 환경은 기동 시점에 고정되므로
> **외부에서 값을 바꾸면 재시작이 필요**하다. 이전 문서의 "재시작 없이 반영" 표현은
> 정정한다. 교체 창구는 양쪽이 합의해 잡는다.

---

## 2. S1-3 결과 — 8건 전부 원인 확인

Backend가 정리한 7개 시나리오는 실제로 **8회 호출**이다(4번이 SKL-1-004·linear_equation
2회). AI①이 관측한 값과 1:1로 맞춘다.

| # | attempt | 요청(요지) | 결과 | AI① 판정 |
|---|---|---|---|---|
| 1 | 900001 | `2x=6`, `linear_equation` | **200** | 정상 |
| 2 | 900106 | `2x=6`, `x=4` | **200** | 오류 1건(step 2, calculation/arithmetic_slip, skill `arithmetic`) |
| 3 | 900101 | `2x=6`, `SKL-1-004` | **200** | `official_skill_codes=[]` → §4 |
| 4a | 900002 | `(x+1)(x+2)`, `SKL-1-004` | 422 | `E_BAD_PROBLEM` → §2.1 |
| 4b | 900102 | `(x+1)(x+2)`, `linear_equation` | 422 | `E_BAD_PROBLEM` → §2.1 |
| 5 | 900104 | `(2x+1)+(3x-4)`, `polynomial_addition` | 422 | `E_BAD_PROBLEM` → §2.1 |
| 6 | 900105 | `(2x+1)+(3x-4)`, `SKL-1-002` | 422 | `E_UNKNOWN_SKILL_CODE` → §3 |
| 7 | 900103 | `(x+1)(x+2)=x^2+3x+2`, `SKL-1-004` | 422 | `E_UNRECOGNIZED` + `analysis_status:"UNKNOWN"` → §2.2 |

### 2.1 `E_BAD_PROBLEM`의 실제 원인 — "다항식이라서"가 아니다

AI①은 `problem_latex`를 파싱해 **등식(`eq`) 노드**여야 분석을 시작한다.

| 입력 | 파싱 결과 | 판정 |
|---|---|---|
| `(x+1)(x+2)` | 식(`expr`) | `E_BAD_PROBLEM` |
| `(2x+1)+(3x-4)` | 식(`expr`) | `E_BAD_PROBLEM` |
| `(x+1)(x+2)=x^2+3x+2` | 등식(`eq`) | 통과 |
| `2x=6` | 등식(`eq`) | 통과 |

즉 4a·4b·5는 **문제가 "전개/계산하시오" 형태의 식**이어서 거절된 것이고, 다항식
자체를 못 읽은 것이 아니다. 현재 오류 문구("문제식을 인식할 수 없습니다.")가 원인을
오해하게 만든다. 파싱 실패와 "파싱됐지만 등식이 아님"을 구분하는 코드
(`E_NOT_EQUATION`, 422)를 추가할지 검토 중이다 — 필요 여부를 회신해 주면 반영 순서를
정한다.

### 2.2 `E_UNRECOGNIZED`(시나리오 7) — 문제가 아니라 **풀이 단계 형식**

900103은 문제가 등식이라 통과했지만, 학생 풀이 2줄이 모두 판정 대상이 아니었다.

| 풀이 줄 | 파싱 결과 |
|---|---|
| `(x+1)(x+2)` | 식(`expr`) — 등식 아님 |
| `=x^2+2x+2` | 파싱 실패(앞 `=`만 있는 형태) |

판정 가능한 줄이 0개 → `E_UNRECOGNIZED`, `analysis_status:"UNKNOWN"`.

**Backend 회신과의 차이 1건:** 시나리오 7을 "200 UNKNOWN"으로 정리하셨지만, AI①이
실제로 보낸 것은 **HTTP 422 + `detail.analysis_status:"UNKNOWN"`** 이다. R-10대로
UNKNOWN은 200/422 두 형태를 모두 내보내며, 이 입력은 422 형태였다. Backend 클라이언트가
**422 + UNKNOWN을 오답이 아니라 UNKNOWN으로 처리**하는지 확인이 필요하다.

### 2.3 `E_UNKNOWN_SKILL_CODE`(시나리오 6)

`problem_skills`의 `SKL-*` 코드는 AI① crosswalk 역방향 인덱스에 있어야 한다. 현재
역변환 가능한 코드가 **2개뿐**이라 `SKL-1-002`는 거절됐다(§3). 기본 Skill로 치환하지
않는 것은 의도된 fail-closed다.

---

## 3. Q2 — 현재 crosswalk 코드 전체 목록

### 3.1 지금 실제로 동작하는 코드 (공식)

| 방향 | AI① 내부 ID | Backend 코드 | 상태 |
|---|---|---|---|
| Skill | `distribution` (분배법칙과 괄호 전개) | `SKL-1-004` (다항식의 곱 전개하기) | **승인** |
| Misconception | `2.1` (분배법칙 누락) | `MIS-1-004` (분배법칙 적용 오류) | **승인** |

- `official_skill_codes` ← 위 승인 Skill만
- `official_misconception_codes` ← 위 승인 MIS만
- 역방향(`problem_skills`의 `SKL-*` 수용)도 **위 1개 코드만** 가능하다.

### 3.2 잠정(provisional) 코드 — 시드 확인 필요

| AI① 내부 ID | 코드 | 출처 | 상태 |
|---|---|---|---|
| `transposition` (이항과 등식의 성질) | `SKL-2-037` | 관계자료 xlsx(`SKL-2-036~040`) | **시드 CSV에 없음** |
| `3.1` (이항 시 부호 미변경) | `MIS-2-001` | 관계자료 xlsx(`MIS-2-001~004`) | **시드 CSV에 없음** |
| `3.2` (등식 양변 조작 비대칭) | `MIS-2-002` | 관계자료 xlsx | **시드 CSV에 없음** |

> 제공해 주신 `STEPi_skills.csv`는 **94행(SKL-2-035까지)**, `STEPi_misconceptions.csv`는
> **23행(MIS-1-001~023, 전부 다항식)** 이다. 위 3개 코드는 관계자료 xlsx에만 있다.
> **공식 시드에 포함되는지 확인 부탁한다.** 시드에 없으면 Backend가 어차피 걸러내므로,
> AI①은 해당 항목을 `provisional_*`에서 제거하거나 "시드 밖 후보"로 표시하겠다.

### 3.3 후보 15건 — **승인 요청** (아직 `official_*` 아님)

`docs/AI1_Backend_코드_매핑_후보.md`의 후보표다. 승인해 주신 행만 `APPROVED_*`로
승격해 `official_*`에 넣는다. 승인 전에는 `provisional_*`에만 노출한다(현재는 연결
작업 예정, 공식 승격 아님).

| AI① skill | 후보 SKL | 신뢰도 |
|---|---|---|
| `polynomial_arithmetic` | `SKL-1-002` `SKL-1-003` `SKL-1-004` `SKL-1-005` | 중 |
| `polynomial_addition` | `SKL-1-002` `SKL-1-003` | 상 |
| `polynomial_multiplication` | `SKL-1-004` `SKL-1-005` | 상 |
| `polynomial_division` | `SKL-1-009` `SKL-1-010` | 상 |
| `factoring` | `SKL-1-020` `SKL-1-021` | 중 |
| `factoring_common` | `SKL-1-022` `SKL-1-023` | 상 |
| `factoring_formula` | `SKL-1-024` `SKL-1-025` `SKL-1-026` | 상 |
| `factoring_application` | `SKL-1-026` | 중 |
| `complex_number` | `SKL-2-001` `SKL-2-002` | 상 |
| `quadratic_equation` | `SKL-2-010` `SKL-2-011` | 상 |
| `quadratic_discriminant` | `SKL-2-012` `SKL-2-013` `SKL-2-014` | 상 |
| `quadratic_function` | `SKL-2-015` `SKL-2-016` `SKL-2-017` `SKL-2-018` | 중 |
| `higher_equation` | `SKL-2-022` `SKL-2-023` `SKL-2-024` `SKL-2-025` `SKL-2-026` `SKL-2-027` | 중 |
| `quadratic_inequality` | `SKL-2-032` `SKL-2-033` | 상 |
| `absolute_inequality` | `SKL-2-030` `SKL-2-031` | 상 |

**승인 형식 제안:** 표의 행 번호 또는 `skill_id → SKL 코드 목록`으로 "승인/보류/수정"
만 회신해 주면 된다. "중" 신뢰도 행은 특히 확인이 필요하다.

**참고:** `arithmetic`·`linear_equation`·`root_coefficient_relation`·`set_operation`·
`proposition`·`proof_method`·`function_concept`·`rational_function`·`irrational_function`은
시드에 대응이 없어 후보에서 제외했다. 이 Skill들은 `official_*`가 계속 빈 배열이 된다.

---

## 4. Q3 — `official_skill_codes`가 비어 오는 이유

`official_skill_codes`는 **요청의 `problem_skills`를 그대로 되돌려주는 필드가 아니다.**
분석에서 **실제로 추적·귀속된 Skill**(AI① 내부 ID)을 승인 crosswalk로 변환한 값이다.

생성 위치(`app/integration/backend_adapter.py::text_analysis_view`):

```python
skill_ids = api.get("skill_ids") or ([api["skill"]] if api.get("skill") else [])
official, provisional = _split_codes(skill_ids, skill_code)
```

`api["skill_ids"]`는 다음 값이다(`app/pipeline/session.py`).

- **정답**: `[problem.primary_tracking_skill()]` — 문제의 대표 추적 Skill
- **오답**: `errors[0].skill_ids` — **첫 오류가 귀속된 Skill**

현재 승인 매핑은 `distribution → SKL-1-004` 하나뿐이라, 그 외 Skill은 전부 `[]`가 된다.

### 4.1 900101이 빈 이유 — `problem_id` 재사용

`P-TEST-1`은 **900001에서 이미 `skills=["linear_equation"]`으로 등록**됐다.
AI①은 `problem_id`가 등록돼 있으면 **저장본을 source of truth로 쓰고 요청의
`problem_skills`로 덮어쓰지 않는다**(`_resolve_problem`).

그래서 900101이 `SKL-1-004`를 보내도 실제 추적 Skill은 저장된 `linear_equation`이었고,
`linear_equation`은 승인 매핑이 없어 `official_skill_codes=[]`가 됐다.

**재현 방법:** 같은 문제라도 **새 `problem_id`** 로 보내고, 그 문제의 실제 Skill이
`distribution`이어야 한다. 예: `2(x+1)=4` + `SKL-1-004` + 새 ID → `skill=distribution`,
`official_skill_codes=["SKL-1-004"]`. (`2x=6`에 `SKL-1-004`를 붙이는 것은 매핑 테스트로
부적절하다 — 문제의 실제 Skill과 선언이 다르다.)

### 4.2 900106이 빈 이유 — 오류 귀속 Skill

`2x=6 → x=4`는 **계산 오류**로 분류돼 `skill=arithmetic`에 귀속됐다(`linear_equation`이
아님). `arithmetic`은 승인 매핑이 없어 `official_skill_codes=[]`다.

### 4.3 정리

| 원인 | 대응 |
|---|---|
| 승인 매핑이 1개뿐 | §3.3 후보 승인 → 그만큼 `official_*`가 채워진다 |
| `problem_id` 재사용 | 테스트·운영 모두 문제별 고유 ID 사용(R-09 해시 방식과 정합) |
| 오류 귀속 Skill | 정상 동작. `skill`은 "문제 선언 Skill"이 아니라 "관측/오류 Skill"이다 |

> `skill` 필드의 의미를 계약에 명시하는 것을 제안한다:
> **"이번 시도에서 Knowledge State를 갱신한 대표 Skill(오답이면 첫 오류 귀속 Skill)"**.
> 앱이 `problem_skills`와 같다고 가정하면 안 된다.

---

## 5. Q1 — 다항식 지원 범위와 일정

### 5.1 현재 엔진이 하는 일

- SymPy 기반 **등식 단계 동치 판정**기다. 문제와 각 풀이 줄이 **등식**이어야 한다.
- 다항식이 포함된 **방정식**은 동작한다(실측):
  - `x^2-5x+6=0` → `(x-2)(x-3)=0` : **True**
  - `x^2-5x+6=0` → `(x-2)(x-4)=0` : **False**
- `correct=true`는 "오류가 검출되지 않음"이지 **완전 검증·정답 증명이 아니다**(기존 정책).

### 5.2 현재 지원하지 않는 것

| 유형 | 예 | 현재 |
|---|---|---|
| 표현식 전용 문제 | `(x+1)(x+2)`를 전개 | `E_BAD_PROBLEM` |
| 표현식 전용 풀이 줄 | `(x+1)(x+2)` / `=x^2+2x+2` | `E_UNRECOGNIZED` |
| 항등식 형태 문제 | `(x+1)(x+2)=x^2+3x+2` | **신뢰 불가**(아래) |

**항등식 주의(중요):** 문제가 항등식이면(양변 정리 결과 `0=0`) 현재 판정기가 해 집합
비교로 흘러가 **오답을 통과시키는 사례**가 실측에서 나왔다.

- `(x+1)(x+2)=x^2+3x+1` → 현재 **True(오탐)**
- `(2x+1)+(3x-4)=5x-3` 문제에 `=5x-2` → 현재 **True(오탐)**

따라서 "문제를 등식으로 바꿔 보내면 전개도 지원된다"고 답할 수 없다. **표현식/항등식
전용 판정 모드**를 만들기 전에는 다항식 전개·인수분해·계산 문제를 지원 범위에 넣지
않는다.

### 5.3 지원 확대에 필요한 것 (설계 결정)

1. **문제 유형 필드** — `problem_type: "equation" | "expression"`(또는 `answer_expression`).
   표현식 문제는 "무엇과 동치여야 하는지" 기준이 필요하다.
2. **기준식 제공** — Backend가 `correct_solution`의 최종 표현식을 주거나, AI①이
   요청의 `answer_expression`을 받아 학생 식과 동치 비교한다. AI①은 정답을 **생성하지
   않는다**(ADR-01) — 제공받은 기준식만 쓴다.
3. **항등식 판정 분기** — 잔차가 0인 문제는 해 집합 비교 대신 **전개/인수분해 동치**
   전용 경로로 보낸다(오탐 제거).
4. **taxonomy 매핑 승인** — §3.3.

### 5.4 일정

**확정 일정은 없다.** 범위가 기획과 함께 정해져야 한다(Backend도 같은 판단).
AI① 제안은 아래 3단계이며, 1단계는 **코드 변경 없이 오늘 가능**하다.

| 단계 | 내용 | 선행 조건 |
|---|---|---|
| P0 (즉시) | 등식 문제 + 등식 풀이 줄 + 문제별 고유 `problem_id` | Backend 요청 형식 정리 |
| P1 | 표현식/항등식 판정 모드(기준식 제공 계약 포함) | §5.3 1·2·3 설계 승인 + 기획 합의 |
| P2 | 다항식 연산·인수분해·나머지정리 등 확대 | P1 + taxonomy 승인 |

P1의 공수·일정은 설계 확정 후 산정해 별도 통보한다. **지금 날짜를 약속하지 않는다.**

---

## 6. 남은 매핑 2건 — 확정

### 6.1 Skill 1개 ↔ SKL 다수 → **대응 코드 전부 전송**

동의한다. `official_skill_codes`는 리스트이며, 관측 Skill 1개에 승인된 SKL 코드가 N개면
**N개 전부**를 순서대로(중복 제거) 보낸다. 대표 1개로 줄이지 않는다.

- 예: `polynomial_addition` 승인 시 → `["SKL-1-002","SKL-1-003"]`
- `official_*`에는 **승인분만**, `provisional_*`에는 **미승인 후보만** 넣는다.
- Backend는 Seed에 있는 코드만 골라 표시하면 된다.

### 6.2 대응 없는 MIS 20개 → **미매핑 유지**

동의한다. AI①은 시드에 없는 MIS 코드를 **만들지 않는다**.

- `official_misconception_codes`는 승인된 코드만 담는다(미매핑은 **항목 생략**, `null`
  원소를 넣지 않는다 — 리스트 계약).
- 내부 오개념 ID는 `errors[].misconception_ids`와 `misconception_tag`에 그대로 남는다.
  Backend는 공식 코드 필드만 FK로 사용하면 된다.
- 시드에 없는 MIS 20개는 계속 미매핑이며, 추가는 기획 결정 사항으로 남긴다.

**한 가지 확인:** `3.2 → MIS-2-002`(등식 양변 조작 비대칭)는 AI① detector가
**누락/비대칭 조작**만 잡고 모든 양변 연산을 보장하지 않는다. 이 코드를 승인한다면
사용 범위를 좁혀 합의해야 한다(현재는 잠정).

---

## 7. O-06 로그 — 확인

- Backend 호출 코드 로그 0줄(키·이미지 URL·문제식·풀이 원문 미기록) 확인.
- Backend 서버 접속 로그가 **요청 경로만** 기록한다는 점도 확인.
- AI①도 응답·오류 메시지에 키 값·서명 URL을 남기지 않는다.
- 실제 Backend 서버 경유 로그는 **서버 재시작 후** 재공유해 주면 O-06을 닫는다.

---

## 8. Backend에 요청하는 것 (액션 아이템)

| # | 요청 | 필요한 답 | 비고 |
|---|---|---|---|
| A1 | 키 교체 창구 | 가능한 시간대·담당 | §1, 재시작 필요 |
| A2 | `SKL-2-037`·`MIS-2-001`·`MIS-2-002`가 공식 시드에 있는가 | 있음/없음 | 없으면 AI①이 provisional에서 제거 |
| A3 | 후보 15건 승인 | 행별 승인/보류/수정 | §3.3 |
| A4 | UNKNOWN 422 처리 확인 | 422+UNKNOWN을 UNKNOWN으로 처리하는가 | §2.2 |
| A5 | `E_NOT_EQUATION` 코드 필요 여부 | 필요/불필요 | §2.1 |
| A6 | 표현식 문제 계약(P1) | `problem_type`/기준식 방식 의견 | §5.3, 기획 공유 |
| A7 | `skill` 필드 의미 명시 동의 | 동의/수정 | §4.3 |
| A8 | 서버 경유 O-06 로그 | 재시작 후 공유 | §7 |

---

## 9. S1-3 재실행 시나리오 (A2·A3 답변 후)

| # | 문제 | `problem_skills` | 풀이 | 기대 |
|---|---|---|---|---|
| R1 | `2(x+1)=4` (새 ID) | `SKL-1-004` | `2(x+1)=4` / `2x+2=4` / `x=1` | 200, `skill=distribution`, `official_skill_codes=["SKL-1-004"]` |
| R2 | `2x=6` (새 ID) | `SKL-1-004` | `2x=6` / `x=4` | 200, 오류 step 2. **선언과 실제 Skill 불일치는 테스트에서 제외** |
| R3 | `(x+1)(x+2)=x^2+3x+2` (새 ID) | `SKL-1-004` | **등식 2줄** | 200 또는 UNKNOWN. 항등식 오탐 여부 기록 |
| R4 | `(2x+1)+(3x-4)` | `SKL-1-002` | — | A2/A3 전에는 `E_UNKNOWN_SKILL_CODE` 유지(정상) |
| R5 | `x^2-5x+6=0` | (승인 후) | `x^2-5x+6=0` / `(x-2)(x-3)=0` | 200, 정상 |
| R6 | `x^2-5x+6=0` | (승인 후) | `x^2-5x+6=0` / `(x-2)(x-4)=0` | 200, 오류 검출 |

> R3은 **지원 판정용이 아니라 한계 기록용**이다. 항등식 오탐이 재현되면 P1 설계 근거로
> 쓴다.

---

## AI① 내부 메모 (전송 제외)

1. **항등식 오탐은 코드 결함 후보.** `equivalent()`가 잔차 0인 문제에서 해 집합 비교로
   흘러가 `[] == []`를 동치로 본다. P1 전에 별도 수정·회귀 테스트가 필요하다.
   관련 실측: `(x+1)(x+2)=x^2+3x+1` → True, `(2x+1)+(3x-4)=5x-3` vs `=5x-2` → True.
2. **`SKL-2-037`은 94행 CSV에 없다.** 관계자료 xlsx(`SKL-2-036~040`)에만 있다.
   `PROVISIONAL_SKILL_CODES`의 `transposition → SKL-2-037`은 시드 확인 전까지
   "시드 밖 후보"로 표시하거나 제거하는 편이 안전하다. `MIS-2-001/002`도 같다.
3. **`internal_auth._configured_keys` docstring의 "재시작 없이 반영"은 부정확.**
   프로세스 환경은 기동 시 고정되므로 외부 변경은 재시작이 필요하다. R-02 문구와
   함께 정정 대상.
4. **`E_BAD_PROBLEM` 메시지가 원인을 오도한다.** 파싱 실패와 비등식을 구분하는
   `E_NOT_EQUATION` 추가는 계약 additive 변경이라 Backend 승인 후 구현.
5. 이 문서는 전송용이며 키·토큰 값을 포함하지 않는다. 전송 전 사용자 확인 필요.

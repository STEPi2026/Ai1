# AI① → Backend 회신 — A1~A8 확인 및 후속 조치 (2026-11-10)

- 작성: AI① Learning Intelligence
- 대상: Backend의 S1-3 결과 확인 + A1~A8 답변
- 성격: 전송용. 키·토큰 값 없음.

## 0. 요약

| 항목 | AI① 답 |
|---|---|
| 정정(시나리오 7) | 확인. AI①도 422 + `detail.analysis_status:"UNKNOWN"`로 기록한다 |
| A1 키 교체 | 절차 동의. 일정 회신 대기. **재시작 필요** |
| A2 시드 밖 코드 | `provisional_*`에서 **제거 완료**(코드 반영). 문서에는 "시드 밖 후보"로만 남긴다 |
| A3 후보 15건 | 1차 검토 반영. **그룹 노드 2건 제외**, 나머지 13건에 대해 AI① 의견 정리 |
| A4 UNKNOWN 422 | 확인. 현행 유지(정답 null·숙련도 미갱신·재분석 허용) |
| A5 `E_NOT_EQUATION` | **추가 동의 + 코드 반영 완료.** `E_BAD_PROBLEM`(파싱 실패)과 분리해 422로 내보낸다 |
| A6 표현식 문제 | 방향 동의. `problem_type`·`target_form` 계약 초안을 만들고 기획 우선순위 후 구현 |
| A7 `skill` 의미 | 동의. 계약 문구를 "관측된 대표 Skill(오답이면 첫 오류 귀속)"으로 명시 |
| A8 O-06 | 서버 재시작 후 로그 공유 대기 |
| `problem_id` | 확인. 운영에서는 재사용 문제 없음. AI①은 저장본 우선 동작 유지 |

---

## 1. A1 — 키 교체

제안한 절차(구·신 동시 허용 → Backend 신 키 확인 → 구 키 제거)에 동의합니다.
Backend도 `.env` 변경 후 재시작이 필요하다는 점 확인했습니다.

AI① 준비 사항:

- 새 43자 키 발급(값은 문서·채팅·깃 비기재)
- `AI1_INTERNAL_API_KEYS=<기존>,<신규>` 설정 후 재기동
- 교체 중 구 키 200 / 신 키 200 / 무키 401 확인
- 구 키 제거 후 구 키 401 재확인

일정은 Backend 회신에 맞춰 잡겠습니다. 교체 전후 검증 결과는 값 없이 통보합니다.

---

## 2. A2 — 시드 밖 코드(`SKL-2-037`, `MIS-2-001`, `MIS-2-002`)

공식 Seed 범위 확인 감사합니다.

- `SKL-2-037` → `provisional_*` 출력에서 **제거**
- `MIS-2-001`, `MIS-2-002` → `provisional_*` 출력에서 **제거**
- `problem_skills` 역방향 수용에서도 **제외**한다(시드에 없는 코드를 통과시키지 않는다)
- 문서에는 **"시드 밖 후보(관계자료 xlsx에만 존재)"** 로만 남긴다
- 관계자료의 `SKL-2-036~040`, `MIS-2-xxx`를 Seed에 넣을지는 **기획 결정**에 맡긴다

제거 후 `provisional_skill_codes` / `provisional_misconception_codes`는 승인 후보가
연결되기 전까지 빈 배열이 됩니다. 내부 taxonomy의 `transposition`·`3.1`·`3.2` 분석은
계속 동작하지만, Backend에 나가는 공식/잠정 코드는 없습니다.

코드 반영 후 재검증 결과를 통보하겠습니다.

---

## 3. A3 — 후보 15건 1차 검토 반영

Backend 1차 의견을 반영해 AI① 입장을 정리했습니다. **승인은 기획 결정**이므로,
아래는 "승인 요청 목록"과 "범위 확인 목록"을 구분한 것입니다.

### 3.1 그룹 노드 2건 — 공식 매핑 대상에서 제외

| # | AI① skill | 이유 |
|---|---|---|
| 1 | `polynomial_arithmetic` | **그룹 노드**(`bkt_eligible=false`). Knowledge State 추적 대상이 아니어서 `official_skill_codes`에 **나오지 않는다**. 2·3과 중복이라는 Backend 의견과 일치 |
| 5 | `factoring` | **그룹 노드**. 6·7의 상위 개념이라 별도 코드가 필요 없다 |

→ 1·5는 승인 목록에서 빼는 것이 맞습니다. AI①도 그렇게 반영합니다.

### 3.2 이름 일치 — 승인 요청

| # | AI① skill | 후보 SKL | 비고 |
|---|---|---|---|
| 2 | `polynomial_addition` | `SKL-1-002` `SKL-1-003` | 이름 일치 |
| 3 | `polynomial_multiplication` | `SKL-1-004` `SKL-1-005` | 이름 일치 |
| 4 | `polynomial_division` | `SKL-1-009` `SKL-1-010` | 이름 일치 |
| 6 | `factoring_common` | `SKL-1-022` `SKL-1-023` | 이름 일치 |
| 7 | `factoring_formula` | `SKL-1-024` `SKL-1-025` `SKL-1-026` | 이름 일치 |
| 10 | `quadratic_equation` | `SKL-2-010` `SKL-2-011` | **우선 승인 요청** |
| 11 | `quadratic_discriminant` | `SKL-2-012` `SKL-2-013` `SKL-2-014` | **우선 승인 요청** |
| 15 | `absolute_inequality` | `SKL-2-030` `SKL-2-031` | **우선 승인 요청** |

### 3.3 범위 확인 — AI① 의견

| # | AI① skill | 후보 SKL | Backend 의견 | AI① 입장 |
|---|---|---|---|---|
| 8 | `factoring_application` | `SKL-1-026` | 7과 중복 | **N:1 매핑은 허용**된다. 한 응답에는 관측된 Skill 하나만 들어가므로 같은 응답에 중복 코드가 생기지 않는다. 8을 빼면 `factoring_application` 관측 시 official 코드가 비므로 **유지 권장** |
| 9 | `complex_number` | `SKL-2-001` `SKL-2-002` | 연산(005~007) 미포함 | `complex_number`는 **허수 단위·복소수 표현**까지다. 상등(003·004)·연산(005~007)·켤레(008·009)는 현재 taxonomy에 별도 Skill이 없어 **커버리지 갭**이다. 매핑 누락이 아니므로 9는 001·002로 유지하고, 갭은 별도 안건으로 둔다 |
| 12 | `quadratic_function` | `SKL-2-015`~`018` | 최대·최소(019~021) 미포함 | `quadratic_function`은 **근↔그래프·x축 관계**다. 최대·최소(019~021)는 **커버리지 갭**이라 12는 015~018로 유지 |
| 13 | `higher_equation` | `SKL-2-022`~`027` | 026·027은 연립이차라 수정 | AI① Skill 이름이 **"고차방정식과 연립이차방정식"** 이라 026·027 포함이 의도된 매핑이다. 분리하려면 taxonomy Skill 분할이 필요하다(현재 frozen). **유지 권장**, 기획이 분리를 원하면 026·027을 임시 제외 |
| 14 | `quadratic_inequality` | `SKL-2-032` `SKL-2-033` | 연립(034·035) 미포함 | `quadratic_inequality`는 **단일 이차부등식**이다. 연립이차부등식(034·035)은 **커버리지 갭**이라 14는 032·033로 유지 |

### 3.4 정리

- **승인 요청:** 2, 3, 4, 6, 7, 8, 10, 11, 15 (9건)
- **범위 확인 후 승인:** 9, 12, 13, 14 (4건, 위 입장 참조)
- **제외:** 1, 5 (그룹 노드)
- 승인 전에는 `official_*`를 바꾸지 않는다. 기획 회신 후 행별로 승격한다.

> 3.3의 "커버리지 갭"은 **AI①이 해당 SKL 코드를 만들지 않는다**는 뜻이다.
> Backend가 Seed에 있는 코드만 표시하는 정책과 충돌하지 않는다.

---

## 4. A4 — UNKNOWN 422 처리

확인했습니다. AI①은 현행대로 **HTTP 422 + `detail.analysis_status:"UNKNOWN"`** 을
내보냅니다. Backend가 `correct=null`, 숙련도 미갱신, 재분석 허용으로 처리하는 것도
현행 정책과 일치합니다.

추가 변경 없음.

---

## 5. A5 — `E_NOT_EQUATION` 추가

필요성에 동의합니다. 다음 의미로 분리하겠습니다.

| 코드 | HTTP | 판정 시점 | 의미 | Backend 처리(제안) |
|---|---|---|---|---|
| `E_BAD_PROBLEM` | 422 | `parse_line(problem_latex)`가 **None** | 수식 해석 실패 | FAILED(재시도 허용) — 현행 유지 |
| `E_NOT_EQUATION` | 422 | 파싱은 됐지만 **등식이 아님**(`expr`) | 지원하지 않는 문제 유형 | **저장 없이** 422 "지원하지 않는 문제 유형" 안내 |

- `detail` 형식: `{"code":"E_NOT_EQUATION","message":"문제식이 등식이 아닙니다. ..."}`
- `analysis_status`는 붙이지 않는다(입력 오류를 UNKNOWN으로 섞지 않음)
- 기존 코드·필드는 그대로다(additive)
- 적용 지점: attempt 분석(`_check_parsing`)과 legacy `/analyze-solution` 두 경로

코드 반영 후 배포 시점을 통보하겠습니다. 그때까지는 현행 `E_BAD_PROBLEM`으로
응답합니다.

---

## 6. A6 — 표현식 문제(P1) 의견

제안 방향(정답 생성 없이 판정)에 동의합니다. AI① 기술 검토는 아래와 같습니다.

### 6.1 판정 방식

1. **각 풀이 줄 ↔ 직전 줄(첫 줄은 문제식) 동치 비교** — 식(`expr`)끼리는
   `simplify(a-b)==0`으로 판정할 수 있다(현재 엔진에 이미 있는 경로).
2. **마지막 줄의 형태 검사** — `target_form`과 일치하는지 확인한다.
3. **정답식 불필요** — 기준식은 "문제식"과 "직전 줄"이므로 별도 정답을 만들지 않는다.

### 6.2 `problem_type` / `target_form` 계약 초안

- `problem_type`: `"equation" | "expression"` (기본 `"equation"` — 기존 요청 호환)
- `target_form`: `"integer" | "expanded" | "factored" | null`
  - `integer`: 자유 기호가 없는 수치 답
  - `expanded`: 전개된 다항식 꼴(미전개 곱·괄호 없음)
  - `factored`: 상수 아닌 인수의 곱 꼴
- 두 필드는 **선택**이며, `expression`일 때만 `target_form`을 쓴다.
- `AnalyzeAttemptRequest`는 `extra="forbid"`라 필드를 명시적으로 추가하고
  `analyze()` 경로까지 전달해야 한다(조용히 무시되지 않게).

### 6.3 형태 판정의 주의

- "형태"는 문자열 모양이 아니라 **의미**로 판정한다. 예: `x(x+2)`와 `(x)(x+2)`는
  둘 다 factored다.
- `expanded`는 `Poly` 전개 후 미전개 곱이 없는지로 본다.
- `factored`는 상수 아닌 인수의 곱으로 표현되는지로 본다(완전 인수분해 강제 여부는
  기획 확인 필요).

### 6.4 항등식 오탐 분리

- **expression 모드**에서는 등식을 풀지 않으므로 해 집합 비교로 흐르지 않는다.
  → 항등식 오탐 경로가 원천적으로 사라진다.
- **equation 모드**에서 문제가 항등식(잔차 0)이면 `correct=true`로 단정하지 않는다.
  `analysis_status="REVIEW_REQUIRED"`로 보내는 방안이 자연스럽다.
- 단, 현재 `AnalyzeAttemptResponse.correct`는 **`bool`(널 불가)** 이다. "판정 보류"를
  표현하려면 `correct` 널 허용 또는 별도 필드가 필요하다 — **계약 변경**이라 함께
  정해야 한다.

### 6.5 일정

`problem_type`·`target_form` 필드 추가와 expression 판정기는 **기획 우선순위 확정 후**
착수한다. AI①은 계약 초안(필드·오류·응답)을 먼저 문서로 공유하고, 승인 후 구현한다.
지금 날짜를 약속하지 않는다.

---

## 7. A7 — `skill` 필드 의미

동의합니다. AI① 응답 스키마 설명을 아래로 명시하겠습니다.

> `skill`: 이번 시도에서 **관측된 대표 Skill**. 오답이면 **첫 오류가 귀속된 Skill**.
> 요청의 `problem_skills`(문제 선언 Skill)와 다를 수 있다.

Backend 처리 확인:

- `official_skill_codes`: APP 표시용(Seed 코드만) — 현행 유지
- 숙련도 갱신: 학생 선택 `problem_skills` 기준 — AI① `mastery` 미사용 — 현행 유지
- 관측 Skill을 숙련도 갱신에 쓸지는 승인 매핑이 늘어난 뒤 AI②·기획과 재논의

AI①의 `mastery`/`state`/`states`는 **관측 Skill 기준 BKT 값**이며, Backend가 쓰지
않아도 내부 관측은 계속 기록됩니다. 나중에 사용할 때 키가 `skill`(관측 Skill)임을
전제로 하면 됩니다.

---

## 8. A8 — O-06 로그

Backend 서버 재시작 후 실제 서버 경유 호출 로그를 기다립니다. AI①은 로그에
키·이미지 URL·문제식·풀이 원문이 없는지 확인해 회신하겠습니다.

---

## 9. `problem_id`

`CAPTURED-{촬영문제ID}-{problem_latex 해시}`로 문제마다 고유하다는 점 확인했습니다.
수식이 바뀌면 ID가 바뀌므로 **운영에서 저장본 재사용 문제는 발생하지 않습니다.**

AI①은 R-09대로 "등록된 `problem_id`는 저장본이 source of truth" 동작을 유지합니다.
900101은 테스트에서 같은 ID를 재사용한 사례로, 운영 계약과 무관합니다.

---

## 10. S1-3 재실행

A2·A3 확정 후 §9(R1~R6)로 진행하겠습니다. 순서 제안:

1. A2 코드 반영(`provisional_*` 제거) → 재검증
2. A5 `E_NOT_EQUATION` 반영 → 배포 통보
3. A3 승인(기획) → `official_*` 승격
4. R1~R6 재실행

R4(`SKL-1-002`)는 승인 전까지 `E_UNKNOWN_SKILL_CODE`가 정상입니다. R3은 지원 판정이
아니라 **항등식 한계 기록**용입니다.

---

## 11. AI① 후속 작업 (Backend 통보용)

| # | 작업 | 상태 |
|---|---|---|
| 1 | A2 `provisional_*`에서 시드 밖 코드 제거 | **반영 완료** |
| 2 | A5 `E_NOT_EQUATION` 추가 + 테스트 | **반영 완료** |
| 3 | A7 `skill` 필드 설명 명시 | **반영 완료** |
| 4 | A3 후보표 갱신(그룹 노드 제외·범위 의견) | 반영 예정 |
| 5 | A6 `problem_type`/`target_form` 계약 초안 | **작성 완료** → `docs/AI1_표현식문제_계약초안_A6.md` |
| 6 | A8 로그 확인 | Backend 재시작 대기 |

> taxonomy ID·기존 응답 필드는 바꾸지 않는다. A2·A5·A7은 additive/정리 범위다.

---

## 12. 반영 결과 (2026-11-10, 코드)

| 항목 | 변경 | 확인 |
|---|---|---|
| A5 | `app/pipeline/analyzer.py`, `app/pipeline/session.py` — `parse_line` 이 `None` 이면 `E_BAD_PROBLEM`, 파싱됐지만 `expr` 이면 `E_NOT_EQUATION`(422) | `tests/test_api.py::test_expression_problem_rejected`, `tests/test_attempt_api.py::test_not_equation_422_without_storing` |
| A5 | `E_NOT_EQUATION` 은 `analysis_status` 를 붙이지 않는다(`_ANALYSIS_STATUS_BY_ERROR` 미등록) | `tests/test_analysis_status.py::test_unknown_mapping_is_limited_to_analysis_outcomes` |
| A2 | `PROVISIONAL_SKILL_CODES` / `PROVISIONAL_MISCONCEPTION_CODES` 를 빈 표로. 역방향 `skill_id_for_code("SKL-2-037")` → `E_UNKNOWN_SKILL_CODE` | `tests/test_backend_adapter.py::test_seed_outside_codes_are_never_mapped`, `tests/test_backend_text_adapter_api.py::test_seed_outside_skill_code_is_rejected_not_reverse_mapped` |
| A7 | `AnalyzeAttemptResponse.skill` / `mastery` 설명에 "관측된 대표 Skill(오답이면 첫 오류 귀속)" 명시 | 스키마 설명 변경(동작 무변경) |
| 전체 | `1717 passed`, `pyflakes` 0건 | 2026-11-10 |

**서버 재시작은 하지 않았다.** 실행 중인 프로세스에는 아직 반영되지 않았으므로,
배포 시점은 재시작 일정과 함께 별도 통보한다.

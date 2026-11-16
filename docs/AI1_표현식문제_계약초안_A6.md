# AI① 표현식 문제 판정 계약 초안 (A6 / P1)

- 작성: AI① Learning Intelligence
- 작성일: 2026-11-10
- 상태: **P1 확정분 구현 완료 (2026-11-13). 기획 B4 확인만 대기**
- 대상: Backend 회신 A6("정답 생성 없이 각 줄 동치 + 마지막 줄 형태 검사")
- 성격: 계약 제안서. 키·토큰·학생 데이터 없음

---

## 0. 요약

| 항목 | 제안 |
|---|---|
| 신규 요청 필드 | `problem_type`, `target_form` (둘 다 선택, 기본값으로 기존 요청 100% 호환) |
| 판정 방식 | 정답식 없이 **줄↔직전 줄 동치** + **마지막 줄 형태 검사** |
| 정답 생성 | **하지 않는다** (ADR-01 유지). 기준식은 문제식과 직전 줄뿐 |
| 항등식 오탐 | 줄↔줄 오탐은 A-1로 수정 완료. `expression` 모드는 발생 경로 자체가 없음. `equation` 모드의 "문제식 자체가 모순" 잔여분만 `REVIEW_REQUIRED` |
| 응답 변경 | `correct` 널 허용 (Backend 확인 완료) |
| 기존 필드 | 삭제·개명 없음 (additive) |
| 남은 결정 | `target_form` 3종의 판정 정의, 형태 불일치의 오류 유형, 완전 인수분해 강제 여부 |

---

## 1. 왜 필요한가

현재 엔진은 문제식과 모든 풀이 줄이 **등식(`eq`)** 이어야 동작한다.

| 입력 | 현재 |
|---|---|
| `(x+1)(x+2)` (전개하시오) | `E_BAD_PROBLEM` → `E_NOT_EQUATION` |
| `(2x+1)+(3x-4)` | `E_BAD_PROBLEM` → `E_NOT_EQUATION` |
| `(x+1)(x+2)=x^2+3x+2` (항등식) | 통과하지만 **오탐 위험** |

Backend가 보낸 4a·4b·5(전개/계산 문제)가 여기서 거절됐다. 공통수학1 다항식 단원의
상당 부분이 이 형태이므로, 범위를 넓히려면 등식 전용 판정기를 확장해야 한다.

---

## 2. 요청 필드 (additive)

`AnalyzeAttemptRequest`는 `extra="forbid"`이므로 필드를 **명시적으로** 추가하고
`analyze()` 경로까지 전달한다(조용히 무시되는 silent failure 금지).

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `problem_type` | `"equation" \| "expression"` | `"equation"` | 생략하면 현행과 완전 동일하게 동작 |
| `target_form` | `"integer" \| "expanded" \| "factored" \| null` | `null` | `problem_type="expression"`일 때만 의미가 있다 |

### 2.1 조합 규칙

| `problem_type` | `target_form` | 판정 |
|---|---|---|
| `equation` (또는 생략) | `null` | 정상 — 현행 동작 |
| `equation` | 값 있음 | **422** (`E_INVALID_TARGET_FORM`) — 등식에 형태 요구는 모순 |
| `expression` | `null` | 정상 — 동치만 검사, 형태는 보지 않음 |
| `expression` | 값 있음 | 정상 — 동치 + 마지막 줄 형태 검사 |

`target_form` 값은 Backend Skill의 `answer_form`(`integer` / `expanded` / `factored`)을
그대로 쓴다(Backend 제안 수용). 값 집합을 늘릴 때는 양쪽 합의가 필요하다.

---

## 3. 판정 알고리즘 (정답 생성 없음)

### 3.1 `expression` 모드

입력: 문제식 `P`, 풀이 줄 `L1..Ln`, `target_form = T`.

1. **첫 줄 동치**: `simplify(P - L1) == 0`
2. **이후 줄 동치**: `simplify(L(i-1) - Li) == 0`
3. **형태 검사**: `T != null`이면 `Ln`이 `T` 꼴인지 검사(§4)

- 동치가 성립하지 않는 첫 줄 = **첫 오류 줄** (`error_step`)
- 정답식이 필요 없다. 기준은 항상 "문제식" 또는 "직전 줄"이다
- 학생이 문제식과 다른 방향으로 변형해도(예: 인수분해 문제를 전개로 풂) 줄끼리
  동치는 성립한다. 이때 잡히는 것은 **형태 위반**(§5)이며 계산 오류가 아니다

### 3.2 `equation` 모드 (현행 + 항등식 분기)

현행 유지. 단, **문제식이 항등식**(양변 정리 결과 `0=0`, 잔차 0)이면:

- 해 집합 비교로 흘려보내지 않는다 (현재 오탐 원인)
- `analysis_status = "REVIEW_REQUIRED"`, `correct = null`로 응답
- `errors = []`, 숙련도 갱신 없음

이 분기는 `expression` 모드와 독립적으로 필요하다. `equation` 모드로 들어온
항등식은 "문제 유형 선언이 틀렸거나, 문제 자체가 항등식" 두 경우이고 AI①이
단정할 수 없다.

---

## 4. `target_form` 판정 정의 (제안)

"형태"는 **문자열 모양이 아니라 의미**로 판정한다. `x(x+2)`와 `(x)(x+2)`는 둘 다
`factored`다.

| 값 | 판정 | 예 |
|---|---|---|
| `integer` | `free_symbols == 0` (자유 기호 없음) | `12`, `-3/4` → O / `2x` → X |
| `expanded` | `Poly`로 전개했을 때 **미전개 곱·괄호가 없음** | `x^2+3x+2` → O / `(x+1)(x+2)` → X |
| `factored` | **상수가 아닌 인수의 곱**이 2개 이상 | `(x+1)(x+2)` → O / `x^2+3x+2` → X |

### 4.1 미해결 — 완전 인수분해 강제 여부

`factored`에서 **완전 인수분해**까지 요구할지 결정이 필요하다.

| 정책 | `x(x^2+3x+2)` | 판정 |
|---|---|---|
| 느슨함(제안 기본) | 상수 아닌 인수 2개 이상 | `factored` O |
| 엄격함 | 기약 인수분해 완료 | `factored` X (`x(x+1)(x+2)` 요구) |

AI① 제안: **느슨함을 기본**으로 하고, 엄격함이 필요하면 `target_form`에
`factored_irreducible`을 추가한다(값 추가는 양쪽 합의 사항). 엄격함을 기본으로
하면 "인수분해는 했지만 더 할 수 있음"을 오류로 처리해야 해서 오판 위험이 크다.

---

## 5. 형태 불일치를 어떻게 표현할까 (미해결)

`expression` 모드에서 동치는 모두 성립하는데 마지막 줄 형태만 틀린 경우:

- 예: 전개 문제(`target_form="expanded"`)인데 `(x+1)(x+2)`로 끝냄
- `correct=false`는 맞다. 그러나 `error_type`/`error_subtype`을 무엇으로 줄지 정해야 한다

현재 taxonomy(`data/error_types.json`, frozen)에서 가장 가까운 값:

| 후보 | 의미 | 적합도 |
|---|---|---|
| `procedure` + `method_mismatch` | 상황에 맞지 않는 풀이 방법 선택 | 중 — "방법"이라기보다 "결과 형태" |
| `concept_error` + `factoring_confusion` | 인수분해·전개 혼동 | 중상 — 전개/인수분해를 바꿔 낸 경우에 한정 |
| 신규 subtype (예: `wrong_form`) | 요구 형태 미충족 | 상 — **taxonomy 변경 필요** |

AI① 제안:

1. `problem_skills`가 전개 계열이면 `concept_error` + `factoring_confusion`
2. 그 외에는 `procedure` + `method_mismatch`
3. `wrong_form` 신규 subtype은 **taxonomy을 고치지 않는다**는 원칙상 만들지 않는다
   (필요하면 기획이 taxonomy 개정으로 결정)

`error_subtype`은 이미 additive 확장 필드이므로 새 값을 넣는 것 자체는 계약 위반이
아니다. 다만 taxonomy 파일에 없는 값을 코드가 만들어 내면 "분류는 taxonomy 조회로만"
원칙에 어긋나므로, 위 2개 중 선택으로 처리한다.

---

## 6. 응답 영향

| 필드 | 변경 | 비고 |
|---|---|---|
| `correct` | `bool` → `bool \| null` | `REVIEW_REQUIRED`일 때 `null`. Backend 확인 완료 |
| `analysis_status` | 값 변화 없음 | `ANALYZED` / `REVIEW_REQUIRED` / `UNKNOWN` 그대로 |
| `error_step`, `error_type`, `error_subtype` | 의미 변화 없음 | 형태 위반은 §5 정책 |
| `steps_latex`, `valid`, `unrecognized` | 변화 없음 | |
| `official_*` / `provisional_*` | 변화 없음 | crosswalk 승인 결과에만 따름 |

### 6.1 `correct` 널 허용은 하위호환인가

- 기존 응답은 계속 `true`/`false`만 낸다. `null`은 **새로 생기는 경우**에만 나온다
- Backend는 `analysis_status="REVIEW_REQUIRED"`이면 `correct` 값과 무관하게 미정
  처리하므로 서버 변경 불필요(Backend 확인 완료)
- 클라이언트가 `correct: bool`로 강타입을 잡고 있으면 널 처리 분기가 필요하다 →
  **APP 쪽 확인 필요 항목**

---

## 7. 오류 코드

| 코드 | HTTP | 조건 | 비고 |
|---|---|---|---|
| `E_NOT_EQUATION` | 422 | `problem_type="equation"`인데 문제식이 등식이 아님 | **양쪽 합의 완료.** Backend 반영 완료 |
| `E_INVALID_TARGET_FORM` | 422 | `equation` + `target_form` 값, 또는 허용되지 않은 `target_form` 값 | 신규 제안 |

`E_NOT_EQUATION`은 `expression` 모드에서는 발생하지 않는다(`expr`이 정상 입력).

---

## 8. 예시

### 8.1 전개 문제

요청

```json
{
  "problem_id": "CAPTURED-1234-ab12cd",
  "problem_latex": "(x+1)(x+2)",
  "problem_type": "expression",
  "target_form": "expanded",
  "problem_skills": ["SKL-1-004"],
  "student_id": "stu-1",
  "solution_text": "(x+1)(x+2)\n=x^2+3x+2"
}
```

응답(요지)

```json
{
  "analysis_status": "ANALYZED",
  "correct": true,
  "error_step": null,
  "steps_latex": ["(x+1)(x+2)", "x^2+3x+2"],
  "valid": [true, true]
}
```

### 8.2 전개 문제인데 인수분해 꼴로 끝냄

```json
{
  "problem_latex": "(x+1)(x+2)",
  "problem_type": "expression",
  "target_form": "expanded",
  "solution_text": "x^2+3x+2\n(x+1)(x+2)"
}
```

→ `correct=false`, 마지막 줄 형태 위반. `error_type`/`error_subtype`은 §5 정책.

### 8.3 항등식이 `equation` 모드로 들어옴

```json
{
  "problem_latex": "(x+1)(x+2)=x^2+3x+2",
  "solution_text": "(x+1)(x+2)=x^2+3x+2"
}
```

→ `analysis_status="REVIEW_REQUIRED"`, `correct=null`, `errors=[]`.

**현재 상태(2026-11-10 A-1 반영 후):** 풀이 **줄과 줄 사이**의 항등식↔모순 오탐
(`5x-3=5x-3` ↔ `5x-3=5x-2` → `True`)은 이미 제거됐다. 남은 것은 **문제식 자체가
모순**인 경우(`(x+1)(x+2)=x^2+3x+1` → `valid=[True]`)이며, 이 분기가 그 잔여분을
`REVIEW_REQUIRED`로 돌린다.

---

## 9. 기획 결정 필요 목록

| # | 질문 | AI① 제안 | 상태 |
|---|---|---|---|
| Q-A6-1 | 표현식 문제를 1차 범위에 넣는가 | 전개·인수분해부터 (다항식 단원) | **확정 — P1 포함(구현 완료)** |
| Q-A6-2 | `factored`에 완전 인수분해를 강제하는가 | 느슨함 기본 (§4.1) | **확정 — 느슨한 정책** |
| Q-A6-3 | 형태 불일치 오류 유형 | §5의 2개 중 선택 | **확정 — `procedure`/`method_mismatch`** |
| Q-A6-4 | `target_form` 값 집합을 `answer_form` 3종으로 고정하는가 | 고정 | **확정 — 3종 고정** |
| Q-A6-5 | APP이 `correct: null`을 처리하는가 | 확인 요청 | **확정 — APP 처리 가능** |
| Q-A6-6 | `integer` 답(계산 문제)도 대상인가 | 2차 | **확정 — 2차(값은 이미 동작)** |

---

## 10. 비목표 (명시)

- 정답식·모범 풀이 생성 없음 (ADR-01)
- 힌트·메시지 생성 없음 (AI② 몫)
- taxonomy ID 변경 없음 (`data/error_types.json` frozen)
- 기존 응답 필드 삭제·개명 없음
- `problem_type` 생략 시 현행과 **완전 동일** 동작 (회귀 위험 0 목표)

---

## 11. 구현 순서 (승인 후)

1. `AnalyzeAttemptRequest`에 필드 2개 추가 + `analyze()` 전달 + 검증 테스트
2. `expression` 동치 판정기(줄↔직전 줄) + 테스트
3. `target_form` 판정기 3종 + 테스트
4. `equation` 모드 항등식 → `REVIEW_REQUIRED` 분기 + `correct` 널 허용 + 테스트
5. `E_INVALID_TARGET_FORM` 추가 + 테스트
6. golden 재생성(변경 없어야 정상 — `problem_type` 생략 케이스)

1~2는 기획 결정 없이도 착수 가능하다. 3은 Q-A6-2, 4는 Q-A6-5에 의존한다.

---

## 12. Backend 의견 반영 (2026-11-10)

Backend 회신: 필드 구성·판정 방식 동의. `target_form` 은 Skill 의 `answer_form`,
`problem_type` 은 **`problem_latex` 에 `=` 가 있으면 `equation`, 없으면 `expression`**
규칙 제안. `E_INVALID_TARGET_FORM` 은 계약 오류로 기록 후 FAILED.

### 12.1 동의

- `target_form` = Skill `answer_form` 값: 동의(§2.1 과 동일).
- `E_INVALID_TARGET_FORM` → 계약 오류 + FAILED: 동의. 단 **APP 재시도를 유도하지 않는다**
  (학생 입력 문제가 아니라 클라이언트 버그 신호).
- `correct: null` APP 확인: 동의.

### 12.2 `=` 존재 규칙 — 조건부 동의

규칙 자체는 **표현식/방정식 구분용으로 타당**하다. 다만 계약에 3가지를 명시해야 한다.

1. **정규화 목록**(양쪽 동일하게 적용)
   - 전각 `＝` → `=`
   - 공백·`\;`·`\,`·`\!` 제거 후 판정
   - `\mathrm{=}`, `\text{=}` → `=`
   - `\le`/`\leq`/`\ge`/`\neq` 등은 `=` 문자를 포함하지 않으므로 자동 제외
   - `\overset{?}{=}` 처럼 `=` 를 포함하는 장식은 **equation** 으로 본다(빈칸 채우기)
2. **단일 소유자**: Backend 가 값을 보내면 AI①은 그 값을 **그대로 신뢰**한다.
   생략했을 때만 AI①이 같은 규칙을 적용한다. 규칙 문구는 이 문서를 기준으로 한다.
3. **오분류 시 조용한 오판 금지** — §12.4 안전장치.

### 12.3 이 규칙의 실제 위험 (AI① 실측)

`parse_line()` 실행 결과:

| `problem_latex` | `=` | 파싱 | 규칙 판정 | 실제 의미 | 비고 |
|---|---|---|---|---|---|
| `x^2-5x+6=0` | O | `eq` | equation | 방정식 | 정상 |
| `(x+1)(x+2)` | X | `expr` | expression | 전개 | 정상 |
| `f(x)=x^2-5x+6` | O | `eq` | equation | 함수 정의 | equation 모드 부적합 가능 |
| `A={1,2,3}` | O | `eq` | equation | 집합 표기 | equation 모드 부적합 |
| `2x+6=` | O | **None** | equation | OCR 우변 유실 | `E_BAD_PROBLEM`(재시도 가능) |
| `\begin{cases} x+y=3 \\ x-y=1 \end{cases}` | O | **None** | equation | 연립방정식 | 미지원 범위 |

**핵심:** `=` 존재 규칙은 "표현식 vs 방정식" 구분에는 유효하다. 그러나
**"등식이면 모두 방정식"은 아니다.** 함수 표기·집합 표기는 `=` 가 있어도 equation
모드가 맞지 않는다.

### 12.4 안전장치 3개 (제안)

| # | 규칙 | 이유 |
|---|---|---|
| S1 | equation 모드인데 문제 파싱이 `expr` → `E_NOT_EQUATION` | 이미 합의(§7). fail-closed |
| S2 | expression 모드인데 **풀이 줄이 `eq`** 로 파싱 → 판정하지 않고 `REVIEW_REQUIRED` | 문제를 표현식으로 봤는데 학생이 등식으로 썼다면 유형 오분류 가능성이 높다. 조용히 true/false 를 내면 오판이 저장된다 |
| S3 | 함수 표기(`f(x)=`, `y=`)·집합 표기(`A={...}`)·연립(`\begin{cases}`)은 **P1 범위 밖** | 별도 유형을 만들지 않고, equation 모드에서 `E_UNVERIFIABLE`/`REVIEW_REQUIRED` 로 떨어지게 둔다 |

### 12.5 `answer_form` 공백과의 관계 — 중요

Backend 계획 1번("`answer_form` 이 없으면 `problem_type` 을 보내지 않는다")을 그대로
두면 다음이 발생한다.

- 표현식 문제인데 `answer_form` 이 비어 있음 → `problem_type` 생략 → 기본 `equation`
  → **`E_NOT_EQUATION` 422**
- 즉 Seed 94개 `answer_form` 이 채워질 때까지 **표현식 문제는 전부 422** 다.

**제안: `problem_type` 과 `target_form` 을 분리해서 보낸다.**

- `problem_type="expression"` + `target_form=null` 은 **유효한 조합**이다(동치만 검사).
- `answer_form` 이 없어도 `problem_type` 은 보낼 수 있다.
- `target_form` 은 `answer_form` 이 있을 때만 채운다.

이렇게 하면 콘텐츠 작업을 기다리지 않고 표현식 문제의 **동치 판정**부터 켤 수 있다.

### 12.6 확정 필요

| # | 항목 | 상태 |
|---|---|---|
| B1 | `=` 존재 규칙 + §12.2 정규화 목록 | 확정 |
| B2 | `problem_type`·`target_form` 분리 전송 | 확정 |
| B3 | expression 모드에서 `eq` 줄 → `REVIEW_REQUIRED` | 확정 |
| B4 | 함수·집합·연립 표기는 P1 범위 밖 | 확정 (부등식 포함, 기획 확인 대기) |

---

## 13. 구현 기록 (2026-11-13)

### 13.1 반영된 계약

- 요청 필드: `problem_type`(기본 `equation`), `target_form`(기본 `null`)
- 부등호 공통 목록 + **경계 매칭**:
  `\\(lt|gt|le|leq|ge|geq|ne|neq|leqslant|geqslant)(?![A-Za-z])`
  기호 `< > ≤ ≥ ≠ ≦ ≧` 는 문자 단위. `\left`·`\gets`·`\ltimes` 오탐 방지.
- `E_UNSUPPORTED_INEQUALITY`(422, 저장 없음, `detail.retryable=false`)
- `E_UNSUPPORTED_FUNCTION`(422, 저장 없음, `retryable=false`) — B4 확정 후 추가.
  `parse_line` 이 `f(x)` 를 `f*x` 로 오독하므로 함수 표기는 판정 전에 거절한다.
- `E_INVALID_TARGET_FORM`(422, 저장 없음, `retryable=false`, FAILED 미사용)
- `correct: bool | null` — `REVIEW_REQUIRED` 에서만 `null`
- `REVIEW_REQUIRED`: `errors=[]`, `error_step=null`, KnowledgeObservation 미생성
- 형태 위반: `procedure` + `method_mismatch` (관측 Skill 귀속, 오개념 없음)
- 같은 `attempt_id` 에 다른 `problem_type`/`target_form` → 409 `E_ATTEMPT_CONFLICT`

### 13.2 구현 위치

| 파일 | 내용 |
|---|---|
| `app/pipeline/inequality.py` | §2.1 정규화 + 경계 매칭 부등호 판정 |
| `app/pipeline/notation.py` | 함수 표기 판정(B4 확정분, `f(x)` 오독 차단) |
| `app/pipeline/forms.py` | `integer`/`expanded`/`factored` 형태 판정 |
| `app/pipeline/verifier.py` | `verify_expression()` + `review_reasons`/`form_violation_index` |
| `app/pipeline/analyzer.py` | 모드 검증·분기·`correct=null` 응답 |
| `app/pipeline/session.py` | attempt 흐름의 expression 판정·판정 보류·형태 오류 |
| `app/pipeline/classifier.py` | `wrong_form` → `procedure`/`method_mismatch` |
| `app/main.py` | 요청 전달, `retryable=false`, attempt 충돌 비교 |
| `tests/test_expression_mode.py` | 토큰·형태·계약·API 회귀 |
| `tests/test_expression_golden.py` | `tests/golden/expression_cases.json` 기반 golden |

### 13.3 알려진 동작

- SymPy 가 `2(x+1)` 을 파싱 단계에서 `2x+2` 로 자동 분배한다.
  형태 판정은 파싱된 식의 **의미 구조**를 보므로 이 입력은 `expanded` 로 판정된다.
- `target_form` 형태 위반은 계산 오류가 아니므로 `calculation` 이 아니라
  `procedure`/`method_mismatch` 로 낸다(§5 후보 2안).
- `factored` 는 느슨한 정책(비상수 인수 2개 이상). 완전 인수분해 강제는 미도입.

### 13.4 회귀 고정 (2026-11-13 추가)

`P1` 구현 이후 다음 동작을 테스트로 고정했다. 동작을 바꾸려면 계약 근거가 먼저다.

| 고정 대상 | 테스트 |
|---|---|
| 부등호 17토큰 검출 / 비부등호 명령 5종 오탐 없음 | `test_expression_mode.py` |
| `target_form` 12케이스 형태 판정 | `test_expression_mode.py` |
| 형태 위반이 `valid=true` 전부여도 `ErrorRecord` 1건으로 저장 | `test_attempt_form_violation_is_stored_as_procedure_error` |
| `REVIEW_REQUIRED` 스냅샷: `correct=null`, `errors=[]`, `states={}` | `test_attempt_review_snapshot_is_stored_without_state_update` |
| 같은 본문 재전송 멱등성(`problem_type`/`target_form` 포함) | `test_same_expression_attempt_is_idempotent` |
| 같은 `attempt_id` + 다른 유형 → 409 | `test_same_attempt_id_with_different_problem_type_conflicts` |
| 레거시 `/analyze-solution` 도 `correct=null`/부등식 거절 동일 | `test_legacy_analyze_solution_*` |
| `analyze()` 시그니처: 기존 6개 인자 순서 불변, 신규 2개만 뒤에 추가 | `test_analyze_signature_unchanged` |
| golden 12케이스 + 거절 5케이스(부등식/계약/유형) | `test_expression_golden.py` |
| 연쇄 오류로 `observations` 가 0건이어도 응답 조립이 깨지지 않음 | `test_analysis_response_survives_when_all_errors_are_suppressed` |
| 함수 표기 문제 거절 + 풀이 줄 함수 표기는 판정 보류 | `test_function_problem_is_rejected_without_store`, `test_function_notation_in_solution_is_review_not_verdict` |
| `a(b+c)` 같은 변수×괄호 곱은 함수로 오탐하지 않음 | `test_variable_times_parenthesis_still_judged` |

`problem_type` 값 검증은 **두 단계**다.
1. 스키마 `Literal` → 알 수 없는 값은 FastAPI 422(분석 이전).
2. `analyze()` 직접 호출 경로 → `E_INVALID_PROBLEM_TYPE`.

`target_form` 은 스키마에서 `str` 로 열어 두고 `analyze()` 에서 검증한다.
스키마 `Literal` 로 막으면 `E_INVALID_TARGET_FORM` + `retryable=false` 를
Backend 에 전달할 수 없기 때문이다(§12.1).

### 13.5 Q-A6-3 판단 근거 (기획 확정: 현행 유지)

**확정(2026-11-16): `procedure` / `method_mismatch` 유지.** 아래는 확정 근거 기록이다.

현재 값: `error_type=procedure`, `error_subtype=method_mismatch`, `misconception_ids=[]`.

| 후보 | 장점 | 단점 |
|---|---|---|
| **현행** `procedure`/`method_mismatch` | 오개념 근거 없이도 성립. `valid` 가 전부 `true` 인데 오개념을 주장하지 않는다 | "왜 틀렸는지"가 약하다 |
| `concept_error`/`factoring_confusion` | 학생이 "인수분해하라"는 요구를 놓친 경우 의미가 맞다 | **근거 부족** — 단순 계산 문제에서도 같은 값이 나온다 |
| `comprehension`/`goal_misunderstood` | "요구 형태를 못 읽었다"에 가장 가깝다 | 계산 오류와 섞이면 분류가 흐려진다. 현재 taxonomy 상 `comprehension` 은 문제 독해 실패용 |

AI① 권고: **현행 유지**. 근거는 (1) `valid=true` 전부인데 개념 오류를 단정하는 것은 ADR-02 위반,
(2) `factoring_confusion` 은 인수분해 **과정** 오류(예: `x^2+5x+6 → (x+1)(x+6)`)에 쓰는 것이 맞고,
형태 미준수는 결과물 규격 문제다. 기획이 `factoring_confusion` 을 원하면
`_TAXONOMY_BINDING["wrong_form"]` 한 줄만 바꾸면 된다(코드 분기 없음).

### 13.6 남은 기획 의존 항목

| 항목 | 상태 | AI① 조치 |
|---|---|---|
| B4: 부등식·함수·집합·연립 P1 제외 | **확정(2026-11-16)** | 부등식 `E_UNSUPPORTED_INEQUALITY`, 함수 표기 `E_UNSUPPORTED_FUNCTION` 로 fail-closed |
| Q-A6-3: 형태 위반 분류 | **확정 — 현행 유지** | `procedure`/`method_mismatch` |
| Q-A6-2: `factored` 완전 인수분해 강제 | **확정 — 미도입** | 느슨한 정책 유지 |
| Q-A6-6: `integer` 계산 문제 범위 | **확정 — 2차** | `target_form=integer` 는 이미 동작 |

---

## 14. 배포·연동 체크리스트 (2026-11-13)

합의된 순서: **AI① 배포 → Backend 전송 ON**. 순서를 뒤집으면 Backend 가
`problem_type` 을 보내는데 AI① 이 무시하는 구버전이 남는다(`extra="forbid"` 로 422).

### 14.1 Backend 확인

- [ ] 요청에 `problem_type` 생략 시 `equation` 으로 처리됨을 전제로 한다.
- [ ] `target_form` 은 Skill 의 `answer_form` 그대로 보낸다(가공 금지).
- [ ] `422` 응답의 `detail.retryable` 이 `false` 인 코드는 **재전송하지 않는다**:
      `E_UNSUPPORTED_INEQUALITY`, `E_UNSUPPORTED_FUNCTION`,
      `E_INVALID_TARGET_FORM`, `E_INVALID_PROBLEM_TYPE`.
- [ ] `E_UNSUPPORTED_INEQUALITY` / `E_UNSUPPORTED_FUNCTION` 는 `ai_analysis_results` 에
      **저장하지 않는다**(분석 자체를 수행하지 않음). FAILED 행도 만들지 않는 정책 유지.
- [ ] 같은 `attempt_id` 재전송 시 `problem_type`/`target_form` 을 **같은 값**으로 보낸다.
      다르면 409 `E_ATTEMPT_CONFLICT`.
- [ ] `analysis_status=REVIEW_REQUIRED` 는 `correct=null` 로 저장한다 (true/false 로 승격 금지).

### 14.2 APP 확인

- [ ] `correct` 가 `null` 인 경우 "보류" 표시 (§12.1 합의).
- [ ] `REVIEW_REQUIRED` 는 재촬영 유도 가능하지만 `retryable=false` 코드는 재촬영 유도 금지.
- [ ] `analysis_status=UNKNOWN` 은 본문 없이 422 이므로 화면 전환을 에러 응답 기준으로 처리.

### 14.3 판정 우선순위 (에러가 겹칠 때)

1. 입력 크기/빈 풀이 (`E_INPUT_TOO_LARGE`, `E_EMPTY_SOLUTION`)
2. 문제식 부등호/함수 표기 (`E_UNSUPPORTED_INEQUALITY`, `E_UNSUPPORTED_FUNCTION`)
   — **유형 선언보다 먼저**
3. `problem_type`/`target_form` 계약 (`E_INVALID_PROBLEM_TYPE`, `E_INVALID_TARGET_FORM`)
4. 문제식 파싱/유형 불일치 (`E_BAD_PROBLEM`, `E_NOT_EQUATION`)
5. 판정 불가 (`E_UNRECOGNIZED`, `E_UNVERIFIABLE`)

즉 부등식 + 잘못된 `target_form` 조합은 `E_UNSUPPORTED_INEQUALITY` 로 응답한다
(범위 밖 입력을 먼저 알려 주는 편이 진단에 유리).

### 14.4 교체 시 무중단 확인

- [ ] 구버전 AI① ↔ 신버전 Backend 조합이 남지 않았는지 배포 순서 로그로 확인.
- [ ] 배포 후 `problem_type` 생략 호출 1건이 이전과 같은 응답인지 확인(§10 회귀 위험 0).
- [ ] `correct=null` 경로 1건을 실제 저장소에서 조회해 `raw_result.correct` 가 `null` 인지 확인.

---

## 15. 기획 결정 기록 (2026-11-16)

기획 결정을 AI① 권고대로 확정했다. 아래 값은 이제 계약이며, 변경하려면 근거를 먼저 남긴다.

| 항목 | 확정 내용 | 코드 반영 |
|---|---|---|
| B4 | 부등식·함수·집합·연립은 P1 범위 밖 | 부등식 → `E_UNSUPPORTED_INEQUALITY`, 함수 표기 → `E_UNSUPPORTED_FUNCTION` |
| Q-A6-1 | 표현식(전개·인수분해) P1 포함 | `verify_expression`, `forms.py` |
| Q-A6-2 | `factored` 는 느슨한 정책 | `_is_factored` (비상수 인수 2개 이상) |
| Q-A6-3 | 형태 위반은 `procedure`/`method_mismatch` | `_TAXONOMY_BINDING["wrong_form"]` |
| Q-A6-4 | `target_form` 3종 고정 | `ALLOWED_TARGET_FORMS` |
| Q-A6-5 | APP의 `correct: null` 처리 | 응답 스키마 `bool \| null` |
| Q-A6-6 | `integer` 계산 문제는 2차 | 값은 이미 동작, 범위 확대만 보류 |

### 15.1 B4 확정 과정에서 발견한 결함

B4 를 "코드는 이미 fail-closed" 로 판단했지만, 함수 표기는 예외였다.

```
parse_line("f(x)=2x+1")  →  ('eq', f*x, 2*x + 1)
parse_line("f(2)=4")     →  ('eq', 2*f, 4)
```

즉 함수 호출이 **곱셈**으로 해석되어, `f(x)=2x+1` 한 줄짜리 풀이가
`correct=true`, `analysis_status=ANALYZED` 로 통과했다. 범위 밖 입력이
정답으로 승격되는 ADR-03 위반이라 판정 전에 거절하도록 고쳤다.

- 문제식: 422 `E_UNSUPPORTED_FUNCTION` (`retryable=false`, 저장 없음)
- 풀이 줄: 판정 보류(`REVIEW_REQUIRED`, `correct=null`) — 학생 입력 실수로
  전체 요청을 거절하지 않는다.

### 15.2 남은 한계 (문서화)

탐지 이름은 `f`/`g`/`h` 와 `sin`·`cos`·`log` 등 관례적 함수 이름으로 제한했다.
`a(b+c)` 처럼 **변수 × 괄호**는 정상 계산이므로 함수로 보지 않는다.
따라서 `f`/`g`/`h` 를 변수로 쓰는 문제는 함수 표기로 오인될 수 있다.
P1 교과 범위(다항식)에서 이 문자를 변수로 쓰지 않는 관례를 전제로 한 선택이며,
실사용 데이터에서 오탐이 확인되면 이름 목록만 조정하면 된다.

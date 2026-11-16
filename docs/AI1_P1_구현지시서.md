# AI① P1 구현 지시서 — 표현식 문제 판정 (2026-11-16)

- 작성: AI① Learning Intelligence
- 상태: **지시서(구현 착수 가능).** 코드 미반영
- 근거 계약: `docs/AI1_표현식문제_계약초안_A6.md` §2~§12, `docs/AI1_Backend_회신_A6_의견반영.md`
- 성격: 개발 지시서. 키·토큰·학생 데이터 없음

---

## 0. 이 문서를 쓰는 방법

- Stage 0 → 6 순서로 진행한다. **각 Stage 끝에서 테스트 green + pyflakes 0** 이어야 다음으로 넘어간다.
- Stage 1~3은 기획 결정 없이 착수 가능하다. Stage 4는 **D2·D3**, Stage 5는 **D1** 결정에 의존한다(§2).
- 각 Stage는 독립적으로 배포 가능해야 한다. 즉 Stage 1만 반영해도 기존 요청 동작은 100% 동일하다.
- 계약 필드는 **additive만** 한다. 기존 필드 삭제·개명 금지(AGENTS.md §1).

---

## 1. 확정된 전제

| # | 전제 | 근거 |
|---|---|---|
| 1 | `problem_type` 생략 시 **현행과 완전 동일**(= `equation`) | A6 초안 §2 |
| 2 | 정답식·모범 풀이를 **생성하지 않는다** | ADR-01 |
| 3 | taxonomy ID를 바꾸지 않는다. 새 분류는 `_TAXONOMY_BINDING`/기존 ID 조회로만 | AGENTS.md §2 |
| 4 | `_MAX_STEPS=30`·문제식 2000자·풀이 2만자·`solve()` 80회 예산 유지. 상한을 올려서 통과시키지 않는다 | AGENTS.md §6 |
| 5 | 새 의존성 없음. SymPy만 사용 | 사용자 제약 |
| 6 | 실행 중 서버를 재시작하지 않는다(배포는 별도 승인) | 사용자 제약 |

---

## 2. 착수 전 결정 (D1~D5)

아래 5개는 "합의되지 않은 계약 변경 금지" 원칙 때문에 **명시적으로 골라야** 하는 항목이다.
채택안으로 구현을 진행하되, D2·D3는 기획 통보 대상이다.

| ID | 질문 | 채택안 | 근거 / 대안을 버린 이유 |
|---|---|---|---|
| **D1** | `correct: null`을 어디까지 적용하나 | **신규 "문제 자체가 판정 불가(항등식/모순)" 경우에만** `null`. 기존 `REVIEW_REQUIRED`(인식 실패·판정 보류)는 계속 `true/false` | `tests/test_analysis_status.py::test_correct_unchanged_under_review_required`가 "인식 실패는 오답이 아니다"를 고정한 **정책 테스트**다. 전체 REVIEW_REQUIRED를 null로 바꾸면 이 정책이 깨지고, Backend도 변경 불필요하다고 답했다. APP 확인 전 전면 변경은 금지 |
| **D2** | 형태 불일치의 `error_type`/`error_subtype` | **`procedure` + `method_mismatch`**, `misconception_ids=[]` | taxonomy에 이미 존재(`data/error_types.json` procedure.method_mismatch). 전개 계열에 `concept_error/factoring_confusion`을 붙이는 2단 분기는 **근거(오개념) 없이 오개념을 붙이는 것**이라 ADR-02 위반 위험. Q-A6-3 확정 시 별도 변경 |
| **D3** | `factored`에 완전 인수분해를 강제하나 | **느슨함**(상수 아닌 인수 ≥ 2) | 완전 인수분해 강제는 "인수분해는 했지만 더 할 수 있음"을 오류로 만들어 오판 위험이 크다. 엄격함이 필요하면 `factored_irreducible` 값을 **추가 합의**로 도입 |
| **D4** | `problem_type`/`target_form` 저장 위치 | **`Problem`에 저장**(additive optional). 재등록 시 저장본이 source of truth | 요청 스코프로 두면 같은 `attempt_id` 재전송에 다른 `problem_type`이 **조용히 무시**된다(멱등 경로가 본문을 비교하지 않음). `problem_latex`와 같은 "최초 등록 승리" 규칙으로 통일 |
| **D5** | `problem_type` 생략 시 `=` 유무로 자동 판정하나 | **하지 않는다.** 생략 = `equation` | B1("`=` 있으면 equation")은 **조건부 동의**이고 Backend 확정 전이다. 자동 판정을 넣으면 표현식 문제가 `E_NOT_EQUATION`이 아니라 **조용히 equation 모드로 분석**될 수 있다. P1-b로 분리 |

---

## 3. 단계별 작업

### Stage 0 — 계약 회귀 테스트 먼저 (코드보다 테스트)

새 파일 `tests/test_p1_contract.py`:

| 케이스 | 기대 |
|---|---|
| `problem_type` 생략 요청 | 기존 `tests/test_attempt_api.py`의 정상 케이스와 **응답 JSON이 동일**(`correct` 타입 제외) |
| `problem_type="equation"` 명시 | 생략과 동일 결과 |
| `problem_type="expression"`, `target_form` 없음 | 200 (동치만 검사) |
| `target_form`만 보내고 `problem_type` 생략 | 422 `E_INVALID_TARGET_FORM` |
| `problem_type="equation"` + `target_form="expanded"` | 422 `E_INVALID_TARGET_FORM` |
| `target_form="factored_irreducible"`(미허용 값) | 422 (pydantic) |
| `problem_type="quadratic"`(미허용 값) | 422 (pydantic) |
| 알 수 없는 필드 추가 | 422 (`extra="forbid"` 유지) |

> 이유: AGENTS.md "정책 변경 시 테스트를 먼저 고친다". 여기서는 **정책을 바꾸지 않는다는 것**을 먼저 고정한다.

---

### Stage 1 — 스키마 + 전달 경로 (동작 변화 0)

**`app/schemas.py`**

```python
ProblemType = Literal["equation", "expression"]
TargetForm = Literal["integer", "expanded", "factored"]
```

1. `AnalyzeAttemptRequest`에 추가(둘 다 마지막에 append — 필드 순서 계약 아님):
   - `problem_type: ProblemType = Field("equation", description=...)`
   - `target_form: TargetForm | None = Field(None, description=...)`
   - `problem_skills` 설명의 "crosswalk(approved + provisional)" 문구는 A2 반영으로 어긋나 있으므로 **approved만** 으로 정정(같은 파일, 문서 수준)
2. `AnalyzeAttemptResponse.correct: bool` → **`bool | None`** (line 175)
3. `AnalysisSummaryItem.correct: bool` → **`bool | None`** (line 253, 분석 이력 목록)
4. `AnalyzeSolutionResponse.correct: bool` → **`bool | None`** (line 441, 레거시 응답도 같은 정책)

> `AttemptDetailResponse.correct`(line 348)는 이미 `bool | None`이므로 변경 없음.

> **하지 말 것:** 조합 검증(`equation` + `target_form`)을 pydantic `model_validator`로 넣지 않는다.
> pydantic 검증 실패는 `detail: {code,message}` 계약이 아닌 FastAPI 기본 형식으로 나가서
> Backend의 `detail.code` 분기가 깨진다. **조합 검증은 핸들러에서 `AnalysisError`로** 한다.

**`app/pipeline/session.py` — `Problem`**

```python
problem_type: Literal["equation", "expression"] = "equation"
target_form: Literal["integer", "expanded", "factored"] | None = None
```

- 저장소는 `model_copy(deep=True)`로 저장하므로(`repository.py`), 필드 추가만으로 보존된다. 별도 직렬화 코드 변경 없음. 단 **파일·DB 영속 저장소가 추가될 때**를 위해 기본값을 반드시 둔다(하위호환).
- `AttemptAnalysis.correct: bool` → **`bool | None`**

**`app/main.py`**

- `_resolve_problem()`: `Problem(...)` 생성 시 `problem_type=req.problem_type`, `target_form=req.target_form` 전달.
- 신규 헬퍼 `_validate_problem_type_request(req)`:
  - `problem_type == "equation" and target_form is not None` → `AnalysisError("E_INVALID_TARGET_FORM", ...)`
  - `problem_type == "expression" and target_form is not None` → 통과
  - 이 헬퍼는 `analyze_attempt_endpoint`에서 **`_normalize_backend_request` 직후, 저장·락 이전**에 호출
- `_ERROR_STATUS`는 현재 `E_INPUT_TOO_LARGE: 413`만 있고 나머지는 `_DEFAULT_ERROR_STATUS=422`다.
  신규 코드 3개는 **422가 기본값이므로 매핑 추가 불필요**(명시하고 싶으면 추가해도 되지만 계약 변화는 없다).

**`app/pipeline/analyzer.py`**

- `analyze(..., problem_type: str = "equation", target_form: str | None = None)` 파라미터 추가.
- Stage 1에서는 **값을 받아 검증만 하고 사용하지 않는다**(Stage 3에서 사용). 시그니처만 미리 열어 `analyze_solution()` → `analyze()` 경로의 silent failure를 막는다.
- `_validate_target_form()`: 허용 값 집합 밖이면 `E_INVALID_TARGET_FORM`.

**수용 기준**: `tests/test_p1_contract.py` 중 Stage 1 케이스 통과 + 기존 1722건 전부 통과.

---

### Stage 2 — 부등식 거절 (`E_UNSUPPORTED_INEQUALITY`)

**`app/pipeline/parser.py`** — 단일 소유

```python
_INEQUALITY = re.compile(r"(<|>|≤|≥|≠|\\(?:leq|geq|neq|lt|gt|le|ge)(?![A-Za-z]))")

def has_inequality(text: str) -> bool:
    ...
```

오탐 방지(필수):
- `\left(...` → `\le` 뒤가 `f`(영문)이므로 `(?![A-Za-z])`로 **제외**
- `\gets` → `\ge` 뒤가 `t`이므로 제외
- `\leq`/`\geq`/`\neq`는 `(?![A-Za-z])`에 걸리지 않아 검출됨
- 유니코드 `≤ ≥ ≠`, ASCII `< >` 검출
- 반드시 **raw 텍스트**에 적용(`normalize()`는 `\left`를 지워서 오탐이 사라짐 → 테스트 의미 없음)

**`app/pipeline/analyzer.py` / `app/pipeline/session.py`**

- `analyze()` 최상단(크기 검사 다음, `parse_line` 이전)과 `_check_parsing()`에서:
  ```python
  if has_inequality(problem_latex):
      raise AnalysisError(
          "E_UNSUPPORTED_INEQUALITY",
          "부등식 문제는 아직 지원하지 않습니다. 등식 또는 식 문제로 다시 시도해 주세요.",
      )
  ```
- `_ANALYSIS_STATUS_BY_ERROR`에 **넣지 않는다**(입력/지원 범위 오류 ≠ UNKNOWN).
- `problem_type`과 무관하게 **양쪽 모드 모두** 거절한다.

**저장 정책 버그 수정(같이 해야 함) — 실측 확인**

현재 `main.py`의 `except AnalysisError` 블록은 `NoTrackingSkillError`만 저장을 건너뛴다. 그래서
`E_NOT_EQUATION`·`E_UNSUPPORTED_INEQUALITY`·`E_INVALID_TARGET_FORM` 같은 **계약 오류에도
Problem이 저장**된다. **2026-11-16 실측**: `problem_latex="(x+1)(x+2)"` 요청 →
`422 E_NOT_EQUATION` + `list_problems()`에 `P-EXPR-CHK` **저장됨**.

D4(저장본이 source of truth)와 결합하면 **잘못된 유형으로 등록된 문제를 복구할 수 없다**
(같은 `problem_id`로 `problem_type="expression"`을 보내도 저장본이 이긴다).

```python
_NO_STORE_ERROR_CODES = {
    NoTrackingSkillError.code,
    "E_NOT_EQUATION",
    "E_INVALID_PROBLEM_TYPE",
    "E_INVALID_TARGET_FORM",
    "E_UNSUPPORTED_INEQUALITY",
}
...
if exc.code not in _NO_STORE_ERROR_CODES:
    _persist_new_records(...)
```

**테스트** (`tests/test_unsupported_inequality.py` 신규)

| 입력 | 기대 |
|---|---|
| `2x+1<5` | 422 `E_UNSUPPORTED_INEQUALITY`, `detail`에 `analysis_status` 없음 |
| `2x+1 \le 5` / `\leq` / `\geq` / `\neq` | 동일 |
| `x ≥ 3`, `x ≤ 3` | 동일 |
| `\left(2x+1\right)=5` | **통과**(200, 기존 동작) |
| `\gets` 포함 텍스트 | `E_UNSUPPORTED_INEQUALITY`가 아님(오탐 방지) |
| attempt 경로 | attempt/analysis/problem **모두 미저장** |
| `E_NOT_EQUATION` 재확인 | problem도 미저장(회귀) |

---

### Stage 3 — 표현식 동치 판정

**`app/pipeline/verifier.py`** — 신규 함수 (기존 `verify()` 시그니처 불변)

```python
def verify_expression(problem_latex: str, steps: list[str]) -> Verdict:
    """표현식 문제: 문제식 → 줄1 → 줄2 … 를 '직전 판정 가능 줄'과 동치 비교한다.

    등식 풀이(solve)를 하지 않는다. 정답식을 만들지 않는다(ADR-01).
    """
```

알고리즘:
1. `problem = parse_line(problem_latex)`; `problem[0] != "expr"`이면 호출부에서 이미 거절(Stage 3의 `_check_parsing`).
2. 각 줄 `parse_line(strip)`:
   - `None` → `valid[i]=None`, `unrecognized`에 1-based 추가(기존 의미 유지)
   - `("expr", e)` → 판정 후보
   - `("eq", ...)` → **S2 안전장치**: `valid[i]=None`으로 두되 `unrecognized`에는 **넣지 않는다**. 표현식 문제에 등식을 쓴 것은 "인식 실패"가 아니라 **유형 오분류 신호**이므로 `analysis_status`만 `REVIEW_REQUIRED`가 되게 한다.
3. 기준 노드 = 이전 판정 가능 줄(없으면 문제식). `equivalent(ref_node, cur_node)` 사용.
   - `equivalent`의 `expr` 분기는 `simplify(a-b)==0` + `_too_complex` 가드 → **`solve()` 호출 0회**(예산 소비 없음).
4. `Verdict(steps, parsed, valid, first_error_index, unrecognized, ref_step_no)`는 기존 규칙 그대로 채운다(1-based `ref_step_no`, 인식 실패 줄 건너뛰기).

**`app/pipeline/session.py` / `app/pipeline/analyzer.py` 분기**

- `_check_parsing(problem, steps, solution_text)`:
  ```python
  node = parse_line(problem.problem_latex)
  if has_inequality(...): raise E_UNSUPPORTED_INEQUALITY
  if problem.problem_type == "expression":
      if node is None:      raise E_BAD_PROBLEM
      if node[0] != "expr": raise E_INVALID_PROBLEM_TYPE
  else:
      if node is None:      raise E_BAD_PROBLEM
      if node[0] != "eq":   raise E_NOT_EQUATION
  ```
- `analyze_attempt()`: `problem_type == "expression"`이면 `verify_expression()`, 아니면 기존 `verify()`.
- `analyze(problem_type=...)`도 동일 분기.
- `E_INVALID_PROBLEM_TYPE`은 422, `analysis_status` 미부착, 저장 금지(Stage 2의 `_NO_STORE_ERROR_CODES`).

**`tests/test_expression_mode.py` 신규**

| 케이스 | 기대 |
|---|---|
| 문제 `(x+1)(x+2)`, 풀이 `x^2+3x+2` (1줄) | 200, `correct=True`, `valid=[True]` |
| `(x+1)(x+2)` → `x^2+3x+2` (2줄) | 200, `valid=[True, True]` |
| `(x+1)(x+3)` (틀림) | `correct=False`, `first_error_step_index=1` |
| `(x+1)(x+2)` → `(x+1)(x+2)` | `valid=[True, True]` (동치만 보므로 통과) |
| 문제식이 등식(`x^2-5x+6=0`)인데 `problem_type="expression"` | 422 `E_INVALID_PROBLEM_TYPE`, 저장 없음 |
| 풀이 줄이 등식(`x=2`) | `valid=[None]`, `analysis_status=REVIEW_REQUIRED`, `correct`는 D1 정책대로 |
| 풀이 줄 인식 불가(`보통 풀이`) | `unrecognized=[1]`, `valid=[None]`, REVIEW_REQUIRED |
| 전 줄 인식 불가 | 422 `E_UNRECOGNIZED` (기존 정책) |
| `problem_type` 생략 + 식 문제 | 422 `E_NOT_EQUATION` (기존 동작 불변) |

**성능 가드**: 표현식 경로는 `solve()`를 부르지 않는다. `test_complexity_guard.py`에
`verify_expression`이 `_SolveBudget`을 소비하지 않는 케이스 1건 추가.

---

### Stage 4 — `target_form` 형태 검사 (D2·D3 의존)

**신규 `app/pipeline/form.py`** — 형태 판정 단일 소유

```python
FormKind = Literal["integer", "expanded", "factored"]

def form_matches(expr, target_form: str) -> bool | None:
    """True=형태 충족, False=불충족, None=판정 불가(오판 금지)."""
```

판정 규칙:

| target_form | 판정 | 예 |
|---|---|---|
| `integer` | `expr.free_symbols == set()` | `12` O / `-3/4` O / `2x` X |
| `expanded` | `expand(expr)`가 구조적으로 `expr`과 동일(미전개 `Mul`/`Pow` 없음) | `x^2+3x+2` O / `(x+1)(x+2)` X / `x(x+2)` X / `x^2` O |
| `factored` | **비상수 인수 ≥ 2**(느슨함). `Pow`는 양의 정수 지수면 반복 인수로 센다 | `(x+1)(x+2)` O / `x(x^2+3x+2)` O / `(x+1)^2` O / `x^2+3x+2` X |

판정 불가(`None`)로 두는 경우:
- 분모에 자유 기호가 있는 유리식(`1/(x+1)+x`) — P1 범위 밖
- `count_ops(expr) > 200` — 과도 연산(오판·지연 방지)
- SymPy 예외

`None`은 **오류로 만들지 않는다.** 대신 `analysis_status="REVIEW_REQUIRED"`로 남긴다(조용한 통과 금지, 조용한 오답 금지).

**적용 위치**

- **마지막 판정 가능 줄**에만 적용한다.
- 이미 `valid=False`인 줄이 있으면 형태 검사는 **하지 않는다**(첫 오류 우선).
- 형태 불일치 → 마지막 줄 `valid=False` + 아래 `ErrorRecord` 1건 추가.

**형태 불일치 ErrorRecord (classifier 무변경)**

`classify()`는 `problem_latex`의 Skill을 모르므로, 형태 오류는 session에서 직접 만든다.
taxonomy에 존재하는 값만 사용하므로 "분류는 taxonomy 조회로만" 원칙을 지킨다.

```python
ErrorRecord(
    error_id=f"{attempt.attempt_id}-E{len(errors) + 1}",
    attempt_id=attempt.attempt_id,
    step_no=마지막_줄_번호,
    error_type="procedure",
    error_subtype="method_mismatch",
    skill_ids=[problem.primary_tracking_skill()],
    misconception_ids=[],          # ADR-02: 근거 없으므로 오개념 미부착
    confidence=0.8,
    evidence_latex=마지막_줄_원문,
    description_ko="요구한 답의 형태와 다릅니다(예: 전개/인수분해).",
)
```

`api_response`의 `error_type`/`error_subtype`/`error_step`/`skill`은 기존 규칙대로
`errors[0]`(최소 step_no)에서 채운다 → 별도 변경 불필요.

**테스트** (`tests/test_target_form.py` 신규): 3종 × {충족, 불충족, 판정 불가} + 형태 불일치 시
`error_type="procedure"`, `misconception_ids=[]`, `error_step`이 마지막 줄인지 + 형태 불일치가
`correct=False`인지 + `target_form` 없으면 형태 검사 안 함.

---

### Stage 5 — 항등식/모순 문제 → `REVIEW_REQUIRED` + `correct=null` (D1 의존)

**`app/pipeline/verifier.py`**

```python
def problem_degeneracy(problem_latex: str) -> Literal["identity", "contradiction"] | None:
    """문제식이 항등식(모든 x) 또는 모순(해 없음)이면 그 종류를 돌려준다.

    `_residual_kind()`를 그대로 재사용한다. 판정 불가는 None.
    """
```

**`app/pipeline/analyzer.py` / `app/pipeline/session.py`**

문제식이 degenerate면 분석은 그대로 수행하되 결과를 아래로 고정한다:

| 필드 | 값 |
|---|---|
| `analysis_status` | `REVIEW_REQUIRED` |
| `correct` | **`None`** |
| `error_step` / `error_type` / `error_subtype` | `None` |
| `errors` | `[]` |
| `observations` | `[]` (**Knowledge State 갱신 금지**) |
| `state` / `mastery` | 현재 값 **조회만**(변경 없음) |
| `valid` / `steps_latex` / `unrecognized` | 계산 결과 그대로 |

- `attempt` 경로도 동일하게 `observations=[]`, `states={}`, `mastery={}`.
- `_to_attempt_response` / `_build_step_list`는 수정 불필요(이미 `api_response`를 읽음).

**반드시 유지해야 하는 것**

- `tests/test_analysis_status.py::test_correct_unchanged_under_review_required` —
  "인식 실패에서는 `correct`가 `true/false`" 는 **그대로 통과**해야 한다. 이 테스트를 고치지 않는다.
- `tests/test_attempt_api.py`의 기존 REVIEW_REQUIRED 케이스(`correct is False`)도 그대로.

**테스트** (`tests/test_degenerate_problem.py` 신규)

| 문제식 | 기대 |
|---|---|
| `5x-3=5x-3` (항등식) | 200, `REVIEW_REQUIRED`, `correct is None`, `observations=[]` |
| `2x+1=2x+2` (모순) | 동일 |
| `(x+1)(x+2)=x^2+3x+2` | 동일 |
| `x^2-5x+6=0` (정상) | `ANALYZED`, `correct: true/false` (불변) |
| 상태 미갱신 확인 | 같은 학생·skill로 정상 문제 전후 `mastery` 불변 |

---

### Stage 6 — 문서·에러표·통보

| 파일 | 변경 |
|---|---|
| `README.md` | 에러 코드 표에 `E_UNSUPPORTED_INEQUALITY`·`E_INVALID_TARGET_FORM`·`E_INVALID_PROBLEM_TYPE` 추가(모두 422, `analysis_status` 없음) |
| `docs/AI1_명세서_대조표.md` | P1 반영 절 추가(필드·에러 코드·`correct` 널 범위 D1) |
| `docs/AI1_HANDOFF.md` | Phase 9-9C.9H 기록(Stage별 결과·테스트 수) |
| `docs/AI1_표현식문제_계약초안_A6.md` | Stage 4·5 결과 반영, B2·B3·B4 상태 갱신 |
| Backend 통보문(신규 1건) | 아래 4개 |

Backend 통보 필수 항목:
1. 신규 에러 코드 3개와 HTTP/`analysis_status`/저장 정책
2. `correct: null` 범위 = **D1(신규 판정 불가 케이스 한정)** — 전면 REVIEW_REQUIRED 아님
3. B2(`problem_type`/`target_form` 분리 전송)·B3(eq 줄 → REVIEW_REQUIRED) 적용 결과
4. D4: `problem_type`은 최초 등록 시에만 유효(저장본 우선)

---

## 4. 파일별 변경 요약

| 파일 | Stage | 변경 |
|---|---|---|
| `app/schemas.py` | 1 | `ProblemType`/`TargetForm` 타입, 요청 필드 2개, `correct: bool\|None` ×3 |
| `app/pipeline/session.py` | 1·2·3·4·5 | `Problem` 필드 2개, `AttemptAnalysis.correct` 널 허용, `_check_parsing` 모드 분기, `verify_expression` 분기, 형태 오류 ErrorRecord, degenerate 분기 |
| `app/pipeline/analyzer.py` | 1·2·3·5 | `analyze()` 파라미터, 부등식 거절, 모드 분기, degenerate 결과 |
| `app/pipeline/parser.py` | 2 | `has_inequality()` |
| `app/pipeline/verifier.py` | 3·5 | `verify_expression()`, `problem_degeneracy()` (`verify` 시그니처 불변) |
| `app/pipeline/form.py` | 4 | **신규** 형태 판정 |
| `app/main.py` | 1·2 | 요청 검증 헬퍼, `_resolve_problem` 전달, `_NO_STORE_ERROR_CODES`, `_ERROR_STATUS` |
| `app/pipeline/repository.py` | — | **변경 없음**(모델 직렬화) |
| `app/pipeline/classifier.py` | — | **변경 없음**(형태 오류는 session 합성) |
| `data/*.json` | — | **변경 없음**(taxonomy frozen) |

---

## 5. 완료 기준 (DoD)

- [ ] `tests/test_p1_contract.py`, `test_unsupported_inequality.py`, `test_expression_mode.py`, `test_target_form.py`, `test_degenerate_problem.py` 신규 통과
- [ ] 기존 1722건 **전부** 통과(정책 테스트 수정 없음)
- [ ] `pyflakes app tests eval` 0건
- [ ] `problem_type` 생략 요청의 응답이 변경 전과 동일(골든 회귀)
- [ ] 표현식 경로 `solve()` 호출 0회(예산 미소비) 확인
- [ ] 전체 suite 소요가 기존 대비 유의미하게 증가하지 않음(±5% 이내, 동일 머신 2회 측정)
- [ ] `_MAX_STEPS`·문자 상한·`solve()` 예산 상수 **무변경**
- [ ] README·대조표·HANDOFF·Backend 통보문 갱신
- [ ] 서버 재시작 없음(배포는 별도 승인)

---

## 6. 하지 말 것

- 기존 응답 필드 삭제·개명, `/analyze-solution` 경로 제거
- `data/skills.json`·`error_types.json`·`misconceptions.json` ID 수정
- `weakness_score`·초기 State 시드·skill↔concept 매핑·목록 API 추가(Q1~Q7 종결 사항)
- `problem_type` 생략 시 `=` 유무 자동 판정(D5, B1 확정 전)
- `correct` 전면 널화(D1 범위 초과)
- 부등식·연립·집합·함수 표기를 P1에서 지원하려는 시도(범위 밖, 422로 거절)
- 새 의존성, `_MAX_*` 상향, multipart/OCR 변경
- 커밋·푸시·staging·서버 재시작

---

## 7. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| `expanded` 판정이 `expand` 구조 비교에 의존해 오판 | 형태 판정은 **마지막 줄에만**, 판정 불가는 `None` → REVIEW_REQUIRED. 3종 × 12케이스 테스트로 고정 |
| 형태 불일치를 계산 오류로 오분류 | `procedure/method_mismatch` + 오개념 미부착. 오답으로만 두고 오개념은 만들지 않음 |
| 표현식 모드에서 학생이 등식으로 풀이 | 조용한 true/false 금지(S2) → 해당 줄 `None`, `REVIEW_REQUIRED` |
| `problem_type` 미전송 시 표현식 문제가 전부 422 | **의도된 동작**(D5). B2 확정 후 Backend가 `problem_type`만 먼저 보내면 해소. AI①이 임의 자동 판정하지 않는다 |
| 계약 오류에 Problem이 저장되어 복구 불가 | Stage 2의 `_NO_STORE_ERROR_CODES`로 차단 + 회귀 테스트 |
| `correct: null`로 기존 클라이언트 파손 | D1 범위 한정. `ANALYZED` 응답은 계속 `true/false`만. APP 확인 항목으로 남김 |

---

## 8. 작업 순서 (권장)

1. Stage 0 + Stage 1 → 커밋 없이 green 확인
2. Stage 2 (부등식 + 저장 정책 수정) → Backend 통보 가능 상태
3. Stage 3 (표현식 동치) → **여기까지가 기획 결정 없이 배포 가능한 최소 단위**
4. Stage 4 (D2·D3 확정 후)
5. Stage 5 (D1 확정 후 — D1은 AI① 채택안으로 진행 가능)
6. Stage 6 (문서·통보)

> 각 Stage 종료 시 `pytest`·`pyflakes` 결과와 변경 파일 목록을 보고한다.

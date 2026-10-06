# STEPi Ai1

## learning-intelligence — AI① 풀이 분석 파이프라인

P5 적응형 학습 튜터의 **AI① Learning Intelligence** 구현체.
텍스트 풀이를 입력받아 SymPy로 단계별 동치를 판정하고, 첫 오류 줄을 특정한 뒤
오개념 트리에 분류하여 Knowledge State를 갱신한다.

## 파이프라인

```
텍스트 풀이 입력
  → 단계 분리 (parser.split_steps)
  → SymPy 동치 판정 (verifier.verify)      ← LLM 단계분석은 이 지점에 주입 (segmenter 인자)
  → 오류 유형 분류 (classifier.classify)     ← taxonomy 조회 기반, 자유생성 금지(ADR-02)
  → Knowledge State 갱신 (knowledge.rule | bkt)
  → 계약 JSON 반환
```

- **인식 실패는 오답이 아님** (ADR-03): 파싱 실패 줄은 `unrecognized`로 분리하고 `valid=null`
- **판정 불가 시 추측 금지**: SymPy가 동치를 확정한 구간만 오류로 표시
- **엔진 2종**: `rule` (Stage 0 MVP), `bkt` (Stage 1, BKT 수식 직접 구현)
- **taxonomy이 runtime source of truth**: Skill / error_type / error_subtype /
  misconception ID를 `data/*.json`에서만 조회한다

## 디렉터리

```
app/
  main.py                 FastAPI — POST /analyze-solution, GET /health
  schemas.py              입출력 JSON 계약 (Pydantic)
  taxonomy.py             frozen taxonomy 정적 데이터 모델 · 검증 · runtime 조회 계층
  pipeline/
    parser.py             정규화 · LaTeX 파싱(중첩 \frac) · 기호 정책 · 단계 분리
    verifier.py           SymPy 동치 판정 → valid[], 첫 오류 줄, 복잡도 가드
    classifier.py         오류 유형 → taxonomy 매핑 (분배/이항/오독/계산)
    knowledge.py          Stage 0 규칙 기반 + Stage 1 BKT, 상태 상한/eviction
    analyzer.py           오케스트레이션 + 입력 정책 검증 + 엔진 레지스트리
data/
  skills.json             Skill 31개 (base 26 + expansion 5)
  error_types.json        error_type 4개 + error_subtype 17개
  misconceptions.json     오개념 19개
tests/
  golden/golden_100.json  Golden 100건 (정답 44·계산 28·분배 11·이항 12·오독 5)
  golden/golden_20.json   초기 20건
  test_parser.py          정규화·단계 분리·중첩 분수·기호 정책
  test_verifier.py        동치 판정 + gap-bridging
  test_verifier_multivariate.py  다변수 동치 판정 (S1)
  test_classifier.py      오류 분류(분배/이항/오독/계산, 다중 괄호 포함)
  test_knowledge.py       규칙 기반 + BKT
  test_knowledge_capacity.py  student_id 정책·상태 상한·eviction·동시성 (S5)
  test_perf_budget.py     step 한도·solve 예산·복잡도 가드 (S2)
  test_skill_validation.py  problem_skill 검증 (S4)
  test_taxonomy_runtime.py  classifier ↔ taxonomy 바인딩 (S3)
  test_taxonomy_validation.py  taxonomy 정합성 + API 하위호환
  test_taxonomy_validation_rules.py  validation 강화 + 실패 fixture (S9)
  test_answer_only_policy.py  정답 단독 제출 정책 (S7)
  test_hygiene_fixes.py   HTTP 상태 매핑·extra 금지·전이 표·분수·기호 (S11~S15)
  test_api.py             계약 필드 + 에러 코드 검증
  test_complexity_guard.py  복잡도/크기 가드 (hang 방지)
  test_golden_regression.py  회귀 게이트 (error_step·error_type ≥ 0.85)
```

## 실행

```bash
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest            # Phase 8-11 기준 940 passed
.venv\Scripts\pyflakes app tests          # lint
.venv\Scripts\uvicorn app.main:app --reload
```

## API

### `GET /health`

```json
{ "status": "ok" }
```

### `POST /analyze-solution`

요청 필드 (`AnalyzeSolutionRequest`, 알 수 없는 필드는 422):

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `problem_id` | string? | null | 문제 ID (선택) |
| `problem_latex` | string | (필수) | 문제식 LaTeX 예: `2(x-3)=6` |
| `solution_text` | string | (필수) | 학생 풀이. 줄바꿈/화살표(→)로 단계 구분 |
| `student_id` | string | `anonymous` | 상태 저장 키. 1~64자, 제어문자 불가 |
| `engine` | `rule` \| `bkt` | `rule` | Knowledge State 엔진 |
| `problem_skill` | string | `linear_equation` | **tracking** Skill ID. taxonomy의 `bkt_eligible=true` leaf만 허용 |

응답 필드 (`AnalyzeSolutionResponse`) — 기존 11개 + additive 4개:

| 필드 | 설명 |
|---|---|
| `correct` | **현재 입력된 풀이에서 오류가 검출되지 않았는지** (완전 검증 아님 — 아래 S7 정책) |
| `error_step` | 첫 오류 줄 번호 (1-based), 없으면 null |
| `error_type` | `calculation` \| `concept_error` \| `procedure` \| `comprehension` \| null |
| `skill` | 기존 단일 primary Skill (하위호환) |
| `state` | `mastered` \| `learning` \| `needs_practice` |
| `mastery` | 숙련도 (rule=상태 대표값 / bkt=p_known) |
| `misconception_id` | 기존 단일 오개념 ID (하위호환) |
| `confidence` | 분류 신뢰도, 정답 시 null |
| `steps_latex` | 인식된 풀이 단계 원문 |
| `valid` | 단계별 동치 판정. `null` = 판정 보류 |
| `unrecognized` | 인식 실패/판정 불가 줄 번호 (1-based) |
| `skills` | 문제와 연결된 Skill ID 목록 (taxonomy 기준, 개념적 분류 — group node 포함 가능) |
| `skill_ids` | **`skills`의 canonical alias.** 호환성 때문에 현재는 동일한 값을 담으며, 향후 정렬·canonicalization이 필요해질 때 반영할 예약 필드 |
| `error_subtype` | 오류 세부 형태 (`error_subtype_id`), 정답 시 null |
| `misconception_ids` | 연결된 오개념 목록. **근거가 있을 때만** 채워지므로 단순 계산 오류는 `[]` |

```bash
curl -X POST http://127.0.0.1:8000/analyze-solution ^
  -H "Content-Type: application/json" ^
  -d "{\"problem_latex\":\"2(x-3)=6\",\"solution_text\":\"2(x-3)=6\\n2x-3=6\",\"student_id\":\"s1\"}"
```

```json
{
  "correct": false, "error_step": 2, "error_type": "concept_error",
  "skill": "distribution", "state": "needs_practice", "mastery": 0.3,
  "misconception_id": "2.1", "confidence": 0.9,
  "skills": ["distribution", "polynomial_multiplication"],
  "skill_ids": ["distribution", "polynomial_multiplication"],
  "error_subtype": "distribution_omit", "misconception_ids": ["2.1"]
}
```

## Attempt 분석 및 이력 API (Phase 8-3~8-11)

Attempt 단위 분석은 기존 text API와 별도로 제공한다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/v1/attempts/{attempt_id}/analyze` | Attempt 분석·저장. 동일 제출 재요청은 멱등 처리 |
| `GET` | `/api/v1/attempts/{attempt_id}/analyses` | Attempt의 분석 이력 목록 |
| `GET` | `/api/v1/attempts/{attempt_id}/analyses/{analysis_id}` | 저장 시점 분석 스냅샷 |
| `GET` | `/api/v1/students/{student_id}/history` | 페이지네이션·기간 필터가 적용된 학생 풀이 이력 |
| `GET` | `/api/v1/students/{student_id}/misconceptions/top` | 시도별 반복 오개념 집계 및 기간 필터 |

기간 필터 `from`/`to`는 timezone offset을 포함한 ISO 8601 시각이며,
`[from, to)` 구간으로 조회한다. 전체 API 계약과 저장 정책은
[`docs/AI1_명세서_대조표.md`](docs/AI1_명세서_대조표.md)를 참고한다.

## 에러 코드

| code | HTTP | 발생 조건 |
|---|---|---|
| `E_INPUT_TOO_LARGE` | **413** | 문제식 2000자·풀이 2만자·30단계 초과 |
| `E_EMPTY_SOLUTION` | 422 | 풀이 입력이 비어 있음 |
| `E_BAD_PROBLEM` | 422 | 문제식 파싱 불가 → `correct` 단정 금지 |
| `E_UNRECOGNIZED` | 422 | 풀이 전량 인식 실패 |
| `E_UNVERIFIABLE` | 422 | 어떤 줄도 동치 판정 성공 실패 → `correct` 단정 금지 |
| `E_UNKNOWN_SKILL` | 422 | taxonomy에 없는 `problem_skill` |
| `E_SKILL_NOT_TRACKABLE` | 422 | `bkt_eligible=false` (group node/future skill) |
| `E_INVALID_STUDENT_ID` | 422 | 빈 값·64자 초과·제어문자 포함 |

정답/오답을 확정할 수 없는 입력은 422로 거절한다. 어림 단정하지 않는 것이 계약이다.
요청 스키마 위반(알 수 없는 필드·타입 오류)은 FastAPI가 422로 처리한다.

## Taxonomy

frozen — `data/*.json`의 ID는 확정 후 변경하지 않는다(`skill_id`는 BKT Knowledge State key).

| 파일 | 내용 |
|---|---|
| `skills.json` | base 26 (BKT 대상 24) + expansion 5 (`status: future`: 지수·로그·삼각·수열) |
| `error_types.json` | `calculation` / `concept_error` / `procedure` / `comprehension` + subtype 17 (active 16 + future 1) |
| `misconceptions.json` | 오개념 19 (`2.1` 분배법칙 누락, `3.1` 이항 시 부호 미변경 유지) |

대단원: 기초(선행 복습) · 다항식의 연산과 인수분해 · 방정식과 부등식 · 집합과 명제 · 함수

`validate_all()` 이 검사하는 것: ID 고유성, 참조 무결성(선행·부모·관련 skill),
subtype→부모 error_type 소속, 기존 ID 유지, `bkt_eligible` boolean, 공통수학1 범위.

---

## 알려진 한계

- `unrecognized` 줄 구간은 부분 검증 — `correct=true`가 완전 검증을 뜻하지 않음 (ADR-03 설계상 트레이드오프)
- **다변수 등식은 '오탐 회피' 우선**: 해 집합이 매개변수 형태면 비교를 보류해 `None`(검증 불가)이 되고 오류로 검출되지 않는다. 한쪽만 해가 없거나 양쪽이 구체적(concrete) 해 집합일 때만 `False`
- `x^2=16 → x=4`는 동치 판정 기준으론 오류(해 누락) — 교육적 판단은 다를 수 있음
- 이항 계열의 "항 누락"은 `3.1`로 분류된다(taxonomy의 `3.2`가 더 정확하지만 ID 추가 없이 매핑을 바꾸지 않음)
- 분배의 "부호 오류"(`2(x-3) → 2x+3`)는 전용 subtype이 없어 `distribution_omit`으로 분류
- 파서는 단일 문자 변수와 `sqrt`만 허용한다. `sin`·`cos`·`log`는 taxonomy상 future 영역이라 차단된다
- LaTeX `\sqrt{}` 형태는 미지원(`sqrt(...)`만 동작). 중첩 `\frac`는 지원한다
- Knowledge State는 in-memory(학생 10,000명 상한·LRU)이며 BKT 파라미터는 미피팅
- 오류 유형 판별 범위는 중학 일차방정식 중심이다

## `correct` 필드의 의미 (S7 확정 정책)

> **`correct=true`는 입력된 풀이에서 오류가 검출되지 않았음을 의미하며,
> 풀이 과정의 완전성이나 최종 정답의 독립적인 증명을 의미하지 않는다.**

따라서 아래와 같이 **풀이 과정 없이 최종 답만 제출해도 `correct=true`가 허용된다.**

```jsonc
// 요청
{ "problem_latex": "2x=6", "solution_text": "x=3" }
// 응답 (주요 필드)
{ "correct": true, "error_step": null, "error_type": null, "valid": [true] }
```

- 판정은 수행되었고(문제식과 대조) 오류가 검출되지 않았을 뿐이다
- 중간 단계 생략도 현재는 오류로 보지 않는다(예: `2x=6 → x=3`은 3x=12 단계를 건너뛰어도 valid)
- `unrecognized` 줄이 있는 경우에도 `correct=true`일 수 있으며, 이는 부분 검증이다
- 이 단계에서 **동작을 변경하지 않았다.** 새 validation state나 API 필드도 추가하지 않았다.
  정책 변경이 필요해지면 `tests/test_answer_only_policy.py`가 즉시 실패하도록 고정해 두었다

---

## 변경 이력 (코드 리뷰 S1~S17)

### PHASE 1 — Critical

- **S1 다변수 등식 동치 판정 오류** — 다변수에서 잔차 '차이' 비교를 하던 것이
  `x+y=3 ↔ 2x+2y=6`을 비동치로 오판했다. 잔차가 0이 아닌 상수배면 `solve()` 없이
  `True`를 확정하는 fast path를 추가하고, 나머지 다변수는 시스템 해 집합을 비교한다.
  fast path는 `True`만 반환하므로 1변수 기존 판정(`x^2=0 ↔ x=0` 동치,
  `x^2=16 ↮ x=4` 비동치)은 원천적으로 보존된다. 조건부·매개변수 결과는 `False`가
  아니라 `None`(검증 불가)으로 남긴다
- **S2 성능/복잡도 가드** — `_too_complex()`가 차수만 통과시키 `count_ops`에
  도달하지 못했고(가드 무력), step 상한 200으로 2차식 200단계가 13.1초를 소요했다.
  차수+연산량+항 개수를 함께 검사하고, 상수배 fast path로 `solve()`를 최소화하며,
  요청당 `solve()` 횟수 예산(80회)을 추가했다. 상한은 30으로 하향(설정 상수).
  **200단계 2차식이 13.1초/200 OK → 0.006초/413**으로 개선
- **S10 dead branch 제거** — S1 재구성 과정에서 함께 해소

### PHASE 2 — High

- **S4 problem_skill 검증** — `bkt_eligible`을 source of truth로 삼아 미등록 ID는
  `E_UNKNOWN_SKILL`, group node/future skill은 `E_SKILL_NOT_TRACKABLE`로 거절한다.
  Knowledge State 키는 검증 통과한 값만 사용한다
- **S3 taxonomy runtime 통합** — classifier의 하드코딩을 `_TAXONOMY_BINDING` 단일
  매핑표로 교체하고 `app/taxonomy.py` 조회 계층을 추가했다. 기존 4 skill·2
  misconception ID와 confidence는 그대로 유지. 계산 오류에는 오개념을 붙이지 않는다.
  응답에 additive 4개 필드를 노출했다

### PHASE 3 — Medium

- **S6 distribution detector** — `_PAREN_FACTOR_RE.search()`가 첫 괄호만 검사해
  `2(x-1)+3(x-1) → 2(x-1)+3x-1`을 놓쳤다. `finditer()`로 전체 순회하되 괄호 하나씩만
  치환해 등치 증거가 있을 때만 검출한다(오류가 발생한 괄호 위치 evidence 반환)
- **S7 정답만 제출 정책 문서화** — 동작 변경 없이 schema description과 README에
  의미를 명시하고 정책 고정 테스트를 추가했다
- **S8 golden 상태 오염** — case를 2회 실행하며 `state` 허위 mismatch 44건이 보고되던
  구조를 case 단위 `reset_knowledge()` 격리 + 1회 실행 결과 재사용으로 변경했다.
  **44건 → 0건**

### PHASE 4 — Memory / Validation

- **S5 Knowledge State 방어** — `student_id` 정책(trim·비어있음·64자·제어문자 금지),
  학생 수 상한 `MAX_KNOWLEDGE_STUDENTS=10_000` + 학생 단위 LRU eviction, RLock
  동시성 보호를 추가했다. 학생 2000명 기준 약 4MB로 상한 내 합리적이며, 정상
  사용자는 전부 유지된다
- **S9 taxonomy validation 강화** — subtype→부모 error_type 소속, `name` blank,
  `example_patterns` blank/빈 목록 검증을 추가했다. 실패 fixture는 `tmp_path` 복사본을
  사용해 frozen taxonomy은 수정하지 않았다. `related_skill_ids`는 개념적 관계이므로
  group node를 허용한다(BKT 추적은 별도 강제)

### PHASE 5 — 위생

- **S11 HTTP 상태 매핑** — `E_INPUT_TOO_LARGE`만 413, 나머지 AnalysisError는 422.
  매핑 없는 코드는 기존대로 422
- **S12 알 수 없는 필드 거부** — `AnalyzeSolutionRequest`에 `extra="forbid"`.
  분석 API 요청에만 적용하고 응답 모델은 그대로 둔다
- **S13 procedure 처리** — `else` 분기가 `procedure`까지 `needs_practice`로
  밀어넣던 것을 `_ERROR_TYPE_TO_STATE` 표로 명시했다. procedure는 개념 오류가 아니므로
  `learning`(부분 정답)으로 전이한다. BKT는 `is_correct_outcome()`으로 이진 관측만 하며
  error_type을 outcome으로 쓰지 않는다
- **S14 중첩 `\frac`** — 정규식 확장이 아니라 중괄호 깊이 기반 매칭(`_match_brace_group`)으로
  재귀 전개한다. `\frac{\frac{1}{2}}{3}` = 1/6, `\frac{1}{\frac{2}{3}}` = 3/2
- **S15 SymPy 특수 상수 차단** — `_ALLOWED`(문자 전면 허용)를 기호 정책으로 교체했다.
  단일 문자는 `E·I·N·O·Q·S`를 제외하고 허용하고, 함수는 `sqrt`만 허용한다
  (`pi`·`oo`·`sin` 등 차단). `x` 등 기존 단일 변수는 유지된다
- **S16 중복 API 테스트** — `test_all_unrecognized_422`와
  `test_all_lines_unrecognized_is_recognized_error` 는 단언이 같고 입력 형태만
  달랐다(1줄 vs 2줄). 완전히 동일하지 않아 삭제하지 않고 파라미터라이즈로 병합했다
  (시나리오 2개 유지)
- **S17 README 갱신** — 이 문서

### PHASE 1~5 당시 검증 현황

| 항목 | 결과 |
|---|---|
| pytest | **345 passed**, 1 warning (Starlette httpx deprecation) |
| lint | `pyflakes app tests` → 0건 |
| taxonomy validation | `validate_all()` → `[]` |
| golden regression | 100 case · `correct`/`error_step`/`error_type`/`skill`/`state` **각 1.000** · 실패 0 · false mismatch 0 |
| API smoke | PHASE 1~5 누적 15개 시나리오 통과 |

기존 테스트는 삭제하지 않았고(271 → 345, 전부 증가), golden expected 값도 변경하지 않았다.

## 다음 단계 (기획 로드맵)

1. **단계 분리 LLM 주입** — `analyze(segmenter=...)` 인자로 교체
2. **BKT EM fitting** — `golden` 데이터로 파라미터 튜닝
3. **MySQL 연동** — in-memory Knowledge State → `mastery` 테이블 교체
   (현재 상한/eviction 정책이 이때 DB 캐시 정책으로 대체된다)
4. **수식 OCR** (3단계) — 이미지 → LaTeX 입력 경로 추가
5. **실사용 검증** — 10인 필기 수집(기획서 7.1) 이후 golden 재구성

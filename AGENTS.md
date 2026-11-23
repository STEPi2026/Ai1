# AGENTS.md — AI① Learning Intelligence 개발 규칙

프로젝트: STEPi P5 적응형 학습 튜터의 **AI① Learning Intelligence** (풀이 과정 → 최초 오류 → 오개념 → Skill별 Knowledge State).
원본 명세: `docs/STEPi_기능명세서_원본.md` · AI① 대조표: `docs/AI1_명세서_대조표.md`

## 명령

```bash
.venv\Scripts\python -m pytest            # 전체 (현재 1875 passed)
.venv\Scripts\pyflakes app tests eval     # lint (0건 유지)
.venv\Scripts\uvicorn app.main:app --reload
```

새 의존성을 추가하기 전에는 **먼저 사용자 승인을 확인한다.** Google Vision SDK는
이번 명시적 Vision 연동 요청에 따라 `requirements.txt`에 추가하고 프로젝트 `.venv`에
설치했다. 이후 새 의존성은 별도 승인 없이 추가하지 않는다.

## 변경 시 반드시 지킬 것

### 1. 계약은 additive — 기존 필드 삭제/개명 금지

`AnalyzeSolutionResponse`의 기존 11개 필드(`correct` `error_step` `error_type` `skill` `state`
`mastery` `misconception_id` `confidence` `steps_latex` `valid` `unrecognized`)는 하위호환 계약이다.
필요한 값은 새 필드로 추가한다. `tests/test_taxonomy_validation.py`가 하위호환을 고정한다.

`AnalyzeSolutionRequest`는 `extra="forbid"`다. **요청에 새 필드를 추가할 때는
`analyze_solution()` → `analyze()` 경로까지 함께 내려야 한다** (조용히 무시되는 silent failure 금지).

### 2. taxonomy은 frozen

`data/skills.json` / `error_types.json` / `misconceptions.json`의 ID는 확정값이다.
오해가 있어도 ID를 바꾸지 말고 매핑 계층을 추가한다.
근거: DB `misconceptions.code`는 `M001` 체계인데 taxonomy은 `2.1`/`3.1`이다 → **taxonomy을 고치지 않고
매핑 테이블을 둔다.** (대조표 §4-3)

`classify()`의 하드코딩은 `_TAXONOMY_BINDING` 단일 매핑표로만 추가한다.

### 3. AI①은 정답·힌트를 만들지 않는다 (ADR-01)

AI②(`tutor_actions`)의 몫이다. `socratic_hint_message`·`message` 성격의 필드를
AI① 응답에 추가하지 않는다.

### 4. 오개념은 근거가 있을 때만 붙인다 (ADR-02/03)

- 분류는 taxonomy 조회로만. 자유생성 금지.
- `calculation` 오류에 오개념을 붙이지 않는다 → `misconception_ids: []`.
- 인식 실패/판정 불가 ≠ 오답. `unrecognized`로 분리하고 `valid=null`을 유지한다.
  단정할 수 없는 입력은 422로 거절한다(`E_BAD_PROBLEM` `E_UNRECOGNIZED` `E_UNVERIFIABLE`).
- `correct=true`는 "오류가 검출되지 않음"이지 완전 검증이 아니다. 이 정책은
  `tests/test_answer_only_policy.py`로 고정돼 있다. **정책 변경 시 이 테스트를 먼저 고쳐야 한다.**

### 5. 상태 전이는 표로만

`error_type` → `state`는 `knowledge._ERROR_TYPE_TO_STATE` 표가 단독 소유한다.
`concept_error`/`comprehension` → `needs_practice`, `calculation`/`procedure` → `learning`.
BKT는 `is_correct_outcome()`으로 **이진 관측만** 한다(error_type을 outcome으로 쓰지 않는다).

### 6. 성능 가드 유지

`_MAX_STEPS=30` · 문제식 2000자 · 풀이 2만자 · 요청당 `solve()` 80회 예산(S2).
가드를 통과시키기 위해 상한을 올리지 말고 상수 배치를 개선한다.

## 명세서에서 온 필수 반영 사항 (미구현 — 구현 시 체크)

대조표 기준 AI①이 아직 갖고 있지 않은 것:

- **이미지 입력 경로**: 앱 r36~r43 (촬영 → 크롭 → 품질검사 → multipart 업로드).
  현재 text only. 흐림/어두움/빈 이미지 → "인식 불가 코드" 필요.
- **`analysis_status`**: ~~현 계약은 `unrecognized` 배열로만 표현한다~~ → **구현됨(2026-11-13)**.
  `ANALYZED` / `REVIEW_REQUIRED` / `UNKNOWN`. UNKNOWN은 422 + `detail.analysis_status`.
  `REVIEW_REQUIRED`는 `correct=null`이며 Knowledge State를 갱신하지 않는다.
- **응답 필드 네이밍**: 앱이 `step_list` / `is_correct` / `first_error_step_index` /
  `misconception_tag`를 읽는다. 현 필드는 `steps_latex` / `valid` / `error_step` / `misconception_ids`.
  앱을 현 계약에 맞출지 alias를 노출할지 **결정 필요**(대조표 §4-4).
- **분석 이력 식별자**: `ai_analysis_results.attempt_id` + 앱 r57(재촬영 시 회차 연결).
  `problem_id` 문자열만으로는 회차를 구분할 수 없다.
- **`weakness_score`**: `student_states`에 필수 컬럼. `1 - mastery` 파생 여부 결정 필요.
- **초기 Knowledge State 시드**: 앱 r27(진단평가 결과 → 최초 Knowledge State 생성 트리거).
- **skill ↔ concept 매핑**: `concepts`/`problem_concepts`/`student_states.concept_id`가
  concept ID 체계이고, 현 코드는 `data/skills.json`의 skill_id를 쓴다.
- **개닝적 목록 API**: 웹 r26(숙련도 차트)·r30(오개념 TOP 5)·앱 r61/r62(오답 노트)는
  학생 단위 skill/misconception **목록 조회**가 필요하다. 현 API는 단일 분석 결과만 반환한다.
- **API prefix**: 명세는 `/api/v1/...`을 쓴다. attempt/students/ocr 라우트는 적용됨.
  `/analyze-solution`은 하위호환 때문에 prefix 없는 경로를 유지한다(둘 다 같은 파이프라인).
- **A6 P1 표현식 문제**: 구현됨(2026-11-13). `problem_type`(기본 `equation`) ·
  `target_form`(`integer`/`expanded`/`factored`) · 부등식은 `E_UNSUPPORTED_INEQUALITY`,
  함수 표기는 `E_UNSUPPORTED_FUNCTION`으로 fail-closed. 기획 확정(2026-11-16):
  B4 P1 제외, Q-A6-3 현행 `procedure` 유지. 계약·회귀 목록은
  `docs/AI1_표현식문제_계약초안_A6.md` §13~§15.

## 하지 않는 것

- 커밋/푸시: 사용자가 명시적으로 요청할 때만.
- `docs/STEPi_기능명세서_원본.md`는 파싱본 산출물이다. xlsx가 갱신되면 재생성한다.

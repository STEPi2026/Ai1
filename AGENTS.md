# AGENTS.md — AI① Learning Intelligence 개발 규칙

프로젝트: STEPi P5 적응형 학습 튜터의 **AI① Learning Intelligence** (풀이 과정 → 최초 오류 → 오개념 → Skill별 Knowledge State).
원본 명세: `docs/STEPi_기능명세서_원본.md` · AI① 대조표: `docs/AI1_명세서_대조표.md`

## 명령

```bash
.venv\Scripts\python -m pytest            # 전체 (현재 940 passed)
.venv\Scripts\pyflakes app tests          # lint (0건 유지)
.venv\Scripts\uvicorn app.main:app --reload
```

의존성을 새로 추가해야 할 때는 **먼저 물어본다.** `.venv`는 uv로 만들어졌고
`requirements.txt` 외에 설치된 것이 없다 (openpyxl조차 없음).

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

## 현재 구현 범위와 남은 기능

현재 text 기반 AI①은 풀이 검증·오류/오개념 분류·Knowledge State 갱신,
단계 정렬 및 다중 오류 분석을 제공한다. Attempt 분석·멱등성·이력,
학생 이력/기간 조회 및 오개념 TOP API도 구현되어 있다.

기능명세서 기준으로 아직 남은 항목:

- **이미지 입력/OCR**: 앱 r36~r44의 사진 업로드·품질 검사·OCR·진행 상태.
  OCR 서비스 경계와 공급자 결정이 필요하다.
- **영속화**: 저장소와 Knowledge State는 현재 in-memory이며 MySQL 미연동.
- **Skill ↔ Concept 매핑**: 방향은 별도 매핑 테이블로 결정했으나 공식 Concept seed가 없어 구현 보류.
- **초기 Knowledge State 시드**: 진단평가 결과에서 시작 숙련도를 만드는 흐름.
- **`weakness_score` 및 오개념 해결 상태**: 데이터 의미/정책 결정 필요.
- **인증·권한 및 멀티프로세스 멱등성**: 서비스 통합 전 별도 설계 필요.

## 하지 않는 것

- 커밋/푸시: 사용자가 명시적으로 요청할 때만.
- `docs/STEPi_기능명세서_원본.md`는 파싱본 산출물이다. xlsx가 갱신되면 재생성한다.

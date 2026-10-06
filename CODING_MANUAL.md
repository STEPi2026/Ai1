# AI① Learning Intelligence — 코딩 매뉴얼

> **이 문서 하나만 읽고 개발을 이어가도 되도록 작성한 작업 지침서입니다.**
> 대상 모델: DeepSeek v4.1 flash (이후 "어시스턴트"로 표기)
> 최종 갱신: PHASE 9-1 설계 보고 직후 · pytest 940 passed 기준

---

## 0. 30초 요약

| 항목 | 값 |
|---|---|
| 프로젝트 경로 | `C:\Users\choi\Desktop\learning-intelligence` |
| 정식 이름 | AI① Learning Intelligence (수학 풀이 분석 백엔드) |
| 언어/프레임워크 | Python 3.12.10 · FastAPI 0.141 · Pydantic 2.13 · SymPy 1.14 |
| 테스트 | **940 passed** (수집 940 = 실행 940) / 경고 1개(무해) / lint 경고 0 |
| 데이터 | `data/*.json` 3개 — **frozen, 수정 금지** |
| 문서 | `docs/AI1_명세서_대조표.md` 가 실행 중인 명세(§1~§12) |
| 미완 결�� | PHASE 9-x (OCR 경계) + PHASE 8-9 (Concept 매핑, 공식 자료 대기) |

```powershell
# 작업 디렉터리는 항상 프로젝트 루트
cd C:\Users\choi\Desktop\learning-intelligence
$py = "C:\Users\choi\Desktop\learning-intelligence\.venv\Scripts\python.exe"

& $py -m pytest -q                                              # 전체 (약 70초)
& $py -m pytest --collect-only -q                               # 수집 수 확인
& $py -m pyflakes app tests                                     # lint (반드시 0)
& $py -X utf8 -c "from app.taxonomy import validate_all; print(validate_all())"   # [] 여야 정상
```

> `-X utf8` 없으면 Windows 콘솔에서 한글 출력이 깨집니다. 디버그 출력에 항상 붙이세요.

---

## 1. 이 프로젝트가 무엇인가 (도메인)

고등학생(공통수학1) 수학 풀이를 받아 **오류 단계를 특정하고 → 오개념으로 분류하고 → 학생의 Knowledge State(BKT)를 갱신**하는 백엔드입니다.
기능명세서의 **AI①** 영역만 담당합니다. AI②(튜터)·앱(인증/과제/학급)은 담당하지 않습니다.

### 처리 파이프라인 (변경 금지)

```
problem_latex + solution_text
   → parser.py     줄 분리 + SymPy 파싱
   → verifier.py   단계별 동치 판정 (valid[] = True/False/None)
   → classifier.py 오류 유형 + 오개념 ID 매핑 (taxonomy 조회만)
   → knowledge.py  Rule 또는 BKT 엔진으로 상태 갱신
   → JSON 응답
```

### 두 개의 분석 진입점 (역할이 다름, 혼동 금지)

| 진입점 | 책임 | Knowledge State |
|---|---|---|
| `analyze()` (`pipeline/analyzer.py`) | 기존 단일 경로. `problem_latex` + `solution_text` 직접 입력 | **직접 갱신** |
| `analyze_attempt()` (`pipeline/session.py`) | Phase 6 신규 학습 세션 경로. Problem/CorrectSolution/StudentAttempt 객체 | **직접 갱신** |
| `analyze-solution` 엔드포인트 | `analyze()` 래퍼 | 갱신 |
| `POST /api/v1/attempts/{id}/analyze` | `analyze_attempt()` 래퍼 + 저장소 | 갱신 |
| **모든 GET 엔드포인트** | **조회 전용** | **갱신 금지 (절대)** |

---

## 2. 절대 바꾸지 말 것 (Frozen Invariants)

### 2.1 taxonomy 데이터 — 수정 금지

| 파일 | 내용 |
|---|---|
| `data/skills.json` | 26 base + 5 expansion = **31 skill**, 그중 BKT 추적 대상 **24개** |
| `data/error_types.json` | error_type **4개**(`calculation`/`comprehension`/`concept_error`/`procedure`), subtype **17개** |
| `data/misconceptions.json` | misconception **19개** |

- `skills.json` 의 `meta.description`이 명시: **"skill_id는 BKT key이므로 확정 후 변경 금지"**
- 이름·순서·`unit` 값으로도 `concept_id` 를 **추측하지 마세요**(§6 미결정 참조)
- taxonomy 은 **frozen**입니다. 오개념 ID 재번호(2.1 → M001) 금지.

### 2.2 API 응답 계약 — 필드 삭제/개명 금지

| 엔드포인트 | 필드 수 | 비고 |
|---|---|---|
| `POST /analyze-solution` | **16** | 기존 11 + additive 5. `analysis_status` 는 null 허용 |
| `POST /api/v1/attempts/{id}/analyze` | **26** | Phase 8-7 `attempted_at` 추가 |
| `GET /api/v1/attempts/{id}/analyses` | 목록 + `AnalysisSummaryItem` **16** | `attempted_at` **없음** (분석 스냅샷 ≠ attempt 행) |
| `GET /api/v1/attempts/{id}/analyses/{aid}` | **27** | = 26 + `raw_result` |
| `GET /api/v1/students/{id}/history` | `StudentHistoryItem` **23** | |
| `GET /api/v1/students/{id}/misconceptions/top` | 항목 **6** / 응답 **5** 키 | |

- **additive(필드 추가)는 허용, 삭��·개명·의미 변경은 금지**입니다.
- 필드 집합을 고정하는 테스트가 존재합니다: `tests/test_attempt_api.py::test_response_field_set_is_stable` 등.
- 새 필드를 추가하면 **해당 고정 테스트의 집합에도 추가**해야 합니다(삭제가 아니라 추가).

### 2.3 의미론적으로 동결된 결정 (과거 Phase 에서 확정)

| 결정 | 내용 |
|---|---|
| `is_correct` | `True→correct`, `False→error`, `None→unreviewable` (ADR-03, 보류 의미) |
| `first_error_step_index` | `error_step` 과 동일, **1-based** |
| `valid` | `None` = 판정 보류. "식 단독 줄"이나 "인식 실패 줄" |
| `analysis_status` | `ANALYZED` / `REVIEW_REQUIRED` / `UNKNOWN` **3개만** |
| `UNKNOWN` | `E_UNRECOGNIZED`·`E_UNVERIFIABLE` → **422 + `detail.analysis_status`** |
| 요청/입력 오류 | 413/415/422/503/504 등에는 `analysis_status` 를 **붙이지 않음** |
| `misconception_tag` | **frozen taxonomy 에 있는 ID만** |
| misclassification 금지 | 계산 오류는 `misconception_id = null` |
| Knowledge State 키 | **항상 `skill_id`** (concept_id 아님) |
| 정답만 제출 | `correct=true` 일 수 있음 (완전성 증명이 아님) |
| 인식 실패 | **오답도 오개념도 아님** |

---

## 3. 아키텍처 지도

```
app/
  main.py            FastAPI 엔드포인트 7개 + 헬퍼. 오류→HTTP 매핑의 유일한 장소
  schemas.py         Pydantic 요청/응답 모델 (전부 extra="forbid")
  taxonomy.py        frozen JSON 로더 + 검증 헬퍼. 파일을 직접 수정하지 않음
  pipeline/
    parser.py        split_steps() / parse_line() / normalize()
    verifier.py      equivalent() / verify() / _SolveBudget
    classifier.py    오류 유형·오개념 매핑
    knowledge.py     RuleStateEngine / BKTEngine (학생별 LRU + RLock)
    session.py       Problem/CorrectSolution/StudentAttempt/ErrorRecord/
                     KnowledgeObservation/AttemptAnalysis + analyze_attempt()
    analyzer.py      analyze() + AnalysisError + _ERROR_STATUS 관련 상수
    repository.py    LearningRepository ABC + InMemoryLearningRepository
tests/               34개 파일, 940건
docs/
  STEPi_기능명세서_원본.md   원본 명세 (읽기 전용)
  AI1_명세서_대조표.md        실행 중 대조표 §1~§12 ← 반드시 함께 갱신
data/                frozen taxonomy 3종
```

### 저장 계층 핵심 규칙

- `LearningRepository` 는 **추상 계약**, `InMemoryLearningRepository` 는 구현. MySQL 단계에서 후자를 교체.
- 저장 계층은 **Knowledge State 를 직접 다루지 않는다** (분석 로직과 분리, 테스트로 고정).
- `claim_analysis(attempt_id)` / `release_analysis(attempt_id)` — attempt 단위 블로킹 락. `threading.Lock` 이므로 **같은 스레드 재진입 불가**.
- `has_analysis()` 은 게터가 아니라 **존재 질의** — 미등록 attempt 에도 `False` (게터는 `E_REFERENCE_INTEGRITY`).
- `list_attempts(student_id=)` / `list_analyses(attempt_id)` 는 **삽입 순서 유지**.

---

## 4. 에러 코드 ↔ HTTP 매핑 (코드 기준: `app/main.py`)

| HTTP | detail.code | 조건 | `analysis_status` |
|---|---|---|---|
| 200 | — | 정상 | `ANALYZED` / `REVIEW_REQUIRED` |
| 200 | — | 멱등 재요청 (기존 분석 반환) | 기존 값 |
| **409** | `E_ATTEMPT_CONFLICT` | 같은 attempt_id + 다른 본문 | 없음 |
| 409 | `E_DUPLICATE_ID` | 저장소 중복 ID | 없음 |
| **413** | `E_INPUT_TOO_LARGE` | 2000자/20000자/30단계 초과 | 없음 |
| 422 | `E_EMPTY_SOLUTION` | 풀이 단계 없음 | 없음 |
| 422 | `E_BAD_PROBLEM` | 문제식 파싱 실패 | 없음 |
| 422 | `E_UNKNOWN_SKILL` / `E_SKILL_NOT_TRACKABLE` | taxonomy 위반 | 없음 |
| 422 | `E_INVALID_STUDENT_ID` | student_id 형식 | 없음 |
| 422 | `E_REFERENCE_INTEGRITY` | 미등록 problem 등 | 없음 |
| 422 | `E_INVALID_INSTANT` | from/to 형식·offset 누락 (9-11) | 없음 |
| 422 | `E_INVALID_TIME_RANGE` | from ≥ to (9-11) | 없음 |
| 422 | `E_ATTEMPT_NOT_FOUND` / `E_ANALYSIS_NOT_FOUND` | GET 경로 404 | 없음 |
| **422** | `E_UNRECOGNIZED` | 전량 인식 실패 | **UNKNOWN** |
| **422** | `E_UNVERIFIABLE` | 판정 불가 | **UNKNOWN** |

새 코드를 추가할 때: `_ERROR_STATUS`(analyzer) 또는 `_REPOSITORY_ERROR_STATUS`(main) 에 등록하고, **기본값 422**. `_ANALYSIS_STATUS_BY_ERROR` 는 "분석을 수행했으나 신뢰 가능한 판정이 없을 때" 만 포함시킵니다.

---

## 5. 이 프로젝트에서 이미 확정한 API 동작 규칙

### 5.1 Attempt 분석 멱등성 (Phase 8-4)
- 같은 `attempt_id` + 같은 본문 → **기존 최신 `AnalysisRecord` 반환**. 새 `analysis_id` 없음, `analyze_attempt()` 미호출, Knowledge Observation 미반영.
- 같은 `attempt_id` + 다른 본문 → 409, 저장본 무변경.
- **분석 실패(422) 제출은 attempt 로 저장되지 않음** → 재시도가 409 에 막히지 않는다.
- `check → analyze → save` 구간은 `claim_analysis()` 락으로 직렬화. 동시 중복 16건 → 분석 1회.

### 5.2 attempted_at (Phase 8-7)
- **서버가 첫 분석 요청을 받은 시각**(UTC, timezone-aware). 클라이언트 풀이 시각 아님.
- 엔드포인트 **최상단**(게이트 대기 이전)에 1회 캡처.
- 멱등 재요청·충돌 거절로 변하지 않음.

### 5.3 문제 표시 메타데이터 (Phase 8-8)
- `Problem.question_text` / `concept_name` / `unit_name` — **최초 등록 시에만** 저장. 저장본이 source of truth.
- `skills.json` 의 `name`·`unit` 으로 **파생하지 않음**. 없으면 `null`.

### 5.4 오개념 TOP (Phase 8-10)
- 원본은 `ErrorRecord.misconception_ids` (frozen ID). 이름·설명은 `misconception_by_id()` 조회.
- Attempt 안 중복 1회, Attempt 간 반복 횟수, **최신 AnalysisRecord 1건만**.
- 정렬 `attempt_count` 내림차순 → 동률 `misconception_id` 오름차순.

### 5.5 기간 필터 (Phase 8-11)
- `from`/`to` = offset 포함 ISO 8601. **구간 `[from, to)`** — from 포함, to 제외.
- UTC 정규화 후 비교. offset 없으면 422. `from >= to` 422.
- **두 엔드포인트가 공통 헬퍼**(`_resolve_time_range`/`_student_attempts`)를 사용 — 경계를 한 곳에 두세요.

---

## 6. 미해결 — 여기서 추측하지 마세요

| # | 쟁점 | 상태 |
|---|---|---|
| 1 | **Skill ↔ Concept ID 매핑** | 방향만 결정(별도 `skill_concept_map` 테이블), **공식 `concepts` 시드 미수신 → row 0개, 구현 금지** |
| 2 | `concepts.name` vs `skills.json.name` | 의미 다름. fallback 금지 |
| 3 | `unit_name` ↔ `skills.json.unit` | 미정의 |
| 4 | OCR 업체/모델·API 키·평가셋 | 미수신. **임의 선택 금지** |
| 5 | OCR 동기/비동기, 이미지 저장 | 미결정 |
| 6 | `python-multipart` 의존성 | 미설치·미승인 |
| 7 | 인증/권한, MySQL, 저장소 상한/TTL | 범위 외 |

공식 자료가 오면 **자료에 근거해서만** 구현하고, 각 행에 출처를 기록하세요.

---

## 7. 작업 방식 (이 프로젝트가 요구하는 규약)

### 7.1 Phase 진행 절차 — 이 순서를 지키세요

```
① 착수 전 사실 확인 (코드/문서/데이터를 실제로 열어 본다)
② 짧은 설계/매핑표 보고  ← 구현 전에 사용자에게 제시
③ 구현
④ 전체 검증 6종 실행
⑤ 완료 보고 (변경 파일·실제 수치·판단·한계·다음 후보)
```

**사용자가 ① 또는 ②를 요청하면 반드시 그 전에 멈추고 조사 결과를 표로 보고**하세요. 추측으로 구현하지 마세요.

### 7.2 검증은 항상 6종

```powershell
& $py -m pytest -q                                                                  # 전체
& $py -m pytest --collect-only -q                                                   # 수집 수
& $py -m pytest tests/test_api.py tests/test_attempt_api.py tests/test_attempt_idempotency.py `
    tests/test_attempt_history_api.py tests/test_student_history_api.py tests/test_attempted_at.py `
    tests/test_problem_metadata_api.py tests/test_misconception_top_api.py `
    tests/test_time_range_filter.py tests/test_repository.py tests/test_analysis_status.py -q   # API/repository
& $py -m pytest tests/test_multi_error.py tests/test_context_multi_error.py `
    tests/test_step_alignment.py tests/test_alignment_confidence.py tests/test_swap_normalization.py -q  # multi-error/alignment
& $py -m pytest tests/test_golden_regression.py -q                                   # golden
& $py -X utf8 -c "from app.taxonomy import validate_all; print(validate_all())"    # taxonomy
& $py -m pyflakes app tests                                                         # lint = 0
```

**보고에는 수집 수와 실행 수를 함께** 적으세요. 이 프로젝트에서 집계 실수가 실제로 2건 있었습니다.

### 7.3 테스트 원칙 (엄격)

- **삭제 금지, 단언 완화 금지.** 실패하면 원인 분석 → 정정.
- 테스트 수가 줄었다면 **왜 줄었는지 먼저 보고**하세요.
- 필드 집합을 고정하는 테스트에 필드를 **추가**하는 것은 정상(additive 변경).
- 이 프로젝트의 테스트 실패는 대부분 **테스트 기대값/픽스처 오류**였습니다. 실패 즉시 제품 코드 의심이 아니라 기대값을 검증하세요.

### 7.4 문서 갱신 규칙

`docs/AI1_명세서_대조표.md` 가 실행 중인 명세입니다. 구현 Phase마다:
- §3 API 경로 표에 경로 추가/상태 변경
- 새 정책이 있으면 **새 절 추가** (번호는 다음 번호)
- 헤더의 `pytest NNN passed` 갱신
- §4 미해결 목록 상태 갱신
- 절 번호를 다른 절에서 **참조**하세요(예: "§10-7")

**설계만 하는 Phase**(코드 미변경)라도 문서 갱신은 수행합니다.

### 7.5 금지

- taxonomy/데이터 JSON 수정
- golden expected 값 수정 (회귀가 깨질 때 **golden 이 틀렸는지 먼저 조사**)
- 기존 API 필드 삭제·개명
- 저장소 구조 변경(MySQL 단계 전)
- `app/schemas.py` → `app.pipeline` **임포트** (순환 참조). 중첩 타입이 필요하면 `list[dict]` + 키 집합 테스트
- 임의의 OCR/외부 서비스 의존성 추가
- 읽기 전용(GET) 엔드포인트에서 `analyze_attempt()` 호출 또는 Knowledge State 변경

---

## 8. 이 프로젝트에서 실제로 겪은 함정 (반드시 피하세요)

아래는 **실제로 발생해 수정한 사고**입니다. 반복하지 마세요.

| # | 사고 | 규칙 |
|---|---|---|
| 1 | **edit 앵커가 잘못된 클래스에 적용** — `AnalysisSummaryItem` 과 `AnalyzeAttemptResponse` 의 헤더 5줄이 동일해서 필드가 엉뚱한 클래스에 들어감 | edit 전 대상 파일을 **Read 로 확인**하고, 유일한 문자열(`analysis_count` 등)을 앵커에 포함 |
| 2 | **모듈 상수 이름 충돌** — `_HISTORY_ORDER` 를 attempt 목록과 학생 이력이 공유해 한쪽 값이 다른 쪽을 덮어씀 | 상수 이름을用途별로 분리하고, grep 으로 사용처 확인 |
| 3 | **테스트 삭제 사고** — 앵커 줄을 되돌려 쓰지 않아 `def test_...` 줄이 사라지고 본문만 고아 코드로 남음 | 새 블록을 삽입할 때 **앵커 줄을 newString 끝에 반드시 재포함** |
| 4 | Pydantic 모델에 **위치 인자** 전달 | `Model(field=...)` 키워드만 사용 |
| 5 | `Problem("P1","2x=6",[...])` → `TypeError` | 위와 동일 |
| 6 | `SKILL_CATALOG["x"]` — `SkillCatalog` 는 dict 아님 | `skill_by_id("x")` 헬퍼 사용 |
| 7 | **소문자 부분 문자열 오판** — `"concept" not in body` 가 `"misconception"` 때문에 실패 | 키/필드명으로 검사 |
| 8 | 같은 `solution_id` 를 서로 다른 problem 에 등록 | `solution_id` 는 **하나의 problem 에 귀속**. 문제마다 다른 ID 사용 |
| 9 | attempt 본문 비교에 메타데이터를 넣어 버그 | 409 판정은 `problem_id`·`student_id`·`solution_text` **3개만** |
| 10 | `datetime.fromisoformat` 는 Python 3.11+ 에서만 `Z` 를 수용 | 3.12 환경임을 기억 |
| 11 | 해결되지 않은 오염: `app/schemas.py` 에 `首次`(중국어) 잔존 | 비ASCII 문자를 넣을 때 주의 |

**디버그 프로브 스크립트 주의**: 이 프로젝트에서 "실패" 다수가 프로브 스크립트 자체의 오타/기대값 오류였습니다(집합 비교 `len(x)=={1}`, `solution_id` 불일치, `analysis_id` 하드코딩 등). 프로브 실패 시 **먼저 프로브를 의심**하세요. `analysis_id` 는 `{attempt_id}#{저장소 전역 seq}` 이고 **attempt별 시퀀스가 아닙니다.**

---

## 9. 코드 스타일 관례

- 한국어 docstring/메시지, 한국어 코드 주석. 오류 메시지는 사람이 읽는 문장.
- f-string·`raise ... from None` 으로 내부 예외 노출을 막기 (외부 API 응답을 깔끔하게).
- 테스트는 `pytest` fixture(`autouse`) 로 `reset_knowledge()` + `reset_repository()` 격리.
- 저장소 private(`_attempts`)에 직접 쓰는 것은 **테스트 픽스처에서만** 허용.
- Pydantic 모델은 전부 `model_config = ConfigDict(extra="forbid")`.
- 상수는 모듈 상수로 하드코딩 하되, 계약에 포함되면 스키마 `description` 에 명시.

---

## 10. 다음 Phase 프롬프트 템플릿

이 템플릿에 Phase 내용을 채워 그대로 사용하세요.

```
PHASE X-Y를 진행해줘: <목표 한 줄>

먼저 다음을 확인하고 <매핑표/계획>을 간단히 보고한 뒤 구현한다.
- <열람 대상 파일/문서>

구현 원칙:
1. ...
2. ...

필수 테스트:
- ...
- 기존 <회귀 대상> 회귀

<금지 사항>

전체 pytest, API/repository, multi-error/alignment, golden regression,
taxonomy validation, pyflakes를 실행하고 실제 수치를 포함해 보고해줘.
```

**프롬프트에 반드시 포함할 것**
- 기준선 테스트 수 (비교 가능하게)
- "보고 수와 실제 수가 다르면 먼저 원인 보고" 지시 (집계 사고 방지)
- "테스트 삭제/단언 완화 금지" 지시
- 금지 범위 (taxonomy·MySQL·저장소 구조 등)

---

## 11. 현재 백로그 (참고)

| 우선 | Phase | 내용 | 선행 조건 |
|---|---|---|---|
| 1 | 8-9 후속 | Skill↔Concept 매핑 데이터·검증·영속화 경계 | **공식 `concepts` 시드** |
| 2 | 9-1 단계 1 | `ocr_result_id` 선택 필드 + 최소 1개 필수 검증 (OCR 없이) | OCR 경계 결정(§6-4,5) |
| 3 | 9-1 단계 2~3 | `OcrResult` 스키마 + 검증기 + 가짜 구현 | `python-multipart` 승인 |
| 4 | 9-1 단계 4~5 | `POST .../ocr` + 실제 OCR 연동 + 평가셋 | OCR 벤더/키/평가셋 |
| 5 | 후순위 | 오개념 해결 상태(`resolved_at`), 커서 페이징, 학생 검색, `analysis_id` URL-safe, 저장소 상한·TTL, MySQL, 인증·권한 | 각각 별도 결정 |

---

## 12. 빠른 참조 카드

```
엔드포인트 7개
  GET  /health
  POST /analyze-solution                              (16 필드)
  POST /api/v1/attempts/{attempt_id}/analyze          (26 필드, 멱등)
  GET  /api/v1/attempts/{attempt_id}/analyses
  GET  /api/v1/attempts/{attempt_id}/analyses/{analysis_id}   (# → %23 인코딩)
  GET  /api/v1/students/{student_id}/history?page&page_size&from&to
  GET  /api/v1/students/{student_id}/misconceptions/top?limit&from&to

하드 한도
  문제식 2000자 / 풀이 20000자 / 30단계  → E_INPUT_TOO_LARGE 413
  page ≥ 1, page_size 1~100, limit 1~20   → 422

검증 6종
  pytest -q · --collect-only -q · pyflakes(app tests) · validate_all() → []
  golden regression · multi-error/alignment

금지
  taxonomy 수정 · golden expected 수정 · API 필드 삭제/개명
  GET 에서 분석/상태 갱신 · 추측으로 concept_id 생성 · 임의 외부 의존성
```

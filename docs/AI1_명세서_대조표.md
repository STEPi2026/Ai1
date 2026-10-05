# AI① ↔ STEPi 기능명세서 대조표

원본: `docs/STEPi_기능명세서_원본.md` (xlsx 파싱본)
코드 기준: `app/`, `data/` (2026-09-29 시점, pytest 940 passed)

상태: `O` 구현됨 · `P` 부분 · `-` 미구현(필요) · `X` AI① 책임 아님

## 1. 앱 기능 흐름상 AI① 책임 (원본 행 기준)

| 원본 | 기능 | AI①가 제공해야 하는 것 | 상태 | 현재 코드 |
|---|---|---|---|---|
| 앱 r33 | 선수 개념 → 핵심 개념 학습 카드 | 선수개념 매핑 + 개념 콘텐츠 | P | `data/skills.json` prerequisite 관계만 존재, 콘텐츠 조회 없음 |
| 앱 r34 | LaTeX 수식 렌더링 | LaTeX 문자열 공급 | O | `problem_latex` / `steps_latex` |
| 앱 r35 | 체류시간·힌트 횟수 로그 | AI① 무관(attempt 기록) | X | — |
| 앱 r36/r38/r40 | 공책 풀이 촬영 → 크롭 | **이미지 입력 경로** | - | 없음 (text only) |
| 앱 r41 | 크롭/회전 보정 | 이미지 전처리 | - | 없음 |
| 앱 r42 | 흐림·어두움·빈 이미지 | **인식 불가 코드** | - | 없음 |
| 앱 r43 | Multipart 업로드 + "수식 인식 중 → 오류 찾는 중" | **multipart/image 엔드포인트**, 단계 상태 스트림 | - | 없음 |
| 앱 r44 | 분석 실패/타임아웃 | 실패 상태 코드 + 타임아웃 기준 | P | `AnalysisError.code`(422/413)만 존재, 타임아웃 없음 |
| 앱 r45 | `step_list` 렌더링 | 단계 목록 | O | attempt 분석 응답 `step_list`(단계+판정+정렬) / 기존 `steps_latex` |
| 앱 r46 | step별 `is_correct` / `status` | 단계 판정 | O | `step_list[].is_correct`·`.status`(correct/error/unreviewable), 기존 `valid[]` 유지 |
| 앱 r47 | `first_error_step_index` 하이라이트 | 최초 오류 단계 | O | `first_error_step_index` = `error_step` (**1-based**) |
| 앱 r48 | `misconception_tag` Chip | 오개념 태그 | O | `misconception_tags` (frozen taxonomy 조회만) / 기존 `misconception_ids` |
| 앱 r49 | `analysis_status=REVIEW_REQUIRED / UNKNOWN` | **판정 불가 상태** | O | `analysis_status` (ANALYZED/REVIEW_REQUIRED/UNKNOWN) + `unrecognized` 배열 |
| 앱 r50 | 재풀이/힌트/개념 복습 CTA | 후속 학습 추천 | X | AI② |
| 앱 r56 | 재풀이 최종 답안 검증 | 정답 검증 재실행 | O | 동일 엔드포인트 재호출 |
| 앱 r57 | 재촬영 제출 시 회차 연결 | **attempt_id / 분석 이력 ID** | O | `POST /api/v1/attempts/{attempt_id}/analyze` + `analysis_id`. **멱등(PHASE 8-4): 동일 제출 재전송은 기존 `analysis_id` 를 그대로 반환** |
| 앱 r61/r62 | 오답 노트, 오답 상세 | 오답 목록 + 원본 분석 조회 | O | `LearningRepository`(in-memory) + `errors[]`·`step_list` 응답 + **PHASE 8-5 `GET /api/v1/attempts/{id}/analyses[/{analysis_id}]`** (저장 시점 스냅샷, 0건/미존재 구분). 학생 단위 통합 이력은 미구현 |
| 앱 r24~r27 | 진단평가 → 최초 Knowledge State | **초기 상태 시드** | - | `rule`: 기본 `learning`, `bkt`: `p_init=0.3` 암묵값만 |
| 웹 r26/r27 | 개념 영역별 숙련도 차트 | skill별 mastery 조회 | P | 단일 skill만 반환, 목록 API 없음 |
| 웹 r30 | 반복 오개념 TOP 5 | 오개념 누적 집계 | P | 학생 단위 state만, 오개념 이력 미보존 |
| 웹 r32 | AI① 학습 분석 카드 | 응답 JSON | O | `POST /analyze-solution` |
| 웹 r37/r38 | 손글씨 풀이 이미지 + 오류 step 하이라이트 | 최초 오류 step | P | `error_step` (이미지 자체는 attempt) |

## 2. 백엔드 DB 계약과의 매핑

| DB 컬럼 | 타입 | AI① 대응 | 상태 | 비고 |
|---|---|---|---|---|
| `attempts.is_correct` | TINYINT(1) | `correct` | O | S7 정책(부분 검증) 문서화됨 |
| `attempts.solution_image_url` | VARCHAR(500) | AI① 입력 | - | 이미지 입력 경로 필요 |
| `attempts.hint_count` / `solve_time_seconds` | INT | AI① 무관 | X | BKT 관측이 아님(주의) |
| `ai_analysis_results.first_error_step` | INT NULL | `error_step` | O | 1-based 일치 |
| `ai_analysis_results.mastery_score` | DECIMAL(5,4) | `mastery` | O | 0~1 정규화 확인 필요 |
| `ai_analysis_results.confidence` | DECIMAL(5,4) | `confidence` | O | DECIMAL(5,4) → 최대 9.9999, 값 범위 0~1 |
| `ai_analysis_results.raw_result` | JSON | **응답 원본 전체** | - | `AnalyzeSolutionResponse` 직렬화 결과 저장 |
| `ai_analysis_results.attempt_id` | INT FK | **분석 식별자** | - | 재시도 회차 연결에 필수 |
| `ai_analysis_results.misconception_id` | INT FK NULL | `misconception_id` | P | 문자열 코드 → INT FK 매핑 계층 필요 |
| `misconceptions.code` | VARCHAR(50) `M001` | taxonomy node_code `2.1`/`3.1` | P | **코드 체계 불일치**, 매핑 테이블 필요 |
| `misconceptions.concept_id` | INT FK | `data/skills.json` skill_id | P | skill ↔ concept 매핑 미정 |
| `student_states.mastery_score` | DECIMAL(5,4) | `mastery` | O | — |
| `student_states.knowledge_state` | VARCHAR(30) `LEARNING` | `mastered/learning/needs_practice` | P | **대소문자 표기 불일치** |
| `student_states.weakness_score` | DECIMAL(5,4) `0.28` | 없음 | - | `1 - mastery` 파생 필요 |
| `student_states.concept_id` | INT FK | tracking skill | P | 개념 ID 체계 연결 필요 |
| `student_misconceptions.confidence` | DECIMAL(5,4) | `confidence` | O | — |
| `student_misconceptions.detected_at` / `resolved_at` | DATETIME | 오개념 발생/해결 이력 | - | in-memory라 이력 없음 |
| `problems.problem_type` | VARCHAR(20) `SHORT_ANSWER` | 등식/단답 파싱 | P | 비등식·객관식 미지원 |
| `problems.answer` | TEXT | 정답 대조 | - | 현재는 문제식↔풀이 동치만 검증 |
| `problems.question` | TEXT "학생에게 제시되는 문제 내용" | `Problem.question_text` | O | PHASE 8-7 이전에는 `problem_latex` 로 겸용. 표시 텍스트는 별도 보관 |
| `concepts.name` | VARCHAR(100) "학습 개념 이름" | `Problem.concept_name` | O(P) | PHASE 8-8. 명시 입력만 저장, skill_id 파생 금지 |
| `concepts.parent_concept_id` | INT FK NULL | `prerequisite` 목록 | P | 트리 대신 평탄 목록 |
| `problem_concepts` | (PK,FK) | `problem_skill` 1개 | P | 1:N 미지원 |
| `learning_sessions` / `learning_logs` | — | AI① 무관(기록 계층) | X | — |
| `tutor_actions` | — | AI② | X | `ai_analysis_id` FK로 AI① 결과 참조 |

## 3. API 경로

| 원본 | 현재 | 상태 |
|---|---|---|
| `/api/v1/...`前缀 계열 (`/api/v1/students`, `/api/v1/reports/pdf`) | `/analyze-solution`, `/health`, `POST /api/v1/attempts/{attempt_id}/analyze`, `GET /api/v1/attempts/{attempt_id}/analyses`, `GET /api/v1/attempts/{attempt_id}/analyses/{analysis_id}`, `GET /api/v1/students/{student_id}/history`, `GET /api/v1/students/{student_id}/misconceptions/top` (일부만 prefix 적용) | P |
| 앱 로그인 `POST /api/auth/login` | AI① 무관 | X |
| 이미지 업로드 (Multipart) | 없음 | - |
| `/api/v1/students/{id}/history` 조회 (학습기록·오답 상세) | `GET /api/v1/students/{student_id}/history?page&page_size` (PHASE 8-6) + attempt 상세 GET | O |
| **오개념 TOP 5** (웹 r30 / 앱 r64) | `GET /api/v1/students/{student_id}/misconceptions/top?limit=5` (PHASE 8-10) | O |
## 4. 미해결 결정 필요 사항

1. **이미지→LaTeX 경로**: AI①이 OCR을 직접 소유하는지, 별도 모듈이 LaTeX를 넘겨주는지
2. **skill ↔ concept ID 체계**: ~~매핑 테이블을 두는지~~ → **방향 결정됨(구현 보류)**. 별도 명시적 매핑 테이블 채택, §10 참조. 차단 요인: 공식 `concepts` 시드 미제공
3. **오개념 코드**: taxonomy `2.1` 체계를 `M001`로 재번호할지(주의: taxonomy은 frozen), 별도 코드 컬럼을 둘지
4. **응답 필드 네이밍**: 앱이 기대하는 `step_list` / `is_correct` / `first_error_step_index` /
   `misconception_tag` / `analysis_status` 를 alias로 노출할지, 앱을 현 계약에 맞출지
5. **`analysis_status` enum 정의**: `SUCCESS` / `REVIEW_REQUIRED` / `UNRECOGNIZED` / `FAILED` / `TIMEOUT`
6. **분석 이력 보존**: `ai_analysis_results.attempt_id` 연결을 위해 in-memory를 언제 DB로 옮길지
7. **weakness_score 정의**: `1 - mastery` 로 고정할지
8. **초기 Knowledge State 시드 규칙**: 진단평가 결과 → per-skill p_init 매핑

## 5. 중복 처리(멱등) 정책 — PHASE 8-4

`POST /api/v1/attempts/{attempt_id}/analyze` 만 적용된다.
기존 `/analyze-solution` 은 저장소를 쓰지 않아 영향이 없다.

| 상황 | 동작 |
|---|---|
| 동일 `attempt_id` + 동일 본문, 이미 성공 분석 | 기존 최신 `AnalysisRecord` 반환. 새 `analysis_id` 없음, `analyze_attempt()` 미호출, Knowledge Observation 미반영 |
| 동일 `attempt_id` + 다른 본문 | 409 `E_ATTEMPT_CONFLICT`. 저장본·이력 불변 |
| 새 `attempt_id` | 별도 제출. 관측 1회 반영 |
| 분석 실패 (422 등) | attempt·이력 모두 남기지 않음 → 재시도 가능 (같은 id 로 다른 본문도 허용) |
| 동시 중복 요청 | 저장소 `claim_analysis()` attempt 단위 락으로 `이력 확인 → 분석 → 저장` 직렬화. 분석·상태 갱신 1회 |
| 의도적 재분석 / 모델 버전 변경 | **미구현** (별도 API 없음). 저장소 `save_analysis()` 은 이력 추가를 허용 |

- `has_analysis(attempt_id)` : 분석 이력 유무만 묻는 쿼리(게터와 달리 미등록 attempt 에도 `False`).
  `get_latest_analysis()` 는 저장된 attempt 에 대해서만 호출한다.
- `claim_analysis()` / `release_analysis()` : `threading.Lock` 기반 attempt 단위 게이트.
  프로세스 내 직렬화만 보장하므로 멀티 워커 배포 시에는 DB 유니크 제약을 함께 사용해야 한다.

## 6. 분석 이력 조회 API — PHASE 8-5

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/v1/attempts/{attempt_id}/analyses` | 해당 attempt 의 분석 이력 목록 |
| GET | `/api/v1/attempts/{attempt_id}/analyses/{analysis_id}` | 저장된 분석 1건 상세 (저장 시점 스냅샷) |

| 상황 | HTTP | detail.code |
|---|---|---|
| attempt 존재 + 이력 0건 | **200** | (빈 목록, `analysis_count=0`, `latest_analysis_id=null`) |
| attempt 미존재 | 404 | `E_ATTEMPT_NOT_FOUND` |
| analysis 미존재 | 404 | `E_ANALYSIS_NOT_FOUND` |
| analysis 가 다른 attempt 소속 | 404 | `E_ANALYSIS_NOT_FOUND` (타 attempt 존재 여부 비노출) |

- **정렬**: `order="created_asc"` = 저장소 생성 순서(오래된→최신) 오름차순. `analyses[-1]` 이 최신.
  `?reverse=true` 로 최신순 반전 가능하며, `latest_analysis_id` 는 두 경우 모두 최신 ID.
- **상세 응답**: POST 응답과 동일한 25개 필드 + `raw_result`(저장 시점 응답 원본, `ai_analysis_results.raw_result` 대응) = 26개.
  저장 이후 다른 제출으로 상태가 변해도 이 값은 바뀌지 않는다(스냅샷). 단 `analysis_count` 만 attempt 의 현재 이력 수를 반환한다.
- **읽기 전용**: `analyze_attempt()` 를 호출하지 않고 Knowledge State 를 변경하지 않으며, POST 의 분석 게이트(`claim_analysis`)도 잡지 않는다.
- **`analysis_id` 인코딩 주의**: 저장소 ID 형식이 `{attempt_id}#{전역 seq}` 이므로 `#` 을 반드시 퍼센트 인코딩(`%23`)해야 한다.
  인코딩하지 않으면 URL fragment 로 잘려 404 가 난다. URL-safe ID 로 바꿀지는 미해결 항목.
- 저장소에는 이력 조회용 메서드(`get_attempt`·`list_analyses`·`get_analysis`·`get_latest_analysis`)가 이미 있어
  이번 단계에서 저장소 계약은 추가하지 않았다.

## 7. 학생 학습 이력 조회 API — PHASE 8-6 (앱 r34)

`GET /api/v1/students/{student_id}/history?page=1&page_size=20&from=&to=`
"최근 학습 기록 테이블 — 최근 풀이한 문제와 정오답 결과" 요구사항에 대응한다.

| 쿼리 | 기본 | 범위 | 위반 시 |
|---|---|---|---|
| `page` | 1 | ≥ 1 | **422** |
| `page_size` | 20 | 1 ~ 100 | **422** |
| `from` / `to` | 없음 (전체 기간) | offset 포함 ISO 8601 datetime | **422** (PHASE 8-11) |

응답: `student_id`, `items[]`, `page`, `page_size`, `total`, `total_pages`, `order`.

| 상황 | HTTP |
|---|---|
| 기록 있음 | 200 |
| 기록 없는 학생 (미존재 학생 포함) | 200 + `items=[]`, `total=0`, `total_pages=0` |
| `student_id` 형식 오류 (빈 값·제어문자·64자 초과) | 422 `E_INVALID_STUDENT_ID` |

### 명세 필드 매핑

| 명세 | 현재 | 비고 |
|---|---|---|
| r34 `GET /api/v1/students/{id}/history / Paging` | **구현** | `items`/`page`/`page_size`/`total` |
| attempts.id (r44 전), student_id (r50) | 구현 | `attempt_id`, 필터 기준 |
| problems 연결 (r45 전) | 구현 | `problem_id` |
| attempts.is_correct (r45) | 구현 | 최신 분석 스냅샷 `correct` |
| problems.question (r25) / concepts.name (r33) / 단원명 | **구현 (PHASE 8-8)** | `problem_text` / `concept_name` / `unit_name` (nullable, 미등록 시 null) |
| ai_analysis_results (r61) | 연결 | `latest_analysis_id` + `detail_path` (원본 미노출) |
| student_states mastery/state (r55/r56) | 구현 | 스냅샷 `mastery`/`state` |
| student_misconceptions (r68) | 구현 | `misconception_tags` (taxonomy ID) |
| **attempts.attempted_at (r49)** | **구현 (PHASE 8-7)** | `StudentAttempt.attempted_at`, timezone-aware UTC, ISO 8601. 이력 정렬 기준 |
| attempts.solve_time_seconds (r47) | 미구현 | - |
| attempts.solution_image_url (r46) | 미구현 | OCR 범위 외 |
| attempts.student_answer (r44) | 미노출 | 원본 중복 노출 회피 (상세 경로로 연결) |

### 정책

- **학생 격리**: 저장소 `list_attempts(student_id=...)` 필터만 사용. 다른 학생 기록은 어떤 경우에도 포함되지 않는다.
- **최신순**: `order="attempted_at_desc"` — `attempted_at` 내림차순(PHASE 8-7).
  시각이 같은 항목의 보조 기준은 **나중에 저장된 것 먼저**(저장 인덱스 내림차순)이며,
  정렬 키를 `(attempted_at, 저장 인덱스)`로 명시해 Python `reverse` 안정성에 의존하지 않는다.
  이력은 **분석 성공 뒤 attempt 를 등록하는 PHASE 8-4 저장 정책**을 그대로 따른다 —
  따라서 **분석에 실패한 제출(422)은 이력에 나타나지 않고**, `REVIEW_REQUIRED` 제출은 나타난다.
- **상세 연결**: `detail_path` 는 `#` 을 `%23` 으로 인코딩한 경로다(경로에 그대로 쓰면 URL fragment 로 잘린다).
  `raw_result`·`errors`·`step_list` 는 학생 이력 응답에 중복 노출하지 않는다.
- **읽기 전용**: `analyze_attempt()` 미호출, 저장 없음, Knowledge State 미변경, 분석 게이트(`claim_analysis`)도 잡지 않는다.
- `StudentAttempt.analysis` 필드는 코드에서 미사용(항상 `None`)이므로 링크는 저장소 조회로 구한다.
- 미구현: 오개념 TOP 5 집계(r61/원본), 학생 검색(`GET /api/v1/students`), 인증·권한, 날짜 필터.
  (문제 표시 텍스트·단원명은 PHASE 8-8 에서 해결 — §9)

## 8. attempted_at (명세 r49) — PHASE 8-7

`StudentAttempt.attempted_at: datetime` — timezone-aware UTC 로 저장·응답하며 ISO 8601 로 직렬화된다.
naive 값이 들어오면 UTC 로 간주하고, 다른 timezone 은 UTC 로 변환한다(`as_utc`).

| 정책 | 동작 |
|---|---|
| 1. 기록 시점 | **요청 접수 시점**(엔드포인트 최상단, 게이트 대기 이전)에 `utc_now()` 로 한 번 기록 |
| 2. 보존 | 분석 성공으로 attempt 가 저장될 때 그 값을 그대로 저장 |
| 3. 멱등 재요청 | `analyze_attempt()` 를 다시 호출하지 않으므로 저장된 `attempted_at` 을 그대로 반환 |
| 4. 충돌 409 | 저장본을 건드리지 않으므로 기존 시각 유지 |
| 5. 재풀이 | 새 `attempt_id` 는 별도의 `attempted_at` 을 가진다 |
| 6. 정렬 | 학생 이력은 `attempted_at` 최신순, 동률은 나중 저장 우선 (명시적 정렬 키) |
| 7. 요청 | **클라이언트가 시각을 보내도 받지 않는다**(스키마에 필드 없음 → 422). 서버 UTC 만 신뢰 |
| 8. 실패 제출 | 저장 정책 불변 — 분석 실패(422) 제출은 attempt 로 저장되지 않아 시각도 남지 않는다 |

- 응답 노출 위치: `POST /api/v1/attempts/{id}/analyze` (26 필드), attempt 상세 GET (27 필드),
  `GET /api/v1/students/{id}/history` 항목 (20 필드).
  attempt 분석 **목록**(`GET .../analyses`) 요약에는 넣지 않았다(분석 스냅샷 항목이지 attempt 행이 아님).
- `analysis_id` 와 마찬가지로 `attempted_at` 은 DB 유니크/정렬 인덱스 대상이 되므로,
  MySQL 단계에서 `DATETIME(6)` + 인덱스로 옮길 수 있다.

## 9. 문제 표시 메타데이터 — PHASE 8-8

`Problem` 에 표시용 메타데이터를 **선택 필드로 추가**했다. 저장·반환은 명시 입력만 사용하고 추측하지 않는다.

| Problem 필드 | 요청 필드 | 명세 | 설명 |
|---|---|---|---|
| `question_text` | `problem_question_text` | `problems.question` (r25) | 학생 제시용 표시 텍스트. `problem_latex`(기계 검증용 식)과 구분 |
| `concept_name` | `problem_concept_name` | `concepts.name` (r33) | 단원/개념 표시명. **skill_id 에서 파생하지 않음** |
| `unit_name` | `problem_unit_name` | (대응 컬럼 없음) | 표시 전용 단원명 |

학생 이력 응답(`GET /api/v1/students/{student_id}/history`)은 `problem_text`·`concept_name`·`unit_name` 을
nullable 로 노출한다(항목 23 필드). 메타데이터가 없으면 `null` 이고 taxonomy 이름으로 대체하지 않는다.

| 정책 | 동작 |
|---|---|
| 최초 등록 시만 저장 | `_resolve_problem()` 이 저장본을 먼저 조회하고, 없을 때만 요청 값으로 등록 |
| 저장본 우선 | 이미 등록된 문제의 `problem_latex`·`skills`·표시 메타데이터를 요청 값으로 덮어쓰지 않는다(동시 등록 시 `E_DUPLICATE_ID` 재조회도 저장본 반환) |
| attempt 멱등성 | attempt 본문 비교는 `problem_id`·`student_id`·`solution_text` 만 사용하므로 메타데이터 변경은 409 를 만들지 않는다 |
| 하위호환 | 메타데이터 없는 기존 요청은 그대로 동작하고 이력에서 `null` 을 받는다. `POST /analyze-solution` 계약(16 필드) 무변경 |

### ID 매핑 결정 상태 (PHASE 8-9)

- **방향 결정: 별도 명시적 매핑 테이블 채택.** `concepts` 테이블은 그대로 두고 `skill_concept_map` 만 추가한다.
  상세 설계·차단 요인·미결정 목록은 **§10** 참고.
- **구현 보류**: 공식 Concept seed 가 제공되기 전까지 **mapping row 를 만들지 않는다.**
  아래 제약은 구현 시에도 계속 적용된다(추측 금지).
  - `skill_id` 는 AI①/BKT 의 기존 frozen key 로 유지한다
  - `concept_id` 를 **이름 유사성 · Skill 순서 · `skills.json.unit` 값**으로 추측하지 않는다
  - `Problem.skills` 만으로 `problem_concepts` 를 **자동 생성하지 않는다**
  - 하나의 Skill mastery 를 여러 Concept 에 **복제하지 않는다**
  - taxonomy 과 기존 API 응답 계약은 변경하지 않는다
- **`concepts.name` vs `skills.json.name`**: taxonomy 의 `name` 은 skill 설명용이고 DB 의 `concepts.name` 은 개념 표시용이라
  의미가 다르다. 두 name 을 같은 값으로 취급하지 않았고, fallback 도 쓰지 않는다.
- **`unit_name`**: 명세에 대응 컬럼이 없는 표시 전용 필드다. `skills.json.unit` 과 관계를 정의할지 미결정(§10-7).
- `problems.answer`(r26)·`difficulty`(r27)·`problem_type`(r28)·`concepts.subject`(r35)·`grade_level`(r36)·`parent_concept_id`(r37) 미구현.

## 10. Skill ↔ Concept 매핑 설계 기록 (PHASE 8-9) — **방향 결정, 구현 보류**

이 절은 **설계 결정 기록**이다. 코드·데이터 파일은 변경하지 않았고, 매핑 row 도 생성하지 않았다.

### 10.1 확인한 사실

| 출처 | 내용 |
|---|---|
| `data/skills.json` | 26 base + 5 expansion = 31 skill. `meta.description`: "skill_id는 BKT key이므로 확정 후 변경 금지". `bkt_eligible=false` 2개(group node), `status=future` 5개 → **추적 대상 24개** |
| `data/misconceptions.json` | 19개. `related_skill_ids` 1개 12건, **2개 7건**(2.1·2.4·3.1·3.2·3.6·3.7·4.2) |
| `app/taxonomy.py` | `validate_tracking_skill()` 이 frozen `bkt_eligible`+`status` 를 source of truth 로 강제 |
| `app/pipeline/session.py` | `KnowledgeObservation.skill_id` 는 추적 가능 skill 만 통과 → **Knowledge State 키는 반드시 skill_id** |
| 명세 r32–38 | `concepts(id INT PK, name, description, subject, grade_level, parent_concept_id, created_at)` — 자기 참조 트리 |
| 명세 r40–41 | `problem_concepts(problem_id, concept_id)` (PK,FK) — **N:M** |
| 명세 r54–60 | `student_states(..., student_id, concept_id)` — (학생, 개념) 행 |
| 명세 r63–66 | `misconceptions(..., concept_id NOT NULL)` — 단일 FK |
| 명세 UI r54 | "AI②가 추천한 **concept_id** 로 개념 학습 뷰 라우팅" — **AI②는 concept_id 소비** |

### 10.2 Skill 과 Concept 의 분리

| | Skill (AI①) | Concept (명세 백엔드) |
|---|---|---|
| 식별자 | `skill_id` VARCHAR (`linear_equation`) | `concepts.id` INT PK |
| 책임 | BKT 관측 단위. 숙련도·상태가 붙는 키 | 학습 내용·표시 단위. 문제·오개념·학생상태가 FK 로 붙는 대상 |
| 계층 | `parent_skill_id` + `prerequisite_skill_ids`(다수) | `parent_concept_id`(단일 부모) |
| 사용처 | Knowledge State, 오류 분류, `Problem.skills`, `KnowledgeObservation` | `problem_concepts`, `misconceptions.concept_id`, `student_states.concept_id`, AI② 라우팅 |
| 상태 | **frozen** (변경 금지) | 명세 소유 |

**Skill 은 측정 단위, Concept 은 참조·표시 단위.** AI① 내부 계산은 skill_id 만 쓰고 concept_id 는 경계 밖에서만 등장해야 한다.

### 10.3 매핑 기수 — 1:1 아님

| 관계 | 명세 기수 | AI① 측 | 결론 |
|---|---|---|---|
| skill ↔ concept | 미정 | 31 skill | **M:N 허용 필요.** 1:1 을 주장할 근거가 없음 |
| problem ↔ concept | **N:M** | `Problem.skills` 1:N | `Problem.skills` 전개를 **하지 않는다**(추측). 명시 등록 필요 |
| misconception → concept | **1** (NOT NULL) | `related_skill_ids` 1 또는 2 | **불일치.** 7건의 축 선택 필요 |
| student_state → concept | (학생, concept) | (학생, **skill**) | skill→concept 변환이 선행 |

### 10.4 제안 매핑 구조 (최소 필드)

```sql
CREATE TABLE skill_concept_map (
  skill_id     VARCHAR(64) NOT NULL,   -- frozen taxonomy 참조만(FK 없음)
  concept_id   INT         NOT NULL,   -- FK -> concepts(id)
  source       VARCHAR(64) NOT NULL,   -- 공식 ID 출처
  PRIMARY KEY (skill_id, concept_id)
);
CREATE INDEX ix_skill_concept_concept ON skill_concept_map(concept_id);
```

| 필드 | 역할 | 제약 |
|---|---|---|
| `skill_id` | AI① BKT 키 | taxonomy 미등록 값 금지. group node 매핑 허용 여부는 미결정(10-8) |
| `concept_id` | 명세 concepts PK | **공식 ID 만 삽입.** 미확인 정수 생성 금지 |
| `source` | ID 출처 추적 | 공식 자료 없이는 행 생성 불가 |

`mapping_kind`(primary/secondary)는 1:N 축 지정이 필요해질 때 추가한다(10-3).
적용 무결성 검증은 `CHECK` 대신 애플리케이션(`is_known_skill`)으로 한다.

### 10.5 연결 흐름과 변환 위치

```
Problem.skills (skill_id 1:N)
      │  분석·분류는 skill_id 만 사용 — 개념 개입 없음
      ▼
KnowledgeObservation.skill_id ──BKT──▶ Knowledge State[(student, skill)]
      │
      │  ★ 변환 지점: 영속화 계층(Persister) 내부
      ▼
skill_concept_map ──▶ concept_id ──▶ student_states.concept_id
                              ├─────▶ problem_concepts.concept_id
                              └─────▶ misconceptions.concept_id ──▶ AI②
```

- **변환 위치는 `app/pipeline/repository.py` 계층 내부로 한정한다.** `analyze_attempt()`·`knowledge.py`·BKT 엔진에는 넣지 않는다.
  Knowledge State 는 skill_id 기반 dict 이며 엔진·테스트·frozen taxonomy 이 이를 가정하므로, 변환을 넣으면 in-memory MVP 가 concept_id 의존으로 오염된다.
- **방향은 쓰기 시 `skill_id → concept_id` 단방향.** 되읽기는 N:M 이므로 단일 복원 불가 → 표시는 `concepts.name` JOIN.
- **미매핑 skill 은 `concept_id` NULL 로 두거나 행을 스킵한다.** placeholder 정수 ID 를 만들지 않는다.

### 10.6 AI② 전달 구분

| 대상 | 값 | 근거 |
|---|---|---|
| AI② (콘텐츠 추천·라우팅) | **`concept_id` (INT)** | 명세 UI r54 |
| AI② (분석 근거 설명) | `skill_id` (문자열) | 사람이 읽는 근거 표현 |
| AI① 내부 계산 | `skill_id` 만 | frozen taxonomy |

매핑이 없는 skill 은 concept_id 를 지어내지 **않고** AI② 전달을 중단한다.

### 10.7 차단 요인 — 공식 Concept seed 필요

**현재 확정된 mapping row 는 0개**이며, 공식 `concepts` 목록(id·name)이 없으면 어떤 행도 만들 수 없다.
이 자료가 있어야 다음 9개가 결정된다.

| # | 미결정 항목 | 필요한 자료 |
|---|---|---|
| 1 | 31 skill 각각의 `concept_id` | 공식 concept 시드 + 매핑 승인 |
| 2 | `mapping_kind` 필요 여부 및 `primary` 1개/skill 제약 | 개념 목록의 세분화 정도 |
| 3 | skill→concept fan-out 시 숙련도 **복제 금지**(결정된 제약) → 미매핑/단일 축 처리 | 동일 |
| 4 | `problem_concepts` 등록 경로(자동 전개 금지 → 명시 등록) | 문제 등록 정책 |
| 5 | **오개념 7건**(2.1·2.4·3.1·3.2·3.6·3.7·4.2)의 축 skill 선택 | 개념 목록 + `concept_id` NOT NULL 해석 |
| 6 | `unit_name` ↔ `skills.json.unit` 관계 | concepts 에 단원 개념 존재 여부 |
| 7 | group node 2개(`polynomial_arithmetic`, `factoring`) 매핑 여부 | 개념 목록의 추상도 |
| 8 | `status=future` 5개 skill 매핑 시점 | 확장 범위 결정 |
| 9 | 매핑 로더 위치(taxis 아닌 별도 모듈) | 구현 Phase 설계 |

### 10.8 영향 범위 — 코드/테스트/계약

| 대상 | 영향 |
|---|---|
| frozen taxonomy / `data/*.json` | **없음** (불변) |
| `/analyze-solution` (16 필드) | **없음** |
| `POST /api/v1/attempts/{id}/analyze` (26 필드) | **없음** (additive 없음) |
| attempt / 학생 이력 GET | **없음.** `problem_text`·`concept_name` 은 Phase 8-8 의 명시 입력이라 매핑과 독립 |
| `session.py`·`knowledge.py`·`analyzer.py`·`taxonomy.py` | **변경 없음** |
| `tests/**` (840건) | **변경 없음.** 매핑 미구현 상태로 스냅샷 불변 |

### 10.9 다음 Phase 제안 (공식 자료 수신 후)

1. **매핑 데이터**: `data/skill_concept_map.json`(또는 seed SQL) — 공식 자료만 근거로 행 작성, 각 행에 `source` 출처 기록
2. **검증 테스트**: skill_id 가 taxonomy 에 존재하는지, concept_id 가 공식 목록에 존재하는지, 미매핑 skill 이 NULL 로 남는지, **fan-out 시 복제 없음**을 검증
3. **영속화 경계**: Persister 내부 `skill_id → concept_id` 변환 + `student_states` upsert, `problem_concepts`/`misconceptions.concept_id` 반영
4. 기존 840건 회귀 + AI② 전달 계약 검증 포함

## 11. 반복 오개념 TOP — PHASE 8-10 (웹 r30, 앱 r64)

`GET /api/v1/students/{student_id}/misconceptions/top?limit=5`
"반복 오개념 TOP 5"(웹 r30) / "취약 개념 우선순위 카드"(앱 r64) 요구사항에 대응한다.

응답: `student_id`, `limit`, `order`, `distinct_misconception_count`, `items[]`
항목: `rank`, `misconception_id`, `name`, `description`, `attempt_count`, `related_skill_ids`

| 쿼리 | 기본 | 범위 | 위반 |
|---|---|---|---|
| `limit` | 5 | 1 ~ 20 | **422** |
| `from` / `to` | 없음 (전체 기간) | offset 포함 ISO 8601 datetime | **422** (PHASE 8-11) |
| `student_id` 형식 오류 | — | — | **422** `E_INVALID_STUDENT_ID` (이력 API 와 동일) |

### 집계 기준

| # | 정책 | 구현 |
|---|---|---|
| 1 | taxonomy ID 만 사용 | 원본은 `ErrorRecord.misconception_ids`. `name`·`description`·`related_skill_ids` 는 `misconception_by_id()` 조회. `SIGN_ERROR` 류 AI② 전용 별칭은 taxonomy ID 로 쓰지 않는다 |
| 2 | Attempt 안 중복 1회 | attempt 별 `set(misconception_ids)` 후 1회 증가 |
| 3 | Attempt 간 반복 횟수 | `attempt_count` = 오개념이 등장한 서로 다른 Attempt 수 |
| 4 | 최신 분석만 | `list_analyses(attempt_id)[-1]` 1건만 사용. 과거 분석은 합산하지 않음 |
| 5 | REVIEW_REQUIRED 포함 | 확정 `ErrorRecord` 에 taxonomy ID 가 있으면 집계. `UNKNOWN` 은 422 로 저장되지 않음. 근거 없는 계산 오류는 ID 가 없어 자동 제외 |
| 6 | 정렬 고정 | `attempt_count` 내림차순 → 동률 `misconception_id` 오름차순. 저장소 스냅샷 기반이라 반복 조회 시 순서 동일 |
| 7 | 학생 격리 | `list_attempts(student_id=)` 필터. 기록 없으면 200 + 빈 목록 |
| 8 | 최소 필드 | `misconception_id` + taxonomy `name`/`description` + `attempt_count` + `related_skill_ids` (+ `rank`) |

### 제약

- **concept_id · Skill↔Concept 매핑 미사용** (Phase 8-9 보류). 응답에 `concept_id` 가 없다.
- 조회 중 `analyze_attempt()` 미호출, 저장 없음, Knowledge State 무변경, 분석 게이트(`claim_analysis`)도 잡지 않음.
- 기간 필터·오개념 해결 상태(`student_misconceptions.resolved_at`)·인증·권한·MySQL 은 범위 외.
- `distinct_misconception_count` 는 `limit` 절단 전 총 개수라 UI 가 "총 N개 중 상위 5개" 를 표시할 수 있다.

## 12. 기간 필터 (`from` / `to`) — PHASE 8-11

`GET /api/v1/students/{student_id}/history` 와 `GET /api/v1/students/{student_id}/misconceptions/top`
두 엔드포인트가 **동일한 기간 계약과 경계 정책**을 공유한다(공통 헬퍼 `_resolve_time_range` / `_student_attempts`).

### 구간 정책

| 항목 | 정책 |
|---|---|
| 입력 형식 | **timezone offset 이 포함된 ISO 8601 datetime** (`2026-09-28T10:00:00Z`, `...+09:00`) |
| 정규화 | 비교 전 **UTC 로 변환**. 같은 instant 를 다른 offset 으로 표현해도 결과가 같다 |
| 구간 | **`[from, to)` — from 포함, to 제외** |
| 생략 | `from`/`to` 각각 선택 사항. 미지정 시 그쪽 경계만 제한하지 않으며 전체 기간 조회와 동일 |
| `from >= to` | **422** `E_INVALID_TIME_RANGE` (같은 instant 도 422) |
| offset 없음 / 파싱 불가 | **422** `E_INVALID_INSTANT` (`2026-09-28T10:00:00`, `2026-09-28`, 숫자 epoch 등) |
| 검증 순서 | 기간 파싱·검증 → `student_id` 검증 (둘 다 422 이지만 코드로 구분) |

### 적용 순서

| 엔드포인트 | 순서 |
|---|---|
| `history` | student_id 검증 → 기간 필터 → `attempted_at` 최신순(동률은 나중 저장 우선) → `total` 계산 → offset/limit 페이지네이션 |
| `misconceptions/top` | student_id 검증 → 기간 필터 → attempt별 **최신 AnalysisRecord** → distinct 집계 → `(-count, id)` 정렬 → `limit` |

`total`·`total_pages`·`distinct_misconception_count` 는 **필터 적용 후** 값이라 UI 가 "기간 내 N건" 을 정확히 표시한다.

### attempted_at 의 의미 (재확인)

- `attempted_at` 은 **서버가 첫 분석 요청을 받은 시각**(UTC)이며, **클라이언트의 실제 풀이 시각이 아니다.**
  이미지 OCR 경로처럼 제출 시점이 늦어질 수 있는 현재 구조에서는 두 값이 다를 수 있다.
- 명세 `attempts.solve_time_seconds`(r47)와도 무관하다. 클라이언트 시각 입력은 정책상 받지 않는다.
- 따라서 기간 필터는 "이 서버가 요청을 받은 시간 기준"이며, 정렬·TOP 집계도 동일 기준이다.

### 범위 외

- MySQL, `resolved_at` 기반 해결 판정, concept_id 매핑(Phase 8-9 보류), 인증·권한, 페이지 커서 방식.
- attempt 분석 이력 조회(`GET .../analyses`)에는 시간 필터가 없다 — attempt 단위 목록이 아니라 분석 스냅샷 목록이라 경계 기준이 다르다.

# AI① ↔ STEPi 기능명세서 대조표

원본: `docs/STEPi_기능명세서_원본.md` (xlsx 파싱본)
코드 기준: `app/`, `data/` (2026-11-23 실행, pytest 1935 passed, 1 warning)

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
| 웹 r26/r27 | 개념 영역별 숙련도 차트 | skill별 mastery 조회 | O | **`GET /api/v1/students/{student_id}/skills`** (engine=rule\|bkt, 관측된 Skill만, skill_id 오름차순). 비고: AI① 로컬 in-memory 상태 기준이며 Backend 최종 상태(MySQL `student_states`)가 아니다 |
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
| `/api/v1/...`접두사 계열 (`/api/v1/students`, `/api/v1/reports/pdf`) | `/analyze-solution`, `/health`, `POST /api/v1/attempts/{attempt_id}/analyze`, `GET /api/v1/attempts/{attempt_id}/analyses`, `GET /api/v1/attempts/{attempt_id}/analyses/{analysis_id}`, `GET /api/v1/students/{student_id}/history`, `GET /api/v1/students/{student_id}/misconceptions/top` (일부만 prefix 적용) | P |
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
9. **Backend ↔ AI① 응답 v1 계약** (`analysis_status` / `error_type`·`error_subtype` / 대표 오류 선택 /
   단일 `skill`·`misconception` target / `overall_confidence` 병합 / ID int↔string 변환) → **§14**
10. **Transposition 공식 코드 연결**: `3.1`→`MIS-2-001`, `3.2`→`MIS-2-002`, `transposition`→`SKL-2-037` → **§14.4**
11. **OCR 경계**: `POST /api/v1/attempts/{id}/ocr` 책임(AI① vs 외부 서비스), 이미지 reference 전달 방식 → **§14.1**
12. **Backend 전달 경로(PHASE 9-7)**: `POST /api/v1/attempts/{id}/analyze` + 응답 additive 5필드
    (`misconception_tag`, `official_*`, `provisional_*`). 계약 12항목은 **잠정**이며,
    code mapping 은 승인 2건 / provisional 3건 / 미매핑 나머지로 분리했다 → **§14.10~14.16**

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

## 13. Backend 승인 코드 crosswalk — PHASE 9-4A

`app/integration/backend_crosswalk.py` — **Backend-facing 출력 경계에서만** 호출하는 변환 모듈.

### 승인된 매핑 (Backend 승인 확정분)

| AI① 내부 ID | Backend 공식 코드 | 종류 |
|---|---|---|
| `distribution` | `SKL-1-004` (다항식의 곱 전개하기) | Skill |
| `2.1` (분배법칙 누락) | `MIS-1-004` (분배법칙 적용 오류) | Misconception |

### 미매핑 (Backend 승인 없음 → `None` / unmapped)

| AI① 내부 ID | 사유 |
|---|---|
| `transposition` / `3.1` | 등식 이항 시 부호 미반전. Backend 대응 코드 없음 |
| `arithmetic` / `arithmetic_slip` | Backend `MIS-1-002` 는 다항식 동류항 한정 |
| `linear_equation` / `misread_problem` | 문제 오독 코드 없음. STP 42개에 일차방정식 개념 부재 |
| 그 외 모든 내부 ID | Backend 별도 승인 전 |

### Concept(STP) 처리

- **AI① 은 STP(concept) 코드를 반환하지 않는다.** Backend 가 공식 Skill-Concept 관계에서 도출한다.
- 모듈에 concept 변환 함수가 **없다** (테스트로 공개 함수 목록 고정).

### 원칙 구현

| # | 원칙 | 구현 |
|---|---|---|
| 1 | frozen taxonomy ID·Knowledge State 키 유지 | 변환은 dict 조회뿐, 원본 무변형 |
| 2 | Backend-facing 경계에서만 | `app/integration/` 격리. **기존 모듈에서 import 0건**(AST 테스트로 고정) |
| 3 | 미승인 ID 는 임의 변환 금지 | 사전 미존재 → `None`. 코드 생성 경로 없음 |
| 4 | `SIGN_ERROR` 미사용 | 코드 패턴 검증 `^(SKL\|MIS)-\d+-\d+$` (import 단계) |
| 5 | 기존 API·taxonomy·golden 불변 | serializer 미수정. 필드 수 16/26/27/23 고정 테스트 |
| 6 | Backend 응답 전체 조립 제외 | **호출 지점 0개.** `analysis_status`·confidence·multi-error 계약 확정 후 |

### 남은 미결정 (U-1~U-6)

대표 오류 선정 / 다중 오류 전달 / 복수 오개-concept 처리 / confidence 병합 /
skill_code 대표 선정 / `unmapped` 표현(필드 생략 vs `null`).

코드 출처: `AI1_공식_STP_SKL_MIS_관계자료.xlsx` (원본 `STEPi 기능명세서.xlsx` 전사).


## 14. Backend ↔ AI① API 계약 대조표 (PHASE 9-6)

대상 문서: `docs/STEPi_AI1_API_명세서_v1.0.docx`, `docs/STEPi_AI1_기능명세서_v1.0.docx`,
`AI1_공식_STP_SKL_MIS_관계자료.xlsx`, 현재 `app/schemas.py`·`app/main.py`·`app/pipeline/repository.py`,
Backend 연동 명세(AIAnalysisRequest/Response, TutorRequest).

상태 표기: **확정** / **제안** / **미결정** / **현재 AI① 구현**.
Backend 승인 없이 어떤 계약도 확정으로 기록하지 않습니다.

### 14.1 호출 흐름과 Endpoint

| # | 항목 | Backend 기준 | AI① 현재 | 상태 |
|---|---|---|---|---|
| 1.1 | 최종 경로 | `APP → Backend(Attempt·이미지 저장) → AI① → Backend(분석·Student State) → AI② → APP` | AI①은 Backend/DB에 직접 접근하지 않음 | 확정 |
| 1.2 | 텍스트 분석 | `problem` + `solution` 입력 | `POST /api/v1/attempts/{id}/analyze` (string ID) | 현재 AI① 구현 |
| 1.3 | 이미지 분석 | `source_type="captured"` + image reference | **미구현** | 미결정 |
| 1.4 | OCR endpoint 경계 | **PHASE 9-8A 구현** (`OcrProvider` 인터페이스) | 잠정 |
| 1.5 | 이미지 전달 방식 | `solution_image_url`(string reference) | 이미지 접근 코드 없음 | 제안 |

**1.5 제안**: AI① 은 이미지 bytes 를 받지 않고 **인증된 reference 를 읽는다.** 이미지의 영속·보관은
Backend 책임(기능명세 §2 "Backend" 행)이며, AI① 이 업로드 받으면 영속 책임이 이중화되고 인증 방식도 갈립니다.

**1.3 / 1.4 경로 분리 — 통일**

| 경로 | 대상 | 상태 |
|---|---|---|
| `POST /analyze-solution` | 레거시 단일 분석 (text only) | 현재 AI① 구현 |
| `POST /api/v1/attempts/{id}/analyze` | Attempt 분석 + 저장 + 멱등 (text) | 현재 AI① 구현 |
| `POST /api/v1/attempts/{id}/ocr` | 이미지 OCR | **미구현** |

이미지 분석(1.3)은 `/ocr` 호출 후 `/analyze` 를 이어서 호출하는 **2단계**로 정리합니다.
`/analyze` 에 이미지 전용 필드를 넣지 않으므로, §14.2 의 `image_reference` 는 `/ocr` 요청에 속합니다.

### 14.2 Request field mapping

| Backend 필드 | 출처 | AI① 대응 | 상태 |
|---|---|---|---|
| `request_id` | Backend 연동 명세 §1 (원문 미확인) | `attempt_id`(path) 로 대체 가능 | 제안 |
| `attempt_id` | Backend 연동 명세 §1 (원문 미확인) | `attempt_id` **str** | 미결정 — int→string 규칙 필요 |
| `student_id` | Backend 연동 명세 §1 (원문 미확인) | `student_id` **str** (1~64자) | 미결정 — 위와 동일 |
| `problem.source_type` | Backend 연동 명세 §2 (원문 미확인) | 없음 | 미결정 |
| `problem.problem_id` | Backend 연동 명세 §2 (원문 미확인) | `problem_id` str | 미결정 |
| `problem.captured_problem_id` | Backend 연동 명세 §2 (원문 미확인) | 없음 | 미결정 |
| `problem.problem_text` | Backend 연동 명세 §2 (원문 미확인) | `problem_question_text` / `problem_latex` | 제안 — 텍스트→question, LaTeX→latex 분리 |
| `problem.problem_image_url` | Backend 연동 명세 §2 (원문 미확인) | 없음 | 미결정 |
| `problem.skills` | **BE 원본 미확인** | `problem_skills` (string[]) | **tracking Skill 최소 1개 필수** (PHASE 9-7.1). 내부 skill_id 또는 SKL code |
| `solution.solution_text` | Backend 연동 명세 §2 (원문 미확인) | `solution_text` (**required**) | 제안 — OCR 경로에서 optional 화 |
| `solution.solution_image_url` | Backend 연동 명세 §2 (원본 미확인) | 없음 | 미결정 |
| `image_reference` (규격 요청 필드) | §14.1 2단계 경로 | `image_reference` (str, **required**) | **PHASE 9-8A 구현** — 사진 bytes 는 받지 않는다 |
| `attempt.retry_count` / `hint_count` / `solve_time_seconds` | Backend 연동 명세 §2, 기능명세 "AI1 does not author these signals", xlsx `attempts` 시트 | 없음 | **제안 철회 — AI① 입력에서 제외** |

**제안 철회 근거 (11)**: 기능명세 "AI① does not author these signals" 이고 xlsx `attempts` 시트도
Backend 소유 필드입니다. AI① 은 이들을 **입력으로 받지 않고**, BACKEND 가 `TutorRequest.attempt_context`
로 AI② 에 전달합니다. AI① 입력에 추가할 근거가 없습니다.

**Backend 연동 요청 예시 (문제 1건)**

```json
POST /api/v1/attempts/4821/analyze
{
  "attempt_id": "4821",
  "problem_id": "PRB-000123",
  "problem_latex": "2(x-3)=6",
  "problem_skills": ["SKL-1-004", "linear_equation"],
  "student_id": 4821,
  "solution_text": "2(x-3)=6\n2x-3=6\n2x=13\nx=6.5"
}
```

- `problem_skills` 는 **tracking Skill 을 최소 1개** 선언해야 한다.
  - `["SKL-1-004", "linear_equation"]` — `SKL-1-004` 는 approved code 라 내부 `distribution` 으로
    역변환되고, `linear_equation` 은 내부 ID 로 그대로 통과한다.
  - `["SKL-2-037"]` — provisional code 라 내부 `transposition` 으로 역변환된다.
  - `linear_equation` 은 현재 **공식 Backend code 가 미매핑** 상태다. Backend 는 내부 ID 로만
    보낼 수 있고, 코드 매핑이 필요하면 승인 항목이다 → Q16.
- group node(`factoring` 등)는 함께 선언해도 되지만 Knowledge State 관측 대상이 아니다.
  **저장된 문제는 저장본이 source of truth** 이므로 멱등 재요청에서 `problem_skills` 를
  생략해도 정상 동작한다.

**거절 규칙 (기본 Skill 을 추측하지 않는다)**

| 요청 | HTTP | `detail.code` | 비고 |
|---|---|---|---|
| `problem_skills` 누락 / 빈 목록 | 422 | `E_SKILL_NOT_TRACKABLE` | 기본 Skill 채우지 않음 |
| group node 만 (예: `["factoring"]`) | 422 | `E_SKILL_NOT_TRACKABLE` | 관측 대상 skill 없음 |
| taxonomy 미등록 ID (예: `NOT_A_SKILL`) | 422 | `E_UNKNOWN_SKILL` | Problem 생성 전에 거절 |
| crosswalk 에 없는 SKL 코드 (예: `SKL-9-999`) | 422 | `E_UNKNOWN_SKILL_CODE` | 기본 Skill 으로 치환하지 않음 |
| group node + tracking Skill | 200 | — | 정상 |

**오류 우선순위 (변경하지 않음)**: 파싱·검증 오류(`E_UNRECOGNIZED` / `E_UNVERIFIABLE` /
`E_EMPTY_SOLUTION` / `E_INPUT_TOO_LARGE`)가 tracking Skill 부재보다 **먼저** 온다.
최종 코드와 HTTP 상태는 어느 쪽이든 동일하다.

**거절된 요청의 저장 정책 (PHASE 9-7.2)**

tracking Skill 이 없어 422 로 거절된 **새 문제** 요청은 저장소에 아무것도 남기지 않는다.

| 상황 | Problem | 정답 풀이 | Attempt | 분석 |
|---|---|---|---|---|
| 정상 분석 | 등록 | 등록 | 등록 | 등록 |
| 파싱·검증 오류 (`E_UNRECOGNIZED` 등) | 등록(기존 동작) | 등록(기존 동작) | 미등록 | 미등록 |
| tracking Skill 누락 (`E_SKILL_NOT_TRACKABLE`) | **미등록** | **미등록** | 미등록 | 미등록 |
| taxonomy 미등록 ID (`E_UNKNOWN_SKILL`) | **미등록** | 미등록 | 미등록 | 미등록 |
| 모르는 SKL 코드 (`E_UNKNOWN_SKILL_CODE`) | **미등록** | 미등록 | 미등록 | 미등록 |

**왜 미등록인가** — 예전에는 문제를 분석 전에 저장했다. 그래서 tracking Skill 이 없는
요청이 `skills=[]` 인 불완전한 문제를 남겼고, 그 뒤 **같은 `problem_id`** 로 올바른
tracking Skill 을 넣어 다시 요청해도 저장본이 source of truth 라 계속 422 가 났다.
**복구 요청으로도 되돌릴 수 없는 상태**였다. 이제 거절된 요청은 아무것도 남기지
않으므로 같은 `problem_id` 로 올바른 값을 넣어 다시 요청하면 정상 분석된다.

저장 순서는 그대로 유지된다: **문제 → 정답 풀이 → Attempt → 분석.**
(문제 저장은 분석 뒤로 미뤄졌지만, 저장되는 순서와 내용은 변하지 않는다.)

이미 등록된 문제는 기존 source-of-truth 정책대로 요청의 `problem_skills` 로 덮어쓰지 않는다.

**동시 요청의 source-of-truth 보장 (PHASE 9-7.3)**

지연 저장은 같은 ID 로 동시에 들어온 요청이 서로 다른 후보를 분석·저장하는
구멍을 만든다. 그래서 리소스 ID 단위 락으로 `resolve → source-of-truth 확인 →
analyze → persist` 구간을 직렬화한다. 항상 `problem` → `solution` 순서로 획득하므로
교착이 생기지 않는다.

| 상황 | 처리 |
|---|---|
| 같은 `problem_id` 동시 신규 등록 | 직렬화. 나중 요청은 먼저 저장된 Problem 을 source-of-truth 로 쓰고 **그 기준으로 분석**한다 |
| 같은 `solution_id` 동시 사용 (서로 다른 Problem) | 한쪽은 `409 E_SOLUTION_PROBLEM_MISMATCH`. 성공 응답으로 나가지 않고 **분석·저장하지 않는다** |
| `E_DUPLICATE_ID` 발생 | **무시하지 않는다.** 저장된 레코드와 분석에 쓴 레코드를 비교해 다르면 `409 E_RESOURCE_CONFLICT` 로 거절하고 Attempt/분석을 저장하지 않는다 |

같은 `problem_id` 동시 등록에서 **둘 다 200 이어야 하는 것은 아니다** — 계약은 둘 중

**동일 attempt_id 동시 충돌 (PHASE 9-7.4)**

같은 `attempt_id` 로 요청이 들어오면 본문(`problem_id` / `student_id` / `solution_text`)이
같을 때만 기존 분석을 돌려준다. 다르면 409 `E_ATTEMPT_CONFLICT` 다.

| 상황 | 응답 |
|---|---|
| 동일 본문 재전송 (동시 포함) | 200 멱등 — 같은 `analysis_id`, 분석 1건·관측 1회 |
| 다른 본문 (동시) | 한쪽만 성공, 다른 쪽 **409** `E_ATTEMPT_CONFLICT` |

비교는 **두 번** 한다. 분석 전(락 밖) + **저장된 분석을 반환하기 직전**(attempt 락 안).
후자가 없으면 동시 요청에서 나중 요청이 사전 검사를 통과해 **다른 제출의 분석을 200 으로
돌려받는다.** 충돌 요청은 분석을 재실행하지 않고 Knowledge State 도 갱신하지 않으며
분석 이력도 추가되지 않는다.

하나다: (a) 둘 다 200 이면 두 분석 모두 최종 저장된 Problem 기준, (b) 한쪽이 명확한
conflict 면 그쪽은 분석도 Knowledge State 변경도 없어야 한다.




### 14.3 Response field mapping

| Backend 필드 | 출처 | AI① 대응 | 상태 |
|---|---|---|---|
| `is_correct` | **BE 원본 미확인** (Backend 연동 명세 "AIAnalysisResult" 참조) | `correct` | 확정 |
| `first_error_step` | **BE 원본 미확인** (동 상) | `error_step` / `first_error_step_index` (1-based) | 확정 |
| `error_content` | **BE 원본 미확인** (동 상) | `errors[].description_ko` 또는 `evidence_latex` | 미결정 — 어느 것을 쓸지 |
| `error_type` / `error_subtype` | **BE 원본 미확인** | `error_type` / `error_subtype` | 현재 AI① 구현 (전달 여부 미결정) |
| `concept` | **BE 원본 미확인** | **AI① 반환 안 함** | 목표, 협의 필요 |
| `skill` (단일) | **BE 원본 미확인** | `skill` / `skill_ids[]` | 미결정 — 대표 선정(§14.5) |
| `misconception` (단일) | **BE 원본 미확인** | `misconception_id` / `misconception_ids[]` | 미결정 |
| `overall_confidence` | **BE 원본 미확인** | `confidence` (대표 오류 1건값) | 미결정 — 병합 규칙 |
| `analysis` (스키마명) | **BE 원본 미확인** | `api_response` (16키) | 현재 AI① 구현 |
| `request_id` / `attempt_id` | **BE 원본 미확인** | `attempt_id` (string) | 확정 (형식만 미결정) |
| — | AI① API §5.2, 대조표 앱 r45·r46 | `step_list[]` (`is_correct`·`status` 포함) | **현재 AI① 구현** |
| — | AI① API §5.2, 앱 r45·r46 | `errors[]` | **현재 AI① 구현** |
| — | AI① API §5.2 | `observations[]` | **현재 AI① 구현** |
| — | AI① API §5.2 | `states{}` / `mastery_by_skill{}` | **현재 AI① 구현** |
| — | AI① API §5.2 | `misconception_tags[]` | 현재 AI① 구현 |
| — | §14.1 이미지 경로 | OCR `confidence` / `bbox` / region | 미결정 |
| — | 연동 명세 §2 "확인·응답" | `analysis_status` | 미결정 — 200 body vs 422 detail |
| — | §13 | `official_skill_codes` / `official_misconception_codes` (신규 응답 행) | **미구현 제안** |

**14.3.1 STP 도출 규칙** — **목표, 협의 필요**: AI① 은 승인된 `SKL-*` 만 반환하고,
Backend 가 공식 Skill-Concept 관계에서 `STP-*` 를 조회합니다. AI① 은 concept 변환 함수를 갖지 않습니다(§13).
현재는 **방향 합의 단계**이며 계약 확정이 아닙니다.

### 14.4 오류 코드 crosswalk 상태

| AI① 내부 ID | Backend target | 출처 | 상태 |
|---|---|---|---|
| `distribution` | `SKL-1-004` | AI① API §8 "Code mapping" | 확정 — Backend 승인. adapter 존재, 호출부 미연결 |
| `2.1` | `MIS-1-004` | AI¹ API §8 (동 상) | 확정 — 동일 |
| `transposition` | `SKL-2-037` | 기능명세 "Transposition codes", AI① API §8 | 제안 — 연동 방향 합의, 미연결 |
| `3.1` | `MIS-2-001` | 기능명세 (동 상), AI¹ API §8 | 미결정 — final seed/Response mapping 확인 필요 |
| `3.2` | `MIS-2-002` | 기능명세 (동 상), AI¹ API §8 | 미결정 — **detector 범위 불일치** |
| `calculation`/`arithmetic_slip` | `null` MIS | 기능명세 "General calculation / misread", AI① API §8 | 확정 — AI¹ 정책. `MIS-2-003` 적용 조건 미합의 |
| `comprehension`/`misread_problem` | `null` / 별도 Observation | 기능명세 (동 상) | 미결정 — Backend schema pending |
| Concept `STP-*` | Backend 도출 | AI① API §8, §13 | 확정 — AI¹ 미반환 |
| (division by zero) | **`MIS-2-004` 후보** | 관계자료 (1) r28(`MIS-2-004`, `STP-2-017`, `DIVISION_BY_ZERO_ERROR`)과 AI① 핸드오프 §5에 후보로 기재. 기능명세 xlsx와 연동 명세서에는 없음 | **미결정 — Backend 신규 코드 협의 필요** |

**⚠ `3.2` detector 범위 불일치** — 기능명세 원문:
> "현재 `transposition_asymmetry` detector는 `MIS-2-002`의 넓은 정의(모든 일방 연산)와 완전히 일치한다고 보장하지 않는다.
> **일방적 곱셈/나눗셈은 별도 검증 범위다.**"

현재 AI① 은 **항 제거 후 반대편 미반영**만 `3.2` 로 봅니다. `0.5x=5 → 2x=5`(일방 곱셈)는 `calculation` 입니다.

**division-by-zero (`MIS-2-004` 후보)** — AI① taxonomy 의 `procedure/domain_violation` 계열이
관련되나 runtime 은 분류하지 않습니다. **코드 ID 를 AI① 이 만들지 않고**, Backend 신규 코드 협의 항목으로만 남깁니다.

**null 처리 (14.4.1)** — 확정: 승인 코드 없는 값은 `None`. **코드 생성 금지.**
`tests/test_backend_crosswalk.py` 가 31 skill·19 misconception 전수를 검증합니다.

### 14.5 multi-error 정책

| # | 항목 | 상태 |
|---|---|---|
| 5.1 | 대표 오류 선택 기준 | 미결정 — 후보 `errors[0]` / `first_error_step` 기준 |
| 5.2 | `errors[]` 전체 전달·저장 | 제안 — AI¹ 내부 보유, 외부 노출 미결정. `raw_result` 에 이미 저장 |
| 5.3 | 복수 MIS → Backend 단일 `misconception` | 미결정 — `null` vs 첫 항목 |
| 5.4 | 복수 Skill → Backend 단일 `skill` | 미결정 — `2.1` 은 `['distribution','polynomial_multiplication']` |
| 5.5 | `overall_confidence` 병합 | 미결정 — 최대값/가중평균/대표값 |

**현재 AI¹ 구현**: `errors[]` 다중, `suppressed_downstream_steps[]` 로 연쇄 오류 억제,
`misconception_tags[]` 정렬 집합.

### 14.6 JSON 예시

아래 6.1~6.6 은 `POST /api/v1/attempts/{id}/analyze` 의 현재 응답에서 추출한 값입니다.
6.8~6.9 는 **미구현 제안**이며 실제 응답이 아닙니다.

공통 요청 조건: `problem_skills=["linear_equation","distribution","transposition"]`, `engine="rule"`.
각 예시의 `problem_id`·`problem_latex`·`solution_text` 는 블록 안에 명시합니다.
`errors[].ref_solution_step_no` 은 `correct_solution` 을 제공한 경우에만 채워지고, 정답 풀이 미제공 시 `null` 입니다.
6.2 만 `correct_solution` 을 제공하며, 사용한 원문(`steps`·`answer_latex`) 을 그 예시에 함께 적습니다.

`mastery` 는 **학생별 Rule 엔진 스냅샷**이며 같은 학생의 누적 관측 결과다
(`knowledge.RuleStateEngine` + `_MASTERY_BY_STATE`, `tests/test_knowledge.py` 기준).
**"오답 관측 시 0.3" 은 모든 오답에 적용되는 규칙이 아니다** — `error_type` 으로 갈린다:

- 정답 1회 `0.60` (state=`learning`) → 연속 정답 2회 `0.85` (`mastered`) → 이후 정답은 `0.85` 유지
- `calculation` / `procedure` 오답 → `learning` `0.60`
- `concept_error` / `comprehension` 오답 → `needs_practice` `0.30`

같은 `student_id`(`"S1"`), `problem_skills=["linear_equation"]`, `engine="rule"` 관측 예
(각 값은 실측이며 `error_type` 을 함께 적는다):

| 관측 | error_type | state | mastery |
|---|---|---|---|
| 정답 1회 | (없음) | `learning` | 0.60 |
| 정답 2회 | (없음) | `mastered` | 0.85 |
| 정답 3회 | (없음) | `mastered` | 0.85 (고정) |
| 오답 (`2x=6` → `x=4`) | `calculation` | `learning` | 0.60 |
| 오답 (`2(x-3)=6` → `2x-3=6`) | `concept_error` | `needs_practice` | 0.30 |

→ BKT 엔진(`engine="bkt"`)에서는 값 규칙이 다릅니다.

#### 6.1 정상 풀이
입력: `problem_id="P1"`, `problem_latex="2(x-3)=6"`, `solution_text="2(x-3)=6
2x-6=6
2x=12
x=6"`
```json
{ "analysis_id":"A1#1","attempt_id":"A1","problem_id":"P1","student_id":"S1",
  "attempted_at":"2026-10-13T04:12:33.730634Z","analysis_status":"ANALYZED","analysis_count":1,
  "correct":true,"error_step":null,"first_error_step_index":null,
  "error_type":null,"error_subtype":null,
  "skill":"linear_equation","state":"learning","mastery":0.6,
  "misconception_id":null,"misconception_tags":[],
  "steps_latex":["2(x-3)=6","2x-6=6","2x=12","x=6"],
  "valid":[true,true,true,true],"unrecognized":[],"errors":[] }
```

#### 6.2 distribution 오류 (2.1)
입력: `problem_id="P1"`, `problem_latex="2(x-3)=6"`, `solution_text="2(x-3)=6
2x-3=6
2x=13
x=6.5"`
`correct_solution={solution_id:"SOL-1", steps:["2(x-3)=6","2x-6=6","2x=12","x=6"], answer_latex:"x=6"}`
— `solution_text` 가 4단계이므로 `valid` 는 4칸입니다. 정답 2단계(`2x-6=6`)와 학생 2단계(`2x-3=6`)가
대응하므로 `ref_solution_step_no=1` 입니다.
```json
{ "analysis_status":"ANALYZED","correct":false,
  "error_step":2,"first_error_step_index":2,
  "error_type":"concept_error","error_subtype":"distribution_omit",
  "skill":"distribution","state":"needs_practice","mastery":0.3,
  "misconception_id":"2.1","misconception_tags":["2.1"],"confidence":0.9,
  "steps_latex":["2(x-3)=6","2x-3=6","2x=13","x=6.5"],
  "valid":[true,false,false,true],"unrecognized":[],
  "errors":[{"error_id":"A1-E1","attempt_id":"A1","step_no":2,"ref_step_no":1,
             "ref_solution_step_no":1,"error_type":"concept_error",
             "error_subtype":"distribution_omit",
             "skill_ids":["distribution","polynomial_multiplication"],
             "misconception_ids":["2.1"],"confidence":0.9,"evidence_latex":"2x-3=6",
             "description_ko":"개념 오류 · 분배법칙 누락 (개념적 원인 추정: 분배법칙 누락)"}],
  "_be_proposed":{"skill":{"code":"SKL-1-004","confidence":0.9},
                  "misconception":{"code":"MIS-1-004","confidence":0.9},"concept":null} }
```

#### 6.3 부호 미변경 (3.1)
입력: `problem_id="P2"`, `problem_latex="3x+5=20"`, `solution_text="3x+5=20
3x=25"`
`correct_solution` **미제공** → `ref_solution_step_no: null`
```json
{ "analysis_status":"ANALYZED","correct":false,
  "error_step":2,"first_error_step_index":2,
  "error_type":"concept_error","error_subtype":"sign_not_flipped",
  "skill":"transposition","state":"needs_practice","mastery":0.3,
  "misconception_id":"3.1","misconception_tags":["3.1"],"confidence":0.9,
  "valid":[true,false],
  "errors":[{"step_no":2,"error_subtype":"sign_not_flipped",
             "skill_ids":["transposition","linear_equation"],
             "misconception_ids":["3.1"],"confidence":0.9,"evidence_latex":"3x=25",
             "description_ko":"개념 오류 · 이항 시 부호 미변경 (개념적 원인 추정: 이항 시 부호 미변경)"}],
  "_be_proposed":{"skill":{"code":null},"misconception":{"code":null},
                  "_note":"MIS-2-001 후보 — 승인 전 unmapped"} }
```

#### 6.4 양변 조작 비대칭 (3.2)
입력: `problem_id="P3"`, `problem_latex="2x+6=14"`, `solution_text="2x+6=14
2x=14"`
`correct_solution` **미제공** → `ref_solution_step_no: null`
```json
{ "analysis_status":"ANALYZED","correct":false,
  "error_step":2,"first_error_step_index":2,
  "error_type":"concept_error","error_subtype":"sign_not_flipped",
  "skill":"transposition","state":"needs_practice","mastery":0.3,
  "misconception_id":"3.2","misconception_tags":["3.2"],"confidence":0.9,
  "valid":[true,false],
  "errors":[{"step_no":2,"error_subtype":"sign_not_flipped",
             "skill_ids":["transposition","linear_equation"],
             "misconception_ids":["3.2"],"confidence":0.9,"evidence_latex":"2x=14",
             "description_ko":"개념 오류 · 이항 시 부호 미변경 (개념적 원인 추정: 등식 양변 조작 비대칭 오해)"}],
  "_be_proposed":{"misconception":{"code":null},
                  "_note":"MIS-2-002 후보 — detector 범위 확인 필요"} }
```

**⚠ 6.4 는 `error_subtype` 이 6.3 과 동일(`sign_not_flipped`)이고 `description_ko` 의 머리말도
"이항 시 부호 미변경"으로 같습니다. 원인은 taxonomy 의 `3.2.typical_error_subtypes` 가
`sign_not_flipped` 로 지정되어 있기 때문입니다(AI① 의 `data/misconceptions.json`).
**차이는 괄호 안의 "개념적 원인 추정" 절에만 나타나며, subtitle·머리말로는 구분되지 않습니다.**
Backend 가 `misconception` 단일 target 으로만 받으면 3.1 과 3.2 가 구별되지 않을 수 있습니다. → Q18.

#### 6.5 MIS 코드 없는 calculation 오류
입력: `problem_id="P4"`, `problem_latex="5x=15"`, `solution_text="5x=15
x=4"`
`correct_solution` **미제공** → `ref_solution_step_no: null`
```json
{ "analysis_status":"ANALYZED","correct":false,
  "error_step":2,"first_error_step_index":2,
  "error_type":"calculation","error_subtype":"arithmetic_slip",
  "skill":"arithmetic","state":"learning","mastery":0.6,
  "misconception_id":null,"misconception_tags":[],"confidence":0.75,
  "valid":[true,false],
  "errors":[{"step_no":2,"error_type":"calculation","error_subtype":"arithmetic_slip",
             "skill_ids":["arithmetic"],"misconception_ids":[],"confidence":0.75,
             "evidence_latex":"x=4","description_ko":"계산 오류 · 사칙연산 실수"}],
  "_be_proposed":{"misconception":null,
                  "_note":"단순 산술 실수에 MIS 미부여(AI¹ 정책). MIS-2-003 적용 조건 미합의"} }
```

#### 6.6 REVIEW_REQUIRED
입력: `problem_id="P1"`, `problem_latex="2(x-3)=6"`, `solution_text="2(x-3)=6
2x-3=6
???
x=6.5"`
`correct_solution` **미제공** → `ref_solution_step_no: null`
```json
{ "analysis_status":"REVIEW_REQUIRED","correct":false,
  "error_step":2,"first_error_step_index":2,
  "error_type":"concept_error","error_subtype":"distribution_omit",
  "misconception_id":"2.1","misconception_tags":["2.1"],"confidence":0.9,
  "state":"needs_practice","mastery":0.3,
  "steps_latex":["2(x-3)=6","2x-3=6","???","x=6.5"],
  "valid":[true,false,null,false],"unrecognized":[3],
  "_note":"step3 미인식 → 부분 판정. KnowledgeObservation 은 확정 오류만 반영. 저장됨"} }
```
> `unrecognized:[3]` 인 3번째 단계는 `valid:null` 이며 **오답으로 집계되지 않습니다**(AI① 정책).

#### 6.7 UNKNOWN (요청 오류)
HTTP **422**, Attempt·분석 저장 없음, 관측 0회.
```json
{ "detail": { "code":"E_UNRECOGNIZED",
              "message":"모든 풀이 단계를 인식하지 못했습니다. 직접 입력으로 다시 시도해 주세요.",
              "analysis_status":"UNKNOWN" } }
```
> `_be_proposed` 의 `AIAnalysisResult` 를 **반환하지 않습니다**. 최종 계약 협의 중(§14.3).

#### 6.8 OCR 이미지 요청 — **미구현 제안**
실제 응답이 아닙니다. AI¹ 에 `/ocr` 엔드포인트가 없습니다.
```json
{ "attempt_id":"A9","image_reference":"https://.../attempt/A9/page1.jpg",
  "problem":{"source_type":"captured","captured_problem_id":4821},
  "_note":"연동 명세서 §3 요청 예시 형식. captured:42"} }
```

#### 6.9 OCR 부분 인식 결과 — **미구현 제안**
실제 응답이 아닙니다.
```json
{ "ocr_result_id":"ocr-7f3a","attempt_id":"A9","status":"PARTIAL",
  "problem":{"latex_text":"2(x-3)=6","confidence":0.97},
  "regions":[{"kind":"printed","bbox":[40,20,960,120]},
             {"kind":"handwritten","bbox":[40,130,900,1400]}],
  "solution_steps":[{"step_no":1,"latex_text":"2(x-3)=6","confidence":0.96},
                    {"step_no":2,"latex_text":"2x-3=6","confidence":0.93},
                    {"step_no":3,"latex_text":"???","confidence":0.21,"unreadable":true}],
  "overall_confidence":0.70,"model_version":"<OCR 모델 버전>" }
```
> 미인식 줄은 `???` + `unreadable:true` 로 표시하며 임의 LaTeX 를 만들어내지 않습니다(추측 금지).
> `???` 와 `2.2` 처럼 임의 식을 채워 넣는 것은 금지입니다.

### 14.7 BE 설계 근거와 충돌

Backend 계약서의 필드명·구조가 **BE 원본 미확인** 인 항목이 많아, 본 대조표는 설계 근거와 충돌을 분리해 기록합니다.

| 항목 | BE 설계 근거(추정) | AI① 현재 | 충돌 |
|---|---|---|---|
| `concept` / `misconception` 단일 target | Backend 는 학생 상태를 (학생, 개념) 행으로 저장(`student_states.concept_id`) | AI① 은 `misconception_ids[]` 복수 | 복수 MIS → 단일 필드 축소 필요 |
| `skill` 단일 target | `student_states.concept_id` 기준 집계 전제 | `skill_ids[]` 복수 (`2.1` 은 2개) | 대표 선정 규칙 미정 |
| `first_error_step` 단일 | UI 는 최초 오류 1곳 표시(앱 r38) | `errors[]` 다중 + 억제 | 대표 오류 규칙 미정 |
| `is_correct` boolean | attempt 정오답 요약 | `correct` 동일 의미 | 충돌 없음 |
| `analysis` 스키마 | 최상위 `analysis` 객체로 감쌈 | 최상위 평면 + `errors[]`/`step_list[]` 중첩 | 평면 vs 중첩 |
| `overall_confidence` 단일 | 신뢰도 1개 노출 | 오류별 `confidence` 다중 | 병합 규칙 미정 |
| int ID | DB 정수 PK | AI¹ 문자열 ID | adapter 필수 |
| `STP` 역참조 | Backend seed 를 진실원(AI¹ API §8) | AI¹ 은 concept 미보유 | 충돌 없음(방향 일치) |

### 14.8 Backend 질의표 (Q1–Q24)

| # | 질문 |
|---|---|
| Q1 | AI¹ 호출 경로가 연동 §1 은 `/api/v1/attempts/{id}/analyze`, 119 문서 INTERNAL-01 은 `/api/analysis` 로 갈립니다. 어느 쪽이 정식입니까? |
| Q2 | 오류 응답 형식(`code`/`message`/`details` 대 `detail` 래퍼)과 `analysis_status` 위치(200 body, 422 detail)를 확정합니까? |
| Q3 | BE→AI¹ 서비스 인증 방식은 무엇입니까? |
| Q4 | `attempt_id`, `student_id` 의 int→문자열 변환 규칙을 정의할까요? (접두사 `t_`/`s_` 등) |
| Q5 | 시드 문제 `problem_id` 를 `problems.problem_code`(`PRB-*`)로 쓸까요? |
| Q6 | 촬영 문제 모델을 확정합니까? (`captured_problems` 신설 여부, attempt 의 problem 참조 방식) |
| Q7 | 학생 상태 키를 어떻게 맞춰합니까? (`student_states.concept_id` 대 AI① `skill_id`) |
| Q8 | `mastery` 와 `weakness`: AI¹ 값을 저장할까요, `weakness = 1 - mastery` 로 확정할까요? |
| Q9 | 대표 오류를 어떻게 정할까요? (응답 배열 유지, 저장은 대표 1개 + `raw_result`) |
| Q10 | `correct` 타입을 boolean 고정으로 할까요, nullable 로 두고 판정 불가(`null`)를 표현할까요? |
| Q11 | `REVIEW_REQUIRED` 인데 `correct=true` 일 때 관측에 반영할까요? |
| Q12 | `attempted_at` 은 서버 접수 시각입니까, 학생 풀이 시각입니까? |
| Q13 | 재분석·재촬영 정책을 정합시다. (새 `attempt_id` 사용, 회차 링크 저장 위치, 119 OCR-07/08 과의 충돌) |
| Q14 | `student_misconceptions` 에 `attempt` 또는 `analysis` FK 를 추가할까요? |
| Q15 | `hint_count`, `solve_time`, 재시도 회차를 AI¹ 입력에서 계속 제외할까요? |
| Q16 | `MIS-2-001~004`, `SKL-2-036~040`(특히 `SKL-2-037`)을 공식 seed 에 반영할까요? |
| Q17 | `MIS-2-002` 범위는 정의 축소입니까, detector 확장은입니까? |
| Q18 | `3.1`/`3.2` 구분 키를 MIS 코드 기준으로 확정합니까? (§14.6 6.4 머리말 중복 참조, 연동 §2 문구 수정 필요) |
| Q19 | `MIS-2-003` 의 성격, 일반 계산 실수 미부여 정책, Observation 구조를 확정합니까? |
| Q20 | unmapped 표현(`[]` 대 `null`)과 `misconceptions.code` 예시 `M001` 을 정정할까요? |
| Q21 | OCR 수행 주체는 AI¹입니까, 외부 벤더를 호출합니까? **잠정: AI① 이 provider 경유로 벤더 호출** (인증키는 AI¹ 환경변수, Backend 로 전달 안 함) → §14.19 |
| Q22 | `image_reference` 의 형식·만료·인증·MIME·용량·해상도 규약을 정의합니까? **잠정: HTTPS presigned URL · TTL 15분(프로젝트 권장값, 벤더 요구 아님) · png/jpeg · 10MB 이하 · 장변 1000px 이상** → §14.19 |
| Q23 | 오류 코드(415/413/422/503/504) 매핑과 타임아웃 기준을 확정합니까? |
| Q24 | `confidence` 정의(OCR 신뢰도, 병합 규칙, 임계값)를 확정합니까? **잠정: 항목별=벤더 줄 confidence, 전체=최솟값, 병합 없음, 임계값 0.85** → §14.19 |

**앱팀 확인 필요**

| # | 질문 |
|---|---|
| A1 | 앱 r48 은 `misconception_tag`(단수)인데 AI¹ 응답은 `misconception_tags[]`(복수)입니다. 앱 측 수신 필드 규약을 맞출까요? |

### 14.9 코드 구현 선행 조건

| 순서 | 구현 | 선행 조건 |
|---|---|---|
| 1 | 호출 경로 확정 (`/api/v1/attempts/{id}/analyze` 대 `/api/analysis`) | Q1 |
| 2 | 오류 응답 형식 + `analysis_status` 위치 | Q2·Q23 |
| 3 | BE→AI¹ 서비스 인증 적용 | Q3 |
| 4 | ID adapter (int → string, `problem_id` 소스) | Q4·Q5 |
| 5 | 촬영 문제 모델 연결 (`captured_problems`, attempt 참조) | Q6 |
| 6 | 학생 상태 키 매핑 (`student_states.concept_id` ↔ AI① `skill_id`) | Q7 |
| 7 | `mastery`/`weakness` 저장 정책 | Q8 |
| 8 | 대표 오류 + `raw_result` 보관 | Q9 |
| 9 | `correct` nullable + REVIEW_REQUIRED 관측 정책 | Q10·Q11 |
| 10 | `attempted_at` 의미 확정 | Q12 |
| 11 | 재분석·재촬영 회차 모델 (119 OCR-07/08 정합) | Q13·Q14 |
| 12 | `3.1`/`3.2` crosswalk 연결 (연동 §2 문구 수정) | Q16·Q17·Q18 |
| 13 | `MIS-2-003` Observation 구조 | Q19 |
| 14 | unmapped / `misconceptions.code` 정정 | Q20 |
| 15 | `POST .../ocr` | Q21·Q22·Q24 + OCR 벤더·평가셋 |
| 16 | AI¹ 입력 신호 제외 확정 (`hint_count`/`solve_time`/재시도) | Q15 |
| 17 | MySQL 영속·멀티워커 멱등 | 별도 결정 |

### 14.10 Backend 전달용 Text Analysis Adapter (PHASE 9-7)

`app/integration/backend_adapter.py` — Backend 로 넘길 값만 이 경계에서 만든다.
parser / classifier / knowledge / taxonomy 안에는 Backend 코드가 들어가지 않는다.

**잠정 계약 결정 12항목**

| # | 결정 | 상태 |
|---|---|---|
| 1 | Backend 텍스트 분석 경로는 기존 `POST /api/v1/attempts/{attempt_id}/analyze`. 별칭 endpoint 없음 | 잠정 |
| 2 | `attempt_id`·`student_id` 는 내부 문자열 처리. 정수 입력은 십진 문자열로 정규화, 접두사 없음 | 잠정 |
| 3 | `problem_id` 는 불투명 문자열 보존 (`captured:<id>`, `PRB-*`). 추측·병합 금지 | 잠정 |
| 4 | `problem_skills` 는 내부 frozen ID. Backend Skill code 는 crosswalk 에 있는 것만 역변환, 모르면 422 | 잠정 |
| 5 | 최종 Student State·weakness 는 Backend 소유. `state`/`mastery`/`observations` 는 AI¹ 로컬 결과 | 잠정 |
| 6 | `attempted_at` 은 AI¹ 첫 요청 접수 UTC. 실제 풀이 시각의 source of truth 는 Backend | 잠정 |
| 7 | 같은 `attempt_id` + 같은 요청은 기존 멱등. 재촬영은 새 `attempt_id`, 회차는 Backend 저장 | 잠정 |
| 8 | 200 `analysis_status` 는 `ANALYZED`/`REVIEW_REQUIRED`. `UNKNOWN` 은 422 `detail.analysis_status`, 미저장 | 잠정 |
| 9 | `REVIEW_REQUIRED` 는 AI² 진행 불가 상태 (아래 14.13) | 잠정 |
| 10 | 다중 오류의 대표 = 가장 낮은 `step_no` (1-based). 전부 `errors[]` 보존 | 잠정 |
| 11 | `confidence` 는 대표 오류의 값. 합산·평균·OCR 혼합 없음 | 잠정 |
| 12 | AI¹ 은 STP/Concept ID 미생성. 공식 Skill code 로 Concept 연결은 Backend 책임 | 잠정 |

이 12개는 Backend·AI² 명세가 **협의 초안**이라 잠정 표기한다. Backend 승인 전까지 확정 계약이 아니다.

### 14.11 신규 응답 필드 (additive 5개)

`AnalyzeAttemptResponse` (26 → **31**) 와 이를 상속하는 `AnalysisDetailResponse` (27 → **32**).

| 필드 | 타입 | 의미 | 미매핑 시 |
|---|---|---|---|
| `misconception_tag` | `str` 또는 `null` | 앱 r48 호환 **단수**. 대표 오류의 내부 Misconception ID | `null` |
| `official_skill_codes` | `list[str]` | Backend **승인** Skill 코드 | `[]` |
| `official_misconception_codes` | `list[str]` | Backend **승인** Misconception 코드 | `[]` |
| `provisional_skill_codes` | `list[str]` | **후보(미승인)** Skill 코드 | `[]` |
| `provisional_misconception_codes` | `list[str]` | **후보(미승인)** Misconception 코드 | `[]` |

- 기존 `skill` · `skill_ids` · `misconception_id` · `misconception_ids` · `misconception_tags` 는
  **그대로 유지**한다. taxonomy ID 를 Backend 코드로 덮어쓰지 않는다.
- 배열 순서는 **최초 등장 순**이며 중복은 제거한다.
- 코드 배열의 입력 기준은 **분석 결과의 `skill_ids`** 다. 문제 선언분(`problem.skills`)은
  요청 역변환에만 쓰고 응답 코드에는 넣지 않는다.
- `POST` · 멱등 재응답 · 상세 `GET` 는 저장된 `AttemptAnalysis` 에서 **매번 재생성**한다
  (저장하지 않는다). 세 경로의 결과는 동일하다. `GET` 은 Knowledge State 를 건드리지 않는다.

응답 예시 (`2(x-3)=6`, 학생 풀이 `2(x-3)=6 → 2x-3=6 → 2x=13 → x=6.5`, `student_id=4821`):

```json
{ "analysis_id":"A1#1","attempt_id":"A1","problem_id":"P1","student_id":"4821",
  "analysis_status":"ANALYZED","analysis_count":1,
  "correct":false,"error_step":2,"first_error_step_index":2,
  "error_type":"concept_error","error_subtype":"distribution_omit",
  "skill":"distribution","state":"needs_practice","mastery":0.30,
  "misconception_id":"2.1","confidence":0.90,
  "misconception_tag":"2.1",
  "official_skill_codes":["SKL-1-004"],
  "official_misconception_codes":["MIS-1-004"],
  "provisional_skill_codes":[],
  "provisional_misconception_codes":[],
  "misconception_tags":["2.1"] }
```

`3.2` (양변 조작 비대칭) 예시 — 내부 ID 만 남고 **Backend 코드는 비어 있다** (A2):

```json
{ "misconception_id":"3.2","misconception_tag":"3.2",
  "official_skill_codes":[], "official_misconception_codes":[],
  "provisional_skill_codes":[],
  "provisional_misconception_codes":[] }
```

> 2026-11-10 A2: `SKL-2-037`·`MIS-2-001`·`MIS-2-002` 는 공식 Seed 에 없어
> `provisional_*` 에서 제거했다. 내부 taxonomy 관측(`3.1`/`3.2`/`transposition`)과
> Knowledge State 갱신은 그대로 동작한다.

### 14.12 Backend code mapping 상태

| 내부 ID | Backend 코드 | 상태 | 근거 |
|---|---|---|---|
| `distribution` | `SKL-1-004` | **승인** | Backend 승인 확정 |
| `2.1` | `MIS-1-004` | **승인** | Backend 승인 확정 |
| `transposition` | `SKL-2-037` | **출력 안 함 (시드 밖)** | 관계자료 xlsx 전용. 공식 Seed 는 `SKL-2-035` 까지 → A2 로 제거 |
| `3.1` | `MIS-2-001` | **출력 안 함 (시드 밖)** | Seed 는 `MIS-1-001~023` → A2 로 제거 |
| `3.2` | `MIS-2-002` | **출력 안 함 (시드 밖)** | A2 로 제거. detector 는 내부 ID 로 계속 관측 |
| `linear_equation` 외 미매핑 | `[]` | unmapped | 기본 Skill 으로 치환하지 않음 |
| `MIS-2-003` | **출력 안 함** | 금지 | 일반 `arithmetic_slip` 에 자동 부여하지 않음 |
| `MIS-2-004` | **출력 안 함** | 금지 | runtime detector 없음 (협의 아이템) |
| `STP-*` | **생성 안 함** | 금지 | AI¹ 은 Concept ID 를 만들지 않음 |

**`3.2` 탐지 범위 제한 (Backend 가 넓게 읽으면 안 됨)**

`3.2` 는 **항 누락 / 일부 비대칭 연산**에서만 나온다. 이항 계열 오류 전체를 탐지하지 않는다.
현재 detector 실측:

| 풀이 | 분류 | 3.2 여부 |
|---|---|---|
| `2x+6=14 → 2x=14` | `concept_error` / `3.2` | O (항 `+6` 제거가 우변에 미반영) |
| `3x+5=20 → 3x=25` | `concept_error` / `3.1` | X (부호 미변경) |
| `2x=10 → 4x=10` | `calculation` / 없음 | X (일방 곱셈) |
| `2x=10 → 2x=5` | `calculation` / 없음 | X (일방 나눗셈) |

즉 **일방 곱셈·나눗셈 전체를 `3.2` 로 탐지한다고 주장하지 않는다.**
공식 seed 반영 시 `MIS-2-001~004` · `SKL-2-036~040`(특히 `SKL-2-037`) 범위가
현재 detector 와 맞는지 Backend 확인이 필요하다 → Q16.

### 14.13 `REVIEW_REQUIRED` 정책

`REVIEW_REQUIRED` 는 **AI² 판단을 진행하지 않는 상태**다.

- `correct=true` 가 있더라도 Backend 는 **완전한 정답 관측으로 처리하지 않는다.**
  `2(x-3)=6 → ??? → 2x=12` 은 `correct=true` + `analysis_status=REVIEW_REQUIRED` 다.
- **확정된 오류 근거만** 오답/오개념 기록 후보로 다룬다. `errors[]` 가 비어 있으면
  오개념 코드·`misconception_tag` 도 모두 비어 있다(추측으로 채우지 않는다).
- 미인식 단계가 섞였더라도 확정 오류가 있으면 그 오류의 코드는 정상 노출한다.
- `UNKNOWN` 은 여기서 나오지 않는다. `E_UNRECOGNIZED` / `E_UNVERIFIABLE` 로
  422 + `detail.analysis_status="UNKNOWN"` 을 반환하고 **Attempt·분석 이력을 저장하지 않는다.**

| 입력 | `analysis_status` | HTTP | 저장 | `misconception_tag` |
|---|---|---|---|---|
| 전 단계 판정 | `ANALYZED` | 200 | O | 오류 있으면 값 |
| 미인식 단계 포함 | `REVIEW_REQUIRED` | 200 | O | 확정 오류 있을 때만 |
| 판정 불가 | `UNKNOWN` | 422 | **X** | (응답 없음) |

### 14.14 식별자 정책 (잠정)

| 입력 | 정규화 | 비고 |
|---|---|---|
| `student_id: 4821` | `"4821"` | 십진 문자열, 접두사 없음 |
| `student_id: "4821"` | `"4821"` | 동일 취급 (같은 학생) |
| `student_id: "004821"` | `"004821"` | **선행 0 제거 안 함** — ID 병합 방지 |
| `student_id: true` | 422 | bool 은 정수 1 로 조용히 정규화되므로 사전 거절 |
| `attempt_id: "A1"` (path) | `"A1"` | 공백만 제거 |
| `problem_id: "captured:42"` | `"captured:42"` | 불투명 문자열 보존 |
| `problem_skills: ["SKL-1-004"]` | `["distribution"]` | crosswalk 역변환 |
| `problem_skills: ["SKL-9-999"]` | **422** `E_UNKNOWN_SKILL_CODE` | 기본 Skill 추측 금지 |
| `problem_skills` 누락/빈 목록 | **422** `E_SKILL_NOT_TRACKABLE` | tracking Skill 최소 1개 필수 |
| `problem_skills: ["factoring"]` | **422** `E_SKILL_NOT_TRACKABLE` | group node 단독은 관측 불가 |
| `problem_skills: ["NOT_A_SKILL"]` | **422** `E_UNKNOWN_SKILL` | taxonomy 미등록 ID |

`linear_equation` 은 승인·후보 코드 모두 없어서 Backend 가 코드로는 보낼 수 없다.
내부 ID 를 써야 하며, 코드 매핑이 필요하면 Backend 승인 항목이다 → Q16.

### 14.15 이 Phase 에 넣지 않은 것

- 인증 방식 (BE→AI¹ 서비스 인증) — 합의 전. 토큰 규약·secret 을 임의로 만들지 않는다 → Q3
- `/ocr` 경계와 provider 인터페이스는 **PHASE 9-8A 로 구현**했다. 다만 **OCR vendor · 이미지 다운로드 · multipart · 인증**은 여전히 없다 → Q21·Q22
- `/api/analysis` (Backend 119개 API v0.3) — **새 운영 경로로 추가하지 않는다** → Q1
- MySQL 영속·멀티워커 멱등, weakness 계산, Skill↔Concept 연결 — Backend 책임

> **PHASE 9-8A.1 보완** — `status` 파생이 problem/regions/solution_steps 를 모두 반영하도록 정정했고, 미인식 항목의 `text` 는 **정확히 `"???"`** 만 허용(문서·스키마 일치), `ocr_result_id` 는 `page_index` 가 있으면 **페이지별**로 만들어 중복을 막는다. **PHASE 9-8A.2 보완** — 읽을 수 있는 problem/step 은 `text`·`latex_text` 중 하나에 실제 내용이 있어야 하고(둘 다 비면 provider 계약 위반), `status` 의 readable 판정에서 **bbox 만 있는 region 은 읽힌 텍스트가 아니므로** readable content 로 세지 않는다.

### 14.17 OCR API 경계 (PHASE 9-8A)

### 14.18 OCR → 텍스트 분석 handoff 규칙 (PHASE 9-8B)

Backend 가 AI¹ OCR 응답을 **기존 Attempt 분석 요청**으로 변환해 2단계로 호출한다.
AI¹ 은 두 단계를 서로 호출하지 않는다 — `/ocr` 응답 안에 분석 요청은 없다.

**요청 구성 규칙 (Backend responsibility)**

| 분석 요청 필드 | 채우는 값 |
|---|---|
| `problem_latex` | OCR `problem.latex_text` |
| `solution_text` | OCR `solution_steps` 를 **순서대로** 줄바꿈 연결 |
| — 읽힌 step | `latex_text` 우선, 없으면 읽힌 `text` |
| — unreadable step | 정확히 `???` |
| `student_id` | **Backend 가 제공** (AI¹ 이 추측하지 않음) |
| `problem_id` | **Backend 가 제공** (AI¹ 이 추측하지 않음) |
| `problem_skills` | **Backend 가 제공** — tracking Skill 최소 1개 필요 |

**호출 금지 조건 (아무것도 채우지 않는다)**

| 조건 | 처리 |
|---|---|
| OCR 결과 `status == "UNREADABLE"` | `/analyze` 를 **호출하지 않는다** |
| OCR `problem.latex_text` 없음 | `/analyze` 를 **호출하지 않는다** |
| OCR `solution_steps` 가 **0개** | `/analyze` 를 **호출하지 않는다** — `solution_text=""` 요청을 만들지 않는다 |

임의의 문제식·Skill·학생 ID 를 만들어 채우지 않는다.

**실측 결과 (fake provider, `tests/test_ocr_analysis_handoff.py`)**

| OCR 상태 | `/analyze` 결과 |
|---|---|
| `COMPLETE` | 200 · `ANALYZED` · 오류 없음 |
| `PARTIAL` (`???` 1단계) | 200 · `REVIEW_REQUIRED` · `unrecognized=[3]` · `valid=[true,false,null]` |
| `PARTIAL` (읽힌 단계가 하나도 없음) | **422** `E_UNRECOGNIZED` · `detail.analysis_status="UNKNOWN"` · Attempt/분석 미저장 |
| `UNREADABLE` | 호출 자체를 하지 않음 · Attempt/분석 미저장 |
| `PARTIAL` + `solution_steps=[]` | 호출하지 않음 (빈 풀이 요청 금지) · Attempt/분석 미저장 |

**`???` 는 오류가 아니다** — 판정 불가 단계다. 판정 가능한 다른 오류(예: 분배법칙 `2.1`)와
혼동되지 않으며, `???` 때문에 오개념이 새로 붙지 않는다. 오류 레코드는 판정 가능한
오류만 담고, `???` 단계는 `step_list.status="unreviewable"` 로 표시된다.

**풀이 단계 0개와 전부 `???` 는 다르다** — 단계가 0개면 분석을 시도하지 않는다
(호출 금지 3번째 조건). 단계가 1개 이상이고 전부 `???` 면 **호출해** AI¹ 이
422 `E_UNRECOGNIZED` / `UNKNOWN` 을 돌려주도록 맡긴다 — Backend 가 임의로
판단하지 않는다.

**confidence 분리** — OCR `ocr_confidence`(문제·region·단계 중 최솟값)와 분석
`confidence`(분류 신뢰도, 오류가 없으면 `null`)는 **서로 다른 값**이다. 분석 응답에
`ocr_confidence` 가, OCR 응답에 `confidence` 가 섞이지 않는다.

**회귀 고정** — `tests/golden/ocr_handoff.json` 이 위 변환 규칙과 그 결과를 고정한다.
`tests/test_ocr_handoff_golden.py` 가 fixture 전 케이스를 100% 일치로 검증한다.
(변환 로직을 테스트에서 **독립 재구현**해 규칙이 어긋나면 즉시 실패한다)

**fixture 구성표 (9건)**

| case | 시나리오 | 기대 |
|---|---|---|
| `OH01` | COMPLETE · 정답 | `ANALYZED` |
| `OH02` | COMPLETE · 분배 오류(2.1) | `ANALYZED` + Backend 코드 |
| `OH03` | PARTIAL · 마지막 단계 `???` | `REVIEW_REQUIRED` · 판정 가능 오류와 혼동 없음 |
| `OH04` | UNREADABLE | **분석 요청 skip** |
| `OH05` | 검증용 problem LaTeX 없음 | **분석 요청 skip** |
| `OH06` | COMPLETE · 독립 오류 3건 | 대표 오류 = 가장 낮은 `step_no` |
| `OH07` | 단계 1개 전부 `???` | 분석 호출 → **422 UNKNOWN** |
| `OH08` | `text` ≠ `latex_text` | `latex_text` 우선 · 없으면 `text` fallback |
| `OH09` | `solution_steps=[]` (PARTIAL) | **분석 요청 skip** |

> **주의 — `ocr_handoff.json` 의 gate 는 회귀 fixture 기준이다.**
> `gate: {step_accuracy: 1.0, error_type_accuracy: 1.0}` 은 **fake provider 의 결정적 입력**에
> 대한 **계약 회귀 기준**이며 **실제 OCR 모델의 측정값이 아니다.**
> 실제 OCR 품질은 별도 평가 세트로 측정한다 → `docs/AI1_OCR_연동_결정안.md` §4

**미확정 — 만들어내지 않는다**

- OCR 결과와 Attempt/분석을 잇는 **연결 ID·FK** 는 아직 정해지지 않았다.
  `ocr_result_id` 는 provider 규칙으로 만든 식별자일 뿐 DB FK 가 아니다.
- `page_index` 가 여러 장일 때의 **페이지 병합·순서 규칙**도 미정이다.
  (지금은 페이지별 ID 만 만들어진다)


`POST /api/v1/attempts/{attempt_id}/ocr` — Backend 가 저장한 이미지 reference를 받는다.
`AI1_OCR_PROVIDER=google_vision` 설정이 있을 때만 Cloud Vision provider를 선택하며,
설정이 없으면 결과를 꾸며내지 않고 **503 `E_OCR_NOT_CONFIGURED`** 를 돌려준다.

**책임 경계**

| 항목 | AI¹ 의 역할 |
|---|---|
| 이미지 원본 저장 | **Backend 책임** (AI¹ 은 저장하지 않는다) |
| 이미지 전달 | Backend 가 준 `image_reference` (문자열). **사진 bytes multipart 업로드는 받지 않는다** |
| OCR 수행 | Google Cloud Vision `DOCUMENT_TEXT_DETECTION` (명시 환경 설정 필요) |
| 응답 image reference | 없음 — `image_reference` 필드 자체를 두지 않는다 (입력 URL/서명/서명 필드 모두 비노출) |
| Knowledge State 갱신 | 하지 않는다 (분석은 `/analyze` 경로의 몫) |
| AI② 호출 | 하지 않는다 |

**요청**

```json
POST /api/v1/attempts/A1/ocr
{ "image_reference": "https://backend.example/attempts/A1/page1.jpg", "page_index": 0 }
```

**raw-text 응답** (OCR confidence — 수학 분석 `confidence` 와 별개 값)

```json
{ "ocr_result_id":"A1#ocr-p0","attempt_id":"A1",
  "status":"PARTIAL","representation":"raw_text",
  "model_version":"Google Cloud Vision DOCUMENT_TEXT_DETECTION (managed model)",
  "ocr_confidence":0.89,"problem":null,"regions":[],"solution_steps":[],
  "raw_text":"다음 식을 풀어라\\n2(x-3)=6\\n2x-6=6",
  "raw_lines":[{"line_index":0,"page_index":0,"text":"다음 식을 풀어라","confidence":0.95,
                "bbox":{"x":20.0,"y":20.0,"width":136.0,"height":24.0},
                "bounding_polygon":[[20,20],[156,20],[156,44],[20,44]],"derived":true},
               {"line_index":1,"page_index":0,"text":"2(x-3)=6","confidence":0.91,
                "bbox":{"x":20.0,"y":70.0,"width":96.0,"height":24.0},
                "bounding_polygon":[[20,70],[116,70],[116,94],[20,94]],"derived":true},
               {"line_index":2,"page_index":0,"text":"2x-6=6","confidence":0.89,
                "bbox":{"x":20.0,"y":120.0,"width":72.0,"height":24.0},
                "bounding_polygon":[[20,120],[92,120],[92,144],[20,144]],"derived":true}],
  "page_index":0,"warnings":[] }
```

**계약 규칙**

| 규칙 | 내용 |
|---|---|
| `status` | **경계가 내용 전체에서 파생**한다 — `COMPLETE`(읽힌 problem + 읽힌 step 1개 이상 + unreadable 없음) / `PARTIAL`(읽을 수 있는 내용이 있지만 problem·풀이가 없거나 unreadable 있음) / `UNREADABLE`(읽을 수 있는 problem·step·**텍스트가 있는 region** 이 하나도 없음) |
| `representation=raw_text` | Vision의 전체 text와 word geometry에서 파생한 줄 preview만 제공한다. 텍스트가 있으면 `PARTIAL`, 없으면 `UNREADABLE`. `problem=null`, `regions=[]`, `solution_steps=[]`; 문제/풀이·인쇄/필기·LaTeX를 추정하지 않는다 |
| `image_reference` | **응답에 필드가 없다** — 입력의 URL/서명과 fingerprint를 포함해 이미지 참조를 어떤 형태로도 응답에 되돌려 보내지 않는다. 참조는 provider 호출 입력으로만 쓴다 |
| `ocr_result_id` | provider 가 준 값을 쓰고, 없으면 `{attempt_id}#ocr`(단일 페이지) 또는 `{attempt_id}#ocr-p{page_index}`(페이지별 — 같은 attempt 안에서 중복되지 않는다) |
| `ocr_confidence` | structured 결과는 problem·region·단계 중 **최솟값**; raw-text 결과는 line confidence 중 **최솟값** (없으면 null) |
| `bbox` | `x`/`y`/`width`/`height` — 음수 불가. 좌표계는 provider 가 정한다 |
| `confidence` (항목별) | 0.0 ~ 1.0. 범위 밖은 502 (provider 계약 위반) |
| 미인식 항목 | `unreadable: true` + `text: "???"`(표식은 **정확히** `???` — None/빈 문자열 불가) + **`latex_text: null`** |
| readable 내용 | unreadable=false 인 `problem`/`step` 은 `text` 또는 `latex_text` 중 하나에 **비어 있지 않은 내용**이 있어야 한다. 둘 다 없거나 공백뿐이면 **provider 계약 위반**(502) |
| bbox-only region | `OcrRegion` 은 bbox 만 있어도 유효하다. 다만 **읽힌 텍스트가 아니므로** readable content 로 세지 않는다(→ UNREADABLE 로 판정될 수 있다) |
| **복원·조작 금지** | unreadable 항목에 `latex_text` 를 넣거나 `text` 를 `???` 가 아닌 값으로 채우면 **스키마가 거부**한다 (응답 단계에서 재검증한다) |
| 단계 순서 | `solution_steps` 는 위에서 아래(번호 증가) 순서. 어긋나면 502 |
| confidence 분리 | OCR 응답에는 분류 신뢰도인 `confidence` 가 **없다**. `ocr_confidence` 만 있다 |

**오류 계약**

| 상황 | HTTP | `detail.code` |
|---|---|---|
| provider 미설정 | 503 | `E_OCR_NOT_CONFIGURED` |
| Vision API 비활성화 | 503 | `E_OCR_API_DISABLED` |
| Google credential/IAM 오류 | 503 | `E_OCR_AUTH_FAILED` |
| billing 비활성화 | 503 | `E_OCR_BILLING_REQUIRED` |
| Vision free quota/provider quota 소진 | **429** | `E_OCR_USAGE_LIMIT_REACHED` (PHASE 9-9C.8 — 협의서 §4.6.3) |
| 공유 로컬 40회 상한 도달 | **429** | `E_OCR_USAGE_LIMIT_REACHED` (PHASE 9-9C.8) |
| 이미지 링크 다운로드 실패(만료·404) | **422** | `E_OCR_IMAGE_FETCH_FAILED` (PHASE 9-9C.8 신설) |
| 지원하지 않는 이미지(비-HTTPS `gs://` 등)·형식/MIME | **422** | `E_OCR_UNSUPPORTED_IMAGE` (PHASE 9-9C.8) |
| provider 호출 실패 | 502 | `E_OCR_PROVIDER_ERROR` |
| provider 계약 위반(범위 초과·순서 위반) | 502 | `E_OCR_PROVIDER_ERROR` |
| 요청 오류(빈 `image_reference`·알 수 없는 필드) | 422 | (pydantic) |


### 14.19 OCR provider 선정 · 잠정 결정 (PHASE 9-9A)

조사·결정 문서: **`docs/AI1_OCR_연동_결정안.md`** (2026-10-22 기준, 가격·한도 포함)

**당시 비교 권장: Mathpix OCR API (`/v3/text`)**. 이후 사용자는 비용을 통제할 수 있는
Google Vision free-tier raw-preview 경로를 선택했다(현재 앱 구현은 §14.20). 이 문서의
Mathpix/Gemini/Azure 비교는 평가 참고이며, Mathpix runtime 연동이 완료됐다는 뜻이 아니다.

| 이유 | 내용 |
|---|---|
| 계약 변환이 가장 얇다 | `lines_json.region` 이 우리 `OcrBBox` 와 형태 동일(필드명만 다름) · 줄별 `is_printed`/`is_handwritten` 가 `kind` 에 · 줄별 `confidence` 가 항목별 confidence 에 · `version` 이 `model_version` 에 대응 |
| 미인식 신호가 있다 | `conversion_output:false` + `error_id`(`math_confidence` 등)가 우리 `unreadable` 대응 신호 |
| 요청 옵션 | `include_line_data=true`(줄별 confidence·인쇄/필기) + `include_lines_json=true`(영역별 축 정렬 bbox) + `confidence_threshold` + `formats=["latex_styled"]` |
| **인증 두 축** | `app_id`/`app_key` = **Mathpix API 인증**(AI¹→벤더). Backend **presigned URL** = **이미지 접근 권한**(벤더→Backend 스토리지). 별개 축 |
| 비용 (공식 2026-10-22) | **이미지 $0.002/장** · `v3/pdf` $0.005/페이지 · **첫 API key 활성화 $19.99 일회성** · **텍스트 12줄 초과 이미지는 `v3/pdf` 요금($0.005) 적용 가능**(2.5배 — 우리 사진은 12줄을 넘기기 쉬워 단가를 $0.002 로 고정하지 않는다) |
| **데이터 보존** | **이미지 엔드포인트**(`/v3/text` 등) — `improve_mathpix` 기본 `true` 면 **입력 이미지 최대 90일** + **결과 캐시 최대 30일**(`/v3/ocr-results` 조회 가능). `false` 면 **미보존·미캐시·비조회**(결과는 그 응답 안에만). `/v3/pdf` 는 별도 — 원본·페이지 이미지 최대 30일, 텍스트 출력 최대 90일 → [Data Retention](https://docs.mathpix.com/concepts/data-retention) |
| **개인정보 opt-out** | **요청 `metadata.improve_mathpix=false`** = 품질 개선 사용 금지 + OCR 데이터 보존 금지. **단 account-level 고정값이 요청 설정을 덮어쓸 수 있고, 요청·응답으로 그 고정 여부를 확인할 방법이 없다** → **콘솔 또는 Mathpix 지원팀**으로 확인. **청구·감사용 요청 메타데이터는 opt-out 여부와 무관하게 보존** → [Privacy](https://docs.mathpix.com/concepts/privacy) · M13 |
| 한국어 | **인쇄 한국어(Hangeul)는 공식 지원 목록에 있음**. **필기는 영어·힌디어·라틴만 지원** → 필기 수식(라틴 기호·숫자)은 범위 안, **한국어 문제집 이미지 정확도는 미측정** |
| 응답이 빠르다는 **벤더 안내**가 있다 | Mathpix 제품 페이지의 "median image response < 0.5s" (**마케팅 수치·계약 보장 아님**). 실제 p50/p95 는 §4.2-C 에서 **우리가 직접 측정**한다 |

> **비교·권장 표에 있는 수치/평가는 우리 도메인의 OCR 정확도가 아니다.**
> "전문화·최고" 류는 **벤더 자기평가**, "정확도 우위" 류는 **타사 벤치마크(영어 수학 교과서 코퍼스)** 다. **우리 이미지의 정확도는 §4 세트로 직접 측정**해야 한다.
> 아래 권장은 **잠정 권장**이며 확정 계약이 아니다.

**확정하지 않은 이유 2가지**

1. **한국어 문제문 품질 미확인** — Mathpix 공식 목록에 **인쇄 한국어(Hangeul)는 있으나** **필기는 영어·힌디어·라틴만 지원**한다. 우리 사진은 **인쇄 문제(한국어) + 수식 필기(라틴 기호·숫자)** 조합이라 필기 쪽은 범위 안이지만, **한국어 문제집 이미지 전체 정확도는 미측정**이다.
   → §4 평가에서 한글 줄 정확률을 **1순위 측정 항목**으로 둔다. 결과가 나쁘면
   §3.3 하이브리드로 전환한다.
   한글 인쇄 문제 + 수식 풀이 혼합이므로 한글 줄 정확률을 1순위 측정 항목으로 둔다.
   결과가 나쁘면 **수식=Mathpix + 한글=범용 엔진** 하이브리드를 검토한다.
2. **의미 오류 사례** — `5` 를 `\overline{0}` 로 읽은 사례는 AI① 판정을 직접 오염시킨다.
   → 기호 혼동 회귀율(≤1%)을 별도 측정한다.

**`OcrResult` 계약과의 불일치 (구현 시 풀어야 할 지점)**

| # | 불일치 | 상태 |
|---|---|---|
| M1 | Mathpix Markdown(MMD) → `text`/`latex_text` 분리 파싱 필요 | 미결 |
| M2 | 벤더에 "step" 개념 없음 — 줄→단계 파생 규칙 필요 | 미결 |
| M3 | provider 1개 가정 — 한글 품질 미달 시 2-provider 하이브리드 | 미결 |
| M4 | `confidence` 2종(`confidence`/`confidence_rate`) → 1종 채택 | 잠정 |
| M5 | `kind` 판별 3값 가능(둘 다 false) — 기본값 | 미결 |
| M6 | 미인식 `"???"` 표식이 벤더에 없음 — 우리가 합성 | 규칙 확정 필요 |
| M7 | 다중 페이지 병합·순서 규칙 | 미결 |
| M8 | HTTP 429(rate limit) 가 현 오류 계약에 없음 | 미결 |
| M9·M10·M11 | bbox 필드명 · `model_version` · `status` 파생 | **해결** |
| M12 | **`confidence` 누락/누락 허용** — 우리 스키마는 필수 float(0~1) 이나 벤더는 미확인 값을 **누락 또는 `null`** 로 준다(누락은 "0 이 아님"을 뜻한다). **아직 결정하지 않았다** — 샌드박스 응답에서 실제 누락 빈도·맥락을 보기 전에는 누락값을 `0.0` 으로 확정하거나 어떤 값으로 코드화하지 않는다 | **미결** |
| M13 | **이미지 보존 기간 / `improve_mathpix` 계약** — 우리 계약에 개념 없음. 이미지 엔드포인트는 기본 `true` 면 입력 이미지 최대 90일 + 결과 캐시 최대 30일, `false` 면 미보존·미캐시·비조회. **account-level 고정값이 요청 설정을 덮어쓸 수 있어** 유효 설정은 **콘솔 또는 지원팀으로만 확인** (요청·응답으로 확인 불가). **청구·감사용 메타데이터는 opt-out 여부와 무관하게 보존** | **미결** |
| M14 | **`OcrRegion` 에 id 필드가 없다** — `OcrStep.region_id` 는 있는데 region 에 식별자가 없어 step→region 연결이 닫히지 않는다 | region id 추가 여부. **미결 동안 하네스는 `region_id` 를 null 로 두고** provider line id 를 초안에 보존한다 | **미결** |
| M15 | **과금 대상 텍스트 행 정의 미확인** — 벤더 요금 문서의 "line" 정의와 우리 type 매핑 불일치 가능 | 요금 문서 확인 후 확정 | **미결** |
| M16 | **라벨 키의 반복 호출 안정성 미확인** — provider 응답 id 는 라벨 키로 쓸 수 없고, 원본 줄 인덱스도 반복 호출 안정성 확인이 필요 | 동일 이미지 반복 호출로 실측 | **미결** |

**개인정보 opt-out 계약 (`metadata.improve_mathpix`)** — [Privacy](https://docs.mathpix.com/concepts/privacy) · [Data Retention](https://docs.mathpix.com/concepts/data-retention)

| 대상 | 동작 |
|---|---|
| `metadata.improve_mathpix=false` (요청) | **데이터 품질 개선 사용 금지** + **OCR 데이터 보존 금지**(미보존·미캐시·`/v3/ocr-results` 비조회). 결과는 그 API 응답 안에만 있음 |
| **account-level 고정값** (app key) | Mathpix 가 enterprise 계약 등에서 고정 가능. **고정값이 있으면 요청 설정을 덮어쓴다**(모든 엔드포인트) |
| 확인 방법 | **요청 설정으로는 고정 여부를 확인할 수 없고, OCR 응답에도 유효 설정의 근거가 없다** → **콘솔 또는 Mathpix 지원팀(`support@mathpix.com`)** 으로만 확인 |
| 보존되는 것 | **청구·감사용 요청 메타데이터**(status·timestamp·page count·error code)는 **opt-out 여부와 무관하게 보존** |
| 우리 구현 | `eval/mathpix_client.py` 는 **기본 요청에 항상 `improve_mathpix=false`** 를 보낸다(플래그 생략 경로 없음) |



**추가할 오류 코드 (구현 시, 이번엔 추가 안 함)**

| 코드 | HTTP | 상황 |
|---|---|---|
| `E_OCR_RATE_LIMITED` | 429 | 벤더 rate limit |
| `E_OCR_IMAGE_INVALID` | 422 | MIME·용량 사전 검증 실패 |

**이번 Phase 에 한 것**: 문서·조사만. **코드·의존성·API key/secret 은 변경하지 않았다.**

**하네스 테스트 219건**(`test_harness.py` 54 + `test_run_batch.py` 38 + `test_aggregation.py` 16 + `test_coverage.py` 13 + `test_result_contract.py` 14 + `test_state_types.py` 41) · `test_google_vision.py` 43건

> **배치 러너**(`eval/run_batch.py`) — 기본 **dry-run**. 외부 호출은 `--run` 과 `--max-calls` 를 **둘 다** 명시해야만 가능하다. presigned URL manifest 는 **실행 직전에 준비하는 로컬 평가 자료**이며 **발급은 이 도구가 하지 않는다.** presigned URL 은 만료되는 bearer 정보로 취급해 **stdout·로그·`results.jsonl`·리포트 어디에도 기록하지 않는다.** **산출물은 실행마다 `eval/_local/runs/<run_id>/` 아래로 격리**되어 기존 결과를 **덮어쓰지 않는다.** 반복 측정은 `--accumulate` 로 누적하되 그룹은 **`(case_id, sample_id)` 기준**이라 **다른 이미지를 반복 측정으로 비교하지 않는다.** 누적은 **같은 파일·같은 `run_id`·혼합/누락 `run_id`·`attempt_id` 중복**을 거부한다. **M16 비교는 성공 호출만** 하며 성공이 2회 미만이면 `null` 이다. **per-sample 정확도는 첫 번째 성공 결과**, **latency·비용은 모든 호출 시도** 기준이다. **coverage**(시도 sample/전체 label)와 **시도된 sample 기준 실패율**을 보고하고 **provider 실패율**과 **adapter 실패율**을 분리한다. **provider 성공 + adapter 실패의 비용은 0 이 아니라 unknown** 이다. **명시 상태 필드(`provider_succeeded`·`adapter_succeeded`·`error_stage`)가 `draft`/`error` 와 모순되면 입력 계약 오류로 중단**하며 조용히 재계산하지 않는다. **label id 와 일치하지 않는 결과 행은 집계에서 제외**하고 건수를 보고한다. **실패율의 분자·분모는 모두 같은 행 집합**(label id 일치) 기준이다. **상태 필드는 JSON 타입으로 검증**한다 — `provider_succeeded`·`adapter_succeeded` 는 **JSON boolean 만**, `error_stage` 는 **null 또는 "provider"/"adapter"** 문자열만 허용한다.
**라벨 키 규칙** — 정답 라벨의 `line_kinds`·`region_bboxes` 키는 **원본 줄 인덱스(문자열)** 다. provider 응답 id 는 반복 호출 안정성이 확인되지 않아 **라벨 키로 쓰지 않는다**(M16). 원본 줄 인덱스조차 반복 호출 안정성은 실측 확인이 필요하다 — `D_label_key_stability` 로 보고한다 (`true`/`false`/`null`=미확인).

**선행 조건 미충족** — Mathpix 계정·API key, 예산 승인, Improve Mathpix opt-out 확인, E01~E16 실제 사진 20장 이상 + 정답 라벨, Backend presigned URL 의 **5개가 모두 미충족**이다. opt-out 항목은 **요청 설정이 아니라 account-level 고정값을 콘솔/지원팀으로 확인**해야 한다. 따라서 이번에 **정확률·지연·비용을 측정하지 않았고** M1~M8·M12·M13·Q21·Q22·Q24 를 **확정하지 않았다** → `docs/AI1_OCR_연동_결정안.md` §7-1

**provider 확정 게이트** — Mathpix 를 **최종 provider 로 확정하지 않는다.**
Mathpix key 와 예산 승인이 준비된 뒤 E01~E16 평가 이미지를 준비하고, 실제 샌드박스 응답으로
**MMD / line_data / lines_json 변환, 누락 confidence, 한글 인쇄 인식률, timeout·비용** 을 측정한다.
측정 결과 전까지 권장은 **잠정** 이다. → `docs/AI1_OCR_연동_결정안.md` §8


### 14.20 Google Cloud Vision 앱 provider (PHASE 9-9C.4)

앱 `/ocr`의 Google Cloud Vision provider는 **명시적 opt-in**으로만 활성화한다.

| 설정/계약 | 내용 |
|---|---|
| Provider 선택 | `AI1_OCR_PROVIDER=google_vision`; 미설정 시 provider 없음(503), 자동 선택 금지 |
| Credential | `GOOGLE_APPLICATION_CREDENTIALS`가 외부 서비스 계정 JSON을 가리킨다. JSON은 저장소 밖에 두고 코드/테스트/로그에서 읽거나 출력하지 않는다 |
| SDK/dependency | `google-cloud-vision>=3.10,<4` (`requirements.txt`); OCR eval probe는 표준 라이브러리 별도 client를 계속 사용 |
| Vision feature | `DOCUMENT_TEXT_DETECTION` 한 기능만 사용. SDK retry는 `None`으로 끄고 Backend가 재시도 정책을 소유한다 |
| Timeout | upstream 8초 기본값(`GOOGLE_VISION_TIMEOUT_SECONDS`). **설정 상한 25초** — 초과 설정과 잘못된 값, **NaN/무한대**는 503 `E_OCR_NOT_CONFIGURED`로 fail-closed (협의서 §3.2 #7: Backend가 OCR 전체에 30초 대기) |
| 결과 | `representation=raw_text`; `full_text`, 줄별 text/좌표/confidence. 문제/풀이·인쇄/필기·LaTeX는 추정하지 않는다. 텍스트가 있으면 PARTIAL, 없으면 UNREADABLE |
| Signed URL 보호 | 요청 URL은 provider에만 전달한다. 응답에 이미지 참조 필드는 두지 않는다 — URL/서명/쿼리뿐 아니라 fingerprint도 되돌려 보내지 않는다. 오류 메시지에서도 URL/쿼리를 redaction한다 |
| URL 입력 검증 | **HTTPS 서명 URL만** 허용(`gs://` 포함 타 스킴 거부, 협의서 §3.3 #10). 허용 호스트는 `AI1_OCR_ALLOWED_HOSTS`(쉼표 구분, 소문자 정규화, **스킴/경로/포트 없는 bare hostname만**) — 미설정이면 HTTPS만 검사, 설정 시 목록 내 host만. 스킴·경로·포트가 붙은 항목은 hostname exact match와 어긋나므로 provider 기동 시 503 `E_OCR_NOT_CONFIGURED`로 fail-closed한다(파싱 규칙은 `app/integration/host_policy.py` 단일 소스, preflight와 공유). 비HTTPS/미허용 host는 budget 예약·Vision 호출 **이전에** 422 `E_OCR_UNSUPPORTED_IMAGE` |
| 재시도·timeout | AI¹ 은 자동 재시도하지 않는다 — 요청당 provider 호출 1회(fake 테스트 고정), SDK retry `None`, 실패 시에도 1회다. 재시도는 Backend의 소유다 (협의서 §4.6.1: 어떤 오류도 재시도 없음) |
| 호출 가드 | 앱 provider와 eval probe가 `eval/_local/google_vision/call_budget.jsonl` 및 같은 lock을 공유한다. 누적 40회는 프로젝트 로컬 제한일 뿐 billing account 전체 quota가 아니다 |
| 오류 | 협의서 §4.6.3 채택: `E_OCR_API_DISABLED`·`E_OCR_AUTH_FAILED`·`E_OCR_BILLING_REQUIRED`·`E_OCR_NOT_CONFIGURED`는 503, `E_OCR_USAGE_LIMIT_REACHED`(quota/local cap)는 **429**, `E_OCR_IMAGE_FETCH_FAILED`(링크 다운로드 실패)·`E_OCR_UNSUPPORTED_IMAGE`(형식·비-HTTPS)는 **422**, 그 외 provider 실패는 502 `E_OCR_PROVIDER_ERROR` |
| 비용 상태 | 1,000 units/month/feature/billing-account 무료 범위와 실제 잔량은 별개다. billing/API 설정 및 인증된 OCR 호출은 없었다. 초기 SDK 테스트에서 fake URL+AnonymousCredentials 요청 2회가 endpoint에 도달했으나 403으로 거절됐고 OCR 처리 성공은 없었다. quota 확인·사용자 승인 전 실제 호출 금지 |
| 평가 상태 | 현재 테스트는 fake Vision client/response와 network-free SDK message/signature 검사만 사용한다. 한글·수식의 실제 인식 품질은 아직 측정하지 않았다 |

Mathpix/OCR vendor 비교(§14.19)는 조사 자료이며, runtime provider 구현과 같지 않다. 앱 응답에는 raw-text additive 필드만 있고 이미지 참조 필드는 없다(PHASE 9-9C.7 — URL·서명·fingerprint 전부 비노출). raw-text 의미·PARTIAL/UNREADABLE·`problem=null`·`ocr_result_id` 파생 등 기존 계약은 보존한다.


### 14.21 `/ocr` 내부 인증 (PHASE 9-9C.6 미결 → PHASE 9-9C.8 구현)

**PHASE 9-9C.8 해소**: Backend-AI① 연동협의서(2026-11-03) §3.1이 전부 결정을 내렸고, 그 기준 그대로 구현했다(`app/integration/internal_auth.py`). 구현 계약:

| 항목 | 구현 계약 (협의서 §3.1) |
|---|---|
| 헤더 | `X-Internal-Api-Key` |
| 키 환경변수 | `AI1_INTERNAL_API_KEYS` (쉼표 구분 다중 키; 각 항목 앞뒤 공백 제거, 빈 항목 무시). 매 요청 재조회라 재시작 없이 키 교체 반영 |
| 누락/불일치 | 401 `{"detail":{"code":"E_AUTH_INVALID_KEY","message":"..."}}` — 403은 쓰지 않는다 |
| 키 미설정/빈 설정 | fail-closed 503 `{"detail":{"code":"E_AUTH_NOT_CONFIGURED","message":"..."}}` — `E_OCR_NOT_CONFIGURED`와 코드로 구분 |
| 비교 | `secrets.compare_digest` 상수 시간, 전체 키에 대해 비교(첫 일치에서 조기 탈출하지 않음) |
| 적용 범위 | `/health`만 예외. `/analyze-solution`(legacy) 포함 7개 라우트 전부 |
| 선행성 | FastAPI dependency 로 부착 — endpoint 본문보다 먼저 실행돼 provider 초기화·호출·budget ledger 예약보다 인증이 선행한다(테스트로 고정) |
| 비노출 | 키 값·헤더 값·credential 경로는 응답·오류 메시지에 없다(변수 이름만 언급) |
| 문서 라우트 | `/docs`·`/redoc`·`/openapi.json`은 인증 guard 밖의 FastAPI 기본 라우트라 **기본 404**(`docs_url/redoc_url/openapi_url=None`). 로컬 개발만 `AI1_ENABLE_DOCS=1`로 opt-in |
| 테스트 | `tests/test_internal_auth.py`(신규) — 기존 API 테스트는 `tests/conftest.py` autouse fixture로 guard를 우회해 무수정 통과 |

이하 PHASE 9-9C.6 당시의 미결 기록(역사 보존).

`/api/v1/attempts/{attempt_id}/ocr` 의 Backend→AI¹ 서비스 간 인증 계약은 **현재 어디에도 문서화돼 있지 않아 guard 를 구현하지 않았다.** 헤더 이름·키 환경변수 이름·미인증 상태/오류 코드를 임의로 만들지 않는 것이 이 Phase 의 제약이다.

| 확인 사항 | 내용 |
|---|---|
| 헤더 이름 | **없음**. `X-Internal-Api-Key` 라는 이름은 저장소 문서·Backend↔AI① 계약 DOCX·협의 문서 어디에도 등장하지 않는다 |
| 서버 간 인증 안건 | Backend 협의안건 2-9 "내부 API Key 헤더. 키는 양쪽 `.env`에 보관" — **Backend 제안(미확정)**, 환경변수 이름 미기재 |
| AI¹ 측 회신 | 회신제안 P0 "TLS + 내부망/API Gateway 제한 + secret manager 기반 credential" — **제안(미확정)**, key 발급·회전 주체 P0 미결 |
| 현재 코드 | app 에 헤더·401/403·인증 검증이 **전무**하다. 미인증 요청은 `require_ocr_provider()` 와 provider 초기화·budget 예약 검사까지 도달하며 provider 미설정일 때만 503 `E_OCR_NOT_CONFIGURED` 로 막힌다 |
| 기존 기록 | 대조표 내 "인증 방식 미확정. 인증 토큰 형식·secret 공유 방식도 미확정"(Q3)과 일치 — 이 Phase 로도 상태는 변하지 않았다 |

**guard 미추가 판단**: 상수(헤더 이름·env 이름·상태 코드·fail-closed 동작)를 임의 결정하면 추측 계약이 코드로 굳고, Backend 결정과 어긋날 때 기존 503 `E_OCR_NOT_CONFIGURED` 계약까지 영향받는다. 계약 확정 후의 적용 규칙만 미리 고정한다: 인증 검증은 `require_ocr_provider()`/provider 초기화 **이전에**, constant-time 비교, 키·헤더 값·credential 경로를 로그·오류에 노출하지 않음, 인증 실패 시 budget ledger(`eval/_local/google_vision/call_budget.jsonl`) 예약 기록 없음.

**Backend 확인 질문 (구현 재개 조건)**:
1. 헤더 이름 — `X-Internal-Api-Key` 인가, 다른 이름인가? (문서에 존재하지 않는 이름이므로 결정 필요)
2. 키 환경변수 이름 — 양쪽 `.env` 에 어떤 이름으로 두는가?
3. 키 미설정·불일치 각각의 HTTP 상태와 오류 코드 — 401/403 구분, 신설 `E_` 코드인지 기존 taxonomy 재사용인지
4. 키 미설정(fail-closed) 시 provider 미설정 503 `E_OCR_NOT_CONFIGURED` 와의 상태 구분
5. 인증 실패 응답 envelope — `AIErrorResponse{request_id, success, error{code,message}}` 형식 승계 여부
6. secret 발급·회전 주체(기존 P0 항목의 재확정)
7. 적용 범위 — `/ocr` 만 먼저인지 `/analyze`·`/analyses*` 포함인지, legacy `/analyze-solution`·`/health` 제외 여부


### 14.22 OCR 응답 필드 정리 (PHASE 9-9C.7)

이미 합의된 Backend 연동 기준만 반영했다. 인증 계약·미확정 재시도 정책은 추측해 구현하지 않는다.

| 항목 | 현재 계약 |
|---|---|
| `image_reference` (응답) | **제거** — 응답에 이미지 참조 필드를 두지 않는다(원본 URL/서명과 hash fingerprint 전부). 요청 입력 필드는 유지되며 provider 호출에만 전달된다 |
| `image_reference` (입력) | 유지 — Backend 가 준 참조를 그대로 Vision 호출에 넘긴다 (협의서 §3.3 #10: **HTTPS 서명 URL만**, `gs://` 거부. 허용 호스트는 `AI1_OCR_ALLOWED_HOSTS` 환경변수 — §14.20) |
| 보존된 기존 계약 | `representation=raw_text`, PARTIAL/UNREADABLE 내용 파생, `problem=null`·`regions=[]`·`solution_steps=[]`, `raw_text`/`raw_lines`, unreadable `"???"` 표식, `ocr_result_id` 파생 — 전부 불변 |
| 재시도 | AI¹ 자동 재시도 **없음** — 요청당 provider 호출 정확히 1회, SDK retry `None`, 실패 시에도 1회다. 재시도는 Backend 소유 (협의서 §4.6.1: 어떤 OCR 오류도 재시도 없음 — 해소) |
| timeout | 기본 8초(`GOOGLE_VISION_TIMEOUT_SECONDS`)가 실제 SDK 호출에 전달된다. **설정 상한 25초**(초과 → 503 `E_OCR_NOT_CONFIGURED` fail-closed). Backend OCR 동기 대기는 30초(협의서 §3.2 #7 — 이전 10초 기록 대체). 전체 응답 28초 이내 목표의 실측은 Vision 실호출 전까지 없다 |
| **미결 1 (해소)** | timeout 상한은 협의서 §3.2 #7로 결정 — 25초 상한 구현+테스트 완료 |
| **미결 2 (해소)** | 503/4xx 계열의 Backend 재시도 여부는 협의서 §4.6.1로 확정 — 어떤 오류도 재시도 없음, AI¹ 재시도는 전무 |


### 14.23 웹호출 계약 정합성 (PHASE 9-9C.8 — 협의서 2026-11-03 기준)

Backend-AI① 연동협의서(최종) §3~§5의 확정 결정을 코드·테스트에 반영했다.

| 협의서 결정 | 반영 내용 |
|---|---|
| §3.1 인증 | guard 구현 — §14.21 상단 표 참조. `tests/test_internal_auth.py` 26건 |
| §3.2 #7 timeout | `GOOGLE_VISION_TIMEOUT_SECONDS` 기본 8초, **설정 상한 25초**(초과 → 503 `E_OCR_NOT_CONFIGURED`). 25.0은 수용 |
| §3.2 #8/#9 재시도 | AI¹ 재시도 전무, 요청당 provider 호출 1회, SDK retry `None` (기존 계약 유지, 협의서 §4.6도 동일) |
| §3.3 #10 이미지 링크 | HTTPS 서명 URL만(`gs://` 거부), 호스트 allowlist는 환경변수 `AI1_OCR_ALLOWED_HOSTS`(미설정 → HTTPS만 검사). 위반은 budget 예약·Vision 호출 이전에 422 |
| §3.3 #12 페이지 | 1장(`page_index=0`) — 요청 스키마는 기존대로(0 이상 허용), 1단계 계약 테스트 추가(`test_phase_one_single_page_contract`) |
| §3.4 #16 raw-text | raw OCR은 검토용 초안 — 앱 문제·풀이 분리/`/analyze` 자동 전달 없음 (기존 `test_raw_text_response_is_not_sent_to_analysis_automatically` 유지) |
| §3.5 #19 오류 표 | §4.6.3 채택 — 코드·HTTP 상태 전부 일치. `OcrUsageLimitError.http_status=429`, 신규 `OcrImageFetchError`/`OcrUnsupportedImageError`(422). endpoint는 `getattr(exc,"http_status",...)` 매핑 |
| §4.6.3 분류 마커 | Vision 오류 메시지 마커로 fetch(`not found`·`image fetch` 등)/unsupported(`image format`·`invalid image` 등) 분류 추가 — `_classify_google_error` |
| 인증 선행성 | 인증 실패 시 provider 초기화·호출·budget ledger 예약 0회 — `test_auth_failure_runs_no_provider_or_budget` |
| 키 보안 | AI① 발급 키는 저장소·문서·응답·로그에 없음(`%TEMP%` 파일로만 보관), 테스트는 더미 키만 사용 |

S1-2는 **2026-11-10 종료**했다(Backend PC `/health` 200·약 0.4초, 키 없음/틀린 키 401, `/docs` 404, `AI1_BASE_URL` 등록, `AI1_OCR_ENABLED=false` 유지). 키 발급 주체는 AI①로 변경해 43자 키를 새로 발급·주입하고 재기동 후 공개 경로를 재검증했다(키 없이 401 `E_AUTH_INVALID_KEY`, 새 키 정상 분석 200 `ANALYZED` 0.76초 — 값은 비기재).

미결(연동 비차단): Backend의 키 수령·실제 분석 호출(S1-3) → O-06 실서버 로그 확인, Vision 실호출 전 잔량·팀장 승인(S2-2), `PUBLIC_BASE_URL` 실제 호스트 값(S2-3 운영자 설정), 공식 코드 매핑 계약 2건(skill 1:N 전부/대표, 시드 MIS 20개 미대응 정책).

S1-3 실행 자료는 `docs/AI1_S1-3_실호출_테스트_준비.md` 에 고정했다(경로·메서드·헤더·요청/응답 스키마·curl 8종·오류 형식 2종, 키 값 비기재). Backend 서버 공개 주소 `https://frugally-unsubtle-luminous.ngrok-free.dev/api/v1`(고정)는 D-06으로 기록했고 AI①은 이를 호출하지 않는다(ADR-01) — S2-3 이미지 URL 허용목록 host 후보로만 쓴다.

**S1-3 실호출 관측(2026-11-10 11:10)**: Backend(Python urllib 3.14)가 이 도메인으로 analyze 6건 호출 — 200 2건(900101·900106), 422 4건(`E_BAD_PROBLEM` 2·`E_UNRECOGNIZED` 1·`E_UNKNOWN_SKILL_CODE` 1). 키 인증은 실제 통과. 남은 실패는 입력·매핑 계약 문제다. 터널 운영·고정 도메인 절차는 `docs/AI1_터널_고정도메인_운영.md`, 실행 스크립트는 `scripts/start_ai1_tunnel.ps1`.


### 14.24 1·2단계 테스트 로컬 커버리지 매트릭스 (PHASE 9-9C.9A)

협의서 §5.1(A-01~A-10)·§5.2(O-01~O-06)의 16개 테스트 중 **무엇이 이미 로컬(fake
provider·더미 키만)로 증명됐는지**, 무엇이 **실호출/Backend 몫인지** 고정한다.
로컬 증명은 AI¹ 쪽 계약만 고정한다 — A 시리즈 실행 자체는 Backend 주도다.

| ID | 시나리오 요지 | 로컬 증명 (테스트) | 미측정 부분 | 실호출/Backend 몫 |
|---|---|---|---|---|
| A-01 | 오답 풀이 → `first_error_step_index`·오류 상세 | `test_attempt_api::test_error_attempt_records_error_and_misconception` + 신설 `test_first_error_step_index_and_error_evidence_fields` (`description_ko`·`evidence_latex` 존재) | 실제 Backend→AI¹ 전송 지연 | Backend 실행 |
| A-02 | 정답 풀이 → 무오류 | `test_attempt_api::test_correct_attempt_stored_and_analysis_id_returned` + 신설 `test_correct_attempt_reports_no_errors` (`errors == []`) | — | Backend 실행 |
| A-03 | REVIEW_REQUIRED | `test_attempt_api::test_review_required_result` (+ `test_expression_only_step_is_unreviewable`) | — | Backend 실행 |
| A-04 | UNKNOWN 반환 · AI② 호출 없음 | `test_attempt_api::test_unknown_keeps_code_status_message` · `test_backend_text_adapter_api::test_unknown_status_is_422_detail_and_persists_nothing`·`test_unknown_does_not_register_attempt` | — | Backend 실행. AI①→AI② 호출 경로는 코드에 부재(설계 속성, ADR-01) |
| A-05 | 미지원 Skill 코드 422 | `test_backend_text_adapter_api::test_unknown_backend_skill_code_rejected_422` · `test_backend_problem_persistence::test_unknown_skill_code_leaves_no_problem` | — | Backend 실행 |
| A-06·A-07 | 키 누락/틀린 키 → 401, Backend FAILED·재시도 없음 | `test_internal_auth` (401 계약 전반: `test_every_route_excluding_health_requires_key`·`test_wrong_key_is_401`) | 개발 서버 접속 실경로 | Backend 재시도 정책·FAILED 기록 |
| A-08 | 동일 attempt_id 재전송 → 동일 결과 | `test_attempt_api::test_idempotent_repeat_returns_same_analysis_id`·`test_attempt_conflict_not_overwritten` (+ `test_backend_text_adapter_api::test_idempotent_repost_does_not_add_history_or_reobserve`·`test_conflict_still_409_on_different_body_same_attempt`) | — | Backend 실행 |
| A-09 | 재촬영 → 새 attempt_id·`parent_attempt_id` 연결 | `test_attempt_api::test_different_attempts_separate_history` (+ `test_backend_text_adapter_api::test_different_attempt_ids_keep_separate_histories`) — 요청 스키마에 `parent_attempt_id` **없음** (app/ 정적 검색 0건) | 회차 연결 저장 | **Backend DB 책임 전부** |
| A-10 | AI¹ 지연 → Backend timeout, 재시도 없음 | 신설 `test_ocr_api::test_provider_failure_causes_exactly_one_call_without_retry` (오류·예외 재호출 0회) + `test_google_vision_provider::test_single_provider_call_per_request_with_default_timeout` (호출 1회, 기본 8초) | 28초/네트워크 지연 실측 | **Backend timeout 책임** |
| O-01 | 깨끗한 문제·풀이 3회, PARTIAL·raw text, 28초 | `test_ocr_api::test_raw_text_ocr_response_is_partial_and_does_not_guess_semantics` (+ D-02 응답 예시, 계약 고정) | 품질·**28초 실측** | **Vision 실호출 3회** (S2-2 승인 후) |
| O-02 | 빈 이미지 → UNREADABLE | `test_ocr_api::test_raw_text_without_recognized_text_is_unreadable` (+ unreadable 계약군·D-02 UNREADABLE 예시) | 실제 빈 이미지 입력 | Vision 실호출 1회 |
| O-03 | 만료 링크 → 422 fetch 계열 | `test_ocr_api::test_ocr_422_error_mapping` (422 매핑·URL 비노출) + `test_google_vision_provider::test_google_errors_have_actionable_types_and_redact_signed_url` (마커 분류) | 실제 만료 URL | Vision 실호출 1회 |
| O-04 | 사용량 상한 → 0회, 429 | `test_ocr_api::test_usage_limit_error_maps_to_429` + `test_google_vision_provider::test_usage_cap_denies_before_google_request` (Google 호출 0회) | ledger 실파일 기반 deny (프로토타입 미실행) | 0회 → 기록만 |
| O-05 | 보정 후 분석 요청 → A-01과 동일 | `tests/test_ocr_analysis_handoff.py` 전반 (`test_complete_ocr_request_uses_ocr_problem_latex_and_step_order`·`test_partial_ocr_keeps_unreadable_marker_as_unrecognized_step` 등) + `test_ocr_handoff_golden.py` 9 케이스 | 앱 보정 UX | **보정은 앱/Backend 책임(§3.4 #17)** |
| O-06 | 로그·오류 점검 — URL·키·원문 노출 없음 | 신설 `test_internal_auth::test_app_code_has_no_logging_or_print_statements` (app/ 정적 스캔) + `test_internal_auth::test_key_values_never_appear_in_auth_responses`·`test_unconfigured_503_leaks_no_key` + `test_google_vision_provider::test_google_errors_have_actionable_types_and_redact_signed_url` + `test_ocr_api::test_ocr_422_error_mapping` (`IMAGE not in r.text`) | 실서버 로그 파일 점검 | Backend 실호출 로그 몫 |

요약: **로컬 증명 16/16 중 14개는 AI¹ 계약 수준으로 증명**, A-09는 AI¹ 측 증명이
스키마 부재·이력 분리뿐(나머지 Backend 몫), A-10은 재호출 금지까지만 AI¹ 측 증명.
28초/8초 실측(O-01)과 실제 서버 로그 점검(O-06)은 2단계 실호출 몫이다.

preflight(`app/integration/preflight.py` — `tests/test_preflight.py` 40건)는
S1-2·S2-3 착수 전 환경변수 6종을 네트워크 0회·값 비노출로 점검한다. timeout
파싱은 NaN/무한대도 fail-closed로 거부하고, allowlist 항목은 스킴/경로/포트가
없는 bare hostname 만 유효로 본다(`app/integration/host_policy.py` 공용 규칙 —
`tests/test_host_policy.py` 20건; provider도 같은 규칙으로 기동 시 503).

### 14.16 상태

- pytest **1935 passed, 1 warning** (2026-11-23 실행). 이전 단계 기록: PHASE 9-6 1020 passed, PHASE 9-7 1147 passed, PHASE 9-7.1 1186 passed, PHASE 9-7.3 1224 passed, PHASE 9-8A 1280 passed, PHASE 9-8A.2 1318 passed, PHASE 9-9C.4 1602 passed, PHASE 9-9C.7 1603 passed, PHASE 9-9C.8 1642 passed, PHASE 9-9C.9A 1676 passed.
- **확정** 으로 표시한 항목은 코드 구현·Backend 승인분·AI¹ 정책 등 이미 정해진 사실입니다.
- **14.1~14.5·14.10~14.15 의 Backend 계약은 전부 잠정(협의 초안)입니다.** 14.12 표에서
  **승인** 은 `distribution`·`2.1` 두 건뿐이고, 나머지는 provisional(미승인) 또는 미매핑입니다.
- 6.1~6.7 은 현재 API 응답에서 추출한 값이며, 6.8~6.9 는 **미구현 제안**으로 분리했습니다.
- 14.6 의 mastery 값은 `knowledge.py` 규칙(`error_type` 별 분기)에 맞춰 정정했습니다.
- **PHASE 9-7.2**: tracking Skill 거절로 새 문제가 등록되지 않도록 저장 시점을 분석 뒤로 옮겼다. 저장 순서(문제 → 정답 풀이 → Attempt → 분석)는 유지된다. → §14.2
- **PHASE 9-7.3**: 지연 저장의 동시 구멍을 막기 위해 리소스 ID 단위 락(problem → solution 순서)으로 resolve → analyze → persist 를 직렬화했다. → §14.2
- **PHASE 9-7.4**: attempt 락 안에서 저장된 분석을 반환하기 직전에 본문을 재비교해 동시 충돌이 409 로 막히게 했다. → §14.2
- **PHASE 9-8A**: OCR 경계(`POST /api/v1/attempts/{id}/ocr`)와 `OcrProvider` 인터페이스를 추가했다. 실제 OCR 업체는 미정 — provider 미설정 시 503. → §14.17
- **PHASE 9-8A.1**: status 파생 규칙 보완(내용 전체 반영) + 미인식 `text` 계약 `"???"` 로 통일 + 페이지별 `ocr_result_id`. → §14.17
- **PHASE 9-8A.2**: readable-content 검증(내용 없는 problem/step 거절) + bbox-only region 판정 정정 + 문서 정합(`"??"` 오타, `ocr_result_id` 예시). → §14.17
- **PHASE 9-8B**: OCR 응답 → Attempt 분석 요청의 Backend orchestration 규칙을 fake provider 테스트로 고정했다. AI¹ 내부 호출은 없다. → §14.18
- **PHASE 9-9C.4**: Google Cloud Vision 앱 provider를 명시적 opt-in으로 구현했다. raw-text 미리보기만 반환하고, 응답에서 signed image URL을 SHA-256 fingerprint로 대체한다. 8초 upstream timeout, SDK retry 비활성화, eval probe와 공유하는 로컬 40회 상한, 인증/API/billing/quota 오류를 추가했다. 초기 SDK 테스트에서 fake URL+AnonymousCredentials 요청 2회가 실수로 endpoint에 도달해 403으로 거절됐다. 이후 client/RPC 생성 테스트를 제거하고 network-free SDK message/signature 검사로 대체했다. 실제 OCR 성공·서비스 계정 사용·billing 설정은 없었다. → §14.17/§14.20
- **PHASE 9-9C.6**: `/ocr` 내부 인증 계약(헤더 이름·키 환경변수·미인증 오류 상태)이 어떤 문서에도 없어 guard 를 임의 구현하지 않았다. 현재 app 에 인증 검증은 전무하다. Backend 협의안건 2-9(내부 API Key 헤더 — 제안)·AI¹ 회신제안 P0(내부망+secret manager — 제안)을 근거로 구현 재개 조건 확인 질문을 기록했다. → §14.21
- **PHASE 9-9C.7**: OCR 응답에서 `image_reference` 필드를 **완전히 제거**했다 — 원본 URL/서명뿐 아니라 hash fingerprint도 응답·테스트·문서에서 동기화해 두지 않는다(요청 입력은 유지, provider 호출에만 전달). 요청당 provider 호출 1회·SDK retry `None`·기본 timeout 8초가 실제 SDK 호출에 전달되는 것을 fake 테스트로 고정했다. `GOOGLE_VISION_TIMEOUT_SECONDS` 가 Backend 10초보다 길어지는 값의 허용 여부는 미결이다. 인증 계약은 미확정 그대로다. → §14.20/§14.22
- **PHASE 9-9C.8**: 협의서(2026-11-03) 기준 구현 — 내부 인증 guard(`X-Internal-Api-Key`/`AI1_INTERNAL_API_KEYS`/401/503 fail-closed/constant-time/`/health` 제외 전부/인증 선행)를 구현하고, OCR 계약 정합성(HTTPS-only+`AI1_OCR_ALLOWED_HOSTS`·timeout 25초 상한·`E_OCR_USAGE_LIMIT_REACHED`→429·fetch/unsupported→422·§4.6.3 매핑)을 반영했다. Code 이전 미결이던 timeout 상한·503 재시도 항목은 협의서 결정으로 해소됐다. → §14.20/§14.21/§14.22/§14.23

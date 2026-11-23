# learning-intelligence — AI① 풀이 분석 파이프라인

P5 적응형 학습 튜터의 **AI① Learning Intelligence** 구현체.
텍스트 풀이를 입력받아 SymPy로 단계별 동치를 판정하고, 첫 오류 줄을 특정한 뒤
오개념 트리에 분류하여 Knowledge State를 갱신한다.

문제집 사진 입력은 **Backend 가 원본을 보관**하고 AI① 은 `image_reference` 만 받아
OCR 경계를 통과시킨다(§API). Backend 는 OCR 응답을 Attempt 분석 요청으로 변환해
2단계로 호출한다 — AI¹ 은 두 단계를 서로 호출하지 않는다.

## 파이프라인

```
텍스트 풀이 입력
  → 단계 분리 (parser.split_steps)
  → SymPy 동치 판정 (verifier.verify)      ← LLM 단계분석은 이 지점에 주입 (segmenter 인자)
  → 오류 유형 분류 (classifier.classify)     ← taxonomy 조회 기반, 자유생성 금지(ADR-02)
  → Knowledge State 갱신 (knowledge.rule | bkt)
  → 계약 JSON 반환
```

문제집 사진 입력(Backend 가 이미지를 보관하며 AI¹ 은 참조만 받는다):

```
image_reference
  → Google Cloud Vision provider (AI1_OCR_PROVIDER=google_vision일 때만 선택)
  → raw OCR 미리보기 (텍스트·줄·좌표·OCR 신뢰도; 문제/풀이 의미는 분류하지 않음)
  → [Backend 가 변환] Attempt 분석 요청 (problem_latex / solution_text)
  → 위 텍스트 파이프라인
```

- **인식 실패는 오답이 아님** (ADR-03): 파싱 실패 줄은 `unrecognized`로 분리하고 `valid=null`
- **판정 불가 시 추측 금지**: SymPy가 동치를 확정한 구간만 오류로 표시
- **엔진 2종**: `rule` (Stage 0 MVP), `bkt` (Stage 1, BKT 수식 직접 구현)
- **taxonomy이 runtime source of truth**: Skill / error_type / error_subtype /
  misconception ID를 `data/*.json`에서만 조회한다

## 디렉터리

```
app/
  main.py                 FastAPI — 8개 라우트 (아래 API 참조)
  schemas.py              입출력 JSON 계약 (Pydantic)
  taxonomy.py             frozen taxonomy 정적 데이터 모델 · 검증 · runtime 조회 계층
  pipeline/
    parser.py             정규화 · LaTeX 파싱(중첩 \frac) · 기호 정책 · 단계 분리
    verifier.py           SymPy 동치 판정 → valid[], 첫 오류 줄, 복잡도 가드
    classifier.py         오류 유형 → taxonomy 매핑 (분배/이항/오독/계산)
    knowledge.py          Stage 0 규칙 기반 + Stage 1 BKT, 상태 상한/eviction
    analyzer.py           오케스트레이션 + 입력 정책 검증 + 엔진 레지스트리
    session.py            Problem · CorrectSolution · StudentAttempt · AttemptAnalysis · 단계 정렬
    repository.py         학습 저장소 계층 (in-memory) — 분석 이력 · attempt 단위 락
  integration/
    backend_crosswalk.py  Backend 승인 코드 crosswalk (승인 2건만, 단독 소유)
    backend_adapter.py    Backend 전달 경계 — ID 정규화 · 코드 배열 · 대표 오류 · tracking Skill 계약
    ocr.py                OCR provider 인터페이스 · registry · status/confidence 파생
    google_vision.py      명시적 선택형 Cloud Vision provider (ADC · DOCUMENT_TEXT_DETECTION)
    google_vision_budget.py 공유 로컬 호출 상한 원장 (앱 provider + eval probe)
data/
  skills.json             Skill 31개 (base 26 + expansion 5)
  error_types.json        error_type 4개 + error_subtype 17개
  misconceptions.json     오개념 19개
docs/
  AI1_명세서_대조표.md     실행 중 대조표 §1~§14 (Backend 계약 · Q1~Q24 질의표)
  AI1_HANDOFF.md          인수인계 문서
  STEPi_기능명세서_원본.md  원본 명세 (읽기 전용, 파싱본)
tests/
  golden/golden_100.json  Golden 100건 (정답 44·계산 28·분배 11·이항 12·오독 5)
  golden/golden_20.json   초기 20건
  golden/ocr_handoff.json OCR → Attempt 분석 handoff 계약 9건
  test_parser.py          정규화·단계 분리·중첩 분수·기호 정책
  test_verifier.py        동치 판정 + gap-bridging
  test_verifier_multivariate.py  다변수 동치 판정 (S1)
  test_classifier.py      오류 분류(분배/이항/오독/계산, 다중 괄호 포함)
  test_transposition_split.py   이항 오류 원인 3.1/3.2 세분화
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
  test_attempt_api.py     Attempt 분석 API 계약
  test_attempt_idempotency.py  멱등성 · 동시 중복 요청 원자성
  test_attempt_history_api.py / test_student_history_api.py  이력 조회
  test_attempted_at.py / test_time_range_filter.py  시각·기간 필터
  test_misconception_top_api.py  반복 오개념 TOP
  test_problem_metadata_api.py  문제 표시 메타데이터
  test_repository.py       저장소 계층 (중복 ID·동시성)
  test_backend_crosswalk.py     Backend 승인 crosswalk
  test_backend_adapter.py       Backend adapter 단위
  test_backend_text_adapter_api.py  Backend 전달 API 계약
  test_backend_request_contract.py  tracking Skill 요청 계약
  test_backend_problem_persistence.py  거절 후 Problem 오염 방지
  test_backend_concurrent_source_of_truth.py  리소스 ID 동시성
  test_backend_concurrent_attempt_conflict.py  동일 attempt_id 동시 충돌
  test_ocr_api.py          OCR 경계 계약 (fake provider)
  test_google_vision_provider.py Google Vision 앱 provider 계약 (fake SDK only)
  test_ocr_analysis_handoff.py  OCR → Attempt 분석 Backend orchestration
  test_ocr_handoff_golden.py   handoff 계약 golden 회귀 (100% 일치)
```

## 실행

```bash
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest            # 전체 회귀 테스트
.venv\Scripts\pyflakes app tests          # lint
.venv\Scripts\uvicorn app.main:app --reload
```

## API

라우트 9개. `/analyze-solution`(Legacy)과 Attempt API 는 응답 계약이 서로 다르며,
새 Backend adapter 를 붙일 때 Legacy 응답은 변경하지 않는다.

| Method | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 프로세스 상태 |
| POST | `/analyze-solution` | **Legacy** 텍스트 풀이 분석 (16필드, 응답 계약 고정) |
| POST | `/api/v1/attempts/{attempt_id}/analyze` | Attempt 분석·저장·멱등성 (31필드) |
| POST | `/api/v1/attempts/{attempt_id}/ocr` | **OCR 경계** (잠정 경로, provider 필요) |
| GET | `/api/v1/attempts/{attempt_id}/analyses` | Attempt 분석 이력 |
| GET | `/api/v1/attempts/{attempt_id}/analyses/{analysis_id}` | 저장 시점 분석 상세 |
| GET | `/api/v1/students/{student_id}/history` | 학생 이력 (페이지네이션·기간 필터) |
| GET | `/api/v1/students/{student_id}/skills` | 학생 Skill 숙련도 목록 (웹 r26/r27, 읽기 전용) |
| GET | `/api/v1/students/{student_id}/misconceptions/top` | 반복 오개-concept TOP |

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

### `POST /api/v1/attempts/{attempt_id}/analyze`

Attempt 단위 텍스트 분석. 같은 `attempt_id` + 같은 본문 재전송은 기존 분석을 반환하고
Knowledge Observation 을 중복 반영하지 않는다. 다른 본문은 409 `E_ATTEMPT_CONFLICT`.

요청 필드 (`AnalyzeAttemptRequest`, 알 수 없는 필드는 422):

| 필드 | 타입 | 설명 |
|---|---|---|
| `problem_id` | string | (필수) Backend 가 준 불투명 ID (`captured:<id>`, `PRB-*` 를 그대로 보존) |
| `problem_latex` | string? | 미등록 문제 최초 등록용 문제식 |
| `problem_skills` | string[] | 문제의 Skill 목록. **tracking Skill 최소 1개 필수** — 내부 ID 또는 Backend `SKL-*` 코드 |
| `student_id` | string \| int | 정수는 십진 문자열로 정규화 |
| `solution_text` | string | (필수) 학생 풀이 |
| `correct_solution` | object? | 정답 풀이 (미등록 문제 최초 등록용) |
| `engine` | `rule` \| `bkt` | Knowledge State 엔진 |

응답 필드 (`AnalyzeAttemptResponse`) — 기존 26개 + Backend 전달용 additive 5개 + 응답 전용 additive 2개:

| 필드 | 설명 |
|---|---|
| `misconception_tag` | 앱 r48 호환 단수 필드. 대표 오류(가장 낮은 `step_no`)의 오개념 ID |
| `official_skill_codes` / `official_misconception_codes` | Backend **승인** 코드. 없으면 `[]` |
| `provisional_skill_codes` / `provisional_misconception_codes` | **후보(미승인)** 코드. 없으면 `[]` |
| `final_answer_present` | 마지막 답 블록이 최종 답을 모두 포함하는지. `true`=모두 포함, `false`=최종 답 아님 확정(중간식·미완성·일부 해), `null`=판단 불가. **`correct` 와 독립** — 틀린 최종 답이면 `true`+`correct=false`. `analysis_status != ANALYZED` 면 항상 `null`. 해를 여러 줄로 쓴 답 블록은 서로 다른 값의 개수로 센다 |
| `review_reasons` | 판정 보류 사유 코드. 정상이면 `[]`. `solution_block_notation` — 해를 여러 줄로 쓴 답 블록이 문제의 해집합과 **값까지 일치**해 `REVIEW_REQUIRED`로 보류한 경우 (`correct=null`, Knowledge State 미갱신) |

기존 `skill` · `skill_ids` · `misconception_id` · `misconception_ids` ·
`misconception_tags` 는 taxonomy ID 그대로 유지한다(Backend 코드로 덮어쓰지 않는다).

### `POST /api/v1/attempts/{attempt_id}/ocr`

문제집 사진 OCR. Backend 가 원본 이미지를 보관하고 `image_reference` 만 준다 —
사진 bytes multipart 업로드는 받지 않는다. Google Vision provider는 환경 설정으로
명시적으로 선택해야 활성화된다.

- 현재 Google Vision provider는 `AI1_OCR_PROVIDER=google_vision`일 때만 선택된다.
  provider 미설정 시 **503 `E_OCR_NOT_CONFIGURED`** 를 반환한다.
- Google Vision 응답은 `representation="raw_text"`, 전체 `raw_text`, 줄별
  `raw_lines`(좌표·confidence 포함), `model_version`, `ocr_confidence`를 담는다.
  Vision은 문제/손풀이 경계를 제공하지 않으므로 `problem=null`, `regions=[]`,
  `solution_steps=[]`이며 의미를 추측하지 않는다. 텍스트가 있으면 `PARTIAL`, 없으면
  `UNREADABLE`로 표시한다. Backend/학생이 raw text를 확인·보정한 뒤에만 분석한다.
- OCR 입력의 `image_reference`는 Vision 호출에 전달하지만, 응답에는 이미지 참조를
  어떤 형태로도 되돌려 보내지 않는다 — 원본 URL/서명뿐 아니라 hash fingerprint
  필드도 두지 않는다. 참조는 provider 호출 입력으로만 쓴다.
- **내부 인증(협의서 §3.1)**: `X-Internal-Api-Key` 헤더를 검사하며 `AI1_INTERNAL_API_KEYS`
  (쉼표 다중 키)와 constant-time 비교한다. 누락/불일치는 401 `E_AUTH_INVALID_KEY`,
  키 미설정/빈 설정은 fail-closed 503 `E_AUTH_NOT_CONFIGURED`. `/health`만 제외하고
  전부(legacy `/analyze-solution` 포함) 보호한다. 인증은 provider 초기화·호출·budget
  예약보다 선행한다. `/docs`·`/redoc`·`/openapi.json`은 인증 밖 기본 라우트라
  **기본 404**로 두고, 로컬 개발에서만 `AI1_ENABLE_DOCS=1`로 켠다.
- **이미지 링크는 HTTPS 서명 URL만** 받는다(`gs://` 거부, 협의서 §3.3 #10). 허용
  호스트는 `AI1_OCR_ALLOWED_HOSTS`(미설정 시 HTTPS만 검사). Vision timeout은 기본
  8초·설정 상한 25초(초과 설정은 503 `E_OCR_NOT_CONFIGURED`). 사용량/상한 초과는
  **429** `E_OCR_USAGE_LIMIT_REACHED`, 이미지 다운로드 실패·지원하지 않는 이미지는
  **422** `E_OCR_IMAGE_FETCH_FAILED` / `E_OCR_UNSUPPORTED_IMAGE` (협의서 §4.6.3).
- **읽을 수 없는 항목은 `unreadable:true` + `text:"???"` + `latex_text:null`** 이며,
  임의의 내용·LaTeX 로 복원하지 않는다(스키마가 강제).
- 이 endpoint 는 **부작용이 없다** — Knowledge State 를 갱신하지 않고, AI② 를 호출하지
  않고, Attempt/분석 이력도 남기지 않는다.
- **OCR confidence 와 수학 분석 `confidence` 는 서로 다른 값**이다.

앱 설정 예시(서비스 계정 JSON 자체는 저장소에 두지 않는다):

```powershell
# Backend↔AI1 내부 인증 (협의서 §3.1) — 실제 키 값은 저장소에 두지 않는다
$env:AI1_INTERNAL_API_KEYS = "<Backend가 전달한 키(쉼표로 여러 개, 교체 기간 구·신 병기)>"
# /health 를 제외한 전부가 이 헤더를 요구한다
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\secure\vision-service-account.json"
$env:GOOGLE_VISION_TIMEOUT_SECONDS = "8"
$env:AI1_OCR_PROVIDER = "google_vision"
# 이미지 링크 허용 호스트(협의서 §3.3 #10) — Backend PUBLIC_BASE_URL. 미설정 시 HTTPS만 검사
$env:AI1_OCR_ALLOWED_HOSTS = "<Backend PUBLIC_BASE_URL 호스트>"
# 로컬 개발 전용: /docs·/redoc·/openapi.json 노출. 운영에서는 설정하지 않는다(기본 404)
$env:AI1_ENABLE_DOCS = "1"
```

환경변수 조성 점검은 `app/integration/preflight.py` 로 한다 — 값은 절대
반환·출력하지 않고 `code/ok/message` 3필드 결과만 돌려준다 (기본 6종 검사:
provider 선택·credential 경로가 **파일**인지·timeout 범위(유한값 0<v≤25)·
호스트 **형식**(스킴/경로/포트 없는 bare hostname)·키 설정·docs 비노출).
1단계 연결 확인(S1-2)·2단계 실호출 직전에 실행한다:

```powershell
.venv\Scripts\python -c "from app.integration.preflight import preflight_report; print(preflight_report())"
```

### 1단계 연결 확인(ngrok 고정 도메인)

Backend가 AI①에 접근하는 1단계(S1-2)는 로컬 서버를 ngrok으로 임시 공개해 확인한다.
LAN에 열지 않으므로 `--host 127.0.0.1` 을 유지한다. 고정 도메인은
`vividness-pep-unboxed.ngrok-free.dev` 이다.

```powershell
# 1) 키를 먼저 설정한다 (값은 저장소·문서·로그에 남기지 않는다)
$env:AI1_INTERNAL_API_KEYS = (Get-Content "$env:TEMP\ai1_internal_key.txt" -Raw).Trim()
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8001

# 2) 별도 터미널 — 도메인을 명시 고정한다
ngrok http 8001 --url https://vividness-pep-unboxed.ngrok-free.dev
```

점검·기동·중지는 스크립트로 처리한다(토큰 하드코딩 없음, 키 값 비출력):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_ai1_tunnel.ps1              # 점검
powershell -ExecutionPolicy Bypass -File scripts\start_ai1_tunnel.ps1 -Action Start
powershell -ExecutionPolicy Bypass -File scripts\start_ai1_tunnel.ps1 -Action Stop
```

- 확인은 `/docs`(운영 기본 404)가 아니라 `GET /health` → 200 으로 한다.
- 무료 플랜은 기본 도메인을 자동 사용하지만 보장이 아니므로 `--url`로 명시한다.
  주소가 바뀌면 Backend에 재통보한다.
- 브라우저 요청에는 ngrok 안내 HTML이 끼어든다. API 클라이언트(curl·httpx)는 영향이
  없고, 브라우저로만 확인할 때 `ngrok-skip-browser-warning: true` 헤더를 붙인다.
- Authtoken·API 키는 채팅·문서·커밋에 남기지 않는다. 자세한 절차는
  `docs/AI1_터널_고정도메인_운영.md`.

`google-cloud-vision`은 `requirements.txt`에 선언되어 있다. 앱과 별도 eval probe는
`eval/_local/google_vision/call_budget.jsonl`의 **누적 40회 로컬 상한**을 공유한다.
이는 billing account 전체 quota가 아니다.

**월별 무료 한도 가드 (fail-closed).** 코드에는 무료 한도·과금 단위를 상수로 두지 않는다.
운영자가 Google 콘솔/문서에서 확인한 값만 아래 환경변수로 주입하며, 없으면 OCR은
503 `E_OCR_NOT_CONFIGURED`로 거부된다(provider 생성 자체를 거부).

| 환경변수 | 의미 |
| --- | --- |
| `AI1_OCR_FREE_QUOTA_CONFIRMED` | `1` — 무료 제공량을 운영자가 확인했다는 attestation |
| `AI1_OCR_BILLING_UNIT` | 이 provider는 `document_text_detection` 필수 (다른 값이면 차단) |
| `AI1_OCR_MONTHLY_FREE_QUOTA` | 확인된 월 무료 OCR units |
| `AI1_OCR_MONTHLY_CALL_CAP` | 내부 안전 상한. **무료 한도보다 작아야** 하며 같거나 크면 차단 |

- 월 경계는 **UTC 1일 00:00** 이다. 이전 달 예약/결과 기록은 삭제하지 않고 보존한다.
- 예약은 Google 요청 **전에** 잠금 안에서 기록하고 `fsync` 한다. 성공·실패·timeout·
  403/429를 포함한 모든 시도가 차감되며, 자동 재시도는 없다(SDK `retry=None`).
- 누적 40회와 월별 내부 상한 중 하나라도 차면 Google API를 호출하지 않는다.
- 원장을 읽을 수 없거나 형식이 깨졌으면 503으로 차단한다.
- 남은 수치는 **AI① 내부 상한의 잔여량**이지 Google Billing의 실제 무료 잔량이 아니다.
  코드는 실제 잔량을 추정하지 않는다.

상태 조회(읽기 전용, 원장·API 무변경):

```powershell
.venv\Scripts\python scripts\ocr_usage_status.py
```

이번 달 예약/성공/실패/미확인, 남은 내부 한도, 누적 40회 사용량, 차단 여부·사유를
JSON으로 출력한다. API 비활성화·인증·billing 설정 오류는 각각
`E_OCR_API_DISABLED`, `E_OCR_AUTH_FAILED`, `E_OCR_BILLING_REQUIRED`로 503을
반환한다. 실제 quota 잔량·결제 설정 확인과 승인 전에는 환경변수를 활성화하거나
실제 OCR 요청을 보내지 않는다. 이 저장소에는 Mathpix/Pix2Text runtime dependency가 없다.

OCR 응답 → Attempt 분석 요청의 변환 규칙(Backend responsibility)은
`docs/AI1_명세서_대조표.md` §14.18 에 있고, `tests/test_ocr_analysis_handoff.py` 가
2단계 흐름을 fake provider 로 검증한다.

AI② 전달 패키지(OpenAPI·대표 예시 4종·판정 한계 사본·전달 README)는 한 명령으로
재현한다 — 수동 편집 금지, 실제 앱에서 생성된다:

```powershell
.venv\Scripts\python scripts\export_contract.py --out-dir docs/AI2_전달_2026-11-23
```

기본 출력은 `docs/AI2_전달_<YYYY-MM-DD>/` 이며, 배포 절차는
`docs/AI1_배포_체크리스트.md` 를 따른다.

배포 스모크는 상태 표만 출력한다 — 키는 환경변수로만 넘기고, 출력에는 host·상태
코드·필드명만 나온다:

```powershell
$env:AI1_SMOKE_KEY = "<키>"
.venv\Scripts\python scripts\smoke_deploy.py --base-url https://<host>
```

### `GET /api/v1/students/{student_id}/skills`

학생이 **관측된** Skill 의 현재 state/mastery 목록 (명세 웹 r26/r27 숙련도 차트).
Knowledge State 엔진이 실제로 관측한 Skill 만 반환하며, taxonomy 전체를 나열하거나
관측 없는 Skill 을 0으로 채우지 않는다. **읽기 전용** — 상태·LRU 를 바꾸지 않는다.

- `engine`: `rule`(기본) | `bkt`. 엔진별 상태를 섞지 않으며 응답에 사용한 engine 을 명시한다.
- 기록이 없는 학생은 `200` + `skills: []`, `count: 0` (404 아님).
- `skill_id` 오름차순 고정. 그룹 노드(`bkt_eligible=false`)는 포함하지 않는다.
- `weakness_score`·추세·타임스탬프는 계산하지 않는다 (Backend 책임).

```json
GET /api/v1/students/S1/skills?engine=rule
{
  "student_id": "S1",
  "engine": "rule",
  "skills": [
    { "skill_id": "linear_equation", "state": "learning", "mastery": 0.6 }
  ],
  "count": 1
}
```

기록 없는 학생:

```json
{ "student_id": "NOBODY", "engine": "rule", "skills": [], "count": 0 }
```

알 수 없는 engine 은 `422 E_UNKNOWN_ENGINE`, 잘못된 `student_id`(공백·64자 초과)는
`422 E_INVALID_STUDENT_ID` 이다.

## 에러 코드

| code | HTTP | 발생 조건 |
|---|---|---|
| `E_INPUT_TOO_LARGE` | **413** | 문제식 2000자·풀이 2만자·30단계 초과 |
| `E_EMPTY_SOLUTION` | 422 | 풀이 입력이 비어 있음 |
| `E_BAD_PROBLEM` | 422 | 문제식 파싱 불가 → `correct` 단정 금지 (학생이 수정 후 재시도 가능) |
| `E_NOT_EQUATION` | 422 | 파싱은 됐지만 등식이 아님(표현식 전용 문제) → 재시도해도 결과 동일. `analysis_status` 없음 |
| `E_UNRECOGNIZED` | 422 | 풀이 전량 인식 실패 |
| `E_UNVERIFIABLE` | 422 | 어떤 줄도 동치 판정 성공 실패 → `correct` 단정 금지 |
| `E_UNKNOWN_SKILL` | 422 | taxonomy에 없는 `problem_skill` |
| `E_SKILL_NOT_TRACKABLE` | 422 | `bkt_eligible=false` (group node/future skill) |
| `E_INVALID_STUDENT_ID` | 422 | 빈 값·64자 초과·제어문자 포함 |
| `E_UNKNOWN_SKILL_CODE` | 422 | crosswalk 에 없는 Backend `SKL-*` 코드 (기본 Skill 으로 치환하지 않음) |
| `E_INVALID_BACKEND_ID` | 422 | Backend 가 준 식별자를 정규화할 수 없음 (빈 값·bool 등) |
| `E_ATTEMPT_CONFLICT` | 409 | 같은 `attempt_id` 로 다른 본문 재전송 |
| `E_RESOURCE_CONFLICT` | 409 | 이미 등록된 리소스가 분석에 쓴 후보와 다름 |
| `E_SOLUTION_PROBLEM_MISMATCH` | 409 | 정답 풀이가 다른 Problem 소속 |
| `E_AUTH_INVALID_KEY` | **401** | `X-Internal-Api-Key` 누락·불일치 (재시도 대상 아님, 협의서 §3.1) |
| `E_AUTH_NOT_CONFIGURED` | **503** | 서버에 인증 키 미설정 — fail-closed, 보호 경로 전체 거부 |
| `E_OCR_NOT_CONFIGURED` | **503** | OCR provider 미설정 — 결과를 지어내지 않는다 |
| `E_OCR_AUTH_FAILED` | **503** | Google credential 또는 IAM 권한 오류 |
| `E_OCR_API_DISABLED` | **503** | Google Cloud Vision API 비활성화 |
| `E_OCR_BILLING_REQUIRED` | **503** | Google Cloud billing 미활성화 |
| `E_OCR_USAGE_LIMIT_REACHED` | **429** | 프로젝트 로컬 누적 호출 상한/quota 도달 (재시도 대상 아님) |
| `E_OCR_IMAGE_FETCH_FAILED` | **422** | 이미지 링크 다운로드 실패 (만료·404 등) |
| `E_OCR_UNSUPPORTED_IMAGE` | **422** | 지원하지 않는 이미지 참조(비-HTTPS)·형식/MIME |
| `E_OCR_PROVIDER_ERROR` | 502 | 그 외 OCR provider 호출 실패·계약 위반 |

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
- 해를 여러 줄로 쓴 답 블록(`x=3` / `x=-3`)은 단계 동치 판정만으로는 오답이 되므로, 값 집합이 문제의 해집합과 **값까지 일치**하고 오류가 모두 답 블록 안에 있으면 `REVIEW_REQUIRED`(`review_reasons=["solution_block_notation"]`, `correct=null`)로 보류한다. **정답으로 자동 확정하지 않는다** — 값이 다르면(진짜 오답) 그대로 `ANALYZED`
- 이항 계열 오류는 원인에 따라 `3.1`(부호 미변경)과 `3.2`(항 누락/일부 양변 조작 비대칭)로 세분화된다. `3.2` detector 는 항 누락/일부 비대칭만 탐지하며 일방적 곱셈·나눗셈 전체를 보장하지 않는다
- 분배의 "부호 오류"(`2(x-3) → 2x+3`)는 전용 subtype이 없어 `distribution_omit`으로 분류
- 파서는 단일 문자 변수와 `sqrt`만 허용한다. `sin`·`cos`·`log`는 taxonomy상 future 영역이라 차단된다
- LaTeX `\sqrt{}` 형태는 미지원(`sqrt(...)`만 동작). 중첩 `\frac`는 지원한다
- Knowledge State는 in-memory(학생 10,000명 상한·LRU)이며 BKT 파라미터는 미피팅
- Google Vision OCR은 **opt-in raw-text preview**다. 문제/풀이 분리·LaTeX·한국어/수식 인식 정확도는 검증되지 않았다. 실제 provider 호출은 billing/free quota 확인 및 사용자 승인 뒤에만 수행한다.
- OCR → 분석 2단계에서 **연결 ID·FK 와 다중 페이지 병합 규칙은 미정**이다
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

### 검증 현황

| 항목 | 결과 |
|---|---|
| pytest | **1713 passed**, 1 warning (Starlette httpx deprecation) |
| lint | `pyflakes app tests` → 0건 |
| taxonomy validation | `validate_all()` → `[]` |
| golden regression | 100 case · `correct`/`error_step`/`error_type`/`skill`/`state` **각 1.000** · 실패 0 · false mismatch 0 |
| API smoke | PHASE 1~5 누적 15개 + PHASE 8/9 누적 시나리오 통과 |

기존 테스트는 삭제하지 않았고(271 → 1713, 전부 증가), golden expected 값도 변경하지 않았다.

## 다음 단계 (기획 로드맵)

1. **단계 분리 LLM 주입** — `analyze(segmenter=...)` 인자로 교체
2. **BKT EM fitting** — `golden` 데이터로 파라미터 튜닝
3. **MySQL 연동** — in-memory Knowledge State → `mastery` 테이블 교체
   (현재 상한/eviction 정책이 이때 DB 캐시 정책으로 대체된다)
4. **OCR 후속 검증** — Backend 응답 계약·이미지 규격·인증을 확정하고, 무료 quota 확인 및 별도 승인 뒤 최소 샘플 실측으로 raw OCR 품질 측정
5. **OCR → 분석 E2E** — Backend 규칙대로 OCR 응답을 Attempt 분석 요청으로 변환해 golden image 데이터셋으로 검증

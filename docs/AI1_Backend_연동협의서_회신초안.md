# AI① 회신 초안 — STEPi Backend ↔ AI① 연동 협의서 (2026-11-03, 2026-11-08 갱신)

- 작성: AI① Learning Intelligence (내부 검토용 초안 — 전송 전 사용자/팀 확인 필요)
- 작성일: 2026-11-03 · 갱신: 2026-11-08 (S1-4 — 빈칸 0개)
- 대상 문서: `STEPi_Backend_AI1_연동협의서.md` (작성일 2026-11-03, Backend 기준 커밋 `d7e6571`)
- 회신 방법: 협의서 §0 규칙에 따라 모든 항목을 **"동의"** 또는 **"수정 제안 + 값"**으로 표기했다.
  AI① 코드가 정할 수 없는 사항 중 R-03·R-13은 AI①이 결정값을 채웠고(잠정치는
  **"잠정"**으로 표기), D-05는 실제 구동해 확인한 주소를 기재했다.
- 검증 환경: Google Vision/Mathpix 실호출 **0회**, GCP/billing 변경 없음, 서비스 계정 미사용.
  모든 테스트는 fake provider와 **더미 키**만 사용했다 (실제 Backend 키는 아직 전달받지 않았다).

---

## 1. §8.1 회신 표 (R-01 ~ R-13)

| 항목 | 협의서 요청(요지) | AI① 회신 | 확정 여부 |
|---|---|---|---|
| R-01 | 헤더 `X-Internal-Api-Key`, 401 `E_AUTH_INVALID_KEY`, 키 미설정 503 `E_AUTH_NOT_CONFIGURED` | **동의.** 구현·테스트 완료 (§14.21, D-03). 누락/불일치 = 401 본문 `{"detail":{"code":"E_AUTH_INVALID_KEY","message":"..."}}`, 키 미설정/빈 설정 = fail-closed 503 `{"detail":{"code":"E_AUTH_NOT_CONFIGURED","message":"..."}}` | 동의(구현 완료) |
| R-02 | AI① 키 환경변수 이름(권장 `AI1_INTERNAL_API_KEYS`, 쉼표 다중) | **동의 — 권장 이름 그대로 채택:** `AI1_INTERNAL_API_KEYS`. 쉼표 구분, 항목별 앞뒤 공백 제거·빈 항목 무시. 매 요청 환경변수 재조회라 재시작 없이 구·신 키 교체 반영 | 확정 |
| R-03 | 키 전달 방법: 대면 또는 팀 비밀번호 관리자 중 선택(도구 이름 기재) | **결정 — 대면(오프라인) 전달 기본.** 키 값은 인쇄물·메신저·문서·이슈 티켓에 남기지 않고, AI① 담당자 1인이 수령 즉시 AI① 서버 환경변수 `AI1_INTERNAL_API_KEYS`에 직접 입력한다(환경변수 재조회라 재시작 불필요). 비대면이 필요할 때만 팀 비밀번호 관리자의 1회 열람 링크(만료 24시간)로 대체하고, 그때 도구 이름을 이 칸에 추가한다. 방법: **대면 직접 입력** / 담당: AI① 개발 담당 1인 | 결정(AI① 제안 — Backend 이의 없으면 확정) |
| R-04 | 인증 범위: `/health`를 뺀 전부 | **동의.** `GET /health`만 예외, 나머지 7개 라우트 전부 보호 — legacy `/analyze-solution` 포함(§3.1 #6 "같이 보호"). 인증은 요청 본문 검증과 provider 초기화·호출·budget ledger 예약보다 선행한다 | 동의(구현 완료) |
| R-05 | Vision timeout 25초 이하, OCR 전체 응답 28초 이내, 분석 일반 응답 8초 이내 | **동의 + 값:** `GOOGLE_VISION_TIMEOUT_SECONDS` 기본 **8초**, 설정 허용 상한 **25.0초**(초과·잘못된 값은 fail-closed 503 `E_OCR_NOT_CONFIGURED`). "OCR 전체 28초/분석 8초"는 실측 근거가 아직 없다(실호출 0회) — 설정이 상한 구조로 잠겨 있다는 것만 확정, 실측은 2단계(테스트 O-01)에서 확인 | 값 확정 / 실측 미결 |
| R-06 | 이미지 링크 형식(4.2), 허용 호스트 환경변수화 | **동의 + 환경변수 이름 확정:** `AI1_OCR_ALLOWED_HOSTS` (쉼표 구분, 소문자 정규화, host exact match). **미설정 → HTTPS만 검사(호스트 제한 없음), 설정 → 목록 내 host만 허용.** 비-HTTPS(`gs://` 포함)와 미허용 host는 budget 예약·Vision 호출 이전에 422 `E_OCR_UNSUPPORTED_IMAGE`. Backend `PUBLIC_BASE_URL` 실제 호스트 값은 AI①이 임의로 정하지 않는다 — 운영자가 S2-3에서 설정 | 확정(환경변수) / 호스트 값은 S2-3 |
| R-07 | OCR 응답 읽는 필드(4.3)와 `image_reference` 제거 | **동의.** 응답 항상 제공: `status`(`COMPLETE·PARTIAL·UNREADABLE`), raw 모드 `raw_text`, `raw_lines[].text`, `ocr_confidence`(nullable), `warnings[]`, `problem.latex_text/.text`, `solution_steps[].latex_text/.text`, 그 외(`representation`, `ocr_result_id`, 좌표)는 저장 전용. 예외: raw 모드는 Vision 특성상 `problem=null`·`regions=[]`·`solution_steps=[]`·`status=PARTIAL`(텍스트 있음)·`UNREADABLE`(없음). **`image_reference`는 필드 자체가 없다** — 원본 URL·서명·해시 어떤 것도 응답에 없다(협의서 §3.4 #18 그대로) | 동의(구현 완료) |
| R-08 | 오류 코드별 HTTP 상태(4.6.3). 다르면 대응표 기재 | **동의 — 이름·상태 모두 4.6.3 표와 일치. 대응표 불필요.** D-01 전체 표 참조. 유일한 덧붙임: 분류는 provider 오류 메시지 마커 기반이라, 새로운 오류 유형이 마커에 걸리지 않으면 502 `E_OCR_PROVIDER_ERROR`로 떨어진다(§4.6.1 "그 밖의 모든 응답 → FAILED"와 정합) | 동의 |
| R-09 | 분석 요청 형식(4.4): `problem_id` 해시 방식, `problem_latex` 항상 전송. `problem_id` 길이 제한: | **동의.** `problem_id`는 opaque 문자열로 처리 — **길이 제한 없음**(스키마 무제한, 약 30자 가이드와 호환). 수식이 바뀌면 ID가 바뀐다는 해시 방식 동의 — AI①은 `problem_id` 기반 결과 캐시가 없어 재사용 부작용 없음. `problem_latex` 항상 전송 동의 — AI① 스키마는 미제공도 수용하지만(opt) Backend가 4.4대로 항상 보내면 제약 없음 | 동의 / 길이 제한 없음 |
| R-10 | 분석 응답 읽는 필드(4.5)와 UNKNOWN 422 형식 | **동의.** 응답에 `analysis_status`(3값), `correct`, `errors[]`(각 항목 `step_no` 1-based, `error_type`, `error_subtype`, `description_ko`, `evidence_latex`, `confidence`, `misconception_ids`), `confidence` 제공. `misconception_ids`는 AI① 내부 ID(`2.1` 체계) — FK는 Backend가 승인 코드만 사용(4.5 그대로). UNKNOWN은 200 + `analysis_status:"UNKNOWN"` **및** 422 + `{"detail":{"analysis_status":"UNKNOWN", ...}}` 두 형식을 모두 내보내며 4.5 "같은 처리"와 정합 (D-04 참조) | 동의 |
| R-11 | 데이터 처리: 이미지 미보관, URL·원문 로그 금지 | **동의.** AI①은 이미지 bytes를 받는 경로가 없다 — `image_reference`는 provider 호출 입력으로만 사용. 이미지·원문 저장 구현 없음, 앱 코드에 logger/print 없음. 서명 URL은 응답·오류 메시지(`_safe_error` redaction)·테스트 산출물에서 금지. OCR 원문은 응답으로만 나가고 AI①이 별도 보관하지 않는다 | 동의(구현 완료) |
| R-12 | 1·2단계 착수 조건과 테스트 항목(5장), Vision 호출 최대 10회 | **동의.** S1-1(인증 검사 구현·테스트) 완료 — D-03 동봉. S2-1의 데모 보완 4종(응답 필드 정합성·URL 노출 제거·결과 파일 충돌 방지·사용량 기록/동시 보호)은 PHASE 9-9C.4~C.8에서 구현. 2단계 Vision 호출 최대 10회 상한 동의 — **단, 시작은 S2-2(billing 잔량 확인 + 팀장 승인) 후** | 동의 |
| R-13 | 예상 일정: S1-1 완료일, S2-1 완료일 | **값 기재:** S1-1 완료일 **2026-11-03**(내부 인증 guard + 경계 테스트 26건, PHASE 9-9C.8). S2-1 완료일 **2026-11-13 (잠정)** — S2-1의 AI① 몫(응답 필드 정합성·URL 비노출·결과 파일 충돌 방지·사용량 기록/동시 보호·provider 1회 호출)은 PHASE 9-9C.4~C.8에서 구현·테스트 완료됐고, 남은 것은 S2-2(billing 잔량 확인 + 팀장 승인) 후 Vision 실연결 1회다. 승인 지연 시 순연하며 변경 시 즉시 통보한다 | 결정(잠정) |

**추가 정리(협의서 항목 외)**: `/docs`·`/redoc`·`/openapi.json`은 FastAPI 기본 라우트라
인증 guard 밖에서 키 없이 스키마가 노출됐다. 데이터 라우트가 아니므로 계약 위반은
아니지만 내부 서비스 불필요 노출을 줄이기 위해 **기본 비노출(404)** 로 바꿨다. 로컬
개발에서만 `AI1_ENABLE_DOCS=1` 로 켠다. Backend가 스키마를 자동 fetch해야 한다면
이 환경변수를 운영에 켜지 말고 별도 요청으로 알려 달라.

---

## 2. §8.2 제출물 (D-01 ~ D-05)

### D-01 — AI① 오류 코드 전체 목록

HTTP 상태는 AI① 실제 코드(app)의 값이다. 4.6.3 제안 표와 **일치**한다(R-08).

인증 (협의서 §3.1):

| 코드 | HTTP | 의미 | Backend 결과(4.6 기준) |
|---|---|---|---|
| `E_AUTH_INVALID_KEY` | 401 | `X-Internal-Api-Key` 누락·불일치 | OCR `FAILED` / 분석 `FAILED`, 재시도 없음 |
| `E_AUTH_NOT_CONFIGURED` | 503 | AI① 서버에 키 미설정(fail-closed) | OCR `FAILED` / 분석 503 `AI_NOT_CONFIGURED` |

OCR (협의서 §4.6.3):

| 코드 | HTTP | 의미 | Backend 결과 |
|---|---|---|---|
| `E_OCR_NOT_CONFIGURED` | 503 | OCR provider 미등록 | OCR `UNAVAILABLE` |
| `E_OCR_API_DISABLED` | 503 | Vision API 비활성화 | OCR `FAILED` |
| `E_OCR_AUTH_FAILED` | 503 | Vision 자격 증명/IAM 실패 | OCR `FAILED` |
| `E_OCR_BILLING_REQUIRED` | 503 | billing 미설정 | OCR `FAILED` |
| `E_OCR_USAGE_LIMIT_REACHED` | 429 | 로컬 가드(누적 40회)·quota 상한 | OCR `FAILED` |
| `E_OCR_IMAGE_FETCH_FAILED` | 422 | 이미지 링크 다운로드 실패(만료·404) | OCR `FAILED` |
| `E_OCR_UNSUPPORTED_IMAGE` | 422 | 지원하지 않는 참조(비-HTTPS)·형식/MIME | OCR `FAILED` |
| `E_OCR_PROVIDER_ERROR` | 502 | 그 외 Vision 일시 오류 | OCR `FAILED` |

분석·기타:

| 코드 | HTTP | 의미 |
|---|---|---|
| `E_UNKNOWN_SKILL_CODE` | 422 | crosswalk에 없는 Backend `SKL-*` 코드 (학생 422 `SKILL_NOT_SUPPORTED`로 승계될 것) |
| `E_UNKNOWN_SKILL` / `E_SKILL_NOT_TRACKABLE` | 422 | 내부 taxonomy에 없는 skill / BKT 비대상 skill |
| `E_EMPTY_SOLUTION` / `E_BAD_PROBLEM` / `E_UNRECOGNIZED` / `E_UNVERIFIABLE` | 422 | 풀이 비어 있음 / 문제식 파싱 불가 / 전량 인식 실패 / 동치 판정 불가 — **오답 취급 금지 정책** (`E_UNRECOGNIZED`·`E_UNVERIFIABLE`는 `detail.analysis_status:"UNKNOWN"` 동봉) |
| `E_NOT_EQUATION` | 422 | 파싱은 됐지만 등식이 아님(표현식 전용 문제). 재시도해도 결과 동일 → 저장 없이 "지원하지 않는 문제 유형" 안내 (2026-11-10 A5) |
| `E_INPUT_TOO_LARGE` | 413 | 문제식 2000자·풀이 2만자·30단계 초과 |
| `E_INVALID_BACKEND_ID` / `E_INVALID_STUDENT_ID` | 422 | 정규화 불가 식별자 (빈 값·bool 등) |
| `E_ATTEMPT_CONFLICT` | 409 | 같은 `attempt_id` + 다른 본문 재전송 |
| `E_RESOURCE_CONFLICT` / `E_SOLUTION_PROBLEM_MISMATCH` / `E_DUPLICATE_ID` | 409 | 등록 충돌 계열 |
| `E_ATTEMPT_NOT_FOUND` / `E_ANALYSIS_NOT_FOUND` | 404 | 이력 조회 대상 없음 |
| `E_REFERENCE_INTEGRITY` | 422 | 저장소 참조 무결성 위반 |
| `E_INVALID_INSTANT` / `E_INVALID_TIME_RANGE` | 422 | 이력 GET의 `from`/`to` 파라미터 형식·구간 오류 |
| (FastAPI 스키마 검증) | 422 | 요청 형식 오류 — `detail`이 배열(4.1 "Backend는 배열도 처리"와 정합) |

오류 본문 표준: `{"detail":{"code":"...","message":"..."}}` (인증·OCR·분석 공통).
재시도 규칙은 Backend §4.6 기준 그대로 — AI①은 어느 오류도 AI① 측에서 재시도하지 않는다.

### D-02 — OCR raw 모드 응답 예시 2개 (fake, parser 출력 형식)

실행 조건 양식: `POST /api/v1/attempts/{attempt_id}/ocr`, 요청 본문
`{"image_reference":"https://<Backend PUBLIC_BASE_URL>/.../page1.jpg?...","page_index":0}` +
`X-Internal-Api-Key` 헤더. **이 예시는 fake provider 출력(실제 Vision 호출 아님 — D-02 양식 허용).
공통으로 `image_reference`는 응답 어디에도 없다.**

PARTIAL (텍스트 인식 성공, 문제/풀이 경계 미분리):

```json
{ "ocr_result_id":"A1#ocr-p0","attempt_id":"A1",
  "status":"PARTIAL","representation":"raw_text",
  "model_version":"Google Cloud Vision DOCUMENT_TEXT_DETECTION (managed model)",
  "ocr_confidence":0.89,"problem":null,"regions":[],"solution_steps":[],
  "raw_text":"다음 식을 풀어라\n2(x-3)=6\n2x-6=6",
  "raw_lines":[{"line_index":0,"page_index":0,"text":"다음 식을 풀어라","confidence":0.97,
                "bbox":{"x":20.0,"y":20.0,"width":136.0,"height":24.0},
                "bounding_polygon":[[20,20],[156,20],[156,44],[20,44]],"derived":true},
               {"line_index":1,"page_index":0,"text":"2(x-3)=6","confidence":0.91,
                "bbox":{"x":20.0,"y":70.0,"width":96.0,"height":24.0},
                "bounding_polygon":[[20,70],[116,70],[116,94],[20,94]],"derived":true},
               {"line_index":2,"page_index":0,"text":"2x-6=6","confidence":0.89,
                "bbox":{"x":20.0,"y":120.0,"width":96.0,"height":24.0},
                "bounding_polygon":[[20,120],[116,120],[116,144],[20,144]],"derived":true}],
  "page_index":0,
  "warnings":["Raw OCR preview only: problem/solution segmentation, printed/handwritten labels, and LaTeX are not inferred.",
              "Google Vision does not expose a stable model version in the response."] }
```

> `status`는 AI① 경계가 내용에서 파생한다(텍스트 있음 → PARTIAL). Backend가 4.3의
> "status 없으면 원문 유무로 PARTIAL/UNREADABLE" 폴백을 소유해도 결과는 같다.

UNREADABLE (빈 종이류):

```json
{ "ocr_result_id":"A9#ocr-p0","attempt_id":"A9",
  "status":"UNREADABLE","representation":"raw_text",
  "model_version":"Google Cloud Vision DOCUMENT_TEXT_DETECTION (managed model)",
  "ocr_confidence":null,"problem":null,"regions":[],"solution_steps":[],
  "raw_text":"","raw_lines":[],
  "page_index":0,
  "warnings":["Raw OCR preview only: problem/solution segmentation, printed/handwritten labels, and LaTeX are not inferred.",
              "Google Vision does not expose a stable model version in the response.",
              "Google Vision returned no readable text."] }
```

> raw 모드에서 COMPLETE가 오지 않는 것은 계약(#15)대로 문제없다. 분석 요청은
> 학생이 APP에서 원문을 확인·보정한 후 Backend가 생성한다(#17) — AI①이 문제/풀이를
> 나누거나 `/analyze`로 자동 전달하지 않는다.

### D-03 — 인증 검사 테스트 결과

실행: `.venv\Scripts\python -m pytest tests/test_internal_auth.py -q` → **26 passed**.
환경변수의 키 값은 테스트용 더미(`dummy-test-key-old`, `dummy-test-key-new`)만 사용했다.

| 요구 케이스(4.6.3/§3.1) | 요청 | 결과 | 본문 |
|---|---|---|---|
| 키 없음 | 보호 경로 중 하나에 `X-Internal-Api-Key` 헤더 누락 | **401** | `{"detail":{"code":"E_AUTH_INVALID_KEY","message":"..."}}` |
| 틀린 키 | 동 경로 + 헤더 `"definitely-wrong-dummy"` | **401** | 동일 코드 |
| 키 미설정 | `AI1_INTERNAL_API_KEYS` 미설정/빈 값/구분자만 | **503 (fail-closed)** | `{"detail":{"code":"E_AUTH_NOT_CONFIGURED","message":"..."}}` |
| 정상 | 동 경로 + 더미 키 | **200** | 엔드포인트 정상 응답 |
| 회전(구·신) | 기존 키와 내부 키 동시 설정 | 둘 다 **200** | 재시작 없이 반영(환경변수 매요청 재조회) |
| `/health` | 헤더·키 없이 GET `/health` | **200** | `{"status":"ok"}` — 유일한 예외 경로 |
| 인증 선행 | 401이 나는 `/ocr` 호출에서 provider 초기화·호출·budget ledger 예약 추적 | **0회** | `require_ocr_provider`/`from_environment` 미실행, `FileLock` 진입 0회(예약 0회) |
| 문서 비노출 | 헤더 없이 GET `/docs`·`/redoc`·`/openapi.json` | **404** | 기본 비노출. 로컬 개발만 `AI1_ENABLE_DOCS=1` 로 opt-in |
| 키 비노출 | 401/503 응답 전체 텍스트에 키 값 삽입 검사 | **노출 없음** | 메시지는 헤더·환경변수 이름만 언급 |
| constant-time | 코드 정적 검사(inspect) | **통과** | `secrets.compare_digest` 사용, 전체 키 비교(조기 탈출 없음) |
| 본문 검증보다 인증 선행 | 잘못된 본문(스키마 위반) + 키 누락 | **401** | 422보다 인증이 먼저 나온다 |

테스트 소스: `tests/test_internal_auth.py` — guard의 dependency 우회 방식은
`tests/conftest.py`(기존 API 테스트 호환용)를 이 파일에서만 제거해 실 guard를 검증한다.

### D-04 — `REVIEW_REQUIRED`·`UNKNOWN` 입력 예시 각 1개

실행 양식: `POST /api/v1/attempts/{attempt_id}/analyze`, 헤더 `X-Internal-Api-Key`.
problem은 1단계 테스트 표준(`problem_latex: 2(x-3)=6`). fake 분석 — 실제 Vision 호출 아님.

REVIEW_REQUIRED (일부 단계만 판정 보류 → 사람 확인 필요):

```json
POST /api/v1/attempts/A4/analyze
{
  "problem_id": "P1",
  "problem_latex": "2(x-3)=6",
  "problem_skills": ["SKL-1-004"],
  "student_id": "S1",
  "solution_text": "2(x-3)=6\n???\n2x-3=6"
}
```

예상 응답(테스트에서 고정된 값, 200 + `analysis_status:"REVIEW_REQUIRED"`):

- `analysis_status`: `"REVIEW_REQUIRED"`
- `correct`: `false` — 풀이 안에 확정된 오류 단계가 존재하므로 오답 확정 영역만 반영
- `first_error_step_index`: `3`
- `unrecognized`: `[2]` — 2단계(`???`)는 인식 실패, 오답으로 취급하지 않음
- `valid`: `[true, null, false]` — 정상/판정 보류/오류
- `errors[]`: 확정 단계의 taxonomy 기반 레코드 (`step_no` 1-based, `error_type`,
  `error_subtype`, `description_ko`, `evidence_latex`, `confidence`,
  `misconception_ids` — AI① 내부 ID로 Backend에서 FK에 쓰지 않음)

UNKNOWN (신뢰 가능한 단계 판정이 하나도 없음 → 저장 없음, 재시도 가능):

```json
POST /api/v1/attempts/G4/analyze
{
  "problem_id": "P1",
  "problem_latex": "2(x-3)=6",
  "problem_skills": ["SKL-1-004"],
  "student_id": "S1",
  "solution_text": "보통은 이렇게 푼다고 합니다"
}
```

예상 응답(**422**, 4.5의 UNKNOWN 처리 형식):

```json
{ "detail": {
    "code": "E_UNRECOGNIZED",
    "message": "모든 풀이 단계를 인식하지 못했습니다. 직접 입력으로 다시 시도해 주세요.",
    "analysis_status": "UNKNOWN" } }
```

> 풀이 전량이 동치 판정 불가이면 같은 형식으로 `E_UNVERIFIABLE` 코드가 나오고,
> 200 + `analysis_status:"UNKNOWN"`이 되는 경로도 있다 — 4.5 "둘 다 같은 처리"와 정합.
> UNKNOWN은 저장되지 않는다(오개념 집계·숙련도 부작용 0).

### D-05 — AI① 개발 서버 주소

**현재 공개 주소(2026-11-10, ngrok 무료 터널): `https://vividness-pep-unboxed.ngrok-free.dev`**

- 로컬: `http://127.0.0.1:8001`
- 구동: `.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8001`
  (서버 실행 후 별도 터미널에서 `ngrok http 8001`)
- **무료 플랜은 터널을 재시작하면 주소가 바뀐다** — 변경 시 Backend에 즉시 재통보한다.

공개 주소에서 실제 확인한 결과(2026-11-10, API 클라이언트 기준):

| 확인 | 결과 |
|---|---|
| `GET /health` (키 없이) | **200** `{"status":"ok"}` |
| `POST /api/v1/attempts/{id}/analyze` (키 없이) | **401** `E_AUTH_INVALID_KEY` |
| `POST /api/v1/attempts/{id}/analyze` (키 있음, 정상 본문) | **200** `analysis_status:"ANALYZED"` (0.52초) |
| `GET /docs` | **404** — 기본 비노출 |
| `POST /api/v1/attempts/{id}/ocr` | provider 미설정 상태에서 422/503 — Vision 실연결은 S2-2 승인 후 |

**브라우저 주의:** 무료 플랜은 브라우저 User-Agent 요청에 ngrok 안내 HTML을
200으로 끼워 넣는다. API 클라이언트(curl·httpx)는 영향이 없고, 브라우저로 직접
확인할 때만 `ngrok-skip-browser-warning: true` 헤더를 붙인다.

### D-06 — Backend 서버 공개 주소 (참고)

**`https://frugally-unsubtle-luminous.ngrok-free.dev/api/v1`** (2026-11-10 Backend 제공,
고정 주소로 회신됨)

- **AI①은 이 주소를 호출하지 않는다** (ADR-01: AI① → AI②/Backend 호출 없음).
  연동 방향은 항상 Backend → AI①이다.
- AI① 코드에서 쓰이는 곳은 S2-3의 **이미지 서명 URL 호스트 허용목록** 후보다.
  이때는 스킴·경로·포트를 뺀 **bare hostname**만 넣는다:
  `AI1_OCR_ALLOWED_HOSTS=frugally-unsubtle-luminous.ngrok-free.dev`
- **확인 필요:** 실제 이미지 서명 URL이 이 호스트로 발급되는지(스토리지가 별도
  호스트일 수 있음). 승인 전에는 Vision 실호출을 하지 않으므로 설정만 준비한다.

---

## 3. 미결 목록 (연동 차단과 무관한 항목 포함)

1. **R-03 키 전달 방법** — **결정:** 대면 직접 입력(기본), 비대면 시 비밀번호 관리자
   1회 열람 링크. Backend 이의 없으면 확정.
2. **R-13 일정** — **기재:** S1-1 2026-11-03(완료) / S2-1 2026-11-13(잠정, S2-2 승인 의존).
3. **D-05 서버 주소** — **기재:** ngrok 공개 주소 `https://vividness-pep-unboxed.ngrok-free.dev`
   (2026-11-10, 재시작 시 변경). 공개 주소에서 `/health` 200·보호 경로 401·정상 분석 200 확인 완료.
4. **S1-3 API 키 전달** — **발급 주체 변경: AI① 발급.** Backend가 만든 키는 폐기하고,
   AI①이 43자(`[A-Za-z0-9-_]`) 키를 발급해 별도 채널로 전달한다(값은 문서·대화·깃에
   남기지 않음). Backend는 수령한 1개를 `AI1_INTERNAL_API_KEYS`에 넣고 분석 API를
   실제 호출한다. 키 미설정 시 보호 경로는 fail-closed 503 `E_AUTH_NOT_CONFIGURED`.
5. **S2-2 billing 잔량 확인 + 팀장 호출 승인(2단계 최대 10회)** — Vision 실호출 전
   필수 조건. 현재 GCP/billing 변경·실호출·잔량 확인은 전부 수행하지 않았다.
6. **S2-3 허용 호스트 값** — `AI1_OCR_ALLOWED_HOSTS`는 환경변수로 확정됐으나
   실제 Backend `PUBLIC_BASE_URL` 호스트 값은 운영자가 설정한다.
7. **R-05의 28초·분석 8초 실측** — 실호출 0회라 근거 자료 없음. 2단계 O-01에서 확인.
8. **분석 응답 최종 계약(`detail` 배열 vs 단일 오브젝트 등)** — 이전 회신 기준(§4.5
   각주) 유지, Backend 실제 코드 대조는 Backend 쪽 남은 작업(§7)에 포함.

## 4. 전송 전 체크리스트

- [x] **빈칸 0개 확인** — R-03(대면 직접 입력)·R-13(S1-1 2026-11-03 / S2-1 2026-11-13
  잠정)·D-05(`https://vividness-pep-unboxed.ngrok-free.dev`, 2026-11-10) 모두 값이 채워졌고, 잠정치는 "잠정"으로 표기됨
- [x] **D-05 서버 주소 확인** — 공개 주소에서 `GET /health` 200(키 없이), 보호 경로 키 없이
  401, 정상 분석 200, `/docs` 404 — 2026-11-10 확인 완료. 무료 터널은 재시작 시 주소가
  바뀌므로 변경 시 재통보
- [x] **키 전달 방법 확인(R-03)** — 도구 이름 없이 성립하는 "대면 직접 입력"으로 결정
  (비대면 전환 시에만 도구 이름 기재)
- [x] **A-01~A-10 로컬 근거 동봉** — 대조표 §14.24 커버리지 매트릭스 참조
  (로컬 증명·미측정·담당 구분). A-09·A-10의 Backend 몫 항목은 회신에 포함하지 않는다
- [x] **S1-2 직전 preflight** — 2026-11-08 실행 결과 **3/6 OK**. 미충족 3건은
  `AI1_OCR_PROVIDER`·`GOOGLE_APPLICATION_CREDENTIALS`·`AI1_INTERNAL_API_KEYS` 로,
  S1-3(실키)·S2-2/S2-3(provider·호스트) 대기 상태에서 **예상된 값**이다
  (timeout·allowlist·docs 는 OK, 값 비노출·네트워크 0회). 2026-11-10 키 발급·주입 후
  서버 프로세스에는 키가 설정됐고, 남은 미충족 2건은 S2-2/S2-3 대기 항목이다
- [x] **제약 준수 서술** — Vision/Mathpix 실호출 0회, GCP/billing 변경 없음,
  서비스 계정 미개봉, 실제 키 아직 미수령(더미 키 테스트만), staging 커밋 없음

## 5. 검증 기록 (2026-11-03 · 2026-11-08 갱신)

- 전체 pytest: **1713 passed**, 1 warning (Starlette/httpx deprecation) — 네트워크·
  실제 SDK client/RPC 호출 없음(fake provider/더미 키만). 2026-11-03 기록은 1642 passed.
- D-05 공개 경로 실측(2026-11-10, ngrok 무료 터널): `GET /health` 200, 보호 경로 키 없이
  401 `E_AUTH_INVALID_KEY`, 키 있는 정상 분석 200(`ANALYZED`, 0.52초), `/docs` 404.
  API 클라이언트 기준이며 브라우저 요청은 ngrok 안내 HTML이 끼어든다.
- **S1-2 종료(2026-11-10):** Backend PC에서 `GET /health` **200(약 0.4초)**, 인증 401 동작,
  `/docs` 404 확인. Backend가 `AI1_BASE_URL`에 주소 등록, `AI1_OCR_ENABLED=false` 유지.
- **키 로테이션(2026-11-10):** AI①이 43자 키를 새로 발급해 서버 프로세스 환경변수로만
  주입하고 재기동 후 공개 경로를 재검증했다 — 키 없이 **401**, 새 키로 **200**
  (`ANALYZED`, 0.76초), `/health` 200, `/docs` 404. 값은 문서·대화·깃에 없음.
- `pyflakes app tests eval`: 0건. `pip check`: 문제 없음 (상동).
- `data/skills.json`·`error_types.json`·`misconceptions.json`·`tests/golden/ocr_handoff.json`
  파싱 검증 통과(분류 taxonomy 불변).
- `git diff --check`: 0 (정상). staging 커밋 없음.
- Google Vision/Mathpix 실호출 0회, GCP/billing/gcloud 변경 0, 서비스 계정 JSON 미개봉,
  실제 학생 이미지 미사용.

---

## 6. Backend 확인 요청 (7건 — 한 번에 회신 요청)

AI① 코드는 합의 범위에서 완료됐다. 아래 7건은 **AI①이 임의로 정하면 계약이 굳는**
항목이라 Backend/기획 확인 후 착수한다. 각 항목에 답만 적어 회신해 주면 된다.

| # | 질문 | 이유 | 차단 | 담당 |
|---|---|---|---|---|
| Q1 | 위 공개 주소(`https://vividness-pep-unboxed.ngrok-free.dev`)에서 `GET /health` → 200인가? (외부 노출이라 방화벽·LAN 조건 불필요) | D-05 도달 확인이 S1-2 착수 조건 | **차단** | Backend |
| Q2 | `AI1_INTERNAL_API_KEYS`에 넣을 키를 생성해 R-03(대면) 방식으로 전달 가능한가? 키 길이·문자 제약이 있는가? | AI①은 임의 문자열(쉼표 다중) 허용 — 제약이 있으면 맞춘다 | **차단** | Backend |
| Q3 | `concepts`/`problem_concepts`의 **공식 concept ID 시드**를 받을 수 있는가? | AI① taxonomy는 skill_id 체계 — 시드 없이는 skill↔concept 매핑 불가 | **차단(데이터)** | Backend/기획 |
| Q4 | `weakness_score`는 Backend가 `1 - mastery`로 파생하는가, 아니면 AI① 응답 필드로 필요한가? | AI①은 현재 `mastery`만 제공 | 높음 | Backend |
| Q5 | 학생 단위 **숙련도 목록** API가 필요한가? 필요하면 응답 필드(`skill_id`, `mastery`, `state`, `updated_at`)와 호출 시점은? | 현재 `/students/{id}/history`·`/misconceptions/top`만 있음 | 중간 | 공동 |
| Q6 | 앱 r36~r43 **이미지 multipart 업로드**는 서명 URL 방식으로 대체되어 폐기해도 되는가? | 폐기 확정 시 AI①에 이미지 입력 경로를 만들지 않는다 | 중간 | Backend/기획 |
| Q7 | 초기 Knowledge State 시드(r27)는 어떤 API로 받는가? (예: `POST /api/v1/students/{id}/states/seed`) | 진단평가 결과 → 최초 상태 생성 트리거 계약 필요 | 중간 | 공동 |

> 답변 시 **키 값 자체는 이 문서에 기재하지 않는다**(R-03대로 대면 전달). 위 표의
> 각 행 끝에 "→ 답:" 형태로만 적어 주면 된다.
>
> 현재 AI① 서버에는 **AI①이 발급한 43자 키**가 설정돼 있다(값은 문서·대화에 남기지
> 않음). Backend가 만든 키는 폐기하고, 이 키를 별도 채널로 받아 그대로 사용한다.

---

## 7. Backend 회신 반영 (2026-11-10)

| 항목 | Backend 답 | AI① 조치 |
|---|---|---|
| Q1 | LAN 접속 불가(10.80.x.x 대역). 외부 HTTPS 주소 필요 → ngrok 권장 | **ngrok 공개 주소 발급 완료:** `https://vividness-pep-unboxed.ngrok-free.dev` (2026-11-10). `/health` 200 실측. 주소 재통보 후 Backend 확인 대기 |
| Q2 | 키 1개(43자, 영문 대소문자·숫자·`-`·`_`), 대면 전달, 쉼표·공백 없음 | 수령 시 `AI1_INTERNAL_API_KEYS`에 그 1개만 설정. 43자 형식은 현재 구현과 호환(길이·문자 제약 없음) |
| Q3 | Concept 시드 불필요. Backend가 `problem_skills`(SKL) 전송, Skill↔Concept는 Backend Seed. 단 `official_skill_codes`/`official_misconception_codes`는 Seed 코드로 응답해야 함. CSV 2종 첨부 | **접수 완료**(94 skill/42 concept/23 misconception). 매핑 후보표 작성 — `docs/AI1_Backend_코드_매핑_후보.md`. **공식 반영은 승인 후** |
| Q4 | Backend가 `1 - mastery` 계산. `weakness_score` 필드 불필요 | 변경 없음 (AI①은 `mastery`만 제공) |
| Q5 | 숙련도 목록 API 불필요. `/students/{id}/history`·`/misconceptions/top`도 Backend 미호출. **R-11 관점에서 학생 데이터 별도 보관 여부 확인 요청** | **답변:** AI①은 학생 데이터를 **디스크·DB에 저장하지 않는다.** 저장소는 프로세스 메모리(in-memory)뿐이라 재시작 시 소멸하고, 이미지·서명 URL·원문을 별도 보관하지 않는다. 디스크 산출물은 OCR 호출 예산 ledger(횟수 카운터, 학생 데이터 아님)뿐이며 Vision 미사용 시 생성되지 않는다. 해당 2개 라우트는 미사용이므로 **원하면 제거 가능**(계약 변경이라 승인 후) |
| Q6 | multipart 폐기 확정 | 이미지 입력 경로를 만들지 않는다 (서명 URL만) |
| Q7 | 초기 Knowledge State 시드는 불필요(Backend가 AI② 결과로 생성) | 구현하지 않는다 |
| Q8 | (미질의) skill 1개↔SKL 다수 대응 | 응답 필드가 복수(`official_skill_codes: list`)이므로 **리스트로 전송** — 별도 승인 불필요 |
| S1-2 | 2026-11-10: Backend PC `/health` 200(0.4초)·401 동작·`/docs` 404, `AI1_BASE_URL` 등록 | **S1-2 종료.** 이후 주소·키가 바뀌면 재통보 |

**남은 1건:** AI① 발급 키를 Backend가 별도 채널로 수령 → `AI1_INTERNAL_API_KEYS` 설정 →
분석 API 실제 호출(S1-3) → 응답/로그 회신으로 O-06(실서버 로그) 확인.

### 7-1. S1-3 회신 본문 (2026-11-10)

Backend의 S1-3 결과 보고 + 다항식 지원/매핑 3문항에 대한 답변은
`docs/AI1_Backend_회신_S1-3_매핑확인.md` 에 정리했다. 요지:

- API Key 1회 교체 **동의** — 교체 기간 구·신 키 동시 수용, **재시작 필요**.
- S1-3 8건 원인 확인: `E_BAD_PROBLEM`은 **문제식이 등식이 아니라서**(다항식 미지원 아님),
  `E_UNRECOGNIZED`는 **풀이 줄이 등식이 아니라서**, `E_UNKNOWN_SKILL_CODE`는 역변환
  가능 코드가 2개뿐이라서.
- `official_skill_codes` 공백은 **관측 Skill 기준 생성** + **`problem_id` 재사용 시
  저장본 우선** 두 원인.
- 다항식은 **방정식**이면 동작하지만, **표현식 전용/항등식**은 미지원·신뢰 불가.
  일정은 기획 협의 후(P0/P1/P2 제안).
- 남은 매핑 2건(1:N 전부 전송, 미매핑 MIS 유지) **확정**.
- 후보 15건 승인 요청, `SKL-2-037`·`MIS-2-001/002`의 시드 포함 여부 확인 요청.

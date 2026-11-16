# AI① Learning Intelligence — 인수인계

- 기준일: 2026-11-02
- 로컬 프로젝트: `C:\Users\choi\Desktop\learning-intelligence`
- 역할: AI① 개발 보고 검토, 기술 결정 정리, OpenCode 작업 지시문 작성
- 응답 언어: 한국어

## 1. 제품 목표와 책임 경계

학생은 STEPi 문제은행이 아니라 **자기가 사용하던 문제집**에서 문제와 풀이가 함께 보이는 사진을 제출한다.

AI① 목표 파이프라인:

```text
문제+손글씨 풀이 이미지
 → 문제/풀이 영역 분리·인식
 → 수식/텍스트 OCR
 → 풀이 Step 구조화
 → Step별 검증
 → 최초 오류 및 독립/연쇄 오류 분석
 → 오류 유형·Misconception·Skill 분류
 → confidence 포함 구조화 결과 반환
```

- **Backend**: 인증, 원본 이미지·Attempt 저장, 공식 STP/SKL/MIS 기준정보, Student State/mastery/weakness 영속화·최종 갱신, AI② 호출을 담당한다.
- **AI①**: 문제/풀이 인식, Step 분석, First Error·오류 근거·Skill/MIS code 후보·confidence를 반환한다. Backend DB에 직접 접근하지 않는다.
- **AI②**: AI① 결과와 Backend 학생 상태를 받아 힌트·복습·변형 문제·동기 개입 등을 결정한다.
- AI①은 학생에게 직접 정답·힌트를 생성하지 않는다.

## 2. 현재 구현 상태

현재 AI①은 **텍스트 기반 분석 백엔드**이며, Google Cloud Vision 기반 OCR raw-text preview가 opt-in으로 연결돼 있다. 이미지 업로드와 문제/풀이 영역 분리는 하지 않는다.

- 문제식/풀이 텍스트 파싱, SymPy 등식 검증, 복잡도·입력 크기 가드
- First Error, 다중 독립 오류, 일부 downstream 오류 억제
- 정답 풀이와 학생 풀이의 단계 정렬: weighted LCS, `identical_text` / `normalized_text` / `math_equivalent`
- Rule/BKT Knowledge State 엔진. 운영 DB 상태의 source of truth는 Backend
- In-memory Repository, Attempt 분석/멱등성/분석 이력/학생 이력/TOP/기간 필터 API
- `AnalysisStatus`: `ANALYZED`, `REVIEW_REQUIRED`, `UNKNOWN`
- 실패·미인식은 오답으로 간주하지 않는다. 단순 계산 오류는 Misconception을 자동 부여하지 않는다.
- OCR: `AI1_OCR_PROVIDER=google_vision`일 때만 Cloud Vision `DOCUMENT_TEXT_DETECTION`을 호출한다. 결과는 `representation=raw_text`; problem/풀이 분리·LaTeX는 추측하지 않는다.
- OCR 응답에는 이미지 참조 필드가 없다 — 요청의 signed URL과 hash fingerprint를 포함해 어떤 형태로도 되돌려 보내지 않으며, 원본 URL은 오류에서 redaction한다.
- 내부 인증(PHASE 9-9C.8): `X-Internal-Api-Key` 헤더를 `/health` 제외 전부에서 검사한다. 환경변수 `AI1_INTERNAL_API_KEYS`(쉼표 다중 키), 누락/불일치 401 `E_AUTH_INVALID_KEY`, 미설정 fail-closed 503 `E_AUTH_NOT_CONFIGURED`, `secrets.compare_digest` 상수 시간 비교. 인증은 provider 초기화·호출·budget ledger 예약보다 선행한다. 테스트는 더미 키만 사용한다.
- 실제 Google Vision API 호출·billing 설정·한국어 수식 이미지 정확도 측정은 하지 않았다. 앱과 eval probe는 프로젝트 로컬 누적 40회 가드를 공유한다. 이는 billing account quota가 아니다.

지원 범위는 제한된 수식/등식 풀이에 중심을 둔다. `split_steps()`는 개행·화살표·번호표식으로 텍스트를 분리하며, 이미지에서 논리적인 풀이 단계를 추출하는 기능은 없다. Backend의 전체 공통수학1 Skill 목록을 모두 분석할 수 있다고 가정하지 않는다.

## 3. 현재 API

| Method | 경로 | 상태 |
|---|---|---|
| GET | `/health` | 구현; 프로세스 상태만 확인 |
| POST | `/analyze-solution` | Legacy 텍스트 풀이 분석 |
| POST | `/api/v1/attempts/{attempt_id}/analyze` | 텍스트 Attempt 분석·저장·멱등성 |
| POST | `/api/v1/attempts/{attempt_id}/ocr` | Google Vision raw-text 미리보기 (명시적 opt-in; 미설정 시 503) |
| GET | `/api/v1/attempts/{attempt_id}/analyses` | Attempt 분석 이력 |
| GET | `/api/v1/attempts/{attempt_id}/analyses/{analysis_id}` | 저장 시점 분석 상세 |
| GET | `/api/v1/students/{student_id}/history` | 페이지네이션·기간 필터 학생 이력 |
| GET | `/api/v1/students/{student_id}/misconceptions/top` | 기간 필터 지원 오개념 집계 |

`/analyze-solution`과 신규 Attempt API는 응답 계약이 서로 다르다. API prefix도 혼재한다. 새 Backend adapter를 붙일 때 Legacy 응답을 변경하지 않는다.

**인증**: `X-Internal-Api-Key` 헤더 검사가 `/health`를 제외한 위 표의 전부에 걸려 있다(협의서 §3.1). 헤더 누락·불일치는 401 `E_AUTH_INVALID_KEY`, 서버 키 미설정은 503 `E_AUTH_NOT_CONFIGURED`(fail-closed). 키 환경변수는 `AI1_INTERNAL_API_KEYS`. `/docs`·`/redoc`·`/openapi.json`은 기본 404이며 로컬 개발만 `AI1_ENABLE_DOCS=1`로 켠다.

## 4. 중요한 의미·보호 정책

- `correct=true`는 “제출된 풀이에서 오류가 검출되지 않음”이지 완전한 정답 증명이 아니다.
- `error_step` / `first_error_step_index`는 1-based.
- `valid`/Step 상태는 `true` 정상, `false` 오류, `null` 판정 보류다.
- `REVIEW_REQUIRED`는 일부 판정 가능한 결과가 있으나 보류/미인식 단계가 남은 상태다.
- `UNKNOWN`은 신뢰할 수 있는 판정이 하나도 없는 상태이며 현재 `E_UNRECOGNIZED`/`E_UNVERIFIABLE` 422 응답의 `detail.analysis_status`에 들어간다.
- 같은 `attempt_id`+같은 성공 제출 재전송은 기존 분석 결과를 반환하고 BKT/KnowledgeObservation을 중복 적용하지 않는다. 다른 본문은 409 `E_ATTEMPT_CONFLICT`; 새 학생 제출은 새 `attempt_id`를 사용한다.
- 분석 실패는 Attempt/분석 이력으로 저장하지 않는 정책이다. `attempted_at`은 첫 요청의 서버 UTC 접수 시각이며 실제 풀이 시각과 다를 수 있다.
- 이력/오개념 GET은 분석이나 Knowledge State 변경을 하지 않는다.

## 5. taxonomy 및 Backend 코드 연동

AI① 내부 taxonomy는 `data/skills.json`, `data/error_types.json`, `data/misconceptions.json`에 있다. 내부 Skill/MIS ID와 Backend 공식 code는 같은 값이 아니다. AI① taxonomy ID와 BKT key는 변경하지 않는다.

확인된 Backend 자료:

- `STEPi 기능명세서 (1).xlsx` 및 `AI1_공식_STP_SKL_MIS_관계자료 (1).xlsx`
- `STEPi2026/BE`의 `app/schemas/ai/analysis.py`, `app/schemas/ai/tutor.py`, `seed.py`
- 최신 관계자료는 STP-2-017, SKL-2-036~040, MIS-2-001~004를 포함한다. 공식 seed 반영·최종 확정 여부는 Backend와 확인한다.
- STP는 Concept code, SKL은 Skill code, MIS는 Misconception code다. Backend가 승인된 SKL의 STP 관계를 조회해 Concept을 도출한다.

### 공식 자료 위치와 주의

- 기능명세서 원본: `C:\Users\choi\Downloads\STEPi 기능명세서 (1).xlsx`
- 기본 관계자료: `C:\Users\choi\Documents\카카오톡 받은 파일\AI1_공식_STP_SKL_MIS_관계자료.xlsx` (STP 42 / SKL 94 / 선수관계 92 / MIS 23)
- 일차방정식 확장 후보: `C:\Users\choi\Documents\카카오톡 받은 파일\AI1_공식_STP_SKL_MIS_관계자료 (1).xlsx` (STP-2-017, SKL-2-036~040, MIS-2-001~004 추가)
- Backend↔AI① 계약 초안: `C:\Users\choi\Documents\카카오톡 받은 파일\STEPi_Backend_AI1_연동_API_명세서_v1.0.docx`
- AI② 자료: `C:\Users\choi\Documents\카카오톡 받은 파일\STEPi_AI2_API_명세서_v1.0.docx`, `STEPi_AI2_기능명세서_v1.0.docx`

관계자료의 `(1)` 확장 행이 Backend의 canonical seed workbook에 병합됐는지 확인해야 한다. Backend `seed.py`의 공개 `EXPECTED_COUNTS`는 기존 42/94/92/94 기준이므로, 확장 데이터를 seed에 반영할 때 검증 건수도 함께 갱신해야 한다.

현재 `app/integration/backend_crosswalk.py`에는 **2개 승인 mapping만** 있다. 모듈은 Backend-facing API route에 아직 연결되지 않았다.

| AI① 내부 ID | Backend code | 상태 |
|---|---|---|
| Skill `distribution` | `SKL-1-004` | Backend 승인·crosswalk에 등록 |
| Misconception `2.1` | `MIS-1-004` | Backend 승인·crosswalk에 등록 |
| Skill `transposition` | `SKL-2-037` | Backend 방향 합의; crosswalk 미연결 |
| Misconception `3.1` (부호 미변경) | `MIS-2-001` | Backend code candidate/seed 최종 상태 확인 필요 |
| Misconception `3.2` (양변 조작 비대칭) | `MIS-2-002` | Backend code candidate/적용 범위 확인 필요 |
| 일반 calculation / `arithmetic_slip` | MIS 없음 | 단순 계산 실수에 Misconception 자동 부여 금지; MIS-2-003 적용 조건 미결 |
| `comprehension` / `misread_problem` | MIS 없음 | Backend 별도 Observation/error 표현 미결 |
| division by zero | MIS-2-004 후보 | AI① 전용 detector 미구현 |

### Phase 9-5 classifier 변경

classifier는 이제 다음 내부 원인을 구분한다.

- `d_lhs + d_rhs == 0` → AI① MIS `3.1`, 이항 시 부호 미변경
- 한쪽에서 항을 제거하고 반대쪽에 반영하지 않은 경우 → AI① MIS `3.2`, 등식 양변 조작 비대칭

기존 내부 `transposition` 별칭은 3.1과 하위호환이다. 두 유형의 Backend MIS code mapping은 아직 연결하지 않았다.

**범위 한계:** 현재 3.2 detector는 항 누락/일부 비대칭 조작을 탐지한다. MIS-2-002 설명의 일방적 곱셈·나눗셈 전체를 보장하지 않는다. Backend 정의를 좁히거나 AI① detector를 확장하기 전에 이 범위를 합의한다.

## 6. Backend API 연동 미결정

Backend의 현재 AI schema는 `request_id`, 정수형 `attempt_id`/`student_id`, internal/captured Problem, 이미지/텍스트 Solution, Attempt context를 사용한다. AI① 현재 API는 문자열 ID와 text 중심이어서 adapter가 필요하다.

최종 계약 전 확인할 것:

1. Response에 `analysis_status`, `error_type`, `error_subtype`, `step_results[]`, `errors[]`, confidence를 어떻게 담을지
2. 대표 오류를 first error로 할지, 다중 `errors[]`를 어떻게 보존할지
3. 미매핑 code target은 `null`로 반환하고 분석 상태는 정상 유지할지
4. AI① 분류 confidence, OCR confidence, target confidence, overall confidence 정의
5. Backend가 AI①에 전달하는 image reference 형식·인증 방식 및 한 장 이미지의 영역 분리 결과 schema
6. MIS-2-002 범위, MIS-2-003이 Misconception인지 별도 error category인지, misread Observation 필드
7. `attempt_id` 재촬영/재시도, image hash, timeout/retry 및 실패 Response
8. Backend mastery/weakness 계산식과 AI① local Rule/BKT 값의 처리 여부
9. Backend→AI¹ 서비스 간 인증: 헤더 이름·키 환경변수·미인증 상태/오류 코드. Backend 협의안건 2-9 "내부 API Key 헤더, 키는 양쪽 `.env`"는 미확정 제안이고 `X-Internal-Api-Key`는 어디에도 문서화되지 않은 이름이다. 확정 전 `/ocr` guard 임의 구현 금지 → 대조표 §14.21

Backend가 원본 이미지를 저장하고 AI①이 인식한다는 책임 방향은 합의됐다. OCR provider는 현재 사용자가 선택한 Google Vision raw-text preview다. Backend는 OCR 전체에 30초를 대기하고 어떤 오류도 재시도하지 않으며, AI① upstream timeout 기본값은 8초(설정 상한 25초), provider SDK retry는 비활성화다. 요청당 provider 호출은 정확히 1회다. OCR 응답에는 이미지 참조 필드가 없다(협의서 §3.4 #18 — URL/서명/fingerprint 전부 비노출). 이미지 형식 제한은 협의서 §3.3 #12로 확인: JPEG/PNG, 10MB 이하, 해상도 제한 없음, 1장(`page_index=0`) — Backend가 업로드 시점에 검증하고 AI①은 재검증하지 않아도 된다. Backend raw-text 소비 계약은 협의서 §3.4 #14·§4.3으로 확정(학생이 APP에서 원문 확인·보정, 분석 요청은 Backend 생성).

## 7. 최근 개발 단계

- Phase 1~8-11: 텍스트 분석, Repository/API, Attempt 이력·멱등성, 시각·문제 메타데이터, Misconception TOP, 기간 필터
- Phase 9-4A: Backend crosswalk module 2 approved mappings, 아직 route 연결 없음
- Phase 9-5: 내부 3.1/3.2 transposition cause 분리
- Phase 9-9C.4: AI①에 Google Cloud Vision provider, 서비스 계정 ADC 경로, 공유 40회 로컬 budget, raw-text 응답과 구분된 API/auth/billing/quota 오류를 추가했다. 전체 1602 tests passed, `pyflakes app tests eval` 0 errors.
- SDK 호환성 테스트 중 fake image URL과 AnonymousCredentials를 사용한 요청이 실수로 Vision endpoint까지 두 차례 전송되어 403으로 거절됐다. 서비스 계정 credential은 사용되지 않았고 OCR 처리 성공은 없었다. 해당 client/RPC 생성 테스트는 제거하고, SDK message/signature만 network-free로 검사하도록 바꿨다.
- 기존 AI① API/기능 DOCX 초안: `docs/STEPi_AI1_API_명세서_v1.0.docx`, `docs/STEPi_AI1_기능명세서_v1.0.docx`
- 이 handoff도 프로젝트 문서로 추가됐다: `docs/AI1_HANDOFF.md`.
- README, CODING_MANUAL, 대조표 테스트 수를 1602로 동기화했다.
- Phase 9-9C.6: `/ocr` 내부 인증 계약(헤더·환경변수·오류 상태)이 문서화되지 않아 guard를 구현하지 않았고, 인증 검증이 앱에 전무한 현재 상태와 Backend 확인 질문을 대조표 §14.21에 기록했다. 코드·테스트 변경 없음.
- Phase 9-9C.7: OCR 응답에서 `image_reference` 필드를 제거했다(URL/서명/fingerprint 전부 비노출). 요청당 provider 호출 1회·SDK retry `None`·기본 timeout 8초 전달을 fake 테스트로 고정했다. timeout 상한·503 재시도는 미결(대조표 §14.22).
- Phase 9-9C.8: 협의서(2026-11-03) 기준 반영 — 내부 인증 guard(`app/integration/internal_auth.py` + 7개 라우트 dependency)와 OCR 계약 정합성(HTTPS-only·`AI1_OCR_ALLOWED_HOSTS`·timeout 25초 상한·429/422 오류 매핑·fetch/unsupported 분류)을 구현했다. `tests/conftest.py` bypass로 기존 API 테스트 무수정 통과, 인증 테스트는 `tests/test_internal_auth.py`(더미 키만) 25건. `/docs`·`/redoc`·`/openapi.json`은 기본 404(로컬만 `AI1_ENABLE_DOCS=1`). pytest 1642 passed. 1단계 S1-1 구현은 완료(D-03 근거)지만 실제 키 전달·서버 주소·Vision 승인은 사용자/팀 결정 사항이다.
- Phase 9-9C.9A(선행 입력 없는 로컬 검증): 협의서 §5.1·§5.2의 A-01~A-10/O-01~O-06 로컬 커버리지 매트릭스를 대조표 **§14.24** 로 고정했다(16개 ID — 로컬 증명·미측정·담당 구분; A-09 회차 연결과 A-10 timeout 처리는 Backend 몫. `parent_attempt_id`는 AI¹ 요청 스키마에 부재). 갭 4건을 기존 파일에 보강했다 — A-01 `first_error_step_index`·`description_ko`/`evidence_latex`, A-02 `errors == []`, A-10 provider 예외 재호출 0회, O-06 app/ 전체 logging/print 정적 스캔(0건 고정). `app/integration/preflight.py` 신설 — S1-2·S2-3 착수 전 환경변수 6종을 **네트워크 0회·값 비노출**로 점검(`tests/test_preflight.py` 40건 + `tests/test_host_policy.py` 20건). preflight·provider timeout 파싱에서 **NaN·무한대를 fail-closed로 거부**하고(0<v≤25 비교만으로는 통과하던 fail-open), allowlist는 **스킴/경로/포트 없는 bare hostname만 유효**로 판정하며 credential 경로는 **파일인지**까지 확인한다. Host allowlist 파싱은 `app/integration/host_policy.py` 단일 소스로 올려 preflight와 provider 런타임이 같은 판정을 쓰고, 런타임은 잘못된 항목이 있으면 503으로 fail-closed한다. S1-4 회신 표는 **빈칸 0개**로 완성했다(R-03 대면 직접 입력 결정 · R-13 S1-1 2026-11-03 / S2-1 2026-11-13 잠정 · D-05는 ngrok 공개 주소 `https://vividness-pep-unboxed.ngrok-free.dev`(2026-11-10, 재시작 시 변경)로 갱신하고 공개 경로 실측(/health 200·보호 401·정상 분석 200)을 기재했다), 전송 전 체크리스트 6항목도 모두 확인했다. pytest 1713 passed.
- Phase 9-9C.9B(S1-2 준비 — ngrok 터널): winget 설치본이 3.3.1로 계정 최소 버전(3.28.0) 미달이라 자체 업데이트로 3.39.13으로 올렸다. `uvicorn --host 127.0.0.1 --port 8001`(LAN 비노출) + `ngrok http 8001` 구성으로 공개 주소를 만들고, **임시 테스트 키**를 프로세스 환경변수로만 주입했다(값은 저장소·문서·로그에 없음, `%TEMP%` 파일로만 보관). 공개 경로 실측 — `/health` 200, 보호 경로 키 없이 401 `E_AUTH_INVALID_KEY`, 키 있는 정상 분석 200(`ANALYZED`, 0.52초), `/docs` 404, OCR provider 미설정으로 422/503. 무료 플랜은 재시작마다 주소가 바뀌고 브라우저 요청에 안내 HTML을 끼워 넣으므로(API 클라이언트는 무영향) 재통보·헤더 주의를 D-05에 명시했다. Vision 실호출은 계속 0회. 같은 날 Backend 공식 시드 2종(`STEPi_skills.csv` 94행, `STEPi_misconceptions.csv` 23행, 개념 42개)을 수령했다 — **저장소에 복사하지 않고** 참조만 했다. 확인 결과 STP(개념)는 공식 Skill-Concept 관계로 Backend가 도출하는 값이라 AI¹은 SKL 코드만 내보내면 되고(대조표·`backend_crosswalk.py` 원칙 유지), AI¹ 26 skill → SKL 94 skill 매핑 확장은 세분도 차이 때문에 **Backend 승인 후** 진행해야 한다. 2026-11-10 Backend 회신으로 Q1~Q7이 종결됐다(키 43자·대면 전달, concept 시드 불필요, `weakness_score`·숙련도 목록·초기 시드·multipart 모두 AI¹ 불필요). Q5의 R-11 확인 요청에는 "학생 데이터를 디스크·DB에 저장하지 않음(프로세스 메모리 전용, 재시작 시 소멸)"으로 답한다. 매핑 후보표는 `docs/AI1_Backend_코드_매핑_후보.md`(skill 16/26 후보, misconception 3/19 후보)로 작성했고, 코드 반영은 승인 후 `provisional_*` 경유로 진행한다.
- Phase 9-9C.9C(**S1-2 종료 · AI① 키 발급으로 전환**): Backend PC에서 `GET /health` **200(약 0.4초)**·키 없음/틀린 키 401·`/docs` 404를 확인해 S1-2를 종료했다. Backend가 `AI1_BASE_URL`에 주소를 등록했고 `AI1_OCR_ENABLED=false`를 유지한다. 키 발급 주체는 **AI①로 변경** — Backend가 만든 키는 폐기하고 AI①이 43자(`[A-Za-z0-9-_]`) 키를 발급해 별도 채널로 전달한다(값은 저장소·문서·대화·로그에 없음, `%TEMP%` 파일로만 보관). 기존 8001 리스너를 종료하고 새 키를 프로세스 환경변수로만 주입해 재기동한 뒤 공개 경로를 재검증했다 — 키 없이 401 `E_AUTH_INVALID_KEY`, 새 키로 정상 분석 200(`ANALYZED`, 0.76초), `/health` 200, `/docs` 404. 남은 것은 Backend가 키를 수령해 실제 분석 호출을 하는 S1-3이며, 그 응답·로그로 O-06(실서버 로그)을 확인한다. Vision/Mathpix 실호출은 계속 0회. Backend 서버 공개 주소 `https://frugally-unsubtle-luminous.ngrok-free.dev/api/v1`(고정)를 회신받아 D-06으로 기록했다 — AI①은 이 주소를 **호출하지 않고**(ADR-01), S2-3 이미지 URL 허용목록 host 후보로만 쓴다. S1-3 실행 자료는 `docs/AI1_S1-3_실호출_테스트_준비.md`(URL·헤더·요청/응답 스키마·curl 예시·오류 형식 6종, 키 값 비기재)로 정리했다.
- Phase 9-9C.9D(**터널 고정 도메인 점검·자동화**): 실행 중인 서버·터널은 건드리지 않고 점검만 했다. 현재 터널은 `ngrok http 8001`(--url 없음)로 떠 있어 **계정 기본 dev 도메인에 의존**하고 있었고(설정 파일은 version+authtoken 74바이트, `tunnels:` 없음), 명시 고정은 없었다. `scripts/start_ai1_tunnel.ps1`(Check/Start/Stop/-Restart, authtoken 하드코딩 없음, 키 값 비출력, 8001·같은 도메인 중복 기동 방지, 공개 확인 시 `ngrok-skip-browser-warning`+JSON 본문 검증, PS 5.1용 UTF-8 BOM)을 추가하고 **점검 모드로 8/8 OK** 를 확인했다. `.gitignore`에 `ngrok.yml`·`*.ngrok.yml`·`.ngrok/`·`ngrok.log`·`ai1_internal_key.txt`·`ai1_*key*.txt`·`*.local.ps1`를 추가하고 `git check-ignore`로 확인했다. 절차 문서는 `docs/AI1_터널_고정도메인_운영.md`(점검 결과·`--url` 고정 명령·재시작 후 8단계 검증). 같은 날 11:10 ngrok 요청 이력에서 **Backend의 S1-3 실호출 6건**을 확인했다 — 200 2건(900101·900106), 422 4건(`E_BAD_PROBLEM` 2·`E_UNRECOGNIZED` 1·`E_UNKNOWN_SKILL_CODE` 1). 즉 키 인증은 실제로 통과했고, 남은 실패는 입력/매핑 계약 문제다. Vision 호출은 계속 0회.
- Phase 9-9C.9E(**S1-3 원인 분석·Backend 회신 초안**): ngrok 이력의 **8회 호출**(900001·900002 + 900101~900106)을 Backend의 7개 시나리오와 1:1로 맞췄다. 회신 본문은 `docs/AI1_Backend_회신_S1-3_매핑확인.md`. 확인한 사실: (1) `E_BAD_PROBLEM`은 문제식이 **등식이 아니어서** 발생(`(x+1)(x+2)`·`(2x+1)+(3x-4)`는 `expr`) — 다항식 미지원이 아니다. (2) 900103은 문제가 등식이지만 풀이 줄이 `expr`/선행 `=`라 `E_UNRECOGNIZED`(HTTP 422 + `analysis_status:UNKNOWN`; Backend는 200 UNKNOWN으로 오인). (3) `SKL-1-002`는 crosswalk 역변환 가능 코드가 `SKL-1-004`·`SKL-2-037` 2개뿐이라 `E_UNKNOWN_SKILL_CODE`. (4) `official_skill_codes=[]`는 **관측/오류 귀속 Skill 기준** 생성 + **`problem_id` 재사용 시 저장본 우선**(900101은 900001이 등록한 `linear_equation`을 그대로 사용). (5) `SKL-1-004`로 새 문제를 보내면 `official=["SKL-1-004"]`가 될 수 있으나, `2x=6`에 붙이는 것은 매핑 테스트로 부적절. 추가 발견: **항등식 문제의 오탐**(`(x+1)(x+2)=x^2+3x+1` → `valid=True`, `(2x+1)+(3x-4)=5x-3` vs `=5x-2` → True) — `equivalent()`가 잔차 0 문제에서 해 집합 `[]==[]`로 흘러가는 결함 후보. 또 `SKL-2-037`·`MIS-2-001/002`는 **94행/23행 시드 CSV에 없고 관계자료 xlsx에만** 있다. `internal_auth` docstring의 "재시작 없이 반영"도 프로세스 환경 고정 때문에 부정확 — R-02 문구 정정 대상. 코드 변경은 하지 않았다.
- Phase 9-9C.9F(**A5·A7·A2 코드 반영**): Backend가 A1~A8 회신에서 A5(`E_NOT_EQUATION`)를 **먼저 배포**해 양쪽 동작이 어긋난 상태를 해소했다. (1) **A5** — `analyzer.analyze()`·`session._check_parsing()`에서 `parse_line(problem_latex)`가 `None`이면 `E_BAD_PROBLEM`(수식 해석 실패, 재시도 가능), 파싱됐지만 `expr`이면 `E_NOT_EQUATION`(422, 재시도해도 동일)으로 분리했다. `E_NOT_EQUATION`은 `_ANALYSIS_STATUS_BY_ERROR`에 넣지 않아 `analysis_status`가 붙지 않는다(입력 오류를 UNKNOWN으로 섞지 않음). attempt 경로는 attempt/분석을 저장하지 않는다. (2) **A7** — `AnalyzeAttemptResponse.skill`/`mastery` 설명에 "관측된 대표 Skill(오답이면 첫 오류 귀속), `problem_skills`와 다를 수 있음"을 명시(동작 무변경). (3) **A2** — 공식 Seed 밖 코드(`SKL-2-037`, `MIS-2-001`, `MIS-2-002`)를 `PROVISIONAL_*`에서 제거하고 역방향 수용(`skill_id_for_code`)도 차단했다. 내부 taxonomy의 `transposition`/`3.1`/`3.2` 관측·State 갱신은 그대로다. 관련 테스트 8개 파일을 정책에 맞게 수정(`test_backend_adapter`·`test_backend_text_adapter_api`·`test_backend_request_contract`·`test_api`·`test_attempt_api`·`test_analysis_status`). **1717 passed, pyflakes 0건.** 서버 재시작은 하지 않았다(배포 시점 별도 통보). A6 계약 초안은 `docs/AI1_표현식문제_계약초안_A6.md`.
- Phase 9-9C.9G(**A-1 항등식/모순 오탐 수정**): S1-3·9E에서 발견한 **유일한 오답→정답 오탐 경로**를 고쳤다. 원인은 sympy `solve()`가 **항등식(모든 x)** 과 **모순(해 없음)** 을 모두 `[]`로 반환해 `_same_solution_set([], [])`가 `True`가 되던 것. 실측 오탐 2건: `(2x+1)+(3x-4)=5x-3`(항등식) ↔ `5x-3=5x-2`(모순) → `True`, `(x+1)(x+2)=x^2+3x+2` ↔ `=x^2+3x+1` → `True`. 수정은 `_residual_kind()`(잔차를 `identity`/`contradiction`/`other`로 분류) + `_compare_degenerate()`(두 쪽 모두 확정이면 같은 종류일 때만 동치, 한쪽만 항등식이면 `False`, 그 외 `None`)다. 수정 후 두 오탐 모두 `False`, `verify("5x-3=5x-3", [...])` → `[True, False]`, 정상 방정식(`x^2-5x+6=0` ↔ `(x-2)(x-3)=0`) 판정은 불변. **성능 주의:** 최초 구현은 `equivalent()` 최상단에서 모든 등식 쌍마다 분류를 2회 호출해 전체 테스트가 80초 → 355초(`simplify`)/203초(`expand`)로 느려졌다. 분류를 **solve() 결과가 한쪽이라도 빈 희귀 경로로 한정**하고, 핫패스에서 분류를 부르지 않는다는 것을 `test_degenerate_classification_is_not_on_the_hot_path`(monkeypatch 스파이)로 고정했다. verifier 마이크로벤치 실측 A-1 델타는 ±0.5ms(노이즈 수준). **1722 passed, pyflakes 0건.** 남은 한계: **문제식 자체가 모순**인 경우(`(x+1)(x+2)=x^2+3x+1`)는 여전히 `valid=[True]` — P1의 `REVIEW_REQUIRED` 분기 소관. 서버 재시작 없음.

## 8. GitHub 현황 및 규칙

Repository: `https://github.com/STEPi2026/Ai1.git`

- GitHub Issue #1: “AI① 분석 파이프라인 및 Attempt API 초기 반영”.
- PR #2로 초기 AI① 구현이 `main`에 merge됐다. 원격 `main`: `4f70ea9783c65ad40f9e31518172647370b5c64b`.
- 원격 `feature/#1-ai1-mvp`는 초기 반영 commit `7aeb2011f90e283dddbfb819882bb9b82cdbe12e`를 가리킨다.
- 원본 로컬 `C:\Users\choi\Desktop\learning-intelligence`는 `master`, HEAD `b9c26e5` (Phase 9-5)이며 remote가 설정되어 있지 않다. 최신 Phase 9-5 작업은 GitHub에 아직 반영되지 않았다.
- 별도 안전 clone `C:\Users\choi\Desktop\learning-intelligence-github-sync`는 오래된 feature/main base에 있으므로 새 작업 전에 최신 `main` 기준으로 확인/갱신한다.
- `README.md`는 remote title `# STEPi Ai1`를 보존해 AI① 내용과 병합했다. AI① DOCX 두 파일과 이 handoff 파일은 원본 local Git 상태에서 untracked일 수 있다.
- 원본 프로젝트 `.gitignore`가 `.env`/`.env.*`를 제외하는지 확인한다. 이전 안전 clone에는 규칙이 들어갔지만 원본 `master`에는 반영되지 않았을 수 있다.

팀 규칙:

1. 새 Issue 후 최신 `main`에서 `feature/#이슈번호-기능명` 생성
2. `main` 직접 개발/Push 금지
3. Commit: `[이모지 type #이슈번호]: 작업 내용`
4. feature branch push 후 main 대상 PR, 승인 후 merge
5. `.env`, `.venv`, key/token/password 등 secret 금지

현재 Phase 9 후속 작업은 초기 Issue #1 범위와 다를 수 있으므로, 별도 Issue가 필요한지 확인한다. local `master`를 GitHub에 직접 push하거나 unrelated history를 임의 병합하지 않는다.

## 9. 다음 권장 단계

1. Backend API v1 Response/Request schema를 확정하고 위 미결정 항목을 정리한다.
2. ~~Backend MIS-2-002 적용 범위와 AI① 3.2 detector 범위를 일치시킨다.~~ → **A2로 종료**: MIS-2-001/002·SKL-2-037은 공식 Seed 밖이라 출력하지 않는다. 3.2 detector는 내부 ID로만 남는다.
3. MIS-2-003의 “계산 오류”를 misconception으로 누적할지 별도 error code로 처리할지 확정한다.
4. 공식 STP/SKL/MIS seed finalization 뒤 approved crosswalk를 Backend-facing adapter에 연결한다. 기존 내부 taxonomy/BKT key는 보존한다.
5. Backend 이미지 규격·용량·MIME·해상도를 확정하고 서비스 계정 credential/API/billing/free-quota 상태를 안전하게 설정한다.
6. 실제 API 검증은 free quota 잔량과 설정 확인 및 별도 사용자 승인 후 최소 샘플로 진행한다. 한국어·수식 OCR 품질 및 OCR→분석 handoff를 별도 평가한다.

## 10. 다음 AI 도우미의 역할

- 사용자가 직접 작업을 요청하면 OpenCode가 해당 코드·문서 작업을 수행한다. Google Vision 실제 API 호출과 과금 가능한 평가에는 별도 명시 승인이 필요하다.
- 사용자 명시 요청 없이 임의로 code, dependency, taxonomy, golden data, Git history를 바꾸지 않는다.
- Backend/AI②와의 미결정 API/code mapping은 추측하지 않고 `미확정/unmapped`로 기록한다.
- Git 작업 전 항상 status/branch/remote/diff/secret을 확인하고, 명시 Issue/feature-branch 규칙을 따른다.

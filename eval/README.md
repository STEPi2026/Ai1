# OCR 평가 하네스 (PHASE 9-9B)

`docs/AI1_OCR_연동_결정안.md` §4 의 측정 기준을 실행하기 위한 하네스다.

**저장소에 아무것도 남기지 않는다.** 실제 provider 응답·학생 이미지·정답 라벨·
측정 리포트는 모두 `eval/_local/` 아래에 두고 gitignore 된다.
`tests/golden/` 에 실제 provider 결과를 고정하지 않는다(회귀 테스트와 분리).

---

## 0. 선행 조건 (모두 충족되어야 측정할 수 있다)

| # | 조건 | 확인 방법 | 현재 상태 |
|---|---|---|---|
| 1 | Mathpix 계정 · API key 발급 | `console.mathpix.com/orgs` 에서 Organization 생성 + 카드 등록 + **일회성 $19.99** | ❌ 미충족 (환경변수 없음) |
| 2 | 예산 승인 | 조직 당 사용량 상한 결정 | ❌ 미승인 |
| 3 | **Improve Mathpix 보존 opt-out** | **콘솔 또는 Mathpix 지원팀으로 account-level 고정값 확인** — 요청 `metadata.improve_mathpix=false` 로는 고정 여부를 확인할 수 없고 **응답에도 근거가 없다** | ❌ 미확인 (key 없음) |
| 4 | E01–E16 대표 실제 사진 최소 20장 + 정답 라벨 | `eval/_local/images/` + `labels.json` | ❌ 미준비 (이미지 0장) |
| 5 | Backend presigned URL 발급 | 이미지별 **TTL 15분(프로젝트 권장값 — 벤더 요구 사항이 아님)** presigned HTTPS URL | ❌ 미준비 |

> **선행 조건이 충족되기 전에는 어떤 측정값도 나올 수 없다.**
> 본 하네스는 그 시점에 1회 명령으로 측정할 준비가 되도록 만든 것이다.

---

## 1. 파일 구성

```
eval/
  README.md               이 문서
  labels.schema.json      정답 라벨 JSON Schema
  labels.example.json     형식 시연용 합성 예시 (실제 아님)
  mathpix_client.py       v3/text 호출 래퍼 (자격증명=환경변수, 원본=로컬 저장)
  mathpix_adapter.py      응답 → OcrResult 변환 프로토타입 (M1/M2/M5/M12 미결)
  sandbox_probe.py        1회 호출로 응답 형식·confidence 누락·error_id 확인
  measure.py              §4.2 A/B/C 지표 계산
  selfcheck.py           하네스 자체 검증 (문서 게시 예시 기준 — 실측 아님)
  test_harness.py        하네스 계약 테스트 54건 (pytest 수집에 포함)
  test_run_batch.py      배치 러너 계약 테스트 38건 (fake transport 만 사용)
  test_aggregation.py     집계 정합성 테스트 16건 (fake transport 만 사용)
  test_coverage.py       coverage·실패 구분 테스트 13건 (fake transport 만 사용)
  test_result_contract.py  입력 계약 테스트 14건 (모순 행·행 집합 정합성)
  test_state_types.py     상태 필드 JSON 타입 테스트 41건 (boolean·error_stage·error)
  google_vision_client.py   Vision 호출 래퍼 (ADC, 표준 라이브러리 전용 — 신규 dependency 없음)
  google_vision_adapter.py  Vision 응답 → 평가용 중간 구조 (raw text OCR 까지만)
  google_vision_probe.py    Vision 데모 평가기 (기본 dry-run · 누적 40회 상한)
  test_google_vision.py     Vision 데모 계약 테스트 43건 (fake 응답만 사용)
---

## 2. 실행 순서

### 2-1. 자격증명 (저장소에 쓰지 않는다)

```powershell
$env:MATHPIX_APP_ID  = "<app_id>"
$env:MATHPIX_APP_KEY = "<app_key>"
```

### 2-2. 보존 설정 확인 (선행 조건 3)

**요청 설정과 account-level 설정은 별개다.**

| | 요청 `metadata.improve_mathpix=false` | account-level 고정값 |
|---|---|---|
| 무엇을 막는가 | **데이터 품질 개선 사용**(QA 팀이 데이터 못 봄) + **OCR 데이터 보존**(이미지 미보존 · 결과 미캐시 · `/v3/ocr-results` 조회 불가) | — |
| 어디서 정하는가 | 요청 본문의 `metadata` | **app key** 에 고정 (엔터프라이즈 계약 등) |
| 우선순위 | 낮음 — **고정값이 있으면 그 값이 요청 설정을 덮어쓴다** | 높음 |
| 확인 방법 | 요청 본문 확인 | **콘솔 또는 Mathpix 지원팀(`support@mathpix.com`)** — **요청 설정만으로는 고정 여부를 알 수 없고, OCR 응답에도 유효 설정의 근거가 없다** |

> **청구·감사용 메타데이터는 opt-out 여부와 무관하게 남는다.** `status` · `timestamp` ·
> `page count` · `error code` 는 감사·청구를 위해 보존된다 — opt-out 이 "아무것도 안
> 남는다"는 뜻이 아니다.

> **기본값이 `improve_mathpix:true` 면 학생 과제 사진이 벤더 QA 팀에 노출된다.**
> 고정값이 있는지 콘솔/지원팀으로 확인한 뒤 호출한다. 우리 클라이언트는 **기본 요청에 항상
> `improve_mathpix=false` 를 넣는다** — 그래도 고정값이 있다면 그게 우선한다.

### 2-3. 계약 검사 (1회 호출)

```powershell
python eval\sandbox_probe.py --image-ref "<presigned https url>" --out probe01
```

확인하는 것(결정안 §8-1):

| # | 확인 항목 | 출력 키 |
|---|---|---|
| 1 | 실제 응답 형식 | `top_level_keys`, `line_keys` |
| 2 | `include_line_data` / `include_lines_json` 출력 | `line_count`, `lines_json_region_keys` |
| 3 | **confidence 누락 여부** (M12 전제) | `line_confidence_missing`, `line_confidence_missing_ids` |
| 4 | `error_id` / `conversion_output` 동작 | `error_id_histogram`, `conversion_output_false_count` |

### 2-3-1. 배치 실행 (20장 이상)

```powershell
# 기본은 dry-run — 호출 0회, 파일 0 writes. 먼저 이것으로 계획만 확인한다.
python eval\run_batch.py

# 실제 호출은 --run 과 --max-calls 를 **둘 다** 명시해야 한다.
python eval\run_batch.py --run --max-calls 20
```

| 입력 | 설명 |
|---|---|
| `eval/_local/labels.json` | 정답 라벨 (스키마: `eval/labels.schema.json`) |
| `eval/_local/presigned_urls.json` | **로컬 평가 자료** presigned URL manifest |

```json
{
  "E01": "https://<presigned url>",
  "E02": { "sample_id": "E02-a", "url": "https://<presigned url>" }
}
```

> **Backend presigned URL 발급은 이 도구가 하지 않는다.** manifest 는 **실행 직전에 준비하는
> 로컬 평가 자료**다. URL 은 **만료되는 bearer 정보**로 취급한다.

#### 산출물 — 실행마다 `run_id` 하위로 격리된다

```
eval/_local/runs/<run_id>/
  responses/<case>__<sample>.json   원본 응답
  results.jsonl                     measure.py 가 읽는 행
  run_log.jsonl                     샘플 단위 실행 로그
```

**불변조건**

| # | 조건 |
|---|---|
| 1 | **경로** — 출력 경로는 resolve 기준으로 `eval/_local/` **안**이어야 한다. 밖으로 나가면 **호출·파일 생성 없이** 실패한다 |
| 2 | **파일명** — `case_id`/`sample_id` 는 안전한 slug 로 변환해 쓴다(경로 탈출 문자 제거). slug 충돌이 있으면 실패한다. **원래 id 는 결과 행에 그대로 남는다** |
| 3 | **덮어쓰기** — 기본 실행은 기존 결과를 **덮어쓰지 않는다.** 실행마다 충돌 없는 `run_id` 하위 경로를 쓴다. 지정 경로가 이미 있으면 **실패**한다 |
| 4 | **URL 비노출** — presigned URL 은 stdout·로그·`results.jsonl`·리포트에 기록되지 않는다(전체 URL·쿼리·개별 쿼리 파라미터까지 치환) |
| 5 | **추적성** — 원본 응답을 저장한 **직후** `raw_response_file` 을 결과 행에 기록한다. adapter 변환이 실패해도 원본 응답 경로로 찾을 수 있다 |
| 6 | **호출 상한** — `run_batch()` 가 `max_calls <= 0` 을 거부한다 (CLI 검사만으로 끝내지 않는다) |

> 원본 응답은 벤더가 `src` 를 반사할 수 있으므로 `eval/_local/` (gitignore) 안에만 둔다.

#### 반복 측정 누적 (M16)

같은 이미지를 여러 번 측정해 **원본 줄 인덱스의 반복 안정성**을 확인한다.

```powershell
python eval\run_batch.py --run --max-calls 20 --run-id run-1
python eval\run_batch.py --run --max-calls 20 --run-id run-2

# 두 실행을 누적한다 (호출 없음)
python eval\run_batch.py --accumulate `
  eval\_local\runs\run-1\results.jsonl `
  eval\_local\runs\run-2\results.jsonl
```

**누적 전 검증 — 독립된 호출만 받는다**

| 거부 사유 | 의미 |
|---|---|
| **같은 결과 파일**을 두 번 제공 | 같은 호출을 반복으로 세지 않는다 |
| **같은 `run_id`** 를 두 파일에서 제공 | 같은 실행을 두 번 넣은 것 |
| **한 파일에 여러 `run_id` 가 섞여 있음** | 실행 단위가 섞여 있음 |
| **`run_id` 가 없는 행** | 실행을 특정할 수 없음 |
| **`attempt_id` 중복** | 독립된 호출이 아님 |
| 같은 `case_id` 가 다른 `sample_id` 를 가리킴 | 평가 id ↔ sample 대응이 어긋남 |

누적 산출물(`eval/_local/accumulated/results.jsonl`)이 **이미 있으면 덮어쓰지 않고 실패**한다.

#### 지표 집계 규칙

| 대상 | 규칙 |
|---|---|
| **행 집합** | `attempts_total` · `failed_attempts` · provider/adapter 실패율의 **분자·분모는 모두 같은 행 집합**(label id 일치 행) 기준이다 |
| **coverage** | `samples_attempted / 전체 label`. **시도하지 않은 sample 은 실패로 세지 않는다** — `samples_unattempted` 로 따로 집계한다 |
| per-sample 정확도 | **첫 번째 성공 결과** 기준. 성공 결과가 하나도 없는 sample 만 **실패/미측정**으로 세고 점수를 만들지 않는다 |
| **sample 실패율** | **분모는 실제 시도된 sample.** 시도된 sample 이 없으면 `null` |
| **end-to-end 실패율** | 분모가 **전체 label** (시도 안 한 sample 포함) — 부분 실행에서 실패율이 **과소 표시**되므로 `sample_failure_rate` 와 함께 본다 |
| **provider 실패율** | 요청 자체가 실패한 시도. **분모 = 모든 시도** |
| **adapter 실패율** | provider 는 성공했고 변환만 실패한 시도. **분모 = provider 성공한 시도** |
| M16 안정성 비교 | **성공 호출만** 비교한다(provider·adapter 모두 성공). 성공이 2회 미만이면 `null`(미확인) |
| 실패 집계 | `failures` = 성공 결과가 없는 **sample** 수 · `failed_attempts` = 실패한 **호출 시도** 수 (**별도**) |
| latency · 비용 | **모든 실제 호출 시도** 기준 — **반복 호출 비용을 누락하지 않는다** |
| 반복 측정 그룹 | **`(case_id, sample_id)`** 기준 — **서로 다른 이미지는 반복으로 비교하지 않는다** |
| **비용 unknown** | **provider 성공 + adapter 실패** 는 벤더가 과금했을 수 있지만 과금 대상 행 수를 알 수 없다 → **0 으로 누락하지 않고 unknown** 으로 집계하고, 추정 합계와 unknown 수를 함께 표시한다 (`cost_estimate_usd_total_is_lower_bound`) |

#### 결과 행 필드

| 필드 | 의미 |
|---|---|
| `id` · `sample_id` | 평가 케이스 ID · 샘플(이미지) ID |
| `run_id` · `attempt_id` · `attempt_no` | 실행 ID · 호출별 식별자 · 실행 내 호출 순번 |
| `provider_succeeded` | 벤더 호출이 성공해 **응답을 받았는가** |
| `adapter_succeeded` | 그 응답을 `OcrResult` 초안으로 **변환했는가** |
| `error_stage` | `"provider"` / `"adapter"` / `null` — 어느 단계에서 실패했는가 |
| `raw_response_file` | 원본 응답 파일 경로 — **adapter 변환이 실패해도** 기록된다 |
| `draft` | 변환 초안 (adapter 성공 시에만) |
| `elapsed_seconds` | 호출 지연 (실패 시도도 기록) |
| `error` | sanitized 오류 메시지 |

#### 입력 계약 (`measure.py` 가 강제한다)

- **명시 상태 필드는 셋 다 있어야 한다** — `provider_succeeded` · `adapter_succeeded` ·
  `error_stage` 중 **하나라도** 있으면 새 형식 행으로 보고 셋 다 있어야 한다.
- **상태값은 JSON 타입으로 검증한다** — Python truthiness 로 판정하면 `"false"`(비어 있지
  않은 문자열)가 참으로 처리되어 **조용히 잘못된 집계**가 난다.

  | 필드 | 허용 타입 |
  |---|---|
  | `provider_succeeded` · `adapter_succeeded` | **JSON boolean 만** (`true`/`false`). 문자열·정수·null 등 **거부** |
  | `error_stage` | JSON **null** 또는 정확히 `"provider"` / `"adapter"` 문자열 |
  | `error` | **string** 또는 **null** |

  위반하면 `ResultContractError` 로 즉시 중단한다(`measure()` 도 즉시 중단).
- **명시 필드가 `draft`/`error` 와 모순되면 조용히 재계산하지 않고**
  `ResultContractError` 로 **중단**한다. (`measure()` 도 즉시 중단)
  - `adapter_succeeded=True` 인데 `draft` 없음
  - `adapter_succeeded=False` 인데 `draft` 있음
  - `provider_succeeded=False` 인데 `adapter_succeeded=True`
  - 두 단계 성공인데 `error` 있음
  - 실패 상태인데 `error_stage` 가 `None` / 값이 `provider`·`adapter` 가 아님
- **구버전 행**(명시 필드 없음)은 기존 호환 fallback 으로 처리한다.
- **label id 와 일치하지 않는 결과 행**은 **집계에서 제외**하고
  `counts.unmatched_result_rows` 로 건수를 보고한다. 실제 호출이었을 수 있으므로
  추정 비용(`unmatched_cost_estimate_usd`)도 따로 보고한다.

#### 행 집합 — 분자·분모 정합성

`attempts_total` · `failed_attempts` · `provider_failure_rate` ·
`adapter_failure_rate` · `attempt_failure_rate` 의 **분자·분모는 모두 같은 행 집합**
을 쓴다 — **label id 와 일치하는 결과 행만.** (`counts.row_set_note` 참고)


### 2-4. 측정 (최소 20장)

```powershell
# 라벨 준비 (eval/_local/labels.json) — 스키마: eval/labels.schema.json
# 이미지: eval/_local/images/
# 각 장을 presigned URL 로 받아 호출하고 결과를 eval/_local/results.jsonl 에 append
python eval\measure.py
```

`measure.py` 가 §4.2 의 지표를 계산한다.

| 층 | 지표 |
|---|---|
| **A. OCR 정확률** | `problem_latex_text` 완전일치율 · **동치율**(AI¹ 판정 기준 `verifier.equivalent`) · 단계 경계 F1 · 단계 순서 완전일치/Kendall τ · bbox IoU median · printed/handwritten 정확률 · unreadable Precision/Recall · confidence ECE |
| **B. AI¹ 임팩트** | 최종 오류 판정 일치율 · 오개념 오염율 · `REVIEW_REQUIRED` 비율 |
| **C. 운영** | 지연 p50/p95 · 1장당 비용 · 실패율 |

---

## 2-5. Google Cloud Vision OCR 데모 (PHASE 9-9C.1) — 별도 도구

**AI¹ `/ocr` 경로와 무관한, 별도의 데모 평가기다.** OcrResult 로 추측 변환하지
않고 **raw text OCR 까지만** 한다. `problem` · `solution_steps` ·
printed/handwritten · LaTeX 는 **추정하지 않는다** — Vision 은 의미상
문제/풀이 분리를 보장하지 않는다.

```powershell
# 기본은 dry-run — 호출 0회, 파일 0 writes
python eval\google_vision_probe.py

# 실제 호출은 --run 과 --max-calls 를 **둘 다** 명시해야 한다
python eval\google_vision_probe.py --run --max-calls 20 --language-hint ko
```

| 파일 | 역할 |
|---|---|
| `eval/google_vision_client.py` | `DOCUMENT_TEXT_DETECTION` 호출 래퍼 (ADC, **표준 라이브러리 전용** — 신규 dependency 없음) |
| `eval/google_vision_adapter.py` | 응답 → **평가용 중간 구조**(원문 텍스트 · block/paragraph/word 순서 · bounding polygon · confidence). **공식 응답 구조** — block 은 `boundingBox`, word 는 `property`·`boundingBox`·`symbols`·`confidence` (word 텍스트는 `symbols[].text` 로 조립) |
| `eval/google_vision_probe.py` | 실행 관리 (dry-run 기본 · 누적 상한 · languageHints 선택) |

#### 과금 — billing account 와 free quota

| 항목 | 내용 |
|---|---|
| **무료 범위** | Cloud Vision 은 **OCR 기능별** 첫 **1,000 units/month/billing account** 가 무료다 |
| **billing account 필요** | **필요하다.** free quota 는 billing account 단위로 붙고, billing account 를 만들려면 카드 등록이 선행된다. 카드 등록 없이는 quota 도 없다 |
| **quota 공유** | 같은 billing account 아래의 **모든 프로젝트가 free quota 를 공유**한다 — 프로젝트별로 따로 주어지지 않는다 |
| **기능당 1 unit** | **이미지 1장 = OCR 기능 1개 = 1 unit.** `DOCUMENT_TEXT_DETECTION` 과 `TEXT_DETECTION` 을 같이 부르면 **같은 이미지에서 unit 을 두 번** 소비한다 → 이 데모는 **OCR 기능 하나만** 쓴다(`google_vision_client.build_body` 가 다른 기능을 거부) |
| **누적 상한** | 이 데모와 앱 provider의 **공유 누적** 상한은 **40회**다. 실행마다가 아니라 누적 — `eval/_local/google_vision/call_budget.jsonl`에 요청 전에 예약하고, 넘으면 거부한다 |
| **과금 guard 한계** | **예산 알림(budget alert)만으로 과금을 막을 수 있다고 가정하지 않는다.** 알림은 지연된 통지일 뿐 하드 캡이 아니다. 이 도구의 40회 상한은 **우리 쪽 가드**지 벤더 측 보호가 아니다 |
| **실제 호출 전 필수** | billing 계정의 **free unit 잔량**과 **결제 설정**을 확인한 뒤, **별도 지시가 있을 때만** 호출한다. 이번 단계에서는 프로젝트 생성·billing 활성화·카드 등록을 **하지 않았다** |

#### credential

- **ADC** (`gcloud auth application-default login`) 또는 환경변수
  `GOOGLE_VISION_ACCESS_TOKEN` 만 쓴다. **새 dependency 없음.**
- 토큰은 요청 헤더에만 쓰고 **어떤 파일·로그·요약에도 남지 않는다**
  (오류 메시지에서도 치환한다).

#### 입력·출력

| 항목 | 경로 |
|---|---|
| 입력 manifest | `eval/_local/google_vision/images.json` — `[{"id": "G01", "uri": "gs://..."}, ...]` |
| 원본 응답 | `eval/_local/google_vision/raw/<run_id>__<image_id>.json` — run·image 로 유일 |
| 평가용 중간 구조 | `eval/_local/google_vision/results/<run_id>__<image_id>.json` |
| **호출 슬롯 원장** | `eval/_local/google_vision/call_budget.jsonl` — **API 요청 전에** 슬롯을 예약하고 flush+fsync 한다 |
| 호출 로그(결과) | `eval/_local/google_vision/call_log.jsonl` — 예약된 슬롯의 결과 |

**전부 `eval/_local/` 아래** — gitignore 확인 완료. (참고: 측정 도구의 manifest URI 스킴은
eval 전용 계약으로 `gs://`와 `https://`를 모두 받는다. **앱 runtime provider(`/ocr`)는
PHASE 9-9C.8 부터 HTTPS 서명 URL만** 허용한다 — 협의서 §3.3 #10.)
> **presigned URL 은 기록하지 않는다** — 이미지 URI 는 `sha256:` 참조로만 남긴다. 전체 URI · 서명 쿼리 · 개별 쿼리 파라미터는 결과 파일·요약(stdout)·오류 메시지·실행 로그 **어디에도** 남지 않는다. **Vision 응답이 `context.uri` 로 요청 URI 를 반사하면 저장 전에 그 URI 를 redaction** 한다(원본 응답 파일·결과 구조 모두).
>
> **`source_image_ref` 는 이미지의 안정 ID 가 아니다** — **URI 전체의 해시**라 같은 이미지여도 presigned URL 이 새로 발급되면 값이 달라질 수 있다. **비교·대응 키로 사용하지 않는다.** 안정적인 대응 키는 manifest 의 `id` 다.
> 되돌릴 수 없음)로만 남긴다. 전체 URI · 서명 쿼리 · 개별 쿼리 파라미터는 결과
> 파일·요약(stdout)·오류 메시지·실행 로그 **어디에도** 남지 않는다.

> **누적 40회 가드** — API 요청 **전에** 슬롯을 원장(`call_budget.jsonl`)에 예약하고
> **flush + fsync** 한다. **잠금 파일**로 동시 실행을 직렬화한다. 따라서
> **timeout·프로세스 중단처럼 실제 과금 여부를 모르는 시도도** 슬롯을 남겨 상한에
> 포함된다.
>
> **월별 무료 한도 가드** — 실제 호출(`--run`)에는 운영자가 확인한 월 무료 한도
> 정책이 **필수**다. `AI1_OCR_FREE_QUOTA_CONFIRMED=1`,
> `AI1_OCR_BILLING_UNIT=document_text_detection`, `AI1_OCR_MONTHLY_FREE_QUOTA`,
> `AI1_OCR_MONTHLY_CALL_CAP`(무료 한도보다 작아야 함)이 없거나 안전하지 않으면
> 예약 단계에서 `BudgetUnverified`로 거부한다. 월 경계는 UTC 1일 00:00이며
> 이전 달 기록은 삭제하지 않는다. 상태는
> `.venv\Scripts\python scripts\ocr_usage_status.py`로 조회한다.

> **이 상한의 한계 (중요)** — 40회 상한은 **AI¹ 데모 도구 내부의 제한**이다.
> **billing account 전체 사용량을 통제하지 않는다.** 같은 billing account 를 쓰는
> 다른 프로젝트·다른 도구가 있으면 그쪽 사용량은 이 상한과 무관하다. 과금 방어는
> **free unit 잔량 확인 + 결제 설정 확인**에서만 이뤄진다.



#### 한국어 힌트

`--language-hint ko` 는 **선택 설정**이다. 힌트 사용/미사용의 차이는 **비교 가능**하도록
요청 body(`imageContext.languageHints`)와 결과 구조(`language_hint`)에 **둘 다** 남긴다.


### 앱 `/ocr` provider와의 관계

이 문서의 eval probe는 표준 라이브러리 기반 독립 도구이고, AI① 앱의 provider는
`app/integration/google_vision.py`에 있다. 앱 provider는 `google-cloud-vision` SDK를
사용하므로 `requirements.txt`에 의존성이 선언되어 있다. 두 경로 모두 동일한
`eval/_local/google_vision/call_budget.jsonl` 원장·잠금을 사용해 프로젝트 로컬 40회
상한을 공유한다. 상한은 billing account 전체 quota가 아니다.

앱에서 실사용하려면 `GOOGLE_APPLICATION_CREDENTIALS`가 서비스 계정 JSON을 가리키고,
`AI1_OCR_PROVIDER=google_vision`을 명시해야 한다. 앱 응답은 `raw_text` 미리보기이며
problem/풀이 분리와 LaTeX를 추측하지 않는다. API 활성화·인증·billing 설정 오류는
구분된 503 오류로 반환된다. quota 확인 및 별도 승인 전에는 실제 API 요청을 보내지 않는다.

## 3. 이 하네스가 **하지 않는** 것 (의도적)

- **confidence 누락을 `0.0` 으로 대체하지 않는다.** M12 가 미결이므로
  `mathpix_adapter.to_ocr_result()` 는 누락이 하나라도 있으면
  `UndecidedContractError` 를 낸다. 값으로 코드화하지 않는다.
- **M1·M2·M5 를 확정하지 않는다.** `mathpix_adapter` 의 변환은 **후보 규칙** 이며
- **종류를 임의 배정하지 않는다.** `is_printed`/`is_handwritten` 이 둘 다 false 인 콘텐츠 줄은 — unreadable 이어도 — handwritten step 으로도 printed region 으로도 나가지 않는다. `UndecidedContractError` 로 변환을 보류한다.
- **provider 응답 id 를 정답 라벨 키로 쓰지 않는다.** 안정성이 확인되지 않았다(M16).
  실제 응답으로 검증하기 전에는 계약으로 채택하지 않는다.
- **`tests/golden/` 에 실제 provider 결과를 고정하지 않는다.** 회귀 테스트는
  fake provider 계약(`ocr_handoff.json`)만 고정한다.
- **API key · 원본 응답 · 학생 이미지를 커밋하지 않는다.**
- **Google Vision 응답을 AI¹ `OcrResult` 로 추측 변환하지 않는다.** Vision 데모는 **raw text OCR 까지만** 한다 — 문제/풀이 구분·인쇄/필기·LaTeX 는 추정하지 않는다.
- **Vision 데모는 Google Cloud billing 계정을 만들지 않는다.** billing 활성화·카드 등록·프로젝트 생성은 하지 않았고, free quota 잔량 확인 후 **별도 지시**가 있을 때만 호출한다.

---

## 3-1. 변환 규칙 요약 (후보 규칙 — 미확정)

`mathpix_adapter.py` 가 쓰는 규칙이다. **모두 후보**이며 실제 샌드박스 응답으로
검증하기 전에는 계약으로 채택하지 않는다.

| 대상 | 규칙 | 상태 |
|---|---|---|
| bbox | 줄의 `region` → `cnt` 폴리곤(min/max) → `lines_json` 같은 `id` 순 | 후보 (M14 연관) |
| 단계 정렬 | 위→아래(y), 같은 y 면 왼쪽→오른쪽(x) | 후보 (M2) |
| `error_id` | `image_max_size` = 미인식 단계로 **보존**, `image_not_supported` 는 **타입이 text/math 면 보존**, `image_no_content` 만 제외(기록은 남김) | 후보 (M6) |
| 종류 판별 | `is_printed`/`is_handwritten` 둘 다 false 면 **임의 배정하지 않고** `undecided_kind` 로 보존 → 변환 보류. **unreadable 인 줄도 똑같이** `undecided_kind` 에 넣는다 | 후보 (M5) |
| 종류 버킷 | 종류가 **미정**인 줄은 어느 버킷에도 넣지 않는다. 미인식(unreadable) 줄도 **자신의 종류 버킷**에 들어간다 — 인쇄 줄을 필기 단계로 몰아넣지 않는다 | 후보 (M5) |
| MMD 분리 | `text` 에 **내용을 남기고** 구분자만 벗긴다. `latex_text` = 구분자 안쪽 | 후보 (M1) |
| confidence | **누락 시 0.0 대체 안 함** — `missing_confidence` 로 모으고 변환 중단 | 후보 (M12) |
| region | 풀이 콘텐츠가 아닌 영역(도형 등)은 `OcrResult.regions` 에 넣지 않고 `excluded_lines` 에 기록 | 후보 (M6) |
| `???` | 미인식 항목은 `text="???"` + `latex_text=null` 만 | 후보 (M6) |
| `metadata.improve_mathpix` | **항상 `false` 로 보낸다** — 품질 개선 사용 금지 + OCR 데이터 미보존. **단 account-level 고정값이 있으면 그 값이 우선**(콘솔/지원팀 확인 필요) | 확정 (공식 문서) |
| region 연결 (M14) | `OcrResult` 에 region 식별자가 없으므로 **`OcrStep.region_id` 는 null** 로 둔다 (존재하지 않는 region 을 참조하지 않는다). provider line id 는 eval 초안 `step_region_links[]` 에 보존한다. 연결이 필수면 `region_link_policy="require"` 로 **M14 미결 오류로 변환을 보류**한다 | 후보 (M14) |

**변환이 보류되는 두 경우** (둘 다 미결 항목이므로 값을 정하지 않는다)
- `missing_confidence` 가 있음 → **M12**
- `undecided_kind` 가 있음 → **M5**


## 3-2. 지표 규칙 (PHASE 9-9B.2)

| 지표 | 규칙 |
|---|---|
| printed/handwritten 정확률 | **원본 줄( region's kind) 단위**로 채점한다. 라벨 `line_kinds` ↔ 예측 `line_kinds` 를 **원본 줄 인덱스**로 맞춘다. **`problem_kind` 에서 추론하지 않는다.** 대응 가능한 줄이 하나도 없으면 **점수를 만들지 않고** `printed_handwritten_unavailable_cases` + 사유를 남긴다 |
| unavailable 사유 | `label_line_kinds_missing` · `label_keys_not_source_line_index` · `no_overlapping_source_line_index` · `all_matched_lines_undecided` |
| bbox IoU | 대응 키는 **원본 줄 인덱스**. provider 응답 id 로 키를 쓴 라벨은 매칭되지 않고 `label_region_bbox_keys_not_source_line_index` 로 집계된다 |
| 단계 경계 F1 | 원본 줄 범위가 있을 때만. 없으면 `null` + `segment_f1_unavailable_cases` |
| 비용 | **추정치**(`cost_estimate_usd_total`). **과금 대상 텍스트 행**이 **12줄을 초과**(13줄부터)하면 페이지 단가($0.005). 도형·차트 등은 **세지 않는다** — 줄 수는 `billable_text_row_count` 기준 |

### 라벨 키 규칙 (중요)

정답 라벨의 키는 **원본 줄 인덱스(문자열)** 다 — `line_kinds` · `region_bboxes` 모두.

> **provider 응답 id 를 라벨 키로 쓰지 않는다.** 벤더 응답 id(32자리 hash)는 반복
> 호출마다 달라질 수 있고 **안정성이 확인되지 않았다**(→ M16). id 로 키를 쓴 라벨은
> 매칭되지 않고 unavailable 로 보고된다.
>
> 원본 줄 인덱스조차 반복 호출에서 안정적인지는 **실측으로 확인해야 한다.**
> `measure.py` 가 같은 케이스의 반복 측정 결과를 비교해
> `D_label_key_stability.source_line_index_stable` 로 보고한다
> (`true` / `false` / **`null` = 반복 측정 없음 → 미확인**).


## 4. 측정 후 해야 할 일 (결정안 §8-5)

측정 결과로 M1~M8·M12·M13 과 Q21·Q22·Q24 를 **확정하거나 수정**한다.
기준에 못 미치면 **Mathpix 를 최종 provider 로 확정하지 않는다** (확정 게이트).

미결 항목과 각각을 닫는 데 필요한 것:

| 항목 | 닫는 데 필요한 것 |
|---|---|
| **M1** MMD → (text, latex_text) 분리 | 실제 MMD 줄 샘플. 혼합 줄·다수식 줄 포함 |
| **M2** 줄 → 단계 파생 | 실제 줄/단계 대응 라벨. 1줄 1단계 가정의 정당성 |
| **M3** 2-provider 하이브리드 | **한국어 인쇄 인식률** 측정 결과 |
| **M4** confidence 2종 | `confidence` vs `confidence_rate` 실제 분포 비교 |
| **M5** `kind` 3값(둘 다 false) | 둘 다 false 인 줄의 실제 비율 |
| **M6** `"???"` 합성 규칙 | `error_id`/`conversion_output` 실제 동작 (2-3 에서 확인) |
| **M7** 다중 페이지 병합 | 2페이지 이상 실제 사례 |
| **M8** HTTP 429 | rate limit 도달 시험 또는 벤더 문서 |
| **M12** confidence 누락 표현 | **실제 누락 빈도·맥락** (2-3 에서 확인) → 그때 표현 결정 |
| **M13** 보존 기간 | opt-out 설정 확인(2-2) + 실제 응답의 `improve_mathpix` 반영 확인 |
| **M14** `OcrRegion` 에 id 필드 없음 | 우리 스키마에 region id 가 없어 step→region 연결이 닫히지 않는다. id 추가 여부를 결정 |
| **M15** 과금 대상 텍스트 행 정의 | 벤더 요금 문서에서 "line" 이 어떤 타입까지 포함하는지 미확인. 하네스는 보수적으로(text/math 계열) 센다 |
| **M16** 라벨 키의 안정성 | provider 응답 id 는 반복 호출 안정성이 미확인 → 라벨 키로 사용 금지. **원본 줄 인덱스**의 반복 호출 안정성도 실측 확인 필요 (`D_label_key_stability`) |

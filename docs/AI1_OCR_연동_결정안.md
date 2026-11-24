# AI① OCR 연동 결정안 (PHASE 9-9A)

- 기준일: **2026-10-22** (조사·가격·한도 모두 이 시점 기준)
- 범위: **조사·결정만.** 이 문서 작성 시점에 서비스 코드·의존성·API key/secret 은
  변경하지 않았다. 구현은 PHASE 9-9B 이후.
- 관련 문서: `docs/AI1_명세서_대조표.md` §14.17(OCR 경계) · §14.18(handoff 규칙) ·
  `app/integration/ocr.py`(`OcrProvider` 인터페이스)

---

## 0. 현재 결정 (2026-11-24): structured OCR provider — DEFERRED

1. 기존 Google Vision raw_text OCR 구조를 유지한다.
2. 사진 풀이는 현재처럼 힌트 제공 용도로만 사용한다.
3. 사진 OCR 결과를 정답 판정·숙련도 업데이트에 반영하지 않는다.
4. `capture_mode` / `layout_detected` / `layout_mismatch` / `region_id` 계약 필드는 유지하되,
   실제 추출하지 못한 값은 `unknown`/`null`로 처리한다.
5. structured OCR 관련 추가 API·의존성·유료 서비스 연동은 진행하지 않는다.
6. 실제 학생 풀이 이미지와 정답 라벨을 확보해 OCR 정확도를 측정한 뒤 도입 여부를 재검토한다.

현재 상태:
- `representation`: `raw_text`
- `layout_detected`: `unknown`
- `layout_mismatch`: `null`
- structured 응답: 미제공
- 실사진 OCR 정확도: 미측정

재검토 조건:
- 실제 촬영 샘플 20장+와 사람 라벨 확보
- 한글 인쇄/손글씨 수식 정확도·기호 혼동률 측정
- 예산·개인정보(데이터 보존/opt-out) 검토
- AI②의 structured 요구 확정

> 이 결정은 2026-10-22 Mathpix 권장안을 **보류**로 대체한다.
> 기존 비교·분석은 이력으로 보존한다.

---

## 1. 요구 계약 — provider가 만족해야 하는 것

`app/schemas.py` 의 `OcrResult` / `OcrResponse` 가 요구한다. 후보를 비교할 기준은
**이 계약을 얼마나 적은 변환으로 만족하는가**다.

| # | 계약 요소 | `OcrResult` 요구 | 왜 필요한가 |
|---|---|---|---|
| 1 | 검증용 LaTeX | `problem.latex_text`, `step.latex_text` | AI① 검증기가 LaTeX 를 파싱한다 (`parse_line`) |
| 2 | 표시 텍스트 | `problem.text`, `step.text` | 사용자 표시·문제 표시 메타데이터 |
| 3 | 인쇄/손글씨 구분 | `region.kind` = `printed`\|`handwritten` | 문제(인쇄)와 풀이(손글씨)를 나눠야 한다 |
| 4 | bbox | `region.bbox` = {x,y,width,height} ≥ 0 | 문제/풀이 영역 위치 |
| 5 | 단계 순서 | `solution_steps` = 위→아래, `step_no` 1-based | 풀이 단계 구조화 |
| 6 | 항목별 confidence | `problem.confidence`, `region.confidence`, `step.confidence` (0~1) | 판정 불가 판정·Backend 임계값 |
| 7 | 미인식 표식 | `unreadable:true` + `text:"???"` + `latex_text:null` | **읽을 수 없는 내용을 복원·LaTeX 조작 금지** |
| 8 | 상태 | `status` = COMPLETE/PARTIAL/UNREADABLE | 경계가 파생 |
| 9 | model version | `model_version` | 재현성·A/B 추적 |
| 10 | URL 입력 | `image_reference` (URL) | Backend 가 원본 보관, AI¹ 은 참조만 받음 |

---

## 2. 후보 비교표

| 항목 | **Mathpix OCR API** (v3/text) | **Gemini 3 Flash** (LLM vision) | **Azure AI Document Intelligence** | Google Cloud Vision / Document AI | PaddleOCR + PP-FormulaNet (자체 호스팅) |
|---|---|---|---|---|---|
| 손글씨 수식 → LaTeX | **벤더 자기평가: 전문화·최고** ("best in the market", 수학·과학 필기) — 우리 도메인 성능이 아니라 벤더 주장이다 | 타사 벤치마크 기준 정확도 우위(영어 수학 교과서) | `features=formulas` 로 LaTeX `value` 제공 | 약함 (수학 구조 붕괴) | PP-FormulaNet 가 강함 |
| 인쇄 문제 ↔ 손글씨 풀이 구분 | **줄 단위 `is_printed` / `is_handwritten`** | 판별 계약 없음 (프롬프트 의존) | 줄 단위 `styles[].isHandwritten` | 없음 | 없음 |
| 단계 순서 | 줄 순서 + 좌표 (파생 필요) | 없음 (프롬프트로 유도) | 줄 순서 + polygon | 읽기 순서만 | 줄 순서 |
| bbox | **`lines_json.region` = {top_left_x, top_left_y, width, height}** (축 정렬) | `box_2d` 정규화 좌표 (0~1000) | `polygon` (다각형) | 다각형 | 사각형 |
| 항목별 confidence | **줄별 `confidence` + `confidence_rate`, 페이지 `page_confidence`** | **없음** | 줄/단 confidence 있으나 **수식 confidence 는 하드코딩** | 있음 (수식 quality 아님) | 보통 없음 |
| model version | **`version` 필드** (예 `SuperNet-200`) | 모델 ID (변경 시 불명확) | 모델 ID | 모델 ID | 가중치 파일 |
| URL 입력 | **`src` = URL 또는 base64** | Files API / URL context | URL 입력 가능 | URL·GCS | 직접 배포 |
| 인증 | **`app_id` + `app_key` 헤더** — 이건 **Mathpix API 인증**이다 (AI¹ → 벤더) | API key (GCP) | Entra ID / API key | GCP API key / ADC | 없음(내부망) |
| MIME | 이미지 (PNG/JPEG 등) | png/jpeg/webp/heic 등 | jpg/png/bmp/tiff/heif | png/jpeg/tiff/webp | 직접 결정 |
| **이미지 접근 권한** (인증과 **별개 축**) | **Backend 의 presigned URL** — 벤더가 Backend 자리에 이미지를 가져갈 수 있는 권한. 벤더 API 키와는 무관 | 동일 | 동일 | 동일 | 내부망 |
| 요청 옵션 (우리 계약에 필요한 것) | **`include_line_data=true`**(줄 단위 `cnt` 다각형·`confidence`·`is_printed`/`is_handwritten`·`type`·`error_id`·`conversion_output`) · **`include_lines_json=true`**(영역 단위 `region` + `confidence`/`text_confidence`/`layout_confidence`) · `confidence_threshold` · `formats=["latex_styled"]` · `include_detected_alphabets` · `alphabets_allowed` | 구조화 출력(프롬프트 정의) | `features=formulas` | OCR 옵션 | 모델 옵션 |
| 용량 한도 | JSON 5MB · **URL 다운로드 10MB** · base64 2MB | 이미지 7MB(인라인) / 30MB(GCS) | 파일 크기 제한 있음 | 제한 있음 | 무관 |
| 다운로드 timeout | **15초 (URL 다운로드)** | 지연 4~8.5초 | 동기/비동기 폴링 | 동기 | 무관 |
| **데이터 보존** | **이미지 엔드포인트**(`/v3/text`·`/v3/latex`·`/v3/strokes`·`/v3/batch`) — `improve_mathpix` 기본값 `true` 면 **입력 이미지 최대 90일** + **OCR 결과 캐시 최대 30일**(`/v3/ocr-results` 로 조회 가능). `false` 면 **이미지를 디스크에 남기지 않고** 결과도 캐시하지 않으며 **조회도 불가**(결과는 그 API 응답 안에만 있음). **`/v3/pdf` 는 별도 규칙** — 원본·페이지 이미지 최대 30일, 텍스트 출력 최대 90일(`false` 면 원본은 처리 직후 삭제). — [Data Retention](https://docs.mathpix.com/concepts/data-retention) | 벤더별 상이 | 벤더별 상이 | 벤더별 상이 | 자체 운영 |
| **개인정보 opt-out** | **요청 `metadata.improve_mathpix=false`** — (1) **데이터 품질 개선 사용 금지**(QA 팀이 데이터 못 봄) (2) **OCR 데이터 보존 금지**(미보존·미캐시·비조회). **단 app key 에 account-level 고정값이 있으면 그 값이 요청 설정을 덮어쓴다** → 유효 설정은 **콘솔 또는 지원팀**으로만 확인 — [Privacy](https://docs.mathpix.com/concepts/privacy) | 미지원 | 미지원 | 미지원 | 자체 운영 |
| 응답 지연 **우리 자체 측정** | §4.2-C 에서 p50/p95 를 **우리가** 측정한다 (벤더 안내값으로 대체하지 않는다) | | | | | |
| 비용 (공식, 2026-10-22) | **이미지 $0.002/장** (1M 이상 $0.0015) · `v3/pdf` $0.005/페이지 · Files API $0.0015/페이지 · **첫 API key 활성화 일회성 $19.99** (환불 불가, 테스트 크레딧 $29 제공) — [요금](https://mathpix.com/pricing/api) · [Billing](https://mathpix.com/docs/convert/billing) | 토큰 과금이라 페이지 단위 환산 불가 | 페이지당 과금 | 페이지당 과금 | infra 비용 |
| **요금 예외 규칙** | **텍스트 12줄(rows) 초과 이미지는 `v3/pdf` 페이지 요금($0.005)으로 청구될 수 있음** (벤더 예약 권리). $0.002 → $0.005 = **2.5배**. 우리 사진은 문제+풀이로 12줄을 넘기기 쉬워 **실제 단가는 12줄 기준이 아니다** — [요금](https://mathpix.com/pricing/api) · [Billing](https://mathpix.com/docs/convert/billing) | 이미지당 과금 | 페이지당 | 페이지당 | 무관 |
| 한국어 **인쇄** | **공식 지원 목록에 있음**(Korean (Hangeul), 인쇄 아시아 언어군) | 양호 | **명시적 지원** (`ko` / Hangul) | 인쇄 라틴 강함 | PP-OCR 계열 CJK 가능 |
| 한국어 **필기** | **미지원** — Mathpix 필기 지원은 **영어·힌디어·라틴 문자만**. 우리의 필기 수식(라틴 기호·숫자)은 범위 안이지만, 필기 한국어 메모는 범위 밖 | 한국어 필기 가능(추정) | 한국어 필기 지원(`ko`) | 미지원 | 미지원 |
| 한국어 문제집 이미지 **정확도** | **미측정** — 지원 목록은 "언어를 인식한다"는 뜻이지 우리 사진(한글 인쇄 문제 + 수식 필기)에서 정확하다는 뜻이 아니다. §4 에서 측정한다 | 미측정 | 미측정 | 미측정 | 미측정 |
| 키 관리 | key 별로 enable/disable 가능(비활성화 시 **즉시** 인증 차단, 이력은 남음 — 키 유출 시 가장 빠른 대응). **`app_key` 를 모바일/브라우저 앱에 넣지 말 것**(벤더 권고: app token 또는 서버측 프록시) | | | | | |
| 알려진 오류 유형 | **의미 오류** (`5` → `\overline{0}`) 보고 사례 | **환각**(내용 생성) 위험 | 그리크 기호 오독 (`ϑ`→`9`, `Δ`→`A`) | 수식 구조 붕괴 | 모델별 편차 |
| 회귀 fixture 대응 | `ocr_handoff.json` 은 **fake provider 계약 회귀**이며 이 표의 측정값이 아니다 (§5) | | | | |

> **비교표에 있는 어떤 수치도 우리 도메인의 OCR 정확도가 아니다.**
> "전문화·최고" 류의 표현은 **벤더 자기평가**(marketing)이고, "정확도 우위" 류는 **타사 벤치마크(영어 수학 교과서 코퍼스 · 우리 도메인 아님)**다.
> **우리 이미지의 정확도는 §4 세트로 직접 측정해야 하며, 그 전까지는 "권장·잠정" 이다.**
> 비교 근거 출처: Mathpix 공식 문서(`docs.mathpix.com`), Gemini 공식 가격·문서, Azure Document Intelligence 언어 지원·add-on capability 문서, 공개 벤치마크 보고.
> **한 Benchmark 수치를 여러 비교 근거로 쓰지 않았다** — 벤더마다 측정 코퍼스(인쇄 교과서 vs 필기 노트 vs 문제집 사진)가 달라 숫자 비교 자체가 의미 없다. 실제 측정은 §4 세트로 한다.

### 참고: 후보에서 제외한 것

- **Mistral OCR / Datalab / Tiny OCR / Scriveno / TeXeR**: 수학 특화 근거가 얕거나
  자체 계약 신뢰도를 확인 못 했다. Mathpix 대비 이득이 없다.
- **Tesseract**: 손글씨·수식 품질이 확보되지 않는다.

---

## 3. (이력) Mathpix 권장 검토 — 2026-10-22

> 2026-11-24 현재 결정은 §0의 **deferred**이며, 이 절은 과거 검토 기록이다.

### 왜 이쪽인가

1. **계약을 가장 적게 바꿔도 된다.** `lines_json` 의 `region` 이 우리 `OcrBBox` 와
   형태가 같고(필드명만 다름), 줄 단위 `is_printed`/`is_handwritten` 가 `kind` 에,
   줄별 `confidence` 가 항목별 confidence 에, `version` 이 `model_version` 에 그대로
   대응한다. **provider 별 변환 계층이 가장 얇다.**
2. **미인식 판정에 쓸 신호가 있다.** `conversion_output:false` + `error_id`
   (`math_confidence` / `image_not_supported` / `image_max_size` / `image_no_content`)
   가 곧 우리 `unreadable` 대응 신호다.
3. **응답이 빠르다는 벤더 안내가 있다.** Mathpix 제품 페이지는 이미지 1장 "median image response < 0.5s"를 안내한다. **마케팅 수치이며 계약 보장이 아니다.** 그래도 현장 흐름에 붙일 수 있는 수준이라는 **기대치**만 얻는다. 실제 p50/p95 는 §4.2-C 에서 **우리가 직접 측정**한다.
4. **인증·URL 입력·한도가 문서로 공개**돼 있어 Q22 를 확정하기 쉽다.

### 왜 확정하지 않고 "권장"인가

1. **한국어 문제문 품질이 미확인**이다. Mathpix 공식 목록에 **인쇄 한국어(Hangeul)는 있으나** **필기는 영어·힌디어·라틴만 지원**한다. 우리 사진은 **인쇄 문제(한국어) + 수식 필기(라틴 기호·숫자)** 조합이라 필기 쪽은 범위 안이지만, **한국어 문제집 이미지 전체 정확도는 미측정**이다.
   → §4 평가에서 한글 줄 정확률을 **1순위 측정 항목**으로 둔다. 결과가 나쁘면 §3.3 하이브리드로 전환한다.
- **의미 오류가 보고됐다.** `5` 를 `\overline{0}` 로 읽은 사례는 "그대로 두면 AI¹ 이
  틀린 판정을 한다"는 우리 도메인에 직접 악영향을 준다. → 평가에서 **기호 혼동
  회귀율**을 별도 측정하고, 저해 시 `confidence` 하한 + Backend 측 사용자 확인
  라우팅으로 대응한다.

### 3.1 수학에는 Mathpix, 한글 표시문에는 다른 엔진 (하이브리드) — 예비안

AI① 의 수학 분석에 **필요한 것은 `problem_latex_text` 와 `step.latex_text` 뿐**이고
한국어 산문은 표시용(`problem.text`)이다. 만약 평가에서 한글 줄 정확률이 낮게 나오면:

- **수식 줄** → Mathpix (`is_handwritten=true` / `type=math`)
- **인쇄 한글 줄** → 범용 엔진(예: Azure `prebuilt-read`, `ko` 지원) 또는 Mathpix MMD

을 합치는 **2-provider 하이브리드**를 고려한다. **AI① 의 `OcrProvider` 인터페이스는
provider 1개만 가정하지 않으므로** 확장 여지가 있다(결정 필요: §6 M3).

### 3.2 Mathpix 응답 → 우리 `OcrResult` 변환표 (구현 시 그대로 쓴다)

| Mathpix 필드 | → 우리 필드 | 변환 |
|---|---|---|
| `lines_json.lines[].region.{top_left_x, top_left_y, width, height}` | `OcrBBox.{x,y,width,height}` | 필드명 rename |
| `line_data[].is_printed` / `is_handwritten` | `OcrRegion.kind` | `printed`→`printed`, `handwritten`→`handwritten`, **둘 다 false → `printed` 로 기본**(규칙 확정 필요) |
| `line_data[].text` (MMD) | `OcrStep.text` | 그대로 |
| MMD 안의 `\( ... \)` | `OcrStep.latex_text` | **MMD 파싱 필요** — 가장 큰 변환 비용 |
| `latex_styled` | `OcrStep.latex_text` (단일 수식일 때) | 수식 전용 경로 |
| `line_data[].confidence` | `OcrStep.confidence` | 그대로 (`confidence_rate` 와 구분 필요) |
| `line_data[].conversion_output=false` 또는 `error_id` 존재 | `OcrStep.unreadable=true` | **`"???"` 표식은 벤더에 없어 우리가 합성** |
| `lines` 순서 | `solution_steps` 순서 | **step 개념이 벤더에 없다 — y 좌표 정렬로 파생** |
| `version` | `OcrResult.model_version` | 그대로 |
| **줄 단위 → 단계** | `include_line_data=true` + `include_lines_json=true` 요청 | 둘 다 켠다 — `line_data` 가 confidence·인쇄/필기 판정을, `lines_json` 가 축 정렬 bbox 를 준다 |
| **`confidence` 누락** | 벤더는 점수 미확인/해당없음 값을 **누락하거나 `null`** 로 준다(누락은 "0 이 아니다"를 뜻한다). **우리 스키마는 `confidence` 를 필수 float(0~1) 로 둔다** | **불일치 — M12(미결). 샌드박스 결과 전에는 값을 정하지 않는다** |
| `page_confidence.confidence` | (참고) | `ocr_confidence` 는 현 규칙대로 **항목별 최솟값** |

### 3.3 이 변환이 만드는 한계 (정직하게)

1. **`step` 이 없다.** 벤더는 "줄"만 준다. 한 줄이 여러 풀이 단계일 수 있고,
   한 단계가 여러 줄일 수 있다. → **단계 분할 규칙(규칙 기반 or 후처리) 미정**.
   수식 한 줄 = 한 단계로 가정하는 1차 규칙부터 시작할 것을 권장(§6 M2).
2. **MMD → (text, latex_text) 분리가 필요하다.** MMD 는 `\(...\)` 구분자를 쓴다.
   분리 실패 시 `latex_text=null` + `text` 에 MMD 원문을 두되, 그 경우 해당 step 은
   `unreadable` 로 취급하는지 **미정**(§6 M1).
3. **`confidence` vs `confidence_rate` 2종**이다. 우리는 1개만 받는다.
   → **항목별 confidence = `confidence`**(100% 정답 확률)로 고정하고,
   `confidence_rate` 는 참고값으로 버린다(§6 M4).

---

## 4. 실제 OCR 평가 — 대표 이미지 사례와 측정 기준

### 4.1 대표 이미지 세트 (최소 20장, 권장 40장)

| # | 케이스 | 기대 |
|---|---|---|
| E01 | 한글 인쇄 문제 + 손글씨 풀이 3단계 (가장 흔한 형태) | 정상 |
| E02 | 한글 인쇄 문제 + 손글씨 풀이 5단계 이상 | 정상 |
| E03 | 손글씨만 (인쇄 문제 없음) | 문제 LaTeX 없음 → 분석 skip |
| E04 | 사진 기울어짐 (역회전 필요) | 정상 |
| E05 | 흐릿·저해상도 | 일부 또는 전체 `unreadable` |
| E06 | 어두움·역광·그림자 | 정상 또는 unreadable |
| E07 | 분수·제곱근·분모 0 등 기호 혼재 | 정상 |
| E08 | 노이즈·접힌 모서리·배경 물체 | 정상 |
| E09 | 한글 산문이 긴 문제 | 문제 LaTeX 정상, 표시문 정확도 측정 |
| E10 | 빈 페이지·도장만 | 전부 unreadable |
| E11 | 2문제 이상이 한 장 | **범위 밖**(현재 1문제 1장) — 측정만 하고 지원 여부 결정 |
| E12 | 세로 방향·2단 구성 | 정상(추가 검증) |
| E13 | 같은 문제를 다른 학생이 쓴 손글씨 (2인) | 동일 LaTeX 판정 |
| E14 | 계산기·그래프 등 비텍스트 요소 포함 | 해당 요소 무시, 수식은 정상 |
| E15 | 한글 문제 + 영어 혼재 문장 | 정상 |
| E16 | 같은 수식의 여러 표기 (`2x-6=6` / `2x−6＝6`) | 정규화 후 동일 판정 |

각 세트는 **학생 3인 이상이 쓴 서로 다른 사진**으로 구성한다(필요한 경우) —
한 학생의 손글씨만으로 정확도를 측정하면 안 된다.

### 4.2 측정 기준 (지표 정의)

**A. OCR 자체 정확률**

| 지표 | 정의 | 목표(잠정) |
|---|---|---|
| `problem.latex_text` 완전일치율 | 정답 LaTeX 문자열 완전일치 | ≥ 90% |
| `problem.latex_text` **동치율** | SymPy 동치 판정 통과율 (문자열이 달라도 수학적으로 같으면 통과) | **≥ 99%** |
| 단계 경계 정확률 | OCR 단계 구분이 정답과 일치(구간 F1) | ≥ 85% |
| 단계 순서 정확률 | 순서 완전일치 / Kendall τ | 완전일치 ≥ 80% |
| 항목 confidence calibration | confidence 구간별 실제 정확률 | 단조 증가, ECE ≤ 0.10 |
| printed/handwritten 분류 정확률 | 줄 단위 | ≥ 95% |
| bbox IoU | 정답 bbox 대비 (정답 라벨이 있을 때만) | median ≥ 0.80 |
| **미인식 판정** | unreadable 판정 Precision/Recall | Recall ≥ 0.90 (Recall 우선 — 오탐보다 누락이 위험) |
| **기호 혼동 회귀율** | `5`↔`S`, `Δ`↔`A`, `ϑ`↔`9` 등 의미 변경 오류 | **≤ 1%** (문자열이 아니라 의미) |

**B. AI① 임팩트 (이게 진짜 지표다)**

| 지표 | 정의 | 목표(잠정) |
|---|---|---|
| 최종 오류 판정 일치율 | OCR 결과를 `/analyze` 에 넣었을 때 정답 오류(단계·유형)와 일치 | ≥ 90% |
| 오개-concept 오염율 | OCR 오류로 잘못 붙은 오개념 | ≤ 3% |
| `REVIEW_REQUIRED` 적정 비율 | 사람이 봐야 하는 사진이 실제로 사람 확인을 요하는 비율 | 15~35% (너무 낮으면 위험 회피, 너무 높으면 UX 손실) |

**C. 운영 지표**

| 지표 | 측정 |
|---|---|
| 지연 | p50 / p95 (images 1장 기준) |
| 비용 | 1장당 실측 (E20 규모에서 산출) |
| 실패율 | 5xx / 타임아웃 비율 |
| 미인식 판정 후 **재시도** 효과 | 동일 이미지 재전송 시 결과 동일성(결정론성) |

### 4.3 평가 방법

1. **정답 라벨**을 사람이 직접 만든다(기대 LaTeX + 단계 경계 + unreadible 표시).
   라벨 파일은 `tests/golden/` 과 분리해 **`eval/`(비-회귀)** 로 둔다.
2. provider 응답을 **그대로 저장**한다(원본 JSON) — 재현·재분석에 필요하다.
3. 평가 스크립트는 `tests/` 밖에 둔다(회귀 스위트에 포함되면 느려지고, vendor 응답에
   의존해 CI가 불안정해진다).
4. **Secret 은 저장하지 않는다.** 원본 응답에 키가 포함되면 그 필드만 제거한다.

---

## 5. `ocr_handoff.json` 의 accuracy gate 는 **회귀 fixture 기준**이다

`tests/golden/ocr_handoff.json` 의

```json
"gate": { "step_accuracy": 1.0, "error_type_accuracy": 1.0 }
```

는 **fake provider 가 돌려준 결정적 입력**에 대한 **계약 회귀 기준**이다.

- **실제 OCR 모델의 측정값이 아니다.**
- fake provider 는 사람이 만든 이상적인 입력(LaTeX 가 이미 깨끗하게 주어진 상태)을
  돌려준다. 그래서 이 gate 는 **"handoff 변환 규칙이 안 깨졌다"** 만 보장한다.
- **실제 OCR 품질은 §4 의 E01~E16 세트로 별도 측정**해야 하며, 그 값은 이 저장소에
  넣지 않는다(커밋하면 vendor 응답에 의존한 스냅샷이 되며, 계정·비용·PII 문제가 생긴다).
- `golden_20` / `golden_100` 과 마찬가지로 **회귀 게이트이지 품질 지표가 아니다.**

---

## 6. `OcrResult` 계약과 불일치하는 부분 · 미결 결정

아래는 **실제 provider 를 붙일 때 반드시 풀어야 하는 지점**이다.

| # | 불일치 | 현재 계약 | 필요한 변경/결정 | 상태 |
|---|---|---|---|---|
| M1 | MMD → `(text, latex_text)` 분리 없음 | `text` 와 `latex_text` 별도 | MMD 파싱기 필요. 실패 시 `latex_text=null` + `unreadable` 처리 여부 | **미결** |
| M2 | "step" 개념 부재 | `solution_steps[]` | 줄→단계 파생 규칙(1줄=1단계 가정 vs 기하 기반) | **미결** |
| M3 | provider 1개 가정 | `OcrProvider` 단일 | 한글 품질 미달 시 2-provider 하이브리드 필요 여부 | **미결** |
| M4 | confidence 2종 | `confidence` 1종 | `confidence` 채택, `confidence_rate` 는 참고로 폐기 | **잠정** |
| M5 | `kind` 판별이 3값 가능 | `printed`\|`handwritten` | 둘 다 false 인 줄의 기본값 | **미결** |
| M6 | **미인식 `"???"` 표식 부재** | `text:"???"` | 벤더 `error_id`/`conversion_output` 에서 **우리가 합성**. `error_id` 는 **값마다 의미가 다르다** — `image_not_supported`/`image_max_size`/`image_no_content` = 텍스트가 아닌 영역(단계 아님), `math_confidence` = 못 읽은 수식(**미인식 단계**). 값별 규칙이 필요하다 | **미결** |
| M7 | 다중 페이지 | `page_index` 선택 | 벤더는 1이미지=1페이지. 페이지 병합·순서 규칙 | **미결** |
| M8 | HTTP 429 없음 | 502/503 만 있음 | rate limit 을 429 로 노출할지 502 로 흡수할지 | **미결** |
| M9 | `OcrBBox` 필드명 | `{x,y,width,height}` | Mathpix `region` 은 `{top_left_x, top_left_y, width, height}` | **해결**(rename) |
| M10 | `model_version` | `str` | Mathpix `version` → 그대로 | **해결** |
| M11 | `status` | COMPLETE/PARTIAL/UNREADABLE | 벤더에 없음 → **경계가 파생**(현 규칙 유지) | **해결** |
| M12 | **`confidence` 누락/누락 허용** | 우리 `confidence` 는 **필수 float(0~1)** 이나 벤더는 미확인 값을 **누락 또는 `null`** 로 준다(누락은 "0 이 아니다"를 뜻한다). **아직 결정하지 않았다** — 샌드박스 응답에서 실제 누락 빈도·맥락을 보기 전에는 누락값을 `0.0` 으로 확정하거나 어떤 값으로 코드화하지 않는다 | **미결** |
| M13 | **이미지 보존 기간 / `improve_mathpix` 계약** | 우리 계약에 개념 없음 | 이미지 엔드포인트는 `improve_mathpix` 기본 `true` 면 입력 이미지 최대 90일 + 결과 캐시 최대 30일, `false` 면 미보존·미캐시·비조회. **단 account-level 고정값이 요청 설정을 덮어쓸 수 있고** 우리 요청·응답으로 그 고정 여부를 확인할 방법이 없다 → 유효 설정은 **콘솔 또는 지원팀으로만 확인**. **청구·감사용 요청 메타데이터는 opt-out 여부와 무관하게 보존** | **미결** |
| M14 | **`OcrRegion` 에 id 필드가 없다** | `OcrStep.region_id` 는 있는데 region 자체에는 식별자가 없다 — step→region 연결이 region 쪽에서 닫히지 않는다 | region id 를 `OcrRegion` 에 추가할지, 없으면 `OcrStep.region_id` 만으로 충분한지. **M14 미결 동안 하네스는 `region_id` 를 null 로 두고** provider line id 를 eval 초안(`step_region_links`)에 보존한다 — 존재하지 않는 region 을 참조하지 않는다 | **미결** |
| M15 | **과금 대상 텍스트 행 정의 미확인** | 벤더 요금 문서에서 "line" 이 어떤 타입까지 포함하는지 확인하지 않았다. 하네스는 보수적으로 text/math 계열을 센다 | 요금 문서 확인 후 타입 매핑 확정 | **미결** |
| M16 | **라벨 키의 반복 호출 안정성 미확인** | provider 응답 id(32자리 hash)는 반복 호출에서 안정적임이 확인되지 않았다 → **정답 라벨 키로 쓸 수 없다.** 원본 줄 인덱스도 안정성 확인이 필요하다 | 같은 이미지를 반복 호출해 줄 대응이 일관되는지 실측 | **미결** |

---

## 7. Q21 · Q22 · Q24 잠정 결정

> **잠정**이다. Backend 승인 전까지 확정 계약이 아니다. §4 평가 결과에 따라 바뀔 수 있다.

### Q21 — OCR 수행 주체

**잠정: AI¹ 이 `OcrProvider` 구현체를 통해 벤더를 호출한다.**

- 벤더 인증키(`app_id` + `app_key`)는 **AI¹ 서버 환경변수**에만 둔다. Backend 로 전달하지 않는다.
- **app key 에 `improve_mathpix` 가 account-level 로 고정될 수도 있다** — 엔터프라이즈 계약 등에서 Mathpix 가 설정하며, **고정값이 있으면 요청 `metadata.improve_mathpix` 를 덮어쓴다.** 유효 설정은 **콘솔 또는 지원팀**으로만 확인해 둘 것(사용자의 사적 자료는 절대로 쓰지 않는다).
  - `app_id`/`app_key` 는 **Mathpix API 인증**이다(AI¹ → 벤더). **이미지 접근 권한과는 다른 축**이다.
  - **이미지 접근 권한**은 Backend 의 presigned URL 이 담당한다(벤더 → Backend 스토리지). 이 두 인증을 한 덩어리로 취급하지 않는다.
  - 벤더 권고: **`app_key` 를 모바일/브라우저 앱에 넣지 않는다.** 우리는 서버측에서만 호출한다.
  - 키 유출 시: key 를 **비활성화**하면 즉시 인증이 차단된다(이력은 남음).
- Backend→AI① 인증(Q3)과 **분리**한다 — 두 인증 주체·회전 주기가 다르다.
- Backend 는 `image_reference`(원본 보관처 URL)만 준다. **AI¹ 은 이미지를 받지 않는다.**
- 대안: Backend 가 벤더를 직접 호출해 OCR 결과만 AI¹ 에 넘긴다.
  → 이 경우 AI¹ 은 `OcrProvider` 대신 **"OCR 결과를 받아 쓰는 쪽"**이 되어
  `/ocr` 경계가 불필요해진다. **결정 필요 — Bench상 AI¹ 호출이 계약 변경을 최소화한다.**

### Q22 — `image_reference` 규약

**잠정 (Mathpix 기준):**

**인증은 두 축이다 — 주체도 대상도 다르다.**

| 축 | 주체 → 대상 | 무엇을 증명하는가 | 우리 구현 |
|---|---|---|---|
| **인증 ① — 벤더 API 인증** | **AI¹ → Mathpix** | **AI¹ 이 Mathpix API 를 호출할 자격** — `app_id` + `app_key` 요청 헤더 | AI¹ **서버 환경변수**에서 읽어 헤더로 보낸다. Backend 로 전달하지 않는다. `app_key` 는 모바일/브라우저 앱에 넣지 않는다(벤더 권고) |
| **인증 ② — 이미지 접근 권한** | **Mathpix → Backend 이미지 저장소** | **Mathpix 가 그 이미지를 실제로 가져갈 수 있는가** — Backend 가 발급한 **presigned HTTPS URL** | Backend 가 발급한 presigned URL 을 AI¹ 이 `image_reference` 로 넘긴다. presigned URL 이 만료되면 Mathpix 는 이미지를 **못 가져간다** |

> **이 둘을 한 덩어리로 취급하지 않는다.**
> `app_id`/`app_key` 가 있어도 presigned URL 이 없으면 Mathpix 는 이미지를 못 가져간다.
> presigned URL 이 있어도 `app_id`/`app_key` 가 없으면 API 호출 자체가 인증 실패한다.
>
> **Q22 가 정의하는 `image_reference` 규약은 축 ②다.** 축 ①은 요청 헤더 문제지
> `image_reference` 의 문제가 아니다 — 그래서 아래 규약 표에는 축 ②만 넣는다.

**`image_reference` 규약 (축 ②):**

| 항목 | 값 | 근거 |
|---|---|---|
| 형식 | **HTTPS URL** (벤더가 직접 GET) | 벤더 `src` 는 URL 또는 base64. **base64 는 AI¹ 경유 대역폭 낭비** |
| 만료 | **15분 이내** — **프로젝트 권장값이며 벤더 요구 사항이 아니다** | 벤더가 요구하는 것은 다운로드 **timeout 15초** 만이다. presigned URL 의 TTL 은 Backend 를 발급하는 쪽의 선택 — 15분 은 timeout 대비 충분한 여유를 두면서 URL 노출 시간을 최소화한 값 |
| MIME | `image/png`, `image/jpeg` | 벤더 허용 목록. 웹프로는 jpeg 다수 |
| **용량** | **10MB 이하** | 벤더 URL 다운로드 한도. AI¹ 이 `Content-Length` 로 사전 확인 가능 ([Limits & Quotas](https://docs.mathpix.com/reference/limits-and-quotas)) |
| 해상도 | **장변 1000px 이상**, 300 DPI 권장 | AI① `parser` 의 기호 인지가 해상도에 직결. 저해상도에서 `\frac` 이 깨진다 |
| 사전 검증 | AI¹ 이 MIME·용량만 확인하고, **벤더 다운로드 실패는 502** | 실제 접근은 벤더 책임 |
| **보존 기간 / 개인정보** | **요청 `metadata.improve_mathpix=false`** — 데이터 품질 개선 사용 금지 + OCR 데이터 미보존·미캐시·`/v3/ocr-results` 비조회. **단 account-level 고정값이 있으면 그 값이 우선**(요청·응답으로 확인 불가 → **콘솔/지원팀 확인**). **청구·감사용 요청 메타데이터는 opt-out 여부와 무관하게 보존** | [Privacy](https://docs.mathpix.com/concepts/privacy) · [Data Retention](https://docs.mathpix.com/concepts/data-retention) |
| **12줄 요금 규칙** | 우리 사진은 12줄을 넘길 수 있어 **1장당 $0.005 가 될 수 있다** — 비용 산정 시 $0.002 로 고정하지 않는다 | [Mathpix 요금](https://mathpix.com/pricing/api) · [Billing 가이드](https://mathpix.com/docs/convert/billing) |

**미정:** 해상도 하한을 강제(저해상도면 422)할지, OCR 로 넘기고 `unreadable` 로
내려갈지. → **권장: 강제하지 않는다**(실사용 사진 품질을 예측하기 어렵다).

### Q22-추가 — `metadata.improve_mathpix` 계약 (개인정보)

**공식 근거** — [Privacy](https://docs.mathpix.com/concepts/privacy) · [Data Retention](https://docs.mathpix.com/concepts/data-retention)

**요청 설정으로 막히는 것**

`metadata.improve_mathpix=false` 를 보내면 아래 두 가지가 **동시에** 적용된다.

1. **데이터 품질 개선 사용 금지** — Mathpix QA 팀이 해당 데이터를 볼 수 없고 콘솔에도 노출되지 않는다.
2. **OCR 데이터 보존 금지** — 이미지 엔드포인트(`/v3/text` 등)는 입력 이미지를 디스크에 남기지 않고
   OCR 결과도 캐시하지 않는다. `/v3/ocr-results` 로 **조회도 불가**다. 결과는 **그 API 응답 안에만**
   있다 → 우리가 응답을 받아 로컬에 보관해야 한다.

**요청 설정으로 못 하는 것 (중요)**

- **account-level 고정값은 덮어쓴다.** Mathpix 는 app key 에 `improve_mathpix` 를 **account-level 로
  고정**할 수 있다(엔터프라이즈 계약 등). **account-level 값이 설정된 경우 그 값이 모든 엔드포인트에서
  요청 설정을 덮어쓴다.**
- **요청 설정으로는 고정 여부를 확인할 수 없다.** OCR **응답에도 유효 설정을 확인하는 근거가 없다** —
  항상 설정으로 확인해야 한다.
- **확인 방법은 콘솔 또는 Mathpix 지원팀(`support@mathpix.com`)** 으로만 가능하다.

**opt-out 이어도 보존되는 것이 있다**

`status` · `timestamp` · `page count` · `error code` 는 **청구·감사용 요청 메타데이터**로
보존된다. `improve_mathpix` 값이 어느 쪽이더라도 그롭다. opt-out 이 "아무것도 안 남는다"는 뜻이 아니다.

**우리 구현** — `eval/mathpix_client.py` 의 기본 요청은 **항상** `metadata.improve_mathpix=false` 를
보낸다(플래그를 아예 생략하지 않는다 — 생략하면 벤더 기본값 `true` 가 적용된다). 단 **account-level 고정값이 있으면 그 값이 우선**하므로, 요청 설정만으로는 보장이 되지 않는다.


### Q24 — `confidence` 정의

**잠정:**

| 층 | 정의 | 출처 |
|---|---|---|
| `region.confidence` / `step.confidence` | 해당 항목이 100% 맞을 확률 (0~1) | 벤더 줄별 `confidence` |
| `problem.confidence` | 문제 영역 confidence | 벤더 |
| `ocr_confidence` (전체) | **모든 항목 confidence 의 최솟값** (현 규칙 유지) | 경계 파생 |
| unreadable 판정 | `confidence < 임계값` **또는** 벤더가 줄을 제외(`conversion_output=false` / `error_id` 존재) | 벤더 + 경계 |
| AI① 분석 `confidence` | **분류 신뢰도**. OCR 값과 섞지 않는다 (현 규칙 유지) | AI¹ classifier |

- **추천 임계값: 0.85** (§4 평가로 확정). 벤더 1차 필터로 `confidence_threshold` 를
  넘겨도, **경계에서 다시 확인**한다(벤더 필터는 면제될 수 있다).
- **병합 규칙 없음.** 여러 줄의 confidence 를 합산·평균하지 않는다.
  "한 줄이라도 못 믿으면 전체를 못 믿는다" 는 현재 `ocr_confidence = min` 규칙과 일치.
- **confidence 가 누락될 수 있다.** 벤더는 미확인 값을 누락/`null` 로 준다. 우리 스키마는 필수 float 이므로 adapter 규칙이 필요하다 → **M12**. **아직 결정하지 않았다** — 샌드박스 응답을 보기 전에는 누락값을 `0.0` 으로 확정하거나 어떤 값으로 코드화하지 않는다.
- `ocr_confidence` 와 분석 `confidence` 는 **이름·의사 모두 분리**한다. 현 계약이 이미
  `ocr_confidence` / `confidence` 로 갈라 두었고, 이 구분을 유지한다.

### 추가 — Q22 와 연동된 신규 오류 코드 (필요성 판단)

| 코드 | HTTP | 상황 | 필요성 |
|---|---|---|---|
| `E_OCR_NOT_CONFIGURED` | 503 | provider 미설정 | **구현됨** |
| `E_OCR_PROVIDER_ERROR` | 502 | 벤더 호출 실패·계약 위반 | **구현됨** |
| `E_OCR_RATE_LIMITED` | 429 | 벤더 rate limit | **추가 필요**(M8) |
| `E_OCR_IMAGE_INVALID` | 422 | MIME·용량 사전 검증 실패 | **추가 필요**(Q22) |

> 이 두 코드는 **추가하지 않았다.** 본 Phase 는 문서·조사만 한다.

---

## 7-1. 평가 하네스 (PHASE 9-9B) 와 선행 조건 현황

측정 하네스를 `eval/` 에 넣었다. **실제 provider 결과·학생 이미지·정답 라벨·리포트는
`eval/_local/` 아래에만 두고 gitignore 된다.** `tests/golden/` 에 실제 provider 결과를
고정하지 않는다(회귀 테스트와 분리).

| 파일 | 역할 |
|---|---|
| `eval/README.md` | 사용법 · 선행 조건 · 지표 매핑 · 미결 항목별 필요 데이터 |
| `eval/labels.schema.json` | 정답 라벨 스키마 |
| `eval/labels.example.json` | 형식 시연용 **합성** 예시(실제 아님) |
| `eval/mathpix_client.py` | `v3/text` 호출 래퍼 — 자격증명은 **환경변수만**, 원본 응답은 로컬 저장 |
| `eval/mathpix_adapter.py` | 응답 → `OcrResult` 변환 **프로토타입**(M1·M2·M5·M6·M12 미결) |
| `eval/sandbox_probe.py` | 1회 호출로 응답 형식 · confidence 누락 · `error_id` 동작 확인 |
| `eval/run_batch.py` | **오프라인 배치 러너** — 기본 dry-run. `--run` + `--max-calls` 를 **둘 다** 명시해야 실제 호출 가능. presigned URL manifest 는 실행 직전에 준비하는 로컬 평가 자료(발급은 하지 않음). URL 은 stdout·로그·`results.jsonl`·리포트에 기록하지 않는다 |
| `eval/measure.py` | §4.2 A/B/C 지표 계산 (동치율은 AI¹ 판정 기준 `verifier.equivalent` 사용) |
| `eval/selfcheck.py` | 하네스 자체 검증 — **문서에 게시된 Mathpix 예시 응답** 기준(실측 아님) |

**선행 조건 현황 — 5개 중 5개 미충족**

| # | 조건 | 현황 |
|---|---|---|
| 1 | Mathpix 계정·API key | **미충족** — `MATHPIX_APP_ID`/`MATHPIX_APP_KEY` 미설정, 로컬에도 키 파일 없음 |
| 2 | 예산 승인 | **미승인** — 첫 key 활성화 일회성 $19.99 포함 |
| 3 | Improve Mathpix 보존 opt-out | **미확인** — key 설정 상태를 볼 key 가 없다. **요청 설정으로는 account-level 고정 여부를 확인할 수 없고 응답에도 근거가 없다** → **콘솔 또는 지원팀**으로 확인해야 한다. 기본값 `true` 면 학생 사진이 벤더 QA 팀에 노출된다 |
| 4 | E01~E16 실제 사진 20장 이상 + 정답 라벨 | **미준비** — 로컬·프로젝트 어디에도 평가 이미지·라벨 없음 |
| 5 | Backend presigned URL | **미준비** |

API 엔드포인트 도달은 확인됐다(자격증명 없이 `401` — 인증만 실패). 하지만 위 1~5가
충족되기 전에는 **정확률·지연·비용 실측값이 나올 수 없다.** 따라서 이번 Phase 는
**측정을 하지 않았고** M1~M8·M12·M13·Q21·Q22·Q24 를 **확정하지 않았다.**

**PHASE 9-9B.3 — M14 region 연결 정합성 · 결정안 정리**

fake payload 로만 검증했다. 실제 Mathpix 호출·학생 이미지·API key 는 쓰지 않았다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | `OcrStep.region_id` 에 provider 의 line id 를 넣어 **존재하지 않는 region 을 참조**했다 | M14 미결 동안 **`region_id` 는 null** 로 둔다. `OcrResult` 의 `OcrRegion` 에 식별자가 없으므로 어떤 값을 넣어도 dangling reference 다 |
| 2 | provider line id 를 초안에도 온전히 보존하지 않았다 | eval 초안 `steps[].source_line_id` + `step_region_links[]` 에 보존한다. 연결이 미해결임을 `linked=false` / `reason="M14"` 로 명시 |
| 3 | 연결이 필수인 상황을 표현할 방법이 없었다 | `to_ocr_result(draft, region_link_policy="require")` → 미해결 연결이 있으면 **M14 미결 오류로 변환을 보류**한다. 기본값은 `"null"` |
| 4 | 측정 리포트에 미해결 연결 건수가 없었다 | `A_ocr_accuracy.region_links_unresolved` 로 집계 |

**결정안 자체 정리**

| # | 항목 | 처리 |
|---|---|---|
| 5 | Q22 의 인증 설명이 `image_reference` 규약 표 안에 섞여 축이 혼재했다 | **인증 ①(AI¹ → Mathpix API 인증, `app_id`/`app_key`)** 과 **인증 ②(Mathpix → Backend 이미지 접근 권한, presigned URL)** 을 **별도 축 블록으로 분리**했다. Q22 규약 표에는 축 ②만 남겼다 |
| 6 | §2 후보 비교표의 **데이터 보존** 행이 2회 중복 | 중복 행 1개 제거 |
| 7 | §3 "왜 확정하지 않고 권장인가" 의 한글 줄 측정 문단 2회 중복 | 중복 문단 제거 |
| 8 | Q24 의 `ocr_confidence = min` 설명 2회 중복 | 중복 문장 제거 |
| 9 | 비교표 한국어 인쇄 행의 CJK 오탈자 | `PP-OCR` 뒤 한자(U+7CFB)를 `계열`로 수정 |
**PHASE 9-9B.5 — `improve_mathpix` 개인정보 계약 정합성**

공식 문서([Privacy](https://docs.mathpix.com/concepts/privacy) · [Data Retention](https://docs.mathpix.com/concepts/data-retention))를 근거로 정리했다.
**실제 Mathpix 호출·API key·이미지는 쓰지 않았다.**

| # | 문제 | 수정 |
|---|---|---|
| 1 | eval 클라이언트가 `metadata.improve_mathpix` 를 **생략**했다 — 생략하면 벤더 기본값 `true` 가 적용 | **기본 요청에 항상 `metadata.improve_mathpix=false`** 를 넣는다. 플래그를 생략하는 경로를 **두지 않는다** |
| 2 | 문서가 "opt-out 시 24시간 이내 삭제" 라고 적어 **현재 공식 문서와 어긋났다** | 공식 문서 기준으로 정정 — 이미지 엔드포인트는 `true` 면 **입력 이미지 최대 90일 + 결과 캐시 최대 30일**, `false` 면 **미보존·미캐시·비조회**(`/v3/ocr-results` 조회 불가) |
| 3 | "key 설정이 요청 플래그보다 우선" 이라는 표현이 **account-level 고정값** 과 요청 설정을 혼동했다 | **account-level 고정값이 있으면 그 값이 요청 설정을 덮어쓴다**(모든 엔드포인트). 표현을 정확히 수정 |
| 4 | pin 여부를 요청·응답으로 확인할 수 있다는 오해를 만들 수 있었다 | **요청 설정만으로는 확인 불가**이고 **OCR 응답에도 유효 설정의 근거가 없다** → **콘솔 또는 Mathpix 지원팀**으로만 확인하도록 명시 |
| 5 | opt-out = "아무것도 남지 않는다" 는 오해 가능 | **청구·감사용 요청 메타데이터**(status·timestamp·page count·error code)는 **opt-out 여부와 무관하게 보존**됨을 명시 |
| 6 | presigned URL 15분 TTL 을 벤더 요구처럼 적어 있었다 | **프로젝트 권장값**으로 명시 — 벤더가 요구하는 것은 **다운로드 timeout 15초** 뿐이다 |
| 7 | 하네스 테스트 수 표기가 실제와 어긋났다 | `eval/README.md` 의 하네스 테스트 수를 실제 값으로 갱신 |

**PHASE 9-9B.6 — privacy 불변조건 · 오프라인 배치 러너**

**실제 OCR/API 호출은 하지 않았다.** fake transport 로만 검증했다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | `build_body(..., improve_mathpix=True)` 로 opt-out 을 해제할 수 있었다 | **참을 넣으면 `ValueError` 로 거절**한다. eval 하네스에는 opt-in 경로가 없다 |
| 2 | presigned URL 이 stdout·로그·`results.jsonl`·리포트에 새어 나갈 수 있었다 | **URL 비노출 불변조건**을 코드와 테스트로 고정 — 전체 URL·쿼리·**개별 쿼리 파라미터**까지 치환 |
| 3 | presigned URL 이 실수로 호출 대상에 들어갈 수 있었다 | manifest 는 **별도 로컬 파일**(`eval/_local/presigned_urls.json`)로 분리. **URL manifest 없음 / ID 대응 오류 / https 아님 → 호출 전에 실패** |
| 4 | 평가 실행이 실수로 시작될 수 있었다 | 기본 **dry-run**(호출 0회, 파일 0 writes). 실제 호출은 `--run` **과** `--max-calls` 를 **둘 다** 명시해야만 가능 |
| 5 | 원본 응답과 결과 행의 위치가 섞여 있었다 | 원본 응답은 `eval/_local/responses/` **뿐**, `measure.py` 입력 행은 `eval/_local/results.jsonl`, 실행 로그도 `eval/_local/run_log.jsonl` — 전부 gitignore |
| 6 | Backend presigned URL 발급을 이 도구가 맡는 것처럼 여겨질 수 있었다 | **발급하지 않는다.** manifest 는 **실행 직전에 준비하는 로컬 평가 자료**로 명시 |
| 7 | 일부 샘플 실패가 전체를 죽이거나 성공으로 덮어써질 수 있었다 | **샘플별 성공·실패를 모두 보존**한다. 실패해도 다음 샘플로 진행 |
| 8 | 결과 행의 필드가 `measure.py` 입력 계약과 어긋날 수 있었다 | 결과 행 = case/sample ID · 원본 응답 파일 · draft · elapsed · **sanitized error** 만. `measure.py` 로 읽히는지 테스트로 확인 |

**PHASE 9-9B.7 — 배치 산출물 안전성 · 반복 측정 보완**

**실제 OCR/API 호출은 하지 않았다.** fake transport 로만 검증했다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | 출력 경로가 `eval/_local` 밖으로도 지정될 수 있었다 | **resolve 기준**으로 `eval/_local` **안**인지 검증한다. 밖이면 **호출·파일 생성 없이** 실패 (`../` · `..\` 탈출 포함) |
| 2 | `case_id`/`sample_id` 를 파일명에 그대로 쓰면 경로 탈출이 가능했다 | **안전한 slug** 로 변환한다(경로 구분자 제거). slug 충돌이 있으면 실패. **원래 id 는 결과 행에 보존** |
| 3 | 같은 경로로 재실행하면 기존 결과를 조용히 덮어썼다 | **덮어쓰지 않는다** — 실행마다 충돌 없는 `run_id` 하위 경로(`eval/_local/runs/<run_id>/`)를 쓴다. 지정 경로가 이미 있으면 실패 |
| 4 | 반복 측정을 모으는 방법이 없었다 | **명시적 누적**(`--accumulate`)을 추가했다 |
| 5 | 반복 측정을 `case_id` 만으로 묶어 **서로 다른 이미지를 비교**할 수 있었다 | 그룹을 **`(case_id, sample_id)`** 기준으로 바꾼다 — **다른 이미지는 반복 측정으로 계산하지 않는다** |
| 6 | 같은 `case_id` 가 다른 `sample_id` 를 가리키는 상태로 누적될 수 있었다 | 누적 시 **case ↔ sample 대응을 검증**하고 어긋나면 거부한다 |
| 7 | adapter 변환이 실패하면 원본 응답 경로를 찾을 수 없었다 | 원본 응답 저장 **직후** `raw_response_file` 을 기록한다 — 변환 실패 후에도 추적 가능 |
| 8 | 호출 상한이 CLI 검사에만 있었다 | `run_batch()` **자체**가 `max_calls <= 0` 을 거부한다 |


**PHASE 9-9B.8 — 반복 측정 독립성 · 지표 집계 정합성**

**실제 OCR/API 호출은 하지 않았다.** fake transport 로만 검증했다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | 결과 행에 어떤 실행의 것인지가 없었다 | `run_id` 와 **호출별 `attempt_id`** 를 기록한다 |
| 2 | 같은 결과 파일을 두 번 넣어도 반복 측정으로 세어질 수 있었다 | **같은 파일 경로**를 두 번 주면 거부한다 |
| 3 | 같은 `run_id` 를 두 번 넣어도 걸리지 않았다 | **중복 `run_id`** 를 거부한다 |
| 4 | 한 파일에 여러 `run_id` 가 섞여 있어도 누적됐다 | **혼합 `run_id`** 와 **`run_id` 누락** 을 거부한다 |
| 5 | 같은 호출이 다른 이름으로 들어올 수 있었다 | **`attempt_id` 중복** 을 거부한다 — 독립된 호출이 아니다 |
| 6 | 누적 산출물을 조용히 덮어썼다 | **덮어쓰지 않는다** — 이미 있으면 실패 |
| 7 | 실패 호출까지 줄 인덱스 안정성 비교에 들어갔다 | M16 비교는 **성공 호출만** 한다. 실패 호출은 줄 인덱스 대응이 없다 |
| 8 | 성공 1건 + 실패 1건이 unstable 로 판정될 수 있었다 | **성공 2회 미만이면 `null`(미확인)**. 실패 호출 수는 **별도 집계**한다 |
| 9 | 첫 호출이 실패하면 sample 전체가 미측정됐다 | per-sample 정확도는 **첫 번째 성공 결과** 기준. 성공이 하나도 없는 sample 만 실패/미측정 |
| 10 | 반복 호출 비용이 누락됐다 | latency·비용·실패율을 **모든 실제 호출 시도** 기준으로 계산한다 |


**PHASE 9-9B.9 — 부분 실행 coverage · provider/adapter 실패 구분**

**실제 OCR/API 호출은 하지 않았다.** fake transport 로만 검증했다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | 결과 행에서 provider 호출 실패와 adapter 변환 실패를 구분하지 못했다 | `provider_succeeded` · `adapter_succeeded` · `error_stage`(`provider`/`adapter`) 를 기록한다 |
| 2 | provider 성공 + adapter 실패가 provider 실패로 집계될 수 있었다 | **provider 실패로 세지 않는다** — 응답을 받았고 원본도 저장되었으므로 호출 성공·지연·원본이 모두 보존된다 |
| 3 | 부분 실행의 실패율이 과소 표시됐다 | **coverage**(`samples_attempted / 전체 label`)와 `samples_unattempted` 를 보고한다. **시도하지 않은 sample 은 실패로 세지 않는다** |
| 4 | sample 실패율의 분모가 전체 label 이었다 | **실제 시도된 sample** 을 분모로 한다. 시도된 sample 이 없으면 `null` |
| 5 | end-to-end 실패율의 의미가 불분명했다 | `end_to_end_failure_rate` 로 분리하고 **분모가 전체 label** 임을 명시한다 |
| 6 | provider 실패와 adapter 실패가 한 지표로 섞여 있었다 | `provider_failure_rate`(분모=모든 시도) 와 `adapter_failure_rate`(분모=provider 성공 시도) 로 **분리**한다 |
| 7 | adapter 실패(=`draft` 없음(provider 성공))의 비용이 0 으로 누락될 수 있었다 | **0 으로 세지 않고 unknown** 으로 집계한다. `cases_cost_unknown_provider_failed` / `cases_cost_unknown_adapter_failed` 로 구분하고 추정 합계와 함께 표시한다 |


**PHASE 9-9B.10 — 측정 입력 계약 · attempt outcome 정합성**

**외부 호출 없음.** fake data 만으로 검증했다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | 명시 상태 필드가 `draft`/`error` 와 모순되어도 조용히 재계산되고 있었다 | **입력 계약 오류**(`ResultContractError`)로 **중단**한다. `measure()` 도 즉시 중단 — 잘못된 집계가 조용히 나오는 게 더 나쁘다 |
| 2 | 명시 필드가 일부만 있는 행을 구분하지 못했다 | 셋 중 **하나라도** 있으면 새 형식으로 보고 **셋 다** 있어야 한다 |
| 3 | label id 와 일치하지 않는 결과 행이 집계에 섞여 있었다 | **집계에서 제외**하고 `counts.unmatched_result_rows` 로 건수를 보고한다. 실제 호출이었을 수 있으므로 추정 비용도 따로 보고한다 |
| 4 | `attempts_total` 과 실패율의 분모가 서로 다른 행 집합을 쓰고 있었다 | **모두 같은 행 집합**(label id 일치)으로 통일 — `counts.row_set_note` 에 명시 |
| 5 | 구버전 행 호환이 사라질 뻔했다 | 명시 필드가 **전혀 없는** 행에는 기존 fallback 을 그대로 적용한다 |


**PHASE 9-9B.11 — 결과 상태 필드의 JSON 타입 검증**

**provider 호출 없음.** fake data 만으로 검증했다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | 상태값을 Python truthiness 로 판정했다 — `"false"`(비어 있지 않은 문자열)는 참이라 **성공으로 오인**될 수 있었다 | `provider_succeeded` · `adapter_succeeded` 는 **JSON boolean 만** 허용한다. 문자열·정수·null 은 `ResultContractError` 로 **거부**한다 |
| 2 | `error_stage` 값 검증이 조합 검증에 섞여 있었다 | **JSON null 또는 정확히 `"provider"`/`"adapter"`** 문자열만 허용하는 **타입 검사를 분리**했다 |
| 3 | `error` 의 타입 검증이 없었다 | **string 또는 null** 이어야 한다(구버전 행에도 적용) |
| 4 | 타입 위반이 집계 도중 드러날 수 있었다 | **입력 계약 위반은 집계 전에 즉시 중단**한다 — 조용히 잘못된 집계가 나지 않게 |

**유지한 것** — 상태 조합 검증(9-9B.10)과 **구버전 행 fallback** 은 그대로다.


**PHASE 9-9C.1 — Google Cloud Vision free-tier OCR 데모 평가기 (별도 도구)**

**AI¹ `/ocr` 에 연결하지 않는다.** OcrResult 로 추측 변환하지 않고 **raw text OCR
까지만** 한다. **실제 Google Vision API 호출·프로젝트 생성·billing 활성화·카드
등록을 하지 않았다.**

| 항목 | 내용 |
|---|---|
| 목적 | 대학 과제 시연용 — Vision 의 `DOCUMENT_TEXT_DETECTION` OCR 품질을 **OCR 층에서만** 평가 |
| **의미 추정 금지** | `problem` · `solution_steps` · printed/handwritten · LaTeX 를 **추정하지 않는다.** Vision 은 의미상 문제/풀이 분리를 보장하지 않는다 |
| **OCR 기능 1개만** | `TEXT_DETECTION` 을 같이 부르면 같은 이미지의 **무료 unit 을 두 번** 소비하므로 `build_body` 가 다른 기능을 **거부**한다 |
| **무료 범위** | OCR **기능별** 첫 **1,000 units/month/billing account** 가 무료. **billing account 가 필요하다**(카드 등록 선행). 같은 billing account 아래 **모든 프로젝트가 quota 를 공유**한다 |
| **누적 상한 40회** | 실행마다가 아니라 **누적**. `eval/_local/google_vision/call_log.jsonl` 로 세고 넘으면 **호출 전에** 거부한다 |
| **과금 guard 한계** | **예산 알림만으로 과금을 막을 수 있다고 가정하지 않는다.** 40회 상한은 **우리 쪽 가드**지 벤더 측 보호가 아니다. 실제 잔량·결제 설정 확인 후 **별도 지시**가 있을 때만 호출한다 |
| **credential** | **ADC** 또는 `GOOGLE_VISION_ACCESS_TOKEN`. **새 dependency 없음**(표준 라이브러리 전용). 토큰은 파일·로그·보고서에 **남지 않는다** |
| 실행 모드 | 기본 **dry-run**(호출 0회·파일 0 writes). 실제 호출은 `--run` **과** `--max-calls` 를 **둘 다** 명시 |
| 한국어 힌트 | `--language-hint ko` 는 **선택 설정**. 힌트 사용/미사용 차이를 비교할 수 있도록 요청 body 와 결과 구조에 둘 다 남긴다 |
| 산출물 | 전부 `eval/_local/google_vision/` 아래 (gitignore 확인) |


**PHASE 9-9C.2 — Vision 응답 계약 · URL 비노출 · 무료 한도 가드 보완**

**Google Vision API 호출 없이** fake 응답으로만 검증했다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | 응답 파싱이 **구버전 모양**(`block.bboxes` · `word.text`)에 맞았다 | **공식 Vision 구조**로 맞춤 — block 은 `boundingBox` 하나, word 는 **`symbols[].text`** 로 조립. 구버전은 fallback 으로만 |
| 2 | presigned URL 이 결과 구조·요약(stdout)·오류·실행 로그에 남았다 | **URI 자체를 기록하지 않는다** — `sha256:` 참조(안정·비가역)만 남긴다. 전체 URI·서명 쿼리·**개별 쿼리 파라미터**까지 치환한다 |
| 3 | 한 run 의 여러 이미지가 **같은 raw 파일명**으로 덮어썼다 | 파일명을 **`<run_id>__<image_id>.json`** 으로 — 서로 덮어쓰지 않는다 |
| 4 | 누적 상한을 **실행 시작 시**에만 세어, 동시 실행이 한도를 넘을 수 있었다 | **API 요청 전에** 슬롯을 원장에 **예약 + flush + fsync** 하고 **잠금**으로 동시 실행을 직렬화한다 |
| 5 | timeout·프로세스 중단처럼 **과금 여부를 모르는 시도**가 상한에서 빠질 수 있었다 | 슬롯을 **요청 전에** 남기므로 그런 시도도 **상한에 포함**된다 |

> **상한의 한계** — 40회 상한은 **AI¹ 데모 도구 내부의 제한**이며 **billing account
> 전체 사용량을 통제하지 않는다.** 같은 billing account 를 쓰는 다른 프로젝트의
> 사용량은 이 상한과 무관하다. 과금 방어는 **free unit 잔량 확인 + 결제 설정 확인**
> 에서만 이뤄진다.


**PHASE 9-9C.3 — Vision word confidence · URL 참조 정합성**

**Google Vision API 호출 없이** fake 응답으로만 검증했다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | fixture 가 **공식 `Word` 구조**가 아니었다 | 공식 필드 **`property` · `boundingBox` · `symbols` · `confidence`** 를 반영하고, 공식 구조가 아닌 **`mid` 는 fixture 에서 제거**했다 |
| 2 | adapter 가 word confidence 를 처리하지 않았다 | **있으면 보존**하고 **실제로 빠졌을 때만** `None` 을 준다 (0 으로 만들지 않는다). 파생 line confidence 는 word 값에서 계산한다 |
| 3 | `word_index` 가 단락 단위라 **문서 안에서 값이 겹쳤다** | **문서 전체에서 유일**하게 바꿨다 — 줄의 `word_indexes` 가 서로 겹치지 않는다 |
| 4 | 9-9C.2 의 "공식 응답에 word confidence 없음" 주장이 틀렸다 | **정정** — `Word` 는 `confidence` 를 공식 필드로 갖는다. README·결정 문서 모두 고쳤다 |
| 5 | `source_image_ref` 가 **URI 전체의 해시**인데 안정 ID 처럼 쓰일 수 있었다 | **이미지의 안정 ID 가 아니며 비교·대응 키로 쓰지 않는다**를 문서와 결과 구조에 명시. 안정 키는 manifest 의 `id` |
| 6 | Vision 응답이 **`context.uri` 로 presigned URL** 을 반사할 수 있었다 | **저장 전에 그 URI 를 redaction** 한다 — 원본 응답 파일·결과 구조 모두에서 서명값이 남지 않는다 |


**계속 유지하는 것** — **opt-out 유효 여부를 OCR 응답에서 확인한다고 주장하는 기능은 추가하지
않았다.** account-level 고정 여부는 **콘솔 또는 Mathpix 지원팀 확인 사항**이다. 요청 설정으로도
응답으로도 그 고정 여부를 확인할 수 없다는 9-9B.5 결론이 그대로 유지된다.






**PHASE 9-9B.2 — 실측 전 하네스 계약 보완 (M5 · 지표 · 비용 · 라벨 키)**

synthetic/fake payload 로만 검증했다. 실제 Mathpix 호출·학생 이미지·API key 는 쓰지 않았다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | `is_printed`/`is_handwritten` 이 **둘 다 false** 인 콘텐츠 줄을 **unreadable 여부와 무관하게** `undecided_kind` 에서 제외하고 있었다 | **unreadable 이어도** `undecided_kind` 에 포함한다. 미인식이라는 사실이 종류를 정해 주지 않는다 |
| 2 | 종류가 미정인 줄이 `derive_steps` 를 통과해 handwritten step 이 될 수 있었다 | **어느 버킷에도 넣지 않는다.** 미정 줄이 handwritten step 으로도 printed region 으로도 나가지 않는다 |
| 3 | unreadable 인 줄을 종류와 무관하게 필기 단계로 몰아넣었다 | 미인식 줄도 **자신의 종류 버킷**에 들어간다 — 인쇄 줄을 필기 단계로 만들지 않는다 |
| 4 | `OcrRegion(kind=region["kind"] or "printed")` 로 종류를 **추측**하고 있었다 | 추측 제거. 종류 미정 region 이 나가면 `UndecidedContractError` 로 방어적으로 차단 |
| 5 | 인쇄/필기 정확률을 `problem_kind` ↔ 라벨 `printed_handwritten` 로 비교했다 (문제 영역 1건 기준) | **원본 줄( region's kind) 단위**로 채점한다. `problem_kind` 에서 **추론하지 않는다** |
| 6 | 대응할 줄이 없는데도 0점/1점으로 점수를 만들었다 | **점수를 만들지 않는다.** `printed_handwritten_accuracy=null` + unavailable 사유 4종(`label_line_kinds_missing` · `label_keys_not_source_line_index` · `no_overlapping_source_line_index` · `all_matched_lines_undecided`) 보고 |
| 7 | 라벨 `region_bboxes` 의 키를 **provider 응답 id** 로 쓰고 있었다 | **원본 줄 인덱스**로 바꾼다. id 로 키를 쓴 라벨은 매칭되지 않고 경고로 집계 (**M16**) |
| 8 | 라벨 키 규칙을 확인할 방법이 없었다 | 같은 케이스의 **반복 측정 결과**를 비교해 `D_label_key_stability.source_line_index_stable` 로 보고 (`true`/`false`/**`null`=미확인**) |
| 9 | 비용의 "12줄 초과" 경계가 `>= 12` 로 구현돼 있었다 (12줄에도 페이지 단가) | **`> 12`** 로 수정 — 11줄·12줄은 이미지 단가, **13줄부터** 페이지 단가. 11/12/13 테스트 추가 |
| 10 | 줄 수에 도형·차트까지 세어 과금 경계가 이르게 잡힐 수 있었다 | **과금 대상 텍스트 행**(`billable_text_row_count`) 기준. 도형 등은 세지 않는다 (**M15**) |
| 11 | 반복 측정 시 마지막 결과가 조용히 이겨서 앞선 결과가 사라졌다 | 반복 측정 결과를 모두 보관하고 **안정성 보고**에 활용. `counts.measurements_total` 추가 |


**PHASE 9-9B.1 — 하네스 계약·측정 정확성 보완에서 고친 것 (실측 아님)**

synthetic/document fixture 로만 검증했다. 실제 Mathpix 호출·학생 이미지·API key 는 쓰지 않았다.

| # | 문제 | 수정 |
|---|---|---|
| 1 | `line_data[].cnt` 폴리곤에서 bbox 를 못 만들었다 | **회전된 사각형**일 수 있으므로 min/max 로 축 정렬 bbox 계산 (`polygon_to_bbox`) |
| 2 | line_data 와 lines_json 의 대응이 없었다 | 줄의 `region` → `cnt` → `lines_json` 같은 `id` 순으로 bbox 해석 (`_bbox_of`) |
| 3 | `image_max_size` 를 조용히 버렸다 | **콘텐츠가 있을 수 있는 줄**이므로 미인식 단계로 **보존** |
| 4 | `image_not_supported` 를 타입과 무관하게 버렸다 | **타입이 text/math 계열이면 유실 금지** — 비풀이 콘텐츠(도형 등)만 생략하되 `excluded_lines` 에 기록 |
| 5 | `is_printed`/`is_handwritten` 둘 다 false 인 줄을 임의로 분류했다 | 종류를 정하지 않고 `undecided_kind` 로 보존 → 변환 **보류** (M5) |
| 6 | MMD → text 변환에서 수식 내용이 사라졌다 | `text` 에도 **내용을 남기고** 구분자만 벗긴다 |
| 7 | `OcrResult` 에 없는 키가 region's 로 들어갔다 | 계약 필드만 매핑. **풀이 콘텐츠가 아닌 영역은 `OcrResult.regions` 에 넣지 않는다** |
| 8 | 제목을 벗겨 같은 줄이 두 번 집계됐다 | 줄 분류를 **단일 패스**로 (`classify_lines`) — 제외/미정 중복 제거 |
| 9 | exact-match 분모와 동치 판정 가능 분모가 섞여 있었다 | **분모를 분리**해 각각 건수를 함께 보고 |
| 10 | 단계 경계 F1 이 원본 줄 범위 없이 계산됐다 | 원본 줄 인덱스(`step_source_lines`)가 있을 때만 계산, 아니면 `null` + 사유 |
| 11 | bbox IoU 가 라벨 안 region 까지 포함했다 | **대응되는 region(id 일치) 끼리만** 계산 + 매칭 건수 보고 |
| 12 | 인쇄/필기 정확률이 라벨과 연결되지 않았다 | 예측 `problem_kind` ↔ 라벨 `printed_handwritten` 비교 + 비교 건수 보고 |
| 13 | 고정 단가를 "actual cost" 로 부르고 12줄 규칙을 무시했다 | **추정치**(`cost_estimate_usd_total`)로 명명하고 12줄 초과는 페이지 단가 적용 + 해당 케이스 건수 보고 |
| 14 | 라벨 예시가 스키마와 어긋났다 | 판정 불가 단계는 **빈 문자열**(null 아님)로 통일 + 스키마 정합 테스트 추가 |

**새로 드러난 스키마 공백** — `OcrRegion` 에 `id` 필드가 없다. `OcrStep.region_id` 는 있는데
region 자체에 식별자가 없어 step→region 연결이 region 쪽에서 닫히지 않는다. → **M14**.


**하네스 자체 검증에서 드러난 것 (실측 아님, 문서 예시 기준)**

프로토타입을 Mathpix 공식 문서에 게시된 예시 응답으로 돌려보고 2가지를 잡았다.

1. **"벤더가 내지 않은 줄" 과 "confidence 누락" 을 구분해야 한다.**
   `conversion_output:false` + `error_id` 인 줄은 애초에 결과에 없는 영역이다.
   이를 confidence 누락으로 세면 **없는 문제가 생긴다**(M12 결정 전에 반드시 분리).
2. **`error_id` 는 값마다 의미가 다르다.**
   `image_not_supported`/`image_max_size`/`image_no_content` 는 **텍스트가 아닌 영역**
   (도형 등) 이므로 풀이 단계가 아니다. 반면 `math_confidence` 는 "수식인데 확신이 없어
   못 읽었다" 이므로 **미인식 단계로 남겨야 한다** — 여기가 빠지면 `???` 표식이 사라진다.
   → **M6 은 `error_id` 값별 규칙으로 결정해야 한다.** (프로토타입은 후보 규칙만 쓴다)


## 8. 다음 작업 (구현 Phase 에서)

> 이 목록은 Mathpix 후보 평가/변환 작업의 원래 계획이다. 이후 사용자가 Google Vision
> free-tier 기반 raw-text preview를 선택해 앱 provider 연동(대조표 §14.20)은 별도 경로로
> 구현했다. 실제 OCR API 호출·billing 설정·한국어/수식 정확도 측정은 아직 하지 않았다.

| 순서 | 작업 | 선행 |
|---|---|---|
| 1 | Mathpix 계약 검증용 **샌드박스 호출 1회** (키는 운영 저장소 밖) | 벤더 계정·키 발급 |
| 2 | E01~E16 라벨 세트 제작 (최소 20장) | §4.1 |
| 3 | **provider 프로토타입** (MMD 파싱 + 줄→단계 파생) | 1 |
| 4 | §4.2 지표 측정 스크립트 (`eval/`, 회귀 스위트 밖) | 2, 3 |
| 5 | 측정 결과로 **Q21·Q22·Q24 잠정 결정 확정** 또는 하이브리드 전환 | 4 |
| 6 | `OcrProvider` 구현체 등록 — `register_ocr_provider()` 로 주입 | 5 |
| 7 | 429·422 오류 코드 추가 + `/ocr` 계약 확장 | 5 |

**이번 Phase 에 한 것**: 문서·조사만. **코드·의존성·API key/secret 은 변경하지 않았다.**
벤더 계약 체결, `/ocr` 응답 필드 확장.

**provider 확정 게이트** — Mathpix 를 **최종 provider 로 확정하지 않는다.**
Mathpix key 와 예산 승인이 준비된 뒤 E01~E16 평가 이미지를 준비하고, 실제 샌드박스 응답으로
**MMD / line_data / lines_json 변환, 누락 confidence, 한글 인쇄 인식률, timeout·비용** 을 측정한다.
측정 결과 전까지 권장은 **잠정** 이다.

---

## 9. 참고 출처

- Mathpix OCR API 문서 — `docs.mathpix.com/reference/post-v3-text`,
  `docs.mathpix.com/reference/image-results`, `docs.mathpix.com/guides/image-ocr`
- Mathpix 가격 — `mathpix.com` 요금 안내 (2026-05 확인본), 2026-10-22 재확인 기준
- Gemini 문서·가격 — `ai.google.dev/gemini-api/docs/document-processing`,
  `ai.google.dev/gemini-api/docs/pricing`, `docs.cloud.google.com/.../gemini/3-flash`
- Azure Document Intelligence — 언어 지원(`language-support/ocr`),
  add-on capability(`concept/add-on-capabilities`), `prebuilt-read`
- 수학 PDF OCR 벤치마크(의미 오류 사례) — `igorrivin.github.io/blog/ocr-benchmark/`
- 범용 OCR 도구 비교 — `dev.to/voqusa/ocr-for-handwriting-and-math-comparing-tools-in-2026-b6m`
- Gemini bbox 품질 — `robkopel.me/field-notes/gemini-3-bounding-boxes/`

> 공개 벤치마크 수치는 **참고용**이며 우리 도메인(한국어 문제집 사진 + 필기 풀이)의
> 성능을 대표하지 않는다. §4 측정이 그 대체다.


## PHASE 9-9C.4 — Google Cloud Vision 앱 provider 통합

사용자가 free-tier raw-text preview 경로를 선택해 앱 `/ocr`에 provider를 연결했다.
기존 Mathpix/Gemini/Azure 비교·eval은 유지하며, runtime은 Google Vision `DOCUMENT_TEXT_DETECTION`만 선택적으로 쓴다.

| 항목 | 구현/정책 |
|---|---|
| 활성화 | `AI1_OCR_PROVIDER=google_vision`일 때만. 기본 provider 없음(fail closed) |
| 인증 | SDK Application Default Credentials; `GOOGLE_APPLICATION_CREDENTIALS`가 서비스 계정 JSON 경로를 가리킨다. JSON은 프로젝트 밖에 둔다 |
| Dependency | `google-cloud-vision>=3.10,<4`를 `requirements.txt`에 선언. Mathpix eval wrapper는 표준 라이브러리 전용이며 Pix2Text runtime dependency는 추가하지 않았다 |
| 응답 | raw-text preview만. `raw_text` 및 geometry에서 파생한 `raw_lines`; 문제/풀이 분리·printed/handwritten·LaTeX는 추측하지 않는다 |
| URL 비노출 | request signed URL은 Vision 호출에만 사용. **응답에 이미지 참조 필드는 없다**(PHASE 9-9C.7 — URL/서명/fingerprint 전부 비노출). URL/쿼리는 응답·오류에서 redaction한다 |
| Timeout/retry | provider timeout 기본 8초(`GOOGLE_VISION_TIMEOUT_SECONDS`로 변경), **설정 상한 25초**(초과 → 503 `E_OCR_NOT_CONFIGURED`); SDK retry 비활성화; 요청당 provider 호출 1회(자동 재시도 없음). Backend 는 OCR 전체에 30초 대기·재시도 없음(협의서 §3.2/§4.6.1) |
| URL 입력 | HTTPS 서명 URL만(`gs://` 거부). 호스트 allowlist는 `AI1_OCR_ALLOWED_HOSTS`(미설정 → HTTPS만 검사). 위반은 budget 예약 이전에 422 `E_OCR_UNSUPPORTED_IMAGE` (PHASE 9-9C.8) |
| 호출 가드 | eval probe와 `eval/_local/google_vision/call_budget.jsonl` 및 lock 공유, 누적 40회. 이는 billing account 전체 quota를 보장하지 않는다 |
| 실패 상태 | API disabled, credential/IAM, billing은 503 코드; quota/local cap은 **429** `E_OCR_USAGE_LIMIT_REACHED`; 이미지 다운로드 실패·지원하지 않는 이미지는 **422** `E_OCR_IMAGE_FETCH_FAILED` / `E_OCR_UNSUPPORTED_IMAGE`; 기타 provider 실패는 502 (협의서 §4.6.3 채택) |
| 검증 | 현재 테스트는 fake provider/response와 공식 SDK message/signature만 사용한다. 초기 compatibility 테스트 중 fake image URL과 AnonymousCredentials를 사용한 요청이 실수로 endpoint에 두 차례 도달해 403으로 거절됐다. service-account credential은 쓰지 않았고 OCR 성공은 없었다. 한글/수식 OCR 정확도는 아직 미측정 |

**실제 요청 게이트** — `GOOGLE_APPLICATION_CREDENTIALS`와 provider 선택을 설정하고 실제 `/ocr` 호출하기 전, billing account의 free units 잔량과 API 활성화를 확인한 뒤 별도 사용자 승인을 받는다. 로컬 40회 상한은 다른 프로젝트/도구에서 쓴 billing quota를 통제하지 않는다.


## PHASE 9-9C.4 — Google Cloud Vision 앱 provider 통합

사용자가 free-tier raw-text preview 경로를 선택해 앱 `/ocr`에 provider를 연결했다.
기존 Mathpix/Gemini/Azure 비교·eval은 유지하며, runtime은 Google Vision `DOCUMENT_TEXT_DETECTION`만 선택적으로 쓴다.

| 항목 | 구현/정책 |
|---|---|
| 활성화 | `AI1_OCR_PROVIDER=google_vision`일 때만. 기본 provider 없음(fail closed) |
| 인증 | SDK Application Default Credentials; `GOOGLE_APPLICATION_CREDENTIALS`가 서비스 계정 JSON 경로를 가리킨다. JSON은 프로젝트 밖에 둔다 |
| Dependency | `google-cloud-vision>=3.10,<4`를 `requirements.txt`에 선언. Mathpix eval wrapper는 표준 라이브러리 전용이며 Pix2Text runtime dependency는 추가하지 않았다 |
| 응답 | raw-text preview만. `raw_text` 및 geometry에서 파생한 `raw_lines`; 문제/풀이 분리·printed/handwritten·LaTeX는 추측하지 않는다 |
| URL 비노출 | request signed URL은 Vision 호출에만 사용. **응답에 이미지 참조 필드는 없다**(PHASE 9-9C.7 — URL/서명/fingerprint 전부 비노출). URL/쿼리는 응답·오류에서 redaction한다 |
| Timeout/retry | provider timeout 기본 8초(`GOOGLE_VISION_TIMEOUT_SECONDS`로 변경), **설정 상한 25초**(초과 → 503 `E_OCR_NOT_CONFIGURED`); SDK retry 비활성화; 요청당 provider 호출 1회(자동 재시도 없음). Backend 는 OCR 전체에 30초 대기·재시도 없음(협의서 §3.2/§4.6.1) |
| URL 입력 | HTTPS 서명 URL만(`gs://` 거부). 호스트 allowlist는 `AI1_OCR_ALLOWED_HOSTS`(미설정 → HTTPS만 검사). 위반은 budget 예약 이전에 422 `E_OCR_UNSUPPORTED_IMAGE` (PHASE 9-9C.8) |
| 호출 가드 | eval probe와 `eval/_local/google_vision/call_budget.jsonl` 및 lock 공유, 누적 40회. 이는 billing account 전체 quota를 보장하지 않는다 |
| 실패 상태 | API disabled, credential/IAM, billing은 503 코드; quota/local cap은 **429** `E_OCR_USAGE_LIMIT_REACHED`; 이미지 다운로드 실패·지원하지 않는 이미지는 **422** `E_OCR_IMAGE_FETCH_FAILED` / `E_OCR_UNSUPPORTED_IMAGE`; 기타 provider 실패는 502 (협의서 §4.6.3 채택) |
| 검증 | fake 응답/provider 테스트 및 API route 테스트만. Google API·GCP 프로젝트·billing/card 설정은 사용하지 않았고, 한글/수식 OCR 정확도는 아직 미측정 |

**실제 요청 게이트** — `GOOGLE_APPLICATION_CREDENTIALS`와 provider 선택을 설정하고 실제 `/ocr` 호출하기 전, billing account의 free units 잔량과 API 활성화를 확인한 뒤 별도 사용자 승인을 받는다. 로컬 40회 상한은 다른 프로젝트/도구에서 쓴 billing quota를 통제하지 않는다.

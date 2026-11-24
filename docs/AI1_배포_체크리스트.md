# AI① 배포 체크리스트

> 대상: `C:\Users\choi\Desktop\learning-intelligence` (AI① Learning Intelligence)
> 기준: 2026-11-23 · `main` · pytest 1940 passed · pyflakes 0
> 원칙: **값이 아니라 이름만** 확인한다. 환경변수 값·API 키·토큰·서비스 계정 JSON
> 내용은 화면·로그·문서에 출력하지 않는다.

## 1. 배포 전 (로컬에서)

- [ ] `.venv\Scripts\python -m pytest -q` → 실패 0
- [ ] `.venv\Scripts\pyflakes app tests eval` → 0건
- [ ] `.venv\Scripts\python scripts\export_contract.py --out-dir <임시 폴더>` → exit 0
      (기대 상태와 다르면 exit 1 — 배포 중단)
- [ ] 예시 4종의 `analysis_status`/`correct`/`final_answer_present`/`review_reasons`가
      `AI1_판정한계_목록.md`와 일치하는지 확인
- [ ] `git status`로 의도하지 않은 파일(키·원장·생성물)이 없는지 확인

## 2. 환경변수 (이름만 확인 — 값은 출력 금지)

| 이름 | 용도 | 확인 |
|---|---|---|
| `AI1_INTERNAL_API_KEYS` | 내부 인증 키(쉼표 다중, 교체 기간 구·신 병기) | 설정됨 |
| `AI1_OCR_PROVIDER` | OCR provider 선택 (`google_vision` opt-in) | 설정 여부 |
| `GOOGLE_APPLICATION_CREDENTIALS` | 서비스 계정 경로 (내용 미열람) | 경로 존재 |
| `GOOGLE_VISION_TIMEOUT_SECONDS` | Vision timeout (기본 8, 상한 25) | 0 < 값 ≤ 25 |
| `AI1_OCR_ALLOWED_HOSTS` | 이미지 링크 허용 호스트 | 운영 호스트 반영 |
| `AI1_ENABLE_DOCS` | `/docs`·`/openapi.json` 노출 (로컬 전용) | **운영 미설정** |
| `AI1_OCR_BILLING_UNIT` | billing 단위 확인값 | 운영자 확인 |
| `AI1_OCR_MONTHLY_FREE_QUOTA` | 월 무료 quota 확인값 | 운영자 확인 |
| `AI1_OCR_MONTHLY_CALL_CAP` | 월 내부 호출 상한 | 운영자 확인 |
| `AI1_OCR_FREE_QUOTA_CONFIRMED` | 무료 quota 확인 완료 플래그 | 확인 후 설정 |

> 미설정 상태가 안전한 기본값이다 — 특히 OCR 은 미설정 시 503 fail-closed.
> 값이 비어 있어도 정상 동작하는 항목이 있으므로, **미설정이 오류인지 아닌지를
> 각 항목 설명으로 판단**한다.

## 3. 배포

- [ ] push (사용자 승인 후)
- [ ] 서버 재시작 (사용자 지시 후)
- [ ] `GET /health` → **200** `{"status":"ok"}` (무키)
- [ ] 보호 라우트가 키 없이 **503** `E_AUTH_NOT_CONFIGURED` (키 미설정 시 의도된 fail-closed)
      또는 **401** `E_AUTH_INVALID_KEY` (키는 설정됐고 헤더 없음)
- [ ] `/docs`·`/redoc`·`/openapi.json` → **404** (운영에서 기본 비노출 확인)

## 4. 스모크 테스트 (배포 직후 — 스크립트 1회)

```powershell
$env:AI1_SMOKE_KEY = "<배포된 서버의 내부 키>"
.venv\Scripts\python scripts\smoke_deploy.py --base-url https://<host>
```

- 키는 **환경변수에서만** 읽는다(CLI 인자 금지). 없으면 검사 전에 exit 2.
- 출력은 검사별 PASS/FAIL + 상태 코드/필드명만 — scheme·경로·쿼리·키는 출력하지 않는다.
- 실패가 하나라도 있으면 exit 1 이며, 배포를 중단하고 롤백 검토로 간다.
- `SMOKE-<UTC run>` 식별자를 쓰므로 실제 학생 데이터와 겹치지 않는다.

| # | 검사 | 기대 |
|---|---|---|
| 1 | health (무키) | 200, `status` 존재 |
| 2 | history (무키) | 401 `E_AUTH_INVALID_KEY` (키 미설정 서버는 503 `E_AUTH_NOT_CONFIGURED` 도 허용·표시) |
| 3 | analyze (키) | 200, ANALYZED, correct=true, fap=true, review_reasons=[], 필수 필드 존재 |
| 4 | skills (키) | 200, count≥1, `linear_equation` 포함, engine=rule |
| 5 | history (키) | 200, total≥1, items 비어 있지 않음 |
| 6 | skills (무키) | 401 |
| 7 | docs (무키) | 404 (운영 기본 비노출) |

## 5. OCR (2단계 — 승인된 경우에만)

- [ ] **환경변수 미설정 시** `POST /api/v1/attempts/{attempt_id}/ocr` →
      **503 `E_OCR_NOT_CONFIGURED` 가 정상** (결과를 지어내지 않는 fail-closed)
- [ ] 실제 Vision 호출은 billing 잔량 확인 + 팀장 승인(최대 10회) 후에만
- [ ] 이미지 링크는 HTTPS 서명 URL만 (`gs://` 422 `E_OCR_UNSUPPORTED_IMAGE`)

## 6. 보안 확인

- [ ] 환경변수 값·API 키·토큰을 콘솔/로그/문서에 출력하지 않았다
- [ ] 서비스 계정 JSON을 열람·복사·출력하지 않았다
- [ ] OCR 응답·오류에 `image_reference`(서명 URL)가 없다
- [ ] `/docs`·`/openapi.json` 404 (운영)
- [ ] `.env`·키 파일이 저장소에 없다 (`git status` clean)

## 7. 롤백

- [ ] 문제 발생 시 **이전 커밋**으로 되돌려 재시작 (사용자 승인 후)
- [ ] 롤백 후 `/health` 200, 401/503 기대값 재확인
- [ ] 실패 원인 기록: 어떤 스모크/필드가 기대와 달랐는지, `review_reasons` 코드,
      응답 상태 코드 (값·키는 기록하지 않는다)
- [ ] 롤백 후 `scripts/export_contract.py` 패키지도 이전 응답과 함께 재생성

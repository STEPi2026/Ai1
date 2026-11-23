# AI① OpenAPI 스냅샷 안내

이 폴더의 `AI1_openapi_2026-11-17.json`은 **과거 스냅샷**이다.

- 기준: 2026-11-17 · 경로 **8개**
- 최신 계약이 **아니다**. 아래 항목이 아직 반영되지 않았다.
  - `final_answer_present` (2026-11-23 추가)
  - `review_reasons` (2026-11-23 추가)
  - `GET /api/v1/students/{student_id}/skills` (2026-11-23 추가)

## 최신 OpenAPI·예시는 스크립트로 생성한다

```powershell
.venv\Scripts\python scripts\export_contract.py --out-dir docs/AI2_전달_2026-11-23
```

이 명령이 생성하는 `openapi.json`이 **최신 계약**이며, 같은 폴더에 대표 예시
4종·판정 한계 사본·전달 README가 함께 만들어진다.

## 규칙

- **수동 편집 금지** — 스냅샷·생성물 모두 손으로 고치지 않는다.
- 이 과거 스냅샷을 제자리에서 재생성하지 않는다. 포맷 차이(약 4,690줄) 때문에
  diff가 계약 변경처럼 보여 리뷰가 어려워진다.
- 최신 스키마 확인이 필요하면 위 스크립트를 실행해 새 폴더에 생성하고,
  전달 범위는 `docs/AI1_배포_체크리스트.md`를 따른다.
- 생성물(`docs/AI2_전달_*/`)은 `.gitignore`로 제외된다. 필요하면 `git add -f`.

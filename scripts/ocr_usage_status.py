"""Google Vision OCR 호출 예산 상태를 **읽기 전용**으로 출력한다.

사용법::

    .venv\\Scripts\\python scripts\\ocr_usage_status.py
    .venv\\Scripts\\python scripts\\ocr_usage_status.py --ledger eval/_local/google_vision/call_budget.jsonl

출력은 JSON 하나이며, 다음을 담는다.

- 이번 달(UTC) 예약/성공/실패/미확인 건수
- 남은 **내부 안전 한도**(Google Billing 의 실제 무료 잔량이 아니다)
- 기존 누적 40회 상한 사용량과 잔여량
- 호출 차단 여부와 사유
- 원장 경로와 검증 여부

**아무것도 쓰지 않는다.** 원장·잠금 파일을 수정하지 않고, 실제 Google API 도
호출하지 않는다. 비밀값(서비스 계정 키·토큰)도 읽거나 출력하지 않는다.

무료 한도 값은 코드에 없다. 운영자가 확인해 주입한 환경변수가 없으면
``blocked_reason`` 이 ``quota_unverified`` 이고 ``blocked`` 가 ``true`` 다 —
이 상태는 의도된 fail-closed 이며, OCR 요청은 503 으로 거부된다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.integration.google_vision_budget import (  # noqa: E402
    BUDGET_LEDGER_PATH,
    status,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Google Vision OCR 호출 예산 상태 조회 (읽기 전용)"
    )
    parser.add_argument(
        "--ledger",
        default=str(BUDGET_LEDGER_PATH),
        help="사용량 원장 경로 (기본: eval/_local/google_vision/call_budget.jsonl)",
    )
    args = parser.parse_args(argv)

    state = status(ledger_path=Path(args.ledger))
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    # 차단 상태여도 조회 자체는 성공이다 — 종료 코드는 스크립트 오류에만 쓴다.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""공유 pytest fixture (PHASE 9-9C.8).

모든 라우트(`/health` 제외)에 내부 인증 guard(`require_internal_auth`)가 부착됐다.
기존 API 계약 테스트 수백 건들은 비즈니스 행위를 검증 대상으로 하며, 인증
더미 호출자로 만들어졌으므로 아래 autouse fixture 로 guard 를
dependency_override 로 우회한다.

인증 guard 자체는 ``tests/test_internal_auth.py`` 에서 이 override 를
제거하고 실제 동작을 검증한다.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def bypass_internal_auth():
    from app.integration.internal_auth import require_internal_auth
    from app.main import app

    def _skip_auth() -> None:
        return None

    app.dependency_overrides[require_internal_auth] = _skip_auth
    yield
    app.dependency_overrides.pop(require_internal_auth, None)

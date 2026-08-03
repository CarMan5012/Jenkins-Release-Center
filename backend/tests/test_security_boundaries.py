from datetime import datetime, timezone

import jwt
from fastapi.routing import APIRoute

from app.api import history, system
from app.api.deps import get_current_active_admin
from app.core.config import settings
from app.core.security import create_access_token


def route_dependencies(router, path: str):
    route = next(
        item
        for item in router.routes
        if isinstance(item, APIRoute) and item.path == path
    )
    return {dependency.call for dependency in route.dependant.dependencies}


def test_decrypt_field_requires_admin():
    assert get_current_active_admin in route_dependencies(
        system.router, "/decrypt-field"
    )


def test_history_reset_requires_admin():
    assert get_current_active_admin in route_dependencies(
        history.router, "/reset-sequence"
    )


def test_access_token_expiry_uses_utc():
    token = create_access_token("operator")
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
    )
    remaining = (
        datetime.fromtimestamp(payload["exp"], timezone.utc)
        - datetime.now(timezone.utc)
    )
    expected_seconds = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert expected_seconds - 5 <= remaining.total_seconds()
    assert remaining.total_seconds() <= expected_seconds + 5

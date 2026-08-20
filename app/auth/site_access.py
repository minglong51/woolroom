"""Outer site-access gate for private deployments."""

from __future__ import annotations

import hmac

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.responses import Response

from app.config import settings

SITE_ACCESS_COOKIE = "woolroom_site_access"
GUEST_ACCESS_COOKIE = "woolroom_guest_access"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="woolroom-site-access")


def site_access_enabled() -> bool:
    return bool(settings.site_password)


def verify_site_password(password: str) -> bool:
    if not site_access_enabled():
        return True
    return hmac.compare_digest(password, settings.site_password)


def has_site_access(cookie: str | None) -> bool:
    if not site_access_enabled():
        return True
    if not cookie:
        return False
    try:
        data = _serializer().loads(
            cookie,
            max_age=settings.site_access_days * 24 * 60 * 60,
        )
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("ok"))


def set_site_access_cookie(response: Response) -> None:
    token = _serializer().dumps({"ok": 1})
    response.set_cookie(
        key=SITE_ACCESS_COOKIE,
        value=token,
        max_age=settings.site_access_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
    )


def clear_site_access_cookie(response: Response) -> None:
    response.delete_cookie(SITE_ACCESS_COOKIE, path="/")


# ────────── read-only guest access ──────────
# A separate cookie + salt from site access: holding one never implies the
# other. The guest cookie only proves "allowed to watch" — it carries no
# session and unlocks only the sanitized guest scene (REST + WS).


def _guest_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="woolroom-guest-access")


def guest_access_enabled() -> bool:
    return bool(settings.guest_access_enabled)


def has_guest_access(cookie: str | None) -> bool:
    if not guest_access_enabled():
        return False
    if not cookie:
        return False
    try:
        data = _guest_serializer().loads(
            cookie,
            max_age=settings.site_access_days * 24 * 60 * 60,
        )
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("guest"))


def set_guest_access_cookie(response: Response) -> None:
    token = _guest_serializer().dumps({"guest": 1})
    response.set_cookie(
        key=GUEST_ACCESS_COOKIE,
        value=token,
        max_age=settings.site_access_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
    )


def clear_guest_access_cookie(response: Response) -> None:
    response.delete_cookie(GUEST_ACCESS_COOKIE, path="/")

"""Outer site-access gate for private deployments."""

from __future__ import annotations

import hmac

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.responses import Response

from app.config import settings
from woolroom.auth import DEFAULT_AUTH_NAMESPACE, AuthNamespace

SITE_ACCESS_COOKIE = DEFAULT_AUTH_NAMESPACE.site_access_cookie
GUEST_ACCESS_COOKIE = DEFAULT_AUTH_NAMESPACE.guest_access_cookie


def _serializer(
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=namespace.site_access_salt)


def site_access_enabled() -> bool:
    return bool(settings.site_password)


def verify_site_password(password: str) -> bool:
    if not site_access_enabled():
        return True
    return hmac.compare_digest(password, settings.site_password)


def has_site_access(
    cookie: str | None,
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> bool:
    if not site_access_enabled():
        return True
    if not cookie:
        return False
    try:
        data = _serializer(namespace).loads(
            cookie,
            max_age=settings.site_access_days * 24 * 60 * 60,
        )
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("ok"))


def set_site_access_cookie(
    response: Response,
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> None:
    token = _serializer(namespace).dumps({"ok": 1})
    response.set_cookie(
        key=namespace.site_access_cookie,
        value=token,
        max_age=settings.site_access_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
    )


def clear_site_access_cookie(
    response: Response,
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> None:
    response.delete_cookie(namespace.site_access_cookie, path="/")


# ────────── read-only guest access ──────────
# A separate cookie + salt from site access: holding one never implies the
# other. The guest cookie only proves "allowed to watch" — it carries no
# session and unlocks only the sanitized guest scene (REST + WS).


def _guest_serializer(
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=namespace.guest_access_salt)


def guest_access_enabled() -> bool:
    return bool(settings.guest_access_enabled)


def has_guest_access(
    cookie: str | None,
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> bool:
    if not guest_access_enabled():
        return False
    if not cookie:
        return False
    try:
        data = _guest_serializer(namespace).loads(
            cookie,
            max_age=settings.site_access_days * 24 * 60 * 60,
        )
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("guest"))


def set_guest_access_cookie(
    response: Response,
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> None:
    token = _guest_serializer(namespace).dumps({"guest": 1})
    response.set_cookie(
        key=namespace.guest_access_cookie,
        value=token,
        max_age=settings.site_access_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
    )


def clear_guest_access_cookie(
    response: Response,
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> None:
    response.delete_cookie(namespace.guest_access_cookie, path="/")

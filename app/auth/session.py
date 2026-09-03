"""Cookie-based sessions. Signed user_id cookie — no email, no password."""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.storage import repo
from app.storage.models import User
from woolroom.auth import DEFAULT_AUTH_NAMESPACE, AuthNamespace

COOKIE_NAME = DEFAULT_AUTH_NAMESPACE.session_cookie
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


def _signer(namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE) -> URLSafeSerializer:
    return URLSafeSerializer(settings.secret_key, salt=namespace.session_salt)


def sign(user_id: str, namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE) -> str:
    return _signer(namespace).dumps({"u": user_id})


def unsign(
    token: str,
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> str | None:
    try:
        data = _signer(namespace).loads(token)
        return data.get("u")
    except BadSignature:
        return None


async def load_user(
    session: AsyncSession,
    cookie: str | None,
    namespace: AuthNamespace = DEFAULT_AUTH_NAMESPACE,
) -> User | None:
    if not cookie:
        return None
    uid = unsign(cookie, namespace)
    if not uid:
        return None
    return await repo.get_user(session, uid)

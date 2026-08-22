"""Cookie-based sessions. Signed user_id cookie — no email, no password."""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.storage import repo
from app.storage.models import User

COOKIE_NAME = "woolroom_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(settings.secret_key, salt="woolroom-session")


def sign(user_id: str) -> str:
    return _signer().dumps({"u": user_id})


def unsign(token: str) -> str | None:
    try:
        data = _signer().loads(token)
        return data.get("u")
    except BadSignature:
        return None


async def load_user(session: AsyncSession, cookie: str | None) -> User | None:
    if not cookie:
        return None
    uid = unsign(cookie)
    if not uid:
        return None
    return await repo.get_user(session, uid)

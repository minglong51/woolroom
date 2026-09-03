from __future__ import annotations

import re
from dataclasses import dataclass, fields

_COOKIE_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_SALT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


@dataclass(frozen=True, slots=True)
class AuthNamespace:
    session_cookie: str
    session_salt: str
    site_access_cookie: str
    site_access_salt: str
    guest_access_cookie: str
    guest_access_salt: str
    pending_invite_cookie: str

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            pattern = _SALT if field.name.endswith("_salt") else _COOKIE_NAME
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 128
                or not pattern.fullmatch(value)
            ):
                raise ValueError(f"{field.name} must be a nonempty safe ASCII value")
        cookie_names = (
            self.session_cookie,
            self.site_access_cookie,
            self.guest_access_cookie,
            self.pending_invite_cookie,
        )
        if len(set(cookie_names)) != len(cookie_names):
            raise ValueError("cookie names must be pairwise distinct")
        salts = (self.session_salt, self.site_access_salt, self.guest_access_salt)
        if len(set(salts)) != len(salts):
            raise ValueError("signing salts must be pairwise distinct")


DEFAULT_AUTH_NAMESPACE = AuthNamespace(
    session_cookie="woolroom_session",
    session_salt="woolroom-session",
    site_access_cookie="woolroom_site_access",
    site_access_salt="woolroom-site-access",
    guest_access_cookie="woolroom_guest_access",
    guest_access_salt="woolroom-guest-access",
    pending_invite_cookie="woolroom_pending_invite",
)


__all__ = ["DEFAULT_AUTH_NAMESPACE", "AuthNamespace"]

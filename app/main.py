"""FastAPI entry. Lifespan = create tables + start scheduler."""

from __future__ import annotations

import logging
import os
import re
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text as _text

from app.api.http import router as http_router
from app.api.ws import router as ws_router
from app.auth.site_access import (
    GUEST_ACCESS_COOKIE,
    SITE_ACCESS_COOKIE,
    clear_site_access_cookie,
    guest_access_enabled,
    has_guest_access,
    has_site_access,
    set_guest_access_cookie,
    set_site_access_cookie,
    site_access_enabled,
    verify_site_password,
)
from app.config import settings
from app.data.voice import CLIENT_VOICE, INDEX_VOICE
from app.packs import client_pack_assets, load_packs
from app.scheduler.jobs import start_scheduler
from app.storage.db import engine
from app.storage.models import Base

STATIC_DIR = Path(__file__).parent / "static"


def _compute_app_version() -> str:
    """Stable per-deploy version string. Prefer GIT_SHA (baked in at image
    build via --build-arg GIT_SHA=...); fall back to the newest static-file
    mtime. Clients compare this against the version they booted with —
    mismatch surfaces a soft "refresh" pill."""
    git_sha = os.environ.get("GIT_SHA")
    if git_sha and git_sha != "dev":
        return git_sha
    try:
        return str(
            max(
                path.stat().st_mtime_ns
                for path in STATIC_DIR.rglob("*")
                if path.is_file()
            )
        )
    except OSError:
        return "dev"


APP_VERSION = _compute_app_version()
SITE_ACCESS_ALLOWLIST = {"/healthz", "/access", "/api/site-access", "/api/guest-access"}
GUEST_HTTP_ALLOWLIST = {"/", "/api/me", "/api/guest/scene", "/api/voice", "/api/packs"}


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


DEFAULT_DEV_SECRET = "dev-change-me"


def _ensure_coat_column(sync_conn) -> None:
    # Dev/test databases predate alembic management, and create_all never
    # alters existing tables — so an older real woolroom.db would miss
    # pets.coat and every pet query would fail on boot. Add it here,
    # tolerantly. Prod is alembic-only and never runs this path.
    cols = {row[1] for row in sync_conn.execute(_text("PRAGMA table_info(pets)"))}
    if cols and "coat" not in cols:
        sync_conn.execute(
            _text("ALTER TABLE pets ADD COLUMN coat VARCHAR(16) NOT NULL DEFAULT 'red'")
        )


def _ensure_demo_column(sync_conn) -> None:
    cols = {row[1] for row in sync_conn.execute(_text("PRAGMA table_info(pets)"))}
    if cols and "is_demo" not in cols:
        sync_conn.execute(
            _text("ALTER TABLE pets ADD COLUMN is_demo BOOLEAN NOT NULL DEFAULT 0")
        )


def _ensure_household_columns(sync_conn) -> None:
    """Two-pet households on dev/test DBs (prod: the alembic initial schema).

    - pets.species / pets.household_id / users.last_room_pet_id added tolerantly;
      founding pets head their own household (household_id = id).
    - the legacy "one human, one pet" unique on pet_participants.user_id can't
      be ALTERed away in SQLite — rebuild the table without it when found.
    """
    cols = {row[1] for row in sync_conn.execute(_text("PRAGMA table_info(pets)"))}
    if not cols:
        return
    if "species" not in cols:
        sync_conn.execute(
            _text("ALTER TABLE pets ADD COLUMN species VARCHAR(16) NOT NULL DEFAULT 'dog'")
        )
    if "household_id" not in cols:
        sync_conn.execute(
            _text("ALTER TABLE pets ADD COLUMN household_id VARCHAR(32)")
        )
    sync_conn.execute(
        _text("UPDATE pets SET household_id = id WHERE household_id IS NULL")
    )
    user_cols = {row[1] for row in sync_conn.execute(_text("PRAGMA table_info(users)"))}
    if user_cols and "last_room_pet_id" not in user_cols:
        sync_conn.execute(
            _text("ALTER TABLE users ADD COLUMN last_room_pet_id VARCHAR(32)")
        )
    row = sync_conn.execute(
        _text("SELECT sql FROM sqlite_master WHERE type='table' AND name='pet_participants'")
    ).fetchone()
    ddl = (row[0] or "").lower() if row else ""
    if "uq_pet_participants_user_id" not in ddl:
        return
    sync_conn.execute(
        _text(
            "CREATE TABLE pet_participants_new ("
            "pet_id VARCHAR(32) NOT NULL, "
            "user_id VARCHAR(32) NOT NULL, "
            "joined_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, "
            "confirmed_adoption_at DATETIME, "
            "PRIMARY KEY (pet_id, user_id), "
            "FOREIGN KEY(pet_id) REFERENCES pets (id) ON DELETE CASCADE, "
            "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE)"
        )
    )
    sync_conn.execute(
        _text(
            "INSERT INTO pet_participants_new "
            "(pet_id, user_id, joined_at, confirmed_adoption_at) "
            "SELECT pet_id, user_id, joined_at, confirmed_adoption_at "
            "FROM pet_participants"
        )
    )
    sync_conn.execute(_text("DROP TABLE pet_participants"))
    sync_conn.execute(
        _text("ALTER TABLE pet_participants_new RENAME TO pet_participants")
    )


class SiteAccessIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _site_access_exempt(path: str) -> bool:
    return (
        path in SITE_ACCESS_ALLOWLIST
        or path.startswith("/static/")
        or path.startswith("/admin/")
    )


# Auth-failure throttle for the two secret-guessing surfaces: the site
# password and the admin token. Counts FAILURES only (401/403), per client
# IP, in-process — correct here because the deployment is single-worker by
# design. Successes never count, so a fumbled password costs a human
# nothing and a dictionary run dies in its first seconds.
THROTTLE_WINDOW_S = 900
THROTTLE_LIMITS = {"site-access": 5, "admin": 20}


def _throttle_scope(path: str, method: str) -> str | None:
    if path.startswith("/admin/"):
        return "admin"
    if path == "/api/site-access" and method == "POST":
        return "site-access"
    return None


def _failures(bucket: dict, key: tuple[str, str]) -> deque:
    q = bucket.setdefault(key, deque())
    cutoff = monotonic() - THROTTLE_WINDOW_S
    while q and q[0] < cutoff:
        q.popleft()
    return q


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    # Fail-fast if we're in prod with the dev secret — a forgotten SECRET_KEY
    # means anyone can forge signed session cookies.
    if settings.is_prod and (
        not settings.secret_key or settings.secret_key == DEFAULT_DEV_SECRET
    ):
        raise RuntimeError(
            "SECRET_KEY is unset or still the default in prod; refusing to start"
        )
    # The site-access and guest cookies are signed with SECRET_KEY too; with
    # the dev default they are forgeable offline in any ENV, so a password
    # gate over a default secret would be theater. Refuse to pretend.
    if (settings.site_password or settings.guest_access_enabled) and (
        not settings.secret_key or settings.secret_key == DEFAULT_DEV_SECRET
    ):
        raise RuntimeError(
            "SITE_PASSWORD / guest access require a real SECRET_KEY (their "
            "cookies are signed with it); set SECRET_KEY to a random value"
        )
    # Woolroom content packs (format v1): load + register species, phrase
    # overlays, quirks, and voice BEFORE any request is served; the
    # registries are frozen again afterwards. PACK_PATHS defaults to empty
    # (a no-op); any gate violation raises a named PackError and refuses boot.
    load_packs(settings.pack_paths)
    # In prod the schema is owned by alembic (scripts/migrate.py runs before
    # uvicorn); create_all here would silently create tables alembic never
    # sees and drift the two apart.
    if not settings.is_prod:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_ensure_coat_column)
            await conn.run_sync(_ensure_demo_column)
            await conn.run_sync(_ensure_household_columns)
    sched = start_scheduler()
    try:
        yield
    finally:
        sched.shutdown(wait=False)
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="woolroom", lifespan=lifespan)
    app.state.auth_failures = {}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        # setdefault: routes that set a stricter policy (e.g. the recovery
        # redirects' Referrer-Policy) keep theirs.
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.middleware("http")
    async def auth_failure_throttle(request: Request, call_next):
        scope = _throttle_scope(request.url.path, request.method)
        if scope is None:
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        failures = _failures(app.state.auth_failures, (scope, ip))
        if len(failures) >= THROTTLE_LIMITS[scope]:
            return JSONResponse(
                {"detail": "too many attempts — the door needs a quiet quarter hour"},
                status_code=429,
            )
        resp = await call_next(request)
        if resp.status_code in (401, 403):
            failures.append(monotonic())
        return resp

    @app.middleware("http")
    async def site_access_gate(request: Request, call_next):
        path = request.url.path
        request.state.guest = False
        if not site_access_enabled() or _site_access_exempt(path):
            return await call_next(request)

        if has_site_access(request.cookies.get(SITE_ACCESS_COOKIE)):
            return await call_next(request)

        # Read-only guests pass the outer gate with their own cookie — every
        # private surface beyond this still requires a real session, and the
        # guest endpoints serve only the sanitized scene.
        if has_guest_access(request.cookies.get(GUEST_ACCESS_COOKIE)):
            request.state.guest = True
            if path in GUEST_HTTP_ALLOWLIST:
                return await call_next(request)
            if path.startswith("/api/"):
                return JSONResponse({"detail": "site access required"}, status_code=401)

        if path.startswith("/api/"):
            return JSONResponse({"detail": "site access required"}, status_code=401)

        target = path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=f"/access?next={quote(target, safe='')}", status_code=303)

    app.include_router(http_router)
    app.include_router(ws_router)

    # Serve the scene. Two cache policies, chosen per request:
    #  - `?v=<APP_VERSION>` present and current → `immutable` for a year. The
    #    index injects the deploy version into every /static/ reference, so a
    #    repeat visit loads JS/CSS/fonts straight from cache with ZERO
    #    revalidation rounds (previously every asset cost a conditional GET
    #    per visit — 10+ RTTs on mobile before the first byte of app code).
    #  - anything else → `max-age=0, must-revalidate` as before: without this,
    #    iOS Safari uses heuristic freshness against last-modified and can
    #    serve stale JS/CSS for days. Etag keeps steady-state revalidation at
    #    cheap 304s. A mismatched/stale ?v falls here on purpose — the client
    #    revalidates and the fresh index hands out the new version.
    _IMMUTABLE = "public, max-age=31536000, immutable"
    _REVALIDATE = "public, max-age=0, must-revalidate"

    class RevalidatingStaticFiles(StaticFiles):
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            qs = parse_qs(scope.get("query_string", b"").decode())
            response.headers["Cache-Control"] = (
                _IMMUTABLE if qs.get("v") == [APP_VERSION] else _REVALIDATE
            )
            return response

    app.mount("/static", RevalidatingStaticFiles(directory=str(STATIC_DIR)), name="static")

    # Index with serve-time voice substitution + deploy-versioned static
    # references, computed once per process (the image is immutable between
    # deploys; APP_VERSION changes → restart). Voice first: the {{VOICE_*}}
    # placeholders (app/data/voice.py:INDEX_VOICE) carry the landing/adopt/
    # ceremony copy that must render before JS boots, so it can't wait for a
    # fetch. A leftover placeholder means the two drifted apart — fail at
    # boot, not in someone's browser. Then the two version injections:
    # href/src/content attributes get ?v=<version> appended; the import map
    # carries __APP_VERSION__ tokens (attribute-scoped regex must NOT touch
    # it — mapped keys have to stay exact resolved URLs).
    def _versioned_index_html() -> str:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        for key, value in INDEX_VOICE.items():
            html = html.replace(f"{{{{VOICE_{key}}}}}", value)
        if "{{VOICE_" in html:
            raise RuntimeError("index.html carries a {{VOICE_*}} placeholder INDEX_VOICE doesn't know")
        html = re.sub(
            r'((?:href|src|content)=")(/static/[A-Za-z0-9_./-]+)(")',
            lambda m: f"{m.group(1)}{m.group(2)}?v={APP_VERSION}{m.group(3)}",
            html,
        )
        return html.replace("__APP_VERSION__", APP_VERSION)

    _INDEX_HTML = _versioned_index_html()

    @app.get("/access", name="site_access_page", response_model=None)
    async def access(request: Request) -> Response:
        if not site_access_enabled():
            return RedirectResponse(url="/", status_code=303)
        if has_site_access(request.cookies.get(SITE_ACCESS_COOKIE)):
            return RedirectResponse(url=request.query_params.get("next", "/"), status_code=303)
        resp = FileResponse(STATIC_DIR / "access.html")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/site-access")
    async def grant_site_access(body: SiteAccessIn) -> JSONResponse:
        if not site_access_enabled():
            return JSONResponse({"ok": True})
        if not verify_site_password(body.password):
            raise HTTPException(status_code=401, detail="access denied")
        resp = JSONResponse({"ok": True})
        resp.headers["Cache-Control"] = "no-store"
        set_site_access_cookie(resp)
        return resp

    @app.post("/api/site-access/logout")
    async def revoke_site_access() -> JSONResponse:
        resp = JSONResponse({"ok": True})
        clear_site_access_cookie(resp)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/guest-access")
    async def grant_guest_access() -> JSONResponse:
        """Mint a read-only guest cookie. No password, no session — the cookie
        only proves guest status and unlocks the sanitized scene."""
        if not guest_access_enabled():
            raise HTTPException(status_code=404, detail="guest access is not open")
        resp = JSONResponse({"ok": True, "guest": True})
        resp.headers["Cache-Control"] = "no-store"
        set_guest_access_cookie(resp)
        return resp

    @app.get("/api/voice")
    async def client_voice(request: Request) -> JSONResponse:
        # The client copy pack (app/data/voice.py:CLIENT_VOICE), fetched once
        # at boot alongside /api/me. Static per deploy, so it rides the same
        # two cache policies as the statics: `?v=<APP_VERSION>` → immutable;
        # anything else (the boot fetch carries no version — the client only
        # learns it from the scene payload later) → cheap revalidation. Open
        # to guests like /api/me: the room's copy is not a secret.
        resp = JSONResponse(CLIENT_VOICE)
        resp.headers["Cache-Control"] = (
            _IMMUTABLE if request.query_params.get("v") == APP_VERSION else _REVALIDATE
        )
        return resp

    @app.get("/api/packs")
    async def pack_assets(request: Request) -> JSONResponse:
        # The pack-species figure assets (app/packs/loader.py:PACK_ASSETS,
        # shaped by client_pack_assets), fetched once at boot alongside
        # /api/voice — the builtin cat is NOT in the map (the client has
        # it), so it's empty when no packs loaded. Same static-content
        # treatment as /api/voice: the two cache policies, guest allowlist,
        # never flag-gated.
        resp = JSONResponse(client_pack_assets())
        resp.headers["Cache-Control"] = (
            _IMMUTABLE if request.query_params.get("v") == APP_VERSION else _REVALIDATE
        )
        return resp

    @app.get("/")
    async def index() -> Response:
        # HTML itself stays revalidating — a stale index can hide a fresh
        # deploy from already-loaded clients — but every /static/ reference
        # inside carries ?v=<APP_VERSION>, so the heavy assets ride the
        # immutable policy above and repeat visits skip revalidation entirely.
        return Response(
            content=_INDEX_HTML,
            media_type="text/html",
            headers={"Cache-Control": "public, max-age=0, must-revalidate"},
        )

    return app


app = create_app()

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI

from woolroom.overlay import (
    PLUGIN_API_VERSION,
    BoundPetCard,
    CatalogOverlayError,
    CatalogOverlayProvider,
    EmptyCatalogOverlayProvider,
    GuestCardSubject,
    OwnerCardSubject,
)

try:
    __version__ = version("woolroom")
except PackageNotFoundError:
    __version__ = "0.2.0"


def create_app(
    *,
    overlay_provider: CatalogOverlayProvider | None = None,
) -> FastAPI:
    from app.main import create_app as app_factory

    return app_factory(overlay_provider=overlay_provider)


def migration_path() -> Path:
    return Path(str(files("woolroom.migrations")))


__all__ = [
    "PLUGIN_API_VERSION",
    "BoundPetCard",
    "CatalogOverlayError",
    "CatalogOverlayProvider",
    "EmptyCatalogOverlayProvider",
    "GuestCardSubject",
    "OwnerCardSubject",
    "__version__",
    "create_app",
    "migration_path",
]

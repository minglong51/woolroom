from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI

from woolroom.adoption import AdoptionDefaults
from woolroom.auth import DEFAULT_AUTH_NAMESPACE, AuthNamespace
from woolroom.database import (
    DatabaseBoundaryError,
    DatabaseInspection,
    DatabaseState,
    adopt_database,
    inspect_database,
    migration_head,
    migration_revisions,
    upgrade_database,
)
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
    __version__ = "0.3.0"


def create_app(
    *,
    overlay_provider: CatalogOverlayProvider | None = None,
    auth_namespace: AuthNamespace | None = None,
    adoption_defaults: AdoptionDefaults | None = None,
) -> FastAPI:
    from app.main import create_app as app_factory

    return app_factory(
        overlay_provider=overlay_provider,
        auth_namespace=auth_namespace,
        adoption_defaults=adoption_defaults,
    )


def migration_path() -> Path:
    return Path(str(files("woolroom.migrations")))


__all__ = [
    "DEFAULT_AUTH_NAMESPACE",
    "PLUGIN_API_VERSION",
    "AdoptionDefaults",
    "AuthNamespace",
    "BoundPetCard",
    "CatalogOverlayError",
    "CatalogOverlayProvider",
    "DatabaseBoundaryError",
    "DatabaseInspection",
    "DatabaseState",
    "EmptyCatalogOverlayProvider",
    "GuestCardSubject",
    "OwnerCardSubject",
    "__version__",
    "adopt_database",
    "create_app",
    "inspect_database",
    "migration_head",
    "migration_path",
    "migration_revisions",
    "upgrade_database",
]

from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    anthropic_api_key: str = ""
    secret_key: str = "dev-change-me"
    site_password: str = ""
    site_access_days: int = 60
    database_url: str = "sqlite+aiosqlite:///./woolroom.db"
    base_url: str = ""  # empty = derive from each request; set via env for proxies
    log_level: str = "info"
    env: str = "dev"

    # LLM provider: "anthropic" (Claude Haiku via cloud) or "ollama" (local).
    llm_provider: str = "anthropic"
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_max_tokens: int = 80
    llm_timeout_s: float = 8.0
    # Budget circuit-breaker: max LLM calls per pet per UTC day. At/over cap the
    # respond path silently falls back to the deterministic phrasebook.
    llm_daily_call_cap: int = 50
    # Ollama-specific. Only consulted when llm_provider == "ollama".
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct-q4_K_M"

    mood_drift_interval_minutes: int = 15
    # The pet's home timezone: drives the diurnal mood curve, day-keyed
    # facts/quirks, and the local hour the daily cron jobs fire at.
    home_tz: str = "UTC"
    daily_outing_hour: int = 9
    buffer_max_events: int = 50
    moments_max_per_year: int = 52

    adopt_allowlist: str = ""

    # Woolroom content packs (pack format v1): comma-separated local
    # directories, loaded + registered at boot by app/packs/loader.py behind
    # fail-closed sanitization gates. Empty = no packs, behavior unchanged.
    # NoDecode: env carries a CSV string (like ADOPT_ALLOWLIST), not JSON.
    pack_paths: Annotated[list[str], NoDecode] = []

    # Household shape. Pair-shaped BY DESIGN: the two-human couple is a
    # semantic, not a default — household_size is pinned to 2 at validation
    # below; N-human is a future designed mode (family mode, woolroom v2),
    # not a config value. Rooms and quirk picks are tunable but must be >= 1.
    household_size: int = 2
    max_rooms_per_household: int = 2
    quirk_pick_count: int = 2

    # Read-only guest mode. When on, /api/guest-access mints a signed cookie
    # that lets a visitor watch the wool scene (REST + WS) without a session —
    # all private fields are stripped server-side. Default off.
    guest_access_enabled: bool = False
    # Which pet guests watch. PRIVACY: when set, ONLY this pet is resolvable —
    # no fallback, so a wrong id can never expose a real household's pet.
    # In prod, seed the demo pet with scripts/seed_demo_pet.py and pin its id
    # here. Empty = first pet (dev convenience only).
    guest_pet_id: str = ""

    @property
    def allowlisted_user_ids(self) -> frozenset[str]:
        if not self.adopt_allowlist:
            return frozenset()
        return frozenset(s.strip() for s in self.adopt_allowlist.split(",") if s.strip())

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    # Shared secret for /admin/* endpoints. Empty disables admin entirely.
    admin_token: str = ""

    # If true, /api/start mints a fresh user even without a pending_invite
    # cookie. Production should keep this off — invite-only signup eliminates
    # the orphan-duplicate failure mode where a returning user accidentally
    # creates a second account by retyping their display_name. Tests flip it
    # on so the fixture path doesn't have to thread invite cookies.
    open_signup: bool = False

    @field_validator("env", mode="before")
    @classmethod
    def normalize_env(cls, value: object) -> str:
        normalized = str(value).strip().casefold()
        if normalized not in {"dev", "prod"}:
            raise ValueError("ENV must be dev or prod")
        return normalized

    @field_validator("pack_paths", mode="before")
    @classmethod
    def split_pack_paths(cls, value: object) -> object:
        # Env carries PACK_PATHS as a comma-separated string, same idiom as
        # ADOPT_ALLOWLIST; an actual list (tests, .env-less construction)
        # passes through.
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        return value

    @model_validator(mode="after")
    def check_household_shape(self) -> "Settings":
        if self.household_size != 2:
            raise ValueError(
                "HOUSEHOLD_SIZE must be 2: woolroom is pair-shaped by design. "
                "N-human households are a future designed mode (family mode, "
                "woolroom v2), not a config value."
            )
        if self.max_rooms_per_household < 1:
            raise ValueError("MAX_ROOMS_PER_HOUSEHOLD must be at least 1")
        if self.quirk_pick_count < 1:
            raise ValueError("QUIRK_PICK_COUNT must be at least 1")
        return self

    @model_validator(mode="after")
    def check_prod_anthropic_credentials(self) -> "Settings":
        if self.is_prod:
            key = self.anthropic_api_key.strip()
            # Metered API keys are allowed; the restriction targets
            # subscription/OAuth credentials, which must never be routed
            # through a public deployment.
            if key and not key.startswith("sk-ant-api"):
                raise ValueError(
                    "only metered Anthropic API keys (sk-ant-api...) are allowed "
                    "in production; subscription/OAuth credentials are forbidden"
                )
            if len(self.secret_key.encode()) < 32:
                raise ValueError("SECRET_KEY must be at least 32 bytes in production")
            if self.guest_access_enabled and not self.guest_pet_id.strip():
                raise ValueError(
                    "GUEST_PET_ID is required when guest access is enabled in production"
                )
        return self


settings = Settings()

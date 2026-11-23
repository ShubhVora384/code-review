from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- app ----
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- groq ----
    # Up to three free keys are rotated so one key's minute/day budget never
    # becomes the whole system's ceiling. Any of these may be set; duplicates
    # and placeholders are dropped.
    groq_api_key: str = ""
    groq_api_key_2: str = ""
    groq_api_key_3: str = ""
    groq_api_keys: str = ""  # optional comma-separated alternative

    # round_robin | random | least_used
    groq_key_strategy: str = "round_robin"
    # Seconds a key sits out after a 429 when the API sends no Retry-After.
    groq_key_cooldown_sec: float = 20.0

    groq_model_deep: str = "openai/gpt-oss-120b"
    groq_model_fast: str = "llama-3.1-8b-instant"

    # gpt-oss models are reasoning models: their reasoning trace is billed as
    # completion tokens. On a free tier capped at tokens-per-minute that is the
    # dominant cost, so this defaults low. Raise to "medium"/"high" for subtler
    # findings if you have headroom.
    groq_reasoning_effort: str = "low"

    # Free-tier governor. These are the published free-plan ceilings; the
    # governor keeps us just under them so we never eat a 429.
    groq_deep_tpm: int = 8_000
    groq_deep_rpm: int = 30
    groq_deep_rpd: int = 1_000
    groq_deep_tpd: int = 200_000
    groq_fast_tpm: int = 15_000
    groq_fast_rpm: int = 30
    groq_fast_rpd: int = 14_400
    groq_fast_tpd: int = 500_000

    # Model context ceiling (both current Groq text models are 131,072).
    model_context_window: int = 131_072
    # Leave headroom for the completion + system prompt.
    max_prompt_tokens: int = 96_000

    # ---- datastores ----
    database_url: str = "sqlite+aiosqlite:///./codereview.db"
    redis_url: str = ""

    # ---- local folder picker ----
    # Directories the /browse endpoint may list. Defaults to the user's home.
    # Keep this as narrow as is practical: it exposes directory structure.
    browse_roots: str = ""

    # ---- ingestion guardrails ----
    workspace_dir: Path = Path("./.workspaces")
    max_repo_mb: int = 200
    max_files: int = 4_000
    max_file_kb: int = 512
    clone_timeout_sec: int = 180

    # ---- review tuning ----
    max_concurrent_file_reviews: int = 3
    critic_max_revisions: int = 1

    # ---- proof engine ----
    # Executes code from the repository under audit, so isolation is the whole
    # point. "subprocess" is a development convenience and is refused outside
    # development; anything hosted must use docker.
    proof_sandbox: str = "auto"  # auto | docker | subprocess | disabled
    proof_enabled: bool = True
    # Proofs cost tokens and wall-clock, and TPM is already the binding
    # constraint, so only the findings that matter are attempted.
    proof_max_per_review: int = 10
    proof_timeout_sec: int = 25
    proof_memory_mb: int = 512
    proof_image: str = "python:3.12-slim"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        """Accept plain driver URLs and upgrade them to async drivers."""
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("sqlite://") and "aiosqlite" not in v:
            return v.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @staticmethod
    def _is_real_key(key: str) -> bool:
        """The .env placeholder is truthy but useless -- filter it out."""
        key = key.strip()
        return bool(key) and not key.startswith("gsk_replace") and len(key) > 20

    @property
    def groq_key_list(self) -> list[str]:
        """Every configured key, de-duplicated, in a stable order."""
        raw = [self.groq_api_key, self.groq_api_key_2, self.groq_api_key_3]
        raw += self.groq_api_keys.split(",")
        seen: set[str] = set()
        keys: list[str] = []
        for candidate in raw:
            candidate = candidate.strip()
            if not self._is_real_key(candidate) or candidate in seen:
                continue
            seen.add(candidate)
            keys.append(candidate)
        return keys

    @property
    def groq_ready(self) -> bool:
        return bool(self.groq_key_list)

    @property
    def has_redis(self) -> bool:
        """Redis is optional — without it we fall back to in-process state."""
        return bool(self.redis_url.strip())

    @property
    def browse_root_list(self) -> list[str]:
        if self.browse_roots.strip():
            return [r.strip() for r in self.browse_roots.split(",") if r.strip()]
        return [str(Path.home())]

    @property
    def workspace_path(self) -> Path:
        p = Path(self.workspace_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

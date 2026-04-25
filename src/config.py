"""Application configuration for Drive Backup Triage."""

import os
from dataclasses import dataclass, field


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_optional(key: str) -> str | None:
    return os.environ.get(key)


@dataclass
class AppConfig:
    """Central configuration for the Drive Backup Triage application.

    All fields can be overridden via environment variables prefixed with
    ``DBT_`` (e.g. ``DBT_LLM_PROVIDER``, ``DBT_MODEL``, ``DBT_API_KEY``).
    """

    db_path: str = field(default_factory=lambda: _env("DBT_DB_PATH", "drive_triage.db"))
    llm_provider: str = field(default_factory=lambda: _env("DBT_LLM_PROVIDER", "ollama"))
    model: str = field(default_factory=lambda: _env("DBT_MODEL", "llama3.2"))
    base_url: str = field(default_factory=lambda: _env("DBT_BASE_URL", "http://localhost:11434"))
    api_key: str | None = field(default_factory=lambda: _env_optional("DBT_API_KEY"))
    confidence_threshold: float = field(default_factory=lambda: float(_env("DBT_CONFIDENCE_THRESHOLD", "0.7")))
    batch_size: int = field(default_factory=lambda: int(_env("DBT_BATCH_SIZE", "50")))

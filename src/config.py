"""Application configuration for bakflow."""

import os
from dataclasses import dataclass, field


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_optional(key: str) -> str | None:
    return os.environ.get(key)


@dataclass
class AppConfig:
    """Central configuration for the bakflow application.

    All fields can be overridden via environment variables prefixed with
    ``BF_`` (e.g. ``BF_LLM_PROVIDER``, ``BF_MODEL``, ``BF_API_KEY``).
    """

    db_path: str = field(default_factory=lambda: _env("BF_DB_PATH", "drive_triage.db"))
    llm_provider: str = field(default_factory=lambda: _env("BF_LLM_PROVIDER", "ollama"))
    model: str = field(default_factory=lambda: _env("BF_MODEL", "llama3.2"))
    base_url: str = field(default_factory=lambda: _env("BF_BASE_URL", "http://localhost:11434"))
    api_key: str | None = field(default_factory=lambda: _env_optional("BF_API_KEY"))
    confidence_threshold: float = field(default_factory=lambda: float(_env("BF_CONFIDENCE_THRESHOLD", "0.7")))
    batch_size: int = field(default_factory=lambda: int(_env("BF_BATCH_SIZE", "50")))

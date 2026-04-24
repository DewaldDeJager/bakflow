"""Application configuration for Drive Backup Triage."""

from dataclasses import dataclass


@dataclass
class AppConfig:
    """Central configuration for the Drive Backup Triage application."""

    db_path: str = "drive_triage.db"
    llm_provider: str = "ollama"
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434"
    api_key: str | None = None
    confidence_threshold: float = 0.7
    batch_size: int = 50

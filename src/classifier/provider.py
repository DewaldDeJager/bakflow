"""LLMProvider protocol and factory function.

Defines the abstract interface that all LLM backends must implement, plus a
factory that returns the correct provider based on application configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.db.models import (
    FileClassification,
    FileSummary,
    FolderClassification,
    FolderSummary,
)


@dataclass
class ClassifierConfig:
    """Configuration subset relevant to the classifier."""

    provider: str = "ollama"
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434"
    api_key: str | None = None
    confidence_threshold: float = 0.7
    batch_size: int = 50


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol that all LLM backends must implement."""

    async def classify_files(
        self, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        """Send file summaries, get back structured File_Class + confidence."""
        ...

    async def classify_folders(
        self, summaries: list[FolderSummary]
    ) -> list[FolderClassification]:
        """Send folder summaries, get back structured Folder_Purpose + confidence."""
        ...


def create_provider(config: ClassifierConfig) -> LLMProvider:
    """Factory: returns OllamaProvider or OpenAIProvider based on config.provider."""
    if config.provider == "ollama":
        from src.classifier.ollama_provider import OllamaProvider

        return OllamaProvider(model=config.model, base_url=config.base_url)
    elif config.provider == "openai":
        from src.classifier.openai_provider import OpenAIProvider

        return OpenAIProvider(model=config.model, api_key=config.api_key)
    else:
        raise ValueError(f"Unknown LLM provider: {config.provider}")

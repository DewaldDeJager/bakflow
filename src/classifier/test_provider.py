"""Tests for provider protocol, ClassifierConfig, and wavefront method implementations."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.classifier.provider import ClassifierConfig, LLMProvider
from src.classifier.ollama_provider import OllamaProvider
from src.classifier.openai_provider import OpenAIProvider
from src.db.models import (
    FileClassification,
    FileSummary,
    FolderClassification,
    FolderSummary,
    WavefrontFolderClassification,
    WavefrontFolderSummary,
)


# ---------------------------------------------------------------------------
# Protocol tests
# ---------------------------------------------------------------------------


class _CompleteProvider:
    """A class implementing all three LLMProvider methods."""

    async def classify_files(
        self, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        return []

    async def classify_folders(
        self, summaries: list[FolderSummary]
    ) -> list[FolderClassification]:
        return []

    async def classify_folders_wavefront(
        self, summaries: list[WavefrontFolderSummary]
    ) -> list[WavefrontFolderClassification]:
        return []


class _IncompleteProvider:
    """A class missing classify_folders_wavefront."""

    async def classify_files(
        self, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        return []

    async def classify_folders(
        self, summaries: list[FolderSummary]
    ) -> list[FolderClassification]:
        return []


def test_protocol_accepts_complete_implementation():
    """A class with all three methods is recognized as LLMProvider."""
    provider = _CompleteProvider()
    assert isinstance(provider, LLMProvider)


def test_protocol_rejects_incomplete_implementation():
    """A class missing classify_folders_wavefront is NOT an LLMProvider."""
    provider = _IncompleteProvider()
    assert not isinstance(provider, LLMProvider)


def test_ollama_provider_has_wavefront_method():
    """OllamaProvider has classify_folders_wavefront attribute."""
    assert hasattr(OllamaProvider, "classify_folders_wavefront")
    assert callable(getattr(OllamaProvider, "classify_folders_wavefront"))


def test_openai_provider_has_wavefront_method():
    """OpenAIProvider has classify_folders_wavefront attribute."""
    assert hasattr(OpenAIProvider, "classify_folders_wavefront")
    assert callable(getattr(OpenAIProvider, "classify_folders_wavefront"))


# ---------------------------------------------------------------------------
# ClassifierConfig tests
# ---------------------------------------------------------------------------


def test_classifier_config_wavefront_batch_size_default():
    """ClassifierConfig has wavefront_batch_size with default value 10."""
    config = ClassifierConfig()
    assert config.wavefront_batch_size == 10


def test_classifier_config_wavefront_batch_size_custom():
    """ClassifierConfig accepts a custom wavefront_batch_size."""
    config = ClassifierConfig(wavefront_batch_size=25)
    assert config.wavefront_batch_size == 25


# ---------------------------------------------------------------------------
# OllamaProvider wavefront prompt integration test (monkeypatched)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_wavefront_uses_wavefront_prompt(monkeypatch):
    """Verify OllamaProvider.classify_folders_wavefront calls the wavefront prompt
    and parses the response correctly."""
    captured_messages: list[str] = []

    llm_response_data = {
        "entry_id": 42,
        "folder_purpose": "system_or_temp",
        "decision": "exclude",
        "classification_confidence": 0.9,
        "decision_confidence": 0.85,
        "reasoning": "System temp folder, safe to exclude.",
    }

    mock_response = MagicMock()
    mock_response.message.content = json.dumps(llm_response_data)

    async def fake_chat(*, model, messages, format):
        captured_messages.append(messages[0]["content"])
        return mock_response

    provider = OllamaProvider.__new__(OllamaProvider)
    provider.model = "test-model"
    provider._client = MagicMock()
    provider._client.chat = fake_chat

    summary = WavefrontFolderSummary(
        entry_id=42,
        path="C:\\Windows\\Temp",
        name="Temp",
        depth=2,
        size_bytes=1024000,
        child_count=50,
        descendant_file_count=200,
        descendant_folder_count=10,
        file_type_distribution={".tmp": 150, ".log": 50},
        subfolder_names=["cache", "logs"],
        parent_classification="system_or_temp",
        parent_decision="descend",
    )

    results = await provider.classify_folders_wavefront([summary])

    assert len(results) == 1
    result = results[0]
    assert result.entry_id == 42
    assert result.folder_purpose == "system_or_temp"
    assert result.decision == "exclude"
    assert result.classification_confidence == 0.9
    assert result.decision_confidence == 0.85

    # Verify the prompt contains wavefront-specific content
    assert len(captured_messages) == 1
    prompt = captured_messages[0]
    assert "triage" in prompt.lower() or "Triage" in prompt
    assert "include" in prompt
    assert "exclude" in prompt
    assert "descend" in prompt
    assert "classification_confidence" in prompt
    assert "decision_confidence" in prompt

"""Ollama LLM provider for file and folder classification.

Uses the ``ollama`` Python SDK with Pydantic JSON schema via the ``format``
parameter for structured output.  Handles connection errors, timeouts, and
malformed responses gracefully.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import ollama as ollama_sdk
from pydantic import BaseModel, Field, ValidationError

from src.classifier.prompts import (
    build_file_classification_prompt,
    build_folder_classification_prompt,
)
from src.db.models import (
    FileClassification,
    FileSummary,
    FolderClassification,
    FolderSummary,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response wrapper models (used for Ollama's format parameter)
# ---------------------------------------------------------------------------


class _FileClassificationItem(BaseModel):
    entry_id: int
    file_class: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class _FileClassificationResponse(BaseModel):
    classifications: list[_FileClassificationItem]


class _FolderClassificationItem(BaseModel):
    entry_id: int
    folder_purpose: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OllamaProvider:
    """LLM provider using Ollama for local inference."""

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self.model = model
        self._client = ollama_sdk.AsyncClient(host=base_url)

    async def classify_files(
        self, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        """Classify file entries via Ollama with structured JSON output."""
        if not summaries:
            return []

        prompt = build_file_classification_prompt(summaries)
        schema = _FileClassificationResponse.model_json_schema()

        try:
            response = await self._client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                format=schema,
            )
        except ollama_sdk.ResponseError as exc:
            logger.error("Ollama response error during file classification: %s", exc)
            raise
        except Exception as exc:
            logger.error("Ollama connection error during file classification: %s", exc)
            raise ConnectionError(
                f"Failed to connect to Ollama: {exc}"
            ) from exc

        return self._parse_file_response(response, summaries)

    async def classify_folders(
        self, summaries: list[FolderSummary]
    ) -> list[FolderClassification]:
        """Classify folder entries via Ollama with structured JSON output.

        Folders are classified one at a time since each prompt includes
        aggregated folder statistics specific to that folder.
        """
        if not summaries:
            return []

        results: list[FolderClassification] = []
        for summary in summaries:
            prompt = build_folder_classification_prompt(summary)
            schema = _FolderClassificationItem.model_json_schema()

            try:
                response = await self._client.chat(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    format=schema,
                )
            except ollama_sdk.ResponseError as exc:
                logger.error(
                    "Ollama response error classifying folder %s: %s",
                    summary.path,
                    exc,
                )
                raise
            except Exception as exc:
                logger.error(
                    "Ollama connection error classifying folder %s: %s",
                    summary.path,
                    exc,
                )
                raise ConnectionError(
                    f"Failed to connect to Ollama: {exc}"
                ) from exc

            classification = self._parse_folder_response(response, summary)
            results.append(classification)

        return results

    # -----------------------------------------------------------------------
    # Response parsing helpers
    # -----------------------------------------------------------------------

    def _parse_file_response(
        self, response: Any, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        """Parse Ollama's structured response into FileClassification models."""
        content = response.message.content
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("Malformed JSON from Ollama for file classification: %s", exc)
            raise ValueError(f"Malformed JSON response from Ollama: {exc}") from exc

        # The response may be a dict with a "classifications" key or a bare list
        if isinstance(data, dict) and "classifications" in data:
            items = data["classifications"]
        elif isinstance(data, list):
            items = data
        else:
            raise ValueError(
                f"Unexpected response structure from Ollama: {type(data)}"
            )

        valid_ids = {s.entry_id for s in summaries}
        results: list[FileClassification] = []
        for item in items:
            try:
                fc = FileClassification.model_validate(item)
                if fc.entry_id in valid_ids:
                    results.append(fc)
                else:
                    logger.warning(
                        "Ollama returned unknown entry_id %d, skipping", fc.entry_id
                    )
            except ValidationError as exc:
                logger.warning("Skipping malformed file classification item: %s", exc)

        return results

    def _parse_folder_response(
        self, response: Any, summary: FolderSummary
    ) -> FolderClassification:
        """Parse Ollama's structured response into a FolderClassification."""
        content = response.message.content
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("Malformed JSON from Ollama for folder classification: %s", exc)
            raise ValueError(f"Malformed JSON response from Ollama: {exc}") from exc

        try:
            fc = FolderClassification.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid folder classification from Ollama: {exc}"
            ) from exc

        # Ensure the entry_id matches
        if fc.entry_id != summary.entry_id:
            logger.warning(
                "Ollama returned entry_id %d but expected %d, correcting",
                fc.entry_id,
                summary.entry_id,
            )
            fc = fc.model_copy(update={"entry_id": summary.entry_id})

        return fc

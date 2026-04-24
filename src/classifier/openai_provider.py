"""OpenAI LLM provider for file and folder classification.

Uses the ``openai`` Python SDK with ``response_format`` / ``json_schema`` for
structured output.  Handles auth failures, rate limits (exponential backoff),
and malformed responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import openai
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
# Constants
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_INITIAL_BACKOFF = 1.0  # seconds


# ---------------------------------------------------------------------------
# JSON-schema wrapper models for OpenAI's response_format
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


class OpenAIProvider:
    """LLM provider using OpenAI API (GPT-4o, etc.)."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self._client = openai.AsyncOpenAI(api_key=api_key)

    async def classify_files(
        self, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        """Classify file entries via OpenAI with structured JSON output."""
        if not summaries:
            return []

        prompt = build_file_classification_prompt(summaries)
        schema = _FileClassificationResponse.model_json_schema()

        response = await self._call_with_backoff(
            prompt,
            schema,
            schema_name="file_classifications",
        )
        return self._parse_file_response(response, summaries)

    async def classify_folders(
        self, summaries: list[FolderSummary]
    ) -> list[FolderClassification]:
        """Classify folder entries via OpenAI with structured JSON output.

        Folders are classified one at a time since each prompt includes
        aggregated folder statistics specific to that folder.
        """
        if not summaries:
            return []

        schema = _FolderClassificationItem.model_json_schema()
        results: list[FolderClassification] = []

        for summary in summaries:
            prompt = build_folder_classification_prompt(summary)
            response = await self._call_with_backoff(
                prompt,
                schema,
                schema_name="folder_classification",
            )
            classification = self._parse_folder_response(response, summary)
            results.append(classification)

        return results

    # -----------------------------------------------------------------------
    # API call with exponential backoff for rate limits
    # -----------------------------------------------------------------------

    async def _call_with_backoff(
        self,
        prompt: str,
        json_schema: dict[str, Any],
        schema_name: str,
    ) -> Any:
        """Call OpenAI chat completions with exponential backoff on rate limits."""
        backoff = _INITIAL_BACKOFF

        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "schema": json_schema,
                            "strict": True,
                        },
                    },
                )
                return response
            except openai.RateLimitError as exc:
                if attempt == _MAX_RETRIES - 1:
                    logger.error(
                        "OpenAI rate limit exceeded after %d retries: %s",
                        _MAX_RETRIES,
                        exc,
                    )
                    raise
                logger.warning(
                    "OpenAI rate limit hit, retrying in %.1fs (attempt %d/%d)",
                    backoff,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(backoff)
                backoff *= 2
            except openai.AuthenticationError as exc:
                logger.error("OpenAI authentication failed: %s", exc)
                raise
            except openai.APIConnectionError as exc:
                logger.error("OpenAI connection error: %s", exc)
                raise ConnectionError(
                    f"Failed to connect to OpenAI: {exc}"
                ) from exc

        # Should not reach here, but just in case
        raise RuntimeError("Exhausted retries without success or exception")

    # -----------------------------------------------------------------------
    # Response parsing helpers
    # -----------------------------------------------------------------------

    def _parse_file_response(
        self, response: Any, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        """Parse OpenAI's structured response into FileClassification models."""
        content = response.choices[0].message.content
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("Malformed JSON from OpenAI for file classification: %s", exc)
            raise ValueError(f"Malformed JSON response from OpenAI: {exc}") from exc

        # The response should be a dict with a "classifications" key
        if isinstance(data, dict) and "classifications" in data:
            items = data["classifications"]
        elif isinstance(data, list):
            items = data
        else:
            raise ValueError(
                f"Unexpected response structure from OpenAI: {type(data)}"
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
                        "OpenAI returned unknown entry_id %d, skipping", fc.entry_id
                    )
            except ValidationError as exc:
                logger.warning("Skipping malformed file classification item: %s", exc)

        return results

    def _parse_folder_response(
        self, response: Any, summary: FolderSummary
    ) -> FolderClassification:
        """Parse OpenAI's structured response into a FolderClassification."""
        content = response.choices[0].message.content
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("Malformed JSON from OpenAI for folder classification: %s", exc)
            raise ValueError(f"Malformed JSON response from OpenAI: {exc}") from exc

        try:
            fc = FolderClassification.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid folder classification from OpenAI: {exc}"
            ) from exc

        # Ensure the entry_id matches
        if fc.entry_id != summary.entry_id:
            logger.warning(
                "OpenAI returned entry_id %d but expected %d, correcting",
                fc.entry_id,
                summary.entry_id,
            )
            fc = fc.model_copy(update={"entry_id": summary.entry_id})

        return fc

"""Property-based tests for classification submission round-trip (P8).

Property 8: For any unclassified Entries and valid classifications, submitting
updates each Entry with correct file_class/folder_purpose, confidence, and
classification_status = ai_classified.

Validates: Requirements 2.5
"""

from __future__ import annotations

import asyncio
import os
import random
import sqlite3
import tempfile
import uuid

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.classifier.batch import BatchClassifier, BatchResult
from src.classifier.prompts import FILE_CLASS_TAXONOMY, FOLDER_PURPOSE_TAXONOMY
from src.classifier.provider import ClassifierConfig
from src.db.models import (
    Entry,
    FileClassification,
    FileSummary,
    FolderClassification,
    FolderSummary,
)
from src.db.repository import Repository
from src.db.schema import init_db

# ---------------------------------------------------------------------------
# Valid taxonomy keys
# ---------------------------------------------------------------------------

VALID_FILE_CLASSES = list(FILE_CLASS_TAXONOMY.keys())
VALID_FOLDER_PURPOSES = list(FOLDER_PURPOSE_TAXONOMY.keys())

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_safe_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=30,
)

_extensions = st.sampled_from(
    [".txt", ".py", ".jpg", ".mp4", ".pdf", ".docx", ".zip", ".db", ".exe", ".log"]
)

_confidence = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@st.composite
def file_entries_strategy(draw):
    """Generate 1-10 file entry dicts."""
    n = draw(st.integers(min_value=1, max_value=10))
    entries = []
    for i in range(n):
        name = draw(_safe_text)
        ext = draw(_extensions)
        entries.append(
            {
                "entry_type": "file",
                "path": f"/{name}{ext}_{i}",
                "name": f"{name}{ext}",
                "extension": ext,
                "size_bytes": draw(st.integers(min_value=0, max_value=10**9)),
                "last_modified": "2024-06-15 12:00:00",
            }
        )
    return entries


@st.composite
def folder_entries_strategy(draw):
    """Generate 1-10 folder entry dicts."""
    n = draw(st.integers(min_value=1, max_value=10))
    entries = []
    for i in range(n):
        name = draw(_safe_text)
        entries.append(
            {
                "entry_type": "folder",
                "path": f"/{name}_{i}",
                "name": name,
                "extension": None,
                "size_bytes": draw(st.integers(min_value=0, max_value=10**9)),
                "last_modified": "2024-06-15 12:00:00",
            }
        )
    return entries


@st.composite
def mixed_entries_strategy(draw):
    """Generate 1-12 mixed file/folder entry dicts."""
    n = draw(st.integers(min_value=1, max_value=12))
    entries = []
    for i in range(n):
        etype = draw(st.sampled_from(["file", "folder"]))
        name = draw(_safe_text)
        ext = draw(_extensions) if etype == "file" else None
        path = f"/{name}{ext}_{i}" if ext else f"/{name}_{i}"
        entries.append(
            {
                "entry_type": etype,
                "path": path,
                "name": f"{name}{ext}" if ext else name,
                "extension": ext,
                "size_bytes": draw(st.integers(min_value=0, max_value=10**9)),
                "last_modified": "2024-06-15 12:00:00",
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Deterministic mock LLM provider that records what it returns
# ---------------------------------------------------------------------------


class DeterministicMockProvider:
    """Mock LLM provider that assigns predetermined classifications.

    Stores the exact classifications it returns so the test can verify
    the database matches them exactly (round-trip).
    """

    def __init__(self) -> None:
        self.file_classifications: dict[int, FileClassification] = {}
        self.folder_classifications: dict[int, FolderClassification] = {}

    async def classify_files(
        self, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        results = []
        for s in summaries:
            cls = FileClassification(
                entry_id=s.entry_id,
                file_class=random.choice(VALID_FILE_CLASSES),
                confidence=round(random.uniform(0.0, 1.0), 4),
                reasoning=f"Mock reasoning for file {s.entry_id}",
            )
            self.file_classifications[s.entry_id] = cls
            results.append(cls)
        return results

    async def classify_folders(
        self, summaries: list[FolderSummary]
    ) -> list[FolderClassification]:
        results = []
        for s in summaries:
            cls = FolderClassification(
                entry_id=s.entry_id,
                folder_purpose=random.choice(VALID_FOLDER_PURPOSES),
                confidence=round(random.uniform(0.0, 1.0), 4),
                reasoning=f"Mock reasoning for folder {s.entry_id}",
            )
            self.folder_classifications[s.entry_id] = cls
            results.append(cls)
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_db():
    """Create a temp database, return (conn, repo, path)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    repo = Repository(conn)
    return conn, repo, path


def _seed_entries(
    conn: sqlite3.Connection,
    repo: Repository,
    entry_dicts: list[dict],
) -> tuple[str, list[Entry]]:
    """Create a drive and insert entries, returning (drive_id, entries)."""
    drive = repo.create_drive(f"test-drive-{uuid.uuid4().hex[:8]}")
    bulk = [
        {
            "drive_id": drive.id,
            "path": ed["path"],
            "name": ed["name"],
            "entry_type": ed["entry_type"],
            "extension": ed.get("extension"),
            "size_bytes": ed["size_bytes"],
            "last_modified": ed["last_modified"],
        }
        for ed in entry_dicts
    ]
    repo.create_entries_bulk(bulk)
    return drive.id, repo.get_entries_by_drive(drive.id)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestFileClassificationSubmissionRoundTrip:
    """Submitted file classifications are persisted exactly in the database."""

    @given(entry_dicts=file_entries_strategy())
    @settings(max_examples=100)
    def test_file_class_and_confidence_persisted(self, entry_dicts):
        conn, repo, path = _create_db()
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            # All entries start unclassified
            for e in entries:
                assert e.classification_status == "unclassified"

            provider = DeterministicMockProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated = repo.get_entries_by_drive(drive_id)
            for entry in updated:
                submitted = provider.file_classifications[entry.id]
                assert entry.file_class == submitted.file_class
                assert entry.confidence == submitted.confidence
                assert entry.classification_status == "ai_classified"
        finally:
            conn.close()
            os.unlink(path)


class TestFolderClassificationSubmissionRoundTrip:
    """Submitted folder classifications are persisted exactly in the database."""

    @given(entry_dicts=folder_entries_strategy())
    @settings(max_examples=100)
    def test_folder_purpose_and_confidence_persisted(self, entry_dicts):
        conn, repo, path = _create_db()
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            for e in entries:
                assert e.classification_status == "unclassified"

            provider = DeterministicMockProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated = repo.get_entries_by_drive(drive_id)
            for entry in updated:
                submitted = provider.folder_classifications[entry.id]
                assert entry.folder_purpose == submitted.folder_purpose
                assert entry.confidence == submitted.confidence
                assert entry.classification_status == "ai_classified"
        finally:
            conn.close()
            os.unlink(path)


class TestMixedSubmissionRoundTrip:
    """Mixed file/folder batches persist the correct classification per type."""

    @given(entry_dicts=mixed_entries_strategy())
    @settings(max_examples=100)
    def test_mixed_entries_match_submitted_values(self, entry_dicts):
        conn, repo, path = _create_db()
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            provider = DeterministicMockProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated = repo.get_entries_by_drive(drive_id)
            for entry in updated:
                assert entry.classification_status == "ai_classified"

                if entry.entry_type == "file":
                    submitted = provider.file_classifications[entry.id]
                    assert entry.file_class == submitted.file_class
                    assert entry.confidence == submitted.confidence
                    assert entry.folder_purpose is None
                else:
                    submitted = provider.folder_classifications[entry.id]
                    assert entry.folder_purpose == submitted.folder_purpose
                    assert entry.confidence == submitted.confidence
                    assert entry.file_class is None
        finally:
            conn.close()
            os.unlink(path)


class TestSubmissionStatusTransition:
    """Every entry transitions from unclassified → ai_classified after submission."""

    @given(entry_dicts=mixed_entries_strategy())
    @settings(max_examples=100)
    def test_all_entries_become_ai_classified(self, entry_dicts):
        conn, repo, path = _create_db()
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            # Confirm starting state
            for e in entries:
                assert e.classification_status == "unclassified"
                assert e.file_class is None
                assert e.folder_purpose is None
                assert e.confidence is None

            provider = DeterministicMockProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            result = asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            files = [e for e in entries if e.entry_type == "file"]
            folders = [e for e in entries if e.entry_type == "folder"]
            assert result.files_classified == len(files)
            assert result.folders_classified == len(folders)
            assert result.files_failed == 0
            assert result.folders_failed == 0

            updated = repo.get_entries_by_drive(drive_id)
            for entry in updated:
                assert entry.classification_status == "ai_classified"
        finally:
            conn.close()
            os.unlink(path)


class TestSubmissionReasoningPersisted:
    """The reasoning string from the LLM is persisted in classification_reasoning."""

    @given(entry_dicts=mixed_entries_strategy())
    @settings(max_examples=100)
    def test_reasoning_stored(self, entry_dicts):
        conn, repo, path = _create_db()
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            provider = DeterministicMockProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated = repo.get_entries_by_drive(drive_id)
            for entry in updated:
                if entry.entry_type == "file":
                    submitted = provider.file_classifications[entry.id]
                else:
                    submitted = provider.folder_classifications[entry.id]
                assert entry.classification_reasoning == submitted.reasoning
        finally:
            conn.close()
            os.unlink(path)

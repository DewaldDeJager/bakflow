"""Property-based tests for classifier output validity (P7).

Property 7: With a mocked LLM, classifier returns exactly one classification
per input Entry; files get non-empty file_class, folders get valid
folder_purpose, all confidences in [0.0, 1.0].

Validates: Requirements 2.3, 2.4, 2.8
"""

from __future__ import annotations

import os
import random
import sqlite3
import tempfile
import uuid

import pytest
import pytest_asyncio
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.classifier.batch import BatchClassifier, BatchResult
from src.classifier.prompts import FOLDER_PURPOSE_TAXONOMY, FILE_CLASS_TAXONOMY
from src.classifier.provider import ClassifierConfig, LLMProvider
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
# Valid taxonomy keys (ground truth)
# ---------------------------------------------------------------------------

VALID_FOLDER_PURPOSES = list(FOLDER_PURPOSE_TAXONOMY.keys())
VALID_FILE_CLASSES = list(FILE_CLASS_TAXONOMY.keys())

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

_entry_type = st.sampled_from(["file", "folder"])


@st.composite
def entry_batch_strategy(draw):
    """Generate a list of 1-15 entry dicts with mixed file/folder types."""
    n = draw(st.integers(min_value=1, max_value=15))
    entries = []
    for i in range(n):
        etype = draw(_entry_type)
        name = draw(_safe_text)
        ext = draw(_extensions) if etype == "file" else None
        path = f"/{name}{ext}" if ext else f"/{name}"
        # Ensure unique paths
        path = f"{path}_{i}"
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
# Mock LLM provider
# ---------------------------------------------------------------------------


class MockLLMProvider:
    """A mock LLM provider that returns valid classifications for every input.

    For files: picks a random file_class from the taxonomy.
    For folders: picks a random folder_purpose from the taxonomy.
    Confidence is drawn uniformly from [0.0, 1.0].
    """

    async def classify_files(
        self, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        results = []
        for s in summaries:
            results.append(
                FileClassification(
                    entry_id=s.entry_id,
                    file_class=random.choice(VALID_FILE_CLASSES),
                    confidence=round(random.uniform(0.0, 1.0), 4),
                    reasoning="Mock classification",
                )
            )
        return results

    async def classify_folders(
        self, summaries: list[FolderSummary]
    ) -> list[FolderClassification]:
        results = []
        for s in summaries:
            results.append(
                FolderClassification(
                    entry_id=s.entry_id,
                    folder_purpose=random.choice(VALID_FOLDER_PURPOSES),
                    confidence=round(random.uniform(0.0, 1.0), 4),
                    reasoning="Mock classification",
                )
            )
        return results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_env():
    """Create a temporary database, returning (conn, repo, path)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    repo = Repository(conn)
    yield conn, repo, path
    conn.close()
    os.unlink(path)


def _seed_entries(
    conn: sqlite3.Connection,
    repo: Repository,
    entry_dicts: list[dict],
) -> tuple[str, list[Entry]]:
    """Create a drive and insert entries, returning (drive_id, entries)."""
    drive = repo.create_drive(f"test-drive-{uuid.uuid4().hex[:8]}")
    bulk = []
    for ed in entry_dicts:
        bulk.append(
            {
                "drive_id": drive.id,
                "path": ed["path"],
                "name": ed["name"],
                "entry_type": ed["entry_type"],
                "extension": ed.get("extension"),
                "size_bytes": ed["size_bytes"],
                "last_modified": ed["last_modified"],
            }
        )
    repo.create_entries_bulk(bulk)
    entries = repo.get_entries_by_drive(drive.id)
    return drive.id, entries


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestClassifierOutputCompleteness:
    """Every input entry gets exactly one classification result."""

    @given(entry_dicts=entry_batch_strategy())
    @settings(max_examples=100)
    def test_one_classification_per_entry(self, entry_dicts):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = init_db(path)
        repo = Repository(conn)
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            provider = MockLLMProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            import asyncio

            result = asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            files = [e for e in entries if e.entry_type == "file"]
            folders = [e for e in entries if e.entry_type == "folder"]

            # Every entry should be classified (no failures with mock)
            assert result.files_classified == len(files)
            assert result.folders_classified == len(folders)
            assert result.files_failed == 0
            assert result.folders_failed == 0

            # Total classified == total entries
            total_classified = result.files_classified + result.folders_classified
            assert total_classified == len(entries)
        finally:
            conn.close()
            os.unlink(path)


class TestFileClassificationsValid:
    """Every file entry gets a non-empty file_class from the taxonomy."""

    @given(entry_dicts=entry_batch_strategy())
    @settings(max_examples=100)
    def test_files_get_valid_file_class(self, entry_dicts):
        # Ensure we have at least one file
        assume(any(e["entry_type"] == "file" for e in entry_dicts))

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = init_db(path)
        repo = Repository(conn)
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            provider = MockLLMProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            import asyncio

            asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            # Re-fetch entries to see persisted classifications
            updated_entries = repo.get_entries_by_drive(drive_id)
            file_entries = [e for e in updated_entries if e.entry_type == "file"]

            for entry in file_entries:
                assert entry.classification_status == "ai_classified"
                assert entry.file_class is not None
                assert entry.file_class != ""
                assert entry.file_class in VALID_FILE_CLASSES
        finally:
            conn.close()
            os.unlink(path)


class TestFolderClassificationsValid:
    """Every folder entry gets a valid folder_purpose from the taxonomy."""

    @given(entry_dicts=entry_batch_strategy())
    @settings(max_examples=100)
    def test_folders_get_valid_folder_purpose(self, entry_dicts):
        # Ensure we have at least one folder
        assume(any(e["entry_type"] == "folder" for e in entry_dicts))

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = init_db(path)
        repo = Repository(conn)
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            provider = MockLLMProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            import asyncio

            asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            # Re-fetch entries to see persisted classifications
            updated_entries = repo.get_entries_by_drive(drive_id)
            folder_entries = [e for e in updated_entries if e.entry_type == "folder"]

            for entry in folder_entries:
                assert entry.classification_status == "ai_classified"
                assert entry.folder_purpose is not None
                assert entry.folder_purpose in VALID_FOLDER_PURPOSES
        finally:
            conn.close()
            os.unlink(path)


class TestConfidenceInRange:
    """All classified entries have confidence in [0.0, 1.0]."""

    @given(entry_dicts=entry_batch_strategy())
    @settings(max_examples=100)
    def test_confidence_bounded(self, entry_dicts):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = init_db(path)
        repo = Repository(conn)
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            provider = MockLLMProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            import asyncio

            asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated_entries = repo.get_entries_by_drive(drive_id)
            for entry in updated_entries:
                assert entry.classification_status == "ai_classified"
                assert entry.confidence is not None
                assert 0.0 <= entry.confidence <= 1.0
        finally:
            conn.close()
            os.unlink(path)


class TestClassificationStatusTransitioned:
    """All entries transition from unclassified to ai_classified after batch."""

    @given(entry_dicts=entry_batch_strategy())
    @settings(max_examples=100)
    def test_all_entries_become_ai_classified(self, entry_dicts):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = init_db(path)
        repo = Repository(conn)
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            # Verify all start as unclassified
            for e in entries:
                assert e.classification_status == "unclassified"

            provider = MockLLMProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            import asyncio

            asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated_entries = repo.get_entries_by_drive(drive_id)
            for entry in updated_entries:
                assert entry.classification_status == "ai_classified"
        finally:
            conn.close()
            os.unlink(path)


class TestMutualExclusivityOfClassificationFields:
    """Files get file_class (not folder_purpose), folders get folder_purpose (not file_class)."""

    @given(entry_dicts=entry_batch_strategy())
    @settings(max_examples=100)
    def test_classification_field_matches_entry_type(self, entry_dicts):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = init_db(path)
        repo = Repository(conn)
        try:
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            provider = MockLLMProvider()
            config = ClassifierConfig(confidence_threshold=0.7, batch_size=100)
            classifier = BatchClassifier(provider, repo, conn, config)

            import asyncio

            asyncio.run(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated_entries = repo.get_entries_by_drive(drive_id)
            for entry in updated_entries:
                if entry.entry_type == "file":
                    assert entry.file_class is not None
                    assert entry.folder_purpose is None
                elif entry.entry_type == "folder":
                    assert entry.folder_purpose is not None
                    assert entry.file_class is None
        finally:
            conn.close()
            os.unlink(path)

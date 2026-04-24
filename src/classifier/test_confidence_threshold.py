"""Property-based tests for confidence threshold (P9).

Property 9: For any classified Entry, confidence below the configured
threshold → priority_review = True; confidence at or above the threshold →
priority_review = False.

Validates: Requirements 2.7
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import uuid

from hypothesis import given, settings
from hypothesis import strategies as st

from src.classifier.batch import BatchClassifier
from src.classifier.prompts import FILE_CLASS_TAXONOMY, FOLDER_PURPOSE_TAXONOMY
from src.classifier.provider import ClassifierConfig
from src.db.models import (
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

# Confidence values in [0.0, 1.0] — no NaN
_confidence = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

# Threshold in (0.0, 1.0) exclusive so there's always room on both sides
_threshold = st.floats(
    min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
)


@st.composite
def mixed_entries_with_confidences(draw):
    """Generate 1-12 entry dicts each paired with a predetermined confidence."""
    n = draw(st.integers(min_value=1, max_value=12))
    entries = []
    for i in range(n):
        etype = draw(st.sampled_from(["file", "folder"]))
        name = draw(_safe_text)
        ext = draw(_extensions) if etype == "file" else None
        path = f"/{name}{ext}_{i}" if ext else f"/{name}_{i}"
        conf = draw(_confidence)
        entries.append(
            {
                "entry_type": etype,
                "path": path,
                "name": f"{name}{ext}" if ext else name,
                "extension": ext,
                "size_bytes": draw(st.integers(min_value=0, max_value=10**9)),
                "last_modified": "2024-06-15 12:00:00",
                "confidence": conf,
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Mock LLM provider with controlled confidence values
# ---------------------------------------------------------------------------


class ControlledConfidenceProvider:
    """Mock LLM provider that returns a predetermined confidence per entry_id.

    The caller sets ``confidence_map[entry_id] = value`` before classification
    so the test can verify the threshold logic with exact values.
    """

    def __init__(self) -> None:
        self.confidence_map: dict[int, float] = {}

    async def classify_files(
        self, summaries: list[FileSummary]
    ) -> list[FileClassification]:
        results = []
        for s in summaries:
            results.append(
                FileClassification(
                    entry_id=s.entry_id,
                    file_class=VALID_FILE_CLASSES[0],
                    confidence=self.confidence_map[s.entry_id],
                    reasoning="controlled mock",
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
                    folder_purpose=VALID_FOLDER_PURPOSES[0],
                    confidence=self.confidence_map[s.entry_id],
                    reasoning="controlled mock",
                )
            )
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


def _seed_entries(conn, repo, entry_dicts):
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


class TestConfidenceBelowThresholdSetsPriorityReview:
    """Entries with confidence < threshold get priority_review = True."""

    @given(data=mixed_entries_with_confidences(), threshold=_threshold)
    @settings(max_examples=100)
    def test_below_threshold_flagged(self, data, threshold):
        conn, repo, path = _create_db()
        try:
            drive_id, entries = _seed_entries(conn, repo, data)

            provider = ControlledConfidenceProvider()
            # Map each entry to its predetermined confidence
            for entry, ed in zip(entries, data):
                provider.confidence_map[entry.id] = ed["confidence"]

            config = ClassifierConfig(
                confidence_threshold=threshold, batch_size=100
            )
            classifier = BatchClassifier(provider, repo, conn, config)

            asyncio.get_event_loop().run_until_complete(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated = repo.get_entries_by_drive(drive_id)
            for entry in updated:
                conf = provider.confidence_map[entry.id]
                if conf < threshold:
                    assert entry.priority_review is True, (
                        f"Entry {entry.id} has confidence {conf} < threshold "
                        f"{threshold} but priority_review={entry.priority_review}"
                    )
        finally:
            conn.close()
            os.unlink(path)


class TestConfidenceAtOrAboveThresholdNoPriorityReview:
    """Entries with confidence >= threshold get priority_review = False."""

    @given(data=mixed_entries_with_confidences(), threshold=_threshold)
    @settings(max_examples=100)
    def test_at_or_above_threshold_not_flagged(self, data, threshold):
        conn, repo, path = _create_db()
        try:
            drive_id, entries = _seed_entries(conn, repo, data)

            provider = ControlledConfidenceProvider()
            for entry, ed in zip(entries, data):
                provider.confidence_map[entry.id] = ed["confidence"]

            config = ClassifierConfig(
                confidence_threshold=threshold, batch_size=100
            )
            classifier = BatchClassifier(provider, repo, conn, config)

            asyncio.get_event_loop().run_until_complete(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated = repo.get_entries_by_drive(drive_id)
            for entry in updated:
                conf = provider.confidence_map[entry.id]
                if conf >= threshold:
                    assert entry.priority_review is False, (
                        f"Entry {entry.id} has confidence {conf} >= threshold "
                        f"{threshold} but priority_review={entry.priority_review}"
                    )
        finally:
            conn.close()
            os.unlink(path)


class TestThresholdBoundaryExact:
    """Confidence exactly equal to threshold → priority_review = False."""

    @given(threshold=_threshold)
    @settings(max_examples=100)
    def test_exact_threshold_not_flagged(self, threshold):
        conn, repo, path = _create_db()
        try:
            # Create one file entry with confidence == threshold
            entry_dicts = [
                {
                    "entry_type": "file",
                    "path": "/boundary_test.txt",
                    "name": "boundary_test.txt",
                    "extension": ".txt",
                    "size_bytes": 100,
                    "last_modified": "2024-06-15 12:00:00",
                }
            ]
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            provider = ControlledConfidenceProvider()
            provider.confidence_map[entries[0].id] = threshold

            config = ClassifierConfig(
                confidence_threshold=threshold, batch_size=100
            )
            classifier = BatchClassifier(provider, repo, conn, config)

            asyncio.get_event_loop().run_until_complete(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated = repo.get_entries_by_drive(drive_id)
            assert len(updated) == 1
            assert updated[0].priority_review is False, (
                f"Confidence {threshold} == threshold {threshold} should NOT "
                f"be flagged for priority review"
            )
        finally:
            conn.close()
            os.unlink(path)


class TestThresholdAppliesToBothFileAndFolder:
    """The threshold logic applies identically to files and folders."""

    @given(
        threshold=_threshold,
        file_conf=_confidence,
        folder_conf=_confidence,
    )
    @settings(max_examples=100)
    def test_both_types_respect_threshold(self, threshold, file_conf, folder_conf):
        conn, repo, path = _create_db()
        try:
            entry_dicts = [
                {
                    "entry_type": "file",
                    "path": "/test_file.txt",
                    "name": "test_file.txt",
                    "extension": ".txt",
                    "size_bytes": 100,
                    "last_modified": "2024-06-15 12:00:00",
                },
                {
                    "entry_type": "folder",
                    "path": "/test_folder",
                    "name": "test_folder",
                    "extension": None,
                    "size_bytes": 0,
                    "last_modified": "2024-06-15 12:00:00",
                },
            ]
            drive_id, entries = _seed_entries(conn, repo, entry_dicts)

            file_entry = next(e for e in entries if e.entry_type == "file")
            folder_entry = next(e for e in entries if e.entry_type == "folder")

            provider = ControlledConfidenceProvider()
            provider.confidence_map[file_entry.id] = file_conf
            provider.confidence_map[folder_entry.id] = folder_conf

            config = ClassifierConfig(
                confidence_threshold=threshold, batch_size=100
            )
            classifier = BatchClassifier(provider, repo, conn, config)

            asyncio.get_event_loop().run_until_complete(
                classifier.classify_batch(drive_id, batch_size=100)
            )

            updated = repo.get_entries_by_drive(drive_id)
            for entry in updated:
                conf = provider.confidence_map[entry.id]
                expected_priority = conf < threshold
                assert entry.priority_review == expected_priority, (
                    f"{entry.entry_type} entry {entry.id}: confidence={conf}, "
                    f"threshold={threshold}, expected priority_review="
                    f"{expected_priority}, got {entry.priority_review}"
                )
        finally:
            conn.close()
            os.unlink(path)

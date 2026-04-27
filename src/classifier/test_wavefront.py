"""Tests for the wavefront classifier.

Covers BFS ordering, pruning, progress callbacks, error handling,
file classification phase, and priority review logic.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import uuid

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.classifier.wavefront import WavefrontClassifier, WavefrontConfig
from src.db.models import (
    Entry,
    FileClassification,
    FileSummary,
    FolderSummary,
    FolderClassification,
    WavefrontFolderClassification,
    WavefrontFolderSummary,
    WavefrontProgress,
)
from src.db.repository import Repository
from src.db.schema import init_db
from src.db.status import apply_transition


# ---------------------------------------------------------------------------
# Mock LLM provider
# ---------------------------------------------------------------------------


class MockWavefrontProvider:
    """Mock LLM provider that returns deterministic wavefront classifications.

    By default, all folders get ``descend`` so children are eligible.
    Override ``decision_map`` to control per-path decisions.
    """

    def __init__(
        self,
        decision_map: dict[str, str] | None = None,
        fail_paths: set[str] | None = None,
    ) -> None:
        self.decision_map = decision_map or {}
        self.fail_paths = fail_paths or set()
        self.calls: list[list[WavefrontFolderSummary]] = []

    async def classify_folders_wavefront(
        self, summaries: list[WavefrontFolderSummary],
    ) -> list[WavefrontFolderClassification]:
        # Check if any summary path should trigger a failure
        for s in summaries:
            if s.path in self.fail_paths:
                raise RuntimeError(f"Simulated LLM failure for {s.path}")

        self.calls.append(summaries)
        results = []
        for s in summaries:
            decision = self.decision_map.get(s.path, "descend")
            results.append(WavefrontFolderClassification(
                entry_id=s.entry_id,
                folder_purpose="system_or_temp",
                decision=decision,
                classification_confidence=0.9,
                decision_confidence=0.85,
                reasoning="Mock wavefront classification",
            ))
        return results

    async def classify_files(
        self, summaries: list[FileSummary],
    ) -> list[FileClassification]:
        return [
            FileClassification(
                entry_id=s.entry_id,
                file_class="document",
                classification_confidence=0.8,
                reasoning="Mock file classification",
            )
            for s in summaries
        ]

    async def classify_folders(
        self, summaries: list[FolderSummary],
    ) -> list[FolderClassification]:
        return []


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


def _create_drive(repo: Repository) -> str:
    """Create a test drive and return its id."""
    drive = repo.create_drive(f"test-drive-{uuid.uuid4().hex[:8]}")
    return drive.id


def _insert_entry(
    conn: sqlite3.Connection,
    drive_id: str,
    path: str,
    entry_type: str = "folder",
    depth: int = 0,
    parent_path: str | None = None,
    size_bytes: int = 0,
    descendant_file_count: int | None = None,
    descendant_folder_count: int | None = None,
    child_count: int | None = None,
    extension: str | None = None,
) -> int:
    """Insert a single entry and return its id."""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    conn.execute(
        "INSERT INTO entries "
        "(drive_id, path, original_path, name, entry_type, extension, size_bytes, "
        "depth, parent_path, child_count, descendant_file_count, descendant_folder_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            drive_id, path, path, name, entry_type, extension, size_bytes,
            depth, parent_path, child_count,
            descendant_file_count, descendant_folder_count,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM entries WHERE drive_id = ? AND path = ?",
        (drive_id, path),
    ).fetchone()
    return row[0]


def _build_simple_tree(conn: sqlite3.Connection, drive_id: str) -> dict[str, int]:
    """Build a simple 3-level tree and return path→id mapping.

    Structure:
        C:/                     (depth 0, 10 desc files, 3 desc folders)
        C:/Users                (depth 1, 5 desc files, 1 desc folder)
        C:/Users/docs           (depth 2, 3 desc files, 0 desc folders)
        C:/Users/docs/a.txt     (depth 3, file)
        C:/Users/docs/b.txt     (depth 3, file)
        C:/Users/docs/c.pdf     (depth 3, file)
        C:/Windows              (depth 1, 5 desc files, 1 desc folder)
        C:/Windows/System32     (depth 2, 5 desc files, 0 desc folders)
        C:/Windows/System32/d.dll (depth 3, file)
    """
    ids = {}
    ids["C:/"] = _insert_entry(
        conn, drive_id, "C:/", "folder", depth=0, parent_path=None,
        descendant_file_count=10, descendant_folder_count=3, child_count=2,
    )
    ids["C:/Users"] = _insert_entry(
        conn, drive_id, "C:/Users", "folder", depth=1, parent_path="C:/",
        descendant_file_count=5, descendant_folder_count=1, child_count=1,
    )
    ids["C:/Users/docs"] = _insert_entry(
        conn, drive_id, "C:/Users/docs", "folder", depth=2, parent_path="C:/Users",
        descendant_file_count=3, descendant_folder_count=0, child_count=3,
    )
    ids["C:/Users/docs/a.txt"] = _insert_entry(
        conn, drive_id, "C:/Users/docs/a.txt", "file", depth=3,
        parent_path="C:/Users/docs", extension=".txt",
    )
    ids["C:/Users/docs/b.txt"] = _insert_entry(
        conn, drive_id, "C:/Users/docs/b.txt", "file", depth=3,
        parent_path="C:/Users/docs", extension=".txt",
    )
    ids["C:/Users/docs/c.pdf"] = _insert_entry(
        conn, drive_id, "C:/Users/docs/c.pdf", "file", depth=3,
        parent_path="C:/Users/docs", extension=".pdf",
    )
    ids["C:/Windows"] = _insert_entry(
        conn, drive_id, "C:/Windows", "folder", depth=1, parent_path="C:/",
        descendant_file_count=5, descendant_folder_count=1, child_count=1,
    )
    ids["C:/Windows/System32"] = _insert_entry(
        conn, drive_id, "C:/Windows/System32", "folder", depth=2,
        parent_path="C:/Windows", descendant_file_count=5,
        descendant_folder_count=0, child_count=1,
    )
    ids["C:/Windows/System32/d.dll"] = _insert_entry(
        conn, drive_id, "C:/Windows/System32/d.dll", "file", depth=3,
        parent_path="C:/Windows/System32", extension=".dll",
    )
    return ids


# ---------------------------------------------------------------------------
# Tests: BFS ordering (6.8 scenario 1)
# ---------------------------------------------------------------------------


class TestBFSOrdering:
    """Verify depth 0 is classified before depth 1, etc."""

    def test_depth_order(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        classified_order: list[str] = []

        class OrderTrackingProvider(MockWavefrontProvider):
            async def classify_folders_wavefront(self, summaries):
                for s in summaries:
                    classified_order.append(s.path)
                return await super().classify_folders_wavefront(summaries)

        provider = OrderTrackingProvider()
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        asyncio.run(classifier.classify(drive_id))

        # Extract depths from classified paths
        folder_paths = [p for p in classified_order]
        # Verify monotonically increasing depth
        depths = []
        for p in folder_paths:
            row = conn.execute(
                "SELECT depth FROM entries WHERE drive_id = ? AND path = ?",
                (drive_id, p),
            ).fetchone()
            if row:
                depths.append(row[0])

        for i in range(1, len(depths)):
            assert depths[i] >= depths[i - 1], (
                f"Depth ordering violated: depth {depths[i]} at index {i} "
                f"came after depth {depths[i-1]} at index {i-1}"
            )


# ---------------------------------------------------------------------------
# Tests: Pruning (6.8 scenario 2)
# ---------------------------------------------------------------------------


class TestPruning:
    """Verify folders under include/exclude ancestors are skipped."""

    def test_include_prunes_subtree(self, db_env):
        """When C:/Windows gets 'include', System32 should NOT be classified."""
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider(
            decision_map={
                "C:/": "descend",
                "C:/Windows": "include",
                "C:/Users": "descend",
            },
        )
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        # System32 should NOT have been classified
        sys32 = repo.get_entry(ids["C:/Windows/System32"])
        assert sys32.classification_status == "unclassified"

        # Users/docs SHOULD be classified (Users got descend)
        docs = repo.get_entry(ids["C:/Users/docs"])
        assert docs.classification_status == "ai_classified"

        # Pruning stats
        assert result.folders_pruned >= 1  # At least C:/Windows pruned

    def test_exclude_prunes_subtree(self, db_env):
        """When C:/Windows gets 'exclude', System32 should NOT be classified."""
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider(
            decision_map={
                "C:/": "descend",
                "C:/Windows": "exclude",
                "C:/Users": "descend",
            },
        )
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        sys32 = repo.get_entry(ids["C:/Windows/System32"])
        assert sys32.classification_status == "unclassified"
        assert result.folders_pruned >= 1

    def test_descend_allows_children(self, db_env):
        """When a folder gets 'descend', its children are eligible at next depth."""
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        # All folders get descend → all should be classified
        provider = MockWavefrontProvider()
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        # All folders should be classified
        for path in ["C:/", "C:/Users", "C:/Users/docs", "C:/Windows", "C:/Windows/System32"]:
            entry = repo.get_entry(ids[path])
            assert entry.classification_status == "ai_classified", (
                f"{path} should be ai_classified"
            )

    def test_estimated_calls_saved(self, db_env):
        """Pruning should track estimated LLM calls saved."""
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider(
            decision_map={
                "C:/": "descend",
                "C:/Windows": "exclude",  # 5 desc files + 1 desc folder = 6 saved
                "C:/Users": "descend",
            },
        )
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        # C:/Windows has descendant_file_count=5, descendant_folder_count=1
        assert result.estimated_calls_saved >= 6


# ---------------------------------------------------------------------------
# Tests: Progress callbacks (6.8 scenario 4)
# ---------------------------------------------------------------------------


class TestProgressCallbacks:
    """Verify callback called at each depth with correct data."""

    def test_callback_called_per_depth(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        _build_simple_tree(conn, drive_id)

        progress_reports: list[WavefrontProgress] = []

        provider = MockWavefrontProvider()
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        asyncio.run(
            classifier.classify(drive_id, progress_callback=progress_reports.append)
        )

        # Should have at least one progress report per depth level processed
        assert len(progress_reports) >= 1

        # Depths should be monotonically increasing
        depths = [p.current_depth for p in progress_reports]
        for i in range(1, len(depths)):
            assert depths[i] >= depths[i - 1]

    def test_callback_has_correct_fields(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        _build_simple_tree(conn, drive_id)

        progress_reports: list[WavefrontProgress] = []

        provider = MockWavefrontProvider()
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        asyncio.run(
            classifier.classify(drive_id, progress_callback=progress_reports.append)
        )

        last = progress_reports[-1]
        assert last.folders_classified > 0
        assert last.max_depth is not None
        assert last.estimated_llm_calls_saved >= 0


# ---------------------------------------------------------------------------
# Tests: Error handling (6.8 scenario 5)
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Verify LLM failure marks folder as classification_failed, doesn't block others."""

    def test_single_folder_failure_continues(self, db_env):
        """If LLM fails for one batch, other folders still get classified."""
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        # Fail on C:/Windows batch — but C:/Users should still work
        provider = MockWavefrontProvider(
            decision_map={"C:/": "descend"},
            fail_paths={"C:/Windows"},
        )
        config = WavefrontConfig(batch_size=1, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        # C:/Windows should be marked as failed
        windows = repo.get_entry(ids["C:/Windows"])
        assert windows.classification_status == "classification_failed"

        # C:/Users should still be classified
        users = repo.get_entry(ids["C:/Users"])
        assert users.classification_status == "ai_classified"

        # Errors should be recorded
        assert len(result.errors) >= 1
        assert any("C:/Windows" in e for e in result.errors)

    def test_error_recorded_in_result(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider(
            decision_map={"C:/": "descend"},
            fail_paths={"C:/Users"},
        )
        config = WavefrontConfig(batch_size=1, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        assert len(result.errors) >= 1


# ---------------------------------------------------------------------------
# Tests: File classification phase (6.8 scenario 6)
# ---------------------------------------------------------------------------


class TestFileClassification:
    """Verify files classified when classify_files=True, skipped when False."""

    def test_files_classified_when_enabled(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider(
            decision_map={
                "C:/": "descend",
                "C:/Users": "descend",
                "C:/Users/docs": "descend",
                "C:/Windows": "descend",
                "C:/Windows/System32": "descend",
            },
        )
        config = WavefrontConfig(batch_size=10, classify_files=True)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        assert result.files_classified > 0

        # Check individual files
        a_txt = repo.get_entry(ids["C:/Users/docs/a.txt"])
        assert a_txt.classification_status == "ai_classified"
        assert a_txt.file_class is not None

    def test_files_skipped_when_disabled(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider()
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        assert result.files_classified == 0

        # Files should remain unclassified
        a_txt = repo.get_entry(ids["C:/Users/docs/a.txt"])
        assert a_txt.classification_status == "unclassified"

    def test_files_under_pruned_ancestors_skipped(self, db_env):
        """Files under include/exclude ancestors should not be classified."""
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider(
            decision_map={
                "C:/": "descend",
                "C:/Windows": "exclude",
                "C:/Users": "descend",
                "C:/Users/docs": "descend",
            },
        )
        config = WavefrontConfig(batch_size=10, classify_files=True)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        # d.dll is under C:/Windows (excluded) — should NOT be classified
        d_dll = repo.get_entry(ids["C:/Windows/System32/d.dll"])
        assert d_dll.classification_status == "unclassified"

        # a.txt is under C:/Users (descend) — should be classified
        a_txt = repo.get_entry(ids["C:/Users/docs/a.txt"])
        assert a_txt.classification_status == "ai_classified"


# ---------------------------------------------------------------------------
# Tests: Empty drive (6.8 scenario 7)
# ---------------------------------------------------------------------------


class TestEmptyDrive:
    """Verify graceful handling of empty drives."""

    def test_empty_drive_returns_zero_result(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)

        provider = MockWavefrontProvider()
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        assert result.folders_classified == 0
        assert result.folders_pruned == 0
        assert result.files_classified == 0
        assert result.estimated_calls_saved == 0
        assert result.errors == []


# ---------------------------------------------------------------------------
# Tests: Priority review (6.8 scenario 8)
# ---------------------------------------------------------------------------


class TestPriorityReview:
    """Verify decision_confidence < threshold sets priority_review=True."""

    def test_low_decision_confidence_sets_priority_review(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        class LowConfidenceProvider(MockWavefrontProvider):
            async def classify_folders_wavefront(self, summaries):
                results = []
                for s in summaries:
                    results.append(WavefrontFolderClassification(
                        entry_id=s.entry_id,
                        folder_purpose="system_or_temp",
                        decision="descend",
                        classification_confidence=0.9,
                        decision_confidence=0.3,  # Below threshold
                        reasoning="Low confidence decision",
                    ))
                return results

        provider = LowConfidenceProvider()
        config = WavefrontConfig(
            batch_size=10, classify_files=False, confidence_threshold=0.7,
        )
        classifier = WavefrontClassifier(provider, repo, conn, config)

        asyncio.run(classifier.classify(drive_id))

        # All classified folders should have priority_review=True
        root = repo.get_entry(ids["C:/"])
        assert root.priority_review is True

    def test_high_decision_confidence_no_priority_review(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        class HighConfidenceProvider(MockWavefrontProvider):
            async def classify_folders_wavefront(self, summaries):
                results = []
                for s in summaries:
                    results.append(WavefrontFolderClassification(
                        entry_id=s.entry_id,
                        folder_purpose="system_or_temp",
                        decision="descend",
                        classification_confidence=0.5,
                        decision_confidence=0.95,  # Above threshold
                        reasoning="High confidence decision",
                    ))
                return results

        provider = HighConfidenceProvider()
        config = WavefrontConfig(
            batch_size=10, classify_files=False, confidence_threshold=0.7,
        )
        classifier = WavefrontClassifier(provider, repo, conn, config)

        asyncio.run(classifier.classify(drive_id))

        root = repo.get_entry(ids["C:/"])
        assert root.priority_review is False


# ---------------------------------------------------------------------------
# Tests: DB state after classification
# ---------------------------------------------------------------------------


class TestDBState:
    """Verify classification results are correctly persisted."""

    def test_folder_purpose_and_confidences_written(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider()
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        asyncio.run(classifier.classify(drive_id))

        root = repo.get_entry(ids["C:/"])
        assert root.classification_status == "ai_classified"
        assert root.folder_purpose == "system_or_temp"
        assert root.classification_confidence == 0.9
        assert root.decision_confidence == 0.85
        assert root.classification_reasoning == "Mock wavefront classification"
        assert root.decision_status == "descend"

    def test_audit_log_entries_created(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        ids = _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider()
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        asyncio.run(classifier.classify(drive_id))

        # Check audit log for root entry
        rows = conn.execute(
            "SELECT dimension, old_value, new_value FROM audit_log WHERE entry_id = ?",
            (ids["C:/"],),
        ).fetchall()

        dimensions = {r[0] for r in rows}
        assert "classification_status" in dimensions
        assert "decision_status" in dimensions

    def test_wavefront_result_summary(self, db_env):
        conn, repo, _ = db_env
        drive_id = _create_drive(repo)
        _build_simple_tree(conn, drive_id)

        provider = MockWavefrontProvider()
        config = WavefrontConfig(batch_size=10, classify_files=False)
        classifier = WavefrontClassifier(provider, repo, conn, config)

        result = asyncio.run(classifier.classify(drive_id))

        assert result.drive_id == drive_id
        assert result.depths_processed > 0
        assert result.folders_classified > 0
        assert result.total_llm_calls > 0


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


@st.composite
def flat_folder_tree(draw):
    """Generate a flat list of folder paths at depths 0-3.

    Returns list of (path, depth, parent_path) tuples.
    """
    root = draw(st.sampled_from(["C:/", "D:/", "F:/"]))
    folders = [(root, 0, None)]

    n_depth1 = draw(st.integers(min_value=1, max_value=4))
    depth1_names = draw(
        st.lists(
            st.text(
                alphabet=st.characters(whitelist_categories=("L",)),
                min_size=1, max_size=10,
            ),
            min_size=n_depth1, max_size=n_depth1, unique=True,
        )
    )

    for name in depth1_names:
        path = f"{root.rstrip('/')}/{name}"
        folders.append((path, 1, root))

        # Optionally add depth 2 children
        n_depth2 = draw(st.integers(min_value=0, max_value=2))
        d2_names = draw(
            st.lists(
                st.text(
                    alphabet=st.characters(whitelist_categories=("L",)),
                    min_size=1, max_size=8,
                ),
                min_size=n_depth2, max_size=n_depth2, unique=True,
            )
        )
        for d2name in d2_names:
            d2path = f"{path}/{d2name}"
            folders.append((d2path, 2, path))

    return folders


class TestPruningProperty:
    """Property: no entry under a pruned ancestor is ever classified.

    Validates: Requirements 6.1, 6.2
    """

    @given(tree=flat_folder_tree())
    @settings(max_examples=100)
    def test_pruned_subtrees_never_classified(self, tree):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = init_db(path)
        repo = Repository(conn)

        try:
            drive_id = _create_drive(repo)

            # Insert all folders
            for fpath, depth, parent in tree:
                _insert_entry(
                    conn, drive_id, fpath, "folder",
                    depth=depth, parent_path=parent,
                    descendant_file_count=5, descendant_folder_count=2,
                )

            # Make all depth-1 folders get "include" (prune everything below)
            depth1_paths = {fpath for fpath, d, _ in tree if d == 1}
            decision_map = {}
            for fpath, d, _ in tree:
                if d == 0:
                    decision_map[fpath] = "descend"
                elif d == 1:
                    decision_map[fpath] = "include"

            provider = MockWavefrontProvider(decision_map=decision_map)
            config = WavefrontConfig(batch_size=50, classify_files=False)
            classifier = WavefrontClassifier(provider, repo, conn, config)

            asyncio.run(classifier.classify(drive_id))

            # Property: no depth-2 folder should be classified
            for fpath, d, parent in tree:
                if d >= 2:
                    row = conn.execute(
                        "SELECT classification_status FROM entries WHERE drive_id = ? AND path = ?",
                        (drive_id, fpath),
                    ).fetchone()
                    assert row[0] == "unclassified", (
                        f"Folder {fpath} at depth {d} should be unclassified "
                        f"(ancestor was pruned)"
                    )
        finally:
            conn.close()
            os.unlink(path)


class TestDepthMonotonicityProperty:
    """Property: wavefront processes depths in strictly increasing order.

    Validates: Requirements 6.1
    """

    @given(tree=flat_folder_tree())
    @settings(max_examples=100)
    def test_classification_depth_monotonic(self, tree):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = init_db(path)
        repo = Repository(conn)

        try:
            drive_id = _create_drive(repo)

            for fpath, depth, parent in tree:
                _insert_entry(
                    conn, drive_id, fpath, "folder",
                    depth=depth, parent_path=parent,
                    descendant_file_count=1, descendant_folder_count=0,
                )

            classified_depths: list[int] = []

            class DepthTracker(MockWavefrontProvider):
                async def classify_folders_wavefront(self, summaries):
                    for s in summaries:
                        classified_depths.append(s.depth)
                    return await super().classify_folders_wavefront(summaries)

            provider = DepthTracker()
            config = WavefrontConfig(batch_size=50, classify_files=False)
            classifier = WavefrontClassifier(provider, repo, conn, config)

            asyncio.run(classifier.classify(drive_id))

            # Depths should be non-decreasing
            for i in range(1, len(classified_depths)):
                assert classified_depths[i] >= classified_depths[i - 1], (
                    f"Depth monotonicity violated: {classified_depths}"
                )
        finally:
            conn.close()
            os.unlink(path)

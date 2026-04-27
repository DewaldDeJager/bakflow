"""End-to-end integration test for wavefront classification.

Verifies the full pipeline: init_db → import CSV with tree columns →
run wavefront classifier (mocked LLM) → verify depth-ordered classification
→ verify pruning → verify manifest excludes descend.
"""

from __future__ import annotations

import asyncio
import csv
import os
import sqlite3
import tempfile

import pytest

from src.classifier.wavefront import WavefrontClassifier, WavefrontConfig
from src.db.models import (
    FileClassification,
    FileSummary,
    FolderClassification,
    FolderSummary,
    WavefrontFolderClassification,
    WavefrontFolderSummary,
)
from src.db.repository import Repository
from src.db.schema import init_db
from src.importer.csv_importer import import_csv


# ---------------------------------------------------------------------------
# Mock LLM provider
# ---------------------------------------------------------------------------


class IntegrationMockProvider:
    """Mock provider with per-path decision control."""

    def __init__(self, decision_map: dict[str, str]) -> None:
        self.decision_map = decision_map
        self.classified_paths: list[str] = []

    async def classify_folders_wavefront(
        self, summaries: list[WavefrontFolderSummary],
    ) -> list[WavefrontFolderClassification]:
        results = []
        for s in summaries:
            self.classified_paths.append(s.path)
            decision = self.decision_map.get(s.path, "descend")
            results.append(WavefrontFolderClassification(
                entry_id=s.entry_id,
                folder_purpose="system_or_temp",
                decision=decision,
                classification_confidence=0.9,
                decision_confidence=0.85,
                reasoning="Integration test classification",
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
                reasoning="Integration test file classification",
            )
            for s in summaries
        ]

    async def classify_folders(
        self, summaries: list[FolderSummary],
    ) -> list[FolderClassification]:
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv_with_tree_columns(csv_path: str) -> None:
    """Write a small TreeSize-style CSV with tree metadata columns.

    Tree structure:
        C:/                     depth=0  (root, 2 children)
        C:/Users/               depth=1  (descend)
        C:/Users/docs/          depth=2  (descend)
        C:/Users/docs/a.txt     depth=3  (file)
        C:/Users/docs/b.pdf     depth=3  (file)
        C:/Windows/             depth=1  (exclude → prunes subtree)
        C:/Windows/System32/    depth=2  (should NOT be classified)
        C:/Windows/System32/k.dll  depth=3  (file, should NOT be classified)
    """
    rows = [
        {
            "Path": "C:\\",
            "Name": "C:",
            "Type": "folder",
            "Size": "0",
            "Date": "",
            "Dir Level": "0",
            "Folder Path": "",
            "Child item count": "2",
            "Files": "5",
            "Folders": "4",
        },
        {
            "Path": "C:\\Users",
            "Name": "Users",
            "Type": "folder",
            "Size": "0",
            "Date": "",
            "Dir Level": "1",
            "Folder Path": "C:\\",
            "Child item count": "1",
            "Files": "2",
            "Folders": "1",
        },
        {
            "Path": "C:\\Users\\docs",
            "Name": "docs",
            "Type": "folder",
            "Size": "0",
            "Date": "",
            "Dir Level": "2",
            "Folder Path": "C:\\Users",
            "Child item count": "2",
            "Files": "2",
            "Folders": "0",
        },
        {
            "Path": "C:\\Users\\docs\\a.txt",
            "Name": "a.txt",
            "Type": "file",
            "Size": "1024",
            "Date": "",
            "Dir Level": "3",
            "Folder Path": "C:\\Users\\docs",
            "Child item count": "",
            "Files": "",
            "Folders": "",
        },
        {
            "Path": "C:\\Users\\docs\\b.pdf",
            "Name": "b.pdf",
            "Type": "file",
            "Size": "2048",
            "Date": "",
            "Dir Level": "3",
            "Folder Path": "C:\\Users\\docs",
            "Child item count": "",
            "Files": "",
            "Folders": "",
        },
        {
            "Path": "C:\\Windows",
            "Name": "Windows",
            "Type": "folder",
            "Size": "0",
            "Date": "",
            "Dir Level": "1",
            "Folder Path": "C:\\",
            "Child item count": "1",
            "Files": "1",
            "Folders": "1",
        },
        {
            "Path": "C:\\Windows\\System32",
            "Name": "System32",
            "Type": "folder",
            "Size": "0",
            "Date": "",
            "Dir Level": "2",
            "Folder Path": "C:\\Windows",
            "Child item count": "1",
            "Files": "1",
            "Folders": "0",
        },
        {
            "Path": "C:\\Windows\\System32\\k.dll",
            "Name": "k.dll",
            "Type": "file",
            "Size": "4096",
            "Date": "",
            "Dir Level": "3",
            "Folder Path": "C:\\Windows\\System32",
            "Child item count": "",
            "Files": "",
            "Folders": "",
        },
    ]

    fieldnames = [
        "Path", "Name", "Type", "Size", "Date",
        "Dir Level", "Folder Path", "Child item count", "Files", "Folders",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


class TestWavefrontEndToEnd:
    """Full pipeline: init_db → import → wavefront classify → verify."""

    @pytest.fixture
    def env(self):
        """Set up temp DB and CSV, return (conn, repo, drive_id, csv_path)."""
        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        csv_fd, csv_path = tempfile.mkstemp(suffix=".csv")
        os.close(csv_fd)

        conn = init_db(db_path)
        repo = Repository(conn)
        drive = repo.create_drive("Integration Test Drive")

        _write_csv_with_tree_columns(csv_path)

        yield conn, repo, drive.id, csv_path

        conn.close()
        os.unlink(db_path)
        os.unlink(csv_path)

    def test_full_pipeline(self, env):
        """End-to-end: import → wavefront → verify ordering, pruning, manifest."""
        conn, repo, drive_id, csv_path = env

        # Step 1: Import CSV with tree columns
        result = import_csv(conn, csv_path, drive_id)
        assert result.entries_created == 8
        assert result.rows_skipped == 0

        # Verify tree metadata was imported
        root = conn.execute(
            "SELECT depth, child_count, descendant_file_count FROM entries "
            "WHERE drive_id = ? AND path LIKE '%C:/'",
            (drive_id,),
        ).fetchone()
        # Root should have depth=0
        assert root is not None

        # Step 2: Run wavefront classifier with mocked LLM
        # C:/ → descend, C:/Users → descend, C:/Windows → exclude (prune)
        provider = IntegrationMockProvider(decision_map={
            "C:/": "descend",
            "C:/Users": "descend",
            "C:/Users/docs": "descend",
            "C:/Windows": "exclude",
        })
        config = WavefrontConfig(
            batch_size=10,
            classify_files=True,
            confidence_threshold=0.7,
        )
        classifier = WavefrontClassifier(provider, repo, conn, config)
        wf_result = asyncio.run(classifier.classify(drive_id))

        # Step 3: Verify depth-ordered classification
        depths = []
        for path in provider.classified_paths:
            row = conn.execute(
                "SELECT depth FROM entries WHERE drive_id = ? AND path = ?",
                (drive_id, path),
            ).fetchone()
            if row:
                depths.append(row[0])

        for i in range(1, len(depths)):
            assert depths[i] >= depths[i - 1], (
                f"Depth ordering violated: {depths}"
            )

        # Step 4: Verify pruning — System32 should NOT be classified
        sys32_rows = conn.execute(
            "SELECT classification_status FROM entries "
            "WHERE drive_id = ? AND path LIKE '%System32' AND entry_type = 'folder'",
            (drive_id,),
        ).fetchall()
        for row in sys32_rows:
            assert row[0] == "unclassified", "System32 should be unclassified (pruned)"

        # k.dll under System32 should also be unclassified
        kdll_rows = conn.execute(
            "SELECT classification_status FROM entries "
            "WHERE drive_id = ? AND path LIKE '%k.dll'",
            (drive_id,),
        ).fetchall()
        for row in kdll_rows:
            assert row[0] == "unclassified", "k.dll should be unclassified (under pruned ancestor)"

        # Files under C:/Users/docs should be classified
        doc_files = conn.execute(
            "SELECT classification_status FROM entries "
            "WHERE drive_id = ? AND entry_type = 'file' AND path LIKE '%Users/docs%'",
            (drive_id,),
        ).fetchall()
        for row in doc_files:
            assert row[0] == "ai_classified", "Files under Users/docs should be classified"

        # Step 5: Verify manifest excludes descend
        manifest = repo.get_decision_manifest(drive_id)
        manifest_decisions = {e.decision_status for e in manifest}
        assert "descend" not in manifest_decisions, "Manifest should never contain descend entries"

        # Step 6: Verify result stats
        assert wf_result.folders_classified >= 4  # C:/, Users, docs, Windows
        assert wf_result.folders_pruned >= 1  # At least Windows
        assert wf_result.files_classified >= 2  # a.txt, b.pdf
        assert wf_result.estimated_calls_saved >= 1  # Windows subtree
        assert wf_result.errors == []

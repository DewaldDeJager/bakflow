"""Property-based tests for folder summary aggregation (P6).

Property 6: For any folder, summary returns correct file_count, total_size,
file type distribution, and subfolder list.

Validates: Requirements 2.2
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections import Counter

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository
from src.mcp_server.server import get_folder_summary
import src.mcp_server.server as server_mod


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_extension_strategy = st.sampled_from([".txt", ".py", ".jpg", ".pdf", ".doc", None])


@st.composite
def _folder_tree(draw):
    """Generate a folder path and a list of child entries under it.

    Returns (folder_path, children) where children is a list of dicts
    with keys: name, entry_type, extension, size_bytes, path.
    """
    folder_path = "/root/testfolder"
    num_files = draw(st.integers(min_value=0, max_value=20))
    num_subfolders = draw(st.integers(min_value=0, max_value=5))

    children = []
    for i in range(num_files):
        ext = draw(_extension_strategy)
        name = f"file_{i}{ext}" if ext else f"file_{i}"
        children.append({
            "name": name,
            "entry_type": "file",
            "extension": ext,
            "size_bytes": draw(st.integers(min_value=0, max_value=10**9)),
            "path": f"{folder_path}/{name}",
        })

    for i in range(num_subfolders):
        name = f"subfolder_{i}"
        children.append({
            "name": name,
            "entry_type": "folder",
            "extension": None,
            "size_bytes": 0,
            "path": f"{folder_path}/{name}",
        })

    return folder_path, children


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    repo = Repository(conn)
    server_mod._conn = conn
    server_mod._repo = repo
    return conn, repo, path


def _setup_folder(repo, conn, folder_path, children):
    """Create a drive, the folder entry, and its children. Return drive_id."""
    drive = repo.create_drive(label="test-drive")

    # Create the folder entry itself
    repo.create_entries_bulk([{
        "drive_id": drive.id,
        "path": folder_path,
        "name": folder_path.rsplit("/", 1)[-1],
        "entry_type": "folder",
        "size_bytes": 0,
    }])

    # Create children
    if children:
        repo.create_entries_bulk([
            {
                "drive_id": drive.id,
                "path": c["path"],
                "name": c["name"],
                "entry_type": c["entry_type"],
                "extension": c["extension"],
                "size_bytes": c["size_bytes"],
            }
            for c in children
        ])

    return drive.id


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# Feature: drive-backup-triage, Property 6: Folder summary aggregation correctness

class TestFolderSummaryAggregation:
    """P6: Folder summary aggregation correctness."""

    @given(data=_folder_tree())
    @settings(max_examples=100)
    def test_file_count_matches(self, data):
        """file_count equals the number of file children under the folder."""
        folder_path, children = data
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _setup_folder(repo, conn, folder_path, children)
            result = asyncio.run(
                get_folder_summary(drive_id=drive_id, path=folder_path)
            )
            assert "error" not in result
            expected_files = sum(1 for c in children if c["entry_type"] == "file")
            assert result["file_count"] == expected_files
        finally:
            conn.close()
            os.unlink(path)

    @given(data=_folder_tree())
    @settings(max_examples=100)
    def test_total_size_matches(self, data):
        """total_size equals the sum of file children's size_bytes."""
        folder_path, children = data
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _setup_folder(repo, conn, folder_path, children)
            result = asyncio.run(
                get_folder_summary(drive_id=drive_id, path=folder_path)
            )
            assert "error" not in result
            expected_size = sum(
                c["size_bytes"] for c in children if c["entry_type"] == "file"
            )
            assert result["total_size"] == expected_size
        finally:
            conn.close()
            os.unlink(path)

    @given(data=_folder_tree())
    @settings(max_examples=100)
    def test_file_type_distribution_matches(self, data):
        """file_type_distribution matches actual extension counts."""
        folder_path, children = data
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _setup_folder(repo, conn, folder_path, children)
            result = asyncio.run(
                get_folder_summary(drive_id=drive_id, path=folder_path)
            )
            assert "error" not in result

            expected: Counter[str] = Counter()
            for c in children:
                if c["entry_type"] == "file":
                    ext = c["extension"] or "(no extension)"
                    expected[ext] += 1

            assert result["file_type_distribution"] == dict(expected)
        finally:
            conn.close()
            os.unlink(path)

    @given(data=_folder_tree())
    @settings(max_examples=100)
    def test_subfolder_list_matches(self, data):
        """subfolder_names matches the direct child folders."""
        folder_path, children = data
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _setup_folder(repo, conn, folder_path, children)
            result = asyncio.run(
                get_folder_summary(drive_id=drive_id, path=folder_path)
            )
            assert "error" not in result

            expected_subfolders = sorted(
                c["name"] for c in children if c["entry_type"] == "folder"
            )
            assert sorted(result["subfolder_names"]) == expected_subfolders
        finally:
            conn.close()
            os.unlink(path)

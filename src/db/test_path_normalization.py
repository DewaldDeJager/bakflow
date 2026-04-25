"""Bug condition exploration tests for path separator normalization.

Property 1 (Bug Condition): Backslash Path Child Lookup Fails
For any parent path containing backslash separators and child entries stored
with backslash paths, ``get_child_entries`` SHALL return all child entries
whose normalized path starts with the normalized parent prefix.

On UNFIXED code these tests are EXPECTED TO FAIL — failure confirms the bug
exists.  After the fix is applied the same tests validate correct behavior.

**Validates: Requirements 1.1, 1.2, 1.4, 1.5, 2.1, 2.2, 2.4**
"""

from __future__ import annotations

import os
import tempfile

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Path segments: short alphanumeric strings (safe for both separators)
_segment = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
).filter(lambda s: s.strip())

# A list of 2-5 segments used to build a parent path
_parent_segments = st.lists(_segment, min_size=2, max_size=5)

# 1-3 extra segments appended to the parent to form child paths
_child_suffix_segments = st.lists(_segment, min_size=1, max_size=3)

# Number of children to generate per parent (1-4)
_num_children = st.integers(min_value=1, max_value=4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_db():
    """Create a temporary database, returning (conn, repo, path)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    return conn, Repository(conn), path


def _backslash_path(segments: list[str]) -> str:
    """Join segments with backslash separators (Windows-style)."""
    return "\\".join(segments)


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

class TestBugConditionBackslashChildLookup:
    """Property 1: get_child_entries returns children for backslash paths."""

    @given(
        parent_segs=_parent_segments,
        child_suffixes=st.lists(_child_suffix_segments, min_size=1, max_size=4),
    )
    @settings(max_examples=100)
    def test_get_child_entries_returns_children_for_backslash_paths(
        self, parent_segs, child_suffixes,
    ):
        """For any parent path built with backslash separators and children
        stored under that parent (also with backslashes), get_child_entries
        must return all children."""
        # Deduplicate child suffixes to avoid UNIQUE constraint violations
        seen = set()
        unique_suffixes = []
        for suffix in child_suffixes:
            key = tuple(suffix)
            if key not in seen:
                seen.add(key)
                unique_suffixes.append(suffix)
        assume(len(unique_suffixes) >= 1)

        parent_path = _backslash_path(parent_segs)

        # Build child paths: parent\child_seg1\child_seg2...
        child_paths = []
        for suffix in unique_suffixes:
            child_path = parent_path + "\\" + "\\".join(suffix)
            child_paths.append(child_path)

        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="test-drive")

            # Insert child entries with backslash paths
            entries = [
                {
                    "drive_id": drive.id,
                    "path": cp,
                    "name": cp.split("\\")[-1],
                    "entry_type": "file",
                    "size_bytes": 100,
                }
                for cp in child_paths
            ]
            repo.create_entries_bulk(entries)

            # Query using the backslash parent path
            children = repo.get_child_entries(drive.id, parent_path)

            assert len(children) == len(child_paths), (
                f"Expected {len(child_paths)} children for parent "
                f"'{parent_path}', got {len(children)}"
            )
        finally:
            conn.close()
            os.unlink(db_path)

    @given(
        parent_segs=_parent_segments,
        child_suffixes=st.lists(_child_suffix_segments, min_size=1, max_size=4),
    )
    @settings(max_examples=100)
    def test_get_child_entries_with_mixed_separator_query(
        self, parent_segs, child_suffixes,
    ):
        """When the query path uses mixed separators (forward + back) and
        children are stored with backslashes, get_child_entries must still
        return all children."""
        seen = set()
        unique_suffixes = []
        for suffix in child_suffixes:
            key = tuple(suffix)
            if key not in seen:
                seen.add(key)
                unique_suffixes.append(suffix)
        assume(len(unique_suffixes) >= 1)
        assume(len(parent_segs) >= 3)  # need at least 3 to mix separators

        # Build stored paths with backslashes
        parent_backslash = _backslash_path(parent_segs)
        child_paths = [
            parent_backslash + "\\" + "\\".join(suffix)
            for suffix in unique_suffixes
        ]

        # Build query path with mixed separators: first half forward, rest back
        mid = len(parent_segs) // 2
        mixed_parent = "/".join(parent_segs[:mid]) + "\\" + "\\".join(parent_segs[mid:])

        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="test-drive")
            entries = [
                {
                    "drive_id": drive.id,
                    "path": cp,
                    "name": cp.split("\\")[-1],
                    "entry_type": "file",
                    "size_bytes": 100,
                }
                for cp in child_paths
            ]
            repo.create_entries_bulk(entries)

            children = repo.get_child_entries(drive.id, mixed_parent)

            assert len(children) == len(child_paths), (
                f"Expected {len(child_paths)} children for mixed-separator "
                f"parent '{mixed_parent}', got {len(children)}"
            )
        finally:
            conn.close()
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Concrete regression cases
# ---------------------------------------------------------------------------

class TestBugConditionConcreteExamples:
    """Concrete examples from the bug report that demonstrate the defect."""

    def test_steam_library_backslash_path(self):
        """get_child_entries with backslash Steam path should return children."""
        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="steam-drive")
            repo.create_entries_bulk([
                {
                    "drive_id": drive.id,
                    "path": "F:\\SteamLibrary\\steamapps\\common\\game",
                    "name": "game",
                    "entry_type": "folder",
                    "size_bytes": 0,
                },
                {
                    "drive_id": drive.id,
                    "path": "F:\\SteamLibrary\\steamapps\\common\\game\\data.pak",
                    "name": "data.pak",
                    "entry_type": "file",
                    "extension": ".pak",
                    "size_bytes": 5000,
                },
            ])

            children = repo.get_child_entries(drive.id, "F:\\SteamLibrary\\steamapps")

            assert len(children) == 2, (
                f"Expected 2 children under 'F:\\SteamLibrary\\steamapps', "
                f"got {len(children)}"
            )
        finally:
            conn.close()
            os.unlink(db_path)

    def test_mixed_separator_query_path(self):
        """get_child_entries with mixed separators should return children."""
        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="mixed-drive")
            repo.create_entries_bulk([
                {
                    "drive_id": drive.id,
                    "path": "C:\\Users\\mixed\\path\\file.txt",
                    "name": "file.txt",
                    "entry_type": "file",
                    "extension": ".txt",
                    "size_bytes": 200,
                },
            ])

            children = repo.get_child_entries(drive.id, "C:/Users/mixed\\path")

            assert len(children) == 1, (
                f"Expected 1 child under 'C:/Users/mixed\\path', "
                f"got {len(children)}"
            )
        finally:
            conn.close()
            os.unlink(db_path)

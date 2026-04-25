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
    @settings(max_examples=100, deadline=None)
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
    @settings(max_examples=100, deadline=None)
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


# ===========================================================================
# Property 2 — Preservation: Forward-Slash Path Behavior Unchanged
# ===========================================================================
#
# These tests run on UNFIXED code and MUST PASS.  They capture the baseline
# behaviour for forward-slash paths that the bugfix must preserve.
#
# Validates: Requirements 3.1, 3.2, 3.4
# ===========================================================================


# ---------------------------------------------------------------------------
# Strategies (forward-slash only)
# ---------------------------------------------------------------------------

# A single path segment: 1-12 alphanumeric chars (no slashes, no backslashes)
_fwd_segment = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=12,
).filter(lambda s: s.strip() and "/" not in s and "\\" not in s)

# 2-5 segments for a parent path
_fwd_parent_segments = st.lists(_fwd_segment, min_size=2, max_size=5)

# 1-3 extra segments for child suffixes
_fwd_child_suffix = st.lists(_fwd_segment, min_size=1, max_size=3)


# ---------------------------------------------------------------------------
# Property-based preservation tests
# ---------------------------------------------------------------------------

class TestPreservationForwardSlashChildLookup:
    """Property 2: get_child_entries works correctly for forward-slash paths.

    This captures the baseline behaviour that MUST be preserved after the fix.
    """

    @given(
        parent_segs=_fwd_parent_segments,
        child_suffixes=st.lists(_fwd_child_suffix, min_size=1, max_size=4),
    )
    @settings(max_examples=100, deadline=None)
    def test_get_child_entries_returns_children_for_forward_slash_paths(
        self, parent_segs, child_suffixes,
    ):
        """For any forward-slash parent with children stored under it,
        get_child_entries must return all children."""
        # Deduplicate child suffixes
        seen: set[tuple[str, ...]] = set()
        unique_suffixes: list[list[str]] = []
        for suffix in child_suffixes:
            key = tuple(suffix)
            if key not in seen:
                seen.add(key)
                unique_suffixes.append(suffix)
        assume(len(unique_suffixes) >= 1)

        parent_path = "/".join(parent_segs)
        child_paths = [
            parent_path + "/" + "/".join(suffix)
            for suffix in unique_suffixes
        ]

        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="fwd-test")
            entries = [
                {
                    "drive_id": drive.id,
                    "path": cp,
                    "name": cp.rsplit("/", 1)[-1],
                    "entry_type": "file",
                    "size_bytes": 42,
                }
                for cp in child_paths
            ]
            repo.create_entries_bulk(entries)

            children = repo.get_child_entries(drive.id, parent_path)

            assert len(children) == len(child_paths), (
                f"Expected {len(child_paths)} children for forward-slash "
                f"parent '{parent_path}', got {len(children)}"
            )
        finally:
            conn.close()
            os.unlink(db_path)

    @given(parent_segs=_fwd_parent_segments)
    @settings(max_examples=100, deadline=None)
    def test_get_child_entries_returns_empty_for_no_children(self, parent_segs):
        """get_child_entries with a parent that has no children must return []."""
        parent_path = "/".join(parent_segs)

        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="empty-test")
            # Insert the parent itself but no children
            repo.create_entries_bulk([
                {
                    "drive_id": drive.id,
                    "path": parent_path,
                    "name": parent_segs[-1],
                    "entry_type": "folder",
                    "size_bytes": 0,
                }
            ])

            children = repo.get_child_entries(drive.id, parent_path)

            assert children == [], (
                f"Expected empty list for parent '{parent_path}' with no "
                f"children, got {len(children)} entries"
            )
        finally:
            conn.close()
            os.unlink(db_path)

    @given(
        parent_segs=_fwd_parent_segments,
        child_suffixes=st.lists(_fwd_child_suffix, min_size=1, max_size=4),
    )
    @settings(max_examples=100, deadline=None)
    def test_get_child_entries_with_trailing_slash(self, parent_segs, child_suffixes):
        """get_child_entries must handle a trailing slash on the parent path
        and still return the correct children (existing rstrip behaviour)."""
        seen: set[tuple[str, ...]] = set()
        unique_suffixes: list[list[str]] = []
        for suffix in child_suffixes:
            key = tuple(suffix)
            if key not in seen:
                seen.add(key)
                unique_suffixes.append(suffix)
        assume(len(unique_suffixes) >= 1)

        parent_path = "/".join(parent_segs)
        child_paths = [
            parent_path + "/" + "/".join(suffix)
            for suffix in unique_suffixes
        ]

        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="trailing-slash-test")
            entries = [
                {
                    "drive_id": drive.id,
                    "path": cp,
                    "name": cp.rsplit("/", 1)[-1],
                    "entry_type": "file",
                    "size_bytes": 10,
                }
                for cp in child_paths
            ]
            repo.create_entries_bulk(entries)

            # Query with trailing slash
            children = repo.get_child_entries(drive.id, parent_path + "/")

            assert len(children) == len(child_paths), (
                f"Expected {len(child_paths)} children for parent "
                f"'{parent_path}/' (trailing slash), got {len(children)}"
            )
        finally:
            conn.close()
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Preservation: create_entries_bulk with forward-slash paths
# ---------------------------------------------------------------------------

class TestPreservationCreateEntriesBulk:
    """Verify create_entries_bulk stores forward-slash paths correctly and
    all metadata is preserved."""

    @given(
        parent_segs=_fwd_parent_segments,
        child_suffixes=st.lists(_fwd_child_suffix, min_size=1, max_size=4),
    )
    @settings(max_examples=100, deadline=None)
    def test_bulk_insert_preserves_forward_slash_paths(
        self, parent_segs, child_suffixes,
    ):
        """Paths inserted via create_entries_bulk must be stored exactly as
        provided when they use forward slashes."""
        seen: set[tuple[str, ...]] = set()
        unique_suffixes: list[list[str]] = []
        for suffix in child_suffixes:
            key = tuple(suffix)
            if key not in seen:
                seen.add(key)
                unique_suffixes.append(suffix)
        assume(len(unique_suffixes) >= 1)

        parent_path = "/".join(parent_segs)
        child_paths = [
            parent_path + "/" + "/".join(suffix)
            for suffix in unique_suffixes
        ]

        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="bulk-test")
            entries = [
                {
                    "drive_id": drive.id,
                    "path": cp,
                    "name": cp.rsplit("/", 1)[-1],
                    "entry_type": "file",
                    "extension": ".dat",
                    "size_bytes": 999,
                }
                for cp in child_paths
            ]
            count = repo.create_entries_bulk(entries)

            assert count == len(child_paths)

            # Verify each entry is stored with the exact path
            stored = repo.get_entries_by_drive(drive.id)
            stored_paths = {e.path for e in stored}
            for cp in child_paths:
                assert cp in stored_paths, (
                    f"Path '{cp}' not found in stored entries"
                )

            # Verify metadata is preserved
            for entry in stored:
                assert entry.entry_type == "file"
                assert entry.extension == ".dat"
                assert entry.size_bytes == 999
        finally:
            conn.close()
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Preservation: CSV import with forward-slash paths
# ---------------------------------------------------------------------------

class TestPreservationCSVImport:
    """Verify CSV import of forward-slash paths stores them correctly with
    all metadata preserved."""

    def test_csv_import_forward_slash_paths_preserved(self, tmp_path):
        """CSV rows with forward-slash paths must be imported with the exact
        path and all metadata intact."""
        from src.importer.csv_importer import import_csv

        csv_file = tmp_path / "test_fwd.csv"
        csv_file.write_text(
            "Path,Name,Size,Last Modified,Type\n"
            "home/user/docs/report.pdf,report.pdf,1024,2024-01-15 10:30:00,file\n"
            "home/user/docs/notes.txt,notes.txt,256,2024-02-20 14:00:00,file\n"
            "home/user/photos,photos,0,,folder\n",
            encoding="utf-8",
        )

        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="csv-fwd-test")
            result = import_csv(conn, str(csv_file), drive.id)

            assert result.entries_created == 3
            assert result.rows_skipped == 0

            stored = repo.get_entries_by_drive(drive.id)
            paths = {e.path: e for e in stored}

            # Verify paths stored exactly
            assert "home/user/docs/report.pdf" in paths
            assert "home/user/docs/notes.txt" in paths
            assert "home/user/photos" in paths

            # Verify metadata
            report = paths["home/user/docs/report.pdf"]
            assert report.name == "report.pdf"
            assert report.size_bytes == 1024
            assert report.entry_type == "file"

            notes = paths["home/user/docs/notes.txt"]
            assert notes.name == "notes.txt"
            assert notes.size_bytes == 256

            photos = paths["home/user/photos"]
            assert photos.entry_type == "folder"
        finally:
            conn.close()
            os.unlink(db_path)

    def test_csv_import_forward_slash_child_lookup_works(self, tmp_path):
        """After CSV import of forward-slash paths, get_child_entries must
        return the correct children."""
        from src.importer.csv_importer import import_csv

        csv_file = tmp_path / "test_children.csv"
        csv_file.write_text(
            "Path,Name,Size,Type\n"
            "data/projects/alpha/readme.md,readme.md,512,file\n"
            "data/projects/alpha/src/main.py,main.py,1024,file\n"
            "data/projects/beta/readme.md,readme.md,256,file\n"
            "data/other/file.txt,file.txt,100,file\n",
            encoding="utf-8",
        )

        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="csv-child-test")
            import_csv(conn, str(csv_file), drive.id)

            # Children of data/projects/alpha
            alpha_children = repo.get_child_entries(drive.id, "data/projects/alpha")
            alpha_paths = {c.path for c in alpha_children}
            assert "data/projects/alpha/readme.md" in alpha_paths
            assert "data/projects/alpha/src/main.py" in alpha_paths
            assert len(alpha_children) == 2

            # Children of data/projects — should include alpha and beta subtrees
            projects_children = repo.get_child_entries(drive.id, "data/projects")
            assert len(projects_children) == 3  # alpha/readme, alpha/src/main, beta/readme

            # Children of data — should include everything
            data_children = repo.get_child_entries(drive.id, "data")
            assert len(data_children) == 4
        finally:
            conn.close()
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Concrete preservation regression cases
# ---------------------------------------------------------------------------

class TestPreservationConcreteExamples:
    """Concrete forward-slash examples that must continue to work."""

    def test_forward_slash_child_lookup(self):
        """get_child_entries with forward-slash paths returns correct children."""
        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="fwd-concrete")
            repo.create_entries_bulk([
                {
                    "drive_id": drive.id,
                    "path": "home/user/docs/report.pdf",
                    "name": "report.pdf",
                    "entry_type": "file",
                    "size_bytes": 1024,
                },
                {
                    "drive_id": drive.id,
                    "path": "home/user/docs/notes.txt",
                    "name": "notes.txt",
                    "entry_type": "file",
                    "size_bytes": 256,
                },
                {
                    "drive_id": drive.id,
                    "path": "home/user/photos/vacation.jpg",
                    "name": "vacation.jpg",
                    "entry_type": "file",
                    "size_bytes": 5000,
                },
            ])

            docs_children = repo.get_child_entries(drive.id, "home/user/docs")
            assert len(docs_children) == 2

            photos_children = repo.get_child_entries(drive.id, "home/user/photos")
            assert len(photos_children) == 1

            user_children = repo.get_child_entries(drive.id, "home/user")
            assert len(user_children) == 3
        finally:
            conn.close()
            os.unlink(db_path)

    def test_forward_slash_empty_parent(self):
        """get_child_entries with no children returns empty list."""
        conn, repo, db_path = _make_temp_db()
        try:
            drive = repo.create_drive(label="empty-concrete")
            repo.create_entries_bulk([
                {
                    "drive_id": drive.id,
                    "path": "home/user/empty",
                    "name": "empty",
                    "entry_type": "folder",
                    "size_bytes": 0,
                },
            ])

            children = repo.get_child_entries(drive.id, "home/user/empty")
            assert children == []
        finally:
            conn.close()
            os.unlink(db_path)

"""Tests for wavefront-related Repository methods.

Covers: get_folders_at_depth, get_pending_files, compute_tree_metadata,
get_max_depth, count_folders_at_depth, get_parent_entry, get_pruned_ancestor.
"""

import os
import sqlite3
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.db.repository import Repository
from src.db.schema import init_db


# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_repository.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    yield conn
    conn.close()
    os.unlink(path)


@pytest.fixture
def repo(db_conn):
    return Repository(db_conn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(drive_id, path, name, entry_type="folder", **kw):
    return {
        "drive_id": drive_id,
        "path": path,
        "name": name,
        "entry_type": entry_type,
        "extension": kw.get("extension"),
        "size_bytes": kw.get("size_bytes", 0),
        "last_modified": kw.get("last_modified", "2024-01-01 00:00:00"),
    }


def _build_tree(repo, db_conn, drive_id):
    """Build the reference tree from the task description and return drive_id.

    /root                    (folder, depth=0)
    ├── /root/docs           (folder, depth=1, decision=descend)
    │   ├── /root/docs/readme.txt    (file, depth=2)
    │   └── /root/docs/notes         (folder, depth=2)
    ├── /root/windows        (folder, depth=1, decision=exclude)  ← PRUNED
    │   ├── /root/windows/system32   (folder, depth=2)
    │   └── /root/windows/sys.dll    (file, depth=2)
    ├── /root/photos         (folder, depth=1, decision=include)  ← PRUNED
    │   └── /root/photos/vacation    (folder, depth=2)
    └── /root/projects       (folder, depth=1, unclassified)
        ├── /root/projects/app       (folder, depth=2)
        └── /root/projects/main.py   (file, depth=2)
    """
    entries = [
        _entry(drive_id, "/root", "root", "folder"),
        _entry(drive_id, "/root/docs", "docs", "folder"),
        _entry(drive_id, "/root/docs/readme.txt", "readme.txt", "file", extension=".txt"),
        _entry(drive_id, "/root/docs/notes", "notes", "folder"),
        _entry(drive_id, "/root/windows", "windows", "folder"),
        _entry(drive_id, "/root/windows/system32", "system32", "folder"),
        _entry(drive_id, "/root/windows/sys.dll", "sys.dll", "file", extension=".dll"),
        _entry(drive_id, "/root/photos", "photos", "folder"),
        _entry(drive_id, "/root/photos/vacation", "vacation", "folder"),
        _entry(drive_id, "/root/projects", "projects", "folder"),
        _entry(drive_id, "/root/projects/app", "app", "folder"),
        _entry(drive_id, "/root/projects/main.py", "main.py", "file", extension=".py"),
    ]
    repo.create_entries_bulk(entries)

    # Set depth and parent_path via SQL
    depth_map = {
        "/root": (0, None),
        "/root/docs": (1, "/root"),
        "/root/docs/readme.txt": (2, "/root/docs"),
        "/root/docs/notes": (2, "/root/docs"),
        "/root/windows": (1, "/root"),
        "/root/windows/system32": (2, "/root/windows"),
        "/root/windows/sys.dll": (2, "/root/windows"),
        "/root/photos": (1, "/root"),
        "/root/photos/vacation": (2, "/root/photos"),
        "/root/projects": (1, "/root"),
        "/root/projects/app": (2, "/root/projects"),
        "/root/projects/main.py": (2, "/root/projects"),
    }
    for path, (depth, parent) in depth_map.items():
        db_conn.execute(
            "UPDATE entries SET depth = ?, parent_path = ? WHERE drive_id = ? AND path = ?",
            (depth, parent, drive_id, path),
        )

    # Set descendant_file_count for ordering tests
    desc_counts = {
        "/root": 3,
        "/root/docs": 1,
        "/root/windows": 1,
        "/root/photos": 0,
        "/root/projects": 1,
    }
    for path, count in desc_counts.items():
        db_conn.execute(
            "UPDATE entries SET descendant_file_count = ? WHERE drive_id = ? AND path = ?",
            (count, drive_id, path),
        )

    # Set decision_status for pruned folders
    db_conn.execute(
        "UPDATE entries SET decision_status = 'descend' WHERE drive_id = ? AND path = ?",
        (drive_id, "/root/docs"),
    )
    db_conn.execute(
        "UPDATE entries SET decision_status = 'exclude' WHERE drive_id = ? AND path = ?",
        (drive_id, "/root/windows"),
    )
    db_conn.execute(
        "UPDATE entries SET decision_status = 'include' WHERE drive_id = ? AND path = ?",
        (drive_id, "/root/photos"),
    )

    db_conn.commit()



# =========================================================================
# get_folders_at_depth
# =========================================================================

class TestGetFoldersAtDepth:
    """Validates: Requirements 4.1"""

    def test_returns_only_folders(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        folders = repo.get_folders_at_depth(d.id, 2, exclude_pruned=False)
        for f in folders:
            assert f.entry_type == "folder"

    def test_returns_only_unclassified_or_needs_reclassification(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # Mark one depth-2 folder as ai_classified
        db_conn.execute(
            "UPDATE entries SET classification_status = 'ai_classified' "
            "WHERE drive_id = ? AND path = '/root/docs/notes'",
            (d.id,),
        )
        db_conn.commit()
        folders = repo.get_folders_at_depth(d.id, 2, exclude_pruned=False)
        for f in folders:
            assert f.classification_status in ("unclassified", "needs_reclassification")
        paths = {f.path for f in folders}
        assert "/root/docs/notes" not in paths

    def test_exclude_pruned_true_omits_include_exclude_subtrees(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        folders = repo.get_folders_at_depth(d.id, 2, exclude_pruned=True)
        paths = {f.path for f in folders}
        # Under /root/windows (exclude) — should be pruned
        assert "/root/windows/system32" not in paths
        # Under /root/photos (include) — should be pruned
        assert "/root/photos/vacation" not in paths
        # Under /root/docs (descend) — should NOT be pruned
        assert "/root/docs/notes" in paths
        # Under /root/projects (unclassified) — should NOT be pruned
        assert "/root/projects/app" in paths

    def test_exclude_pruned_false_returns_all_eligible(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        folders = repo.get_folders_at_depth(d.id, 2, exclude_pruned=False)
        paths = {f.path for f in folders}
        # All unclassified folders at depth 2 should appear
        assert "/root/windows/system32" in paths
        assert "/root/photos/vacation" in paths
        assert "/root/docs/notes" in paths
        assert "/root/projects/app" in paths

    def test_ordered_by_descendant_file_count_desc(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # Set specific descendant counts on depth-2 folders
        db_conn.execute(
            "UPDATE entries SET descendant_file_count = 50 WHERE drive_id = ? AND path = '/root/projects/app'",
            (d.id,),
        )
        db_conn.execute(
            "UPDATE entries SET descendant_file_count = 10 WHERE drive_id = ? AND path = '/root/docs/notes'",
            (d.id,),
        )
        db_conn.commit()
        folders = repo.get_folders_at_depth(d.id, 2, exclude_pruned=True)
        counts = [f.descendant_file_count for f in folders]
        # Should be descending (NULLs treated as 0 for ordering)
        non_null = [c or 0 for c in counts]
        assert non_null == sorted(non_null, reverse=True)

    def test_nulls_last_in_ordering(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # Set one folder with a count, leave others NULL
        db_conn.execute(
            "UPDATE entries SET descendant_file_count = 5 WHERE drive_id = ? AND path = '/root/projects/app'",
            (d.id,),
        )
        db_conn.execute(
            "UPDATE entries SET descendant_file_count = NULL WHERE drive_id = ? AND path = '/root/docs/notes'",
            (d.id,),
        )
        db_conn.commit()
        folders = repo.get_folders_at_depth(d.id, 2, exclude_pruned=True)
        # The folder with count=5 should come before the one with NULL
        assert folders[0].path == "/root/projects/app"

    def test_empty_when_no_eligible_folders(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # Depth 5 has no entries
        folders = repo.get_folders_at_depth(d.id, 5, exclude_pruned=True)
        assert folders == []

    def test_descend_ancestor_does_not_prune(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # /root/docs has decision=descend, so /root/docs/notes should still appear
        folders = repo.get_folders_at_depth(d.id, 2, exclude_pruned=True)
        paths = {f.path for f in folders}
        assert "/root/docs/notes" in paths



# =========================================================================
# get_pending_files
# =========================================================================

class TestGetPendingFiles:
    """Validates: Requirements 4.2"""

    def test_returns_only_files(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        files = repo.get_pending_files(d.id, batch_size=100)
        for f in files:
            assert f.entry_type == "file"

    def test_returns_only_unclassified(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        files = repo.get_pending_files(d.id, batch_size=100)
        for f in files:
            assert f.classification_status in ("unclassified", "needs_reclassification")

    def test_excludes_files_under_pruned_ancestors(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        files = repo.get_pending_files(d.id, batch_size=100)
        paths = {f.path for f in files}
        # /root/windows/sys.dll is under exclude — should be excluded
        assert "/root/windows/sys.dll" not in paths
        # /root/docs/readme.txt is under descend — should be included
        assert "/root/docs/readme.txt" in paths
        # /root/projects/main.py is under unclassified parent — should be included
        assert "/root/projects/main.py" in paths

    def test_respects_batch_size(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        files = repo.get_pending_files(d.id, batch_size=1)
        assert len(files) <= 1

    def test_files_under_descend_ancestor_returned(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        files = repo.get_pending_files(d.id, batch_size=100)
        paths = {f.path for f in files}
        # /root/docs has decision=descend, so its file children should still appear
        assert "/root/docs/readme.txt" in paths



# =========================================================================
# compute_tree_metadata
# =========================================================================

class TestComputeTreeMetadata:
    """Validates: Requirements 4.3"""

    def test_sets_depth_from_path_separators(self, repo, db_conn):
        d = repo.create_drive("D")
        entries = [
            _entry(d.id, "C:/", "C:", "folder"),
            _entry(d.id, "C:/child", "child", "folder"),
            _entry(d.id, "C:/child/grandchild", "grandchild", "folder"),
        ]
        repo.create_entries_bulk(entries)
        repo.compute_tree_metadata(d.id)
        rows = db_conn.execute(
            "SELECT path, depth FROM entries WHERE drive_id = ? ORDER BY path",
            (d.id,),
        ).fetchall()
        depth_map = {r[0]: r[1] for r in rows}
        assert depth_map["C:/"] == 0
        assert depth_map["C:/child"] == 1
        assert depth_map["C:/child/grandchild"] == 2

    def test_sets_parent_path_from_dirname(self, repo, db_conn):
        d = repo.create_drive("D")
        entries = [
            _entry(d.id, "C:/", "C:", "folder"),
            _entry(d.id, "C:/child", "child", "folder"),
            _entry(d.id, "C:/child/file.txt", "file.txt", "file", extension=".txt"),
        ]
        repo.create_entries_bulk(entries)
        repo.compute_tree_metadata(d.id)
        rows = db_conn.execute(
            "SELECT path, parent_path FROM entries WHERE drive_id = ? ORDER BY path",
            (d.id,),
        ).fetchall()
        parent_map = {r[0]: r[1] for r in rows}
        assert parent_map["C:/"] is None  # root has no parent
        assert parent_map["C:/child"] == "C:/"
        assert parent_map["C:/child/file.txt"] == "C:/child"

    def test_sets_child_count_for_folders(self, repo, db_conn):
        d = repo.create_drive("D")
        entries = [
            _entry(d.id, "C:/", "C:", "folder"),
            _entry(d.id, "C:/a", "a", "folder"),
            _entry(d.id, "C:/b", "b", "folder"),
            _entry(d.id, "C:/c.txt", "c.txt", "file", extension=".txt"),
        ]
        repo.create_entries_bulk(entries)
        repo.compute_tree_metadata(d.id)
        row = db_conn.execute(
            "SELECT child_count FROM entries WHERE drive_id = ? AND path = 'C:/'",
            (d.id,),
        ).fetchone()
        assert row[0] == 3  # a, b, c.txt

    def test_sets_descendant_file_count(self, repo, db_conn):
        d = repo.create_drive("D")
        entries = [
            _entry(d.id, "C:/", "C:", "folder"),
            _entry(d.id, "C:/sub", "sub", "folder"),
            _entry(d.id, "C:/a.txt", "a.txt", "file", extension=".txt"),
            _entry(d.id, "C:/sub/b.txt", "b.txt", "file", extension=".txt"),
        ]
        repo.create_entries_bulk(entries)
        repo.compute_tree_metadata(d.id)
        row = db_conn.execute(
            "SELECT descendant_file_count FROM entries WHERE drive_id = ? AND path = 'C:/'",
            (d.id,),
        ).fetchone()
        assert row[0] == 2  # a.txt + sub/b.txt

    def test_sets_descendant_folder_count(self, repo, db_conn):
        d = repo.create_drive("D")
        entries = [
            _entry(d.id, "C:/", "C:", "folder"),
            _entry(d.id, "C:/sub", "sub", "folder"),
            _entry(d.id, "C:/sub/deep", "deep", "folder"),
        ]
        repo.create_entries_bulk(entries)
        repo.compute_tree_metadata(d.id)
        row = db_conn.execute(
            "SELECT descendant_folder_count FROM entries WHERE drive_id = ? AND path = 'C:/'",
            (d.id,),
        ).fetchone()
        assert row[0] == 2  # sub + sub/deep

    def test_does_not_overwrite_existing_values(self, repo, db_conn):
        d = repo.create_drive("D")
        entries = [
            _entry(d.id, "C:/", "C:", "folder"),
            _entry(d.id, "C:/child", "child", "folder"),
        ]
        repo.create_entries_bulk(entries)
        # Pre-set depth on C:/
        db_conn.execute(
            "UPDATE entries SET depth = 99 WHERE drive_id = ? AND path = 'C:/'",
            (d.id,),
        )
        db_conn.commit()
        repo.compute_tree_metadata(d.id)
        row = db_conn.execute(
            "SELECT depth FROM entries WHERE drive_id = ? AND path = 'C:/'",
            (d.id,),
        ).fetchone()
        assert row[0] == 99  # preserved, not overwritten

    def test_returns_count_of_updated_entries(self, repo, db_conn):
        d = repo.create_drive("D")
        entries = [
            _entry(d.id, "C:/", "C:", "folder"),
            _entry(d.id, "C:/a.txt", "a.txt", "file", extension=".txt"),
        ]
        repo.create_entries_bulk(entries)
        count = repo.compute_tree_metadata(d.id)
        assert count > 0

    @given(
        num_children=st.integers(min_value=0, max_value=5),
        num_files=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=100)
    def test_property_depth_equals_separator_count(self, num_children, num_files):
        """**Validates: Requirements 4.3** — depth = number of '/' separators after stripping trailing slash."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = init_db(path)
        try:
            r = Repository(conn)
            d = r.create_drive("D")
            entries = [_entry(d.id, "C:/", "C:", "folder")]
            for i in range(num_children):
                entries.append(_entry(d.id, f"C:/child{i}", f"child{i}", "folder"))
            for i in range(num_files):
                entries.append(
                    _entry(d.id, f"C:/file{i}.txt", f"file{i}.txt", "file", extension=".txt")
                )
            r.create_entries_bulk(entries)
            r.compute_tree_metadata(d.id)

            rows = conn.execute(
                "SELECT path, depth FROM entries WHERE drive_id = ?", (d.id,)
            ).fetchall()
            for entry_path, depth in rows:
                expected = entry_path.rstrip("/").count("/")
                assert depth == expected, f"path={entry_path}, depth={depth}, expected={expected}"
        finally:
            conn.close()
            os.unlink(path)



# =========================================================================
# get_max_depth
# =========================================================================

class TestGetMaxDepth:
    """Validates: Requirements 4.4"""

    def test_returns_highest_depth(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        assert repo.get_max_depth(d.id) == 2

    def test_returns_zero_when_no_entries(self, repo):
        d = repo.create_drive("D")
        assert repo.get_max_depth(d.id) == 0

    def test_returns_zero_when_all_depths_null(self, repo, db_conn):
        d = repo.create_drive("D")
        repo.create_entries_bulk([_entry(d.id, "/a", "a", "folder")])
        # depth is NULL by default
        assert repo.get_max_depth(d.id) == 0


# =========================================================================
# count_folders_at_depth
# =========================================================================

class TestCountFoldersAtDepth:
    """Validates: Requirements 4.4"""

    def test_accurate_count(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # Depth 1 has: docs, windows, photos, projects = 4 folders
        assert repo.count_folders_at_depth(d.id, 1) == 4

    def test_excludes_files(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # Depth 2 has folders: notes, system32, vacation, app = 4
        # Files at depth 2: readme.txt, sys.dll, main.py — should not be counted
        assert repo.count_folders_at_depth(d.id, 2) == 4

    def test_zero_at_empty_depth(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        assert repo.count_folders_at_depth(d.id, 10) == 0


# =========================================================================
# get_parent_entry
# =========================================================================

class TestGetParentEntry:
    """Validates: Requirements 4.4"""

    def test_returns_matching_folder(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        parent = repo.get_parent_entry(d.id, "/root/docs")
        assert parent is not None
        assert parent.path == "/root/docs"
        assert parent.entry_type == "folder"

    def test_returns_none_when_not_found(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        assert repo.get_parent_entry(d.id, "/nonexistent") is None

    def test_does_not_return_files(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # /root/docs/readme.txt is a file, not a folder
        result = repo.get_parent_entry(d.id, "/root/docs/readme.txt")
        assert result is None


# =========================================================================
# get_pruned_ancestor
# =========================================================================

class TestGetPrunedAncestor:
    """Validates: Requirements 4.4"""

    def test_returns_nearest_include_ancestor(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # /root/photos has decision=include
        ancestor = repo.get_pruned_ancestor(d.id, "/root/photos/vacation")
        assert ancestor is not None
        assert ancestor.path == "/root/photos"
        assert ancestor.decision_status == "include"

    def test_returns_nearest_exclude_ancestor(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        ancestor = repo.get_pruned_ancestor(d.id, "/root/windows/system32")
        assert ancestor is not None
        assert ancestor.path == "/root/windows"
        assert ancestor.decision_status == "exclude"

    def test_returns_none_when_no_pruned_ancestor(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # /root/projects has no pruned ancestor
        assert repo.get_pruned_ancestor(d.id, "/root/projects/app") is None

    def test_descend_is_not_pruned(self, repo, db_conn):
        d = repo.create_drive("D")
        _build_tree(repo, db_conn, d.id)
        # /root/docs has decision=descend — should NOT count as pruned
        assert repo.get_pruned_ancestor(d.id, "/root/docs/notes") is None

    def test_returns_nearest_when_multiple_ancestors(self, repo, db_conn):
        """When multiple ancestors have include/exclude, return the nearest (longest path)."""
        d = repo.create_drive("D")
        entries = [
            _entry(d.id, "/a", "a", "folder"),
            _entry(d.id, "/a/b", "b", "folder"),
            _entry(d.id, "/a/b/c", "c", "folder"),
            _entry(d.id, "/a/b/c/d.txt", "d.txt", "file", extension=".txt"),
        ]
        repo.create_entries_bulk(entries)
        db_conn.execute(
            "UPDATE entries SET decision_status = 'exclude' WHERE drive_id = ? AND path = '/a'",
            (d.id,),
        )
        db_conn.execute(
            "UPDATE entries SET decision_status = 'include' WHERE drive_id = ? AND path = '/a/b'",
            (d.id,),
        )
        db_conn.commit()
        ancestor = repo.get_pruned_ancestor(d.id, "/a/b/c/d.txt")
        assert ancestor is not None
        # Nearest ancestor is /a/b (longer path)
        assert ancestor.path == "/a/b"

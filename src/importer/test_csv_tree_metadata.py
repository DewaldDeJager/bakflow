"""Tests for CSV importer tree metadata handling (Task 5).

Tests cover:
- Importing CSV with all TreeSize tree columns (Dir Level, Folder Path, etc.)
- Importing CSV without tree columns (verify derivation and NULL counts)
- Integer parsing with space-separated thousands

Validates: Requirements 5.1, 5.2
"""

from __future__ import annotations

import csv
import os
import tempfile

from src.db.schema import init_db
from src.db.repository import Repository
from src.importer.csv_importer import (
    import_csv,
    ColumnMapping,
    _parse_tree_int,
    _derive_depth,
    _derive_parent_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_db():
    """Create a temporary database, returning (conn, repo, path)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    return conn, Repository(conn), path


def _write_csv(rows: list[dict], fieldnames: list[str]) -> str:
    """Write rows to a temporary CSV file and return the path."""
    fd, csv_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


# ---------------------------------------------------------------------------
# Tests: CSV with all tree columns present
# ---------------------------------------------------------------------------

class TestTreeColumnsPresent:
    """When CSV has all TreeSize tree columns, values are parsed and stored."""

    def test_depth_populated_from_dir_level(self):
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test")
            rows = [
                {"Path": "C:\\", "Size": "0", "Dir Level": "0",
                 "Folder Path": "", "Child item count": "5",
                 "Files": "10", "Folders": "3"},
                {"Path": "C:\\Users\\", "Size": "0", "Dir Level": "1",
                 "Folder Path": "C:\\", "Child item count": "2",
                 "Files": "100", "Folders": "8"},
            ]
            fieldnames = ["Path", "Size", "Dir Level", "Folder Path",
                          "Child item count", "Files", "Folders"]
            csv_path = _write_csv(rows, fieldnames)

            import_csv(conn, csv_path, drive.id)

            entries = {e.name: e for e in repo.get_entries_by_drive(drive.id)}
            assert entries["C:"].depth == 0
            assert entries["Users"].depth == 1
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    def test_parent_path_populated_from_folder_path(self):
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test")
            rows = [
                {"Path": "C:\\", "Size": "0", "Dir Level": "0",
                 "Folder Path": "", "Child item count": "5",
                 "Files": "10", "Folders": "3"},
                {"Path": "C:\\Users\\", "Size": "0", "Dir Level": "1",
                 "Folder Path": "C:\\", "Child item count": "2",
                 "Files": "100", "Folders": "8"},
            ]
            fieldnames = ["Path", "Size", "Dir Level", "Folder Path",
                          "Child item count", "Files", "Folders"]
            csv_path = _write_csv(rows, fieldnames)

            import_csv(conn, csv_path, drive.id)

            entries = {e.name: e for e in repo.get_entries_by_drive(drive.id)}
            assert entries["C:"].parent_path is None  # empty string → None
            assert entries["Users"].parent_path == "C:/"
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    def test_count_columns_populated(self):
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test")
            rows = [
                {"Path": "C:\\Users\\", "Size": "0", "Dir Level": "1",
                 "Folder Path": "C:\\", "Child item count": "12",
                 "Files": "350", "Folders": "25"},
            ]
            fieldnames = ["Path", "Size", "Dir Level", "Folder Path",
                          "Child item count", "Files", "Folders"]
            csv_path = _write_csv(rows, fieldnames)

            import_csv(conn, csv_path, drive.id)

            entry = repo.get_entries_by_drive(drive.id)[0]
            assert entry.child_count == 12
            assert entry.descendant_file_count == 350
            assert entry.descendant_folder_count == 25
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    def test_space_separated_thousands_in_tree_columns(self):
        """TreeSize uses space-separated thousands like '85 218'."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test")
            rows = [
                {"Path": "C:\\Data\\", "Size": "0", "Dir Level": "1",
                 "Folder Path": "C:\\", "Child item count": "1 234",
                 "Files": "85 218", "Folders": "4 567"},
            ]
            fieldnames = ["Path", "Size", "Dir Level", "Folder Path",
                          "Child item count", "Files", "Folders"]
            csv_path = _write_csv(rows, fieldnames)

            import_csv(conn, csv_path, drive.id)

            entry = repo.get_entries_by_drive(drive.id)[0]
            assert entry.child_count == 1234
            assert entry.descendant_file_count == 85218
            assert entry.descendant_folder_count == 4567
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)


# ---------------------------------------------------------------------------
# Tests: CSV without tree columns (derivation)
# ---------------------------------------------------------------------------

class TestTreeColumnsMissing:
    """When CSV lacks tree columns, depth and parent_path are derived."""

    def test_depth_derived_from_path_separators(self):
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test")
            rows = [
                {"Path": "C:\\", "Size": "0"},
                {"Path": "C:\\Users\\", "Size": "0"},
                {"Path": "C:\\Users\\John\\Documents\\", "Size": "0"},
            ]
            fieldnames = ["Path", "Size"]
            csv_path = _write_csv(rows, fieldnames)

            import_csv(conn, csv_path, drive.id)

            entries = {e.path: e for e in repo.get_entries_by_drive(drive.id)}
            assert entries["C:/"].depth == 0
            assert entries["C:/Users"].depth == 1
            assert entries["C:/Users/John/Documents"].depth == 3
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    def test_parent_path_derived_from_dirname(self):
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test")
            rows = [
                {"Path": "C:\\", "Size": "0"},
                {"Path": "C:\\Users\\", "Size": "0"},
                {"Path": "C:\\Users\\John\\file.txt", "Size": "100"},
            ]
            fieldnames = ["Path", "Size"]
            csv_path = _write_csv(rows, fieldnames)

            import_csv(conn, csv_path, drive.id)

            entries = {e.path: e for e in repo.get_entries_by_drive(drive.id)}
            # Root has no parent
            assert entries["C:/"].parent_path is None
            # Users parent is C:/
            assert entries["C:/Users"].parent_path == "C:/"
            # file.txt parent is C:/Users/John
            assert entries["C:/Users/John/file.txt"].parent_path == "C:/Users/John"
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    def test_count_columns_are_null_when_absent(self):
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test")
            rows = [
                {"Path": "C:\\Users\\", "Size": "0"},
            ]
            fieldnames = ["Path", "Size"]
            csv_path = _write_csv(rows, fieldnames)

            import_csv(conn, csv_path, drive.id)

            entry = repo.get_entries_by_drive(drive.id)[0]
            assert entry.child_count is None
            assert entry.descendant_file_count is None
            assert entry.descendant_folder_count is None
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)


# ---------------------------------------------------------------------------
# Tests: _parse_tree_int helper
# ---------------------------------------------------------------------------

class TestParseTreeInt:
    """Unit tests for the _parse_tree_int helper."""

    def test_plain_integer(self):
        assert _parse_tree_int("42") == 42

    def test_space_separated_thousands(self):
        assert _parse_tree_int("85 218") == 85218

    def test_large_space_separated(self):
        assert _parse_tree_int("1 234 567") == 1234567

    def test_empty_string_returns_none(self):
        assert _parse_tree_int("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_tree_int("   ") is None

    def test_zero(self):
        assert _parse_tree_int("0") == 0

    def test_thin_space_separator(self):
        assert _parse_tree_int("85\u202f218") == 85218


# ---------------------------------------------------------------------------
# Tests: derivation helpers
# ---------------------------------------------------------------------------

class TestDeriveDepth:
    def test_root_drive(self):
        assert _derive_depth("C:/") == 0

    def test_depth_one(self):
        assert _derive_depth("C:/Users") == 1

    def test_depth_one_trailing_slash(self):
        assert _derive_depth("C:/Users/") == 1

    def test_depth_three(self):
        assert _derive_depth("C:/Users/John/Documents") == 3

    def test_depth_three_trailing_slash(self):
        assert _derive_depth("C:/Users/John/Documents/") == 3

    def test_file_path(self):
        assert _derive_depth("C:/Users/John/file.txt") == 3


class TestDeriveParentPath:
    def test_root_returns_none(self):
        assert _derive_parent_path("C:/", 0) is None

    def test_depth_one_parent(self):
        assert _derive_parent_path("C:/Users", 1) == "C:/"

    def test_deeper_parent(self):
        assert _derive_parent_path("C:/Users/John/Documents", 3) == "C:/Users/John"

    def test_file_parent(self):
        assert _derive_parent_path("C:/Users/John/file.txt", 3) == "C:/Users/John"

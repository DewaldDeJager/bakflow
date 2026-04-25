"""Property-based tests for CSV import round-trip (P2).

Property 2: For any valid CSV content, importing creates exactly one Entry per
valid row with matching fields and correct default statuses; ImportResult
reports accurate counts.

Validates: Requirements 1.2, 1.3, 1.6
"""

from __future__ import annotations

import csv
import os
import tempfile

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository
from src.importer.csv_importer import import_csv, ColumnMapping


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# File extensions that the importer recognises as files
_KNOWN_EXTENSIONS = [
    ".txt", ".doc", ".pdf", ".jpg", ".png", ".mp3", ".mp4",
    ".py", ".js", ".zip", ".exe", ".csv", ".json", ".html",
]

# Generate a valid file path (with extension)
_file_path_strategy = st.builds(
    lambda parts, ext: "/".join(parts) + ext,
    parts=st.lists(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
            min_size=1,
            max_size=20,
        ),
        min_size=1,
        max_size=5,
    ),
    ext=st.sampled_from(_KNOWN_EXTENSIONS),
)

# Generate a valid folder path (ends with /)
_folder_path_strategy = st.builds(
    lambda parts: "/".join(parts) + "/",
    parts=st.lists(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
            min_size=1,
            max_size=20,
        ),
        min_size=1,
        max_size=5,
    ),
)


# A single CSV row as a dict with all columns
_csv_row_strategy = st.one_of(
    # File rows
    st.fixed_dictionaries({
        "Path": _file_path_strategy,
        "Name": st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-."),
            min_size=1,
            max_size=30,
        ),
        "Size": st.integers(min_value=0, max_value=10**12).map(str),
        "Last Modified": st.sampled_from([
            "2024-01-15 10:30:00",
            "2023-06-01 08:00:00",
            "2025-12-31 23:59:59",
            "2020-03-14 12:00:00",
        ]),
        "Type": st.just("file"),
    }),
    # Folder rows
    st.fixed_dictionaries({
        "Path": _folder_path_strategy,
        "Name": st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
            min_size=1,
            max_size=30,
        ),
        "Size": st.integers(min_value=0, max_value=10**12).map(str),
        "Last Modified": st.sampled_from([
            "2024-01-15 10:30:00",
            "2023-06-01 08:00:00",
            "2025-12-31 23:59:59",
            "2020-03-14 12:00:00",
        ]),
        "Type": st.just("folder"),
    }),
)

# A list of CSV rows with unique paths
_csv_rows_strategy = st.lists(
    _csv_row_strategy,
    min_size=1,
    max_size=30,
).filter(lambda rows: len({r["Path"] for r in rows}) == len(rows))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_db():
    """Create a temporary database, returning (conn, repo, path)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    return conn, Repository(conn), path


def _write_csv(rows: list[dict[str, str]], fieldnames: list[str] | None = None) -> str:
    """Write rows to a temporary CSV file and return the path."""
    fd, csv_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    if fieldnames is None:
        fieldnames = ["Path", "Name", "Size", "Last Modified", "Type"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

class TestCsvImportRoundTrip:
    """P2: CSV import round-trip."""

    @given(rows=_csv_rows_strategy)
    @settings(max_examples=100)
    def test_import_creates_one_entry_per_valid_row(self, rows):
        """Importing valid CSV creates exactly one Entry per row."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(rows)

            result = import_csv(conn, csv_path, drive.id)

            assert result.entries_created == len(rows)
            assert result.rows_skipped == 0

            entries = repo.get_entries_by_drive(drive.id)
            assert len(entries) == len(rows)
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(rows=_csv_rows_strategy)
    @settings(max_examples=100)
    def test_imported_entries_have_correct_default_statuses(self, rows):
        """Every imported Entry has unclassified/pending_review/undecided defaults."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(rows)

            import_csv(conn, csv_path, drive.id)

            entries = repo.get_entries_by_drive(drive.id)
            for entry in entries:
                assert entry.classification_status == "unclassified"
                assert entry.review_status == "pending_review"
                assert entry.decision_status == "undecided"
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(rows=_csv_rows_strategy)
    @settings(max_examples=100)
    def test_imported_entries_match_csv_paths(self, rows):
        """Each imported Entry's path matches the corresponding CSV row."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(rows)

            import_csv(conn, csv_path, drive.id)

            entries = repo.get_entries_by_drive(drive.id)
            entry_paths = {e.path for e in entries}
            csv_paths = {r["Path"] for r in rows}
            assert entry_paths == csv_paths
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(rows=_csv_rows_strategy)
    @settings(max_examples=100)
    def test_imported_entries_match_csv_sizes(self, rows):
        """Each imported Entry's size_bytes matches the CSV row's Size."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(rows)

            import_csv(conn, csv_path, drive.id)

            entries = repo.get_entries_by_drive(drive.id)
            entry_by_path = {e.path: e for e in entries}
            for row in rows:
                entry = entry_by_path[row["Path"]]
                assert entry.size_bytes == int(row["Size"])
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(rows=_csv_rows_strategy)
    @settings(max_examples=100)
    def test_imported_entries_match_csv_entry_type(self, rows):
        """Each imported Entry's entry_type matches the CSV row's Type."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(rows)

            import_csv(conn, csv_path, drive.id)

            entries = repo.get_entries_by_drive(drive.id)
            entry_by_path = {e.path: e for e in entries}
            for row in rows:
                entry = entry_by_path[row["Path"]]
                assert entry.entry_type == row["Type"]
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(rows=_csv_rows_strategy)
    @settings(max_examples=100)
    def test_import_result_reports_correct_drive_info(self, rows):
        """ImportResult contains the correct drive_id and drive_label."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="my-drive")
            csv_path = _write_csv(rows)

            result = import_csv(conn, csv_path, drive.id)

            assert result.drive_id == drive.id
            assert result.drive_label == "my-drive"
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(rows=_csv_rows_strategy)
    @settings(max_examples=100)
    def test_imported_entries_associated_with_correct_drive(self, rows):
        """All imported Entries have the correct drive_id."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(rows)

            import_csv(conn, csv_path, drive.id)

            entries = repo.get_entries_by_drive(drive.id)
            for entry in entries:
                assert entry.drive_id == drive.id
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(rows=_csv_rows_strategy)
    @settings(max_examples=50)
    def test_import_without_type_column_infers_entry_type(self, rows):
        """When CSV has no Type column, entry_type is inferred from path/extension."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            # Write CSV without the Type column
            stripped_rows = [
                {k: v for k, v in row.items() if k != "Type"}
                for row in rows
            ]
            csv_path = _write_csv(
                stripped_rows,
                fieldnames=["Path", "Name", "Size", "Last Modified"],
            )

            result = import_csv(conn, csv_path, drive.id)

            entries = repo.get_entries_by_drive(drive.id)
            assert result.entries_created == len(rows)
            # Each entry should have a valid entry_type
            for entry in entries:
                assert entry.entry_type in ("file", "folder")
            # Paths ending with / should be folders
            entry_by_path = {e.path: e for e in entries}
            for row in rows:
                path = row["Path"]
                if path.endswith("/") or path.endswith("\\"):
                    assert entry_by_path[path].entry_type == "folder"
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(rows=_csv_rows_strategy)
    @settings(max_examples=50)
    def test_import_writes_import_log(self, rows):
        """Each import writes a record to the import_log table."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(rows)

            result = import_csv(conn, csv_path, drive.id)

            log_row = conn.execute(
                "SELECT drive_id, entries_created, rows_skipped FROM import_log "
                "WHERE drive_id = ? ORDER BY id DESC LIMIT 1",
                (drive.id,),
            ).fetchone()
            assert log_row is not None
            assert log_row[0] == drive.id
            assert log_row[1] == result.entries_created
            assert log_row[2] == result.rows_skipped
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    def test_extension_extracted_from_filename_not_full_path(self):
        """Extension must come from the filename, not a dot in a parent directory."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            rows = [
                {"Path": "F:\\Games\\MyGame.app\\Contents\\MacOS\\binary", "Size": "100"},
                {"Path": "F:\\Games\\MyGame.app\\Contents\\MacOS\\lib\\helper.dll", "Size": "200"},
                {"Path": "F:\\Games\\v1.2.3\\readme.txt", "Size": "50"},
                {"Path": "F:\\Games\\no_ext_file", "Size": "10"},
            ]
            csv_path = _write_csv(rows)

            import_csv(conn, csv_path, drive.id)

            entries = {
                e.name: e for e in repo.get_entries_by_drive(drive.id)
            }

            # "binary" has no extension — the .app in the parent dir must not leak
            assert entries["binary"].extension is None

            # "helper.dll" should have .dll, not something involving .app
            assert entries["helper.dll"].extension == ".dll"

            # "readme.txt" should have .txt, not something involving .2
            assert entries["readme.txt"].extension == ".txt"

            # "no_ext_file" has no extension
            assert entries["no_ext_file"].extension is None
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

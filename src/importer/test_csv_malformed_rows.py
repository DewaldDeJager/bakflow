"""Property-based tests for malformed CSV row handling (P3).

Property 3: For any CSV with mixed valid/malformed rows, only valid rows
produce Entries; ImportResult reports correct skip count and row numbers.

Validates: Requirements 1.4
"""

from __future__ import annotations

import csv
import os
import tempfile

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository, normalize_path
from src.importer.csv_importer import import_csv, ColumnMapping


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_KNOWN_EXTENSIONS = [
    ".txt", ".doc", ".pdf", ".jpg", ".png", ".mp3", ".mp4",
    ".py", ".js", ".zip", ".exe", ".csv", ".json", ".html",
]

# Valid file path (has a recognised extension)
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

# Valid folder path (ends with /)
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

# A valid CSV row
_valid_row_strategy = st.one_of(
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
        ]),
        "Type": st.just("file"),
        "_valid": st.just(True),
    }),
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
        ]),
        "Type": st.just("folder"),
        "_valid": st.just(True),
    }),
)

# A malformed CSV row — missing or empty Path (the only required field)
_malformed_row_strategy = st.one_of(
    # Empty path
    st.fixed_dictionaries({
        "Path": st.just(""),
        "Name": st.text(min_size=0, max_size=10),
        "Size": st.text(min_size=0, max_size=10),
        "Last Modified": st.text(min_size=0, max_size=20),
        "Type": st.text(min_size=0, max_size=10),
        "_valid": st.just(False),
    }),
    # Whitespace-only path
    st.fixed_dictionaries({
        "Path": st.sampled_from(["  ", "\t", " \t "]),
        "Name": st.text(min_size=0, max_size=10),
        "Size": st.text(min_size=0, max_size=10),
        "Last Modified": st.text(min_size=0, max_size=20),
        "Type": st.text(min_size=0, max_size=10),
        "_valid": st.just(False),
    }),
)


def _mixed_rows_strategy():
    """Generate a list with at least one valid and one malformed row."""
    return st.tuples(
        st.lists(_valid_row_strategy, min_size=1, max_size=15),
        st.lists(_malformed_row_strategy, min_size=1, max_size=10),
    ).flatmap(
        lambda pair: st.tuples(
            st.just(pair[0]),
            st.just(pair[1]),
            # Generate an interleaving permutation
            st.permutations(
                [(i, True) for i in range(len(pair[0]))]
                + [(i, False) for i in range(len(pair[1]))]
            ),
        )
    ).map(
        lambda triple: _interleave(triple[0], triple[1], triple[2])
    )


def _interleave(valid_rows, malformed_rows, ordering):
    """Interleave valid and malformed rows according to the ordering."""
    result = []
    for idx, is_valid in ordering:
        if is_valid:
            result.append(valid_rows[idx])
        else:
            result.append(malformed_rows[idx])
    return result


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
    """Write rows to a temporary CSV file and return the path.

    Strips the internal ``_valid`` marker before writing.
    """
    fd, csv_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    if fieldnames is None:
        fieldnames = ["Path", "Name", "Size", "Last Modified", "Type"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

class TestMalformedCsvRowHandling:
    """P3: Malformed CSV rows are skipped without affecting valid rows."""

    @given(data=_mixed_rows_strategy())
    @settings(max_examples=100)
    def test_only_valid_rows_produce_entries(self, data):
        """Only valid rows create Entries; malformed rows are skipped."""
        valid_rows = [r for r in data if r["_valid"]]
        malformed_rows = [r for r in data if not r["_valid"]]

        # Ensure unique paths among valid rows
        seen_paths = set()
        unique_valid = []
        for r in valid_rows:
            if r["Path"] not in seen_paths:
                seen_paths.add(r["Path"])
                unique_valid.append(r)
        assume(len(unique_valid) >= 1)

        # Rebuild data preserving order but deduplicating valid paths
        deduped_data = []
        seen = set()
        for r in data:
            if r["_valid"]:
                if r["Path"] not in seen:
                    seen.add(r["Path"])
                    deduped_data.append(r)
                # Skip duplicate valid paths
            else:
                deduped_data.append(r)

        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(deduped_data)

            result = import_csv(conn, csv_path, drive.id)

            expected_valid = len(unique_valid)
            expected_skipped = len([r for r in deduped_data if not r["_valid"]])

            assert result.entries_created == expected_valid
            assert result.rows_skipped == expected_skipped

            entries = repo.get_entries_by_drive(drive.id)
            assert len(entries) == expected_valid
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(data=_mixed_rows_strategy())
    @settings(max_examples=100)
    def test_skip_details_report_correct_row_numbers(self, data):
        """skip_details contains the correct row numbers for malformed rows."""
        # Deduplicate valid paths
        seen = set()
        deduped_data = []
        for r in data:
            if r["_valid"]:
                if r["Path"] not in seen:
                    seen.add(r["Path"])
                    deduped_data.append(r)
            else:
                deduped_data.append(r)

        assume(any(r["_valid"] for r in deduped_data))

        # Compute expected malformed row numbers (1-indexed, row 1 = header)
        expected_skip_rows = set()
        for i, r in enumerate(deduped_data):
            if not r["_valid"]:
                expected_skip_rows.add(i + 2)  # +2: header is row 1, data starts at row 2

        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(deduped_data)

            result = import_csv(conn, csv_path, drive.id)

            actual_skip_rows = {sd.row_number for sd in result.skip_details}
            assert actual_skip_rows == expected_skip_rows
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(data=_mixed_rows_strategy())
    @settings(max_examples=100)
    def test_valid_entries_have_correct_paths(self, data):
        """Entries created from valid rows have the correct normalized paths."""
        seen = set()
        deduped_data = []
        unique_valid = []
        for r in data:
            if r["_valid"]:
                norm = normalize_path(r["Path"])
                if norm not in seen:
                    seen.add(norm)
                    deduped_data.append(r)
                    unique_valid.append(r)
            else:
                deduped_data.append(r)

        assume(len(unique_valid) >= 1)

        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(deduped_data)

            import_csv(conn, csv_path, drive.id)

            entries = repo.get_entries_by_drive(drive.id)
            entry_paths = {e.path for e in entries}
            expected_paths = {normalize_path(r["Path"]) for r in unique_valid}
            assert entry_paths == expected_paths
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(data=_mixed_rows_strategy())
    @settings(max_examples=100)
    def test_valid_entries_have_correct_default_statuses(self, data):
        """Entries from valid rows have correct default statuses despite malformed siblings."""
        seen = set()
        deduped_data = []
        for r in data:
            if r["_valid"]:
                if r["Path"] not in seen:
                    seen.add(r["Path"])
                    deduped_data.append(r)
            else:
                deduped_data.append(r)

        assume(any(r["_valid"] for r in deduped_data))

        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(deduped_data)

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

    @given(
        malformed_rows=st.lists(_malformed_row_strategy, min_size=1, max_size=10),
    )
    @settings(max_examples=50)
    def test_all_malformed_csv_creates_no_entries(self, malformed_rows):
        """A CSV with only malformed rows creates zero Entries."""
        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(malformed_rows)

            result = import_csv(conn, csv_path, drive.id)

            assert result.entries_created == 0
            assert result.rows_skipped == len(malformed_rows)
            assert len(result.skip_details) == len(malformed_rows)

            entries = repo.get_entries_by_drive(drive.id)
            assert len(entries) == 0
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

    @given(data=_mixed_rows_strategy())
    @settings(max_examples=100)
    def test_skip_details_contain_reason(self, data):
        """Every skip_detail has a non-empty reason string."""
        seen = set()
        deduped_data = []
        for r in data:
            if r["_valid"]:
                if r["Path"] not in seen:
                    seen.add(r["Path"])
                    deduped_data.append(r)
            else:
                deduped_data.append(r)

        assume(any(not r["_valid"] for r in deduped_data))

        conn, repo, db_path = _make_temp_db()
        csv_path = None
        try:
            drive = repo.create_drive(label="test-drive")
            csv_path = _write_csv(deduped_data)

            result = import_csv(conn, csv_path, drive.id)

            assert len(result.skip_details) > 0
            for sd in result.skip_details:
                assert isinstance(sd.reason, str)
                assert len(sd.reason) > 0
        finally:
            conn.close()
            os.unlink(db_path)
            if csv_path:
                os.unlink(csv_path)

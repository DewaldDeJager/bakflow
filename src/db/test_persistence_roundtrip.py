"""Property-based tests for persistence round-trip (P15).

Property 15: Persistence round-trip
For any set of Drives and Entries written to the Index, closing and reopening
the database yields identical data.

**Validates: Requirements 5.1**
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

# Labels: non-empty printable strings
_label_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda s: s.strip())

# Volume serial: optional short text
_volume_serial_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P")),
        min_size=1,
        max_size=50,
    ),
)

# Volume label: optional short text
_volume_label_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
        min_size=1,
        max_size=100,
    ),
)

# Capacity: optional positive integer (bytes)
_capacity_strategy = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=20 * 10**12),
)


# Entry name: simple alphanumeric names
_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=50,
)

# Entry type
_entry_type_strategy = st.sampled_from(["file", "folder"])

# Extension: optional short extension for files
_extension_strategy = st.one_of(
    st.none(),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=5),
)

# Size in bytes
_size_strategy = st.integers(min_value=0, max_value=10**12)

# Last modified: ISO format datetime string or None
_last_modified_strategy = st.one_of(
    st.none(),
    st.datetimes(
        min_value=__import__("datetime").datetime(2000, 1, 1),
        max_value=__import__("datetime").datetime(2030, 12, 31),
    ).map(lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")),
)


# Strategy for a list of entry dicts for a single drive
_entries_list_strategy = st.lists(
    st.fixed_dictionaries({
        "name": _name_strategy,
        "entry_type": _entry_type_strategy,
        "extension": _extension_strategy,
        "size_bytes": _size_strategy,
        "last_modified": _last_modified_strategy,
    }),
    min_size=0,
    max_size=10,
)

# Strategy for a drive with its entries
_drive_with_entries_strategy = st.tuples(
    _label_strategy,
    _volume_serial_strategy,
    _volume_label_strategy,
    _capacity_strategy,
    _entries_list_strategy,
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


def _create_drive_and_entries(repo, label, volume_serial, volume_label, capacity_bytes, entry_dicts):
    """Create a drive and its entries, returning (drive, entry_count)."""
    drive = repo.create_drive(
        label=label,
        volume_serial=volume_serial,
        volume_label=volume_label,
        capacity_bytes=capacity_bytes,
    )
    # Build entry rows with unique paths
    entries = []
    for i, e in enumerate(entry_dicts):
        entries.append({
            "drive_id": drive.id,
            "path": f"/{drive.id}/{i}/{e['name']}",
            "name": e["name"],
            "entry_type": e["entry_type"],
            "extension": e["extension"] if e["entry_type"] == "file" else None,
            "size_bytes": e["size_bytes"],
            "last_modified": e["last_modified"],
        })
    count = repo.create_entries_bulk(entries)
    return drive, count


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

class TestPersistenceRoundTrip:
    """P15: Persistence round-trip — closing and reopening the database
    yields identical data."""

    @given(
        drives_data=st.lists(
            _drive_with_entries_strategy,
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=100)
    def test_drives_survive_close_and_reopen(self, drives_data):
        """For any set of Drives written to the Index, closing and reopening
        the database yields identical Drive records."""
        # Ensure unique volume serials across drives
        seen_serials: set[str] = set()
        cleaned = []
        for label, vs, vl, cap, entries in drives_data:
            if vs is not None:
                if vs in seen_serials:
                    vs = None  # deduplicate
                else:
                    seen_serials.add(vs)
            cleaned.append((label, vs, vl, cap, entries))

        conn, repo, path = _make_temp_db()
        try:
            # Write drives
            created_drives = []
            for label, vs, vl, cap, _ in cleaned:
                d = repo.create_drive(
                    label=label, volume_serial=vs,
                    volume_label=vl, capacity_bytes=cap,
                )
                created_drives.append(d)

            # Close and reopen
            conn.close()
            conn2 = init_db(path)
            repo2 = Repository(conn2)

            # Verify each drive is identical
            for original in created_drives:
                restored = repo2.get_drive(original.id)
                assert restored is not None, f"Drive {original.id} not found after reopen"
                assert restored.id == original.id
                assert restored.label == original.label
                assert restored.volume_serial == original.volume_serial
                assert restored.volume_label == original.volume_label
                assert restored.capacity_bytes == original.capacity_bytes
                assert restored.created_at == original.created_at
                assert restored.updated_at == original.updated_at

            # Verify list_drives returns same count
            all_drives = repo2.list_drives()
            assert len(all_drives) == len(created_drives)

            conn2.close()
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @given(
        label=_label_strategy,
        entry_dicts=_entries_list_strategy,
    )
    @settings(max_examples=100)
    def test_entries_survive_close_and_reopen(self, label, entry_dicts):
        """For any set of Entries written to the Index, closing and reopening
        the database yields identical Entry records."""
        conn, repo, path = _make_temp_db()
        try:
            drive, count = _create_drive_and_entries(
                repo, label, None, None, None, entry_dicts,
            )
            assert count == len(entry_dicts)

            # Read entries before close
            original_entries = repo.get_entries_by_drive(drive.id)
            assert len(original_entries) == len(entry_dicts)

            # Close and reopen
            conn.close()
            conn2 = init_db(path)
            repo2 = Repository(conn2)

            # Verify each entry is identical
            restored_entries = repo2.get_entries_by_drive(drive.id)
            assert len(restored_entries) == len(original_entries)

            # Sort both by path for stable comparison
            original_sorted = sorted(original_entries, key=lambda e: e.path)
            restored_sorted = sorted(restored_entries, key=lambda e: e.path)

            for orig, rest in zip(original_sorted, restored_sorted):
                assert rest.id == orig.id
                assert rest.drive_id == orig.drive_id
                assert rest.path == orig.path
                assert rest.name == orig.name
                assert rest.entry_type == orig.entry_type
                assert rest.extension == orig.extension
                assert rest.size_bytes == orig.size_bytes
                assert rest.last_modified == orig.last_modified
                assert rest.classification_status == orig.classification_status
                assert rest.review_status == orig.review_status
                assert rest.decision_status == orig.decision_status
                assert rest.folder_purpose == orig.folder_purpose
                assert rest.file_class == orig.file_class
                assert rest.classification_confidence == orig.classification_confidence
                assert rest.priority_review == orig.priority_review
                assert rest.decision_destination == orig.decision_destination
                assert rest.decision_notes == orig.decision_notes
                assert rest.created_at == orig.created_at
                assert rest.updated_at == orig.updated_at

            conn2.close()
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @given(
        drives_data=st.lists(
            _drive_with_entries_strategy,
            min_size=1,
            max_size=3,
        ),
    )
    @settings(max_examples=100)
    def test_full_roundtrip_drives_and_entries(self, drives_data):
        """For any set of Drives and Entries, closing and reopening the
        database yields identical data for both drives and entries."""
        # Ensure unique volume serials
        seen_serials: set[str] = set()
        cleaned = []
        for label, vs, vl, cap, entries in drives_data:
            if vs is not None:
                if vs in seen_serials:
                    vs = None
                else:
                    seen_serials.add(vs)
            cleaned.append((label, vs, vl, cap, entries))

        conn, repo, path = _make_temp_db()
        try:
            # Write drives and entries
            created = []  # list of (drive, original_entries)
            for label, vs, vl, cap, entry_dicts in cleaned:
                drive, _ = _create_drive_and_entries(
                    repo, label, vs, vl, cap, entry_dicts,
                )
                original_entries = repo.get_entries_by_drive(drive.id)
                created.append((drive, original_entries))

            # Close and reopen
            conn.close()
            conn2 = init_db(path)
            repo2 = Repository(conn2)

            # Verify drives
            for original_drive, _ in created:
                restored_drive = repo2.get_drive(original_drive.id)
                assert restored_drive is not None
                assert restored_drive.id == original_drive.id
                assert restored_drive.label == original_drive.label
                assert restored_drive.volume_serial == original_drive.volume_serial
                assert restored_drive.volume_label == original_drive.volume_label
                assert restored_drive.capacity_bytes == original_drive.capacity_bytes

            # Verify entries per drive
            for original_drive, original_entries in created:
                restored_entries = repo2.get_entries_by_drive(original_drive.id)
                assert len(restored_entries) == len(original_entries)

                orig_sorted = sorted(original_entries, key=lambda e: e.id)
                rest_sorted = sorted(restored_entries, key=lambda e: e.id)

                for orig, rest in zip(orig_sorted, rest_sorted):
                    assert rest.id == orig.id
                    assert rest.path == orig.path
                    assert rest.name == orig.name
                    assert rest.entry_type == orig.entry_type
                    assert rest.size_bytes == orig.size_bytes
                    assert rest.classification_status == orig.classification_status
                    assert rest.review_status == orig.review_status
                    assert rest.decision_status == orig.decision_status

            # Verify total counts
            total_expected = sum(len(entries) for _, entries in created)
            total_restored = sum(
                repo2.count_entries_by_drive(d.id) for d, _ in created
            )
            assert total_restored == total_expected

            conn2.close()
        finally:
            if os.path.exists(path):
                os.unlink(path)

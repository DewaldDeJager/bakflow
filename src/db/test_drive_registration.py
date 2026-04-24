"""Property-based tests for drive registration (P1).

Property 1: For any label and optional hardware identifiers, creating a Drive
produces a record with a valid UUID, exact label, matching optional fields,
and is retrievable by UUID.

Validates: Requirements 1.1
"""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Labels: non-empty printable strings (drives must have a label)
_label_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda s: s.strip())

# Volume serial: optional string like "ABCD-1234" or arbitrary short text
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
    st.integers(min_value=0, max_value=20 * 10**12),  # up to 20 TB
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


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

class TestDriveRegistrationProducesValidRecords:
    """P1: Drive registration produces valid records."""

    @given(
        label=_label_strategy,
        volume_serial=_volume_serial_strategy,
        volume_label=_volume_label_strategy,
        capacity_bytes=_capacity_strategy,
    )
    @settings(max_examples=100)
    def test_created_drive_has_valid_uuid(
        self, label, volume_serial, volume_label, capacity_bytes
    ):
        """Every created Drive has a valid UUID v4."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(
                label=label,
                volume_serial=volume_serial,
                volume_label=volume_label,
                capacity_bytes=capacity_bytes,
            )
            # Must parse as a valid UUID (raises ValueError otherwise)
            parsed = uuid.UUID(drive.id)
            assert parsed.version == 4
        finally:
            conn.close()
            os.unlink(path)

    @given(
        label=_label_strategy,
        volume_serial=_volume_serial_strategy,
        volume_label=_volume_label_strategy,
        capacity_bytes=_capacity_strategy,
    )
    @settings(max_examples=100)
    def test_created_drive_preserves_label(
        self, label, volume_serial, volume_label, capacity_bytes
    ):
        """The created Drive's label matches exactly what was provided."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(
                label=label,
                volume_serial=volume_serial,
                volume_label=volume_label,
                capacity_bytes=capacity_bytes,
            )
            assert drive.label == label
        finally:
            conn.close()
            os.unlink(path)

    @given(
        label=_label_strategy,
        volume_serial=_volume_serial_strategy,
        volume_label=_volume_label_strategy,
        capacity_bytes=_capacity_strategy,
    )
    @settings(max_examples=100)
    def test_created_drive_preserves_optional_fields(
        self, label, volume_serial, volume_label, capacity_bytes
    ):
        """Optional hardware identifiers are stored exactly as provided."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(
                label=label,
                volume_serial=volume_serial,
                volume_label=volume_label,
                capacity_bytes=capacity_bytes,
            )
            assert drive.volume_serial == volume_serial
            assert drive.volume_label == volume_label
            assert drive.capacity_bytes == capacity_bytes
        finally:
            conn.close()
            os.unlink(path)

    @given(
        label=_label_strategy,
        volume_serial=_volume_serial_strategy,
        volume_label=_volume_label_strategy,
        capacity_bytes=_capacity_strategy,
    )
    @settings(max_examples=100)
    def test_created_drive_retrievable_by_uuid(
        self, label, volume_serial, volume_label, capacity_bytes
    ):
        """A created Drive is retrievable from the Index by its UUID."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(
                label=label,
                volume_serial=volume_serial,
                volume_label=volume_label,
                capacity_bytes=capacity_bytes,
            )
            fetched = repo.get_drive(drive.id)
            assert fetched is not None
            assert fetched.id == drive.id
            assert fetched.label == label
            assert fetched.volume_serial == volume_serial
            assert fetched.volume_label == volume_label
            assert fetched.capacity_bytes == capacity_bytes
        finally:
            conn.close()
            os.unlink(path)

    @given(
        label=_label_strategy,
        volume_serial=_volume_serial_strategy,
        volume_label=_volume_label_strategy,
        capacity_bytes=_capacity_strategy,
    )
    @settings(max_examples=100)
    def test_created_drive_has_timestamps(
        self, label, volume_serial, volume_label, capacity_bytes
    ):
        """A created Drive has non-null created_at and updated_at timestamps."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(
                label=label,
                volume_serial=volume_serial,
                volume_label=volume_label,
                capacity_bytes=capacity_bytes,
            )
            assert drive.created_at is not None
            assert drive.updated_at is not None
        finally:
            conn.close()
            os.unlink(path)

    @given(
        label=_label_strategy,
    )
    @settings(max_examples=50)
    def test_each_drive_gets_unique_uuid(self, label):
        """Two drives created with the same label get distinct UUIDs."""
        conn, repo, path = _make_temp_db()
        try:
            d1 = repo.create_drive(label=label)
            d2 = repo.create_drive(label=label)
            assert d1.id != d2.id
        finally:
            conn.close()
            os.unlink(path)

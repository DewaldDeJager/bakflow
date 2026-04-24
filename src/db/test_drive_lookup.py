"""Property-based tests for drive lookup equivalence (P4).

Property 4: Drive lookup equivalence by UUID and volume serial
For any Drive with non-null volume serial, lookup by UUID and by volume serial
returns the same record.

**Validates: Requirements 1.9**
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

class TestDriveLookupEquivalence:
    """P4: Drive lookup equivalence by UUID and volume serial."""

    @given(
        label=_label_strategy,
        volume_serial=_volume_serial_strategy,
        volume_label=_volume_label_strategy,
        capacity_bytes=_capacity_strategy,
    )
    @settings(max_examples=100)
    def test_lookup_by_uuid_and_serial_returns_same_record(
        self, label, volume_serial, volume_label, capacity_bytes
    ):
        """For any Drive with non-null volume serial, get_drive and
        get_drive_by_serial return the same record with identical fields."""
        # Only test with non-null volume serials
        assume(volume_serial is not None)

        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(
                label=label,
                volume_serial=volume_serial,
                volume_label=volume_label,
                capacity_bytes=capacity_bytes,
            )

            by_uuid = repo.get_drive(drive.id)
            by_serial = repo.get_drive_by_serial(drive.volume_serial)

            # Both lookups must return non-None results
            assert by_uuid is not None, "get_drive returned None for a valid UUID"
            assert by_serial is not None, "get_drive_by_serial returned None for a valid serial"

            # All fields must match
            assert by_uuid.id == by_serial.id
            assert by_uuid.label == by_serial.label
            assert by_uuid.volume_serial == by_serial.volume_serial
            assert by_uuid.volume_label == by_serial.volume_label
            assert by_uuid.capacity_bytes == by_serial.capacity_bytes
            assert by_uuid.created_at == by_serial.created_at
            assert by_uuid.updated_at == by_serial.updated_at
        finally:
            conn.close()
            os.unlink(path)

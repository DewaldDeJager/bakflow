"""Property-based tests for unclassified batch filtering (P5).

Property 5: For any Drive with mixed statuses, get_unclassified_batch returns
only entries with classification_status in {unclassified, needs_reclassification},
count ≤ batch_size.

Validates: Requirements 2.1
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository
from src.db.status import apply_transition
from src.mcp_server.server import (
    get_unclassified_batch,
    init_server,
    _conn,
    _repo,
)
import src.mcp_server.server as server_mod


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_classification_statuses = ["unclassified", "ai_classified", "classification_failed", "needs_reclassification"]

_entry_status_strategy = st.sampled_from(_classification_statuses)

_batch_size_strategy = st.integers(min_value=1, max_value=200)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    repo = Repository(conn)
    # Wire up the server module globals
    server_mod._conn = conn
    server_mod._repo = repo
    return conn, repo, path


def _create_drive_with_entries(repo, conn, statuses: list[str]) -> str:
    """Create a drive and entries with the given classification statuses.

    Returns the drive_id.
    """
    drive = repo.create_drive(label="test-drive")
    for i, status in enumerate(statuses):
        repo.create_entries_bulk([{
            "drive_id": drive.id,
            "path": f"/file_{i}.txt",
            "name": f"file_{i}.txt",
            "entry_type": "file",
            "extension": ".txt",
            "size_bytes": 100,
        }])
        entry_id = i + 1  # autoincrement starts at 1

        # Transition to the desired status
        if status == "ai_classified":
            # Set classification fields first
            conn.execute(
                "UPDATE entries SET file_class = 'document', confidence = 0.9 WHERE id = ?",
                (entry_id,),
            )
            conn.commit()
            apply_transition(conn, entry_id, "classification_status", "ai_classified")
        elif status == "classification_failed":
            apply_transition(conn, entry_id, "classification_status", "classification_failed")
        elif status == "needs_reclassification":
            # Must go through ai_classified first
            conn.execute(
                "UPDATE entries SET file_class = 'document', confidence = 0.9 WHERE id = ?",
                (entry_id,),
            )
            conn.commit()
            apply_transition(conn, entry_id, "classification_status", "ai_classified")
            apply_transition(conn, entry_id, "classification_status", "needs_reclassification")
        # "unclassified" is the default — no transition needed

    return drive.id


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# Feature: drive-backup-triage, Property 5: Unclassified batch filtering and size limit

class TestUnclassifiedBatchFiltering:
    """P5: Unclassified batch filtering and size limit."""

    @given(
        statuses=st.lists(
            st.sampled_from(_classification_statuses),
            min_size=1,
            max_size=50,
        ),
        batch_size=_batch_size_strategy,
    )
    @settings(max_examples=100)
    def test_returns_only_eligible_statuses(self, statuses, batch_size):
        """Returned entries have classification_status in {unclassified, needs_reclassification}."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_drive_with_entries(repo, conn, statuses)
            result = asyncio.run(
                get_unclassified_batch(drive_id=drive_id, batch_size=batch_size)
            )
            assert "error" not in result
            eligible = {"unclassified", "needs_reclassification"}
            for entry in result["entries"]:
                assert entry["classification_status"] in eligible
        finally:
            conn.close()
            os.unlink(path)

    @given(
        statuses=st.lists(
            st.sampled_from(_classification_statuses),
            min_size=1,
            max_size=50,
        ),
        batch_size=_batch_size_strategy,
    )
    @settings(max_examples=100)
    def test_count_does_not_exceed_batch_size(self, statuses, batch_size):
        """Number of returned entries is at most batch_size."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_drive_with_entries(repo, conn, statuses)
            result = asyncio.run(
                get_unclassified_batch(drive_id=drive_id, batch_size=batch_size)
            )
            assert "error" not in result
            assert result["count"] <= batch_size
            assert len(result["entries"]) <= batch_size
        finally:
            conn.close()
            os.unlink(path)

    @given(
        statuses=st.lists(
            st.sampled_from(_classification_statuses),
            min_size=1,
            max_size=50,
        ),
    )
    @settings(max_examples=100)
    def test_returns_all_eligible_when_batch_large_enough(self, statuses):
        """When batch_size >= total eligible, all eligible entries are returned."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_drive_with_entries(repo, conn, statuses)
            eligible_count = sum(
                1 for s in statuses if s in ("unclassified", "needs_reclassification")
            )
            result = asyncio.run(
                get_unclassified_batch(drive_id=drive_id, batch_size=len(statuses) + 10)
            )
            assert "error" not in result
            assert result["count"] == eligible_count
        finally:
            conn.close()
            os.unlink(path)

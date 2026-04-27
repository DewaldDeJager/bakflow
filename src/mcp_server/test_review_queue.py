"""Property-based tests for review queue filtering (P10).

Property 10: get_review_queue returns only entries where
classification_status = ai_classified AND review_status = pending_review,
ordered by confidence ascending.

Validates: Requirements 3.1
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository
from src.db.status import apply_transition
from src.mcp_server.server import get_review_queue
import src.mcp_server.server as server_mod


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_classification_statuses = [
    "unclassified", "ai_classified", "classification_failed", "needs_reclassification",
]

_confidence_strategy = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@st.composite
def _entry_spec(draw):
    """Generate a spec for an entry with a classification status and confidence."""
    status = draw(st.sampled_from(_classification_statuses))
    confidence = draw(_confidence_strategy) if status == "ai_classified" else None
    return {"classification_status": status, "classification_confidence": confidence}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    repo = Repository(conn)
    server_mod._conn = conn
    server_mod._repo = repo
    return conn, repo, path


def _create_drive_with_mixed_entries(repo, conn, entry_specs):
    """Create a drive with entries in various classification states.

    entry_specs: list of dicts with 'classification_status' and optional 'confidence'.
    Returns drive_id.
    """
    drive = repo.create_drive(label="test-drive")

    for i, spec in enumerate(entry_specs):
        repo.create_entries_bulk([{
            "drive_id": drive.id,
            "path": f"/file_{i}.txt",
            "name": f"file_{i}.txt",
            "entry_type": "file",
            "extension": ".txt",
            "size_bytes": 100,
        }])
        # Entry IDs are sequential starting from 1
        entry_id = i + 1
        status = spec["classification_status"]
        confidence = spec["classification_confidence"]

        if status == "ai_classified":
            conn.execute(
                "UPDATE entries SET file_class = 'document', classification_confidence = ? WHERE id = ?",
                (confidence, entry_id),
            )
            conn.commit()
            apply_transition(conn, entry_id, "classification_status", "ai_classified")
        elif status == "classification_failed":
            apply_transition(conn, entry_id, "classification_status", "classification_failed")
        elif status == "needs_reclassification":
            conn.execute(
                "UPDATE entries SET file_class = 'document', classification_confidence = 0.5 WHERE id = ?",
                (entry_id,),
            )
            conn.commit()
            apply_transition(conn, entry_id, "classification_status", "ai_classified")
            apply_transition(conn, entry_id, "classification_status", "needs_reclassification")

    return drive.id


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# Feature: bakflow, Property 10: Review queue filtering and ordering

class TestReviewQueueFiltering:
    """P10: Review queue filtering and ordering."""

    @given(
        entry_specs=st.lists(_entry_spec(), min_size=1, max_size=30),
    )
    @settings(max_examples=100)
    def test_returns_only_ai_classified_pending_review(self, entry_specs):
        """All returned entries have classification_status=ai_classified and review_status=pending_review."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_drive_with_mixed_entries(repo, conn, entry_specs)
            result = asyncio.run(
                get_review_queue(drive_id=drive_id)
            )
            assert "error" not in result
            for entry in result["entries"]:
                assert entry["classification_status"] == "ai_classified"
                assert entry["review_status"] == "pending_review"
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_specs=st.lists(_entry_spec(), min_size=1, max_size=30),
    )
    @settings(max_examples=100)
    def test_count_matches_eligible_entries(self, entry_specs):
        """Count matches the number of ai_classified + pending_review entries."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_drive_with_mixed_entries(repo, conn, entry_specs)
            result = asyncio.run(
                get_review_queue(drive_id=drive_id, limit=1000)
            )
            assert "error" not in result
            expected = sum(
                1 for s in entry_specs if s["classification_status"] == "ai_classified"
            )
            assert result["count"] == expected
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_specs=st.lists(_entry_spec(), min_size=2, max_size=30),
    )
    @settings(max_examples=100)
    def test_ordered_by_confidence_ascending(self, entry_specs):
        """Returned entries are ordered by confidence ascending."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_drive_with_mixed_entries(repo, conn, entry_specs)
            result = asyncio.run(
                get_review_queue(drive_id=drive_id, limit=1000)
            )
            assert "error" not in result
            confidences = [e["classification_confidence"] for e in result["entries"]]
            assert confidences == sorted(confidences)
        finally:
            conn.close()
            os.unlink(path)

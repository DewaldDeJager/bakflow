"""Property-based tests for progress aggregation (P16).

Property 16: get_drive_progress returns counts per status dimension matching
actual Entry counts; completion % = reviewed / total.

Validates: Requirements 5.3
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository
from src.db.status import apply_transition
from src.mcp_server.server import get_drive_progress
import src.mcp_server.server as server_mod


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_entry_state_strategy = st.fixed_dictionaries({
    "classified": st.booleans(),
    "reviewed": st.booleans(),
    "decision": st.sampled_from(["undecided", "include", "exclude", "defer"]),
})


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


def _create_entries_with_states(repo, conn, entry_states):
    """Create a drive with entries in specified states. Returns drive_id.

    Tracks the actual resulting statuses for verification.
    Returns (drive_id, actual_statuses) where actual_statuses is a list of
    dicts with classification_status, review_status, decision_status.
    """
    drive = repo.create_drive(label="test-drive")
    actual_statuses = []

    for i, state in enumerate(entry_states):
        repo.create_entries_bulk([{
            "drive_id": drive.id,
            "path": f"/file_{i}.txt",
            "name": f"file_{i}.txt",
            "entry_type": "file",
            "extension": ".txt",
            "size_bytes": 100,
        }])
        entries = repo.get_entries_by_drive(drive.id)
        entry_id = max(e.id for e in entries)

        cs = "unclassified"
        rs = "pending_review"
        ds = "undecided"

        if state["classified"]:
            conn.execute(
                "UPDATE entries SET file_class = 'document', confidence = 0.8 WHERE id = ?",
                (entry_id,),
            )
            conn.commit()
            apply_transition(conn, entry_id, "classification_status", "ai_classified")
            cs = "ai_classified"

            if state["reviewed"]:
                apply_transition(conn, entry_id, "review_status", "reviewed")
                rs = "reviewed"

                if state["decision"] != "undecided":
                    apply_transition(conn, entry_id, "decision_status", state["decision"])
                    ds = state["decision"]

        actual_statuses.append({
            "classification_status": cs,
            "review_status": rs,
            "decision_status": ds,
        })

    return drive.id, actual_statuses


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# Feature: bakflow, Property 16: Progress aggregation correctness

class TestProgressAggregation:
    """P16: Progress aggregation correctness."""

    @given(
        entry_states=st.lists(_entry_state_strategy, min_size=1, max_size=30),
    )
    @settings(max_examples=100)
    def test_total_matches_entry_count(self, entry_states):
        """Total count matches the number of entries created."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id, _ = _create_entries_with_states(repo, conn, entry_states)
            result = asyncio.run(
                get_drive_progress(drive_id=drive_id)
            )
            assert "error" not in result
            assert result["total"] == len(entry_states)
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_states=st.lists(_entry_state_strategy, min_size=1, max_size=30),
    )
    @settings(max_examples=100)
    def test_classification_status_counts_match(self, entry_states):
        """Classification status counts match actual entry counts."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id, actual = _create_entries_with_states(repo, conn, entry_states)
            result = asyncio.run(
                get_drive_progress(drive_id=drive_id)
            )
            assert "error" not in result

            expected = Counter(s["classification_status"] for s in actual)
            for status, count in expected.items():
                assert result["classification_status"].get(status, 0) == count
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_states=st.lists(_entry_state_strategy, min_size=1, max_size=30),
    )
    @settings(max_examples=100)
    def test_review_status_counts_match(self, entry_states):
        """Review status counts match actual entry counts."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id, actual = _create_entries_with_states(repo, conn, entry_states)
            result = asyncio.run(
                get_drive_progress(drive_id=drive_id)
            )
            assert "error" not in result

            expected = Counter(s["review_status"] for s in actual)
            for status, count in expected.items():
                assert result["review_status"].get(status, 0) == count
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_states=st.lists(_entry_state_strategy, min_size=1, max_size=30),
    )
    @settings(max_examples=100)
    def test_decision_status_counts_match(self, entry_states):
        """Decision status counts match actual entry counts."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id, actual = _create_entries_with_states(repo, conn, entry_states)
            result = asyncio.run(
                get_drive_progress(drive_id=drive_id)
            )
            assert "error" not in result

            expected = Counter(s["decision_status"] for s in actual)
            for status, count in expected.items():
                assert result["decision_status"].get(status, 0) == count
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_states=st.lists(_entry_state_strategy, min_size=1, max_size=30),
    )
    @settings(max_examples=100)
    def test_completion_pct_equals_reviewed_over_total(self, entry_states):
        """completion_pct = reviewed / total."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id, actual = _create_entries_with_states(repo, conn, entry_states)
            result = asyncio.run(
                get_drive_progress(drive_id=drive_id)
            )
            assert "error" not in result

            reviewed = sum(1 for s in actual if s["review_status"] == "reviewed")
            total = len(actual)
            expected_pct = reviewed / total if total > 0 else 0.0
            assert abs(result["completion_pct"] - expected_pct) < 1e-9
        finally:
            conn.close()
            os.unlink(path)

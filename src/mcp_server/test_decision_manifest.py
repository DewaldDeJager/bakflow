"""Property-based tests for decision manifest filtering (P13).

Property 13: get_decision_manifest returns only entries where
review_status = reviewed AND decision_status matches filter.

Validates: Requirements 4.1
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
from src.mcp_server.server import get_decision_manifest
import src.mcp_server.server as server_mod


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_decision_filter_strategy = st.sampled_from(["include", "exclude", "defer"])

# Each entry can be in various states
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
    """Create a drive with entries in specified states.

    entry_states: list of dicts with 'classified', 'reviewed', 'decision'.
    Returns drive_id.
    """
    drive = repo.create_drive(label="test-drive")

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

        if state["classified"]:
            conn.execute(
                "UPDATE entries SET file_class = 'document', classification_confidence = 0.8 WHERE id = ?",
                (entry_id,),
            )
            conn.commit()
            apply_transition(conn, entry_id, "classification_status", "ai_classified")

            if state["reviewed"]:
                apply_transition(conn, entry_id, "review_status", "reviewed")

                if state["decision"] != "undecided":
                    apply_transition(conn, entry_id, "decision_status", state["decision"])

    return drive.id


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# Feature: bakflow, Property 13: Decision manifest contains only matching entries

class TestDecisionManifestFiltering:
    """P13: Decision manifest contains only matching entries."""

    @given(
        entry_states=st.lists(_entry_state_strategy, min_size=1, max_size=30),
        decision_filter=_decision_filter_strategy,
    )
    @settings(max_examples=100)
    def test_manifest_contains_only_reviewed_entries(self, entry_states, decision_filter):
        """All returned entries have review_status=reviewed."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_entries_with_states(repo, conn, entry_states)
            result = asyncio.run(
                get_decision_manifest(drive_id=drive_id, decision_filter=decision_filter)
            )
            assert "error" not in result
            for entry in result["entries"]:
                assert entry["review_status"] == "reviewed"
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_states=st.lists(_entry_state_strategy, min_size=1, max_size=30),
        decision_filter=_decision_filter_strategy,
    )
    @settings(max_examples=100)
    def test_manifest_matches_decision_filter(self, entry_states, decision_filter):
        """All returned entries have decision_status matching the filter."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_entries_with_states(repo, conn, entry_states)
            result = asyncio.run(
                get_decision_manifest(drive_id=drive_id, decision_filter=decision_filter)
            )
            assert "error" not in result
            for entry in result["entries"]:
                assert entry["decision_status"] == decision_filter
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_states=st.lists(_entry_state_strategy, min_size=1, max_size=30),
        decision_filter=_decision_filter_strategy,
    )
    @settings(max_examples=100)
    def test_manifest_count_matches_expected(self, entry_states, decision_filter):
        """Count matches the number of reviewed entries with matching decision."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_entries_with_states(repo, conn, entry_states)
            result = asyncio.run(
                get_decision_manifest(drive_id=drive_id, decision_filter=decision_filter)
            )
            assert "error" not in result

            expected = sum(
                1
                for s in entry_states
                if s["classified"] and s["reviewed"] and s["decision"] == decision_filter
            )
            assert result["count"] == expected
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_states=st.lists(_entry_state_strategy, min_size=1, max_size=30),
        decision_filter=_decision_filter_strategy,
    )
    @settings(max_examples=100)
    def test_manifest_entries_have_required_fields(self, entry_states, decision_filter):
        """Each manifest entry includes path, destination, classification, and notes."""
        conn, repo, path = _make_temp_db()
        try:
            drive_id = _create_entries_with_states(repo, conn, entry_states)
            result = asyncio.run(
                get_decision_manifest(drive_id=drive_id, decision_filter=decision_filter)
            )
            assert "error" not in result
            for entry in result["entries"]:
                assert "path" in entry
                assert "decision_destination" in entry
                assert "decision_notes" in entry
                # Classification field present (file_class or folder_purpose)
                assert "file_class" in entry or "folder_purpose" in entry
        finally:
            conn.close()
            os.unlink(path)

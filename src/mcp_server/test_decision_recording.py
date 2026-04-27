"""Property-based tests for decision recording (P11).

Property 11: For any ai_classified Entry and valid decision, recording sets
review_status = reviewed and decision_status to chosen value; destination
and notes persisted exactly.

Validates: Requirements 3.4
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
from src.mcp_server.server import record_decision
import src.mcp_server.server as server_mod


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_decision_strategy = st.sampled_from(["include", "exclude", "defer"])

_destination_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P")),
        min_size=1,
        max_size=200,
    ),
)

_notes_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
        min_size=1,
        max_size=500,
    ),
)


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


def _create_ai_classified_entry(repo, conn, idx=0):
    """Create a drive and an ai_classified file entry. Return (drive_id, entry_id)."""
    drive = repo.create_drive(label=f"test-drive-{idx}")
    repo.create_entries_bulk([{
        "drive_id": drive.id,
        "path": f"/file_{idx}.txt",
        "name": f"file_{idx}.txt",
        "entry_type": "file",
        "extension": ".txt",
        "size_bytes": 100,
    }])
    # Get the entry id
    entries = repo.get_entries_by_drive(drive.id)
    entry_id = entries[0].id

    # Classify it
    conn.execute(
        "UPDATE entries SET file_class = 'document', classification_confidence = 0.85 WHERE id = ?",
        (entry_id,),
    )
    conn.commit()
    apply_transition(conn, entry_id, "classification_status", "ai_classified")

    return drive.id, entry_id


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# Feature: bakflow, Property 11: Decision recording round-trip

class TestDecisionRecording:
    """P11: Decision recording round-trip."""

    @given(
        decision=_decision_strategy,
        destination=_destination_strategy,
        notes=_notes_strategy,
    )
    @settings(max_examples=100)
    def test_decision_sets_review_and_decision_status(self, decision, destination, notes):
        """Recording a decision sets review_status=reviewed and decision_status to the chosen value."""
        conn, repo, path = _make_temp_db()
        try:
            _, entry_id = _create_ai_classified_entry(repo, conn)
            result = asyncio.run(
                record_decision(
                    entry_id=entry_id,
                    decision=decision,
                    destination=destination,
                    notes=notes,
                )
            )
            assert "error" not in result
            entry = result["entry"]
            assert entry["review_status"] == "reviewed"
            assert entry["decision_status"] == decision
        finally:
            conn.close()
            os.unlink(path)

    @given(
        decision=_decision_strategy,
        destination=_destination_strategy,
        notes=_notes_strategy,
    )
    @settings(max_examples=100)
    def test_destination_and_notes_persisted_exactly(self, decision, destination, notes):
        """Destination and notes are stored exactly as provided."""
        conn, repo, path = _make_temp_db()
        try:
            _, entry_id = _create_ai_classified_entry(repo, conn)
            result = asyncio.run(
                record_decision(
                    entry_id=entry_id,
                    decision=decision,
                    destination=destination,
                    notes=notes,
                )
            )
            assert "error" not in result
            entry = result["entry"]
            assert entry["decision_destination"] == destination
            assert entry["decision_notes"] == notes
        finally:
            conn.close()
            os.unlink(path)

    @given(
        decision=_decision_strategy,
    )
    @settings(max_examples=50)
    def test_re_decision_updates_status(self, decision):
        """Re-deciding on an already-reviewed entry updates the decision_status."""
        conn, repo, path = _make_temp_db()
        try:
            _, entry_id = _create_ai_classified_entry(repo, conn)
            # First decision
            asyncio.run(
                record_decision(entry_id=entry_id, decision="include")
            )
            # Second decision (change mind)
            result = asyncio.run(
                record_decision(entry_id=entry_id, decision=decision)
            )
            assert "error" not in result
            entry = result["entry"]
            assert entry["decision_status"] == decision
            assert entry["review_status"] == "reviewed"
        finally:
            conn.close()
            os.unlink(path)

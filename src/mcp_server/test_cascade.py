"""Property-based tests for cascade behavior (P12).

Property 12: Cascading a decision updates only children with
decision_status = undecided; leaves others unchanged.

Validates: Requirements 3.7
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

# For children: some undecided, some already decided
_child_decision_strategy = st.sampled_from(["undecided", "include", "exclude", "defer"])


@st.composite
def _cascade_scenario(draw):
    """Generate a scenario with a folder and children in mixed decision states.

    Returns (num_children, child_decisions) where child_decisions is a list
    of decision_status values for each child.
    """
    num_children = draw(st.integers(min_value=1, max_value=15))
    child_decisions = draw(
        st.lists(_child_decision_strategy, min_size=num_children, max_size=num_children)
    )
    return num_children, child_decisions


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


def _setup_cascade_scenario(repo, conn, child_decisions):
    """Create a folder with children in specified decision states.

    All entries are ai_classified so they can be reviewed/decided.
    Returns (drive_id, folder_entry_id, child_entry_ids).
    """
    drive = repo.create_drive(label="test-drive")

    # Create the parent folder
    repo.create_entries_bulk([{
        "drive_id": drive.id,
        "path": "/parent",
        "name": "parent",
        "entry_type": "folder",
        "size_bytes": 0,
    }])
    folder_entries = repo.get_entries_by_drive(drive.id)
    folder_id = folder_entries[0].id

    # Classify the folder
    conn.execute(
        "UPDATE entries SET folder_purpose = 'project_or_work', confidence = 0.9 WHERE id = ?",
        (folder_id,),
    )
    conn.commit()
    apply_transition(conn, folder_id, "classification_status", "ai_classified")

    # Create children
    child_ids = []
    for i, decision in enumerate(child_decisions):
        repo.create_entries_bulk([{
            "drive_id": drive.id,
            "path": f"/parent/child_{i}.txt",
            "name": f"child_{i}.txt",
            "entry_type": "file",
            "extension": ".txt",
            "size_bytes": 100,
        }])
        entries = repo.get_entries_by_drive(drive.id)
        child_id = max(e.id for e in entries)
        child_ids.append(child_id)

        # Classify the child
        conn.execute(
            "UPDATE entries SET file_class = 'document', confidence = 0.8 WHERE id = ?",
            (child_id,),
        )
        conn.commit()
        apply_transition(conn, child_id, "classification_status", "ai_classified")

        # Set the child's decision if not undecided
        if decision != "undecided":
            apply_transition(conn, child_id, "review_status", "reviewed")
            apply_transition(conn, child_id, "decision_status", decision)

    return drive.id, folder_id, child_ids


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# Feature: bakflow, Property 12: Cascade applies decision only to undecided children

class TestCascadeBehavior:
    """P12: Cascade applies decision only to undecided children."""

    @given(
        scenario=_cascade_scenario(),
        cascade_decision=_decision_strategy,
    )
    @settings(max_examples=100)
    def test_cascade_updates_only_undecided_children(self, scenario, cascade_decision):
        """Only children with decision_status=undecided are updated by cascade."""
        num_children, child_decisions = scenario
        conn, repo, path = _make_temp_db()
        try:
            drive_id, folder_id, child_ids = _setup_cascade_scenario(
                repo, conn, child_decisions
            )

            # Record decision on folder with cascade
            result = asyncio.run(
                record_decision(
                    entry_id=folder_id,
                    decision=cascade_decision,
                    cascade_to_children=True,
                )
            )
            assert "error" not in result

            # Check each child
            for child_id, original_decision in zip(child_ids, child_decisions):
                child = repo.get_entry(child_id)
                assert child is not None

                if original_decision == "undecided":
                    # Should have been updated
                    assert child.decision_status == cascade_decision
                else:
                    # Should be unchanged
                    assert child.decision_status == original_decision
        finally:
            conn.close()
            os.unlink(path)

    @given(
        scenario=_cascade_scenario(),
        cascade_decision=_decision_strategy,
    )
    @settings(max_examples=100)
    def test_cascade_reports_correct_counts(self, scenario, cascade_decision):
        """Cascade result reports correct updated and skipped counts."""
        num_children, child_decisions = scenario
        conn, repo, path = _make_temp_db()
        try:
            drive_id, folder_id, child_ids = _setup_cascade_scenario(
                repo, conn, child_decisions
            )

            result = asyncio.run(
                record_decision(
                    entry_id=folder_id,
                    decision=cascade_decision,
                    cascade_to_children=True,
                )
            )
            assert "error" not in result
            assert "cascade" in result

            expected_updated = sum(1 for d in child_decisions if d == "undecided")
            expected_skipped = sum(1 for d in child_decisions if d != "undecided")

            assert result["cascade"]["updated"] == expected_updated
            assert result["cascade"]["skipped"] == expected_skipped
        finally:
            conn.close()
            os.unlink(path)

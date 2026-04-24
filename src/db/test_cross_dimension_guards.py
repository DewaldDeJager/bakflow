"""Property-based tests for cross-dimension guard enforcement (P19).

Property 19: For any Entry where classification_status ≠ ai_classified,
transitioning review_status to reviewed is rejected; succeeds only when
classification_status = ai_classified.

Validates: Requirements 7.4
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.db.models import Entry
from src.db.schema import init_db
from src.db.status import (
    CROSS_DIMENSION_GUARDS,
    InvalidTransitionError,
    apply_transition,
    validate_transition,
)
from src.db.repository import Repository


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# All classification_status values that are NOT ai_classified
NON_AI_CLASSIFIED_STATUSES = [
    "unclassified",
    "classification_failed",
    "needs_reclassification",
]

ALL_CLASSIFICATION_STATUSES = NON_AI_CLASSIFIED_STATUSES + ["ai_classified"]


def _build_entry(
    classification_status: str = "unclassified",
    review_status: str = "pending_review",
    decision_status: str = "undecided",
) -> Entry:
    """Build an in-memory Entry with the given statuses."""
    return Entry(
        id=1,
        drive_id="fake-drive-id",
        path="/test.txt",
        name="test.txt",
        entry_type="file",
        size_bytes=100,
        classification_status=classification_status,
        review_status=review_status,
        decision_status=decision_status,
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
    )


def _make_temp_db():
    """Create a temporary database, returning (conn, repo, path)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    return conn, Repository(conn), path


def _insert_entry_in_state(
    conn: sqlite3.Connection,
    repo: Repository,
    classification_status: str = "unclassified",
    review_status: str = "pending_review",
) -> int:
    """Insert a drive + entry and force it into the given state. Returns entry id."""
    drive = repo.create_drive("guard-test-drive")
    entry_path = f"/test/{uuid.uuid4()}.txt"
    repo.create_entries_bulk([{
        "drive_id": drive.id,
        "path": entry_path,
        "name": "test.txt",
        "entry_type": "file",
        "extension": ".txt",
        "size_bytes": 100,
        "last_modified": "2024-01-01 00:00:00",
    }])
    entry = repo.get_entries_by_drive(drive.id)[0]

    conn.execute(
        "UPDATE entries SET classification_status = ?, review_status = ? WHERE id = ?",
        (classification_status, review_status, entry.id),
    )
    conn.commit()
    return entry.id


# ---------------------------------------------------------------------------
# Pure validation tests (no DB)
# ---------------------------------------------------------------------------


class TestGuardRejectsNonAiClassified:
    """review_status → reviewed MUST be rejected when classification_status ≠ ai_classified."""

    @given(cs=st.sampled_from(NON_AI_CLASSIFIED_STATUSES))
    @settings(max_examples=100)
    def test_reviewed_rejected_when_not_ai_classified(self, cs: str):
        entry = _build_entry(classification_status=cs, review_status="pending_review")

        with pytest.raises(InvalidTransitionError) as exc_info:
            validate_transition("review_status", "pending_review", "reviewed", entry)

        err = exc_info.value
        assert err.dimension == "review_status"
        assert err.current_value == "pending_review"
        assert err.target_value == "reviewed"
        assert "cross-dimension guard" in str(err).lower() or "classification_status" in str(err)


class TestGuardAcceptsAiClassified:
    """review_status → reviewed MUST succeed when classification_status = ai_classified."""

    def test_reviewed_accepted_when_ai_classified(self):
        entry = _build_entry(classification_status="ai_classified", review_status="pending_review")
        # Should not raise
        validate_transition("review_status", "pending_review", "reviewed", entry)


class TestGuardOnlyAppliesToReviewed:
    """The cross-dimension guard only fires for the (review_status, reviewed) target.

    Other review_status transitions (reviewed → pending_review) should not be
    blocked by classification_status.
    """

    @given(cs=st.sampled_from(ALL_CLASSIFICATION_STATUSES))
    @settings(max_examples=100)
    def test_pending_review_not_guarded(self, cs: str):
        entry = _build_entry(classification_status=cs, review_status="reviewed")
        # reviewed → pending_review should always succeed regardless of classification_status
        validate_transition("review_status", "reviewed", "pending_review", entry)


class TestGuardErrorMessageDescriptive:
    """Rejection error must mention the guard condition and current classification_status."""

    @given(cs=st.sampled_from(NON_AI_CLASSIFIED_STATUSES))
    @settings(max_examples=100)
    def test_error_mentions_classification_status(self, cs: str):
        entry = _build_entry(classification_status=cs, review_status="pending_review")

        with pytest.raises(InvalidTransitionError) as exc_info:
            validate_transition("review_status", "pending_review", "reviewed", entry)

        msg = str(exc_info.value)
        # Error should mention the actual classification_status value
        assert cs in msg, (
            f"Error message should mention current classification_status '{cs}', got: {msg}"
        )
        # Error should mention the required value
        assert "ai_classified" in msg, (
            f"Error message should mention required 'ai_classified', got: {msg}"
        )


# ---------------------------------------------------------------------------
# Full DB round-trip tests
# ---------------------------------------------------------------------------


class TestApplyTransitionGuardRejectsInDB:
    """apply_transition must reject review_status → reviewed when guard fails, leaving DB unchanged."""

    @given(cs=st.sampled_from(NON_AI_CLASSIFIED_STATUSES))
    @settings(max_examples=100)
    def test_apply_rejected_no_db_change(self, cs: str):
        conn, repo, path = _make_temp_db()
        try:
            entry_id = _insert_entry_in_state(conn, repo, classification_status=cs, review_status="pending_review")

            with pytest.raises(InvalidTransitionError):
                apply_transition(conn, entry_id, "review_status", "reviewed")

            # Entry must remain in pending_review
            refetched = repo.get_entry(entry_id)
            assert refetched is not None
            assert refetched.review_status == "pending_review"
            assert refetched.classification_status == cs

            # No audit log entry should have been created for this failed transition
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE entry_id = ? AND dimension = 'review_status'",
                (entry_id,),
            ).fetchone()
            assert row[0] == 0, "Failed transition should not create an audit log entry"
        finally:
            conn.close()
            os.unlink(path)


class TestApplyTransitionGuardAcceptsInDB:
    """apply_transition must succeed for review_status → reviewed when classification_status = ai_classified."""

    def test_apply_accepted_persists(self):
        conn, repo, path = _make_temp_db()
        try:
            entry_id = _insert_entry_in_state(
                conn, repo,
                classification_status="ai_classified",
                review_status="pending_review",
            )

            updated = apply_transition(conn, entry_id, "review_status", "reviewed")

            assert updated.review_status == "reviewed"

            refetched = repo.get_entry(entry_id)
            assert refetched is not None
            assert refetched.review_status == "reviewed"

            # Audit log should record the transition
            row = conn.execute(
                "SELECT old_value, new_value FROM audit_log "
                "WHERE entry_id = ? AND dimension = 'review_status'",
                (entry_id,),
            ).fetchone()
            assert row is not None
            assert row[0] == "pending_review"
            assert row[1] == "reviewed"
        finally:
            conn.close()
            os.unlink(path)


class TestGuardRegisteredInMap:
    """Verify the cross-dimension guard is properly registered in CROSS_DIMENSION_GUARDS."""

    def test_guard_exists_for_review_status_reviewed(self):
        assert ("review_status", "reviewed") in CROSS_DIMENSION_GUARDS, (
            "CROSS_DIMENSION_GUARDS must contain a guard for (review_status, reviewed)"
        )

    def test_guard_is_callable(self):
        guard = CROSS_DIMENSION_GUARDS[("review_status", "reviewed")]
        assert callable(guard)

    def test_guard_returns_true_for_ai_classified(self):
        entry = _build_entry(classification_status="ai_classified")
        assert CROSS_DIMENSION_GUARDS[("review_status", "reviewed")](entry) is True

    @given(cs=st.sampled_from(NON_AI_CLASSIFIED_STATUSES))
    @settings(max_examples=100)
    def test_guard_returns_false_for_non_ai_classified(self, cs: str):
        entry = _build_entry(classification_status=cs)
        assert CROSS_DIMENSION_GUARDS[("review_status", "reviewed")](entry) is False

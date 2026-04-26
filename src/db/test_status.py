"""Property-based tests for status transition enforcement (P18).

Property 18: For any dimension and (current, target) pair, transition succeeds
iff the pair is in the valid transitions map; invalid transitions are rejected
with a descriptive error.

Validates: Requirements 7.1, 7.2, 7.3, 7.5
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.status import (
    VALID_TRANSITIONS,
    InvalidTransitionError,
    apply_transition,
    validate_transition,
)
from src.db.models import Entry
from src.db.repository import Repository


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

ALL_DIMENSIONS = list(VALID_TRANSITIONS.keys())

ALL_VALUES: dict[str, list[str]] = {
    dim: sorted(set(
        list(transitions.keys()) + [
            target
            for targets in transitions.values()
            for target in targets
        ]
    ))
    for dim, transitions in VALID_TRANSITIONS.items()
}

# Bogus values that should never appear in any dimension
BOGUS_VALUES = ["", "bogus", "INVALID", "nope", "42", "null"]


def _dimension_strategy():
    """Strategy that picks a valid dimension name."""
    return st.sampled_from(ALL_DIMENSIONS)


def _valid_pair_strategy():
    """Strategy that picks a (dimension, current, target) triple from the valid map."""
    triples = []
    for dim, transitions in VALID_TRANSITIONS.items():
        for current, targets in transitions.items():
            for target in targets:
                triples.append((dim, current, target))
    return st.sampled_from(triples)


def _invalid_pair_strategy():
    """Strategy that picks a (dimension, current, target) triple NOT in the valid map."""
    invalid_triples = []
    for dim, transitions in VALID_TRANSITIONS.items():
        all_states = ALL_VALUES[dim]
        for current in all_states:
            valid_targets = transitions.get(current, set())
            for target in all_states:
                if target not in valid_targets and target != current:
                    invalid_triples.append((dim, current, target))
    return st.sampled_from(invalid_triples)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn():
    """Create a temporary database and return the connection."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    yield conn
    conn.close()
    os.unlink(path)


@pytest.fixture
def repo(db_conn):
    return Repository(db_conn)


def _make_entry_in_state(
    conn: sqlite3.Connection,
    repo: Repository,
    classification_status: str = "unclassified",
    review_status: str = "pending_review",
    decision_status: str = "undecided",
) -> int:
    """Insert a drive + entry and force the entry into the given status combination.

    Returns the entry id.
    """
    drive = repo.create_drive("test-drive")
    # Use a unique path to avoid UNIQUE constraint violations
    import uuid
    path = f"/test/{uuid.uuid4()}.txt"
    repo.create_entries_bulk([{
        "drive_id": drive.id,
        "path": path,
        "name": "test.txt",
        "entry_type": "file",
        "extension": ".txt",
        "size_bytes": 100,
        "last_modified": "2024-01-01 00:00:00",
    }])
    entry = repo.get_entries_by_drive(drive.id)[0]

    # Force the entry into the desired state directly via SQL
    conn.execute(
        "UPDATE entries SET classification_status = ?, review_status = ?, "
        "decision_status = ? WHERE id = ?",
        (classification_status, review_status, decision_status, entry.id),
    )
    conn.commit()
    return entry.id


def _build_entry_with_status(
    classification_status: str = "unclassified",
    review_status: str = "pending_review",
    decision_status: str = "undecided",
) -> Entry:
    """Build an in-memory Entry model with the given statuses (for validate_transition tests)."""
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


def _build_folder_entry_with_status(
    classification_status: str = "unclassified",
    review_status: str = "pending_review",
    decision_status: str = "undecided",
) -> Entry:
    """Build an in-memory folder Entry model with the given statuses."""
    return Entry(
        id=1,
        drive_id="fake-drive-id",
        path="/test-folder",
        name="test-folder",
        entry_type="folder",
        size_bytes=0,
        classification_status=classification_status,
        review_status=review_status,
        decision_status=decision_status,
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
    )


def _make_folder_entry_in_state(
    conn: sqlite3.Connection,
    repo: Repository,
    classification_status: str = "unclassified",
    review_status: str = "pending_review",
    decision_status: str = "undecided",
) -> int:
    """Insert a drive + folder entry and force it into the given status combination.

    Returns the entry id.
    """
    drive = repo.create_drive("test-drive")
    import uuid
    path = f"/test/{uuid.uuid4()}"
    repo.create_entries_bulk([{
        "drive_id": drive.id,
        "path": path,
        "name": "test-folder",
        "entry_type": "folder",
        "size_bytes": 0,
    }])
    entry = repo.get_entries_by_drive(drive.id)[0]

    conn.execute(
        "UPDATE entries SET classification_status = ?, review_status = ?, "
        "decision_status = ? WHERE id = ?",
        (classification_status, review_status, decision_status, entry.id),
    )
    conn.commit()
    return entry.id


# ---------------------------------------------------------------------------
# Property tests — validate_transition (pure logic, no DB)
# ---------------------------------------------------------------------------

class TestValidTransitionsAccepted:
    """Valid (dimension, current, target) triples must not raise."""

    @given(triple=_valid_pair_strategy())
    @settings(max_examples=100)
    def test_valid_transition_does_not_raise(self, triple):
        dim, current, target = triple

        # Build an entry whose current state matches `current` for the dimension.
        # For the cross-dimension guard on review_status→reviewed, we need
        # classification_status == ai_classified.
        # For the cross-dimension guard on decision_status→descend, we need
        # entry_type == folder.
        kwargs = {}
        kwargs[dim] = current
        if dim == "review_status" and target == "reviewed":
            kwargs["classification_status"] = "ai_classified"

        build_fn = _build_entry_with_status
        if dim == "decision_status" and target == "descend":
            build_fn = _build_folder_entry_with_status

        entry = build_fn(**kwargs)
        # Should not raise
        validate_transition(dim, current, target, entry)


class TestInvalidTransitionsRejected:
    """Invalid (dimension, current, target) triples must raise InvalidTransitionError."""

    @given(triple=_invalid_pair_strategy())
    @settings(max_examples=100)
    def test_invalid_transition_raises(self, triple):
        dim, current, target = triple

        kwargs = {}
        kwargs[dim] = current
        # Satisfy cross-dimension guard so rejection is purely about the transition map
        if dim == "review_status":
            kwargs["classification_status"] = "ai_classified"

        entry = _build_entry_with_status(**kwargs)

        with pytest.raises(InvalidTransitionError) as exc_info:
            validate_transition(dim, current, target, entry)

        err = exc_info.value
        assert err.dimension == dim
        assert err.current_value == current
        assert err.target_value == target


class TestSelfTransitionRejected:
    """Transitioning from a state to itself is never valid."""

    @given(dim=_dimension_strategy())
    @settings(max_examples=50)
    def test_self_transition_not_in_valid_map(self, dim):
        for state in ALL_VALUES[dim]:
            valid_targets = VALID_TRANSITIONS[dim].get(state, set())
            assert state not in valid_targets, (
                f"Self-transition {state}→{state} should not be in valid map for {dim}"
            )


class TestUnknownDimensionRejected:
    """An unrecognised dimension name must raise InvalidTransitionError."""

    @given(bogus_dim=st.sampled_from(["bogus_dim", "status", "xyz", "classification"]))
    @settings(max_examples=20)
    def test_unknown_dimension_raises(self, bogus_dim):
        entry = _build_entry_with_status()
        with pytest.raises(InvalidTransitionError) as exc_info:
            validate_transition(bogus_dim, "unclassified", "ai_classified", entry)
        assert "unknown dimension" in str(exc_info.value)


class TestErrorMessageDescriptive:
    """Rejection errors must include dimension, current value, and target value."""

    @given(triple=_invalid_pair_strategy())
    @settings(max_examples=50)
    def test_error_contains_context(self, triple):
        dim, current, target = triple

        kwargs = {dim: current}
        if dim == "review_status":
            kwargs["classification_status"] = "ai_classified"

        entry = _build_entry_with_status(**kwargs)

        with pytest.raises(InvalidTransitionError) as exc_info:
            validate_transition(dim, current, target, entry)

        msg = str(exc_info.value)
        assert dim in msg, f"Error message should mention dimension '{dim}'"
        assert current in msg, f"Error message should mention current value '{current}'"
        assert target in msg, f"Error message should mention target value '{target}'"


# ---------------------------------------------------------------------------
# Property tests — apply_transition (full DB round-trip)
# ---------------------------------------------------------------------------

def _make_temp_db():
    """Create a temporary database, returning (conn, repo, path)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    return conn, Repository(conn), path


class TestApplyValidTransition:
    """apply_transition succeeds for valid transitions and persists the new state."""

    @given(triple=_valid_pair_strategy())
    @settings(max_examples=100)
    def test_apply_valid_transition_persists(self, triple):
        dim, current, target = triple
        conn, repo, path = _make_temp_db()
        try:
            # Set up the entry in the right starting state.
            cs = "unclassified"
            rs = "pending_review"
            ds = "undecided"
            if dim == "classification_status":
                cs = current
            elif dim == "review_status":
                rs = current
                cs = "ai_classified"  # satisfy cross-dimension guard
            elif dim == "decision_status":
                ds = current

            # Use folder entry when descend is involved (cross-dimension guard)
            if dim == "decision_status" and (target == "descend" or current == "descend"):
                entry_id = _make_folder_entry_in_state(conn, repo, cs, rs, ds)
            else:
                entry_id = _make_entry_in_state(conn, repo, cs, rs, ds)

            updated = apply_transition(conn, entry_id, dim, target)

            # The returned entry should reflect the new state
            assert getattr(updated, dim) == target

            # Re-fetch from DB to confirm persistence
            refetched = repo.get_entry(entry_id)
            assert refetched is not None
            assert getattr(refetched, dim) == target
        finally:
            conn.close()
            os.unlink(path)


class TestApplyInvalidTransitionRejected:
    """apply_transition raises for invalid transitions and does NOT change the DB."""

    @given(triple=_invalid_pair_strategy())
    @settings(max_examples=100)
    def test_apply_invalid_transition_no_change(self, triple):
        dim, current, target = triple
        conn, repo, path = _make_temp_db()
        try:
            cs = "unclassified"
            rs = "pending_review"
            ds = "undecided"
            if dim == "classification_status":
                cs = current
            elif dim == "review_status":
                rs = current
                cs = "ai_classified"
            elif dim == "decision_status":
                ds = current

            # Use folder entry when descend is the current state (DB constraint)
            if dim == "decision_status" and current == "descend":
                entry_id = _make_folder_entry_in_state(conn, repo, cs, rs, ds)
            else:
                entry_id = _make_entry_in_state(conn, repo, cs, rs, ds)

            with pytest.raises(InvalidTransitionError):
                apply_transition(conn, entry_id, dim, target)

            # Entry should remain unchanged
            refetched = repo.get_entry(entry_id)
            assert refetched is not None
            assert getattr(refetched, dim) == current
        finally:
            conn.close()
            os.unlink(path)


# ---------------------------------------------------------------------------
# Exhaustive coverage of all valid transitions per dimension
# ---------------------------------------------------------------------------

class TestClassificationStatusTransitions:
    """Req 7.1: All valid classification_status transitions are accepted."""

    @pytest.mark.parametrize("current,target", [
        ("unclassified", "ai_classified"),
        ("unclassified", "classification_failed"),
        ("classification_failed", "ai_classified"),
        ("classification_failed", "needs_reclassification"),
        ("ai_classified", "needs_reclassification"),
        ("needs_reclassification", "ai_classified"),
    ])
    def test_valid(self, current, target):
        entry = _build_entry_with_status(classification_status=current)
        validate_transition("classification_status", current, target, entry)


class TestReviewStatusTransitions:
    """Req 7.2: All valid review_status transitions are accepted."""

    @pytest.mark.parametrize("current,target", [
        ("pending_review", "reviewed"),
        ("reviewed", "pending_review"),
    ])
    def test_valid(self, current, target):
        # reviewed requires classification_status == ai_classified
        entry = _build_entry_with_status(
            classification_status="ai_classified",
            review_status=current,
        )
        validate_transition("review_status", current, target, entry)


class TestDecisionStatusTransitions:
    """Req 7.3: All valid decision_status transitions are accepted."""

    @pytest.mark.parametrize("current,target", [
        ("undecided", "include"),
        ("undecided", "exclude"),
        ("undecided", "defer"),
        ("undecided", "descend"),
        ("include", "exclude"),
        ("include", "defer"),
        ("include", "descend"),
        ("include", "undecided"),
        ("exclude", "include"),
        ("exclude", "defer"),
        ("exclude", "descend"),
        ("exclude", "undecided"),
        ("defer", "include"),
        ("defer", "exclude"),
        ("defer", "descend"),
        ("defer", "undecided"),
        ("descend", "include"),
        ("descend", "exclude"),
        ("descend", "defer"),
        ("descend", "undecided"),
    ])
    def test_valid(self, current, target):
        # descend requires a folder entry (cross-dimension guard)
        if target == "descend":
            entry = _build_folder_entry_with_status(decision_status=current)
        else:
            entry = _build_entry_with_status(decision_status=current)
        validate_transition("decision_status", current, target, entry)


# ---------------------------------------------------------------------------
# Descend transition tests (Req 3.1, 3.2)
# ---------------------------------------------------------------------------

class TestDescendFolderGuard:
    """Req 3.2: descend is only valid for folder entries."""

    @pytest.mark.parametrize("current", ["undecided", "include", "exclude", "defer"])
    def test_descend_rejected_for_file_entry(self, current):
        """File entries must not transition to descend."""
        entry = _build_entry_with_status(decision_status=current)
        with pytest.raises(InvalidTransitionError) as exc_info:
            validate_transition("decision_status", current, "descend", entry)
        assert "folder" in str(exc_info.value).lower()
        assert "descend" in str(exc_info.value)

    @pytest.mark.parametrize("current", ["undecided", "include", "exclude", "defer"])
    def test_descend_accepted_for_folder_entry(self, current):
        """Folder entries can transition to descend."""
        entry = _build_folder_entry_with_status(decision_status=current)
        validate_transition("decision_status", current, "descend", entry)

    def test_descend_guard_error_message_mentions_entry_type(self):
        """Guard failure error should indicate descend is only for folders."""
        entry = _build_entry_with_status(decision_status="undecided")
        with pytest.raises(InvalidTransitionError) as exc_info:
            validate_transition("decision_status", "undecided", "descend", entry)
        msg = str(exc_info.value)
        assert "descend" in msg
        assert "folder" in msg.lower()


class TestDescendTransitions:
    """Req 3.1: All transitions to/from descend work for folder entries."""

    @pytest.mark.parametrize("target", ["include", "exclude", "defer", "undecided"])
    def test_descend_to_other(self, target):
        """Folder entries can transition from descend to any other decision status."""
        entry = _build_folder_entry_with_status(decision_status="descend")
        validate_transition("decision_status", "descend", target, entry)

    @pytest.mark.parametrize("current", ["undecided", "include", "exclude", "defer"])
    def test_other_to_descend(self, current):
        """Folder entries can transition from any decision status to descend."""
        entry = _build_folder_entry_with_status(decision_status=current)
        validate_transition("decision_status", current, "descend", entry)


class TestFullBidirectionalTransitions:
    """Req 3.1: include→undecided, exclude→undecided, defer→undecided are now valid."""

    @pytest.mark.parametrize("current", ["include", "exclude", "defer"])
    def test_back_to_undecided(self, current):
        """All non-undecided states can transition back to undecided."""
        entry = _build_entry_with_status(decision_status=current)
        validate_transition("decision_status", current, "undecided", entry)


class TestDescendDBRoundTrip:
    """Req 3.1: apply_transition works for descend on folder entries in the DB."""

    @pytest.mark.parametrize("current,target", [
        ("undecided", "descend"),
        ("descend", "include"),
        ("descend", "exclude"),
        ("descend", "defer"),
        ("descend", "undecided"),
        ("include", "descend"),
        ("exclude", "descend"),
        ("defer", "descend"),
    ])
    def test_descend_round_trip(self, current, target):
        conn, repo, path = _make_temp_db()
        try:
            entry_id = _make_folder_entry_in_state(conn, repo, decision_status=current)
            updated = apply_transition(conn, entry_id, "decision_status", target)
            assert updated.decision_status == target
            assert updated.entry_type == "folder"

            refetched = repo.get_entry(entry_id)
            assert refetched is not None
            assert refetched.decision_status == target
        finally:
            conn.close()
            os.unlink(path)

    def test_descend_file_rejected_at_db_level(self):
        """DB CHECK constraint also prevents file entries from having descend status."""
        conn, repo, path = _make_temp_db()
        try:
            entry_id = _make_entry_in_state(conn, repo, decision_status="undecided")
            # Bypass the status engine and try to set descend directly via SQL
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE entries SET decision_status = 'descend' WHERE id = ?",
                    (entry_id,),
                )
        finally:
            conn.close()
            os.unlink(path)

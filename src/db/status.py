"""Status transition validation and enforcement.

Defines valid transitions for all three status dimensions, cross-dimension
guards, and functions to validate and apply transitions with audit logging.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from src.db.models import Entry

# ---------------------------------------------------------------------------
# Valid transitions per dimension
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "classification_status": {
        "unclassified": {"ai_classified", "classification_failed"},
        "classification_failed": {"ai_classified", "needs_reclassification"},
        "ai_classified": {"needs_reclassification"},
        "needs_reclassification": {"ai_classified"},
    },
    "review_status": {
        "pending_review": {"reviewed"},
        "reviewed": {"pending_review"},
    },
    "decision_status": {
        "undecided": {"include", "exclude", "defer", "descend"},
        "include": {"exclude", "defer", "descend", "undecided"},
        "exclude": {"include", "defer", "descend", "undecided"},
        "defer": {"include", "exclude", "descend", "undecided"},
        "descend": {"include", "exclude", "defer", "undecided"},
    },
}

# ---------------------------------------------------------------------------
# Cross-dimension guards
# ---------------------------------------------------------------------------

CROSS_DIMENSION_GUARDS: dict[tuple[str, str], Any] = {
    # review_status can only become "reviewed" if classification_status == "ai_classified"
    ("review_status", "reviewed"): lambda entry: entry.classification_status == "ai_classified",
    # decision_status can only become "descend" if entry is a folder
    ("decision_status", "descend"): lambda entry: entry.entry_type == "folder",
}

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """Raised when a status transition is not allowed."""

    def __init__(self, dimension: str, current_value: str, target_value: str, reason: str = "") -> None:
        self.dimension = dimension
        self.current_value = current_value
        self.target_value = target_value
        if reason:
            msg = (
                f"Invalid transition for '{dimension}': "
                f"'{current_value}' → '{target_value}' — {reason}"
            )
        else:
            msg = (
                f"Invalid transition for '{dimension}': "
                f"'{current_value}' → '{target_value}' is not allowed"
            )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_transition(dimension: str, current: str, target: str, entry: Entry) -> None:
    """Raise InvalidTransitionError if the transition is not allowed.

    Checks:
    1. The dimension is recognised.
    2. The current value has an entry in the transitions map.
    3. The target is in the set of allowed next states.
    4. Any cross-dimension guards pass.
    """
    if dimension not in VALID_TRANSITIONS:
        raise InvalidTransitionError(
            dimension, current, target,
            reason=f"unknown dimension '{dimension}'",
        )

    dim_map = VALID_TRANSITIONS[dimension]

    if current not in dim_map:
        raise InvalidTransitionError(
            dimension, current, target,
            reason=f"no transitions defined from '{current}'",
        )

    if target not in dim_map[current]:
        raise InvalidTransitionError(dimension, current, target)

    # Cross-dimension guards
    guard = CROSS_DIMENSION_GUARDS.get((dimension, target))
    if guard is not None and not guard(entry):
        if dimension == "review_status" and target == "reviewed":
            reason = (
                "cross-dimension guard failed: "
                f"classification_status must be 'ai_classified' "
                f"(currently '{entry.classification_status}')"
            )
        elif dimension == "decision_status" and target == "descend":
            reason = (
                "cross-dimension guard failed: "
                f"descend is only valid for folder entries "
                f"(entry_type is '{entry.entry_type}')"
            )
        else:
            reason = "cross-dimension guard failed"
        raise InvalidTransitionError(
            dimension, current, target,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Apply transition (validate → update → audit → return)
# ---------------------------------------------------------------------------


def _fetch_entry(conn: sqlite3.Connection, entry_id: int) -> Entry:
    """Fetch a single Entry row and return it as a Pydantic model."""
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise ValueError(f"Entry with id={entry_id} not found")
    col_names = [desc[0] for desc in conn.execute("SELECT * FROM entries LIMIT 0").description]
    data = dict(zip(col_names, row))
    # SQLite stores booleans as integers
    data["priority_review"] = bool(data.get("priority_review", 0))
    return Entry.model_validate(data)


def apply_transition(
    conn: sqlite3.Connection,
    entry_id: int,
    dimension: str,
    target: str,
) -> Entry:
    """Validate, update the field, write audit log, return updated Entry.

    Steps:
    1. Fetch the current entry from the database.
    2. Call validate_transition (raises on failure).
    3. UPDATE the entry's dimension field.
    4. INSERT into audit_log with entry_id, dimension, old_value, new_value.
    5. Return the updated Entry (re-fetched to get trigger-updated timestamps).
    """
    entry = _fetch_entry(conn, entry_id)
    current = getattr(entry, dimension)

    validate_transition(dimension, current, target, entry)

    # Update the status field
    # Use string formatting for column name (safe — dimension is validated above)
    conn.execute(
        f"UPDATE entries SET {dimension} = ? WHERE id = ?",
        (target, entry_id),
    )

    # Write audit log
    conn.execute(
        "INSERT INTO audit_log (entry_id, dimension, old_value, new_value) "
        "VALUES (?, ?, ?, ?)",
        (entry_id, dimension, current, target),
    )

    conn.commit()

    # Re-fetch to pick up trigger-updated timestamps
    return _fetch_entry(conn, entry_id)

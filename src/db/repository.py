"""Repository — CRUD operations and query builders for drives and entries.

Provides the ``Repository`` class that wraps a :class:`sqlite3.Connection` and
exposes typed methods for creating, reading, updating, and querying Drive and
Entry records.  All public methods return Pydantic models from
:mod:`src.db.models`.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from src.db.models import Drive, Entry


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------

def normalize_path(p: str) -> str:
    """Replace all backslashes with forward slashes."""
    return p.replace("\\", "/")


# ---------------------------------------------------------------------------
# Row → model helpers
# ---------------------------------------------------------------------------

def _row_to_drive(row: sqlite3.Row | tuple, col_names: list[str]) -> Drive:
    """Convert a raw DB row into a :class:`Drive` model."""
    return Drive.model_validate(dict(zip(col_names, row)))


def _row_to_entry(row: sqlite3.Row | tuple, col_names: list[str]) -> Entry:
    """Convert a raw DB row into an :class:`Entry` model."""
    data = dict(zip(col_names, row))
    # SQLite stores booleans as integers
    data["priority_review"] = bool(data.get("priority_review", 0))
    return Entry.model_validate(data)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class Repository:
    """Data-access layer for drives and entries."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- internal helpers ---------------------------------------------------

    def _drive_columns(self) -> list[str]:
        cur = self._conn.execute("SELECT * FROM drives LIMIT 0")
        return [desc[0] for desc in cur.description]

    def _entry_columns(self) -> list[str]:
        cur = self._conn.execute("SELECT * FROM entries LIMIT 0")
        return [desc[0] for desc in cur.description]

    # -----------------------------------------------------------------------
    # Drives
    # -----------------------------------------------------------------------

    def create_drive(
        self,
        label: str,
        volume_serial: str | None = None,
        volume_label: str | None = None,
        capacity_bytes: int | None = None,
    ) -> Drive:
        """Register a new drive and return the created :class:`Drive`."""
        drive_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO drives (id, label, volume_serial, volume_label, capacity_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            (drive_id, label, volume_serial, volume_label, capacity_bytes),
        )
        self._conn.commit()
        return self.get_drive(drive_id)  # type: ignore[return-value]

    def get_drive(self, drive_id: str) -> Drive | None:
        """Look up a drive by its UUID.  Returns ``None`` if not found."""
        cols = self._drive_columns()
        row = self._conn.execute(
            "SELECT * FROM drives WHERE id = ?", (drive_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_drive(row, cols)

    def get_drive_by_serial(self, volume_serial: str) -> Drive | None:
        """Look up a drive by its volume serial number."""
        cols = self._drive_columns()
        row = self._conn.execute(
            "SELECT * FROM drives WHERE volume_serial = ?", (volume_serial,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_drive(row, cols)

    def list_drives(self) -> list[Drive]:
        """Return all registered drives ordered by creation time."""
        cols = self._drive_columns()
        rows = self._conn.execute(
            "SELECT * FROM drives ORDER BY created_at"
        ).fetchall()
        return [_row_to_drive(r, cols) for r in rows]

    def update_drive_label(self, drive_id: str, label: str) -> Drive:
        """Update a drive's label and return the refreshed :class:`Drive`.

        Raises :class:`ValueError` if the drive does not exist.
        """
        existing = self.get_drive(drive_id)
        if existing is None:
            raise ValueError(f"Drive with id={drive_id} not found")
        self._conn.execute(
            "UPDATE drives SET label = ? WHERE id = ?",
            (label, drive_id),
        )
        self._conn.commit()
        return self.get_drive(drive_id)  # type: ignore[return-value]

    # -----------------------------------------------------------------------
    # Entries
    # -----------------------------------------------------------------------

    def create_entries_bulk(self, entries: list[dict]) -> int:
        """Insert multiple entries efficiently using ``executemany``.

        Each dict should contain keys matching Entry columns:
        ``drive_id``, ``path``, ``name``, ``entry_type``, ``extension``,
        ``size_bytes``, ``last_modified``.  Default status values are applied
        automatically by the schema.

        Returns the number of entries created.
        """
        if not entries:
            return 0

        rows = [
            (
                e["drive_id"],
                normalize_path(e["path"]),
                e["path"],
                e["name"],
                e["entry_type"],
                e.get("extension"),
                e.get("size_bytes", 0),
                e.get("last_modified"),
            )
            for e in entries
        ]
        self._conn.executemany(
            "INSERT INTO entries "
            "(drive_id, path, original_path, name, entry_type, extension, size_bytes, last_modified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def get_entry(self, entry_id: int) -> Entry | None:
        """Fetch a single entry by its primary key."""
        cols = self._entry_columns()
        row = self._conn.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_entry(row, cols)

    def get_entries_by_drive(self, drive_id: str, **filters: object) -> list[Entry]:
        """Return entries for a drive with optional filters.

        Supported keyword filters:
        - ``entry_type``: ``"file"`` or ``"folder"``
        - ``classification_status``: a valid classification status string
        - ``review_status``: a valid review status string
        - ``decision_status``: a valid decision status string
        - ``limit``: max rows to return
        - ``offset``: rows to skip
        """
        cols = self._entry_columns()
        clauses = ["drive_id = ?"]
        params: list[object] = [drive_id]

        for key in ("entry_type", "classification_status", "review_status", "decision_status"):
            if key in filters and filters[key] is not None:
                clauses.append(f"{key} = ?")
                params.append(filters[key])

        sql = "SELECT * FROM entries WHERE " + " AND ".join(clauses)
        sql += " ORDER BY path"

        if "limit" in filters and filters["limit"] is not None:
            sql += " LIMIT ?"
            params.append(filters["limit"])
        if "offset" in filters and filters["offset"] is not None:
            sql += " OFFSET ?"
            params.append(filters["offset"])

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r, cols) for r in rows]

    def count_entries_by_drive(self, drive_id: str) -> int:
        """Return the total number of entries for a drive."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM entries WHERE drive_id = ?", (drive_id,)
        ).fetchone()
        return row[0]

    # -----------------------------------------------------------------------
    # Batching
    # -----------------------------------------------------------------------

    def get_unclassified_batch(
        self, drive_id: str, batch_size: int, *, include_failed: bool = False
    ) -> list[Entry]:
        """Return up to *batch_size* entries needing classification.

        Selects entries where ``classification_status`` is ``'unclassified'``
        or ``'needs_reclassification'``.  When *include_failed* is True,
        entries with ``'classification_failed'`` are also included so they
        can be retried.  (Req 2.1)
        """
        statuses = ["unclassified", "needs_reclassification"]
        if include_failed:
            statuses.append("classification_failed")
        placeholders = ", ".join("?" for _ in statuses)
        cols = self._entry_columns()
        rows = self._conn.execute(
            f"SELECT * FROM entries "
            f"WHERE drive_id = ? "
            f"  AND classification_status IN ({placeholders}) "
            f"LIMIT ?",
            (drive_id, *statuses, batch_size),
        ).fetchall()
        return [_row_to_entry(r, cols) for r in rows]

    def get_review_queue(self, drive_id: str, filters: dict | None = None) -> list[Entry]:
        """Return entries ready for human review, ordered by confidence ASC.

        Base filter: ``classification_status = 'ai_classified'`` AND
        ``review_status = 'pending_review'``.

        Optional *filters* keys:
        - ``category``: matches against ``file_class`` OR ``folder_purpose``
        - ``min_confidence``: float lower bound (inclusive)
        - ``max_confidence``: float upper bound (inclusive)
        - ``limit``: max rows
        - ``offset``: pagination offset

        (Req 3.1)
        """
        if filters is None:
            filters = {}

        cols = self._entry_columns()
        clauses = [
            "drive_id = ?",
            "classification_status = 'ai_classified'",
            "review_status = 'pending_review'",
        ]
        params: list[object] = [drive_id]

        if "category" in filters and filters["category"] is not None:
            clauses.append("(file_class = ? OR folder_purpose = ?)")
            params.extend([filters["category"], filters["category"]])

        if "min_confidence" in filters and filters["min_confidence"] is not None:
            clauses.append("confidence >= ?")
            params.append(filters["min_confidence"])

        if "max_confidence" in filters and filters["max_confidence"] is not None:
            clauses.append("confidence <= ?")
            params.append(filters["max_confidence"])

        sql = "SELECT * FROM entries WHERE " + " AND ".join(clauses)
        sql += " ORDER BY confidence ASC"

        if "limit" in filters and filters["limit"] is not None:
            sql += " LIMIT ?"
            params.append(filters["limit"])
        if "offset" in filters and filters["offset"] is not None:
            sql += " OFFSET ?"
            params.append(filters["offset"])

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r, cols) for r in rows]

    # -----------------------------------------------------------------------
    # Progress
    # -----------------------------------------------------------------------

    def get_drive_progress(self, drive_id: str) -> dict:
        """Return status counts and completion percentage for a drive.

        Returns a dict with keys:
        - ``total``: total entry count
        - ``classification_status``: ``{status: count, ...}``
        - ``review_status``: ``{status: count, ...}``
        - ``decision_status``: ``{status: count, ...}``
        - ``completion_pct``: ``reviewed / total`` as a float (0.0–1.0)

        (Req 5.3)
        """
        total = self.count_entries_by_drive(drive_id)

        result: dict = {
            "total": total,
            "classification_status": {},
            "review_status": {},
            "decision_status": {},
            "completion_pct": 0.0,
        }

        if total == 0:
            return result

        for dimension in ("classification_status", "review_status", "decision_status"):
            rows = self._conn.execute(
                f"SELECT {dimension}, COUNT(*) FROM entries "
                f"WHERE drive_id = ? GROUP BY {dimension}",
                (drive_id,),
            ).fetchall()
            result[dimension] = {row[0]: row[1] for row in rows}

        reviewed = result["review_status"].get("reviewed", 0)
        result["completion_pct"] = reviewed / total

        return result

    # -----------------------------------------------------------------------
    # Manifest
    # -----------------------------------------------------------------------

    def get_decision_manifest(self, drive_id: str, filters: dict | None = None) -> list[Entry]:
        """Return reviewed entries, optionally filtered by decision status.

        Base filter: ``review_status = 'reviewed'``.
        Optional *filters* key ``decision_status`` narrows to a specific
        decision value (e.g. ``'include'``).

        (Req 4.1)
        """
        if filters is None:
            filters = {}

        cols = self._entry_columns()
        clauses = ["drive_id = ?", "review_status = 'reviewed'"]
        params: list[object] = [drive_id]

        if "decision_status" in filters and filters["decision_status"] is not None:
            clauses.append("decision_status = ?")
            params.append(filters["decision_status"])

        sql = "SELECT * FROM entries WHERE " + " AND ".join(clauses)
        sql += " ORDER BY path"

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r, cols) for r in rows]

    # -----------------------------------------------------------------------
    # Children (for cascade)
    # -----------------------------------------------------------------------

    def get_child_entries(self, drive_id: str, parent_path: str) -> list[Entry]:
        """Return entries whose path starts with ``parent_path + '/'``.

        Used for cascade operations when a folder decision should propagate
        to its children.
        """
        cols = self._entry_columns()
        prefix = normalize_path(parent_path).rstrip("/") + "/"
        rows = self._conn.execute(
            "SELECT * FROM entries "
            "WHERE drive_id = ? AND path LIKE ? || '%'",
            (drive_id, prefix),
        ).fetchall()
        return [_row_to_entry(r, cols) for r in rows]

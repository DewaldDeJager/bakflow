"""MCP server tool definitions for bakflow.

Exposes 8 tools via FastMCP:
- get_unclassified_batch
- get_folder_summary
- submit_classification
- classify_batch
- get_review_queue
- record_decision
- get_drive_progress
- get_decision_manifest

Each tool resolves drive identifiers (UUID or volume serial), validates
parameters, delegates to Repository/status.py, and returns structured dicts.
Requirements: 6.1, 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from src.classifier.batch import BatchClassifier
from src.classifier.provider import ClassifierConfig, create_provider
from src.config import AppConfig
from src.db.models import Entry
from src.db.repository import Repository, normalize_path
from src.db.status import InvalidTransitionError, apply_transition

mcp = FastMCP("bakflow")

# ---------------------------------------------------------------------------
# Module-level connection holder (set via init_server)
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None
_repo: Repository | None = None
_batch_classifier: BatchClassifier | None = None


def init_server(db_path: str) -> FastMCP:
    """Initialise the module-level connection, repository, and classifier, return the app."""
    global _conn, _repo, _batch_classifier
    _conn = sqlite3.connect(db_path)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _repo = Repository(_conn)

    app_config = AppConfig()
    classifier_config = ClassifierConfig(
        provider=app_config.llm_provider,
        model=app_config.model,
        base_url=app_config.base_url,
        api_key=app_config.api_key,
        confidence_threshold=app_config.confidence_threshold,
        batch_size=app_config.batch_size,
    )
    provider = create_provider(classifier_config)
    _batch_classifier = BatchClassifier(
        provider=provider,
        repo=_repo,
        conn=_conn,
        config=classifier_config,
    )

    return mcp


def get_repo() -> Repository:
    """Return the active repository, raising if not initialised."""
    if _repo is None:
        raise RuntimeError("MCP server not initialised — call init_server() first")
    return _repo


def get_conn() -> sqlite3.Connection:
    """Return the active connection, raising if not initialised."""
    if _conn is None:
        raise RuntimeError("MCP server not initialised — call init_server() first")
    return _conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(code: str, message: str, details: dict | None = None) -> dict:
    """Build a consistent error response dict."""
    resp: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        resp["error"]["details"] = details
    return resp


def _resolve_drive(drive_id: str) -> Any:
    """Resolve a drive identifier — try UUID first, then volume serial.

    Returns the Drive model or a dict error response.
    """
    repo = get_repo()
    drive = repo.get_drive(drive_id)
    if drive is None:
        drive = repo.get_drive_by_serial(drive_id)
    if drive is None:
        return _error_response(
            "DRIVE_NOT_FOUND",
            f"No drive found for identifier '{drive_id}'",
            {"drive_id": drive_id},
        )
    return drive


def _entry_to_dict(entry: Entry) -> dict:
    """Serialise an Entry model to a plain dict for MCP responses."""
    return entry.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tool: get_unclassified_batch
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_unclassified_batch(
    drive_id: str, batch_size: int = 50, include_failed: bool = False
) -> dict:
    """Get a batch of unclassified entries for a drive.

    Args:
        drive_id: UUID of the drive (also accepts volume serial number)
        batch_size: Maximum number of entries to return (default 50)
        include_failed: Also include entries with classification_failed status for retry (default False)
    """
    if not drive_id:
        return _error_response(
            "MISSING_PARAMETER", "drive_id is required", {"parameter": "drive_id"}
        )
    if batch_size < 1:
        return _error_response(
            "INVALID_PARAMETER",
            "batch_size must be a positive integer",
            {"parameter": "batch_size", "value": batch_size},
        )

    drive = _resolve_drive(drive_id)
    if isinstance(drive, dict):
        return drive

    repo = get_repo()
    entries = repo.get_unclassified_batch(drive.id, batch_size, include_failed=include_failed)
    return {
        "drive_id": drive.id,
        "batch_size": batch_size,
        "include_failed": include_failed,
        "count": len(entries),
        "entries": [_entry_to_dict(e) for e in entries],
    }


# ---------------------------------------------------------------------------
# Tool: get_folder_summary
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_folder_summary(drive_id: str, path: str) -> dict:
    """Get an aggregated summary of folder contents.

    Args:
        drive_id: UUID of the drive (also accepts volume serial number)
        path: Full path of the folder to summarize
    """
    if not drive_id:
        return _error_response(
            "MISSING_PARAMETER", "drive_id is required", {"parameter": "drive_id"}
        )
    if not path:
        return _error_response(
            "MISSING_PARAMETER", "path is required", {"parameter": "path"}
        )

    drive = _resolve_drive(drive_id)
    if isinstance(drive, dict):
        return drive

    repo = get_repo()
    children = repo.get_child_entries(drive.id, normalize_path(path))

    file_children = [c for c in children if c.entry_type == "file"]
    folder_children = [c for c in children if c.entry_type == "folder"]

    # File type distribution
    ext_counts: dict[str, int] = {}
    for child in file_children:
        ext = child.extension or "(no extension)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    # Direct subfolders only
    prefix = normalize_path(path).rstrip("/") + "/"
    folder_path_normalized = prefix.rstrip("/")
    direct_subfolders = [
        c.name
        for c in folder_children
        if c.path.startswith(prefix)
        and c.path.rstrip("/") != folder_path_normalized
        and "/" not in c.path[len(prefix):].rstrip("/")
    ]

    return {
        "drive_id": drive.id,
        "path": path,
        "file_count": len(file_children),
        "total_size": sum(c.size_bytes for c in file_children),
        "file_type_distribution": ext_counts,
        "subfolder_names": direct_subfolders,
    }


# ---------------------------------------------------------------------------
# Tool: submit_classification
# ---------------------------------------------------------------------------


@mcp.tool()
async def submit_classification(classifications: list[dict]) -> dict:
    """Submit AI classification results for a batch of entries.

    Args:
        classifications: List of dicts, each with entry_id and either
            file_class or folder_purpose, plus confidence and reasoning.
    """
    if not classifications:
        return _error_response(
            "MISSING_PARAMETER",
            "classifications list is required and must not be empty",
            {"parameter": "classifications"},
        )

    conn = get_conn()
    repo = get_repo()
    succeeded = 0
    failed = 0
    errors: list[dict] = []

    for item in classifications:
        entry_id = item.get("entry_id")
        if entry_id is None:
            failed += 1
            errors.append({"error": "missing entry_id", "item": item})
            continue

        entry = repo.get_entry(entry_id)
        if entry is None:
            failed += 1
            errors.append({"error": f"entry {entry_id} not found", "entry_id": entry_id})
            continue

        confidence = item.get("confidence")
        if confidence is None or not (0.0 <= confidence <= 1.0):
            failed += 1
            errors.append({
                "error": "confidence must be a float in [0.0, 1.0]",
                "entry_id": entry_id,
                "confidence": confidence,
            })
            continue

        reasoning = item.get("reasoning", "")
        file_class = item.get("file_class")
        folder_purpose = item.get("folder_purpose")

        try:
            if entry.entry_type == "file":
                if not file_class:
                    failed += 1
                    errors.append({
                        "error": "file_class required for file entries",
                        "entry_id": entry_id,
                    })
                    continue
                conn.execute(
                    "UPDATE entries SET file_class = ?, confidence = ?, "
                    "classification_reasoning = ? WHERE id = ?",
                    (file_class, confidence, reasoning, entry_id),
                )
            else:
                if not folder_purpose:
                    failed += 1
                    errors.append({
                        "error": "folder_purpose required for folder entries",
                        "entry_id": entry_id,
                    })
                    continue
                conn.execute(
                    "UPDATE entries SET folder_purpose = ?, confidence = ?, "
                    "classification_reasoning = ? WHERE id = ?",
                    (folder_purpose, confidence, reasoning, entry_id),
                )
            conn.commit()

            apply_transition(conn, entry_id, "classification_status", "ai_classified")
            succeeded += 1
        except (InvalidTransitionError, Exception) as exc:
            failed += 1
            errors.append({"error": str(exc), "entry_id": entry_id})

    return {
        "submitted": succeeded,
        "failed": failed,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Tool: classify_batch
# ---------------------------------------------------------------------------


@mcp.tool()
async def classify_batch(
    drive_id: str, batch_size: int = 50, include_failed: bool = False
) -> dict:
    """Fetch unclassified entries and classify them via the configured LLM.

    This is an end-to-end operation: it fetches a batch of unclassified
    entries, sends them to the LLM provider for classification, and writes
    the results back to the database (including status transitions and
    confidence-based priority_review flags).

    Args:
        drive_id: UUID of the drive (also accepts volume serial number)
        batch_size: Maximum number of entries to classify (default 50)
        include_failed: Also retry entries with classification_failed status (default False)
    """
    if not drive_id:
        return _error_response(
            "MISSING_PARAMETER", "drive_id is required", {"parameter": "drive_id"}
        )
    if batch_size < 1:
        return _error_response(
            "INVALID_PARAMETER",
            "batch_size must be a positive integer",
            {"parameter": "batch_size", "value": batch_size},
        )

    drive = _resolve_drive(drive_id)
    if isinstance(drive, dict):
        return drive

    if _batch_classifier is None:
        return _error_response(
            "SERVER_NOT_READY",
            "Batch classifier not initialised — check LLM provider configuration",
        )

    try:
        result = await _batch_classifier.classify_batch(
            drive.id, batch_size, include_failed=include_failed
        )
    except Exception as exc:
        return _error_response(
            "CLASSIFICATION_ERROR",
            f"Batch classification failed: {exc}",
        )

    return {
        "drive_id": drive.id,
        "files_classified": result.files_classified,
        "folders_classified": result.folders_classified,
        "files_failed": result.files_failed,
        "folders_failed": result.folders_failed,
        "errors": result.errors,
    }


# ---------------------------------------------------------------------------
# Tool: get_review_queue
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_review_queue(
    drive_id: str,
    category: str | None = None,
    min_confidence: float | None = None,
    max_confidence: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Get entries ready for human review, ordered by confidence ascending.

    Args:
        drive_id: UUID of the drive (also accepts volume serial number)
        category: Filter by Folder_Purpose or File_Class
        min_confidence: Minimum confidence threshold
        max_confidence: Maximum confidence threshold
        limit: Page size (default 100)
        offset: Pagination offset (default 0)
    """
    if not drive_id:
        return _error_response(
            "MISSING_PARAMETER", "drive_id is required", {"parameter": "drive_id"}
        )
    if limit < 0:
        return _error_response(
            "INVALID_PARAMETER",
            "limit must be non-negative",
            {"parameter": "limit", "value": limit},
        )
    if offset < 0:
        return _error_response(
            "INVALID_PARAMETER",
            "offset must be non-negative",
            {"parameter": "offset", "value": offset},
        )

    drive = _resolve_drive(drive_id)
    if isinstance(drive, dict):
        return drive

    repo = get_repo()
    filters: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
    }
    if category is not None:
        filters["category"] = category
    if min_confidence is not None:
        filters["min_confidence"] = min_confidence
    if max_confidence is not None:
        filters["max_confidence"] = max_confidence

    entries = repo.get_review_queue(drive.id, filters)
    return {
        "drive_id": drive.id,
        "count": len(entries),
        "limit": limit,
        "offset": offset,
        "entries": [_entry_to_dict(e) for e in entries],
    }


# ---------------------------------------------------------------------------
# Tool: record_decision
# ---------------------------------------------------------------------------


@mcp.tool()
async def record_decision(
    entry_id: int,
    decision: str,
    destination: str | None = None,
    notes: str | None = None,
    override_classification: str | None = None,
    cascade_to_children: bool = False,
    request_reclassification: bool = False,
) -> dict:
    """Record a backup decision for an entry.

    Args:
        entry_id: ID of the entry
        decision: include, exclude, or defer
        destination: Backup destination path (optional)
        notes: User notes (optional)
        override_classification: New classification to override AI suggestion (optional)
        cascade_to_children: Apply decision to undecided child entries (optional)
        request_reclassification: Mark related entries for reclassification after override (optional)
    """
    valid_decisions = {"include", "exclude", "defer"}
    if decision not in valid_decisions:
        return _error_response(
            "INVALID_PARAMETER",
            f"decision must be one of {sorted(valid_decisions)}",
            {"parameter": "decision", "value": decision},
        )

    conn = get_conn()
    repo = get_repo()

    entry = repo.get_entry(entry_id)
    if entry is None:
        return _error_response(
            "ENTRY_NOT_FOUND",
            f"No entry found with id={entry_id}",
            {"entry_id": entry_id},
        )

    # Handle classification override
    if override_classification:
        conn.execute(
            "UPDATE entries SET user_override_classification = ? WHERE id = ?",
            (override_classification, entry_id),
        )
        if entry.entry_type == "file":
            conn.execute(
                "UPDATE entries SET file_class = ? WHERE id = ?",
                (override_classification, entry_id),
            )
        else:
            conn.execute(
                "UPDATE entries SET folder_purpose = ? WHERE id = ?",
                (override_classification, entry_id),
            )
        conn.commit()

        if request_reclassification:
            try:
                apply_transition(
                    conn, entry_id, "classification_status", "needs_reclassification"
                )
            except InvalidTransitionError:
                pass  # best-effort

    # Store destination and notes
    conn.execute(
        "UPDATE entries SET decision_destination = ?, decision_notes = ? WHERE id = ?",
        (destination, notes, entry_id),
    )
    conn.commit()

    # Transition review_status → reviewed (if not already)
    try:
        entry = repo.get_entry(entry_id)  # re-fetch after updates
        if entry and entry.review_status != "reviewed":
            apply_transition(conn, entry_id, "review_status", "reviewed")
    except InvalidTransitionError as exc:
        return _error_response(
            "INVALID_TRANSITION",
            str(exc),
            {
                "dimension": "review_status",
                "current_value": entry.review_status if entry else "unknown",
                "attempted_value": "reviewed",
            },
        )

    # Transition decision_status
    try:
        entry = repo.get_entry(entry_id)  # re-fetch
        if entry and entry.decision_status != decision:
            apply_transition(conn, entry_id, "decision_status", decision)
    except InvalidTransitionError as exc:
        return _error_response(
            "INVALID_TRANSITION",
            str(exc),
            {
                "dimension": "decision_status",
                "current_value": entry.decision_status if entry else "unknown",
                "attempted_value": decision,
            },
        )

    # Cascade to children if requested
    cascade_result: dict[str, Any] | None = None
    if cascade_to_children and entry:
        cascade_result = _cascade_decision(
            conn, repo, entry, decision, destination, notes
        )

    updated = repo.get_entry(entry_id)
    result: dict[str, Any] = {"entry": _entry_to_dict(updated) if updated else None}
    if cascade_result is not None:
        result["cascade"] = cascade_result
    return result


def _cascade_decision(
    conn: sqlite3.Connection,
    repo: Repository,
    parent: Entry,
    decision: str,
    destination: str | None,
    notes: str | None,
) -> dict:
    """Apply a decision to undecided children of a folder entry.

    When a user explicitly cascades a decision to children, the intent is
    to apply it to the entire subtree.  Children that are already
    ``ai_classified`` go through the normal transition path.  Children
    that are still ``unclassified`` (or ``classification_failed``) are
    updated directly — the cascade is treated as an explicit human
    override that bypasses the classification → review pipeline.
    """
    children = repo.get_child_entries(parent.drive_id, parent.path)
    updated = 0
    skipped = 0
    skip_reasons: list[dict] = []

    for child in children:
        if child.decision_status != "undecided":
            skipped += 1
            skip_reasons.append({
                "entry_id": child.id,
                "reason": f"already has decision_status={child.decision_status}",
            })
            continue

        try:
            if child.classification_status == "ai_classified":
                # Normal path: transition review_status then decision_status
                if child.review_status != "reviewed":
                    apply_transition(conn, child.id, "review_status", "reviewed")
            else:
                # Unclassified/failed child: direct update bypassing guards
                # since the human explicitly chose to cascade
                conn.execute(
                    "UPDATE entries SET review_status = 'reviewed' WHERE id = ?",
                    (child.id,),
                )
                conn.execute(
                    "INSERT INTO audit_log (entry_id, dimension, old_value, new_value) "
                    "VALUES (?, 'review_status', ?, 'reviewed')",
                    (child.id, child.review_status),
                )
                conn.commit()

            conn.execute(
                "UPDATE entries SET decision_destination = ?, decision_notes = ? WHERE id = ?",
                (destination, notes, child.id),
            )
            conn.commit()
            apply_transition(conn, child.id, "decision_status", decision)
            updated += 1
        except InvalidTransitionError as exc:
            skipped += 1
            skip_reasons.append({"entry_id": child.id, "reason": str(exc)})

    return {"updated": updated, "skipped": skipped, "skip_reasons": skip_reasons}


# ---------------------------------------------------------------------------
# Tool: get_drive_progress
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_drive_progress(drive_id: str) -> dict:
    """Get triage progress for a drive across all status dimensions.

    Args:
        drive_id: UUID of the drive (also accepts volume serial number)
    """
    if not drive_id:
        return _error_response(
            "MISSING_PARAMETER", "drive_id is required", {"parameter": "drive_id"}
        )

    drive = _resolve_drive(drive_id)
    if isinstance(drive, dict):
        return drive

    repo = get_repo()
    progress = repo.get_drive_progress(drive.id)
    progress["drive_id"] = drive.id
    return progress


# ---------------------------------------------------------------------------
# Tool: get_decision_manifest
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_decision_manifest(
    drive_id: str,
    decision_filter: str | None = "include",
) -> dict:
    """Get the decision manifest for export.

    Args:
        drive_id: UUID of the drive (also accepts volume serial number)
        decision_filter: Filter by decision_status (default: include)
    """
    if not drive_id:
        return _error_response(
            "MISSING_PARAMETER", "drive_id is required", {"parameter": "drive_id"}
        )

    valid_decisions = {"include", "exclude", "defer", None}
    if decision_filter not in valid_decisions:
        return _error_response(
            "INVALID_PARAMETER",
            f"decision_filter must be one of {sorted(d for d in valid_decisions if d)}",
            {"parameter": "decision_filter", "value": decision_filter},
        )

    drive = _resolve_drive(drive_id)
    if isinstance(drive, dict):
        return drive

    repo = get_repo()
    filters: dict[str, Any] = {}
    if decision_filter is not None:
        filters["decision_status"] = decision_filter

    entries = repo.get_decision_manifest(drive.id, filters)
    return {
        "drive_id": drive.id,
        "drive_label": drive.label,
        "volume_serial": drive.volume_serial,
        "decision_filter": decision_filter,
        "count": len(entries),
        "entries": [_entry_to_dict(e) for e in entries],
    }

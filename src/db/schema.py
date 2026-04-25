"""Database schema initialization — DDL, WAL mode, triggers, indexes."""

import sqlite3

_DDL = """\
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS drives (
    id              TEXT PRIMARY KEY,  -- UUID
    label           TEXT NOT NULL,
    volume_serial   TEXT,
    volume_label    TEXT,
    capacity_bytes  INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_drives_volume_serial
    ON drives(volume_serial) WHERE volume_serial IS NOT NULL;

CREATE TABLE IF NOT EXISTS entries (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id                TEXT NOT NULL REFERENCES drives(id),
    path                    TEXT NOT NULL,
    original_path           TEXT NOT NULL DEFAULT '',
    name                    TEXT NOT NULL,
    entry_type              TEXT NOT NULL CHECK (entry_type IN ('file', 'folder')),
    extension               TEXT,
    size_bytes              INTEGER NOT NULL DEFAULT 0,
    last_modified           TEXT,

    -- Classification
    classification_status   TEXT NOT NULL DEFAULT 'unclassified'
        CHECK (classification_status IN (
            'unclassified', 'ai_classified', 'classification_failed', 'needs_reclassification'
        )),
    folder_purpose          TEXT CHECK (folder_purpose IS NULL OR folder_purpose IN (
        'irreplaceable_personal', 'important_personal', 'project_or_work',
        'reinstallable_software', 'media_archive', 'redundant_duplicate',
        'system_or_temp', 'unknown_review_needed'
    )),
    file_class              TEXT,
    confidence              REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    classification_reasoning TEXT,
    priority_review         INTEGER NOT NULL DEFAULT 0,  -- boolean

    -- Review
    review_status           TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (review_status IN ('pending_review', 'reviewed')),

    -- Decision
    decision_status         TEXT NOT NULL DEFAULT 'undecided'
        CHECK (decision_status IN ('undecided', 'include', 'exclude', 'defer')),
    decision_destination    TEXT,
    decision_notes          TEXT,

    -- Override
    user_override_classification TEXT,

    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(drive_id, path)
);

-- Query performance indexes
CREATE INDEX IF NOT EXISTS idx_entries_drive_classification
    ON entries(drive_id, classification_status);

CREATE INDEX IF NOT EXISTS idx_entries_drive_review
    ON entries(drive_id, review_status, classification_status);

CREATE INDEX IF NOT EXISTS idx_entries_drive_decision
    ON entries(drive_id, decision_status, review_status);

CREATE INDEX IF NOT EXISTS idx_entries_drive_path
    ON entries(drive_id, path);

CREATE INDEX IF NOT EXISTS idx_entries_confidence
    ON entries(confidence) WHERE classification_status = 'ai_classified';

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    INTEGER NOT NULL REFERENCES entries(id),
    dimension   TEXT NOT NULL,
    old_value   TEXT NOT NULL,
    new_value   TEXT NOT NULL,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_entry
    ON audit_log(entry_id);

-- Import log (tracks import operations)
CREATE TABLE IF NOT EXISTS import_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id        TEXT NOT NULL REFERENCES drives(id),
    csv_path        TEXT NOT NULL,
    entries_created INTEGER NOT NULL,
    rows_skipped    INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    completed_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Trigger: auto-update updated_at on entries
CREATE TRIGGER IF NOT EXISTS trg_entries_updated_at
    AFTER UPDATE ON entries
    BEGIN
        UPDATE entries SET updated_at = datetime('now') WHERE id = NEW.id;
    END;

-- Trigger: auto-update updated_at on drives
CREATE TRIGGER IF NOT EXISTS trg_drives_updated_at
    AFTER UPDATE ON drives
    BEGIN
        UPDATE drives SET updated_at = datetime('now') WHERE id = NEW.id;
    END;
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """Create tables, indexes, triggers. Enable WAL mode. Return connection."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(_DDL)
    return conn

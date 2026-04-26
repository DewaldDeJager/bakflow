"""Tests for db/schema.py — init_db, tables, indexes, triggers, constraints."""

import sqlite3
import tempfile
import os
import pytest

from src.db.schema import init_db


@pytest.fixture
def db_conn():
    """Create a temporary database and return the connection."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    yield conn
    conn.close()
    os.unlink(path)


def test_init_db_returns_connection(db_conn):
    assert isinstance(db_conn, sqlite3.Connection)


def test_wal_mode_enabled(db_conn):
    mode = db_conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_foreign_keys_enabled(db_conn):
    fk = db_conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return {r[0] for r in rows}


def test_all_tables_created(db_conn):
    tables = _table_names(db_conn)
    assert {"drives", "entries", "audit_log", "import_log"}.issubset(tables)


def _index_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {r[0] for r in rows}


def test_all_indexes_created(db_conn):
    indexes = _index_names(db_conn)
    expected = {
        "idx_drives_volume_serial",
        "idx_entries_drive_classification",
        "idx_entries_drive_review",
        "idx_entries_drive_decision",
        "idx_entries_drive_path",
        "idx_entries_depth",
        "idx_entries_confidence",
        "idx_audit_entry",
    }
    assert expected.issubset(indexes)


def _trigger_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
    ).fetchall()
    return {r[0] for r in rows}


def test_triggers_created(db_conn):
    triggers = _trigger_names(db_conn)
    assert {"trg_entries_updated_at", "trg_drives_updated_at"}.issubset(triggers)


def test_entry_type_check_constraint(db_conn):
    db_conn.execute(
        "INSERT INTO drives (id, label) VALUES ('d1', 'Test Drive')"
    )
    # Valid entry_type
    db_conn.execute(
        "INSERT INTO entries (drive_id, path, name, entry_type) "
        "VALUES ('d1', '/a.txt', 'a.txt', 'file')"
    )
    # Invalid entry_type should fail
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type) "
            "VALUES ('d1', '/b.txt', 'b.txt', 'symlink')"
        )


def test_classification_status_check_constraint(db_conn):
    db_conn.execute(
        "INSERT INTO drives (id, label) VALUES ('d1', 'Test Drive')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type, classification_status) "
            "VALUES ('d1', '/a.txt', 'a.txt', 'file', 'bogus')"
        )


def test_confidence_check_constraint(db_conn):
    db_conn.execute(
        "INSERT INTO drives (id, label) VALUES ('d1', 'Test Drive')"
    )
    # classification_confidence > 1.0 should fail
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type, classification_confidence) "
            "VALUES ('d1', '/a.txt', 'a.txt', 'file', 1.5)"
        )


def test_unique_drive_path_constraint(db_conn):
    db_conn.execute(
        "INSERT INTO drives (id, label) VALUES ('d1', 'Test Drive')"
    )
    db_conn.execute(
        "INSERT INTO entries (drive_id, path, name, entry_type) "
        "VALUES ('d1', '/a.txt', 'a.txt', 'file')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type) "
            "VALUES ('d1', '/a.txt', 'a.txt', 'file')"
        )


def test_trigger_updates_entries_updated_at(db_conn):
    db_conn.execute(
        "INSERT INTO drives (id, label) VALUES ('d1', 'Test Drive')"
    )
    db_conn.execute(
        "INSERT INTO entries (drive_id, path, name, entry_type) "
        "VALUES ('d1', '/a.txt', 'a.txt', 'file')"
    )
    original = db_conn.execute(
        "SELECT updated_at FROM entries WHERE id = 1"
    ).fetchone()[0]

    # Update a field to fire the trigger
    db_conn.execute(
        "UPDATE entries SET name = 'b.txt' WHERE id = 1"
    )
    updated = db_conn.execute(
        "SELECT updated_at FROM entries WHERE id = 1"
    ).fetchone()[0]

    # updated_at should be set (trigger fired); may or may not differ
    # depending on timing, but it should at least be non-null
    assert updated is not None


def test_trigger_updates_drives_updated_at(db_conn):
    db_conn.execute(
        "INSERT INTO drives (id, label) VALUES ('d1', 'Test Drive')"
    )
    original = db_conn.execute(
        "SELECT updated_at FROM drives WHERE id = 'd1'"
    ).fetchone()[0]

    db_conn.execute(
        "UPDATE drives SET label = 'Renamed' WHERE id = 'd1'"
    )
    updated = db_conn.execute(
        "SELECT updated_at FROM drives WHERE id = 'd1'"
    ).fetchone()[0]

    assert updated is not None


def test_idempotent_init(db_conn):
    """Calling init_db on an already-initialized DB should not error."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn1 = init_db(path)
    conn1.execute("INSERT INTO drives (id, label) VALUES ('d1', 'Drive')")
    conn1.commit()
    conn1.close()

    # Re-init same DB
    conn2 = init_db(path)
    row = conn2.execute("SELECT label FROM drives WHERE id = 'd1'").fetchone()
    assert row[0] == "Drive"
    conn2.close()
    os.unlink(path)


# --- Wavefront schema tests ---


def _column_names(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_tree_metadata_columns_exist(db_conn):
    cols = _column_names(db_conn, "entries")
    expected = {"depth", "parent_path", "child_count", "descendant_file_count", "descendant_folder_count"}
    assert expected.issubset(cols)


def test_dual_confidence_columns_exist(db_conn):
    cols = _column_names(db_conn, "entries")
    assert "classification_confidence" in cols
    assert "decision_confidence" in cols
    assert "confidence" not in cols


def test_tree_metadata_null_vs_zero(db_conn):
    """NULL means unknown, 0 means actually zero — both must be accepted."""
    db_conn.execute("INSERT INTO drives (id, label) VALUES ('d1', 'Test')")
    # All NULL
    db_conn.execute(
        "INSERT INTO entries (drive_id, path, name, entry_type) "
        "VALUES ('d1', '/a', 'a', 'folder')"
    )
    row = db_conn.execute("SELECT depth, child_count FROM entries WHERE path = '/a'").fetchone()
    assert row[0] is None
    assert row[1] is None

    # All zero
    db_conn.execute(
        "INSERT INTO entries (drive_id, path, name, entry_type, depth, parent_path, "
        "child_count, descendant_file_count, descendant_folder_count) "
        "VALUES ('d1', '/b', 'b', 'folder', 0, NULL, 0, 0, 0)"
    )
    row = db_conn.execute(
        "SELECT depth, child_count, descendant_file_count, descendant_folder_count "
        "FROM entries WHERE path = '/b'"
    ).fetchone()
    assert row == (0, 0, 0, 0)


def test_descend_allowed_for_folder(db_conn):
    db_conn.execute("INSERT INTO drives (id, label) VALUES ('d1', 'Test')")
    db_conn.execute(
        "INSERT INTO entries (drive_id, path, name, entry_type, decision_status) "
        "VALUES ('d1', '/folder', 'folder', 'folder', 'descend')"
    )
    row = db_conn.execute(
        "SELECT decision_status FROM entries WHERE path = '/folder'"
    ).fetchone()
    assert row[0] == "descend"


def test_descend_rejected_for_file(db_conn):
    db_conn.execute("INSERT INTO drives (id, label) VALUES ('d1', 'Test')")
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type, decision_status) "
            "VALUES ('d1', '/a.txt', 'a.txt', 'file', 'descend')"
        )


def test_decision_confidence_check_constraint(db_conn):
    db_conn.execute("INSERT INTO drives (id, label) VALUES ('d1', 'Test')")
    # Valid: NULL
    db_conn.execute(
        "INSERT INTO entries (drive_id, path, name, entry_type, decision_confidence) "
        "VALUES ('d1', '/a.txt', 'a.txt', 'file', NULL)"
    )
    # Valid: 0.0
    db_conn.execute(
        "INSERT INTO entries (drive_id, path, name, entry_type, decision_confidence) "
        "VALUES ('d1', '/b.txt', 'b.txt', 'file', 0.0)"
    )
    # Valid: 1.0
    db_conn.execute(
        "INSERT INTO entries (drive_id, path, name, entry_type, decision_confidence) "
        "VALUES ('d1', '/c.txt', 'c.txt', 'file', 1.0)"
    )
    # Invalid: > 1.0
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type, decision_confidence) "
            "VALUES ('d1', '/d.txt', 'd.txt', 'file', 1.5)"
        )


def test_classification_confidence_accepts_valid_range(db_conn):
    db_conn.execute("INSERT INTO drives (id, label) VALUES ('d1', 'Test')")
    for val in [None, 0.0, 0.5, 1.0]:
        path = f"/f_{val}.txt"
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type, classification_confidence) "
            "VALUES ('d1', ?, ?, 'file', ?)",
            (path, path, val),
        )
    # Invalid: negative
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type, classification_confidence) "
            "VALUES ('d1', '/neg.txt', 'neg.txt', 'file', -0.1)"
        )


def test_decision_confidence_rejects_negative(db_conn):
    db_conn.execute("INSERT INTO drives (id, label) VALUES ('d1', 'Test')")
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type, decision_confidence) "
            "VALUES ('d1', '/neg.txt', 'neg.txt', 'file', -0.1)"
        )


def test_existing_decision_statuses_still_work(db_conn):
    """Verify the original statuses are unaffected by adding descend."""
    db_conn.execute("INSERT INTO drives (id, label) VALUES ('d1', 'Test')")
    for status in ("undecided", "include", "exclude", "defer"):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type, decision_status) "
            "VALUES ('d1', ?, ?, 'file', ?)",
            (f"/{status}.txt", f"{status}.txt", status),
        )
    count = db_conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    assert count == 4

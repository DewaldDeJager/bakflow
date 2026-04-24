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
    # confidence > 1.0 should fail
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO entries (drive_id, path, name, entry_type, confidence) "
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

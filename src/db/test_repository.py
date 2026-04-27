"""Tests for db/repository.py — Repository CRUD and query methods."""

import sqlite3
import tempfile
import os
import uuid

import pytest

from src.db.schema import init_db
from src.db.repository import Repository


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


# ---------------------------------------------------------------------------
# Drive CRUD
# ---------------------------------------------------------------------------

class TestCreateDrive:
    def test_creates_drive_with_valid_uuid(self, repo):
        drive = repo.create_drive("My Drive")
        uuid.UUID(drive.id)  # should not raise
        assert drive.label == "My Drive"

    def test_optional_fields_stored(self, repo):
        drive = repo.create_drive(
            "Drive", volume_serial="ABCD-1234",
            volume_label="DATA", capacity_bytes=500_000_000_000,
        )
        assert drive.volume_serial == "ABCD-1234"
        assert drive.volume_label == "DATA"
        assert drive.capacity_bytes == 500_000_000_000

    def test_optional_fields_default_none(self, repo):
        drive = repo.create_drive("Bare")
        assert drive.volume_serial is None
        assert drive.volume_label is None
        assert drive.capacity_bytes is None


class TestGetDrive:
    def test_returns_none_for_missing(self, repo):
        assert repo.get_drive("nonexistent") is None

    def test_round_trip(self, repo):
        created = repo.create_drive("Test")
        fetched = repo.get_drive(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.label == "Test"


class TestGetDriveBySerial:
    def test_returns_none_when_no_match(self, repo):
        assert repo.get_drive_by_serial("XXXX") is None

    def test_finds_by_serial(self, repo):
        created = repo.create_drive("D", volume_serial="SER-1")
        found = repo.get_drive_by_serial("SER-1")
        assert found is not None
        assert found.id == created.id


class TestListDrives:
    def test_empty(self, repo):
        assert repo.list_drives() == []

    def test_returns_all(self, repo):
        repo.create_drive("A")
        repo.create_drive("B")
        assert len(repo.list_drives()) == 2


class TestUpdateDriveLabel:
    def test_updates_label(self, repo):
        d = repo.create_drive("Old")
        updated = repo.update_drive_label(d.id, "New")
        assert updated.label == "New"

    def test_raises_for_missing_drive(self, repo):
        with pytest.raises(ValueError):
            repo.update_drive_label("nope", "X")


# ---------------------------------------------------------------------------
# Entry CRUD
# ---------------------------------------------------------------------------

def _make_entry_dict(drive_id, path="/a.txt", name="a.txt", entry_type="file", **kw):
    d = {
        "drive_id": drive_id,
        "path": path,
        "name": name,
        "entry_type": entry_type,
        "extension": kw.get("extension", ".txt"),
        "size_bytes": kw.get("size_bytes", 100),
        "last_modified": kw.get("last_modified", "2024-01-01 00:00:00"),
    }
    return d


class TestCreateEntriesBulk:
    def test_empty_list(self, repo):
        assert repo.create_entries_bulk([]) == 0

    def test_creates_entries(self, repo):
        d = repo.create_drive("D")
        entries = [
            _make_entry_dict(d.id, "/a.txt", "a.txt"),
            _make_entry_dict(d.id, "/b.txt", "b.txt"),
        ]
        count = repo.create_entries_bulk(entries)
        assert count == 2
        assert repo.count_entries_by_drive(d.id) == 2

    def test_default_statuses(self, repo):
        d = repo.create_drive("D")
        repo.create_entries_bulk([_make_entry_dict(d.id)])
        entries = repo.get_entries_by_drive(d.id)
        e = entries[0]
        assert e.classification_status == "unclassified"
        assert e.review_status == "pending_review"
        assert e.decision_status == "undecided"


class TestGetEntry:
    def test_returns_none_for_missing(self, repo):
        assert repo.get_entry(9999) is None

    def test_round_trip(self, repo):
        d = repo.create_drive("D")
        repo.create_entries_bulk([_make_entry_dict(d.id)])
        entries = repo.get_entries_by_drive(d.id)
        fetched = repo.get_entry(entries[0].id)
        assert fetched is not None
        assert fetched.path == "/a.txt"


class TestGetEntriesByDrive:
    def test_filters_by_entry_type(self, repo):
        d = repo.create_drive("D")
        repo.create_entries_bulk([
            _make_entry_dict(d.id, "/a.txt", "a.txt", "file"),
            _make_entry_dict(d.id, "/dir", "dir", "folder"),
        ])
        files = repo.get_entries_by_drive(d.id, entry_type="file")
        assert len(files) == 1
        assert files[0].entry_type == "file"

    def test_limit_and_offset(self, repo):
        d = repo.create_drive("D")
        repo.create_entries_bulk([
            _make_entry_dict(d.id, f"/{i}.txt", f"{i}.txt") for i in range(5)
        ])
        page = repo.get_entries_by_drive(d.id, limit=2, offset=1)
        assert len(page) == 2


class TestCountEntriesByDrive:
    def test_zero_when_empty(self, repo):
        d = repo.create_drive("D")
        assert repo.count_entries_by_drive(d.id) == 0


# ---------------------------------------------------------------------------
# Batching / Query methods
# ---------------------------------------------------------------------------

def _seed_classified_entries(repo, db_conn, drive_id, count=5):
    """Helper: create entries and manually set some to ai_classified."""
    entries = [
        _make_entry_dict(drive_id, f"/file{i}.txt", f"file{i}.txt")
        for i in range(count)
    ]
    repo.create_entries_bulk(entries)
    all_entries = repo.get_entries_by_drive(drive_id)
    # Classify the first half directly via SQL (bypass status engine for test setup)
    for e in all_entries[: count // 2]:
        db_conn.execute(
            "UPDATE entries SET classification_status='ai_classified', "
            "classification_confidence=0.5, file_class='document' WHERE id=?",
            (e.id,),
        )
    db_conn.commit()
    return all_entries


class TestGetUnclassifiedBatch:
    def test_returns_only_unclassified(self, repo, db_conn):
        d = repo.create_drive("D")
        _seed_classified_entries(repo, db_conn, d.id, count=6)
        batch = repo.get_unclassified_batch(d.id, batch_size=100)
        for e in batch:
            assert e.classification_status in ("unclassified", "needs_reclassification")

    def test_respects_batch_size(self, repo):
        d = repo.create_drive("D")
        repo.create_entries_bulk([
            _make_entry_dict(d.id, f"/{i}.txt", f"{i}.txt") for i in range(10)
        ])
        batch = repo.get_unclassified_batch(d.id, batch_size=3)
        assert len(batch) <= 3


class TestGetReviewQueue:
    def test_returns_ai_classified_pending_review(self, repo, db_conn):
        d = repo.create_drive("D")
        _seed_classified_entries(repo, db_conn, d.id, count=6)
        queue = repo.get_review_queue(d.id)
        for e in queue:
            assert e.classification_status == "ai_classified"
            assert e.review_status == "pending_review"

    def test_ordered_by_confidence_asc(self, repo, db_conn):
        d = repo.create_drive("D")
        repo.create_entries_bulk([
            _make_entry_dict(d.id, f"/f{i}.txt", f"f{i}.txt") for i in range(3)
        ])
        all_e = repo.get_entries_by_drive(d.id)
        for i, e in enumerate(all_e):
            db_conn.execute(
                "UPDATE entries SET classification_status='ai_classified', "
                "classification_confidence=0.8, decision_confidence=?, file_class='doc' WHERE id=?",
                (0.9 - i * 0.3, e.id),
            )
        db_conn.commit()
        queue = repo.get_review_queue(d.id)
        confs = [e.decision_confidence for e in queue]
        assert confs == sorted(confs)

    def test_category_filter(self, repo, db_conn):
        d = repo.create_drive("D")
        repo.create_entries_bulk([
            _make_entry_dict(d.id, "/a.txt", "a.txt", "file"),
            _make_entry_dict(d.id, "/b", "b", "folder"),
        ])
        all_e = repo.get_entries_by_drive(d.id)
        db_conn.execute(
            "UPDATE entries SET classification_status='ai_classified', "
            "classification_confidence=0.8, file_class='photo' WHERE id=?",
            (all_e[0].id,),
        )
        db_conn.execute(
            "UPDATE entries SET classification_status='ai_classified', "
            "classification_confidence=0.7, folder_purpose='media_archive' WHERE id=?",
            (all_e[1].id,),
        )
        db_conn.commit()
        queue = repo.get_review_queue(d.id, filters={"category": "photo"})
        assert len(queue) == 1
        assert queue[0].file_class == "photo"


class TestGetDriveProgress:
    def test_empty_drive(self, repo):
        d = repo.create_drive("D")
        prog = repo.get_drive_progress(d.id)
        assert prog["total"] == 0
        assert prog["completion_pct"] == 0.0

    def test_counts_and_completion(self, repo, db_conn):
        d = repo.create_drive("D")
        repo.create_entries_bulk([
            _make_entry_dict(d.id, f"/{i}.txt", f"{i}.txt") for i in range(4)
        ])
        # Mark 1 entry as reviewed
        all_e = repo.get_entries_by_drive(d.id)
        db_conn.execute(
            "UPDATE entries SET classification_status='ai_classified', "
            "review_status='reviewed', decision_status='include' WHERE id=?",
            (all_e[0].id,),
        )
        db_conn.commit()
        prog = repo.get_drive_progress(d.id)
        assert prog["total"] == 4
        assert prog["review_status"]["reviewed"] == 1
        assert prog["completion_pct"] == pytest.approx(25.0)


class TestGetDecisionManifest:
    def test_returns_only_reviewed(self, repo, db_conn):
        d = repo.create_drive("D")
        repo.create_entries_bulk([
            _make_entry_dict(d.id, f"/{i}.txt", f"{i}.txt") for i in range(3)
        ])
        all_e = repo.get_entries_by_drive(d.id)
        db_conn.execute(
            "UPDATE entries SET classification_status='ai_classified', "
            "review_status='reviewed', decision_status='include' WHERE id=?",
            (all_e[0].id,),
        )
        db_conn.commit()
        manifest = repo.get_decision_manifest(d.id)
        assert len(manifest) == 1
        assert manifest[0].review_status == "reviewed"

    def test_decision_status_filter(self, repo, db_conn):
        d = repo.create_drive("D")
        repo.create_entries_bulk([
            _make_entry_dict(d.id, f"/{i}.txt", f"{i}.txt") for i in range(2)
        ])
        all_e = repo.get_entries_by_drive(d.id)
        db_conn.execute(
            "UPDATE entries SET classification_status='ai_classified', "
            "review_status='reviewed', decision_status='include' WHERE id=?",
            (all_e[0].id,),
        )
        db_conn.execute(
            "UPDATE entries SET classification_status='ai_classified', "
            "review_status='reviewed', decision_status='exclude' WHERE id=?",
            (all_e[1].id,),
        )
        db_conn.commit()
        includes = repo.get_decision_manifest(d.id, filters={"decision_status": "include"})
        assert len(includes) == 1
        assert includes[0].decision_status == "include"


class TestGetChildEntries:
    def test_returns_children(self, repo):
        d = repo.create_drive("D")
        repo.create_entries_bulk([
            _make_entry_dict(d.id, "/parent", "parent", "folder"),
            _make_entry_dict(d.id, "/parent/child.txt", "child.txt", "file"),
            _make_entry_dict(d.id, "/parent/sub/deep.txt", "deep.txt", "file"),
            _make_entry_dict(d.id, "/other/file.txt", "file.txt", "file"),
        ])
        children = repo.get_child_entries(d.id, "/parent")
        paths = {c.path for c in children}
        assert "/parent/child.txt" in paths
        assert "/parent/sub/deep.txt" in paths
        assert "/other/file.txt" not in paths
        assert "/parent" not in paths

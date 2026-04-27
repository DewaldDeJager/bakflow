"""Tests for MCP server updates — wavefront tool, record_decision with descend,
manifest exclusion, review queue sort order, and cascade skip logic."""

import os
import tempfile

import pytest
import pytest_asyncio

from src.db.schema import init_db
from src.db.repository import Repository
from src.db.status import apply_transition
from src.mcp_server.server import (
    init_server,
    record_decision,
    get_decision_manifest,
    get_review_queue,
    mcp,
)


@pytest.fixture
def db_path():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def server(db_path):
    """Initialise the MCP server with a temp database."""
    init_db(db_path)
    return init_server(db_path)


@pytest.fixture
def repo(server):
    """Return the active repository after server init."""
    from src.mcp_server.server import get_repo
    return get_repo()


@pytest.fixture
def conn(server):
    """Return the active connection after server init."""
    from src.mcp_server.server import get_conn
    return get_conn()


def _create_drive(repo: Repository) -> str:
    """Helper: create a drive and return its id."""
    drive = repo.create_drive("Test Drive", volume_serial="TEST-001")
    return drive.id


def _create_entry(conn, drive_id: str, path: str, name: str, entry_type: str = "folder", **kwargs):
    """Helper: insert an entry directly and return its id."""
    conn.execute(
        "INSERT INTO entries (drive_id, path, original_path, name, entry_type, size_bytes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (drive_id, path, path, name, entry_type, kwargs.get("size_bytes", 0)),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM entries WHERE drive_id = ? AND path = ?",
        (drive_id, path),
    ).fetchone()
    return row[0]


def _classify_entry(conn, entry_id: int, folder_purpose: str = "project_or_work",
                     classification_confidence: float = 0.8,
                     decision_confidence: float = 0.7):
    """Helper: set an entry to ai_classified state with confidence values."""
    conn.execute(
        "UPDATE entries SET classification_status = 'ai_classified', "
        "folder_purpose = ?, classification_confidence = ?, decision_confidence = ? "
        "WHERE id = ?",
        (folder_purpose, classification_confidence, decision_confidence, entry_id),
    )
    conn.execute(
        "INSERT INTO audit_log (entry_id, dimension, old_value, new_value) "
        "VALUES (?, 'classification_status', 'unclassified', 'ai_classified')",
        (entry_id,),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 9.7a: Wavefront tool exists
# ---------------------------------------------------------------------------

class TestWavefrontToolRegistration:
    def test_run_wavefront_classification_is_registered(self, server):
        """Verify run_wavefront_classification is registered as an MCP tool."""
        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
        assert "run_wavefront_classification" in tool_names


# ---------------------------------------------------------------------------
# 9.7b: record_decision accepts descend for folders
# ---------------------------------------------------------------------------

class TestRecordDecisionDescend:
    @pytest.mark.asyncio
    async def test_descend_accepted_for_folder(self, server, repo, conn):
        """A folder entry should accept 'descend' as a valid decision."""
        drive_id = _create_drive(repo)
        entry_id = _create_entry(conn, drive_id, "C:/Projects", "Projects", "folder")
        _classify_entry(conn, entry_id)

        result = await record_decision(entry_id=entry_id, decision="descend")
        assert "error" not in result
        assert result["entry"]["decision_status"] == "descend"

    @pytest.mark.asyncio
    async def test_descend_rejected_for_file(self, server, repo, conn):
        """A file entry should reject 'descend' as a decision."""
        drive_id = _create_drive(repo)
        entry_id = _create_entry(conn, drive_id, "C:/Projects/readme.txt", "readme.txt", "file")
        _classify_entry(conn, entry_id, folder_purpose=None)
        # Set file_class instead for file entries
        conn.execute(
            "UPDATE entries SET file_class = 'document' WHERE id = ?", (entry_id,)
        )
        conn.commit()

        result = await record_decision(entry_id=entry_id, decision="descend")
        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"
        assert "folder" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# 9.7c: Manifest excludes descend
# ---------------------------------------------------------------------------

class TestManifestExcludesDescend:
    @pytest.mark.asyncio
    async def test_manifest_excludes_descend_entries(self, server, repo, conn):
        """Entries with decision_status='descend' should never appear in the manifest."""
        drive_id = _create_drive(repo)

        # Create entries with various decision statuses
        include_id = _create_entry(conn, drive_id, "C:/Include", "Include", "folder")
        exclude_id = _create_entry(conn, drive_id, "C:/Exclude", "Exclude", "folder")
        descend_id = _create_entry(conn, drive_id, "C:/Descend", "Descend", "folder")

        for eid in (include_id, exclude_id, descend_id):
            _classify_entry(conn, eid)

        # Record decisions
        await record_decision(entry_id=include_id, decision="include")
        await record_decision(entry_id=exclude_id, decision="exclude")
        await record_decision(entry_id=descend_id, decision="descend")

        # Get manifest with no filter (should still exclude descend)
        result = await get_decision_manifest(drive_id=drive_id, decision_filter=None)
        entry_ids = [e["id"] for e in result["entries"]]

        assert include_id in entry_ids
        assert exclude_id in entry_ids
        assert descend_id not in entry_ids

    @pytest.mark.asyncio
    async def test_manifest_rejects_descend_filter(self, server, repo, conn):
        """Passing decision_filter='descend' should return an error."""
        drive_id = _create_drive(repo)
        result = await get_decision_manifest(drive_id=drive_id, decision_filter="descend")
        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"


# ---------------------------------------------------------------------------
# 9.7d: Review queue sorts by decision_confidence
# ---------------------------------------------------------------------------

class TestReviewQueueSortOrder:
    @pytest.mark.asyncio
    async def test_sorted_by_decision_confidence_asc_nulls_first(self, server, repo, conn):
        """Review queue should sort by decision_confidence ASC with NULLs first."""
        drive_id = _create_drive(repo)

        # Create entries with different decision_confidence values
        e_null = _create_entry(conn, drive_id, "C:/NullConf", "NullConf", "folder")
        e_low = _create_entry(conn, drive_id, "C:/LowConf", "LowConf", "folder")
        e_high = _create_entry(conn, drive_id, "C:/HighConf", "HighConf", "folder")

        # Classify with different decision_confidence values
        _classify_entry(conn, e_high, decision_confidence=0.9)
        _classify_entry(conn, e_low, decision_confidence=0.3)
        # e_null: classified but with NULL decision_confidence
        conn.execute(
            "UPDATE entries SET classification_status = 'ai_classified', "
            "folder_purpose = 'project_or_work', classification_confidence = 0.8, "
            "decision_confidence = NULL WHERE id = ?",
            (e_null,),
        )
        conn.execute(
            "INSERT INTO audit_log (entry_id, dimension, old_value, new_value) "
            "VALUES (?, 'classification_status', 'unclassified', 'ai_classified')",
            (e_null,),
        )
        conn.commit()

        result = await get_review_queue(drive_id=drive_id)
        entries = result["entries"]
        assert len(entries) == 3

        # NULL should come first, then 0.3, then 0.9
        assert entries[0]["id"] == e_null
        assert entries[1]["id"] == e_low
        assert entries[2]["id"] == e_high


# ---------------------------------------------------------------------------
# 9.7e: Cascade skips reviewed children
# ---------------------------------------------------------------------------

class TestCascadeSkipsReviewed:
    @pytest.mark.asyncio
    async def test_cascade_skips_reviewed_children(self, server, repo, conn):
        """Cascade should skip children where review_status='reviewed'."""
        drive_id = _create_drive(repo)

        # Create parent folder
        parent_id = _create_entry(conn, drive_id, "C:/Parent", "Parent", "folder")
        _classify_entry(conn, parent_id)

        # Create child entries under parent
        child_pending_id = _create_entry(
            conn, drive_id, "C:/Parent/child_pending", "child_pending", "folder"
        )
        child_reviewed_id = _create_entry(
            conn, drive_id, "C:/Parent/child_reviewed", "child_reviewed", "folder"
        )

        # Classify both children
        _classify_entry(conn, child_pending_id)
        _classify_entry(conn, child_reviewed_id)

        # Mark one child as reviewed with its own decision
        apply_transition(conn, child_reviewed_id, "review_status", "reviewed")
        apply_transition(conn, child_reviewed_id, "decision_status", "exclude")

        # Now record parent decision with cascade
        result = await record_decision(
            entry_id=parent_id,
            decision="include",
            cascade_to_children=True,
        )

        assert "cascade" in result
        cascade = result["cascade"]

        # child_reviewed should be skipped (already reviewed by human)
        assert cascade["skipped"] >= 1
        skipped_ids = [r["entry_id"] for r in cascade["skip_reasons"]]
        assert child_reviewed_id in skipped_ids

        # child_pending should be updated
        assert cascade["updated"] >= 1

        # Verify the pending child got the cascaded decision
        pending_entry = repo.get_entry(child_pending_id)
        assert pending_entry.decision_status == "include"

        # Verify the reviewed child kept its original decision
        reviewed_entry = repo.get_entry(child_reviewed_id)
        assert reviewed_entry.decision_status == "exclude"

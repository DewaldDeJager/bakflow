"""Integration tests for MCP server tool registration and concurrent access.

Verifies:
- All 8 tools are registered and callable via FastMCP
- Each tool has the expected name and input schema
- Concurrent MCP tool calls with database-level locking don't corrupt data

Requirements: 6.1, 6.5
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from src.db.schema import init_db
from src.db.repository import Repository
from src.db.status import apply_transition
import src.mcp_server.server as server_mod
from src.mcp_server.server import mcp


# ---------------------------------------------------------------------------
# Expected tools — the 8 MCP tools from Requirement 6.1
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "list_drives",
    "get_unclassified_batch",
    "get_folder_summary",
    "submit_classification",
    "classify_batch",
    "get_review_queue",
    "record_decision",
    "get_drive_progress",
    "get_decision_manifest",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_env():
    """Create a temp DB, wire up the server module, yield (conn, repo, path)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    repo = Repository(conn)
    server_mod._conn = conn
    server_mod._repo = repo
    yield conn, repo, path
    conn.close()
    os.unlink(path)


def _seed_drive_with_entries(repo, conn, count=5):
    """Create a drive with classified entries ready for review/decisions."""
    drive = repo.create_drive(label="integration-test-drive")
    entries_data = []
    for i in range(count):
        entries_data.append({
            "drive_id": drive.id,
            "path": f"/test/file_{i}.txt",
            "name": f"file_{i}.txt",
            "entry_type": "file",
            "extension": ".txt",
            "size_bytes": (i + 1) * 100,
        })
    # Add a folder entry
    entries_data.append({
        "drive_id": drive.id,
        "path": "/test",
        "name": "test",
        "entry_type": "folder",
        "size_bytes": 0,
    })
    repo.create_entries_bulk(entries_data)
    entries = repo.get_entries_by_drive(drive.id)

    # Classify all entries so they're ready for review
    for entry in entries:
        if entry.entry_type == "file":
            conn.execute(
                "UPDATE entries SET file_class = ?, confidence = ? WHERE id = ?",
                ("document", 0.85, entry.id),
            )
        else:
            conn.execute(
                "UPDATE entries SET folder_purpose = ?, confidence = ? WHERE id = ?",
                ("project_or_work", 0.9, entry.id),
            )
        conn.commit()
        apply_transition(conn, entry.id, "classification_status", "ai_classified")

    return drive, repo.get_entries_by_drive(drive.id)


# ---------------------------------------------------------------------------
# Tool Registration Tests
# ---------------------------------------------------------------------------

class TestToolRegistration:
    """Verify all 9 tools are registered and discoverable via FastMCP."""

    def test_all_nine_tools_registered(self):
        """list_tools returns exactly the 9 expected tools."""
        tools = asyncio.run(mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert tool_names == EXPECTED_TOOLS

    def test_each_tool_has_input_schema(self):
        """Every registered tool has a non-empty input schema."""
        tools = asyncio.run(mcp.list_tools())
        for tool in tools:
            assert tool.inputSchema is not None, f"{tool.name} missing inputSchema"
            assert "properties" in tool.inputSchema, (
                f"{tool.name} schema missing 'properties'"
            )

    def test_tool_names_are_unique(self):
        """No duplicate tool names."""
        tools = asyncio.run(mcp.list_tools())
        names = [t.name for t in tools]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Tool Callability Tests
# ---------------------------------------------------------------------------

class TestToolCallability:
    """Verify each tool is callable via mcp.call_tool and returns structured responses."""

    def test_get_unclassified_batch_callable(self, db_env):
        conn, repo, _ = db_env
        drive = repo.create_drive(label="test")
        result = asyncio.run(
            mcp.call_tool("get_unclassified_batch", {"drive_id": drive.id})
        )
        parsed = _parse_call_result(result)
        assert "entries" in parsed
        assert parsed["drive_id"] == drive.id

    def test_get_folder_summary_callable(self, db_env):
        conn, repo, _ = db_env
        drive = repo.create_drive(label="test")
        repo.create_entries_bulk([{
            "drive_id": drive.id,
            "path": "/root/file.txt",
            "name": "file.txt",
            "entry_type": "file",
            "extension": ".txt",
            "size_bytes": 42,
        }])
        result = asyncio.run(
            mcp.call_tool("get_folder_summary", {"drive_id": drive.id, "path": "/root"})
        )
        parsed = _parse_call_result(result)
        assert "file_count" in parsed

    def test_submit_classification_callable(self, db_env):
        conn, repo, _ = db_env
        drive = repo.create_drive(label="test")
        repo.create_entries_bulk([{
            "drive_id": drive.id,
            "path": "/f.txt",
            "name": "f.txt",
            "entry_type": "file",
            "size_bytes": 10,
        }])
        entry = repo.get_entries_by_drive(drive.id)[0]
        result = asyncio.run(
            mcp.call_tool("submit_classification", {
                "classifications": [{
                    "entry_id": entry.id,
                    "file_class": "document",
                    "confidence": 0.9,
                    "reasoning": "test",
                }],
            })
        )
        parsed = _parse_call_result(result)
        assert parsed["submitted"] == 1

    def test_get_review_queue_callable(self, db_env):
        conn, repo, _ = db_env
        drive = repo.create_drive(label="test")
        result = asyncio.run(
            mcp.call_tool("get_review_queue", {"drive_id": drive.id})
        )
        parsed = _parse_call_result(result)
        assert "entries" in parsed

    def test_record_decision_callable(self, db_env):
        conn, repo, _ = db_env
        drive, entries = _seed_drive_with_entries(repo, conn, count=1)
        entry = entries[0]
        result = asyncio.run(
            mcp.call_tool("record_decision", {
                "entry_id": entry.id,
                "decision": "include",
            })
        )
        parsed = _parse_call_result(result)
        assert "entry" in parsed

    def test_get_drive_progress_callable(self, db_env):
        conn, repo, _ = db_env
        drive = repo.create_drive(label="test")
        result = asyncio.run(
            mcp.call_tool("get_drive_progress", {"drive_id": drive.id})
        )
        parsed = _parse_call_result(result)
        assert parsed["drive_id"] == drive.id

    def test_get_decision_manifest_callable(self, db_env):
        conn, repo, _ = db_env
        drive = repo.create_drive(label="test")
        result = asyncio.run(
            mcp.call_tool("get_decision_manifest", {"drive_id": drive.id})
        )
        parsed = _parse_call_result(result)
        assert "entries" in parsed


# ---------------------------------------------------------------------------
# Concurrent Access Tests
# ---------------------------------------------------------------------------

class TestConcurrentAccess:
    """Verify concurrent MCP tool calls don't corrupt data (Req 6.5)."""

    def test_concurrent_classifications_no_data_loss(self, db_env):
        """Multiple concurrent submit_classification calls all succeed."""
        conn, repo, _ = db_env
        drive = repo.create_drive(label="concurrent-test")
        entry_count = 10
        entries_data = [
            {
                "drive_id": drive.id,
                "path": f"/concurrent/file_{i}.txt",
                "name": f"file_{i}.txt",
                "entry_type": "file",
                "size_bytes": 100,
            }
            for i in range(entry_count)
        ]
        repo.create_entries_bulk(entries_data)
        entries = repo.get_entries_by_drive(drive.id)

        async def classify_one(entry_id: int):
            return await mcp.call_tool("submit_classification", {
                "classifications": [{
                    "entry_id": entry_id,
                    "file_class": "document",
                    "confidence": 0.8,
                    "reasoning": "concurrent test",
                }],
            })

        async def run_all():
            tasks = [classify_one(e.id) for e in entries]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_all())

        # All should succeed
        total_submitted = 0
        for r in results:
            parsed = _parse_call_result(r)
            total_submitted += parsed.get("submitted", 0)
        assert total_submitted == entry_count

        # Verify all entries are now classified
        updated = repo.get_entries_by_drive(drive.id)
        classified = [e for e in updated if e.classification_status == "ai_classified"]
        assert len(classified) == entry_count

    def test_concurrent_decisions_no_corruption(self, db_env):
        """Multiple concurrent record_decision calls on different entries."""
        conn, repo, _ = db_env
        drive, entries = _seed_drive_with_entries(repo, conn, count=8)
        file_entries = [e for e in entries if e.entry_type == "file"]

        decisions = ["include", "exclude", "defer"]

        async def decide_one(entry_id: int, decision: str):
            return await mcp.call_tool("record_decision", {
                "entry_id": entry_id,
                "decision": decision,
            })

        async def run_all():
            tasks = [
                decide_one(e.id, decisions[i % len(decisions)])
                for i, e in enumerate(file_entries)
            ]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_all())

        # All should return an entry (no errors)
        for r in results:
            parsed = _parse_call_result(r)
            assert "entry" in parsed, f"Expected entry in result, got: {parsed}"

        # Verify each entry got the expected decision
        for i, entry in enumerate(file_entries):
            updated = repo.get_entry(entry.id)
            expected = decisions[i % len(decisions)]
            assert updated.decision_status == expected
            assert updated.review_status == "reviewed"

    def test_concurrent_reads_during_writes(self, db_env):
        """Read operations (progress, queue) work while writes happen."""
        conn, repo, _ = db_env
        drive, entries = _seed_drive_with_entries(repo, conn, count=5)
        file_entries = [e for e in entries if e.entry_type == "file"]

        async def read_progress():
            return await mcp.call_tool(
                "get_drive_progress", {"drive_id": drive.id}
            )

        async def read_queue():
            return await mcp.call_tool(
                "get_review_queue", {"drive_id": drive.id}
            )

        async def write_decision(entry_id: int):
            return await mcp.call_tool("record_decision", {
                "entry_id": entry_id,
                "decision": "include",
            })

        async def run_mixed():
            tasks = []
            # Interleave reads and writes
            for e in file_entries:
                tasks.append(read_progress())
                tasks.append(write_decision(e.id))
                tasks.append(read_queue())
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_mixed())

        # No exceptions — all calls returned structured data
        for r in results:
            parsed = _parse_call_result(r)
            assert isinstance(parsed, dict)

    def test_concurrent_manifest_reads(self, db_env):
        """Multiple concurrent get_decision_manifest calls return consistent data."""
        conn, repo, _ = db_env
        drive, entries = _seed_drive_with_entries(repo, conn, count=3)
        file_entries = [e for e in entries if e.entry_type == "file"]

        # Decide all entries first
        for e in file_entries:
            apply_transition(conn, e.id, "review_status", "reviewed")
            apply_transition(conn, e.id, "decision_status", "include")

        async def read_manifest():
            return await mcp.call_tool(
                "get_decision_manifest", {"drive_id": drive.id}
            )

        async def run_all():
            return await asyncio.gather(*[read_manifest() for _ in range(10)])

        results = asyncio.run(run_all())

        # All should return the same count
        counts = set()
        for r in results:
            parsed = _parse_call_result(r)
            counts.add(parsed["count"])
        assert len(counts) == 1, f"Inconsistent manifest counts: {counts}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_call_result(result) -> dict:
    """Parse the TextContent response from mcp.call_tool into a dict."""
    # call_tool returns a list of ContentBlock (typically TextContent)
    assert len(result) > 0, "call_tool returned empty result"
    text = result[0].text
    return json.loads(text)

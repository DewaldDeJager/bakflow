"""Property-based tests for MCP parameter validation (P20).

Property 20: For any tool with missing/invalid parameters, returns structured
error response rather than unhandled exception.

Validates: Requirements 6.2
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository
from src.mcp_server.server import (
    get_unclassified_batch,
    get_folder_summary,
    submit_classification,
    get_review_queue,
    record_decision,
    get_drive_progress,
    get_decision_manifest,
)
import src.mcp_server.server as server_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    repo = Repository(conn)
    server_mod._conn = conn
    server_mod._repo = repo
    return conn, repo, path


def _assert_error_response(result):
    """Assert the result is a structured error response."""
    assert isinstance(result, dict)
    assert "error" in result
    assert "code" in result["error"]
    assert "message" in result["error"]


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# Feature: bakflow, Property 20: MCP tool parameter validation

class TestMCPParameterValidation:
    """P20: MCP tool parameter validation."""

    @given(
        drive_id=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=50,
        ),
    )
    @settings(max_examples=50)
    def test_nonexistent_drive_returns_error(self, drive_id):
        """Tools return structured error for nonexistent drive IDs."""
        conn, repo, path = _make_temp_db()
        try:
            # get_unclassified_batch
            result = asyncio.run(
                get_unclassified_batch(drive_id=drive_id)
            )
            _assert_error_response(result)

            # get_folder_summary
            result = asyncio.run(
                get_folder_summary(drive_id=drive_id, path="/some/path")
            )
            _assert_error_response(result)

            # get_review_queue
            result = asyncio.run(
                get_review_queue(drive_id=drive_id)
            )
            _assert_error_response(result)

            # get_drive_progress
            result = asyncio.run(
                get_drive_progress(drive_id=drive_id)
            )
            _assert_error_response(result)

            # get_decision_manifest
            result = asyncio.run(
                get_decision_manifest(drive_id=drive_id)
            )
            _assert_error_response(result)
        finally:
            conn.close()
            os.unlink(path)

    def test_empty_drive_id_returns_error(self):
        """Tools return structured error for empty drive_id."""
        conn, repo, path = _make_temp_db()
        try:
            for tool_fn in [
                lambda: get_unclassified_batch(drive_id=""),
                lambda: get_folder_summary(drive_id="", path="/x"),
                lambda: get_review_queue(drive_id=""),
                lambda: get_drive_progress(drive_id=""),
                lambda: get_decision_manifest(drive_id=""),
            ]:
                result = asyncio.run(tool_fn())
                _assert_error_response(result)
        finally:
            conn.close()
            os.unlink(path)

    def test_empty_path_returns_error(self):
        """get_folder_summary returns error for empty path."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(label="test")
            result = asyncio.run(
                get_folder_summary(drive_id=drive.id, path="")
            )
            _assert_error_response(result)
        finally:
            conn.close()
            os.unlink(path)

    def test_invalid_batch_size_returns_error(self):
        """get_unclassified_batch returns error for batch_size < 1."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(label="test")
            result = asyncio.run(
                get_unclassified_batch(drive_id=drive.id, batch_size=0)
            )
            _assert_error_response(result)

            result = asyncio.run(
                get_unclassified_batch(drive_id=drive.id, batch_size=-5)
            )
            _assert_error_response(result)
        finally:
            conn.close()
            os.unlink(path)

    @given(
        decision=st.text(
            alphabet=st.characters(whitelist_categories=("L",)),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s not in ("include", "exclude", "defer")),
    )
    @settings(max_examples=50)
    def test_invalid_decision_returns_error(self, decision):
        """record_decision returns error for invalid decision values."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(label="test")
            repo.create_entries_bulk([{
                "drive_id": drive.id,
                "path": "/file.txt",
                "name": "file.txt",
                "entry_type": "file",
                "size_bytes": 100,
            }])
            entries = repo.get_entries_by_drive(drive.id)
            entry_id = entries[0].id

            result = asyncio.run(
                record_decision(entry_id=entry_id, decision=decision)
            )
            _assert_error_response(result)
        finally:
            conn.close()
            os.unlink(path)

    @given(
        entry_id=st.integers(min_value=9000, max_value=99999),
    )
    @settings(max_examples=50)
    def test_nonexistent_entry_returns_error(self, entry_id):
        """record_decision returns error for nonexistent entry_id."""
        conn, repo, path = _make_temp_db()
        try:
            result = asyncio.run(
                record_decision(entry_id=entry_id, decision="include")
            )
            _assert_error_response(result)
        finally:
            conn.close()
            os.unlink(path)

    def test_empty_classifications_returns_error(self):
        """submit_classification returns error for empty list."""
        conn, repo, path = _make_temp_db()
        try:
            result = asyncio.run(
                submit_classification(classifications=[])
            )
            _assert_error_response(result)
        finally:
            conn.close()
            os.unlink(path)

    def test_classification_missing_entry_id_reports_failure(self):
        """submit_classification reports failure for items missing entry_id."""
        conn, repo, path = _make_temp_db()
        try:
            result = asyncio.run(
                submit_classification(classifications=[
                    {"file_class": "document", "confidence": 0.9}
                ])
            )
            # Should not raise — returns structured result with failure count
            assert result["failed"] >= 1
        finally:
            conn.close()
            os.unlink(path)

    def test_classification_invalid_confidence_reports_failure(self):
        """submit_classification reports failure for out-of-range confidence."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(label="test")
            repo.create_entries_bulk([{
                "drive_id": drive.id,
                "path": "/file.txt",
                "name": "file.txt",
                "entry_type": "file",
                "size_bytes": 100,
            }])
            entries = repo.get_entries_by_drive(drive.id)
            entry_id = entries[0].id

            result = asyncio.run(
                submit_classification(classifications=[
                    {"entry_id": entry_id, "file_class": "document", "confidence": 1.5}
                ])
            )
            assert result["failed"] >= 1
        finally:
            conn.close()
            os.unlink(path)

    def test_negative_limit_returns_error(self):
        """get_review_queue returns error for negative limit."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(label="test")
            result = asyncio.run(
                get_review_queue(drive_id=drive.id, limit=-1)
            )
            _assert_error_response(result)
        finally:
            conn.close()
            os.unlink(path)

    def test_negative_offset_returns_error(self):
        """get_review_queue returns error for negative offset."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(label="test")
            result = asyncio.run(
                get_review_queue(drive_id=drive.id, offset=-1)
            )
            _assert_error_response(result)
        finally:
            conn.close()
            os.unlink(path)

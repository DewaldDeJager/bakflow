"""Tests for db/models.py — Pydantic model validation for wavefront classification."""

import sqlite3
import os
import tempfile
from datetime import datetime

import pytest
from hypothesis import given, settings, strategies as st
from pydantic import ValidationError

from src.db.models import (
    DecisionStatus,
    Entry,
    FileClassification,
    FolderClassification,
    WavefrontFolderClassification,
    WavefrontFolderSummary,
    WavefrontProgress,
    WavefrontResult,
)
from src.db.schema import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    conn.execute("INSERT INTO drives (id, label) VALUES ('d1', 'Test')")
    conn.commit()
    yield conn
    conn.close()
    os.unlink(path)


_NOW = datetime(2024, 6, 15, 12, 0, 0)

def _make_entry(**overrides) -> Entry:
    defaults = dict(
        id=1,
        drive_id="d1",
        path="/test/file.txt",
        name="file.txt",
        entry_type="file",
        size_bytes=1024,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return Entry(**defaults)


# ---------------------------------------------------------------------------
# 2.1 DecisionStatus includes 'descend'
# ---------------------------------------------------------------------------

def test_decision_status_includes_descend():
    """DecisionStatus literal accepts 'descend'."""
    entry = _make_entry(entry_type="folder", decision_status="descend")
    assert entry.decision_status == "descend"


def test_decision_status_all_values():
    for status in ("undecided", "include", "exclude", "defer", "descend"):
        entry = _make_entry(entry_type="folder", decision_status=status)
        assert entry.decision_status == status


# ---------------------------------------------------------------------------
# 2.2 Entry model: renamed confidence, new fields
# ---------------------------------------------------------------------------

def test_entry_has_classification_confidence():
    entry = _make_entry(classification_confidence=0.85)
    assert entry.classification_confidence == 0.85


def test_entry_has_decision_confidence():
    entry = _make_entry(decision_confidence=0.72)
    assert entry.decision_confidence == 0.72


def test_entry_confidence_fields_default_none():
    entry = _make_entry()
    assert entry.classification_confidence is None
    assert entry.decision_confidence is None


def test_entry_tree_metadata_defaults_none():
    entry = _make_entry()
    assert entry.depth is None
    assert entry.parent_path is None
    assert entry.child_count is None
    assert entry.descendant_file_count is None
    assert entry.descendant_folder_count is None


def test_entry_tree_metadata_accepts_values():
    entry = _make_entry(
        entry_type="folder",
        depth=3,
        parent_path="/test",
        child_count=5,
        descendant_file_count=100,
        descendant_folder_count=10,
    )
    assert entry.depth == 3
    assert entry.parent_path == "/test"
    assert entry.child_count == 5
    assert entry.descendant_file_count == 100
    assert entry.descendant_folder_count == 10


def test_entry_roundtrip_through_sqlite(db_conn):
    """Entry with all new fields round-trips through SQLite correctly."""
    db_conn.execute(
        """INSERT INTO entries (
            drive_id, path, name, entry_type, size_bytes,
            classification_confidence, decision_confidence,
            depth, parent_path, child_count,
            descendant_file_count, descendant_folder_count,
            decision_status
        ) VALUES (
            'd1', '/projects', 'projects', 'folder', 4096,
            0.9, 0.75,
            1, '/', 3, 50, 5,
            'descend'
        )""",
    )
    db_conn.commit()

    row = db_conn.execute("SELECT * FROM entries WHERE path = '/projects'").fetchone()
    col_names = [desc[0] for desc in db_conn.execute("SELECT * FROM entries LIMIT 0").description]
    data = dict(zip(col_names, row))
    data["priority_review"] = bool(data.get("priority_review", 0))
    entry = Entry.model_validate(data)

    assert entry.classification_confidence == 0.9
    assert entry.decision_confidence == 0.75
    assert entry.depth == 1
    assert entry.parent_path == "/"
    assert entry.child_count == 3
    assert entry.descendant_file_count == 50
    assert entry.descendant_folder_count == 5
    assert entry.decision_status == "descend"


# ---------------------------------------------------------------------------
# 2.3 WavefrontFolderClassification
# ---------------------------------------------------------------------------

def test_wavefront_folder_classification_valid():
    wfc = WavefrontFolderClassification(
        entry_id=1,
        folder_purpose="project_or_work",
        decision="descend",
        classification_confidence=0.85,
        decision_confidence=0.6,
        reasoning="Contains mixed project files, need to inspect children.",
    )
    assert wfc.decision == "descend"
    assert wfc.classification_confidence == 0.85


def test_wavefront_folder_classification_validates_decision():
    with pytest.raises(ValidationError):
        WavefrontFolderClassification(
            entry_id=1,
            folder_purpose="project_or_work",
            decision="defer",  # not a valid wavefront decision
            classification_confidence=0.8,
            decision_confidence=0.7,
            reasoning="test",
        )


@settings(max_examples=100)
@given(
    conf=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    dec_conf=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_wavefront_folder_classification_confidence_range(conf, dec_conf):
    """Both confidence fields accept any value in [0.0, 1.0]."""
    wfc = WavefrontFolderClassification(
        entry_id=1,
        folder_purpose="system_or_temp",
        decision="exclude",
        classification_confidence=conf,
        decision_confidence=dec_conf,
        reasoning="test",
    )
    assert 0.0 <= wfc.classification_confidence <= 1.0
    assert 0.0 <= wfc.decision_confidence <= 1.0


def test_wavefront_folder_classification_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        WavefrontFolderClassification(
            entry_id=1,
            folder_purpose="system_or_temp",
            decision="exclude",
            classification_confidence=1.5,
            decision_confidence=0.5,
            reasoning="test",
        )
    with pytest.raises(ValidationError):
        WavefrontFolderClassification(
            entry_id=1,
            folder_purpose="system_or_temp",
            decision="exclude",
            classification_confidence=0.5,
            decision_confidence=-0.1,
            reasoning="test",
        )


# ---------------------------------------------------------------------------
# 2.4 WavefrontFolderSummary
# ---------------------------------------------------------------------------

def test_wavefront_folder_summary_none_parent_context():
    wfs = WavefrontFolderSummary(
        entry_id=1,
        path="/projects",
        name="projects",
        depth=1,
        size_bytes=4096,
        file_type_distribution={".py": 10, ".txt": 3},
        subfolder_names=["src", "tests"],
        parent_classification=None,
        parent_decision=None,
    )
    assert wfs.parent_classification is None
    assert wfs.parent_decision is None


def test_wavefront_folder_summary_with_parent_context():
    wfs = WavefrontFolderSummary(
        entry_id=2,
        path="/projects/myapp",
        name="myapp",
        depth=2,
        size_bytes=8192,
        child_count=5,
        descendant_file_count=100,
        descendant_folder_count=10,
        file_type_distribution={".py": 50},
        subfolder_names=["src"],
        parent_classification="project_or_work",
        parent_decision="descend",
    )
    assert wfs.parent_classification == "project_or_work"
    assert wfs.parent_decision == "descend"
    assert wfs.child_count == 5


# ---------------------------------------------------------------------------
# 2.5 WavefrontProgress and WavefrontResult
# ---------------------------------------------------------------------------

def test_wavefront_progress_construction():
    wp = WavefrontProgress(
        current_depth=2,
        max_depth=5,
        folders_classified=15,
        folders_pruned=3,
        files_classified=0,
        total_folders=20,
        total_files=100,
        estimated_llm_calls_saved=500,
    )
    assert wp.current_depth == 2
    assert wp.estimated_llm_calls_saved == 500


def test_wavefront_progress_none_max_depth():
    wp = WavefrontProgress(
        current_depth=0,
        max_depth=None,
        folders_classified=0,
        folders_pruned=0,
        files_classified=0,
        total_folders=10,
        total_files=50,
        estimated_llm_calls_saved=0,
    )
    assert wp.max_depth is None


def test_wavefront_result_construction():
    wr = WavefrontResult(
        drive_id="d1",
        depths_processed=3,
        folders_classified=20,
        folders_pruned=5,
        files_classified=100,
        files_skipped=50,
        total_llm_calls=25,
        estimated_calls_saved=200,
    )
    assert wr.drive_id == "d1"
    assert wr.errors == []


def test_wavefront_result_with_errors():
    wr = WavefrontResult(
        drive_id="d1",
        depths_processed=1,
        folders_classified=5,
        folders_pruned=0,
        files_classified=0,
        files_skipped=0,
        total_llm_calls=5,
        estimated_calls_saved=0,
        errors=["LLM timeout for entry 42"],
    )
    assert len(wr.errors) == 1


# ---------------------------------------------------------------------------
# 2.6 & 2.7 FolderClassification and FileClassification renamed field
# ---------------------------------------------------------------------------

def test_folder_classification_uses_classification_confidence():
    fc = FolderClassification(
        entry_id=1,
        folder_purpose="project_or_work",
        classification_confidence=0.9,
        reasoning="test",
    )
    assert fc.classification_confidence == 0.9


def test_file_classification_uses_classification_confidence():
    fc = FileClassification(
        entry_id=1,
        file_class="document",
        classification_confidence=0.8,
        reasoning="test",
    )
    assert fc.classification_confidence == 0.8

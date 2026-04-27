"""Tests for prompt builders — focused on build_wavefront_folder_prompt."""

from __future__ import annotations

import pytest

from src.classifier.prompts import (
    FOLDER_PURPOSE_TAXONOMY,
    build_wavefront_folder_prompt,
)
from src.db.models import WavefrontFolderSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_summary(**overrides) -> WavefrontFolderSummary:
    """Create a WavefrontFolderSummary with sensible defaults."""
    defaults = dict(
        entry_id=42,
        path="C:\\Users\\alice\\Documents",
        name="Documents",
        depth=2,
        size_bytes=1_048_576,
        child_count=10,
        descendant_file_count=150,
        descendant_folder_count=5,
        file_type_distribution={".pdf": 30, ".docx": 20},
        subfolder_names=["Work", "Personal"],
        parent_classification=None,
        parent_decision=None,
    )
    defaults.update(overrides)
    return WavefrontFolderSummary(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWavefrontFolderPrompt:
    """Tests for build_wavefront_folder_prompt."""

    def test_taxonomy_present(self):
        """Verify the prompt contains all folder purpose taxonomy keys."""
        prompt = build_wavefront_folder_prompt(_make_summary())
        for key in FOLDER_PURPOSE_TAXONOMY:
            assert key in prompt, f"Taxonomy key '{key}' missing from prompt"

    def test_triage_signals_present(self):
        """Verify the prompt contains include, exclude, descend signal explanations."""
        prompt = build_wavefront_folder_prompt(_make_summary())
        assert "include" in prompt
        assert "exclude" in prompt
        assert "descend" in prompt
        # Check the explanations are present, not just the bare words
        assert "Back up the entire subtree" in prompt
        assert "Skip the entire subtree" in prompt
        assert "Classify children individually" in prompt

    def test_tree_metadata_with_values(self):
        """When tree metadata fields are set, verify they appear in the prompt."""
        summary = _make_summary(
            child_count=10,
            descendant_file_count=150,
            descendant_folder_count=5,
        )
        prompt = build_wavefront_folder_prompt(summary)
        assert "child_count: 10" in prompt
        assert "descendant_file_count: 150" in prompt
        assert "descendant_folder_count: 5" in prompt

    def test_tree_metadata_with_none(self):
        """When tree metadata fields are None, verify 'unknown' appears."""
        summary = _make_summary(
            child_count=None,
            descendant_file_count=None,
            descendant_folder_count=None,
        )
        prompt = build_wavefront_folder_prompt(summary)
        assert "child_count: unknown" in prompt
        assert "descendant_file_count: unknown" in prompt
        assert "descendant_folder_count: unknown" in prompt

    def test_parent_context_included(self):
        """When parent_classification is set, verify parent context section appears."""
        summary = _make_summary(
            parent_classification="project_or_work",
            parent_decision="descend",
        )
        prompt = build_wavefront_folder_prompt(summary)
        assert "Parent Folder Context" in prompt
        assert "project_or_work" in prompt
        assert "Parent decision: descend" in prompt

    def test_parent_context_excluded(self):
        """When parent_classification is None, verify no parent context section."""
        summary = _make_summary(parent_classification=None)
        prompt = build_wavefront_folder_prompt(summary)
        assert "Parent Folder Context" not in prompt

    def test_json_fields_requested(self):
        """Verify the prompt requests all required JSON output fields."""
        prompt = build_wavefront_folder_prompt(_make_summary())
        for field in [
            "entry_id",
            "folder_purpose",
            "decision",
            "classification_confidence",
            "decision_confidence",
            "reasoning",
        ]:
            assert field in prompt, f"JSON field '{field}' missing from prompt"

    def test_folder_metadata_present(self):
        """Verify entry_id, path, name appear in the prompt."""
        summary = _make_summary(
            entry_id=99,
            path="D:\\Photos\\Vacation",
            name="Vacation",
        )
        prompt = build_wavefront_folder_prompt(summary)
        assert "entry_id: 99" in prompt
        assert "D:\\Photos\\Vacation" in prompt
        assert "name: Vacation" in prompt

"""Wavefront classifier — tree-aware BFS traversal for folder classification.

Replaces flat batch classification with a depth-first wavefront that classifies
folders top-down by depth level.  Each folder receives a triage signal:
``include`` (back up entire subtree), ``exclude`` (skip entire subtree), or
``descend`` (classify children individually).  Pruned subtrees are never sent
to the LLM, dramatically reducing API calls for large drives.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from itertools import batched
from typing import Callable

from src.classifier.provider import LLMProvider
from src.db.models import (
    Entry,
    FileClassification,
    FileSummary,
    WavefrontFolderClassification,
    WavefrontFolderSummary,
    WavefrontProgress,
    WavefrontResult,
)
from src.db.repository import Repository
from src.db.status import apply_transition

logger = logging.getLogger(__name__)


@dataclass
class WavefrontConfig:
    """Configuration for wavefront classification."""

    max_depth: int | None = None        # None = no limit, process all depths
    classify_files: bool = True          # Whether to classify individual files after folder pass
    batch_size: int = 10                 # Folders per LLM call
    confidence_threshold: float = 0.7    # Below this → priority_review


class WavefrontClassifier:
    """Orchestrates tree-aware BFS classification of folders via an LLM provider."""

    def __init__(
        self,
        provider: LLMProvider,
        repo: Repository,
        conn: sqlite3.Connection,
        config: WavefrontConfig,
    ) -> None:
        self._provider = provider
        self._repo = repo
        self._conn = conn
        self._config = config

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    async def classify(
        self,
        drive_id: str,
        progress_callback: Callable[[WavefrontProgress], None] | None = None,
    ) -> WavefrontResult:
        """Run the full wavefront classification for a drive.

        BFS traversal by depth level (0, 1, 2, …).  Within each depth,
        folders are sorted by ``descendant_file_count`` DESC so that
        large-subtree pruning decisions happen first.

        After the folder pass, optionally classifies remaining files
        (controlled by ``config.classify_files``).
        """
        result = WavefrontResult(
            drive_id=drive_id,
            depths_processed=0,
            folders_classified=0,
            folders_pruned=0,
            files_classified=0,
            files_skipped=0,
            total_llm_calls=0,
            estimated_calls_saved=0,
        )
        max_d = self._config.max_depth
        if max_d is None:
            max_d = self._repo.get_max_depth(drive_id)

        for depth in range(0, max_d + 1):
            folders = self._repo.get_folders_at_depth(
                drive_id, depth, exclude_pruned=True,
            )
            if not folders:
                result.depths_processed = depth + 1
                if progress_callback:
                    progress_callback(WavefrontProgress(
                        current_depth=depth,
                        max_depth=max_d,
                        folders_classified=result.folders_classified,
                        folders_pruned=result.folders_pruned,
                        files_classified=result.files_classified,
                        total_folders=self._repo.count_folders_at_depth(drive_id, depth),
                        total_files=0,
                        estimated_llm_calls_saved=result.estimated_calls_saved,
                    ))
                continue

            # Sort by descendant count DESC — prune large subtrees first
            folders.sort(
                key=lambda f: (f.descendant_file_count or 0),
                reverse=True,
            )

            for batch in batched(folders, self._config.batch_size):
                batch_list = list(batch)
                summaries = [self._build_wavefront_summary(f) for f in batch_list]

                try:
                    classifications = await self._provider.classify_folders_wavefront(summaries)
                except Exception as exc:
                    # Per-batch failure: mark all folders in this batch as failed
                    for folder in batch_list:
                        self._mark_folder_failed(folder, str(exc), result)
                    continue

                result.total_llm_calls += 1

                for folder, classification in zip(batch_list, classifications):
                    try:
                        self._apply_folder_classification(folder, classification)

                        if classification.decision in ("include", "exclude"):
                            result.folders_pruned += 1
                            result.estimated_calls_saved += (
                                (folder.descendant_file_count or 0)
                                + (folder.descendant_folder_count or 0)
                            )

                        result.folders_classified += 1
                    except Exception as exc:
                        self._mark_folder_failed(folder, str(exc), result)

            result.depths_processed = depth + 1

            if progress_callback:
                progress_callback(WavefrontProgress(
                    current_depth=depth,
                    max_depth=max_d,
                    folders_classified=result.folders_classified,
                    folders_pruned=result.folders_pruned,
                    files_classified=result.files_classified,
                    total_folders=self._repo.count_folders_at_depth(drive_id, depth),
                    total_files=0,
                    estimated_llm_calls_saved=result.estimated_calls_saved,
                ))

        # Phase 2: Classify remaining files (optional)
        if self._config.classify_files:
            await self._classify_remaining_files(drive_id, result, progress_callback)

        return result

    # -------------------------------------------------------------------
    # Summary builder
    # -------------------------------------------------------------------

    def _build_wavefront_summary(self, entry: Entry) -> WavefrontFolderSummary:
        """Build a WavefrontFolderSummary from a folder Entry.

        Queries child entries for file type distribution and subfolder names,
        and fetches parent context via ``get_parent_entry``.
        """
        children = self._repo.get_child_entries(entry.drive_id, entry.path)

        file_children = [c for c in children if c.entry_type == "file"]
        folder_children = [c for c in children if c.entry_type == "folder"]

        # File type distribution: count extensions
        ext_counter: Counter[str] = Counter()
        for child in file_children:
            ext = child.extension or "(no extension)"
            ext_counter[ext] += 1

        # Direct subfolder names only
        folder_prefix = entry.path.rstrip("/") + "/"
        direct_subfolders = [
            c.name
            for c in folder_children
            if c.path.startswith(folder_prefix)
            and "/" not in c.path[len(folder_prefix):]
        ]

        # Parent context
        parent_classification: str | None = None
        parent_decision: str | None = None
        if entry.parent_path:
            parent = self._repo.get_parent_entry(entry.drive_id, entry.parent_path)
            if parent:
                parent_classification = parent.folder_purpose
                parent_decision = parent.decision_status

        return WavefrontFolderSummary(
            entry_id=entry.id,
            path=entry.path,
            name=entry.name,
            depth=entry.depth or 0,
            size_bytes=entry.size_bytes,
            child_count=entry.child_count,
            descendant_file_count=entry.descendant_file_count,
            descendant_folder_count=entry.descendant_folder_count,
            file_type_distribution=dict(ext_counter),
            subfolder_names=direct_subfolders,
            parent_classification=parent_classification,
            parent_decision=parent_decision,
        )

    # -------------------------------------------------------------------
    # Classification application
    # -------------------------------------------------------------------

    def _apply_folder_classification(
        self,
        folder: Entry,
        classification: WavefrontFolderClassification,
    ) -> None:
        """Write a wavefront classification result to the database.

        Sets folder_purpose, dual confidence, reasoning, and priority_review.
        Transitions classification_status → ai_classified and
        decision_status → the LLM's triage signal.
        """
        priority = classification.decision_confidence < self._config.confidence_threshold

        self._conn.execute(
            "UPDATE entries SET "
            "folder_purpose = ?, "
            "classification_confidence = ?, "
            "decision_confidence = ?, "
            "classification_reasoning = ?, "
            "priority_review = ? "
            "WHERE id = ?",
            (
                classification.folder_purpose,
                classification.classification_confidence,
                classification.decision_confidence,
                classification.reasoning,
                int(priority),
                folder.id,
            ),
        )
        self._conn.commit()

        # Transition classification_status → ai_classified
        apply_transition(
            self._conn, folder.id, "classification_status", "ai_classified",
        )
        # Transition decision_status → include/exclude/descend
        apply_transition(
            self._conn, folder.id, "decision_status", classification.decision,
        )

    # -------------------------------------------------------------------
    # File classification phase
    # -------------------------------------------------------------------

    async def _classify_remaining_files(
        self,
        drive_id: str,
        result: WavefrontResult,
        progress_callback: Callable[[WavefrontProgress], None] | None = None,
    ) -> None:
        """Classify remaining files not under pruned ancestors.

        Uses the existing ``classify_files`` provider method.  Files under
        ancestors with include/exclude decisions are skipped by the
        repository query.
        """
        while True:
            files = self._repo.get_pending_files(
                drive_id, batch_size=self._config.batch_size,
            )
            if not files:
                break

            summaries = [
                FileSummary(
                    entry_id=f.id,
                    path=f.path,
                    name=f.name,
                    extension=f.extension,
                    size_bytes=f.size_bytes,
                    last_modified=f.last_modified,
                )
                for f in files
            ]

            try:
                classifications = await self._provider.classify_files(summaries)
            except Exception as exc:
                logger.error("File classification batch failed: %s", exc)
                result.errors.append(f"File batch failed: {exc}")
                # Mark all files in this batch as failed
                for f in files:
                    try:
                        apply_transition(
                            self._conn, f.id,
                            "classification_status", "classification_failed",
                        )
                    except Exception:
                        pass
                break

            result.total_llm_calls += 1
            classified_map = {c.entry_id: c for c in classifications}

            for file_entry in files:
                classification = classified_map.get(file_entry.id)
                if classification is None:
                    result.files_skipped += 1
                    continue

                self._apply_file_classification(file_entry, classification)
                result.files_classified += 1

    def _apply_file_classification(
        self,
        entry: Entry,
        classification: FileClassification,
    ) -> None:
        """Write a file classification to the database."""
        priority = classification.classification_confidence < self._config.confidence_threshold

        self._conn.execute(
            "UPDATE entries SET "
            "file_class = ?, "
            "classification_confidence = ?, "
            "classification_reasoning = ?, "
            "priority_review = ? "
            "WHERE id = ?",
            (
                classification.file_class,
                classification.classification_confidence,
                classification.reasoning,
                int(priority),
                entry.id,
            ),
        )
        self._conn.commit()

        apply_transition(
            self._conn, entry.id, "classification_status", "ai_classified",
        )

    # -------------------------------------------------------------------
    # Error handling helpers
    # -------------------------------------------------------------------

    def _mark_folder_failed(
        self,
        folder: Entry,
        error_msg: str,
        result: WavefrontResult,
    ) -> None:
        """Mark a folder as classification_failed and record the error."""
        logger.error(
            "Folder classification failed for entry %d (%s): %s",
            folder.id, folder.path, error_msg,
        )
        result.errors.append(
            f"Folder {folder.id} ({folder.path}) failed: {error_msg}",
        )
        try:
            apply_transition(
                self._conn, folder.id,
                "classification_status", "classification_failed",
            )
        except Exception as exc:
            logger.error(
                "Failed to mark entry %d as classification_failed: %s",
                folder.id, exc,
            )

"""Batch orchestration for LLM classification.

The ``BatchClassifier`` fetches unclassified entries, separates files from
folders, builds summaries, calls the LLM provider, applies the confidence
threshold for the ``priority_review`` flag, and submits results via status
transitions.  Per-batch failures set affected entries to
``classification_failed`` without blocking other batches.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from src.classifier.provider import ClassifierConfig, LLMProvider
from src.db.models import (
    Entry,
    FileClassification,
    FileSummary,
    FolderClassification,
    FolderSummary,
)
from src.db.repository import Repository
from src.db.status import apply_transition

logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    """Summary of a single batch classification run."""

    files_classified: int = 0
    folders_classified: int = 0
    files_failed: int = 0
    folders_failed: int = 0
    errors: list[str] = field(default_factory=list)


class BatchClassifier:
    """Orchestrates batch classification of entries via an LLM provider."""

    def __init__(
        self,
        provider: LLMProvider,
        repo: Repository,
        conn: sqlite3.Connection,
        config: ClassifierConfig,
    ) -> None:
        self._provider = provider
        self._repo = repo
        self._conn = conn
        self._config = config

    async def classify_batch(
        self, drive_id: str, batch_size: int | None = None, *, include_failed: bool = False
    ) -> BatchResult:
        """Fetch unclassified entries and classify them via the LLM provider.

        Steps:
        1. Fetch unclassified entries from the repository.
        2. Separate into files and folders.
        3. Classify folders first (their purpose informs file classification).
        4. Classify files in sub-batches.
        5. Apply confidence threshold for priority_review flag.
        6. Submit results via status transitions.

        Per-entry failures set affected entries to ``classification_failed``
        and do not block other entries.

        When *include_failed* is True, entries with ``classification_failed``
        are also fetched so they can be retried.
        """
        if batch_size is None:
            batch_size = self._config.batch_size

        result = BatchResult()

        entries = self._repo.get_unclassified_batch(
            drive_id, batch_size, include_failed=include_failed
        )
        if not entries:
            return result

        folders = [e for e in entries if e.entry_type == "folder"]
        files = [e for e in entries if e.entry_type == "file"]

        # Classify folders first
        if folders:
            await self._classify_folders(folders, result)

        # Classify files in sub-batches of 20
        file_batch_size = 20
        for i in range(0, len(files), file_batch_size):
            sub_batch = files[i : i + file_batch_size]
            await self._classify_files(sub_batch, result)

        return result

    # -----------------------------------------------------------------------
    # Internal classification methods
    # -----------------------------------------------------------------------

    async def _classify_folders(
        self, folders: list[Entry], result: BatchResult
    ) -> None:
        """Build folder summaries and classify via the LLM provider.

        Folders are classified individually so that a single failure does
        not block the rest of the batch.
        """
        for folder in folders:
            summary = self._build_folder_summary(folder)

            try:
                classifications = await self._provider.classify_folders([summary])
            except Exception as exc:
                logger.error(
                    "Folder classification failed for entry %d (%s): %s",
                    folder.id,
                    folder.path,
                    exc,
                )
                result.errors.append(
                    f"Folder {folder.id} ({folder.path}) failed: {exc}"
                )
                self._mark_entry_failed(folder)
                result.folders_failed += 1
                continue

            if not classifications:
                logger.warning(
                    "No classification returned for folder entry %d, marking failed",
                    folder.id,
                )
                self._mark_entry_failed(folder)
                result.folders_failed += 1
                continue

            classification = classifications[0]
            # Correct entry_id if the provider returned a different one
            if classification.entry_id != folder.id:
                classification = classification.model_copy(
                    update={"entry_id": folder.id}
                )

            self._submit_folder_classification(folder, classification)
            result.folders_classified += 1

    async def _classify_files(
        self, files: list[Entry], result: BatchResult
    ) -> None:
        """Build file summaries and classify via the LLM provider."""
        summaries = [self._build_file_summary(f) for f in files]

        try:
            classifications = await self._provider.classify_files(summaries)
        except Exception as exc:
            logger.error("File classification batch failed: %s", exc)
            result.errors.append(f"File batch failed: {exc}")
            self._mark_entries_failed(files)
            result.files_failed += len(files)
            return

        classified_map = {c.entry_id: c for c in classifications}

        for file_entry in files:
            classification = classified_map.get(file_entry.id)
            if classification is None:
                logger.warning(
                    "No classification returned for file entry %d, marking failed",
                    file_entry.id,
                )
                self._mark_entry_failed(file_entry)
                result.files_failed += 1
                continue

            self._submit_file_classification(file_entry, classification)
            result.files_classified += 1

    # -----------------------------------------------------------------------
    # Summary builders
    # -----------------------------------------------------------------------

    def _build_file_summary(self, entry: Entry) -> FileSummary:
        """Build a FileSummary from an Entry."""
        return FileSummary(
            entry_id=entry.id,
            path=entry.path,
            name=entry.name,
            extension=entry.extension,
            size_bytes=entry.size_bytes,
            last_modified=entry.last_modified,
        )

    def _build_folder_summary(self, entry: Entry) -> FolderSummary:
        """Build a FolderSummary from a folder Entry by querying its children."""
        children = self._repo.get_child_entries(entry.drive_id, entry.path)

        file_children = [c for c in children if c.entry_type == "file"]
        folder_children = [c for c in children if c.entry_type == "folder"]

        # File type distribution: count extensions
        ext_counter: Counter[str] = Counter()
        for child in file_children:
            ext = child.extension or "(no extension)"
            ext_counter[ext] += 1

        # Direct subfolders only (one level deep)
        folder_prefix = entry.path.rstrip("/") + "/"
        direct_subfolders = [
            c.name
            for c in folder_children
            if c.path.count("/") == folder_prefix.count("/")
            or (
                c.path.startswith(folder_prefix)
                and "/" not in c.path[len(folder_prefix) :]
            )
        ]

        return FolderSummary(
            entry_id=entry.id,
            path=entry.path,
            name=entry.name,
            file_count=len(file_children),
            total_size_bytes=sum(c.size_bytes for c in file_children),
            file_type_distribution=dict(ext_counter),
            subfolder_names=direct_subfolders,
        )

    # -----------------------------------------------------------------------
    # Classification submission
    # -----------------------------------------------------------------------

    def _submit_file_classification(
        self, entry: Entry, classification: FileClassification
    ) -> None:
        """Write a file classification to the database via status transition."""
        priority = classification.classification_confidence < self._config.confidence_threshold

        self._conn.execute(
            "UPDATE entries SET file_class = ?, classification_confidence = ?, "
            "classification_reasoning = ?, priority_review = ? WHERE id = ?",
            (
                classification.file_class,
                classification.classification_confidence,
                classification.reasoning,
                int(priority),
                entry.id,
            ),
        )
        self._conn.commit()

        # Transition classification_status → ai_classified
        apply_transition(
            self._conn, entry.id, "classification_status", "ai_classified"
        )

    def _submit_folder_classification(
        self, entry: Entry, classification: FolderClassification
    ) -> None:
        """Write a folder classification to the database via status transition."""
        priority = classification.classification_confidence < self._config.confidence_threshold

        self._conn.execute(
            "UPDATE entries SET folder_purpose = ?, classification_confidence = ?, "
            "classification_reasoning = ?, priority_review = ? WHERE id = ?",
            (
                classification.folder_purpose,
                classification.classification_confidence,
                classification.reasoning,
                int(priority),
                entry.id,
            ),
        )
        self._conn.commit()

        # Transition classification_status → ai_classified
        apply_transition(
            self._conn, entry.id, "classification_status", "ai_classified"
        )

    # -----------------------------------------------------------------------
    # Failure handling
    # -----------------------------------------------------------------------

    def _mark_entries_failed(self, entries: list[Entry]) -> None:
        """Mark a list of entries as classification_failed."""
        for entry in entries:
            self._mark_entry_failed(entry)

    def _mark_entry_failed(self, entry: Entry) -> None:
        """Mark a single entry as classification_failed."""
        try:
            apply_transition(
                self._conn,
                entry.id,
                "classification_status",
                "classification_failed",
            )
        except Exception as exc:
            logger.error(
                "Failed to mark entry %d as classification_failed: %s",
                entry.id,
                exc,
            )

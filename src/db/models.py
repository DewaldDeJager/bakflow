"""Pydantic models — shared data contract for the bakflow system.

All models mirror the SQLite schema defined in schema.py and serve as the
canonical Python representation used across importer, classifier, MCP server,
and UI layers.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Status literal types
# ---------------------------------------------------------------------------

ClassificationStatus = Literal[
    "unclassified",
    "ai_classified",
    "classification_failed",
    "needs_reclassification",
]

ReviewStatus = Literal["pending_review", "reviewed"]

DecisionStatus = Literal["undecided", "include", "exclude", "defer", "descend"]

# ---------------------------------------------------------------------------
# Core data models (match SQLite tables)
# ---------------------------------------------------------------------------


class Drive(BaseModel):
    """A registered hard drive."""

    id: str  # UUID
    label: str
    volume_serial: str | None = None
    volume_label: str | None = None
    capacity_bytes: int | None = None
    created_at: datetime
    updated_at: datetime


class Entry(BaseModel):
    """A file or folder record in the index, tracked across three status dimensions."""

    id: int  # autoincrement PK
    drive_id: str  # FK → Drive.id
    path: str
    original_path: str = ""
    name: str
    entry_type: Literal["file", "folder"]
    extension: str | None = None
    size_bytes: int
    last_modified: datetime | None = None

    # Classification
    classification_status: ClassificationStatus = "unclassified"
    folder_purpose: str | None = None  # from Folder_Purpose_Taxonomy
    file_class: str | None = None
    classification_confidence: float | None = None
    decision_confidence: float | None = None
    classification_reasoning: str | None = None
    priority_review: bool = False

    # Tree metadata (all nullable — NULL means unknown)
    depth: int | None = None
    parent_path: str | None = None
    child_count: int | None = None
    descendant_file_count: int | None = None
    descendant_folder_count: int | None = None

    # Review & Decision
    review_status: ReviewStatus = "pending_review"
    decision_status: DecisionStatus = "undecided"
    decision_destination: str | None = None
    decision_notes: str | None = None

    # Overrides
    user_override_classification: str | None = None

    created_at: datetime
    updated_at: datetime


class AuditLogEntry(BaseModel):
    """Record of a status field transition (audit trail)."""

    id: int
    entry_id: int
    dimension: str  # "classification_status" | "review_status" | "decision_status"
    old_value: str
    new_value: str
    timestamp: datetime


class ImportLogEntry(BaseModel):
    """Record of a CSV import operation."""

    id: int
    drive_id: str
    csv_path: str
    entries_created: int
    rows_skipped: int
    started_at: datetime
    completed_at: datetime


# ---------------------------------------------------------------------------
# Classifier I/O models
# ---------------------------------------------------------------------------


class FileClassification(BaseModel):
    """LLM output for a single file classification."""

    entry_id: int
    file_class: str
    classification_confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class FolderClassification(BaseModel):
    """LLM output for a single folder classification."""

    entry_id: int
    folder_purpose: Literal[
        "irreplaceable_personal",
        "important_personal",
        "project_or_work",
        "reinstallable_software",
        "media_archive",
        "redundant_duplicate",
        "system_or_temp",
        "unknown_review_needed",
    ]
    classification_confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class FileSummary(BaseModel):
    """Summary of a file entry for LLM classification."""

    entry_id: int
    path: str
    name: str
    extension: str | None = None
    size_bytes: int
    last_modified: datetime | None = None


class FolderSummary(BaseModel):
    """Summary of a folder entry for LLM classification."""

    entry_id: int
    path: str
    name: str
    file_count: int
    total_size_bytes: int
    file_type_distribution: dict[str, int]  # extension -> count
    subfolder_names: list[str]


# ---------------------------------------------------------------------------
# Wavefront classification models
# ---------------------------------------------------------------------------


class WavefrontFolderClassification(BaseModel):
    """LLM output for wavefront folder classification with triage signal."""

    entry_id: int
    folder_purpose: Literal[
        "irreplaceable_personal",
        "important_personal",
        "project_or_work",
        "reinstallable_software",
        "media_archive",
        "redundant_duplicate",
        "system_or_temp",
        "unknown_review_needed",
    ]
    decision: Literal["include", "exclude", "descend"]
    classification_confidence: float = Field(ge=0.0, le=1.0)
    decision_confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class WavefrontFolderSummary(BaseModel):
    """Enhanced folder summary with tree metadata and parent context."""

    entry_id: int
    path: str
    name: str
    depth: int
    size_bytes: int
    child_count: int | None = None
    descendant_file_count: int | None = None
    descendant_folder_count: int | None = None
    file_type_distribution: dict[str, int]
    subfolder_names: list[str]
    parent_classification: str | None = None
    parent_decision: str | None = None


class WavefrontProgress(BaseModel):
    """Progress snapshot for a running wavefront classification."""

    current_depth: int
    max_depth: int | None
    folders_classified: int
    folders_pruned: int
    files_classified: int
    total_folders: int
    total_files: int
    estimated_llm_calls_saved: int


class WavefrontResult(BaseModel):
    """Final result of a wavefront classification run."""

    drive_id: str
    depths_processed: int
    folders_classified: int
    folders_pruned: int
    files_classified: int
    files_skipped: int
    total_llm_calls: int
    estimated_calls_saved: int
    errors: list[str] = Field(default_factory=list)

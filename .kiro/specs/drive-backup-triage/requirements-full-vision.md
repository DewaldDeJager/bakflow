# Requirements Document (Full Vision — Archived Reference)

> **Note:** This is the full-scope requirements document preserved as a reference for future phases.
> The active MVP spec is in `requirements.md`.

## Introduction

The Drive Backup Triage system is an AI-agent-assisted workflow for triaging and backing up data from multiple hard drives. It addresses the challenge of sorting through large volumes of files across multiple drives — separating irreplaceable personal files from system junk — with a human-in-the-loop approach. The system uses a four-layer architecture: a deterministic local pipeline for indexing and metadata extraction, an AI reasoning layer (LLM via Ollama by default) for classification, a human review layer (Streamlit) for decision-making, and a deterministic execution layer for performing backups.

Each Entry in the system is tracked across five independent status dimensions — inventory, classification, review, decision, and execution — rather than a single linear state. This multi-dimensional model cleanly separates concerns: an Entry can be classified but not yet reviewed, reviewed but explicitly excluded, included but not yet copied, or copied but failed verification, without any of these states conflicting. Review_Units are first-class persisted entities with stable identities, versioning semantics for split/merge operations, and their own lifecycle status. Classification precedence rules define how folder-level purpose, file-level class, and Review_Unit purpose interact during review and decision-making.

SQLite serves as the single source of truth, and the system is exposed via MCP server tools to enable agent-driven workflows. Multi-day sessions with full resume capability are a core requirement.

## Glossary

- **Index**: The SQLite database that stores all file metadata, classifications, decisions, Review_Units, and session state. The single source of truth for the entire system.
- **Pipeline**: The deterministic local processing stage that scans drives, extracts metadata, computes hashes, detects duplicates, and identifies known file classes.
- **Classifier**: The AI reasoning component that uses an LLM (via Ollama by default) to analyze structured file summaries and propose content classifications with confidence ratings.
- **Review_UI**: The Streamlit-based human review interface where users inspect AI classifications, make include/exclude/defer decisions, and set backup destinations.
- **Executor**: The deterministic execution layer that reads included Entries with `decision_status = include` and `review_status = reviewed` and performs backup operations using tools such as robocopy, rsync, or rclone.
- **MCP_Server**: The Model Context Protocol server (Python + MCP SDK) that exposes the Index as a set of tools for agent-driven workflows.
- **Decision_Manifest**: The structured record of all human-reviewed decisions (include/exclude/defer, backup destination, notes) stored in the Index.
- **Entry**: A single file or folder record in the Index, tracked across five independent status dimensions. File Entries receive File_Class labels; folder Entries receive Folder_Purpose labels.
- **Drive**: A physical or logical hard drive registered in the system for triage and backup.
- **Batch**: A group of Entries retrieved together for classification or review.
- **Confidence_Rating**: A numeric score (0.0 to 1.0) assigned by the Classifier indicating certainty of a classification.
- **File_Class**: A category label for known file types (e.g., system file, application cache, personal document, photo, video). Applied to file-level Entries.
- **Folder_Purpose**: A classification label for folder-level Entries drawn from the Folder_Purpose_Taxonomy. May be assigned by the Pipeline (deterministic), the Classifier (AI), or the user (override).
- **Effective_Purpose**: The resolved classification for an Entry that accounts for precedence rules. Computed as: explicit user override > Review_Unit purpose (if no override) > direct classification (File_Class or Folder_Purpose) > inherited parent folder purpose. Effective_Purpose is derived at query time, not stored.
- **Review_Unit**: A first-class persisted entity representing a coherent grouping of Entries presented as a single decision point during human review. Each Review_Unit has a stable ID, a type, a membership rule, aggregate metadata, an optional parent/child relationship, and its own `unit_status` lifecycle. The Classifier proposes Review_Unit boundaries during classification, and the user may split or merge units during review. Split/merge operations supersede the original unit and create new units with new IDs.
- **Review_Unit_Type**: The structural type of a Review_Unit, one of: `top_level_folder`, `subtree_cluster`, `duplicate_group`, `media_collection`, `app_install_dir`, `system_junk_category`, `manual_exception_group`.
- **Drill_Down**: The act of expanding a Review_Unit to inspect and make decisions on individual child Entries or sub-units. Triggered when the Classifier flags mixed signals, ambiguity, or potentially irreplaceable content within a unit. The Confidence_Rating threshold for recommending drill-down is configurable.
- **Folder_Purpose_Taxonomy**: The predefined set of folder classification categories:
  - `irreplaceable_personal` — unique personal files that cannot be recovered (photos, personal documents, etc.)
  - `important_personal` — personal files that are important but potentially recoverable
  - `project_or_work` — work projects, code repositories, professional documents
  - `reinstallable_software` — applications and software that can be re-downloaded/reinstalled
  - `media_archive` — media collections (music, movies, etc.) that may be replaceable from other sources
  - `redundant_duplicate` — files/folders that are duplicates of content already backed up or classified elsewhere
  - `system_or_temp` — OS files, temp files, caches, system junk
  - `unknown_review_needed` — items that could not be confidently classified

### Entry Status Dimensions

- **inventory_status**: `discovered` / `missing` / `inaccessible` / `deleted_since_scan`
- **classification_status**: `unclassified` / `deterministic_classified` / `ai_classified` / `classification_failed` / `needs_reclassification`
- **review_status**: `not_queued` / `queued` / `in_review` / `partially_reviewed` / `reviewed`
- **decision_status**: `undecided` / `include` / `exclude` / `defer`
- **execution_status**: `not_applicable` / `pending` / `copying` / `copied` / `verified` / `failed_copy` / `failed_verify` / `skipped`

### Review_Unit Status

- **unit_status**: `proposed` / `ready_for_review` / `in_review` / `partially_decided` / `decided` / `superseded`

## Requirements

### Requirement 1: Drive Registration and Scanning
### Requirement 2: AI Classification via Local LLM
### Requirement 3: Human Review and Decision Recording
### Requirement 4: Backup Execution and Validation
### Requirement 5: Session Persistence and Resume
### Requirement 6: MCP Server Tool Interface
### Requirement 7: Multi-Dimensional Status Integrity
### Requirement 8: Review_Unit Lifecycle Management
### Requirement 9: Classification Precedence Rules

> See the full acceptance criteria in the git history or request the complete document.

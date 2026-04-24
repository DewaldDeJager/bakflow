# Requirements Document — MVP (Spec 1)

## Introduction

The Drive Backup Triage MVP is the first deliverable of an AI-agent-assisted workflow for triaging data across multiple hard drives. This MVP focuses on the core classification-and-review loop: importing a directory listing from an external tool (e.g., TreeSize CSV export), classifying entries via a local LLM, reviewing classifications and recording backup decisions in a Streamlit UI, and exporting a decision manifest for manual execution.

Automated drive scanning and automated backup execution are deferred to later phases. The MVP proves the value of the AI classification + human review pattern and establishes the foundational data model, MCP server, and Streamlit UI that future phases build upon.

The system uses a multi-dimensional status model to cleanly separate classification progress, review progress, and decision outcome. SQLite serves as the single source of truth, and the system is exposed via MCP server tools to enable agent-driven workflows. Multi-day sessions with full resume capability are a core requirement.

## Scope

**In scope (MVP):**
- CSV import from TreeSize (or similar tool) into SQLite index
- Multi-dimensional status model (classification, review, decision — simplified for MVP)
- MCP server with core tools
- AI classification via Ollama (folder-level Folder_Purpose, file-level File_Class, confidence ratings)
- Streamlit review UI (browse, approve/exclude/defer, override classifications)
- Session persistence and multi-day resume
- Decision manifest export (viewable/exportable for manual backup execution)

**Deferred to Spec 2:**
- Drive registration and automated scanning (hashing, duplicate detection, resumable scans)
- Review_Unit as a first-class persisted entity (split/merge/supersession)
- Review_Unit-level review workflow
- Classification precedence rules (Effective_Purpose derivation)
- Automated backup execution and verification
- Rescan and inventory_status tracking

## Glossary

- **Index**: The SQLite database that stores all imported file/folder metadata, classifications, decisions, and session state. The single source of truth for the system.
- **Importer**: The component that parses a CSV export (e.g., from TreeSize) and populates the Index with Entry records.
- **Classifier**: The AI reasoning component that uses an LLM (via Ollama by default) to analyze structured file/folder summaries and propose content classifications with confidence ratings.
- **Review_UI**: The Streamlit-based human review interface where users inspect AI classifications, make include/exclude/defer decisions, and set backup destinations.
- **MCP_Server**: The Model Context Protocol server (Python + MCP SDK) that exposes the Index as a set of tools for agent-driven workflows.
- **Decision_Manifest**: The structured record of all human-reviewed decisions (include/exclude/defer, backup destination, notes) stored in the Index and exportable as CSV/JSON for manual execution.
- **Entry**: A single file or folder record in the Index, tracked across three independent status dimensions (classification, review, decision). File Entries receive File_Class labels; folder Entries receive Folder_Purpose labels.
- **Drive**: A persisted entity representing a single physical or logical hard drive registered in the system. Each Drive has a system-generated UUID (primary key, used internally for all queries and associations), a user-friendly label (editable, for display in the UI), and optional hardware identifiers: volume serial number (Windows-assigned, survives remounting to different drive letters), volume label (the name shown in Explorer/Finder), and total capacity in bytes. The UUID is the stable reference; hardware identifiers are used to match a drive across plug/unplug cycles.
- **Batch**: A group of Entries retrieved together for classification or review.
- **Confidence_Rating**: A numeric score (0.0 to 1.0) assigned by the Classifier indicating certainty of a classification.
- **File_Class**: A category label for known file types (e.g., system file, application cache, personal document, photo, video). Applied to file-level Entries.
- **Folder_Purpose**: A classification label for folder-level Entries drawn from the Folder_Purpose_Taxonomy.
- **Folder_Purpose_Taxonomy**: The predefined set of classification categories:
  - `irreplaceable_personal` — unique personal files that cannot be recovered (photos, personal documents, etc.)
  - `important_personal` — personal files that are important but potentially recoverable
  - `project_or_work` — work projects, code repositories, professional documents
  - `reinstallable_software` — applications and software that can be re-downloaded/reinstalled
  - `media_archive` — media collections (music, movies, etc.) that may be replaceable from other sources
  - `redundant_duplicate` — files/folders that are duplicates of content already backed up or classified elsewhere
  - `system_or_temp` — OS files, temp files, caches, system junk
  - `unknown_review_needed` — items that could not be confidently classified

### Entry Status Dimensions (MVP)

Each Entry is tracked across three independent status fields. These fields are orthogonal — transitions in one dimension do not require or imply transitions in another.

- **classification_status**: Tracks the classification progress of the Entry.
  - `unclassified` — No classification has been assigned
  - `ai_classified` — Classified by the Classifier via LLM
  - `classification_failed` — Classification was attempted but failed (LLM error, timeout)
  - `needs_reclassification` — Previously classified but marked for reclassification (e.g., after user override)

- **review_status**: Tracks whether the Entry has been presented to and evaluated by a human.
  - `pending_review` — Entry is classified and awaiting human review
  - `reviewed` — A human has evaluated this Entry

- **decision_status**: Tracks the backup decision for the Entry.
  - `undecided` — No decision has been made
  - `include` — Entry is marked for backup
  - `exclude` — Entry is explicitly excluded from backup
  - `defer` — Decision is postponed to a future session

## Requirements

### Requirement 1: Drive Registration and CSV Import

**User Story:** As a user, I want to register a hard drive with identifying information and import its directory listing from a TreeSize CSV export, so that I have a structured and reliably identifiable inventory of files and folders to classify and review.

#### Acceptance Criteria

1. WHEN a new drive is registered, THE Importer SHALL create a Drive record in the Index with a system-generated UUID, a user-provided label, and optional hardware identifiers: volume serial number, volume label, and total capacity in bytes.
2. WHEN a CSV file path and a Drive UUID are provided, THE Importer SHALL parse the CSV and create an Entry in the Index for each row, storing: full path, name, type (file or folder), extension (for files), size in bytes, and last-modified timestamp, associated with the Drive UUID.
3. WHEN an Entry is created during import, THE Importer SHALL set its `classification_status` to `unclassified`, `review_status` to `pending_review`, and `decision_status` to `undecided`.
4. WHEN the CSV contains rows that cannot be parsed (malformed path, missing required fields), THE Importer SHALL skip the row, log a warning with the row number and reason, and continue processing remaining rows.
5. WHEN an import is performed for a Drive UUID that already has Entries in the Index, THE Importer SHALL report the conflict and require explicit confirmation before adding or replacing Entries.
6. WHEN import is complete, THE Importer SHALL report the total number of Entries created, the number of skipped rows, and the Drive label and UUID.
7. WHEN a volume serial number is provided during registration, THE Importer SHALL check for existing Drive records with the same volume serial number and warn the user if a match is found (potential duplicate registration).
8. THE Review_UI SHALL provide a drive management view where the user can list registered Drives, edit labels, and view hardware identifiers.
9. ALL MCP tools that accept a drive identifier SHALL accept the Drive UUID as the primary identifier; the MCP_Server SHALL also support lookup by volume serial number as a convenience.

### Requirement 2: AI Classification via LLM

**User Story:** As a user, I want an AI agent to classify unclassified files and folders using an LLM, so that I can quickly understand what is on each drive without manually inspecting every item.

#### Acceptance Criteria

1. WHEN the MCP_Server receives a `get_unclassified_batch` request with a drive identifier and batch size, THE MCP_Server SHALL return a Batch of Entries with `classification_status` in (`unclassified`, `needs_reclassification`) from the specified Drive, up to the requested batch size.
2. WHEN the MCP_Server receives a `get_folder_summary` request with a path, THE MCP_Server SHALL return an aggregated summary of the folder contents including file count, total size, file type distribution, and subfolder structure.
3. WHEN the Classifier receives a Batch of file-level Entries, THE Classifier SHALL send structured summaries to the LLM (via Ollama by default) and produce a File_Class label and a Confidence_Rating for each Entry.
4. WHEN the Classifier receives a Batch of folder-level Entries, THE Classifier SHALL send structured folder summaries to the LLM and assign a Folder_Purpose label from the Folder_Purpose_Taxonomy and a Confidence_Rating for each Entry.
5. WHEN the MCP_Server receives a `submit_classification` request with an array of classified Entries, THE MCP_Server SHALL update each Entry in the Index with the proposed File_Class or Folder_Purpose, Confidence_Rating, and set the `classification_status` to `ai_classified`.
6. IF the Ollama service is unreachable, THEN THE Classifier SHALL return a descriptive error and set the `classification_status` of affected Entries to `classification_failed`.
7. WHEN the Classifier assigns a Confidence_Rating below a configurable threshold, THE Classifier SHALL flag the Entry as requiring priority human review.
8. THE Classifier SHALL only assign Folder_Purpose labels that exist in the Folder_Purpose_Taxonomy; the Classifier SHALL not invent new categories.

### Requirement 3: Human Review and Decision Recording

**User Story:** As a user, I want to review AI classifications and record my backup decisions for files and folders, so that no irreplaceable data is lost and no junk is backed up without my explicit approval.

#### Acceptance Criteria

1. WHEN the MCP_Server receives a `get_review_queue` request with a drive identifier and optional filter, THE MCP_Server SHALL return Entries with `classification_status = ai_classified` and `review_status = pending_review` matching the filter criteria, ordered by Confidence_Rating ascending (lowest confidence first).
2. THE Review_UI SHALL present Entries grouped by parent folder, showing each Entry's path, File_Class or Folder_Purpose, Confidence_Rating, size, and last-modified timestamp.
3. THE Review_UI SHALL allow the user to filter the review queue by Folder_Purpose or File_Class category, by confidence range, and by drive.
4. WHEN the MCP_Server receives a `record_decision` request, THE MCP_Server SHALL store the decision (`include`, `exclude`, or `defer`), an optional backup destination path, and optional user notes, and set the Entry's `review_status` to `reviewed` and `decision_status` to the chosen value.
5. THE Review_UI SHALL allow the user to override the AI-proposed Folder_Purpose or File_Class when recording a decision.
6. THE Review_UI SHALL support bulk decisions, allowing the user to select multiple Entries and apply the same decision in a single operation.
7. WHEN a user applies a decision to a folder Entry, THE Review_UI SHALL offer the option to cascade that decision to all child Entries of that folder that still have `decision_status = undecided`.
8. IF a `record_decision` request references an Entry that already has `review_status = reviewed`, THEN THE MCP_Server SHALL allow the decision to be updated (user changed their mind) and record the change in the audit log.
9. WHEN a user overrides the File_Class or Folder_Purpose of an Entry, THE MCP_Server SHALL store the override and set `classification_status` to `needs_reclassification` only if the user explicitly requests reclassification of related Entries.

### Requirement 4: Decision Manifest Export

**User Story:** As a user, I want to export my reviewed decisions as a structured manifest, so that I can execute backups manually using my preferred tools.

#### Acceptance Criteria

1. WHEN the MCP_Server receives a `get_decision_manifest` request with a drive identifier and optional filter, THE MCP_Server SHALL return all Entries with `review_status = reviewed` and `decision_status = include`, including each Entry's full source path, backup destination, File_Class or Folder_Purpose, and user notes.
2. THE Review_UI SHALL provide an export function that writes the decision manifest to a CSV file with columns: source_path, destination_path, entry_type, classification, confidence, decision, notes.
3. THE Review_UI SHALL also support exporting the manifest as JSON for programmatic consumption.
4. THE export SHALL include a summary header with: Drive UUID, drive label, volume serial number (if available), export timestamp, total entries included, total entries excluded, total entries deferred, total entries undecided.
5. WHEN the user requests an export, THE Review_UI SHALL allow filtering by `decision_status` so that excluded or deferred items can also be exported for reference.

### Requirement 5: Session Persistence and Resume

**User Story:** As a user, I want to stop and resume the triage process across multiple days without losing progress, so that I can work through large drives at my own pace.

#### Acceptance Criteria

1. THE Index SHALL persist all Entry status fields (`classification_status`, `review_status`, `decision_status`), classifications, decisions, and session metadata in the SQLite database so that the system state survives process restarts.
2. WHEN the system starts, THE MCP_Server SHALL restore its operational state from the Index without requiring user re-entry of previous decisions or classifications.
3. WHEN the MCP_Server receives a `get_drive_progress` request with a drive identifier, THE MCP_Server SHALL return counts of Entries grouped by each status dimension (`classification_status`, `review_status`, `decision_status`) and overall completion percentage for that Drive.
4. WHEN a classification Batch is partially submitted before interruption, THE MCP_Server SHALL accept the partial submission and leave unsubmitted Entries in their prior status values.
5. THE Index SHALL record a timestamp for every status field transition across all three dimensions, providing a full audit trail of the triage process.

### Requirement 6: MCP Server Tool Interface

**User Story:** As a developer, I want the system exposed as MCP server tools, so that I can drive the triage workflow through an AI agent and learn MCP patterns.

#### Acceptance Criteria

1. THE MCP_Server SHALL expose the following tools via the Model Context Protocol: `get_unclassified_batch`, `get_folder_summary`, `submit_classification`, `get_review_queue`, `record_decision`, `get_drive_progress`, and `get_decision_manifest`.
2. WHEN any MCP tool is called with missing or invalid parameters, THE MCP_Server SHALL return a structured error response describing the validation failure.
3. THE MCP_Server SHALL be implemented in Python using the MCP SDK.
4. THE MCP_Server SHALL connect to the Index (SQLite database) as its sole data source for all tool operations.
5. WHEN multiple MCP tool calls reference the same Entry concurrently, THE MCP_Server SHALL use database-level locking to prevent data corruption.

### Requirement 7: Status Transition Integrity

**User Story:** As a user, I want the system to enforce valid status transitions, so that no entry reaches an inconsistent state.

#### Acceptance Criteria

1. THE Index SHALL enforce the following valid `classification_status` transitions: `unclassified` → `ai_classified`, `unclassified` → `classification_failed`, `classification_failed` → `ai_classified`, `classification_failed` → `needs_reclassification`, `ai_classified` → `needs_reclassification`, `needs_reclassification` → `ai_classified`.
2. THE Index SHALL enforce the following valid `review_status` transitions: `pending_review` → `reviewed`, `reviewed` → `pending_review` (triggered by reclassification).
3. THE Index SHALL enforce the following valid `decision_status` transitions: `undecided` → `include`, `undecided` → `exclude`, `undecided` → `defer`, `include` → `exclude`, `include` → `defer`, `exclude` → `include`, `exclude` → `defer`, `defer` → `include`, `defer` → `exclude`.
4. THE Index SHALL enforce cross-dimension guards: an Entry's `review_status` SHALL NOT transition to `reviewed` unless `classification_status` is `ai_classified`.
5. IF a status transition is requested that does not appear in the valid transitions for that dimension, THEN THE Index SHALL reject the transition and return a descriptive error identifying the dimension, current value, and attempted value.
6. WHEN a status field transition occurs on any dimension, THE Index SHALL record the dimension name, previous value, new value, and timestamp in an audit log table.

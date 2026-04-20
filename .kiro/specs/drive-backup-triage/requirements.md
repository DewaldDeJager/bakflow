# Requirements Document

## Introduction

The Drive Backup Triage system is an AI-agent-assisted workflow for triaging and backing up data from multiple hard drives. It addresses the challenge of sorting through large volumes of files across multiple drives — separating irreplaceable personal files from system junk — with a human-in-the-loop approach. The system uses a four-layer architecture: a deterministic local pipeline for indexing and metadata extraction, an AI reasoning layer (LLM via Ollama by default) for classification, a human review layer (Streamlit) for decision-making, and a deterministic execution layer for performing backups. The Classifier assigns file-level File_Class labels and folder-level Folder_Purpose labels from a predefined taxonomy. SQLite serves as the single source of truth, and the system is exposed via MCP server tools to enable agent-driven workflows. Multi-day sessions with full resume capability are a core requirement.

## Glossary

- **Index**: The SQLite database that stores all file metadata, classifications, decisions, and session state. The single source of truth for the entire system.
- **Pipeline**: The deterministic local processing stage that scans drives, extracts metadata, computes hashes, detects duplicates, and identifies known file classes.
- **Classifier**: The AI reasoning component that uses an LLM (via Ollama by default) to analyze structured file summaries and propose content classifications with confidence ratings.
- **Review_UI**: The Streamlit-based human review interface where users inspect AI classifications, make include/exclude decisions, and set backup destinations.
- **Executor**: The deterministic execution layer that reads the Decision_Manifest and performs backup operations using tools such as robocopy, rsync, or rclone.
- **MCP_Server**: The Model Context Protocol server (Python + MCP SDK) that exposes the Index as a set of tools for agent-driven workflows.
- **Decision_Manifest**: The structured record of all human-reviewed decisions (include/exclude, backup destination, notes) stored in the Index.
- **Entry**: A single file or folder record in the Index, tracked through the state machine lifecycle. File Entries receive File_Class labels; folder Entries receive Folder_Purpose labels.
- **Entry_State**: The lifecycle state of an Entry: `unclassified`, `ai_classified`, `human_reviewed`, or `backed_up`.
- **Drive**: A physical or logical hard drive registered in the system for triage and backup.
- **Batch**: A group of Entries retrieved together for classification or review.
- **Confidence_Rating**: A numeric score (0.0 to 1.0) assigned by the Classifier indicating certainty of a classification.
- **File_Class**: A category label for known file types (e.g., system file, application cache, personal document, photo, video). Applied to file-level Entries.
- **Folder_Purpose**: A classification label for folder-level Entries drawn from a predefined taxonomy: `irreplaceable_personal`, `important_personal`, `project_or_work`, `reinstallable_software`, `media_archive`, `redundant_duplicate`, `system_or_temp`, `unknown_review_needed`.
- **Review_Unit**: A coherent grouping of Entries presented as a single decision point during human review. Examples include: a top-level folder, a subtree cluster, a duplicate group, a media collection, an application install directory, a known system-junk category, or a manual exception group. The Classifier proposes Review_Unit boundaries during classification, and the user may split or merge units during review.
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

## Requirements

### Requirement 1: Drive Registration and Scanning

**User Story:** As a user, I want to register hard drives and scan their contents into a local index, so that I have a structured inventory of all files to triage.

#### Acceptance Criteria

1. WHEN a drive path is provided, THE Pipeline SHALL scan the directory tree and store each discovered file and folder as an Entry in the Index with its full path, name, type (file or folder), extension (for files), size, and last-modified timestamp.
2. WHEN scanning a drive, THE Pipeline SHALL compute a content hash (SHA-256) for files below a configurable size threshold and a partial sample hash for files above that threshold.
3. WHEN scanning is complete for a drive, THE Pipeline SHALL set the Entry_State of all newly created Entries to `unclassified`.
4. WHEN a scan is interrupted, THE Pipeline SHALL record the last successfully scanned path in the Index so that a subsequent scan resumes from that point.
5. WHEN two or more Entries share the same content hash, THE Pipeline SHALL flag them as duplicates in the Index.
6. WHEN a file matches a known File_Class signature (by extension or metadata pattern), THE Pipeline SHALL assign that File_Class to the Entry.
7. IF a drive path does not exist or is inaccessible, THEN THE Pipeline SHALL return a descriptive error message and not modify the Index.

### Requirement 2: AI Classification via Local LLM

**User Story:** As a user, I want an AI agent to classify unclassified files and folders using an LLM, so that I can quickly understand what is on each drive without manually inspecting every item.

#### Acceptance Criteria

1. WHEN the MCP_Server receives a `get_unclassified_batch` request with a drive identifier and batch size, THE MCP_Server SHALL return a Batch of Entries in `unclassified` state from the specified Drive, up to the requested batch size.
2. WHEN the MCP_Server receives a `get_folder_summary` request with a path, THE MCP_Server SHALL return an aggregated summary of the folder contents including file count, total size, file type distribution, and subfolder structure.
3. WHEN the Classifier receives a Batch of file-level Entries, THE Classifier SHALL send structured summaries to the LLM (via Ollama by default) and produce a File_Class label and a Confidence_Rating for each Entry.
4. WHEN the Classifier receives a Batch of folder-level Entries, THE Classifier SHALL send structured folder summaries to the LLM and assign a Folder_Purpose label from the Folder_Purpose_Taxonomy and a Confidence_Rating for each Entry.
5. WHEN the MCP_Server receives a `submit_classification` request with an array of classified Entries, THE MCP_Server SHALL update each Entry in the Index with the proposed File_Class or Folder_Purpose, Confidence_Rating, and transition the Entry_State from `unclassified` to `ai_classified`.
6. IF the Ollama service is unreachable, THEN THE Classifier SHALL return a descriptive error and leave affected Entries in their current Entry_State.
7. WHEN the Classifier assigns a Confidence_Rating below a configurable threshold, THE Classifier SHALL flag the Entry as requiring priority human review.
8. THE Classifier SHALL only assign Folder_Purpose labels that exist in the Folder_Purpose_Taxonomy; the Classifier SHALL not invent new categories.
9. THE Classifier SHALL propose Review_Unit boundaries by identifying coherent groupings of Entries such as subtrees with homogeneous content, duplicate groups, and application directories.
10. WHEN a Review_Unit contains mixed content signals or Entries with conflicting classifications, THE Classifier SHALL flag the Review_Unit with a `drill_down_recommended` indicator.
11. THE Classifier SHALL assign a single Folder_Purpose label and Confidence_Rating to each proposed Review_Unit based on the aggregate content analysis of its child Entries.

### Requirement 3: Human Review and Decision Recording

**User Story:** As a user, I want to review AI classifications at the Review_Unit level and record my backup decisions, so that no irreplaceable data is lost and no junk is backed up without my explicit approval, while keeping the review process scalable for drives with hundreds of thousands of files.

#### Acceptance Criteria

1. WHEN the MCP_Server receives a `get_review_queue` request with a drive identifier and optional filter, THE MCP_Server SHALL return Review_Units in `ai_classified` state matching the filter criteria, ordered by Confidence_Rating ascending (lowest confidence first).
2. THE Review_UI SHALL present Review_Units as the default review granularity, showing each unit's aggregate Folder_Purpose classification, Confidence_Rating, total size, and Entry count.
3. WHEN the Review_UI presents a Review_Unit for review, THE Review_UI SHALL display the unit path, Folder_Purpose, Confidence_Rating, total size, Entry count, and duplicate status.
4. THE Review_UI SHALL visually highlight Review_Units flagged with `drill_down_recommended` to draw user attention to ambiguous or mixed-content units.
5. THE Review_UI SHALL allow the user to Drill_Down into a Review_Unit to inspect and make individual decisions on child Entries or sub-units.
6. THE Review_UI SHALL allow the user to split a Review_Unit into smaller sub-units or merge adjacent Review_Units during review.
7. WHEN the user records a decision on a Review_Unit, THE MCP_Server SHALL cascade that decision to all child Entries within the unit, transitioning each child Entry_State from `ai_classified` to `human_reviewed`.
8. WHEN the MCP_Server receives a `record_decision` request, THE MCP_Server SHALL store the decision (include or exclude from backup), the backup destination path, and optional user notes in the Decision_Manifest.
9. THE Review_UI SHALL allow the user to override the AI-proposed Folder_Purpose or File_Class when recording a decision at either the Review_Unit or individual Entry level.
10. THE Review_UI SHALL support bulk decisions, allowing the user to apply the same action to multiple Review_Units in a single operation.
11. IF a `record_decision` request references an Entry not in `ai_classified` state, THEN THE MCP_Server SHALL reject the request with a descriptive error indicating the current Entry_State.
12. WHEN the user drills down into a Review_Unit and makes individual Entry-level decisions, THE MCP_Server SHALL track those Entry-level decisions independently, leaving sibling Entries in their current Entry_State.

### Requirement 4: Backup Execution and Validation

**User Story:** As a user, I want the system to execute backups according to my reviewed decisions and verify file integrity, so that I can trust that my data is safely copied.

#### Acceptance Criteria

1. WHEN the Executor processes the Decision_Manifest, THE Executor SHALL copy only Entries with a `human_reviewed` state and an "include" decision to their specified backup destination using a deterministic copy tool (robocopy, rsync, or rclone).
2. WHEN a file has been copied, THE Executor SHALL verify the destination file hash matches the source file hash recorded in the Index.
3. WHEN verification succeeds for an Entry, THE Executor SHALL transition the Entry_State from `human_reviewed` to `backed_up` in the Index.
4. IF a file copy fails, THEN THE Executor SHALL log the failure with the source path, destination path, and error details, and leave the Entry_State as `human_reviewed` for retry.
5. IF hash verification fails after a copy, THEN THE Executor SHALL log a verification failure, remove the corrupt destination file, and leave the Entry_State as `human_reviewed`.
6. WHEN the Executor begins a backup run, THE Executor SHALL record a run identifier and timestamp in the Index so that partial runs can be identified and resumed.

### Requirement 5: Session Persistence and Resume

**User Story:** As a user, I want to stop and resume the triage process across multiple days without losing progress, so that I can work through large drives at my own pace.

#### Acceptance Criteria

1. THE Index SHALL persist all Entry_States, classifications, decisions, and session metadata in the SQLite database so that the system state survives process restarts.
2. WHEN the system starts, THE MCP_Server SHALL restore its operational state from the Index without requiring user re-entry of previous decisions or classifications.
3. WHEN the MCP_Server receives a `get_drive_progress` request with a drive identifier, THE MCP_Server SHALL return counts of Entries in each Entry_State (`unclassified`, `ai_classified`, `human_reviewed`, `backed_up`) and the percentage complete for that Drive.
4. WHEN a classification Batch is partially submitted before interruption, THE MCP_Server SHALL accept the partial submission and leave unsubmitted Entries in their prior Entry_State.
5. THE Index SHALL record a timestamp for every Entry_State transition to provide a full audit trail of the triage process.

### Requirement 6: MCP Server Tool Interface

**User Story:** As a developer, I want the system exposed as MCP server tools, so that I can drive the triage workflow through an AI agent and learn MCP patterns.

#### Acceptance Criteria

1. THE MCP_Server SHALL expose the following tools via the Model Context Protocol: `get_unclassified_batch`, `get_folder_summary`, `submit_classification`, `get_review_queue`, `record_decision`, and `get_drive_progress`.
2. WHEN any MCP tool is called with missing or invalid parameters, THE MCP_Server SHALL return a structured error response describing the validation failure.
3. THE MCP_Server SHALL be implemented in Python using the MCP SDK.
4. THE MCP_Server SHALL connect to the Index (SQLite database) as its sole data source for all tool operations.
5. WHEN multiple MCP tool calls reference the same Entry concurrently, THE MCP_Server SHALL use database-level locking to prevent data corruption.

### Requirement 7: State Machine Integrity

**User Story:** As a user, I want the system to enforce a strict state machine for every file entry, so that no file is backed up without going through classification and review.

#### Acceptance Criteria

1. THE Index SHALL enforce that an Entry_State transition follows the sequence: `unclassified` → `ai_classified` → `human_reviewed` → `backed_up`.
2. IF a state transition is requested that does not follow the allowed sequence, THEN THE Index SHALL reject the transition and return a descriptive error.
3. THE Index SHALL allow an Entry in any state to be reset to `unclassified` to support reclassification workflows.
4. WHEN an Entry_State transition occurs, THE Index SHALL record the previous state, new state, and timestamp of the transition in an audit log table.

# Requirements: Wavefront Classification

## Requirement 1: Schema Changes

### 1.1 Tree Metadata Columns
Add nullable columns `depth` (INTEGER), `parent_path` (TEXT), `child_count` (INTEGER), `descendant_file_count` (INTEGER), `descendant_folder_count` (INTEGER) to the `entries` table. NULL means "unknown", 0 means "actually zero".

**Acceptance Criteria:**
- [ ] Columns exist after `init_db()` and accept NULL values
- [ ] Columns accept integer 0 (distinct from NULL)
- [ ] Entries can be inserted with all tree metadata columns as NULL
- [ ] Entries can be inserted with all tree metadata columns as valid integers

### 1.2 Dual Confidence Columns
Rename `confidence` column to `classification_confidence`. Add new `decision_confidence` column. Both are REAL with CHECK constraints for [0.0, 1.0] range, nullable.

**Acceptance Criteria:**
- [ ] `confidence` column no longer exists; `classification_confidence` column exists
- [ ] `decision_confidence` column exists with same CHECK constraint as `classification_confidence`
- [ ] Both columns accept NULL, 0.0, 0.5, and 1.0
- [ ] Both columns reject values < 0.0 or > 1.0

### 1.3 Descend Decision Status
Add `'descend'` to the `decision_status` CHECK constraint. Add table-level CHECK: `decision_status != 'descend' OR entry_type = 'folder'`.

**Acceptance Criteria:**
- [ ] Folder entries can have `decision_status = 'descend'`
- [ ] File entries with `decision_status = 'descend'` are rejected by the DB CHECK constraint
- [ ] Existing decision statuses ('undecided', 'include', 'exclude', 'defer') still work

### 1.4 Depth Index
Add composite index `idx_entries_depth` on `(drive_id, depth, classification_status, decision_status)`.

**Acceptance Criteria:**
- [ ] Index exists after `init_db()`
- [ ] Queries filtering by `drive_id + depth + classification_status` use the index

## Requirement 2: Pydantic Model Updates

### 2.1 Entry Model Updates
Update `Entry` model: rename `confidence` to `classification_confidence`, add `decision_confidence`, add tree metadata fields (`depth`, `parent_path`, `child_count`, `descendant_file_count`, `descendant_folder_count`). Update `DecisionStatus` literal to include `'descend'`.

**Acceptance Criteria:**
- [ ] `Entry` model has `classification_confidence` field (not `confidence`)
- [ ] `Entry` model has `decision_confidence` field
- [ ] `Entry` model has all five tree metadata fields, all optional (None default)
- [ ] `DecisionStatus` literal includes `'descend'`
- [ ] Entry model round-trips through SQLite correctly with all new fields

### 2.2 Wavefront Classification Models
Add `WavefrontFolderClassification` model with `entry_id`, `folder_purpose`, `decision` (include/exclude/descend), `classification_confidence`, `decision_confidence`, `reasoning`. Add `WavefrontFolderSummary` with tree metadata and parent context fields. Add `WavefrontProgress` and `WavefrontResult` models.

**Acceptance Criteria:**
- [ ] `WavefrontFolderClassification` validates `decision` is one of include/exclude/descend
- [ ] `WavefrontFolderClassification` validates both confidence fields in [0.0, 1.0]
- [ ] `WavefrontFolderSummary` accepts None for `parent_classification` and `parent_decision`
- [ ] `WavefrontProgress` and `WavefrontResult` models can be constructed with valid data

## Requirement 3: Status Engine Updates

### 3.1 Decision Status Transitions
Add `'descend'` to all decision_status transition sets. Enable full bidirectional transitions: every decision status can transition to every other decision status (for folders).

**Acceptance Criteria:**
- [ ] `undecided → descend` is a valid transition
- [ ] `descend → include`, `descend → exclude`, `descend → defer`, `descend → undecided` are all valid
- [ ] `include → descend`, `exclude → descend`, `defer → descend` are all valid
- [ ] All existing transitions remain valid
- [ ] `include → undecided`, `exclude → undecided`, `defer → undecided` are valid (full bidirectional)

### 3.2 Descend-Folder Guard
Add cross-dimension guard: `decision_status → 'descend'` requires `entry.entry_type == 'folder'`. Raise `InvalidTransitionError` for file entries.

**Acceptance Criteria:**
- [ ] Transitioning a folder entry to `descend` succeeds
- [ ] Transitioning a file entry to `descend` raises `InvalidTransitionError`
- [ ] Error message indicates the guard failure reason

### 3.3 Cascade on Review Confirmation
When a human confirms a folder decision (review_status → reviewed) with cascade, propagate the decision to all descendants. Skip descendants where `review_status = 'reviewed'` (human already made explicit decision).

**Acceptance Criteria:**
- [ ] Cascade applies parent's decision to all `pending_review` descendants
- [ ] Cascade skips descendants where `review_status = 'reviewed'`
- [ ] Cascade creates audit log entries for every updated descendant
- [ ] `descend` decisions do NOT cascade (only include/exclude/defer)

## Requirement 4: Repository — Wavefront Queries

### 4.1 Get Folders at Depth
Add `get_folders_at_depth(drive_id, depth, exclude_pruned=True)` method. Returns folders at a specific depth level, excluding those under ancestors with `decision_status IN ('include', 'exclude')`. Ordered by `descendant_file_count DESC` (NULLS LAST).

**Acceptance Criteria:**
- [ ] Returns only folders (not files) at the specified depth
- [ ] Returns only unclassified or needs_reclassification entries
- [ ] When `exclude_pruned=True`, excludes folders under ancestors with include/exclude decision
- [ ] Results ordered by descendant_file_count descending, NULLs last
- [ ] Returns empty list when no eligible folders exist

### 4.2 Get Pending Files
Add `get_pending_files(drive_id, batch_size)` method. Returns unclassified files not under pruned ancestors.

**Acceptance Criteria:**
- [ ] Returns only file entries (not folders)
- [ ] Returns only unclassified entries
- [ ] Excludes files under ancestors with include/exclude decision
- [ ] Respects batch_size limit

### 4.3 Compute Tree Metadata
Add `compute_tree_metadata(drive_id)` method. Derives `depth`, `parent_path`, `child_count`, `descendant_file_count`, `descendant_folder_count` from path structure for entries where these are NULL.

**Acceptance Criteria:**
- [ ] Sets `depth` based on path separator count for entries where depth is NULL
- [ ] Sets `parent_path` based on dirname for non-root entries where parent_path is NULL
- [ ] Sets `child_count` for folders based on direct children count
- [ ] Sets `descendant_file_count` and `descendant_folder_count` for folders based on recursive counts
- [ ] Does not overwrite existing non-NULL values
- [ ] Returns count of updated entries

### 4.4 Supporting Queries
Add `get_max_depth(drive_id)`, `count_folders_at_depth(drive_id, depth)`, `get_parent_entry(drive_id, parent_path)`, `get_pruned_ancestor(drive_id, path)`.

**Acceptance Criteria:**
- [ ] `get_max_depth` returns the highest depth value for a drive
- [ ] `count_folders_at_depth` returns accurate count of folders at a depth
- [ ] `get_parent_entry` returns the folder entry matching the parent_path
- [ ] `get_pruned_ancestor` returns nearest ancestor with include/exclude decision, or None

## Requirement 5: CSV Importer Updates

### 5.1 Parse TreeSize Tree Columns
Detect and parse `Dir Level`, `Folder Path`, `Child item count`, `Files`, `Folders` columns from TreeSize CSV. Map to `depth`, `parent_path`, `child_count`, `descendant_file_count`, `descendant_folder_count`.

**Acceptance Criteria:**
- [ ] When CSV has `Dir Level` column, `depth` is populated from it
- [ ] When CSV has `Folder Path` column, `parent_path` is populated (normalized)
- [ ] When CSV has `Child item count`, `Files`, `Folders` columns, corresponding fields are populated
- [ ] Integer parsing handles TreeSize's space-separated thousands format
- [ ] `ColumnMapping` dataclass has fields for all five new columns

### 5.2 Derive Missing Tree Metadata
When tree columns are absent from CSV, derive `depth` from path separator count and `parent_path` from dirname. Leave count columns as NULL.

**Acceptance Criteria:**
- [ ] When CSV lacks `Dir Level`, `depth` is derived from path separators
- [ ] When CSV lacks `Folder Path`, `parent_path` is derived from dirname
- [ ] When CSV lacks `Child item count`/`Files`/`Folders`, those columns remain NULL (not 0)
- [ ] Derived depth matches the number of path separators minus 1

## Requirement 6: Wavefront Classifier

### 6.1 BFS Depth Traversal
Implement `WavefrontClassifier.classify()` that traverses depth levels 0 through max_depth in order, classifying folders at each level before proceeding deeper.

**Acceptance Criteria:**
- [ ] Folders at depth 0 are classified before depth 1, depth 1 before depth 2, etc.
- [ ] Within each depth, folders are sorted by descendant_file_count DESC
- [ ] Folders under pruned ancestors are not sent to the LLM
- [ ] Classification results are written to DB with both confidence values
- [ ] `classification_status` transitions to `ai_classified` for each classified folder
- [ ] `decision_status` transitions to the LLM's triage signal (include/exclude/descend)

### 6.2 Subtree Pruning
When a folder receives `include` or `exclude` decision, its entire subtree is frozen — no descendants are classified in subsequent depth passes.

**Acceptance Criteria:**
- [ ] After a folder is classified as include/exclude, its children do not appear in `get_folders_at_depth` for deeper levels
- [ ] Estimated LLM calls saved is tracked (sum of descendant counts for pruned folders)
- [ ] `descend` folders have their children eligible at the next depth level

### 6.3 Optional File Classification Phase
After the folder wavefront completes, optionally classify remaining files (controlled by `classify_files` parameter).

**Acceptance Criteria:**
- [ ] When `classify_files=True`, unclassified files not under pruned ancestors are classified
- [ ] When `classify_files=False`, no file classification occurs
- [ ] File classification uses existing `classify_files` provider method

### 6.4 Progress Reporting
Report progress via callback at each depth level with current depth, folders classified, folders pruned, and estimated calls saved.

**Acceptance Criteria:**
- [ ] Progress callback is called after each depth level completes
- [ ] Progress includes `current_depth`, `folders_classified`, `folders_pruned`, `estimated_llm_calls_saved`
- [ ] Final `WavefrontResult` contains complete summary statistics

### 6.5 Error Handling
Per-folder LLM failures mark the folder as `classification_failed` without blocking other folders. Missing tree metadata triggers post-import derivation.

**Acceptance Criteria:**
- [ ] LLM failure for one folder does not prevent classification of other folders at the same depth
- [ ] Failed folders are marked `classification_failed` and can be retried
- [ ] Errors are collected in `WavefrontResult.errors`

## Requirement 7: Enhanced Prompts

### 7.1 Wavefront Folder Prompt
Build a prompt that requests folder_purpose classification AND triage signal (include/exclude/descend), with dual confidence scores and combined reasoning.

**Acceptance Criteria:**
- [ ] Prompt includes the Folder_Purpose taxonomy
- [ ] Prompt explains the three triage signals (include, exclude, descend)
- [ ] Prompt requests JSON with: entry_id, folder_purpose, decision, classification_confidence, decision_confidence, reasoning
- [ ] Prompt includes tree metadata (child_count, descendant counts) when available
- [ ] Prompt includes parent classification context when available
- [ ] Prompt handles NULL tree metadata gracefully (shows "unknown" instead of 0)

## Requirement 8: Provider Protocol Update

### 8.1 Wavefront Classification Method
Add `classify_folders_wavefront` method to `LLMProvider` protocol. Accepts `WavefrontFolderSummary` list, returns `WavefrontFolderClassification` list.

**Acceptance Criteria:**
- [ ] `LLMProvider` protocol includes `classify_folders_wavefront` method
- [ ] Method signature accepts `list[WavefrontFolderSummary]` and returns `list[WavefrontFolderClassification]`
- [ ] Existing `classify_files` and `classify_folders` methods remain unchanged

## Requirement 9: MCP Server Updates

### 9.1 Wavefront Classification Tool
Add `run_wavefront_classification` MCP tool with `task=True` for background execution. Accepts `drive_id`, `max_depth`, `classify_files`, `batch_size`. Reports progress via FastMCP Context.

**Acceptance Criteria:**
- [ ] Tool is registered with `task=True`
- [ ] Tool accepts drive_id (required), max_depth (optional), classify_files (default True), batch_size (default 10)
- [ ] Tool reports progress at each depth level via `ctx.report_progress()`
- [ ] Tool returns WavefrontResult summary on completion

### 9.2 Updated Record Decision
Update `record_decision` to accept `'descend'` as a valid decision for folder entries. Update cascade logic to skip already-reviewed children.

**Acceptance Criteria:**
- [ ] `descend` is accepted as a valid decision for folder entries
- [ ] `descend` is rejected for file entries
- [ ] Cascade skips children where `review_status = 'reviewed'`
- [ ] Human can transition between any decision statuses (include↔exclude↔descend↔defer↔undecided)

### 9.3 Updated Decision Manifest
Exclude entries with `decision_status = 'descend'` from the decision manifest. `descend` is an intermediate routing decision, not a final exportable decision.

**Acceptance Criteria:**
- [ ] Manifest never contains entries with `decision_status = 'descend'`
- [ ] Manifest still includes entries with include, exclude, and defer decisions
- [ ] `decision_filter` parameter does not accept `'descend'` as a valid filter value

### 9.4 Updated Review Queue
Sort review queue by `decision_confidence` ASC (instead of the old `confidence` column).

**Acceptance Criteria:**
- [ ] Review queue entries are sorted by `decision_confidence` ascending
- [ ] Entries with NULL `decision_confidence` appear first (most uncertain)
- [ ] Filtering by confidence range uses `decision_confidence`

## Requirement 10: Configuration

### 10.1 Wavefront Configuration Options
Add `wavefront_max_depth` (int | None), `wavefront_classify_files` (bool, default True), `wavefront_batch_size` (int, default 10) to `AppConfig`.

**Acceptance Criteria:**
- [ ] `wavefront_max_depth` defaults to None (no limit) and is overridable via `BF_WAVEFRONT_MAX_DEPTH`
- [ ] `wavefront_classify_files` defaults to True and is overridable via `BF_WAVEFRONT_CLASSIFY_FILES`
- [ ] `wavefront_batch_size` defaults to 10 and is overridable via `BF_WAVEFRONT_BATCH_SIZE`

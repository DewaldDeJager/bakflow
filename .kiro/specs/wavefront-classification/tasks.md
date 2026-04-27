# Tasks: Wavefront Classification

## Task 1: Schema Changes (Req 1.1, 1.2, 1.3, 1.4)

- [x] 1.1 Add tree metadata columns (`depth`, `parent_path`, `child_count`, `descendant_file_count`, `descendant_folder_count`) to the `entries` table DDL in `src/db/schema.py`. All columns nullable INTEGER/TEXT.
- [x] 1.2 Rename `confidence` column to `classification_confidence` and add `decision_confidence` column with same CHECK constraint in `src/db/schema.py`.
- [x] 1.3 Add `'descend'` to the `decision_status` CHECK constraint and add table-level CHECK `(decision_status != 'descend' OR entry_type = 'folder')` in `src/db/schema.py`.
- [x] 1.4 Add composite index `idx_entries_depth` on `(drive_id, depth, classification_status, decision_status)` in `src/db/schema.py`.
- [x] 1.5 Update existing `idx_entries_confidence` index to use `classification_confidence` instead of `confidence`.
- [x] 1.6 Write tests for schema changes: verify columns exist, CHECK constraints enforce descend-folder-only, dual confidence range validation, NULL vs 0 semantics for tree metadata.

## Task 2: Pydantic Model Updates (Req 2.1, 2.2)

- [x] 2.1 Update `DecisionStatus` literal to include `'descend'` in `src/db/models.py`.
- [x] 2.2 Update `Entry` model: rename `confidence` to `classification_confidence`, add `decision_confidence: float | None = None`, add tree metadata fields (`depth`, `parent_path`, `child_count`, `descendant_file_count`, `descendant_folder_count`) all as `int | None = None` or `str | None = None`.
- [x] 2.3 Add `WavefrontFolderClassification` model with `entry_id`, `folder_purpose` (Literal), `decision` (Literal["include", "exclude", "descend"]), `classification_confidence`, `decision_confidence`, `reasoning`.
- [x] 2.4 Add `WavefrontFolderSummary` model with tree metadata and parent context fields (`parent_classification`, `parent_decision`).
- [x] 2.5 Add `WavefrontProgress` and `WavefrontResult` models.
- [x] 2.6 Update `FolderClassification` model: rename `confidence` to `classification_confidence` (or keep for backward compat with existing classify_folders).
- [x] 2.7 Update `FileClassification` model: rename `confidence` to `classification_confidence`.
- [x] 2.8 Write tests for model updates: verify Entry round-trips with new fields, WavefrontFolderClassification validates decision and confidence ranges, WavefrontFolderSummary accepts None parent context.

## Task 3: Status Engine Updates (Req 3.1, 3.2, 3.3)

- [x] 3.1 Update `VALID_TRANSITIONS["decision_status"]` to include `'descend'` in all transition sets and add `'undecided'` as a target from all states (full bidirectional) in `src/db/status.py`.
- [x] 3.2 Add cross-dimension guard `("decision_status", "descend")` that checks `entry.entry_type == "folder"` in `src/db/status.py`.
- [x] 3.3 Update `_fetch_entry` to handle new columns (`classification_confidence`, `decision_confidence`, tree metadata) when constructing Entry from DB row.
- [x] 3.4 Write tests for status engine: verify all decision_status transitions including descend, verify descend-folder guard rejects files, verify full bidirectional transitions for folders.

## Task 4: Repository — Wavefront Queries (Req 4.1, 4.2, 4.3, 4.4)

- [x] 4.1 Add `get_folders_at_depth(drive_id, depth, exclude_pruned=True)` method to `Repository` in `src/db/repository.py`. Uses NOT EXISTS subquery for pruning, orders by `descendant_file_count DESC NULLS LAST`.
- [x] 4.2 Add `get_pending_files(drive_id, batch_size=50)` method that returns unclassified files not under pruned ancestors.
- [x] 4.3 Add `compute_tree_metadata(drive_id)` method that derives depth, parent_path, child_count, descendant_file_count, descendant_folder_count from path structure.
- [x] 4.4 Add `get_max_depth(drive_id)`, `count_folders_at_depth(drive_id, depth)`, `get_parent_entry(drive_id, parent_path)`, `get_pruned_ancestor(drive_id, path)` methods.
- [x] 4.5 Write tests for repository queries: verify get_folders_at_depth with pruned/unpruned trees, verify compute_tree_metadata against known tree structures, verify get_pending_files excludes pruned files.

## Task 5: CSV Importer Updates (Req 5.1, 5.2)

- [x] 5.1 Add `dir_level`, `folder_path`, `child_item_count`, `files_count`, `folders_count` fields to `ColumnMapping` dataclass in `src/importer/csv_importer.py`.
- [x] 5.2 Update `import_csv` to detect and parse tree metadata columns (`Dir Level`, `Folder Path`, `Child item count`, `Files`, `Folders`). Populate `depth`, `parent_path`, `child_count`, `descendant_file_count`, `descendant_folder_count` in the INSERT statement.
- [x] 5.3 When tree columns are absent, derive `depth` from path separator count and `parent_path` from dirname. Leave count columns as NULL.
- [x] 5.4 Update the bulk INSERT statement to include the five new columns.
- [x] 5.5 Write tests for importer: test with CSV containing all tree columns, test with CSV missing tree columns (verify derivation and NULL counts), test integer parsing with space-separated thousands.

## Task 6: Wavefront Classifier (Req 6.1, 6.2, 6.3, 6.4, 6.5)

- [x] 6.1 Create `src/classifier/wavefront.py` with `WavefrontConfig` dataclass and `WavefrontClassifier` class.
- [x] 6.2 Implement `WavefrontClassifier.classify()` — BFS depth traversal loop, folder batching, LLM calls, result processing.
- [x] 6.3 Implement `_build_wavefront_summary()` — builds `WavefrontFolderSummary` with parent context from `get_parent_entry`.
- [x] 6.4 Implement `_apply_folder_classification()` — writes classification + decision to DB, transitions statuses, sets priority_review based on decision_confidence.
- [x] 6.5 Implement `_classify_remaining_files()` — optional file classification phase using existing provider method.
- [x] 6.6 Implement progress reporting via callback at each depth level.
- [x] 6.7 Implement per-folder error handling: catch LLM failures, mark as classification_failed, continue with remaining folders.
- [x] 6.8 Write tests for wavefront classifier: mock LLM provider, verify BFS ordering, verify pruning skips subtrees, verify progress callbacks, verify error handling.

## Task 7: Enhanced Prompts (Req 7.1)

- [x] 7.1 Add `build_wavefront_folder_prompt(summary: WavefrontFolderSummary) -> str` function to `src/classifier/prompts.py`.
- [x] 7.2 Include triage signal explanation (include = back up entire subtree, exclude = skip entire subtree, descend = classify children individually).
- [x] 7.3 Include tree metadata (child_count, descendant_file_count, descendant_folder_count) when not None, show "unknown" for None values.
- [x] 7.4 Include parent classification context section when `parent_classification` is not None.
- [x] 7.5 Request JSON output with `entry_id`, `folder_purpose`, `decision`, `classification_confidence`, `decision_confidence`, `reasoning`.
- [x] 7.6 Write tests for prompt builder: verify prompt contains taxonomy, triage signals, handles None tree metadata, includes/excludes parent context.

## Task 8: Provider Protocol Update (Req 8.1)

- [x] 8.1 Add `classify_folders_wavefront(summaries: list[WavefrontFolderSummary]) -> list[WavefrontFolderClassification]` method to `LLMProvider` protocol in `src/classifier/provider.py`.
- [x] 8.2 Update `ClassifierConfig` to include `wavefront_batch_size` field.
- [x] 8.3 Implement `classify_folders_wavefront` in OllamaProvider (or whichever provider is primary) using the new wavefront prompt.
- [x] 8.4 Write tests for provider: verify protocol includes new method, verify implementation calls wavefront prompt and parses response.

## Task 9: MCP Server Updates (Req 9.1, 9.2, 9.3, 9.4)

- [x] 9.1 Add `run_wavefront_classification` MCP tool with `task=True` decorator. Accept `drive_id`, `max_depth`, `classify_files`, `batch_size`. Wire to `WavefrontClassifier.classify()` with progress reporting via `ctx.report_progress()`.
- [x] 9.2 Update `record_decision` to accept `'descend'` as a valid decision for folder entries. Add validation that rejects `descend` for file entries.
- [x] 9.3 Update cascade logic in `_cascade_decision`: skip children where `review_status = 'reviewed'` (human already made explicit decision).
- [x] 9.4 Update `get_decision_manifest` to exclude entries with `decision_status = 'descend'`. Update `valid_decisions` set to not include `'descend'` as a filter option.
- [x] 9.5 Update `get_review_queue` to sort by `decision_confidence ASC` instead of `confidence ASC`. Handle NULL `decision_confidence` (sort first).
- [x] 9.6 Update all references from `confidence` to `classification_confidence` or `decision_confidence` as appropriate across server.py.
- [x] 9.7 Write tests for MCP server updates: verify wavefront tool registration, verify record_decision with descend, verify manifest excludes descend, verify review queue sort order.

## Task 10: Configuration (Req 10.1) — SKIPPED

Skipped: wavefront parameters (max_depth, classify_files, batch_size) are already controllable via the `run_wavefront_classification` MCP tool parameters. App-level env-var config is redundant.

## Task 11: Update Existing Code References

- [x] 11.1 Update `BatchClassifier` in `src/classifier/batch.py` to use `classification_confidence` instead of `confidence` in all DB writes and model references.
- [x] 11.2 Update `submit_classification` MCP tool to use `classification_confidence` instead of `confidence`.
- [x] 11.3 Update any existing tests that reference the `confidence` field to use `classification_confidence`.
- [x] 11.4 Update `_row_to_entry` and `_fetch_entry` helpers to handle the renamed column and new columns.

## Task 12: Integration Verification

- [x] 12.1 Run full test suite to verify no regressions from schema/model changes.
- [x] 12.2 Verify end-to-end: init_db → import CSV with tree columns → run wavefront classifier (mocked LLM) → verify depth-ordered classification → verify pruning → verify manifest excludes descend.

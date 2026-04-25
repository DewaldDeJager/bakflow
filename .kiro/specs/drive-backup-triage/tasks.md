# Implementation Plan: Drive Backup Triage MVP

## Overview

Build the Drive Backup Triage MVP bottom-up: data layer (SQLite schema, models, status engine, repository), then service layer (CSV importer, classifier with provider abstraction, MCP server), then presentation layer (Streamlit UI), and finally integration wiring and export. Each task builds on the previous, with property-based tests placed close to the code they validate.

## Tasks

- [x] 1. Set up project structure, configuration, and database foundation
  - [x] 1.1 Create the `src/` package directory structure with `__init__.py` files for all subpackages (`db/`, `importer/`, `classifier/`, `mcp_server/`, `ui/`, `ui/pages/`, `ui/components/`)
    - Create `config.py` with `AppConfig` dataclass (db_path, llm_provider, model, base_url, api_key, confidence_threshold, batch_size defaults)
    - Create `cli.py` stub with Click or argparse entry points for `run-server`, `run-ui`, `import-csv`
    - Set up `pyproject.toml` with dependencies: pydantic, mcp, streamlit, hypothesis, pytest, httpx, ollama, openai
    - _Requirements: 6.3_

  - [x] 1.2 Implement `db/schema.py` — DDL, WAL mode, triggers, indexes
    - Implement `init_db(db_path: str) -> sqlite3.Connection` that creates all tables (`drives`, `entries`, `audit_log`, `import_log`), indexes, CHECK constraints, and triggers (`trg_entries_updated_at`, `trg_drives_updated_at`)
    - Enable WAL mode and foreign keys
    - _Requirements: 5.1, 6.4_

  - [x] 1.3 Implement `db/models.py` — Pydantic models
    - Define `Drive`, `Entry`, `AuditLogEntry`, `ImportLogEntry` Pydantic models with all fields matching the SQLite schema
    - Define `ClassificationStatus`, `ReviewStatus`, `DecisionStatus` as Literal types
    - Define `FileClassification`, `FolderClassification`, `FileSummary`, `FolderSummary` models for classifier I/O
    - _Requirements: 1.1, 1.2, 2.3, 2.4_

- [x] 2. Implement status transition engine and repository
  - [x] 2.1 Implement `db/status.py` — status transition validation and enforcement
    - Define `VALID_TRANSITIONS` dict for all three dimensions (classification_status, review_status, decision_status)
    - Define `CROSS_DIMENSION_GUARDS` — review_status → reviewed requires classification_status == ai_classified
    - Implement `validate_transition(dimension, current, target, entry)` raising `InvalidTransitionError`
    - Implement `apply_transition(conn, entry_id, dimension, target)` that validates, updates, writes audit log, returns updated Entry
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 2.2 Write property tests for status transition enforcement (P18)
    - **Property 18: Status transition enforcement**
    - For any dimension and (current, target) pair, transition succeeds iff the pair is in the valid transitions map; invalid transitions are rejected with descriptive error
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.5**

  - [x] 2.3 Write property tests for cross-dimension guard enforcement (P19)
    - **Property 19: Cross-dimension guard enforcement**
    - For any Entry where classification_status ≠ ai_classified, transitioning review_status to reviewed is rejected; succeeds only when classification_status = ai_classified
    - **Validates: Requirements 7.4**

  - [x] 2.4 Write property tests for audit log completeness (P17)
    - **Property 17: Audit log completeness**
    - For any status transition on any dimension, an audit_log entry is created with correct entry_id, dimension, old_value, new_value, and valid timestamp
    - **Validates: Requirements 5.5, 7.6, 3.8**

  - [x] 2.5 Implement `db/repository.py` — CRUD operations and query builders
    - Implement `Repository` class with all methods: `create_drive`, `get_drive`, `get_drive_by_serial`, `list_drives`, `update_drive_label`
    - Implement Entry methods: `create_entries_bulk`, `get_entry`, `get_entries_by_drive`, `count_entries_by_drive`
    - Implement batch/query methods: `get_unclassified_batch`, `get_review_queue`, `get_drive_progress`, `get_decision_manifest`, `get_child_entries`
    - _Requirements: 1.1, 1.9, 2.1, 3.1, 4.1, 5.3_

  - [x] 2.6 Write property tests for drive registration (P1)
    - **Property 1: Drive registration produces valid records**
    - For any label and optional hardware identifiers, creating a Drive produces a record with valid UUID, exact label, matching optional fields, and is retrievable by UUID
    - **Validates: Requirements 1.1**

  - [x] 2.7 Write property tests for drive lookup equivalence (P4)
    - **Property 4: Drive lookup equivalence by UUID and volume serial**
    - For any Drive with non-null volume serial, lookup by UUID and by volume serial returns the same record
    - **Validates: Requirements 1.9**

  - [x] 2.8 Write property tests for persistence round-trip (P15)
    - **Property 15: Persistence round-trip**
    - For any set of Drives and Entries written to the Index, closing and reopening the database yields identical data
    - **Validates: Requirements 5.1**

- [x] 3. Checkpoint — Data layer complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement CSV importer
  - [x] 4.1 Implement `importer/csv_importer.py` — TreeSize CSV parsing and Entry creation
    - Implement `ColumnMapping` dataclass with configurable column names
    - Implement `ImportResult` and `SkipDetail` dataclasses
    - Implement `import_csv(conn, csv_path, drive_id, column_mapping, force)` that parses CSV, infers entry_type from extension when no type column, creates Entries with default statuses, handles re-import conflict detection, skips malformed rows with warnings
    - Write import metadata to `import_log` table
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6_

  - [x] 4.2 Write property tests for CSV import round-trip (P2)
    - **Property 2: CSV import round-trip**
    - For any valid CSV content, importing creates exactly one Entry per valid row with matching fields and correct default statuses; ImportResult reports accurate counts
    - **Validates: Requirements 1.2, 1.3, 1.6**

  - [x] 4.3 Write property tests for malformed CSV row handling (P3)
    - **Property 3: Malformed CSV rows are skipped without affecting valid rows**
    - For any CSV with mixed valid/malformed rows, only valid rows produce Entries; ImportResult reports correct skip count and row numbers
    - **Validates: Requirements 1.4**

- [x] 5. Implement classifier with provider abstraction
  - [x] 5.1 Implement `classifier/provider.py` — LLMProvider protocol and factory
    - Define `LLMProvider` Protocol with `classify_files` and `classify_folders` async methods
    - Implement `create_provider(config)` factory returning OllamaProvider or OpenAIProvider based on config
    - _Requirements: 2.3, 2.4_

  - [x] 5.2 Implement `classifier/prompts.py` — prompt templates
    - Implement `build_file_classification_prompt(summaries)` including full File_Class taxonomy with descriptions
    - Implement `build_folder_classification_prompt(summary)` including full Folder_Purpose_Taxonomy with descriptions and aggregated folder stats
    - _Requirements: 2.3, 2.4, 2.8_

  - [x] 5.3 Implement `classifier/ollama_provider.py` — Ollama LLM provider
    - Implement `OllamaProvider` class with `classify_files` and `classify_folders` using Ollama's `format` parameter with Pydantic JSON schema for structured output
    - Handle connection errors, timeouts, malformed responses
    - _Requirements: 2.3, 2.4, 2.6_

  - [x] 5.4 Implement `classifier/openai_provider.py` — OpenAI LLM provider
    - Implement `OpenAIProvider` class with `classify_files` and `classify_folders` using OpenAI's `response_format` with `json_schema`
    - Handle auth failures, rate limits (exponential backoff), malformed responses
    - _Requirements: 2.3, 2.4_

  - [x] 5.5 Implement `classifier/batch.py` — batch orchestration and confidence threshold logic
    - Implement `BatchClassifier` with `classify_batch(drive_id, batch_size)` that fetches unclassified entries, separates files/folders, builds summaries, calls LLM provider, applies confidence threshold for priority_review flag, submits results via status transitions
    - Handle per-batch failures: set affected entries to `classification_failed`, don't block other batches
    - _Requirements: 2.1, 2.5, 2.6, 2.7_

  - [x] 5.6 Write property tests for classifier output validity (P7)
    - **Property 7: Classifier output completeness and validity**
    - With a mocked LLM, classifier returns exactly one classification per input Entry; files get non-empty file_class, folders get valid folder_purpose, all confidences in [0.0, 1.0]
    - **Validates: Requirements 2.3, 2.4, 2.8**

  - [x] 5.7 Write property tests for classification submission round-trip (P8)
    - **Property 8: Classification submission round-trip**
    - For any unclassified Entries and valid classifications, submitting updates each Entry with correct file_class/folder_purpose, confidence, and classification_status = ai_classified
    - **Validates: Requirements 2.5**

  - [x] 5.8 Write property tests for confidence threshold (P9)
    - **Property 9: Confidence threshold determines priority review flag**
    - For any classified Entry, confidence below threshold → priority_review = True; at or above → priority_review = False
    - **Validates: Requirements 2.7**

- [x] 6. Checkpoint — Service layer (importer + classifier) complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement MCP server tools
  - [x] 7.1 Implement `mcp_server/server.py` — all 7 MCP tool definitions
    - Register tools with FastMCP: `get_unclassified_batch`, `get_folder_summary`, `submit_classification`, `get_review_queue`, `record_decision`, `get_drive_progress`, `get_decision_manifest`
    - Each tool handler: resolve drive identifier (UUID or volume serial), validate parameters, delegate to Repository/status.py, return structured dict responses
    - Implement consistent error response format for all tools
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 7.2 Write property tests for unclassified batch filtering (P5)
    - **Property 5: Unclassified batch filtering and size limit**
    - For any Drive with mixed statuses, get_unclassified_batch returns only entries with classification_status in {unclassified, needs_reclassification}, count ≤ batch_size
    - **Validates: Requirements 2.1**

  - [x] 7.3 Write property tests for folder summary aggregation (P6)
    - **Property 6: Folder summary aggregation correctness**
    - For any folder, summary returns correct file_count, total_size, file type distribution, and subfolder list
    - **Validates: Requirements 2.2**

  - [x] 7.4 Write property tests for review queue filtering (P10)
    - **Property 10: Review queue filtering and ordering**
    - get_review_queue returns only entries where classification_status = ai_classified AND review_status = pending_review, ordered by confidence ascending
    - **Validates: Requirements 3.1**

  - [x] 7.5 Write property tests for decision recording (P11)
    - **Property 11: Decision recording round-trip**
    - For any ai_classified Entry and valid decision, recording sets review_status = reviewed and decision_status to chosen value; destination and notes persisted exactly
    - **Validates: Requirements 3.4**

  - [x] 7.6 Write property tests for cascade behavior (P12)
    - **Property 12: Cascade applies decision only to undecided children**
    - Cascading a decision updates only children with decision_status = undecided; leaves others unchanged
    - **Validates: Requirements 3.7**

  - [x] 7.7 Write property tests for decision manifest filtering (P13)
    - **Property 13: Decision manifest contains only matching entries**
    - get_decision_manifest returns only entries where review_status = reviewed AND decision_status matches filter
    - **Validates: Requirements 4.1**

  - [x] 7.8 Write property tests for progress aggregation (P16)
    - **Property 16: Progress aggregation correctness**
    - get_drive_progress returns counts per status dimension matching actual Entry counts; completion % = reviewed / total
    - **Validates: Requirements 5.3**

  - [x] 7.9 Write property tests for MCP parameter validation (P20)
    - **Property 20: MCP tool parameter validation**
    - For any tool with missing/invalid parameters, returns structured error response rather than unhandled exception
    - **Validates: Requirements 6.2**

- [x] 8. Checkpoint — MCP server complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement Streamlit UI
  - [x] 9.1 Implement `ui/app.py` — Streamlit app entry point and navigation
    - Set up multi-page Streamlit app with sidebar navigation to Drive Management, Review Queue, Progress Dashboard, and Export pages
    - Configure Streamlit page settings and session state initialization
    - _Requirements: 1.8_

  - [x] 9.2 Implement `ui/pages/drive_management.py` — drive registration and CSV import
    - Drive registration form: label, volume serial, volume label, capacity
    - Drive list with edit label capability and hardware identifier display
    - CSV import: file upload, drive selector, column mapping override, force re-import toggle
    - Display import results (entries created, rows skipped, skip details)
    - _Requirements: 1.1, 1.2, 1.5, 1.7, 1.8_

  - [x] 9.3 Implement `ui/pages/review_queue.py` and `ui/components/` — review interface
    - Sidebar filters: drive selector, category filter (Folder_Purpose / File_Class), confidence range slider, status filters
    - Entry display grouped by parent folder with expandable cards (path, classification, confidence, size, last_modified)
    - Per-entry action buttons: Include / Exclude / Defer with optional destination and notes fields
    - Classification override dropdown before recording decision
    - Bulk selection with checkboxes and "Apply to selected" action bar
    - Cascade prompt dialog when deciding on a folder entry
    - Implement `ui/components/entry_card.py`, `ui/components/filters.py`, `ui/components/bulk_actions.py`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9_

  - [x] 9.4 Implement `ui/pages/progress_dashboard.py` — triage progress visualization
    - Per-drive progress bars for each status dimension (classification, review, decision)
    - Overall completion percentage
    - Drive selector
    - _Requirements: 5.3_

  - [x] 9.5 Implement `ui/pages/export.py` — manifest preview and export
    - Decision manifest preview table with filtering by decision_status
    - Export to CSV with columns: source_path, destination_path, entry_type, classification, confidence, decision, notes
    - Export to JSON for programmatic consumption
    - Summary header: Drive UUID, label, volume serial, export timestamp, counts per decision status
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 9.6 Write property tests for export round-trip (P14)
    - **Property 14: Export round-trip**
    - Exporting to CSV and parsing back recovers all Entry records with correct columns; same for JSON; header contains correct Drive info and accurate counts
    - **Validates: Requirements 4.2, 4.3, 4.4**

- [x] 10. Wire CLI entry points and integration
  - [x] 10.1 Complete `cli.py` — wire all entry points
    - `import-csv` command: accepts CSV path, drive label, optional hardware IDs, calls importer
    - `run-server` command: starts MCP server with configured DB path
    - `run-ui` command: launches Streamlit app
    - `init-db` command: initializes database at configured path
    - _Requirements: 6.3_

  - [x] 10.2 Wire `run-server` and `run-ui` CLI stubs to their implementations
    - `run-server`: start the MCP server via `mcp_server/server.py` with configured DB path
    - `run-ui`: launch the Streamlit app via `subprocess` or `streamlit.cli` pointing at `ui/app.py`
    - _Requirements: 6.3_

  - [x] 10.3 Write integration tests for MCP server tool registration
    - Verify all 7 tools are registered and callable
    - Test concurrent MCP tool calls with database-level locking
    - _Requirements: 6.1, 6.5_

- [x] 11. Final checkpoint — All components integrated
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property-based tests use Hypothesis with `@settings(max_examples=100)`
- Checkpoints ensure incremental validation at each layer boundary
- The build order (data → service → presentation) ensures each layer has a solid foundation before the next begins

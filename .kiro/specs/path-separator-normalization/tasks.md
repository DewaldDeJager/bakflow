# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Backslash Path Child Lookup Fails
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate `get_child_entries` returns empty results for backslash paths
  - **Scoped PBT Approach**: Use Hypothesis to generate path segments composed of alphanumeric characters, then construct parent/child paths using backslash separators. For each generated hierarchy, insert entries with backslash paths into an in-memory SQLite database, call `get_child_entries`, and assert the expected children are returned.
  - **Bug Condition from design**: `isBugCondition(input)` returns true when `storedPath` or `queryPath` contains backslash separators
  - **Expected Behavior assertions**: `get_child_entries(drive_id, backslash_parent)` returns all child entries whose normalized path starts with the normalized parent prefix (Property 2 from design)
  - **Concrete cases to include**:
    - `get_child_entries(drive_id, "F:\\SteamLibrary\\steamapps")` with children stored as `F:\SteamLibrary\steamapps\common\game` → should return children, will return `[]` on unfixed code
    - `get_child_entries(drive_id, "C:/Users/mixed\\path")` with backslash children → should return children, will return `[]` on unfixed code
  - Create test file at `src/db/test_path_normalization.py`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 2.1, 2.2, 2.4_

- [ ] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Forward-Slash Path Behavior Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - **Observe on UNFIXED code**:
    - `get_child_entries(drive_id, "home/user/docs")` with forward-slash children returns correct children
    - `get_child_entries(drive_id, "home/user/empty")` with no children returns `[]`
    - CSV import of forward-slash paths stores them correctly with all metadata
    - `create_entries_bulk` with forward-slash paths inserts correctly
  - **Property-based test with Hypothesis**: Generate random forward-slash-only path hierarchies (segments of alphanumeric chars joined by `/`), insert entries, call `get_child_entries`, and assert the correct children are returned. This captures the baseline behavior that must be preserved.
  - **Preservation Requirements from design**:
    - All existing behavior for forward-slash paths must work identically (Req 3.1)
    - `get_child_entries` with no children must return empty list (Req 3.2)
    - CSV import of forward-slash paths must preserve all metadata, `original_path == path` after fix (Req 3.4)
  - Add tests to `src/db/test_path_normalization.py`
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.4_

- [ ] 3. Implement path separator normalization fix

  - [ ] 3.1 Add `original_path` column to schema DDL
    - In `src/db/schema.py`, add `original_path TEXT` to the `entries` CREATE TABLE statement, after the `path` column
    - The column stores the verbatim path from CSV import before normalization
    - _Bug_Condition: isBugCondition(input) where storedPath contains backslashes from TreeSize CSV_
    - _Expected_Behavior: original_path preserves verbatim OS-native path for export tooling_
    - _Preservation: Forward-slash paths will have original_path == path_
    - _Requirements: 2.5, 2.7, 3.4_

  - [ ] 3.2 Add `original_path` field to Entry Pydantic model
    - In `src/db/models.py`, add `original_path: str | None = None` to the `Entry` class
    - This makes the field available across all layers (repository, MCP server, export, UI)
    - _Requirements: 2.5, 2.7_

  - [ ] 3.3 Add `normalize_path` helper in `repository.py`
    - In `src/db/repository.py`, create a module-level function: `def normalize_path(p: str) -> str: return p.replace("\\", "/")`
    - This is the single normalization point used by all other changes
    - _Bug_Condition: isBugCondition(input) where path contains backslash separators_
    - _Expected_Behavior: normalize_path replaces all backslashes with forward slashes, output contains no backslashes_
    - _Requirements: 2.1, 2.5, 2.6_

  - [ ] 3.4 Normalize in `get_child_entries`
    - Apply `normalize_path` to `parent_path` before constructing the LIKE prefix
    - Change `parent_path.rstrip("/")` to `normalize_path(parent_path).rstrip("/")`
    - This ensures the LIKE prefix uses forward slashes, matching normalized stored paths
    - _Bug_Condition: get_child_entries builds prefix with rstrip("/") only, mismatches backslash paths_
    - _Expected_Behavior: get_child_entries returns all children regardless of separator style in query path_
    - _Preservation: Forward-slash paths produce identical prefix as before_
    - _Requirements: 2.1, 2.3, 3.1, 3.2_

  - [ ] 3.5 Normalize in `create_entries_bulk`
    - Apply `normalize_path` to the `path` value when building insert tuples
    - Store the original (un-normalized) path in the `original_path` column
    - Update the INSERT statement to include `original_path` in the column list
    - _Bug_Condition: paths stored verbatim with backslashes from CSV import_
    - _Expected_Behavior: path column contains normalized forward-slash path, original_path contains verbatim input_
    - _Preservation: Forward-slash paths are unchanged by normalize_path, original_path == path_
    - _Requirements: 2.5, 3.4_

  - [ ] 3.6 Normalize on CSV import in `csv_importer.py`
    - Import `normalize_path` from `src.db.repository`
    - When building the batch tuple, use `normalize_path(raw_path)` for the `path` position and `raw_path` for the new `original_path` position
    - Update the INSERT statement in both the bulk `executemany` and the row-by-row fallback to include `original_path`
    - _Bug_Condition: TreeSize CSV exports contain backslash paths stored verbatim_
    - _Expected_Behavior: imported paths are normalized; original_path preserves verbatim CSV value_
    - _Preservation: Forward-slash CSV paths import identically, original_path == path_
    - _Requirements: 2.5, 3.4_

  - [ ] 3.7 Normalize `path` parameter in `get_folder_summary` and fix subfolder filtering
    - In `src/mcp_server/server.py`, import `normalize_path` from `src.db.repository`
    - Apply `normalize_path` to the `path` parameter in `get_folder_summary` before passing to `get_child_entries`
    - Update the direct subfolder prefix construction: `prefix = normalize_path(path).rstrip("/") + "/"`
    - Update the subfolder check to use `"/" not in c.path[len(prefix):]` (works because stored paths are now normalized)
    - _Bug_Condition: get_folder_summary uses path.rstrip("/") + "/" which fails for backslash paths_
    - _Expected_Behavior: accurate file counts, size totals, distributions, and subfolder names for any separator style_
    - _Preservation: Forward-slash queries produce identical results_
    - _Requirements: 2.2, 2.4, 2.6, 3.1, 3.3_

  - [ ] 3.8 Use `original_path` in export
    - In `src/export.py`, update `entries_to_csv` and `entries_to_json` to use `e.original_path or e.path` as the `source_path` value
    - This ensures backup tooling receives the OS-native path for file operations
    - _Expected_Behavior: exported source_path uses original OS-native path with backslashes preserved_
    - _Preservation: Forward-slash entries export identically since original_path == path_
    - _Requirements: 2.7, 3.4_

  - [ ] 3.9 Create migration script for existing data
    - Create `src/scripts/migrate_paths.py` as a standalone script
    - The script should: add `original_path` column if it doesn't exist, copy current `path` values to `original_path`, normalize `path` values by replacing backslashes with forward slashes
    - Report how many rows were updated
    - Accept a `--db-path` argument (default: `drive_triage.db`)
    - _Requirements: 3.6_

  - [ ] 3.10 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Backslash Path Child Lookup Works
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior for backslash paths
    - When this test passes, it confirms: `get_child_entries` returns correct children for backslash paths, `normalize_path` produces consistent forward-slash paths
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6_

  - [ ] 3.11 Verify preservation tests still pass
    - **Property 2: Preservation** - Forward-Slash Path Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all forward-slash path behavior is identical after fix
    - _Requirements: 3.1, 3.2, 3.4_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Run the full test suite with `.venv/bin/pytest` to verify no regressions across the entire codebase
  - Verify bug condition exploration tests pass (backslash paths work correctly)
  - Verify preservation tests pass (forward-slash paths unchanged)
  - Verify existing tests in `src/db/`, `src/importer/`, `src/mcp_server/` still pass
  - Ask the user if questions arise

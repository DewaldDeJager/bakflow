# Path Separator Normalization Bugfix Design

## Overview

The bakflow system stores file paths from TreeSize CSV exports verbatim, preserving Windows-style backslash separators (`\`). However, all query logic in `repository.py` and `server.py` constructs path prefixes using forward slashes (`/`). This mismatch causes `get_child_entries` to return empty results for any folder whose stored path uses backslashes, breaking `get_folder_summary`, `_cascade_decision`, and direct subfolder detection.

The fix normalizes all paths to forward slashes at the storage boundary (CSV import) and at the query boundary (repository methods, MCP tool parameters). A new `original_path` column preserves the verbatim OS-native path for export and backup tooling. A one-time migration script handles existing data.

## Glossary

- **Bug_Condition (C)**: A path query where the stored path or the query parameter contains backslash separators, causing the forward-slash-based LIKE prefix to fail matching
- **Property (P)**: Child entry lookups, folder summaries, and cascade operations return correct results regardless of the separator style in stored paths or query parameters
- **Preservation**: All existing behavior for forward-slash paths, error handling, cascade guards, and non-path-related operations must remain unchanged
- **`normalize_path(p)`**: A new helper function that replaces all backslashes with forward slashes in a path string
- **`get_child_entries`**: Repository method that finds entries whose path starts with `parent_path + '/'` using SQL LIKE
- **`get_folder_summary`**: MCP tool that aggregates file counts, sizes, and subfolder names for a folder
- **`_cascade_decision`**: Internal MCP server function that propagates a backup decision to child entries of a folder
- **`original_path`**: New column in the `entries` table storing the verbatim path from the CSV import, before normalization

## Bug Details

### Bug Condition

The bug manifests when any path stored in the database contains backslash separators (typical of Windows TreeSize CSV exports). The `get_child_entries` method strips only forward slashes from the parent path and appends a forward slash to build the LIKE prefix. This prefix never matches backslash-separated stored paths, so all child lookups return zero rows.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type { storedPath: string, queryPath: string }
  OUTPUT: boolean

  RETURN containsBackslash(input.storedPath)
         OR containsBackslash(input.queryPath)
END FUNCTION

FUNCTION containsBackslash(path)
  RETURN '\\' IN path
END FUNCTION
```

### Examples

- **Example 1**: `get_child_entries(drive_id, "F:\\SteamLibrary\\steamapps")` → strips `/`, appends `/`, produces prefix `F:\\SteamLibrary\\steamapps/` → LIKE matches nothing → returns `[]`. Expected: returns all entries under that folder.
- **Example 2**: `get_folder_summary(drive_id, "D:\\Photos\\2024")` → delegates to `get_child_entries` → returns `file_count: 0`, empty distributions. Expected: accurate counts and distributions.
- **Example 3**: `record_decision(entry_id, "include", cascade_to_children=True)` on a folder with path `E:\\Projects\\myapp` → `get_child_entries` finds no children → cascade silently does nothing. Expected: cascades to all children.
- **Example 4 (edge case)**: `get_child_entries(drive_id, "C:/Users/mixed\\path")` → mixed separators produce an inconsistent prefix → partial or no matches. Expected: normalized prefix matches all children.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- All existing behavior for paths that already use forward slashes must continue to work identically
- `get_child_entries` called with a parent path that has no children must continue to return an empty list
- Error responses for invalid/missing `drive_id` in MCP tools must remain unchanged
- CSV import of rows with forward-slash paths must continue to import correctly with all metadata preserved
- `_cascade_decision` must continue to skip children whose `decision_status` is not `undecided`
- Status transition validation, audit logging, and cross-dimension guards are unaffected
- The `UNIQUE(drive_id, path)` constraint continues to prevent duplicate entries

**Scope:**
All inputs that do NOT involve backslash-containing paths should be completely unaffected by this fix. This includes:
- Queries with forward-slash-only paths
- Non-path-related operations (classification, review, decision recording without cascade)
- Error handling paths (missing drives, invalid parameters)
- UI rendering and display logic

## Hypothesized Root Cause

Based on the bug description and code analysis, the root causes are:

1. **`get_child_entries` prefix construction** (`repository.py` line ~270): `parent_path.rstrip("/") + "/"` only strips forward slashes. When `parent_path` is `F:\SteamLibrary\steamapps`, the prefix becomes `F:\SteamLibrary\steamapps/` which never matches stored paths like `F:\SteamLibrary\steamapps\common\game`.

2. **`get_folder_summary` direct subfolder filtering** (`server.py` line ~160): `path.rstrip("/") + "/"` has the same forward-slash-only assumption. The subsequent check `"/" not in c.path[len(prefix):]` also fails because stored child paths use `\` not `/`.

3. **No normalization at import time** (`csv_importer.py`): `raw_path` from the CSV is stored verbatim via `batch.append((drive_id, raw_path, ...))`. TreeSize on Windows produces backslash paths, which are stored as-is.

4. **No normalization at query time**: Neither the repository methods nor the MCP tool handlers normalize incoming path parameters before using them in SQL queries.

## Correctness Properties

Property 1: Bug Condition - Path normalization produces consistent forward-slash paths

_For any_ path string containing backslash separators, the `normalize_path` function SHALL replace all backslashes with forward slashes, producing a path that contains no backslash characters and is otherwise identical to the input.

**Validates: Requirements 2.1, 2.5, 2.6**

Property 2: Bug Condition - Child entry lookup works with backslash paths

_For any_ parent path (with any mix of forward and backslash separators) and a set of child entries stored with backslash paths, `get_child_entries` SHALL return all entries whose normalized path starts with the normalized parent path prefix, matching the same entries that would be found if all paths used forward slashes.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 3: Bug Condition - CSV import normalizes paths and preserves originals

_For any_ CSV row containing a path with backslash separators, after import the `path` column SHALL contain the forward-slash-normalized version and the `original_path` column SHALL contain the verbatim input path.

**Validates: Requirements 2.5, 2.7**

Property 4: Preservation - Forward-slash paths are unchanged

_For any_ input path that already uses only forward slashes, the fixed code SHALL produce exactly the same query results, import behavior, and export output as the original code. The `original_path` column SHALL equal the `path` column for these entries.

**Validates: Requirements 3.1, 3.4**

Property 5: Preservation - Non-path operations are unchanged

_For any_ operation that does not involve path-based queries (classification submission, status transitions, error handling for missing drives, cascade skip logic for non-undecided children), the fixed code SHALL produce exactly the same behavior as the original code.

**Validates: Requirements 3.2, 3.3, 3.5**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `src/db/schema.py`

**Changes**:
1. **Add `original_path` column**: Add `original_path TEXT` to the `entries` table DDL, after the `path` column. This column stores the verbatim path from the CSV import. It should default to the value of `path` (for forward-slash paths, they are identical).

**File**: `src/db/models.py`

**Changes**:
2. **Add `original_path` field**: Add `original_path: str | None = None` to the `Entry` Pydantic model so the field is available across all layers.

**File**: `src/db/repository.py`

**Changes**:
3. **Add `normalize_path` helper**: Create a module-level function `normalize_path(p: str) -> str` that returns `p.replace("\\", "/")`.
4. **Normalize in `get_child_entries`**: Apply `normalize_path` to `parent_path` before constructing the LIKE prefix. Change `rstrip` to strip both separators: `normalize_path(parent_path).rstrip("/")`.
5. **Normalize in `create_entries_bulk`**: Apply `normalize_path` to the `path` value when building insert tuples, and store the original value in `original_path`.

**File**: `src/importer/csv_importer.py`

**Changes**:
6. **Normalize on import**: Import `normalize_path` from `src.db.repository`. When building the batch tuple, use `normalize_path(raw_path)` for the `path` column and `raw_path` for the `original_path` column.
7. **Update INSERT statement**: Add `original_path` to the INSERT column list in both the bulk insert and the row-by-row fallback.

**File**: `src/mcp_server/server.py`

**Changes**:
8. **Normalize `path` parameter in `get_folder_summary`**: Import `normalize_path` and apply it to the `path` parameter before passing to `get_child_entries`.
9. **Fix direct subfolder filtering**: Update the prefix construction in `get_folder_summary` to use `normalize_path(path).rstrip("/") + "/"` and ensure the child path comparison uses normalized paths.

**File**: `src/export.py`

**Changes**:
10. **Use `original_path` in export**: In `entries_to_csv` and `entries_to_json`, use `e.original_path or e.path` as the `source_path` value so backup tooling gets the OS-native path.

**File**: `src/scripts/migrate_paths.py` (new)

**Changes**:
11. **Migration script**: Create a standalone script that:
    - Adds the `original_path` column if it doesn't exist
    - Copies current `path` values to `original_path`
    - Normalizes `path` values by replacing backslashes with forward slashes
    - Reports how many rows were updated

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that insert entries with backslash-separated paths into the database, then call `get_child_entries` and `get_folder_summary` and assert the expected results. Run these tests on the UNFIXED code to observe failures.

**Test Cases**:
1. **Child Lookup with Backslash Paths**: Insert entries with paths like `F:\SteamLibrary\steamapps\common\game`, call `get_child_entries(drive_id, "F:\\SteamLibrary\\steamapps")` — will return empty on unfixed code
2. **Folder Summary with Backslash Paths**: Call `get_folder_summary` on a folder with backslash children — will return `file_count: 0` on unfixed code
3. **Mixed Separator Query**: Call `get_child_entries` with a mixed-separator parent path — will return empty or partial results on unfixed code
4. **Cascade with Backslash Paths**: Record a decision with `cascade_to_children=True` on a backslash-path folder — will cascade to zero children on unfixed code

**Expected Counterexamples**:
- `get_child_entries` returns `[]` when children exist with backslash paths
- `get_folder_summary` returns `file_count: 0` for non-empty folders
- Root cause confirmed: `rstrip("/")` does not strip `\`, and the resulting LIKE prefix mismatches stored paths

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := get_child_entries_fixed(input.driveId, input.parentPath)
  ASSERT len(result) == expectedChildCount(input)
  ASSERT all children have paths starting with normalize_path(input.parentPath)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT get_child_entries_original(input) == get_child_entries_fixed(input)
  ASSERT get_folder_summary_original(input) == get_folder_summary_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many path strings automatically across the input domain
- It catches edge cases like empty paths, root paths, paths with trailing slashes
- It provides strong guarantees that behavior is unchanged for all forward-slash paths

**Test Plan**: Observe behavior on UNFIXED code first for forward-slash paths, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Forward-Slash Child Lookup Preservation**: Verify `get_child_entries` with forward-slash paths returns the same results before and after the fix
2. **Forward-Slash Import Preservation**: Verify CSV import of forward-slash paths produces identical entries, with `original_path == path`
3. **Error Handling Preservation**: Verify invalid drive_id still returns error responses
4. **Cascade Guard Preservation**: Verify cascade still skips non-undecided children

### Unit Tests

- Test `normalize_path` with backslash-only, forward-slash-only, mixed, and empty paths
- Test `get_child_entries` with backslash parent paths and backslash stored paths
- Test `get_folder_summary` direct subfolder detection with normalized paths
- Test CSV import stores normalized `path` and verbatim `original_path`
- Test export uses `original_path` for `source_path` output
- Test migration script correctly normalizes existing data

### Property-Based Tests

- Generate random path strings with Hypothesis (mix of `/`, `\`, alphanumeric segments) and verify `normalize_path` always produces a backslash-free string identical to `input.replace("\\", "/")`
- Generate random folder hierarchies with backslash paths, import them, and verify `get_child_entries` returns the correct children after normalization
- Generate random forward-slash paths and verify import + query behavior is identical to the unfixed code (preservation)

### Integration Tests

- End-to-end: import a CSV with backslash paths → classify → review → decide with cascade → export manifest with `original_path` values
- Verify `get_folder_summary` returns accurate counts and subfolder names for backslash-path folders after import
- Verify migration script on a database seeded with backslash paths, then confirm all queries work correctly

# Bugfix Requirements Document

## Introduction

The `get_folder_summary` MCP tool and the underlying `get_child_entries` repository method return empty results for folders that contain files. The root cause is a path separator mismatch: paths stored in the database use Windows-style backslashes (from TreeSize CSV exports), but `get_child_entries` constructs its LIKE prefix using forward slashes only (`parent_path.rstrip("/") + "/"`). The resulting prefix never matches backslash-separated paths, causing all child lookups to return zero results. This also affects `_cascade_decision` in the MCP server, which delegates to the same method.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `get_child_entries` is called with a parent path containing backslash separators (e.g., `F:\SteamLibrary\steamapps`) THEN the system strips only forward slashes and appends a forward slash, producing a mismatched prefix (e.g., `F:\SteamLibrary\steamapps/`) that matches no database rows

1.2 WHEN `get_folder_summary` is called with a valid drive and folder path using backslash separators THEN the system returns `file_count: 0`, empty `file_type_distribution`, and empty `subfolder_names` even though the folder contains files

1.3 WHEN `_cascade_decision` is called with `cascade_to_children=True` on a folder whose children use backslash-separated paths THEN the system finds no children to cascade to, silently skipping the cascade

1.4 WHEN `get_folder_summary` computes direct subfolders, it uses `path.rstrip("/") + "/"` to build the prefix for filtering THEN the system fails to identify any direct subfolders when paths use backslash separators

1.5 WHEN paths are imported from a TreeSize CSV containing backslash separators THEN the system stores them verbatim with backslashes, creating a permanent mismatch with forward-slash-based query logic

### Expected Behavior (Correct)

2.1 WHEN `get_child_entries` is called with a parent path using any combination of forward or backslash separators THEN the system SHALL normalize the path to use forward slashes before constructing the LIKE prefix, and match against normalized paths in the database

2.2 WHEN `get_folder_summary` is called with a valid drive and folder path using any separator style THEN the system SHALL return accurate file counts, size totals, file type distributions, and subfolder names

2.3 WHEN `_cascade_decision` is called with `cascade_to_children=True` on a folder THEN the system SHALL find and cascade to all child entries regardless of the separator style used in the input path or stored paths

2.4 WHEN `get_folder_summary` computes direct subfolders THEN the system SHALL correctly identify direct subfolders using normalized path separators

2.5 WHEN paths are imported from a TreeSize CSV containing backslash separators THEN the system SHALL normalize all backslashes to forward slashes in the `path` column and preserve the original verbatim path in a separate `original_path` column for use by export and backup tooling

2.6 WHEN query methods in the repository or MCP server receive path parameters THEN the system SHALL normalize incoming path parameters to forward slashes before using them in queries

2.7 WHEN the decision manifest is exported THEN the system SHALL include the `original_path` value so that backup tooling can reference files using the original OS-native path

### Unchanged Behavior (Regression Prevention)

3.1 WHEN paths are already stored with forward slashes THEN the system SHALL CONTINUE TO return correct child entries, folder summaries, and cascade results

3.2 WHEN `get_child_entries` is called with a parent path that has no children THEN the system SHALL CONTINUE TO return an empty list

3.3 WHEN `get_folder_summary` is called with an invalid or missing drive_id THEN the system SHALL CONTINUE TO return the appropriate error response

3.4 WHEN CSV import processes rows with valid forward-slash paths THEN the system SHALL CONTINUE TO import them correctly with all metadata preserved, and `original_path` SHALL equal `path`

3.5 WHEN `_cascade_decision` encounters children that already have a decision status other than `undecided` THEN the system SHALL CONTINUE TO skip those entries as before

3.6 WHEN existing data in the database uses backslash paths THEN a standalone migration script SHALL normalize stored paths (setting `path` to forward slashes and populating `original_path` with the original value) outside of the application runtime

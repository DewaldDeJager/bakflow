# Roadmap

- [ ] Define state transitions for cascading decisions, incl. classification details and cascading conflicts (overwriting)
- [ ] Handle mount path or drive letter changes/clashes
- [ ] Add support for more providers
    - [ ] Amazon Bedrock
    - [ ] OpenRouter?
- [ ] Add support for more import formats
    - [ ] Borg lists
    - [ ] *nix ls/tree formats
- [ ] Improve TreeSize CSV importer to use metadata at the top of the CSV instead of skipping it (eg. Report date and time, drive capacity, multi-drive exports.)
- [ ] Improve classification approach to be breadth-first and focus on folders
- [ ] Consider compacting the depth of subtrees with single-nested folders
- [ ] Improve context propagation for folders
- [ ] Add context propagation for files (eg. context on parent classification, siblings, nearby subtrees, etc.)
- [ ] Add heuristics to aid in classification without AI (/tmp, .DS_Store, etc.)
- [x] Add MCP tool for listing drives
- [ ] Deprecate SSE transport for MCP server
- [ ] Add support for remote database (MySQL/Consider DuckDB)
- [ ] Update get_unclassified_batch to return only essential fields to be token efficient
- [ ] Handle symlinks
- [ ] Add support for multiple partitions on a drive
- [ ] Add metadata about drive filesystem (NTFS, ext4, APFS, etc)
- [ ] Add support for multiple backup destinations
- [ ] Add support for rclone format export
- [ ] Create a user guide or similar documentation
- [ ] Wrap UI as a native desktop app
- [ ] Explore prompt caching
- [ ] Handle duplicates (identify and perform deduplication)
- [ ] Containerize the MCP Server
- [ ] Extend import log to include file hashes and metadata about when the export was run
- [ ] Add telemetry to understand classififier and LLM performance (incl. cost)
- [ ] Add benchmarking to compare use of different models, prompts or other meaningful changes
- [ ] Consider fine-tuning/training a model to improve performance
- [ ] Improve review queue priorization/sorting algorithm
- [ ] Add support for different modes that influence how granular to go (eg. cleanup vs. general backup)

### Bugs

1. .GamingRoot which is a file without an extension that got misclassified as a folder in the bare import.
    - `_infer_entry_type()` is too brittle - Consider making Type mandatory
2. depth derivation logic in the compute_tree_metadata is still the old incorrect logic
    - Should this even be there at all or can we assume that at this point all data should have a depth and parent_path?

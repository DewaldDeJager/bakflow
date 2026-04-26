# Roadmap

- [ ] Define state transitions for cascading decisions, incl. classification details and cascading conflicts (overwriting)
- [ ] Add support for Amazon Bedrock models
- [ ] Add support for importing Borg lists
- [ ] Add support for importing *nix ls/tree formats
- [ ] Improve classification approach to be breadth-first and focus on folders
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

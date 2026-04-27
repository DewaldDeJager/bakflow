# Product: bakflow

AI-assisted tool for classifying and triaging files on hard drive backups. Users import a TreeSize CSV export of a drive's file listing, then an LLM classifies each file/folder by purpose and importance. A human review workflow lets users accept, override, or reclassify AI decisions before making include/exclude/defer backup decisions. Final decisions are exported as a manifest (CSV or JSON) for use by backup tooling.

## Core Workflow

1. Register a drive and import its file listing from TreeSize CSV (with optional tree metadata columns)
2. LLM classifies entries using either wavefront classification (tree-aware BFS by depth, preferred) or flat batch classification
3. Human reviews AI classifications via a Streamlit UI, sorted by decision_confidence ascending (NULLs first)
4. Decisions (include/exclude/defer/descend) are recorded per entry, with optional cascade to child entries
5. Export a decision manifest for the drive (excludes `descend` entries — those are intermediate routing decisions)

## Key Concepts

- **Drive**: A registered hard drive identified by UUID, with optional volume serial/label
- **Entry**: A file or folder record tracked across three status dimensions: classification, review, and decision
- **Three-Dimension Status Model**: Each entry has independent `classification_status`, `review_status`, and `decision_status` with enforced valid transitions and cross-dimension guards
- **Wavefront Classification**: Tree-aware BFS traversal that classifies folders top-down by depth level. Each folder gets a triage signal: `include` (back up subtree), `exclude` (skip subtree), or `descend` (classify children individually). Pruned subtrees are never sent to the LLM.
- **Dual Confidence**: Each classified entry has `classification_confidence` (how sure the LLM is about the category) and `decision_confidence` (how sure it is about the triage decision). Priority review is driven by `decision_confidence`.
- **Tree Metadata**: Entries carry `depth`, `parent_path`, `child_count`, `descendant_file_count`, `descendant_folder_count` — parsed from TreeSize CSV or derived from path structure.
- **Audit Log**: Every status transition is recorded for traceability
- **Confidence Threshold**: Entries classified with `decision_confidence` below the threshold are flagged for priority review

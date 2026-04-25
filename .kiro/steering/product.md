# Product: bakflow

AI-assisted tool for classifying and triaging files on hard drive backups. Users import a TreeSize CSV export of a drive's file listing, then an LLM classifies each file/folder by purpose and importance. A human review workflow lets users accept, override, or reclassify AI decisions before making include/exclude/defer backup decisions. Final decisions are exported as a manifest (CSV or JSON) for use by backup tooling.

## Core Workflow

1. Register a drive and import its file listing from TreeSize CSV
2. LLM batch-classifies entries using a folder purpose taxonomy and file class taxonomy
3. Human reviews AI classifications via a Streamlit UI, sorted by confidence (lowest first)
4. Decisions (include/exclude/defer) are recorded per entry, with optional cascade to child entries
5. Export a decision manifest for the drive

## Key Concepts

- **Drive**: A registered hard drive identified by UUID, with optional volume serial/label
- **Entry**: A file or folder record tracked across three status dimensions: classification, review, and decision
- **Three-Dimension Status Model**: Each entry has independent `classification_status`, `review_status`, and `decision_status` with enforced valid transitions and cross-dimension guards
- **Audit Log**: Every status transition is recorded for traceability
- **Confidence Threshold**: Entries classified below the threshold are flagged for priority review

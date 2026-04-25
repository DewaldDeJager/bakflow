"""Export logic — CSV and JSON manifest generation.

Pure functions with no Streamlit dependency, usable from both the UI and tests.

Requirements: 4.2, 4.3, 4.4
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from src.db.models import Entry, Drive


def build_summary(drive: Drive, entries: list[Entry], decision_filter: str | None) -> dict:
    """Build the export summary header."""
    counts: dict[str, int] = {"include": 0, "exclude": 0, "defer": 0, "undecided": 0}
    for e in entries:
        counts[e.decision_status] = counts.get(e.decision_status, 0) + 1

    return {
        "drive_id": drive.id,
        "drive_label": drive.label,
        "volume_serial": drive.volume_serial,
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "decision_filter": decision_filter,
        "total_entries": len(entries),
        "counts_by_decision": counts,
    }


def entries_to_csv(entries: list[Entry], summary: dict) -> str:
    """Convert entries to CSV string with summary header."""
    output = io.StringIO()

    # Summary header as comments
    output.write(f"# Drive UUID: {summary['drive_id']}\n")
    output.write(f"# Drive Label: {summary['drive_label']}\n")
    output.write(f"# Volume Serial: {summary['volume_serial'] or 'N/A'}\n")
    output.write(f"# Export Timestamp: {summary['export_timestamp']}\n")
    for status, count in summary["counts_by_decision"].items():
        output.write(f"# {status}: {count}\n")
    output.write("#\n")

    writer = csv.writer(output)
    writer.writerow([
        "source_path", "destination_path", "entry_type",
        "classification", "confidence", "decision", "notes",
    ])
    for e in entries:
        classification = e.folder_purpose or e.file_class or ""
        writer.writerow([
            e.original_path or e.path,
            e.decision_destination or "",
            e.entry_type,
            classification,
            f"{e.confidence:.4f}" if e.confidence is not None else "",
            e.decision_status,
            e.decision_notes or "",
        ])

    return output.getvalue()


def entries_to_json(entries: list[Entry], summary: dict) -> str:
    """Convert entries to JSON string with summary header."""
    records = []
    for e in entries:
        classification = e.folder_purpose or e.file_class or None
        records.append({
            "source_path": e.original_path or e.path,
            "destination_path": e.decision_destination,
            "entry_type": e.entry_type,
            "classification": classification,
            "confidence": e.confidence,
            "decision": e.decision_status,
            "notes": e.decision_notes,
        })

    payload = {
        "summary": summary,
        "entries": records,
    }
    return json.dumps(payload, indent=2)

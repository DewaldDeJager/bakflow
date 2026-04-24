"""Property-based tests for export round-trip (P14).

Property 14: Export round-trip
Exporting to CSV and parsing back recovers all Entry records with correct
columns; same for JSON; header contains correct Drive info and accurate counts.

**Validates: Requirements 4.2, 4.3, 4.4**
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import datetime

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.db.schema import init_db
from src.db.repository import Repository
from src.db.status import apply_transition
from src.db.models import Entry
from src.export import entries_to_csv, entries_to_json, build_summary


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_label_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip())

_volume_serial_strategy = st.one_of(
    st.none(),
    st.text(alphabet="ABCDEF0123456789", min_size=4, max_size=12),
)

_decision_strategy = st.sampled_from(["include", "exclude", "defer"])

_confidence_strategy = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

_folder_purpose_strategy = st.sampled_from([
    "irreplaceable_personal", "important_personal", "project_or_work",
    "reinstallable_software", "media_archive", "redundant_duplicate",
    "system_or_temp", "unknown_review_needed",
])

_file_class_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1, max_size=30,
)

_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1, max_size=30,
)

_notes_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Z")),
        min_size=1, max_size=50,
    ).filter(lambda s: "\n" not in s and "\r" not in s),
)

_destination_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P")),
        min_size=1, max_size=60,
    ).filter(lambda s: "\n" not in s and "\r" not in s),
)


# Entry spec: (name, entry_type, classification, confidence, decision, destination, notes)
_entry_spec_strategy = st.tuples(
    _name_strategy,
    st.sampled_from(["file", "folder"]),
    _folder_purpose_strategy,  # used for folders; file_class for files
    _file_class_strategy,
    _confidence_strategy,
    _decision_strategy,
    _destination_strategy,
    _notes_strategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    return conn, Repository(conn), path


def _create_reviewed_entries(
    conn, repo, drive_id, entry_specs,
) -> list[Entry]:
    """Create entries, classify them, review them, and apply decisions.

    Returns the list of final Entry objects.
    """
    entries_data = []
    for i, (name, entry_type, folder_purpose, file_class, conf, decision, dest, notes) in enumerate(entry_specs):
        ext = ".txt" if entry_type == "file" else None
        entries_data.append({
            "drive_id": drive_id,
            "path": f"/drive/{i}/{name}",
            "name": name,
            "entry_type": entry_type,
            "extension": ext,
            "size_bytes": i * 1000,
            "last_modified": "2024-01-15 10:00:00",
        })

    repo.create_entries_bulk(entries_data)
    all_entries = repo.get_entries_by_drive(drive_id)

    result = []
    for entry, (name, entry_type, folder_purpose, file_class, conf, decision, dest, notes) in zip(
        all_entries, entry_specs
    ):
        # Classify
        if entry_type == "file":
            conn.execute(
                "UPDATE entries SET file_class = ?, confidence = ? WHERE id = ?",
                (file_class, conf, entry.id),
            )
        else:
            conn.execute(
                "UPDATE entries SET folder_purpose = ?, confidence = ? WHERE id = ?",
                (folder_purpose, conf, entry.id),
            )
        conn.commit()
        apply_transition(conn, entry.id, "classification_status", "ai_classified")

        # Review
        apply_transition(conn, entry.id, "review_status", "reviewed")

        # Decision
        conn.execute(
            "UPDATE entries SET decision_destination = ?, decision_notes = ? WHERE id = ?",
            (dest, notes, entry.id),
        )
        conn.commit()
        apply_transition(conn, entry.id, "decision_status", decision)

        result.append(repo.get_entry(entry.id))

    return result


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

class TestExportRoundTrip:
    """P14: Export round-trip — CSV and JSON exports can be parsed back
    with correct columns and accurate summary counts."""

    @given(
        label=_label_strategy,
        volume_serial=_volume_serial_strategy,
        entry_specs=st.lists(_entry_spec_strategy, min_size=1, max_size=10),
    )
    @settings(max_examples=100)
    def test_csv_roundtrip(self, label, volume_serial, entry_specs):
        """Exporting to CSV and parsing back recovers all entries with
        correct column values."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(label=label, volume_serial=volume_serial)
            entries = _create_reviewed_entries(conn, repo, drive.id, entry_specs)

            summary = build_summary(drive, entries, "All reviewed")
            csv_str = entries_to_csv(entries, summary)

            # Parse back
            lines = csv_str.split("\n")
            # Skip comment lines
            data_lines = [l for l in lines if not l.startswith("#") and l.strip()]
            reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
            rows = list(reader)

            assert len(rows) == len(entries), (
                f"Expected {len(entries)} rows, got {len(rows)}"
            )

            for row, entry in zip(rows, entries):
                assert row["source_path"] == entry.path
                assert row["destination_path"] == (entry.decision_destination or "")
                assert row["entry_type"] == entry.entry_type
                expected_class = entry.folder_purpose or entry.file_class or ""
                assert row["classification"] == expected_class
                assert row["decision"] == entry.decision_status
                assert row["notes"] == (entry.decision_notes or "")
                if entry.confidence is not None:
                    parsed_conf = float(row["confidence"])
                    assert abs(parsed_conf - entry.confidence) < 1e-3

            # Verify summary header comments
            header_lines = [l for l in lines if l.startswith("#")]
            header_text = "\n".join(header_lines)
            assert drive.id in header_text
            assert drive.label in header_text
        finally:
            conn.close()
            os.unlink(path)

    @given(
        label=_label_strategy,
        volume_serial=_volume_serial_strategy,
        entry_specs=st.lists(_entry_spec_strategy, min_size=1, max_size=10),
    )
    @settings(max_examples=100)
    def test_json_roundtrip(self, label, volume_serial, entry_specs):
        """Exporting to JSON and parsing back recovers all entries with
        correct values and accurate summary."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(label=label, volume_serial=volume_serial)
            entries = _create_reviewed_entries(conn, repo, drive.id, entry_specs)

            summary = build_summary(drive, entries, "All reviewed")
            json_str = entries_to_json(entries, summary)

            # Parse back
            parsed = json.loads(json_str)

            assert "summary" in parsed
            assert "entries" in parsed
            assert len(parsed["entries"]) == len(entries)

            # Verify summary
            s = parsed["summary"]
            assert s["drive_id"] == drive.id
            assert s["drive_label"] == drive.label
            assert s["volume_serial"] == drive.volume_serial
            assert s["total_entries"] == len(entries)

            # Verify entries
            for record, entry in zip(parsed["entries"], entries):
                assert record["source_path"] == entry.path
                assert record["destination_path"] == entry.decision_destination
                assert record["entry_type"] == entry.entry_type
                expected_class = entry.folder_purpose or entry.file_class or None
                assert record["classification"] == expected_class
                assert record["decision"] == entry.decision_status
                assert record["notes"] == entry.decision_notes
                if entry.confidence is not None:
                    assert abs(record["confidence"] - entry.confidence) < 1e-6
        finally:
            conn.close()
            os.unlink(path)

    @given(
        label=_label_strategy,
        entry_specs=st.lists(_entry_spec_strategy, min_size=1, max_size=10),
    )
    @settings(max_examples=100)
    def test_summary_counts_accurate(self, label, entry_specs):
        """Summary header contains accurate counts per decision status."""
        conn, repo, path = _make_temp_db()
        try:
            drive = repo.create_drive(label=label)
            entries = _create_reviewed_entries(conn, repo, drive.id, entry_specs)

            summary = build_summary(drive, entries, "All reviewed")

            # Count actual decisions
            actual_counts: dict[str, int] = {}
            for e in entries:
                actual_counts[e.decision_status] = actual_counts.get(e.decision_status, 0) + 1

            for status, count in actual_counts.items():
                assert summary["counts_by_decision"][status] == count, (
                    f"Expected {count} for {status}, got {summary['counts_by_decision'].get(status)}"
                )

            assert summary["total_entries"] == len(entries)
            assert summary["drive_id"] == drive.id
            assert summary["drive_label"] == drive.label
        finally:
            conn.close()
            os.unlink(path)

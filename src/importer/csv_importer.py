"""TreeSize CSV parsing and Entry creation.

Parses CSV exports from TreeSize (or similar tools), creates Entry records
in the Index with default statuses, and logs import metadata.
"""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import sqlite3


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ColumnMapping:
    """Configurable column names for CSV parsing."""

    path: str = "Path"
    name: str = "Name"
    size: str = "Size"
    last_modified: str = "Last Modified"
    entry_type: str = "Type"  # may be absent — inferred from extension


@dataclass
class SkipDetail:
    """Details about a skipped CSV row."""

    row_number: int
    reason: str


@dataclass
class ImportResult:
    """Summary of a CSV import operation."""

    drive_id: str
    drive_label: str
    entries_created: int
    rows_skipped: int
    skip_details: list[SkipDetail] = field(default_factory=list)


class ConflictError(Exception):
    """Raised when importing into a drive that already has entries."""

    def __init__(self, drive_id: str, existing_count: int) -> None:
        self.drive_id = drive_id
        self.existing_count = existing_count
        super().__init__(
            f"Drive {drive_id} already has {existing_count} entries. "
            "Use force=True to add entries anyway."
        )



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Pattern to fix unquoted comma-decimal percent values that break CSV parsing.
# Matches e.g. ``100,0 %`` or ``0,4 %`` that are NOT inside quotes.
_PERCENT_COMMA_RE = re.compile(r"(?<=,)(\d+),(\d+ %)")


def _sanitise_csv_line(line: str) -> str:
    """Fix TreeSize CSV quirks in a single line.

    - Replaces non-breaking spaces (\\xa0) with regular spaces.
    - Fixes comma-decimal percent values (``100,0 %`` → ``100.0 %``) that
      would otherwise be mis-parsed as an extra CSV field.
    """
    line = line.replace("\xa0", " ")
    line = _PERCENT_COMMA_RE.sub(r"\1.\2", line)
    return line


# Common file extensions — used to infer entry_type when no Type column exists
_FILE_EXTENSIONS = frozenset({
    ".txt", ".doc", ".docx", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx",
    ".csv", ".json", ".xml", ".html", ".htm", ".md", ".rtf",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".svg", ".webp",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma",
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".rb", ".go",
    ".rs", ".swift", ".kt", ".php", ".sh", ".bat", ".ps1",
    ".exe", ".dll", ".so", ".dylib", ".msi", ".dmg", ".app",
    ".db", ".sqlite", ".sql", ".log", ".ini", ".cfg", ".yaml", ".yml",
    ".toml", ".env", ".iso", ".img", ".vmdk", ".vhd",
})


def _infer_entry_type(path: str, extension: str | None) -> str:
    """Infer whether a path represents a file or folder.

    Heuristic:
    - If the path ends with a separator, it's a folder.
    - If there's a recognised file extension, it's a file.
    - If there's any extension (dot in the last component), treat as file.
    - Otherwise, treat as folder.
    """
    if path.endswith("/") or path.endswith("\\"):
        return "folder"
    if extension and extension.lower() in _FILE_EXTENSIONS:
        return "file"
    if extension:
        return "file"
    return "folder"


def _extract_extension(path: str) -> str | None:
    """Extract the file extension from a path, or None if there isn't one."""
    _, ext = os.path.splitext(path)
    return ext.lower() if ext else None


def _extract_name(path: str) -> str:
    """Extract the file/folder name from a full path."""
    # Strip trailing separators for folders
    cleaned = path.rstrip("/\\")
    return os.path.basename(cleaned) or cleaned


def _parse_size(raw: str) -> int:
    """Parse a size string into bytes. Handles plain integers and common suffixes.

    Also handles TreeSize format like ``85 218 497 486 Bytes`` where spaces
    are used as thousands separators.
    """
    raw = raw.strip()
    if not raw:
        return 0

    # Strip a trailing "Bytes" / "bytes" suffix first (TreeSize format)
    raw_lower = raw.lower()
    if raw_lower.endswith("bytes"):
        raw = raw[: -len("bytes")].strip()

    # Remove space/thin-space thousands separators so "85 218 497 486" → "85218497486"
    raw = raw.replace("\u202f", "").replace(" ", "")

    # Replace comma decimal separator with dot (e.g. "1,5" → "1.5")
    # but only when it looks like a decimal (single comma with digits on both sides)
    if raw.count(",") == 1:
        raw = raw.replace(",", ".")

    # Try plain integer
    try:
        return int(raw)
    except ValueError:
        pass

    # Try float (some CSVs use decimal bytes)
    try:
        return int(float(raw))
    except ValueError:
        pass

    # Handle suffixes like "1.5 KB", "200 MB", etc.
    suffixes = {
        "b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4,
    }
    raw_lower = raw.lower().strip()
    for suffix, multiplier in sorted(suffixes.items(), key=lambda x: -len(x[0])):
        if raw_lower.endswith(suffix):
            num_part = raw_lower[: -len(suffix)].strip()
            try:
                return int(float(num_part) * multiplier)
            except ValueError:
                pass

    return 0


def _parse_timestamp(raw: str) -> str | None:
    """Try to parse a timestamp string. Returns ISO format or None."""
    raw = raw.strip()
    if not raw:
        return None

    # Common formats from TreeSize and similar tools
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M %p",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.isoformat()
        except ValueError:
            continue

    # If it already looks like ISO, return as-is
    try:
        datetime.fromisoformat(raw)
        return raw
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------


def import_csv(
    conn: sqlite3.Connection,
    csv_path: str,
    drive_id: str,
    column_mapping: ColumnMapping | None = None,
    force: bool = False,
    skip_rows: int = 0,
) -> ImportResult:
    """Parse a TreeSize CSV and create Entry records in the Index.

    Args:
        conn: SQLite connection (with schema already initialised).
        csv_path: Path to the CSV file on disk.
        drive_id: UUID of the Drive to associate entries with.
        column_mapping: Optional custom column name mapping.
        force: If True, allow importing into a drive that already has entries.
        skip_rows: Number of preamble lines to skip before the header row.

    Returns:
        ImportResult with counts and skip details.

    Raises:
        ConflictError: If the drive already has entries and force is False.
        ValueError: If the drive does not exist.
    """
    if column_mapping is None:
        column_mapping = ColumnMapping()

    # Verify drive exists
    drive_row = conn.execute(
        "SELECT id, label FROM drives WHERE id = ?", (drive_id,)
    ).fetchone()
    if drive_row is None:
        raise ValueError(f"Drive with id={drive_id} not found")
    drive_label = drive_row[1]

    # Check for existing entries (conflict detection)
    existing_count = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE drive_id = ?", (drive_id,)
    ).fetchone()[0]
    if existing_count > 0 and not force:
        raise ConflictError(drive_id, existing_count)

    started_at = datetime.utcnow().isoformat()

    entries_created = 0
    skip_details: list[SkipDetail] = []

    # Determine if the CSV has a header with the expected type column
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        # Skip preamble lines (e.g. TreeSize report metadata)
        for _ in range(skip_rows):
            f.readline()

        # Sanitise remaining lines to fix TreeSize quirks (non-breaking
        # spaces, comma-decimal percent values) before CSV parsing.
        sanitised = io.StringIO("".join(_sanitise_csv_line(l) for l in f))
        reader = csv.DictReader(sanitised)
        if reader.fieldnames is None:
            return ImportResult(
                drive_id=drive_id,
                drive_label=drive_label,
                entries_created=0,
                rows_skipped=0,
                skip_details=[],
            )

        has_type_column = column_mapping.entry_type in reader.fieldnames
        has_name_column = column_mapping.name in reader.fieldnames

        batch: list[tuple] = []

        for row_idx, row in enumerate(reader, start=skip_rows + 2):  # account for skipped + header
            try:
                # Path is required
                raw_path = row.get(column_mapping.path, "").strip()
                if not raw_path:
                    skip_details.append(SkipDetail(
                        row_number=row_idx,
                        reason="missing or empty path",
                    ))
                    continue

                # Name: from column or derived from path
                if has_name_column:
                    name = row.get(column_mapping.name, "").strip()
                    if not name:
                        name = _extract_name(raw_path)
                else:
                    name = _extract_name(raw_path)

                # Extension
                extension = _extract_extension(raw_path)

                # Entry type: from column or inferred
                if has_type_column:
                    raw_type = row.get(column_mapping.entry_type, "").strip().lower()
                    if raw_type in ("file", "folder"):
                        entry_type = raw_type
                    elif raw_type:
                        # Unrecognised type value — try to infer
                        entry_type = _infer_entry_type(raw_path, extension)
                    else:
                        entry_type = _infer_entry_type(raw_path, extension)
                else:
                    entry_type = _infer_entry_type(raw_path, extension)

                # If it's a folder, clear the extension
                if entry_type == "folder":
                    extension = None

                # Size
                raw_size = row.get(column_mapping.size, "0").strip()
                size_bytes = _parse_size(raw_size)

                # Last modified
                raw_modified = row.get(column_mapping.last_modified, "").strip()
                last_modified = _parse_timestamp(raw_modified)

                batch.append((
                    drive_id,
                    raw_path,
                    name,
                    entry_type,
                    extension,
                    size_bytes,
                    last_modified,
                ))

            except Exception as exc:
                skip_details.append(SkipDetail(
                    row_number=row_idx,
                    reason=str(exc),
                ))

    # Bulk insert
    if batch:
        try:
            conn.executemany(
                "INSERT INTO entries "
                "(drive_id, path, name, entry_type, extension, size_bytes, last_modified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            conn.commit()
            entries_created = len(batch)
        except sqlite3.IntegrityError as exc:
            # Handle duplicate path conflicts — fall back to row-by-row insert
            conn.rollback()
            entries_created = 0
            for row_tuple in batch:
                try:
                    conn.execute(
                        "INSERT INTO entries "
                        "(drive_id, path, name, entry_type, extension, size_bytes, last_modified) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        row_tuple,
                    )
                    entries_created += 1
                except sqlite3.IntegrityError:
                    # Duplicate path for this drive — skip silently on force re-import
                    pass
            conn.commit()

    # Write import log
    conn.execute(
        "INSERT INTO import_log (drive_id, csv_path, entries_created, rows_skipped, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (drive_id, csv_path, entries_created, len(skip_details), started_at),
    )
    conn.commit()

    return ImportResult(
        drive_id=drive_id,
        drive_label=drive_label,
        entries_created=entries_created,
        rows_skipped=len(skip_details),
        skip_details=skip_details,
    )

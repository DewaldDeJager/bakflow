"""One-time migration: fix extensions extracted from full paths instead of filenames.

Usage:
    .venv/Scripts/python -m src.scripts.migrate_extensions [--db-path DB_PATH]

Background:
    The original ``_extract_extension`` used ``os.path.splitext`` on the full
    path, which could pick up dots from parent directories (e.g. ``.app`` in
    ``MyGame.app/Contents/MacOS/python`` produced the bogus
    extension ``.app\\contents\\macos\\python``).  The fix extracts the
    basename first, but existing rows need their extensions recalculated.

Steps:
1. For each entry, recompute the extension from the filename (basename of path).
2. Clear the extension for folder entries.
3. Report how many rows were updated.
"""

from __future__ import annotations

import argparse
import os
import sqlite3


def _extension_from_name(name: str) -> str | None:
    """Extract extension from a filename (not a full path)."""
    _, ext = os.path.splitext(name)
    return ext.lower() if ext else None


def migrate(db_path: str) -> int:
    """Run the migration and return the number of rows updated."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    rows = conn.execute(
        "SELECT id, name, entry_type, extension FROM entries "
        "WHERE extension LIKE '%\\%'"
    ).fetchall()

    updated = 0
    for row_id, name, entry_type, old_ext in rows:
        if entry_type == "folder":
            new_ext = None
        else:
            new_ext = _extension_from_name(name)

        if new_ext != old_ext:
            conn.execute(
                "UPDATE entries SET extension = ? WHERE id = ?",
                (new_ext, row_id),
            )
            updated += 1

    conn.commit()
    conn.close()
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix extensions that were incorrectly extracted from full paths."
    )
    parser.add_argument(
        "--db-path",
        default="drive_triage.db",
        help="Path to the SQLite database (default: drive_triage.db)",
    )
    args = parser.parse_args()

    updated = migrate(args.db_path)
    print(f"Migration complete. {updated} row(s) had extensions corrected.")


if __name__ == "__main__":
    main()

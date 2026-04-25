"""One-time migration: normalize backslash paths and populate original_path.

Usage:
    .venv/bin/python -m src.scripts.migrate_paths [--db-path DB_PATH]

Steps:
1. Add ``original_path`` column if it doesn't exist.
2. Copy current ``path`` values into ``original_path``.
3. Replace backslashes with forward slashes in ``path``.
4. Report how many rows were updated.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys


def migrate(db_path: str) -> int:
    """Run the migration and return the number of rows updated."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # 1. Add original_path column if missing
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()
    }
    if "original_path" not in columns:
        conn.execute("ALTER TABLE entries ADD COLUMN original_path TEXT")
        conn.commit()

    # 2. Copy path → original_path (only where original_path is still NULL or empty)
    conn.execute(
        "UPDATE entries SET original_path = path "
        "WHERE original_path IS NULL OR original_path = ''"
    )
    conn.commit()

    # 3. Normalize path: replace backslashes with forward slashes
    cur = conn.execute(
        "UPDATE entries SET path = REPLACE(path, '\\', '/') "
        "WHERE path LIKE '%\\%'"
    )
    conn.commit()
    updated = cur.rowcount

    conn.close()
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize backslash paths in the entries table."
    )
    parser.add_argument(
        "--db-path",
        default="drive_triage.db",
        help="Path to the SQLite database (default: drive_triage.db)",
    )
    args = parser.parse_args()

    updated = migrate(args.db_path)
    print(f"Migration complete. {updated} row(s) had paths normalized.")


if __name__ == "__main__":
    main()

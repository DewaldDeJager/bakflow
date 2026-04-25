"""CLI entry points for Drive Backup Triage."""

import argparse
import sys

from src.config import AppConfig
from src.db.schema import init_db
from src.db.repository import Repository
from src.importer.csv_importer import import_csv, ConflictError


def cmd_run_server(args: argparse.Namespace) -> None:
    """Start the MCP server."""
    config = AppConfig()
    db_path = getattr(args, "db_path", None) or config.db_path

    # Ensure the database exists
    conn = init_db(db_path)
    conn.close()

    from src.mcp_server.server import init_server

    app = init_server(db_path)
    transport = getattr(args, "transport", "stdio")
    app.run(transport=transport)


def cmd_run_ui(args: argparse.Namespace) -> None:
    """Launch the Streamlit review UI."""
    import subprocess
    import pathlib

    app_path = pathlib.Path(__file__).parent / "ui" / "app.py"
    port = getattr(args, "port", None) or "8501"

    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(app_path),
        "--server.port", str(port),
        "--server.headless", "true",
    ]
    subprocess.run(cmd)


def cmd_init_db(args: argparse.Namespace) -> None:
    """Initialize the SQLite database."""
    config = AppConfig()
    db_path = getattr(args, "db_path", None) or config.db_path
    conn = init_db(db_path)
    conn.close()
    print(f"Database initialized at {db_path}")


def cmd_import_csv(args: argparse.Namespace) -> None:
    """Import a TreeSize CSV export into the index."""
    config = AppConfig()
    db_path = config.db_path
    conn = init_db(db_path)
    repo = Repository(conn)

    try:
        # Create or reuse a drive
        drive = repo.create_drive(
            label=args.drive_label,
            volume_serial=args.volume_serial,
            volume_label=args.volume_label,
            capacity_bytes=int(args.capacity) if args.capacity else None,
        )

        result = import_csv(
            conn=conn,
            csv_path=args.csv_path,
            drive_id=drive.id,
            force=args.force,
            skip_rows=args.skip_rows,
        )

        print(f"Drive registered: {drive.id} ({drive.label})")
        print(f"Entries created: {result.entries_created}")
        print(f"Rows skipped:    {result.rows_skipped}")
        for detail in result.skip_details:
            print(f"  Row {detail.row_number}: {detail.reason}")
    except ConflictError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: CSV file not found: {args.csv_path}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="drive-backup-triage",
        description="AI-assisted drive backup triage tool",
    )
    subparsers = parser.add_subparsers(dest="command")

    server_parser = subparsers.add_parser("run-server", help="Start the MCP server")
    server_parser.add_argument("--db-path", default=None, help="Path to the database file")
    server_parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport protocol (default: stdio)",
    )

    ui_parser = subparsers.add_parser("run-ui", help="Launch the Streamlit review UI")
    ui_parser.add_argument("--port", default="8501", help="Port for the Streamlit server (default: 8501)")

    import_parser = subparsers.add_parser("import-csv", help="Import a TreeSize CSV")
    import_parser.add_argument("csv_path", help="Path to the CSV file")
    import_parser.add_argument("--drive-label", required=True, help="Label for the drive")
    import_parser.add_argument("--volume-serial", default=None, help="Volume serial number")
    import_parser.add_argument("--volume-label", default=None, help="Volume label")
    import_parser.add_argument("--capacity", default=None, help="Drive capacity in bytes")
    import_parser.add_argument("--force", action="store_true", help="Force re-import if entries exist")
    import_parser.add_argument("--skip-rows", type=int, default=0, help="Number of preamble lines to skip before the CSV header")

    init_parser = subparsers.add_parser("init-db", help="Initialize the database")
    init_parser.add_argument("--db-path", default=None, help="Path to the database file")

    return parser


COMMANDS = {
    "run-server": cmd_run_server,
    "run-ui": cmd_run_ui,
    "import-csv": cmd_import_csv,
    "init-db": cmd_init_db,
}


def main() -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    handler = COMMANDS[args.command]
    handler(args)


if __name__ == "__main__":
    main()

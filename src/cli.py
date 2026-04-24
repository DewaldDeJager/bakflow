"""CLI entry points for Drive Backup Triage."""

import argparse
import sys


def cmd_run_server(args: argparse.Namespace) -> None:
    """Start the MCP server."""
    print("Not yet implemented")


def cmd_run_ui(args: argparse.Namespace) -> None:
    """Launch the Streamlit review UI."""
    print("Not yet implemented")


def cmd_import_csv(args: argparse.Namespace) -> None:
    """Import a TreeSize CSV export into the index."""
    print("Not yet implemented")


def cmd_init_db(args: argparse.Namespace) -> None:
    """Initialize the SQLite database."""
    print("Not yet implemented")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="drive-backup-triage",
        description="AI-assisted drive backup triage tool",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run-server", help="Start the MCP server")
    subparsers.add_parser("run-ui", help="Launch the Streamlit review UI")

    import_parser = subparsers.add_parser("import-csv", help="Import a TreeSize CSV")
    import_parser.add_argument("csv_path", help="Path to the CSV file")
    import_parser.add_argument("--drive-label", required=True, help="Label for the drive")

    subparsers.add_parser("init-db", help="Initialize the database")

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

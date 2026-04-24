# Drive Backup Triage

AI-assisted tool for classifying and triaging files on hard drive backups. Import a [TreeSize](https://www.jam-software.com/treesize) CSV export, let an LLM classify each entry by purpose and importance, then review and make include/exclude/defer decisions via a Streamlit UI.

## Quick Start

```bash
# Install in development mode
pip install -e ".[dev]"

# Initialize the database
drive-backup-triage init-db

# Import a TreeSize CSV
drive-backup-triage import-csv THREADRIPPER_F.csv --drive-label "Threadripper F:" --skip-rows 4
```

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com/) running locally (default LLM provider), or an OpenAI API key

## CLI Reference

### `init-db`

Creates the SQLite database with all required tables, indexes, and triggers.

```bash
drive-backup-triage init-db [--db-path PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--db-path` | `drive_triage.db` | Path to the SQLite database file |

### `import-csv`

Imports a TreeSize CSV export into the database, registering a drive and creating entries for every file and folder in the listing.

```bash
drive-backup-triage import-csv <csv_path> --drive-label <label> [options]
```

| Argument / Flag | Required | Default | Description |
|-----------------|----------|---------|-------------|
| `csv_path` | yes | — | Path to the TreeSize CSV file |
| `--drive-label` | yes | — | Human-readable label for the drive (e.g. `"Backup Drive E:"`) |
| `--skip-rows` | no | `0` | Number of preamble lines to skip before the CSV header (TreeSize typically adds 4) |
| `--volume-serial` | no | — | Volume serial number for the drive |
| `--volume-label` | no | — | Volume label from the OS |
| `--capacity` | no | — | Drive capacity in bytes |
| `--force` | no | `false` | Allow re-importing into a drive that already has entries |

### `run-server`

Starts the MCP server (FastMCP) exposing triage tools to LLM clients.

```bash
drive-backup-triage run-server
```

### `run-ui`

Launches the Streamlit review UI.

```bash
drive-backup-triage run-ui
```

## Importing TreeSize CSVs

TreeSize exports include a few lines of report metadata before the actual CSV header. A typical export looks like this:

```
TreeSize Report, 2026/04/24  21:33
  F:\
Drive: F:\      Size: 215,8 GB      Used: 79,8 GB      Free: 136,0 GB

Name,Path,Size,Files,Folders,% of Parent (Allocated),Last Modified,Last Accessed,Type,File Extension
"F:\","F:\",85 218 497 486 Bytes,37 870,1 313,100,0 %,2026/04/24,2026/04/24,"Folder",""
...
```

Use `--skip-rows 4` to skip the preamble. The importer automatically handles:

- Non-breaking space (`\xa0`) thousands separators in sizes (e.g. `85 218 497 486 Bytes`)
- Comma-decimal percent values (e.g. `100,0 %`) that would otherwise break CSV parsing
- `YYYY/MM/DD` date formats
- Type inference from the `Type` column or from file extensions/path patterns when absent

### Expected Columns

The importer looks for these column headers (case-sensitive):

| Column | Used for |
|--------|----------|
| `Path` | Full path of the file or folder |
| `Name` | Display name (falls back to extracting from path) |
| `Size` | Size in bytes (parsed from various formats) |
| `Last Modified` | Timestamp for the entry |
| `Type` | `Folder`, file type description, etc. |

## Running Tests

```bash
# All tests
pytest

# Just the importer tests
pytest src/importer/ -v

# Just the database tests
pytest src/db/ -v
```

## Project Structure

```
src/
├── cli.py               # CLI entry points
├── config.py            # AppConfig (db path, LLM settings, thresholds)
├── db/                  # Data layer (schema, models, repository, status engine)
├── importer/            # TreeSize CSV import
├── classifier/          # LLM classification (Ollama / OpenAI)
├── mcp_server/          # FastMCP tool definitions
└── ui/                  # Streamlit multi-page app
```

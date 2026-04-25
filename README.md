# bakflow

AI-assisted tool for classifying and triaging files on hard drive backups. Import a [TreeSize](https://www.jam-software.com/treesize) CSV export, let an LLM classify each entry by purpose and importance, then review and make include/exclude/defer decisions via a Streamlit UI.

## Quick Start

```bash
# Install in development mode
pip install -e ".[dev]"

# Initialize the database
bakflow init-db

# Import a TreeSize CSV
bakflow import-csv THREADRIPPER_F.csv --drive-label "Threadripper F:" --skip-rows 4
```

## Requirements

- Python 3.14+
- [Ollama](https://ollama.com/) running locally (default LLM provider), or an OpenAI API key

## CLI Reference

### `init-db`

Creates the SQLite database with all required tables, indexes, and triggers.

```bash
bakflow init-db [--db-path PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--db-path` | `drive_triage.db` | Path to the SQLite database file |

### `import-csv`

Imports a TreeSize CSV export into the database, registering a drive and creating entries for every file and folder in the listing.

```bash
bakflow import-csv <csv_path> --drive-label <label> [options]
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
bakflow run-server [--transport stdio|sse|streamable-http] [--db-path PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--transport` | `stdio` | MCP transport protocol (`stdio`, `sse`, or `streamable-http`) |
| `--db-path` | `drive_triage.db` | Path to the SQLite database file |

See [MCP Server](#mcp-server) below for tool descriptions and client configuration.

### `run-ui`

Launches the Streamlit review UI.

```bash
bakflow run-ui
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

## MCP Server

The MCP server exposes 8 tools that let any MCP-compatible client (Kiro, Claude Desktop, etc.) drive the full triage workflow programmatically.

### Tools

| Tool | Description |
|------|-------------|
| `get_unclassified_batch` | Fetch entries that haven't been classified yet. Supports `batch_size` and `include_failed` to retry failures. |
| `get_folder_summary` | Aggregated summary of a folder's contents — file count, total size, type distribution, and subfolder names. |
| `submit_classification` | Submit AI classification results (file class or folder purpose, confidence, reasoning) for a batch of entries. |
| `classify_batch` | End-to-end: fetches unclassified entries, sends them to the configured LLM, and writes results back to the database. |
| `get_review_queue` | Entries ready for human review, ordered by confidence (lowest first). Filterable by category and confidence range. |
| `record_decision` | Record an include/exclude/defer decision for an entry. Supports classification overrides, cascade to children, and reclassification requests. |
| `get_drive_progress` | Triage progress across all three status dimensions (classification, review, decision). |
| `get_decision_manifest` | Export the decision manifest, filtered by decision status. |

All tools that accept a `drive_id` parameter resolve it as a UUID first, then fall back to volume serial number.

### Client Configuration

To connect an MCP client to the server, point it at the `run-server` CLI command. Here's an example `mcp.json` for Kiro (place in `.kiro/settings/mcp.json`):

```json
{
  "mcpServers": {
    "bakflow": {
      "command": "./.venv/bin/bakflow",
      "args": ["run-server", "--transport", "stdio"],
      "env": {
        "BF_LLM_PROVIDER": "ollama",
        "BF_MODEL": "llama3.2",
        "BF_BASE_URL": "http://localhost:11434",
        "OLLAMA_API_KEY": "your-key-here (only needed for Ollama Cloud)"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

For Claude Desktop, add the equivalent block to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "bakflow": {
      "command": "/path/to/project/.venv/bin/bakflow",
      "args": ["run-server", "--transport", "stdio"],
      "env": {
        "BF_LLM_PROVIDER": "ollama",
        "BF_MODEL": "llama3.2",
        "BF_BASE_URL": "http://localhost:11434",
        "OLLAMA_API_KEY": "your-key-here (only needed for Ollama Cloud)"
      }
    }
  }
}
```

### Environment Variables

The server reads its configuration from environment variables (set via `env` in the MCP config or your shell):

| Variable | Default | Description |
|----------|---------|-------------|
| `BF_DB_PATH` | `drive_triage.db` | Path to the SQLite database |
| `BF_LLM_PROVIDER` | `ollama` | LLM provider (`ollama` or `openai`) |
| `BF_MODEL` | `llama3.2` | Model name to use for classification |
| `BF_BASE_URL` | `http://localhost:11434` | Provider API base URL |
| `BF_API_KEY` | — | API key (required for OpenAI) |
| `OLLAMA_API_KEY` | — | API key for Ollama Cloud (read by the `ollama` SDK; not needed for local Ollama) |
| `BF_CONFIDENCE_THRESHOLD` | `0.7` | Entries below this confidence are flagged for priority review |
| `BF_BATCH_SIZE` | `50` | Default batch size for classification |

### Typical Workflow via MCP

1. `get_drive_progress` — check where things stand
2. `classify_batch` — run LLM classification on unclassified entries
3. `get_review_queue` — pull low-confidence entries for human review
4. `record_decision` — accept or override classifications, record backup decisions
5. `get_decision_manifest` — export the final include list for backup tooling

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

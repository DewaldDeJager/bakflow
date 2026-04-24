# Tech Stack

- **Language**: Python 3.14+
- **Build System**: setuptools via `pyproject.toml`
- **Data Validation**: Pydantic v2 (models as data contracts across all layers)
- **Database**: SQLite with WAL mode, foreign keys, CHECK constraints, and triggers
- **LLM Integration**: Ollama (local, default) and OpenAI (cloud) via `ollama` and `openai` SDKs
- **MCP Server**: `mcp` library (FastMCP) for tool registration
- **UI**: Streamlit (multi-page app)
- **HTTP**: httpx (async)
- **Testing**: pytest + Hypothesis (property-based testing)
- **Async Testing**: pytest-asyncio

## Common Commands

```bash
# Run all tests
pytest

# Run tests for a specific module
pytest src/db/test_status.py

# Run tests with verbose output
pytest -v

# Run only property-based tests (by marker or filename pattern)
pytest -k "test_" src/db/

# CLI entry point (after install)
drive-backup-triage --help
drive-backup-triage init-db
drive-backup-triage import-csv <csv_path> --drive-label <label>
drive-backup-triage run-server
drive-backup-triage run-ui
```

## Key Conventions

- Pydantic models in `src/db/models.py` are the canonical data contract — all layers use them
- SQLite schema lives in `src/db/schema.py` as a single DDL script executed via `init_db()`
- Status transitions are validated and enforced in `src/db/status.py` with audit logging
- Repository pattern in `src/db/repository.py` wraps raw SQL and returns Pydantic models
- LLM providers follow a Protocol-based abstraction for swappable backends
- Property-based tests use `@settings(max_examples=100)` by default
- Test files are co-located with the code they test (e.g., `src/db/test_*.py`)

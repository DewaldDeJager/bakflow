# Tech Stack

- **Language**: Python 3.14+
- **Virtual Environment**: Always use `.venv/bin/python` and `.venv/bin/pytest` (or other `.venv/bin/` executables) when running commands. Never use the global/system Python interpreter.
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
.venv/bin/pytest

# Run tests for a specific module
.venv/bin/pytest src/db/test_status.py

# Run tests with verbose output
.venv/bin/pytest -v

# Run only property-based tests (by marker or filename pattern)
.venv/bin/pytest -k "test_" src/db/

# CLI entry point (after install)
.venv/bin/bakflow --help
.venv/bin/bakflow init-db
.venv/bin/bakflow import-csv <csv_path> --drive-label <label>
.venv/bin/bakflow run-server
.venv/bin/bakflow run-ui
```

## Key Conventions

- Pydantic models in `src/db/models.py` are the canonical data contract — all layers use them
- SQLite schema lives in `src/db/schema.py` as a single DDL script executed via `init_db()`
- Status transitions are validated and enforced in `src/db/status.py` with audit logging
- Repository pattern in `src/db/repository.py` wraps raw SQL and returns Pydantic models
- LLM providers follow a Protocol-based abstraction for swappable backends
- Property-based tests use `@settings(max_examples=100)` by default
- Test files are co-located with the code they test (e.g., `src/db/test_*.py`)

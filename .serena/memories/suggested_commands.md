# Suggested Commands

Always use `.venv/bin/` executables — never the system Python.

## Testing
```bash
# Run all tests
.venv/bin/pytest

# Run tests for a specific module
.venv/bin/pytest src/db/test_status.py

# Run tests with verbose output
.venv/bin/pytest -v

# Run property-based tests by pattern
.venv/bin/pytest -k "test_" src/db/
```

## CLI Entry Points
```bash
.venv/bin/bakflow --help
.venv/bin/bakflow init-db
.venv/bin/bakflow import-csv <csv_path> --drive-label <label>
.venv/bin/bakflow run-server
.venv/bin/bakflow run-ui
```

## System Utilities (macOS/Darwin)
```bash
git status / git diff / git log --oneline
ls -la
find . -name "*.py" -not -path "./.venv/*"
grep -r "pattern" src/
```

## Package Management
```bash
uv pip install -e ".[dev]"   # Install in dev mode
uv sync                       # Sync dependencies from uv.lock
```

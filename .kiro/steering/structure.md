# Project Structure

```
src/
├── __init__.py          # Package root
├── cli.py               # CLI entry points (argparse subcommands)
├── config.py            # AppConfig dataclass (central configuration)
├── db/                  # Data layer
│   ├── models.py        # Pydantic models (Drive, Entry, AuditLogEntry, classifier I/O)
│   ├── schema.py        # SQLite DDL, init_db() — tables, indexes, triggers, constraints
│   ├── status.py        # Status transition validation, cross-dimension guards, audit logging
│   ├── repository.py    # Repository class — CRUD, queries, batch operations
│   └── test_*.py        # Co-located tests (unit + property-based)
├── importer/            # CSV import from TreeSize exports
├── classifier/          # LLM classification (provider protocol, prompts, batch orchestration)
├── mcp_server/          # FastMCP tool definitions (8 tools)
└── ui/                  # Streamlit multi-page app
    ├── pages/           # Page modules (drive management, review queue, progress, export)
    └── components/      # Reusable UI components (entry card, filters, bulk actions)
```

## Architecture

Layered bottom-up: **data → service → presentation**

- `db/` is the foundation — schema, models, repository, status engine
- `importer/` and `classifier/` are service-layer modules that depend on `db/`
- `mcp_server/` exposes service-layer functionality as MCP tools
- `ui/` is the Streamlit presentation layer
- `cli.py` wires everything together as subcommands

## Patterns

- **Repository pattern**: `Repository` class wraps `sqlite3.Connection`, returns Pydantic models
- **Status engine**: Transition validation with a declarative `VALID_TRANSITIONS` map and `CROSS_DIMENSION_GUARDS`
- **Co-located tests**: Test files live next to the modules they test (`src/db/test_status.py` tests `src/db/status.py`)
- **Property-based testing**: Hypothesis strategies generate inputs; tests verify invariants rather than specific examples
- **Protocol-based abstraction**: LLM providers implement a shared Protocol for swappable backends

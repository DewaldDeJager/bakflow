# Code Style & Conventions

## Data Models
- Pydantic v2 models in `src/db/models.py` are the canonical data contract — all layers use them
- Models: Drive, Entry, AuditLogEntry, ImportLogEntry, FileClassification, FolderClassification, FileSummary, FolderSummary
- Status enums: ClassificationStatus, ReviewStatus, DecisionStatus

## Patterns
- **Repository pattern**: `Repository` class wraps `sqlite3.Connection`, returns Pydantic models
- **Status engine**: Declarative `VALID_TRANSITIONS` map + `CROSS_DIMENSION_GUARDS` in `src/db/status.py`
- **Protocol-based abstraction**: LLM providers implement a shared Protocol for swappable backends
- **Co-located tests**: Test files live next to the modules they test (e.g., `src/db/test_status.py`)

## Testing Conventions
- Property-based tests use Hypothesis with `@settings(max_examples=100)` by default
- Hypothesis profiles configured in `conftest.py`: "default" (100 examples, 500ms deadline) and "ci" (200 examples, no deadline)
- Tests verify invariants rather than specific examples
- pytest-asyncio for async tests

## Naming & Style
- Python standard: snake_case for functions/variables, PascalCase for classes
- Type hints used throughout
- SQLite schema in `src/db/schema.py` as a single DDL script via `init_db()`

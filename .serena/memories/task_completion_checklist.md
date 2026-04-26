# Task Completion Checklist

After completing a coding task:

1. **Run tests**: `.venv/bin/pytest` (or targeted: `.venv/bin/pytest src/db/test_status.py`)
2. **Check for regressions**: Ensure existing tests still pass
3. **Verify type consistency**: Pydantic models are the canonical contract — ensure any new code uses them
4. **Status transitions**: If modifying status-related code, verify against `VALID_TRANSITIONS` and `CROSS_DIMENSION_GUARDS`
5. **Audit logging**: Status changes must be recorded in the audit log

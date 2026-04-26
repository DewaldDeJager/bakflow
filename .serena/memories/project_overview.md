# bakflow — Project Overview

**Purpose**: AI-assisted tool for classifying and triaging files on hard drive backups. Users import TreeSize CSV exports, an LLM classifies entries by purpose/importance, humans review via Streamlit UI, and final decisions are exported as manifests.

**Tech Stack**:
- Python 3.14+, setuptools via pyproject.toml
- Pydantic v2 for data models/contracts
- SQLite (WAL mode, foreign keys, CHECK constraints, triggers)
- LLM: Ollama (local, default) + OpenAI (cloud) via respective SDKs
- MCP Server: FastMCP (`mcp` library)
- UI: Streamlit (multi-page app)
- HTTP: httpx (async)
- Testing: pytest + Hypothesis (property-based), pytest-asyncio

**Architecture**: Layered bottom-up: data → service → presentation
- `src/db/` — foundation: schema, models, repository, status engine
- `src/importer/` — CSV import from TreeSize exports
- `src/classifier/` — LLM classification (provider protocol, prompts, batch orchestration)
- `src/mcp_server/` — FastMCP tool definitions
- `src/ui/` — Streamlit multi-page app (pages + components)
- `src/cli.py` — CLI entry points wiring everything together
- `src/config.py` — AppConfig dataclass (central configuration)
- `src/export.py` — Decision manifest export

**Key Concepts**:
- Drive: registered hard drive (UUID, optional volume serial/label)
- Entry: file/folder record with three status dimensions (classification, review, decision)
- Three-Dimension Status Model with enforced valid transitions and cross-dimension guards
- Audit log for every status transition
- Confidence threshold for flagging entries for priority review

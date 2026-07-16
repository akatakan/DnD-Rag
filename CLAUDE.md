# CLAUDE.md

## Project Overview

Source-aware D&D rules assistant using Streamlit, LlamaIndex, Qdrant, Ollama, and
optional Gemini. The current index contains separate Player and DM collections
with dense and BM25 sparse vectors.

## Commands

```bash
uv sync
docker compose up -d
uv run python ingestion.py
uv run streamlit run main.py
uv run python run_api.py
cd web && npm run build && npm run test:e2e
uv run python -m unittest discover -s tests -v
uv run python evaluate.py --mode router
uv run python evaluate.py --mode retrieval --retrieval dense
uv run python evaluate.py --mode retrieval --retrieval hybrid
```

## Architecture

- `config.py`: environment configuration, retrieval defaults, index version, prompts.
- `ingestion.py`: PDF parsing, metadata, dense/BM25 vectors, signature-based rebuilds.
- `retriever.py`: book catalog, Qdrant metadata filters, dense/hybrid retrievers, rerank.
- `router.py`: deterministic two-book routing with LLM fallback for other catalogs.
- `agent.py`: provider setup, balanced multi-book retrieval, single response synthesis, operational error boundary.
- `sources.py`: deduplicated source book/page extraction.
- `evaluate.py`: router exact/precision/recall and retrieval hit/MRR metrics.
- `main.py`: Streamlit chat, character, encounter, and session views.
- `dice.py`: bounded cryptographic dice parser and `/roll` command.
- `game_state.py`: character/HP/inventory models and encounter state machine.
- `session_store.py`: persistent SQLite sessions, messages, notes, and game state.
- `api/app.py`: FastAPI REST/WebSocket surface and role-redacted snapshots.
- `api/game_engine.py`: authoritative validated multiplayer commands.
- `api/store.py`: multiplayer state, membership, requests, and visibility events.
- `api/ai_dm.py`: structured assisted/AI DM plans; game engine applies commands.
- `web/`: separate React Player and DM workspaces.

## Retrieval Contract

Every node must preserve these Qdrant payload fields:

- `source_book` and `source_book_id`
- `source_file` and 1-based `page_number`
- `pdf_sha256`, `index_version`, and `index_signature`

Collection names are `dnd_<pdf-stem>`. Index signature includes index version,
dense model, sparse model, and PDF SHA-256. Any change triggers a collection rebuild.

Dense is the production default. Hybrid BM25 and Ollama `LLMRerank` are available
through configuration/UI but remain experimental based on committed evaluation
reports. Do not change defaults without rerunning the 40-question set.

## Current Scope

Stages 1, 2, and 3 are complete. Session data lives in ignored `runtime/sessions.db`.
Rule mode uses one sourced synthesis. Story mode performs that rule synthesis first,
then a second creative pass that must not alter mechanics or source references.


## Multiplayer Invariants

- Clients request commands; only GameEngine mutates shared state.
- Player HP requests require DM approval; dice may auto-commit.
- Snapshot/event visibility must be filtered server-side, never only in React.
- Human, assisted, and AI DM modes are mutually exclusive per game.
- AI produces structured plans; deterministic code rolls and mutates state.

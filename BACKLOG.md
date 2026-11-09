# Backlog

> **Principle:** biggest risk first, then inside-out. Each story is testable on its own.

Status: ✅ done

## Stories

| # | Story | Folder | Result | Status |
|---|-------|--------|--------|--------|
| B01 | **README:** business requirements, system design, folder structure, how to run | `/`, `00_assets` | The README explains the whole system | ✅ |
| B03 | **Database:** pgvector in Docker Compose, ledger schema, seed (company 100, customer 1,000) | `01_database` | Balances are calculated from the ledger (`balances` view) | ✅ |
| B04 | **MCP server (stdio):** `create_refund` writes one ledger row company → customer, `get_account` reads balance and refunds | `02_mcp_server` | Golden question 3 ends in "Refund 60"; the new balance comes from the ledger | ✅ |
| B05 | **RAG:** chunk both PDFs, embed with all-MiniLM-L6-v2, store `region` per chunk; HyDE retrieval filtered by region | `03_rag` | `ingest.py` runs once, `retrieve.py` on every question; 58 General + 67 Arizona chunks | ✅ |
| B06 | **LangGraph agent:** route region → retrieve → answer → refund / account | `04_agent` | All 3 golden questions answered; account questions read the ledger; unknown questions → "Not covered" | ✅ |
| B07 | **Guardrails:** Pydantic schema + business rules, one retry, safe fallback | `04_agent` | Refunds over 100, invented page citations and empty answers are blocked | ✅ |
| B08 | **FastAPI:** `POST /chat` returns the full `AgentOutput` (answer, pages, tokens, shadow cost) | `05_api` | One endpoint, testable at `localhost:8000/docs` | ✅ |
| B09 | **Streamlit frontend:** chat, golden questions (click → chat), route line + tokens/cost under each answer | `06_frontend` | Demo runs at `localhost:8501` | ✅ |
| B10 | **Evaluation:** live view of input tokens, output tokens, cost; expected answer shown under the agent's answer | `06_frontend` | Correctness judged side by side in the demo | ✅ |
| B11 | **Docker:** one Dockerfile, Compose with `db`, `ingest`, `api`, `frontend` (+ `agent` CLI on demand) | `/` | `docker compose up -d --build` starts everything in the right order | ✅ |
| B12 | **GitHub Actions CI:** `ruff` + `pytest` on every push and pull request | `.github/workflows`, `04_agent` | Lint green, 9 guardrail tests pass | ✅ |

## Guardrail rules

| Layer | Rule | Implementation |
|-------|------|----------------|
| 1 Schema | Valid fields, allowed actions (`answer`, `refund`, `account`, `cannot_help`), no empty answer | Pydantic `AgentLLMOutput` |
| 1 Schema | Refund ≤ 100, a refund needs an amount | `Field(le=100)` + model validator |
| 2 Business | Refund ≤ the amount the customer asked for | `check_business_rules()` |
| 2 Business | Policy answers cite pages, and only pages that were retrieved | `check_business_rules()` (grounding) |
| 3 Tool | Amount between 0 and 100, the owner has enough money | `create_refund()` in the MCP server |
| – | Failure → one retry with the error message → safe fallback | `answer` node in `agent.py` |

## Decisions

| Topic | Decision | Why |
|-------|----------|-----|
| Model | `gpt-oss:120b-cloud` via the Ollama app on the laptop | 120b needs an 80 GB GPU locally; cloud runs it with zero local VRAM |
| Ollama | Native app, not in Docker | Holds the sign-in; GPU in Docker possible later for local models |
| Cost metric | Shadow cost: tokens × market price (~$0.037 in / $0.17 out per 1M tokens) | Cloud/free inference would always show 0 |
| Agent framework | LangGraph, one class `CustomerServiceAgent` | The agent is a state machine with branches |
| Region routing | Scan the question for regions found in the database, otherwise General | 0 tokens, deterministic; a new PDF = a new region |
| Retrieval | HyDE: the LLM writes a fake policy passage, search with its vector | Customer language ≠ contract language |
| Embeddings | all-MiniLM-L6-v2 (384 dims), own `chunks` table | Small, runs on CPU, no extra library |
| Customer | Always Alex Miller | MVP scope |
| Money actions | The LLM only proposes; guardrails validate; the graph calls MCP | The LLM never touches money directly |
| Numbers about money | Balances in answers come from the ledger, written by the graph | The LLM can't know them, so it must not guess them |
| Guardrails | Pydantic + business rules, schema also in the prompt | gpt-oss ignores the API-level schema |
| Human-in-the-loop | Out of scope | Keeps the MVP small |
| Evaluation | Live: tokens, cost, expected answer side by side | Enough for the demo |
| Database | Postgres + pgvector | One container for ledger and vector store |
| Currency | Amounts compared as-is, no FX conversion | MVP simplicity |
| Dependencies | `pyproject.toml` with dependency groups (`app`, `dev`), exact pins | One file for packages + tool config |
| Docker | One Dockerfile, one image, different `command` per service; MCP via stdio inside `api` | Less structure |
| API | One endpoint `POST /chat`, plain `def` | Minimal; `asyncio.run()` for MCP needs a sync endpoint |
| Frontend | Streamlit, API connection in `api_client.py`, styling in `style.css` | Pure Python; React would be the production path |
| Kubernetes | No | Single machine, no load; Compose → Helm is the production path |
| CI/CD | CI only: lint + guardrail tests, no LLM in CI | Deterministic checks in CI; LLM quality via the golden questions |

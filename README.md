# CiteBot

CiteBot is a production-oriented research assistant scaffold for agentic retrieval-augmented generation workflows.

## Quick Start

Run the API and local dependencies with one command:

```bash
make dev-up
```

The stack starts:

- FastAPI on `http://localhost:8000`
- PostgreSQL with pgvector on `localhost:5432`
- Qdrant on `localhost:6333`
- Redis on `localhost:6379`

## Local Python Workflow

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
uvicorn app.main:app --reload
```

Set `EMBEDDING_PROVIDER` in `.env` to `mock`, `openai`, or `gemini`.
Use `OPENAI_API_KEY` with `EMBEDDING_MODEL` for OpenAI, or `GEMINI_API_KEY`
with `GEMINI_EMBEDDING_MODEL` for Gemini.

## Ingest a Sample Corpus

```bash
python -m app.ingestion.cli ingest data/sample_corpus
python -m app.ingestion.cli search "citation traceability" --top-k 3
```

## API Endpoints

- `GET /api/v1/health`
- `GET /api/v1/ready`
- `GET /api/v1/version`
- `POST /api/v1/admin/ingestion/jobs`
- `GET /api/v1/admin/ingestion/jobs/{job_id}`
- `POST /api/v1/admin/ingestion/search`
- `GET /api/v1/admin/ingestion/metrics`

## Development Commands

```bash
make test
make lint
make dev-down
```


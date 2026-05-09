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
python -m app.ingestion.cli search "citation traceability" --top-k 3 --strategy hybrid --include-explain
```

Search supports:

- `--strategy sparse|dense|hybrid`
- `--index-target auto|pgvector|qdrant|local`
- `--document-id`, `--source-uri`, and `--access-policy` filters
- `--embedding-version` and `--index-version` filters
- `--disable-reranking` to inspect fused rankings without the reranker

## API Endpoints

- `GET /api/v1/health`
- `GET /api/v1/ready`
- `GET /api/v1/version`
- `POST /api/v1/admin/ingestion/jobs`
- `GET /api/v1/admin/ingestion/jobs/{job_id}`
- `POST /api/v1/admin/ingestion/search`
- `GET /api/v1/admin/ingestion/metrics`

The admin search endpoint accepts dense, sparse, and hybrid retrieval requests and can return per-result explain payloads showing backend choice, fallback decisions, fusion metadata, and reranker scores.

## Development Commands

```bash
make test
make lint
make integration-retrieval
make benchmark-retrieval
make dev-down
```

## Real Backend Benchmarking

Use the retrieval harness to compare live `pgvector` and `qdrant` responses through the API.

```bash
make integration-retrieval
make benchmark-retrieval
```

The harness will:

- ensure the Docker Compose stack is up,
- wait for `/api/v1/ready`,
- ingest the sample corpus into PostgreSQL, pgvector, Qdrant, and the sparse index,
- run dense retrieval requests against `pgvector` and `qdrant`,
- write JSON reports under `artifacts/retrieval-benchmarks/`.

You can also run it directly:

```bash
python -m app.evaluation.retrieval_harness integration --start-compose
python -m app.evaluation.retrieval_harness benchmark --start-compose --iterations 10
```


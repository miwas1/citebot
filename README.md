# CiteBot

CiteBot is a privacy-first document RAG system for local, offline operation. The default deployment keeps documents, OCR, embeddings, generation, vectors, sessions, and evaluation on one machine; hosted providers and web search are compatibility-only development paths and are rejected by offline production mode.

---

## Table of Contents

- [Local Setup](#local-setup)
- [Quick Start (Docker)](#quick-start-docker)
- [Corpus Download](#corpus-download)
- [Ingest a Corpus](#ingest-a-corpus)
- [API Endpoints](#api-endpoints)
- [Research API](#research-api)
- [Evaluation Workflow](#evaluation-workflow)
- [Development Commands](#development-commands)
- [Real Backend Benchmarking](#real-backend-benchmarking)
- [Contributing](#contributing)

---

## Local Setup

### Prerequisites

- Python 3.11+
- Docker + Docker Compose (for the full stack)
- Git

### 1. Clone and install

```bash
git clone https://github.com/miwas1/citebot.git
cd citebot
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Install the optional evaluation extras if you plan to run RAGAS scoring:

```bash
pip install -e .[dev,evaluation]
```

### 2. Configure environment

```bash
cp .env.example .env
```

The example file is configured for an offline local run. Copy it before starting:

| Variable | Description | Default |
|---|---|---|
| `RUNTIME_MODE` | `offline` fail-closed runtime policy | `offline` |
| `EMBEDDING_PROVIDER` | `local-http` (real local service) or `local`/`test` (deterministic tests) | `local-http` |
| `EMBEDDING_MODEL` | Local embedding artifact | `Qwen/Qwen3-Embedding-0.6B` |
| `EMBEDDING_BASE_URL` | Private embedding service URL | `http://embedding:8081` |
| `EMBEDDING_DIMENSION` | Must match the local model artifact | `1024` |
| `ANSWER_PROVIDER` | `llama-cpp` or `local`/`test` (deterministic tests) | `llama-cpp` |
| `ANSWER_MODEL` | Local quantized model artifact | `phi-4-mini-instruct-q4` |
| `LLM_BASE_URL` | Private llama.cpp-compatible URL | `http://llm:8082/v1` |
| `OCR_PROVIDER` | `paddleocr` or `none` | `paddleocr` |
| `OCR_FALLBACK_PROVIDER` | `tesseract` or `none` | `tesseract` |
| `EVALUATION_EVALUATOR_PROVIDER` | `local` by default | `local` |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` | Compatibility-only credentials; not valid in offline production | — |
| `TAVILY_API_KEY` | Compatibility-only web search credential; disabled offline | — |
| `RESEARCH_API_KEY` | Optional in development; protects research routes when set | — |
| `ADMIN_API_KEY` | Optional in development; protects admin routes when set | — |
| `S2_API_KEY` | Optional – raises Semantic Scholar rate limits | — |

### 3. Provision model artifacts and start the offline stack

Run the one-time bootstrap command below. It downloads the default Qwen embedding
model, Phi-4-mini GGUF, and PaddleOCR detection/recognition models to the local
artifact directory, records their exact upstream commits and checksums in
`manifest.lock.json`, builds/pulls the required containers, and starts the stack.
Set the `*_REPOSITORY`, `*_REVISION`, `LLM_MODEL_FILENAME`, or
`MODEL_ARTIFACT_ROOT` values in `.env` before running it to override a default.
The runtime never downloads models after this command succeeds.

```bash
make local-setup
```

Services started:

- FastAPI on `http://127.0.0.1:8000`
- local embedding service on the private Compose network
- local llama.cpp server on the private Compose network
- Qdrant on the private Compose network
- SQLite, raw/structured documents, and indexes under `./storage`

Qdrant, model services, and worker ports are not published to the host. Dozzle is opt-in with `docker compose --profile observability up` and remains loopback-bound.

### 4. Run without Docker (SQLite / local vector index)

```bash
uvicorn app.main:app --reload
```

For fast tests without model services, set `EMBEDDING_PROVIDER=local`, `ANSWER_PROVIDER=local`, `ENABLE_QDRANT=false`, and `INGESTION_EXECUTION_MODE=foreground`. These deterministic providers are not production inference.

---

## Quick Start (Docker)

```bash
make dev-up          # start all services
make ingest-sample   # ingest the bundled sample corpus
make search-sample   # run a test search
make test            # run the test suite
make dev-logs        # follow API and document-worker logs
make dev-down        # stop containers and preserve volumes
make dev-reset       # stop containers and delete volumes
```

---

## Corpus Download

The `scripts/download_corpus.py` script fetches research papers from three free public APIs and writes them to JSONL files that can be ingested directly by CiteBot.

### Sources

| Source | API | Auth needed | Scale |
|---|---|---|---|
| **arXiv** | Atom/XML | None | up to ~2M papers |
| **Semantic Scholar** | REST JSON | Optional (`S2_API_KEY`) | up to ~10k/query |
| **OpenAlex** | REST JSON | None | up to 500k+ |

### Output format

Each JSONL line is a `LoadedDocument`-compatible JSON object:

```json
{
  "source_uri":    "https://arxiv.org/abs/2304.01234",
  "title":         "Attention is Not Explanation",
  "text":          "<abstract text>",
  "publisher":     "arXiv",
  "published_at":  "2023-04-01T00:00:00+00:00",
  "access_policy": "public",
  "metadata": {
    "authors":        ["Jane Smith", "John Doe"],
    "doi":            "10.48550/arXiv.2304.01234",
    "citation_count": 150,
    "categories":     ["cs.LG", "cs.CL"],
    "source":         "arxiv"
  }
}
```

### Usage

```bash
# Single source – 2 000 papers from arXiv after 2022
python scripts/download_corpus.py arxiv \
    --query "transformer interpretability mechanistic attention" \
    --max-papers 2000 \
    --after-date 2022-01-01 \
    --output-dir data/corpus/interpretability

# All three sources at once
python scripts/download_corpus.py all \
    --query "transformer interpretability mechanistic attention" \
    --max-papers 5000 \
    --after-date 2022-01-01 \
    --output-dir data/corpus/interpretability

# Large-scale OpenAlex download (up to 500 000 papers)
python scripts/download_corpus.py openalex \
    --query "transformer interpretability" \
    --max-papers 500000 \
    --after-date 2022-01-01 \
    --output-dir data/corpus/interpretability \
    --contact-email your@email.com
```

**All flags:**

| Flag | Default | Description |
|---|---|---|
| `source` | — | `arxiv`, `semantic-scholar`, `openalex`, or `all` |
| `--query` | `transformer interpretability …` | Search query |
| `--max-papers` | `5000` | Papers per source |
| `--after-date` | none | ISO date lower bound (YYYY-MM-DD) |
| `--output-dir` | `data/corpus` | Output directory |
| `--contact-email` | `research@citebot.local` | Used in OpenAlex `User-Agent` header for polite-pool access |

**Environment variables:**

| Variable | Effect |
|---|---|
| `S2_API_KEY` | Raises Semantic Scholar rate limit to ~10 req/s |

### Make targets

```bash
# Download 2k papers per source (default, ~6k total)
make corpus-download

# Download 10k papers per source (~30k total)
make corpus-download-large

# Download up to 500k papers from OpenAlex only
make corpus-download-full

# Download + merge + deduplicate (no ingestion)
make corpus-seed

# Download + merge + ingest in one step
make corpus-seed-ingest

# Show file sizes and line counts for downloaded files
make corpus-stats
```

Override defaults inline:

```bash
make corpus-download CORPUS_MAX_PAPERS=20000 CORPUS_AFTER_DATE=2023-01-01
```

### Interpretability scenario (end-to-end)

The bundled scenario tests the full pipeline against the query:
> *"Compare transformer interpretability techniques published after 2022 and summarize limitations."*

```bash
# 1. Download and ingest corpus
make corpus-seed-ingest

# 2. Run evaluation (retrieval probes + agent answer + citation metrics)
make eval-interpretability

# 3. Run with RAGAS faithfulness scoring (requires OPENAI_API_KEY)
make eval-interpretability-ragas

# 4. Run the scenario script directly with custom options
python scripts/run_interpretability_scenario.py \
    --corpus data/corpus/interpretability/interpretability_merged.jsonl \
    --top-k 15 \
    --ragas \
    --output artifacts/evaluations/interp_result.json
```

Metrics reported:

| Metric | Description |
|---|---|
| `recall@k` | Gold paper recall (5 known interpretability papers) |
| `avg_keyword_hit_rate` | Expected keywords found across 5 retrieval probes |
| `avg_temporal_compliance` | % of returned chunks dated ≥ 2022 |
| `agent_trait_score` | % of expected answer traits satisfied |
| `ragas_faithfulness` | RAGAS grounded-in-context score (optional) |
| `ragas_answer_relevancy` | RAGAS question-answer alignment (optional) |

---

## Ingest a Corpus

Ingest the bundled sample corpus:

```bash
python -m app.ingestion.cli ingest data/sample_corpus
```

Ingest a downloaded research corpus:

```bash
python -m app.ingestion.cli ingest data/corpus/interpretability/interpretability_merged.jsonl
# or via Make:
make ingest-interpretability
```

Search after ingestion:

```bash
python -m app.ingestion.cli search "citation traceability" --top-k 3 --strategy hybrid --include-explain
```

Search flags:

- `--strategy sparse|dense|hybrid`
- `--index-target auto|pgvector|qdrant|local`
- `--document-id`, `--source-uri`, and `--access-policy` filters
- `--embedding-version` and `--index-version` filters
- `--disable-reranking` to inspect fused rankings without the reranker

---

## API Endpoints

- `GET /api/v1/health`
- `GET /api/v1/ready`
- `GET /api/v1/version`
- `POST /api/v1/admin/ingestion/jobs`
- `GET /api/v1/admin/ingestion/jobs/{job_id}`
- `POST /api/v1/admin/ingestion/search`
- `GET /api/v1/admin/ingestion/metrics`
- `POST /api/v1/admin/evaluation/runs`
- `GET /api/v1/admin/evaluation/runs/{run_id}`

The admin search endpoint accepts dense, sparse, and hybrid retrieval requests and can return per-result explain payloads showing backend choice, fallback decisions, fusion metadata, and reranker scores.

---

## Research API

The repository includes a LangGraph-backed research workflow for grounded local answer generation and citation verification. Web enrichment remains disabled in offline mode; the Python sandbox is separately opt-in.

- `POST /api/v1/research/query`

Example request:

```json
{
	"session_id": "session-1",
	"query": "How does citation traceability work in CiteBot?",
	"top_k": 3,
	"allow_web_search": false,
	"allow_python_execution": false
}
```

The response includes:

- a structured answer with citations,
- citation verification verdicts,
- compressed memory for follow-up turns,
- tool audit records,
- approximate token accounting by graph stage,
- a `trace_id` and explicit state transitions for replay/debugging.

Relevant configuration flags:

- `ANSWER_PROVIDER=llama-cpp|local|test`
- `ANSWER_MODEL` and `LLM_BASE_URL`
- `RUNTIME_MODE=offline`
- `ALLOW_WEB_SEARCH_DEFAULT`
- `ALLOW_PYTHON_EXECUTION_DEFAULT`
- `TAVILY_API_KEY`
- `RESEARCH_MIN_CONTEXT_SCORE`
- `PYTHON_SANDBOX_TIMEOUT_SECONDS`
- `PYTHON_SANDBOX_MEMORY_MB`

---

## Evaluation Workflow

CiteBot includes a versioned evaluation runner that executes the real research pipeline against graded datasets, persists JSON artifacts under `artifacts/evaluations/`, and applies CI-style thresholds.

### Sample corpus evaluation

```bash
make eval-smoke                                         # quick smoke test
make eval-ci                                            # CI quality gate (ragas_ci marker)
python -m app.evaluation.cli run --source-path data/sample_corpus
python -m app.evaluation.cli show <run_id>
```

### Interpretability scenario evaluation

The dataset at `data/evaluation_datasets/interpretability_scenario.json` contains 6 graded cases covering the query:
> *"Compare transformer interpretability techniques published after 2022 and summarize limitations."*

```bash
# Run against the already-ingested index
make eval-interpretability

# Run with RAGAS faithfulness + answer relevancy scoring
make eval-interpretability-ragas                        # requires OPENAI_API_KEY

# Run with web search tool enabled
make eval-interpretability-web

# Run through the CiteBot evaluation service (full artifact + thresholds)
make eval-dataset-interpretability
```

Install `citebot[evaluation]` to enable RAGAS scoring:

```bash
pip install -e .[evaluation]
```

---

## Development Commands

```bash
make test                       # run pytest suite
make lint                       # ruff check
make dev-down                   # stop Docker services
make dev-logs                   # follow Docker logs
make dev-reset                  # remove Docker services and volumes

make corpus-download            # download ~6k interpretability papers
make corpus-seed-ingest         # download + merge + ingest in one step
make ingest-interpretability    # ingest already-downloaded merged corpus
make corpus-stats               # show JSONL file sizes and line counts

make eval-smoke                 # smoke evaluation against sample corpus
make eval-ci                    # CI quality gate
make eval-interpretability      # interpretability scenario metrics
make eval-interpretability-ragas  # + RAGAS faithfulness scoring

make integration-retrieval      # live backend integration test
make benchmark-retrieval        # retrieval latency benchmark
```

---

## Real Backend Benchmarking

Use the retrieval harness to benchmark the default local Qdrant path through the API. The pgvector comparison remains an optional compatibility profile.

```bash
make integration-retrieval
make benchmark-retrieval
```

The harness will:

- ensure the Docker Compose stack is up,
- wait for `/api/v1/ready`,
- ingest the sample corpus into Qdrant and the sparse index,
- run dense retrieval requests against the local Qdrant path,
- write JSON reports under `artifacts/retrieval-benchmarks/`.

You can also run it directly:

```bash
python -m app.evaluation.retrieval_harness integration --start-compose
python -m app.evaluation.retrieval_harness benchmark --start-compose --iterations 10
```

---

## Project Structure

```
app/
  agents/       LangGraph research agent (generation, compression, prompts)
  api/          FastAPI routes
  core/         Config, DI container, lifecycle, security
  db/           SQLAlchemy models and session management
  evaluation/   Evaluation runner, metrics, RAGAS integration
  ingestion/    Loaders, chunker, embedder, sparse index, vector writers
  observability/ Prometheus metrics, middleware
  retrieval/    Hybrid retrieval service, reranker
  tools/        Citation verifier, web search, Python sandbox
scripts/
  download_corpus.py              Multi-source corpus downloader
  seed_interpretability_corpus.sh Download + merge shell orchestrator
  run_interpretability_scenario.py End-to-end scenario evaluator
data/
  sample_corpus/                  Bundled sample documents
  evaluation_datasets/            Versioned evaluation datasets
  corpus/                         Downloaded corpus files (gitignored)
artifacts/
  evaluations/                    Persisted evaluation run JSON artifacts
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow. Run `make test` and `make lint` before opening a pull request. Security issues should be reported privately using the process in [SECURITY.md](SECURITY.md).

CiteBot source code is released under the MIT License; see [LICENSE](LICENSE). Bundled third-party corpus content has separate provenance and licensing considerations described in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

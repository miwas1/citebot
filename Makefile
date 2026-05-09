PYTHON ?= python3

.PHONY: dev-up dev-down test lint ingest-sample search-sample benchmark-retrieval integration-retrieval

dev-up:
	docker compose up --build

dev-down:
	docker compose down -v

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

ingest-sample:
	$(PYTHON) -m app.ingestion.cli ingest data/sample_corpus

search-sample:
	$(PYTHON) -m app.ingestion.cli search "citation traceability" --top-k 3

benchmark-retrieval:
	$(PYTHON) -m app.evaluation.retrieval_harness benchmark --start-compose

integration-retrieval:
	$(PYTHON) -m app.evaluation.retrieval_harness integration --start-compose

PYTHON ?= python3

.PHONY: dev-up dev-down test lint ingest-sample search-sample

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

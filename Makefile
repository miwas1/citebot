PYTHON ?= python3

# Corpus download configuration (override via env or make args)
CORPUS_MAX_PAPERS  ?= 2000
CORPUS_AFTER_DATE  ?= 2022-01-01
CORPUS_OUTPUT_DIR  ?= data/corpus/interpretability
CORPUS_MERGED      ?= $(CORPUS_OUTPUT_DIR)/interpretability_merged.jsonl
EVAL_DATASET       ?= data/evaluation_datasets/interpretability_scenario.json
EVAL_OUTPUT        ?= artifacts/evaluations/interpretability_scenario_result.json

.PHONY: dev-up dev-down test lint ingest-sample search-sample benchmark-retrieval integration-retrieval eval-smoke eval-ci \
        corpus-download corpus-download-large corpus-download-full \
        ingest-interpretability eval-interpretability eval-interpretability-ragas \
        corpus-stats

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

eval-smoke:
	$(PYTHON) -m app.evaluation.cli run --source-path data/sample_corpus

eval-ci:
	$(PYTHON) -m pytest -m ragas_ci

# ---------------------------------------------------------------------------
# Interpretability corpus workflow
# ---------------------------------------------------------------------------

## corpus-download: Download ~2k papers per source (arXiv + S2 + OpenAlex)
corpus-download:
	$(PYTHON) scripts/download_corpus.py all \
		--query "transformer interpretability mechanistic attention visualization probing circuits" \
		--max-papers $(CORPUS_MAX_PAPERS) \
		--after-date $(CORPUS_AFTER_DATE) \
		--output-dir $(CORPUS_OUTPUT_DIR)

## corpus-download-large: Download ~10k papers per source (targeted for realistic eval)
corpus-download-large:
	$(MAKE) corpus-download CORPUS_MAX_PAPERS=10000

## corpus-download-full: Download up to 500k papers from OpenAlex only (large-scale stress test)
corpus-download-full:
	$(PYTHON) scripts/download_corpus.py openalex \
		--query "transformer interpretability mechanistic attention probing circuits sparse autoencoder" \
		--max-papers 500000 \
		--after-date $(CORPUS_AFTER_DATE) \
		--output-dir $(CORPUS_OUTPUT_DIR)

## corpus-seed: Run the full seed script (download + merge, no ingestion)
corpus-seed:
	bash scripts/seed_interpretability_corpus.sh

## corpus-seed-ingest: Run seed script and immediately ingest into CiteBot
corpus-seed-ingest:
	INGEST=1 bash scripts/seed_interpretability_corpus.sh

## ingest-interpretability: Ingest the merged interpretability corpus
ingest-interpretability:
	$(PYTHON) -m app.ingestion.cli ingest $(CORPUS_MERGED)

## corpus-stats: Show line counts and file sizes for downloaded corpus files
corpus-stats:
	@echo "=== Corpus file stats ==="
	@for f in $(CORPUS_OUTPUT_DIR)/*.jsonl; do \
		[ -f "$$f" ] && printf "  %-55s %8s lines  %s\n" "$$f" "$$(wc -l < $$f)" "$$(du -sh $$f | cut -f1)" || true; \
	done

## eval-interpretability: Run interpretability scenario against current index (no RAGAS)
eval-interpretability:
	$(PYTHON) scripts/run_interpretability_scenario.py \
		--top-k 10 \
		--output $(EVAL_OUTPUT)

## eval-interpretability-ragas: Run interpretability scenario with RAGAS scoring
eval-interpretability-ragas:
	$(PYTHON) scripts/run_interpretability_scenario.py \
		--top-k 10 \
		--ragas \
		--output $(EVAL_OUTPUT)

## eval-interpretability-web: Run scenario with web search enabled
eval-interpretability-web:
	$(PYTHON) scripts/run_interpretability_scenario.py \
		--top-k 10 \
		--web-search \
		--output $(EVAL_OUTPUT)

## eval-dataset-interpretability: Run the named evaluation dataset through the CiteBot eval service
eval-dataset-interpretability:
	$(PYTHON) -m app.evaluation.cli run \
		--dataset-path $(EVAL_DATASET) \
		--source-path $(CORPUS_MERGED)

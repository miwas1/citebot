#!/usr/bin/env bash
# scripts/seed_interpretability_corpus.sh
#
# End-to-end seed script for the "transformer interpretability" test scenario.
#
# What this does
# --------------
# 1. Downloads targeted papers from arXiv, Semantic Scholar, and OpenAlex
#    (focused on transformer interpretability, post-2022).
# 2. Merges all JSONL files into a single file CiteBot can ingest.
# 3. Optionally ingests the corpus into a running CiteBot instance.
# 4. Prints summary stats (file sizes, paper counts).
#
# Usage
# -----
#   # Download only (no ingestion)
#   bash scripts/seed_interpretability_corpus.sh
#
#   # Download + ingest into local CiteBot dev instance
#   INGEST=1 bash scripts/seed_interpretability_corpus.sh
#
#   # Larger corpus (5 000 per source ≈ up to 15 000 papers)
#   MAX_PAPERS=5000 bash scripts/seed_interpretability_corpus.sh
#
#   # Full 500k scale test (OpenAlex only, takes ~2 hrs)
#   SOURCES=openalex MAX_PAPERS=500000 bash scripts/seed_interpretability_corpus.sh
#
# Environment variables
# ---------------------
#   SOURCES       Comma-separated list: arxiv,semantic-scholar,openalex  (default: all)
#   MAX_PAPERS    Max papers per source                                   (default: 2000)
#   AFTER_DATE    ISO date lower bound                                    (default: 2022-01-01)
#   OUTPUT_DIR    Directory for JSONL files                               (default: data/corpus/interpretability)
#   INGEST        Set to 1 to auto-ingest after download                 (default: 0)
#   S2_API_KEY    Semantic Scholar API key (optional, increases rate limit)
#   CONTACT_EMAIL Email for OpenAlex polite-pool User-Agent              (default: research@citebot.local)

set -euo pipefail

# ---------------------------------------------------------------------------
# Config (overridable via env)
# ---------------------------------------------------------------------------

SOURCES="${SOURCES:-all}"
MAX_PAPERS="${MAX_PAPERS:-2000}"
AFTER_DATE="${AFTER_DATE:-2022-01-01}"
OUTPUT_DIR="${OUTPUT_DIR:-data/corpus/interpretability}"
INGEST="${INGEST:-0}"
CONTACT_EMAIL="${CONTACT_EMAIL:-research@citebot.local}"

PYTHON="${PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOWNLOADER="$SCRIPT_DIR/download_corpus.py"

# Interpretability-specific queries (multi-term for broader recall)
QUERY="transformer interpretability mechanistic attention visualization probing circuits"

# Merged output file (ingested as one corpus)
MERGED_FILE="$OUTPUT_DIR/interpretability_merged.jsonl"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }
count_lines() { [[ -f "$1" ]] && wc -l < "$1" || echo 0; }
human_size()  { du -sh "$1" 2>/dev/null | cut -f1 || echo "n/a"; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

log "=== CiteBot Interpretability Corpus Seed ==="
log "Sources:     $SOURCES"
log "Max papers:  $MAX_PAPERS per source"
log "After date:  $AFTER_DATE"
log "Output dir:  $OUTPUT_DIR"

[[ -f "$DOWNLOADER" ]] || die "Downloader not found: $DOWNLOADER"
$PYTHON --version >/dev/null 2>&1 || die "python3 not found in PATH"

mkdir -p "$OUTPUT_DIR"
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------------
# Step 1: Download from each source
# ---------------------------------------------------------------------------

log ""
log "--- Step 1: Downloading papers ---"

IFS=',' read -ra SOURCE_LIST <<< "$SOURCES"

for src in "${SOURCE_LIST[@]}"; do
    src="$(echo "$src" | tr -d '[:space:]')"
    if [[ "$src" == "all" ]]; then
        SOURCE_LIST=(arxiv semantic-scholar openalex)
        break
    fi
done

for src in "${SOURCE_LIST[@]}"; do
    out_file="$OUTPUT_DIR/${src//-/_}_papers.jsonl"
    log "Downloading from $src → $out_file ..."
    $PYTHON "$DOWNLOADER" "$src" \
        --query "$QUERY" \
        --max-papers "$MAX_PAPERS" \
        --after-date "$AFTER_DATE" \
        --output-dir "$OUTPUT_DIR" \
        --contact-email "$CONTACT_EMAIL"
    log "  $src: $(count_lines "$out_file") papers ($(human_size "$out_file"))"
done

# ---------------------------------------------------------------------------
# Step 2: Merge JSONL files and deduplicate by source_uri
# ---------------------------------------------------------------------------

log ""
log "--- Step 2: Merging and deduplicating ---"

$PYTHON - <<'PYEOF'
"""Merge JSONL files and deduplicate by source_uri."""
import json, sys, os
from pathlib import Path

output_dir = Path(os.environ.get("OUTPUT_DIR", "data/corpus/interpretability"))
merged = output_dir / "interpretability_merged.jsonl"

seen: set[str] = set()
total_in = 0
total_out = 0

with merged.open("w", encoding="utf-8") as out_fh:
    for jsonl_file in sorted(output_dir.glob("*_papers.jsonl")):
        with jsonl_file.open(encoding="utf-8") as in_fh:
            for line in in_fh:
                total_in += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError:
                    continue
                uri = doc.get("source_uri", "")
                if uri and uri in seen:
                    continue
                seen.add(uri)
                out_fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
                total_out += 1

print(f"Merged: {total_in} total lines → {total_out} unique documents")
print(f"Output: {merged}")
PYEOF

MERGED_COUNT=$(count_lines "$MERGED_FILE")
MERGED_SIZE=$(human_size "$MERGED_FILE")
log "Merged file: $MERGED_COUNT unique papers ($MERGED_SIZE)"

# ---------------------------------------------------------------------------
# Step 3: (Optional) Ingest into CiteBot
# ---------------------------------------------------------------------------

if [[ "$INGEST" == "1" ]]; then
    log ""
    log "--- Step 3: Ingesting into CiteBot ---"
    log "Running: citebot-ingest ingest $MERGED_FILE"
    $PYTHON -m app.ingestion.cli ingest "$MERGED_FILE" \
        --embedding-version v1 \
        --index-version v1
    log "Ingestion complete."
else
    log ""
    log "--- Step 3: Ingestion skipped (set INGEST=1 to auto-ingest) ---"
    log "To ingest manually:"
    log "  python -m app.ingestion.cli ingest $MERGED_FILE"
    log "  make ingest-interpretability"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log ""
log "=== Summary ==="
log "Merged corpus:  $MERGED_FILE"
log "Paper count:    $MERGED_COUNT"
log "File size:      $MERGED_SIZE"
log ""
log "Next steps:"
log "  1. Ingest:   make ingest-interpretability"
log "  2. Evaluate: make eval-interpretability"
log "  3. Full eval: python scripts/run_interpretability_scenario.py"

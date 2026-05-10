#!/usr/bin/env python3
"""Multi-source academic corpus downloader for CiteBot.

Downloads research papers from arXiv, Semantic Scholar, and OpenAlex,
normalizing each record to the LoadedDocument JSONL format that
``app/ingestion/loaders.py`` (and the CLI) can ingest directly.

Sources
-------
- **arXiv**         – free Atom/XML API; up to 500 results per call.
- **Semantic Scholar** – REST JSON API; 100 results per call (key optional).
- **OpenAlex**      – fully free, cursor pagination; scales to millions.

Usage
-----
.. code-block:: bash

    # Download up to 5 000 interpretability papers from arXiv after 2022
    python scripts/download_corpus.py arxiv \\
        --query "transformer interpretability" \\
        --max-papers 5000 \\
        --after-date 2022-01-01 \\
        --output-dir data/corpus/arxiv

    # Semantic Scholar (set S2_API_KEY env var for higher rate limits)
    python scripts/download_corpus.py semantic-scholar \\
        --query "mechanistic interpretability attention" \\
        --max-papers 10000 \\
        --after-date 2022-01-01 \\
        --output-dir data/corpus/s2

    # OpenAlex bulk (best for large-scale; no auth required)
    python scripts/download_corpus.py openalex \\
        --query "transformer interpretability" \\
        --max-papers 500000 \\
        --after-date 2022-01-01 \\
        --output-dir data/corpus/openalex

    # All three sources combined
    python scripts/download_corpus.py all \\
        --query "transformer interpretability" \\
        --max-papers 50000 \\
        --after-date 2022-01-01 \\
        --output-dir data/corpus

Output
------
Each source writes a compressed JSONL file:
  ``<output-dir>/<source>_papers.jsonl``

Each line is a JSON object matching ``app.ingestion.schemas.LoadedDocument``::

    {
      "source_uri":   "https://arxiv.org/abs/2304.01234",
      "title":        "Attention is Not Explanation",
      "text":         "<abstract or full text>",
      "publisher":    "arXiv",
      "published_at": "2023-04-01T00:00:00",
      "access_policy": "public",
      "metadata": {
        "authors":        ["Jane Smith", "John Doe"],
        "doi":            "10.48550/arXiv.2304.01234",
        "citation_count": 150,
        "categories":     ["cs.LG", "cs.CL"],
        "source":         "arxiv"
      }
    }
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("corpus-downloader")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARXIV_API_BASE = "http://export.arxiv.org/api/query"
ARXIV_BATCH_SIZE = 200  # max safely supported without 503s
ARXIV_RATE_DELAY = 3.0  # seconds between batches (arXiv ToS)
ARXIV_ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"

S2_API_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_BATCH_SIZE = 100  # max per request
S2_MAX_OFFSET = 9_900  # API hard limit
S2_RATE_DELAY = 1.5  # seconds; reduced with API key

OPENALEX_API_BASE = "https://api.openalex.org/works"
OPENALEX_BATCH_SIZE = 200  # max per_page allowed
OPENALEX_RATE_DELAY = 0.12  # ~8 req/s (polite pool)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _http_get(
    url: str, headers: dict[str, str] | None = None, retries: int = 5
) -> bytes:
    """Perform an HTTP GET with exponential back-off on transient errors.

    Args:
        url: The fully-qualified URL to fetch.
        headers: Optional HTTP headers to include in the request.
        retries: Maximum number of retry attempts before raising.

    Returns:
        Raw response body as bytes.

    Raises:
        RuntimeError: If all retry attempts are exhausted.
    """
    hdrs = headers or {}
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=hdrs)  # noqa: S310 – URL validated by callers
            with urlopen(req, timeout=30) as resp:  # noqa: S310
                return resp.read()
        except HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                wait = 2**attempt
                log.warning(
                    "HTTP %s – retrying in %ss (attempt %s/%s)",
                    exc.code,
                    wait,
                    attempt,
                    retries,
                )
                time.sleep(wait)
            else:
                raise
        except URLError as exc:
            wait = 2**attempt
            log.warning("Network error: %s – retrying in %ss", exc.reason, wait)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts")


def _iso(dt_str: str | None) -> str | None:
    """Normalise a date/datetime string to ISO-8601 UTC format or return None.

    Args:
        dt_str: A date string in one of several common formats, or None.

    Returns:
        ISO-8601 formatted datetime string, or None if parsing fails.
    """
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return dt_str  # return as-is if unparseable


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Append a list of document records to a JSONL file.

    Args:
        path: Destination file path (created if absent, appended otherwise).
        records: List of LoadedDocument-compatible dicts to serialise.
    """
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _count_lines(path: Path) -> int:
    """Count lines in an existing JSONL file for resume support.

    Args:
        path: Path to the JSONL file.

    Returns:
        Number of lines (i.e. documents) already written.
    """
    if not path.exists():
        return 0
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


# ---------------------------------------------------------------------------
# arXiv downloader
# ---------------------------------------------------------------------------


def _arxiv_search_url(query: str, start: int, after_date: str | None) -> str:
    """Build an arXiv Atom API URL for the given search parameters.

    Args:
        query: Free-text search query.
        start: Zero-based result offset for pagination.
        after_date: Optional lower-bound date in YYYY-MM-DD format.

    Returns:
        Fully-qualified URL string ready to fetch.
    """
    # arXiv date filter uses submittedDate range inside the query field
    if after_date:
        date_compact = after_date.replace("-", "")
        q = f"({query}) AND submittedDate:[{date_compact}* TO *]"
    else:
        q = query
    params = urlencode(
        {
            "search_query": q,
            "start": start,
            "max_results": ARXIV_BATCH_SIZE,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    return f"{ARXIV_API_BASE}?{params}"


def _parse_arxiv_atom(raw: bytes) -> tuple[int, list[dict[str, Any]]]:
    """Parse an arXiv Atom XML response into LoadedDocument dicts.

    Args:
        raw: Raw bytes of the Atom/XML response body.

    Returns:
        A 2-tuple of (total_results, list_of_document_dicts).
    """
    root = ET.fromstring(raw)  # noqa: S314 – data from arXiv official API

    ns = {"atom": ARXIV_ATOM_NS, "os": ARXIV_OPENSEARCH_NS}
    total_el = root.find("os:totalResults", ns)
    total = int(total_el.text) if total_el is not None and total_el.text else 0

    docs: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        arxiv_id_el = entry.find("atom:id", ns)
        arxiv_id = arxiv_id_el.text.strip() if arxiv_id_el is not None else ""
        # Normalise ID URL: https://arxiv.org/abs/<id>
        if "abs/" in arxiv_id:
            abs_url = arxiv_id
        else:
            abs_url = f"https://arxiv.org/abs/{arxiv_id}"

        title_el = entry.find("atom:title", ns)
        title = " ".join((title_el.text or "").split()) if title_el is not None else ""

        summary_el = entry.find("atom:summary", ns)
        abstract = (
            " ".join((summary_el.text or "").split()) if summary_el is not None else ""
        )

        published_el = entry.find("atom:published", ns)
        published_at = (
            _iso((published_el.text or "").strip())
            if published_el is not None
            else None
        )

        authors = [
            a.find("atom:name", ns).text.strip()
            for a in entry.findall("atom:author", ns)
            if a.find("atom:name", ns) is not None
        ]

        categories = [tag.get("term", "") for tag in entry.findall("atom:category", ns)]

        doi_el = next(
            (
                link
                for link in entry.findall("atom:link", ns)
                if link.get("title") == "doi"
            ),
            None,
        )
        doi = doi_el.get("href") if doi_el is not None else None

        if not title or not abstract:
            continue

        docs.append(
            {
                "source_uri": abs_url,
                "title": title,
                "text": abstract,
                "publisher": "arXiv",
                "published_at": published_at,
                "access_policy": "public",
                "metadata": {
                    "authors": authors,
                    "categories": categories,
                    "doi": doi,
                    "source": "arxiv",
                },
            }
        )
    return total, docs


def download_arxiv(
    query: str,
    max_papers: int,
    after_date: str | None,
    output_path: Path,
) -> int:
    """Download papers from the arXiv Atom API and append them to a JSONL file.

    Respects the arXiv API usage policy (3-second delay between requests).
    Supports resuming by counting lines already present in ``output_path``.

    Args:
        query: Full-text search query (arXiv advanced query syntax accepted).
        max_papers: Maximum number of papers to download.
        after_date: Optional ISO date string (YYYY-MM-DD) for a lower date bound.
        output_path: Destination JSONL file path.

    Returns:
        Total number of papers written to ``output_path`` in this run.
    """
    already = _count_lines(output_path)
    if already:
        log.info("arXiv: resuming – %s papers already in %s", already, output_path)

    start = already
    written = 0
    total_available = None

    while start < max_papers:
        url = _arxiv_search_url(query, start, after_date)
        log.info("arXiv: fetching offset=%s url=%s", start, url)
        try:
            raw = _http_get(url)
        except RuntimeError as exc:
            log.error("arXiv: aborting batch at offset %s – %s", start, exc)
            break

        total_available, docs = _parse_arxiv_atom(raw)
        if total_available is not None and start == already:
            log.info("arXiv: %s total results available", total_available)

        if not docs:
            log.info("arXiv: no more results at offset=%s", start)
            break

        batch = docs[: max_papers - start]
        _write_jsonl(output_path, batch)
        written += len(batch)
        start += len(docs)

        if total_available is not None and start >= total_available:
            log.info("arXiv: reached end of result set (%s)", total_available)
            break

        log.info("arXiv: %s/%s downloaded so far", already + written, max_papers)
        time.sleep(ARXIV_RATE_DELAY)

    log.info("arXiv: finished – %s new papers written to %s", written, output_path)
    return written


# ---------------------------------------------------------------------------
# Semantic Scholar downloader
# ---------------------------------------------------------------------------


def _s2_fields() -> str:
    """Return the comma-separated Semantic Scholar field list for paper queries.

    Returns:
        Field list string suitable for the ``fields`` query parameter.
    """
    return (
        "paperId,externalIds,title,abstract,year,publicationDate,"
        "authors,venue,publicationVenue,citationCount,openAccessPdf,"
        "fieldsOfStudy,s2FieldsOfStudy"
    )


def _parse_s2_paper(
    paper: dict[str, Any], after_date_dt: datetime | None
) -> dict[str, Any] | None:
    """Convert a Semantic Scholar paper dict to a LoadedDocument dict.

    Args:
        paper: Raw paper dict from the Semantic Scholar API.
        after_date_dt: Optional datetime to filter out older papers.

    Returns:
        A LoadedDocument-compatible dict, or None if the paper should be skipped.
    """
    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    if not title or not abstract:
        return None

    pub_date_str = paper.get("publicationDate") or (
        f"{paper['year']}-01-01" if paper.get("year") else None
    )
    published_at = _iso(pub_date_str)

    # Apply temporal filter
    if after_date_dt and pub_date_str:
        try:
            pd = datetime.strptime(pub_date_str[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            if pd < after_date_dt:
                return None
        except ValueError:
            pass

    external_ids = paper.get("externalIds") or {}
    arxiv_id = external_ids.get("ArXiv")
    doi = external_ids.get("DOI")
    source_uri = (
        f"https://arxiv.org/abs/{arxiv_id}"
        if arxiv_id
        else f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}"
    )

    authors = [a.get("name", "") for a in (paper.get("authors") or [])]
    venue = (paper.get("venue") or "") or (
        (paper.get("publicationVenue") or {}).get("name", "")
    )
    open_access = (paper.get("openAccessPdf") or {}).get("url")
    fields = [f.get("category", "") for f in (paper.get("s2FieldsOfStudy") or [])]

    return {
        "source_uri": source_uri,
        "title": title,
        "text": abstract,
        "publisher": venue or "Semantic Scholar",
        "published_at": published_at,
        "access_policy": "public" if open_access else "internal",
        "metadata": {
            "authors": authors,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "citation_count": paper.get("citationCount"),
            "open_access_pdf": open_access,
            "fields_of_study": fields,
            "source": "semantic-scholar",
            "s2_paper_id": paper.get("paperId"),
        },
    }


def download_semantic_scholar(
    query: str,
    max_papers: int,
    after_date: str | None,
    output_path: Path,
) -> int:
    """Download papers from the Semantic Scholar Graph API.

    Uses the ``S2_API_KEY`` environment variable when present for higher
    rate limits (10 req/s vs ~1 req/s unauthenticated).

    Args:
        query: Free-text search query.
        max_papers: Maximum number of papers to write.
        after_date: Optional ISO date string for temporal filtering.
        output_path: Destination JSONL file path.

    Returns:
        Total number of papers written in this run.
    """
    api_key = os.environ.get("S2_API_KEY", "")
    headers: dict[str, str] = {"x-api-key": api_key} if api_key else {}
    delay = 0.15 if api_key else S2_RATE_DELAY

    after_date_dt: datetime | None = None
    if after_date:
        try:
            after_date_dt = datetime.strptime(after_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            log.warning("S2: could not parse after_date '%s'; ignoring", after_date)

    already = _count_lines(output_path)
    if already:
        log.info("S2: resuming – %s papers already in %s", already, output_path)

    written = 0
    offset = already  # approximate resume (S2 results are not stable across runs)

    while written + already < max_papers:
        if offset >= S2_MAX_OFFSET:
            log.info("S2: reached API offset cap (%s); stopping", S2_MAX_OFFSET)
            break

        params = urlencode(
            {
                "query": query,
                "fields": _s2_fields(),
                "limit": S2_BATCH_SIZE,
                "offset": offset,
            }
        )
        url = f"{S2_API_BASE}?{params}"
        log.info("S2: fetching offset=%s", offset)

        try:
            raw = _http_get(url, headers=headers)
        except RuntimeError as exc:
            log.error("S2: aborting at offset %s – %s", offset, exc)
            break

        payload: dict[str, Any] = json.loads(raw)
        papers = payload.get("data") or []
        if not papers:
            log.info("S2: no more results at offset=%s", offset)
            break

        docs: list[dict[str, Any]] = []
        for paper in papers:
            doc = _parse_s2_paper(paper, after_date_dt)
            if doc and written + already + len(docs) < max_papers:
                docs.append(doc)

        _write_jsonl(output_path, docs)
        written += len(docs)
        offset += len(papers)
        log.info("S2: %s/%s downloaded so far", already + written, max_papers)
        time.sleep(delay)

    log.info("S2: finished – %s new papers written to %s", written, output_path)
    return written


# ---------------------------------------------------------------------------
# OpenAlex downloader  (best for large-scale bulk)
# ---------------------------------------------------------------------------


def _openalex_filter(query: str, after_date: str | None) -> str:
    """Build an OpenAlex filter string for the given query and date range.

    Applies a concept search for AI/ML (C41008148) combined with a full-text
    title/abstract search and an optional date lower bound.

    Args:
        query: Free-text search string mapped to ``title_and_abstract.search``.
        after_date: Optional YYYY-MM-DD lower bound for publication date.

    Returns:
        Comma-separated OpenAlex filter expression.
    """
    parts: list[str] = [
        # Restrict to Computer Science concept
        "concepts.id:C41008148",
        f"title_and_abstract.search:{query}",
    ]
    if after_date:
        parts.append(f"from_publication_date:{after_date}")
    return ",".join(parts)


def _parse_openalex_work(work: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an OpenAlex Work object to a LoadedDocument dict.

    Args:
        work: Raw work dict from the OpenAlex API response.

    Returns:
        A LoadedDocument-compatible dict, or None if the work lacks
        a title or abstract.
    """
    title = (work.get("title") or "").strip()
    # OpenAlex stores abstract as an inverted index; rebuild it
    inv_idx: dict[str, list[int]] | None = work.get("abstract_inverted_index")
    abstract = ""
    if inv_idx:
        max_pos = max(pos for positions in inv_idx.values() for pos in positions)
        tokens: list[str] = [""] * (max_pos + 1)
        for word, positions in inv_idx.items():
            for pos in positions:
                tokens[pos] = word
        abstract = " ".join(t for t in tokens if t)

    if not title or not abstract:
        return None

    doi = work.get("doi") or ""
    oa_url = work.get("open_access", {}).get("oa_url") or ""
    source_uri = doi if doi else f"https://openalex.org/{work.get('id', '')}"

    pub_date = work.get("publication_date") or ""
    published_at = _iso(pub_date)

    authors = [
        a.get("author", {}).get("display_name", "")
        for a in (work.get("authorships") or [])
    ]
    venue_obj = work.get("primary_location", {}).get("source") or {}
    venue = venue_obj.get("display_name", "")

    concepts = [c.get("display_name", "") for c in (work.get("concepts") or [])]

    return {
        "source_uri": source_uri,
        "title": title,
        "text": abstract,
        "publisher": venue or "OpenAlex",
        "published_at": published_at,
        "access_policy": "public" if oa_url else "internal",
        "metadata": {
            "authors": authors,
            "doi": doi,
            "open_access_url": oa_url,
            "concepts": concepts[:10],
            "citation_count": work.get("cited_by_count"),
            "source": "openalex",
            "openalex_id": work.get("id"),
        },
    }


def download_openalex(
    query: str,
    max_papers: int,
    after_date: str | None,
    output_path: Path,
    contact_email: str = "research@citebot.local",
) -> int:
    """Download works from the OpenAlex API using cursor pagination.

    OpenAlex supports unlimited cursor-based pagination at no cost.  A
    contact email is sent in the ``User-Agent`` header for the polite pool
    (faster, more reliable).

    Args:
        query: Free-text search query.
        max_papers: Maximum number of works to download.
        after_date: Optional YYYY-MM-DD lower bound for filtering.
        output_path: Destination JSONL file path.
        contact_email: Email address for the OpenAlex polite-pool User-Agent.

    Returns:
        Total number of works written in this run.
    """
    already = _count_lines(output_path)
    if already:
        log.info("OpenAlex: resuming – %s works already present", already)

    headers = {"User-Agent": f"CiteBot-corpus-downloader/1.0 (mailto:{contact_email})"}
    cursor = "*"
    written = 0
    oa_filter = _openalex_filter(query, after_date)

    while written + already < max_papers:
        params = urlencode(
            {
                "filter": oa_filter,
                "per-page": OPENALEX_BATCH_SIZE,
                "cursor": cursor,
                "select": (
                    "id,doi,title,abstract_inverted_index,publication_date,"
                    "authorships,primary_location,concepts,cited_by_count,open_access"
                ),
            }
        )
        url = f"{OPENALEX_API_BASE}?{params}"
        log.info(
            "OpenAlex: cursor=%s written=%s",
            cursor[:20] if cursor != "*" else "*",
            already + written,
        )

        try:
            raw = _http_get(url, headers=headers)
        except RuntimeError as exc:
            log.error("OpenAlex: aborting – %s", exc)
            break

        payload: dict[str, Any] = json.loads(raw)
        works = payload.get("results") or []
        if not works:
            log.info("OpenAlex: result set exhausted")
            break

        docs: list[dict[str, Any]] = []
        for work in works:
            if written + already + len(docs) >= max_papers:
                break
            doc = _parse_openalex_work(work)
            if doc:
                docs.append(doc)

        _write_jsonl(output_path, docs)
        written += len(docs)

        meta = payload.get("meta") or {}
        cursor = meta.get("next_cursor") or ""
        if not cursor:
            log.info("OpenAlex: no next cursor – end of result set")
            break

        time.sleep(OPENALEX_RATE_DELAY)

    log.info("OpenAlex: finished – %s new works written to %s", written, output_path)
    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with sub-commands per source.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="download_corpus",
        description=(
            "Download academic papers from arXiv, Semantic Scholar, or OpenAlex "
            "into CiteBot-compatible JSONL files."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "source",
        choices=["arxiv", "semantic-scholar", "openalex", "all"],
        help="Data source (or 'all' to run all three).",
    )
    parser.add_argument(
        "--query",
        default="transformer interpretability mechanistic attention",
        help="Search query passed to each source API.",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=5_000,
        metavar="N",
        help="Maximum papers to download per source (default: 5 000).",
    )
    parser.add_argument(
        "--after-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Only include papers published on or after this date.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/corpus"),
        help="Directory for JSONL output files (created if absent).",
    )
    parser.add_argument(
        "--contact-email",
        default="research@citebot.local",
        help="Email sent in OpenAlex User-Agent for polite pool access.",
    )
    return parser


def main() -> None:
    """Entrypoint: parse CLI arguments and run the requested download(s).

    Exits with code 1 on fatal errors.
    """
    parser = _build_parser()
    args = parser.parse_args()

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    totals: dict[str, int] = {}
    sources = (
        ["arxiv", "semantic-scholar", "openalex"]
        if args.source == "all"
        else [args.source]
    )

    for source in sources:
        out_file = output_dir / f"{source.replace('-', '_')}_papers.jsonl"
        log.info("=== Starting source: %s → %s ===", source, out_file)

        try:
            if source == "arxiv":
                totals[source] = download_arxiv(
                    query=args.query,
                    max_papers=args.max_papers,
                    after_date=args.after_date,
                    output_path=out_file,
                )
            elif source == "semantic-scholar":
                totals[source] = download_semantic_scholar(
                    query=args.query,
                    max_papers=args.max_papers,
                    after_date=args.after_date,
                    output_path=out_file,
                )
            elif source == "openalex":
                totals[source] = download_openalex(
                    query=args.query,
                    max_papers=args.max_papers,
                    after_date=args.after_date,
                    output_path=out_file,
                    contact_email=args.contact_email,
                )
        except Exception as exc:  # noqa: BLE001
            log.error("Source %s failed: %s", source, exc)

    log.info("=== Download complete ===")
    for source, count in totals.items():
        log.info("  %-20s %s papers", source, count)


if __name__ == "__main__":
    main()

"""Bounded, deterministic query decomposition for multi-part document questions."""

from __future__ import annotations

import re


def decompose_query(query: str, maximum: int = 4) -> list[str]:
    """Split only obvious multi-part questions while preserving the original query."""

    normalized = " ".join(query.split())
    if not normalized:
        return []
    parts = re.split(
        r"\s+(?:and|also|then|versus|vs\.?|compared with|compare with)\s+|[;?]\s+",
        normalized,
        flags=re.IGNORECASE,
    )
    candidates = [part.strip(" ,") for part in parts if part.strip(" ,")]
    if len(candidates) <= 1:
        return [normalized]
    return [normalized, *candidates[: max(1, maximum - 1)]]

"""Optional compact local NLI adapter for unresolved claim/evidence pairs."""

from __future__ import annotations

import asyncio
from typing import Any


class CompactNliVerifier:
    """Lazy-load a sentence-transformers cross-encoder only when enabled."""

    def __init__(self, model_name: str, max_pairs: int = 32) -> None:
        self._model_name = model_name
        self._max_pairs = max_pairs
        self._model: Any = None

    async def verify(self, claim: str, evidence: str) -> dict[str, float | str]:
        """Return entailment/contradiction/neutral scores for one pair."""

        return await asyncio.to_thread(self._verify_sync, claim, evidence)

    def _verify_sync(self, claim: str, evidence: str) -> dict[str, float | str]:
        """Run one bounded model prediction and normalize common NLI outputs."""

        model = self._get_model()
        scores = model.predict([[evidence, claim]], apply_softmax=True)[0]
        values = [float(value) for value in scores]
        labels = ["contradiction", "entailment", "neutral"]
        if len(values) != len(labels):
            raise RuntimeError("Configured NLI model returned an unexpected score shape")
        return {
            "model": self._model_name,
            **{label: value for label, value in zip(labels, values, strict=True)},
        }

    def _get_model(self):
        """Load and cache the verifier model on first unresolved claim."""

        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as error:
                raise RuntimeError(
                    "sentence-transformers is required for compact NLI verification"
                ) from error
            self._model = CrossEncoder(self._model_name)
        return self._model

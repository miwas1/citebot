"""Real evaluator provider binding for RAGAS-backed evaluation runs."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class EvaluatorBinding:
    """Resolved evaluator configuration for one evaluation run."""

    provider: Literal["openai", "gemini"]
    model: str
    environment: dict[str, str]


def build_evaluator_binding(settings: Settings) -> EvaluatorBinding:
    """Resolve credentials for a real evaluator provider."""

    if settings.evaluation_evaluator_provider == "openai":
        if not settings.openai_api_key:
            msg = (
                "OPENAI_API_KEY is required when "
                "EVALUATION_EVALUATOR_PROVIDER=openai"
            )
            raise ValueError(msg)
        return EvaluatorBinding(
            provider="openai",
            model=settings.evaluation_evaluator_model,
            environment={"OPENAI_API_KEY": settings.openai_api_key},
        )
    if settings.evaluation_evaluator_provider == "gemini":
        if not settings.gemini_api_key:
            msg = (
                "GEMINI_API_KEY is required when "
                "EVALUATION_EVALUATOR_PROVIDER=gemini"
            )
            raise ValueError(msg)
        return EvaluatorBinding(
            provider="gemini",
            model=settings.evaluation_evaluator_model,
            environment={
                "GEMINI_API_KEY": settings.gemini_api_key,
                "GOOGLE_API_KEY": settings.gemini_api_key,
            },
        )
    msg = "EVALUATION_EVALUATOR_PROVIDER must be one of openai or gemini"
    raise ValueError(msg)


@contextmanager
def use_evaluator_environment(binding: EvaluatorBinding) -> Iterator[None]:
    """Temporarily expose evaluator credentials to provider SDKs and RAGAS."""

    previous = {name: os.environ.get(name) for name in binding.environment}
    os.environ.update(binding.environment)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

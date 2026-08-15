"""Typed allowlisted calculation contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CalculationCell(BaseModel):
    """One typed input value with source lineage."""

    name: str
    value: float | int | str
    unit: str | None = None
    currency: str | None = None
    anchor_ids: list[str] = Field(default_factory=list)


class CalculationPlan(BaseModel):
    """Allowlisted expression plan; no arbitrary SQL or Python is accepted."""

    operation: Literal[
        "sum",
        "difference",
        "product",
        "ratio",
        "percentage_change",
        "min",
        "max",
        "average",
        "count",
        "date_difference_days",
    ]
    input_names: list[str] = Field(min_length=1, max_length=32)
    output_unit: str | None = None
    output_currency: str | None = None
    tolerance: float = Field(default=0.0, ge=0.0)


class CalculationResult(BaseModel):
    """Reproducible calculation result with input evidence."""

    calculation_run_id: str
    operation: str
    value: float | int
    unit: str | None = None
    currency: str | None = None
    input_cells: list[CalculationCell]
    warnings: list[str] = Field(default_factory=list)
    reproducibility_hash: str
    engine_name: str = "typed-arithmetic"
    engine_version: str = "v1"

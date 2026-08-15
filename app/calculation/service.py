"""Safe deterministic calculations for extracted document values."""

from __future__ import annotations

import json
from datetime import date, datetime
from hashlib import sha256
from uuid import uuid4

from app.calculation.schemas import CalculationCell, CalculationPlan, CalculationResult


class CalculationService:
    """Execute only typed, bounded operations over validated cells."""

    def execute(
        self,
        plan: CalculationPlan,
        cells: list[CalculationCell],
    ) -> CalculationResult:
        """Validate units and calculate without evaluating user-supplied code."""

        by_name = {cell.name: cell for cell in cells}
        selected = [self._require_cell(name, by_name) for name in plan.input_names]
        warnings = self._validate_units(plan, selected)
        values = (
            [self._number(cell) for cell in selected]
            if plan.operation != "date_difference_days"
            else []
        )
        if plan.operation == "sum":
            value = sum(values)
        elif plan.operation == "difference":
            value = values[0] - sum(values[1:])
        elif plan.operation == "product":
            value = 1.0
            for item in values:
                value *= item
        elif plan.operation == "ratio":
            if len(values) != 2 or values[1] == 0:
                raise ValueError("ratio requires two inputs and a non-zero denominator")
            value = values[0] / values[1]
        elif plan.operation == "percentage_change":
            if len(values) != 2 or values[0] == 0:
                raise ValueError("percentage_change requires old and new non-zero values")
            value = ((values[1] - values[0]) / values[0]) * 100
        elif plan.operation == "min":
            value = min(values)
        elif plan.operation == "max":
            value = max(values)
        elif plan.operation == "average":
            value = sum(values) / len(values)
        elif plan.operation == "count":
            value = len(values)
        else:
            value = self._date_difference(selected)
        payload = plan.model_dump(mode="json")
        payload["cells"] = [cell.model_dump(mode="json") for cell in selected]
        reproducibility_hash = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return CalculationResult(
            calculation_run_id=uuid4().hex,
            operation=plan.operation,
            value=value,
            unit=plan.output_unit,
            currency=plan.output_currency,
            input_cells=selected,
            warnings=warnings,
            reproducibility_hash=reproducibility_hash,
            engine_name="typed-arithmetic",
            engine_version="v1",
        )

    def _require_cell(
        self,
        name: str,
        by_name: dict[str, CalculationCell],
    ) -> CalculationCell:
        """Return a named cell or fail closed."""

        if name not in by_name:
            raise ValueError(f"Calculation input is missing: {name}")
        return by_name[name]

    def _number(self, cell: CalculationCell) -> float:
        """Convert one numeric cell without accepting ambiguous strings."""

        if isinstance(cell.value, bool):
            raise ValueError(f"Boolean is not numeric: {cell.name}")
        try:
            return float(cell.value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Calculation input is not numeric: {cell.name}") from error

    def _validate_units(
        self,
        plan: CalculationPlan,
        cells: list[CalculationCell],
    ) -> list[str]:
        """Reject incompatible additive inputs and flag missing provenance."""

        warnings: list[str] = []
        if plan.operation in {"sum", "difference", "average", "min", "max"}:
            units = {cell.unit for cell in cells}
            currencies = {cell.currency for cell in cells}
            if len(units - {None}) > 1:
                raise ValueError("Inputs have incompatible units")
            if len(currencies - {None}) > 1:
                raise ValueError("Inputs have incompatible currencies")
        for cell in cells:
            if not cell.anchor_ids:
                warnings.append(f"Input has no source anchor: {cell.name}")
        return warnings

    def _date_difference(self, cells: list[CalculationCell]) -> float:
        """Calculate day difference from two ISO date/datetime values."""

        if len(cells) != 2:
            raise ValueError("date_difference_days requires two inputs")
        parsed = [self._parse_date(cell.value) for cell in cells]
        return float((parsed[1] - parsed[0]).days)

    def _parse_date(self, value: float | int | str) -> date:
        """Parse only ISO dates and datetimes for reproducibility."""

        if not isinstance(value, str):
            raise ValueError("Date calculation inputs must be ISO strings")
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"Invalid ISO date: {value}") from error

"""Persistence for deterministic calculation runs."""

from __future__ import annotations

from app.calculation.schemas import CalculationPlan, CalculationResult
from app.db.models import CalculationRunRecord
from app.db.session import DatabaseSessionManager


class CalculationRepository:
    """Store typed inputs, outputs, warnings, and reproducibility hashes."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        self._session_manager = session_manager

    async def save(
        self,
        plan: CalculationPlan,
        result: CalculationResult,
        analysis_run_id: str | None = None,
    ) -> str:
        """Persist one calculation result and return its stable run ID."""

        async with self._session_manager.session() as session:
            session.add(
                CalculationRunRecord(
                    calculation_run_id=result.calculation_run_id,
                    analysis_run_id=analysis_run_id,
                    plan_json=plan.model_dump(mode="json"),
                    output_json=result.model_dump(mode="json"),
                    warnings_json=result.warnings,
                    engine_name=result.engine_name,
                    engine_version=result.engine_version,
                    reproducibility_hash=result.reproducibility_hash,
                )
            )
        return result.calculation_run_id

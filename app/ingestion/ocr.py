"""Local OCR engines used by the structured document loader.

The heavy PaddleOCR dependency is optional at import time so text-only installs
remain lightweight. Runtime readiness should verify the selected engine before
accepting scanned documents.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from app.ingestion.schemas import DocumentElement


class OcrEngine:
    """Small synchronous interface for page OCR."""

    name = "unknown"

    def extract(self, image_bytes: bytes, page_number: int) -> list[DocumentElement]:
        raise NotImplementedError


class PaddleOcrEngine(OcrEngine):
    """PaddleOCR/PP-Structure adapter with lazy model loading."""

    name = "paddleocr"

    def __init__(
        self,
        languages: Sequence[str],
        model_path: Path | None = None,
        detection_model_name: str = "PP-OCRv5_mobile_det",
        recognition_model_name: str = "PP-OCRv5_mobile_rec",
    ) -> None:
        self._languages = list(languages)
        self._model_path = model_path
        self._detection_model_name = detection_model_name
        self._recognition_model_name = recognition_model_name
        self._engine: object | None = None

    def extract(self, image_bytes: bytes, page_number: int) -> list[DocumentElement]:
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError(
                "PaddleOCR is required for scanned documents; install the OCR image "
                "or set OCR_PROVIDER=none"
            ) from error
        if self._engine is None:
            language = self._languages[0] if self._languages else "en"
            kwargs: dict[str, object] = {
                "lang": language,
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
                "text_detection_model_name": self._detection_model_name,
                "text_recognition_model_name": self._recognition_model_name,
            }
            if self._model_path is not None:
                detection_path = self._model_path / "detection"
                recognition_path = self._model_path / "recognition"
                if not detection_path.is_dir() or not recognition_path.is_dir():
                    raise RuntimeError(
                        "PaddleOCR model directories are missing; run `make models-provision` "
                        "or set OCR_PROVIDER=none"
                    )
                kwargs["text_detection_model_dir"] = str(detection_path)
                kwargs["text_recognition_model_dir"] = str(recognition_path)
            self._engine = PaddleOCR(**kwargs)
        with tempfile.NamedTemporaryFile(suffix=".png") as image_file:
            image_file.write(image_bytes)
            image_file.flush()
            result = self._predict(image_file.name)
        return _parse_paddle_result(result, page_number)

    def _predict(self, path: str) -> object:
        """Support both the current ``predict`` and older ``ocr`` APIs."""

        if hasattr(self._engine, "predict"):
            return self._engine.predict(path)  # type: ignore[union-attr]
        return self._engine.ocr(path, cls=True)  # type: ignore[union-attr]


class TesseractOcrEngine(OcrEngine):
    """Fast local Tesseract fallback for low-confidence Paddle results."""

    name = "tesseract"

    def __init__(self, language: str = "eng") -> None:
        self._language = language

    def extract(self, image_bytes: bytes, page_number: int) -> list[DocumentElement]:
        executable = shutil.which("tesseract")
        if executable is None:
            raise RuntimeError(
                "Tesseract is required as the configured OCR fallback but was not found"
            )
        process = subprocess.run(
            [executable, "stdin", "stdout", "--psm", "6", "-l", self._language],
            input=image_bytes,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if process.returncode != 0:
            detail = process.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Tesseract OCR failed: {detail}")
        text = process.stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return []
        return [
            DocumentElement(
                element_id=f"p{page_number}-tesseract-0",
                element_type="paragraph",
                text=text,
                reading_order=0,
                confidence=None,
                source_engine=self.name,
            )
        ]


def _parse_paddle_result(result: object, page_number: int) -> list[DocumentElement]:
    """Normalize common PaddleOCR result shapes into stable elements."""

    records: list[dict[str, object]] = []
    candidates = result if isinstance(result, list) else [result]
    for candidate in candidates:
        payload: object = candidate
        if hasattr(candidate, "json"):
            payload = candidate.json  # type: ignore[union-attr]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict):
            records.append(payload)

    elements: list[DocumentElement] = []
    for record in records:
        texts = record.get("rec_texts") or record.get("texts") or []
        scores = record.get("rec_scores") or record.get("scores") or []
        boxes = record.get("rec_boxes") or record.get("boxes") or []
        if not isinstance(texts, list):
            continue
        for index, text in enumerate(texts):
            if not isinstance(text, str) or not text.strip():
                continue
            score = scores[index] if isinstance(scores, list) and index < len(scores) else None
            box = boxes[index] if isinstance(boxes, list) and index < len(boxes) else None
            bbox = _normalize_box(box)
            elements.append(
                DocumentElement(
                    element_id=f"p{page_number}-paddle-{len(elements)}",
                    element_type="paragraph",
                    text=text.strip(),
                    bbox=bbox,
                    reading_order=len(elements),
                    confidence=float(score) if isinstance(score, (int, float)) else None,
                    source_engine="paddleocr",
                )
            )
    return elements


def _normalize_box(value: object) -> tuple[float, float, float, float] | None:
    """Convert Paddle quadrilateral or xyxy boxes to a four-value tuple."""

    if not isinstance(value, list):
        return None
    points: list[tuple[float, float]] = []
    for point in value:
        if isinstance(point, list) and len(point) >= 2:
            try:
                points.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                return None
    if len(points) == 2:
        return points[0][0], points[0][1], points[1][0], points[1][1]
    if len(points) < 4:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)

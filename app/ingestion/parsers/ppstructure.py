"""Optional PaddleOCR PP-Structure adapter boundary."""

from __future__ import annotations

from app.ingestion.schemas import LoadedDocument


class PPStructureParser:
    """Lazy boundary for layout, table, and formula extraction."""

    name = "ppstructure"
    version = "optional-v1"

    def can_parse(self, media_type: str | None) -> bool:
        """Support image-like and PDF inputs when explicitly selected."""

        return media_type in {"application/pdf", "image/png", "image/jpeg", "image/tiff"}

    def parse(self, document: LoadedDocument) -> LoadedDocument:
        """Run PP-OCRv5 prediction and convert recognized regions to elements."""

        try:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError(
                "PP-Structure parser requested but paddleocr is not installed"
            ) from error
        engine = PaddleOCR(
            lang=document.metadata.get("language", "en"),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        results = list(engine.predict(document.source_uri))
        from app.ingestion.schemas import DocumentElement, StructuredDocument, StructuredPage

        elements: list[DocumentElement] = []
        for result_index, result in enumerate(results):
            payload = _result_payload(result)
            texts = payload.get("rec_texts") or payload.get("texts") or []
            scores = payload.get("rec_scores") or payload.get("scores") or []
            for index, text in enumerate(texts):
                value = str(text).strip()
                if not value:
                    continue
                confidence = float(scores[index]) if index < len(scores) else None
                elements.append(
                    DocumentElement(
                        element_id=f"ppstructure-{result_index}-{index}",
                        element_type="paragraph",
                        text=value,
                        reading_order=len(elements),
                        confidence=confidence,
                        source_engine="ppstructure",
                    )
                )
        if not elements:
            raise RuntimeError("PP-Structure returned no recognized text")
        text = "\n\n".join(element.text for element in elements)
        structured = StructuredDocument(
            media_type=document.structured.media_type if document.structured else None,
            parser_version=self.version,
            pages=[
                StructuredPage(
                    page_number=1,
                    extraction_method="ppstructure",
                    elements=elements,
                )
            ],
            quality_summary={"adapter": "ppstructure", "conversion_status": "completed"},
        )
        return document.model_copy(
            update={
                "text": text,
                "structured": structured,
                "metadata": {
                    **document.metadata,
                    "parser_name": self.name,
                    "parser_version": self.version,
                },
            }
        )


def _result_payload(result: object) -> dict[str, object]:
    """Read current and older Paddle result wrappers without hard dependency coupling."""

    if isinstance(result, dict):
        return result
    json_value = getattr(result, "json", None)
    if callable(json_value):
        payload = json_value()
        if isinstance(payload, dict):
            return payload.get("res", payload)
    payload = getattr(result, "res", None)
    return payload if isinstance(payload, dict) else {}

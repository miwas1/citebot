"""Optional Docling adapter loaded only when explicitly configured."""

from __future__ import annotations

from app.ingestion.schemas import LoadedDocument


class DoclingParser:
    """Lazy adapter boundary for Docling hierarchical parsing."""

    name = "docling"
    version = "optional-v1"

    def can_parse(self, media_type: str | None) -> bool:
        """Support PDF and office documents when the optional package is installed."""

        return media_type in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }

    def parse(self, document: LoadedDocument) -> LoadedDocument:
        """Convert through Docling and retain a hierarchical Markdown candidate."""

        try:
            from docling.document_converter import (
                DocumentConverter,  # type: ignore[import-not-found]
            )
        except ImportError as error:
            raise RuntimeError(
                "Docling parser requested but docling is not installed; use the native path"
            ) from error
        result = DocumentConverter().convert(document.source_uri)
        markdown = result.document.export_to_markdown()
        elements: list[dict[str, object]] = []
        for index, line in enumerate(markdown.splitlines()):
            text = line.strip()
            if not text:
                continue
            element_type = "heading" if text.startswith("#") else "paragraph"
            elements.append(
                {
                    "element_id": f"docling-{index}",
                    "element_type": element_type,
                    "text": text.lstrip("# "),
                    "markdown": text,
                    "reading_order": index,
                    "source_engine": "docling",
                }
            )
        from app.ingestion.schemas import DocumentElement, StructuredDocument, StructuredPage

        structured = StructuredDocument(
            media_type=document.structured.media_type if document.structured else None,
            parser_version=self.version,
            pages=[
                StructuredPage(
                    page_number=1,
                    extraction_method="docling",
                    elements=[DocumentElement.model_validate(item) for item in elements],
                )
            ],
            extraction_issues=[],
            quality_summary={"adapter": "docling", "conversion_status": "completed"},
        )
        return document.model_copy(
            update={
                "text": markdown,
                "structured": structured,
                "metadata": {
                    **document.metadata,
                    "parser_name": self.name,
                    "parser_version": self.version,
                },
            }
        )

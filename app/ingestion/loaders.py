"""Corpus loaders for local file-based sample datasets."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from xml.etree import ElementTree

from app.core.config import Settings
from app.ingestion.ocr import PaddleOcrEngine, TesseractOcrEngine
from app.ingestion.parsers.docling import DoclingParser
from app.ingestion.parsers.ppstructure import PPStructureParser
from app.ingestion.quality import assess_page, route_page
from app.ingestion.schemas import (
    DocumentElement,
    DocumentTable,
    LoadedDocument,
    StructuredDocument,
    StructuredPage,
)
from app.ingestion.table_serializer import table_to_markdown


class LocalCorpusLoader:
    """Load local corpora and preserve page/element provenance for documents."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._paddle = None
        self._tesseract = None

    def load(self, source_path: Path) -> list[LoadedDocument]:
        """Load all supported documents, retaining the compatibility list API."""

        return list(self.iter_load(source_path))

    def iter_load(self, source_path: Path) -> Iterator[LoadedDocument]:
        """Stream supported documents while enforcing per-job resource budgets."""

        if not source_path.exists():
            msg = f"Source path does not exist: {source_path}"
            raise FileNotFoundError(msg)

        files = (
            [source_path]
            if source_path.is_file()
            else sorted(path for path in source_path.rglob("*") if path.is_file())
        )
        total_bytes = 0
        documents_seen = 0
        for file_path in files:
            file_size = file_path.stat().st_size
            total_bytes += file_size
            if total_bytes > self._settings.ingestion_max_source_bytes:
                raise ValueError(
                    "Ingestion source exceeds INGESTION_MAX_SOURCE_BYTES: "
                    f"{self._settings.ingestion_max_source_bytes}"
                )
            if file_size > self._settings.max_input_bytes:
                raise ValueError(
                    f"Input exceeds MAX_INPUT_BYTES: {file_path.name}"
                )
            loaded_documents: Iterator[LoadedDocument]
            if file_path.suffix.lower() in {".txt", ".md"}:
                loaded_documents = iter((self._load_text_document(file_path),))
            elif file_path.suffix.lower() == ".json":
                loaded_documents = self._load_json_documents(file_path)
            elif file_path.suffix.lower() == ".jsonl":
                loaded_documents = self._load_jsonl_documents(file_path)
            elif file_path.suffix.lower() == ".pdf":
                loaded_documents = iter((self._load_pdf_document(file_path),))
            elif file_path.suffix.lower() == ".docx":
                loaded_documents = iter((self._load_docx_document(file_path),))
            elif file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
                loaded_documents = iter((self._load_image_document(file_path),))
            else:
                continue
            for document in loaded_documents:
                documents_seen += 1
                if documents_seen > self._settings.ingestion_max_documents:
                    raise ValueError(
                        "Ingestion source exceeds INGESTION_MAX_DOCUMENTS: "
                        f"{self._settings.ingestion_max_documents}"
                    )
                yield document

    def _load_text_document(self, file_path: Path) -> LoadedDocument:
        """Read a plain-text or Markdown document from disk."""

        return LoadedDocument(
            source_uri=str(file_path.resolve()),
            title=file_path.stem.replace("_", " ").strip() or file_path.name,
            text=file_path.read_text(encoding="utf-8"),
            metadata={
                "file_name": file_path.name,
                "file_type": file_path.suffix.lower().lstrip("."),
            },
        )

    def _load_pdf_document(self, file_path: Path) -> LoadedDocument:
        """Extract native PDF text and OCR only pages below the quality gate."""

        if self._settings.document_parser in {"docling", "ppstructure"}:
            parser = (
                DoclingParser()
                if self._settings.document_parser == "docling"
                else PPStructureParser()
            )
            return parser.parse(
                LoadedDocument(
                    source_uri=str(file_path.resolve()),
                    title=file_path.stem.replace("_", " ").strip() or file_path.name,
                    text="",
                    metadata={
                        "file_name": file_path.name,
                        "file_type": "pdf",
                        "parser_name": parser.name,
                    },
                    structured=StructuredDocument(media_type="application/pdf"),
                )
            )

        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("PyMuPDF is required to ingest PDF documents") from error
        with fitz.open(file_path) as pdf:
            if len(pdf) > self._settings.ocr_max_pages:
                raise ValueError(
                    f"PDF has {len(pdf)} pages; OCR_MAX_PAGES is "
                    f"{self._settings.ocr_max_pages}"
                )
            pages: list[StructuredPage] = []
            tables: list[DocumentTable] = []
            page_texts: list[str] = []
            for page_number, page in enumerate(pdf, start=1):
                native_text = page.get_text("text") or ""
                coverage = min(1.0, len(native_text.strip()) / 100.0)
                blocks = self._native_blocks(page, page_number, native_text)
                page_tables, table_elements = self._native_tables(page, page_number)
                tables.extend(page_tables)
                blocks.extend(table_elements)
                if page_tables:
                    native_text = "\n\n".join(
                        [native_text, *(table_to_markdown(table) for table in page_tables)]
                    ).strip()
                extraction_method = "native"
                ocr_confidence = None
                quality = assess_page(native_text, blocks)
                route = route_page(quality, self._settings.ocr_min_native_text_coverage)
                if (
                    self._settings.document_parser == "ocr"
                    or (
                        self._settings.document_parser == "auto"
                        and route == "ocr"
                    )
                ):
                    if self._settings.ocr_provider == "none":
                        raise RuntimeError(
                            f"Page {page_number} requires OCR but OCR_PROVIDER=none"
                        )
                    image = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).tobytes("png")
                    blocks = self._ocr_blocks(image, page_number)
                    native_text = "\n".join(element.text for element in blocks)
                    extraction_method = "ocr"
                    confidences = [
                        element.confidence
                        for element in blocks
                        if element.confidence is not None
                    ]
                    ocr_confidence = min(confidences) if confidences else None
                page_texts.append(native_text.strip())
                pages.append(
                    StructuredPage(
                        page_number=page_number,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        rotation=int(page.rotation),
                        extraction_method=extraction_method,
                        native_text_coverage=coverage,
                        ocr_confidence=ocr_confidence,
                        quality_issues=(
                            [{"route": route, "signals": quality.as_dict()}]
                            if route != "native"
                            else []
                        ),
                        elements=blocks,
                    )
                )
            text = "\n\n".join(value for value in page_texts if value).strip()
            structured = self._assign_character_offsets(
                StructuredDocument(
                    document_id=str(uuid5(NAMESPACE_URL, str(file_path.resolve()))),
                    media_type="application/pdf",
                    parser_version="pymupdf-structured-v2",
                    language=self._settings.ocr_languages.split(",")[0].strip() or None,
                    pages=pages,
                    tables=tables,
                    quality_summary={
                        "page_count": len(pages),
                        "table_count": len(tables),
                        "ocr_pages": sum(page.extraction_method == "ocr" for page in pages),
                    },
                ),
                text,
            )
            return LoadedDocument(
                source_uri=str(file_path.resolve()),
                title=file_path.stem.replace("_", " ").strip() or file_path.name,
                text=text,
                metadata={"file_name": file_path.name, "file_type": "pdf"},
                structured=structured,
            )

    def _native_tables(
        self,
        page: object,
        page_number: int,
    ) -> tuple[list[DocumentTable], list[DocumentElement]]:
        """Extract born-digital PDF tables when the installed PyMuPDF supports it."""

        find_tables = getattr(page, "find_tables", None)
        if not callable(find_tables):
            return [], []
        try:
            finder = find_tables()
            raw_tables = getattr(finder, "tables", []) or []
        except Exception:
            return [], []
        tables: list[DocumentTable] = []
        elements: list[DocumentElement] = []
        for table_index, raw_table in enumerate(raw_tables):
            try:
                extracted = raw_table.extract() or []
            except Exception:
                continue
            rows = [
                [str(cell or "").strip() for cell in row]
                for row in extracted
                if any(str(cell or "").strip() for cell in row)
            ]
            if not rows:
                continue
            table_id = f"p{page_number}-table-{table_index}"
            bbox = getattr(raw_table, "bbox", None)
            source_element_ids: list[str] = []
            for row_index, row in enumerate(rows):
                for column_index, value in enumerate(row):
                    if not value:
                        continue
                    element_id = f"{table_id}-r{row_index}-c{column_index}"
                    source_element_ids.append(element_id)
                    elements.append(
                        DocumentElement(
                            element_id=element_id,
                            element_type="table_cell",
                            text=value,
                            bbox=(
                                tuple(float(item) for item in bbox)
                                if bbox and len(bbox) == 4
                                else None
                            ),
                            reading_order=len(elements),
                            source_engine="pymupdf-table",
                            table_id=table_id,
                            row_index=row_index,
                            column_index=column_index,
                        )
                    )
            tables.append(
                DocumentTable(
                    table_id=table_id,
                    page_numbers=[page_number],
                    headers=rows[0],
                    rows=rows[1:],
                    source_element_ids=source_element_ids,
                    confidence=1.0,
                    attributes={"bbox": list(bbox) if bbox and len(bbox) == 4 else None},
                )
            )
        return tables, elements

    def _native_blocks(
        self,
        page: object,
        page_number: int,
        text: str,
    ) -> list[DocumentElement]:
        """Convert native PDF text blocks into ordered elements."""

        blocks = page.get_text("blocks")  # type: ignore[union-attr]
        elements: list[DocumentElement] = []
        for block in blocks:
            if not isinstance(block, tuple) or len(block) < 5:
                continue
            block_text = str(block[4]).strip()
            if not block_text:
                continue
            element_type = "heading" if self._looks_like_heading(block_text) else "paragraph"
            elements.append(
                DocumentElement(
                    element_id=f"p{page_number}-native-{len(elements)}",
                    element_type=element_type,
                    text=block_text,
                    bbox=(float(block[0]), float(block[1]), float(block[2]), float(block[3])),
                    reading_order=len(elements),
                    source_engine="pymupdf",
                )
            )
        if elements or not text.strip():
            return elements
        return [
            DocumentElement(
                element_id=f"p{page_number}-native-0",
                element_type="paragraph",
                text=text.strip(),
                reading_order=0,
                source_engine="pymupdf",
            )
        ]

    def _ocr_blocks(self, image: bytes, page_number: int) -> list[DocumentElement]:
        """Run PaddleOCR and optionally retry an empty/low-confidence page."""

        if self._settings.ocr_provider == "none":
            raise RuntimeError("OCR_PROVIDER=none cannot process image content")
        if self._paddle is None:
            self._paddle = PaddleOcrEngine(
                self._settings.ocr_languages.split(","),
                self._settings.ocr_model_path,
                self._settings.ocr_detection_model_name,
                self._settings.ocr_recognition_model_name,
            )
        try:
            blocks = self._paddle.extract(image, page_number)
        except RuntimeError:
            if self._settings.ocr_fallback_provider != "tesseract":
                raise
            blocks = []
        confidence_values = [
            element.confidence
            for element in blocks
            if element.confidence is not None
        ]
        low_confidence = (
            not confidence_values
            or min(confidence_values) < self._settings.ocr_min_confidence
        )
        if low_confidence and self._settings.ocr_fallback_provider == "tesseract":
            if self._tesseract is None:
                language = self._settings.ocr_languages.split(",")[0].strip() or "eng"
                language = "eng" if language == "en" else language
                self._tesseract = TesseractOcrEngine(
                    language
                )
            fallback = self._tesseract.extract(image, page_number)
            if fallback:
                return fallback
        return blocks

    def _assign_character_offsets(
        self,
        structured: StructuredDocument,
        text: str,
    ) -> StructuredDocument:
        """Map ordered page elements to offsets in the flattened text."""

        cursor = 0
        for page in structured.pages:
            for element in page.elements:
                start = text.find(element.text, cursor)
                if start < 0:
                    continue
                element.char_start = start
                element.char_end = start + len(element.text)
                cursor = element.char_end
        return structured

    def _looks_like_heading(self, value: str) -> bool:
        """Use conservative lexical signals for native heading classification."""

        compact = " ".join(value.split())
        return len(compact) <= 120 and (compact.isupper() or compact[:2].isdigit())

    def _load_json_documents(self, file_path: Path) -> Iterator[LoadedDocument]:
        """Read one or more documents from a JSON file."""

        payload = json.loads(file_path.read_text(encoding="utf-8"))
        raw_documents = payload if isinstance(payload, list) else [payload]
        for index, raw_document in enumerate(raw_documents):
            yield LoadedDocument(
                source_uri=raw_document.get("source_uri")
                or f"{file_path.resolve()}#{index}",
                title=raw_document.get("title") or f"{file_path.stem}-{index}",
                text=raw_document.get("text") or raw_document.get("content") or "",
                publisher=raw_document.get("publisher"),
                published_at=raw_document.get("published_at"),
                access_policy=raw_document.get("access_policy", "internal"),
                metadata=raw_document.get("metadata", {}),
            )

    def _load_jsonl_documents(self, file_path: Path) -> Iterator[LoadedDocument]:
        """Read one JSON document per line without loading the whole corpus."""

        with file_path.open(encoding="utf-8") as source:
            lines = enumerate(source)
            for index, line in lines:
                if not line.strip():
                    continue
                payload = json.loads(line)
                yield LoadedDocument(
                    source_uri=payload.get("source_uri") or f"{file_path.resolve()}#{index}",
                    title=payload.get("title") or f"{file_path.stem}-{index}",
                    text=payload.get("text") or payload.get("content") or "",
                    publisher=payload.get("publisher"),
                    published_at=payload.get("published_at"),
                    access_policy=payload.get("access_policy", "internal"),
                    metadata=payload.get("metadata", {}),
                )

    def _load_docx_document(self, file_path: Path) -> LoadedDocument:
        """Extract DOCX paragraphs using the standard-library ZIP/XML parser."""

        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        with zipfile.ZipFile(file_path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        paragraphs: list[str] = []
        for paragraph in root.iter(f"{namespace}p"):
            text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
            if text.strip():
                paragraphs.append(text.strip())
        text = "\n\n".join(paragraphs)
        structured = StructuredDocument(
            document_id=str(uuid5(NAMESPACE_URL, str(file_path.resolve()))),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            parser_version="docx-structured-v1",
            pages=[
                StructuredPage(
                    page_number=1,
                    extraction_method="native",
                    elements=[
                        DocumentElement(
                            element_id=f"p1-docx-{index}",
                            text=value,
                            reading_order=index,
                            source_engine="docx-xml",
                        )
                        for index, value in enumerate(paragraphs)
                    ],
                )
            ],
        )
        return LoadedDocument(
            source_uri=str(file_path.resolve()),
            title=file_path.stem.replace("_", " ").strip() or file_path.name,
            text=text,
            metadata={"file_name": file_path.name, "file_type": "docx"},
            structured=self._assign_character_offsets(structured, text),
        )

    def _load_image_document(self, file_path: Path) -> LoadedDocument:
        """OCR a standalone image and retain the resulting page structure."""

        blocks = self._ocr_blocks(file_path.read_bytes(), 1)
        text = "\n\n".join(element.text for element in blocks)
        structured = StructuredDocument(
            document_id=str(uuid5(NAMESPACE_URL, str(file_path.resolve()))),
            media_type="image/" + file_path.suffix.lower().lstrip("."),
            parser_version="image-ocr-v1",
            language=self._settings.ocr_languages.split(",")[0].strip() or None,
            pages=[
                StructuredPage(
                    page_number=1,
                    extraction_method="ocr",
                    elements=blocks,
                )
            ],
        )
        return LoadedDocument(
            source_uri=str(file_path.resolve()),
            title=file_path.stem.replace("_", " ").strip() or file_path.name,
            text=text,
            metadata={"file_name": file_path.name, "file_type": "image"},
            structured=self._assign_character_offsets(structured, text),
        )

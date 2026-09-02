from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from pypdf import PdfReader

from .chunk_service import split_into_chunks
from .vector_store import SourceChunk


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_FILE_TYPES = {".txt": "txt", ".pdf": "pdf", ".docx": "docx"}


class DocumentService:
    def __init__(
        self,
        store: Any,
        embedder: Any,
        chunk_size: int = 260,
        overlap: int = 50,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.chunk_size = chunk_size
        self.overlap = overlap

    def add_text_document(
        self, filename: str, content: str, allow_existing: bool = True
    ) -> dict:
        chunks = [
            SourceChunk(content=chunk, file_type="txt")
            for chunk in split_into_chunks(content, self.chunk_size, self.overlap)
        ]
        if not chunks:
            raise ValueError("document content is empty after cleaning")
        return self.store.add_document(
            filename, chunks, self.embedder, allow_existing=allow_existing
        )

    def add_file_document(self, filename: str, data: bytes) -> dict:
        if not data:
            raise ValueError("uploaded file is empty")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("uploaded file exceeds the 10 MiB limit")

        suffix = Path(filename).suffix.lower()
        file_type = SUPPORTED_FILE_TYPES.get(suffix)
        if not file_type:
            raise ValueError("unsupported file type; use TXT, PDF, or DOCX")

        if file_type == "txt":
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError("TXT file must use UTF-8 encoding") from exc
            parts = [(text, None, None)]
        elif file_type == "pdf":
            parts = self._read_pdf(data)
        else:
            parts = self._read_docx(data)

        chunks = [
            SourceChunk(chunk, file_type, page_number, section)
            for text, page_number, section in parts
            for chunk in split_into_chunks(text, self.chunk_size, self.overlap)
        ]
        if not chunks:
            raise ValueError("document content is empty after cleaning")
        return self.store.add_document(filename, chunks, self.embedder)

    @staticmethod
    def _read_pdf(data: bytes) -> list[tuple[str, int | None, str | None]]:
        try:
            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted:
                raise ValueError("encrypted PDF files are not supported")
            parts = [
                (page.extract_text() or "", page_number, None)
                for page_number, page in enumerate(reader.pages, start=1)
            ]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDF parsing failed") from exc
        if not any(text.strip() for text, _, _ in parts):
            raise ValueError(
                "PDF contains no extractable text; scanned PDFs require OCR and are not supported"
            )
        return parts

    @staticmethod
    def _read_docx(data: bytes) -> list[tuple[str, int | None, str | None]]:
        try:
            document = DocxDocument(BytesIO(data))
        except Exception as exc:
            raise ValueError("DOCX parsing failed") from exc

        parts = []
        heading = ""
        for paragraph_number, paragraph in enumerate(document.paragraphs, start=1):
            text = paragraph.text.strip()
            if not text:
                continue
            if paragraph.style.style_id.startswith("Heading"):
                heading = text
                section = f"{text} / 段落 {paragraph_number}"
                content = text
            else:
                section = (
                    f"{heading} / 段落 {paragraph_number}"
                    if heading
                    else f"段落 {paragraph_number}"
                )
                content = f"{heading}\n{text}" if heading else text
            parts.append((content, None, section))
        return parts

    def load_sample_documents(self, sample_dir: Path) -> list[dict]:
        loaded_documents: list[dict] = []

        for file_path in sorted(sample_dir.glob("*.txt")):
            content = file_path.read_text(encoding="utf-8")
            loaded_documents.append(
                self.add_text_document(file_path.name, content)
            )

        return loaded_documents

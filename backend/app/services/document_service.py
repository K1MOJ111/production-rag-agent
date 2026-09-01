from pathlib import Path
from typing import Any

from .chunk_service import split_into_chunks


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

    def add_text_document(self, filename: str, content: str) -> dict:
        chunks = split_into_chunks(content, self.chunk_size, self.overlap)
        if not chunks:
            raise ValueError("document content is empty after cleaning")
        return self.store.add_document(filename, chunks, self.embedder)

    def load_sample_documents(self, sample_dir: Path) -> list[dict]:
        loaded_documents: list[dict] = []

        for file_path in sorted(sample_dir.glob("*.txt")):
            content = file_path.read_text(encoding="utf-8")
            loaded_documents.append(
                self.add_text_document(file_path.name, content)
            )

        return loaded_documents

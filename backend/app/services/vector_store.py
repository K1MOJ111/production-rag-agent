from dataclasses import dataclass
from uuid import uuid4

from .mock_embedding_service import MockEmbeddingService


@dataclass
class ChunkRecord:
    document_id: str
    filename: str
    chunk_id: str
    index: int
    content: str
    embedding: dict[str, float]


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._documents: dict[str, dict] = {}
        self._chunks: list[ChunkRecord] = []

    def add_document(
        self,
        filename: str,
        chunks: list[str],
        embedder: MockEmbeddingService,
    ) -> dict:
        document_id = f"doc_{uuid4().hex[:8]}"
        document_info = {
            "document_id": document_id,
            "filename": filename,
            "chunk_count": len(chunks),
            "preview": chunks[0][:120] if chunks else "",
        }
        self._documents[document_id] = document_info

        for index, chunk in enumerate(chunks, start=1):
            self._chunks.append(
                ChunkRecord(
                    document_id=document_id,
                    filename=filename,
                    chunk_id=f"{document_id}_chunk_{index:03d}",
                    index=index,
                    content=chunk,
                    embedding=embedder.embed(chunk),
                )
            )

        return document_info

    def list_documents(self) -> list[dict]:
        return list(self._documents.values())

    def get_chunks(self, document_id: str) -> list[ChunkRecord]:
        return [chunk for chunk in self._chunks if chunk.document_id == document_id]

    def delete_document(self, document_id: str) -> bool:
        if document_id not in self._documents:
            return False

        del self._documents[document_id]
        self._chunks = [
            chunk for chunk in self._chunks if chunk.document_id != document_id
        ]
        return True

    def search(
        self,
        question: str,
        top_k: int,
        embedder: MockEmbeddingService,
    ) -> list[dict]:
        query_embedding = embedder.embed(question)
        scored_chunks = []

        for chunk in self._chunks:
            score = embedder.similarity(query_embedding, chunk.embedding)
            if score > 0:
                scored_chunks.append(
                    {
                        "document_id": chunk.document_id,
                        "filename": chunk.filename,
                        "chunk_id": chunk.chunk_id,
                        "score": round(score, 4),
                        "content": chunk.content,
                    }
                )

        scored_chunks.sort(key=lambda item: item["score"], reverse=True)
        return scored_chunks[:top_k]

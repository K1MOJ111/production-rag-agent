import asyncio
import hashlib
import sys
from uuid import uuid4

from langchain_core.documents import Document
from langchain_postgres import Column, PGEngine, PGVectorStore
from sqlalchemy import create_engine, inspect, text

from .vector_store import ChunkRecord, DuplicateDocumentError, SourceChunk


def _configure_postgres_event_loop() -> None:
    if sys.platform == "win32":
        # ponytail: remove when langchain-postgres supports a loop factory on Windows.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class PostgresVectorStore:
    TABLE_NAME = "rag_chunks"

    def __init__(self, database_url: str, embedding_service, vector_size: int) -> None:
        _configure_postgres_event_loop()
        self._sql_engine = create_engine(database_url, pool_pre_ping=True)
        self._pg_engine = PGEngine.from_connection_string(url=database_url)
        self._ensure_schema(vector_size)
        self._vector_store = PGVectorStore.create_sync(
            engine=self._pg_engine,
            table_name=self.TABLE_NAME,
            embedding_service=embedding_service,
            metadata_columns=[
                "document_id",
                "filename",
                "chunk_id",
                "chunk_index",
                "content_hash",
                "file_type",
                "page_number",
                "section",
            ],
        )

    @staticmethod
    def document_hash(chunks: list[str]) -> str:
        return hashlib.sha256("\n".join(chunks).encode("utf-8")).hexdigest()

    def _ensure_schema(self, vector_size: int) -> None:
        with self._sql_engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS documents (
                        document_id UUID PRIMARY KEY,
                        filename TEXT NOT NULL,
                        file_type VARCHAR(10) NOT NULL DEFAULT 'txt',
                        content_hash CHAR(64) NOT NULL UNIQUE,
                        status VARCHAR(20) NOT NULL CHECK (status IN ('indexing', 'ready', 'failed')),
                        chunk_count INTEGER NOT NULL DEFAULT 0,
                        preview TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
                    "file_type VARCHAR(10) NOT NULL DEFAULT 'txt'"
                )
            )

        if not inspect(self._sql_engine).has_table(self.TABLE_NAME):
            self._pg_engine.init_vectorstore_table(
                table_name=self.TABLE_NAME,
                vector_size=vector_size,
                metadata_columns=[
                    Column("document_id", "VARCHAR", nullable=False),
                    Column("filename", "TEXT", nullable=False),
                    Column("chunk_id", "VARCHAR", nullable=False),
                    Column("chunk_index", "INTEGER", nullable=False),
                    Column("content_hash", "CHAR(64)", nullable=False),
                    Column("file_type", "VARCHAR", nullable=False),
                    Column("page_number", "INTEGER", nullable=True),
                    Column("section", "TEXT", nullable=True),
                ],
            )
        else:
            with self._sql_engine.begin() as connection:
                connection.execute(
                    text(
                        f"""
                        ALTER TABLE {self.TABLE_NAME}
                            ADD COLUMN IF NOT EXISTS file_type VARCHAR NOT NULL DEFAULT 'txt',
                            ADD COLUMN IF NOT EXISTS page_number INTEGER,
                            ADD COLUMN IF NOT EXISTS section TEXT
                        """
                    )
                )

    def add_document(
        self,
        filename: str,
        chunks: list[SourceChunk],
        embedder,
        allow_existing: bool = False,
    ) -> dict:
        del embedder
        content_hash = self.document_hash([chunk.content for chunk in chunks])
        existing = None
        with self._sql_engine.begin() as connection:
            existing = connection.execute(
                text(
                    """
                    SELECT document_id, filename, file_type, content_hash, status,
                           chunk_count, preview, created_at
                    FROM documents WHERE content_hash = :content_hash
                    """
                ),
                {"content_hash": content_hash},
            ).mappings().first()
            if existing and existing["status"] == "ready":
                if allow_existing:
                    return self._document_info(existing)
                raise DuplicateDocumentError("document content already exists")

            document_id = str(existing["document_id"] if existing else uuid4())
            if existing:
                connection.execute(
                    text(
                        """
                        UPDATE documents
                        SET filename = :filename, file_type = :file_type,
                            status = 'indexing', chunk_count = 0, preview = :preview
                        WHERE document_id = :document_id
                        """
                    ),
                    {
                        "document_id": document_id,
                        "filename": filename,
                        "file_type": chunks[0].file_type,
                        "preview": chunks[0].content[:120],
                    },
                )
            else:
                connection.execute(
                    text(
                        """
                        INSERT INTO documents (
                            document_id, filename, file_type, content_hash, status, preview
                        ) VALUES (
                            :document_id, :filename, :file_type, :content_hash,
                            'indexing', :preview
                        )
                        """
                    ),
                    {
                        "document_id": document_id,
                        "filename": filename,
                        "file_type": chunks[0].file_type,
                        "content_hash": content_hash,
                        "preview": chunks[0].content[:120],
                    },
                )

        try:
            if existing:
                self._vector_store.delete(filter={"document_id": document_id})
            chunk_ids = [str(uuid4()) for _ in chunks]
            documents = [
                Document(
                    id=chunk_id,
                    page_content=chunk.content,
                    metadata={
                        "document_id": document_id,
                        "filename": filename,
                        "chunk_id": chunk_id,
                        "chunk_index": index,
                        "content_hash": content_hash,
                        "file_type": chunk.file_type,
                        "page_number": chunk.page_number,
                        "section": chunk.section,
                    },
                )
                for index, (chunk_id, chunk) in enumerate(
                    zip(chunk_ids, chunks), start=1
                )
            ]
            self._vector_store.add_documents(documents, ids=chunk_ids)
        except Exception as exc:
            with self._sql_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE documents SET status = 'failed' WHERE document_id = :document_id"
                    ),
                    {"document_id": document_id},
                )
            raise RuntimeError("document indexing failed") from exc

        with self._sql_engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE documents SET status = 'ready', chunk_count = :chunk_count
                    WHERE document_id = :document_id
                    RETURNING document_id, filename, file_type, content_hash, status,
                              chunk_count, preview, created_at
                    """
                ),
                {"document_id": document_id, "chunk_count": len(chunks)},
            ).mappings().one()
        return self._document_info(row)

    def list_documents(self) -> list[dict]:
        with self._sql_engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT document_id, filename, file_type, content_hash, status,
                           chunk_count, preview, created_at
                    FROM documents ORDER BY created_at, document_id
                    """
                )
            ).mappings().all()
        return [self._document_info(row) for row in rows]

    def get_chunks(self, document_id: str) -> list[ChunkRecord]:
        with self._sql_engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT document_id, filename, chunk_id, chunk_index, content,
                           file_type, page_number, section
                    FROM {self.TABLE_NAME}
                    WHERE document_id = :document_id ORDER BY chunk_index
                    """
                ),
                {"document_id": document_id},
            ).mappings().all()
        return [
            ChunkRecord(
                document_id=str(row["document_id"]),
                filename=row["filename"],
                chunk_id=row["chunk_id"],
                index=row["chunk_index"],
                content=row["content"],
                embedding={},
                file_type=row["file_type"],
                page_number=row["page_number"],
                section=row["section"],
            )
            for row in rows
        ]

    def delete_document(self, document_id: str) -> bool:
        with self._sql_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM documents WHERE document_id = :document_id"),
                {"document_id": document_id},
            ).first()
        if not exists:
            return False

        self._vector_store.delete(filter={"document_id": document_id})
        with self._sql_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM documents WHERE document_id = :document_id"),
                {"document_id": document_id},
            )
        return True

    def check_ready(self) -> None:
        with self._sql_engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()

    def search(self, question: str, top_k: int, embedder) -> list[dict]:
        del embedder
        dense_results = self._vector_store.similarity_search_with_relevance_scores(
            question, k=top_k
        )
        candidates: dict[str, dict] = {}
        for rank, (document, score) in enumerate(dense_results, start=1):
            candidates[document.metadata["chunk_id"]] = {
                "document_id": document.metadata["document_id"],
                "filename": document.metadata["filename"],
                "chunk_id": document.metadata["chunk_id"],
                "score": round(max(0.0, min(1.0, float(score))), 4),
                "content": document.page_content,
                "file_type": document.metadata.get("file_type", "txt"),
                "page_number": document.metadata.get("page_number"),
                "section": document.metadata.get("section"),
                "_rrf": 1 / (60 + rank),
            }

        with self._sql_engine.connect() as connection:
            lexical_rows = connection.execute(
                text(
                    f"""
                    SELECT document_id, filename, chunk_id, content,
                           file_type, page_number, section,
                           similarity(content, :question) AS lexical_score
                    FROM {self.TABLE_NAME}
                    WHERE similarity(content, :question) > 0
                    ORDER BY lexical_score DESC
                    LIMIT :top_k
                    """
                ),
                {"question": question, "top_k": top_k},
            ).mappings().all()

        for rank, row in enumerate(lexical_rows, start=1):
            chunk_id = row["chunk_id"]
            candidate = candidates.setdefault(
                chunk_id,
                {
                    "document_id": row["document_id"],
                    "filename": row["filename"],
                    "chunk_id": chunk_id,
                    "score": round(float(row["lexical_score"]), 4),
                    "content": row["content"],
                    "file_type": row["file_type"],
                    "page_number": row["page_number"],
                    "section": row["section"],
                    "_rrf": 0.0,
                },
            )
            candidate["_rrf"] += 1 / (60 + rank)

        ranked = sorted(candidates.values(), key=lambda item: item["_rrf"], reverse=True)
        for item in ranked:
            item.pop("_rrf")
        return ranked[:top_k]

    async def close(self) -> None:
        await self._pg_engine.close()
        self._sql_engine.dispose()

    @staticmethod
    def _document_info(row) -> dict:
        return {
            "document_id": str(row["document_id"]),
            "filename": row["filename"],
            "file_type": row["file_type"],
            "chunk_count": row["chunk_count"],
            "preview": row["preview"],
            "content_hash": row["content_hash"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

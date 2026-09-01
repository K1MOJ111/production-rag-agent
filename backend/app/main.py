from pathlib import Path

from fastapi import FastAPI, HTTPException

from .models import (
    AskRequest,
    AskResponse,
    ChunkInfo,
    DocumentInfo,
    LoadSamplesResponse,
    TextUploadRequest,
    UploadResponse,
)
from .services.document_service import DocumentService
from .services.mock_embedding_service import MockEmbeddingService
from .services.mock_llm_service import MockLLMService
from .services.prompt_service import build_prompt
from .services.vector_store import InMemoryVectorStore


app = FastAPI(
    title="RAG Demo API",
    description="A minimal enterprise document Q&A demo for interview practice.",
    version="0.1.0",
)

embedder = MockEmbeddingService()
store = InMemoryVectorStore()
document_service = DocumentService(store=store, embedder=embedder)
llm_service = MockLLMService()
MIN_SIMILARITY_SCORE = 0.1
LOW_CONFIDENCE_ANSWER = (
    "我没有在知识库中检索到足够相关的资料，"
    "所以不能基于企业文档回答这个问题。"
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/documents/upload", response_model=UploadResponse)
def upload_document(payload: TextUploadRequest) -> UploadResponse:
    try:
        document = document_service.add_text_document(
            filename=payload.filename,
            content=payload.content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return UploadResponse(
        document_id=document["document_id"],
        filename=document["filename"],
        chunk_count=document["chunk_count"],
        status="success",
    )


@app.post("/documents/load-samples", response_model=LoadSamplesResponse)
def load_samples() -> LoadSamplesResponse:
    sample_dir = Path(__file__).resolve().parents[2] / "sample_docs"
    documents = document_service.load_sample_documents(sample_dir)
    return LoadSamplesResponse(
        loaded_count=len(documents),
        documents=[DocumentInfo(**document) for document in documents],
    )


@app.get("/documents", response_model=list[DocumentInfo])
def list_documents() -> list[DocumentInfo]:
    return [DocumentInfo(**document) for document in store.list_documents()]


@app.get("/documents/{document_id}/chunks", response_model=list[ChunkInfo])
def list_document_chunks(document_id: str) -> list[ChunkInfo]:
    chunks = store.get_chunks(document_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="document not found")

    return [
        ChunkInfo(chunk_id=chunk.chunk_id, index=chunk.index, content=chunk.content)
        for chunk in chunks
    ]


@app.delete("/documents/{document_id}")
def delete_document(document_id: str) -> dict:
    deleted = store.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="document not found")

    return {"deleted": True, "document_id": document_id}


@app.post("/qa/ask", response_model=AskResponse)
def ask_question(payload: AskRequest) -> AskResponse:
    sources = store.search(
        question=payload.question,
        top_k=payload.top_k,
        embedder=embedder,
    )

    # A weak top result is not reliable evidence for a grounded answer.
    if not sources or sources[0]["score"] < MIN_SIMILARITY_SCORE:
        prompt = build_prompt(question=payload.question, sources=[])
        return AskResponse(
            answer=LOW_CONFIDENCE_ANSWER,
            sources=[],
            prompt=prompt,
            is_refused=True,
        )

    prompt = build_prompt(question=payload.question, sources=sources)
    answer = llm_service.generate_answer(
        question=payload.question,
        sources=sources,
        prompt=prompt,
    )

    return AskResponse(
        answer=answer,
        sources=sources,
        prompt=prompt,
        is_refused=False,
    )

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .models import (
    AskRequest,
    AskResponse,
    AgentConfirmRequest,
    AgentResponse,
    AgentRunRequest,
    ChunkInfo,
    DocumentInfo,
    LoadSamplesResponse,
    TextUploadRequest,
    UploadResponse,
)
from .config import Settings
from .services.document_service import DocumentService
from .services.mock_embedding_service import MockEmbeddingService
from .services.mock_llm_service import MockLLMService
from .services.prompt_service import build_prompt, has_valid_citations
from .services.vector_store import InMemoryVectorStore


settings = Settings.from_env()
if settings.rag_mode == "real":
    from .services.dashscope_embedding_service import DashScopeEmbeddingService
    from .services.deepseek_llm_service import DeepSeekLLMService
    from .services.dashscope_rerank_service import DashScopeRerankService
    from .services.postgres_vector_store import PostgresVectorStore
    from .services.agent_service import LangGraphAgentService

    embedder = DashScopeEmbeddingService(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    store = PostgresVectorStore(
        database_url=settings.database_url,
        embedding_service=embedder,
        vector_size=settings.embedding_dimension,
    )
    llm_service = DeepSeekLLMService(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
    )
    reranker = DashScopeRerankService(
        settings.dashscope_api_key,
        settings.dashscope_base_url,
        settings.rerank_model,
    )
else:
    embedder = MockEmbeddingService()
    store = InMemoryVectorStore()
    llm_service = MockLLMService()
    reranker = None

document_service = DocumentService(store=store, embedder=embedder)
MIN_SIMILARITY_SCORE = settings.min_similarity_score
LOW_CONFIDENCE_ANSWER = (
    "我没有在知识库中检索到足够相关的资料，"
    "所以不能基于企业文档回答这个问题。"
)

if settings.rag_mode == "real":
    def agent_knowledge_search(question: str) -> dict:
        candidates = store.search(question, 12, embedder)
        sources = reranker.rerank(question, candidates, 3)
        if not sources or sources[0]["score"] < MIN_SIMILARITY_SCORE:
            return {"found": False, "sources": []}
        return {
            "found": True,
            "sources": [
                {**source, "citation_id": index}
                for index, source in enumerate(sources, start=1)
            ],
        }

    agent_service = LangGraphAgentService(
        settings.deepseek_model, agent_knowledge_search, llm_service.client
    )
else:
    agent_service = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    close = getattr(store, "close", None)
    if close:
        await close()


app = FastAPI(
    title="RAG Demo API",
    description="Enterprise knowledge base API with mock and real RAG modes.",
    version="0.3.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": settings.rag_mode}


@app.post("/documents/upload", response_model=UploadResponse)
def upload_document(payload: TextUploadRequest) -> UploadResponse:
    try:
        document = document_service.add_text_document(
            filename=payload.filename,
            content=payload.content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="document indexing unavailable") from exc

    return UploadResponse(
        document_id=document["document_id"],
        filename=document["filename"],
        chunk_count=document["chunk_count"],
        status="success",
    )


@app.post("/documents/load-samples", response_model=LoadSamplesResponse)
def load_samples() -> LoadSamplesResponse:
    sample_dir = Path(__file__).resolve().parents[2] / "sample_docs"
    try:
        documents = document_service.load_sample_documents(sample_dir)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="document indexing unavailable") from exc
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
    try:
        raw_sources = store.search(
            question=payload.question,
            top_k=payload.top_k * 4,
            embedder=embedder,
        )
        if reranker:
            raw_sources = reranker.rerank(
                payload.question, raw_sources, payload.top_k
            )
        else:
            raw_sources = raw_sources[: payload.top_k]
    except Exception as exc:
        raise HTTPException(status_code=503, detail="retrieval unavailable") from exc
    sources = [
        {**source, "citation_id": index}
        for index, source in enumerate(raw_sources, start=1)
    ]

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
    try:
        answer = llm_service.generate_answer(
            question=payload.question,
            sources=sources,
            prompt=prompt,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="answer generation unavailable") from exc
    if not has_valid_citations(answer, sources):
        raise HTTPException(status_code=502, detail="answer citation validation failed")

    return AskResponse(
        answer=answer,
        sources=sources,
        prompt=prompt,
        is_refused=False,
    )


@app.post("/agent/run", response_model=AgentResponse)
def run_agent(payload: AgentRunRequest) -> AgentResponse:
    if not agent_service:
        raise HTTPException(status_code=503, detail="agent requires real mode")
    try:
        return AgentResponse(**agent_service.run(payload.thread_id, payload.message))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="agent unavailable") from exc


@app.post("/agent/confirm", response_model=AgentResponse)
def confirm_agent(payload: AgentConfirmRequest) -> AgentResponse:
    if not agent_service:
        raise HTTPException(status_code=503, detail="agent requires real mode")
    try:
        return AgentResponse(**agent_service.confirm(payload.thread_id, payload.approved))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="agent unavailable") from exc

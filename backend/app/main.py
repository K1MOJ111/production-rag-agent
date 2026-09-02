from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from .models import (
    AskRequest,
    AskResponse,
    AgentConfirmRequest,
    AgentAuditEntry,
    AgentResponse,
    AgentRunRequest,
    ChunkInfo,
    DocumentInfo,
    LoadSamplesResponse,
    TextUploadRequest,
    TokenResponse,
    UploadResponse,
)
from .config import Settings
from .services.document_service import DocumentService, MAX_UPLOAD_BYTES
from .services.mock_embedding_service import MockEmbeddingService
from .services.mock_llm_service import MockLLMService
from .services.prompt_service import build_prompt, has_valid_citations
from .services.vector_store import DuplicateDocumentError, InMemoryVectorStore
from .services.auth_service import AuthService, Principal, ROLES


logger = logging.getLogger("uvicorn.error")
settings = Settings.from_env()
auth_service = AuthService(
    settings.database_url if settings.rag_mode == "real" else None,
    settings.jwt_secret,
    settings.jwt_issuer,
    settings.jwt_audience,
    settings.jwt_expire_minutes,
)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")
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
        settings.deepseek_model,
        agent_knowledge_search,
        llm_service.client,
        settings.database_url,
    )
else:
    agent_service = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if agent_service:
        agent_service.close()
    close = getattr(store, "close", None)
    if close:
        await close()
    auth_service.close()


app = FastAPI(
    title="Enterprise RAG Agent API",
    description="Knowledge retrieval and controlled business Agent service.",
    version="0.7.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_request(request: Request, call_next):
    request_id = str(uuid4())
    started = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        logger.info(
            json.dumps(
                {
                    "event": "request_complete",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                }
            )
        )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": settings.rag_mode}


@app.get("/ready")
def ready() -> dict:
    checks = [getattr(store, "check_ready", None)]
    if agent_service:
        checks.append(agent_service.check_ready)
    try:
        for check in checks:
            if check:
                check()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {
        "status": "ready",
        "mode": settings.rag_mode,
        "database": "ok" if checks[0] else "not_required",
    }


def get_current_principal(token: str = Depends(oauth2_scheme)) -> Principal:
    try:
        return auth_service.get_principal(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail="could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_role(principal: Principal, minimum: str) -> None:
    if ROLES[principal.role] < ROLES[minimum]:
        raise HTTPException(status_code=403, detail="insufficient permissions")


@app.post("/auth/token", response_model=TokenResponse)
def login(
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
) -> TokenResponse:
    principal = auth_service.authenticate(form.username, form.password)
    if not principal:
        raise HTTPException(
            status_code=401,
            detail="incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=auth_service.create_access_token(principal))


@app.post("/documents/upload", response_model=UploadResponse)
def upload_document(
    payload: TextUploadRequest,
    principal: Principal = Depends(get_current_principal),
) -> UploadResponse:
    require_role(principal, "admin")
    try:
        document = document_service.add_text_document(
            filename=payload.filename,
            content=payload.content,
            allow_existing=False,
        )
    except DuplicateDocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="document indexing unavailable") from exc

    return UploadResponse(
        document_id=document["document_id"],
        filename=document["filename"],
        file_type=document["file_type"],
        chunk_count=document["chunk_count"],
        status="success",
    )


@app.post("/documents/upload-file", response_model=UploadResponse)
async def upload_document_file(
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
) -> UploadResponse:
    require_role(principal, "admin")
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if (
        not filename
        or len(filename) > 255
        or any(ord(character) < 32 for character in filename)
    ):
        raise HTTPException(status_code=400, detail="invalid filename")
    try:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        document = document_service.add_file_document(filename, data)
    except DuplicateDocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="document indexing unavailable") from exc
    finally:
        await file.close()

    return UploadResponse(
        document_id=document["document_id"],
        filename=document["filename"],
        file_type=document["file_type"],
        chunk_count=document["chunk_count"],
        status="success",
    )


@app.post("/documents/load-samples", response_model=LoadSamplesResponse)
def load_samples(
    principal: Principal = Depends(get_current_principal),
) -> LoadSamplesResponse:
    require_role(principal, "admin")
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
def list_documents(
    principal: Principal = Depends(get_current_principal),
) -> list[DocumentInfo]:
    require_role(principal, "viewer")
    return [DocumentInfo(**document) for document in store.list_documents()]


@app.get("/documents/{document_id}/chunks", response_model=list[ChunkInfo])
def list_document_chunks(
    document_id: str,
    principal: Principal = Depends(get_current_principal),
) -> list[ChunkInfo]:
    require_role(principal, "viewer")
    chunks = store.get_chunks(document_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="document not found")

    return [
        ChunkInfo(
            chunk_id=chunk.chunk_id,
            index=chunk.index,
            content=chunk.content,
            file_type=chunk.file_type,
            page_number=chunk.page_number,
            section=chunk.section,
        )
        for chunk in chunks
    ]


@app.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    principal: Principal = Depends(get_current_principal),
) -> dict:
    require_role(principal, "admin")
    deleted = store.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="document not found")

    return {"deleted": True, "document_id": document_id}


@app.post("/qa/ask", response_model=AskResponse)
def ask_question(
    payload: AskRequest,
    principal: Principal = Depends(get_current_principal),
) -> AskResponse:
    require_role(principal, "viewer")
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
def run_agent(
    payload: AgentRunRequest,
    principal: Principal = Depends(get_current_principal),
) -> AgentResponse:
    require_role(principal, "operator")
    if not agent_service:
        raise HTTPException(status_code=503, detail="agent requires real mode")
    try:
        return AgentResponse(
            **agent_service.run(principal.user_id, payload.thread_id, payload.message)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="agent unavailable") from exc


@app.post("/agent/confirm", response_model=AgentResponse)
def confirm_agent(
    payload: AgentConfirmRequest,
    principal: Principal = Depends(get_current_principal),
) -> AgentResponse:
    require_role(principal, "operator")
    if not agent_service:
        raise HTTPException(status_code=503, detail="agent requires real mode")
    try:
        return AgentResponse(
            **agent_service.confirm(
                principal.user_id, payload.thread_id, payload.approved
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="agent unavailable") from exc


@app.get("/agent/{thread_id}/audit", response_model=list[AgentAuditEntry])
def get_agent_audit(
    thread_id: str,
    principal: Principal = Depends(get_current_principal),
) -> list[AgentAuditEntry]:
    require_role(principal, "operator")
    if not agent_service:
        raise HTTPException(status_code=503, detail="agent requires real mode")
    try:
        return [
            AgentAuditEntry(**entry)
            for entry in agent_service.list_audit(
                principal.user_id,
                thread_id,
                allow_other=principal.role == "admin",
            )
        ]
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

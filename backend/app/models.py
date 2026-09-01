from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class TextUploadRequest(BaseModel):
    filename: str = Field(..., examples=["员工报销制度.txt"])
    content: str = Field(..., min_length=1)


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    chunk_count: int
    status: str


class DocumentInfo(BaseModel):
    document_id: str
    filename: str
    chunk_count: int
    preview: str
    content_hash: str | None = None
    status: str = "ready"
    created_at: datetime | None = None


class ChunkInfo(BaseModel):
    chunk_id: str
    index: int
    content: str


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["差旅报销需要准备哪些材料？"])
    top_k: int = Field(3, ge=1, le=10)


class SourceInfo(BaseModel):
    citation_id: int
    document_id: str
    filename: str
    chunk_id: str
    score: float
    content: str


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceInfo]
    prompt: str
    is_refused: bool = False


class LoadSamplesResponse(BaseModel):
    loaded_count: int
    documents: list[DocumentInfo]


class AgentRunRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    thread_id: str = Field(
        default_factory=lambda: uuid4().hex, min_length=1, max_length=128
    )


class AgentConfirmRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, max_length=128)
    approved: bool


class AgentResponse(BaseModel):
    thread_id: str
    status: Literal["completed", "needs_confirmation"]
    answer: str | None = None
    pending_action: dict | None = None
    used_tools: list[str] = Field(default_factory=list)


class AgentAuditEntry(BaseModel):
    event_id: int
    thread_id: str
    actor_id: str
    event_type: str
    status: str
    used_tools: list[str]
    details: dict
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"

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


class ChunkInfo(BaseModel):
    chunk_id: str
    index: int
    content: str


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["差旅报销需要准备哪些材料？"])
    top_k: int = Field(3, ge=1, le=10)


class SourceInfo(BaseModel):
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

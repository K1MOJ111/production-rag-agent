import re

from langchain_text_splitters import RecursiveCharacterTextSplitter


def clean_text(text: str) -> str:
    """Normalize whitespace while keeping readable line breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def split_into_chunks(text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
    cleaned = clean_text(text)
    if not cleaned:
        return []

    if chunk_size <= overlap:
        raise ValueError("chunk_size must be larger than overlap")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", "、", " ", ""],
    )
    return splitter.split_text(cleaned)

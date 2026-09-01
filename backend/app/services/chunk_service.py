import re


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

    chunks: list[str] = []
    step = chunk_size - overlap

    for start in range(0, len(cleaned), step):
        chunk = cleaned[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(cleaned):
            break

    return chunks

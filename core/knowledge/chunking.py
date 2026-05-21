"""Text chunking helpers for knowledge ingestion."""

from __future__ import annotations


class RecursiveTextChunker:
    """Split text into overlapping chunks while preferring natural separators."""

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 120) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be greater than or equal to 0")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, text: str) -> list[str]:
        source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not source.strip():
            return []

        chunks: list[str] = []
        start = 0
        text_len = len(source)
        while start < text_len:
            end = self._best_end(source, start)
            chunk = source[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= text_len:
                break
            next_start = max(0, end - self.chunk_overlap)
            if next_start <= start:
                next_start = end
            start = next_start
        return chunks

    def _best_end(self, text: str, start: int) -> int:
        hard_end = min(len(text), start + self.chunk_size)
        if hard_end >= len(text):
            return len(text)

        window = text[start:hard_end]
        for separator in ("\n\n", "\n", "\u3002", "\uff01", "\uff1f", ". ", "! ", "? ", " "):
            idx = window.rfind(separator)
            if idx > 0:
                if separator.isspace():
                    return start + idx
                return start + idx + len(separator)
        return hard_end

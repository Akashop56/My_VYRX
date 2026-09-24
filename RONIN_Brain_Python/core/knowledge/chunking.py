"""Deterministic bounded chunk construction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from core.knowledge.parsers import ParsedUnit


@dataclass(frozen=True)
class ChunkingConfig:
    """Character-based chunk settings.

    ``chunk_size`` is the normal maximum emitted size. ``overlap`` is copied
    from the end of one chunk to the start of the next. ``min_size`` applies
    to normal chunks; a final non-empty tail is retained even when shorter so
    no source text is silently discarded.
    """

    chunk_size: int = 1200
    overlap: int = 120
    min_size: int = 1
    max_size: int = 1200

    def __post_init__(self) -> None:
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if self.max_size < self.chunk_size:
            raise ValueError("max_size must be >= chunk_size")
        if self.overlap < 0 or self.overlap >= self.chunk_size:
            raise ValueError("overlap must be >= 0 and smaller than chunk_size")
        if self.min_size < 1 or self.min_size > self.chunk_size:
            raise ValueError("min_size must be between 1 and chunk_size")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    content: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    source_kind: str = "text"


def iter_chunks(
    units: Iterable[ParsedUnit],
    config: ChunkingConfig | None = None,
) -> Iterator[Chunk]:
    """Convert parser units to ordered, bounded chunks.

    The buffer is bounded to approximately ``chunk_size + unit_size`` and
    parser units are themselves bounded by the parser read size. No complete
    source file is accumulated.
    """
    cfg = config or ChunkingConfig()
    buffer = ""
    buffer_start: int | None = None
    buffer_line_start = 1
    buffer_kind = "text"
    ordinal = 0

    def emit(content: str, start: int, line_start: int, kind: str) -> Chunk:
        nonlocal ordinal
        chunk = Chunk(
            ordinal=ordinal,
            content=content,
            char_start=start,
            char_end=start + len(content),
            line_start=line_start,
            line_end=line_start + content.count("\n"),
            source_kind=kind,
        )
        ordinal += 1
        return chunk

    for unit in units:
        remaining = unit.text
        if not remaining:
            continue
        while remaining:
            if buffer_start is None:
                buffer_start = unit.char_start
                buffer_line_start = unit.line_start
                buffer_kind = unit.kind
            capacity = cfg.chunk_size - len(buffer)
            take = min(capacity, len(remaining))
            buffer += remaining[:take]
            remaining = remaining[take:]

            if len(buffer) < cfg.chunk_size:
                continue

            content = buffer[:cfg.chunk_size]
            prefix_len = cfg.chunk_size - cfg.overlap
            ready = emit(content, buffer_start, buffer_line_start, buffer_kind)
            # Hold no more than the overlap for the next window.  The
            # line/offset position advances by the non-overlap prefix.
            yield ready
            buffer = content[prefix_len:]
            buffer_start += prefix_len
            buffer_line_start += content[:prefix_len].count("\n")

    if buffer and buffer_start is not None:
        # A final tail is emitted rather than dropped. This is the only
        # permitted min_size exception and keeps source ordering lossless.
        yield emit(buffer, buffer_start, buffer_line_start, buffer_kind)

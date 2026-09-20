"""Data records for the local knowledge substrate."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChangeKind(str, Enum):
    NEW = "new"
    MODIFIED = "modified"
    DELETED = "deleted"
    UNCHANGED = "unchanged"
    UNSUPPORTED = "unsupported"


class IngestionStatus(str, Enum):
    INDEXED = "indexed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    DELETED = "deleted"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class FileObservation:
    """A bounded-memory observation of one source path."""

    path: str
    absolute_path: str
    size: int
    mtime_ns: int
    content_hash: str
    suffix: str
    change: ChangeKind
    parser_name: str | None = None
    parser_status: str | None = None
    parser_error: str | None = None


@dataclass(frozen=True)
class ScanReport:
    observations: tuple[FileObservation, ...] = ()
    deleted: tuple[FileObservation, ...] = ()

    @property
    def new(self) -> tuple[FileObservation, ...]:
        return tuple(item for item in self.observations if item.change == ChangeKind.NEW)

    @property
    def modified(self) -> tuple[FileObservation, ...]:
        return tuple(item for item in self.observations if item.change == ChangeKind.MODIFIED)

    @property
    def unchanged(self) -> tuple[FileObservation, ...]:
        return tuple(item for item in self.observations if item.change == ChangeKind.UNCHANGED)

    @property
    def unsupported(self) -> tuple[FileObservation, ...]:
        return tuple(item for item in self.observations if item.change == ChangeKind.UNSUPPORTED)


@dataclass(frozen=True)
class IngestionFileResult:
    path: str
    status: IngestionStatus
    change: ChangeKind
    chunks: int = 0
    error: str | None = None


@dataclass(frozen=True)
class IngestionReport:
    files: tuple[IngestionFileResult, ...] = ()

    @property
    def indexed(self) -> tuple[IngestionFileResult, ...]:
        return tuple(item for item in self.files if item.status == IngestionStatus.INDEXED)

    @property
    def failed(self) -> tuple[IngestionFileResult, ...]:
        return tuple(item for item in self.files if item.status == IngestionStatus.FAILED)

    @property
    def skipped(self) -> tuple[IngestionFileResult, ...]:
        return tuple(item for item in self.files if item.status == IngestionStatus.SKIPPED)

    @property
    def deleted(self) -> tuple[IngestionFileResult, ...]:
        return tuple(item for item in self.files if item.status == IngestionStatus.DELETED)

    @property
    def unsupported(self) -> tuple[IngestionFileResult, ...]:
        return tuple(item for item in self.files if item.status == IngestionStatus.UNSUPPORTED)


@dataclass(frozen=True)
class KnowledgeSearchResult:
    """Structured result returned by lexical, semantic, or hybrid retrieval."""

    chunk_id: int
    matched_chunk: str
    source_file: str
    location: dict[str, int]
    retrieval_method: str
    relevance: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "matched_chunk": self.matched_chunk,
            "source_file": self.source_file,
            "location": dict(self.location),
            "retrieval_method": self.retrieval_method,
            "relevance": self.relevance,
            "metadata": dict(self.metadata),
        }

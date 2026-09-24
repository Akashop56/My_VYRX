"""Persistent, local, provider-agnostic knowledge substrate.

The package is intentionally independent from the planner and execution
boundaries.  Its deterministic public operations are:

* :meth:`KnowledgeEngine.scan`
* :meth:`KnowledgeEngine.ingest`
* :meth:`KnowledgeEngine.search`

Large source files are streamed into bounded parser units and chunks; source
files are never modified by the engine.
"""

from core.knowledge.chunking import Chunk, ChunkingConfig, iter_chunks
from core.knowledge.embeddings import (
    EmbeddingAdapter,
    EmbeddingResult,
    NullEmbeddingAdapter,
    adapter_identity,
)
from core.knowledge.engine import KnowledgeEngine
from core.knowledge.models import (
    ChangeKind,
    FileObservation,
    IngestionReport,
    IngestionStatus,
    KnowledgeSearchResult,
    ScanReport,
)
from core.knowledge.parsers import (
    ParserError,
    ParserRegistry,
    ParsedUnit,
    UnsupportedFormatError,
    default_parser_registry,
)
from core.knowledge.watcher import IngestionWatcherConfig, KnowledgeIngestionWatcher

__all__ = [
    "ChangeKind",
    "Chunk",
    "ChunkingConfig",
    "EmbeddingAdapter",
    "EmbeddingResult",
    "FileObservation",
    "IngestionWatcherConfig",
    "KnowledgeIngestionWatcher",
    "IngestionReport",
    "IngestionStatus",
    "KnowledgeEngine",
    "KnowledgeSearchResult",
    "NullEmbeddingAdapter",
    "ParsedUnit",
    "ParserError",
    "ParserRegistry",
    "ScanReport",
    "UnsupportedFormatError",
    "adapter_identity",
    "default_parser_registry",
    "iter_chunks",
]

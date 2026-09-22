"""Phase 10 application integration for the local knowledge engine.

This module is the narrow adapter between the lifecycle-owned
:class:`KnowledgeEngine`, the capability registry, and the ReAct tool surface.
It does not alter indexing, chunking, FTS5, or semantic retrieval behavior.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    SemanticCapabilityType,
)
from core.knowledge.engine import KnowledgeEngine
from core.knowledge.models import IngestionReport, KnowledgeSearchResult, ScanReport

KNOWLEDGE_CAPABILITY_ID = "knowledge-local"
KNOWLEDGE_TOOL_NAME = "search_local_knowledge"


def knowledge_capability_descriptor(engine: KnowledgeEngine | None) -> CapabilityDescriptor:
    """Describe the local lexical/semantic knowledge implementation."""
    semantic_health = (
        engine.semantic_health_status().value
        if engine is not None else CapabilityHealth.UNAVAILABLE.value
    )
    return CapabilityDescriptor(
        id=KNOWLEDGE_CAPABILITY_ID,
        capability_type=SemanticCapabilityType.LOCAL_KNOWLEDGE_SEARCH,
        description="Persistent local SQLite FTS5 knowledge search",
        requires_internet=False,
        requires_auth=False,
        is_local=True,
        estimated_latency_ms=50,
        estimated_cost_tier="free",
        metadata={
            "engine": "KnowledgeEngine",
            # Preserve the Phase 9 default identity; modes are selected by
            # the engine/tool without changing LOCAL_KNOWLEDGE_SEARCH.
            "retrieval_method": "lexical_fts5",
            "retrieval_modes": ["lexical-only", "semantic-only", "hybrid"],
            "lexical_retrieval": "available_when_sqlite_fts5_is_healthy",
            "semantic_retrieval": semantic_health,
            "root": str(engine.root) if engine is not None else None,
        },
    )


def establish_knowledge_health(
    registry: Any,
    engine: KnowledgeEngine | None,
) -> CapabilityHealth:
    """Register the implementation and establish health from storage checks.

    Registration itself always starts at UNKNOWN. A successful health check is
    then recorded through the Registry's existing health policy rather than by
    mutating a descriptor directly.
    """
    registry.register_if_absent(knowledge_capability_descriptor(engine))
    if engine is None:
        registry.update_health(
            KNOWLEDGE_CAPABILITY_ID,
            success=False,
            failure_class=CanonicalFailureClass.UNKNOWN_FATAL,
        )
        return CapabilityHealth.UNAVAILABLE

    try:
        health = engine.health_status()
    except Exception:
        registry.update_health(
            KNOWLEDGE_CAPABILITY_ID,
            success=False,
            failure_class=CanonicalFailureClass.UNKNOWN_FATAL,
        )
        return CapabilityHealth.UNAVAILABLE
    if health == CapabilityHealth.AVAILABLE:
        registry.update_health(KNOWLEDGE_CAPABILITY_ID, success=True)
    elif health == CapabilityHealth.DEGRADED:
        registry.update_health(
            KNOWLEDGE_CAPABILITY_ID,
            success=False,
            failure_class=CanonicalFailureClass.DETERMINISTIC_ERROR,
        )
    else:
        registry.update_health(
            KNOWLEDGE_CAPABILITY_ID,
            success=False,
            failure_class=CanonicalFailureClass.UNKNOWN_FATAL,
        )
    current = registry.get(KNOWLEDGE_CAPABILITY_ID)
    return current.health if current is not None else CapabilityHealth.UNAVAILABLE


def knowledge_tool_schema() -> dict[str, Any]:
    """Return the context-bound search tool schema exposed to the ReAct loop."""
    return {
        "type": "function",
        "function": {
            "name": KNOWLEDGE_TOOL_NAME,
            "description": (
                "Search indexed local files and return concise relevant excerpts "
                "with source paths and locations using lexical, semantic, or hybrid retrieval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or a natural-language phrase to search locally.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of local excerpts to return.",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["lexical-only", "semantic-only", "hybrid"],
                        "description": "Retrieval mode; hybrid combines lexical and semantic rankings.",
                        "default": "lexical-only",
                    },
                },
                "required": ["query"],
            },
        },
    }


def _safe_relative_source(
    result: KnowledgeSearchResult,
    engine: KnowledgeEngine | None = None,
) -> str | None:
    """Return a normalized, indexed path below the configured root.

    The engine keeps the absolute source path for internal file access, while
    its result metadata carries the indexed relative path. Transport uses only
    that relative value and, when an engine is available, verifies it against
    the indexed-file table. Database paths and database-internal identifiers
    are never citation sources.
    """
    raw_value = (result.metadata or {}).get("relative_path")
    if not isinstance(raw_value, str):
        return None
    raw = raw_value
    if not raw or raw != raw.strip() or len(raw) > 512:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return None
    if raw.startswith(("/", "\\", "~")) or "://" in raw or ":" in raw:
        return None
    normalized_text = raw.replace("\\", "/")
    normalized = PurePosixPath(normalized_text)
    if normalized.is_absolute() or any(part in {"", ".", ".."} for part in normalized.parts):
        return None
    value = normalized.as_posix()
    lowered = value.casefold()
    database_suffixes = (
        ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm",
        ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm",
    )
    if lowered.endswith(database_suffixes):
        return None
    database_identifiers = {
        "sqlite_sequence", "knowledge_chunks", "knowledge_chunks_fts",
        "knowledge_files", "knowledge_embedding_metadata",
    }
    basename = lowered.rsplit("/", 1)[-1]
    if basename.isdigit() or basename in database_identifiers:
        return None
    if re.match(r"^(?:chunk|chunk_id|rowid|file_id|source_id)[_:-]?\d+$", basename):
        return None
    if lowered.startswith((
        "chunk:", "chunk_id:", "rowid:", "sqlite_",
        "knowledge_chunks:", "knowledge_files:",
        "knowledge_embedding_metadata:",
    )):
        return None

    if engine is not None:
        try:
            root = Path(engine.root).resolve()
            candidate = (root / Path(*normalized.parts)).resolve()
            candidate.relative_to(root)
            indexed = engine.file_metadata(value)
        except (AttributeError, OSError, ValueError, TypeError):
            return None
        if not indexed or str(indexed.get("parser_status", "")).casefold() != "indexed":
            return None
        try:
            if int(indexed.get("chunk_count", 0)) <= 0:
                return None
        except (TypeError, ValueError):
            return None
    return value if value and value != "." else None


def _format_result(
    result: KnowledgeSearchResult,
    ordinal: int,
    engine: KnowledgeEngine | None = None,
) -> str:
    location = result.location or {}
    lines = location.get("line_start")
    end_line = location.get("line_end")
    char_start = location.get("char_start")
    char_end = location.get("char_end")
    valid_location = all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (lines, end_line, char_start, char_end)
    ) and lines >= 1 and end_line >= lines and char_start >= 0 and char_end >= char_start
    if not valid_location:
        return f"[{ordinal}] source unavailable\n{result.matched_chunk.strip()}"
    line_label = str(lines) if lines == end_line else f"{lines}-{end_line}"
    source = _safe_relative_source(result, engine)
    # Keep citation locations useful without exposing SQLite ids, absolute
    # roots, relevance scores, or embedding/RRF implementation details. An
    # unsafe source is not formatted as a citation at all.
    if source is None:
        return f"[{ordinal}] source unavailable\n{result.matched_chunk.strip()}"
    return (
        f"[{ordinal}] {source} "
        f"(lines {line_label}, chars {char_start}-{char_end})\n"
        f"{result.matched_chunk.strip()}"
    )


def format_knowledge_results(
    query: str,
    results: list[KnowledgeSearchResult],
    *,
    engine: KnowledgeEngine | None = None,
) -> str:
    """Format structured search results without exposing engine diagnostics."""
    if not results:
        return f"No local knowledge matches found for: {query.strip()}"
    body = "\n\n".join(
        _format_result(result, index, engine)
        for index, result in enumerate(results, 1)
    )
    return f"Local knowledge results ({len(results)}) for: {query.strip()}\n{body}"


@dataclass(frozen=True)
class KnowledgeSearchExecution:
    """Safe text plus verified retrieval metadata for the stream boundary."""

    text: str
    metadata: dict[str, Any]


def _parse_knowledge_arguments(arguments: dict[str, Any] | str) -> tuple[str, int, str]:
    if isinstance(arguments, str):
        arguments = json.loads(arguments) if arguments.strip() else {}
    if not isinstance(arguments, dict):
        raise TypeError("knowledge search arguments must be an object")
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("knowledge search query is required")
    limit = max(1, min(20, int(arguments.get("limit", 5))))
    mode = str(arguments.get("mode") or "lexical-only").strip().casefold()
    if mode not in {"lexical-only", "semantic-only", "hybrid"}:
        raise ValueError("knowledge search mode must be lexical-only, semantic-only, or hybrid")
    return query, limit, mode


def execute_knowledge_search_with_metadata(
    engine: KnowledgeEngine | None,
    arguments: dict[str, Any] | str,
) -> KnowledgeSearchExecution:
    """Execute search and keep actual retrieval separate from requested mode."""
    query, limit, mode = _parse_knowledge_arguments(arguments)
    if engine is None:
        raise RuntimeError("local knowledge engine is unavailable")
    try:
        results = engine.search(query, limit=limit, mode=mode)
    except TypeError as exc:
        # Keep the narrow pre-mode test/injection seam compatible; the
        # lifecycle-owned KnowledgeEngine accepts and records the mode above.
        if "mode" not in str(exc).casefold():
            raise
        results = engine.search(query, limit=limit)
    methods = {str(item.retrieval_method).strip() for item in results if item.retrieval_method}
    metadata: dict[str, Any] = {
        "requested_mode": mode,
        "semantic_capability": SemanticCapabilityType.LOCAL_KNOWLEDGE_SEARCH.value,
        "locality": "local",
    }
    # An empty semantic search, for example, does not prove that semantic
    # retrieval actually ran. Only expose a method verified by returned data.
    if len(methods) == 1:
        metadata["retrieval_method"] = next(iter(methods))
    return KnowledgeSearchExecution(
        format_knowledge_results(query, results, engine=engine),
        metadata,
    )


def execute_knowledge_search(
    engine: KnowledgeEngine | None,
    arguments: dict[str, Any] | str,
) -> str:
    """Compatibility wrapper for the existing non-streaming tool boundary."""
    return execute_knowledge_search_with_metadata(engine, arguments).text


def scan_knowledge(engine: KnowledgeEngine) -> ScanReport:
    """Administrative deterministic scan trigger."""
    return engine.scan()


def ingest_knowledge(engine: KnowledgeEngine) -> IngestionReport:
    """Administrative deterministic ingestion trigger."""
    return engine.ingest()


def scan_report_dict(report: ScanReport) -> dict[str, Any]:
    return {
        "observations": [
            {
                "path": item.path,
                "change": item.change.value,
                "size": item.size,
                "mtime_ns": item.mtime_ns,
                "parser": item.parser_name,
                "status": item.parser_status,
                "error": item.parser_error,
            }
            for item in report.observations
        ],
        "deleted": [item.path for item in report.deleted],
    }


def ingestion_report_dict(report: IngestionReport) -> dict[str, Any]:
    return {
        "files": [
            {
                "path": item.path,
                "status": item.status.value,
                "change": item.change.value,
                "chunks": item.chunks,
                "error": item.error,
            }
            for item in report.files
        ]
    }

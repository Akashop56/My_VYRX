"""Phase 10 application integration for the local knowledge engine.

This module is the narrow adapter between the lifecycle-owned
:class:`KnowledgeEngine`, the capability registry, and the ReAct tool surface.
It does not alter indexing, chunking, FTS5, or semantic retrieval behavior.
"""
from __future__ import annotations

import json
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


def _format_result(result: KnowledgeSearchResult, ordinal: int) -> str:
    location = result.location
    lines = location.get("line_start", 0)
    end_line = location.get("line_end", lines)
    line_label = str(lines) if lines == end_line else f"{lines}-{end_line}"
    return (
        f"[{ordinal}] chunk {result.chunk_id} — {result.source_file} "
        f"(lines {line_label}, chars {location.get('char_start', 0)}-"
        f"{location.get('char_end', 0)})\n"
        f"{result.matched_chunk.strip()}"
    )


def format_knowledge_results(query: str, results: list[KnowledgeSearchResult]) -> str:
    """Format structured search results without exposing engine diagnostics."""
    if not results:
        return f"No local knowledge matches found for: {query.strip()}"
    body = "\n\n".join(_format_result(result, index) for index, result in enumerate(results, 1))
    return f"Local knowledge results ({len(results)}) for: {query.strip()}\n{body}"


def execute_knowledge_search(
    engine: KnowledgeEngine | None,
    arguments: dict[str, Any] | str,
) -> str:
    """Execute the read-only search operation for the existing tool boundary."""
    if isinstance(arguments, str):
        arguments = json.loads(arguments) if arguments.strip() else {}
    if not isinstance(arguments, dict):
        raise TypeError("knowledge search arguments must be an object")
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("knowledge search query is required")
    if engine is None:
        raise RuntimeError("local knowledge engine is unavailable")
    limit = max(1, min(20, int(arguments.get("limit", 5))))
    mode = str(arguments.get("mode") or "lexical-only").strip().casefold()
    if mode not in {"lexical-only", "semantic-only", "hybrid"}:
        raise ValueError("knowledge search mode must be lexical-only, semantic-only, or hybrid")
    return format_knowledge_results(query, engine.search(query, limit=limit, mode=mode))


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

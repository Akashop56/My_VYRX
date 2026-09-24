"""Provider-agnostic, local-first embedding contracts.

The knowledge engine owns persistence and compatibility checks; adapters only
provide vectors. No vendor, cloud API, or model filename is selected here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class EmbeddingResult:
    """An adapter result suitable for versioned persistence."""

    vector: tuple[float, ...]
    adapter_name: str
    model_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding_version: str = "unknown"

    @property
    def dimensions(self) -> int:
        return len(self.vector)


class EmbeddingAdapter(Protocol):
    """Minimal synchronous adapter; provider policy stays outside the engine."""

    adapter_name: str
    model_name: str | None
    embedding_version: str

    def embed(self, text: str) -> EmbeddingResult:
        """Return one vector or raise if the backend is unavailable."""
        ...


@dataclass(frozen=True)
class NullEmbeddingAdapter:
    """Explicitly unavailable adapter used to document lexical-only operation."""

    adapter_name: str = "none"
    model_name: str | None = None
    embedding_version: str = "none"
    available: bool = False

    def embed(self, text: str) -> EmbeddingResult:
        raise RuntimeError("semantic embedding backend is unavailable")


def adapter_identity(adapter: EmbeddingAdapter) -> tuple[str, str | None, str]:
    """Return the versioned embedding-space identity for an adapter."""
    return (
        str(getattr(adapter, "adapter_name", type(adapter).__name__)),
        getattr(adapter, "model_name", None),
        str(getattr(adapter, "embedding_version", getattr(adapter, "version", "unknown"))),
    )


def normalize_embedding_result(
    result: EmbeddingResult | Sequence[float],
    adapter: EmbeddingAdapter,
) -> EmbeddingResult:
    """Accept a vector from a simple adapter while keeping identity explicit."""
    if isinstance(result, EmbeddingResult):
        return result
    adapter_name, model_name, version = adapter_identity(adapter)
    return EmbeddingResult(
        vector=tuple(float(value) for value in result),
        adapter_name=adapter_name,
        model_name=model_name,
        embedding_version=version,
    )

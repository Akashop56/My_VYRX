"""Provider-agnostic embedding contracts with no built-in backend."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class EmbeddingResult:
    """An adapter result suitable for persistence as metadata."""

    vector: tuple[float, ...]
    adapter_name: str
    model_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def dimensions(self) -> int:
        return len(self.vector)


class EmbeddingAdapter(Protocol):
    """Minimal synchronous adapter; provider policy lives outside this package."""

    adapter_name: str
    model_name: str | None

    def embed(self, text: str) -> EmbeddingResult:
        """Return one vector or raise if the backend is unavailable."""
        ...


@dataclass(frozen=True)
class NullEmbeddingAdapter:
    """Explicitly unavailable adapter used to document lexical-only operation."""

    adapter_name: str = "none"
    model_name: str | None = None

    def embed(self, text: str) -> EmbeddingResult:
        raise RuntimeError("semantic embedding backend is unavailable")


def normalize_embedding_result(result: EmbeddingResult | Sequence[float], adapter: EmbeddingAdapter) -> EmbeddingResult:
    """Accept a vector from a simple adapter while keeping identity explicit."""
    if isinstance(result, EmbeddingResult):
        return result
    return EmbeddingResult(
        vector=tuple(float(value) for value in result),
        adapter_name=str(getattr(adapter, "adapter_name", type(adapter).__name__)),
        model_name=getattr(adapter, "model_name", None),
    )

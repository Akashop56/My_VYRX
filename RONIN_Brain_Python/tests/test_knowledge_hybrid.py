"""Deterministic Phase 13 hybrid retrieval tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.knowledge import ChunkingConfig, EmbeddingResult, KnowledgeEngine


class FakeEmbeddingAdapter:
    adapter_name = "fake-local"
    model_name = "fake-test-space"

    def __init__(self, version: str = "v1", *, fail: bool = False, batch: bool = False):
        self.embedding_version = version
        self.fail = fail
        self.batch = batch
        self.calls: list[str] = []
        self.batch_sizes: list[int] = []

    @staticmethod
    def _vector(text: str) -> tuple[float, float]:
        lowered = text.casefold()
        if (
            "semantic-only" in lowered
            or lowered.strip() in {"concept query", "semantic query"}
            or ("target" in lowered and "concept" in lowered)
        ):
            return (1.0, 0.0)
        if "shared concept" in lowered:
            return (0.95, 0.05)
        return (0.0, 1.0)

    def embed(self, text: str) -> EmbeddingResult:
        if self.fail:
            raise RuntimeError("synthetic embedding backend failure")
        self.calls.append(text)
        return EmbeddingResult(
            vector=self._vector(text),
            adapter_name=self.adapter_name,
            model_name=self.model_name,
            embedding_version=self.embedding_version,
            metadata={"test": True},
        )

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        if not self.batch:
            return [self.embed(text) for text in texts]
        self.batch_sizes.append(len(texts))
        return [self.embed(text) for text in texts]


class FailingBatchAdapter(FakeEmbeddingAdapter):
    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        self.batch_sizes.append(len(texts))
        raise RuntimeError("synthetic batch failure")


class KnowledgeHybridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "knowledge"
        self.root.mkdir()
        self.db = Path(self.tempdir.name) / "knowledge.sqlite"
        self.config = ChunkingConfig(chunk_size=200, overlap=0, min_size=1, max_size=200)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def engine(self, adapter, **kwargs) -> KnowledgeEngine:
        return KnowledgeEngine(
            self.root,
            db_path=self.db,
            embedding_adapter=adapter,
            chunking=self.config,
            **kwargs,
        )

    def test_chunk_generation_and_versioned_persistence(self):
        (self.root / "semantic.txt").write_text("semantic-only concept", encoding="utf-8")
        adapter = FakeEmbeddingAdapter()
        engine = self.engine(adapter)
        report = engine.ingest()

        self.assertEqual(len(report.indexed), 1)
        row = engine._db.execute(
            "SELECT adapter_name, model_name, embedding_version, dimensions, status, vector_json "
            "FROM knowledge_embedding_metadata"
        ).fetchone()
        self.assertEqual(row["adapter_name"], "fake-local")
        self.assertEqual(row["model_name"], "fake-test-space")
        self.assertEqual(row["embedding_version"], "v1")
        self.assertEqual(row["dimensions"], 2)
        self.assertEqual(row["status"], "available")
        self.assertIsNotNone(row["vector_json"])
        engine.close()

    def test_hybrid_rrf_combines_candidates_and_deduplicates_chunk_identity(self):
        (self.root / "lexical.txt").write_text("lexical-only target", encoding="utf-8")
        (self.root / "semantic.txt").write_text("semantic-only concept", encoding="utf-8")
        (self.root / "shared.txt").write_text("shared concept target", encoding="utf-8")
        engine = self.engine(FakeEmbeddingAdapter())
        engine.ingest()

        lexical = engine.search("target", mode="lexical-only", limit=10)
        semantic = engine.search("concept query", mode="semantic-only", limit=10)
        hybrid = engine.search("target concept", mode="hybrid", limit=10)

        self.assertTrue(lexical)
        self.assertTrue(semantic)
        self.assertEqual(semantic[0].retrieval_method, "semantic_cosine")
        self.assertTrue(hybrid)
        self.assertEqual(len({item.chunk_id for item in hybrid}), len(hybrid))
        self.assertTrue(all(item.retrieval_method == "hybrid_rrf" for item in hybrid))
        self.assertTrue(all("rrf_k" in item.metadata for item in hybrid))
        self.assertTrue(any(item.metadata["lexical_rank"] is not None for item in hybrid))
        self.assertTrue(any(item.metadata["semantic_rank"] is not None for item in hybrid))
        engine.close()

    def test_same_chunk_found_by_both_sources_is_returned_once(self):
        (self.root / "shared.txt").write_text("shared concept target", encoding="utf-8")
        engine = self.engine(FakeEmbeddingAdapter())
        engine.ingest()

        results = engine.search("shared concept", mode="hybrid", limit=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].retrieval_method, "hybrid_rrf")
        self.assertIsNotNone(results[0].metadata["lexical_rank"])
        self.assertIsNotNone(results[0].metadata["semantic_rank"])
        engine.close()

    def test_embedding_failure_leaves_fts_fully_functional(self):
        (self.root / "lexical.txt").write_text("lexical survives embedding failure", encoding="utf-8")
        adapter = FakeEmbeddingAdapter(fail=True)
        engine = self.engine(adapter)
        report = engine.ingest()

        self.assertEqual(len(report.indexed), 1)
        self.assertTrue(engine.search("lexical", mode="lexical-only"))
        self.assertEqual(engine.search("lexical", mode="semantic-only"), [])
        status = engine._db.execute(
            "SELECT status, error FROM knowledge_embedding_metadata"
        ).fetchone()
        self.assertEqual(status["status"], "unavailable")
        self.assertIn("synthetic embedding", status["error"])
        engine.close()

    def test_unchanged_chunks_are_not_reembedded(self):
        (self.root / "stable.txt").write_text("stable lexical content", encoding="utf-8")
        adapter = FakeEmbeddingAdapter()
        engine = self.engine(adapter)
        engine.ingest()
        calls_after_first = len(adapter.calls)
        second = engine.ingest()

        self.assertEqual(len(second.skipped), 1)
        self.assertEqual(len(adapter.calls), calls_after_first)
        engine.close()

    def test_version_change_reembeds_and_old_space_is_rejected(self):
        (self.root / "versioned.txt").write_text("versioned semantic content", encoding="utf-8")
        first_adapter = FakeEmbeddingAdapter("v1")
        first = self.engine(first_adapter)
        first.ingest()
        first.close()

        second_adapter = FakeEmbeddingAdapter("v2")
        second = self.engine(second_adapter)
        second.ingest()
        self.assertGreater(len(second_adapter.calls), 0)
        row = second._db.execute(
            "SELECT DISTINCT embedding_version FROM knowledge_embedding_metadata"
        ).fetchone()
        self.assertEqual(row["embedding_version"], "v2")
        second.close()

        incompatible = self.engine(FakeEmbeddingAdapter("v1"))
        self.assertEqual(incompatible.search("versioned", mode="semantic-only"), [])
        incompatible.close()

    def test_large_data_embedding_batches_are_bounded(self):
        (self.root / "large.txt").write_text(" ".join(f"token-{i}" for i in range(40)), encoding="utf-8")
        adapter = FakeEmbeddingAdapter(batch=True)
        engine = self.engine(adapter, batch_size=2)
        engine.ingest()

        self.assertTrue(adapter.batch_sizes)
        self.assertLessEqual(max(adapter.batch_sizes), 2)
        self.assertEqual(engine.embedding_metadata_count(), engine.chunk_count("large.txt"))
        engine.close()


if __name__ == "__main__":
    unittest.main()

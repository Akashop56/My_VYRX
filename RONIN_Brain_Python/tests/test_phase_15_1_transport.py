"""Focused Phase 15.1 transport tests."""
from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from core.capabilities import CapabilityDescriptor, SemanticCapabilityType
from core.knowledge.integration import (
    _safe_relative_source,
    execute_knowledge_search_with_metadata,
    format_knowledge_results,
)
from core.knowledge.models import KnowledgeSearchResult
from core.planner import _capability_transport_metadata, _observation, _tool_call
from core.streaming import AgentStreamer, bind_stream, unbind_stream


class Phase151TransportTests(unittest.TestCase):
    def test_citation_transport_uses_relative_metadata_not_absolute_source(self):
        result = KnowledgeSearchResult(
            chunk_id=99,
            matched_chunk="safe excerpt",
            source_file="/private/knowledge/notes.md",
            location={"line_start": 2, "line_end": 3, "char_start": 0, "char_end": 12},
            retrieval_method="lexical_fts5",
            relevance=0.5,
            metadata={"relative_path": "notes/project.md"},
        )
        rendered = format_knowledge_results("project", [result])
        self.assertIn("source unavailable", rendered)
        self.assertNotIn("notes/project.md", rendered)
        self.assertNotIn("/private/knowledge", rendered)
        self.assertNotIn("chunk 99", rendered)

    def test_citation_requires_verified_indexed_engine_source(self):
        result = KnowledgeSearchResult(
            chunk_id=1,
            matched_chunk="safe excerpt",
            source_file="ignored absolute source",
            location={"line_start": 2, "line_end": 3, "char_start": 0, "char_end": 12},
            retrieval_method="lexical_fts5",
            relevance=0.5,
            metadata={"relative_path": "notes/project.md"},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes").mkdir()
            (root / "notes" / "project.md").write_text("indexed", encoding="utf-8")

            class Engine:
                def __init__(self, indexed):
                    self.root = root
                    self.indexed = indexed

                def file_metadata(self, path):
                    return self.indexed.get(path)

            verified = Engine({"notes/project.md": {"parser_status": "indexed", "chunk_count": 1}})
            self.assertEqual(_safe_relative_source(result, verified), "notes/project.md")
            self.assertIn(
                "notes/project.md",
                format_knowledge_results("project", [result], engine=verified),
            )
            self.assertIsNone(_safe_relative_source(result))
            self.assertIn(
                "source unavailable",
                format_knowledge_results("project", [result]),
            )

            unindexed = Engine({"notes/project.md": {"parser_status": "failed", "chunk_count": 1}})
            self.assertIsNone(_safe_relative_source(result, unindexed))

            outside = root.parent / "outside.md"
            outside.write_text("outside", encoding="utf-8")
            (root / "link.md").symlink_to(outside)
            outside_result = replace(result, metadata={"relative_path": "link.md"})
            indexed_outside = Engine({"link.md": {"parser_status": "indexed", "chunk_count": 1}})
            self.assertIsNone(_safe_relative_source(outside_result, indexed_outside))

    def test_retrieval_metadata_reports_verified_fallback_method(self):
        result = KnowledgeSearchResult(
            chunk_id=1,
            matched_chunk="lexical fallback",
            source_file="/private/root/a.md",
            location={"line_start": 1, "line_end": 1, "char_start": 0, "char_end": 16},
            retrieval_method="lexical_fts5",
            relevance=1.0,
            metadata={"relative_path": "a.md"},
        )

        class Engine:
            def search(self, query, limit, mode):
                self.requested = (query, limit, mode)
                return [result]

        execution = execute_knowledge_search_with_metadata(Engine(), {"query": "x", "mode": "hybrid"})
        self.assertEqual(execution.metadata["requested_mode"], "hybrid")
        self.assertEqual(execution.metadata["retrieval_method"], "lexical_fts5")
        self.assertNotIn("hybrid", execution.metadata["retrieval_method"])

    def test_locality_is_authoritative_descriptor_metadata_not_a_tool_guess(self):
        local = CapabilityDescriptor(
            id="knowledge-local",
            capability_type=SemanticCapabilityType.LOCAL_KNOWLEDGE_SEARCH,
            description="local",
            requires_internet=False,
            requires_auth=False,
            is_local=True,
        )
        remote = CapabilityDescriptor(
            id="web-search",
            capability_type=SemanticCapabilityType.WEB_RETRIEVAL,
            description="web",
            requires_internet=True,
            requires_auth=False,
            is_local=False,
        )
        self.assertEqual(
            _capability_transport_metadata(local),
            {"semantic_capability": "local_knowledge_search", "locality": "local"},
        )
        self.assertEqual(
            _capability_transport_metadata(remote),
            {"semantic_capability": "web_retrieval", "locality": "remote"},
        )
        self.assertEqual(_capability_transport_metadata(None), {})

    def test_tool_identity_transports_call_id_and_attempt(self):
        recorder = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        token = bind_stream(streamer)
        try:
            _tool_call(
                2,
                "search",
                {"query": "x"},
                device=False,
                call_id="call-7",
                attempt=3,
            )
            _observation(
                2,
                "search",
                False,
                "failed",
                12,
                call_id="call-7",
                attempt=3,
            )
        finally:
            unbind_stream(token)
        self.assertEqual(
            [(event.type, event.data["call_id"], event.data["attempt"]) for event in recorder],
            [("tool_call", "call-7", 3), ("observation", "call-7", 3)],
        )

    def test_producer_locality_cannot_override_or_supply_authority(self):
        local = CapabilityDescriptor(
            id="local",
            capability_type=SemanticCapabilityType.LOCAL_KNOWLEDGE_SEARCH,
            description="local",
            is_local=True,
        )
        remote = CapabilityDescriptor(
            id="remote",
            capability_type=SemanticCapabilityType.WEB_RETRIEVAL,
            description="remote",
            is_local=False,
        )
        producer_local = {"locality": "local", "retrieval_method": "lexical_fts5"}
        producer_remote = {"locality": "remote", "retrieval_method": "lexical_fts5"}
        self.assertEqual(
            _capability_transport_metadata(local, producer_remote)["locality"],
            "local",
        )
        self.assertEqual(
            _capability_transport_metadata(remote, producer_local)["locality"],
            "remote",
        )
        self.assertNotIn("locality", _capability_transport_metadata(None, producer_local))
        self.assertNotIn("locality", _capability_transport_metadata(None, producer_remote))


if __name__ == "__main__":
    unittest.main()

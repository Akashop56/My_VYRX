"""Focused Phase 15.1 transport tests."""
from __future__ import annotations

import unittest

from core.capabilities import CapabilityDescriptor, SemanticCapabilityType
from core.knowledge.integration import (
    execute_knowledge_search_with_metadata,
    format_knowledge_results,
)
from core.knowledge.models import KnowledgeSearchResult
from core.planner import _capability_transport_metadata


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
        self.assertIn("notes/project.md", rendered)
        self.assertNotIn("/private/knowledge", rendered)
        self.assertNotIn("chunk 99", rendered)

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


if __name__ == "__main__":
    unittest.main()

"""Focused tests for the isolated Phase 9 local knowledge substrate."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.knowledge import (
    ChangeKind,
    ChunkingConfig,
    KnowledgeEngine,
    ParsedUnit,
    ParserRegistry,
)
from core.knowledge.parsers import ParserError, TextParser


def _minimal_pdf(text: str) -> bytes:
    """Build a tiny valid PDF with one extractable Helvetica text object."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


class KnowledgeEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "brain"
        self.db = Path(self.temp.name) / "knowledge.sqlite3"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def engine(self, **kwargs):
        options = {
            "db_path": self.db,
            "chunking": ChunkingConfig(chunk_size=32, overlap=5, min_size=4, max_size=32),
        }
        options.update(kwargs)
        return KnowledgeEngine(self.root, **options)

    def test_new_unchanged_modified_and_deleted_lifecycle(self):
        source = self.root / "notes.md"
        source.write_text("alpha local knowledge\n", encoding="utf-8")
        engine = self.engine()

        first_scan = engine.scan()
        self.assertEqual(first_scan.new[0].change, ChangeKind.NEW)
        first = engine.ingest(first_scan)
        self.assertEqual(len(first.indexed), 1)
        first_count = engine.chunk_count("notes.md")
        first_hash = engine.file_metadata("notes.md")["indexed_hash"]

        unchanged = engine.scan()
        self.assertEqual(len(unchanged.unchanged), 1)
        skipped = engine.ingest(unchanged)
        self.assertEqual(len(skipped.skipped), 1)
        self.assertEqual(engine.chunk_count("notes.md"), first_count)

        source.write_text("beta modified knowledge\n", encoding="utf-8")
        modified = engine.scan()
        self.assertEqual(len(modified.modified), 1)
        engine.ingest(modified)
        metadata = engine.file_metadata("notes.md")
        self.assertNotEqual(metadata["indexed_hash"], first_hash)
        self.assertEqual(engine.search("beta")[0].matched_chunk.find("beta") >= 0, True)
        self.assertEqual(engine.search("alpha"), [])

        source.unlink()
        deleted = engine.scan()
        self.assertEqual(len(deleted.deleted), 1)
        deletion = engine.ingest(deleted)
        self.assertEqual(len(deletion.deleted), 1)
        self.assertEqual(engine.chunk_count("notes.md"), 0)
        self.assertEqual(engine.search("beta"), [])
        engine.close()

    def test_utf8_chunk_boundaries_are_ordered_and_bounded(self):
        text = "😀 alpha\nbeta café\ngamma delta\n"
        (self.root / "utf8.txt").write_text(text, encoding="utf-8")
        engine = self.engine(chunking=ChunkingConfig(chunk_size=10, overlap=2, min_size=3, max_size=10))
        report = engine.ingest()
        self.assertEqual(len(report.indexed), 1)
        rows = engine._db.execute(
            "SELECT ordinal, content, char_start, char_end FROM knowledge_chunks WHERE file_path=? ORDER BY ordinal",
            ("utf8.txt",),
        ).fetchall()
        self.assertGreater(len(rows), 2)
        self.assertEqual([row["ordinal"] for row in rows], list(range(len(rows))))
        self.assertTrue(all(0 < len(row["content"]) <= 10 for row in rows))
        self.assertEqual(rows[0]["char_start"], 0)
        self.assertTrue(all(rows[index]["char_start"] < rows[index + 1]["char_start"]
                            for index in range(len(rows) - 1)))
        self.assertIn("café", " ".join(row["content"] for row in rows))

    def test_pdf_parser_indexes_page_text(self):
        (self.root / "manual.pdf").write_bytes(_minimal_pdf("PDF local phrase"))
        engine = self.engine()
        report = engine.ingest()
        self.assertEqual(len(report.indexed), 1)
        results = engine.search("PDF phrase")
        self.assertTrue(results)
        self.assertEqual(results[0].retrieval_method, "lexical_fts5")
        self.assertIn("manual.pdf", results[0].source_file)

    def test_unsupported_file_is_recorded_and_isolated(self):
        (self.root / "binary.bin").write_bytes(b"not a configured parser")
        (self.root / "known.txt").write_text("known parser content", encoding="utf-8")
        engine = self.engine()
        report = engine.ingest()
        self.assertIn("binary.bin", {item.path for item in report.unsupported})
        self.assertIn("known.txt", {item.path for item in report.indexed})
        self.assertEqual(engine.file_metadata("binary.bin")["parser_status"], "unsupported")
        self.assertEqual(len(engine.scan().unchanged), 2)

    def test_json_and_csv_parsers_are_streaming_formats(self):
        (self.root / "config.json").write_text(
            '{"technical_term": "json-stream marker", "items": [1, 2]}',
            encoding="utf-8",
        )
        (self.root / "rows.csv").write_text(
            "name,description\\nalpha,csv-stream marker\\nbeta,second row\\n",
            encoding="utf-8",
        )
        engine = self.engine()
        report = engine.ingest()
        self.assertEqual({item.path for item in report.indexed}, {"config.json", "rows.csv"})
        self.assertTrue(engine.search("json-stream"))
        self.assertTrue(engine.search("csv-stream"))

    def test_parser_failure_isolated_and_modified_index_rolls_back(self):
        valid = self.root / "valid.txt"
        corrupt = self.root / "broken.json"
        valid.write_text("old stable marker", encoding="utf-8")
        corrupt.write_text("{ not valid json", encoding="utf-8")
        engine = self.engine()

        first = engine.ingest()
        self.assertEqual(len(first.indexed), 1)
        self.assertEqual(len(first.failed), 1)
        self.assertTrue(engine.search("stable"))

        valid.write_text("new replacement marker", encoding="utf-8")
        second = engine.ingest()
        self.assertIn("valid.txt", {item.path for item in second.indexed})
        self.assertIn("broken.json", {item.path for item in second.failed})
        # The corrupt JSON fails without preventing the independent text file
        # from being indexed.
        self.assertEqual(engine.file_metadata("broken.json")["parser_status"], "failed")
        self.assertTrue(engine.search("replacement"))

    def test_failure_recording_error_does_not_abort_later_files(self):
        bad = self.root / "broken.json"
        good = self.root / "valid.txt"
        third = self.root / "third.txt"
        bad.write_text("{ not valid json", encoding="utf-8")
        good.write_text("valid file continues", encoding="utf-8")
        third.write_text("third file continues", encoding="utf-8")
        engine = self.engine()

        with patch.object(
            engine,
            "_record_failure",
            side_effect=RuntimeError("synthetic database locked"),
        ):
            report = engine.ingest()

        self.assertIn("broken.json", {item.path for item in report.failed})
        self.assertIn("valid.txt", {item.path for item in report.indexed})
        self.assertIn("third.txt", {item.path for item in report.indexed})
        engine.close()

    def test_directory_traversal_error_does_not_become_mass_deletion(self):
        source = self.root / "stable.txt"
        source.write_text("stable indexed content", encoding="utf-8")
        engine = self.engine()
        engine.ingest()

        with patch(
            "core.knowledge.engine.os.walk",
            side_effect=PermissionError("synthetic directory denial"),
        ):
            with self.assertRaises(PermissionError):
                engine.scan()

        self.assertTrue(engine.search("stable"))
        self.assertIsNotNone(engine.file_metadata("stable.txt"))
        engine.close()

    def test_transaction_rollback_and_recovery_with_midstream_parser_failure(self):
        class FlakyParser:
            name = "flaky-test"
            fail = False

            def parse(self, path: Path, *, read_size: int = 65536):
                yield ParsedUnit("new content", 0, 11, 1, 1)
                if self.fail:
                    raise ParserError("synthetic parser failure")

        flaky = FlakyParser()
        registry = ParserRegistry({".txt": flaky})
        source = self.root / "rollback.txt"
        source.write_text("old", encoding="utf-8")
        engine = self.engine(parser_registry=registry)
        self.assertEqual(len(engine.ingest().indexed), 1)
        old_id = engine.search("new")[0].chunk_id

        flaky.fail = True
        source.write_text("changed", encoding="utf-8")
        failed = engine.ingest()
        self.assertEqual(len(failed.failed), 1)
        self.assertEqual(engine.search("new")[0].chunk_id, old_id)
        metadata = engine.file_metadata("rollback.txt")
        self.assertNotEqual(metadata["indexed_hash"], metadata["content_hash"])
        self.assertEqual(metadata["parser_status"], "failed")

        flaky.fail = False
        source.write_text("recovered", encoding="utf-8")
        recovered = engine.ingest()
        self.assertEqual(len(recovered.indexed), 1)
        self.assertTrue(engine.search("new"))

    def test_restart_reuses_persisted_index_and_skips_unchanged(self):
        source = self.root / "restart.py"
        source.write_text("def restart_marker():\n    return True\n", encoding="utf-8")
        first = self.engine()
        first.ingest()
        first.close()

        second = self.engine()
        scan = second.scan()
        self.assertEqual(len(scan.unchanged), 1)
        self.assertTrue(second.search("restart_marker"))
        self.assertEqual(len(second.ingest(scan).skipped), 1)

    def test_fts5_works_without_embeddings_and_results_are_structured(self):
        source = self.root / "technical.py"
        source.write_text("def lexical_keyword():\n    return 'FTS5 technical term'\n", encoding="utf-8")
        engine = self.engine()
        engine.ingest()
        results = engine.search("FTS5 technical")
        self.assertTrue(results)
        result = results[0]
        self.assertEqual(result.retrieval_method, "lexical_fts5")
        self.assertIn("line_start", result.location)
        self.assertIn("technical.py", result.as_dict()["source_file"])
        self.assertEqual(engine.embedding_metadata_count(), 0)

    def test_embedding_adapter_is_optional_and_failures_do_not_break_lexical_index(self):
        class DownAdapter:
            adapter_name = "test-down"
            model_name = "none"

            def embed(self, text: str):
                raise RuntimeError("backend unavailable")

        (self.root / "embed.txt").write_text("embedding lexical fallback", encoding="utf-8")
        engine = self.engine(embedding_adapter=DownAdapter())
        report = engine.ingest()
        self.assertEqual(len(report.indexed), 1)
        self.assertTrue(engine.search("fallback"))
        self.assertGreater(engine.embedding_metadata_count(), 0)
        status = engine._db.execute(
            "SELECT status FROM knowledge_embedding_metadata LIMIT 1"
        ).fetchone()[0]
        self.assertEqual(status, "unavailable")

    def test_large_synthetic_input_uses_bounded_parser_reads(self):
        class TrackingParser:
            name = "tracking-text"

            def __init__(self):
                self.max_read = 0
                self.read_calls = 0

            def parse(self, path: Path, *, read_size: int = 65536):
                offset = 0
                with path.open("r", encoding="utf-8") as handle:
                    while True:
                        part = handle.read(read_size)
                        if not part:
                            break
                        self.max_read = max(self.max_read, len(part))
                        self.read_calls += 1
                        end = offset + len(part)
                        yield ParsedUnit(part, offset, end, 1, 1)
                        offset = end

        tracking = TrackingParser()
        registry = ParserRegistry({".txt": tracking})
        (self.root / "large.txt").write_text("0123456789" * 200_000, encoding="utf-8")
        engine = KnowledgeEngine(
            self.root,
            db_path=self.db,
            parser_registry=registry,
            chunking=ChunkingConfig(chunk_size=256, overlap=16, min_size=8, max_size=256),
            read_size=1024,
            batch_size=32,
        )
        report = engine.ingest()
        self.assertEqual(len(report.indexed), 1)
        self.assertGreater(tracking.read_calls, 100)
        self.assertLessEqual(tracking.max_read, 1024)
        self.assertGreater(engine.chunk_count("large.txt"), 1000)


if __name__ == "__main__":
    unittest.main()

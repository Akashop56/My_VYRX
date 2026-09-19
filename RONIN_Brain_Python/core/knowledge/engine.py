"""Persistent incremental local knowledge engine.

The engine is deliberately independent from the planner. It performs only
source discovery, transactional ingestion, and lexical search; callers decide
when to invoke those deterministic operations.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.knowledge.chunking import Chunk, ChunkingConfig, iter_chunks
from core.knowledge.embeddings import EmbeddingAdapter, normalize_embedding_result
from core.knowledge.models import (
    ChangeKind,
    FileObservation,
    IngestionFileResult,
    IngestionReport,
    IngestionStatus,
    KnowledgeSearchResult,
    ScanReport,
)
from core.knowledge.parsers import ParserRegistry, default_parser_registry
from memory.db_manager import DATABASE_PATH as DEFAULT_DATABASE_PATH


_HASH_READ_SIZE = 1024 * 1024
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "brain"


class SourceChangedError(RuntimeError):
    """The source changed while a transactional parse was in progress."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: BaseException) -> str:
    """Persist a bounded, class-level error without a traceback or secrets."""
    message = " ".join(str(exc).split())[:500]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _content_hash(path: Path, *, read_size: int = _HASH_READ_SIZE) -> str:
    """Hash incrementally; never materialize a source file in memory."""
    digest = hashlib.blake2b(digest_size=32)
    with path.open("rb") as handle:
        while True:
            block = handle.read(max(1024, int(read_size)))
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


class KnowledgeEngine:
    """Persistent local file index with bounded streaming ingestion."""

    def __init__(
        self,
        knowledge_root: str | Path | None = None,
        *,
        db_path: str | Path | None = None,
        parser_registry: ParserRegistry | None = None,
        chunking: ChunkingConfig | None = None,
        embedding_adapter: EmbeddingAdapter | None = None,
        read_size: int = 65536,
        batch_size: int = 128,
    ) -> None:
        configured_root = knowledge_root or os.getenv("VYRX_KNOWLEDGE_ROOT") or _DEFAULT_ROOT
        self.root = Path(configured_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path or DEFAULT_DATABASE_PATH).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.parsers = parser_registry or default_parser_registry()
        self.chunking = chunking or ChunkingConfig()
        self.embedding_adapter = embedding_adapter
        self.read_size = max(1024, int(read_size))
        self.batch_size = max(1, int(batch_size))
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_files (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    indexed_size INTEGER,
                    indexed_mtime_ns INTEGER,
                    indexed_hash TEXT,
                    parser_name TEXT,
                    parser_status TEXT NOT NULL,
                    parser_error TEXT,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT NOT NULL,
                    indexed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL REFERENCES knowledge_files(path) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    char_start INTEGER NOT NULL,
                    char_end INTEGER NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    source_kind TEXT NOT NULL DEFAULT 'text',
                    UNIQUE(file_path, ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_file
                    ON knowledge_chunks(file_path, ordinal);

                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    content,
                    source_path,
                    tokenize = 'unicode61'
                );

                CREATE TABLE IF NOT EXISTS knowledge_embedding_metadata (
                    chunk_id INTEGER PRIMARY KEY REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
                    adapter_name TEXT NOT NULL,
                    model_name TEXT,
                    dimensions INTEGER,
                    status TEXT NOT NULL,
                    metadata_json TEXT,
                    vector_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_ingestion_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._db.execute(
                "INSERT OR REPLACE INTO knowledge_ingestion_state(key, value) VALUES(?, ?)",
                ("schema_version", "1"),
            )
            self._db.execute(
                "INSERT OR REPLACE INTO knowledge_ingestion_state(key, value) VALUES(?, ?)",
                ("knowledge_root", str(self.root)),
            )

    # -- source discovery -------------------------------------------------

    def _relative_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _source_paths(self) -> list[Path]:
        paths: list[Path] = []
        try:
            for path in self.root.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    paths.append(path)
        except OSError:
            # Individual stat/hash errors are handled below; directory walk
            # failure simply leaves discoverable entries out of this pass.
            pass
        return sorted(paths, key=lambda item: item.as_posix().casefold())

    def _stored_files(self) -> dict[str, sqlite3.Row]:
        rows = self._db.execute("SELECT * FROM knowledge_files").fetchall()
        return {str(row["path"]): row for row in rows}

    def scan(self) -> ScanReport:
        """Observe source files and classify new/modified/deleted/unchanged."""
        with self._lock:
            stored = self._stored_files()
            observations: list[FileObservation] = []
            seen: set[str] = set()
            for path in self._source_paths():
                try:
                    stat = path.stat()
                    relative = self._relative_path(path)
                    digest = _content_hash(path)
                    parser = self.parsers.parser_for(path)
                    previous = stored.get(relative)
                    seen.add(relative)
                    if parser is None:
                        change = (
                            ChangeKind.UNCHANGED
                            if previous is not None
                            and previous["indexed_hash"] == digest
                            and previous["parser_status"] == IngestionStatus.UNSUPPORTED.value
                            else ChangeKind.UNSUPPORTED
                        )
                    elif (
                        previous is not None
                        and previous["indexed_hash"] == digest
                        and previous["indexed_size"] == stat.st_size
                        and previous["indexed_mtime_ns"] == stat.st_mtime_ns
                        and previous["parser_status"] == IngestionStatus.INDEXED.value
                    ):
                        change = ChangeKind.UNCHANGED
                    else:
                        change = ChangeKind.NEW if previous is None else ChangeKind.MODIFIED
                    observations.append(FileObservation(
                        path=relative,
                        absolute_path=str(path),
                        size=int(stat.st_size),
                        mtime_ns=int(stat.st_mtime_ns),
                        content_hash=digest,
                        suffix=path.suffix.casefold(),
                        change=change,
                        parser_name=getattr(parser, "name", None),
                        parser_status=previous["parser_status"] if previous else None,
                        parser_error=previous["parser_error"] if previous else None,
                    ))
                except (OSError, UnicodeError, ValueError) as exc:
                    relative = path.name
                    try:
                        relative = self._relative_path(path)
                    except (OSError, ValueError):
                        pass
                    previous = stored.get(relative)
                    observations.append(FileObservation(
                        path=relative,
                        absolute_path=str(path),
                        size=int(previous["size"]) if previous else 0,
                        mtime_ns=int(previous["mtime_ns"]) if previous else 0,
                        content_hash="",
                        suffix=path.suffix.casefold(),
                        change=ChangeKind.MODIFIED if previous else ChangeKind.NEW,
                        parser_name=None,
                        parser_status="error",
                        parser_error=_safe_error(exc),
                    ))
                    seen.add(relative)

            deleted: list[FileObservation] = []
            for relative, row in sorted(stored.items()):
                if relative in seen:
                    continue
                deleted.append(FileObservation(
                    path=relative,
                    absolute_path=str(self.root / relative),
                    size=int(row["size"]),
                    mtime_ns=int(row["mtime_ns"]),
                    content_hash=str(row["content_hash"]),
                    suffix=Path(relative).suffix.casefold(),
                    change=ChangeKind.DELETED,
                    parser_name=row["parser_name"],
                    parser_status=row["parser_status"],
                    parser_error=row["parser_error"],
                ))
            return ScanReport(tuple(observations), tuple(deleted))

    # -- transactional ingestion -----------------------------------------

    def ingest(self, scan: ScanReport | None = None) -> IngestionReport:
        """Incrementally index a scan, isolating failures per source file."""
        report = scan or self.scan()
        results: list[IngestionFileResult] = []
        for observation in report.observations:
            if observation.change == ChangeKind.UNCHANGED:
                results.append(IngestionFileResult(
                    observation.path,
                    IngestionStatus.SKIPPED,
                    observation.change,
                ))
                continue
            parser = self.parsers.parser_for(Path(observation.path))
            if parser is None:
                self._record_unsupported(observation)
                results.append(IngestionFileResult(
                    observation.path,
                    IngestionStatus.UNSUPPORTED,
                    observation.change,
                    error="no parser registered for suffix",
                ))
                continue
            try:
                chunks = self._index_one(observation, parser)
                results.append(IngestionFileResult(
                    observation.path,
                    IngestionStatus.INDEXED,
                    observation.change,
                    chunks=chunks,
                ))
            except Exception as exc:
                # _index_one has rolled back before this status transaction.
                error = _safe_error(exc)
                self._record_failure(observation, parser_name=getattr(parser, "name", None), error=error)
                results.append(IngestionFileResult(
                    observation.path,
                    IngestionStatus.FAILED,
                    observation.change,
                    error=error,
                ))

        for observation in report.deleted:
            try:
                self._delete_file(observation.path)
                results.append(IngestionFileResult(
                    observation.path,
                    IngestionStatus.DELETED,
                    ChangeKind.DELETED,
                ))
            except Exception as exc:
                results.append(IngestionFileResult(
                    observation.path,
                    IngestionStatus.FAILED,
                    ChangeKind.DELETED,
                    error=_safe_error(exc),
                ))
        return IngestionReport(tuple(results))

    def _begin_reindex(self, relative: str) -> None:
        # Let SQLite stream the old-id subquery internally instead of copying
        # every chunk ID from a multi-gigabyte source into Python memory.
        self._db.execute(
            """DELETE FROM knowledge_chunks_fts
               WHERE CAST(chunk_id AS INTEGER) IN (
                   SELECT id FROM knowledge_chunks WHERE file_path = ?
               )""",
            (relative,),
        )
        self._db.execute("DELETE FROM knowledge_chunks WHERE file_path = ?", (relative,))

    def _embedding_rows(self, chunk_rows: list[tuple[int, Chunk]]) -> list[tuple[Any, ...]]:
        if self.embedding_adapter is None:
            return []
        rows: list[tuple[Any, ...]] = []
        for chunk_id, chunk in chunk_rows:
            try:
                raw = self.embedding_adapter.embed(chunk.content)
                result = normalize_embedding_result(raw, self.embedding_adapter)
                rows.append((
                    chunk_id,
                    result.adapter_name,
                    result.model_name,
                    result.dimensions,
                    "available",
                    json.dumps(result.metadata, ensure_ascii=False, sort_keys=True),
                    json.dumps(list(result.vector), ensure_ascii=False),
                    None,
                    _utc_now(),
                ))
            except Exception as exc:
                # Embeddings are optional. A backend outage never invalidates
                # lexical ingestion or deletes a previously valid index.
                rows.append((
                    chunk_id,
                    str(getattr(self.embedding_adapter, "adapter_name", type(self.embedding_adapter).__name__)),
                    getattr(self.embedding_adapter, "model_name", None),
                    None,
                    "unavailable",
                    "{}",
                    None,
                    _safe_error(exc),
                    _utc_now(),
                ))
        return rows

    def _insert_chunk_batch(self, relative: str, batch: list[Chunk]) -> int:
        self._db.executemany(
            """INSERT INTO knowledge_chunks(
                file_path, ordinal, content, char_start, char_end,
                line_start, line_end, source_kind
            ) VALUES(?,?,?,?,?,?,?,?)""",
            [(
                relative,
                chunk.ordinal,
                chunk.content,
                chunk.char_start,
                chunk.char_end,
                chunk.line_start,
                chunk.line_end,
                chunk.source_kind,
            ) for chunk in batch],
        )
        first = batch[0].ordinal
        last = batch[-1].ordinal
        rows = self._db.execute(
            """SELECT id, ordinal, content, char_start, char_end, line_start,
                      line_end, source_kind
               FROM knowledge_chunks
               WHERE file_path = ? AND ordinal BETWEEN ? AND ?
               ORDER BY ordinal""",
            (relative, first, last),
        ).fetchall()
        by_ordinal = {int(row["ordinal"]): row for row in rows}
        fts_rows: list[tuple[str, str, str]] = []
        embedding_inputs: list[tuple[int, Chunk]] = []
        for chunk in batch:
            row = by_ordinal[chunk.ordinal]
            chunk_id = int(row["id"])
            fts_rows.append((str(chunk_id), chunk.content, relative))
            embedding_inputs.append((chunk_id, chunk))
        self._db.executemany(
            "INSERT INTO knowledge_chunks_fts(chunk_id, content, source_path) VALUES(?,?,?)",
            fts_rows,
        )
        embedding_rows = self._embedding_rows(embedding_inputs)
        if embedding_rows:
            self._db.executemany(
                """INSERT OR REPLACE INTO knowledge_embedding_metadata(
                    chunk_id, adapter_name, model_name, dimensions, status,
                    metadata_json, vector_json, error, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                embedding_rows,
            )
        return len(batch)

    def _index_one(self, observation: FileObservation, parser: Any) -> int:
        path = Path(observation.absolute_path)
        count = 0
        batch: list[Chunk] = []
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                # Establish the parent row before inserting child chunks. The
                # row is part of this same transaction, so a parser failure
                # still rolls it back and _record_failure() can persist a
                # separate status afterward.
                self._db.execute(
                    """INSERT OR IGNORE INTO knowledge_files(
                        path, size, mtime_ns, content_hash, indexed_size,
                        indexed_mtime_ns, indexed_hash, parser_name, parser_status,
                        parser_error, chunk_count, last_seen_at, indexed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        observation.path,
                        observation.size,
                        observation.mtime_ns,
                        observation.content_hash,
                        None,
                        None,
                        None,
                        getattr(parser, "name", type(parser).__name__),
                        "processing",
                        None,
                        0,
                        _utc_now(),
                        None,
                    ),
                )
                self._begin_reindex(observation.path)
                units = parser.parse(path, read_size=self.read_size)
                for chunk in iter_chunks(units, self.chunking):
                    batch.append(chunk)
                    if len(batch) >= self.batch_size:
                        count += self._insert_chunk_batch(observation.path, batch)
                        batch.clear()
                if batch:
                    count += self._insert_chunk_batch(observation.path, batch)
                    batch.clear()
                try:
                    stat = path.stat()
                except OSError as exc:
                    raise SourceChangedError("source disappeared during ingestion") from exc
                if stat.st_size != observation.size or stat.st_mtime_ns != observation.mtime_ns:
                    raise SourceChangedError("source changed during ingestion")
                stamp = _utc_now()
                self._db.execute(
                    """INSERT INTO knowledge_files(
                        path, size, mtime_ns, content_hash, indexed_size,
                        indexed_mtime_ns, indexed_hash, parser_name, parser_status,
                        parser_error, chunk_count, last_seen_at, indexed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(path) DO UPDATE SET
                        size=excluded.size, mtime_ns=excluded.mtime_ns,
                        content_hash=excluded.content_hash,
                        indexed_size=excluded.indexed_size,
                        indexed_mtime_ns=excluded.indexed_mtime_ns,
                        indexed_hash=excluded.indexed_hash,
                        parser_name=excluded.parser_name,
                        parser_status=excluded.parser_status,
                        parser_error=NULL, chunk_count=excluded.chunk_count,
                        last_seen_at=excluded.last_seen_at, indexed_at=excluded.indexed_at""",
                    (
                        observation.path,
                        observation.size,
                        observation.mtime_ns,
                        observation.content_hash,
                        observation.size,
                        observation.mtime_ns,
                        observation.content_hash,
                        getattr(parser, "name", type(parser).__name__),
                        IngestionStatus.INDEXED.value,
                        None,
                        count,
                        stamp,
                        stamp,
                    ),
                )
                self._db.commit()
                return count
            except Exception:
                self._db.rollback()
                raise

    def _record_unsupported(self, observation: FileObservation) -> None:
        with self._lock:
            stamp = _utc_now()
            self._db.execute(
                """INSERT INTO knowledge_files(
                    path, size, mtime_ns, content_hash, indexed_size,
                    indexed_mtime_ns, indexed_hash, parser_name, parser_status,
                    parser_error, chunk_count, last_seen_at, indexed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    size=excluded.size, mtime_ns=excluded.mtime_ns,
                    content_hash=excluded.content_hash,
                    parser_status=excluded.parser_status,
                    parser_error=excluded.parser_error,
                    last_seen_at=excluded.last_seen_at""",
                (
                    observation.path, observation.size, observation.mtime_ns,
                    observation.content_hash, observation.size, observation.mtime_ns,
                    observation.content_hash, None,
                    IngestionStatus.UNSUPPORTED.value,
                    "no parser registered for suffix", 0, stamp, None,
                ),
            )

    def _record_failure(self, observation: FileObservation, *, parser_name: str | None, error: str) -> None:
        with self._lock:
            stamp = _utc_now()
            self._db.execute(
                """INSERT INTO knowledge_files(
                    path, size, mtime_ns, content_hash, indexed_size,
                    indexed_mtime_ns, indexed_hash, parser_name, parser_status,
                    parser_error, chunk_count, last_seen_at, indexed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    size=excluded.size, mtime_ns=excluded.mtime_ns,
                    content_hash=excluded.content_hash,
                    parser_name=excluded.parser_name,
                    parser_status=excluded.parser_status,
                    parser_error=excluded.parser_error,
                    last_seen_at=excluded.last_seen_at""",
                (
                    observation.path, observation.size, observation.mtime_ns,
                    observation.content_hash, None, None, None, parser_name,
                    IngestionStatus.FAILED.value, error, 0, stamp, None,
                ),
            )

    def _delete_file(self, relative: str) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    """DELETE FROM knowledge_chunks_fts
                       WHERE CAST(chunk_id AS INTEGER) IN (
                           SELECT id FROM knowledge_chunks WHERE file_path = ?
                       )""",
                    (relative,),
                )
                self._db.execute("DELETE FROM knowledge_files WHERE path = ?", (relative,))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    # -- independent lexical retrieval -----------------------------------

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[\w]+", query or "", flags=re.UNICODE)
        if not tokens:
            return ""
        # Quoting prevents user punctuation from becoming FTS5 operators.
        # Include the exact phrase first, then individual terms so both
        # natural-language phrases and technical keywords remain useful
        # without claiming semantic similarity.
        phrase = " ".join(tokens).replace('"', '""')
        terms = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        return f'"{phrase}" OR {terms}'

    def search(self, query: str, limit: int = 20) -> list[KnowledgeSearchResult]:
        """Search only the persisted FTS5 lexical index."""
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        limit = max(1, min(200, int(limit or 20)))
        with self._lock:
            rows = self._db.execute(
                """SELECT c.id, c.content, c.file_path, c.char_start,
                          c.char_end, c.line_start, c.line_end, c.source_kind,
                          bm25(knowledge_chunks_fts) AS score
                   FROM knowledge_chunks_fts AS f
                   JOIN knowledge_chunks AS c ON c.id = CAST(f.chunk_id AS INTEGER)
                   WHERE knowledge_chunks_fts MATCH ?
                   ORDER BY score ASC, c.id ASC
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        results: list[KnowledgeSearchResult] = []
        for row in rows:
            score = float(row["score"] or 0.0)
            results.append(KnowledgeSearchResult(
                chunk_id=int(row["id"]),
                matched_chunk=str(row["content"]),
                source_file=str(self.root / str(row["file_path"])),
                location={
                    "char_start": int(row["char_start"]),
                    "char_end": int(row["char_end"]),
                    "line_start": int(row["line_start"]),
                    "line_end": int(row["line_end"]),
                },
                retrieval_method="lexical_fts5",
                relevance=1.0 / (1.0 + abs(score)),
                metadata={
                    "relative_path": str(row["file_path"]),
                    "source_kind": str(row["source_kind"]),
                },
            ))
        return results

    # -- inspection / lifecycle ------------------------------------------

    def file_metadata(self, relative_path: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM knowledge_files WHERE path = ?", (relative_path,)
            ).fetchone()
        return dict(row) if row else None

    def chunk_count(self, relative_path: str | None = None) -> int:
        with self._lock:
            if relative_path is None:
                row = self._db.execute("SELECT COUNT(*) AS c FROM knowledge_chunks").fetchone()
            else:
                row = self._db.execute(
                    "SELECT COUNT(*) AS c FROM knowledge_chunks WHERE file_path = ?", (relative_path,)
                ).fetchone()
        return int(row["c"]) if row else 0

    def embedding_metadata_count(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS c FROM knowledge_embedding_metadata"
            ).fetchone()
        return int(row["c"]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "KnowledgeEngine":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

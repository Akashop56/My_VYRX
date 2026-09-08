"""VYRX three-layer memory engine (Personal / Experience / Knowledge).

SQLite-backed, used by the Memory module UI and by the planner when the AI
saves new knowledge. Lives in the same DB file as conversations/facts.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import aiosqlite

CATEGORIES = ("personal", "experience", "knowledge")

SORTS = {
    "newest": "created_at DESC, id DESC",
    "oldest": "created_at ASC, id ASC",
    "most_used": "use_count DESC, created_at DESC",
    "important": "pinned DESC, importance DESC, created_at DESC",
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _norm(row) -> dict:
    """Row -> JSON dict with SQLite 0/1 ints normalized to real booleans."""
    d = dict(row)
    if "pinned" in d:
        d["pinned"] = bool(d["pinned"])
    return d


class MemoryEngine:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    async def init(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL DEFAULT 'personal'
                        CHECK (category IN ('personal','experience','knowledge')),
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
                    pinned INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'user',
                    confidence REAL NOT NULL DEFAULT 0.9,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0)"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category, created_at DESC)"
            )
            await db.commit()

    async def add(
        self,
        category: str = "personal",
        title: str = "",
        content: str = "",
        importance: int = 3,
        pinned: bool = False,
        source: str = "user",
        confidence: float = 0.9,
    ) -> dict:
        if category not in CATEGORIES:
            category = "personal"
        stamp = _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """INSERT INTO memories(category, title, content, importance, pinned, source,
                                        confidence, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (category, title[:200], content, max(1, min(5, importance)),
                 1 if pinned else 0, source, confidence, stamp, stamp),
            )
            await db.commit()
            memory_id = cursor.lastrowid
            cursor = await db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = await cursor.fetchone()
        return _norm(row) if row else {}

    async def list(
        self,
        category: str | None = None,
        search: str | None = None,
        sort: str = "newest",
        limit: int = 100,
    ) -> list[dict]:
        query = "SELECT * FROM memories"
        clauses: list[str] = []
        params: list = []
        if category in CATEGORIES:
            clauses.append("category = ?")
            params.append(category)
        term = (search or "").strip()
        if term:
            like = f"%{term}%"
            clauses.append("(title LIKE ? OR content LIKE ?)")
            params.extend([like, like])
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY " + SORTS.get(sort, SORTS["newest"]) + " LIMIT ?"
        params.append(max(1, min(500, limit)))
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            return [_norm(row) for row in await cursor.fetchall()]

    async def get(self, memory_id: int) -> dict | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = await cursor.fetchone()
        return _norm(row) if row else None

    async def update(self, memory_id: int, fields: dict) -> dict | None:
        allowed = {
            "category": lambda v: v if v in CATEGORIES else None,
            "title": lambda v: str(v)[:200] if v and str(v).strip() else None,
            "content": lambda v: str(v) if v and str(v).strip() else None,
            "importance": lambda v: max(1, min(5, int(v))) if v is not None else None,
            "pinned": lambda v: 1 if v else 0,
            "confidence": lambda v: max(0.0, min(1.0, float(v))) if v is not None else None,
        }
        sets: list[str] = []
        params: list = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            transformed = allowed[key](value)
            if transformed is None:
                continue
            sets.append(f"{key} = ?")
            params.append(transformed)
        if not sets:
            return await self.get(memory_id)
        sets.append("updated_at = ?")
        params.append(_now_iso())
        params.append(memory_id)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params)
            await db.commit()
            cursor = await db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = await cursor.fetchone()
        return _norm(row) if row else None

    async def touch(self, memory_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE memories SET use_count = use_count + 1, last_used = ? WHERE id = ?",
                (_now_iso(), memory_id),
            )
            await db.commit()

    # -- agent recall ---------------------------------------------------------
    # Keyword-ranked retrieval used by the ReAct loop to inject standing
    # orders / preferences into the system prompt on every request.

    _STOPWORDS = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "and", "or", "but", "if", "then", "else", "when", "what", "which",
        "who", "whom", "this", "that", "these", "those", "am", "do", "does",
        "did", "will", "would", "could", "should", "may", "might", "must",
        "have", "has", "had", "having", "with", "for", "from", "into", "on",
        "of", "to", "in", "it", "its", "my", "your", "you", "i", "me", "we",
        "open", "close", "please", "boss",
    })

    @classmethod
    def _query_tokens(cls, query: str) -> list[str]:
        cleaned = "".join(
            ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in query
        )
        return [word for word in cleaned.split()
                if len(word) > 2 and word not in cls._STOPWORDS]

    async def search_relevant(self, query: str, limit: int = 5) -> list[dict]:
        """Return memories most relevant to ``query`` (keyword-ranked).

        Pinned + high-importance memories win ties. Returns [] when nothing
        matches so the prompt stays clean.
        """
        limit = max(1, min(20, int(limit or 5)))
        words = self._query_tokens(query or "")
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if not words:
                cursor = await db.execute(
                    "SELECT * FROM memories ORDER BY pinned DESC, importance DESC, id DESC LIMIT ?",
                    (limit,),
                )
                return [_norm(row) for row in await cursor.fetchall()]
            like_clauses = " OR ".join(["(title LIKE ? OR content LIKE ?)"] * len(words))
            params: list = []
            for word in words:
                like = f"%{word}%"
                params.extend([like, like])
            cursor = await db.execute(
                f"SELECT * FROM memories WHERE {like_clauses}", params  # noqa: S608 - placeholders only
            )
            candidates = [_norm(row) for row in await cursor.fetchall()]

        def _score(row: dict) -> float:
            haystack = f"{row.get('title', '')} {row.get('content', '')}".lower()
            hits = sum(1 for word in words if word in haystack)
            if hits == 0:
                return -1.0
            return (hits * 10.0 + float(row.get("importance", 3)) * 2.0
                    + (5.0 if row.get("pinned") else 0.0)
                    + min(float(row.get("use_count", 0)), 10.0) * 0.3)

        ranked = sorted(candidates, key=_score, reverse=True)
        return [row for row in ranked if _score(row) >= 0.0][:limit]

    async def delete(self, memory_id: int) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def count(self) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM memories")
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def stats(self) -> dict:
        total = await self.count()
        by_category = {"personal": 0, "experience": 0, "knowledge": 0}
        duplicates = 0
        stale = 0
        oldest: str | None = None
        newest: str | None = None
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT category, COUNT(*) AS c FROM memories GROUP BY category"
            )
            for row in await cursor.fetchall():
                by_category[row["category"]] = int(row["c"])
            cursor = await db.execute(
                "SELECT COUNT(*) AS c FROM (SELECT title FROM memories GROUP BY title HAVING COUNT(*) > 1)"
            )
            row = await cursor.fetchone()
            duplicates = int(row["c"]) if row else 0
            cursor = await db.execute(
                "SELECT COUNT(*) AS c FROM memories WHERE use_count = 0 AND last_used IS NULL"
            )
            row = await cursor.fetchone()
            stale = int(row["c"]) if row else 0
            cursor = await db.execute("SELECT MIN(created_at) AS m, MAX(created_at) AS x FROM memories")
            row = await cursor.fetchone()
            if row:
                oldest, newest = row["m"], row["x"]

        try:
            size_bytes = os.path.getsize(self._db_path)
        except OSError:
            size_bytes = 0

        # Health: start at 100, penalize exact duplicate titles and never-used entries.
        dup_penalty = min(30, duplicates * 3)
        stale_penalty = min(20, round(100 * stale / total)) if total else 0
        health_score = max(0, 100 - dup_penalty - stale_penalty) if total else 100

        retention_days = 0
        if oldest:
            try:
                retention_days = max(0, (datetime.now().astimezone() - datetime.fromisoformat(oldest)).days)
            except ValueError:
                retention_days = 0

        return {
            "total": total,
            "by_category": by_category,
            "size_bytes": size_bytes,
            "health_score": health_score,
            "retention_days": retention_days,
            "oldest": oldest,
            "newest": newest,
            "duplicates": duplicates,
            "unused": stale,
        }

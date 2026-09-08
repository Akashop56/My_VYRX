"""Self-learning memory tools for the ReAct agent (Brain-side).

The agent MUST call :func:`save_memory` whenever Boss corrects it, teaches a
preference, or states a lasting fact — in the SAME task, before speaking the
final answer. Relevant memories are auto-injected into every prompt, and the
agent can proactively recall with :func:`retrieve_memory`.

Synchronous sqlite3 is used (instead of aiosqlite) so these tools run inside
the sync ``core.tool_registry.execute_tool`` path. Schema matches
``memory.memory_engine.MemoryEngine`` exactly.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

CATEGORIES = ("personal", "experience", "knowledge")

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "if", "then", "else", "when", "what", "which",
    "who", "whom", "this", "that", "these", "those", "am", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "must",
    "have", "has", "had", "having", "with", "for", "from", "into", "on",
    "of", "to", "in", "it", "its", "my", "your", "you", "i", "me", "we",
})


def _db_path() -> Path:
    override = os.getenv("RONIN_DB_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "memory" / "ronin_brain.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
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
    con.commit()
    return con


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _tokens(text: str) -> list[str]:
    return [word for word in "".join(
        ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in text
    ).split() if len(word) > 2 and word not in _STOPWORDS]


def save_memory(category: str, title: str, content: str, importance: int = 3) -> dict:
    """Persist a learning: user preference, correction, or lasting fact.

    Category must be personal, experience, or knowledge. Call this BEFORE
    speaking the final answer whenever Boss teaches the agent something new.
    """
    category = (category or "personal").strip().lower()
    if category not in CATEGORIES:
        category = "personal"
    title = (title or "").strip()[:200] or "Untitled memory"
    content = (content or "").strip()
    if not content:
        return {"error": "content must not be empty"}
    try:
        importance = max(1, min(5, int(importance)))
    except (TypeError, ValueError):
        importance = 3
    stamp = _now_iso()
    con = _connect()
    try:
        cursor = con.execute(
            """INSERT INTO memories(category, title, content, importance, pinned,
                                    source, confidence, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (category, title, content, importance, 0, "agent", 0.9, stamp, stamp),
        )
        con.commit()
        memory_id = cursor.lastrowid
    finally:
        con.close()
    return {"saved": True, "id": memory_id, "category": category, "title": title}


def retrieve_memory(query: str, limit: int = 5) -> dict:
    """Recall past learnings relevant to the query (keyword-ranked).

    Returns the most relevant memories with id, category, title, content and
    importance. Use when the standing [MEMORY] context may be missing something.
    """
    query = (query or "").strip()
    try:
        limit = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        limit = 5
    if not query:
        return {"query": query, "memories": []}
    words = _tokens(query)
    con = _connect()
    try:
        if not words:
            cursor = con.execute(
                "SELECT * FROM memories ORDER BY pinned DESC, importance DESC, id DESC LIMIT ?",
                (limit,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        else:
            like_clauses = " OR ".join(["(title LIKE ? OR content LIKE ?)"] * len(words))
            params: list = []
            for word in words:
                like = f"%{word}%"
                params.extend([like, like])
            cursor = con.execute(
                f"SELECT * FROM memories WHERE {like_clauses}", params  # noqa: S608 - placeholders only
            )
            candidates = [dict(row) for row in cursor.fetchall()]

            def _score(row: dict) -> float:
                haystack = f"{row.get('title', '')} {row.get('content', '')}".lower()
                hits = sum(1 for word in words if word in haystack)
                return (
                    hits * 10.0
                    + float(row.get("importance", 3)) * 2.0
                    + (5.0 if row.get("pinned") else 0.0)
                    + min(float(row.get("use_count", 0)), 10.0) * 0.3
                )

            candidates.sort(key=_score, reverse=True)
            rows = candidates[:limit]
        # Count this recall as usage.
        for row in rows:
            con.execute(
                "UPDATE memories SET use_count = use_count + 1, last_used = ? WHERE id = ?",
                (_now_iso(), row["id"]),
            )
        con.commit()
    finally:
        con.close()
    memories = [
        {"id": row["id"], "category": row["category"], "title": row["title"],
         "content": row["content"][:2000], "importance": row["importance"],
         "pinned": bool(row.get("pinned"))}
        for row in rows
    ]
    return {"query": query, "memories": memories, "count": len(memories)}

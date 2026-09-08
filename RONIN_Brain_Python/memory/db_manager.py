from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import aiosqlite

DATABASE_PATH = Path(__file__).parent / "ronin_brain.db"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def initialize_database() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            user_message TEXT NOT NULL, assistant_response TEXT NOT NULL,
            created_at TEXT NOT NULL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fact_key TEXT UNIQUE NOT NULL,
            fact_value TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        await db.commit()


async def save_conversation(session_id: str, user_message: str, assistant_response: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT INTO conversations(session_id,user_message,assistant_response,created_at) VALUES(?,?,?,?)",
                         (session_id, user_message, assistant_response, now()))
        await db.commit()


async def recent_history(session_id: str, limit: int = 10) -> list[dict[str, str]]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT user_message,assistant_response,created_at FROM conversations WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit))
        return [dict(row) for row in reversed(await cursor.fetchall())]


async def store_fact(key: str, value: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        stamp = now()
        await db.execute("INSERT INTO facts(fact_key,fact_value,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(fact_key) DO UPDATE SET fact_value=excluded.fact_value,updated_at=excluded.updated_at", (key, value, stamp, stamp))
        await db.commit()


async def search_facts(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Keyword recall over the legacy facts table for prompt injection."""
    words = [word for word in "".join(
        ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in (query or "")
    ).split() if len(word) > 2]
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if not words:
            cursor = await db.execute(
                "SELECT fact_key,fact_value FROM facts ORDER BY updated_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in await cursor.fetchall()]
        clauses = " OR ".join(["(fact_key LIKE ? OR fact_value LIKE ?)"] * len(words))
        params: list = []
        for word in words:
            like = f"%{word}%"
            params.extend([like, like])
        cursor = await db.execute(
            f"SELECT fact_key,fact_value FROM facts WHERE {clauses} LIMIT ?",  # noqa: S608 - placeholders only
            (*params, max(1, min(20, limit))),
        )
        return [dict(row) for row in await cursor.fetchall()]

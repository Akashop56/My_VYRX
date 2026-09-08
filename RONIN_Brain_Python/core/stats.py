"""Daily counters + tool usage events that power the VYRX Dashboard.

All numbers are real, accumulated by the planner/tool execution pipeline.
Stored in SQLite (same DB file as conversations/facts) so they survive
Brain restarts.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import aiosqlite

DAILY_COUNTERS = (
    "tasks_completed",
    "auto_tasks",
    "learned",
    "voice_commands",
    "apps_opened",
    "web_searches",
)

_DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


class StatsTracker:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    async def init(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """CREATE TABLE IF NOT EXISTS stats_daily (
                    date TEXT PRIMARY KEY,
                    tasks_completed INTEGER NOT NULL DEFAULT 0,
                    auto_tasks INTEGER NOT NULL DEFAULT 0,
                    learned INTEGER NOT NULL DEFAULT 0,
                    voice_commands INTEGER NOT NULL DEFAULT 0,
                    apps_opened INTEGER NOT NULL DEFAULT 0,
                    web_searches INTEGER NOT NULL DEFAULT 0)"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS tool_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    detail TEXT,
                    success INTEGER NOT NULL DEFAULT 1,
                    auto INTEGER NOT NULL DEFAULT 0,
                    ts TEXT NOT NULL)"""
            )
            await db.commit()

    async def bump(self, counter: str, amount: int = 1, auto: bool = False) -> None:
        if counter not in DAILY_COUNTERS:
            return
        today = _today()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("INSERT OR IGNORE INTO stats_daily(date) VALUES(?)", (today,))
            await db.execute(
                f"UPDATE stats_daily SET {counter} = {counter} + ? WHERE date = ?",
                (amount, today),
            )
            if auto:
                await db.execute(
                    "UPDATE stats_daily SET auto_tasks = auto_tasks + ? WHERE date = ?",
                    (amount, today),
                )
            await db.commit()

    async def record_tool_usage(
        self, tool: str, detail: str | None = None, success: bool = True, auto: bool = False
    ) -> None:
        now = datetime.now().astimezone()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO tool_usage(date, tool, detail, success, auto, ts) VALUES(?,?,?,?,?,?)",
                (_date_str(now), tool, (detail or "")[:240], 1 if success else 0, 1 if auto else 0,
                 now.isoformat()),
            )
            await db.commit()

    async def _day_row(self, date: str) -> dict:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM stats_daily WHERE date = ?", (date,))
            row = await cursor.fetchone()
        if row is None:
            return {"date": date, **{name: 0 for name in DAILY_COUNTERS}}
        data = dict(row)
        return {key: data.get(key, 0) for key in ("date", *DAILY_COUNTERS)}

    async def day(self, date: str) -> dict:
        return await self._day_row(date)

    async def today(self) -> dict:
        return await self._day_row(_today())

    async def yesterday(self) -> dict:
        return await self._day_row(_date_str(datetime.now() - timedelta(days=1)))

    async def last_7_days(self) -> list[dict]:
        now = datetime.now()
        rows = []
        for offset in range(6, -1, -1):
            dt = now - timedelta(days=offset)
            data = await self._day_row(_date_str(dt))
            rows.append({
                "date": data["date"],
                "label": "Today" if offset == 0 else _DAY_LABELS[dt.weekday()],
                "tasks": int(data.get("tasks_completed", 0)) + int(data.get("auto_tasks", 0)),
            })
        return rows

    async def top_tools(self, limit: int = 5, days: int = 7) -> list[dict]:
        since = _date_str(datetime.now() - timedelta(days=days - 1))
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT tool, COUNT(*) AS count FROM tool_usage WHERE date >= ? GROUP BY tool ORDER BY count DESC LIMIT ?",
                (since, limit * 3),
            )
            rows = [dict(r) for r in await cursor.fetchall()]
            total = sum(r["count"] for r in rows) or 1
        top: list[dict] = []
        for r in rows[:limit]:
            top.append({"tool": r["tool"], "count": r["count"], "percent": round(100.0 * r["count"] / total, 1)})
        return top

    async def recent_events(self, limit: int = 5) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT tool, detail, success, auto, ts FROM tool_usage ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = [dict(r) for r in await cursor.fetchall()]
        out = []
        for r in rows:
            try:
                when = datetime.fromisoformat(r["ts"]).strftime("%I:%M %p")
            except (ValueError, TypeError):
                when = ""
            out.append({
                "tool": r["tool"],
                "detail": r["detail"] or "",
                "time": when,
                "success": bool(r["success"]),
                "auto": bool(r["auto"]),
            })
        return out

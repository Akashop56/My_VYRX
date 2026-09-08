from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

# VYRX core modules
from core.action_log import ActionLog
from core.llm_handler import SYSTEM_PROMPT, LLMError
from core.planner import BrainContext, plan_request
from core.provider_manager import KNOWN_PROVIDERS, ProviderManager
from core.router import route_request
from core.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    AskRequest,
    AskResponse,
    DeviceStatusRequest,
    FeedbackRequest,
    MemoryRequest,
    MemoryUpdate,
    ProviderRequest,
    ProviderUpdate,
    UpdateProposal,
)
from core.state_manager import StateManager
from core.stats import StatsTracker
from core.system_updater import safe_hot_reload
from core.tool_registry import execute_tool, get_available_tools
from core.tools_catalog import TOOLS_CATALOG, tool_label, tool_reason
from memory.db_manager import initialize_database, recent_history, save_conversation, store_fact
from memory.memory_engine import MemoryEngine
from tools.system_control import AndroidCommand, command_for_request
from tools.web_search import search

BRAIN_VERSION = "1.1.0"
BOOT_TIME = time.time()
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "memory" / "ronin_brain.db"
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"
PROVIDER_STATE_PATH = BASE_DIR / "config" / "providers.json"
DEVICE_STATE_PATH = BASE_DIR / "config" / "device_status.json"


# ---------------------------------------------------------------------------
# Core singletons (created at import; DB init happens in the lifespan)
# ---------------------------------------------------------------------------

def _load_device_status() -> dict:
    try:
        data = json.loads(DEVICE_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


ACTION_LOG = ActionLog()
STATE = StateManager()
STATE.add_listener(ACTION_LOG.publish_state)
MEMORY_ENGINE = MemoryEngine(DATABASE_PATH)
STATS = StatsTracker(DATABASE_PATH)
PROVIDER_MANAGER = ProviderManager(PROVIDER_STATE_PATH)
CTX = BrainContext(
    log=ACTION_LOG,
    state=STATE,
    stats=STATS,
    provider_manager=PROVIDER_MANAGER,
    memory_engine=MEMORY_ENGINE,
    device_status=_load_device_status(),
)


def _persist_device_status() -> None:
    try:
        DEVICE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_STATE_PATH.write_text(json.dumps(CTX.device_status, indent=2), encoding="utf-8")
    except OSError:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    await initialize_database()
    await MEMORY_ENGINE.init()
    await STATS.init()
    ACTION_LOG.log("VYRX Brain online", "success")
    ACTION_LOG.log(f"Core engine v{BRAIN_VERSION} ready", "info")
    yield


app = FastAPI(title="VYRX Brain (RONIN core)", version=BRAIN_VERSION, lifespan=lifespan)


# ---------------------------------------------------------------------------
# OS-level health sampling — no psutil, works on Android/Termux
# ---------------------------------------------------------------------------

def _read_cpu_times() -> tuple[int, int]:
    try:
        with open("/proc/stat", "r", encoding="ascii") as handle:
            parts = handle.readline().split()[1:]
    except (OSError, IndexError, ValueError):
        return (0, 0)
    values = [int(value) for value in parts if value.isdigit()]
    if not values:
        return (0, 0)
    idle = sum(values[3:5]) if len(values) >= 4 else 0
    return (idle, sum(values))


async def _sample_cpu_percent(sample_ms: int = 150) -> float:
    idle_1, total_1 = await asyncio.to_thread(_read_cpu_times)
    await asyncio.sleep(sample_ms / 1000.0)
    idle_2, total_2 = await asyncio.to_thread(_read_cpu_times)
    delta = total_2 - total_1
    if delta <= 0:
        return 0.0
    return round(max(0.0, min(100.0, 100.0 * (1.0 - (idle_2 - idle_1) / delta))), 1)


def _read_meminfo() -> dict:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    info[key.strip()] = int(parts[0])  # kB
    except (OSError, ValueError):
        return {"percent": 0.0, "used_mb": 0.0, "total_mb": 0.0}
    total_kb = info.get("MemTotal", 0)
    available_kb = info.get("MemAvailable", info.get("MemFree", 0))
    used_kb = max(0, total_kb - available_kb)
    percent = round(100.0 * used_kb / total_kb, 1) if total_kb else 0.0
    return {"percent": percent, "used_mb": round(used_kb / 1024.0, 1), "total_mb": round(total_kb / 1024.0, 1)}


def _storage_info() -> dict:
    try:
        stat = os.statvfs("/")
        total_gb = round(stat.f_blocks * stat.f_frsize / 1024 ** 3, 1)
        free_gb = round(stat.f_bavail * stat.f_frsize / 1024 ** 3, 1)
    except (OSError, AttributeError, ValueError):
        return {"total_gb": 0.0, "free_gb": 0.0, "used_gb": 0.0}
    return {"total_gb": total_gb, "free_gb": free_gb, "used_gb": round(total_gb - free_gb, 1)}


# ---------------------------------------------------------------------------
# Original RONIN endpoints (kept)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/ask_ronin", response_model=AskResponse)
async def ask_ronin(request: AskRequest) -> AskResponse:
    """Full pipeline: Planner -> Intent Router -> Tool Execution (with logs)."""
    return await plan_request(request, CTX)


@app.post("/approve_update", response_model=ApprovalResponse)
async def approve_update(request: ApprovalRequest) -> ApprovalResponse:
    if not request.approved:
        ACTION_LOG.log(f"Update rejected: {request.proposal.file_path}", "warning")
        return ApprovalResponse(accepted=False, success=False, message="Update rejected; no source files changed.")
    workspace = Path(__file__).resolve().parent
    target = Path(request.proposal.file_path).resolve()
    if workspace not in target.parents:
        raise HTTPException(400, "Update target must remain inside RONIN_Brain_Python")

    STATE.set("executing", "Applying approved update...")
    outcome = safe_hot_reload(request.proposal.module_name, str(target), request.proposal.new_code)
    is_success = outcome.get("success") == True or outcome.get("status") == "success"  # noqa: E712
    if is_success:
        ACTION_LOG.log(f"Self-update applied: {request.proposal.module_name}", "success")
    else:
        ACTION_LOG.log(f"Self-update failed: {outcome.get('error')}", "error")
    STATE.set("idle", "Ready. Waiting for your command.")
    return ApprovalResponse(accepted=True, success=is_success,
                            message=outcome.get("error") or outcome.get("message") or "Update applied safely.")


# ---------------------------------------------------------------------------
# VYRX UI API surface
# ---------------------------------------------------------------------------

@app.get("/api/state")
async def api_state() -> dict:
    snapshot = STATE.get()
    snapshot["version"] = BRAIN_VERSION
    snapshot["online"] = True
    return snapshot


@app.get("/api/health")
async def api_health() -> dict:
    cpu_percent = await _sample_cpu_percent()
    memory = _read_meminfo()
    storage = _storage_info()
    try:
        db_bytes = os.path.getsize(DATABASE_PATH)
    except OSError:
        db_bytes = 0
    return {
        "status": "ok",
        "version": BRAIN_VERSION,
        "uptime_seconds": int(time.time() - BOOT_TIME),
        "cpu_percent": cpu_percent,
        "memory_percent": memory["percent"],
        "memory_used_mb": memory["used_mb"],
        "memory_total_mb": memory["total_mb"],
        "storage_total_gb": storage["total_gb"],
        "storage_free_gb": storage["free_gb"],
        "storage_used_gb": storage["used_gb"],
        "memory_db_bytes": db_bytes,
        "python": sys.version.split()[0],
    }


@app.get("/api/action_logs")
async def api_action_logs(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return {"logs": ACTION_LOG.recent(limit)}


@app.get("/api/events")
async def api_events(request: Request) -> StreamingResponse:
    """SSE stream: live state changes + action log entries."""
    events = ACTION_LOG.subscribe()

    async def generate():
        try:
            yield ACTION_LOG.encode_sse({
                "type": "hello",
                "state": STATE.get(),
                "logs": ACTION_LOG.recent(30),
            })
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.to_thread(events.get, True, 15)
                    yield ACTION_LOG.encode_sse(event)
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            ACTION_LOG.unsubscribe(events)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/summary")
async def api_summary() -> dict:
    today = await STATS.today()
    yesterday = await STATS.yesterday()
    tasks_over_time = await STATS.last_7_days()
    top_tools = await STATS.top_tools(5)
    top_tools = [
        {"tool": entry["tool"], "label": tool_label(entry["tool"]), "count": entry["count"], "percent": entry["percent"]}
        for entry in top_tools
    ]
    recent_automations = [
        {"tool": entry["tool"], "label": tool_label(entry["tool"]), "detail": entry["detail"],
         "time": entry["time"], "success": entry["success"], "auto": entry["auto"]}
        for entry in await STATS.recent_events(5)
    ]
    learned_today = int(today.get("learned", 0))
    if learned_today > 0:
        learning = {
            "status": "in_progress",
            "progress": min(100, learned_today * 25),
            "message": f"Consolidating {learned_today} new memor{'y' if learned_today == 1 else 'ies'} from today's interactions.",
        }
    else:
        learning = {
            "status": "idle",
            "progress": 0,
            "message": "Nothing to consolidate yet. New memories will appear here.",
        }
    return {
        "today": today,
        "yesterday": yesterday,
        "tasks_over_time": tasks_over_time,
        "top_tools": top_tools,
        "recent_automations": recent_automations,
        "learning": learning,
        "memory": await MEMORY_ENGINE.stats(),
    }


# --- Memory module (Personal / Experience / Knowledge) ----------------------

@app.get("/api/memory")
async def api_memory_list(
    category: str | None = None,
    search: str | None = None,
    sort: str = "newest",
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    memories = await MEMORY_ENGINE.list(category=category, search=search, sort=sort, limit=limit)
    return {"memories": memories, "stats": await MEMORY_ENGINE.stats()}


@app.post("/api/memory")
async def api_memory_create(req: MemoryRequest) -> dict:
    item = await MEMORY_ENGINE.add(
        category=req.category, title=req.title, content=req.content,
        importance=req.importance, pinned=req.pinned, source=req.source,
        confidence=req.confidence,
    )
    await STATS.bump("learned")
    await STATS.record_tool_usage("note_creator", item.get("title", ""), True)
    ACTION_LOG.log(f"Memory saved: \"{item.get('title', '')}\" ({item.get('category', '')})", "success")
    if STATE.get()["state"] == "idle":
        STATE.set("learning", "Saving new memory...")

        async def _back_to_idle():
            await asyncio.sleep(3)
            if STATE.get()["state"] == "learning":
                STATE.set("idle", "Ready. Waiting for your command.")

        asyncio.create_task(_back_to_idle())
    return item


@app.get("/api/memory/{memory_id}")
async def api_memory_get(memory_id: int) -> dict:
    item = await MEMORY_ENGINE.get(memory_id)
    if item is None:
        raise HTTPException(404, "Memory not found")
    await MEMORY_ENGINE.touch(memory_id)
    return item


@app.post("/api/memory/{memory_id}")
async def api_memory_update(memory_id: int, req: MemoryUpdate) -> dict:
    item = await MEMORY_ENGINE.get(memory_id)
    if item is None:
        raise HTTPException(404, "Memory not found")
    updated = await MEMORY_ENGINE.update(memory_id, req.model_dump(exclude_none=True))
    ACTION_LOG.log(f"Memory updated: \"{updated.get('title', '') if updated else memory_id}\"", "info")
    return updated or item


@app.delete("/api/memory/{memory_id}")
async def api_memory_delete(memory_id: int) -> dict:
    existed = await MEMORY_ENGINE.delete(memory_id)
    if not existed:
        raise HTTPException(404, "Memory not found")
    ACTION_LOG.log(f"Memory deleted (id {memory_id})", "info")
    return {"ok": True}


# --- API providers -----------------------------------------------------------

@app.get("/api/providers")
async def api_providers() -> dict:
    providers = PROVIDER_MANAGER.list_providers()
    active = next((p for p in providers if p["active"]), None)
    if active is not None and active["requests_today"] > 0:
        health_percent = round(100.0 - 100.0 * active["errors_today"] / active["requests_today"], 1)
    else:
        health_percent = 100.0 if active is not None else 0.0
    return {
        "engine": {
            "online": True,
            "mode": "hybrid-multi-provider",
            "auto_failover": True,
            "active": active["name"] if active else None,
            "health": health_percent,
        },
        "providers": providers,
    }


@app.post("/api/providers")
async def api_providers_update(req: ProviderUpdate) -> dict:
    if req.name not in KNOWN_PROVIDERS:
        raise HTTPException(400, f"Unknown provider: {req.name}")
    info = PROVIDER_MANAGER.update(req.name, req.enabled, req.model, req.endpoint)
    ACTION_LOG.log(f"Provider config updated: {req.name} (enabled={info['enabled']})", "info")
    return info


# --- Tools module -------------------------------------------------------------

@app.get("/api/tools")
async def api_tools() -> dict:
    device = CTX.device_status
    custom_configured = any(
        p["configured"] and p["enabled"] for p in PROVIDER_MANAGER.list_providers() if p["name"] == "custom"
    )
    tools = []
    for spec in TOOLS_CATALOG:
        if spec.brain_kind == "server":
            active = True
        elif spec.brain_kind == "server_device":
            active = bool(device.get(spec.device_signal, False))
        elif spec.brain_kind == "custom":
            active = custom_configured
        else:
            active = False
        tools.append({
            "id": spec.id,
            "name": spec.name,
            "category": spec.category,
            "description": spec.description,
            "active": active,
            "reason": tool_reason(spec.id),
        })
    tools_used_today = await _distinct_tools_today()
    return {
        "tools": tools,
        "available": sum(1 for tool in tools if tool["active"]),
        "total": len(tools),
        "device_status": device,
        "usage_today": {
            "tools_used": tools_used_today,
            "success_rate": _usage_success_rate(),
        },
    }


async def _distinct_tools_today() -> int:
    from datetime import datetime
    import aiosqlite
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(DISTINCT tool) FROM tool_usage WHERE date = ?", (today,)
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


def _usage_success_rate() -> int:
    from datetime import datetime
    # Synchronous one-shot read is fine for a small table; wrapped defensively.
    try:
        import sqlite3
        today = datetime.now().strftime("%Y-%m-%d")
        con = sqlite3.connect(str(DATABASE_PATH))
        try:
            cursor = con.execute(
                "SELECT SUM(success), COUNT(*) FROM tool_usage WHERE date = ?", (today,)
            )
            success, total = cursor.fetchone()
        finally:
            con.close()
        if not total:
            return 0
        return int(round(100.0 * (success or 0) / total))
    except sqlite3.Error:
        return 0


@app.post("/api/device_status")
async def api_device_status(req: DeviceStatusRequest) -> dict:
    payload = req.model_dump(exclude_none=True)
    for key in ("accessibility", "notifications", "microphone", "battery_saver_ok"):
        if key in payload:
            CTX.device_status[key] = payload[key]
    if payload.get("body_version"):
        CTX.device_status["body_version"] = payload["body_version"]
    _persist_device_status()
    return {"ok": True, "device_status": CTX.device_status}


# --- Chat feedback -------------------------------------------------------------

@app.post("/api/feedback")
async def api_feedback(req: FeedbackRequest) -> dict:
    label = "positive" if req.rating == "up" else "negative"
    text = f"User feedback: {label}"
    if req.comment:
        text += f" — {req.comment}"
    ACTION_LOG.log(text, "info")
    return {"ok": True}


# --- Live status dashboard (additive — for previewing the Brain in a browser) --

_STATUS_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VYRX Brain — Live Status</title>
<style>
:root{--bg:#05060A;--card:#0E1118;--stroke:#1E2635;--txt:#E8EAF0;--dim:#8A93A6;--faint:#5A6478;
--purple:#8B5CF6;--purple2:#A78BFA;--blue:#38BDF8;--green:#4ADE80;--amber:#FBBF24;--red:#F87171;--log:#34D399}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 ui-sans-serif,system-ui,sans-serif;padding:28px 20px 48px}
.wrap{max-width:980px;margin:0 auto}
header{display:flex;align-items:center;gap:14px;margin-bottom:20px}
.mark{width:46px;height:46px;border-radius:14px;background:radial-gradient(circle at 35% 30%,var(--purple2),var(--purple) 55%,#4C3A85);box-shadow:0 0 24px rgba(139,92,246,.45)}
h1{font-size:20px;letter-spacing:3px}
.sub{color:var(--dim);font-size:12px}
.dot{margin-left:auto;display:flex;align-items:center;gap:7px;color:var(--dim);font-size:12px}
.dot i{width:9px;height:9px;border-radius:50%;background:var(--red);box-shadow:0 0 10px var(--red)}
.dot.on i{background:var(--green);box-shadow:0 0 10px var(--green)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--stroke);border-radius:20px;padding:18px}
.card h2{font-size:10px;letter-spacing:1.6px;color:var(--dim);text-transform:uppercase;margin-bottom:12px}
.state{display:flex;gap:14px;align-items:center}
.orb{width:64px;height:64px;border-radius:50%;flex:none;animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.07)}}
.state .name{font-size:17px;font-weight:700}
.state .msg{color:var(--dim);font-size:12px;margin-top:3px}
.kv{margin-top:12px;display:flex;justify-content:space-between;font-size:12px;color:var(--dim)}
.kv b{color:var(--purple2);font-weight:600}
.bar{height:7px;border-radius:4px;background:#161D2A;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;border-radius:4px;background:linear-gradient(90deg,var(--green),var(--blue))}
.metric{margin-top:10px}
.metric .row{display:flex;justify-content:space-between;font-size:12px;color:var(--dim)}
.metrics{grid-column:1/-1}
.counters{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;grid-column:1/-1}
.cnt{background:#0B0F17;border:1px solid var(--stroke);border-radius:14px;padding:12px;text-align:center}
.cnt b{display:block;font-size:20px;color:var(--txt)}
.cnt span{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
#log{grid-column:1/-1;font:12px/1.7 ui-monospace,Menlo,Consolas,monospace;max-height:260px;overflow:auto}
#log div{white-space:pre-wrap;word-break:break-word}
#log .t{color:#3E9C6E}#log .success{color:var(--log)}#log .error{color:var(--red)}
#log .tool{color:var(--blue)}#log .info{color:var(--log)}
.off{color:var(--faint);font-size:12px}
</style></head><body><div class="wrap">
<header><div class="mark"></div><div><h1>VYRX BRAIN</h1><div class="sub" id="ver">v? • FastAPI • 127.0.0.1:8000</div></div>
<div class="dot" id="pdot"><i></i><span id="ptxt">connecting…</span></div></header>
<div class="grid">
 <div class="card"><h2>AI State</h2><div class="state">
   <div class="orb" id="orb" style="background:radial-gradient(circle at 35% 30%,#c4b5fd,#38BDF8 60%,#0b1e3a)"></div>
   <div><div class="name" id="sname" style="color:var(--blue)">…</div><div class="msg" id="smsg">waking up…</div></div>
 </div><div class="kv"><span>Active model</span><b id="smodel">—</b></div>
 <div class="kv"><span>Response</span><b id="sms">—</b></div></div>
 <div class="card metrics"><h2>System Health</h2>
   <div class="metric"><div class="row"><span>CPU</span><span id="cpu">—</span></div><div class="bar"><i id="cpub" style="width:0%"></i></div></div>
   <div class="metric"><div class="row"><span>Memory (RAM)</span><span id="ram">—</span></div><div class="bar"><i id="ramb" style="width:0%"></i></div></div>
   <div class="metric"><div class="row"><span>Storage</span><span id="sto">—</span></div><div class="bar"><i id="stob" style="width:0%"></i></div></div>
   <div class="kv"><span>Uptime</span><b id="up">—</b></div><div class="kv"><span>Python</span><b id="py">—</b></div></div>
 <div class="card" style="grid-column:1/-1"><h2>Today's Activity</h2>
   <div class="counters" id="cnt"></div></div>
 <div class="card"><h2>Live Action Log <span style="color:var(--faint)">— SSE stream</span></h2><div id="log"><div class="off">waiting for events…</div></div></div>
</div></div>
<script>
const ORB={idle:["#c4b5fd","#38BDF8","#0b1e3a","#38BDF8","Ready"],listening:["#bae6fd","#38BDF8","#0b1e3a","#38BDF8","Listening"],
thinking:["#ddd6fe","#8B5CF6","#2a1a4a","#A78BFA","Thinking"],executing:["#bbf7d0","#4ADE80","#0a2e1a","#4ADE80","Executing"],
learning:["#fde68a","#FBBF24","#3a2a08","#FBBF24","Learning"]};
const $=id=>document.getElementById(id);
async function j(u){try{const r=await fetch(u);return r.ok?r.json():null}catch(e){return null}}
async function tick(){
 const s=await j("/api/state"),h=await j("/api/health"),sum=await j("/api/summary");
 const pdot=$("pdot");pdot.classList.toggle("on",!!s);$("ptxt").textContent=s?"online":"offline";
 if(s){$("ver").textContent="v"+(s.version||"?")+" • FastAPI • 127.0.0.1:8000";
  const c=ORB[s.state]||ORB.idle;$("orb").style.background="radial-gradient(circle at 35% 30%,"+c[0]+","+c[1]+" 60%,"+c[2]+")";
  $("orb").style.boxShadow="0 0 34px "+c[1]+"66";$("sname").textContent=c[4];$("sname").style.color=c[3];
  $("smsg").textContent=s.message||"";$("smodel").textContent=s.model||s.provider||"—";
  $("sms").textContent=s.response_ms!=null?(s.response_ms/1000).toFixed(2)+" s":"—";}
 if(h){$("cpu").textContent=Math.round(h.cpu_percent)+"%";$("cpub").style.width=h.cpu_percent+"%";
  $("ram").textContent=Math.round(h.memory_percent)+"% ("+Math.round(h.memory_used_mb)+" MB)";$("ramb").style.width=h.memory_percent+"%";
  const sp=h.storage_total_gb?h.storage_used_gb/h.storage_total_gb*100:0;
  $("sto").textContent=h.storage_used_gb+" / "+h.storage_total_gb+" GB";$("stob").style.width=sp+"%";
  const u=h.uptime_seconds;$("up").textContent=(u>=3600?Math.floor(u/3600)+"h ":"")+(Math.floor(u%3600/60))+"m "+(u%60)+"s";
  $("py").textContent=h.python||"—";}
 if(sum&&sum.today){const t=sum.today;
  $("cnt").innerHTML=[["Tasks",t.tasks_completed],["Auto tasks",t.auto_tasks],["Learned",t.learned],
   ["Voice",t.voice_commands],["Apps opened",t.apps_opened],["Web searches",t.web_searches]]
   .map(x=>'<div class="cnt"><b>'+x[1]+'</b><span>'+x[0]+'</span></div>').join("");}
}
const logEl=$("log");
function addLog(e){if(logEl.firstElementChild&&logEl.firstElementChild.className==="off")logEl.innerHTML="";
 const d=document.createElement("div");
 d.innerHTML='<span class="t">['+e.time+']</span> <span class="'+(e.level||"info")+'">'+e.text.replace(/</g,"&lt;")+'</span>';
 logEl.appendChild(d);while(logEl.children.length>200)logEl.firstChild.remove();logEl.scrollTop=logEl.scrollHeight;}
function es(){try{const s=new EventSource("/api/events");
 s.addEventListener("hello",ev=>{const o=JSON.parse(ev.data);(o.logs||[]).forEach(addLog);});
 s.addEventListener("log",ev=>addLog(JSON.parse(ev.data).log||{}));
 s.onerror=()=>{s.close();setTimeout(es,5000);};}catch(e){setTimeout(es,5000);}}
tick();setInterval(tick,5000);es();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def status_dashboard() -> HTMLResponse:
    return HTMLResponse(_STATUS_HTML)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, log_level="info")

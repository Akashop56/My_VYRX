"""Runtime provider registry: enable flags, model overrides, live metrics.

API keys are NEVER stored here — they stay encrypted on the Android device
and ride in each ``/ask_ronin`` request. The Brain only remembers whether a
key has been seen, plus request/latency/success metrics per day.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

KNOWN_PROVIDERS = ("groq", "gemini", "openai", "openrouter", "custom")

ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "openrouter": "openai/gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "custom": None,
    "gemini": "gemini-1.5-flash",
}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class ProviderManager:
    def __init__(self, state_path: Path) -> None:
        self._path = Path(state_path)
        self._lock = threading.Lock()
        self._state: dict = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        except OSError:
            pass  # metrics persistence is best-effort

    def _entry(self, name: str) -> dict:
        entry = self._state.get(name)
        if not isinstance(entry, dict):
            entry = {"enabled": True, "model": None, "endpoint": None, "seen_key": False, "metrics": {}}
            self._state[name] = entry
        return entry

    # -- runtime updates ----------------------------------------------------
    def mark_keys(self, providers: list[dict]) -> None:
        """Remember (without storing) which providers delivered a key today."""
        with self._lock:
            for provider in providers:
                name = str(provider.get("provider") or "").lower()
                if name in KNOWN_PROVIDERS and str(provider.get("api_key") or "").strip():
                    entry = self._entry(name)
                    if provider.get("endpoint") and name == "custom":
                        entry["endpoint"] = str(provider["endpoint"])
                    if provider.get("model"):
                        entry.setdefault("seen_model", provider["model"])
                    entry["seen_key"] = True
            self._save()

    def update(self, name: str, enabled: bool | None = None, model: str | None = None,
               endpoint: str | None = None) -> dict:
        with self._lock:
            entry = self._entry(name)
            if enabled is not None:
                entry["enabled"] = bool(enabled)
            if model is not None:
                entry["model"] = model
            if endpoint is not None:
                entry["endpoint"] = endpoint
            self._save()
            return self._public(name)

    def record(self, name: str, latency_ms: int | None, success: bool) -> None:
        today = _today()
        with self._lock:
            entry = self._entry(name)
            metrics = entry.setdefault("metrics", {})
            # keep only the last 7 days of metrics
            for key in [k for k in metrics if k < _days_ago(6)]:
                metrics.pop(key, None)
            day = metrics.setdefault(today, {"requests": 0, "errors": 0, "last_latency_ms": None,
                                             "total_latency_ms": 0})
            day["requests"] = int(day.get("requests", 0)) + 1
            if not success:
                day["errors"] = int(day.get("errors", 0)) + 1
            if latency_ms is not None and latency_ms >= 0:
                day["last_latency_ms"] = int(latency_ms)
                day["total_latency_ms"] = int(day.get("total_latency_ms", 0)) + int(latency_ms)
            self._save()

    def active_name(self, provided: list[dict]) -> str | None:
        """First enabled provider from the current request payload (or env fallback)."""
        for provider in provided:
            name = str(provider.get("provider") or "").lower()
            if name not in KNOWN_PROVIDERS:
                continue
            if not str(provider.get("api_key") or "").strip():
                continue
            with self._lock:
                if not self._entry(name).get("enabled", True):
                    continue
            return name
        with self._lock:
            for name in KNOWN_PROVIDERS:
                if os.getenv(ENV_KEYS.get(name, ""), "") and self._entry(name).get("enabled", True):
                    return name
        return None

    def model_for(self, name: str) -> str | None:
        with self._lock:
            entry = self._entry(name)
            return entry.get("model") or entry.get("seen_model") or DEFAULT_MODELS.get(name)

    # -- API surface ---------------------------------------------------------
    def list_providers(self, provided: list[dict] | None = None) -> list[dict]:
        provided = provided or []
        provided_names = {str(p.get("provider") or "").lower() for p in provided if p.get("api_key")}
        active = self.active_name(provided)
        with self._lock:
            snapshot = {name: json.loads(json.dumps(self._state.get(name) or {})) for name in KNOWN_PROVIDERS}
        out: list[dict] = []
        for name in KNOWN_PROVIDERS:
            entry = snapshot.get(name, {})
            out.append(self._public(name, entry, bool(entry.get("seen_key") or name in provided_names
                                                      or bool(os.getenv(ENV_KEYS.get(name, ""), ""))), active))
        return out

    def _public(self, name: str, entry: dict | None = None, configured: bool | None = None,
                active: str | None = None) -> dict:
        entry = entry if isinstance(entry, dict) else self._state.get(name) or {}
        metrics = entry.get("metrics") or {}
        today = metrics.get(_today(), {})
        requests_today = int(today.get("requests", 0))
        errors_today = int(today.get("errors", 0))
        total_latency = int(today.get("total_latency_ms", 0))
        if configured is None:
            configured = bool(entry.get("seen_key") or bool(os.getenv(ENV_KEYS.get(name, ""), "")))
        return {
            "name": name,
            "label": name.capitalize(),
            "configured": bool(configured),
            "enabled": bool(entry.get("enabled", True)),
            "active": (active == name) if active is not None else False,
            "model": entry.get("model") or entry.get("seen_model") or DEFAULT_MODELS.get(name),
            "latency_ms": int(today.get("last_latency_ms")) if today.get("last_latency_ms") else None,
            "avg_latency_ms": round(total_latency / requests_today) if requests_today else None,
            "requests_today": requests_today,
            "errors_today": errors_today,
            "success_rate": round(100.0 * (requests_today - errors_today) / requests_today, 1) if requests_today else None,
        }


def _days_ago(n: int) -> str:
    return (datetime.now() - _timedelta(days=n)).strftime("%Y-%m-%d")


def _timedelta(**kwargs):
    from datetime import timedelta
    return timedelta(**kwargs)

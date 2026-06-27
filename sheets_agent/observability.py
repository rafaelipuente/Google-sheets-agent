"""Observability: record LLM calls, tool calls, token usage, and cost.

Events are appended as JSONL so the `usage` CLI view can aggregate across runs
without a database. Cost is computed from a per-model pricing table (USD per
1M tokens); prices change, so PRICING is easy to override.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from . import config

# USD per 1,000,000 tokens. Update as provider pricing changes.
PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "o3": {"input": 2.00, "output": 8.00},
}


def compute_cost(model: str | None, prompt_tokens: int, completion_tokens: int) -> float:
    """Cost in USD for one call. Unknown models cost 0 (flagged elsewhere)."""
    rates = PRICING.get(model or "")
    if not rates:
        return 0.0
    return round(
        prompt_tokens / 1_000_000 * rates["input"]
        + completion_tokens / 1_000_000 * rates["output"],
        6,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UsageTracker:
    """Append-only recorder for one agent session.

    path=None keeps events in memory only (used by tests).
    """

    def __init__(self, path: str | None = None, in_memory: bool = False) -> None:
        self.session_id = uuid.uuid4().hex[:12]
        self.in_memory = in_memory
        self.path = None if in_memory else (path or config.USAGE_LOG_PATH)
        self.events: list[dict] = []

    def _write(self, event: dict) -> None:
        event["ts"] = _now()
        event["session"] = self.session_id
        self.events.append(event)
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")

    def record_llm_call(
        self,
        model: str | None,
        usage: dict | None,
        latency_ms: float,
        num_tool_calls: int,
    ) -> dict:
        usage = usage or {}
        prompt = int(usage.get("prompt_tokens", 0))
        completion = int(usage.get("completion_tokens", 0))
        total = int(usage.get("total_tokens", prompt + completion))
        cost = compute_cost(model, prompt, completion)
        event = {
            "type": "llm_call",
            "model": model,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "cost_usd": cost,
            "latency_ms": round(latency_ms, 1),
            "num_tool_calls": num_tool_calls,
            "priced": model in PRICING,
        }
        self._write(event)
        return event

    def record_tool_call(
        self, tool: str, duration_ms: float, ok: bool, error: str | None = None
    ) -> dict:
        event = {
            "type": "tool_call",
            "tool": tool,
            "duration_ms": round(duration_ms, 1),
            "ok": ok,
            "error": error,
        }
        self._write(event)
        return event

    def record_system_step(
        self, step: str, duration_ms: float, detail: str | None = None
    ) -> dict:
        """A step the agent runs directly each turn (e.g. the forced
        get_sheet_structure read), distinct from a model-emitted tool call."""
        event = {
            "type": "system_step",
            "step": step,
            "duration_ms": round(duration_ms, 1),
            "detail": detail,
        }
        self._write(event)
        return event


class timer:
    """Context manager returning elapsed milliseconds via .ms."""

    def __enter__(self) -> "timer":
        self._start = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self.ms = (time.perf_counter() - self._start) * 1000.0


def load_events(path: str | None = None) -> list[dict]:
    path = path or config.USAGE_LOG_PATH
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def summarize(events: list[dict]) -> dict:
    """Aggregate events into totals and per-model / per-tool breakdowns."""
    llm = [e for e in events if e.get("type") == "llm_call"]
    tools = [e for e in events if e.get("type") == "tool_call"]
    steps = [e for e in events if e.get("type") == "system_step"]

    by_model: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    )
    for e in llm:
        m = by_model[e.get("model") or "unknown"]
        m["calls"] += 1
        m["prompt_tokens"] += e.get("prompt_tokens", 0)
        m["completion_tokens"] += e.get("completion_tokens", 0)
        m["cost_usd"] = round(m["cost_usd"] + e.get("cost_usd", 0.0), 6)

    by_tool: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "errors": 0, "duration_ms": 0.0}
    )
    for e in tools:
        t = by_tool[e.get("tool") or "unknown"]
        t["calls"] += 1
        t["errors"] += 0 if e.get("ok", True) else 1
        t["duration_ms"] = round(t["duration_ms"] + e.get("duration_ms", 0.0), 1)

    by_step: dict[str, dict] = defaultdict(lambda: {"count": 0, "duration_ms": 0.0})
    for e in steps:
        s = by_step[e.get("step") or "unknown"]
        s["count"] += 1
        s["duration_ms"] = round(s["duration_ms"] + e.get("duration_ms", 0.0), 1)

    sessions = {e.get("session") for e in events}
    return {
        "sessions": len([s for s in sessions if s]),
        "llm_calls": len(llm),
        "model_tool_calls": len(tools),
        "system_steps": len(steps),
        "total_prompt_tokens": sum(e.get("prompt_tokens", 0) for e in llm),
        "total_completion_tokens": sum(e.get("completion_tokens", 0) for e in llm),
        "total_tokens": sum(e.get("total_tokens", 0) for e in llm),
        "total_cost_usd": round(sum(e.get("cost_usd", 0.0) for e in llm), 6),
        "by_model": {k: v for k, v in by_model.items()},
        "by_tool": {k: v for k, v in by_tool.items()},
        "by_system_step": {k: v for k, v in by_step.items()},
    }

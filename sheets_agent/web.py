"""Minimal local web chat UI over the existing agent loop.

Run:  uvicorn sheets_agent.web:app --reload   (then open http://localhost:8000)

A single persistent Agent instance holds conversation state on the backend, so
multi-turn flows (e.g. confirming a column delete in a follow-up "yes") work.
The agent logic is reused as-is; this module only wraps it in HTTP.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config

app = FastAPI(title="Sheets Agent")

_INDEX = os.path.join(os.path.dirname(__file__), "static", "index.html")
_SHEET_MARKER = "\n\nSheet: "

# Lazily-built singleton so the server can start even before a request, and so
# conversation state persists across requests for this single user.
_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        from .agent import Agent

        _agent = Agent()
    return _agent


class ChatIn(BaseModel):
    message: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_INDEX)


@app.post("/chat")
def chat(body: ChatIn) -> dict:
    try:
        agent = _get_agent()
        raw = agent.send(body.message)
        sheet_url = config.sheet_url(agent.tools.client.sheet_id)
    except Exception as exc:
        return {"reply": f"Error: {exc}", "sheet_url": config.sheet_url()}

    reply = raw
    if _SHEET_MARKER in raw:
        reply, sheet_url = raw.rsplit(_SHEET_MARKER, 1)
    return {"reply": reply.strip(), "sheet_url": sheet_url}

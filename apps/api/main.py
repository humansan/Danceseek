"""Danceseek API — FastAPI over the SoundSeek pipeline + Neon cache.

Read endpoints (browse + detail + search) plus the add flow: POST enqueues a
background `process` job (the worker does fetch -> normalize -> resolve) and
returns instantly; clients watch progress over SSE. Export lands in a later
phase.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import soundseek.config  # noqa: F401 — loads .env (DATABASE_URL etc.)
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from soundseek import db
from soundseek.models import ParserInfo, Setlist

# SSE safety cap: at ~1s/iteration this bounds a stream to ~20 min even if a
# client never disconnects and a job never terminates.
_SSE_MAX_ITERS = 1200
_RERESOLVE_GATE_DAYS = 30

app = FastAPI(title="Danceseek API", version="0.1.0")

# Next.js dev server + (later) the deployed web origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SetlistSummary(BaseModel):
    """Lightweight browse-card row."""

    id: str
    title: str | None
    dj_names: list[str]
    event: str | None
    date_recorded: str | None
    genres: list[str]
    media_url: str | None
    track_count: int
    status: str
    coverage: dict | None
    created_at: str | None


class SetlistDetail(BaseModel):
    """Full setlist plus its resolution lifecycle metadata."""

    setlist: Setlist
    status: str
    coverage: dict | None
    resolved_at: str | None
    created_at: str | None


class AddSetlistRequest(BaseModel):
    url: str


class AddSetlistResponse(BaseModel):
    id: str
    status: str


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/setlists", response_model=list[SetlistSummary])
def list_setlists(
    q: str | None = None,
    dj: str | None = None,
    genre: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[SetlistSummary]:
    rows = db.list_summaries(limit=limit, offset=offset, q=q, dj=dj, genre=genre)
    return [SetlistSummary(**row) for row in rows]


@app.get("/setlists/{setlist_id}", response_model=SetlistDetail)
def get_setlist(setlist_id: str) -> SetlistDetail:
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, meta = result
    return SetlistDetail(setlist=setlist, **meta)


@app.post("/setlists", response_model=AddSetlistResponse, status_code=201)
def add_setlist(body: AddSetlistRequest, response: Response) -> AddSetlistResponse:
    """Add a 1001TL URL. Returns instantly; the worker resolves in background.

    Idempotent on source_url: re-adding a known URL returns its existing row
    (and current status) instead of re-enqueuing.
    """
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="url is required")

    existing = db.get_by_url(url)
    if existing is not None:
        setlist, meta = existing
        response.status_code = 200
        return AddSetlistResponse(id=setlist.id, status=meta["status"])

    stub = Setlist(source_url=url, parser=ParserInfo(model="pending"), tracks=[])
    db.upsert_content(stub, status="normalizing")
    db.enqueue("process", stub.id)
    return AddSetlistResponse(id=stub.id, status="normalizing")


def _progress(setlist: Setlist, meta: dict) -> dict:
    total = len(setlist.tracks)
    done = sum(1 for t in setlist.tracks if t.resolution is not None)
    return {
        "status": meta["status"],
        "coverage": meta["coverage"],
        "resolved": done,
        "total": total,
    }


@app.get("/setlists/{setlist_id}/events")
async def setlist_events(setlist_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: poll the row ~1s and emit progress until it settles."""
    if await asyncio.to_thread(db.get_by_id, setlist_id) is None:
        raise HTTPException(status_code=404, detail="setlist not found")

    async def gen():
        last = None
        for _ in range(_SSE_MAX_ITERS):
            if await request.is_disconnected():
                break
            result = await asyncio.to_thread(db.get_by_id, setlist_id)
            if result is None:
                break
            setlist, meta = result
            payload = _progress(setlist, meta)
            if payload != last:
                yield f"data: {json.dumps(payload)}\n\n"
                last = payload
            if meta["status"] in ("resolved", "failed"):
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _within_days(iso: str | None, days: int) -> bool:
    if not iso:
        return False
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt < timedelta(days=days)


@app.post("/setlists/{setlist_id}/resolve", status_code=202)
def reresolve_setlist(setlist_id: str) -> dict:
    """Month-gated re-resolve: retry unmatched slots (newly-released tracks)."""
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    _setlist, meta = result
    if _within_days(meta.get("resolved_at"), _RERESOLVE_GATE_DAYS):
        raise HTTPException(
            status_code=409,
            detail=f"re-resolve is available {_RERESOLVE_GATE_DAYS} days after the last resolve",
        )
    db.set_status(setlist_id, "resolving")
    db.enqueue("reresolve", setlist_id)
    return {"id": setlist_id, "status": "resolving"}

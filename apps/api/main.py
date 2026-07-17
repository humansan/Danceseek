"""Danceseek API — FastAPI over the SoundSeek pipeline + Neon cache.

Vertical-slice scope: read endpoints (browse + detail + search) served from the
Neon `setlists` table via soundseek.db. Add/resolve/export land in later phases.
"""

from __future__ import annotations

import soundseek.config  # noqa: F401 — loads .env (DATABASE_URL etc.)
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from soundseek import db
from soundseek.models import Setlist

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

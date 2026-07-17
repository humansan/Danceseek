"""Danceseek API — FastAPI over the SoundSeek pipeline & Neon.

Vertical slice: browse and read setlists straight from the Neon repository
(`soundseek.db`). Later phases add `POST /setlists` (add), background resolve,
SSE progress, auth, and export.
"""

from __future__ import annotations

import soundseek.config  # noqa: F401 — loads .env (DATABASE_URL etc.)
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from soundseek import db
from soundseek.models import Setlist

app = FastAPI(title="Danceseek API", version="0.1.0")

# Next.js dev server + local origins. Tighten in production (Phase 8).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/setlists")
def list_setlists(
    limit: int = Query(50, le=100),
    offset: int = 0,
    q: str | None = None,
    dj: str | None = None,
    genre: str | None = None,
) -> dict:
    """Browse cards: lightweight summaries, newest first, optional filters."""
    items = db.list_summaries(limit=limit, offset=offset, q=q, dj=dj, genre=genre)
    return {"items": items, "count": len(items)}


@app.get("/setlists/{setlist_id}")
def get_setlist(setlist_id: str) -> dict:
    """Full setlist (tracks + resolutions) plus lifecycle meta (status/coverage)."""
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, meta = result
    return {"setlist": setlist.model_dump(mode="json"), "meta": meta}


@app.get("/setlists/by-url")
def get_setlist_by_url(url: str) -> dict:
    result = db.get_by_url(url)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, meta = result
    return {"setlist": setlist.model_dump(mode="json"), "meta": meta}

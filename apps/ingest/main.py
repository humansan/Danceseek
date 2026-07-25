"""Local ingest console — the maintainer's tool for adding sets.

Deliberately separate from the public API (`apps/api`), which stays a thin
read layer over Neon. All the heavy machinery lives here: the browser, the
LLM, the platform searches. Run it on your own machine:

    uv run --group api python -m apps.ingest.main
    # -> http://127.0.0.1:8020

It binds to loopback only and has no auth, because it never leaves your
machine. Do not deploy it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import soundseek.config  # noqa: F401 — loads .env (DATABASE_URL etc.) first

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from soundseek import db, edit
from soundseek import ingest as runner  # the shared job runner (CLI uses it too)
from soundseek.fetcher import FetchError, validate_url
from soundseek.pipeline import ManualInputError, youtube_watch_url

STATIC = Path(__file__).parent / "static"

MODES = ("ingest", "reresolve", "manual")

app = FastAPI(title="SoundSeek Ingest Console", version="0.1.0")
jobs = runner.JobStore()


class IngestRequest(BaseModel):
    url: str
    lastfm_only: bool = False
    force: bool = False
    mode: str = "ingest"  # "ingest" | "reresolve" | "manual"
    # manual mode: the YouTube link goes in `url`, and these carry the rest.
    title: str | None = None
    tracklist: str | None = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/jobs", status_code=201)
def create_job(body: IngestRequest) -> dict:
    url = body.url.strip()
    if body.mode not in MODES:
        raise HTTPException(
            status_code=422, detail=f"mode must be one of {', '.join(MODES)}"
        )

    if body.mode == "manual":
        try:
            url = youtube_watch_url(url)
        except ManualInputError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if not (body.tracklist or "").strip():
            raise HTTPException(status_code=422, detail="a manual set needs tracklist text")
    else:
        try:
            validate_url(url)
        except FetchError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

    job = jobs.create(
        url=url,
        platforms=runner.normalize_platforms(body.lastfm_only),
        force=body.force,
        mode=body.mode,
        manual_text=body.tracklist,
        manual_title=(body.title or "").strip() or None,
    )
    runner.start(job)
    return job.snapshot()


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [j.snapshot() for j in jobs.recent()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {**job.snapshot(), "events": job.events}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """SSE: replay this job's log from the start, then follow it live."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def gen():
        sent = 0
        last_status = None
        while True:
            while sent < len(job.events):
                yield f"event: log\ndata: {json.dumps(job.events[sent])}\n\n"
                sent += 1
            if job.status != last_status:
                yield f"event: status\ndata: {json.dumps(job.snapshot())}\n\n"
                last_status = job.status
            if job.status in ("done", "failed") and sent >= len(job.events):
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/setlists")
def list_setlists(limit: int = 30) -> list[dict]:
    """What's already in the catalog, so the console shows the current state."""
    return db.list_summaries(limit=limit)


@app.get("/api/setlists/{setlist_id}")
def get_setlist(setlist_id: str) -> dict:
    """The full tracklist, for the results table shown after a run."""
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, meta = result
    return {"setlist": setlist.model_dump(mode="json"), **meta}


@app.patch("/api/setlists/{setlist_id}")
def edit_setlist(setlist_id: str, body: edit.SetlistEdit) -> dict:
    """Save the results table after the maintainer has corrected it.

    The whole table is submitted, not a diff: rows left out are deleted, and
    rows with a null position are new. See `soundseek.edit` for what an edit
    does to the resolutions that were stamped on the old values.
    """
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, meta = result

    try:
        edited = edit.apply_edits(setlist, body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    coverage = edit.save_edited(edited, meta)
    return {
        "setlist": edited.model_dump(mode="json"),
        **{**meta, "coverage": coverage},
    }


def main() -> None:
    import uvicorn

    print("Ingest console: http://127.0.0.1:8020")
    uvicorn.run(app, host="127.0.0.1", port=8020, log_level="warning")


if __name__ == "__main__":
    main()

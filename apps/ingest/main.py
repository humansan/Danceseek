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

from soundseek import db
from soundseek import ingest as runner  # the shared job runner (CLI uses it too)
from soundseek.fetcher import FetchError, validate_url

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="SoundSeek Ingest Console", version="0.1.0")
jobs = runner.JobStore()


class IngestRequest(BaseModel):
    url: str
    lastfm_only: bool = False
    force: bool = False
    mode: str = "ingest"  # "ingest" | "reresolve"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/jobs", status_code=201)
def create_job(body: IngestRequest) -> dict:
    url = body.url.strip()
    try:
        validate_url(url)
    except FetchError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if body.mode not in ("ingest", "reresolve"):
        raise HTTPException(status_code=422, detail="mode must be 'ingest' or 'reresolve'")

    job = jobs.create(
        url=url,
        platforms=runner.normalize_platforms(body.lastfm_only),
        force=body.force,
        mode=body.mode,
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


def main() -> None:
    import uvicorn

    print("Ingest console: http://127.0.0.1:8020")
    uvicorn.run(app, host="127.0.0.1", port=8020, log_level="warning")


if __name__ == "__main__":
    main()

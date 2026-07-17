"""Background resolve worker.

Polls the Neon `jobs` table (FOR UPDATE SKIP LOCKED) and runs the pipeline that
would otherwise block an HTTP request:

  process   — a freshly added set: fetch -> normalize -> resolve, then stamp
              coverage. The job's setlist_id is a stub row the API already
              handed to the client, so we reuse that id (never mint a new one).
  reresolve — a month-gated re-run: clear the no_match/partial slots (so they
              re-enter the pending set) and resolve again, catching tracks that
              have since been released.

Run it as its own process (separate from the API):
    uv run --group api python -m apps.api.worker
"""

from __future__ import annotations

import sys
import time

# Track/artist names carry non-Latin-1 characters (Ørjan, Beyoncé, →); the
# Windows console defaults to cp1252 and would crash on them.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import soundseek.config  # noqa: F401 — loads .env (DATABASE_URL etc.) first

from soundseek import db, pipeline, store
from soundseek.resolver.resolve import build_coverage, resolve_setlist

_RETRYABLE_STATUSES = ("no_match", "partial")


def _log(msg: str) -> None:
    print(f"worker: {msg}", flush=True)


def _load_setlist(setlist_id: str):
    result = db.get_by_id(setlist_id)
    if result is None:
        raise RuntimeError(f"setlist {setlist_id} not found")
    return result[0]  # (Setlist, meta) -> Setlist


def _process(job: dict) -> None:
    """New add: normalize the stub's URL under its id, then resolve."""
    setlist_id = job["setlist_id"]
    stub = _load_setlist(setlist_id)

    db.set_status(setlist_id, "normalizing")
    setlist = pipeline.build_setlist(stub.source_url)
    setlist.id = setlist_id  # keep the id the client is already polling
    store.save(setlist)
    db.set_status(setlist_id, "resolving")

    summary = resolve_setlist(setlist)
    db.set_coverage(setlist_id, build_coverage(setlist, summary), status="resolved")
    _log(f"resolved {setlist_id}: {summary.resolved} matched, {summary.no_match} no_match")


def _reresolve(job: dict) -> None:
    """Retry previously unmatched/partial slots on an already-resolved set."""
    setlist_id = job["setlist_id"]
    setlist = _load_setlist(setlist_id)

    cleared = 0
    for track in setlist.tracks:
        if track.resolution and track.resolution.status in _RETRYABLE_STATUSES:
            track.resolution = None
            cleared += 1
        for component in track.mashup_components:
            if component.resolution and component.resolution.status in _RETRYABLE_STATUSES:
                component.resolution = None
                cleared += 1

    db.set_status(setlist_id, "resolving")
    store.save(setlist)  # persist cleared slots so a crash mid-run retries them

    summary = resolve_setlist(setlist)
    db.set_coverage(setlist_id, build_coverage(setlist, summary), status="resolved")
    _log(f"re-resolved {setlist_id}: {cleared} slots retried, {summary.resolved} matched")


HANDLERS = {"process": _process, "reresolve": _reresolve}


def run(poll_interval: float = 2.0) -> None:
    _log("started; polling for jobs (Ctrl-C to stop)")
    while True:
        job = db.claim_job()
        if job is None:
            time.sleep(poll_interval)
            continue

        handler = HANDLERS.get(job["type"])
        if handler is None:
            db.fail_job(job["id"], f"unknown job type: {job['type']}")
            _log(f"job {job['id']} skipped: unknown type {job['type']!r}")
            continue

        _log(f"claimed job {job['id']} ({job['type']}) for setlist {job['setlist_id']}")
        try:
            handler(job)
            db.complete_job(job["id"])
        except Exception as e:  # never let one bad job kill the loop
            if job.get("setlist_id"):
                try:
                    db.set_status(job["setlist_id"], "failed")
                except Exception:
                    pass
            db.fail_job(job["id"], repr(e))
            _log(f"job {job['id']} failed: {e!r}")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        _log("stopped")

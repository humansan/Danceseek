"""The ingest pipeline as a background job, with a progress stream.

Drives capture → normalize → resolve for one URL and records what happened, so
both front-ends can share it: the local console (`apps/ingest`) streams the
events to a browser, and `soundseek publish` prints them to a terminal.

This runs on the **maintainer's machine**, never on the deployed server:
scraping 1001tracklists needs a real browser with a Cloudflare-cleared
profile, and normalizing needs the LLM key. Everything here writes straight to
Neon, so the web API stays a thin read layer over the results.

  capture   fetch the page with the local browser (disk-cached), store the HTML
            in Postgres so it can be re-normalized later without re-scraping
  normalize extract the DOM rows, then LLM-normalize them into tracks
  resolve   match tracks against the platforms (all three, or Last.fm only)

A set that 1001tracklists does not have can be added by hand instead (`manual`
mode): a YouTube link, a title, and the tracklist text copied out of the
video's chapters or comments. That skips capture entirely — the pasted text is
the source — and joins the same normalize → resolve path.

Jobs are held in memory: this is a single-user local tool, so a job queue
table would be ceremony. The results are what's durable.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from . import config as _config  # noqa: F401 — loads .env (DATABASE_URL etc.) first
from . import db, fetcher, pipeline, store
from .resolver.resolve import ALL_PLATFORMS, LASTFM_ONLY, build_coverage, resolve_setlist

_RETRYABLE_STATUSES = ("no_match", "partial")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """One ingest run. `events` is append-only so a reconnecting client can replay."""

    id: str
    url: str
    platforms: list[str]
    force: bool
    mode: str = "ingest"  # "ingest" | "reresolve" | "manual"
    # manual mode only: the pasted tracklist and the title to file it under.
    manual_text: str | None = None
    manual_title: str | None = None
    status: str = "queued"  # queued | capturing | normalizing | resolving | done | failed
    setlist_id: str | None = None
    title: str | None = None
    error: str | None = None
    coverage: dict[str, Any] | None = None
    progress: tuple[int, int] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)

    def emit(self, message: str, level: str = "info", **extra: Any) -> None:
        self.events.append({"ts": _now(), "level": level, "message": message, **extra})

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "platforms": self.platforms,
            "mode": self.mode,
            "status": self.status,
            "setlist_id": self.setlist_id,
            "title": self.title,
            "error": self.error,
            "coverage": self.coverage,
            "done": self.progress[0] if self.progress else None,
            "total": self.progress[1] if self.progress else None,
            "created_at": self.created_at,
        }


class JobStore:
    """In-memory job registry (newest first), safe across the worker threads."""

    def __init__(self, keep: int = 50) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._keep = keep
        self._lock = threading.Lock()

    def create(
        self,
        url: str,
        platforms: list[str],
        force: bool,
        mode: str = "ingest",
        manual_text: str | None = None,
        manual_title: str | None = None,
    ) -> Job:
        job = Job(
            id=str(uuid.uuid4()), url=url, platforms=platforms, force=force, mode=mode,
            manual_text=manual_text, manual_title=manual_title,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._order.insert(0, job.id)
            for stale in self._order[self._keep:]:
                self._jobs.pop(stale, None)
            del self._order[self._keep:]
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in self._order[:limit] if i in self._jobs]


def normalize_platforms(lastfm_only: bool, platforms: Iterable[str] | None = None) -> list[str]:
    """Resolve the UI's checkbox (or an explicit list) into a platform list."""
    if platforms:
        return list(platforms)
    return list(LASTFM_ONLY if lastfm_only else ALL_PLATFORMS)


def run_job(job: Job) -> None:
    """Execute a job start to finish, recording progress on it. Never raises."""
    try:
        if job.mode == "reresolve":
            _run_reresolve(job)
        elif job.mode == "manual":
            _run_manual(job)
        else:
            _run_ingest(job)
        job.status = "done"
        job.emit("done", level="ok")
    except Exception as e:
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        job.emit(job.error, level="error")
        job.emit(traceback.format_exc(), level="trace")


def _resolve_with_progress(job: Job, setlist, force: bool = False) -> None:
    """Shared resolve + coverage stamp for both job modes."""
    job.status = "resolving"
    job.emit(f"resolving against: {', '.join(job.platforms)}")

    def on_progress(phase: str, done: int, total: int) -> None:
        job.progress = (done, total)
        if phase == "matching" or done == total:
            job.emit(f"{phase}: {done}/{total}", progress=[done, total])

    summary = resolve_setlist(
        setlist, force=force, platforms=job.platforms, on_progress=on_progress
    )
    for warning in summary.warnings:
        job.emit(warning, level="warn")

    coverage = build_coverage(setlist, summary)
    db.set_coverage(setlist.id, coverage, status="resolved")
    job.coverage = coverage
    job.setlist_id = setlist.id
    job.emit(
        f"{summary.resolved} resolved · {summary.partial} partial · "
        f"{summary.no_match} no match · {summary.unreleased} unreleased "
        f"(registry hits: {summary.registry_hits}, LLM batches: {summary.llm_batches})",
        level="ok",
    )


def _run_ingest(job: Job) -> None:
    # --- capture -----------------------------------------------------------
    job.status = "capturing"
    job.emit(f"fetching {job.url}")
    fetcher.validate_url(job.url)
    html = fetcher.fetch_local(job.url, force=job.force)
    job.emit(f"captured {len(html):,} chars")

    stored = db.put_page(job.url, html)
    job.emit(f"stored page in Postgres ({stored:,} bytes gzipped)")

    # Re-ingesting a known URL keeps its id so existing links stay valid.
    existing = db.get_by_url(job.url)
    if existing is not None and not job.force:
        setlist, meta = existing
        job.setlist_id = setlist.id
        job.title = setlist.title
        job.emit(
            f"already ingested (status: {meta['status']}) — enable 'force' to re-run",
            level="warn",
        )
        return

    # --- normalize ---------------------------------------------------------
    job.status = "normalizing"
    job.emit("extracting rows and normalizing with the LLM…")
    setlist = pipeline.build_from_html(html, job.url)
    if existing is not None:
        setlist.id = existing[0].id  # keep the id the site already links to
    job.setlist_id = setlist.id
    job.title = setlist.title
    job.emit(f"normalized {len(setlist.tracks)} tracks — {setlist.title or '(untitled)'}", level="ok")

    store.save(setlist)
    db.upsert_content(setlist, status="resolving")

    # --- resolve -----------------------------------------------------------
    _resolve_with_progress(job, setlist)


def _run_manual(job: Job) -> None:
    """Build a set from a YouTube link plus a hand-pasted tracklist.

    No capture step: the text the maintainer pasted *is* the source, so there
    is nothing to scrape and nothing to store in `raw_pages`.
    """
    job.status = "normalizing"
    watch_url = pipeline.youtube_watch_url(job.url)
    job.url = watch_url  # canonical form is the key everything else is stored under
    job.emit(f"manual tracklist for {watch_url}")

    existing = db.get_by_url(watch_url)
    if existing is not None and not job.force:
        setlist, meta = existing
        job.setlist_id = setlist.id
        job.title = setlist.title
        job.emit(
            f"already added (status: {meta['status']}) — enable 'force' to replace it",
            level="warn",
        )
        return

    lines = len([ln for ln in (job.manual_text or "").splitlines() if ln.strip()])
    job.emit(f"parsing {lines} pasted lines with the LLM…")
    setlist = pipeline.build_from_manual(job.manual_text or "", watch_url, job.manual_title)
    if existing is not None:
        setlist.id = existing[0].id  # keep the id the site already links to
    job.setlist_id = setlist.id
    job.title = setlist.title
    job.emit(
        f"parsed {len(setlist.tracks)} tracks — {setlist.title or '(untitled)'}", level="ok"
    )

    store.save(setlist)
    db.upsert_content(setlist, status="resolving")

    _resolve_with_progress(job, setlist)


def _run_reresolve(job: Job) -> None:
    """Retry previously unmatched/partial slots on an already-ingested set."""
    existing = db.get_by_url(job.url) if job.url.startswith("http") else None
    if existing is None:
        raise RuntimeError(f"not ingested yet: {job.url}")
    setlist, _meta = existing
    job.setlist_id = setlist.id
    job.title = setlist.title

    cleared = 0
    for track in setlist.tracks:
        if track.resolution and track.resolution.status in _RETRYABLE_STATUSES:
            track.resolution = None
            cleared += 1
        for component in track.mashup_components:
            if component.resolution and component.resolution.status in _RETRYABLE_STATUSES:
                component.resolution = None
                cleared += 1

    db.set_status(setlist.id, "resolving")
    store.save(setlist)  # persist cleared slots so a crash mid-run retries them
    job.emit(f"cleared {cleared} unmatched slots for retry")

    _resolve_with_progress(job, setlist)


def start(job: Job, on_done: Callable[[Job], None] | None = None) -> threading.Thread:
    """Run a job on a background thread so the UI can stream it."""

    def target() -> None:
        run_job(job)
        if on_done is not None:
            on_done(job)

    thread = threading.Thread(target=target, name=f"ingest-{job.id[:8]}", daemon=True)
    thread.start()
    return thread

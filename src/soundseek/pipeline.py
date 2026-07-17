"""The ingest pipeline: URL -> fetch -> extract -> normalize -> persist."""

from __future__ import annotations

from . import fetcher, store
from .config import settings
from .extractor import extract
from .models import ParserInfo, RawSetlistPage, Setlist, SetlistTrack
from .normalizer import normalize


def _assemble(page: RawSetlistPage, tracks) -> Setlist:
    rows_by_position = {r.position: r for r in page.rows}
    return Setlist(
        source_url=page.source_url,
        title=page.title,
        dj_names=page.dj_names,
        event=page.event,
        date_recorded=page.date_recorded,
        genres=page.genres,
        media_url=page.media_url,
        media_kind=page.media_kind,
        parser=ParserInfo(model=settings.llm_model),
        tracks=[
            SetlistTrack(
                **track.model_dump(),
                source_track_number=(
                    row.source_track_number
                    if (row := rows_by_position.get(track.position))
                    else None
                ),
                cue_time=row.cue_time if row else None,
            )
            for track in tracks
        ],
    )


def build_setlist(url: str, force: bool = False) -> Setlist:
    """Fetch + extract + LLM-normalize a URL into a Setlist. Does NOT persist.

    Split out of ingest() so the background worker can normalize a set and then
    save it under an id it already handed to the client (the stub row's id),
    rather than minting a fresh one.
    """
    html = fetcher.fetch(url, force=force)
    page = extract(html, url)
    tracks = normalize(page)
    return _assemble(page, tracks)


def ingest(url: str, force: bool = False, skip_llm: bool = False) -> Setlist:
    """Ingest a 1001tracklists URL. Returns the stored (or existing) setlist.

    force:    re-fetch and re-parse even if the URL was already ingested.
    skip_llm: debug mode — store raw rows without LLM normalization.
    """
    if not force:
        existing = store.load_by_url(url)
        if existing is not None:
            return existing

    if skip_llm:
        html = fetcher.fetch(url, force=force)
        page = extract(html, url)
        setlist = Setlist(
            source_url=page.source_url,
            title=page.title,
            dj_names=page.dj_names,
            event=page.event,
            date_recorded=page.date_recorded,
            genres=page.genres,
            media_url=page.media_url,
            media_kind=page.media_kind,
            parser=ParserInfo(model="none (--no-llm)"),
            tracks=[
                SetlistTrack(
                    position=r.position,
                    source_track_number=r.source_track_number,
                    raw_text=r.raw_text,
                    cue_time=r.cue_time,
                    played_with=-1 if r.is_played_with else None,
                )
                for r in page.rows
            ],
        )
        return setlist  # debug output is not persisted

    setlist = build_setlist(url, force=force)
    store.save(setlist)
    return setlist

"""The ingest pipeline: URL -> fetch -> extract -> normalize -> persist.

Two ways in. The scraped path (1001tracklists) is the default. The manual path
takes a YouTube link plus a tracklist a human pasted — chapters, a description,
a comment — and runs it through the LLM into the same Setlist shape, so
resolution, scrobbling and the web UI cannot tell the two apart afterwards.
"""

from __future__ import annotations

import re

from . import fetcher, store
from .config import settings
from .extractor import extract, extract_manual
from .models import ParserInfo, RawSetlistPage, Setlist, SetlistTrack
from .normalizer import normalize


def _assemble(page: RawSetlistPage, tracks, source: str = "1001tracklists") -> Setlist:
    rows_by_position = {r.position: r for r in page.rows}
    return Setlist(
        source=source,
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


# youtube.com/watch?v=, youtu.be/, /live/, /embed/, /shorts/ — all the shapes a
# link gets copied in as. The id itself is 11 URL-safe base64 characters.
_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|live/|embed/|shorts/|v/)|youtu\.be/)"
    r"(?P<id>[\w-]{11})",
    re.IGNORECASE,
)


class ManualInputError(ValueError):
    """The maintainer's manual-set input is not usable."""


def youtube_watch_url(url: str) -> str:
    """Canonicalize any YouTube link to `https://www.youtube.com/watch?v=<id>`.

    The canonical form is what gets stored as `source_url`, which is the table's
    unique key — so the same video pasted as a youtu.be short link and as a
    watch URL updates one set rather than creating two.
    """
    m = _YT_ID_RE.search((url or "").strip())
    if not m:
        raise ManualInputError(
            f"Not a YouTube video URL: {url or '(empty)'}\n"
            "Expected something like https://www.youtube.com/watch?v=<id> or https://youtu.be/<id>"
        )
    return f"https://www.youtube.com/watch?v={m.group('id')}"


def build_from_manual(text: str, youtube_url: str, title: str | None = None) -> Setlist:
    """Turn a pasted tracklist into a Setlist for a YouTube video. Does NOT persist.

    Timestamps and list numbering come off deterministically (`extract_manual`);
    only the artist/title/remix split is the LLM's, through the same prompt and
    the same round-trip validation the scraped path uses. The video is both the
    source — there is no page to scrape — and the media the cue times refer to,
    so `source_url` and `media_url` are the same canonical watch URL.
    """
    watch_url = youtube_watch_url(youtube_url)
    page = extract_manual(text, watch_url, title)
    tracks = normalize(page)
    return _assemble(page, tracks, source="manual")


def build_from_html(html: str, url: str) -> Setlist:
    """Extract + LLM-normalize already-fetched HTML. Does NOT persist.

    For callers that have the page in hand (the ingest tool captures it, stores
    it, then normalizes) so the fetch isn't repeated.
    """
    page = extract(html, url)
    tracks = normalize(page)
    return _assemble(page, tracks)


def build_setlist(url: str, force: bool = False) -> Setlist:
    """Fetch + extract + LLM-normalize a URL into a Setlist. Does NOT persist.

    Split out of ingest() so a caller can normalize a set and then save it under
    an id it already holds, rather than minting a fresh one.
    """
    return build_from_html(fetcher.fetch(url, force=force), url)


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

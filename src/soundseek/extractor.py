"""Deterministic extraction: source -> RawSetlistPage.

All CSS selectors for 1001TL's markup live in this module so layout drift
only breaks one place. Rows are extracted verbatim (`raw_text`); splitting
artist/title/remix is the LLM normalizer's job.

`extract_manual` is the same job for a tracklist a human pasted out of a
YouTube video. It yields the identical RawSetlistPage, so the pasted text goes
through the exact prompt and the exact round-trip validation the scraped rows
do — including the check that the LLM invented nothing.

Observed markup (2026-07):
    div.tlpItem[data-trno]                  one row per track
      span[id$='_tracknumber_value']        "01".."29" or "w/" (layered row)
      input[id$='_cue_seconds']             cue offset in seconds
      meta[itemprop='name']                 clean "Artist - Title (Remix)"
      span.trackValue                       visible text (fallback, richer)
      meta[itemprop='genre']                per-track genre
    div.tlpItem.tlpSubTog                   mashup COMPONENT sub-row (chain-link
                                            icon, no track number) — belongs to
                                            the preceding parent row (subPosTog)
    h1#pageTitle / og:title                 "DJ @ Event, City, Country YYYY-MM-DD"
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from .models import RawSetlistPage, RawTrackRow

# Title convention: "DJ1 & DJ2 @ Event 2025-11-14" (date suffix optional)
_TITLE_RE = re.compile(r"^(?P<djs>.+?)\s+@\s+(?P<event>.+?)(?:\s+(?P<date>\d{4}-\d{2}-\d{2}))?$")

_YT_EMBED_RE = re.compile(r"youtube\.com/embed/([\w-]+)")


class ExtractionError(RuntimeError):
    """Raised when the page yields no tracklist — layout drift or a bad page."""


def _clean(text: str) -> str:
    """Collapse whitespace and fix "( X )" artifacts from joined spans."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\s+,", ",", text)
    return text


def _format_cue(seconds_str: str | None) -> str | None:
    if not seconds_str or not seconds_str.isdigit():
        return None
    s = int(seconds_str)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _row_from_item(item: Tag, position: int) -> RawTrackRow | None:
    num_el = item.select_one("span[id$='_tracknumber_value']")
    number = num_el.get_text(strip=True) if num_el else ""
    is_played_with = number.lower() == "w/"
    source_track_number = int(number) if number.isdigit() else None

    meta_name = item.select_one("meta[itemprop='name']")
    track_value = item.select_one("span.trackValue")
    # Prefer visible text (occasionally richer, e.g. "(Co-Prod. by ...)"),
    # fall back to the schema.org meta.
    if track_value is not None:
        raw_text = _clean(track_value.get_text(" ", strip=True))
    elif meta_name is not None and meta_name.get("content"):
        raw_text = _clean(str(meta_name["content"]))
    else:
        return None  # not a track row (ads/actions sometimes share the class)

    cue_input = item.select_one("input[id$='_cue_seconds']")
    cue_seconds = str(cue_input["value"]) if cue_input and cue_input.get("value") else None
    # The site stores unknown cues as 0; only the very first track can
    # legitimately start at 0:00. "w/" rows often carry real cues — keep them.
    if cue_seconds == "0" and position != 1:
        cue = None
    else:
        cue = _format_cue(cue_seconds)

    return RawTrackRow(
        position=position,
        source_track_number=source_track_number,
        cue_time=cue,
        raw_text=raw_text,
        is_played_with=is_played_with,
    )


def _component_text(item: Tag) -> str | None:
    """Raw text of a mashup component sub-row (same fields as a normal row)."""
    track_value = item.select_one("span.trackValue")
    if track_value is not None:
        return _clean(track_value.get_text(" ", strip=True))
    meta_name = item.select_one("meta[itemprop='name']")
    if meta_name is not None and meta_name.get("content"):
        return _clean(str(meta_name["content"]))
    return None


def parse_title(title: str | None) -> tuple[str | None, list[str], str | None, str | None]:
    """Split a set title into (title, dj_names, event, date_recorded).

    Public because the manual path needs it too: a hand-typed title following
    the same "DJ @ Event 2025-11-14" convention should land the set on the same
    DJ/event/year filter chips as a scraped one.
    """
    title = _clean(title or "")
    if not title:
        return None, [], None, None

    m = _TITLE_RE.match(title)
    if not m:
        return title, [], None, None
    djs = [d.strip() for d in re.split(r"\s*[&,]\s*", m.group("djs")) if d.strip()]
    return title, djs, m.group("event"), m.group("date")


def _parse_page_title(soup: BeautifulSoup) -> tuple[str | None, list[str], str | None, str | None]:
    """Return (title, dj_names, event, date_recorded) from the page header."""
    el = soup.select_one("h1#pageTitle") or soup.find("meta", property="og:title")
    if el is None:
        return None, [], None, None
    raw = el.get_text(" ", strip=True) if isinstance(el, Tag) and el.name == "h1" else str(el.get("content", ""))
    return parse_title(raw)


def _media_kind(url: str) -> str:
    for kind in ("youtube", "soundcloud", "hearthis"):
        if kind in url:
            return kind
    return "other"


def _extract_media(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Best link to the set recording the cue timestamps refer to.

    Preferred: the schema.org VideoObject the page features (YouTube embed) ->
    normalized to a watch URL. Fallback: the first mediaLink player iframe
    (hearthis.at / SoundCloud / ...)."""
    video = soup.select_one("div[itemprop='video'] meta[itemprop='embedUrl']")
    if video and video.get("content"):
        url = str(video["content"])
        m = _YT_EMBED_RE.search(url)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}", "youtube"
        return url, _media_kind(url)

    iframe = soup.select_one("div.mediaLink iframe[src]")
    if iframe:
        url = str(iframe["src"])
        return url, _media_kind(url)
    return None, None


# ---------------------------------------------------------------------------
# Pasted tracklists (YouTube chapters, descriptions, comments)
# ---------------------------------------------------------------------------

# A timestamp: 0:00 / 12:34 / 1:02:30, optionally bracketed.
_TS = r"\d{1,2}:\d{2}(?::\d{2})?"
# Leading: "0:00 ", "[0:00] ", "(0:00) - ", "0:00 — "
_LEADING_TS_RE = re.compile(rf"^[\[\(]?\s*(?P<ts>{_TS})\s*[\]\)]?\s*[-–—:.)]?\s*")
# Trailing: "... @ 12:34", "... [12:34]", "... (1:02:30)"
_TRAILING_TS_RE = re.compile(rf"\s*[@\-–—]?\s*[\[\(]?\s*(?P<ts>{_TS})\s*[\]\)]?\s*$")
# List numbering: "1. ", "01) ", "12 - " (kept distinct from timestamps)
_LIST_NUM_RE = re.compile(r"^\s*\d{1,3}\s*[.)\]]\s+")

# Lines that are never tracks. Deliberately short: anything ambiguous is better
# passed through and deleted by hand in the console than dropped silently.
_JUNK_PREFIXES = ("http://", "https://", "www.", "#")
_JUNK_EXACT = ("tracklist", "tracklist:", "track list", "track list:", "setlist", "setlist:")


def _normalize_cue(ts: str) -> str:
    """`00:03:41` -> `3:41`, `01:02:30` -> `1:02:30`, `0:00` -> `0:00`."""
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
    minutes, seconds = parts
    return f"{minutes}:{seconds:02d}"


def _split_manual_line(line: str) -> tuple[str, str | None] | None:
    """One pasted line -> (track text, cue) — or None when it is not a track.

    The timestamp and any list numbering are stripped here rather than left to
    the LLM: they are unambiguous, and a cue that drives scrobble windows should
    not depend on a model getting it right.
    """
    text = _clean(line)
    if not text:
        return None

    cue = None
    text = _LIST_NUM_RE.sub("", text, count=1)
    leading = _LEADING_TS_RE.match(text)
    if leading:
        cue = _normalize_cue(leading.group("ts"))
        text = text[leading.end():]
        text = _LIST_NUM_RE.sub("", text, count=1)  # "0:00 1. Artist - Title"
    else:
        trailing = _TRAILING_TS_RE.search(text)
        if trailing and trailing.start() > 0:
            cue = _normalize_cue(trailing.group("ts"))
            text = text[: trailing.start()]

    # Pasted lists trail separators off the end of a row ("… [X Mashup] /").
    text = _clean(text).strip("-–—•·|/").strip()
    lowered = text.lower()
    if not text or lowered in _JUNK_EXACT or lowered.startswith(_JUNK_PREFIXES):
        return None
    if not re.search(r"[A-Za-z0-9]", text):
        return None
    return text, cue


def extract_manual(
    text: str, source_url: str, title: str | None = None, media_url: str | None = None
) -> RawSetlistPage:
    """A pasted tracklist -> the same RawSetlistPage the scraper produces."""
    rows: list[RawTrackRow] = []
    for line in text.splitlines():
        parsed = _split_manual_line(line)
        if parsed is None:
            continue
        row_text, cue = parsed
        rows.append(
            RawTrackRow(
                position=len(rows) + 1,
                source_track_number=len(rows) + 1,
                cue_time=cue,
                raw_text=row_text,
            )
        )

    if not rows:
        raise ExtractionError(
            "No track lines found in the pasted text — check that it is a "
            "tracklist and not, say, the description around one."
        )

    clean_title, dj_names, event, date_recorded = parse_title(title)
    return RawSetlistPage(
        source_url=source_url,
        title=clean_title,
        dj_names=dj_names,
        event=event,
        date_recorded=date_recorded,
        media_url=media_url or source_url,
        media_kind="youtube",
        rows=rows,
    )


def extract(html: str, source_url: str) -> RawSetlistPage:
    soup = BeautifulSoup(html, "lxml")

    title, dj_names, event, date_recorded = _parse_page_title(soup)
    media_url, media_kind = _extract_media(soup)

    rows: list[RawTrackRow] = []
    items = soup.select("div.tlpItem")
    # Keep DOM order; data-trno can have gaps (deleted rows), so number ourselves.
    position = 0
    for item in items:
        classes = item.get("class") or []
        if "tlpSubTog" in classes:
            # Explicit mashup component sub-row: attach to the parent row.
            raw = _component_text(item)
            if raw and rows:
                rows[-1].component_texts.append(raw)
            continue
        position += 1
        row = _row_from_item(item, position)
        if row is None:
            position -= 1
            continue
        rows.append(row)

    genres = sorted(
        {
            str(meta["content"]).strip()
            for meta in soup.select("div.tlpItem meta[itemprop='genre']")
            if meta.get("content")
        }
    )

    page = RawSetlistPage(
        source_url=source_url,
        title=title,
        dj_names=dj_names,
        event=event,
        date_recorded=date_recorded,
        genres=genres,
        media_url=media_url,
        media_kind=media_kind,
        rows=rows,
    )

    if not rows:
        # Selector failure: degrade to the visible page text so the LLM
        # normalizer can attempt full extraction from prose.
        body_text = _clean(soup.get_text(" ", strip=True))
        if len(body_text) < 200 or "tracklist" not in html.lower():
            raise ExtractionError(
                f"0 tracks extracted from {source_url} and the page does not look "
                "like a tracklist — 1001TL layout may have changed, or the fetch "
                "was blocked. Inspect the cached HTML in data/raw_html/."
            )
        page.fallback_text = body_text[:20_000]

    return page

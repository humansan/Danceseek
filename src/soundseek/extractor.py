"""Deterministic extraction: 1001tracklists HTML -> RawSetlistPage.

All CSS selectors for 1001TL's markup live in this module so layout drift
only breaks one place. Rows are extracted verbatim (`raw_text`); splitting
artist/title/remix is the LLM normalizer's job.

Observed markup (2026-07):
    div.tlpItem[data-trno]                  one row per track
      span[id$='_tracknumber_value']        "01".."29" or "w/" (layered row)
      input[id$='_cue_seconds']             cue offset in seconds
      meta[itemprop='name']                 clean "Artist - Title (Remix)"
      span.trackValue                       visible text (fallback, richer)
      meta[itemprop='genre']                per-track genre
    h1#pageTitle / og:title                 "DJ @ Event, City, Country YYYY-MM-DD"
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from .models import RawSetlistPage, RawTrackRow

# Title convention: "DJ1 & DJ2 @ Event 2025-11-14" (date suffix optional)
_TITLE_RE = re.compile(r"^(?P<djs>.+?)\s+@\s+(?P<event>.+?)(?:\s+(?P<date>\d{4}-\d{2}-\d{2}))?$")


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
        cue_time=cue,
        raw_text=raw_text,
        is_played_with=is_played_with,
    )


def _parse_page_title(soup: BeautifulSoup) -> tuple[str | None, list[str], str | None, str | None]:
    """Return (title, dj_names, event, date_recorded) from the page header."""
    el = soup.select_one("h1#pageTitle") or soup.find("meta", property="og:title")
    if el is None:
        return None, [], None, None
    title = _clean(el.get_text(" ", strip=True) if isinstance(el, Tag) and el.name == "h1" else str(el.get("content", "")))
    if not title:
        return None, [], None, None

    m = _TITLE_RE.match(title)
    if not m:
        return title, [], None, None
    djs = [d.strip() for d in re.split(r"\s*[&,]\s*", m.group("djs")) if d.strip()]
    return title, djs, m.group("event"), m.group("date")


def extract(html: str, source_url: str) -> RawSetlistPage:
    soup = BeautifulSoup(html, "lxml")

    title, dj_names, event, date_recorded = _parse_page_title(soup)

    rows: list[RawTrackRow] = []
    items = soup.select("div.tlpItem")
    # Keep DOM order; data-trno can have gaps (deleted rows), so number ourselves.
    position = 0
    for item in items:
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

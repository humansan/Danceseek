"""LLM normalization: raw track rows -> structured ParsedTrack list.

LangChain chain (prompt | Gemini with structured output) that splits each
verbatim row into artists/title/remix and flags IDs and mashups. The LLM
never invents data: every row's `raw_text` must round-trip verbatim and
`played_with` is computed deterministically from the extractor's "w/" flags,
not trusted from the model.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from langchain_core.prompts import ChatPromptTemplate
from langchain_openrouter import ChatOpenRouter

from .config import settings
from .fetcher import url_digest
from .models import ParsedTrack, ParsedTracklist, RawSetlistPage

SYSTEM_PROMPT = """You are a music metadata normalizer for DJ setlists scraped from 1001tracklists.

You receive numbered track rows exactly as they appear on the site. For each row, split the text into structured fields. Rules:

- Echo `position` and `raw_text` back EXACTLY as given — character for character. Never correct, translate, or reformat them.
- `artists`: split artist credits on "&", "and", commas, "ft.", "feat.", "pres.", "b2b". Featured artists count as artists. Keep each name verbatim (e.g. "Fred again..", "CA7RIEL").
- `title`: the track title WITHOUT any remix/version suffix and without the artist part.
- `remix`: parenthesized or suffixed version info, e.g. "Extended Mix", "Hamdi Remix", "Skrillex & Fred again.. Edit", "Fred again.. Mashup". Null if the parenthetical is part of the actual title (e.g. "Danielle (Smile On My Face)" — subtitle, not a remix) or absent.
- Unreleased/unidentified tracks: if the artist and/or title is literally "ID", set `is_id` to true. For "ID - ID" leave `artists` empty and `title` null. For "Artist - ID" keep the artists but leave `title` null. Never guess what an ID track might be.
- Mashups written as "A vs. B" (two tracks played as one): fill `mashup_components` with one entry per component track, best-effort splitting the artists/titles across the "vs.". Leave `mashup_components` empty for normal tracks.
- Set `played_with` to null always (it is computed elsewhere).
- Never add tracks, drop tracks, or reorder. Output exactly one entry per input row, in the same order.
- Never use outside knowledge to "fix" names — only restructure what is written."""

USER_PROMPT = """Normalize these {count} track rows. Each line is formatted as `position | raw_text`; the `position | ` part is NOT part of raw_text.

{rows}"""

FALLBACK_SYSTEM_PROMPT = """You are a music metadata extractor. You receive the raw visible text of a DJ tracklist page from 1001tracklists (the structured markup could not be parsed). Extract the ordered tracklist from it.

- One entry per track, `position` starting at 1 in page order.
- `raw_text`: the track's text exactly as it appears (typically "Artist - Title (Remix)").
- Apply the same field rules: split artists, separate title and remix, flag literal "ID" tracks with is_id, mashups ("A vs. B") into mashup_components, played_with always null.
- Only extract tracks actually present in the text. Never invent or guess tracks."""

FALLBACK_USER_PROMPT = """Extract the tracklist from this page text:

{text}"""


class NormalizationError(RuntimeError):
    """Raised when the LLM output fails validation against the input rows."""


def _build_llm():
    llm = ChatOpenRouter(model=settings.llm_model, temperature=0)
    # json_schema (not the default function_calling): lightweight models like
    # Gemma have no native tool calling, but OpenRouter can enforce a JSON
    # schema on the response for them.
    structured = llm.with_structured_output(ParsedTracklist, method="json_schema")
    return structured.with_retry(stop_after_attempt=3)


def _validate_round_trip(page: RawSetlistPage, tracks: list[ParsedTrack]) -> None:
    """Hard anti-hallucination check: positions and raw_text must round-trip."""
    if len(tracks) != len(page.rows):
        raise NormalizationError(
            f"LLM returned {len(tracks)} tracks for {len(page.rows)} input rows."
        )
    mismatches = []
    for row, track in zip(page.rows, tracks):
        # Deterministically strip the input framing if the model echoed it.
        for prefix in (f"{row.position} | ", f"{row.position}. "):
            if track.raw_text == prefix + row.raw_text:
                track.raw_text = row.raw_text
                break
        if track.position != row.position or track.raw_text != row.raw_text:
            mismatches.append(
                f"  row {row.position}: expected {row.raw_text!r}, "
                f"got position={track.position} raw_text={track.raw_text!r}"
            )
    if mismatches:
        raise NormalizationError(
            "LLM output does not round-trip the input rows:\n" + "\n".join(mismatches)
        )


def _dump_llm_input(page: RawSetlistPage, mode: str, user_content: str):
    """Persist exactly what goes to the LLM, for debugging/sanity checks.

    Keyed by the same URL digest as the raw_html cache, so the chain
    raw_html/<digest>.html -> llm_inputs/<digest>.json -> setlists/<id>.json
    can be followed for any URL. Returns the file path.
    """
    settings.llm_inputs_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "source_url": page.source_url,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.llm_model,
        "mode": mode,  # "rows" (normal) or "fallback_text" (selector failure)
        "extracted_page": page.model_dump(),
        "llm_user_content": user_content,
    }
    path = settings.llm_inputs_dir / f"{url_digest(page.source_url)}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _apply_played_with(page: RawSetlistPage, tracks: list[ParsedTrack]) -> None:
    """`played_with` comes from the DOM's "w/" markers, not the model:
    a w/ row is layered over the nearest preceding non-w/ track."""
    last_main_position: int | None = None
    for row, track in zip(page.rows, tracks):
        if row.is_played_with:
            track.played_with = last_main_position
        else:
            track.played_with = None
            last_main_position = row.position


def normalize(page: RawSetlistPage) -> list[ParsedTrack]:
    """Normalize a scraped page's rows into structured tracks."""
    llm = _build_llm()

    if page.rows:
        prompt = ChatPromptTemplate.from_messages(
            [("system", SYSTEM_PROMPT), ("user", USER_PROMPT)]
        )
        rows_text = "\n".join(f"{r.position} | {r.raw_text}" for r in page.rows)
        _dump_llm_input(page, "rows", rows_text)
        chain = prompt | llm
        result: ParsedTracklist = chain.invoke(
            {"count": len(page.rows), "rows": rows_text}
        )
        _validate_round_trip(page, result.tracks)
        _apply_played_with(page, result.tracks)
        return result.tracks

    if page.fallback_text:
        prompt = ChatPromptTemplate.from_messages(
            [("system", FALLBACK_SYSTEM_PROMPT), ("user", FALLBACK_USER_PROMPT)]
        )
        _dump_llm_input(page, "fallback_text", page.fallback_text)
        chain = prompt | llm
        result = chain.invoke({"text": page.fallback_text})
        if not result.tracks:
            raise NormalizationError(
                "Fallback text extraction produced no tracks — page likely is not "
                "a tracklist or the fetch was blocked."
            )
        return result.tracks

    raise NormalizationError("Nothing to normalize: no rows and no fallback text.")

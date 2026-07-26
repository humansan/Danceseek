# SoundSeek

![](/danceseek-img.webp)

A browsable catalog of DJ sets that scrobbles to Last.fm as you listen.

A tracklist page tells you what was played, but the text is often messy — `ID - ID`, bootlegs,
`A vs. B` mashups, artist credits glued together six different ways. SoundSeek normalizes that
into structured tracks, matches each one against **Last.fm records**, and plays the set back in the browser with the tracklist
synced to the video. Sit through a track and it scrobbles, under a consistent names the scrobbler
recognizes.

It also has currently WIP feature to use automatically match tracklists against Spotify and Youtube and export accurate playlists to those platforms.

The tracklist upload and process ui is currently not open to the public, it's done through a maintainer console app, found in apps/ingest.
The deployed web ui is therefore currently Read-Only - public implementation is tbd.

---

## Architecture

```
maintainer's machine                          deployed
────────────────────                          ────────
browser
LLM normalization                 ──► Neon ──► read API ──► web app
platform search / matching           Postgres  (browse,      (player,
ingest console + editor                         cues,         scrobbling)
                                                scrobble)
```

Tracklists can be added from 1001 tracklists or youtube videos that have matching tracklists. Normalization and matching need LLM and platform API keys. Currently it all runs locally and writes results straight to Postgres. The deployed API installs *only* the `api` dependency group: no Playwright, no LangChain, no yt-dlp. `tests/test_api_surface.py` asserts it never imports the scraper or the pipeline, and CI installs that group alone and imports the app, so the day someone adds an ingest route the build says so.

---

## Setup

```powershell
uv sync                          # everything: core + ingest + cli + api
uv run playwright install chromium
copy .env.example .env
npm ci                           # web app (from the repo root — see below)
```

Minimum to do anything useful: `OPENROUTER_API_KEY` (normalization), `LASTFM_API_KEY` +
`LASTFM_SECRET` (matching and scrobbling), `DATABASE_URL` (Neon). `SPOTIFY_CLIENT_ID/SECRET`
and `GOOGLE_CLIENT_ID/SECRET` are only needed for full-platform matching and playlist export (for Spotify and Youtube);
a missing key disables that platform with a warning rather than failing.

Apply the schema once: `uv run alembic upgrade head`.

First run, run with `SOUNDSEEK_HEADLESS=false`, clear it by hand, and the profile in
`data/browser_profile/` keeps the clearance cookie for headless runs afterwards.

**npm at the root, not in `apps/web`.** The generated API client is a workspace package whose
own dependency (`openapi-fetch`) only resolves once npm has hoisted it at the root.

---

## Adding sets — the ingest console

```powershell
uv run soundseek console      # http://127.0.0.1:8020
```

Binds to loopback, has no auth, and is never deployed. Two ways to add a set.

### From 1001tracklists

Paste the tracklist URL. The console streams the run live: capture → normalize → resolve.

- **capture** — fetches the page, disk-cached in `data/raw_html/`, and the HTML
  is also stored in Postgres.
- **normalize** — the initial data is extracted deterministically, then an LLM cleans the data and splits each row into structured data like artists/title/remix and flags IDs and mashups.
- **resolve** — the LLM runs queries from the structured data for each track on Last.fm (and optionally Spotify + YouTube) and matches against results. If high confidence results are found they're stored, otherwise they're ignored and just the normalized result is kept.

**Last.fm only** is the default and is what scrobbling needs - one search family instead of
three and a much shorter prompt, so it's faster and cheaper. **Force** re-runs a set
that's already in the catalog, keeping its id so existing links stay valid. **Re-resolve**
retries only the unmatched and pending slots of a set already in the catalog.

### From a YouTube video

Paste the video link, a title, and the tracklist text - could be from the chapter list, description, comment, etc:

```
0:00 Alesso - Destinations x 3LAU ft. Bright Lights - How You Love Me [Flipboitamidles Mashup] /
2:07 Galantis - Runaway (U&I) (Mew Remix) /
1. 0:00 Artist - Title        10. Artist - Title @ 10:38        [12:00] Artist - Title
```

Timestamps, list numbering and trailing separators are split **deterministically** - they drive scrobble windows, so a model shouldn't be the thing that gets them right. Only the
artist/title/remix split goes to the LLM, through the same prompt and the same round-trip validation the scraped rows use. Junk lines (`Tracklist:`, URLs, separators) are dropped; anything ambiguous is passed through for you to delete in the editor.

A title following the `DJ @ Event 2025-11-14` convention lands the set on the same DJ / event / year filter chips as a scraped one. The result is byte-identical in shape to a scraped set - resolution, scrobbling and the web UI can't tell them apart.

### Editing the results

Every set in the catalog is editable from the results table: cue, artists, title, remix, the ID flag, and the **Last.fm artist + track** — the exact strings that get scrobbled. Mashup components edit on their own rows; `✕` deletes a row, `+ row` adds one.

Two rules decide what a save does:

- **`raw_text` is never editable.** It's what the source said, and it stays the provenance  normalization can be re-derived from.
- **Editing a row's identity drops its match.** That Last.fm entry was chosen for the old strings; keeping it would silently scrobble the wrong track. The slot goes null, shows as `pending`, and the next re-resolve picks it up. A Last.fm target *you* typed is the exception - it's treated as best match.

The submission is the whole table, not a diff: rows left out are deleted, and positions are renumbered with `played_with` remapped to follow its row.

---

## The catalog

Sets live in one table (`setlists`): metadata in columns, the full tracks array as JSONB. Alongside it, `tracks` is a cross-set registry so a track resolved once is reused instantly, `raw_pages` holds captured HTML for re-normalizing without re-scraping, and `users` holds Last.fm identity. (`jobs`, `oauth_tokens`, `saved_sets`, `exports` exist too; see `migrations/versions/0001_initial_schema.py`.)

**Precision over recall.** A platform field is populated only when the match cleared the confidence threshold. A bootleg that exists nowhere ends as `no_match` with empty slots - that's the correct outcome, not a failure, and the scrobbler falls back to our normalized names. Matches are picked by a batched LLM call over gathered candidates, validated against those candidates (so a hallucinated id is discarded) and gated by `SOUNDSEEK_RESOLVE_MIN_CONFIDENCE`.

**Mashups** resolve twice over: the row as a whole is a YouTube-only search (a bootleg upload may exist; there is no canonical Last.fm entry for `A vs. B`), while its components each resolve individually — and the components are what gets scrobbled. A credit closing a mashup row (`[Someone Mashup]`) names whoever made the *combination*, so it's moved onto the row rather than left on whichever component happened to end the string.

---

## The site

```powershell
uv run uvicorn apps.api.main:app --port 8010     # read API
npm run dev                                       # web app on :3000
```

Browse with multi-select filters (OR within a facet, AND across them), open a set, and the YouTube recording plays with the tracklist synced to it. Connect Last.fm and tracks scrobble as they play.

**The server decides what's playing, not the browser.** `scrobble/windows.py` is the single source of truth: one track's window runs from its cue to the next. The client reports a playhead; the server re-derives the window, re-checks your settings, and nothing the client reports is trusted. That's what keeps the UI highlight and the scrobbler from ever disagreeing. 

Last.fm's own threshold applies - half the track or four minutes, whichever comes first, with a 30s floor — measured against the cue window, and the dwell clock only runs while the video is actually playing. Scrubbing through a set doesn't log tracks you didn't hear. Sets with no cue times still get evenly-spread windows marked `estimated`; those can't drive live scrobbling, but they can be scrobbled in one action as a whole set.

What gets scrobbled is configurable: layered (`w/`) rows, mashup components (all / primary / skip), unreleased tracks, and unmatched tracks. The defaults are the cautious reading — log what you can stand behind.

The Last.fm **session key is a permanent write credential** and never reaches a browser. `db.get_user` structurally cannot return it; only the server-side scrobbler can read it.

---

## CLI

The console has a terminal twin for everything, plus the maintenance commands:

```powershell
uv run soundseek publish <url>          # capture → normalize → resolve → Neon
uv run soundseek publish <url> --lastfm-only --force
uv run soundseek publish <url> --reresolve
uv run soundseek show <id|url>          # the results table, in the terminal
uv run soundseek list                   # local catalog
uv run soundseek remote                 # what's in Neon
uv run soundseek push <id|url> | --all  # backfill
uv run soundseek export <id|url> --target youtube --dry-run
```

**Export** turns a resolved set into a real playlist. Only confidently-matched tracks are added; the rest are reported with a reason. On YouTube the whole-mashup upload is preferred, falling back to components; on Spotify (where no mashup entry can exist) they always expand. One-time browser login per platform, refresh token cached under `data/auth/`. Note YouTube's quota (~50 units per insert, 10,000/day).

`soundseek ingest` / `resolve` are the older single-step commands and still work; `publish` is the one that runs the whole pipeline and pushes the output data to the db.

---

## Debugging a set

Everything about one URL is reachable through a shared digest:

```
data/raw_html/<digest>.html          the page as fetched
data/llm_inputs/<digest>.json        extracted rows + exactly what went to the LLM
data/resolution_logs/<digest>.jsonl  per-track candidates, queries, and the pick
```

If a set came out wrong, that chain says which stage did it.

---

## Layout

```
src/soundseek/         the shared core
  models.py            Setlist / SetlistTrack / Resolution — the domain
  extractor.py         HTML and pasted text -> RawSetlistPage (deterministic)
  normalizer.py        the normalizr LLM, its validation, and the fixups
  resolver/            candidate gathering + the batched query & picker LLM
  edit.py              maintainer edits and what they invalidate
  scrobble/windows.py  cue windows — what's playing, and under which name
  db.py                Postgres. No ORM, no relations by design.
apps/api/              deployed read API (FastAPI)
apps/ingest/           local maintainer console
apps/web/              Next.js site
packages/api-client/   TypeScript client generated from the OpenAPI schema
```

Config is `SOUNDSEEK_*` env vars (see `.env.example`). Two backends switch behaviour:
`SOUNDSEEK_STORE_BACKEND` (`json` locally, `postgres` deployed) and `SOUNDSEEK_FETCH_BACKEND`
(`local` drives a browser, `stored` reads a previously-captured page out of Postgres).

---

## Tests

```powershell
uv run pytest
```

No network, no browser, no database — every edge is stubbed, and CI runs without a
`DATABASE_URL` on purpose so a test that reaches for Neon fails there rather than quietly
passing against live data. Extractor tests run against real saved pages in `tests/fixtures/`.

CI additionally builds the API with only the `api` group and runs the Next build, because both
are deploy contracts that a plain `pytest` wouldn't catch.

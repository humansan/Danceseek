"""Danceseek API — a thin read layer over the Neon catalog.

Deliberately lean: this service does **no** ingestion. Scraping needs a real
browser with a Cloudflare-cleared profile and normalizing needs the LLM, so all
of that runs on the maintainer's machine via the ingest console
(`apps/ingest`), which writes results straight to Neon. What's left here is
browse, detail, and a network-free export preview — cheap, fast, and safe to
deploy anywhere.

Scrobbling endpoints land in a later phase; ingestion comes back to the server
only if/when the managed-browser backend does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import soundseek.config  # noqa: F401 — loads .env (DATABASE_URL etc.)
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from soundseek import db, session
from soundseek.config import settings
from soundseek.lastfm import auth as lastfm_auth
from soundseek.lastfm import submit
from soundseek.models import Setlist
from soundseek.scrobble.windows import ScrobbleConfig, WindowSet, build_windows

app = FastAPI(title="Danceseek API", version="0.3.0")

# The browser normally reaches us through the web app's /api/* rewrite (same
# origin, so the session cookie just works). CORS with credentials is here for
# direct calls; the origin list must stay explicit — browsers reject "*" plus
# credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", settings.web_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SetlistSummary(BaseModel):
    """Lightweight browse-card row."""

    id: str
    title: str | None
    dj_names: list[str]
    event: str | None
    date_recorded: str | None
    genres: list[str]
    media_url: str | None
    track_count: int
    status: str
    coverage: dict | None
    created_at: str | None
    # Set length in seconds, from the last cue. None when a set has no cues.
    length_s: int | None = None


class Facet(BaseModel):
    value: str
    count: int


class Facets(BaseModel):
    """Everything the filter chips offer, counted across the whole catalog."""

    djs: list[Facet]
    genres: list[Facet]
    events: list[Facet]
    years: list[Facet]


class SetlistDetail(BaseModel):
    """Full setlist plus its resolution lifecycle metadata."""

    setlist: Setlist
    status: str
    coverage: dict | None
    resolved_at: str | None
    created_at: str | None


class ExportPreviewRequest(BaseModel):
    target: Literal["spotify", "youtube"]
    expand_mashups: bool = True
    skip_played_with: bool = False


class ExportPlanItem(BaseModel):
    id: str
    label: str


class ExportSkippedItem(BaseModel):
    label: str
    reason: str


class ExportPreview(BaseModel):
    """Dry-run export plan: what would be added / skipped, per target platform."""

    target: str
    added: int
    total_considered: int
    items: list[ExportPlanItem]
    skipped: list[ExportSkippedItem]


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------------
# Last.fm identity. The session key Last.fm hands back authorizes scrobbling on
# that account forever, so it stays in the database: the browser only ever gets
# a signed cookie naming which user it is.
# ---------------------------------------------------------------------------


class Me(BaseModel):
    lastfm_username: str | None = None
    connected: bool = False
    # True when a Last.fm approval is in flight: the user has been sent to
    # Last.fm but we never got the callback. Last.fm only redirects back when
    # the API account has a Callback URL registered — without one it just shows
    # its own "connected" page — so the token has to be redeemable on return.
    pending: bool = False


def current_user_id(ds_session: str | None = Cookie(default=None)) -> str | None:
    payload = session.verify(ds_session)
    return payload.get("uid") if payload else None


def pending_token(ds_pending: str | None = Cookie(default=None)) -> str | None:
    payload = session.verify(ds_pending, max_age=session.PENDING_MAX_AGE)
    return payload.get("tok") if payload else None


def _sign_in(response: Response, token: str) -> str:
    """Trade an approved token for a session and attach the cookie. Raises
    LastfmAuthError if the user never approved it."""
    username, session_key = lastfm_auth.get_session(token)
    user_id = db.upsert_user(username, session_key)
    _set_session_cookie(response, user_id)
    response.delete_cookie(session.PENDING_COOKIE_NAME, path="/")
    return username


def _set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        session.COOKIE_NAME,
        session.sign({"uid": user_id}),
        httponly=True,  # JS must never read it
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=session.DEFAULT_MAX_AGE,
        path="/",
    )


@app.get("/me", response_model=Me)
def me(
    user_id: str | None = Depends(current_user_id),
    token: str | None = Depends(pending_token),
) -> Me:
    if not user_id:
        return Me(pending=bool(token))
    user = db.get_user(user_id)  # cannot return the session key by construction
    if user is None:
        return Me(pending=bool(token))
    return Me(lastfm_username=user["lastfm_username"], connected=True)


@app.get("/auth/lastfm/start")
def lastfm_start() -> RedirectResponse:
    """Send the user to Last.fm to approve us, remembering the request token.

    The token is kept in a short-lived signed cookie so the approval can be
    completed on the user's return even if Last.fm never calls us back.
    """
    try:
        token = lastfm_auth.get_token()
    except lastfm_auth.LastfmAuthError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    response = RedirectResponse(
        lastfm_auth.auth_url(token, settings.lastfm_callback_url), status_code=307
    )
    response.set_cookie(
        session.PENDING_COOKIE_NAME,
        session.sign({"tok": token}),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=session.PENDING_MAX_AGE,
        path="/",
    )
    return response


@app.get("/auth/lastfm/callback")
def lastfm_callback(
    token: str | None = None, pending: str | None = Depends(pending_token)
) -> RedirectResponse:
    """Trade the approved token for a session and sign the user in.

    Uses the token Last.fm hands back, falling back to the one we stashed at
    the start of the flow — a user who returns by any route still lands signed in.
    """
    token = token or pending
    if not token:
        return RedirectResponse(f"{settings.web_url}/?lastfm=denied", status_code=303)

    response = RedirectResponse(f"{settings.web_url}/?lastfm=connected", status_code=303)
    try:
        _sign_in(response, token)
    except lastfm_auth.LastfmAuthError:
        # Declined, expired, or replayed — never surface Last.fm's wording.
        return RedirectResponse(f"{settings.web_url}/?lastfm=failed", status_code=303)
    return response


@app.post("/auth/lastfm/complete", response_model=Me)
def lastfm_complete(response: Response, pending: str | None = Depends(pending_token)) -> Me:
    """Finish a connection whose callback never arrived.

    Last.fm only redirects back when the API account has a Callback URL
    registered; without one it shows its own success page and leaves the user
    to navigate back. The approved token is still redeemable, so this closes
    the loop from the app side.
    """
    if not pending:
        raise HTTPException(status_code=409, detail="no connection in progress")
    try:
        username = _sign_in(response, pending)
    except lastfm_auth.LastfmAuthError as e:
        # Drop the dead token so the page doesn't retry on every load. This has
        # to be a returned Response, not a raised HTTPException: raising builds
        # a fresh response and discards the cookie header set here.
        failure = JSONResponse(
            status_code=409, content={"detail": f"not approved on Last.fm yet: {e}"}
        )
        failure.delete_cookie(session.PENDING_COOKIE_NAME, path="/")
        return failure
    return Me(lastfm_username=username, connected=True)


@app.post("/auth/logout", status_code=204)
def logout(response: Response) -> Response:
    """Forget the browser. The stored Last.fm session key is left intact so
    reconnecting doesn't need another round trip to Last.fm."""
    response.delete_cookie(session.COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@app.get("/setlists", response_model=list[SetlistSummary])
def list_setlists(
    q: str | None = None,
    dj: list[str] | None = Query(None),
    genre: list[str] | None = Query(None),
    event: list[str] | None = Query(None),
    year: list[str] | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[SetlistSummary]:
    """Browse rows. Filters repeat to multi-select: `?dj=A&dj=B` matches either.

    There's no total count — callers asking for `limit + 1` and checking whether
    they got it is enough to drive a "load more", and avoids a second query.
    """
    rows = db.list_summaries(
        limit=limit, offset=offset, q=q, dj=dj, genre=genre, event=event, year=year
    )
    return [SetlistSummary(**row) for row in rows]


@app.get("/facets", response_model=Facets)
def list_facets() -> Facets:
    """What the filter chips can offer, counted over the whole catalog."""
    return Facets(**db.facets())


@app.get("/setlists/{setlist_id}", response_model=SetlistDetail)
def get_setlist(setlist_id: str) -> SetlistDetail:
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, meta = result
    return SetlistDetail(setlist=setlist, **meta)


@app.get("/setlists/{setlist_id}/cues", response_model=WindowSet)
def setlist_cues(
    setlist_id: str,
    duration: int | None = Query(None, ge=0, description="Recording length in seconds, when known"),
    user_id: str | None = Depends(current_user_id),
) -> WindowSet:
    """Cue windows: which track is playing when, and under what name.

    The client reports the player's duration; everything else is derived here so
    the highlight and the scrobbler can never disagree about what's playing.
    Eligibility reflects the signed-in user's settings, so the tracklist shows
    exactly what would be scrobbled.
    """
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, _meta = result
    config = _user_config(user_id) if user_id else ScrobbleConfig()
    return build_windows(setlist, media_duration_s=duration, config=config)


# ---------------------------------------------------------------------------
# Scrobbling. The client reports a playhead and an intent; the server re-derives
# the window, re-checks eligibility against the user's settings, and signs the
# submission. A client that lies about what is playing still gets the window the
# server computed.
#
# Nothing about a play is stored. There is no scrobble log and no dedupe table:
# replaying a track is a second listen and should scrobble again, which is the
# only thing a dedupe table would have prevented. The browser keeps enough state
# for one page session to stop a single window firing twice while you sit in it.
# ---------------------------------------------------------------------------


class ScrobbleTarget(BaseModel):
    setlist_id: str
    position: int
    component_index: int | None = None
    # The player's reported length, so the server's last window matches the
    # client's view of the recording.
    duration: int | None = None
    started_at: int | None = None  # unix seconds; defaults to "just now"


class ScrobbleResult(BaseModel):
    scrobbled: bool
    reason: str | None = None
    artist: str | None = None
    track: str | None = None


class ScrobbleSetRequest(BaseModel):
    duration: int | None = None
    started_at: int | None = None  # when the set began; defaults to just-finished


class ScrobbleSetResult(BaseModel):
    submitted: int
    accepted: int
    skipped: int
    timing: str
    problems: list[str] = []


def _require_user(user_id: str | None) -> str:
    if not user_id:
        raise HTTPException(status_code=401, detail="connect Last.fm first")
    return user_id


def _user_config(user_id: str) -> ScrobbleConfig:
    try:
        return ScrobbleConfig(**(db.get_scrobble_config(user_id) or {}))
    except Exception:
        return ScrobbleConfig()  # a malformed stored config must not block scrobbling


def _windows_for_user(setlist_id: str, user_id: str, duration: int | None):
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, _meta = result
    return build_windows(setlist, media_duration_s=duration, config=_user_config(user_id))


def _find_window(windows, position: int, component_index: int | None):
    for w in windows:
        if w.position == position and w.component_index == component_index:
            return w
    return None


@app.post("/scrobble/now-playing", response_model=ScrobbleResult)
def now_playing(body: ScrobbleTarget, user_id: str | None = Depends(current_user_id)) -> ScrobbleResult:
    """Flag what's playing. Not a play — no dedupe, nothing recorded."""
    uid = _require_user(user_id)
    ws = _windows_for_user(body.setlist_id, uid, body.duration)
    window = _find_window(ws.windows, body.position, body.component_index)
    if window is None:
        raise HTTPException(status_code=404, detail="no such track in this set")
    if not window.eligible:
        return ScrobbleResult(scrobbled=False, reason=window.reason)

    key = db.session_key_for(uid)
    if not key:
        raise HTTPException(status_code=401, detail="connect Last.fm first")
    try:
        submit.update_now_playing(
            key, window.scrobble_artist, window.scrobble_track, window.duration_s,
            album=ws.album, album_artist=ws.album_artist,
        )
    except submit.LastfmAuthError as e:
        return ScrobbleResult(scrobbled=False, reason=str(e))
    return ScrobbleResult(
        scrobbled=False, artist=window.scrobble_artist, track=window.scrobble_track
    )


@app.post("/scrobble", response_model=ScrobbleResult)
def scrobble_one(body: ScrobbleTarget, user_id: str | None = Depends(current_user_id)) -> ScrobbleResult:
    """Scrobble the track at a position. Idempotent within a playback session."""
    uid = _require_user(user_id)
    ws = _windows_for_user(body.setlist_id, uid, body.duration)
    window = _find_window(ws.windows, body.position, body.component_index)
    if window is None:
        raise HTTPException(status_code=404, detail="no such track in this set")
    if not window.eligible:
        return ScrobbleResult(scrobbled=False, reason=window.reason)

    key = db.session_key_for(uid)
    if not key:
        raise HTTPException(status_code=401, detail="connect Last.fm first")

    played_at = _played_at(body.started_at, window.duration_s)
    try:
        submit.scrobble(
            key,
            [
                submit.Play(
                    artist=window.scrobble_artist,
                    track=window.scrobble_track,
                    timestamp=int(played_at.timestamp()),
                    album=ws.album,
                    album_artist=ws.album_artist,
                )
            ],
        )
    except submit.LastfmAuthError as e:
        return ScrobbleResult(scrobbled=False, reason=str(e))

    return ScrobbleResult(
        scrobbled=True, artist=window.scrobble_artist, track=window.scrobble_track
    )


def _played_at(started_at: int | None, window_s: int) -> datetime:
    """When the play started, clamped to the past — Last.fm rejects the future."""
    now = datetime.now(timezone.utc)
    if started_at:
        stamp = datetime.fromtimestamp(started_at, tz=timezone.utc)
    else:
        stamp = now - timedelta(seconds=min(window_s, 600))
    return min(stamp, now)


@app.post("/setlists/{setlist_id}/scrobble-set", response_model=ScrobbleSetResult)
def scrobble_whole_set(
    setlist_id: str, body: ScrobbleSetRequest, user_id: str | None = Depends(current_user_id)
) -> ScrobbleSetResult:
    """Log the whole set in one action.

    This is the mode that makes a set without cue times useful: timings are
    estimated rather than exact, but the content and order are right. Timestamps
    run from `started_at` (defaulting to just-finished) and never reach into the
    future.
    """
    uid = _require_user(user_id)
    ws = _windows_for_user(setlist_id, uid, body.duration)
    key = db.session_key_for(uid)
    if not key:
        raise HTTPException(status_code=401, detail="connect Last.fm first")

    total = ws.duration_s or (ws.windows[-1].end_s if ws.windows else 0)
    now = datetime.now(timezone.utc)
    origin = (
        datetime.fromtimestamp(body.started_at, tz=timezone.utc)
        if body.started_at
        else now - timedelta(seconds=total)
    )

    plays: list[submit.Play] = []
    skipped = 0

    for window in ws.windows:
        if not window.eligible:
            skipped += 1
            continue
        played_at = min(origin + timedelta(seconds=window.start_s), now)
        plays.append(
            submit.Play(
                artist=window.scrobble_artist,
                track=window.scrobble_track,
                timestamp=int(played_at.timestamp()),
                album=ws.album,
                album_artist=ws.album_artist,
            )
        )

    if not plays:
        return ScrobbleSetResult(submitted=0, accepted=0, skipped=skipped, timing=ws.timing)

    try:
        result = submit.scrobble(key, plays)
    except submit.LastfmAuthError as e:
        raise HTTPException(status_code=502, detail=f"Last.fm rejected the batch: {e}") from e

    return ScrobbleSetResult(
        submitted=len(plays),
        accepted=result.accepted,
        skipped=skipped,
        timing=ws.timing,
        problems=[text for _, text in result.problems][:10],
    )


@app.get("/me/scrobble-config", response_model=ScrobbleConfig)
def read_scrobble_config(user_id: str | None = Depends(current_user_id)) -> ScrobbleConfig:
    return _user_config(_require_user(user_id))


@app.put("/me/scrobble-config", response_model=ScrobbleConfig)
def write_scrobble_config(
    body: ScrobbleConfig, user_id: str | None = Depends(current_user_id)
) -> ScrobbleConfig:
    db.set_scrobble_config(_require_user(user_id), body.model_dump())
    return body


@app.post("/setlists/{setlist_id}/export", response_model=ExportPreview)
def export_preview(setlist_id: str, body: ExportPreviewRequest) -> ExportPreview:
    """Dry-run preview: the ordered add-list + skip report for a target platform.

    Network-free (uses exporter.collect.build_plan). Actual playlist creation
    needs a connected account and lands with the export phase.
    """
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, _meta = result

    from soundseek.exporter.collect import build_plan

    plan = build_plan(
        setlist,
        body.target,
        expand_mashups=body.expand_mashups,
        skip_played_with=body.skip_played_with,
    )
    return ExportPreview(
        target=plan.target,
        added=plan.added,
        total_considered=plan.total_considered,
        items=[ExportPlanItem(id=i.id, label=i.label) for i in plan.items],
        skipped=[ExportSkippedItem(label=label, reason=reason) for label, reason in plan.skipped],
    )

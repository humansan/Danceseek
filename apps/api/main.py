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

from typing import Literal

import soundseek.config  # noqa: F401 — loads .env (DATABASE_URL etc.)
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from soundseek import db, session
from soundseek.config import settings
from soundseek.lastfm import auth as lastfm_auth
from soundseek.models import Setlist
from soundseek.scrobble.windows import WindowSet, build_windows

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
    dj: str | None = None,
    genre: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[SetlistSummary]:
    rows = db.list_summaries(limit=limit, offset=offset, q=q, dj=dj, genre=genre)
    return [SetlistSummary(**row) for row in rows]


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
) -> WindowSet:
    """Cue windows: which track is playing when, and under what name.

    The client reports the player's duration; everything else is derived here so
    the highlight and the scrobbler can never disagree about what's playing.
    """
    result = db.get_by_id(setlist_id)
    if result is None:
        raise HTTPException(status_code=404, detail="setlist not found")
    setlist, _meta = result
    return build_windows(setlist, media_duration_s=duration)


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

"""Fetch 1001tracklists pages, cache-first, behind a swappable backend.

1001tracklists sits behind Cloudflare, so plain HTTP clients get blocked.
Getting past it needs a real browser with a persistent profile (which keeps
the clearance cookie between runs) — something we only want to do on a
human-operated machine, not on a server.

So `fetch()` dispatches on `SOUNDSEEK_FETCH_BACKEND`:

  local    the maintainer's machine (and dev): drive real Chromium via
           Playwright, disk-cache the HTML. The default.
  stored   the server/worker: read a page captured earlier and uploaded to
           Postgres (`raw_pages`). Never launches a browser — Playwright is
           not even imported — so the deployed image stays small.
  managed  a third-party browser API. Not implemented; the seam exists so it
           can be added without touching the rest of the pipeline.

Everything above `fetch()` (extract → normalize → resolve) is identical
regardless of where the HTML came from.
"""

from __future__ import annotations

import hashlib
import re
import time

from .config import settings

TRACKLIST_URL_RE = re.compile(r"^https?://(www\.)?1001tracklists\.com/tracklist/.+", re.IGNORECASE)

# Signs that we're looking at a challenge/interstitial, not the real page:
# Cloudflare's challenge page, or 1001TL's own JS forwarding page.
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-chl",
    "challenge-platform",
    "you will be forwarded",
)

# Present on real tracklist pages; used as a "content is ready" signal.
_TRACKLIST_SELECTOR = "div.tlpItem, #tlTab, div[itemprop='track']"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class FetchError(RuntimeError):
    """Raised when a page cannot be fetched or looks blocked."""


def validate_url(url: str) -> None:
    if not TRACKLIST_URL_RE.match(url):
        raise FetchError(
            f"Not a 1001tracklists tracklist URL: {url}\n"
            "Expected something like https://www.1001tracklists.com/tracklist/<id>/<slug>.html"
        )


def url_digest(url: str) -> str:
    """Short stable hash used to key per-URL files (raw_html, llm_inputs)."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def cache_path(url: str):
    return settings.raw_html_dir / f"{url_digest(url)}.html"


def _looks_blocked(html: str) -> bool:
    low = html.lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


def _fetch_live(url: str) -> str:
    """Fetch the page with a real browser, waiting out any Cloudflare challenge.

    Playwright is imported here rather than at module scope so the `stored`
    backend works on a machine where it isn't installed.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    def content(page) -> str:
        """page.content() that tolerates in-flight navigations (the forwarding page)."""
        try:
            return page.content()
        except PlaywrightError:
            page.wait_for_timeout(1000)
            return page.content()

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(settings.browser_profile_dir),
            headless=settings.headless,
            user_agent=_UA,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=settings.fetch_timeout_seconds * 1000)

            # Wait out Cloudflare challenges and 1001TL's JS forwarding page,
            # both of which navigate to the real page on their own.
            deadline = time.monotonic() + settings.fetch_timeout_seconds
            while _looks_blocked(content(page)):
                if time.monotonic() > deadline:
                    raise FetchError(
                        f"Challenge/forwarding page did not clear within "
                        f"{settings.fetch_timeout_seconds:.0f}s for {url}. "
                        "Try SOUNDSEEK_HEADLESS=false to solve it interactively once; "
                        "the browser profile keeps the clearance cookie afterwards."
                    )
                page.wait_for_timeout(1500)

            # Wait for tracklist markup (best effort — extractor validates for real).
            try:
                page.wait_for_selector(_TRACKLIST_SELECTOR, timeout=15_000)
            except PlaywrightTimeoutError:
                pass  # layout may have changed; the extractor will complain loudly
            return content(page)
        finally:
            context.close()


def fetch_local(url: str, force: bool = False) -> str:
    """Fetch with a real browser here, disk-cached so a URL is fetched at most once.

    Called directly by `soundseek publish` (the maintainer capture step), which
    must drive a browser regardless of the configured backend.
    """
    validate_url(url)
    path = cache_path(url)
    if path.exists() and not force:
        return path.read_text(encoding="utf-8")

    time.sleep(settings.fetch_delay_seconds)  # politeness: never hammer the site
    html = _fetch_live(url)

    settings.raw_html_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return html


def fetch_stored(url: str) -> str:
    """Read a page captured earlier and uploaded to Postgres."""
    from . import db

    html = db.get_page(url)
    if html is None:
        raise FetchError(
            f"No captured page for {url}. This server does not scrape "
            "(SOUNDSEEK_FETCH_BACKEND=stored) — capture it from a machine with a "
            "browser profile first:  soundseek publish <url>"
        )
    return html


def fetch(url: str, force: bool = False) -> str:
    """Return page HTML for `url` via the configured backend."""
    backend = settings.fetch_backend
    if backend == "local":
        return fetch_local(url, force=force)
    if backend == "stored":
        validate_url(url)
        return fetch_stored(url)
    if backend == "managed":
        raise FetchError(
            "SOUNDSEEK_FETCH_BACKEND=managed is not implemented yet "
            "(it lands with the managed-browser phase). Use 'local' or 'stored'."
        )
    raise FetchError(
        f"Unknown SOUNDSEEK_FETCH_BACKEND={backend!r}; expected 'local', 'stored', or 'managed'."
    )

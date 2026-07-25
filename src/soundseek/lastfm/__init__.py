"""Last.fm write-side API: identity now, scrobble submission later.

Read-side lookups (track.getInfo / track.search, used by resolution) live in
resolver/clients.py and need only the API key. Everything here needs the
shared secret, because every call is signed.
"""

from .auth import LastfmAuthError, api_sig, auth_url, get_session, get_token

__all__ = ["LastfmAuthError", "api_sig", "auth_url", "get_session", "get_token"]

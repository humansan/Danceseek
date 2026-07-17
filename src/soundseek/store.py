"""Setlist persistence facade.

Delegates to the JSON backend (local/CLI/dev) or the Postgres/Neon backend,
selected by `settings.store_backend`. Call sites import this module unchanged:
`from . import store; store.save(...)`.
"""

from __future__ import annotations

from .config import settings

if settings.store_backend == "postgres":
    from . import store_pg as _backend
else:
    from . import store_json as _backend

lookup = _backend.lookup
setlist_path = _backend.setlist_path
save = _backend.save
load = _backend.load
load_by_url = _backend.load_by_url
list_all = _backend.list_all

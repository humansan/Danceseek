"use client";

/**
 * Top-right Last.fm control (design §4.1): connect when signed out, the
 * username when signed in.
 *
 * Everything goes through the web app's own origin (`/api/*` is rewritten to
 * the API) so the session cookie is same-site. The cookie is HttpOnly, so this
 * component learns who you are only by asking `/api/me`.
 */

import { useCallback, useEffect, useState } from "react";

type Me = { lastfm_username: string | null; connected: boolean; pending?: boolean };

const SIGNED_OUT: Me = { lastfm_username: null, connected: false };

export function LastfmControl() {
  const [me, setMe] = useState<Me | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const refresh = useCallback(async (): Promise<Me> => {
    try {
      const r = await fetch("/api/me", { credentials: "include", cache: "no-store" });
      const next: Me = r.ok ? await r.json() : SIGNED_OUT;
      setMe(next);
      return next;
    } catch {
      setMe(SIGNED_OUT);
      return SIGNED_OUT;
    }
  }, []);

  const complete = useCallback(async () => {
    setBusy(true);
    try {
      const r = await fetch("/api/auth/lastfm/complete", {
        method: "POST",
        credentials: "include",
      });
      if (r.ok) setMe(await r.json());
      else {
        setFailed(true);
        await refresh();
      }
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  // Last.fm doesn't always redirect back (it only does so when the API account
  // has a Callback URL registered). If an approval is still in flight when we
  // load, redeem it here — that turns "connected on Last.fm but not in the
  // app" into just working.
  useEffect(() => {
    refresh().then((next) => {
      if (!next.connected && next.pending) complete();
    });
  }, [refresh, complete]);

  const disconnect = async () => {
    setBusy(true);
    try {
      await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  // Every control in the bar is h-8 so the row stays optically aligned.
  const chip = "flex h-8 items-center px-3 font-mono text-xs";

  // Until /me answers, render the signed-out shape so the bar doesn't jump.
  if (!me?.connected) {
    if (busy) {
      return <span className={`${chip} border border-lastfm/60 text-lastfm`}>finishing…</span>;
    }
    return (
      <div className="flex items-center gap-2">
        {failed ? (
          <span
            className="font-mono text-[11px] text-warn"
            title="approve the app on Last.fm, then try again"
          >
            not approved
          </span>
        ) : null}
        <a
          href="/api/auth/lastfm/start"
          className={`${chip} border border-lastfm bg-lastfm/10 text-lastfm hover:bg-lastfm hover:text-bg`}
        >
          connect last.fm
        </a>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className={`${chip} border border-lastfm/50 gap-2 text-fg`}>
        <span className="text-lastfm" title="connected to Last.fm">
          ●
        </span>
        {me.lastfm_username}
      </span>
      <button
        onClick={disconnect}
        disabled={busy}
        className={`${chip} cursor-pointer border border-border text-dim hover:border-lastfm hover:text-lastfm disabled:opacity-50`}
      >
        {busy ? "…" : "disconnect"}
      </button>
    </div>
  );
}

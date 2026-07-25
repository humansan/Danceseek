"use client";

/**
 * Shares one scrobbler across the app: the bottom bar shows its status, the
 * setlist page drives it, and the tracklist reflects what has been logged.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useScrobbler, type ScrobbleState } from "./useScrobbler";

export type ScrobbleConfig = {
  layered: "scrobble" | "skip";
  mashups: "all" | "primary" | "skip";
  unreleased: "scrobble" | "skip";
  unmatched: "scrobble" | "skip";
};

type Ctx = ScrobbleState & {
  connected: boolean;
  username: string | null;
  config: ScrobbleConfig | null;
  saveConfig: (next: ScrobbleConfig) => Promise<void>;
  refreshIdentity: () => void;
};

const ScrobbleContext = createContext<Ctx | null>(null);

export function useScrobble(): Ctx {
  const ctx = useContext(ScrobbleContext);
  if (!ctx) throw new Error("useScrobble must be used inside <ScrobbleProvider>");
  return ctx;
}

export function ScrobbleProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [username, setUsername] = useState<string | null>(null);
  const [config, setConfig] = useState<ScrobbleConfig | null>(null);
  const scrobbler = useScrobbler(connected);

  const refreshIdentity = useCallback(() => {
    fetch("/api/me", { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((me) => {
        setConnected(!!me?.connected);
        setUsername(me?.lastfm_username ?? null);
        if (!me?.connected) return;
        return fetch("/api/me/scrobble-config", { credentials: "include", cache: "no-store" })
          .then((r) => (r.ok ? r.json() : null))
          .then(setConfig);
      })
      .catch(() => setConnected(false));
  }, []);

  useEffect(refreshIdentity, [refreshIdentity]);

  const saveConfig = useCallback(async (next: ScrobbleConfig) => {
    setConfig(next); // optimistic: the panel should feel instant
    await fetch("/api/me/scrobble-config", {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(next),
    }).catch(() => undefined);
  }, []);

  const value = useMemo<Ctx>(
    () => ({ ...scrobbler, connected, username, config, saveConfig, refreshIdentity }),
    [scrobbler, connected, username, config, saveConfig, refreshIdentity],
  );

  return <ScrobbleContext.Provider value={value}>{children}</ScrobbleContext.Provider>;
}

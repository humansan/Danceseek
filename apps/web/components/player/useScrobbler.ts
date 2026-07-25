"use client";

/**
 * Live scrobbling: watch the playhead, report intent, let the server decide.
 *
 * Last.fm's own rule is "half the track or four minutes, whichever comes
 * first", with a 30s floor — we apply it against the cue window's length. The
 * dwell clock only runs while the video is actually playing, and seeking out of
 * a window before the threshold cancels it, so scrubbing through a set doesn't
 * log tracks you didn't hear.
 *
 * There is no server-side scrobble log. The guard here is per *window entry*,
 * not per track: sitting inside one window fires once, but seeking back and
 * listening through it again is a genuine replay and scrobbles again — which is
 * the record it should produce. `done` only drives the ✓ marks.
 *
 * The server re-derives the window and re-checks the user's settings; nothing
 * reported here is trusted.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePlayer } from "./PlayerProvider";

const MAX_DWELL_S = 240; // Last.fm's four-minute cap
const MIN_WINDOW_S = 30;

export type ScrobbleState = {
  enabled: boolean;
  setEnabled: (on: boolean) => void;
  /** "position:componentIndex" keys logged since this page loaded — display only. */
  done: Set<string>;
  lastError: string | null;
  scrobbleWholeSet: () => Promise<string>;
  busy: boolean;
  /** True once the whole set has been logged, so the button can say "again". */
  setLogged: boolean;
};

const keyOf = (position: number, component: number | null) => `${position}:${component ?? -1}`;

/** The master switch is a device preference: on unless explicitly turned off. */
const SWITCH_KEY = "danceseek:scrobbling";

function storedSwitch(): boolean {
  if (typeof window === "undefined") return true;
  return window.localStorage.getItem(SWITCH_KEY) !== "off";
}

export function useScrobbler(connected: boolean): ScrobbleState {
  const { loaded, playing, playhead, duration, currentIndex } = usePlayer();
  // On by default once Last.fm is connected — having to arm it per set was
  // just a way to miss scrobbles. The settings panel can freeze it globally.
  const [enabled, setEnabledState] = useState(true);

  // localStorage isn't readable during SSR, so adopt the stored value on mount.
  useEffect(() => setEnabledState(storedSwitch()), []);

  const setEnabled = useCallback((on: boolean) => {
    setEnabledState(on);
    try {
      window.localStorage.setItem(SWITCH_KEY, on ? "on" : "off");
    } catch {
      /* private mode — the session default still applies */
    }
  }, []);
  const [done, setDone] = useState<Set<string>>(new Set());
  const [lastError, setLastError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [setLogged, setSetLogged] = useState(false);

  const dwellRef = useRef(0);
  // Cleared on every window entry — that's what makes a replay scrobble again.
  const firedRef = useRef(false);
  const nowPlayingRef = useRef<string | null>(null);
  const tickRef = useRef<number>(0);

  const setId = loaded?.setId ?? null;
  const windows = useMemo(() => loaded?.windows ?? [], [loaded]);
  const current = currentIndex >= 0 ? windows[currentIndex] : undefined;

  // A new set starts fresh.
  useEffect(() => {
    setDone(new Set());
    setSetLogged(false);
    dwellRef.current = 0;
    firedRef.current = false;
    nowPlayingRef.current = null;
  }, [setId]);

  const post = useCallback(
    async (path: string, body: unknown) => {
      const r = await fetch(path, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    },
    [],
  );

  // Entering a window: tell Last.fm what's playing and restart the dwell clock.
  useEffect(() => {
    if (!enabled || !connected || !setId || !current) return;
    const key = keyOf(current.position, current.component_index ?? null);
    if (nowPlayingRef.current === key) return;

    nowPlayingRef.current = key;
    dwellRef.current = 0;
    firedRef.current = false; // re-entering a window arms it again

    if (!current.eligible) return;
    post("/api/scrobble/now-playing", {
      setlist_id: setId,
      position: current.position,
      component_index: current.component_index,
      duration: duration || undefined,
    }).catch(() => {
      /* a status update is best-effort */
    });
  }, [enabled, connected, setId, current, duration, post]);

  // The dwell clock. Runs only while playing, so pausing pauses it.
  useEffect(() => {
    if (!enabled || !connected || !playing || !setId || !current?.eligible) return;

    tickRef.current = window.setInterval(() => {
      dwellRef.current += 1;
      const length = current.end_s - current.start_s;
      if (length < MIN_WINDOW_S) return;

      const threshold = Math.min(length / 2, MAX_DWELL_S);
      const key = keyOf(current.position, current.component_index ?? null);
      // Once per stay in this window. Leaving and coming back re-arms it, so a
      // replay logs a second listen — which is what a replay is.
      if (dwellRef.current < threshold || firedRef.current) return;

      firedRef.current = true;
      post("/api/scrobble", {
        setlist_id: setId,
        position: current.position,
        component_index: current.component_index,
        duration: duration || undefined,
        // The play started when the window did, not when the threshold passed.
        started_at: Math.floor(Date.now() / 1000 - dwellRef.current),
      })
        .then((result) => {
          if (result?.scrobbled) setDone((prev) => new Set(prev).add(key));
          else if (result?.reason) setLastError(result.reason);
        })
        .catch(() => setLastError("could not reach the scrobbler"));
    }, 1000);

    return () => window.clearInterval(tickRef.current);
  }, [enabled, connected, playing, setId, current, duration, post]);

  // Seeking away before the threshold cancels the pending scrobble.
  useEffect(() => {
    if (!current) return;
    if (playhead < current.start_s || playhead > current.end_s) dwellRef.current = 0;
  }, [playhead, current]);

  const scrobbleWholeSet = useCallback(async (): Promise<string> => {
    if (!setId) return "nothing loaded";
    setBusy(true);
    try {
      const r = await post(`/api/setlists/${setId}/scrobble-set`, {
        duration: duration || undefined,
      });
      setDone((prev) => {
        const next = new Set(prev);
        for (const w of windows) if (w.eligible) next.add(keyOf(w.position, w.component_index ?? null));
        return next;
      });
      setSetLogged(true);
      const estimated = r.timing === "estimated" ? " (timings estimated)" : "";
      return `scrobbled ${r.accepted}/${r.submitted}${estimated} · ${r.skipped} skipped`;
    } catch {
      return "could not reach the scrobbler";
    } finally {
      setBusy(false);
    }
  }, [setId, duration, windows, post]);

  return { enabled, setEnabled, done, lastError, scrobbleWholeSet, busy, setLogged };
}

export { keyOf };

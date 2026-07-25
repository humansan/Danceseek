"use client";

/**
 * One YouTube player for the whole app.
 *
 * The set recording must survive navigation (browse while a set plays) *and*
 * appear large inside the setlist page. Those two demands rule out re-parenting
 * the iframe — moving an iframe in the DOM reloads it, restarting playback — so
 * the element is created once here, never moved, and positioned over whatever
 * slot should hold it (see PlayerSurface).
 *
 * The playhead lives here too, because the tracklist highlight, the bottom bar
 * and (later) the scrobbler must all agree on what is playing.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CueWindow } from "@/lib/api";

/** Everything the player needs to show a set. */
export type LoadedSet = {
  setId: string;
  videoId: string;
  title: string;
  windows: CueWindow[];
  /** False when the set has no cue times — no live highlight is possible. */
  liveCapable: boolean;
};

type PlayerState = {
  loaded: LoadedSet | null;
  ready: boolean;
  playing: boolean;
  playhead: number;
  duration: number;
  /** Index into `loaded.windows` under the playhead, or -1. */
  currentIndex: number;
  current: CueWindow | null;
  load: (set: LoadedSet) => void;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  seek: (seconds: number) => void;
  step: (delta: -1 | 1) => void;
  /** The host element the iframe is created into (PlayerSurface renders it). */
  hostRef: (el: HTMLDivElement | null) => void;
};

const PlayerContext = createContext<PlayerState | null>(null);

export function usePlayer(): PlayerState {
  const ctx = useContext(PlayerContext);
  if (!ctx) throw new Error("usePlayer must be used inside <PlayerProvider>");
  return ctx;
}

// --- YouTube IFrame API ------------------------------------------------------

declare global {
  interface Window {
    YT?: { Player: new (el: HTMLElement, opts: unknown) => YTPlayer; loaded?: number };
    onYouTubeIframeAPIReady?: () => void;
  }
}

type YTPlayer = {
  playVideo(): void;
  pauseVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  getCurrentTime(): number;
  getDuration(): number;
  loadVideoById(id: string): void;
  destroy(): void;
};

let apiPromise: Promise<void> | null = null;

function loadYouTubeApi(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.YT?.Player) return Promise.resolve();
  if (apiPromise) return apiPromise;

  apiPromise = new Promise((resolve) => {
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      previous?.();
      resolve();
    };
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(script);
  });
  return apiPromise;
}

/**
 * The window under `seconds`. Windows are ordered and contiguous; layered `w/`
 * rows share their anchor's window, and among those ties the anchor row is the
 * one we call "playing" (the layered track is playing *over* it).
 */
function findWindow(windows: CueWindow[], seconds: number): number {
  let found = -1;
  for (let i = windows.length - 1; i >= 0; i--) {
    if (seconds >= windows[i].start_s) {
      found = i;
      break;
    }
  }
  if (found < 0) return windows.length ? 0 : -1;
  while (found > 0 && windows[found - 1].start_s === windows[found].start_s) found--;
  return found;
}

export function PlayerProvider({ children }: { children: React.ReactNode }) {
  const [loaded, setLoaded] = useState<LoadedSet | null>(null);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [playhead, setPlayhead] = useState(0);
  const [duration, setDuration] = useState(0);

  const playerRef = useRef<YTPlayer | null>(null);
  const hostElRef = useRef<HTMLDivElement | null>(null);
  const pendingVideoRef = useRef<string | null>(null);

  const hostRef = useCallback((el: HTMLDivElement | null) => {
    hostElRef.current = el;
  }, []);

  // Create the player once, on the first set that has a recording. Subsequent
  // sets swap the video in place — the element itself is never recreated.
  useEffect(() => {
    const videoId = loaded?.videoId;
    if (!videoId) return;

    if (playerRef.current) {
      if (pendingVideoRef.current !== videoId) {
        pendingVideoRef.current = videoId;
        playerRef.current.loadVideoById(videoId);
        setPlayhead(0);
      }
      return;
    }

    let cancelled = false;
    pendingVideoRef.current = videoId;

    loadYouTubeApi().then(() => {
      if (cancelled || !hostElRef.current || playerRef.current || !window.YT) return;
      playerRef.current = new window.YT.Player(hostElRef.current, {
        videoId,
        playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
        events: {
          onReady: (e: { target: YTPlayer }) => {
            setReady(true);
            setDuration(Math.round(e.target.getDuration()));
          },
          // 1 playing · 2 paused · 0 ended
          onStateChange: (e: { data: number; target: YTPlayer }) => {
            setPlaying(e.data === 1);
            if (e.data === 1) setDuration(Math.round(e.target.getDuration()));
            if (e.data === 0) setPlaying(false);
          },
        },
      });
    });

    return () => {
      cancelled = true;
    };
  }, [loaded?.videoId]);

  // Track the playhead. The IFrame API has no timeupdate event, so poll — but
  // only while playing, and only fast enough for a 1s-resolution highlight.
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      const player = playerRef.current;
      if (!player) return;
      setPlayhead(player.getCurrentTime());
      const d = Math.round(player.getDuration());
      if (d) setDuration((prev) => (prev === d ? prev : d));
    }, 250);
    return () => window.clearInterval(id);
  }, [playing]);

  const load = useCallback((next: LoadedSet) => {
    setLoaded((prev) =>
      prev && prev.setId === next.setId && prev.videoId === next.videoId ? prev : next,
    );
  }, []);

  const play = useCallback(() => playerRef.current?.playVideo(), []);
  const pause = useCallback(() => playerRef.current?.pauseVideo(), []);
  const toggle = useCallback(() => {
    if (playing) playerRef.current?.pauseVideo();
    else playerRef.current?.playVideo();
  }, [playing]);

  const seek = useCallback((seconds: number) => {
    const target = Math.max(0, seconds);
    playerRef.current?.seekTo(target, true);
    setPlayhead(target); // update the highlight immediately, don't wait for the poll
  }, []);

  const windows = loaded?.windows ?? [];
  const currentIndex = useMemo(
    () => (loaded?.liveCapable ? findWindow(windows, playhead) : -1),
    [loaded?.liveCapable, windows, playhead],
  );

  const step = useCallback(
    (delta: -1 | 1) => {
      if (!windows.length) return;
      const from = currentIndex < 0 ? 0 : currentIndex;
      // "previous" restarts the current track first, like every music player.
      if (delta === -1 && playhead - windows[from].start_s > 3) {
        seek(windows[from].start_s);
        return;
      }
      const next = Math.min(windows.length - 1, Math.max(0, from + delta));
      seek(windows[next].start_s);
    },
    [windows, currentIndex, playhead, seek],
  );

  const value = useMemo<PlayerState>(
    () => ({
      loaded,
      ready,
      playing,
      playhead,
      duration,
      currentIndex,
      current: currentIndex >= 0 ? windows[currentIndex] ?? null : null,
      load,
      play,
      pause,
      toggle,
      seek,
      step,
      hostRef,
    }),
    [loaded, ready, playing, playhead, duration, currentIndex, windows, load, play, pause, toggle, seek, step, hostRef],
  );

  return <PlayerContext.Provider value={value}>{children}</PlayerContext.Provider>;
}

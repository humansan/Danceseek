"use client";

/**
 * The interactive half of the setlist page: hand the set to the player, and
 * render the tracklist synced to its playhead.
 *
 * The tracklist is the hero content (design §5.2), so the row treatments carry
 * the CLI's status vocabulary — lit pills for what resolved, indents for `w/`,
 * nested components for mashups, and the `▮▮▯` glyph on whatever is playing.
 */

import { useEffect, useMemo } from "react";
import { usePlayer } from "@/components/player/PlayerProvider";
import { formatTime } from "@/components/player/BottomBar";
import type { CueWindow, Resolution, Track, WindowSet } from "@/lib/api";

/** Hands the set to the app-wide player. Renders nothing. */
export function LoadSet({
  setId,
  videoId,
  title,
  cues,
}: {
  setId: string;
  videoId: string | null;
  title: string;
  cues: WindowSet | null;
}) {
  const { load } = usePlayer();
  useEffect(() => {
    if (!videoId) return;
    load({
      setId,
      videoId,
      title,
      windows: cues?.windows ?? [],
      liveCapable: cues?.live_capable ?? false,
    });
  }, [load, setId, videoId, title, cues]);
  return null;
}

/** The canonical name of whatever is playing — what would be scrobbled. */
export function NowPlaying({ estimated }: { estimated: boolean }) {
  const { current, playing } = usePlayer();

  if (estimated) {
    return (
      <div className="flex items-center gap-2 border border-t-0 border-border bg-surface px-3 py-2 font-mono text-xs text-dim">
        <span className="text-warn">no cue times</span>
        <span>· timings estimated, live sync unavailable</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-3 border border-t-0 border-border bg-surface px-3 py-2 font-mono text-xs">
      <span className={playing ? "text-accent" : "text-border"}>▮▮▯</span>
      <span className="min-w-0 flex-1 truncate text-fg">{current ? current.label : "—"}</span>
      {current?.canonical ? (
        <span className="shrink-0 text-ok" title="canonical Last.fm name">
          canonical
        </span>
      ) : current ? (
        <span className="shrink-0 text-dim" title="no Last.fm match; our normalized name">
          normalized
        </span>
      ) : null}
    </div>
  );
}

function Pill({ label, on, color }: { label: string; on: boolean; color: string }) {
  return (
    <span
      className={`inline-block border px-1 font-mono text-[10px] ${
        on ? `${color} border-current` : "border-border text-border"
      }`}
    >
      {label}
    </span>
  );
}

function Pills({ r }: { r: Resolution | null | undefined }) {
  return (
    <span className="flex shrink-0 gap-1">
      <Pill label="S" on={!!r?.spotify} color="text-ok" />
      <Pill label="Y" on={!!r?.youtube} color="text-warn" />
      <Pill label="L" on={!!r?.lastfm} color="text-link" />
    </span>
  );
}

function Row({
  t,
  window,
  isCurrent,
  inWindow,
  onSeek,
}: {
  t: Track;
  window: CueWindow | undefined;
  isCurrent: boolean;
  inWindow: boolean;
  onSeek: (seconds: number) => void;
}) {
  const r = t.resolution;
  const layered = t.played_with != null;
  const components = t.mashup_components ?? [];
  const noMatch = r?.status === "no_match";

  return (
    <>
      <div
        className={`flex items-baseline gap-3 border-b border-border/60 px-2 py-1.5 font-mono text-sm ${
          inWindow ? "bg-surface-2" : "hover:bg-surface-2"
        } ${layered ? "pl-6" : ""} ${noMatch ? "text-dim" : ""}`}
      >
        <span className="w-4 shrink-0 text-accent">{isCurrent ? "▮▮▯" : ""}</span>
        <button
          onClick={() => window && onSeek(window.start_s)}
          disabled={!window}
          title={window ? `seek to ${formatTime(window.start_s)}` : undefined}
          className="w-14 shrink-0 text-right text-xs text-dim enabled:hover:text-accent"
        >
          {t.cue_time ?? ""}
        </button>
        <span className="w-6 shrink-0 text-right text-xs text-dim">
          {t.source_track_number ?? (layered ? "w/" : "")}
        </span>
        {/* min-w-0 is what lets truncate actually bite: a flex child defaults
            to min-width:auto and would otherwise widen the row instead. Mashup
            titles ("A vs. B vs. C (… Mashup)") are long enough to matter. */}
        <span className="min-w-0 flex-1 truncate">
          {t.is_id ? (
            <span className="border border-dashed border-flag/50 px-1 text-flag">ID · unreleased</span>
          ) : (
            <>
              <span className={noMatch ? "text-dim" : "text-fg"}>
                {t.artists?.join(", ") || t.raw_text}
              </span>
              {t.title ? <span className="text-dim"> — {t.title}</span> : null}
              {t.remix ? <span className="text-link"> ({t.remix})</span> : null}
              {components.length ? <span className="ml-1 text-flag">⋈</span> : null}
            </>
          )}
        </span>
        <Pills r={r} />
      </div>

      {/* mashup components resolve individually — each is its own scrobble */}
      {components.map((c, i) => (
        <div
          key={i}
          className="flex items-baseline gap-3 border-b border-border/40 py-1 pl-16 pr-2 font-mono text-xs text-dim"
        >
          <span className="min-w-0 flex-1 truncate">
            ↳ {c.artists?.join(", ")}
            {c.title ? <span> — {c.title}</span> : null}
            {c.remix ? <span className="text-link"> ({c.remix})</span> : null}
          </span>
          <Pills r={c.resolution} />
        </div>
      ))}
    </>
  );
}

export function TrackList({ tracks, cues }: { tracks: Track[]; cues: WindowSet | null }) {
  const { currentIndex, seek } = usePlayer();
  const windows = cues?.windows ?? [];

  // Mashup components share their parent's position and window, so the row map
  // keys off the parent windows only.
  const byPosition = useMemo(() => {
    const map = new Map<number, CueWindow>();
    for (const w of windows) if (w.component_index == null) map.set(w.position, w);
    return map;
  }, [windows]);

  const currentWindow = currentIndex >= 0 ? windows[currentIndex] : undefined;

  return (
    <section className="min-w-0 border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-2 py-1.5 font-mono text-xs text-dim">
        <span>tracklist · {tracks.length}</span>
        {cues?.timing === "estimated" ? <span className="text-warn">timings estimated</span> : null}
      </div>
      <div>
        {tracks.map((t) => {
          const w = byPosition.get(t.position);
          return (
            <Row
              key={t.position}
              t={t}
              window={w}
              isCurrent={!!currentWindow && currentWindow.position === t.position}
              inWindow={
                !!currentWindow && !!w && w.start_s === currentWindow.start_s
              }
              onSeek={seek}
            />
          );
        })}
      </div>
    </section>
  );
}

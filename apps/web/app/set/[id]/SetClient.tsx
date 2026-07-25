"use client";

/**
 * The interactive half of the setlist page: hand the set to the player, and
 * render the tracklist synced to its playhead.
 *
 * The tracklist is the hero content (design §5.2), so the row treatments carry
 * the CLI's status vocabulary — lit pills for what resolved, indents for `w/`,
 * nested components for mashups, and the `▮▮▯` glyph on whatever is playing.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePlayer } from "@/components/player/PlayerProvider";
import { useScrobble } from "@/components/player/ScrobbleProvider";
import { keyOf } from "@/components/player/useScrobbler";
import { formatTime } from "@/components/player/BottomBar";
import { useKeyboardShortcuts } from "@/components/useKeyboardShortcuts";
import { cueSeconds } from "@/lib/format";
import type { CueWindow, Resolution, Track, WindowSet } from "@/lib/api";

/** Copy helper shared by the row actions, tracklist copy and share. */
async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Plain-text tracklist (design §10) — the thing people paste into a chat. */
export function tracklistText(tracks: Track[], title: string): string {
  const lines = tracks.map((t) => {
    const cue = t.cue_time ? `${t.cue_time}  ` : "";
    if (t.is_id) return `${cue}ID - ID`;
    const artists = (t.artists ?? []).join(", ");
    const remix = t.remix ? ` (${t.remix})` : "";
    return `${cue}${artists}${t.title ? ` - ${t.title}` : ""}${remix}`;
  });
  return [title, "", ...lines].join("\n");
}

/** `scrobble ▸` — live toggle plus the whole-set action (design §6.2). */
export function ScrobbleActions({ liveCapable }: { liveCapable: boolean }) {
  const { connected, enabled, setEnabled, scrobbleWholeSet, busy, setLogged } = useScrobble();
  const [note, setNote] = useState<string | null>(null);

  if (!connected) {
    return (
      <a
        href="/api/auth/lastfm/start"
        className="block border border-border px-2 py-1.5 text-left font-mono text-xs text-dim hover:border-accent hover:text-accent"
      >
        scrobble ▸ connect last.fm
      </a>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <button
        onClick={() => setEnabled(!enabled)}
        disabled={!liveCapable}
        title={liveCapable ? undefined : "this set has no cue times — use whole set"}
        className={`border px-2 py-1.5 text-left font-mono text-xs disabled:opacity-40 ${
          enabled ? "border-ok text-ok" : "border-border text-dim hover:text-fg"
        }`}
      >
        scrobble ▸ live {enabled ? "✓" : ""}
      </button>
      <button
        onClick={async () => setNote(await scrobbleWholeSet())}
        disabled={busy}
        className="border border-border px-2 py-1.5 text-left font-mono text-xs text-dim hover:text-fg disabled:opacity-40"
      >
        {busy ? "scrobbling…" : setLogged ? "scrobble ▸ whole set again" : "scrobble ▸ whole set"}
      </button>
      {!liveCapable ? (
        <div className="font-mono text-[10px] text-warn">
          no cue times — whole set uses estimated timings
        </div>
      ) : null}
      {note ? <div className="font-mono text-[10px] text-ok">{note}</div> : null}
    </div>
  );
}

/** Hands the set to the app-wide player, and honours a `?t=45:20` deep link. */
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
  const { load, seek, ready } = usePlayer();
  const seekedRef = useRef(false);

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

  // Deep link (design §10). Waits for the player, and fires once.
  useEffect(() => {
    if (!ready || seekedRef.current) return;
    const t = new URLSearchParams(window.location.search).get("t");
    const at = cueSeconds(t) ?? (t && /^\d+$/.test(t) ? Number(t) : null);
    if (at === null) return;
    seekedRef.current = true;
    seek(at);
  }, [ready, seek]);

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

/** Hover actions (design §5.2): copy the clean name, seek here, open elsewhere. */
function RowActions({
  t,
  window,
  onSeek,
}: {
  t: Track;
  window: CueWindow | undefined;
  onSeek: (seconds: number) => void;
}) {
  const [copied, setCopied] = useState(false);
  const r = t.resolution;
  const name =
    window?.scrobble_artist && window?.scrobble_track
      ? `${window.scrobble_artist} - ${window.scrobble_track}`
      : null;

  const links: [string, string | null | undefined][] = [
    ["S", r?.spotify?.url],
    ["Y", r?.youtube?.url],
    ["L", r?.lastfm?.url],
  ];

  return (
    <span className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover/row:opacity-100">
      {name ? (
        <button
          onClick={async () => {
            if (await copy(name)) {
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            }
          }}
          title={`copy "${name}"`}
          className="px-1 text-[10px] text-dim hover:text-accent"
        >
          {copied ? "✓" : "⧉"}
        </button>
      ) : null}
      {window ? (
        <button
          onClick={() => onSeek(window.start_s)}
          title={`seek to ${formatTime(window.start_s)}`}
          className="px-1 text-[10px] text-dim hover:text-accent"
        >
          ▸
        </button>
      ) : null}
      {links.map(([label, href]) =>
        href ? (
          <a
            key={label}
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            title={`open on ${label === "S" ? "Spotify" : label === "Y" ? "YouTube" : "Last.fm"}`}
            className="px-0.5 text-[10px] text-dim hover:text-link"
          >
            ↗
          </a>
        ) : null,
      )}
    </span>
  );
}

function Row({
  t,
  window,
  isCurrent,
  inWindow,
  selected,
  scrobbled,
  onSeek,
}: {
  t: Track;
  window: CueWindow | undefined;
  isCurrent: boolean;
  inWindow: boolean;
  selected: boolean;
  scrobbled: Set<string>;
  onSeek: (seconds: number) => void;
}) {
  const rowRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (selected) rowRef.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  const r = t.resolution;
  const layered = t.played_with != null;
  const components = t.mashup_components ?? [];
  const noMatch = r?.status === "no_match";

  return (
    <>
      <div
        ref={rowRef}
        className={`group/row flex items-baseline gap-3 border-b border-border/60 px-2 py-1.5 font-mono text-sm ${
          inWindow ? "bg-surface-2" : "hover:bg-surface-2"
        } ${selected ? "outline outline-1 -outline-offset-1 outline-accent/60" : ""} ${
          layered ? "pl-6" : ""
        } ${noMatch ? "text-dim" : ""}`}
      >
        <span
          className={`w-4 shrink-0 ${scrobbled.has(keyOf(t.position, null)) ? "text-ok" : "text-accent"}`}
          title={
            scrobbled.has(keyOf(t.position, null))
              ? "scrobbled"
              : window?.eligible === false
                ? `not scrobbled: ${window.reason}`
                : undefined
          }
        >
          {scrobbled.has(keyOf(t.position, null)) ? "✓" : isCurrent ? "▮▮▯" : ""}
        </span>
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
        <RowActions t={t} window={window} onSeek={onSeek} />
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

/** Copy the tracklist / share a link, optionally at the current position. */
function TracklistTools({ tracks, title }: { tracks: Track[]; title: string }) {
  const { playhead } = usePlayer();
  const [note, setNote] = useState<string | null>(null);

  const flash = (text: string) => {
    setNote(text);
    setTimeout(() => setNote(null), 1500);
  };

  return (
    <span className="flex items-center gap-2">
      {note ? <span className="text-ok">{note}</span> : null}
      <button
        onClick={async () => flash((await copy(tracklistText(tracks, title))) ? "copied" : "failed")}
        className="text-dim hover:text-fg"
        title="copy the tracklist as plain text"
      >
        copy
      </button>
      <button
        onClick={async () => {
          const url = new URL(window.location.href);
          url.searchParams.delete("t");
          flash((await copy(url.toString())) ? "link copied" : "failed");
        }}
        className="text-dim hover:text-fg"
        title="copy a link to this set"
      >
        share
      </button>
      <button
        onClick={async () => {
          const url = new URL(window.location.href);
          url.searchParams.set("t", formatTime(playhead));
          flash((await copy(url.toString())) ? `link @ ${formatTime(playhead)}` : "failed");
        }}
        className="text-dim hover:text-fg"
        title="copy a link that opens at the current position"
      >
        share @ time
      </button>
    </span>
  );
}

export function TrackList({
  tracks,
  cues,
  title,
}: {
  tracks: Track[];
  cues: WindowSet | null;
  title: string;
}) {
  const { currentIndex, seek, toggle } = usePlayer();
  const { done, connected, enabled, setEnabled } = useScrobble();
  const windows = cues?.windows ?? [];
  const [selected, setSelected] = useState<number | null>(null);

  // Mashup components share their parent's position and window, so the row map
  // keys off the parent windows only.
  const byPosition = useMemo(() => {
    const map = new Map<number, CueWindow>();
    for (const w of windows) if (w.component_index == null) map.set(w.position, w);
    return map;
  }, [windows]);

  const currentWindow = currentIndex >= 0 ? windows[currentIndex] : undefined;

  // j/k walk the rows, ↵ seeks to the selected one (design §10).
  const move = useCallback(
    (delta: 1 | -1) =>
      setSelected((i) => {
        const next = i === null ? (currentIndex >= 0 ? currentIndex : 0) : i + delta;
        return Math.min(tracks.length - 1, Math.max(0, next));
      }),
    [currentIndex, tracks.length],
  );

  const shortcuts = useMemo(
    () => ({
      j: () => move(1),
      k: () => move(-1),
      ArrowDown: () => move(1),
      ArrowUp: () => move(-1),
      space: toggle,
      Enter: () => {
        if (selected === null) return;
        const w = byPosition.get(tracks[selected]?.position);
        if (w) seek(w.start_s);
      },
      s: () => connected && setEnabled(!enabled),
    }),
    [move, toggle, selected, byPosition, tracks, seek, connected, enabled, setEnabled],
  );
  useKeyboardShortcuts(shortcuts);

  return (
    <section className="min-w-0 border border-border bg-surface">
      <div className="flex items-center justify-between gap-3 border-b border-border px-2 py-1.5 font-mono text-xs text-dim">
        <span className="shrink-0">
          tracklist · {tracks.length}
          {cues?.timing === "estimated" ? (
            <span className="ml-2 text-warn">timings estimated</span>
          ) : null}
        </span>
        <TracklistTools tracks={tracks} title={title} />
      </div>
      <div>
        {tracks.map((t, i) => {
          const w = byPosition.get(t.position);
          return (
            <Row
              key={t.position}
              t={t}
              window={w}
              selected={selected === i}
              isCurrent={!!currentWindow && currentWindow.position === t.position}
              inWindow={
                !!currentWindow && !!w && w.start_s === currentWindow.start_s
              }
              scrobbled={done}
              onSeek={seek}
            />
          );
        })}
      </div>
    </section>
  );
}

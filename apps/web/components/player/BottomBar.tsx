"use client";

/**
 * The persistent bottom player bar (design §4.2). It is the single source of
 * truth for "what's playing": the mini slot on the left is where the player
 * docks, the centre carries transport and a whole-set scrubber ticked at track
 * boundaries, and the right shows scrobble status.
 *
 * Full-bleed and percentage-based — the bar spans the viewport rather than the
 * content column, so the scrubber gets all the room that's left over.
 */

import Link from "next/link";
import { usePlayer } from "./PlayerProvider";
import { useScrobble } from "./ScrobbleProvider";
import { usePlayerSlots } from "./slots";

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

function Transport() {
  const { playing, toggle, step } = usePlayer();
  return (
    <div className="flex items-center gap-3">
      <button
        onClick={() => step(-1)}
        title="previous track"
        className="cursor-pointer px-1 text-xl leading-none text-dim hover:text-fg"
      >
        ⏮
      </button>
      <button
        onClick={toggle}
        title={playing ? "pause" : "play"}
        className="flex h-11 w-11 cursor-pointer items-center justify-center border border-accent text-lg text-accent hover:bg-accent hover:text-bg"
      >
        {playing ? "⏸" : "▶"}
      </button>
      <button
        onClick={() => step(1)}
        title="next track"
        className="cursor-pointer px-1 text-xl leading-none text-dim hover:text-fg"
      >
        ⏭
      </button>
    </div>
  );
}

/** Whole-set scrubber; ticks mark where each track begins, lit once passed. */
function Scrubber() {
  const { playhead, duration, seek, loaded } = usePlayer();
  const windows = loaded?.windows ?? [];
  // The axis must cover both clocks. Cue times come from 1001TL and the
  // duration from YouTube, and they disagree whenever the tracklist was timed
  // against a longer upload — scaling to the shorter one pushed the late ticks
  // past 100% and out of the bar.
  const span = Math.max(duration, windows.at(-1)?.end_s ?? 0, 0);
  const pct = span ? Math.min(100, (playhead / span) * 100) : 0;

  return (
    <div className="flex flex-1 items-center gap-3">
      <span className="w-14 shrink-0 text-right font-mono text-xs text-dim">
        {formatTime(playhead)}
      </span>

      <div
        role="slider"
        aria-label="seek"
        aria-valuemin={0}
        aria-valuemax={span}
        aria-valuenow={Math.floor(playhead)}
        tabIndex={0}
        onClick={(e) => {
          if (!span) return;
          const box = e.currentTarget.getBoundingClientRect();
          seek(((e.clientX - box.left) / box.width) * span);
        }}
        className="group relative h-6 min-w-0 flex-1 cursor-pointer overflow-hidden"
      >
        {/* the track itself */}
        <div className="absolute top-1/2 h-1.5 w-full -translate-y-1/2 bg-surface-2">
          <div className="h-full bg-accent" style={{ width: `${pct}%` }} />
        </div>

        {/* Track boundaries. Only real rows: a mashup's components share their
            parent's position and window, so including them would stack
            duplicate ticks on the same mark. */}
        {span
          ? windows
              .filter((w) => w.component_index == null)
              .map((w) => {
                const passed = playhead >= w.start_s;
                // Clamped so a stray cue time can never place a tick outside
                // the track; the last pixel is pulled back in so an end-of-set
                // tick isn't clipped away by overflow-hidden.
                const at = Math.min(100, Math.max(0, (w.start_s / span) * 100));
                return (
                  <span
                    key={w.position}
                    title={w.label}
                    className={`absolute top-1/2 h-4 w-px -translate-y-1/2 ${
                      passed ? "bg-accent" : "bg-white/70"
                    }`}
                    style={{ left: `calc(${at}% - ${at === 100 ? 1 : 0}px)` }}
                  />
                );
              })
          : null}

        {/* playhead handle */}
      </div>

      <span className="w-14 shrink-0 font-mono text-xs text-dim">{formatTime(span)}</span>
    </div>
  );
}

export function BottomBar() {
  const { loaded, current } = usePlayer();
  const { setDock } = usePlayerSlots();

  // The bar exists only once a set is loaded; the dock ref must still be
  // registered on first paint so the surface has somewhere to land.
  if (!loaded) return null;

  const estimated = !loaded.liveCapable;

  return (
    <div className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-surface">
      <div className="flex h-20 w-full items-center gap-5 px-5">
        {/* left — the dock the player lands in, plus what's playing.
            The slot is taller than the bar so the player reads as hovering
            above it rather than being boxed inside. */}
        <div className="flex w-[28%] min-w-0 shrink-0 items-center gap-3">
          <div ref={setDock} className="-mt-6 h-[76px] w-[135px] shrink-0 bg-black" />
          <div className="min-w-0 flex-1">
            <div className="truncate font-sans text-sm font-medium text-fg">
              {current ? (current.scrobble_track ?? current.label) : estimated ? "no cue times" : "—"}
            </div>
            <div className="truncate font-mono text-xs text-dim">
              {current?.scrobble_artist ?? ""}
            </div>
            <Link
              href={`/set/${loaded.setId}`}
              className="block truncate font-mono text-xs text-dim hover:text-link"
            >
              {loaded.title}
            </Link>
          </div>
        </div>

        <Transport />
        <Scrubber />

        <div className="flex w-[10%] shrink-0 justify-end">
          <ScrobbleStatus estimated={estimated} />
        </div>
      </div>
    </div>
  );
}

/** Right side of the bar: the live scrobble toggle and its state (design §4.2). */
function ScrobbleStatus({ estimated }: { estimated: boolean }) {
  const { connected, enabled, setEnabled, done, lastError } = useScrobble();

  if (!connected) {
    return (
      <div className="text-right font-mono text-xs text-dim">
        <a href="/api/auth/lastfm/start" className="text-lastfm hover:underline">
          connect last.fm
        </a>
        <div className="text-[11px] text-border">to scrobble</div>
      </div>
    );
  }

  if (estimated) {
    return (
      <div
        className="text-right font-mono text-xs text-warn"
        title="this set has no cue times, so live scrobbling is unavailable — use scrobble ▸ whole set"
      >
        timings estimated
        <div className="text-[11px] text-border">live sync off</div>
      </div>
    );
  }

  return (
    <div className="text-right font-mono text-xs">
      <button
        onClick={() => setEnabled(!enabled)}
        className={`cursor-pointer ${enabled ? "text-ok" : "text-dim hover:text-fg"}`}
        title={enabled ? "scrobbling on — click to pause" : "scrobbling paused — click to resume"}
      >
        scrobbling {enabled ? "✓" : "off"}
      </button>
      <div className="text-[11px] text-border">
        {lastError ? <span className="text-warn">{lastError}</span> : `${done.size} logged`}
      </div>
    </div>
  );
}

"use client";

/**
 * The persistent bottom player bar (design §4.2). It is the single source of
 * truth for "what's playing": the mini slot on the left is where the player
 * docks, the centre carries transport and a whole-set scrubber ticked at track
 * boundaries, and the right shows scrobble status.
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
    <div className="flex items-center gap-1">
      <button
        onClick={() => step(-1)}
        title="previous track"
        className="px-2 py-1 font-mono text-xs text-dim hover:text-fg"
      >
        ⏮
      </button>
      <button
        onClick={toggle}
        title={playing ? "pause" : "play"}
        className="border border-border px-3 py-1 font-mono text-xs text-fg hover:border-accent hover:text-accent"
      >
        {playing ? "⏸" : "▶"}
      </button>
      <button
        onClick={() => step(1)}
        title="next track"
        className="px-2 py-1 font-mono text-xs text-dim hover:text-fg"
      >
        ⏭
      </button>
    </div>
  );
}

/** Whole-set scrubber; ticks mark where each track begins. */
function Scrubber() {
  const { playhead, duration, seek, loaded } = usePlayer();
  const windows = loaded?.windows ?? [];
  const span = duration || windows.at(-1)?.end_s || 0;

  return (
    <div className="flex flex-1 items-center gap-2">
      <span className="w-12 shrink-0 text-right font-mono text-[11px] text-dim">
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
        className="relative h-4 flex-1 cursor-pointer"
      >
        <div className="absolute top-1/2 h-[3px] w-full -translate-y-1/2 bg-surface-2">
          <div
            className="h-full bg-accent"
            style={{ width: span ? `${Math.min(100, (playhead / span) * 100)}%` : "0%" }}
          />
        </div>
        {/* Track boundaries — the set's shape at a glance. Only real rows: a
            mashup's components share their parent's position and window, so
            including them would stack duplicate ticks on the same mark. */}
        {span
          ? windows
              .filter((w) => w.component_index == null)
              .map((w) => (
                <span
                  key={w.position}
                  className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-border"
                  style={{ left: `${(w.start_s / span) * 100}%` }}
                />
              ))
          : null}
      </div>
      <span className="w-12 shrink-0 font-mono text-[11px] text-dim">{formatTime(span)}</span>
    </div>
  );
}

export function BottomBar() {
  const { loaded, current, playhead } = usePlayer();
  const { setDock } = usePlayerSlots();

  // The bar exists only once a set is loaded; the dock ref must still be
  // registered on first paint so the surface has somewhere to land.
  if (!loaded) return null;

  const estimated = !loaded.liveCapable;

  return (
    <div className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-surface">
      <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-2">
        {/* left — the dock the player falls into, plus what's playing */}
        <div className="flex min-w-0 shrink-0 items-center gap-3" style={{ width: 300 }}>
          <div ref={setDock} className="h-[72px] w-32 shrink-0 bg-black" />
          <div className="min-w-0">
            <div className="truncate font-mono text-xs text-fg">
              {current ? current.label : estimated ? "no cue times" : "—"}
            </div>
            <Link
              href={`/set/${loaded.setId}`}
              className="block truncate font-mono text-[11px] text-dim hover:text-link"
            >
              {loaded.title}
            </Link>
          </div>
        </div>

        <Transport />
        <Scrubber />

        <ScrobbleStatus estimated={estimated} />
      </div>
    </div>
  );
}

/** Right side of the bar: the live scrobble toggle and its state (design §4.2). */
function ScrobbleStatus({ estimated }: { estimated: boolean }) {
  const { connected, enabled, setEnabled, done, lastError } = useScrobble();

  if (!connected) {
    return (
      <div className="shrink-0 text-right font-mono text-[11px] text-dim">
        <a href="/api/auth/lastfm/start" className="hover:text-link">
          connect last.fm
        </a>
        <div className="text-[10px] text-border">to scrobble</div>
      </div>
    );
  }

  if (estimated) {
    return (
      <div
        className="shrink-0 text-right font-mono text-[11px] text-warn"
        title="this set has no cue times, so live scrobbling is unavailable — use scrobble ▸ whole set"
      >
        timings estimated
        <div className="text-[10px] text-border">live sync off</div>
      </div>
    );
  }

  return (
    <div className="shrink-0 text-right font-mono text-[11px]">
      <button
        onClick={() => setEnabled(!enabled)}
        className={enabled ? "text-ok" : "text-dim hover:text-fg"}
        title={enabled ? "live scrobbling on — click to stop" : "start live scrobbling"}
      >
        scrobbling {enabled ? "✓" : "off"}
      </button>
      <div className="text-[10px] text-border">
        {lastError ? (
          <span className="text-warn">{lastError}</span>
        ) : (
          `${done.size} logged`
        )}
      </div>
    </div>
  );
}

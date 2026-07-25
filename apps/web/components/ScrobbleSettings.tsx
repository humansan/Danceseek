"use client";

/**
 * The settings menu (design §4.3): a compact panel, not a page. Holds the
 * connection and the scrobble configuration the scrobbler obeys.
 *
 * These are enforced server-side too — this panel only chooses them.
 */

import { useEffect, useRef, useState } from "react";
import { useScrobble, type ScrobbleConfig } from "@/components/player/ScrobbleProvider";

type Choice<K extends keyof ScrobbleConfig> = { value: ScrobbleConfig[K]; label: string };

function Row<K extends keyof ScrobbleConfig>({
  label,
  hint,
  field,
  choices,
}: {
  label: string;
  hint: string;
  field: K;
  choices: Choice<K>[];
}) {
  const { config, saveConfig } = useScrobble();
  if (!config) return null;

  return (
    <div className="border-b border-border/60 px-3 py-2 last:border-b-0">
      <div className="font-mono text-xs text-fg">{label}</div>
      <div className="mb-1.5 font-mono text-[10px] text-dim">{hint}</div>
      <div className="flex gap-1">
        {choices.map((choice) => {
          const on = config[field] === choice.value;
          return (
            <button
              key={String(choice.value)}
              onClick={() => saveConfig({ ...config, [field]: choice.value })}
              className={`border px-2 py-0.5 font-mono text-[11px] ${
                on ? "border-accent text-accent" : "border-border text-dim hover:text-fg"
              }`}
            >
              {choice.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ScrobbleSettings() {
  const { connected, username, config } = useScrobble();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  if (!connected) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="border border-border px-2 py-1 font-mono text-xs text-dim hover:text-fg"
        title="scrobble settings"
      >
        ⚙
      </button>

      {open ? (
        <div className="absolute right-0 top-full z-50 mt-1 w-80 border border-border bg-surface">
          <div className="border-b border-border px-3 py-2 font-mono text-[11px] text-dim">
            scrobbling as <span className="text-ok">{username}</span>
          </div>
          {config ? (
            <>
              <Row
                label="layered w/ tracks"
                hint="rows played over the track above"
                field="layered"
                choices={[
                  { value: "skip", label: "skip" },
                  { value: "scrobble", label: "scrobble" },
                ]}
              />
              <Row
                label="mashups"
                hint="the parent row is never scrobbled; this is its components"
                field="mashups"
                choices={[
                  { value: "primary", label: "primary only" },
                  { value: "all", label: "all" },
                  { value: "skip", label: "skip" },
                ]}
              />
              <Row
                label="unreleased IDs"
                hint="tracks 1001tracklists lists as ID"
                field="unreleased"
                choices={[
                  { value: "skip", label: "skip" },
                  { value: "scrobble", label: "scrobble" },
                ]}
              />
              <Row
                label="unmatched tracks"
                hint="no Last.fm entry — uses our normalized Artist – Title"
                field="unmatched"
                choices={[
                  { value: "scrobble", label: "scrobble" },
                  { value: "skip", label: "skip" },
                ]}
              />
            </>
          ) : (
            <div className="px-3 py-2 font-mono text-[11px] text-dim">loading…</div>
          )}
          <div className="border-t border-border px-3 py-2 font-mono text-[10px] text-dim">
            threshold: half the track or 4 min, Last.fm's own rule
          </div>
        </div>
      ) : null}
    </div>
  );
}

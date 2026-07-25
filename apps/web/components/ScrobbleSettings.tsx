"use client";

/**
 * The settings menu (design §4.3): a compact panel, not a page. Holds the
 * connection, the master scrobble switch, and the configuration the scrobbler
 * obeys.
 *
 * On Base UI's Popover — unlike the command bar, a menu *should* take focus,
 * trap it while open, and restore it on close.
 *
 * The per-type choices are enforced server-side too; this panel only picks them.
 */

import { Popover } from "@base-ui/react/popover";
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
    <div className="border-b border-border/60 px-4 py-3 last:border-b-0">
      <div className="font-mono text-sm text-fg">{label}</div>
      <div className="mb-2 font-mono text-xs text-dim">{hint}</div>
      <div className="flex flex-wrap gap-1.5">
        {choices.map((choice) => {
          const on = config[field] === choice.value;
          return (
            <button
              key={String(choice.value)}
              onClick={() => saveConfig({ ...config, [field]: choice.value })}
              className={`cursor-pointer border px-2.5 py-1 font-mono text-xs ${
                on
                  ? "border-accent bg-accent/15 text-accent"
                  : "border-border text-dim hover:border-dim hover:text-fg"
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
  const { connected, username, config, enabled, setEnabled } = useScrobble();

  if (!connected) return null;

  return (
    <Popover.Root>
      <Popover.Trigger
        className="flex h-8 cursor-pointer items-center border border-border px-3 font-mono text-sm text-dim hover:border-accent hover:text-accent"
        title="scrobble settings"
      >
        ⚙
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Positioner sideOffset={6} align="end" className="z-50">
          <Popover.Popup className="w-96 border border-border bg-surface">
            <div className="flex items-center justify-between border-b border-border px-4 py-3 font-mono text-xs text-dim">
              <span>
                scrobbling as <span className="text-lastfm">{username}</span>
              </span>
            </div>

            {/* Master switch: freezes everything without losing the settings. */}
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <div className="font-mono text-sm text-fg">scrobbling</div>
                <div className="font-mono text-xs text-dim">
                  {enabled ? "on — plays are sent as you listen" : "paused — nothing is sent"}
                </div>
              </div>
              <button
                onClick={() => setEnabled(!enabled)}
                role="switch"
                aria-checked={enabled}
                className={`h-7 w-14 cursor-pointer border font-mono text-xs ${
                  enabled
                    ? "border-ok bg-ok/15 text-ok"
                    : "border-border text-dim hover:border-dim hover:text-fg"
                }`}
              >
                {enabled ? "on" : "off"}
              </button>
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
              <div className="px-4 py-3 font-mono text-xs text-dim">loading…</div>
            )}

            <div className="border-t border-border px-4 py-2.5 font-mono text-xs text-dim">
              threshold: half the track or 4 min — Last.fm&rsquo;s own rule
            </div>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

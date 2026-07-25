"use client";

/**
 * Multi-select filter chips (design §5.1), backed by /facets so the options are
 * the whole catalog rather than whatever happens to be on this page.
 *
 * State lives in the URL — every chip is shareable, the back button works, and
 * the grid stays server-rendered.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";
import type { Facet, Facets } from "@/lib/api";

const GROUPS: { key: keyof Facets; param: string; label: string }[] = [
  { key: "djs", param: "dj", label: "dj" },
  { key: "genres", param: "genre", label: "genre" },
  { key: "events", param: "event", label: "event" },
  { key: "years", param: "year", label: "year" },
];

const VISIBLE = 6; // chips per group before "more"

function Chip({
  facet,
  active,
  onToggle,
}: {
  facet: Facet;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      aria-pressed={active}
      className={`border px-2 py-0.5 font-mono text-[11px] transition-colors ${
        active
          ? "border-accent bg-accent/10 text-accent"
          : "border-border text-dim hover:border-dim hover:text-fg"
      }`}
    >
      {facet.value}
      <span className={active ? "ml-1 text-accent/60" : "ml-1 text-border"}>{facet.count}</span>
    </button>
  );
}

export function FilterChips({ facets }: { facets: Facets }) {
  const router = useRouter();
  const params = useSearchParams();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggle = useCallback(
    (param: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      const current = next.getAll(param);
      next.delete(param);
      for (const v of current) if (v !== value) next.append(param, v);
      if (!current.includes(value)) next.append(param, value);
      router.push(next.size ? `/?${next}` : "/", { scroll: false });
    },
    [params, router],
  );

  const activeCount = GROUPS.reduce((n, g) => n + params.getAll(g.param).length, 0);

  return (
    <div className="mb-4 border border-border bg-surface">
      {GROUPS.map(({ key, param, label }) => {
        const all = facets[key] ?? [];
        if (!all.length) return null;
        const active = params.getAll(param);
        const open = expanded[param];
        // Selected chips always show, even past the fold.
        const shown = open ? all : all.filter((f, i) => i < VISIBLE || active.includes(f.value));

        return (
          <div key={param} className="flex gap-3 border-b border-border/60 px-3 py-2 last:border-b-0">
            <span className="w-12 shrink-0 pt-0.5 font-mono text-[10px] uppercase tracking-widest text-dim">
              {label}
            </span>
            <div className="flex flex-wrap gap-1">
              {shown.map((f) => (
                <Chip
                  key={f.value}
                  facet={f}
                  active={active.includes(f.value)}
                  onToggle={() => toggle(param, f.value)}
                />
              ))}
              {all.length > shown.length ? (
                <button
                  onClick={() => setExpanded((e) => ({ ...e, [param]: true }))}
                  className="px-2 py-0.5 font-mono text-[11px] text-dim hover:text-fg"
                >
                  +{all.length - shown.length} more
                </button>
              ) : null}
            </div>
          </div>
        );
      })}

      {activeCount ? (
        <div className="border-t border-border px-3 py-1.5">
          <button
            onClick={() => router.push("/", { scroll: false })}
            className="font-mono text-[11px] text-warn hover:text-fg"
          >
            ✕ clear {activeCount} filter{activeCount === 1 ? "" : "s"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

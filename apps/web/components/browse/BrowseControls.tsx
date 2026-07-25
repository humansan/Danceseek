"use client";

/** Grid ↔ dense toggle and "load more", both carried in the URL so a browse
 *  state survives a reload and a shared link. */

import { useRouter, useSearchParams } from "next/navigation";

function setParam(params: URLSearchParams, key: string, value: string | null) {
  const next = new URLSearchParams(params.toString());
  if (value === null) next.delete(key);
  else next.set(key, value);
  return next;
}

export function ViewToggle() {
  const router = useRouter();
  const params = useSearchParams();
  const view = params.get("view") === "list" ? "list" : "grid";

  return (
    <div className="flex border border-border">
      {(["grid", "list"] as const).map((mode) => (
        <button
          key={mode}
          onClick={() =>
            router.push(`/?${setParam(params, "view", mode === "grid" ? null : mode)}`, {
              scroll: false,
            })
          }
          className={`px-2 py-1 font-mono text-[11px] ${
            view === mode ? "bg-surface-2 text-accent" : "text-dim hover:text-fg"
          }`}
        >
          {mode === "grid" ? "▦ grid" : "≣ list"}
        </button>
      ))}
    </div>
  );
}

export function LoadMore({ nextLimit }: { nextLimit: number }) {
  const router = useRouter();
  const params = useSearchParams();
  return (
    <button
      onClick={() =>
        router.push(`/?${setParam(params, "limit", String(nextLimit))}`, { scroll: false })
      }
      className="mx-auto mt-4 block border border-border px-4 py-2 font-mono text-xs text-dim hover:border-accent hover:text-accent"
    >
      load more ↓
    </button>
  );
}

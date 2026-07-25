"use client";

/**
 * The command bar (design §4.1): a single `> ` prompt that searches sets, DJs
 * and events. Adding by URL is maintainer-only until managed scraping lands, so
 * a pasted 1001tracklists link is answered honestly rather than silently
 * ignored.
 *
 * The results list is deliberately hand-rolled rather than a Popover: a popover
 * moves focus into itself when it opens, which fought the input — one click
 * focused it, the popover stole focus straight back, and you had to click a
 * second time to type. Focus must never leave the input here, so the list is a
 * plain absolutely-positioned listbox with `aria-activedescendant` pointing at
 * the highlighted row.
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { SetlistSummary } from "@/lib/api";

const TRACKLIST_URL = /1001tracklists\.com\/tracklist\//i;

export function CommandBar() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [value, setValue] = useState("");
  const [results, setResults] = useState<SetlistSummary[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();

  const isUrl = TRACKLIST_URL.test(value);

  // `/` or ⌘K focuses from anywhere — but never while typing somewhere else.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      const typing =
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        (el as HTMLElement | null)?.isContentEditable;
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Close when the click lands outside the whole control.
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  // Debounced search.
  useEffect(() => {
    const term = value.trim();
    if (!term || isUrl) {
      setResults([]);
      setOpen(isUrl);
      return;
    }
    const timer = window.setTimeout(async () => {
      try {
        const r = await fetch(`/api/setlists?q=${encodeURIComponent(term)}&limit=8`, {
          cache: "no-store",
        });
        const found: SetlistSummary[] = r.ok ? await r.json() : [];
        setResults(found);
        setActive(0);
        setOpen(true);
      } catch {
        setResults([]);
      }
    }, 200);
    return () => window.clearTimeout(timer);
  }, [value, isUrl]);

  const go = useCallback(
    (id: string) => {
      setOpen(false);
      setValue("");
      router.push(`/set/${id}`);
    },
    [router],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => {
        const n = results.length;
        if (!n) return 0;
        return e.key === "ArrowDown" ? (i + 1) % n : (i - 1 + n) % n;
      });
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (results[active]) go(results[active].id);
      // A search with no hit falls back to the filtered browse page.
      else if (value.trim() && !isUrl) router.push(`/?q=${encodeURIComponent(value.trim())}`);
    }
  };

  return (
    <div ref={boxRef} className="relative w-[min(38rem,60vw)]">
      <div className="flex items-center gap-2 border border-border bg-bg px-3 py-2 focus-within:border-accent">
        <span className="font-mono text-sm text-accent">&gt;</span>
        <input
          ref={inputRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => (results.length || isUrl) && setOpen(true)}
          placeholder="search sets, DJs, events"
          aria-label="search"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && results[active] ? `${listId}-${active}` : undefined}
          className="w-full bg-transparent font-mono text-sm text-fg placeholder:text-dim focus:outline-none"
        />
        {!value ? <span className="inline-block h-4 w-1.5 animate-pulse bg-accent" /> : null}
      </div>

      {open ? (
        <div
          id={listId}
          role="listbox"
          className="absolute left-0 right-0 top-full z-50 mt-1 max-h-80 overflow-y-auto border border-border bg-surface"
        >
          {isUrl ? (
            <div className="px-3 py-2 font-mono text-xs">
              <div className="text-warn">✗ adding is maintainer-only</div>
              <div className="mt-1 text-dim">
                run <span className="text-fg">uv run soundseek console</span> to ingest this URL
              </div>
            </div>
          ) : results.length === 0 ? (
            <div className="px-3 py-2 font-mono text-xs text-dim">no matches</div>
          ) : (
            results.map((s, i) => (
              <button
                key={s.id}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                // mousedown would blur the input before the click registers
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => go(s.id)}
                className={`block w-full cursor-pointer border-b border-border/60 px-3 py-2 text-left last:border-b-0 ${
                  i === active ? "bg-surface-2" : ""
                }`}
              >
                <div className="truncate font-sans text-sm text-fg">
                  {s.title ?? "(untitled set)"}
                </div>
                <div className="truncate font-mono text-[11px] text-dim">
                  {(s.dj_names ?? []).join(", ")}
                  {s.date_recorded ? <span className="sep" /> : null}
                  {s.date_recorded ?? ""}
                  <span className="sep" />
                  {s.track_count} trk
                </div>
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

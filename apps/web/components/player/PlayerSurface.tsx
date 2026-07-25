"use client";

/**
 * The player's body: one fixed-position shell that follows whichever slot
 * should hold it.
 *
 * While the theatre slot is on screen the shell sits exactly on top of it, so
 * it reads as part of the page and scrolls with it. As the slot leaves the
 * viewport the shell interpolates down into the bottom bar's mini slot — the
 * "falls into the bar" transition — and stays there while you browse.
 *
 * Nothing here ever re-parents the iframe: only its position and size change,
 * which is what keeps playback alive across all of it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { usePlayer } from "./PlayerProvider";
import { usePlayerSlots } from "./slots";

type Rect = { left: number; top: number; width: number; height: number };

// The morph runs over the last stretch of the theatre slot's exit: fully
// theatre while its bottom edge is below MORPH_START, fully docked above
// MORPH_END (both measured from the top of the viewport).
const MORPH_START = 300;
const MORPH_END = 110;

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const clamp01 = (n: number) => Math.min(1, Math.max(0, n));

function rectOf(el: HTMLElement): Rect {
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top, width: r.width, height: r.height };
}

/** Where the mini player sits before the bottom bar has been measured. */
function fallbackDock(): Rect {
  const width = 128;
  return {
    left: 16,
    top: (typeof window === "undefined" ? 800 : window.innerHeight) - 88,
    width,
    height: Math.round((width * 9) / 16),
  };
}

export function PlayerSurface() {
  const { loaded, hostRef } = usePlayer();
  const { theatre, dock } = usePlayerSlots();
  const shellRef = useRef<HTMLDivElement | null>(null);
  const [docked, setDocked] = useState(true);

  useEffect(() => {
    if (!loaded) return;

    let frame = 0;

    const apply = () => {
      frame = 0;
      const shell = shellRef.current;
      if (!shell) return;

      const target = dock ? rectOf(dock) : fallbackDock();
      let rect = target;
      let progress = 1;

      if (theatre) {
        const big = rectOf(theatre);
        progress = clamp01((MORPH_START - (big.top + big.height)) / (MORPH_START - MORPH_END));
        rect = {
          left: lerp(big.left, target.left, progress),
          top: lerp(big.top, target.top, progress),
          width: lerp(big.width, target.width, progress),
          height: lerp(big.height, target.height, progress),
        };
      }

      shell.style.transform = `translate(${rect.left}px, ${rect.top}px)`;
      shell.style.width = `${rect.width}px`;
      shell.style.height = `${rect.height}px`;
      setDocked(progress > 0.5);
    };

    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(apply);
    };

    apply();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    const observer = new ResizeObserver(schedule);
    if (theatre) observer.observe(theatre);
    if (dock) observer.observe(dock);

    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      observer.disconnect();
    };
  }, [loaded, theatre, dock]);

  return (
    <div
      ref={shellRef}
      aria-hidden={!loaded}
      className={`fixed left-0 top-0 z-30 origin-top-left overflow-hidden border bg-black ${
        loaded ? "" : "pointer-events-none opacity-0"
      } ${docked ? "border-border" : "border-border"}`}
      style={{ willChange: "transform,width,height" }}
    >
      {/* The YouTube API replaces this node with the iframe; it is created once
          and never moved, so playback survives navigation and the morph. */}
      <div ref={hostRef} className="h-full w-full" />
    </div>
  );
}

/** Reserves the theatre space on a setlist page and registers it as the anchor. */
export function TheatreSlot({ hasRecording }: { hasRecording: boolean }) {
  const { setTheatre } = usePlayerSlots();
  const ownRef = useRef<HTMLDivElement | null>(null);

  const attach = useCallback(
    (el: HTMLDivElement | null) => {
      ownRef.current = el;
      if (el) setTheatre(el);
    },
    [setTheatre],
  );

  // Navigating set → set can unmount this page *after* the next one has
  // registered its slot; only clear the anchor if it is still ours.
  useEffect(
    () => () => setTheatre((current) => (current === ownRef.current ? null : current)),
    [setTheatre],
  );

  if (!hasRecording) {
    return (
      <div className="flex aspect-video w-full items-center justify-center border border-border bg-surface font-mono text-xs text-dim">
        no set recording linked
      </div>
    );
  }
  return <div ref={attach} className="aspect-video w-full bg-black" />;
}

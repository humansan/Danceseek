"use client";

/**
 * The player's body: one fixed-position shell that follows whichever slot
 * should hold it.
 *
 * While the theatre slot is on screen the shell sits exactly on top of it, so
 * it reads as part of the page and scrolls with it. Once the slot scrolls past,
 * the shell snaps to the bottom bar's mini slot and stays there while you
 * browse.
 *
 * Nothing here ever re-parents the iframe: only its position and size change,
 * which is what keeps playback alive across all of it.
 */

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import { usePlayer } from "./PlayerProvider";
import { usePlayerSlots } from "./slots";

type Rect = { left: number; top: number; width: number; height: number };

// The player is either in the theatre slot or docked — no in-between. The
// interpolated version looked smooth with trackpad scrolling but stuttered on
// stepped mouse wheels, and it floated over the tracklist on the way down.
// It now snaps when the slot's bottom edge crosses this line.
const DOCK_AT = 140;

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
      let isDocked = true;

      if (theatre) {
        const big = rectOf(theatre);
        isDocked = big.top + big.height < DOCK_AT;
        if (!isDocked) rect = big;
      }

      shell.style.transform = `translate(${rect.left}px, ${rect.top}px)`;
      shell.style.width = `${rect.width}px`;
      shell.style.height = `${rect.height}px`;
      setDocked(isDocked);
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
      // Docked, the player rises above the bar rather than sitting inside it —
      // a brighter border is the only elevation available (shadows are off
      // globally to keep the flat terminal look).
      className={`fixed left-0 top-0 z-40 origin-top-left overflow-hidden bg-black ${
        loaded ? "" : "pointer-events-none opacity-0"
      } ${docked ? "" : "border border-border border-t-0"}`}
      style={{ willChange: "transform,width,height" }}
    >
      {/* The YouTube API replaces this node with the iframe; it is created once
          and never moved, so playback survives navigation and the morph. */}
      <div ref={hostRef} className="h-full w-full" />
    </div>
  );
}

/**
 * Reserves the theatre space on a setlist page.
 *
 * If another set is currently playing, this page does *not* claim the player —
 * it shows a poster with a play button instead, so browsing never interrupts
 * what you're listening to. Pressing play hands the player over.
 */
export function TheatreSlot({
  setId,
  videoId,
}: {
  setId: string;
  videoId: string | null;
}) {
  const { loaded, pending, adopt } = usePlayer();
  const { setTheatre } = usePlayerSlots();
  const ownRef = useRef<HTMLDivElement | null>(null);

  // True when the player is busy with a different set than the one on screen.
  const waiting = Boolean(pending && pending.setId === setId && loaded?.setId !== setId);

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

  // While waiting, release the anchor so the real player stays docked with the
  // set that's actually playing rather than being dragged over this page.
  useEffect(() => {
    if (waiting) setTheatre((current) => (current === ownRef.current ? null : current));
  }, [waiting, setTheatre]);

  if (!videoId) {
    return (
      <div className="flex aspect-video w-full items-center justify-center border border-border bg-surface font-mono text-sm text-dim">
        no set recording linked
      </div>
    );
  }

  if (waiting) {
    return (
      <button
        onClick={adopt}
        className="group relative flex aspect-video w-full cursor-pointer items-center justify-center overflow-hidden border border-border bg-black"
        title="play this set (stops the one currently playing)"
      >
        <Image
          src={`https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`}
          alt=""
          fill
          className="object-cover opacity-45 transition-opacity group-hover:opacity-60"
          unoptimized
        />
        <span className="relative flex items-center gap-3 border border-accent bg-bg/80 px-5 py-3 font-mono text-sm text-accent">
          ▶ play this set
        </span>
        <span className="absolute bottom-3 font-mono text-[11px] text-dim">
          another set is playing — this won&rsquo;t interrupt it until you press play
        </span>
      </button>
    );
  }

  return <div ref={attach} className="aspect-video w-full bg-black" />;
}

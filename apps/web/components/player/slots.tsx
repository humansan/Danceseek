"use client";

/**
 * The two places the player can sit: the big theatre slot on a setlist page,
 * and the mini slot in the bottom bar. Both register their element here so the
 * surface can measure them; the player itself is never moved between them.
 */

import { createContext, useContext, useMemo, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

type Slots = {
  theatre: HTMLElement | null;
  dock: HTMLElement | null;
  // Dispatchers (not plain setters) so a slot can clear itself conditionally —
  // see TheatreSlot, where set→set navigation can unmount the old page after
  // the new one has already registered.
  setTheatre: Dispatch<SetStateAction<HTMLElement | null>>;
  setDock: Dispatch<SetStateAction<HTMLElement | null>>;
};

const SlotsContext = createContext<Slots | null>(null);

export function usePlayerSlots(): Slots {
  const ctx = useContext(SlotsContext);
  if (!ctx) throw new Error("usePlayerSlots must be used inside <PlayerSlotsProvider>");
  return ctx;
}

export function PlayerSlotsProvider({ children }: { children: React.ReactNode }) {
  const [theatre, setTheatre] = useState<HTMLElement | null>(null);
  const [dock, setDock] = useState<HTMLElement | null>(null);
  const value = useMemo(() => ({ theatre, dock, setTheatre, setDock }), [theatre, dock]);
  return <SlotsContext.Provider value={value}>{children}</SlotsContext.Provider>;
}

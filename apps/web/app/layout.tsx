import type { Metadata } from "next";
import Link from "next/link";
import { PlayerProvider } from "@/components/player/PlayerProvider";
import { PlayerSurface } from "@/components/player/PlayerSurface";
import { PlayerSlotsProvider } from "@/components/player/slots";
import { BottomBar } from "@/components/player/BottomBar";
import { LastfmControl } from "@/components/LastfmControl";
import "./globals.css";

export const metadata: Metadata = {
  title: "Danceseek",
  description: "A terminal for DJ sets — browse, scrobble clean, export.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-fg">
        {/* The player lives above the routes so a set keeps playing while you
            browse; the surface positions it over whichever slot is on screen. */}
        <PlayerProvider>
          <PlayerSlotsProvider>
            <header className="sticky top-0 z-50 flex items-center gap-4 border-b border-border bg-surface px-4 py-3">
              <Link href="/" className="font-mono text-sm font-bold tracking-tight text-fg">
                <span className="text-accent">$</span> danceseek
              </Link>
              <div className="flex-1">
                <div className="max-w-xl border border-border bg-bg px-3 py-1.5 font-mono text-sm text-dim">
                  <span className="text-accent">&gt;</span> search sets, DJs, tracks
                  <span className="ml-1 inline-block h-3.5 w-1.5 animate-pulse bg-accent align-middle" />
                </div>
              </div>
              <LastfmControl />
            </header>

            {/* pb-24 keeps the bottom bar from covering the end of a page */}
            <main className="mx-auto max-w-6xl px-4 py-6 pb-24">{children}</main>

            <PlayerSurface />
            <BottomBar />
          </PlayerSlotsProvider>
        </PlayerProvider>
      </body>
    </html>
  );
}

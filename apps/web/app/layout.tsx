import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import { PlayerProvider } from "@/components/player/PlayerProvider";
import { PlayerSurface } from "@/components/player/PlayerSurface";
import { PlayerSlotsProvider } from "@/components/player/slots";
import { BottomBar } from "@/components/player/BottomBar";
import { CommandBar } from "@/components/CommandBar";
import { LastfmControl } from "@/components/LastfmControl";
import { ScrobbleProvider } from "@/components/player/ScrobbleProvider";
import { ScrobbleSettings } from "@/components/ScrobbleSettings";
import "./globals.css";

// Self-hosted by next/font — no runtime request to Google, and no layout shift.
// latin-ext matters here: set titles carry Ø, €, ¥ and similar.
const sans = Inter({
  subsets: ["latin", "latin-ext"],
  variable: "--font-sans-face",
  display: "swap",
});
const mono = JetBrains_Mono({
  subsets: ["latin", "latin-ext"],
  variable: "--font-mono-face",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Danceseek",
  description: "A terminal for DJ sets — browse, scrobble clean, export.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-bg text-fg">
        {/* The player lives above the routes so a set keeps playing while you
            browse; the surface positions it over whichever slot is on screen. */}
        <PlayerProvider>
          <PlayerSlotsProvider>
            <ScrobbleProvider>
              {/* Three columns so the command bar is centred on the viewport,
                  not pushed around by the width of the controls beside it. */}
              <header className="sticky top-0 z-50 grid grid-cols-[1fr_auto_1fr] items-center gap-4 border-b border-border bg-surface px-4 py-2.5">
                <Link
                  href="/"
                  className="justify-self-start font-mono text-lg font-bold tracking-tight text-fg hover:text-accent"
                >
                  <span className="text-accent">♫</span> danceseek
                </Link>

                <CommandBar />

                <div className="flex items-center justify-self-end gap-2">
                  <LastfmControl />
                  <ScrobbleSettings />
                </div>
              </header>

              {/* pb-32 keeps the taller bottom bar from covering page content */}
              <main className="mx-auto max-w-7xl px-4 py-6 pb-32">{children}</main>

              <PlayerSurface />
              <BottomBar />
            </ScrobbleProvider>
          </PlayerSlotsProvider>
        </PlayerProvider>
      </body>
    </html>
  );
}

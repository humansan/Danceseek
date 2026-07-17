import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Danceseek",
  description: "A terminal for DJ sets — browse, scrobble clean, export.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-fg">
        {/* Persistent top bar (command bar + Last.fm connect land here later) */}
        <header className="sticky top-0 z-10 flex items-center gap-4 border-b border-border bg-surface px-4 py-3">
          <Link href="/" className="font-mono text-sm font-bold tracking-tight text-fg">
            <span className="text-accent">$</span> danceseek
          </Link>
          <div className="flex-1">
            <div className="max-w-xl border border-border bg-bg px-3 py-1.5 font-mono text-sm text-dim">
              <span className="text-accent">&gt;</span> search or paste a 1001tracklists URL
              <span className="ml-1 inline-block h-3.5 w-1.5 animate-pulse bg-accent align-middle" />
            </div>
          </div>
          <button className="border border-border px-3 py-1.5 font-mono text-xs text-dim hover:text-fg">
            connect last.fm
          </button>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}

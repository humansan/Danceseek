import Link from "next/link";
import { getSetlist, type Track } from "@/lib/api";

export const dynamic = "force-dynamic";

function youtubeId(url: string | null): string | null {
  if (!url) return null;
  const m = url.match(/[?&]v=([\w-]+)/) ?? url.match(/youtu\.be\/([\w-]+)/);
  return m ? m[1] : null;
}

function Pill({ label, on, color }: { label: string; on: boolean; color: string }) {
  return (
    <span
      className={`inline-block border px-1 font-mono text-[10px] ${
        on ? `${color} border-current` : "border-border text-border"
      }`}
    >
      {label}
    </span>
  );
}

function TrackRow({ t }: { t: Track }) {
  const r = t.resolution;
  const layered = t.played_with !== null;
  const isMashup = t.mashup_components.length > 0;
  return (
    <div
      className={`flex items-baseline gap-3 border-b border-border/60 px-2 py-1.5 font-mono text-sm hover:bg-surface-2 ${
        layered ? "pl-6" : ""
      }`}
    >
      <span className="w-14 shrink-0 text-right text-xs text-dim">{t.cue_time ?? ""}</span>
      <span className="w-6 shrink-0 text-right text-xs text-dim">
        {t.source_track_number ?? (layered ? "w/" : "")}
      </span>
      <span className="flex-1 truncate">
        {t.is_id ? (
          <span className="text-flag">[ID · unreleased]</span>
        ) : (
          <>
            <span className="text-fg">{t.artists.join(", ") || t.raw_text}</span>
            {t.title ? <span className="text-dim"> — {t.title}</span> : null}
            {t.remix ? <span className="text-link"> ({t.remix})</span> : null}
            {isMashup ? <span className="ml-1 text-flag">⋈</span> : null}
          </>
        )}
      </span>
      <span className="flex shrink-0 gap-1">
        <Pill label="S" on={!!r?.spotify} color="text-ok" />
        <Pill label="Y" on={!!r?.youtube} color="text-warn" />
        <Pill label="L" on={!!r?.lastfm} color="text-link" />
      </span>
    </div>
  );
}

export default async function SetlistPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const detail = await getSetlist(id);

  if (!detail) {
    return (
      <div className="font-mono text-sm text-dim">
        <Link href="/" className="text-link">
          ← back
        </Link>
        <p className="mt-4">no such setlist.</p>
      </div>
    );
  }

  const { setlist: s, status, coverage } = detail;
  const vid = youtubeId(s.media_url);
  const cov = coverage ?? {};

  return (
    <div>
      <Link href="/" className="font-mono text-xs text-link">
        ← back
      </Link>
      {/* three columns: tracklist · player · metadata */}
      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px_240px]">
        {/* LEFT — tracklist */}
        <section className="order-3 border border-border bg-surface lg:order-1">
          <div className="border-b border-border px-2 py-1.5 font-mono text-xs text-dim">
            tracklist · {s.tracks.length}
          </div>
          <div className="max-h-[70vh] overflow-y-auto">
            {s.tracks.map((t) => (
              <TrackRow key={t.position} t={t} />
            ))}
          </div>
        </section>

        {/* CENTER — the actual set recording */}
        <section className="order-1 lg:order-2">
          {vid ? (
            <div className="aspect-video w-full border border-border bg-black">
              <iframe
                className="h-full w-full"
                src={`https://www.youtube.com/embed/${vid}`}
                title={s.title ?? "set"}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; picture-in-picture"
                allowFullScreen
              />
            </div>
          ) : (
            <div className="flex aspect-video items-center justify-center border border-border bg-surface font-mono text-xs text-dim">
              no set recording linked
            </div>
          )}
        </section>

        {/* RIGHT — metadata & actions */}
        <aside className="order-2 border border-border bg-surface p-3 lg:order-3">
          <h1 className="font-sans text-sm font-semibold leading-snug">{s.title}</h1>
          <div className="mt-2 font-mono text-xs text-dim">{s.event}</div>
          <div className="font-mono text-xs text-dim">{s.date_recorded}</div>
          <div className="mt-3 font-mono text-xs">
            <span className="text-ok">{status}</span>
            <span className="text-dim">
              {" · "}
              {(cov.resolved ?? 0) + (cov.partial ?? 0)}/{s.tracks.length} matched
              {cov.unreleased ? ` · ${cov.unreleased} ID` : ""}
            </span>
          </div>
          <div className="mt-3 flex flex-col gap-1.5">
            <button className="border border-border px-2 py-1.5 text-left font-mono text-xs text-dim hover:text-fg">
              scrobble ▸
            </button>
            <button
              className="border border-border px-2 py-1.5 text-left font-mono text-xs text-dim disabled:opacity-50"
              disabled={status !== "resolved"}
            >
              export ▸ {status !== "resolved" ? "(locked)" : ""}
            </button>
          </div>
          {s.genres.length ? (
            <div className="mt-3 flex flex-wrap gap-1">
              {s.genres.map((g) => (
                <span key={g} className="border border-border px-1 font-mono text-[10px] text-dim">
                  {g}
                </span>
              ))}
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

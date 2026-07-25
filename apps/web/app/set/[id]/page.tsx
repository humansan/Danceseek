import Link from "next/link";
import { TheatreSlot } from "@/components/player/PlayerSurface";
import { getCues, getSetlist } from "@/lib/api";
import { LoadSet, NowPlaying, ScrobbleActions, TrackList } from "./SetClient";

export const dynamic = "force-dynamic";

function youtubeId(url: string | null): string | null {
  if (!url) return null;
  const m = url.match(/[?&]v=([\w-]+)/) ?? url.match(/youtu\.be\/([\w-]+)/);
  return m ? m[1] : null;
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
  const vid = youtubeId(s.media_url ?? null);
  const cov = coverage ?? {};
  const tracks = s.tracks ?? [];
  const genres = s.genres ?? [];
  const djs = s.dj_names ?? [];

  // Cue windows come from the API so the highlight and the scrobbler can never
  // disagree. The player's real duration refines the last window client-side.
  const cues = await getCues(id);

  return (
    <div>
      <LoadSet setId={id} videoId={vid} title={s.title ?? "set"} cues={cues} />

      <div className="mb-3 flex items-baseline gap-3">
        <Link href="/" className="font-mono text-xs text-link">
          ← back
        </Link>
        <span className="font-mono text-xs text-dim">
          {djs.join(", ")}
          {s.event ? ` · ${s.event}` : ""}
          {s.date_recorded ? ` · ${s.date_recorded}` : ""}
        </span>
      </div>

      {/* THEATRE — the set recording, large. Scrolling past it drops the player
          into the bottom bar and reveals the tracklist below. */}
      <section>
        <TheatreSlot hasRecording={!!vid} />
        {vid ? <NowPlaying estimated={cues?.live_capable === false} /> : null}
      </section>

      <h1 className="mt-5 font-sans text-lg font-semibold leading-snug">{s.title}</h1>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_260px]">
        <TrackList tracks={tracks} cues={cues} title={s.title ?? "set"} />

        <aside className="order-first lg:order-last">
          <div className="border border-border bg-surface p-3">
            <div className="font-mono text-xs">
              <span className={status === "resolved" ? "text-ok" : "text-warn"}>{status}</span>
              <span className="text-dim">
                {" · "}
                {(cov.resolved ?? 0) + (cov.partial ?? 0)}/{tracks.length} matched
                {cov.unreleased ? ` · ${cov.unreleased} ID` : ""}
              </span>
            </div>
            {/* Per-platform counts. `platforms` is what the run actually
                searched, so a 0 reads as "never tried" rather than "no match". */}
            {cov.platforms?.length ? (
              <div className="mt-2 border-t border-border/60 pt-2 font-mono text-[11px]">
                {(["spotify", "youtube", "lastfm"] as const).map((p) => {
                  const searched = cov.platforms?.includes(p);
                  const n = (cov[p] as number | undefined) ?? 0;
                  return (
                    <div key={p} className="flex justify-between">
                      <span className="text-dim">{p}</span>
                      <span className={searched ? (n ? "text-ok" : "text-warn") : "text-border"}>
                        {searched ? `${n}/${tracks.length}` : "not searched"}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : null}

            <div className="mt-3 flex flex-col gap-1.5">
              <ScrobbleActions liveCapable={cues?.live_capable ?? false} />
              <button
                className="border border-border px-2 py-1.5 text-left font-mono text-xs text-dim disabled:opacity-50"
                disabled={status !== "resolved"}
              >
                export ▸ {status !== "resolved" ? "(locked)" : ""}
              </button>
            </div>

            {genres.length ? (
              <div className="mt-3 flex flex-wrap gap-1">
                {genres.map((g) => (
                  <span key={g} className="border border-border px-1 font-mono text-[10px] text-dim">
                    {g}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </aside>
      </div>
    </div>
  );
}

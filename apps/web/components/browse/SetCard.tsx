import Image from "next/image";
import Link from "next/link";
import { formatLength, youtubeId } from "@/lib/format";
import type { SetlistSummary } from "@/lib/api";

/** The mono stat line (design §5.1): honest about what resolved and what didn't. */
export function StatLine({ s }: { s: SetlistSummary }) {
  const cov = s.coverage ?? {};
  const resolved = cov.resolved ?? 0;
  const unreleased = cov.unreleased ?? 0;
  const length = formatLength(s.length_s);
  return (
    <div className="font-mono text-[11px] text-dim">
      {s.track_count} trk
      {resolved ? <span className="text-ok"> · {resolved}✓</span> : null}
      {unreleased ? <span className="text-flag"> · {unreleased} ID</span> : null}
      {length ? <span> · {length}</span> : null}
    </div>
  );
}

/** A flat block derived from the set's genres, for sets with no recording. */
function GenreBlock({ genres }: { genres: string[] }) {
  const seed = (genres[0] ?? "").split("").reduce((a, c) => a + c.charCodeAt(0), 0);
  const hue = seed % 360;
  return (
    <div
      className="flex aspect-video w-full items-center justify-center border-b border-border"
      style={{
        background: `linear-gradient(135deg, hsl(${hue} 30% 12%), hsl(${(hue + 40) % 360} 30% 7%))`,
      }}
    >
      <span className="px-3 text-center font-mono text-[10px] uppercase tracking-widest text-dim">
        {genres[0] ?? "no recording"}
      </span>
    </div>
  );
}

export function SetCard({ s }: { s: SetlistSummary }) {
  const vid = youtubeId(s.media_url);
  const djs = s.dj_names ?? [];

  return (
    <Link
      href={`/set/${s.id}`}
      className="group flex flex-col border border-border bg-surface transition-colors hover:border-accent"
    >
      <div className="relative">
        {vid ? (
          <Image
            src={`https://i.ytimg.com/vi/${vid}/hqdefault.jpg`}
            alt=""
            width={480}
            height={270}
            className="aspect-video w-full border-b border-border object-cover"
            unoptimized
          />
        ) : (
          <GenreBlock genres={s.genres ?? []} />
        )}
        {vid ? (
          <span className="absolute bottom-1 right-1 bg-bg/80 px-1 font-mono text-[10px] text-link opacity-0 transition-opacity group-hover:opacity-100">
            ▸ play
          </span>
        ) : null}
      </div>

      <div className="flex flex-1 flex-col justify-between gap-3 p-3">
        <div>
          <div className="font-sans text-sm font-semibold leading-snug text-fg group-hover:text-accent">
            {s.title ?? "(untitled set)"}
          </div>
          <div className="mt-1 truncate font-mono text-[11px] text-dim">
            {djs.join(", ")}
            {s.event ? ` · ${s.event}` : ""}
            {s.date_recorded ? ` · ${s.date_recorded}` : ""}
          </div>
        </div>
        <StatLine s={s} />
      </div>
    </Link>
  );
}

/** The dense bordered table power users get from the view toggle (design §5.1). */
export function SetRow({ s }: { s: SetlistSummary }) {
  const length = formatLength(s.length_s);
  const cov = s.coverage ?? {};
  return (
    <Link
      href={`/set/${s.id}`}
      className="flex items-baseline gap-3 border-b border-border/60 px-2 py-1.5 font-mono text-xs hover:bg-surface-2"
    >
      <span className="min-w-0 flex-1 truncate text-fg">{s.title ?? "(untitled set)"}</span>
      <span className="hidden w-40 shrink-0 truncate text-dim sm:block">
        {(s.dj_names ?? []).join(", ")}
      </span>
      <span className="w-20 shrink-0 text-dim">{s.date_recorded ?? ""}</span>
      <span className="w-16 shrink-0 text-right text-dim">{length ?? ""}</span>
      <span className="w-20 shrink-0 text-right">
        <span className="text-dim">{s.track_count} trk</span>
        {cov.resolved ? <span className="text-ok"> {cov.resolved}✓</span> : null}
      </span>
    </Link>
  );
}

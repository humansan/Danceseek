import Link from "next/link";
import { listSetlists, type SetlistSummary } from "@/lib/api";

export const dynamic = "force-dynamic"; // always fresh from the API

function StatLine({ s }: { s: SetlistSummary }) {
  const cov = s.coverage ?? {};
  const resolved = cov.resolved ?? 0;
  const unreleased = cov.unreleased ?? 0;
  return (
    <div className="font-mono text-xs text-dim">
      {s.track_count} trk
      {resolved ? <span className="text-ok"> · {resolved}✓</span> : null}
      {unreleased ? <span className="text-flag"> · {unreleased} ID</span> : null}
      <span className="text-dim"> · {s.status}</span>
    </div>
  );
}

function Card({ s }: { s: SetlistSummary }) {
  return (
    <Link
      href={`/set/${s.id}`}
      className="group flex flex-col justify-between border border-border bg-surface p-4 transition-colors hover:border-accent"
    >
      <div>
        <div className="font-sans text-sm font-semibold leading-snug text-fg group-hover:text-accent">
          {s.title ?? "(untitled set)"}
        </div>
        <div className="mt-1 font-mono text-xs text-dim">
          {s.event ?? ""}
          {s.date_recorded ? ` · ${s.date_recorded}` : ""}
        </div>
      </div>
      <div className="mt-4 flex items-end justify-between">
        <StatLine s={s} />
        {s.media_url ? <span className="font-mono text-xs text-link">▸ video</span> : null}
      </div>
    </Link>
  );
}

export default async function Home() {
  let sets: SetlistSummary[] = [];
  let error: string | null = null;
  try {
    sets = await listSetlists();
  } catch (e) {
    error = String(e);
  }

  return (
    <div>
      <h1 className="mb-4 font-mono text-sm text-dim">
        <span className="text-accent">//</span> recently added
      </h1>

      {error ? (
        <div className="border border-warn/40 bg-surface p-4 font-mono text-sm text-warn">
          could not reach the API — {error}
        </div>
      ) : sets.length === 0 ? (
        <div className="border border-border bg-surface p-6 font-mono text-sm text-dim">
          <span className="text-accent">&gt;</span> no sets yet — paste a 1001tracklists URL to add one
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {sets.map((s) => (
            <Card key={s.id} s={s} />
          ))}
        </div>
      )}
    </div>
  );
}

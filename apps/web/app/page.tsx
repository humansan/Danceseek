import { FilterChips } from "@/components/browse/FilterChips";
import { LoadMore, ViewToggle } from "@/components/browse/BrowseControls";
import { SetCard, SetRow } from "@/components/browse/SetCard";
import { getFacets, listSetlists, type Facets, type SetlistSummary } from "@/lib/api";

export const dynamic = "force-dynamic"; // always fresh from the API

const PAGE = 24;

/** Repeated params arrive as string | string[] | undefined; normalize to a list. */
function many(value: string | string[] | undefined): string[] {
  if (!value) return [];
  return Array.isArray(value) ? value : [value];
}

function Empty({ filtered }: { filtered: boolean }) {
  return (
    <div className="border border-border bg-surface p-6 font-mono text-sm text-dim">
      <span className="text-accent">&gt;</span>{" "}
      {filtered
        ? "no sets match those filters — clear one and try again"
        : "no sets yet — add one from the ingest console (uv run soundseek console)"}
    </div>
  );
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const query = {
    q: typeof sp.q === "string" ? sp.q : undefined,
    dj: many(sp.dj),
    genre: many(sp.genre),
    event: many(sp.event),
    year: many(sp.year),
  };
  const limit = Math.min(Number(sp.limit) || PAGE, 100);
  const list = sp.view === "list";
  const filtered = Boolean(
    query.q || query.dj.length || query.genre.length || query.event.length || query.year.length,
  );

  let sets: SetlistSummary[] = [];
  let facets: Facets | null = null;
  let error: string | null = null;
  try {
    // One extra row is how we know whether there's another page — no count query.
    [sets, facets] = await Promise.all([
      listSetlists({ ...query, limit: limit + 1 }),
      getFacets(),
    ]);
  } catch (e) {
    error = String(e);
  }

  const hasMore = sets.length > limit;
  const page = hasMore ? sets.slice(0, limit) : sets;

  if (error) {
    return (
      <div className="border border-warn/40 bg-surface p-4 font-mono text-sm text-warn">
        ✗ could not reach the API — {error}
        <div className="mt-1 text-dim">is it running on {process.env.API_URL ?? "127.0.0.1:8010"}?</div>
      </div>
    );
  }

  return (
    <div>
      {facets ? <FilterChips facets={facets} /> : null}

      <div className="mb-3 flex items-center justify-between">
        <h1 className="font-mono text-sm text-dim">
          <span className="text-accent">//</span>{" "}
          {filtered ? `${page.length} match${page.length === 1 ? "" : "es"}` : "recently added"}
          {query.q ? <span className="text-fg"> · &ldquo;{query.q}&rdquo;</span> : null}
        </h1>
        <ViewToggle />
      </div>

      {page.length === 0 ? (
        <Empty filtered={filtered} />
      ) : list ? (
        <div className="border border-border bg-surface">
          {page.map((s) => (
            <SetRow key={s.id} s={s} />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {page.map((s) => (
            <SetCard key={s.id} s={s} />
          ))}
        </div>
      )}

      {hasMore ? <LoadMore nextLimit={limit + PAGE} /> : null}
    </div>
  );
}

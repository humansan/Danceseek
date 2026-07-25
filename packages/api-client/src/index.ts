// Typed Danceseek API client. Types are generated from the FastAPI OpenAPI
// schema (see ./schema.d.ts, regenerate with `npm run generate`); the thin
// helpers below wrap openapi-fetch for the calls the web app makes.

import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];

export type Setlist = Schemas["Setlist"];
export type SetlistTrack = Schemas["SetlistTrack"];
export type Resolution = Schemas["Resolution"];
export type PlatformMatch = Schemas["PlatformMatch"];
export type ExportPreview = Schemas["ExportPreview"];
export type CueWindow = Schemas["CueWindow"];
export type WindowSet = Schemas["WindowSet"];
export type Facet = Schemas["Facet"];
export type Facets = Schemas["Facets"];

/** The coverage column is a free JSONB dict on the API; these are the keys
 *  build_coverage() actually writes, typed as numbers for the UI. */
export type Coverage = {
  total?: number;
  resolved?: number;
  partial?: number;
  no_match?: number;
  unreleased?: number;
  skipped?: number;
  registry_hits?: number;
  spotify?: number;
  youtube?: number;
  lastfm?: number;
  /** Which platforms the run actually searched — lets a 0 above be read as
   *  "never tried" rather than "found nothing". */
  platforms?: string[];
  [k: string]: number | string[] | undefined;
};

export type SetlistSummary = Omit<Schemas["SetlistSummary"], "coverage"> & {
  coverage: Coverage | null;
};
export type SetlistDetail = Omit<Schemas["SetlistDetail"], "coverage"> & {
  coverage: Coverage | null;
};

export type ApiClient = ReturnType<typeof createClient<paths>>;

export function createApiClient(baseUrl: string): ApiClient {
  return createClient<paths>({ baseUrl });
}

/** Browse filters. Repeating a facet widens the result; facets combine with AND. */
export type BrowseQuery = {
  q?: string;
  dj?: string[];
  genre?: string[];
  event?: string[];
  year?: string[];
  limit?: number;
  offset?: number;
};

export async function listSetlists(
  baseUrl: string,
  query: BrowseQuery = {},
): Promise<SetlistSummary[]> {
  const { data, error } = await createApiClient(baseUrl).GET("/setlists", {
    params: { query },
    cache: "no-store",
  });
  if (error) throw new Error("GET /setlists failed");
  return (data ?? []) as SetlistSummary[];
}

export async function getFacets(baseUrl: string): Promise<Facets> {
  const { data, error } = await createApiClient(baseUrl).GET("/facets", { cache: "no-store" });
  if (error || !data) throw new Error("GET /facets failed");
  return data as Facets;
}

export async function getSetlist(baseUrl: string, id: string): Promise<SetlistDetail | null> {
  const { data, response } = await createApiClient(baseUrl).GET("/setlists/{setlist_id}", {
    params: { path: { setlist_id: id } },
    cache: "no-store",
  });
  if (response.status === 404) return null;
  return (data ?? null) as SetlistDetail | null;
}

/** Cue windows for a set. `duration` is the player's reported length — pass it
 *  when known so the final window ends with the recording instead of a guess. */
export async function getCues(
  baseUrl: string,
  id: string,
  duration?: number,
): Promise<WindowSet | null> {
  const { data, response } = await createApiClient(baseUrl).GET("/setlists/{setlist_id}/cues", {
    params: { path: { setlist_id: id }, query: duration ? { duration } : {} },
    cache: "no-store",
  });
  if (response.status === 404) return null;
  return (data ?? null) as WindowSet | null;
}

// NOTE: there is no addSetlist helper. Adding a set is a maintainer action
// performed in the local ingest console (apps/ingest) — the deployed API has
// no ingest route to call.

export async function exportPreview(
  baseUrl: string,
  id: string,
  target: "spotify" | "youtube",
): Promise<ExportPreview> {
  const { data, error } = await createApiClient(baseUrl).POST("/setlists/{setlist_id}/export", {
    params: { path: { setlist_id: id } },
    body: { target, expand_mashups: true, skip_played_with: false },
  });
  if (error || !data) throw new Error("POST /setlists/{id}/export failed");
  return data;
}

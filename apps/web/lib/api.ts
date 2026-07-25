// Web-facing API adapter: binds the generated @danceseek/api-client to the
// configured API base URL. Types come from the FastAPI OpenAPI schema; regen
// with `npm run generate` in packages/api-client after changing endpoints.

import {
  exportPreview as _exportPreview,
  getCues as _cues,
  getSetlist as _get,
  listSetlists as _list,
  type Coverage,
  type CueWindow,
  type WindowSet,
  type ExportPreview,
  type PlatformMatch,
  type Resolution,
  type Setlist,
  type SetlistDetail,
  type SetlistSummary,
  type SetlistTrack,
} from "@danceseek/api-client";

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8010";

export type {
  Coverage,
  CueWindow,
  WindowSet,
  ExportPreview,
  PlatformMatch,
  Resolution,
  Setlist,
  SetlistDetail,
  SetlistSummary,
};
// Historical alias used by the setlist page (schema name is SetlistTrack).
export type Track = SetlistTrack;

export function listSetlists(): Promise<SetlistSummary[]> {
  return _list(API_URL);
}

export function getSetlist(id: string): Promise<SetlistDetail | null> {
  return _get(API_URL, id);
}

// Adding a set is a maintainer action done in the local ingest console
// (`uv run soundseek console`), not from the web app — the API has no
// ingest route. The command bar is search-only.

export function exportPreview(id: string, target: "spotify" | "youtube"): Promise<ExportPreview> {
  return _exportPreview(API_URL, id, target);
}

export function getCues(id: string, duration?: number): Promise<WindowSet | null> {
  return _cues(API_URL, id, duration);
}

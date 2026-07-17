// Thin typed client for the Danceseek API. (Phase 2 will generate this from
// the OpenAPI schema; hand-written for the vertical slice.)

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8010";

export interface Coverage {
  resolved?: number;
  partial?: number;
  no_match?: number;
  unreleased?: number;
  [k: string]: number | undefined;
}

export interface SetlistSummary {
  id: string;
  title: string | null;
  dj_names: string[];
  event: string | null;
  date_recorded: string | null;
  genres: string[];
  media_url: string | null;
  track_count: number;
  status: string;
  coverage: Coverage | null;
  created_at: string | null;
}

export interface PlatformMatch {
  id: string;
  title: string;
  artists: string[];
  url: string;
}

export interface Resolution {
  status: string;
  spotify: PlatformMatch | null;
  youtube: PlatformMatch | null;
  lastfm: { artist: string; track: string } | null;
  confidence: number;
}

export interface Track {
  position: number;
  source_track_number: number | null;
  cue_time: string | null;
  raw_text: string;
  artists: string[];
  title: string | null;
  remix: string | null;
  is_id: boolean;
  played_with: number | null;
  mashup_components: { artists: string[]; title: string | null }[];
  resolution: Resolution | null;
}

export interface Setlist {
  id: string;
  title: string | null;
  dj_names: string[];
  event: string | null;
  date_recorded: string | null;
  genres: string[];
  media_url: string | null;
  media_kind: string | null;
  tracks: Track[];
}

export interface SetlistDetail {
  setlist: Setlist;
  status: string;
  coverage: Coverage | null;
  resolved_at: string | null;
  created_at: string | null;
}

export async function listSetlists(): Promise<SetlistSummary[]> {
  const res = await fetch(`${API_URL}/setlists`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET /setlists ${res.status}`);
  return res.json();
}

export async function getSetlist(id: string): Promise<SetlistDetail | null> {
  const res = await fetch(`${API_URL}/setlists/${id}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GET /setlists/${id} ${res.status}`);
  return res.json();
}

/** Shared display helpers. Kept out of components so the browse cards and the
 *  player agree on how a duration reads. */

/** 3706 -> "1h01m", 2400 -> "40m". Set lengths, not track positions. */
export function formatLength(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null;
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.round((total % 3600) / 60);
  return h ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
}

/** "45:20" or "1:02:30" -> seconds. Mirrors soundseek.scrobble.windows.cue_seconds. */
export function cueSeconds(text: string | null | undefined): number | null {
  if (!text) return null;
  const parts = text.trim().split(":");
  if (parts.length < 2 || parts.length > 3) return null;
  const values = parts.map((p) => Number.parseInt(p, 10));
  if (values.some((v) => Number.isNaN(v) || v < 0)) return null;
  return values.length === 2
    ? values[0] * 60 + values[1]
    : values[0] * 3600 + values[1] * 60 + values[2];
}

/** The YouTube video id inside a watch/youtu.be URL, or null. */
export function youtubeId(url: string | null | undefined): string | null {
  if (!url) return null;
  const m = url.match(/[?&]v=([\w-]+)/) ?? url.match(/youtu\.be\/([\w-]+)/);
  return m ? m[1] : null;
}

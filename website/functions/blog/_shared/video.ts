// Video references for blog articles (#7751) — provider URL parsing, the
// derived embed/thumbnail URLs, and the `VideoObject` structured data.
//
// Kept pure and dependency-free so the whole contract is unit-testable without
// a Cloudflare runtime, mirroring `_shared/slug.ts` / `_shared/seo-constraints.ts`.
//
// SECURITY: the embed URL is CONSTRUCTED from a validated id, never taken from
// the author's raw URL. An author can therefore write any string they like and
// the worst outcome is "not recognised as a video" — no `javascript:` or
// off-site iframe can reach the page through this path. `id` is charset-checked
// per provider, so it cannot break out of the attribute either.

import { unescapeMd } from "./md-escape.ts";

export type VideoProvider = "youtube" | "vimeo";

export interface VideoRef {
  provider: VideoProvider;
  /** Provider-validated id. */
  id: string;
  /** Always constructed here, never echoed from input. */
  embedUrl: string;
  /** Derived for YouTube; null for Vimeo (needs an API call we do not make). */
  thumbnailUrl: string | null;
  /** ISO 8601 duration, e.g. `PT12M34S`. Optional. */
  duration: string | null;
  /** `YYYY-MM-DD`. Optional — the caller falls back to the post date. */
  uploadDate: string | null;
  /** Optional explicit video title; the caller falls back to the post title. */
  title: string | null;
}

// YouTube ids are 11 chars of [A-Za-z0-9_-]. Vimeo ids are digits.
const YOUTUBE_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_DURATION_RE = /^PT(\d+H)?(\d+M)?(\d+S)?$/;

/**
 * Extract a provider + id from a video URL. Returns null for anything else —
 * including a URL on the right host but with no recognisable id, and any
 * non-http(s) scheme.
 */
export function parseVideoUrl(raw: string): { provider: VideoProvider; id: string } | null {
  const url = raw.trim();
  if (!url) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  // http is allowed for parity with `validUrl`; both are same-scheme-agnostic
  // for an iframe src, but rejecting everything else is what matters.
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  const host = parsed.hostname.toLowerCase();

  if (host === "youtu.be") {
    const id = parsed.pathname.replace(/^\//, "").split("/")[0];
    return YOUTUBE_ID_RE.test(id) ? { provider: "youtube", id } : null;
  }
  if (host === "youtube.com" || host === "www.youtube.com" || host === "m.youtube.com" || host === "music.youtube.com") {
    const v = parsed.searchParams.get("v");
    if (v && YOUTUBE_ID_RE.test(v)) return { provider: "youtube", id: v };
    // /embed/<id>, /shorts/<id>, /live/<id>
    const m = /^\/(?:embed|shorts|live|v)\/([A-Za-z0-9_-]{11})/.exec(parsed.pathname);
    if (m) return { provider: "youtube", id: m[1] };
    return null;
  }
  if (host === "youtube-nocookie.com" || host === "www.youtube-nocookie.com") {
    const m = /^\/embed\/([A-Za-z0-9_-]{11})/.exec(parsed.pathname);
    return m ? { provider: "youtube", id: m[1] } : null;
  }
  if (host === "vimeo.com" || host === "www.vimeo.com" || host === "player.vimeo.com") {
    const m = /(?:^|\/)(?:video\/)?([0-9]{6,12})(?:$|[/?#])/.exec(parsed.pathname + (parsed.search || ""));
    return m ? { provider: "vimeo", id: m[1] } : null;
  }
  return null;
}

/**
 * The embed host for each provider. Exhaustive by construction: adding a
 * `VideoProvider` forces a key here, so the origin a page will be served with
 * is a runtime value — `tests/test_blog_markdown.mjs` asserts every one of them
 * is admitted by the served `RELAXED_CSP` `frame-src` (#8379 finding 1).
 */
export const VIDEO_EMBED_ORIGINS: Readonly<Record<VideoProvider, string>> = {
  youtube: "https://www.youtube-nocookie.com",
  vimeo: "https://player.vimeo.com",
};

/** The privacy-preserving embed URL for a parsed reference. */
export function embedUrlFor(provider: VideoProvider, id: string): string {
  return provider === "youtube"
    // youtube-nocookie: no tracking cookies until playback.
    ? `${VIDEO_EMBED_ORIGINS.youtube}/embed/${id}`
    : `${VIDEO_EMBED_ORIGINS.vimeo}/video/${id}`;
}

/**
 * The poster image, where it is derivable without an API call.
 *
 * ONE rule for both providers: emit a thumbnail only when the URL is guaranteed
 * to resolve. YouTube's `hqdefault.jpg` (480x360) is generated for EVERY
 * uploaded video — including the 240p/SD-only ones; `maxresdefault.jpg` is not,
 * and 404s exactly there (measured on `jNQXAC9IVRw`, "Me at the zoo"). Since
 * `thumbnailUrl` is a REQUIRED field for a video rich result, a 404 on it is
 * worse than omitting it — which is the same reason Vimeo returns null, whose
 * poster needs an oEmbed/API call we do not make at render time.
 */
export function thumbnailUrlFor(provider: VideoProvider, id: string): string | null {
  return provider === "youtube" ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : null;
}

function normaliseDate(s: string): string | null {
  if (!ISO_DATE_RE.test(s)) return null;
  const d = new Date(`${s}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? null : s;
}

function normaliseDuration(s: string): string | null {
  const t = s.trim().toUpperCase();
  return ISO_DURATION_RE.test(t) ? t : null;
}

/**
 * Parse the payload of a `[[video: …]]` marker.
 *
 * Shape: `<url>` and then, optionally and in any order, a title, an ISO-8601
 * duration (`PT12M34S`) and a `YYYY-MM-DD` date — separated by `|`. The two
 * typed fields are recognised by SHAPE, so `| PT12M34S` and `| 2026-10-12` are
 * unambiguous, and anything else is treated as the title. That keeps the common
 * case (`[[video: <url>]]`) free of ceremony while still allowing an explicit
 * video title where the article title is not the video's.
 */
export function parseVideoMarker(payload: string): VideoRef | null {
  // Undo CommonMark backslash escapes BEFORE the shape tests (#8379 finding 1).
  // The canonical body is authored in the TipTap editor, which escapes `_`, `*`,
  // `~`, `[` and friends; that escapes the URL too (a YouTube id may contain
  // `_`), and the title is author text exactly like a heading or a link label.
  // Every other author-facing text path in `_lib.ts` unescapes; this one did not,
  // so `Episode 2: \*the\* finale` reached the iframe `title` and
  // `VideoObject.name` with the backslashes intact. Unescaping first also means
  // an escaped duration/date is recognised by shape, not mistaken for a title.
  const parts = payload.split("|").map((p) => unescapeMd(p.trim()));
  const head = parts.shift() ?? "";
  const parsed = parseVideoUrl(head);
  if (!parsed) return null;

  let duration: string | null = null;
  let uploadDate: string | null = null;
  let title: string | null = null;
  for (const part of parts) {
    if (!part) continue;
    const d = normaliseDuration(part);
    if (d !== null && duration === null) {
      duration = d;
      continue;
    }
    const dt = normaliseDate(part);
    if (dt !== null && uploadDate === null) {
      uploadDate = dt;
      continue;
    }
    if (title === null) title = part;
  }

  return {
    provider: parsed.provider,
    id: parsed.id,
    embedUrl: embedUrlFor(parsed.provider, parsed.id),
    thumbnailUrl: thumbnailUrlFor(parsed.provider, parsed.id),
    duration,
    uploadDate,
    title,
  };
}

export interface VideoPostLike {
  title: string;
  body: string;
  published_at?: string | null;
  excerpt?: string | null;
  meta_description?: string | null;
}

/**
 * The `VideoObject` node for an article that embeds a video (#7751).
 *
 * `name`/`description`/`uploadDate` fall back to the POST's own metadata: for
 * the intended shape — an interview published WITH its article — the article's
 * title and date ARE the video's. An explicit `title`/date in the marker wins.
 *
 * `thumbnailUrl` is REQUIRED by Google for a video rich result and is emitted
 * only when `thumbnailUrlFor` is sure the URL resolves (see that function for the
 * one rule), so a Vimeo reference emits the node WITHOUT it: the schema stays
 * valid and useful to other consumers, and we do not fabricate a URL that would
 * 404.
 */
export function videoObjectJsonLd(video: VideoRef, post: VideoPostLike, url: string): Record<string, unknown> {
  const node: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "VideoObject",
    name: video.title ?? post.title,
    description: post.meta_description ?? post.excerpt ?? post.title,
    embedUrl: video.embedUrl,
    uploadDate: video.uploadDate ?? post.published_at ?? undefined,
    url,
  };
  if (video.thumbnailUrl) node.thumbnailUrl = [video.thumbnailUrl];
  if (video.duration) node.duration = video.duration;
  return node;
}

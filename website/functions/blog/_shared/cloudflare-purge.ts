// Cloudflare edge-cache purge by URL (#1865) — zero-dep Pages Function helper.
//
// Why: a Pages deploy updates the origin but does NOT purge the zone's edge
// cache (verified 2026-08-28 — Cloudflare docs + a documented stale-200
// incident). When a published post is unpublished/archived, the old HTML can
// keep serving from the edge for up to the cache TTL. Purge-by-URL is
// Cloudflare's recommended precise fix (free plan supports it).
//
// Fail-open-but-logged: missing CF_API_TOKEN / CF_ZONE_ID → return false, never
// throw into the caller's write path. The purge is a best-effort correctness
// layer; the 404 origin response + no-store on 404 remains the contract.
//
// Env bindings (Pages Function environment variables, server-side only):
//   CF_API_TOKEN   — Cloudflare API token with Zone.Cache Purge permission
//   CF_ZONE_ID     — the zone id for tortoise.premiselabs.co
//
// ZERO-DEPENDENCY (plain TS, no imports).

export interface PurgeEnv {
  CF_API_TOKEN?: string;
  CF_ZONE_ID?: string;
}

const API = "https://api.cloudflare.com/client/v4";
const TIMEOUT_MS = 3000;

/**
 * Purge one URL (and its trailing-slash variant) from the Cloudflare edge
 * cache. Resolves true when the purge API accepted the request, false when
 * the env is not configured or the call failed. Never throws.
 */
export async function purgeUrl(url: string, env: PurgeEnv): Promise<boolean> {
  const { CF_API_TOKEN, CF_ZONE_ID } = env;
  if (!CF_API_TOKEN || !CF_ZONE_ID) return false;
  if (!/^https:\/\//.test(url)) return false;

  const files = new Set<string>([url]);
  if (url.endsWith("/")) files.add(url.slice(0, -1));
  else files.add(`${url}/`);

  try {
    const res = await fetch(`${API}/zones/${encodeURIComponent(CF_ZONE_ID)}/purge_cache`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${CF_API_TOKEN}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ files: [...files] }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) return false;
    const body = (await res.json()) as { success?: boolean };
    return body.success === true;
  } catch {
    return false; // timeout / network — fail open, never block the write
  }
}

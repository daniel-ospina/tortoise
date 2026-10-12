// Shared agent-key authentication + rate limiting.
//
// Extracted from `api/posts/[[path]].ts` (#8293) so a SECOND agent-facing
// endpoint can authenticate the same way without a second copy of a security
// primitive — two copies of a key check drift, and a drifted key check is a
// fail-open waiting for a maintenance edit.
//
// Contract (unchanged from the posts API):
//   - `X-Agent-Key` → sha256 → looked up in `blog_agent_keys` by `key_hash`.
//   - No key / unknown key → 401.
//   - Known-but-inactive key → 404 (no key information leak: an inactive key is
//     indistinguishable from a missing route).
//   - Store unreachable → 401, never 500 — an outage must not become an oracle.
//
// The service-role key is used for the lookup because `blog_agent_keys` has no
// public read policy; the key itself is never returned in any response.

import type { Env } from "../_lib.ts";

export interface AgentIdentity {
  agentName: string;
}

export type AuthResult = { ok: true; identity: AgentIdentity } | { ok: false; code: 401 | 404 };

export async function sha256Hex(s: string): Promise<string> {
  const data = new TextEncoder().encode(s);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function authenticate(env: Env, request: Request): Promise<AuthResult> {
  const key = request.headers.get("X-Agent-Key");
  if (!key) return { ok: false, code: 401 };
  const hash = await sha256Hex(key);
  try {
    const url = `${env.SUPABASE_URL ?? ""}/rest/v1/blog_agent_keys?select=agent_name,active&key_hash=eq.${hash}&limit=1`;
    const res = await fetch(url, {
      headers: {
        apikey: env.SUPABASE_SERVICE_ROLE_KEY ?? "",
        Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY ?? ""}`,
        Accept: "application/json",
      },
    });
    if (!res.ok) throw new Error(`supabase ${res.status}`);
    const rows = (await res.json()) as Array<{ agent_name: string; active: boolean }>;
    const row = rows[0];
    if (!row) return { ok: false, code: 401 };
    if (!row.active) return { ok: false, code: 404 }; // known-but-inactive key (contract)
    return { ok: true, identity: { agentName: row.agent_name } };
  } catch {
    return { ok: false, code: 401 }; // upstream unreachable — treat as unauthorized (no key info leak)
  }
}

// ── Rate limiting (best-effort per isolate, keyed on caller identity) ──────
// Per-isolate Maps are not a global limit; they bound a single hot isolate,
// which is the failure mode that matters (a runaway loop). Kept identical to
// the posts API's shape: a burst window plus a daily ceiling.
export interface RateLimit {
  windowMs: number;
  burst: number;
  dayMs: number;
  day: number;
}

const counters = new Map<string, { burst: number[]; day: number[] }>();

export function rateLimited(callerId: string, limit: RateLimit): boolean {
  const now = Date.now();
  const c = counters.get(callerId) ?? { burst: [], day: [] };
  c.burst = c.burst.filter((t) => now - t < limit.windowMs);
  c.day = c.day.filter((t) => now - t < limit.dayMs);
  if (c.burst.length >= limit.burst || c.day.length >= limit.day) {
    counters.set(callerId, c);
    return true;
  }
  c.burst.push(now);
  c.day.push(now);
  counters.set(callerId, c);
  return false;
}

/** Test seam — reset the per-isolate counters between cases. */
export function __resetRateLimits(): void {
  counters.clear();
}

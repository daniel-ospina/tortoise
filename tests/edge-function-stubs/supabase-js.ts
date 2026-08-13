// Test stub for https://esm.sh/@supabase/supabase-js@2. The real client is
// only used on the JWT auth path (a Bearer token present) and the RPC write
// (post-auth) — neither runs in the CORS harness. Throwing proves those paths
// are never reached when we assert preflight/401/403/405 responses.
export function createClient(): never {
  throw new Error("createClient called — unexpected on CORS-test paths");
}

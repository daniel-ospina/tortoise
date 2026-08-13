// Test stub for supabase/functions/_shared/lookup.ts — pure TS with no Deno
// deps (the real file). Only reachable on the deep success path (201), which
// the CORS harness does not exercise; throwing proves no tested path reaches it.
export async function lookupHash(_key: string, _pepper: string): Promise<string> {
  throw new Error("lookupHash called — unexpected on CORS-test paths");
}

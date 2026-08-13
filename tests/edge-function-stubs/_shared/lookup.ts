// Test stub for supabase/functions/_shared/lookup.ts — pure TS with no Deno
// deps (like the real file). Returns a fixed value so the success (201) path
// can complete; the harness asserts the response shape, not the hash content.
export async function lookupHash(_key: string, _pepper: string): Promise<string> {
  return "lookup-stub";
}

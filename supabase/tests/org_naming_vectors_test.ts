// org_naming_vectors_test.ts — Deno test pinning supabase/functions/_shared/
// orgNaming.ts to the SHARED #2779 vectors (also read by the Python and
// dashboard suites). Run explicitly:
//   deno test --allow-read supabase/tests/org_naming_vectors_test.ts
// (Not wired into CI — there is no deno step; run it when touching this
// mirror, the fixture, or org_naming.py.)
import {
  DISPLAY_NAME_MAX,
  RESERVED_IDENTIFIERS,
  normalizeDisplayName,
  orgIdentifierError,
  slugifyOrgId,
} from "../functions/_shared/orgNaming.ts";

const VECTORS = JSON.parse(
  await Deno.readTextFile(
    new URL("../../tests/fixtures/org_naming_vectors.json", import.meta.url),
  ),
);

function eq(actual: unknown, expected: unknown, label: string): void {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

Deno.test("#2779 shared vectors: slugifyOrgId matches org_naming.slugify_id", () => {
  for (const { input, expected } of VECTORS.slugify) {
    eq(slugifyOrgId(input), expected, `slugifyOrgId(${JSON.stringify(input)})`);
  }
});

Deno.test("#2779 shared vectors: normalizeDisplayName accepts free text", () => {
  for (const v of VECTORS.display_names) {
    const got = normalizeDisplayName(v.input);
    if (v.valid) {
      eq(got, v.normalized, `normalizeDisplayName(${JSON.stringify(v.input)})`);
    } else {
      if (got !== null) {
        throw new Error(`expected rejection for ${JSON.stringify(v.input)}, got ${JSON.stringify(got)}`);
      }
    }
  }
});

Deno.test("#2779 shared vectors: orgIdentifierError names the character", () => {
  for (const v of VECTORS.identifier_errors) {
    const err = orgIdentifierError(v.input);
    if (v.message_contains.length === 0) {
      eq(err, null, `orgIdentifierError(${JSON.stringify(v.input)})`);
    } else {
      if (err === null) throw new Error(`expected a rejection for ${JSON.stringify(v.input)}`);
      for (const needle of v.message_contains) {
        if (!err.includes(needle)) throw new Error(`${JSON.stringify(err)} should contain ${JSON.stringify(needle)}`);
      }
    }
  }
});

Deno.test("#2779: derived identifiers are charset-safe and never reserved", () => {
  const samples = [
    ...VECTORS.slugify.map((v: { input: string }) => v.input),
    ...VECTORS.display_names.map((v: { input: string }) => v.input),
    "a/b", "a b", "!!!", "../../etc/passwd", "Café Ltd", "x".repeat(200),
  ];
  for (const raw of samples) {
    const id = slugifyOrgId(raw);
    if (!id || id.length > DISPLAY_NAME_MAX) throw new Error(`bad length for ${JSON.stringify(raw)}: ${JSON.stringify(id)}`);
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(id)) throw new Error(`bad charset for ${JSON.stringify(raw)}: ${JSON.stringify(id)}`);
    if (RESERVED_IDENTIFIERS.has(id)) throw new Error(`reserved id for ${JSON.stringify(raw)}: ${id}`);
  }
});

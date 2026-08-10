// TS↔Python parity test for lookup_hash (#669 plan P1-1 — the Supabase
// control-plane instant key lookup digest).
//
//   lookup_hash := SHA-256(pepper + key), lowercase hex
//
// Contract: tortoise/auth.py lookup_hash() and
// supabase/functions/_shared/lookup.ts lookupHash() MUST produce identical
// digests — Task 3 (#767) resolves keys against these hashes, so any drift
// breaks auth for every provisioned team. This test locks both sides:
//
//   1. hardcoded test vectors (independently computed with hashlib.sha256 —
//      the authoritative digest),
//   2. live cross-check: spawns python3 and compares the TS digest against
//      the actual Python implementation for every vector.
//
// Run:  node supabase/tests/lookup_parity.test.mjs   (node >= 22.18 — type
//       stripping runs the .ts mirror directly; python3 must be on PATH)
// Exit: 0 = parity confirmed; non-zero = drift (fail the build).
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, '..', '..');
const SHARED_TS = join(REPO_ROOT, 'supabase', 'functions', '_shared', 'lookup.ts');

const { lookupHash } = await import(SHARED_TS);

// ── Authoritative vectors (computed with hashlib.sha256(pepper + key)) ──
const VECTORS = [
  { key: 'tt_test_key_vector_1', pepper: 'test-pepper-vector-1',
    expect: '6425fad49ef4d3200dedf1fcb29bbb82cfa09a3c269ff4ed7efb5cbc911bdb23' },
  { key: 'tt_', pepper: '',
    expect: 'da3be08582a6cc5a51661a4be5483393d777f8e48858fdde0d750e5ac0c62468' },
  { key: 'tt_abcdef0123456789abcdef0123456789', pepper: 'another-pepper',
    expect: '8d75c03bd1c61b114afc8371d2de8cc896bd1bd0da50581046b3a27663c658c0' },
];

let failures = 0;

for (const v of VECTORS) {
  const tsHex = await lookupHash(v.key, v.pepper);
  if (tsHex !== v.expect) {
    console.error(`✗ TS vector mismatch: key=${v.key} pepper=${v.pepper}`);
    console.error(`  expected ${v.expect}\n  got      ${tsHex}`);
    failures++;
  } else {
    console.log(`✓ TS digest matches authoritative vector (${v.expect.slice(0, 16)}…)`);
  }
}

// ── Live cross-check against the real Python implementation ─────────────
const PY_SNIPPET = `
import os, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
os.environ['TORTOISE_SECRET_PEPPER'] = 'python-side-pepper'
# Re-import so the pepper is picked up from env (module reads it at import).
import importlib, tortoise.auth as a
importlib.reload(a)
import json
print(json.dumps({k: a.lookup_hash(k) for k in sys.argv[1:]}))
`;
const pyKeys = ['tt_cross_check_key_1', 'tt_cross_check_key_2'];
const pythonOut = execFileSync('python3', ['-c', PY_SNIPPET, ...pyKeys], {
  encoding: 'utf8',
  env: { ...process.env, PYTHONPATH: REPO_ROOT },
}).trim();
const pyResults = JSON.parse(pythonOut);

for (const key of pyKeys) {
  const tsHex = await lookupHash(key, 'python-side-pepper');
  const pyHex = pyResults[key];
  if (tsHex !== pyHex) {
    console.error(`✗ TS/Python drift: key=${key}`);
    console.error(`  TS:      ${tsHex}\n  Python:  ${pyHex}`);
    failures++;
  } else {
    console.log(`✓ TS and Python agree on ${key} (${tsHex.slice(0, 16)}…)`);
  }
}

if (failures > 0) {
  console.error(`\n✗ ${failures} parity check(s) FAILED — Python/TS lookup_hash drift`);
  process.exit(1);
}
console.log('\n✅ lookup_hash TS↔Python parity confirmed');

<!-- issue-scoping: v5.1 double diamond + verify (slice context) -->

# #1930 Scope — TORTOISE_PACKS_DIR env override + fail-safe resolution order

> Epic #1891, slice WF-2 (Registry resolution at startup). Complexity: standard.
> Scope artifact for issue-scoping gates; implementation plan follows in
> `docs/plans/2026-08-29-1930-packs-dir.md`.

## Confirmed Problem

Self-host operators need a way to point Tortoise at a custom pack directory,
and the resolution must be fail-safe: a misconfigured `TORTOISE_PACKS_DIR`
(missing, empty, or containing only broken manifests) must NEVER silently
produce an empty pack registry (the G1 defect class). Today there is no env
var at all — `_PACKS_DIR` is test-only injection and `default_packs_dir()`
resolves packaged → repo root.

Resolution order (epic 05-plan §2 WF-2): `TORTOISE_PACKS_DIR` (set+valid) →
packaged default (`tortoise/packs/` via package_dir mapping) → repo root
(dev/editable). Every fallback trigger warns; unknown `TORTOISE_STARTER_PACKS`
names keep their existing warn-skip.

## Problem Diamond

### problem-diverge — alternative framings considered

| Framing | Strength | Weakness | Verdict |
|---|---|---|---|
| **F1 (original): env override with warn+fallback chain** | Matches epic WF-2 + TORTOISE_STARTER_PACKS/TORTOISE_ROUTING_CONFIG precedent; minimal surface | None material | **Chosen** |
| F2: config file (YAML/TOML) for pack dir | More powerful (multiple dirs, ordering) | No precedent in the daemon config surface; env is the established pattern for self-host (DB_URI, STARTER_PACKS, ROUTING_CONFIG); scope creep | Rejected |
| F3: make the empty-registry defect a hard error (raise) | Loudest possible failure | Violates WF-2 ("warn + fallback", never break startup); breaks dev bootstrap where catalog legitimately absent; the registry must degrade to legacy vocabulary (ponytail) | Rejected |
| F4: symlink/CLI-only config | No env var needed | Self-host operators use env (12-factor); CLI is #1931's scope; not available at daemon startup before shell init | Rejected |

### problem-converge

**Confirmed problem (one sentence):** Honor `TORTOISE_PACKS_DIR` with the
env → packaged → repo-root resolution chain and warn-on-fallback so a bad
value never silently yields an empty registry.

**Falsification check:** the definition is wrong if (a) a set+valid env dir
does not appear in `tortoise_packs_list` after restart, or (b) a missing /
empty / all-malformed env dir resolves to anything other than warn + packaged
→ repo-root fallback.

**Confidence: 90** — issue O/I/T + epic WF-2/E2E-2 + in-repo precedent
(TORTOISE_STARTER_PACKS warn-skip, TORTOISE_ROUTING_CONFIG packaged-default
fallback) all converge on the same design.

## Assumptions (mapped)

| Assumption | Status | Evidence / falsification |
|---|---|---|
| All pack-dir consumers resolve through `default_packs_dir()` | [validated] | grep: sdk.py:919, commit_schema.py:139, query_suggestions.py:108, extractor_v2.py:2645/2686, value_extractor.py:26/117, domain_loader.py:90 all call it (the #1929 single primitive) |
| The env leg must therefore live inside `default_packs_dir()` — NOT only in `_get_registry()` | [validated] | If only `_get_registry()` honored it, extractor/value-brief/write-gate consumers would mint from the default catalog → custom pack kinds never flow into extraction = feature broken (split-brain) |
| "Malformed manifests → fallback" (issue Indicator 2) means all-manifests-malformed → fallback; mixed malformed+valid → isolate (WF-2: "that pack isolated, others load") | [validated] | WF-2 text + E2E-2 assert ("malformed pack ABSENT while the others load") are explicit on the mixed case; Indicator 2's "never silent empty" governs the all-broken case |
| `load_all()` reports 0 healthy packs with non-empty `errors` when every manifest fails validation | [validated] | pack_registry.py load_all: per-pack try/except → errors dict; R-16 drops; count reflects healthy only |
| `TORTOISE_STARTER_PACKS` warn-skip needs NO code change | [validated] | pack_state.py ensure_tenant_packs already warn-skips unknown names; tests exist (test_pack_state.py::test_unknown_starter_names_skipped_with_warning). Preserve = regression-test only |
| config.py is out of scope | [validated] | config.py owns DB path resolution only (TORTOISE_DB_PATH/URI); no pack role. Issue's "Components" list is auto-populated generic |

## Solution Diamond

### solution-diverge — approaches

**S1 — Env leg inside `default_packs_dir()` (the #1929 single primitive).**
New `env_packs_dir()` helper resolves the TORTOISE_PACKS_DIR leg (set+valid →
dir; set-but-missing/not-a-dir/empty → warn + None). `default_packs_dir()`
prepends it. All 7 consumers honor the override automatically.
- Files: pack_registry.py, domain_loader.py, tests.
- Best fit if: we want the feature to work everywhere (list surface AND extraction
  prompts AND write gates) with one resolution rule.
- Risks: warn-once needed (hot-path resolver); env dir with all-broken manifests
  still resolves to itself → registry-level fallback needed (S1a).

**S1a — registry-level all-broken fallback (subsidiary to S1).** In
`domain_loader._get_registry()`: when the env leg was active but `load_all()`
yielded 0 healthy packs with non-empty `errors` → warn + re-resolve skipping
env + reload. Mixed valid+malformed → R-16 isolation + warn, no fallback.
- Best fit if: the "never silent empty" guarantee must hold for the daemon
  registry (`tortoise_packs_list`) even when manifests exist but are all broken.

**S2 — Env handling only in `domain_loader._get_registry()` (literal issue
wording).** `_get_registry` checks `os.environ["TORTOISE_PACKS_DIR"]` itself.
- Rejected: splits the resolution primitive — extractor_v2/value_extractor/
  sdk/commit_schema/query_suggestions would load the DEFAULT catalog while the
  registry list shows the custom pack. Custom pack kinds never reach extraction.
  The issue's own target says the resolution chain is "env → packaged default
  (`tortoise/packs/`) → repo root" — a single chain, not a registry-only chain.

**S3 — Health-check inside `default_packs_dir()` (load manifests in the path
resolver).** The resolver validates manifests to decide fallback.
- Rejected: loads/validates on a hot path (called per value-brief compile, per
  write-gate build); duplicates PackRegistry validation; circular (pack_registry
  internal consumers call `default_packs_dir()`); path resolution and manifest
  validation are different concerns (R-16 already owns isolation).

### solution-converge

**Chosen: S1 + S1a.** S1 gives every consumer the override through the single
primitive; S1a closes the "all manifests broken → silent empty" gap at the
daemon registry (the `tortoise_packs_list` surface the issue's indicators
reference). S2 was rejected on split-brain grounds; S3 on layering/perf grounds.

**Rejected-alternative notes (when each WOULD have been better):**
- S2 would be better if the issue truly intended list-only semantics — it does
  not (extraction must mint custom kinds; epic WF-2 is one chain).
- S3 would be better if manifests were cheap to validate and the resolver were
  the only consumer — it is not.

## Complexity

| Domain | Rating | Rationale |
|--------|--------|-----------|
| Architecture | standard | Resolution-chain change at daemon startup + conditional-guard surface (issue rating); bounded: 2 source files + tests |

## Wiring Check

| Touch Point | Type | Covered By | Status |
|-------------|------|------------|--------|
| `pack_registry.default_packs_dir()` env leg | code | S1 implementation + unit tests | ✅ |
| `domain_loader._get_registry()` all-broken fallback | code | S1a + integration tests | ✅ |
| Extractor / value-brief / sdk / write-gate consumers | code | Transitive via `default_packs_dir()` (no change needed; regression by existing suites) | ✅ |
| `TORTOISE_STARTER_PACKS` warn-skip | behavior | Existing test (no change) | ✅ |
| `.env.example` docs | docs | New `TORTOISE_PACKS_DIR` block | ✅ |
| FalkorDB integration lane | infra | Slice tests + pack-registry tests on docker lane | ✅ |

## Verification Checklist (issue §Verification)

| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| 3 Registry resolution | unit + integration | each fallback trigger boundary-tested: env set+valid / env missing / env empty-dir / env malformed-dir / env unset |
| 3 warn-not-silent | integration | empty/missing dir produces a startup warning, never a silent empty registry |

## Controller Convergence (scope-verify gate — 2 verifiers, findings incorporated)

Verified against code: `tortoise_packs_list` (mcp_server.py) is served by
`pack_state.get_tenant_packs` — an ACTIVATION-GATED join: only `PackInstall`
nodes (created by `ensure_tenant_packs` for `TORTOISE_STARTER_PACKS` /
DEFAULT_STARTER_PACKS namespaces) are listed, joined with catalog metadata
that flows through `_get_registry()` (`_resolve_catalog`).

| # | Severity | Finding | Controller action |
|---|----------|---------|-------------------|
| 1 | P1 | `tortoise_packs_list` is activation-gated, not a raw registry listing — Indicator 1 as literally written needs starter-set membership or auto-activation | FIX (re-scoped honestly): this slice makes the custom pack resolve in the SHARED CATALOG (`_get_registry()`), which `get_tenant_packs` joins against; indicator-1 test = custom ns in catalog + composition test (env dir + `TORTOISE_STARTER_PACKS` incl. custom ns → activated + visible). Falsification (a) reworded to the catalog surface. Auto-activation wiring is epic-level (E2E-2, later slices), documented as boundary |
| 2 | P1 | Reverse split-brain: S1a fallback only in `_get_registry()` → 5 transitive consumers (extractor_v2, value_extractor, sdk, commit_schema, query_suggestions) resolve the all-broken env dir → silent empty vocab | FIX: (i) `_has_loadable_manifests()` skip-rules-aligned glob protects ALL consumers from `_template`/hidden-only silent-empty; (ii) `load_all()` gains warn-not-silent (one change → every consumer warns when a pack is isolated, E2E-2's "startup warning"); (iii) S1a keeps the daemon-registry fallback. Residual (all-broken env dir at transitive surfaces → base vocab + load warning, ponytails intact) documented as acceptable degradation |
| 3 | P1 | Reload storm: `_get_registry()` re-resolves per call → post-fallback `_registry.packs_dir` mismatch → rebuild+warn per call | FIX: sticky `_env_fallback_key` (env value that fell back) in domain_loader — while env value unchanged, resolve directly to the skip-env default; boundary test counts PackRegistry constructions across 2 calls |
| 4 | P1 | `_template`-only silent-empty: naive `glob(*/manifest.yaml)` matches `packs/_template/manifest.yaml`; load_all skips `_`/`.` dirs → 0 packs, 0 errors, no warn, no fallback | FIX: `_has_loadable_manifests()` applies the same `_`/`.` skip rule as load_all; test: env dir with only `_template/manifest.yaml` → warn + fallback |
| 5 | P1 | Ambient-env breakage: test_pack_shipping.py resolver tests + wheel subprocess inherit a set `TORTOISE_PACKS_DIR` → suite silently exercises the wrong path | FIX: autouse fixture `monkeypatch.delenv("TORTOISE_PACKS_DIR", raising=False)` in test_pack_shipping.py; sanitized `env=` for the wheel subprocess; sticky-key reset alongside `_registry = None` |
| 6 | P2 | Blank/whitespace env → `Path("")` = CWD (dangerous in Docker) | FIX: stripped-blank = unset (config.py precedent) + boundary test |
| 7 | P2 | Fallback invisible to introspection | FIX: `PackRegistry.fallback_note` attribute set by S1a (queryable; not wired into pack_summaries — out of slice) |
| 8 | P2 | skip-env mechanism unspecified / racy | FIX: pure kwarg `default_packs_dir(_skip_env=True)`, no global/env mutation |
| 9 | P2 | Always-fallback masks staged-empty operator intent + self-heal MERGE persistence side effect | ACCEPT (epic decision: empty registry = defect, never a choice; side effect is identical to today's unset case) + documented in plan |
| 10 | P2 | Docker/quickstart bind-mount docs | PARTIAL: `.env.example` block documents absolute-path + bind-mount convention; container deploy docs deferred (deploy-surface slices) |
| 11 | P3 | `_PACKS_DIR` precedence undocumented | FIX: precedence `_PACKS_DIR` (test injection) > env > default; test both-set case |
| 12 | P3 | Relative/tilde env values; per-process consumer caches; G1 double-warning attribution | FIX/DOC: expanduser + absolute-path convention; caches documented as process-lifetime ("after restart" semantics); env warnings attribute the cause |
| 13 | P4 | Env-var composition test (packs dir + starter names) missing | FIX: composition integration test |

Re-dispatch: not required — all P0/P1 addressed by controller fixes above (verifiers will re-check at plan gate).

## Review Cycle Log

- problem-verify: merged into scope-verify dispatch (2 verifiers, 1 cycle, P1s 1–5 fixed as above).
- solution-verify: same dispatch covered both diamonds (scope + solution) — P0/P1 fixed as above.

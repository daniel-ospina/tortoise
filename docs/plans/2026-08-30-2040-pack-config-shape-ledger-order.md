---
title: "Plan — #2040 import pack_config shape 500s post-swap; pack failures stamp ledger"
type: engineering
domain: capability
doc_status: live
created: 2026-08-30
subjects.team: epistemic-team
ownedBy: epistemic-team
---

<!-- research-path: issue #2040 (surfaced by #2028 post-fix adversarial verification, 2026-08-30); epic #1891 slice #1936; scoping comment 2026-08-30 (v5.1 double diamond + verify) -->

# Issue #2040 — Malformed pack_config 500s post-swap; pack failures wedge the import ledger

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** (a) Any malformed `pack_config` shape on import must 422 fail-closed PRE-RESTORE (live graph untouched, `last_import_sha256` unstamped) instead of crashing as a post-swap 500 (or silently 200ing with the config dropped). (b) Pack-application failures must not stamp `last_import_sha256` — the stamp moves AFTER successful pack application and failures CLEAR the ledger so a failed import is retryable/rollback-able (never `{"already": true}` wedge).

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Three-part fix in `tortoise/hosted_api.py` + one shared-validator hardening in `tortoise/pack_manifest_store.py` (+ tests).

1. **`_pack_config_shape_error(pc) -> str | None`** — single-source shape validator (check order pinned: dict → schema_version → packs → entry → ns → yaml). `_validate_import_envelope` (final step) raises `_ImportVerifyError(err, payload_sha)` → 422 + quarantine pre-restore (live graph untouched, ledger unstamped). `_apply_import_pack_config` raises `ValueError` on the same error (drift-defense for direct callers).
2. **Stamp reorder + ledger clear**: `last_import_sha256` stamped AFTER successful `_apply_import_pack_config`; on pack-application failure the except block quarantines AND clears `last_import_sha256` (`""` — the issue's prescribed "leaves last_import_sha256 clear" alternative) so same-artifact retry and rollback-to-prior-artifact both converge.
3. **Apply hardening**: fail-loudly via the shared validator; PRE-WRITE ns↔yaml guard (`validate_manifest` first, compare `result.namespace` vs declared ns BEFORE upserting — no stray residue).
4. **`validate_manifest` RecursionError catch** (deeply-nested yaml → clean validation failure, not a 500).

### Pattern Research

> **Findings date:** 2026-08-30

**Library docs (preflight)** — no third-party deps in plan (pure internal Python in `tortoise/hosted_api.py` + `tortoise/pack_manifest_store.py` + tests).

> Gate skipped: plan touches zero third-party deps. In-repo precedent: the fail-closed import chain (#1230 plan Task 2 "order matters"), the #2028 pre-restore foreign-kind guard (same fix family — hoisted pre-restore exactly to keep retries convergent), the #2029 security review (manifest namespace charset/containment). External pattern (run during problem-diverge research, 2026-08-30): AWS builders-library / Well-Architected idempotent execution — a success marker is written only after all side effects complete and consulted before executing side effects; the current stamp-before-apply order is the documented anti-pattern.

### Integration Surface Map

| Surface Type | Specific Surface | Data Flow | Contract | Test Layer |
|---|---|---|---|---|
| Pure logic | `_pack_config_shape_error(pc)` legality matrix | In | str-error-or-None per pinned check order | Unit (`test_export_pack_config.py`) |
| Pure logic | `_validate_import_envelope` pack_config gate step | In | `_ImportVerifyError(err, payload_sha)` | Unit (via endpoint — envelope is crypto-wrapped; endpoint 422 tests cover both artifact forms) |
| API endpoint | `POST /v1/teams/{team_id}/import` shape rejection | In | 422 + `quarantined_import` audit + `last_import_quarantined_sha256` stamped + live graph untouched (`ids == []`) + `last_import_sha256` unstamped | Integration (`test_import_endpoint.py`, un-skipped no-`_seed_live_graph` pattern) |
| API endpoint | apply-failure retryability | In | 422 + ledger clear + re-import 422s again (never `already`) + fixed-artifact convergence (200 + stamped + `already`) | Integration (`test_import_endpoint.py`) |
| DB write (ledger props) | `last_import_sha256` / `last_import_quarantined_sha256` on Team node/row | Both | stamp-after-apply; clear-on-failure; allowlist `_IMPORT_LEDGER_PROPS` unchanged | Integration (FakeControlPlane PATCH observable via `fake.tables["teams"][0]`) |
| External (control plane) | Supabase PATCH / registry SET via `_stamp_import_prop` seam | Out | unchanged seam | Integration (existing harness) |
| Pure logic | `_apply_import_pack_config` guards + ns↔yaml pre-write guard | In | ValueError (422-class); no stray PackManifest/PackInstall on mismatch | Unit (`test_export_pack_config.py` sdk fixture) |
| Pure logic | `validate_manifest` RecursionError | In | `ManifestValidation(False, ["invalid YAML: nesting too deep"])` | Unit (`test_pack_manifest_store.py`) |
| Artifact forms | wire (header+blob) and CLI (`blob_b64` single JSON) | In | both funnel through `_validate_import_envelope`; CLI-form malformed case locks the shared path | Integration |

No Postgres-function business logic, no RLS, no UI. All surfaces are in-repo Python; integration layer weighted per the Testing Trophy (defects here cluster at the endpoint/ledger boundary).

### Implementation Tasks

### Task 1: Shared pack_config shape validator

**Intent:** Single source of truth for the pack_config legality matrix, used by both the envelope gate (pre-restore, fail-closed) and the apply defense (ValueError) so the matrix cannot drift between two code sites.

**Acceptance:** `_pack_config_shape_error(pc)` returns `None` for every legal family (absent, `None`, `packs: []`, well-formed incl. int `version`, `activated: "anything"`, unknown key, yaml absent/`None`) and a distinct error string for every malformed class; check order pinned.

**Files:**
- Modify: `tortoise/hosted_api.py` (new function next to `_validate_import_envelope`, ~7866)
- Test: `tests/test_export_pack_config.py` (new `TestPackConfigShapeGate` + module-level `MALFORMED_PC_CASES`)

**Step 1: Write the failing unit tests** — `MALFORMED_PC_CASES` (single-violation artifacts where possible): non-dict pc (`"x"`, `5`, `[1]`); schema_version missing / `2` / `"1"` / `True` / `1.0`; packs missing / `None` / `42` / `"junk"` / `("a",)`; entry `42` / `"x"` / `None`; ns absent / `""` / `" "` / `42`; yaml `""` / `0` / `False` / `[]` / `{}` / `42`. Each asserts a truthy error string containing `"pack_config"`. Legal cases assert `None`.

**Step 2: Run — expected FAIL** (`_pack_config_shape_error` undefined).

**Step 3: Implement** `_pack_config_shape_error(pc) -> str | None` in `hosted_api.py` — check order: (0) `if pc is None: return None` (absent/`None` pack_config is LEGAL — pre-v1.1 artifacts have no pack_config key; `payload.get("pack_config")` yields `None` for both absent and explicit-null); (1) `not isinstance(pc, dict)` → `"pack_config must be an object"`; (2) `not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version != 1` → `"pack_config schema_version must be 1"`; (3) `"packs" not in pc` → `"pack_config packs is required and must be a list"`; (4) `not isinstance(packs, list)` → `"pack_config packs must be a list"`; (5) entry not dict → `f"pack_config packs[{i}] must be an object"`; (6) `not isinstance(ns, str) or not ns.strip()` → `f"pack_config packs[{i}] must declare a non-empty string namespace"` (whitespace-only `" "` is malformed shape — the `:`/traversal charset rejection stays semantic at `validate_manifest` per #2029, deferred);(7) yaml present and not (None or non-empty str) → empty-str case `f"pack_config packs[{i}] yaml must be a non-empty string or null"`; non-str case `f"pack_config packs[{i}] yaml must be a non-empty string or null (got {type(yaml).__name__})"`. `version`/`activated`/unknown keys unconstrained; entry-level messages interpolate the concrete index + offending type.

**Step 4: Run — expected PASS.**

**Step 5: Commit** `feat(import): add shared pack_config shape validator (#2040)`.

### Task 2: Wire the gate into the envelope + endpoint 422 tests

**Intent:** Malformed pack_config 422s pre-restore (Indicator 1 — "ideally pre-restore via envelope validation"), live graph untouched, ledger unstamped — through the existing `_ImportVerifyError` → quarantine → 422 chain.

**Acceptance:** Endpoint returns 422 (never 500) with `"pack_config"` in the detail for malformed shapes, `quarantined_import` audit recorded, `_counts(db_path)["ids"] == []` (nothing landed), `last_import_sha256` unstamped, `last_import_quarantined_sha256` stamped; both wire and CLI artifact forms covered; existing `_check_foreign_kinds` 422 messages unchanged on well-formed shapes.

**Files:**
- Modify: `tortoise/hosted_api.py` (`_validate_import_envelope` final step ~7864; docstring chain list + module-header chain summary)
- Test: `tests/test_import_endpoint.py` (new `TestImportPackConfigShape422`)

**Step 1: Write the failing endpoint tests** (un-skipped no-`_seed_live_graph` pattern, mirroring `TestImportForeignKindsGuard`): parametrized over `{"packs":[42]}`, `{"packs":42}`, `pc="x"`, `{"schema_version":2,"packs":[]}` → 422 + `"pack_config" in detail` + audit + `ids == []` + ledger unstamped + quarantine stamped; plus one CLI-form case (`build_artifact`/`artifact_bytes`); plus one DUAL-FAULT case (malformed pc `{"packs": 42}` AND a foreign kind `pointKind: "tenant-ops:contract"`) → 422 with the SHAPE reason in detail (gate fires before the guard; locks the envelope-before-`_check_foreign_kinds` precedence the class-(b) taxonomy update rests on) + quarantine reason is the shape error.

**Step 2: Run — expected FAIL** (malformed pc currently 500s or 200s).

**Step 3: Implement** — call `_pack_config_shape_error(payload.get("pack_config"))` as the final `_validate_import_envelope` step; non-None → `raise _ImportVerifyError(err, payload_sha)  # noqa: B904` (payload_sha in scope — computed ~7849-7851). Update the envelope docstring chain (add step 7) and module-header summary. **Also update `_check_foreign_kinds`'s class-(b) docstring/comment**: malformed pack_config shapes (non-dict, non-list packs, non-dict entries, bad ns/yaml) are now rejected pre-restore by the envelope gate, so the guard's class (b) covers ONLY the legal empty-`packs: []` shape from the endpoint — the audit-reason taxonomy must not mislead operators.

**Step 4: Run — expected PASS** incl. no-regression (`TestImportForeignKindsGuard`, `TestImportHappyPath` unskipped cases stay green).

**Step 5: Commit** `feat(import): reject malformed pack_config pre-restore (422, graph untouched) (#2040)`.

### Task 3: Apply hardening — shared-validator guard + PRE-WRITE ns↔yaml check

**Intent:** `_apply_import_pack_config` cannot crash (AttributeError/TypeError → 500) or silently no-op on malformed input (drift-defense for direct callers), and a declared pack namespace that disagrees with its manifest's yaml namespace fails LOUDLY before any write (closes the crafted-artifact silent-200 class).

**Acceptance:** Every `MALFORMED_PC_CASES` entry raises ValueError through `_apply_import_pack_config` (single-validator drift-lock); ns↔yaml mismatch raises ValueError with a message naming both namespaces and leaves NO PackManifest/PackInstall residue; existing legal apply tests unchanged.

**Files:**
- Modify: `tortoise/hosted_api.py` (`_apply_import_pack_config` ~7928-7987; docstring)
- Test: `tests/test_export_pack_config.py` (`TestApplyImportPackConfig` extended)

**Step 1: Write the failing tests** — parametrize `MALFORMED_PC_CASES` → `pytest.raises(ValueError, match=<per-case shape-error string>)` on `_apply_import_pack_config(sdk, payload)` — the `match=` pins the SHAPE error message (e.g. "must be a non-empty string or null (got int)", "must declare a non-empty string namespace"), NOT a bare `ValueError`: several malformed cases (falsy yaml, non-str ns) already raise `ValueError("unknown starter pack ...")` today via the starter branch, so a bare raises() would pass vacuously pre-implementation; ns↔yaml mismatch (declared `"tenant-ops"`, yaml declaring a different namespace) → `pytest.raises(ValueError, match="does not match manifest namespace")`.

**Step 2: Run — expected FAIL** (malformed cases crash or silently no-op today).

**Step 3: Implement** — in `_apply_import_pack_config`: `pc = payload.get("pack_config")`; `if pc is None: _check_foreign_kinds(payload); return` (unchanged); `err = _pack_config_shape_error(pc); if err: raise ValueError(err)`. In the custom-manifest branch: `result = validate_manifest(yaml_text)` (needed for `result.namespace`); `if result.ok and result.namespace != ns: raise ValueError(f"pack_config pack namespace {ns!r} does not match manifest namespace {result.namespace!r}")` BEFORE `upsert_tenant_manifest(sdk, yaml_text)`. The ok-ness check is deliberately NOT duplicated: `upsert_tenant_manifest` re-validates unconditionally before any graph write with the identical message + zero residue, so the guard adds only the ns↔yaml comparison (accepted double-validate cost: one tempdir+PackRegistry parse per custom pack — small vs the restore/swap). Add `validate_manifest` to the existing function-local `from tortoise.pack_manifest_store import ...` import. Update the docstring (defense role).

**Step 4: Run — expected PASS.**

**Step 5: Commit** `feat(import): fail-loudly apply guards + pre-write ns/yaml consistency (#2040)`.

### Task 4: validate_manifest RecursionError catch

**Intent:** A deeply-nested payload-controlled yaml raises `RecursionError` (not `YAMLError`) → escapes `except ValueError` → 500 post-swap. Catch it as a clean validation failure (422-class) — completes Indicator 1's "must 422, not 500" for the yaml-content class, and hardens the #2029 upload path identically.

**Acceptance:** `validate_manifest` with deeply-nested yaml returns `ManifestValidation(False, ["invalid YAML: nesting too deep"])` — never raises; existing valid/invalid YAML cases unchanged.

**Files:**
- Modify: `tortoise/pack_manifest_store.py` (~116)
- Test: `tests/test_pack_manifest_store.py`

**Step 1: Write the failing test** — genuinely nested flow mapping `"{" * 1500 + "}" * 1500` (empirically raises `RecursionError` through `validate_manifest` today — the `"a:" * 1000` form raises `ScannerError`, a `YAMLError` subclass the existing handler already catches, so it would pass vacuously) → no exception, `not result.ok`.

**Step 2: Run — expected FAIL** (RecursionError escapes).

**Step 3: Implement** — wrap `yaml.safe_load(manifest_yaml)` in `except RecursionError` → `ManifestValidation(False, errors=["invalid YAML: nesting too deep"])`.

**Step 4: Run — expected PASS.**

**Step 5: Commit** `fix(import): catch RecursionError in manifest yaml parse (422-class, not 500) (#2040)`.

### Task 5: Stamp reorder + ledger clear on pack failure + retry endpoint tests

**Intent:** The idempotency ledger stamps only fully-applied imports; a pack failure leaves the ledger clear so same-artifact retry and rollback-to-prior-artifact both converge (Indicator 2 — "quarantine-then-retry, or stamp after successful pack application").

**Acceptance:** Endpoint tests: invalid-manifest → 422 "invalid YAML" + `last_import_sha256` NOT stamped (cleared) + `last_import_quarantined_sha256 == sha` + swap landed (`_counts["ids"] == ["pt-0"]`) + re-import 422s again (never `already`) + fixed-manifest artifact (new sha) → 200 + stamped + re-import `already`; unknown-starter → 422 + ledger clear; deeply-nested-yaml artifact → 422 (not 500) + ledger clear (REQUIRES Task 4 — its 422 source is the RecursionError catch, not Task 3); rollback case: import A (200, stamped A) → import B broken (422, ledger CLEARED — assert `not fake.tables["teams"][0].get("last_import_sha256")` immediately after the 422, distinguishing clear-from-reorder-only) → re-import A → 200 `imported: true` (re-swap, not `already`); clear-path stamp-failure injection (monkeypatched `_stamp_import_prop` raising) → 422 not 500 + warning logged; sticky-quarantine case: fail-then-succeed on the same sha (e.g. transient apply failure then fixed env) → re-import returns `already: true` (quarantine prop cleared on success); registry-mode `_stamp_import_prop` clear test (SET path stores `""`); success-with-packs → 200 + stamped + `already`. Existing `TestImportIdempotencyAndSwapSafety` green.

**Files:**
- Modify: `tortoise/hosted_api.py` (`import_team` stamp move ~8252-8273; `#2040` comment; `import_team` docstring carve-out)
- Test: `tests/test_import_endpoint.py` (new `TestImportPackConfigApplyFailures`)

**Step 1: Write the failing endpoint tests** (un-skipped no-`_seed_live_graph` pattern; success/invalid-manifest fixtures MUST reuse `CUSTOM_MANIFEST` — declared ns `tenant-ops` == yaml `namespace: tenant-ops` — so Task 3's ns↔yaml guard does not trip the fixture for the wrong reason): `test_invalid_manifest_422_ledger_clear_retryable`, `test_unknown_starter_422_ledger_clear_retryable`, `test_deeply_nested_manifest_yaml_422_not_500`, `test_rollback_prior_artifact_after_pack_failure` (asserts ledger cleared to falsy after B's 422 AND re-import A returns `imported: true`), `test_clear_path_stamp_failure_still_422`, `test_same_sha_fail_then_success_returns_already` (quarantine cleared on success), `test_registry_mode_clear_stamps_empty` (registry-source `_stamp_import_prop` SET path stores `""` — direct unit test via `_registry_sdk()` on the temp DB, falling back to documenting if the registry harness proves finicky), `test_import_with_pack_config_success_ledger_stamped`.

**Step 2: Run — expected FAIL** (ledger stamped on pack failure today → re-import returns `already`).

**Step 3: Implement** — move `_stamp_import_prop(cp_source, team_id, "last_import_sha256", sha)` from before to AFTER the `_apply_import_pack_config` try/except (before the `team_import` audit; `cp_source` in scope), and immediately after it also clear the quarantine prop best-effort (`await asyncio.to_thread(_stamp_import_prop, cp_source, team_id, "last_import_quarantined_sha256", "")` wrapped in try/except → warning). **The success-path quarantine clear is REQUIRED**: `last_import_quarantined_sha256` is sticky (only `_quarantine_import` writes it, never cleared) — without the clear, a sha that was quarantined then succeeded would have BOTH props == sha, so the in-lock short-circuit's quarantine consultation would block `already` forever (every re-import re-swaps). In the `except ValueError` pack-failure block: quarantine (existing) + clear the success ledger (`await asyncio.to_thread(_stamp_import_prop, cp_source, team_id, "last_import_sha256", "")` wrapped in try/except → warning log) + raise 422. **Extend the in-lock idempotency short-circuit (~8173-8182)**: the `already` fast-path must ALSO require `fresh.get("last_import_quarantined_sha256") != sha` — a quarantined sha re-runs validation (422s with the real reason if still broken, or succeeds and clears), so a same-artifact retry stays retryable even if a clear failed (control-plane blip). (Success path unaffected given the quarantine clear above.) Replace the `#2040` comment with the new ordering rationale; add the post-swap semantic carve-out line to `import_team`'s docstring.

**Step 4: Run — expected PASS** incl. `TestImportIdempotencyAndSwapSafety` (ledger-stamped success, 503-swap, idempotent re-import).

**Step 5: Commit** `fix(import): stamp ledger after pack application; clear on pack failure (#2040)`.

### Task 6: Full verification + docs

**Intent:** Whole-suite verification, lint-clean, plan-review signature.

**Acceptance:** ruff clean on all touched files; `test_export_pack_config.py` + `test_pack_manifest_store.py` + `test_import_endpoint.py` green on the docker lane; plan doc carries the `<!-- plan-review:` signature; issue labels `scoped`/`planned`/`implementing` set.

**Files:**
- Modify: `docs/plans/2026-08-30-2040-pack-config-shape-ledger-order.md` (signature)

**Step 1:** `uv run ruff check tortoise/hosted_api.py tortoise/pack_manifest_store.py tests/test_import_endpoint.py tests/test_export_pack_config.py tests/test_pack_manifest_store.py`.

**Step 2:** `export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'` + `uv run pytest tests/test_export_pack_config.py tests/test_pack_manifest_store.py -q` + `uv run pytest tests/test_import_endpoint.py -q`.

**Step 3:** Append `<!-- plan-review: cycles=N, status=clean, version=2.3.0 -->` after the gate passes.

### Testing Strategy

| Layer | Test | Asserts |
|---|---|---|
| Shape gate unit | legal matrix parametrized | no error for the legal families |
| Shape gate unit | `MALFORMED_PC_CASES` parametrized | error string per class (check-order pinned, single-violation cases) |
| Apply guards | same cases → `_apply_import_pack_config` | `ValueError` (single-validator drift-lock) |
| Apply guard | ns↔yaml mismatch | `ValueError` pre-write; no residue |
| Endpoint 422 pre-restore | malformed ×4 + CLI form | 422 shape reason; `quarantined_import` audit; `ids == []`; ledger unstamped; quarantine prop stamped |
| Endpoint retry (Indicator 2) | invalid manifest / unknown starter / deeply-nested yaml | 422; ledger clear; quarantine stamped; re-import 422s again; convergence on fixed artifact; rollback-to-prior works |
| Endpoint success ordering | well-formed pc | 200; ledger stamped; quarantine cleared; re-import `already:true` |
| Endpoint clear-path failure injection | monkeypatched `_stamp_import_prop` raising during pack-failure block | 422 not 500; warning logged; retry still 422s (quarantine consultation) |
| Sticky-quarantine regression | fail-then-succeed same sha | re-import `already:true` (quarantine cleared on success) |
| No-regression | existing foreign-kinds guard, happy path, idempotency, ledger, 503-swap classes | unchanged messages/behavior |

### Verification Plan

```bash
# Docker lane (FalkorDB must be up):
# docker compose -f ../eldato/operations/memory/docker-compose.yml up -d
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run ruff check tortoise/hosted_api.py tortoise/pack_manifest_store.py tests/test_import_endpoint.py tests/test_export_pack_config.py tests/test_pack_manifest_store.py
uv run pytest tests/test_export_pack_config.py tests/test_pack_manifest_store.py -q
uv run pytest tests/test_import_endpoint.py -q   # pops TORTOISE_DB_URI itself (module fixture)
```

`test_import_endpoint.py` is an embedded-file-contract file (RAW_EMBEDDED_ALLOWLIST; module fixture deletes `TORTOISE_DB_URI`) — runs on both lanes. `test_export_pack_config.py` + `test_pack_manifest_store.py` are docker-lane (URI required). No export-side changes → `collect_pack_config` round-trip tests unaffected.

### Acceptance Criteria (mapped to the issue)

- **Indicator 1:** `pack_config: {"packs": [42]}` and non-list `packs` → **422 (never 500)**, pre-restore: live graph untouched (`ids == []`), `last_import_sha256` unstamped, quarantine stamped.
- **Indicator 2:** invalid manifest YAML / unknown starter → **422 + ledger clear, retryable** — re-import of the same artifact returns 422 with the real reason (never `{"already": true}`); fixed artifact converges (200 + stamped); rollback to a prior artifact re-swaps.
- **Verification Checklist (9 Export/import):** "malformed packs → 422 (not 500)" ✓; "invalid-manifest → 422 + ledger clear (retryable)" ✓.
- **Existing contract preserved:** the three `_check_foreign_kinds` messages fire unchanged on well-formed shapes; junk-tolerance unit tests untouched; `packs: []` legal; `version`/`activated`/unknown keys unconstrained; `already` response shape unchanged.

### Risks

- **Destructive retry on semantic failure (accepted, documented):** retry of a semantically-failed import re-swaps the live graph (data written between failure and retry is replaced by the artifact). #1230 convergence model; the 422 reason names the remediation. Applies equally to a post-apply stamp failure (deferred wrap): the retry re-swaps, replacing anything written in between.
- **H3 — pre-restore 422 removes the (buggy) content-restore escape hatch:** an operator with a corrupted live graph and only a broken artifact must remediate manually (decrypt with the export key → patch `pack_config` → recompute canonical `payload_sha256` → rebuild via `build_artifact`/`artifact_bytes`) or restore from a backup via `restore_backup` (same dump format, no pack application). Documented trade of the "ideally pre-restore" framing.
- **Validation drift:** eliminated by the single `_pack_config_shape_error` source of truth used by both gate and apply (check order pinned, single-violation test cases).
- **Forward-compat:** a future `schema_version: 2` exporter 422s until import ships lockstep support (deliberate fail-closed choice).
- **413-precedence note:** the envelope shape gate fires before the endpoint's 413 max_points check for dual-fault payloads (422 wins over 413 for the malformed-shape class — documented, not changed).
- **Ledger consumers ("" sentinel contract):** `last_import_sha256` now means "fully applied incl. vocabulary"; `""` is the deliberate cleared sentinel. Consumers: the in-lock idempotency re-read (~8174 — `""` falsy-safe; with the quarantine consultation a quarantined sha never short-circuits to `already`), the `/v1/team` additive-tier read (`supabase_control.py:97-109` — fail-soft, renders empty cell), dashboard columns (`test_dashboard_login.py:451-467` — degrade-tested). No consumer treats `""` as a valid sha.
- **Parallelizability:** Task 4 (pack_manifest_store.py + test_pack_manifest_store.py) shares zero files with Tasks 1-3 and can be implemented in parallel or first. Task 5's red-green does NOT require Task 3 (its 422 sources — upsert validation, unknown starter — predate it), BUT its deeply-nested-yaml test REQUIRES Task 4 (that 422 source is the RecursionError catch) — order Task 5 after Task 4. Only the success fixture must be ns-consistent (Task 5 Step 1).
- **Deferred success-path stamp wrap:** the moved success-path stamp remains unwrapped (pre-existing; a control-plane blip 500s after a fully-applied import with the ledger clear → fix-and-retry converges). Tracked as a documented deferral, not a #2040 deliverable.
<!-- plan-review: cycles=1, status=clean, version=2.3.0 -->

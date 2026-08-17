<!-- research-path: docs/scoping/2026-08-17-1230-export-import-scoping.md -->
<!-- plan-review: status=clean, reviewers=1 (fresh-context, 2026-08-17); 2×P0 fixed (key exchange → caller-supplied artifact key; graph_name isolation → import-mode override), P1s fixed (blob_sha256 pre-decrypt chain, shared temp→verify→swap helper refactor, owner-only auth), P2s folded into contract (streaming cap, pinned test name, rate-limit bucket location) -->

# Graph Export→Import Tool — Implementation Plan (Epic #1230)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Give self-hosters a first-class migration path — `tortoise export` produces a versioned, encrypted, portable artifact that hosted `POST /v1/import` ingests into a team graph, replacing manual replay and preserving Point IDs + edge topology.

**Team:** epistemic-team
**Role:** (unset)

**Architecture:** Wiring + surfacing the existing production-verified `tortoise/hosted_backup.py` engine (`dump_graph`/`restore_graph`, `tortoise-logical-dump-v1`). The CLI wraps `dump_graph()` output in a new versioned envelope (`tortoise-export-v1`) and encrypts by default (AES-256-GCM via existing `encrypt_backup`) with a **caller-supplied key** (env `TORTOISE_BACKUP_KEY` or an ephemeral key printed once at export). The hosted endpoint authenticates the team (owner-only, mirroring `GET /v1/teams/{team_id}/export`), enforces size/rate caps, validates the envelope (format/version/blob-sha256 — fail closed pre-decrypt, plaintext-sha256 post-decrypt), restores into a temp graph, verifies counts, then atomically swaps into the team graph (the temp→verify→swap stage is **extracted from `restore_backup` into a shared helper** so both paths share the guards: empty-guard, pre-restore safety copy, cross-team isolation; the storage-coupled manifest/R2 layer stays in `restore_backup`). Point IDs + edge topology preserved as props; EP recomputes server-side (derived, as today). No new third-party deps, no schema change.

### Pattern Research

> **Findings date:** 2026-08-17

> **Gate skipped: plan touches zero third-party deps — all in-repo reuse** (`hosted_backup.py` engine, `projection.py` graph resolution, existing hosted auth/quota/rate-limit middleware). Codebase-first precedent is the engine itself (production-verified in the hosted backup pipeline #305), and scoping's external pitfalls pass (MS Power Platform / GCP Cloud SQL / AWS Aurora migration whitepapers) already mapped every pitfall to an existing guard — envelope validation, temp-graph verify-before-swap, sha256 + AES-GCM, size/rate caps, pre-restore safety copy (scoping doc §External Research). No library version/API-surface/idiom questions to triangulate.

### Integration Surface Map

Derived from scoping's Wiring Check (all surfaces were enumerated there). Test layers assigned per surface:

| # | Surface | Test Layer | Expected Verification |
|---|---------|-----------|----------------------|
| S1 | `tortoise export` CLI entry (`tortoise/__main__.py`) | unit + e2e | Envelope shape, flags, encrypt-by-default, stdout JSON line, exit codes |
| S2 | Envelope schema `tortoise-export-v1` + sha256 | unit | Canonical serialization stable; tamper → verify fails |
| S3 | Encryption (AES-256-GCM reuse) | unit | Round-trip decrypt; wrong key fails closed; no plaintext graph content in clear header |
| S4 | `POST /v1/import` auth + caps + rate limit | integration | Wrong/foreign key 403; oversize 413; rate exceeded 429; owner-scoped |
| S5 | Envelope validation + restore-into-temp + verify + swap | integration | Valid import swaps atomically; dangling-edge dump quarantined (422), live graph untouched |
| S6 | Idempotency (sha256 ledger) | integration | Re-import of same envelope → 200 already-imported, no double-swap |
| S7 | Export→import parity E2E | e2e (full) | `tortoise_check_structure` counts + Point IDs + edge count match source (beats E2E-12-D baseline) |
| S8 | Docs (2 quickstarts + changelog) | docs | `npm run check:docs`-style link check; migration sections reference the tool |

Bug-pattern flags: (1) encryption-key mishandling — ephemeral key must be printed once, never persisted into the artifact; (2) envelope tamper → silent partial restore — sha256 verified BEFORE restore, fail closed; (3) oversized payload → OOM/DoS — size cap checked BEFORE decrypt; (4) double-import → duplicated graph — idempotency ledger; (5) verify-fail leaving partial state — restore into temp only, live untouched until swap; (6) CLI on embedded (no local FalkorDB) — `FalkorProjection` InMemory path fallback.

### Verification Plan

Domain complexity (from scoping): Architecture **high**, Security **high**, Ontology **low**, UX **low**.

- Unit layer: S1–S3 (export envelope, crypto round-trip, canonical sha256).
- Integration layer: S4–S6 (auth/caps/rate/idempotency/quarantine) via the hosted test harness (`tests/hosted/*` or api fixtures — follow the pattern used by E2E-6-D export tests).
- E2E layer: S7 — extend `tests/e2e/hosted/test_12_selfhost_migration.py` with the parity journey (full-depth e2e).
- Docs: S8 — quickstart link check in CI (existing check), manual review of migration sections.
- Deferred/none: research (no new research needed — engine precedent is codebase-first), content/config (no config changes beyond new constants).

**Baseline to beat:** E2E-12-D asserts content-presence only (replayed knowledge present on hosted). The parity journey must assert structure (node/edge counts), Point-ID survival, and edge topology — strictly stronger.

---

## Task 1: CLI `tortoise export` — envelope + encrypt-by-default

**Intent:** Self-hosters produce a portable, versioned, encrypted artifact from their local graph. This is the first half of the migration path (Indicator 1).
**Acceptance:** `tortoise export --output graph.tortoise` (with local FalkorDB) produces a file whose clear header is `{format: "tortoise-export-v1", artifact_version: 1, encrypted: true, algorithm: "AES-256-GCM", key_fingerprint, exported_at}` containing zero graph content; `--decrypt`-style verification (or import Task 2) recovers the full graph with identical counts. `--no-encrypt` exists but warns loudly. Stdout emits ONE JSON line (machine contract, like the index path). `tortoise_check_structure` on a restored copy matches source counts.
**Files:**
- Modify: `tortoise/__main__.py` (add `_cmd_export` + subparser in `main`)
- Modify: `tortoise/hosted_backup.py` (add envelope constants/helpers if not already present — reuse `DUMP_FORMAT`, `encrypt_backup`)
- Create: `tortoise/export.py` (envelope build + canonical sha256 + key handling)
- Test: `tests/test_export_cli.py`

**Step 1: Define the envelope schema (design contract — freeze before coding).**

```jsonc
// Clear header (no graph content — only metadata)
{
  "format": "tortoise-export-v1",
  "artifact_version": 1,
  "encrypted": true,
  "algorithm": "AES-256-GCM",
  "key_fingerprint": "<sha256 prefix of the key, 8 hex chars>",
  "exporter_version": "1.0.0",
  "exported_at": "<ISO-8601 UTC>",
  "source_surface": "selfhost" | "embedded",
  "blob_sha256": "<sha256 of the ENCRYPTED blob>" // header-integrity: verifiable pre-decrypt
}
// payload (encrypted): JSON of the inner envelope
{
  "format": "tortoise-export-v1",
  "payload_sha256": "<sha256 of the canonical plaintext payload>", // post-decrypt integrity
  "payload": {
    "format": "tortoise-logical-dump-v1",   // dump_graph() output — nodes/edges/props verbatim
    "dumped_at": "...",
    "graph_name": "<selfhost graph name — NOT matched server-side>",
    "node_count": N,
    "edge_count": M,
    "nodes": [...],
    "edges": [...]
  }
}
```

Design decisions (frozen): (1) outer `tortoise-export-v1` envelope wraps the inner `tortoise-logical-dump-v1` payload, so engine upgrades don't break the artifact contract; (2) `blob_sha256` in the CLEAR header = sha256 of the **encrypted blob** (verifiable pre-decrypt, same pattern as `create_backup`'s manifest) and a second `payload_sha256` INSIDE the decrypted payload = sha256 of the canonical plaintext (verifiable post-decrypt) — two-link integrity chain; (3) key resolution at export: `TORTOISE_BACKUP_KEY` env if set, else generate a fresh 32-byte key and print it once to stdout (never write to disk); the same key must be supplied at import time (see Task 2 — there is NO server-side per-team key material; the caller supplies the artifact key in the request, matching the ephemeral key printed at export); (4) canonical serialization = `json.dumps(payload, sort_keys=True, separators=(",", ":"))` — must be byte-stable for sha256.

**Step 2: Write the failing unit tests** (`tests/test_export_cli.py`): envelope shape/version; canonical-sha256 stability (same graph → same hash); encrypt-by-default (no plaintext graph content in clear header — assert `"nodes"` ∉ header); `--no-encrypt` warns; ephemeral key printed exactly once; embedded (InMemory) fallback path works.

Run: `uv run pytest tests/test_export_cli.py -v` — Expected: FAIL (module missing).

**Step 3: Implement `tortoise/export.py` + `_cmd_export`** (envelope build, key resolution, encryption, stdout JSON line, exit codes: 0 ok / 1 pre-walk or graph-unavailable). Register `tortoise export` subparser in `main` with flags: `--output/-o` (default `graph-<date>.tortoise`), `--no-encrypt` (warn), `--db` (override graph URI), `--json` (machine stdout, default on).

Run: `uv run pytest tests/test_export_cli.py -v` — Expected: PASS. Then `uv run pytest tests/test_backup.py tests/test_export_cli.py -v` — no regressions in the engine.

**Step 4: Commit** — `git add tortoise/export.py tortoise/__main__.py tests/test_export_cli.py && git commit -m "feat(export): tortoise export CLI — tortoise-export-v1 envelope, encrypt-by-default (epic #1230)"`

## Task 2: Hosted `POST /v1/teams/{team_id}/import` — key-scoped, caps, verify-before-swap, idempotency

**Intent:** Hosted ingests the artifact into a team graph — the second half of the migration path (Indicator 2). Security-critical: the endpoint accepts arbitrary graph content into a tenant graph.
**Acceptance:** An owner-authenticated team key can import a valid artifact (supplying the artifact key) → team graph node/edge counts + Point IDs match the artifact (verified via `tortoise_check_structure`); foreign-key/team 403; payload over cap 413 (enforced while streaming, not just Content-Length); rate-exceeded 429; tampered envelope → 422 + quarantine (audit logged, live graph untouched); re-import of the same plaintext sha256 → 200 `{"imported": false, "already": true}`; swap is atomic (crash mid-import leaves the old graph intact); `restore_backup` behavior unchanged (refactor regression-tested).
**Files:**
- Modify: `tortoise/hosted_api.py` (new route at `POST /v1/teams/{team_id}/import` + caps + quarantine; `_SENSITIVE_OP_LIMITS` already lives here at ~line 1628 — extend with `"import": 5`)
- Modify: `tortoise/hosted_backup.py` (extract the temp-restore→verify→swap stage of `restore_backup` into a shared helper `_restore_into_temp_verify_swap(...)`; import calls it directly with an explicit `graph_name_override` — `restore_backup` keeps its storage/manifest layer and delegates to the same helper)
- Test: `tests/hosted/test_import_endpoint.py` (harness matching E2E-6-D export tests)

**Step 1: Freeze the endpoint contract.**

```text
POST /v1/teams/{team_id}/import
Auth: tt_ API key scoped to {team_id} AND owner-only (mirror export's _require_owner — a
      full-graph overwrite must not be writable by any member key; fail closed: foreign/
      absent key → 403, no existence oracle). Audit event (team_import, actor_key, sha256).
Body: raw artifact bytes (Content-Type: application/vnd.tortoise.export.v1) +
      artifact key supplied by the caller (JSON field or X-Tortoise-Import-Key header —
      the key printed at export; there is NO server-side per-team key material)
Caps:  streaming byte cap <= _IMPORT_MAX_BYTES (constant, e.g. 64 MiB) — enforced WHILE
       reading the body (Content-Length alone is spoofable) → else 413
       artifact node_count <= team max_points (graph_size_cap) → else 413
       rate: _SENSITIVE_OP_LIMITS["import"] = 5 per hour per IP (mirror "export": 20) → else 429
Validation (fail closed, order matters):
  1. format == "tortoise-export-v1" and artifact_version == 1 → else 422
  2. blob_sha256 (clear header) matches computed hash of the received blob → else 422 (truncated/tampered)
  3. supplied key fingerprint matches header key_fingerprint → else 422 (wrong artifact key)
  4. decrypt blob with the supplied key → decrypt failure → 422
  5. payload_sha256 (inner) matches recomputed plaintext hash → else 422
  6. node_count/edge_count fields match len(nodes)/len(edges) → else 422
  7. idempotency: team node prop last_import_sha256 == payload sha256 → 200 {"imported": false, "already": true, "id": <sha>}
Restore (shared helper extracted from restore_backup — storage/manifest layer NOT involved):
  temp graph `{team}_import_{ts}_{rnd}` → restore_graph() → verify node/edge counts vs payload
  → empty-backup-over-live guard → pre-restore safety copy → delete live → GRAPH.COPY temp→live
  → cleanup → stamp team node last_import_sha256 = payload sha256 (same op as the swap, best-effort;
  documented crash-window: a crash between swap and stamp can double-import — idempotency is
  convergence, not strict-once)
  graph_name isolation: payload.graph_name is the SELFHOST graph name and is NOT matched
  against team_{team_id} — the explicit import-mode override (logged) is what makes the
  migration legitimate; cross-TEAM isolation is still enforced by auth (step Auth).
Quarantine on ANY verify/restore failure: 422 + audit event (quarantined_import, actor_key, sha256) +
  team node prop last_import_quarantined_sha256; live graph NEVER touched.
```

**Step 2: Write the failing integration tests** — happy path (valid artifact + correct artifact key → counts + Point IDs match via `tortoise_check_structure`); foreign key 403; non-owner key 403; oversize 413 (incl. a spoofed Content-Length that the stream cap still catches); rate 429; tampered blob (blob_sha256 mismatch) 422 + live graph unchanged + quarantine recorded; wrong artifact key 422; re-import idempotent 200 (no double graph); empty-backup 422; **regression suite for `restore_backup`** (refactor extracted the temp→verify→swap helper — existing backup-restore tests must pass unchanged); crash-mid-swap leaves old graph (simulate by pre-seeding a conflicting prop on the temp graph).

Run: `uv run pytest tests/hosted/test_import_endpoint.py -v` — Expected: FAIL (route missing).

**Step 3: Implement the endpoint** (route + caps + validation chain + restore reuse + quarantine + idempotency ledger). Thread-safe: restore runs in `asyncio.to_thread` like `export_team`.

Run: `uv run pytest tests/hosted/test_import_endpoint.py tests/hosted/test_backup*.py -v` — Expected: PASS (incl. restore_backup regression). Then the full hosted suite (`uv run pytest tests/hosted/ -v`) — no regressions (esp. existing export/backup tests).

**Step 4: Commit** — `git add tortoise/hosted_api.py tortoise/hosted_backup.py tests/hosted/test_import_endpoint.py && git commit -m "feat(import): hosted POST /v1/teams/{team_id}/import — owner-scoped, capped, verify-before-swap, idempotent (epic #1230)"`

## Task 3: E2E parity + docs

**Intent:** Prove the full migration path end-to-end at parity with (stronger than) the E2E-12-D baseline, and document it so self-hosters can find it (Indicators 3 + 4).
**Acceptance:** New parity test in `tests/e2e/hosted/test_12_selfhost_migration.py` passes: selfhost graph (points + operators + events) → `tortoise export` → import into fresh hosted team → `tortoise_check_structure` node/edge counts, Point IDs, and edge count all match source. Both quickstarts' migration sections reference `tortoise export` → import and downgrade the "no automated import today" caveat.
**Files:**
- Modify: `tests/e2e/hosted/test_12_selfhost_migration.py` (add parity journey)
- Modify: `docs/quickstart-selfhosted.md` §7, `docs/quickstart-cloud.md` §6
- Modify: `CHANGELOG.md`
- Test: the parity test itself (`test_parity_export_import` — pinned name, referenced by the `-k parity` selector) + docs link check

**Step 1: Write the failing parity E2E** (`test_parity_export_import`) — build a selfhost graph with ≥3 points (one with a Point ID assertion) + ≥1 operator + ≥1 edge; run `tortoise export` (subprocess); register fresh hosted team; `POST /v1/teams/{team_id}/import` with the artifact key; assert:
- `tortoise_check_structure` node count == source node count, edge count == source edge count
- every source Point ID present in hosted graph (survives round-trip)
- edge count via `MATCH ()-[r]->() RETURN count(r)` == source edge count
- operator node + its edge to the point survive (topology), Point IDs of the operator unchanged

Run: `uv run pytest tests/e2e/hosted/test_12_selfhost_migration.py -v -k parity` — Expected: FAIL (endpoint not yet present at task time — TDD). Note: embedded/FalkorDBLite surface is the `tortoise export` source for the E2E — `dump_graph` requires a `g.query`-capable handle (the InMemory fallback terminology in Task 1 refers to the FalkorDBLite embedded path, not a pure-Python stub).

**Step 2: Wire the E2E to the real endpoint** (once Task 2 merges) — pass. **Step 3: Update docs** (both quickstarts + changelog; downgrade caveat, reference the tool; keys-are-not-portable note stays). **Step 4: Commit** — `git add tests/e2e/hosted/test_12_selfhost_migration.py docs/quickstart-selfhosted.md docs/quickstart-cloud.md CHANGELOG.md && git commit -m "test(e2e): export→import parity journey + quickstart docs (epic #1230)"`

---

## Rollout

Order matters: **Task 1 → Task 2 → Task 3** (each child issue depends on the previous). No schema migration, no new env vars required (artifact key is caller-supplied — no server-side key material to provision), no feature flag — the import endpoint is opt-in by owner-auth scoping. Deploy via the standard hosted deploy pipeline; E2E-12 parity + existing E2E-6-D export tests + the `restore_backup` regression suite are the merge gate. Post-deploy smoke: import a small fixture artifact into a scratch team and run `tortoise_check_structure`. Rollback: the pre-restore safety copy + temp-graph design means a failed import never damages a live graph; endpoint removal is a route revert.

## E2E-12-D Baseline Reference

Existing test `tests/e2e/hosted/test_12_selfhost_migration.py::test_migration_journey_selfhost_to_hosted` replays knowledge via the ingest path and asserts content-presence only (`set(knowledge) <= hosted`). The parity journey in Task 3 is strictly stronger: structure counts + Point-ID survival + edge topology.

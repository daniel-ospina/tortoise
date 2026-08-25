<!-- research-path: docs/plans/1708-key-mint-idempotency/scope.md -->

# #1708 — Signup/Key-Minting Idempotency (Client-side) + Key Visibility — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make `tortoise signup` idempotent client-side (reuse a validated stored key, stable persisted device_id, global `~/.tortoise/credentials.json`, `--force` escape hatch, crash fixes) and expose `created_via`/`expires_at` through `GET /v1/team/keys` in both lanes so the dashboard renders session keys from API data instead of a prefix heuristic.

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** No server-side behavior changes (the `agent_signup` handler is untouched — `#741(a)` stays; that's #1709's territory). Client-side: one shared config resolver in `tortoise/__main__.py` (precedence env → cwd → global) replaces the four duplicated `cwd/.tortoise` read sites; `_cmd_signup` gains reuse-before-mint (validate via existing `GET /v1/team` pattern from `_cmd_init`), writes credentials atomically to `~/.tortoise/credentials.json` (0600, dir 0700), and persists `device_id` there. Read-side: `list_api_keys` adds two additive response fields — Supabase lane via an extended `team_api_keys` select list (in-repo seam), registry lane via an extended `MATCH` `RETURN` with None-tolerant field access (registry APIKey nodes don't carry the props until #1709 writes them). Dashboard: replace the `isSessionKey` prefix-match heuristic with `created_via === 'bootstrap' || expires_at`.

**Note on scope vs scope.md:** `docs/plans/1708-key-mint-idempotency/scope.md` (pre-verification revision) lists "registry lane sets these props at mint time" under B7. The **approved scope (user + controller) explicitly defers registry APIKey mint prop-writes to #1709** — this plan implements the None-tolerant RETURN only (zero changes to the `agent_signup` mint path). Precision on current state: the registry `session_key` mint (hosted_api.py:7250+, `created_via='bootstrap'/'recovery'` + 24h `expires_at` for bootstrap) **already** writes both props; the registry `create_api_key` mint (L3653) and the `agent_signup` registry mint (L6946) **do not** — those nodes return None until #1709. Test that the server mint path stays untouched: `tests/test_agent_signup.py` is **byte-for-byte unchanged**.

---

### Pattern Research

> **Findings date:** 2026-09-02
> **Gate skipped:** plan touches zero third-party deps — CLI changes are Python stdlib (`urllib`, `pathlib`, `json`, `os`, `uuid`) against in-repo API endpoints (`GET /v1/team` validation + `POST /v1/agent/signup`, both already tested); the Supabase lane change extends an existing in-repo seam (`supabase_control.team_api_keys` select list, exercised via `tests/fake_control_plane.py`); the dashboard change is a predicate swap in the existing React 19 app (no new library, no new usage pattern — same field reads as `k.revoked_at`/`k.created_at` already in `main.jsx`).

---

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | `~/.tortoise/credentials.json` (new global config file) | Data (file) | Write (signup) / Read (resolver) | Unit (CLI, mocked urlopen) | `{"api_key", "api_url", "team_id", "team_name", "device_id"}`; 0600 perms; `~/.tortoise` dir 0700; unique tmp name | IsADirectoryError on `~/.tortoise` dir (the bug); OSError between mint and save → orphan key (must echo key + exit 1); concurrent writers clobbering a shared tmp name → unique tmp; `./.tortoise`-is-a-dir on read path |
| 2 | `TORTOISE_API_KEY` env + `TORTOISE_API_URL` env | Config | Read | Unit (CLI) | env wins over file configs; **empty/whitespace env key is skipped** | bad env key shadows a good stored key (without `--force` no escape) → warn + `--force` hint; empty-string env key → lockout (resolver must treat as unset); mint target must derive from the validated config's URL, not ambient env, when re-minting |
| 3 | `GET /v1/team` (key validation) | External API (in-repo endpoint) | Out | Unit (CLI, mocked urlopen) | 200 = valid; 401 = invalid → re-mint; 403 SUSPENDED → fail-closed exit 1 (no mint); 403 non-suspended = invalid → re-mint; 429/5xx/URLError/200-garbage = cannot-validate → fail-closed exit 1 (no mint, no orphan) | network down (must NOT mint — that's the incident pattern); 401 on revoked stored key; 403 with `_suspended_detail()` body (suspension must not mint); 200 HTML body (captive portal) hits the JSONDecodeError leg; 401-then-429 dead end (stored key dead + budget spent → message must say both) |
| 4 | `POST /v1/agent/signup` (mint) | External API (in-repo endpoint) | Out | Unit (CLI, mocked urlopen) | existing response shape `{key, team_id, team_name, graph_name, identity, tier}` | 429 per-IP limiter (existing handling kept); config-save failure AFTER mint (must not orphan — echo key + exit 1); re-mint host divergence (mint to the validated config's base URL) |
| 5 | `GET /v1/team/keys` Supabase lane | External API + DB (api_keys via seam) | Out/In | Integration (`test_hosted_api.py` + FakeControlPlane; `test_supabase_control.py`) | additive fields `created_via`, `expires_at` in response; `team_api_keys` select adds both columns | `created_via`/`expires_at` absent from seeded rows (PostgREST 400 via fake `missing_columns` → seam fails closed) |
| 6 | `GET /v1/team/keys` registry lane | DB (FalkorDB APIKey nodes) | In | Integration (`test_hosted_api.py`, registry env) | `MATCH` `RETURN` adds `k.created_via, k.expires_at` at row idx 5/6; None-tolerant for agent_signup + create_api_key-minted nodes pre-#1709 (session_key mints already carry props) | row index drift breaking `row[0..4]` readers; None values must serialize as JSON null (additive, non-breaking); None-tolerance must be exercised by an agent_signup-minted key (create_api_key/session_key have or lack props differently); **lane must be pinned TORTOISE_CONTROL_PLANE=registry — exported Supabase creds would silently run the Supabase branch** |
| 7 | `website/apps/dashboard/src/main.jsx` keys table | UI (React 19) | In | Unit (`node --test` on extracted pure `isSessionKey`) + build + code-review | `isSessionKey(k, activeKey)`: API-first (`created_via === 'bootstrap' \|\| expires_at`), active-key prefix fallback ONLY when `created_via` is absent (stale cache / registry pre-#1709) | stale cached responses (fields absent) must NOT enable revoke of the live session key → fallback keeps the old guard; older bootstrap keys now uniformly classified session (intended — removes the only UI cleanup path for stale session keys, expiry/sweep is the cleanup); mid-rollout window where the server doesn't send fields yet |
| 8 | Existing CLI test determinism (HOME-dependent) | Test infra | — | Unit | CLI tests must not read the developer's real `~/.tortoise/credentials.json`; test working tree must not contain a stray `./.tortoise` file | reuse-path fires on a dev machine with a real global config → mint-path tests fail non-deterministically → HOME isolation required; a repo-root `.tortoise` file in the test CWD would flip local-mode tests (guard with tmp cwd + HOME isolation)

### Bug Pattern Flags

- **Silent function skips** (reuse-before-mint short-circuits the mint): verify via urlopen-call **counting** (`mock.patch` call counter / `assert_not_called`), not just exit code — a test that only asserts rc 0 would pass even if the mint ran.
- **Conditional guards** (401/403 vs 429/5xx vs network on validation; `--force` vs reuse): boundary tests per branch — both sides of each guard.
- **Race/concurrency**: two concurrent `signup` runs — both validate "no config" then both mint. Documented as a known limitation (client-side file race; server-side dedupe is #1709). Do NOT add a file-lock in this issue (YAGNI; the per-IP limiter bounds it; atomic replace prevents corruption).

### Checklist Notes

- Atomic write: tmp file + `os.replace` in the same dir, chmod 0600 before replace → a crash never leaves a corrupt `credentials.json` that would silently re-mint (the exact orphan class this issue fixes).
- Additive API contract: new response fields only — no consumer breaks (verify with the existing `test_list_keys_has_expected_fields` subset assertions).
- `test_agent_signup.py` unchanged — the server mint path is out of scope.

---

### Journey Test Map

### Journey: First-run signup from `$HOME`
1. **Step:** `tortoise signup` from `~` → **Acceptance:** mints once, no `IsADirectoryError`, writes `~/.tortoise/credentials.json` (0600, dir 0700) → **Test:** `tests/test_cli_signup.py::test_signup_from_home_no_crash`
2. **Step:** run `tortoise signup` again → **Acceptance:** reuses, exits 0, `urlopen` never called (0 new keys) → **Test:** `test_reuse_global_config_skips_mint`
3. **Step:** run from a different CWD → **Acceptance:** finds global config, reuses → **Test:** `test_reuse_from_other_cwd`

### Journey: Key rotation / recovery
1. **Step:** stored key revoked → **Acceptance:** validation 401 → auto re-mint → **Test:** `test_reuse_invalid_key_remints`
2. **Step:** bad `TORTOISE_API_KEY` env → **Acceptance:** `--force` mints fresh and warns the env key shadows it → **Test:** `test_force_mints_despite_existing`

### Journey: Dashboard key table
1. **Step:** session key listed → **Acceptance:** renders "ephemeral · session", cannot be toggled/revoked → **Test:** no harness — build + code-review (see Task 5)

### Failure Modes
- API unreachable during validation → **Expected behavior:** fail-closed exit 1 with `--force` hint (no mint, no orphan) → **Test:** `test_reuse_validation_network_fail_closed` / `test_reuse_validation_5xx_fail_closed` / `test_reuse_validation_200_garbage_fail_closed`
- `./.tortoise` is a directory (some repos use it) → **Expected behavior:** treated as no-config, next candidate wins → **Test:** `tests/test_cli_resolver.py::test_dot_tortoise_dir_skipped`
- Suspended team's stored key 403s → **Expected behavior:** fail-closed exit 1 with the suspension message, NO mint → **Test:** `test_reuse_suspended_403_no_remint`
- Mint succeeds but the global write fails → **Expected behavior:** key echoed on stderr, exit 1, no silent re-mint → **Test:** `test_mint_write_failure_echoes_key_exits_1`
- `created_via`/`expires_at` missing from registry nodes (pre-#1709) → **Expected behavior:** JSON null in response, dashboard treats as durable → **Test:** `tests/test_hosted_api.py` registry-lane field-presence test

---

**Tech Stack:** Python 3.12 (stdlib only for CLI), FalkorDB registry (registry lane), Supabase via `supabase_control` seam + `tests/fake_control_plane.py`, React 19 + Vite 6 (dashboard), pytest (docker lane / embedded carve-out).

### UX Design Decisions

UX gate skipped (`UX_RATING = low` per scope). Decisions recorded for the implementer:

| # | Decision Type | User Choice | Rationale |
|---|---|---|---|
| 1 | CLI messaging (reuse) | Print the config **source** (`~/.tortoise/credentials.json` / `./.tortoise` / `TORTOISE_API_KEY`) in the reuse message | Users need to know which key is in use to debug env-vs-file confusion |
| 2 | CLI messaging (validation failure) | Fail-closed with a `--force` hint | Prevents re-creating the orphan-key incident when the API is unreachable |
| 3 | Dashboard status cell | Keep the existing `ephemeral · session` / `active` / `revoked` vocabulary; only change the predicate | No copy/UX change beyond removing the fragile heuristic |
| 4 | Resolver precedence (D1) | env → cwd → global, pending user/controller ratification of the divergence from the scope's literal env → global → cwd | "cwd wins for legacy projects" parenthetical; strictly safer for legacy projects |

**Pending:** ONE open approval item — D1 precedence `env → cwd → global` vs the approved scope's literal `env → global → cwd` (see D1 ⚠️ sign-off item). Ratify at plan-review approval before implementation.

### Verification Plan

**Domain(s):** code, ux (light)
**Complexity:** Architecture=standard, UX=low, Ontology=low, Config=low

| # | Skill | Depth | Reason |
|---|-------|-------|--------|
| 1 | @test-writing | standard | TDD steps per task (red-green-refactor) |
| 2 | @test-integration | standard | `list_api_keys` both lanes via `test_hosted_api.py` + FakeControlPlane (Supabase) + registry env |
| 3 | @ux-verification | low (component catalog) | Dashboard predicate swap; no test harness exists → `npm run build` + code-review |
| 4 | @code-review | standard | PR gate via `commit-workflow` |
| 5 | node --test (pure predicate) | unit | AC6 unit/component check — extracted `src/sessionKey.js` (D8) |

**Skipped:** e2e (no browser harness for dashboard; UI change is a pure predicate), pgTAP (no Postgres business-logic change — `api_keys` column reads only), architectural-soundness (no schema/architecture change).

---

## Design Decisions

### D1 — Resolver precedence: env → cwd → global ("cwd wins for legacy projects")
The scope text states "precedence env → global → cwd (cwd wins for legacy projects)". The parenthetical governs the global-vs-cwd tie: `./.tortoise` is an explicit per-project pin (written deliberately by `init --api-key`), and a global default silently overriding it would switch which team a legacy project talks to (data-integrity footgun). **Effective precedence: `TORTOISE_API_KEY` env → `Path.cwd()/.tortoise` → `~/.tortoise/credentials.json`, first-found-wins.** Legacy projects (cwd-only) are unaffected; the two orders differ only in the both-exist case, where cwd-first is strictly safer. The reuse-before-mint path uses the same resolver (running `signup` inside a legacy project reuses the project key — no new key).

> ⚠️ **Explicit sign-off item (scope-divergence):** the approved scope's literal enumeration is `env → global → cwd`; this plan implements `env → cwd → global` per the "cwd wins for legacy projects" parenthetical. The divergence is documented, tested, and strictly safer, but it is a reading of an internally contradictory sentence in an approved contract — the user/controller should ratify the precedence at plan-review approval. If they prefer the literal order, only D1, the resolver order in Task 1, and `test_cwd_wins_over_global` change.

### D1b — `_cmd_context` deviation: env EXCLUDED for the context command
`_cmd_context` is the one converted command with a **local-mode fallback** (no config → `TortoiseSDK.session_context()` for the Claude Code SessionStart hook). `TORTOISE_API_KEY` is commonly exported in dev shells (`serve --http --auth static`, stdio-MCP guard at L4134), so an env-first resolver would silently flip `tortoise context` from local-memory digest to hosted mode in those shells — a silent backend switch for a documented consumer. **Deviation (explicit):** `_cmd_context` calls the resolver with `include_env=False` (file candidates only: cwd → global). Global-config presence still flips it to hosted (intended per scope A6 — a machine that ran `signup` has a hosted identity); env alone does not. This is the GOOD>EASY choice: the env-exclusion is one parameter and preserves a documented consumer's semantics.

### D2 — Reuse validation outcome mapping
`GET /v1/team` with the found key (same pattern `_cmd_init` uses, `timeout=10`):
- **200** → reuse: print source + "already have a key", exit 0, **0 new keys**. (Note: `GET /v1/team` is NOT per-IP limited — reuse never consumes the 2/24h signup budget; verified the limiter attaches only to `POST /v1/agent/signup`.)
- **401** → key invalid → print "stored key invalid (401) — minting fresh", set `reminting_after_401 = True`, fall through to mint (acceptance criterion 3). Mint target = **the validated config's base URL** (host-consistency, D1), not the ambient env/default.
- **403** → parse the body with the existing `_suspended_info` helper (`__main__.py:794`): **SUSPENDED** (`detail.code == "SUSPENDED"`) → fail-closed exit 1 with the suspension message + appeal URL, **NO mint** (a suspended team must not be silently orphaned by a fresh anonymous mint — every other `_cmd_*` team command already handles this, e.g. L982/L1048/L1120). **Non-suspended 403** → key invalid → re-mint (mirrors `_cmd_init`'s `key_rejected`; both branches tested).
- **429/5xx/URLError/TimeoutError/JSON-decode failure (incl. 200-with-garbage-body)** → cannot-validate → **fail-closed exit 1** with message + `--force` hint. The except tuple MUST include `TimeoutError`/`OSError` (a `socket.timeout` from `resp.read()` after headers arrive — flaky proxy/captive-portal stall — is not a `URLError`; without it the D2 contract degrades into a raw traceback). Rationale: minting on an unvalidatable existing key risks an orphan duplicate (the incident pattern). Never mint on an unvalidatable existing key.
- **401-then-429 (revoked key + exhausted 2/24h budget):** when the re-mint POST 429s after a 401-triggered re-mint (`reminting_after_401`), the rate-limit message must ALSO mention that the stored key is invalid — a user with a dead key must not be told to "wait" as if nothing else is wrong.
- **Mint-POST 200-with-garbage-body (proxy/mitm):** the existing mint handler prints "Cannot reach API" on JSON-decode failure, but the server DID mint — that message actively misleads the user into retrying (the double-fire pattern). Change the mint POST JSON-decode failure message to: "A key may have been minted but the response was unreadable — check the dashboard or support before re-running; do NOT blindly retry." (covers both the validation and mint legs).

### D3 — `--force` semantics
Skips reuse-before-mint **and** validation entirely → mints fresh → writes global. If `TORTOISE_API_KEY` is set, print a warning that the env key shadows the new key at read time (env wins per D1). `--force` does NOT unset or edit the env.

### D4 — Global credentials file
Path `Path.home() / ".tortoise" / "credentials.json"`. Dir: `mkdir(parents=True, exist_ok=True)` then `os.chmod(dir, 0o700)` unconditionally (the data home already stores `tortoise.db` + audit logs; private by design). Write: create a **unique per-writer tmp** file **born at 0600** via `os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` + `os.fdopen` — `Path.write_text` would create at umask (typically 0644) with a plaintext-key window before the chmod, and a crash in that window leaves key material world-readable; `os.replace(tmp, credentials.json)` — atomic, last-writer-wins. Content: `{api_key, api_url, team_id, team_name, device_id}`.
**Stale-tmp hygiene:** on each successful write, sweep `credentials.json.tmp-*` files in `~/.tortoise` (a crashed writer leaves one behind; unbounded accumulation = key-material residue).
**Write-failure handling (the orphan class):** wrap the mkdir/chmod/write/replace block in `try/except OSError` → on failure print to stderr: a key WAS minted but could NOT be saved to `{path}` (`{err}`); **echo the minted key** so it is not lost; tell the user to fix the path or store the key manually; exit 1. Never exit 0 with an unsaved key, never re-mint silently.

### D5 — Read-path dir guards
In the resolver, every candidate path is read only `if path.is_file()`: a directory at `cwd/.tortoise` (some repos use `.tortoise/` as a dir) or a directory at `~/.tortoise/credentials.json` is skipped as "no config here". `~/.tortoise` being a directory is by design — the config is the `credentials.json` *file inside it*.

### D6 — Shared resolver shape + corrupt-config semantics
```python
class _ConfigError(Exception):
    """Candidate config file exists but is corrupt or unreadable."""

def _resolve_config_path(include_env: bool = True) -> tuple[Path | None, dict | None, str | None, str | None]:
    """env → cwd/.tortoise → ~/.tortoise/credentials.json; first with a non-empty api_key wins.
    Returns (config_path, config, api_key, api_url) or (None, None, None, None).
    INVARIANT: whenever api_key is not None, config is a dict (env candidate is
    synthesized as {"api_key": key, "api_url": url} — callers like _cmd_team_keys_list
    do config.get(...) unconditionally and must never see None).
    - Empty/whitespace env TORTOISE_API_KEY (.strip()) is treated as unset (prevents a lockout shadow).
    - A candidate file that exists but fails JSON parse / is unreadable / has a
      non-string api_key raises _ConfigError(path) (catch (OSError, JSONDecodeError, TypeError)).
    - include_env=False: file candidates only (used by _cmd_context, D1b)."""
```
- `_read_config(json_mode)` becomes a thin wrapper preserving its exact 3-tuple contract + `_cmd_fail` messages (4 callers: `_cmd_team_info`, `_cmd_team_keys_{list,create,revoke}`); `_ConfigError` maps to the existing `_cmd_fail(json_mode, "no_config", "Invalid config at {path}: ...")` shape. The no-config message keeps the pinned substring `"Run 'tortoise init --api-key <key>' first"` contiguous (tests/test_cli_team_keys.py L88/L167 pin it): `"No .tortoise config found. Run 'tortoise init --api-key <key>' first, or run 'tortoise signup' for a free hosted key."`
- **Per-site `_ConfigError` handling (all converted sites, not just signup):** `_cmd_create_point` and `_cmd_session` → print `"Invalid config at {path}"` + return 1 (preserves today's clean corrupt-config behavior); `_cmd_context` → print a warning to stderr and **fall back to local mode** (preserves today's graceful degradation — the SessionStart hook must never traceback on a corrupt global config; test this); `_cmd_signup` → stderr `config at {path} is corrupt or unreadable — fix or delete it, or use --force` → exit 1, never mint.
- Inline sites converted: `_cmd_create_point` (L1167-1184), `_cmd_context` (L1231-1240, `include_env=False`), `_cmd_session` (L1316-1330).
- **Explicitly NOT converted (documented):** `_cmd_init` (L248 write + already-connected read — deliberate per-project connect flow, its own semantics), `_cmd_serve_http` (L3717 reads `args.api_key or TORTOISE_API_KEY` only — no cwd config read; unchanged). MCP config writers (`_write_mcp_config_file`/`_print_mcp_configs`) receive the key explicitly from `init` — no config read of their own.

### D7 — `list_api_keys` additive fields
- Supabase lane: extend `team_api_keys` select to `["id", "key_prefix", "created_at", "last_used_at", "revoked_at", "enabled", "created_via", "expires_at"]`; response adds `"created_via": row.get("created_via"), "expires_at": row.get("expires_at")`.
- Registry lane: `RETURN k.id, k.key_prefix, k.created_at, k.last_used_at, k.revoked_at, k.created_via, k.expires_at` → `"created_via": row[5], "expires_at": row[6]`. **None-tolerant for the registry mints that omit the props until #1709 — `agent_signup` (L6946) and `create_api_key` (L3653); the `session_key` mint (L7250+) already writes them.** Existing `row[0..4]` readers unchanged.
- Additive only — no consumer breaks.

### D8 — Dashboard predicate (API-first, active-key self-revocation guard)
```js
// extracted to src/sessionKey.js — pure, unit-testable with node --test
// (k: key row, activeKey: the current session's plaintext key, or null)
export function isSessionKey(k, activeKey) {
  if (!k || k.revoked_at) return false
  if (k.created_via === 'bootstrap' || !!k.expires_at) return true
  if (k.created_via == null) {
    // API fields absent (stale cached responses / registry lane pre-#1709):
    // keep the old active-key guard so the live session key can't be revoked
    return !!activeKey && (k.key_prefix === String(activeKey).slice(0, 10))
  }
  return false // durable (created_via 'provisioned'/'recovery'/etc.)
}
// separate guard — the live data-plane key must NEVER be revocable from the
// UI, even when it is a durable key (created_via 'provisioned'):
export function isActiveKey(k, activeKey) {
  return !!activeKey && !k.revoked_at && (k.key_prefix === String(activeKey).slice(0, 10))
}
```
`main.jsx`: status rendering uses `isSessionKey(k, active)`; the toggle/revoke guard (L2883) becomes `!isSessionKey(k, active) && !isActiveKey(k, active)` — the old heuristic protected the active key by prefix regardless of kind (Fix A comment: "so revoke can tell whether the active data-plane key is being revoked"); the API-first predicate alone would make a durable active key revocable (self-lockout). `teamKeysRef`/`currentTeamId` stay (used by loadAll/restore for the real key value).

### D9 — Test determinism (HOME isolation)
The reuse path reads the developer's real `$HOME/.tortoise/credentials.json`; CLI tests that exercise the mint/no-config path would break non-deterministically on machines with a real global config. Every affected CLI test class gets an autouse HOME isolation (`monkeypatch.setenv("HOME", str(tmp_path))`). This is a **test-only** change; `tests/test_agent_signup.py` stays untouched.

---

## Tasks

> Test commands (per AGENTS.md — docker lane is the default):
> `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest <files> -v`
> Embedded carve-out (URI-less): `TORTOISE_TEST_CARVE_OUT=1 uv run pytest <files> -v`

### Task 1: Shared config resolver + call-site conversion

**Intent:** One canonical "where is my key?" answer for the CLI (env → cwd → global), killing the four duplicated `cwd/.tortoise` reads and making the global credentials store reachable from every hosted command — the foundation Tasks 2–3 build on.
**Acceptance:** All four enumerated read sites (L760 `_read_config`, L1167 `_cmd_create_point`, L1231 `_cmd_context`, L1316 `_cmd_session`) resolve through one helper with precedence env → cwd → global; `./.tortoise`-is-a-dir is skipped; existing CLI suites green with HOME isolation in place; `tortoise create-point/context/session/team` work from a global config.
**Files:**
- Modify: `tortoise/__main__.py:748-779` (`_read_config` → resolver wrapper), `tortoise/__main__.py:1159-1184`, `tortoise/__main__.py:1218-1240`, `tortoise/__main__.py:1308-1330`
- Test: `tests/test_cli_team_keys.py`, `tests/test_cli_context.py`, `tests/test_cli_claim.py`, `tests/test_cli_signup.py` (HOME isolation)

**Step 1: Write the failing resolver tests (new `tests/test_cli_resolver.py`)**
```python
# tests/test_cli_resolver.py
"""Shared config resolver — env → cwd → global (#1708 D1/D5/D6)."""
import json
from unittest import mock
import tortoise.__main__ as main

GLOBAL = json.dumps({"api_key": "tt_global", "api_url": "https://api.premiselabs.co",
                     "team_id": "team-g", "device_id": "anon-g"})


def _write_global(home):  # simulate signup output
    d = home / ".tortoise"; d.mkdir(parents=True, exist_ok=True)
    f = d / "credentials.json"; f.write_text(GLOBAL); f.chmod(0o600)

def test_env_wins_over_files(monkeypatch, tmp_path):
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_env")
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".tortoise" / "credentials.json").parent.mkdir()
    (tmp_path / ".tortoise" / "credentials.json").write_text(GLOBAL)
    (tmp_path / ".tortoise").chmod(0o700)
    p, cfg, key, url = main._resolve_config_path()
    assert key == "tt_env" and p is None

def test_cwd_wins_over_global(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "proj").mkdir(); monkeypatch.chdir(tmp_path / "proj")
    (tmp_path / "proj" / ".tortoise").write_text(json.dumps({"api_key": "tt_cwd", "api_url": "https://api.premiselabs.co"}))
    _write_global(tmp_path)
    _, cfg, key, _ = main._resolve_config_path()
    assert key == "tt_cwd"

def test_global_when_no_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path)); _write_global(tmp_path)
    monkeypatch.chdir(tmp_path / "..")
    p, cfg, key, _ = main._resolve_config_path()
    assert key == "tt_global" and p == tmp_path / ".tortoise" / "credentials.json"

def test_dot_tortoise_dir_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path)); _write_global(tmp_path)
    (tmp_path / "repos" / "p").mkdir(parents=True)
    (tmp_path / "repos" / "p" / ".tortoise").mkdir()  # a DIRECTORY, not a file
    monkeypatch.chdir(tmp_path / "repos" / "p")
    _, cfg, key, _ = main._resolve_config_path()
    assert key == "tt_global"  # dir is skipped, global wins

def test_no_config_anywhere(monkeypatch, tmp_path):
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main._resolve_config_path() == (None, None, None, None)

def test_empty_env_key_treated_as_unset(monkeypatch, tmp_path):
    for bad in ("", "   ", "\t"):
        monkeypatch.setenv("TORTOISE_API_KEY", bad)
        monkeypatch.setenv("HOME", str(tmp_path)); _write_global(tmp_path)
        _, cfg, key, _ = main._resolve_config_path()
        assert key == "tt_global", f"{bad!r} must be skipped (strip), not win"

def test_corrupt_global_raises_config_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tortoise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".tortoise" / "credentials.json").write_text("{not json")
    try:
        main._resolve_config_path()
        assert False, "expected _ConfigError"
    except main._ConfigError as e:
        assert "credentials.json" in str(e)

def test_unreadable_global_raises_config_error(monkeypatch, tmp_path):
    """mode 000 passes is_file() but read_text raises PermissionError (an
    OSError) — the resolver must wrap it in _ConfigError, not traceback."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tortoise").mkdir(parents=True, exist_ok=True)
    f = tmp_path / ".tortoise" / "credentials.json"; f.write_text(GLOBAL)
    f.chmod(0o000)
    try:
        main._resolve_config_path()
        assert False, "expected _ConfigError"
    except main._ConfigError:
        pass

def test_non_string_api_key_raises_config_error(monkeypatch, tmp_path):
    """{"api_key": 123} is undefined behavior — pin it as _ConfigError (never
    a request with 'Bearer 123')."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tortoise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".tortoise" / "credentials.json").write_text(json.dumps({"api_key": 123}))
    try:
        main._resolve_config_path()
        assert False, "expected _ConfigError"
    except main._ConfigError:
        pass
```
**Step 2: Run to verify fail**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_cli_resolver.py -v`
Expected: FAIL — `AttributeError: module 'tortoise.__main__' has no attribute '_resolve_config_path'`

**Step 3: Implement `_resolve_config_path` + refactor `_read_config` + convert the 3 inline sites**
Add `_ConfigError` + `_resolve_config_path(include_env=True)` (D6) above `_read_config`. Env candidate: empty/whitespace key (`.strip()`) → skipped; synthesize `{"api_key": key, "api_url": url}` so `config` is a dict whenever `api_key is not None` (env-only `team keys list/create` dereference `config.get(...)`). Corrupt/unreadable/non-string-key candidate file → `raise _ConfigError(path)` catching `(OSError, JSONDecodeError, TypeError)`. Rewrite `_read_config` to call it and preserve the existing 3-tuple + `_cmd_fail` error contract, mapping `_ConfigError` to the `no_config` failure with the file path; use the pinned-safe no-config message from D6 (keeps `"Run 'tortoise init --api-key <key>' first"` contiguous — tests/test_cli_team_keys.py L88/L167 assert that substring). Replace the inline reads in `_cmd_create_point`, `_cmd_session` with resolver calls, catching `_ConfigError` → `"Invalid config at {path}"` + return 1. `_cmd_context` calls `_resolve_config_path(include_env=False)` (D1b — env alone must not flip local→hosted), catches `_ConfigError` → stderr warning + **local-mode fallback** (preserves the SessionStart hook's graceful degradation), and keeps its local fallback when no config anywhere.

**Step 4: Run to verify pass (run AFTER Step 5's HOME isolation is in place —
without it, a developer machine with a real `~/.tortoise/credentials.json` makes
the no-config tests in test_cli_team_keys.py fail non-deterministically)**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_cli_resolver.py tests/test_cli_team_keys.py tests/test_cli_context.py -v`
Expected: PASS (new resolver tests; existing team-keys/context tests — cwd fixtures still win; the two pinned `"Run 'tortoise init --api-key <key>' first"` assertions at test_cli_team_keys.py L88/L167 stay green because the message keeps the substring contiguous).

**Step 4b: Command-level smoke tests from a global config + env-only + D1b regression**
Task 1's acceptance ("tortoise create-point/context/session/team work from a global config") needs command-level verification, not just resolver unit tests. **Create `tests/test_cli_global_config.py` (mandatory — the run commands below reference it):**
1. Global-config smoke: seed ONLY `~/.tortoise/credentials.json` (HOME isolated, empty cwd), mock `urlopen`, assert `team info`, `create-point`, and `session capture` issue their request with `Authorization: Bearer tt_global` on the expected path; `context` gets one hosted-mode test (global config present → `/v1/context` called).
2. **Env-only smoke (the config=None crash guard):** `TORTOISE_API_KEY` set + no files → `team keys list` (human + `--json`) and `team keys create --json` resolve and call the API with the env key — never `AttributeError: 'NoneType' object has no attribute 'get'`.
3. **D1b regression:** `TORTOISE_API_KEY` set + no cwd/global config → `tortoise context` stays on the LOCAL SDK path (no `/v1/context` urlopen call); global config present + env set → hosted.
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_cli_team_keys.py tests/test_cli_context.py tests/test_cli_global_config.py -v`
Expected: PASS

**Step 5: Add HOME isolation to mint/no-config CLI tests (D9)**
In `tests/test_cli_signup.py`, `tests/test_cli_claim.py`, `tests/test_cli_team_keys.py`, `tests/test_cli_context.py`: add an autouse fixture per module/class that does `monkeypatch.setenv("HOME", str(tmp_path))`, `monkeypatch.delenv("TORTOISE_API_KEY", raising=False)`, AND `monkeypatch.chdir(tmp_path)` — the chdir closes surface-map row 8's second failure mode (a stray `./.tortoise` file in the pytest CWD would 401→re-mint and break the reuse tests).

**Step 6: Full CLI regression**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_cli_signup.py tests/test_cli_claim.py tests/test_cli_team_keys.py tests/test_cli_context.py tests/test_main_guards.py -v`
Expected: PASS

**Step 7: Commit** — invoke `@commit-workflow` (`git add` the 4 test files + `tortoise/__main__.py`; message: `feat(cli): shared config resolver env→cwd→global (#1708)`).

---

### Task 2: Global credentials write + stable device_id + crash fixes

**Intent:** Signup stops writing to `cwd/.tortoise` (the `~` IsADirectoryError crash + per-directory key scattering) and persists a stable `device_id` so client identity is anchored for reuse (and for #1709 later).
**Acceptance:** `tortoise signup` from `$HOME` writes `~/.tortoise/credentials.json` (0600, dir 0700) with `device_id`; no `IsADirectoryError`; a second mint reuses the stored `device_id`; config no longer written to CWD.
**Files:**
- Modify: `tortoise/__main__.py:632-705` (`_cmd_signup` write block)
- Test: `tests/test_cli_signup.py`, `tests/test_cli_claim.py` (update the cwd-path assertion)

**Step 1: Write the failing tests**
```python
# tests/test_cli_signup.py (extend)
import os, stat

def _ok_mint(body=None):
    import json as _j
    resp = mock.MagicMock(); resp.read.return_value = _j.dumps(body or {
        "key": "tt_mint_000000000000000000000000000000000000000000",
        "team_id": "team-mint-1", "team_name": "agent-mint", "graph_name": "team_team-mint-1",
        "identity": "anon-mint", "tier": "free"}).encode(); resp.__enter__.return_value = resp
    return resp

class TestGlobalWrite:
    def test_signup_from_home_writes_global_no_crash(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.chdir(tmp_path)  # cwd IS home (the bug)
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            rc = main._cmd_signup(mock.Mock())
        assert rc == 0
        cfg_path = tmp_path / ".tortoise" / "credentials.json"
        assert cfg_path.is_file(), f"expected {cfg_path} (was IsADirectoryError before #1708)"
        assert (cfg_path.stat().st_mode & 0o777) == 0o600
        assert (tmp_path / ".tortoise").stat().st_mode & 0o077 == 0  # dir 0700
        cfg = json.loads(cfg_path.read_text())
        assert cfg["api_key"].startswith("tt_")
        assert cfg["device_id"].startswith("anon-")

    def test_signup_from_home_with_pre_existing_data_home(self, monkeypatch, tmp_path):
        """The exact incident mechanism: ~/.tortoise ALREADY exists as the data
        home (tortoise.db + audit logs) and cwd == HOME — the old write to
        cwd/.tortoise raised IsADirectoryError AFTER minting."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        data_home = tmp_path / ".tortoise"; data_home.mkdir(parents=True, exist_ok=True)
        (data_home / "tortoise.db").write_bytes(b"\x00" * 16)  # data home marker
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            rc = main._cmd_signup(mock.Mock())
        assert rc == 0
        assert (data_home / "credentials.json").is_file()  # written INSIDE the dir
        assert (data_home / "tortoise.db").is_file()  # data home untouched

    def test_no_cwd_config_written(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        sub = tmp_path / "sub"; sub.mkdir(); monkeypatch.chdir(sub)  # cwd != HOME
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock())
        assert not (sub / ".tortoise").exists(), "signup must not write cwd/.tortoise"
        assert (tmp_path / ".tortoise" / "credentials.json").is_file()

    def test_mint_write_failure_echoes_key_exits_1(self, monkeypatch, tmp_path, capsys):
        """Orphan class: mint succeeds, save fails → key echoed, exit 1, never exit 0."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            with mock.patch("os.replace", side_effect=OSError("read-only fs")):
                rc = main._cmd_signup(mock.Mock())
        assert rc == 1
        err = capsys.readouterr().err
        assert "could NOT be saved" in err
        assert "tt_mint_" in err  # the minted key is echoed so it isn't lost

    def test_mint_mkdir_failure_exits_1(self, monkeypatch, tmp_path, capsys):
        """The mkdir/chmod legs of the write-failure handler (not just os.replace)."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            with mock.patch("pathlib.Path.mkdir", side_effect=OSError("EACCES")):
                rc = main._cmd_signup(mock.Mock())
        assert rc == 1
        assert "could NOT be saved" in capsys.readouterr().err

    def test_successful_write_cleans_stale_tmp(self, monkeypatch, tmp_path):
        """A crashed writer leaves credentials.json.tmp-* behind (key material) —
        the next successful write must sweep them."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        d = tmp_path / ".tortoise"; d.mkdir(parents=True, exist_ok=True)
        stale = d / "credentials.json.tmp-deadbeef"; stale.write_text("partial key material")
        stale.chmod(0o600)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock())
        assert not stale.exists(), "stale tmp must be swept on next successful write"

    def test_device_id_stable_across_mints(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock())
        first = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())["device_id"]
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock(force=True))  # --force re-mints, same device
        second = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())["device_id"]
        assert first == second
```
Also update `tests/test_cli_claim.py::test_signup_claim_prints_dashboard_instructions` — the existing assertion `json.loads((tmp_path / ".tortoise").read_text())` now fails (config is global). Change to read `tmp_path / ".tortoise" / "credentials.json"` (with HOME isolation).
**Step 2: Run to verify fail**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_cli_signup.py tests/test_cli_claim.py -v`
Expected: FAIL — on a fresh tmp HOME (no pre-existing `~/.tortoise` dir) the pre-fix code writes `cwd/.tortoise` (so `credentials.json` is absent / `test_no_cwd_config_written` sees a cwd `.tortoise`); the IsADirectoryError crash reproduces on a machine whose real `~/.tortoise` exists (the historical incident) — the fresh-HOME tests fail on missing/absent-config assertions instead, which is the same red signal. `device_id` key absent.

**Step 3: Implement the global write (D4)**
In `_cmd_signup`, replace the `config_path = Path.cwd() / ".tortoise"` block with the D4 write, wrapped for write-failure handling:
```python
# Global credentials store (#1708 D4): ~/.tortoise/credentials.json (0600),
# dir 0700, atomic unique-tmp write — fixes the IsADirectoryError crash when
# cwd == ~ (previously wrote to ~/.tortoise which IS the data home directory).
home_dir = Path.home() / ".tortoise"
config_path = home_dir / "credentials.json"
# Stable device_id: reuse a previously stored one (server still ignores it —
# #741(a) unchanged; it anchors CLIENT-side reuse only).
stored = {}
if config_path.is_file():
    try:
        stored = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        stored = {}
device_id = stored.get("device_id") or f"anon-{uuid.uuid4().hex[:12]}"
config = {
    "api_key": data["key"], "api_url": api_url,
    "team_id": data["team_id"], "team_name": data["team_name"],
    "device_id": device_id,
}
try:
    home_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(home_dir, 0o700)
    # tmp born at 0600 (os.open O_EXCL) — write_text would create at umask
    # (0644) with a plaintext-key window before chmod; sweep stale tmp-* first
    for stale in home_dir.glob("credentials.json.tmp-*"):
        try:
            stale.unlink()
        except OSError:
            pass
    tmp_path = home_dir / f"credentials.json.tmp-{uuid.uuid4().hex}"  # unique per writer (D4)
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(config, indent=2) + "\n")
    os.replace(tmp_path, config_path)
except OSError as e:
    # Orphan class: the key was minted but cannot be saved — echo it and
    # fail closed so the user never loses the key and never silently re-mints.
    print(f"A key was minted but could NOT be saved to {config_path}: {e}", file=sys.stderr)
    print(f"Your API key (store it manually): {data['key']}", file=sys.stderr)
    print("Fix the path permissions and re-run, or use the key directly.", file=sys.stderr)
    return 1
```
Move the `device_id` generation to before the POST so the mint sends the stable id (body + `X-Device-Id`).

**Step 4: Run to verify pass**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_cli_signup.py tests/test_cli_claim.py -v`
Expected: PASS

**Step 5: Commit** — `@commit-workflow`; message: `fix(cli): signup writes global ~/.tortoise/credentials.json + stable device_id (#1708)`.

---

### Task 3: Reuse-before-mint + validation + `--force`

**Intent:** The core idempotency fix — a second `tortoise signup` in the same environment mints **0 new keys** by validating and reusing the existing one; `--force` escapes a poisoned env/file key.
**Acceptance:** With an existing valid key (env/global/cwd per D1), signup validates and exits 0 with a reuse message and **0 new keys**; 401/403 stored key → auto re-mint; validation network/5xx → fail-closed exit 1; `--force` mints despite existing valid config.
**Files:**
- Modify: `tortoise/__main__.py:632-705` (`_cmd_signup` head + argparse `--force`)
- Test: `tests/test_cli_signup.py`

**Step 1: Write the failing tests**
> Imports to add at the top of `tests/test_cli_signup.py`: `import io` and `from urllib.error import URLError`. **All reuse-path calls below pass `mock.Mock(force=False)`** — a bare `mock.Mock()` has truthy attribute access, so `getattr(args, "force", False)` would be truthy and the reuse gate would always be skipped (the tests would be vacuous).
```python
# tests/test_cli_signup.py (extend) — reuse path
class TestReuse:
    def _valid_team(self):  # GET /v1/team 200
        import json as _j
        resp = mock.MagicMock(); resp.read.return_value = _j.dumps(
            {"team_id": "team-g", "tier": "free"}).encode()
        resp.__enter__.return_value = resp
        return resp

    def _global_cfg(self, tmp_path, **extra):
        d = tmp_path / ".tortoise"; d.mkdir(parents=True, exist_ok=True); d.chmod(0o700)
        cfg = {"api_key": "tt_valid", "api_url": "https://api.premiselabs.co",
               "team_id": "team-g", **extra}
        (d / "credentials.json").write_text(json.dumps(cfg))

    def test_reuse_global_config_skips_mint(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=self._valid_team()) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "already" in out.lower() or "reus" in out.lower()
        # 0 new keys: the mint POST was NEVER issued
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_from_other_cwd(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        (tmp_path / "elsewhere").mkdir(); monkeypatch.chdir(tmp_path / "elsewhere")
        with mock.patch("urllib.request.urlopen", return_value=self._valid_team()) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_env_key(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey"); monkeypatch.setenv("HOME", str(tmp_path))
        with mock.patch("urllib.request.urlopen", return_value=self._valid_team()) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_invalid_key_remints(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_revoked")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "unauthorized", {},
                                  io.BytesIO(b'{"detail":"unauthorized"}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0  # re-minted after 401
        # mirror the skip-mint tests: a regression that treats 401 as "reuse"
        # (rc 0, no mint) must fail here
        assert any(call.args[0].full_url.endswith("/v1/agent/signup")
                   for call in urlopen.call_args_list)

    def test_reuse_forbidden_not_suspended_remints(self, monkeypatch, tmp_path):
        """Non-SUSPENDED 403 = key rejected → re-mint (D2); only SUSPENDED
        fail-closes. Both 403 branches must be pinned."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_rejected")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 403, "forbidden", {},
                                  io.BytesIO(b'{"detail":"not allowed"}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert any(call.args[0].full_url.endswith("/v1/agent/signup")
                   for call in urlopen.call_args_list)

    def test_reuse_invalid_key_remints_from_cwd_config(self, monkeypatch, tmp_path):
        """Legacy upgrade path: a pre-#1708 cwd/.tortoise (no device_id) whose
        key 401s → re-mint MUST persist the device_id to the GLOBAL file so
        client identity stays anchored (future #1709 dedupe)."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        (tmp_path / "proj").mkdir(); monkeypatch.chdir(tmp_path / "proj")
        (tmp_path / "proj" / ".tortoise").write_text(json.dumps(
            {"api_key": "tt_old", "api_url": "https://api.premiselabs.co", "team_id": "team-old"}))
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        global_cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert global_cfg["device_id"].startswith("anon-")
        mint_body = urlopen.call_args_list[-1].args[0].data.decode()
        assert json.loads(mint_body)["identity"] == global_cfg["device_id"]

    def test_reuse_validation_timeout_fail_closed(self, monkeypatch, tmp_path, capsys):
        """socket.timeout / TimeoutError from resp.read() after headers arrive is
        NOT a URLError — the except tuple must include it (flaky-proxy stall)."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=TimeoutError("timed out")) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert "--force" in capsys.readouterr().err
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_remint_post_timeout_reports_orphan(self, monkeypatch, tmp_path, capsys):
        """401 → re-mint POST hangs (timeout): the server may have minted —
        the message must not say 'Cannot reach API' (that misleads into retry)."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_revoked")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        TimeoutError("timed out")]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        err = capsys.readouterr().err
        assert "may have been minted" in err.lower() or "not blindly" in err.lower()

    def test_mint_200_garbage_reports_orphan(self, monkeypatch, tmp_path, capsys):
        """Mint POST returns 200 with an HTML body (proxy) — the server DID
        mint; 'Cannot reach API' would mislead the user into double-firing."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_revoked")
        bad = mock.MagicMock(); bad.read.return_value = b"<html>Sign in</html>"
        bad.__enter__.return_value = bad
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        bad]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        err = capsys.readouterr().err
        assert "may have been minted" in err.lower()
        assert "cannot reach api" not in err.lower()

    def test_reuse_env_url_vs_stored_url(self, monkeypatch, tmp_path):
        """TORTOISE_API_URL env AND a 401-ing stored config URL: D2 pins the
        mint to the CONFIG URL; the message must surface which host is used."""
        monkeypatch.setenv("TORTOISE_API_URL", "https://api.premiselabs.co")
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_url="https://old-host.example.com", api_key="tt_stale")
        side_effects = [HTTPError("https://old-host.example.com/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert any(call.args[0].full_url.startswith("https://old-host.example.com/v1/agent/signup")
                   for call in urlopen.call_args_list)

    def test_concurrent_signup_writers_no_corruption(self, monkeypatch, tmp_path):
        """Two racing writers: unique tmp + os.replace must leave a parseable
        credentials.json with one complete config and no torn inode (D4)."""
        import threading
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        barrier = threading.Barrier(2)
        results = []
        def _mint(_self=None, _args=None):
            barrier.wait()  # both writers hit the write block concurrently
            return _ok_mint()
        def _run():
            with mock.patch("urllib.request.urlopen", side_effect=_mint):
                results.append(main._cmd_signup(mock.Mock()))
        t1 = threading.Thread(target=_run); t2 = threading.Thread(target=_run)
        t1.start(); t2.start(); t1.join(); t2.join()
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert cfg["api_key"].startswith("tt_mint_")
        assert len(list((tmp_path / ".tortoise").glob("credentials.json.tmp-*"))) <= 1

    def test_reuse_validation_network_fail_closed(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=URLError("connection refused")) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert "--force" in capsys.readouterr().err  # escape hint
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)  # NO mint on unreachable

    def test_reuse_validation_429_fail_closed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(429, json.dumps({"detail": "limited"}))) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_validation_5xx_fail_closed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(500, json.dumps({"detail": "boom"}))) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_validation_200_garbage_fail_closed(self, monkeypatch, tmp_path):
        """Captive-portal/proxy 200-with-HTML hits the JSONDecodeError leg."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        resp = mock.MagicMock(); resp.read.return_value = b"<html>Sign in</html>"
        resp.__enter__.return_value = resp
        with mock.patch("urllib.request.urlopen", return_value=resp) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert "--force" in capsys.readouterr().err
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_suspended_403_no_remint(self, monkeypatch, tmp_path, capsys):
        """#308: SUSPENDED 403 must fail closed — never mint over a suspension."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_susp")
        body = json.dumps({"detail": {"code": "SUSPENDED",
                                       "message": "Team suspended",
                                       "appeal_url": "https://support"}})
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(403, body)) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)  # NO mint on suspension
        cap = capsys.readouterr()  # readouterr DRAINS the buffer — capture ONCE
        assert "suspended" in (cap.out + cap.err).lower()

    def test_reuse_remints_against_stored_api_url(self, monkeypatch, tmp_path):
        """Re-mint must target the validated config's base URL, not env/default."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)
        self._global_cfg(tmp_path, api_url="https://staging.example.com", api_key="tt_stage")
        side_effects = [HTTPError("https://staging.example.com/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert any(call.args[0].full_url.startswith("https://staging.example.com/v1/agent/signup")
                   for call in urlopen.call_args_list)

    def test_reuse_invalid_then_rate_limited(self, monkeypatch, tmp_path, capsys):
        """Revoked key + exhausted 2/24h budget: message must mention BOTH."""
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_revoked")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        _http_error(429, json.dumps({"detail": {"error_code": "over_signup_ip_rate_limit"}}),
                                    {"Retry-After": "3600"})]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        err = capsys.readouterr().err
        assert "rate limit" in err.lower()
        assert "invalid" in err.lower()  # the stored key is ALSO dead — say both

    def test_force_mints_despite_existing(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=True))
        assert rc == 0
        assert any(call.args[0].full_url.endswith("/v1/agent/signup")
                   for call in urlopen.call_args_list)  # mint ran (no validation call)

    def test_force_warns_env_shadow(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_bad_env"); monkeypatch.setenv("HOME", str(tmp_path))
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            rc = main._cmd_signup(mock.Mock(force=True))
        assert rc == 0
        err = capsys.readouterr().err
        assert "TORTOISE_API_KEY" in err and "shadow" in err.lower()
```
**Step 2: Run to verify fail**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_cli_signup.py -v`
Expected: FAIL — mint still runs unconditionally.

**Step 3: Implement reuse-before-mint + `--force` (D2/D3)**
In `_cmd_signup`, before the mint POST (keep a comment cross-referencing `_cmd_init._validate_key` — the two `GET /v1/team` validation sites must stay in sync):
```python
force = getattr(args, "force", False)
mint_url = api_url  # may be overridden below when re-minting after a 401/403
reminting_after_401 = False
if not force:
    try:
        cfg_path, cfg, existing_key, existing_url = _resolve_config_path()
    except _ConfigError as e:
        print(f"Config at {e} is corrupt or unreadable — fix or delete it, or use --force.",
              file=sys.stderr)
        return 1  # never mint on a corrupt config (D6)
    if existing_key:
        base = (existing_url or api_url).rstrip("/")
        try:
            req = Request(f"{base}/v1/team", headers={"Authorization": f"Bearer {existing_key}"})
            with urlopen(req, timeout=10) as resp:
                json.loads(resp.read())
            src = (str(cfg_path) if cfg_path else "TORTOISE_API_KEY")
            print(f"✅ Already have a Tortoise Cloud key ({src}) — reusing it.")
            print(f"   Run 'tortoise team keys' or 'tortoise create-point \"hello\"' to use it.")
            print(f"   To mint a fresh key instead: tortoise signup --force")
            return 0
        except HTTPError as e:
            body = e.read().decode() if e.fp else ""
            if e.code in (401, 403):
                # #308: SUSPENDED 403 must NOT mint (mirrors the other _cmd_* handlers)
                sus = _suspended_info(body)
                if sus is not None:
                    print(f"{sus[0]}", file=sys.stderr)
                    return 1
                print(f"Stored key is invalid ({e.code}) — minting a fresh one.", file=sys.stderr)
                reminting_after_401 = True
                mint_url = base  # re-mint against the validated config's host (D2)
                # backfill device_id anchor: a legacy cwd/.tortoise has none —
                # persist it so client identity stays stable (future #1709)
            else:
                print(f"Cannot validate existing key (API error {e.code}) — not minting "
                      "to avoid duplicate keys. Retry later or use --force.", file=sys.stderr)
                return 1
        except (URLError, ValueError, json.JSONDecodeError, TimeoutError, OSError) as e:
            # TimeoutError/OSError: socket.timeout from resp.read() (headers
            # arrived, body stalled — flaky proxy) is NOT a URLError.
            print(f"Cannot validate existing key ({e}) — not minting to avoid duplicate "
                  "keys. Retry later or use --force.", file=sys.stderr)
            return 1
```
Use `mint_url` (not the ambient `api_url`) for the POST. **Device_id backfill:** the `stored`/`device_id` logic in the Task 2 write block must ALSO consult the resolved legacy config (`cfg.get("device_id")`) when re-minting after a 401 — a pre-#1708 `cwd/.tortoise` has no `device_id`; a fresh one every re-mint cycle would defeat the client-side anchor. Add `--force` to the argparse block (L4003-4009): `signup_p.add_argument("--force", action="store_true", help="Mint a fresh key even if a stored key exists (#1708)")`. After a forced mint, if `os.environ.get("TORTOISE_API_KEY")`, warn on stderr that the env key shadows the new key at read time (D3). **Mint-POST handler updates:** (a) extend the existing `(URLError, ValueError, json.JSONDecodeError)` except tuple with `TimeoutError, OSError` — `test_remint_post_timeout_reports_orphan` requires the timeout leg; (b) split the tuple so plain `URLError` keeps "Cannot reach API" but a JSON-decode failure (200-with-garbage) prints "A key may have been minted but the response was unreadable — check the dashboard or support before re-running; do NOT blindly retry" (D2, `test_mint_200_garbage_reports_orphan`); (c) when the 429 follows a 401-triggered re-mint (`reminting_after_401`), append "your stored key is also invalid" context (D2).

**Step 4: Run to verify pass**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_cli_signup.py tests/test_cli_claim.py -v`
Expected: PASS — this single run covers acceptance criterion 1 (reuse tests assert the mint POST count == 0 via `urlopen.call_args_list` filtering) and criterion 3 (401 → re-mint). No separate E2E step needed; the mocked-server key counting IS the assertion.

**Step 6: Commit** — `@commit-workflow`; message: `feat(cli): signup reuse-before-mint with validation + --force (#1708)`.

---

### Task 4: `list_api_keys` returns `created_via` + `expires_at` (both lanes)

> **Parallelizability note:** Tasks 4 and 5 touch files/test files entirely disjoint from Tasks 1–3 (`hosted_api.py`, `supabase_control.py`, `test_hosted_api.py`, `test_supabase_control.py` vs `__main__.py` + CLI tests). Under subagent-driven execution, Task 4 (and Task 5's code change) can be dispatched in parallel with Tasks 1–3; the sequential order in this plan is by choice (single-PR flow), not dependency. Task 5's manual behavioral check does want Task 4's server fields live.

**Intent:** Give the dashboard (and API consumers) first-class session-key metadata instead of the fragile prefix heuristic; registry lane stays None-tolerant until #1709 writes the props at mint.
**Acceptance:** `GET /v1/team/keys` includes `created_via` + `expires_at` for every key in BOTH lanes; Supabase lane reads them through `team_api_keys`; registry lane returns them None-safe; no mint-path code changes (`test_agent_signup.py` untouched).
**Files:**
- Modify: `tortoise/supabase_control.py:1468-1479` (`team_api_keys` select), `tortoise/hosted_api.py:3700-3755` (`list_api_keys`)
- Test: `tests/test_supabase_control.py`, `tests/test_hosted_api.py`

**Step 1: Write the failing tests**
```python
# tests/test_supabase_control.py (extend TestTeamApiKeys)
def test_team_api_keys_selects_created_via_expires_at(self, fake):
    fake.seed("api_keys", [_key_row(id="k1", created_via="bootstrap",
                                    expires_at="2026-08-02T00:00:00Z")])
    rows = team_api_keys(fake, "team-free-001")
    assert rows[0]["created_via"] == "bootstrap"
    assert rows[0]["expires_at"] == "2026-08-02T00:00:00Z"

def test_team_api_keys_missing_created_via_fails_closed(self, fake):
    fake.missing_columns = {"api_keys": {"expires_at"}}
    fake.seed("api_keys", [_key_row()])
    with pytest.raises(RuntimeError):
        team_api_keys(fake, "team-free-001")
```
```python
# tests/test_hosted_api.py (extend TestListApiKeys — registry lane; BOTH registry
# tests must pin the lane: exported SUPABASE_URL + service key would silently run
# the Supabase branch — mirror the existing registry_env fixture pattern)
def test_list_keys_has_created_via_expires_at_fields(self, client, monkeypatch):
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    client.post("/v1/team/keys")
    r = client.get("/v1/team/keys")
    for k in r.json()["keys"]:
        assert "created_via" in k
        assert "expires_at" in k

def test_list_keys_agent_signup_registry_none_tolerant(self, client, monkeypatch):
    """agent_signup-minted registry nodes lack the props until #1709 — the
    None-tolerant row[5]/row[6] branch must be exercised by THIS mint.
    The client fixture overrides get_current_team → TEST_TEAM, so re-point
    the override at the minted team before GET (list_api_keys is team-scoped;
    the signup key lives under its own fresh team_id)."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    signup_team = r.json()["team_id"]
    app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM, team_id=signup_team)
    r = client.get("/v1/team/keys")
    keys = r.json()["keys"]
    assert keys, "signup team should have exactly one key"
    assert keys[0]["created_via"] is None   # JSON null, no crash on absent props
    assert keys[0]["expires_at"] is None
    app.dependency_overrides.clear()
```
```python
# tests/test_hosted_api.py — Supabase lane (reuse the existing `client` fixture +
# autouse monkeypatch, NOT a bare TestClient: the app lifespan composes the MCP
# mount and needs the SDK-init patch + _FALLBACK_KEEPALIVE hygiene of the client
# fixture — mirror test_dashboard_login's _env pattern)
class TestListApiKeysSupabase:
    @pytest.fixture(autouse=True)
    def _supabase_env(self, client, monkeypatch):
        import tortoise.hosted_api as _ha
        from tests.fake_control_plane import FakeControlPlane
        import tortoise.supabase_control as sc
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://listkeys.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-listkeys")
        fake = FakeControlPlane()
        fake.seed("api_keys", [{
            "id": "k1", "team_id": "team-001", "key_prefix": "tt_abcdef1234",
            "created_at": "2026-08-01T00:00:00Z", "last_used_at": None,
            "revoked_at": None, "enabled": True,
            "created_via": "bootstrap", "expires_at": "2026-08-02T00:00:00Z",
        }])
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM, team_id="team-001")
        yield fake
        app.dependency_overrides.clear()

    def test_list_keys_supabase_round_trips_created_via(self, client):
        r = client.get("/v1/team/keys")
        k = r.json()["keys"][0]
        assert k["created_via"] == "bootstrap"
        assert k["expires_at"] == "2026-08-02T00:00:00Z"

    def test_disabled_key_401_on_team(self, client):
        """Pins the reuse suite's '401 → re-mint' contract at the server auth
        boundary: a disabled key must 401 (a fail-open regression here would
        make reuse silently reuse a disabled key).
        IMPLEMENTER-AUTHORED, FAILING-FIRST: write the full body in the red
        phase (Step 1) using real key auth — seed an enabled=false api_keys
        row in the fake, REMOVE the get_current_team override for this test,
        call GET /v1/team with Authorization: Bearer <plaintext>, assert 401.
        Do NOT ship this as a comment-only stub."""
        ...

    def test_expired_key_401_on_team(self, client):
        """Same contract pin for past-expires_at keys (24h bootstrap expiry).
        IMPLEMENTER-AUTHORED, FAILING-FIRST: seed expires_at in the past,
        real key auth, assert GET /v1/team → 401."""
        ...
```
(Implementer note for the two auth pins: they belong in the Supabase-lane class fixture — seed a disabled (`enabled=false`) and an expired (`expires_at` past) `api_keys` row, call `GET /v1/team` through the real `get_current_team` dependency (REMOVE the `get_current_team` override for these — the override bypasses auth, so the disabled/expired rejection must be observed with real key auth, e.g. `Authorization: Bearer <plaintext>` against the fake's resolve path, mirroring test_dashboard_login's auth tests). The essential pin: `enabled=false` and past-`expires_at` keys return 401 on `/v1/team`, so the CLI reuse path's 401→re-mint contract holds in both lanes. Document the #1096 fail-open degrade window as an accepted residual in the PR body.)
**Step 2: Run to verify fail**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_supabase_control.py tests/test_hosted_api.py -k 'created_via or expires_at or team_api_keys or none_tolerant' -v`
Expected: FAIL — `created_via`/`expires_at` absent (the `none_tolerant` token ensures `test_list_keys_agent_signup_registry_none_tolerant` participates in the red run).

**Step 3: Implement the seam + endpoint changes (D7)**
- `supabase_control.py team_api_keys`: select → `["id", "key_prefix", "created_at", "last_used_at", "revoked_at", "enabled", "created_via", "expires_at"]`.
- `hosted_api.py list_api_keys` Supabase branch: add `"created_via": row.get("created_via"), "expires_at": row.get("expires_at")` to each key dict.
- Registry branch: `RETURN k.id, k.key_prefix, k.created_at, k.last_used_at, k.revoked_at, k.created_via, k.expires_at`; add `"created_via": row[5], "expires_at": row[6]` (None for agent_signup-minted nodes pre-#1709; registry recovery/bootstrap mints already carry values).

**Step 4: Run to verify pass**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_supabase_control.py tests/test_hosted_api.py -v`
Expected: PASS

**Step 5: Regression — untouched suites**
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_agent_signup.py tests/test_writer_inventory.py tests/test_dashboard_login.py tests/test_session_login.py tests/test_hosted_api.py tests/test_supabase_control.py -v`
Expected: PASS — and `git diff tests/test_agent_signup.py` is empty (server mint path untouched).

**Step 6: Commit** — `@commit-workflow`; message: `feat(api): list_api_keys exposes created_via + expires_at in both lanes (#1708)`.

---

### Task 5: Dashboard renders session keys from API data

**Intent:** Remove the prefix-match heuristic; session-key rendering/toggle/revoke gating now derives from server-provided `created_via`/`expires_at`, with the old active-key guard retained ONLY as a fallback when the API fields are absent (stale cache / registry lane pre-#1709) so the live session key can never be revoked from the UI.
**Acceptance:** `isSessionKey(k, activeKey)` returns true iff not revoked AND (`created_via === 'bootstrap'` OR `expires_at` truthy OR, when `created_via` is null/absent, the key matches the active session's prefix); behavior is identical to today for the currently-active session key; older bootstrap keys are now uniformly classified session (intended per scope — removes the only UI cleanup path for stale session keys; expiry + registry/Supabase sweep is the cleanup, note in the PR body); unit-tested with `node --test` (AC 6 unit/component check).
**Files:**
- Create: `website/apps/dashboard/src/sessionKey.js` (pure exported predicate)
- Modify: `website/apps/dashboard/src/main.jsx:1973-1981` (`isSessionKey` → thin wrapper passing the active key), consumers at L2882-2899
- Test: `website/apps/dashboard/src/sessionKey.test.js`

**Step 1: Write the failing unit test (pure predicate, no harness needed)**
```js
// website/apps/dashboard/src/sessionKey.test.js — run with node --test (Node 20+,
// zero deps: the predicate is pure, no jsdom/React needed)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isSessionKey, isActiveKey } from './sessionKey.js'

test('bootstrap session key is a session key', () => {
  assert.equal(isSessionKey({ created_via: 'bootstrap', expires_at: null, revoked_at: null }, null), true)
})
test('expiring key is a session key', () => {
  assert.equal(isSessionKey({ created_via: 'recovery', expires_at: '2026-08-02T00:00:00Z', revoked_at: null }, null), true)
})
test('durable key (NULL created_via is absent/undefined → durable only if not active) is not session', () => {
  assert.equal(isSessionKey({ created_via: null, expires_at: null, revoked_at: null, key_prefix: 'tt_other' }, 'tt_other_plaintext_here'), false)
})
test('durable provisioned key is not session', () => {
  assert.equal(isSessionKey({ created_via: 'provisioned', expires_at: null, revoked_at: null, key_prefix: 'tt_x' }, null), false)
})
test('durable key that IS the live session stays non-revocable via isActiveKey', () => {
  const live = 'tt_durable_abcdefgh'
  assert.equal(isSessionKey({ created_via: 'provisioned', expires_at: null, revoked_at: null, key_prefix: live.slice(0, 10) }, live), false)
  // the toggle/revoke guard uses isActiveKey separately — never revoke the live key
  assert.equal(isActiveKey({ created_via: 'provisioned', key_prefix: live.slice(0, 10), revoked_at: null }, live), true)
  assert.equal(isActiveKey({ key_prefix: 'tt_other', revoked_at: null }, live), false)
})
test('revoked key is never session', () => {
  assert.equal(isSessionKey({ created_via: 'bootstrap', expires_at: null, revoked_at: '2026-08-03T00:00:00Z' }, null), false)
})
test('stale-cache (no created_via field) active-key fallback protects the live session', () => {
  // registry lane pre-#1709 / stale response: fields absent → the old guard must hold
  const live = 'tt_livesess_abcdefgh'
  assert.equal(isSessionKey({ key_prefix: live.slice(0, 10), revoked_at: null }, live), true)
  assert.equal(isSessionKey({ key_prefix: 'tt_otherkey', revoked_at: null }, live), false)
})
```
**Step 2: Run to verify fail**
Run: `cd website/apps/dashboard && node --test src/sessionKey.test.js`
Expected: FAIL — `sessionKey.js` does not exist / module not found.

**Step 3: Implement the extracted predicate + wire main.jsx (D8)**
Create `src/sessionKey.js` exporting `isSessionKey` + `isActiveKey` (D8, pure). In `main.jsx`, import under an alias to avoid an ESM redeclaration collision (`import { isSessionKey as isSessionKeyPredicate, isActiveKey } from './sessionKey.js'`), keep a local `function isSessionKey(k) { return isSessionKeyPredicate(k, currentTeamId ? teamKeysRef.current[currentTeamId] : null) }` for the status cell (L2882), and change the toggle/revoke guard (L2883) to `!isSessionKey(k) && !isActiveKey(k, currentTeamId ? teamKeysRef.current[currentTeamId] : null)` — a durable key that IS the live session must never be revocable from the UI (Fix A property). Update the surrounding comments to reference #1708.

**Step 4: Run unit test + build**
Run: `cd website/apps/dashboard && node --test src/sessionKey.test.js && npm run build`
Expected: 7/7 unit tests PASS (bootstrap, expiring, durable-null, provisioned, durable-active+isActiveKey, revoked, stale-cache fallback); vite build completes with no JSX/syntax errors. No dashboard component harness exists — do not add one for a predicate swap (YAGNI); the extracted pure function is the unit-tested surface.

**Step 5: Behavioral note for the PR body**
Call out in the PR: older bootstrap/session keys (prior logins, multiple sessions) now render `ephemeral · session` and are excluded from toggle/revoke — the intended uniform classification per scope AC6 (previously only the single active-session key was protected by the prefix heuristic). Durable keys (`created_via` provisioned/recovery/null-with-no-active-match) keep toggle/revoke.

**Step 6: Commit** — `@commit-workflow`; message: `fix(dashboard): session-key detection from created_via/expires_at with active-key fallback (#1708)`.

---

## Out of Scope (do NOT implement)

- Server-side dedupe in `agent_signup` (#1709 — reverses `#741(a)`; identity model review required).
- Registry APIKey mint prop-writes (#1709 — `list_api_keys` is None-tolerant until then).
- `team_create` idempotency phantom-key (#1710 — already merged as PR #1712).
- Any schema migration / Supabase migration files.
- Rate-limiter changes (#1081 unchanged).
- `_cmd_init` write-target change and incident key revocation (ops action — manual, offered to the user).

## Final Verification

After all tasks: run the full affected suite in one command:
```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_cli_signup.py tests/test_cli_claim.py tests/test_cli_team_keys.py tests/test_cli_context.py tests/test_cli_resolver.py tests/test_cli_global_config.py tests/test_main_guards.py tests/test_hosted_api.py tests/test_supabase_control.py tests/test_agent_signup.py tests/test_writer_inventory.py tests/test_dashboard_login.py tests/test_session_login.py -v
```
Expected: PASS; `git diff --stat` shows no changes to `tests/test_agent_signup.py`. Dashboard: `cd website/apps/dashboard && node --test src/sessionKey.test.js && npm run build`.
> Note: `test_cli_serve.py` shares the `TORTOISE_API_KEY` env surface — run it once (`pytest tests/test_cli_serve.py -v`) to confirm the resolver/env changes don't perturb it; it is not part of the core regression set because it exercises the self-hosted serve path, which the plan does not change.

<!-- plan-review: cycles=3, status=clean, version=2.3.0 -->

---

## Plan-Review Cycle Log

### Cycle 1 (3 parallel fresh-context reviewers: Structural+Efficiency, Integration, Failure-Mode Auditor)

Reviewer findings merged + deduped: **0 P0, 7 P1, 17 P2** (24 raw findings). Confidence: high — all high-stakes claims verified against source (mock-attr truthiness reproduced in `python3`; registry `create_api_key` mint confirmed to write `created_via`/`expires_at` at hosted_api.py:7405; pinned `"Run 'tortoise init --api-key <key>' first"` substring confirmed at test_cli_team_keys.py L88/L167; `_suspended_info`/`_suspended_detail` usage confirmed).

CHANGELOG (fixes applied inline by orchestrator):

| # | Issue | Severity | Location | Fix Applied | Research? |
|---|-------|----------|-----------|-------------|-----------|
| 1 | Bare `mock.Mock()` makes `--force` truthy → reuse gate never runs | P1 | Task 3 Step 1 | All reuse tests now pass `mock.Mock(force=False)` (force test → `force=True`); note added | verified in python |
| 2 | Registry None-tolerant branch untested (create_api_key mint already writes props) | P1 | Task 4 | New `test_list_keys_agent_signup_registry_none_tolerant` mints via `/v1/agent/signup`; rationale corrected in header note + surface map row 6 + D7 | code-verified |
| 3 | Corrupt/unreadable config semantics undefined → silent mint vector | P1 | D6, Task 1, Task 3 | `_ConfigError` defined; resolver raises on corrupt candidate; `_read_config` maps to `no_config` failure; signup catches → fail-closed exit 1; tests added | no |
| 4 | `_cmd_context` env-first flip local→hosted when TORTOISE_API_KEY set | P1 | D1b, Task 1 | `include_env=False` parameter for `_cmd_context`; deviation documented | no |
| 5 | SUSPENDED 403 → silent re-mint, no `_suspended_info` | P1 | D2, Task 3 | `_suspended_info` on reuse path; SUSPENDED → exit 1 no mint; `test_reuse_suspended_403_no_remint` | code-verified |
| 6 | Mint-succeeds-write-fails orphan unhandled + untested | P1 | D4, Task 2 | OSError handler echoes the minted key + exits 1; `test_mint_write_failure_echoes_key_exits_1` | no |
| 7 | Re-mint hits ambient env URL, not the validated config's host | P1 | D2, Task 3 | `mint_url` derives from the validated config's base URL; `test_reuse_remints_against_stored_api_url` | code-verified |
| 8 | AC6 dashboard "unit/component check" substituted with build+eyeball | P1 | Task 5 | Predicate extracted to pure `src/sessionKey.js` + `node --test` unit tests (zero deps) | no |
| 9 | Resolver message update breaks pinned test substrings (L88/L167) | P1 | D6, Task 1 | Pinned-safe message keeps `"Run 'tortoise init --api-key <key>' first"` contiguous | code-verified |
| 10 | D1 precedence divergence from literal scope order | P2 | D1 | Explicit ⚠️ sign-off item added (user/controller ratification); UX Pending row tracks it | no |
| 11 | `_cmd_context` local→hosted flip not surfaced | P2 | D1b | Documented deviation (merged with #4) | no |
| 12 | `test_no_cwd_config_written` vacuous (no chdir) | P2 | Task 2 | Added `monkeypatch.chdir(sub)` + global-path assertion; Step 2 expected-failure text corrected | no |
| 13 | Task 3 snippets missing `io`/`URLError` imports | P2 | Task 3 | Import note added | no |
| 14 | Task 3 Step 5 redundant duplicate run | P2 | Task 3 | Merged into Step 4 (mint-count assertion already in the tests) | no |
| 15 | Fixed tmp name `credentials.json.tmp` → concurrent-writer corruption | P2 | D4, Task 2 | Unique per-writer tmp (`credentials.json.tmp-<uuid>`) | no |
| 16 | D2 429/5xx/200-garbage/wrong-host/env-shadow/401-429 gaps | P2 | Task 3 | 6 new tests added | no |
| 17 | Empty/whitespace env key → lockout | P2 | D6, Task 1 | Resolver skips empty env key; `test_empty_env_key_treated_as_unset` | no |
| 18 | Task 4/5 parallelizable | P2 | Task 4 | Parallelizability note added (ordering by choice, not dependency) | no |
| 19 | Task 5 acceptance "identical behavior" overstates (multi-session) | P2 | Task 5 | Acceptance reworded; PR-body behavioral note (Step 5) | no |
| 20 | Stale-cache case must retain active-key self-revocation guard | P2 | D8 | Fallback keeps prefix guard when `created_via` absent; unit test added | no |
| 21 | GOOD>EASY: validation duplicated vs `_cmd_init._validate_key` | P2 | Task 3 | Accepted; cross-reference comment required in the implementation | no |
| 22 | Command-level smoke tests for converted commands missing | P2 | Task 1 | Step 4b added (`test_cli_global_config.py` smoke tests) | no |
| 23 | Final Verification omitted test_main_guards.py / serve env surface | P2 | Final Verification | Added; test_cli_serve.py run once with note | no |
| 24 | Surface map <2 failure modes per surface | P2 | Surface Map | Enriched rows 1-8 (env shadow, dashboard stale-cache/rollout, HOME+cwd determinism) | no |

**Summary:** Fixes applied: 24 (7 P1, 17 P2). Research queries: 0 (all fixes grounded in code verification; no third-party API surface involved). New content introduced: yes (6 new test specs, extracted dashboard predicate, `_ConfigError` semantics, D1b deviation, sign-off item).

### Cycle 2
Fresh-context re-review (3 parallel reviewers) after Cycle 1 fixes. Reviewer findings merged + deduped: **0 P0, 5 P1, 15 P2** (23 raw findings — the gate catching real new issues, not re-flagging old ones). Confidence: high — function-attribution claims verified against source (the registry `session_key` mint at L7250+ writes both props; `create_api_key` registry mint at L3653 and `agent_signup` registry mint at L6946 do not).

CHANGELOG (Cycle 2 fixes applied inline):

| # | Issue | Severity | Location | Fix Applied | Research? |
|---|-------|----------|-----------|-------------|-----------|
| 1 | Task 4 None-tolerant test cannot pass (get_current_team override → TEST_TEAM; signup mints a different team; k["team_id"] doesn't exist) | P1 | Task 4 | Test re-overrides `get_current_team` to the minted team; asserts `keys[0]` created_via/expires_at None | code-verified |
| 2 | Registry tests not lane-pinned → exported Supabase creds run the wrong branch | P1 | Task 4 | `TORTOISE_CONTROL_PLANE=registry` monkeypatch added to both registry tests | code-verified |
| 3 | `_read_config` env-only → `config=None` crashes `_cmd_team_keys_list/create` (`config.get`) | P1 | D6, Task 1 | Resolver synthesizes `{"api_key", "api_url"}` for env; env-only smoke tests in Step 4b | code-verified |
| 4 | `_ConfigError` unhandled at the 3 inline sites (context SessionStart hook would traceback) | P1 | D6, Task 1 | Per-site handling specified (create_point/session → msg+1; context → warn+local fallback); tests | no |
| 5 | Validation timeout leg missing (`TimeoutError` not in except tuple) | P1 | D2, Task 3 | TimeoutError/OSError added; `test_reuse_validation_timeout_fail_closed` + `test_remint_post_timeout_reports_orphan` | code-verified |
| 6 | Tmp file born at umask (0644) before chmod → plaintext key exposure window | P1 | D4, Task 2 | `os.open(..., O_CREAT|O_EXCL, 0o600)` born-0600; stale-tmp sweep on next write | no |
| 7 | D1 ratification item not tracked (UX Pending said "none") | P2 | UX Decisions | Pending row now lists the single D1 approval item | no |
| 8 | `test_reuse_invalid_key_remints` vacuous without mint-count assertion | P2 | Task 3 | Mint-POST call-count assertion added | no |
| 9 | Non-suspended 403 re-mint branch untested | P2 | Task 3 | `test_reuse_forbidden_not_suspended_remints` | no |
| 10 | 401-then-429 flag missing from code sketch | P2 | Task 3 | `reminting_after_401` flag + conditional message in sketch | no |
| 11 | Task 1 Step 4 runs before HOME isolation → machine-dependent | P2 | Task 1 | Step 4 note "run AFTER Step 5"; ordering fixed | no |
| 12 | Task 5 ESM redeclaration collision (import + same-name function) | P2 | Task 5 | Import alias `isSessionKeyPredicate` specified | no |
| 13 | D1b `include_env=False` unenforced by tests | P2 | Task 1 | D1b regression test added to Step 4b | no |
| 14 | Header/D7/CycleLog misattribute (create_api_key vs session_key) | P2 | header, D7, log | Corrected to `session_key` (L7250+); create_api_key L3653 + agent_signup L6946 lack props | code-verified |
| 15 | Supabase-lane test builds unpatched TestClient (SDK init + keepalive hygiene) | P2 | Task 4 | Fixture reuses `client` + autouse monkeypatch | code-verified |
| 16 | Reuse tests never chdir from pytest CWD (stray ./.tortoise risk) | P2 | Task 1 | `monkeypatch.chdir(tmp_path)` folded into the autouse HOME fixture | no |
| 17 | Unreadable-file leg untested; exception classes unnamed | P2 | Task 1 | `except (OSError, JSONDecodeError, TypeError)`; `test_unreadable_global_raises_config_error` | no |
| 18 | Whitespace env test covers only "" | P2 | Task 1 | Parameterized `["", "   ", "\t"]` + `.strip()` | no |
| 19 | Write-failure coverage gaps (mkdir/chmod legs; pre-existing data-home variant) | P2 | Task 2 | `test_mint_mkdir_failure_exits_1`; `test_signup_from_home_with_pre_existing_data_home` (the exact incident mechanism) | no |
| 20 | Non-string api_key in config undefined | P2 | Task 1 | `test_non_string_api_key_raises_config_error` | no |
| 21 | Mint-POST 200-garbage misdiagnosed as "Cannot reach API" (double-fire lure) | P2 | D2, Task 3 | Orphan-aware message + `test_mint_200_garbage_reports_orphan` | no |
| 22 | cwd-legacy re-mint device_id anchor lost | P2 | Task 3 | Device_id backfill from resolved legacy config; `test_reuse_invalid_key_remints_from_cwd_config` | no |
| 23 | env-URL vs stored-URL collision unpinned | P2 | Task 3 | `test_reuse_env_url_vs_stored_url` + host-visibility message | no |
| 24 | Unique-tmp race claim untested | P2 | Task 3 | `test_concurrent_signup_writers_no_corruption` (threaded) | no |
| 25 | Disabled/expired key 401 contract unpinned at the server boundary | P2 | Task 4 | `test_disabled_key_401_on_team` / `test_expired_key_401_on_team` (real auth) + #1096 residual note | no |
| 26 | Durable active key self-revocation regression (Fix A property) | P2 | D8, Task 5 | `isActiveKey` guard on toggle/revoke (L2883); unit vectors + `isActiveKey` tests | no |

**Summary (Cycle 2):** Fixes applied: 26 (5 P1, 21 P2). Research queries: 0 (all fixes grounded in code verification). New content: 6 new test specs, `isActiveKey` split, born-0600 tmp + sweep, per-site `_ConfigError`, env-config synthesis, TimeoutError leg, device_id backfill.

### Cycle 3
Final verification (fresh-context reviewer, combined dimensions) after Cycle 2 fixes: **0 P0, 1 P1, 4 P2** — all execution-facing. The P1 (capsys double-read draining the capture buffer in `test_reuse_suspended_403_no_remint`, making the assertion check stdout-only while the implementation writes stderr) was verified empirically and fixed to a single `cap = capsys.readouterr()` capture. P2 fixes: mint-POST except tuple extended with `TimeoutError, OSError` + split so plain URLError keeps "Cannot reach API" while JSON-decode failures get the orphan-aware message; the two auth-pin tests marked IMPLEMENTER-AUTHORED, FAILING-FIRST (no comment-only stubs); Task 4 Step 2 `-k` filter gains the `none_tolerant` token; `tests/test_cli_global_config.py` pinned as mandatory; Task 5 Step 4 corrected 6/6 → 7/7. Final verification verdict: **clean** — "No P0s … the plan is structurally sound, internally consistent on the resolver precedence contract, and the code/test snippets are otherwise verified accurate against the current sources."

🔍 Final verification: found 5 residuals — resolved in 1 fix cycle.

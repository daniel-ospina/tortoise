<!-- research-path: none — issue #942 states "Research: none — architecture already decided (docker-compose.yml #338)" -->

# #942 — Self-hosted trust: durable multi-writer is the default; embedded bound to single-writer eval

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the durable multi-writer path (docker compose sidecar) the documented default for self-hosting, prove concurrent-writer atomicity on the real FalkorDB server in CI, and bound embedded FalkorDBLite to single-writer eval with loud warnings at every runtime entrypoint.

**Team:** epistemic-team
**Architecture:** No data-plane or schema changes. Four workstreams: (WS1) docs flip — README, docs/quickstart-selfhosted.md, website/self-hosted.html, docs/infra-runbook.md §4.5, one small compose addition (loopback publish of the sidecar port so host-side MCP configs work); (WS2) runtime guards — loud stderr banners in `serve --http` embedded branch, `selfhost.py` daemon, `key create` (team-mode minting moment), and `init`; (WS3) live concurrency tests — docker:// variants of the seq-monotonicity and multi-writer tests that skip when `TORTOISE_DB_URI` is unset; (WS4) new pre-merge CI job `test-concurrency-falkor` running those two tests against a `falkordb/falkordb-server` service container. Guard decision (documented in issue scope): **WARN, not refuse** — `--auth tenant` is the argparse default, tenant-on-embedded is functionally correct auth (registry keys verify), and post-#915 embedded is AOF-durable for a single process; the trust killer is silence, the fix is loud.

### Pattern Research

Skipped — plan touches zero third-party dependencies. `falkordb/falkordb-server:latest` is already the repo's compose image (docker-compose.yml), the healthcheck (`redis-cli -a falkordb ping`) is copied verbatim from compose, and the `docker://:falkordb@localhost:6379/tortoise` URI form is already exercised in-repo (tests/ep_diagnostic.py, ep_e2e_patterns.py).

### Integration Surface Map

| Surface | Layer | Coverage | Bug-pattern flags |
|---|---|---|---|
| `serve --http` embedded branch (CLI) | unit | tests/test_cli_serve.py (capsys) | banner must be stderr-only (stdout asserts exist); must not contain "reachable on your network" (asserted absent in test_serve_http_loopback_aliases_no_network_warning); rc unchanged |
| `selfhost.py` daemon startup | unit | tests/test_selfhost.py (capsys via importlib.reload) | module-level print fires on every import — TestClient suite already imports it |
| `tortoise key create` embedded target | unit | tests/test_cli_serve.py (capsys) | key tests assert stdout + rc only — stderr warning safe |
| `tortoise init` embedded success line | unit | tests/test_cli_context.py (stdout asserts — add note to stdout, not stderr) | success line is stdout; keep note one-line |
| Event-store seq atomicity (live) | integration/live | NEW test_seq_is_monotonic_under_concurrency_live_falkor (CI job) | vacuity trap: must NOT use sdk_factory (mints isolated embedded files); namespace must pass `_assert_test_graph` (test_ prefix); warm-up create_point before threads (schema install) |
| Multi-writer projection (live) | integration/live | NEW test_concurrent_writers_live_falkor_no_lost_writes (CI job) | subprocess helper must inherit env (no pop); graph name test-prefixed |
| CI workflow | config | test-concurrency-falkor job (CI run proves) | job-scoped env only; node-ID targeting; -rs skip-fail guard; watchdog summary pattern |
| Docs (README/quickstart/website/runbook) | docs | ci.yml docs job (markdownlint + link check on PR) | canonical image `falkordb/falkordb-server:latest` + canonical host URI `docker://:falkordb@localhost:6379/tortoise` everywhere |

### Tech Stack
Python 3.12, pytest, GitHub Actions (service containers), docker compose, FalkorDB (falkordb/falkordb-server).

---

## Task 1: WS1 — Docs flip: durable compose path leads everywhere

**Intent:** The issue's O/I/T indicator 1 — onboarding must lead with `docker compose up -d` (durable) and document embedded as single-agent eval only, so self-hosters land on the multi-writer path by default.
**Acceptance:** README quickstart, docs/quickstart-selfhosted.md, website/self-hosted.html, and docs/infra-runbook.md §4.5 all present `docker compose up -d` as the first/recommended self-hosted path; every embedded mention carries the single-writer eval boundary; decision blocks present; no doc still calls embedded "default" or "recommended"; canonical image + host URI consistent; self-hosted.html Python version is 3.12 and "multi-node" claim removed.
**Files:**
- Modify: `README.md`, `docs/quickstart-selfhosted.md`, `website/self-hosted.html`, `docs/infra-runbook.md`, `.env.example`, `docker-compose.yml`, `tests/test_selfhost.py` (stale comment)

**Step 1: README quickstart — add the durable compose path as Option 1**
In `README.md` §Quickstart → §1 Install → "Self-hosted (run it yourself)" bullet: add before the pip lines:

```markdown
- **Self-hosted — durable (recommended): Docker compose.** Runs the daemon +
  a FalkorDB sidecar (AOF, named volume, healthcheck):

  ```bash
  git clone https://github.com/daniel-ospina/tortoise.git && cd tortoise
  docker compose up -d          # daemon on http://localhost:8000 (MCP at /mcp)
  ```

  Durable multi-writer: the compose sidecar is the supported team/production
  path. For a single-agent eval without Docker, see the pip path below —
  embedded FalkorDBLite is SINGLE-WRITER / EVAL-ONLY (concurrent writers lose data).
```

Keep the existing pip lines as "Self-hosted — single-agent eval (no Docker)" with an eval-only note.

**Step 2: README env table — bound `TORTOISE_DB_PATH` row**
Change the `TORTOISE_DB_PATH` row description to: "Embedded FalkorDBLite eval path — SINGLE-WRITER, eval only (concurrent writers lose data); delete the db + `<db>-appendonlydir` to reset" and the `TORTOISE_DB_URI` row to "Durable FalkorDB connection string — the recommended path (docker compose or managed Cloud)".

**Step 3: docs/quickstart-selfhosted.md — rewrite §2 to compose-first + decision block**
- Header line "The default path needs **no Docker**" → "This guide gets you from zero to a running server in ~5 minutes. **The recommended path uses Docker** (durable multi-writer); a no-Docker embedded path is available for single-agent eval."
- Add decision block after prerequisites:

```markdown
## Which path?

| You are… | Use… |
|---|---|
| One agent, experimenting / laptop eval | Embedded (Option C) — single-writer, eval only |
| A team / multiple agents / production | Docker (Option A or B) — durable multi-writer |
| Zero-ops | [Hosted Cloud](quickstart-cloud.md) |
```

- §2 becomes: **Option A — Docker compose (recommended, durable)** → `docker compose up -d` in the repo clone (daemon http://localhost:8000/mcp; sidecar with requirepass + AOF; TORTOISE_DB_URI is wired by compose — nothing to set). **Option B — Bare container (durable variant)** → `docker run -d --name tortoise-falkordb -p 127.0.0.1:6379:6379 -e REDIS_ARGS="--requirepass falkordb --appendonly yes" falkordb/falkordb-server:latest` + `export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise'`. ⚠️ Auth/AOF go via the `REDIS_ARGS` env var — the falkordb image entrypoint ignores command-line args (same lesson as docker-compose.yml's header; a bare `--requirepass` run arg silently starts a passwordless sidecar). **Option C — Embedded (single-agent eval only, no Docker)** → `tortoise init` auto-creates `~/.tortoise/tortoise.db` via falkordblite; ⚠️ SINGLE-WRITER: concurrent writers lose data; fine for one agent.
- §5 connect step: lead with the DAEMON-URL config for compose users (`claude mcp add tortoise http://localhost:8000/mcp`, or the `.mcp.json` `"type": "http"` block — both already exist in README §2); DEMOTE the stdio block (`"command": "python3", "args": ["-m", "tortoise.mcp_server"]` + TORTOISE_DB_PATH) to the no-Docker eval path, explicitly labeled single-writer. ⚛️ Rationale: after the flip, a compose user following the quickstart to §5 must NOT be steered into configuring a SECOND embedded stdio server on the single-writer engine — install leads with compose, connect must lead with the daemon too (the flip is only half-done otherwise).
- §5 "Authenticated local MCP (optional)" block: add one line — "`serve --http --auth tenant` on an embedded DB is single-agent eval only; a durable team deployment uses Docker (Option A/B) or Cloud." (Compose users: the daemon already serves /mcp with auth via TORTOISE_API_KEY.)

**Step 4: website/self-hosted.html — compose-first + decision table**
Keep `id="install-code"` on the compose pre in §1 (the copy button `copyText('install-code')` depends on it); give the pip alternative its own pre WITHOUT a duplicate id.
- §1 Install: replace "No Docker needed for the embedded mode" with compose as step 1 (`git clone … && docker compose up -d`), pip install as the no-Docker alternative; fix "Python 3.11+" → "Python 3.12+".
- New §2 "Run the daemon (Docker, recommended)": `docker compose up -d` → daemon at `http://localhost:8000/mcp`; renumber subsequent sections by TITLE (Onboard → §3, Start the MCP server → §4, Role memory → §5, Which path → §6).
- §4 (the old §3 "Start the MCP server (stdio)" — now the connect section): lead with the daemon URL for the compose path (`claude mcp add tortoise http://localhost:8000/mcp`); keep stdio as the no-Docker path.
- old §5 "Docker vs embedded" (becomes §6): title → "Which path should I use?" decision table: rows single-agent eval → Embedded; team/multi-agent → Docker compose sidecar; production/HA → Docker compose or managed Cloud; each with a "why" column (durability, concurrent writers, backups). Replace the old "Docker vs embedded" comparison; fix the "multi-node" claim (single-node sidecar).
- Harness configs (JS): `TORTOISE_DB_URI: "docker://:falkordb@localhost:6379/tortoise"` (canonical, password included).

**Step 5: docs/infra-runbook.md §4.5 — align authenticated-MCP wording (CONCRETE)**
The §4.5 bullet (~line 108) — "resolves its DB target from `TORTOISE_DB_URI`, defaulting to the local container `docker://:@localhost:16379/tortoise` (`.mcp.json`, `.env.example`)" — is ONE occurrence (the "defaulting" wording and the cross-ref are the same line; there is no second line — do not hunt for one). Replace it with the canonical line: "resolves its DB target from `TORTOISE_DB_URI` — canonical local form `docker://:falkordb@localhost:6379/tortoise` (compose publishes 127.0.0.1:6379; `.mcp.json`, `.env.example`)". Also update the authenticated-MCP block so the LEAD command is the durable path (daemon via compose / `TORTOISE_DB_URI`), with the `TORTOISE_DB_PATH=~/.tortoise/tortoise.db tortoise serve --http` embedded variant explicitly demoted and labeled single-agent eval — the ops runbook must not present an embedded+tenant command as the primary authenticated setup. (The CI guard cannot enforce "leads" — this is a judgment edit; the guard only prevents machine-checkable regressions.)

**Step 6: docker-compose.yml — loopback-publish the sidecar port**
Add to the `falkordb` service (after `image:`):

```yaml
    ports:
      - "127.0.0.1:6379:6379"   # loopback only — host-side MCP configs reach the sidecar
```

Update the header comment's usage block to mention `docker://:falkordb@localhost:6379/tortoise` for host-side clients.

**Step 7: .env.example + README premise-labs note + runbook — purge the dead 16379/passwordless URI**
- `.env.example`: replace `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise` (and the comment claiming compose maps host 16379 → container 6379, which was already false) with the canonical `docker://:falkordb@localhost:6379/tortoise` and a correct comment (compose publishes 127.0.0.1:6379 after Task 1 Step 6; sidecar runs with `--requirepass falkordb`).
- `README.md` §Repository layout (premise-labs note): the sentence "this repo uses `:16379` consistently" is now false — reword to "this repo's canonical host URI is `docker://:falkordb@localhost:6379/tortoise` (compose publishes 127.0.0.1:6379)".
- `docs/infra-runbook.md` §4.5: rewrite the "defaulting to the local container `docker://:@localhost:16379/tortoise`" line to the canonical URI.
- `.env.example`: ALSO flip `FALKORDB_PORT=16379` → `6379` in the legacy `FALKORDB_*` trio ALSO add the single-writer boundary to the `TORTOISE_DB_PATH` comment block ("Embedded (FalkorDBLite) DB target…" — currently reads as first-class guidance with no eval note; this file IS in the guard's scan scope). (with `FALKORDB_PASSWORD=falkordb` already set, the legacy path then constructs the canonical URI exactly). Note: this trio is the legacy CLI path and must match the compose-published port.
- `docs/infra-runbook.md` §4.5: the SINGLE dead-URI line (the "defaulting…" wording and the `.mcp.json`/`.env.example` cross-ref are the SAME line, ~107-108) → canonical URI (see Step 5 for the exact replacement).
- `website/docs.html` (~line 147): the curl example "the production port is 16379" is a HOSTED-API sample — 16379 is the repo's hosted convention (per .env.example history comment, #761). ⛔ Do NOT flip — document as EXCLUDED (hosted surface, out of #942 scope).
- `graph-scripts/setup.py` (~lines 584/630/645): ⛔ Do NOT flip — 16379 is a deliberately distinct port for its standalone `falkordb-tortoise` installer container; flipping would collide with the compose publish (127.0.0.1:6379). Document as EXCLUDED (legacy dev installer; takes TORTOISE_DB_URI env first).
- Blast-radius note for the plan: remaining `graph-scripts/*.py` 16379 defaults are historical dev scripts (env-overridable) — scoped OUT, not touched.
- Legacy-trio drift note: CODE defaults for `FALKORDB_*` remain 16379 (env-overridable; tests pin them — do NOT flip code defaults in this issue). Users without a .env must set `FALKORDB_PORT=6379` or use the canonical `TORTOISE_DB_URI`; add one troubleshooting line in quickstart §Troubleshooting.
- `docs/quickstart-selfhosted.md`: also sweep §3 env-table example URI and the "The passwordless URI is **canonical**" claim (now false — canonical carries `:falkordb@`), and the Troubleshooting "Port 16379 already in use" entry → "Port 6379 already in use" with remap guidance (`127.0.0.1:16380:6379` + URI change). Note: host 6379 can collide with a local redis/FalkorDB or another compose stack (NOT the embedded test suite — redislite binds unix sockets) — document the symptom and the remap.

**Step 8: tests/test_selfhost.py — fix stale comment**
`_client_for_env` docstring says "conftest.py globally sets TORTOISE_DB_URI (test container)" — false (conftest sets only the pepper). Replace with: "conftest sets only TORTOISE_SECRET_PEPPER; clear TORTOISE_DB_URI for embedded-mode tests so selfhost's URI check falls through to TORTOISE_DB_PATH."

**Step 9: verify docs consistency**
Run: `grep -rn "Embedded (default)\|recommended to start\|multi-node\|16379\|passwordless URI is\|docker://localhost:6379\|docker://:@localhost:6379" README.md docs/quickstart-selfhosted.md docs/infra-runbook.md website/self-hosted.html .env.example` — expect no hits. (Byte-identical to the CI guard pattern in Task 4 Step 1 — local and CI must not diverge. `website/docs.html` + `graph-scripts/setup.py` are EXCLUDED by design: hosted sample / legacy installer.)

**Step 10: README default-column + compose cross-ref accuracy**
- README env table `TORTOISE_DB_PATH` row: default column says `/data/tortoise.db` (the Docker-image value) — the canonical default is `~/.tortoise/tortoise.db` (config.py resolve_db_path); fix the column while editing the row.
- docker-compose.yml header comment cross-references "the embedded-FalkorDBLite single `docker run` (see README quickstart)" — the README has no such path; reword to reference docs/quickstart-selfhosted.md Option C. ALSO update the header's durability framing to the post-#915 boundary: embedded is "durable for ONE process since #915; concurrent writers lose data — single-writer eval only" (drop the now-imprecise "not durable / AOF-off" phrasing; #101 stays as historical context).

## Task 2: WS2 — Loud embedded single-writer warnings (serve, daemon, stdio, key create, init)

**Intent:** Issue O/I/T indicator 3 — embedded mode surfaces a loud, explicit single-writer warning at every runtime entrypoint, and `--auth tenant` on embedded is marked eval-only (WARN decision, documented in scope cycle 2).
**Acceptance:** `serve --http` (any auth) on embedded prints a loud stderr banner; `selfhost.py` prints it on embedded startup; the stdio entrypoint (`tortoise serve` / `python -m tortoise.mcp_server`) prints it; `tortoise key create` warns at key-mint time; `tortoise init` embedded success line carries a one-line eval note; tests prove all five (plus the URI-branch negative); existing tests stay green.
**Files:**
- Create: `tortoise/_embedded.py` (zero-import leaf — the ONLY home of EMBEDDED_EVAL_BANNER)
- Modify: `tortoise/__main__.py` (`_cmd_serve_http` embedded branch, `_cmd_key_create`, `_cmd_init`), `tortoise/selfhost.py`, `tortoise/mcp_server.py` (stdio `main()` + `from tortoise.config import is_db_uri`)
- Test: `tests/test_cli_serve.py`, `tests/test_selfhost.py`, `tests/test_mcp_server.py` (PINNED — stdio banner), `tests/test_cli_context.py` (init note)

**Step 1: shared banner constant — new zero-import leaf module `tortoise/_embedded.py`**
⛔ Do NOT put the constant in `selfhost.py` — `selfhost.py:32` imports `create_http_app` from `mcp_server` at module load, so `mcp_server` importing the constant from `selfhost` is an import cycle (ImportError on the stdio entrypoint; masked in-process when selfhost imports first — the failure mode that ships green locally and breaks in prod). `tortoise/_embedded.py` imports NOTHING:

```python
"""Embedded-mode boundary text, shared by every entrypoint (#942).

Zero-import leaf module: __main__, mcp_server, and selfhost all import
EMBEDDED_EVAL_BANNER from here. NOT in selfhost.py: selfhost imports
create_http_app from mcp_server at module load, so mcp_server importing
back from selfhost would be an import cycle.
"""

EMBEDDED_EVAL_BANNER = (
    "⚠️  EMBEDDED FalkorDBLite — SINGLE-WRITER, EVAL ONLY. "
    "Concurrent writers (multiple agents) LOSE DATA on this engine. "
    "Durable multi-writer: `docker compose up -d` (repo root) or set "
    "TORTOISE_DB_URI (managed Cloud). --auth tenant on embedded is "
    "single-agent eval only — NOT a supported team deployment."
)
```

Consumers: `tortoise/__main__.py` (serve --http + key create), `tortoise/selfhost.py`, `tortoise/mcp_server.py` — all `from tortoise._embedded import EMBEDDED_EVAL_BANNER`. No cycle: `_embedded` is a leaf; `config.py` (also leaf: logging/os/pathlib only) supplies `is_db_uri`.

**Step 2: banner in `_cmd_serve_http` embedded branch**
In the `else:` branch (non-URI target, after the "DB target = path" print), emit `print(EMBEDDED_EVAL_BANNER, file=sys.stderr)` — auth-mode-independent, before the tenant branch constructs `registry_sdk`. Must not contain "reachable on your network". rc unchanged.

**Step 3: warning in `_cmd_key_create` embedded branch**
In `_cmd_key_create`, in the `else:` (embedded) branch after the "registry at …" print, emit the SAME shared constant — single banner-text source, and the test asserts its "SINGLE-WRITER" / "EVAL ONLY" substrings:

```python
print(EMBEDDED_EVAL_BANNER, file=sys.stderr)
```

**Step 4: warning in `selfhost.py`**
Replace the bare `_logger.warning(...)` embedded block with a stderr print + keep the logger call (constant imported from `tortoise._embedded`); ALSO reword the #101 incident comment directly above it (currently "AOF-off") to the post-#915 boundary — "durable for ONE process since #915; concurrent writers lose data". ⚠️ `selfhost.py` currently imports NO `sys` — the same edit MUST add `import sys` (the snippet's `file=sys.stderr` would otherwise NameError at import in embedded mode, crashing the daemon in exactly the mode it warns about, and reddening the 7 `_client_for_env` tests that reload the module with URI=""):

```python
if not os.environ.get("TORTOISE_DB_URI"):
    print(f"tortoise selfhost: {EMBEDDED_EVAL_BANNER}", file=sys.stderr)
    _logger.warning(EMBEDDED_EVAL_BANNER)
```

**Step 5: one-line note in `tortoise init` embedded success line + test**
Find the embedded success print in `_cmd_init` (line ~500, "Embedded mode initialized"); append ` (single-writer, eval only — docker compose for durable multi-writer)` to the stdout line. Test in tests/test_cli_context.py: `_delenv_falkordb(monkeypatch)` + `monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "init.db"))` (⚠️ without a tmp path, `_resolve_db_target(None)` falls through to the REAL `~/.tortoise/tortoise.db` — home-dir pollution) + `_cmd_init(mock.Mock(path=None, cmd="init", yes=True, api_key=None, no_index=True))` (⚠️ `no_index=True` prevents the git-repo auto-index subprocess; there is no positive embedded-init precedent in the file — the existing `_cmd_init` tests are all negative pre-branch exits). Assert "Embedded mode initialized" still in stdout AND the eval note substring in stdout.

**Step 5b: banner in the stdio path (tortoise/mcp_server.py main())**
The stdio entrypoint (`python -m tortoise.mcp_server` / `tortoise serve` default / `tortoise-serve` console script → deployment.serve → mcp_server.main()) is where embedded eval users actually land (two MCP clients sharing one embedded DB = concurrent writers = data loss — the documented redislite limitation). In `tortoise/mcp_server.py` `main()` (~line 1177):
- Condition: `not is_db_uri(uri)` — NOT "URI unset": `_get_sdk` treats a bare-path `TORTOISE_DB_URI` as embedded (backward-compat, mcp_server.py:310-313), and that path must also warn. `is_db_uri` is NOT currently imported in mcp_server.py — add `from tortoise.config import is_db_uri` (cycle-safe: config imports only logging/os/pathlib).
- Placement: AFTER the `sys.exit(1)` config-error guard (neither URI nor DB_PATH nor TORTOISE_ALLOW_EMBEDDED → exit before any banner), immediately before `mcp.run(transport="stdio")`. Single-fire verified: `tortoise serve` stdio dispatches straight to mcp_server.main() (no own banner) — the banner fires exactly once.

**Step 5c: test — stdio banner (tests/test_mcp_server.py — PINNED)**
Add `import tortoise.mcp_server as mcp_mod` at the TOP of the test module (import order is the contract: mcp_server must load FIRST so the import cycle can never be masked), then the test (monkeypatch the module-level instance's run, set `TORTOISE_DB_PATH` — else main() sys.exit(1)s before the banner — delenv `TORTOISE_DB_URI`):

```python
def test_stdio_embedded_banner(monkeypatch, capsys):
    import tortoise.mcp_server as mcp_mod
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", "/tmp/tortoise-eval.db")
    called = {}
    # Instance-attribute functions are NEVER bound — a method-shaped
    # fake_run(self, **kw) would TypeError on the call. **kw-only.
    def fake_run(**kw):
        called["run"] = True
    monkeypatch.setattr(mcp_mod.mcp, "run", fake_run)
    mcp_mod.main()
    assert called.get("run"), "stdio main() must reach mcp.run"
    err = capsys.readouterr().err
    assert "SINGLE-WRITER" in err and "EVAL ONLY" in err
    # main() registers _get_sdk() with monitoring — reset the cached module
    # SDK so later tests in this file don't silently reuse the embedded one.
    mcp_mod.sdk = None
    mcp_mod._sdk = None
```

(Env is read at call time inside main(); monkeypatch.setattr on the instance attribute works; instance-attribute functions are never bound, so the fake is **kw-only.)

**Step 6: tests — banner in serve + key create (tests/test_cli_serve.py)**
There is NO `_dispatch_serve_http` helper in this file — the existing pattern (test_serve_http_main_dispatch_tenant, line ~250) is `_patch_serve_runtime(monkeypatch, tmp_path)` then `rc = main(["serve", "--http"])` (`_patch_serve_runtime` delenvs TORTOISE_DB_URI and sets TORTOISE_DB_PATH → embedded branch). Add, mirroring that exact shape:

```python
def test_serve_http_embedded_banner_stderr(monkeypatch, tmp_path, capsys):
    # embedded (no TORTOISE_DB_URI) + default auth tenant → rc==0, banner on stderr
    _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "SINGLE-WRITER" in err and "EVAL ONLY" in err
    assert "reachable on your network" not in err

def test_serve_http_uri_branch_no_banner(monkeypatch, tmp_path, capsys):
    # negative pin: banner must NOT fire when TORTOISE_DB_URI is a supported URI.
    # Order matters: _patch_serve_runtime DELENVS TORTOISE_DB_URI, so patch FIRST,
    # then set the URI (monkeypatch restores both at teardown).
    _patch_serve_runtime(monkeypatch, tmp_path)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379/tortoise")
    rc = main(["serve", "--http"])
    assert rc == 0
    assert "SINGLE-WRITER" not in capsys.readouterr().err

def test_key_create_embedded_warns(monkeypatch, tmp_path, capsys):
    # embedded env: delenv URI + set a tmp DB path (never the real ~/.tortoise db),
    # then `main(["key", "create", "--name", "t"])`; banner on stderr, key still
    # printed to stdout. Do NOT reuse the local_db fixture — it calls
    # capsys.readouterr() internally and would consume the banner.
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "t.db"))
    rc = main(["key", "create", "--name", "t"])
    assert "SINGLE-WRITER" in capsys.readouterr().err
    assert rc == 0
```

**Step 7: test — daemon banner (tests/test_selfhost.py)**
Add `test_embedded_banner_stderr` using `capsys` + the existing `_client_for_env` (which sets TORTOISE_DB_URI="" and reloads the module): after building the client, `err = capsys.readouterr().err; assert "SINGLE-WRITER" in err and "EVAL ONLY" in err`.

**Step 8: run affected tests**
Run: `python -m pytest tests/test_cli_serve.py tests/test_selfhost.py tests/test_mcp_server.py tests/test_cli_context.py tests/test_bridge_mcp.py -v --timeout=300 -p no:cacheprovider` — all green, including the pre-existing dispatch/namespace/loopback/MCP tests (they assert stdout + rc / MCP handshake, not stderr content).

## Task 3: WS3 — Live docker:// concurrency tests (skip when URI unset)

**Intent:** Issue O/I/T indicator 2 + target 2 — the TRUE cross-worker path of the concurrency suite, runnable against a real sidecar in CI, non-vacuous by construction.
**Acceptance:** Two new tests: `test_seq_is_monotonic_under_concurrency_live_falkor` (8 threads on ONE shared live graph; union of seqs contiguous 1..max; URI backend asserted) and `test_concurrent_writers_live_falkor_no_lost_writes` (5 subprocess writers on one live graph; all keys + count present). Both `pytest.skip` visibly when `TORTOISE_DB_URI` unset. Both use test-prefixed graph names that pass `_assert_test_graph`. Embedded/local suites unaffected (skip).
**Files:**
- Modify: `tests/test_event_store.py`, `tests/test_embedded_concurrency.py`

**Step 1: shared skip helper (tests/conftest.py)**
In `tests/conftest.py` add (conftest is auto-collected and `tests/` is on pytest's sys.path, so `from conftest import _skip_unless_live_uri` works in both test files; no conflict with `_skip_if_no_falkor` — that lives in tests/test_projection.py):

```python
def _skip_unless_live_uri():
    """Skip a docker:// live-FalkorDB test when no URI is configured.

    Divergence from _skip_if_no_falkor (probe-based, test_projection_version_gate):
    these tests REQUIRE the real server that CI's test-concurrency-falkor job
    provides (TORTOISE_DB_URI set); in every other surface they must skip
    VISIBLY (pytest.skip), never early-return-green.
    """
    import os
    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("requires TORTOISE_DB_URI (live FalkorDB sidecar; see CI job test-concurrency-falkor)")
```

**Step 2: live seq test (tests/test_event_store.py)**
Append after `test_seq_is_monotonic_under_concurrency` (note: this file imports only `json` at module level — add `import os` and `from conftest import _skip_unless_live_uri` in the test or at module level; `pytest` is imported in conftest so `pytest.skip` is available there):

```python
def test_seq_is_monotonic_under_concurrency_live_falkor():
    import os
    """TRUE cross-worker atomicity on ONE shared live graph (#942).

    The embedded sibling test documents that real cross-worker atomicity
    needs a live FalkorDB — this is that path, run by CI's
    test-concurrency-falkor job (TORTOISE_DB_URI=docker://...). Skips
    elsewhere. Deliberately does NOT use sdk_factory (it mints isolated
    embedded files — the vacuous pattern this test exists to replace).
    """
    import threading
    from tortoise.sdk import TortoiseSDK
    _skip_unless_live_uri()
    uri = os.environ["TORTOISE_DB_URI"]

    # Reset the shared graph (test-prefixed name passes _assert_test_graph).
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(uri, graph_name="test_live_seq_tortoise")
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.close()

    # Warm-up: install the event schema deterministically before threads race.
    warm = TortoiseSDK(namespace="test_live_seq")
    assert warm._db_uri, "live test must resolve the URI backend (not embedded)"
    warm.create_point("statement", "warmup")
    warm.close()

    errors, per_worker = [], []

    def worker(i):
        try:
            s = TortoiseSDK(namespace="test_live_seq")
            s.create_point("statement", f"live-c{i}")
            per_worker.append([e["seq"] for e in _events(s._get_proj())])
            s.close()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    union = sorted({seq for seqs in per_worker for seq in seqs})
    assert union == list(range(1, len(union) + 1)), (
        f"global seqs not contiguous 1..N — lost or duplicate seqs: {union}")
    assert len(union) >= 8, "expected >= 8 events (warmup + 8 workers)"
```

**Step 3: live multi-writer test (tests/test_embedded_concurrency.py)**
Add `_spawn_live_writer` (mirrors `_spawn_writer` but inherits env — no pop, no TORTOISE_DB_PATH; subprocess uses `FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"], graph_name="test_live_mw_tortoise")` and `_upsert`s `writes` keys, printing WROTE/DONE handshakes) plus `from conftest import _skip_unless_live_uri` at module level, and:

```python
def test_concurrent_writers_live_falkor_no_lost_writes():
    """5 concurrent subprocess writers on ONE live server — no lost writes (#942).

    The embedded failure mode (concurrent writers lose data) must not exist
    on the durable path. CI's test-concurrency-falkor job sets
    TORTOISE_DB_URI; elsewhere this skips visibly.
    """
    import os
    _skip_unless_live_uri()
    uri = os.environ["TORTOISE_DB_URI"]
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(uri, graph_name="test_live_mw_tortoise")
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.close()

    procs = [_spawn_live_writer(i, writes=10) for i in range(5)]
    for p in procs:
        p.wait(timeout=90)
        assert p.returncode == 0, p.stderr.read()

    proj = FalkorProjection.from_uri(uri, graph_name="test_live_mw_tortoise")
    try:
        rows = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set
        assert rows and rows[0][0] == 50, f"expected 50 points (5×10), got {rows}"
        rows = proj.g.query("MATCH (n:Point) RETURN n.id").result_set
        ids = {r[0] for r in rows}
        expected = {f"w{i}-k{j}" for i in range(5) for j in range(10)}
        assert ids == expected, f"lost or duplicate writes: {len(expected) - len(ids & expected)} missing"
    finally:
        proj.close()
```

**Step 4: run embedded suites to prove skip semantics**
Run: `python -m pytest tests/test_event_store.py tests/test_embedded_concurrency.py -v -rs --timeout=300 -p no:cacheprovider` — live tests show `SKIPPED`; all others green.

## Task 4: WS4 — CI job `test-concurrency-falkor` (pre-merge, real sidecar)

**Intent:** Issue O/I/T indicator 2 + target 2 — a CI job proves concurrent-writer atomicity on the real falkordb server, so the durable story is verified, not asserted.
**Acceptance:** New job in `.github/workflows/python-ci.yml`; runs ONLY the two live node IDs against a `falkordb/falkordb-server:latest` service container (requirepass falkordb, redis-cli healthcheck); job-scoped `TORTOISE_DB_URI`; skip-fail guard matching both pytest output formats (job fails if either live test SKIPPED); docs-consistency grep step; watchdog summary pattern; `timeout-minutes` cap. Other jobs unchanged (URI stays unset → live tests skip there).
**Files:**
- Modify: `.github/workflows/python-ci.yml`

**Step 1: add the job** (with the skip-fail guard matching pytest's ACTUAL output formats — verified empirically on pytest 8.x: the `-v` progress line is `nodeid SKIPPED (reason)` (name BEFORE the word), and the `-rs` summary is `SKIPPED [N] tests/file.py:line: reason` (file:line, no nodeid). The guard greps BOTH formats and also asserts exactly 2 skip-summary lines never appear):

```yaml
  test-concurrency-falkor:
    # #942 target 2: prove concurrent-writer atomicity on the REAL FalkorDB
    # sidecar (the durable path docs now default to). Runs ONLY the two live
    # docker:// tests — never whole files (test_embedded_concurrency.py is
    # also in test-slow, and whole-file runs would start embedded redislite
    # servers against the live 6379 — the #798 collision mode). The -rs
    # skip-fail guard turns a future skip regression RED instead of green.
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      TORTOISE_DB_URI: "docker://:falkordb@localhost:6379/tortoise"
    services:
      falkordb:
        image: falkordb/falkordb-server:latest
        env:
          REDIS_ARGS: "--requirepass falkordb"
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli -a falkordb ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install package + test extras
        # [test,embeddings] — NOT just [test]: the pre-cache step imports
        # sentence_transformers (embeddings extra), and create_point calls
        # compute_embedding. Matches the sibling jobs exactly.
        run: pip install -e '.[test,embeddings]'
      - name: Cache HF embedding model (all-MiniLM-L6-v2)
        # Sibling parity (test/test-slow both cache BEFORE pre-caching) —
        # without the restore step every run downloads ~90MB.
        uses: actions/cache@v4
        with:
          path: ~/.cache/huggingface
          key: hf-embedding-cache-v1-${{ runner.os }}
          restore-keys: |
            hf-embedding-cache-v1-${{ runner.os }}-
      - name: Pre-cache embedding model (all-MiniLM-L6-v2)
        # Same best-effort contract as the sibling jobs: no mid-suite HF
        # access; the live seq test exercises compute_embedding via create_point.
        continue-on-error: true
        run: |
          python - <<'EOF'
          import os, time
          os.environ["HF_HUB_OFFLINE"] = "1"
          from sentence_transformers import SentenceTransformer
          try:
              SentenceTransformer("all-MiniLM-L6-v2")
              print("embedding model: cached, no download needed")
          except Exception:
              print("embedding model: not cached — downloading (with retries)")
              del os.environ["HF_HUB_OFFLINE"]
              for attempt in range(1, 6):
                  try:
                      SentenceTransformer("all-MiniLM-L6-v2")
                      print("embedding model: downloaded")
                      break
                  except Exception as e:
                      print(f"download attempt {attempt}/5 failed: {e}")
                      if attempt == 5:
                          raise
                      time.sleep(10 * attempt)
          EOF
      - name: Run live concurrency tests (real FalkorDB sidecar)
        env:
          HF_HUB_OFFLINE: "1"
          TRANSFORMERS_OFFLINE: "1"
        run: |
          set +e
          timeout -s INT -k 10 10m stdbuf -oL python -m pytest \
            tests/test_event_store.py::test_seq_is_monotonic_under_concurrency_live_falkor \
            tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes \
            -v -rs --timeout=300 -p no:cacheprovider --maxfail=10 -rfE --durations=15 > /tmp/pytest.log 2>&1
          rc=$?
          tail -n 120 /tmp/pytest.log
          # Skip-fail guard: live tests must RUN here, never skip (vacuity).
          # Matches BOTH pytest output formats (-v progress: "nodeid SKIPPED";
          # -rs summary: "SKIPPED [N] tests/file.py:...").
          if grep -E '(test_seq_is_monotonic_under_concurrency_live_falkor|test_concurrent_writers_live_falkor_no_lost_writes) SKIPPED|SKIPPED \[[0-9]+\] tests/(test_event_store|test_embedded_concurrency)\.py' /tmp/pytest.log; then
            echo "❌ live tests SKIPPED — the job would be green while testing nothing"
            rc=1
          fi
          echo "==================== pytest exit code: $rc ===================="
          exit $rc
      - name: Validate docker-compose.yml (ports + header edits)
        # T1 Step 6 ships with zero other verification — no workflow validates
        # compose today. ubuntu-latest has docker + the repo checked out.
        run: docker compose config -q && echo "compose config OK"
      - name: Docs-consistency guard (embedded must never be "default" again)
        # #942 T1 enforcement: the flipped onboarding surfaces must not regress.
        # Runs even when the pytest step fails (if: always()) — it is the
        # cheap gate that keeps the flip honest. Scope = the SAME 5 files as
        # T1 Step 9. EXCLUDED by design: docs/plans|epics|migrations (historical
        # records), website/docs.html (hosted sample), graph-scripts/setup.py
        # (legacy installer on a deliberately distinct port).
        if: always()
        run: |
          HITS=$(grep -rn "Embedded (default)\|recommended to start\|multi-node\|16379\|passwordless URI is\|docker://localhost:6379\|docker://:@localhost:6379" README.md docs/quickstart-selfhosted.md docs/infra-runbook.md website/self-hosted.html .env.example || true)
          if [ -n "$HITS" ]; then
            echo "❌ embedded-first/16379 leftovers in flipped onboarding docs:"
            echo "$HITS"
            exit 1
          fi
          echo "docs-consistency guard OK"
```

**Step 2: verify no other job inherits the URI**
Confirm the workflow-level `env:` block contains no `TORTOISE_DB_URI` (it must stay job-scoped — `test`/`test-slow` run URI-unset so live tests skip there).

**Step 3: local dry-run of the wiring (no docker on this machine)**
Validate YAML: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/python-ci.yml')); print('yaml ok')"`. The CI run itself proves the job (docker unavailable locally — documented in the final report).

**Step 4: confirm no other job inherits the URI or the docs-consistency guard's assumptions**
The workflow-level `env:` block must stay free of `TORTOISE_DB_URI`; the docs-consistency grep must pass against the flipped files before the PR opens (run it locally in Task 1 Step 9).

## Task 5: Final verification + CHANGELOG

**Intent:** Red-green evidence, docs consistency, and the user-facing changelog note.
**Acceptance:** All affected suites green locally; banner emitted (verified by the new tests); CHANGELOG entry added; git state clean and ready for commit-workflow.
**Files:**
- Modify: `CHANGELOG.md`

**Step 1: run the affected suites**
`python -m pytest tests/test_cli_serve.py tests/test_selfhost.py tests/test_mcp_server.py tests/test_cli_context.py tests/test_bridge_mcp.py tests/test_event_store.py tests/test_embedded_concurrency.py -v -rs --timeout=300 -p no:cacheprovider` — expect: 8 new tests (serve banner + URI-negative + key-create + daemon banner + stdio banner + init note + 2 live) — the 6 banner/init tests PASS locally, the 2 live tests SKIP locally; zero regressions.

**Step 2: CHANGELOG entry**
Add under the current Unreleased section:

```markdown
### Self-hosted trust (#942)
- Durable multi-writer (docker compose sidecar) is now the documented default;
  embedded FalkorDBLite is explicitly single-writer / eval-only everywhere.
- New CI job `test-concurrency-falkor` proves concurrent-writer atomicity on a
  real FalkorDB sidecar (pre-merge).
- Embedded mode now emits loud single-writer warnings at `serve --http`, the
  daemon, the stdio MCP entrypoint (`tortoise serve` / `python -m tortoise.mcp_server`),
  `tortoise key create`, and `tortoise init`; `--auth tenant` on embedded is
  marked single-agent eval only.
- self-hosted.html comparison table is now a decision table.
```

**Step 3: full local smoke**
`python -m pytest tests/test_selfhost.py tests/test_cli_serve.py -q --timeout=300` green; `git status` shows only the planned files.

## Failure Modes
- Live tests skip in CI (env leak/removal) → the `-rs` skip-fail guard greps BOTH pytest output formats (`nodeid SKIPPED` in -v progress; `SKIPPED [N] tests/file.py` in -rs summary) and turns the job red; the skip helper is env-based by design (CI sets it, everything else skips).
- Banner breaks a stdout/stderr assertion → all banner output is stderr-only; the plan pins the known assertion surfaces (loopback-no-network-warning; key tests; stdio MCP handshake tests assert stdout).
- `_assert_test_graph` blocks the reset → graph names are test-prefixed (`test_live_seq_tortoise`, `test_live_mw_tortoise`).
- Service container won't start (healthcheck/password) → healthcheck + REDIS_ARGS copied verbatim from the working docker-compose.yml; the CI run is the proof.
- create_point embedding on the real server (embedding=None → vecf32 path unproven on falkordb-server) → embedding load failure degrades to None (ImportError caught); the HF pre-cache step + OFFLINE envs keep the model deterministic when reachable.
- Live seq test fails (non-contiguous union) on the REAL server → the red job IS the evidence (MERGE-serialization on falkordb-server is proven nowhere else). Fix direction: counter redesign (e.g. INCR on a key), NOT weakening the assertion.
- Legacy-trio drift: code defaults for FALKORDB_* remain 16379 (env-overridable; tests pin them). Fresh users without a .env must set FALKORDB_PORT=6379 or use the canonical TORTOISE_DB_URI — documented in T1 Step 7 + quickstart troubleshooting.

---

## PLAN REVISION — cycle 2 (controller response to plan-verify cycle-1 P0/P1s)

1. **P1 FIX — skip-fail guard pattern was dead** (both verifiers, empirically proven on pytest 8.x: `-v` progress = `nodeid SKIPPED (reason)`, `-rs` summary = `SKIPPED [N] tests/file.py:line` — `SKIPPED.*nodeid` never matches). Job now greps BOTH formats: `(nodeid) SKIPPED` and `SKIPPED \[[0-9]+\] tests/(test_event_store|test_embedded_concurrency)\.py`.
2. **P1 FIX — .env.example + README:119 + runbook §4.5 + quickstart §3 leftover `16379`/passwordless URIs** contradicting the new canonical — added to T1 (Step 7) with explicit rewrites; docs-consistency grep extended with `16379|passwordless URI is`.
3. **P1 FIX — nonexistent `_dispatch_serve_http` helper** — T2 Step 6 now uses the real pattern (`_patch_serve_runtime` + `main(["serve","--http"])`); key-create test drives main() directly (local_db fixture consumes capsys).
4. **P1 FIX — live-test samples missing imports** (`os`, `from conftest import _skip_unless_live_uri`) — added to T3 Steps 2-3.
5. **P2 FIX — stdio banner gap** (default serve mode; two MCP clients sharing an embedded DB = concurrent writers): banner now also emitted in `tortoise/mcp_server.py main()` embedded branch; shared single-sourced constant.
6. **P2 FIX — quickstart §3 env table / troubleshooting port remap** folded into T1 Step 7 sweep; 6379-collision symptom documented.
7. **P3 FIXES — banner-absence negative test** (URI branch); docs-consistency grep step added to the CI job; HF pre-cache + OFFLINE envs copied from sibling jobs; README default-column (`~/.tortoise/tortoise.db`) + compose header cross-ref fixed.

## PLAN REVISION — cycle 3 (controller response to plan-verify cycle-2 P0/P1s)

1. **P1 FIX — shared constant home pinned to `tortoise/_embedded.py`** (zero-import leaf; `selfhost.py` is a circular-import trap: `selfhost.py:32` imports `create_http_app` from `mcp_server` at module load, so `mcp_server` importing from `selfhost` = ImportError on the stdio entrypoint, masked in-process when selfhost imports first).
2. **P1 FIX — stdio banner condition is `not is_db_uri(uri)`** (bare-path TORTOISE_DB_URI is treated as embedded backward-compat in `_get_sdk` — must warn too), placed AFTER the sys.exit config-error guard; stdio banner test mechanics specified (monkeypatch `mcp_mod.mcp.run`, set TORTOISE_DB_PATH, assert stderr, import mcp_server first so the cycle can never be masked).
3. **P1 FIX — URI-negative test env ordering swapped** (patch FIRST, then setenv URI — `_patch_serve_runtime` delenvs unconditionally); `assert rc == 0` added.
4. **P1 FIX — CI docs-consistency guard scoped to the 5 flipped files** (blanket `docs/ website/` would red on ~11 untouched files incl. historical archives that must NOT be rewritten); T1 Step 7 extended: `.env.example` FALKORDB_PORT 16379→6379, infra-runbook.md:108 second occurrence, website/docs.html:147, graph-scripts/setup.py installer flip; T1 Step 9 grep matches the CI guard exactly.
5. **P2 FIXES — key-create test gets explicit env isolation (delenv URI + tmp DB path); troubleshooting entry drops the false "embedded suite port" clause (redislite binds unix sockets); `--durations=15` added to the job; CHANGELOG entry covers the stdio entrypoint; Task 5 suite includes tests/test_mcp_server.py; blast-radius note for remaining graph-scripts (legacy 16379, env-overridable, scoped out).**

## Cycle log
- plan-verify cycle 2: Verifier A: P0=0, P1=3 (guard scope, negative-test ordering, stdio test gap), P2=5, P3=2, P4=1. Verifier B: P0=0, P1=3 (same three, independently), P2=2 (stdio predicate, setup.py), P3=2, P4=1. Controller: all fixed, no ignores. Re-dispatching both verifiers (cycle 3).

## PLAN REVISION — cycle 4 (controller response to plan-verify cycle-3 P0/P1s)
1. **P1 FIX (process) — body/log desync**: the cycle-3 edit call was all-or-nothing (one edit failed → whole call aborted), so the log claimed fixes the BODY lacked. Task 2 Steps 1-5c and T1 Steps 7/9/10 + T4 install/guard steps rewritten in the body to match the log. Verifier A's catch; verified by re-grep.
2. **P1 FIX — key-create banner reuses the shared constant** (single text source; the "SINGLE-WRITER" assertion can no longer drift from Step 3's message).
3. **P1 FIX — CI job installs `'.[test,embeddings]'`** (sentence-transformers lives in the embeddings extra; the pre-cache step and create_point's embedding path need it; matches sibling jobs).
4. **P2 DECISIONS (documented, no code change)** — `website/docs.html:147` stays 16379 (hosted-API sample; hosted convention per #761 history) and `graph-scripts/setup.py` stays 16379 (legacy distinct-port installer; flipping collides with the compose publish). Both explicitly EXCLUDED in T1 Step 7 + CI guard comment.
5. **P2 FIX — durability wording**: compose header + selfhost.py #101 comment reworded to the post-#915 boundary ("durable for ONE process; concurrent writers lose data") — matches the banner framing.
6. **P3 FIXES — init-note test added (test_cli_context.py); stdio banner test file pinned (tests/test_mcp_server.py); guard step gets `if: always()`; guard comment documents exclusions.**

## Cycle log
- plan-verify cycle 3: Verifier A: P0=0, P1=4 (body/log desync ×3 + key-create case mismatch), P2=1, P4=1. Verifier B: P0=0, P1=2 (install extras; key-create case mismatch), P2=4 (setup.py flip contradiction — resolved by non-flip decision; docs.html hosted sample — resolved by exclusion; stale durability wording — fixed; guard scope comment — fixed), P3=3. Controller: all fixed or explicitly decided. Re-dispatching both verifiers (cycle 4).

## PLAN REVISION — cycle 5 (controller response to plan-verify cycle-4 P0/P1s)
1. **P1 FIX — stdio test `fake_run` binding**: instance-attribute functions are never bound; `def fake_run(self, **kw)` TypeErrors on the call (empirically proven by verifier). Now `def fake_run(**kw)` (method-shaped rejection noted in a comment).
2. **P1 FIX — quickstart Option B uses `-e REDIS_ARGS="--requirepass falkordb --appendonly yes"`** — the falkordb image entrypoint ignores command-line args (the repo's own compose header documents this lesson); a bare `--requirepass` run arg would silently start a PASSWORDLESS sidecar and the canonical URI would fail auth — the exact trust-killer #942 exists to prevent.
3. **P2 FIX — guard pattern extended** with `docker://localhost:6379|docker://:@localhost:6379` (passwordless canonical-port forms in the harness JS configs) in BOTH the CI guard and T1 Step 9 (byte-identical).
4. **P2 FIX — Task 5 suite + count**: adds tests/test_cli_context.py + tests/test_bridge_mcp.py; correct inventory: 8 new tests (6 pass locally, 2 live SKIP).
5. **P3 FIXES — Failure Modes**: live-seq red-on-real-server expectation (fix = counter redesign, not test weakening); legacy-trio 16379 code-default drift documented in T1 Step 7 + troubleshooting.
6. **P4 FIX — selfhost.py:31 → :32 line ref.**

## Cycle log
- plan-verify cycle 4: Verifier A: P0=0, P1=1 (fake_run TypeError), P2=1 (Task 5 suite/count), P4=1. Verifier B: P0=0, P1=1 (Option B REDIS_ARGS), P2=2 (Task 5 count; guard pattern gap), P3=2, P4=0. Controller: all fixed. Re-dispatching both verifiers (cycle 5).

## PLAN REVISION — cycle 6 (controller response to plan-verify cycle-5 P0/P1s)
1. **P1 FIX — selfhost.py `import sys`**: the file imports NO sys today; the snippet's `file=sys.stderr` would NameError at import in embedded mode (daemon crash in the exact mode it warns about; 7 `_client_for_env` tests + subprocess smoke red). The edit now mandates adding `import sys`.
2. **P1 FIX — quickstart §5 connect leads with the daemon URL** for compose users (`claude mcp add tortoise http://localhost:8000/mcp` / `.mcp.json` http block); stdio demoted to the no-Docker eval path labeled single-writer. The flip was previously half-done (install leads with compose, connect led to a SECOND embedded stdio server).
3. **P2 FIX — init-note test**: tmp TORTOISE_DB_PATH (no home-dir pollution) + `no_index=True` (no repo auto-index subprocess); noted there is no positive embedded-init precedent in test_cli_context.py.
4. **P2 FIX — infra-runbook §4.5**: concrete replacement text; corrected to ONE occurrence (the "defaulting…cross-ref" is a single line); authenticated-MCP block leads with the durable path, embedded+tenant demoted + labeled.
5. **P2 FIX — compose verification**: `docker compose config -q` step added to the CI job (docker-compose.yml was the only modified file with zero verification).
6. **P2 FIX — .env.example `TORTOISE_DB_PATH` comment** gets the single-writer boundary (file is in the guard's scan scope).
7. **P2 FIX — HF actions/cache restore step** added before pre-cache (sibling parity; ~90MB per run otherwise).
8. **P3/P4 — stdio test resets `mcp_mod.sdk/_sdk`; mcp_server line ref :310-313; dead `lock` var dropped from the live seq sample.**

## Cycle log
- plan-verify cycle 5: Verifier A: P0=0, P1=0, P2=0, P3=1, P4=3. Verifier B: P0=0, P1=2 (selfhost import sys; §5 connect flip half-done), P2=5, P3=1, P4=1. Controller: all fixed. Re-dispatching both verifiers (cycle 6).

<!-- research-path: docs/plans/2026-09-11-2863-oauth-code-atomicity.md -->

# OAuth auth-code / refresh-grant atomicity — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make `/oauth/token` never burn a grant without delivering a token: a control-plane failure in any single write window compensates itself, every failure is reported as a truthful typed OAuth error (retryable only when a retry provably works), and no infra error escapes the grant dispatch as a bare 500.

**Team:** organisation-design-team
**Role:** product-implementer

**Dependency map (AGENTS.md: record it, don't force serial):**

```
1 ──> 2 ──> 3 ──> {4, 5} ──> 6 ──> 7
```

4 and 5 are logically independent (different production functions, different failure windows) but
are **serialized deliberately**: both write the shared `tortoise/oauth.py` regions and the same test
module. Splitting the fault suite per concern to parallelize them was considered and rejected — the
suite shares one fixture layer and one control plane, and the `oauth.py` edits would collide.

**Architecture:** `_issue_tokens` becomes a 3-lane unit with a structural no-leak guarantee (intentional `OAuthError` re-raised; infra failures compensated then mapped to an internal `OAuthMintAborted(recovered)`; post-commit hygiene outside the handler). `exchange_auth_code` replaces its unguarded post-consume window with a `consumed` + `attempted_consume` flag handler that CAS-restores `used_at` only when the restoration is *observable*; `refresh_grant` wraps its pre-mint reads and unmasks the two revokes that currently swallow a terminal `OAuthError`. `hosted_api.oauth_token` gains a typed boundary whose last-resort net returns a coherent RFC 6749 `server_error` (a bug detector — every reachable control-plane failure is converted inside `oauth.py` first).

**Invariants this plan enforces — and their exact scope:**

| # | Invariant | Enforced by | Scope (do NOT over-read) |
|---|---|---|---|
| I1 | no `Exception` raised by a state write escapes `_issue_tokens` | lane 2's blanket catch + its nested guard | `BaseException` deliberately excluded (`KeyboardInterrupt`/`SystemExit`/future `CancelledError`) |
| I2 | the minted pair is never **disclosed** on a compensated failure | lane 2 always raises `OAuthMintAborted`, never `return`s | a row that committed *is* still live; it is inert (plaintext never disclosed) — cleanup is #3036 |
| I3 | `OAuthTemporarilyUnavailable` is emitted only when the grant is established still-usable | the Raise-Site Audit below (every site enumerated) | — |
| I4 | each conversion path emits ≤1 `sentry.capture_exception` per request | `_log_and_capture`'s owner table | an inventory over the enumerated paths; `revoke_token` is an unconverted hole deferred to #3026 |
| I5 | every failure raised **from the grant dispatch** returns an RFC 6749 §5.2 body | Task 6 | does **not** cover the two pre-existing bare shapes outside the dispatch — `:21887` `HTTPException(503, "OAuth not configured")` and `:21895` `HTTPException(400, "Invalid form body")`; both pre-existing, left as-is, owned by #3026 — plus the `_read_capped_body` 413 re-raised at `:21892`, a third bare shape on this endpoint |

Scope boundary (from `/tmp/2863-scope.md`): **no schema migration, no new RPC.** Out of scope: #3025 (transactional RPC — deletes this compensation), #3026 (sibling endpoints incl. the two bare shapes above), #3027 (redemption state / delayed-commit residual), #3036 (FK, retention/GC, inert-orphan hygiene).

### Pattern Research

> **Findings date:** 2026-09-11

**Library docs (preflight)** — no third-party deps in plan — skipped.

> Gate skipped: no new third-party dependency or API surface. Reuses in-repo patterns used 2+ times:
> FastAPI `TestClient` + `raise_server_exceptions=False`, the `tests/fake_control_plane.py`
> PostgREST double, the local-`HTTPServer` real-seam harness
> (`tests/test_supabase_control.py::TestClientConstruction.test_real_client_survives_multiple_queries`,
> ~:1832-1868, which already drives the real `SupabaseControlPlane.query` without PostgREST), and
> `tortoise/sentry.capture_exception`.
> Protocol research from `issue-scoping`: RFC 6749 §5.2 (six core token-error codes —
> `temporarily_unavailable` is **not** among them), §4.1.2.1/§4.2.2.1 (where it is), §8.5 (additional
> codes permitted), §5.2 charset (admits `_`). mcp 1.29.0 raises `OAuthTokenError` on any non-200 and
> `clear_tokens()` on a non-200 refresh — hence this plan's value is **DB-state integrity + a truthful
> contract**, not client retry behaviour.

### Integration Surface Map

| # | Surface | Failure modes | Where tested |
|---|---|---|---|
| S1 | `cp.query(...)` seam, **transport-level** | (a) HTTP ≥300; (b) transport error; (c) unparseable 2xx; (d) commit-then-lost-response | (a)(b)(c): Task 2 Step 6's real-seam handler returns 500 / non-JSON 200 and asserts the mapped typed error, not a raw exception; (d): Task 3's `after_mutation` cases. *The fake injector raises a Python `RuntimeError` for every case, so it cannot itself distinguish (a)-(c) — that is why the real-seam leg is a hard requirement.* |
| S2 | `oauth_codes.used_at` (claim + CAS restore) | (a) concurrent redemption; (b) CAS miss; (c) expired; (d) restore PATCH fails | Task 2 unit tests; existing `test_oauth_mcp.py` (a) |
| S3 | mint rows (2 POSTs + claim + prev-access revoke) | (a) partial mint; (b) claim contention; (c) ambiguous claim; (d) rollback fails; (e) observation fails | Task 3 (+ Task 5 for (c) at the endpoint) |
| S4 | grant-path reads: `oauth_clients` (`_verify_client_auth`), refresh-token SELECT, `teams`, `team_memberships`, `prev_access` | (a) failure **pre-consume** (constructive-clean → 503); (b) failure **post-consume** (→ CAS restore/terminal); (c) failure on the **first** read of the path (`oauth_clients`) — the exact leak a wrap starting at `:728` would miss | Task 4 (b), Task 5 (a)+(c), **parametrized over all five call sites** incl. `team_memberships` |
| S5 | `POST /oauth/token` boundary | (a) untyped 500 on a control-plane failure; (b) bare `{"detail":…}` on an unconverted exception; (c) divergence from the existing `_control_plane_unavailable` 503 convention | (a)(b)(c) Task 6. **(d) the pre-existing bare shapes at `:21887`/`:21895` are OUT OF SCOPE — #3026, no test here.** |
| S6 | Sentry/log observability | (a) swallowed infra failure; (b) **double capture**; (c) `capture_exception` itself raising | Task 3 (b, counts), Task 4/5 (a, per-path), Task 6 (a, c) |

**Bug pattern flags:** (a) fail-open re-arm — v4/v5 cleared `used_at` on a value another request wrote; the replacement observation is **read-only**. (b) error-masking — a revoke raise replacing a terminal `OAuthError`. (c) double-capture. (d) **fake-vs-seam divergence** — the CAS depends on `used_at=eq.<value>` round-tripping; a green fake-only suite is not evidence about production (Task 2 Step 6 pins the dialect; the value leg is a named fail-closed residual).

### Raise-Site Audit (I3 — every 503 and every terminal)

| Raise site | Basis | Signal |
|---|---|---|
| `exchange_auth_code`, `not attempted_consume` | constructive-clean (pure read) | 503 |
| `exchange_auth_code`, `_consume_state == "unconsumed"` | observed | 503 |
| `exchange_auth_code`, post-consume `except Exception` + `_restore_code` True | observed (CAS representation) | 503 |
| `exchange_auth_code`, `OAuthMintAborted(recovered=True)` + `_restore_code` True | observed | 503 |
| `refresh_grant`, pre-mint wrap | constructive-clean (nothing consumed) | 503 |
| `refresh_grant`, `OAuthMintAborted(recovered=True)` | observed (zero live rows + `prev_refresh` unclaimed) | 503 |
| all `OAuthError`; `_restore_code` False; `_consume_state != "unconsumed"`; `recovered=False` | dead / unobservable | terminal `invalid_grant` |
| `oauth_token` last-resort net | unreachable-by-design bug detector | 500 `server_error` |

### Verification Plan

| Layer | Depth | Rationale |
|---|---|---|
| unit | full | the fault matrix via the fake control plane |
| integration (dialect contract, **no live DB**) | **required** | Task 2 Step 6 pins the emitted query encoding + the seam's own ≥300 / unparseable-2xx branches against a local `HTTPServer` |
| integration (live PostgREST, *value* round-trip) | deferred → **named** | fail-closed; carried on #2863 (see Open Residuals) |
| e2e / ux | n/a | no browser surface (`UX_RATING = low`) |
| observability | full | per-path `caplog` + capture-**count** assertions |

**Tech Stack:** Python 3.12, FastAPI/Starlette `TestClient`, pytest, `FakeControlPlane`, the `tests/test_supabase_control.py` local-`HTTPServer` harness, `tortoise/sentry`.

---

### Task 1: Test harness — fault-injection hook + the shared fixture layer

**Depends on:** nothing.
**Intent:** Make every window reachable in-process (including **commit-then-lost-response** and a
failure of a *specific call site*, which a raise-on-N injector cannot express), and build the fixture
layer by **reusing** the helpers `tests/test_oauth_mcp.py` already has — not by re-implementing them.
**Acceptance:** `fail_query` raises before or after a matched call, matched on `(table, method, select,
filters)`, N times; `tests/test_oauth_token_fault.py` collects with no undefined name; the new file's
client is a `TestClient` with `raise_server_exceptions=False` (the repo `api_client` fixture defaults
to `True`, which would propagate the injected exception instead of yielding the response).

**Files:**
- Modify: `tests/fake_control_plane.py` (`query`, ~line 571; add `fail_query`/`_take_fault`)
- Create: `tests/test_oauth_token_fault.py`

**Step 1: Write the failing test** (in the new file):

```python
def test_fault_hook_raises_before_and_after_mutation():
    from tests.fake_control_plane import FakeControlPlane
    cp = FakeControlPlane()
    cp.query("oauth_codes", method="POST", json_body={"code_hash": "h", "used_at": None})

    cp.fail_query(table="oauth_codes", method="GET", times=1, exc=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", filters=[("code_hash", "eq", "h")])
    assert cp.query("oauth_codes", filters=[("code_hash", "eq", "h")])   # times consumed

    cp.fail_query(table="oauth_codes", method="PATCH", after_mutation=True,
                  exc=RuntimeError("lost response"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", method="PATCH", select=["used_at"],
                 filters=[("code_hash", "eq", "h")], json_body={"used_at": "T"})
    assert cp.query("oauth_codes", filters=[("code_hash", "eq", "h")])[0]["used_at"] == "T"


def test_fault_hook_discriminates_call_sites_by_select_shape():
    """The observation SELECT and the consume PATCH share table+method; only `select`
    distinguishes them — this is what makes the CAS/observation tests possible."""
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                  times=1, exc=RuntimeError("observation only"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                 filters=[], json_body={"used_at": None})
    assert cp.query("oauth_codes", method="PATCH", select=["id"], filters=[],
                    json_body={"revoked_at": "T"}) == []      # different select → no fault
```

**Step 2: Run** `uv run pytest tests/test_oauth_token_fault.py -v` → FAIL (`AttributeError: 'FakeControlPlane' object has no attribute 'fail_query'`).

**Step 3: Implement** — `FakeControlPlane.__init__`: `self._faults: list[dict] = []`. Rename the existing `query` body to `_query_impl`; `query` becomes:

```python
    def fail_query(self, *, table=None, method=None, select=None, filters=None,
                   match=None, times: int = 1, after_mutation: bool = False,
                   exc: Exception | None = None) -> None:
        """Install a fault injector (#2863). The first unexhausted injector whose
        predicate accepts the call fires. Provide either the simple attribute matches
        or (preferred) ``match=fn(table, method, select, filters) -> bool`` — a callable
        does NOT go stale when a production select list changes (two review cycles were
        lost to hand-written select literals that never matched). ``after_mutation=True``
        applies the write THEN raises — the commit-then-lost-response case a plain
        raise-on-N cannot express."""
        self._faults.append({
            "table": table, "method": method, "select": select, "filters": filters,
            "match": match, "consumed": False,
            "times": times, "after_mutation": after_mutation,
            "exc": exc or RuntimeError("Supabase unreachable (simulated)"),
        })
        _FAULT_CPS.append(self)          # module-level registry: the guard's only view
                                         # of a bare `FakeControlPlane()` built by a test

    def unfired_faults(self) -> list[dict]:
        """Injectors that did not do what the test intended — either never fired, or
        fired fewer times than `times` asked for. A test MUST fail on these; a stale
        matcher is otherwise a silent green test."""
        return [f for f in self._faults if not f["consumed"] or f["times"] > 0]

    def _take_fault(self, table, method, select, filters):
        for fault in self._faults:
            if fault["times"] <= 0: continue
            # The table filter applies INDEPENDENTLY of `match` — otherwise a
            # shape-scoped injector would also fire on the other token table
            # (the rollback and lane 3 are both select-less PATCHes).
            if fault["table"] is not None and fault["table"] != table: continue
            if fault["match"] is not None:
                if not fault["match"](table, method, select, filters): continue
            if fault["method"] is not None and fault["method"] != method: continue
            if fault["select"] is not None and list(fault["select"]) != list(select or []): continue
            if fault["filters"] is not None and list(fault["filters"]) != list(filters or []): continue
            fault["times"] -= 1
            fault["consumed"] = True
            return fault
        return None

    def query(self, table, *, select=None, filters=None, method="GET",
              json_body=None, order=None, limit=None):
        fault = self._take_fault(table, method, select, filters)
        if fault is not None and not fault["after_mutation"]:
            raise fault["exc"]
        result = self._query_impl(table, select=select, filters=filters, method=method,
                                  json_body=json_body, order=order, limit=limit)
        if fault is not None:
            raise fault["exc"]
        return result
```

**Step 4: Run** → PASS. **Regression** — the double is widely depended on, including query-overriding
subclasses the OAuth suite would never touch:

```bash
uv run pytest tests/test_oauth_token_fault.py tests/test_fake_control_plane.py \
               tests/test_supabase_control.py tests/test_oauth_mcp.py -q
```

**Step 5: Commit** — `feat(tests): fault-injection hook + fixtures for OAuth window tests (#2863)`

#### Task 1b — the shared fixture layer (same file, same commit)

**Reuse, do not re-implement.** `tests/test_oauth_mcp.py` already provides the app wiring; import it:

```python
from tests.test_oauth_mcp import _U1, _auth_code_flow, _enable_supabase, _exchange, _pkce, _register_client
from tests.fake_control_plane import FakeControlPlane

_CLIENT_ID = "client-1"                      # must equal the seeded oauth_clients.id
_REDIRECT = "https://app.example/cb"
```

Module imports the file also needs (all already used in-repo) — note `oauth` and `copy`,
which the test bodies use:
`json`, `logging`, `secrets`, `threading`, `base64`, `hashlib`, `tempfile`, `os`,
`datetime`/`timedelta`/`timezone`, `pytest`, `pytest.fixture`,
`from http.server import BaseHTTPRequestHandler, HTTPServer`,
`from fastapi.testclient import TestClient`,
`from tortoise.hosted_api import app`,
`from tortoise.supabase_control import SupabaseControlPlane`,
`from tortoise.oauth import _sha256, _expires_iso, REFRESH_TOKEN_TTL_S, AUTH_CODE_TTL_S`,
`from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane`,
`from tests._http_fixtures import patched_tortoise_sdk` (the helper `api_client` uses),
`import tortoise.oauth as oauth`, `import copy`.

**The base tables are the part three review cycles kept missing.** Every endpoint path calls
`_verify_client_auth` → `_client_row` (`oauth_clients`) FIRST, then `_assert_team_usable` (`teams`),
and the refresh path then `membership_for_user_team` (`team_memberships`, `status=eq.active`).
An unseeded table yields 401/403 **before** any injected fault is reached — so the fixture seeds all
three. `team_memberships.user_id` MUST be a UUID (the fake's `UUID_FILTER_COLUMNS` raises
`RuntimeError(...HTTP 400)` for a non-UUID, which the pre-mint wrap would convert to a 503 and hide
the real assertion). Use `tests/test_oauth_mcp.py`'s `_U1`.

```python
@pytest.fixture
def fault_client(monkeypatch):
    """(TestClient with raise_server_exceptions=False, cp, seeded). Mirrors `api_client`
    (test_oauth_mcp.py:167) but must NOT reuse it: that fixture yields TestClient(app),
    where raise_server_exceptions defaults True, so an injected fault would propagate
    instead of becoming a response."""
    cp = FakeControlPlane()
    _enable_supabase(monkeypatch, cp)
    _seed_base_tables(cp)
    with tempfile.TemporaryDirectory() as tmpdir:
        with patched_tortoise_sdk(os.path.join(tmpdir, "oauth.db")), \
             TestClient(app, raise_server_exceptions=False) as tc:
            yield tc, cp


_FAULT_CPS: list[FakeControlPlane] = []


@pytest.fixture(autouse=True)
def _no_silent_faults():
    """THE recurrence guard for this whole suite. A fault matcher that does not match
    the real call shape is a test that pins nothing — and it passes. `fail_query`
    registers every control plane it is called on, so bare-`FakeControlPlane` tests are
    covered too (not just `fault_client` ones). Any injector left unfired (or under-fired
    vs its `times`) fails the test loudly."""
    _FAULT_CPS.clear()
    yield
    stale = [f for cp in _FAULT_CPS for f in cp.unfired_faults()]
    if stale:
        pytest.fail(f"fault injectors never fired (stale match): {stale}")


def _seed_base_tables(cp) -> None:
    """oauth_clients (id=_CLIENT_ID, token_endpoint_auth_method='none'), teams
    (id='t1'), team_memberships (user_id=_U1 UUID, team_id='t1', status='active')."""
    cp.tables.setdefault("oauth_clients", []).append({
        "id": _CLIENT_ID, "client_name": "test", "redirect_uris": [_REDIRECT],
        "scope": "mcp", "token_endpoint_auth_method": "none",
        "created_at": "2026-01-01T00:00:00+00:00"})
    cp.tables.setdefault("teams", []).append({
        "id": "t1", "tier": "Team", "suspended_at": None, "flagged_at": None,
        "email": "t@example.com"})
    cp.tables.setdefault("team_memberships", []).append({
        "user_id": _U1, "team_id": "t1", "role": "owner", "status": "active"})


def _live(cp, table: str) -> list[dict]:
    return [r for r in cp.tables.get(table, []) if r.get("revoked_at") is None]


def _seed_code(cp, code="code-1", *, verifier=None, client_id=_CLIENT_ID,
               user_id=_U1, team_id="t1", redirect_uri=_REDIRECT, used_at=None,
               expires_in=600) -> str:
    """Insert an oauth_codes row and RETURN the PKCE verifier (a code seeded without
    its verifier 400s on PKCE before ever reaching the injected fault). Uses the
    production encoders so the hash and timestamp formats match."""
    verifier = verifier or _pkce()[0]
    cp.tables.setdefault("oauth_codes", []).append({
        "code_hash": _sha256(code), "client_id": client_id, "user_id": user_id,
        "team_id": team_id, "redirect_uri": redirect_uri,
        "code_challenge": _s256(verifier), "code_challenge_method": "S256",   # see below
        "scope": "mcp", "resource": None,
        "expires_at": _expires_iso(expires_in), "used_at": used_at,
        "created_at": _expires_iso(0)})
    return verifier


def _s256(verifier: str) -> str:
    """base64url(sha256(verifier)), unpadded — the same transform `_verify_pkce` applies.
    Compare within ONE `_pkce()` pair (it generates a fresh pair per call, so comparing
    two invocations can never hold): `v, c = _pkce(); assert _s256(v) == c`."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                    ).rstrip(b"=").decode()


def _seed_refresh_token(cp, token="rt-1", **over) -> tuple[str, str]:
    """Insert an oauth_refresh_tokens row. RETURNS (row_id, plaintext_token) — ONE
    VALUE CANNOT BE BOTH: `refresh_grant` looks the row up by `token_hash` but lane 3
    and the claim PATCH address it by `id`. Column defaults: id=secrets.token_urlsafe(16),
    client_id=_CLIENT_ID, user_id=_U1, team_id='t1', scope='mcp',
    expires_at=_expires_iso(REFRESH_TOKEN_TTL_S), revoked_at=None, rotated_from=None."""
    token = over.pop("token", token)
    row = {"id": over.pop("id", secrets.token_urlsafe(16)), "token_hash": _sha256(token),
           "client_id": _CLIENT_ID, "user_id": _U1, "team_id": "t1", "scope": "mcp",
           "expires_at": _expires_iso(REFRESH_TOKEN_TTL_S), "revoked_at": None,
           "rotated_from": None, "created_at": _expires_iso(0), **over}
    cp.tables.setdefault("oauth_refresh_tokens", []).append(row)
    return row["id"], token


def _seed_access_token(cp, *, refresh_id: str) -> str:
    """Insert an oauth_access_tokens row with `refresh_token_id=refresh_id` and return
    its `id` (this is what `refresh_grant`'s prev_access SELECT matches)."""
    row_id = secrets.token_urlsafe(16)
    cp.tables.setdefault("oauth_access_tokens", []).append({
        "id": row_id, "token_hash": _sha256("at-" + row_id), "client_id": _CLIENT_ID,
        "user_id": _U1, "team_id": "t1", "scope": "mcp",
        "expires_at": _expires_iso(3600), "revoked_at": None,
        "refresh_token_id": refresh_id, "created_at": _expires_iso(0)})
    return row_id


def _post_refresh(tc, cp, token: str, **over):
    return tc.post("/oauth/token", data={"grant_type": "refresh_token",
                                        "refresh_token": token,
                                        "client_id": _CLIENT_ID, **over})
```

**Acceptance for 1b:** `uv run pytest tests/test_oauth_token_fault.py --collect-only -q` lists every
test named in Tasks 2–6 with no import/name error; and a no-fault probe
(`_seed_code` → `_post_code` → **200**, `_seed_refresh_token` → `_post_refresh` → **200**) proves the
fixtures do not themselves 400/401.

#### Fault-matching rule (read before writing any Task 2–6 test)

`_take_fault` matches on exact attribute equality, so a hand-written `select` list goes stale the
moment production changes — three review cycles were lost to that. **Match with a callable**, not a
literal, and rely on the autouse `_no_silent_faults` guard to fail any non-firing injector. The four
`oauth.py` call sites this suite must discriminate:

| Call site | table | method | discriminator |
|---|---|---|---|
| `_consume_code` (atomic claim) | `oauth_codes` | PATCH | `lambda t,m,s,f: m=="PATCH" and s and "code_challenge" in s` |
| `_restore_code` (CAS restore) | `oauth_codes` | PATCH | `select=["used_at","expires_at"]` (stable, 2 cols) |
| claim PATCH (rotation) | `oauth_refresh_tokens` | PATCH | `select=["id"]` |
| `_rollback_minted` (soft-revoke) | both token tables | PATCH | `lambda t,m,s,f: m=="PATCH" and not s` (select-less) |
| lane 3 prev-access revoke | `oauth_access_tokens` | PATCH | `lambda t,m,s,f: m=="PATCH" and not s` |

### Task 2: Vocabulary, observability owner, and the read-only observation/CAS primitives

**Depends on:** Task 1.
**Intent:** Give the contract its vocabulary, its one-capture **owner table**, and its two read-only
primitives. `recovered` must be *established*, never assumed; a zero-row rollback PATCH must be a
**success** (a missing row is the expected idempotent no-op — "raise on empty" would invert the
protocol and re-burn the very windows the fix exists for).
**Acceptance:** `_consume_state` returns exactly **three** outcomes (`unconsumed`/`consumed`/`unknown`)
with `"unknown"` on any raise, and performs no write; `_restore_code` is CAS-guarded **and**
expiry-guarded, returning `False` on any raise/empty; `_rollback_minted(..., capture=)` never raises;
the emitted PostgREST encoding is pinned by a real-seam test.

**Files:**
- Modify: `tortoise/oauth.py` — top-of-module `import logging` (never inline after a class body: ruff `E402`/`I001`), `logger = logging.getLogger("tortoise.oauth")`, `OAuthError` docstring (`:71-84` → status may also be 500/503), new classes after it, helpers near `_consume_code` (`~:506`)
- Test: `tests/test_oauth_token_fault.py`, `tests/test_supabase_control.py`

**Step 1: Write the failing unit tests** (concrete bodies — no stubs):

```python
@pytest.mark.parametrize("used_at,expires_in,expected", [
    (None, 600, "unconsumed"),
    ("2026-01-01T00:00:00+00:00", 600, "consumed"),
    (None, -10, "consumed"),
])
def test_consume_state_outcomes(used_at, expires_in, expected):
    cp = FakeControlPlane()
    _seed_code(cp, "c", used_at=used_at, expires_in=expires_in)
    assert oauth._consume_state(cp, "c") == expected


def test_consume_state_missing_row_is_consumed():
    assert oauth._consume_state(FakeControlPlane(), "nope") == "consumed"


def test_consume_state_unknown_on_raise_and_never_writes():
    cp = FakeControlPlane(); _seed_code(cp, "c")
    before = copy.deepcopy(cp.tables)
    cp.fail_query(table="oauth_codes", method="GET", times=1, exc=RuntimeError("down"))
    assert oauth._consume_state(cp, "c") == "unknown"
    assert cp.tables == before              # read-only: no clobber of a concurrent claim


def test_restore_code_cas_match_miss_expiry_and_raise():
    cp = FakeControlPlane(); _seed_code(cp, "c", used_at="T1")
    assert oauth._restore_code(cp, "c", "T1") is True
    assert cp.tables["oauth_codes"][0]["used_at"] is None
    cp = FakeControlPlane(); _seed_code(cp, "c", used_at="T1")
    assert oauth._restore_code(cp, "c", "T2") is False                       # CAS miss
    cp = FakeControlPlane(); _seed_code(cp, "c", used_at="T1", expires_in=-10)
    assert oauth._restore_code(cp, "c", "T1") is False                       # over TTL
    cp = FakeControlPlane(); _seed_code(cp, "c", used_at="T1")
    cp.fail_query(table="oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                  times=1, exc=RuntimeError("down"))
    assert oauth._restore_code(cp, "c", "T1") is False                       # raise → terminal


def test_rollback_minted_zero_rows_is_success_not_failure():
    """A never-inserted row must NOT be reported as a failed rollback."""
    cp = FakeControlPlane()
    oauth._rollback_minted(cp, [("oauth_refresh_tokens", "never-existed")], "now",
                           capture=False)                      # must not raise
    assert oauth._mint_observably_clean(cp, [("oauth_refresh_tokens", "never-existed")]) is True


def test_expiry_forms_agree_and_the_iso_encoders_are_offset_form(monkeypatch):
    """Pin the FORMAT invariant (the fake's `gt` is a lexical compare), not an
    absolute timestamp: a hardcoded literal cannot separate the two suffix forms
    because the date/time prefix decides the comparison first. Freeze the clock and
    build a definitely-future instant in both forms."""
    monkeypatch.setattr(oauth, "_now", lambda: FIXED_NOW)
    assert oauth._now_iso().endswith("+00:00") and oauth._expires_iso(60).endswith("+00:00")
    future = FIXED_NOW + timedelta(seconds=600)
    forms = [future.isoformat(), future.strftime("%Y-%m-%dT%H:%M:%S") + "Z"]
    results = []
    for rendered in forms:
        cp = FakeControlPlane()
        _seed_code(cp, "c", used_at="T1")
        cp.tables["oauth_codes"][0]["expires_at"] = rendered
        results.append((oauth._restore_code(cp, "c", "T1"), oauth._consume_state(cp, "c")))
    assert results[0][0] is True and results[1][0] is True      # future in BOTH forms
    assert results[0][1] == results[1][1] == "unconsumed"       # lexical and semantic agree
```

(`FIXED_NOW` = a module constant `datetime(2026, 1, 1, tzinfo=timezone.utc)`.)

**Step 2: Run** → FAIL (`AttributeError: module 'tortoise.oauth' has no attribute '_consume_state'`).

**Step 3: Implement** — vocabulary + owner table:

```python
# top-of-module, with the other imports
import logging
logger = logging.getLogger("tortoise.oauth")

# Transient-failure contract (#2863). /oauth/token's consumer (mcp 1.29.0) parses the
# RFC 6749 §5.2 body, so it must NOT reuse `hosted_api._control_plane_unavailable()`'s
# FastAPI {"detail": {"error_code": ...}} shape. Same STATUS (503), different driver:
# that is deliberate, and Task 6 Step 4 pins the status parity. §8.5 permits the extra
# error code; §5.2's charset admits "_".


class OAuthMintAborted(Exception):
    """Internal (#2863): `_issue_tokens` failed after possibly writing.
    `recovered` is True iff an observation confirmed the mint left no live minted row
    (and, on the rotation path, left the previous refresh token unclaimed). Never
    escapes `oauth.py` — callers map it to a typed `OAuthError`."""

    def __init__(self, recovered: bool, cause: str = ""):
        super().__init__(cause or "token mint aborted")
        self.recovered = recovered


class OAuthTemporarilyUnavailable(OAuthError):
    """503 `temporarily_unavailable` — raised ONLY when the grant is established
    still-usable: by observation where a write may have landed, or constructively
    where no write was attempted. Never on an unobserved write state."""

    def __init__(self, error_description: str = "Temporary control-plane failure — retry."):
        super().__init__(503, "temporarily_unavailable", error_description)


def _log_and_capture(exc: BaseException, *, where: str) -> None:
    """One WARNING + at most one Sentry capture for a conversion path. Must never raise.

    OWNER TABLE (I4 — never capture twice for one request):
      lane 2 of `_issue_tokens`        → the single capture of the TRIGGERING exception
      lane 1 loser rollback (capture=True)  → the single capture for the loser path
                                               (nothing else captures there)
      lane 2 rollback/observation (capture=False) → log only (lane 2 captured the trigger)
      lane 3 prev-access revoke        → log only (non-decision-bearing hygiene)
      the two correction-#8 revokes    → each the single capture for its terminal path
      `exchange_auth_code` / `refresh_grant` pre-consume/pre-mint `except Exception`
                                       → this call IS the single capture for that path
      `oauth_token` boundary           → this call IS the single capture for that path
    """
    try:
        logger.warning("oauth: %s failed: %s", where, exc, exc_info=True)
    except Exception:  # noqa: BLE001 — logging never breaks the response
        pass
    try:
        from tortoise.sentry import capture_exception as _capture
        _capture(exc, tags={"component": "oauth", "where": where})
    except Exception:  # noqa: BLE001 — capture never breaks the response
        pass


def _consume_state(cp, code: str) -> str:
    """READ-ONLY observation of a code's redemption state (#2863).

    "unconsumed" iff the row exists, is unclaimed and unexpired (a retry provably
    works); "consumed" for any other observed state (non-NULL `used_at`, no row,
    expired); "unknown" on any failure of the read OR its predicate.

    Performs NO write — this is what separates it from the withdrawn v4/v5 re-arm
    helpers, which cleared `used_at` and could clobber a concurrent claim.
    """
    try:
        rows = cp.query("oauth_codes", select=["used_at", "expires_at"],
                        filters=[("code_hash", "eq", _sha256(code))])
        if not rows or rows[0].get("used_at") is not None:
            return "consumed"
        expires = _parse_ts(rows[0].get("expires_at"))
        if expires is None or expires < _now():
            return "consumed"
        return "unconsumed"
    except Exception as exc:  # noqa: BLE001 — any raise ⇒ conservative terminal
        logger.warning("oauth: consume-state observation failed: %s", exc)
        return "unknown"


def _restore_code(cp, code: str, expected) -> bool:
    """CAS re-arm (#2863): clear `used_at` ONLY if it still holds the value this
    request wrote, and only while the code is still redeemable.

    True iff the re-arm is confirmed observable. Any raise / empty result / None
    expectation ⇒ False (terminal) — never a retryable signal on unobserved state.
    The expiry filter mirrors `_consume_state`: the failure path can spend ~20 s
    before the re-arm, so a near-TTL code must not be re-armed into a 503 whose retry
    then returns expired `invalid_grant`.
    """
    if expected is None:
        return False
    try:
        rows = cp.query("oauth_codes", method="PATCH",
                        select=["used_at", "expires_at"],
                        filters=[("code_hash", "eq", _sha256(code)),
                                 ("used_at", "eq", expected),
                                 ("expires_at", "gt", _now_iso())],
                        json_body={"used_at": None})
        return bool(rows)
    except Exception as exc:  # noqa: BLE001 — terminal on unobservable state
        logger.warning("oauth: code re-arm failed: %s", exc)
        return False


def _rollback_minted(cp, minted: list[tuple[str, str]], now: str, *, capture: bool) -> None:
    """Idempotent soft-revoke by id of every row this request may have written.

    A PATCH filtered by `id` is a VERIFIED no-op on a missing row in both seams
    (real: `Prefer: return=minimal` → `[]`; fake: `select is None` → `[]`), so zero
    affected rows is the EXPECTED SUCCESS for a write that never committed — this
    function must never raise on an empty result. Each row is attempted in its own
    try/except. `capture=True` is for lane 1 (nothing else captures on that path);
    lane 2 passes False because it already captured the trigger (I4).
    """
    for table, row_id in minted:
        try:
            cp.query(table, method="PATCH", filters=[("id", "eq", row_id)],
                     json_body={"revoked_at": now})
        except Exception as exc:  # noqa: BLE001
            if capture:
                _log_and_capture(exc, where=f"loser rollback {table}")
            else:
                logger.warning("oauth: mint rollback failed for %s/%s: %s", table, row_id, exc)


def _mint_observably_clean(cp, minted: list[tuple[str, str]]) -> bool:
    """True iff no minted row is live. Any raise → False (terminal, never fail-open)."""
    try:
        for table, row_id in minted:
            rows = cp.query(table, select=["id"],
                            filters=[("id", "eq", row_id), ("revoked_at", "is", None)])
            if rows:
                return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("oauth: mint observation failed: %s", exc)
        return False


def _prev_refresh_unclaimed(cp, prev_refresh: dict) -> bool:
    """True iff the presented refresh token is still unrevoked. Any raise → False."""
    try:
        rows = cp.query("oauth_refresh_tokens", select=["revoked_at"],
                        filters=[("id", "eq", prev_refresh["id"])])
        return bool(rows) and rows[0].get("revoked_at") is None
    except Exception as exc:  # noqa: BLE001
        logger.warning("oauth: prev-refresh observation failed: %s", exc)
        return False
```

**Step 4: Run** the unit tests → PASS.

**Step 5: Real-seam dialect contract test** — a **hard requirement**, no escape hatch. The harness
already drives the real client without PostgREST
(`tests/test_supabase_control.py::TestClientConstruction.test_real_client_survives_multiple_queries`):

There is **no reusable fixture** for this in the repo — `tests/test_supabase_control.py`'s
`test_real_client_survives_multiple_queries` builds an inline `HTTPServer(("127.0.0.1", 0), Handler)`
with only `do_GET`. Task 2 must add the fixture (a small, self-contained addition to the new test
file; do not refactor the existing suite):

```python
@pytest.fixture
def capture_server():
    """Start a local HTTPServer, record (command, self.path, headers, body) per request,
    and serve a configurable (status, body) — defaults to a representation list.
    `self.path` is the RAW request target, so the query string is included."""
    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.server.seen.append((self.command, self.path, dict(self.headers), body))
            code, payload = self.server.respond
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(payload)
        do_GET = do_PATCH = do_POST = _serve
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    httpd.seen, httpd.respond = [], (200, b"[]")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def test_real_seam_encodes_the_cas_dialect(capture_server):
    """Drive the REAL SupabaseControlPlane and assert the dialect the CAS relies on.
    No live PostgREST is needed."""
    cp = SupabaseControlPlane(url=f"http://127.0.0.1:{capture_server.server_port}",
                              service_key="svc")
    captured = "2026-01-01T00:00:00+00:00"
    capture_server.respond = (200, json.dumps([{"used_at": None, "expires_at": "2099-01-01T00:00:00+00:00"}]).encode())
    assert oauth._restore_code(cp, "c", captured) is True        # representation non-empty → True
    cmd, path, headers, _ = capture_server.seen[-1]
    assert cmd == "PATCH"
    assert "code_hash=eq." in path and "used_at=eq." in path and "expires_at=gt." in path
    assert "used_at=eq.2026-01-01T00%3A00%3A00%2B00%3A00" in path   # ':'→%3A, '+'→%2B
    assert headers["Prefer"] == "return=representation"
    assert "Content-Profile" not in headers      # pins that no schema profile is switched

    capture_server.respond = (200, json.dumps([{"used_at": None,
                                                "expires_at": "2099-01-01T00:00:00+00:00"}]).encode())
    oauth._consume_code(cp, "c")
    assert "used_at=is.null" in capture_server.seen[-1][1]


@pytest.mark.parametrize("status,payload,expected", [
    (500, b'{"message":"boom"}', "unknown"),      # S1(a)
    (200, b"<html>not json</html>", "unknown"),   # S1(c) unparseable 2xx
])
def test_real_seam_maps_status_and_unparseable_body(capture_server, status, payload, expected):
    """The fake's injector raises a Python error, so it CANNOT exercise the seam's own
    HTTP branches. Assert the callers surface a typed outcome, never a raw exception."""
    cp = SupabaseControlPlane(url=f"http://127.0.0.1:{capture_server.server_port}",
                              service_key="svc")
    capture_server.respond = (status, payload)
    assert oauth._consume_state(cp, "c") == expected
    assert oauth._restore_code(cp, "c", "T1") is False
```

**Step 6: Commit** — `feat(oauth): typed mint-abort vocabulary + read-only redemption observation (#2863)`

---

### Task 3: `_issue_tokens` — 3-lane taxonomy with a structural no-leak guarantee

**Depends on:** Task 2.
**Intent:** The unit that must be all-or-nothing gains a compensation protocol: roll back what it wrote,
**observe** that the rollback worked, and never disclose a pair it could not commit.
**Acceptance:** Every single-fault window (pre-commit **and** commit-then-lost) leaves zero live minted
rows; the double fault leaves no *disclosed* pair and records `recovered=False`; an observation read
failure → `recovered=False`; the concurrent loser is re-raised unchanged (`OAuthError`, **not**
`OAuthMintAborted`) with its rollback attempted and its failure captured; lane 3 cannot fail the
response; one abort produces exactly one capture.

**Files:** Modify `tortoise/oauth.py::_issue_tokens` (~598–673). Test: `tests/test_oauth_token_fault.py`.

**Step 1: Write the failing tests** (fresh `FakeControlPlane` per case):

```python
def _mint(cp, **over):
    return oauth._issue_tokens(cp, client_id="c1", user_id="u1", team_id="t1",
                               scope="mcp", resource=None, **over)

def test_refresh_insert_failure_rolls_back_and_observes():
    cp = FakeControlPlane(); cp.fail_query(table="oauth_refresh_tokens", method="POST")
    with pytest.raises(oauth.OAuthMintAborted) as ei: _mint(cp)
    assert ei.value.recovered is True       # never inserted → nothing to revoke → clean
    assert _live(cp, "oauth_refresh_tokens") == [] and _live(cp, "oauth_access_tokens") == []

def test_access_insert_failure_removes_the_live_orphan():
    cp = FakeControlPlane(); cp.fail_query(table="oauth_access_tokens", method="POST")
    with pytest.raises(oauth.OAuthMintAborted) as ei: _mint(cp)
    assert ei.value.recovered is True
    assert _live(cp, "oauth_refresh_tokens") == []      # TODAY this is the live orphan (matrix row 5)

@pytest.mark.parametrize("table", ["oauth_refresh_tokens", "oauth_access_tokens"])
def test_mint_write_that_committed_then_lost_its_response_is_rolled_back(table):
    cp = FakeControlPlane()
    cp.fail_query(table=table, method="POST", after_mutation=True, times=1)
    with pytest.raises(oauth.OAuthMintAborted) as ei: _mint(cp)
    assert ei.value.recovered is True
    assert _live(cp, table) == []           # a live orphan here would be a #3036-class leak

def test_double_fault_reports_recovered_false():
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_access_tokens", method="POST")
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH", times=1)   # the rollback
    with pytest.raises(oauth.OAuthMintAborted) as ei: _mint(cp)
    assert ei.value.recovered is False

def test_observation_read_failure_is_terminal_never_fail_open():
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_access_tokens", method="POST")                 # trigger
    cp.fail_query(table="oauth_access_tokens", method="GET", select=["id"])   # observation
    with pytest.raises(oauth.OAuthMintAborted) as ei: _mint(cp)
    assert ei.value.recovered is False

def test_prev_refresh_observed_revoked_is_recovered_false():
    cp = FakeControlPlane(); prev = _seed_refresh_token(cp)
    cp.fail_query(table="oauth_access_tokens", method="POST")       # trigger after the claim
    cp.tables["oauth_refresh_tokens"][0]["revoked_at"] = "T"        # claim landed
    with pytest.raises(oauth.OAuthMintAborted) as ei:
        _mint(cp, prev_refresh=cp.tables["oauth_refresh_tokens"][0])
    assert ei.value.recovered is False

def test_loser_rollback_failure_still_raises_invalid_grant(caplog, monkeypatch):
    calls = []
    monkeypatch.setattr("tortoise.sentry.capture_exception", lambda exc, **kw: calls.append(exc))
    """Pin lane 1's rollback-failure path. The fault MATCHES BY SHAPE (select-less
    PATCH), never by a hand-written filter literal — and the autouse `_no_silent_faults`
    guard fails this test if the injector never fires."""
    cp = FakeControlPlane()
    row_id, _ = _seed_refresh_token(cp, "old")
    cp.query("oauth_refresh_tokens", method="PATCH", select=["id"],
             filters=[("id", "eq", row_id)], json_body={"revoked_at": "T"})   # pre-claim → loser
    # Match the ROLLBACK by its select-less shape; a bare match would be taken by the
    # claim PATCH (select=["id"]) and land in lane 2 instead.
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)
    with pytest.raises(oauth.OAuthError) as ei:
        _mint(cp, prev_refresh={"id": row_id})
    assert ei.value.status == 400 and ei.value.error == "invalid_grant"   # NOT OAuthMintAborted
    assert len(calls) == 1 and "loser rollback" in caplog.text   # lane 1 captured exactly once

def test_prev_access_revoke_failure_does_not_fail_a_delivered_pair(caplog):
    cp = FakeControlPlane()
    rid, _ = _seed_refresh_token(cp, "acc-parent")
    acc = _seed_access_token(cp, refresh_id=rid)
    cp.fail_query(table="oauth_access_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)   # lane 3
    out = _mint(cp, prev_access_id=acc)
    assert out["access_token"] and out["refresh_token"]          # delivered
    assert "prev-access revoke failed" in caplog.text            # the fault DID fire

@pytest.mark.parametrize("second_fault", ["none", "one_rollback", "both_rollbacks"])
def test_one_capture_per_abort(monkeypatch, second_fault):
    calls = []
    monkeypatch.setattr("tortoise.sentry.capture_exception",
                        lambda exc, **kw: calls.append(exc))
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)          # trigger
    if second_fault in ("one_rollback", "both_rollbacks"):
        cp.fail_query(table="oauth_refresh_tokens", method="PATCH",
                      match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)
    if second_fault == "both_rollbacks":
        cp.fail_query(table="oauth_access_tokens", method="PATCH",
                      match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)
    with pytest.raises(oauth.OAuthMintAborted): _mint(cp)
    assert len(calls) == 1          # counts, not presence — the panic case would be 3
```

For the loser case, drive the race deterministically the way
`tests/test_oauth_mcp.py::test_rotation_race_single_winner` does (pre-claim the row) rather than
relying on thread interleaving.

**Step 2: Run** → FAIL (today: `test_access_insert_failure_removes_the_live_orphan` sees a live orphan;
the commit-then-lost cases raise a raw `RuntimeError`; the capture-count test sees 0).

**Step 3: Implement** — rewrite `_issue_tokens`:

```python
    minted: list[tuple[str, str]] = []   # (table, id) — appended BEFORE the POST, so a
                                         # commit-then-lost POST still gets its rollback
    try:
        minted.append(("oauth_refresh_tokens", refresh_id))
        cp.query("oauth_refresh_tokens", method="POST", json_body={...})
        minted.append(("oauth_access_tokens", access_id))
        cp.query("oauth_access_tokens", method="POST", json_body={...})
        if prev_refresh is not None:
            claimed = cp.query("oauth_refresh_tokens", method="PATCH", select=["id"],
                               filters=[("id", "eq", prev_refresh["id"]),
                                        ("revoked_at", "is", None)],
                               json_body={"revoked_at": _now_iso()})
            if not claimed:
                try:
                    _rollback_minted(cp, minted, now, capture=True)   # lane 1
                except Exception as exc:  # noqa: BLE001 — belt & suspenders
                    logger.warning("oauth: loser rollback raised: %s", exc)
                raise OAuthError(400, "invalid_grant",
                                 "Refresh token already revoked (rotated or invalidated).")
    except OAuthError:
        raise                                              # lane 1 — intentional signal
    except Exception as exc:
        _log_and_capture(exc, where="_issue_tokens")        # lane 2 — the SINGLE capture
        try:
            _rollback_minted(cp, minted, now, capture=False)   # lane 2 already captured
            recovered = _mint_observably_clean(cp, minted)
            if recovered and prev_refresh is not None:
                recovered = _prev_refresh_unclaimed(cp, prev_refresh)
        except Exception as inner:                          # structural no-leak guarantee
            logger.warning("oauth: mint compensation raised: %s", inner)
            recovered = False
        raise OAuthMintAborted(recovered) from exc
    if prev_access_id:                                      # lane 3 — outside the handler
        try:
            cp.query("oauth_access_tokens", method="PATCH",
                     filters=[("id", "eq", prev_access_id)],
                     json_body={"revoked_at": now})
        except Exception as exc:  # noqa: BLE001 — non-fatal
            logger.warning("oauth: prev-access revoke failed: %s", exc)
    return {...}
```

Three deliberate differences from the earlier draft: (i) `_rollback_minted` may **not** raise on an
empty result (that clause inverted the protocol); (ii) lane 1's rollback **is** wrapped (a raise there
would escape lane 1 into `except Exception`, converting the pinned loser `OAuthError` into
`OAuthMintAborted` and breaking `test_rotation_race_single_winner`); (iii) lane 2/3 log only, because
`_log_and_capture`'s owner table permits exactly one capture.

**Step 4: Run** → PASS, then `uv run pytest tests/test_oauth_mcp.py -q` (incl. `test_rotation_race_single_winner`).

**Step 5: Commit** — `fix(oauth): compensate and observe every mint write window (#2863)`

---

### Task 4: `exchange_auth_code` — `consumed` + `attempted_consume`

**Depends on:** Task 3.
**Intent:** Cover the post-consume window the issue names, without re-introducing the fail-open the
earlier design had, and without mislabelling a still-live grant as dead during a total outage.
**Acceptance:** Post-consume failure → code restored iff the CAS confirms it → 503, else terminal;
a consume-attempt failure is **observed** before signalling; a client-auth failure (write never
attempted) → 503 directly; an `OAuthError` never re-arms; the `OAuthMintAborted` arm maps
`recovered` → 503/terminal **at the endpoint**, with the Target's retry assertion proven end-to-end.

**Files:** Modify `tortoise/oauth.py::exchange_auth_code` (~675–708). Test: `tests/test_oauth_token_fault.py`.

**Step 1: Write the failing tests** (all against `fault_client`, `raise_server_exceptions=False`):

```python
def _fail_consume(cp, **kw):
    """The atomic-claim PATCH: matched by SHAPE (a PATCH whose select includes
    code_challenge), never by a hand-written 11-column list — the exact drift three
    review cycles flagged. `_restore_code`'s PATCH has a different 2-column select and
    is never caught by this predicate."""
    cp.fail_query(table="oauth_codes", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and bool(sel) and "code_challenge" in sel,
                  **kw)


def _post_code(tc, cp, code, verifier, **over):
    body = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "client_id": _CLIENT_ID, "redirect_uri": _REDIRECT, **over}
    return tc.post("/oauth/token", data=body)

def test_consume_patch_failure_uncommitted_is_retryable(fault_client):
    tc, cp = fault_client
    v = _seed_code(cp, "c1", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    _fail_consume(cp)                                # shape-matched, see the rule above
    r1 = _post_code(tc, cp, "c1", v)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_codes"][0]["used_at"] is None       # observed unconsumed
    assert _post_code(tc, cp, "c1", v).status_code == 200       # retry works

def test_consume_patch_failure_committed_is_terminal_not_retryable(fault_client):
    tc, cp = fault_client
    v = _seed_code(cp, "c2", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    _fail_consume(cp, after_mutation=True)
    r = _post_code(tc, cp, "c2", v)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"   # NOT 503

def test_consume_state_unknown_is_terminal(fault_client):
    tc, cp = fault_client
    v = _seed_code(cp, "c3", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    _fail_consume(cp)                                                     # the claim PATCH
    cp.fail_query(table="oauth_codes", method="GET",
                  select=["used_at", "expires_at"], times=1)              # the observation
    assert _post_code(tc, cp, "c3", v).json()["error"] == "invalid_grant"

def test_client_auth_failure_during_outage_is_503_not_invalid_grant(fault_client):
    tc, cp = fault_client
    v = _seed_code(cp, "c4", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    cp.fail_query(table="oauth_clients", method="GET", times=1)     # pure read, pre-consume
    r = _post_code(tc, cp, "c4", v)
    assert r.status_code == 503
    assert cp.tables["oauth_codes"][0]["used_at"] is None            # grant NOT burned

def test_post_consume_team_read_failure_restores_the_code_and_retry_succeeds(fault_client):
    """matrix row 3 — TODAY: 500, used_at stays set, retry → 400."""
    tc, cp = fault_client
    v = _seed_code(cp, "c5", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    cp.fail_query(table="teams", method="GET", times=1)
    r1 = _post_code(tc, cp, "c5", v)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_codes"][0]["used_at"] is None
    r2 = _post_code(tc, cp, "c5", v)
    assert r2.status_code == 200 and "access_token" in r2.json()     # the Target's assertion

@pytest.mark.parametrize("table", ["oauth_refresh_tokens", "oauth_access_tokens"])
def test_mint_abort_recovered_true_is_503_and_the_code_redeems_on_retry(fault_client, table):
    tc, cp = fault_client
    v = _seed_code(cp, f"m-{table}", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    cp.fail_query(table=table, method="POST", times=1)
    r1 = _post_code(tc, cp, f"m-{table}", v)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert _live(cp, "oauth_refresh_tokens") == [] and _live(cp, "oauth_access_tokens") == []
    assert _post_code(tc, cp, f"m-{table}", v).status_code == 200

def test_mint_abort_recovered_false_is_terminal_invalid_grant(fault_client):
    tc, cp = fault_client
    v = _seed_code(cp, "m-f", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH", times=1)
    r = _post_code(tc, cp, "m-f", v)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert cp.tables["oauth_codes"][0]["used_at"] is not None

def test_mint_abort_with_cas_miss_is_terminal_and_leaves_used_at_set(fault_client):
    tc, cp = fault_client
    v = _seed_code(cp, "m-cas", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)      # recovered=True
    cp.fail_query(table="oauth_codes", method="PATCH",
                  select=["used_at", "expires_at"], times=1)                # CAS restore fails
    r = _post_code(tc, cp, "m-cas", v)
    assert r.status_code == 400 and cp.tables["oauth_codes"][0]["used_at"] is not None

def test_bad_pkce_never_re_arms_the_code(fault_client):
    tc, cp = fault_client
    _seed_code(cp, "p1", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    r = _post_code(tc, cp, "p1", verifier="wrong-verifier-wrong-verifier-wrong-verifier")
    assert r.status_code == 400 and cp.tables["oauth_codes"][0]["used_at"] is not None

@pytest.mark.parametrize("table", ["oauth_clients", "teams"])
@pytest.mark.parametrize("grant", ["code", "refresh"])
def test_exactly_one_capture_per_conversion_path(monkeypatch, fault_client, table, grant):
    calls = []
    monkeypatch.setattr("tortoise.sentry.capture_exception", lambda exc, **kw: calls.append(exc))
    tc, cp = fault_client
    cp.fail_query(table=table, method="GET", times=1)
    if grant == "code":                                        # pre-consume
        v = _seed_code(cp, f"cap-{table}")
        _post_code(tc, cp, f"cap-{table}", v)
    else:                                                      # refresh pre-mint
        _post_refresh(tc, cp, _seed_refresh_token(cp, f"cap-{table}-rt")[1])
    assert len(calls) == 1
```

**Step 2: Run** → FAIL (`test_post_consume_team_read_failure_...` returns 500; the committed-consume
case returns 500 rather than a typed error; the capture test sees 0).

**Step 3: Implement** — exactly the scoping artifact's pseudocode:

```python
    consumed = False
    attempted_consume = False
    code_row: dict | None = None
    try:
        client = _verify_client_auth(cp, body.get("client_id"), body)   # pure read
        attempted_consume = True
        code_row = _consume_code(cp, body.get("code", ""))              # THE atomic gate
        consumed = True
        <the existing client_id / redirect_uri / PKCE / resource checks, unchanged>
        _assert_team_usable(cp, code_row["team_id"])
        scope = code_row.get("scope") or " ".join(SCOPES_SUPPORTED)
        out = _issue_tokens(cp, ..., resource=code_row.get("resource"))
    except OAuthError:
        raise
    except OAuthMintAborted as exc:
        logger.warning("oauth: auth-code mint aborted (recovered=%s)", exc.recovered)  # I4: log-only
        if exc.recovered and _restore_code(cp, body.get("code", ""), code_row["used_at"]):
            raise OAuthTemporarilyUnavailable() from None
        raise OAuthError(400, "invalid_grant",
                         "The authorization code could not be redeemed — re-run authorization.") from None
    except Exception as exc:
        _log_and_capture(exc, where="exchange_auth_code")
        if consumed:
            if _restore_code(cp, body.get("code", ""), code_row["used_at"]):
                raise OAuthTemporarilyUnavailable() from None
            raise OAuthError(400, "invalid_grant", "… re-run authorization.") from None
        if not attempted_consume:
            raise OAuthTemporarilyUnavailable() from None        # constructive-clean
        if _consume_state(cp, body.get("code", "")) == "unconsumed":
            raise OAuthTemporarilyUnavailable() from None
        raise OAuthError(400, "invalid_grant", "… re-run authorization.") from None
    return {k: v for k, v in out.items() if not k.startswith("_")}
```

Branch order matters: `consumed` → `attempted_consume` → observe. `consumed` implies
`attempted_consume`, so the middle arm is unreachable while the last is set.

**Step 4: Run** → PASS. The 7-row matrix is now an **assertion** (the endpoint tests above), not a manual observation.

**Step 5: Commit** — `fix(oauth): atomic-feel auth-code redemption with truthful retry signals (#2863)`

---

### Task 5: `refresh_grant` — wrap the pre-mint reads, map the abort, unmask the revokes

**Depends on:** Task 3.
**Intent:** The refresh path has the same untyped-500 hole and two revokes that swallow a terminal
`OAuthError`; the worst matrix row (claim OK + prev-access revoke raises) currently locks the client out.
**Acceptance:** A pre-mint read failure → 503 **parametrized over every read call site including the
first** (`oauth_clients`) and `team_memberships`; a mint abort maps `recovered` → 503/terminal;
a raising `_revoke_team_family` or lapsed-membership revoke still propagates the terminal
`OAuthError(403)`; row 7 delivers a **usable** rotated pair.

**Files:** Modify `tortoise/oauth.py::refresh_grant` (~719–773). Test: `tests/test_oauth_token_fault.py`.

**Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("table,select", [
    ("oauth_clients", None),        # FIRST read on the path — the :726 leak
    ("oauth_refresh_tokens", None), # the refresh-token SELECT
    ("teams", None),                # _assert_team_usable
    ("team_memberships", None),     # membership_for_user_team — S4 call site #4
    ("oauth_access_tokens", ["id"]),# prev_access
])
def test_refresh_pre_mint_read_failure_is_503_not_500(fault_client, table, select):
    tc, cp = fault_client
    rid, rt = _seed_refresh_token(cp, "rt-pre")
    cp.fail_query(table=table, method="GET", select=select, times=1)
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is None    # grant untouched
    assert _post_refresh(tc, cp, rt).status_code == 200                  # retry works

def test_refresh_membership_revoke_failure_still_returns_403_invalid_grant(fault_client):
    tc, cp = fault_client
    rid, rt = _seed_refresh_token(cp, "rt-mem")
    cp.tables["team_memberships"] = []     # `setdefault` would be a NO-OP: the fixture seeded one
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)   # the revoke
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 403 and r.json()["error"] == "invalid_grant"  # not a 500
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is not None

def test_refresh_suspension_family_revoke_failure_still_returns_403(fault_client):
    tc, cp = fault_client
    rid, rt = _seed_refresh_token(cp, "rt-susp")
    cp.tables["teams"][0]["suspended_at"] = "2026-01-01T00:00:00+00:00"
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH", times=1)  # _revoke_team_family
    assert _post_refresh(tc, cp, rt).status_code == 403

def test_refresh_mint_abort_recovered_true_is_503_and_retry_succeeds(fault_client):
    tc, cp = fault_client
    rid, rt = _seed_refresh_token(cp, "rt-ok")
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)      # mint abort
    r1 = _post_refresh(tc, cp, rt)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    r2 = _post_refresh(tc, cp, rt)                                          # retry
    assert r2.status_code == 200 and "refresh_token" in r2.json()


def test_refresh_mint_abort_recovered_false_is_terminal_invalid_grant(fault_client):
    tc, cp = fault_client
    rid, rt = _seed_refresh_token(cp, "rt-bad")
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)   # rollback fails
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

def test_refresh_claim_raise_pre_commit_is_503_and_retry_succeeds(fault_client):
    """matrix row 6 — the claim PATCH raises pre-commit. Both minted rows exist and the OLD
    token is still unrevoked, so `recovered` depends on `_prev_refresh_unclaimed`."""
    tc, cp = fault_client
    rid, rt = _seed_refresh_token(cp, "rt-6")
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH", select=["id"], times=1)
    r1 = _post_refresh(tc, cp, rt)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert _live(cp, "oauth_refresh_tokens") == []            # the orphan pair was rolled back
    assert _post_refresh(tc, cp, rt).status_code == 200       # the old token still works


def test_refresh_claim_committed_then_lost_response_is_terminal_and_no_live_family(fault_client):
    """The ambiguous-claim lockout shape (a): the claim landed, our rollback un-revokes
    the new pair, `_prev_refresh_unclaimed` sees revoked_at set → recovered=False."""
    tc, cp = fault_client
    rid, rt = _seed_refresh_token(cp, "rt-amb")
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH", select=["id"],
                  times=1, after_mutation=True)              # claim commits, response lost
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

def test_row7_prev_access_revoke_failure_delivers_a_USABLE_pair(fault_client, caplog):
    """The fix's headline (matrix row 7): the client must receive a pair it can use."""
    tc, cp = fault_client
    rid, rt = _seed_refresh_token(cp, "rt-7")
    prev_acc = _seed_access_token(cp, refresh_id=rid)          # the row lane 3 will revoke
    cp.fail_query(table="oauth_access_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)   # lane 3 only
    r1 = _post_refresh(tc, cp, rt)
    assert r1.status_code == 200 and "refresh_token" in r1.json()
    assert _live(cp, "oauth_access_tokens")                     # a NEW live pair exists
    r2 = _post_refresh(tc, cp, r1.json()["refresh_token"])      # the delivered pair ROTATES
    assert r2.status_code == 200
    assert "prev-access revoke failed" in caplog.text
```

**Step 2: Run** → FAIL (500s today; the revoke-failure tests see a `RuntimeError`-shaped 500 instead of 403).

**Step 3: Implement** — wrap from `oauth.py:726` (`_verify_client_auth` → `_client_row` →
`oauth_clients` SELECT is the first read; a wrap starting at `:728` leaves it leaking):

```python
    try:
        client = _verify_client_auth(cp, body.get("client_id"), body)
        <the existing refresh-token SELECT, revoked/expiry/client checks, resource check>
        try:
            _assert_team_usable(cp, row["team_id"])
        except OAuthTemporarilyUnavailable:
            raise        # a transient signal must NEVER trigger family revocation
        except OAuthError:
            try:
                _revoke_team_family(cp, row["user_id"], row["team_id"])
            except Exception as exc:  # noqa: BLE001 — correction #8: the single capture
                _log_and_capture(exc, where="family revoke")
            raise
        if membership_for_user_team(cp, row["user_id"], row["team_id"]) is None:
            try:
                cp.query("oauth_refresh_tokens", method="PATCH",
                         filters=[("id", "eq", row["id"])],
                         json_body={"revoked_at": _now_iso()})
            except Exception as exc:  # noqa: BLE001 — correction #8: the single capture
                _log_and_capture(exc, where="membership revoke")
            raise OAuthError(403, "invalid_grant",
                             "Membership in the team has ended — the grant was revoked.")
        prev_access = cp.query("oauth_access_tokens", select=["id"],
                               filters=[("refresh_token_id", "eq", row["id"]),
                                        ("revoked_at", "is", None)])
    except OAuthError:
        raise
    except Exception as exc:
        _log_and_capture(exc, where="refresh_grant pre-mint")
        raise OAuthTemporarilyUnavailable(
            "Temporary control-plane failure before token rotation — retry.") from None
    try:
        out = _issue_tokens(cp, ..., prev_refresh=row,
                            prev_access_id=prev_access[0]["id"] if prev_access else None)
    except OAuthMintAborted as exc:
        logger.warning("oauth: refresh mint aborted (recovered=%s)", exc.recovered)   # I4: log-only
        if exc.recovered:
            raise OAuthTemporarilyUnavailable() from None
        raise OAuthError(400, "invalid_grant",
                         "The refresh token could not be rotated — re-run authorization.") from None
    return {k: v for k, v in out.items() if not k.startswith("_")}
```

**Residual note (must remain in the plan):** the ambiguous-claim lockout is **not** closed. (a) A claim
that commits then loses its response → `_prev_refresh_unclaimed` sees `revoked_at` set → `recovered=False`
→ terminal, leaving the old token revoked **and** the replacement pair revoked. (b) A claim raising
pre-commit → `recovered=True` → 503 → the late claim lands on retry → `invalid_grant` → mcp
`clear_tokens()` (lockout). Both are bounded by F11 (a write committing after the observation); cleanup
is #3036, the durable answer is #3027.

**Step 4: Run** → PASS + `tests/test_oauth_mcp.py -q`. **Step 5: Commit** — `fix(oauth): typed refresh-grant boundary + unmasked terminal signals (#2863)`

---

### Task 6: `POST /oauth/token` — typed boundary + coherent last-resort net

**Depends on:** Tasks 4, 5.
**Intent:** No infra error may escape the grant dispatch as a bare `{"detail":"Internal server
error"}`; the unreachable last resort must still be a coherent RFC 6749 error, built by the same
producer as every other OAuth error, and must preserve observability.
**Acceptance:** A control-plane failure on either grant returns a typed 503 (or terminal 400); any
*unconverted* exception returns `500` with an RFC 6749 body produced by `_oauth_error_response`, plus
exactly one log + capture; `_log_and_capture` raising internally cannot turn a typed error into a 500;
the two transient-503 conventions agree on **status**.

**Files:** Modify `tortoise/hosted_api.py::oauth_token` (~21875–21907) — **including its import block**. Test: `tests/test_oauth_token_fault.py`.

**Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("fn,data", [
    ("exchange_auth_code", {"grant_type": "authorization_code", "code": "x"}),
    ("refresh_grant", {"grant_type": "refresh_token", "refresh_token": "x"}),
])
def test_unconverted_failure_returns_coherent_500_not_bare_detail(monkeypatch, fault_client, fn, data):
    """Raise from OUTSIDE the dispatch — patching `_verify_client_auth` would be caught by
    Task 4's own constructive-clean arm (503), never reaching this net. The body must drive
    the grant whose function is patched, or the other one runs and never hits the net."""
    monkeypatch.setattr(f"tortoise.oauth.{fn}",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    tc, _ = fault_client
    r = tc.post("/oauth/token", data=data)
    assert r.status_code == 500
    assert r.json().get("error") == "server_error" and "detail" not in r.json()

@pytest.mark.parametrize("grant,data", [
    ("authorization_code", {"code": "x", "client_id": _CLIENT_ID, "redirect_uri": _REDIRECT}),
    ("refresh_token", {"refresh_token": "rt", "client_id": _CLIENT_ID}),
])
def test_boundary_converts_control_plane_failures_to_503_not_500(monkeypatch, grant, data):
    """ErrorControlPlane (every call raises) — the wrap in oauth.py must turn it into a
    503 before the boundary, on BOTH grant types."""
    cp = ErrorControlPlane()
    monkeypatch.setattr("tortoise.hosted_api._oauth_control_plane", lambda: (cp, True))
    with TestClient(app, raise_server_exceptions=False) as tc:
        r = tc.post("/oauth/token", data={"grant_type": grant, **data})
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable"


def test_boundary_logs_and_captures_the_unconverted_exception(monkeypatch, caplog, fault_client):
    calls = []
    monkeypatch.setattr("tortoise.sentry.capture_exception",
                        lambda exc, **kw: calls.append(exc))
    monkeypatch.setattr("tortoise.oauth.exchange_auth_code",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    tc, _ = fault_client
    with caplog.at_level(logging.WARNING, logger="tortoise.oauth"):
        r = tc.post("/oauth/token", data={"grant_type": "authorization_code", "code": "x"})
    assert r.status_code == 500 and len(calls) == 1 and "oauth/token boundary" in caplog.text

def test_capture_exception_raising_does_not_break_the_typed_error(monkeypatch, fault_client):
    """S6(c): if Sentry itself raises, the typed 503/400 must survive (it must not
    become the bare 500 this task exists to remove)."""
    monkeypatch.setattr("tortoise.sentry.capture_exception",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sentry down")))
    tc, cp = fault_client
    v = _seed_code(cp, "sentry", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    cp.fail_query(table="teams", method="GET", times=1)
    r = _post_code(tc, cp, "sentry", v)
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable"

def test_transient_503_conventions_agree_on_status():
    """Two drivers (OAuth §5.2 body vs the dashboard detail body), one rule: a transient
    control-plane failure is a 503. This pins only the STATUS agreement — the bodies are
    deliberately different, and that divergence is documented on the class."""
    from tortoise.hosted_api import _control_plane_unavailable
    assert _control_plane_unavailable().status_code == 503
    assert oauth.OAuthTemporarilyUnavailable().status == 503
    assert oauth.OAuthTemporarilyUnavailable().body() == {
        "error": "temporarily_unavailable",
        "error_description": "Temporary control-plane failure — retry."}
```

**Step 2: Run** → FAIL (the net returns `{"detail": ...}` today).

**Step 3: Implement** — amend the import block (`:21880-21884`) and make the net use the domain's
single body producer:

```python
    from tortoise.oauth import (
        OAuthError,
        _log_and_capture,
        exchange_auth_code,
        refresh_grant,
    )
    ...
    except OAuthError as exc:
        return _oauth_error_response(exc)
    except Exception as exc:            # last-resort bug detector, NOT a retry signal
        _log_and_capture(exc, where="oauth/token boundary")
        return _oauth_error_response(
            OAuthError(500, "server_error", "Internal error processing the token request."))
    return out
```

**Consumer audit (re-derived; put it in the commit message):**
`_oauth_error_response` (`:21706`) is status-generic and is the **only** reader of `exc.status`. All
seven `except OAuthError` sites (`:21763, :21818, :21853, :21868, `**`:21905`**`, :21927, :21958`) route to it
or to authorize's redirect-with-error branch and are **status-generic pass-throughs**; `:21905`
(`oauth_token`) is precisely the receiving site for the new 503 and needs no change because
`_oauth_error_response` forwards `exc.status` verbatim; **none inspects `exc.status`**. Also audited:
`oauth.py:752`'s own `except OAuthError` around `_assert_team_usable` — narrowed above so the new
503 can never trigger `_revoke_team_family`. No `except OAuthError` exists in
`mcp_auth.py`/`mcp_server.py`; no telemetry/metering parses OAuth error codes. **Out of scope
(explicitly):** three pre-existing bare shapes on this same endpoint — `HTTPException(503, "OAuth not
configured")` (`:21887`), `HTTPException(400, "Invalid form body")` (`:21895`), and the
`_read_capped_body` 413 re-raised at `:21892` — all outside the grant dispatch, all owned by #3026.

**Step 4: Run** → PASS. **Step 5: Commit** — `fix(hosted_api): typed last-resort boundary on /oauth/token (#2863)`

---

### Task 7: CI registration + full green

**Depends on:** Task 6.
**Intent:** A new test file outside the manifest is a silent CI hole; a live-DB file in the fast lane
breaks the lane contract.
**Acceptance:** `uv run python tools/ci_selection.py --integrity` passes; the new file runs in the fast
`api` surface; the OAuth suite is green.

**Files:** Modify `config/ci-surfaces.yml` (add `- test_oauth_token_fault.py` under `api`, next to `test_oauth_mcp.py`, line ~99). Modify `.github/workflows/python-ci.yml` **only if** `--integrity` reports a halves issue.

**Step 1:** `uv run python tools/ci_selection.py --integrity` → expect "missing from manifest" (the red state).
**Step 2:** register under `api` (no live DB needed — `FakeControlPlane` + the local-`HTTPServer` harness + `TestClient`), re-run → `✅ integrity: all test files classified; slow_files consistent; halves consistent`.
**Step 3: Full suite.** Graph port from your compose mapping (this machine: `:6380`; repo/CI default `:6379`). Adjust the port:

```bash
env -u TORTOISE_API_KEY TORTOISE_SECRET_PEPPER=test-static-pepper RATE_LIMIT_DISABLED=1 \
  TORTOISE_DB_URI='docker://:falkordb@localhost:6380/tortoise_2863' \
  uv run pytest tests/test_oauth_token_fault.py tests/test_oauth_mcp.py \
                 tests/test_fake_control_plane.py tests/test_supabase_control.py -v
```

Known pre-existing failures NOT to chase: `test_ci_selection.py::test_integrity_covers_all_test_files`,
`test_hosted_api.py::TestPointsCreate::test_create_point_enqueues_dream` (#2893), the conftest DB-health OOM (#2959).
**Step 4: Commit** — `test(oauth): register the fault-injection suite in the CI manifest (#2863)`

---

## Falsified invariants (recorded — do NOT read these as handled)

1. **"no second live token family"** — false on the raising-rollback double fault: the minted row
   *stays live in the DB* (`recovered=False` is exactly that state). What is enforced is I2 — it is
   never **disclosed**. The live orphan is **inert and unaudited until #3036**; no in-plan owner.
2. **"never leaks a raw exception"** — scoped to `Exception`, and `_log_and_capture` is itself
   raise-proof, so a logging/Sentry failure cannot convert a typed error into a 500. A raised
   `BaseException` would still escape (deliberately excluded).
3. **"exactly one log + capture"** — an inventory over I4's owner table, not a global guarantee:
   `revoke_token` is a documented unconverted hole deferred to #3026. Capture **counts** are asserted
   per path.

## Good > Easy — explicit deferral

**Deferred:** keeping `_assert_team_usable` **after** the atomic consume (the compensation must cover
matrix row 3). **Good alternative:** read `code_row.team_id` with one extra read-only `oauth_codes`
SELECT, check the team **before** `_consume_code`, then consume atomically — which deletes the
post-consume `except Exception` branch (and its `_restore_code` call) outright.
**Cost:** +1 round trip per code exchange, plus a benign TOCTOU on the immutable `team_id` (the atomic
`used_at IS NULL` claim still gates redemption).
**Rationale:** it changes an approved scoping design mid-flight, and `_restore_code` must exist
regardless for the `except OAuthMintAborted` arm, so the saving is one branch rather than the protocol.
Recorded on #2863 for #3025 (which deletes the compensation entirely).

## Open Residuals (fail-closed, named carriers)

| Residual | Consequence | Carrier |
|---|---|---|
| A write committing **after** the observation (client 5s timeout / delayed commit) — consume, mint insert, rotation claim | an untruthful signal; a possible lockout on the rotation window | **#3027**; #3036 for the inert orphan |
| CAS **value** round-trip through real PostgREST (`used_at=eq.<timestamptz>`) — the *encoding* is pinned by Task 2 Step 6; the live value leg is not | **fail-closed** — a mismatch → `False` → terminal `invalid_grant`, silently reinstating the burn | named on #2863 (not assigned to #3025, which *deletes* the CAS) |
| Live orphan rows from a raising-rollback double fault | inert (never disclosed); storage hygiene | #3036 |
| `revoke_token` untyped boundary; `OAuth not configured` (:21887) and `Invalid form body` (:21895) bare shapes | bare 500 / bare detail | #3026 |

## Cross-references

- Scoping artifact (problem-verify PASSED, cycle 8): `/tmp/2863-scope.md`, posted to issue #2863. The
  normative helper contracts are **inlined in Task 2 Step 3** — the `/tmp` path is provenance only.
- Sibling issues: #3025, #3026, #3027, #3036.

<!-- plan-review: cycles=4, status=issues, version=2.3.0 -->

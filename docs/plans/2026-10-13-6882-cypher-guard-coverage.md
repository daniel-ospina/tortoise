---
title: "Plan — #6882 close cypher_guard's residual unguarded channels and pin the production wiring"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-13
subjects.team: organisation-design-team
aboutSubjects: organisation-design-team
ownedBy: organisation-design-team
governingAgreement: "#6882 (follow-up of #6072; scope of record /tmp/pi-6882-scope.md)"
aboutObjects: cypher-guard, tortoise-projection, falkordb
---

<!-- research-path: none (Phase 1.5 justified-skip: no new third-party dep; in-repo precedent verified) -->

# cypher_guard coverage: pin the production wiring, close the `.connection` channel, make `_guard_handle` idempotent

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make `tortoise.cypher_guard`'s coverage contract enforceable — a one-line revert of any
production `guarded_client(...)` construction fails a test, the guarded client's `.connection`
command channel refuses `=~`, and `_guard_handle` honours its documented idempotence.

**Team:** organisation-design-team

**Architecture:** Three surgical changes across three existing files. (1) `tortoise/cypher_guard.py`:
`_guarded_execute_command` returns a **class-based** wrapper (so the module's own MRO-based
`_carries_marker` can read it — invisibly to a permissive double) and becomes idempotent; a new
`_guard_connection(conn)` rebinds the connection's command-entry attributes **in place** (never
replacing the object); `_guard_handle` early-returns an existing proxy. (2) `tests/test_guard.py`
(carve-out, redirect-exempt, **already** builds a real embedded projection) gains the runtime marker
assertion. (3) `tests/test_bounded_raw_clients_3065.py`'s existing AST scanner is **widened to a
single verdict-returning predicate** that now feeds BOTH the #3065 read-bound inventory and the new
guard-seam inventory of #6882.

**Scope of record:** `/tmp/pi-6882-scope.md` (adversarial threat surface T1–T8; cycle log below).
**Issue:** #6882. Finding 2 (bytes payloads) is **already fixed** at this head
(`cypher_guard.py:296 _as_text()`, pinned by
`test_cypher_regex_guard_3595.py::test_execute_command_refuses_the_operator_in_a_bytes_payload`) —
no code change; the PR states that with evidence.

## Pattern Research

**Skipped — the plan introduces zero third-party dependencies and no new external API surface.**
The only third-party shapes it relies on were read from the *installed, pinned* source, not from
training data:

| Dep | Pinned | Shape read | Where |
|---|---|---|---|
| `falkordb` | 1.6.2 | `FalkorDB.__init__` binds `self.connection` and `self.execute_command` as **instance** attributes; `Graph.__init__` binds `self.execute_command = client.execute_command` and does **not** copy `connection` | `.venv/.../falkordb/falkordb.py:150-151`, `graph.py:38` |
| `redis` | 7.4.1 | `Redis.execute_command(self, *args, **options)` is a façade: `return self._execute_command(*args, **options)` — the REAL dispatcher. `Redis` has **no** `send_command` (verified `hasattr` is False). `Pipeline` flushes on the Connection layer (`pack_commands`/`send_packed_command`) | `.venv/.../redis/client.py` |

## Integration Surface Map

| Integration surface | Layer | Why / bug pattern | Test |
|---|---|---|---|
| `cypher_guard._guard_connection` ↔ the raw `redis.Redis` connection object | unit (fake + real-vendor-shape) | **ownership of a foreign object's attribute** — must be in-place and idempotent; identity/type load-bearing | `tests/test_cypher_regex_guard_3595.py` |
| `cypher_guard` ↔ `falkordb`/`redislite` vendor shapes | unit on the REAL classes | vendor-shape drift (the round-2 `AttributeError` class) | same |
| 5 production `guarded_client(...)` constructions | static AST (both branches) + embedded runtime (embedded branch) | **silent revert** of a construction seam: the guard suite stayed green on a `guarded_client(...)` → `FalkorDB(...)` revert | `tests/test_bounded_raw_clients_3065.py` (AST, all 5) + `tests/test_guard.py` (runtime, embedded) |
| `.connection` consumers: `backup.py:668` (`BGSAVE`), `projection:421` (`_apply_embedded_connect_timeout` → `connection_pool`), `graph_delete_guard:158/181` (`EVAL` Lua lock), `fork_slot:196/212` (`connection_pool`/`pid`), `hosted_backup:2224` (`connection_pool.connection_kwargs`), `projection:7034` (`info`), `:12218` (`_host`/`port`), `:12379` (`MODULE LIST`); test-side `tests/_embedded.py:1160` | anti-regression | **over-fix**: the guard must not break a non-Cypher command or an attribute/identity read | `tests/test_redirect_seam.py`, `tests/test_bounded_raw_clients_3065.py` |

## Journey Test Map

(not user-facing — no journeys)

## Failure Modes

| Failure | Expected behaviour | Test |
|---|---|---|
| `=~` over `client.connection.execute_command` / `.connection._execute_command` | refused pre-dispatch, wire untouched | T1/T8 tests |
| A non-query command over the same channel (`BGSAVE`, `EVAL`, `MODULE LIST`) | forwarded untouched | T5 arm |
| Re-guarding the same channel (client + connection, or two clients sharing one connection) | no second wrapper; the marker is unforgeable by a `MagicMock` | T6b test |
| `_guard_handle` handed an already-guarded proxy | returned unchanged, no bogus guarded subclass cached | T6 test |
| Production wiring reverted to raw `FalkorDB(...)` | AST test reds for any of the 5 sites; runtime test reds for the embedded site | T7 AST + runtime tests |

**Tech Stack:** Python 3.12, pytest, `falkordb` 1.6.2, `redis` 7.4.1, `redislite` (embedded lane).

---

## Task 1: Red test — the `.connection` channel is unguarded (T1/T2/T3/T8)

**Intent:** Reproduce issue finding 3 as a failing test, at the real dispatcher too.
**Acceptance:** the new test fails on the pre-fix module (the `=~` statement reaches the spy wire).
**Files:**

- Modify: `tests/test_cypher_regex_guard_3595.py`

Steps:

1. Add a module-level `_VendorLikeClientWithConnection` stub mirroring the vendor: it binds
   `self.connection` and `self.execute_command` as **instance** attributes, and its `select_graph`
   returns the REAL `falkordb.Graph` **passing the client itself** (`VendorGraph(self, graph_id)`)
   — NOT the wire. This is load-bearing for the T2 route: the existing `_VendorClientWithRealGraph`
   passes the *wire* as the graph's client, so `handle.client.connection` would raise
   `AttributeError` rather than exercise the guard. Reusing that stub verbatim is not possible; say
   so in the test docstring.
   The connection itself must be a REAL `redis.Redis` **subclass** (so the in-place contract can be
   asserted honestly) whose `_execute_command` records instead of dialling.
   `_RecordingRedis.__init__` calls `super().__init__(host="localhost", port=1, socket_timeout=1)`
   and sets `self.sent = []`; `_execute_command` appends `args` and returns an empty result set.
   A `_SpyWire`-style plain object would fail the `isinstance` assertion.
2. Add imports of the privates the test drives: `_GUARDED_CHANNEL_MARKER`, `_GuardedHandleProxy`,
   `_carries_marker`, `_guard_handle`, `_guard_connection`, `_guarded_execute_command`.
3. Write `test_the_client_connection_channel_is_guarded`, asserting for each of four routes —
   (a) `guarded_client(Cls, wire).connection.execute_command(...)`,
   (b) `…connection._execute_command(...)`,
   (c) `…select_graph("g").client.connection.execute_command(...)`,
   (d) `guarded_client(factory).connection.execute_command(...)` — that a `=~` statement raises
   `UnsupportedCypherOperatorError` and `wire.commands == []`.
4. In the same test assert the **in-place contract** (the whole reason the replacement-proxy design
   was rejected): `client.connection is conn`, `type(client.connection) is type(conn)`, and
   `isinstance(client.connection, redis.Redis)`.
5. Add the anti-over-fix arms (T4/T5): the bytes payload is refused on the connection channel too,
   and a supported `GRAPH.DELETE` is forwarded (`wire.sent == [(...) ]`).
6. Run it and confirm it FAILS (the statement reaches the wire).

## Task 2: Red test — `_guard_handle` is not idempotent for the proxy shape (T6)

**Intent:** Reproduce issue finding 4.
**Acceptance:** the new test fails on the pre-fix module (`_guard_handle(proxy) is proxy` is `False`).
**Files:**

- Modify: `tests/test_cypher_regex_guard_3595.py`

Steps:

1. Write `test_guard_handle_is_idempotent_for_the_proxy_shape`: build a proxy via
   `guarded_client(lambda: stub).select_graph("g")`, assert `isinstance(proxy, _GuardedHandleProxy)`,
   `_guard_handle(proxy) is proxy`, that no `Guarded_` subclass of `_GuardedHandleProxy` was minted
   into `_GUARDED_GRAPH_CLASSES` (`assert not any("_GuardedHandleProxy" in c.__name__ for c in
   _GUARDED_GRAPH_CLASSES)`), and that calling `query` on the result still refuses `=~`.
2. Run it and confirm it FAILS.

## Task 3: Implement — class-based marked wrapper, in-place connection guard, idempotent `_guard_handle`

**Intent:** Close T1–T3/T8 and T6b at the seam, and make the in-place contract real.
**Acceptance:** Tasks 1–2 tests go green; `tests/test_redirect_seam.py` (the `isinstance` + pool-leak
test) stays green; `ruff` clean.
**Files:**

- Modify: `tortoise/cypher_guard.py`

Steps:

1. Add `_GUARDED_CHANNEL_MARKER` beside the other markers, documenting that **it is carried on a
   CLASS dict, not on a function instance**, because `_carries_marker` reads the class MRO — an
   attribute set on a closure is invisible to it, and checking it with a bare `getattr` would let a
   permissive double (`MagicMock` answers every name) forge "already guarded" and silently skip the
   guard (the round-2 P1 class). Verified: `_carries_marker(MagicMock().execute_command, marker)`
   is `False` for an MRO-based check.
2. Convert `_guarded_execute_command`'s wrapper from a closure to a small callable CLASS
   (`_GuardedExecuteCommand`, `__slots__ = ("__weakref__", "_raw")`, `__call__` runs
   `_guard_execute_command(args)` then delegates), and `setattr(_GuardedExecuteCommand,
   _GUARDED_CHANNEL_MARKER, True)` immediately after its definition. Make
   `_guarded_execute_command(raw)` return `raw` unchanged when `raw is None` or
   `_carries_marker(raw, _GUARDED_CHANNEL_MARKER)` is true — one shared declaration, read through
   the module's own unforgeable helper, so the three existing call sites get idempotence for free.
3. Add `_CONNECTION_COMMAND_ATTRS = ("execute_command", "_execute_command")` and
   `_guard_connection(conn) -> bool`: for **every** attribute present on *conn*, wrap it and
   `setattr` the wrapper **on the object**; return whether anything was wrapped. Document: (i) why
   the object must not be replaced (identity/type are load-bearing — an in-package consumer asserts
   `isinstance(client.connection, redis.Redis)`, and the vendor's own internals call
   `self.connection.<method>`); (ii) why BOTH attributes are bound (on the pin the public verb is a
   façade over the private dispatcher, so binding the private one covers the public — but binding
   both is strictly stronger against a subclass that overrode the public verb without delegating);
   (iii) the residuals — `pipeline()`, deeper raw access (`connection_pool.get_connection()`), and
   a connection subclass whose public verb does not delegate to `_execute_command`.
4. Wire it into the two client construction paths, defensively:
   `_guarded_execute_command(getattr(self, "execute_command", None))` then
   `_guard_connection(getattr(self, "connection", None))` in `_GuardedClient.__init__`, and
   `_guard_connection(getattr(client, "connection", None))` in `_GuardedClientProxy.__init__`. The
   guarded helpers must no-op on `None`, because existing green tests build clients with **no**
   `.connection` / `.execute_command` (`_VendorClientWithRealGraph`, `_StubClient`,
   `_ClientReturningStub`, `_VendorLikeClient`). No `__slots__` change: the proxy is not mutated —
   the underlying client's connection object is.
5. In `_guard_handle`, early-return `handle` when `isinstance(handle, _GuardedHandleProxy)`
   **before** `guarded_graph_class(type(handle))`, so no bogus `Guarded_GuardedHandleProxy` is
   minted or cached.
6. Rewrite the module docstring's SCOPE paragraph as **coverage with declared exclusions**: one
   stated invariant — "every Cypher-bearing command entry point reachable from a
   `guarded_client`-built client refuses `=~` before dispatch" — the three drivers (handle verbs,
   the client's own `execute_command`, the connection's command entries), and the exclusion list
   *derived from* that invariant rather than tacked on (`pipeline()`, deep pool access, raw clients
   built outside the package, test doubles).

## Task 4: Pin the production wiring (T7)

**Intent:** A one-line revert of any production `guarded_client(...)` must fail a test.
**Acceptance:** AST test reds on a revert of any of the 5 sites; the runtime test reds if the
embedded production construction stops carrying the marker.
**Files:**

- Modify: `tests/test_bounded_raw_clients_3065.py` (AST — both branches, no store, fast lane)
- Modify: `tests/test_guard.py` (runtime — embedded; already carve-out + redirect-exempt)

Steps:

1. **Unify the scanner (do not add a parallel one).** Change `_client_aliases` to return a
   `dict[local_name, original_name]` (resolving `from falkordb import FalkorDB as F`), and introduce
   `_client_verb(node, aliases) -> str | None` — the ONE predicate that returns the recognised
   client name a construction calls. Re-express `_constructs_a_client` as
   `_client_verb(...) is not None` so the existing #3065 scan is unchanged in behaviour and the two
   verdicts can never drift. Extend `_CLIENT_MODULES` to `{"redis", "falkordb", "tortoise"}` so the
   REAL home of the seam (`from tortoise.cypher_guard import guarded_client as G`) is resolvable —
   otherwise its alias control is vacuous — and say so in the helper's docstring.
2. Define the recognised-name set ONCE: `_FALKORDB_CLIENT_NAMES = frozenset({"FalkorDB",
   _GUARD_SEAM})` with `_GUARD_SEAM = "guarded_client"`, and derive
   `_CLIENT_NAMES = _FALKORDB_CLIENT_NAMES | {"Redis", "StrictRedis", "from_url"}` — no hand-copied
   subset.
3. Add `_EXPECTED_GUARD_SEAM_SITES` = the 5 measured sites (`projection/__init__.py: 2`,
   `backup.py: 1`, `session_indexer.py: 1`, `__main__.py: 1`).
4. Add `test_every_product_falkordb_client_goes_through_the_guard_seam`: walk the same
   `PRODUCT_PKG.rglob("*.py")` scan; every `_FALKORDB_CLIENT_NAMES` construction whose verb is not
   `_GUARD_SEAM` is an offender (with file:line); the seam inventory must equal
   `_EXPECTED_GUARD_SEAM_SITES`; anti-vacuity (the total is 5). Add the **parity pin** against the
   #3065 inventory — `_EXPECTED_GUARD_SEAM_SITES[rel] <= _EXPECTED_SITES[rel]` for every rel — so a
   new construction cannot satisfy one guard while silently skipping the other.
5. Extend `test_the_guard_itself_reads_the_shapes_it_claims_to` with negative controls for
   `_client_verb`: `FalkorDB(...)`, `guarded_client(...)`, `falkordb.FalkorDB(...)`,
   `from falkordb import FalkorDB as F; F(...)` → `"FalkorDB"`,
   `from tortoise.cypher_guard import guarded_client as G; G(...)` → `"guarded_client"`, and an
   unrelated call → `None`. **Update the existing assertions for the dict return** — both the
   `_client_aliases(...) == {...}` lines AND the `_constructs_a_client(_only_call("R(...)"), {"R"})`
   call (`assert _constructs_a_client(_only_call("R(host='h', port=1)"), {"R": "Redis"})`), or
   `_client_verb` receives a `set` and the guard mis-reads.
6. Add `test_production_projection_client_goes_through_the_guard_seam` to `tests/test_guard.py`
   (beside the existing `test_projection_still_works_with_guard_active`, which already builds and
   closes a real embedded `FalkorProjection`): build an embedded projection in a temp dir, assert
   `_carries_marker(proj.db, _GUARDED_CLIENT_MARKER)`, and close/clean up. This file is the
   carve-out + redirect-exempt home for it, so the run is genuinely embedded (no redislite server in
   the fast pool, and no redirect that would make the assertion about the other branch).
7. **Mutation proof (pin the env).** Under `env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1`
   (embedded): revert `projection/__init__.py:6804` to raw `FalkorDB(...)`, run the two new tests,
   confirm BOTH red, restore. Then under the docker lane (`TORTOISE_DB_URI=…`) revert the host site
   `:6861` and confirm the AST test reds (the runtime test there exercises whichever branch the lane
   built — state that in the test docstring rather than assuming which).

## Task 5: Verify

**Intent:** Prove the change on the real surfaces.
**Acceptance:** all commands below pass; ruff clean.
**Files:** —

Steps:

1. `env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
   .venv/bin/python -m pytest tests/test_cypher_regex_guard_3595.py
   tests/test_bounded_raw_clients_3065.py tests/test_guard.py -q -p no:cacheprovider`
2. Docker lane: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' …`
   `pytest tests/test_redirect_seam.py -q` — the `isinstance` / pool-leak test the rejected design
   would have reddened.
3. `.venv/bin/python -m ruff check tortoise/cypher_guard.py tests/test_cypher_regex_guard_3595.py
   tests/test_bounded_raw_clients_3065.py tests/test_guard.py`

---

## Review Cycle Log

### problem-verify (scope) — Cycle 1

- Verifier 1: P1=1 (redirect-seam `isinstance(p.db.connection, redis.Redis)` would red under the
  draft's replacement proxy), P2=1 (`pipeline`/`send_command` residual), P3=1 (wrong class name in
  the precedent citation), P4=1 (stale "29 tests")
- Controller action: **Fixed P1** by retargeting the design to an in-place attribute rebind (no
  object replacement, no test edit); **Incorporated P2** — declared `pipeline` out of scope at its
  real width, **rejected** the `send_command` arm (it does not exist on `redis.Redis` 7.4.1);
  **Fixed P3**; **Fixed P4** (re-measured: 34).
- Re-dispatching.

### problem-verify (scope) — Cycle 2

- Verifier 1: P2=1 (the `execute_command`-only target leaves `connection._execute_command`, the
  real dispatcher, open), P4=2 (`projection:12218` row omitted; the marker claim was unimplemented)
- Controller action: **Incorporated all three** — the mechanism now binds `_execute_command` (with
  the public name as fallback), which closes both channels with one wrap; the table row is added;
  the marker is implemented in Task 3. **No P0/P1 returned → gate PASSES.**
- `[ADVERSARIAL-BOUND] cycles=2 threats=8 covered=8 residuals=none`

### plan-review — Cycle 1

- Plan reviewer: P1=1 (the marker was to be set on a **function instance**, which the module's own
  MRO-based `_carries_marker` cannot read — so the stated idempotence would silently not exist, and
  reading it with a bare `getattr` would let a `MagicMock` forge "already guarded"), P2=2
  (`_guard_connection` defensiveness dropped; mutation proof lane-dependent), P2=1 (redislite in the
  fast pool / wrong lane for the runtime assertion), P3=2 (unreachable fallback arm; the T2 stub
  contract), P4=1 (two table omissions)
- Duplication/architecture reviewer (advisory): the marker mechanism must be a CLASS-carried marker
  read through `_carries_marker` (same P1); the planned second AST scanner + second name set +
  second inventory are duplication (D1) against `_constructs_a_client`/`_CLIENT_NAMES`/
  `_EXPECTED_SITES` and must be unified; add a direct identity/type assertion at the new seam; state
  one invariant rather than an exception list.
- Controller action: **Fixed P1** (class-carried marker + `_carries_marker`); **Fixed** the
  defensiveness and stub-contract P2s in the tasks; **Fixed** the lane problem by moving the runtime
  assertion to `tests/test_guard.py` (carve-out + redirect-exempt, already an embedded-projection
  test) and pinning the mutation-run env; **Fixed** the unification findings (one predicate, one
  name set, one inventory + a parity pin); **Incorporated** the identity/type assertion and the
  single-invariant docstring; **Incorporated** the table omissions. `execute_command` is now bound
  *in addition to* `_execute_command`, which closes the unreachable-fallback residual.
- Re-dispatching.

### plan-review — Cycle 2

- Plan reviewer: **`THREAT SURFACE COVERED`** (no P0/P1/P2 remain). Six P3/P4 advisories, all
  incorporated: keep the `if raw is not None` guard rather than an unconditional assignment; make
  the new stub's `.connection` a real `redis.Redis` **subclass** (else the `isinstance` assertion
  is unsatisfiable); make the seam's real alias path resolvable (`_CLIENT_MODULES` gains
  `"tortoise"`) so the alias control is not vacuous; word the invariant as coverage-with-declared-
  exclusions; update the existing `_constructs_a_client(..., {"R"})` control to the dict form; and
  pin the “no bogus guarded subclass cached” half of T6 with consistent test names.
- `[ADVERSARIAL-BOUND] cycles=2 threats=8 covered=8 residuals=none`

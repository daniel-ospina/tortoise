# Plan: #2866 — bounded, stated DCR capacity policy + reachable CIDR exemption

**Issue:** [#2866](https://github.com/daniel-ospina/tortoise/issues/2866) — Level: `task`, complexity: `standard`
**Parent:** #2833 · **Branch:** `feat/2866-dcr-capacity-policy` · **Tier:** standard (task-workflow-standard)
**Scope artifact:** issue #2866 comment (`scope-2866.md` rev 4 — 4 problem/solution verify cycles, no P0/P1 open)
**Revision:** plan rev 8 (plan-review cycles 1–7: Structural, Integration, Failure-Mode, Duplication/Architecture). Where the scope artifact (`/tmp/scope-2866.md`) and this plan differ on `IPV6_PREFIX` semantics, the CIDR cache, or leg (o), **this plan governs** (no CIDR cache; out-of-range prefix falls back to 64; leg (o) is split).

**Research path.** Step A (prior research intake): the scoping artifact's assumption map + Axis Research.
Step B (multi-call Perplexity gate): **justified-skip** — zero third-party dependencies (stdlib
`ipaddress`/`collections`/`math`/`time` only); every pattern has in-repo precedent. Integration Docs:
N/A. External provenance for the one external fact: trusted CIDR `160.79.104.0/21` = Anthropic's
published outbound/MCP egress (`platform.claude.com/docs/en/api/ip-addresses`; whois AP-2440).

---

## 1. Problem (confirmed)

The `/register` DCR limiter (`hosted_api.py:21687-21689`, `:21942-21948`) has no stated policy and an
enforcement shape that cannot do the job: it reuses `_check_ip_bucket_rate_limit`, which inserts a
bucket before the 429 check (`:3131-3135`) and prunes only *stale* buckets (`:3146-3151`), so a
fresh-key flood grows the store unboundedly and each request scans it once over `max_entries`; there is
**no CIDR exemption** anywhere in `tortoise/` (it must be built); DCR rejects `offline_access` while
the AS issues refresh tokens; and `oauth_clients` growth has no named owner/date.

## 2. Design decisions (locked)

| # | Decision | Rationale |
|---|---|---|
| D1 | The DCR limiter gets **its own four stores**; `_check_ip_bucket_rate_limit` is untouched | ~14 call sites share the primitive; the issue forbids changing it |
| D2 | **Stated policy:** per-bucket 20/hr; anonymous global aggregate 600/hr; trusted-CIDR aggregate 1200/hr; `STORE_CAP` 256; IPv6 `/64` keying; window 3600 s. **Dimension membership (from the scope, binding):** *Trusted ⇒ per-CIDR aggregate only* — no per-key bucket, no shared overflow, no anonymous aggregate; *anonymous ⇒ per-key bucket (or shared overflow) **and** the anonymous global aggregate*. The trusted carve-out is evaluated **before** any per-key/overflow path, so the 1200/hr exemption is actually reachable under an anonymous flood | Concrete, falsifiable numbers; `STORE_CAP (256) < ANON_AGGREGATE (600)` keeps reject-new/overflow live; without the carve-out a first-time trusted IP is a "new key" and would 429 at `STORE_CAP + PER_HOUR = 276`, making the issue's exemption unreachable |
| D3 | **Eviction:** reclaim-expired + reject-new + shared overflow. A bucket is active iff it has an in-window entry; reclaim pops the LRU head while inactive; only if still at cap is the new key denied its bucket and charged to one shared overflow bucket | Deterministic; cannot evict an active key; no permanent store saturation |
| D4 | Store order = **last charge**; `move_to_end` on a **charged hit** only; reclaim runs **only** on the new-key-at-cap path | Makes D3's invariant sound ("stop at first active ⇒ rest active"); tracked-key charge stays O(1) |
| D5 | **Atomic dimensions:** one lock; phase 1 evaluates all dimensions (may evict *inactive* buckets; never charges/inserts), phase 2 inserts+charges only if all pass | A 429 charges nothing and inserts nothing |
| D6 | IPv6 store key = `/64` **after** `_normalize_mapped_ipv6`; CIDR match on the normalized IP; malformed IP/CIDR fail closed; an **out-of-range IPv6 prefix falls back to the default 64** (not clamped — clamping `0`→`1` would collapse all IPv6 into one bucket); malformed client IP keys as the single constant `"anonymous"` (not the raw string, so a malformed-identity flood cannot occupy `STORE_CAP` buckets); **missing client (`request.client is None` / empty host) early-returns like the primitive (`hosted_api.py:3123`) — no `AttributeError`, no charge** | Root-cause fix for the single-/64 flood; prevents `::/64` collapse; no 500 on an unauthenticated endpoint |
| D7 | `SCOPES_SUPPORTED = ["mcp"]` stays (default + PRM); `SCOPES_ACCEPTED = [*SCOPES_SUPPORTED, "offline_access"]` drives the DCR gate (`oauth.py:343`), the error string (`:345`), and AS metadata (`:857`) | Accepts Claude's scope without changing any default fallback or the PRM document; the superset relation is structural + asserted |
| D8 | All knobs read **at call time** via `_int_env` with module `*_DEFAULT` constants; `TRUSTED_CIDRS` comes from the env **verbatim when set** — **comma-separated** (whitespace-trimmed, empty items skipped; a malformed item is skipped without aborting the list) — an **empty string ⇒ an empty trusted set (fail closed, the documented lever to disable the exemption)**, never the default (no `or DEFAULT` falsy-coalescing); **no CIDR cache** (parse per call); `limit <= 0` denies with a synthesized `Retry-After = window_s` **per dimension** (never indexes an empty bucket) — **this zero-rule applies to the three *rate* knobs only** (`PER_HOUR`, `ANON_AGGREGATE_PER_HOUR`, `TRUSTED_PER_HOUR`); the capacity/index knobs keep their own semantics (`STORE_CAP=0` ⇒ the overflow path, per (o3); an out-of-range `IPV6_PREFIX` ⇒ fall back to 64, per D6); `_OAUTH_DCR_ANON_KEY`/`_OAUTH_DCR_OVERFLOW_KEY` are the singleton store keys; malformed client IP keys as the constant `"anonymous"` | Testability; no staleness foot-gun; no `IndexError` 500 on any dimension; the disable-the-exemption lever is reachable |
| D9 | An **overflow charge is charged to `_OAUTH_DCR_OVERFLOW` AND `_OAUTH_DCR_ANON`** | Otherwise the stated "anonymous ≤ 600/hr" bound is asserted but not enforced |
| D10 | Window semantics are **not** re-derived: the DCR limiter uses pure helpers (`_dcr_prune_window`, `_dcr_retry_after_s`) that the shared primitive's *contract* is pinned against by a parity test — the primitive's code stays byte-identical | Avoids silent drift between the copied window math and the primitive (Duplication/Architecture reviewer I1) without touching the primitive |

Accepted limitations (stated in code comment + PR): in-process stores ⇒ `limit × machines`, reset on
restart (#1677 — process commitment, no automated check); the limiter runs **before body parsing**, so
invalid-JSON/oversized 400/413 POSTs **do** consume budget (charges ≤ 600/hr anonymous + 1200/hr
trusted; row writes are NOT bounded — #2853 owns pruning); the distinct-new-anonymous-key ceiling is
**`STORE_CAP + PER_HOUR = 276/hr`** (256 live keys + 20 overflow) — a *burst/concurrent-live* bound, **derived**
from the constants pinned by leg (r) and exercised structurally at small scale by legs (b)/(o3); it is a
stated derivation, **not** a bound tested at shipped scale; the global aggregate is a one-source DoS
(trusted CIDRs unaffected — see D2's dimension rule); `/register` also passes the generic `RateLimitMiddleware` whose `_buckets`
store has no `max_entries` (a second attacker-keyed store on the same endpoint — filed as sibling
issue (a)); the exemption's security rests on the Fly edge stripping client `Fly-*` headers
(assumption 12, operator recipe + dated re-verification issue with owner/date); wall-clock
`time.time()` (no monotonic seam) — consistent with the primitive; a backward clock step can delay
reclaim, documented not fixed.

**Rejected:** approach B (parametrize the shared primitive), C (LRU evicting active buckets), D
(out-of-process limiter, #1677), E (CIMD, #2847), F (bounded store only), G (aggregate or /64 alone),
H (aggregate-only — loses per-key fairness), I (stale-reclaim only with cap ≥ aggregate — makes the
Target-required reject-new/overflow branch dead at defaults). Moving the pure core to a new
`tortoise/dcr_capacity.py` was considered (Duplication/Architecture I4) and rejected: every limiter and
store in this module lives in `hosted_api.py`; a new module would split the pattern-family and add a
file for ~120 lines. Recorded, not silently dropped.

## 3. Integration Surface Map

| # | Surface | Type | Consumers / readers | Test layer | Failure modes (≥2) |
|---|---|---|---|---|---|
| S1 | `_check_oauth_dcr_rate_limit` + 4 stores (+ pure helpers) | in-process state | `/register` only | HTTP integration + unit (`tests/test_oauth_mcp.py`) | store grows unbounded; active bucket evicted; concurrent interleaving yields `limit+N` charges |
| S2 | `/register` endpoint call site | HTTP API | Claude/ChatGPT/Cursor DCR clients | HTTP integration | 500 on malformed `Fly-Client-IP`; wrong bucket key → global 20/hr |
| S3 | `ClientIPMiddleware` trust flag + `fly.toml [env]` | config seam | all per-IP limiters | HTTP integration + `tomllib` config test | flag missing ⇒ exemption dead; flag on a non-proxy ingress ⇒ spoofable |
| S4 | `_oauth_control_plane()` registry-mode 503 | mode gate | selfhost/registry deployments | HTTP integration | 503 consumes a charge / creates a bucket |
| S5 | `SCOPES_ACCEPTED` / `authorization_server_metadata` | protocol metadata | MCP clients (scope discovery), DCR gate | unit + HTTP integration | `offline_access` 400s registration; AS metadata disagrees with the gate; `SUPPORTED ⊄ ACCEPTED` |
| S6 | authorize/consent scope forward (`hosted_api.py:21752/21779/21865`) + store (`oauth.py:499`) | protocol path (unchanged) | consent page | existing tests + filed sibling issue | unvalidated scope minted into a token (filed, not fixed here) |
| S7 | Generic `RateLimitMiddleware` (100/min, no `max_entries`) also wrapping `/register` | middleware | all routes | non-interference assertion in the DCR fixture | test-isolation break if its `_disabled` is captured with the flag removed; residual unbounded store |
| S8 | `docs/oauth-mcp.md` env-knob table | docs | operators | review | stale knob docs |

## 4. Tasks

**Parallelism map (AGENTS.md batch rule):** Tasks 1 (scope vectors), 3 (sibling filings), and 5
(config test) are independent of everything and start immediately. Task 2 (limiter) is independent of
1/3/5. Task 4 (comment + docs) **depends on Task 2 and Task 3** — it edits the same `_OAUTH_DCR_*` block
Task 2 rewrites, and its comment must cite the Task 3 issue numbers. Only the commit is serialized.

### Task 1: DCR scope-vector decision (`offline_access`) — independent
**Intent:** Stop 400ing Claude's scope while keeping every default-scope path unchanged.
**Acceptance:** `SCOPES_SUPPORTED == ["mcp"]`; `SCOPES_ACCEPTED = [*SCOPES_SUPPORTED, "offline_access"]`;
gate `oauth.py:343` + error string `:345` + AS metadata `:857` use `SCOPES_ACCEPTED`; PRM `:839` uses
`SCOPES_SUPPORTED`; defaults at `:339,499,703,769` byte-identical; a test asserts
`set(SCOPES_SUPPORTED) <= set(SCOPES_ACCEPTED)`.
**Files:** Modify `tortoise/oauth.py`; Test `tests/test_oauth_mcp.py`.

### Task 2: DCR limiter — stated policy implementation — independent
**Intent:** Replace the unbounded, policy-free DCR limiter with the stated policy; shared primitive
byte-identical.
**Acceptance:** `_check_ip_bucket_rate_limit` source hash unchanged vs `origin/main`; `/register` calls
the new limiter; the **five int knobs** (`PER_HOUR`, `ANON_AGGREGATE_PER_HOUR`, `TRUSTED_PER_HOUR`,
`STORE_CAP`, `IPV6_PREFIX`) exist as `_OAUTH_DCR_*_DEFAULT` constants read via call-time `_int_env`;
the one string knob (`TRUSTED_CIDRS`) is a `_OAUTH_DCR_TRUSTED_CIDRS_DEFAULT` + call-time env read; the
overflow cap is derived (`= PER_HOUR`) and the window is a fixed `_OAUTH_DCR_WINDOW_S = 3600`; the
`STORE_CAP < ANON_AGGREGATE` relation holds in defaults; malformed IP/CIDR and out-of-range prefix never
raise; `limit <= 0` denies without creating a bucket; D5/D9 hold; **a repeat charge on an
already-tracked key consumes the anonymous aggregate**; no CIDR cache; the limiter reads the four stores
**dynamically off the module globals** (no default-argument capture, no local alias — the test fixture
swaps them) and **must not call `_charge_ip_bucket`** (that helper takes `_OAUTH_DCR_LOCK`, and
`asyncio.Lock` is non-reentrant → self-deadlock); charging is inline in phase 2; trusted traffic takes
the D2 carve-out **before** the per-key/overflow path.
**Files:** Modify `tortoise/hosted_api.py` (add `import ipaddress`; DCR block ~`21687`; `/register`
~`21942`). The four stores keep the name `_OAUTH_DCR_BUCKETS` (re-declared `OrderedDict`) plus
`_OAUTH_DCR_TRUSTED`, `_OAUTH_DCR_OVERFLOW`, `_OAUTH_DCR_ANON`; the old `defaultdict` initializer and
`_OAUTH_DCR_MAX_PER_HOUR` are deleted.
**Steps:** (1) write the failing limiter legs first and record the base-failing set; (2) constants +
stores + pure helpers `_dcr_prune_window(bucket: list[float], now: float, window_s: int) -> list[float]`,
`_dcr_retry_after_s(bucket: list[float], now: float, window_s: int) -> int` (non-empty bucket assumed —
the caller guards), `_oauth_dcr_trusted_networks` (parse
per call), `_oauth_dcr_trusted_net`, `_oauth_dcr_store_key`, `_oauth_dcr_reclaim`; (3) implement
`_check_oauth_dcr_rate_limit` per D2–D6, D8, D9; (4) switch `/register`; (5) run → PASS.

### Task 3: Sibling issue filings — independent (must precede Task 4)
**Intent:** File the pre-existing defects instead of absorbing them.
**Acceptance:** one issue each for (a) shared primitive + generic `RateLimitMiddleware._buckets`
unbounded attacker-keyed stores; (b) `_check_claim_rate_limit` proxy-IP keying + dead 24 h prune;
(c) sibling token-table pruning (`oauth_codes`/`oauth_refresh_tokens`/`oauth_access_tokens`);
(d) dated live-edge re-verification of `Fly-Client-IP` non-overridability, with **owner @daniel-ospina
and an absolute date** and the operator recipe in the body; (e) unvalidated authorize/consent scope.
**Files:** GitHub only.

### Task 4: Pruning owner/date + docs — depends on Task 2 and Task 3
**Intent:** Remove the silent-rot on `oauth_clients` growth and document the new knobs.
**Acceptance:** the DCR block comment names **owner @daniel-ospina (epistemic-team)** and the **absolute
date 2026-10-15** and references #2853 + the Task 3 issue numbers; #2853 gets an assignee + dated
comment (a process commitment — no automated check, noted in the comment); `docs/oauth-mcp.md` lists all
new knobs and the stated policy table (incl. the derived **276/hr distinct-new-key** ceiling) (the docs
table + the code `*_DEFAULT` constants are the single source of truth; the PR body links rather than
restates).
**Files:** Modify `tortoise/hosted_api.py` (comment), `docs/oauth-mcp.md`; GitHub (#2853).
**Test list:** the limiter legs (a)–(y) all live in `TestDcrCapacityPolicy` in
`tests/test_oauth_mcp.py`, under the **function-scoped autouse** isolation fixture described in §5 (leg
(q) re-sets the flag inside the test); the Task 5 config test lives in
`tests/test_client_ip_middleware.py`.

### Task 5: fly.toml config test — independent
**Intent:** Prevent the exemption from silently becoming dead code.
**Acceptance:** a test reads the repo `fly.toml` with `tomllib` and asserts
`env["TORTOISE_TRUST_FLY_CLIENT_IP"] == "1"`.
**Files:** Test `tests/test_client_ip_middleware.py`.

### Task 6: Test list (executed within Tasks 1, 2, 5)
The limiter legs (a)–(y) live in `TestDcrCapacityPolicy` in `tests/test_oauth_mcp.py`, with the
isolation fixture described in §5; the config test is Task 5.

## 5. Testing strategy

Red→green with recorded differential evidence. **Honest base-failing set:** legs (a), (c), (d), (j),
(k), (q), (s) and the **Task 5** config leg pass trivially on `origin/main` (base already 429s a per-key
limit, never evicts, ignores a spoofed header without the flag, early-returns on `RATE_LIMIT_DISABLED`,
and already ships `TORTOISE_TRUST_FLY_CLIENT_IP = "1"` at `fly.toml:59`) — they guard *alternative*
wrong implementations, not the base. Base-failing: (b)(e)(f)(g)(h)(i)(l)(**m**)(n)(**o1/o2/o3**)(**r**)
(t)(u)(v)(w)(**x**)(**y**) plus the Task 1 leg (p) — main inserts before the 429 check, reads the cap at import, has no
`_OAUTH_DCR_*_DEFAULT`, no eviction/CIDR//64/normalization logic, and no aggregate. Legs that reference
new symbols (`_dcr_prune_window`) *error* on base rather than fail — recorded as such, not as “pass”.
The isolation fixture is **function-scoped** (`autouse=True` on `TestDcrCapacityPolicy`) — it must depend
on the function-scoped `api_client` (`tests/test_oauth_mcp.py:170`), so `scope="class"` would raise
`ScopeMismatch`; a function scope also keeps the fresh `_OAUTH_DCR_LOCK` bound to the current event loop
(the concurrency leg's `asyncio.run` loop), which a class-scoped lock would break with
`RuntimeError: … bound to a different event loop`. It is the load-bearing seam (reviewer P1): Starlette
builds the middleware stack
lazily on the **first ASGI call**. `TestClient(app).__enter__` triggers that build (via the lifespan
call) **inside `api_client`**, which is guaranteed to run while `RATE_LIMIT_DISABLED=1`
(`tests/conftest.py:22` + `tests/test_oauth_mcp.py:32`) is still set — so the generic
`RateLimitMiddleware` is constructed with `_disabled=True` and stays disabled. The fixture therefore
**depends on `api_client`, issues one GET first** (belt-and-suspenders rebuild check, not the
mechanism), **then** `monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)` and resets the four DCR
stores + a fresh `_OAUTH_DCR_LOCK` by module-global lookup. If any earlier test ever deleted the flag
before the session's first app call, the disabled build is not recoverable — so the fixture also
asserts the live generic middleware instance has `_disabled is True` and fails loudly rather than
silently breaking the flood legs. Limiter legs run through the real endpoint (`TestClient` +
`FakeControlPlane`); the store-cap legs override `STORE_CAP=8, ANON_AGGREGATE=10⁶` and the aggregate
legs override `ANON_AGGREGATE=3`, leg (i) overrides `TRUSTED_PER_HOUR=2` (+ `PER_HOUR=1`), leg (w)
overrides `ANON_AGGREGATE=1`, and legs (x)/(y) override `TRUSTED_CIDRS`/`IPV6_PREFIX`, so neither bound
masks another. **All knob overrides use `monkeypatch.setenv`** (function-scoped, shared with the autouse
fixture, auto-restored) — the fixture must never mutate `os.environ` directly, or leg (y)'s
`IPV6_PREFIX=48` would leak into leg (n)'s `/64`-distinctness assertion and leg (i)'s `PER_HOUR=1` would
bleed out. The DCR fixture sets **`TORTOISE_TRUST_FLY_CLIENT_IP=1`** — legs (h)(i)(n)(w)(x)(y) and every
distinct-key flood leg deliver their subject IP via `Fly-Client-IP`, and without the flag
`ClientIPMiddleware.dispatch` falls back to `request.client.host`. **Leg (j) toggles it back off** (the
fail-closed path); **leg (k) keeps it set** — its whole point is *flag set* + no proxy header + a spoofed
`X-Forwarded-For` ⇒ still not exempt, so turning the flag off there would collapse it into (j). A fake clock
(`monkeypatch.setattr(hosted_api.time, "time", …)`) drives the aging/ordering legs. The non-O(n) leg
injects a counting `OrderedDict` prepopulated with 10 001 entries (base scans, new does not). One
concurrency leg uses `httpx.ASGITransport` + `asyncio.gather` (the `test_agent_signup_idempotency.py`
pattern) to pin D5 under interleaving — it must **not** first drive `/register` through the TestClient
(the fresh `_OAUTH_DCR_LOCK` must bind to the `asyncio.run` loop). Distinct anonymous identities come
from `Fly-Client-IP` + `TORTOISE_TRUST_FLY_CLIENT_IP=1` with non-trusted addresses.

Legs (in `tests/test_oauth_mcp.py`):
(a) anonymous per-key 429 + `Retry-After >= 1` at the limit; (b) fresh-key flood (257 distinct /64s):
store `<= STORE_CAP`, overflow binds with 429 + `Retry-After`, and a genuine non-exempt new IP still
429s while the store is full; (c) a tracked key's 429 does NOT reset after `STORE_CAP + k` fresh keys;
(d) **ordering invariant:** charge A, fill to cap with B at t=0, recharge A at t=3500, new C at
t=3601 is a **member of `_OAUTH_DCR_BUCKETS`** (assert membership, not 201); (e) aged buckets reclaimed
(all expired heads) and A survives; (f) **non-O(n):** 10 001-entry prepopulated counting store + tracked
key → 0 iterations; new key at an all-live cap → `<= 1` head inspection; counting subclass overrides
`__iter__/items/keys/values/__reversed__/copy`; (g) IPv6 `/64` collapse (two addresses → one bucket,
2 entries) **and** a second charged hit from the same /64 (no `KeyError`, key becomes MRU);
(h) `::ffff:160.79.104.11` trusted (normalize before match) **and** `::ffff:1.2.3.4` untrusted → key
`1.2.3.4`, not `::/64`; (i) **trusted carve-out reachable:** `TRUSTED_PER_HOUR=2` and a **low per-key
cap (`PER_HOUR=1`)** — one trusted IP registering **twice** via `Fly-Client-IP` → 2×201 with
`_OAUTH_DCR_BUCKETS` empty (a design that charges trusted traffic to the per-key/overflow path 429s the
second request → the leg discriminates), **then** the trusted aggregate binds: a **3rd trusted
registration from a distinct IP in the same /21** → 429 with `Retry-After` present (the default
`TRUSTED_CIDRS` is one CIDR, so the aggregate is a single bucket); (j) flag unset + spoofed `Fly-Client-IP` ⇒ not exempt; (k) flag set
+ no `Fly-Client-IP` + a spoofed `X-Forwarded-For` set to an address **inside** the trusted range
(e.g. `160.79.104.11`, so a wrongly-XFF-reading impl would wrongly trust it) ⇒ not exempt; (l) anonymous aggregate binds across
distinct keys → 429; (m) atomicity both directions + **overflow charges the aggregate** (D9);
(n) malformed `Fly-Client-IP` → keyed as `"anonymous"`, no 500; malformed CIDR → skipped, no 500;
**missing client (`request.client is None`) → early-return, no 500, no charge**;
**prefix `0`/`129`/`-1`/`abc` → default 64 (out-of-range falls back, never clamps), no 500, and two
addresses that differ only in the 4th hextet stay in distinct /64 buckets**; **a mixed list (one valid +
one malformed entry) keeps the valid entry trusted while skipping the malformed one**; (o) `limit<=0`,
split into **three separate test functions** (monkeypatch is per-test: a shared function would leak
`ANON_AGGREGATE=0`/`TRUSTED_PER_HOUR=0` into (o2)/(o3); each sub-case sets every knob it relies on):
(o1) `PER_HOUR=0` (anon key) / `ANON_AGGREGATE=0` (anon key) / `TRUSTED_PER_HOUR=0` (trusted key) → **request 1** is
429 with `Retry-After == window_s` **on that dimension**, no bucket, no 500 (the guard is per-dimension);
(o2) `PER_HOUR=1` → request 1 201, request 2 429;
(o3) `STORE_CAP=0` → the first anonymous key is served by `_OAUTH_DCR_OVERFLOW` (201,
`_OAUTH_DCR_BUCKETS` stays empty) and the 21st new key in the window 429s; (p) scope vectors `None | "mcp" | "mcp offline_access"` (plus
standalone `"offline_access"`) → 201 + granted scope, `"admin"` → 400 with an
`SCOPES_ACCEPTED`-correct message, AS metadata == `SCOPES_ACCEPTED`, PRM == `SCOPES_SUPPORTED`,
`SUPPORTED ⊆ ACCEPTED`, and the `mcp offline_access` round trip yields a **refresh_token** that
completes a refresh grant; (q) `RATE_LIMIT_DISABLED=1` → limiter no-op; (r) default constants == the
stated policy and `STORE_CAP < ANON_AGGREGATE` (this constant pin **is** the coverage for the shipped
`600`/`1200` caps — the enforcement path is value-independent and every other bound is binding-tested;
the two stated caps are deliberately not run at shipped scale); (s) **registry mode** 503 precedes the limiter and
consumes no charge; (t) **parity:** the DCR window helpers produce the same prune set and Retry-After
as the primitive **itself** (D10) — the oracle is `_check_ip_bucket_rate_limit(..., limit=1,
retry_after_s=None, defer_charge=True, detail="parity")` with `time.time` pinned: rows whose bucket
prunes to **empty fall through (no 429)** and are compared on post-prune contents only, while rows with
≥1 surviving in-window entry 429 and their `Retry-After` is compared against `_dcr_retry_after_s`; the
age matrix **must include the exact boundary (`now - t == window_s`), a just-inside age, and a
non-integer remainder**; (u) **concurrency:** `PER_HOUR=1` + 6 gathered POSTs → exactly 1×201, 5×429,
bucket `<= 1`; `STORE_CAP=1` + 4 concurrent distinct keys → store `<= 1` (**as two separate test
functions** — two `asyncio.run` calls with one `asyncio.Lock` raise `RuntimeError: bound to a different
event loop` once the lock has been contended; the fixture's per-test fresh lock makes the split correct,
and no scenario may drive `/register` through the TestClient after its gathered ASGI calls); (v) **a repeat charge on a
tracked key consumes the anonymous aggregate:** `ANON_AGGREGATE=3`, one fixed client IP, 4 POSTs →
3×201 then 429, and `len(_OAUTH_DCR_ANON[_OAUTH_DCR_ANON_KEY]) == 3`; (w) **a trusted request does not
consume the anonymous aggregate:** with `ANON_AGGREGATE=1`, one trusted (`Fly-Client-IP` in the CIDR)
registration then one fresh anonymous registration → both 201 (the aggregate is still 0 before the
anonymous charge); (x) **`TRUSTED_CIDRS` is a live knob (D8):** set `TORTOISE_OAUTH_DCR_TRUSTED_CIDRS` to a custom range → an
address **inside the default `160.79.104.0/21`** (e.g. `160.79.104.11`, so the assertion is not vacuous
under an `or DEFAULT` read) is **no longer exempt** — it lands in its own per-key bucket
(`160.79.104.11` ∈ `_OAUTH_DCR_BUCKETS`) and 429s at `PER_HOUR`; an address in the **custom** range is
exempt; an **empty** value (`""`) ⇒ fully anonymous (the D8 lever to disable the exemption); (y) **`IPV6_PREFIX` is a live knob:** `IPV6_PREFIX=48` → two addresses that **agree on the first 3
hextets and differ in the 4th** (e.g. `2001:db8:aaaa:1::1` vs `2001:db8:aaaa:2::1`) **collapse into one
bucket**, and the same pair stays **distinct** under the shipped `64` default — the coarser-collapse case
the default cannot exercise. (a /48 is hextets 1–3 and a /64 is hextets 1–4, so a difference in the
**3rd** hextet would collapse under *neither*.)

## 6. Verification plan

1. `uv run python tools/ci_selection.py --integrity` (no new test file → expected clean).
2. `env -u TORTOISE_API_KEY TORTOISE_SECRET_PEPPER=test-static-pepper RATE_LIMIT_DISABLED=1
   TORTOISE_DB_URI='docker://:falkordb@localhost:6380/tortoise_2866' uv run pytest
   tests/test_oauth_mcp.py tests/test_client_ip_middleware.py -q`
   (port 6380 is this machine's healthy FalkorDB sibling, designated in the task brief so concurrent
   agents do not collide on the 6379 default; the DB name is session-unique. **Local-only** — CI uses
   the repo default.)
3. **Byte-identity of the shared primitive** — source-hash, not grep (the plan's first draft used a
   grep that counts the *removed* `/register` call line and can never return 0):
   `uv run python -c "import ast,hashlib,pathlib,subprocess; ..."` extracting
   `_check_ip_bucket_rate_limit` from the worktree and from `git show origin/main:tortoise/hosted_api.py`
   and comparing SHA-256; must be equal.
4. Red/green differential evidence + the recorded base-failing set in the PR body.

## 7. Acceptance criteria (issue Target → coverage)

| Target | Covered by |
|---|---|
| stated policy with concrete numbers in the PR | PR body (links docs) + `docs/oauth-mcp.md` + Task 2 constants |
| bounded store, deterministic eviction that cannot evict an active key | tests (b)(c)(d)(e) |
| fill past cap → (i) genuine IP 429+`Retry-After`, (ii) own 429 not reset | tests (b)(c) |
| lookups not O(n) at store-cap scale | test (f) |
| do not change the shared primitive | Task 2 step 4 + verification step 3 (source hash) |
| trust-flag plumbing, 3 legs | tests (i)(j)(k) |
| fly.toml `[env]` config test | Task 5 |
| `RATE_LIMIT_DISABLED` delenv in limiter tests | fixture + test (q) |
| DCR scope vectors `None \| "mcp" \| "mcp offline_access"` | test (p) |
| `oauth_clients` pruning owner + absolute date | Task 4 |

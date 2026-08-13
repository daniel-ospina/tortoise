<!-- research-path: product/competition/user-journeys.md (issue #1081 in-issue web research, verified 2026-08-13; scoping artifact: docs/scoping/scoping-1081-agent-signup-abuse.md) -->

# Agent Signup Abuse Protection (per-IP limiter + R8 signup_velocity) — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make anonymous free-tier key provisioning (`POST /v1/agent/signup`) robust against farming — a separate configurable 2/24h per-IP limiter on the agent path only, an ops-visible signup-velocity abuse rule (R8), free-tier caps bound to anon teams, rewritten dead-semantics tests, and a friendly CLI 429 UX — without touching the shared register bucket or adding CAPTCHA.

**Team:** organisation-design-team
**Role:** (omitted — no AGENT_SESSION_ROLE)

**Architecture:** Two independent, precedented mechanisms, deliberately NOT coupled:
1. **Per-IP signup limiter** — a parametrized in-memory IP-bucket primitive (`_check_ip_bucket_rate_limit`) that replaces the two existing duplicated limiter bodies (register + sensitive-op, hosted_api.py:1411/1448 — already near-identical copies) and adds a third call site for agent_signup with its OWN bucket store + env knobs. The shared `_register_buckets` (3/hr) that /v1/register + /v1/signup/email depend on is untouched.
2. **R8 signup_velocity** — an in-memory `SignupVelocityTracker` in abuse.py mirroring the `ReadVelocityTracker` (R3) precedent: fed on the SUCCESSFUL mint path per IP, notify-only via `notify_abuse` (BILLING_NOTIFY_TO fallback — the documented anon ops path, notify.py:153), never suspends. The durable sweeper over audit_events is a documented follow-on; the `idx_audit_ip_time` index ships now (tiny, idempotent, data already accruing).

### Pattern Research

> **Findings date:** 2026-08-13

> **Gate skipped: plan touches zero third-party dependencies.** All changes are in-repo Python (asyncio/time/defaultdict/os/threading stdlib), FastAPI/starlette patterns already used throughout hosted_api.py, urllib (CLI stdlib), and one idempotent Postgres `CREATE INDEX IF NOT EXISTS`. No new library versions, no new API surfaces, no SDKs. Prior research (Stripe 7.4% multi-account abuse, AIPower post-mortem, Harness/Nexu, Mem0 shadow-account scoping, NAT/CGNAT false-positive mitigations) is carried in the issue Context (verified 2026-08-13) and consumed as the design posture: friction + ceiling, not hard gates; soft-threshold awareness for shared NAT.

**Design postures adopted from prior research (issue Context, verified):**
- 2/24h means "3rd signup within rolling 24h → 429 + Retry-After" (P2 #8 — hard default, documented 429 appeal path via support email, env-tunable).
- Anonymous = scoped + limited (Mem0 shadow-account pattern); reduced anon ceiling is **deferred to #1082** (one-way lockout without a claim path) — this issue only asserts existing free-tier caps bind anon teams.
- No CAPTCHA/device-fingerprint on the agent path (headless CLI constraint; #741 makes client device IDs untrustworthy) — rejected-alternative documented below.

### Integration Surface Map

| Surface | Layer | Test |
|---|---|---|
| Signup limiter (in-memory, env-tunable 2/24h) | integration (TestClient, delenv RATE_LIMIT_DISABLED) | `tests/test_agent_signup.py` (new: `test_signup_ip_limit_2_per_24h`, `test_signup_ip_limit_configurable_via_env`, rewritten `test_rate_limit_not_bypassable_via_client_identity`) |
| Shared register bucket contract (untouched) | integration (locked test) | `tests/test_email_signup.py::test_shared_ip_bucket_3_per_hour` (must stay green) |
| Sensitive-op buckets refactored onto helper | integration (existing regression) | `tests/test_export_delete.py` (429 tests at 686/702 — must stay green) |
| Dead per-identity count removal | integration (rewritten) | `tests/test_writer_inventory.py::test_rate_limit_query_shape` |
| R8 tracker (threshold/window/dedup/kill-switch/memory-bound) | unit | `tests/test_abuse.py::TestSignupVelocity` (new) |
| R8 success-path feed + block-path feed from agent_signup | integration (monkeypatched seam) | `tests/test_agent_signup.py::test_signup_feed` (new, monkeypatch `tortoise.abuse.record_signup`) |
| notify KINDS + BILLING_NOTIFY_TO fallback | unit | `tests/test_notify.py` (kind-allowed assert) + `test_abuse.py` `notified` fixture |
| Free-tier caps bind anon teams | integration | `tests/test_agent_signup.py::test_anon_team_bound_by_free_tier_caps` (registry Team node readback) + writer-inventory `p_*` param assert |
| `idx_audit_ip_time` index | migration (SQL, idempotent) | `supabase db push` dry-run + re-apply idempotency check |
| CLI 429 UX | unit (mocked urlopen) | `tests/test_cli_signup.py` (new) |

**Bug pattern flags:** (1) cross-test IP sharing — module-scoped TestClient uses one client host (`testclient`), so per-IP buckets persist across tests in a module → mandatory bucket reset fixture; (2) import-mask — RATE_LIMIT_DISABLED=1 is set at import in test_writer_inventory.py:31 and masks limiter behavior in full-suite runs → rewritten tests MUST use the `monkeypatch.delenv` pattern (precedent test_email_signup.py:261); (3) helper behavior must be byte-identical for register/sensitive-op (Retry-After 3600, bare-string detail, 10k memory bound) — the #863 enrichment at signup/email (hosted_api.py:2077) catches the wrapper's HTTPException and is untouched.

### Journey Test Map

### Journey: "Vibecoder mints a key in one command"
1. **Step:** `tortoise signup` → **Acceptance:** key returned, config saved, works immediately → **Test:** `test_minted_key_authenticates_team_info`, `test_signup_returns_key` (unchanged, stay green)
2. **Step:** CLI hits 429 → **Acceptance:** friendly Retry-After + support pointer, not raw JSON body → **Test:** `tests/test_cli_signup.py::test_signup_429_friendly`

### Journey: "Farmer hammers signup from one IP"
1. **Step:** 1st–2nd mints → **Acceptance:** 200 (legit flow unaffected) → **Test:** `test_signup_ip_limit_2_per_24h`
2. **Step:** 3rd mint in rolling 24h → **Acceptance:** 429 + computed `Retry-After` (≤ 86400) + `error_code: over_signup_ip_rate_limit` + support email → **Test:** `test_signup_ip_limit_2_per_24h`
3. **Step:** client identity rotation → **Acceptance:** still 429 (IP is the key, not the identity) → **Test:** rewritten `test_rate_limit_not_bypassable_via_client_identity`
4. **Step:** ops inbox → **Acceptance:** `abuse_signup_velocity` notification (BILLING_NOTIFY_TO fallback since anon team has no email) → **Test:** `test_abuse.py::TestSignupVelocity`

### Failure Modes
- **Restart resets buckets** → 2/24h and R8 windows reset on deploy → **Expected:** documented degradation, identical to register limiter precedent (#498) → **Test:** n/a (documented); durable sweeper is the follow-on fix
- **Multi-instance (Fly) deployment** → per-process in-memory limits, each instance allows 2/24h → **Expected:** documented degradation; deferred durable sweeper (audit_events + idx_audit_ip_time) is the authoritative multi-instance signal → **Test:** n/a (documented in plan + artifact)
- **Shared NAT office (2 devs mint in 24h)** → 429 on 3rd, R8 fires on 2nd → **Expected:** hard-limit false positive posture documented in 429 detail (support email); env-tunable; R8 is notify-only (one ops email, dedup'd once/window) → **Test:** `test_signup_ip_limit_2_per_24h` asserts the contract
- **R8 notify flood** → dedup once per window per (scope, ip) + `TORTOISE_ABUSE_DISABLED=1` kill switch (shared `abuse_disabled()`) → **Test:** `TestSignupVelocity::test_notify_once_per_window`
- **Audit JSONL mode (no DSN)** → irrelevant: R8 does not read audit_events (in-memory) → **Expected:** no DSN dependency in shipped scope → **Test:** `test_abuse.py` runs DSN-free

**Tech Stack:** Python 3.11+ (FastAPI/starlette, stdlib), Postgres (Supabase migrations, one index), urllib (CLI).

---

## Task 0: Verify/fix client-IP resolution behind the Fly proxy (PRE-DEPLOY GATE — P1-FIX-11)

**Intent:** `request.client.host` may be the Fly proxy IP — Dockerfile.hosted:68 runs uvicorn with NO `--forwarded-allow-ips`, and uvicorn only trusts X-Forwarded-For from 127.0.0.1. If so, ALL per-IP limiters (register 3/hr, sensitive-op, new signup 2/24h) collapse to a GLOBAL cap. This gate runs BEFORE deploying any limiter change (it also un-breaks the EXISTING register/sensitive-op limiters, which are live-global in prod today if the premise holds).

**Acceptance:** `request.state.client_ip` reflects the real client IP; a forged `X-Forwarded-For` header does NOT change it (non-spoofable).

**Files:** `tortoise/hosted_api.py` (middleware), `Dockerfile.hosted` (only if proxy-IP confirmed AND spoof probe passes)

**Step 1 — Confirm premise in staging:** log `request.client.host` vs `X-Forwarded-For` vs `Fly-Client-IP` for 2 distinct egress IPs. If `client.host` differs per egress IP → premise false, limiters already correct, record and skip to Step 3.

**Step 2 — Spoof probe (HARD BLOCKER, two directions):** (a) send `X-Forwarded-For: 203.0.113.99` (forged) → assert `request.client.host != "203.0.113.99"`. If it equals the forged value, DO NOT ship `--forwarded-allow-ips="*"` (uvicorn parses the FIRST XFF entry — client-controlled → per-IP cap bypassable by any patched `tortoise signup`). (b) ALSO send a forged `Fly-Client-IP: 203.0.113.99` header → assert the middleware resolves `request.state.client_ip != "203.0.113.99"` (a non-proxy ingress or misconfigured proxy would pass client-supplied Fly-Client-IP through — the fix must strip/overwrite it). Record both probe results in the Handoff; either failing → the middleware must only trust Fly-Client-IP from the Fly proxy (e.g. verify it arrives from the proxy's address or strip client-supplied values).

**Step 3 — Fly-Client-IP middleware (the fix):** add a small middleware (precedent: `AnalyticsMiddleware`/`RateLimitMiddleware` in hosted_api.py) that reads the `Fly-Client-IP` header (non-spoofable per Fly docs — set by Fly Proxy from the connection peer; XFF is documented "treat with caution") into `request.state.client_ip`, falling back to `request.client.host` when absent. All limiters (register wrapper, sensitive-op wrapper, signup wrapper) resolve the bucket key via `getattr(request.state, "client_ip", None) or request.client.host`.

**Step 4 — Testable:** middleware unit test monkeypatching the header (file: `tests/test_client_ip_middleware.py`, new); limiter tests use the default TestClient host as before.

**Step 5 — Record** probe result + chosen source (Fly-Client-IP vs native host) + **instance count / scaling policy** (single-instance = real 2/24h enforcement; auto-scaled = effective 2N/24h — changes the deferred-sweeper urgency) in the Handoff. Hard pre-deploy gate for Tasks 1/3.

---

## Task 1: Parametrized IP-bucket limiter + standalone signup limiter

**Intent:** Kill the duplicated-limiter drift (register + sensitive-op are already copies) AND give agent_signup its own per-IP bucket + env knobs without touching the shared `_register_buckets` store — the P1 blast-radius fix from problem-verify.

**Acceptance:** `_check_ip_bucket_rate_limit` exists with the register + sensitive-op wrappers re-implemented on it (behavior byte-identical: Retry-After 3600, same detail strings, 10k memory bound); `agent_signup` calls `_check_signup_ip_rate_limit` (own store, env knobs); `test_shared_ip_bucket_3_per_hour` green; new signup 429 tests green; rewritten dead test green.

**Files:**
- Modify: `tortoise/hosted_api.py:1404-1475` (limiter block), `:4705` (agent_signup call), `:26` import block
- Test: `tests/test_agent_signup.py`, `tests/test_email_signup.py` (locked), `tests/test_export_delete.py` (regression), `tests/conftest.py:255`

**Step 1: Write the failing tests (new signup limiter behavior)**

REPLACE the existing `test_rate_limit_not_bypassable_via_client_identity` (4×200 dead semantics) and ADD to `tests/test_agent_signup.py`:

```python
class TestSignupIpRateLimit:
    def test_signup_ip_limit_2_per_24h(self, client, monkeypatch):
        # delenv pattern: test_writer_inventory.py:31 sets RATE_LIMIT_DISABLED=1
        # at import; the signup limiter must actually be ON for this test.
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        for _ in range(2):
            r = client.post("/v1/agent/signup", json={})
            assert r.status_code == 200, r.text
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 429, r.text
        # P2-FIX-5: computed Retry-After (sliding window) — integer <= 86400,
        # NOT flat "86400" (remaining = oldest + window - now, so once >=1s
        # elapses it is 86399).
        assert int(r.headers.get("retry-after")) <= 86400
        assert r.json()["detail"]["error_code"] == "over_signup_ip_rate_limit"

    def test_signup_ip_limit_configurable_via_env(self, client, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setenv("TORTOISE_SIGNUP_IP_LIMIT", "1")
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 429, r.text

    def test_signup_ip_limit_not_bypassable_via_client_identity(self, client, monkeypatch):
        # REWRITTEN from dead-per-identity semantics (#741): the IP is the key.
        # Rotating client identities must NOT dodge the per-IP limit.
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        for _ in range(2):
            r = client.post("/v1/agent/signup",
                            json={"identity": f"anon-{uuid.uuid4().hex[:12]}"})
            assert r.status_code == 200, r.text
        r = client.post("/v1/agent/signup", json={"identity": "anon-fresh"})
        assert r.status_code == 429, r.text
```

Also extend the autouse reset fixture in `tests/conftest.py:255` (rename `_reset_register_rate_limit` → `_reset_ip_rate_limits`, keep both clears):

```python
@pytest.fixture(autouse=True)
def _reset_ip_rate_limits():
    """#498 register + #1081 signup IP limiters are in-memory per process and
    share one TestClient host across a module — reset both per test."""
    # P3-FIX-6: getattr-guard so the red phase (before _SIGNUP_BUCKETS exists)
    # does not ImportError the whole suite; also reset the R8 tracker
    # (order-dependent dedup flake guard — module-scoped testclient host).
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import _register_buckets
    _register_buckets.clear()
    signup_buckets = getattr(ha_mod, "_SIGNUP_BUCKETS", None)
    if signup_buckets is not None:
        signup_buckets.clear()
    try:
        from tortoise.abuse import SIGNUP_TRACKER
        SIGNUP_TRACKER.reset()
    except (ImportError, AttributeError):
        pass
    yield
    _register_buckets.clear()
    if signup_buckets is not None:
        signup_buckets.clear()
    try:
        from tortoise.abuse import SIGNUP_TRACKER
        SIGNUP_TRACKER.reset()
    except (ImportError, AttributeError):
        pass
```

**Step 2: Run to verify they fail**

Run: `pytest tests/test_agent_signup.py::TestSignupIpRateLimit -v`
Expected: FAIL — 3rd request returns 200 (limiter still shared/absent).

**Step 3: Implement the parametrized helper + wrappers**

In `tortoise/hosted_api.py`, replace the duplicated limiter bodies (1404-1475) with:

```python
# ── IP-based rate limiters (#498, #302, #1081) ────────────────────
# One parametrized primitive; three bucket stores. #1081: the register and
# sensitive-op limiters were near-identical copies — a third copy for the
# agent-signup path would be drift. Register (+signup/email) and sensitive-op
# semantics are byte-identical to the pre-refactor behavior; the signup
# limiter is a NEW, separate per-IP store with env-tunable knobs.

_register_buckets: dict[str, list[float]] = defaultdict(list)
_register_lock = asyncio.Lock()
_REGISTER_MAX_PER_HOUR = 3

_SENSITIVE_OP_LIMITS = {"export": 20, "team_delete": 5}  # per hour per IP
_SENSITIVE_BUCKETS: dict[tuple[str, str], list[float]] = defaultdict(list)
_SENSITIVE_LOCK = asyncio.Lock()

# Agent-signup limiter: OWN store, NOT shared with /v1/register or
# /v1/signup/email (locked by test_shared_ip_bucket_3_per_hour). Default
# 2 signups / 24h per IP — "3rd signup in rolling 24h → 429" (issue decision).
_SIGNUP_BUCKETS: dict[str, list[float]] = defaultdict(list)
_SIGNUP_LOCK = asyncio.Lock()
# P2-1: retained R8 feed tasks (create_task must hold a reference — asyncio GC)
_SIGNUP_FEED_TASKS: dict[str, asyncio.Task] = {}


async def _check_ip_bucket_rate_limit(
    request: Request, *,
    buckets: dict, lock: asyncio.Lock, limit: int, window_s: int,
    detail: str | dict, retry_after_s: int | None = None,
    key: Hashable | None = None, max_entries: int = 10_000,  # Hashable: typing import (or drop annotation)
) -> None:
    """Per-IP sliding-window rate limit over a caller-owned bucket store.

    Shared by /v1/register (3/hr), sensitive ops (export/team_delete), and
    /v1/agent/signup (2/24h). RATE_LIMIT_DISABLED=1 opts out (test env).
    Raises HTTPException(429) with Retry-After when the window is exhausted.
    Memory bound: when the store exceeds max_entries, drop buckets whose
    entries are all older than window_s (dead weight — #750.2 precedent).

    P1-FIX-1: bucket key is the caller-supplied `key` (required at wrappers)
    — the sensitive-op store is keyed (ip, op) composite; a bare-ip default
    would silently merge export/delete budgets (locked by
    test_export_rate_limited_independently). P2-FIX-5: retry_after_s=None
    computes time-until-oldest-entry-expires (sliding-window precision).
    """
    if os.environ.get("RATE_LIMIT_DISABLED") == "1":
        return
    if not request.client or not request.client.host:
        return
    ip = key if key is not None else request.client.host
    # P2-2 (coherence): normalize IPv4-mapped IPv6 (::ffff:1.2.3.4 == 1.2.3.4)
    # so a dual-stack client cannot present two keys for the same address.
    if isinstance(ip, str) and ip.startswith("::ffff:") and "." in ip:
        ip = ip[7:]
    now = time.time()
    async with lock:
        bucket = buckets[ip]
        bucket[:] = [t for t in bucket if now - t < window_s]
        if len(bucket) >= limit:
            remaining = (int(bucket[0] + window_s - now)
                         if retry_after_s is None else retry_after_s)
            raise HTTPException(
                status_code=429,
                detail=detail,
                headers={"Retry-After": str(remaining)},
            )
        bucket.append(now)
        if len(buckets) > max_entries:
            stale = [ip for ip, b in buckets.items()
                     if not any(now - t < window_s for t in b)]
            for ip in stale:
                del buckets[ip]


async def _check_register_rate_limit(request: Request) -> None:
    """3 registrations per hour per IP — shared by /v1/register +
    /v1/signup/email (unchanged contract, #498/#863)."""
    await _check_ip_bucket_rate_limit(
        request, buckets=_register_buckets, lock=_register_lock,
        limit=_REGISTER_MAX_PER_HOUR, window_s=3600,
        key=(getattr(request.state, "client_ip", None)
             or (request.client.host if request.client else None)),
        detail="Too many registration attempts. Please try again later.",
        retry_after_s=3600)


async def _check_sensitive_op_rate_limit(request: Request, op: str) -> None:
    """Per-IP hourly budget for sensitive team ops (export / team_delete)."""
    max_per_hour = _SENSITIVE_OP_LIMITS.get(op)
    if max_per_hour is None:
        return
    # P1-FIX-1: composite (ip, op) key — export and delete keep independent
    # budgets (locked by test_export_rate_limited_independently).
    # P3-3 (phase-7): normalize IPv4-mapped IPv6 HERE (tuple bypasses the
    # helper's isinstance guard).
    _ip = (getattr(request.state, "client_ip", None)
           or (request.client.host if request.client else None))
    if isinstance(_ip, str) and _ip.startswith("::ffff:") and "." in _ip:
        _ip = _ip[7:]
    await _check_ip_bucket_rate_limit(
        request, buckets=_SENSITIVE_BUCKETS, lock=_SENSITIVE_LOCK,
        limit=max_per_hour, window_s=3600,
        key=(_ip, op),
        detail=f"Rate limit exceeded for {op}. Please try again later.",
        retry_after_s=3600)


async def _check_signup_ip_rate_limit(request: Request) -> None:
    """2 anonymous signups / 24h per IP — agent path ONLY.

    Separate bucket store from the register limiter (the shared store is
    locked by test_shared_ip_bucket_3_per_hour). Env-tunable; read at call
    time so tests monkeypatch without reload:
    TORTOISE_SIGNUP_IP_LIMIT (default 2), TORTOISE_SIGNUP_IP_WINDOW_S
    (default 86400). The 429 detail carries the support pointer (P2 #8) —
    hard-limit posture with a documented appeal path.
    """
    window_s = _int_env("TORTOISE_SIGNUP_IP_WINDOW_S", 86400)
    await _check_ip_bucket_rate_limit(
        request, buckets=_SIGNUP_BUCKETS, lock=_SIGNUP_LOCK,
        limit=_int_env("TORTOISE_SIGNUP_IP_LIMIT", 2),
        window_s=window_s,
        key=(getattr(request.state, "client_ip", None)
             or (request.client.host if request.client else None)),
        detail={
            "error_code": "over_signup_ip_rate_limit",
            "message": ("Too many anonymous signups from this IP (max 2 per 24h). "
                        "Try again later or contact support@premiselabs.co."),
            # ISSUE-3 (phase-7): NO retry_after_s body field — Retry-After
            # header is the RFC 7231 contract (#863 precedent); body/header
            # duplication would drift (computed remaining vs flat window).
        },
        retry_after_s=None)  # computed sliding-window remaining (P2-FIX-5)
```

Add `from tortoise.abuse import _int_env` to the module import block (hosted_api.py:26 area). Abuse has no module-level hosted_api import (stdlib-only at module level) — no cycle. If the reviewer prefers zero cross-module coupling, inline a local 5-line `_int_env` copy; but reuse is the single-source-of-truth fix (abuse.py:57).

**Step 4: Re-point agent_signup**

Replace `await _check_register_rate_limit(request)` (hosted_api.py:4705) with `await _check_signup_ip_rate_limit(request)`, and update the stale comment block at 4699-4704 (the "shared per-IP register bucket is the compensating control" comment is now wrong — it's the signup's OWN bucket).

**Step 5: Run the tests**

Run:
- `pytest tests/test_agent_signup.py::TestSignupIpRateLimit tests/test_email_signup.py::TestEmailSignup::test_shared_ip_bucket_3_per_hour -v` → PASS
- `pytest tests/test_export_delete.py -v` → PASS (sensitive-op refactor regression)

**Step 6: Commit**

```bash
git add tortoise/hosted_api.py tests/test_agent_signup.py tests/conftest.py
git commit -m "feat(abuse): parametrized IP-bucket limiter + standalone 2/24h signup limiter (#1081)"
```

---

## Task 2: Remove the dead per-identity count; rewrite writer-inventory dead test

**Intent:** The per-identity signup count is dead by construction (#741 — server-side identity is fresh per request, count always 0), costs a DB round-trip per signup, and carries a fail-closed 500 branch. Its only test asserts the dead semantics. Removing it also removes the last reason the old tests existed.

**Acceptance:** agent_signup has no membership_count_since/registry count block; `test_rate_limit_query_shape` rewritten to assert the NEW per-IP 429 in Supabase mode; writer-inventory suite green.

**Files:**
- Modify: `tortoise/hosted_api.py:4715-4747` (dead query block + `membership_count_since` in the function-level import at 4720)
- Test: `tests/test_writer_inventory.py:347`

**Step 1: Write the failing test (rewrite)**

In `tests/test_writer_inventory.py`, replace `test_rate_limit_query_shape` (347) — keep the Supabase-mode context, assert the new per-IP limiter (mode-independent, in-memory):

```python
def test_rate_limit_query_shape(self, client, monkeypatch):
    """#1081: the old per-identity count was dead (#741 — server-side
    identity is fresh per request) and has been REMOVED. The per-IP
    signup limiter (2/24h) is the compensating control; the 3rd mint
    from one IP 429s in Supabase mode too (mode-independent store)."""
    tc, fake, _ = client
    monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
    for _ in range(2):
        r = tc.post("/v1/agent/signup", json={"identity": "anon-client-chosen"})
        assert r.status_code == 200, r.text
    r = tc.post("/v1/agent/signup", json={"identity": "anon-client-chosen"})
    assert r.status_code == 429, r.text
```

**Step 2: Run to verify it fails**

Run: `pytest tests/test_writer_inventory.py::TestAgentSignup::test_rate_limit_query_shape -v`
Expected: FAIL — 3rd request returns 200 (shared register bucket is 3/hr, so 3 mints pass).

**Step 3: Remove the dead query block**

Delete hosted_api.py:4715-4747 (the `cutoff`/`membership_count_since`/registry-count/`if recent >= 3` block) and drop `membership_count_since` from the function-level import at 4720. Keep the `#741(a)` comment above (identity is always server-side — still true). The OTHER `membership_count_since` usage (team_create per-user owner limit, hosted_api.py:3614/3625) is a REAL limit — untouched.

**Step 4: Run to verify it passes**

Run: `pytest tests/test_writer_inventory.py -v` and `pytest tests/test_agent_signup.py -v`
Expected: PASS (both).

**Step 5: Commit**

```bash
git add tortoise/hosted_api.py tests/test_writer_inventory.py
git commit -m "fix(abuse): remove dead per-identity signup count; rewrite dead-semantics test (#1081)"
```

---

## Task 3: R8 signup_velocity — in-memory SignupVelocityTracker + notify kind

**Intent:** Ops-visible farming signal for anon teams (NULL user_id → R3/R4 owner-notify resolves nothing). The `ReadVelocityTracker` (R3) precedent: in-memory, notify-only, never suspends — durability of the signal source is explicitly acceptable because R8 has no enforcement role (consensus fact).

**Acceptance:** `SignupVelocityTracker` with success-path + block-path feeds, notify-once-per-window dedup, memory bound, kill switch; fed from agent_signup on BOTH success branches; `abuse_signup_velocity` in KINDS + ALERT_TYPES; unit + integration tests green.

**Files:**
- Modify: `tortoise/abuse.py` (EVENT_SIGNUP_VELOCITY, ALERT_TYPES, `_alert_dict`, tracker class, singleton seam)
- Modify: `tortoise/notify.py:30-33` (KINDS)
- Modify: `tortoise/hosted_api.py:4793, 4821` (feed call sites)
- Test: `tests/test_abuse.py` (TestSignupVelocity), `tests/test_agent_signup.py` (feed spy), `tests/test_notify.py` (kind)

**Step 1: Write the failing unit tests**

Add to `tests/test_abuse.py` (following `TestReadVelocity` at 335):

```python
class TestSignupVelocity:
    def test_success_feed_breach_notifies_once(self, monkeypatch, notified):
        # P1-FIX-2: breach on >= — the success feed fires on the 2nd mint
        # ("IP consumed its entire allowance" = the designed review signal).
        tr = SignupVelocityTracker(threshold=2, window_s=3600)
        assert tr.record_signup("1.2.3.4", team_id="t1") is None
        breach = tr.record_signup("1.2.3.4", team_id="t2")  # 2nd mint = breach
        assert breach == ("ip", "1.2.3.4")
        assert [c[0] for c in notified] == ["abuse_signup_velocity"]
        assert len(notified) == 1
        # dedup: further mints in the same window do NOT re-notify
        tr.record_signup("1.2.3.4", team_id="t3")
        assert len(notified) == 1

    def test_window_expiry_rearms(self, monkeypatch, notified):
        tr = SignupVelocityTracker(threshold=2, window_s=60)
        for i in range(2):
            tr.record_signup("9.9.9.9", team_id=f"t{i}", now=1000.0 + i)
        assert len(notified) == 1
        # window expires → a fresh burst is a NEW episode (re-notify)
        for i in range(2):
            tr.record_signup("9.9.9.9", team_id=f"t{i}", now=2000.0 + i)
        assert len(notified) == 2

    def test_block_path_same_episode(self, monkeypatch, notified):
        # P1-FIX-2: success breach (2nd mint) + 429 block dedup to ONE email
        # per (ip, window) — same dedup key, never two.
        tr = SignupVelocityTracker(threshold=2, window_s=3600)
        tr.record_signup("1.2.3.4", team_id="t1")
        tr.record_signup("1.2.3.4", team_id="t2")   # success breach → notify
        tr.record_block("1.2.3.4")                    # 429 path → dedup'd
        tr.record_block("1.2.3.4")
        assert len(notified) == 1

    def test_kill_switch(self, monkeypatch, notified):
        monkeypatch.setenv("TORTOISE_ABUSE_DISABLED", "1")
        tr = SignupVelocityTracker(threshold=1, window_s=3600)
        assert tr.record_signup("1.2.3.4", team_id="t1") is None

    def test_memory_bound(self, monkeypatch, notified):
        # P1-B: the prune drops STALE entries (R3 precedent) — feed 10,100
        # distinct IPs where the first 200 have old `now` timestamps outside
        # the window; assert they are pruned and live entries survive.
        tr = SignupVelocityTracker(threshold=1000, window_s=3600)
        base = 1_000_000.0
        for i in range(10_100):
            now = base - 7200 if i < 200 else base  # first 200 stale (>window)
            tr.record_signup(f"10.{(i // 250) % 250}.{i % 250}", team_id=f"t{i}", now=now)
        # 10,100 > 10,000 → prune ran; 200 stale dropped, 9,900 live remain
        assert len(tr._by_ip) == 9_900
```

**Step 2: Run to verify they fail**

Run: `pytest tests/test_abuse.py::TestSignupVelocity -v`
Expected: FAIL — SignupVelocityTracker undefined.

**Step 3: Implement the tracker in abuse.py**

```python
EVENT_SIGNUP_VELOCITY = "signup_velocity"
ALERT_TYPES = (EVENT_FLAG, EVENT_SUSPEND, EVENT_AUTH_IP, EVENT_READ_VELOCITY,
               EVENT_SIGNUP_VELOCITY)


# ── R8: signup-velocity tracker (in-memory, notify-only) ───────────────────

class SignupVelocityTracker:
    """>N anonymous signups per IP per window → notify ops once per window.

    Anon teams have NULL user_id, so R3/R4 owner-notify resolves nothing —
    R8 is the OPS-visible farming signal (BILLING_NOTIFY_TO fallback, the
    documented anon path, notify.py:153). In-memory by design (mirrors
    ReadVelocityTracker/R3): R8 NEVER suspends, so deploy-reset damage is
    bounded to a notify. The durable multi-instance sweeper over audit_events
    is a documented follow-on (idx_audit_ip_time ships in #1081; sweeper
    contract in docs/scoping/scoping-1081-agent-signup-abuse.md).

    Two feeds:
    - record_signup  — SUCCESSFUL mint path (consensus: blocked farmers must
      not inflate the success count with attempts). Breach on >= threshold:
      threshold = allowance (2/24h) so the feed fires exactly when an IP
      consumes its entire anonymous allowance (the designed review signal).
    - record_block   — the signup limiter's 429 (the unmistakable farming
      evidence). Same dedup key as the success feed (bare ip) — one ops
      email per (ip, window), never two (P1-FIX-2).
    """

    def __init__(self, threshold: int | None = None, window_s: int | None = None):
        self.threshold = threshold if threshold is not None else _int_env(
            "TORTOISE_ABUSE_SIGNUP_THRESHOLD",
            _int_env("TORTOISE_SIGNUP_IP_LIMIT", 2))  # P3-5: defaults follow allowance
        self.window_s = window_s if window_s is not None else _int_env(
            "TORTOISE_ABUSE_SIGNUP_WINDOW_S", 86400)
        self._by_ip: dict[str, list[float]] = defaultdict(list)
        self._notified: dict[str, float] = {}  # bare ip -> last notify ts
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Test seam: clear per-IP counts and dedup state (P1-FIX-10 —
        module-scoped TestClient shares one host across tests)."""
        with self._lock:
            self._by_ip.clear()
            self._notified.clear()

    def record_signup(self, ip: str | None, team_id: str | None = None,
                      now: float | None = None) -> tuple[str, str] | None:
        """Success-path feed: count minted teams per IP per window.
        Returns ('ip', ip) on breach (len >= threshold), else None. Notify
        dedup once per window per IP (bare-ip key, shared with block path)."""
        if abuse_disabled() or not ip:
            return None
        now = now if now is not None else time.time()
        cutoff = now - self.window_s
        breach: tuple[str, str] | None = None
        with self._lock:
            bucket = self._by_ip[ip]
            bucket[:] = [t for t in bucket if t > cutoff]
            bucket.append(now)
            if len(bucket) >= self.threshold:
                breach = ("ip", ip)
            if len(self._by_ip) > 10_000:
                self._by_ip = {k: v for k, v in self._by_ip.items()
                               if any(t > cutoff for t in v)}
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            if breach is not None:
                last = self._notified.get(ip)
                if last is not None and now - last < self.window_s:
                    return None  # already notified this window
                self._notified[ip] = now
        if breach is not None:
            self._notify("velocity", ip, team_id, {"count": len(bucket)})
        return breach

    def record_block(self, ip: str | None, team_id: str | None = None,
                     now: float | None = None) -> None:
        """Block-path feed: the signup limiter 429'd this IP. Same dedup key
        (bare ip) as the success feed — the 429 after a 2-mint allowance is
        dedup-suppressed (one email per episode, P1-FIX-2)."""
        if abuse_disabled() or not ip:
            return
        now = now if now is not None else time.time()
        with self._lock:
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            last = self._notified.get(ip)
            if last is not None and now - last < self.window_s:
                return
            self._notified[ip] = now
        self._notify("blocked", ip, team_id, {})

    def _notify(self, reason: str, ip: str, team_id: str | None,
                details: dict) -> None:
        # P4-FIX: payload carries count ALWAYS (block path details={} → count
        # 0 is fine; _alert_dict reads details.get('count')).
        details = dict(details)  # do not mutate caller's dict
        details.setdefault("count", 0)
        # Dashboard alert row (best-effort; minted team anchors the FK).
        store = None
        try:
            from tortoise.supabase_control import get_abuse_store
            store = get_abuse_store()
            if team_id:
                store.record_event(
                    team_id, EVENT_SIGNUP_VELOCITY,
                    details={"ip": ip, "reason": reason, **details})
        except Exception:
            logger.debug("signup-velocity event record failed (%s)", ip)
        try:
            from tortoise.notify import notify_abuse
            # anon team → no email → BILLING_NOTIFY_TO ops fallback
            notify_abuse("abuse_signup_velocity",
                         {"team_id": team_id, "email": None},
                         {"ip": ip, "reason": reason,
                          "count": details.get("count", 0),
                          "threshold": self.threshold,
                          "window_s": self.window_s,
                          "appeal_url": appeal_url()})
        except Exception:
            logger.debug("signup-velocity notify failed (%s)", ip)


SIGNUP_TRACKER = SignupVelocityTracker()


def record_signup(ip: str | None, team_id: str | None = None,
                  now: float | None = None) -> tuple[str, str] | None:
    """Module-level seam (monkeypatchable) over the shared tracker."""
    return SIGNUP_TRACKER.record_signup(ip, team_id, now)


def record_signup_block(ip: str | None, team_id: str | None = None,
                        now: float | None = None) -> None:
    """Module-level seam for the 429 path."""
    SIGNUP_TRACKER.record_block(ip, team_id, now)
```

Add the `_alert_dict` message entry (the dict already binds `details = row.get("details") or {}` before `messages`):

```python
EVENT_SIGNUP_VELOCITY: f"Signup velocity breach: {details.get('count', '?')} anon signups from {details.get('ip', '?')}",
```

Also add `"abuse_signup_velocity"` to `KINDS` in `tortoise/notify.py:30-33`.

**P3-4 (phase-7):** add an `ip` line to BOTH renderers — `_abuse_email_text` (notify.py:112-137) and the Telegram parts (notify.py:165-175) — the IP is the single most actionable field in an IP-scoped ops alert. Assert it in `TestNotifyAbuse` (the `notified` fixture details tuple index 2 carries `ip`).

Add R8 to the abuse.py header rule list (P3-FIX-7):
```python
# - R8 signup_velocity: N anon signups/IP/window (breach >= threshold) ->
#   notify ops only (BILLING_NOTIFY_TO; never suspends)
```

**Step 4: Feed the tracker from agent_signup**

At BOTH success points (after `_async_audit`, before return — hosted_api.py:4793 and 4821):

```python
from tortoise import abuse as _abuse  # add to the function-level import block
...
await _async_audit(request, team_id, "agent_signup", resource_type="team", resource_id=team_id)
# P3-D/P3-6: notify_abuse is sync httpx — fire-and-forget so ops email
# latency never delays the cold-start mint (best-effort telemetry; #310)
asyncio.create_task(asyncio.to_thread(_abuse.record_signup, getattr(request.state, "client_ip", None)
                        or (request.client.host if request.client else None), team_id)
```

At the limiter call (4705) — block-path feed:

```python
try:
    await _check_signup_ip_rate_limit(request)
except HTTPException as exc:
    if exc.status_code == 429:
        # P2-2 (phase-7): same fire-and-forget pattern as the success feed —
        # the 429 response must NOT absorb ops email latency (up to ~15s Resend).
        _SIGNUP_FEED_TASKS["block-" + (getattr(request.state, "client_ip", None)
            or (request.client.host if request.client else None))] = asyncio.create_task(
                asyncio.to_thread(_abuse.record_signup_block,
                    getattr(request.state, "client_ip", None)
                    or (request.client.host if request.client else None)))
    raise
```

(Import `abuse` once in the existing function-level import block at 4718-4721.)

**Step 5: Write the integration feed test**

Add to `tests/test_agent_signup.py`:

```python
def test_signup_feeds_velocity_tracker(self, client, monkeypatch):
    """R8 success-path feed: a successful mint records the IP."""
    calls = []
    monkeypatch.setattr("tortoise.abuse.record_signup",
                        lambda ip, team_id=None, now=None: calls.append((ip, team_id)))
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200
    assert len(calls) == 1
    assert calls[0][1] == r.json()["team_id"]


def test_signup_ip_limit_and_velocity_single_notify(self, client, monkeypatch):
    """P1-FIX-2 integration: 3rd signup 429 AND exactly ONE ops notify —
    the success feed fires at the allowance boundary (2nd mint); the 429's
    record_block path is dedup-suppressed (same bare-ip key).
    """
    monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
    notified = []
    monkeypatch.setattr("tortoise.notify.notify_abuse",
                        lambda kind, team, details: notified.append(kind))
    for _ in range(2):
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 429, r.text
    assert notified == ["abuse_signup_velocity"]  # exactly one, not two
```

**Step 6: Run all tests**

Run: `pytest tests/test_abuse.py tests/test_notify.py tests/test_agent_signup.py -v`
Expected: PASS. Also confirm the `notified` fixture call captures kind `abuse_signup_velocity`.

**Step 7: Commit**

```bash
git add tortoise/abuse.py tortoise/notify.py tortoise/hosted_api.py tests/test_abuse.py tests/test_agent_signup.py tests/test_notify.py
git commit -m "feat(abuse): R8 signup_velocity in-memory tracker + ops notify (#1081)"
```

---

## Task 4: Free-tier caps bind anon teams (assertion test)

**Intent:** Indicator 3. agent_signup already passes `p_max_*` from `tier_limits("free")` (hosted_api.py:4754, 4771-4785; registry CREATE at ~4805) — the reduced anon ceiling is deliberately deferred to #1082. This issue only LOCKS the binding with a readback test so a pricing-drift regression can't silently un-cap anon teams.

**Acceptance:** Registry-mode test asserts minted Team node props == `tier_limits("free")`; writer-inventory Supabase-mode test asserts `p_*` params match `tier_limits("free")`; both green.

**Files:**
- Test: `tests/test_agent_signup.py`, `tests/test_writer_inventory.py`

**Step 1: Write the failing tests**

`tests/test_agent_signup.py`:

```python
def test_anon_team_bound_by_free_tier_caps(self, client):
    from tortoise.pricing import tier_limits
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    lim = tier_limits("free")
    sdk = ha_mod._make_sdk(namespace="registry")
    row = sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.max_users, t.max_graphs, "
        "t.max_api_keys, t.ops_allowance, t.graph_size_cap",
        params={"id": r.json()["team_id"]},
    ).result_set[0]
    assert row[0] == lim["max_users_per_team"]
    assert row[1] == lim["max_graphs_per_team"]
    assert row[2] == lim["max_api_keys"]
    assert row[3] == lim["included_write_ops_per_month"]
    assert row[4] == lim["max_graph_nodes"]
```

`tests/test_writer_inventory.py` (inside the existing Supabase signup test or a new one, mirroring `TestRegister::test_register_provisions_with_email`'s `fake.rpc_calls` assertion):

```python
def test_signup_caps_match_free_tier(self, client):
    from tortoise.pricing import tier_limits
    tc, fake, _ = client
    r = tc.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    fn, p = next(c for c in fake.rpc_calls if c[0] == "provision_team")
    lim = tier_limits("free")
    assert p["p_max_users"] == lim["max_users_per_team"]
    assert p["p_max_graphs"] == lim["max_graphs_per_team"]
    assert p["p_ops_allowance"] == lim["included_write_ops_per_month"]
    assert p["p_graph_size_cap"] == lim["max_graph_nodes"]
    assert p["p_tier"] == "free"
```

**Step 2: Run to verify they pass**

Run: `pytest tests/test_agent_signup.py::TestAgentSignup::test_anon_team_bound_by_free_tier_caps tests/test_writer_inventory.py -v`
Expected: PASS (caps are already wired — these are regression locks; if red, fix the wiring, do not weaken the test).

**Step 3: Commit**

```bash
git add tests/test_agent_signup.py tests/test_writer_inventory.py
git commit -m "test(abuse): lock free-tier caps binding for anon signup teams (#1081)"
```

---

## Task 5: Migration — idx_audit_ip_time

**Intent:** Indicator 2's "audit_events indexed on ip_address". The index ships NOW (idempotent, 4 lines, data already accruing from every signup's `_async_audit`); the durable sweeper that USES it is a documented follow-on (decision + contract in §Decision 1 and the scoping artifact).

**Acceptance:** Migration file created, idempotent (applies twice cleanly), dry-run passes; no unit test needed (pure DDL — audit schema tests are DSN-gated; the JSONL-mode tests in test_audit_events.py are unaffected).

**Files:**
- Create: `supabase/migrations/20260813000003_audit_ip_time_index.sql`

**Step 1: Write the migration**

```sql
-- Migration 20260813000003: audit_events ip index (#1081)
--
-- R8 signup_velocity (durable sweeper — documented follow-on, see
-- docs/scoping/scoping-1081-agent-signup-abuse.md): per-IP window queries
-- over audit_events (operation='agent_signup', ip_address, created_at).
-- The index ships ahead of the sweeper: signup audit rows are accruing now
-- and the table is small — adding the index later on a large append-only
-- table is the riskier path. Sweeper itself is deferred (no consumer yet:
-- the shipped R8 signal is the in-memory tracker).

CREATE INDEX IF NOT EXISTS idx_audit_ip_time
    ON public.audit_events (ip_address, created_at DESC);
```

**Step 2: Verify idempotency + dry-run**

```bash
supabase db push --dry-run        # shows the single CREATE INDEX
supabase db push                   # applies to the linked remote
# idempotency: re-run the statement locally against a scratch DB — no error
psql "$DATABASE_URL" -c "CREATE INDEX IF NOT EXISTS idx_audit_ip_time ON public.audit_events (ip_address, created_at DESC);"  # ok
psql "$DATABASE_URL" -c "CREATE INDEX IF NOT EXISTS idx_audit_ip_time ON public.audit_events (ip_address, created_at DESC);"  # ok again
```

**Step 3: Commit**

```bash
git add supabase/migrations/20260813000003_audit_ip_time_index.sql
git commit -m "feat(abuse): idx_audit_ip_time index for durable R8 sweeper (#1081)"
```

---

## Task 6: CLI 429 UX for `tortoise signup`

**Intent:** P3 fix — the 429 path at `__main__.py:645-650` prints a raw JSON body. The agent-facing surface must explain the retry window + the contact-support path (matching the `_cmd_fail` precedent at __main__.py:980 for team keys).

**Acceptance:** On 429, `_cmd_signup` prints a friendly message with the Retry-After window + `support@premiselabs.co` pointer and returns 1; happy path unchanged; new CLI tests green.

**Files:**
- Modify: `tortoise/__main__.py:644-651`
- Test: Create `tests/test_cli_signup.py`

**Step 1: Write the failing test**

```python
"""CLI `tortoise signup` tests (#1081)."""
import json
from unittest import mock
from urllib.error import HTTPError

import tortoise.__main__ as main


def _http_error(code, body, headers=None):
    import io
    from email.message import Message
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return HTTPError("https://api.premiselabs.co/v1/agent/signup", code,
                     "err", msg, io.BytesIO(body.encode()))


class TestSignup429:
    def test_429_prints_retry_and_support(self, capsys):
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(
                            429, json.dumps({"detail": {"error_code": "over_signup_ip_rate_limit"}}),
                            {"Retry-After": "86399"})):  # computed remaining (P2-FIX-5)
            rc = main._cmd_signup(mock.Mock())
        err = capsys.readouterr().err
        assert rc == 1
        assert "86399" in err or "24h" in err or "later" in err
        assert "support@premiselabs.co" in err
        assert "Signup rate limit" in err
```

**Step 2: Run to verify it fails**

Run: `pytest tests/test_cli_signup.py -v`
Expected: FAIL — raw body printed, no support pointer.

**Step 3: Implement**

Replace the `HTTPError` branch in `_cmd_signup` (__main__.py:644-651):

```python
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code == 429:
            retry = (e.headers.get("Retry-After")
                     if e.headers and e.headers.get("Retry-After") else None)
            when = f"{int(retry)}s" if (retry and retry.isdigit()) else "later"
            print(f"Signup rate limit reached — try again in {when}. "
                  "Need more keys? Contact support@premiselabs.co.",
                  file=sys.stderr)
            return 1
        print(f"Signup failed ({e.code}): {body}", file=sys.stderr)
        return 1
```

**Step 4: Run to verify it passes**

Run: `pytest tests/test_cli_signup.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tortoise/__main__.py tests/test_cli_signup.py
git commit -m "fix(cli): friendly 429 UX for tortoise signup — Retry-After + support pointer (#1081)"
```

---

## Task 7: Env knobs documentation + scoping artifact + quickstart

**ISSUE-4 (phase-7):** add a ~4-line "Zero-email signup" subsection to `docs/quickstart-cloud.md` (currently only documents web signup): `tortoise signup` → key saved to `.tortoise`, note "2 free anonymous teams per IP per 24h; on a shared network or need more? support@premiselabs.co". Optionally surface the limit in `tortoise signup`'s `add_parser` help string (__main__.py:3307).

**Intent:** (a) `.env.example` documents the four new knobs next to the existing abuse block (abuse.py's `_int_env` reads them; an undocumented knob is a support trap); (b) persist the converged architecture + rejected alternatives + deferred-sweeper contract to `docs/scoping/` per house convention (scoping-308-abuse-prevention.md precedent) — this is the artifact the reviewer consensus facts and the deferred follow-on point at.

**Acceptance:** `.env.example` lists the four knobs; `docs/scoping/scoping-1081-agent-signup-abuse.md` exists with converged design, rejected alternatives (with "when this would have been better"), and the durable-sweeper follow-on contract.

**Files:**
- Modify: `.env.example:190-200`
- Create: `docs/scoping/scoping-1081-agent-signup-abuse.md`

**Step 1: Document the knobs**

In `.env.example`, after the `TORTOISE_ABUSE_READ_WINDOW_S` line:

```
# #1081 agent-signup abuse protection:
# TORTOISE_SIGNUP_IP_LIMIT=2              # anonymous signups per IP per window (agent path only)
# TORTOISE_SIGNUP_IP_WINDOW_S=86400       # rolling window (s); 3rd signup within → 429
# TORTOISE_ABUSE_SIGNUP_THRESHOLD=2       # R8: successful mints per IP per window → ops notify
# TORTOISE_ABUSE_SIGNUP_WINDOW_S=86400    # R8 window (s); notify dedup once per window
```

**Step 2: Write the scoping artifact**

Create `docs/scoping/scoping-1081-agent-signup-abuse.md` with: confirmed problem, converged architecture (this plan's §Architecture + Decision 1), rejected alternatives (verbatim from the plan's Rejected Alternatives section), and the **deferred durable-sweeper contract**: "periodic job querying `audit_events` (WHERE operation='agent_signup' AND ip_address=$ip AND created_at > $cutoff, using idx_audit_ip_time) per IP, count > TORTOISE_ABUSE_SIGNUP_THRESHOLD within window → flag + notify_abuse('abuse_signup_velocity') via AbuseEngine; ships when multi-instance hosting or the ops dashboard exists."

**Step 3: Commit**

```bash
git add .env.example docs/scoping/scoping-1081-agent-signup-abuse.md
git commit -m "docs(abuse): #1081 env knobs + scoping artifact with deferred sweeper contract (#1081)"
```

---

## Verification Plan

(Embedded from the integration surface map; routed per test-routing: Architecture=complex → integration + unit + migration-apply; Config=standard → env-knob unit coverage; no UI surface → ux-verification skipped; no third-party deps → research gate skipped.)

```bash
# Task-scoped
python -m pytest tests/test_agent_signup.py tests/test_writer_inventory.py -v
python -m pytest tests/test_abuse.py tests/test_notify.py tests/test_email_signup.py tests/test_export_delete.py -v
python -m pytest tests/test_cli_signup.py -v

# Full suite (indicator target: 0 regressions)
python -m pytest tests/ -v -m "not slow"

# Migration
supabase db push --dry-run && supabase db push   # + idempotent re-apply check
```

**Signals to watch:** `test_shared_ip_bucket_3_per_hour` MUST stay green (shared-store contract); `test_export_delete.py` MUST stay green (helper refactor regression); the two rewritten tests must run with RATE_LIMIT_DISABLED deleted (delenv) — if they pass WITHOUT the delenv, they're testing the mask, not the limiter.

## Acceptance Criteria (→ revised indicators 1-4)

| # | Indicator | Shipped by | Verification |
|---|---|---|---|
| 1 | Separate per-IP limiter for agent_signup: default 2/24h, env-configurable; 3rd signup → 429 + computed Retry-After (≤ 86400); register/signup-email limits UNCHANGED | Task 1 | `test_signup_ip_limit_2_per_24h`, `test_signup_ip_limit_configurable_via_env` green; `test_shared_ip_bucket_3_per_hour` green |
| 2 | R8 signup_velocity: flag + ops notify via BILLING_NOTIFY_TO fallback; new alert type; audit_events indexed on ip_address | Task 3 + Task 5 | `TestSignupVelocity` green; `abuse_signup_velocity` ∈ KINDS + ALERT_TYPES; migration 20260813000003 applied; durable sweeper deferred per §Decision 1 |
| 3 | Anon teams bound by existing free-tier caps; reduced ceiling deferred to #1082 | Task 4 | `test_anon_team_bound_by_free_tier_caps` + writer-inventory `p_*` assert green |
| 4 | Rewritten signup-rate-limit tests pass; unrelated signup tests stay green; no CAPTCHA on agent path | Tasks 1+2+6 | rewritten `test_rate_limit_not_bypassable_via_client_identity` + `test_rate_limit_query_shape` green; `test_minted_key_authenticates_team_info` + `test_signup_returns_key` unchanged-green; agent_signup has no Turnstile call |

---

## Decision 1 — idx_audit_ip_time ships NOW; the durable sweeper is a documented follow-on

**Decision: ship the index in this issue (Task 5); defer the audit read API + sweeper.** Justification:

1. **No consumer in the shipped scope.** The converged R8 signal is the in-memory tracker (consensus: notify-only → durability matters less; audit read path is infrastructure for an ops dashboard that doesn't exist). An index has value only when queried; the sweeper that queries it is speculative.
2. **But the index is the CHEAP half of the deferred work and satisfies indicator 2 verbatim.** 4 idempotent lines, negligible write cost on an append-heavy table, and the table is SMALL now — the correct time to add an index is while it's cheap, not after the sweeper ships against a large table (CREATE INDEX on a big append-only log is the risky-lock path).
3. **Data is already accruing.** Every signup writes `_async_audit(..., "agent_signup")` with ip_address (hosted_api.py:4793/4821) — the index serves day-one data once the sweeper lands.
4. **Migration surface is one file, revertible, and follows the active 2026081300000N numbering.** The heavier parts of the audit-read path (read API, RLS policy for a query surface, DSN dependency, JSONL-mode handling) are precisely the parts that need a consumer to justify — they defer.
5. **Deferral is explicit and tracked** in the scoping artifact contract (Task 7) so the follow-on can't be lost.

**When the OTHER choice would have been better:** if multi-instance hosting were live today (the in-memory per-process tracker would be visibly evadable and ops would want the durable signal NOW), or if the ops dashboard were in active build (the read API would have a consumer). Neither is true at 2026-08-13.

**Sweeper contract note (ISSUE-7, phase-7):** the durable sweeper threshold SHOULD default ABOVE the allowance (e.g. `TORTOISE_ABUSE_SIGNUP_THRESHOLD + 1`) — the in-memory signal already fires at the allowance boundary ("used allowance"), so the durable layer should signal "exceeded allowance" to avoid duplicating the ops email.

---

## Rejected Alternatives (documented, with "when this WOULD have been better")

**1. R8 on audit_events DB count (Reviewer 1 A — durable gate).** Query audit_events (operation='agent_signup', ip_address, window) per request. Rejected: needs a read API that doesn't exist (AuditLogger is write-only), DSN dependency (JSONL mode has no queryable store — the rule would silently no-op in selfhost), a per-request DB query on the mint path (latency + a new failure surface), and RLS/reader considerations — all for a NOTIFY-ONLY signal whose durability consensus says matters less. Its descendant IS shipped: the deferred sweeper (Decision 1) + idx_audit_ip_time. *Would have been better when:* multi-instance hosting is live or the ops dashboard is in build — the durable count is authoritative across restarts/instances.

**2. abuse_events piggyback (Reviewer 1 C).** Record signup velocity into abuse_events via middleware refactor. Rejected: `abuse_events.team_id NOT NULL REFERENCES teams(id)` (migration 0015) + NO ip_address column + team-scoped indexes make it structurally wrong for IP-scoped rules; the middleware refactor couples the enforcement layer to the signal layer. *Would have been better when:* the rule were team-scoped (e.g. per-minted-team key velocity) — then the existing store + R2 machinery would apply directly. (R8's event ROW does land in abuse_events for the dashboard via `record_event`, anchored on the minted team_id — best-effort, no schema change.)

**3. Per-device/device-ID fallback limiter (issue P2 #9).** Rejected: #741 — client-supplied device IDs are ignored (trivially spoofable); server-side fingerprinting needs a client SDK incompatible with the one-command CLI. *Would have been better when:* a first-party client (SDK/daemon) exists that can carry a trusted device attestation — then the NAT false-positive class (research: corporate VPN/CGNAT) gets its second factor.

**4. Global tightening of the shared register bucket (e.g. 3/hr → 2/24h everywhere).** Rejected: breaks /v1/register + /v1/signup/email contracts (locked by test_shared_ip_bucket_3_per_hour; #863 error-code contract). *Would have been better when:* email/register signup were also confirmed high-abuse — a single knob would be simpler. They're not (email requires GoTrue + Turnstile; register requires a verified email).

**5. Reduced anon ceiling in THIS issue.** Rejected: hard-dependent on #1082's claim path (same key, memories intact, verified identifier); shipping a reduced ceiling without the unlock = one-way lockout, contradicting Mem0's generous-first-session pattern. *Would have been better when:* the #1082 claim/upgrade path already shipped.

**6. Rate-limit-only (no R8).** Rejected: farms stay invisible — R3/R4 owner-notify resolves nothing for NULL-user_id anon teams; ops would never see the pattern behind the 429s. *Would have been better when:* the limiter were perfect (it isn't — in-memory, per-process, restart-resettable).

**7. Durable sweeper NOW (read API + scheduler + DSN dependency).** Rejected: the ops consumer doesn't exist; a scheduled job has no host in the product today (R2's cleanup is ops-run, per migration 0015 note); DSN dependency breaks selfhost parity. *Would have been better when:* a hosted scheduler or the dashboard existed. Deferred with a written contract (Task 7).

**8. Keep the dead per-identity count "for shape parity".** Rejected: it costs a DB round-trip + a fail-closed 500 branch per signup for a count that is 0 by construction (#741); its only test asserted the dead semantics and is rewritten. *Would have been better when:* a future rule could reuse the per-identity shape — it can't, permanently (#741 server-side-fresh identity).

**9. Reviewer 2 C "notify-on-block only" (limiter-as-detector, minimal).** Not rejected — FOLDED IN: `record_block` is the 429-path feed, but the success-path feed (consensus: "blocked farmer inflates velocity with attempts") remains the count substrate so the R8 signal names the ALLOWANCE boundary, not the attack noise. *Would have been better as the only design when:* ops only ever wanted "someone got blocked" and not "an IP used its full allowance" — the block notify alone would be a smaller diff, but a weaker signal (one 429 could be a NAT false positive; the success count + block pair is the fuller picture).

## Runtime Prerequisites

- **RATE_LIMIT_DISABLED parity:** the new signup limiter reads the same env flag as register/sensitive-op (single helper → parity by construction). Rewritten tests MUST delenv it (test_email_signup.py:261 precedent) — without the delenv they'd test the mask, not the limiter.
- **conftest fixture:** `_reset_register_rate_limit` (conftest.py:255) extends to also clear `_SIGNUP_BUCKETS` (autouse, applies to all modules).
- **Module-scoped client IP sharing:** test_agent_signup.py's module-scoped TestClient shares one client host ("testclient") across tests → per-IP buckets are cross-test within a module → the autouse reset is mandatory, not cosmetic.
- **Env knobs read at call time** (limiter) / at tracker init (R8, mirroring ReadVelocityTracker) — tests monkeypatch via setenv/delenv without reload; R8 env changes need a restart (documented; consistent with R3).
- **Middleware unchanged:** /v1/agent/signup is NOT in RateLimitMiddleware.SKIP (hosted_api.py:410) → it keeps the coarse 100/min/IP bucket on top of the new 2/24h limiter (layered defense — intended, no change).
- **Migration numbering:** next free slot is `20260813000003_` (existing: `20260813000001_teams_deleted_at`, `20260813000002_metering_nodes_written`).

## Handoff

- Plan doc: `docs/plans/2026-08-13-1081-agent-signup-abuse-protection.md`
- Branch: `feat/1081-abuse-protection` (worktree `.worktrees/1081-abuse-protection`)
- **8 tasks** (Task 0 pre-deploy gate + Tasks 1-7) → **subagent-driven execution** (≤ 8 tasks), one fresh subagent per task + code-review gate.
- ⛔ **Task 0 is a HARD pre-deploy gate for Tasks 1/3** — record the spoof-probe result in this Handoff before any deploy.
- Next gate: `plan-review` on this doc (fresh-context reviewers), then `planned` label, then `commit-workflow` at each commit (mandatory: pre-flight tests, review gate).
- ⛔ Skill compliance: `how-to-use-tortoise` before any graph writes; `commit-workflow` before any commit/push/merge; `code-review` for the PR (standard+ complexity).

---

### Implementation Record (2026-08-13 — executed in worktree, all 8 tasks)

**Commits (in order, branch `feat/1081-abuse-protection`):**

| Task | Commit | Message |
|---|---|---|
| 0 | `585f4509` | feat(abuse): Fly-Client-IP middleware — per-IP limiters resolve real client IP (#1081) |
| 1 | `e1dcfd50` | feat(abuse): parametrized IP-bucket limiter + standalone 2/24h signup limiter (#1081) |
| 2 | `6a977d10` | fix(abuse): remove dead per-identity signup count; rewrite dead-semantics test (#1081) |
| 3 | `87a70cd2` | feat(abuse): R8 signup_velocity in-memory tracker + ops notify (#1081) |
| 4 | `21d597cb` | test(abuse): lock free-tier caps binding for anon signup teams (#1081) |
| 5 | `bfb56c90` | feat(abuse): idx_audit_ip_time index for durable R8 sweeper (#1081) |
| 6 | `1efc4ace` | fix(cli): friendly 429 UX for tortoise signup — Retry-After + support pointer (#1081) |
| 7 | `50fd716d` + `82e42be9` | docs(abuse): #1081 env knobs + scoping artifact… / docs(abuse): quickstart-cloud zero-email signup section |

**Env vars added (documented in `.env.example`):** `TORTOISE_SIGNUP_IP_LIMIT` (default 2), `TORTOISE_SIGNUP_IP_WINDOW_S` (default 86400), `TORTOISE_ABUSE_SIGNUP_THRESHOLD` (default 2, follows `TORTOISE_SIGNUP_IP_LIMIT`), `TORTOISE_ABUSE_SIGNUP_WINDOW_S` (default 86400). All read via `abuse._int_env`; limiter knobs read at call time, R8 knobs at tracker init (restart to apply — R3 precedent).

**Task 0 staging probe — ⛔ NOT YET RUN (pre-deploy gate).** No staging environment access from the implementation machine. The middleware fix is SHIPPED (P1-FIX-11 design: read non-spoofable `Fly-Client-IP`, never XFF; `--forwarded-allow-ips="*"` REJECTED). Before deploying Tasks 1/3 to production, run these probes in staging and record the results here:
1. **Premise check:** log `request.client.host` vs `X-Forwarded-For` vs `Fly-Client-IP` for 2 distinct egress IPs. If `client.host` differs per egress IP → premise false (limiters already correct).
2. **Spoof probe (hard blocker, two directions):** (a) POST with forged `X-Forwarded-For: 203.0.113.99` → assert `request.client.host != "203.0.113.99"` AND `request.state.client_ip != "203.0.113.99"` (middleware ignores XFF). (b) POST with forged `Fly-Client-IP: 203.0.113.99` behind the real proxy → assert `request.state.client_ip != "203.0.113.99"` (the proxy overwrites client-supplied values; if a non-proxy ingress passes it through, the middleware must be hardened to trust Fly-Client-IP only from the proxy).
3. **Instance count / scaling policy:** record instance count + autoscaling policy — single instance = real 2/24h enforcement; auto-scaled = effective 2N/24h (changes the deferred-sweeper urgency). `Dockerfile.hosted` unchanged (no `--forwarded-allow-ips` — correct posture).

**Deviations from plan (all minimal, noted):**
1. `abuse.py` memory-bound prune re-wraps `defaultdict(list, …)` — the plan's literal dict-comprehension replace lost defaultdict semantics and KeyError'd on new IPs after the prune (exposed by the plan's own `test_memory_bound`, 10,100 distinct IPs).
2. `agent_signup` block-path feed: `_abuse` import moved ABOVE the limiter call — the plan's placement (later import block) raised `UnboundLocalError` at the 429 site.
3. Success-path feed tasks are ALSO retained in `_SIGNUP_FEED_TASKS` (plan's literal success-feed code used bare `create_task`; P2-1's stated intent — hold a reference — applied to both paths).
4. `_cmd_signup`: added `import os` — PRE-EXISTING `NameError: name 'os' is not defined` at `api_url = os.environ.get(...)` (line 627) made the entire CLI signup path (and the plan's own 429 test) unreachable. Bug present on origin/main too; fix is required by the plan's test.
5. `timedelta` dropped from agent_signup's function-level datetime import (dead after the per-identity block removal).
6. Integration feed tests poll briefly (`_wait_for`) for the fire-and-forget `to_thread` side effect — the response can beat the background thread (race); assertions unchanged.
7. Added `test_notify.py::test_abuse_signup_velocity_kind_allowed_with_ip` (plan's surface map lists a kind-allowed assert; none existed) — locks KINDS membership, IP renderers, and BILLING_NOTIFY_TO fallback.
8. `supabase db push --dry-run` NOT run — no linked project ref on this machine (`LegacyProjectNotLinkedError`); the migration is idempotent `CREATE INDEX IF NOT EXISTS` matching 0002's schema (`ip_address TEXT`, `created_at TIMESTAMPTZ`).

**Test results per task:** Task 0: 4/4 new middleware tests + 7/8 agent-signup (1 pre-existing dead-test failure, replaced in Task 1). Task 1: 38/38 (agent_signup + export_delete incl. locked `test_shared_ip_bucket_3_per_hour`). Task 2: 67/67 (writer_inventory + agent_signup). Task 3: 161/161 (abuse + notify + email_signup + export_delete + writer_inventory); feed tests stable across 5 repeated runs. Task 4: 2/2 caps tests. Task 6: 2/2 CLI tests + happy-path smoke. Full suite: `python -m pytest tests/ -v -m "not slow"` — see run record appended by executor.

---

## Controller Merge Decision (solution-converge, 2026-08-13)

Two independent convergers produced plans; controller merged with rationale (quality over convenience):

| Axis | Decision | Rationale |
|---|---|---|
| Limiter shape | **Parametrize** (`_check_ip_bucket_rate_limit` + 3 thin wrappers, own stores) | Register+sensitive-op (hosted_api.py:1411/1448) are already near-identical copies; a 3rd copy is the drift P2 #8 flagged. Behavior pinned by `test_shared_ip_bucket_3_per_hour` + `test_export_delete.py` — parametrization is verifiably behavior-preserving. |
| R8 substrate | **In-memory SignupVelocityTracker**, threshold = allowance (2/24h), notify-only; plus `record_block` on the 429 path | In-memory tracker with threshold > allowance can never fire (limiter caps at 2). Setting threshold = allowance makes R8 fire exactly at the designed review boundary ("an IP used its entire anonymous allowance" = the user's "or contact support" trigger), dedup'd once/window. Never suspends (R3 precedent). |
| Durable sweeper | **Index ships now; read API + sweeper deferred** (documented follow-on) | No ops consumer exists for a durable query surface; RLS policy + DSN dependency + JSONL handling need a consumer to justify. Index is 4 idempotent lines, correct timing while table is small, data already accruing. |
| Caps binding | Test-only (registry readback + p_* param assert) | agent_signup already passes p_max_* from `tier_limits("free")` (4754/4771); a runtime guard re-verifies the mint path. |
| Dead per-identity count | Remove | DB round-trip + fail-closed 500 per mint for a count that is 0 by construction; its only test asserts dead semantics. |
| ⚠️ NEW from Converger 2 | **Task 0: verify uvicorn client-IP resolution behind Fly proxy** | Dockerfile.hosted runs uvicorn with NO `--forwarded-allow-ips` → `request.client.host` may be the Fly proxy IP, making ALL per-IP limits (including the existing register 3/hr!) a GLOBAL cap. Verify in staging; one-line fix `--forwarded-allow-ips="*"` (or equivalent) if confirmed. This is pre-existing (affects current register limiter), but the new limiter makes it material. |

Rejected alternative (when better): audit-count R8 (Reviewer 1 A / Converger 2) — better when multi-instance hosting or an ops dashboard exists (both the deferred sweeper's trigger conditions).

---

## Solution-verify — Cycle 1 → controller fixes (2 P1 + 6 P2/P3/P4, all incorporated)

### P1-FIX-1: helper keeps per-call-site keying (`key` param)
`_check_ip_bucket_rate_limit(request, *, buckets, lock, limit, window_s, detail, retry_after_s, key=None)` — wrapper passes the bucket key explicitly:
- register: `key=request.client.host`
- signup: `key=request.client.host`
- sensitive-op: `key=(request.client.host, op)` — PRESERVES the composite `(ip, op)` keying; export (20/hr) and delete (5/hr) keep independent budgets (locked by `test_export_delete.py::test_export_rate_limited_independently`). Helper must NOT hardcode `buckets[ip]`.
- Test added: `test_sensitive_op_composite_key_preserved` (burn delete budget, assert export unaffected).

### P1-FIX-2: R8 success feed fires at allowance boundary (`>=` breach, single dedup key)
- Breach semantics: `if len(bucket) >= self.threshold` — success feed fires on the **2nd** successful mint = "an IP consumed its entire anonymous allowance" (the designed review signal; the user's "or contact support" trigger).
- **One dedup key per (ip) per window across BOTH feeds** — `_notified[(ip)]` (drop the `("block", ip)` second key) → one ops email per episode, never two.
- `_blocks` store DROPPED: block feed only touches `_notified` dedup (no count needed for the review signal); `_notified` prune moved into BOTH `record_signup` and `record_block` (memory-bound; R3 precedent prunes inside `record_read`).
- Defaults: `TORTOISE_ABUSE_SIGNUP_THRESHOLD=2` (= allowance, breach on `>=`), `TORTOISE_ABUSE_SIGNUP_WINDOW_S=86400`.
- Tracker docstring states: success feed is the allowance-boundary signal; block feed is the cap-hit signal; both dedup to one email/window/IP.
- Integration test added: `test_signup_ip_limit_and_velocity_single_notify` — 3rd signup 429 AND exactly ONE `abuse_signup_velocity` notify (block path, same episode).

### P2-FIX-3: Task 0 becomes a real task (uvicorn forwarded-allow-ips)
Task 0 (pre-deploy gate, BEFORE Task 1 deploy): verify `request.client.host` is the real client IP behind the Fly proxy (Dockerfile.hosted:68 has no `--forwarded-allow-ips`; proxy IP would collapse ALL per-IP limiters into a global cap).
- Acceptance: staging test posts from 2 distinct egress IPs → `request.client.host` differs; if proxy IP observed → fix `CMD ... --forwarded-allow-ips="*"` (with XFF-spoof caveat: Fly must overwrite client-supplied XFF) → re-verify.
- Also un-breaks the pre-existing register 3/hr + sensitive-op limiters (currently global in prod if confirmed).
- Files: Dockerfile.hosted, staging verify script; test: none (manual/staging gate), noted in Handoff.

### P2-FIX-4: TestSignupVelocity uses the list-fixture convention
`notified` fixture returns a list — assert `[c[0] for c in notified] == ["abuse_signup_velocity"]`, `len(notified) == 1`, `notified[0][1]["ip"]`. No Mock API on a list.

### P2-FIX-5: Retry-After precision (signup wrapper only)
Problem-verify fix #5 restored: helper computes `remaining = int(oldest + window_s - now)` for the Retry-After header when the bucket is exhausted; register/sensitive wrappers keep flat 3600 (byte-identical, locked tests); signup wrapper uses computed `remaining` (test asserts `retry_after` ≤ 86400 and reflects oldest entry). CLI prints the computed value.

### P3-FIX-6: conftest fixture TDD sequencing
Autouse reset fixture uses `getattr(ha_mod, "_SIGNUP_BUCKETS", None)` and clears only if present — no ImportError during the red phase before the store exists. `_SIGNUP_BUCKETS` + `_SIGNUP_LOCK` created in Task 1 Step 1 alongside the helper.

### P3-FIX-7: abuse.py header rule list gains R8
`- R8 signup_velocity: N anon signups/IP/window (breach >= threshold) -> notify ops only (BILLING_NOTIFY_TO; never suspends)`.

### P4-FIX-8: CLI Retry-After int() guard
`if retry.isdigit(): remaining = int(retry)` — tolerate RFC 7231 HTTP-date Retry-After.

### P4-FIX-9: env-knob naming supersession
`TORTOISE_SIGNUP_IP_WINDOW_S=86400` supersedes the scoping fix's `_WINDOW_H` proposal (consistent with `_int_env` seconds convention) — noted in .env.example comment + issue body addendum.

### ASN note (P3, research dimension)
IP-count alone is the shipped posture; ASN grouping is deferred to the sweeper contract in Task 7 (no ASN data source exists — R4 documents IPINFO_TOKEN as follow-on). Recorded as explicit rejection rationale.

---

## Solution-verify — Cycle 2 → controller fixes (P1 ×2, P3 ×3; plan-text reconciliation)

Both re-verifiers confirmed the Cycle-1 fix INTENTS are sound; the remaining P1s are plan-text lag (addendum vs task blocks) + a genuine Task-0 correctness flaw. Controller fixes:

### P1-FIX-10: Task blocks reconciled with addendum semantics (executable plan)
The addendum described `key=` param, `>=` breach, single dedup key, computed Retry-After, list-fixture asserts — but Task 1/3 code blocks were NOT updated (contradiction). Execution rules (overrides the earlier Task 1/3 text where they conflict with the addendum):
- **Task 1 helper signature (FINAL, superseded by P1-A):** `def _check_ip_bucket_rate_limit(request, *, buckets, lock, limit, window_s, detail, retry_after_s=None, key=None, max_entries=10_000)`; `key` is a REQUIRED kwarg at the wrapper call sites (no silent re-keying footgun); wrappers resolve `key=(getattr(request.state, "client_ip", None) or request.client.host)` (sensitive: composite `(resolved_ip, op)`). `retry_after_s=None` → compute `int(oldest + window_s - now)` on exhaustion; register/sensitive wrappers pass `retry_after_s=3600` (flat, byte-identical).
- **Task 1 test (FINAL):** `test_signup_ip_limit_2_per_24h` asserts `retry-after` header is an integer ≤ 86400 (NOT `== "86400"` — computed remaining is 86399 after ≥1s elapse); register contract test (`test_shared_ip_bucket_3_per_hour`) still asserts exact `"3600"`.
- **Task 3 tracker (FINAL):** breach = `>=`; threshold default 2; dedup key = bare `ip` in BOTH `record_signup` (on breach) and `record_block` (on 429) — byte-identical key form; NO `_blocks` store; `_notified` pruned inside both feeds; `reset()` method; `notify` payload `{"ip": ip, "count": n, "threshold": t, "window_s": w}`. Memory-bound test asserts stale-prune (`== 9900` per P1-B), not `< 10100`.
- **Task 3 unit tests (FINAL, list-fixture convention):** `test_success_feed_breach_notifies_once` — 2 mints → 1 notify at t2 (`[c[0] for c in notified] == ["abuse_signup_velocity"]`, `len(notified) == 1`, t3 dedup'd); `test_block_path_same_episode` — 2 mints + 1 blocked 429 → exactly 1 notify total; `test_window_expiry_rearms` — second burst after window expiry re-notifies; `test_memory_bound` — 10,100 distinct IPs with 200 stale → prune keeps `len(_by_ip) == 9900`; `test_kill_switch` — `TORTOISE_ABUSE_DISABLED=1` → no notify.
- **Task 3 integration:** `test_signup_ip_limit_and_velocity_single_notify` (test_agent_signup.py, `delenv("RATE_LIMIT_DISABLED")`) — 3rd signup 429 AND exactly ONE `abuse_signup_velocity` notify (fires on the success feed at mint 2; the 429's block path is dedup-suppressed — comment corrected from "(block path, same episode)" to "(success feed at allowance boundary; block path dedup-suppressed)").
- **P3-FIX-6 (fixture):** conftest autouse resets `_SIGNUP_BUCKETS` (getattr-guarded) AND calls `SignupVelocityTracker.reset()` (new method clearing `_by_ip` + `_notified`) — prevents order-dependent dedup flake from the module-scoped `testclient` host.

### P1-FIX-11: Task 0 → Fly-Client-IP (NOT `--forwarded-allow-ips="*"`)
Verifier B verified against Fly docs: X-Forwarded-For is client-and-proxy chain; uvicorn parses the FIRST entry; a spoofing client controls it → `--forwarded-allow-ips="*"` is bypassable (defeats the entire per-IP cap) and the 2-egress-IP staging test doesn't catch the spoof dimension. **FINAL Task 0 design:**
1. Confirm the premise in staging: log `request.client.host` vs `X-Forwarded-For` vs `Fly-Client-IP` for 2 distinct egress IPs.
2. **Spoof probe (hard blocker):** send `X-Forwarded-For: 203.0.113.99` (forged) → assert `request.client.host != "203.0.113.99"`. If it equals the forged value → do NOT ship `--forwarded-allow-ips="*"`.
3. **Fix:** app middleware parses `Fly-Client-IP` header (non-spoofable per Fly docs — set by the Fly proxy, no upstream override) into `request.state.client_ip`; limiters read `request.state.client_ip` (fallback: `request.client.host`). Precedented middleware pattern in hosted_api.py. Small, contained, testable (monkeypatch header).
4. Record the probe result + chosen source in the Handoff; Task 0 is a hard pre-deploy gate for Tasks 1/3 (also un-breaks the EXISTING register/sensitive-op limiters, which are live-global in prod today if the premise holds).

### P3-FIX-12: Handoff = 8 tasks (Task 0 listed explicitly with staging gate + spoof probe as pre-deploy blocker)

### P3-FIX-13: Test placement
- `test_signup_ip_limit_and_velocity_single_notify` → Task 3, tests/test_agent_signup.py.
- `test_sensitive_op_composite_key_preserved` → DROPPED (redundant with locked `test_export_rate_limited_independently` which already guards composite keying).
- `test_signup_feeds_velocity_tracker` (feed-spy) → Task 3, monkeypatch `tortoise.abuse.record_signup` + `record_signup_block`.

### P3-FIX-14: dedup key form documented
`_notified` keyed by bare `ip` (string) in both feeds; the success-breach and block paths MUST use the identical key form (`_notified[ip]`) — guarded by `test_block_path_same_episode`.

### Exit: cycle 2 — both verifiers: P0=0, P1→fixed. Gate advances on next clean pass.

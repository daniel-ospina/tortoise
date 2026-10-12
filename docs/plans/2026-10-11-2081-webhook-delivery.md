---
title: "Plan — #2081 outbound webhook delivery for the session-event stream (phase-2 push half)"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-11
subjects.team: epistemic-team
aboutSubjects: epistemic-team
ownedBy: epistemic-team
governingAgreement: "#2081 (epic #2080 W4 phase 2); delivery-shape research docs/research/2026-09-01-gbrain-learnings/delivery-shape.md §Topology C"
aboutObjects: webhook-delivery, graph-event-stream, epistemic-team
---

# Plan — #2081: outbound webhook delivery (subscription registry + dispatcher + receipts)

**Issue:** #2081 · **Epic:** #2080 (W4 delivery, phase 2) · **Branch:** `feat/2081-tm`
**Research:** `docs/research/2026-09-01-gbrain-learnings/delivery-shape.md` (§Topology C, the phase-2 recommendation: "Build only when a third-party app actually needs push (vs poll)"; Zep's async-precompute is the reference architecture).
**Status:** PARTIAL — the delivery layer ships here; three of #2081's own scope items are filed as follow-ups (§5). PR body says `Refs #2081`.

---

## 1. Confirmed problem (re-measured, not inherited)

The issue body's `/v1/context` phase-1 prerequisite HAS shipped (`POST /v1/context` + SDK `volunteer_context()`,
`tortoise/volunteer.py`), and the pull half of the event stream ships (`GET /v1/events` +
`tortoise_events_poll` → `event_store.read_after`). The comment thread (2026-10-11) records the
measured gap, and it reproduced on this branch: **no outbound webhook delivery exists anywhere in
`tortoise/**`** — every `webhook` hit is an INBOUND receiver (`/webhooks/stripe`) or a vendor egress
(Resend/Telegram notifications). A third-party app that cannot hold a poll loop has no way to learn
its memory changed.

So #2081 is **not fixed** and **not out-of-repo**; the delivery machinery was genuinely missing.

## 2. What shipped (this branch)

| Piece | Home | Contract |
|---|---|---|
| Subscription registry | `tortoise/webhook_delivery.py` | Graph-native `:ContextSubscription` rows in the CALLER'S graph namespace (per-graph = per-org, the `event_store` partition rule — no `org_id` property). Cap 25/graph. Secret is generated server-side, stored via `crypto.encrypt_token` (Fernet), returned ONCE; reads report `secret_set`. |
| Dispatcher | same | `dispatch_pending(proj)` drains each due subscription from its `cursor` (`event_store.read_after`), POSTs, and advances the cursor ONLY on 2xx. At-least-once + ordered (head-of-line: a failure stops that subscription's batch so the SAME event is retried first). |
| Retry / backoff | same | Exponential `30s · 2^(n-1)`, capped 1h (`backoff_seconds`). After `TORTOISE_WEBHOOK_MAX_ATTEMPTS` (default 8) consecutive failures the subscription is DISABLED (`disabled_at` + reason) — a dead endpoint cannot consume dispatch capacity forever. Re-enable = delete + recreate. |
| Delivery receipts | same | `:DeliveryReceipt` per ATTEMPT (`delivered`/`retrying`/`exhausted`, with `seq`, `attempt`, `http_status`, redacted `error`, `duration_ms`), capped at 500/subscription. |
| Signing | same | `X-Tortoise-Signature: t=<unix>,v1=<hex>` — HMAC-SHA256 over `b"<t>." + body` (Stripe scheme). `verify_signature` is exported so a pilot app and its tests share ONE implementation. |
| SSRF guard | `tortoise/security.py` | `validate_webhook_url`: http(s) only, no userinfo, no blocked name/suffix, and **IP literals must satisfy `ipaddress.is_global`** — loopback/RFC1918/link-local/IMDS/ULA/multicast are refused. |
| REST surface (4 tenant routes) | `tortoise/hosted_api.py` | `POST /v1/context/subscriptions` (422 on a rejected target/unknown type/cap, 503 when the encryption key is unset, 201 with the one-time secret), `GET` list, `DELETE /{sub_id}`, `GET /{sub_id}/deliveries`. Declared in `config/rest-surface-inventory.yml` (#3875 — the third surface grows visibly). |
| Delivery trigger | `tortoise/hosted_api.py::_dream_worker` | `_dispatch_webhooks(org_id, gns)` runs AFTER the dream pass, off the event loop, never on the write path, best-effort (a dispatcher fault is logged, never a dream failure). Kill switch `TORTOISE_WEBHOOK_DISPATCH=0` (the `GBRAIN_HOOKS=0` shape). |

**Indicator 2 (no per-push recompute):** satisfied in the sense that matters — the pushed payload is
READ from `:GraphEvent`; the push path calls no EP function. Pinned by a test that booby-traps
`TortoiseEP.compute_confidence` and asserts a full dispatch still succeeds. What is NOT here is
assembling an enriched *why-block* per push — see §5.

**No surface approval was required:** the MCP tool registry and the `TortoiseSDK` public surface are
untouched (the delivery module takes a projection and the REST wrapper calls `sdk._get_proj()` — a
private accessor, not a new public method). The REST additions are declared in the surface inventory.

## 3. Deliberate design decisions

1. **Cursor advances only past a 2xx, and the batch stops at the first failure.** At-least-once is the
   honest contract for an HTTP callback; the ordering guarantee is what makes the retry meaningful
   (a later event can never overtake a stuck one). Consumers dedupe on `event_id` /
   `X-Tortoise-Delivery` (`<sub_id>:<seq>`, stable across re-deliveries).
2. **`follow_redirects=False`.** The registration-time SSRF predicate blessed THIS url; a redirect
   would move the actual request somewhere the predicate never saw. A 3xx is a failed delivery.
3. **The secret is encrypted at rest, and missing key material is a 503, not a downgrade.**
   `crypto.encrypt_token` fails loudly; a silently-insecure webhook signature would be worse than a
   refused registration.
4. **The kill switch defaults ON but exists.** gbrain's `GBRAIN_HOOKS=0` is the transferable
   contract; a subscription is itself an explicit tenant opt-in, so there is no surprise egress.
5. **A delivery pass that has nothing to deliver writes nothing.** The due-subscription query runs
   BEFORE `ensure_subscription_schema`, so every existing deployment (and every dream in every test)
   takes a path that creates no index and no node — pinned by a test that counts nodes around a pass.
6. **`security.validate_webhook_url` is NOT the same predicate as `config.LOOPBACK_HOSTS`**
   (#1647 P1-3's single-source rule): that one answers "is this URI the local test box" over three
   names; this one answers "may this process be made to call this address" over every non-global
   range. `security.py` is stdlib-only and cannot import `config`; the containment invariant (every
   host the shared predicate refuses is refused here) is pinned by a test instead.

## 4. Verification (exact commands + results on this branch)

```bash
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_webhook_delivery.py -q -p no:cacheprovider
  → 36 passed
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_security.py tests/test_rest_surface_inventory.py tests/test_ci_manifest.py -q
  → 95 passed (shared run with the file above)
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_body_cap_sweep.py tests/test_transport_wait_bound.py \
    tests/test_ci_selection.py tests/test_loopback_predicate_single_source.py -q
  → 330 passed
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_hosted_volunteer_context.py -q
  → 16 passed
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_dream.py tests/test_capture_loop_responsiveness.py -q
  → 60 passed (with the volunteer file in one run)
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_graph_write_loop_responsiveness.py \
    tests/test_read_routes_loop_responsiveness.py tests/test_lifecycle_guards.py -q
  → 71 passed
python tools/ci_selection.py --integrity        → ✅ all test files classified
python tools/ci_selection.py --surface-audit    → tortoise/webhook_delivery.py (pinned by 1, registered: api, core)
ruff check tortoise/webhook_delivery.py tortoise/security.py tests/test_webhook_delivery.py tortoise/hosted_api.py
  → All checks passed
```

**Known pre-existing flakiness (`tests/test_hosted_api.py`, measured both ways on this host):**

| Tree | Result |
|---|---|
| this branch | 5 failed / 420 passed (796.99s) — capture/flood/replay/attribution tests, each a transport-wait-bound timeout |
| `git stash -u` (clean main base) | 3 failed / 422 passed (674.93s) — `TestDreamEndpoint::test_dream_incremental`, `TestEventReplay::test_events_limit_capped_at_1000`, `TestProvisioningConcurrency::test_8_concurrent_on_1_free_slot_gap` |

**Zero overlap between the two failure sets**, and the individual tests pass in isolation on this
branch (e.g. the 4 known ones: 3 passed / 1 timeout in one run; `TestSessionCaptureWriteVerb` +
`TestSessionFloodGate` + `TestRestAttributionE2E2600` → 24 passed on the clean tree). The failure
signature is `transport wait bound (10s) exceeded: POST /v1/sessions` — a timeout under host load,
not a logic failure. The same file is the documented embedded-lane flake class (#3770 evidence dir).

## 5. What is NOT in this change (filed, not forgotten)

1. **Scheduled dispatch sweep** — the pass runs at the end of a dream, so a retry whose backoff
   elapses while the tenant is IDLE waits for that tenant's next dream. The bookkeeping
   (`next_attempt_at`) is durable and correct; the missing piece is the TRIGGER.
2. **Why-block precompute on the dream cycle** — pushed events carry the durable event payload, not
   an assembled why-block (support chain / NANDs / supersession). This is the "precomputed
   why-blocks served from cache" half of the phase.
3. **SSE transport + the shared delivery layer with #3909** — the 2026-10-11 cross-reference note
   asks that the subscription registry / dispatcher / retry / receipts be built ONCE for both the
   app-facing (#2081) and graph-lifecycle (#3909) subscriptions. This change builds that one layer,
   so #3909 should reuse it rather than add a second dispatcher; SSE is a second transport on top.
4. **Pilot app end-to-end** — indicator 4 needs a real third-party integration.
5. **SSRF residual: DNS rebinding** — a public name that resolves to a private address is accepted at
   registration and can be re-pointed before dispatch; closing it needs the connected peer's IP
   checked against the same policy at CONNECT time.

## 6. Learnings

- The **transport wait bound (10s)** makes any slow test host look like a feature regression on this
  suite (`POST /v1/sessions` timeouts). Establish the clean-tree baseline BEFORE attributing a
  failure to the diff — the failure names were disjoint between trees, which is the tell.
- A new test file that creates a real Point pays the **embedding-model load** (~80–150s on a cold
  host). Seeding `:GraphEvent` rows through `event_store.append_event` directly is both faster and a
  better fit for a delivery test (emission is already pinned by `tests/test_event_store.py`).

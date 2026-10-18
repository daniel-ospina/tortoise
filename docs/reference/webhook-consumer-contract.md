---
title: "Webhook consumer contract — receiving Tortoise push deliveries"
type: reference
domain: capability
doc_status: live
created: 2026-10-18
subjects.team: epistemic-team
aboutSubjects: epistemic-team
ownedBy: epistemic-team
governingAgreement: "#8102 (pilot app, indicator 4 of #2081); sender side docs/plans/2026-10-11-2081-webhook-delivery.md"
aboutObjects: webhook-delivery, graph-event-stream, pilot-app
---

# Webhook consumer contract

The consumer-side half of the #2081 push path: what a third-party app receives
when it subscribes to a graph's event stream, and what it must do to be correct
under the sender's **at-least-once, ordered-per-subscription** contract.

Sender implementation: `tortoise/webhook_delivery.py` (dispatcher, signing,
receipts — #2081 / PR #8104). REST surface: `tortoise/hosted_api.py`
(`/v1/context/subscriptions*`). A working consumer, exercised end-to-end against
the real dispatcher, is `integrations/webhooks/pilot/` (#8102).

---

## 1. Registering

```http
POST /v1/context/subscriptions
Authorization: Bearer <key with graphs:write>
Content-Type: application/json

{"url": "https://app.example.com/webhooks/tortoise",
 "types": ["PointAdded"],          // optional filter; omit for all event types
 "description": "pilot app"}       // optional, ≤500 chars
```

`201` returns the record **including `secret` — the only time it is ever
returned**. Later reads (`GET /v1/context/subscriptions`) report `secret_set`
instead, and the secret is stored encrypted at rest (`TORTOISE_ENCRYPTION_KEY`).
Store it as a credential.

Refusals worth handling in a client:

| Status | Cause |
|---|---|
| `401`/`403` | auth fail-closed, or the key lacks `graphs:write` |
| `422` | SSRF gate rejected the URL (non-http(s), userinfo, loopback / private / link-local / metadata host), unknown event type, or the per-graph cap of 25 subscriptions |
| `503` | deployment has no encryption key — secrets cannot be issued |

The subscription lives in the caller key's graph namespace (per-graph =
per-org), so a graph-bound key subscribes to that graph's events only.

## 2. What arrives

`POST` to the registered URL, `Content-Type: application/json`:

| Header | Value | Use |
|---|---|---|
| `X-Tortoise-Signature` | `t=<unix>,v1=<hex>` | **verify before parsing** |
| `X-Tortoise-Delivery` | `<sub_id>:<seq>` | dedupe key, stable across re-deliveries |
| `X-Tortoise-Event` | event type, e.g. `PointAdded` | routing/logging |
| `X-Tortoise-Seq` | the event's per-graph monotonic `seq` | ordering |
| `User-Agent` | `tortoise-webhook/1` | — |

Body (compact JSON, keys sorted):

```json
{
  "subscription_id": "sub_01J...",
  "delivery_id": "sub_01J...:42",
  "event": {
    "event_id": "evt-01J...",
    "seq": 42,
    "type": "PointAdded",
    "ts": "2026-10-18T19:03:07.930843+00:00",
    "payload": {"id": "pt_0", "content": "..."}
  }
}
```

`payload` is the durable event payload **as written at mutation time** — the push
path performs no belief propagation. Per-push *why-block assembly* (support
chain / NANDs / supersession) is a separate, not-yet-shipped precompute
(docs/plans/2026-10-11-2081-webhook-delivery.md §5.2); today a consumer sees the
event stream, and anything richer is a graph read on the consumer's side.

## 3. Verifying

The scheme is Stripe's: HMAC-SHA256 over `b"<t>." + body`. The sender exports
the verifier so a consumer's tests can share it
(`tortoise/webhook_delivery.verify_signature`), and it is ~8 lines standalone:

```python
import hashlib, hmac, time

def verify(secret: str, header: str, body: bytes, tolerance_s: int = 300) -> bool:
    try:
        fields = dict(p.split("=", 1) for p in header.split(","))
        ts, v1 = int(fields["t"]), str(fields.get("v1", ""))
    except Exception:
        return False
    if abs(int(time.time()) - ts) > tolerance_s:
        return False                      # a capture is not replayable forever
    expected = hmac.new(secret.encode(),
                        str(ts).encode() + b"." + body,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
```

Two non-negotiables:

1. **Verify the raw bytes you received**, before JSON parsing or any
   re-serialisation (a round-trip through a parser changes the bytes and will
   fail verification).
2. **Return `401` and change no state** on a mismatch. The tolerance window is
   300 s.

## 4. Being idempotent (the part that actually matters)

The sender advances a subscription's cursor **only past a 2xx**, and holds
head-of-line ordering (a failure stops that subscription's batch so the same
event is retried first). Consequences for a consumer:

- The same event **will** arrive more than once (a crash between the POST and the
  cursor write, a forced redelivery, a retry after a timeout you did not answer).
- Dedupe on `event_id` and/or `X-Tortoise-Delivery` — **durably** (a process that
  restarts between two deliveries must still know), e.g. a unique index rather
  than an in-memory set.
- Answer **2xx for a duplicate too**. A 4xx for "I already have this" makes the
  sender retry it and stalls every later event behind it.

## 5. Retry, backoff, and the end of the line

- Backoff: `30 s · 2^(n-1)`, capped at `1 h` (constants `BASE_BACKOFF_S` /
  `MAX_BACKOFF_S` in `tortoise/webhook_delivery.py`).
- After `TORTOISE_WEBHOOK_MAX_ATTEMPTS` consecutive failures (default **8**) the
  subscription is **disabled** (`disabled_at` + reason); it is not retried again
  (`TORTOISE_WEBHOOK_MAX_ATTEMPTS` overrides the default).
- Dispatch runs at the **end of the tenant's dream cycle**, so latency is bounded
  by the dream cadence, not by the write. A retry whose backoff elapses while the
  tenant is idle waits for that tenant's next dream (the scheduled-sweep gap,
  #2081 §5.1).
- A `3xx` is a failed delivery: the sender does not follow redirects (the
  registration-time SSRF check blessed the original URL only).

## 6. Inspecting deliveries

```http
GET /v1/context/subscriptions                     # list (never secrets)
GET /v1/context/subscriptions/{sub_id}/deliveries # receipts, newest first
DELETE /v1/context/subscriptions/{sub_id}         # delete the subscription + receipts
```

One `:DeliveryReceipt` is written per **attempt**: `status`
(`delivered` / `retrying` / `exhausted`), `attempt`, `http_status`, redacted
`error`, `duration_ms`, `seq`, `ts` — retained at 500 per subscription, newest
kept. This is the sender's record of the same delivery the consumer logged: when
the two disagree, the receipts say which attempt the sender actually made.

## 7. Known gaps (measured while wiring the pilot, filed against #2081)

- **No "start from now"** — a new subscription's cursor is always `0`, so a
  third party onboarding onto a graph with history receives the **entire** stream
  in batches of 50 per dream cycle, and `DELETE` + recreate replays it again
  (the `DELETE` route's "resume from the tail" note does not hold). Handle it by
  deduping/ignoring history on your side. Filed: **#9029**.
- **One signature timestamp per dispatch pass** — every delivery in a batch
  carries the same `t=`. A batch that takes longer than the 300 s tolerance to
  drain is unverifiable at the tail. Filed: **#9030**.
- **No per-event skip / dead-letter** — a payload the consumer permanently
  rejects blocks every later event (head-of-line) until the subscription is
  disabled. Return 2xx and discard on your side if a payload is poisonous; the
  recovery path in #9029 is also part of this story.

## 8. Reference consumer

`integrations/webhooks/pilot/` implements exactly this contract (verify →
dedupe → inject), with `demo.py` producing a replayable transcript against the
real dispatcher:

```bash
python integrations/webhooks/pilot/demo.py --out /tmp/pilot-demo
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_webhook_pilot.py -q
```

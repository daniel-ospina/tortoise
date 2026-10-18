# Webhook pilot — a real consumer on Tortoise's push path

The pilot app for **#8102** (indicator 4 of **#2081**, epic #2080 W4 phase 2):
one consumer that learns about graph changes **only** by push, verifies the
sender's signature, dedupes re-deliveries durably, and injects what it received
into its own turns.

- Consumer contract (headers, envelope, verification, retry semantics):
  [`docs/reference/webhook-consumer-contract.md`](../../../docs/reference/webhook-consumer-contract.md)
- Sender: `tortoise/webhook_delivery.py` (#2081 / PR #8104)
- Tests: `tests/test_webhook_pilot.py` · Demo: `demo.py`

## What it proves

| #8102 indicator | How this pilot shows it |
|---|---|
| A real app receives a push **without polling** | The only input is `POST /webhooks/tortoise`. There is no poll path in `app.py` — pinned structurally by `test_the_pilot_app_has_no_poll_path` (AST, not a grep: the docstring may name the pull path, no executable reference may). |
| Signature verification accepts real deliveries, rejects a tampered one | Real deliveries are verified with `webhook_delivery.verify_signature` (the sender's own implementation). A changed body under the original signature → `401` and **no** state change; a capture older than the 300 s tolerance → `401`. |
| Dedupe on `event_id` / `X-Tortoise-Delivery` survives a forced re-delivery | The demo resets the subscription cursor to `0` and dispatches again: both events are re-sent to real `2xx`, the app injects **nothing** twice, and the dedupe still holds after the app is restarted on the same state directory (SQLite, not a set). |

## Files

| File | What it is |
|---|---|
| `app.py` | The pilot consumer: threaded HTTP listener, signature verification, SQLite dedupe store, why-context block, `next_turn()` injection, `received.jsonl` audit trail. |
| `demo.py` | End-to-end demo against the **real** dispatcher (`dispatch_pending` with its real `httpx` transport) — pushes, forced re-delivery, tampered body, stale capture, restart-dedupe, receipts. Writes a markdown + JSONL transcript. |
| `demo-artifacts/` | The committed transcript of a real run (`transcript.md`, `transcript.jsonl`). Regenerate with the command in §Run. |

## Run it

Against a live graph (embedded FalkorDB, no network needed):

```bash
python integrations/webhooks/pilot/demo.py --out /tmp/pilot-demo
# → 11/11 steps passed, /tmp/pilot-demo/transcript.md
```

The whole exchange, demonstrated (the demo prints it; `--state-dir` keeps the
graph and the app's state if you want to inspect them):

1. seed two durable `:GraphEvent` rows through `event_store`;
2. register a subscription through the **real** `security.validate_webhook_url`
   SSRF gate (public URL shape);
3. start the pilot on ephemeral loopback and point the stored target at it;
4. `dispatch_pending` → 2 real POSTs, app accepts both;
5. `next_turn()` shows the pushed block injected into the prompt;
6. cursor reset → re-dispatch → **0 duplicated effects**;
7. 4 delivery receipts (`delivered` ×4) in the graph;
8. a tampered body under the real signature → `401`;
9. a 360 s-stale capture → `401`;
10. app restarted on the same state → the same delivery is still a duplicate;
11. subscription state: `cursor=2`, `delivered=4`, not disabled.

### Against a public endpoint (the real third-party case)

`validate_webhook_url` refuses loopback/private/metadata targets **by design**
(it is an SSRF gate, and it must not grow an exemption), so the demo registers
against a gate-passing URL and repoints the stored target at its own loopback
listener. With a public HTTPS endpoint — a tunnel or a deploy — run the app and
pass the URL instead; nothing is repointed:

```bash
# terminal 1 — expose the app (any tunnel: cloudflared, ngrok, a Fly deploy)
python integrations/webhooks/pilot/app.py --state-dir ~/.pilot --secret "$SECRET" --port 8080
cloudflared tunnel --url http://localhost:8080      # → https://<id>.trycloudflare.com

# terminal 2 — register the PUBLIC url (no repoint, real gate)
curl -X POST https://api.premiselabs.co/v1/context/subscriptions \
  -H "Authorization: Bearer $TORTOISE_API_KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://<id>.trycloudflare.com/webhooks/tortoise","types":["PointAdded"]}'
# → 201 with "secret": store it, start the app with --secret "$SECRET"
```

The secret is returned **once**; pass it to the app via `--secret` (or
`TORTOISE_WEBHOOK_SECRET`). Pin the app to its subscription with
`--subscription-id sub_…` to `403` any other tenant's deliveries.

## State it keeps

```
<state-dir>/
  deliveries.sqlite3   # dedupe: delivery_id PK + UNIQUE(event_id) — survives restart
  received.jsonl       # audit: every request as received (status, signature, body)
  turns.jsonl          # each turn's prompt + which delivery_ids were injected
```

`received.jsonl` deliberately keeps the raw body and signature: replaying them is
how the demo proves a tampered body is refused, and it is what a real consumer
retains to answer "what did you send us?".

## Not in this pilot (filed, not patched around)

Wiring a real consumer surfaced three contract gaps — recorded in
`docs/reference/webhook-consumer-contract.md` §7 and filed against #2081 rather
than patched around here:

- **#9029** — no "start from now": a new subscription's cursor is hardcoded to
  `0`, so registering on a graph with history replays the whole stream (and
  `DELETE` + recreate replays it again, contradicting that route's own note).
- **#9030** — one signature timestamp per dispatch pass: a pass slower than the
  300 s tolerance is unverifiable at its tail.
- no per-event skip/dead-letter (part of the #9029 recovery story): the pilot
  recommends the documented consumer workaround — return `2xx` and discard a
  payload you cannot use.

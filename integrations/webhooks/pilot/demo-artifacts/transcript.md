# Pilot demo transcript — #8102 (indicator 4 of #2081)

- Generated: `2026-10-18T19:09:12.614375+00:00`
- Command: `python integrations/webhooks/pilot/demo.py --out integrations/webhooks/pilot/demo-artifacts`
- Result: **11/11 steps passed**

> Regenerate with the command above. The app under test is
> `integrations/webhooks/pilot/app.py`; the sender is the shipped
> dispatcher `tortoise/webhook_delivery.dispatch_pending`.

## Steps

| # | Step | Result | Detail |
|---|------|--------|--------|
| 1 | seed the durable :GraphEvent stream | PASS | `{"event_ids": ["evt-1a1506b0b5d-4460e16d190d", "evt-1a1506b0bbf-989f8f1d0d9c"], "graph": "/var/folders/1g/wrx4rqtd2xg2rgx_0f3_tm8m0000gn/T/tm8102-pilot-_2shpkfz/graph.db"}` |
| 2 | register the subscription (real security.validate_webhook_url gate) | PASS | `{"sub_id": "sub_1a1506b0c4c-bbb6a706f42b", "url": "https://hooks.example.com/webhooks/tortoise", "types": ["PointAdded"], "secret_returned_once": true, "signature_header": "X-Tortoise-Signature"}` |
| 3 | start the pilot consumer | PASS | `{"listening": "http://127.0.0.1:61772/webhooks/tortoise", "target": "http://127.0.0.1:61772/webhooks/tortoise", "target_repointed_to_loopback": true, "webhook_path": "/webhooks/tortoise"}` |
| 4 | dispatch_pending pushes both events (app never polled) | PASS | `{"dispatch": {"enabled": true, "subscriptions": 1, "delivered": 2, "failed": 0, "disabled": 0, "more_pending": 0}, "app": {"accepted": 2, "duplicates": 0, "rejected": 0, "events": 2, "turns": 0}}` |
| 5 | the pushed block is injected into the next turn | PASS | `{"turn": 1, "injected_delivery_ids": ["sub_1a1506b0c4c-bbb6a706f42b:2", "sub_1a1506b0c4c-bbb6a706f42b:1"], "prompt": "You are a memory-aware assistant. The block below was PUSHED to this app by Tortoise; it was not polled for.\n\n## Memory pushed by Tortoise (no polling)\n- PointAdded · seq 2 · evt-1a1506b0bbf-989f8f1d0d9c · 2026-10-18T19:09:16.862815+00:00\n  {\"content\": \"pilot app #8102\",...` |
| 6 | forced re-delivery (cursor reset): 0 duplicated effects in the app | PASS | `{"dispatch": {"enabled": true, "subscriptions": 1, "delivered": 2, "failed": 0, "disabled": 0, "more_pending": 0}, "app": {"accepted": 2, "duplicates": 2, "rejected": 0, "events": 2, "turns": 1}, "memory_block": "## Memory pushed by Tortoise (no polling)\n- PointAdded · seq 2 · evt-1a1506b0bbf-989f8f1d0d9c · 2026-10-18T19:09:16.862815+00:00\n  {\"content\": \"pilot app #8102\", \"id\": \"pt_1\"...` |
| 7 | delivery receipts record every attempt (4 = 2 + 2 re-delivered) | PASS | `{"receipt_count": 4, "statuses": ["delivered", "delivered", "delivered", "delivered"], "seqs": [2, 1, 2, 1]}` |
| 8 | a tampered body is refused (401) and injects nothing | PASS | `{"status": 401, "response": {"ok": false, "error": "bad-signature"}, "original_body": "{\"delivery_id\": \"sub_1a1506b0c4c-bbb6a706f42b:2\", \"event\": {\"event_id\": \"evt-1a1506b0bbf-989f8f1d0d9c\", \"payload\": {\"content\": \"pilot app #8102\", \"id\": \"pt_1\"}, \"seq\": 2, \"ts\": \"2026-10-18T19:09:15.073368+00:00\", \"type\": \"PointAdded\"}, \"subscription_id\": \"sub_1a1506b0c4c-bbb6a...` |
| 9 | a 300s-stale capture is refused (401) | PASS | `{"status": 401, "response": {"ok": false, "error": "bad-signature"}, "signature_timestamp": 1792350197}` |
| 10 | dedupe survives a restart: the same delivery is still a duplicate | PASS | `{"status": 200, "response": {"ok": true, "duplicate": true, "delivery_id": "sub_1a1506b0c4c-bbb6a706f42b:2", "event_id": "evt-1a1506b0bbf-989f8f1d0d9c", "injected": false}, "app": {"accepted": 0, "duplicates": 1, "rejected": 0, "events": 2, "turns": 0}}` |
| 11 | subscription state: cursor advanced twice over the same two events | PASS | `{"subscription": {"sub_id": "sub_1a1506b0c4c-bbb6a706f42b", "url": "http://127.0.0.1:61772/webhooks/tortoise", "types": ["PointAdded"], "description": "pilot app (#8102)", "created_at": "2026-10-18T19:09:15.212826+00:00", "created_by": null, "cursor": 2, "attempts": 0, "next_attempt_at": null, "disabled_at": null, "disabled_reason": null, "delivered": 4, "failed": 0, "last_error": null, "last_d...` |

## What this proves (issue #8102 indicators)

| Indicator | Evidence in this transcript |
|---|---|
| a real push received without polling | step 4 — the app has no poll code path; its only input is `POST /webhooks/tortoise` |
| real signature accepted, tampered one refused | steps 4, 8, 9 |
| forced re-delivery produces 0 duplicated effects | steps 6, 7, 10 |


## Environment

- pilot app: `integrations/webhooks/pilot/app.py` (state `/var/folders/1g/wrx4rqtd2xg2rgx_0f3_tm8m0000gn/T/tm8102-pilot-_2shpkfz/app`)
- dispatcher: `tortoise.webhook_delivery.dispatch_pending` (real `httpx` transport, `follow_redirects=False`)
- retry policy observed: base `30s`, cap `3600s`, disable after `8` consecutive failures
- signature scheme: `X-Tortoise-Signature` — HMAC-SHA256 over `b"<t>." + body`, tolerance `300s`


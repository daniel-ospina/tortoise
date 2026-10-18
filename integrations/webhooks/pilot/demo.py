#!/usr/bin/env python3
"""End-to-end demo of the pilot consumer on a REAL push — #8102 (indicator 4 of #2081).

This drives the shipped delivery layer (``tortoise/webhook_delivery.py``) against
the pilot app in this directory and records what happened:

1. **push, not poll** — the app receives its two events over HTTP from
   ``dispatch_pending``; it has no poll code path at all;
2. **signature verification** — a real delivery is accepted, a body whose bytes
   were changed under the same signature is refused with 401 (and a capture
   outside the tolerance window is refused too);
3. **dedupe in practice** — the subscription cursor is FORCED back to 0, the
   dispatcher re-delivers both events (at-least-once, exactly as a crash between
   POST and cursor write would), and the app injects nothing twice — including
   after the app is restarted on the same state directory.

Two honest notes about the target:

* **Loopback.** ``security.validate_webhook_url`` refuses loopback/private/
  metadata targets at registration — that is the SSRF gate, and it must not grow
  an exemption. The demo therefore registers through the REAL gate against a
  public URL shape and then repoints the stored target at its own loopback
  listener (logged as its own step). Pass ``--public-url`` with a tunnel or a
  deployed endpoint and the repoint does not happen — the app code is identical.
* **A real third-party deploy is a human step** (needs a public HTTPS endpoint
  and hosting). This script is the machine-checkable half: it produces a
  replayable transcript of the whole exchange instead of a video.

    python integrations/webhooks/pilot/demo.py --out /tmp/pilot-demo
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

PILOT_DIR = Path(__file__).resolve().parent


def _load_app_module():
    """Load the sibling ``app.py`` under a unique name (no sys.path pollution)."""
    spec = importlib.util.spec_from_file_location(
        "tortoise_webhook_pilot_app", PILOT_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot_app = _load_app_module()


# ── transcript ────────────────────────────────────────────────────────────

class Transcript:
    """Collects steps and writes a markdown + JSONL record of the run."""

    def __init__(self, out_dir: Path, *, command: str) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.command = command
        self.started = datetime.now(UTC)
        self.steps: list[dict] = []
        self.failed = 0

    def record(self, name: str, ok: bool, **detail) -> bool:
        entry = {"step": len(self.steps) + 1, "name": name, "ok": bool(ok),
                 "at": datetime.now(UTC).isoformat(), "detail": detail}
        self.steps.append(entry)
        if not ok:
            self.failed += 1
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {entry['step']}. {name}")
        for key, value in detail.items():
            rendered = json.dumps(value, ensure_ascii=False)
            if len(rendered) > 220:
                rendered = rendered[:217] + "..."
            print(f"         {key}: {rendered}")
        return ok

    # -- output ----------------------------------------------------------

    def write(self, extra_sections: list[tuple[str, str]]) -> tuple[Path, Path]:
        md = self.out_dir / "transcript.md"
        jsonl = self.out_dir / "transcript.jsonl"
        with jsonl.open("w", encoding="utf-8") as fh:
            for entry in self.steps:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        body = [
            "# Pilot demo transcript — #8102 (indicator 4 of #2081)",
            "",
            f"- Generated: `{self.started.isoformat()}`",
            f"- Command: `{self.command}`",
            f"- Result: **{len(self.steps) - self.failed}/{len(self.steps)} steps "
            f"passed**",
            "",
            "> Regenerate with the command above. The app under test is",
            "> `integrations/webhooks/pilot/app.py`; the sender is the shipped",
            "> dispatcher `tortoise/webhook_delivery.dispatch_pending`.",
            "",
            "## Steps",
            "",
            "| # | Step | Result | Detail |",
            "|---|------|--------|--------|",
        ]
        for entry in self.steps:
            detail = json.dumps(entry["detail"], ensure_ascii=False)
            detail = detail.replace("|", "\\|")
            if len(detail) > 400:
                detail = detail[:397] + "..."
            body.append(f"| {entry['step']} | {entry['name']} | "
                        f"{'PASS' if entry['ok'] else 'FAIL'} | `{detail}` |")
        for title, content in extra_sections:
            body.extend(["", f"## {title}", "", content.strip(), ""])
        md.write_text("\n".join(body) + "\n", encoding="utf-8")
        return md, jsonl


# ── demo ──────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=None,
                        help="graph + app state (default: a fresh temp dir)")
    parser.add_argument("--out", default=None,
                        help="where the transcript is written (default: "
                             "<state-dir>/demo-artifacts)")
    parser.add_argument("--public-url", default=None,
                        help="register THIS public HTTPS endpoint instead of "
                             "repointing a loopback listener (a tunnel/deploy)")
    parser.add_argument("--keep-state", action="store_true",
                        help="reuse --state-dir instead of deleting it first")
    args = parser.parse_args(argv)

    invoked = sys.argv[1:] if argv is None else list(argv)
    command = " ".join(["python", sys.argv[0] if sys.argv else "demo.py",
                        *invoked])
    state_dir = Path(args.state_dir or tempfile.mkdtemp(prefix="tm8102-pilot-"))
    if not args.keep_state and state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out) if args.out else state_dir / "demo-artifacts"
    transcript = Transcript(out_dir, command=command)

    # The signing secret is encrypted at rest with TORTOISE_ENCRYPTION_KEY; a
    # demo generates one when the environment has none (never reuse it).
    if not os.environ.get("TORTOISE_ENCRYPTION_KEY"):
        from cryptography.fernet import Fernet
        os.environ["TORTOISE_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    # Egress ON: this is the feature under test, and the target is loopback.
    os.environ.pop("TORTOISE_WEBHOOK_DISPATCH", None)

    from tortoise import event_store
    from tortoise import webhook_delivery as wd
    from tortoise.config import is_db_uri
    from tortoise.ids import ulid
    from tortoise.sdk import TortoiseSDK

    namespace = (f"pilot_{os.urandom(4).hex()}"
                 if is_db_uri(os.environ.get("TORTOISE_DB_URI")) else None)
    sdk = TortoiseSDK(str(state_dir / "graph.db"), namespace=namespace)
    proj = sdk._get_proj()

    # 1 ─ the graph writes the app must learn about (the real event stream).
    seeded = []
    for i, title in enumerate(("Tortoise ships webhook push", "pilot app #8102")):
        event_id = f"evt-{ulid()}"
        event_store.append_event(
            proj, event_store.next_seq(proj), "PointAdded",
            {"id": f"pt_{i}", "content": title}, event_id)
        seeded.append(event_id)
    transcript.record("seed the durable :GraphEvent stream", len(seeded) == 2,
                      event_ids=seeded, graph=str(state_dir / "graph.db"))

    # 2 ─ register through the REAL SSRF gate (public URL shape).
    registered_url = args.public_url or "https://hooks.example.com/webhooks/tortoise"
    sub = wd.create_subscription(proj, url=registered_url, types=["PointAdded"],
                                 description="pilot app (#8102)")
    transcript.record(
        "register the subscription (real security.validate_webhook_url gate)",
        sub["secret"].startswith("whsec_") and sub["active"],
        sub_id=sub["sub_id"], url=registered_url, types=sub["types"],
        secret_returned_once=bool(sub.get("secret")),
        signature_header=sub.get("signature_header"))

    # 3 ─ start the pilot app and point the subscription at it.
    app = pilot_app.PilotApp(secret=sub["secret"],
                             state_dir=state_dir / "app",
                             expected_subscription_id=sub["sub_id"])
    server = pilot_app.serve(app, host="127.0.0.1", port=0).start()
    if args.public_url:
        target_url = args.public_url
        repointed = False
    else:
        # See the module docstring: the SSRF gate refuses loopback BY DESIGN, so
        # the stored target is moved after a gate-passing registration. Nothing
        # else is relaxed — dispatch, signing and receipts are untouched.
        target_url = server.url
        proj.g.query(
            "MATCH (s:ContextSubscription {sub_id:$id}) SET s.url=$url",
            params={"id": sub["sub_id"], "url": target_url})
        repointed = True
    transcript.record("start the pilot consumer", True, listening=server.url,
                      target=target_url, target_repointed_to_loopback=repointed,
                      webhook_path=pilot_app.WEBHOOK_PATH)

    # 4 ─ the push. One call to the shipped dispatcher; no poll anywhere.
    summary = wd.dispatch_pending(proj)
    stats = app.stats()
    transcript.record(
        "dispatch_pending pushes both events (app never polled)",
        summary["delivered"] == 2 and stats["accepted"] == 2
        and stats["events"] == 2,
        dispatch=summary, app=stats)

    # 5 ─ injection into a real turn.
    turn = app.next_turn("What changed in the graph since my last turn?")
    block = app.memory_block()
    injected = all(event_id in block for event_id in seeded)
    transcript.record("the pushed block is injected into the next turn",
                      injected and turn["injected"],
                      turn=turn["turn"], injected_delivery_ids=turn["injected"],
                      prompt=turn["prompt"])

    # 6 ─ forced re-delivery: blow the cursor away, dispatch again.
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.cursor=0",
        params={"id": sub["sub_id"]})
    replay = wd.dispatch_pending(proj)
    stats_after_replay = app.stats()
    block_after = app.memory_block()
    transcript.record(
        "forced re-delivery (cursor reset): 0 duplicated effects in the app",
        replay["delivered"] == 2 and stats_after_replay["duplicates"] == 2
        and stats_after_replay["events"] == 2
        and block_after.count("seq ") == 2,
        dispatch=replay, app=stats_after_replay,
        memory_block=block_after,
        prompt_lines=block_after.count("\n") + 1)

    # 7 ─ receipts: one per attempt, durable in the graph.
    receipts = wd.read_receipts(proj, sub["sub_id"], limit=20)
    delivered_receipts = [r for r in receipts if r.get("status") == "delivered"]
    transcript.record(
        "delivery receipts record every attempt (4 = 2 + 2 re-delivered)",
        len(delivered_receipts) == 4,
        receipt_count=len(receipts),
        statuses=[r.get("status") for r in receipts],
        seqs=[r.get("seq") for r in receipts])

    # 8 ─ a tampered body is refused under the real delivery's signature.
    accepted = _last_accepted_request(app)
    tampered_body = _tamper(accepted["body"])
    status, payload = _post(target_url, tampered_body, accepted["signature"])
    transcript.record(
        "a tampered body is refused (401) and injects nothing",
        status == 401 and app.stats()["events"] == 2
        and app.stats()["rejected"] >= 1,
        status=status, response=payload,
        original_body=accepted["body"], tampered_body=tampered_body,
        events_in_app=app.stats()["events"])

    # 9 ─ an old capture cannot be replayed forever (timestamp tolerance).
    stale_ts = int(time.time()) - (wd.SIGNATURE_TOLERANCE_S + 60)
    stale_body = accepted["body"].encode("utf-8")
    stale_sig = wd.signature_header(sub["secret"], stale_ts, stale_body)
    status_stale, payload_stale = _post(target_url, accepted["body"], stale_sig)
    transcript.record(
        f"a {wd.SIGNATURE_TOLERANCE_S}s-stale capture is refused (401)",
        status_stale == 401,
        status=status_stale, response=payload_stale,
        signature_timestamp=stale_ts)

    # 10 ─ the dedupe survives an app restart (state on disk, not in memory).
    restarted = pilot_app.PilotApp(secret=sub["secret"],
                                   state_dir=state_dir / "app",
                                   expected_subscription_id=sub["sub_id"])
    server.app = restarted
    status_restart, payload_restart = _post(target_url, accepted["body"],
                                            accepted["signature"])
    transcript.record(
        "dedupe survives a restart: the same delivery is still a duplicate",
        status_restart == 200 and payload_restart.get("duplicate") is True
        and restarted.stats()["events"] == 2,
        status=status_restart, response=payload_restart, app=restarted.stats())

    # 11 ─ subscription state after all of it (cursor, counters, activity).
    final_sub = wd.get_subscription(proj, sub["sub_id"]) or {}
    transcript.record(
        "subscription state: cursor advanced twice over the same two events",
        final_sub.get("cursor") == 2 and final_sub.get("delivered") == 4
        and not final_sub.get("disabled_at"),
        subscription=final_sub)

    server.stop()
    app.close()
    restarted.close()
    with contextlib.suppress(Exception):
        # A close failure must not mask the demo result (the graph is temp).
        sdk.close()

    md, jsonl = transcript.write([
        ("What this proves (issue #8102 indicators)",
         "\n".join([
             "| Indicator | Evidence in this transcript |",
             "|---|---|",
             "| a real push received without polling | step 4 — the app has no "
             "poll code path; its only input is `POST "
             f"{pilot_app.WEBHOOK_PATH}` |",
             "| real signature accepted, tampered one refused | steps 4, 8, 9 |",
             "| forced re-delivery produces 0 duplicated effects | steps 6, 7, 10 |",
         ])),
        ("Environment", "\n".join([
            f"- pilot app: `integrations/webhooks/pilot/app.py` "
            f"(state `{state_dir / 'app'}`)",
            "- dispatcher: `tortoise.webhook_delivery.dispatch_pending` "
            "(real `httpx` transport, `follow_redirects=False`)",
            f"- retry policy observed: base `{wd.BASE_BACKOFF_S}s`, cap "
            f"`{wd.MAX_BACKOFF_S}s`, disable after `{wd.max_attempts()}` "
            f"consecutive failures",
            f"- signature scheme: `{sub.get('signature_header')}` — HMAC-SHA256 "
            f"over `b\"<t>.\" + body`, tolerance "
            f"`{wd.SIGNATURE_TOLERANCE_S}s`",
        ])),
    ])
    print(f"\ntranscript: {md}")
    print(f"transcript: {jsonl}")
    print(f"state:      {state_dir}")
    print(f"result:     {len(transcript.steps) - transcript.failed}/"
          f"{len(transcript.steps)} steps passed")
    return 0 if transcript.failed == 0 else 1


# ── helpers ───────────────────────────────────────────────────────────────

def _last_accepted_request(app) -> dict:
    """The newest 200-with-injection request the app logged (its audit trail)."""
    path = Path(app.state_dir) / "received.jsonl"
    accepted = [json.loads(line) for line in path.read_text(encoding="utf-8")
                .splitlines() if line.strip()]
    for record in reversed(accepted):
        if record.get("status") == 200 and record.get("duplicate") is False:
            return record
    raise RuntimeError("the pilot app logged no accepted delivery to replay")


def _tamper(body: str) -> str:
    """Change the payload but keep the signature — the refusal case."""
    if '"content":' in body:
        return body.replace('"content":', '"contentx":', 1)
    # Fallback that works on any JSON envelope: flip the first letter of a value.
    return body.replace("PointAdded", "PointAddeb", 1)


def _post(url: str, body: str, signature: str) -> tuple[int, dict]:
    import httpx

    resp = httpx.post(url, content=body.encode("utf-8"), timeout=10,
                      headers={"Content-Type": "application/json",
                               pilot_app.SIGNATURE_HEADER: signature},
                      follow_redirects=False)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"raw": resp.text[:200]}


if __name__ == "__main__":
    raise SystemExit(main())

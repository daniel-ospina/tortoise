"""Pilot webhook consumer for the Tortoise push path — #8102 (indicator 4 of #2081).

WHAT THIS IS. One real third-party-style consumer of the phase-2 delivery layer
(`tortoise/webhook_delivery.py`, #2081 / PR #8104). It is deliberately a
STANDALONE app — no poll loop, no SDK read path, no import of the dispatcher — so
what it proves is the consumer contract itself:

1. **Push, not poll.** Its only input is an inbound HTTP POST. It never calls
   ``GET /v1/events`` and never holds a cursor: the whole reason the push half
   exists (``docs/research/2026-09-01-gbrain-learnings/delivery-shape.md``
   §Topology C) is an app that cannot hold a poll loop.
2. **Signature verification.** ``X-Tortoise-Signature`` (``t=…,v1=…``, HMAC-SHA256
   over ``b"<t>." + body``) is verified with
   ``webhook_delivery.verify_signature`` — the SAME implementation the sender
   signs with, which is why that helper is exported. A delivery whose body does
   not match its signature is refused with 401 and changes NO state. A delivery
   outside the tolerance window is refused the same way (a captured request is
   not replayable forever).
3. **Dedupe that survives at-least-once.** The dispatcher retries (ordered,
   head-of-line) and can re-deliver the same event after a cursor reset, a crash
   between POST and cursor write, or a forced redelivery. The app keys on
   ``X-Tortoise-Delivery`` (``<sub_id>:<seq>``, stable across re-deliveries) AND
   on ``event_id``, in SQLite on disk — so a duplicate is dropped by a process
   that was restarted between the two deliveries, not just by a warm cache.
4. **Injection.** Accepted events become the app's own why-context block, which
   is prepended to the next turn's prompt (``next_turn``) and recorded, so
   "the memory reached a real turn" is an artifact, not a claim.

The consumer-side reference note (exact headers, envelope, verification call,
retry semantics) is ``docs/reference/webhook-consumer-contract.md``.

RUN IT (see ``README.md`` for the public-endpoint path):

    python integrations/webhooks/pilot/app.py \
        --state-dir /tmp/pilot --secret "$TORTOISE_WEBHOOK_SECRET" --port 8080
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tortoise.webhook_delivery import SIGNATURE_TOLERANCE_S, verify_signature

#: Where the dispatcher's target URL should point on this app.
WEBHOOK_PATH = "/webhooks/tortoise"
#: Header carrying ``<sub_id>:<seq>`` — stable across re-deliveries.
DELIVERY_HEADER = "X-Tortoise-Delivery"
#: Header carrying ``t=<unix>,v1=<hex>``.
SIGNATURE_HEADER = "X-Tortoise-Signature"

#: Events rendered into one why-context block (the block is a prompt prefix, so
#: it is bounded: an app that pushes 10k events must not build a 10k-line prompt).
DEFAULT_MAX_BLOCK_EVENTS = 20


def _iso(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now,
                                 UTC).isoformat()


def header(headers, name: str) -> str | None:
    """Case-insensitive header read (``http.server``/``dict``/``email.Message``)."""
    for key, value in dict(headers).items():
        if key.lower() == name.lower():
            return value
    return None


# ── durable at-least-once bookkeeping ─────────────────────────────────────

class DeliveryStore:
    """SQLite record of every event this app has injected.

    The PRIMARY KEY is the delivery id and there is a UNIQUE index on
    ``event_id``, so a duplicate is refused at the storage layer — the same
    place a real consumer would put it — instead of in a set that dies with the
    process. ``check_same_thread=False`` + a lock because the HTTP server is
    threaded and SQLite connections are not.
    """

    _SCHEMA = (
        "CREATE TABLE IF NOT EXISTS deliveries ("
        "delivery_id TEXT PRIMARY KEY, "
        "event_id TEXT NOT NULL, "
        "subscription_id TEXT NOT NULL, "
        "seq INTEGER NOT NULL, "
        "type TEXT NOT NULL, "
        "payload TEXT NOT NULL, "
        "received_at TEXT NOT NULL)",
        "CREATE UNIQUE INDEX IF NOT EXISTS deliveries_event_id "
        "ON deliveries(event_id)",
    )

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        with self._lock, self._db:
            for stmt in self._SCHEMA:
                self._db.execute(stmt)

    def record(self, *, delivery_id: str, event_id: str, subscription_id: str,
               seq: int, type_: str, payload: dict, received_at: str) -> bool:
        """Store the event. Returns True when this is the FIRST sighting.

        False means the dispatcher re-delivered something already injected — the
        at-least-once contract showing up in practice, not a bug.
        """
        with self._lock:
            try:
                with self._db:
                    self._db.execute(
                        "INSERT INTO deliveries (delivery_id, event_id, "
                        "subscription_id, seq, type, payload, received_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (delivery_id, event_id, subscription_id, int(seq),
                         str(type_), json.dumps(payload, sort_keys=True,
                                                ensure_ascii=False),
                         received_at))
                return True
            except sqlite3.IntegrityError:
                # Either the delivery id or the event id was already recorded.
                return False

    def recent(self, limit: int = DEFAULT_MAX_BLOCK_EVENTS) -> list[dict]:
        """Newest events first (the why-context block is built from these)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT delivery_id, event_id, subscription_id, seq, type, "
                "payload, received_at FROM deliveries "
                "ORDER BY seq DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [{"delivery_id": r[0], "event_id": r[1],
                 "subscription_id": r[2], "seq": r[3], "type": r[4],
                 "payload": json.loads(r[5]), "received_at": r[6]} for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute(
                "SELECT count(*) FROM deliveries").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()


# ── the app ───────────────────────────────────────────────────────────────

class PilotApp:
    """A memory-aware app whose only memory input is the push stream."""

    def __init__(self, *, secret: str, state_dir: str | Path,
                 expected_subscription_id: str | None = None,
                 tolerance_s: int = SIGNATURE_TOLERANCE_S,
                 max_block_events: int = DEFAULT_MAX_BLOCK_EVENTS,
                 clock=time.time) -> None:
        self.secret = secret
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.expected_subscription_id = expected_subscription_id
        self.tolerance_s = int(tolerance_s)
        self.max_block_events = int(max_block_events)
        self.clock = clock
        self.store = DeliveryStore(self.state_dir / "deliveries.sqlite3")
        self._audit_path = self.state_dir / "received.jsonl"
        self._turns_path = self.state_dir / "turns.jsonl"
        self._lock = threading.Lock()
        self._accepted = 0
        self._duplicates = 0
        self._rejected = 0
        self._turns = 0

    # -- inbound delivery ------------------------------------------------

    def handle_delivery(self, headers, body: bytes,
                        *, now: float | None = None) -> tuple[int, dict]:
        """Verify, dedupe, and record one delivery. Returns ``(status, json)``.

        401 is returned BEFORE any state write: an unverified body is hostile
        input, and a consumer that stores it has already lost (an attacker who
        can POST arbitrary "memory" can steer every later turn).
        """
        signature = header(headers, SIGNATURE_HEADER)
        if not signature:
            return self._reject(401, "missing-signature", headers, body, now)
        if not verify_signature(self.secret, signature, body,
                               tolerance_s=self.tolerance_s,
                               now=None if now is None else int(now)):
            # Covers a tampered body, a wrong secret, and a stale timestamp —
            # `verify_signature` never raises and never says which, so a
            # prober cannot tell a wrong secret from a replayed capture.
            return self._reject(401, "bad-signature", headers, body, now)
        try:
            envelope = json.loads(body.decode("utf-8"))
            event = envelope["event"]
            delivery_id = str(envelope["delivery_id"])
            event_id = str(event["event_id"])
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            return self._reject(400, f"malformed-envelope: {exc}", headers,
                                body, now)
        subscription_id = str(envelope.get("subscription_id") or "")
        if (self.expected_subscription_id
                and subscription_id != self.expected_subscription_id):
            return self._reject(
                403, f"unexpected subscription {subscription_id}", headers,
                body, now)
        # The header and the envelope must agree; a mismatch means one of them
        # was mangled in transit and the dedupe key cannot be trusted.
        if header(headers, DELIVERY_HEADER) not in (None, delivery_id):
            return self._reject(400, "delivery-id header/envelope mismatch",
                                headers, body, now)

        fresh = self.store.record(
            delivery_id=delivery_id, event_id=event_id,
            subscription_id=subscription_id, seq=int(event.get("seq") or 0),
            type_=str(event.get("type") or ""),
            payload=event.get("payload") or {},
            received_at=_iso(now if now is not None else self.clock()))
        with self._lock:
            if fresh:
                self._accepted += 1
            else:
                self._duplicates += 1
        self._audit(status=200, delivery_id=delivery_id, event_id=event_id,
                    duplicate=not fresh, signature=signature, body=body, now=now)
        # 200 on a duplicate too: the delivery WAS handled (idempotently). A
        # 4xx here would make the dispatcher retry a payload it already
        # delivered, and the head-of-line contract would stall the stream.
        return 200, {"ok": True, "duplicate": not fresh,
                     "delivery_id": delivery_id, "event_id": event_id,
                     "injected": fresh}

    def _reject(self, status: int, reason: str, headers, body: bytes,
                now: float | None) -> tuple[int, dict]:
        with self._lock:
            self._rejected += 1
        self._audit(status=status, delivery_id=None, event_id=None,
                    duplicate=None,
                    signature=header(headers, SIGNATURE_HEADER),
                    body=body, now=now, reason=reason)
        return status, {"ok": False, "error": reason}

    def _audit(self, *, status: int, delivery_id, event_id, duplicate,
               signature, body: bytes, now: float | None,
               reason: str | None = None) -> None:
        """Append the request as received (a consumer logs what it got).

        The raw body + signature are kept on purpose: replaying them is how the
        pilot proves a TAMPERED body is refused (``demo.py``), and a real
        consumer keeps the same record for support ("what did you send us?").
        """
        record = {
            "at": _iso(now if now is not None else self.clock()),
            "status": int(status),
            "delivery_id": delivery_id,
            "event_id": event_id,
            "duplicate": duplicate,
            "signature": signature,
            "body": body.decode("utf-8", "replace"),
        }
        if reason:
            record["reason"] = reason
        with self._lock, self._audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # -- injection -------------------------------------------------------

    def memory_block(self) -> str:
        """Render the delivered events as the app's why-context block."""
        events = self.store.recent(self.max_block_events)
        if not events:
            return "(no pushed memory yet)"
        lines = ["## Memory pushed by Tortoise (no polling)"]
        for ev in events:
            payload = json.dumps(ev["payload"], sort_keys=True,
                                 ensure_ascii=False)
            if len(payload) > 200:
                payload = payload[:197] + "..."
            lines.append(f"- {ev['type']} · seq {ev['seq']} · "
                         f"{ev['event_id']} · {ev['received_at']}")
            lines.append(f"  {payload}")
        return "\n".join(lines)

    def next_turn(self, message: str) -> dict:
        """Build the next turn's prompt with the pushed block injected."""
        block = self.memory_block()
        prompt = ("You are a memory-aware assistant. The block below was PUSHED "
                  "to this app by Tortoise; it was not polled for.\n\n"
                  f"{block}\n\n--- user ---\n{message}")
        injected = [ev["delivery_id"] for ev in
                    self.store.recent(self.max_block_events)]
        with self._lock:
            self._turns += 1
            turn = self._turns
            with self._turns_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"turn": turn, "message": message,
                                     "injected": injected,
                                     "at": _iso(self.clock())},
                                    ensure_ascii=False) + "\n")
        return {"turn": turn, "prompt": prompt, "injected": injected}

    def stats(self) -> dict:
        with self._lock:
            return {"accepted": self._accepted, "duplicates": self._duplicates,
                    "rejected": self._rejected, "events": self.store.count(),
                    "turns": self._turns}

    def close(self) -> None:
        self.store.close()


# ── HTTP server ───────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    """``POST <WEBHOOK_PATH>`` + two read-only probes. No poll endpoint exists."""

    server_version = "tortoise-webhook-pilot/1"

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path.split("?")[0] != WEBHOOK_PATH:
            self._respond(404, {"ok": False, "error": "unknown path"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._respond(400, {"ok": False, "error": "bad content-length"})
            return
        body = self.rfile.read(max(0, length))
        status, payload = self.server.app.handle_delivery(self.headers, body)
        self._respond(status, payload)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/healthz":
            self._respond(200, {"ok": True})
        elif path == "/stats":
            self._respond(200, self.server.app.stats())
        else:
            self._respond(404, {"ok": False, "error": "unknown path"})

    def log_message(self, fmt: str, *args) -> None:  # keep test output clean
        return


class PilotServer:
    """A loopback/`--host` HTTP listener for a :class:`PilotApp`."""

    def __init__(self, app: PilotApp, *, host: str = "127.0.0.1",
                 port: int = 8080) -> None:
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.app = app
        self._thread: threading.Thread | None = None

    @property
    def app(self) -> PilotApp:
        return self._httpd.app

    @app.setter
    def app(self, value: PilotApp) -> None:
        """Swap in a fresh app instance (a "restart" on the same state dir)."""
        self._httpd.app = value

    @property
    def host(self) -> str:
        return self._httpd.server_address[0]

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{WEBHOOK_PATH}"

    def start(self) -> PilotServer:
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="pilot-webhook", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> PilotServer:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def serve(app: PilotApp, *, host: str = "127.0.0.1",
          port: int = 8080) -> PilotServer:
    """Build (not start) a listener. Port 0 = ephemeral (tests, demo)."""
    return PilotServer(app, host=host, port=port)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default="./pilot-state",
                       help="where the dedupe store + turn log live")
    parser.add_argument("--secret", default=os.environ.get(
        "TORTOISE_WEBHOOK_SECRET"),
        help="the one-time signing secret from POST /v1/context/subscriptions")
    parser.add_argument("--subscription-id", default=None,
                       help="pin deliveries to this sub_id (403 for others)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if not args.secret:
        parser.error("--secret (or TORTOISE_WEBHOOK_SECRET) is required — it is "
                     "returned ONCE at subscription creation")
    app = PilotApp(secret=args.secret, state_dir=args.state_dir,
                   expected_subscription_id=args.subscription_id)
    with serve(app, host=args.host, port=args.port) as server:
        print(f"pilot listening on {server.url} — state: {app.state_dir}")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print(f"\nstats: {json.dumps(app.stats())}")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised by demo.py
    raise SystemExit(main())

"""#2853 ruling C — the `oauth_clients` row count must be VISIBLE, and never fake.

Ruling C accepted unbounded growth (no prune, no expiry, no cap) and made one
thing part of the decision rather than optional: *"a way to see the number is
part of this ruling, not optional. 'Watch it' is empty unless the count is
observable."* So the failure this file guards is not "the endpoint 500s" — it is
**the endpoint answering with a number that is not the count**, because a wrong
number is worse than no number: it reads as evidence and retires the decision.

Two ways to get a wrong number, both pinned here:

1. ``len(rows)`` instead of the server's ``COUNT``. PostgREST truncates a page at
   the deployment's ``max_rows``, so a page length is a FLOOR that renders exactly
   like a total (the #5388 confusion).
2. Rendering an unknown total as ``0``. ``query_with_total`` returns ``None``
   when the server states no number, and its own contract says callers must fail
   closed on it — ``0`` would read as "nothing has accumulated".

The auth arm is here because the count is fleet-wide operator information, and
``_check_internal`` is the only thing keeping it off a tenant-reachable path.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
import tortoise.supabase_control as sc_mod

_INTERNAL_KEY = "test-internal-shared-secret-2853"
_HEADERS = {"Authorization": f"Bearer {_INTERNAL_KEY}"}
_PATH = "/v1/internal/oauth/clients/count"


@pytest.fixture
def counts(monkeypatch):
    """Substitute the control-plane seam and record how it was called.

    The endpoint imports ``get_control_plane`` locally, so patching it on
    ``tortoise.supabase_control`` (the source module the local import resolves
    from at call time) is what reaches it.
    """
    calls: list[dict] = []
    state: dict = {"rows": [{"id": "c1"}], "total": 4217, "raises": None,
                   "on_loop": None, "thread": None}

    class _CP:
        def query_with_total(self, table, **kwargs):
            calls.append({"table": table, **kwargs})
            # WHERE did the read run? `query_with_total` is a synchronous
            # `httpx.Client.get`, so if it executes on the loop's OWN thread it
            # holds the loop (the #2988/#3498/#3718 class). `get_running_loop()`
            # returns a loop only from the loop's thread — a worker thread
            # raises — which makes this the falsifier for that class.
            try:
                asyncio.get_running_loop()
                state["on_loop"] = True
            except RuntimeError:
                state["on_loop"] = False
            state["thread"] = threading.current_thread().name
            if state["raises"] is not None:
                raise state["raises"]
            return state["rows"], state["total"]

    monkeypatch.setattr(sc_mod, "get_control_plane", lambda *a, **k: _CP())
    state["calls"] = calls
    return state


@pytest.fixture
def client(monkeypatch):
    """A minimal app carrying ONLY this handler, so no app startup/lifespan runs."""
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
    app = FastAPI()
    app.add_api_route(_PATH, ha_mod.oauth_clients_count, methods=["GET"])
    return TestClient(app)


# ── the route exists on the REAL app ─────────────────────────────────────────
def test_the_count_route_is_registered_on_the_served_app():
    """A green test on a minimal app proves nothing if the real one lacks the route."""
    paths = {getattr(r, "path", None) for r in ha_mod.app.routes}
    assert _PATH in paths


# ── the number ───────────────────────────────────────────────────────────────
def test_it_reports_the_servers_own_count_not_the_page_length(client, counts):
    """The whole point: a page of 1 row must still report the full total."""
    r = client.get(_PATH, headers=_HEADERS)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # `rows` has exactly one element; `len(rows)` would answer 1 here.
    assert counts["rows"] == [{"id": "c1"}]
    assert body["count"] == 4217
    assert body["count"] != len(counts["rows"])


def test_it_asks_the_server_for_an_exact_count(client, counts):
    """`count_exact` is what turns PostgREST's `*` into a number — pin the request."""
    client.get(_PATH, headers=_HEADERS)

    assert counts["calls"] == [
        {"table": "oauth_clients", "select": ["id"], "limit": 1,
         "count_exact": True}
    ]


# ── where the read runs ──────────────────────────────────────────────────────
def test_the_read_runs_off_the_event_loop(client, counts):
    """A synchronous `httpx.Client.get` must not hold the process's single loop.

    `hosted_api` runs ONE uvicorn worker (`Dockerfile.hosted`, no `--workers`),
    so an inline control-plane round trip stalls EVERY concurrent request for
    up to the client's 5 s timeout — the shape #2988 measured ("a 1.00 s read
    produced a 1.00 s loop gap"). Non-vacuity: putting the call back inline in
    the `async def` body makes the stub observe a running loop and reds this.
    """
    r = client.get(_PATH, headers=_HEADERS)

    assert r.status_code == 200
    assert counts["on_loop"] is False, (
        "the control-plane read ran ON the event loop "
        f"(thread={counts['thread']}) — every concurrent request waits for it"
    )


@pytest.mark.parametrize("undelivered", [None, "OFFLOAD_REFUSED"])
def test_an_offload_that_never_ran_the_read_is_not_a_count(
        client, counts, monkeypatch, undelivered):
    """The seam can fail to deliver (wait bound missed / pool refused).

    "We could not ask" is not a number: the body must stay `unavailable` with
    no `count`, exactly like a read that raised — otherwise a bound miss
    renders as a confident zero and retires the ruling it exists to inform.
    """
    refused = (ha_mod.OFFLOAD_REFUSED if undelivered == "OFFLOAD_REFUSED"
               else None)

    async def _undelivered(fn, **kwargs):
        return refused

    monkeypatch.setattr(ha_mod, "_cp_offload", _undelivered)

    r = client.get(_PATH, headers=_HEADERS)

    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "unavailable"
    assert "count" not in body
    assert counts["calls"] == [], "the read never ran, so there is no number"


# ── the absence, which must NOT look like zero ───────────────────────────────
def test_an_unstated_total_is_unavailable_and_carries_no_count(client, counts):
    """`None` is "the server did not say", never "0 rows"."""
    counts["total"] = None

    r = client.get(_PATH, headers=_HEADERS)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unavailable"
    assert "count" not in body, (
        "an unstated total must not be rendered as a count; a confident 0 "
        "would read as 'nothing has accumulated' and retire the ruling"
    )
    assert body["reason"]


def test_a_failed_read_is_unavailable_and_carries_no_count(client, counts):
    counts["raises"] = RuntimeError("control plane unreachable")

    r = client.get(_PATH, headers=_HEADERS)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unavailable"
    assert "count" not in body
    assert "RuntimeError" in body["reason"]


def test_a_failed_read_is_logged_with_the_cause(client, counts, caplog):
    """The body carries only the exception TYPE; the log must carry the detail.

    `query_with_total` raises a RuntimeError whose message holds the real cause
    (e.g. `HTTP 503`). If the unavailable path logged nothing, an incident would
    leave the fleet with a surface that says "unavailable" and no trace.
    """
    counts["raises"] = RuntimeError("HTTP 503 from PostgREST")

    with caplog.at_level("WARNING", logger="tortoise.hosted_api"):
        client.get(_PATH, headers=_HEADERS)

    hits = [r for r in caplog.records if "oauth clients count" in r.getMessage()]
    assert hits, "the unavailable path must leave a server-side trace"
    assert hits[0].levelname == "WARNING"
    info = hits[0].exc_info
    # NOT `is not None`: `LogRecord.__init__` stores `exc_info` VERBATIM (the
    # normalisation to a tuple happens later, in `Logger._log`, and only for a
    # truthy value), so `exc_info=False` stores `False` — and `False is not
    # None` is True. That form of this assertion passes with the traceback
    # dropped, which is the one regression it exists to catch.
    assert isinstance(info, tuple) and info[0] is RuntimeError, (
        "the traceback carries the cause the HTTP body deliberately omits"
    )


# ── the decision travels WITH the number ─────────────────────────────────────
@pytest.mark.parametrize("unavailable", [False, True])
def test_the_ruling_and_its_revisit_triggers_ride_along(client, counts,
                                                        unavailable):
    """The acceptance is not open-ended: the reader gets the triggers, always."""
    counts["total"] = None if unavailable else 4217

    body = client.get(_PATH, headers=_HEADERS).json()

    assert len(body["revisit_triggers"]) == 2
    assert any("2026-11-15" in t for t in body["revisit_triggers"])
    assert any("Dynamic Client Registration" in t
               for t in body["revisit_triggers"])


def test_the_ok_body_names_the_ruling_it_is_serving(client, counts):
    body = client.get(_PATH, headers=_HEADERS).json()

    assert "#2853" in body["ruling"]
    assert "no prune" in body["ruling"]
    assert body["table"] == "oauth_clients"


# ── auth ─────────────────────────────────────────────────────────────────────
def test_a_wrong_key_is_rejected(client, counts):
    r = client.get(_PATH, headers={"Authorization": "Bearer wrong"})

    assert r.status_code == 401
    assert counts["calls"] == [], "auth must be checked before the read"


def test_an_absent_key_is_rejected(client, counts):
    assert client.get(_PATH).status_code == 401
    assert counts["calls"] == []


def test_an_unconfigured_internal_key_fails_closed(client, counts, monkeypatch):
    """No key configured is 503 — never an unauthenticated fleet count."""
    monkeypatch.delenv("FASTAPI_INTERNAL_KEY", raising=False)

    assert client.get(_PATH, headers=_HEADERS).status_code == 503
    assert counts["calls"] == []

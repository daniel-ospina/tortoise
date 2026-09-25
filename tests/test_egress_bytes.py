"""#4491 — egress (response bytes) per org: the missing network cost dimension.

Before this change the hosted API accounted for INBOUND bytes only, and only as
a DoS cap (the manifest route's ``content-length`` guard) — a safety bound, not
a cost metric. Nothing counted OUTBOUND bytes, so a read-heavy org's network
cost (retrieval/ask result sets, graph read, export) had no signal at all while
reads are free by decision (``product/pricing.json`` -> ``billing.reads_free``).

This file pins the four properties the issue's indicators ask for, plus the two
that rot silently:

* **per-org attributed bytes** exist and accumulate (indicator 1);
* the transport-level wrapper covers the **large-payload read paths** — REST and
  a mounted sub-app — so no route is born unmeasured (indicator 2);
* the figure is **readable without a dashboard** (``egress_bytes_by_org()`` and
  the existing ``/metrics`` exposition; indicator 3);
* a **size distribution** exists (indicator 1's "at minimum" arm);
* **the production call site is asserted**, not assumed — the #4493 lesson: a
  registered-but-zero metric reads as a measurement. ``test_wiring`` asserts the
  middleware is installed on the real app AND that a real request through the
  real stack records its exact response size;
* **both labels are bounded** — the metric is request-derived, so a client
  walking unknown paths (or a fleet of orgs) must not be able to grow the
  Prometheus child set without bound.
"""
from __future__ import annotations

import asyncio
import pathlib
import time

import pytest
from fastapi import FastAPI, Request, Response
from prometheus_client import generate_latest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from tortoise import hosted_api as ha
from tortoise import mcp_auth as ma
from tortoise import monitoring

# ── helpers ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_egress():
    """Every test starts and ends from an empty egress registry.

    The cap registries are process-global, so a leaked admission in one test
    would change another test's overflow behaviour.
    """
    monitoring._reset_egress()
    yield
    monitoring._reset_egress()


def _bytes_by_org_path() -> dict[tuple[str, str], int]:
    """The ``(org, path)`` children of ``EGRESS_BYTES``, from the metric itself."""
    out: dict[tuple[str, str], int] = {}
    for family in monitoring.EGRESS_BYTES.collect():
        for sample in family.samples:
            if (not sample.name.endswith("_total")
                    or sample.name.endswith("_created_total")):
                continue
            out[(sample.labels["org"], sample.labels["path"])] = int(sample.value)
    return out


def _histogram(path_label: str) -> tuple[int, int]:
    """``(count, sum)`` of the response-size histogram for one route class."""
    for family in monitoring.EGRESS_RESPONSE_BYTES.collect():
        count = total = 0
        matched = False
        for sample in family.samples:
            if sample.labels.get("path") != path_label:
                continue
            matched = True
            if sample.name.endswith("_count"):
                count = int(sample.value)
            elif sample.name.endswith("_sum"):
                total = int(sample.value)
        if matched:
            return count, total
    return 0, 0


def _app_with_payload(payload: bytes, *, org: str | None, route: str = "/v1/thing/{tid}"):
    """A minimal FastAPI app whose handler sets ``request.state.org_id``.

    ``request.state`` IS ``scope["state"]`` in Starlette, which is the exact
    seam the middleware reads — so this exercises the real attribution path,
    not a stubbed one.
    """
    app = FastAPI()

    @app.get(route)
    def _handler(tid: str, request: Request):
        if org is not None:
            request.state.org_id = org
        return Response(content=payload, media_type="application/octet-stream")

    app.add_middleware(ha.EgressBytesMiddleware)
    return app


# ── the writer: attribution, cardinality, readability ─────────────────────


class TestWriter:
    def test_bytes_accumulate_per_org_and_route(self):
        monitoring.record_egress("org_a", "/v1/points/{pid}", 100)
        monitoring.record_egress("org_a", "/v1/points/{pid}", 50)
        monitoring.record_egress("org_b", "/v1/export", 7)

        assert monitoring.egress_bytes_by_org() == {"org_a": 150, "org_b": 7}
        assert _bytes_by_org_path() == {
            ("org_a", "/v1/points/{pid}"): 150,
            ("org_b", "/v1/export"): 7,
        }

    def test_unresolved_org_is_the_unattributed_child_not_an_invented_org(self):
        monitoring.record_egress(None, "/v1/version", 11)
        monitoring.record_egress("", "/v1/version", 4)

        assert monitoring.egress_bytes_by_org() == {"": 15}
        assert ("", "/v1/version") in _bytes_by_org_path()

    def test_negative_bytes_are_clamped_not_subtracted(self):
        """A negative increment would corrupt a monotonic counter."""
        monitoring.record_egress("org_a", "/v1/x", -5)
        monitoring.record_egress("org_a", "/v1/x", 3)

        assert monitoring.egress_bytes_by_org() == {"org_a": 3}

    def test_zero_byte_response_is_recorded(self):
        """A measured 0 is a measurement — the route responded and sent nothing.

        It must still create the child, or "is this route measured?" cannot be
        answered from the metric.
        """
        monitoring.record_egress("org_a", "/v1/x", 0)

        assert monitoring.egress_bytes_by_org() == {"org_a": 0}

    def test_org_cardinality_is_capped_into_one_overflow_child(self):
        for i in range(monitoring.EGRESS_MAX_ORGS):
            monitoring.record_egress(f"org_{i}", "/v1/x", 1)
        monitoring.record_egress("org_one_too_many", "/v1/x", 7)

        totals = monitoring.egress_bytes_by_org()
        assert "org_one_too_many" not in totals
        assert totals[monitoring.EGRESS_OVERFLOW] == 7
        assert len(totals) == monitoring.EGRESS_MAX_ORGS + 1

    def test_path_cardinality_is_capped_into_one_overflow_child(self):
        for i in range(monitoring.EGRESS_MAX_PATHS):
            monitoring.record_egress("org_a", f"/v1/unknown-{i}", 1)
        monitoring.record_egress("org_a", "/v1/unknown-overflow", 9)

        assert ("org_a", "/v1/unknown-overflow") not in _bytes_by_org_path()
        assert _bytes_by_org_path()[("org_a", monitoring.EGRESS_OVERFLOW)] == 9

    def test_admitted_labels_are_stable_once_past_the_cap(self):
        """The cap must not evict: an admitted org keeps its own child forever."""
        for i in range(monitoring.EGRESS_MAX_ORGS):
            monitoring.record_egress(f"org_{i}", "/v1/x", 1)
        monitoring.record_egress("org_0", "/v1/x", 100)
        monitoring.record_egress("org_late", "/v1/x", 1)

        assert monitoring.egress_bytes_by_org()["org_0"] == 101

    def test_reset_forgets_admitted_labels_and_series(self):
        monitoring.record_egress("org_a", "/v1/x", 5)
        monitoring._reset_egress()

        assert monitoring.egress_bytes_by_org() == {}
        monitoring.record_egress("org_a", "/v1/x", 3)
        assert monitoring.egress_bytes_by_org() == {"org_a": 3}

    def test_histogram_carries_the_size_distribution(self):
        monitoring.record_egress("org_a", "/v1/export", 70000)
        monitoring.record_egress("org_a", "/v1/export", 20)

        assert _histogram("/v1/export") == (2, 70020)
        # The distribution is per route class, NOT per org: the org does not
        # change an answer to "is this route returning 10 MB?".
        assert _histogram("/v1/points/{pid}") == (0, 0)

    def test_metric_is_in_the_existing_prometheus_exposition(self):
        """Indicator 3: readable from the metric surface that already exists."""
        monitoring.record_egress("org_a", "/v1/points/{pid}", 42)

        text = generate_latest().decode()
        assert (
            'tortoise_egress_bytes_total{org="org_a",path="/v1/points/{pid}"} 42.0'
            in text
        ), "per-org egress is not in the /metrics exposition"
        assert "tortoise_egress_response_bytes_bucket" in text

    def test_record_egress_is_the_only_writer_of_the_metric(self):
        """The #501/#3677 house shape: callers never touch the metric directly.

        A second writer would fork the unit, the attribution key and the
        cardinality cap — the exact drift #4491's shape note forbids.
        """
        root = pathlib.Path(monitoring.__file__).resolve().parent
        offenders = []
        for source in sorted(root.glob("*.py")):
            if source.name == "monitoring.py":
                continue
            text = source.read_text(encoding="utf-8")
            if "EGRESS_BYTES" in text or "EGRESS_RESPONSE_BYTES" in text:
                offenders.append(source.name)
        assert not offenders, (
            "egress metrics must be written only through monitoring.record_egress; "
            f"direct references found in {offenders}"
        )


# ── the middleware: coverage, attribution, and the drop path ──────────────


class TestMiddleware:
    def test_counts_exact_payload_bytes_by_route_template(self):
        payload = b"x" * 4321
        client = TestClient(_app_with_payload(payload, org="org_a"))

        r = client.get("/v1/thing/abc")

        assert r.status_code == 200
        assert r.content == payload
        assert monitoring.egress_bytes_by_org() == {"org_a": len(payload)}
        # The label is the ROUTE TEMPLATE, not the raw path.
        assert ("org_a", "/v1/thing/{tid}") in _bytes_by_org_path()

    def test_two_ids_collapse_to_one_child(self):
        """A route template label is what keeps cardinality bounded per route."""
        client = TestClient(_app_with_payload(b"abc", org="org_a"))

        client.get("/v1/thing/one")
        client.get("/v1/thing/two")

        assert _bytes_by_org_path() == {("org_a", "/v1/thing/{tid}"): 6}

    def test_attribution_is_per_org_not_global(self):
        first = TestClient(_app_with_payload(b"aaaa", org="org_a"))
        second = TestClient(_app_with_payload(b"bb", org="org_b"))

        first.get("/v1/thing/x")
        second.get("/v1/thing/x")

        assert monitoring.egress_bytes_by_org() == {"org_a": 4, "org_b": 2}

    def test_streamed_body_is_summed_across_chunks(self):
        async def _streaming(scope, receive, send):
            scope.setdefault("state", {})["org_id"] = "org_stream"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"12345", "more_body": True})
            await send({"type": "http.response.body", "body": b"6789", "more_body": False})

        app = ha.EgressBytesMiddleware(_streaming)
        client = TestClient(app)  # no context manager: this raw ASGI app has no lifespan
        assert client.get("/stream").content == b"123456789"

        assert monitoring.egress_bytes_by_org() == {"org_stream": 9}

    def test_unrouted_path_falls_back_to_a_bounded_two_segment_label(self):
        """A 404 has no route template; the fallback must still be bounded."""
        async def _not_found(scope, receive, send):
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})

        app = ha.EgressBytesMiddleware(_not_found)
        client = TestClient(app)  # no context manager: this raw ASGI app has no lifespan
        client.get("/nope/12345")
        client.get("/nope/67890")

        # Numeric ids collapse, so two unknown ids are ONE child.
        assert _bytes_by_org_path() == {("", "/nope/{id}"): 0}

    def test_fallback_keeps_literal_segments_and_bounds_length(self):
        async def _app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        app = ha.EgressBytesMiddleware(_app)
        client = TestClient(app)  # no context manager: this raw ASGI app has no lifespan
        client.get("/v1/version")
        client.get("/" + "z" * 500 + "/" + "y" * 500)

        labels = {path for _, path in _bytes_by_org_path()}
        assert "/v1/version" in labels, "`v1` is a literal, not an id"
        over_long = [lab for lab in labels if len(lab) > ha._EGRESS_MAX_LABEL_LEN]
        assert not over_long, f"unbounded fallback label: {over_long!r}"

    def test_mounted_sub_app_is_counted_and_attributed_by_its_own_path(self):
        """The large-payload read paths include mounted surfaces (the MCP mount).

        Plain Starlette routes do NOT stamp ``scope["route"]`` (measured), so
        this is also the coverage proof for the normalised fallback.
        """
        sub = Starlette(routes=[Route("/export", lambda request: JSONResponse({"data": "d" * 300}))])
        app = Starlette(routes=[Mount("/mcp", app=sub)])
        app.add_middleware(ha.EgressBytesMiddleware)

        with TestClient(app) as client:
            r = client.get("/mcp/export")

        assert r.status_code == 200
        assert ("", "/mcp/export") in _bytes_by_org_path()
        assert monitoring.egress_bytes_by_org()[""] == len(r.content)

    def test_response_the_bound_dropped_is_not_credited(self, monkeypatch):
        """#3834 interaction: on a breach the bound DROPS the late response.

        Bytes that never left must not be credited to the org. The drop is
        invisible in ``send`` (``_guarded_send`` returns normally), so the
        middleware reads the bound's published flag instead — this test fails
        if that read is removed.
        """
        monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", 0.05)

        async def _slow(scope, receive, send):
            if scope["type"] != "http":  # the TestClient's lifespan scope
                return
            scope.setdefault("state", {})["org_id"] = "org_slow"
            await asyncio.sleep(0.3)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"z" * 4096})

        # Built by hand (no router, no `app.router` surgery): the bound OUTSIDE
        # the counter is exactly the production nesting this test must exercise.
        app = ha.WaitBoundMiddleware(ha.EgressBytesMiddleware(_slow))

        client = TestClient(app)  # no context manager: this raw ASGI stack has no lifespan
        r = client.get("/slow")
        assert r.status_code == 504
        # Let the ABANDONED handler finish (the bound never cancels it), or the
        # assertion would pass for the wrong reason — nothing recorded yet.
        deadline = time.monotonic() + 5
        while ha._pending_wait_bound_requests and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not ha._pending_wait_bound_requests, "abandoned handler never drained"

        assert monitoring.egress_bytes_by_org() == {}, (
            "the bound dropped this response — its bytes must not be egress")

    def test_accounting_failure_never_fails_the_request(self, monkeypatch):
        """Measurement must not become a new failure mode for the request."""
        payload = b"still served"

        def _boom(*args, **kwargs):
            raise RuntimeError("metrics backend exploded")

        monkeypatch.setattr(monitoring, "record_egress", _boom)
        client = TestClient(_app_with_payload(payload, org="org_a"))

        r = client.get("/v1/thing/x")

        assert r.status_code == 200
        assert r.content == payload

    def test_non_http_scope_is_passed_through_untouched(self):
        seen = []

        async def _inner(scope, receive, send):
            seen.append(scope["type"])

        app = ha.EgressBytesMiddleware(_inner)
        asyncio.run(app({"type": "lifespan"}, None, None))

        assert seen == ["lifespan"]


# ── wiring on the real app (the #4493 lesson) ─────────────────────────────


class TestWiring:
    def test_middleware_is_installed_inside_the_bound_and_the_gauge(self):
        classes = [m.cls for m in ha.app.user_middleware]
        assert ha.EgressBytesMiddleware in classes, (
            "EgressBytesMiddleware is not installed — the metric is registered "
            "but never written, which reads as a measurement")
        egress = classes.index(ha.EgressBytesMiddleware)
        # Order is REQUIRED, not incidental: the bound must stay outermost
        # (#3834) and the gauge immediately inside it (#2850), and egress must
        # sit INSIDE the bound so a dropped response is not credited.
        assert classes[0] is ha.WaitBoundMiddleware
        assert classes[1] is ha.InFlightMiddleware
        assert egress > classes.index(ha.WaitBoundMiddleware)
        assert egress > classes.index(ha.InFlightMiddleware)

    def test_real_app_request_records_its_response_bytes(self):
        """A real request through the real stack, with nothing stubbed.

        ``/v1/version`` is the one public, DB-free PRODUCT route (``/v1/``, not
        the ``/metrics`` handler the pre-#4489 histogram timed), so this is the
        end-to-end proof that the product surface is measured — and that the
        label is the route template.
        """
        with TestClient(ha.app) as client:
            version = client.get("/v1/version")
            health = client.get("/health")

        assert version.status_code == 200 and health.status_code == 200
        assert _bytes_by_org_path()[("", "/v1/version")] == len(version.content)
        assert monitoring.egress_bytes_by_org().get("") == (
            len(version.content) + len(health.content)), (
            "the real app did not record its own response sizes: "
            f"{monitoring.egress_bytes_by_org()!r}")

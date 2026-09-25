"""#4491 — egress (response bytes) per org: the missing network cost dimension.

Before this change the hosted API accounted for INBOUND bytes only, and only as
a DoS cap (the manifest route's ``content-length`` guard) — a safety bound, not
a cost metric. Nothing counted OUTBOUND bytes, so a read-heavy org's network
cost (retrieval/ask result sets, graph read, export) had no signal at all while
reads are free by decision (``product/pricing.json`` -> ``billing.reads_free``).

This file pins the four properties the issue's indicators ask for, plus the ones
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
* **the labels are bounded** — the metric is request-derived, so a client
  walking unknown paths (or a fleet of orgs) must not be able to grow the
  Prometheus child set without bound;
* **the two provenances cannot mix** — a request-derived label and a code-literal
  route label that happen to be the same STRING must not share a series (the
  histogram carries no org, so mixing there is cross-tenant), and a code-literal
  surface (a mount prefix) must not be starvable by unrelated junk, which is
  what the two axes plus the ``origin`` label and the declared prefixes buy.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import threading
import time

import pytest
from fastapi import FastAPI, Request, Response
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from tortoise import hosted_api as ha
from tortoise import mcp_auth as ma
from tortoise import monitoring

ORIGIN_ROUTE = "route"
ORIGIN_DERIVED = "derived"

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


def _bytes_by_child() -> dict[tuple[str, str, str], int]:
    """``(org, path, origin)`` children of ``EGRESS_BYTES``, from the metric.

    ``origin`` is part of the key deliberately: the whole point of the axis is
    that the same ``(org, path)`` may exist twice, once per provenance, and a
    helper that merged them would hide exactly the bug the axis prevents.
    """
    out: dict[tuple[str, str, str], int] = {}
    for family in monitoring.EGRESS_BYTES.collect():
        for sample in family.samples:
            if (not sample.name.endswith("_total")
                    or sample.name.endswith("_created_total")):
                continue
            labels = sample.labels
            out[(labels["org"], labels["path"], labels["origin"])] = int(sample.value)
    return out


def _histogram(path_label: str, origin: str = ORIGIN_ROUTE) -> tuple[int, int]:
    """``(count, sum)`` of the response-size histogram for one route class."""
    for family in monitoring.EGRESS_RESPONSE_BYTES.collect():
        count = total = 0
        matched = False
        for sample in family.samples:
            labels = sample.labels
            if labels.get("path") != path_label or labels.get("origin") != origin:
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
        assert _bytes_by_child() == {
            ("org_a", "/v1/points/{pid}", ORIGIN_ROUTE): 150,
            ("org_b", "/v1/export", ORIGIN_ROUTE): 7,
        }

    def test_unresolved_org_is_the_unattributed_child_not_an_invented_org(self):
        monitoring.record_egress(None, "/v1/version", 11)
        monitoring.record_egress("", "/v1/version", 4)

        assert monitoring.egress_bytes_by_org() == {"": 15}
        assert ("", "/v1/version", ORIGIN_ROUTE) in _bytes_by_child()

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

        children = _bytes_by_child()
        assert ("org_a", "/v1/unknown-overflow", ORIGIN_ROUTE) not in children
        assert children[("org_a", monitoring.EGRESS_OVERFLOW, ORIGIN_ROUTE)] == 9

    def test_derived_path_cardinality_folds_into_one_fixed_child(self):
        """Request-derived labels have their OWN, much smaller budget."""
        for i in range(monitoring.EGRESS_MAX_DERIVED_PATHS):
            monitoring.record_egress("org_a", f"/scan-{i}", 1, derived=True)
        monitoring.record_egress("org_a", "/scan-overflow", 9, derived=True)

        children = _bytes_by_child()
        assert ("org_a", "/scan-overflow", ORIGIN_DERIVED) not in children
        assert ("org_a", monitoring.EGRESS_UNROUTED, ORIGIN_DERIVED) in children
        paths = {path for _, path, origin in children if origin == ORIGIN_DERIVED}
        assert len(paths) == monitoring.EGRESS_MAX_DERIVED_PATHS + 1

    def test_request_derived_labels_cannot_starve_route_templates(self):
        """The round-1 review finding, pinned (bug + security reviewers, converged).

        The live app already carries 121 route templates, so ONE shared path
        budget of 128 left ~7 slots: an unauthenticated client could fill them
        with distinct unknown paths and fold real routes — and the size
        histogram — into ``__other__`` for the whole process. The two axes are
        now separate budgets, and this asserts the separation from the side a
        client can actually reach.
        """
        for i in range(monitoring.EGRESS_MAX_PATHS + 50):
            monitoring.record_egress("org_a", f"/scan-{i}", 1, derived=True)

        monitoring.record_egress("org_a", "/v1/retrieval/{rid}", 500)

        assert _bytes_by_child()[
            ("org_a", "/v1/retrieval/{rid}", ORIGIN_ROUTE)] == 500, (
            "request-derived volume displaced a code-literal route template")

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

    def test_derived_label_never_writes_into_a_template_series(self):
        """The round-2 review finding, pinned (bug + security, converged).

        The budgets were separate but the label STRING was the series key, so a
        request-derived label equal to a route template (`/v1/version` is what an
        unmatched `/v1/version/<junk>` normalises to) wrote its bytes into that
        template's counter — and into its histogram, which carries no org label
        at all. The ``origin`` axis makes the same string two distinct children.
        """
        monitoring.record_egress("org_a", "/v1/version", 100)
        monitoring.record_egress("", "/v1/version", 22, derived=True)

        children = _bytes_by_child()
        assert children[("org_a", "/v1/version", ORIGIN_ROUTE)] == 100, (
            "a request-derived label added bytes to a template's series")
        assert children[("", "/v1/version", ORIGIN_DERIVED)] == 22

        assert _histogram("/v1/version", ORIGIN_ROUTE) == (1, 100), (
            "a request-derived observation entered a template's distribution")
        assert _histogram("/v1/version", ORIGIN_DERIVED) == (1, 22)

    def test_metric_is_in_the_existing_prometheus_exposition(self):
        """Indicator 3: readable from the metric surface that already exists.

        Parsed, not string-matched: the exposition orders labels canonically
        (alphabetically), so a literal substring would pin the ORDER rather
        than the content.
        """
        monitoring.record_egress("org_a", "/v1/points/{pid}", 42)
        monitoring.record_egress("", "/nope", 3, derived=True)

        text = generate_latest().decode()
        assert "tortoise_egress_response_bytes_bucket" in text

        samples: list[tuple[dict[str, str], float]] = []
        for family in text_string_to_metric_families(text):
            if family.name != "tortoise_egress_bytes":
                continue
            samples.extend((s.labels, s.value) for s in family.samples)

        assert ({"org": "org_a", "path": "/v1/points/{pid}", "origin": "route"},
                42.0) in samples, (
            f"per-org egress is not in the /metrics exposition: {samples!r}")
        assert ({"org": "", "path": "/nope", "origin": "derived"}, 3.0) in samples, (
            "the provenance axis is not readable from the exposition")

    def test_unencodable_label_is_repaired_so_metrics_still_serve(self):
        """A lone surrogate would make ``generate_latest()`` raise forever.

        ``/metrics`` encodes label values, and the handler returns 500 on a
        raise — so ONE unencodable label would blind every alert in the process,
        not just this dimension. The single writer is the place that can prevent
        it. (Not reachable from HTTP today: uvicorn replaces invalid bytes and
        org ids are charset-validated.)
        """
        monitoring.record_egress("org_a", "/v1/\ud800bad", 1)

        text = generate_latest().decode()  # must not raise
        assert "tortoise_egress_bytes_total" in text
        assert ("org_a", "/v1/?bad", ORIGIN_ROUTE) in _bytes_by_child()

    def test_org_overflow_sentinel_cannot_be_produced_by_the_id_generators(self):
        """The org sentinel must not be reachable as a real org id.

        Org ids are GENERATED, not chosen: the provisioning lanes use
        ``uuid4().hex[:26]`` and the registry lane ``org_<name>``. This asserts
        the sentinel is outside both shapes, so changing it to a colliding value
        (``org_overflow``) would fail here.
        """
        assert not re.fullmatch(r"[0-9a-f]{26}", monitoring.EGRESS_OVERFLOW)
        assert not monitoring.EGRESS_OVERFLOW.startswith("org_")

    def test_control_characters_are_stripped_from_labels(self):
        """A percent-decoded path carries control characters into the exposition.

        ``prometheus_client`` escapes only ``\\``, ``\n`` and ``"``, so CR, NUL
        and ESC would ride into every series of the family — the ``*_bucket``
        lines included. The class is the one ``mcp_auth._sanitize_for_log``
        covers, which is why the C1 range and the Unicode line separators are
        asserted here too: a NEL or U+2028 in a label adds PHYSICAL lines to a
        ``splitlines()``-based reader (measured).
        """
        monitoring.record_egress("", "/a\r\x00b", 5, derived=True)
        monitoring.record_egress("", "/c\x85d", 5, derived=True)
        monitoring.record_egress("", "/e\u2028f", 5, derived=True)

        children = _bytes_by_child()
        for expected in ("/a??b", "/c?d", "/e?f"):
            assert ("", expected, ORIGIN_DERIVED) in children, (
                f"control characters survived into a label: {sorted(children)!r}")
        text = generate_latest().decode()
        for bad in ("\x00", "\r", "\x85", "\u2028", "\u2029"):
            assert bad not in text

    def test_admission_rechecks_inside_the_lock(self):
        """The in-lock re-check keeps ONE logical label on ONE child.

        The window is between the lock-free fast path and the lock: a label
        admitted by another thread in that window must be returned as itself,
        not folded to overflow — otherwise a label could end up with two
        children. Driven deterministically with a registry that reports a known
        label absent ONCE (a lying ``__contains__``), which is exactly the race
        shape; without the re-check this returns overflow.
        """
        class _RacySet(set):
            lies = 0

            def __contains__(self, item):
                if self.lies:
                    self.lies -= 1
                    return False
                return super().__contains__(item)

        seen = _RacySet()
        seen.add("/known")
        seen.lies = 1

        assert monitoring._admit_egress_label(
            "/known", seen, 0, overflow="__overflow__") == "/known"

    def test_unrouted_sentinel_is_not_a_reachable_label(self):
        """The derived overflow child must not be a label a client can request.

        Otherwise ``GET /__unrouted__`` pre-occupies the very bucket routine
        folding writes into, and an operator cannot tell folded traffic from a
        client-chosen path. It is unproducible BY CONSTRUCTION: the fallback
        always emits a leading ``/``.
        """
        literal_path = "/" + monitoring.EGRESS_UNROUTED
        monitoring.record_egress("", literal_path, 5, derived=True)

        children = _bytes_by_child()
        assert children[("", literal_path, ORIGIN_DERIVED)] == 5, (
            "a request for the sentinel path must be an ordinary derived label")
        assert ("", monitoring.EGRESS_UNROUTED, ORIGIN_DERIVED) not in children

    def test_concurrent_admissions_stop_at_the_cap(self):
        """The cap must hold under CONCURRENCY, not just in one thread.

        The hot path is lock-free for an admitted label and takes the lock only
        to admit, so a burst of simultaneous first-sightings is the shape that
        would expose a cap check applied outside the lock. (The in-lock RE-check
        is about series stability, not the cap — it is pinned separately by
        ``test_admission_rechecks_inside_the_lock``.)
        """
        def _spray(base: int) -> None:
            for i in range(200):
                monitoring.record_egress("", f"/t{base}-{i}", 1, derived=True)

        threads = [threading.Thread(target=_spray, args=(t,)) for t in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        derived = {path for _, path, origin in _bytes_by_child()
                   if origin == ORIGIN_DERIVED}
        assert len(derived) == monitoring.EGRESS_MAX_DERIVED_PATHS + 1
        assert monitoring.EGRESS_UNROUTED in derived

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
        assert ("org_a", "/v1/thing/{tid}", ORIGIN_ROUTE) in _bytes_by_child()

    def test_two_ids_collapse_to_one_child(self):
        """A route template label is what keeps cardinality bounded per route."""
        client = TestClient(_app_with_payload(b"abc", org="org_a"))

        client.get("/v1/thing/one")
        client.get("/v1/thing/two")

        assert _bytes_by_child() == {("org_a", "/v1/thing/{tid}", ORIGIN_ROUTE): 6}

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
        assert _bytes_by_child() == {("", "/nope/{id}", ORIGIN_DERIVED): 0}

    def test_fallback_keeps_literal_segments_and_bounds_length(self):
        """The fallback is bounded BY CONSTRUCTION — assert the real bound.

        An over-long segment is truncated to ``_EGRESS_MAX_SEGMENT_LEN`` and at
        most two segments are kept, so the longest possible label is exactly
        ``/`` + 24 + ``/`` + 24. (Asserting an overall cap that the segment cap
        already implies would be unfalsifiable.)
        """
        async def _app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        app = ha.EgressBytesMiddleware(_app)
        client = TestClient(app)  # no context manager: this raw ASGI app has no lifespan
        client.get("/v1/version")
        client.get("/" + "z" * 500 + "/" + "y" * 500)

        labels = {path for _, path, _ in _bytes_by_child()}
        assert "/v1/version" in labels, "`v1` is a literal, not an id"
        longest = "/" + "z" * ha._EGRESS_MAX_SEGMENT_LEN + "/" + "y" * ha._EGRESS_MAX_SEGMENT_LEN
        assert longest in labels, f"over-long segments were not truncated: {labels!r}"
        assert max(len(lab) for lab in labels) == len(longest)

    def test_unrouted_traffic_through_the_middleware_cannot_starve_templates(self):
        """The attacker side is end-to-end; the template side is a direct write.

        Real unauthenticated 404 requests through the middleware's own fallback
        path fill the DERIVED budget — that is the side a client can reach. The
        route template is then recorded through the single writer, which is the
        only way to add one (nothing a client sends can create a template).
        """
        async def _not_found(scope, receive, send):
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})

        app = ha.EgressBytesMiddleware(_not_found)
        client = TestClient(app)  # no context manager: raw ASGI app, no lifespan
        for i in range(monitoring.EGRESS_MAX_PATHS + 20):
            client.get(f"/scan{i}x/y")

        monitoring.record_egress("org_real", "/v1/export/{rid}", 1234)

        assert _bytes_by_child()[("org_real", "/v1/export/{rid}", ORIGIN_ROUTE)] == 1234

    def test_mounted_surface_is_a_declared_route_label(self):
        """The large-payload read paths include mounted surfaces (the MCP mount).

        A mounted sub-app must be attributed to the SURFACE the client hit, not
        to the sub-app's own route: on the derived axis the sub-route label
        (`/export`) would be shared by any two mounts with the same inner path.
        A mount prefix is a code literal, so it is declared (see
        ``ha._EGRESS_DECLARED_PREFIXES``) and admitted as a ROUTE-axis label —
        which is also what makes it un-starvable (next test).

        Run against the REAL router, not a hand-forged scope: whether Starlette
        stamps ``scope["route"]`` with the sub-app's inner route is a version
        detail (1.6.0 does not, 1.7.0 does — measured), and CI runs the second.
        """
        sub = Starlette(routes=[Route("/export", lambda request: JSONResponse({"data": "d" * 300}))])
        app = Starlette(routes=[Mount("/mcp", app=sub)])
        app.add_middleware(ha.EgressBytesMiddleware)

        with TestClient(app) as client:
            r = client.get("/mcp/export")

        assert r.status_code == 200
        assert ("", "/mcp", ORIGIN_ROUTE) in _bytes_by_child()
        assert not [c for c in _bytes_by_child() if c[1] in ("/export", "/mcp/export")], (
            "the mounted request must be labelled by the declared prefix only")
        assert monitoring.egress_bytes_by_org()[""] == len(r.content)

    def test_declared_mount_prefix_survives_an_unrelated_junk_flood(self):
        """The round-2 review finding, pinned (bug + security, converged).

        Mounted surfaces used to live on the request-derived axis, so eight
        cheap 404s on UNRELATED paths filled its budget and folded every later
        ``/mcp`` response into ``/__unrouted__`` for the process lifetime. The
        prefix is a code literal and now sits on the route axis: unrelated junk
        cannot reach it.
        """
        sub = Starlette(routes=[Route("/export", lambda request: JSONResponse({"data": "d" * 300}))])
        app = Starlette(routes=[Mount("/mcp", app=sub)])
        app.add_middleware(ha.EgressBytesMiddleware)

        with TestClient(app) as client:
            for i in range(monitoring.EGRESS_MAX_DERIVED_PATHS + 5):
                client.get(f"/junk{i}/x")  # unauthenticated 404s, fill the derived axis
            r = client.get("/mcp/export")

            assert ("", "/mcp", ORIGIN_ROUTE) in _bytes_by_child(), (
                "unrelated junk starved the mounted surface's label")
            assert ("", monitoring.EGRESS_UNROUTED, ORIGIN_DERIVED) in _bytes_by_child()
            assert sum(
                value for (_, _, origin), value in _bytes_by_child().items()
                if origin == ORIGIN_ROUTE) == len(r.content)

    def test_trailing_newline_path_keeps_the_route_label(self):
        """Router parity: the label must agree with the router that served it.

        Starlette compiles templates as ``^…$`` and matches with ``re.match``,
        and Python's ``$`` matches just before a TRAILING NEWLINE — so
        ``/v1/version\n`` is served by the ``/v1/version`` route. A stricter
        matcher on our side would label a matched route as unrouted traffic and
        hand an unauthenticated client a way to move a route's bytes onto the
        untrusted axis (measured on both starlette versions).
        """
        inner = FastAPI()

        @inner.get("/v1/version")
        def _version():
            return Response(content=b"ok")

        sent: list[dict] = []

        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(message):
            sent.append(message)

        app = ha.EgressBytesMiddleware(inner)
        scope = {
            "type": "http", "http_version": "1.1", "method": "GET",
            "path": "/v1/version\n", "root_path": "", "raw_path": b"/v1/version%0a",
            "query_string": b"", "headers": [], "scheme": "http",
            "server": ("testserver", 80), "client": ("1.2.3.4", 1234), "state": {},
        }
        asyncio.run(app(scope, _receive, _send))

        assert sent[0]["status"] == 200  # the router really served it
        assert ("", "/v1/version", ORIGIN_ROUTE) in _bytes_by_child(), (
            "a route the router served was labelled as unrouted: "
            f"{sorted(_bytes_by_child())!r}")

    def test_dot_segment_path_does_not_borrow_the_mount_label(self):
        """A traversal shape must not carry the mounted surface's label.

        The bypass this pins (found in review): the mount's own sub-app receives
        ``/mcp/../v1/version`` (the sub-app emits the 404), and a ``Mount``'s
        ``path_regex`` is ``^/mcp/(?P<path>.*)$`` — it describes that path. So a
        stamped ``Mount`` would hand the traversal shape the mount's label; the
        scope here is exactly that shape, which the older test (a hand-built
        ``{"route": None}``) could not detect.
        """
        mount = Mount("/mcp", app=Starlette(routes=[]))

        label, derived = ha._egress_route_class(
            {"type": "http", "route": mount}, entry_path="/mcp/../v1/version")

        assert label != "/mcp", "a traversal path borrowed the mounted label"
        assert derived is True

    def test_route_with_a_raising_regex_falls_back(self):
        """The totality arc: a predicate that cannot answer must not raise.

        ``_route_describes`` is called from a ``finally`` block; a raise there
        would escape as the request's failure. Any exception is a "no".
        """
        class _RaisingRegex:
            def match(self, path):
                raise ValueError("bad pattern")

        class _Route:
            path = "/v1/version"
            path_regex = _RaisingRegex()

        assert ha._route_describes(_Route(), "/v1/version") is False
        assert ha._egress_route_class(
            {"type": "http", "route": _Route()}, entry_path="/v1/version") == (
                "/v1/version", True)

    def test_declared_plain_route_paths_are_stamped_on_the_route_axis(self):
        """FastAPI does not stamp plain Starlette routes, so they are declared.

        ``/openapi.json`` is a 146 KB code-literal response served by a plain
        ``Route``; without the declaration it would sit on the 8-slot untrusted
        axis (starvable) with unknown traffic.
        """
        with TestClient(ha.app) as client:
            r = client.get("/openapi.json")

        assert r.status_code == 200
        assert ("", "/openapi.json", ORIGIN_ROUTE) in _bytes_by_child()
        assert monitoring.egress_bytes_by_org()[""] >= len(r.content)

    def test_middleware_labels_by_the_arrival_path_not_the_response_scope(self):
        """Version-independent pin of the arrival-path capture.

        The router may stamp ``scope["route"]`` and rewrite ``scope["path"]``
        before the response is sent (1.7.0 stamps; a future version could do
        more), so the label must come from what arrived. This inner app does
        both, deliberately: reading the scope at response time would record
        `/export`.
        """
        async def _inner(scope, receive, send):
            scope.setdefault("state", {})["org_id"] = "org_pin"
            scope["path"] = "/export"
            scope["route"] = Route("/export", lambda request: JSONResponse({}))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"abcd"})

        app = ha.EgressBytesMiddleware(_inner)
        client = TestClient(app)  # no context manager: this raw ASGI app has no lifespan
        r = client.get("/plugins/export")

        assert r.status_code == 200
        assert ("org_pin", "/plugins/export", ORIGIN_DERIVED) in _bytes_by_child()
        assert not [c for c in _bytes_by_child() if c[1] == "/export"], (
            "the label came from the mutated response scope, not the arrival path")

    def test_mounted_route_is_not_accepted_as_the_template(self):
        """The 1.7.0 shape, forced, for a mount prefix that is NOT declared.

        The declared prefixes cover the app's own mounts (`/mcp`); any other
        mount still must not hand its sub-route label to the writer.
        """
        sub_route = Route("/export", lambda request: JSONResponse({}))
        scope = {"type": "http", "path": "/export", "route": sub_route}

        label, derived = ha._egress_route_class(scope, entry_path="/plugins/export")

        assert (label, derived) == ("/plugins/export", True)

    def test_template_is_kept_when_it_describes_the_entry_path(self):
        """The normal case must NOT regress: a matched template still wins.

        Path params resolve to the template (that is what bounds cardinality),
        so ``/v1/points/{pid}`` describes ``/v1/points/abc`` and is kept as a
        CODE-literal label (``derived=False``).
        """
        template = Route("/v1/points/{pid}", lambda request: JSONResponse({}))
        scope = {"type": "http", "path": "/v1/points/abc", "route": template}

        assert ha._egress_route_class(scope, entry_path="/v1/points/abc") == (
            "/v1/points/{pid}", False)

        # ...and a request the template does NOT describe falls back instead.
        assert ha._egress_route_class(
            scope, entry_path="/v1/points/abc/children") == (
                "/v1/points", True)

    def test_template_matching_survives_a_server_root_path(self):
        """``entry_path`` is ``get_route_path(scope)``, i.e. path minus root_path.

        uvicorn puts the prefix in BOTH ``scope["path"]`` and ``root_path``, and
        a route's regex is matched against the root_path-RELATIVE path. Comparing
        the regex against the prefixed path would match nothing, so every
        response under ``--root-path`` would land on the derived axis.
        """
        template = Route("/v1/version", lambda request: JSONResponse({}))
        scope = {"type": "http", "path": "/x/v1/version",
                 "root_path": "/x", "route": template}

        assert ha._egress_route_class(scope, entry_path="/v1/version") == (
            "/v1/version", False)

    def test_middleware_uses_the_root_path_relative_path(self):
        """A server ``--root-path`` must not push every response onto the fallback.

        Driven at the ASGI layer so ``path`` and ``root_path`` are both set the
        way a real server sets them: uvicorn puts the prefix in BOTH, while a
        route's regex is matched against the root_path-RELATIVE path. Reading
        ``scope["path"]`` here would match no template at all and label every
        response ``/x/v1`` on the derived axis.
        """
        async def _inner(scope, receive, send):
            scope.setdefault("state", {})["org_id"] = "org_root"
            scope["route"] = Route("/v1/version", lambda request: JSONResponse({}))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        sent: list[dict] = []

        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(message):
            sent.append(message)

        app = ha.EgressBytesMiddleware(_inner)
        scope = {
            "type": "http", "http_version": "1.1", "method": "GET",
            "path": "/x/v1/version", "root_path": "/x", "raw_path": b"/x/v1/version",
            "query_string": b"", "headers": [], "scheme": "http",
            "server": ("testserver", 80), "client": ("1.2.3.4", 1234), "state": {},
        }
        asyncio.run(app(scope, _receive, _send))

        assert sent[-1]["type"] == "http.response.body"
        assert ("org_root", "/v1/version", ORIGIN_ROUTE) in _bytes_by_child(), (
            "the root_path prefix leaked into the label: "
            f"{sorted(_bytes_by_child())!r}")

    def test_empty_path_falls_back_to_the_root_child(self):
        """No segment at all is not an exception — it is the root child."""
        assert ha._egress_route_class({"type": "http"}, entry_path="") == ("/", True)
        assert ha._egress_route_class({"type": "http"}, entry_path="/") == ("/", True)

    def test_route_without_a_path_regex_matches_by_exact_path(self):
        """A route object with no ``path_regex`` is matched by its path exactly.

        Two provenances, one string — which is why the flag matters even here.
        """
        class _NoRegexRoute:
            path = "/v1/version"

        scope = {"type": "http", "route": _NoRegexRoute()}

        assert ha._egress_route_class(scope, entry_path="/v1/version") == (
            "/v1/version", False)
        assert ha._egress_route_class(scope, entry_path="/v1/version/x") == (
            "/v1/version", True)

    def test_label_resolution_failure_never_fails_the_request(self, monkeypatch):
        """The label derivation is inside the guard, not just the increment.

        A raise there would escape the middleware's ``finally`` and replace the
        app's own exception (or break a request whose 200 was already sent).
        """
        payload = b"still served"

        def _boom(*args, **kwargs):
            raise RuntimeError("label derivation exploded")

        monkeypatch.setattr(ha, "_egress_route_class", _boom)
        client = TestClient(_app_with_payload(payload, org="org_a"))

        r = client.get("/v1/thing/x")

        assert r.status_code == 200
        assert r.content == payload
        assert monitoring.egress_bytes_by_org() == {}

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

    def test_declared_mount_prefixes_match_the_apps_real_mounts(self):
        """The declared list is a code literal — pin it against the real mounts.

        A mount that is not declared falls back to the request-derived axis,
        where unrelated junk can starve it; so the declaration must not drift
        from ``app.mount(...)``.
        """
        mounted = {route.path for route in ha.app.routes if isinstance(route, Mount)}
        assert set(ha._EGRESS_DECLARED_PREFIXES) == mounted, (
            "the egress declared-prefix list drifted from the app's mounts: "
            f"declared={ha._EGRESS_DECLARED_PREFIXES!r} mounted={sorted(mounted)!r}")

    def test_declared_paths_match_the_apps_plain_routes(self):
        """Pin the declared plain-Route paths against the real app.

        FastAPI stamps ``APIRoute`` but not plain ``Route``, so a plain route
        that is NOT declared falls to the request-derived axis, where unrelated
        junk can fold it. A new plain route must therefore be declared.
        """
        plain = {route.path for route in ha.app.routes if type(route) is Route}
        assert set(ha._EGRESS_DECLARED_PATHS) == plain, (
            "the egress declared-path list drifted from the app's plain routes: "
            f"declared={ha._EGRESS_DECLARED_PATHS!r} plain={sorted(plain)!r}")

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
        assert _bytes_by_child()[("", "/v1/version", ORIGIN_ROUTE)] == len(version.content)
        assert monitoring.egress_bytes_by_org().get("") == (
            len(version.content) + len(health.content)), (
            "the real app did not record its own response sizes: "
            f"{monitoring.egress_bytes_by_org()!r}")

"""#4490: hosted-API compute (machine time) attribution per org.

Covers the writer/attribution/readability contract, the bounded-cardinality
threat surface (the label axes are code-literal / auth-resolved, never
request-derived), the pure-ASGI middleware's coverage and fail-soft discipline,
and the production wiring (no dead hook — the middleware is on the real app and
a real request records).

The probes that matter are evidence, not assertions-of-existence: the fail-soft
probe injects a RAISING writer and asserts the response still flows; the
cardinality probe pushes past the cap and asserts the child set stays bounded.
"""
from __future__ import annotations

import asyncio
import re

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route

from tortoise import monitoring
from tortoise.hosted_api import ComputeAttributionMiddleware, _compute_route_class
from tortoise.hosted_api import app as real_app


@pytest.fixture(autouse=True)
def _clean_compute():
    """Isolate every test: the module-global metric family is process-wide."""
    monitoring._reset_compute()
    yield
    monitoring._reset_compute()


# ── ASGI harness ────────────────────────────────────────────────────────


def _scope(path="/", *, org=None, route=None, method="GET", root_path=""):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": root_path,
        "query_string": b"",
        "headers": [],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "state": {} if org is None else {"org_id": org},
        **({"route": route} if route is not None else {}),
    }


async def _drive(middleware, scope, *, inner=None, drop_response=False):
    """Run ``middleware`` with a trivial inner app; return the sent messages."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if drop_response:
            return  # the wait bound DROPS a late response: bytes never leave
        sent.append(message)

    if inner is None:
        async def inner_app(_scope, _receive, _send):
            await _send({"type": "http.response.start", "status": 200, "headers": []})
            await _send({"type": "http.response.body", "body": b"ok"})

        inner = inner_app
    await middleware(inner)(scope, receive, send)
    return sent


def _compute_label_values(path):
    """The set of (org, path) children currently recorded."""
    values = set()
    for family in monitoring.COMPUTE_REQUESTS.collect():
        for sample in family.samples:
            if not sample.name.endswith("_total") or sample.name.endswith("_created_total"):
                continue
            values.add((sample.labels.get("org"), sample.labels.get("path")))
    return values


# ── writer / attribution ────────────────────────────────────────────────


class TestWriterAndAttribution:
    def test_records_count_wall_and_cpu_per_org_path(self):
        monitoring.record_compute("org-a", "/v1/points/{pid}", 0.25, 0.05)
        monitoring.record_compute("org-a", "/v1/points/{pid}", 0.75, 0.10)
        snap = monitoring.compute_by_org()
        assert snap["org-a"]["requests"] == 2.0
        assert snap["org-a"]["wall_seconds"] == pytest.approx(1.0)
        assert snap["org-a"]["cpu_seconds"] == pytest.approx(0.15)

    def test_two_orgs_stay_distinct(self):
        monitoring.record_compute("org-a", "/v1/x", 0.1)
        monitoring.record_compute("org-b", "/v1/x", 0.2)
        snap = monitoring.compute_by_org()
        assert snap["org-a"]["wall_seconds"] == pytest.approx(0.1)
        assert snap["org-b"]["wall_seconds"] == pytest.approx(0.2)

    def test_no_org_is_an_honest_unattributed_child(self):
        monitoring.record_compute(None, "/v1/x", 0.1)
        monitoring.record_compute("", "/v1/x", 0.2)
        assert monitoring.compute_by_org()[""]["requests"] == 2.0

    def test_negative_durations_are_clamped_never_subtract(self):
        monitoring.record_compute("org-a", "/v1/x", -5.0, -3.0)
        snap = monitoring.compute_by_org()["org-a"]
        assert snap["wall_seconds"] == 0.0
        assert snap["cpu_seconds"] == 0.0

    def test_cpu_defaults_to_zero(self):
        monitoring.record_compute("org-a", "/v1/x", 0.5)
        assert monitoring.compute_by_org()["org-a"]["cpu_seconds"] == 0.0

    def test_snapshot_reconciles_to_the_requests_recorded(self):
        for i in range(7):
            monitoring.record_compute(f"org-{i % 3}", "/v1/x", 0.01)
        snap = monitoring.compute_by_org()
        assert sum(v["requests"] for v in snap.values()) == 7.0


# ── bounded cardinality (the declared adversarial surface) ──────────────


class TestBoundedCardinality:
    def test_org_axis_folds_past_cap_into_one_child(self):
        cap = monitoring.COMPUTE_MAX_ORGS
        for i in range(cap + 3):
            monitoring.record_compute(f"org-{i}", "/v1/x", 0.001)
        snap = monitoring.compute_by_org()
        assert monitoring.COMPUTE_OVERFLOW in snap
        # exactly `cap` real org children + the one shared overflow child
        assert len(snap) == cap + 1

    def test_path_axis_folds_past_cap_into_one_child(self):
        cap = monitoring.COMPUTE_MAX_PATHS
        for i in range(cap + 3):
            monitoring.record_compute("org-a", f"/v1/route-{i}", 0.001)
        paths = {p for (_o, p) in _compute_label_values("/v1/x")}
        assert monitoring.COMPUTE_OVERFLOW in paths
        assert len(paths) == cap + 1

    def test_admitted_org_is_never_displaced_by_later_traffic(self):
        monitoring.record_compute("org-a", "/v1/x", 0.1)
        for i in range(monitoring.COMPUTE_MAX_ORGS + 5):
            monitoring.record_compute(f"filler-{i}", "/v1/x", 0.001)
        assert monitoring.compute_by_org()["org-a"]["requests"] == 1.0

    def test_reset_clears_registries_and_children(self):
        for i in range(monitoring.COMPUTE_MAX_ORGS + 2):
            monitoring.record_compute(f"org-{i}", "/v1/x", 0.001)
        monitoring._reset_compute()
        assert monitoring.compute_by_org() == {}
        # registries cleared too — a following record must create a fresh child,
        # not inherit "already admitted" membership from a dead metric.
        monitoring.record_compute("org-new", "/v1/x", 0.001)
        assert monitoring.compute_by_org()["org-new"]["requests"] == 1.0

    def test_histogram_records_the_observed_wall_seconds(self):
        """The distribution is a MEASUREMENT, not just a registered object:
        deleting the ``observe`` call must red this."""
        monitoring.record_compute("org-a", "/v1/h", 0.2)
        monitoring.record_compute("org-a", "/v1/h", 0.8)
        sums = {s.name: s.value for fam in monitoring.COMPUTE_REQUEST_SECONDS.collect()
                for s in fam.samples if s.name.endswith(("_sum", "_count"))}
        assert sums["tortoise_compute_request_seconds_count"] == 2.0
        assert sums["tortoise_compute_request_seconds_sum"] == pytest.approx(1.0)

    def test_overflow_sentinels_are_not_org_id_shaped(self):
        """A generator change that made org ids collide with either sentinel would
        make routine folding indistinguishable from a real org/path. Pins the
        sentinels against the org-id SHAPE only — deliberately NOT coupled to the
        unrelated id generator (a widened id format must not red this)."""
        org_shape = re.compile(r"[0-9a-f]{26}")
        for sentinel in (monitoring.COMPUTE_OVERFLOW, monitoring.COMPUTE_UNROUTED):
            assert not org_shape.fullmatch(sentinel)

    def test_histogram_carries_no_org_label(self):
        """The cardinality decision: the distribution is route-class only, so the
        org axis and the bucket axis can never multiply."""
        monitoring.record_compute("org-a", "/v1/x", 0.5, 0.1)
        samples = [s for fam in monitoring.COMPUTE_REQUEST_SECONDS.collect()
                   for s in fam.samples]
        assert samples
        assert all("org" not in s.labels for s in samples)

    def test_generate_latest_exposes_the_family_without_an_org_on_the_histogram(self):
        from prometheus_client import generate_latest
        monitoring.record_compute("org-a", "/v1/x", 0.5, 0.1)
        text = generate_latest().decode()
        assert "tortoise_compute_wall_seconds_total" in text
        assert "tortoise_compute_cpu_seconds_total" in text
        assert "tortoise_compute_requests_total" in text
        hist_lines = [ln for ln in text.splitlines()
                      if ln.startswith("tortoise_compute_request_seconds")]
        assert hist_lines, "the histogram should be exported"
        assert all('org="' not in ln for ln in hist_lines), hist_lines

    def test_label_repair_makes_control_chars_and_surrogates_emittable(self):
        """Not a tautology: the RAW exposition is inspected. Deleting the
        translation would leave a literal newline/NUL/CSI inside the ``org="``
        value, which the scoped regex (``[^}]`` crosses a newline) still sees."""
        raw = "org\n\x00\x9b\ud800x"
        monitoring.record_compute(raw, "/v1/x\n", 0.1)
        from prometheus_client import generate_latest
        text = generate_latest().decode()
        org_values = set(re.findall(r'tortoise_compute_\w+\{[^}]*org="([^"]*)"', text))
        assert org_values, text[:300]
        for value in org_values:
            assert "\n" not in value
            assert "\x00" not in value
            assert "\u009b" not in value
            assert "\ud800" not in value
        assert monitoring.compute_by_org(), "the repaired label is a real child"


# ── the middleware ──────────────────────────────────────────────────────


class TestMiddleware:
    def test_attributes_a_matched_route_template_not_the_raw_path(self):
        route = Route("/v1/points/{pid}", endpoint=lambda: None)
        scope = _scope("/v1/points/abc", org="org-a", route=route)
        asyncio.run(_drive(ComputeAttributionMiddleware, scope))
        (_o, path), = _compute_label_values("/v1/points/abc")
        assert path == "/v1/points/{pid}"
        assert monitoring.compute_by_org()["org-a"]["requests"] == 1.0

    def test_unmatched_path_falls_to_the_single_unrouted_constant(self):
        scope = _scope("/nope/whatever/123", org="org-a")
        asyncio.run(_drive(ComputeAttributionMiddleware, scope))
        (_o, path), = _compute_label_values("/nope/whatever/123")
        assert path == monitoring.COMPUTE_UNROUTED

    def test_declared_mount_prefix_is_boundary_aware(self):
        assert _compute_route_class(_scope("/mcp"), "/mcp") == "/mcp"
        # boundary-aware: a sub-path matches, a bare-prefix sibling does not
        assert _compute_route_class(_scope("/mcp/tools"), "/mcp/tools") == "/mcp"
        # /mcpfoo must NOT be attributed to the real /mcp class
        assert _compute_route_class(_scope("/mcpfoo"), "/mcpfoo") == monitoring.COMPUTE_UNROUTED

    def test_declared_plain_route_stays_off_the_fallback(self):
        assert _compute_route_class(_scope("/openapi.json"), "/openapi.json") == "/openapi.json"

    def test_dotted_subpath_under_a_declared_mount_stays_the_mount(self):
        """No dot-gate: a legitimate dotted sub-route must not fold to
        ``__unrouted__`` (the boundary check alone already excludes ``/mcpfoo``)."""
        assert _compute_route_class(
            _scope("/mcp/tools.json"), "/mcp/tools.json") == "/mcp"

    def test_route_without_a_pattern_falls_back_to_the_constant(self):
        class _Bare:
            path = "/v1/bare"
            path_regex = None

        assert _compute_route_class(
            _scope("/v1/bare", route=_Bare()), "/v1/bare") == "/v1/bare"
        assert _compute_route_class(
            _scope("/nope", route=_Bare()), "/nope") == monitoring.COMPUTE_UNROUTED

    def test_empty_path_folds_to_unrouted(self):
        monitoring.record_compute("org-a", "", 0.1)
        (_o, path), = _compute_label_values("")
        assert path == monitoring.COMPUTE_UNROUTED

    def test_a_mount_is_never_the_serving_template(self):
        mount = Mount("/weird", app=lambda *a, **k: None)
        got = _compute_route_class(_scope("/weird/x", route=mount), "/weird/x")
        assert got == monitoring.COMPUTE_UNROUTED

    def test_a_route_whose_pattern_does_not_describe_the_path_is_not_stamped(self):
        route = Route("/export", endpoint=lambda: None)  # e.g. a mount's inner route
        got = _compute_route_class(_scope("/other/export", route=route), "/other/export")
        assert got == monitoring.COMPUTE_UNROUTED

    def test_root_path_is_stripped_so_templates_still_match(self):
        """READ BEFORE the app runs: under a server --root-path, matching
        ``scope["path"]`` would collapse the whole path dimension."""
        route = Route("/v1/points/{pid}", endpoint=lambda: None)
        scope = _scope("/root/v1/points/abc", org="org-a", route=route, root_path="/root")
        asyncio.run(_drive(ComputeAttributionMiddleware, scope))
        (_o, path), = _compute_label_values("/root/v1/points/abc")
        assert path == "/v1/points/{pid}"

    def test_org_resolved_midrequest_is_still_attributed(self):
        """The org is read AFTER the app returns, so a late auth resolution counts."""
        async def inner(_scope, _receive, _send):
            _scope["state"]["org_id"] = "late-org"
            await _send({"type": "http.response.start", "status": 200, "headers": []})
            await _send({"type": "http.response.body", "body": b""})

        asyncio.run(_drive(ComputeAttributionMiddleware, _scope("/v1/x"), inner=inner))
        assert monitoring.compute_by_org()["late-org"]["requests"] == 1.0

    def test_non_http_scope_is_passed_through_and_not_recorded(self):
        async def inner(_scope, _receive, _send):
            return
        scope = {"type": "lifespan"}
        asyncio.run(_drive(ComputeAttributionMiddleware, scope, inner=inner))
        assert monitoring.compute_by_org() == {}

    # ── fail-soft probes ────────────────────────────────────────────────

    def test_a_raising_writer_does_not_fail_the_request(self, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("metering explodes")
        monkeypatch.setattr(monitoring, "record_compute", boom)
        sent = asyncio.run(_drive(ComputeAttributionMiddleware, _scope("/v1/x", org="org-a")))
        # the response still flowed: measurement is never a new failure mode
        assert any(m["type"] == "http.response.body" for m in sent)

    def test_a_raising_label_derivation_does_not_fail_the_request(self, monkeypatch):
        import tortoise.hosted_api as ha
        def boom(*_a, **_k):
            raise RuntimeError("label derivation explodes")
        monkeypatch.setattr(ha, "_compute_route_class", boom)
        sent = asyncio.run(_drive(ComputeAttributionMiddleware, _scope("/v1/x", org="org-a")))
        assert any(m["type"] == "http.response.body" for m in sent)

    def test_compute_is_recorded_even_when_the_response_is_dropped(self):
        """The placement rationale: the wait bound DROPS a late response, but the
        CPU was burned — compute (unlike egress) must still be recorded."""
        monitoring._reset_compute()
        sent = asyncio.run(_drive(ComputeAttributionMiddleware, _scope("/v1/x", org="org-a"),
                                  drop_response=True))
        assert sent == [], "the harness must actually drop every message"
        snap = monitoring.compute_by_org()["org-a"]
        assert snap["requests"] == 1.0
        assert snap["wall_seconds"] > 0.0, "the burned wall time must be recorded"

    def test_a_client_header_or_query_cannot_set_the_org(self):
        """Attribution is auth-only: a header/query must never become the org key."""
        scope = _scope("/v1/x")
        scope["headers"] = [(b"x-org-id", b"evil"), (b"x-org", b"evil")]
        scope["query_string"] = b"org_id=evil&org=evil"
        asyncio.run(_drive(ComputeAttributionMiddleware, scope))
        snap = monitoring.compute_by_org()
        assert "evil" not in snap
        assert snap[""]["requests"] == 1.0

    def test_recording_compute_does_not_touch_the_write_ops_meter(self, monkeypatch):
        """Indicator 4: this adds a dimension, it does not reach the billed unit."""
        import tortoise.metering as metering

        def boom(*_a, **_k):
            raise AssertionError("a compute record must not touch record_write_ops")

        monkeypatch.setattr(metering, "record_write_ops", boom)
        monitoring.record_compute("org-a", "/v1/x", 0.1, 0.01)  # must not raise


# ── production wiring (no dead hook) ────────────────────────────────────


class TestProductionWiring:
    def test_middleware_is_installed_inside_the_bound_and_the_gauge(self):
        import tortoise.hosted_api as ha
        classes = [m.cls for m in ha.app.user_middleware]
        assert ha.ComputeAttributionMiddleware in classes, (
            "ComputeAttributionMiddleware is not installed — compute is never recorded")
        # Starlette inserts at index 0, so classes[0] is OUTERMOST.
        assert classes[0] is ha.WaitBoundMiddleware
        assert classes.index(ha.ComputeAttributionMiddleware) > classes.index(ha.InFlightMiddleware)

    def test_a_real_app_request_records_compute(self):
        """The hook is ALIVE on the real app, not merely present as an object."""
        monitoring._reset_compute()
        client = TestClient(real_app)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        snap = monitoring.compute_by_org()
        assert snap, "a real request through the real app recorded nothing"
        assert sum(v["requests"] for v in snap.values()) >= 1.0

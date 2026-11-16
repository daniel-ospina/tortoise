"""#12941 — a write must not read back as ABSENT.

WHY THIS FILE EXISTS
====================
Measured 2026-11-15 on the hosted app `tortoise-y4mjjq`, graph
`g_311d79c684de4d0e8953d189ed`: 8 goal Objects + 39 decision Events + 5
operators were accepted by the **record** (`public.object` / `event` / `point`)
and the **cache** held 1 object / 0 events / 0 operators — and
`TORTOISE_RECORD_SERVE` was UNSET, so every read door answered from the cache.
An agent that wrote and then verified saw `{}`, `404` and no signal at all: a
**successful write reading back as absent**, which is the one wrong answer that
is indistinguishable from a right one (§17.2).

The seam that fixes it already existed (`tortoise/record_serve.py`, #7970) and
was honoured by ONE door — the hosted `GET /v1/points/{id}`. The MCP door
(`tortoise_get_point`) and the SDK door (`TortoiseSDK.get_point`) had no record
fallback at all, and no door could tell a caller *"we cannot see it from here"*
from *"it does not exist"* (#7952's rule).

WHAT IS PINNED HERE — one test per door, per the issue's Definition of Done
==========================================================================
1. **read-your-writes at the public surface** — write lands in the record, cache
   is COLD, the read returns the node AND names the store that answered.
2. **`not-hot` is never `empty`** — a cache-only answer that found nothing says
   `residency: "not-hot"` when the record could not be asked, and says nothing
   when the record WAS asked (that absence is real).
3. **the default is unchanged** — `off` mode with no flag armed is byte
   identical, and `off` + `TORTOISE_RESIDENCY` reports `not-hot` for a partial
   cache instead of inventing an absence.

⚠️ These tests are DB-free on purpose (the repo's `test_record_serve.py` S9
pattern): a fake record answers the row query and a fake cache answers the graph
query, so the test pins the SEAM and the DOOR, not a live deployment.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from tests.test_record_serve import TENANT, _FakeConn, _opener, _row
from tortoise import record_serve as rs
from tortoise.residency import RESIDENCY_FIELD, RESIDENCY_NOT_HOT

# ── the two stores, faked ──────────────────────────────────────────────────


class _FakeG:
    """The cache graph: answers the point lookup and the residency declaration.

    `props=None` is a COLD cache — the measured state: the row exists in the
    record and the cache has never seen it.
    """

    def __init__(self, props, *, coverage=None, hot_keys=None):
        self.props = props
        self.coverage = coverage
        self.hot_keys = hot_keys
        self.queries: list[str] = []

    def query(self, sql, params=None, **_kw):
        self.queries.append(sql)
        if ":Meta" in sql:
            rows = ([[self.coverage, self.hot_keys]] if self.coverage else [])
        else:
            rows = ([[dict(self.props)]] if self.props is not None else [])

        class _R:
            result_set: ClassVar[list] = rows

        return _R()


class _FakeProj:
    def __init__(self, g):
        self.g = g


class _SDK:
    """The REAL `TortoiseSDK.get_point` body over a fake cache and a tenant.

    Subclassing is deliberate: the fallback must live in the METHOD under test,
    not in this stub — a stub that implemented the seam would pin the stub.
    """

    def __init__(self, props, *, tenant=TENANT, **decl):
        self._g = _FakeG(props, **decl)
        self._record_tenant = tenant

    def _get_proj(self):
        return _FakeProj(self._g)


def _sdk_class():
    """The real class, so `get_point` is the one under test."""
    from tortoise.sdk import TortoiseSDK

    Base = TortoiseSDK

    class _Stub(Base):
        def __init__(self, props, *, tenant=TENANT, **decl):
            self._g = _FakeG(props, **decl)
            self._record_tenant = tenant
            # ⛔ THE HANDLE OPEN IS THE COSTLY, FAILING EVENT (#12941 review
            # P1-1), so it is counted separately from the query: a cold open is a
            # connect + index ensure, and on a stopped graph it RAISES before the
            # record is ever asked. Asserting only "no query ran" left the
            # mutation that hoists `self._get_proj()` to the top of `get_point`
            # invisible — measured GREEN (23 passed) by mutation.
            self.handle_opens = 0

        def _get_proj(self):
            self.handle_opens += 1
            return _FakeProj(self._g)

    return _Stub


# ── 1. the MCP door — read-your-writes at the public surface ───────────────


@pytest.fixture
def mcp_harness(monkeypatch):
    """Drive the REAL MCP tool body (the `_safe` auth gate needs `http`)."""
    from tortoise import mcp_server
    from tortoise.mcp_auth import _current_org_id, _current_org_limits, _transport_mode

    _transport_mode.set("http")
    _current_org_id.set("org_t")
    _current_org_limits.set(None)
    try:
        def make(props, **decl):
            stub = _sdk_class()(props, **decl)
            monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: stub)
            return stub

        yield make
    finally:
        _transport_mode.set(None)
        _current_org_id.set(None)
        _current_org_limits.set(None)


def test_MCP_a_write_that_landed_in_the_record_is_not_absent(monkeypatch,
                                                             mcp_harness):
    """THE ISSUE, at the door an agent actually uses (#12941).

    Write → the record holds the row, the cache is cold → the read must return
    the node and name the store. On the pre-fix tree this returns `{}`: the
    written node reads back as ABSENT, with no error and no signal.
    """
    from tortoise.mcp_server import tortoise_get_point

    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[_row()])))
    mcp_harness(None)  # the cache is COLD: it has never seen this node

    out = tortoise_get_point("p-1")

    assert isinstance(out, dict) and out.get("id") == "p-1", (
        "a node the RECORD holds read back as absent through the MCP door — "
        f"got {out!r}")
    assert out.get("servedFrom") == "record", (
        "the read must name the store that answered; a caller cannot tell a "
        "record-served node from a cache hit otherwise")


def test_MCP_off_mode_is_byte_identical_today(monkeypatch, mcp_harness):
    """The default must not move: `off` + a cold cache returns `{}` as before."""
    from tortoise.mcp_server import tortoise_get_point

    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.delenv("TORTOISE_RESIDENCY", raising=False)
    mcp_harness(None)
    assert tortoise_get_point("p-1") == {}, (
        "off mode changed shape — the default must stay byte-identical")


def test_MCP_off_plus_the_RESIDENCY_flag_still_marks_a_PARTIAL_cache(monkeypatch,
                                                                     mcp_harness):
    """The default is byte-identical ONLY while the marker is unarmed too.

    `TORTOISE_RESIDENCY` is a SEPARATE flag from `TORTOISE_RECORD_SERVE` (#7952),
    so the honest statement about the default is "off + unarmed is unchanged",
    not "off is unchanged". Pinned at the DOOR as well as at the SDK layer
    (`test_SDK_off_mode_marks_a_PARTIAL_cache_not_hot`), because the door is what
    an agent reads and a docstring now says so: a cache that declares itself
    PARTIAL and does not cover the id answers `{"residency": "not-hot"}` here
    where pre-#12941 it answered `{}`. The record is never consulted either way.
    """
    from tortoise.mcp_server import tortoise_get_point

    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RESIDENCY", "1")
    rs.reset_fallback_counts()
    mcp_harness(None, coverage="partial", hot_keys=["other"])

    out = tortoise_get_point("p-1")

    assert out.get(RESIDENCY_FIELD) == RESIDENCY_NOT_HOT, (
        "a partial cache must not be allowed to establish absence (#7952)")
    assert rs.fallback_counts() == {}, (
        "the seam must not be consulted with TORTOISE_RECORD_SERVE unset")


def test_MCP_off_plus_the_RESIDENCY_flag_on_a_COMPLETE_cache_is_byte_identical(
        monkeypatch, mcp_harness):
    """The converse, so the marker cannot leak onto a complete cache."""
    from tortoise.mcp_server import tortoise_get_point

    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RESIDENCY", "1")
    mcp_harness(None, coverage="complete")

    assert tortoise_get_point("p-1") == {}


def test_MCP_says_not_hot_when_the_record_could_not_be_asked(monkeypatch,
                                                             mcp_harness):
    """A cache-only empty answer is `not-hot`, never `{}` (#7952)."""
    from tortoise.mcp_server import tortoise_get_point

    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(OSError("refused")))
    mcp_harness(None)

    out = tortoise_get_point("p-1")

    assert out.get("residency") == RESIDENCY_NOT_HOT, (
        "'we cannot see it from here' was reported as 'it does not exist' — "
        f"got {out!r}")
    assert out.get("servedFrom") == "cache"
    assert out.get("fallbackReason") == rs.FALLBACK_RECORD_UNAVAILABLE


def test_MCP_an_ESTABLISHED_absence_still_returns_an_empty_dict(monkeypatch,
                                                               mcp_harness):
    """#12941 review P0-1: the absence shape must stay FALSY.

    The seam's attribution was being merged into an empty answer, so a point
    that does not exist came back as a TRUTHY dict — and a verifier written as
    `if out: my write landed` read an absence as a success, the same wrong
    answer this issue removes, inverted. `hosted_api` never had this problem (an
    absence is a 404 wrapper); the MCP door must not either.
    """
    from tortoise.mcp_server import tortoise_get_point

    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    # The record WAS asked and holds no such row: an absence, not `not-hot`.
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[])))
    mcp_harness(None)

    out = tortoise_get_point("p-1")

    assert out == {}, f"an established absence must stay falsy — got {out!r}"
    assert not out


def test_MCP_record_mode_absence_is_also_empty(monkeypatch, mcp_harness):
    """…and `record` mode (which never falls back) keeps the same falsy shape."""
    from tortoise.mcp_server import tortoise_get_point

    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[])))
    mcp_harness(None)

    assert tortoise_get_point("p-1") == {}


def test_MCP_resolves_the_tenant_the_lane_actually_carries(monkeypatch,
                                                          mcp_harness):
    """The MCP lane carries the tenant AMBIENTLY, not on the SDK (#12941 P2-3).

    `mcp_auth._get_org_sdk` never sets `_record_tenant`; the tenant arrives
    through `record_writer.use_record_tenant`, installed by
    `OrgResolutionMiddleware` (`_record_tenant_scope`) for the whole request. A
    stub that hand-sets `_record_tenant` would prove the plumbing and nothing
    about production, so this test carries NO carried tenant — only the ambient
    scope — and the record read must still happen.
    """
    from tortoise import mcp_server
    from tortoise.record_writer import use_record_tenant

    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[_row()])))
    stub = _sdk_class()(None, tenant=None)      # the production shape
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: stub)

    with use_record_tenant(*TENANT):
        out = mcp_server.tortoise_get_point("p-1")

    assert out.get("id") == "p-1", (
        "the ambient tenant the MCP lane installs was not used, so the read "
        f"never reached the record — got {out!r}")
    assert out.get("servedFrom") == "record"


def test_MCP_attribution_shadows_a_stored_property_of_the_same_name(monkeypatch,
                                                                    mcp_harness):
    """The reserved-name rule (#12941 P2-3), the same one the hosted door states.

    A point whose own props carry `servedFrom` must not be able to forge an
    attribution — and in `off` mode, where no attribution exists, the stored
    value must pass through untouched (the pre-#12941 shape).
    """
    from tortoise.mcp_server import tortoise_get_point

    stored = {"id": "p-1", "servedFrom": "user-owned", "status": "live"}
    # `off`, unarmed: the stored property is the caller's data and nothing else.
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.delenv("TORTOISE_RESIDENCY", raising=False)
    mcp_harness(stored)
    assert tortoise_get_point("p-1")["servedFrom"] == "user-owned"

    # Armed and served by the cache: the attribution WINS.
    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[])))
    mcp_harness(stored)
    assert tortoise_get_point("p-1")["servedFrom"] == "cache", (
        "a stored property forged an attribution on an armed read")


def test_MCP_the_OPERATOR_door_reads_its_own_writes(monkeypatch, mcp_harness):
    """#12941 review P1-1: the operators are the issue's OWN measured population.

    "5 operators were accepted by the record, 0 in the cache" — and
    `tortoise_get_operator` read a Point by id with no seam, so an operator
    written and then verified answered `{}` with no signal. It shares the ONE
    caller-facing read now.
    """
    from tortoise.mcp_server import tortoise_get_operator

    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(
        _FakeConn(rows=[_row(is_operator=True, point_kind="operator")])))
    mcp_harness(None)

    out = tortoise_get_operator("p-1")

    assert out.get("id") == "p-1", (
        f"an operator the record holds read back as absent — got {out!r}")
    assert out.get("servedFrom") == "record"


def test_MCP_the_OPERATOR_door_does_not_turn_an_impairment_into_a_domain_fact(
        monkeypatch, mcp_harness):
    """A `not-hot` answer must not become "Point … is not an operator".

    The non-operator guard fires on "a dict with no `is_operator`", and a
    not-hot dict has no properties at all — so an impairment would have been
    reported as a fact about the graph.
    """
    from tortoise.mcp_server import tortoise_get_operator

    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(OSError("refused")))
    mcp_harness(None)

    out = tortoise_get_operator("p-1")

    assert out.get("residency") == RESIDENCY_NOT_HOT, out
    assert "error" not in out, f"an impairment was reported as a domain fact: {out!r}"


# ── 2. the SDK door — the same two guarantees, opt-in ──────────────────────


def test_SDK_a_cold_cache_falls_back_to_the_record_and_names_it(monkeypatch):
    """`served_out` is the opt-in instrument; without it the call is unchanged."""
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[_row()])))
    served: dict = {}
    sdk = _sdk_class()(None)

    props = sdk.get_point("p-1", served_out=served)

    assert props.get("id") == "p-1", (
        f"the SDK door served the cold cache, not the record — got {props!r}")
    assert served.get("servedFrom") == "record"
    assert served.get("fallbackReason") is None, (
        "the record answered: there was no fallback to explain")


def test_SDK_record_mode_refuses_instead_of_inventing_an_absence(monkeypatch):
    """`record` has no cache branch: an unreadable record RAISES (not `{}`)."""
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(rs, "open_record", _opener(OSError("refused")))
    sdk = _sdk_class()(None)

    with pytest.raises(rs.RecordUnavailable):
        sdk.get_point("p-1", served_out={})


def test_SDK_says_not_hot_when_the_record_could_not_be_asked(monkeypatch):
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(OSError("refused")))
    served: dict = {}
    sdk = _sdk_class()(None)

    props = sdk.get_point("p-1", served_out=served)

    assert props.get(RESIDENCY_FIELD) == RESIDENCY_NOT_HOT, (
        f"a cache-only empty answer came back as absence — got {props!r}")
    assert served.get("residency") == RESIDENCY_NOT_HOT
    assert served.get("fallbackReason") == rs.FALLBACK_RECORD_UNAVAILABLE


def test_SDK_without_the_instrument_is_byte_identical(monkeypatch):
    """No `served_out` → no record consult, no added key: today's call."""
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    opened = _opener(_FakeConn(rows=[_row()]))
    monkeypatch.setattr(rs, "open_record", opened)
    sdk = _sdk_class()(None)

    assert sdk.get_point("p-1") == {}
    assert opened.calls == [], (
        "the opt-in instrument was absent, yet the record was consulted — the "
        "internal accessor is called MID-WRITE and must stay cache-only")


def test_SDK_off_mode_marks_a_PARTIAL_cache_not_hot(monkeypatch):
    """#7952's rule for `off`: a partial cache cannot establish absence."""
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RESIDENCY", "1")
    served: dict = {}
    sdk = _sdk_class()(None, coverage="partial", hot_keys=["other"])

    props = sdk.get_point("p-1", served_out=served)

    assert props.get(RESIDENCY_FIELD) == RESIDENCY_NOT_HOT
    assert served.get("residency") == RESIDENCY_NOT_HOT


def test_SDK_off_mode_with_a_COMPLETE_cache_is_byte_identical(monkeypatch):
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RESIDENCY", "1")
    sdk = _sdk_class()(None, coverage="complete")

    assert sdk.get_point("p-1", served_out={}) == {}


def test_SDK_a_RECORD_answered_absence_is_never_marked_not_hot(monkeypatch):
    """The mirror of the off-mode test: an armed absence is a REAL absence.

    `record` mode on a cache that declares itself PARTIAL and the record
    answering "no row" is the strongest absence there is — the truth store was
    asked and held nothing. Reading the cache's declaration on top of that would
    invent an impairment out of a correctness guarantee, which is the same class
    of error as reporting not-hot as empty, in the other direction.
    """
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setenv("TORTOISE_RESIDENCY", "1")
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[])))
    served: dict = {}
    sdk = _sdk_class()(None, coverage="partial", hot_keys=["other"])

    props = sdk.get_point("p-1", served_out=served)

    # ⛔ #12941 review P2-1: the CACHE HANDLE was never opened. `record` mode
    # exists to answer with the graph engine stopped, so an eager `_get_proj()`
    # is not a cost question — on a stopped graph it is a hard failure before the
    # record is ever asked, re-introducible with a fully green suite until this
    # assertion existed (the reviewer proved that by mutation).
    assert sdk.handle_opens == 0, (
        f"record mode OPENED the cache graph handle ({sdk.handle_opens}×) — on a "
        "stopped graph that raises before the record is asked")
    assert sdk._g.queries == [], (
        "record mode queried the cache graph: "
        f"{sdk._g.queries}")
    assert props == {}, f"an armed absence must stay an absence — got {props!r}"
    assert served.get("servedFrom") == "record"
    assert RESIDENCY_FIELD not in served


def test_the_marker_speaks_the_DECLARATIONS_key_vocabulary(monkeypatch):
    """#12941 review P2-2: `partial` enumerates `Point:<id>`, not `<id>`.

    `cache_projection.declared_key` is the coordinate the WRITER puts in the
    declaration; passing a bare id made `point_residency`'s `key` inert and
    reported a node the declaration DOES cover as `not-hot` — a false
    impairment, in the fail-safe direction but still wrong.
    """
    from tortoise.cache_projection import declared_key

    monkeypatch.setenv("TORTOISE_RESIDENCY", "1")
    coord = declared_key("Point", "p-1")
    g = _FakeG(None, coverage="partial", hot_keys=[coord])

    assert rs.point_residency(g, found=False, key="p-1") is None, (
        "the declaration covers this node, so a miss is an in-store absence — "
        "not an impairment")
    # …and an UNCOVERED node is still a residency miss.
    assert rs.point_residency(g, found=False, key="p-2") == RESIDENCY_NOT_HOT


# ── 3. the hosted door — the 404 must carry the verdict ────────────────────


def _call_endpoint(monkeypatch, mode, *, cache="empty", record=None,
                   declaration=None):
    """Drive the REAL route. `declaration` arms the residency `:Meta` marker."""
    from fastapi import HTTPException

    import tortoise.hosted_api as api

    class _SpySDK:
        def _get_proj(self):
            class _G:
                def query(self, sql, params=None, **_k):
                    if ":Meta" in sql:
                        meta = ([[declaration[0], declaration[1]]]
                                if declaration else [])
                        return type("_R", (), {"result_set": meta})()
                    # The tombstone rule lives in the QUERY: the cache hands a
                    # retracted point to the endpoint as "no row", exactly as the
                    # production Cypher does.
                    if cache == "retracted":
                        # The TOMBSTONE-FILTERED query (the one whose text names
                        # `retracted`) answers "no row"; the any-status query
                        # answers the retracted node (#12941 review P1-3).
                        rows = ([] if "retracted" in sql else
                                [[{"id": "p-1", "status": "retracted"}]])
                    elif cache == "props":
                        rows = [[{"id": "p-1", "content": "cache",
                                  "pointKind": "claim", "status": "live"}]]
                    else:
                        rows = []
                    return type("_R", (), {"result_set": rows})()
            return type("P", (), {"g": _G()})()

    async def _sdk(_org, **_kwargs):
        return _SpySDK()

    monkeypatch.setenv(rs.RECORD_SERVE_ENV, mode)
    if record is not None:
        monkeypatch.setattr(rs, "open_record", record)
    monkeypatch.setattr(api, "_data_sdk_offloaded", _sdk)
    monkeypatch.setattr(api, "_make_sdk", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a direct SDK construction")))
    monkeypatch.setattr(api, "_resolve_record_tenant", lambda _org: TENANT)
    monkeypatch.setattr(api, "_require_scope", lambda *a, **k: None)
    org = {"id": "org_t", "graph_id": "g_t"}
    try:
        return asyncio.run(api.get_point("p-1", org=org)), None
    except HTTPException as e:
        return None, e


def test_hosted_404_says_not_hot_when_the_record_could_not_be_asked(monkeypatch):
    rs.reset_fallback_counts()
    body, err = _call_endpoint(monkeypatch, rs.MODE_PREFER, cache="empty",
                               record=_opener(OSError("refused")))

    assert body is None and err is not None and err.status_code == 404
    assert err.detail["servedFrom"] == "cache"
    assert err.detail[RESIDENCY_FIELD] == RESIDENCY_NOT_HOT, (
        "the hosted 404 reported 'not here' for a read the record was never "
        f"able to answer — got {err.detail!r}")


def test_hosted_404_is_a_PLAIN_absence_when_the_record_answered(monkeypatch):
    """The mirror: the record said 'no such row', so absence IS established."""
    rs.reset_fallback_counts()
    body, err = _call_endpoint(monkeypatch, rs.MODE_PREFER, cache="empty",
                               record=_opener(_FakeConn(rows=[])))

    assert body is None and err is not None and err.status_code == 404
    assert err.detail["fallbackReason"] == rs.FALLBACK_RECORD_MISS
    assert RESIDENCY_FIELD not in err.detail, (
        "the record WAS asked and held nothing — that is an absence, and a "
        f"not-hot marker here would be a false impairment: {err.detail!r}")


def test_hosted_off_mode_carries_no_verdict_without_its_own_flag(monkeypatch):
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.delenv("TORTOISE_RESIDENCY", raising=False)
    body, err = _call_endpoint(monkeypatch, rs.MODE_OFF, cache="empty")

    assert body is None and err is not None and err.status_code == 404
    assert err.detail == "Point not found", (
        "off mode must stay byte-identical: a plain string detail, no wrapper")


def test_hosted_off_mode_reports_not_hot_for_a_partial_cache(monkeypatch):
    """#7952 at the hosted door: `off` + a PARTIAL cache is not an absence.

    The marker has its OWN flag (`TORTOISE_RESIDENCY`), which is why this does
    not move the default — a deployment that arms neither switch is unchanged.
    """
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RESIDENCY", "1")
    body, err = _call_endpoint(monkeypatch, rs.MODE_OFF, cache="empty",
                               declaration=("partial", ["other"]))

    assert body is None and err is not None and err.status_code == 404
    assert isinstance(err.detail, dict), f"expected a verdict, got {err.detail!r}"
    assert err.detail[RESIDENCY_FIELD] == RESIDENCY_NOT_HOT


def test_hosted_off_mode_does_not_mark_a_RETRACTED_point_not_hot(monkeypatch):
    """The mirror (#12941 review P1-3): the cache CAN see a retracted point.

    `_cache_props` applies the endpoint's tombstone rule, so a retracted node
    answers this route with `None`. Reading that as "the store cannot see it"
    marked a node the store plainly holds `not-hot` — a false impairment on a
    correctness-guaranteed 404.
    """
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RESIDENCY", "1")
    body, err = _call_endpoint(monkeypatch, rs.MODE_OFF, cache="retracted",
                               declaration=("partial", ["other"]))

    assert body is None and err is not None and err.status_code == 404
    assert err.detail == "Point not found", (
        "a retracted point the cache holds was reported as an impairment — "
        f"got {err.detail!r}")


# ── 4. the three modes, named once ────────────────────────────────────────


def test_the_three_modes_and_the_absent_verdict():
    """`off`/`record`/`prefer` + the one rule that separates not-hot from absent."""
    cache_cannot_see = rs.ServedAnswer(
        read=rs.READ_POINT_BY_ID, coordinate="Point:p-1",
        served_from="cache", props=None,
        fallback_reason=rs.FALLBACK_RECORD_UNAVAILABLE)
    record_says_no = rs.ServedAnswer(
        read=rs.READ_POINT_BY_ID, coordinate="Point:p-1",
        served_from="cache", props=None,
        fallback_reason=rs.FALLBACK_RECORD_MISS)
    record_answers = rs.ServedAnswer(
        read=rs.READ_POINT_BY_ID, coordinate="Point:p-1",
        served_from="record", props=None)
    found = rs.ServedAnswer(
        read=rs.READ_POINT_BY_ID, coordinate="Point:p-1",
        served_from="cache", props={"id": "p-1"})

    assert cache_cannot_see.not_hot is True
    assert cache_cannot_see.absent_wire()[RESIDENCY_FIELD] == RESIDENCY_NOT_HOT
    for other in (record_says_no, record_answers, found):
        assert other.not_hot is False
        assert RESIDENCY_FIELD not in other.absent_wire()
    # …and the pinned wire shape of a found answer is untouched.
    assert cache_cannot_see.as_wire() == {
        "servedFrom": "cache",
        "fallbackReason": rs.FALLBACK_RECORD_UNAVAILABLE}


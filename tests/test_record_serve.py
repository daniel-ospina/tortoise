"""#7970 — the ANCHORED read surface served by the RECORD, and its anchoring.

WHAT THIS PINS. `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 makes Postgres
the system of record; the epic (#7879) built the schema, the projections and the
residency router, and left the router's own named consumer unbuilt — *"fetching
from the record is #7970"*. `tortoise/record_serve.py` is that fetch. Four
claims, each a test here rather than a sentence in a design doc:

1. **THE DECISION IS A REGISTRY, AND IT NAMES ITS EXCLUSIONS.** Every anchored
   read is either served (with a parity rule) or not served (with a reason). An
   exclusion without a reason is refused at construction, so "we did not serve
   it" can never be mistaken for "we forgot it".
2. **NO SILENT FALLBACK — STRUCTURALLY.** `read_node` takes a database
   connection, a scope and a coordinate. It has no graph handle and the module
   imports no graph driver, so the strict mode's inability to fall back is a
   property of the signature, not a rule someone has to remember. Only the
   `prefer` mode consults the cache, and when it does the answer SAYS SO and the
   event is COUNTED.
3. **AN ABSENCE AND AN IMPAIRMENT ARE NOT THE SAME ANSWER.** A record miss is
   `None` (the record is authoritative) — but only after the record was
   successfully read. A failure to read raises, and in `record` mode it is never
   downgraded to an empty answer or to a 404.
4. **PARITY IS A RULE, NOT AN EYEBALL.** `parity_difference` compares a cache
   answer with the record's and explains every difference: the schema's own
   refused set is the explanation, and a difference it does NOT explain — in
   EITHER direction — is a failure. That is what keeps "the same answer"
   meaningful while the EP carve-out (§17.5) legitimately keeps derived fields
   out of the record.

MUTATION PROOF — the guard each test exists to hold, and the edit that turns it
RED (reverts and their output are recorded in the PR body):

* **Let a record read fall back silently** — make `serve_point`'s `record` mode
  call `cache_answer()` on an opener failure instead of raising →
  `test_record_mode_never_consults_the_cache` RED.
* **Merge an impairment into an absence** — return `None` instead of raising
  when the opener fails → `test_an_unreadable_record_is_not_an_absence` RED.
* **Drop the anchor predicate** — remove `{key_column} = %s` from `_read_row`
  → `test_the_anchor_predicate_is_in_the_sql` RED.
* **Silently take the first row of an unbound anchor** — remove the
  `len(rows) > 1` refusal → `test_the_anchor_must_bind` RED.
* **Hide a fallback** — remove `note_fallback` from the `prefer` paths →
  `test_a_preferred_fallback_is_named_and_counted` RED.
* **Explain away a projection gap** — make `parity_difference` treat ANY
  missing field as explained → `test_parity_reddens_on_an_unexplained_absence`
  RED.
* **Invent a field** — drop the `invented` arm → `test_parity_reddens_on_an_invented_field`
  RED.
* **Serve the EP carve-out from a stored copy** — add `confidence` to the
  record answer → `test_a_record_served_read_carries_no_derived_name` RED.
* **Let the endpoint answer 404 for an impaired record** — replace the 503 with
  404 → `test_the_endpoint_never_turns_an_impaired_record_into_404` RED.

NO DATABASE IS NEEDED for any of the above. The live half — parity on a real
Postgres, and the S9 shape with the cache DESTROYED — is
`tests/test_record_serve_live.py`, which skips visibly when no record DSN is
configured rather than passing vacuously.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import ClassVar

import pytest

from tortoise import record_serve as rs
from tortoise.cache_projection import NODE_TABLES, _refused
from tortoise.record_projection import TENANT_GUC_SQL

TENANT = ("org_t", "g_t")


# ── the fake record ─────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, conn: _FakeConn):
        self._conn = conn
        self.description = None

    def execute(self, sql, params=()):
        self._conn.statements.append((sql, params))
        self._sql = " ".join(sql.split())
        self._params = params
        if self._sql == " ".join(TENANT_GUC_SQL.split()):
            # The prologue: remember the tenant this "transaction" was scoped to.
            self._conn.tenant = params[0]
            self._rows = [{"tenant": params[0]}]
        elif "bypassing" in self._sql:
            # The same round trip's second half: the GUC read back (what the
            # prologue actually set — a lost scope therefore reddens) plus the
            # role posture, which is non-bypassing.
            self._rows = [{"tenant": self._conn.tenant, "bypassing": False}]
        elif self._conn.error is not None:
            raise self._conn.error
        else:
            self._rows = list(self._conn.rows)
        return None

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        return None


class _FakeConn:
    """A record connection that answers what a test configures, and remembers."""

    def __init__(self, rows=None, *, error=None, org_id=TENANT[0]):
        self.rows = rows or []
        self.error = error
        self.org_id = org_id
        self.statements: list[tuple[str, tuple]] = []
        self.tenant = None
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        self.closed = True

    # -- assertions the tests make about the ONE row query it issued ---------
    def row_statements(self):
        return [s for s in self.statements
                if "FROM public." in " ".join(s[0].split())]


def _row(**kwargs):
    """A record row for a Point — the schema's columns, not the graph's names."""
    best = {"id": "p-1", "content": "c", "point_kind": "claim",
            "status": "live", "created_at": "T", "props": {"key": "K"}}
    best.update(kwargs)
    return best


def _opener(fake):
    calls = []

    def _open(_dsn=None):
        calls.append(_dsn)
        if isinstance(fake, Exception):
            raise fake
        return fake

    _open.calls = calls
    return _open


def _cache(props):
    calls = []

    def _answer():
        calls.append(1)
        if isinstance(props, Exception):
            raise props
        return props

    _answer.calls = calls
    return _answer


# ── 1. the decision is a registry that names its exclusions ────────────────


def test_the_registry_serves_the_anchored_surface_and_nothing_else():
    assert set(rs.served_reads()) == {rs.READ_POINT_BY_ID, rs.READ_NODE_BY_KEY}
    # The exclusions are part of the decision, not an omission.
    assert set(rs.not_served_reads()) == {
        "anchored-neighbours", "anchored-ep-scalars", "entity-accessor",
        "internal-accessor", "selfhost-endpoint", "whole-graph",
    }


def test_every_served_read_carries_its_parity_rule_and_its_coordinate():
    for name in rs.served_reads():
        read = rs.ANCHORED_READS[name]
        assert read.parity, name
        assert read.coordinate, name
        assert read.cypher, name


def test_every_exclusion_names_who_else_answers_the_same_read():
    """An exclusion must name the OTHER caller-facing surface, not just say no.

    `internal-accessor` is the one that drifted: the SDK accessor is reached by
    the hosted endpoint AND by the MCP tools, so a reason naming only the
    endpoint under-states what the exclusion covers — and the registry is the
    artifact a lane reads.
    """
    reason = rs.ANCHORED_READS["internal-accessor"].reason
    assert "tortoise_get_point" in reason
    assert "hosted endpoint" in reason
    # ... and it must say WHY they are not wired, not merely that they are not.
    assert "org credential" in reason
    # A SECOND accessor carries the same hazard and must be its own exclusion —
    # `tortoise_get_entity` reaches `TortoiseSDK.get_entity`, not `get_point`.
    entity = rs.ANCHORED_READS["entity-accessor"].reason
    assert "get_entity" in entity
    assert "tortoise_get_entity" in entity
    assert "mid-write" in entity


def test_an_exclusion_without_a_reason_is_refused_at_construction():
    with pytest.raises(ValueError):
        rs.AnchoredRead(name="x", status=rs.NOT_SERVED, cypher="MATCH ...")
    with pytest.raises(ValueError):
        rs.AnchoredRead(name="y", status=rs.SERVED, cypher="MATCH ...",
                        coordinate=("Point", "id"))


# ── 2. no silent fallback — structural ─────────────────────────────────────


def test_the_record_read_has_no_cache_handle_in_its_signature():
    for fn in (rs.read_node, rs.read_point):
        params = set(inspect.signature(fn).parameters)
        assert params <= {"conn", "org_id", "graph_id", "label", "key",
                          "point_id"}, (fn.__name__, params)


def test_the_module_imports_no_graph_driver():
    """The IMPORT graph, not the prose: this module cannot reach a graph client."""
    import ast

    tree = ast.parse(inspect.getsource(rs))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * (node.level or 0)) + (node.module or ""))
    forbidden = {"falkordb", "tortoise.projection", ".projection", ".sdk",
                 "tortoise.sdk", ".embedded_lifecycle", ".embedded"}
    assert not (imported & forbidden), sorted(imported & forbidden)


def test_record_mode_never_consults_the_cache():
    conn = _FakeConn(rows=[_row()])
    cache = _cache(AssertionError("the cache was consulted in record mode"))
    answer = rs.serve_point(tenant=TENANT, point_id="p-1",
                            cache_answer=cache, connect=_opener(conn),
                            mode=rs.MODE_RECORD)
    assert cache.calls == []
    assert answer.served_from == "record"
    assert answer.props["content"] == "c"


def test_off_mode_never_opens_the_record():
    """The default MUST stay byte-identical: no DSN, no connection, no import."""
    conn = _FakeConn(rows=[_row()])
    opener = _opener(conn)
    cache = _cache({"id": "p-1", "content": "from the cache"})
    answer = rs.serve_point(tenant=TENANT, point_id="p-1",
                            cache_answer=cache, connect=opener,
                            mode=rs.MODE_OFF)
    assert opener.calls == []
    assert conn.statements == []
    assert answer.served_from == "cache"
    assert answer.fallback_reason is None      # nothing was skipped
    assert answer.props["content"] == "from the cache"


# ── 3. absence ≠ impairment ────────────────────────────────────────────────


def test_a_record_miss_is_an_absence_in_record_mode():
    conn = _FakeConn(rows=[])
    cache = _cache(AssertionError("must not consult the cache"))
    answer = rs.serve_point(tenant=TENANT, point_id="gone",
                            cache_answer=cache, connect=_opener(conn),
                            mode=rs.MODE_RECORD)
    assert answer.props is None and answer.found is False
    assert answer.served_from == "record"
    assert cache.calls == []


@pytest.mark.parametrize("failure", [
    OSError("connection refused"),          # the driver / the door
    rs.RecordUnavailable("no record DSN"),  # the seam's own refusal
])
def test_an_unreadable_record_is_not_an_absence(failure):
    """BOTH arms of the failure path raise in record mode — no arm falls back."""
    cache = _cache({"id": "p-1"})
    with pytest.raises(rs.RecordUnavailable):
        rs.serve_point(tenant=TENANT, point_id="p-1", cache_answer=cache,
                       connect=_opener(failure), mode=rs.MODE_RECORD)
    assert cache.calls == [], "record mode must not fall back, even on an outage"


def test_a_preferred_fallback_is_named_and_counted():
    rs.reset_fallback_counts()
    cache = _cache({"id": "p-1", "content": "cache copy"})
    answer = rs.serve_point(tenant=TENANT, point_id="p-1", cache_answer=cache,
                            connect=_opener(OSError("down")),
                            mode=rs.MODE_PREFER)
    assert answer.served_from == "cache"
    assert answer.fallback_reason == rs.FALLBACK_RECORD_UNAVAILABLE
    assert answer.as_wire() == {"servedFrom": "cache",
                                "fallbackReason": "record-unavailable"}
    assert rs.fallback_counts() == {rs.FALLBACK_RECORD_UNAVAILABLE: 1}

    # A MISS is a different reason: it repairs by backfill, not by ops.
    answer = rs.serve_point(tenant=TENANT, point_id="gone", cache_answer=cache,
                            connect=_opener(_FakeConn(rows=[])),
                            mode=rs.MODE_PREFER)
    assert answer.fallback_reason == rs.FALLBACK_RECORD_MISS
    assert rs.fallback_counts() == {rs.FALLBACK_RECORD_UNAVAILABLE: 1,
                                    rs.FALLBACK_RECORD_MISS: 1}


def test_an_unknown_fallback_reason_is_refused():
    with pytest.raises(ValueError):
        rs.note_fallback("because")


# ── the anchor ─────────────────────────────────────────────────────────────


def test_the_anchor_predicate_is_in_the_sql():
    conn = _FakeConn(rows=[_row()])
    rs.read_point(conn, org_id="org_t", graph_id="g_t", point_id="p-1")
    (sql, params), = conn.row_statements()
    flat = " ".join(sql.split())
    assert "FROM public.point" in flat
    assert "org_id = %s" in flat
    assert "graph_id = %s" in flat
    assert "node_key = %s" in flat, (
        "the identity-contract column is the anchor: it is the PK (indexed) and "
        "the schema's CHECK pins it to the label's key property")
    assert "LIMIT 2" in flat, "the bind check is LIMIT 2, not a page size"
    assert params == ("org_t", "g_t", "p-1")


def test_the_table_and_key_come_from_the_schema_not_from_the_caller():
    """Every record label resolves to the schema's own table + key column."""
    for label in NODE_TABLES:
        key = "k-bind"
        conn = _FakeConn(rows=[])
        rs.read_node(conn, org_id="o", graph_id="g", label=label, key=key)
        (sql, params), = conn.row_statements()
        flat = " ".join(sql.split())
        assert f"FROM public.{NODE_TABLES[label]}" in flat
        # The column is a CONSTANT, never interpolated from the request; the
        # schema's CHECK pins it to `snake(KEY_PROPERTY[label])` per label.
        assert f"{rs.NODE_KEY_COLUMN} = %s" in flat
        assert params == ("o", "g", key)


def test_the_anchor_must_bind():
    """Two rows for a primary-keyed coordinate = the predicate did not restrict."""
    conn = _FakeConn(rows=[_row(), _row(id="other")])
    with pytest.raises(rs.AnchorDidNotBind):
        rs.read_point(conn, org_id="org_t", graph_id="g_t", point_id="p-1")
    # ... and it is NOT swallowed by the preferred mode's fallback.
    cache = _cache({"id": "p-1"})
    with pytest.raises(rs.AnchorDidNotBind):
        rs.serve_point(tenant=TENANT, point_id="p-1", cache_answer=cache,
                       connect=_opener(conn), mode=rs.MODE_PREFER)
    assert cache.calls == []


def test_an_unknown_label_is_a_refusal_not_an_empty_answer():
    conn = _FakeConn(rows=[])
    with pytest.raises(rs.RecordServeError):
        rs.read_node(conn, org_id="o", graph_id="g", label="NotALabel", key="k")


def test_the_read_installs_the_tenant_scope():
    conn = _FakeConn(rows=[_row()])
    rs.read_point(conn, org_id="org_t", graph_id="g_t", point_id="p-1")
    flat = [" ".join(s[0].split()) for s in conn.statements]
    assert any(s.startswith(TENANT_GUC_SQL.split("(")[0].strip()) for s in flat), (
        "an UNscoped read under RLS returns zero rows — which would be a false "
        "absence, the one answer this module must never invent")
    assert any("bypassing" in s for s in flat), (
        "the role posture is verified on the read door too (#9600)")


# ── 4. parity: the rule ────────────────────────────────────────────────────


GRAPH_POINT = {
    "id": "p-1", "content": "c", "pointKind": "claim", "status": "live",
    "createdAt": "T", "is_operator": False, "key": "K",
    "content_hash": "abc", "ep_dirty": True, "ep_dirty_at": 1,
}


def test_parity_explains_the_carve_out_and_finds_nothing_else():
    record = {"id": "p-1", "content": "c", "pointKind": "claim",
              "status": "live", "createdAt": "T", "is_operator": False,
              "key": "K"}
    diff = rs.parity_difference(GRAPH_POINT, record, label="Point")
    assert diff.clean, diff.as_dict()
    assert set(diff.explained_missing) == {"content_hash", "ep_dirty",
                                           "ep_dirty_at"}
    # The explanation is the SCHEMA's, not a list this test curates.
    assert set(diff.explained_missing) <= _refused("point")


def test_parity_reddens_on_an_unexplained_absence():
    record = {"id": "p-1", "pointKind": "claim", "status": "live",
              "createdAt": "T", "is_operator": False, "key": "K"}  # no content
    diff = rs.parity_difference(GRAPH_POINT, record, label="Point")
    assert not diff.clean
    assert set(diff.unexplained_missing) == {"content"}


def test_parity_reddens_on_an_invented_field():
    record = {"id": "p-1", "content": "c", "pointKind": "claim",
              "status": "live", "createdAt": "T", "is_operator": False,
              "key": "K", "aFieldTheRecordMadeUp": 1}
    diff = rs.parity_difference(GRAPH_POINT, record, label="Point")
    assert not diff.clean
    assert set(diff.invented) == {"aFieldTheRecordMadeUp"}


def test_parity_reddens_on_a_changed_value():
    record = {"id": "p-1", "content": "DIFFERENT", "pointKind": "claim",
              "status": "live", "createdAt": "T", "is_operator": False,
              "key": "K"}
    diff = rs.parity_difference(GRAPH_POINT, record, label="Point")
    assert not diff.clean
    assert set(diff.changed) == {"content"}


def test_a_field_both_stores_carry_is_always_compared():
    """The carve-out explains an ABSENCE; it never excuses a VALUE.

    This is what keeps a caller's own re-derivation honest: the served endpoint
    recomputes `content_hash` into its response, and a wrong recomputation must
    redden rather than hide behind the carve-out.
    """
    record = {"id": "p-1", "content": "c", "pointKind": "claim",
              "status": "live", "createdAt": "T", "is_operator": False,
              "key": "K", "content_hash": "WRONG"}
    diff = rs.parity_difference(GRAPH_POINT, record, label="Point")
    assert not diff.clean
    assert set(diff.changed) == {"content_hash"}


def test_a_fold_on_create_default_is_not_an_invention():
    """`Object.objectKind`/`title` are cache-side `ON CREATE` constants (#9634)."""
    cache = {"name": "acme", "objectKind": "other", "title": ""}
    record = {"name": "acme"}
    diff = rs.parity_difference(cache, record, label="Object")
    assert diff.clean, diff.as_dict()


def test_a_record_served_read_carries_no_derived_name():
    conn = _FakeConn(rows=[_row(content_hash="abc", ep_dirty=True)])
    props = rs.read_point(conn, org_id="org_t", graph_id="g_t", point_id="p-1")
    assert not (set(props) & _refused("point")), set(props) & _refused("point")


# ── the mode ───────────────────────────────────────────────────────────────


def test_the_mode_is_off_unless_it_names_a_store(monkeypatch):
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    assert rs.serve_mode() == rs.MODE_OFF
    for off in ("", "  ", "0", "false", "no", "off", "1", "true", "yes", "on",
                "recordd"):
        monkeypatch.setenv(rs.RECORD_SERVE_ENV, off)
        assert rs.serve_mode() == rs.MODE_OFF, off
    for on in ("record", "RECORD", " Record ", "prefer", "PREFER"):
        monkeypatch.setenv(rs.RECORD_SERVE_ENV, on)
        assert rs.serve_mode() == on.strip().lower()


# ── the endpoint seam — S9's shape, DB-free ────────────────────────────────


def _call_endpoint(monkeypatch, point_id="p-1", *, cache="unusable"):
    """Drive the REAL route function; `cache` is how the graph answers.

    ``unusable`` (the default) raises the moment the cache is touched — the
    record-served read must never reach it. ``empty`` answers "no such point",
    which is what a `prefer`-mode fallback needs in order to produce a 404 that
    still carries its attribution.
    """
    from fastapi import HTTPException

    import tortoise.hosted_api as api

    class _SpySDK:
        def _get_proj(self):
            if cache == "unusable":  # pragma: no cover - the default arm
                raise AssertionError("the endpoint touched the cache")

            class _G:
                def query(self, *_a, **_k):
                    class _R:
                        # `props` serves a point that carries its OWN property
                        # named like the attribution field, which is what the
                        # reserved-name rule is about.
                        result_set: ClassVar[list] = (
                            [[{"id": "p-1", "content": "cache",
                               "pointKind": "claim", "status": "live",
                               "servedFrom": "user-owned"}]] if cache == "props"
                            else [])
                    return _R()
            return type("P", (), {"g": _G()})()

    async def _sdk(_org):
        built.append(_org)
        return _SpySDK()

    def _no_direct_sdk(*_a, **_k):
        raise AssertionError("the endpoint constructed an SDK directly")

    built: list = []
    # #7970: the route reaches the cache ONLY through the bounded graph-lane seam
    # (`_data_sdk_offloaded`, #3498), which is also the seam the pre-PR code used,
    # so this spy proves "record mode built no handle" on the real path.
    # `_make_sdk` is the OTHER member of the repo's `_SDK_CONSTRUCTION_SEAMS`
    # (tests/test_read_routes_loop_responsiveness.py), so it is trapped too.
    monkeypatch.setattr(api, "_data_sdk_offloaded", _sdk)
    monkeypatch.setattr(api, "_make_sdk", _no_direct_sdk)
    monkeypatch.setattr(api, "_resolve_record_tenant", lambda _org: TENANT)
    monkeypatch.setattr(api, "_require_scope", lambda *_a, **_k: None)
    org = {"id": "org_t", "graph_id": "g_t"}
    try:
        body = asyncio.run(api.get_point(point_id, org=org))
        return body, None, built
    except HTTPException as e:  # pragma: no cover - the failure arms below
        return None, e, built


def test_the_endpoint_answers_from_the_record_with_the_cache_unusable(monkeypatch):
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[_row()])))
    body, err, _built = _call_endpoint(monkeypatch)
    assert err is None
    assert body["servedFrom"] == "record"
    assert body["kind"] == "claim"          # pointKind → kind still applied
    assert body["content"] == "c"
    # `content_hash` is refused as a column, so the endpoint RE-DERIVES it with
    # the writer's own function rather than dropping the field.
    from tortoise.sdk import _content_hash

    assert body["content_hash"] == _content_hash("c")
    assert "ep_dirty" not in body


def test_the_endpoint_never_turns_an_impaired_record_into_404(monkeypatch):
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(rs, "open_record",
                        _opener(OSError("connection refused")))
    body, err, _built = _call_endpoint(monkeypatch)
    assert body is None and err is not None
    assert err.status_code == 503, (
        "'the record could not be asked' is NOT 'no such point' — a 404 here "
        "would be a false absence (§17.2's rule, mirrored)")  


def test_a_preferred_miss_keeps_its_attribution_on_the_404(monkeypatch):
    """A 404 is an ANSWERED read: a counted fallback must not become invisible."""
    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[])))
    body, err, _built = _call_endpoint(monkeypatch, cache="empty")
    assert body is None and err is not None
    assert err.status_code == 404
    assert err.detail["servedFrom"] == "cache"
    assert err.detail["fallbackReason"] == rs.FALLBACK_RECORD_MISS
    assert rs.fallback_counts() == {rs.FALLBACK_RECORD_MISS: 1}


def test_the_default_path_carries_no_attribution_at_all(monkeypatch):
    """OFF is byte-identical: no `servedFrom`, no `fallbackReason`, no record."""
    rs.reset_fallback_counts()
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.setattr(rs, "open_record",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("off mode opened the record")))
    _body, err, _built = _call_endpoint(monkeypatch, cache="empty")
    assert err is not None and err.status_code == 404
    assert err.detail == "Point not found"  # the pre-#7970 shape, a plain string
    assert rs.fallback_counts() == {}


def test_the_endpoint_passes_the_mode_it_routed_on(monkeypatch):
    """The seam reads the flag ONCE — `serve_point` must not re-read it."""
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[_row()])))
    seen = {}
    real = rs.serve_point

    def _spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(rs, "serve_point", _spy)
    body, err, _built = _call_endpoint(monkeypatch)
    assert err is None and body["servedFrom"] == "record"
    assert seen["mode"] == rs.MODE_RECORD


def test_a_retracted_point_keeps_the_attribution_too(monkeypatch):
    """Both 404 arms carry attribution — the tombstone arm is still an answer."""
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(rs, "open_record",
                        _opener(_FakeConn(rows=[_row(status="retracted")])))
    body, err, _built = _call_endpoint(monkeypatch)
    assert body is None and err is not None
    assert err.status_code == 404
    assert err.detail["servedFrom"] == "record"


def test_the_endpoint_still_404s_a_real_absence_in_record_mode(monkeypatch):
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[])))
    body, err, _built = _call_endpoint(monkeypatch)
    assert body is None and err is not None and err.status_code == 404


def test_the_endpoint_prefers_the_cache_with_a_named_reason(monkeypatch):
    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[])))
    import tortoise.hosted_api as api

    class _CacheSDK:
        def _get_proj(self):
            class _G:
                def query(self, *_a, **_k):
                    class _R:
                        result_set: ClassVar[list] = [[
                            {"id": "p-1", "content": "cache",
                             "pointKind": "claim"}]]
                    return _R()
            return type("P", (), {"g": _G()})()

    async def _sdk(_org):
        return _CacheSDK()

    # #7970: the route reaches the cache through the bounded graph-lane seam,
    # lazily — only the `prefer` fallback arm constructs a handle at all.
    monkeypatch.setattr(api, "_data_sdk_offloaded", _sdk)
    monkeypatch.setattr(api, "_resolve_record_tenant", lambda _org: TENANT)
    monkeypatch.setattr(api, "_require_scope", lambda *_a, **_k: None)
    body = asyncio.run(api.get_point("p-1", org={"id": "org_t",
                                                 "org_id": "org_t"}))
    assert body["servedFrom"] == "cache"
    assert body["fallbackReason"] == rs.FALLBACK_RECORD_MISS
    assert body["content"] == "cache"
    assert rs.fallback_counts() == {rs.FALLBACK_RECORD_MISS: 1}


# ── #7970 review findings — the arms those fixes exist to hold ─────────────


def test_record_mode_builds_no_cache_handle_at_all(monkeypatch):
    """The ENDPOINT, not just `serve_point`, must be cache-free in `record` mode.

    Measured defect this pins: the route built its data SDK *before* reading the
    mode, and `_data_sdk` runs `_assert_graph_owned` — whose probe failure is a
    **403 GRAPH_NOT_FOUND**, an impairment answered as absence. So "answers from
    the record with the graph engine stopped" was true of `serve_point` and false
    of the shipped route. Patching the SDK FACTORY (the route's own seam) is what
    makes the claim observable: a construction here is the regression.
    """
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[_row()])))
    body, err, built = _call_endpoint(monkeypatch)
    assert err is None and body["servedFrom"] == "record"
    assert built == [], (
        "record mode constructed a cache handle, so the endpoint is "
        "cache-dependent even when the record answers")


def test_open_record_closes_the_connection_when_the_bound_fails(monkeypatch):
    """A connection that EXISTS when the read is abandoned must be closed.

    The statement_timeout prologue runs after the connect, and `serve_point` only
    closes a connection the opener RETURNED — so a raise between the two leaked a
    live socket per failed armed request, on the path that is already degraded.
    """
    import tortoise.record_writer as rw

    class _BadCursorConn(_FakeConn):
        def cursor(self):
            raise RuntimeError("statement_timeout rejected")

    conn = _BadCursorConn()
    monkeypatch.setattr(rw, "_default_connect", lambda _dsn: conn)
    with pytest.raises(rs.RecordUnavailable):
        rs.open_record("postgresql://u:p@h:5432/db")
    assert conn.closed is True, "the abandoned connection was left open"


def test_a_misconfigured_dsn_is_structural_not_an_outage(monkeypatch):
    """A non-Postgres DSN is a misconfiguration, so it must not read as an outage.

    `record_writer.record_dsn` refuses it with `RecordWriteError`. Left bare it
    would reach `serve_point`'s last arm and be counted as `record-unavailable` —
    pointing an operator at the database instead of at the value they set.
    """
    with pytest.raises(rs.RecordServeError) as caught:
        rs.open_record("mysql://u:p@h:3306/db")
    assert not isinstance(caught.value, rs.RecordUnavailable)


def test_an_unprojectable_record_is_structural_not_a_fallback(monkeypatch):
    """A row the reader cannot project is a DEFECT, never a named fallback.

    `cache_projection.node_from_row` raises `UnprojectableRecord` /
    `UnknownRecordColumn` for a row or column it has not been taught — the
    "the record is newer than the reader" class. Downgrading that to
    `record-unavailable` would hide a stale reader behind a plausible cache
    answer, which is the same downgrade the `AnchorDidNotBind` arm refuses.
    """
    from tortoise.cache_projection import UnprojectableRecord

    def _boom(_conn, **_kw):
        raise UnprojectableRecord("no contract key on this row")

    monkeypatch.setattr(rs, "read_point", _boom)
    cache = _cache({})
    with pytest.raises(UnprojectableRecord):
        rs.serve_point(tenant=TENANT, point_id="p-1", cache_answer=cache,
                       connect=_opener(_FakeConn(rows=[_row()])),
                       mode=rs.MODE_PREFER)
    assert cache.calls == [], "a structural refusal fell back to the cache"


def test_node_by_key_parity_is_declared_measured_for_point_only():
    """The served capability's parity bound is DECLARED, and it is real.

    `node-by-key` is served for any record label, but parity was measured for
    `Point` only: a record-sourced `:Event` loses the fold's `ON CREATE`
    defaults (including the ONTOLOGY §4.5-required `eventKind`), which
    `FOLD_ON_CREATE_DEFAULTS` does not cover — the open gap in #9721. The
    registry must say so, and the rule must actually redden, or "served" would
    be read as "parity proven for every label".
    """
    reason = rs.ANCHORED_READS[rs.READ_NODE_BY_KEY].reason
    assert "9721" in reason and "Point" in reason

    diff = rs.parity_difference(
        {"eventId": "e-1", "eventKind": "", "startedAt": "",
         "participants": []},
        {"eventId": "e-1"}, label="Event")
    assert diff.clean is False, (
        "the declared #9721 gap did not redden — the disclosure is stale")
    assert "eventKind" in diff.unexplained_missing


def test_a_misconfigured_tenancy_is_structural_not_a_fallback(monkeypatch):
    """The DSN's ROLE is a value problem, so it must not be counted as an outage.

    `record_projection.scope_connection` refuses a role that BYPASSES RLS (the
    production-shape misconfiguration, since `TEST_RECORD_DB_URI` is a superuser)
    with a `RecordProjectionError` whose message names the fix — *"arm
    TORTOISE_RECORD_DSN with tortoise_record_app"*. It is not a
    `RecordServeError`, so before this arm it fell to the availability branch:
    `prefer` counted it `record-unavailable` (an ops reason for a value problem)
    and `record` mode dropped the instruction, keeping only the class name.
    """
    from tortoise.record_projection import RecordProjectionError

    def _boom(_conn, **_kw):
        raise RecordProjectionError(
            "the connecting role BYPASSES RLS — arm TORTOISE_RECORD_DSN with "
            "tortoise_record_app")

    monkeypatch.setattr(rs, "read_point", _boom)
    cache = _cache({})
    with pytest.raises(RecordProjectionError):
        rs.serve_point(tenant=TENANT, point_id="p-1", cache_answer=cache,
                       connect=_opener(_FakeConn(rows=[_row()])),
                       mode=rs.MODE_PREFER)
    assert cache.calls == [], "a tenancy misconfiguration fell back to the cache"


def test_a_credential_without_a_record_tenant_is_a_fault_not_an_outage(monkeypatch):
    """No resolvable tenant is PERMANENT, so it is 500 — never 503 and never 404.

    503 is reserved for 'the record could not be asked' (retryable); a credential
    that resolves no record tenant never becomes resolvable by retrying, so
    answering 503 invites a retry loop that cannot succeed — and 404 would report
    absence, the defect the whole unit forbids.
    """
    from fastapi import HTTPException

    import tortoise.hosted_api as api

    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(api, "_resolve_record_tenant", lambda _org: None)
    monkeypatch.setattr(api, "_require_scope", lambda *_a, **_k: None)

    async def _no_cache(_org):  # pragma: no cover - must not be reached
        raise AssertionError("the cache was consulted without a record tenant")

    monkeypatch.setattr(api, "_data_sdk_offloaded", _no_cache)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.get_point("p-1", org={"id": "org_t",
                                              "org_id": "org_t"}))
    assert caught.value.status_code == 500
    assert "tenant" in str(caught.value.detail)


def test_off_mode_leaks_no_attribution_and_keeps_a_stored_name(monkeypatch):
    """OFF emits NO attribution, so a point's own `servedFrom` passes through.

    The reserved-name rule is one-way: an ARMED read lets the attribution win (a
    caller must be able to tell a real fallback from a stored field), while OFF
    must stay byte-identical to the pre-#7970 response — no wrapper, no keys, and
    the point's own property untouched.
    """
    body, err, built = _call_endpoint(monkeypatch, cache="props")
    assert err is None
    assert body["servedFrom"] == "user-owned", (
        "off mode overwrote a stored property with an attribution field")
    assert "fallbackReason" not in body
    assert built != [], "off mode did not consult the cache"


def test_prefer_answers_from_the_record_when_the_cache_cannot_be_opened(monkeypatch):
    """`prefer` must be ABLE to answer when the cache is unavailable.

    Measured defect this pins: the handle was constructed eagerly for `prefer`, so
    `_data_sdk_offloaded` raised **403 GRAPH_NOT_FOUND** before the record was ever
    asked — making the lenient mode strictly LESS able to answer than `record`,
    which is the opposite of its purpose. The construction failure is captured and
    re-raised only when the cache is actually needed.
    """
    from fastapi import HTTPException

    import tortoise.hosted_api as api

    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(rs, "open_record", _opener(_FakeConn(rows=[_row()])))
    monkeypatch.setattr(api, "_resolve_record_tenant", lambda _org: TENANT)
    monkeypatch.setattr(api, "_require_scope", lambda *_a, **_k: None)

    async def _graph_down(_org):
        raise HTTPException(status_code=403,
                            detail={"error_code": "GRAPH_NOT_FOUND"})

    monkeypatch.setattr(api, "_data_sdk_offloaded", _graph_down)
    body = asyncio.run(api.get_point("p-1", org={"id": "org_t",
                                                 "org_id": "org_t"}))
    assert body["servedFrom"] == "record"
    assert body["content"] == "c"


def test_prefer_counts_an_unresolved_tenant_under_its_own_reason(monkeypatch):
    """A permanent condition gets its own fallback reason, not the outage one.

    `record_tenant_for` returns `None` for every session/JWT read and every
    non-Supabase deployment, so counting it `record-unavailable` would fire the
    OUTAGE counter permanently on a condition the record never caused.
    """
    from fastapi import HTTPException

    import tortoise.hosted_api as api

    rs.reset_fallback_counts()
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    monkeypatch.setattr(api, "_resolve_record_tenant", lambda _org: None)
    monkeypatch.setattr(api, "_require_scope", lambda *_a, **_k: None)

    class _EmptySDK:
        def _get_proj(self):
            class _G:
                def query(self, *_a, **_k):
                    class _R:
                        result_set: ClassVar[list] = []
                    return _R()
            return type("P", (), {"g": _G()})()

    async def _sdk(_org):
        return _EmptySDK()

    monkeypatch.setattr(api, "_data_sdk_offloaded", _sdk)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.get_point("p-1", org={"id": "org_t",
                                              "org_id": "org_t"}))
    assert caught.value.status_code == 404
    assert (caught.value.detail["fallbackReason"]
            == rs.FALLBACK_RECORD_TENANT_UNRESOLVED)
    assert caught.value.detail["message"] == "Point not found"
    assert rs.fallback_counts() == {rs.FALLBACK_RECORD_TENANT_UNRESOLVED: 1}


def test_the_endpoint_answers_500_for_a_tenancy_misconfiguration(monkeypatch):
    """The refusal's INSTRUCTION must reach the client, as a fault (500).

    `RecordProjectionError` is not a `RecordServeError`, so before this arm it
    escaped `get_point` as a raw traceback and the operator instruction ("arm
    TORTOISE_RECORD_DSN with tortoise_record_app") never arrived. 500 — never
    503 (unreadable) and never 404 (absence).
    """
    from fastapi import HTTPException

    import tortoise.hosted_api as api
    from tortoise.record_projection import RecordProjectionError

    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(api, "_resolve_record_tenant", lambda _org: TENANT)
    monkeypatch.setattr(api, "_require_scope", lambda *_a, **_k: None)

    def _boom(**_kw):
        raise RecordProjectionError(
            "the connecting role BYPASSES RLS — arm TORTOISE_RECORD_DSN with "
            "tortoise_record_app")

    monkeypatch.setattr(rs, "serve_point", _boom)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.get_point("p-1", org={"id": "org_t",
                                              "org_id": "org_t"}))
    assert caught.value.status_code == 500
    assert "tortoise_record_app" in str(caught.value.detail)

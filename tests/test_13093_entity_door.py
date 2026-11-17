"""#13093 — an Object read back as `{}`: the entity door's verdict, then the record.

WHY THIS FILE EXISTS
====================
Measured 2026-11-17 on the hosted app `tortoise-y4mjjq`, graph
`g_311d79c684de4d0e8953d189ed`, `TORTOISE_RECORD_SERVE=prefer`,
`TORTOISE_RECORD_DSN` present: the record held **8 objects / 39 events / 5
points**, `POST /v1/points` → `GET /v1/points/{id}` answered **200
`servedFrom: "record"`**, and the canonical entity door answered

    MCP `tortoise_get_entity(id=<goal id>)`  →  `{}`

— not `not-hot`, not the node. A bare `{}` is the one wrong answer that is
indistinguishable from a right one (#7952): *"we cannot see it from here"* and
*"it does not exist"* came back as the same bytes on the only by-id door an agent
has for **Objects and Events**, and the owner's migration writes those two.

WHAT IS PINNED HERE — one test per door, plus the coverage rule
==============================================================
1. **an Object round-trips** — written to the record, cache COLD, the read
   returns the node and names the store (`servedFrom: "record"`). On the pre-fix
   tree this returns `{}` with `prefer` armed: the door never consulted the seam.
2. **an Event does NOT round-trip, and says so** — its parity is open (#9721), so
   the honest answer is `residency: "not-hot"`, NEVER `{}`. The parity dependency
   is named in the assertion's own message because that message is what the next
   lane will read.
3. **a record that cannot be asked is `not-hot`, never `{}`** — the record-down
   arm, on both the SDK and the MCP door.
4. **the coverage rule is conditional** — with full coverage the SAME miss is an
   established absence (`{}`), so the abstention is a declared coverage gap and
   not a blanket refusal to answer.
5. **the internal accessor stays cache-only** — `_get_entity` must not open the
   record even when armed: the write echoes and the mid-sweep read paths call
   it, and a record answer there reads a store a write has not projected.
6. **`off` is byte-identical** — the default does not move.

⚠️ DB-FREE on purpose (the repo's `test_record_serve.py` S9 pattern, reused by
`test_12941_read_your_writes.py`): a fake record answers the row queries per
TABLE and a fake cache answers the union query, so these pin the SEAM and the
DOORS, not a live deployment. The live arm would need a throwaway graph AND a
throwaway record tenant; `tests/test_record_serve_live.py` is where that class of
evidence lives, and it must never point at `live-v5`.
"""
from __future__ import annotations

import pytest

from tests.test_record_serve import (
    TENANT,
    _FakeConn,
    _FakeCursor,
    _opener,
    _row,
)
from tortoise import record_serve as rs
from tortoise.residency import RESIDENCY_FIELD, RESIDENCY_NOT_HOT

# ── the two stores, faked ──────────────────────────────────────────────────


class _TableCursor(_FakeCursor):
    """The shared fake cursor, plus PER-TABLE rows.

    ⛔ This is the whole point of the fake: the union read asks each label's OWN
    table, so one blob of rows would let an Object row answer a `public.point`
    query and the test would never notice the door asking the wrong table. The
    prologue (tenant GUC + role posture) is the parent's, unchanged.
    """

    def execute(self, sql, params=()):
        super().execute(sql, params)
        norm = " ".join(sql.split())
        if "FROM public." in norm:
            table = norm.split("FROM public.", 1)[1].split(" ", 1)[0].strip()
            self._rows = list(self._conn.tables.get(table, []))
        return None


class _TableConn(_FakeConn):
    """A record connection that answers a DIFFERENT row set per table."""

    def __init__(self, tables=None, *, error=None):
        super().__init__(rows=[], error=error)
        self.tables = dict(tables or {})

    def cursor(self):
        return _TableCursor(self)

    def table_statements(self):
        return [(" ".join(s[0].split()).split("FROM public.", 1)[1].split(" ", 1)[0])
                for s in self.row_statements()]


class _UnionG:
    """The cache graph: answers `_resolve_entity`'s UNION query.

    `nodes` are the rows `_resolve_entity` expects — ``[label, key, value,
    props]`` per branch — so the REAL `_resolve_entity`/`_get_entity` body runs
    under the test and the cache side is not a stub of the thing under test.
    """

    def __init__(self, nodes=None):
        self.nodes = list(nodes or [])
        self.queries: list[str] = []

    def query(self, sql, params=None, **_kw):
        self.queries.append(" ".join(sql.split()))

        class _R:
            result_set = list(self.nodes)

        return _R()


class _Proj:
    """The REAL projector's union resolver over the fake graph.

    `_resolve_entity` is bound off the real class so the body that runs is the
    production resolution ORDER (Point → Subject → Object → Source → Event →
    Source) — a hand-written stub here would pin the stub, and the precedence
    is half of what the record read has to mirror.
    """

    def __init__(self, g):
        self.g = g

    def _resolve_entity(self, *a, **kw):
        from tortoise.projection import FalkorProjection

        return FalkorProjection._resolve_entity(self, *a, **kw)

    def __getattr__(self, name):
        # `_RESOLVE_BRANCHES` and its siblings are CLASS attributes the real
        # resolver reads off `self`; take them from the real class rather than
        # restating the branch table in the test.
        from tortoise.projection import FalkorProjection

        return getattr(FalkorProjection, name)


def _sdk_class():
    """The REAL `TortoiseSDK`, so `get_entity` is the method under test."""
    from tortoise.sdk import TortoiseSDK

    class _Stub(TortoiseSDK):
        def __init__(self, nodes=None, *, tenant=TENANT):
            self._g = _UnionG(nodes)
            self._record_tenant = tenant
            self.handle_opens = 0

        def _get_proj(self):
            self.handle_opens += 1
            return _Proj(self._g)

    return _Stub


def _object_row(**kwargs):
    """A record row for an `:Object` — the schema's columns, not graph names."""
    row = {"id": "obj-1", "name": "migration-goal", "props": {}}
    row.update(kwargs)
    return row


def _event_row(**kwargs):
    row = {"event_id": "ev-1", "name": "decided", "props": {}}
    row.update(kwargs)
    return row


def _cache_object():
    return [["Object", "id", "obj-1",
             {"id": "obj-1", "name": "migration-goal", "objectKind": "other",
              "title": ""}]]


@pytest.fixture
def armed(monkeypatch):
    """Arm `prefer` and install a fake record opener that records its calls."""
    def _arm(tables=None, *, error=None):
        monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
        rs.reset_fallback_counts()
        conn = _TableConn(tables, error=error)
        opened = _opener(conn)
        monkeypatch.setattr(rs, "open_record", opened)
        return conn, opened

    return _arm


# ── 1. an Object round-trips through the record ────────────────────────────


def test_SDK_an_OBJECT_written_to_the_record_is_not_absent(armed):
    """THE ISSUE. Pre-fix this is `{}` — the measured production answer."""
    armed({"object": [_object_row()]})
    sdk = _sdk_class()(nodes=[])  # the cache is COLD

    out = sdk.get_entity("obj-1")

    assert isinstance(out, dict) and out.get("id") == "obj-1", (
        "an Object the RECORD holds read back as absent through the canonical "
        f"entity door — got {out!r}. This is #13093's measured production "
        "answer (8 objects in the record, `tortoise_get_entity` -> `{}`).")
    assert out.get("servedFrom") == "record", (
        "the read must name the store that answered; without it a caller "
        f"cannot tell a record-served node from a cache hit — got {out!r}")


def test_SDK_the_union_asks_each_labels_OWN_table_in_the_cache_order(armed):
    """The union is not a single blob query — it is per-label, first hit wins.

    ⛔ NON-VACUOUS: an Object row is present under the OBJECT table only, so a
    door that asked the wrong table (or one shared row set) could not find it,
    and the statement order is asserted against the declaration rather than
    against a hard-coded list.
    """
    conn, _ = armed({"object": [_object_row()]})
    sdk = _sdk_class()(nodes=[])

    assert sdk.get_entity("obj-1").get("id") == "obj-1"
    asked = [t for t in conn.table_statements() if t != "journal_event"]
    assert asked == ["point", "subject", "object"], (
        "the union must ask every served label's own table, in the cache "
        f"resolver's order — asked {conn.table_statements()}")
    # The `journal_event` read is the `status` fold `read_node` applies to the
    # label it HIT (`EVENT_STREAM_FOLDS`), not a fourth union branch.
    assert conn.table_statements().count("object") == 1


def test_MCP_an_OBJECT_written_to_the_record_is_not_absent(armed, monkeypatch):
    """The door an agent actually uses — `tortoise_get_entity(id)`."""
    from tortoise import mcp_server
    from tortoise.mcp_auth import (
        _current_org_id, _current_org_limits, _transport_mode,
    )

    armed({"object": [_object_row()]})
    stub = _sdk_class()(nodes=[])
    _transport_mode.set("http")
    _current_org_id.set("org_t")
    _current_org_limits.set(None)
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: stub)
    try:
        out = mcp_server.tortoise_get_entity("obj-1")
    finally:
        _transport_mode.set(None)
        _current_org_id.set(None)
        _current_org_limits.set(None)

    assert isinstance(out, dict) and out.get("id") == "obj-1", (
        f"MCP `tortoise_get_entity` answered {out!r} for an Object the record "
        "holds — the production defect #13093 measured")
    assert out.get("servedFrom") == "record", out


def test_SDK_a_cache_hit_still_answers_when_the_record_misses(armed):
    """A `prefer` MISS falls back to the cache and NAMES the reason.

    ⛔ The reason is the COVERAGE one, not `record-miss` — this union's `no row`
    settles only `ENTITY_UNION_LABELS`, so it cannot be read as an established
    absence (that is the difference from the single-label point door, whose
    `record-miss` IS one). `answered_by_cache` is what counts it, so the
    fallback stays measurable.
    """
    armed({"object": []})
    sdk = _sdk_class()(nodes=_cache_object())

    out = sdk.get_entity("obj-1")

    assert out.get("id") == "obj-1", out
    assert out.get("fallbackReason") == rs.FALLBACK_RECORD_LABEL_NOT_SERVED, out
    assert rs.fallback_counts().get(rs.FALLBACK_RECORD_LABEL_NOT_SERVED) == 1


# ── 2. an Event: no parity, so an HONEST verdict — never `{}` ──────────────


def test_SDK_an_EVENT_is_not_hot_and_never_an_empty_answer(armed):
    """#9721 is the named dependency, and it is NOT silently widened here.

    A record-sourced `:Event` loses SEVEN fold-written `ON CREATE` properties
    (`id`, `eventKind`, `subject`, `object`, `startedAt`, `classificationLevel`,
    `format`, `participants`) and `FOLD_ON_CREATE_DEFAULTS` has no `Event` row —
    so `parity_difference(label='Event')` reports them UNEXPLAINED. Serving it
    would ship a silent parity gap (a lost `id`) through the door whose whole
    purpose is honest reads; answering `{}` would claim it does not exist. The
    door abstains.
    """
    armed({"event": [_event_row()]})
    sdk = _sdk_class()(nodes=[])  # cold cache, Event only in the record

    out = sdk.get_entity("ev-1")

    assert out != {}, (
        "an Event read back as a BARE EMPTY DICT — that is the 'cannot see it' "
        "/ 'does not exist' conflation #7952 forbids, and the exact production "
        "defect #13093 measured")
    assert out.get(RESIDENCY_FIELD) == RESIDENCY_NOT_HOT, (
        f"expected the honest impairment marker, got {out!r}")
    # The reason names the COVERAGE gap, not a plain record miss: `record-miss`
    # would make this an ESTABLISHED absence (`RECORD_ANSWERED_REASONS`).
    assert out.get("fallbackReason") == rs.FALLBACK_RECORD_LABEL_NOT_SERVED, out


def test_the_coverage_reason_is_never_read_as_an_established_absence():
    """⛔ THE LOAD-BEARING PIN on the reason vocabulary.

    `RECORD_ANSWERED_REASONS` is what turns an empty answer into an established
    absence. Adding the coverage reason there would convert "this door does not
    serve that label" into "the node does not exist" — #7952 re-introduced by a
    string.
    """
    assert rs.FALLBACK_RECORD_LABEL_NOT_SERVED not in rs.RECORD_ANSWERED_REASONS
    assert rs.FALLBACK_RECORD_LABEL_NOT_SERVED in rs.FALLBACK_REASONS


# ── 3. a record that cannot be asked ───────────────────────────────────────


def test_SDK_says_not_hot_when_the_record_could_not_be_asked(monkeypatch):
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    rs.reset_fallback_counts()
    monkeypatch.setattr(rs, "open_record", _opener(OSError("record is down")))
    sdk = _sdk_class()(nodes=[])

    out = sdk.get_entity("obj-1")

    assert out.get(RESIDENCY_FIELD) == RESIDENCY_NOT_HOT, (
        f"an unaskable record must answer not-hot, got {out!r}")
    assert out.get("fallbackReason") == rs.FALLBACK_RECORD_UNAVAILABLE, out
    assert rs.fallback_counts().get(rs.FALLBACK_RECORD_UNAVAILABLE) == 1, (
        "the fallback must be COUNTED, not only named")


def test_MCP_a_record_that_could_not_be_asked_is_not_hot(monkeypatch):
    from tortoise import mcp_server
    from tortoise.mcp_auth import (
        _current_org_id, _current_org_limits, _transport_mode,
    )

    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    rs.reset_fallback_counts()
    monkeypatch.setattr(rs, "open_record", _opener(OSError("record is down")))
    stub = _sdk_class()(nodes=[])
    _transport_mode.set("http")
    _current_org_id.set("org_t")
    _current_org_limits.set(None)
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: stub)
    try:
        out = mcp_server.tortoise_get_entity("obj-1")
    finally:
        _transport_mode.set(None)
        _current_org_id.set(None)
        _current_org_limits.set(None)

    assert out.get(RESIDENCY_FIELD) == RESIDENCY_NOT_HOT, (
        f"the MCP door answered {out!r} for a record it could not ask")
    assert out != {}


def test_SDK_record_mode_refuses_instead_of_inventing_an_absence(monkeypatch):
    """`record` has no cache branch, so a structural failure RAISES."""
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    monkeypatch.setattr(rs, "open_record", _opener(OSError("record is down")))
    sdk = _sdk_class()(nodes=[])

    with pytest.raises(rs.RecordUnavailable):
        sdk.get_entity("obj-1")


# ── 4. the coverage rule is CONDITIONAL, not a blanket refusal ─────────────


def test_the_abstention_is_a_COVERAGE_gap_not_a_refusal_to_answer(monkeypatch):
    """With every record label served, the SAME miss is an established absence.

    ⛔ This is what makes the abstention honest rather than convenient: the
    door answers `{}` the moment nothing is outside its coverage, so the
    `not-hot` above is caused by #9721's open labels and not by a rule that
    never yields an answer.
    """
    from tortoise.cache_projection import NODE_TABLES

    answer = rs.ServedAnswer(read=rs.READ_ENTITY_UNION_BY_ID, coordinate="c",
                             served_from="record", props=None)
    assert rs.entity_absent_wire(answer) != {}
    assert rs.entity_coverage_complete() is False

    monkeypatch.setattr(rs, "ENTITY_UNION_LABELS", tuple(NODE_TABLES))
    assert rs.entity_coverage_complete() is True
    assert rs.entity_absent_wire(answer) == {}, (
        "with complete coverage the record's `no row` IS an absence, and the "
        "answer must be the FALSY empty dict (a verifier written as `if out:` "
        "must not read an absence as a success, #12941 review P0-1)")


def test_the_served_labels_are_the_ones_with_measured_parity():
    """The declaration, pinned to the evidence rather than to a count.

    `Subject`/`Object` are cleared by #9634 (`FOLD_ON_CREATE_DEFAULTS` covers
    their fold constants) and `Point` by `point-by-id`'s measured parity; `Event`
    and `Source` are the open #9721 list. A label added here without that
    evidence would serve a silent parity gap through an honesty door.
    """
    from tortoise.cache_projection import FOLD_ON_CREATE_DEFAULTS, KEY_PROPERTY

    assert set(rs.ENTITY_UNION_LABELS) == {"Point", "Subject", "Object"}
    assert {"Subject", "Object"} <= set(FOLD_ON_CREATE_DEFAULTS), (
        "#9634's declared fold constants are what clear Subject/Object for a "
        "record-served read")
    assert "Event" not in FOLD_ON_CREATE_DEFAULTS, (
        "if #9721 lands, revisit ENTITY_UNION_LABELS — the abstention is caused "
        "by THIS gap")
    # Every served label must be addressable by the bare id this door is handed.
    for label in rs.ENTITY_UNION_LABELS:
        assert KEY_PROPERTY.get(label) == "id", (
            f"{label} is keyed by {KEY_PROPERTY.get(label)!r} in the record, so "
            "a bare id cannot address it — that is Source's own exclusion")


# ── 5. the internal accessor stays cache-only ──────────────────────────────


def test_the_INTERNAL_accessor_never_consults_the_record(monkeypatch):
    """⛔ `_get_entity` is the write echo and the mid-sweep read path.

    Serving it would make a writer read a store the same write has not finished
    projecting, and would hand `derived_invalidation` / `goal_judgement` a
    TRUTHY `{"residency": "not-hot"}` where they test `or MISSING` and read it
    as a found node. So the seam is on `get_entity` only.
    """
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    conn, opened = _TableConn({"object": [_object_row()]}), None
    opened = _opener(conn)
    monkeypatch.setattr(rs, "open_record", opened)
    sdk = _sdk_class()(nodes=[])

    assert sdk._get_entity("obj-1") == {}, (
        "the internal accessor answered from something other than the cache")
    assert opened.calls == [], (
        "the internal accessor opened the record — a mid-write reader must not")


# ── 6. the default does not move ───────────────────────────────────────────


def test_off_mode_is_byte_identical_and_consults_nothing(monkeypatch):
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.delenv("TORTOISE_RESIDENCY", raising=False)
    opened = _opener(_TableConn({"object": [_object_row()]}))
    monkeypatch.setattr(rs, "open_record", opened)
    sdk = _sdk_class()(nodes=[])

    assert sdk.get_entity("obj-1") == {}, (
        "off mode changed shape — the default must stay byte-identical")
    assert opened.calls == [], "off mode consulted the record"


def test_off_mode_still_answers_a_cache_hit_unchanged(monkeypatch):
    monkeypatch.delenv(rs.RECORD_SERVE_ENV, raising=False)
    monkeypatch.delenv("TORTOISE_RESIDENCY", raising=False)
    sdk = _sdk_class()(nodes=_cache_object())

    out = sdk.get_entity("obj-1")

    assert out == {"id": "obj-1", "name": "migration-goal",
                   "objectKind": "other", "title": ""}, (
        f"off mode must carry NO attribution and no marker — got {out!r}")


# ── 7. the bounded door shares the point door's policy ────────────────────


def test_the_entity_door_is_BOUNDED_like_the_point_door(monkeypatch):
    """A slow record must yield `prefer`'s cache answer, not the transport's
    `-32009` (#12947 finding 1, one door over)."""
    import time

    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    rs.reset_fallback_counts()
    monkeypatch.setattr(rs, "open_record", _opener(_SlowConn(0.4)))
    monkeypatch.setattr("tortoise.monitoring.record_read_timeout_s",
                        lambda: 0.05)
    sdk = _sdk_class()(nodes=_cache_object())

    started = time.monotonic()
    out = sdk.get_entity("obj-1")
    elapsed = time.monotonic() - started

    assert out.get("fallbackReason") == rs.FALLBACK_RECORD_BOUND_MISS, out
    assert elapsed < 0.4, (
        f"the read waited {elapsed:.2f}s for a record the bound had abandoned")
    assert rs.fallback_counts().get(rs.FALLBACK_RECORD_BOUND_MISS) == 1


class _SlowConn(_TableConn):
    """A record connection that answers after a delay."""

    def __init__(self, delay):
        super().__init__({"object": [_object_row()]})
        self.delay = delay

    def cursor(self):
        import time

        time.sleep(self.delay)
        return _TableCursor(self)


def test_a_negative_bound_is_refused_rather_than_unbounded():
    with pytest.raises(ValueError):
        rs.serve_entity_bounded(bound_s=0, tenant=TENANT, id_val="x",
                                cache_answer=lambda: None, mode=rs.MODE_PREFER)

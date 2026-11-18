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

import re

import pytest

from tests.test_record_serve import (
    TENANT,
    _FakeConn,
    _FakeCursor,
    _opener,
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
            # An INSTANCE attribute, not a class one: a mutable class attribute
            # would be shared across responses and is what `ruff` RUF012 refuses.
            def __init__(self, rows):
                self.result_set = list(rows)

        return _R(self.nodes)


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
    door that asked the wrong table (or one shared row set) could not find it.
    The statement order IS asserted as a literal list, which is deliberate —
    `:test_the_coverage_domain_is_the_cache_resolvers_branch_labels` is what
    pins the declaration, and a literal here is what catches a reordering of the
    SERVED subset (mutating the tuple reddens it). An earlier docstring claimed
    this was "asserted against the declaration rather than against a hard-coded
    list"; it is not, so the claim is corrected rather than the assertion
    (#13322 review observation 4).
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
        _current_org_id,
        _current_org_limits,
        _transport_mode,
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

    A record-sourced `:Event` loses the fold-written `ON CREATE` properties its
    row does not carry, PER COLUMN: #9721's measured set is seven (`id`, `subject`,
    `object`, `startedAt`, `classificationLevel`, `format`, `participants`) for the
    shape it probed (a producer that supplied `event_kind`), `eventKind` is an
    eighth when that column is NULL, and all eight are carried — and none lost —
    when the row has them. `FOLD_ON_CREATE_DEFAULTS` has no `Event` row —
    so `parity_difference(label='Event')` reports them UNEXPLAINED. Serving it
    would ship a silent parity gap (a lost `id`) through the door whose whole
    purpose is honest reads; answering `{}` would claim it does not exist. The
    door abstains.
    
    ⚠️ TODAY-PIN: this row states the state #13353 shipped in, and it is EXPECTED
    to be updated when #9721 lands. The only acceptable greening is a GROWN
    union — never a shrunken jurisdiction, and never a label taken back out.
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


def test_a_RECORD_mode_miss_carries_no_fallback_reason_and_counts_nothing(monkeypatch):
    """A coverage fact is not a fallback, and the wire must agree with the counter.

    ⛔ MEASURED DEFECT THIS PINS (#13353 review cycle 2, finding 2): the `record`
    arm used to set `fallbackReason: "record-label-not-served"` on a read where
    NOTHING fell back — no cache branch, no counted reason, the graph handle never
    opened — so an operator reconciling the wire attribution against
    `fallback_counts()` saw two different stories for the same read, and
    `ServedAnswer.fallback_reason`'s own docstring ("the named reason a
    `prefer`-mode read landed on the cache, or `None` when no fallback happened")
    was false on that arm.
    """
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_RECORD)
    rs.reset_fallback_counts()
    conn = _TableConn({"object": []})
    monkeypatch.setattr(rs, "open_record", _opener(conn))
    sdk = _sdk_class()(nodes=None)

    out = sdk.get_entity("obj-1")

    assert out != {}, "a record-mode miss is not an established absence here (#9721)"
    assert out.get(RESIDENCY_FIELD) == RESIDENCY_NOT_HOT, out
    assert "fallbackReason" not in out or out["fallbackReason"] is None, (
        f"a `record`-mode miss named a fallback that never happened: {out!r}")
    assert rs.fallback_counts() == {}, (
        f"a reason was counted for a read that never fell back: {rs.fallback_counts()}")
    assert sdk.handle_opens == 0, (
        "record mode must answer without building a graph handle")


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
        _current_org_id,
        _current_org_limits,
        _transport_mode,
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


@pytest.mark.parametrize("mode", [rs.MODE_OFF, rs.MODE_PREFER, rs.MODE_RECORD])
def test_an_EMPTY_key_is_absent_in_EVERY_mode_and_opens_NO_store(monkeypatch, mode):
    """`get_entity("")` must not depend on the mode, and must reach neither store.

    The record's reader refuses an empty key as STRUCTURAL (`read_node`), so an
    armed `get_entity("")` would answer by raising while `off` answered `{}` —
    the same id behaving differently on a deployment flag, at a door whose only
    contract is "the node's properties, or an empty dict". Nothing is named, so
    nothing is established.

    ⛔ ALL THREE MODES, AND BOTH STORES (#13353 review, findings 2 and 2b). The
    previous cut armed only `prefer`, and asserted only that the RECORD was not
    opened — so it stayed green when the guard was moved BELOW the tenant
    resolution (where a tenant-less `record` deployment then raised) and it could
    not see the guard delegating to `_get_entity`, which builds a graph handle in
    the one mode that exists to run with the graph engine stopped, and can return
    a node for `""` from the store that mode promises not to read.
    """
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, mode)
    rs.reset_fallback_counts()
    conn = _TableConn()
    opened = _opener(conn)
    monkeypatch.setattr(rs, "open_record", opened)
    sdk = _sdk_class()(nodes=[], tenant=None)

    assert sdk.get_entity("") == {}, (
        f"an empty key is not a coordinate — it must stay the absent answer in "
        f"{mode!r}")
    assert opened.calls == [], (
        f"the record was opened for a key that names no node ({mode!r})")
    if mode != rs.MODE_OFF:
        # `off` is byte-identical to the pre-#13093 call BY CONTRACT (it goes to
        # the cache accessor and may build the handle — that is what "off" is),
        # so the no-handle promise is an ARMED-mode promise only.
        assert sdk.handle_opens == 0, (
            f"the cache handle was built for an empty key in {mode!r} — `record` "
            f"mode exists to run with the graph engine stopped, and the cache has "
            f"no `node_key <> ''` constraint")


# ── 4. the coverage rule is CONDITIONAL, not a blanket refusal ─────────────


def test_the_abstention_is_a_COVERAGE_gap_not_a_refusal_to_answer(monkeypatch):
    """With BOTH coverage facts satisfied, the SAME miss is an established absence.

    ⛔ THE COMPLETION CLAIM MUST BE GREEN *AT* COMPLETION (#13353 review, finding
    1). This test therefore asserts only the repair side: patch BOTH facts to the
    state the fix direction names — the union covers the whole jurisdiction, and
    no address residual is left — and `{}` becomes the answer. It stays green
    before and after #9721 lands, because it never asserts anything about today.

    ⛔ AND THE STATE PATCHED IN HERE MUST BE ONE THE FIX DIRECTION PRODUCES. The
    previous cut patched the JURISDICTION down to `ENTITY_UNION_LABELS` —
    "narrow the question until it is satisfied", which no ticket proposes and
    which hid the fact that the predicate was `False` for every reachable
    configuration.
    """
    answer = rs.ServedAnswer(read=rs.READ_ENTITY_UNION_BY_ID, coordinate="c",
                             served_from="record", props=None)

    monkeypatch.setattr(rs, "ENTITY_UNION_LABELS", rs.ENTITY_UNION_JURISDICTION)
    monkeypatch.setattr(rs, "ENTITY_UNION_ADDRESS_RESIDUALS", {})
    assert rs.entity_coverage_complete() is True, (
        "a state that satisfies both declared facts must SATISFY the predicate — "
        "a completion claim whose completion state is red is not a claim anyone "
        "can act on")
    assert rs.entity_absent_wire(answer) == {}, (
        "with complete coverage the record's `no row` IS an absence, and the "
        "answer must be the FALSY empty dict (a verifier written as `if out:` "
        "must not read an absence as a success, #12941 review P0-1)")


def test_TODAYs_two_blockers_and_the_direction_they_must_move():
    """TODAY's state, and the repair direction — a change detector, not a rule.

    Kept SEPARATE from the completion claim above on purpose: this row asserts the
    present gap, so it is expected to be updated when the gap closes.

    ⛔ THE ONLY ACCEPTABLE GREENING IS A GROWN UNION. `#9721` landing adds
    `Event`/`Source` to `ENTITY_UNION_LABELS`; the retirement of the `Source`
    residual is the other fact. Taking a label back OUT of the union, or shrinking
    the jurisdiction to whatever the union serves, would green this row while
    making the door abstain for a label class for ever — a false PASS on exactly
    the gate #13093 is about (#13353 review, finding 1).

    ⛔ AND THE PIN IS NOT `KEY_PROPERTY == "id"`. That spelling is the conflation
    this PR removed ("addressable" reduced to "keyed on id"): it is false for
    `Event`, which a bare id DOES address.
    """
    assert set(rs.ENTITY_UNION_JURISDICTION) - set(rs.ENTITY_UNION_LABELS) == {
        "Source", "Event"}, (
        "today's parity gap (#9721) is Event + Source. If it has CLOSED, update "
        "this row — the union must have GROWN, not the jurisdiction shrunk")
    assert rs.ENTITY_UNION_ADDRESS_RESIDUALS, (
        "#9721 landing alone must NOT complete coverage: the cache resolves a "
        "Source from a bare `id` that the record can only address by url")
    assert rs.entity_coverage_complete() is False, (
        "coverage cannot be complete while either blocker stands — the `{}` "
        "arm must stay unreachable rather than be fabricated")

    answer = rs.ServedAnswer(read=rs.READ_ENTITY_UNION_BY_ID, coordinate="c",
                             served_from="record", props=None)
    assert rs.entity_absent_wire(answer) != {}, (
        "an unestablished absence answered falsy — a verifier written `if out:` "
        "would read a coverage gap as `the node does not exist`")


def test_the_coverage_domain_is_the_cache_resolvers_branch_labels():
    """The declared domain IS the labels a bare id can name — pinned to its owner.

    `ENTITY_UNION_JURISDICTION` is a local literal because `record_serve` imports
    no graph driver; this is the pin that stops it drifting from
    `_RESOLVE_BRANCHES`, the resolver whose branches make a label addressable at
    all. ⛔ Non-vacuous on the failure that produced #13322 review F2: the
    previous `entity_coverage_complete` measured `cache_projection.NODE_TABLES`,
    which carries `Session` — a label in NO branch. Re-measuring `NODE_TABLES`
    reddens this row.
    """
    from tortoise.cache_projection import NODE_TABLES
    from tortoise.projection import FalkorProjection

    branch_labels = tuple(dict.fromkeys(
        label for label, _prop in FalkorProjection._RESOLVE_BRANCHES))
    assert tuple(rs.ENTITY_UNION_JURISDICTION) == branch_labels, (
        "the coverage domain drifted from the resolver's own branch labels — "
        f"declared {rs.ENTITY_UNION_JURISDICTION}, resolver {branch_labels}")
    assert set(rs.ENTITY_UNION_JURISDICTION) <= set(NODE_TABLES), (
        "every addressable label must still be a record table")
    assert "Session" in NODE_TABLES, (
        "if Session ever leaves NODE_TABLES this row's premise changed")
    assert "Session" not in rs.ENTITY_UNION_JURISDICTION, (
        "#13322 F2: Session is in the record's table list but in no resolve "
        "branch — measuring it made the coverage predicate False forever")


def test_the_two_coverage_facts_are_SEPARATE_and_Event_is_not_a_residual():
    """Parity-fixable and re-addressing-fixable are DIFFERENT facts.

    #13322's second review sharpened this on the record, and it decides what the
    next lane does: a bare id DOES address an Event — `Event.id == eventId` for
    all current writes, and the record's `node_key` for an Event IS the eventId —
    so `Event`'s only gap is parity (#9721). A Source is the opposite: the cache
    resolves it by `id` and by `url`, while the record's key for it is
    `node_key = url`, so a bare Source *id* names a node the record read would
    miss. Folding the two into one condition is what let the previous predicate
    look reachable.

    ⛔ THESE ARE *TODAY'S* DECLARATIONS, AND THE DIRECTION THEY MUST MOVE IS THE
    ONE THAT GROWS THE UNION — never a jurisdiction shrunk to whatever the union
    happens to serve, which would green this row while making the door abstain
    for a label class for ever.

    ⛔ AND THE PIN IS ON THE MECHANISM, NOT ON `KEY_PROPERTY == "id"`: that
    spelling is the conflation this PR removed, and it would red at the state
    #9721 produces even though `Event` is genuinely addressable (#13353 review,
    finding 1).
    
    ⚠️ TODAY-PIN: this row states the state #13353 shipped in, and it is EXPECTED
    to be updated when #9721 lands. The only acceptable greening is a GROWN
    union — never a shrunken jurisdiction, and never a label taken back out.
"""
    from tortoise.cache_projection import KEY_PROPERTY

    assert KEY_PROPERTY.get("Event") == "eventId", (
        "Event's contract key is the eventId a bare id names — if this moves, "
        "re-measure whether a bare id still addresses an Event")
    assert KEY_PROPERTY.get("Source") == "url", (
        "Source's contract key is url (#5012), the identity a bare id cannot "
        "be assumed to carry")
    assert "Event" not in rs.ENTITY_UNION_ADDRESS_RESIDUALS, (
        "Event is ADDRESSABLE — its gap is parity (#9721). Putting it here "
        "would duplicate the parity list in the address map and tell the next "
        "lane the wrong repair")
    # ⛔ DERIVED, NOT A LITERAL (#13353 review, finding 2): the residual exists
    # because a label has a cache branch whose property is NOT its record contract
    # key. Measured off the two owners the rest of the module already reads, so a
    # resolver change that drops `("Source", "id")` reddens here instead of leaving
    # coverage False for ever on a stale entry.
    from tortoise.projection import FalkorProjection

    derived = {
        label for label in rs.ENTITY_UNION_JURISDICTION
        if {prop for lab, prop in FalkorProjection._RESOLVE_BRANCHES if lab == label}
        - {KEY_PROPERTY.get(label)}}
    assert set(rs.ENTITY_UNION_ADDRESS_RESIDUALS) == derived, (
        f"the residual set drifted from what the resolvers imply: declared "
        f"{sorted(rs.ENTITY_UNION_ADDRESS_RESIDUALS)}, derived {sorted(derived)}")
    assert "Source" in derived, (
        "if the resolver ever stops resolving a Source by `id`, revisit the "
        "residual AND the docstring that says a bare Source id is unaddressable")

    # ⛔ THE PIN THAT #9721 ALONE DOES NOT COMPLETE COVERAGE: with every label
    # served, the residual still blocks the absence verdict.
    from unittest import mock

    with mock.patch.object(rs, "ENTITY_UNION_LABELS", rs.ENTITY_UNION_JURISDICTION):
        assert rs.entity_coverage_complete() is False, (
            "with Event+Source served and the Source residual standing, coverage "
            "must still be False — otherwise the door claims an absence for a "
            "Source id it never asked about")

    # ⛔ THE OTHER HALF, WHICH NOTHING ELSE PINS (#13353 review cycle 3, finding
    # 1, MEASURED: deleting the completeness conjunct left the suite 29/29 green).
    # Without this row the predicate silently degrades to "no address residual",
    # so the moment #13351 retires that entry WITHOUT #9721 parity landing it
    # flips True — and answers `{}`, a claimed absence, for an `Event` the record
    # cannot reproduce. That is #13093 re-opened behind a green suite: a fail-open
    # on exactly the verdict this PR exists to make conservative.
    with mock.patch.object(rs, "ENTITY_UNION_ADDRESS_RESIDUALS", {}):
        assert set(rs.ENTITY_UNION_LABELS) < set(rs.ENTITY_UNION_JURISDICTION), (
            "this row is only discriminating while the union is INCOMPLETE — if "
            "#9721 has landed, MOVE the pin rather than delete it")
        assert rs.entity_coverage_complete() is False, (
            "with no address residual but an INCOMPLETE union, coverage must "
            "still be False — an unserved label can hold the node a bare id names")

def test_read_entity_addresses_each_label_by_its_OWN_contract_key():
    """The reader and the declaration agree STRUCTURALLY, not by accident.

    ⛔ THE PREVIOUS SPELLING SKIPPED ANY LABEL WHOSE KEY WAS NOT THE LITERAL
    `"id"`. That was two defects in one line: it threw away `Event`, whose
    record `node_key` IS the eventId a bare id names; and it made the coverage
    predicate's `KEY_PROPERTY == "id"` test agree with the reader only because
    both consulted the same accident, so "served" and "readable" were two
    different questions asked of one flag (#13322 review, finding 1).

    ⛔ AND THE BOUND VALUE IS ASSERTED, NOT JUST THE ROW (#13353 review, finding
    6): the fake cursor answers each table from its own row list whatever the
    bind params are, so "the row came back" alone cannot tell `key=id_val` from
    `key=<some property>`. A wrong address value would have stayed green.
    """
    conn = _TableConn({"source": [{"url": "url-1", "name": "a source"}],
                       "event": [{"event_id": "ev-1", "name": "decided"}]})

    source = rs.read_entity(conn, org_id=TENANT[0], graph_id=TENANT[1],
                            id_val="url-1", labels=("Source",))
    assert source is not None and source.get("name") == "a source", (
        f"a Source is addressed by its own contract key (url) — got {source!r}")
    assert conn.row_statements()[0][1][-1] == "url-1", (
        f"the Source row was anchored on {conn.row_statements()[0][1]!r}, not "
        f"on the bare value the door was handed")

    event = rs.read_entity(conn, org_id=TENANT[0], graph_id=TENANT[1],
                           id_val="ev-1", labels=("Event",))
    assert event is not None and event.get("name") == "decided", (
        "an Event is addressed by its own contract key (eventId) — the bare id "
        f"IS the eventId, got {event!r}")
    assert conn.row_statements()[-1][1][-1] == "ev-1", (
        f"the Event row was anchored on {conn.row_statements()[-1][1]!r}")


# ── the record's own pin on an Event's node_key, read append-only-safe ──────

#: The record's constraint pinning an `event`'s `node_key` to its `event_id`.
_EVENT_KEY_CONSTRAINT = "chk_event_node_key_is_contract_key"

#: What that constraint must say. Compared after normalisation, so a cosmetic
#: reflow is not a change.
_EVENT_KEY_CHECK = "(event_id is not null and node_key = event_id)"

#: The name as it appears in DDL — optionally DOUBLE-QUOTED (a re-key written
#: with a quoted identifier is the same constraint), and always followed by a
#: non-identifier character so a SIBLING name (`…_v2`) is never read as ours.
#: ⛔ Without the `(?![A-Za-z0-9_$])` boundary the `DROP` arm cleared the premise
#: on `DROP CONSTRAINT chk_event_node_key_is_contract_key_v2` — a false RED on a
#: constraint that is not this one.
_EVENT_KEY_NAME_RE = r"[\"']?" + _EVENT_KEY_CONSTRAINT + r"[\"']?(?![A-Za-z0-9_$])"

#: A `DROP CONSTRAINT` of this name — that half of the statement set has no body.
_EVENT_KEY_DROP_RE = re.compile(
    r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?" + _EVENT_KEY_NAME_RE,
    re.IGNORECASE)
#: ONE re-declaration (`CONSTRAINT <name> CHECK (…)`, in the landed `CREATE TABLE`
#: or behind a forward `ADD CONSTRAINT`), in file order. The body alternative
#: `(?:[^;']|'(?:[^']|'')*')*?` IS what lets a `;` sit inside the expression's own
#: string literal while a nested paren still reaches the real closing paren; a
#: trailing quote is allowed before the terminator because the house style for
#: conditional DDL is `DO $$ … EXECUTE '…' … $$`
#: (`20261025000001_record_tenancy_role_and_guc.sql:113,142`), where the literal
#: ends `'` before the `;`.
#: ⚠️ The corpus's `EXECUTE '…'` bodies are `CREATE ROLE` / `ALTER ROLE`
#: (`20261025000001_record_tenancy_role_and_guc.sql:113,142`) and
#: `ALTER TABLE … DISABLE/ENABLE TRIGGER`
#: (`20261023000002_blog_author_backfill_atomic.sql:91,109`) — no migration has
#: declared a CHECK through `EXECUTE` yet, so the arm that covers it is
#: forward-robustness rather than a spelling the tree already contains.
#:
#: ⛔ THE BODY MAY CROSS A `;` INSIDE A LITERAL, and the modifier list is not just
#: `NOT VALID`: `CHECK (node_key = 'a;b')` and a re-declaration carrying
#: `NO INHERIT` are ordinary DDL, and the first spelling of this pattern answered
#: both with the HISTORICAL expression (measured) — a false green in the exact
#: direction this premise exists for. The literal alternative
#: (`'(?:[^']|'')*'`) keeps the captured text verbatim, doubled quotes included.
_EVENT_KEY_DECL_RE = re.compile(
    r"(?:ADD\s+)?CONSTRAINT\s+(?P<name>" + _EVENT_KEY_NAME_RE
    + r")\s+CHECK\s*(?P<expr>\((?:[^;']|'(?:[^']|'')*')*?\))"
    r"\s*(?:(?:NOT\s+VALID|NO\s+INHERIT)\s*)*[\"']*[,;]",
    re.IGNORECASE | re.DOTALL)
_EVENT_KEY_RENAME_RE = re.compile(
    r"RENAME\s+CONSTRAINT\s+(?P<old>[\"']?[A-Za-z0-9_]+[\"']?)\s+TO\s+"
    r"(?P<new>[\"']?[A-Za-z0-9_]+[\"']?)",
    re.IGNORECASE)

#: A statement that removes the PREMISE **without naming the constraint**: the
#: table itself, or the `node_key` column the CHECK pins (`CASCADE` takes the
#: constraint with it). ⛔ `ALTER TABLE … DROP CONSTRAINT` IS NOT THE ONLY WAY IT
#: DIES — measured before this pattern existed, both shapes below left the reader
#: answering the HISTORICAL expression while the record enforced nothing, the same
#: false-green class the reader exists for.
#: The record table this premise lives on, as DDL spells it — schema-qualified or
#: not, and with EITHER part double-quoted (`"public"."event"` is the same table
#: as `public.event`; measured before this atom existed, the quoted spelling left
#: the row GREEN on a constraint the record no longer enforced).
_EVENT_KEY_TABLE_RE = r'(?:"?public"?\s*\.\s*)?"?event"?(?![A-Za-z0-9_$])'

#: ⛔ `COLUMN` IS OPTIONAL IN POSTGRES, AND THE KEYWORD LIST MUST MIRROR THE
#: GRAMMAR: `ALTER TABLE … DROP [COLUMN] node_key` drops the column — and the
#: CHECK with it — without the keyword. Measured before this pattern accepted it:
#: `ALTER TABLE public.event DROP node_key CASCADE` answered the HISTORICAL
#: expression while the record enforced nothing (a false green, the same class
#: this reader exists for). `DROP TABLE a, b` is likewise a comma LIST, so the
#: table may not be the first element.
_EVENT_KEY_OWNER_GONE_RE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:\S+\s*,\s*)*" + _EVENT_KEY_TABLE_RE
    + r"|ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?"
    + _EVENT_KEY_TABLE_RE
    + r'[^;]*?DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?"?node_key"?(?![A-Za-z0-9_$])',
    re.IGNORECASE | re.DOTALL)

#: A statement that MOVES the premise's owner without touching the constraint —
#: the table renamed, or the pinned `node_key` column renamed. Neither is a drop,
#: so `None` would be wrong too: the right answer is "the premise moved, re-derive
#: it", which is the raise the constraint-rename arm already produces. The repo
#: has precedent for exactly this spelling of a re-key
#: (`20260915000001_tenancy_team_to_org.sql:50-51`).
_EVENT_KEY_OWNER_MOVE_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?"
    + _EVENT_KEY_TABLE_RE
    + r'\s*RENAME\s+(?:TO\s+(?P<table>\S+?)'
    + r'|(?:COLUMN\s+)?"?node_key"?\s+TO\s+(?P<column>\S+?))'
    + r'(?=[\s;,)]|["\']|$)',
    re.IGNORECASE | re.DOTALL)


def _strip_sql_comments(sql: str) -> str:
    """Drop `--` line and `/* … */` block comments, WITHOUT touching literals.

    ⛔ A SIMPLE `re.sub(r"--[^\n]*", …)` WOULD EAT THE STRINGS THIS READER MUST
    SEE: the house style for conditional DDL carries the statement inside an
    `EXECUTE '…'` literal, and the reader has to read THAT text. So the scanner
    tracks single-quoted literals (including the `''` escape) and leaves their
    contents alone, which means a commented-out statement is dropped while an
    `EXECUTE`d one survives.

    ⚠️ A plain `'`-toggle is SUFFICIENT for that, with no special case for SQL's
    `''` escape: an escaped pair is PARITY-PRESERVING (two toggles), so the
    scanner leaves the pair in the same state it would reach by skipping it — and
    the only instant it could be wrong is BETWEEN the two quotes of a pair, where
    no `--` can appear. An escape branch here would be logic no input can
    distinguish (measured: deleting one changed no output for any arm below), so
    there is none to go stale.

    ⚠️ Dollar-quoted bodies are treated as CODE on purpose — that is where the
    `EXECUTE` carries real DDL — so a `--` comment inside one IS stripped, which
    is what the database does too. ⚠️ DOUBLE-quoted identifiers are not tracked
    (a `--` inside one would be stripped); the corpus has no such identifier, and
    the reader only ever looks at the constraint name and a CHECK expression.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    in_literal = False
    while i < n:
        ch = sql[i]
        if in_literal:
            out.append(ch)
            if ch == "'":
                in_literal = False
            i += 1
            continue
        if ch == "'":
            in_literal = True
            out.append(ch)
            i += 1
            continue
        if sql[i:i + 2] == "--":
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline
            continue
        if sql[i:i + 2] == "/*":
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


#: How an `EXECUTE`d literal is recognised: the token immediately before the
#: opening quote, WITH an identifier boundary. ⛔ The boundary matters even though
#: an identifier cannot be directly followed by a literal in valid SQL: without it
#: `xexecute '…'` is read as `EXECUTE` and the mask leaves a string it should
#: blank (measured — the docstring says "the token", so the code now means it).
_EXECUTE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_$])execute$", re.IGNORECASE)

#: A dollar-quote opener — `$$`, `$m$`, `$body$` …
_DOLLAR_TAG_RE = re.compile(r"\$(?P<tag>[A-Za-z0-9_]*)\$")

#: Tokens a BLOCK is introduced by. A dollar-quoted BODY (`DO $$ … $$`,
#: `CREATE FUNCTION … AS $$ … $$`) is DDL that carries more DDL inside it, so it
#: is NOT masked — it is scanned recursively, which is what lets a `RAISE NOTICE
#: $m$…$m$` INSIDE the body still be masked.
_BODY_INTRODUCERS = frozenset({"do", "as", "return"})


def _mask_plain_literals(sql: str) -> str:
    """Blank the CONTENTS of string literals that are not DDL the record runs.

    ⛔ A LITERAL THAT MERELY *MENTIONS* THE DDL MUST NOT MOVE THE PREMISE. This
    corpus documents replaced DDL in `COMMENT ON … IS '…'` and `RAISE NOTICE`
    prose, so a literal quoting `DROP CONSTRAINT …` or the OLD
    `ADD CONSTRAINT … CHECK (…)` is exactly the text a future migration is most
    likely to carry. Measured before this mask existed: a `COMMENT ON CONSTRAINT
    … IS 'DROP CONSTRAINT <name>;'` cleared the premise (false RED), and a real
    `DROP` followed by a `RAISE NOTICE` quoting the historical
    `ADD CONSTRAINT … CHECK (…)` returned that expression — a false GREEN, the
    class this whole reader exists for.

    WHAT IS KEPT, AND WHY THE TWO CASES CANNOT BE TOLD APART BY TEXT ALONE:

      * an `EXECUTE 'ALTER TABLE …'` literal — that IS the DDL, and the house
        style wraps conditional DDL in `DO $$ … EXECUTE '…' … $$`
        (`20261025000001_record_tenancy_role_and_guc.sql:113,142`,
        `20261023000002_blog_author_backfill_atomic.sql:91,109`);
      * a dollar-quoted BODY (`DO $$ … $$`, `… AS $$ … $$`) — it carries DDL
        inside it, so it is scanned RECURSIVELY instead of blanked, which is how
        a `RAISE NOTICE $m$…$m$` inside the body is still masked.

    ⛔ BOTH LITERAL KINDS ARE COVERED. The first cut handled single quotes only
    and left `$tag$` strings readable, which re-opened the same false green one
    quote style away (measured: a real `DROP CONSTRAINT` followed by
    `RAISE NOTICE $m$was: ADD CONSTRAINT … CHECK (…) $m$` returned the
    historical expression).

    ⚠️ POSITION-PRESERVING: contents become spaces (newlines kept), the quotes and
    dollar tags stay, and the length never changes — so a caller can match on the
    masked text and read the REAL expression back out of the original by the same
    span. `''` escape pairs are masked as part of the contents, which is what
    keeps `CHECK (node_key = 'a;b')` matchable and recoverable verbatim.
    """
    out = list(sql)

    def blank(start: int, stop: int) -> None:
        for k in range(start, stop):
            if out[k] != "\n":
                out[k] = " "

    def last_token(before: str) -> str:
        stripped = before.rstrip()
        return stripped.rsplit(None, 1)[-1].lower() if stripped else ""

    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            end = i + 1
            while end < n:
                if sql[end] == "'":
                    if sql[end + 1:end + 2] == "'":
                        end += 2
                        continue
                    break
                end += 1
            if not _EXECUTE_TOKEN_RE.search(sql[:i].rstrip()):
                blank(i + 1, min(end, n))
            i = end + 1
            continue
        if ch == "$":
            opener = _DOLLAR_TAG_RE.match(sql, i)
            if opener is not None:
                tag = opener.group(0)
                close = sql.find(tag, opener.end())
                if close != -1:
                    token = last_token(sql[:i])
                    if token == "execute":
                        i = close + len(tag)          # the DDL itself — keep
                        continue
                    if token in _BODY_INTRODUCERS:
                        inner = _mask_plain_literals(sql[opener.end():close])
                        for offset, masked_char in enumerate(inner):
                            out[opener.end() + offset] = masked_char
                    else:
                        blank(opener.end(), close)    # a STRING — blank it
                    i = close + len(tag)
                    continue
        i += 1
    return "".join(out)


def _effective_event_node_key_check(sql: str) -> str | None:
    """The `node_key = …` expression the record ENFORCES, or `None` if dropped.

    ⛔ **The LAST statement wins.** Migrations are append-only history, so a
    re-key arrives as a FORWARD `ALTER TABLE … DROP/ADD CONSTRAINT` — never as an
    edit of the landed `CREATE TABLE`. That is the rule
    `record_projection.key_properties` already applies to `node_identity`
    (`#9881`: a key move arrives as a forward `ALTER`, and the last declaration
    is the enforced one), and it is simply what the database enforces: a `CHECK`
    is the expression of its LAST declaration.

    ⛔ It is NOT an external Alembic convention, and must not be cited as one:
    alembic's autogenerate compares a named `CHECK` **by name only** and never
    detects an expression change at all
    (https://alembic.sqlalchemy.org/en/latest/autogenerate.html — "the detection
    is name-based only … always presumed to be equivalent, regardless of any
    difference in their expression text"). What that documentation does support
    is narrow: a REFLECTED expression cannot be compared textually, because
    dialects normalize and rewrite the DDL — which is why the premise is read
    from the migrations' effective statement, not from a reflection.

    ⛔ A substring search over the corpus finds the HISTORICAL expression for
    ever, so it stays GREEN on the one mutation this premise exists to catch —
    the false green measured in this PR's first review
    (https://github.com/daniel-ospina/tortoise/pull/13358#issuecomment-6963029739,
    §3.1 P1): a forward re-key left the `Event` row green with its justification
    gone. #13347's own body carries F-A/F-B from #13322's review; the
    forward-re-key false green is this PR's, not that issue's.

    Returns the normalised expression (whitespace collapsed, spaces inside the
    parentheses trimmed, lowercased), or `None` when a later statement drops the
    constraint so nothing is enforced.

    ⛔ TWO STATES RAISE, because in both the premise's OWNER moved and a silent
    answer would be read as a fact:

      * no migration names the constraint at all (the landed `CREATE TABLE`
        edited away or the migration set replaced);
      * a `RENAME CONSTRAINT` either FROM or TO this name. ⛔ A rename is NOT
        caught by the first `assert`: the landed `CREATE TABLE` keeps the
        original name for ever in append-only history, so `events` is never empty
        through a rename — and without this arm the reader would return the
        HISTORICAL expression, a false green (measured before the arm existed).

    ⚠️ SEEN, NOT OMNISCIENT: it reads LITERAL statements, so the re-key spellings
    this repo's house style produces are covered — a plain `ALTER TABLE`, a
    double-quoted identifier, and an `EXECUTE '…'` inside `DO $$ … $$`. ⚠️ The
    shape itself has NO corpus precedent for a CHECK: no migration declares one
    through `EXECUTE`, so that arm is forward-robustness, not a spelling the tree
    already contains (the corpus's `EXECUTE 'ALTER TABLE …'` uses are
    `20261023000002_blog_author_backfill_atomic.sql:91,109`, DISABLE/ENABLE
    TRIGGER). A re-key assembled at RUNTIME from parts (`format()` into a
    variable, a name built from a catalog query) is invisible here and is NOT
    claimed to be covered; a migration written that way must update this reader
    deliberately.

    ⚠️ A LITERAL THAT MERELY *MENTIONS* THE DDL CANNOT MOVE THE PREMISE: every
    statement scan runs on text whose NON-`EXECUTE` literal contents are blanked
    (`_mask_plain_literals`), so a `COMMENT ON … IS 'DROP CONSTRAINT …'` or a
    `RAISE NOTICE` quoting the OLD expression is inert — while an `EXECUTE 'ALTER
    TABLE …'` literal is still read, because that IS the DDL. ⚠️ The residual is
    the flip side of that rule: DDL mentioned inside an `EXECUTE`d literal is read
    as DDL, which is what makes the conditional-DDL house style work and is the
    reason the two cases cannot be told apart by text alone.

    ⚠️ TWO LIMITS THIS READER DOES NOT MODEL, both stated rather than implied:
    a `$tag$`-quoted STRING is not distinguished from a dollar-quoted BODY, and
    an `E'…'` backslash escape is not modelled — so an apostrophe inside one
    (e.g. `RAISE NOTICE $m$don't$m$;`) flips the comment scanner's literal state
    for the rest of the file, which can make a COMMENTED-OUT statement read as
    DDL (measured: false red). No record migration has an apostrophe inside
    either today — the two `E'…'` escapes that DO exist
    (`20261102000002_record_point_search_keys_text.sql:139,143`, `E' \t\n\r\v\f'`)
    contain none.

    ⚠️ It DOES model the premise dying or MOVING without a `DROP CONSTRAINT`: the
    table dropped, `node_key` dropped (which cascades the CHECK), the table or the
    column RENAMEd (a raise, like a constraint rename), and a `CREATE TABLE` that
    re-declares the constraint as its last element. It does NOT model a
    `DROP SCHEMA` or a `SET SCHEMA` move of the table, which would move the
    premise's owner without touching any of those statements.

    ⚠️ "LAST" MEANS LAST IN `RECORD_MIGRATIONS`, WHICH IS HAND-MAINTAINED LIST
    ORDER AND NOT FILE-NAME SORTED (measured: the list is not `sorted()` — two
    entries are out of date order). Today only one migration names this
    constraint, so the answer is unambiguous; a re-key must be APPENDED TO THE END
    of that list, or "last" is the wrong last.

    ⚠️ A RENAME raises CONSERVATIVELY: a rename FROM this name, a rename TO it,
    and a rename away-and-back all raise, and the last two leave the premise
    where it was. That is accepted rather than followed — a reader that tracks
    renames properly is more machinery than this one premise needs, and a raise
    cannot be mistaken for a fact, which the silence it replaced could.
    """
    events: list[tuple[int, str, str | None, str | None]] = []
    # ⛔ COMMENTS ARE NOT DDL, and this corpus WRITES DDL IN COMMENTS: a
    # commented `ALTER TABLE` is house style
    # (`20261023000002_blog_author_backfill_atomic.sql:7,10`), and
    # `20261014000001_record_subject_props_no_derived.sql:41` names a
    # `DROP CONSTRAINT`/`ADD CONSTRAINT` pair in prose. Measured before this
    # strip: a commented `DROP CONSTRAINT <name>` returned `None` (a FALSE RED on
    # a premise that never moved) and a commented `ADD CONSTRAINT … CHECK (…)`
    # after a real drop returned the unenforced expression (a FALSE GREEN on
    # exactly the class this reader exists for).
    sql = _strip_sql_comments(sql)
    # ⛔ ONE VIEW, POSITION-PRESERVING: every scan runs on the masked text, and the
    # captured expression is read back out of the ORIGINAL by the same span, so a
    # literal inside the expression survives verbatim while a literal that merely
    # MENTIONS the DDL cannot move the premise.
    masked = _mask_plain_literals(sql)
    for match in _EVENT_KEY_DROP_RE.finditer(masked):
        events.append((match.start(), "drop", None, None))
    for match in _EVENT_KEY_DECL_RE.finditer(masked):
        events.append((match.start(), "decl",
                       sql[match.start("expr"):match.end("expr")], None))
    for match in _EVENT_KEY_OWNER_GONE_RE.finditer(masked):
        events.append((match.start(), "drop", None, None))
    for match in _EVENT_KEY_OWNER_MOVE_RE.finditer(masked):
        # ⛔ STRIP THE QUOTES: a quoted target (`RENAME TO "Event_V2"`) must report
        # the name the DDL names, not `"Event_V2`.
        target = (match.group("table") or match.group("column")).strip('"')
        moved = (f"table `public.event` -> `{target}`"
                 if match.group("table") else f"column `node_key` -> `{target}`")
        events.append((match.start(), "rename", moved, "owner-move"))
    for match in _EVENT_KEY_RENAME_RE.finditer(masked):
        old = match.group("old").strip('"').lower()
        new = match.group("new").strip('"').lower()
        if _EVENT_KEY_CONSTRAINT in (old, new):
            events.append((match.start(), "rename", f"{old} -> {new}",
                           "constraint-rename"))
    events.sort(key=lambda event: event[0])

    assert events, (
        f"no migration names `{_EVENT_KEY_CONSTRAINT}` at all — the landed "
        "`CREATE TABLE` no longer declares it, so the premise that makes "
        "`Event` addressable by a bare id has been removed: re-derive "
        "ENTITY_UNION_ADDRESS_RESIDUALS. (A RENAME is a different state and is "
        "reported by its own assertion below.)")

    expr: str | None = None
    for _pos, kind, body, cause in events:
        # ⛔ THE MESSAGE NAMES THE CAUSE THAT ACTUALLY HAPPENED. A table or column
        # rename is not a constraint rename, and sending a lane to "the renamed
        # constraint" sends it to something that does not exist.
        assert kind != "rename", (
            (
                f"a migration MOVED the premise's owner ({body}) — `public.event` "
                "/ `node_key` is no longer the table/column `read_entity` "
                "addresses, and the expression the record enforces can no longer "
                "be read from the statements carrying this name: re-derive "
                "ENTITY_UNION_ADDRESS_RESIDUALS"
            ) if cause == "owner-move" else (
                f"a migration RENAMEs `{_EVENT_KEY_CONSTRAINT}` ({body}) — this "
                "reader does not follow renames, so the expression enforced under "
                "this name can no longer be read from the statements carrying it. "
                "If the constraint was renamed BACK, teach this reader to follow "
                "the rename; otherwise re-derive "
                "ENTITY_UNION_ADDRESS_RESIDUALS against the renamed constraint"
            ))
        if kind == "drop":
            expr = None
            continue
        assert body is not None  # `decl` always captures its expression
        # ⛔ TRUNCATE AT THE FIRST BALANCED CLOSE. When a `CREATE TABLE` declares
        # this constraint as its LAST element, the body alternative has to run
        # past the CHECK's own closing paren to reach the `,`/`;` terminator and
        # swallows the TABLE's `)` too — measured as a false red on an unchanged
        # expression. A balance check, not a tighter body, because the same
        # tolerance is what lets a nested-paren expression read correctly.
        # ⛔ LITERAL-AWARE, because a literal can carry an unbalanced paren:
        # `CHECK (payload <> ')')` and `CHECK (node_key = '(ev')` both defeated a
        # plain depth count — the first was cut INSIDE the literal, the second
        # re-attached the table's `)` (measured; both false reds).
        depth = 0
        in_literal = False
        for idx, ch in enumerate(body):
            if in_literal:
                if ch == "'":
                    in_literal = False
                continue
            if ch == "'":
                in_literal = True
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    body = body[:idx + 1]
                    break
        normalised = " ".join(body.split()).lower()
        normalised = re.sub(r"\(\s+", "(", normalised)
        expr = re.sub(r"\s+\)", ")", normalised)
    return expr


def test_the_comment_stripper_keeps_literals_and_drops_comments():
    """The scanner's OWN contract, arm by arm — a guard that cannot fail is not one.

    The premise reader strips comments so a commented-out `ALTER TABLE` cannot
    move the premise (this corpus writes DDL in comments). Two properties make
    that safe, and both are measured here rather than assumed:

      * a `--` INSIDE a single-quoted literal is DATA, not a comment — including
        one that follows SQL's `''` escape, or the reader would truncate the
        `EXECUTE '…'` body it exists to read (measured against a naive strip:
        `'it''s -- fine'` loses everything after `--`);
      * a `--` comment OUTSIDE a literal, and a `/* … */` block, are removed.
    """
    assert _strip_sql_comments("SELECT 'a -- b', 1; -- tail\nSELECT 2;") == (
        "SELECT 'a -- b', 1; \nSELECT 2;"), (
        "a `--` inside a literal was eaten, or a real trailing comment survived")
    assert _strip_sql_comments(
        "DO $$ BEGIN EXECUTE 'ALTER TABLE t ADD CONSTRAINT c "
        "CHECK (x = ''v'')'; END $$;") == (
        "DO $$ BEGIN EXECUTE 'ALTER TABLE t ADD CONSTRAINT c "
        "CHECK (x = ''v'')'; END $$;"), (
        "the `EXECUTE '…'` body carrying the DDL was altered by the stripper")
    assert _strip_sql_comments("SELECT 'it''s -- fine';") == (
        "SELECT 'it''s -- fine';"), (
        "the `''` escape was not honoured: everything after `--` inside the "
        "literal was dropped, which would truncate a re-key expression")
    assert _strip_sql_comments("/* block -- x */ SELECT 3;") == " SELECT 3;", (
        "a block comment survived (or its contents were not dropped)")


def test_the_event_key_premise_reader_sees_a_FORWARD_re_key():
    """The premise row must red on the SANCTIONED way a re-key arrives.

    This PR's first review measured the original assertion as a FALSE GREEN: it
    regex-searched the concatenation of all record migrations, so a forward
    `ALTER TABLE … DROP/ADD CONSTRAINT` — how a re-key is actually allowed to
    arrive — left it green while an edit of the already-landed `CREATE TABLE`
    (which this repo forbids) was the only shape it caught
    (PR #13358 review §3.1). The arms below measure the forward shapes the repo's
    house style produces, the sibling name that must NOT move it, and the
    formatting that must not be mistaken for one.
    """
    from tortoise import record_projection as _rp

    landed = _rp._read_migrations(None)
    assert _effective_event_node_key_check(landed) == _EVENT_KEY_CHECK, (
        "the landed corpus does not read back as the expected premise, so the "
        "arms below would prove nothing")

    re_keyed_sql = "CHECK (event_id IS NOT NULL AND node_key = payload ->> 'ref')"
    #: The expression the record ENFORCES after that re-key. Asserted EXACTLY, not
    #: as `!= _EVENT_KEY_CHECK`: a reader that mis-read the re-key as a plain DROP
    #: returns `None`, which satisfies inequality while being just as wrong.
    re_keyed_expr = "(event_id is not null and node_key = payload ->> 'ref')"

    # 1. A forward re-key, the way the repo allows one. The last declaration
    #    moved, so the premise reader must move with it.
    re_keyed = landed + (
        "\nALTER TABLE public.event\n"
        f"    DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT};\n"
        "ALTER TABLE public.event\n"
        f"    ADD CONSTRAINT {_EVENT_KEY_CONSTRAINT}\n"
        f"    {re_keyed_sql};\n")
    assert _effective_event_node_key_check(re_keyed) == re_keyed_expr, (
        "a forward `ALTER TABLE … DROP/ADD CONSTRAINT` re-key did not move the "
        "premise reader to the expression the record now enforces — the `Event` "
        "row would stay green with its justification gone (the false green this "
        "PR's first review caught)")

    # 1b. The SAME re-key with the constraint name DOUBLE-QUOTED — a legal
    #     spelling of the same DDL, and one the first reader missed entirely
    #     (measured: it returned the HISTORICAL expression, i.e. the row stayed
    #     green through a real re-key).
    quoted = landed + (
        "\nALTER TABLE public.event\n"
        f"    DROP CONSTRAINT \"{_EVENT_KEY_CONSTRAINT}\";\n"
        "ALTER TABLE public.event\n"
        f"    ADD CONSTRAINT \"{_EVENT_KEY_CONSTRAINT}\"\n"
        f"    {re_keyed_sql};\n")
    assert _effective_event_node_key_check(quoted) == re_keyed_expr, (
        "a double-quoted identifier hid a forward re-key — the reader did not "
        "reach the expression the record now enforces")

    # 1c. The house style for CONDITIONAL DDL: the statement inside
    #     `DO $$ … EXECUTE '…' … $$` — that corpus writes `EXECUTE 'CREATE ROLE …'` /
    #     `'ALTER ROLE …'` (`20261025000001_record_tenancy_role_and_guc.sql:113,142`)
    #     and `EXECUTE 'ALTER TABLE … DISABLE/ENABLE TRIGGER'`
    #     (`20261023000002_blog_author_backfill_atomic.sql:91,109`), and no
    #     migration has declared a CHECK this way, so this arm is
    #     FORWARD-ROBUSTNESS. The literal ends `'` before the `;`, and the first
    #     reader could not see past it.
    dynamic = landed + (
        "\nDO $$ BEGIN\n"
        f"  EXECUTE 'ALTER TABLE public.event DROP CONSTRAINT "
        f"{_EVENT_KEY_CONSTRAINT}';\n"
        "  EXECUTE 'ALTER TABLE public.event ADD CONSTRAINT "
        f"{_EVENT_KEY_CONSTRAINT} "
        "CHECK (event_id IS NOT NULL AND node_key = payload ->> ''ref'')';\n"
        "END $$;\n")
    #: The doubled single quotes are the SQL-literal escaping INSIDE the
    #: `EXECUTE '…'` string; the reader reports the statement's own text.
    assert _effective_event_node_key_check(dynamic) == (
        "(event_id is not null and node_key = payload ->> ''ref'')"), (
        "an `EXECUTE '…'` re-key inside a `DO $$` block was invisible — the "
        "reader did not reach the expression the record now enforces")

    # 1c-bis. Two ordinary re-declaration shapes the first pattern mis-read:
    #     a CHECK modifier other than `NOT VALID` (`NO INHERIT` is the same
    #     family) and a `;` INSIDE the expression's own string literal. Measured
    #     before this fix: both answered the HISTORICAL expression (a false green).
    no_inherit = landed + (
        f"\nALTER TABLE public.event DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT};\n"
        f"ALTER TABLE public.event ADD CONSTRAINT {_EVENT_KEY_CONSTRAINT} "
        f"    {re_keyed_sql} NO INHERIT;\n")
    assert _effective_event_node_key_check(no_inherit) == re_keyed_expr, (
        "a `NO INHERIT` re-declaration was not read — the modifier list must "
        "not be limited to `NOT VALID`")
    semicolon = landed + (
        f"\nALTER TABLE public.event DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT};\n"
        f"ALTER TABLE public.event ADD CONSTRAINT {_EVENT_KEY_CONSTRAINT}"
        "     CHECK (node_key = 'a;b');\n")
    assert _effective_event_node_key_check(semicolon) == "(node_key = 'a;b')", (
        "a `;` inside the expression's own string literal stopped the match — a "
        "legal CHECK was read as no re-key at all")

    # 1d. A SIBLING name is not this constraint: `…_v2` must NOT move the reader.
    #     Without an identifier boundary the `DROP` arm cleared the premise and
    #     the row reddened with a message sending a lane to re-derive an
    #     invariant that had not moved (measured on the first reader).
    sibling = landed + (
        "\nALTER TABLE public.event\n"
        f"    DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT}_v2;\n")
    assert _effective_event_node_key_check(sibling) == _EVENT_KEY_CHECK, (
        "a `DROP CONSTRAINT " + _EVENT_KEY_CONSTRAINT + "_v2` cleared OUR "
        "premise — a sibling name is not this constraint")

    # 1e. A RENAME moves the premise's owner, and append-only history keeps the
    #     landed name for ever, so it can be neither `None` nor the historical
    #     expression: it must RAISE (measured before this arm existed: the first
    #     reader returned the HISTORICAL expression, a false green).
    renamed = landed + (
        "\nALTER TABLE public.event\n"
        f"    RENAME CONSTRAINT {_EVENT_KEY_CONSTRAINT} TO "
        f"{_EVENT_KEY_CONSTRAINT}_v2;\n")
    with pytest.raises(AssertionError, match="RENAMEs"):
        _effective_event_node_key_check(renamed)

    # 1f. COMMENTS ARE NOT DDL. This corpus writes DDL in comments (a commented
    #     `ALTER TABLE` is house style:
    #     `20261023000002_blog_author_backfill_atomic.sql:7,10`), so the reader
    #     strips comments — outside literals only, because an `EXECUTE`d statement
    #     must stay visible. Measured before the strip, both directions were wrong:
    #     a commented `DROP CONSTRAINT <name>` returned `None` (false RED) and a
    #     commented re-key after a real drop returned the unenforced expression
    #     (false GREEN).
    commented_drop = landed + (
        f"\n-- we deliberately do NOT run: DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT};\n")
    assert _effective_event_node_key_check(commented_drop) == _EVENT_KEY_CHECK, (
        "a COMMENTED-OUT `DROP CONSTRAINT` cleared the premise — a false red "
        "that would send a lane to re-derive an invariant that never moved")
    commented_key = landed + (
        f"\nALTER TABLE public.event DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT};\n"
        f"-- rejected re-key: ADD CONSTRAINT {_EVENT_KEY_CONSTRAINT} "
        "CHECK (node_key = payload ->> 'ref');\n")
    assert _effective_event_node_key_check(commented_key) is None, (
        "a COMMENTED-OUT re-key was read as the enforced expression — the premise "
        "would be pinned to DDL the record never ran")

    # 1g. THE PREMISE DIES WITHOUT A `DROP CONSTRAINT` TOO — the table dropped, or
    #     the `node_key` column dropped (CASCADE takes the CHECK with it). Measured
    #     before this arm existed: both shapes left the row GREEN on the historical
    #     expression while the record enforced nothing.
    for gone in ("\nDROP TABLE public.event;\n",
                 "\nALTER TABLE public.event DROP COLUMN node_key CASCADE;\n",
                 # ⛔ a QUOTED, schema-qualified name is the SAME table; measured
                 # before the table atom accepted quotes, this left the row green.
                 '\nDROP TABLE "public"."event";\n',
                 '\nALTER TABLE "public"."event" DROP COLUMN node_key CASCADE;\n',
                 # `COLUMN` is OPTIONAL (`DROP [COLUMN]`), and `DROP TABLE` takes
                 # a comma LIST — the table need not be first. Both measured green
                 # on the historical expression before the patterns accepted them.
                 "\nALTER TABLE public.event DROP node_key CASCADE;\n",
                 "\nDROP TABLE public.foo, public.event;\n",
                 # the schema qualifier and `IF EXISTS` are OPTIONAL, and `ONLY`
                 # is legal — each of these was unpinned before this round.
                 "\nDROP TABLE event;\n",
                 "\nDROP TABLE IF EXISTS public.event;\n",
                 "\nALTER TABLE event DROP node_key CASCADE;\n",
                 "\nALTER TABLE ONLY public.event DROP COLUMN node_key CASCADE;\n"):
        assert _effective_event_node_key_check(landed + gone) is None, (
            f"`{gone.strip()}` removes the constraint without naming it, and the "
            "reader still reported an expression the record no longer enforces")

    # 1h. A MOVE IS NOT A DROP: renaming the table or the pinned column moves the
    #     premise's owner, so the reader must RAISE (as it does for a constraint
    #     rename), never answer the historical expression and never `None`. The
    #     repo writes re-keys this way (`20260915000001_tenancy_team_to_org.sql:50-51`).
    #     ⛔ AND THE MESSAGE NAMES THE CAUSE THAT HAPPENED: a table/column move is
    #     not a constraint rename, and "the renamed constraint" sends a lane to
    #     something that does not exist.
    for moved in ("\nALTER TABLE public.event RENAME TO event_v2;\n",
                  "\nALTER TABLE public.event RENAME COLUMN node_key TO key;\n",
                  # `COLUMN` is OPTIONAL in PostgreSQL — measured before the
                  # pattern accepted it, this spelling answered the historical
                  # expression (a false green).
                  "\nALTER TABLE public.event RENAME node_key TO key;\n",
                  "\nALTER TABLE ONLY public.event RENAME TO ev2;\n"):
        with pytest.raises(AssertionError, match="MOVED the premise's owner"):
            _effective_event_node_key_check(landed + moved)

    # 1h-bis. A LITERAL THAT MENTIONS THE DDL IS INERT. Every statement scan runs
    #     on `_mask_plain_literals`' output, so a `COMMENT ON … IS '…'`, a
    #     `RAISE NOTICE '…'` and a `RAISE NOTICE $m$…$m$` quoting the constraint
    #     cannot move the premise — while an `EXECUTE 'ALTER TABLE …'` literal,
    #     which IS the DDL, still can. Both directions are measured below:
    #     "the mention is inert" AND "prose cannot re-install a dropped premise".
    mention = landed + (
        f"\nCOMMENT ON CONSTRAINT {_EVENT_KEY_CONSTRAINT} ON public.event\n"
        f"    IS 'DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT};';\n")
    assert _effective_event_node_key_check(mention) == _EVENT_KEY_CHECK, (
        "a literal that MENTIONS a drop moved the premise — `COMMENT ON … IS` "
        "and `RAISE NOTICE` prose is how this corpus documents DDL, so this is "
        "the text a future migration is most likely to carry")
    # ⛔ AND THE DANGEROUS DIRECTION: a real drop, then prose quoting the OLD
    #     expression. Before the mask this returned the historical expression and
    #     the row stayed GREEN while the record enforced nothing.
    stale_prose = landed + (
        f"\nALTER TABLE public.event DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT};\n"
        "DO $$ BEGIN RAISE NOTICE 'replaced " + _EVENT_KEY_CONSTRAINT + " "
        f"CHECK {_EVENT_KEY_CHECK[1:-1]}'; END $$;\n")
    assert _effective_event_node_key_check(stale_prose) is None, (
        "prose quoting the OLD expression re-installed the premise after a real "
        "drop — the record enforces nothing there, so the row must NOT stay green")
    #     ⛔ AND THE SAME IN EVERY LITERAL KIND. The first mask handled single
    #     quotes only, so `$m$` prose re-opened the false green one quote style
    #     away (measured). A dollar-quoted BODY (`DO $$ … $$`) is still SCANNED —
    #     it carries DDL — but the STRING inside it is masked.
    dollar_stale = landed + (
        f"\nALTER TABLE public.event DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT};\n"
        "DO $$ BEGIN RAISE NOTICE $m$was: "
        f"ADD CONSTRAINT {_EVENT_KEY_CONSTRAINT} CHECK {_EVENT_KEY_CHECK[1:-1]}"
        "; $m$; END $$;\n")
    assert _effective_event_node_key_check(dollar_stale) is None, (
        "a dollar-quoted literal quoting the OLD expression re-installed the "
        "premise after a real drop — the mask must cover `$tag$` strings too")
    for prose in ("we no longer run: DROP CONSTRAINT " + _EVENT_KEY_CONSTRAINT,
                  "old: RENAME CONSTRAINT " + _EVENT_KEY_CONSTRAINT + " TO x"):
        assert _effective_event_node_key_check(
            landed + f"\nDO $$ BEGIN RAISE NOTICE $m${prose}; $m$; END $$;\n"
        ) == _EVENT_KEY_CHECK, (
            f"a dollar-quoted literal mentioning `{prose}` moved the premise")
    #     ⛔ AND THE `EXECUTE` TOKEN NEEDS A BOUNDARY: `xexecute '…'` is not
    #     `EXECUTE`, and a mask that reads it as one leaves a string it should
    #     blank (measured on the mask directly).
    assert _strip_sql_comments("SELECT 1") == "SELECT 1"
    not_execute = landed + (
        f"\nSELECT xexecute 'DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT}';\n")
    assert _effective_event_node_key_check(not_execute) == _EVENT_KEY_CHECK, (
        "an identifier merely ending in `execute` exempted a literal from the "
        "mask")

    #     ⛔ AND AN `EXECUTE`-WRAPPED MOVE MUST NOT SWALLOW THE LITERAL'S CLOSING
    #     QUOTE: the name atom is non-greedy, so a lookahead that omits `'` lets
    #     `\S+?` run to the `;` and the message reports `event_v2'` — a target
    #     that appears in no DDL, which is the message a lane would act on.
    with pytest.raises(
            AssertionError,
            match=r"MOVED the premise's owner \(table `public\.event` -> "
                  r"`event_v2`\)") as excinfo:
        _effective_event_node_key_check(
            landed + "\nDO $$ BEGIN EXECUTE 'ALTER TABLE public.event "
                     "RENAME TO event_v2'; END $$;\n")
    assert "event_v2'" not in str(excinfo.value), (
        f"the moved-to name carried the literal's quote — {excinfo.value}")

    #     ⛔ AND A QUOTED TARGET REPORTS THE NAME, NOT THE QUOTES: `"Event_V2"` is
    #     the same name as `Event_V2` (the constraint-name branch already strips),
    #     so a diagnostic carrying `"Event_V2` names no DDL either.
    with pytest.raises(AssertionError, match="MOVED the premise's owner") as qexc:
        _effective_event_node_key_check(
            landed + '\nALTER TABLE public.event RENAME TO "Event_V2";\n')
    assert "`Event_V2`" in str(qexc.value) and '"Event_V2' not in str(qexc.value), (
        f"the quoted target kept its quote — {qexc.value}")

    # 1h-ter. THE EMPTY CASE RAISES rather than answering `None` silently: a
    #     corpus that names the constraint nowhere means the premise's owner was
    #     renamed or removed, not that the record enforces nothing.
    with pytest.raises(AssertionError, match="no migration names"):
        _effective_event_node_key_check("")

    # 1i. A `CREATE TABLE` that declares the constraint as its LAST element: the
    #     body must stop at its OWN balanced close, not swallow the table's `)`.
    #     Measured before the truncation: the expression came back as
    #     `(node_key = event_id))` — a false red on an unchanged declaration.
    recreated = landed + (
        "\nCREATE TABLE public.event (\n    event_id text NOT NULL,\n"
        "    node_key text NOT NULL,\n"
        f"    CONSTRAINT {_EVENT_KEY_CONSTRAINT} "
        f"    {re_keyed_sql});\n")
    assert _effective_event_node_key_check(recreated) == re_keyed_expr, (
        "a re-declaration whose CHECK is the LAST element of the CREATE TABLE "
        "was read with the table's closing paren attached")

    # 1j. THE BALANCED-CLOSE TRUNCATION IS LITERAL-AWARE, because a literal can
    #     carry an UNBALANCED paren. Measured with a plain depth count: an
    #     expression was cut INSIDE the literal (`(payload <>)`), and the
    #     last-element shape re-attached the table's `)` — both false reds.
    unbalanced = landed + (
        f"\nALTER TABLE public.event DROP CONSTRAINT {_EVENT_KEY_CONSTRAINT};\n"
        f"ALTER TABLE public.event ADD CONSTRAINT {_EVENT_KEY_CONSTRAINT} "
        "CHECK (payload <> ')');\n")
    assert _effective_event_node_key_check(unbalanced) == "(payload <> ')')", (
        "a `)` inside a literal closed the expression early")
    recreate_literal = landed + (
        "\nCREATE TABLE public.event (event_id text NOT NULL, "
        "node_key text NOT NULL, "
        f"CONSTRAINT {_EVENT_KEY_CONSTRAINT} CHECK (node_key = '(ev'));\n")
    assert _effective_event_node_key_check(recreate_literal) == (
        "(node_key = '(ev')"), (
        "a `(` inside a literal stopped the depth count from ever reaching zero "
        "— the table's closing paren got attached again")

    # 2. A forward DROP with no re-declaration. The constraint is GONE, so no
    #    expression is enforced and the premise is not merely different — it is
    #    absent, which is the answer the addressability declaration must not
    #    survive.
    dropped = landed + (
        "\nALTER TABLE public.event\n"
        f"    DROP CONSTRAINT IF EXISTS {_EVENT_KEY_CONSTRAINT};\n")
    assert _effective_event_node_key_check(dropped) is None, (
        "a forward `DROP CONSTRAINT` left the reader reporting an expression "
        "the record no longer enforces")

    # 3. NOT formatting-coupled: a cosmetic reflow inside the parentheses is the
    #    SAME declaration and must not red this with a message that sends a lane
    #    to re-derive an invariant that did not move.
    reflowed, hits = re.subn(
        r"CHECK\s*\(" + re.escape(_EVENT_KEY_CHECK[1:-1]) + r"\)",
        "CHECK (\n      event_id IS NOT NULL\n      AND node_key = event_id\n  )",
        landed, count=1, flags=re.IGNORECASE)
    assert hits == 1, (
        "the reflow fixture no longer matches the corpus — update it rather "
        "than deleting this arm")
    assert _effective_event_node_key_check(reflowed) == _EVENT_KEY_CHECK, (
        "a cosmetic reflow inside the CHECK reds the premise reader")


def test_an_EVENT_is_addressable_by_the_bare_id_that_IS_its_eventId():
    """The RECORD-side premise of "Event is addressable", read from the DDL.

    `ENTITY_UNION_ADDRESS_RESIDUALS` says `Event` is NOT a residual because a bare
    id does address an Event. That sentence has a record-side premise no test
    pinned: the schema's

        CONSTRAINT chk_event_node_key_is_contract_key
            CHECK (event_id IS NOT NULL AND node_key = event_id)

    while :func:`read_entity` addresses an Event by its contract key (`eventId`)
    and :func:`read_node` anchors on `node_key`. A forward migration that re-keys
    `event` leaves every declaration in this module untouched and still wrong, so
    the premise is read from its OWNERS here: the EFFECTIVE DDL expression (last
    declaration wins — see :func:`_effective_event_node_key_check`), the record's
    key contract for the label, the column the record matches an `event` by, and
    then the REAL reader, asserting the query was bound to `node_key` with the
    bare value handed in.

    ⛔ `Event.id` is NOT that identity and nothing here rests on it (PR #13358 review §3.2, the
    comment cited above): `projection/entities.py::_event_guarded_merge`'s SUFFIX branch
    MERGEs on a suffixed `eventId` while `id` keeps the candidate, so two Events
    can share one `id` — and `live.py:1194-1197` says exactly that about that
    shared value: *"The coalesced value is NOT a unique identity, and must not be
    used as one"* (`EPISTEMIC_ID_KEYS` at `live.py:1204` still lists `id` FIRST,
    deliberately, for DISPLAY and hand-back — the prohibition is on COMPARISON
    and DEDUPE, not on mentioning the value).
    The value this door is handed for an Event is the one the cache RESOLVER
    matched (`FalkorProjection._RESOLVE_BRANCHES` → `("Event", "eventId")`).

    ⚠️ `record_serve.py`'s own docstring for this map still justifies `Event`'s
    absence from it with *"`Event.id == eventId` for all current writes"* — the
    universal this docstring disowns. That is the same defect tracked in #13377
    (the `Event.id == eventId` invariant stated as truth); it is on `main` and
    deliberately not touched here, because this PR changes no production code.
    """
    assert "Event" not in rs.ENTITY_UNION_ADDRESS_RESIDUALS, (
        "Event's record coordinate is the eventId, and the bare id handed to "
        "this door IS that value — putting it in the residual map tells the "
        "next lane the wrong repair (parity, #9721, not re-addressing)")
    # ⚠️ TODAY's state, not a rule: when #13351 lands (a Source-by-id read, or a
    # decision that a bare Source id should never have resolved) this residual is
    # deleted and this arm goes with it. `Event`'s addressability is unaffected
    # by that change — which is the point of separating the two facts at all.
    assert "Source" in rs.ENTITY_UNION_ADDRESS_RESIDUALS, (
        "a Source's record coordinate is `url` — a bare Source id names nothing "
        "in the record, which is why that residual stands TODAY (#13351 closes "
        "it; delete this arm then)")

    from tortoise import record_projection as _rp

    got = _effective_event_node_key_check(_rp._read_migrations(None))
    assert got == _EVENT_KEY_CHECK, (
        "the record's own DDL no longer pins an Event's node_key to its "
        "event_id — the premise that makes `Event` addressable by a bare id "
        f"has changed, so re-derive ENTITY_UNION_ADDRESS_RESIDUALS — got {got!r}")
    assert _rp.key_properties()["Event"] == "eventId", (
        "public.node_identity no longer keys an Event by `eventId` — the value "
        "this door is handed for an Event is no longer its contract key")
    assert _rp._ENTITY_MATCH_COLUMNS["event"] == ("node_key",), (
        "the record no longer matches an `event` by `node_key` — an id-keyed "
        "read would address a different column than the constraint pins")

    conn = _TableConn({"event": [{"event_id": "ev-1", "name": "decided",
                                   "node_key": "ev-1"}]})
    out = rs.read_entity(conn, org_id=TENANT[0], graph_id=TENANT[1],
                         id_val="ev-1", labels=("Event",))
    assert out is not None and out.get("name") == "decided", (
        "the record could not be asked for an Event by the id that IS its "
        f"eventId — got {out!r}")
    sql, params = conn.row_statements()[-1]
    assert "node_key = %s" in " ".join(sql.split()), (
        "the Event read must anchor on node_key — the column the DDL pins to "
        f"event_id — got {sql!r}")
    assert "ev-1" in params, (
        f"the read was not bound to the bare id handed in — params were {params!r}")


def test_a_label_with_no_contract_key_RAISES_rather_than_being_skipped(monkeypatch):
    """A declaration the reader cannot honour is broken, not quietly excluded.

    ⛔ THE ROW HAS TO MAKE THIS GUARD THE ONLY POSSIBLE RAISE. `NODE_TABLES` and
    `KEY_PROPERTY` have identical key sets today, and `read_node` raises the SAME
    `RecordServeError` for a label it has no table for — so passing a made-up
    label proved nothing: with the new guard deleted the row stayed GREEN
    (#13353 review, finding 5, measured `25 passed`). The state below removes
    `Session` from `KEY_PROPERTY` only, so the table exists and the key does not
    — the one shape in which this guard is the sole source of the refusal.
    """
    from tortoise import cache_projection

    monkeypatch.delitem(cache_projection.KEY_PROPERTY, "Session")
    conn = _TableConn()
    with pytest.raises(rs.RecordServeError, match="declares no contract key"):
        rs.read_entity(conn, org_id=TENANT[0], graph_id=TENANT[1],
                       id_val="x", labels=("Session",))
    assert conn.row_statements() == [], (
        "the reader asked the record for a label it cannot address")


def test_a_FOUND_answer_is_never_turned_into_a_marker():
    """`entity_absent_wire` is the rule's home; a hit routed through it stays a hit.

    The first cut discarded every property (`id` included), so a read that SAW
    the node was reported as "nobody could see it". Unreachable at the time (its
    one caller guards), but it is the module's published home for the rule and a
    name that invites exactly that call.
    """
    found = rs.ServedAnswer(read=rs.READ_ENTITY_UNION_BY_ID, coordinate="c",
                            served_from="record",
                            props={"id": "obj-1", "name": "migration-goal"})
    out = rs.entity_absent_wire(found)
    assert out.get("id") == "obj-1" and out.get("name") == "migration-goal", (
        f"a FOUND answer came back as {out!r} — a marker for a node the read saw")
    assert RESIDENCY_FIELD not in out
    assert out.get("servedFrom") == "record", (
        "the hit lost its store attribution — the seam's contract is that "
        "`servedFrom`/`fallbackReason` appear on EVERY armed response, so an "
        f"unattributable hit is an invisible answer, got {out!r}")
    assert "fallbackReason" in out, (
        "the null `fallbackReason` is emitted deliberately (a stored property of "
        "that name must not forge an attribution)")



def test_the_INTERNAL_goal_readers_never_open_the_record(armed):
    """The ten internal `cache_only=True` CALL SITES, driven for real (#13322 F4).

    ⛔ THE SDK-SIDE FLAG IS NOT THE SITE-SIDE ONE. `test_the_INTERNAL_accessor_
    never_consults_the_record` pins that `cache_only=True` stays cache-only, but
    it says nothing about whether the internal readers PASS it — and nothing did:
    every suite driving these paths runs in `off`, where the two spellings are
    byte-identical, while the same diff widened the stubs to `(**kwargs)` /
    `(*args, **kwargs)` so a dropped keyword no longer even `TypeError`s.
    Measured consequence: delete `cache_only=True` from
    `derived_invalidation.py:325` and NO row reddened.

    The two readers below are the real functions on two of the ten sites, run
    against the real SDK with `prefer` armed and a record that WOULD answer, so a
    reader that dropped the flag consults the record and reddens here.
    """
    from tortoise import goal_judgement as gj
    from tortoise.derived_invalidation import reconcile_goal_state_cache

    _, opened = armed({"object": [_object_row()]})
    sdk = _sdk_class()(nodes=[["Object", "id", "goal-1",
                               {"id": "goal-1", "status": "archived",
                                "goalState": "blocked"}]])

    # goal_judgement.py:1534 — the status axis, read mid-`_cache_then_arrow`.
    assert gj._goal_retired_on_status(sdk, "goal-1") is True, (
        "the reader did not see the archived status — the fixture is wrong")
    # derived_invalidation.py:1134 — the public idempotent repair route.
    assert reconcile_goal_state_cache(sdk, "goal-1")["preserved"] is True, (
        "a human word must be preserved, and the read must still be cache-only")
    assert opened.calls == [], (
        "an INTERNAL reader opened the record — a mid-write reader must read a "
        "store the write has already finished projecting, and a truthy "
        "`{\"residency\": \"not-hot\"}` here would be read as a found node by "
        "`or {}` (#13322 review F4)")
    # …and the caller-facing door in the SAME armed state DOES reach it, so the
    # absence of a consult above is the flag's effect and not an unarmed env.
    assert sdk.get_entity("obj-1").get("id") == "obj-1"
    assert opened.calls


def test_every_INTERNAL_get_entity_call_site_declares_cache_only():
    """All TEN sites, site by site — not just the two exercised above.

    ⛔ The behavioural arm above proves the MECHANISM on two real readers; this
    one proves COVERAGE of the whole set, which is the finding: a single site
    losing the keyword is invisible to every suite that drives it (they all run
    in `off`, where both spellings are byte-identical). A source-level sweep is
    the only cheap way to hold all ten, and it is a coarse but honest instrument:
    it reddens on a dropped keyword, which is exactly the mutant the review named.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    sites: list[tuple[str, int, bool]] = []
    for rel in ("tortoise/derived_invalidation.py",
                "tortoise/goal_judgement.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get_entity"):
                flagged = any(
                    kw.arg == "cache_only"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in node.keywords)
                sites.append((rel, node.lineno, flagged))

    unflagged = [(rel, lineno) for rel, lineno, ok in sites if not ok]
    assert unflagged == [], (
        "these internal `get_entity` call sites do NOT declare "
        f"`cache_only=True` — {unflagged}. The public door honours "
        "`TORTOISE_RECORD_SERVE` since #13093, so a plain call here can answer "
        "from the record or hand back a TRUTHY `{'residency': 'not-hot'}` that "
        "`or {}` / `or MISSING` reads as a found node")
    assert len(sites) >= 10, (
        f"only {len(sites)} internal `get_entity` call sites found in the two "
        "reader modules — the measured set is 10 (derived_invalidation x4, "
        "goal_judgement x6); if sites were legitimately removed, re-derive this "
        "floor from the tree rather than dropping the row")


def test_the_deferred_and_inline_paths_spell_one_coordinate(armed):
    """One coordinate, one spelling (#13322 review observation 3).

    `serve_entity` builds `union:<labels>:<id>`; `_get_entity_served`'s deferred
    path used to build `union:<id>`, so the SAME fallback was attributed to two
    different coordinates depending on which of the two shapes answered it and an
    ops join across them silently missed. The value is attribution only — it
    never reaches the wire — so this pins hygiene, not a verdict.
    """
    armed({"object": []})  # the record answers `no row`
    seen: list[str] = []
    real = rs.answered_by_cache

    def _spy(**kwargs):
        seen.append(kwargs["coordinate"])
        return real(**kwargs)

    monkeypatch_target = rs
    monkeypatch_target.answered_by_cache = _spy
    try:
        sdk = _sdk_class()(nodes=[])
        sdk.get_entity("obj-1")           # deferred shape (sdk-side spelling)
        rs.serve_entity(                  # inline shape (module-side spelling)
            tenant=TENANT, id_val="obj-1", cache_answer=lambda: {},
            connect=rs.open_record, mode=rs.MODE_PREFER)
    finally:
        monkeypatch_target.answered_by_cache = real

    assert len(seen) == 2, f"expected both shapes to fall back, got {seen}"
    assert seen[0] == seen[1], (
        f"the same fallback is attributed to two coordinates: {seen}")
    assert seen[0] == f"union:{'|'.join(rs.ENTITY_UNION_LABELS)}:obj-1", seen[0]


def _assert_labels_are_ADDRESSABLE(labels, state):
    """The real guard: each label's contract key IS one of its cache branches.

    ⛔ ONE DIRECTION, NOT AN EQUALITY (#13392 review, P2 — the same shape as
    cycle-4 finding 1, one issue later, for `Source` instead of `Event`). A
    SECOND cache branch is not by itself a reason a label cannot be served.
    `Source` has two (`_RESOLVE_BRANCHES`: `id` and `url`) while its record key
    is `url`, and the repair the module itself names is a Source-by-`id`
    **probe in this read** (`record_serve.ENTITY_UNION_ADDRESS_RESIDUALS` →
    #13351) — which moves neither `_RESOLVE_BRANCHES` nor `KEY_PROPERTY`. The
    latter is schema-bound: `url` comes from the generated
    `public.node_identity` CASE and `chk_source_node_key_is_contract_key` pins
    `node_key = url`, and a generated column's value "cannot be written to
    directly … cannot be overridden" (PostgreSQL 18 §5.4) — only a migration
    moves it, and #13351 is not a migration. So at the repair state an EQUALITY
    reddens with a message — "a label with a second branch is the address
    residual, not a served label" — that tells the `#13351` lane the repair they
    were told to make cannot exist.

    What addressability actually means: the branch that yields the bare value
    the record's `node_key` carries EXISTS, so a value the cache resolved can
    address the record row. That still reds on both real defects — `KEY_PROPERTY`
    drift, and a served label losing its contract-key branch — the two failures
    the old `KEY_PROPERTY == "id"` literal could not see, because it reddened on
    CORRECT states (`Event`, `Source`) instead.

    ⛔ AND NOTHING HERE IS SAID ABOUT A *SECOND* BRANCH, DELIBERATELY. Whether an
    extra branch is addressed by the record is `ENTITY_UNION_ADDRESS_RESIDUALS`'
    question, derived and pinned by
    `test_the_two_coverage_facts_are_SEPARATE_and_Event_is_not_a_residual` — and a
    guard here that demanded "declare it as a residual" would be the SAME defect
    one step later: `#13351`'s repair RETIRES that entry (the probe covers the
    branch), so the message would send that lane backwards. One home per question:
    this row pins the contract key, the residual map pins what the read does not
    cover, and a second blocker with a misdirecting message is what #13392 review,
    P2 removed.
    """
    from tortoise.cache_projection import KEY_PROPERTY
    from tortoise.projection import FalkorProjection

    for label in labels:
        branch_props = {prop for lab, prop in FalkorProjection._RESOLVE_BRANCHES
                        if lab == label}
        contract_key = KEY_PROPERTY.get(label)
        assert contract_key in branch_props, (
            f"{state}: {label} has cache branches {sorted(branch_props)} but the "
            f"record addresses it by {contract_key!r} — a label whose contract "
            f"key is not one of its cache branches is resolved from a value the "
            f"record row cannot carry, which is an ADDRESSABILITY defect, not a "
            f"residual (#13351 repairs the reverse case: a branch the read does "
            f"not probe yet, which is ENTITY_UNION_ADDRESS_RESIDUALS' question "
            f"and not this row's)")


def test_the_served_labels_are_the_ones_with_measured_parity():
    """The declaration, pinned to the evidence rather than to a count.

    `Subject`/`Object` are cleared by #9634 (`FOLD_ON_CREATE_DEFAULTS` covers
    their fold constants) and `Point` by `point-by-id`'s measured parity; `Event`
    and `Source` are the open #9721 list. A label added here without that
    evidence would serve a silent parity gap through an honesty door.
    
    ⚠️ TODAY-PIN: this row states the state #13353 shipped in, and it is EXPECTED
    to be updated when #9721 lands. The only acceptable greening is a GROWN
    union — never a shrunken jurisdiction, and never a label taken back out.
"""
    from tortoise.cache_projection import FOLD_ON_CREATE_DEFAULTS

    assert set(rs.ENTITY_UNION_LABELS) == {"Point", "Subject", "Object"}
    assert {"Subject", "Object"} <= set(FOLD_ON_CREATE_DEFAULTS), (
        "#9634's declared fold constants are what clear Subject/Object for a "
        "record-served read")
    assert "Event" not in FOLD_ON_CREATE_DEFAULTS, (
        "if #9721 lands, revisit ENTITY_UNION_LABELS — the abstention is caused "
        "by THIS gap")
    # ⛔ EVERY SERVED LABEL MUST BE ADDRESSABLE — MEASURED, NOT ASSERTED AS
    # `KEY_PROPERTY == "id"` (#13353 review cycle 4, finding 1). That spelling is
    # the conflation this PR removed: `Event` IS addressable by a bare id while its
    # key is `eventId` (that is #13347's finding), so the literal would redden at
    # exactly the repair state #9721 produces — and its message ("a bare id cannot
    # address it") would tell the next lane the opposite of the fix.
    _assert_labels_are_ADDRESSABLE(rs.ENTITY_UNION_LABELS, "the served union")


def test_the_addressability_guard_does_NOT_prohibit_the_13351_repair():
    """The invariant is asserted at the state the repair produces, not against it.

    ⛔ #13392 review, P2. The repair state is two steps the module itself names:
    `#9721` adds `Source` to `ENTITY_UNION_LABELS` (the union must GROW — a
    shrunken jurisdiction is the false PASS `test_TODAYs_two_blockers…`
    forbids), and `#13351` gives THIS READ a Source-by-`id` probe over the column
    the record already has. Neither step touches `_RESOLVE_BRANCHES` (`Source`
    keeps both branches) nor `KEY_PROPERTY["Source"]` (`url`, schema-bound), so a
    guard spelled as EQUALITY reddens there and its message tells the #13351
    lane the repair cannot exist.

    So the invariant is asserted over the whole JURISDICTION — which is that
    state with `Event` included — rather than over today's smaller served set:
    every label a bare value can name has its contract key among its cache
    branches. What the record does NOT cover (a second branch, until a probe
    does) stays `ENTITY_UNION_ADDRESS_RESIDUALS`' question, derived and pinned by
    the sibling row — see the helper's docstring for why it must not be doubled
    here.
    MEASURED non-vacuous: under the previous equality spelling this row reds
    today, on `Source` (`{'id', 'url'} != {'url'}`).
    """
    from tortoise.cache_projection import KEY_PROPERTY
    from tortoise.projection import FalkorProjection

    source_branches = {prop for lab, prop in FalkorProjection._RESOLVE_BRANCHES
                       if lab == "Source"}
    assert source_branches == {"id", "url"}, (
        f"this row's premise: a Source is resolved by two properties, only one of "
        f"which is its record key — got {sorted(source_branches)}")
    assert KEY_PROPERTY["Source"] == "url", (
        "and that one is `url`: the generated `node_identity` CASE, which no code "
        "change moves (#5012)")

    _assert_labels_are_ADDRESSABLE(rs.ENTITY_UNION_JURISDICTION,
                                   "the #9721 + #13351 repair state")


# ── 5. the internal accessor stays cache-only ──────────────────────────────


def test_the_INTERNAL_accessor_never_consults_the_record(monkeypatch):
    """⛔ `_get_entity` / `get_entity(..., cache_only=True)` is the write echo and
    the mid-sweep read path.

    Serving it would make a writer read a store the same write has not finished
    projecting, and would hand `derived_invalidation` / `goal_judgement` a
    TRUTHY `{"residency": "not-hot"}` where they test `or MISSING` and read it
    as a found node. So both spellings of the internal read stay cache-only, and
    only the plain caller-facing call reaches the seam.
    """
    monkeypatch.setenv(rs.RECORD_SERVE_ENV, rs.MODE_PREFER)
    opened = _opener(_TableConn({"object": [_object_row()]}))
    monkeypatch.setattr(rs, "open_record", opened)
    sdk = _sdk_class()(nodes=[])

    assert sdk._get_entity("obj-1") == {}, (
        "the internal accessor answered from something other than the cache")
    assert sdk.get_entity("obj-1", cache_only=True) == {}, (
        "`cache_only=True` did not stay cache-only")
    assert opened.calls == [], (
        "an internal read opened the record — a mid-write reader must not")
    # …and the caller-facing call in the same armed state DOES reach it, so the
    # absence of a record consult above is the flag's effect, not an unarmed env.
    assert sdk.get_entity("obj-1").get("id") == "obj-1"
    assert opened.calls


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

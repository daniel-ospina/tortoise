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
    """"Fixable by parity" and "fixable only by re-addressing" are different facts.

    #13322's second review sharpened this on the record and it decides what the
    "next lane does: a bare id DOES address an Event — `Event.id == eventId` for
    all current writes, and the record's `node_key` for an Event IS the eventId —
    so `Event`'s only gap is parity (#9721). A Source is the opposite: the cache
    resolves it by `id` and by `url`, while the record's key for it is
    `node_key = url`, so a bare Source *id* names a node the record read would
    miss. Folding the two into one condition is what let the previous predicate
    look reachable.

    ⛔ THESE ARE *TODAY'S* DECLARATIONS, AND THE DIRECTION THEY MUST MOVE IS THE
    ONE THAT GROWS THE UNION. `Event` is not a residual because a bare id DOES
    address it — `Event.id == eventId` for all current writes, and the record's
    `node_key` for an Event IS the eventId — so Event's only gap is parity
    (#9721). A Source is the opposite: the cache resolves it by `id` and by
    `url`, while the record's key for it is `node_key = url`, so a bare Source
    *id* names a node the record read would miss. Folding the two into one
    condition is what let the previous predicate look reachable.

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

    ⛔ AND A SECOND BRANCH IS ALLOWED ONLY WHERE IT IS DECLARED. `extra` must be
    empty or the label must sit in `ENTITY_UNION_ADDRESS_RESIDUALS` — the one
    home that says "the cache resolves this label from a value the record cannot
    be assumed to carry", and the fact `entity_coverage_complete` turns into an
    abstention. Without that half, a new branch on a served label would move the
    defect to a declaration nothing reads.
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
            f"residual (#13351 repairs the reverse case)")
        extra = branch_props - {contract_key}
        assert not extra or label in rs.ENTITY_UNION_ADDRESS_RESIDUALS, (
            f"{state}: {label} is resolved from {sorted(extra)} as well as its "
            f"contract key {contract_key!r}, and is not declared in "
            f"ENTITY_UNION_ADDRESS_RESIDUALS — declare it there, so the absence "
            f"verdict knows this read cannot be assumed to cover that value")


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
    branches, and any second branch is DECLARED as an address residual.
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

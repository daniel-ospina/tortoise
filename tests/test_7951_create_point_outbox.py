"""#7951 §17.1 — `create_point` through the OUTBOX ordering.

`docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner ruling, 2026-10-08) makes
**Postgres the system of record and FalkorDB a derived hot cache**; §17.1 states
the rule that makes a derived cache derived:

    > every write goes to the truth store first, and the cache is projected
    > from it.
    >
    > **A derived cache that is also written to directly is not derived.**

`#7950`'s capstone proved the label is not yet true on the highest-traffic write:
`create_point` wrote the Point through the guarded handle and only then appended
its `PointAdded` record — the recorded apply-first deviation (`cache ahead of
truth`, the window no replay can undo). This suite pins the fix: `create_point`
now builds the record from the **write intent** (`_create_map`, the properties
the `CREATE` will write) and appends it BEFORE any cache write, so the journal
append is the commit point.

⛔ SCOPE — what this suite does NOT claim. It does not claim the raw guarded
handle (`proj.g.query`) is closed, and it does not claim every `create_point`
door is converted: the DEDUP-HIT path routes to `update_point` and is still
apply-first (recorded residual). It does not claim the record projection /
`last_applied_seq` watermark (mechanism 2) exists. It pins the ORDERING on the
fresh-create path, and the replay-healability of a committed record whose cache
write did not land.

The verifier gates for this increment are recorded in
`docs/plans/2026-10-14-7951-atomic-write-outbox.md`; the review exit was
`[ADVERSARIAL-BOUND]` (cycle 2), not a clean pass.
"""
from __future__ import annotations

import datetime
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from tortoise.sdk import _journal_snapshot_from_create_map


@pytest.fixture
def env(tmp_path):
    """(sdk, events_dir) with the JSONL journal wired — the guard-arming shape."""
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "outbox.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        yield sdk, events
    finally:
        sdk.close()


def _journal(events) -> list[dict]:
    from tortoise.log import EventLog

    path = Path(events) / "events.jsonl"
    return EventLog(str(path)).read_all() if path.exists() else []


def _point_added(events) -> list[dict]:
    return [e for e in _journal(events) if e.get("type") == "PointAdded"]


def _count(proj, cypher: str, **params) -> int:
    rows = proj.g.query(cypher, params=params or None).result_set
    return int(rows[0][0]) if rows else 0


def _edges(proj, pid: str) -> set[tuple]:
    """The outgoing edge set as ``(type, target-key, edge-properties)``.

    A count-only comparison is the defect two reviews just found elsewhere in
    this repo: it is blind to a *changed* edge (same count, different target).
    Edge PROPERTIES are part of the tuple too — dropping them let a
    `sourceVersion` loss pass a suite green (round-3 review, P2).
    """
    rows = proj.g.query(
        "MATCH (p:Point {id:$id})-[r]->(m) "
        "RETURN type(r), coalesce(m.url, m.id, m.name), properties(r)",
        params={"id": pid},
    ).result_set
    return {(r[0], r[1], tuple(sorted((r[2] or {}).items()))) for r in rows}


# ── T1: the record precedes the cache write (the defect) ──────────────────

def test_create_point_appends_the_record_before_the_cache_write(env, monkeypatch):
    """§17.1's ORDER on the highest-traffic write — journal, then cache.

    The spy records the two events in the order they happen: `journal` when
    `_emit_event` is called for `PointAdded`, `cache` when a `:Point` write
    reaches the handle. The assertion is POSITIONAL, not a count, because a
    count of either event passes whether or not the order changed.

    MUTATION: move the `_emit_event("PointAdded", …)` back below the `CREATE`
    (or delete it) and this fails — `order` becomes `["cache", …]` (or the
    journal entry is absent).
    """
    sdk, _events = env
    proj = sdk._get_proj()
    order: list[str] = []

    real_emit = sdk._emit_event

    def spy_emit(type_, *args, **kwargs):
        if type_ == "PointAdded":
            order.append("journal")
        return real_emit(type_, *args, **kwargs)

    monkeypatch.setattr(sdk, "_emit_event", spy_emit)

    real_query = proj.g.query

    def spy_query(_self, cypher, *args, **kwargs):
        if ("CREATE (n:Point" in cypher
                or re.search(r"MERGE \([a-z]*:Point", cypher or "")):
            order.append("cache")
        return real_query(cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", spy_query)

    point = sdk.create_point("statement", "a journal-first claim")
    assert point.get("id"), f"create_point returned no id: {point!r}"

    assert order.count("journal") == 1, (
        f"exactly one PointAdded must be appended per fresh create: {order!r}")
    assert "cache" in order, (
        "the Point was never written — the spy missed the CREATE, so this test "
        f"would be vacuous: {order!r}")
    assert order.index("journal") < order.index("cache"), (
        "create_point wrote the CACHE before the record (apply-first, the "
        f"#7951 defect): {order!r}")


# ── T2: a failed append must not write the cache ──────────────────────────

def test_a_failed_append_does_not_write_the_cache(env, monkeypatch):
    """Derived can never LEAD truth: no record ⇒ no cache write.

    The journal append is the COMMIT POINT (the append's own per-call outcome —
    `_emit_event` returns `False`). If it fails, the cache must not be written —
    the one arm where `WriteNotLandedError(record_committed=False)` is literally
    true.

    MUTATION: drop the per-call outcome gate (`_record_committed is False`) and
    the Point is written with no record — `_count(...) == 1`.
    """
    from tortoise.exceptions import WriteNotLandedError

    sdk, _events = env
    proj = sdk._get_proj()

    log = sdk._get_event_log()
    assert log is not None, "the fixture must wire a journal for this arm"

    def _boom(_event):
        raise OSError("simulated journal append failure")

    monkeypatch.setattr(log, "append", _boom)

    with pytest.raises(WriteNotLandedError) as ei:
        sdk.create_point("statement", "a claim whose record cannot be written")

    assert ei.value.record_committed is False, (
        "the append FAILED, so the record is not committed and the cache must "
        "not hold it — re-issuing is the correct caller response")
    assert _count(proj, "MATCH (n:Point) RETURN count(n)") == 0, (
        "a Point was written although its record was never committed — the "
        "derived store leads the truth (§17.1)")


# ── T3a/T4: the payload builder is complete and fail-closed ───────────────

def test_the_snapshot_builder_covers_every_create_map_key():
    """Every `_create_map` key must appear in the record — no silent drop.

    A property the `CREATE` writes but the record omits is a node a replay
    cannot reproduce, i.e. `derived = replay(journal)` false. The builder is
    closed over the expression forms `_create_map` can carry today.
    """
    create_map = {
        "id": "$id", "content": "$c", "pointKind": "$k",
        "is_operator": "false", "status": "$st",
        "createdAt": "$now", "updatedAt": "$now",
        "ep_dirty": "true", "ep_dirty_at": "$_epv",
        "ep_alpha": "$ba", "ep_beta": "$bb",
        "baseline_set": "true", "baseline_source": "$bsrc",
        "embedding": "vecf32($embedding)", "embedding_verbatim": "true",
        "tags": "$_cp0", "custom": "$_cp1",
    }
    params = {
        "id": "p1", "c": "text", "k": "statement", "st": "live",
        "now": "2026-10-14T00:00:00Z", "_epv": 7,
        "ba": 3.0, "bb": 1.0, "bsrc": "system-default",
        "embedding": [0.1, 0.2], "_cp0": ["a"], "_cp1": 42,
    }

    payload = _journal_snapshot_from_create_map(create_map, params)

    # An EXACT map, not just the key set: the key set alone cannot fail for a
    # builder that iterates `create_map` (#7951 review — the previous
    # `set(payload) == set(create_map)` assertion was structurally vacuous).
    assert payload == {
        "id": "p1", "content": "text", "pointKind": "statement",
        "is_operator": False, "status": "live",
        "createdAt": "2026-10-14T00:00:00Z",
        "updatedAt": "2026-10-14T00:00:00Z",
        "ep_dirty": True, "ep_dirty_at": 7,
        "ep_alpha": 3.0, "ep_beta": 1.0,
        "baseline_set": True, "baseline_source": "system-default",
        "embedding": [0.1, 0.2], "embedding_verbatim": True,
        "tags": ["a"], "custom": 42,
    }, "the builder dropped or changed a `_create_map` key"

    assert payload["is_operator"] is False
    assert payload["baseline_set"] is True
    assert payload["embedding_verbatim"] is True
    assert payload["embedding"] == [0.1, 0.2], (
        "vecf32($x) must journal the RAW intent; the replay re-narrows it")
    assert payload["custom"] == 42


def test_the_snapshot_builder_fails_closed_on_an_unknown_form():
    """A future expression form must RAISE, never mis-journal.

    The builder cannot guess what an unknown form means. Raising fails the
    write loudly; returning a wrong value would journal a record a replay turns
    into a different node.
    """
    with pytest.raises(ValueError, match="expression form"):
        _journal_snapshot_from_create_map(
            {"weird": "toUpper($x)"}, {"x": "v"})


# ── T3b/T7: the record's own payload replays to the live node ─────────────

def test_the_journaled_payload_matches_the_live_node_and_replays(env, monkeypatch):
    """live == replay: the record's `point` payload carries the node's properties.

    Captures the journaled payload at the append, then compares it to the live
    node's own property map (minus the payload-only embedding identity stamps),
    then rebuilds from the journal and compares the FULL property map and the
    EDGE SET — a count-only comparison would be blind to a changed edge.

    `updatedAt` and `embedding` are excluded from the live-vs-replay map compare:
    `_upsert_point_props` stamps `updatedAt` at fold time and `vecf32`-narrows
    the vector, so neither is a property the record can reproduce literally.
    Both are covered elsewhere (#5004); excluding them here is stated, not
    silent.
    """
    sdk, events = env
    proj = sdk._get_proj()
    captured: list[dict] = []

    real_emit = sdk._emit_event

    def spy_emit(type_, *args, **kwargs):
        if type_ == "PointAdded":
            captured.append(dict(kwargs.get("point") or {}))
        return real_emit(type_, *args, **kwargs)

    monkeypatch.setattr(sdk, "_emit_event", spy_emit)

    # A real Source WITH a contentHash, so `resolve_source_versions` produces a
    # non-empty transit map and `sourceVersionTransit` is actually exercised (a
    # non-existent ref yields `{}` and let a transit loss pass the suite green).
    source_url = "https://example.test/7951"
    proj.g.query(
        "MERGE (s:Source {url:$u}) SET s.contentHash=$h",
        params={"u": source_url, "h": "hash-7951"},
    )

    point = sdk.create_point(
        "statement", "a sourced, tagged claim",
        tags=["alpha"], extractedFrom=source_url,
        credibility="gold",
    )
    pid = point["id"]
    assert captured, "no PointAdded payload was captured"

    payload = captured[-1]
    live = dict(sdk.get_point(pid))
    assert live, "the live Point is not readable"

    # Every live node property is carried by the record (payload metadata that
    # never becomes a node property is allowed in the other direction).
    payload_props = set(payload) - {"embedding_model", "embedding_revision",
                                    "embedding_text_hash"}
    missing = set(live) - payload_props
    assert not missing, (
        f"live node properties absent from the journal record: {missing!r} — a "
        "replay of this record cannot reproduce the node")
    assert payload.get("sourceVersionTransit"), (
        "the `extractedFrom` read-version transit is NOT in the record — a "
        "replay cannot re-stamp the edge's `sourceVersion` without re-reading "
        "the Source (which `_upsert_source` may have advanced, #5024)")

    live_edges = _edges(proj, pid)
    assert live_edges, "no live edges to compare — the edge half would be vacuous"

    proj.rebuild_all(str(events), confirm_destructive=True)

    replayed = dict(sdk.get_point(pid))
    assert replayed, "rebuild_all did not materialise the Point from the record"

    _skip = {
        "updatedAt", "embedding",
        # ⚠️ PRE-EXISTING replay-parity defect, declared as residual 6 of
        # `docs/plans/2026-10-14-7951-atomic-write-outbox.md` and filed as
        # tortoise#8699: the replay fold drops the EP-owned / baseline fields
        # (`_POINT_DENY`, no SET clause), so a `rebuild_all` does not restore
        # the `#2199` baseline the live create wrote. NOT introduced by #7951
        # (the record carries them; the fold drops them) — excluded here rather
        # than silently ignored so the exclusion is visible. DELETE these two
        # entries when #8699 lands.
        "ep_alpha", "ep_beta", "baseline_set", "baseline_source",
    }
    assert ({k: v for k, v in live.items() if k not in _skip}
            == {k: v for k, v in replayed.items() if k not in _skip}), (
        "live and replayed property maps diverge — the record is not complete")
    assert _edges(proj, pid) == live_edges, (
        "live and replayed EDGE sets diverge — a node-only comparison is blind "
        "to this (the defect class the round-2 review found)")


# ── T5/T6: the commit survives a failed cache write; one record per create ─

def test_a_failed_cache_write_leaves_the_record_replayable(env, monkeypatch):
    """The commit survives: the cache write fails, the record stands, replay heals.

    This is the ordering contract's replay-healable window. The CREATE is forced
    to fail (a non-abort error, so `_graph_write_with_retry` does not retry it),
    the record is still appended, and a later `rebuild_all` materialises the
    Point from that record.

    MUTATION: move the append below the CREATE (apply-first) and the journal
    holds nothing → this fails at `_point_added(events) == []`.
    """

    sdk, events = env
    proj = sdk._get_proj()

    real_query = proj.g.query
    state = {"fail": True}

    def spy_query(_self, cypher, *args, **kwargs):
        if state["fail"] and "CREATE (n:Point" in (cypher or ""):
            raise RuntimeError("simulated write failure (test)")
        return real_query(cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", spy_query)

    # The failed CREATE surfaces as the engine error (`_graph_write_with_retry`
    # re-raises a non-abort error unchanged) — the point here is that the
    # RECORD was already committed before the cache write was attempted.
    with pytest.raises(RuntimeError, match="simulated write failure"):
        sdk.create_point("statement", "a claim whose cache write will fail")

    added = _point_added(events)
    assert len(added) == 1, (
        f"the record must be committed BEFORE the failing cache write: {added!r}")
    pid = added[0].get("id") or (added[0].get("point") or {}).get("id")
    assert pid, f"the committed record carries no id: {added[0]!r}"
    assert _count(proj, "MATCH (n:Point) RETURN count(n)") == 0, (
        "the cache write was supposed to fail — the node must not exist yet")

    state["fail"] = False
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _count(proj, "MATCH (n:Point {id:$id}) RETURN count(n)", id=pid) == 1, (
        "the committed record did not heal the cache on replay — the write is "
        "lost, which is the very window this ordering exists to close")


def test_a_fresh_create_appends_exactly_one_record(env):
    """One commit point per fresh create — a mutation guard, not a defect detector.

    Passes on `main` as well; it exists so a future change that appends twice
    (e.g. keeping the old post-`get_point` emit alongside the new pre-`CREATE`
    one) reddens here rather than growing the journal silently.
    """
    sdk, events = env
    sdk.create_point("statement", "one claim", dedup=False)
    assert len(_point_added(events)) == 1, (
        f"a fresh create must append exactly one PointAdded: {_point_added(events)!r}")


# ── T8: the record states what the STORE HOLDS, not the raw caller object ──
#
# The append is the commit point, so a payload the record cannot encode does
# not merely lose the record — the per-call append gate SKIPS the cache write,
# and one unlucky prop value destroys the whole Point (#7951 review P1). And a
# value `json` accepts but the engine DROPS must not be journalled as a
# property the node never had (the review's secondary symptom). The payload is
# therefore built from the store's own outcome, not from the caller's object.
#
# ⚠️ These are MUTATION GUARDS, NOT pre-change detectors. On `origin/main` the
# payload IS the post-`CREATE` `get_point` read-back, so the offending object
# never reaches `json.dumps` and EVERY test below PASSES there (measured, #7951
# review). Each reddens only under the mutation it names (journal the raw
# caller value) — do not read them as pinning a `main` defect.

@pytest.mark.parametrize("propname, value", [
    ("when", datetime.datetime(2026, 10, 14, 12, 0, 0)),
    ("amount", Decimal("1.5")),
    ("blob", b"raw"),
    ("tags_like", {"a", "b"}),
])
def test_a_non_json_encodable_prop_does_not_destroy_the_write(env, propname, value):
    """A raw caller value must not turn a create into a LOST write.

    MUTATION GUARD, not a pre-change detector. On `origin/main` the payload IS
    the post-`CREATE` `get_point` read-back, so the offending object never
    reaches `json.dumps` and this PASSES there (measured, #7951 review) — it
    does not pin a `main` defect.

    MUTATION: journal the raw caller value and this fails on the raise (and on
    the empty node count): `json.dumps` raises inside `_emit_event`, the
    per-call append gate reports `False`, and `create_point` raises
    `WriteNotLandedError` with the Point NEVER written.
    """
    sdk, events = env
    proj = sdk._get_proj()

    point = sdk.create_point("statement", "a claim with a hostile prop",
                             **{propname: value})

    assert point.get("id"), f"create_point returned no id: {point!r}"
    assert sdk._journal_write_failures == 0, (
        "the record could not be encoded — the append failed and the whole "
        "write was lost (#7951 review P1)")
    pid = point["id"]
    assert _count(proj, "MATCH (n:Point {id:$id}) RETURN count(n)", id=pid) == 1, (
        "the Point was not written although nothing about the caller's prop "
        "should stop it")
    added = _point_added(events)
    assert len(added) == 1, (
        f"exactly one PointAdded must be committed: {added!r}")
    # The append itself is the proof: the record is JSON-encodable end to end.
    json.dumps(added[0], ensure_ascii=False)


def test_the_record_states_only_what_the_store_will_hold(env):
    """live(write) == record: the payload must not claim a dropped property.

    `extra={"inner": 1}` is JSON-encodable but FalkorDB DROPS it (`_value_ok`
    refuses a map), so the live node has no `extra`. A record that carried it
    anyway would be a journal/graph asymmetry no replay could close — and
    `check_consistency` would name it (`uncarried_journal_fields: ['extra']`;
    #7951 review, secondary symptom).

    MUTATION GUARD, not a pre-change detector: on `origin/main` the payload is
    the read-back, so the dropped map never reaches the record and this PASSES
    there (measured, #7951 review). It reddens under the mutation it names:
    journal the raw param and the `payload` assertions fail (the
    `check_consistency` half still passes, because a carried-but-dropped field
    is reported, not failed).
    """
    from tortoise.consistency import check_consistency

    sdk, events = env
    pid = sdk.create_point("statement", "a claim with a nested prop",
                           extra={"k": {"inner": 1}})["id"]

    live = dict(sdk.get_point(pid))
    payload = _point_added(events)[0]["point"]
    assert "extra" not in live, (
        "the engine dropped the map — the live half of this test is vacuous "
        f"otherwise: {live!r}")
    assert "extra" not in payload, (
        "the record claims a property the store refused — a replay would "
        f"materialise a node live never had: {payload.get('extra')!r}")

    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert "extra" not in report["uncarried_journal_fields"], (
        "the record carries a field the graph does not — the reported "
        f"asymmetry: {report['uncarried_journal_fields']!r}")


def test_a_driver_coerced_number_is_recorded_as_the_store_holds_it(env):
    """`Decimal("1.5")` is stored as the float literal, so the record says 1.5.

    MUTATION GUARD, not a pre-change detector: on `origin/main` the payload is
    the read-back (`1.5`), so this PASSES there (measured, #7951 review).

    MUTATION: journal the raw param and the append gate raises
    `WriteNotLandedError` before the comparisons.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "a claim with a Decimal",
                           amount=Decimal("1.5"))["id"]

    live = dict(sdk.get_point(pid))
    payload = _point_added(events)[0]["point"]
    assert live["amount"] == 1.5, "the store half of the comparison is vacuous"
    assert payload["amount"] == 1.5, (
        f"the record states {payload.get('amount')!r}, the store holds 1.5")
    assert isinstance(payload["amount"], float), (
        "the record must carry the parsed literal, not the raw Decimal object")


def test_the_snapshot_builder_states_the_stored_form_not_the_raw_param():
    """The builder is JSON-safe BY CONSTRUCTION — a unit pin on the seam.

    MUTATION GUARD, not a pre-change detector: the builder did not exist on
    `origin/main` (the pre-#7951 record was the read-back), so there is no
    pre-change revision for this unit to redden on. It reddens under the
    mutation it names.

    MUTATION: return the param verbatim and both halves fail —
    `json.dumps(payload)` raises `TypeError: Object of type Decimal is not
    JSON serializable`, and the dropped key is present.
    """
    payload = _journal_snapshot_from_create_map(
        {"ok": "$_cp0", "dropped": "$_cp1", "null": "$_cp2",
         "nested_null": "$_cp3", "amount": "$_cp4"},
        {"_cp0": [1, 2], "_cp1": {"a": 1}, "_cp2": None,
         "_cp3": [1, None], "_cp4": Decimal("1.5")},
    )
    assert payload == {"ok": [1, 2], "amount": 1.5}, (
        "a value the store keeps no property for must be OMITTED (a null, a "
        "map, and a null inside an array are all refused), and a "
        f"driver-coerced number must ride as the parsed literal: {payload!r}")
    json.dumps(payload, ensure_ascii=False)

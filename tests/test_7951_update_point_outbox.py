"""#7951 §17.1 — `update_point` through the OUTBOX ordering.

`docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner ruling, 2026-10-08) makes
**Postgres the system of record and FalkorDB a derived hot cache**; §17.1 states
the rule that makes a derived cache derived:

    > every write goes to the truth store first, and the cache is projected
    > from it.
    >
    > **A derived cache that is also written to directly is not derived.**

Increment 1 (`#8697`) took the ordering to `create_point`
(`tests/test_7951_create_point_outbox.py`). This suite pins the SECOND door of
increment 2 of the plan of record
(`docs/plans/2026-10-14-7951-atomic-write-outbox.md` §7): `update_point`, which
wrote the node and only then emitted `PointRevised`. `_emit_event` is
deliberately best-effort (`#4240`), so a failed append left the CACHE AHEAD OF
THE JOURNAL with no repair path: `rebuild_all` replays the OLD value and the
revision is gone — window (a) of the ordering contract, on the SDK's most common
mutation door.

⛔ SCOPE — what this suite does NOT claim.

* It does not claim the raw guarded handle (`proj.g.query`) is closed. The door
  still writes through it, so no `_live_write_scope` is opened here (a scope
  around a raw handle authorizes nothing — see `create_point`'s own note).
* It does not claim the RECORD projection on the live path (`record_projection`
  / Postgres) or mechanism (2)'s `last_applied_seq` watermark exists. This
  increment pins the ORDERING on the unguarded revision arms.
* The PROMOTE arm (`status='live'`) is deliberately still apply-first: its `SET`
  is guarded by a `WHERE` that can legitimately match nothing, and the door then
  raises — journalling first would record a promotion the graph REFUSED. That
  deviation is pinned below, in both directions (the order, and the absence of a
  record when the promote is refused).

WHERE THE PRE-FIX BEHAVIOUR FAILS. Each test whose docstring says "RED before
this change" fails on the pre-change tree for the reason it names; the rest are
mutation guards / deviation pins and PASS there (stated, never implied).
"""
from __future__ import annotations

import re

import pytest

from tortoise.exceptions import WriteNotLandedError


@pytest.fixture
def env(tmp_path):
    """(sdk, events_dir) with the JSONL journal wired — the guard-arming shape."""
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "outbox_update.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        yield sdk, events
    finally:
        sdk.close()


def _journal(events) -> list[dict]:
    from tortoise.log import EventLog

    path = events / "events.jsonl"
    return EventLog(str(path)).read_all() if path.exists() else []


def _revisions(events) -> list[dict]:
    return [e for e in _journal(events) if e.get("type") == "PointRevised"]


def _order_spies(sdk, monkeypatch, cache_pattern: str) -> list[str]:
    """Record `journal` / `cache` in the order the two writes happen.

    POSITIONAL, not a count: a count of either event passes whether or not the
    order changed, which is why the create-side suite records the same shape.
    `cache_pattern` is the statement shape the arm under test issues, so a
    different arm cannot accidentally satisfy the spy.
    """
    order: list[str] = []
    real_emit = sdk._emit_event

    def spy_emit(type_, *args, **kwargs):
        if type_ == "PointRevised":
            order.append("journal")
        return real_emit(type_, *args, **kwargs)

    monkeypatch.setattr(sdk, "_emit_event", spy_emit)

    proj = sdk._get_proj()
    real_query = proj.g.query

    def spy_query(_self, cypher, *args, **kwargs):
        if re.search(cache_pattern, cypher or ""):
            order.append("cache")
        return real_query(cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", spy_query)
    return order


# ── T1: the record precedes the cache write (the defect) ──────────────────

def test_update_point_appends_the_revision_before_the_cache_write(env, monkeypatch):
    """§17.1's ORDER on the revision door — journal, then cache.

    RED before this change: `update_point` issued the `SET n += $props` writes
    and emitted `PointRevised` only on the last line, so `order` was
    `["cache", ..., "journal"]` and the positional assertion failed.

    MUTATION: move the `_emit_event("PointRevised", ...)` back below the writes
    (or delete it) and this fails — `order` starts with `"cache"` (or the
    journal entry is absent).
    """
    sdk, _events = env
    pid = sdk.create_point("statement", "a claim to revise")["id"]

    order = _order_spies(sdk, monkeypatch, r"SET n \+= \$props")

    updated = sdk.update_point(pid, custom_field="revised")
    assert updated.get("custom_field") == "revised", (
        f"the update did not land, so the cache half is vacuous: {updated!r}")

    assert order.count("journal") == 1, (
        f"exactly one PointRevised must be appended per update: {order!r}")
    assert "cache" in order, (
        "the Point was never written — the spy missed the SET, so this test "
        f"would be vacuous: {order!r}")
    assert order.index("journal") < order.index("cache"), (
        "update_point wrote the CACHE before the record (apply-first, the "
        f"#7951 window): {order!r}")


# ── T2: a failed append must not write the cache ──────────────────────────

def test_a_failed_append_does_not_write_the_cache_on_update(env, monkeypatch):
    """Derived can never LEAD truth: no record ⇒ no cache write.

    RED before this change: the `SET` ran first, so the node carried the new
    value and the call returned normally — a revision with no journal record and
    no signal to the caller.

    MUTATION: drop the `_record_committed is False` gate and the property is
    written with no record — the `custom_field` assertion fails.
    """
    sdk, _events = env
    pid = sdk.create_point("statement", "a claim whose revision cannot be "
                                        "journalled")["id"]

    log = sdk._get_event_log()
    assert log is not None, "the fixture must wire a journal for this arm"

    def _boom(_event):
        raise OSError("simulated journal append failure")

    monkeypatch.setattr(log, "append", _boom)

    with pytest.raises(WriteNotLandedError) as ei:
        sdk.update_point(pid, custom_field="never-landed")

    assert ei.value.record_committed is False, (
        "the append FAILED, so the record is not committed and the cache must "
        "not hold it")
    assert ei.value.operation == "update_point", (
        "the error must name the door that raised it — a create_point message "
        f"on an update is a lie the caller cannot see past: {ei.value}")
    live = dict(sdk.get_point(pid))
    assert "custom_field" not in live, (
        "the revision was written although its record was never committed — "
        f"the derived store leads the truth (§17.1): {live!r}")


# ── T3: the commit survives a failed cache write ──────────────────────────

def test_a_failed_cache_write_leaves_the_revision_replayable(env, monkeypatch):
    """The commit survives: the cache write fails, the record stands, replay heals.

    This is the ordering contract's replay-healable window, on the revision
    door. The `SET` is forced to fail (a non-abort error, so no retry path
    applies), the record is still appended, and a later `rebuild_all`
    materialises the new content from that record.

    RED before this change: the raise happened BEFORE the emit, so the journal
    held no `PointRevised` and the rebuild replayed the ORIGINAL content — the
    revision was lost, which is the very window this ordering closes.
    """
    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "original content")["id"]

    real_query = proj.g.query
    state = {"fail": True}

    def spy_query(_self, cypher, *args, **kwargs):
        if state["fail"] and re.search(r"SET n \+= \$props", cypher or ""):
            raise RuntimeError("simulated write failure (test)")
        return real_query(cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", spy_query)

    with pytest.raises(RuntimeError, match="simulated write failure"):
        sdk.update_point(pid, content="revised content")

    revisions = _revisions(events)
    assert len(revisions) == 1, (
        "the record must be committed BEFORE the failing cache write: "
        f"{revisions!r}")
    assert revisions[0].get("new_content") == "revised content", (
        f"the committed record does not carry the intent: {revisions[0]!r}")
    assert dict(sdk.get_point(pid)).get("content") == "original content", (
        "the cache write was supposed to fail — the node must still hold the "
        "pre-revision content")

    state["fail"] = False
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert dict(sdk.get_point(pid)).get("content") == "revised content", (
        "the committed revision did not heal the cache on replay — the update "
        "is lost, which is the window this ordering exists to close")


# ── T4: the promote arm stays apply-first (a RECORDED deviation) ──────────

def test_the_promote_arm_stays_apply_first_a_recorded_deviation(env, monkeypatch):
    """The `status='live'` promote is still apply-first, on purpose.

    A deviation PIN, not a defect detector — it passes on the pre-change tree
    too. Its `SET` is guarded by `WHERE (n.status IS NULL OR n.status = 'draft')`
    and `update_point` RAISES when the guard matches nothing, so journalling
    first would record a promotion the graph refused. The order is asserted so a
    future change that "completes" the conversion without solving the guarded
    arm reddens HERE rather than shipping a journal that claims refused writes.
    """
    sdk, _events = env
    pid = sdk.create_point("statement", "a draft claim", status="draft")["id"]

    order = _order_spies(sdk, monkeypatch, r"SET n\.status = 'live'")

    sdk.update_point(pid, status="live", custom_field="promoted")
    assert "cache" in order and "journal" in order, (
        f"the spy missed one half of the promote — vacuous otherwise: {order!r}")
    assert order.index("cache") < order.index("journal"), (
        "the promote arm is no longer apply-first. That is only safe if the "
        "guarded `SET` can no longer refuse — read the outbox block in "
        f"`update_point` before changing this pin: {order!r}")


def test_a_refused_promote_journals_no_revision(env):
    """A promote the graph REFUSES must leave no record behind.

    A GUARD for the outbox change, not a pre-change detector: on the pre-change
    tree the emit also never ran (the raise precedes it), so this PASSES there.
    It is here because the cheapest way to "convert the last arm" is to hoist
    the emit above the writes — which would journal a terminal point's
    promotion, and a replay would resurrect it.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "a claim to retract")["id"]
    sdk.retract_point(pid)
    before = len(_revisions(events))

    with pytest.raises(ValueError, match="Illegal status transition"):
        sdk.update_point(pid, status="live")

    assert len(_revisions(events)) == before, (
        "the refused promote was journalled: a replay would apply a promotion "
        f"the live graph refused — {_revisions(events)[before:]!r}")


# ── T5: one commit point per update ───────────────────────────────────────

def test_update_point_appends_exactly_one_revision(env):
    """One commit point per revision — a mutation guard, not a defect detector.

    Passes on the pre-change tree as well; it exists so a future change that
    appends twice (e.g. keeping the old post-`get_point` emit alongside the new
    pre-write one) reddens here rather than growing the journal silently.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "one claim")["id"]
    sdk.update_point(pid, custom_field="one revision")
    assert len(_revisions(events)) == 1, (
        f"a revision must append exactly one PointRevised: {_revisions(events)!r}")

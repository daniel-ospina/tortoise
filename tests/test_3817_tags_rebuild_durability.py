"""#3817 — do a Point's TAGS survive ``rebuild_all``? (objective 6, lane 6)

Objective 6 requires that *"props, **tags** and EP state survive"* a rebuild.
`#2958` (`37d5ef00c`, PR #2958) proved the **props** half and left the tags half
unproven. This module is the tags half, measured through the **resolved query a
user path uses** (``TortoiseSDK.query_points_by_tag`` — the backing method of the
MCP ``tortoise_query(tag=...)`` surface), never by reading a field off an object.

## What was measured first (this is the point of the module)

The issue body's framing — *"rebuild_all **silently** drops them"* — is
**half stale**, and re-measuring is what separates the two halves:

| #3817's claim | measured (this file + the runs recorded in the PR) |
|---|---|
| "if the journal does not carry them" | **FALSE** — the ``PointAdded`` snapshot DOES carry ``tags``; so does a tag-editing ``PointRevised``. The tag is reconstructible from the journal (`test_the_journal_carries_the_tag`). |
| "or the fold does not re-apply them" | **TRUE** — no replay path re-applies them (`rg 'tags' tortoise/projection/` → zero tag writes), so the ``n.tags`` prop and every ``TAGGED`` edge are gone after a rebuild. |
| "silently drops them" | **FALSE since `#2958`** — the drop is **REPORTED** by name on every replay arm (`test_the_drop_is_reported_on_every_replay_arm`). |

## Why AC1/AC5 are ``xfail(strict=True)`` and not green

Restoring tags on replay is **not an unlanded fix**. On `main` it is a
**deliberately refused** one, and the refusal is recorded in three places:

* ``tortoise/projection/entities.py`` — *"`tags` … is DENIED on replay (its
  raw-list half-restore is refused — #2897) and the drop is reported …";
  ``_POINT_LIST_PROPS: frozenset = frozenset()``;
* ``tests/test_projection.py::test_falkor_rebuild_undeclared_list_prop_denied``
  — asserts ``n.tags is None`` after a rebuild **and** that a warning naming
  ``tags`` is emitted;
* the PR #2948 adjudication (``e1181bd9e``, ``docs/audits/2948-superseded-
  adjudication/README.md``) — the parked tags-replay PR "**REFUTED**: `main`
  deliberately refuses exactly what #2948 lands", verdict CLOSE, *"adopting over
  the decision is not an available route"* — plus the owner's note on #2897
  (2026-09-21): *"restoring tags on replay requires **reopening this decision**"*.

So the AC1/AC5 assertion below states the **required** outcome (the tag is
retrievable after a rebuild) and is bound to **#2897** with ``strict=True``:
it fails today *for the recorded reason*, and it turns **XPASS → RED the moment
a restore lands**, which is what forces the decision to be reopened deliberately
instead of reversed quietly. That is also why the decided shape is pinned
positively in the same file: a silent reversal has to edit this module.

## Mutations that must RED (evidence standard)

* **Reverse the decision** — restore the tag on replay (write ``n.tags`` from
  the payload + re-create the ``:Tag``/``TAGGED`` edges in the fold): the AC1/AC5
  test **XPASSes to RED** (``strict``) and the decided-shape test fails.
* **Make the loss silent again** — remove the undeclared-list drop report:
  ``test_the_drop_is_reported_on_every_replay_arm`` fails.
* **Stop the journal from carrying the tag** — strip ``tags`` from the emitted
  snapshot: ``test_the_journal_carries_the_tag`` fails (the journal, not the
  fold, is then the source of the gap).

## Lane

Runs on both lanes: the SDK is built from a tmp path (redirected to
``TORTOISE_DB_URI`` when one is set, embedded otherwise), and ``rebuild_all``
replays the JSONL journal on disk. Locally:

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_3817_tags_rebuild_durability.py -q -p no:cacheprovider
"""

from __future__ import annotations

import logging

import pytest

from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

# The replay writer's own logger — the channel every drop report rides.
_PROJECTION_LOGGER = "tortoise.projection.entities"


@pytest.fixture
def env(tmp_path):
    """``(sdk, events_dir)`` with the JSONL journal wired.

    Same shape as ``test_unjournaled_mutation_class.py``: the journal is a real
    file on disk so ``rebuild_all`` replays **the event log**, never an
    in-memory snapshot (the distinction AC2 exists to enforce).
    """
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "tags3817.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _journal(events) -> list[dict]:
    return EventLog(str(events / "events.jsonl")).read_all()


def _tag_hits(sdk, tag: str) -> list[str]:
    """The resolved user path: Points retrieved by ``TAGGED`` edge.

    ``query_points_by_tag`` is the SDK method behind the MCP
    ``tortoise_query(tag=...)`` surface — the retrieval a user performs, not a
    property read.
    """
    return sorted(str(row.get("id")) for row in sdk.query_points_by_tag(tag))


def _drops_naming(caplog, key: str) -> list[str]:
    """Warnings that name a dropped prop, for one key."""
    return [r.getMessage() for r in caplog.records
            if "dropped" in r.getMessage() and f"'{key}'" in r.getMessage()]


# ══ AC2 — the source of truth is named: the JOURNAL carries the tag ═══════

def test_the_journal_carries_the_tag(env):
    """AC2: the tag is reconstructible from the journal alone.

    This is the measurement that locates the gap in the **fold**, not in the
    journal: a fix has the payload it needs, and no backfill/migration is
    required to restore tags for pre-existing journals.
    """
    sdk, events = env
    point = sdk.create_point("statement", "the sky is blue",
                             tags=["alpha", "beta"])
    pid = point["id"]

    added = [e for e in _journal(events) if e.get("type") == "PointAdded"]
    assert len(added) == 1, "expected exactly one PointAdded record"
    assert added[0]["point"].get("tags") == ["alpha", "beta"], (
        "#3817: the PointAdded snapshot must carry `tags` — otherwise the gap "
        "is journalling, not replay, and the fix direction changes")

    # A tag EDIT rides PointRevised (top level, not under `point`) — so the
    # removal half of a tag change is journaled too (AC3's premise).
    sdk.update_point(pid, tags=["alpha"])
    revised = [e for e in _journal(events) if e.get("type") == "PointRevised"]
    assert revised and revised[-1].get("tags") == ["alpha"], (
        "a tag edit must be recorded, carrying the NEW list")


# ══ AC1 / AC5 — the requirement, blocked by the recorded decision ═════════

def test_the_same_query_returns_the_point_before_the_rebuild(env):
    """Control for the xfail below — makes the failure ATTRIBUTABLE.

    Without this, an xfail on "the tag is retrievable after a rebuild" could be
    green for the wrong reason (a wrong query, a wrong id, an unseeded graph).
    Here the identical resolved query runs pre-rebuild and must HIT.
    """
    sdk, _events = env
    pid = sdk.create_point("statement", "findable before rebuild",
                           tags=["alpha"])["id"]
    assert _tag_hits(sdk, "alpha") == [pid], (
        "the live path must make the point retrievable by tag")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#3817 AC1/AC5 (objective 6: tags survive): `main` deliberately "
        "REFUSES the tags replay — `_POINT_LIST_PROPS` is empty "
        "(tortoise/projection/entities.py) and the drop is only reported "
        "(#2897; PR #2948 adjudication e1181bd9e: 'adopting over the decision "
        "is not an available route'). Restoring the tag on replay XPASSes "
        "this to red — reopening the decision is what unblocks it."),
)
def test_a_tag_survives_a_rebuild(env):
    """AC1 + AC5: the point is retrievable BY TAG after ``rebuild_all``.

    Behavioural (a tag-filtered query), not a property read — a ``tags``
    property without its ``TAGGED`` edges does not satisfy this, which is
    exactly the "raw-list half-restore" the decision refuses.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "the sky is blue",
                           tags=["alpha"])["id"]
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _tag_hits(sdk, "alpha") == [pid], (
        "the tag must survive the rebuild and still resolve to the point")


# ══ The recorded decision, pinned positively (the enforcement half) ═══════

def test_the_recorded_decision_holds_behaviourally(env, caplog):
    """The DECIDED shape, asserted where a user can observe it.

    ``tests/test_projection.py::test_falkor_rebuild_undeclared_list_prop_denied``
    pins this at the **node** (``n.tags`` is ``None``) and at the **log** (a
    warning names ``tags``). What it does not pin is the user-visible
    consequence — that a tag-filtered retrieval returns nothing afterwards.
    That is the gap AC5 names, and this test closes it in the decided direction.

    ⚠️ This is the pin that must be **deliberately edited** if #2897 is ever
    reopened in favour of restoring tags. Failing here is the intended signal
    for a quiet reversal, not a bug to work around.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "the sky is blue",
                           tags=["alpha"])["id"]
    assert _tag_hits(sdk, "alpha") == [pid]

    with caplog.at_level(logging.WARNING, logger=_PROJECTION_LOGGER):
        caplog.clear()
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    # (a) user-visible: the tag retrieval is empty after the rebuild.
    assert _tag_hits(sdk, "alpha") == [], (
        "#2897/#3817: the DECIDED behaviour is that tags are not replayed — "
        "if this ever returns the point, the decision was reversed and this "
        "test (and the xfail above) must be updated on purpose")
    # (b) structural: no :Tag node survives either (the edge half).
    assert sdk._get_proj().g.query(
        "MATCH (t:Tag) RETURN t.name").result_set == [], (
        "#2897: the TAGGED-edge half is lost with the property half")
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN n.tags",
        params={"i": pid}).result_set == [[None]]
    # (c) DECLARED, not silent: the drop is reported by name (#2795 ind. 4 /
    # D7 — the requirement the refusal was accepted against).
    assert _drops_naming(caplog, "tags"), (
        "#2795 indicator 4 / WS-A D7: a prop that cannot be replayed must be "
        f"REPORTED, never dropped in silence; got {[r.getMessage() for r in caplog.records]}")


def test_the_drop_is_reported_on_every_replay_arm(env, caplog):
    """The refusal is only legitimate while it is LOUD on **every** arm.

    Three engines replay the journal: ``rebuild_all(dir)``,
    ``rebuild(EventLog)`` and ``apply(ev)``. A report on one arm and silence on
    another is the "silent drop" class #2795 exists to eliminate.
    """
    sdk, events = env
    sdk.create_point("statement", "the sky is blue", tags=["alpha"])
    proj = sdk._get_proj()

    arms = {
        "apply()": lambda: [proj.apply(e) for e in _journal(events)],
        "rebuild(EventLog)": lambda: proj.rebuild(
            EventLog(str(events / "events.jsonl")), confirm_destructive=True),
        "rebuild_all(dir)": lambda: proj.rebuild_all(
            str(events), confirm_destructive=True),
    }
    unreported = []
    for name, run in arms.items():
        with caplog.at_level(logging.WARNING, logger=_PROJECTION_LOGGER):
            caplog.clear()
            run()
        if not _drops_naming(caplog, "tags"):
            unreported.append(name)
    assert unreported == [], (
        f"the tags drop is silent on {unreported} — the refusal is only "
        "acceptable while it is reported (#2795 indicator 4)")


# ══ AC4 — negative control: the rebuild invents nothing ═══════════════════

def test_a_tag_that_is_not_in_the_journal_is_not_invented(env):
    """AC4: a graph-only tag must NOT be reconstructed.

    The ``TAGGED`` edge is written **straight into the graph** with no journal
    record. If the post-rebuild tag query still returned the point, the earlier
    assertions could pass for the wrong reason (tags recomputed from the graph
    rather than replayed from the journal) — this rules that out.
    """
    sdk, events = env
    proj = sdk._get_proj()
    # A tagged Point with NO journal record at all (the #548 graph-only door).
    proj.g.query(
        "CREATE (n:Point {id:'graph-only-tag', content:'raw', "
        "pointKind:'statement', status:'live', is_operator:false, "
        "tags:['gamma']})")
    proj.g.query(
        "MATCH (p:Point {id:'graph-only-tag'}) "
        "MERGE (t:Tag {name:'gamma'}) MERGE (p)-[:TAGGED]->(t)")
    assert _tag_hits(sdk, "gamma") == ["graph-only-tag"], (
        "precondition: the graph-only tag is retrievable before the rebuild")

    proj.rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "gamma") == [], (
        "#3817 AC4: the journal never recorded this tag, so the rebuild must "
        "not invent it — a hit here would mean tags are reconstructed from "
        "something other than the journal")


# ══ AC3 — a tag change is durable across a rebuild ════════════════════════

def test_a_removed_tag_is_not_resurrected(env):
    """AC3: the ``#3299``/``#2488`` class (a rebuild resurrecting deleted
    state), applied to tags.

    A rebuild that replayed the *creation* snapshot while ignoring the tag
    edit would resurrect ``drop``. The journal states both states (PointAdded
    carries the pair, PointRevised carries the removal), so the journal is a
    sufficient source for a faithful replay — the assertion is that the fold
    never produces anything outside that final state.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "tag churn",
                           tags=["keep", "drop"])["id"]
    sdk.update_point(pid, tags=["keep"])
    assert _tag_hits(sdk, "keep") == [pid]
    assert _tag_hits(sdk, "drop") == []
    revised = [e for e in _journal(events) if e.get("type") == "PointRevised"]
    assert revised and revised[-1].get("tags") == ["keep"], (
        "the journal must state the POST-removal list, or a faithful replay "
        "has no way to avoid resurrecting the removed tag")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "drop") == [], (
        "a removed tag must never be resurrected by a rebuild (#3299/#2488)")
    # Nothing outside the journal's final state may appear: a rebuilt tag set
    # is the replayed set, never a union of every state the point ever held.
    replayed = {t["name"] for t in sdk.list_tags() if t["count"]}
    assert replayed <= {"keep"}, (
        f"a rebuilt tag set contains state the journal's final record does "
        f"not carry: {sorted(replayed - {'keep'})}")

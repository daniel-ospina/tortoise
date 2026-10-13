"""#3817 — do a Point's TAGS survive ``rebuild_all``? (objective 6, lane 6)

Objective 6 requires that *"props, **tags** and EP state survive"* a rebuild.
`#2958` (`37d5ef00c`, PR #2958) proved the **props** half and left the tags half
unproven. This module is the tags half, measured through the **resolved query a
user path uses** (``TortoiseSDK.query_points_by_tag`` — the backing method of the
MCP ``tortoise_query(tag=...)`` surface), never by reading a field off an object.

## The decision this module used to cite — and what it actually is

Until #2897 landed, the assertions below were ``xfail(strict=True)`` on the
reading that ``main`` **deliberately refused** the tags replay, citing a code
comment (``_POINT_LIST_PROPS``), a test that pinned it, and a PR #2948
adjudication that lives only on an **unmerged** branch. Every one of those three
is the category the repo's own rule **excludes** from "a recorded decision"
(*"not merely an existing practice, and not a thing the code happens to do
today"*).

What *is* recorded points the other way, and rests on three artifacts — none of
which is code, a test, or an unmerged branch:

**1 · #3856 (OD-4 — "are tags permanent data or something we may rebuild?")
is CLOSED**, disposition **"ADOPT, withdraw the question"** — explicitly *"WORK,
not an owner decision"*. A Tag is an ``:Tag`` Object subclass shared via MERGE —
*"shared, MERGEd, first-class nodes are data"* — so permanence answers itself and
*"the owner is not asked"*. The work it names is literally *"#3817 — prove tags
survive ``rebuild_all``"*.

**2 · The launch plan's objective 6 done-state** (premise-labs
``product/2026-09-22-tortoise-beta-plan.md`` §6 — *"props, **tags** and EP state
survive"*): an acceptance criterion, **not** a decision section. It states the
required outcome without deciding the mechanism, so it corroborates the direction
rather than authorising it.

**3 · The 2026-09-14 design comment on this issue**, which records the chosen
mechanism as **D2**: *"``TAGGED`` joins the snapshot-derivable edge set … write
``n.tags`` in the props pass, and move ``_sync_tags`` (``sdk.py:2115``) into the
projection so ``_upsert_point_edges`` calls it in pass 2 — one reconciliation
routine for live and replay, no second implementation."* ⚠️ Read it as what it
is: that comment self-describes as a *design round posted before any research*, it
carries its own *"§9 · Open decision (recorded, not blocking)"* section, and it is
posted under the shared ``daniel-ospina`` account — which by this repo's own rule
cannot be attributed to a person. It is the DESIGN RECORD this module measures,
not a ruling that outranks the canonical ontology. What makes the direction safe
is that **nothing recorded points the other way** — not that this comment
outranks anything.

**Supporting, and deliberately demoted:** ``docs/ONTOLOGY.md`` (canonical) —
``TAGGED``: *"Tags are ``:Tag`` nodes (Object subclass, ``objectKind: tag``)
shared across Points via MERGE."* ⚠️ The row is CONFIRMING for the permanence
question and was unreliable for the implementation ones: it also carried a stale
*"Write-only today — no tag-filter query surfaced yet"* note, while the query this
module asserts through (``SDK.query_points_by_tag``) is exactly what the note says
does not exist, and it attributed creation to hosted-api ingestion alone. The row
was corrected in the same change; the ``objectKind``/Object-subclass claim is a
doc-vs-code divergence filed as **#8325**.

⚠️ An earlier version of this module said that disposition *"was recorded
**before** the refusal landed"*. **That was false and is deleted.** The refusal
landed in PR #2958 (``37d5ef00c``, merged 2026-09-17T15:58Z); #3856 was created
at 17:51Z and its disposition comment posted at 23:46Z — later, the same day.
The repo's rule turns on whether an artifact is a *recorded decision*, never on
the order in which two artifacts appeared, so the chronology was never
load-bearing — and a wrong date only invites a reader to distrust the rest.

So restoring tags on replay is the **implementation** of D2 / #3856 / objective
6, not a reversal of any decision — and this module was edited **deliberately**
(the ⚠️ it used to carry recorded what the requirement was).

## What the fold now does (and why it needs the journal, not the graph)

* ``_upsert_point_props`` writes the ``n.tags`` **property** half from the
  payload, **presence-and-store gated** (``_stated_tags``): a value the store
  holds EXACTLY as stated (``_store_holds_prop_value``) is written verbatim (a
  non-list scalar included), while ``None``/absent and any value the transport
  gate refuses or the numeric domain would alter states NO property — which is
  what the live create does too (it builds the node from a
  ``CREATE (n:Point {…})`` map, not a ``SET``).
  ``_upsert_point_edges`` (pass 2) reconciles the ``:Tag``/``TAGGED`` **edge**
  half from the node's own final value. ``_revise_point`` folds a ``tags`` EDIT
  — and a value the store cannot hold removes the property exactly as ``None``
  does (a dict is degraded to null by ``#7369`` before dispatch, measured) — then
  re-syncs with the live expression ``ev["tags"] or []``. One reconciliation
  routine (the projection's ``_sync_tags``) is shared by the live SDK writers, so
  live and replay cannot drift **on the property**. ⚠️ The EDGE half for a truthy
  non-list value does not converge, and no instrument reports it — the bound is
  stated on ``_sync_tags`` itself.
* The journal is a **sufficient** source and needs no backfill or new event
  type: the ``PointAdded`` snapshot carries ``tags`` and a tag-editing
  ``PointRevised`` carries the new list (asserted below — AC2 keeps that pill).
* The **graph-only** rule is stated, not assumed: a Point with no journal record
  is preserved by the #548 pre-wipe **snapshot**, which carries its props — so
  its tags are part of what that record preserves. A tag that no record (journal
  *or* snapshot) authored is still never invented (AC4).

## Mutations that must RED (evidence standard)

* **Reverse the fix** — drop the ``n.tags`` clause (or the pass-2 sync, or the
  ``_revise_point`` fold): AC1/AC5 fails.
* **Re-gate the fold on ``isinstance(ev.get("tags"), list)``** — the clear form
  (``tags=None``) then skips BOTH halves, pass 1a's creation list survives on
  the node and pass 2 resurrects the tag; ``test_a_cleared_tag_is_not_
  resurrected`` fails. This is the mutation the suite most needs to be able to catch:
  re-gating the fold on the payload's key presence makes it fail, and the clear
  form is the pinned live idiom
  (``tests/test_sdk.py::test_update_none_clears_tags``).
* **Write only a list in ``_upsert_point_props``** — a scalar ``tags`` the live
  writer stored is dropped on replay and ``check_consistency`` reports a
  ``tags`` divergence on a graph the live path wrote:
  ``test_a_non_list_tag_value_is_restored_verbatim`` fails.
* **Steal from the graph instead of the journal** — recompute tags from the
  pre-wipe node for a journaled Point whose tag is graph-only: AC4 fails.
* **Sync from the PointAdded payload instead of the node** — the removed tag is
  resurrected: AC3 fails.
* **Stop the journal from carrying the tag** — strip ``tags`` from the emitted
  snapshot: AC2 fails (the journal, not the fold, is then the source of the gap).

## Lane

Runs on both lanes: the SDK is built from a tmp path (redirected to
``TORTOISE_DB_URI`` when one is set, embedded otherwise), and ``rebuild_all``
replays the JSONL journal on disk. Locally:

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_3817_tags_rebuild_durability.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json

import pytest

from tortoise.consistency import check_consistency
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK


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


def _tag_nodes(sdk) -> set[str]:
    return {r[0] for r in sdk._get_proj().g.query(
        "MATCH (t:Tag) RETURN t.name").result_set}


# ══ AC2 — the source of truth is named: the JOURNAL carries the tag ═══════

def test_the_journal_carries_the_tag(env):
    """AC2: the tag is reconstructible from the journal alone.

    This is the measurement that locates the gap in the **fold**, not in the
    journal: the fix has the payload it needs, and no backfill/migration is
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


# ══ AC1 / AC5 — the requirement, through the user-facing query ═══════════

def test_the_same_query_returns_the_point_before_the_rebuild(env):
    """Control — makes any failure below ATTRIBUTABLE.

    Without this, an assertion on "the tag is retrievable after a rebuild" could
    fail for the wrong reason (a wrong query, a wrong id, an unseeded graph).
    The identical resolved query runs pre-rebuild and must HIT.
    """
    sdk, _events = env
    pid = sdk.create_point("statement", "findable before rebuild",
                           tags=["alpha"])["id"]
    assert _tag_hits(sdk, "alpha") == [pid], (
        "the live path must make the point retrievable by tag")


def test_a_tag_survives_a_rebuild(env):
    """AC1 + AC5: the point is retrievable BY TAG after ``rebuild_all``, and
    both halves are present — the ``n.tags`` property AND the ``TAGGED`` edge.

    Behavioural (a tag-filtered query), not a property read: a ``tags``
    property without its ``TAGGED`` edges does not satisfy this, which is
    exactly the "raw-list half-restore" the old refusal was protecting against.
    The fix restores BOTH halves, so the concern does not apply.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "the sky is blue",
                           tags=["alpha"])["id"]
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _tag_hits(sdk, "alpha") == [pid], (
        "the tag must survive the rebuild and still resolve to the point")
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN n.tags",
        params={"i": pid}).result_set == [[["alpha"]]], (
        "the property half must be restored too — the edge query alone would "
        "not catch a node left without `n.tags`")
    assert _tag_nodes(sdk) == {"alpha"}, "the :Tag node half must survive"


def test_the_tag_survives_on_every_replay_arm(env):
    """The three engines that replay the journal all restore the tag.

    ``rebuild_all(dir)``, ``rebuild(EventLog)`` and ``apply(ev)`` are the same
    contract (cf. the old drop-report test this replaces, which asserted the
    direct inverse): a fix on one arm and a loss on another is the live/replay
    divergence class #2897 is about.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "the sky is blue", tags=["alpha"])["id"]
    proj = sdk._get_proj()

    arms = {
        "apply()": lambda: [proj.apply(e) for e in _journal(events)],
        "rebuild(EventLog)": lambda: proj.rebuild(
            EventLog(str(events / "events.jsonl")), confirm_destructive=True),
        "rebuild_all(dir)": lambda: proj.rebuild_all(
            str(events), confirm_destructive=True),
    }
    missing = []
    for name, run in arms.items():
        proj.g.query("MATCH (n) DETACH DELETE n")
        run()
        if _tag_hits(sdk, "alpha") != [pid] or _tag_nodes(sdk) != {"alpha"}:
            missing.append(name)
    assert missing == [], (
        f"the tag was lost on {missing} — every replay arm must restore the "
        "property AND the :Tag/TAGGED half")


# ══ AC4 — negative control: the rebuild invents nothing ═══════════════════

def test_a_tag_that_is_not_in_the_journal_is_not_invented(env):
    """AC4: a tag no record authored must NOT be reconstructed.

    The Point IS journaled (without tags) and the ``TAGGED`` edge is written
    **straight into the graph** with no journal record. If the post-rebuild tag
    query still returned the point, the assertions above could pass for the
    wrong reason — tags recomputed from the pre-wipe graph rather than replayed
    from the journal. This rules that out, and isolates "the tag is not
    invented" from "the point is gone" (the point is restored; the tag is not).
    """
    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "no tags in the journal")["id"]
    proj.g.query("MATCH (p:Point {id:$id}) SET p.tags=['gamma']",
                 params={"id": pid})
    proj.g.query(
        "MATCH (p:Point {id:$id}) "
        "MERGE (t:Tag {name:'gamma'}) MERGE (p)-[:TAGGED]->(t)",
        params={"id": pid})
    assert _tag_hits(sdk, "gamma") == [pid], (
        "precondition: the graph-only tag is retrievable before the rebuild")

    proj.rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "gamma") == [], (
        "#3817 AC4: the journal never recorded this tag, so the rebuild must "
        "not invent it — a hit here would mean tags are reconstructed from "
        "something other than the record set")
    assert _tag_nodes(sdk) == set(), "no :Tag node may be invented either"


def test_a_graph_only_point_keeps_its_tag_via_the_548_snapshot(env):
    """The stated graph-only rule, pinned so it is a decision rather than a
    surprise.

    A Point with **no journal record at all** is preserved by the #548 pre-wipe
    snapshot, which carries its props (all but the declared-volatile ones). Its
    tags therefore ride that record — this is preservation, not invention, and
    it is the case the original AC4 premise collided with when it asserted that
    a graph-only tag must always vanish. The contrast with the test above is the
    whole point: a tag the *snapshot* carries survives; one that neither the
    journal nor the snapshot carries does not.
    """
    sdk, events = env
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (n:Point {id:'graph-only-tag', content:'raw', "
        "pointKind:'statement', status:'live', is_operator:false, "
        "tags:['gamma']})")
    proj.g.query(
        "MATCH (p:Point {id:'graph-only-tag'}) "
        "MERGE (t:Tag {name:'gamma'}) MERGE (p)-[:TAGGED]->(t)")
    assert _tag_hits(sdk, "gamma") == ["graph-only-tag"]

    proj.rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "gamma") == ["graph-only-tag"], (
        "#548: the pre-wipe snapshot is a RECORD — a graph-only Point survives "
        "it and so do its tags; if this ever regresses, the snapshot's prop "
        "carriage changed, not the tag rule")


# ══ AC3 — a tag change is durable across a rebuild ════════════════════════

def test_a_removed_tag_is_not_resurrected(env):
    """AC3: the ``#3299``/``#2488`` class (a rebuild resurrecting deleted
    state), applied to tags.

    A rebuild that replayed the *creation* snapshot while ignoring the tag
    edit would resurrect ``drop``. The journal states both states (PointAdded
    carries the pair, PointRevised carries the removal), so the journal is a
    sufficient source for a faithful replay — the assertion is that the fold
    never produces anything outside that final state. This is the test that
    forces the pass-2 sync to read the NODE's final value rather than the
    PointAdded payload.
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
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN n.tags",
        params={"i": pid}).result_set == [[["keep"]]], (
        "the node's tag property must hold the journal's FINAL list")
    # Nothing outside the journal's final state may appear: a rebuilt tag set
    # is the replayed set, never a union of every state the point ever held.
    replayed = {t["name"] for t in sdk.list_tags() if t["count"]}
    assert replayed == {"keep"}, (
        f"a rebuilt tag set contains state the journal's final record does "
        f"not carry: {sorted(replayed - {'keep'})}")


@pytest.mark.parametrize("clear_value", [None, ""], ids=["None", "empty-string"])
def test_a_cleared_tag_is_not_resurrected(env, clear_value):
    """AC3, the CLEAR form: a documented clear must be as durable as a removal.

    ``update_point(tags=None)`` is the pinned clear idiom
    (``tests/test_sdk.py::test_update_none_clears_tags``): the live write is
    ``SET n += {tags: null}`` — which REMOVES the property (measured on the
    embedded engine) — plus ``_sync_tags(id, props["tags"] or [])``, so BOTH
    halves go. A replay fold gated on ``isinstance(ev["tags"], list)`` skipped
    both: pass 1a's creation list stayed on the node and pass 2 re-derived the
    edges from it, so the cleared tag came back. Worse, the checker's reference
    fold (``_apply_one``) skipped the same clause, so ``check_consistency`` was
    BLIND to it — both sides agreed on the wrong value (#8317 review P1,
    reproduced). ``""`` rides the same path with a property that is SET rather
    than removed, so both forms are asserted.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "clear me",
                           tags=["alpha", "beta"])["id"]
    sdk.update_point(pid, tags=clear_value)
    assert _tag_hits(sdk, "alpha") == [] and _tag_hits(sdk, "beta") == [], (
        "precondition: the live clear must remove the tag, or this measures "
        "the wrong thing")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "alpha") == [], (
        "a cleared tag must not be resurrected by a rebuild — the fold is "
        "skipping the clear form (#8317 review P1)")
    assert _tag_hits(sdk, "beta") == [], (
        "...nor may the sibling it was cleared WITH")
    assert _tag_nodes(sdk) == set(), "no orphan :Tag node may survive either"
    expected = "" if isinstance(clear_value, str) else None
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN n.tags",
        params={"i": pid}).result_set == [[expected]], (
        "the property half must match what the live writer left: removed for "
        "None, the empty string for \"\"")


def test_a_non_list_tag_value_is_restored_verbatim(env):
    """A scalar ``tags`` value is junk input, but the live writer STORES it —
    so a replay must too, or ``check_consistency`` reports a divergence on a
    graph the live path itself wrote.

    ``create_point`` writes ``tags`` in its ``CREATE (n:Point {…})`` property
    MAP while ``update_point`` merges it with ``SET n += $props`` — either way
    ``tags="scalar"`` lands as a plain property (``_sync_tags`` is list-only,
    so it gets no edges) — pinned by
    ``tests/test_sdk.py::test_update_non_list_tags_no_crash``. The replay's
    list-only clause dropped the property while the checker — which now
    COMPARES ``tags`` — still saw it in the journal: a false ``content``
    divergence whose ``action`` (*reconcile by replaying the journal*) could
    never clear it, because replay produced exactly the graph that was already
    there (#8317 review P1, reproduced).
    """
    sdk, events = env
    pid = sdk.create_point("statement", "scalar tags",
                           tags="scalar")["id"]
    assert sdk.get_point(pid).get("tags") == "scalar", (
        "precondition: the live writer stores a non-list value verbatim")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN n.tags",
        params={"i": pid}).result_set == [["scalar"]], (
        "the scalar the live writer stored must be restored verbatim, not "
        "dropped for not being a list")
    assert _tag_nodes(sdk) == set(), (
        "a non-list value is not a tag membership — it must not mint :Tag "
        "nodes, on the replay path any more than on the live one")


def test_a_non_persistable_tag_edit_removes_the_property_like_live(env):
    """The EDIT form of the carriage widening: a ``tags`` value the
    STORE cannot hold must clear the property, exactly as ``None`` does — the
    dict is degraded to null by ``#7369`` before the live writer ever sees it.

    Measured live semantics for ``update_point(tags={"d": 1})``: the property
    is **removed** (``'tags' IN keys(n)`` is False) and the ``TAGGED`` edges are
    **left untouched** (``_sync_tags`` is list-only, so it never runs). The
    round-1 fold gated on ``isinstance(ev["tags"], list)``, which made
    ``None``-the-clear and dict-the-junk ONE case: the node kept its previous
    list (``["alpha"]``) and pass 2 re-derived edges from it, resurrecting a
    membership the live path had removed from the property — while the
    checker's own fold (``_apply_one``) took the dict at face value. The two
    halves were unreconcilable in both directions, so no ``action`` the report
    could offer would ever clear the divergence (#8317 review R2-B1/R2-C4).

    The PROPERTY half reaches parity (both live and replay end with no
    ``n.tags``) — that is the divergence the round-1 fold produced, and the
    checker's UNCLEARABLE red it caused. The EDGE half does NOT reach parity
    for this junk value, and that bound is stated on ``_sync_tags`` — which
    owns it, filed as **#8333** — rather than asserted away: live leaves its
    pre-existing edge in place (the guard returns early), while a replay starts
    from a wiped graph and derives no edge from a non-array node value, so the
    rebuilt graph is the self-consistent one and live carries the stale
    artifact (the property is the SOURCE of the edge half). Both halves are
    pinned here, so a change to either is visible.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "dict tags", tags=["alpha"])["id"]
    sdk.update_point(pid, tags={"d": 1})
    assert _tag_hits(sdk, "alpha") == [pid], (
        "precondition: the live writer LEAVES the existing edge for a "
        "non-list value (the `_sync_tags` bound) — if this changes, the "
        "replay's job changes with it")
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN 'tags' IN keys(n)",
        params={"i": pid}).result_set == [[False]], (
        "precondition: the live writer REMOVES the property for a value the "
        "store cannot hold (a dict is null-degraded by #7369)")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN 'tags' IN keys(n)",
        params={"i": pid}).result_set == [[False]], (
        "replay resurrected a tag property the live writer had removed — the "
        "non-persistable value must clear it, exactly as None does")
    assert _tag_hits(sdk, "alpha") == [], (
        "the stated `_sync_tags` bound (#8333, which owns it): a rebuild "
        "starts from a wiped graph, so it derives no edge from a non-array "
        "node value and cannot reproduce the stale edge live left behind — "
        "pin it so a future change to either half is visible")
    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "the PROPERTY halves agree, so the checker must not red a graph the "
        "live path itself wrote: " + str(report.get("divergent_points")))
    assert "tags" in report["uncarried_journal_fields"], (
        "the dropped value must still be NAMED — silence would leave an "
        "operator unable to tell this from a faithful replay: "
        f"{report['uncarried_journal_fields']}")


def test_a_tuple_tag_value_syncs_edges_on_both_arms(env):
    """A TUPLE is an ARRAY to the engine, so it must not be the one value on
    which live and replay disagree (#8317 review R2).

    The live gate tested ``isinstance(tags, list)``, so a tuple created no
    ``TAGGED`` edges — while the journal round-trips the tuple to a LIST and
    the replay's pass 2 re-derived them, inventing membership the live graph
    never had. Both arms now treat a tuple as the array the engine stores
    (``_is_persistable_prop_value``), so ``query_points_by_tag`` answers the
    same before and after a rebuild.

    (1) Fails on the pre-fix code at the FIRST assertion: a list-only live
        gate synced no edge for the tuple, which is exactly the divergence the
        replay's list-shaped journal produced.
    (2) The EDIT form is asserted too — a tuple edit is an array edit, so the
        live path must move the membership, not leave the creation edges stale.
    """
    sdk, events = env
    pid = sdk.create_point("statement", "tuple tags",
                           tags=("alpha", "beta"))["id"]
    assert _tag_hits(sdk, "alpha") == [pid], (
        "precondition: a tuple is an array to the engine, so the live path "
        "must sync its edges — a list-only live gate is what the replay "
        "diverged from (#8317 review R2)")
    assert _tag_hits(sdk, "beta") == [pid]

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "alpha") == [pid], (
        "the tuple's tag must survive the rebuild exactly as the live graph "
        "held it — the replay must not invent membership live never wrote")
    assert _tag_hits(sdk, "beta") == [pid]

    sdk.update_point(pid, tags=("gamma",))
    assert _tag_hits(sdk, "gamma") == [pid], (
        "a tuple EDIT is an array edit: the live path must sync it, not "
        "leave the creation edges stale")
    assert _tag_hits(sdk, "alpha") == [], (
        "the replaced tuple's old membership must be removed live")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "gamma") == [pid] and _tag_hits(sdk, "alpha") == [], (
        "the tuple edit must replay to the same membership the live graph "
        "held (#8317 review R2)")
    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "a faithful tuple replay must not be red: "
        + str(report.get("divergent_points")))


# ══ The writer's OUTCOME, not the key's presence ═══════════════════════════
#
# The fold must carry a previous `tags` when the payload states no WRITABLE
# value: the clause is `_stated_tags`-gated, so `tags: null` and an unwritable
# value write NO property, and pass 1a's MERGE therefore PRESERVES the node's
# list. Keying on omission alone folded
# `tags: null` as a CLEAR, so the journal view lost the value the graph held and
# `check_consistency` red a graph the write itself produced — reachable from the
# ordinary ingest seam (`EventAPI.add_point(id=…, tags=None)`), not only a
# hand-written journal. Both directions are pinned below, and so is the
# `_revise_point` sync gate: a `:Tag` MERGE KEY cannot be null, so an edit
# stating `[None]` must not send it (the sync would raise mid-rebuild).


@pytest.mark.parametrize("second", [None, {"a": 1}], ids=["null", "unwritable"])
def test_a_reemit_that_states_no_writable_tag_keeps_the_graphs_value(env, second):
    """A second `PointAdded` stating `tags: null` (or an unwritable value)
    writes NO property, so the MERGE keeps the tag the graph holds.

    The reference fold must ask the WRITER'S outcome (`_stated_tags`), not the
    key's presence: with only the omission-only test, this shape red the
    checker on a graph the write itself produced.
    """
    sdk, events = env
    _write_journal(events, [
        {"type": "PointAdded",
         "point": {"id": "p1", "content": "v1", "tags": ["alpha"]}},
        {"type": "PointAdded",
         "point": {"id": "p1", "content": "v2", "tags": second}},
    ])

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "alpha") == ["p1"], (
        "the re-emit writes no tag property (MERGE preserves), so the derived "
        "edge half must survive it")
    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "the fold must mirror the writer's OUTCOME, not the key's presence: "
        + str(report.get("divergent_points")))


def test_an_edit_whose_tag_array_cannot_be_a_merge_key_does_not_abort(env):
    """A `PointRevised` stating `[None]` must not abort the rebuild after the
    wipe — `:Tag`'s MERGE key cannot be null, so the edge sync is gated on the
    same write decision the property clause uses.

    Mutation-checked: removing the gate leaves every other test in this module
    green while `rebuild_all` raises `Cannot merge node using null property
    value` — the #325/#3689 class (a malformed record must DROP, never strand
    the recovery).
    """
    sdk, events = env
    _write_journal(events, [
        {"type": "PointAdded",
         "point": {"id": "p1", "content": "v1", "tags": ["alpha"]}},
        {"type": "PointRevised", "id": "p1", "tags": [None]},
    ])

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "alpha") == [], (
        "the property half is cleared, so the derived edge half must follow it")


# ══ The checker's CARRIAGE predicate is the writer's EFFECTIVE one ═════════
#
# The `tags` branch of `_uncarried` classified carriage
# with `_is_persistable_prop_value` — the SHAPE allowlist — while the writer's
# effective rule is that predicate PLUS the `#7369` transport gate (a value
# `_value_ok` refuses is nulled before dispatch) PLUS the store's numeric domain
# (an int outside INT64 is CLAMPED by a 4.16–4.20 engine, and refused at
# parameter parse by a 6.x one). A value that passes the shape
# test but fails either of the other two is written by nobody and was named by
# nobody, so it fell through to the field-aware comparison: the journal held it,
# the graph did not (or held a clamped value), and `check_consistency` reported
# an UNCLEARABLE `divergence="content"` whose `action` ("reconcile by replaying
# the journal") reproduces exactly the graph already present. Each `value` below
# is measured on the embedded engine; the first three FAIL on the pre-fix code
# (`ok is False`), which is the non-vacuity proof. The map case is a control:
# the shape predicate already refused it, so it passed pre-fix too.


def _write_journal(events, records: list[dict]) -> None:
    """Replace the JSONL journal with ``records`` — the hand-edited/alternate-
    producer shape the finder's own repro used, so the test cannot be satisfied
    by the SDK's `_sanitize_props` boundary instead of the replay boundary."""
    (events / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(["a", float("nan")], id="non-finite-float"),
        pytest.param(["a", "b\x00c"], id="nul-string"),
        pytest.param(["a", 2 ** 70], id="int-outside-int64"),
        pytest.param({"a": 1}, id="map-shape (control)"),
    ],
)
def test_a_tag_value_the_store_cannot_hold_is_named_not_a_false_divergence(
        env, value):
    """A `tags` value the writer's EFFECTIVE gate refuses is UNCURRIED, so the
    checker NAMES the drop instead of comparing a value the graph never holds.

    The graph is produced by the replay itself, so any `divergence` here is a
    false red about the graph this code just wrote. `main` named every
    list-valued `tags` as uncarried; #2897 correctly started COMPARING writable
    lists, and this pins the boundary of that widening: a value either the
    transport gate nulls or an engine alters (4.16–4.20 clamps it; 6.x refuses
    it at parameter parse) is still a named drop, not a comparison.
    """
    sdk, events = env
    _write_journal(events, [{
        "type": "PointAdded",
        "point": {"id": "p1", "content": "c", "tags": value},
    }])

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "a graph the replay itself produced must not be reported as diverged: "
        + str(report.get("divergent_points")))
    assert "tags" in report["uncarried_journal_fields"], (
        "the value was dropped, so the drop must be NAMED — silence leaves an "
        "operator unable to tell it from a faithful replay: "
        f"{report['uncarried_journal_fields']}")


# ══ A bare same-id re-emit MERGEs — the fold must mirror that ═════════════
#
# `_upsert_point_props` writes `tags` with a
# PRESENCE-based clause and pass 1a MERGEs, so a same-id `PointAdded` whose
# payload OMITS `tags` PRESERVES the node's list — while the reference fold's
# PointAdded arm REPLACED the entry and dropped it. Journal view had no tags,
# the graph had them, and `check_consistency` reported an un-clearable
# `divergence="content"`. This is a production shape: the capture turn loop
# re-emits `PointAdded` for a turn on every capture of an existing session
# window with a hand-built snapshot that carries no `tags` (`tortoise/sdk.py`).
# The test FAILS on the pre-fix code (`ok is False`).


def test_a_bare_reemit_that_omits_tags_does_not_drop_them(env):
    """A re-emit that states no `tags` must not delete the tag the graph holds.

    Differential against the node's own value: the re-emit is a MERGE, so the
    rebuild keeps `alpha` (the property) and its `TAGGED` half. The reference
    fold must agree with that, or the export/health gate is permanently red on
    a faithful graph.
    """
    sdk, events = env
    _write_journal(events, [
        {"type": "PointAdded",
         "point": {"id": "p1", "content": "v1", "tags": ["alpha"]}},
        {"type": "PointAdded", "point": {"id": "p1", "content": "v2"}},
    ])

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "alpha") == ["p1"], (
        "the re-emit MERGEs, so the graph keeps the tag — if this changes, the "
        "fold's job changes with it")
    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "the fold dropped a tag the graph's own MERGE preserved, so a faithful "
        "graph read as diverged: " + str(report.get("divergent_points")))


def test_a_recreation_that_omits_tags_still_drops_the_dead_incarnations(env):
    """The re-emit fix must NOT reach a real delete→recreate.

    A re-creation is a FRESH node live and the graph wipes `n.tags` (the #4042
    pass-1a wipe), so the fold must drop the dead incarnation's list — the
    `journal_recreate` gate. This is the anti-regression control for the fix
    above: a naive "preserve on omit" fold leaks `ghost` here.
    """
    sdk, events = env
    _write_journal(events, [
        {"type": "PointAdded",
         "point": {"id": "p1", "content": "v1", "tags": ["ghost"]}},
        {"type": "EntityMutated", "op": "delete", "id": "p1",
         "label": "Point"},
        {"type": "PointAdded", "point": {"id": "p1", "content": "v2"}},
    ])

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _tag_hits(sdk, "ghost") == [], (
        "the dead incarnation's tag leaked onto the re-created node — the "
        "fold must treat a re-creation as a fresh node, not a MERGE")
    report = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert report["ok"] is True, (
        "the wipe and the fold must agree on a re-creation: "
        + str(report.get("divergent_points")))

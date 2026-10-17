"""#8398 — the ``about*`` links of a ``create_event`` must survive ``rebuild_all``.

``create_event(aboutObject=…)`` wires its links as GRAPH EDGES (its docstring
says so), so they were never among the writes ``rebuild_all`` replays: the
``EventRecorded`` record carried no link field at all. ``derived ==
replay(journal)`` was therefore FALSE for exactly this class — the edge lived
until the next rebuild and was gone after it.

The control matters as much as the defect: ``create_operator``'s IMPL edges
journal an ``OperatorAdded`` record and survived the same rebuild unchanged, so
this was never a general rebuild problem — the Event→entity link specifically
was not journaled.

Fix shape: the SDK classifies each about-link against the LIVE graph at write
time, then RESOLVES it read-only (``resolve_about_link``) and journals the
precise decision the live write will carry: ``{"rel", "how": "id",
"targets": [...]}`` for an id-resolved link, plus ``target_label`` /
``resolve_key`` / ``mint_if_absent`` for a name-path one. Replay never re-runs
the cascade — it repeats that decision: the endpoint by id when it resolves,
else the same label/key live matched on, minting only when the live construction
minted. ``how: "name"`` survives as a DEFENSIVE branch only — ``aboutLinks``
is introduced by this unmerged change (it does not exist on ``origin/main``), so
no journal in the wild can hold that form and the branch defends records an
earlier commit of this branch wrote — matching ``replay_about_links``'s
docstring. The record carries links as data, NOT as node
properties — an ``about*`` prop must never become a stored string property.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_8398_aboutobject_replay.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from tortoise.projection import _replay_write_scope
from tortoise.sdk import TortoiseSDK

ABOUT_RELS = ("aboutSubject", "aboutObject", "aboutPoint", "aboutDocument")


@pytest.fixture
def wired(tmp_path):
    """(sdk, projection, events_dir, journal_path) with the journal wired."""
    db = os.path.join(str(tmp_path), "g6.db")
    events = tmp_path / "events"
    events.mkdir()
    journal = events / "events.jsonl"
    sdk = TortoiseSDK(db, event_log_path=str(journal))
    yield sdk, sdk._get_proj(), events, journal
    sdk.close()


def _nid(node) -> str:
    if not isinstance(node, dict):
        return node
    return node.get("id") or node.get("uuid")


class _DirLog:
    """Minimal ``EventLog`` over a DIRECTORY, in ``sorted(os.listdir())`` order.

    ``rebuild`` takes one EventLog (a single file); ``rebuild_all`` reads a
    directory. This adapter gives the single-file engine the same rotated-journal
    view, so the two engines can be compared on identical input.
    """

    def __init__(self, path) -> None:
        self.path = str(path)

    def read_all(self):
        out = []
        for name in sorted(os.listdir(self.path)):
            with open(os.path.join(self.path, name), encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        out.append(json.loads(line))
        return out


def _counts(proj) -> dict[str, int]:
    return {
        rel: proj.g.query(
            f"MATCH ()-[x:{rel}]->() RETURN count(x)").result_set[0][0]
        for rel in ABOUT_RELS
    }


def _records(journal) -> list[dict]:
    with open(journal, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _event_records(journal, name: str) -> list[dict]:
    return [
        r for r in _records(journal)
        if r.get("type") == "EventRecorded" and r.get("name") == name
    ]


def test_the_observation_link_survives_a_rebuild(wired) -> None:
    """The #8398 repro, with ``create_operator``'s IMPL edge as the control."""
    sdk, proj, events, _journal = wired
    indicator = sdk.create_object("I", objectKind="indicator")
    sdk.create_event("obs", "observation", aboutObject=_nid(indicator))
    a, b = sdk.create_object("A"), sdk.create_object("B")
    sdk.create_operator("IMPL", _nid(a), [_nid(b)],
                        label="measuredBy", direction="unidirectional")

    def snapshot() -> tuple[int, int]:
        return (
            proj.g.query(
                "MATCH ()-[x:aboutObject]->() RETURN count(x)").result_set[0][0],
            proj.g.query(
                "MATCH ()-[x:IMPL]->() RETURN count(x)").result_set[0][0],
        )

    before = snapshot()
    assert before[0] >= 1, "the observation link was never wired live"
    proj.rebuild_all(str(events), confirm_destructive=True)
    after = snapshot()

    # The control first: if IMPL had dropped too this would be a general
    # rebuild complaint, not the Event→entity defect.
    assert after[1] == before[1], (
        f"the IMPL control changed across rebuild ({before[1]} → {after[1]}) — "
        "this is no longer the #8398 class")
    assert after[0] == before[0], (
        f"the aboutObject link was dropped by rebuild_all ({before[0]} → "
        f"{after[0]}): the Event→entity edge is wired live but not replayed")


def test_replay_reproduces_the_live_about_counts(wired) -> None:
    """The invariant is LIVE == REPLAY, per rel — not a count of four.

    ``aboutDocument`` with a URL is NOT wired by the live write (it takes the
    resolve-or-mint name path), so this test must not claim document coverage:
    it asserts replay reproduces whatever live produced, per rel, and a second
    rebuild changes nothing (MERGE, not append).
    """
    sdk, proj, events, _journal = wired
    obj = _nid(sdk.create_object("TheObject"))
    sub = _nid(sdk.create_subject("TheSubject"))
    point = _nid(sdk.create_point("the point", "a point about something"))
    sdk.create_event("all four", "event", aboutObject=obj, aboutSubject=sub,
                     aboutPoint=point, aboutDocument="https://example.test/doc")

    before = _counts(proj)
    proj.rebuild_all(str(events), confirm_destructive=True)
    after_one = _counts(proj)
    proj.rebuild_all(str(events), confirm_destructive=True)
    after_two = _counts(proj)

    assert after_one == before, (
        "a rebuild changed the about* edge counts per rel "
        f"(aboutSubject/aboutObject/aboutPoint/aboutDocument): "
        f"{before} → {after_one}")
    assert after_two == after_one, (
        "replay is not idempotent — a second rebuild kept adding edges: "
        f"{after_one} → {after_two}")
    # The record carries the links as DATA; replay must not fold the field onto
    # the node (the defect's other direction, now that the field exists).
    leak = proj.g.query(
        "MATCH (e:Event {name:'all four'}) RETURN e.aboutLinks").result_set
    assert leak == [[None]], f"replay folded aboutLinks onto the Event: {leak}"


def test_the_record_journals_the_precise_link_form(wired) -> None:
    """The record carries the RESOLVED link, for BOTH paths.

    ``how`` is decided live: a value that resolved against the rel's own target
    label journals ``id`` with its value. A value that did not resolves through
    the rel-IGNORING cascade, so the record must pin what the cascade actually
    produced — here a ``:Subject`` stub under ``aboutSubject``, NOT the
    ``aboutDocument`` the caller asked for (#8398 review P1-2). """
    sdk, _proj, _events, journal = wired
    obj = _nid(sdk.create_object("TheObject"))
    sdk.create_event("marked", "event", aboutObject=obj,
                     aboutDocument="https://example.test/doc")
    record = _event_records(journal, "marked")[0]
    links = {link["rel"]: link for link in record["aboutLinks"]}

    assert links["aboutObject"]["how"] == "id", (
        "a value that resolved to the rel's target label must journal as 'id'")
    assert links["aboutObject"]["targets"] == [obj], (
        "the RESOLVED target value must be journaled, so replay repeats an exact "
        f"endpoint instead of re-deriving the cascade: {links['aboutObject']!r}")
    assert "aboutSubject" in links, (
        "an unresolved aboutDocument value takes the rel-IGNORING cascade, which "
        f"mints a Subject stub under aboutSubject: {record['aboutLinks']!r}")
    pinned = links["aboutSubject"]
    assert (pinned["how"] == "id" and pinned["target_label"] == "Subject"
            and pinned["resolve_key"] == "https://example.test/doc"
            and pinned["mint_if_absent"] is True), (
        "the cascade's answer must be PINNED (rel, label, resolve key, and the "
        f"mint claim) for replay to repeat it: {pinned!r}")
    assert pinned["targets"] == ["https://example.test/doc"], pinned


def test_about_links_never_become_node_properties(wired) -> None:
    """The record carries the links as DATA; the folded node must stay clean.

    ``create_event`` wires edges *rather than storing string properties* — the
    journal field must not leak onto the Event node in either direction.
    """
    sdk, proj, _events, _journal = wired
    obj = _nid(sdk.create_object("TheObject"))
    sdk.create_event("marked", "event", aboutObject=obj)
    row = proj.g.query(
        "MATCH (e:Event {name:'marked'}) RETURN e.aboutObject, e.aboutLinks"
    ).result_set
    assert row and row[0] == [None, None], (
        f"the about link leaked onto the node as a property: {row}")


def test_an_unresolvable_handle_is_not_journaled(wired) -> None:
    """No edge is wired live for a stale handle (#3586), so nothing replays."""
    sdk, proj, _events, journal = wired
    sdk.create_event("stale ref", "event", aboutObject="obj-" + "a" * 26)
    record = _event_records(journal, "stale ref")[0]
    assert not record.get("aboutLinks"), (
        "an unresolvable handle wired NO edge, so the record must not claim a "
        f"link to replay: {record.get('aboutLinks')!r}")
    assert _counts(proj)["aboutObject"] == 0


def test_the_link_survives_a_ROTATED_journal(wired) -> None:
    """The replay stream is filename order, not chronology.

    ``rebuild_all`` reads ``sorted(os.listdir(log_dir))``, and pass-1a hoists
    only ``PointAdded``/``OperatorAdded`` — so if the target's registration sits
    in a LATER file than the ``EventRecorded`` that links to it, an inline
    re-wire would run before the target exists and drop the link silently. The
    wiring is buffered into a trailing sweep for exactly this reason. Here the
    event is forced into a file that sorts BEFORE the object registrations.
    """
    sdk, proj, events, journal = wired
    obj = _nid(sdk.create_object("TheObject"))
    sdk.create_event("obs", "event", aboutObject=obj)
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _counts(proj)["aboutObject"] == 1

    # Re-cut the journal: the EventRecorded alone in "a-events.jsonl", every
    # other record in "b-rest.jsonl" -> the event replays FIRST.
    with open(journal, encoding="utf-8") as fh:
        records = [line for line in fh if line.strip()]
    event_lines = [ln for ln in records if '"EventRecorded"' in ln]
    rest = [ln for ln in records if '"EventRecorded"' not in ln]
    assert event_lines and rest
    os.remove(journal)
    with open(os.path.join(str(events), "a-events.jsonl"), "w", encoding="utf-8") as fh:
        fh.writelines(event_lines)
    with open(os.path.join(str(events), "b-rest.jsonl"), "w", encoding="utf-8") as fh:
        fh.writelines(rest)

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _counts(proj)["aboutObject"] == 1, (
        "the link was dropped because the EventRecorded replays before the "
        "ObjectRegistered in a rotated journal — the re-wire must be a trailing "
        "sweep, not inline")


def test_the_EventLog_engine_also_reproduces_the_links(wired) -> None:
    """The ``rebuild()`` (EventLog) engine feeds ``apply()`` one record at a time.

    ``apply()`` wires inline, but its endpoints may not exist yet, so this engine
    must ALSO buffer the record into a trailing sweep — the same forward-reference
    treatment it gives ``EntityLinked``. Without that, a rotated journal drops the
    link here while ``rebuild_all`` keeps it (two engines, two answers).
    """
    sdk, proj, events, journal = wired
    obj = _nid(sdk.create_object("TheObject2"))
    sdk.create_event("obs2", "event", aboutObject=obj)
    before = _counts(proj)

    with open(journal, encoding="utf-8") as fh:
        records = [line for line in fh if line.strip()]
    event_lines = [ln for ln in records if '"EventRecorded"' in ln]
    rest = [ln for ln in records if '"EventRecorded"' not in ln]
    os.remove(journal)
    with open(os.path.join(str(events), "a-events.jsonl"), "w", encoding="utf-8") as fh:
        fh.writelines(event_lines)
    with open(os.path.join(str(events), "b-rest.jsonl"), "w", encoding="utf-8") as fh:
        fh.writelines(rest)

    proj.rebuild(_DirLog(events), confirm_destructive=True)
    after = _counts(proj)
    assert after["aboutObject"] == before["aboutObject"] >= 1, (
        f"the EventLog rebuild lost the link: {before} → {after}")


def test_a_name_link_replays_without_a_false_failure(wired) -> None:
    """A cascade-resolved link must not report a failure it did not have.

    ``_create_about_edges`` is the rel-IGNORING cascade, and an unresolved
    ``aboutDocument`` value is one whose OWN rel resolved to nothing at write time
    — so live wireed a Subject stub under ``aboutSubject``, not ``aboutDocument``.
    Measuring the caller's ``rel`` would report failure for the reproduction live
    just made (and again on every re-run of an already-wired link, because MERGE
    adds nothing). The record now PINS the cascade's answer, so replay measures
    the link it was told to make — and the link's PRESENCE is still the test.
    """
    sdk, proj, events, journal = wired
    sdk.create_event("nm", "event", aboutDocument="https://example.test/named")
    proj.rebuild_all(str(events), confirm_destructive=True)
    record = _event_records(journal, "nm")[0]
    assert [lk["rel"] for lk in record["aboutLinks"]] == ["aboutSubject"], (
        "the record must pin the rel the cascade produced, not the rel the "
        f"caller asked for: {record['aboutLinks']!r}")

    wired_n, failed_n = proj.replay_about_links(record)
    assert failed_n == 0, (
        f"a pinned link reported failure although the value is linked: "
        f"wired={wired_n} failed={failed_n}")
    assert wired_n == 1


def test_every_unusable_link_is_COUNTED_not_skipped(wired) -> None:
    """Negative control: a journaled link the replay cannot wire is a LOSS.

    Before round 2 these arms `continue`d without touching ``failed``, so a
    journal claiming a link the replay did not understand produced no signal at
    all. Each case below must report exactly one failure and zero wired.
    """
    sdk, proj, _events, _journal = wired
    ev = _nid(sdk.create_event("mystery", "event"))
    cases = [
        {"id": ev, "aboutLinks": [{"rel": "aboutObject", "how": "bogus",
                                   "targets": ["x"]}]},
        {"id": ev, "aboutLinks": [{"rel": "aboutObject", "how": "id",
                                   "targets": []}]},
        {"id": ev, "aboutLinks": [{"rel": "aboutObject", "how": "id",
                                   "targets": "a-string-not-a-list"}]},
        {"id": ev, "aboutLinks": [{"rel": "notARel", "how": "id",
                                   "targets": ["x"]}]},
    ]
    for payload in cases:
        assert proj.replay_about_links(payload) == (0, 1), (
            f"{payload['aboutLinks']} was not counted as a loss")

    # A record whose id cannot address an Event still CLAIMED links: count them.
    assert proj.replay_about_links({"aboutLinks": cases[0]["aboutLinks"]}) == (0, 1)


def test_a_document_target_survives_a_rotated_journal(wired) -> None:
    """A ``Source`` registration later in the stream must not cost the link.

    The URL resolves to the `Source` at write time, so this journals the exact
    ``how:"id"`` form and replay wires it by value. Pinned as a regression for the
    rotated-journal case on the DOCUMENT rel specifically (the other rotated
    tests use ``aboutObject``), NOT as a pin on the deferral: measured, ``apply``'s
    inline call plus the trailing sweep produce the same result here, because the
    ``id`` path cannot mint — the stub hazard exists only on the ``name`` path,
    where minting is symmetric with live.
    """
    sdk, proj, events, journal = wired
    url = "https://example.test/docs/late-report"
    sdk.create_source(url, sourceKind="document")
    sdk.create_event("late-doc", "event", aboutDocument=url)
    before = _counts(proj)
    assert before["aboutDocument"] == 1, f"live did not wire the document: {before}"

    with open(journal, encoding="utf-8") as fh:
        records = [line for line in fh if line.strip()]
    event_lines = [ln for ln in records if '"EventRecorded"' in ln]
    rest = [ln for ln in records if '"EventRecorded"' not in ln]
    os.remove(journal)
    with open(os.path.join(str(events), "a-events.jsonl"), "w", encoding="utf-8") as fh:
        fh.writelines(event_lines)
    with open(os.path.join(str(events), "b-rest.jsonl"), "w", encoding="utf-8") as fh:
        fh.writelines(rest)

    proj.rebuild(_DirLog(events), confirm_destructive=True)
    assert _counts(proj) == before, (
        f"the document link was lost across a rotated journal: "
        f"{before} → {_counts(proj)}")


# ── Round 2 (adversarial review at a38d6e775) ────────────────────────────────


def test_a_name_link_replays_the_REL_and_NODE_live_produced(wired) -> None:
    """P1-2: journaled name links must not re-run the cascade at replay.

    ``_create_about_edges`` is the rel-IGNORING Subject→Object→Event→Source→Point
    cascade, so ``aboutObject="Acme"`` with no ``Acme`` node wired
    ``aboutSubject`` to a fresh Subject stub — not ``aboutObject``. Journaling
    that as ``how:"name"`` made replay re-ask a LATER graph: once the caller
    registers the Object, the cascade answers ``aboutObject``. Different rel,
    different node, and silent — ``_count_about_out`` only asks "is the source
    joined to anything", so the wrong-rel landing counted as success.

    MEASURED on the shipped code (a38d6e775):
      LIVE  : [['aboutSubject', ['Subject'], 'Acme']]
      REPLAY: [['aboutObject',  ['Object'],  'Acme']]
    """
    sdk, proj, events, journal = wired

    def edges() -> list:
        return proj.g.query(
            "MATCH (e:Event {name:'m'})-[r]->(t) "
            "RETURN type(r), labels(t), t.name").result_set

    sdk.create_event("m", "event", aboutObject="Acme")   # no Acme node yet
    sdk.create_object("Acme")                            # registered LATER
    live = edges()
    assert live == [["aboutSubject", ["Subject"], "Acme"]], (
        f"the live cascade did not mint the Subject stub: {live}")

    record = _event_records(journal, "m")[0]
    assert [link["rel"] for link in record["aboutLinks"]] == ["aboutSubject"], (
        "the record must pin the rel the LIVE cascade produced, not the rel the "
        f"caller asked for: {record['aboutLinks']!r}")

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert edges() == live, (
        "replay re-ran the name cascade against the END-OF-JOURNAL graph and "
        f"landed on a different rel/node: live={live} replay={edges()}")


def test_a_name_link_to_an_EXISTING_node_replays_onto_that_node(wired) -> None:
    """The other name-path outcome: the cascade RESOLVED an existing node.

    Live matched the Object by name (its id is ``obj-<sha26>``, so the id-keyed
    classification misses and the name path runs). The record must carry that
    endpoint, and replay must land on the same node — not re-run the cascade and
    land on the Subject branch a later registration makes available.
    """
    sdk, proj, events, journal = wired
    obj = _nid(sdk.create_object("Acme"))
    sdk.create_event("n", "event", aboutObject="Acme")
    sdk.create_subject("Acme")   # a same-name Subject registered AFTER the link

    def edges() -> list:
        return proj.g.query(
            "MATCH (e:Event {name:'n'})-[r]->(t) "
            "RETURN type(r), labels(t), t.id").result_set

    live = edges()
    assert live == [["aboutObject", ["Object"], obj]], (
        f"live did not resolve the existing Object by name: {live}")
    record = _event_records(journal, "n")[0]
    assert [link["rel"] for link in record["aboutLinks"]] == ["aboutObject"], (
        f"the record dropped the resolved rel: {record['aboutLinks']!r}")
    assert record["aboutLinks"][0]["targets"] == [obj], (
        "the record must carry the endpoint live resolved: "
        f"{record['aboutLinks']!r}")
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert edges() == live, (
        f"replay did not reproduce the live endpoint: {live} → {edges()}")


def test_recording_a_name_link_leaves_no_phase_two_drift(wired) -> None:
    """A caller ``aboutLinks`` prop must not collide with the journal field.

    ⚠️ **Its own discriminating power is the PAIR, not either half** (measured
    at b26bf2a2c, each half reverted separately): with only the two ``**``-splats
    restored it stays GREEN (the reserved-name strip keeps ``aboutLinks`` out of
    the journaled payload) and with only the strip removed it stays GREEN (one
    bag cannot collide with itself). It goes red only when BOTH are reverted.
    So read it as the end-to-end assertion — the public call returns a node,
    writes exactly ONE ``EventRecorded``, and ``rebuild_all`` keeps the Event —
    never as evidence that either fix is load-bearing. The per-half evidence
    lives in ``test_the_EventRecorded_emit_is_ONE_bag_so_a_shared_key_cannot_
    collide`` (the merge alone) and
    ``test_a_caller_aboutLinks_prop_is_not_read_as_journal_data`` (the strip
    alone), which is why the three exist together.

    P1-1, MEASURED at a38d6e775: ``create_event(..., aboutObject=oid,
    aboutLinks=["caller"])`` raised ``TypeError: _emit_event() got multiple
    values for keyword argument 'aboutLinks'`` — AFTER the live write had
    committed and BEFORE anything was journaled, so the Event was live-only and
    the next ``rebuild_all`` silently deleted it. On ``origin/main`` that call
    works, i.e. it was a regression.

    Three things must hold at once: the call returns a node, exactly ONE
    ``EventRecorded`` line is written, and the record carries the links the SDK
    RESOLVED (never the caller's prop, which is not journal data).
    """
    sdk, proj, events, journal = wired
    oid = _nid(sdk.create_object("Collide"))

    node = sdk.create_event("collide", "event", aboutObject=oid,
                            aboutLinks=["caller"])
    assert _nid(node), f"create_event returned no node: {node!r}"

    records = _event_records(journal, "collide")
    assert len(records) == 1, (
        "the Event was written live but journaled "
        f"{len(records)} times — a caller `aboutLinks` prop broke the record: "
        f"{[r.get('type') for r in _records(journal)]}")
    assert records[0]["aboutLinks"] == [
        {"rel": "aboutObject", "how": "id", "targets": [oid]}], (
        "the record must carry the links the SDK resolved, not the caller's "
        f"prop: {records[0]['aboutLinks']!r}")

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert proj.g.query(
        "MATCH (e:Event {name:'collide'}) RETURN e.id").result_set == [[_nid(node)]], (
        "the Event was live-only: `rebuild_all` destroyed it")


def test_a_caller_aboutLinks_prop_is_not_read_as_journal_data(wired, caplog) -> None:
    """P1-1 (second half): ``aboutLinks`` is a ``_emit_event`` call-collision name.

    It rides a ``**props`` splat, so it is unreachable via
    ``inspect.signature`` — the derived-coverage test cannot see it. Without the
    reserved-set strip the caller's prop reaches the live fold, where ``apply``'s
    inline re-wire reads it as journal data and reports a phantom failure
    (measured: "#8398: apply() could not re-wire 1 journaled Event→entity
    about* link(s)").

    **This test discriminates the STRIP alone** (measured at b26bf2a2c): with
    ``aboutLinks`` removed from ``_RESERVED_EMIT_CALL_PROPS`` and the single-bag
    emit intact it is the ONLY test that goes red, so it is the evidence that
    the strip — not the merge — closes this half. The merge's own evidence is
    ``test_the_EventRecorded_emit_is_ONE_bag_so_a_shared_key_cannot_collide``.
    """
    import logging

    sdk, _proj, _events, _journal = wired
    oid = _nid(sdk.create_object("Collide"))
    with caplog.at_level(logging.WARNING):
        sdk.create_event("collide", "event", aboutObject=oid,
                         aboutLinks=["caller"])
    assert "#8398: apply() could not re-wire" not in caplog.text, (
        "the caller's `aboutLinks` prop was interpreted as journal data by the "
        f"live fold: {caplog.text[-2000:]}")


def test_a_flat_list_aboutLinks_record_never_becomes_a_node_property(wired) -> None:
    """P2-2: the drop must be DECLARED, not incidental.

    ``aboutLinks`` is record DATA. A record whose field is a list of dicts happens
    to be filtered by ``_is_persistable_prop_value``, but a FLAT list is a
    persistable value — measured on the shipped code, replaying one yields
    ``node aboutLinks = [['aboutObject:obj-1']]``, a property live never had.
    """
    sdk, proj, _events, _journal = wired
    ev = _nid(sdk.create_event("flat", "event"))
    with _replay_write_scope():
        proj.apply({"type": "EventRecorded", "id": ev, "eventId": ev,
                    "name": "flat", "eventKind": "event",
                    "aboutLinks": ["aboutObject:obj-1"]})
    row = proj.g.query(
        "MATCH (e:Event {id:$id}) RETURN e.aboutLinks",
        params={"id": ev}).result_set
    assert row == [[None]], (
        "a flat-list `aboutLinks` record folded onto the Event as a node "
        f"property live never had: {row}")


def test_apply_does_not_abort_when_the_about_re_wire_raises(wired, monkeypatch) -> None:
    """P2-5: ``apply``'s inline re-wire must be guarded like both sweeps.

    ``backup.restore`` and ``consistency.py`` drive ``apply`` directly and have no
    trailing sweep, so an engine error or a malformed link escaping this call
    aborts the RESTORE — contradicting the patch's own "Reported, never raised"
    policy. The record must still fold (the fold precedes the re-wire).
    """
    sdk, proj, _events, _journal = wired
    ev = _nid(sdk.create_event("boom", "event"))

    def _raise(_payload, *_rest, **_kw):
        raise RuntimeError("engine error mid re-wire")

    monkeypatch.setattr(proj, "replay_about_links", _raise)
    with _replay_write_scope():
        proj.apply({"type": "EventRecorded", "id": ev, "eventId": ev,
                    "name": "boom2", "eventKind": "event"})
    assert proj.g.query(
        "MATCH (e:Event {id:$id}) RETURN e.name",
        params={"id": ev}).result_set == [["boom2"]], (
        "the record did not fold before the failing re-wire")


def test_a_LEGACY_name_link_record_still_replays(wired) -> None:
    """The ``how:"name"`` branch is DEFENSIVE — no journal in the wild has one.

    ``aboutLinks`` is introduced by this unmerged change: ``git grep aboutLinks
    origin/main -- tortoise/`` is empty, so no released version writes the field
    and no deployment anywhere holds a ``how:"name"`` link. The only records
    that could carry one were written by an EARLIER COMMIT OF THIS BRANCH (it
    journaled ``{"rel", "value", "how": "name"}`` before the P1-2 fix pinned
    the resolved endpoint). The branch therefore defends a form only this branch
    ever produced — it is forward-compatible handling, not a migration path.

    It is kept rather than deleted because dropping it would turn such a record
    into a counted loss instead of the pre-fix behaviour it had, and this pins
    that behaviour AND documents the drift it keeps: the edge lands on whatever
    the rel-ignoring cascade finds, not on the record's ``rel``.
    """
    sdk, proj, _events, _journal = wired
    ev = _nid(sdk.create_event("legacy", "event"))
    payload = {"id": ev,
               "aboutLinks": [{"rel": "aboutObject", "value": "Legacy",
                               "how": "name"}]}
    assert proj.replay_about_links(payload) == (1, 0)
    assert proj.g.query(
        "MATCH (e:Event {id:$id})-[r]->(t) RETURN type(r), labels(t), t.name",
        params={"id": ev}).result_set == [["aboutSubject", ["Subject"], "Legacy"]], (
        "the legacy name link did not replay through the cascade")


# ── Round 3 (second independent review at 2b9626072) ─────────────────────────


def test_a_name_link_to_an_EXISTING_Subject_never_mints_at_replay(wired) -> None:
    """P2-1: ``mint_if_absent`` is a claim about the CONSTRUCTION, not the label.

    The stub construction is ``MERGE (s:Subject {name:$name}) ON CREATE SET
    s.id=$name``, so a stub's id EQUALS its name. Keying the journal claim on
    ``label == "Subject"`` alone claimed a mint for a Subject that live merely
    MATCHED — measured at 2b9626072: a link to the existing real Subject
    ``sub-6772…`` journaled ``mint_if_absent: true``, and a replay where that
    Subject was absent minted a duplicate same-name stub (``id == name``) and
    reported ``wired=1, failed=0``. That is ``derived == replay(journal)`` broken
    in the FABRICATION direction — the invariant this PR exists to establish.

    ``test_a_name_link_to_an_EXISTING_node_replays_onto_that_node`` is the
    sibling for ``Object``, where ``mint_if_absent`` was already ``False``: a
    mint on a non-stubbable label is a no-op, so that test cannot see this.
    """
    sdk, proj, _events, journal = wired
    subj = _nid(sdk.create_subject("Acme"))
    sdk.create_event("s", "event", aboutObject="Acme")

    record = _event_records(journal, "s")[0]
    link = record["aboutLinks"][0]
    assert (link["rel"], link["target_label"]) == ("aboutSubject", "Subject"), (
        f"the cascade did not resolve the existing Subject: {link!r}")
    assert link["targets"] == [subj], (
        f"the record must carry the endpoint live resolved: {link!r}")
    assert link["mint_if_absent"] is False, (
        "the record claims a mint for a Subject that live merely MATCHED — a "
        f"replay would fabricate a node live never had: {link!r}")

    # Removed, not deleted through the journal: the Subject is ABSENT at replay
    # (a lost/rotated ``SubjectAdded`` line, or a delete the journal cannot
    # rebuild it from). Live created nothing, so replay must create nothing.
    proj.g.query("MATCH (s:Subject {id:$id}) DETACH DELETE s",
                 params={"id": subj})
    wired_n, failed_n = proj.replay_about_links(record)
    assert (wired_n, failed_n) == (0, 1), (
        "a link whose endpoint live RESOLVED must report an honest failure when "
        f"that endpoint is gone — never a mintage: wired={wired_n} "
        f"failed={failed_n}")
    rows = proj.g.query(
        "MATCH (s:Subject {name:'Acme'}) RETURN s.id, s.name").result_set
    assert rows == [], (
        f"the replay fabricated a Subject stub (id == name) live never had: {rows}")


def test_a_hard_deleted_Event_is_not_a_LOST_about_link(wired, caplog) -> None:
    """P2-2: a link the journal's own hard delete removed is not a replay loss.

    Neither sweep filtered the records by the replay's hard-delete set, while the
    sibling immediately above (``fold_deferred_entity_links(entity_link_events,
    hard_delete_seqs)``) does. Measured at 2b9626072: create an Event with
    ``aboutObject`` → ``delete(event_id)`` → ``rebuild_all`` warned *"1 journaled
    Event→entity about* link(s) could NOT be re-wired — the live graph had these
    edges"*, neither half of which is true (live had no such edge either). It
    also inflated the ``failed`` count this PR advertises as the honest signal,
    which trains readers to ignore the warning.
    """
    import logging

    sdk, proj, events, journal = wired
    obj = _nid(sdk.create_object("Gone"))
    ev = _nid(sdk.create_event("gone", "event", aboutObject=obj))
    assert _counts(proj)["aboutObject"] == 1, "live did not wire the link"
    assert sdk.delete(ev) is True, "the Event was not deleted"
    assert _counts(proj)["aboutObject"] == 0, (
        "live kept the edge of a hard-deleted Event")

    with caplog.at_level(logging.WARNING):
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert _counts(proj)["aboutObject"] == 0, (
        "rebuild_all resurrected the link of a hard-deleted Event")
    assert "could NOT be re-wired" not in caplog.text, (
        "the sweep reported a loss for a link the journal legitimately deleted "
        f"— live never had it: {caplog.text[-2000:]}")

    # The same in the single-file engine: both sweeps must apply the rule.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        proj.rebuild(_DirLog(journal.parent), confirm_destructive=True)
    assert _counts(proj)["aboutObject"] == 0, (
        "rebuild resurrected the link of a hard-deleted Event")
    assert "could NOT be re-wired" not in caplog.text, (
        f"the EventLog sweep reported a phantom loss: {caplog.text[-2000:]}")


def test_a_caller_event_prop_cannot_hide_the_HARD_DELETE_GATE(
        wired, caplog) -> None:
    """P2 (6th review at 947f20f36): the GATE must resolve the record exactly
    as the READER does.

    ``replay_about_links`` was taught to address-check ``id``/``eventId`` before
    treating ``event`` as the nested payload; the gate that decides whether to
    hand it the record (``_about_link_event_hard_deleted``) kept unwrapping
    ``event`` unconditionally. For a record whose ``event`` key is a caller PROP
    the gate read ``rid = None``, ``_hard_delete_suppresses`` answered False, the
    record was NOT dropped, and the sweep then reported the very falsehood the
    gate exists to remove ("could NOT be re-wired … the live graph had these
    edges" — measured shape, hand-built because the SDK now strips the prop at
    the Event door).

    Latent in today's call graph — every in-tree ``EventRecorded`` door strips a
    caller ``event`` prop — but a journal written by any other build can carry
    it, and the reader already refuses to assume the intake strip ran. Both
    therefore call ONE resolver (``_about_link_payload``), so they cannot drift
    apart again.

    Mutation: restore the unconditional unwrap in the gate → the gate answers
    False for this record and the warning reappears.
    """
    import logging

    from tortoise.projection import _about_link_event_hard_deleted, journal_hard_delete_seqs

    sdk, proj, events, journal = wired
    obj = _nid(sdk.create_object("Gone"))
    ev = _nid(sdk.create_event("shadowed", "event", aboutObject=obj))
    assert _counts(proj)["aboutObject"] == 1, "live did not wire the link"
    assert sdk.delete(ev) is True, "the Event was not deleted"
    assert _counts(proj)["aboutObject"] == 0, (
        "live kept the edge of a hard-deleted Event")

    # Rewrite the journal into the one shape the intake strip prevents the SDK
    # from producing — a caller ``event`` prop on an EventRecorded record whose
    # address stays at the RECORD's top level:
    #   {"type": "EventRecorded", "id": <ev>, "event": {"payload": "x"}, …}
    lines = _records(journal)
    shadowed = shadowed_seq = None
    for idx, rec in enumerate(lines):
        if rec.get("type") == "EventRecorded" and rec.get("name") == "shadowed":
            assert ev in (rec.get("id"), rec.get("eventId")), (
                f"the record does not address its own Event: {rec!r}")
            rec["event"] = {"payload": "x"}
            shadowed, shadowed_seq = rec, idx
    assert shadowed is not None, "the EventRecorded record was not journaled"
    with open(journal, "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec) + "\n")

    # The GATE, on the record the reader would be handed: the deleted Event's
    # address is unreachable through an unconditional unwrap, so this is the
    # assertion the mutation flips.
    assert _about_link_event_hard_deleted(
        journal_hard_delete_seqs(lines), shadowed_seq, shadowed) is True, (
        "the gate did not recognise the hard-deleted Event behind a caller "
        "`event` prop, so the record was handed to the sweep as a live link")

    with caplog.at_level(logging.WARNING):
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert _counts(proj)["aboutObject"] == 0, (
        "rebuild_all resurrected the link of a hard-deleted Event")
    assert "could NOT be re-wired" not in caplog.text, (
        "the sweep reported a loss for a link the journal legitimately deleted "
        f"— live never had it: {caplog.text[-2000:]}")

    # The same in the single-file engine: both sweeps must apply the rule.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        proj.rebuild(_DirLog(journal.parent), confirm_destructive=True)
    assert _counts(proj)["aboutObject"] == 0, (
        "rebuild resurrected the link of a hard-deleted Event")
    assert "could NOT be re-wired" not in caplog.text, (
        f"the EventLog sweep reported a phantom loss: {caplog.text[-2000:]}")


def test_two_same_name_carriers_are_BOTH_journaled_and_BOTH_wired(wired) -> None:
    """P2-3: never pin a ``LIMIT 1`` row-order pick into the journal.

    A name is a mutable natural key (D2: two live carriers are legal). The probe
    was ``… RETURN ID(e), … LIMIT 1``, so live wired ONE arbitrary carrier, that
    pick was written into an append-only journal, and live and replay then ran
    OPPOSITE policies — live guessed, replay refused via
    ``_refuse_ambiguous_target``. The retired ``_try_about_edge`` probed
    ``LIMIT 1`` but then ``MERGE``d the edge against EVERY same-name carrier:
    this pins that behaviour as the CHOSEN one (nothing is picked, so nothing
    arbitrary is pinned, and live writes exactly what the record says).

    THREE same-name ``:Event`` carriers, because ``_upsert_event`` MERGEs on
    ``eventId`` (not ``name``) — so all are reachable through a public door AND
    all are journaled, which keeps the full ``rebuild_all`` comparison honest
    instead of measuring an unjournaled producer (which no replay can restore).
    Three, not two, deliberately: a bounded probe (``LIMIT 2``) tells "one" from
    "many" but still TRUNCATES at the third carrier, so a two-carrier fixture
    cannot see that defect at all.
    """
    sdk, proj, events, journal = wired
    carriers = {_nid(sdk.create_event("Shared", "event")) for _ in range(3)}
    assert len(carriers) == 3, f"the carriers collapsed: {carriers}"

    sdk.create_event("dup", "event", aboutObject="Shared")

    def edges() -> set:
        return {
            row[0] for row in proj.g.query(
                "MATCH (e:Event {name:'dup'})-[:aboutEvent]->(t) "
                "RETURN t.id").result_set
        }

    assert edges() == carriers, (
        "live did not wire EVERY same-name carrier (the retired behaviour, and "
        f"the only one that pins nothing arbitrary): {edges()}")

    record = _event_records(journal, "dup")[0]
    link = record["aboutLinks"][0]
    assert link["rel"] == "aboutEvent", f"wrong rel: {link!r}"
    assert sorted(link["targets"]) == sorted(carriers), (
        "the record pinned a subset of the carriers live addressed: "
        f"{link!r}")
    assert link["mint_if_absent"] is False, (
        f"an :Event was never mintable live: {link!r}")

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert edges() == carriers, (
        f"replay did not reproduce every journaled endpoint: {edges()}")

    # And the SINGLE-carrier case still journals one endpoint, not two: the
    # probe must not turn every name into a list.
    sdk.create_event("only", "event")
    sdk.create_event("solo", "event", aboutObject="only")
    solo = _event_records(journal, "solo")[0]["aboutLinks"][0]
    assert len(solo["targets"]) == 1, (
        f"a lone carrier must journal exactly one endpoint: {solo!r}")


def test_a_non_list_targets_on_a_pinned_link_is_a_COUNTED_failure(
        wired) -> None:
    """P2-8: ``targets: "abcd"`` iterates CHARACTERS, it does not fail.

    ``for value in (link.get("targets") or [])`` walks a string character by
    character, so a malformed record can WIRE to a node whose id is a single
    character and then "repair" itself through the key fallback — while the body
    claims "a ``targets`` that is not a list … is a failure". Pinned with a
    one-character-id carrier present, so the bad arm is reachable rather than
    vacuously empty.
    """
    sdk, proj, _events, _journal = wired
    sdk.create_subject("Onechar")
    proj.g.query("MATCH (s:Subject {name:'Onechar'}) SET s.id='a'")
    ev = _nid(sdk.create_event("malf", "event"))
    for bad in ("abcd", {"a": 1}, 7):
        payload = {"id": ev, "aboutLinks": [{
            "rel": "aboutSubject", "how": "id", "targets": bad,
            "target_label": "Subject", "resolve_key": "Onechar",
            "mint_if_absent": False}]}
        assert proj.replay_about_links(payload) == (0, 1), (
            f"targets={bad!r} was not counted as exactly one loss")
    assert proj.g.query(
        "MATCH (e:Event {id:$id})-[r]->() RETURN count(r)",
        params={"id": ev}).result_set == [[0]], (
        "the malformed record wired an edge from a single-character target")


def test_the_EventRecorded_emit_is_ONE_bag_so_a_shared_key_cannot_collide(
        wired, monkeypatch) -> None:
    """P2-5: the MERGE (not the reserved-name strip) needs a test of its own.

    Round 2 proved two named tests guard the ``aboutLinks`` reserved name, but
    restoring the two ``**``-splats with that set intact leaves BOTH green: the
    strip keeps ``aboutLinks`` out of the journaled payload, so the collision is
    unreachable through a public door. Without this test the merge is
    defence-in-depth with no evidence it is a merge.

    But this test is the MERGE's own discriminator, which is why it exists: with
    the single-bag emit restored and the strip alone removed, this test stays
    green and only ``test_a_caller_aboutLinks_prop_is_not_read_as_journal_data``
    goes red (measured at b26bf2a2c); with the two splats restored and the strip
    intact, this is the ONLY test that goes red.

    So the collision is constructed directly: the journaled payload is made to
    carry a key the write-time edge facts also carry — the exact overlap a
    second ``**`` bag would turn into ``TypeError: got multiple values for
    keyword argument 'aboutLinks'`` AFTER the live write and BEFORE the record,
    leaving the Event live-only. Pinned: ONE call, ONE record, and the edge facts
    win.
    """
    from tortoise import sdk as sdk_mod

    sdk, _proj, _events, journal = wired
    oid = _nid(sdk.create_object("Collide2"))
    real = sdk_mod._journal_record_payload

    def _colliding(event):
        out = real(event)
        out.setdefault("aboutLinks", ["forged-by-the-journaled-payload"])
        return out

    monkeypatch.setattr(sdk_mod, "_journal_record_payload", _colliding)
    node = sdk.create_event("two-bags", "event", aboutObject=oid)
    monkeypatch.undo()

    assert _nid(node), f"create_event returned no node: {node!r}"
    records = _event_records(journal, "two-bags")
    assert len(records) == 1, (
        "the emit raised on a key present in BOTH bags — the two `**`-splats "
        f"are back: {[r.get('type') for r in _records(journal)]}")
    assert records[0]["aboutLinks"] == [
        {"rel": "aboutObject", "how": "id", "targets": [oid]}], (
        "the write-time edge facts must stay authoritative over the journaled "
        f"payload: {records[0]['aboutLinks']!r}")


# ── Round 4 (third independent review at b26bf2a2c) ──────────────────────────


def test_a_title_held_by_THREE_document_Sources_wires_them_ALL(wired) -> None:
    """P2-1: the ``Source`` arm kept the ``LIMIT 1`` the OTHER arm documents away.

    ``_resolve_about_edge_target``'s docstring promises "every one of them is
    journaled in ``targets`` … instead of pinning an arbitrary row", and the
    non-``Source`` arm spends 20 lines explaining why a bounded probe is wrong.
    The ``Source`` arm 30 lines above it still ended ``RETURN ID(e), e.url LIMIT
    1`` — while the retired ``_try_about_edge`` limited only its EXISTENCE probe
    and then MERGE'd the edge against EVERY same-title document Source. So it
    was both an undisclosed behaviour regression against ``origin/main`` and the
    identical defect this PR removed one arm over.

    THREE carriers, because a bounded probe (``LIMIT 2``) tells "one" from "many"
    and still truncates at the third — a two-carrier fixture cannot see that.
    """
    sdk, proj, events, journal = wired
    urls = set()
    for i in range(3):
        row = sdk.create_source(f"https://example.test/same/{i}", "document",
                                title="Same Title", documentKind="report")
        urls.add(row.get("url") or _nid(row))
    assert len(urls) == 3, f"the carriers collapsed: {urls}"

    sdk.create_event("titled", "event", aboutDocument="Same Title")

    def edges() -> set:
        return {
            row[0] for row in proj.g.query(
                "MATCH (e:Event {name:'titled'})-[:aboutDocument]->(t) "
                "RETURN t.url").result_set
        }

    assert edges() == urls, (
        "live did not wire EVERY same-title document Source — the retired "
        f"behaviour, and the one whose journal replay can reproduce: {edges()}")

    link = _event_records(journal, "titled")[0]["aboutLinks"][0]
    assert link["rel"] == "aboutDocument", f"wrong rel: {link!r}"
    assert sorted(link["targets"]) == sorted(urls), (
        "the record pinned a subset of the carriers live addressed — an "
        f"append-only journal cannot repair that: {link!r}")
    assert link["mint_if_absent"] is False, (
        f"an aboutDocument :Source is never mintable live: {link!r}")

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert edges() == urls, (
        f"replay did not reproduce every journaled document endpoint: {edges()}")


def test_a_PARTIAL_multi_carrier_loss_is_COUNTED_per_endpoint(wired) -> None:
    """P2-2: a pinned link's success is per ENDPOINT, not per link.

    The pinned arm collapsed every ``targets`` value into ONE boolean (``ok``),
    so a link that lost SOME of its journaled endpoints reported full success —
    while the ``how:"id"`` arm immediately below counts per value, so the two
    arms disagreed about the same fact. Measured at b26bf2a2c: three same-name
    ``:Event`` carriers in one link with one carrier gone, and
    ``replay_about_links`` returned ``(1, 0)`` while only two of the three edges
    existed. A lost journaled endpoint with ``failed == 0`` and no warning is
    exactly what the docstring forbids ("Every skip that costs a journaled link
    counts as ``failed``"; "a resolve-or-mint that joined nothing [is] a
    failure, not a silent skip").
    """
    sdk, proj, _events, journal = wired
    carriers = {_nid(sdk.create_event("Shared", "event")) for _ in range(3)}
    assert len(carriers) == 3, f"the carriers collapsed: {carriers}"
    sdk.create_event("dup", "event", aboutObject="Shared")

    def edges() -> set:
        return {
            row[0] for row in proj.g.query(
                "MATCH (e:Event {name:'dup'})-[:aboutEvent]->(t) "
                "RETURN t.id").result_set
        }

    assert edges() == carriers, f"live did not wire every carrier: {edges()}"

    record = _event_records(journal, "dup")[0]
    link = record["aboutLinks"][0]
    assert sorted(link["targets"]) == sorted(carriers), (
        f"the fixture needs a multi-carrier pinned link: {link!r}")

    gone = sorted(carriers)[0]
    proj.g.query("MATCH (e:Event {id:$id}) DETACH DELETE e",
                 params={"id": gone})
    assert edges() == carriers - {gone}, (
        "deleting the carrier did not take its edge with it — the fixture "
        f"assumes it does: {edges()}")

    wired_n, failed_n = proj.replay_about_links(record)
    assert (wired_n, failed_n) == (2, 1), (
        "a PARTIAL multi-carrier loss was reported as full success: "
        f"wired={wired_n} failed={failed_n} for {len(link['targets'])} "
        "journaled endpoints with one of them gone")
    assert edges() == carriers - {gone}, (
        f"the replay did not re-wire the surviving endpoints: {edges()}")


def test_a_hard_deleted_LINK_TARGET_is_not_RESURRECTED(wired, caplog) -> None:
    """A link whose TARGET the journal hard-deletes later is stale, not lost.

    The sweep applied the #8398 staleness rule to the record's EVENT only, so a
    hard-deleted target was still re-wired — and, because the Subject-stub
    fallback journals ``mint_if_absent``, RE-MINTED. Measured at 208f96eb8:
    ``create_event(aboutObject="Acme")`` with no ``Acme`` node mints
    ``(:Subject {name:"Acme", id:"Acme"})`` and wires ``aboutSubject``;
    ``delete("Acme")`` (the SDK door, so the journal carries
    ``EntityMutated op=delete label=Subject``) empties the live graph again; then
    ``rebuild_all`` reported ``wired=1, failed=0`` — SUCCESS — while FABRICATING
    the Subject and its edge back. ``derived != replay(journal)`` in the
    fabrication direction, and the counters this PR advertises as its honest
    signal said nothing.

    Asserted on NODES and EDGES, not on a bare count: a count can agree with a
    fabricated graph.
    """
    import logging

    sdk, proj, events, journal = wired

    def subjects() -> set:
        return {row[0] for row in proj.g.query(
            "MATCH (s:Subject) RETURN s.id").result_set}

    def edges() -> set:
        return {(row[0], row[1]) for row in proj.g.query(
            "MATCH (e:Event)-[:aboutSubject]->(t:Subject) "
            "RETURN e.id, t.id").result_set}

    ev = _nid(sdk.create_event("e", "event", aboutObject="Acme"))
    link = _event_records(journal, "e")[0]["aboutLinks"][0]
    assert link.get("resolve_key") == "Acme" and link.get("mint_if_absent") is True, (
        "the fixture needs the Subject-stub path (the one that MINTS on replay): "
        f"{link!r}")
    assert subjects() == {"Acme"}, f"live did not mint the stub: {subjects()}"
    assert edges() == {(ev, "Acme")}, f"live did not wire the edge: {edges()}"

    # The TARGET, through the SDK door — the only door that journals the delete.
    assert sdk.delete("Acme") is True, "the Subject was not deleted"
    assert subjects() == set(), (
        "live kept the hard-deleted Subject — the fixture is not measuring a "
        f"delete: {subjects()}")
    assert edges() == set(), f"live kept the deleted Subject's edge: {edges()}"

    with caplog.at_level(logging.WARNING):
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert subjects() == set(), (
        "rebuild_all RESURRECTED the hard-deleted link target — the stub was "
        f"re-minted from an append-only record: {subjects()}")
    assert edges() == set(), (
        f"rebuild_all re-wired the stale aboutSubject edge: {edges()}")
    assert "could NOT be re-wired" not in caplog.text, (
        "the sweep reported a loss for a link the journal legitimately deleted "
        f"— live never had it: {caplog.text[-2000:]}")

    # Both engines: ``rebuild`` sweeps through its own call site.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        proj.rebuild(_DirLog(journal.parent), confirm_destructive=True)
    assert subjects() == set(), (
        f"rebuild resurrected the hard-deleted link target: {subjects()}")
    assert edges() == set(), f"rebuild re-wired the stale edge: {edges()}"
    assert "could NOT be re-wired" not in caplog.text, (
        f"the EventLog sweep reported a phantom loss: {caplog.text[-2000:]}")


def test_a_hard_deleted_ID_TARGET_is_not_a_FALSE_failure(wired, caplog) -> None:
    """... and the same rule on the ``how: "id"`` form, where it was a lie.

    A registered Object target journals ``{"rel": "aboutObject", "how": "id",
    "targets": [...]}`` — no ``resolve_key``. Hard-deleting it left the sweep
    trying to re-wire an endpoint live no longer has, so ``rebuild_all``
    reported ``1 journaled Event→entity about* link(s) could NOT be re-wired —
    the target node was not restored by this replay; the live graph had these
    edges``. BOTH halves are false: the target WAS deleted on purpose (not
    merely "not restored"), and ``DETACH DELETE`` removed those edges live, so
    the live graph never had them. That inflates ``failed`` — the honest signal
    this PR advertises — and trains readers to ignore the warning.
    """
    import logging

    sdk, proj, events, journal = wired

    def objects() -> set:
        return {row[0] for row in proj.g.query(
            "MATCH (o:Object) RETURN o.id").result_set}

    def edges() -> set:
        return {(row[0], row[1]) for row in proj.g.query(
            "MATCH (e:Event)-[:aboutObject]->(t:Object) "
            "RETURN e.id, t.id").result_set}

    obj = _nid(sdk.create_object("Gone"))
    ev = _nid(sdk.create_event("obs", "observation", aboutObject=obj))
    link = _event_records(journal, "obs")[0]["aboutLinks"][0]
    assert link.get("how") == "id" and not link.get("resolve_key"), (
        f"the fixture needs the id-resolved arm: {link!r}")
    assert objects() == {obj}, f"live did not create the Object: {objects()}"
    assert edges() == {(ev, obj)}, f"live did not wire the edge: {edges()}"

    assert sdk.delete(obj) is True, "the Object was not deleted"
    assert objects() == set() and edges() == set(), (
        f"live kept the hard-deleted Object / its edge: {objects()} {edges()}")

    with caplog.at_level(logging.WARNING):
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert objects() == set(), (
        f"rebuild_all resurrected the hard-deleted Object: {objects()}")
    assert edges() == set(), (
        f"rebuild_all re-wired an edge to a hard-deleted Object: {edges()}")
    assert "could NOT be re-wired" not in caplog.text, (
        "a deliberately hard-deleted target was reported as a replay loss: "
        f"{caplog.text[-2000:]}")


# ── Round 4 (adversarial review at 208f96eb8) ────────────────────────────────
def test_a_hard_deleted_SOURCE_target_is_not_a_LOST_about_link(
        wired, caplog) -> None:
    """P2-1, the ``Source``/``aboutDocument`` arm — and the ids-vs-urls caveat.

    The staleness filter compares a journaled endpoint VALUE against a
    ``hard_delete_seqs`` key, and for a document link those are not the same
    KIND of thing in general: the endpoint is a Source's ``url`` while the map
    is keyed by the id its ``EntityMutated`` delete record carried. They are
    EQUAL in-repo — ``_upsert_source`` MERGEs on ``url``, ``create_document``
    keys a document ``:Source`` on ``url = id``, and ``_delete_entity``
    journals the value it was CALLED with — and this test is what pins that:
    delete the document Source by its url and the sweep must stay silent.
    If that identity ever moves, this goes red rather than the filter silently
    mis-suppressing (a mismatch suppresses nothing, the safe direction).
    """
    import logging

    sdk, proj, events, _journal = wired
    url = "https://example.test/docs/gone"
    sdk.create_source(url, sourceKind="document")
    sdk.create_event("d", "event", aboutDocument=url)
    assert _counts(proj)["aboutDocument"] == 1, (
        "live did not wire the document link")
    assert sdk.delete(url) is True, "the Source was not deleted by its url"
    assert _counts(proj)["aboutDocument"] == 0, (
        "live kept the edge of a hard-deleted Source")

    with caplog.at_level(logging.WARNING):
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert _counts(proj)["aboutDocument"] == 0, (
        "rebuild_all resurrected the document link of a hard-deleted Source")
    assert "could NOT be re-wired" not in caplog.text, (
        "the sweep reported a loss for a document link whose Source the journal "
        "legitimately deleted (is the delete record still keyed by the url?): "
        f"{caplog.text[-2000:]}")


def test_an_EMPTY_pinned_targets_never_mints_and_is_a_COUNTED_loss(
        wired) -> None:
    """P2-2: a pinned link with ``targets == []`` must not be "repaired".

    With ``targets == []`` both ``missing`` and ``wired - wired_before`` were 0,
    so control fell through to the key fallback; if ``resolve_key`` resolved (or
    ``mint_if_absent`` MINTED) the branch did ``wired += 1; failed += max(0-1, 0)``
    → ``failed == 0``. Measured: ``(1, 0)`` and a brand-new ``:Subject`` named
    ``Ghost-True`` plus an edge — a record journaling NO endpoint fabricating a
    node and reporting ``derived == replay(journal)`` clean. The sibling
    ``how:"id"`` arm counts the same record as one loss, so the two arms
    disagreed about the SAME record, and the fallback's failure was the only
    reason the ``else`` arm ever agreed.
    """
    sdk, proj, _events, _journal = wired
    ev = _nid(sdk.create_event("malf", "event"))

    def _edges() -> int:
        return proj.g.query(
            "MATCH (e:Event {id:$id})-[r]->() RETURN count(r)",
            params={"id": ev}).result_set[0][0]

    for mint in (True, False):
        wired_n, failed_n = proj.replay_about_links({
            "id": ev, "aboutLinks": [{
                "rel": "aboutSubject", "how": "id", "targets": [],
                "target_label": "Subject", "resolve_key": "Ghost-True",
                "mint_if_absent": mint}]})
        assert (wired_n, failed_n) == (0, 1), (
            f"empty targets (mint_if_absent={mint}) was not counted as exactly "
            f"one loss: wired={wired_n} failed={failed_n}")
        assert proj.g.query(
            "MATCH (s:Subject {name:'Ghost-True'}) RETURN count(s)"
        ).result_set[0][0] == 0, (
            "the replay MINTED a Subject for a record that journaled no "
            "endpoint at all")
        assert _edges() == 0, (
            "the replay wired an edge for a record that journaled no endpoint")


def test_wire_about_link_reports_FALSE_when_the_target_is_gone(wired) -> None:
    """P2-3: the return value must be the WRITE's effect, not the loop's.

    ``wrote = True`` was set unconditionally after the MERGE, so a target the
    graph no longer holds — the ``MATCH`` half of the ``MERGE`` finds no row and
    writes nothing — reported success. Measured:
    ``wire_about_link(ev, {"rel": "aboutObject"}, 999999)`` → ``True`` while
    ``count(r) == 0``. Latent in today's call graph (``replay_about_links``
    resolves its ids first), but it is the boolean that feeds ``wired += 1``
    there, and its docstring asserts "Every resolved id is written".
    """
    sdk, proj, _events, _journal = wired
    ev = _nid(sdk.create_event("noop", "event"))

    def _edges() -> int:
        return proj.g.query(
            "MATCH ()-[r:aboutObject]->() RETURN count(r)").result_set[0][0]

    assert proj.wire_about_link(
        ev, {"rel": "aboutObject"}, 999999) is False, (
        "wire_about_link claimed it wrote an edge to a target that does not "
        "exist")
    assert _edges() == 0, f"an edge appeared from nowhere: {_edges()}"

    # Control: the SAME call with a live target does report True, so the fix is
    # "measure the write", not "return False".
    obj = _nid(sdk.create_object("Present"))
    internal = proj.g.query(
        "MATCH (o:Object {id:$id}) RETURN ID(o)",
        params={"id": obj}).result_set[0][0]
    assert proj.wire_about_link(ev, {"rel": "aboutObject"}, internal) is True, (
        "wire_about_link reported failure for an edge it DID create")
    assert _edges() == 1, f"the control edge was not written: {_edges()}"


def test_an_ALL_STALE_pinned_link_is_not_repaired_from_its_key(wired) -> None:
    """P2-2, the second face: a link with NO live endpoint must not be
    "repaired" by its key either.

    Once every journaled endpoint of a pinned link is hard-deleted, the link is
    stale as a LINK — live has no such edge — but the key fallback only asked
    whether the KEY was stale, so a live key still in the graph (a same-name
    node the record never named) put the fallback back in business: with
    ``mint_if_absent`` it would MINT. Measured before this boundary:
    ``replay_about_links`` returned ``(1, 0)`` and the edge landed on the live
    ``Acme`` node, i.e. on a node the journaled endpoint was not.

    Suppressing it is not a loss: nothing was wired, so nothing is reported.
    """
    sdk, proj, _events, _journal = wired
    sdk.create_subject("Acme")           # the KEY's carrier — never journaled
    ev = _nid(sdk.create_event("malf", "event"))
    assert proj.g.query(
        "MATCH (s:Subject {name:'Acme'}) RETURN count(s)"
    ).result_set[0][0] == 1, "the key carrier is missing"

    payload = {"id": ev, "aboutLinks": [{
        "rel": "aboutSubject", "how": "id", "targets": ["gone-id"],
        "target_label": "Subject", "resolve_key": "Acme",
        "mint_if_absent": True}]}
    # The record sits at seq 1; the journal hard-deletes the endpoint at seq 5.
    wired_n, failed_n = proj.replay_about_links(
        payload, {"gone-id": {"Subject": 5}}, 1)
    assert (wired_n, failed_n) == (0, 0), (
        "an ALL-STALE pinned link was repaired from its key instead of being "
        f"recognised as stale: wired={wired_n} failed={failed_n}")
    assert proj.g.query(
        "MATCH (e:Event {id:$id})-[r]->() RETURN count(r)",
        params={"id": ev}).result_set[0][0] == 0, (
        "the replay wired an edge from a link whose endpoints are all "
        "hard-deleted")

    # Control: the SAME payload with the delete AFTER the record is not
    # suppressed — so the assertions above are about staleness, not about the
    # key path being broken.
    wired_n, failed_n = proj.replay_about_links(
        payload, {"gone-id": {"Subject": 0}}, 1)
    assert (wired_n, failed_n) == (1, 0), (
        f"the key repair stopped working for a live endpoint: wired={wired_n} "
        f"failed={failed_n}")


# ── Round 5 (fifth independent review at 742ff7246) ──────────────────────────


def test_a_caller_event_prop_cannot_shadow_the_records_aboutLinks(wired) -> None:
    """P2-1 (reader half): the nested unwrap must be ADDRESS-CHECKED.

    ``event`` is a legal caller PROP NAME and also the key the NESTED record
    format puts its payload under (``EventAPI.add_event`` / the miner journal
    ``{event: {id, …}}``). The unconditioned
    ``inner = payload.get("event", payload)`` resolved that ambiguity in the one
    direction that destroys journal data silently. MEASURED at 742ff7246 on the
    record

        {"type": "EventRecorded", "id": "1a14…", "event": {"payload": "x"},
         "aboutLinks": [{"rel": "aboutObject", "how": "id",
                         "targets": ["obj-…"]}]}

    — ``inner`` became the PROP, ``source`` was ``None`` and
    ``replay_about_links`` returned ``(0, 0)``: no count, no warning, while the
    record demonstrably carried ``aboutLinks``. A journaled link claim was
    discarded in exactly the silence this PR exists to end, and a later repair
    of the (pre-existing) Event node-fold collision would turn that over-claimed
    record into replay-created state live never had.

    **This test discriminates the READER alone** — it hand-builds the record, so
    the SDK's intake strip (the sibling test below) cannot help it. With the
    unwrap reverted to ``payload.get("event", payload)`` both assertions fail
    (``(0, 0)``), which is the mutation.
    """
    sdk, proj, _events, _journal = wired
    oid = _nid(sdk.create_object("Shadow"))
    ev = _nid(sdk.create_event("shadowed", "event"))
    payload = {
        "type": "EventRecorded", "id": ev, "eventId": ev,
        "name": "shadowed", "eventKind": "event", "eventStatus": "scheduled",
        # A caller PROP of that name — not the nested payload.
        "event": {"payload": "x"},
        "aboutLinks": [{"rel": "aboutObject", "how": "id", "targets": [oid]}],
    }
    wired_n, failed_n = proj.replay_about_links(payload)
    assert (wired_n, failed_n) == (1, 0), (
        "the record's aboutLinks were silently discarded because a caller "
        f"`event` prop shadowed the record: wired={wired_n} failed={failed_n} "
        "(the pre-fix measurement is (0, 0))")
    assert proj.g.query(
        "MATCH (e:Event {id:$id})-[r:aboutObject]->(o:Object {id:$oid}) "
        "RETURN count(r)", params={"id": ev, "oid": oid}).result_set[0][0] == 1, (
        "the shadowed record's link did not replay onto the Object")

    # The same record with its Event gone must be a COUNTED loss, never (0, 0):
    # "not wired" and "no links claimed" are different facts, and only the
    # second may be silent.
    proj.g.query("MATCH (e:Event {id:$id}) DETACH DELETE e", params={"id": ev})
    wired_n, failed_n = proj.replay_about_links(payload)
    assert (wired_n, failed_n) == (0, 1), (
        "a shadowed record whose source Event is gone reported nothing at all "
        f"instead of one loss: wired={wired_n} failed={failed_n}")


def test_a_caller_event_prop_is_STRIPPED_at_the_Event_door(wired, caplog) -> None:
    """P2-1 (intake half): the ambiguity is removed at the SOURCE as well.

    Reading the record correctly stops the SILENT discard; stripping the prop
    makes the journal claim true in the first place. MEASURED at 742ff7246,
    ``create_event("withprop", "event", event={"payload": "x"},
    aboutObject=oid)`` journaled its ``aboutLinks`` while
    ``_upsert_event`` — which unwraps ``event`` the same way — skipped the node
    entirely, so ``create_event`` returned ``{}``, ``MATCH (e:Event)`` was empty
    and the link had no source to replay onto.

    ``event`` is not Event content: ``_EVENT_HANDLED`` already declares it an
    envelope field the fold must never persist. The strip therefore costs no
    node property — it trades a dropped prop for the Event node AND its links.
    """
    import logging

    sdk, proj, events, journal = wired
    oid = _nid(sdk.create_object("Shadow"))
    with caplog.at_level(logging.WARNING):
        node = sdk.create_event("withprop", "event", event={"payload": "x"},
                                aboutObject=oid)
    ev = _nid(node)
    assert ev, (
        "create_event returned no node — the caller `event` prop still shadows "
        f"the fold: {node!r}")
    assert "ignoring client-supplied 'event'" in caplog.text, (
        "the dropped caller prop was not reported: the strip must be visible, "
        f"not silent: {caplog.text[-2000:]}")

    record = _event_records(journal, "withprop")[0]
    assert "event" not in record, (
        f"the caller prop rode into the record and re-creates the ambiguity: "
        f"{record!r}")
    assert record["aboutLinks"] == [
        {"rel": "aboutObject", "how": "id", "targets": [oid]}], (
        f"the record lost the link the SDK resolved: {record!r}")

    def _edges() -> int:
        return proj.g.query(
            "MATCH (e:Event {id:$id})-[r:aboutObject]->() RETURN count(r)",
            params={"id": ev}).result_set[0][0]

    assert _edges() == 1, "live did not wire the link"
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert proj.g.query(
        "MATCH (e:Event {id:$id}) RETURN count(e)",
        params={"id": ev}).result_set[0][0] == 1, (
        "the Event was live-only: rebuild_all destroyed it")
    assert _edges() == 1, (
        "the link did not survive rebuild_all — the record and the live write "
        "disagree")


def test_a_name_equal_to_the_Events_OWN_name_resolves_to_the_SUBJECT_arm(
        wired) -> None:
    """P2-2: the name cascade is resolved PRE-fold, and that is DELIBERATE.

    ``resolve_about_link`` runs before the Event is written, so the ``Event`` arm
    of ``_ABOUT_NAME_CASCADE`` can never match the record being created.
    MEASURED ``create_event("SelfName", "event", aboutSubject="SelfName")``:

    * ``origin/main`` — live ``(Event)-[:aboutEvent]->(itself)``, which a rebuild
      then LOSES (the #8398 defect: the self-edge was never journaled).
    * this branch — live ``(Event)-[:aboutSubject]->(:Subject{id:"SelfName"})``,
      which SURVIVES rebuild.

    That is an undisclosed change of live wiring in the direction of
    replay-safety, so it is pinned here (and stated in the resolver's
    docstring): the retired self-``Event`` arm could never survive a rebuild, and
    a link that cannot be replayed is not worth preserving. Moving the
    resolution POST-fold would restore it only by giving up the property that
    makes the record and the edge agree — ONE resolution for both — so the
    behaviour is stated instead of changed.
    """
    sdk, proj, events, journal = wired
    ev = _nid(sdk.create_event("SelfName", "event", aboutSubject="SelfName"))

    rows = proj.g.query(
        "MATCH (e:Event {id:$id})-[r]->(t) "
        "RETURN type(r), labels(t), t.id, t.name",
        params={"id": ev}).result_set
    assert rows == [["aboutSubject", ["Subject"], "SelfName", "SelfName"]], (
        "a name equal to the Event's own name must resolve to the :Subject arm "
        f"(measured), got {rows}")
    assert proj.g.query(
        "MATCH (e:Event {id:$id})-[:aboutEvent]->(x:Event) RETURN count(x)",
        params={"id": ev}).result_set[0][0] == 0, (
        "the retired self-aboutEvent edge is back — it can never survive a "
        "rebuild")

    link = _event_records(journal, "SelfName")[0]["aboutLinks"][0]
    assert (link["rel"], link.get("target_label")) == ("aboutSubject", "Subject"), (
        f"the record must pin the arm live actually wired: {link!r}")

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert proj.g.query(
        "MATCH (e:Event {id:$id})-[r]->(t) "
        "RETURN type(r), labels(t), t.id, t.name",
        params={"id": ev}).result_set == rows, (
        "the resolved link did not survive rebuild_all")

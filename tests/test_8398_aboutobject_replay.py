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
minted. ``how: "name"`` survives as a LEGACY-ONLY branch (records written before
that fix, uncorrectable because the journal is append-only), matching
``replay_about_links``'s docstring. The record carries links as data, NOT as node
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

    def _raise(_payload):
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
    """A record written BEFORE the P1-2 fix carries no endpoint — it still replays.

    The journal is append-only, so a deployment upgrading mid-stream has
    ``how:"name"`` links in it. They cannot be repaired (there is no resolved
    endpoint on them to repeat), so the legacy branch keeps their pre-fix
    behaviour rather than reporting every one of them as a lost link. This pins
    that branch AND documents the drift it keeps: the edge lands on whatever the
    rel-ignoring cascade finds, not on the record's ``rel``.
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

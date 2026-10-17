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
time (``id`` = it resolved to the rel's target label; ``name`` = it did not and
will be resolve-or-minted by name) and journals that precise form on the
``EventRecorded`` record; replay re-wires it with the same construction rather
than re-deriving the decision. The record carries links as data, NOT as node
properties — an ``about*`` prop must never become a stored string property.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_8398_aboutobject_replay.py -q
"""
from __future__ import annotations

import json
import os

import pytest

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
    """``how`` is decided LIVE (resolved → ``id``, else the name path)."""
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
    assert links["aboutDocument"]["how"] == "name", (
        "a value that did not resolve must journal as the name path")


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
    """A ``how:"name"`` link must not report a failure it did not have.

    ``_create_about_edges`` is the rel-IGNORING cascade, and a name link is one
    whose own rel resolved to nothing at write time — so live wired whatever the
    cascade found, not ``rel``. Measuring ``rel`` reported failure for the
    reproduction live just made, and reported failure again on every re-run of an
    already-wired link (MERGE adds nothing). The link's PRESENCE is the test.
    """
    sdk, proj, events, journal = wired
    sdk.create_event("nm", "event", aboutDocument="https://example.test/named")
    proj.rebuild_all(str(events), confirm_destructive=True)
    record = _event_records(journal, "nm")[0]
    assert [lk["how"] for lk in record["aboutLinks"]] == ["name"]

    wired_n, failed_n = proj.replay_about_links(record)
    assert failed_n == 0, (
        f"a name link reported failure although the value is linked: "
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


def test_title_matching_is_Source_scoped(wired) -> None:
    """``title`` is a display prop on Object/Event too — the guard is Source-only.

    The cascade matches a document by ``e.url = $name OR e.title = $name`` on
    ``:Source``. Matching ``title`` label-agnostically reported SUCCESS for an
    Event joined to a same-titled Object while no document was joined — a false
    success that masks a real loss.
    """
    sdk, proj, _events, _journal = wired
    # The edge must EXIST for the count to be able to see the title, or the test
    # cannot discriminate: with no edge the query returns 0 rows under the
    # unscoped query too, so it would pass against the very code it pins.
    obj = _nid(sdk.create_object("o", title="MyDoc"))
    ev = _nid(sdk.create_event("titled", "event", aboutObject=obj))
    assert proj._count_about_to_value(ev, "MyDoc") == 0, (
        "an Object's title satisfied a document link — the Event IS joined to "
        "that Object, so an unscoped `t.title` match counts it")
    assert proj._count_about_to_value(ev, "MyDoc-not-a-thing") == 0


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

"""#8171 — a claim must be creatable WITH its source.

The ruling (owner, delivered here): an agent asserting something without
research is a WEAK source, and the source is the agent-session transcript.
``sourceKind: agentSession`` and the canonical ``session:<id>`` identity are
already in the system; this file pins the two halves of the write path that
were NOT.

Three facts, each falsifiable by a named mutation:

1. ``create_entity`` MATERIALIZES the ``:Source`` a caller declares inline.

   This is the capability the epic #888 / #4282 consolidation LOST.
   ``create_document`` — which ``type='document'`` absorbed — wired the Source
   via ``projection._link_source`` (#394). ``create_entity`` stored the
   ``extractedFrom`` STRING as a node property and created NOTHING: no
   ``:Source`` node, no ``extractedFrom`` edge. A caller that had declared a
   source therefore got a node the auditor reads as a provenance gap
   (``audit.missing_sourceKind``), and a later rebuild had no edge to replay.

2. An ``agentSession`` source is not merely present — it is *typed*, so the
   weakness the ruling describes is recorded rather than implied.

3. A write with no source and no provenance is ADVISED, never blocked.

   The ruling says "guard (advisory)". ``audit.missing_sourceKind`` already
   performs the check — as a POST-HOC sweep the writer never sees. Doing it at
   write time is the only version that can change what the writer does next.

Embedded lane (no Docker): ``TORTOISE_TEST_CARVE_OUT=1``.
"""
from __future__ import annotations

import os

import pytest

from tortoise.sdk import TortoiseSDK


def _sdk(tmp_path):
    return TortoiseSDK(db_path=os.path.join(tmp_path, "t.db"),
                       event_log_path=os.path.join(tmp_path, "events.jsonl"))


def _scalar(proj, cypher, **params):
    rows = proj.query(cypher, **params).result_set
    return rows[0][0] if rows else None


def _source_count(proj, url):
    """How many :Source nodes carry this url. 0 ⇒ the Source was never minted."""
    return _scalar(proj, "MATCH (s:Source {url:$u}) RETURN count(s)", u=url)


def _edge_count(proj, node_id, url):
    """How many extractedFrom edges reach the Source at `url` from `node_id`."""
    return _scalar(
        proj,
        "MATCH (n {id:$i})-[:extractedFrom]->(s:Source {url:$u}) RETURN count(*)",
        i=node_id, u=url)


def _any_edge_count(proj, url):
    return _scalar(
        proj,
        "MATCH ()-[:extractedFrom]->(s:Source {url:$u}) RETURN count(*)",
        u=url)


def test_create_entity_materializes_the_declared_source(tmp_path):
    """The lost capability: declaring a source must CREATE the :Source + edge.

    MUTATION THAT MUST RED: drop the ``_link_source`` call added to
    ``create_entity``. Then ``source_count`` is 0 and ``edge_count`` is 0 —
    which is exactly the pre-fix measurement:
        create_entity    :Source nodes=0  edges=0
        create_document  :Source nodes=1  edges=1
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    url = "corpus://notes/8171-declared"

    res = sdk.create_entity(type="document", name="A declared-source doc",
                            documentKind="note", extractedFrom=url)
    node_id = res["node"]["id"]

    assert _source_count(proj, url) == 1, (
        "create_entity did not materialize the declared :Source — the "
        "capability #394 gave create_document is missing here")
    assert _edge_count(proj, node_id, url) == 1, (
        "create_entity stored the extractedFrom STRING but wired no edge, so "
        "the provenance is not traversable and not replayable")


def test_create_entity_session_source_is_typed_agent_session(tmp_path):
    """A session ref materializes as agentSession — the ruling's weak source.

    Not merely present: TYPED. The kind is what carries the weakness, so a
    source that exists but is untyped would satisfy a laxer test and still
    leave the ruling unexercised.

    MUTATION THAT MUST RED: drop the ``_link_source`` call — the Source is
    then never created, so the kind cannot be read at all.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    url = "session:8171-parity-session"

    res = sdk.create_entity(type="document", name="Session-sourced doc",
                            documentKind="note", extractedFrom=url)
    node_id = res["node"]["id"]

    assert _source_count(proj, url) == 1
    assert _edge_count(proj, node_id, url) == 1
    kind = _scalar(proj, "MATCH (s:Source {url:$u}) RETURN s.sourceKind", u=url)
    assert kind == "agentSession", (
        f"a session: ref must mint sourceKind=agentSession (ONTOLOGY §4.6); "
        f"got {kind!r}")


def test_a_claim_door_still_materializes_its_session_source(tmp_path):
    """The claim door (create_point) keeps the capability — regression guard.

    #8171 is about the ENTITY door losing what the claim door already had.
    ``create_point``'s #3263 path infers ``session:<id>`` when a session
    context is present and wires it through the same ``_link_source``; this
    pins that the fix to ``create_entity`` did not disturb it.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    url = "session:8171-claim-door"

    p = sdk.create_point("statement", "a claim sourced from its own session",
                         extractedFrom=url)

    assert _source_count(proj, url) == 1
    assert _edge_count(proj, p["id"], url) == 1
    assert _scalar(proj, "MATCH (s:Source {url:$u}) RETURN s.sourceKind",
                   u=url) == "agentSession"


def test_unsourced_write_is_advised_not_blocked(tmp_path):
    """No provenance ⇒ an advisory. Never a block, never a silent pass.

    The ruling says "guard (advisory)". Three things must hold at once, and a
    laxer test would miss two of them:
      - the write SUCCEEDS (advisory, not a gate), and the node exists;
      - an advisory IS returned (so the writer can act on it);
      - it names the fix, not just the fault.

    MUTATION THAT MUST RED: make ``_provenance_advisory`` always return {}
    (advisory dropped) — the second assertion fails. Make it raise instead
    (advisory turned into a block) — the first fails.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()

    res = sdk.create_entity(type="document", name="Unsourced doc",
                            documentKind="note")

    # 1. The write HAPPENED. An advisory that blocks is not an advisory.
    node_id = res["node"]["id"]
    assert _scalar(proj, "MATCH (n {id:$i}) RETURN count(n)", i=node_id) == 1, (
        "the write must land — the ruling is advisory-only, never a block")

    # 2. The advisory is present.
    advisories = res.get("advisories") or []
    assert advisories, (
        "an unsourced write must carry an advisory; got none")
    assert advisories[0].get("advisory") is True

    # 3. It names the fix (the session ref), not merely the fault.
    assert "session:" in advisories[0]["message"], (
        "the advisory must name the cheap fix (extractedFrom='session:<id>'), "
        "or it tells the writer only that something is wrong")


def test_a_sourced_write_carries_no_advisory(tmp_path):
    """The advisory is about the GAP — it must not fire on a sourced write.

    Without this, an implementation that always advises would pass the test
    above while making the advisory noise, which is how an advisory gets
    ignored and stops changing behaviour.
    """
    sdk = _sdk(tmp_path)
    res = sdk.create_entity(type="document", name="Sourced doc",
                            documentKind="note",
                            extractedFrom="corpus://notes/8171-sourced")
    assert not (res.get("advisories") or []), (
        "a write that declared its source must not be advised")


# ── the shape guard (review P1) ────────────────────────────────────────
#
# `create_entity` hands the ref to `projection._link_source`, whose fan-out is
# `list(source_ref)` for a non-str. MEASURED pre-fix, three ways, all worse
# than a refusal:
#   - `extractedFrom=123`  → TypeError AFTER the CREATE → an ORPHAN node the
#     caller gets no handle to (and an already-journaled EventRecorded on the
#     event door);
#   - `extractedFrom={...}` → one fabricated `:Source` PER KEY — an invented
#     provenance, which the design contract says is worse than none;
#   - a list with a non-str member → that member silently dropped.
# `create_point` has refused all three pre-write since #3263; this is the
# entity door being held to the same contract.


def test_a_non_string_source_ref_is_refused_pre_write(tmp_path):
    """A bad ref must raise and leave NOTHING behind — not a half-write.

    The atomicity half is the load-bearing one: a `ValueError` raised after the
    CREATE still reports an error, but the node is already in the graph and the
    caller has no id for it. Asserting only `raises` would pass for both.

    MUTATION THAT MUST RED: move the guard below the type dispatch — the raise
    still happens (so a `pytest.raises`-only test would still pass) but the
    node count becomes 1.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    before = _scalar(proj, "MATCH (n) RETURN count(n)")

    for bad in (123, 4.5, True, {"url": "corpus://forged"}):
        with pytest.raises(ValueError):
            sdk.create_entity(type="document", name="Bad ref doc",
                              documentKind="note", extractedFrom=bad)

    assert _scalar(proj, "MATCH (n) RETURN count(n)") == before, (
        "the refusal must happen BEFORE the write — a node left behind is an "
        "orphan the caller cannot even name")


def test_a_dict_source_ref_fabricates_no_source(tmp_path):
    """The invented-provenance failure: a dict must mint NO `:Source` at all.

    Pre-fix, `{"url": ..., "kind": ...}` minted one `:Source` per KEY, each
    with an `extractedFrom` edge — provenance that does not exist, reported as
    if it did. Asserting only that the call raises would miss the choke-point
    half: `_link_source` is also reached by callers that do not validate.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()

    with pytest.raises(ValueError):
        sdk.create_entity(type="document", name="Dict ref doc",
                          documentKind="note",
                          extractedFrom={"url": "corpus://d", "kind": "x"})

    # The choke point, exercised directly — a caller that skips the door guard
    # must still not be able to invent a source.
    sdk._get_proj()._link_source(
        sdk.create_entity(type="document", name="Choke doc",
                          documentKind="note")["node"]["id"],
        {"url": "corpus://forged", "kind": "forged"})

    for forged in ("url", "kind", "corpus://forged", "forged"):
        assert _source_count(proj, forged) == 0, (
            f"a non-string ref fabricated a :Source named {forged!r} — an "
            f"invented source is worse than none (design contract #3)")


def test_a_list_source_ref_with_a_bad_member_is_refused(tmp_path):
    """A list is legal; a list with a non-string member is not.

    Pre-fix this silently DROPPED the bad member and wired the rest — the
    caller believed both were recorded. `create_point` refuses the identical
    input, so accepting it here would make the two doors disagree.
    """
    sdk = _sdk(tmp_path)
    with pytest.raises(ValueError):
        sdk.create_entity(type="document", name="List ref doc",
                          documentKind="note",
                          extractedFrom=["corpus://ok", 5])


def test_a_list_of_real_refs_still_fans_out(tmp_path):
    """The guard must not break the legal many-to-many case (ontology §3.3).

    Without this, a guard that refused every list would pass the test above
    while destroying a documented capability.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    refs = ["corpus://notes/8171-a", "corpus://notes/8171-b"]

    res = sdk.create_entity(type="document", name="Fan-out doc",
                            documentKind="note", extractedFrom=refs)
    node_id = res["node"]["id"]

    assert [_edge_count(proj, node_id, r) for r in refs] == [1, 1], (
        "a claim read from several sources carries one edge per source")


# ── the DOMAIN restriction (review P1) ─────────────────────────────────

def test_entity_domains_other_than_document_get_no_extracted_from_edge(
        tmp_path):
    """`extractedFrom` is Point -> Source. Do not invent a wider domain.

    ONTOLOGY §3.3 declares the predicate's domain, and §4.6 is explicit that
    "derived Events and Objects reach their source through their own links
    rather than `extractedFrom`" — their declared predicate is `references`
    (written by `link_source_to_entity`). Wiring subject/object/event through
    `_link_source` did two bad things at once: it landed an edge domain the
    ontology does not declare, AND the edge was live-only, because no
    `_upsert_subject`/`_upsert_object`/`_upsert_event` replays it — so
    `rebuild_all` deleted it and `derived = replay(journal)` failed.

    MUTATION THAT MUST RED: re-add "subject"/"object"/"event" to
    `_ENTITY_SOURCE_LABELS`. Each becomes 1 instead of 0.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    for t, extra in (("subject", {}), ("object", {}),
                     ("event", {"eventKind": "note"})):
        res = sdk.create_entity(type=t, name=f"Dom {t}",
                                extractedFrom=f"corpus://dom/{t}", **extra)
        node_id = res["node"].get("id") or res["node"].get("eventId")
        assert _any_edge_count(proj, f"corpus://dom/{t}") == 0, (
            f"create_entity(type={t!r}) wrote an `extractedFrom` edge — an "
            f"edge domain ONTOLOGY §3.3 does not declare for :{t.title()}, "
            f"and one no replay handler restores")
        assert _source_count(proj, f"corpus://dom/{t}") == 0
        assert node_id, f"the {t} itself must still be created"


def test_create_document_refuses_a_bad_ref_instead_of_swallowing_it(
        tmp_path):
    """The sibling document door must not silently drop what it cannot honour.

    `create_document` routes the ref to the same `_link_source`, so without
    this guard the choke point's container gate turned a bad ref into a silent
    no-op: the caller got a success, no provenance, and no advisory — while
    `create_entity(type='document')` raised. Two doors onto one node kind
    disagreeing about what a legal ref is, is the defect.

    MUTATION THAT MUST RED: remove the `_validate_extracted_from` call from
    `create_document` — no `ValueError` is raised for any of the three inputs.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    before = _scalar(proj, "MATCH (n) RETURN count(n)")

    for bad in (123, {"url": "corpus://forged"}, ["corpus://ok", 5]):
        with pytest.raises(ValueError):
            sdk.create_document("Bad ref doc", "note", extractedFrom=bad)

    assert _scalar(proj, "MATCH (n) RETURN count(n)") == before, (
        "the refusal must precede the write — a rejected ref must leave no "
        "node behind for the caller to lose track of")


def test_the_advisory_does_not_fire_on_a_write_that_carries_a_session_id(
        tmp_path):
    """A supplied `session_id` IS provenance — do not advise it as a gap.

    `create_point` infers `session:<id>` from a usable `session_id` (#3263), so
    a writer that supplied one is not unsourced. Advising it would be a FALSE
    POSITIVE telling the writer to declare the very ref the sibling door
    derives for free — and a false-positive advisory is how an advisory stops
    being read.
    """
    sdk = _sdk(tmp_path)
    res = sdk.create_entity(type="document", name="Session ctx doc",
                            documentKind="note", session_id="sess-8171")
    assert not (res.get("advisories") or []), (
        "a write carrying a usable session_id already has a derivable source")

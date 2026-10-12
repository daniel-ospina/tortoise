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

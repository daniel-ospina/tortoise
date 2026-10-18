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
   source therefore got a node with no provenance, and the provenance was not
   traversable. (``audit`` has no entity-level provenance check to catch it —
   its checks 1 and 7 are Point-only — which is why the gap went unseen.)

   NOT claimed here: that the edge survives ``rebuild_all``. It does not, and
   that gap is PRE-EXISTING (#2296 — ``_create_entity`` journals no
   ``DocumentCreated`` line and no ``_upsert_*`` replays ``_link_source``).
   This change neither creates nor widens it; it makes the entity door behave
   as its own ``create_document`` alias always has. A rebuild-parity assertion
   belongs to whoever closes #2296, not to this test — asserting it here would
   have pinned a claim the fix does not deliver.

2. An ``agentSession`` source is not merely present — it is *typed*, so the
   weakness the ruling describes is recorded rather than implied.

3. A write with no source and no provenance is ADVISED, never blocked.

   The ruling says "guard (advisory)". No post-hoc sweep covers an entity write
   (``audit``'s checks 1 and 7 are Point-only), and a sweep the writer never
   sees cannot change what they do next anyway. Doing it at
   write time is the only version that can change what the writer does next.
   This applies on the CLAIM door too (``create_point``, below): the ruling's
   sentence is about "a claim", and the door that creates Points had no
   advisory at all — ``audit``'s post-hoc check was the only reporter.

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


def _ep(sdk, point_id):
    """The RESOLVED belief of a point after one inheritance pass.

    ``recency_decay=1.0, recompute_interval=0`` makes the pass deterministic
    and ungated, so the assertion reads the weight the sources actually
    resolve to — not the registry key that fed it.
    """
    sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
    return sdk.get_point(point_id)


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
        "the provenance is not traversable")


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
# The choke point is `projection._link_source`, whose fan-out is
# `list(source_ref)` for a non-str — and the pre-fix callers that reached it
# with an UNVALIDATED ref were `create_document` (since #394) and the
# `create_entity` document path this change adds. MEASURED, three ways, all
# worse than a refusal:
#   - `extractedFrom=123`  → TypeError AFTER the CREATE → an ORPHAN node the
#     caller gets no handle to (and an already-journaled EventRecorded on the
#     event door);
#   - `extractedFrom={...}` → one fabricated `:Source` PER KEY — an invented
#     provenance, which the design contract says is worse than none;
#   - a list with a non-str member → that member silently dropped.
# `create_point` has refused all three pre-write since #3263; the shape guard is
# now ONE function all three doors call, so the rules cannot drift apart.


def test_a_bare_source_kind_does_not_silence_the_advisory_on_a_document(tmp_path):
    """`sourceKind` is NOT provenance — it must not silence `no_source`.

    `sourceKind` is a Source-registry property, and NEITHER document door
    stores it: it is in `projection/entities.py::_SOURCE_HANDLED` and so
    is denied the document upsert's open passthrough, while `_upsert_document`'s
    fixed SET clause never writes it. MEASURED: a document created through
    either door with `sourceKind` only ends with no `sourceKind` property, no
    separate `:Source` and no edge.

    So keying silence on it — first unconditionally, then gated on the type —
    was a borrowed-capability bypass on a write with no provenance at all. The
    gate is deleted; only a materializable `extractedFrom` counts.

    (Non-document types are not advised at all — see
    `test_no_source_is_only_advised_where_the_remedy_works` — so the document
    door is the only one that can still be advised, and the one pinned here.)

    MUTATION THAT MUST RED: restore a `sourceKind` branch that returns `{}`.
    """
    sdk = _sdk(tmp_path)
    res = sdk.create_entity(type="document", name="Bare kind doc",
                            documentKind="note", sourceKind="agentSession")
    advisories = res.get("advisories") or []
    assert advisories, (
        "sourceKind is not stored, so this document write is unsourced")
    assert advisories[0].get("code") == "no_source"


def test_an_inert_extracted_from_on_a_non_document_still_advises(tmp_path):
    """A declared ref this type cannot WIRE is a provenance gap, so advise it.

    Only document-kind writes materialize `extractedFrom`. A subject/object/
    event that declares one stores the ref as a bare property, mints no
    `:Source` and wires no edge — the node then asserts a source nothing can
    traverse to, which is worse than declaring none (design contract #3).
    Keying the advisory on the PRESENCE of the field made it silent about
    exactly that gap: the writer is told nothing while the node lies.

    Pairs with `test_entity_domains_other_than_document_get_no_extracted_from_edge`,
    which pins the inertness. This pins that the inertness is REPORTED.

    MUTATION THAT MUST RED: remove the `if _ref:` inert branch from
    `_provenance_advisory`, which returns `{}` and silences the writer.
    """
    sdk = _sdk(tmp_path)
    res = sdk.create_entity(type="subject", name="Inert source subject",
                            extractedFrom="corpus://phantom/subject")
    advisories = res.get("advisories") or []
    assert advisories, (
        "a declared ref that this door cannot wire is a provenance gap")
    assert advisories[0].get("code") == "extracted_from_inert"
    assert advisories[0].get("node_id"), "the advisory must name the node"


def test_no_source_is_only_advised_where_the_remedy_works(tmp_path):
    """An advisory's remedy must be applicable, or the note is noise (P1).

    `_ENTITY_SOURCE_LABELS` is document-only, so `extractedFrom` is the only
    source this door can materialize. A `no_source` advisory on a
    subject/object/event would name `extractedFrom='session:<id>'` as the fix —
    but applying it swaps `no_source` for `extracted_from_inert` and leaves the
    provenance gap exactly where it was. Following the advice must remove the
    condition; so `no_source` fires only where its remedy actually does.

    For a document, re-writing with the named ref yields no advisory. For a
    subject/object/event there is no in-door remedy, so no `no_source` is
    given; a *declared* ref is still reported as `extracted_from_inert`.

    MUTATION THAT MUST RED: delete the `if not _source_label: return {}` guard
    in `_provenance_advisory` — subject/object/event then carry `no_source`,
    whose remedy is inert for them.
    """
    sdk = _sdk(tmp_path)

    # Document: the named remedy is real — applying it removes the condition.
    first = sdk.create_entity(type="document", name="Doc A", documentKind="note")
    adv = (first.get("advisories") or [{}])[0]
    assert adv.get("code") == "no_source"
    assert "session:<session_id>" in adv.get("message", "")
    second = sdk.create_entity(
        type="document", name="Doc B", documentKind="note",
        extractedFrom="session:remedy")
    assert not (second.get("advisories") or []), (
        "the remedy the document advisory names must actually remove the "
        "condition it reports")

    # Non-document: no in-door remedy exists, so no `no_source` is given.
    for t, extra in (("subject", {}), ("object", {}),
                     ("event", {"eventKind": "note"})):
        res = sdk.create_entity(type=t, name=f"Plain {t}", **extra)
        codes = [a.get("code") for a in (res.get("advisories") or [])]
        assert "no_source" not in codes, (
            f"type={t}: no_source names extractedFrom='session:<id>', which "
            "this door cannot wire — applying it would swap to "
            "extracted_from_inert, not remove the condition")
        # A declared ref is still reported as inert — the actionable case.
        inert = sdk.create_entity(type=t, name=f"Inert {t}",
                                  extractedFrom="session:inert", **extra)
        assert (inert.get("advisories") or [{}])[0].get("code") == \
            "extracted_from_inert"


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


def test_a_session_id_alone_does_not_silence_the_advisory(tmp_path):
    """`session_id` is NOT provenance on this door — advise it, and say so.

    This test previously asserted the OPPOSITE, on the reasoning that
    `create_point` infers `session:<id>` from a usable `session_id` (#3263).
    That reasoning was borrowed, and false: the inference lives only in
    `create_point`, which never calls `_provenance_advisory`. Its only callers
    are `create_entity` and `create_document`, and neither derives anything
    from `session_id` — so a `session_id`-only write here lands with no
    `:Source` and no `extractedFrom` edge, and suppressing the advisory hid
    exactly the gap it exists to report (review P1).

    MEASURED on the head tree before this fix: `advisories: None`,
    `extractedFrom` prop `None`, `:Source` count 0, edges 0.

    MUTATION THAT MUST RED: restore the `if isinstance(_sid, str): return {}`
    bypass in `_provenance_advisory` — no `advisories` key is returned.
    """
    sdk = _sdk(tmp_path)
    res = sdk.create_entity(type="document", name="Session ctx doc",
                            documentKind="note", session_id="sess-8171")
    advisories = res.get("advisories") or []
    assert advisories, (
        "this door derives no source from session_id, so the write is unsourced")
    assert advisories[0].get("code") == "no_source"
    assert "session:<session_id>" in advisories[0].get("message", ""), (
        "the advisory must name the ref that WOULD have made this sourced")
    assert "only `create_point` derives" in advisories[0].get("message", ""), (
        "and must say why the session_id the caller DID pass did not count — "
        "otherwise the advice reads as ignoring information the writer gave")


def test_create_document_delivers_the_advisory_too(tmp_path):
    """Both document doors must advise — an advisory on one door only is dead.

    `create_document` is the documented alias AND a live MCP tool
    (`tortoise_create_document`). If only `create_entity(type='document')`
    advised, a caller of the alias would be told nothing — and the advisory's
    entire purpose is to change what the writer does next, which it cannot do
    on a door that never surfaces it.

    MUTATION THAT MUST RED: drop the `result.update(_provenance_advisory(...))`
    line from `create_document` — no `advisories` key is returned.
    """
    sdk = _sdk(tmp_path)
    res = sdk.create_document("Unsourced alias doc", "note")
    advisories = res.get("advisories") or []
    assert advisories, (
        "create_document must deliver the same advisory as create_entity")
    assert advisories[0].get("advisory") is True


def test_create_document_with_a_source_is_not_advised(tmp_path):
    """...and the alias must not advise a write that DID declare its source.

    Pairs with the test above the same way the create_entity pair does: an
    implementation that always advised would pass the first while turning the
    advisory into noise.
    """
    sdk = _sdk(tmp_path)
    res = sdk.create_document("Sourced alias doc", "note",
                              extractedFrom="corpus://notes/8171-alias")
    assert not (res.get("advisories") or [])


def test_alias_doors_deliver_the_advisory(tmp_path):
    """`create_subject`/`create_object`/`create_event` must surface advisories.

    The three alias doors returned ``create_entity(...)["node"]`` and thereby
    DROPPED the top-level ``advisories`` key — so the identical write advised
    through ``create_entity`` but not through the alias. The alias MCP tools
    (`tortoise_create_subject` / `-object` / `-event`) forward that return, so
    an agent writing through them never saw the note; an advisory that reaches
    only some of the doors cannot change behaviour.

    MUTATION THAT MUST RED: unwrap ``["node"]`` again — the key is absent, so
    the assertion has no advisory to read.

    (A subject/object/event with nothing declared is not advised at all — see
    `test_no_source_is_only_advised_where_the_remedy_works` — so a DECLARED
    ref this door cannot wire is the actionable advisory these types produce,
    and that is what is pinned here.)
    """
    sdk = _sdk(tmp_path)
    cases = (
        (sdk.create_subject, ("Alias subject", "person")),
        (sdk.create_object, ("Alias object", "product")),
        (sdk.create_event, ("Alias event", "meeting")),
    )
    for create, args in cases:
        res = create(*args, extractedFrom="session:alias-advised")
        assert (res.get("advisories") or [{}])[0].get("code") == \
            "extracted_from_inert", (
                f"{create.__name__}: the alias door dropped the advisory the "
                "same write surfaces through create_entity")


# ── the CLAIM door ─────────────────────────────────────────────────────
#
# The ruling's sentence is "when a CLAIM is created with no source and no
# provenance, say so". `create_point` is the door that creates claims (Points;
# `create_entity`'s types are Subject/Object/Event/Source — no claim type), and
# it reported nothing: the advisory was wired to the entity doors only.
#
# Sourcing on this door is judged by what IT materializes, the same rule the
# entity doors apply: a declared (or `session_id`-inferred, #3263)
# `extractedFrom` becomes a `:Source` + `extractedFrom` edge, so it is sourced;
# an authored word (`credibility=`) is a source under #398's ruling; a bare
# legacy point-level `sourceKind` is NOT, because it is an annotation with no
# link behind it.

def test_an_unsourced_claim_is_advised_and_still_lands(tmp_path):
    """No source, no provenance ⇒ written AND advised — never blocked.

    MEASURED on origin/main before this change: `create_point('statement',
    text)` returned `['content', 'content_hash', 'createdAt', 'ep_dirty',
    'ep_dirty_at', 'id', 'is_operator', 'pointKind', 'status', 'updatedAt']`
    — no `advisories` key at all, and 0 `extractedFrom` edges. So the only
    reporter of this gap was `audit`'s POST-HOC `missing_sourceKind` check,
    which cannot change what the writer does next.

    Three things must hold at once, and a laxer test would miss two: the write
    LANDED (an advisory that blocks is not an advisory), an advisory is
    RETURNED, and it names the cheap fix rather than only the fault.

    MUTATION THAT MUST RED: drop the `_provenance_notes` block from
    `create_point`'s return — no `advisories` key appears. Make it raise
    instead — the write-landed assertion fails.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()

    res = sdk.create_point("statement", "a claim nobody sourced")

    # 1. The write HAPPENED, and it really is unsourced in the graph.
    node_id = res["id"]
    assert _scalar(proj, "MATCH (n:Point {id:$i}) RETURN count(n)", i=node_id) == 1, (
        "the write must land — the ruling is advisory-only, never a block")
    assert _scalar(
        proj, "MATCH (n:Point {id:$i})-[:extractedFrom]->(s) RETURN count(*)", i=node_id
    ) == 0, "fixture check: the gap must be real (nothing was materialized)"

    # 2. The advisory is present, advisory-only, and 3. names the fix.
    advisories = res.get("advisories") or []
    assert advisories, "a CLAIM written with no source and no provenance must be advised"
    assert advisories[0].get("code") == "no_source"
    assert advisories[0].get("advisory") is True
    assert advisories[0].get("node_id") == node_id
    assert "session:" in advisories[0]["message"], (
        "the advisory must name the cheap fix (extractedFrom='session:<id>')")


def test_a_sourced_claim_carries_no_advisory(tmp_path):
    """...and the other direction: a sourced claim is NOT advised.

    Pairs with the test above the way the entity-door pair does — an
    implementation that always advised would pass the first while turning the
    advisory into noise, which is how an advisory stops changing behaviour.
    Also re-pins the materialization on this door (the capability #3263 gave
    it and #8171 must not perturb).

    Passes on origin/main for the trivial reason that NO claim was ever
    advised; it is the guard that makes the new behaviour two-directional
    rather than a blanket note.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()
    url = "session:8171-sourced-claim"

    res = sdk.create_point("statement", "a claim that declared its session",
                           extractedFrom=url)

    assert not (res.get("advisories") or []), (
        "a claim that declared its source must not be advised")
    assert _source_count(proj, url) == 1
    assert _edge_count(proj, res["id"], url) == 1


def test_a_session_id_inferred_source_silences_the_advisory(tmp_path):
    """The #3263 inference IS materialization — so it counts as sourced.

    A `session_id` on this door derives `session:<id>` and wires it through the
    same `_link_source`, so advising it would be false (the opposite error to
    the borrowed-capability bypass #8171's entity door removed: there the
    inference did not exist on the door, here it does).

    Passes on origin/main trivially (nothing was advised); it is the guard that
    keeps the new note from firing on a write that IS sourced.
    """
    sdk = _sdk(tmp_path)
    proj = sdk._get_proj()

    res = sdk.create_point("statement", "an assertion from a live session",
                           session_id="8171-claim-session")

    assert not (res.get("advisories") or [])
    assert res.get("extractedFrom") == "session:8171-claim-session"
    assert _scalar(proj, "MATCH (s:Source {url:$u}) RETURN s.sourceKind",
                   u="session:8171-claim-session") == "agentSession"


def test_an_authored_word_is_a_source_not_a_provenance_gap(tmp_path):
    """`credibility=` is a SOURCE under #398 — advising it would be wrong.

    #398 (owner ruling 2026-10-12) makes the author's declared word one weak
    evidence body, recorded by `_stamp_authored_word`. A writer who declared
    their basis has a source, so the note must stay silent — this is a
    correctness guard, not a noise guard.

    Passes on origin/main trivially (nothing was advised); it fails the moment
    the advisory is keyed on "no `extractedFrom`" instead of "no source".
    """
    sdk = _sdk(tmp_path)
    res = sdk.create_point("statement", "a claim with a declared word",
                           credibility="medium")
    assert not (res.get("advisories") or [])


def test_an_originating_session_resolves_to_the_WEAK_weight(tmp_path):
    """Acceptance 3 — the RESOLVED weight, not the registry key.

    `SOURCE_KIND_DEFAULTS['agentSession']` is NEUTRAL by #398's recorded
    decision, so `resolve_tier(None, 'agentSession')` is `None` and asking
    that function is NOT how a session's weight is decided: the belief path
    routes an `agentSession` to the carrier rule BEFORE any tier is resolved
    (`session_support_pc`). This test therefore asserts the OUTCOME:
      - an ORIGINATING session is a weak body — ep_alpha 1.1 (pc 0.1, the T4
        rung), mean 0.5238, i.e. real evidence ABOVE ignorance;
      - a NEUTRAL source contributes NOTHING — ep_alpha stays unset, exactly
        the state an originating session must not be left in.

    The pair is the point: weak ≠ neutral. On a tree where the session
    resolved neutral this test fails on the SECOND-to-last assertion (the
    claim would keep `ep_alpha is None`); that is the pre-#398 behaviour the
    body measured as "identical to no source at all".
    """
    from tortoise.source_credibility import (
        AUTHOR_ASSERTION_WEAK_PC,
        pc_base,
        session_support_pc,
    )

    assert pytest.approx(pc_base("T4")) == AUTHOR_ASSERTION_WEAK_PC, (
        "the weak rung is pinned to T4's pseudo-count, never re-invented")
    assert session_support_pc(carries_attached_source=False) == pytest.approx(0.1)
    assert session_support_pc(carries_attached_source=True) == 0.0

    sdk = _sdk(tmp_path)
    originating = sdk.create_point(
        "statement", "an assertion the agent itself originates",
        extractedFrom="session:8171-weight")
    pt = _ep(sdk, originating["id"])
    assert pt["ep_alpha"] == pytest.approx(1.1), (
        f"an originating session must resolve WEAK (alpha 1.1), got {pt!r}")
    assert pt["ep_beta"] == pytest.approx(1.0)
    assert pt["baseline_source"] == "inherited-from-source"
    mean = pt["ep_alpha"] / (pt["ep_alpha"] + pt["ep_beta"])
    assert mean == pytest.approx(1.1 / 2.1)  # 0.5238 — above ignorance

    # CONTROL: a neutral source is recorded and contributes nothing.
    neutral = sdk.create_point(
        "statement", "a claim backed only by an untiered document",
        extractedFrom="corpus://notes/8171-neutral")
    assert _ep(sdk, neutral["id"]).get("ep_alpha") is None, (
        "a neutral source must leave the claim at no weight — that is exactly "
        "the state the ruling says an agent's own assertion must NOT be in")

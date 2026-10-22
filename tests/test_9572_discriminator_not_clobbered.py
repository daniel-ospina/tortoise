"""#9572 — a bare re-create must not rewrite a user-authored discriminator.

The defect (measured on ``origin/main`` 02e18e752):

    create_subject("Probe Subject Z", "team", description="orig")
    create_subject("Probe Subject Z")          # no kind, no props supplied
    -> {'subjectKind': 'other', ...}           # 'team' silently reset

Root cause is the SDK's *fabricated* default: ``create_subject``'s signature
defaults ``subjectKind="other"`` and ``create_entity`` pops
``props.pop("subjectKind", "other")``, so a call that supplies **no**
discriminator still ships ``subject_kind="other"`` on the journal record. The
projection then does what it is told — ``ON MATCH ... subjectKind=coalesce($sk,
s.subjectKind)`` — and overwrites the stored value, because "not supplied" and
"supplied as the default" are the same string.

The contract these tests pin:

* "not supplied" is representable (``None`` / absent), and the projection's
  ``ON CREATE`` arm supplies the ``'other'`` default instead of the caller;
* a bare re-create leaves the stored discriminator alone;
* an **explicit** ``subjectKind``/``objectKind`` still updates it (the fix must
  not turn the discriminator immortal);
* the journal record for a bare re-create carries no ``subject_kind`` /
  ``object_kind`` key, so a ``rebuild_all`` replay reaches the same state as the
  live fold (live == replay).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

TEST_GRAPH = "tortoise_test_9572_discriminator_clobber"


@pytest.fixture
def sdk(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "topic.db"), namespace=TEST_GRAPH,
                      event_log_path=str(events / "events.jsonl"))
    sdk._test_journal_path = str(events / "events.jsonl")
    yield sdk
    try:
        sdk.test_guard()
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


def _graph(sdk):
    return sdk._get_proj().g


def test_subject_bare_recreate_keeps_supplied_kind(sdk):
    sdk.create_subject("Probe Subject Z", "team", description="orig")
    node = sdk.create_subject("Probe Subject Z")
    assert node["subjectKind"] == "team", (
        "a call that supplies no subjectKind rewrote the stored discriminator")
    assert node["description"] == "orig"


def test_subject_explicit_kind_still_updates(sdk):
    """The discriminator must stay mutable when a caller DOES supply one."""
    sdk.create_subject("Probe Subject Y", "team")
    node = sdk.create_subject("Probe Subject Y", "organization")
    assert node["subjectKind"] == "organization"


def test_subject_first_create_still_defaults_to_other(sdk):
    """No caller-supplied kind on a FIRST create still means 'other'."""
    node = sdk.create_subject("Probe Subject X")
    assert node["subjectKind"] == "other"


def test_object_bare_recreate_keeps_supplied_kind(sdk):
    sdk.create_object("Probe Object Z", "capability", description="orig")
    node = sdk.create_object("Probe Object Z", description="CHANGED")
    assert node["objectKind"] == "capability"
    assert node["description"] == "CHANGED"


def test_object_first_create_still_defaults_to_other(sdk):
    node = sdk.create_object("Probe Object X")
    assert node["objectKind"] == "other"


def test_create_entity_bare_recreate_keeps_supplied_kind(sdk):
    """The same root cause through the consolidated door (no alias default)."""
    sdk.create_entity("subject", "Probe Entity Z", subjectKind="team")
    node = sdk.create_entity("subject", "Probe Entity Z")["node"]
    assert node["subjectKind"] == "team"


def test_bare_create_journal_carries_no_fabricated_kind(sdk, tmp_path):
    """live == replay: the journal must not name a kind the caller never named.

    The fabricated default is what split the two layers, in the direction that
    is easy to get backwards: a canonical re-create is probe-gated (#2295 — it
    does not double-journal), so the record kept the FIRST write's real kind
    (`team`) while the LIVE node was reset to `other` — measured on the pre-fix
    tree, `live == replay` was **False**. Replay was the honest copy; the live
    graph was the corrupted one.

    So the assertion is on the FIRST registration: it must carry no fabricated
    key, and a bare create replayed over an empty graph then reaches the same
    state the live fold reaches (`ON CREATE` supplies `'other'`).
    """
    sdk.create_subject("Probe Journal Z")
    journal = EventLog(str(tmp_path / "events" / "events.jsonl")).read_all()
    payloads = [e for e in journal if e.get("type") == "SubjectAdded"]
    assert len(payloads) == 1, "expected exactly one SubjectAdded record"
    bare = payloads[0]
    assert "subject_kind" not in bare, (
        "a bare create journaled a discriminator the caller never supplied: "
        f"{bare.get('subject_kind')!r}")
    assert set(bare) & {"subject_kind", "subjectKind"} == set(), bare


def test_event_api_bare_remention_keeps_supplied_kind(sdk, tmp_path):
    """The EventAPI lane (`add_subject`) had the same fabricated default."""
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    api = EventAPI(EventLog(str(tmp_path / "api.jsonl")), initiated_by="user",
                   agent_id="test", projection=sdk._get_proj())
    api.add_subject("Probe Api Z", "team")
    api.add_subject("Probe Api Z")
    row = _graph(sdk).query(
        "MATCH (s:Subject {name:'Probe Api Z'}) RETURN s.subjectKind").result_set
    assert row and row[0][0] == "team", row


def test_caller_supplied_props_still_apply_on_recreate(sdk):
    """The fix is scoped to the DISCRIMINATOR the caller never named.

    Caller-supplied props (including a caller-supplied kind) still apply on
    MATCH — that is the identity-upsert contract these doors have always had,
    and it is what the `destructive` risk tier (#4122 axis) is derived from.
    """
    sdk.create_object("Probe Props Z", "capability", meta="a")
    node = sdk.create_object("Probe Props Z", meta="b")
    assert node["meta"] == "b"


def test_hosted_rest_request_models_do_not_fabricate_a_kind():
    """The hosted REST doors are a *third* fabrication site (#9572 review P1).

    ``POST /v1/subjects {"name": "x"}`` used to validate into
    ``subjectKind="other"`` and forward it explicitly, so the customer-facing
    write performed exactly the call this issue removed. The model must keep
    "not supplied" distinguishable — the endpoint forwards the kind only when
    it is not ``None``.
    """
    from tortoise.hosted_api import CreateObjectRequest, CreateSubjectRequest

    assert CreateSubjectRequest(name="Probe").subjectKind is None
    assert CreateObjectRequest(name="Probe").objectKind is None
    # a NAMED kind still round-trips (the field is not disabled)
    assert CreateSubjectRequest(name="Probe", subjectKind="team").subjectKind == "team"


def test_org_seed_surface_does_not_fabricate_a_kind(sdk):
    """#9572 review P2: the duck-typed seed surfaces had the same default."""
    from tortoise.hosted_api import _OrgSeedSurface

    surface = _OrgSeedSurface(sdk)
    surface.create_subject("Probe Seed Z", "team")
    node = surface.create_subject("Probe Seed Z")
    assert node["subjectKind"] == "team"


def test_selfhost_seed_surface_does_not_fabricate_a_kind(sdk):
    from tortoise.onboarding.selfhost import _SdkSeedSurface

    surface = _SdkSeedSurface(sdk)
    surface.create_subject("Probe Seed Y", "team")
    node = surface.create_subject("Probe Seed Y")
    assert node["subjectKind"] == "team"


# ── the ingest lane: a mention must not reset a stored discriminator ─────────
#
# The extractor and the capture/claim door were the two remaining fabricators
# (found by a fresh-context review of this fix). Both invented a kind when the
# payload named none, so a document that merely MENTIONED a Subject reset its
# stored kind — the same customer-visible clobber, one layer further out.
# `_s7_document_entities` is default-ON, so this is the default ingest path.


class _KardlessModel:
    """A model that names entities but never names a kind — which an LLM may."""

    id = "kardless-model"

    def __init__(self, payload: dict):
        self._payload = payload

    def complete(self, *, system, user):
        import json
        return json.dumps(self._payload)


def test_semantic_stage_does_not_fabricate_a_kind():
    """The stage output must carry NO kind key when the model named none."""
    from tortoise.extractor import _SemanticStage

    stage = _SemanticStage(_KardlessModel({
        "subjects": [{"name": "Probe Stage Z"}],
        "objects": [{"name": "Probe Stage Obj Z"}],
    }))
    out = stage.run("Heading", "Some body text about Probe Stage Z.", "document:probe.md")
    assert out["subjects"], out
    assert "subjectKind" not in out["subjects"][0], (
        f"the stage fabricated a kind: {out['subjects'][0]}")
    assert "objectKind" not in out["objects"][0], (
        f"the stage fabricated a kind: {out['objects'][0]}")


def test_ingest_mention_does_not_reset_a_stored_kind(sdk):
    """End to end: extracting a document that mentions a known Subject/Object.

    Before the fix, `extract_entities` passed a fabricated `"other"` to
    `api.add_subject`, which the fold applied ON MATCH — so ingesting any
    document that mentioned an existing `team` Subject reset it to `other`.
    """
    from tortoise.api import EventAPI
    from tortoise.extractor import LLMExtractor
    from tortoise.log import EventLog

    sdk.create_subject("Probe Mention Z", "team")
    sdk.create_object("Probe Mention Obj Z", "capability")
    api = EventAPI(EventLog(sdk._test_journal_path),
                   initiated_by="extractor", agent_id="test",
                   projection=sdk._get_proj())
    model = _KardlessModel({
        "subjects": [{"name": "Probe Mention Z"}],
        "objects": [{"name": "Probe Mention Obj Z"}],
    })
    ext = LLMExtractor(model, model)
    text = "## Section\nProbe Mention Z and Probe Mention Obj Z are discussed here."
    ext.extract_entities(text, "probe.md", api)

    subj = _graph(sdk).query(
        "MATCH (s:Subject {name:'Probe Mention Z'}) RETURN s.subjectKind").result_set
    obj = _graph(sdk).query(
        "MATCH (o:Object {name:'Probe Mention Obj Z'}) RETURN o.objectKind").result_set
    assert subj and subj[0][0] == "team", subj
    assert obj and obj[0][0] == "capability", obj


def test_snake_case_kind_prop_is_honoured(sdk):
    """A behaviour change that rides this fix, pinned so it is not "restored".

    `_create_entity` used to overwrite the snake spelling from the camel one
    (`event["subject_kind"] = props["subjectKind"]`), and the camel key always
    existed because of the fabricated default — so `create_object(name,
    object_kind="capability")` was silently rewritten to `"other"`. With the
    fabricated default gone, the caller's snake-case kind reaches the fold.
    """
    assert sdk.create_object("Snake Obj Z", object_kind="capability")["objectKind"] == "capability"
    assert sdk.create_subject("Snake Subj Z", subject_kind="team")["subjectKind"] == "team"


# ── the two lanes the second review round found still fabricating ───────────


def test_conversation_mining_does_not_reset_a_stored_kind(sdk):
    """#9572 review round 2 (P1): `ConversationMiner` — the default path behind
    `tortoise_mine_conversations` (`mine_corpus(extract_entities=True)`).

    It forwarded `_normalize_entity`'s normalized `'other'` to `api.add_object`,
    so mining a conversation that merely MENTIONED a stored `capability` Object
    reset it. Measured on this head: the stored kind survives.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.mining import ConversationMiner

    sdk.create_object("Probe Mined Obj Z", "capability")
    api = EventAPI(EventLog(sdk._test_journal_path), initiated_by="extractor",
                   agent_id="test", projection=sdk._get_proj())
    miner = ConversationMiner(
        _KardlessModel({"entities": [{"name": "Probe Mined Obj Z"}]}))
    miner.mine("We talked about Probe Mined Obj Z in detail.", "src-probe", api)

    row = _graph(sdk).query(
        "MATCH (o:Object {name:'Probe Mined Obj Z'}) RETURN o.objectKind").result_set
    assert row and row[0][0] == "capability", row


def test_normalize_entity_reports_whether_a_kind_was_named():
    """The provenance bit the write door keys on, pinned directly."""
    from tortoise.extractor import _normalize_entity

    assert _normalize_entity({"name": "A"})["objectKind_named"] is False
    assert _normalize_entity({"name": "B", "objectKind": "   "})["objectKind_named"] is False
    assert _normalize_entity(
        {"name": "C", "objectKind": "tool"})["objectKind_named"] is True
    # an EXPLICIT 'other' is a named value — the fix must not freeze it out
    assert _normalize_entity(
        {"name": "D", "objectKind": "other"})["objectKind_named"] is True
    # a value the closed vocab repaired is OUR guess, not the caller's
    assert _normalize_entity(
        {"name": "E", "objectKind": "not-a-kind"})["objectKind_named"] is False


def test_capture_lane_does_not_write_the_classifier_fallback_as_a_kind():
    """#9572 review round 2 (P1): the v2 capture door.

    A v2 payload's `kind` is a CLASSIFICATION and the pipeline repairs an
    unclassifiable entity to `core:other`, so by the time the door runs a kind
    is ALWAYS present — an absent-key guard alone never fires on the default
    capture path, and the repaired sentinel reset a stored `capability`. The
    guard is therefore on the VALUE (`sdk._payload_entity_kind`).
    """
    from tortoise.sdk import _payload_entity_kind

    assert _payload_entity_kind({"name": "x", "kind": "core:other"}) is None
    assert _payload_entity_kind({"name": "x"}) is None
    assert _payload_entity_kind({"name": "x", "kind": "   "}) is None
    # a genuine re-classification still writes
    assert _payload_entity_kind({"name": "x", "kind": "capability"}) == "capability"


def test_uses_stub_does_not_reset_a_stored_kind(sdk):
    """#9572 review round 4 (P2): the Event fold's `uses[]` stub.

    A bare-string `uses` entry names no kind, and the fold both defaulted it to
    `'other'` and wrote it with a hard `ON MATCH SET o.objectKind=$kind` — so an
    event that merely `uses` an existing `capability` Object reset it. The
    structured `{name, kind}` form still writes.
    """
    sdk.create_object("Probe Uses Z", "capability")
    sdk.create_event("Probe Uses Event Z", "meeting", uses=["Probe Uses Z"])
    row = _graph(sdk).query(
        "MATCH (o:Object {name:'Probe Uses Z'}) RETURN o.objectKind").result_set
    assert row and row[0][0] == "capability", row


def test_commit_door_does_not_write_the_classifier_fallback():
    """#9572 review round 4 (P1): the hosted `/v1/sessions/commit` door.

    `TortoiseSDK.commit_session` POSTs the v2 payload here, and `extractor_v2`
    repairs every unclassifiable entity to `core:other` — so this door wrote the
    repair as a real kind and reset stored discriminators. It is the PRODUCTION
    capture entry point; the private `sdk._payload_entity_kind` guard does not
    cover it. Both now share `tortoise.kind_fallbacks`.
    """
    from tortoise.kind_fallbacks import payload_entity_kind, writable_kind

    assert writable_kind("core:other") is None
    assert writable_kind("CORE:other ") is None
    assert writable_kind("") is None
    assert writable_kind(None) is None
    assert writable_kind("capability") == "capability"
    assert payload_entity_kind({"kind": "core:other"}) is None
    assert payload_entity_kind({"kind": "tool"}) == "tool"


def test_summary_payload_names_the_declared_fallback_not_an_invented_kind():
    """#9572 review round 5 (P1): the v1 BYOK summary seam.

    `_summary_to_payload` (reachable from `commit_session(extractor="v1")` /
    `summary=`) invented `kind="core:concept"` for a summary `state` entry that
    named no `objectKind` — a kind written NOWHERE else in the codebase — and
    the commit door wrote it as a real discriminator over a stored `capability`.

    The layer-1 payload REQUIRES `entities[].kind`, so the honest value is the
    declared family fallback, which the door's shared predicate drops. Pinned
    here so a future "be more specific" edit cannot re-invent a kind.
    """
    from tortoise.kind_fallbacks import writable_kind
    from tortoise.sdk import _summary_to_payload

    payload = _summary_to_payload(
        {"summary": "s", "state": [{"name": "ProbeSummaryZ"}]}, "sess-probe")
    kinds = [e["kind"] for e in payload["entities"]]
    assert kinds, payload
    assert all(writable_kind(k) is None for k in kinds), (
        f"the summary seam named a kind the door would WRITE: {kinds}")
    # a caller-supplied kind is still honoured and still writable
    payload2 = _summary_to_payload(
        {"summary": "s", "state": [{"name": "ProbeSummaryY",
                                    "objectKind": "tool"}]}, "sess-probe")
    assert [e["kind"] for e in payload2["entities"]] == ["tool"]


def test_kind_fallbacks_do_not_swallow_an_explicit_other():
    """#9572 review round 6 (P2): `FAMILY_FALLBACK_KINDS` is the NAMESPACED
    sentinel only. The bare `other` is a REAL core kind a caller may name —
    including to re-classify a node back to `other` — so it must stay writable.
    The module's rationale used to say the two spellings were "the same kind",
    contradicting the constant: a reader making the set match the prose would
    silently make an explicit `other` unwritable on the payload doors, and
    contradict `_normalize_entity`, which treats an explicit `other` as a NAMED
    kind (`objectKind_named`). Pinned so that edit reds a test.
    """
    from tortoise.kind_fallbacks import is_family_fallback, writable_kind

    assert writable_kind("other") == "other"
    assert writable_kind(" core:other ") is None
    assert is_family_fallback("core:other") is True
    assert is_family_fallback("other") is False


def test_issue_linking_does_not_retype_a_stored_object(sdk):
    """#9572 review round 6 (P1): the SEVENTH fabricator.

    `_connect_issue_objects` synthesises the kind itself (`"pr"` / `"issue"`
    from a session file's frontmatter key) and wrote it with a hard
    `MERGE (o:Object {id:$oid}) SET o.objectKind=$okind …` — which, on a MERGE
    that MATCHES, re-typed the stored Object (measured through the public
    `index_sessions()` door: a connector's `pm:issue` and a user's `capability`
    both became `issue`) from a hand-written session file. The kind now belongs
    to the fold's `ON CREATE` arm: a re-index refreshes the fields the door
    legitimately owns (name/repo/issue_number/url) and nothing else.

    The test drives the helper DIRECTLY (so it needs no session file); the
    public `index_sessions()` reachability was measured separately by the
    reviewer that found it — this pins the mechanism, not the caller.
    """
    sdk.create_object("Tortoise 9572 issue carrier", "capability")
    g = _graph(sdk)
    rows = g.query(
        "MATCH (o:Object) WHERE o.name = $n RETURN o.id AS id, o.objectKind AS k",
        params={"n": "Tortoise 9572 issue carrier"}).result_set
    assert rows, "the object was not created"
    oid = rows[0][0]
    assert rows[0][1] == "capability", rows

    sdk._connect_issue_objects("evt-9572-issue", {"issues": [{
        "id": oid, "title": "Tortoise 9572 issue carrier",
        "repo": "daniel-ospina/tortoise", "number": 9572,
        "url": "https://example/9572"}]})

    after = g.query(
        "MATCH (o:Object {id:$id}) RETURN o.objectKind AS k, o.repo AS repo, "
        "o.issue_number AS n", params={"id": oid}).result_set
    assert after and after[0][0] == "capability", after
    # …while the fields the door legitimately owns WERE refreshed
    assert after[0][1] == "daniel-ospina/tortoise", after
    assert after[0][2] == 9572, after

    # A connector-written kind survives a re-index too (the measured case).
    sdk.create_object("Connector 9572 carrier", "pm:issue")
    cid = g.query("MATCH (o:Object {name:'Connector 9572 carrier'}) "
                  "RETURN o.id").result_set[0][0]
    sdk._connect_issue_objects("evt-9572-c", {"issues": [{
        "id": cid, "title": "Connector 9572 carrier", "number": 1}]})
    kept = g.query("MATCH (o:Object {id:$id}) RETURN o.objectKind AS k",
                   params={"id": cid}).result_set
    assert kept and kept[0][0] == "pm:issue", kept

    # …and a FIRST reference still gets the door's synthesised kind.
    sdk._connect_issue_objects("evt-9572-new", {"issues": [{
        "id": "obj-9572-brand-new", "title": "Brand new 9572 issue",
        "number": 2}]})
    minted = g.query("MATCH (o:Object {id:'obj-9572-brand-new'}) "
                     "RETURN o.objectKind AS k").result_set
    assert minted and minted[0][0] == "issue", minted


# ── the SECOND spelling of "nothing was named": BLANK ───────────────────────
#
# The seventh round drew the identity doors' line at `kind is not None`, so
# `None` was "absent" — but the blank spelling was not. `""` is not `None`, so
# it passed the approved-kind gate (a bare value names no pack, by design) and
# reached the fold's `coalesce($k, stored)`, which returns the first argument
# that is not NULL — and `""` is not NULL. Measured through the public REST
# door: `{"name":"x","subjectKind":"team"}` then `{"name":"x","subjectKind":""}`
# left the node carrying `""` — a discriminator no pack declares, which is worse
# than the pre-fix `other`. On a FIRST create the same value beat the `'other'`
# default (`coalesce("", 'other')` = `""`).
#
# The rule is `kind_fallbacks.named_kind`, the ONE predicate the payload doors
# already applied (`writable_kind` returns None for blank) and that
# `extractor._normalize_entity` applies (`not str(value).strip()` ⇒ not named).


def test_named_kind_treats_blank_as_absent_but_keeps_a_named_value():
    """The predicate itself, pinned before any door is asserted on it.

    It is deliberately NOT `writable_kind`: that one also drops the classifier's
    `core:other` repair, which is a PAYLOAD rule. An identity door's kind is
    authored by the caller, not classified, so a caller naming `core:other` is
    naming a kind and must stay writable (the alternative would silently make a
    namespaced value unwritable at the door — a `core:other` value written by
    the classifier is dropped by the payload rule, not by this one).
    """
    from tortoise.kind_fallbacks import named_kind, writable_kind

    assert named_kind(None) is None
    assert named_kind("") is None
    assert named_kind("   ") is None
    assert named_kind("team") == "team"
    assert named_kind(" team ") == "team"
    # a named value, even the classifier's spelling: the identity doors write it
    assert named_kind("core:other") == "core:other"
    # …and the PAYLOAD rule still drops it, so the two rules stay distinct
    assert writable_kind("core:other") is None


def test_sdk_subject_door_does_not_write_a_blank_kind(sdk):
    """`create_subject(name, "")` must not reset a stored kind, nor store ``""``."""
    sdk.create_subject("Probe Blank Subject Z", "team")
    node = sdk.create_subject("Probe Blank Subject Z", "")
    assert node["subjectKind"] == "team", (
        f"a blank subjectKind reset the stored discriminator to "
        f"{node['subjectKind']!r}")
    assert node["subjectKind"] != "", "the door stored a blank discriminator"

    # a blank FIRST create still gets the fold's ON CREATE default
    fresh = sdk.create_subject("Probe Blank Subject Y", "  ")
    assert fresh["subjectKind"] == "other", fresh.get("subjectKind")


def test_sdk_object_door_does_not_write_a_blank_kind(sdk):
    sdk.create_object("Probe Blank Object Z", "capability")
    node = sdk.create_object("Probe Blank Object Z", "")
    assert node["objectKind"] == "capability", (
        f"a blank objectKind reset the stored discriminator to "
        f"{node['objectKind']!r}")
    fresh = sdk.create_object("Probe Blank Object Y", "   ")
    assert fresh["objectKind"] == "other", fresh.get("objectKind")


def test_create_entity_door_does_not_write_a_blank_kind(sdk):
    """The consolidated door, where the alias default does not run at all."""
    sdk.create_entity("subject", "Probe Blank Entity Z", subjectKind="team")
    node = sdk.create_entity("subject", "Probe Blank Entity Z",
                             subjectKind="")["node"]
    assert node["subjectKind"] == "team", node.get("subjectKind")
    obj = sdk.create_entity("object", "Probe Blank Entity Obj Z",
                            objectKind="capability")
    node2 = sdk.create_entity("object", "Probe Blank Entity Obj Z",
                              objectKind="  ")["node"]
    assert node2["objectKind"] == "capability", (
        obj.get("objectKind"), node2.get("objectKind"))


def test_event_api_blank_kind_does_not_reset_the_stored_kind(sdk, tmp_path):
    """The EventAPI lane journals a mention, so a blank one reaches the record."""
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    api = EventAPI(EventLog(str(tmp_path / "api-blank.jsonl")),
                   initiated_by="user", agent_id="test",
                   projection=sdk._get_proj())
    api.add_subject("Probe Blank Api Z", "team")
    api.add_subject("Probe Blank Api Z", "")
    row = _graph(sdk).query(
        "MATCH (s:Subject {name:'Probe Blank Api Z'}) RETURN s.subjectKind"
    ).result_set
    assert row and row[0][0] == "team", row

    api.add_object("Probe Blank Api Obj Z", "capability")
    api.add_object("Probe Blank Api Obj Z", "   ")
    row2 = _graph(sdk).query(
        "MATCH (o:Object {name:'Probe Blank Api Obj Z'}) RETURN o.objectKind"
    ).result_set
    assert row2 and row2[0][0] == "capability", row2


def test_seed_surfaces_do_not_write_a_blank_kind(sdk):
    """Both duck-typed seed surfaces forward the kind — blank included."""
    from tortoise.hosted_api import _OrgSeedSurface
    from tortoise.onboarding.selfhost import _SdkSeedSurface

    org = _OrgSeedSurface(sdk)
    org.create_subject("Probe Blank Seed Z", "team")
    assert org.create_subject("Probe Blank Seed Z", "")["subjectKind"] == "team"

    selfhost = _SdkSeedSurface(sdk)
    selfhost.create_subject("Probe Blank Seed Y", "organization")
    node = selfhost.create_subject("Probe Blank Seed Y", "  ")
    assert node["subjectKind"] == "organization", node.get("subjectKind")


def test_blank_kind_journals_no_kind_at_all(sdk, tmp_path):
    """live == replay for the blank spelling too: no key, not a blank key."""
    sdk.create_subject("Probe Blank Journal Z", "")
    journal = EventLog(str(tmp_path / "events" / "events.jsonl")).read_all()
    payloads = [e for e in journal if e.get("type") == "SubjectAdded"]
    assert len(payloads) == 1, payloads
    assert "subject_kind" not in payloads[0], payloads[0]
    assert "subjectKind" not in payloads[0], payloads[0]

"""#12094 — the referentiality cue reaches the S7 ``subjects`` mint, which lacked it.

⛔ NOT "the one Subject mint": a SECOND ``:Subject`` mint runs on the same document
``_run_document`` (each Point's ``aboutEntities`` → ``_create_about_edges`` →
``_mint_subject_stub``) and this change does not gate it — ``#12247`` records it,
with the ``#8398`` decision that keeps it open. The scope statement is the third
paragraph below; this headline used to contradict it.

**What was measured, and what this pins.** ``extractor_v2``'s Subject candidates
ride ``entities[]``, so ``route_mentions`` (S2.2, ``#11021``/``#11211``) already
holds a non-referent before the S5 entity loop can mint it — kind-agnostically,
because it iterates ``entities[]`` by NAME and also strips the typed
``subject``/``object`` slots. ``subject_binding`` is not a mint door at all
(``_resolve_target``: *"Never mints: an unresolved name is a refusal (D8)"*).

The gap was the OTHER extractor generation: ``extractor.py``'s S7
``extract_entities`` carries a literal ``subjects`` section and called
``api.add_subject`` — the explicit registration door (``#3590`` D1) that mints
when no live holder exists — for every name the stage returned, with **no
predicate in between**. These tests pin the reuse: the SAME leaf
(``referentiality.judge``), under the SAME flag, so the two generations cannot
disagree about a surface **this stage returns** — ⛔ NOT about the document:
``#12247`` records the second ``:Subject`` mint on the same ingest (a Point's
``aboutEntities``), which this change declines to gate.

⛔ ``the owner`` is NOT held here, and must not become so. ``#2453``/``#4899``
measured the definite-description class as containing real entities with no
antecedent (``the owner``) alongside non-entities (``the parity harness``) that
are structurally identical; ``referentiality``'s docstring records that the
difference is *meaning* and belongs to the arbiter, never to this cue. The test
that pins this exists so a later "helpful" tightening fails loudly instead of
silently reversing a recorded measurement.
"""
from __future__ import annotations

import os
import sys
import tempfile
from typing import ClassVar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402, RUF100

from tortoise import extractor as extractor_mod  # noqa: E402, RUF100
from tortoise import referentiality  # noqa: E402, RUF100
from tortoise.api import EventAPI  # noqa: E402, I001, RUF100
from tortoise.extractor import LLMExtractor, MockModel  # noqa: E402, RUF100
from tortoise.log import EventLog  # noqa: E402, RUF100
from tortoise.projection import FalkorProjection  # noqa: E402, RUF100

FLAG = "TORTOISE_REFERENTIALITY"
#: The ``#12060`` class (453 Subjects measured in the ``default`` graph) that ``#12118``
#: tracks and this change deliberately does NOT gate — ``value_gate.identifier_only``
#: does not claim it either (measured: it returns ``None`` here while claiming
#: ``#4118``/a path), so it is the surface that pins the DECLINE rather than the
#: ``value_gate`` boundary alone.
ISSUE_SURFACE = "issue:daniel-ospina/tortoise#1234"


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_12094_"), name)


def _proj_api():
    """Projection-backed EventAPI, so a mint is asserted as a NODE, not a line."""
    proj = FalkorProjection(_tmp("t.db"))
    log = EventLog(_tmp("events.jsonl"))
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=proj)
    api._ingest_cache = {}
    return api, proj, log


class _FixedStage:
    """Deterministic stand-in for ``_SemanticStage`` — no LLM, no network.

    The stage's contract is the dict shape ``extract_entities`` consumes, so
    replacing the class is enough and keeps the test about the GATE rather than
    about a model's output.
    """

    payload: ClassVar[dict] = {}

    def __init__(self, *args, **kwargs):
        pass

    def run(self, title, body, context):
        return self.payload


DOC = (
    "## Product Strategy\n"
    "The Organisation Design Team adopted FalkorDB as the memory backbone for "
    "the Tortoise project, replacing an earlier stack after a long review.\n"
)


def _extract(monkeypatch, payload, api):
    monkeypatch.setattr(extractor_mod, "_SemanticStage", _FixedStage)
    monkeypatch.setattr(_FixedStage, "payload",
                        payload, raising=False)
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    return ext.extract_entities(DOC, "strategy.md", api)


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    """Every test states the flag it means; never inherit one from the shell."""
    monkeypatch.delenv(FLAG, raising=False)


# ── 1. a held surface is not materialised, at the node level ────────────────

def test_a_pronoun_subject_is_held_and_mints_no_node(monkeypatch):
    """A pronoun returned as a subject produces ZERO ``:Subject`` nodes.

    ``#11021``'s contract row is *"zero mints from the mention channel"*; the
    v1 twin is *zero mints from a held surface*. Asserted against the graph,
    not just the journal, because a journal line is not a node.
    """
    monkeypatch.setenv(FLAG, "1")
    api, proj, log = _proj_api()
    payload = {"subjects": [
        {"name": "it", "subjectKind": "other"},
        {"name": "they", "subjectKind": "other"},
        {"name": "the former", "subjectKind": "other"},
        {"name": "the thing", "subjectKind": "other"},
    ], "objects": [], "aboutEntities": []}

    result = _extract(monkeypatch, payload, api)

    rows = proj.g.query("MATCH (s:Subject) RETURN s.name").result_set
    assert rows == [], f"held surfaces were materialised: {rows}"
    assert result["subjects"] == 0
    assert result["subjects_minted"] == 0
    assert result["subjects_held"] == 4
    assert not [e for e in log.read_all() if e["type"] == "SubjectAdded"]


def test_a_held_surface_is_reported_with_its_rule_section_and_span(monkeypatch):
    """A hold is RECORDED, never a silent drop (I2) — rule, section, span."""
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    payload = {"subjects": [{"name": "the former", "subjectKind": "other"}],
               "objects": [], "aboutEntities": []}

    result = _extract(monkeypatch, payload, api)

    assert len(result["held_subjects"]) == 1
    held = result["held_subjects"][0]
    assert held["surface"] == "the former"
    assert held["rule_id"] == referentiality.RULE_DEICTIC
    assert held["reason"], "a hold must carry the reason it was held"
    assert held["section"] == "Product Strategy"
    assert len(held["span"]) == 2, "the claim's span must be recorded"
    assert result["subjects_held_by_rule"] == {
        referentiality.RULE_DEICTIC: 1}


def test_a_held_surface_is_not_wired_to_the_document(monkeypatch):
    """``subject_names`` drives the document's aboutSubject wiring (#4938).

    A held surface must stay out of it, or ``_create_about_edges`` falls back
    to a Subject-stub and the hold is undone one layer down.
    """
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    payload = {"subjects": [
        {"name": "it", "subjectKind": "other"},
        {"name": "FalkorDB", "subjectKind": "tool"},
    ], "objects": [], "aboutEntities": []}

    result = _extract(monkeypatch, payload, api)

    assert result["subject_names"] == ["FalkorDB"]
    assert "it" not in result["subject_names"]


# ── 2. a genuine referent still mints ──────────────────────────────────────

def test_a_genuine_subject_still_mints(monkeypatch):
    """The gate must not become a filter — a referent still gets its node."""
    monkeypatch.setenv(FLAG, "1")
    api, proj, log = _proj_api()
    payload = {"subjects": [
        {"name": "Organisation Design Team", "subjectKind": "team"},
    ], "objects": [], "aboutEntities": []}

    result = _extract(monkeypatch, payload, api)

    assert result["subjects"] == 1
    assert result["subjects_held"] == 0
    rows = proj.g.query("MATCH (s:Subject) RETURN s.name").result_set
    assert [r[0] for r in rows] == ["Organisation Design Team"]
    assert [e for e in log.read_all() if e["type"] == "SubjectAdded"]


# ── 3. the census answers in distinct buckets, and never lies by omission ──

def test_the_census_reports_minted_and_held_separately(monkeypatch):
    """Three buckets, never one number — and ``mentions`` is UNANSWERABLE here.

    ``mentions`` is ``None``, not ``[]``: DM1 (``#11021``) puts a mention record
    on the claim Point, and this stage runs before any Point exists. An empty
    list would read as "clean", which is the defect class ``#12047`` records.
    """
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    payload = {"subjects": [
        {"name": "Organisation Design Team", "subjectKind": "team"},
        {"name": "they", "subjectKind": "other"},
        {"name": "the thing", "subjectKind": "other"},
    ], "objects": [], "aboutEntities": []}

    result = _extract(monkeypatch, payload, api)

    assert result["subjects_minted"] == 1
    assert result["subjects_held"] == 2
    assert result["subjects"] == result["subjects_minted"]
    assert sum(result["subjects_held_by_rule"].values()) == result["subjects_held"]
    assert result["mentions"] is None, (
        "this stage has no claim carrier for a mention — the honest answer is "
        "'unanswerable', never an empty list that reads as clean")


# ── 4. unarmed makes the same MINT DECISIONS (the recorded default) ────────

def test_unarmed_the_predicate_does_not_run(monkeypatch):
    """``#5087`` plan R1: *unmeasured ⇒ fail open*. Unarmed MINTS unchanged.

    ⛔ Not "byte-identical": the census keys (``subjects_minted`` … ``mentions``)
    are returned unconditionally, so the dict has seven keys the pre-#12094
    return did not. What the recorded default promises — and what this test
    pins — is that no surface's MINT DECISION changes.
    """
    api, proj, _log = _proj_api()
    payload = {"subjects": [{"name": "it", "subjectKind": "other"}],
               "objects": [], "aboutEntities": []}

    result = _extract(monkeypatch, payload, api)

    assert result["subjects_held"] == 0
    assert result["held_subjects"] == []
    rows = proj.g.query("MATCH (s:Subject) RETURN s.name").result_set
    assert [r[0] for r in rows] == ["it"], "an unarmed run must be unchanged"


# ── 5. the recorded measurement is not silently reversed ───────────────────

@pytest.mark.parametrize("surface", [
    "the owner", "the founder", "the operator", "the user",
])
def test_the_definite_description_class_is_not_held(monkeypatch, surface):
    """``#2453``/``#4899``: these are REAL entities with no antecedent.

    The cue deliberately declines them — the difference between ``the owner``
    and ``the parity harness`` is meaning, and the arbiter owns it
    (``s2_decision_seam``). Tightening the cue to catch them would reverse a
    recorded measurement, so this test fails first.
    """
    monkeypatch.setenv(FLAG, "1")
    assert referentiality.judge(surface) is None

    api, proj, _log = _proj_api()
    payload = {"subjects": [{"name": surface, "subjectKind": "naturalPerson"}],
               "objects": [], "aboutEntities": []}
    result = _extract(monkeypatch, payload, api)

    assert result["subjects_minted"] == 1
    assert result["subjects_held"] == 0
    assert proj.g.query(
        "MATCH (s:Subject) RETURN s.name").result_set == [[surface]]


def test_an_identifier_subject_is_left_to_the_value_gate(monkeypatch):
    """The identifier-only class has ONE owner (``value_gate``), and it is not
    this cue — ``referentiality.judge`` skips it by design so one phrase never
    has two owners deciding two outcomes.

    ⛔ The flag is set for the HOLD's benefit, not ``judge``'s: ``judge`` does
    not consult ``enabled()`` (measured — unset, ``judge("it")`` still returns
    a rule), so what arms the cue on the write path is the ``enabled()`` check at
    ``_referentiality_hold``'s call site. ``_referentiality_hold``'s docstring
    records that split; this comment exists so the setenv here does not read as
    "``judge`` needs the flag".

    ⛔ AND THE CLASS MUST BE PINNED AT THE **EXTRACTOR BOUNDARY**, not only at
    ``judge`` (``#12102`` review P2, measured). ``judge("#4118") is None`` is a
    property of ``referentiality.py`` — a module this change barely touches — so
    on its own it cannot red when the GATE is widened: an arm that holds whatever
    ``value_gate.identifier_only`` claims (``#4118``, a path) left this suite
    **24/24 green**, while that widening is exactly the *"unmeasured discard on a
    live document ingest"* ``_referentiality_hold``'s docstring says it declines
    to ship. The second half below drives ``extract_entities`` for real, over all
    THREE surfaces the docstring's decline covers — ``#4118`` and a path (the two
    ``value_gate.identifier_only`` actually claims) and ``issue:<repo>#<n>``,
    which it does NOT — so the claim the docstring makes as *measured* — that the
    453 ``issue:<repo>#<n>`` Subjects ``#12060`` counted still mint on this path
    (``#12118``) — has an assertion behind it.

    ⛔ THE ``issue:`` SURFACE IS THE LOAD-BEARING ONE (#12102 re-review, measured):
    a mutant that widens the gate to hold anything ``identifier_only`` claims reds
    this test, but a mutant that holds ``issue:``-prefixed surfaces left the suite
    **24/24 green** while this payload was ``#4118`` alone — the very class the
    docstring cites as the open, tracked one (``#12118``) had no pin at all.
    """
    monkeypatch.setenv(FLAG, "1")
    assert referentiality.judge("#4118") is None
    assert referentiality.judge("config/ci-surfaces.yml") is None
    assert referentiality.judge(ISSUE_SURFACE) is None

    api, proj, _log = _proj_api()
    payload = {"subjects": [
        {"name": "#4118", "subjectKind": "other"},
        {"name": "config/ci-surfaces.yml", "subjectKind": "other"},
        {"name": ISSUE_SURFACE, "subjectKind": "other"},
    ], "objects": [], "aboutEntities": []}

    result = _extract(monkeypatch, payload, api)

    assert result["subjects"] == 3 and result["subjects_minted"] == 3
    assert result["subjects_held"] == 0
    rows = proj.g.query("MATCH (s:Subject) RETURN s.name").result_set
    assert sorted(r[0] for r in rows) == sorted(["#4118", "config/ci-surfaces.yml",
                                                ISSUE_SURFACE]), (
        "the identifier class must still mint on the S7 path — widening"
        " _referentiality_hold to value_gate's class is a DIFFERENT decision"
        " and would ship an unmeasured discard")


def test_the_point_about_entities_door_is_declined_and_still_mints(monkeypatch):
    """⛔ A pin on a **DECLINED** door, not on a repaired one — ``#12247``.

    ``#12094`` closes the S7 ``subjects`` write. It does NOT close the other
    ``:Subject`` mint on the SAME document ingest: each Point's ``aboutEntities``
    is passed verbatim to ``api.add_point`` (``_run_document``), the about*
    cascade resolves it against Subject→Object→Event→Source→Point, and a name
    that resolves to nothing mints a Subject stub (``_mint_subject_stub``).

    That construction is a RECORDED DESIGN — ``#8398`` withdrew its retirement
    because *"a name no producer registered lost its link on every replay"* —
    so the open question is not "add a predicate", it is *"may a non-referent be
    an about* target at all"*. v4 answered it on its own path (``route_mentions``
    strips held surfaces from claim/event ``about_entities``); v1 has not, and
    this change declines to decide it here.

    ⛔ SO THIS ASSERTS THE MEASURED STATE: armed, a held surface in
    ``aboutEntities`` STILL becomes a ``:Subject``. It exists so that closing
    the door is a LOUD event — a later change that strips the surface here reds
    this test, and whoever reds it reads ``#12247`` and ``_referentiality_hold``'s
    docstring instead of widening this change silently. ⛔ Do not "fix" this by
    asserting zero nodes without discharging ``#12247``: that reverses the
    recorded scope decision rather than implementing it.
    """
    monkeypatch.setenv(FLAG, "1")
    api, proj, _log = _proj_api()

    class _PointStage:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, title, body, llm_context):
            # `it` is held by the cue (asserted below), and it is exactly the
            # string the point prompt invites: "entity names the claim is about".
            return [{"content": "It replaced an earlier stack.",
                     "pointKind": "statement",
                     "aboutEntities": ["it"],
                     "confidence": 0.8}]

    class _NoRelations:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, contents, llm_context):
            return []

    assert referentiality.judge("it") is not None, (
        "the cue must hold `it` — otherwise this test would pass for the wrong "
        "reason and pin nothing about the declined door")

    # ⛔ Construct FIRST: ``LLMExtractor.__init__`` builds ``self.relations`` from
    # this same class, so patching it earlier replaces the attribute object and
    # ``_run_document`` then reads ``self.relations.model`` off the stand-in.
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    monkeypatch.setattr(extractor_mod, "_DocumentPointStage", _PointStage)
    monkeypatch.setattr(extractor_mod, "_RelationStage", _NoRelations)
    body = "## Product Strategy\nIt replaced an earlier stack in 2024.\n"
    ext._run_document([("Product Strategy", body, [0, len(body)])],
                      "strategy.md", api)

    rows = proj.g.query("MATCH (s:Subject) RETURN s.name").result_set
    assert rows == [["it"]], (
        f"the declined door's measured state is ONE Subject stub named `it`; "
        f"got {rows}. If this is now empty, the `aboutEntities` door was closed "
        f"— discharge #12247 and update this docstring rather than deleting the "
        f"assertion's reasoning")


# ── 6. the non-document return carries the SAME contract ────────────────────

def test_a_non_document_input_returns_the_full_disposition_shape(monkeypatch):
    """#12094 review P2: the early return must not be a SHORTER dict.

    ``extract_entities``' docstring documents its return as ALWAYS carrying the
    disposition keys (``subjects_minted``/``subjects_held``/``held_subjects``/
    ``subjects_held_by_rule``/``mentions``). The non-document branch used to
    return the pre-#12094 five-key dict, so a caller written against the
    documented contract raised ``KeyError`` on any input that is not markdown
    with ``##`` sections. The keys must exist with their ZERO values, and
    ``mentions`` stays ``None`` ("this instrument cannot answer"), never ``[]``
    ("nothing to report"), which would read as clean.
    """
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    monkeypatch.setattr(extractor_mod, "_SemanticStage", _FixedStage)
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))

    # `_document_sections` returns (False, []) exactly when no `## ` header
    # exists, so this input takes the branch under test.
    assert extractor_mod._document_sections("just a plain note")[0] is False
    out = ext.extract_entities("just a plain note", "note.txt", api)

    documented = {"subjects", "objects", "subject_names", "entities",
                  "failed_sections", "subjects_minted", "subjects_held",
                  "held_subjects", "subjects_held_by_rule", "mentions",
                  "subjects_hold_failed", "hold_failures"}
    assert documented <= set(out), (
        f"the non-document return is missing {sorted(documented - set(out))}; "
        "the docstring documents these as always present")
    assert out["subjects_minted"] == 0 and out["subjects_held"] == 0
    assert out["held_subjects"] == [] and out["subjects_held_by_rule"] == {}
    # #12102 review cycle 2: the FAIL-OPEN keys are part of the documented
    # contract too, and a `documented` set that omits them lets them vanish
    # silently — the same KeyError class this test exists to prevent.
    assert out["subjects_hold_failed"] == 0 and out["hold_failures"] == []
    assert out["mentions"] is None, (
        "mentions must be None (unanswerable here), never [] which reads clean")


# ── 7. a RAISING cue is fail-open, and the failure is visible ──────────────

def test_a_raising_cue_is_fail_open_and_reported(monkeypatch):
    """A cue that raises must not abort the document pass (#12094 review P2).

    Both v4 call sites wrap ``route_mentions`` in
    ``except Exception … "candidates kept (fail-open)"``. This module's CALLER
    (``tortoise/ingest.py``) turns an escaping exception into *"S7 entity
    extraction failed"*, which discards every remaining Subject AND every Object
    in the document — a far larger loss than the one non-referent the gate
    exists to stop. So a raise is "judge could not answer": the surface MINTS
    (pre-#12094 behaviour) and the failure is RECORDED, never swallowed.
    """
    monkeypatch.setenv(FLAG, "1")
    api, proj, _log = _proj_api()
    payload = {"subjects": [
        {"name": "Organisation Design Team", "subjectKind": "team"},
        {"name": "it", "subjectKind": "other"},
    ], "objects": [{"name": "FalkorDB", "objectKind": "tool"}], "aboutEntities": []}

    real_hold = extractor_mod._referentiality_hold

    def _boom(surface):
        if surface == "it":
            raise RuntimeError("cue exploded")
        return real_hold(surface)

    monkeypatch.setattr(extractor_mod, "_referentiality_hold", _boom)
    result = _extract(monkeypatch, payload, api)

    # FAIL-OPEN: the surface minted, and the rest of the pass survived.
    assert result["subjects_held"] == 0
    assert result["subjects_minted"] == 2
    assert result["objects"] == 1, (
        "a raising cue must not take the Object loop down with it")
    assert "it" in result["subject_names"]
    rows = proj.g.query("MATCH (s:Subject) RETURN s.name").result_set
    assert sorted(r[0] for r in rows) == ["Organisation Design Team", "it"]

    # VISIBLE: the degradation is reported, not inferred from a count.
    assert result["subjects_hold_failed"] == 1
    assert result["hold_failures"] == [{
        "surface": "it", "section": "Product Strategy",
        "error": "RuntimeError: cue exploded",
    }]


# ── 8. the census buckets count the SAME unit ──────────────────────────────

def test_the_census_counts_surfaces_while_the_records_count_occurrences(monkeypatch):
    """``subjects_minted``/``subjects_held`` are SURFACE-level; records are not.

    A surface the model returns in TWO sections is held twice (two records, two
    spans) but is ONE held surface. The counters must not count occurrences: a
    caller reading "minted + held" would otherwise be adding names to
    occurrences, and the asymmetry used to be invisible because every gate test
    used distinct surfaces inside a single section.
    """
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    two_sections = (
        "## Product Strategy\n"
        "The Organisation Design Team adopted FalkorDB as the memory backbone.\n"
        "## Open Questions\n"
        "It is still unclear whether the adoption holds at scale.\n"
    )
    payload = {"subjects": [
        {"name": "Organisation Design Team", "subjectKind": "team"},
        {"name": "it", "subjectKind": "other"},
    ], "objects": [], "aboutEntities": []}

    monkeypatch.setattr(extractor_mod, "_SemanticStage", _FixedStage)
    monkeypatch.setattr(_FixedStage, "payload", payload, raising=False)
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    result = ext.extract_entities(two_sections, "strategy.md", api)

    # The SAME payload for both sections: 1 minted name (deduped) + 1 held name.
    assert result["subjects_minted"] == 1
    assert result["subjects_held"] == 1, (
        "subjects_held is SURFACE-level, like subjects_minted — one name, not one "
        f"record per section (records: {result['held_subjects']})")
    assert len(result["held_subjects"]) == 2, (
        "held_subjects is OCCURRENCE-level: one record per (surface, section)")
    assert {h["section"] for h in result["held_subjects"]} == {
        "Product Strategy", "Open Questions"}
    assert sum(result["subjects_held_by_rule"].values()) == result["subjects_held"]


# ── 9. the hold reaches the ONLY production caller (#12102 review P2) ───────

class _DispositionExtractor:
    """Returns a fixed census — the caller boundary is what is under test.

    ``_s7_document_entities`` takes ``extractor=`` for injection, so the
    production caller can be driven with no LLM and no graph: the question is
    only whether the disposition ``extract_entities`` computed leaves this
    function, not whether it was computed correctly (section 1 pins that).
    """

    def __init__(self, **census):
        self.census = census

    def extract_entities(self, text, source_id, api, **kwargs):
        return self.census


def _ingest_args(**overrides):
    """The three attributes `_s7_document_entities` reads off the CLI args."""
    base = {"semantic_extract": True, "capture_metadata": False, "domain": None}
    base.update(overrides)
    return type("Args", (), base)()


def test_the_held_subject_census_reaches_the_only_production_caller(capsys):
    """#12102 review P2: a hold that never leaves `extract_entities` is a DROP.

    The PR's central safety claim is *"recorded with its rule, its section and
    the claim span it addressed (I2: never a silent drop)"*. Section 1 above
    pins that ``extract_entities`` RETURNS the record; this one pins that the
    only production caller (`tortoise/ingest.py::_s7_document_entities`, whose
    own return value is `subject_names` — exactly the list a held surface is
    kept out of) does not throw it away.

    Pre-fix this printed nothing about holds, so the assertion below fails on
    the code before the fix: on a live armed ``ingest.py`` run a held Subject
    was discarded with zero observable trace. That matters concretely, because
    ``#5087`` plan R1 makes the *measurement* the gate to ever arming the cue,
    and this is the path that would produce it.
    """
    from tortoise import ingest as ingest_mod

    held = [{"surface": "it", "rule_id": referentiality.RULE_PRONOUN,
             "reason": "pronoun with no antecedent", "section": "Product Strategy",
             "span": [0, 2], "phrase_offset": 4}]
    fake = _DispositionExtractor(
        subjects=1, objects=0, subject_names=["FalkorDB"], failed_sections=[],
        subjects_minted=1, subjects_held=1, held_subjects=held,
        subjects_held_by_rule={referentiality.RULE_PRONOUN: 1}, mentions=None)

    wired = ingest_mod._s7_document_entities(
        DOC, "strategy.md", None, _ingest_args(), extractor=fake)

    out = capsys.readouterr().out
    assert wired == ["FalkorDB"], (
        "the caller still returns only the MINTED names — a held surface must "
        f"never reach `_create_about_edges` (got {wired})")
    assert "held 1 subject surface" in out, (
        "a held Subject left the extractor with no trace at the caller: the "
        f"disposition never reaches a run's output (#12094 I2). stdout was {out!r}")
    assert referentiality.RULE_PRONOUN in out, (
        f"the warning must carry the rule breakdown, so the hold is diagnosable; got {out!r}")


def test_an_unarmed_run_says_nothing_about_holds(capsys):
    """The new line must not appear on a run that held nothing.

    ``#5087`` plan R1: an unarmed run PRINTS nothing about holds. ⛔ Not
    "byte-identical" — the return dict carries the census keys unconditionally
    (see `extractor.py`'s module note and section 4 above); what this pins is
    that the ingest gains no new stdout LINE on every default run, so the
    presence of the message is tested in BOTH directions.
    """
    from tortoise import ingest as ingest_mod

    fake = _DispositionExtractor(
        subjects=1, objects=0, subject_names=["FalkorDB"], failed_sections=[],
        subjects_minted=1, subjects_held=0, held_subjects=[],
        subjects_held_by_rule={}, mentions=None)

    ingest_mod._s7_document_entities(
        DOC, "strategy.md", None, _ingest_args(), extractor=fake)

    out = capsys.readouterr().out
    assert "held" not in out.lower(), (
        f"nothing was held, so nothing may be reported as held; stdout was {out!r}")


# ── 10. the recorded offset is the PHRASE's position, not a substring ───────

def test_the_phrase_offset_is_a_word_boundary_match(monkeypatch):
    """``phrase_offset`` must locate the SURFACE, not a substring inside a word.

    ``body.find("it")`` is wrong for precisely the class this gate holds: the
    pronoun ``it`` occurs INSIDE ``with``/``within``/``itself``, so the recorded
    offset named a different word than the surface — measured at the #12102
    review (``body[34:36] == 'it'`` inside "with"). A phrase that is genuinely
    absent records ``None``, which is the honest answer; a nearby substring is
    not.
    """
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    payload = {"subjects": [{"name": "it", "subjectKind": "other"}],
               "objects": [], "aboutEntities": []}

    # (a) "it" appears ONLY inside other words → no offset at all.
    no_standalone = (
        "## Product Strategy\n"
        "The team met with the vendor within itself, without a decision.\n"
    )
    monkeypatch.setattr(extractor_mod, "_SemanticStage", _FixedStage)
    monkeypatch.setattr(_FixedStage, "payload", payload, raising=False)
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    held = ext.extract_entities(no_standalone, "strategy.md", api)["held_subjects"]

    assert len(held) == 1 and held[0]["surface"] == "it"
    assert held[0]["phrase_offset"] is None, (
        "a substring inside 'with'/'within' is not the phrase's position "
        f"(got {held[0]['phrase_offset']})")

    # (b) a STANDALONE occurrence is located exactly.
    api2, _proj2, _log2 = _proj_api()
    standalone = (
        "## Product Strategy\n"
        "The team met with the vendor and it was decided.\n"
    )
    body = standalone.split("\n", 1)[1]
    expected = body.index(" it ") + 1
    held2 = ext.extract_entities(standalone, "strategy.md", api2)["held_subjects"]
    assert held2[0]["phrase_offset"] == expected, (
        f"expected the standalone occurrence at {expected}, got "
        f"{held2[0]['phrase_offset']}")


# ── 11. the (surface, section) unit is ENFORCED, not just documented ────────

def test_a_repeated_surface_in_one_section_records_once(monkeypatch):
    """``held_subjects`` carries ONE record per (surface, section) — enforced.

    The ``name in seen_subjects`` skip at the top of the loop never applies to a
    HELD surface (its ``continue`` precedes the mint, so it is not added to
    ``seen_subjects``), which is what makes a second section record correctly.
    The same asymmetry let a model that returns the SAME name twice inside ONE
    section append two records with the SAME section — measured at the #12102
    review: ``{"it","it","ODT"}`` gave ``len(held_subjects)==2`` with
    ``[h["section"]] == ['A','A']`` while ``subjects_held==1``.

    The census feeds a measurement (``#5087`` R1), so an occurrence count read
    off ``held_subjects`` must not overcount: the record list is the OCCURRENCE
    unit, and one name raised once in one section is one occurrence.
    """
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    payload = {"subjects": [
        {"name": "Organisation Design Team", "subjectKind": "team"},
        {"name": "it", "subjectKind": "other"},
        {"name": "it", "subjectKind": "other"},
    ], "objects": [], "aboutEntities": []}

    result = _extract(monkeypatch, payload, api)

    assert result["subjects_minted"] == 1
    assert result["subjects_held"] == 1, "surface-level: one name, however often raised"
    assert len(result["held_subjects"]) == 1, (
        "one record per (surface, section): a repeat inside ONE section is the "
        f"same disposition (records: {result['held_subjects']})")
    assert result["held_subjects"][0]["section"] == "Product Strategy"


# ── 12. the failed bucket is INSIDE minted, never a third disjoint unit ─────

def test_a_failed_surface_is_counted_in_minted_and_in_failed(monkeypatch):
    """``subjects_hold_failed`` ⊆ ``subjects_minted`` — pin the CONTAINMENT.

    A cue that raises is fail-open: the surface is MINTED. So a caller computing
    "the population" as ``minted + held + failed`` overcounts by exactly
    ``failed`` — and the overcount lands in the direction that flatters the gate
    (one extra surface looks like one more disposition than the document had).
    The failed count exists so a degraded gate is VISIBLE, not so it can be added.
    """
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    payload = {"subjects": [
        {"name": "Organisation Design Team", "subjectKind": "team"},
        {"name": "it", "subjectKind": "other"},
        {"name": "they", "subjectKind": "other"},
    ], "objects": [], "aboutEntities": []}

    real_hold = extractor_mod._referentiality_hold

    def _boom(surface):
        if surface == "it":
            raise RuntimeError("cue exploded")
        return real_hold(surface)

    monkeypatch.setattr(extractor_mod, "_referentiality_hold", _boom)
    result = _extract(monkeypatch, payload, api)

    minted, held, failed = (result["subjects_minted"], result["subjects_held"],
                            result["subjects_hold_failed"])
    assert (minted, held, failed) == (2, 1, 1), (
        "the raising surface mints (fail-open) AND is reported as failed")
    population = minted + held
    assert population == 3, "minted + held IS the population — no double count"
    assert minted + held + failed == 4 > population, (
        "the three-bucket sum OVERCOUNTS by exactly `failed`; the docstring says "
        "so and a caller must not add it")
    assert "it" in result["subject_names"], "fail-open means it was minted"


# ── 13. a REPEATED HEADER is two sections, not one (round-2 review P2) ─────

def test_two_sections_with_the_same_title_are_two_records(monkeypatch):
    """The record unit is (surface, SECTION), and a section is not its title.

    A repeated header (two `## Open Questions`) is ordinary markdown, and
    ``_document_sections`` returns it as TWO sections with distinct spans —
    measured at the #12102 round-2 review: ``[18, 80]`` and ``[98, 168]``.
    Keying the dedupe on the title collapsed them and UNDERCOUNTED the very
    occurrences this list exists to count, which is the direction that hides a
    gate's work. The record already carries ``span``; that is the identity.
    """
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    doc = (
        "## Open Questions\n"
        "It is still unclear whether the adoption holds at scale here.\n"
        "## Open Questions\n"
        "It remains unclear whether the rollout survives contact with reality.\n"
    )
    payload = {"subjects": [{"name": "it", "subjectKind": "other"}],
               "objects": [], "aboutEntities": []}

    monkeypatch.setattr(extractor_mod, "_SemanticStage", _FixedStage)
    monkeypatch.setattr(_FixedStage, "payload", payload, raising=False)
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    result = ext.extract_entities(doc, "strategy.md", api)

    assert result["subjects_held"] == 1, "still ONE surface"
    assert len(result["held_subjects"]) == 2, (
        "two SECTIONS with the same title are two occurrences — keyed on span, "
        f"never on the title (records: {result['held_subjects']})")
    spans = [tuple(h["span"]) for h in result["held_subjects"]]
    assert len(set(spans)) == 2, f"the two records must be distinguishable: {spans}"


# ── 14. a FAIL-OPEN mint wins over an earlier hold (round-2 review P2) ─────

def test_a_surface_held_then_minted_is_counted_once(monkeypatch):
    """`minted + held == population` — the FINAL disposition, not first sighting.

    A surface held in section A whose cue RAISES in section B is minted by
    fail-open. Mint wins (a node reached the graph), so it must not still be
    counted as held: counting it in both makes ``minted + held`` exceed the
    population. Its section-A ``held_subjects`` record is KEPT — that occurrence
    really was held, and deleting it would be the I2 silent drop.
    """
    monkeypatch.setenv(FLAG, "1")
    api, _proj, _log = _proj_api()
    doc = (
        "## Product Strategy\n"
        "The team met and it was decided to adopt FalkorDB as the backbone.\n"
        "## Open Questions\n"
        "It remains unclear whether the adoption survives at scale.\n"
    )
    payload = {"subjects": [{"name": "it", "subjectKind": "other"}],
               "objects": [], "aboutEntities": []}

    real_hold = extractor_mod._referentiality_hold
    calls = {"n": 0}

    def _flaky(surface):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_hold(surface)          # section A: genuinely held
        raise RuntimeError("cue exploded")     # section B: fail-open mint

    monkeypatch.setattr(extractor_mod, "_referentiality_hold", _flaky)
    monkeypatch.setattr(extractor_mod, "_SemanticStage", _FixedStage)
    monkeypatch.setattr(_FixedStage, "payload", payload, raising=False)
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
    result = ext.extract_entities(doc, "strategy.md", api)

    assert result["subjects_minted"] == 1, "fail-open minted it in section B"
    assert result["subjects_hold_failed"] == 1
    assert result["subjects_held"] == 0, (
        "a MINTED surface is not held — counting it in both overcounts the "
        f"population (minted={result['subjects_minted']}, "
        f"held={result['subjects_held']})")
    assert result["subjects_minted"] + result["subjects_held"] == 1, "the population"
    assert result["subjects_held_by_rule"] == {}, (
        "the by-rule table is derived from the same final-disposition set")
    assert [h["section"] for h in result["held_subjects"]] == ["Product Strategy"], (
        "the section-A occurrence stays on the record — never a silent drop")


# ── 15. the offset locates the phrase the CUE judged (round-3 review P2) ────

def test_the_phrase_offset_survives_case_and_spacing(monkeypatch):
    """`phrase_offset` must find a phrase the cue HELD — not the raw string.

    ``referentiality.judge`` decides on case-folded, edge-stripped TOKENS, so
    ``The  Former`` is HELD. Locating it with a case-sensitive search of the raw
    surface recorded ``None`` for a phrase that IS in the section — measured at
    the #12102 round-3 review — which is the "None means absent" reading a
    measurement (#5087 R1) would take. The offset is now built from the SAME
    tokeniser the decision used, so the two paths cannot disagree.
    """
    monkeypatch.setenv(FLAG, "1")
    body = "The report compares the former approach against the latter one here."

    for surface in ("The  Former", "the   former", "the former"):
        api, _proj, _log = _proj_api()
        payload = {"subjects": [{"name": surface, "subjectKind": "other"}],
                   "objects": [], "aboutEntities": []}
        monkeypatch.setattr(extractor_mod, "_SemanticStage", _FixedStage)
        monkeypatch.setattr(_FixedStage, "payload", payload, raising=False)
        ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))
        result = ext.extract_entities(
            "## Product Strategy\n" + body + "\n", "strategy.md", api)

        assert result["subjects_held"] == 1, f"{surface!r} must be HELD"
        held = result["held_subjects"]
        assert len(held) == 1
        assert held[0]["phrase_offset"] is not None, (
            f"{surface!r} is present in the section, so the offset must exist — "
            "the cue judged its tokens, and the offset searches the same phrase")
        assert body[held[0]["phrase_offset"]:held[0]["phrase_offset"] + 10].lower() == "the former", (
            f"the offset must point AT the phrase (got "
            f"{held[0]['phrase_offset']}: {body[held[0]['phrase_offset']:][:14]!r})")


def test_the_phrase_offset_does_not_share_a_coordinate_base_with_span(monkeypatch):
    """``span[0] + phrase_offset`` is not a document index, and the gap is not 1.

    The code's COORDINATE BASE note forbids combining the two fields of one
    record — ``phrase_offset`` indexes the section's STRIPPED body, ``span`` is
    document coordinates. That note used to claim the combination "lands one
    char left of the surface", which does NOT reproduce (#12102 review P3): the
    gap is the WIDTH OF THE STRIPPED PREFIX — 0 for a flush-left body and -3 for
    a body indented three spaces. A constant-offset story is worse than no
    story, because it invites a caller to apply a constant correction; this
    measures both arms so the note cannot drift back into a plausible wrong
    number.
    """
    monkeypatch.setenv(FLAG, "1")
    payload = {"subjects": [{"name": "it", "subjectKind": "other"}],
               "objects": [], "aboutEntities": []}
    monkeypatch.setattr(extractor_mod, "_SemanticStage", _FixedStage)
    monkeypatch.setattr(_FixedStage, "payload", payload, raising=False)
    ext = LLMExtractor(MockModel("cheap"), MockModel("reason"))

    for pad in ("", "   "):
        api, _proj, _log = _proj_api()
        doc = ("## Product Strategy\n" + pad
               + "The team met with the vendor and it was decided.\n")
        held = ext.extract_entities(doc, "strategy.md", api)["held_subjects"]
        assert len(held) == 1, f"expected one hold, got {held!r}"
        span, offset = held[0]["span"], held[0]["phrase_offset"]
        assert offset is not None, (
            f"the phrase is present (pad={pad!r}); a None offset here is the "
            "'genuinely absent' value")
        true_index = doc.index("it was")
        assert (span[0] + offset) - true_index == -len(pad), (
            "the offset is documented as indexing the STRIPPED body, so "
            f"span[0]+offset must miss the document index by exactly the stripped "
            f"prefix width ({-len(pad)}) for pad={pad!r}; measured "
            f"{span[0] + offset} against {true_index}")

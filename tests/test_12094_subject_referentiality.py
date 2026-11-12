"""#12094 — the referentiality cue reaches the ONE Subject mint that lacked it.

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
disagree about a surface.

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


# ── 4. unarmed is byte-identical (the recorded default) ────────────────────

def test_unarmed_the_predicate_does_not_run(monkeypatch):
    """``#5087`` plan R1: *unmeasured ⇒ fail open*. Unarmed changes nothing."""
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
    has two owners deciding two outcomes."""
    monkeypatch.setenv(FLAG, "1")
    assert referentiality.judge("#4118") is None
    assert referentiality.judge("config/ci-surfaces.yml") is None

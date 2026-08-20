"""Slice 4c (#951, epic #909) — domain_loader adapter over PackRegistry +
dead-call repair regression tests.

Research-r6 §1.2 verified three dead code paths, all swallowed by bare
`except` ponytails:
  1. extractor called `domain_kinds(domain, "pointKind")` — function did not
     exist (AttributeError).
  2. extractor called `known_kinds("subjectKind"/"objectKind")` — took zero
     args (TypeError).
  3. `_warn_unrecognized_kinds` called `kind_is_known(k, "pointKind")` — took
     one arg (TypeError).

Plan §5.2 boundary 4: kind sources collapse to ONE — pack_registry is
canonical, domain_loader is a thin adapter over it.
"""
from __future__ import annotations

import os
import sys
import tempfile  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tortoise import domain_loader
from tortoise.domain_loader import (  # noqa: E402, RUF100
    known_kinds, kind_is_known, domain_kinds, domain_kind_semantics, register_kind,
)
from tortoise.extractor import (  # noqa: E402, RUF100
    _build_pointkind_prompt, _DocumentPointStage, MockModel,
)


# ── Adapter over the real packs ──────────────────────────────────────

def test_domain_kinds_returns_pack_kinds():
    """domain_kinds(domain, bucket) — previously nonexistent (AttributeError)."""
    kinds = domain_kinds("product-strategy", "pointKind")
    assert isinstance(kinds, list)
    assert "useCase" in kinds            # product-strategy pack pointKind
    assert "jobToBeDone" in kinds
    assert "valueProposition" in kinds
    assert "statement" in kinds          # core canonical point kind
    assert "decision" in kinds
    # pack kinds come first, core kinds after
    assert kinds.index("useCase") < kinds.index("statement")


def test_domain_kinds_bucket_scoped():
    """Object bucket for a domain returns pack object kinds + core object kinds."""
    kinds = domain_kinds("product-strategy", "objectKind")
    assert "product" in kinds
    assert "feature" in kinds
    assert "WorkItem" in kinds           # core canonical object kind
    assert "useCase" not in kinds        # bucket-scoped, no cross-bucket bleed


def test_domain_kinds_unknown_domain_falls_back_to_core():
    """A domain with no matching pack degrades to core kinds (no crash)."""
    kinds = domain_kinds("no-such-domain", "pointKind")
    assert "statement" in kinds
    assert "valueProposition" not in kinds  # pack-only kind absent


def test_kind_is_known_two_arg():
    """kind_is_known(kind, bucket) — previously TypeError (took 1 arg)."""
    assert kind_is_known("useCase", "pointKind")
    assert kind_is_known("statement", "pointKind")
    assert not kind_is_known("useCase", "objectKind")
    assert kind_is_known("product", "objectKind")
    assert not kind_is_known("product", "pointKind")
    # namespaced refs resolve to the bare name
    assert kind_is_known("product-strategy:useCase", "pointKind")


def test_known_kinds_bucket():
    """known_kinds(bucket) — previously TypeError (took zero args)."""
    point = known_kinds("pointKind")
    assert "statement" in point
    assert "useCase" in point            # pack point kinds compiled in
    obj = known_kinds("objectKind")
    assert "product" in obj
    assert "statement" not in obj
    assert "meeting" in known_kinds("eventKind")
    assert "strategyDoc" in known_kinds("documentKind")
    # subjectKind has no pack category — resolves empty (extractor falls back
    # to _SemanticStage defaults)
    assert known_kinds("subjectKind") == frozenset()


def test_known_kinds_zero_arg_backward_compat():
    """0-arg known_kinds() keeps working and now includes the pack vocabulary."""
    kinds = known_kinds()
    assert isinstance(kinds, frozenset)
    assert "statement" in kinds
    assert "session" in kinds
    assert "useCase" in kinds            # pack vocabulary compiled into the flat view


def test_kind_is_known_one_arg_backward_compat():
    """1-arg kind_is_known() still works; register_kind() still suppresses."""
    assert kind_is_known("statement")
    assert kind_is_known("useCase")      # compiled pack vocabulary, flat
    register_kind("slice4cTestKind")
    assert kind_is_known("slice4cTestKind")


def test_unknown_bucket_raises():
    """Typo'd buckets fail loudly instead of silently returning empty."""
    with pytest.raises(ValueError):
        known_kinds("bogusKind")
    with pytest.raises(ValueError):
        domain_kinds("product-strategy", "bogusKind")


# ── Pack kind SEMANTICS reach the extractor prompt (R6 §5.4) ─────────

_TMP_PACK = """\
namespace: {ns}
name: "Slice 4c Test Pack"
version: "0.1.0"
tier: free
description: "temp pack exercising #951 kindDefs semantics"

ontology:
  extends: core

  objectKinds:
    - gadget

  pointKinds:
    - alphaPoint
    - betaPoint

  kindDefs:
    alphaPoint:
      description: "the alpha point — first-class pack semantics"
    betaPoint:
      examples: ["beta"]

connectors: []
tools: []
depends_on: []
"""


@pytest.fixture
def temp_pack_registry(monkeypatch, tmp_path):
    """Inject a temp packs dir with one pack declaring kindDefs."""
    pack_dir = tmp_path / "packs" / "slice4c-test"
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.yaml").write_text(_TMP_PACK.format(ns="slice4c-test"))
    monkeypatch.setattr(domain_loader, "_PACKS_DIR", tmp_path / "packs")
    monkeypatch.setattr(domain_loader, "_registry", None)
    yield
    monkeypatch.setattr(domain_loader, "_PACKS_DIR", None)
    monkeypatch.setattr(domain_loader, "_registry", None)


def test_domain_kind_semantics_from_pack_kinddefs(temp_pack_registry):
    """Kind defs come from the packs now — descriptions ride with the kinds."""
    sem = domain_kind_semantics("slice4c-test", "pointKind")
    assert sem["alphaPoint"] == "the alpha point — first-class pack semantics"
    assert sem["betaPoint"] == ""        # no description declared
    assert "statement" in sem            # core kinds included
    assert sem["statement"] == ""        # core fallback filled by the extractor


def test_build_pointkind_prompt_includes_pack_semantics(temp_pack_registry):
    """_build_pointkind_prompt carries pack descriptions + core defaults."""
    sem = domain_kind_semantics("slice4c-test", "pointKind")
    prompt = _build_pointkind_prompt(sem)
    assert "- alphaPoint: the alpha point — first-class pack semantics" in prompt
    assert "- statement: a factual claim or assertion" in prompt  # core fallback
    assert "- betaPoint" in prompt       # no def → bare name


def test_document_stage_receives_pack_semantics(temp_pack_registry):
    """The document point stage embeds pack kind semantics in its system prompt."""
    sem = domain_kind_semantics("slice4c-test", "pointKind")
    stage = _DocumentPointStage(MockModel("mock"), point_kinds=sem)
    assert "alphaPoint: the alpha point — first-class pack semantics" in stage._system


def test_prompt_core_fallback_without_pack(temp_pack_registry):
    """A domain with no pack defs → core defaults (behavior unchanged)."""
    sem = domain_kind_semantics("no-such-domain", "pointKind")
    assert sem == {"statement": "", "decision": "", "vision": "", "strategy": "",
                   "plan": "", "goal": "", "target": "", "observation": "",
                   "hypothesis": ""}
    prompt = _build_pointkind_prompt(sem)
    assert "- statement: a factual claim or assertion" in prompt


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

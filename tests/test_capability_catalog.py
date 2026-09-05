"""#2004 (W8) builder capability catalog — lane-agnostic registry unit tests.

The indexers+extractors registry lives in tortoise/tool_registry.py
(CAPABILITY_CATALOG — R2-9: no new infra). Pins (epic #1976 DM-5 / I-7 /
DE2E-9 + surface 13):

- 4 canonical modules (Session recorder, Session extractor, Document
  indexer + future Document extractor) with kinds ⊆ {indexer, extractor}
  and builder-facing descriptions (the presented copy — the dashboard
  offline fallback mirrors them byte-for-byte).
- every available entry's ``modules`` paths exist on disk (typo'd path =
  registry stale).
- W8b module-note inventory: every listed module file's docstring carries
  the catalog-reference note + the catalog module name (DM-5 wording).
- capability_catalog() rows are the wire shape (name/kind/description/
  available) and match CAPABILITY_CATALOG order/content.

Lane-agnostic: pure registry + filesystem reads — runs in every lane
(carve-out embedded legs included), no graph, no TORTOISE_DB_URI.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tortoise.tool_registry import (
    CAPABILITY_CATALOG,
    CATALOG_NOTE,
    capability_catalog,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _module_docstring(path: Path) -> str:
    """The module's __doc__ (ast-scoped — a note elsewhere in the file
    does NOT satisfy the sweep; DM-5 places it in the module docstring).
    Whitespace-normalized so line wraps inside the note can't mask or
    break phrase matching."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc = ast.get_docstring(tree, clean=False)
    assert doc is not None, f"{path} has no module docstring"
    return re.sub(r"\s+", " ", doc).strip()

# The canonical presented set (W1 placeholder + DE2E-9's 3-module assert).
CANONICAL_NAMES = ("Session recorder", "Session extractor", "Document indexer")
FUTURE_NAME = "Document extractor"
VALID_KINDS = {"indexer", "extractor"}


class TestRegistryShape:
    def test_four_canonical_modules(self):
        """Registry carries the 4 inventory modules — the 3 presented at
        launch (DE2E-9's minimum) + the future document extractor."""
        assert len(CAPABILITY_CATALOG) == 4
        assert tuple(m.name for m in CAPABILITY_CATALOG) == (*CANONICAL_NAMES, FUTURE_NAME)

    def test_kinds_and_descriptions(self):
        """Every entry is a valid kind with builder-facing copy (renderable)."""
        for m in CAPABILITY_CATALOG:
            assert m.kind in VALID_KINDS, m.name
            assert m.description and len(m.description) > 1, m.name
        kinds = {m.name: m.kind for m in CAPABILITY_CATALOG}
        assert kinds["Session recorder"] == "indexer"
        assert kinds["Session extractor"] == "extractor"
        assert kinds["Document indexer"] == "indexer"
        assert kinds[FUTURE_NAME] == "extractor"

    def test_document_extractor_is_future(self):
        """The 4th module is the honest planned entry: no implementation
        modules yet, available=False (the registry entry is its note — a
        future module cannot host a docstring)."""
        future = next(m for m in CAPABILITY_CATALOG if m.name == FUTURE_NAME)
        assert future.available is False
        assert future.modules == ()

    def test_all_module_paths_exist_on_disk(self):
        """No typo'd/dangling module path in the registry (accuracy pin)."""
        for m in CAPABILITY_CATALOG:
            for mod in m.modules:
                assert (_REPO_ROOT / mod).is_file(), f"{m.name} references missing module {mod}"


class TestModuleNoteInventory:
    """W8b sweep inventory (DE2E-9: every module carries the catalog note)."""

    def test_every_listed_module_docstring_carries_note_and_name(self):
        """Every module file a catalog entry lists has the DM-5 note in its
        module DOCSTRING, naming that catalog module. A module added without
        the note (or a sweep that missed a file) reds here."""
        for m in CAPABILITY_CATALOG:
            for mod in m.modules:
                doc = _module_docstring(_REPO_ROOT / mod)
                # DM-5 canonical phrase lives inside the module __doc__
                assert "referenced in the builder capability catalog" in doc, mod
                assert "CAPABILITY_CATALOG" in doc, mod
                # the catalog module's own name is named by the note
                # (docstring-scoped: a name elsewhere in the file can't pass)
                assert m.name in doc, f"{mod} note does not name {m.name}"

    def test_future_entry_documented_in_registry(self):
        """The future Document extractor carries its note in the registry
        entry (no file to sweep) — its description marks it planned."""
        future = next(m for m in CAPABILITY_CATALOG if not m.available)
        assert "planned" in future.description.lower()


class TestWireAccessor:
    def test_capability_catalog_rows_match_entries(self):
        rows = capability_catalog()
        assert len(rows) == len(CAPABILITY_CATALOG)
        for row, m in zip(rows, CAPABILITY_CATALOG, strict=True):
            assert row == {
                "name": m.name,
                "kind": m.kind,
                "description": m.description,
                "available": m.available,
            }

    def test_wire_contract_keys(self):
        """I-7 wire shape: modules rows carry name/kind/description (+ the
        availability flag marking the honest future/planned module)."""
        rows = capability_catalog()
        assert set(rows[0]) == {"name", "kind", "description", "available"}
        assert {r["name"] for r in rows} == {*CANONICAL_NAMES, FUTURE_NAME}

    def test_note_constant_is_sweepable(self):
        """CATALOG_NOTE carries the DM-5 phrase the inventory asserts."""
        assert "builder capability catalog" in CATALOG_NOTE
        assert "CAPABILITY_CATALOG" in CATALOG_NOTE
        # every swept docstring embeds the SAME canonical anchor phrase —
        # drift between CATALOG_NOTE and the swept notes reds the inventory
        # (notes may add per-module clauses after the anchor, never before)
        anchors = [_module_docstring(_REPO_ROOT / mod)
                   for m in CAPABILITY_CATALOG for mod in m.modules]
        phrase = re.search(r"this module is referenced in the builder capability "
                           r"catalog \(onboarding\)", CATALOG_NOTE, re.IGNORECASE)
        assert phrase, "CATALOG_NOTE lost its anchor phrase"
        for doc in anchors:
            assert phrase.group(0).lower() in doc.lower()

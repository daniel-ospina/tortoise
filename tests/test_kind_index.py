"""Kind-index tests (issue #1695, Task 3): content-addressed persisted
kind-embedding index. Stub-encoder lane (fixed numpy fixture vectors — no
torch, no model); the real-embedder smoke subset lives in
tests/test_kind_classifier.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.kind_index import (
    KindIndex,
    _clear_index_cache,
    cache_key_for,
)
from tortoise.value_extractor import compile_kind_index_spec


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Cross-test isolation: the module-level memo must not leak built
    indexes between tests (persist/load/missing-file pins)."""
    _clear_index_cache()
    yield
    _clear_index_cache()


class StubEncoder:
    """Deterministic fixture encoder: fixed seeded vectors (8-dim), one row
    per text. Records call counts for the memoization pins."""

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        rng = np.random.default_rng(42)
        return rng.standard_normal((len(texts), 8)), False


@pytest.fixture(scope="module")
def spec():
    return compile_kind_index_spec()


class TestKindIndexBuild:
    def test_build_uses_spec_kinds(self, spec):
        idx = KindIndex.build(spec, encoder=StubEncoder(), persist=False)
        assert list(idx.kind_names) == sorted(spec)
        assert len(idx) == len(spec)
        assert idx.vectors.shape == (len(spec), 8)

    def test_core_vocabulary_included(self, spec):
        """A core bit is classifiable: core:Project is in the candidate set."""
        assert "core:Project" in spec
        assert spec["core:Project"]["section"] == "objects"

    def test_pack_kinds_carry_synonyms_examples(self, spec):
        """The kindDefs FULL key set rides through (the D0-2 refinement)."""
        assert "product-strategy:product" in spec
        assert spec["product-strategy:product"]["examples"]  # Tortoise/El Dato
        assert "dev:issue" in spec
        assert "ticket" in spec["dev:issue"]["synonyms"]
        assert spec["dev:issue"]["nearMisses"]

    def test_spec_surface_embeds_synonyms_and_examples(self, spec):
        txt = spec["product-strategy:product"]["text"]
        assert "examples:" in txt and "Tortoise" in txt
        txt2 = spec["dev:issue"]["text"]
        assert "synonyms:" in txt2 and "ticket" in txt2

    def test_section_derived_from_pack_declarations(self, spec):
        """The per-type candidate restriction needs correct sections: pack
        pointKinds land in "points", eventKinds in "events", object/
        document kinds in "objects" — never a blanket "objects"."""
        assert spec["product-strategy:jobToBeDone"]["section"] == "points"
        assert spec["product-strategy:useCase"]["section"] == "points"
        assert spec["dev:requirement"]["section"] == "points"
        assert spec["product-strategy:architecture"]["section"] == "objects"
        assert spec["core:Project"]["section"] == "objects"
        assert spec["core:decision"]["section"] == "events"


class TestPersistLoad:
    def test_round_trip(self, spec, tmp_path):
        idx = KindIndex.build(spec, encoder=StubEncoder(), persist=True, cache_dir=tmp_path)
        path = idx.persist(cache_dir=tmp_path)
        assert path.exists()
        loaded = KindIndex.load(spec, cache_dir=tmp_path)
        assert loaded is not None
        assert loaded.kind_names == idx.kind_names
        assert loaded.vectors.shape == idx.vectors.shape
        assert loaded.metadata == idx.metadata
        np.testing.assert_allclose(loaded.vectors, idx.vectors)

    def test_cache_key_rotates_on_spec_change(self, spec):
        key_a = cache_key_for(spec)
        mutated = dict(spec)
        mutated["core:Project"] = {
            "text": "changed",
            "section": "objects",
            "description": "x",
            "synonyms": [],
            "examples": [],
            "nearMisses": [],
        }
        key_b = cache_key_for(mutated)
        assert key_a != key_b  # a pack/core change rotates the index

    def test_missing_file_returns_none(self, spec, tmp_path):
        assert KindIndex.load(spec, cache_dir=tmp_path) is None

    def test_persisted_file_is_gitignored_dir(self, spec):
        from tortoise.kind_index import DEFAULT_CACHE_DIR

        assert "kind_index" in str(DEFAULT_CACHE_DIR)


class TestNearest:
    def test_returns_top_k_in_order(self, spec):
        idx = KindIndex.build(spec, encoder=StubEncoder(), persist=False)
        # first kind's own vector → it must be the top-1 candidate
        v = idx.vectors[0]
        top = idx.nearest(v, k=5)
        assert len(top) == 5
        assert top[0][0] == idx.kind_names[0]
        assert all(top[i][1] >= top[i + 1][1] for i in range(4))

    def test_restrict_filters_candidates(self, spec):
        idx = KindIndex.build(spec, encoder=StubEncoder(), persist=False)
        v = idx.vectors[0]
        restrict = ["core:Project", "core:plan", "core:goal"]
        top = idx.nearest(v, k=2, restrict=restrict)
        assert all(k in restrict for k, _ in top)
        assert len(top) == 2

    def test_near_misses_resolves_bare_refs(self, spec):
        idx = KindIndex.build(spec, encoder=StubEncoder(), persist=False)
        nms = idx.near_misses("dev:issue")
        # the manifest declares nearMisses: [code] — resolves to dev:code
        assert any(k.endswith(":code") for k in nms)


class TestMemoizationAndLazy:
    def test_build_memoized_per_spec(self, spec):
        enc = StubEncoder()
        KindIndex.build(spec, encoder=enc, persist=False)
        KindIndex.build(spec, encoder=enc, persist=False)
        assert enc.calls == 1  # second build hits the memo

    def test_import_has_no_torch(self):
        """Lazy-import guard: importing tortoise.kind_index must not pull
        torch/sentence-transformers into the process. Checked in a SUBPROCESS
        so earlier modules in this pytest process can't contaminate it."""
        import subprocess
        import sys as _sys

        code = (
            "import tortoise.kind_index, sys; "
            "assert 'sentence_transformers' not in sys.modules, "
            "'sentence_transformers imported by kind_index'; "
            "assert 'torch' not in sys.modules, 'torch imported by kind_index'"
        )
        subprocess.run([_sys.executable, "-c", code], cwd=Path(__file__).parent.parent, check=True)

    def test_gitignored_cache_dir(self, spec):
        """The persisted index lives under the repo's gitignored data dir."""
        from tortoise.kind_index import DEFAULT_CACHE_DIR

        gitignore = (Path(__file__).parent.parent / ".gitignore").read_text()
        assert "data/kind_index/" in gitignore
        assert DEFAULT_CACHE_DIR.name == "kind_index"
        assert DEFAULT_CACHE_DIR.parent.name == "data"

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
    """Cross-test isolation: the module-level memos must not leak built
    indexes / kind-specs between tests (persist/load/missing-file pins)."""
    from tortoise.value_extractor import _clear_kind_spec_cache

    _clear_index_cache()
    _clear_kind_spec_cache()
    yield
    _clear_index_cache()
    _clear_kind_spec_cache()


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
        """The per-type candidate restriction needs correct sections:
        eventKinds land in "events", object/document kinds in "objects";
        pack pointKinds are NOT classifiable (points = statement only,
        FIX A) — point-only kinds never appear in the spec."""
        assert spec["product-strategy:architecture"]["section"] == "objects"
        assert spec["core:Project"]["section"] == "objects"
        assert spec["core:decision"]["section"] == "events"
        # synthesized declared kinds (FIX L): event/doc kinds without a
        # kindDef ride through with the derived section
        assert spec["pm:cardCreated"]["section"] == "events"
        assert spec["dev:apiSpec"]["section"] == "objects"
        # point-only kinds are absent (never classifiable)
        assert "dev:requirement" not in spec
        assert "product-strategy:jobToBeDone" not in spec

    def test_points_section_contains_only_statement(self, spec):
        """FIX A candidate/write-gate alignment: the index's "points"
        section must contain ONLY "statement" — pack pointKinds are NOT
        classifiable (point classification is trivial per the design doc),
        so a point item can never be assigned a pack point kind."""
        points = {k: md for k, md in spec.items()
                  if md.get("section") == "points"}
        assert set(points) == {"statement"}
        assert "dev:bug" not in spec
        assert "dev:technicalDebt" not in spec
        assert "pm:estimate" not in spec

    def test_declared_kinds_without_kinddefs_synthesized(self, spec):
        """FIX L: declared-but-kindDefs-less pack kinds (dev:apiSpec,
        marketing:keyword, pm:milestone, ALL 8 pm eventKinds, ...) are in
        the candidate set — the classifier can't assign a kind that isn't
        in the index, and nearMisses refs to it would resolve to ∅.
        Synthesized entries are name-only; an existing kindDefs entry is
        never clobbered."""
        assert spec["dev:apiSpec"]["section"] == "objects"
        assert spec["dev:deployment"]["section"] == "objects"
        assert spec["dev:api"] is not None
        assert spec["marketing:keyword"]["section"] == "objects"
        assert spec["marketing:contentCalendar"]["section"] == "objects"
        assert spec["pm:milestone"]["section"] == "objects"
        assert spec["pm:kanbanBoard"]["section"] == "objects"
        for ek in ("cardCreated", "cardMoved", "sprintStarted",
                   "sprintCompleted", "stepStarted", "stepCompleted",
                   "gatePassed", "gateBlocked"):
            assert spec[f"pm:{ek}"]["section"] == "events", ek
            assert spec[f"pm:{ek}"]["description"] == "", ek
            assert spec[f"pm:{ek}"]["synonyms"] == [], ek
        # multi-declared kind: marketing:contentBrief is in pointKinds AND
        # documentKinds → objects (document first; FIX A skips point-only)
        assert spec["marketing:contentBrief"]["section"] == "objects"
        # a kindDefs entry is never clobbered by the synthesis
        assert spec["dev:issue"]["description"]
        assert spec["dev:issue"]["text"] != "dev:issue"


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

    def test_degraded_npz_load_returns_none(self, spec, tmp_path):
        """A persisted index built while the embedder was DOWN (the stored
        degraded flag) must never load — the classifier rebuilds in-process
        so a degraded npz can't load forever after the embedder recovers
        (cycle-3 P2 degraded-npz stickiness)."""
        idx = KindIndex.build(spec, encoder=StubEncoder(), persist=False)
        idx.degraded = True
        path = idx.persist(cache_dir=tmp_path)
        assert path.exists()
        assert KindIndex.load(spec, cache_dir=tmp_path) is None

    def test_persisted_file_is_gitignored_dir(self, spec):
        from tortoise.kind_index import DEFAULT_CACHE_DIR

        assert "kind_index" in str(DEFAULT_CACHE_DIR)

    def test_load_pops_degraded_memo_entry(self, monkeypatch, tmp_path):
        """FIX-N: a DEGRADED index memoized in-process (embedder down during
        a default-encoder build) is POPPED on load — treated as a miss so a
        recovered embedder rebuilds good (persist=True). The disk guard
        cannot cover the memo path; without this, every classify_items would
        fail-open for the process lifetime."""
        import tortoise.kind_index as ki

        state = {"up": False}

        class FakeDefault:
            def encode(self, texts):
                rng = np.random.default_rng(1)
                return rng.standard_normal((len(texts), 4)), not state["up"]

        monkeypatch.setattr(ki, "_DefaultEncoder", FakeDefault)
        monkeypatch.setattr(ki, "DEFAULT_CACHE_DIR", tmp_path)
        spec = compile_kind_index_spec()
        state["up"] = False
        idx = KindIndex.build(spec, persist=False)
        assert idx.degraded is True, "embedder down → degraded build"
        key = cache_key_for(spec)
        with ki._INDEX_LOCK:
            assert key in ki._INDEX_CACHE
            assert ki._INDEX_CACHE[key].degraded is True
        state["up"] = True
        assert KindIndex.load(spec) is None, \
            "a memoized degraded index is popped + treated as a miss"
        with ki._INDEX_LOCK:
            assert key not in ki._INDEX_CACHE, \
                "the degraded entry must not survive the load"
        good = KindIndex.build(spec, persist=True)
        assert good.degraded is False, "recovery rebuilds good"


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
    def test_stub_builds_never_memoized(self, spec):
        """An injected stub encoder changes the vector space — its builds
        must never be memoized (a stub build must not shadow a production
        index under the same spec key, or vice versa)."""
        enc = StubEncoder()
        KindIndex.build(spec, encoder=enc, persist=False)
        KindIndex.build(spec, encoder=enc, persist=False)
        assert enc.calls == 2  # fresh build each time (no memo for stubs)

    def test_default_encoder_build_memoized(self, spec, monkeypatch):
        """The PRODUCTION (default-encoder) build is load-once memoized."""
        import tortoise.kind_index as ki
        calls = {"n": 0}

        class FakeDefault:
            def encode(self, texts):
                calls["n"] += 1
                rng = np.random.default_rng(1)
                return rng.standard_normal((len(texts), 4)), False

        monkeypatch.setattr(ki, "_DefaultEncoder", FakeDefault)
        KindIndex.build(spec, persist=False)
        KindIndex.build(spec, persist=False)
        assert calls["n"] == 1  # second default build hits the memo

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

    def test_kind_spec_parsed_once_and_clear_hook(self, monkeypatch, tmp_path):
        """compile_kind_index_spec is load-once per process per RESOLVED
        packs_dir (Task 3 deliverable — per-session classifier construction
        must not re-read + YAML-parse every pack manifest); the clear hook
        forces a re-parse. The memo is KEYED by the resolved dir: a
        custom-dir call never poisons the default-dir memo and vice versa
        (cycle-3 P2 unkeyed memo)."""
        import tortoise.pack_registry as pr
        from tortoise.value_extractor import (
            _clear_kind_spec_cache,
            compile_kind_index_spec,
        )

        _clear_kind_spec_cache()
        calls = {"n": 0}
        orig = pr.PackRegistry.load_all

        def counting_load_all(self):
            calls["n"] += 1
            return orig(self)

        monkeypatch.setattr(pr.PackRegistry, "load_all", counting_load_all)
        spec_a = compile_kind_index_spec()
        first = calls["n"]
        assert first >= 2  # compile_value_brief + the kindDefs pass
        spec_b = compile_kind_index_spec()
        assert calls["n"] == first, \
            "a second default-dir call must hit the memo (no manifest re-parse)"
        assert spec_a == spec_b
        # custom dir → SEPARATE memo slot (resolved-path key): the custom
        # call parses its own (empty) dir, and the default-dir memo is
        # untouched by it — no cross-dir poisoning.
        other_dir = tmp_path / "packs"
        other_dir.mkdir()
        spec_custom = compile_kind_index_spec(other_dir)
        assert calls["n"] > first, \
            "a custom-dir call parses its own manifests (separate memo slot)"
        assert spec_custom != spec_a, \
            "the custom dir (no packs) yields only the core vocabulary"
        after_custom = calls["n"]
        spec_a2 = compile_kind_index_spec()
        assert spec_a2 == spec_a
        assert calls["n"] == after_custom, \
            "the default-dir memo is untouched by the custom-dir call"
        _clear_kind_spec_cache()
        compile_kind_index_spec()
        assert calls["n"] > first, "clear hook must force a re-parse"

"""#4520 — one clear-then-set seam for vector writes, and a start-up discard probe.

**The defect.** On the embedded engine (``falkordblite``/``redislite``) a
``SET x.embedding = vecf32($v)`` **OVERWRITE** of a property that already holds a
vector is **silently discarded** unless some component moves by ~1.0 — and real
embedder output never does. ``typeof`` still reads ``Vectorf32`` and the *old*
value stays on the node, so a type check passes while the value is stale. The
server lane lands the identical write.

**Why the vectors here are realistic.** A synthetic one-hot probe changes a
component by exactly 1.0, which LANDS even on the embedded engine — it passes and
hides the defect (that is how the finding was briefly refuted during review).
Every vector in this module differs by **0.01 per component**, squarely inside
the dropped regime, and the assertions read the **VALUE**, never ``typeof``.

**RED before, GREEN after.** ``test_ingest_corpus_small_delta_overwrite_lands``
drives a PRODUCTION writer (``ingest_corpus``'s ``AgentSession`` update branch)
and fails on the pre-fix tree (the stored value is the pre-write vector) and
passes once the shared seam clears the property first.

*Mutations that make it fail again* — either one re-reddens the test:

* delete the clear guard from the writer (or from
  ``tortoise/vector_write.py``); or
* replace ``v1`` with a one-hot swap (Δ = 1.0) — which would then **pass**, and
  that is exactly the trap this module exists to avoid.

Hermetic: the session embedder is stubbed, the store is a per-test embedded
file. No provider keys, no network.
"""
from __future__ import annotations

import ast
import os
import random
from pathlib import Path

import pytest

DIM = 384

#: The server lane is the only lane where a bare overwrite is known to land.
#: Read the env inside a helper (not a module-level `_URI…` capture) so the
#: test-isolation lint (#221) is satisfied and the read stays at collect time.
def _server_uri_set() -> bool:
    return bool(os.environ.get("TORTOISE_DB_URI"))


requires_server_lane = pytest.mark.skipif(
    not _server_uri_set(), reason="needs a server FalkorDB lane (TORTOISE_DB_URI)")


def _base_vector() -> list[float]:
    """A realistic-looking embedding: small components in [-0.05, 0.05]."""
    rnd = random.Random(4520)
    return [rnd.uniform(-0.05, 0.05) for _ in range(DIM)]


class _SequentialSessionEmbedder:
    """Deterministic stub for ``compute_session_embedding``.

    Returns ``vectors[0]`` on the first call, ``vectors[1]`` on the second, and
    the last vector thereafter — so the second ingest of a changed file writes a
    *different*, small-Δ vector over the first.
    """

    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = [list(v) for v in vectors]
        self.calls = 0

    def __call__(self, *args, **kwargs) -> list[float]:
        idx = min(self.calls, len(self._vectors) - 1)
        self.calls += 1
        return list(self._vectors[idx])


def _read_event_embedding(graph, event_id: str) -> list[float] | None:
    row = graph.query(
        "MATCH (e:Event {eventId:$eid}) RETURN e.embedding",
        params={"eid": event_id},
    ).result_set
    return list(row[0][0]) if row and row[0][0] is not None else None


# ── acceptance: a realistic small-Δ overwrite must LAND on the embedded lane ──

@pytest.mark.embedded_only  # the defect reproduces only on redislite
def test_ingest_corpus_small_delta_overwrite_lands(tmp_path, monkeypatch):
    """A production vector OVERWRITE with Δ=0.01/component must land.

    RED on the pre-fix tree: ``ingest_corpus``'s update branch issues a bare
    ``SET e.embedding = … vecf32(…)``, the embedded engine discards it, and the
    node keeps the first ingest's vector while ``typeof`` still reads
    ``Vectorf32``. GREEN once the writer clears the property first.
    """
    import tortoise.session_indexer as si
    from tortoise.sdk import TortoiseSDK

    v0 = _base_vector()
    v1 = list(v0)
    v1[0] += 0.01  # realistic per-component delta — far below the ~1.0 threshold
    stub = _SequentialSessionEmbedder([v0, v1])
    monkeypatch.setattr(si, "compute_session_embedding", stub)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    session_file = corpus / "s.md"
    session_file.write_text(
        "---\nsessionId: seam4520\ntitle: Seam session\n---\nBody one\n")
    sdk = TortoiseSDK(str(tmp_path / "seam.db"))
    event_id = "session_seam4520"
    try:
        first = sdk.ingest_corpus(
            str(corpus), eventKind="AgentSession", extract_metadata=False)
        assert first.get("ingested", 0) == 1, first
        graph = sdk._get_proj().g
        stored0 = _read_event_embedding(graph, event_id)
        assert stored0 is not None, "the first ingest must store a vector"
        assert stored0[0] == pytest.approx(v0[0], abs=1e-4)

        # Change the file so its hash changes -> the UPDATE branch (an overwrite).
        session_file.write_text(
            "---\nsessionId: seam4520\ntitle: Seam session\n---\nBody two\n")
        second = sdk.ingest_corpus(
            str(corpus), eventKind="AgentSession", extract_metadata=False)
        assert second.get("updated", 0) == 1, second

        got = _read_event_embedding(graph, event_id)
        assert got is not None
        # typeof reads Vectorf32 even when the value is STALE — assert the value.
        assert got[0] == pytest.approx(v1[0], abs=1e-4), (
            "the small-Δ (0.01/component) vector OVERWRITE was DISCARDED: the "
            f"node still holds the PRE-write vector ({got[0]!r}), expected "
            f"{v1[0]!r} (#4520 — real embedder output always qualifies)")
    finally:
        sdk.close()


# ── the seam's own contract: both shapes land a realistic small-Δ overwrite ──

def _seed_probe_node(graph, node_id: str, label: str, vector: list[float]) -> None:
    graph.query(
        f"CREATE (n:{label} {{id:$id}}) SET n.embedding = vecf32($v)",
        params={"id": node_id, "v": vector})


def _read_probe_node(graph, node_id: str, label: str) -> list[float] | None:
    rows = graph.query(
        f"MATCH (n:{label} {{id:$id}}) RETURN n.embedding",
        params={"id": node_id}).result_set
    return list(rows[0][0]) if rows and rows[0][0] is not None else None


@pytest.mark.embedded_only
def test_seam_in_query_guard_lands_a_small_delta_overwrite(tmp_path):
    """`clear_then_set_prefix` + `set_embedding_clause` land Δ=0.02/component."""
    from tortoise.sdk import TortoiseSDK
    from tortoise.vector_write import clear_then_set_prefix, set_embedding_clause

    sdk = TortoiseSDK(str(tmp_path / "seam_guard.db"))
    try:
        graph = sdk._get_proj().g
        base = _base_vector()
        moved = list(base)
        moved[0] += 0.02
        label = "SeamGuardProbe"
        _seed_probe_node(graph, "in-query", label, base)
        graph.query(
            f"MATCH (n:{label} {{id:$id}}) " + clear_then_set_prefix("n")
            + "SET " + set_embedding_clause("n"),
            params={"id": "in-query", "embedding": moved})
        got = _read_probe_node(graph, "in-query", label)
        assert got is not None and got[0] == pytest.approx(moved[0], abs=1e-4)
        # and the guard must PRESERVE the stored vector on a null write
        graph.query(
            f"MATCH (n:{label} {{id:$id}}) " + clear_then_set_prefix("n")
            + "SET " + set_embedding_clause("n"),
            params={"id": "in-query", "embedding": None})
        kept = _read_probe_node(graph, "in-query", label)
        assert kept is not None and kept[0] == pytest.approx(moved[0], abs=1e-4)
    finally:
        sdk.close()


@pytest.mark.embedded_only
def test_seam_separate_query_guard_lands_a_small_delta_overwrite(tmp_path):
    """`clear_embedding` (the ON CREATE/ON MATCH shape) also lands Δ=0.02."""
    from tortoise.sdk import TortoiseSDK
    from tortoise.vector_write import clear_embedding, set_embedding_clause

    sdk = TortoiseSDK(str(tmp_path / "seam_sep.db"))
    try:
        graph = sdk._get_proj().g
        base = _base_vector()
        moved = list(base)
        moved[0] += 0.02
        label = "SeamSepProbe"
        _seed_probe_node(graph, "separate", label, base)
        clear_embedding(graph, f"(n:{label} {{id:$id}})", "n",
                        {"id": "separate"}, moved)
        graph.query(
            f"MATCH (n:{label} {{id:$id}}) SET " + set_embedding_clause("n"),
            params={"id": "separate", "embedding": moved})
        got = _read_probe_node(graph, "separate", label)
        assert got is not None and got[0] == pytest.approx(moved[0], abs=1e-4)
    finally:
        sdk.close()


# ── the start-up probe: it must detect the discard by VALUE, not by typeof ──

@pytest.mark.embedded_only
def test_probe_accepts_the_embedded_store_through_the_seam(tmp_path):
    """Embedded + the seam is TRUSTWORTHY — the probe must not refuse it.

    Gating on the RAW engine would refuse every embedded store (the a4 real
    lane's default target), so the probe checks the PRODUCT write path.
    """
    from battery.runner.retrieval_preflight import (
        require_vector_overwrite_integrity,
        vector_overwrite_available,
    )
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "probe_seam.db"))
    try:
        graph = sdk._get_proj().g
        available, reason = vector_overwrite_available(graph)
        assert available is True, (
            "the seam makes the embedded engine land a small-Δ overwrite, so "
            f"the probe must accept it (got: {reason})")
        require_vector_overwrite_integrity(graph)  # must not raise
        left = graph.query(
            "MATCH (n:_TortoiseVectorProbe) RETURN count(n)").result_set[0][0]
        assert left == 0
    finally:
        sdk.close()


@pytest.mark.embedded_only
def test_probe_refuses_when_the_write_path_discards(tmp_path, monkeypatch):
    """When the clear-then-set guard is ineffective, the probe refuses BY VALUE.

    Neutering the seam is exactly the "a writer bypassed it / the store cannot
    land a guarded write" case, so the probe must detect the stale value (not a
    ``typeof``) and refuse.
    """
    import tortoise.vector_write as vw
    from battery.runner.retrieval_preflight import (
        VectorOverwriteDiscarded,
        require_vector_overwrite_integrity,
        vector_overwrite_available,
    )
    from tortoise.sdk import TortoiseSDK

    monkeypatch.setattr(vw, "clear_then_set_prefix", lambda alias, expr="$embedding": "")
    sdk = TortoiseSDK(str(tmp_path / "probe_bad.db"))
    try:
        graph = sdk._get_proj().g
        available, reason = vector_overwrite_available(graph)
        assert available is False, "a bare overwrite discards on embedded"
        assert reason and "did not land" in reason
        with pytest.raises(VectorOverwriteDiscarded):
            require_vector_overwrite_integrity(graph)
        left = graph.query(
            "MATCH (n:_TortoiseVectorProbe) RETURN count(n)").result_set[0][0]
        assert left == 0
    finally:
        sdk.close()


@requires_server_lane
def test_probe_passes_on_the_server_lane(tmp_path):
    """A store that lands the overwrite is accepted (no false refusal)."""
    from battery.runner.retrieval_preflight import (
        require_vector_overwrite_integrity,
        vector_overwrite_available,
    )
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "probe_good.db"))
    try:
        graph = sdk._get_proj().g
        available, reason = vector_overwrite_available(graph)
        assert available is True, f"server lane must land the overwrite: {reason}"
        require_vector_overwrite_integrity(graph)  # must not raise
    finally:
        sdk.close()


# ── the migration is the seam's guarantee: the ENUMERATED writers import it ─

def test_every_known_vector_writer_uses_the_seam():
    """A regression net: the writers #4520 ENUMERATES route through the seam.

    The defect landed three separate times because each writer re-spelled the
    clause locally. This asserts each enumerated writer MODULE still IMPORTS the
    one seam, so dropping it in a future refactor fails here rather than
    silently restoring the silent discard.

    Scope is the ENUMERATED set below, not "every writer in the repo": the net
    cannot prove anything about an unenumerated file, and `update_point` /
    `update_entity` are a KNOWN, tracked gap (see `tortoise/vector_write.py`).

    This net is FILE-granular — it only asserts the module imports a seam name,
    so it cannot see a SECOND, un-seamed write inside a module it already
    passes. `graph-scripts/backfill_embeddings.py` has exactly that: its
    `_repair_legacy_event_embeddings` is a Δ = 0 self-recast deliberately out
    of scope. That blind spot is covered by
    `test_vector_write_scope_claim_is_not_absolute` below.
    """
    root = Path(__file__).resolve().parent.parent
    writers = {
        "tortoise/projection/entities.py": ("clear_then_set_prefix", "clear_embedding"),
        "tortoise/projection/__init__.py": ("clear_then_set_prefix",),
        "tortoise/session_indexer.py": ("clear_then_set_prefix",),
        "tortoise/sdk.py": ("clear_then_set_prefix", "clear_embedding"),
        "tortoise/hosted_backup.py": ("clear_then_set_prefix",),
        # `_backfill_graph` (the OVERWRITE writer) is seamed in this module.
        # The same module ALSO holds `_repair_legacy_event_embeddings`, a
        # Δ = 0 self-recast that is deliberately OUT of scope (the SET reads
        # the property a clear would remove) — see `tortoise/vector_write.py`.
        # Because this net is file-granular, that second write is invisible to
        # it; `test_vector_write_scope_claim_is_not_absolute` pins the scope.
        "graph-scripts/backfill_embeddings.py": ("clear_then_set_prefix",),
        "battery/runner/setup.py": ("clear_then_set_prefix",),
        "battery/runner/retrieval_preflight.py": ("clear_then_set_prefix",),
        # #4520 cycle-3: the eval seeder writes DIFFERENT vectors for the SAME
        # ids on a re-seed, so it is an overwrite the first migration missed.
        "benchmarks/synthetic_corpus.py": ("clear_then_set_prefix",),
        "tools/ask_spotcheck.py": ("clear_then_set_prefix",),
        # #4520 review P2-2: a runbook diagnostic that set `p.embedding`
        # directly. Latent (it seeds `embed=False` first, so the write is a
        # first write, not an overwrite), but it was an undeclared writer
        # outside this net — routed through the seam so the net is complete.
        "docs/runbook/w6c_gold_rank_diagnostic.py": ("clear_then_set_prefix",),
    }
    for rel, markers in writers.items():
        source = (root / rel).read_text()
        # Assert the IMPORT FORM from the AST, not a substring: an unanchored
        # regex or `marker in source` is satisfied by a COMMENT mentioning the
        # seam, so a refactor that deletes the real import but leaves a note
        # would still pass. Parse the module and read the bindings it actually
        # imports from `...vector_write`.
        imported = _seam_import_names(source)
        assert imported, (
            f"{rel} no longer IMPORTS the shared vector-write seam — a vector "
            f"writer must go through tortoise/vector_write.py (#4520)")
        for marker in markers:
            assert marker in imported, (
                f"{rel} imports the seam but not {marker!r} — the guarded "
                f"form was re-spelled locally (#4520); imported={sorted(imported)}")


def _seam_import_names(source: str) -> set[str]:
    """Names imported from a ``…vector_write`` module in ``source``.

    An AST walk, so a comment or a docstring that mentions the seam cannot
    satisfy the check — only a real ``from …vector_write import …`` node counts.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.ImportFrom)
                and (node.module or "").endswith("vector_write")):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def test_vector_write_scope_claim_is_not_absolute():
    """The seam's scope claim must NOT cover every writer (#4520 P2-2).

    ``graph-scripts/backfill_embeddings.py`` contains a SECOND write the
    file-granular inventory above cannot see: ``_repair_legacy_event_embeddings``
    issues ``SET n.embedding = vecf32(n.embedding)`` — a Δ = 0 SELF-RECAST
    (source == target), deliberately OUT of the seam's scope. It must not be
    routed through the guard: the SET reads the same property the clear just
    REMOVED, so ``clear_then_set_prefix`` would destroy the value.

    The module docstring is where that scope is stated, so an absolute "Every
    vector writer KNOWN TO #4520" claim would silently read as covering that
    un-seamed write. This pins the narrowed claim: it fails if the claim goes
    absolute again, or if the out-of-scope self-recast stops being named.
    """
    root = Path(__file__).resolve().parent.parent
    source = (root / "tortoise/vector_write.py").read_text()
    assert "Every vector writer KNOWN TO #4520" not in source, (
        "the seam's scope claim went absolute again — it would read as "
        "covering `_repair_legacy_event_embeddings`, a Δ = 0 self-recast in "
        "graph-scripts/backfill_embeddings.py that is deliberately out of "
        "scope (#4520 P2-2)")
    assert "_repair_legacy_event_embeddings" in source, (
        "the out-of-scope Δ = 0 self-recast is no longer named in the seam's "
        "scope statement, so the file-granular inventory's blind spot is "
        "undocumented (#4520 P2-2)")
    assert "self-recast" in source.lower(), (
        "the Δ = 0 self-recast category is no longer distinguished from the "
        "overwrite writers the seam covers (#4520 P2-2)")

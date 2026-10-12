"""#4520 residual — the DERIVED content-edit vector must land as a ``Vectorf32``.

THE DEFECT
----------
``TortoiseSDK.update_point(id, content=...)`` re-encodes the embedding from the
new content (#4208) and then writes it through the bare ``SET n += $props``
merge. A Python float list is a plain Cypher ``List`` — no ``vecf32`` cast and
no clear — so ``typeof(n.embedding)`` flips ``Vectorf32`` -> ``List`` in ONE
content edit. ``search_engine``'s per-row dense leg
(``vec.euclideanDistance(n.embedding, ...)``) then raises::

    Type mismatch: expected Null or VectorF32 but was List

for the WHOLE ``MATCH``, so ONE edited Point degrades the entire Point dense leg
to FTS-only recall. This is #4521's class, but on the **LIVE** path —
``update_point`` is the writer behind the MCP ``tortoise_update`` tool and the
hosted update route — where #4521 / PR #7740 fixed only the REPLAY's
``_revise_point``.

WHY THE VECTORS ARE SMALL-DELTA
-------------------------------
The fix needs BOTH halves of the shared clear-then-set seam
(``tortoise/vector_write.py``): the ``vecf32()`` cast (a plain list is
unreadable by the dense leg) AND the clear (a small-Delta ``vecf32`` OVERWRITE
is silently DISCARDED by the embedded engine — #4520's own, previously-fixed
defect). The embedder here returns a nearby vector for the edited text (every
component moves by 0.01), which is what real embedder output does. A synthetic
one-hot probe (Delta = 1.0) LANDS even on the broken engine and hides BOTH
halves — that is the trap recorded on #4520. The value assertion below is
therefore load-bearing: a ``vecf32``-only fix satisfies the ``typeof`` and
dense-read assertions while leaving the PRE-edit vector stored.

RED before / GREEN after
------------------------
``test_content_edit_leaves_a_readable_typed_vector`` fails on the pre-fix tree
(``typeof`` reads ``List`` and the dense leg raises) and passes once the derived
embedding is routed through the seam.

*Mutations that re-redden it* (each must be reverted to pass):

* restore the bare ``SET n += $props`` for the derived embedding — ``typeof``
  flips to ``List`` again and the dense read raises; or
* drop the clear guard and keep only ``vecf32()`` — the small-Delta overwrite is
  silently DISCARDED and the stored value stays the PRE-edit vector.

Hermetic: the embedder is stubbed, the store is a per-test embedded file. No
provider keys, no network.

Run (embedded lane)::

    env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \\
        uv run pytest tests/test_update_point_derived_embedding_4520.py -q
"""
from __future__ import annotations

import math
from unittest import mock

import pytest

from tortoise.embeddings import EMBEDDING_DIM
from tortoise.sdk import TortoiseSDK

# The defect (a plain-list embedding write) and its fix (the clear-then-set
# seam) are embedded-engine specific — the server lane lands the plain-list
# write too, so the module is module-level `embedded_only` and MUST be
# registered in config/ci-surfaces.yml `carve_out:` AND in
# tests/_embedded.py TEST_NO_REDIRECT_STEMS. Without that registration a
# URI-set selection collects the file and SKIPS every case via the marker hook
# — a permanently green, permanently unexecuted gate (the #4047 shape).
pytestmark = pytest.mark.embedded_only

_EMBED_PATCH = "tortoise.embeddings.compute_embedding"
DIM = EMBEDDING_DIM if isinstance(EMBEDDING_DIM, int) else 384

#: The per-component move the embedded engine DISCARDS on a bare ``vecf32``
#: overwrite (the #4520 defect the seam's clear half exists for).
_DELTA = 0.01

_ORIGINAL = "the kettle boiled dry on the stove"
_REPLACEMENT = "the kettle boiled dry on the hob"


def _nearby_vectors() -> tuple[list[float], list[float]]:
    """(v_before, v_after) — realistic and small-Delta on EVERY component.

    A smooth, embedder-shaped base: no one-hot axis, because a one-hot probe's
    Delta = 1.0 lands even on the broken engine and hides the defect (#4520).
    """
    before = [math.sin(i * 0.017) * 0.4 + math.cos(i * 0.011) * 0.2
              for i in range(DIM)]
    after = [component + _DELTA for component in before]
    return before, after


class _ContentEmbedder:
    """Deterministic per-text embedder stub (exact float64 values)."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def __call__(self, text: str, *_a, **_k) -> list[float]:
        self.calls.append(text)
        return list(self._mapping[text])


def _typeof(sdk: TortoiseSDK, pid: str) -> str | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN typeof(n.embedding)",
        params={"id": pid}).result_set
    return rows[0][0] if rows else None


def _stored(sdk: TortoiseSDK, pid: str) -> list[float] | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.embedding",
        params={"id": pid}).result_set
    return None if not rows or rows[0][0] is None else list(rows[0][0])


def _dense_distance(sdk: TortoiseSDK, pid: str, query: list[float]):
    """The dense leg's own per-row expression — this is what RAISES on a List."""
    return sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) WITH vecf32($q) AS _qv, n "
        "RETURN vec.euclideanDistance(n.embedding, _qv)",
        params={"id": pid, "q": query}).result_set


# ── acceptance: a content edit must leave a readable, TYPED, FRESH vector ────

def test_content_edit_leaves_a_readable_typed_vector(tmp_path):
    """RED before: ``typeof`` flips to ``List`` and the dense read raises."""
    before, after = _nearby_vectors()
    assert max(abs(a - b) for a, b in zip(before, after, strict=True)) < 1.0, (
        "premise: the vectors must be small-Delta (a one-hot probe hides the "
        "defect — #4520)")

    embedder = _ContentEmbedder({_ORIGINAL: before, _REPLACEMENT: after})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "derived.db"))
        try:
            pid = sdk.create_point("statement", _ORIGINAL)["id"]
            assert _typeof(sdk, pid) == "Vectorf32", (
                "premise: create_point stores a typed vector")

            sdk.update_point(pid, content=_REPLACEMENT)  # THE live content edit

            # (1) the type must not flip to a plain Cypher List
            assert _typeof(sdk, pid) == "Vectorf32", (
                "ONE content edit stored the re-derived embedding as a plain "
                "`List` — the dense leg raises 'Type mismatch: expected Null "
                "or VectorF32 but was List' for the WHOLE MATCH (#4520 "
                "residual, the #4521 class on the live path)")

            # (2) the dense read must not raise, and must see the Point
            _dense_distance(sdk, pid, after)
            from tortoise.search_engine import run_vector_query
            hits = [row[0] for row in run_vector_query(
                sdk._get_proj().g, after, is_embedded=True)]
            assert pid in hits, (
                "the edited Point fell out of the dense leg — a plain-list "
                "embedding poisons the whole vector query (#4520 residual)")

            # (3) the small-Delta overwrite must LAND — this is the CLEAR half
            #     of the seam, and the reason a one-hot probe is forbidden
            stored = _stored(sdk, pid)
            assert stored is not None, "the content edit wiped the vector"
            assert max(abs(a - b) for a, b in zip(stored, after, strict=True)) < 1e-4, (
                "the small-Delta (0.01/component) re-derived vector was "
                "DISCARDED — `vecf32()` alone is not enough on the embedded "
                "engine; the property must be CLEARED first (#4520)")
        finally:
            sdk.close()


def test_the_consolidated_update_route_is_fixed_too(tmp_path):
    """``update(id, content=...)`` — the writer behind MCP ``tortoise_update``.

    ``TortoiseSDK.update`` resolves a Point to ``update_point`` and the MCP tool
    ``tortoise_update`` calls ``update``, so this pins the reachable surface end
    to end.
    """
    before, after = _nearby_vectors()
    embedder = _ContentEmbedder({_ORIGINAL: before, _REPLACEMENT: after})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "consolidated.db"))
        try:
            pid = sdk.create_point("statement", _ORIGINAL)["id"]
            sdk.update(pid, content=_REPLACEMENT)
            assert _typeof(sdk, pid) == "Vectorf32"
            stored = _stored(sdk, pid)
            assert stored is not None
            assert max(abs(a - b) for a, b in zip(stored, after, strict=True)) < 1e-4
        finally:
            sdk.close()


# ── every update_point write arm carries the seam (the WHERE shapes too) ─────

def _mint_object_node(sdk: TortoiseSDK, pid: str) -> None:
    """Add the ``:Object`` label — a REAL multi-label node.

    NOTE: ``create_operator`` mints ``(:Point {is_operator:true})``, a SINGLE
    label, so ``update_point`` on an operator takes the plain-Point arm. The
    ``:Point:Object`` arms are reached by a node carrying BOTH labels, which is
    what this mints (the repo's own tests do the same).
    """
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
    assert sdk._get_proj().g.query(
        "MATCH (n:Point:Object {id:$id}) RETURN count(n) > 0",
        params={"id": pid}).result_set[0][0] is True, (
        "premise: the node must carry BOTH labels to reach the :Object arms")


def test_the_object_arm_content_edit_is_typed(tmp_path):
    """The ``:Point:Object`` no-status arm writes the seam.

    A distinct query shape (``SET n += $props, n.version = …``) — pinned so a
    refactor cannot drop the seam from it while the plain-Point arm stays green.
    """
    before, after = _nearby_vectors()
    embedder = _ContentEmbedder({_ORIGINAL: before, _REPLACEMENT: after})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "object_arm.db"))
        try:
            pid = sdk.create_point("statement", _ORIGINAL)["id"]
            _mint_object_node(sdk, pid)
            sdk.update_point(pid, content=_REPLACEMENT)
            assert _typeof(sdk, pid) == "Vectorf32"
            stored = _stored(sdk, pid)
            assert stored is not None
            assert max(abs(x - y) for x, y in zip(stored, after, strict=True)) < 1e-4
        finally:
            sdk.close()


def test_the_object_status_promote_arm_content_edit_is_typed(tmp_path):
    """The ``:Point:Object`` draft->live arm splices the seam after its WHERE.

    The clear guard must sit AFTER the promote gate, so a refused transition
    cannot remove the embedding — and the Cypher must stay valid with the
    ``FOREACH … REMOVE`` between the two clauses.
    """
    before, after = _nearby_vectors()
    embedder = _ContentEmbedder({_ORIGINAL: before, _REPLACEMENT: after})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "object_status_arm.db"))
        try:
            pid = sdk.create_point(
                "statement", _ORIGINAL, status="draft")["id"]
            _mint_object_node(sdk, pid)
            sdk.update_point(pid, status="live", content=_REPLACEMENT)
            assert _typeof(sdk, pid) == "Vectorf32"
            stored = _stored(sdk, pid)
            assert stored is not None
            assert max(abs(x - y) for x, y in zip(stored, after, strict=True)) < 1e-4
        finally:
            sdk.close()


def test_the_status_promote_arm_content_edit_is_typed(tmp_path):
    """The plain-Point draft->live arm splices the seam between WHERE and SET.

    The clear guard must sit AFTER the promote gate, so a refused transition
    cannot remove the embedding — and the Cypher must still be valid with the
    ``FOREACH … REMOVE`` between the two clauses.
    """
    before, after = _nearby_vectors()
    embedder = _ContentEmbedder({_ORIGINAL: before, _REPLACEMENT: after})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "status_arm.db"))
        try:
            pid = sdk.create_point(
                "statement", _ORIGINAL, status="draft")["id"]
            sdk.update_point(pid, status="live", content=_REPLACEMENT)
            assert _typeof(sdk, pid) == "Vectorf32"
            stored = _stored(sdk, pid)
            assert stored is not None
            assert max(abs(x - y) for x, y in zip(stored, after, strict=True)) < 1e-4
        finally:
            sdk.close()


# ── scope guard: the fix routes ONLY the DERIVED vector ──────────────────────

def test_a_caller_supplied_vector_stays_verbatim(tmp_path):
    """A CALLER vector must NOT be narrowed by the fix.

    A caller-supplied embedding is stored VERBATIM by a recorded decision
    (PR #3018's TYPE CHANGE GUARD: a float64 list must not be silently
    narrowed through ``vecf32``). Routing it through ``set_embedding_clause``
    would narrow 0.1234567890123 -> 0.12345679..., which is a reversal of that
    decision, not a bug fix — so the fix must route the DERIVED branch ONLY.
    """
    caller_value = 0.1234567890123
    caller_vec = [caller_value] * DIM
    embedder = _ContentEmbedder(
        {_ORIGINAL: [0.0] * DIM, _REPLACEMENT: [0.0] * DIM})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "caller.db"))
        try:
            pid = sdk.create_point("statement", _ORIGINAL)["id"]
            sdk.update_point(pid, embedding=caller_vec)
            stored = _stored(sdk, pid)
            assert stored is not None
            assert stored[0] == caller_value, (
                "the caller's float64 was silently narrowed to float32 — the "
                "recorded verbatim decision (PR #3018) was reversed")
        finally:
            sdk.close()

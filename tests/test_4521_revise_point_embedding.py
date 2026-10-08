"""#4521 — ``_revise_point`` wrote a PLAIN-LIST embedding, killing the dense leg.

**The defect.** ``FalkorProjection._revise_point`` appended
``"n.embedding = $embedding"`` with **no ``vecf32()`` cast**, while every other
Point embedding writer casts. ``_revise_point`` is reached by ``apply()`` and by
``rebuild_all`` pass-1b, so a ``PointRevised`` that is the LAST writer left the
node's ``embedding`` as a Cypher ``List``.

**Why that is worse than one bad node.** ``tortoise/search_engine.py`` runs
``vec.euclideanDistance(n.embedding, _qv)`` per row. A ``List`` raises
``Type mismatch: expected Null or Vectorf32 but was List``, and that aborts the
WHOLE query — so one mis-typed node degraded the entire Point dense leg to
FTS-only recall for that graph, not just its own row. This is the "#244 poison"
the Event path already fixed; the Point path was never aligned.

**Both writers, one clause.** ``apply()`` and ``rebuild_all`` reach the field
through the SAME ``_revise_point`` clause, so the fix is one line — but the
issue's acceptance asks for the guard on **both** paths, because the two used to
disagree and the parity tests compare *values* and never *types*.

**Shape is not enough.** A guard that only pins ``typeof`` would pass on a
stale vector: ``#4520`` shows the embedded engine can DISCARD a ``vecf32``
overwrite, leaving the type correct and the value from the PREVIOUS write. So
each path also asserts the stored vector IS the revision's and is NOT the
original's — the silent-loss half.

**The guard is embedded-only.** ``falkordblite``/``redislite`` is the default
self-hosted backend, and it is where the observed read-back is unavailable to
the docker lane. The module carries ``pytestmark = pytest.mark.embedded_only``
and is registered in ``config/ci-surfaces.yml`` (``carve_out``) and
``tests/_embedded.py`` (``TEST_NO_REDIRECT_STEMS``); a guard that never runs is
worse than none (#4047).

**Mutation-proven.** Reverting the cast on that one clause reddens both tests —
a guard never watched fail is not evidence.

**Hermetic.** A real embedded store on its own tmp path, plus a deterministic
384-dim unit-norm encoder double keyed on the TEXT (so the value assertions do
not depend on call order) installed over the ``compute_embedding`` seam. No
model, no network, no container.
"""
from __future__ import annotations

import hashlib
import json
import math
from unittest import mock

import pytest

from tests._embedded import fresh_embedded_proj
from tortoise.embeddings import EMBEDDING_DIM
from tortoise.sdk import TortoiseSDK

pytestmark = pytest.mark.embedded_only

_POINT_ID = "p-4521"
_ORIGINAL = "original content"
_REVISED = "revised content, materially different from the original"

_EMBED_PATCH = "tortoise.embeddings.compute_embedding"


def _fake_embed(text: str, max_tokens: int = 512):
    """Deterministic 384-dim unit-norm vector, keyed on the TEXT.

    Keyed on text rather than on call order so the value assertions below mean
    what they say: the revision's vector is a property of the revision's
    content, not of how many encodes happened first. Unit-norm and 384-wide so
    the store's width guard and the HNSW index accept it — a short vector would
    be dropped and the guard would go vacuous.
    """
    raw = hashlib.sha256(f"tortoise-4521::{text}".encode()).digest()
    buf = (raw * (EMBEDDING_DIM // len(raw) + 1))[:EMBEDDING_DIM]
    vec = [(byte / 255.0) - 0.5 for byte in buf]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _close(a, b, tol: float = 1e-3) -> bool:
    """Compare vectors within the float32 narrowing the store applies."""
    a, b = list(a), list(b)
    return len(a) == len(b) and max(
        abs(x - y) for x, y in zip(a, b, strict=True)) < tol


def _assert_revised_vector(proj, path: str) -> None:
    """The revised Point's stored embedding: right type, right VALUE, usable."""
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN typeof(n.embedding), n.embedding",
        params={"id": _POINT_ID}).result_set
    assert rows, f"{path}: no Point matched — the guard cannot observe"

    kind, stored = rows[0]
    assert stored is not None, (
        f"{path}: _revise_point left the embedding NULL — the revision wrote "
        "nothing at all (a different failure than the list-shape one)")
    assert kind == "Vectorf32", (
        f"{path}: _revise_point left the embedding as {kind!r}, not Vectorf32. "
        "Every other Point embedding writer casts with vecf32(); a plain List "
        "makes vec.euclideanDistance raise, which aborts the WHOLE dense leg "
        "for the graph, not just this node (#4521).")

    # VALUE, not just shape (#4520: an overwrite can be silently DISCARDED,
    # leaving typeof correct and the vector from the previous write).
    assert _close(stored, _fake_embed(_REVISED)), (
        f"{path}: the stored embedding is not the REVISION's vector — the "
        "revised Point is carrying a stale vector (#4520/#4521).")
    assert not _close(stored, _fake_embed(_ORIGINAL)), (
        f"{path}: the stored embedding is still the PRE-write vector — the "
        "vecf32 overwrite was silently discarded (#4520).")

    # The CONSEQUENCE: the query the search engine's per-row dense leg makes.
    try:
        dist = proj.g.query(
            "MATCH (n:Point {id:$id}) "
            "RETURN vec.euclideanDistance(n.embedding, vecf32($q))",
            params={"id": _POINT_ID, "q": _fake_embed(_REVISED)}).result_set
    except Exception as exc:
        raise AssertionError(
            f"{path}: the Point dense leg is BROKEN — vec.euclideanDistance "
            f"over the stored embedding raised {type(exc).__name__}: {exc}"
        ) from exc
    assert dist and dist[0][0] is not None, (
        f"{path}: vec.euclideanDistance returned no distance for the revised "
        "point")


def _records() -> list[dict]:
    """A journal whose LAST writer of the embedding is the revision."""
    return [
        {"event_id": "e-4521-1", "ts": "2026-09-18T00:00:00+00:00",
         "type": "PointAdded", "initiated_by": "raw-producer",
         "projection_version": 2,
         "point": {"id": _POINT_ID, "content": _ORIGINAL,
                   "pointKind": "statement"}},
        {"event_id": "e-4521-2", "ts": "2026-09-18T00:00:01+00:00",
         "type": "PointRevised", "initiated_by": "raw-producer",
         "projection_version": 2, "id": _POINT_ID, "new_content": _REVISED},
    ]


def test_apply_revise_leaves_a_vectorf32_not_a_list(tmp_path):
    """Live ``apply()``: a ``PointRevised`` last writer must leave Vectorf32."""
    with mock.patch(_EMBED_PATCH, _fake_embed), \
            fresh_embedded_proj(tmp_path) as proj:
        for rec in _records():
            proj.apply(rec)
        _assert_revised_vector(proj, "apply()")


def test_rebuild_all_revise_leaves_a_vectorf32_not_a_list(tmp_path):
    """``rebuild_all`` pass-1b reaches the field via the SAME clause.

    The journal replays ``PointAdded`` then ``PointRevised``, so pass-1b's
    revise is the last writer — the shape the issue's evidence table recorded
    on both sides.
    """
    events = tmp_path / "events"
    events.mkdir()
    (events / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in _records()))

    with mock.patch(_EMBED_PATCH, _fake_embed):
        sdk = TortoiseSDK(str(tmp_path / "revise4521.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            _assert_revised_vector(sdk._get_proj(), "rebuild_all")
        finally:
            sdk.close()

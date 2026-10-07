"""#5148: a WRITE must not depend on the embeddings SEAM being importable at its call site.

`tortoise.embeddings` is normally imported transitively at package-import time
(`tortoise/sdk.py` -> `cross_lens.py`), so this is **not** a missing-dependency
install. What it covers is an import hook / broken import environment that fails
**at the call site**, plus the principle that a write must never depend on an
import succeeding.

Two of these branches are on the **replay** path, where a raise is worse than a
lost write: `_upsert_point_props` reads them outside any `try`, so an exception
tears every replayed event, `recover_from_log` counts `applied == 0`, and the DB
is refused with *"replay produced an empty graph"* — the #5119 failure shape,
re-entered through an advisory check.

Every test here pins a branch that the cycle-2 review of #5154 found was guarded
but **unpinned**: the guards were verified by hand and nothing would have caught
their removal.
"""
from __future__ import annotations

import builtins
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI, provenance  # noqa: E402, RUF100
from tortoise.log import EventLog  # noqa: E402, RUF100


class _UnimportableSeam:
    """Raise ImportError for any import whose name contains 'embeddings'.

    The same condition `tests/test_extractor.py::test_mock_extractor_multi_source_fallback`
    installs, factored out so the other writers can be pinned against it.
    """

    def __enter__(self):
        self._orig = builtins.__import__

        def fake(name, *args, **kwargs):
            if "embeddings" in name:
                raise ImportError("unreachable seam (test)")
            return self._orig(name, *args, **kwargs)

        builtins.__import__ = fake
        return self

    def __exit__(self, *exc):
        builtins.__import__ = self._orig
        return False


def _api():
    log = EventLog(os.path.join(tempfile.mkdtemp(prefix="tortoise_5148_"),
                                "events.jsonl"))
    return EventAPI(log, initiated_by="extractor", agent_id="test"), log


# ── the two REPLAY-path guards: a raise here refuses the whole rebuild ──

def test_record_embedding_identity_is_fail_soft():
    """The replay's ADVISORY identity check must never abort a replay.

    It runs once per journalled vector inside `_upsert_point_props`; letting it
    raise aborted every event and refused the DB (#5119's shape). With no
    configured identity there is nothing to compare against, so silence is the
    correct degradation.
    """
    from tortoise.projection.entities import _record_embedding_identity

    p = {"id": "p1", "embedding": [1.0, 2.0], "embedding_model": "some-model"}
    with _UnimportableSeam():
        _record_embedding_identity(p, set())  # must not raise


def test_required_embedding_dim_is_fail_soft():
    """An unimportable embeddings module must not make the width guard raise.

    `_upsert_point_props` reads this on the REPLAY path OUTSIDE any `try`, so a
    raise is the #5119 shape. The degraded answer is the property's own
    documented one: "any width" (the brute-force lane).
    """
    from tortoise.projection import FalkorProjection

    proj = object.__new__(FalkorProjection)

    # A store WITH an index reaches the import branch this guard protects.
    proj._vector_index_api = "cypher"
    with _UnimportableSeam():
        assert proj.required_embedding_dim is None

    # No index: the documented any-width answer, import irrelevant.
    proj._vector_index_api = None
    assert proj.required_embedding_dim is None


# ── the two WRITE-path guards ──

def test_add_point_caller_vector_survives_unreachable_seam():
    """`add_point(embedding=...)` must survive an unimportable seam.

    This pins the GUARD, not the ASSIGNMENT ORDER: `p.update(fields)` has
    already put the key in the payload before this branch runs, so presence is
    owned either way and moving the explicit assignment would not change the
    outcome. What this catches is the unguarded import raising out of the write.
    """
    api, log = _api()
    with _UnimportableSeam():
        api.add_point("content", provenance("test", [0, 7], "content"),
                      embedding=[0.25, 0.5])

    recs = [e for e in log.read_all() if e.get("type") == "PointAdded"]
    assert recs, "the point must still reach the journal"
    assert "embedding" in recs[0]["point"], (
        "PRESENCE IS OWNERSHIP: the key must be present even when the seam is "
        "unreachable, or the replay would recompute a vector the live node "
        "does not have")
    assert recs[0]["point"]["embedding"] == [0.25, 0.5]


def test_point_without_caller_vector_survives_unreachable_seam():
    """The `_point` guard itself: no vector is computable, so the key is an owned None."""
    api, log = _api()
    with _UnimportableSeam():
        api.add_point("content", provenance("test", [0, 7], "content"))

    recs = [e for e in log.read_all() if e.get("type") == "PointAdded"]
    assert recs, "the point must still reach the journal"
    assert "embedding" in recs[0]["point"]
    assert recs[0]["point"]["embedding"] is None


@pytest.mark.embedded_only
def test_sdk_emit_event_survives_unreachable_seam(tmp_path):
    """The PRIMARY write path must not lose its journal record.

    `_emit_event` imports the seam AFTER the graph CREATE, so on base an
    unreachable seam raised `ImportError` for a write that had SUCCEEDED and
    then skipped `log.append` — the point existed and the journal was empty.
    """
    from tortoise.sdk import TortoiseSDK

    events = str(tmp_path / "events.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "test.db"), event_log_path=events)
    try:
        with _UnimportableSeam():
            sdk.create_point("observation", "log-0")

        with open(events, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
        assert lines, ("the journal record must survive an unimportable seam — "
                       "the graph write already happened")
        assert '"embedding"' in lines[0], (
            "PRESENCE IS OWNERSHIP: `setdefault` runs before the seam import, "
            "so the key must be in the record")
    finally:
        sdk.close()

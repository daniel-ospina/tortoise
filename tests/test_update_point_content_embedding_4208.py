"""#4208 — a content edit must recompute the DERIVED embedding, not keep a stale one.

THE DEFECT THIS PINS
--------------------
``TortoiseSDK.update_point(id, content=...)`` recomputed the derived
``content_hash`` (#1904) but never touched the node's ``embedding``. So a
content edit left the node holding content B with ``vec(A)`` — the dense
retrieval leg then ranked it by text no longer on the node — and live diverged
from a rebuild, because the replay's ``_revise_point`` DOES re-encode from
``new_content``. Same "stored vector disagrees with stored content" class as
#4194 (the capture turn-write path) and #4206 (the apply/replay path); this is
the LIVE writer half.

THE FIX
-------
When ``content`` is in the props and the caller supplied NO ``embedding``, the
writer re-encodes from the new content in the SAME write, through
``encode_for_store`` (the store-width seam #4194/#4280), and writes the result:
a fresh vector, or a WIPE (``None``) when the embedder is unavailable — never
the pre-edit vector (#19). The re-encoded vector is DERIVED, so it is stripped
from the ``PointRevised`` record exactly like ``content_hash``: the replay
re-encodes from the content, so a journalled copy would be dead weight and a
false presence-is-ownership claim under #5004.

A CALLER-supplied vector is deliberately left alone — that write is the
caller's, and the record/replay treatment of it on this path is the separate
declared gap #5046 (see ``test_journal_embedding_5004.py``).

Run (docker lane)::

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
        uv run pytest tests/test_update_point_content_embedding_4208.py -q
"""
from __future__ import annotations

import hashlib
import json
from unittest import mock

import pytest

from tortoise.embeddings import EMBEDDING_DIM
from tortoise.sdk import TortoiseSDK

_EMBED_PATCH = "tortoise.embeddings.compute_embedding"
_DIM = EMBEDDING_DIM if isinstance(EMBEDDING_DIM, int) else 384


def _embed_a(text: str, max_tokens: int = 512):
    """Deterministic, content-dependent vector (component 0 = text length)."""
    return [float(len(text))] + [0.25] * (_DIM - 1)


@pytest.fixture
def sup(tmp_path):
    """(events_dir, sdk) with the JSONL journal wired and a deterministic embedder."""
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "graph.db"),
                          event_log_path=str(events / "events.jsonl"))
        yield events, sdk
    sdk.close()


def _events(events_dir) -> list[dict]:
    out = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _vector(sdk: TortoiseSDK, pid: str):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.embedding",
        params={"id": pid},
    ).result_set
    assert rows, f"point {pid} missing from the graph"
    return None if rows[0][0] is None else list(rows[0][0])


# ── the core regression ──────────────────────────────────────────────────

def test_content_edit_recomputes_the_derived_vector(sup):
    """FAILS BEFORE: the node keeps ``vec('original')`` beside content
    ``'replacement'`` (the dense leg ranks it by text no longer on it)."""
    _events_dir, sdk = sup
    pid = sdk.create_point("statement", "original").get("id")
    before = _vector(sdk, pid)
    assert before == _embed_a("original")

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update_point(pid, content="replacement")

    after = _vector(sdk, pid)
    assert after != before, "the stale pre-edit vector survived the content edit"
    assert after == _embed_a("replacement"), (
        "the vector must be re-encoded from the NEW content"
    )
    # ...and it moves together with the already-recomputed derived hash (#1904).
    assert sdk.get_point(pid)["content_hash"] == hashlib.sha256(
        b"replacement").hexdigest()


def test_the_revised_vector_is_the_revise_replays_value(sup):
    """The live vector and the rebuild's re-encoded vector agree.

    The rebuild path (`_revise_point`) re-encodes from `new_content`; before
    this fix the live node held the creation vector, so live != rebuild.
    """
    events, sdk = sup
    pid = sdk.create_point("statement", "original").get("id")
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update_point(pid, content="replacement")
    live = _vector(sdk, pid)

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk._get_proj().rebuild_all(str(events))

    assert _vector(sdk, pid) == live, "live and rebuild disagree after a content edit"


# ── the degraded-embedder contract ───────────────────────────────────────

def test_content_edit_with_the_embedder_down_wipes_the_stale_vector(sup):
    """No crash, and no stale vector for the OLD text left beside the new.

    A vector for different text is worse than none: the writer must WIPE it
    (`None`), mirroring `_revise_point`'s own `except Exception: … = None`
    (#19), rather than preserving `vec('original')`.
    """
    _events_dir, sdk = sup
    pid = sdk.create_point("statement", "original").get("id")
    assert _vector(sdk, pid) is not None

    def _down(*_a, **_k):
        raise RuntimeError("embedder unavailable (simulated)")

    with mock.patch(_EMBED_PATCH, _down):
        sdk.update_point(pid, content="replacement")  # must not raise

    assert _vector(sdk, pid) is None, (
        "an unavailable embedder must fail CLOSED (wipe), never keep vec(old)"
    )
    assert sdk.get_point(pid)["content"] == "replacement"


# ── the record shape (derived, like content_hash) ────────────────────────

def test_the_derived_vector_is_not_journalled(sup):
    """The re-encoded vector is DERIVED — the record carries content only.

    Mirrors the `content_hash` strip (#1904): the replay's `_revise_point`
    re-encodes from `new_content`, so a copy on the record is dead weight, and
    #5004's presence-is-ownership rule would read the key as a producer claim
    the replay does not honour. (A CALLER-supplied vector still rides the
    record — see `test_journal_embedding_5004.py`.)
    """
    events, sdk = sup
    pid = sdk.create_point("statement", "original").get("id")
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update_point(pid, content="replacement")

    revises = [e for e in _events(events) if e.get("type") == "PointRevised"]
    assert revises, "expected a PointRevised record"
    for e in revises:
        assert "embedding" not in e
        assert "embedding" not in (e.get("point") or {})
        assert "content_hash" not in e
        assert e["new_content"] == "replacement"


# ── the live/replay consistency reference stays green ────────────────────

def test_a_healthy_content_edit_is_not_a_consistency_divergence(sup):
    """`check_consistency` must not read the fix as a divergence.

    The journal fold cannot re-encode, so its reference must not assert the
    pre-edit creation vector against the graph's re-encoded one — the checker's
    own arm declares a graph-only vector faithful for the recompute path. This
    is the companion to the `consistency.py` fold change.
    """
    events, sdk = sup
    pid = sdk.create_point("statement", "original").get("id")
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update_point(pid, content="replacement")

    from tortoise.consistency import check_consistency
    r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert r["ok"] is True, r["divergent_points"]
    assert r["divergence"] is None


# ── scope guard: a caller-supplied vector is NOT recomputed ──────────────

def test_a_caller_supplied_vector_is_not_recomputed(sup):
    """The caller owns an explicit vector; #4208 must not override it.

    `update_point(id, embedding=…)` (with or without a content edit) takes the
    caller's write. Its record/replay treatment on the revise path is the
    separate declared gap #5046 — widening it here would reverse a recorded
    decision.
    """
    _events_dir, sdk = sup
    pid = sdk.create_point("statement", "original").get("id")
    caller_vec = [0.2] * _DIM
    sdk.update_point(pid, embedding=caller_vec)
    assert _vector(sdk, pid) == caller_vec


# ── reachability: the consolidated `update()` routes through the same writer ─

def test_the_consolidated_update_route_recomputes_too(sup):
    """`update(id, content=…)` — the MCP `tortoise_update` behind it — is fixed.

    Both `tortoise_update_point` and `tortoise_update` land on this writer, so
    one test on the consolidated entry point pins the reachable surface.
    """
    _events_dir, sdk = sup
    pid = sdk.create_point("statement", "original").get("id")
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update(pid, content="replacement")
    assert _vector(sdk, pid) == _embed_a("replacement")

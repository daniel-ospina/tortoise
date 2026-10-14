"""#5240 — `update_point(id, content=None)` must be a no-op, not an AttributeError.

THE DEFECT THIS PINS
--------------------
``update_point(id, content=None)`` — the shape a caller produces whenever the
value comes from an optional variable (``update_point(id, content=maybe)``) —
raised a bare ``AttributeError: 'NoneType' object has no attribute 'encode'``
from ``_content_hash``, two frames away from the call and naming neither
``content`` nor ``update_point``. The derived-hash line tested KEY PRESENCE
(``"content" in props``) while the #4208 embedding guard eighteen lines below
already tested the VALUE (``_new_content is not None``).

THE FIX
-------
The boundary is decided ONCE, before any consumer reads the key: a ``None``
content is removed from the props, so the hash, the derived vector, the write
and the ``PointRevised`` record ALL agree that this is "no content edit" —
exactly what ``_revise_point`` already implements (``if new_content is not
None:`` folding ``n.content = coalesce($c, n.content)``).

⛔ A guard on the hash ALONE would not have fixed this, and the second test
below is what says so: ``SET n += $props`` carrying a None REMOVES the
property, so the live node would LOSE its content while the replay's
``coalesce`` put it back — live != rebuild, strictly worse than the crash.
The boundary is therefore "absent", not "None-but-still-in-props".

``content=""`` stays a REAL edit (it hashes the empty string and wipes the
stale vector) — only ``None`` means "nothing to set" — and a non-``str`` is
refused by the same boundary with a message that names the parameter, instead
of dying at ``.encode`` one frame away.

Run (embedded carve-out lane)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_update_point_none_content_5240.py -q
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
_ORIGINAL = "original"


def _embed_a(text: str, max_tokens: int = 512):
    """Deterministic, content-dependent vector (component 0 = text length)."""
    return [float(len(text))] + [0.25] * (_DIM - 1)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _point(sdk: TortoiseSDK, pid: str) -> tuple[str, bool]:
    """``(content, content key present)`` straight off the node.

    Read from the graph rather than ``get_point`` — this pins the STORE, and a
    value the read boundary normalises away would hide the defect the write
    boundary has to prevent.
    """
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.content, 'content' IN keys(n)",
        params={"id": pid},
    ).result_set
    assert rows, f"point {pid} missing from the graph"
    return rows[0][0], bool(rows[0][1])


def _vector(sdk: TortoiseSDK, pid: str):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.embedding",
        params={"id": pid},
    ).result_set
    assert rows, f"point {pid} missing from the graph"
    return None if rows[0][0] is None else list(rows[0][0])


def _hash(sdk: TortoiseSDK, pid: str) -> str | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.content_hash",
        params={"id": pid},
    ).result_set
    assert rows, f"point {pid} missing from the graph"
    return rows[0][0]


def _revised_records(events_dir) -> list[dict]:
    out = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("type") == "PointRevised":
                    out.append(rec)
    return out


def _created(sdk: TortoiseSDK) -> str:
    """Create the subject point and pin the pre-update state; return its id."""
    pid = sdk.create_point("statement", _ORIGINAL).get("id")
    # The no-op must be measurable against a NON-EMPTY starting state: a wipe
    # of content/hash/vector is then visible, which it would not be from None.
    assert _point(sdk, pid) == (_ORIGINAL, True)
    assert _hash(sdk, pid) == _sha(_ORIGINAL)
    assert _vector(sdk, pid) == _embed_a(_ORIGINAL)
    return pid


# ── the core regression ──────────────────────────────────────────────────

def test_content_none_is_a_no_op_not_an_attributeerror(sup):
    """FAILS BEFORE: ``NoneType has no attribute 'encode'`` from ``_content_hash``.

    The caller's optional variable is ``None`` — nothing to edit — so the call
    must succeed and leave content, the derived hash and the derived vector
    exactly as the creation wrote them.
    """
    _events, sdk = sup
    pid = _created(sdk)

    with mock.patch(_EMBED_PATCH, _embed_a):
        result = sdk.update_point(pid, content=None)  # ← AttributeError before

    assert result["content"] == _ORIGINAL, result
    # Asserted on the NODE, not through `get_point`: the property must still
    # exist. `None` is not a content CLEAR here (that would need a replay the
    # fold cannot express) — a clear must not be smuggled in by this fix.
    assert _point(sdk, pid) == (_ORIGINAL, True)
    assert _hash(sdk, pid) == _sha(_ORIGINAL), "the derived hash was re-derived"
    assert _vector(sdk, pid) == _embed_a(_ORIGINAL), "the derived vector moved"


def test_content_none_keeps_live_and_rebuild_in_agreement(sup):
    """The no-op must be REPLAY-CONSISTENT, not merely non-crashing.

    ⛔ This is the test that rules out the one-line hash guard: a ``None``
    that stays in the props is written by ``SET n += $props`` as a property
    REMOVAL, while the replay folds ``n.content = coalesce($c, n.content)``
    and therefore KEEPS the creation content. A hash-only fix passes the first
    test's crash half and fails this one — measured against that alternative:
    ``update_point(pid, content=None)`` returns a point with NO ``content``
    key at all (``KeyError: 'content'`` on the returned dict), i.e. the live
    graph loses content the rebuild restores.
    """
    events, sdk = sup
    pid = _created(sdk)

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update_point(pid, content=None)
    live = (_point(sdk, pid), _hash(sdk, pid), _vector(sdk, pid))

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    rebuilt = (_point(sdk, pid), _hash(sdk, pid), _vector(sdk, pid))
    assert rebuilt == live, f"live {live!r} != rebuild {rebuilt!r}"
    assert live[0] == (_ORIGINAL, True)


def test_content_none_does_not_swallow_the_other_props(sup):
    """A ``None`` content is a no-op for CONTENT, not for the whole call.

    ``update_point(id, content=None, confidence=0.42)`` is the real caller
    shape (an optional content beside props that ARE set) — the rest of the
    props must still land, and the journalled revision must carry them.
    """
    events, sdk = sup
    pid = _created(sdk)

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update_point(pid, content=None, confidence=0.42)

    assert sdk.get_point(pid)["confidence"] == pytest.approx(0.42)
    assert _point(sdk, pid) == (_ORIGINAL, True)
    assert _hash(sdk, pid) == _sha(_ORIGINAL)

    records = _revised_records(events)
    assert records, "no PointRevised record for the update"
    rec = records[-1]
    assert rec.get("new_content") is None, rec
    assert rec.get("confidence") == pytest.approx(0.42), rec
    # The key is GONE from the payload — a residual `"content": None` would be
    # a content claim the replay does not honour (#5004 presence-is-ownership).
    assert "content" not in rec, rec


def test_an_empty_string_is_still_a_real_edit(sup):
    """The other half of the boundary: ``""`` IS a content edit.

    Pinned beside the ``None`` cases so neither can be "tidied" into the other:
    ``""`` hashes, stores and WIPES the stale vector (#19/#4208 — a vector for
    different text is worse than none), while ``None`` touches nothing.
    """
    _events, sdk = sup
    pid = _created(sdk)

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update_point(pid, content="")
    assert _point(sdk, pid) == ("", True), "an empty string must be stored"
    assert _hash(sdk, pid) == _sha(""), "'' must re-derive the hash"
    assert _vector(sdk, pid) is None, "'' must WIPE the stale vector"

    # ...and a following `None` neither restores nor changes that state.
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update_point(pid, content=None)
    assert _point(sdk, pid) == ("", True)
    assert _hash(sdk, pid) == _sha("")
    assert _vector(sdk, pid) is None


def test_a_non_str_content_is_refused_by_name(sup):
    """The same boundary carries bad-TYPE input: a message, not a helper trace.

    Without it, ``content=123`` died at ``123.encode`` — the identical
    "internal bug" reading the issue objects to for ``None``.
    """
    _events, sdk = sup
    pid = _created(sdk)

    with pytest.raises(TypeError) as exc:
        sdk.update_point(pid, content=123)
    assert "content" in str(exc.value) and "update_point" in str(exc.value)
    # Refused BEFORE the write: the point is untouched.
    assert _point(sdk, pid) == (_ORIGINAL, True)
    assert _hash(sdk, pid) == _sha(_ORIGINAL)


# ── reachability: the consolidated `update()` routes through the same writer ─

def test_the_consolidated_update_route_shares_the_boundary(sup):
    """`update(id, content=None)` — the MCP `tortoise_update` behind it.

    Both `tortoise_update_point(id, props)` and `tortoise_update` splat the
    caller's props into this one writer, so one test on the consolidated entry
    point pins the reachable surface.
    """
    _events, sdk = sup
    pid = _created(sdk)
    with mock.patch(_EMBED_PATCH, _embed_a):
        result = sdk.update(pid, content=None)
    assert result["content"] == _ORIGINAL
    assert _point(sdk, pid) == (_ORIGINAL, True)

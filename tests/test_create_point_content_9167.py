"""#9167 — `create_point(kind, content)` must refuse a bad `content` BY NAME.

THE DEFECT THIS PINS
--------------------
``sdk.create_point("statement", None)`` — and every non-``str`` beside it —
raised a bare ``AttributeError: 'NoneType' object has no attribute 'encode'``
from ``_content_hash``, one frame inside a private helper. ``content`` is a
REQUIRED ``str`` in the signature, yet nothing validated it, so the caller saw
a message naming neither ``create_point`` nor ``content``: a caller error that
reads as an internal bug. Every write path funnels through this door, and the
MCP ``tortoise_create_point`` tool passes the caller's ``content`` straight
through, so a client sending ``{"content": null}`` got that same trace back
over the wire.

THE FIX (the door, not the helper)
----------------------------------
Validate ONCE, at the door, before any prop processing / graph read / write,
naming the argument — the posture ``update_point`` (#5240) and ``checkpoint``'s
item schema (#8554) already take at their own doors.

⛔ Deliberately NOT a ``_content_hash`` guard: that would close every remaining
surface at once but CANNOT name the caller's parameter, and it would hide which
doors still lack validation — #8554's own recorded reason for choosing the door
over the helper. The ``.encode``-free message below is what makes that choice
observable: a helper-level fix would still let ``encode`` reach the caller.

Run (embedded carve-out lane)::

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest \\
        tests/test_create_point_content_9167.py -q
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


def _point_count(sdk: TortoiseSDK) -> int:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set
    return int(rows[0][0])


def _journal_lines(events) -> list[str]:
    out: list[str] = []
    for path in sorted(events.glob("*.jsonl")):
        out.extend(line for line in path.read_text().splitlines() if line.strip())
    return out


# ── the core regression ──────────────────────────────────────────────────

def test_a_non_str_content_is_refused_by_name(sup):
    """FAILS BEFORE: ``'<type>' object has no attribute 'encode'`` from ``_content_hash``.

    The exception TYPE is asserted, not merely "some error": a bare
    ``AttributeError`` here IS the defect. So is a message that fails to name
    the surface and the argument — and the ``encode`` assertion is what keeps a
    future helper-level guard from passing this file while still handing the
    caller an internal-looking trace.
    """
    _events, sdk = sup

    for bad in (None, 123, 4.5, b"bytes", ["a"], {"x": 1}, True):
        with pytest.raises(TypeError) as exc:
            sdk.create_point("statement", bad)
        msg = str(exc.value)
        assert "create_point" in msg, f"must name the surface: {msg}"
        assert "content" in msg, f"must name the argument: {msg}"
        assert "encode" not in msg, f"no helper-level message may leak: {msg}"


def test_a_refused_call_writes_nothing_and_journals_nothing(sup):
    """Fail-closed means BEFORE the write, not before the crash.

    ``_content_hash(content)`` runs before the CREATE, but the guard's job is to
    be earlier still: a refused call must leave no Point, no journal record and
    no derived prop behind — otherwise the caller's retry would dedup onto a
    half-written node.
    """
    events, sdk = sup
    assert _point_count(sdk) == 0

    with pytest.raises(TypeError):
        sdk.create_point("statement", None, confidence=0.42, dedup=False)

    assert _point_count(sdk) == 0, "a refused create wrote a Point"
    assert _journal_lines(events) == [], "a refused create journaled a record"
    # ...and the door still works right after the refusal.
    with mock.patch(_EMBED_PATCH, _embed_a):
        pid = sdk.create_point("statement", "a real claim").get("id")
    assert sdk.get_point(pid)["content"] == "a real claim"


def test_an_empty_string_is_still_a_valid_content(sup):
    """The other half of the boundary: only a wrong TYPE is refused.

    ``""`` hashes to a real digest and ``update_point`` already pins it as a
    real content edit (#5240). A guard on "falsy" instead of "non-str" would
    refuse it, so this is pinned beside the refusals — the refusal must never be
    "tidied" into a length/content rule nobody decided.
    """
    _events, sdk = sup
    with mock.patch(_EMBED_PATCH, _embed_a):
        pid = sdk.create_point("statement", "").get("id")

    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.content, n.content_hash",
        params={"id": pid}).result_set
    assert rows and rows[0][0] == "", rows
    assert rows[0][1] == _sha(""), rows


def test_a_valid_str_content_is_unaffected(sup):
    """No-regression pin: the guard must not touch the ordinary path."""
    events, sdk = sup
    with mock.patch(_EMBED_PATCH, _embed_a):
        res = sdk.create_point("statement", "the release is on Friday")

    assert res["id"]
    assert res["content"] == "the release is on Friday"
    assert _point_count(sdk) == 1
    records = [json.loads(line) for line in _journal_lines(events)]
    assert [r.get("type") for r in records] == ["PointAdded"], records


def test_mcp_tortoise_create_point_returns_a_naming_validation_error(
        sup, monkeypatch):
    """The MCP surface a client actually calls must name the argument.

    ``tortoise_create_point`` forwards the caller's ``content`` verbatim, so
    ``{"content": null}`` used to return the ``_content_hash`` ``AttributeError``
    text through ``_safe``'s generic branch — an error the caller cannot act on.
    """
    import tortoise.mcp_server as m

    _events, sdk = sup
    monkeypatch.setattr(m, "_get_org_sdk", lambda: sdk)
    monkeypatch.setattr(m, "_enforce_quota", lambda *_a, **_k: None)
    m._transport_mode.set("stdio")
    try:
        r = m.tortoise_create_point("statement", None)
    finally:
        m._transport_mode.set(None)

    message = r["error"]
    assert "create_point" in message, message
    assert "content" in message, message
    assert "encode" not in message, f"no helper-level message may leak: {message}"

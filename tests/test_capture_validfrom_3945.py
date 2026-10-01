"""#3945 — primary capture must carry the date it already holds.

``_extract_session_v2`` forwards the extractor's validated ``when`` onto
``create_point`` as a passthrough prop (``_CAPTURE_PASSTHROUGH_PROPS``), but
nothing mapped it to ``validFrom``, so every Point created on this path was
born undated. A downstream supersession then stamped the predecessor's
``validTo`` from the successor's ``createdAt`` (the no-``validFrom``
fallback), so the undated successor's open window double-covered the
predecessor and ``restore_point_at`` answered ``ambiguous`` with no answer
instead of ``found``.

``docs/ONTOLOGY.md`` §4.7 states ``when`` and ``validFrom`` are one slot under
two spellings, so the fix maps ``when`` → ``validFrom`` at the create site.

These tests assert through the READ path (``restore_point_at``) and pin BOTH
halves of the contract: a dated session must produce a dated Point (and an
unambiguous restore across the resulting supersession chain), and an UNDATED
session must still produce an undated Point — an open window, never a
fabricated ``now``.
"""

from __future__ import annotations

import pytest

from tortoise.sdk import TortoiseSDK

#: Any non-empty conversation — the extractor is replaced by the payload below.
CONV = [
    {"role": "user", "content": "the rollout approach changed"},
    {"role": "assistant", "content": "ack"},
]


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """The offline session-extractor seam: the provider gate passes, and the
    payload-injecting fake below runs with zero network."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _point(content: str, pid: str, *, when: str | None = None) -> dict:
    """A v2 payload point — the shape ``extractor_v2`` emits, with its
    validated ``when`` slot set only when the session carried a date."""
    pt: dict = {
        "id": pid,
        "content": content,
        "pointKind": "statement",
        "reason": "NEW",
        "confidence": 0.5,
        "c_cal": 0.5,
        "about_entities": [],
        "source_ref": "session.md",
        "status": "draft",
    }
    if when is not None:
        pt["when"] = when
    return pt


def _install_payload(monkeypatch, payload: dict) -> None:
    """Drive ``_extract_session_v2`` with an exact v2 payload (the
    ``test_capture_session.py`` #2813 pattern) — the extractor's own point
    building is not where this defect lives; the SDK create site is."""
    import tortoise.extractor_v2 as ev2

    def _fake_extract(model, conversation, **kw):
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "chain_notes": [], "link_before_create": [],
                "warnings": [], "story_arc": "", "search": {},
                "stats": {}, "errors": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)


def _props(sdk: TortoiseSDK, pid: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pid}).result_set
    assert rows, f"point {pid} missing from the graph"
    return dict(rows[0][0])


def test_capture_maps_when_to_validfrom_so_restore_is_unambiguous(
        sdk, monkeypatch):
    """POSITIVE half. Two dated captures form a supersession chain through the
    real ``apply_supersessions`` path. The successor must carry the date the
    extractor held as ``validFrom``; the predecessor's ``validTo`` must then be
    exactly that date (contiguous windows), so ``restore_point_at`` between
    them returns the predecessor — never ``ambiguous``.

    Pre-fix failure: the successor is created with no ``validFrom``, so
    ``supersede_point`` falls back to the successor's ``createdAt`` for the
    predecessor's ``validTo`` and the successor's open window double-covers the
    predecessor ⇒ ``{ambiguous: True, candidates: [...]}`` and no answer.
    """
    v1 = "pt_3945_rollout_v1"
    v2 = "pt_3945_rollout_v2"

    _install_payload(monkeypatch, {
        "entities": [], "events": [],
        "points": [_point("the rollout uses approach A", v1,
                          when="2026-06-10")],
        "operators": [],
    })
    r1 = sdk.capture_session(CONV, session_id="3945-s1")
    assert r1["ok"] is True, r1
    assert r1["errors"] == [], r1
    assert [p["id"] for p in r1["points"]] == [v1], r1["points"]
    # The date the extractor held reaches the NODE as a validity window start.
    assert _props(sdk, v1).get("validFrom") == "2026-06-10", _props(sdk, v1)

    _install_payload(monkeypatch, {
        "entities": [], "events": [],
        "points": [_point("the rollout uses approach B", v2,
                          when="2026-06-14")],
        "operators": [],
        # The supersession channel the extractor derives — same-payload,
        # applied AFTER the points exist (ordering contract).
        "supersessions": [{"superseded": v1, "supersedes_by": v2,
                           "evidence": "later session value change"}],
    })
    r2 = sdk.capture_session(CONV, session_id="3945-s2")
    assert r2["ok"] is True, r2
    assert r2["errors"] == [], r2
    # The chain actually formed (the fold landed, not a silent skip).
    assert _props(sdk, v1).get("status") == "superseded", _props(sdk, v1)

    # Successor carries its own start; predecessor's end is exactly that start
    # (contiguous — no overlap, no gap).
    assert _props(sdk, v2).get("validFrom") == "2026-06-14", _props(sdk, v2)
    assert _props(sdk, v1).get("validTo") == "2026-06-14", _props(sdk, v1)

    # ...and the READ PATH answers the temporal question with ONE candidate.
    out = sdk.restore_point_at(v2, "2026-06-12")
    assert out.get("ambiguous") is not True, out
    assert out["found"] is True, out
    assert out["valid_point"]["id"] == v1, out
    assert out["valid_point"]["valid_from"] == "2026-06-10", out
    assert out["valid_point"]["valid_to"] == "2026-06-14", out


def test_capture_without_a_date_leaves_the_point_open_ended(sdk, monkeypatch):
    """NEGATIVE half — the case that matters. A session with NO date must still
    produce an UNDATED Point: no ``validFrom`` (open window), never a
    fabricated ``now``. A positive-only test cannot fail for this.

    The open window is proven behaviourally: a query far in the past still
    finds the point (a ``now``-stamped start would exclude it).
    """
    pid = "pt_3945_timeless"
    _install_payload(monkeypatch, {
        "entities": [], "events": [],
        "points": [_point("the cache is warm", pid)],   # no ``when``
        "operators": [],
    })
    res = sdk.capture_session(CONV, session_id="3945-undated")
    assert res["ok"] is True, res
    assert res["errors"] == [], res

    props = _props(sdk, pid)
    assert "validFrom" not in props, f"undated capture fabricated a start: {props}"
    assert "when" not in props, props

    out = sdk.restore_point_at(pid, "1999-01-01")
    assert out["found"] is True, out
    assert out["valid_point"]["valid_from"] is None, out
    assert out["valid_point"]["valid_to"] is None, out

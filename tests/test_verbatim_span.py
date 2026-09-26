"""Issue #5007 — the fourth layer's SPAN LINK: a pointer, not a payload.

`EXTRACTOR-V4-ARCHITECTURE.md` §2.4 / `STORAGE-ARCHITECTURE.md` §9.4: at
answer time the model must be able to re-fetch the VERBATIM span a `Point`
was derived from. The 200-char `quote` is a copy taken at write time — it
cannot be re-fetched and it truncates exactly the qualifiers that matter — so
the load-bearing half is an ADDRESS into the `Source`'s raw text.

⛔ The trap this file pins: a pointer, **not** a payload. The offsets go on
the `Point`; the raw text stays in raw storage.

The acceptance test fails on the pre-change code for a structural reason —
`Point` is `extra="forbid"`, so the two fields do not exist and a payload
carrying a span is rejected.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pydantic
import pytest

from tortoise.commit_schema import Point, _point_canonical
from tortoise.ids import content_hash
from tortoise.sdk import TortoiseSDK

PID = "pt_" + content_hash("span-probe")
RAW = "On 2026-09-19 p99 was 480 ms under the load test."
BASE = dict(
    id=PID,
    content="p99 was 480 ms on 2026-09-19",
    pointKind="measurement",
    reason="NEW",
    confidence=0.6,
    c_cal=0.6,
    source_ref="session:s1",
)


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


# ── the schema half ──────────────────────────────────────────────────────


def test_span_fields_are_declared_on_the_point_schema():
    """ACCEPTANCE: the pointer exists on the contract."""
    p = Point(**BASE, span_start=7, span_end=30)
    assert (p.span_start, p.span_end) == (7, 30)


def test_a_point_without_a_span_is_unchanged():
    """Additive: a payload that never had a span is byte-identical."""
    p = Point(**BASE)
    assert p.span_start is None and p.span_end is None
    assert "span_start" not in _point_canonical(p)
    assert "span_end" not in _point_canonical(p)


def test_a_span_folds_into_the_canonical_entry():
    p = Point(**BASE, span_start=7, span_end=30)
    canon = _point_canonical(p)
    assert canon["span_start"] == 7
    assert canon["span_end"] == 30


@pytest.mark.parametrize(
    "kw",
    [
        {"span_start": 3},                 # half a span is not an address
        {"span_end": 9},                   # ... in either direction
        {"span_start": 9, "span_end": 3},  # inverted
        {"span_start": 3, "span_end": 3},  # zero length names no text
        {"span_start": -1, "span_end": 5},  # offsets are not negative
        # ...and the store is int64: a larger offset is CLAMPED on write, so
        # the committed address and the stored address would diverge.
        {"span_start": 1, "span_end": 2**63},
    ],
)
def test_a_span_that_is_not_an_address_is_rejected(kw):
    with pytest.raises(pydantic.ValidationError):
        Point(**BASE, **kw)


@pytest.mark.parametrize(
    "kw",
    [
        {"span_start": 7},                     # half a span, either side
        {"span_end": 7},
        {"span_start": 30, "span_end": 7},     # inverted
        {"span_start": 7, "span_end": 7},      # zero length
        {"span_start": "7", "span_end": "30"},  # stringly-typed offsets
        # bool is not an offset (`isinstance(True, int)` is True in Python).
        # NOTE: this case belongs HERE, not on the model — pydantic's lax mode
        # coerces `True` to `1` for an `int` field, so the model accepts it and
        # only the props path can reject it.
        {"span_start": True, "span_end": 5},
        {"span_start": 1, "span_end": 2**63},  # past the store's int64
    ],
)
def test_sdk_create_point_rejects_a_span_that_is_not_an_address(sdk, kw):
    """The `Point` model only guards the COMMIT-SCHEMA path; these two props
    also ride `create_point`'s generic passthrough, so the same rule has to
    hold here or a node can be written holding the very state the contract
    declares impossible (#5007 review P2)."""
    with pytest.raises(ValueError):
        sdk.create_point("statement", "p99 was 480 ms", **kw)


def test_sdk_update_point_rejects_a_half_span(sdk):
    pid = sdk.create_point("statement", "p99 was 480 ms")["id"]
    with pytest.raises(ValueError):
        sdk.update_point(pid, span_start=7)
    # ...and the node is untouched by the refused write.
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) RETURN p.span_start, p.span_end",
        params={"id": pid},
    ).result_set
    assert rows[0] == [None, None]


def test_update_point_carries_a_valid_span_to_the_node(sdk):
    pid = sdk.create_point("statement", "p99 was 480 ms")["id"]
    sdk.update_point(pid, span_start=7, span_end=30)
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) RETURN p.span_start, p.span_end",
        params={"id": pid},
    ).result_set
    assert rows[0] == [7, 30]


@pytest.mark.parametrize(
    "kw",
    [
        {"span_start": 7},                      # half a span
        {"span_end": 7},
        {"span_start": 30, "span_end": 7},      # inverted
        {"span_start": "7", "span_end": "30"},  # stringly-typed
    ],
)
def test_update_entity_rejects_a_span_that_is_not_an_address(sdk, kw):
    """Re-review P2: `_update_entity` is the GENERIC tenant surface
    (`tortoise_update_entity`) and its Point branch writes caller props
    straight through `SET n += $p`. The model and `create_point`/`update_point`
    checks did not cover it, so a tenant could still persist a half span."""
    pid = sdk.create_point("statement", "p99 was 480 ms")["id"]
    with pytest.raises(ValueError):
        sdk._update_entity(pid, **kw)
    assert _span_on_node(sdk, pid) == [None, None]


# ── the round-trip half (write path → node) ──────────────────────────────


def test_span_round_trips_through_create_point(sdk):
    """The pointer reaches the NODE, not just the contract."""
    pid = sdk.create_point(
        "statement", "p99 was 480 ms", quote=RAW[:20],
        source_turn_id=4, span_start=7, span_end=30,
    )["id"]
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) RETURN p.span_start, p.span_end, p.quote",
        params={"id": pid},
    ).result_set
    assert len(rows) == 1, rows
    assert (rows[0][0], rows[0][1]) == (7, 30)


def test_span_is_a_pointer_not_a_payload(sdk):
    """⛔ The exact trap: the verbatim text must NOT be copied onto the node.

    The node carries the ADDRESS; the raw text stays in raw storage. A node
    that holds the sliced sentence is the failure mode this design exists to
    avoid (the graph is the RAM-priced store).

    COMPARATIVE (review P3): a bare "the slice is not in any string prop"
    check is vacuous — no code path ever receives `RAW`, so it could only
    fire on a hard-coded fixture. The real assertion is that a span-bearing
    point and its span-less TWIN differ by exactly the two offsets: the span
    introduces no new text prop.
    """
    plain = sdk.create_point("statement", "p99 was 480 ms",
                             source_turn_id=4)["id"]
    pid = sdk.create_point(
        "statement", "p99 was 480 ms", source_turn_id=4,
        span_start=7, span_end=30,
    )["id"]
    q = "MATCH (p:Point {id:$id}) RETURN properties(p) AS p"
    with_span = sdk._get_proj().g.query(q, params={"id": pid}).result_set[0][0]
    without = sdk._get_proj().g.query(q, params={"id": plain}).result_set[0][0]
    added = set(with_span) - set(without)
    assert added == {"span_start", "span_end"}, (
        f"the span added more than the two offsets: {sorted(added)}"
    )
    for k in ("span_start", "span_end"):
        assert k not in without, f"the span-less twin already carried {k}"
    # The address is there, so the re-fetch is possible...
    assert (with_span["span_start"], with_span["span_end"]) == (7, 30)
    # ...and no string prop holds the verbatim sentence.
    verbatim = RAW[7:30]
    assert verbatim, "fixture is wrong — the slice is empty"
    offenders = {k: v for k, v in with_span.items()
                 if isinstance(v, str) and verbatim in v}
    assert offenders == {}, (
        f"the verbatim span text is stored on the node: {offenders}"
    )


def test_span_does_not_break_a_spanless_point(sdk):
    pid = sdk.create_point("statement", "no span here")["id"]
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) RETURN p.span_start, p.span_end",
        params={"id": pid},
    ).result_set
    assert rows[0] == [None, None]


def test_capture_read_back_returns_the_span(sdk):
    """The dedup-hit read-back is DERIVED from the passthrough whitelist, so a
    span the node stores must also come back — otherwise the reply advertises a
    node that does not hold it (the #2813/#2949 class). Fails on the
    pre-change code: the field is absent from the derived RETURN clause."""
    pid = sdk.create_point(
        "statement", "p99 was 480 ms", source_turn_id=4,
        span_start=7, span_end=30,
    )["id"]
    stored = sdk._read_capture_passthrough_props(sdk._get_proj(), pid)
    assert stored.get("span_start") == 7, stored
    assert stored.get("span_end") == 30, stored


# ── the commit-path half (hosted commit → create_point → node) ───────────


def _commit_payload(pid: str) -> dict:
    """A minimal §6.1 payload carrying one span-bearing point."""
    from tortoise.commit_schema import compute_client_commit_id

    raw = {
        "schema_version": "1",
        "session_id": "s-span",
        "client_commit_id": "",
        "captured_at": "2026-09-19T10:00:00Z",
        "extractor": {"version": "value@1.0.0+abc+def", "mode": "byok",
                      "calibration_version": "v3"},
        "summary": "summary text",
        "story_arc": "arc text",
        "provenance_refs": [{"path": "session.md", "spans": ["7-30"]}],
        "sources": [],
        "entities": [{"name": "Alpha", "kind": "Project",
                      "passes_frequency_gate": True}],
        "points": [{
            "id": pid, "content": "p99 was 480 ms", "pointKind": "decision",
            "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
            "about_entities": ["Alpha"], "source_ref": "session.md",
            "quote": "", "status": "live",
            "span_start": 7, "span_end": 30,
        }],
        "operators": [],
        "telemetry": {
            "extractor": {"version": "value@1.0.0+abc+def", "mode": "byok"},
            "model": {"provider": "anthropic", "id": "claude-3-7",
                      "cfg_hash": "h1"},
            "counts": {"kept": 5, "candidate": 10, "segment": 12,
                       "window": 3, "empty_windows": 0},
            "keep_ratio": 0.5, "dedup_hits": 0, "frontier_calls": 1,
            "llm_cost_usd": 0.02, "extraction_ms": 1234, "retry_count": 0,
            "last_error_code": None,
            "confidence_histogram": [0, 0, 0, 0, 0, 0, 0, 1, 2, 2],
        },
    }
    raw["client_commit_id"] = compute_client_commit_id(
        raw["session_id"], raw["points"], raw["entities"], raw["operators"],
        raw["summary"], raw["story_arc"], [], [])
    return raw


def _span_on_node(sdk, node_id: str):
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) RETURN p.span_start, p.span_end",
        params={"id": node_id},
    ).result_set
    assert rows, f"the commit path did not write {node_id}"
    return rows[0]


def _run_commit(sdk, raw: dict, state):
    from tortoise.commit_schema import plan_commit, validate_payload_dict
    from tortoise.hosted_api import _execute_commit_writes

    result, payload = validate_payload_dict(raw)
    assert result.ok, result.errors
    plan = plan_commit(payload, state, record=None)
    _execute_commit_writes(sdk, payload, plan)
    return plan


def test_commit_path_carries_the_span_to_the_node(sdk):
    """Review P3: the two `sdk.create_point` calls in
    `_execute_commit_writes` forward the span, and nothing exercised that.
    A regression that dropped or mis-mapped the fields there would have kept
    the suite green, so drive the real commit path end to end.

    This is the NEW-point branch; the supersede branch has its own test below
    (re-review P3: one site was left unguarded when only this case existed)."""
    from tortoise.commit_schema import GraphState

    pid = "pt_" + content_hash("commit-path-span")
    plan = _run_commit(sdk, _commit_payload(pid), GraphState())
    assert plan.reconcile.points[0].action == "new", plan.reconcile.points[0]
    assert _span_on_node(sdk, pid) == [7, 30]


def test_commit_path_supersede_carries_the_span_to_the_node(sdk):
    """The OTHER `create_point` call site in `_execute_commit_writes` (the
    supersede branch) writes a NEW content-addressed node — so it needs its own
    assertion, or deleting the span kwargs from that one call alone leaves the
    suite green (re-review P3)."""
    from tortoise.commit_schema import GraphState, point_content_id

    pid = "pt_" + content_hash("commit-path-supersede")
    # The supersede branch calls `supersede_point(existing_id, new_id)`, so the
    # PRIOR node has to exist in the graph, not just in the reconcile state.
    sdk.create_point("statement", "an older statement", id=pid,
                     status="live")
    state = GraphState(points={pid: "an older statement"})
    plan = _run_commit(sdk, _commit_payload(pid), state)
    rec = plan.reconcile.points[0]
    assert rec.action == "supersede", rec
    assert rec.supersede_id == point_content_id("p99 was 480 ms")  # pt_<sha>
    assert _span_on_node(sdk, rec.supersede_id) == [7, 30]

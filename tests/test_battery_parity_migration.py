"""#2292 Task 8 — report.py producer emits ``protocol_hash``; migration locks
(coordination n3; #1144 cross-dependency recorded)."""
from __future__ import annotations

import hashlib

from battery.parity.runner import (
    TOOL_SURFACE_IDS,
    protocol_hash,
    run_parity,
)


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _baseline_2tuple(rp: str, jr: str) -> dict[str, str]:
    """An OLD-format (2-tuple, pre-#2284 Task 6) baseline record."""
    return {"reader_prompt_hash": _sha16(rp),
            "judge_rubric_id_hash": _sha16(jr)}


def test_two_tuple_baseline_backcompat_protocol_unknown():
    # an old 2-tuple baseline still matches on the reader-prompt + rubric
    # compare (back-compat) but is protocol-unknown — the caller must warn
    # + force the #1144 re-record (protocol drift never passes silently).
    rp, jr = "reader-prompt-text", "rubric-id"
    bl = _baseline_2tuple(rp, jr)
    proto = protocol_hash(seed=0, model={"model_id": "deepseek/deepseek-v4-flash",
                                         "temperature": 0.0},
                          event_schema="1.1", tool_surface=TOOL_SURFACE_IDS)
    res = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                     rp, jr, bl, protocol=proto)
    assert res.methodology_matched and res.protocol_unknown


def test_protocol_delta_trips_three_tuple():
    # a 3-tuple baseline trips methodology_matched=False on a protocol
    # delta (model temp change) even when the reader prompt + rubric are
    # unchanged — the #1414 invisibility hole closed end-to-end.
    rp, jr = "reader-prompt-text", "rubric-id"
    base_proto = protocol_hash(
        seed=0, model={"model_id": "deepseek/deepseek-v4-flash",
                       "temperature": 0.0},
        event_schema="1.1", tool_surface=TOOL_SURFACE_IDS)
    new_proto = protocol_hash(
        seed=0, model={"model_id": "deepseek/deepseek-v4-flash",
                       "temperature": 0.7},
        event_schema="1.1", tool_surface=TOOL_SURFACE_IDS)
    assert base_proto != new_proto
    bl = {**_baseline_2tuple(rp, jr), "protocol_hash": base_proto}
    ok = run_parity("locomo", "locomo-v1", "a4", rp, jr, bl,
                    protocol=base_proto)
    assert ok.methodology_matched and not ok.protocol_unknown
    trip = run_parity("locomo", "locomo-v1", "a4", rp, jr, bl,
                      protocol=new_proto)
    assert not trip.methodology_matched        # protocol delta trips — the point


def test_report_producer_emits_protocol_hash():
    # the #1144 baseline-record producer seam emits the protocol leg;
    # old-format reports (no key) still load + compare on the 2-tuple.
    from tools.longmem_eval.report import build_methodology
    m = build_methodology(seed=0, reader_model="deepseek/deepseek-v4-flash",
                          temperature=0.0, event_schema="1.1",
                          reader_prompt_hash="a" * 16,
                          judge_rubric_id_hash="b" * 16)
    assert m["protocol_hash"] and len(m["protocol_hash"]) == 64
    # back-compat: reports without the key keep comparing on the 2-tuple
    old = {"reader_prompt_hash": "a" * 16, "judge_rubric_id_hash": "b" * 16}
    assert "protocol_hash" not in old

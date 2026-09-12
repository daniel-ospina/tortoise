"""#3011 Track B — hermetic tests for the frozen serializer + union packer.

NO database, NO model, NO network. Every test builds
:class:`tortoise.subgraph.Subgraph` objects directly and asserts the exact
text frozen by ``docs/experiments/2026-09-11-abc-context-assembly-experiment.md``
§3 (serializer template + worked examples) and §5 (word budget).

Coverage maps 1:1 onto the frozen rules:

* the two worked examples (arm B and arm C) render byte-for-byte;
* block order — reserved lines at the head, confidence last, arm-C turns
  between provenance and confidence;
* every fallback (``session ?`` / ``turn ?`` / ``(date unknown)``);
* confidence formatting (half-even, trailing zeros) and ``unmeasured``;
* zero seeds → the literal sentinel;
* whole-line budget truncation (never mid-line) and ``reserved_overflow``;
* ``IMPL``→``IMPLIES``, ``NAND``→``CONTRADICTS``, ``aboutObject`` → entity name;
* session ordinal from ``haystack_session_ids`` position, turn ordinal 1-based
  from a 0-based ``t<ti>``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.subgraph import Candidate, Relation, Subgraph
from tortoise.subgraph_render import (
    EMPTY_CONTEXT_SENTINEL,
    render_arm_b,
    render_arm_c,
)

# ── builders ─────────────────────────────────────────────────────────────


def _cand(point_id: str, *, anchor_id: str, edge_type: str = "IMPL",
          reserved: bool = False, content: str = "") -> Candidate:
    return Candidate(
        point_id=point_id,
        content=content,
        anchor_id=anchor_id,
        edge_type=edge_type,
        hop=1,
        s_norm=1.0,
        raw_score=1.0,
        score=1.0,
        damped=False,
        reserved=reserved,
    )


def _sg(
    anchors: tuple[str, ...],
    *,
    candidates: tuple[Candidate, ...] = (),
    relations: tuple[Relation, ...] = (),
    content: dict[str, str] | None = None,
    zero_seed: bool = False,
) -> Subgraph:
    return Subgraph(
        seeds=tuple((a, 1.0) for a in anchors),
        anchors=tuple(anchors),
        candidates=tuple(candidates),
        relations=tuple(relations),
        zero_seed=zero_seed,
        reserved_overflow=0,
        seed_fn="vector",
        content_by_id=dict(content or {}),
    )


def _ep(alpha: float, beta: float) -> dict:
    return {"posterior_alpha": alpha, "posterior_beta": beta}


# ── the worked examples (spec §3) ────────────────────────────────────────

_ANCHOR = "lme:q1:s11:t3"
_DUMMY = "lme:q1:s11:t7"
_C3 = "lme:q1:s11:t8"
_C4 = "lme:q1:s9:t0"
_ANCHOR_TEXT = "The user is flying to Lisbon on 6 May 2023 for the offsite."

#: s11 is at 1-based position 12 → "session 12"; id t3 is 0-based → "turn 4".
_SESSIONS = [f"s{i}" for i in range(14)]

_WORKED_B = (
    f"C1: {_ANCHOR_TEXT}\n"
    "C4 CONTRADICTS C1\n"
    "C1 is about Rovo\n"
    "C1 IMPLIES C3\n"
    "C1 came from session 12 (2023-05-06), turn 4\n"
    "confidence: 0.82"
)

_WORKED_C = (
    f"C1: {_ANCHOR_TEXT}\n"
    "C4 CONTRADICTS C1\n"
    "C1 is about Rovo\n"
    "C1 IMPLIES C3\n"
    "C1 came from session 12 (2023-05-06), turn 4\n"
    "  > user: I'm flying to Lisbon on the 6th of May for the offsite.\n"
    "  > assistant: Got it — Lisbon, May 6.\n"
    "confidence: 0.82"
)


def _worked_subgraph() -> Subgraph:
    return _sg(
        (_ANCHOR,),
        candidates=(
            _cand(_DUMMY, anchor_id=_ANCHOR, content="unused"),
            _cand(_C3, anchor_id=_ANCHOR, content="c3"),
            _cand(_C4, anchor_id=_ANCHOR, edge_type="NAND", reserved=True, content="c4"),
        ),
        relations=(
            Relation(_ANCHOR, "aboutObject", "rovo", "Rovo"),
            Relation(_ANCHOR, "IMPL", _C3, ""),
            Relation(_C4, "NAND", _ANCHOR, ""),
        ),
        content={_ANCHOR: _ANCHOR_TEXT, _DUMMY: "unused", _C3: "c3", _C4: "c4"},
    )


def _worked_points() -> dict:
    return {
        _ANCHOR: {
            "session_id": "s11",
            "createdAt": "2023-05-06",
            "source_turn_id": _ANCHOR,
            **_ep(0.82, 0.18),
        }
    }


def test_worked_example_arm_b_byte_for_byte():
    result = render_arm_b(
        _worked_subgraph(),
        haystack_session_ids=_SESSIONS,
        points_by_id=_worked_points(),
    )
    assert result.text == _WORKED_B
    assert result.zero_seed is False
    assert result.claims_rendered == 1
    assert result.relations_rendered == 3
    assert result.reserved_overflow == 0


def test_worked_example_arm_c_byte_for_byte():
    turns = {
        _ANCHOR: [
            "> user: I'm flying to Lisbon on the 6th of May for the offsite.",
            "> assistant: Got it — Lisbon, May 6.",
        ]
    }
    result = render_arm_c(
        _worked_subgraph(),
        haystack_session_ids=_SESSIONS,
        points_by_id=_worked_points(),
        turns_by_point=turns,
    )
    assert result.text == _WORKED_C
    # Arm C is arm B plus its provenance turns.
    assert result.word_count >= render_arm_b(
        _worked_subgraph(),
        haystack_session_ids=_SESSIONS,
        points_by_id=_worked_points(),
    ).word_count


def test_worked_example_arm_c_accepts_role_content_mappings():
    turns = {
        _ANCHOR: [
            {"role": "user", "content": "I'm flying to Lisbon on the 6th of May for the offsite."},
            {"role": "assistant", "content": "Got it — Lisbon, May 6."},
        ]
    }
    result = render_arm_c(
        _worked_subgraph(),
        haystack_session_ids=_SESSIONS,
        points_by_id=_worked_points(),
        turns_by_point=turns,
    )
    assert result.text == _WORKED_C


# ── block order ──────────────────────────────────────────────────────────

_SUPERSEDER = "lme:q1:s1:t0"
_NAND_SRC = "lme:q1:s2:t1"
_IMPL_TGT = "lme:q1:s3:t2"


def _ordered_subgraph() -> Subgraph:
    return _sg(
        (_ANCHOR,),
        relations=(
            # Deliberately supplied in label-discovery order; the renderer
            # re-orders reserved lines (supersession before NAND) at the head.
            Relation(_SUPERSEDER, "supersession", _ANCHOR, ""),
            Relation(_NAND_SRC, "NAND", _ANCHOR, ""),
            Relation(_ANCHOR, "aboutObject", "rovo", "Rovo"),
            Relation(_ANCHOR, "IMPL", _IMPL_TGT, ""),
        ),
        content={_ANCHOR: _ANCHOR_TEXT},
    )


def test_block_order_reserved_head_confidence_last():
    result = render_arm_b(
        _ordered_subgraph(),
        haystack_session_ids=_SESSIONS,
        points_by_id={_ANCHOR: {"session_id": "s11", "createdAt": "2023-05-06"}},
    )
    lines = result.text.split("\n")
    assert lines[0] == f"C1: {_ANCHOR_TEXT}"
    # Reserved lines lead, supersession (0.9) before NAND (0.8).
    assert lines[1] == "C1 [SUPERSEDED BY C2]"
    assert lines[2] == "C3 CONTRADICTS C1"
    # Remaining lines in admission-priority order.
    assert lines[3] == "C1 is about Rovo"
    assert lines[4] == "C1 IMPLIES C4"
    assert lines[5] == "C1 came from session 12 (2023-05-06), turn 4"
    assert lines[6] == "confidence: unmeasured"
    assert lines[-1] == "confidence: unmeasured"


def test_arm_c_turns_sit_between_provenance_and_confidence():
    result = render_arm_c(
        _ordered_subgraph(),
        haystack_session_ids=_SESSIONS,
        points_by_id={_ANCHOR: {"session_id": "s11", "createdAt": "2023-05-06"}},
        turns_by_point={_ANCHOR: ["> user: turn text"]},
    )
    lines = result.text.split("\n")
    provenance_index = next(i for i, line in enumerate(lines) if "came from" in line)
    turn_index = next(i for i, line in enumerate(lines) if line == "  > user: turn text")
    confidence_index = next(i for i, line in enumerate(lines) if line.startswith("confidence:"))
    assert provenance_index < turn_index < confidence_index
    assert confidence_index == len(lines) - 1


def test_two_anchors_render_two_blocks_back_to_back_with_mirrored_nand():
    a = "lme:q1:s0:t0"
    b = "lme:q1:s1:t0"
    sg = _sg(
        (a, b),
        relations=(Relation(a, "NAND", b, ""),),
        content={a: "claim a", b: "claim b"},
    )
    result = render_arm_b(sg, haystack_session_ids=_SESSIONS)
    lines = result.text.split("\n")
    assert lines[0] == "C1: claim a"
    assert lines[1] == "C1 CONTRADICTS C2"
    assert lines[2].startswith("C1 came from")
    assert lines[3] == "confidence: unmeasured"
    assert lines[4] == "C2: claim b"
    # The mirrored reserved line leads the reserved endpoint's own block.
    assert lines[5] == "C1 CONTRADICTS C2"
    assert result.claims_rendered == 2
    assert result.relations_rendered == 2


def test_supersession_rendered_when_anchor_is_the_superseder():
    # P1 regression: the superseded claim is a non-seed candidate, so
    # attributing the relation only to its target silently dropped the line
    # from every block. Attribution is symmetric with NAND, and the marker
    # always names the SUPERSEDED claim (the relation's target).
    superseder = "lme:q1:s1:t0"
    superseded = "lme:q1:s2:t0"
    sg = _sg(
        (superseder,),
        candidates=(
            _cand(
                superseded,
                anchor_id=superseder,
                edge_type="supersession",
                reserved=True,
                content="old claim",
            ),
        ),
        relations=(Relation(superseder, "supersession", superseded, ""),),
        content={superseder: "new claim", superseded: "old claim"},
    )
    result = render_arm_b(sg, haystack_session_ids=_SESSIONS)
    lines = result.text.split("\n")
    assert lines[0] == "C1: new claim"
    assert lines[1] == "C2 [SUPERSEDED BY C1]"
    assert result.relations_rendered == 1


def test_two_anchors_render_mirrored_supersession_in_both_blocks():
    # Both endpoints admitted as anchors: each block leads with the same
    # marker line naming the superseded claim.
    new = "lme:q1:s0:t0"
    old = "lme:q1:s1:t0"
    sg = _sg(
        (new, old),
        relations=(Relation(new, "supersession", old, ""),),
        content={new: "claim new", old: "claim old"},
    )
    result = render_arm_b(sg, haystack_session_ids=_SESSIONS)
    lines = result.text.split("\n")
    assert lines[0] == "C1: claim new"
    assert lines[1] == "C2 [SUPERSEDED BY C1]"
    assert lines[4] == "C2: claim old"
    assert lines[5] == "C2 [SUPERSEDED BY C1]"
    assert result.claims_rendered == 2
    assert result.relations_rendered == 2


# ── fallbacks ────────────────────────────────────────────────────────────

_PLAIN_ANCHOR = "point-without-turn-id"


def test_session_and_turn_and_date_fallbacks():
    sg = _sg((_PLAIN_ANCHOR,), content={_PLAIN_ANCHOR: "text"})
    result = render_arm_b(
        sg,
        haystack_session_ids=["s0", "s1"],
        points_by_id={_PLAIN_ANCHOR: {}},
    )
    assert "came from session ? (date unknown), turn ?" in result.text


def test_unparseable_and_sentinel_dates_render_date_unknown():
    for value in ("", _UNDATED_SENTINEL_GUARD, "early May", "not-a-date"):
        sg = _sg((_ANCHOR,), content={_ANCHOR: "text"})
        result = render_arm_b(
            sg,
            haystack_session_ids=_SESSIONS,
            points_by_id={_ANCHOR: {"createdAt": value}},
        )
        assert "(date unknown)" in result.text, value


_UNDATED_SENTINEL_GUARD = "1970-01-01T00:00:00Z"


def test_valid_from_wins_over_created_at():
    sg = _sg((_ANCHOR,), content={_ANCHOR: "text"})
    result = render_arm_b(
        sg,
        haystack_session_ids=_SESSIONS,
        points_by_id={_ANCHOR: {"validFrom": "2024-02-03T00:00:00Z", "createdAt": "2023-05-06"}},
    )
    assert "(2024-02-03)" in result.text


def test_session_dates_fallback_is_ignored_point_without_date_is_unknown():
    # The spec's frozen chain is validFrom → createdAt → (date unknown); it
    # defines no session-level date fallback, so a supplied session_dates map
    # must never fabricate a date the point does not carry.
    sg = _sg((_ANCHOR,), content={_ANCHOR: "text"})
    result = render_arm_b(
        sg,
        haystack_session_ids=_SESSIONS,
        session_dates={"s11": "2022-12-31"},
        points_by_id={_ANCHOR: {"session_id": "s11"}},
    )
    assert "(2022-12-31)" not in result.text
    assert "(date unknown)" in result.text


def test_session_id_absent_from_frozen_list_is_session_question():
    sg = _sg((_ANCHOR,), content={_ANCHOR: "text"})
    result = render_arm_b(
        sg,
        haystack_session_ids=["s0", "s1"],
        points_by_id={_ANCHOR: {"session_id": "s99"}},
    )
    assert "session ?" in result.text


# ── session / turn ordinals ──────────────────────────────────────────────


def test_session_ordinal_comes_from_haystack_position_not_the_id():
    sg = _sg((_ANCHOR,), content={_ANCHOR: "text"})
    result = render_arm_b(
        sg,
        haystack_session_ids=["x", "y", "s11"],  # s11 is 3rd → session 3
        points_by_id={_ANCHOR: {"session_id": "s11"}},
    )
    assert "session 3" in result.text


def test_turn_ordinal_is_one_based_from_zero_based_suffix():
    sg = _sg((_ANCHOR,), content={_ANCHOR: "text"})
    result = render_arm_b(
        sg,
        haystack_session_ids=_SESSIONS,
        points_by_id={_ANCHOR: {"session_id": "s11"}},
    )
    # id ends in t3 → turn 4 (t<ti> is 0-based).
    assert "turn 4" in result.text


def test_turn_ordinal_falls_back_to_source_turn_id():
    anchor = "claim-point"
    sg = _sg((anchor,), content={anchor: "text"})
    result = render_arm_b(
        sg,
        haystack_session_ids=_SESSIONS,
        points_by_id={anchor: {"source_turn_id": "lme:q1:s11:t9"}},
    )
    assert "turn 10" in result.text


def test_turn_ordinal_unresolvable_is_turn_question():
    anchor = "claim-point"
    sg = _sg((anchor,), content={anchor: "text"})
    result = render_arm_b(
        sg,
        haystack_session_ids=_SESSIONS,
        points_by_id={anchor: {"source_turn_id": "nope"}},
    )
    assert "turn ?" in result.text


# ── confidence ───────────────────────────────────────────────────────────


def _confidence_block(alpha: float | None, beta: float | None) -> str:
    props = {} if alpha is None else _ep(alpha, beta)
    sg = _sg((_ANCHOR,), content={_ANCHOR: "text"})
    result = render_arm_b(
        sg, haystack_session_ids=_SESSIONS, points_by_id={_ANCHOR: props}
    )
    return result.text.split("\n")[-1]


def test_confidence_two_decimals_trailing_zeros_kept():
    assert _confidence_block(0.8, 0.2) == "confidence: 0.80"
    assert _confidence_block(1.0, 0.0) == "confidence: 1.00"


def test_confidence_half_even_rounding():
    # 0.825 → 0.82 (banker's rounding), per the frozen f"{c:.2f}" rule.
    assert _confidence_block(0.825, 0.175) == "confidence: 0.82"


def test_confidence_unmeasured_for_no_ep_state():
    assert _confidence_block(None, None) == "confidence: unmeasured"


def test_confidence_unmeasured_for_partial_ep_state():
    sg = _sg((_ANCHOR,), content={_ANCHOR: "text"})
    result = render_arm_b(
        sg,
        haystack_session_ids=_SESSIONS,
        points_by_id={_ANCHOR: {"posterior_alpha": 0.9}},
    )
    assert result.text.split("\n")[-1] == "confidence: unmeasured"


# ── zero seeds ───────────────────────────────────────────────────────────


def test_zero_seed_renders_sentinel():
    sg = _sg((), zero_seed=True)
    result = render_arm_b(sg, haystack_session_ids=_SESSIONS)
    assert result.text == EMPTY_CONTEXT_SENTINEL
    assert result.zero_seed is True
    assert result.claims_rendered == 0
    assert result.relations_rendered == 0
    assert result.word_count == int(len(EMPTY_CONTEXT_SENTINEL.split()) * 1.1)


def test_no_anchors_renders_sentinel():
    sg = _sg(())
    result = render_arm_c(sg, haystack_session_ids=_SESSIONS)
    assert result.text == EMPTY_CONTEXT_SENTINEL
    assert result.zero_seed is True


# ── budget ───────────────────────────────────────────────────────────────


def _budget_subgraph() -> Subgraph:
    return _sg(
        (_ANCHOR,),
        candidates=(
            _cand(_NAND_SRC, anchor_id=_ANCHOR, edge_type="NAND", reserved=True),
        ),
        relations=(
            Relation(_ANCHOR, "aboutObject", "rovo", "Rovo"),
            Relation(_ANCHOR, "IMPL", _IMPL_TGT, ""),
            Relation(_NAND_SRC, "NAND", _ANCHOR, ""),
        ),
        content={_ANCHOR: "alpha beta gamma"},
    )


def test_budget_truncation_is_whole_line_only():
    sg = _budget_subgraph()
    kwargs = dict(
        haystack_session_ids=_SESSIONS,
        points_by_id={_ANCHOR: {"session_id": "s11", "createdAt": "2023-05-06"}},
    )
    full = render_arm_b(sg, max_words=10_000, **kwargs)
    full_lines = full.text.split("\n")
    assert len(full_lines) > 3

    for cap in range(full.word_count + 1):
        result = render_arm_b(sg, max_words=cap, **kwargs)
        got = result.text.split("\n") if result.text else []
        # Every truncation is a whole-line prefix — never a mid-line cut.
        assert got == full_lines[: len(got)], cap
        assert result.word_count == int(len(result.text.split()) * 1.1), cap
        assert result.word_count <= cap, cap


def test_reserved_overflow_counts_dropped_reserved_lines():
    nand_sources = [f"lme:q1:s{i}:t0" for i in range(1, 6)]
    sg = _sg(
        (_ANCHOR,),
        relations=tuple(Relation(s, "NAND", _ANCHOR, "") for s in nand_sources),
        content={_ANCHOR: "tiny"},
    )
    kwargs = dict(
        haystack_session_ids=_SESSIONS,
        points_by_id={_ANCHOR: {"session_id": "s11", "createdAt": "2023-05-06"}},
    )
    full = render_arm_b(sg, max_words=10_000, **kwargs)
    full_lines = full.text.split("\n")
    full_reserved = [line for line in full_lines if "CONTRADICTS" in line]
    assert len(full_reserved) == 5

    saw_overflow = False
    for cap in range(full.word_count + 1):
        result = render_arm_b(sg, max_words=cap, **kwargs)
        got = result.text.split("\n") if result.text else []
        got_reserved = sum(1 for line in got if "CONTRADICTS" in line)
        assert result.reserved_overflow == len(full_reserved) - got_reserved, cap
        # Reserved lines lead their block, so truncation drops the tail first.
        assert got == full_lines[: len(got)], cap
        if result.reserved_overflow:
            saw_overflow = True
    assert saw_overflow, "the test fixture must force at least one reserved drop"


def test_budget_preserves_reserved_lines_when_possible():
    nand_sources = [f"lme:q1:s{i}:t0" for i in range(1, 4)]
    sg = _sg(
        (_ANCHOR,),
        relations=tuple(Relation(s, "NAND", _ANCHOR, "") for s in nand_sources),
        content={_ANCHOR: "tiny"},
    )
    result = render_arm_b(
        sg,
        haystack_session_ids=_SESSIONS,
        points_by_id={_ANCHOR: {"session_id": "s11", "createdAt": "2023-05-06"}},
        max_words=5,
    )
    lines = result.text.split("\n")
    # The claim line and the leading reserved line survive; the trailing
    # reserved lines are the only reserved lines budget truncation drops.
    assert lines[0] == "C1: tiny"
    assert lines[1] == "C2 CONTRADICTS C1"
    assert result.reserved_overflow == 2
    assert result.word_count <= 5


# ── relation wording ─────────────────────────────────────────────────────


def test_impl_nand_and_about_object_wording():
    sg = _sg(
        (_ANCHOR,),
        relations=(
            Relation(_ANCHOR, "IMPL", _IMPL_TGT, ""),
            Relation(_NAND_SRC, "NAND", _ANCHOR, ""),
            Relation(_ANCHOR, "aboutObject", "obj-1", "Rovo"),
        ),
        content={_ANCHOR: "text"},
    )
    result = render_arm_b(sg, haystack_session_ids=_SESSIONS)
    assert "IMPLIES" in result.text and "IMPL " not in result.text
    assert "CONTRADICTS" in result.text
    # aboutObject renders the entity's display NAME, never its id.
    assert "is about Rovo" in result.text
    assert "obj-1" not in result.text


def test_content_by_id_is_used_for_the_claim_line():
    sg = _sg((_ANCHOR,), content={_ANCHOR: "the actual claim text"})
    result = render_arm_b(sg, haystack_session_ids=_SESSIONS)
    assert result.text.split("\n")[0] == "C1: the actual claim text"

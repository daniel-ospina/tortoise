"""#8043 — the Slice A reader-window FLOODING BOUND, measured hermetically.

WHY THIS FILE EXISTS
--------------------
#2683 (Slice A of the evidence-assembly wave, epic #2080) shipped its code half
on 2026-09-09 as ``b3ed420d9`` and left one un-run indicator: **T** —
*"same frozen reader/judge/slice — C1 0.780 reproduced with arms OFF; the 2 C2
flooding regressions recover when arms re-enabled atop the fixed assembly"*.

That target, **as written**, expired three days later: PR #3000 (`c01d9c468`,
2026-09-11 23:00 -0500) fixed #2978, so content-less operator hits stopped
consuming a reader-context item slot. The C2 window that defines "recover" no
longer exists, so a re-run today cannot attribute a delta to Slice A.

This instrument is the **structural leg of the re-specified gate** — the part
that IS measurable without a reader, a judge or a graph, on the SHIPPED product
functions (``tortoise.retrieval``), deterministically and for free. It measures
the one thing the old T was really protecting:

  * a coverage-loop FLOOD — many restatements of ONE fact occupying the
    reader window (§1 of the wave doc: *"the coverage loop merged 79 points
    into one question's 40-item window"*), and
  * the pre-#2978 composition — *"61% of admitted slots were content-less
    operator points"* (#2978).

Three arms on the SAME C2-shaped pool (item-bound 40, the wave's window):

  ``pre_2978``          the shipped ``assemble_context`` with the pre-#2978
                        admission predicate restored (``_has_claim_text``
                        forced True — a one-line counterfactual, labelled as
                        such: it is a RECONSTRUCTION, not the original C2
                        receipt, which is gone).
  ``post_2978``         the shipped predicate. **The baseline the re-specifi-
                        cation must use.**
  ``post_2978_assembly````assemble_context`` fed by
                        ``package_evidence_pool`` (the A6 arm,
                        ``TORTOISE_LME_EVIDENCE_ASSEMBLY``).

WHAT IT IS NOT
--------------
⛔ It is **not** the old T, and it must never be reported as one. There is no
reader and no judge here: it measures the *window composition* (which facts get
slots), never an answer quality number. The end-to-end ON/OFF reader run stays
owed and is re-homed onto the next end-to-end run (see
``docs/runbook/8043-slice-a-t-gate.md`` §4 and the ``not_run`` block of the
receipt).

⛔ The pool is a **constructed C2 shape** (§1's two measured facts scaled to one
window), not the frozen 50-Q slice. Its numbers characterise the mechanism, not
the corpus.

SCOPE NOTE (must travel with every number)
-------------------------------------------
The measured bounds hold for a hit that CARRIES its provenance (``quote`` /
``source_turn_id``) — the eval lane's shape (``tools/longmem_eval/ingest.py::
point_props_for_hits``) and the shape ``package_evidence_pool`` keys on. On the
PRODUCT ask lane those fields are absent from ``SearchResult``, so Slice A's
own-source leg is inert there today — measured, open, owned by **#8658**. This
instrument does not assume that bug away: the ``provenance_free`` arm pins the
opposite (a pool without provenance yields an untouched package), so the
instrument stays honest about where the bound applies.

RUN
---
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m tools.longmem_eval.slice_a_flood_bound
    # writes docs/runbook/8043-slice-a-t-gate-receipt.json (deterministic),
    # or `--check` to compare against the committed receipt without writing.

The guard is ``tests/longmem_eval/test_slice_a_flood_bound.py`` (hermetic; it
re-derives the receipt and fails on any diff).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import tortoise.retrieval as retrieval
from tortoise.retrieval import (
    DEFAULT_CONTEXT_ITEM_CAP,
    DEFAULT_PACKAGE_MAX_VERBATIM,
    assemble_context,
    package_evidence_pool,
    render_context,
)

REPO = Path(__file__).resolve().parent.parent.parent
RECEIPT_PATH = REPO / "docs" / "runbook" / "8043-slice-a-t-gate-receipt.json"

#: The C2 window (§1: *"one question's 40-item window"*). The wave's window is
#: ITEM-bound, so the token budget is held far above the cap for every arm —
#: the measurement must not confuse the item bound with the token bound.
WINDOW_ITEM_CAP = DEFAULT_CONTEXT_ITEM_CAP  # 40
MAX_CONTEXT_TOKENS = 10**6
QUESTION_DATE = "2025-07-01"

#: Pool tags (never read by the product — this instrument's own census key).
FACT_FLOOD = "flood"  # the one fact the coverage loop flooded with
FACT_VALUE = "value"  # exact-value claims (the Slice B numerator)
FACT_OTHER = "other"  # unrelated real evidence
FACT_EMPTY = "contentless"  # content-less operator hits (#2978's class)

#: §1: *"merged 79 points"* — 40 exact re-extractions + 39 near-verbatim
#: restatements (the variant is a SUPERSET of the anchor's content tokens, so
#: the near-verbatim leg applies and no fact-critical token differs).
_FLOOD_RESTATEMENTS = 79
_FLOOD_EXACT = 40
_FLOOD_TAILS = (
    "which the user mentioned",
    "according to the user",
    "the user noted",
    "as the user says",
    "apparently",
    "per the transcript",
    "which is typical",
    "the user is aware",
    "as they told me",
    "the user confirmed",
    "which was recorded",
    "the user stated",
    "in their own words",
    "which the assistant summarized",
    "the user repeated",
    "which the user emphasised",
    "as noted",
    "the user recalls",
    "which came up again",
    "the user agreed",
    "as discussed",
    "which the user flagged",
    "the user added",
    "as they put it",
    "which the user described",
    "the user observed",
    "as recorded",
    "the user highlighted",
    "which they mentioned again",
    "the user pointed out",
    "as the user explained",
    "the user remarked",
    "which the assistant echoed",
    "the user restated",
    "as the user related",
    "which the user stressed",
    "the user disclosed",
    "as they explained",
    "the user summarised",
)
_FLOOD_ANCHOR = "the user's cat Luna sheds a lot in summer"
_FLOOD_QUOTE = "the cat sheds a lot"

#: The own-source TURNS are source turns whose verbatim text CONTAINS the
#: anchored quote — the pass-2 own-source leg's shape (they are source hits,
#: not statements). Their content differs only by non-fact-critical filler; a
#: numeric filler would be a real value difference and the guard would —
#: correctly — refuse to treat them as the same utterance.
_TURN_TAILS = (
    "which they said",
    "as they remember it",
    "which came up again",
)
#: The own-source CHUNKS must be DISTINCT documents (that is what makes the
#: pass-2 leg measurable): same-session duplicate content would dedup on its
#: own, so each chunk carries its own filler words — no digits, so the chunk
#: set never trips the value guard.
_CHUNK_FILLERS = (
    "brushing",
    "vacuum",
    "grooming",
    "couch",
    "carpet",
    "blanket",
    "windowsill",
    "armchair",
)

#: §1: *"61.3% of admitted slots were empty"* — 24 of the C2 window's 40 slots.
_CONTENTLESS_HITS = 24

#: §5 Slice A: *"a distilled point + its own source raw chunks + its turns"* —
#: the pass-2 leg. The chunks carry the anchored quote plus DISTINCT filler, so
#: they are not same-session duplicates of each other: without provenance each
#: stands alone, with provenance all are the anchor's own source (<= 1 kept).
_OWN_SOURCE_CHUNKS = 8
_OWN_SOURCE_TURNS = 3

#: The value-critical pairs: same frame, DIFFERENT value. These must never
#: collapse (`_pkg_differ_value_critical`) and must survive into the window.
_VALUE_STATEMENTS = (
    ("v1", "the tea set cost 300 dollars", "the tea set cost 300 dollars", "t1"),
    ("v2", "the tea set cost 400 dollars", "the tea set cost 400 dollars", "t2"),
    ("v3", "the mugs cost 12 dollars each", "the mugs cost 12 dollars each", "t3"),
    ("v4", "the mugs cost 60 dollars total", "the mugs cost 60 dollars total", "t4"),
)

_OTHER_STATEMENTS = (
    ("o1", "the user's flight to Osaka departs at 7am", "s7"),
    ("o2", "the user's hotel is booked for three nights", "s7"),
    ("o3", "the user prefers aisle seats", "s7"),
    ("o4", "the user's passport expires in 2027", "s8"),
    ("o5", "the user runs a book club on Thursdays", "s8"),
    ("o6", "the user's sister lives in Lisbon", "s9"),
    ("o7", "the user is learning Portuguese", "s9"),
    ("o8", "the user's laptop is a 14-inch model", "s9"),
)

#: The value-first mark provider the EVAL injects
#: (``evidence.mark_for_question``): the precise classes drive the ordering.
_VALUE_MARK_CLASSES = ("answer_string", "verbatim", "raw_chunk")


@contextmanager
def _pre_2978_predicate() -> Iterator[None]:
    """Restore the pre-#2978 admission predicate for the counterfactual arm.

    #2978 added ONE line to ``assemble_context`` — ``if not _has_claim_text(h):
    continue``. Forcing the predicate True removes exactly that skip, so the
    arm measures the pre-fix window on the shipped function rather than a
    re-implementation of it. Labelled ``reconstructed`` in the receipt: the
    original pre-#2978 window cannot be re-run (its baseline is what expired).
    """
    original = retrieval._has_claim_text
    retrieval._has_claim_text = lambda h: True  # type: ignore[assignment]
    try:
        yield
    finally:
        retrieval._has_claim_text = original  # type: ignore[assignment]


def _point(
    pid: str,
    content: str,
    *,
    session: str,
    index: int,
    quote: str = "",
    source_turn: str = "",
    fact: str,
    session_date: str = "2025-06-10",
) -> dict[str, Any]:
    return {
        "id": pid,
        "content": content,
        "point_kind": "statement",
        "session_id": session,
        "session_date": session_date,
        "quote": quote,
        "source_turn_id": source_turn,
        "has_answer": False,
        "lme_session_index": index,
        "_bound_fact": fact,
    }


def _chunk(cid: str, lines: list[str], *, session: str, index: int, fact: str) -> dict[str, Any]:
    return {
        "id": cid,
        "point_kind": "session-transcript",
        "content": "\n".join(lines),
        "session_id": session,
        "session_date": "2025-06-10",
        "lme_session_index": index,
        "_bound_fact": fact,
    }


def _operator(oid: str, op_type: str, *, index: int) -> dict[str, Any]:
    """A content-less operator node — ``create_operator`` writes no
    ``content``; this is the #2978 class (61% of the pre-fix window)."""
    return {
        "id": oid,
        "is_operator": True,
        "op_type": op_type,
        "lme_session_index": index,
        "_bound_fact": FACT_EMPTY,
    }


def build_c2_pool() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The C2-shaped pool (§1's two measured phenomena in ONE 40-item window).

    Rank order is the C2 shape: the coverage loop's duplicate flood and the
    content-less operator nodes occupy the top of the pool (that is what the
    loop did — it MERGED duplicates into the window, ahead of the other facts);
    the value-critical and unrelated evidence sits below them. The construction
    is deterministic, so the receipt is reproducible byte-for-byte.
    """
    pool: list[dict[str, Any]] = []
    index = 0

    # 1. The #2978 class: content-less operators, interleaved with the flood at
    #    the top of the pool. 24 of the first 40 positions -> the measured
    #    "61.3% of admitted slots" composition under the pre-fix predicate.
    for i in range(_CONTENTLESS_HITS):
        pool.append(_operator(f"op{i:02d}", "IMPL" if i % 2 else "NAND", index=index))
        index += 1
        if i < _FLOOD_EXACT // 3:
            # keep the flood entangled with the empty class (the coverage loop
            # merged into a window the empty nodes already shared)
            pool.append(
                _point(
                    f"f{i:02d}",
                    _FLOOD_ANCHOR,
                    session="s1",
                    index=index,
                    quote=_FLOOD_QUOTE,
                    source_turn="t1",
                    fact=FACT_FLOOD,
                )
            )
            index += 1

    # 2. The flood anchor + its restatements (79 points, §1).
    pool.append(
        _point(
            "f_anchor",
            _FLOOD_ANCHOR,
            session="s1",
            index=index,
            quote=_FLOOD_QUOTE,
            source_turn="t1",
            fact=FACT_FLOOD,
        )
    )
    index += 1
    for i in range(_FLOOD_EXACT - 1):
        pool.append(
            _point(
                f"f_exact{i:02d}",
                _FLOOD_ANCHOR,
                session="s1",
                index=index,
                quote=_FLOOD_QUOTE,
                source_turn="t1",
                fact=FACT_FLOOD,
            )
        )
        index += 1
    for i in range(_FLOOD_RESTATEMENTS - _FLOOD_EXACT):
        pool.append(
            _point(
                f"f_near{i:02d}",
                f"{_FLOOD_ANCHOR} {_FLOOD_TAILS[i]}",
                session="s1",
                index=index,
                quote=_FLOOD_QUOTE,
                source_turn="t1",
                fact=FACT_FLOOD,
            )
        )
        index += 1

    # 3. The flood fact's OWN sources (the pass-2 collapse leg): its source
    #    turn (id == the anchor's source_turn_id) and a raw chunk containing
    #    the anchored quote.
    for i in range(_OWN_SOURCE_TURNS):
        pool.append(
            _point(
                f"f_turn{i:02d}",
                f"[user] {_FLOOD_QUOTE} {_TURN_TAILS[i]}",
                session="s1",
                index=index,
                quote=_FLOOD_QUOTE,
                source_turn="t1",
                fact=FACT_FLOOD,
            )
        )
        index += 1
    for i in range(_OWN_SOURCE_CHUNKS):
        pool.append(
            _chunk(
                f"f_chunk{i:02d}",
                [
                    f"user: {_FLOOD_QUOTE}",
                    f"assistant: noted about the {_CHUNK_FILLERS[i]}",
                    f"user: {_CHUNK_FILLERS[i]} indeed",
                ],
                session="s1",
                index=index,
                fact=FACT_FLOOD,
            )
        )
        index += 1

    # 4. The value-critical claims (the Slice B numerator) and the unrelated
    #    evidence — BELOW the flood, exactly as the loop's merge left them.
    for pid, content, quote, turn in _VALUE_STATEMENTS:
        pool.append(
            _point(
                pid,
                content,
                session="s2",
                index=index,
                quote=quote,
                source_turn=turn,
                fact=FACT_VALUE,
                session_date="2025-06-14",
            )
        )
        index += 1
    for sid, content, session in _OTHER_STATEMENTS:
        pool.append(
            _point(
                sid,
                content,
                session=session,
                index=index,
                fact=FACT_OTHER,
                session_date="2025-06-20",
            )
        )
        index += 1

    census: dict[str, Any] = {
        "pool_items": len(pool),
        "by_fact": {
            tag: sum(1 for h in pool if h["_bound_fact"] == tag)
            for tag in (FACT_FLOOD, FACT_VALUE, FACT_OTHER, FACT_EMPTY)
        },
        "contentless_hits": sum(1 for h in pool if not retrieval._has_claim_text(h)),
    }
    return pool, census


def _value_mark_provider(h: dict) -> dict[str, bool]:
    """The eval's read-time mark provider, reduced to what this pool needs: the
    value-critical statements carry the precise (verbatim) class — the #1763/
    #1945 value-first tier the assembly orders on."""
    marks = {cls: False for cls in _VALUE_MARK_CLASSES}
    marks["source_session"] = bool(h.get("has_answer"))
    if h.get("_bound_fact") == FACT_VALUE:
        marks["answer_string"] = True
        marks["verbatim"] = True
    return marks


def _slot_census(admitted: list[dict[str, Any]]) -> dict[str, Any]:
    by_fact = {
        tag: sum(1 for h in admitted if h.get("_bound_fact") == tag)
        for tag in (FACT_FLOOD, FACT_VALUE, FACT_OTHER, FACT_EMPTY)
    }
    text = render_context(admitted, question_date=QUESTION_DATE)
    values_present = [pid for pid, content, _q, _t in _VALUE_STATEMENTS if content in text]
    return {
        "admitted": len(admitted),
        "by_fact": by_fact,
        "contentless_slots": by_fact[FACT_EMPTY],
        "contentless_slot_share": (
            round(by_fact[FACT_EMPTY] / len(admitted), 4) if admitted else 0.0
        ),
        "flood_slots": by_fact[FACT_FLOOD],
        "flood_slot_share": (round(by_fact[FACT_FLOOD] / len(admitted), 4) if admitted else 0.0),
        "value_slots": by_fact[FACT_VALUE],
        "value_claims_present": values_present,
        "other_slots": by_fact[FACT_OTHER],
    }


def measure() -> dict[str, Any]:
    """Run the three arms + the two controls over the C2-shaped pool."""
    pool, pool_census = build_c2_pool()

    with _pre_2978_predicate():
        pre = assemble_context(
            pool,
            top_k=WINDOW_ITEM_CAP,
            context_item_cap=WINDOW_ITEM_CAP,
            max_context_tokens=MAX_CONTEXT_TOKENS,
            question_date=QUESTION_DATE,
        )

    post = assemble_context(
        pool,
        top_k=WINDOW_ITEM_CAP,
        context_item_cap=WINDOW_ITEM_CAP,
        max_context_tokens=MAX_CONTEXT_TOKENS,
        question_date=QUESTION_DATE,
    )

    packaged, pkg_stats = package_evidence_pool(pool)
    assembly = assemble_context(
        packaged,
        top_k=WINDOW_ITEM_CAP,
        context_item_cap=WINDOW_ITEM_CAP,
        max_context_tokens=MAX_CONTEXT_TOKENS,
        question_date=QUESTION_DATE,
    )

    marked_packaged, marked_stats = package_evidence_pool(pool, mark_for=_value_mark_provider)
    assembly_marked = assemble_context(
        marked_packaged,
        top_k=WINDOW_ITEM_CAP,
        context_item_cap=WINDOW_ITEM_CAP,
        max_context_tokens=MAX_CONTEXT_TOKENS,
        question_date=QUESTION_DATE,
    )

    # Control 1 — the OFF-parity / no-regression leg: a duplicate-free,
    # provenance-bearing pool must come back BYTE-IDENTICAL (same ids, same
    # order) from the arm, so flipping it ON cannot regress a single-evidence
    # question.
    clean = [
        _point(
            f"c{i}",
            content,
            session="s9",
            index=i,
            quote=content,
            source_turn=f"ct{i}",
            fact=FACT_OTHER,
        )
        for i, (_pid, content, _s) in enumerate(_OTHER_STATEMENTS)
    ]
    clean_packaged, clean_stats = package_evidence_pool(clean)

    # Control 2 — the provenance-free pool (the PRODUCT ask-lane shape today,
    # #8658): with no ``quote``/``source_turn_id`` the own-source leg is inert
    # and the flood is NOT bounded by pass 2. Measured, not assumed.
    bare = [{k: v for k, v in h.items() if k not in ("quote", "source_turn_id")} for h in pool]
    bare_packaged, bare_stats = package_evidence_pool(bare)
    bare_assembly = assemble_context(
        bare_packaged,
        top_k=WINDOW_ITEM_CAP,
        context_item_cap=WINDOW_ITEM_CAP,
        max_context_tokens=MAX_CONTEXT_TOKENS,
        question_date=QUESTION_DATE,
    )

    receipt: dict[str, Any] = {
        "issue": 8043,
        "parent_slice": 2683,
        "kind": "measurement (structural leg of the re-specified Slice A gate)",
        "reader": None,
        "judge": None,
        "why_no_reader": (
            "the old T's end-to-end leg is NOT run: its pre-#2978 baseline "
            "expired (PR #3000, c01d9c468, 2026-09-11T23:00:02-05:00) and no "
            "frozen reader/judge credential or graph exists in this lane. "
            "No substitute reader is used (wave §3 freezes the reader); the "
            "end-to-end leg is re-homed per docs/runbook/8043-slice-a-t-gate.md"
        ),
        "instrument": {
            "module": "tools/longmem_eval/slice_a_flood_bound.py",
            "functions": [
                "tortoise.retrieval.assemble_context",
                "tortoise.retrieval.package_evidence_pool",
                "tortoise.retrieval.render_context",
            ],
            "hermetic": True,
            "window_item_cap": WINDOW_ITEM_CAP,
            "max_context_tokens": MAX_CONTEXT_TOKENS,
            "max_verbatim": DEFAULT_PACKAGE_MAX_VERBATIM,
            "pre_2978_arm": (
                "reconstructed — the shipped assemble_context with "
                "_has_claim_text forced True (removes exactly the #2978 skip). "
                "NOT the original C2 receipt, which does not exist"
            ),
            "pool": "constructed C2 shape (wave doc §1), not the frozen slice",
            "caveat": (
                "every number is a WINDOW-COMPOSITION figure — no answer "
                "quality is measured and none may be inferred"
            ),
        },
        "pool_census": pool_census,
        "arms": {
            "pre_2978": {
                "admission_predicate": "pre-#2978 (reconstructed)",
                "assembly": False,
                **_slot_census(pre),
            },
            "post_2978": {
                "admission_predicate": "shipped (#2978 skip)",
                "assembly": False,
                **_slot_census(post),
            },
            "post_2978_assembly": {
                "admission_predicate": "shipped (#2978 skip)",
                "assembly": True,
                "package_stats": pkg_stats,
                **_slot_census(assembly),
            },
            "post_2978_assembly_value_marked": {
                "admission_predicate": "shipped (#2978 skip)",
                "assembly": True,
                "mark_provider": "eval read-time marks (value classes)",
                "package_stats": marked_stats,
                **_slot_census(assembly_marked),
            },
            "provenance_free_assembly": {
                "admission_predicate": "shipped (#2978 skip)",
                "assembly": True,
                "provenance": "quote/source_turn_id stripped on EVERY hit — the "
                "product ask-lane shape today (#8658)",
                "package_stats": bare_stats,
                **_slot_census(bare_assembly),
            },
        },
        "controls": {
            "no_dupe_no_regression": {
                "pool_items": len(clean),
                "package_items": len(clean_packaged),
                "collapsed_duplicates": clean_stats["collapsed_duplicates"],
                "ids_identical": [h["id"] for h in clean_packaged] == [h["id"] for h in clean],
            },
        },
        "verdicts": {},
    }

    arms = receipt["arms"]
    receipt["verdicts"] = {
        "baseline_expired": {
            "measured": (
                f"pre-#2978 admits {arms['pre_2978']['contentless_slots']}/"
                f"{arms['pre_2978']['admitted']} content-less slots "
                f"({arms['pre_2978']['contentless_slot_share']:.1%}) and "
                f"{arms['pre_2978']['flood_slots']} slots for ONE fact"
            ),
            "why": "the C2 window the old T measured against cannot be "
            "reproduced: #2978 changed the admission predicate after "
            "the slice merged",
        },
        "2978_alone_does_not_bound_the_flood": {
            "measured": (
                f"post-#2978 admits {arms['post_2978']['contentless_slots']} "
                f"content-less slots but {arms['post_2978']['flood_slots']}/"
                f"{arms['post_2978']['admitted']} for the flooded fact "
                f"({arms['post_2978']['flood_slot_share']:.1%}) and "
                f"{arms['post_2978']['value_slots']} value slots"
            ),
            "why": "the fix REALLOCATES the freed slots to the highest-ranked "
            "real hits — on a duplicate flood those are the duplicates",
        },
        "assembly_bounds_the_flood": {
            "measured": (
                f"assembly ON holds the flooded fact to "
                f"{arms['post_2978_assembly']['flood_slots']} slots "
                f"(= 1 + max_verbatim), admits "
                f"{arms['post_2978_assembly']['admitted']} items and "
                f"{len(arms['post_2978_assembly']['value_claims_present'])}/4 "
                f"value-critical claims"
            ),
            "why": "one fact occupies <= 1 + max_verbatim window slots; the "
            "freed slots go to DISTINCT evidence",
        },
        "value_safety": {
            "measured": (
                "value-critical claims present: "
                f"pre={len(arms['pre_2978']['value_claims_present'])}/4 · "
                f"post={len(arms['post_2978']['value_claims_present'])}/4 · "
                f"assembly="
                f"{len(arms['post_2978_assembly']['value_claims_present'])}/4"
            ),
            "why": "the 300/400 and 12/60 pairs never collapse "
            "(_pkg_differ_value_critical) and are admitted once the "
            "flood is bounded",
        },
        "provenance_leg_is_lane_scoped": {
            "measured": (
                f"with provenance {pkg_stats['verbatim_refs_kept']} verbatim "
                f"refs kept / {arms['post_2978_assembly']['flood_slots']} flood "
                f"slots; without it "
                f"{bare_stats['verbatim_refs_kept']} refs kept / "
                f"{arms['provenance_free_assembly']['flood_slots']} flood slots"
            ),
            "why": "pass 1 (the content legs) is provenance-independent, so the "
            "STATEMENT flood is bounded either way; pass 2 — a point's "
            "OWN source chunks/turns — is the leg that is INERT without "
            "quote/source_turn_id, and #8658 is that state on the "
            "product ask lane. The instrument measures it rather than "
            "assuming it away",
        },
    }
    return receipt


def render_receipt(receipt: dict[str, Any] | None = None) -> str:
    """The canonical receipt text (deterministic — no timestamp, no path)."""
    if receipt is None:
        receipt = measure()
    return json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"


def write_receipt(path: Path = RECEIPT_PATH) -> Path:
    path.write_text(render_receipt(), encoding="utf-8")
    return path


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare against the committed receipt; write nothing (exit 1 on drift)",
    )
    parser.add_argument(
        "--stdout", action="store_true", help="print the receipt instead of writing it"
    )
    args = parser.parse_args(argv)

    text = render_receipt()
    if args.stdout:
        print(text, end="")
        return 0
    if args.check:
        committed = RECEIPT_PATH.read_text(encoding="utf-8") if RECEIPT_PATH.exists() else ""
        if committed != text:
            print(f"⛔ receipt drift: {RECEIPT_PATH} does not match the instrument output")
            return 1
        print(f"✅ receipt reproduces: {RECEIPT_PATH}")
        return 0
    path = write_receipt()
    print(f"✅ wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

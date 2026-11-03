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
  * the pre-#2978 composition — #2978's title names it *"…content-less
    operator points consume 61% of the reader's context slots…"*.

Three primary arms on the SAME C2-shaped pool (item-bound 40, the wave's
window), plus two variant arms measured on it too —
``post_2978_assembly_value_marked`` (value-critical claims marked, which makes
the value-MARK CENSUS measurable — retention is already 4/4 unmarked) and
``provenance_free_assembly`` (provenance stripped — the PRE-#8658 product
ask-lane shape, and still the shape of a DEGRADED fallback read — §SCOPE NOTE).
Five arm records in the receipt, not three:

  ``pre_2978``          the shipped ``assemble_context`` with the pre-#2978
                        admission predicate restored (``_has_claim_text``
                        forced True — a one-line counterfactual, labelled as
                        such: it is a RECONSTRUCTION, not the original C2
                        receipt, which is gone).
  ``post_2978``         the shipped predicate. **The baseline the re-specifi-
                        cation must use.**
  ``post_2978_assembly``  ``assemble_context`` fed by
                        ``package_evidence_pool`` (the A6 arm,
                        ``TORTOISE_LME_EVIDENCE_ASSEMBLY``).

⚠️ ORDERING is not observable in this receipt (D3 #9092): the hit TYPE picks
the package tier and the #1763/#1945 value mark only breaks ties INSIDE a
tier, while ``_slot_census`` records counts and text containment — never an
id order. So these arms measure the mark CENSUS and the slot bound, not which
package led the window; the ordering contract is pinned hermetically in
``tests/test_evidence_assembly.py``.

WHAT IT IS NOT
--------------
⛔ It is **not** the old T, and it must never be reported as one. There is no
reader and no judge here: it measures the *window composition* (which facts get
slots), never an answer quality number. The end-to-end ON/OFF reader run stays
owed and is re-homed onto the next end-to-end run (see
``docs/runbook/8043-slice-a-t-gate.md`` §4 and the ``why_no_reader`` block of
the receipt).

⛔ The pool is a **constructed C2 shape** — wave §1's duplicate flood plus
#2978's content-less class, scaled to one 40-item window — not the frozen 50-Q
slice. (§1 records TWO phenomena: the coverage loop's flood and the
evidence-boost re-rank of 11 items. The second is **not** modelled here.) Its
numbers characterise the mechanism, not the corpus.

SCOPE NOTE (must travel with every number)
-------------------------------------------
The measured bounds hold for a hit that CARRIES its provenance (``quote`` /
``source_turn_id``) — the eval lane's shape (``tools/longmem_eval/ingest.py::
point_props_for_hits``) and the shape ``package_evidence_pool`` keys on. The
PRODUCT ask lane did not put those fields on a ``SearchResult`` until **#8658**
landed (the point fetch now reads all four provenance columns and emits them
present-only), so before it Slice A's own-source leg was inert there. This
instrument still does not assume that shape away: the
``provenance_free_assembly`` arm measures the PRE-#8658 product shape — pass 1
(the content legs) is provenance-INDEPENDENT, so it still collapses 91 of the
139 items
(139 -> 48 packages); what breaks is pass 2, so the one flooded fact costs
**12** window slots instead of 2 (receipt
``arms.provenance_free_assembly``). It remains live as the DEGRADED-fallback
shape: those two read tiers still carry no provenance (see the
``#3804 KNOWN LIMITATION`` note in ``sdk.py``), so the arm guards a state the
product can still enter. The bound is therefore stated for the
eval lane and measured, not assumed, for the ask lane.

RUN
---
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m tools.longmem_eval.slice_a_flood_bound
    # writes docs/runbook/8043-slice-a-t-gate-receipt.json (deterministic),
    # or `--check` to compare against the committed receipt without writing.

The guard is ``tests/longmem_eval/test_slice_a_flood_bound.py`` (hermetic; it
re-derives the receipt and fails on any diff).
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/slice_a_flood_bound.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python -m tools.longmem_eval.slice_a_flood_bound`"
    )

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
    DEFAULT_PACKAGE_SAME_TURN_OVERLAP,
    DEFAULT_PACKAGE_VERBATIM_OVERLAP,
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

#: The own-source TURNS are the pass-2 own-source leg's shape: POINTS (not raw
#: chunks) whose content carries the `[user] ` prefix `_is_turn_point()` keys
#: on and whose verbatim text CONTAINS the anchored quote — that containment is
#: the leg that fires here. Measured on THIS pool: stripping `quote` changes the
#: ids the arm emits; stripping `source_turn_id` leaves them unchanged — so the
#: FLOOD BOUND is carried by containment, not by the id link. (The receipt as a
#: whole does move when the id is stripped, for two reasons that are about what
#: is REACHED and REPORTED rather than about the ids the arm emits: this
#: instrument records id-derived metadata (`value_pairs[].turns`,
#: `refused_pairs[].same_source_turn`), and with no id the same-turn guard leg is
#: never reached, so the counters drop from 119 to 117.)
#: The eval lane's OTHER leg — the turn NODE's id equal to the point's
#: `source_turn_id` — is not modelled by this fixture, so a regression in that
#: leg would not be caught here. Their content differs only by non-fact-critical
#: filler. A numeric filler is NOT caught here: containment is indifferent to
#: digits, and the value guard is not consulted on pass 2 at all (measured — a
#: numeric filler in one of these turns leaves the arm's ids identical and the
#: guard counters unchanged), so digits inside a turn tail collapse exactly as
#: verbal filler does.
_TURN_TAILS = (
    "which they said",
    "as they remember it",
    "which came up again",
)
#: The own-source CHUNKS must be DISTINCT documents (that is what makes the
#: pass-2 leg measurable): same-session duplicate content would dedup on its
#: own, so each chunk carries its own filler words. They are digit-free for
#: readability, NOT for safety: the value guard is never consulted on pass 2
#: (measured — digit-bearing chunk fillers leave the receipt byte-identical), so
#: a digit here could not have tripped it either way.
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

#: #2978's census — 168 admitted item slots, 103 EMPTY = 61.3% — is the
#: measured class this fixture reproduces. THIS fixture's share is its own:
#: 24 of the C2 window's 40 slots = 60.0% (the receipt's
#: `pre_2978.contentless_slot_share`), not the issue's 61.3%.
_CONTENTLESS_HITS = 24

#: §5, quoted: *"a distilled point + its source raw chunks + its turns all
#: restating the same fact"* — the pass-2 leg. The chunks carry the anchored
#: quote plus DISTINCT filler, so
#: they are not same-session duplicates of each other: without provenance each
#: stands alone, with provenance all are the anchor's own source (<= 1 kept).
_OWN_SOURCE_CHUNKS = 8
_OWN_SOURCE_TURNS = 3

#: The value-critical pairs: same frame, DIFFERENT value. These must never
#: collapse (`_pkg_differ_value_critical`) and must survive into the window.
#:
#: ⛔ The (content, quote) pair must NOT be byte-identical for every statement.
#: #8945 emits TWO measured quantities because `_same_fact` has two ratio legs
#: reading DIFFERENT strings — the near-verbatim leg reads normalized CONTENT,
#: the same-source-turn leg reads the verbatim QUOTE. With `content == quote`
#: everywhere, both calls at the `value_pairs` site receive the same arguments,
#: the two numbers are numerically identical by construction, and reverting the
#: content leg to the quote formula (#8945's defect: labelling a quote ratio as
#: the content threshold) would leave every assertion — and the receipt —
#: unchanged. So v3/v4 carry a paraphrase WRAPPER the quote lacks, which is the
#: real production shape the runbook argues for ("whole-quote similarity carries
#: provenance text the values do not") and the only thing that can discriminate
#: the two fields.
_VALUE_STATEMENTS = (
    ("v1", "the tea set cost 300 dollars", "the tea set cost 300 dollars", "t1"),
    ("v2", "the tea set cost 400 dollars", "the tea set cost 400 dollars", "t2"),
    (
        "v3",
        "the mugs cost 12 dollars each, per the user, for the kitchen set",
        "the mugs cost 12 dollars each",
        "t3",
    ),
    (
        "v4",
        "the mugs cost 60 dollars total, per the user, for the kitchen set",
        "the mugs cost 60 dollars total",
        "t4",
    ),
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

#: The mark provider the EVAL injects (``evidence.mark_for_question``): the
#: precise classes are what the #1763/#1945 classes name. What this pool
#: MEASURES is the value-mark CENSUS (``value_first_packages``) — under D3
#: (#9092) the mark is a within-tier tiebreak, NOT the ordering key (the hit
#: type is), and this order-blind receipt cannot see order at all.
_VALUE_MARK_CLASSES = ("answer_string", "verbatim", "raw_chunk")


@contextmanager
def _pre_2978_predicate() -> Iterator[None]:
    """Restore the pre-#2978 admission predicate for the counterfactual arm.

    #2978 added ONE line to ``assemble_context`` — ``if not _has_claim_text(h):
    continue``. Forcing the predicate True removes exactly that skip, so the
    arm measures the pre-fix window on the shipped function rather than a
    re-implementation of it. Labelled ``reconstructed`` in the receipt: the
    original pre-#2978 window cannot be re-run (its baseline is what expired).

    ⛔ The shim takes ``**_kw`` on purpose (#9741 review P2-1). The predicate
    is patched at the MODULE GLOBAL and resolved there at call time
    (``retrieval._SafeMarkerCache.claim_text`` → ``_has_claim_text(h,
    marker=…)``), so a single-positional lambda raised ``TypeError: <lambda>()
    got an unexpected keyword argument 'marker'`` the moment the real function
    gained a keyword — and the exception escaped ``measure()``, i.e. the whole
    instrument and ``render_receipt`` died and this receipt could drift
    unobserved. Accepting-and-ignoring extra keywords keeps the shim's own
    intent (force the predicate True) while surviving signature growth; a
    shim that has to be widened on every call-site change is the fragility
    ``tests/test_2350_validity_precision.py``
    ::test_the_pre_2978_predicate_shim_still_drives_assemble_context now reds
    on, on a GATING surface (this tool's own test is ``on_demand``).
    """
    original = retrieval._has_claim_text
    retrieval._has_claim_text = lambda h, **_kw: True  # type: ignore[assignment]
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
    """The C2-shaped pool — wave §1's duplicate flood + #2978's content-less
    class, in ONE 40-item window.

    Rank order is the C2 shape: the coverage loop's duplicate flood and the
    content-less operator nodes occupy the top of the pool (that is what the
    loop did — it MERGED duplicates into the window, ahead of the other facts);
    the value-critical and unrelated evidence sits below them. §1's OTHER
    recorded phenomenon — the evidence-boost re-rank of 11 items — is not
    modelled here. The construction
    is deterministic, so the receipt is reproducible byte-for-byte.
    """
    pool: list[dict[str, Any]] = []
    index = 0

    # 1. The #2978 class: content-less operators, interleaved with the flood at
    #    the top of the pool. 24 of the first 40 positions = 60.0%, this
    #    fixture's own share (see `_CONTENTLESS_HITS`) — it reproduces the
    #    CLASS #2978 measured at 61.3% (103 of 168 admitted slots EMPTY), not
    #    that percentage.
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
    value-critical statements carry the precise (verbatim) classes — the #1763/
    #1945 value-mark CENSUS this arm reports. ⚠️ D3 (#9092): the mark does NOT
    pick the package tier (the hit type does) — it only breaks ties inside a
    tier, so this provider cannot be read as "the ordering" any more."""
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


def _shares_source_turn(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """The same-source-turn leg's PRECONDITION, computed the way the leg does.

    ``_same_fact`` (``tortoise/retrieval.py``) gates that leg on
    ``_pkg_turn_key`` **presence and equality**:

        at = _pkg_turn_key(a); bt = _pkg_turn_key(b)
        if not (at and at == bt):
            return False

    ⛔ Raw ``a.get("source_turn_id") == b.get("source_turn_id")`` is NOT that
    predicate, and it diverges in two reachable ways (#10508 review, Finding 2):

    * **both absent** — raw ``None == None`` reads "same turn" and, with a quote
      overlap over the threshold, a receipt would claim the leg is what refuses
      a merge the product never even consults. This is exactly the state the
      ``provenance_free_assembly`` arm documents ("its same-turn leg is inert").
    * **``0`` vs ``"0"``** — raw reads "distinct turns", while the product's
      ``_pkg_turn_key`` maps the 0-based turn index to a string and the leg is
      REACHABLE. A false "unreachable" is the #8945 shape one level down: prose
      from a precondition the leg does not use.

    Shared by the ``value_pairs`` reachability fields and the ``refused_pairs``
    metadata so the two cannot drift apart.
    """
    ta, tb = retrieval._pkg_turn_key(a), retrieval._pkg_turn_key(b)
    return bool(ta) and ta == tb


def _refused_leg(
    a: str, b: str, hits: tuple[dict[str, Any], dict[str, Any]] | None
) -> str:
    """WHICH of ``_same_fact``'s two value-guard call sites refused ``a``/``b``.

    ``_pkg_differ_value_critical`` has two call sites in the product and they
    receive DIFFERENT KINDS of string (#10508 review round 3, Finding 2):

      * the near-verbatim leg passes the pair's normalized **CONTENT**
        (``tortoise/retrieval.py``: ``_pkg_differ_value_critical(ca, cb)``);
      * the same-source-turn leg passes the verbatim **QUOTE** spans
        (``_pkg_differ_value_critical(qa, qb)``).

    A counter that records only the arguments therefore cannot say which
    quantity a refusal PUBLISHED — and publishing a content ratio under the
    key ``quote_overlap`` is #8945 item 2 ("two quantities under one label")
    one field lower in the same verdict. So the leg is named here, from the
    strings each site passes, and the two quantities are emitted separately.

    The identity of the refusing site is read off the HITS the product
    compared (registered by :func:`_probe_value_guard`'s ``_same_fact``
    wrapper), never guessed from a pool-wide lookup: a quote-keyed pool index
    cannot resolve a CONTENT argument at all, and where the argument IS a quote
    every shared-quote hit answers the lookup, so it returns an arbitrary
    member instead of the hit actually compared.

    ⛔ When the pair's content and its quote are byte-identical (a statement
    extracted verbatim), BOTH sites would pass these same strings. The
    product's branch order decides: the content leg RETURNS at its own branch,
    so the quote leg is only ever reached with a content overlap BELOW the
    near-verbatim threshold. No case is resolved by a plausible default — an
    unrecognised pair is a loud error, because a guessed label is exactly the
    defect this function exists to remove.
    """
    if hits is None:
        raise RuntimeError(
            "the value guard was called outside `_same_fact`, so which leg "
            "refused is unknown — the receipt cannot label a ratio it did not "
            "disambiguate (#8945)"
        )
    ha, hb = hits
    ca = retrieval._pkg_norm(str(ha.get("content") or ""))
    cb = retrieval._pkg_norm(str(hb.get("content") or ""))
    qa = str(ha.get("quote") or "")
    qb = str(hb.get("quote") or "")
    content_match = (a, b) == (ca, cb)
    quote_match = bool(qa and qb) and (a, b) == (qa, qb)
    if content_match and quote_match:
        below = retrieval._pkg_overlap(ca, cb) < DEFAULT_PACKAGE_VERBATIM_OVERLAP
        return "quote" if below else "content"
    if content_match:
        return "content"
    if quote_match:
        return "quote"
    raise RuntimeError(
        "the refused pair's arguments match neither the compared hits' "
        f"normalized CONTENT nor their QUOTE spans (a={a!r}, b={b!r}) — the "
        "product's guard call sites no longer pass the strings this receipt "
        "labelled, so the label would be a guess (#10508 review, Finding 2)"
    )


def _refused_pair_record(arm: str, rp: dict[str, Any]) -> dict[str, Any]:
    """One ``refused_pairs`` record — each quantity measured from ITS strings.

    ``content_overlap`` is the ratio of the CONTENT leg (0.90 near-verbatim);
    ``quote_overlap`` the ratio of the same-source-turn leg (0.75), and
    ``leg`` names the one that actually refused. Both are computed from the
    hits the product compared, which the probe registers, so nothing here has
    to look a hit up by a string — a quote-keyed lookup cannot resolve a
    content argument at all and picks an arbitrary member among shared quotes.
    """
    ha, hb = rp["hits"]
    ca = retrieval._pkg_norm(str(ha.get("content") or ""))
    cb = retrieval._pkg_norm(str(hb.get("content") or ""))
    qa = str(ha.get("quote") or "")
    qb = str(hb.get("quote") or "")
    return {
        "arm": arm,
        "a": rp["a"],
        "b": rp["b"],
        # WHICH of the two hits the guard compared (ids, not a string lookup)
        "a_id": ha.get("id"),
        "b_id": hb.get("id"),
        # WHICH leg refused, so a content ratio is never published under a
        # quote label (the surviving #8945 item 2, one field lower).
        "leg": rp["leg"],
        # The same predicate the value-pairs block uses, and the one
        # `_same_fact` gates on (#10508 Finding 2) — not raw equality, and
        # resolved from the COMPARED hits rather than a quote-keyed index.
        "same_source_turn": _shares_source_turn(ha, hb),
        # Each quantity from its own leg's strings, so the label is true of the
        # number beside it.
        "content_overlap": round(retrieval._pkg_overlap(ca, cb), 4),
        "quote_overlap": round(retrieval._pkg_overlap(qa, qb), 4),
    }


@contextmanager
def _probe_value_guard() -> Iterator[dict[str, Any]]:
    """Count `_pkg_differ_value_critical` calls/refusals for the duration.

    The value-safety verdict makes a claim about a MECHANISM ("the guard is
    consulted N times and refuses M"), and a claim about a mechanism that
    nothing measures is prose: a deleted guard call site would leave the
    receipt asserting it while every gate stayed green. So the counters are
    recorded in the receipt and asserted by the test — the sentence is
    composed FROM them, and a change in the mechanism moves them.

    It also records WHICH LEG refused and the two HITS the product compared
    (#10508 review round 3, Finding 2). The guard is called with strings, and
    two different call sites pass two different kinds of string — so the args
    alone cannot say which quantity a refusal published. ``_same_fact`` is
    wrapped to register the pair being compared, which is what makes the leg
    (and the pair's own turn relationship) a fact rather than an inference.
    """
    original = retrieval._pkg_differ_value_critical
    original_same_fact = retrieval._same_fact
    counts: dict[str, Any] = {"calls": 0, "refusals": 0, "refused_pairs": []}
    comparing: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def _registering_same_fact(
        a: dict[str, Any], b: dict[str, Any]
    ) -> bool:
        comparing.append((a, b))
        try:
            return original_same_fact(a, b)
        finally:
            comparing.pop()

    def _counting(a: str, b: str) -> bool:
        counts["calls"] += 1
        out = original(a, b)
        if out:
            counts["refusals"] += 1
            hits = comparing[-1] if comparing else None
            counts["refused_pairs"].append(
                {
                    "a": a,
                    "b": b,
                    "hits": hits,
                    "leg": _refused_leg(a, b, hits),
                }
            )
        return out

    retrieval._pkg_differ_value_critical = _counting
    retrieval._same_fact = _registering_same_fact
    try:
        yield counts
    finally:
        retrieval._pkg_differ_value_critical = original
        retrieval._same_fact = original_same_fact


def _value_pairs_sentence(value_pairs: list[dict[str, Any]]) -> str:
    """The value-safety verdict's leading clause, COMPOSED from the measured
    pairs (#8945).

    Every number and every comparative word in the result is read from
    ``value_pairs``, so the receipt cannot state "below the threshold" for a
    pair whose measured overlap is at or above it — the failure #8945 names,
    where the generator emitted the clause unconditionally and a run with
    overlap 0.99 would still have printed "sit below 0.90".

    Two quantities, two thresholds, never one label over both:
      * ``content_overlap`` vs ``DEFAULT_PACKAGE_VERBATIM_OVERLAP`` — the
        near-verbatim ratio leg of ``_same_fact``, which reads the hits'
        normalized CONTENT;
      * ``quote_overlap`` vs ``DEFAULT_PACKAGE_SAME_TURN_OVERLAP`` — the
        same-source-turn leg, which reads the verbatim QUOTE spans.

    Exposed at module level so the guard test can feed a FORCED pair whose
    overlaps sit above a threshold and assert the wording flips.
    """

    # AN EMPTY MEASUREMENT IS NOT A PASS (#8945, one level down). "Every
    # content overlap is below the threshold" is a UNIVERSAL, and over zero
    # pairs it is vacuously true; the sentence would then read as a safety
    # verdict drawn from a comparison nothing ran — the exact defect this
    # function exists to remove, re-created one level down. The shape is not
    # hypothetical: the comparable default CONFLATES it, since dbt's data-test
    # rule is "zero failing rows → pass" and an empty population therefore
    # reads as a pass. So an empty population is stated as unmeasured.
    if not value_pairs:
        return (
            "no value pair was measured, so this verdict states nothing about "
            "either collapse threshold — an empty measurement is not a safety "
            "result."
        )

    def _comparator(below: bool) -> str:
        return "<" if below else ">="

    def _pair_clause(p: dict[str, Any]) -> str:
        return (
            f"{p['values']} (content overlap {p['content_overlap']:.2f} "
            f"{_comparator(p['content_below_threshold'])} the "
            f"{DEFAULT_PACKAGE_VERBATIM_OVERLAP:.2f} near-verbatim threshold; "
            f"quote overlap {p['quote_overlap']:.2f} "
            f"{_comparator(p['quote_below_threshold'])} the "
            f"{DEFAULT_PACKAGE_SAME_TURN_OVERLAP:.2f} same-turn threshold; "
            f"turns {p['turns'][0]}/{p['turns'][1]}"
            + (" — SAME TURN" if p["same_source_turn"] else "")
            + ")"
        )

    # REACHABILITY, not threshold position alone: a leg merges a pair only if
    # its ratio passes AND its own precondition holds. The near-verbatim leg
    # reads content and has no turn precondition; the same-turn leg additionally
    # requires a shared ``source_turn_id``, so a quote overlap over its
    # threshold on DISTINCT turns is a number that merges nothing. Asserting
    # "a leg can bite" from the ratio alone would repeat #8945's error one
    # level down — a comparison that runs, against a precondition nothing
    # checked.
    #
    # The precondition is READ from ``same_source_turn``, which ``measure()``
    # computes with the product's own ``_pkg_turn_key`` presence test (#10508
    # Finding 2). It is not re-derived from ``turns`` here: absent-vs-absent is
    # not a shared turn (the product leg is inert), and re-deriving it is how
    # this file would grow a second, divergent definition of the leg.
    reached = [
        p
        for p in value_pairs
        if p["content_leg_reachable"] or p["same_turn_leg_reachable"]
    ]
    out = "the " + " and ".join(_pair_clause(p) for p in value_pairs) + " pairs "
    if reached:
        out += (
            "do not clear both collapse legs — "
            + " and ".join(p["values"] for p in reached)
            + " reaches at least one leg's ratio on a pair its precondition "
            "allows, so the guard leg below is what refuses that merge"
        )
    else:
        out += (
            "clear both collapse legs on their own terms: every content overlap "
            "is below the near-verbatim threshold"
        )
        # COUNT the above-threshold quote pairs; never claim "the only" from an
        # ``any()``. With two such pairs the singular sentence was false on a
        # measured fact — a count asserted from a boolean, which is #8945's
        # shape again (prose outrunning the measurement).
        above_turn = [p for p in value_pairs if not p["quote_below_threshold"]]
        if not above_turn:
            out += " and every quote overlap is below the same-turn threshold"
        elif len(above_turn) == 1:
            out += (
                ", and the only quote overlap at or above the same-turn "
                "threshold belongs to a pair on DISTINCT turns, where that leg "
                "is unreachable"
            )
        else:
            out += (
                ", and every quote overlap at or above the same-turn threshold "
                "belongs to a pair on DISTINCT turns, where that leg is "
                "unreachable"
            )
        out += ", so no value pair is a collapse candidate on its own account"

    if all(p["distinct_turns"] for p in value_pairs):
        out += ", and every pair carries distinct turns"
    else:
        same = [p["values"] for p in value_pairs if not p["distinct_turns"]]
        out += (
            ". "
            + " and ".join(same)
            + (
                " sits on a single turn, so THAT pair is a same-turn candidate "
                "and the guard leg below is what this arm exercises"
                if len(same) == 1
                else " sit on a single turn, so THOSE pairs are same-turn "
                "candidates and the guard leg below is what this arm exercises"
            )
        )
    return out + "."


def measure() -> dict[str, Any]:
    """Run the three primary arms + two variant arms + the duplicate-free
    controls over the C2-shaped pool. The provenance-free pool is recorded as
    the ``provenance_free_assembly`` ARM — it measures a scope limit, not a
    no-regression control. ``controls`` holds the two no-regression legs:
    ``no_dupe_no_regression`` (a single-tier pool, which must come back
    byte-identical) and ``no_dupe_tier_boundary`` (a pool ranking a raw chunk
    above a statement, which under D3 is REGROUPED by design — so it is
    asserted against the tier key, not against byte-identity; #9114)."""
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

    with _probe_value_guard() as assembly_guard:
        packaged, pkg_stats = package_evidence_pool(pool)
    assembly = assemble_context(
        packaged,
        top_k=WINDOW_ITEM_CAP,
        context_item_cap=WINDOW_ITEM_CAP,
        max_context_tokens=MAX_CONTEXT_TOKENS,
        question_date=QUESTION_DATE,
    )

    with _probe_value_guard() as marked_guard:
        marked_packaged, marked_stats = package_evidence_pool(pool, mark_for=_value_mark_provider)
    assembly_marked = assemble_context(
        marked_packaged,
        top_k=WINDOW_ITEM_CAP,
        context_item_cap=WINDOW_ITEM_CAP,
        max_context_tokens=MAX_CONTEXT_TOKENS,
        question_date=QUESTION_DATE,
    )

    # The no-regression controls. There are TWO, because one pool cannot
    # exercise both clauses (#9114).
    #
    # (a) ``no_dupe_no_regression`` — a duplicate-free, provenance-bearing,
    #     SINGLE-TIER pool (every hit a statement) must come back
    #     BYTE-IDENTICAL (same ids, same order) from the arm, so flipping it
    #     ON cannot regress a single-evidence question.
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

    # (b) ``no_dupe_tier_boundary`` — the same duplicate-free pool with a raw
    #     CHUNK ranked ABOVE the statements. Under D3 (#9092) the ordering key
    #     is (hit-type tier, value-mark tiebreak, pool rank), and a chunk is
    #     tier 2 against the statements' tier 0 — so this pool IS reordered,
    #     BY DESIGN. Blanket byte-identity is therefore the wrong assertion
    #     here (it would pass only by the sort key being absent), and the
    #     single-tier control above cannot observe the boundary at all because
    #     every hit shares one tier. The tier-scoped pair below is the
    #     assertion that CAN fail: delete ``_package_type_tier`` from the sort
    #     key and ``tier_regroup_matches_key`` goes False.
    clean_mixed = [
        _chunk(
            "mc0",
            ["user: the rent is due on the first of the month"],
            session="s10",
            index=0,
            fact=FACT_OTHER,
        ),
        *clean,
    ]
    clean_mixed_packaged, clean_mixed_stats = package_evidence_pool(clean_mixed)
    _mixed_pool_ids = [h["id"] for h in clean_mixed]
    _mixed_pkg_ids = [h["id"] for h in clean_mixed_packaged]
    _mixed_pool_tiers = [retrieval._package_type_tier(h) for h in clean_mixed]
    _mixed_pkg_tiers = [retrieval._package_type_tier(h) for h in clean_mixed_packaged]
    # The projection the ordering key requires: a STABLE sort of the pool by
    # hit-type tier alone (no value marks on this pool, and pool rank is the
    # pool's own order, so tier is the only key that can move anything).
    _tier_key_order = [
        h["id"]
        for _tier, h in sorted(
            zip(_mixed_pool_tiers, clean_mixed, strict=True), key=lambda p: p[0]
        )
    ]

    def _ids_by_tier(ids: list[str], tiers: list[int]) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for i, t in zip(ids, tiers, strict=True):
            out.setdefault(t, []).append(i)
        return out

    _within_tier_preserved = _ids_by_tier(
        _mixed_pool_ids, _mixed_pool_tiers
    ) == _ids_by_tier(_mixed_pkg_ids, _mixed_pkg_tiers)

    # The provenance-free pool (the PRE-#8658 product ask-lane shape, and the
    # shape a DEGRADED fallback read still has) —
    # recorded under ``arms.provenance_free_assembly``, not under ``controls``:
    # with no ``quote``/``source_turn_id`` the own-source leg is inert and the
    # flood is NOT bounded by pass 2. Measured, not assumed.
    bare = [{k: v for k, v in h.items() if k not in ("quote", "source_turn_id")} for h in pool]
    with _probe_value_guard() as bare_guard:
        bare_packaged, bare_stats = package_evidence_pool(bare)
    bare_assembly = assemble_context(
        bare_packaged,
        top_k=WINDOW_ITEM_CAP,
        context_item_cap=WINDOW_ITEM_CAP,
        max_context_tokens=MAX_CONTEXT_TOKENS,
        question_date=QUESTION_DATE,
    )

    # Counterfactual for the "its refusals decide no outcome here" clause: force
    # the guard to accept everything and re-run the provenance-bearing arm. A
    # diff here means a refusal CHANGED the outcome, and the verdict must say so
    # rather than assert inertness.
    _original_guard = retrieval._pkg_differ_value_critical
    retrieval._pkg_differ_value_critical = lambda a, b: False
    try:
        packaged_unguarded, _ = package_evidence_pool(pool)
        assembly_unguarded = assemble_context(
            packaged_unguarded,
            top_k=WINDOW_ITEM_CAP,
            context_item_cap=WINDOW_ITEM_CAP,
            max_context_tokens=MAX_CONTEXT_TOKENS,
            question_date=QUESTION_DATE,
        )
    finally:
        retrieval._pkg_differ_value_critical = _original_guard
    # ``assemble_context`` returns the ordered context ITEMS, so the projection
    # is the id sequence plus the rendered probe text — the two things the
    # receipt reports about the arm.
    def _proj(items: list[dict[str, Any]]) -> list[tuple[Any, str]]:
        return [(i.get("id"), str(i.get("quote") or i.get("content"))[:60]) for i in items]

    disabling_guard_changes_output = _proj(assembly_unguarded) != _proj(assembly)

    # The value PAIRS the verdict's leading clause talks about — measured with
    # the quantity EACH threshold actually governs (#8945). `_same_fact` has
    # two ratio legs and they read DIFFERENT strings:
    #   * the near-verbatim leg compares the hits' normalized CONTENT against
    #     DEFAULT_PACKAGE_VERBATIM_OVERLAP (0.90);
    #   * the same-source-turn leg compares the verbatim QUOTE spans against
    #     DEFAULT_PACKAGE_SAME_TURN_OVERLAP (0.75).
    # Reporting one number labelled as "the threshold" stated a comparison
    # nothing performed, and labelled a quote ratio as the content threshold.
    # So both are emitted, each beside its OWN threshold, and `below_threshold`
    # is a COMPUTED fact rather than prose the generator always emits.
    _by_id = {h.get("id"): h for h in pool}
    value_pairs = []
    for a, b in (("v1", "v2"), ("v3", "v4")):
        ha, hb = _by_id.get(a, {}), _by_id.get(b, {})
        qa = str(ha.get("quote") or "")
        qb = str(hb.get("quote") or "")
        ca = str(ha.get("content") or "")
        cb = str(hb.get("content") or "")
        # The leg's OWN precondition, by the leg's own predicate (#10508
        # Finding 2): `_pkg_turn_key` presence+equality, never raw equality on
        # the raw field. Two hits with no `source_turn_id` at all share NO turn
        # — the leg is inert — and `0` vs `"0"` is the SAME turn.
        same_source_turn = _shares_source_turn(ha, hb)
        ta = retrieval._pkg_turn_key(ha)
        tb = retrieval._pkg_turn_key(hb)
        _num = lambda q: next((t for t in q.replace("/", " ").split() if t.isdigit()), "?")  # noqa: E731
        content_overlap = round(retrieval._pkg_overlap(ca, cb), 4)
        quote_overlap = round(retrieval._pkg_overlap(qa, qb), 4)
        value_pairs.append(
            {
                "a": a,
                "b": b,
                "values": f"{_num(qa)}/{_num(qb)}",
                # the near-verbatim leg's own quantity, vs its own threshold
                "content_overlap": content_overlap,
                "content_below_threshold": (
                    content_overlap < DEFAULT_PACKAGE_VERBATIM_OVERLAP
                ),
                # could the near-verbatim leg merge this pair, absent the guard?
                "content_leg_reachable": (
                    content_overlap >= DEFAULT_PACKAGE_VERBATIM_OVERLAP
                ),
                # the same-source-turn leg's own quantity, vs its own threshold
                "quote_overlap": quote_overlap,
                "quote_below_threshold": (
                    quote_overlap < DEFAULT_PACKAGE_SAME_TURN_OVERLAP
                ),
                # …and its OWN precondition: that leg is only reachable when the
                # pair actually shares a source turn, so a quote overlap over
                # the threshold on DISTINCT turns merges nothing.
                "same_turn_leg_reachable": (
                    quote_overlap >= DEFAULT_PACKAGE_SAME_TURN_OVERLAP
                    and same_source_turn
                ),
                "turns": [ta, tb],
                # The leg's precondition, stated rather than inferred from
                # `turns`: absent-vs-absent is NOT "same turn" (the product leg
                # is inert), so `distinct_turns` is its negation, not `ta != tb`.
                "same_source_turn": same_source_turn,
                "distinct_turns": not same_source_turn,
            }
        )

    guard_calls = {
        "post_2978_assembly": assembly_guard["calls"],
        "post_2978_assembly_value_marked": marked_guard["calls"],
        "provenance_free_assembly": bare_guard["calls"],
    }
    guard_refusals = {
        "post_2978_assembly": assembly_guard["refusals"],
        "post_2978_assembly_value_marked": marked_guard["refusals"],
        "provenance_free_assembly": bare_guard["refusals"],
    }
    total_calls = sum(guard_calls.values())
    total_refusals = sum(guard_refusals.values())
    refused_pairs = []
    for arm_name, g in (
        ("post_2978_assembly", assembly_guard),
        ("post_2978_assembly_value_marked", marked_guard),
        ("provenance_free_assembly", bare_guard),
    ):
        for rp in g["refused_pairs"]:
            refused_pairs.append(_refused_pair_record(arm_name, rp))

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
                "PRE-#8658 product ask-lane shape (still the shape of a "
                "DEGRADED fallback read)",
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
                "scope": (
                    "SINGLE-TIER pool (statements only) — byte-identity is the "
                    "right assertion HERE, and it cannot observe the D3 tier "
                    "regroup (see no_dupe_tier_boundary)"
                ),
            },
            "no_dupe_tier_boundary": {
                "pool_items": len(clean_mixed),
                "package_items": len(clean_mixed_packaged),
                "collapsed_duplicates": clean_mixed_stats["collapsed_duplicates"],
                "shape": "a raw chunk ranked ABOVE the statements, no duplicates",
                "pool_order": _mixed_pool_ids,
                "package_order": _mixed_pkg_ids,
                "pool_tiers": _mixed_pool_tiers,
                "package_tiers": _mixed_pkg_tiers,
                "ids_identical": _mixed_pool_ids == _mixed_pkg_ids,
                "tier_key_order": _tier_key_order,
                "tier_regroup_matches_key": _mixed_pkg_ids == _tier_key_order,
                "ids_identical_within_tier": _within_tier_preserved,
                "why": (
                    "the chunk starts at pool rank 0 and is packaged BEHIND every "
                    "statement (tier "
                    f"{retrieval._package_type_tier(clean_mixed[0])} vs "
                    f"{retrieval._package_type_tier(clean[0])}), so ids_identical "
                    "is FALSE by design and blanket byte-identity would assert "
                    "the wrong clause. The two tier-scoped assertions are what "
                    "this pool can actually falsify: tier_regroup_matches_key "
                    "fails if _package_type_tier leaves the sort key, and "
                    "ids_identical_within_tier fails if the regroup also disturbs "
                    "the peers it is not allowed to touch. Hermetic pin of the "
                    "same clause: tests/test_evidence_assembly.py::"
                    "test_unmarked_mixed_type_pool_regroups_by_hit_type"
                ),
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
            # MEASURED, not asserted in prose: these counters are what a
            # deletion of the guard (or of one of its call sites) moves. The
            # receipt's sentence below is COMPOSED from them, so a mechanism
            # change cannot leave a true-sounding claim behind.
            "guard_calls": guard_calls,
            "guard_refusals": guard_refusals,
            "guard_total_calls": total_calls,
            "guard_total_refusals": total_refusals,
            "disabling_guard_changes_output": disabling_guard_changes_output,
            "refused_pairs": refused_pairs,
            "value_pairs": value_pairs,
            "collapse_threshold": DEFAULT_PACKAGE_VERBATIM_OVERLAP,
            "same_turn_threshold": DEFAULT_PACKAGE_SAME_TURN_OVERLAP,
            "why": (
                _value_pairs_sentence(value_pairs)
                + f" The guard is still consulted — {total_calls} calls "
                "across the three packaging arms ("
                + ", ".join(f"{k}={v}" for k, v in guard_calls.items())
                + f"), {total_refusals} of them refusals ("
                + ", ".join(f"{k}={v}" for k, v in guard_refusals.items())
                + ") — and forcing it to accept everything "
                + (
                    "CHANGES this arm's ids/order, so a refusal decides an outcome here"
                    if disabling_guard_changes_output
                    else "leaves this arm's ids/order unchanged, so no refusal decides an outcome here"
                )
                + ". Its own regression is "
                "tests/test_evidence_assembly.py::"
                "test_production_length_different_values_never_collapse"
            ),
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
            "quote/source_turn_id, which was the product ask lane's state "
            "until #8658 landed and is still the state of a degraded "
            "fallback read. The instrument measures it rather than "
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

#!/usr/bin/env python3
"""Measure the S4 re-emit tax — the output-token cost of the legacy
complete-list contract vs the #1789 delta/gaps-only contract, on REAL
recorded geometry.

THE QUESTION THIS EXISTS FOR
----------------------------
`#1789` switched S4's prompt from "emitting the COMPLETE embed list" to a
delta contract (gap ADDITIONS + full-item CORRECTIONS only; unchanged S2
items are OMITTED and preserved by the `merge_embed_lists` union). The
structural change landed (PR #8041) but the issue stayed open for exactly
one reason, in its own words:

    "the honest remainder is a measurement, not code … run the S4-token
     measurement against a frozen baseline, then compare to ≥40 %."

The plan doc specified a fresh 50-question end-to-end run for that
(`docs/plans/2026-08-27-1789-s4-delta-contract.md`, Task 10). That needs a
key, a live graph and hours — and it measures the WHOLE pipeline, so a
change in S1/S2 could mask the S4 shape change. The tax is an
OUTPUT-CONTRACT property, so it can be measured directly and cheaply:
hold every input constant and vary only `delta`.

WHAT IT DOES (one session, two arms, one variable)
--------------------------------------------------
For each recorded session it replays `run_s4()` twice over the SAME real
inputs —

  * ``legacy`` runs with ``delta=False`` → the legacy re-emit template
    (`S4_TMPL` / `S4_TMPL_CORE_ONLY`), i.e. today's ``TORTOISE_S4_DELTA=0``;
  * ``delta``  runs with ``delta=True``  → `S4_TMPL_DELTA`, the shipped
    default.

— and reads the **provider's own** `usage.completion_tokens` for each call
(`_complete` records it in `stats["completion_tokens"]`). Nothing is
estimated, tokenised or modelled: the number is what the model actually
emitted.

The S3 "existing graph" block is reconstructed from the session's own S2
list, so both arms get the SAME search results and the comparison stays
single-variable. ⛔ **This is NOT production geometry, and an earlier
revision of this docstring claimed it was.** ``extract_session_v2`` runs
S3 (``search_graph``) BEFORE S4 (``run_s4``) and writes the session's items
only afterwards (``execute_embed``), so production S4 sees PREVIOUS-graph
hits — bounded at ``max_queries=15`` × ``limit=3``, with the capture's own
turn echoes dropped — never the session's own list relabelled as "already
exists". The reconstruction therefore maximally SUPPRESSES gap emission in
the delta arm (a "emit only what is missing" contract told that everything
already exists emits nothing) while maximally FRIENDING its correction channel —
and only for the 231 of the 266 items it can offer (`_render_search_results`
renders entities/points/events and has no `operators` section, so the 35
operators are never shown). The two value channels therefore sit at OPPOSITE
extremes and the aggregate's DIRECTION is not established, which is why
`compare` reports the delta arm's `s4_items` / `added_by_s4` next to the token
number instead of leaving the saving to be read alone (#11385 review P1/P3).

Composition is recorded alongside the tokens (`_s4_merge_stats` /
`_verbatim_reemissions`, the #2408 census quantities), so a reader can see
WHY the delta arm is smaller: how many items it omitted (the tax), how many
it added (gaps) and how many it corrected.

    # the measurement (needs DEEPSEEK_API_KEY). Cost, from the two recorded runs
    # rather than guessed: ~145 s TOTAL for the 7-session A/B (~21 s per session
    # across both arms), and ~4.7k output tokens on the LEGACY arm vs ~0.3k on
    # the delta arm per session — so the run is ~2.4 min and cents. (An earlier revision said "~6k per arm" — wrong for the
    # delta arm by ~20x, and a cost hint nobody can check is what a reader plans
    # a re-run against.)
    DEEPSEEK_API_KEY=… uv run python tools/s4_reeemit_tax.py run \
        --receipt tests/eval/write_path/receipts/7947-arms-2026-10-18 \
        --out docs/evidence/1789-s4-reeemit-tax/ab-<utc>.json

    # zero-cost, no-network: both arms' PROMPT geometry (the input-side half,
    # and the CI pin that the two renders differ at all)
    uv run python tools/s4_reeemit_tax.py dry-run --receipt …

Exit codes: ``0`` measured (the report is written) · ``1`` NOT MEASURED (an
unreadable receipt, a receipt with no embed list, **or zero usable pairs** —
a measurement that produced no arm is not a zero result) · ``2`` refused (no
``DEEPSEEK_API_KEY`` in the environment, an interpreter older than 3.12, or a
BAD FLAG — the last via argparse).

⚠️ WHAT THIS DOES NOT MEASURE, stated here so the report cannot be read
wider than it is: accuracy / chunk@20 (the eval lane's gates), and model
COMPLIANCE in the wild (the delta arm here is one call per session, not a
production run). Non-compliance is nonetheless *observable* per row —
`verbatim_reemissions`, `added_by_s4`, `s4_delta_reemit_suspect` — so a
model that ignored the delta contract shows up in the artifact instead of
being assumed away.
"""
from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter before the imports below — this module
# uses `datetime.UTC` (3.11+) and the repo floor is 3.12, so on an older ambient
# interpreter the failure would be an unattributed `AttributeError` instead of
# the message that says how to run it. The gate that enforces this shape is
# tests/test_entry_point_python_guard.py, not a style rule.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/s4_reeemit_tax.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/s4_reeemit_tax.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import datetime as _dt
import json
import os
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tortoise import extractor_v2 as x  # noqa: E402

# The 4 content-bearing sections S4 can carry — the tax surface
# (#2408 I-2-7: chain_notes / link_before_create / retractions are tiny and
# carry different re-emit semantics, so they are outside the cost surface).
_TAX_SECTIONS = ("entities", "events", "points", "operators")
_DEFAULT_RECEIPT = "tests/eval/write_path/receipts/7947-arms-2026-10-18"
_ARM_CHOICES = ("legacy", "delta")


# ── receipt loading ────────────────────────────────────────────────────────


def load_sessions(receipt_dir: Path, *, embeds_arm: str = "narrative",
                  only: list[str] | None = None) -> list[dict]:
    """Read the recorded (story, S2 embed list) pairs out of the write-path
    eval receipts.

    A receipt is ``{"embed_list": {<arm>: <embed list>}, "stories": {<arm>:
    <S1 story>}, "s2_stats": {<arm>: …}}`` — the S1/S2 half of a real
    write-path run. `embeds_arm` picks which arm's output to replay (both
    arms of that eval are real S2 outputs; ``narrative`` is the production
    shape).

    Raises ValueError on a receipt that carries no usable embed list — a
    silently skipped session would shrink the sample without saying so.
    """
    paths = sorted(receipt_dir.glob("*.arms.json"))
    if not paths:
        raise ValueError(f"no *.arms.json receipts under {receipt_dir}")
    out: list[dict] = []
    for p in paths:
        name = p.name.split(".")[0]
        if only and name not in only:
            continue
        doc = json.loads(p.read_text())
        embeds = (doc.get("embed_list") or {}).get(embeds_arm)
        story = (doc.get("stories") or {}).get(embeds_arm)
        if not isinstance(embeds, dict) or not story:
            raise ValueError(
                f"{p} carries no '{embeds_arm}' embed list + story pair")
        s2_stats = (doc.get("s2_stats") or {}).get(embeds_arm) or {}
        out.append({
            "session": name,
            "receipt": str(p.relative_to(_REPO)) if p.is_relative_to(_REPO) else str(p),
            "story": story,
            "s2": {s: list(embeds.get(s) or []) for s in _TAX_SECTIONS},
            "s2_reference": {
                "completion_tokens": s2_stats.get("completion_tokens"),
                "prompt_tokens": s2_stats.get("prompt_tokens"),
            },
            "turns": doc.get("turns"),
            "session_date": doc.get("session_date"),
        })
    if not out:
        raise ValueError(f"no session matched {only!r} under {receipt_dir}")
    return out


def search_from_s2(s2: dict) -> dict:
    """The S3 "existing graph" block, reconstructed from the session's S2 list.

    ⛔ **This is NOT what production S4 sees, and an earlier revision of this
docstring said it was** ("Production runs S4 *after* S2's items reach the
graph, so S4's search results are the S2 items themselves").
    ``extract_session_v2`` runs S3 (``search_graph``) BEFORE S4 and writes the
    session's items only afterwards (``execute_embed``), so production S4 reads
    bounded PREVIOUS-graph hits — ``max_queries=15`` × ``limit=3``, with the
    capture's own turn echoes dropped — never the session's own list
    relabelled as "already exists". Handing both arms the same reconstruction
    keeps the A/B single-variable (an empty block would be the "degraded
    search" condition the plan excludes from compliance reads, and it would
    change both arms' behaviour) — but the reconstruction is the MAXIMALLY
    gap-SUPPRESSING input for a delta contract while being the MAXIMALLY
    correction-FRIENDLY one (it says every S2 item it OFFERS already exists,
    and a delta correction is emitted only where the search shows that), so the
    two value channels are measured at opposite extremes and the aggregate's
    DIRECTION is not established: neither a ceiling nor a floor, just the effect
    under this input.

    ⛔ IT OFFERS 231 OF THE 266, NOT ALL 266 (#11385 review P2): this function
    returns ``entities`` / ``points`` / ``events`` only, and
    ``_render_search_results`` has no ``operators`` section either — in
    production too. The 7 receipts carry 84 entities + 33 events + 114 points +
    35 ``operators`` = 266, so the 35 operator items are never shown, their
    correction channel is STRUCTURALLY ZERO for this input, and the delta arm's
    0 collisions is NOT evidence that it declined operator corrections. The direction
    argument does not run through a subset relation at all — production's block
    holds PREVIOUS-graph hits, not this session's own items — it runs through the
    floor: 0 is the minimum a correction channel can emit, so that channel cannot emit
    FEWER than this block did, in production either, whatever its block offers. The COUNT was wrong, and an
    earlier revision of the evidence document said "all 266".

    ⛔ THE IDS ARE SYNTHETIC (`s2-ent-0`, `s2-pt-1`, …): the receipts record no
    graph ids. Production would offer real node ids (or none), so any output
    field that COPIES an id — `supersedes` is the one S4 uses — can only be an
    artefact of this alias here, and is counted as a correction by any mask that
    looks at that field. The artifact therefore cannot tell a real supersession
    from an echo of the instrument's own alias; a reader must discount those
    rows, and the evidence document does.
    """
    return {
        "entities": [
            {"id": f"s2-ent-{i}", "name": e.get("name", ""),
             "kind": e.get("kind", "")}
            for i, e in enumerate(s2.get("entities") or []) if isinstance(e, dict)
        ],
        "points": [
            {"id": f"s2-pt-{i}", "content": p.get("content", ""),
             "kind": p.get("pointKind") or p.get("kind") or ""}
            for i, p in enumerate(s2.get("points") or []) if isinstance(p, dict)
        ],
        "events": [
            {"id": f"s2-ev-{i}", "content": e.get("content", ""),
             "kind": e.get("eventKind") or e.get("kind") or ""}
            for i, e in enumerate(s2.get("events") or []) if isinstance(e, dict)
        ],
    }


# ── token accounting ───────────────────────────────────────────────────────


def prompt_tokens_total(stats: dict) -> int:
    """TOTAL prompt tokens over every successful call, from the ACCUMULATOR.

    ⛔ ``stats["prompt_tokens"]`` is a PER-CALL snapshot: ``_complete``
    overwrites it on every call (``tortoise/extractor_v2.py``, the
    ``stats.update(...)`` on the success path, whose own comment says the token
    fields stay per-call because other readers treat them as a snapshot). Beside
    ``out_tokens`` — an accumulated TOTAL with a ``basis`` string precisely
    because a final-call read under-counts exactly the expensive arms — that
    snapshot would mix a last-call number with a sum under two names that read
    alike. Invisible in the committed runs (one call per arm) and wrong on the
    first retried arm, so the row reads the accumulator
    ``_accumulate_call_cost`` sums into ``stats["cost"]["prompt_tokens"]``
    (#11385 review P3).
    """
    return int((stats.get("cost") or {}).get("prompt_tokens") or 0)


def arm_out_tokens(stats: dict) -> tuple[int | None, str]:
    """(TOTAL output tokens this arm emitted, the basis used).

    TOTAL over every SUCCESSFUL call the arm made — the escalated retry and the
    error-informed re-prompt are separate billed calls, and a final-call read
    under-counts exactly the expensive arms (the ones that truncate or emit a
    malformed list), i.e. it shrinks the legacy arm and flatters the delta.
    ⛔ A call that RAISED is in no total here — `_accumulate_call_cost` runs on
    `_complete`'s success return, so an attempt that failed before returning a
    response is counted nowhere. That is NOT detectable from this accumulator:
    `attempts` is the RUNNING TOTAL of per-call attempt numbers (production
    re-accumulates it across an arm's logical calls — `#2134`'s running-total
    accounting, and `escalated_abort` re-adds the base call's), and the raising
    call's own write CLOBBERS it, so a gap against `cost["calls"]` is neither
    sound (a healthy single 429-retried call reports `attempts=2`, `calls=1`) nor
    complete. It is recorded rather than classified: every row carries
    `attempts` and `calls` beside the total.

    `stats["cost"]["completion_tokens"]` is the ONE accumulator with that
    property: it takes one entry per SUCCESSFUL call, so an escalation or
    parse-retry is summed, never overwritten (``tortoise/extractor_v2.py::
    _accumulate_call_cost``, called from ``_complete``'s success path). It is
    read FIRST for that reason.

    The recovery-based sum is only a fallback: `recovery.s4_out_tokens` is the
    terminal successful call and `truncation_completion_tokens_s4` the
    truncated base call, so it is complete ONLY on the plain truncation path
    (no re-prompt, no partial accept). Where the cost accumulator is absent —
    a fake model in a test, a provider that reports no usage — those are the
    best available numbers, and the basis string names them so a reader never
    has to guess which quantity they are looking at.

    A tokenless response yields ``(None, …)`` — an unmeasured arm, never a 0
    that would read as "this arm emitted nothing".
    """
    cost_acc = stats.get("cost") or {}
    cost = int(cost_acc.get("completion_tokens") or 0)
    # ⛔ A partial sum must never be presented as the arm's total. THREE DISJOINT
    # counters each mean "a billed call whose completion count is NOT in
    # `cost["completion_tokens"]`", and reading only the first one was the bug:
    #   * `calls_without_tokens` — usage present, the count was rejected;
    #   * `calls_without_usage`  — the response carried NO usage block at all
    #     (`_accumulate_call_cost`'s sibling branch — the counter one to the left);
    #   * `deadline_aborts`      — killed by the wall clock after the provider had
    #     already been asked: billed, counted nowhere.
    dropped = (int(cost_acc.get("calls_without_tokens") or 0)
               + int(cost_acc.get("calls_without_usage") or 0)
               + int(stats.get("deadline_aborts") or 0))
    if dropped > 0:
        return None, (
            "unmeasured (no token count for "
            f"{dropped} of {cost_acc.get('calls') or '?'} call(s) — rejected, "
            "usage-less or deadline-aborted)")
    if cost > 0:
        # ⛔ A call that RAISED is in neither `cost` nor any counter above, and
        # this function does NOT try to detect it. `stats["attempts"]` cannot be
        # used for that: `_complete` writes it on the SUCCESS path as the attempt
        # number of ONE logical call (so a single 429-retried call reports
        # `attempts=2` while `_accumulate_call_cost` ran once — an earlier
        # revision refused that healthy arm as partial), and it OVERWRITES the
        # same key on the raise path, so a gap between it and `cost["calls"]` is
        # neither sound nor complete. The honest statement is the one below: the
        # row records `attempts` and `calls` beside the total, so a reader can
        # SEE the discrepancy even though this function cannot classify it.
        return cost, "stats.cost.completion_tokens"
    rec = stats.get("recovery") or {}
    healthy = int(rec.get("s4_out_tokens") or 0)
    trunc = int(rec.get("truncation_completion_tokens_s4") or 0)
    if trunc and int(stats.get("completion_tokens") or 0) not in (0, trunc, healthy):
        # A truncation was recorded AND the last call's count is a THIRD number,
        # so the escalated call never reached `s4_out_tokens` (the
        # partial-accept arm). The recovery sum is therefore the base call
        # alone — a partial sum must not be presented as the arm's total.
        return None, ("unmeasured (recovery accumulator incomplete after an "
                      "escalated retry)")
    if healthy or trunc:
        return healthy + trunc, "recovery.s4_out_tokens+truncation"
    fallback = int(stats.get("completion_tokens") or 0)
    if fallback > 0:
        return fallback, "stats.completion_tokens"
    # ⛔ UNMEASURED, never 0. A provider that reports no usage (or a rejected
    # count normalised to 0 — `model_adapters` defaults `usage.get(..., 0)` and
    # `_accumulate_seam_out_tokens` deliberately writes nothing on a non-positive
    # count) would otherwise hand `compare` a delta of 0, i.e. a —100 %
    # "reduction": the most flattering possible number, indistinguishable from a
    # real one. `None` propagates to "not measured" in the reduction column.
    return None, "unmeasured (provider reported no token usage)"


def run_arm(model: Any, sess: dict, arm: str) -> dict:
    """One S4 call under one contract.

    Never raises for a model/parse failure — the failure is the observation,
    and dropping it would bias the delta (the expensive arm is the one that
    truncates).
    """
    stats: dict = {}
    started = time.monotonic()
    row: dict = {"arm": arm, "delta": arm == "delta"}
    try:
        s4 = x.run_s4(model, sess["story"], search_from_s2(sess["s2"]),
                      sess["s2"], session_date=sess["session_date"],
                      stats=stats, delta=(arm == "delta"))
    except Exception as e:  # a failed arm is data, not a crash
        # ⛔ the SPEND is read here too: `_complete` accumulates cost on its
        # SUCCESS return, BEFORE `_complete_parsed` parses, so an arm that
        # emitted 16k tokens and then failed to parse HAS a populated
        # accumulator. Hard-coding ``None`` threw that away — and the arm it
        # threw away is the expensive one (a truncating / parse-failing arm),
        # which biases the delta toward the arm that never fails. The `None`
        # polarity of `arm_out_tokens` already covers a genuinely tokenless
        # failure (#11385 review P3).
        spent, spend_basis = arm_out_tokens(stats)
        row.update({
            "error": f"{type(e).__name__}: {e}"[:400],
            "out_tokens": spent,
            "basis": spend_basis,
            "prompt_tokens": prompt_tokens_total(stats),
            "truncated": bool(stats.get("truncated")),
            # ⛔ the raised-attempt case the docstring's residual describes IS
            # this branch, so the row that most needs these two numbers is the
            # one that previously recorded neither (#11385 review P1/P2). The
            # pair is the disclosure: `attempts` beside `calls`, so a reader can
            # see a retry whose spend never reached the accumulator.
            "attempts": int(stats.get("attempts") or 0),
            "calls": int((stats.get("cost") or {}).get("calls") or 0),
            "elapsed_s": round(time.monotonic() - started, 2),
        })
        return row
    merged = x.merge_embed_lists(sess["s2"], s4)
    # production's own "found nothing" predicate. It has TWO conjuncts, not one
    # (`extract_session_v2`, the `s4_delta_empty = 1` arm):
    #     if any(_s4_section_is_content(k, v) …):  merge
    #     elif not s4_shape_bad:                   s4_delta_empty = 1
    # so a section-bearing list of malformed items (`{"entities": [{}]}`) is NOT
    # production's "no changes" response — production records `s4_shape_error`
    # and does not merge. An earlier revision of this line tested the content
    # predicate ALONE, which published a malformed delta as the COMPLIANT
    # "found nothing" — the flattering direction on the one compliance signal
    # the evidence document leans on (#11385 review P2).
    malformed = x._s4_malformed_sections(s4) if isinstance(s4, dict) else []
    emitted_nothing = (not malformed and not any(
        x._s4_section_is_content(k, v)
        for k, v in (s4 or {}).items()))
    # the #2408 census instrument, reused verbatim — one implementation
    comp = x._s4_merge_stats(sess["s2"], s4, merged)
    out_tokens, basis = arm_out_tokens(stats)
    rec = stats.get("recovery") or {}
    row.update({
        "out_tokens": out_tokens,
        "basis": basis,
        "prompt_tokens": prompt_tokens_total(stats),
        "truncated": bool(stats.get("truncated")),
        "finish_reason": stats.get("finish_reason"),
        "attempts": int(stats.get("attempts") or 0),
        # ⛔ the accumulator's own contributed-call count, recorded so the
        # raised-attempt residual is AUDITABLE rather than merely asserted: a
        # reader can compare it with `attempts` (see `arm_out_tokens`). Without
        # this key the docstring's "both numbers are recorded" was false
        # (#11385 review P2).
        "calls": int((stats.get("cost") or {}).get("calls") or 0),
        # ⛔ `run_s4` does not write `s4_delta_empty` / `s4_delta_reemit_suspect`
        # — both are `extract_session_v2`-stage keys, so reading them here would
        # be a STRUCTURALLY DEAD field (None on every row, and `False` on rows
        # whose delta emitted nothing, which contradicts the data). They are
        # DERIVED here with PRODUCTION'S OWN predicates: `s4_delta_empty` from
        # `_s4_section_is_content` over EVERY emitted section (a retraction-only
        # delta is NOT empty — it carries content the merge keeps) AND
        # `_s4_malformed_sections` being empty (production's `elif not
        # s4_shape_bad`), and the
        # suspect flag from pure verbatim re-emission at ≥
        # `_S4_REEMIT_SUSPECT_RATIO` × S2.
        "s4_delta_empty": emitted_nothing if arm == "delta" else None,
        "s4_delta_reemit_suspect": (
            (comp["verbatim_reemissions"]
             >= x._S4_REEMIT_SUSPECT_RATIO * comp["s2_items"])
            if (arm == "delta" and comp["s2_items"]) else None),
        "escalation_out_tokens": int(
            rec.get("truncation_completion_tokens_s4") or 0),
        "s2_items": comp["s2_items"],
        "s4_items": comp["s4_items"],
        "merged_items": comp["merged_items"],
        "verbatim_reemissions": comp["verbatim_reemissions"],
        "corrected_by_s4": comp["corrected_by_s4"],
        "added_by_s4": comp["added_by_s4"],
        "kept_from_s2": comp["kept_from_s2"],
        # ⛔ The item TEXT, not only the counts. The plan's Task 10 Step 5
        # (partition each correction into content-changing / kind-lifecycle-only
        # / re-emission-noise) needs, per correction, BOTH the S2 item and the
        # S4 re-emitted copy for the SAME merge key — a counts-only artifact
        # cannot support the follow-up it points at, and neither can a list of
        # the additions alone (a correction is by definition the same key, so it
        # is the one thing an "items not in S2" filter drops).
        "added_items": _capped(_added_items(sess["s2"], s4),
                               comp["added_by_s4"]),
        "corrected_items": _capped(_corrected_items(sess["s2"], s4),
                                   max(0, comp["corrected_by_s4"]
                                       - comp["verbatim_reemissions"])),
        "elapsed_s": round(time.monotonic() - started, 2),
    })
    return row


#: The fields compared to classify a same-key re-emission as a CORRECTION.
#: ⛔ NOT the same mask as the census `_verbatim_reemissions` helper, which
#: compares EVERY non-optional field with a normalised fold. These 8 are the
#: fields whose change carries meaning for the plan's Task 10 Step 5, so the two
#: quantities diverge in BOTH directions: a change confined to `note` is a
#: correction to the census and invisible here, while a case/whitespace-only
#: difference is verbatim to the census and a correction here. The evidence
#: document states the divergence rather than papering over it.
_CORRECTION_FIELDS = ("content", "name", "kind", "pointKind", "eventKind",
                      "lifecycle", "supersedes", "slots")


def _field_snapshot(item: dict) -> dict:
    return {f: item.get(f) for f in _CORRECTION_FIELDS if f in item}


#: Per-channel cap on the items recorded in the artifact. The artifact is
#: evidence, not a data store; but a silent cap would make `len(list)` read as
#: the channel count, so every capped list carries an explicit truncation flag
#: and `count` (the TRUE number, taken from the census, not from the list).
_ITEM_CAP = 40


def _capped(built: tuple[list[dict], int], census_total: int) -> dict:
    """A capped list plus the truth about BOTH numbers, so a reader can never
    mistake the recorded length for the channel count.

    Two INDEPENDENT deficits can shrink the recorded list, and they must not be
    conflated (the first version of this function derived both from the census
    count, which reported a cap that never happened):

      * the CAP — `candidates` (what this mask could enumerate) exceeded
        `_ITEM_CAP`, so `cap_dropped` items were cut;
      * the MASK — the census (`added_by_s4` / `corrected_by_s4 −
        verbatim_reemissions`) counts with a different basis (every non-optional
        field) than `_CORRECTION_FIELDS`, so `unclassified` candidates are ones
        the census sees and this mask does not.
    """
    items, candidates = built
    return {
        "count": int(census_total),      # the census number, never len(items)
        "candidates": int(candidates),   # what this mask could enumerate
        "recorded": len(items),
        "cap": _ITEM_CAP,
        "cap_hit": candidates > _ITEM_CAP,
        "cap_dropped": max(0, candidates - len(items)),
        "unclassified": max(0, int(census_total) - candidates),
        "items": items,
    }


def _added_items(s2: dict, s4: dict, *, max_items: int = _ITEM_CAP,
                 clip: int = 160) -> tuple[list[dict], int]:
    """(capped list, candidate count) for the GAP channel: items this arm
    emitted that the S2 base did not contain. The candidate count is returned
    so the cap and the mask divergence stay separable."""
    s2_keys = {x._merge_key(s, i)
               for s in _TAX_SECTIONS for i in (s2.get(s) or [])
               if isinstance(i, dict)}
    out: list[dict] = []
    candidates = 0
    for s in _TAX_SECTIONS:
        for i in (s4 or {}).get(s) or []:
            if not isinstance(i, dict) or x._merge_key(s, i) in s2_keys:
                continue
            candidates += 1
            if len(out) < max_items:
                out.append(_item_record(s, i, clip))
    return out, candidates


def _corrected_items(s2: dict, s4: dict, *, max_items: int = _ITEM_CAP,
                     clip: int = 160) -> tuple[list[dict], int]:
    """(capped list, candidate count) for the CORRECTION channel: same merge key
    as an S2 item, with at least one correction-relevant field changed (≠ a
    verbatim re-emission), recorded as BOTH sides so the Task 10 Step 5
    partition can classify it offline."""
    s2_by_key = {x._merge_key(s, i): (s, i)
                 for s in _TAX_SECTIONS for i in (s2.get(s) or [])
                 if isinstance(i, dict)}
    out: list[dict] = []
    candidates = 0
    for s in _TAX_SECTIONS:
        for i in (s4 or {}).get(s) or []:
            if not isinstance(i, dict):
                continue
            hit = s2_by_key.get(x._merge_key(s, i))
            if hit is None:
                continue
            before = _field_snapshot(hit[1])
            after = _field_snapshot(i)
            changed = sorted(f for f in set(before) | set(after)
                             if before.get(f) != after.get(f))
            if not changed:
                continue                      # a verbatim re-emission — the tax
            candidates += 1
            if len(out) < max_items:
                out.append({**_item_record(s, i, clip),
                            "changed_fields": changed,
                            "s2_before": {f: str(before.get(f))[:clip]
                                          for f in changed},
                            "s4_after": {f: str(after.get(f))[:clip]
                                         for f in changed}})
    return out, candidates


def _item_record(section: str, item: dict, clip: int) -> dict:
    return {
        "section": section,
        "key": str(section[:4]) + ":" + " | ".join(
            str(part) for part in x._merge_key(section, item))[:clip],
        "content": str(item.get("content") or item.get("name") or "")[:clip],
        "kind": item.get("pointKind") or item.get("eventKind") or item.get("kind"),
        "lifecycle": item.get("lifecycle"),
        "supersedes": item.get("supersedes"),
    }


# ── the comparison ─────────────────────────────────────────────────────────


def _reduction(legacy: int, delta: int | None) -> float | None:
    """1 − delta/legacy. None when the legacy arm is unusable — never 0.0,
    which would read as "no saving" instead of "not measured"."""
    if not legacy or legacy <= 0 or delta is None:
        return None
    return round(1.0 - (delta / legacy), 4)


def _composition_totals(pairs: list[dict]) -> dict:
    """Sum the census quantities per arm — WHY the delta arm is smaller, and
    what it costs in the two value channels.

    Summed over the SAME usable pairs the token totals use — a "why it is
    smaller" table built over a different population than the headline number
    would silently describe different sessions.

    `verbatim_reemissions` is the tax itself (items re-typed unchanged);
    `added_by_s4` is the gap channel and `corrected_by_s4` the correction
    channel — the two the contract exists to keep. Reporting all three per arm
    is what stops the token delta from being read as a pure win: a delta arm
    that emits nothing because it stopped LOOKING shows up as BOTH value
    channels collapsing against the legacy arm, next to the reduction number.
    """
    keys = ("s2_items", "s4_items", "merged_items", "added_by_s4",
            "corrected_by_s4", "verbatim_reemissions")
    out: dict = {}
    for arm in _ARM_CHOICES:
        tot = dict.fromkeys(keys, 0)
        for p in pairs:
            row = p.get(arm) or {}
            for k in keys:
                tot[k] += int(row.get(k) or 0)
        out[arm] = tot
    return out


def compare(pairs: list[dict]) -> dict:
    """Aggregate one (legacy, delta) pair set into the measurement.

    PRIMARY number is the TOKEN-WEIGHTED reduction (Σdelta/Σlegacy), because
    the ticket's target is a per-chunk output-size drop and sessions differ in
    size; the per-session median is reported as the robustness check (a single
    huge session must not carry the verdict alone), plus the min so a reader can
    see whether the effect is universal or carried by outliers.
    """
    rows = []
    for p in pairs:
        lg, dl = p["legacy"], p["delta"]
        rows.append({
            "session": p["session"],
            "legacy_out_tokens": lg.get("out_tokens"),
            "delta_out_tokens": dl.get("out_tokens"),
            "reduction": _reduction(lg.get("out_tokens") or 0,
                                    dl.get("out_tokens")),
            "legacy_truncated": lg.get("truncated"),
            "delta_truncated": dl.get("truncated"),
            "legacy_s4_items": lg.get("s4_items"),
            "delta_s4_items": dl.get("s4_items"),
            "s2_items": lg.get("s2_items"),
            "delta_verbatim_reemissions": dl.get("verbatim_reemissions"),
            "delta_added_by_s4": dl.get("added_by_s4"),
            "delta_corrected_by_s4": dl.get("corrected_by_s4"),
            # the CENSUS count, never len() of a capped list
            "delta_corrected_items": (dl.get("corrected_items") or {}).get(
                "count"),
            "legacy_error": lg.get("error"),
            "delta_error": dl.get("error"),
        })
    # ⛔ A pair with a FAILED arm is not a measurement of output size, even
    # though the failed arm now keeps its spend (deliberately — the spend is
    # data). The direction matters: a legacy arm that emitted 32 k and then
    # failed to parse would enter `sum_lg` and INFLATE the reduction, which is
    # the flattering direction this instrument exists to prevent. Both
    # committed runs have zero errors, so no published number moves; the row
    # keeps `legacy_error`/`delta_error` and `sessions_attempted` keeps the
    # excluded population visible (#11385 review P2).
    # ⛔ `failed` must be the EXCLUDED population, not the errored one: an arm
    # can be unmeasured with no `error` at all (`arm_out_tokens` returns `None`
    # for a tokenless / rejected-count / deadline-aborted / incomplete-recovery
    # call), so counting errors alone reported "nothing excluded" in exactly the
    # shape the null polarity exists to create (#11385 review P2).
    # ONE keep-mask for BOTH views (`usable` rows and the `pairs` they came
    # from). The same three-clause filter written out twice is the drift this
    # branch keeps repairing: `composition` MUST be summed over exactly the
    # population the token totals use (the docstring of `_composition_totals`
    # states that invariant), and a second, looser filter here would publish a
    # value-channel table describing sessions the headline excludes — while
    # nothing kept the two copies agreeing (#11385 review P2/P3).
    keep = [r["reduction"] is not None
            and not r.get("legacy_error") and not r.get("delta_error")
            for r in rows]
    usable = [r for r, k in zip(rows, keep, strict=True) if k]
    usable_pairs = [p for p, k in zip(pairs, keep, strict=True) if k]
    failed = len(rows) - len(usable)   # the EXCLUDED population, errors or not
    sum_lg = sum(r["legacy_out_tokens"] for r in usable)
    sum_dl = sum(r["delta_out_tokens"] for r in usable)
    reductions = sorted(r["reduction"] for r in usable)
    median = None
    if reductions:
        mid = len(reductions) // 2
        median = (reductions[mid] if len(reductions) % 2
                  else round((reductions[mid - 1] + reductions[mid]) / 2, 4))
    return {
        "sessions_measured": len(usable),
        "sessions_attempted": len(rows),
        "sessions_failed": failed,
        # a partial run is visible in ONE field, so no reader has to subtract
        # ⛔ `0 < … < len(rows)` read False on a run that measured NOTHING, so
        # the one field that exists to make incompleteness visible said
        # "complete" exactly when it was empty (#11385 review P2).
        "partial": len(usable) < len(rows),
        "sum_legacy_out_tokens": sum_lg,
        "sum_delta_out_tokens": sum_dl,
        # THE number the issue asks for:
        "token_weighted_reduction": _reduction(sum_lg, sum_dl),
        "median_session_reduction": median,
        "min_session_reduction": reductions[0] if reductions else None,
        "max_session_reduction": reductions[-1] if reductions else None,
        "composition": _composition_totals(usable_pairs),
        "rows": rows,
    }


# ── commands ───────────────────────────────────────────────────────────────


def _model_for(model_id: str):
    """The direct DeepSeek adapter — the same route the extractor uses when
    DEEPSEEK_API_KEY is set (`build_extractor_model`'s deepseek-direct branch),
    constructed directly so the measurement does not depend on routing config."""
    from tortoise.model_adapters import DeepSeekDirectModel
    return DeepSeekDirectModel(model_id)


# ⛔ The emitted caveats are a module-level constant so a TEST can pin them.
# They were previously an inline list inside `cmd_run`, which made the one claim
# that separates "fixed" from "re-permitted" — the README keeps the artifacts'
# one-sided `caveats[2]` sentence on the strength of "the instrument at this head
# emits a corrected string" — unpinnable: reverting the emitted prose to the
# refuted CEILING wording passed every test (#11385 review P3).
CAVEATS: tuple[str, ...] = (
    "Measured per-session S4 output tokens on RECORDED real S2 geometry "
    "(the 4 tax-surface sections); both arms get the same INPUT CONTENT, but "
    "the delta contract's own instruction is longer, so the delta arm's "
    "prompt is +242 tokens in every session of these runs — a small POSITIVE "
    "input cost, not a saving. The delta measured here is therefore the whole "
    "contract's effect, and the prompt did not get cheaper.",
    "The replayed S2 embed list is the 4 tax sections — 266 of the 311 "
    "items the receipts record (chain_notes 7 and link_before_create 38 "
    "are not in the prompt; retractions 0). Both arms see the same "
    "narrowed list, so the A/B stays single-variable, but it shrinks the "
    "legacy arm's re-emit target and therefore UNDERSTATES the measured "
    "reduction (#11385 review P3).",
    "One call per arm per session (no production-run composition): model "
    "compliance in the wild is not measured here — it is OBSERVABLE per "
    "row via verbatim_reemissions / added_by_s4 / corrected_by_s4 / "
    "s4_delta_reemit_suspect.",
    "The S3 search block is reconstructed from the session's own S2 list "
    "and is NOT production geometry: extract_session_v2 runs S3 "
    "(search_graph) BEFORE S4 and writes the session's items only "
    "afterwards (execute_embed), so production S4 sees bounded "
    "PREVIOUS-graph hits (max_queries=15 x limit=3, the capture's own "
    "turn echoes dropped). Relabelling the session's own list as "
    "already-in-the-graph is the maximally gap-SUPPRESSING input for a "
    "delta contract AND the maximally correction-FRIENDLY one (a "
    "correction is emitted only where the search shows the item exists) "
    "— for the 231 of the 266 items it can offer: the block renders "
    "entities/points/events only, so the 35 operators are never shown "
    "(in production too) and their correction channel is structurally "
    "zero here. So the two value channels are measured at opposite "
    "extremes and the aggregate's direction is not established — "
    "neither a ceiling nor a floor.",
    "model_id is the model REQUESTED; the served id is not recorded here.",
    "Accuracy / chunk@20 are the eval lane's gates and are NOT measured here.",
)


def cmd_run(args: argparse.Namespace) -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("REFUSED: DEEPSEEK_API_KEY is not set — an S4 A/B with no model "
              "call is not a measurement of the model's output size.",
              file=sys.stderr)
        return 2
    sessions = load_sessions(Path(args.receipt), embeds_arm=args.embeds_arm,
                             only=args.sessions)
    model = _model_for(args.model)
    pairs: list[dict] = []
    for sess in sessions:
        pair: dict = {"session": sess["session"]}
        for arm in _ARM_CHOICES:
            print(f"  {sess['session']} · {arm} …", file=sys.stderr, flush=True)
            pair[arm] = run_arm(model, sess, arm)
            r = pair[arm]
            print(f"    → out={r.get('out_tokens')} items={r.get('s4_items')} "
                  f"trunc={r.get('truncated')} {r.get('elapsed_s')}s "
                  f"{r.get('error') or ''}", file=sys.stderr, flush=True)
        pairs.append(pair)
    summary = compare(pairs)
    report = {
        "instrument": "tools/s4_reeemit_tax.py",
        "issue": "#1789",
        "measured_at": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_id": args.model,
        "receipt_dir": str(args.receipt),
        "embeds_arm": args.embeds_arm,
        "contracts": {
            "legacy": "S4_TMPL (complete-list re-emit) — TORTOISE_S4_DELTA=0",
            "delta": "S4_TMPL_DELTA (gaps + corrections only) — shipped default",
        },
        # ⛔ `run_s4` does not take `core_only`, so `render_s4_prompt` falls back
        # to `_classify_later_enabled()` — a SECOND env switch that selects a
        # different TEMPLATE PAIR (`S4_TMPL_CORE_ONLY{,_DELTA}`). Both arms see
        # the same value, so the A/B stays one-variable, but without this the
        # artifact cannot say whether the magnitude is reproducible on another
        # box (#11385 review P2).
        "classify_later": bool(x._classify_later_enabled()),
        "templates": {
            "legacy": "S4_TMPL_CORE_ONLY" if x._classify_later_enabled()
            else "S4_TMPL",
            "delta": "S4_TMPL_CORE_ONLY_DELTA" if x._classify_later_enabled()
            else "S4_TMPL_DELTA",
        },
        "inputs": [
            {"session": s["session"], "receipt": s["receipt"],
             "s2_items": sum(len(v) for v in s["s2"].values()),
             "s2_reference": s["s2_reference"], "turns": s["turns"]}
            for s in sessions
        ],
        # every per-arm observation, so the artifact is auditable without
        # re-running (the token totals are only trustworthy if the rows behind
        # them are inspectable), and the ITEMS so the Task 10 Step 5 partition
        # can be done offline.
        "arms": [
            {"session": p["session"], "legacy": p["legacy"],
             "delta": p["delta"]}
            for p in pairs
        ],
        "summary": summary,
        "caveats": list(CAVEATS),
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1) + "\n")
        print(f"wrote {out}", file=sys.stderr)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"},
                     indent=1))
    # FAIL-CLOSED: a run whose every arm failed measured NOTHING, and exit 0
    # there would let a broken credential or a dead endpoint read as "the tax
    # was measured, reduction is null".
    # a PARTIAL run is publishable but must never read as a complete one: the
    # population is already in the artifact (`sessions_measured`/`attempted`),
    # and this makes it impossible to miss (#11385 review P2).
    if 0 < summary["sessions_measured"] < summary["sessions_attempted"]:
        print(f"WARNING: PARTIAL — {summary['sessions_measured']} of "
              f"{summary['sessions_attempted']} sessions produced a usable "
              "pair; the headline describes the measured ones only.",
              file=sys.stderr)
    if summary["sessions_measured"] == 0:
        print("FAILED: 0 sessions produced a usable pair — no measurement was "
              "made (see the per-arm errors above; a 401/transport failure is "
              "NOT a zero result).", file=sys.stderr)
        return 1
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Both arms' PROMPT geometry, no model call and no key.

    This is the input-side half of the tax (the delta template is longer than
    the legacy one) and the CI-runnable proof that the two renders really are
    different documents — a cheap pin that cannot rot into "both arms render the
    same prompt", which would make the whole measurement vacuous.
    """
    sessions = load_sessions(Path(args.receipt), embeds_arm=args.embeds_arm,
                             only=args.sessions)
    rows = []
    for sess in sessions:
        search = search_from_s2(sess["s2"])
        legacy = x.render_s4_prompt(sess["story"], search, sess["s2"],
                                    session_date=sess["session_date"],
                                    delta=False)
        delta = x.render_s4_prompt(sess["story"], search, sess["s2"],
                                   session_date=sess["session_date"],
                                   delta=True)
        rows.append({
            "session": sess["session"],
            "s2_items": sum(len(v) for v in sess["s2"].values()),
            "legacy_prompt_chars": len(legacy),
            "delta_prompt_chars": len(delta),
            "delta_minus_legacy_pct": round(
                100.0 * (len(delta) - len(legacy)) / len(legacy), 3),
            "renders_differ": legacy != delta,
            "legacy_has_reexmit_mandate": (
                "the COMPLETE embed list" in legacy),
            "delta_has_reexmit_mandate": (
                "the COMPLETE embed list" in delta),
            "delta_has_delta_mandate": "EMIT ONLY DELTAS" in delta,
        })
    report = {"instrument": "tools/s4_reeemit_tax.py dry-run",
              "issue": "#1789", "rows": rows}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("dry-run", cmd_dry_run)):
        s = sub.add_parser(name)
        s.add_argument("--receipt", default=_DEFAULT_RECEIPT,
                       help="write-path eval receipt dir (default: the "
                            "7947-arms real-session receipts)")
        s.add_argument("--embeds-arm", default="narrative",
                       choices=("narrative", "raw"),
                       help="which recorded S2 arm to replay (default narrative)")
        s.add_argument("--sessions", default=None,
                       help="comma-separated session names (default: all)")
        s.add_argument("--out", default=None, help="write the JSON report here")
        if name == "run":
            s.add_argument("--model", default="deepseek-v4-flash",
                           help="extractor model id (default deepseek-v4-flash)")
        s.set_defaults(fn=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sessions:
        args.sessions = [s.strip() for s in args.sessions.split(",") if s.strip()]
    try:
        return args.fn(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""#1779 — the cross-question haystack-overlap census, and the granularity verdict.

**Why this exists.** #1779 (filed as a #1746 follow-up) asked for
"session-dedup/caching across eval questions" and made the measurement the
precondition: *"measure the overlap first (what fraction of sessions/prompts
repeat across the 50/500-Q instances), then decide cache granularity
(session-level extracted-embed reuse vs prompt-level response cache)"*. The
"5-10× redundant encodes" that ``encode_cache.py`` / ``README.md`` /
``run.py`` all cited as the cache's justification was never measured — it was
asserted in ``docs/plans/2026-08-17-1349-embedder-selection.md:63`` and copied
forward. This module is the measurement, and its ``decision`` block is the
granularity answer the issue asks for.

**What it counts** (all from the dataset + the PRODUCT's own renderers, so the
counted unit is the encoded unit and there is no second implementation to
drift):

  * **session slots** — one haystack session per question;
  * **session identity** — ``sha256(ingest._session_transcript(session))``,
    the text the ingest writes (role-prefixed turns);
  * **session prompt identity** — that transcript **plus the session date**. The
    date is prompt-visible: ``extractor_v2._date_anchor`` renders
    "**DATE ANCHOR — today is `<session_date>`**" into the S1/S2/S4 *system*
    prompt whenever the session is dated (a slot whose date is missing renders
    no anchor, and the census counts it under the empty date rather than
    assuming it away). So a response cache keyed on prompt text reuses a
    session only when the content **and** the date match;
  * **chunk prompt identity** — the raw-chunk text **plus the same session
    date**, i.e. the ``(text, date)`` joint over the chunk leg. ⛔ This is the
    cell the ``prompt_level_response_cache`` verdict is derived from, because
    the prompt the harness actually renders is **not** a whole-session
    transcript: ``ingest_v2`` hands each session to ``extract_session_v2``,
    which splits it into EDU chunks and calls ``run_s1`` **once per chunk**
    with the session date (``extractor_v2.chunk_transcript`` → ``_arm_extract``
    → ``run_s1(..., session_date=...)``, whose system prompt is
    ``S1_TMPL.replace("{date_anchor}", _date_anchor(session_date))``). A
    session-level identity repeating 1.000× therefore does NOT settle the
    prompt unit — P(a session repeats) ≤ P(a chunk repeats), so the session
    cell can only understate chunk reuse. The exact S1 unit is not
    dataset-derivable (its chunks are ``chunk_transcript`` over **LLM-derived**
    EDUs), so the raw-chunk leg is the finest cell a dataset-only census can
    measure — the same 'dataset-derivable only' boundary the encode-unit
    denominator states. ``extract_session_v2`` is the **v2** leg's extractor
    (``ingest_v2.py`` → ``extract_session_v2``); the v1 leg
    (``ingest.py``) does no LLM extraction at all — it writes deterministic
    episodic turns and raw chunks only — so the S1 prompt unit is the v2
    path's granularity. That makes the response-cache avenue *more* clearly
    unreachable, not less: the v1 leg renders no prompt to cache;
  * **encode units** — the two texts ``ingest_haystack`` /
    ``ingest_haystack_v2`` embed per session: one turn Point per turn
    (``ingest._turn_text``) and one raw chunk per ``chunk_turns`` window
    (``ingest._session_chunks``). The denominator is the **dataset-derivable**
    set ONLY: a ``v2`` run also embeds LLM-derived extraction Point texts
    (``ingest_v2`` → ``sdk.create_point``, and ``encode_cache`` wraps
    ``tortoise.embeddings`` process-wide), and those cannot be computed from
    the dataset, so they sit OUTSIDE this census. Every published share below
    is therefore "of the dataset-derivable encode units", not of a whole v2
    run.

**The measured answer (S split, 500 Q, 2026-11-08):** see ``CENSUS``. In one
pass: 23,867 session slots, 18,464 distinct session contents (1.2926×),
5,403 duplicate slots — but **0** of the repeated pairs share a session date,
so the SESSION prompt identity is exactly 1.000× and a session-level
extraction cache has nothing to hit. The **S1 prompt** is a finer unit (one
prompt per EDU chunk, not per session), so its cell is measured separately:
124,345 chunk prompts, 124,318 distinct ``(chunk text, session date)``
identities — 1.0002×, i.e. **27 repeats, 0.02% of the leg**. A prompt-level
response cache is therefore arithmetically reachable and worth nothing: below
``MATERIAL_HIT_SHARE``, which is the verdict the issue asked for, computed on
the unit the harness actually renders. The text→embedding cache (already
built) is the one realizable lever, at 1.299× / 23.0% of encode units.

**What this module is NOT.** Not a run instrument and not a gate: it reads the
dataset only (no DB, no LLM, no keys) and writes nothing unless ``--out`` is
passed. ``CENSUS`` is the pinned record a ``@slow`` test re-derives, so a
dataset refresh that changes the overlap reds a test instead of silently
re-basing a published number.

Usage::

    python -m tools.longmem_eval.haystack_overlap              # human summary
    python -m tools.longmem_eval.haystack_overlap --json        # census JSON
    python -m tools.longmem_eval.haystack_overlap --limit 50 --out /tmp/c.json
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/haystack_overlap.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python -m tools.longmem_eval.haystack_overlap`"
    )

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from . import dataset as lme_dataset
from .ingest import (
    DEFAULT_CHUNK_TURNS,
    _session_chunks,
    _session_transcript,
    _turn_text,
)

#: Measurement date. Any number quoted in a doc must name this census.
FINDINGS_DATE = "2026-11-08"

#: A SESSION-level prompt is identified by the transcript the prompts carry
#: PLUS the session date the date anchor injects (`extractor_v2._date_anchor`)
#: — both are inputs of `render_s2_prompt` / the S1/S4 system prompt.
PROMPT_IDENTITY = "rendered session transcript + session_date"

#: The S1 prompt is rendered per EDU CHUNK, not per session
#: (`extract_session_v2` → `chunk_transcript` → `run_s1`), so the identity the
#: response-cache verdict turns on is the chunk text plus the same date.
CHUNK_PROMPT_IDENTITY = "rendered S1 chunk text + session_date"

#: How much of an avenue's OWN unit population must hit before the avenue is
#: worth building at all. `realizable` is `reuse > 1.0 AND hit_share >= this`:
#: reuse polarity alone is not a decision, because an identity that repeats 27
#: times out of 124,345 is arithmetically realizable and practically worthless —
#: reporting it as "realizable" would be the same overstatement this module
#: exists to retire, only pointed the other way (PR #11577 review). The floor is
#: DECLARED once, here, and every published verdict quotes the measured share it
#: was applied to, so the rule is visible instead of living in a doc's prose.
#: 1% is deliberately far below the cache this lane already ships (the
#: text→embedding cache measures 23.0% of encode units in one pass), so the
#: rule can only ever reject an avenue that is an order of magnitude weaker.
MATERIAL_HIT_SHARE = 0.01

#: The measured S-split census (2026-11-08, 500 questions, chunk_turns=2).
#: Pinned by `tests/test_haystack_overlap_1779.py::test_census_on_real_s_split`
#: (a @slow test — CI has no dataset) so the numbers in the docs cannot rot
#: silently, and so a dataset refresh surfaces as a red pin rather than as a
#: quietly re-based published reuse factor. The `decision` block is NOT pinned
#: as prose: its reason is DERIVED from these numbers (`_decision`), so the
#: verdict fields are asserted directly in the same test.
CENSUS: dict[str, Any] = {
    "findings_date": FINDINGS_DATE,
    "n_instances": 500,
    "chunk_turns": 2,
    "sessions": {
        "slots": 23867,
        "distinct_ids": 19195,
        "distinct_contents": 18464,
        "duplicate_content_slots": 5403,
        "duplicate_content_groups": 4375,
        "repeated_pairs": 6660,
        "same_date_pairs": 0,
        "distinct_content_date": 23867,
        "within_question_duplicate_slots": 13,
        "questions_with_repeated_content": 500,
        "repeat_histogram": {1: 14089, 2: 3533, 3: 688, 4: 130, 5: 18,
                             6: 5, 8: 1},
        "content_reuse_factor": 1.2926,
        "prompt_reuse_factor": 1.0,
    },
    # The date population. Measured on the same run as the rest of this block;
    # it is what stops the 1.000× prompt verdict being an artefact of unique
    # dates, so it is pinned with them. Value inherited from that run — this box
    # has no cached dataset to re-derive it, which is exactly what the @slow pin
    # is for (a re-run that disagrees reds the pin rather than silently
    # re-basing a published number).
    "dates": {"slots": 23867, "distinct": 18360, "reuse_factor": 1.2999},
    "encode_units": {
        "turn": {"slots": 246750, "distinct": 189522, "hit_slots": 57228,
                 "reuse_factor": 1.302, "hit_share": 0.2319},
        "chunk": {"slots": 124345, "distinct": 96086, "hit_slots": 28259,
                  "reuse_factor": 1.2941, "hit_share": 0.2273,
                  "distinct_content_date": 124318,
                  "prompt_reuse_factor": 1.0002},
        "total": {"slots": 371095, "distinct": 285608, "hit_slots": 85487,
                  "reuse_factor": 1.2993, "hit_share": 0.2304,
                  "chunk_share_of_units": 0.3351},
    },
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _leg(counter: Counter[str], **extra: Any) -> dict[str, Any]:
    """The slots/distinct/hit/reuse shape every leg reports (one definition, so
    the turn and chunk legs cannot disagree about what a hit is)."""
    slots = sum(counter.values())
    distinct = len(counter)
    hits = slots - distinct
    return {
        "slots": slots,
        "distinct": distinct,
        "hit_slots": hits,
        "reuse_factor": round(slots / distinct, 4) if distinct else 0.0,
        "hit_share": round(hits / slots, 4) if slots else 0.0,
        **extra,
    }


def _histogram(counts) -> dict[int, int]:
    """How many identities repeat how many times (``{repeats: identities}``)."""
    return dict(sorted(Counter(counts).items()))


def census_overlap(instances: list[dict],
                   *, chunk_turns: int = DEFAULT_CHUNK_TURNS) -> dict[str, Any]:
    """Measure haystack overlap across ``instances`` (a loaded dataset slice).

    Pure and deterministic: same instances in, same census out. Length
    mismatches between ``haystack_sessions`` / ``haystack_session_ids`` /
    ``haystack_dates`` are tolerated (a missing date is ``""``; a missing id is
    ``""``) because this is a census of what the reader is HANDED — a
    misaligned instance is a datum, not a reason to refuse. The ingest's own
    alignment checks are `dataset_audit`'s job.

    A ``None`` date and an absent date are the SAME prompt: `_date_anchor`
    (`extractor_v2.py:1466-1467`) renders ``""`` for any falsy value, so the
    identity function here is ``str(date or "")`` — not ``str(date)``, which
    would read ``"None"`` as a date the product never renders (review finding,
    PR #11577).
    """
    if chunk_turns < 1:
        raise ValueError(f"chunk_turns must be >= 1, got {chunk_turns!r}")

    id_counter: Counter[str] = Counter()
    content_counter: Counter[str] = Counter()
    date_counter: Counter[str] = Counter()
    prompt_counter: Counter[tuple[str, str]] = Counter()
    content_dates: dict[str, Counter[str]] = {}
    turn_units: Counter[str] = Counter()
    chunk_units: Counter[str] = Counter()
    # The JOINT the `prompt_level_response_cache` verdict is derived from: S1
    # renders one prompt per chunk, so the identity is (chunk text, date).
    chunk_prompts: Counter[tuple[str, str]] = Counter()

    per_question: list[set[str]] = []
    within_question_dups = 0

    for q in instances:
        sessions = list(q.get("haystack_sessions") or [])
        ids = list(q.get("haystack_session_ids") or [])
        dates = list(q.get("haystack_dates") or [])
        seen_here: set[str] = set()
        q_contents: set[str] = set()

        for i, session in enumerate(sessions):
            transcript = _session_transcript(session)
            digest = _sha(transcript)
            date = str(dates[i] or "") if i < len(dates) else ""
            sid = str(ids[i]) if i < len(ids) else ""

            id_counter[sid] += 1
            content_counter[digest] += 1
            date_counter[date] += 1
            prompt_counter[(digest, date)] += 1
            content_dates.setdefault(digest, Counter())[date] += 1
            q_contents.add(digest)
            if digest in seen_here:
                within_question_dups += 1
            seen_here.add(digest)

            for turn in session:
                turn_units[_turn_text(str(turn.get("role") or "unknown"),
                                      str(turn.get("content") or ""))] += 1
            for _ci, text, _idxs in _session_chunks(session, chunk_turns):
                chunk_units[text] += 1
                chunk_prompts[(_sha(text), date)] += 1

        per_question.append(q_contents)

    slots = sum(content_counter.values())
    distinct_contents = len(content_counter)
    duplicate_content_slots = slots - distinct_contents
    # Any question whose sessions include a content seen more than once in the
    # corpus — a within-question repeat counts too (it is still a repeat; the
    # separate `within_question_duplicate_slots` readout says which kind).
    questions_with_repeated = sum(
        1 for contents in per_question
        if any(content_counter[d] > 1 for d in contents))

    # Repeated pairs, and how many of them share a date — the measurement the
    # prompt-cache verdict turns on. A pair sharing (content, date) renders the
    # SAME prompt; a pair differing only in date does not.
    repeated_pairs = 0
    same_date_pairs = 0
    for digest, n in content_counter.items():
        if n < 2:
            continue
        repeated_pairs += n * (n - 1) // 2
        for date_n in content_dates[digest].values():
            if date_n > 1:
                same_date_pairs += date_n * (date_n - 1) // 2

    turn_leg = _leg(turn_units)
    chunk_prompt_slots = sum(chunk_prompts.values())
    chunk_prompt_distinct = len(chunk_prompts)
    chunk_prompt_reuse = (round(chunk_prompt_slots / chunk_prompt_distinct, 4)
                          if chunk_prompt_distinct else 0.0)
    chunk_leg = _leg(chunk_units,
                     distinct_content_date=chunk_prompt_distinct,
                     prompt_reuse_factor=chunk_prompt_reuse)
    total_leg = _leg(turn_units + chunk_units)
    total_leg["chunk_share_of_units"] = (
        round(chunk_leg["slots"] / total_leg["slots"], 4)
        if total_leg["slots"] else 0.0)

    prompt_slots = sum(prompt_counter.values())
    prompt_distinct = len(prompt_counter)
    prompt_reuse = round(prompt_slots / prompt_distinct, 4) if prompt_distinct else 0.0

    decision = _decision(
        duplicate_content_slots=duplicate_content_slots,
        repeated_pairs=repeated_pairs,
        same_date_pairs=same_date_pairs,
        session_prompt={"slots": prompt_slots, "distinct": prompt_distinct,
                        "reuse_factor": prompt_reuse},
        chunk_prompt={"slots": chunk_prompt_slots,
                      "distinct": chunk_prompt_distinct,
                      "reuse_factor": chunk_prompt_reuse},
        encode=total_leg,
    )

    return {
        "findings_date": FINDINGS_DATE,
        "n_instances": len(instances),
        "chunk_turns": chunk_turns,
        "sessions": {
            "slots": slots,
            "distinct_ids": len(id_counter),
            "distinct_contents": distinct_contents,
            "duplicate_content_slots": duplicate_content_slots,
            "duplicate_content_groups": sum(
                1 for n in content_counter.values() if n > 1),
            "repeated_pairs": repeated_pairs,
            "same_date_pairs": same_date_pairs,
            "distinct_content_date": prompt_distinct,
            "within_question_duplicate_slots": within_question_dups,
            "questions_with_repeated_content": questions_with_repeated,
            "repeat_histogram": _histogram(content_counter.values()),
            "content_reuse_factor": round(slots / distinct_contents, 4)
            if distinct_contents else 0.0,
            "prompt_reuse_factor": prompt_reuse,
        },
        # The date population, as its own published cell: the 1.000× verdict
        # would be an ARTEFACT if every session carried a unique date, so the
        # count that rules that out is measured here and pinned in ``CENSUS``
        # like every other number the docs quote — a published figure with no
        # producer and no pin is the drift this module exists to retire.
        "dates": {
            "slots": slots,
            "distinct": len(date_counter),
            "reuse_factor": round(slots / len(date_counter), 4)
            if date_counter else 0.0,
        },
        "encode_units": {
            "turn": turn_leg,
            "chunk": chunk_leg,
            "total": total_leg,
        },
        "decision": decision,
    }


def _avenue(identity: str, cell: dict[str, Any], *,
            blocked_by: str | None) -> dict[str, Any]:
    """One avenue's verdict, DERIVED from its own measured cell.

    ``realizable`` answers the only question a decision needs — would building
    this cache pay? — so it requires the identity to repeat **materially**
    (``MATERIAL_HIT_SHARE``), not merely to repeat. The measured magnitude is
    always published beside it (``hit_slots`` / ``hit_share``), so a reader never
    has to trust the boolean: 27 hits out of 124,345 is realizable in arithmetic
    and worthless in practice, and the row says both.
    """
    slots = cell["slots"]
    hits = slots - cell["distinct"]
    share = round(hits / slots, 4) if slots else 0.0
    return {
        "identity": identity,
        "reuse_factor": cell["reuse_factor"],
        "hit_slots": hits,
        "hit_share": share,
        "realizable": cell["reuse_factor"] > 1.0 and share >= MATERIAL_HIT_SHARE,
        "blocked_by": blocked_by,
        "materiality_floor": MATERIAL_HIT_SHARE,
    }


def _decision(*, duplicate_content_slots: int, repeated_pairs: int,
              same_date_pairs: int, session_prompt: dict[str, Any],
              chunk_prompt: dict[str, Any],
              encode: dict[str, Any]) -> dict[str, Any]:
    """The cache-granularity verdict, DERIVED from the census numbers.

    ⛔ The two LLM avenues do NOT share one identity condition. A session's
    extraction can only be reused when its **whole-session** prompt is identical
    — the SESSION cell (transcript + date) settles that. The prompt a response
    cache would store is rendered **per EDU chunk**
    (``extract_session_v2`` → ``chunk_transcript`` → ``run_s1``), so that avenue
    is settled by the CHUNK cell ``(chunk text, session date)``: a session-level
    1.000× does not imply a chunk-level 1.000× (P(session repeats) ≤ P(chunk
    repeats)), and deriving it from the coarser cell asserts a verdict beyond
    the measured cell — the 'published number with no producer' class this
    module exists to retire (PR #11577 review, following the earlier finding
    that the session cell had been used as a proxy for the prompt unit).
    """
    session_blocked = None
    if session_prompt["reuse_factor"] <= 1.0:
        if duplicate_content_slots:
            session_blocked = (
                f"{duplicate_content_slots} session slots repeat another slot's "
                f"content, but {same_date_pairs} of {repeated_pairs} repeated "
                "pairs share a session date — and the date is prompt-visible "
                "(extractor_v2._date_anchor injects it into the S1/S2/S4 system "
                "prompt), so no two repeated sessions render the same prompt")
        else:
            session_blocked = "no cross-question session repeats (nothing to reuse)"
    elif ((session_prompt["slots"] - session_prompt["distinct"])
          / max(session_prompt["slots"], 1)) < MATERIAL_HIT_SHARE:
        session_blocked = (
            "the session prompt identity repeats, but on "
            f"{session_prompt['slots'] - session_prompt['distinct']} of "
            f"{session_prompt['slots']} slots — below the "
            f"{MATERIAL_HIT_SHARE:.0%} materiality floor")

    chunk_blocked = None
    if chunk_prompt["reuse_factor"] <= 1.0:
        chunk_blocked = (
            f"{chunk_prompt['slots']} S1 chunk prompts render from the chunk "
            f"leg and only {chunk_prompt['distinct']} distinct (chunk text, "
            "session date) identities exist: the date is prompt-visible "
            "(extractor_v2._date_anchor renders into run_s1's system prompt), "
            "so a repeated chunk text carried by a differently-dated session "
            "is a different prompt")
    elif ((chunk_prompt["slots"] - chunk_prompt["distinct"])
          / max(chunk_prompt["slots"], 1)) < MATERIAL_HIT_SHARE:
        chunk_blocked = (
            "the chunk prompt identity repeats, but on only "
            f"{chunk_prompt['slots'] - chunk_prompt['distinct']} of "
            f"{chunk_prompt['slots']} S1 chunk prompts "
            f"({(chunk_prompt['slots'] - chunk_prompt['distinct']) / max(chunk_prompt['slots'], 1):.2%})"
            f" — below the {MATERIAL_HIT_SHARE:.0%} materiality floor, so a "
            "response cache would save a rounding error of the LLM work")

    return {
        "session_level_extraction_reuse": _avenue(
            PROMPT_IDENTITY, session_prompt, blocked_by=session_blocked),
        "prompt_level_response_cache": _avenue(
            CHUNK_PROMPT_IDENTITY, chunk_prompt, blocked_by=chunk_blocked),
        "encode_cache_text_keyed": {
            **_avenue("sha256(model_id + prompt_name + text)", encode,
                      blocked_by=None),
            "already_implemented": "tools/longmem_eval/encode_cache.py",
        },
    }


def _summary(census: dict[str, Any]) -> str:
    s, e, d = census["sessions"], census["encode_units"], census["decision"]
    lines = [
        f"#1779 haystack-overlap census — {census['findings_date']}"
        f" ({census['n_instances']} questions, chunk_turns={census['chunk_turns']})",
        f"  session slots                 : {s['slots']}",
        f"  distinct session contents     : {s['distinct_contents']}"
        f"  (reuse {s['content_reuse_factor']}x, "
        f"{s['duplicate_content_slots']} duplicate slots)",
        f"  repeated pairs / same-date    : {s['repeated_pairs']} / "
        f"{s['same_date_pairs']}",
        f"  questions w/ a repeat         : "
        f"{s['questions_with_repeated_content']}/{census['n_instances']}",
        f"  prompt identity reuse (session): {s['prompt_reuse_factor']}x",
        f"  prompt identity reuse (S1 chunk): "
        f"{e['chunk']['prompt_reuse_factor']}x "
        f"({e['chunk']['slots'] - e['chunk']['distinct_content_date']} of "
        f"{e['chunk']['slots']} chunk prompts repeat)",
        f"  encode units (total)          : {e['total']['slots']}"
        f"  distinct {e['total']['distinct']}"
        f"  reuse {e['total']['reuse_factor']}x"
        f"  hits {e['total']['hit_share']:.1%}",
        f"  chunk leg share of units      : {e['total']['chunk_share_of_units']:.1%}",
        "",
        "granularity verdict:",
    ]
    for name in ("session_level_extraction_reuse",
                 "prompt_level_response_cache", "encode_cache_text_keyed"):
        row = d[name]
        verdict = "REALIZABLE" if row["realizable"] else "NOT realizable"
        lines.append(f"  {name:32s} reuse {row['reuse_factor']}x — {verdict}")
        if row.get("blocked_by"):
            lines.append(f"      {row['blocked_by']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Measure cross-question haystack overlap (#1779).")
    ap.add_argument("--split", default=lme_dataset.DEFAULT_SPLIT,
                    help="dataset split to load (default: s)")
    ap.add_argument("--data", default=None,
                    help="local dataset JSON/JSONL (skips the cache/download)")
    ap.add_argument("--limit", type=int, default=None,
                    help="truncate the instance list (smoke runs)")
    ap.add_argument("--chunk-turns", type=int, default=DEFAULT_CHUNK_TURNS,
                    help=f"raw-chunk window width (default: {DEFAULT_CHUNK_TURNS})")
    ap.add_argument("--json", action="store_true",
                    help="print the census JSON instead of the summary")
    ap.add_argument("--download", action="store_true",
                    help="fetch the split from HuggingFace when it is not cached "
                         "(default: refuse — a census must not silently pull "
                         "hundreds of MB)")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the census JSON here")
    args = ap.parse_args(argv)

    instances = lme_dataset.load_dataset(
        split=args.split, limit=args.limit, data_path=args.data,
        download=args.download)
    # #1779: an EMPTY population is not a measurement, and a census over zero
    # instances publishes a full, authoritative-looking VERDICT (`0.0x — NOT
    # realizable`) plus a fabricated explanation — an empty or mistyped read
    # would invert the built cache's published "realizable" with exit 0. Fail
    # closed with a refusal instead: a census must never report on a
    # population it did not read.
    if not instances:
        print(
            "[haystack_overlap] refusing to publish a census over 0 instances "
            "— no dataset read (check --data/--split, or drop --limit)",
            file=sys.stderr,
        )
        return 2
    census = census_overlap(instances, chunk_turns=args.chunk_turns)
    payload = json.dumps(census, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
        print(f"[haystack_overlap] census written: {args.out}", file=sys.stderr)
    print(payload if args.json else _summary(census))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

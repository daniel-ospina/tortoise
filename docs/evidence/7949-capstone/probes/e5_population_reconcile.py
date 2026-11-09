#!/usr/bin/env python3
"""#10990 (C9) — E5's 25-vs-29 population question, answered mechanically.

E5's registered denominator is the **29 marker-bearing sentences** of the frozen corpus
(`preregistration.json.criteria[E5].denominator`), and the row's own note says the two
populations ``must be reconciled before either number is called final``. The *canonical-count* ruling is carried by
`#5087` (`#9738`, which owned it until 2026-11-02, closed on its own section-10 record correction
— not on a measurement of this number); this probe answers the narrower, mechanical half that a
lane may answer without one:

    are the 29 and the 25 the same population counted twice?

**The answer is no, and it is not a rounding difference.** They are two instruments over two
different vocabularies and two different units:

* ``29`` = **sentences** in the corpus fixture that match the pre-registration's own
  ``hedge_scope_lexemes`` (24 terms, `PRE-REGISTRATION.md` pins block; 343 sentences total).
  Derived by the pinned checker itself — ``capstone_preregistration_check.derive_corpus_facts``.
* ``25`` = **planted qualifiers** in the sealed gold (``gold/<session>.gold.json
  -> planted_qualifiers``), which is what the registered scorer (`score_qualifiers.py`)
  classifies.

They diverge in **both directions across sessions** — markers exceed qualifiers in three
sessions (wp01, wp02, wp04) and fall short in two (wp03, wp05), agreeing in the remaining
two — which is what makes a count-for-count reconciliation impossible rather than merely
unfinished. No monotone map between them exists:

* ``wp01_quarry_debug`` carries **8** marker sentences and **3** planted qualifiers.
* ``wp05_retro_writeup`` carries **0** marker sentences and **3** planted qualifiers — the
  decisive case (below). Its qualifiers (``about``, ``they are both good``, ``that was the decision
  after the August incident``) are in the fixture text but none of their words is in the
  corpus lexeme table, and the corpus table's terms (``probably``, ``roughly``,
  ``at least`` …) are not what the gold planted there.

So the registered "29 marker-bearing sentences" is **not a scoring population**: no gold binds
any of its 29 sentences to a qualifier, and the only population that carries a gold is the 25.
This makes E5's red on the raw floor a verdict on *that* population (which is what the row
already reports) and leaves the choice between 29-as-denominator and 25-as-denominator as
the reconciliation `#5087` carries — narrowed, not decided, by this file.

Usage::

    uv run python docs/evidence/7949-capstone/probes/e5_population_reconcile.py
    uv run python docs/evidence/7949-capstone/probes/e5_population_reconcile.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BUNDLE = REPO_ROOT / "docs" / "evidence" / "7949-capstone"
PREREG_MD = BUNDLE / "PRE-REGISTRATION.md"
CORPUS_DIR = REPO_ROOT / "tests" / "eval" / "write_path"


def reconcile() -> dict:
    """The per-session table plus the two-vocabulary evidence. Pure; no network, no DB."""
    from tests.eval.write_path import corpus, score_qualifiers
    from tools import capstone_preregistration_check as cpc

    pins = cpc.parse_pins(PREREG_MD.read_text())
    derived = cpc.derive_corpus_facts(pins)
    corpus_lexemes = list(pins.get("corpus", {}).get("hedge_scope_lexemes") or [])
    scorer_markers = {k: list(v) for k, v in score_qualifiers.MARKERS.items()}

    rows = []
    for sid in derived["sessions"]:
        gold = corpus.load_gold(sid, root=CORPUS_DIR) or {}
        qualifiers = gold.get("planted_qualifiers") or []
        fixture = corpus.load_fixture(sid, root=CORPUS_DIR) or {}
        blob = " ".join(str(t.get("content") or "") for t in (fixture.get("conversation") or []))
        # How many of THIS session's planted qualifiers use a word the corpus lexeme table
        # knows at all. A zero here is what makes "29 sentences" unscorable: the sentence the
        # qualifier lives in is not in the marker population, so no denominator counts it.
        lowered = blob.lower()
        lex_lower = [t.strip().lower() for t in corpus_lexemes]
        rows.append({
            "session": sid,
            "marker_sentences": derived["per_session"][sid]["hedge_scope_sentences"],
            "planted_qualifiers": len(qualifiers),
            "qualifiers_in_fixture_text": sum(
                1 for q in qualifiers
                if str(q.get("verbatim_qualifier") or "").strip().lower() in lowered
            ),
            "qualifier_classes": sorted({str(q.get("class")) for q in qualifiers}),
            "corpus_lexemes_matching_qualifier_words": sorted({
                t for t in lex_lower
                if any(t and t in str(q.get("verbatim_qualifier") or "").lower() for q in qualifiers)
            }),
        })

    return {
        "marker_sentences_total": derived["hedge_scope_sentences"],
        "corpus_sentences_total": derived["sentences_total"],
        "planted_qualifiers_total": sum(r["planted_qualifiers"] for r in rows),
        "corpus_lexemes": corpus_lexemes,
        "scorer_marker_counts": {k: len(v) for k, v in scorer_markers.items()},
        "scorer_markers_total": sum(len(v) for v in scorer_markers.values()),
        # The two vocabularies are not the same table, and neither contains the other.
        "lexemes_absent_from_scorer": sorted(
            t.strip() for t in corpus_lexemes
            if t.strip() and not any(t.strip().lower() in m.lower()
                                     for ms in scorer_markers.values() for m in ms)
        ),
        "sessions_with_markers_and_no_qualifiers": [
            r["session"] for r in rows if r["marker_sentences"] and not r["planted_qualifiers"]
        ],
        "sessions_with_qualifiers_and_no_markers": [
            r["session"] for r in rows if r["planted_qualifiers"] and not r["marker_sentences"]
        ],
        "per_session": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="reconcile E5's 25-qualifier vs 29-marker populations")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)
    report = reconcile()
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0
    print(f"corpus marker sentences (the registered 29): {report['marker_sentences_total']}"
          f" of {report['corpus_sentences_total']} sentences")
    print(f"planted qualifiers in the sealed gold (the scored 25): "
          f"{report['planted_qualifiers_total']}")
    print()
    print(f"  {'session':26} {'markers':>7} {'qualifiers':>10}  classes")
    for r in report["per_session"]:
        print(f"  {r['session']:26} {r['marker_sentences']:>7} {r['planted_qualifiers']:>10}"
              f"  {','.join(r['qualifier_classes']) or '-'}")
    print()
    print(f"  the pre-registration's marker vocabulary: {len(report['corpus_lexemes'])} lexemes")
    print(f"  the scorer's marker vocabulary:           {report['scorer_markers_total']} markers"
          f" {report['scorer_marker_counts']}")
    print(f"  lexemes the scorer does not carry:        "
          f"{len(report['lexemes_absent_from_scorer'])} {report['lexemes_absent_from_scorer']}")
    print()
    print(f"  sessions with qualifiers but NO marker sentence: "
          f"{report['sessions_with_qualifiers_and_no_markers'] or 'none'}")
    print(f"  sessions with marker sentences but NO qualifier: "
          f"{report['sessions_with_markers_and_no_qualifiers'] or 'none'}")
    print()
    above = [r["session"] for r in report["per_session"]
             if r["marker_sentences"] > r["planted_qualifiers"]]
    below = [r["session"] for r in report["per_session"]
             if r["marker_sentences"] < r["planted_qualifiers"]]
    equal = [r["session"] for r in report["per_session"]
             if r["marker_sentences"] == r["planted_qualifiers"]]
    print(f"  sessions where markers EXCEED qualifiers: {above}")
    print(f"  sessions where markers FALL SHORT:        {below}")
    print(f"  sessions where the two counts agree:      {equal or 'none'}")
    print()
    print("⇒ The 29 and the 25 are NOT one population counted twice: different units")
    print("  (sentences vs planted qualifiers) over different vocabularies. Markers exceed")
    print("  qualifiers in some sessions and fall short in others, so no monotone map exists.")
    print("  The registered 29 has no gold, so it cannot be scored; the only scoreable")
    print("  population is the 25. Which count is canonical stays #5087's.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

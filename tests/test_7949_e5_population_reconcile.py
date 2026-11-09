"""#10990 (C9) — E5's 25-vs-29 population question is a claim, and this pins the answer.

E5's row has carried an open note since 2026-10-23: *"the 25-bound (this instrument) vs
29-marker-sentence (the record) populations must be reconciled before either number is
called final."* `#5087` carries the question of WHICH count is canonical (`#9738`, which owned it until
2026-11-02, closed on its own section-10 record correction, not on a measurement of this number). This
module does not touch that. It pins the mechanical half a lane may answer without a ruling:

    the 29 and the 25 are not one population counted twice.

**Why that needs a pin rather than a paragraph.** "Unreconciled" reads as a task left undone,
so the next reader either re-derives it or — worse — assumes the two counts measure the same
thing and divides one by the other. Each test below fails if the underlying fact moves:

* the 29 is a **corpus** count (sentences matching the pre-registration's own
  ``hedge_scope_lexemes``) and the 25 is a **gold** count (``planted_qualifiers``): a corpus
  or gold edit that makes them coincide reds ``test_the_two_counts_are_not_one_population``;
* ``wp05_retro_writeup`` carries **3 planted qualifiers and 0 marker sentences**, which is the
  decisive case — if a later lexeme table starts matching wp05's words, the "they are different
  populations" claim weakens and this test says so instead of letting the prose rot;
* the registered ``29`` count itself is pinned by the pre-registration checker
  (``capstone_preregistration_check``), so this module reads it rather than restating it — a
  second encoding of that number is exactly the drift #9075's gate exists to catch.

Measured at `origin/main` `af864a42a6` (2026-11-06); the README's C9 section carries the
commands and their outputs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BUNDLE = REPO_ROOT / "docs" / "evidence" / "7949-capstone"
PROBE = BUNDLE / "probes" / "e5_population_reconcile.py"


@pytest.fixture(scope="module")
def report() -> dict:
    """The probe's own report — imported, never re-implemented (#9738's lesson)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("e5_population_reconcile", PROBE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.reconcile()


def test_the_registered_29_is_the_corpus_marker_count(report: dict) -> None:
    """The 29 in `preregistration.json` is DERIVED from the corpus, not a gold count.

    If this ever reads something else, the row's denominator has been re-registered and the
    rest of this module is answering a stale question.
    """
    import json

    prereg = json.loads((BUNDLE / "preregistration.json").read_text())
    registered = int(prereg["corpus"]["hedge_scope_sentences"])

    assert report["marker_sentences_total"] == registered == 29
    assert report["corpus_sentences_total"] == int(
        prereg["corpus"]["hedge_scope_sentences_total"]
    )


def test_the_scored_25_is_the_gold_qualifier_count(report: dict) -> None:
    """The 25 the scorer classifies is the sealed gold's `planted_qualifiers`, summed."""
    assert report["planted_qualifiers_total"] == 25
    assert len(report["per_session"]) == 7


def test_the_two_counts_are_not_one_population(report: dict) -> None:
    """⛔ THE PIN: markers exceed qualifiers in some sessions and fall short in others.

    A reconciliation that treats the 29 as "the same 25 plus four stragglers" is falsified by
    this: no monotone map exists between the two series, so the 29 cannot be re-scored by
    adding rows to the 25.
    """
    above = [r["session"] for r in report["per_session"]
             if r["marker_sentences"] > r["planted_qualifiers"]]
    below = [r["session"] for r in report["per_session"]
             if r["marker_sentences"] < r["planted_qualifiers"]]

    assert above, "no session has more marker sentences than planted qualifiers"
    assert below, "no session has fewer marker sentences than planted qualifiers"
    assert report["marker_sentences_total"] == 29
    assert report["planted_qualifiers_total"] == 25


def test_a_session_with_qualifiers_and_zero_marker_sentences(report: dict) -> None:
    """wp05 is the decisive case: its qualifiers are real and its marker count is zero.

    Its words (``about``, ``they are both good``, ``that was the decision after the August
    incident``) are in the fixture text but not in the corpus lexeme table, so the registered
    29-sentence population cannot contain the sentence they live in. If this list stops being
    `['wp05_retro_writeup']`, the vocabulary changed and the doc's argument must be re-read —
    which is the whole point of pinning it.
    """
    assert report["sessions_with_qualifiers_and_no_markers"] == ["wp05_retro_writeup"]

    wp05 = next(r for r in report["per_session"] if r["session"] == "wp05_retro_writeup")
    assert wp05["marker_sentences"] == 0
    assert wp05["planted_qualifiers"] == 3
    # The qualifiers ARE in the fixture — the mismatch is the vocabulary, not a bad gold.
    assert wp05["qualifiers_in_fixture_text"] == 3


def test_the_two_marker_vocabularies_are_not_the_same_table(report: dict) -> None:
    """The corpus lexemes and the scorer's MARKERS are different tables, both in the tree.

    That is WHY the two counts cannot be reconciled by arithmetic: the 29 is filtered by 24
    terms and the 25 is classified against 78. A future edit that unifies them is a
    re-registration (#5087), and it reds this test rather than passing silently.
    """
    assert len(report["corpus_lexemes"]) == 24
    # ⛔ THE LITERAL, not a re-sum of the same live dict (#10990 review, P2): both sides
    # reading `MARKERS` in one process agrees trivially, so the "78 markers" the doc
    # quotes would have been pinned by nothing.
    assert report["scorer_markers_total"] == 78
    assert report["scorer_marker_counts"] == {"hedge": 38, "scope": 18, "stance": 22}
    assert report["lexemes_absent_from_scorer"], (
        "every corpus lexeme is now also a scorer marker — the vocabularies have converged, "
        "which changes E5's population argument and needs an owner ruling before the doc says so"
    )

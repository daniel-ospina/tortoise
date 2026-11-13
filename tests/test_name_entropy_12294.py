"""The name-entropy gate (#12294): the ported predicate, and why we do NOT ship it.

WHAT THIS PINS, AND WHY IT IS TWO THINGS
----------------------------------------
`#12294` proposed porting Zep/Graphiti's name-entropy gate to our capture prior:
issue no query for a name whose entropy is too low to discriminate, and resolve
such a name through the deterministic rungs (or hold it) instead. It was built,
measured on the `#11901` corpus, and **falsified by the issue's own falsifier** —
recall drops while the duplicate rate does not improve. The product wiring was
reverted; the predicate lives in `tools/candidate_recall_11901.py` as live
measurement code.

So this module guards two different things, and both are needed:

1. **The port is faithful.** Zep's three constants and the `AND` in its length
   test are the whole narrowness of the gate. A later reader who "simplifies"
   `len < 6 and tokens < 2` into an `or` would refuse every single-token name,
   and a drift in the `1.5` threshold silently changes which population is
   affected. Both are pinned here against the values read from
   `graphiti_core/utils/maintenance/dedup_helpers.py:31-33,60-86`.

2. **The falsification cannot be re-derived by accident.** The gate was declined
   because its premise is false for our leg: a bare definite description is
   queried as its ORIGINAL whitespace terms (two-term implicit AND), not as a
   bare content word, so the leg finds the canonical node — 98 of 98 gated rows
   at k=15 on the measured corpus. `test_the_product_does_not_gate` reds if
   anyone re-wires the gate into `_derive_queries` without new evidence, and
   `test_the_premise_is_false_for_our_query_builder` states the mechanism.

MEASURED NUMBERS (the record the decline rests on — artifact: the `gate`,
`gate_by_class`, `gate_variant` and `pooled_gate` arms of
`tools/candidate_recall_11901.py --sample 300`, keyword-only leg on this box;
`#11901`'s committed artifacts are hybrid, and this arm's conclusion is about
WHICH NAMES GET SEARCHED, which is leg-independent):

  * shipped predicate: 85 distinct names of 300 gated; 0 of them unreachable at
    any k; 85 of 98 rows in-window at k=3, 98 of 98 at k=15;
  * exact Zep port: 13 distinct names (all ``#NNNN``), 26 of 26 rows in-window
    at k=15, 0 of the 82-name unreachable class;
  * within a real 15-name capture union, the other names' queries rescue 1 of 85
    gated names — the recall cost is not an isolation artefact;
  * gated VARIANT pairs that phase 1 binds today: 0 at every k, so the gate
    prevents no duplicate (`#11901` measured variant attach 0.0 % either way).

HOW TO RUN
----------
No graph is needed, but every `pytest` run in this repo needs the lane's DB seam
(epic #1647 P4), so use::

    uv run python tools/test_lane.py run -- tests/test_name_entropy_12294.py -q
    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_name_entropy_12294.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _instrument():
    """The tool module, imported by path — `tools/` is not a package."""
    path = REPO / "tools" / "candidate_recall_11901.py"
    spec = importlib.util.spec_from_file_location("_recall_11901", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _instrument()


# ── 1. the port is faithful ────────────────────────────────────────────────


def test_zep_constants_are_pinned():
    """The three numbers are the gate's whole narrowness (dedup_helpers.py:31-33)."""
    assert gate._NAME_ENTROPY_THRESHOLD == 1.5
    assert gate._MIN_NAME_LENGTH == 6
    assert gate._MIN_TOKEN_COUNT == 2


@pytest.mark.parametrize("name,expected", [
    # the AND: refused only when SHORT **and** SINGLE-TOKEN
    ("fix", False),        # 3 chars, 1 token → both conditions hold → refused
    ("hotfix", True),      # 6 chars, 1 token → length passes, entropy ~2.5
    ("the fix", True),     # 9 chars, 2 tokens → both pass on the long-name arm
    ("a bcd", True),      # 5 chars but 2 tokens → escapes the length test, and
                           # entropy of "abcd" is 2.0, so it passes on merit
    ("a b", False),       # …but a two-token name is not automatically allowed: it
                           # still faces the entropy test, and "ab" is only 1.0
    # entropy, with the length/token test already satisfied
    ("ababab", False),     # 6 chars, 1 token, entropy 1.0 < 1.5 → refused
    ("abcabc", True),      # 6 chars, 1 token, entropy 1.585 >= 1.5 → allowed
    ("aaaaaaaa", False),   # 8 chars, 1 token, entropy 0.0 → refused
    ("", False),           # empty is never high-entropy
])
def test_has_high_entropy_behaviour(name, expected):
    """Threshold drift reds here, on both sides of `1.5` and on the `AND`."""
    assert gate._has_high_entropy(name) is expected, (
        f"{name!r} → {gate._name_char_entropy(name):.3f} bits/char")


def test_entropy_is_over_characters_with_spaces_stripped():
    """The measure is repetition, not length: spaces do not dilute it."""
    assert gate._name_char_entropy("ab ab") == pytest.approx(1.0)
    assert gate._name_char_entropy("abab") == pytest.approx(1.0)
    assert gate._name_char_entropy("") == 0.0


# ── 2. why the gate was declined: the premise is false for OUR leg ─────────


@pytest.mark.parametrize("name", ["the suite", "the diff", "the repo",
                                  "the tool", "the issue"])
def test_the_premise_is_false_for_our_query_builder(name):
    """`the <word>` is NOT queried as the bare word — it is a two-term AND.

    This is the finding the decline rests on, and it is mechanical, not a
    judgement:
      * `_signal_tokens` keeps one token, so the gate refuses the name;
      * `build_or_query` only builds the `|`-union when TWO OR MORE tokens
        survive; with one survivor it returns the RAW query (`sparse.py:536-550`);
      * RediSearch evaluates space-separated terms as implicit AND
        (`sparse.py:13-17`), so the leg searches BOTH terms — and finds the
        node. Measured: 98 of 98 gated rows in-window at k=15.
    """
    from tortoise.sparse import build_or_query, SPARSE_STOPWORDS

    tokens = gate._signal_tokens(name)
    assert len(tokens) == 1, tokens          # the gate's trigger
    assert tokens[0] not in SPARSE_STOPWORDS
    assert gate._name_carries_signal(name) is False, (
        "the predicate is expected to GATE this shape — that is the finding")

    query = build_or_query(name)
    assert query == name, (
        "a single surviving token takes the raw-literal fallback, so the article "
        f"is still searched; build_or_query({name!r}) returned {query!r}")
    assert "|" not in query, "no OR-union was built — the cluster premise fails"


def test_the_identifiers_the_zep_port_gates_are_searched_verbatim():
    """The verbatim Zep port gates only `#NNNN` — a literal the leg matches exactly."""
    from tortoise.sparse import build_or_query

    for name in ("#2258", "#2981", "#1484"):
        assert gate._has_high_entropy(name.lower().strip()) is False
        assert gate._name_carries_signal(name) is False
        assert build_or_query(name) == name


def test_a_zero_content_token_name_is_gated_by_both():
    """The one class with no query to run — and it is empty on the measured corpus."""
    assert gate._signal_tokens("the and") == []   # article + connective, both stopwords
    assert gate._name_carries_signal("the and") is False
    # A name whose only surviving token IS a content word is not this class,
    # even though the predicate still gates it — the two reasons are distinct
    # (`no_content_token` vs `low_entropy_token`) and must not be conflated.
    assert gate._signal_tokens("the same") == ["same"]


# ── 3. the product must not gate (the regression guard) ────────────────────


def test_the_product_does_not_gate():
    """`_derive_queries` still derives a query for every entity name (#12294).

    If someone re-wires the gate, this reds — and it is meant to. The decline is
    evidence-backed (see this module's docstring), so re-arming it needs new
    evidence, not a narrower threshold.
    """
    from tortoise.extractor_v2 import _derive_queries

    embed_list = {"entities": [{"name": "the fix"},
                               {"name": "the plan doc"},
                               {"name": "#2258"}]}
    assert _derive_queries(embed_list, "")["object"] == [
        # `#2258` reaches the leg as `2258`: `_sanitise_query_text` strips the
        # punctuation RediSearch reads as syntax (`_derive_queries`' one home).
        "the fix", "the plan doc", "2258"]


def test_no_gated_entity_reporting_reached_the_product():
    """No `gated_entities` key, no `lookup_gated` hold reason — the wiring is out.

    The two product-side seams the issue proposed were `search_graph`'s report
    and `resolve_entities`' hold. Both were reverted with the gate; this pins
    that a half-revert cannot leave one of them behind.
    """
    from tortoise import extractor_v2 as v2

    assert not hasattr(v2, "HOLD_LOOKUP_GATED")
    assert "lookup_gated" not in v2.HOLD_REASONS
    assert not hasattr(v2, "_name_carries_signal")
    assert not hasattr(v2, "_gated_entity_names")

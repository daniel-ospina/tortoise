"""#5534 — the D3/ask-shape instrument must be ABLE to measure A4.

A4 (``TORTOISE_ASK_SEARCH_KEYS_PRF``) is implemented as
``sdk._search_keys_prf_expansion`` → ``tortoise.sparse.expansion_tokens``,
whose output depends ONLY on the ``search_keys`` harvested from the first-pass
top-5 hits (``expansion_tokens`` opens with ``if not aliases: return []``).
The D3 fixture seeder seeded a pure capture TURN store with zero
``search_keys``, so the FTS leg was byte-identical ON vs OFF and **every A4
A/B returned a guaranteed zero**. That is not a verdict on A4 — it is an
instrument with no input, and the risk (#5534) is that a future lane re-runs
the same A/B, sees a clean zero, and records A4 as validated when nothing was
measured.

These tests pin the INPUT, at the three levels the issue names:

* unit — ``_derive_search_keys`` produces the E3 shape, and
  ``expansion_tokens`` on the harvested aliases is non-empty;
* mechanism — the seeded store carries non-empty ``search_keys`` on the
  Points the first-pass FTS actually returns;
* arm — the ORDERED hit list DIFFERS between the two arms.

The arm test needs the FalkorDB full-text index (the embedded lane has none,
and A4 is structurally inert there — ``index_missing`` — which is the same
blank-input trap one lane over), so it SKIPS on the embedded carve-out. The
mutation direction is pinned too: ``search_keys=False`` reproduces #5534's
defect shape (zero keys, empty expansion, identical arms).

#5534 follow-up (two P3 review fixes): the A4 input is an EXPLICIT OPT-IN.
``_seed_memory`` defaults to the CAPTURE-EXACT shape (``search_keys=False`` —
capture's turn write stores none), so a caller that passes nothing gets a
byte-identical store to pre-#5534 and the A4-bearing tests below pass
``search_keys=True`` THEMSELVES. That makes this file evidence for BOTH
halves: the lever moves when opted in, and the default does not move anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_spotcheck import (
    SEED_SEARCH_KEYS_BY_DEFAULT,
    _derive_search_keys,
    _seed_memory,
    seed_capture_turn_store,
)
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import run_fts_query
from tortoise.sparse import expansion_tokens, tokenize_sparse_query

#: A tiny synthetic question shaped so A4 has something to do: the first-pass
#: top hit carries a distinctive alias (``zanzibar``) whose token appears in
#: ANOTHER turn that the original query does not match. The expansion must
#: therefore pull that turn into the FTS leg — if it does not, the arms are
#: identical and the instrument cannot see A4.
_QUESTION = {
    "question_id": "a4-input-guard",
    "question": "What did we discuss about the migration budget?",
    "haystack_dates": ["2023/01/01 (Sun) 10:00", "2023/01/02 (Mon) 10:00",
                       "2023/01/03 (Tue) 10:00"],
    "haystack_session_ids": ["a4-s0", "a4-s1", "a4-s2"],
    "haystack_sessions": [
        [
            {"role": "user", "content":
             "the zanzibar migration budget is forty dollars"},
            {"role": "assistant", "content": "noted the migration budget"},
        ],
        [
            {"role": "user", "content":
             "zanzibar notes are filed under travel research"},
            {"role": "assistant", "content":
             "the travel research folder holds the zanzibar notes"},
        ],
        [
            {"role": "user", "content":
             "we reviewed the migration budget for the quarter"},
            {"role": "assistant", "content": "the budget review is done"},
        ],
    ],
}


@pytest.fixture
def sdk(tmp_path, keyword_lane):
    """Depends on ``keyword_lane`` so the embedder is stubbed BEFORE the SDK
    is constructed (an SDK-level warm-up must not pay the model load either).
    """
    s = TortoiseSDK(str(tmp_path / "a4.db"))
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def keyword_lane(monkeypatch):
    """A4 lives on the FTS leg, so the arm test runs the KEYWORD lane (no
    embedder installed — a real product configuration) rather than paying the
    ~40 s embedder load for a leg that cannot affect A4 either way. The
    DENSE-lane A/B is measured by ``docs/runbook/5534_a4_ab_diagnostic.py``
    (21/21 questions differ); this file guards the INPUT, cheaply."""
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: None))
    return None


def _skip_if_no_fts(sdk) -> None:
    """The embedded FalkorDBLite lane has no Point FTS index — the ``fts``
    leg records ``reason='index_missing'`` and A4 can never expand. Same
    blank-input condition as #5534, so skip rather than assert a zero."""
    trace: list[dict] = []
    run_fts_query(sdk._get_proj().g, "probe", entity_type="point", limit=1,
                  leg_trace=trace)
    if trace and trace[0].get("reason") == "index_missing":
        pytest.skip("no Point FTS index on this lane (embedded carve-out) — "
                    "A4 is structurally inert without it")


def _arms(sdk, question: str, limit: int = 20) -> tuple[list[str], list[str]]:
    def ids(prf: bool) -> list[str]:
        hits = sdk.tortoise_fts_query(
            question, limit=limit, pool_size=limit, include_terminal=True,
            keep_numeric=True, search_keys_prf=prf)
        return [str(h.get("id") or "") for h in hits]
    return ids(False), ids(True)


def _search_keys_points(sdk) -> int:
    return sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.search_keys IS NOT NULL "
        "AND p.search_keys <> '' RETURN count(p)").result_set[0][0]


# ── unit: the derivation, and the expansion it feeds ──────────────────────

def test_derive_search_keys_matches_the_e3_shape():
    """Flat space-joined STRING (the post-``_flatten_search_keys_prop`` shape
    the Point FTS index reads), at most 4 entries, each within the schema's
    60-char bound, longest-first, no stopwords, deterministic."""
    text = ("[user] the zanzibar migration budget is forty dollars and "
            "some extremely-long-unbroken-alias-token-beyond-the-bound-x")
    sk = _derive_search_keys(text)
    entries = sk.split()
    assert sk and isinstance(sk, str)
    assert len(entries) <= 4, entries
    assert all(1 <= len(e) <= 60 for e in entries), entries
    # longest-first (the specificity rule expansion_tokens applies)
    assert [len(e) for e in entries] == sorted(
        (len(e) for e in entries), reverse=True)
    assert sk == _derive_search_keys(text)          # deterministic
    assert not set(entries) & {"the", "and", "is"}  # stopwords dropped
    assert _derive_search_keys("a i of") == ""      # nothing storable


def test_harvested_aliases_drive_a_non_empty_expansion(sdk):
    """The UNIT leg: aliases harvested off the seeded first-pass hits yield a
    non-empty ``expansion_tokens`` — the call A4 makes. Before #5534 this was
    always ``[]`` because the store carried no ``search_keys``."""
    _skip_if_no_fts(sdk)
    _seed_memory(sdk, _QUESTION, embed=False, search_keys=True)
    assert _search_keys_points(sdk) > 0
    first = run_fts_query(sdk._get_proj().g, _QUESTION["question"],
                          entity_type="point", limit=20,
                          keep_numeric=True)
    top5 = [pid for pid, _s in first[:5] if pid]
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.id IN $ids "
        "RETURN coalesce(p.search_keys, '')", params={"ids": top5},
    ).result_set
    aliases = [r[0] for r in rows if r[0]]
    assert aliases, f"first-pass top-5 {top5} carry no search_keys"
    expansion = expansion_tokens(
        aliases, reserved=set(tokenize_sparse_query(
            _QUESTION["question"], keep_numeric=True)))
    assert expansion, "expansion_tokens returned [] — A4 has no input again"


# ── arm: the A/B the instrument runs ──────────────────────────────────────

def test_a4_ab_ordered_hit_list_differs_between_arms(sdk, keyword_lane):
    """THE guard #5534 asks for: with the seeded input, the ordered hit list
    DIFFERS between ``search_keys_prf`` False and True. A clean zero here is
    the defect, not a passing A4 — so this test fails on a zero."""
    _skip_if_no_fts(sdk)
    _seed_memory(sdk, _QUESTION, embed=False, search_keys=True)
    off, on = _arms(sdk, _QUESTION["question"])
    assert off, "retrieval returned nothing — the premise is broken"
    assert on != off, (
        "A4 is INERT on the seeded store — the ordered hit list is identical "
        "with the PRF lever on and off (#5534). The instrument has no A4 "
        "input; do not record A4 as validated.")


def test_seed_without_search_keys_reproduces_5534(sdk, keyword_lane):
    """The MUTATION direction: ``search_keys=False`` restores #5534's defect
    shape — zero keys, empty expansion, byte-identical arms. This is what
    makes the test above evidence that the fix (not the instrument) moved."""
    _skip_if_no_fts(sdk)
    _seed_memory(sdk, _QUESTION, embed=False, search_keys=False)
    assert _search_keys_points(sdk) == 0
    off, on = _arms(sdk, _QUESTION["question"])
    assert off == on, ("with no search_keys the arms must be byte-identical "
                       "(the defect shape) — a difference means the arm test "
                       "is measuring something other than A4")


# ── the shared primitive stays capture-exact by default ───────────────────

def test_seed_capture_turn_store_keeps_search_keys_off(sdk):
    """The SHARED capture-exact seeder writes no ``search_keys`` by default
    (capture's turn write stores none, and the committed transcript goldens
    seed through it). The D3 FIXTURE seeder opts in EXPLICITLY — flipping a
    shared default instead would silently rewrite those goldens' store."""
    seed_capture_turn_store(sdk, "sess-a4", [
        {"role": "user", "content": "zanzibar migration budget"},
    ])
    # ``test_ask_seed_shape``'s ``_read_shape`` does not project search_keys;
    # read it directly.
    val = sdk._get_proj().g.query(
        "MATCH (p:Point {id:'sess-a4_t0'}) RETURN p.search_keys"
    ).result_set[0][0]
    assert val is None, f"shared seeder wrote search_keys by default: {val!r}"


def test_seed_memory_defaults_to_the_capture_exact_shape(sdk):
    """#5534 follow-up: ``_seed_memory``'s DEFAULT is the faithful capture
    shape (``search_keys=False``), NOT the A4 opt-in. This is the separation
    the review demanded: every non-A4 caller (``tools/profile_read_path.py``,
    ``tools/ask_pool_admission_probe.py``, the ``w6c_*``/``w7a_*``/``4107_*``
    diagnostics) passes nothing and must therefore produce a store
    byte-identical to pre-#5534. Non-vacuous: the explicit opt-in below DOES
    seed keys on the same fixture."""
    assert SEED_SEARCH_KEYS_BY_DEFAULT is False, (
        "the library default must stay the capture-exact OFF — flipping it ON "
        "would silently change every non-A4 caller's store (#5534 review)")
    _seed_memory(sdk, _QUESTION, embed=False)
    assert _search_keys_points(sdk) == 0, (
        "a caller that does NOT opt in seeded search_keys — the default flip "
        "is back")
    # Non-vacuity: the same fixture, opted in, DOES carry the substrate.
    _seed_memory(sdk, _QUESTION, embed=False, search_keys=True)
    assert _search_keys_points(sdk) > 0, (
        "the opt-in seeded nothing — the A4 input guard is vacuous")

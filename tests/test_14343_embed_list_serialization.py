"""#14343 increment 2 — the S4 embed-list *shape*, pinned on the REAL emissions.

WHY THIS FILE EXISTS, WHEN `tests/test_extractor_embed_json_14343.py` ALREADY DOES
------------------------------------------------------------------------------
That file pins the **lever's semantics**: the flag's default bytes, that `compact`
changes only layout, that the value is read at call time. It builds a **synthetic**
embed list to do it.

This file pins the **data and the prompt**, on the real corpus the measurement was
taken from, and it pins the two things a *future* lever (keys-declared-once, see
`docs/evidence/14343-amplify/reduction-measured.md` §3b) gets wrong silently:

1. **The measured cells are recorded, and the payload bytes are the tripwire.** The
   token figures below were measured with `cl100k_base` over
   `tests/eval/write_path/receipts/7947-arms-2026-10-18/` (7 sessions / 14 emissions /
   580 items). They are pinned as constants, **not re-derived**, because
   `tiktoken.get_encoding("cl100k_base")` downloads ~1.6 MB of BPE ranks from
   `openaipublic.blob.core.windows.net` on a cold cache — real egress from a
   deterministic test, which `tests/conftest.py`'s egress guard cannot even see
   (it goes over `requests`/`urllib`, not `httpx`). The same reason, and the same
   pattern, as `tests/test_s2_decision_record_5087.py`'s `_S7_TOKEN_CELLS`. What
   reds if a receipt changes is the **character** count, which needs no tokenizer.

2. **Both layouts parse back to the identical object.** This is the property the
   brief calls "the risk … a silent format change the downstream parser tolerates
   but reads differently": the saving is only information-neutral if the rendered
   region round-trips to what S2 emitted — key order included.

3. **Records are heterogeneous, and `null` is a legitimate value.** 10 of the 80 lists in
   this corpus carry records whose key sets differ (`operators` in 9 of its 12 lists,
   `points` in 1 of 14). The naive `items[0].keys()` implementation drops every field a
   later record adds — and the render gets SMALLER, so the data loss reads as a bigger
   saving. Padding the survivors with `dict.get(k)` is no better: the corpus carries 210
   cells that are explicitly `null` against 54 that are an absent key, so `null` cannot be
   the sentinel either. Measured: naive 44,993 · `null`-padded 45,905 · correct
   (`␀`-padded, verified absent) 46,028.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from tortoise import extractor_v2 as v2  # noqa: E402

_RECEIPTS = _REPO_ROOT / "tests/eval/write_path/receipts/7947-arms-2026-10-18"

#: The measured `cl100k_base` cells for this corpus (#14343 increment 2, §1 and §3b of
#: `docs/evidence/14343-amplify/reduction-measured.md`). Re-measure with the recipe in
#: that file's §6 — do NOT derive them here (see the module docstring on the BPE fetch).
#:
#: ⛔ These CANNOT be re-derived in this test (no tokenizer, deliberately), so they are made
#: REACHABLE by `test_the_recorded_token_cells_encode_the_doc_s_own_deltas` below: the
#: ordering the three traps encode, and every delta the evidence doc publishes, is asserted
#: against these cells. A typo here reds that test instead of shipping silently — which is
#: the exact defect class `test_s2_decision_record_5087.py`'s `durations:` row records
#: ("the table's token column was pinned by nothing — a token-only edit … shipped green").
_MEASURED_TOKENS = {
    "indent": 77_561,          # json.dumps(embed, indent=1)              — today's bytes
    "compact": 52_346,         # json.dumps(embed, separators=(",",":")) — the shipped lever
    "keys_once": 46_028,       # union-of-keys + sentinel padding (§3b) — measured, NOT shipped
    "keys_once_naive": 44_993,  # items[0].keys() — LOSSY, listed to show the trap's size
    "keys_once_null_padded": 45_905,  # dict.get(k) — cannot tell absent from null
}
#: The LIVE tripwire: characters, not tokens. A receipts change moves these first.
_MEASURED_CHARS = {"indent": 278_874, "compact": 224_109}
#: The per-emission saving share, measured in TOKENS (14 emissions span 29.6 %–35.0 %);
#: the aggregate cell §1 publishes (32.51 %) sits inside it. Read by
#: `test_the_recorded_token_cells_encode_the_doc_s_own_deltas` — a band, not a point,
#: because it is a property of the serialization, so drifting outside it means the
#: receipts changed shape, not that the tokenizer moved. Enforcing a band rather than a
#: point is deliberate: a token cell cannot be re-measured without a tokenizer, and §1
#: correctly refuses to fetch one at test time.
_SAVING_SHARE_BAND = (29.0, 36.0)


#: Padding for an ABSENT key. `None` cannot be used: it is a legitimate value in this
#: schema (measured: 210 explicitly-`null` cells vs 54 absent ones), so `dict.get(k)` would
#: conflate "field not present" with "field present and null". `~` cannot either — it occurs
#: inside real content. This sentinel is verified absent from the corpus below, which is the
#: property a real renderer must check at render time rather than assume.
_ABSENT = "\u2400"


def _emissions() -> list[tuple[str, str, dict]]:
    """(session, arm, embed_list) for every real S2 emission on disk."""
    out: list[tuple[str, str, dict]] = []
    for path in sorted(_RECEIPTS.glob("*.arms.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        for arm, embed in (rec.get("embed_list") or {}).items():
            if not isinstance(embed, dict):
                continue
            if not any(isinstance(v, list) and v for v in embed.values()):
                continue
            out.append((path.name.split(".")[0], arm, embed))
    return out


def _keys_once(embed: dict) -> dict:
    """Declare a list's keys ONCE — union of keys, absent ones padded.

    Padding is the whole point: the corpus's lists are heterogeneous (§3b), so the
    obvious `items[0].keys()` drops fields and shrinks the render.
    """
    out: dict = {}
    for name, items in embed.items():
        if isinstance(items, list) and items and all(isinstance(x, dict) for x in items):
            keys = list(dict.fromkeys(k for it in items for k in it))
            out[name] = {"cols": keys,
                         # `.get` with a SENTINEL default, never `None`: `null` is a legal
                         # value in this schema, so a `None` default cannot tell an absent
                         # key from an explicitly-null one (the trap this test pins).
                         "rows": [[it.get(k, _ABSENT) for k in keys]
                                  for it in items]}
        else:
            out[name] = items
    return out


def _expand(keys_once: dict) -> dict:
    """Inverse of :func:`_keys_once` — restores the list-of-dicts shape."""
    out: dict = {}
    for name, val in keys_once.items():
        if isinstance(val, dict) and set(val) == {"cols", "rows"}:
            out[name] = [{k: row[i] for i, k in enumerate(val["cols"])
                          if row[i] != _ABSENT} for row in val["rows"]]
        else:
            out[name] = val
    return out


@pytest.fixture(scope="module")
def emissions() -> list[tuple[str, str, dict]]:
    rows = _emissions()
    assert rows, f"no usable write-path receipts under {_RECEIPTS} — the corpus this " \
                 f"pins the measurement to is MISSING, not empty"
    return rows


# ── the measured cells, and the byte tripwire that protects them ──────────────

def test_the_pinned_cells_are_the_measured_corpus(emissions):
    """The characters the token cells were measured on are still these characters."""
    chars = {
        "indent": sum(len(json.dumps(e, indent=1)) for _, _, e in emissions),
        "compact": sum(len(json.dumps(e, separators=(",", ":"))) for _, _, e in emissions),
    }
    assert chars == _MEASURED_CHARS, (
        "the write-path receipts moved, so the pinned `cl100k_base` cells in "
        f"{Path(__file__).name} are no longer the cells this corpus measures — "
        "re-measure with the recipe in docs/evidence/14343-amplify/"
        f"reduction-measured.md §6 (was {_MEASURED_CHARS}, now {chars})")
    assert len(emissions) == 14, f"14 emissions were measured, saw {len(emissions)}"
    assert len({s for s, _, _ in emissions}) == 7, "the frozen corpus is 7 sessions"


def test_the_recorded_token_cells_encode_the_doc_s_own_deltas():
    """The recorded `cl100k_base` cells are a PIN, not decoration — each one is read here.

    A tokenizer is unavailable to a deterministic test (§1: fetching one is real egress),
    so the cells themselves cannot be recomputed. They can still be made FALSIFIABLE: every
    ordering the three §3b traps encode, and every delta the evidence doc publishes FROM
    these cells, is asserted below. Editing any single cell moves an asserted delta or
    breaks an ordering, so a token-only edit reds instead of shipping green.

    What is still NOT enforced: the absolute values. Nothing here can tell 46,028 from a
    different corpus's 46,028 — a receipts change is caught by the character tripwire in
    `test_the_pinned_cells_are_the_measured_corpus` above, which is the live half.
    """
    t = _MEASURED_TOKENS
    # The trap ordering, which is the whole argument of §3b: declaring keys once with the
    # naive `items[0].keys()` renders SMALLER than the correct union-of-keys render, which
    # is what makes data loss read as a bigger saving. `null`-padding cannot use `null` as
    # the sentinel either, so it too must land above the naive render and below the correct
    # one. Compact must beat today's indent render, and the correct keys-once render must
    # beat compact — otherwise §3b has no headroom and the lever is pointless.
    assert (t["keys_once_naive"] < t["keys_once_null_padded"] < t["keys_once"]
            < t["compact"] < t["indent"]), (
        "the recorded cells no longer encode the §3b traps' ordering "
        "(naive < null-padded < keys-once < compact < indent) — re-measure §1/§3b before "
        f"trusting any delta computed from them: {t}")
    # Every delta the evidence doc publishes, recomputed from the cells.
    assert t["indent"] - t["compact"] == 25_215, "§1's saving cell (−25,215 tokens)"
    assert round(100.0 * (t["indent"] - t["compact"]) / t["indent"], 2) == 32.51, (
        "§1's `25,215 / 77,561 = 32.5099 %` (printed 32.51 at two decimals)")
    assert t["indent"] - t["keys_once"] == 31_533, "§3b's −31,533 (−40.66 %) cell"
    assert t["compact"] - t["keys_once"] == 6_318, "§3b's −6,318 (−12.07 %) off compact"
    assert t["keys_once"] - t["keys_once_naive"] == 1_035, (
        "§3b's 'understates the render by 1,035 tokens while losing data' cell")
    # The band the failure message below quotes: measured in TOKENS per emission
    # (29.6 %–35.0 %), and the aggregate cell must sit inside it.
    token_share = 100.0 * (t["indent"] - t["compact"]) / t["indent"]
    assert _SAVING_SHARE_BAND[0] < token_share < _SAVING_SHARE_BAND[1], (
        f"the recorded token share {token_share:.2f}% left the band this file pins, "
        f"{_SAVING_SHARE_BAND[0]}–{_SAVING_SHARE_BAND[1]}% — re-measure §1")


def test_the_compact_layout_always_shrinks_and_the_share_is_a_property_of_the_layout(emissions):
    """The compact render is in its measured character band on EVERY emission.

    Measured per emission (tokens): 29.6 %–35.0 %, and 17.11 %–21.68 % on characters — the
    two differ because `indent=1` spends characters on whitespace the tokenizer folds into
    single tokens. Asserted on characters here because a tokenizer is unavailable to a
    deterministic test; the token band is pinned separately, against the recorded cells, in
    `test_the_recorded_token_cells_encode_the_doc_s_own_deltas`.

    ⛔ What this test does NOT catch, stated because the earlier wording claimed it did:
    a per-emission regression in PRODUCTION (one list rendered compact, another not). It
    re-serializes the corpus itself and never calls `extractor_v2`'s embed render; the only
    production-touching assertion in this file is the S4 prompt test below, which is
    layout-agnostic by design. What the band does catch is the mutant that renders the
    second layout with DEFAULT separators: the share then falls to 13.41 %–17.39 %, outside
    the band.
    """
    shares = []
    for _, _, embed in emissions:
        ind = json.dumps(embed, indent=1)
        comp = json.dumps(embed, separators=(",", ":"))
        shares.append(100.0 * (len(ind) - len(comp)) / len(ind))
    assert min(shares) > 15.0 and max(shares) < 25.0, (
        f"character-level share drifted outside its measured band: {min(shares):.1f}–"
        f"{max(shares):.1f}% (measured 17.11–21.68%); the TOKEN band this file pins is "
        f"{_SAVING_SHARE_BAND[0]}–{_SAVING_SHARE_BAND[1]}% and is asserted against the "
        f"recorded cells, not against these characters")


# ── the round-trip: same object, key order included ───────────────────────────

def test_both_layouts_round_trip_to_the_identical_object(emissions):
    """Parsing either render yields what S2 emitted — key order included.

    This is the property that makes the lever information-neutral at the JSON level.
    `json.loads` preserves insertion order, so a reordered render reds here even
    though it is "the same dict" by equality-insensitive comparison.
    """
    for session, arm, embed in emissions:
        for label, render in (("indent", json.dumps(embed, indent=1)),
                              ("compact", json.dumps(embed, separators=(",", ":")))):
            parsed = json.loads(render)
            assert parsed == embed, f"{session}/{arm} {label}: not the same object"
            assert json.dumps(parsed) == json.dumps(embed), (
                f"{session}/{arm} {label}: key order moved")
            for name, items in embed.items():
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    assert [list(it) for it in parsed[name]] == [list(it) for it in items], (
                        f"{session}/{arm} {label}: record key order moved in {name!r}")


def test_the_s4_prompt_carries_a_region_that_parses_back_to_the_same_list(monkeypatch):
    """The region the lever re-serializes is READ by a parser, not just by the model.

    Layout-agnostic on purpose: it accepts whichever serialization the prompt carries
    (`indent=1` today, compact once armed), so this test does not have to change when
    the lever is armed — it only has to keep being true.
    """
    embed = {
        "points": [{"content": f"claim {i}", "pointKind": "statement",
                    "about_entities": [f"e{i}"], "quote": f"quote {i}"}
                   for i in range(4)],
        "operators": [{"type": "IMPL", "src": "claim 0", "dst": "claim 1"}],
    }
    prompt = v2.render_s4_prompt("the story", {"results": []}, embed,
                                 v2.build_master_list(),
                                 session_date="2026-11-23T00:00:00+00:00")
    carriers = [s for s in (json.dumps(embed, indent=1),
                            json.dumps(embed, separators=(",", ":"))) if s in prompt]
    assert len(carriers) == 1, (
        "the S4 prompt must carry the embed list in exactly one of the two known "
        "layouts — carrying neither means the region moved, carrying both means a "
        "fragment leaked into another slot")
    assert json.loads(carriers[0]) == embed


# ── the trap in the *next* lever: heterogeneous records ───────────────────────

def test_the_corpus_records_are_heterogeneous_so_a_keys_once_render_must_pad(emissions):
    """Pinned because the naive implementation is lossy AND looks like a bigger win."""
    hetero: dict[str, int] = {}
    total: dict[str, int] = {}
    nulls: dict[str, int] = {}
    absent: dict[str, int] = {}
    for _, _, embed in emissions:
        for name, items in embed.items():
            if not isinstance(items, list) or not items:
                continue
            if not all(isinstance(x, dict) for x in items):
                continue
            total[name] = total.get(name, 0) + 1
            if len({tuple(sorted(it)) for it in items}) > 1:
                hetero[name] = hetero.get(name, 0) + 1
            cols = list(dict.fromkeys(k for it in items for k in it))
            for it in items:
                for k in cols:
                    if k not in it:
                        absent[name] = absent.get(name, 0) + 1
                    elif it[k] is None:
                        nulls[name] = nulls.get(name, 0) + 1
    assert hetero, (
        "no list in the corpus has differing key sets — if the emissions were "
        "normalised, revisit the `keys_once` measurement before trusting its saving")
    assert hetero.get("operators"), (
        "`operators` was the 9-of-12 heterogeneous list the padding requirement was "
        "measured on; its records becoming uniform means the emission shape changed")
    # The padding sentinel can be neither `null` nor a conventional glyph: both are in use.
    assert sum(nulls.values()) > 0, (
        "`None` was live in this corpus (210 cells at the measurement) — if `null` is no "
        "longer a legal value, a `dict.get(k)` pad becomes admissible and §3b shrinks by "
        "one trap; re-measure rather than relaxing this silently")
    assert sum(absent.values()) > 0, (
        "no absent-key cells — then padding is unnecessary and the union-of-keys "
        "render is trivially lossless, which contradicts the measured heterogeneity")


def test_the_padding_sentinel_is_absent_from_the_corpus(emissions):
    """A sentinel is only usable because it does not occur — assert that, not assume it."""
    blob = "".join(json.dumps(e, ensure_ascii=False) for _, _, e in emissions)
    assert _ABSENT not in blob, (
        f"the padding sentinel {_ABSENT!r} now occurs inside real data — pick one that "
        f"does not and re-measure the keys-once cells")


def test_a_keys_once_render_is_lossless_only_with_the_union_of_keys(emissions):
    """Union-of-keys round-trips exactly; `items[0].keys()` silently drops fields.

    The lost fields are named rather than only counted: on the corpus aggregate the naive
    render is *smaller* (44,993 vs 46,028) which is what makes the defect read as a win,
    but per emission it is not reliably smaller — it keeps every record's keys and only
    drops the fields past `items[0]`, so the direction depends on the list. What is always
    true is that it is WRONG, which is what this asserts.
    """
    lost_fields: set[str] = set()
    lossy = 0
    for session, arm, embed in emissions:
        keys_once = _keys_once(embed)
        assert _expand(keys_once) == embed, f"{session}/{arm}: padded round-trip failed"
        naive: dict = {}
        for name, items in embed.items():
            if isinstance(items, list) and items and all(isinstance(x, dict) for x in items):
                cols = list(items[0].keys())
                naive[name] = [{k: it[k] for k in cols if k in it} for it in items]
                lost_fields.update(k for it in items for k in it if k not in cols)
            else:
                naive[name] = items
        if naive != embed:
            lossy += 1
    # Per emission the naive render is lossless exactly when `items[0]` happens to carry
    # the union — which is why it passes review. It is the aggregate that is wrong.
    assert lossy, (
        "the naive render was lossless on EVERY emission — the heterogeneity measured in "
        "§3b does not bite, so re-measure before dropping the padding requirement")
    assert lost_fields, (
        "no field was ever dropped by the naive render — re-measure §3b")
    # The fields the naive render loses are the operator membership metadata — the same
    # E2/E4 evidence a keys-once render must not drop.
    assert lost_fields <= {"target_edge", "strength", "slots", "when", "tier", "quote",
                           "search_keys", "source_turn_id"}, (
        f"the naive render lost a field outside the measured set: {sorted(lost_fields)}")

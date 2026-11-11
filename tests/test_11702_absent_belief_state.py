"""#11702 — a claim with NO belief state must not be scored as confidence 0.5.

The defect: a claim with no persisted belief answered the neutral Beta(1,1)
mean 0.5 on every read/rank surface, so "we have not computed this" and "a
measured symmetric coin flip (α = β)" were the SAME number. `0.5` is the
maximum-entropy value, i.e. it reads as mid-strength evidence — a no-evidence
claim sorted between genuinely-disproved and genuinely-supported ones, and any
threshold comparing `confidence` let it through as if it carried neutral
evidence.

The recorded rule this file pins is `docs/architecture/SEARCH-ARCHITECTURE.md`
§6.3 (merged, PR #11481):

    absent state is `null` with `ep_state: "absent"`, never 0.5 — a value the
    ranker refuses to order on.

The distinction is PRESENCE, not value: a claim that really is at 0.5 because
α = β keeps its 0.5. Both directions are pinned below, because a fix that
nulls every 0.5 would satisfy half of this file and destroy the other half.

These tests are written against the PROPERTY (no surface reports or orders on a
belief that does not exist) rather than against a call site, so a new ranker or
a re-introduced `coalesce(…, 0.5)` is caught by the same assertions.

**Coverage, stated exactly** (so a later lane does not read this file as
covering more than it does). PINNED here: the vocabulary (`ep_state`,
`ep_has_belief_cypher` via the surfaces below, `belief_value`),
`annotate_ep_batch`, the canonical `why` `ep` block, `GraphRanker`,
`StateRanker`/`recall_ranking`, `GapsRanker`, `SubgraphExpander`, the
`analyze` templates (`evidence_chain`, and the `_BELIEF` gate they share),
`navigation.confidenceMin`, `sdk.approve`'s `confidence_delta`, and the
journal records both belief-clearing writers emit. Each is pinned for the
whole rule, not one shape: absent / half pair / zero-total pair / mirror-only /
unreadable-operand / out-of-band-mirror / measured.

Deliberately OUT of this file's scope: `battery/arms/a4_tortoise.py` (an eval
arm, not a product surface) and `sdk.get_confidence` /
`TortoiseEP.compute_confidence`, which remain the EP prior read under
`require_calibration=False` and still answer 0.5 for an absent claim — the
one stated exception, recorded in the #11702 decision comment
(issuecomment-6781252637).
"""
from __future__ import annotations

import json
import os
import struct
import sys
import uuid
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The HOSTED write verb's half of the `ep_updated` pin drives the real
# `POST /v1/sessions` route, so it reuses `tests/test_hosted_api.py`'s
# authenticated TestClient fixture — the established repo pattern (see
# `tests/test_capture_phase_d_dedup.py`), not a second hand-rolled app harness.
from tests.test_hosted_api import TEST_ORG_ID  # noqa: E402, RUF100
from tests.test_hosted_api import client as client  # noqa: E402, RUF100, F401
from tortoise import ranking as _ranking  # noqa: E402, RUF100
from tortoise import search_engine as _se  # noqa: E402, RUF100
from tortoise.ranking import (  # noqa: E402, RUF100
    UNCOMPUTED_CONFIDENCE_SOURCE,
    GapsRanker,
    GraphRanker,
    StateRanker,
    SubgraphExpander,
)
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100
from tortoise.search_engine import (  # noqa: E402, RUF100
    EpBreakdown,
    annotate_ep_batch,
)

#: The #11702 vocabulary. Resolved through ``getattr`` on purpose: this file must
#: still IMPORT against a tree that predates the fix, so that running it there
#: REDS on the property assertions below instead of dying at collection. An
#: import-time error proves nothing about the defect; a red assertion does.
EP_STATE_MEASURED = getattr(_se, "EP_STATE_MEASURED", "measured")
EP_STATE_BASELINE = getattr(_se, "EP_STATE_BASELINE", "baseline")
EP_STATE_ABSENT = getattr(_se, "EP_STATE_ABSENT", "absent")
#: The fourth `confidence_source` token (#11702 cycle 24): the row HAS a belief
#: (so the state is `baseline`/`measured`) but its stored spelling cannot be
#: decoded here. `getattr` for the same reason as the states above — this file
#: must import against a pre-fix tree and RED there, not die at collection.
UNREADABLE_CONFIDENCE_SOURCE = getattr(
    _ranking, "UNREADABLE_CONFIDENCE_SOURCE", "unreadable")


class _MissingState:
    """``ep_state`` sentinel for a tree that has no such field."""

    def __call__(self, **_kwargs):
        raise AssertionError(
            "search_engine has no ``ep_state`` — the absent state is unnamed "
            "(#11702)")

    def __repr__(self) -> str:  # pragma: no cover — diagnosis only
        return "<no ep_state>"


ep_state = getattr(_se, "ep_state", _MissingState())

#: A real, persisted, measured posterior whose mean is exactly 0.5 — symmetric
#: evidence (α = β). This is the row that must NOT change.
SYMMETRIC_POSTERIOR = (2.0, 2.0)


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    yield s
    s.close()


def _absent_point(sdk) -> str:
    """A Point nothing has been persisted for: no posterior, no prior, no
    mirror. `create_point` alone does not write belief columns."""
    return sdk.create_point("statement", "no belief was ever computed")["id"]


def _store_f32(value):
    """The float32 the STORE would serve for a number, for an exact comparison.

    Written here (not imported from `search_engine._float32`) so the twin-grammar
    table checks the coercion against an independent definition: a one-ULP miss
    is the whole defect class, and `pytest.approx`'s default 1e-6 relative
    tolerance passes it at 1.0.
    """
    if value is None:
        return None
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _symmetric_point(sdk) -> str:
    """A Point with a REAL symmetric posterior — a legitimately computed 0.5."""
    pid = sdk.create_point("statement", "a measured symmetric coin flip")["id"]
    alpha, beta = SYMMETRIC_POSTERIOR
    sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id = $id "
        "SET n.posterior_alpha = $a, n.posterior_beta = $b",
        params={"id": pid, "a": alpha, "b": beta},
    )
    return pid


# ── The vocabulary is one mapping, in one place ────────────────────────────

def test_ep_state_names_the_three_presence_states():
    """`ep_state` is the ONE mapping from the internal measurement predicate to
    the wire state. `unmeasured` ships as `absent` (the word §6.3's rule uses),
    and nothing else is renamed."""
    assert ep_state(posterior_alpha=None, ep_alpha=None) == EP_STATE_ABSENT
    assert ep_state(posterior_alpha=2.0, ep_alpha=None,
                    posterior_beta=2.0) == EP_STATE_MEASURED
    # A declared #2199 baseline prior: a VALUE, but not a measurement (#3276).
    assert ep_state(posterior_alpha=None, ep_alpha=3.0, ep_beta=1.0,
                    baseline_set=True) == EP_STATE_BASELINE
    # ⛔ HALF a pair is no belief: the mean needs both operands, so a row with
    # only β (or only α) invents nothing — the `coalesce(…, 1.0)` default used
    # to supply the missing one and answer 1/4.
    assert ep_state(posterior_alpha=None, ep_alpha=None,
                    posterior_beta=3.0) == EP_STATE_ABSENT
    assert ep_state(posterior_alpha=2.0, ep_alpha=None) == EP_STATE_ABSENT
    # …and a complete pair that sums to zero is no belief either.
    assert ep_state(posterior_alpha=0.0, ep_alpha=None,
                    posterior_beta=0.0) == EP_STATE_ABSENT
    # A legacy EP-flush mirror alone is still a persisted belief.
    assert ep_state(posterior_alpha=None, ep_alpha=None,
                    confidence=0.42) == EP_STATE_BASELINE


def test_ep_breakdown_default_reports_none_and_absent():
    """The dataclass default is the ABSENT contract — a default-constructed
    breakdown must never claim 0.5 (which is what every "no row" fallback in
    the read path used to inherit)."""
    ep = EpBreakdown()
    assert ep.confidence_mean is None
    assert ep.ep_state == EP_STATE_ABSENT


# ── Direction 1: absent ⇒ null + the state name, on EVERY surface ──────────

def test_ep_block_reports_null_and_absent_for_no_belief(sdk):
    pid = _absent_point(sdk)
    ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    assert ep.confidence_mean is None, (
        f"a claim with no persisted belief must report NO number, got {ep.confidence_mean}")
    assert ep.ep_state == EP_STATE_ABSENT
    assert ep.has_ep is False


def test_graph_ranker_signal_reports_null_and_absent(sdk):
    pid = _absent_point(sdk)
    sig = GraphRanker(projection=sdk._get_proj())._fetch_signals([pid], "point")[pid]
    assert sig["confidence"] is None
    assert sig["ep_state"] == EP_STATE_ABSENT
    assert sig["has_prior"] is False


def test_state_ranker_reports_null_and_absent(sdk):
    pid = _absent_point(sdk)
    out = StateRanker(projection=sdk._get_proj()).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])
    rr = out[0]["recall_ranking"]
    assert rr["confidence"] is None
    assert rr["confidence_source"] == EP_STATE_ABSENT
    assert rr["ep_state"] == EP_STATE_ABSENT


def test_gaps_ranker_reports_null_and_absent(sdk):
    pid = _absent_point(sdk)
    out = GapsRanker(projection=sdk._get_proj()).rerank([{"id": pid}])
    rr = out[0]["gaps_ranking"]
    assert rr["confidence"] is None
    assert rr["ep_state"] == EP_STATE_ABSENT


def test_subgraph_node_reports_null_and_absent(sdk):
    pid = _absent_point(sdk)
    nodes = SubgraphExpander(sdk._get_proj()).expand([pid], depth=1)["nodes"]
    node = next(n for n in nodes if n["id"] == pid)
    assert node["confidence"] is None
    assert node["ep_state"] == EP_STATE_ABSENT


def test_a_non_point_node_carries_neither_belief_key(sdk):
    """A non-Point node carries NEITHER key — not `null`.

    `config/surface-manifest.yml`'s `nodes[].ep_state` row said "a non-Point
    node carries `null`". It carries no key at all: `SubgraphExpander._enrich`
    writes `ep_state` and `confidence` inside ONE `if state:` gate, and
    `ep_state_cypher` answers `null` for `labels(n)[0] <> 'Point'`, so the gate
    never opens for an Object/Subject/Event/Source node. `null`-vs-absent is a
    distinction this repo treats as meaningful (#2294), so a consumer following
    the row would index a key that is not on the wire — and no test pinned the
    non-Point shape, which is why the row survived twenty-odd cycles.
    """
    pid = _absent_point(sdk)
    obj = sdk.create_object("zq-absent-node", "product")
    oid = obj["id"]
    proj = sdk._get_proj()
    proj.g.query("MATCH (p:Point {id:$pid}), (o:Object {id:$oid}) "
                 "CREATE (p)-[:IS_ABOUT]->(o)",
                 params={"pid": pid, "oid": oid})
    nodes = SubgraphExpander(proj).expand([pid], depth=1)["nodes"]
    by_id = {n["id"]: n for n in nodes}
    assert oid in by_id, (
        "the linked Object did not reach the expansion, so this test could "
        "not fail: " + repr(sorted(by_id)))
    non_point = by_id[oid]
    assert non_point["type"] == "object", non_point
    assert "ep_state" not in non_point and "confidence" not in non_point, (
        "a non-Point node must carry NEITHER belief key (the manifest said "
        "`null`, a different wire shape): " + repr(non_point))
    # …and the Point beside it still reports the absent state, both keys.
    assert by_id[pid]["ep_state"] == EP_STATE_ABSENT, by_id[pid]
    assert "confidence" in by_id[pid] and by_id[pid]["confidence"] is None, \
        by_id[pid]


# ── Direction 2: a MEASURED 0.5 keeps its 0.5 (presence, not value) ────────

def test_measured_symmetric_claim_keeps_its_five_tenths(sdk):
    """The distinction is presence, not value. A claim whose posterior really
    is 0.5 (α = β) still reads 0.5 — nulling every 0.5 would be the same
    conflation in the other direction."""
    pid = _symmetric_point(sdk)

    ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    assert ep.confidence_mean == pytest.approx(0.5, abs=1e-4)
    assert ep.ep_state == EP_STATE_MEASURED
    assert ep.has_ep is True

    sig = GraphRanker(projection=sdk._get_proj())._fetch_signals([pid], "point")[pid]
    assert sig["confidence"] == pytest.approx(0.5, abs=1e-4)
    assert sig["ep_state"] == EP_STATE_MEASURED

    rr = StateRanker(projection=sdk._get_proj()).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["recall_ranking"]
    assert rr["confidence"] == pytest.approx(0.5, abs=1e-4)
    assert rr["confidence_source"] == "posterior"
    assert rr["ep_state"] == EP_STATE_MEASURED


def test_declared_baseline_prior_keeps_its_value_and_is_not_measured(sdk):
    """A #2199 declared baseline is a persisted VALUE EP has not measured
    (#3276): it keeps its prior mean and is named `baseline`, never `absent`
    and never `measured`."""
    pid = sdk.create_point("statement", "a baseline-backed decision")["id"]
    sdk.set_point_baseline(pid, 18.0, 2.0)

    ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    assert ep.confidence_mean == pytest.approx(18.0 / 20.0, abs=1e-4)
    assert ep.ep_state == EP_STATE_BASELINE
    assert ep.measured is False and ep.baseline is True


def test_a_degenerate_declared_baseline_is_absent_and_not_prior_only(sdk):
    """A `set_point_baseline(claim, 0, 0)` has no prior MEAN — 0/0 — so it cannot
    be the `baseline` state (#11702 cycle 30).

    `baseline=True` is the alias for "prior-only: confidence_mean IS the declared
    prior mean". Every surface reads the (0, 0) prior as `absent` (the mean does
    not exist), so the flag claiming prior-only was the same two-keys-of-one-block
    contradiction the state vocabulary exists to remove — a consumer deciding
    "there is a prior, use the mean" from `baseline` got `None`.
    """
    pid = sdk.create_point("statement", "a degenerate baseline")["id"]
    sdk.set_point_baseline(pid, 0.0, 0.0)

    from tortoise.why import assemble_why_blocks

    ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    assert ep.ep_state == EP_STATE_ABSENT, (
        f"a 0/0 prior has no mean, so the state is absent — got {ep.ep_state!r}")
    assert ep.confidence_mean is None, ep
    assert ep.baseline is False, (
        "baseline=True beside ep_state='absent' — prior-only claims a mean that "
        "does not exist for a degenerate (0, 0) prior")

    # …and the `why` surface ships its OWN `baseline` key, built from the same
    # rule. Pinning only `annotate_ep_batch` left it able to disagree with the
    # `ep` block for one claim — which it did, until this round.
    why_ep = assemble_why_blocks(sdk._get_proj(), [pid])[pid].get("ep") or {}
    assert why_ep.get("ep_state") == EP_STATE_ABSENT, why_ep
    assert why_ep.get("baseline") is False, (
        f"the `why` block still calls a degenerate prior prior-only: {why_ep}")
    assert why_ep.get("confidence_mean") is None, why_ep


def test_a_literal_past_the_int_str_digit_cap_reads_like_its_short_spelling():
    """Cycle 30 P2: `Fraction("<4301 digits>")` raises `ValueError` — CPython
    caps int↔str conversion — and the `exact is None` fallback then DISCARDED
    the literal and answered from the double, i.e. from exactly the rounding
    `_f32_from_literal` exists to remove.

    Two spellings of ONE value then disagreed, in both directions: the
    1 + 2⁻²⁴ midpoint padded with trailing fractional zeros (value unchanged)
    served `1.0` — maximum certainty — beside the store's `absent`, and an
    overflow-band spelling served `None` beside the store's max. Trailing zeros
    are the cheapest way for a stored operand to reach 4,300 characters, so the
    padding is not exotic bookkeeping: it is the reachable spelling.

    `_exact_literal_fraction` now reads decimals through `Decimal`, whose parse
    has no digit cap (and `as_integer_ratio` never converts back to a string),
    while hex stays on `int(digits, 16)` — exempt from the cap — and keeps its
    exponent bound. So each padded literal must read exactly like its short
    spelling, and like the value the store serves.
    """
    from tortoise.search_engine import numeric_operand

    rows = {
        # short spelling -> the float32 the store serves
        "1.0000000596046448": 1.0000001192092896,        # above the 1+2⁻²⁴ midpoint
        "1.000000059604644775390625": 1.0,               # the midpoint (tie → even)
        "3.4028235677973366e+38": 3.4028234663852886e38,  # below the overflow mid
        "5.877471754111438e-39": None,                   # a repr() of a subnormal
        "0x1.0000010000000001p0": 1.0000001192092896,    # hex midpoint + 2⁻⁶⁴
    }

    def pad(lit: str, zeros: int = 4400) -> str:
        mantissa, sep, exponent = lit.partition("p") if "p" in lit else lit.partition("e")
        if "x" in mantissa.lower():
            # A hex literal is already exempt from the cap; pad its mantissa so
            # the row still proves the value is unchanged, not the code path.
            head, _, frac = mantissa.partition(".")
            mantissa = head + "." + (frac or "0") + "0" * zeros
        else:
            mantissa = (mantissa + "." if "." not in mantissa else mantissa) \
                + "0" * zeros
        return mantissa + sep + exponent

    for short, expected in rows.items():
        long = pad(short)
        assert len(long) > 4300, (short, len(long))
        assert numeric_operand(long) == numeric_operand(short), (
            f"{short!r} and its {len(long)}-character spelling of the SAME value "
            f"read differently: {numeric_operand(short)!r} vs "
            f"{numeric_operand(long)!r} — the literal was discarded")
        if expected is None:
            assert numeric_operand(long) is None, (short, numeric_operand(long))
        else:
            assert _store_f32(numeric_operand(long)) == _store_f32(expected), (
                short, numeric_operand(long), expected)


def test_a_padded_hex_exponent_cannot_empty_the_batch():
    """Cycle 30 P2: the padding axis this cycle bounded had a CRASHING sibling.

    The mantissa of a hex literal is reconstructed with `int(digits, 16)`, which
    CPython's int↔str cap exempts — but the `p` exponent is base 10 and is NOT
    exempt. A zero-padded exponent (`p-000…149`, the same cheap padding that
    makes any stored operand arbitrarily long) raised `ValueError` inside
    `int()`, and `annotate_ep_batch`'s blanket handler answered `{}` for the
    whole batch: ONE legacy row made every claim in the response read
    `ep_state: "absent"`, `confidence_mean: null`, `measured: false` — including
    claims genuinely measured — and any belief floor then returned nothing.

    Pinned on the HELPER (the batch surface needs a live graph and the helper is
    where the raise was), and on the shape that matters: the value must be the
    literal's, not a discarded `None`.
    """
    from fractions import Fraction

    from tortoise.search_engine import _exact_literal_fraction

    # A padded exponent is the SAME exponent: value must be unchanged, not None.
    assert _exact_literal_fraction("0x1p-149") == Fraction(1, 2 ** 149)
    padded = "0x1p-" + "0" * 4400 + "149"
    assert len(padded) > 4300, len(padded)
    assert _exact_literal_fraction(padded) == Fraction(1, 2 ** 149), (
        f"a {len(padded)}-character exponent of the SAME value read "
        f"{_exact_literal_fraction(padded)!r} instead of 2**-149 — the literal "
        "was discarded, and one such row empties the whole batch")
    assert _exact_literal_fraction("0x1p+1") == 2
    assert _exact_literal_fraction("0x1p+" + "0" * 4400 + "1") == 2
    # …and a genuinely out-of-bound exponent still declines, without raising.
    assert _exact_literal_fraction("0x1p+9999") is None
    assert _exact_literal_fraction("0x1p+" + "9" * 4400) is None


# ── A hex exponent the mantissa CANCELS is not an unreadable literal ──────

def test_a_hex_exponent_past_the_bound_its_mantissa_cancels_is_still_read(sdk):
    """#11897: the digit-cap bound refused a value the STORE serves.

    `_exact_literal_fraction` RETURNED `None` when `abs(exponent) >
    _EXACT_FRACTION_MAX_EXP`, and the three call sites answered that `None` with
    `return None` — ABSENT — on the stated ground that the branch was
    unreachable for an admitted literal. It was reachable: a long mantissa
    CANCELS a large exponent, leaving a finite, in-band, non-zero value.

    Measured on FalkorDB 4.20.6, the literal below is exactly `1 + 2⁻²⁴`:

        store          toFloat(<literal>)         -> 1.0   (non-null; served)
        adb5d47cf8^    numeric_operand(<literal>) -> 1.0   (the `rounded` fallback)
        on `main`      numeric_operand(<literal>) -> None  (ABSENT — the regression)
        at this fix    numeric_operand(<literal>) -> 1.0   (asserted below)

    `adb5d47cf8` is the commit that replaced the three call sites' `return
    rounded` fallbacks with `return None`; APPLYING only that payload restores
    the regression, which is why the row is named by SHA rather than by "before"
    (measured: the parent tree answers 1.0 for this literal).

    Stored as the α operand with `β = 3`, the claim read DIFFERENTLY on the two
    sides of the read path — and the split is exactly the Python/Cypher line,
    because the refusal lived only in the Python parse:

        Python side     `ep` block (None, 'absent'), `why` (None, 'absent', False),
                        StateRanker (None, 'measured'), gaps_ranking (None, 'measured')
        Cypher side     GraphRanker 0.25, recall_subgraph
                        {"ep_state": "measured", "confidence": 0.25}

    ⚠️ Not 0.5: the neutral-Beta(1,1) fallback was already gone (that is what
    #11702 removed), so nothing answered 0.5 here. What the refusal produced was
    the other half of the same defect — a store-reading surface reporting a
    measured 0.25 while the `ep` block in the SAME response said there is no
    belief. Correcting that claim changes no assertion below; the `!= 0.5` guard
    is kept as a tripwire against a fallback ever coming back.
    """
    from fractions import Fraction

    from tortoise.ranking import GapsRanker, GraphRanker, StateRanker
    from tortoise.search_engine import _exact_literal_fraction, numeric_operand

    # 1 + 2⁻²⁴ — the float32 MIDPOINT above 1.0, spelled so the exponent is
    # past the bound and the mantissa cancels it back into band.
    long_hex = "0x" + format((2**24 + 1) * 2**4076, "x") + "p-4100"
    assert len(long_hex) > 1000
    assert float.fromhex(long_hex) == 1.0 + 2.0**-24

    # The reconstruction, and then the value: a midpoint settles to even, so
    # both spellings answer 1.0 — the same float32 the store serves.
    assert _exact_literal_fraction(long_hex) == Fraction(2**24 + 1, 2**24), (
        "the exact parse refused a literal whose exponent its mantissa cancels")
    assert numeric_operand(long_hex) == numeric_operand("0x1.000001p0") == 1.0, (
        numeric_operand(long_hex), numeric_operand("0x1.000001p0"))

    # …and every surface agrees once it is STORED. `β = 3` keeps the mean off
    # 0.5, so an imputed Beta(1,1) cannot pass this test by coincidence.
    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "ZQHEXBOUND a cancelled exponent")["id"]
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = $a, "
        "n.posterior_beta = '3'",
        params={"id": pid, "a": long_hex})
    ann = annotate_ep_batch(proj.g, [pid])[pid]
    assert ann.ep_state == EP_STATE_MEASURED, (
        f"the store serves this operand, the reader called it {ann.ep_state!r}")
    assert ann.confidence_mean == pytest.approx(0.25, abs=1e-6), ann
    state = StateRanker(projection=proj)._fetch_point_signals([pid])[pid]
    graph = GraphRanker(projection=proj)._fetch_signals([pid], "point")[pid]
    gaps = GapsRanker(projection=proj)._fetch_confidence_signals(
        [pid], labels=["Point"])[pid]
    for name, sig in (("state", state), ("graph", graph), ("gaps", gaps)):
        assert sig.get("confidence") == pytest.approx(0.25, abs=1e-6), (
            f"{name} disagreed with the `ep` block for one stored claim: {sig}")
        assert sig["confidence"] != pytest.approx(0.5, abs=1e-9), (name, sig)


def test_a_cancelling_hex_exponent_reads_at_both_band_edges(sdk):
    """#11897, the OTHER two call sites — clock the fix at the edges of the band.

    The headline case above is the MIDPOINT site (`_f32_from_literal`'s
    tie-break). The same refusal lived in the other two call sites, and each is
    a separate live split, not a duplicate:

    * the OVERFLOW site: `_FLOAT32_MAX` spelled with a `p` exponent past the
      bound is refused there, so Python answered ABSENT for the value every
      Cypher surface serves as max — the mirror of the headline case.
    * the UNDERFLOW site (`_string_operand`): a float32 subnormal named EXACTLY
      by a cancelling spelling was refused, so a claim the store reads at
      `1.4e-45` read absent here — and that `# pragma: no cover` comment said
      "a fail-safe for a future widening only", which is the same kind of
      reachability claim this PR exists to correct.

    Both spellings below are >1000 chars, exactly-valued, and past the bound as
    WRITTEN (`|exponent| > _EXACT_FRACTION_MAX_EXP`) while their MAGNITUDES sit at
    OPPOSITE ends of the band — the MAX witness at `magnitude_bits` = +128, the
    subnormal witness at -145, both far inside the ±4096 bound — so
    `_exact_literal_fraction` refused them before #11897 and reconstructs them
    after. One witness near MAX would leave the negative half of the bound
    unpinned; this test does not. The float32 MIDPOINT spelled the same way stays
    NULL: loosening the bound must not turn a null into a value either.
    """
    from fractions import Fraction

    from tortoise.search_engine import (
        _exact_literal_fraction,
        numeric_operand,
        numeric_operand_cypher,
    )

    max32 = Fraction(2**128 - 2**104)          # _FLOAT32_MAX, exactly
    shift = 4204                               # bits the mantissa cancels
    max_hex = "0x" + format(max32.numerator << shift, "x") + f"p-{shift}"
    sub_hex = "0x1" + "0" * 1025 + "p-4249"    # 2**-149, the min subnormal
    mid_hex = ("0x" + format((2**128 - 2**103) << shift, "x") + f"p-{shift}")
    for raw in (max_hex, sub_hex, mid_hex):
        assert len(raw) > 1000, (len(raw), raw[:24])

    assert float.fromhex(max_hex) == 3.4028234663852886e38
    assert _exact_literal_fraction(max_hex) == max32, (
        "the exact parse refused max, spelled with an exponent its mantissa "
        "cancels — the overflow site answers None for a value the store serves")
    assert numeric_operand(max_hex) == 3.4028234663852886e38, (   # was None
        numeric_operand(max_hex))

    assert float.fromhex(sub_hex) == 2.0**-149
    assert _exact_literal_fraction(sub_hex) == Fraction(1, 2**149)
    assert numeric_operand(sub_hex) == float.fromhex("0x1p-149"), (  # was None
        "the underflow site refused an EXACT subnormal whose spelling names "
        "it exactly — that is the twin split it exists to prevent")

    # The nulls stay null: the exact midpoint above `_FLOAT32_MAX` overflows to
    # `2**128`, which is not a float32.
    assert _exact_literal_fraction(mid_hex) == Fraction(2**128 - 2**103)
    assert numeric_operand(mid_hex) is None, numeric_operand(mid_hex)

    # …and the STORE agrees, on both twins, for the two that read.
    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "ZQHEXEDGE cancelling edge")["id"]
    for raw, expected in ((max_hex, 3.4028234663852886e38),
                          (sub_hex, float.fromhex("0x1p-149"))):
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.posterior_alpha = $a, "
            "n.posterior_beta = '1'", params={"id": pid, "a": raw})
        cypher = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN "
            + numeric_operand_cypher("n.posterior_alpha"),
            params={"id": pid}).result_set[0][0]
        py = numeric_operand(raw)
        assert py is not None and cypher is not None, (raw[:24], py, cypher)
        assert _store_f32(py) == _store_f32(expected) == _store_f32(cypher), (
            py, cypher, expected)


def test_the_hex_bound_is_a_magnitude_and_zero_is_zero():
    """#11897: what the loosened bound must still REFUSE, and the zero case.

    Loosening a cost guard is the half that can only break silently, so the
    surviving half is pinned here rather than inferred from the value rows: the
    shape the guard exists for (`Fraction(2) ** 10**9`) is still refused, the
    boundary is the MAGNITUDE rather than the exponent as written, and a zero
    mantissa is EXACTLY zero at any exponent instead of "unreadable".
    """
    from fractions import Fraction

    from tortoise.search_engine import _exact_literal_fraction

    # 1 + 0 hex digits of magnitude slack: `4 * significant + exponent`, so the
    # boundary sits at 4092/4093 positive and -4100/-4101 negative.
    assert _exact_literal_fraction("0x1p4092") == Fraction(2) ** 4092
    assert _exact_literal_fraction("0x1p4093") is None
    assert _exact_literal_fraction("0x1p-4100") == Fraction(1, 2**4100)
    assert _exact_literal_fraction("0x1p-4101") is None

    # Zero mantissa: the value is 0 whatever the exponent says. Four of these
    # were REFUSED before #11897 (their exponent is past the old bound); the
    # fifth, `0x0000`, has no `p` exponent at all, so it was never refused and
    # it asserted nothing about the zero-mantissa return — it is here as the
    # no-exponent control, not as a fifth failure.
    for raw in ("0x" + "0" * 16 + "p-999999999", "0x0.000p1000000",
                "-0x0p-999999999", "0x0000", "-0x.0p4097"):
        assert _exact_literal_fraction(raw) == Fraction(0), raw

    # The bound measures SIGNIFICANT digits, so a LEFT-padded mantissa is still
    # a small value: only `digits.lstrip("0")` keeps this in band (the leading
    # zeros would otherwise contribute 4 bits each and put it 13,000+ bits over
    # the bound). Leading zeros are ordinary hex digits — C99's `strtod` grammar
    # (and so the store's `toFloat`) accepts them and they do not change the
    # value — and this literal is SERVED, not hypothetical: measured on
    # FalkorDB 4.20.6, `toFloat` answers `1.0` and `_exact_literal_fraction`
    # answers `Fraction(1)`, the same answer `main` gives (its bound was on the
    # EXPONENT, and `p0` is far inside it). So bounding the padded SPELLING
    # instead of the value refuses a served literal: the same defect class this
    # PR exists to remove, re-entered from the other side (#11898 review).
    assert _exact_literal_fraction("0x" + "0" * 4400 + "1p0") == Fraction(1)


def test_a_hex_magnitude_past_the_bound_is_refused_before_it_is_built(
        monkeypatch):
    """#11897: the bound still bounds the COST, not just the answer.

    The DoS rows in the twin-grammar table are DECIMAL (`1e10000000`), and they
    are excluded by `_FLOAT32_OVERFLOW_CEILING` before the exact parse is ever
    entered — so nothing pinned the HEX bound this PR rewrote. A formula whose
    guard stops tripping (or trips in the wrong direction) would ship green
    while `Fraction(2) ** 10**9` became reachable from a stored operand.

    Pinned by MECHANISM, not by a timing assertion: the refusal must happen
    before any `Fraction` is constructed. A timing test would be flaky; a test
    that merely asserts `None` would pass on a guard that builds the value first
    and discards it — which is the cost, not the answer.
    """
    import tortoise.search_engine as se

    built: list[tuple] = []

    def _spy(*args, **_kwargs):
        built.append(args)
        raise AssertionError(
            "the guard did not trip before the value was BUILT — that is the "
            "unbounded-cost path `_EXACT_FRACTION_MAX_EXP` exists to prevent")

    monkeypatch.setattr(se, "Fraction", _spy)
    for raw in ("0x1p1000000", "0x1p-999999999", "-0x1p999999999",
                "0x" + "f" * 2000 + "p1000000", "0x1.8p4093"):
        assert se._exact_literal_fraction(raw) is None, raw
    assert built == [], built

    # The spy IS reachable — an in-band literal reaches the construction — so
    # `built == []` above is the guard tripping, not a dead spy.
    with pytest.raises(AssertionError, match="did not trip"):
        se._exact_literal_fraction("0x1p0")
    assert built, "the spy never fired — the refusals above prove nothing"


def test_a_zero_literal_with_an_empty_integer_part_is_still_zero():
    """Cycle 30 P3: the trailing-zero canonicalisation can EMPTY the integer
    part, and `Decimal("")` raises — so `.0`, `+.00`, `-.0e5` returned `None`
    where the literal's exact value is 0.

    Latent rather than live — `numeric_operand` short-circuits `val == 0.0`
    before the string path, so no served value changed — but this helper's
    contract is "the literal's EXACT value, or None", and 0 is a value. A later
    caller that reaches the helper directly would otherwise read "absent" for a
    literal that is exactly zero.
    """
    from fractions import Fraction

    from tortoise.search_engine import _exact_literal_fraction

    for spelling in (".0", ".00", "+.0", "-.0", ".0e5", ".000E-7", "+.00e+0"):
        assert _exact_literal_fraction(spelling) == Fraction(0), (
            f"{spelling!r} read {_exact_literal_fraction(spelling)!r} — the "
            "canonicalisation emptied the integer part and `Decimal` raised")
    # A dot with no digits at all is not a number, and must still decline.
    assert _exact_literal_fraction(".") is None


# ── The property: an absent claim is not ORDERED as a 0.5 ──────────────────

def test_absent_and_measured_symmetric_do_not_share_an_ordering_input(sdk):
    """THE property test. Two claims, one with no belief and one measured at
    exactly 0.5, must NOT produce the same ordering input on any ranker.

    Before #11702 both read `confidence: 0.5`, so GraphRanker gave both the
    identical `graph_boost` (0.25) and `final_score`, and StateRanker gave both
    the identical `final_score` — the ORDER could not tell "no evidence" from
    "neutral evidence", which is what made this a P1 rather than a nit.
    """
    absent = _absent_point(sdk)
    measured = _symmetric_point(sdk)
    # ⛔ Cycle 20: the nodes need a non-zero DEGREE, or this test cannot see the
    # inversion its own docstring names. With `degree = 0` the connectivity term
    # is 0 for both rows, so the mutant the docstring forbids (returning the
    # UNWEIGHTED term instead of `0.5·connectivity`) produces the same numbers
    # and 285 tests stay green — measured by mutation. At degree 1 connectivity
    # is 0.5: correct absent 0.25 < measured 0.45, mutant 0.50 > 0.45.
    for pid in (absent, measured):
        sdk.create_operator("IMPL", pid, [sdk.create_point(
            "statement", f"a support for {pid}")["id"]])
    degrees = {r["id"]: r["recall_ranking"].get("degree", 0) for r in
               StateRanker(projection=sdk._get_proj()).rerank(
                   [{"id": absent, "scores": {"rrf": 1.0}},
                    {"id": measured, "scores": {"rrf": 1.0}}])}
    assert all(d >= 1 for d in degrees.values()), (
        f"the fixture did not reach the degree-sensitive arm: {degrees}")
    rows = [{"id": absent, "scores": {"rrf": 1.0}},
            {"id": measured, "scores": {"rrf": 1.0}}]

    g_out = {r["id"]: r["graph_ranking"]
             for r in GraphRanker(projection=sdk._get_proj()).rerank(rows)}
    assert g_out[absent]["confidence"] is None
    assert g_out[measured]["confidence"] == pytest.approx(0.5, abs=1e-4)
    # DIRECTION, not just difference: absence must not be scored ABOVE a real
    # measurement. Dropping the belief TERM while keeping the remaining term's
    # weight is what guarantees this; returning the unweighted term would make
    # "nothing measured this" (0.75) beat "measured, symmetric" (0.65).
    assert g_out[absent]["graph_boost"] < g_out[measured]["graph_boost"], (
        "an absent belief scored HIGHER than a measured 0.5")
    assert g_out[absent]["final_score"] < g_out[measured]["final_score"], (
        "an absent belief outranked a measured 0.5")
    # …and it is not BURIED either: the belief TERM is dropped but its WEIGHT
    # stays, so absence keeps the whole boost's structure. Returning 0.0 (or the
    # unweighted term) fails one of these two bounds.
    assert g_out[absent]["graph_boost"] > 0.0, (
        "the absent arm dropped the remaining term's weight as well — "
        "'nothing measured this' must be neither buried nor promoted")
    assert g_out[absent]["graph_boost"] == pytest.approx(
        0.5 * (1.0 - 1.0 / (1.0 + degrees[absent])), abs=1e-4), g_out[absent]

    s_out = {r["id"]: r["recall_ranking"]
             for r in StateRanker(projection=sdk._get_proj()).rerank(rows)}
    assert s_out[absent]["confidence"] is None
    assert s_out[measured]["confidence"] == pytest.approx(0.5, abs=1e-4)
    # DIRECTION: the multiplicative gate cannot express "absent" as a factor
    # (`conf**exp` ranges over (0, 1], so the identity 1.0 is that axis's
    # MAXIMUM — an absent row left to compete on it outranks a measured 0.9).
    # The refusal is in the ORDER: the absent row must come out last.
    order = [r["id"] for r in StateRanker(projection=sdk._get_proj()).rerank(
        [*rows, {"id": "no-such-id", "scores": {"rrf": 1.0}}])]
    assert order.index(absent) > order.index(measured), (
        "recall ranked an absent belief above a measured 0.5")
    assert s_out[absent]["final_score"] != s_out[measured]["final_score"], (
        "the multiplicative confidence gate treated 'no belief' as a 0.5")


def test_set_point_baseline_refuses_a_non_numeric_operand(sdk):
    """Cycle 21 P3: a Boolean slipped every guard on the BASELINE writer.

    `_reject_unrepresentable_number(True)` returns None (the numeric domain does
    not treat a Boolean as an altered number) and `True < 0` is False, so
    `set_point_baseline(claim, True, 0.5)` answered ok and stored `ep_alpha=true`
    — which every reader then reports ABSENT, because a Boolean is not a number
    — while the same call set `baseline_set` + `baseline_source="set-by-author"`,
    freezing the claim against the source-inheritance recompute that would have
    given it a readable belief. The caller was told the write succeeded.
    """
    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "ZQBASELINE guard probe")["id"]

    for bad in (True, [0.7], "0.7", {"a": 1}, 1j):
        with pytest.raises(ValueError):
            sdk.set_point_baseline(pid, bad, 0.5)
        stored = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.ep_alpha, "
            "coalesce(n.baseline_set, false)", params={"id": pid}).result_set[0]
        assert stored[0] is None, (bad, stored)
        assert stored[1] is False, (bad, stored)

    sdk.set_point_baseline(pid, 2.0, 1.0)
    ann = annotate_ep_batch(proj.g, [pid])[pid]
    assert ann.ep_state == EP_STATE_BASELINE, ann
    assert ann.confidence_mean == pytest.approx(2.0 / 3.0, abs=1e-3), ann


def test_the_persisted_why_snapshot_encodes_absent_as_absent(sdk):
    """Cycle 21 P2: the DURABLE half of `why` had no test for its absent arm.

    `why_snapshot._write` does `mean = float(raw_mean) if raw_mean is not None
    else None`, and nothing could tell that from `… else 0.0` — including
    `tests/test_why_snapshot_8099.py`, whose only `mean` assertion seeds a
    MEASURED point (α=3, β=2 → 0.6), a value both variants produce. The file is
    also `embedded_only`, so it skips on any URI lane. Under the mutation the
    snapshot node served `confidence_mean: 0.0` for a claim nothing was measured
    on — "maximum disbelief" in a column named ``mean``, persisted.
    """
    from tortoise import why_snapshot

    proj = sdk._get_proj()
    absent = sdk.create_point("statement", "ZQWHYSNAP no belief")["id"]
    measured = sdk.create_point("statement", "ZQWHYSNAP measured")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 9.0, "
                 "n.posterior_beta = 1.0", params={"id": measured})

    summary = why_snapshot.precompute(proj, [absent, measured], purge=False)
    assert summary.get("enabled", True), summary
    assert summary.get("written", 0) >= 1, summary

    rows = proj.g.query(
        "MATCH (w:WhySnapshot) WHERE w.point_id IN $ids "
        "RETURN w.point_id, w.confidence_mean",
        params={"ids": [absent, measured]}).result_set
    stored = {r[0]: r[1] for r in rows}
    assert absent in stored, (
        "the snapshot dropped the absent claim rather than encoding its "
        f"absence: {stored}")
    assert stored[absent] is None, (
        "the persisted snapshot stored a NUMBER for a claim nothing was "
        f"measured on: {stored[absent]!r} — 0.0 is maximum disbelief, not "
        "'no belief'")
    assert stored[measured] == pytest.approx(0.9, abs=1e-4), stored


def test_the_subgraph_labels_a_terminal_claim_the_same_way_the_ep_block_does(sdk):
    """Cycle 21 P3: the subgraph half of the #2490 fold was unpinned.

    `SubgraphExpander` sets `ep_state_resolve(state, measured=measured_raw and
    not is_terminal)`. Dropping the terminal conjunct made `recall_subgraph`
    label a SUPERSEDED `(1.0, 1.0)` claim `measured` while the `ep` block on
    the SAME response labelled it `baseline` — the "two halves of one reply
    disagreeing about one claim" defect this issue removes. No test failed.
    """
    from tortoise.ranking import SubgraphExpander
    from tortoise.why import assemble_why_blocks

    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "ZQTERM a superseded claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 1.0, "
                 "n.posterior_beta = 1.0, n.status = 'superseded'",
                 params={"id": pid})

    expanded = SubgraphExpander(projection=proj).expand([pid], depth=1)
    nodes = expanded.get("nodes") if isinstance(expanded, dict) else expanded
    node = next(n for n in nodes if n.get("id") == pid)
    ep = assemble_why_blocks(proj, [pid])[pid].get("ep") or {}
    assert node["ep_state"] == EP_STATE_BASELINE, (
        "a superseded claim — whose posterior decayed at the terminalising "
        f"write — is reported as {node['ep_state']} in the subgraph")
    assert node["ep_state"] == ep.get("ep_state"), (
        f"the response contradicts itself: subgraph {node['ep_state']!r} vs "
        f"the ep block {ep.get('ep_state')!r}")


def test_a_legacy_string_operand_outside_float32_is_absent_on_every_surface(sdk):
    """Cycle 22 P1: the string branch of the coercion answered a NUMBER for a
    literal the store cannot read.

    `numeric_operand`'s numeric branch applies `math.isfinite`; the STRING branch
    — added a cycle earlier to pin the shared grammar — did not, so
    `float("1e400")` came back ``inf`` while ``toFloat("1e400")`` is null. One
    legacy `n.confidence='1e400'` therefore read `baseline / inf` in the `ep`
    block and `absent` in all three rankers and the subgraph, and
    `json.dumps(asdict(EpBreakdown(confidence_mean=inf)), allow_nan=False)` —
    what Starlette's JSONResponse does — raises, i.e. a 500 on the agent-facing
    surfaces rather than a wrong number.

    The rule is the store's, measured: `strtof` accepts float32's NORMAL range
    and nothing else (`toFloat("1e-38")` and `toFloat("1e-320")` are null, while
    `"1.2e-38"` reads), with any zero accepted.
    """

    from tortoise.search_engine import numeric_operand

    proj = sdk._get_proj()
    # TWO literals, because two guards answer for them and each needs its own
    # probe: `'1e400'` overflows the DOUBLE (caught by the finiteness guard) and
    # `'1e-320'` is a perfectly finite double that the store's float32 parse
    # underflows to null (caught only by the range guard). A test that used just
    # the first stayed green when the second guard was disabled — measured.
    # FOUR literals, four rules, each proved by mutation: `'1e400'` overflows the
    # DOUBLE; `'1e-320'` is a finite double the store's float32 parse underflows;
    # `'1e-350'`/`'1e-400'` are ALREADY zero as a double, so only the literal's
    # own mantissa distinguishes them from an exact `'0'` (they answered `0.0` —
    # maximum disbelief — until cycle 23).
    for literal in ("1e400", "1e-320", "1e-350", "1e-400"):
        assert numeric_operand(literal) is None, (
            f"the Python twin still answers {numeric_operand(literal)!r} for "
            f"{literal!r}, which the store reports as absent")
    pid = sdk.create_point("statement", "ZQERANGE an out-of-range mirror")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.confidence = '1e-320'",
                 params={"id": pid})
    _assert_absent_everywhere(proj, pid)
    proj.g.query("MATCH (n:Point {id:$id}) SET n.confidence = '1e400'",
                 params={"id": pid})
    _assert_absent_everywhere(proj, pid)


def _assert_absent_everywhere(proj, pid):
    """One claim, every surface: absent, and the `ep` block must not carry inf."""
    import json
    from dataclasses import asdict

    from tortoise.analyze import analyze
    from tortoise.ranking import GapsRanker, GraphRanker, StateRanker, SubgraphExpander
    from tortoise.why import assemble_why_blocks

    ann = annotate_ep_batch(proj.g, [pid])[pid]
    assert ann.ep_state == EP_STATE_ABSENT, ann
    assert ann.confidence_mean is None, ann
    # …and the wire can carry it: the whole point of the None.
    json.dumps(asdict(ann), allow_nan=False)

    ep = assemble_why_blocks(proj, [pid])[pid].get("ep") or {}
    assert ep.get("ep_state") == EP_STATE_ABSENT, ep
    node = next(n for n in SubgraphExpander(projection=proj).expand(
        [pid], depth=1)["nodes"] if n.get("id") == pid)
    assert node["ep_state"] == EP_STATE_ABSENT, node
    # All three rankers' own reads: StateRanker and GraphRanker through their
    # point-signal batches, GapsRanker through the confidence batch it grades on.
    for sigs in (
        StateRanker(projection=proj)._fetch_point_signals([pid]),
        GraphRanker(projection=proj)._fetch_signals([pid], "point"),
        GapsRanker(projection=proj)._fetch_confidence_signals(
            [pid], labels=["Point"]),
    ):
        sig = sigs[pid]
        assert sig.get("confidence") is None, sig
    answers = analyze("what are we most uncertain about?", proj=proj)
    # The rendered ANSWER, not the response dict — the dict echoes the Cypher
    # template, and the template's own comments legitimately say "inf".
    assert "inf" not in answers["answer"].lower(), answers["answer"][:400]
    assert "formatting error" not in answers["answer"].lower(), answers["answer"]


def test_a_literal_at_the_stores_rounding_boundary_is_read_not_refused(sdk):
    """Cycle 23 P2: a readable literal must not fall through to the OPERANDS.

    `"3.4028235e38"` is above float32's maximum yet rounds to it, so the store
    READS it. A window written as `<= max` refused it — and the consequence was
    not a value difference: with the mirror refused, the rankers fell through to
    their operand columns, which read
    `coalesce(n.posterior_alpha, n.ep_alpha, 1.0)` — an IMPUTED Beta(1,1) — and
    answered `0.5` labelled `belief_informed_order: True`. Three answers for one
    claim (absent / 3.4e38 / 0.5), the last being the neutral score this issue
    removes, in the tier that decides order.
    """
    from tortoise.ranking import GapsRanker, GraphRanker, StateRanker
    from tortoise.search_engine import numeric_operand

    proj = sdk._get_proj()
    assert numeric_operand("3.4028235e38") is not None, (
        "a literal that rounds to float32's maximum is read by the store")
    assert numeric_operand("3.40282355e38") is not None, (
        "a literal that rounds DOWN to float32's maximum is read")
    assert numeric_operand("1.1754944e-38") is not None   # min normal, reads
    # …and the rules stay exact one notch out, in both directions.
    assert numeric_operand("3.4028236e38") is None        # rounds to inf
    assert numeric_operand("1.1754943e-38") is None       # rounds up, still null
    assert numeric_operand("1e-38") is None               # subnormal

    # The OPERANDS are pseudo-counts, so a large value is legitimate there — which
    # is the surface this boundary belongs on. (The MIRROR is a probability and is
    # banded at [0, 1] since cycle 24: see
    # `test_a_mirror_outside_the_probability_band_is_not_a_belief`.)
    pid = sdk.create_point("statement", "ZQROUND a boundary operand")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = "
                 "'3.4028235e38', n.posterior_beta = '1'", params={"id": pid})
    ann = annotate_ep_batch(proj.g, [pid])[pid]
    assert ann.confidence_mean is not None, ann
    assert ann.confidence_mean == pytest.approx(1.0, abs=1e-6), ann
    state = StateRanker(projection=proj)._fetch_point_signals([pid])[pid]
    graph = GraphRanker(projection=proj)._fetch_signals([pid], "point")[pid]
    gaps = GapsRanker(projection=proj)._fetch_confidence_signals(
        [pid], labels=["Point"])[pid]
    for name, sig in (("state", state), ("graph", graph), ("gaps", gaps)):
        assert sig.get("confidence") is not None, (name, sig)
        assert sig["confidence"] != pytest.approx(0.5, abs=1e-9), (name, sig)


def test_an_unreadable_mirror_is_not_replaced_by_an_imputed_pair(sdk):
    """Cycle 23 P2: the rankers' operand columns defaulted to `1.0`/`1.0`.

    `coalesce(n.posterior_alpha, n.ep_alpha, 1.0)` invents a Beta(1, 1) for a
    claim with NO stored operands, so whenever the mirror was refused in
    Python while a Cypher arm still read it — `"0x1p4"` (16.0 to both grammars
    now, and refused as a belief by `belief_mirror`'s [0, 1] BAND) or a numeric
    string the pair arm alone could coerce —
    the pair arm produced `_beta_mean(1.0, 1.0) == 0.5`, labelled
    `confidence_source: "neutral"` and `belief_informed_order: True`. That is the
    neutral score this issue exists to remove, in the tier that decides order —
    and it contradicted the `ep` block, which answered `absent` for the same
    claim. The operand columns no longer carry a default, so the pair arm needs
    STORED operands.
    """
    from tortoise.ranking import GapsRanker, GraphRanker, StateRanker

    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "ZQIMPUTE an unreadable mirror")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.confidence = '0x1p4'",
                 params={"id": pid})
    ann = annotate_ep_batch(proj.g, [pid])[pid]
    assert ann.confidence_mean is None, ann

    state = StateRanker(projection=proj)._fetch_point_signals([pid])[pid]
    gaps = GapsRanker(projection=proj)._fetch_confidence_signals(
        [pid], labels=["Point"])[pid]
    for name, sig in (("state", state), ("gaps", gaps)):
        assert sig.get("confidence") is None, (
            f"{name} imputed a belief for a mirror it cannot read: {sig}")
        assert not sig.get("belief_informed_order"), (name, sig)
    # A claim WITH stored operands still reads normally — the fix is about the
    # default, not the pair.
    ok = sdk.create_point("statement", "ZQIMPUTE a measured claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 3.0, "
                 "n.posterior_beta = 1.0", params={"id": ok})
    assert StateRanker(projection=proj)._fetch_point_signals([ok])[
        ok]["confidence"] == pytest.approx(0.75, abs=1e-3)
    assert GraphRanker(projection=proj)._fetch_signals([ok], "point")[
        ok]["confidence"] == pytest.approx(0.75, abs=1e-3)


def test_a_numpy_boolean_is_not_a_belief_count(sdk):
    """Cycle 23 P2: `np.bool_` slipped the cycle-22 predicate.

    It is not a `bool` subclass — and in numpy 2 its `__name__` is `"bool"`, not
    `"bool_"` — while `float(np.bool_(True))` is `1.0`, so `_is_real_number`
    admitted it: `update_point(confidence=np.bool_(True))` stored a Boolean every
    reader reports absent, and `set_point_baseline(claim, np.bool_(True), 0.5)`
    stored `ep_alpha=true` AND set `baseline_set` + `baseline_source`, freezing
    the claim against the source-inheritance recompute.
    """
    import numpy as np

    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "ZQNPBOOL a pseudo-count")["id"]
    with pytest.raises(ValueError):
        sdk.update_point(pid, confidence=np.bool_(True))
    with pytest.raises(ValueError):
        sdk.set_point_baseline(pid, np.bool_(True), 0.5)
    stored = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence, n.ep_alpha, "
        "coalesce(n.baseline_set, false)", params={"id": pid}).result_set[0]
    assert stored == [None, None, False], stored


def test_a_mirror_outside_the_probability_band_is_not_a_belief(sdk):
    """Cycle 24 P1: the mirror's BAND was unenforced, so a legacy string the two
    coercions disagree about ranked FIRST.

    `toFloat` parses the hex-float spelling (`"0x1p4"` → 16.0); at the head this
    test was written for, the Python grammar refused it, so
    `ep_has_belief_cypher` said present while every Python presence surface said
    absent — for ONE row. Measured at `929e18c512`, the
    graph tier served `confidence: 16.0 / ep_state: "baseline"`,
    `graph_boost = 0.5·16 + 0.5·0 = 8.0` (outside the module's own documented
    `[0, 1]` boost invariant), and ranked the claim FIRST — above a genuinely
    measured 0.9 — while `annotate`, `why`, StateRanker and GapsRanker reported
    `absent`. One `tortoise_recall` row even carried `confidence: null` beside
    `ep_state: "baseline"`.

    The band is now part of the coercion on BOTH twins (`belief_mirror` /
    `belief_mirror_cypher`): a served belief is a probability, so a stored value
    outside `[0, 1]` is not a belief — which also closes the pre-existing
    out-of-range number (`confidence = 2.5` gave `graph_boost 2.5`).
    """
    from tortoise.analyze import _belief
    from tortoise.ranking import GapsRanker, GraphRanker, StateRanker, SubgraphExpander
    from tortoise.search_engine import belief_mirror, belief_value

    proj = sdk._get_proj()
    assert belief_mirror("0x1p4") is None
    assert belief_mirror(16.0) is None
    assert belief_mirror(2.5) is None
    assert belief_mirror(0.9) == pytest.approx(0.9)
    # ⛔ The BAND EDGE is decided by the value the STORE serves, which for a string
    # property is its float32 rounding: `"1.00000001"` rounds to exactly 1.0 and
    # READS on both twins, `"1.00000006"` is past the midpoint and is absent on
    # both. Comparing the exact double let Cypher say present-at-1.0 while Python
    # said absent — one claim, two `ep_state`s, inside a 6e-8 window (measured:
    # `'1.00000001'` was served `1.0 / baseline` on the graph tier and ranked
    # above a measured 0.9 while the `ep` block said `absent`).
    assert belief_mirror("1.00000001") == pytest.approx(1.0), (
        "the band must be applied to the float32 the store serves")
    assert belief_mirror("1.00000006") is None
    # ⛔ the double-rounding edge: this literal IS 1 + 2⁻²⁴, the float32 midpoint.
    # `strtof` rounds the DECIMAL up (1.0000001…, outside the band) while rounding
    # the double is round-half-to-even and sends it DOWN to 1.0 (inside it) — the
    # one shape where the twins disagreed about PRESENCE at the band edge.
    assert belief_mirror("1.0000000596046448") is None, (
        "the midpoint must be decided from the DECIMAL, as strtof does")
    assert proj.g.query(
        "RETURN toFloatOrNull('1.0000000596046448') AS v"
    ).result_set[0][0] == pytest.approx(1.0000001192092896)
    # …and the store's own answer is asserted, so the fixture cannot drift.
    assert proj.g.query(
        "RETURN toFloatOrNull('1.00000001') AS v").result_set == [[1.0]]

    for raw in ("'0x1p4'", "2.5", "-0.5"):
        pid = sdk.create_point("statement", f"ZQBAND mirror {raw}")["id"]
        proj.g.query(f"MATCH (n:Point {{id:$id}}) SET n.confidence = {raw}",
                     params={"id": pid})
        ann = annotate_ep_batch(proj.g, [pid])[pid]
        assert (ann.confidence_mean, ann.ep_state) == (None, EP_STATE_ABSENT), (
            raw, ann)
        node = next(n for n in SubgraphExpander(projection=proj).expand(
            [pid], depth=1)["nodes"] if n.get("id") == pid)
        assert node["ep_state"] == EP_STATE_ABSENT, (raw, node)
        assert node.get("confidence") is None, (raw, node)
        for sigs in (
            StateRanker(projection=proj)._fetch_point_signals([pid]),
            GraphRanker(projection=proj)._fetch_signals([pid], "point"),
            GapsRanker(projection=proj)._fetch_confidence_signals(
                [pid], labels=["Point"]),
        ):
            assert sigs[pid].get("confidence") is None, (raw, sigs[pid])
        # The Cypher-side `analyze` belief expression must agree with all of them.
        assert proj.g.query(
            "MATCH (c:Point {id:$id}) RETURN " + _belief("c"),
            params={"id": pid}).result_set[0][0] is None, raw
        assert belief_value(confidence=raw.strip("'")) is None

    # …and a claim carrying BOTH an out-of-band mirror and a stored pair serves
    # the PAIR on every surface (no `ep`-block-vs-ranking split).
    mixed = sdk.create_point("statement", "ZQBAND mirror + pair")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.confidence = '0x1p4', "
                 "n.posterior_alpha = 9.0, n.posterior_beta = 1.0",
                 params={"id": mixed})
    ann = annotate_ep_batch(proj.g, [mixed])[mixed]
    assert ann.confidence_mean == pytest.approx(0.9, abs=1e-3), ann
    assert GraphRanker(projection=proj)._fetch_signals([mixed], "point")[
        mixed]["confidence"] == pytest.approx(0.9, abs=1e-6)

    # The batch METRICS and the session digest read the same mirror, so they
    # must be banded too: `mean_grounding`'s DE2E-4 imputation is deliberate, but
    # an out-of-band value entering it as a belief is not.
    from tortoise.analyze import mean_grounding

    gap = sdk.create_point("statement", "ZQBAND a 2.5 mirror")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.confidence = 2.5",
                 params={"id": gap})
    assert mean_grounding(proj) <= 1.0, mean_grounding(proj)

    # …and the ORDER: an out-of-band mirror must not outrank a measured 0.9.
    hexed = sdk.create_point("statement", "ZQBAND a hexed claim")["id"]
    measured = sdk.create_point("statement", "ZQBAND a measured claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.confidence = '0x1p4'",
                 params={"id": hexed})
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 9.0, "
                 "n.posterior_beta = 1.0", params={"id": measured})
    graph = GraphRanker(projection=proj)._fetch_signals([hexed, measured], "point")
    assert graph[hexed]["confidence"] is None, graph[hexed]
    assert graph[measured]["confidence"] == pytest.approx(0.9, abs=1e-6)


def test_a_hex_operand_reads_the_same_on_every_surface(sdk):
    """Cycle 26 P2: refusing a spelling the STORE reads split presence between
    the twins.

    `toFloat("0x1p4")` is 16.0, and the pinned Python grammar refused hex floats,
    so a legacy `posterior_alpha="0x1p4", posterior_beta=1.0` pair — pre-write-guard
    data, or raw Cypher — answered FOUR things for one claim, measured: `absent /
    null` on the canonical `ep` block, `measured / 0.94` on `recall_ranking`
    (`confidence_source: "unreadable"`), `0.94` on `analyze`'s `_belief` — which
    then printed and RANKED it first in `most_uncertain` — and the `why` block
    self-contradictory (`measured: True` beside `ep_state: "absent"`).

    `float.fromhex` is the same parser the store uses for the same spelling, so
    both twins now read the literal; presence then follows the ONE rule, and every
    surface returns the same state and the same value.
    """
    from tortoise.analyze import _belief
    from tortoise.ranking import GapsRanker, GraphRanker, StateRanker, SubgraphExpander
    from tortoise.search_engine import numeric_operand
    from tortoise.why import assemble_why_blocks

    proj = sdk._get_proj()
    assert numeric_operand("0x1p4") == 16.0, (
        "the Python grammar refuses a spelling the store reads — the twins "
        "split, and presence is decided by the Cypher side")
    pid = sdk.create_point("statement", "ZQHEXOP a hex operand pair")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = '0x1p4', "
                 "n.posterior_beta = 1.0", params={"id": pid})

    ann = annotate_ep_batch(proj.g, [pid])[pid]
    ep = assemble_why_blocks(proj, [pid])[pid].get("ep") or {}
    node = next(n for n in SubgraphExpander(projection=proj).expand(
        [pid], depth=1)["nodes"] if n.get("id") == pid)
    state = StateRanker(projection=proj)._fetch_point_signals([pid])[pid]
    graph = GraphRanker(projection=proj)._fetch_signals([pid], "point")[pid]
    gaps = GapsRanker(projection=proj)._fetch_confidence_signals(
        [pid], labels=["Point"])[pid]
    belief = proj.g.query("MATCH (c:Point {id:$id}) RETURN " + _belief("c"),
                          params={"id": pid}).result_set[0][0]

    # 16 / 17 — every surface names the SAME state and the SAME value.
    for name, got in (("ep block", ann.confidence_mean),
                      ("why ep", ep.get("confidence_mean")),
                      ("subgraph", node.get("confidence")),
                      ("recall_ranking", state.get("confidence")),
                      ("graph_ranking", graph.get("confidence")),
                      ("gaps_ranking", gaps.get("confidence")),
                      ("analyze", belief)):
        # `annotate`/`why` round to 4 places by contract; the readers do not.
        assert got == pytest.approx(16.0 / 17.0, abs=1e-4), (name, got)
    for name, got in (("ep block", ann.ep_state),
                      ("why ep", ep.get("ep_state")),
                      ("subgraph", node.get("ep_state")),
                      ("recall_ranking", state.get("ep_state"))):
        assert got == EP_STATE_MEASURED, (name, got)
    # The self-contradiction is gone too: `measured` cannot ride an `absent` row.
    assert ep.get("measured") is True, ep


def test_the_object_aggregate_reports_absent_without_a_mean_over_nothing(sdk):
    """Cycle 20: an Object whose about-Points carry NO belief.

    `_fetch_object_signals` filters the `None`s out before averaging (the old
    `NEUTRAL_CONFIDENCE` 0.5 was the fabrication one level up), and the guard
    that makes it total was unpinned: keeping the Nones made `sum(confs)` raise
    `TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'` and 285
    tests stayed green. Both arms are asserted here — measured and absent — so
    removing the filter is caught.
    """
    proj = sdk._get_proj()
    # `_fetch_object_signals` lives on StateRanker (the owner that takes the
    # signal map for a batch of mixed labels).
    ranker = StateRanker(projection=proj)

    measured_point = sdk.create_point("statement", "ZQOBJ a measured claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 9.0, "
                 "n.posterior_beta = 1.0", params={"id": measured_point})
    absent_point = sdk.create_point("statement", "ZQOBJ a belief-less claim")["id"]

    # Created with raw Cypher on purpose: `sdk.create_object` DEDUPES by kind
    # and returned ONE node for two different names (measured: same id twice,
    # degree 2), which would make both assertions read the same aggregate.
    measured_obj = f"zqobj-measured-{uuid.uuid4().hex[:8]}"
    absent_obj = f"zqobj-absent-{uuid.uuid4().hex[:8]}"
    for oid, name in ((measured_obj, "ZQOBJ measured object"),
                      (absent_obj, "ZQOBJ absent object")):
        proj.g.query("CREATE (o:Object {id:$id, name:$name})",
                     params={"id": oid, "name": name})
    for oid, pid in ((measured_obj, measured_point), (absent_obj, absent_point)):
        proj.g.query("MATCH (p:Point {id:$pid}), (o:Object {id:$oid}) "
                     # `aboutObject` is in `ABOUT_EDGE_TYPES`; a bare `:about`
                     # edge is not traversed by the aggregate and would have
                     # produced an "absent" Object for a measured Point.
                     "MERGE (p)-[:aboutObject]->(o)",
                     params={"pid": pid, "oid": oid})

    sigs = ranker._fetch_object_signals([measured_obj, absent_obj])
    assert set(sigs) == {measured_obj, absent_obj}, sigs
    assert sigs[measured_obj]["confidence"] == pytest.approx(0.9, abs=1e-4), sigs
    assert sigs[measured_obj]["ep_state"] == EP_STATE_MEASURED, sigs
    # The absent arm: no value, and a NAMED state — never a mean over nothing.
    assert sigs[absent_obj]["confidence"] is None, sigs[absent_obj]
    assert sigs[absent_obj]["ep_state"] == EP_STATE_ABSENT, sigs[absent_obj]
    assert sigs[absent_obj]["has_ep"] is False, sigs[absent_obj]


def test_absent_never_outranks_a_strongly_measured_claim(sdk):
    """The property §6.3 actually protects, at the top of the range.

    `1.0` (the multiplicative identity) is the MAXIMUM of the confidence axis,
    so an absent row competing on it would beat a measured 0.9 (0.5 vs 0.45).
    The refusal is a sort TIER: every row with a belief sorts above every row
    without one, at equal relevance.
    """
    absent = _absent_point(sdk)
    strong = sdk.create_point("statement", "a strongly measured claim")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id = $id "
        "SET n.posterior_alpha = 19.0, n.posterior_beta = 1.0",
        params={"id": strong},
    )
    # The DISCRIMINATING comparison is against a measured claim worth exactly
    # what absence used to be fabricated as: 0.5. Pre-fix both rows carried the
    # same number and the same (absent) tier, so the order was decided by row
    # position and this assertion could not fail — which is why the twin at
    # 0.95 is not the pin. Here the tier must separate them.
    symmetric = _symmetric_point(sdk)
    rows = [{"id": absent, "scores": {"rrf": 1.0}},
            {"id": strong, "scores": {"rrf": 1.0}},
            {"id": symmetric, "scores": {"rrf": 1.0}}]
    ranked = {r["id"]: r["recall_ranking"]
              for r in StateRanker(projection=sdk._get_proj()).rerank(rows)}
    order = [r["id"] for r in StateRanker(projection=sdk._get_proj()).rerank(rows)]
    assert order[0] == strong, (
        "an absent belief outranked a claim measured at 0.95 — the identity "
        "factor made 'nothing measured this' the strongest evidence")
    assert ranked[absent]["belief_informed_order"] is False
    assert ranked[symmetric]["belief_informed_order"] is True, ranked[symmetric]
    assert ranked[absent]["ep_state"] == "absent", ranked[absent]
    assert ranked[symmetric]["ep_state"] == "measured", ranked[symmetric]
    assert ranked[absent]["confidence"] is None, (
        "the absent row still carries a fabricated confidence: "
        + repr(ranked[absent]))


def test_absent_belief_contributes_no_factor_to_the_recall_gate(sdk):
    """The absent BELIEF term does not participate at all — neither as a
    fabricated 0.5 (a mid-strength claim) nor as a 0.0 (which would be
    "absence is evidence against", the #898 concern this keeps). The row is
    ordered by its remaining signal, relevance."""
    pid = _absent_point(sdk)
    rr = StateRanker(projection=sdk._get_proj()).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["recall_ranking"]
    # relevance_norm is the midpoint of a 1-element set (0.5); with no belief
    # factor the gate leaves it untouched.
    assert rr["relevance_norm"] == pytest.approx(0.5, abs=1e-6)
    assert rr["base_score"] == pytest.approx(rr["relevance_norm"], abs=1e-6)


# ── The ep block and the ranker AGREE on the state ─────────────────────────

def test_ep_block_and_rankers_agree_on_the_state(sdk):
    """§6.3's last line, made checkable: whatever the `ep` block says about a
    claim's belief, the rankers that ordered on it say the same."""
    absent = _absent_point(sdk)
    measured = _symmetric_point(sdk)
    baseline = sdk.create_point("statement", "a baseline-backed claim 2")["id"]
    sdk.set_point_baseline(baseline, 3.0, 1.0)

    eps = annotate_ep_batch(sdk._get_proj().g, [absent, measured, baseline])
    sigs = GraphRanker(projection=sdk._get_proj())._fetch_signals(
        [absent, measured, baseline], "point")
    recs = {r["id"]: r["recall_ranking"]
            for r in StateRanker(projection=sdk._get_proj()).rerank(
                [{"id": p, "scores": {"rrf": 1.0}}
                 for p in (absent, measured, baseline)])}
    # The canonical `ep` block — the block `/v1/context`, the volunteer reflex
    # and the pushed webhook actually serve — is a DIFFERENT producer
    # (`why._assemble_ep_rows`) from `annotate_ep_batch`. Covering only the
    # search path is how the fabricated 0.5 survived there.
    from tortoise.why import assemble_why_blocks
    whys = assemble_why_blocks(sdk._get_proj(), [absent, measured, baseline])

    for pid in (absent, measured, baseline):
        assert sigs[pid]["ep_state"] == eps[pid].ep_state, pid
        assert recs[pid]["ep_state"] == eps[pid].ep_state, pid
        assert whys[pid]["ep"]["ep_state"] == eps[pid].ep_state, pid
        # A value exists exactly when the state is not `absent` — on EVERY
        # surface, including the why block.
        assert (eps[pid].confidence_mean is not None) == (
            eps[pid].ep_state != EP_STATE_ABSENT), pid
        assert (sigs[pid]["confidence"] is not None) == (
            eps[pid].ep_state != EP_STATE_ABSENT), pid
        assert (whys[pid]["ep"]["confidence_mean"] is not None) == (
            eps[pid].ep_state != EP_STATE_ABSENT), pid
        # …and the VALUE agrees, not only its presence. Two surfaces under the
        # same state name reporting different numbers is the same class of bug
        # (the mirror/preference split) one level down.
        if eps[pid].ep_state != EP_STATE_ABSENT:
            assert whys[pid]["ep"]["confidence_mean"] == pytest.approx(
                eps[pid].confidence_mean, abs=1e-4), pid
            assert sigs[pid]["confidence"] == pytest.approx(
                eps[pid].confidence_mean, abs=1e-4), pid


def test_mirror_only_claim_agrees_on_the_value_everywhere(sdk):
    """The THIRD shape: a belief stored ONLY in the `n.confidence` mirror.

    `compute_confidence` / `dream` write the mirror with no α/β, and a
    pre-#852 node carries nothing else — so this is a live population, not a
    legacy corner. It must report `baseline` AND the same NUMBER on every
    surface; the `ep` block used to compute from α/β alone and answer the
    Beta(1,1) 0.5 for it while the rankers read 0.95.
    """
    pid = sdk.create_point("statement", "a mirror-only claim")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence = 0.95", params={"id": pid})

    from tortoise.why import assemble_why_blocks
    eps = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    why_ep = assemble_why_blocks(sdk._get_proj(), [pid])[pid]["ep"]
    sig = GraphRanker(projection=sdk._get_proj())._fetch_signals([pid], "point")[pid]
    rec = StateRanker(projection=sdk._get_proj()).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["recall_ranking"]

    assert eps.ep_state == EP_STATE_BASELINE, eps.ep_state
    for name, value in (("ep block", eps.confidence_mean),
                        ("why block", why_ep["confidence_mean"]),
                        ("graph signal", sig["confidence"]),
                        ("recall_ranking", rec["confidence"])):
        assert value == pytest.approx(0.95, abs=1e-4), name
    assert why_ep["ep_state"] == EP_STATE_BASELINE, why_ep


def test_degenerate_posterior_pair_is_ABSENT_on_every_surface(sdk):
    """A stored (α, β) = (0, 0) pair is NO belief — not a 0.5, not a NaN.

    `ep.py::_write_node` writes that pair verbatim (a decayed or never-seeded
    posterior), and Beta(0, 0) is not a distribution: it carries no
    information. Read as "columns present" it was a belief with no computable
    mean, and one claim got FOUR answers — 0.5 in the `ep` block, `None` in
    `recall_ranking`, `absent` in `gaps_ranking`, and **NaN** from
    `graph_ranking`'s unguarded α/(α+β) division (serialised as a bare `NaN`
    token). The presence predicate is therefore a SUM (`α + β > 0`), and this
    is the regression test that keeps every surface on that one rule.
    """
    pid = sdk.create_point("statement", "a degenerate posterior pair")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 0.0, "
        "n.posterior_beta = 0.0", params={"id": pid})
    eps = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    proj = sdk._get_proj()
    rec = StateRanker(projection=proj).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["recall_ranking"]
    gap = GapsRanker(projection=proj).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["gaps_ranking"]
    graph = GraphRanker(projection=proj).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["graph_ranking"]
    sig = GraphRanker(projection=proj)._fetch_signals([pid], "point")[pid]

    assert eps.ep_state == EP_STATE_ABSENT, (
        "Beta(0, 0) is not a distribution — the pair carries no belief")
    assert eps.confidence_mean is None, (
        "a zero-total pair has NO mean; answering 0.5 is the fabrication")
    for name, value in (("recall_ranking", rec["confidence"]),
                        ("gaps_ranking", gap["confidence"]),
                        ("graph_ranking", graph["confidence"]),
                        ("graph signal", sig["confidence"])):
        # A NaN-specific assertion here would be unreachable: NaN is not None,
        # so this pin already grades the unguarded-0/0 path.
        assert value is None, f"{name} reports {value!r} for a zero-total pair"
    for name, state in (("recall_ranking", rec["ep_state"]),
                        ("gaps_ranking", gap["ep_state"]),
                        ("graph_ranking", graph["ep_state"]),
                        ("graph signal", sig["ep_state"])):
        assert state == EP_STATE_ABSENT, f"{name} reports {state!r}"
    assert rec["belief_informed_order"] is False, rec


def test_confidence_source_never_contradicts_the_ep_state_beside_it(sdk):
    """`confidence_source` and `ep_state` must name the SAME fact on one row.

    The source string is the vocabulary a consumer filters on ("show me the
    rows with no belief"), and `ep_state` is the state the `ep` block reports
    for the same claim. The reviewed head emitted `confidence_source:
    "absent"` BESIDE `ep_state: "measured"` for a stored (α, β) = (0, 0)
    pair: one key said "nothing is persisted" while the other said "EP
    measured this", so no consumer could trust either. (`_confidence_of`
    collapsed every `None` value to the absent source, and the state was read
    from the signal, which still called that pair present — two presence rules
    on one row.)

    Both rules are now the shared predicate, so the agreement is structural —
    but "structural" is a claim, and this is the assertion that keeps it true
    across all ELEVEN stored shapes if either side is edited again.

    Five of the eleven are the readability boundary, and they are DIFFERENT
    shapes with different honest answers:

    * an OPERAND written as a hex-float literal (`posterior_alpha = '0x1p4'`) was
      the `"unreadable"` shape until cycle 26 taught the Python twin to read the
      same spelling (`float.fromhex`) — the two coercions now agree (16.0) and
      the row reads `measured / 16/17` with `confidence_source: "posterior"` on
      every surface, which is what this test now asserts;
    * a MIRROR written the same way (`n.confidence = '0x1p4'`) is not a
      probability, so the served-belief band rejects it on BOTH twins and the
      row is `absent`/`absent`;
    * an exact float32 SUBNORMAL operand (`0x1p-127`, cycle 27) reads on both
      twins — the underflow rule refuses only an INEXACT subnormal;
    * a MIRROR at the band edge whose literal the DOUBLE cannot hold exactly
      (`0x1.0000010000000001p0`, cycle 28) rounds past 1.0 in the store and to
      `1.0` in Python if the midpoint is decided from the double: `absent` /
      `present at maximum certainty`, one claim, two answers;
    * an OPERAND whose `p` EXPONENT the old bound refused while its mantissa
      cancelled that exponent back into band (#11897): the literal above is
      exactly 1 + 2⁻²⁴, so the store serves it and the reader called it absent —
      `measured / 0.25` on the Cypher-reading surfaces, `absent` on the Python
      ones, in the SAME response (not `0.5`: the neutral fallback was already
      gone, see the headline test). The bound now measures the value's MAGNITUDE,
      not the exponent as written.

    Collapsing those two into one shape is how the token lost its only test
    when the band landed (cycle 25 review): the fixture stayed the mirror, so it
    asserted `absent` and nothing exercised `"unreadable"` any more.
    """
    proj = sdk._get_proj()

    def _point(content, sql=None, params=None):
        pid = sdk.create_point("statement", content)["id"]
        if sql:
            proj.g.query(f"MATCH (n:Point {{id:$id}}) SET {sql}",
                         params={"id": pid, **(params or {})})
        return pid

    absent = _point("no belief at all")
    half_pair = _point("a beta-only row", "n.posterior_beta = 3.0")
    zero_total = _point("a degenerate pair", "n.posterior_alpha = 0.0, "
                        "n.posterior_beta = 0.0")
    mirror_only = _point("a mirror-only claim", "n.confidence = 0.95")
    measured = _symmetric_point(sdk)  # α = β = 2.0, a real measured 0.5
    baseline = _point("a declared baseline")
    sdk.set_point_baseline(baseline, 18.0, 2.0)
    # The readability boundary, split into its TWO shapes (they are not the
    # same row and they do not have the same answer — see the docstring):
    #
    # (a) OPERAND: `toFloatOrNull('0x1p4')` is 16.0, so the Cypher presence
    #     predicate calls this claim measured — and since cycle 26 Python reads
    #     the same literal with `float.fromhex`, so its value agrees instead of
    #     being `"unreadable"`. Asserted as agreement below.
    hex_operand = _point("a hex-float operand",
                        "n.posterior_alpha = '0x1p4', n.posterior_beta = '1'")
    # (b) MIRROR: 16.0 is not a probability, so the served-belief band rejects
    #     it on both twins — the row is ABSENT, not "unreadable".
    out_of_band_mirror = _point("a hex-float mirror",
                                "n.confidence = '0x1p4'")
    # (c) SUBNORMAL operand: the underflow edge is INEXACTNESS, not subnormal-ness
    #     (cycle 27). `0x1p-127` is a float32 subnormal the literal names exactly,
    #     so the store serves it; the rule used to refuse every subnormal, which
    #     made the `ep` block say `absent` beside every ranker's `measured / 5.9e-39`.
    hex_subnormal = _point("a hex-subnormal operand",
                           "n.posterior_alpha = '0x1p-127', "
                           "n.posterior_beta = '1'")
    # (d) BAND-EDGE mirror, decided by a double-ROUNDING fallback (cycle 28): the
    #     literal is 1 + 2⁻²⁴ + 2⁻⁶⁴, so `float.fromhex` rounds it ONTO the
    #     float32 midpoint and the old tie-break answered `1.0` — inside the
    #     band, present at MAXIMUM certainty — while the store serves
    #     1.000000119… and calls the row ABSENT. Measured as a presence split.
    band_edge_mirror = _point("a band-edge hex mirror",
                              "n.confidence = '0x1.0000010000000001p0'")
    # (e) The EXPONENT BOUND (#11897): a 4,000-bit mantissa CANCELS a `p-4100`,
    #     so this literal is exactly 1 + 2⁻²⁴ — in band, served by the store —
    #     while the old bound refused it for having `|exponent| > 4096`. The
    #     Python-reading surfaces then said `absent` (this ranker's `confidence`
    #     was `None`) while the Cypher-reading ones reported the real 0.25. β = 3
    #     keeps the mean off 0.5, so a re-introduced neutral fallback would fail
    #     the `== 0.25` assertion rather than pass it by coincidence.
    hex_cancelling = _point(
        "a cancelling hex exponent",
        "n.posterior_alpha = $a, n.posterior_beta = '3'",
        {"a": "0x" + format((2**24 + 1) * 2**4076, "x") + "p-4100"})

    ids = [absent, half_pair, zero_total, mirror_only, measured, baseline,
           hex_operand, out_of_band_mirror, hex_subnormal, band_edge_mirror,
           hex_cancelling]
    recs = {r["id"]: r["recall_ranking"] for r in
            StateRanker(projection=proj).rerank(
                [{"id": p, "scores": {"rrf": 1.0}} for p in ids])}
    gaps = {r["id"]: r["gaps_ranking"] for r in
            GapsRanker(projection=proj).rerank(
                [{"id": p, "scores": {"rrf": 1.0}} for p in ids])}

    for pid in ids:
        for name, row in (("recall_ranking", recs[pid]),
                          ("gaps_ranking", gaps[pid])):
            state, source = row["ep_state"], row["confidence_source"]
            assert (source == EP_STATE_ABSENT) == (state == EP_STATE_ABSENT), (
                f"{name} reports confidence_source={source!r} beside "
                f"ep_state={state!r} — the two keys describe one fact")
            # …and the VALUE follows the same rule, so a row cannot say "no
            # belief" in the source and carry a number in the value. On a LIVE
            # row a null has TWO causes and `confidence_source` names which:
            # `absent` (nothing is persisted) or `unreadable` (a belief exists
            # and its stored spelling cannot be decoded here). A third cause —
            # the #2598 DRAFT gate, source `uncomputed` — belongs to
            # `test_a_draft_withholds_its_belief_without_denying_it` below;
            # every row here is live, so it is not admitted here.
            if row["confidence"] is None:
                assert source in (EP_STATE_ABSENT,
                                  UNREADABLE_CONFIDENCE_SOURCE), (
                    f"{name} reports a null confidence with no source naming "
                    f"why: source={source!r}, ep_state={state!r}")
            else:
                assert source in ("posterior", "neutral"), (
                    f"{name} reports confidence={row['confidence']!r} from "
                    f"source={source!r}")
        # The two rankers must not disagree about the same node either.
        assert recs[pid]["ep_state"] == gaps[pid]["ep_state"], pid
        assert (recs[pid]["confidence_source"] == EP_STATE_ABSENT) == (
            gaps[pid]["confidence_source"] == EP_STATE_ABSENT), pid

    # The pre-fix contradiction, named: a zero-total pair is ABSENT in BOTH
    # keys — never `"absent"` beside `"measured"`.
    for row in (recs[zero_total], gaps[zero_total]):
        assert row["ep_state"] == EP_STATE_ABSENT, row
        assert row["confidence_source"] == EP_STATE_ABSENT, row
        assert row["confidence"] is None, row
    # …and the source still names a REAL state for the rows that have a belief.
    assert recs[measured]["confidence_source"] == "posterior", recs[measured]
    assert gaps[measured]["confidence_source"] == "posterior", gaps[measured]
    for pid in (mirror_only, baseline):
        assert recs[pid]["ep_state"] == EP_STATE_BASELINE, recs[pid]
        assert recs[pid]["confidence_source"] != EP_STATE_ABSENT, recs[pid]

    # (e) The EXPONENT-BOUND shape: a served value must be RANKED on. This row is
    #     the #11897 regression — `recall_ranking`/`gaps_ranking` answered
    #     `confidence: None` (Python refused the operand) while `GraphRanker`
    #     reported the store's 0.25 in the same response, so one claim carried no
    #     belief on one surface and a measured one on another. The `!= 0.5` clause
    #     below is a TRIPWIRE against the pre-#11702 neutral fallback returning;
    #     it is not what this shape produced (nothing answered 0.5).
    for name, row in (("recall_ranking", recs[hex_cancelling]),
                      ("gaps_ranking", gaps[hex_cancelling])):
        assert row["ep_state"] == EP_STATE_MEASURED, (name, row)
        assert row["confidence"] == pytest.approx(0.25, abs=1e-6), (name, row)
        assert row["confidence_source"] == "posterior", (name, row)

    # (a) The OPERAND shape: a hex-float pair. This USED to be the reachable
    # "unreadable" shape — the store reads `toFloatOrNull('0x1p4')` (16.0) and the
    # pinned Python grammar refused hex, so the `ep` block said `absent` beside the
    # rankers' `measured / 0.94`, and `analyze` printed and RANKED the claim. Cycle
    # 26 closed it at the source (`float.fromhex` is the same parser for the same
    # spelling), so the fixture now asserts AGREEMENT on both halves: the two
    # coercions read the literal, and every surface reports one state and one
    # value. ⚠️ That closure was a MEASUREMENT, and the next two cycles found two
    # more shapes inside the class (c) — so `UNREADABLE_CONFIDENCE_SOURCE` is not
    # claimed here to be unreachable by construction: it names no shape this
    # table pins, stays as a defensive label, and every new spelling belongs in
    # `test_the_twins_parse_the_same_string_language` first.
    assert _se.numeric_operand("0x1p4") == 16.0, (
        "the Python grammar stopped reading a spelling the store reads — the "
        "twins split again, and presence is then decided by whichever side is "
        "asked")
    assert proj.g.query(
        "RETURN toFloatOrNull('0x1p4') AS v").result_set == [[16.0]], (
        "the store no longer reads the hex-float spelling — this fixture is no "
        "longer the shape it claims")
    for name, row in (("recall_ranking", recs[hex_operand]),
                      ("gaps_ranking", gaps[hex_operand])):
        assert row["ep_state"] == EP_STATE_MEASURED, (name, row)
        assert row["confidence"] == pytest.approx(16.0 / 17.0, abs=1e-5), (
            name, row)
        assert row["confidence_source"] == "posterior", (name, row)
    eps = annotate_ep_batch(proj.g, [hex_operand])[hex_operand]
    assert eps.ep_state == EP_STATE_MEASURED, eps
    assert eps.confidence_mean == pytest.approx(16.0 / 17.0, abs=1e-4), eps

    # (b) The MIRROR shape: the band resolves it rather than reporting it. A
    # served belief is a probability, so `16.0` is not a belief on EITHER twin —
    # `belief_mirror_cypher` answers null and `ep_has_belief_cypher` is false,
    # which is what the Python side always said. (Before the band this row was
    # `ep_state: "baseline"` with a null value and `confidence_source:
    # "unreadable"`; the reviewer measured that state ranking the claim FIRST
    # with `graph_boost 8.0` above a measured 0.9 — so the honest answer is
    # absent, not "unreadable".)
    assert _se.belief_mirror("0x1p4") is None, (
        "the band admits the hex-float spelling — the fixture is no longer the "
        "out-of-band shape")
    for name, row in (("recall_ranking", recs[out_of_band_mirror]),
                      ("gaps_ranking", gaps[out_of_band_mirror])):
        assert row["ep_state"] == EP_STATE_ABSENT, (name, row)
        assert row["confidence"] is None, (name, row)
        assert row["confidence_source"] == EP_STATE_ABSENT, (name, row)
    assert recs[out_of_band_mirror]["belief_informed_order"] is False, (
        recs[out_of_band_mirror])

    # (c) The SUBNORMAL operand, pinned on both halves (cycle 27 P2). `strtof`
    # sets ERANGE for an INEXACT subnormal only, so a literal naming one exactly
    # READS. The rule refused all subnormals, which put `absent` in the `ep`
    # block beside `measured / 5.877e-39` in every ranker — the same two-keys-of-
    # one-response split, 23 exponents wide. Both directions are asserted so a
    # fix cannot trade this split for its mirror image: the exact subnormal
    # reads on BOTH twins and every surface agrees on state AND value, while
    # `0x1.fffffep-127` (the subnormal/normal midpoint, inexact) is null on both
    # (it is in `test_the_twins_parse_the_same_string_language`'s table).
    sub = float.fromhex("0x1p-127")
    assert _se.numeric_operand("0x1p-127") == sub, (
        "the exact-subnormal window is refused here again — the twins split on "
        "a value the store serves")
    assert proj.g.query(
        "RETURN toFloatOrNull('0x1p-127') AS v").result_set[0][0] == (
            pytest.approx(sub, rel=1e-6)), (
        "the store stopped reading an exactly-representable subnormal — this "
        "fixture is no longer the shape it claims")
    sub_mean = sub / (1.0 + sub)
    for name, row in (("recall_ranking", recs[hex_subnormal]),
                      ("gaps_ranking", gaps[hex_subnormal])):
        # ⚠️ The rankers ROUND the mean to 6 dp (`round(_beta_mean(...), 6)`), so a
        # 5.9e-39 belief is served as 0.0 — pre-existing, unchanged by #11702, and
        # why this asserts the rounded value. The STATE is what must agree, and
        # `confidence_source: "posterior"` is what says the 0.0 is a rounded real
        # number rather than the old "nothing measured" 0.0 (that is the whole
        # point of §6.3). The `ep` block below serves the unrounded mean.
        assert row["ep_state"] == EP_STATE_MEASURED, (name, row)
        assert row["confidence"] == pytest.approx(round(sub_mean, 6)), (
            name, row)
        assert row["confidence_source"] == "posterior", (name, row)
    sub_eps = annotate_ep_batch(proj.g, [hex_subnormal])[hex_subnormal]
    assert sub_eps.ep_state == EP_STATE_MEASURED, sub_eps
    assert sub_eps.confidence_mean == pytest.approx(sub_mean, rel=1e-6), sub_eps

    # (d) The BAND-EDGE mirror (cycle 28). The literal is the float32 midpoint
    # 1 + 2⁻²⁴ PLUS 2⁻⁶⁴, i.e. past the band edge — but `float.fromhex` keeps
    # only 53 bits, so the double lands exactly ON the midpoint and the old
    # fallback answered `1.0`, INSIDE the band: Python read `baseline / 1.0`
    # (maximum certainty) where the store's band rejected the row as ABSENT.
    # This is the shape that made the "no stored shape reaches it" sentences
    # false for the second time, so both halves are asserted.
    band_lit = "0x1.0000010000000001p0"
    assert _se.belief_mirror(band_lit) is None, (
        "the band admits a literal the store rejects — the midpoint was decided "
        "from the double again, and this row is a presence split")
    assert proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN " + _se.belief_mirror_cypher(
            "n.confidence"), params={"id": band_edge_mirror}).result_set == [
                [None]], (
        "the store now reads this literal as in-band — the fixture is no longer "
        "the band-edge shape it claims")
    assert proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN " + _se.ep_has_belief_cypher("n"),
        params={"id": band_edge_mirror}).result_set == [[False]], (
            "the store's presence predicate disagrees with its own band read")
    for name, row in (("recall_ranking", recs[band_edge_mirror]),
                      ("gaps_ranking", gaps[band_edge_mirror])):
        assert row["ep_state"] == EP_STATE_ABSENT, (name, row)
        assert row["confidence"] is None, (name, row)
        assert row["confidence_source"] == EP_STATE_ABSENT, (name, row)
    assert recs[band_edge_mirror]["belief_informed_order"] is False, (
        recs[band_edge_mirror])


def test_a_draft_withholds_its_belief_without_denying_it(sdk):
    """A #2598 draft row reports `confidence: null` while it HAS a belief.

    The draft gate (pre-existing, owner-reviewed, NOT reversed by #11702)
    suppresses the DISPLAY value so a staged claim is not ranked on a belief.
    #11702 adds `ep_state` and `belief_informed_order` beside that null, so the
    three keys must be readable together: the state NAME stays true ("baseline"
    for a persisted prior), the flag stays true (the belief DID move the order),
    and `confidence_source: "uncomputed"` is what says the null is a
    withholding rather than an absence. Measured before this test: `ep_state:
    "baseline"` + `belief_informed_order: true` + `confidence: null` on one row,
    with no test covering a draft at all — so a revert that called it "absent"
    would have been green.
    """
    proj = sdk._get_proj()
    draft = sdk.create_point(
        "statement", "a staged claim that carries a prior",
        credibility="high", status="draft")["id"]
    rows = {r["id"]: r for r in StateRanker(projection=proj).rerank(
        [{"id": draft, "scores": {"rrf": 1.0}}])}
    row = rows[draft]["recall_ranking"]
    assert row.get("draft") is True, row
    # The belief exists and is NAMED…
    assert row["ep_state"] == EP_STATE_BASELINE, row
    # …the display value is withheld, and the source says so…
    assert row["confidence"] is None, row
    assert row["confidence_source"] == UNCOMPUTED_CONFIDENCE_SOURCE, row
    # …and the flag reports what actually happened to the ORDER, which is that
    # the belief participated (`base_score` carries the confidence factor).
    assert row["belief_informed_order"] is True, row
    # The `ep` block for the same claim reports the same state and a VALUE —
    # so the null is provably a display decision, not a missing belief.
    ep = row  # keep the row readable in the failure message
    del ep
    ann = annotate_ep_batch(proj.g, [draft])[draft]
    assert ann.ep_state == EP_STATE_BASELINE, ann
    assert ann.confidence_mean is not None, ann


def test_zero_total_pair_does_not_survive_an_equal_relevance_order(sdk):
    """The absent tier places the zero-total claim after a MEASURED 0.1 claim.

    The value check above is not enough on its own: the failure it guards was
    an ORDERING one (absent sorted as if it were 0.5, above a measured low).
    """
    absent = sdk.create_point("statement", "zero-total claim")["id"]
    low = sdk.create_point("statement", "measured low claim")["id"]
    proj = sdk._get_proj()
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 0.0, "
                 "n.posterior_beta = 0.0", params={"id": absent})
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 1.0, "
                 "n.posterior_beta = 9.0", params={"id": low})
    rows = StateRanker(projection=proj).rerank(
        [{"id": absent, "scores": {"rrf": 1.0}},
         {"id": low, "scores": {"rrf": 1.0}}])
    order = [r["id"] for r in rows]
    assert order == [low, absent], (
        f"absent must sort after a measured 0.1 claim, got {order}")
    assert rows[0]["recall_ranking"]["confidence"] == pytest.approx(0.1, abs=1e-4)
    assert rows[1]["recall_ranking"]["confidence"] is None


def test_analyze_evidence_chain_reports_null_and_sorts_an_absent_claim_last(sdk):
    """`tortoise_analyze` is a read surface too — it must not score an absent claim.

    The `strongest_for` / `counter_arguments` / `evidence_chain` templates
    deliberately ADMIT a belief-less claim (the IMPL edge is the evidence), so
    the belief never gates them — but they RANK by it, and the `CASE … ELSE 0.5`
    arm did not distinguish "no columns" from a stored (0, 0) pair. The absent
    supporter therefore printed `conf: 0.50` and sorted ABOVE a claim genuinely
    measured at 0.10, which is the ranker ordering on a fabricated absence.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    g.query("CREATE (:Point {id:'absent-sup', content:'absent evidence claim', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'low-sup', content:'measured low claim', "
            "is_operator:false, status:'live', posterior_alpha:1.0, "
            "posterior_beta:9.0})")
    g.query("CREATE (:Point {id:'tgt', content:'evidence ai strategy is working', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'op', content:'IMPL', op_type:'IMPL', "
            "is_operator:true, status:'live'})")
    g.query("MATCH (a:Point {id:'absent-sup'}), (b:Point {id:'low-sup'}), "
            "(o:Point {id:'op'}), (t:Point {id:'tgt'}) "
            "CREATE (a)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:1}]->(t), "
            "       (b)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:2}]->(t)")

    out = analyze("show me the evidence chain for the AI strategy",
                  proj=proj, use_llm=False)
    answer = out["answer"]
    assert "measured low claim" in answer and "absent evidence claim" in answer, answer
    # The measured 0.1 claim must lead; the absent one follows with NO number.
    first = answer.index("measured low claim")
    second = answer.index("absent evidence claim")
    assert first < second, (
        "an absent belief must sort LAST, not above a measured 0.1 claim: " + answer)
    assert "0.50" not in answer, (
        "the absent claim was scored 0.50 — a fabricated neutral belief: " + answer)


@pytest.mark.parametrize("template", ["strongest_for", "counter_arguments",
                                     "evidence_chain"])
def test_every_ranking_template_renders_an_absent_belief_with_no_number(
        sdk, template):
    """All THREE admitting templates, not just the one a previous test picked.

    The reviewed finding named `strongest_for` (:99), `counter_arguments`
    (:116) and `evidence_chain` (:194) — the three templates that carry no
    presence gate in their WHERE and therefore take the shared belief
    expression directly. `evidence_chain` was pinned; the other two were not,
    so a bare `opponent.confidence` (or a re-introduced `ELSE 0.5`) on either
    would have kept the suite green. This runs each template's OWN cypher and
    its OWN formatter, so both halves of that surface are graded.

    Both directions matter: the absent claim must still be RETURNED (the IMPL / 
    NAND edge is the evidence — absence is not disqualifying) and must carry no
    number and no rank above a measured 0.1 claim.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import TEMPLATES

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    g.query("CREATE (:Point {id:'absent-rel', content:'absent related claim', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'low-rel', content:'measured low claim', "
            "is_operator:false, status:'live', posterior_alpha:1.0, "
            "posterior_beta:9.0})")
    g.query("CREATE (:Point {id:'tgt', content:'the ai strategy is working', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'impl', content:'IMPL', op_type:'IMPL', "
            "is_operator:true, status:'live'})")
    g.query("CREATE (:Point {id:'nand', content:'NAND', op_type:'NAND', "
            "is_operator:true, status:'live'})")
    # IMPL: both related claims support the target (idx 0 = the source).
    g.query("MATCH (a:Point {id:'absent-rel'}), (b:Point {id:'low-rel'}), "
            "(o:Point {id:'impl'}), (t:Point {id:'tgt'}) "
            "CREATE (a)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:1}]->(t), "
            "       (b)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:2}]->(t)")
    # NAND: both contradict the target, for `counter_arguments`.
    g.query("MATCH (a:Point {id:'absent-rel'}), (b:Point {id:'low-rel'}), "
            "(o:Point {id:'nand'}), (t:Point {id:'tgt'}) "
            "CREATE (t)<-[:NAND {idx:1}]-(o)-[:NAND {idx:0}]->(a), "
            "       (t)<-[:NAND {idx:2}]-(o)-[:NAND {idx:3}]->(b)")

    tmpl = TEMPLATES[template]
    rows = g.query(tmpl["cypher"],
                   params={"entity": "ai strategy", "limit": 20}).result_set
    answer = tmpl["format"](rows)

    assert "absent related claim" in answer, (
        f"{template} dropped an absent claim instead of rendering it without "
        f"a number: {answer}")
    assert "0.50" not in answer, (
        f"{template} scored an absent belief 0.50 — the fabrication #11702 "
        f"removes: {answer}")
    if template != "counter_arguments":
        # The two IMPL templates rank, so the measured 0.1 claim must lead.
        assert "measured low claim" in answer, answer
        assert answer.index("measured low claim") < answer.index(
            "absent related claim"), (
            f"{template} ranked an absent belief above a measured 0.1 claim: "
            + answer)


def test_ep_evidence_prewrite_journals_the_mirror_clear(tmp_path):
    """The pre-write CLEARS `n.confidence`; the record must clear it too.

    The journal fold is presence-conditional — only the keys a record carries
    are written — so a `ConfidenceChanged` carrying just the two posterior keys
    cleared the posterior and LEFT the mirror the statement had just nulled.
    Live: the claim reads `absent`/None. Rebuilt from the journal:
    `baseline`/0.9 — a belief the live graph no longer has, resurrected by
    recovery. This is the recovery-equivalence property a journal exists for.
    """
    import json as _json

    from tortoise.sdk import TortoiseSDK

    db = str(tmp_path / "j.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    try:
        # A claim carrying a posterior AND the flush-mirror beside it.
        pid = sdk.create_point("statement", "mirrored claim", dedup=False,
                               status="live")["id"]
        g = sdk._get_proj().g
        g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 3.0, "
                "n.posterior_beta = 1.0, n.confidence = 0.75",
                params={"id": pid})
        # An EP run seeded with this claim as evidence takes the pre-write
        # branch that clears a non-baseline claim's posterior + mirror.
        sdk.compute_confidence(evidence={pid: (10, 1)},
                               require_calibration=False)

        recs = [r for r in ( _json.loads(line)
                             for line in (events / "events.jsonl").read_text().splitlines()
                             if line.strip())
                if r.get("type") == "ConfidenceChanged" and r.get("id") == pid]
        assert recs, "no ConfidenceChanged journaled for the cleared claim"
        # The record must carry EVERY key the statement cleared.
        assert any("confidence" in r for r in recs), (
            "the record omitted `confidence` — a rebuild replays only the keys "
            "a record carries, so the mirror the SET nulled survives recovery")
        assert all(r.get("confidence") is None
                   for r in recs if "confidence" in r), (
            "the record carries a stale mirror value instead of the clear")
    finally:
        sdk.close()


# ── entityProfile's floor may not read absence off the mirror alone ────────

def test_entity_profile_floor_reads_a_posterior_only_belief(sdk):
    """`navigation.entityProfile(confidenceMin=…)` read only the
    `n.confidence` MIRROR, so a claim whose belief is persisted in its
    posterior — the population the EP flush never reached, and the reason
    `_fetch_point_signals` carries its "a node the flush never reached"
    fallback — read `conf is None` and dropped out of EVERY floor. Before
    #11702 the fabricated 0.5 admitted it at any floor ≤ 0.5, so this is a
    recall regression, and it contradicts the §6.3 rule that a read surface
    uses the shared presence predicate instead of re-deriving absence.

    An ABSENT claim must still be excluded by a real floor, and kept by the
    no-op `0.0` default.
    """
    from tortoise.navigation import entityProfile

    proj = sdk._get_proj()
    root = sdk.create_point("statement", "the profiled root entity")["id"]
    measured_kid = sdk.create_point("statement", "a posterior-only kid")["id"]
    absent_kid = sdk.create_point("statement", "a kid with no belief")["id"]
    for kid in (measured_kid, absent_kid):
        proj.g.query(
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) "
            "CREATE (a)-[:IMPL {idx:1}]->(b)", params={"a": root, "b": kid})
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 9.0, "
        "n.posterior_beta = 1.0", params={"id": measured_kid})

    def ids(floor):
        out = entityProfile(proj.db, proj.graph_name, root, hops=1,
                            confidenceMin=floor)
        return {p["id"] for p in out["connected"]["points"]}

    assert measured_kid in ids(0.3), (
        "a claim MEASURED at 0.9 was dropped by a 0.3 floor — the filter read "
        "the absent mirror instead of the persisted belief")
    assert absent_kid not in ids(0.3), "an absent belief cleared a real floor"
    assert {measured_kid, absent_kid} <= ids(0.0), (
        "the no-op floor must keep an absent claim (0.0 is the #11702 default)")

    # ── the NON-Point families, which the mirror-only read admitted as well ──
    # The floor is VALUE-based on every family the profile lines up, not only
    # on Points. An Object / Event / Subject neighbour (or a Source with no
    # `documentKind`) carries no Point belief at all, and the pre-#11702
    # `node.get("confidence", 0.5)` admitted it at any floor <= 0.5 as if it
    # carried neutral evidence. §6.3 applies UNIFORMLY: an absent belief has no
    # number to clear a floor with, whatever label it sits on — and restoring
    # the 0.5 "because it is not a Point" is the same fabrication one family
    # over. The one thing that differs by label is WHERE a belief could live,
    # not whether the floor applies: a non-Point carrying a `confidence` mirror
    # is a value and clears it.
    suffix = uuid.uuid4().hex[:8]
    obj_id = f"zqfloor-object-{suffix}"
    evt_id = f"zqfloor-event-{suffix}"
    sub_id = f"zqfloor-subject-{suffix}"
    src_id = f"zqfloor-source-{suffix}"
    obj_measured = f"zqfloor-object-measured-{suffix}"
    for nid, label, extra in (
        (obj_id, "Object", {}),
        (evt_id, "Event", {}),
        (sub_id, "Subject", {}),
        (src_id, "Source", {}),
        (obj_measured, "Object", {"confidence": 0.8}),
    ):
        proj.g.query(
            f"CREATE (n:{label} {{id:$id, name:$name}}) "
            + "".join(f"SET n.{k} = ${k} " for k in extra),
            params={"id": nid, "name": nid, **extra})
        proj.g.query(
            "MATCH (a:Point {id:$a}), (n {id:$b}) "
            "CREATE (a)-[:aboutEntity]->(n)", params={"a": root, "b": nid})

    def connected(floor):
        out = entityProfile(proj.db, proj.graph_name, root, hops=1,
                            confidenceMin=floor)["connected"]
        return {n["id"] for bucket in out.values() for n in bucket}

    kept_real = connected(0.4)
    for nid, label in ((obj_id, "Object"), (evt_id, "Event"),
                       (sub_id, "Subject"), (src_id, "Source")):
        assert nid not in kept_real, (
            f"a belief-less {label} neighbour cleared a 0.4 floor — the filter "
            "fabricated the neutral 0.5 for a non-Point family")
        assert nid in connected(0.0), (
            f"the no-op floor must keep a belief-less {label} neighbour")
    assert obj_measured in kept_real, (
        "a non-Point node carrying a real `confidence` mirror was dropped by a "
        "0.4 floor — the floor must read the VALUE, not the label")


def test_belief_value_is_the_one_python_twin():
    """`belief_value` is the Python twin of the Cypher presence+precedence rule.

    Every surface that holds a plain property dict (navigation's floor,
    `sdk.approve`'s delta) calls THIS, so the "a half pair is no belief" rule
    cannot be re-decided differently per surface — which is exactly how
    `tortoise_analyze` came to answer `1/4` for a β-only row.
    """
    from tortoise.search_engine import belief_value

    assert belief_value(confidence=0.95) == pytest.approx(0.95)
    assert belief_value(posterior_alpha=2.0, posterior_beta=2.0) == pytest.approx(0.5)
    assert belief_value(ep_alpha=10.0, ep_beta=1.0) == pytest.approx(10 / 11)
    # mirror leads over the pair (the #2206 served value)
    assert belief_value(confidence=0.4, posterior_alpha=9.0,
                        posterior_beta=1.0) == pytest.approx(0.4)
    # every absent shape
    assert belief_value() is None
    assert belief_value(posterior_alpha=2.0) is None            # half pair
    assert belief_value(posterior_beta=3.0) is None             # half pair
    assert belief_value(posterior_alpha=0.0, posterior_beta=0.0) is None


def test_analyze_reports_no_belief_for_a_half_pair(sdk):
    """The `analyze` templates gate on the SHARED predicate, not a local sum.

    A private sum called a β-only row a belief (`coalesce(…, 1.0)` supplied the
    missing α) and reported `1/4`, which `_order_by_belief` then ordered on —
    the exact inversion §6.3 removes, on a surface whose own comment claimed
    the fix.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    g.query("CREATE (:Point {id:'half', content:'half pair support', "
            "is_operator:false, status:'live', posterior_beta:3.0})")
    g.query("CREATE (:Point {id:'low', content:'measured low support', "
            "is_operator:false, status:'live', posterior_alpha:1.0, "
            "posterior_beta:9.0})")
    g.query("CREATE (:Point {id:'tgt', content:'evidence ai strategy is working', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'op', content:'IMPL', op_type:'IMPL', "
            "is_operator:true, status:'live'})")
    g.query("MATCH (h:Point {id:'half'}), (b:Point {id:'low'}), "
            "(o:Point {id:'op'}), (t:Point {id:'tgt'}) "
            "CREATE (h)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:1}]->(t), "
            "       (b)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:2}]->(t)")
    answer = analyze("show me the evidence chain for the AI strategy",
                     proj=proj, use_llm=False)["answer"]
    # NOTE ON DISCRIMINATING POWER (cycle-8 review): this test also passes on
    # the PRE-#11702 tree, because that tree coalesced BOTH rows to 0.5 and the
    # incidental `ORDER BY conf DESC` tie order happened to match. It therefore
    # guards the INTERMEDIATE `ep_has_belief_cypher` shape (which admitted a
    # half pair) rather than being a before/after pin — `grading`-style honesty
    # beats a docstring that claims more than the assertion earns. The
    # before/after pin for the half pair is
    # `test_why_half_posterior_is_not_contested_and_has_no_variance`, which is
    # red pre-fix (a β-only row got variance 1/12 and `contested: true`).
    assert "half pair support" in answer and "measured low support" in answer, answer
    assert "0.25" not in answer, (
        "a β-only row was scored 0.25 — the half-pair fabrication: " + answer)
    assert (answer.index("measured low support")
            < answer.index("half pair support")), (
        "the absent half-pair row must sort after the measured 0.1 row: " + answer)


def test_set_point_baseline_journals_the_mirror_it_writes(sdk, tmp_path):
    """The baseline write now MOVES `n.confidence`; the record must say so.

    The fold writes only the keys a record carries, so a record with just the
    two posterior clears left the mirror stale on replay: live `baseline`/0.75,
    rebuilt `absent`/None. The payload is the value the statement committed.
    """
    import json as _json

    from tortoise.sdk import TortoiseSDK

    db = str(tmp_path / "b.db")
    events = tmp_path / "bevents"
    events.mkdir()
    j = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    try:
        pid = j.create_point("statement", "baseline journal claim",
                             dedup=False, status="live")["id"]
        j.set_point_baseline(pid, 3.0, 1.0)
        mirror = j._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.confidence",
            params={"id": pid}).result_set[0][0]
        assert mirror == pytest.approx(0.75, abs=1e-4), mirror
        recs = [r for r in (_json.loads(line)
                            for line in (events / "events.jsonl").read_text().splitlines()
                            if line.strip())
                if r.get("type") == "ConfidenceChanged" and r.get("id") == pid]
        assert recs, "set_point_baseline journaled no ConfidenceChanged"
        carried = [r["confidence"] for r in recs if "confidence" in r]
        assert carried, (
            "the record omitted `confidence` while the statement wrote it — a "
            "rebuild would leave the mirror stale (live baseline/0.75 -> rebuilt absent)")
        assert carried[-1] == pytest.approx(0.75, abs=1e-4), carried
    finally:
        j.close()


def test_degenerate_baseline_writes_no_mirror(sdk):
    """`set_point_baseline(0, 0)` must not manufacture a 0.5 belief.

    The new mirror write made the row PRESENT by the shared predicate (the
    mirror test wins over the zero-total pair), so one SDK call produced the
    exact `0.5` + belief state §6.3 forbids — and journaled it, so a rebuild
    reproduced it. The degenerate write now leaves the row ABSENT, which is
    what every read surface reports.
    """
    pid = sdk.create_point("statement", "degenerate baseline claim",
                           dedup=False, status="live")["id"]
    sdk.set_point_baseline(pid, 0.0, 0.0)
    stored = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence", params={"id": pid}
    ).result_set[0][0]
    assert stored is None, f"a degenerate baseline wrote a mirror: {stored!r}"
    eps = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    assert eps.ep_state == EP_STATE_ABSENT, eps
    assert eps.confidence_mean is None, eps


def test_update_point_clearing_the_mirror_leaves_one_answer(sdk):
    """Review cycle 12 (P2): `update_point` is the THIRD belief writer.

    `reject_belief_props` refuses `credibility` and the baseline quartet only,
    so `posterior_alpha` / `posterior_beta` ride through this door and stored a
    NEW PAIR beside the OLD flush mirror — the state three docstrings said the
    other two writers had made impossible. Post-#11702 the mirror leads on ~12
    surfaces, so the stale value is indistinguishable from a correct one. The
    door now clears the mirror with the pair (the same treatment
    `set_point_baseline` got), and every surface falls through to the pair mean.

    Discriminating power: pre-fix every assertion below sees 0.9 (the stale
    mirror) where the pair says 0.5.
    """
    pid = sdk.create_point("statement", "mirror vs pair claim",
                           dedup=False, status="live")["id"]
    proj = sdk._get_proj()
    # A caller-set mirror beside no pair — a real public shape.
    sdk.update_point(pid, confidence=0.9)
    assert annotate_ep_batch(proj.g, [pid])[pid].confidence_mean == pytest.approx(0.9)
    # Moving the pair must not leave it behind.
    sdk.update_point(pid, posterior_alpha=10.0, posterior_beta=10.0)
    stored = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence",
        params={"id": pid}).result_set[0][0]
    assert stored is None, f"the stale mirror survived the pair write: {stored!r}"

    eps = annotate_ep_batch(proj.g, [pid])[pid]
    assert eps.confidence_mean == pytest.approx(0.5), (
        "the ep block still reports the stale mirror: " + repr(eps))
    assert eps.ep_state == "measured", eps
    sig = StateRanker(projection=proj)._fetch_point_signals([pid])[pid]
    assert sig["confidence"] == pytest.approx(0.5), sig
    node = next(n for n in SubgraphExpander(proj).expand([pid], depth=1)["nodes"]
                if n["id"] == pid)
    assert node["confidence"] == pytest.approx(0.5), node
    assert node["ep_state"] == "measured", node

    # A HALF pair is the same rule, and it used to inherit the mirror's number
    # for a claim that has no mean at all — so on a FRESH claim (no stored β)
    # the cleared mirror is what turns 0.7 into an honest `absent`.
    half = sdk.create_point("statement", "half pair after a mirror",
                            dedup=False, status="live")["id"]
    sdk.update_point(half, confidence=0.7)
    sdk.update_point(half, posterior_alpha=3.0)
    eps = annotate_ep_batch(proj.g, [half])[half]
    assert eps.ep_state == EP_STATE_ABSENT, eps
    assert eps.confidence_mean is None, eps


def test_the_mirror_wins_over_a_contradicting_pair(sdk):
    """The CONTRACT for the reachable mirror≠pair shape, pinned as a contract.

    `test_ranking_w4b`'s fixture used to call mirror≠posterior "not a state any
    write path produces". That was false — the public writes reach it in this
    order (`update_point(posterior…)` clears the mirror, then a caller-owned
    `update_point(confidence=…)` sets it), and `create_point`-side writers that
    flush the mirror can be followed by a pair write from an operator run.

    So the precedence is pinned here rather than assumed: the `n.confidence`
    MIRROR is the SERVED belief (#2206) and it WINS over a contradicting pair
    on every surface — one number, not two — while the STATE still comes from
    the pair (a mirrored claim with a pair is `measured`, not `baseline`).

    Discriminating power: a reader that preferred α/β answers 0.5 here.
    """
    pid = sdk.create_point("statement", "contradicting pair claim",
                           dedup=False, status="live")["id"]
    proj = sdk._get_proj()
    sdk.update_point(pid, posterior_alpha=1.0, posterior_beta=1.0)  # pair 0.5
    sdk.update_point(pid, confidence=0.9)  # caller-owned mirror, wins
    stored = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence, n.posterior_alpha, "
        "n.posterior_beta", params={"id": pid}).result_set[0]
    assert stored[0] == pytest.approx(0.9), stored
    assert stored[1] == pytest.approx(1.0) and stored[2] == pytest.approx(1.0), stored

    eps = annotate_ep_batch(proj.g, [pid])[pid]
    assert eps.confidence_mean == pytest.approx(0.9), eps
    assert eps.ep_state == "measured", eps
    sig = StateRanker(projection=proj)._fetch_point_signals([pid])[pid]
    assert sig["confidence"] == pytest.approx(0.9), sig
    assert GraphRanker(projection=proj)._fetch_point_signals([pid])[pid][
        "confidence"] == pytest.approx(0.9)


def test_a_draft_with_a_persisted_prior_names_its_belief_beside_a_null_value(sdk):
    """Review cycle 12 (P3): the manifest said `null` means "absent" and
    NOTHING ELSE, and a draft row breaks that — which is why the row now states
    the exception instead of the consumer inferring a state from the number.

    The #2598 staging gate forces `confidence: null` on a draft the belief
    predicate does not call measured (`uncomputed = draft and not has_ep`),
    while `ep_state` still names the PERSISTED belief. A draft carrying a #2199
    declared baseline is therefore `confidence: null` + `ep_state: "baseline"`
    + `confidence_source: "uncomputed"` — null is NOT "absent" there, and
    `ep_state` is the key a consumer must read.

    Discriminating power: this is the ONLY shape that reds a "confidence is
    null iff ep_state is absent" assertion, so it is what keeps the qualifier
    from being reverted as noise.
    """
    proj = sdk._get_proj()
    measured = sdk.create_point("statement", "a live measured claim",
                                dedup=False, status="live")["id"]
    draft = sdk.create_point("statement", "a draft carrying a prior",
                             dedup=False, status="draft")["id"]
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha = 5.0, n.ep_beta = 1.0, "
        "n.baseline_set = true", params={"id": draft})
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 9.0, "
        "n.posterior_beta = 1.0", params={"id": measured})

    rows = {r["id"]: r for r in StateRanker(projection=proj).rerank(
        [{"id": measured, "scores": {"rrf": 1.0}},
         {"id": draft, "scores": {"rrf": 1.0}}])}
    gaps = {r["id"]: r for r in GapsRanker(projection=proj).rerank(
        [{"id": draft, "scores": {"rrf": 1.0}}])}
    eps = annotate_ep_batch(proj.g, [draft, measured])

    rec = rows[draft]["recall_ranking"]
    gap = gaps[draft]["gaps_ranking"]
    # The persisted belief is NAMED …
    assert rec["ep_state"] == EP_STATE_BASELINE, rec
    assert gap["ep_state"] == EP_STATE_BASELINE, gap
    assert eps[draft].ep_state == EP_STATE_BASELINE, eps[draft]
    # … beside a null the staging gate forces, and the source says why.
    assert rec["confidence"] is None, rec
    assert gap["confidence"] is None, gap
    assert rec["confidence_source"] == "uncomputed", rec
    assert gap["confidence_source"] == "uncomputed", gap
    # The `ep` block has no staging gate, so the same claim reports its prior
    # mean there — that asymmetry is #2598's, and it is WHY the manifest row
    # qualifies the "null means absent" clause rather than restating it.
    assert eps[draft].confidence_mean == pytest.approx(0.8333, abs=1e-4), (
        eps[draft])
    # A live measured claim is the control: null really does mean absent only
    # when nothing is persisted.
    assert rows[measured]["recall_ranking"]["confidence"] == pytest.approx(0.9)
    assert rows[measured]["recall_ranking"]["ep_state"] == "measured"


def test_why_support_chain_does_not_weigh_an_absent_supporter(sdk):
    """The support chain is ordered and capped by `weight`.

    `_mean(coalesce(…, 1.0), coalesce(…, 1.0))` weighed an ABSENT supporter
    0.5 (and a β-only one 0.25), so the ≤3 cap was filled by fabricated numbers
    and a genuinely measured 0.4 supporter could be crowded out entirely.
    """
    from tortoise.why import _assemble_supports

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "the supported target")["id"]
    absent = [sdk.create_point("statement", f"absent supporter {i}")["id"]
              for i in range(3)]
    measured = sdk.create_point("statement", "measured 0.4 supporter")["id"]
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
        "n.posterior_beta = 3.0", params={"id": measured})
    for sup in (*absent, measured):
        proj.g.query(
            "MATCH (s:Point {id:$s}), (t:Point {id:$t}) "
            "CREATE (s)-[:IMPL {idx:1}]->(t)", params={"s": sup, "t": target})

    blocks = {target: {"support_chain": []}}
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) WITH n "
        "MATCH (sup:Point)-[r:IMPL]->(n) "
        "RETURN n.id, sup.id, sup.content, sup.confidence, "
        "       sup.posterior_alpha, sup.posterior_beta, sup.ep_alpha, sup.ep_beta",
        params={"id": target}).result_set
    _assemble_supports(rows, blocks)
    chain = blocks[target]["support_chain"]
    assert chain, "no support chain assembled"
    assert chain[0]["point_id"] == measured, (
        f"the measured 0.4 supporter must lead the chain, got {chain}")
    assert chain[0]["weight"] == pytest.approx(0.4, abs=1e-4), chain[0]
    for entry in chain[1:]:
        assert entry["weight"] is None, (
            f"an absent supporter was weighed {entry['weight']!r}: {entry}")


def test_most_uncertain_does_not_rank_a_mirror_only_claim_first(sdk):
    """Variance is computed from α/β, so its population must HAVE α/β.

    Gating that population on the presence predicate admitted a mirror-only
    claim, whose `coalesce(…, 1.0)` defaults produced the MAXIMUM variance
    1/12 — first place on a "how uncertain are we" answer, for a claim with no
    stored operands at all.

    NOTE ON DISCRIMINATING POWER (cycle-8 review): this assertion also holds on
    the PRE-#11702 tree, whose gate was already `posterior_alpha IS NOT NULL OR
    ep_alpha IS NOT NULL` — it pins the INTERMEDIATE `ep_has_belief_cypher`
    shape (which would admit a mirror-only row), not the original defect. Kept
    because that intermediate shape is one edit away from coming back; the
    original-defect pin is `test_analyze_reports_no_belief_for_a_half_pair`.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    g.query("CREATE (:Point {id:'mirror', content:'ZEBRA mirror-only claim', "
            "is_operator:false, status:'live', confidence:0.42})")
    g.query("CREATE (:Point {id:'meas', content:'ZEBRA measured claim', "
            "is_operator:false, status:'live', posterior_alpha:9.0, "
            "posterior_beta:1.0})")
    answer = analyze("what are we most uncertain about?", proj=proj,
                     use_llm=False)["answer"]
    assert "ZEBRA measured claim" in answer, answer
    assert "ZEBRA mirror-only claim" not in answer, (
        "a mirror-only claim has no stored α/β, so it has no variance to rank "
        "— and the coalesce(…, 1.0) defaults gave it the maximum: " + answer)


def test_ranker_belief_value_coerces_an_integer_alpha_beta_pair(sdk):
    """One claim must not answer TWO numbers: 0.90 on `ep`, 0.00 on the rankers.

    FalkorDB's `/` is INTEGER division on int operands (`RETURN 9/10` → `0`,
    measured on falkordb/falkordb-server:v4.20.6), so a pair stored as ints —
    `update_point(posterior_alpha=9, posterior_beta=1)`,
    `set_point_baseline(2, 1)`, the legacy `tier_map` — truncated
    `9/10 → 0` in the Cypher mean `coalesce(…, 1.0) / (… + …)` of
    `GraphRanker._fetch_point_signals` and `SubgraphExpander._enrich`, while
    `annotate_ep_batch` and `search_engine.belief_value` (both `toFloat`- or
    Python-guarded) answered `0.90` for the SAME row. A measured 0.90 claim
    reported as 0.00 is a fabricated value, not an absent one.

    Discriminating power: pre-fix the two ranker assertions see `0.0`.
    """
    from tests._embedded import _wipe_or as wipe

    proj = sdk._get_proj()
    wipe(proj)
    # Ints on purpose — the storage layer keeps them as ints.
    proj.g.query("CREATE (:Point {id:'intpair', content:'integer pair claim', "
                 "is_operator:false, status:'live', posterior_alpha:9, "
                 "posterior_beta:1})")

    # The reference answer, from the read that is already guarded.
    ep = annotate_ep_batch(proj.g, ["intpair"])["intpair"]
    assert ep.confidence_mean == pytest.approx(0.9), ep.confidence_mean

    sig = GraphRanker(projection=proj)._fetch_signals(["intpair"], "point")["intpair"]
    assert sig["confidence"] == pytest.approx(0.9), (
        "graph_rankings truncated an integer 9/1 posterior to "
        f"{sig['confidence']!r} while the ep block answered {ep.confidence_mean}")

    node = next(n for n in SubgraphExpander(proj).expand(["intpair"], depth=1)["nodes"]
                if n["id"] == "intpair")
    assert node["confidence"] == pytest.approx(0.9), (
        "the subgraph display mean truncated an integer 9/1 posterior to "
        f"{node['confidence']!r}")


def test_trends_prints_only_stored_alpha_beta(sdk):
    """`trends` prints α/β, so those numbers must be STORED, not defaults.

    `coalesce(…, 1.0)` printed `β=1.0` for a half pair that stores no β at all,
    and admitted rows with no complete pair.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    # `$entity` is matched with CONTAINS, so both contents carry the exact
    # phrase `classify` extracts ('zebra strategy over time?').
    g.query("CREATE (:Point {id:'half', content:'zebra strategy over time? "
            "alpha-only claim', is_operator:false, status:'live', "
            "posterior_alpha:3.0})")
    g.query("CREATE (:Point {id:'meas', content:'zebra strategy over time? "
            "measured claim', is_operator:false, status:'live', "
            "posterior_alpha:9.0, posterior_beta:1.0})")
    answer = analyze("how has the ZEBRA strategy changed over time?",
                     proj=proj, use_llm=False)["answer"]
    assert "measured claim" in answer, answer
    assert "α=3.0" not in answer, (
        "a half pair was printed with a fabricated β from the coalesce default: "
        + answer)


def test_analyze_coerces_integer_alpha_beta(sdk):
    """FalkorDB `/` truncates INTEGERS — the belief expression must coerce.

    `update_point(posterior_alpha=9, posterior_beta=1)` is accepted and stores
    INTs with no mirror (only the `ep_*` baseline fields are gated). Without
    `toFloat`, `9/10` truncated to `0`, so a claim measured at 0.90 rendered
    `confidence: 0.00`, ranked BELOW a 0.10 claim, and failed its own belief
    floor — the exact inversion §6.3 removes, on a claim that HAS a belief.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    # Ints on purpose — the storage layer keeps them as ints.
    g.query("CREATE (:Point {id:'strong', content:'evidence ai strategy is "
            "working via strong', is_operator:false, status:'live', "
            "posterior_alpha:9, posterior_beta:1})")
    g.query("CREATE (:Point {id:'weak', content:'weak support claim', "
            "is_operator:false, status:'live', posterior_alpha:1, "
            "posterior_beta:9})")
    g.query("CREATE (:Point {id:'tgt', content:'evidence ai strategy is working', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'op', content:'IMPL', op_type:'IMPL', "
            "is_operator:true, status:'live'})")
    g.query("MATCH (a:Point {id:'strong'}), (b:Point {id:'weak'}), "
            "(o:Point {id:'op'}), (t:Point {id:'tgt'}) "
            "CREATE (a)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:1}]->(t), "
            "       (b)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:2}]->(t)")
    answer = analyze("show me the evidence chain for the AI strategy",
                     proj=proj, use_llm=False)["answer"]
    assert "0.90" in answer, (
        "integer α/β truncated to 0 — the toFloat guard is missing: " + answer)
    assert "0.00" not in answer, answer
    assert answer.index("strong") < answer.index("weak"), (
        "the 0.90 claim must outrank the 0.10 one: " + answer)


def test_why_conflict_severity_and_tradeoff_weight_use_the_shared_reader(sdk):
    """`severity`/`ep_weight` ordered on `_mean(coalesce(…, 1.0), …)` = 0.5.

    An ABSENT counter-claim therefore took a real severity tier (competing for
    the ≤2 cap), and an absent alternative was presented as STRONGER than a
    measured 0.4 one. Both now weigh `None` and sort in their own last tier.
    """
    from tortoise.why import _assemble_conflicts, _assemble_tradeoffs

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "the contested claim")["id"]
    absent = sdk.create_point("statement", "absent counter-claim")["id"]
    measured = sdk.create_point("statement", "measured 0.55 counter-claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 11.0, "
                 "n.posterior_beta = 9.0", params={"id": measured})
    for nander in (absent, measured):
        proj.g.query(
            "MATCH (c:Point {id:$c}), (t:Point {id:$t}) CREATE (c)-[:NAND]->(t)",
            params={"c": nander, "t": target})

    blocks = {target: {}}
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) WITH n MATCH (c:Point)-[r:NAND]->(n) "
        "RETURN n.id, c.id, c.content, "
        "  c.confidence, c.posterior_alpha, c.posterior_beta, "
        "  c.ep_alpha, c.ep_beta, "
        "  null AS c1, null AS c2, null AS c3, null AS c4, null AS c5",
        params={"id": target}).result_set
    _assemble_conflicts(rows, blocks)
    nands = blocks[target]["nands"]
    by_id = {n["point_id"]: n for n in nands}
    assert by_id[measured]["severity"] == "medium", by_id[measured]
    assert by_id[absent]["severity"] is None, (
        "an absent counter-claim took a real severity tier on a fabricated 0.5: "
        + repr(by_id[absent]))

    # Tradeoffs: absent must sort BELOW a measured 0.4 alternative.
    decision = sdk.create_point("statement", "the decision",
                                status="live")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.pointKind = 'decision'",
                 params={"id": decision})
    absent_alt = sdk.create_point("statement", "absent alternative")["id"]
    measured_alt = sdk.create_point("statement", "measured 0.4 alternative")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
                 "n.posterior_beta = 3.0", params={"id": measured_alt})
    for alt in (absent_alt, measured_alt):
        proj.g.query(
            "MATCH (d:Point {id:$d}), (a:Point {id:$a}) CREATE (d)-[:IMPL]->(a)",
            params={"d": decision, "a": alt})
    tblocks = {decision: {}}
    trows = proj.g.query(
        "MATCH (n:Point {id:$id}) WITH n MATCH (n)-[:IMPL]->(alt:Point) "
        "RETURN n.id, coalesce(n.pointKind,''), alt.id, alt.content, "
        "  coalesce(alt.pointKind,''), null AS o1, null AS o2, "
        "  alt.confidence, alt.posterior_alpha, alt.posterior_beta, "
        "  alt.ep_alpha, alt.ep_beta",
        params={"id": decision}).result_set
    _assemble_tradeoffs(trows, tblocks)
    alts = {t["point_id"]: t for t in tblocks[decision].get("tradeoffs", [])}
    assert alts, ("no tradeoffs assembled — the fixture did not build a "
                  "decision with two alternatives")
    assert alts[measured_alt]["ep_weight"] == pytest.approx(0.4, abs=1e-4), alts
    assert alts[absent_alt]["ep_weight"] is None, (
        "an absent alternative was weighed 0.5 and presented as stronger than "
        "a measured 0.4: " + repr(alts[absent_alt]))
    order = [t["point_id"] for t in tblocks[decision]["tradeoffs"]]
    assert order.index(measured_alt) < order.index(absent_alt), order


def test_why_half_posterior_is_not_contested_and_has_no_variance(sdk):
    """A HALF pair (β stored, α absent) got `variance = 1/12` — the MAXIMUM.

    `_assemble_ep_rows` computed the variance from independently-coalesced
    operands, so a claim whose stored posterior is `(null, 3.0)` produced a
    full-entropy Beta(1,3)… and `contested: true` beside `ep_state: "absent"`,
    firing the volunteer's "read the counterargument" for a claim that has no
    counterargument. Presence is a property of the PAIR, so a half pair has no
    variance to report and cannot be contested.
    """
    from tortoise.search_engine import EP_STATE_ABSENT
    from tortoise.why import _assemble_ep_rows

    proj = sdk._get_proj()
    half = sdk.create_point("statement", "half pair claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_beta = 3.0",
                 params={"id": half})
    blocks = {half: {}}
    # The REAL `_EP_CYPHER` shape (13 columns) — the coalesced α/β inputs, then
    # the raw columns the presence predicate reads. Built here so the fixture
    # exercises the presence path and not the legacy 7-col double branch.
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.id, "
        "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "  coalesce(n.posterior_beta, n.ep_beta, 1.0), "
        "  n.ep_alpha IS NOT NULL AS measured, '' AS status, "
        "  false AS outdated, false AS baseline_set, true AS has_belief, "
        "  n.posterior_alpha AS pa, n.ep_alpha AS ea, n.confidence AS conf, "
        "  n.posterior_beta AS pb, n.ep_beta AS eb",
        params={"id": half}).result_set
    _assemble_ep_rows(rows, blocks)
    block = blocks[half]["ep"]
    assert block.get("ep_state") == EP_STATE_ABSENT, block
    # Pin the EXACT value: a disjunction accepts either rendering of "absent"
    # and so cannot fail if the width comes back as any zero-ish number.
    assert block.get("variance") == 0.0, (
        "a half pair (β only) was given a variance — the coalesce default "
        "invented an α it does not store: " + repr(block))
    assert block.get("contested") is not True, (
        "a claim with no belief at all was marked contested: " + repr(block))


def test_ranking_conflict_boost_ignores_a_counter_claim_with_no_belief(sdk):
    """`coalesce(…, 1.0)` gave an ABSENT counter-claim a 0.5 severity mean.

    The W4 boost fired on a claim whose belief does not exist, and — worse —
    the fabricated 0.5 landed in the `medium` tier positioned ABOVE a genuinely
    measured 0.4 counter-claim, so the unmeasured conflict won the ≤2 budget.
    """
    from tortoise.ranking import resolve_contested_relevance

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "the contested claim")["id"]
    absent = sdk.create_point("statement", "absent zebra counter-claim")["id"]
    measured = sdk.create_point("statement", "measured zebra counter-claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
                 "n.posterior_beta = 3.0", params={"id": measured})
    for nander in (absent, measured):
        proj.g.query(
            "MATCH (c:Point {id:$c}), (t:Point {id:$t}) CREATE (c)-[:NAND]->(t)",
            params={"c": nander, "t": target})

    # The real read (no fixture double). The boost itself legitimately fires on
    # a NAND edge — the edge IS the evidence (the same rule the why-layer uses
    # when it surfaces an ungraded conflict). What must NOT happen is an
    # ungraded claim winning the <=2 relevance BUDGET against a measured one:
    # pre-fix the coalesce default weighed it 0.5 — ABOVE the measured 0.4 —
    # so the measured counter-claim was the one squeezed out.
    crowded = sdk.create_point("statement", "contested by three")["id"]
    filler_a = sdk.create_point("statement", "zebra filler a")["id"]
    filler_b = sdk.create_point("statement", "zebra filler b")["id"]
    real = sdk.create_point("statement", "zebra measured 0.4")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
                 "n.posterior_beta = 3.0", params={"id": real})
    for nander in (filler_a, filler_b, real):
        proj.g.query(
            "MATCH (c:Point {id:$c}), (t:Point {id:$t}) CREATE (c)-[:NAND]->(t)",
            params={"c": nander, "t": crowded})
    got = resolve_contested_relevance(proj, [crowded], "zebra measured")
    assert crowded in got, ("the budget dropped a genuinely measured "
                            "counter-claim: " + repr(got))

    # And the ordering itself, read through the resolver's own key: every
    # measured candidate precedes every absent one.
    from tortoise.ranking import _contested_claim_key
    measured_key = _contested_claim_key({"mean": 0.4, "relevance": 0.0,
                                         "claim_id": "a", "nid": 1})
    absent_key = _contested_claim_key({"mean": None, "relevance": 0.0,
                                       "claim_id": "a", "nid": 1})
    assert measured_key < absent_key, (measured_key, absent_key)


def test_conflict_dedupe_ranks_a_nullable_mean_without_raising(sdk):
    """Two rows for ONE counter-claim, one answering a float and one `None`.

    The dedupe compared `(mean, content)` tuples directly, so the moment #11702
    made `mean` nullable a float/None pair raised `TypeError` inside the read's
    outer handler — which returns `{}` for the WHOLE read, dropping the boost
    for every point in it, not only the offending one. Pre-#11702 the comparison
    was unreachable (the mean was always a float), so this is a regression the
    change itself introduced.

    The shape that produces it: claim `X` has NO belief and NANDs the target
    directly, while ALSO being the idx-0 source of a NAND operator `O` that
    carries a belief and NANDs the same target. Both rows share
    `coalesce(id(src), id(c)) == id(X)`; one resolves a float from `O`, the
    other `None` from `X`.
    """
    from tortoise.ranking import _claim_rank, resolve_contested_relevance

    # The pure key is total over the pair that used to raise.
    assert _claim_rank({"mean": 0.75, "content": "x"})[0] is True
    assert _claim_rank({"mean": None, "content": "x"})[0] is False
    assert (_claim_rank({"mean": 0.75, "content": "x"})
            > _claim_rank({"mean": None, "content": "x"})), (
        "a measured claim must rank above an absent one in the dedupe")

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "dedupe target")["id"]
    bare = sdk.create_point("statement", "zebra bare counter-claim")["id"]
    # The NAND operator: is_operator + op_type, carrying the belief.
    op = sdk.create_point("statement", "zebra operator counter")["id"]
    proj.g.query(
        "MATCH (o:Point {id:$o}) SET o.is_operator = true, "
        "o.op_type = 'NAND', o.posterior_alpha = 3.0, o.posterior_beta = 1.0",
        params={"o": op})
    proj.g.query(
        "MATCH (b:Point {id:$b}), (t:Point {id:$t}) CREATE (b)-[:NAND]->(t)",
        params={"b": bare, "t": target})
    # `r.idx > 0` is part of the read's WHERE: the operator's own NAND must be
    # indexed, or the row is NULL-filtered and the two paths never collide.
    proj.g.query(
        "MATCH (o:Point {id:$o}), (t:Point {id:$t}) "
        "CREATE (o)-[:NAND {idx:1}]->(t)",
        params={"o": op, "t": target})
    proj.g.query(
        "MATCH (o:Point {id:$o}), (b:Point {id:$b}) "
        "CREATE (b)-[:INPUT {idx:0}]->(o)", params={"o": op, "b": bare})

    got = resolve_contested_relevance(proj, [target], "zebra counter-claim")
    assert target in got, (
        "the read returned nothing for a contested claim — a TypeError in the "
        "null/float dedupe is swallowed by the outer handler and drops EVERY "
        "point's boost: " + repr(got))


#: The capture conversation the write-verb `ep_updated` pins drive. A single
#: plain user turn: the deterministic v2 mock (TORTOISE_SESSION_LLM_MOCK=1) then
#: mints ONE operator-less claim — a claim the ingest EP pass can never
#: calibrate, so `ep_updated` reflects the PRESENCE read and nothing else. Its
#: id is content-derived (`pt_<hash>`), so a re-capture resolves to the SAME
#: node — which is what lets a test move a belief onto it between captures.
_EP_FLAG_CONV = [{"role": "user",
                  "content": "W5 write-verb presence contract test."}]


def _clear_belief(proj, pid: str, *, mirror: float | None = None,
                  beta: float | None = None) -> None:
    """One belief shape per call: MIRROR-only, or a β-only HALF pair."""
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence = $c, "
        "n.posterior_alpha = null, n.ep_alpha = null, "
        "n.posterior_beta = $b, n.ep_beta = null",
        params={"id": pid, "c": mirror, "b": beta})


def _mirror_only_ep_flag_conversation(sdk, monkeypatch, session: str):
    """Drive the SDK write verb twice and return the id it reports on."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    first = sdk._capture_session(_EP_FLAG_CONV, session_id=session)
    assert first["points"], first
    # Honest baseline: with no persisted belief nothing has been EP-updated.
    assert all(p["ep_updated"] is False for p in first["points"]), \
        first["points"]
    pid = first["points"][0]["id"]
    proj = sdk._get_proj()
    _clear_belief(proj, pid, mirror=0.95)
    # A SECOND session id on purpose: a repeat under the same id is refused
    # as "session already captured". Cross-session re-ingest is the seam that
    # dedups by CONTENT onto the same node (the `pt_<hash>` id is stable).
    second = sdk._capture_session(_EP_FLAG_CONV, session_id=session + "-b")
    return proj, pid, second


def test_write_verb_ep_updated_pins_the_mirror_only_claim(sdk, monkeypatch):
    """`ep_updated` on the SDK write verb used a local presence spelling.

    Cycle 20 replaced `α IS NOT NULL OR ep_α IS NOT NULL` with the shared
    `ep_state`, because that spelling called a MIRROR-only claim — a genuinely
    served belief, e.g. 0.95 — "not ep_updated" while every read surface called
    it measured. This test previously asserted the Cypher predicate on two
    seeded rows instead of reading `ep_updated` off a write verb, so the wire
    flag could regress to any local spelling and the suite stayed green
    (mutation-verified by the reviewing lane: reverting `tortoise/sdk.py`'s
    producer to `origin/main` left every capture suite passing).

    Here the flag is read off the REAL producer: a capture whose claim carries
    NO belief reports False, and the same claim (same content-derived id, via a
    re-capture that dedups onto it) carrying only the `n.confidence` MIRROR
    reports True. Under the old spelling the second answer is False.
    """
    proj, pid, second = _mirror_only_ep_flag_conversation(
        sdk, monkeypatch, "zq-ep-flag-sdk")
    got = {p["id"]: p["ep_updated"] for p in second["points"]}
    assert pid in got, (
        "the re-capture did not resolve onto the same claim, so this test "
        "could not fail: " + repr(got))
    assert got[pid] is True, (
        "a mirror-only claim is a persisted, served belief — the write verb "
        "must report ep_updated: true for it, not a local α-column spelling: "
        + repr(got))
    # …and the reverse direction, so the pin is not just `always True`.
    _clear_belief(proj, pid, beta=3.0)
    third = sdk._capture_session(_EP_FLAG_CONV,
                                 session_id="zq-ep-flag-sdk-c")
    got = {p["id"]: p["ep_updated"] for p in third["points"]}
    assert got[pid] is False, (
        "a β-only HALF pair is not a belief (α + β needs both operands) — the "
        "write verb must report ep_updated: false: " + repr(got))


def test_write_verb_ep_updated_pins_the_hosted_mirror_only_claim(
        client, monkeypatch):
    """The HOSTED producer (`POST /v1/sessions`) shares that presence read.

    Same shape as the SDK half above, on the other write verb: the mirrored
    claim reports `ep_updated: true`, and a β-only half pair reports false —
    read from the served response, not from a hand-composed predicate.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    payload = {"conversation": _EP_FLAG_CONV, "session_id": "zq-ep-flag-http",
               "harness": "pi"}
    # The re-captures below need a FRESH session id each time (a repeat under
    # one id is refused as "session already captured"); the claim id is
    # content-derived, so they still resolve onto the SAME node.
    r = client.post("/v1/sessions", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["points"], body
    assert all(p["ep_updated"] is False for p in body["points"]), body["points"]
    pid = body["points"][0]["id"]

    import tortoise.hosted_api as ha_mod
    # The mirror belief lives in the graph the HOSTED write verb opens, which for
    # an org with no promoted graph is keyed by the AUTHENTICATED team id
    # (`_data_sdk` -> `_make_sdk(namespace=org_id)`) — so this
    # handle must be built from the fixture's own `TEST_ORG_ID`, imported
    # beside the `client` fixture above, not from a second hardcoded copy of
    # the id. (That default is a POINTER that a promotion can move, #10180 —
    # true of this fixture's unpromoted org, and a move would red the
    # `ep_updated: True` arm rather than let it pass.) The form matters twice:
    # `tests/test_markers.py`'s `test_namespace_literals_guard_passing_or_routed`
    # reds on an un-routed non-test literal (#11905) and this scan cannot see a
    # variable, so the construction needs no `ROUTED_NAMESPACES` declaration —
    # do not re-add a `"team-001"` row for it, that is the rejected alternative
    # and it becomes a whitelist once the literal is gone. And a `test_*`
    # rename would open a DIFFERENT graph than the write verb writes, where
    # `_clear_belief`'s `MATCH (n:Point {id:$id})` finds no node — it clears
    # nothing, so the `ep_updated: True` arm below would fail loudly (the
    # β-only arm after it can never be reached, so it is non-discriminating
    # rather than merely vacuous).
    proj = ha_mod._make_sdk(namespace=TEST_ORG_ID)._get_proj()
    _clear_belief(proj, pid, mirror=0.95)
    payload = dict(payload, session_id="zq-ep-flag-http-b")
    r2 = client.post("/v1/sessions", json=payload)
    assert r2.status_code == 200, r2.text
    got = {p["id"]: p["ep_updated"] for p in r2.json()["points"]}
    assert pid in got, (
        "the re-capture did not resolve onto the same claim, so this test "
        "could not fail: " + repr(got))
    assert got[pid] is True, (
        "the hosted write verb must report ep_updated: true for a mirror-only "
        "claim: " + repr(got))
    _clear_belief(proj, pid, beta=3.0)
    payload = dict(payload, session_id="zq-ep-flag-http-c")
    r3 = client.post("/v1/sessions", json=payload)
    assert r3.status_code == 200, r3.text
    got = {p["id"]: p["ep_updated"] for p in r3.json()["points"]}
    assert got[pid] is False, (
        "a β-only HALF pair is not a belief: " + repr(got))


def test_no_surface_fabricates_a_variance_for_an_absent_belief(sdk):
    """`ep_pair_stored`: a WIDTH also needs both operands (#11702 cycle 9).

    Cycle 7 gave `why` the half-pair guard, but `annotate_ep_batch` and all
    three rankers kept computing `_beta_variance` from the `coalesce(…, 1.0)`
    defaults, so a claim with NO belief still reported the Beta(1,1) MAXIMUM
    1/12 — "there is no belief here, and it is maximally uncertain" — and an
    α-only half pair reached `contested: True` and emitted the recall warning
    `['contested']` for a claim nothing measured. Measured before the fix:

        annotate_ep_batch : mean=None state='absent' variance=0.0833
        recall_ranking    : conf=None state='absent' variance=0.0833
        (α-only) contested: True on annotate + all three rankers
        why               : variance=0.0 contested=False   <- the only guarded one

    One rule now, imported by all five surfaces.
    """
    from tortoise.ranking import GapsRanker, GraphRanker, StateRanker
    from tortoise.search_engine import EP_STATE_ABSENT, annotate_ep_batch, ep_pair_stored
    from tortoise.why import _assemble_ep_rows

    # The rule itself, in the four shapes that matter.
    assert ep_pair_stored(posterior_alpha=2.0, posterior_beta=3.0) is True
    assert ep_pair_stored(posterior_alpha=2.0) is False            # half
    assert ep_pair_stored(posterior_beta=3.0) is False             # half
    assert ep_pair_stored(ep_alpha=2.0, ep_beta=3.0) is True
    assert ep_pair_stored() is False

    proj = sdk._get_proj()
    absent = sdk.create_point("statement", "no belief at all")["id"]
    alpha_only = sdk.create_point("statement", "alpha-only half pair")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 0.5",
                 params={"id": alpha_only})

    # annotate_ep_batch: the EpBreakdown every SDK consumer reads.
    bds = annotate_ep_batch(proj.g, [absent, alpha_only])
    for pid, label in ((absent, "absent"), (alpha_only, "alpha-only")):
        bd = bds[pid]
        assert bd.ep_state == EP_STATE_ABSENT, (label, bd)
        assert bd.variance == 0.0, (
            f"{label}: a claim with no belief reported a posterior WIDTH "
            f"{bd.variance} — the coalesced 1.0 default fabricated 1/12: {bd}")
        assert bd.contested is False, (
            f"{label}: an absent belief read contested: {bd}")

    # All three rankers.
    ids = [absent, alpha_only]
    for name, signals in (
            ("state", StateRanker(projection=proj)._fetch_point_signals(ids)),
            ("graph", GraphRanker(projection=proj)._fetch_signals(ids, "point")),
            ("gaps", GapsRanker(projection=proj)._fetch_confidence_signals(ids)),
    ):
        for pid in ids:
            sig = signals[pid]
            assert sig["ep_state"] == EP_STATE_ABSENT, (name, pid, sig)
            assert sig["variance"] == 0.0, (
                f"{name} ranker reported variance {sig['variance']} for an "
                f"absent belief: {sig}")
            assert sig["contested"] is False, (name, pid, sig)

    # `why` agrees with all of them (it had the guard first).
    blocks = {pid: {} for pid in ids}
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, "
        "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "  coalesce(n.posterior_beta, n.ep_beta, 1.0), "
        "  n.ep_alpha IS NOT NULL AS measured, coalesce(n.status,'') AS st, "
        "  coalesce(n.outdated, false) AS od, false AS baseline_set, "
        "  true AS has_belief, n.posterior_alpha AS pa, n.ep_alpha AS ea, "
        "  n.confidence AS conf, n.posterior_beta AS pb, n.ep_beta AS eb",
        params={"ids": ids}).result_set
    _assemble_ep_rows(rows, blocks)
    for pid in ids:
        ep = blocks[pid]["ep"]
        assert ep["variance"] == 0.0, ep
        assert ep["contested"] is False, ep

    # And a genuinely stored pair is STILL wide and still contested, so the
    # guard cannot pass by answering "no" for everything.
    # Beta(1, 1): mean 0.5, variance 1/12 — the maximum, and legitimately wide.
    wide = sdk.create_point("statement", "a genuinely uncertain claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 1.0, "
                 "n.posterior_beta = 1.0", params={"id": wide})
    bd = annotate_ep_batch(proj.g, [wide])[wide]
    assert bd.variance == pytest.approx(1 / 12, abs=1e-6), bd
    assert bd.contested is True, bd
    state_sig = StateRanker(projection=proj)._fetch_point_signals([wide])[wide]
    assert state_sig["variance"] == pytest.approx(1 / 12, abs=1e-6), state_sig
    assert state_sig["contested"] is True, state_sig


def test_relationship_peer_block_uses_the_shared_belief_readers(sdk):
    """`get_relationships_bounded`'s peer block was the last local re-derivation.

    It derived presence from `ep_measured_cypher` and the value/width from the
    `coalesce(…, 1.0)` operands, so on the LIVE row shape one claim had two
    answers:

        half pair : peer conf=0.3333 variance=0.0889 contested=True
                    ep  mean=None  state=absent    variance=0.0  contested=False
        (0,0)     : peer conf=0.5    variance=0.0      contested=False
                    ep  mean=None  state=absent    variance=0.0  contested=False

    `contested` is load-bearing here: it promotes a peer into the CRITICAL
    class, which is exempt from the per-point cap AND the global budget — so a
    claim nothing measured could displace a real support peer. `sdk.search`
    attaches this block to every point result, so it is agent-facing.
    """
    from tortoise.search_engine import annotate_ep_batch, get_relationships_bounded

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "the peer-block target")["id"]
    half = sdk.create_point("statement", "half pair peer")["id"]
    degenerate = sdk.create_point("statement", "degenerate pair peer")["id"]
    measured = sdk.create_point("statement", "genuinely wide peer")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 0.5",
                 params={"id": half})
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 0.0, "
                 "n.posterior_beta = 0.0", params={"id": degenerate})
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 1.0, "
                 "n.posterior_beta = 1.0", params={"id": measured})
    op = sdk.create_point("statement", "the peer-block operator")["id"]
    proj.g.query(
        "MATCH (o:Point {id:$o}) SET o.is_operator = true, o.op_type = 'IMPL'",
        params={"o": op})
    proj.g.query(
        "MATCH (o:Point {id:$o}), (t:Point {id:$t}) CREATE (o)-[:IMPL]->(t)",
        params={"o": op, "t": target})
    for idx, peer in enumerate((half, degenerate, measured), start=1):
        proj.g.query(
            "MATCH (p:Point {id:$p}), (o:Point {id:$o}) "
            f"CREATE (p)-[:IMPL {{idx:{idx}}}]->(o)",
            params={"p": peer, "o": op})

    out = get_relationships_bounded(proj.g, [target])
    assert out.decoration_error == "", out.decoration_error
    peers = {e["related_id"]: e["peer"] for e in out[target]}
    eps = annotate_ep_batch(proj.g, [half, degenerate, measured])

    for pid in (half, degenerate):
        peer, ep = peers[pid], eps[pid]
        assert peer["confidence"] is None, (
            "the peer block fabricated a mean for a claim with no belief: "
            + repr(peer))
        assert peer["ep_state"] == "absent", peer
        # The peer block answers `None` for "no width applies" (a mitigation /
        # CORRECTS peer emits `None` too), so it can never be mistaken for the
        # Beta(1,1) maximum. The `ep` block renders the SAME absence as a
        # float-typed 0.0; the two are deliberately different renderings of one
        # fact, so this asserts each surface's own contract rather than a
        # disjunction that accepts either.
        assert peer["variance"] is None, (
            "the peer block fabricated a WIDTH from the coalesced 1.0 "
            "defaults: " + repr(peer))
        assert ep.variance == 0.0, ep
        assert peer["contested"] is False, (
            "a claim nothing measured entered the CRITICAL class as contested, "
            "where it is exempt from the caps and can displace real peers: "
            + repr(peer))
        # …and the ep block agrees on the VALUE and the state, which is the
        # whole invariant.
        assert ep.confidence_mean is None and ep.ep_state == "absent", ep

    # A genuinely wide peer keeps its width and its contested flag.
    assert peers[measured]["confidence"] == pytest.approx(0.5, abs=1e-4), peers[measured]
    assert peers[measured]["variance"] == pytest.approx(1 / 12, abs=1e-5), peers[measured]
    assert peers[measured]["contested"] is True, peers[measured]
    assert eps[measured].variance == pytest.approx(1 / 12, abs=1e-5), eps[measured]

    # The STATE must agree too, on every shape — not only on the absent ones.
    # The measured peer is the case that catches a wrong `baseline_set`
    # argument: seeded with a posterior it answers `measured` either way, so
    # the legacy-EP-prior population below is the discriminating one.
    for pid in (half, degenerate, measured):
        assert peers[pid]["ep_state"] == eps[pid].ep_state, (
            f"the peer block and the ep block disagree on {pid}: "
            f"{peers[pid]['ep_state']!r} vs {eps[pid].ep_state!r}")


def test_relationship_peer_block_agrees_on_ep_state_for_every_stored_shape(sdk):
    """Review cycle 12 (P2): the peer block reported the WRONG state name.

    It passed `baseline_set=bool(has_ep)` to `ep_state` — but `has_ep` is
    `ep_measured_cypher`'s "EP measured this", the exact OPPOSITE of a declared
    baseline. For a legacy EP prior (`ep_alpha`/`ep_beta` set, no posterior, no
    mirror — the shape `ep.py`'s evidence pre-write LEAVES on a non-baseline
    claim it clears) this block answered `baseline` while `annotate_ep_batch`,
    `why` and all three rankers answered `measured` — breaking the
    one-vocabulary invariant on the block `sdk.search` attaches to EVERY point
    result.

    Discriminating power: pre-fix the legacy-prior row below answers
    `baseline` where the `ep` block answers `measured`.
    """
    from tortoise.search_engine import annotate_ep_batch, get_relationships_bounded

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "the ep-state peer target")["id"]
    shapes = {
        # id: (cypher SET body, expected token) — every stored shape the
        # presence/measurement rules distinguish.
        "peer_posterior": ("n.posterior_alpha = 9.0, n.posterior_beta = 1.0",
                           "measured"),
        "peer_legacy_prior": ("n.ep_alpha = 3.0, n.ep_beta = 1.0",
                              "measured"),
        "peer_declared_baseline": ("n.ep_alpha = 5.0, n.ep_beta = 1.0, "
                                   "n.baseline_set = true", "baseline"),
        "peer_mirror_only": ("n.confidence = 0.9", "baseline"),
        "peer_half_pair": ("n.posterior_alpha = 0.5", "absent"),
        "peer_degenerate": ("n.posterior_alpha = 0.0, n.posterior_beta = 0.0",
                            "absent"),
        "peer_terminal": ("n.posterior_alpha = 1.0, n.posterior_beta = 1.0, "
                          "n.status = 'retracted'", "baseline"),
        "peer_absent": (None, "absent"),
    }
    ids = {}
    for pid, (sets, _expected) in shapes.items():
        ids[pid] = sdk.create_point("statement", f"shape {pid}")["id"]
        if sets:
            proj.g.query(f"MATCH (n:Point {{id:$id}}) SET {sets}",
                         params={"id": ids[pid]})
    op = sdk.create_point("statement", "the ep-state peer operator")["id"]
    proj.g.query(
        "MATCH (o:Point {id:$o}) SET o.is_operator = true, o.op_type = 'IMPL'",
        params={"o": op})
    proj.g.query(
        "MATCH (o:Point {id:$o}), (t:Point {id:$t}) CREATE (o)-[:IMPL]->(t)",
        params={"o": op, "t": target})
    for idx, pid in enumerate(ids.values(), start=1):
        proj.g.query(
            "MATCH (p:Point {id:$p}), (o:Point {id:$o}) "
            f"CREATE (p)-[:IMPL {{idx:{idx}}}]->(o)",
            params={"p": pid, "o": op})

    out = get_relationships_bounded(proj.g, [target])
    assert out.decoration_error == "", out.decoration_error
    peers = {e["related_id"]: e["peer"] for e in out[target]}
    eps = annotate_ep_batch(proj.g, list(ids.values()))
    for name, (_sets, expected) in shapes.items():
        pid = ids[name]
        assert pid in peers, f"{name} was dropped from the peer block"
        assert eps[pid].ep_state == expected, (
            f"the ep block answered {eps[pid].ep_state!r} for {name}, "
            f"expected {expected!r}")
        assert peers[pid]["ep_state"] == expected, (
            f"the peer block answered {peers[pid]['ep_state']!r} for {name}, "
            f"expected {expected!r} — the one-vocabulary invariant broken on "
            "the block sdk.search attaches to every point result")
        # The value must be consistent with the state on both surfaces: null
        # exactly when absent.
        assert (peers[pid]["confidence"] is None) == (expected == "absent"), (
            f"{name}: peer confidence {peers[pid]['confidence']!r} beside "
            f"state {peers[pid]['ep_state']!r}")
        assert (eps[pid].confidence_mean is None) == (expected == "absent"), (
            f"{name}: ep confidence_mean {eps[pid].confidence_mean!r} beside "
            f"state {eps[pid].ep_state!r}")


def test_the_twins_read_the_same_window_at_the_store_float_edges(sdk):
    """Cycle 27 (P2 + P3): the FLOOR of the store's parse window was not FLT_MIN,
    and the hex arm inherited a decimal-only rule.

    Two measured splits, both of which put presence on the Cypher side and
    nothing on the Python side — the shape this whole issue removes:

    * a HEX operand that is an exact float32 subnormal. `toFloatOrNull` reads
      `"0x1p-149"` (1.40129846432482e-45) while `numeric_operand` refused it,
      because the underflow guard (`|val| < FLT_MIN`) was justified by `strtof`'s
      ERANGE — a DECIMAL behaviour. Measured on one row
      (`posterior_alpha="0x1p-149", posterior_beta="1"`): the `ep` block said
      `absent`, the rankers said `measured` with `confidence_source:
      "unreadable"`, and `analyze` printed `0.00` — three answers.
    * a DECIMAL literal just BELOW min-normal that the store still reads.
      Scanning `1.1754943XXe-38` one digit at a time, `toFloatOrNull` flips
      between `1.175494315e-38` (null) and `1.175494316e-38`
      (1.17549435082229e-38 = FLT_MIN). Guarding at FLT_MIN refused the whole
      accepting band.

    So this test is a MEASURED table, not a rule restated: every literal below is
    answered by the engine and by the twin, and the two must agree on presence
    AND on the value. `"0x1.8p-149"` (inexact below the floor) and the exact
    decimal subnormal `"1.401298464324817e-45"` are the shapes that stay NULL —
    the store nulls both, so the twin must too, and a blanket "accept subnormals"
    fix would fail them.

    The last two rows are a RENDER facts, not a value split: the driver prints a
    float with ~15 significant digits, so a >2**53 float32 comes back as
    `9007199254740990.0` while its value IS `2**53` (proved in the assertion).
    That is why the twin is compared to the store's VALUE in the loop below and
    those two rows are checked separately.
    """
    from decimal import Decimal, localcontext

    from tortoise import search_engine as _se

    g = sdk._get_proj().g
    agreeing = [
        # the hex window: exact float32 subnormals the store READS
        "0x1p-149", "0x1p-148", "0x1p-140", "0x0.8p-148", "0x0.000002p-126",
        "0x1p-127", "0x1p-126",
        # …and the hex shapes it nulls
        "0x1.8p-149", "0x1.000002p-149", "0x1p-150", "0x1.fffffffp-149",
        "0x1.fffffep-127", "0x1p-1030",
        # the decimal floor band, both sides of the measured flip
        "1.175494315e-38", "1.175494316e-38", "1.17549435e-38",
        "1.1754944e-38", "1.1754943e-38", "1.1754942e-38",
        # the DECIMAL arm has NO exactness exemption (measured: this literal is
        # 2**-149 exactly and the store still answers null)
        "1.401298464324817e-45", "2.802596928649634e-45", "1e-45", "1e-40",
        # LEADING WHITESPACE on a hex literal: `toFloat` skips every byte C's
        # `isspace` accepts, so the store reads `"\n0x1p4"`. The hex patterns
        # spelled the prefix `[ \t]*` while the decimal ones used `\s*`, so
        # Python refused five spellings the store reads — one claim reported
        # `absent` in the `ep` block beside `measured` in both rankers
        # (measured: 35 splits over a 104-literal sweep). `\xa0` (NBSP) is
        # NOT in C's isspace, and `re.ASCII` is what keeps `\s` inside that
        # class — the row below is null on BOTH sides and pins the flag on the
        # FLOAT pattern (the zero pattern's copy of it is defensive: a prefix
        # this refuses can never reach it).
        "\n0x1p4", "\r0x1p4", "\t0x1p4", "  \n\t0x1p4", "\x0b0x1.8p1",
        "\x0c0x10", "\n0x0p0", "\n0x1p4 ", "\u00a00x1p4", "\u00a00x0p0",
        # ordinary values, and the NEGATIVE midpoint (`_f32_neighbour` used to
        # step the raw sign-magnitude bits the wrong way below zero)
        "0.6", "0x1p4", "0x1.0000001p4", "1.0000000596046448",
        "-1.0000000596046448", "-1.0", "-1.00000006",
    ]
    for lit in agreeing:
        stored = g.query("RETURN toFloatOrNull($v)", params={"v": lit}).result_set[0][0]
        twin = _se.numeric_operand(lit)
        if stored is None:
            assert twin is None, (
                f"{lit!r}: the store answers null, the twin answers {twin!r} — "
                "presence is then decided by whichever side is asked")
            continue
        assert twin is not None, (
            f"{lit!r}: the store reads {stored!r}, the twin refuses it — the "
            "rankers then order on a belief they cannot read")
        # Compare at FLOAT32 resolution, exactly: the driver renders a float
        # with ~15 significant digits, so a raw `==` is too strict for a normal
        # value and a 1e-6 relative tolerance is too loose — it would accept the
        # one-ulp error this test exists to catch (the negative-midpoint row).
        assert _se._float32(twin) == _se._float32(stored), (
            f"{lit!r}: the store serves {stored!r} ({_se._float32(stored)!r} as "
            f"float32), the twin answers {twin!r}")

    # The read floor is the MEASURED one, and it sits BELOW min-normal.
    assert _se._F32_UNDERFLOW_READ_FLOOR < _se._FLOAT32_MIN_NORMAL
    assert _se._F32_UNDERFLOW_READ_FLOOR == (
        _se._FLOAT32_MIN_NORMAL - 2.0 ** -151)

    # ⛔ The rule is the LITERAL's exactness, NOT an arm-wide band: a DECIMAL
    # literal that names 2**-149 to all 149 of its digits is READ by the store
    # (measured), so a floor-only rule would refuse a spelling the store serves
    # — the same split, mirrored. Its 16-digit `repr()` is null.
    with localcontext() as ctx:
        ctx.prec = 200                     # 2**-149 has 149 decimals
        exact_subnormal = format(Decimal(2) ** -149, "f")
    assert len(exact_subnormal.split(".")[1]) == 149, exact_subnormal
    assert g.query("RETURN toFloatOrNull($v)", params={
        "v": exact_subnormal}).result_set[0][0] == pytest.approx(
            1.401298464324817e-45, rel=1e-6), (
        "the store stopped reading the exact decimal expansion of 2**-149 — "
        "re-measure before trusting the text-exactness rule")
    assert _se.numeric_operand(exact_subnormal) is not None
    assert _se.numeric_operand("1.401298464324817e-45") is None

    # The render artifact, stated instead of implied: the VALUE agrees, the
    # driver's ~15-digit render does not.
    assert g.query(
        "RETURN toFloatOrNull('9007199254740993') = 9007199254740992.0"
    ).result_set == [[True]], "the store's value for this literal changed"
    assert _se.numeric_operand("9007199254740993") == 9007199254740992.0


def test_a_hex_subnormal_operand_is_a_belief_on_every_surface(sdk):
    """Cycle 27 (P2) end to end: one row, four surfaces, one answer.

    `posterior_alpha="0x1p-149", posterior_beta="1"` is a real belief to the
    store (alpha 1.4e-45, beta 1). Before the fix the `ep` block and `why` said
    `absent` while both rankers said `measured` and could not read the value, and
    `SubgraphExpander` served a confidence of 0.0 — maximum disbelief — for it.
    The mean is 1.4e-45, which ROUNDS to 0.0 at 4 and 6 dp, so the test compares
    what the surfaces serve to each other rather than to a constant.

    Discriminating power: with the decimal-only underflow guard restored, the
    `ep` block below answers `absent`.
    """
    from tortoise import search_engine as _se
    from tortoise.ranking import GapsRanker, StateRanker, SubgraphExpander
    from tortoise.search_engine import (
        EP_STATE_MEASURED,
        annotate_ep_batch,
        belief_value,
    )

    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "a hex subnormal operand")["id"]
    proj.g.query(
        "MATCH (n:Point {id:$id}) "
        "SET n.posterior_alpha = '0x1p-149', n.posterior_beta = '1'",
        params={"id": pid})

    # The fixture is the shape it claims: the store reads it, and so does the twin.
    assert proj.g.query(
        "RETURN toFloatOrNull('0x1p-149') AS v").result_set[0][0] ==         pytest.approx(1.401298464324817e-45, rel=1e-6)
    assert _se.numeric_operand("0x1p-149") == pytest.approx(
        1.401298464324817e-45, rel=1e-6)

    eps = annotate_ep_batch(proj.g, [pid])[pid]
    assert eps.ep_state == EP_STATE_MEASURED, eps
    assert eps.confidence_mean is not None, (
        "the ep block calls a belief it can read absent")

    rec = StateRanker(projection=proj).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["recall_ranking"]
    gap = GapsRanker(projection=proj).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["gaps_ranking"]
    for name, row in (("recall_ranking", rec), ("gaps_ranking", gap)):
        assert row["ep_state"] == EP_STATE_MEASURED, (name, row)
        assert row["confidence"] is not None, (name, row)
        assert row["confidence_source"] == "posterior", (name, row)
        assert row["confidence"] == pytest.approx(
            eps.confidence_mean, abs=1e-4), (name, row, eps)

    # …and the shared value reader agrees with both, which is the invariant the
    # four surfaces are supposed to share.
    assert belief_value(posterior_alpha="0x1p-149", posterior_beta="1") is not None

    node = next(n for n in SubgraphExpander(projection=proj).expand(
        [pid], depth=1)["nodes"] if n.get("id") == pid)
    assert node["ep_state"] == EP_STATE_MEASURED, node
    assert node["confidence"] is not None, (
        f"the subgraph served {node['confidence']!r} for a belief it can read "
        "— 0.0 is maximum disbelief, not absence")


def test_the_peer_block_serves_a_rounded_belief_like_every_sibling(sdk):
    """Cycle 27 (P3): the peer block was the ONE surface serving a raw double.

    Same claim, α=1 β=2: `ep` block `0.3333`, rankers `0.333333`, peer block
    `0.3333333333333333`. A consumer comparing two surfaces for one claim got two
    numbers, and the peer payload's own `variance` key two lines below was
    already rounded to 6.
    """
    from tortoise.search_engine import annotate_ep_batch, get_relationships_bounded

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "the rounded-peer target")["id"]
    peer = sdk.create_point("statement", "the rounded-peer claim")["id"]
    proj.g.query(
        "MATCH (n:Point {id:$id}) "
        "SET n.posterior_alpha = 1.0, n.posterior_beta = 2.0",
        params={"id": peer})
    op = sdk.create_point("statement", "the rounded-peer operator")["id"]
    proj.g.query(
        "MATCH (o:Point {id:$o}) SET o.is_operator = true, o.op_type = 'IMPL'",
        params={"o": op})
    proj.g.query(
        "MATCH (p:Point {id:$p}), (o:Point {id:$o}) "
        "CREATE (p)-[:IMPL {idx:1}]->(o)",
        params={"p": peer, "o": op})
    proj.g.query(
        "MATCH (o:Point {id:$o}), (t:Point {id:$t}) CREATE (o)-[:IMPL]->(t)",
        params={"o": op, "t": target})

    out = get_relationships_bounded(proj.g, [target])
    assert out.decoration_error == "", out.decoration_error
    peers = {e["related_id"]: e["peer"] for e in out[target]}
    assert peer in peers, "the peer was dropped from the block"
    value = peers[peer]["confidence"]
    assert value is not None, peers[peer]
    assert value == round(value, 6), (
        f"the peer block serves an unrounded belief ({value!r}) while every "
        "sibling key and surface rounds")
    # …and it is the SAME belief the other surfaces serve, to their precision.
    assert round(value, 4) == round(
        annotate_ep_batch(proj.g, [peer])[peer].confidence_mean, 4)


def test_negative_beta_operands_are_not_a_belief(sdk):
    """Cycle 11: a NEGATIVE operand passed every pre-existing guard.

    Beta α/β are pseudo-counts. `α=-1, β=3` satisfies `α+β > 0`, so the
    presence rule called it a belief and the arithmetic produced a mean of
    -0.5 (or 1.5 for `α=3, β=-1`) and a variance of -0.25 — an out-of-range
    number that every threshold consumer (`min_confidence`,
    `topic_summarization`'s "settled", `volunteer._eligible`) read as real and
    maximally certain. The write path made it worse than before this change:
    `set_point_baseline` refreshed the SERVED `n.confidence` mirror to it and
    journaled it, so a rebuild reproduced it.
    """
    from tortoise.ranking import StateRanker
    from tortoise.search_engine import (
        EP_STATE_ABSENT,
        _beta_variance,
        annotate_ep_batch,
        belief_value,
        ep_has_belief_cypher,
        ep_state,
    )

    # The rules, in isolation.
    assert belief_value(posterior_alpha=-1.0, posterior_beta=3.0) is None
    assert belief_value(posterior_alpha=3.0, posterior_beta=-1.0) is None
    assert _beta_variance(3.0, -1.0) == 0.0
    assert _beta_variance(-1.0, 3.0) == 0.0
    assert ep_state(posterior_alpha=3.0, ep_alpha=None,
                    posterior_beta=-1.0, ep_beta=None) == EP_STATE_ABSENT
    assert ep_state(posterior_alpha=-1.0, ep_alpha=None,
                    posterior_beta=3.0, ep_beta=None) == EP_STATE_ABSENT

    # …and the Cypher twin agrees on the live engine.
    proj = sdk._get_proj()
    bad = sdk.create_point("statement", "negative beta operand")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 3.0, "
                 "n.posterior_beta = -1.0", params={"id": bad})
    present = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN " + ep_has_belief_cypher("n"),
        params={"id": bad}).result_set
    assert bool(present[0][0]) is False, (
        "the Cypher presence rule called a NEGATIVE operand a belief — the "
        "Python twin says absent, so a surface would read a state the `ep` "
        "block denies")
    bd = annotate_ep_batch(proj.g, [bad])[bad]
    assert bd.confidence_mean is None and bd.ep_state == "absent", bd
    assert bd.variance == 0.0 and bd.contested is False, bd
    sig = StateRanker(projection=proj)._fetch_point_signals([bad])[bad]
    assert sig["confidence"] is None and sig["variance"] == 0.0, sig

    # The write path REFUSES a negative operand rather than persisting it.
    with pytest.raises(ValueError, match="must be >= 0"):
        sdk.set_point_baseline(bad, -1.0, 3.0)
    with pytest.raises(ValueError, match="must be >= 0"):
        sdk.set_point_baseline(bad, 3.0, -1.0)
    after = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.ep_alpha, n.ep_beta, n.confidence",
        params={"id": bad}).result_set[0]
    assert after[0] is None and after[1] is None, (
        "a refused baseline still wrote the prior: " + repr(after))


def test_contested_claims_reads_an_int_stored_pair(sdk):
    """Cycle 11: `(a*b)/((a+b)*(a+b)*(a+b+1))` INTEGER-divides on int operands.

    FalkorDB `/` truncates when both operands are integers, and α/β are stored
    as ints when written as ints. So a genuinely contested integer pair
    computed `v = 0`, failed `v > threshold`, and the claim was OMITTED from
    `ep.get_contested_claims` and the PR-prune scan — while
    `annotate_ep_batch`, `why` and all three rankers reported `variance 0.05,
    contested True` for it. `toFloat` on every operand, the same guard
    `analyze._BELIEF` and the ranker reads already carry.
    """
    from tortoise.search_engine import annotate_ep_batch

    proj = sdk._get_proj()
    int_pair = sdk.create_point("statement", "int stored contested pair")["id"]
    float_pair = sdk.create_point("statement", "float stored pair")["id"]
    # Written as INTEGERS — the shape that truncated.
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2, "
                 "n.posterior_beta = 2", params={"id": int_pair})
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
                 "n.posterior_beta = 2.0", params={"id": float_pair})
    stored = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.posterior_alpha, "
        "n.posterior_beta", params={"ids": [int_pair, float_pair]}).result_set
    kinds = {row[0]: (type(row[1]).__name__, type(row[2]).__name__)
             for row in stored}
    assert kinds[int_pair] == ("int", "int"), (
        "fixture did not store integers — the truncation cannot be exercised: "
        + repr(kinds))

    expected = annotate_ep_batch(proj.g, [int_pair])[int_pair]
    assert expected.variance == pytest.approx(0.05, abs=1e-6), expected
    assert expected.contested is True, expected

    from tortoise.ep import TortoiseEP
    ep_view = TortoiseEP(projection=proj)
    listed = {c["id"] for c in ep_view.get_contested_claims()}
    assert int_pair in listed, (
        "an INT-stored contested pair was omitted by the variance scan — the "
        "Cypher divided as integers and computed v = 0 while every read "
        "surface says variance 0.05 / contested: "
        + repr(sorted(listed)))


def test_every_ranker_reads_an_overflowing_pair_the_same_way(sdk):
    """Cycle 14: `StateRanker` and `GapsRanker` still divided directly.

    Cycle 13 routed `belief_value`, `GraphRanker`, `SubgraphExpander` and
    `analyze._BELIEF` through `_beta_mean`, and MISSED the two rankers that
    ORDER on the value. For `update_point(posterior_alpha=1e308,
    posterior_beta=1e308)` — a stored pair the presence rule accepts — the sum
    overflows to `inf`, so `1e308/inf == 0.0`: measured on the reviewed head,
    one claim answered `0.5` in the `ep` block and in `graph_ranking`, and
    `0.0` in `recall_ranking` and `gaps_ranking`, all four labelled `measured`.
    `recall_ranking`'s 0.0 is not cosmetic — it is the `conf**exp` base of the
    row's score.

    A `(0, 1.7e308)` pair is pinned too: its sum is FINITE but above the
    overflow threshold, and the Cypher twin's first cut rendered `null` for it
    (the identity `1/(1 + β/α)` is undefined at α = 0) beside
    `ep_state: "measured"` while the Python owner answered 0.0.
    """
    proj = sdk._get_proj()

    def _point(content, alpha, beta):
        pid = sdk.create_point("statement", content)["id"]
        proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = $a, "
                     "n.posterior_beta = $b, n.confidence = null",
                     params={"id": pid, "a": alpha, "b": beta})
        return pid

    symmetric = _point("overflowing symmetric pair", 1e308, 1e308)
    zero_alpha = _point("zero alpha, huge beta", 0.0, 1.7e308)
    skewed = _point("overflowing skewed pair", 1.7e308, 1e307)
    ids = [symmetric, zero_alpha, skewed]
    expected = {
        symmetric: 1.0 / (1.0 + 1e308 / 1e308),          # 0.5
        zero_alpha: 0.0,                                  # 0/1.7e308
        skewed: 1.0 / (1.0 + 1e307 / 1.7e308),            # 0.944…
    }

    ann = annotate_ep_batch(proj.g, ids)
    rec = {r["id"]: r["recall_ranking"] for r in
           StateRanker(projection=proj).rerank(
               [{"id": p, "scores": {"rrf": 1.0}} for p in ids])}
    gaps = {r["id"]: r["gaps_ranking"] for r in
            GapsRanker(projection=proj).rerank(
                [{"id": p, "scores": {"rrf": 1.0}} for p in ids])}
    graph = {r["id"]: r["graph_ranking"] for r in
             GraphRanker(projection=proj).rerank(
                 [{"id": p, "scores": {"rrf": 1.0}} for p in ids])}

    for pid in ids:
        want = expected[pid]
        got = {
            "ep": ann[pid].confidence_mean,
            "recall_ranking": rec[pid]["confidence"],
            "gaps_ranking": gaps[pid]["confidence"],
            "graph_ranking": graph[pid]["confidence"],
        }
        for surface, value in got.items():
            assert value is not None, (
                f"{surface} reported no belief for {pid} — an overflowing "
                "pair is a stored belief, and null beside a present state is "
                "the two-answers defect")
            # 1e-4, not 1e-9: the `ep` block rounds its mean to 4 dp.
            assert value == pytest.approx(want, abs=1e-4), (
                f"{surface} reports {value!r} for the pair behind {pid}; the "
                f"mean is {want!r} — one claim, one number")
        for surface, row in (("recall_ranking", rec[pid]),
                             ("gaps_ranking", gaps[pid]),
                             ("graph_ranking", graph[pid])):
            assert row["ep_state"] == EP_STATE_MEASURED, (surface, row)


def test_a_width_that_cannot_be_computed_is_not_the_widest_claim(sdk):
    """Cycle 14: `ep_width_cypher` admitted a pair whose VARIANCE is NaN.

    `(1e308, 1e308)` has both operands, both non-negative and a positive
    total, so the strict width rule said "usable width" — and the variance
    formula `αβ/((α+β)²(α+β+1))` then overflows to `inf/inf`. Measured:
    `analyze.most_uncertain` put that claim FIRST with `var: nan`, ahead of a
    genuine Beta(1,1) `1/12` — "no computable width" winning a "where are we
    least certain" answer, the same inversion as the mirror-only case.

    `_beta_variance` refuses a non-finite sum; the predicate that decides
    ADMISSIBILITY must carry the same guard.
    """
    proj = sdk._get_proj()

    def _point(content, alpha, beta):
        pid = sdk.create_point("statement", content)["id"]
        proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = $a, "
                     "n.posterior_beta = $b", params={"id": pid, "a": alpha,
                                                      "b": beta})
        return pid

    no_width = _point("a pair with no computable width", 1e308, 1e308)
    widest = _point("a genuinely maximally wide pair", 1.0, 1.0)
    tight = _point("a contested pair", 2.0, 2.0)

    from tortoise.ep import TortoiseEP
    listed = {c["id"]: c for c in TortoiseEP(projection=proj).get_contested_claims()}
    assert no_width not in listed, (
        "a pair whose variance overflows to NaN was listed as contested: "
        + repr(listed.get(no_width)))
    assert widest in listed and tight in listed, sorted(listed)

    ann = annotate_ep_batch(proj.g, [no_width, widest, tight])
    assert ann[no_width].variance == 0.0, ann[no_width]
    assert ann[no_width].contested is False, ann[no_width]
    assert ann[widest].variance == pytest.approx(1 / 12, abs=1e-6)
    assert ann[tight].variance == pytest.approx(0.05, abs=1e-6)
    assert ann[tight].contested is True, ann[tight]

    # …and the analyze template that ranks by width must not put it on top.
    from tortoise.analyze import analyze
    out = analyze("which claims are least uncertain?", proj=proj,
                  use_llm=False, entity_subgraph_ids={no_width, widest, tight})
    text = str(out.get("answer") or "")
    assert "no computable width" not in text, (
        "a claim with no computable width won the uncertainty answer: " + text)
    assert "nan" not in text.lower(), (
        "the answer rendered a NaN width: " + text)


def test_a_draft_with_no_belief_is_absent_not_withheld(sdk):
    """Cycle 14: the revised manifest rule must hold for a BELIEF-LESS draft.

    The doc now says `ep_state` is the authority (absent vs present) and
    `confidence_source: "uncomputed"` explains why a value is missing. A draft
    with NO belief emits `uncomputed` + `absent` — so `uncomputed` alone does
    not mean "withheld", and a consumer reading it that way would label a
    no-belief row as "carries a belief, display suppressed": the exact
    conflation this issue removes, at the contract boundary.
    """
    proj = sdk._get_proj()
    bare = sdk.create_point("statement", "a draft with no belief at all",
                            status="draft")["id"]
    with_prior = sdk.create_point("statement", "a draft carrying a prior",
                                  credibility="high", status="draft")["id"]
    rows = {r["id"]: r["recall_ranking"] for r in StateRanker(projection=proj).rerank(
        [{"id": p, "scores": {"rrf": 1.0}} for p in (bare, with_prior)])}

    assert rows[bare]["ep_state"] == EP_STATE_ABSENT, rows[bare]
    assert rows[bare]["confidence"] is None, rows[bare]
    assert rows[bare]["confidence_source"] == UNCOMPUTED_CONFIDENCE_SOURCE, rows[bare]
    assert rows[bare]["belief_informed_order"] is False, rows[bare]

    assert rows[with_prior]["ep_state"] == EP_STATE_BASELINE, rows[with_prior]
    assert rows[with_prior]["confidence"] is None, rows[with_prior]
    assert rows[with_prior]["confidence_source"] == UNCOMPUTED_CONFIDENCE_SOURCE, rows[with_prior]
    assert rows[with_prior]["belief_informed_order"] is True, rows[with_prior]

    # The documented rule, executable: the STATE decides, the SOURCE explains.
    for pid, row in rows.items():
        has_belief = annotate_ep_batch(proj.g, [pid])[pid].ep_state != EP_STATE_ABSENT
        assert (row["ep_state"] != EP_STATE_ABSENT) == has_belief, (pid, row)


def test_the_width_rule_and_the_width_formula_agree_across_the_whole_range(sdk):
    """Cycle 15: `ep_width_cypher` bounded the SUM, but the formula squares it.

    The cycle-14 guard closed with `sum <= 1e308` and its comment claimed that
    was "the same guard `_beta_variance` applies". It is not: `_beta_variance`
    guards the DENOMINATOR `s*s*(s+1)`, which overflows far earlier and
    underflows far later. Measured on the reviewed head, one predicate / one
    formula over a log sweep:

        pair            width_admits  _beta_variance  query formula
        1e-200,0        True          0.0             nan
        1e-165,1e-165   True          0.0             nan
        1e+200,1e+200   True          0.0             nan
        1e+308,1e+308   False         0.0             nan   <- the only one rejected

    so `analyze.most_uncertain` put six rows in front of the genuine maxima by
    a NaN, and with TWO nan rows FalkorDB's non-total comparator de-sorted the
    whole list. This is the property, over the sweep: the rule admits a shape
    IF AND ONLY IF the formula computes a value, on both twins.
    """
    from tortoise.search_engine import (
        _beta_variance,
        beta_variance_cypher,
    )

    proj = sdk._get_proj()
    exponents = list(range(-200, 309, 17))
    pairs = [(10.0 ** e, 10.0 ** e) for e in exponents]
    pairs += [(10.0 ** e, 0.0) for e in exponents[:6]]
    pairs += [(1.0, 1e308), (1e308, 1.0), (3.0, -1.0)]
    # #11702 cycle 19: the UNREADABLE shapes. The sweep was numeric-only, so the
    # one class the rule must refuse — an operand the store cannot read as a
    # number — was absent from it, and `("abc", 2.0)` slipped through the
    # admission rule while the formula answered NULL. `most_uncertain` then put
    # it FIRST with a NULL width and `analyze()` answered the question with a
    # TypeError string (measured).
    UNREADABLE = [(None, 2.0), ("abc", 2.0), (True, 2.0), (2.0, "abc"),
                  (0.0, "abc"), ("", 2.0)]
    unreadable_from = len(pairs)
    pairs += UNREADABLE

    ids = []
    for i, (a, b) in enumerate(pairs):
        pid = sdk.create_point("statement", f"width sweep {i}")["id"]
        proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = $a, "
                     "n.posterior_beta = $b, n.confidence = null",
                     params={"id": pid, "a": a, "b": b})
        ids.append(pid)

    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, " + _se.ep_width_cypher("n") + " AS admits, "
        # The operands the WIDTH RULE itself reads — `coalesce(posterior, ep)`,
        # with NO `1.0` default. A `1.0` default would pre-invent the operand
        # and hide exactly the unreadable shapes this sweep must cover (cycle
        # 19: `(None, 2.0)` measured 0.0556 through the default instead of 0.0).
        + beta_variance_cypher("coalesce(n.posterior_alpha, n.ep_alpha)",
                              "coalesce(n.posterior_beta, n.ep_beta)") + " AS v",
        params={"ids": ids}).result_set
    by_id = {r[0]: (r[1], r[2]) for r in rows}

    # Cycle 16: the property has TEETH IN BOTH DIRECTIONS, and it is checked
    # against an INDEPENDENT reference (Decimal), not against the shipped band —
    # comparing the two twins is near-tautological now that both apply the same
    # band. The direction the earlier form was missing is the one a too-TIGHT
    # floor violates: a materially wide claim whose width a double CAN carry must
    # not be published as 0.0. Measured on the cycle-15 head, which shipped a
    # `1e-100` floor: a `(1e-150, 1e-150)` pair has true variance 0.25 and a
    # perfectly ordinary double representation, and it was answered
    # `variance 0.0, contested False` and excluded from every uncertainty scan.
    MIN_NORMAL = sys.float_info.min  # 2.225e-308: the double's normal floor
    MATERIAL = Decimal("1e-6")       # below this no threshold can care

    def _true_variance(x, y):
        """The variance at high precision, independent of any shipped band."""
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None  # an unreadable operand has no variance at all
        if isinstance(x, bool) or isinstance(y, bool):
            return None
        if x < 0 or y < 0:
            return None
        dx, dy = Decimal(repr(x)), Decimal(repr(y))
        total = dx + dy
        if total <= 0:
            return None
        return dx * dy / (total * total * (total + 1))

    admittable = 0
    required = 0
    # Cycle 19: an UNREADABLE operand is never admitted, and the formula answers
    # `0.0` (a number) for it rather than NULL — the two halves must agree.
    for pid, (a, b) in zip(ids[unreadable_from:],
                            pairs[unreadable_from:], strict=False):
        admits, query_v = by_id[pid]
        assert admits is None or bool(admits) is False, (
            f"({a!r}, {b!r}) has an UNREADABLE operand but the width rule "
            "admitted it — it then wins the uncertainty ranking with no width")
        assert query_v == 0.0, (
            f"({a!r}, {b!r}): the width formula answered {query_v!r} where its "
            "Python twin and its docstring both answer 0.0")
    for pid, (a, b) in zip(ids, pairs, strict=False):
        admits, query_v = by_id[pid]
        # (i) The query's width is ALWAYS a number — never null, never NaN, never
        # inf. This is the arm that catches a band too LOOSE: a NaN sorts first
        # on FalkorDB because its comparator is not total over NaN, and one such
        # row de-sorts the whole list.
        assert query_v is not None, (pid, a, b, "null width from the query")
        assert query_v == query_v, (pid, a, b, "NaN width from the query")
        assert query_v >= 0.0 and query_v != float("inf"), (pid, a, b, query_v)
        if admits is None:
            continue
        if admits:
            admittable += 1

        truth = _true_variance(a, b)
        if truth is None:
            # An unreadable/negative pair: no double arithmetic to demand.
            continue
        numerator, total = a * b, a + b
        denominator = total * total * (total + 1)
        # (ii) A materially wide claim the double CAN compute must be admitted
        # and answered with (approximately) its real width. This is the arm that
        # catches a band too TIGHT — i.e. the bug cycle 15 shipped.
        if (truth >= MATERIAL
                and numerator >= MIN_NORMAL and 0.0 < denominator < 1e307):
            required += 1
            assert admits, (
                f"({a!r}, {b!r}) has true variance {float(truth):.4g} — "
                "materially wide, and a double carries it exactly — but the "
                "width rule REFUSED it, so it is published as 0.0 and dropped "
                "from every uncertainty scan (fabricated certainty)")
            assert query_v > 0.0, (a, b, query_v)
            assert query_v == pytest.approx(float(truth), rel=1e-6), (
                f"({a!r}, {b!r}) answered width {query_v!r} but its true "
                f"variance is {float(truth)!r}")

        # (iii) …and the Python owner answers what Cypher answered.
        py_var = _beta_variance(a, b)
        assert py_var == pytest.approx(query_v, abs=1e-12) or (
            py_var == 0.0 and query_v == 0.0), (
            f"_beta_variance({a!r}, {b!r}) = {py_var!r} but the Cypher twin "
            f"answers {query_v!r} — two widths for one claim")

    # Non-vacuity, counted on BOTH arms: the sweep must contain shapes the rule
    # admits AND materially wide shapes whose admission is mandatory. A guard of
    # the form `assert admittable >= 10` on its own tolerated a band ~50 orders
    # tighter than shipped (measured: 12 of 39).
    assert 10 <= admittable < len(ids), (
        f"the sweep admitted {admittable} of {len(ids)} shapes — it must contain "
        "shapes on BOTH sides of the band, or one arm is untested")
    assert required >= 4, (
        f"only {required} shapes in the sweep were materially wide — the sweep "
        "cannot detect a too-tight floor")

    # …and the rendered answer contains no NaN, with ordinary widths in order.
    for i, (a, b) in enumerate(((1.0, 1.0), (2.0, 2.0), (90.0, 10.0))):
        pid = sdk.create_point("statement", f"ordinary width {i}")["id"]
        proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = $a, "
                     "n.posterior_beta = $b", params={"id": pid, "a": a, "b": b})
        ids.append(pid)
    from tortoise.analyze import analyze
    out = analyze("what is uncertain?", proj=proj, use_llm=False,
                  entity_subgraph_ids=set(ids))
    text = str(out.get("answer") or "")
    assert "nan" not in text.lower(), "the uncertainty answer rendered a NaN: " + text


def test_the_subgraph_display_agrees_with_every_other_surface(sdk):
    """Cycle 17 P1: `SubgraphExpander` spelled its OWN presence test.

    The display arm was
    `coalesce(n.confidence, CASE WHEN alpha + beta > 0 THEN <mean> ELSE null END)`,
    and on a NUMERIC-STRING pair FalkorDB CONCATENATES the sum — `'0.7' + '0.3'`
    is `'0.70.3'`, which compares to 0 as NULL — so the arm was never taken and
    `recall_subgraph` served `confidence: null` beside `ep_state: "measured"`
    for a claim every other surface reports at 0.7. That pair (null value,
    measured state) is the exact combination the sibling comment calls
    impossible.
    """
    from tortoise.ranking import SubgraphExpander

    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "a claim with string operands")["id"]
    proj.g.query('MATCH (n:Point {id:$id}) SET n.posterior_alpha = "0.7", '
                 'n.posterior_beta = "0.3", n.confidence = null',
                 params={"id": pid})

    # The mechanism first, so a regression here is diagnosed rather than
    # guessed: FalkorDB concatenates `string + string`.
    concat = proj.g.query('RETURN "0.7" + "0.3" AS s').result_set[0][0]
    assert concat == "0.70.3", concat

    expanded = SubgraphExpander(projection=proj).expand([pid], depth=1)
    nodes = expanded.get("nodes") if isinstance(expanded, dict) else expanded
    node = next(n for n in nodes if n.get("id") == pid)
    assert node["ep_state"] == EP_STATE_MEASURED, node
    assert node.get("confidence") == pytest.approx(0.7, abs=1e-3), (
        "the subgraph display shows `confidence: null` beside "
        f"`ep_state: measured` for a claim whose belief is 0.7: {node}")
    ann = annotate_ep_batch(proj.g, [pid])[pid]
    assert node.get("confidence") == pytest.approx(ann.confidence_mean or 0.0,
                                                  abs=1e-3), (node, ann)


def _decorate(proj, pid):
    """Decorate ``pid`` through a REAL operator neighbourhood (cycle 19).

    The contested arm (Q2-crit) is the only place the peer variance is
    evaluated, and it is gated on `if op_ids:` — the operators the requested
    points belong to. A bare point has none, so with no operator beside it the
    variance expression is never evaluated and the query cannot raise. The
    cycle-17 pin built exactly that fixture, so it passed on the PRE-FIX code:
    measured with `tortoise/search_engine.py` reverted to `3bd1ba47c1`, the old
    pin was green while the real call answered
    `decoration_error='ResponseError: Type mismatch: …'` with 0 entries.

    The peer is the point ITSELF on purpose: Q2-crit matches
    `(op)-[r2]-(other:Point)`, so the requested point is one of its own
    operator's `other` rows — the unreadable operands reach the variance
    expression through the shape under test, not through a bystander.

    `decoration_error` is read as the ATTRIBUTE it is. `str(out)` is the plain
    dict repr and never carries it (measured: `"Type mismatch" in str(out)` is
    False even with the error set), so a stringified assertion cannot see the
    one channel the property is about — the old pin's second vacuity.
    """
    from tortoise.search_engine import get_relationships_bounded

    out = get_relationships_bounded(proj.g, [pid])
    # `decoration_error` is a class attribute default, so an empty answer is
    # readable unconditionally (a plain dict from a stand-in answers "").
    error = getattr(out, "decoration_error", "")
    assert not error, (
        "the relationship scan raised on an operand the store cannot read as "
        f"a number: {error}")
    return out


def test_the_relationship_scan_survives_an_unreadable_operand(sdk):
    """Cycle 17 P1: the contested arm's inline variance RAISED on the whole query.

    FalkorDB raises on `string * string` / `bool * bool` ("Type mismatch:
    expected Integer, Float, or Null but was String"), and the raise is not
    per-row: `get_relationships_bounded` returned its fail-open seed with
    `decoration_error` set, so EVERY requested point lost its relationships.
    Three shapes this PR declares must read ABSENT destroyed the decoration
    instead — the opposite of the totality contract. The arm now calls the
    shared `beta_variance_cypher`.

    ⛔ Cycle 19 F2: the earlier form of this test created LONE points, and the
    arm it names (Q2-crit) only runs when the Q1a `MATCH (n)-[…]->(op:Point
    {is_operator:true})` found an operator — so the query never executed, the
    variance expression was never evaluated, and the test PASSED UNCHANGED ON
    `origin/main`. A real operator edge is what reaches the arm; without it
    this asserts nothing. Verified red-on-main / green-here with the edge
    present (`ResponseError: Type mismatch …` vs `decoration_error: ''`).

    Cycle 21: the pin also carries an all-readable CONTROL. Without it a guard
    that made the query raise unconditionally would satisfy the three bad
    shapes on its own, and the three shapes would be indistinguishable from a
    scan that simply returned nothing for every point.
    """
    proj = sdk._get_proj()
    shapes = {
        "numeric_string_pair": ('SET n.posterior_alpha = "0.7", '
                                'n.posterior_beta = "0.3"'),
        "boolean_pair": ("SET n.posterior_alpha = true, n.posterior_beta = 2.0"),
        "nonnumeric_string": ('SET n.posterior_alpha = "abc", '
                              'n.posterior_beta = 1.0'),
        "all_numeric_control": ("SET n.posterior_alpha = 0.7, "
                                "n.posterior_beta = 0.3"),
    }
    support = sdk.create_point("statement", "ZQDECOR one supported claim")["id"]
    pids = {}
    for label, setter in shapes.items():
        pid = sdk.create_point("statement", f"ZQDECOR {label}")["id"]
        # The operator is what lets Q2-crit evaluate the peer variance at all,
        # and the requested point is its own `other` row, so the bad operands
        # below reach the variance expression through the shape under test.
        sdk.create_operator("IMPL", pid, [support])
        proj.g.query(f"MATCH (n:Point {{id:$id}}) {setter}", params={"id": pid})
        pids[label] = pid

    for label, pid in pids.items():
        out = _decorate(proj, pid)
        assert pid in out, (label, out)
        assert out[pid], (
            f"{label}: the requested point came back with NO relationships — "
            "the scan could not read its own operator neighbourhood")

    # The all-readable control keeps its decoration through the same path, so
    # the three bad shapes are not passing because everything comes back empty.
    control = pids["all_numeric_control"]
    assert _decorate(proj, control)[control], (
        "the control lost its relationships — the scan is empty for every "
        "shape, so the three orderings above prove nothing")


def test_the_trends_answer_survives_a_numeric_string_operand(sdk):
    """Cycle 17 P2: `_format_timeline` raised on the raw operands.

    The `trends` template returns the RAW coalesced α/β so they can be shown,
    and `f"{value:.1f}"` raises on a stored string — which `analyze()`'s
    blanket handler renders as the ANSWER ("Formatting error: …") for a
    question the graph can still answer. `_format_uncertain` had this guard;
    `_format_timeline` had only the `conf` half of it.
    """
    from tortoise.analyze import _format_timeline

    # The formatter, pinned directly: the row shape the `trends` template
    # returns (id, content, conf, alpha, beta, createdAt) with the operands
    # stored as STRINGS, which is what `ep_width_cypher` admits.
    row = ("pid-1", "ZQTREND a widget claim", 0.7, "0.7", "0.3",
           "2026-11-11T00:00:00Z")
    text = _format_timeline([row])  # must not raise
    assert "Formatting error" not in text, text
    assert "α=0.7" in text and "β=0.3" in text, text

    # …and an unreadable operand degrades instead of raising.
    degraded = _format_timeline([("pid-2", "ZQ claim", 0.7, "abc", "0.3",
                                 "2026-11-11T00:00:00Z")])
    assert "abc" not in degraded, degraded
    assert "no readable operands" in degraded, degraded

    # Control: the all-numeric row still renders its operands, so the guard is
    # not simply hiding the column.
    control = _format_timeline([("pid-3", "ZQ claim", 0.5, 2.0, 2.0,
                                "2026-11-11T00:00:00Z")])
    assert "α=2.0" in control and "β=2.0" in control, control


def test_a_numeric_string_operand_is_measured_on_both_twins(sdk):
    """Cycle 16: the presence predicate compared RAW, the formula coerced.

    `update_point(posterior_alpha="0.7", posterior_beta="0.3")` was accepted
    and stored verbatim (JSON- and LLM-sourced values arrive like this) — since
    the cycle-20 write guard the shape is reachable only from a row written
    BEFORE it, or by raw Cypher, which is what this fixture does. The presence
    rule then evaluated `"0.7" >= 0`, which is NULL in Cypher, so the
    conjunction was NULL and the claim read `absent` — in the SAME query that
    computed `mean 0.7, variance 0.105` — while Python's `float()` said
    `measured`. One claim, two states, and a belief floor
    (`min_confidence=0.5`) dropped it. The twins now share ONE coercion
    (`numeric_operand_cypher` / `numeric_operand`).
    """
    from tortoise.analyze import analyze
    from tortoise.ranking import StateRanker
    from tortoise.search_engine import (
        belief_mean_cypher,
        ep_has_belief_cypher,
        ep_state_cypher,
        numeric_operand,
    )
    from tortoise.why import assemble_why_blocks

    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "a claim with string operands")["id"]
    proj.g.query('MATCH (n:Point {id:$id}) SET n.posterior_alpha = "0.7", '
                 'n.posterior_beta = "0.3", n.confidence = null',
                 params={"id": pid})

    row = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN " + ep_has_belief_cypher("n") + ", "
        + ep_state_cypher("n") + ", "
        + belief_mean_cypher("n.posterior_alpha", "n.posterior_beta")
        + ", coalesce(n.posterior_alpha, n.ep_alpha, 1.0)",
        params={"id": pid}).result_set[0]
    has_belief, state, mean, raw = row
    assert bool(has_belief) is True, (
        f"the presence predicate says absent for a numeric-STRING pair, while "
        f"the same query computed mean={mean!r} from it")
    assert state == EP_STATE_MEASURED, state
    assert float(mean) == pytest.approx(0.7, abs=1e-6), mean
    assert raw == "0.7", raw  # the operand really is a string

    # …and every Python surface agrees with it.
    assert numeric_operand("0.7") == pytest.approx(0.7)
    ann = annotate_ep_batch(proj.g, [pid])[pid]
    assert ann.ep_state == EP_STATE_MEASURED, ann
    assert ann.confidence_mean == pytest.approx(0.7, abs=1e-4), ann
    rec = {r["id"]: r["recall_ranking"] for r in
           StateRanker(projection=proj).rerank(
               [{"id": pid, "scores": {"rrf": 1.0}}])}[pid]
    assert rec["ep_state"] == EP_STATE_MEASURED, rec
    assert rec["confidence"] == pytest.approx(0.7, abs=1e-4), rec
    why_blocks = assemble_why_blocks(proj, [pid])
    assert why_blocks, "`why` returned nothing for a measurable claim"
    assert why_blocks[pid]["ep"]["confidence_mean"] == pytest.approx(
        0.7, abs=1e-4), why_blocks[pid]
    out = analyze("ZQSTRINGS", proj=proj, use_llm=False,
                  entity_subgraph_ids={pid})
    assert "Formatting error" not in str(out.get("answer") or ""), out


def test_one_malformed_mirror_does_not_discard_the_batch(sdk):
    """Cycle 16 P1: cycle 15 coerced the α/β columns and left the MIRROR raw.

    A LEGACY row stores `confidence="abc"` (the cycle-20 write guard refuses
    that at `update_point` now; this shape pre-dates it, or is written by raw
    Cypher, which is what this fixture does). `float(mirror)` then raised
    inside `annotate_ep_batch`, whose blanket `except` returned `{}` — so EVERY
    claim in the response read `ep_state: "absent"`, measured ones included, and
    `min_confidence=0.5` returned nothing at all. The same shape emptied `why`
    and killed the rankers' signal fetch, which left rows with no
    `recall_ranking` key at all. One bad row must not speak for the batch.
    """
    from tortoise.ranking import StateRanker
    from tortoise.search_engine import ep_state_cypher
    from tortoise.why import assemble_why_blocks

    proj = sdk._get_proj()
    bad = sdk.create_point("statement", "ZQFORMAT bad mirror claim")["id"]
    good = sdk.create_point("statement", "ZQFORMAT good measured claim")["id"]
    # …WITH a valid pair beside it (cycle 18): with the mirror alone the
    # presence predicate takes its `ELSE null` arm and the bad mirror is never
    # served, so the test could not fail. This is the shape that reaches the
    # value expression — the one GraphRanker got wrong for 17 cycles.
    proj.g.query('MATCH (n:Point {id:$id}) SET n.confidence = "abc", '
                 "n.posterior_alpha = 0.7, n.posterior_beta = 0.3",
                 params={"id": bad})
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
                 "n.posterior_beta = 2.0, n.confidence = 0.5",
                 params={"id": good})

    # The mirror is unreadable; the PAIR is what the state reports.
    assert proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN " + ep_state_cypher("n"),
        params={"id": bad}).result_set[0][0] == EP_STATE_MEASURED

    ann = annotate_ep_batch(proj.g, [bad, good])
    assert set(ann) == {bad, good}, (
        "the batch annotation dropped the WHOLE response because of one "
        f"malformed row: {sorted(ann)}")
    # The mirror is unreadable but the PAIR is not: the pair decides, exactly
    # as the Cypher `coalesce(<coerced mirror>, <mean>)` does.
    assert ann[bad].ep_state == EP_STATE_MEASURED, ann[bad]
    assert ann[bad].confidence_mean == pytest.approx(0.7, abs=1e-4), ann[bad]
    assert ann[good].ep_state == EP_STATE_MEASURED, ann[good]
    assert ann[good].confidence_mean == 0.5, ann[good]

    why_blocks = assemble_why_blocks(proj, [bad, good])
    assert why_blocks, "`why` returned nothing for the batch"

    recs = {r["id"]: r["recall_ranking"] for r in
            StateRanker(projection=proj).rerank(
                [{"id": bad, "scores": {"rrf": 1.0}},
                 {"id": good, "scores": {"rrf": 1.0}}])}
    assert set(recs) == {bad, good}, recs
    # Both rankers agree with annotate: the unreadable mirror is skipped and
    # the stored pair answers, so neither row loses its belief.
    assert recs[bad]["ep_state"] == EP_STATE_MEASURED, recs[bad]
    assert recs[bad]["confidence"] == pytest.approx(0.7, abs=1e-4), recs[bad]
    assert recs[good]["ep_state"] == EP_STATE_MEASURED, recs[good]
    assert recs[good]["confidence"] == 0.5, recs[good]


def test_a_belief_value_the_store_cannot_read_is_refused_at_the_write(sdk):
    """Cycle 20 P1: `_sanitize_props` guarded property NAMES, not value types.

    `update_point(posterior_alpha=[0.7])` and `update_point(confidence=True)`
    were ACCEPTED and stored verbatim, and FalkorDB then refuses to inspect
    them — `toString(<List>)` and `toFloat(<List>)` raise "Type mismatch", which
    takes the whole QUERY down. Measured consequences of ONE list-valued node:
    `tortoise_analyze` answered "Query error: …", `recall_subgraph` raised, and
    the belief signal map for the whole recall batch came back empty — so every
    result read `absent`, the exact conflation this issue removes, at batch
    scope. The belief READS were made total separately (the `*OrNull` coercion —
    see `test_a_legacy_list_operand_is_absent_without_blanking_the_batch`), so
    the remaining pre-guard rows degrade to `absent` instead of blanking a
    batch; the refusal stays HERE because a List/Boolean/string belief is a
    writer defect that no reader should have to absorb.
    """
    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "ZQWRITE guard probe")["id"]

    # Two refusal CLASSES, two owners, deliberately: a value that is not a number
    # at all is refused HERE (the engine cannot inspect it), while a number the
    # store cannot hold EXACTLY is refused by the #4647 domain guard with its own
    # reason. `inf`/`nan` land in the second — so their message is the domain's,
    # and its "store it as a string" advice does not apply to a belief column
    # (a string belief is refused here; see the next case).
    for prop, bad in (
        ("posterior_alpha", [0.7]),
        ("posterior_beta", {"a": 1}),
        ("confidence", True),
        ("confidence", "abc"),
        ("confidence", "0.7"),
        # Cycle 22 P3: `numbers.Number` admitted this, and the domain has no arm
        # for it, so the write was ACCEPTED and the store's degraded-value path
        # (#7369) wrote null — "succeeded" with nothing stored.
        ("confidence", 1j),
    ):
        with pytest.raises(ValueError) as exc:
            sdk.update_point(pid, **{prop: bad})
        assert "must be a number" in str(exc.value), (prop, bad, str(exc.value))
    for prop, bad in (
        ("posterior_alpha", float("inf")),
        ("posterior_alpha", float("nan")),
    ):
        with pytest.raises(ValueError) as exc:
            sdk.update_point(pid, **{prop: bad})
        assert "not a finite number" in str(exc.value), (prop, bad, str(exc.value))
        # Cycle 22 P3: the domain's remedy ("store it as a string") is refused
        # by the belief guard, so the message must say so — otherwise the caller
        # follows it and hits a second error.
        assert "does not apply" in str(exc.value), (prop, bad, str(exc.value))
        # …and nothing was written.
        stored = proj.g.query(
            f"MATCH (n:Point {{id:$id}}) RETURN n.{prop}",
            params={"id": pid}).result_set
        assert stored[0][0] is None, (prop, stored)

    # Cycle 21: a number the store holds EXACTLY is accepted, even when it is not
    # a builtin float — the representability question belongs to
    # `numeric_domain.numeric_alteration_reason`, which certifies both of these.
    # An inline `isinstance(int, float)` refused them and called them
    # "malformed", a regression against the pre-PR tree.
    from decimal import Decimal

    sdk.update_point(pid, confidence=Decimal("0.5"))
    assert annotate_ep_batch(proj.g, [pid])[pid].confidence_mean == pytest.approx(
        0.5, abs=1e-6)
    import numpy as np

    sdk.update_point(pid, confidence=np.float64(0.5))
    assert annotate_ep_batch(proj.g, [pid])[pid].confidence_mean == pytest.approx(
        0.5, abs=1e-6)
    # …and one it does NOT hold exactly is refused by the domain, with ITS
    # reason (a precision loss is not "malformed").
    with pytest.raises(ValueError) as exc:
        sdk.update_point(pid, confidence=np.float32(0.1))
    assert "exactly representable" in str(exc.value) or "alter" in str(
        exc.value), str(exc.value)

    # The legitimate writes still work, including clearing.
    sdk.update_point(pid, confidence=0.42)
    assert annotate_ep_batch(proj.g, [pid])[pid].confidence_mean == 0.42
    # Moving the PAIR clears the mirror (cycle 12's third-writer rule), so the
    # pair's mean is what the block reports next.
    sdk.update_point(pid, posterior_alpha=3.0, posterior_beta=1.0)
    assert annotate_ep_batch(proj.g, [pid])[pid].confidence_mean == pytest.approx(
        0.75, abs=1e-4)
    sdk.update_point(pid, confidence=None)
    assert annotate_ep_batch(proj.g, [pid])[pid].confidence_mean == pytest.approx(
        0.75, abs=1e-4)


def test_the_twins_parse_the_same_string_language(sdk):
    """Cycle 20 P1/P2: `float()` and `toFloat` are not the same parser.

    Measured divergences, each of which put a DIFFERENT belief on two surfaces
    for one claim:

    * ``toFloat("Infinity")`` is ``inf`` → `graph_ranking` read maximum
      certainty 1.0 for a claim with no readable belief (`0.0` on
      `recall_ranking`, `absent` in the `ep` block — three answers);
    * ``toFloat("0x1p4")`` is 16.0 while Python rejected hex floats (closed in
      cycle 26 — `float.fromhex` is the same parser for the same spelling, so
      both twins read it and this bullet is history, not a live divergence);
    * ``float("0.7 ")`` is 0.7 while ``toFloat("0.7 ")`` is null — so a value
      with a trailing space was `measured / 0.7` in the `ep` block and `absent`
      in every ranker beside it.

    The string spelling is now pinned to the intersection (a plain decimal
    literal) and the Cypher coercion additionally requires a FINITE result.

    Cycle 22 settled the other half of that intersection, by measurement rather
    than by reading `toFloat`'s name: it parses a string as a **float32**
    (`toFloat('0.7')` → `0.699999988079071`) and answers NULL outside the normal
    float32 range. So the RANGE is part of the pin too, and it is the half that
    was missing: `'1e309'` matched the grammar, Python returned `inf` (a
    maximum-certainty belief) while every Cypher surface said `absent`, and
    `'1e39'`/`'1e308'`/`'1e-40'` had the same split at magnitudes the table
    below now covers. A string outside the range is ABSENT on both twins.

    NO KNOWN RESIDUAL (cycles 26, 27, 28, 29 — and the qualifier is the point).
    Three rounds of "bounded divergence" turned out to be three splits in which
    one twin could not read what the other could, and each one moved `ep_state`
    somewhere; a fourth round then found two more inside the "closed" ones. So
    this table is a MEASUREMENT of the spellings pinned here, not a proof that
    no shape exists — every widening has to add its rows:

    * the value: `numeric_operand` used to return the DOUBLE (`0.7`) where the
      store serves the float32 (`0.699999988…`), so a `> 0.6` threshold could
      disagree between the twins — it now returns the store's float32;
    * the SPELLING: a hex float (`"0x1p4"` = 16.0 to `toFloat`) was refused here,
      so a legacy hex operand pair read `absent` in the `ep` block and
      `measured / 0.94` in the rankers and in `analyze` — `float.fromhex` is the
      same parser for the same spelling, so both twins read it;
    * the UNDERFLOW edge (cycle 27): the rule refused EVERY float32 subnormal,
      but `strtof` sets ERANGE only for an INEXACT one — an exact subnormal
      reads, so a hex-subnormal operand was `absent` here over a 23-exponent
      window the store served;
    * the ROUNDING: `strtof` rounds the DECIMAL to float32 directly, while
      rounding the double to float32 is a DOUBLE rounding that disagrees at a
      float32 midpoint (`"1.0000000596046448"` = 1 + 2⁻²⁴, up in the store,
      down in Python). `_f32_from_literal` resolves ONLY that case, from the
      literal, with exact `Fraction` arithmetic — for a HEX literal too, where
      `float.fromhex` keeps only 53 bits, and on the SIDE of the midpoint the
      literal actually lies (cycle 28);
    * the UNDERFLOW edge, second time (cycle 29): the inexactness `strtof` sets
      ERANGE on is a property of the TEXT, not of the double. A subnormal's
      `repr()` is EXACT as a double and INEXACT as text, so the guard
      `rounded != val` served every decimal spelling of every subnormal while
      the store nulled it — the same split, mirrored. And the store's read
      window does not end AT min-normal: it ends a quarter of a subnormal ULP
      below it, so both edges are pinned here.

    So the table below asserts agreement on presence AND on value; the rows are
    the shapes this repo has measured a split on.
    """
    from tortoise.search_engine import numeric_operand, numeric_operand_cypher

    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "ZQPARSE a string-operand claim")["id"]
    shapes = {
        "0.7": 0.7,
        # Leading whitespace is skipped by `toFloat`, trailing is not — measured
        # (`" 0.7"` → 0.7, `"0.7 "` → null). The pinned grammar follows.
        " 0.7": 0.7,
        "\t0.7": 0.7,
        "0.7 ": None,
        " 0.7 ": None,
        "Infinity": None,
        "-Infinity": None,
        "nan": None,
        "abc": None,
        "": None,
        "1e3": 1000.0,
        "+.5": 0.5,
        "0.": 0.0,
        # The store reads hex floats (`float.fromhex` is the same parser), so
        # Python must too — refusing them split presence between twins.
        "0x1p4": 16.0,
        "0X1P4": 16.0,
        "-0x1p4": -16.0,
        # A float32 MIDPOINT literal: `strtof` rounds the decimal up, and the
        # double rounds down to 1.0 — the band edge, where the flip changed
        # `ep_state`. `_f32_from_literal` decides it from the literal.
        "1.0000000596046448": 1.0000001192092896,
        # The RANGE half (`toFloat` is a float32 parser — measured). Every one of
        # these matched the pinned grammar, and every one of them was a twin
        # split before the range bound: the first two made Python return `inf`
        # (ranked first, `json.dumps(allow_nan=False)` → a 500 on the hosted
        # read), the rest made Cypher `absent` beside a Python number.
        "1e309": None,
        "-1e309": None,
        "1e308": None,
        "1e39": None,
        "1e-40": None,
        "3.4028236e38": None,          # just over float32 MAX → null
        "1.1754943e-38": None,         # below float32 MIN-NORMAL → null
        "3.4028235e38": 3.4028235e38,  # float32 MAX itself is readable
        "1.1754944e-38": 1.1754944e-38,  # min normal is readable
        "1e-37": 1e-37,
        # Cycle 22 P1: the store parses a string with `strtof`, so the window
        # is float32's NORMAL range and everything outside it answers null.
        # Before this, Python answered inf/0.0 for these and the `ep` block
        # reported `measured` beside `absent` in every ranker — and `inf` then
        # made the response body unserialisable (`json.dumps(allow_nan=False)`).
        "1e38": 1e38,
        "1.2e-38": 1.2e-38,
        "1e-38": None,
        "1e-320": None,
        "1e400": None,
        "-1e400": None,
        # Cycle 23 P3: `\s` is Unicode without `re.ASCII`, and `float()` skips a
        # NBSP while `strtof` does not — a value pasted out of a spreadsheet was
        # 0.7 to Python and null to the store.
        "\u00a00.7": None,
        "\u20070.7": None,
        "\u0667.7": None,
        "0e400": 0.0,
        "-0.0": -0.0,
        # The rounding edges the two rules decide DIFFERENTLY, and they are the
        # reason `_string_operand` packs to float32 rather than comparing to the
        # max outright: `'3.40282355e38'` is above float32 max but rounds DOWN
        # to it, and the store reads it — while `'1.1754943e-38'` is below
        # min-normal, rounds UP to it, and the store nulls it.
        "3.40282355e38": 3.40282355e38,
        # The OVERFLOW edge (cycle 30) — the mirror of the underflow window
        # below, and the half the rounding rule left to the DOUBLE. Measured on
        # 4.20.6: the store reads every literal strictly BELOW the float32
        # overflow midpoint (`2**128 - 2**103`, rounding it down to max) and
        # nulls one at or above it. `'3.4028235677973366e38'` is below the
        # midpoint as a LITERAL while its nearest DOUBLE is exactly on it, so
        # `struct.pack('<f', …)` raised OverflowError here and Python answered
        # `absent` for a value every Cypher surface served; `'0x1.ffffffp127'`
        # IS the exact midpoint and is null. BOTH edges are pinned — a rule that
        # refused the whole band would leave every null row green.
        "3.402823567797336e38": 3.4028234663852886e38,    # below mid → reads
        "3.4028235677973366e38": 3.4028234663852886e38,   # below mid (double ON it)
        "3.4028235677973367e38": None,                    # just above → null
        "-3.402823567797336e38": -3.4028234663852886e38,
        "-3.4028235677973366e38": -3.4028234663852886e38,
        "-3.4028235677973367e38": None,
        "0x1.fffffefffffffp127": 3.4028234663852886e38,   # 1 ULP below → max
        "0x1.ffffffp127": None,                           # the exact midpoint
        # The OVERFLOW edge must be decided in BOUNDED work, and the store makes
        # that cheap: `toFloatOrNull('1e10000000')` is null in ~1 ms. Python's
        # first attempt at this branch ran the exact literal parse on every
        # overflowing spelling, so this row cost 7.5 s (`10**10**7`) and grew
        # superlinearly — measured. The double's magnitude bounds the parse.
        "1e1000000": None,
        "1e10000000": None,
        "1e999999999": None,
        "-1e10000000": None,
        # The UNDERFLOW edge is INEXACTNESS, not subnormal-ness (cycle 27): the
        # rule refused every subnormal, so a hex literal naming a float32
        # subnormal EXACTLY — which `strtof` accepts — was `absent` here and
        # `measured / 5.87e-39` in every Cypher surface, and that number moved
        # the order. Both halves are pinned: the exact ones READ, the inexact
        # ones are null on both twins (see the table in `_string_operand`).
        "0x1p-149": float.fromhex("0x1p-149"),      # min subnormal, exact
        "0x1p-127": float.fromhex("0x1p-127"),      # subnormal, exact
        "0x1.fffffcp-127": float.fromhex("0x1.fffffcp-127"),  # max subnormal
        "0x1.8p-149": None,                        # not representable
        "0x1.fffffep-127": None,                   # rounds to min normal → inexact
        # ⛔ The inexactness is a property of the TEXT, not of the double
        # (cycle 29). A subnormal's `repr()` — and any hex literal carrying more
        # precision than `float.fromhex` keeps — is EXACT as a double and
        # INEXACT as text, so `rounded != val` served every one of these while
        # the store nulled them: the `ep` block read `measured / 5.9e-39` beside
        # `absent / null` in every ranker, one block apart in one response.
        "0x1.0000000000000001p-127": None,
        "0x1.ffffffffffffffffp-128": None,
        "5.877471754111438e-39": None,             # repr() of 0x1p-127
        "1.401298464324817e-45": None,             # repr() of 0x1p-149
        "1.1754942106924411e-38": None,            # repr() of 0x1.fffffcp-127
        # …and the read window does NOT end at min-normal: an inexact literal
        # within a quarter subnormal-ULP BELOW min-normal still rounds UP to it
        # and reads (measured at a quarter-ULP sweep and again at 2⁻¹⁶³). BOTH
        # edges are pinned: a rule that refused this band would be the same
        # split pointing the other way, and only the nulls would stay green if
        # it did.
        "0x1ffffffp-151": float.fromhex("0x1p-126"),   # min-normal − 2⁻¹⁵¹ → reads
        "0x1fffffep-151": None,                     # min-normal − 2⁻¹⁵⁰ → null
        # The MIDPOINT edge, for HEX (cycle 28). `float.fromhex` keeps 53 bits,
        # so a hex literal with more precision than that lands ON a float32
        # midpoint after the double rounding — and the tie-break must be taken
        # from the literal and from the SIDE it lies on, not by falling back to
        # the double's answer. `0x1.000001p0` is a TRUE tie (round-half-to-even
        # keeps 1.0); `0x1.0000010000000001p0` is that tie PLUS 2⁻⁶⁴, i.e. the
        # next float32 up — the band edge, where the old fallback said `1.0`
        # (present at maximum certainty) while the store said `absent`;
        # `0x1.000002ffffffffffp0` is just BELOW the next tie and rounds DOWN.
        "0x1.000001p0": 1.0,
        "0x1.0000010000000001p0": 1.0000001192092896,
        "0x1.000002ffffffffffp0": 1.0000001192092896,
        # …and the same SIDE bug on the DECIMAL path, where the double lands on
        # the midpoint from above and `_float32`'s tie-break picks the UPPER
        # candidate: the rule assumed `rounded` was always the lower one and
        # answered a ulp low.
        "1.0000001788139344": 1.000000238418579,
    }
    for raw, expected in shapes.items():
        proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = $a, "
                     "n.posterior_beta = '1'", params={"id": pid, "a": raw})
        cypher = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN "
            + numeric_operand_cypher("n.posterior_alpha"),
            params={"id": pid}).result_set[0][0]
        py = numeric_operand(raw)
        assert (py is None) == (cypher is None), (
            f"{raw!r}: python={py!r} cypher={cypher!r} — the twins disagree "
            "about whether this string is a number")
        if expected is None:
            assert py is None and cypher is None, (raw, py, cypher)
        else:
            # float32 equality on BOTH halves, not `approx`: the value under
            # test IS a float32, and a one-ULP difference is exactly the defect
            # (cycle 28's midpoint rows were GREEN against the defective code,
            # because `pytest.approx`'s default 1e-6 relative tolerance passes a
            # 1.19e-7 relative miss at 1.0 — a vacuous row).
            assert _store_f32(py) == _store_f32(expected), (raw, py, expected)
            assert _store_f32(cypher) == _store_f32(expected), (
                raw, cypher, expected)


def test_an_overflowing_literal_is_decided_without_an_exact_parse(monkeypatch):
    """Cycle 30: the overflow branch must not run the exact parse unbounded.

    `Fraction("1e10000000")` builds `10**10**7`. Measured: 7.5 s for that row,
    0.2 s for `1e1000000`, superlinear beyond — on literals the store answers
    `null` for in about a millisecond, and that the PRE-branch code answered
    `None` for in microseconds. A stored string operand is exactly the
    population `numeric_operand` exists to serve, so an unbounded parse here is
    a per-request stall on `recall_ranking` / `analyze` / `belief_mirror`.

    A timing assertion would be flaky, so this pins the MECHANISM: for a literal
    whose double is above `2**128` the exact parse is never entered. (The exact
    parse is still what decides the real band — `0.0`-valued spellings and the
    in-band decimals — which the twin-grammar table pins by value.)
    """
    import tortoise.search_engine as se

    parsed: list[str] = []

    def _forbidden(raw):
        parsed.append(raw)
        raise AssertionError(
            "the exact-literal parse ran for a literal far outside the overflow "
            "band — that is the unbounded-cost path this test exists to prevent")

    monkeypatch.setattr(se, "_exact_literal_fraction", _forbidden)
    for literal in ("1e10000000", "1e999999999", "-1e10000000", "1e309", "1e39"):
        assert se.numeric_operand(literal) is None, literal
    assert parsed == [], parsed


def test_a_half_pair_reports_measured_but_absent_by_design(sdk):
    """The documented divergence: `measured` answers "is an α column persisted",
    `ep_state` answers "is a belief readable" (#11702 cycle 30).

    A HALF pair (`posterior_alpha` with no beta — accepted by `update_point`) and
    a `(0, 0)` pair both carry a persisted α while no belief is readable: 0.5
    against nothing has no mean, and 0/0 has none either. The state fold names
    both `absent` and the mean is `None`; `measured`/`has_ep` stay True because
    they answer the OTHER question.

    ⛔ **`measured` is NOT an EP receipt.** The rows below are written by a raw
    `SET`, i.e. by a direct write with no EP pass anywhere — and they come back
    `measured=True`. That is the point: the predicate is
    `posterior_alpha IS NOT NULL OR (ep_alpha IS NOT NULL AND NOT
    baseline_set)`, which asks whether an α is PERSISTED, not who wrote it. So a
    caller that needs an EP measurement still has to ask for one; it must never
    read this flag as proof one happened. (An earlier round of this docstring
    said "did EP run" and "deciding whether to RE-RUN EP reads `measured`" — a
    rule that would skip the one action able to produce a belief on exactly
    these rows. Corrected here so the pin cannot teach it back.)

    This is pinned rather than fixed so neither can be silently repurposed: a
    consumer deciding whether a stored posterior can be reasoned from reads
    `measured`; one deciding whether to show or order a belief reads
    `ep_state`/`confidence_mean`. Collapsing them would either claim a belief
    that does not exist or make EP re-run over a measurement it already holds.
    """
    from tortoise.why import assemble_why_blocks

    proj = sdk._get_proj()
    for label, assignment in (
            ("half pair", "n.posterior_alpha = 0.5"),
            ("zero pair", "n.posterior_alpha = 0.0, n.posterior_beta = 0.0")):
        pid = sdk.create_point("statement", f"ZQMEAS {label}")["id"]
        proj.g.query(f"MATCH (n:Point {{id:$id}}) SET {assignment}",
                     params={"id": pid})

        ep = annotate_ep_batch(proj.g, [pid])[pid]
        why_ep = assemble_why_blocks(proj, [pid])[pid].get("ep") or {}
        for surface, state, mean, measured in (
                ("ep", ep.ep_state, ep.confidence_mean, ep.measured),
                ("why", why_ep.get("ep_state"), why_ep.get("confidence_mean"),
                 why_ep.get("measured"))):
            assert state == EP_STATE_ABSENT, (label, surface, state)
            assert mean is None, (label, surface, mean)
            assert measured is True, (
                f"{label}: {surface} dropped the documented `measured` meaning — "
                "an α column IS persisted on this row (by a direct write, no EP "
                "pass); only the belief is unreadable")


def test_a_legacy_list_operand_is_absent_without_blanking_the_batch(sdk):
    """Cycle 22: the coercion is TOTAL — `toFloat`/`toString` are the RAISERS.

    `toString(<List>)` and `toFloat(<List>)` answer "Type mismatch: expected
    String, Integer, Float, or Null but was List" and take the whole QUERY
    down. The cycle-20 write guard refuses a list belief at `_sanitize_props`,
    so the remaining population is pre-guard rows and raw-Cypher writes — but
    the whole reason the readers are kept total is that population. Measured at
    `be229efdb7df`: one node storing `[0.7]` made `_fetch_point_signals` raise
    and `_fetch_signals` return None, so a healthy `(3, 1)` claim BESIDE it read
    `ep_state: absent, confidence: null` on `recall_ranking` — the batch-scope
    conflation #11702 removes, reached through the presence predicate's own
    coercion. `toFloatOrNull`/`toStringOrNull` answer null for the types the
    plain spellings refuse (and are identical for every type they accept), so
    the list reads ABSENT and its neighbours keep their belief.
    """
    from tortoise.ranking import StateRanker
    from tortoise.search_engine import (
        annotate_ep_batch,
        ep_state_cypher,
        numeric_operand,
        numeric_operand_cypher,
    )

    proj = sdk._get_proj()
    bad = sdk.create_point("statement", "ZQLIST legacy list operand")["id"]
    good = sdk.create_point("statement", "ZQLIST healthy neighbour")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = [0.7], "
                 "n.posterior_beta = 0.3", params={"id": bad})
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 3.0, "
                 "n.posterior_beta = 1.0", params={"id": good})

    # The coercion answers NULL rather than raising. (It used to raise here —
    # the assertion is what a Cypher exception cannot satisfy.)
    assert numeric_operand([0.7]) is None
    row = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN "
        + numeric_operand_cypher("n.posterior_alpha") + ", "
        + ep_state_cypher("n"), params={"id": bad}).result_set[0]
    assert row[0] is None, row
    assert row[1] == EP_STATE_ABSENT, row

    ann = annotate_ep_batch(proj.g, [bad, good])
    assert ann[bad].ep_state == EP_STATE_ABSENT, ann[bad]
    assert ann[bad].confidence_mean is None, ann[bad]
    assert ann[good].ep_state == EP_STATE_MEASURED, ann[good]

    # …and the batch-wide blanking is closed on the ranker too.
    recs = {r["id"]: r["recall_ranking"] for r in
            StateRanker(projection=proj).rerank(
                [{"id": bad, "scores": {"rrf": 1.0}},
                 {"id": good, "scores": {"rrf": 1.0}}])}
    assert set(recs) == {bad, good}, recs
    assert recs[bad]["ep_state"] == EP_STATE_ABSENT, recs[bad]
    assert recs[bad]["confidence"] is None, recs[bad]
    assert recs[good]["ep_state"] == EP_STATE_MEASURED, recs[good]
    assert recs[good]["confidence"] == pytest.approx(0.75, abs=1e-4), recs[good]


def test_the_third_ranker_reads_an_unreadable_mirror_like_the_other_two(sdk):
    """Cycle 18 P1: `GraphRanker` was the ONE read that passed the mirror raw.

    Cycles 16 and 17 replaced the raw mirror at `annotate_ep_batch`, `why`,
    `StateRanker`, `GapsRanker`, `SubgraphExpander` and the `analyze` templates
    and missed `_fetch_point_signals`, which still did `float(raw_conf)`. Two
    measured consequences, both on shapes this PR declares must read `absent`:

    * a stored BOOLEAN mirror reached `float(True) == 1.0`, so a claim whose
      pair says 0.7 ranked FIRST at confidence 1.0 (`final_score` 0.575 vs
      0.5225) while every other surface answered 0.7;
    * a non-numeric mirror raised out of the batch, and `_fetch_signals`
      returns None on a raise — so EVERY id in the response lost its
      confidence/variance/contested state.

    The earlier fixtures could not catch either: they set the mirror with NO
    pair, so `ep_has_belief_cypher` took its `ELSE null` arm and the unreadable
    mirror was never served. The pair beside the bad mirror is the shape that
    reaches the value expression, so it is the shape pinned here.
    """
    from tortoise.ranking import GraphRanker

    proj = sdk._get_proj()
    pids = {}
    for label, setter in {
        "bool_mirror_pair": ("SET n.confidence = true, "
                             "n.posterior_alpha = 0.7, n.posterior_beta = 0.3"),
        "bad_mirror_pair": ('SET n.confidence = "abc", '
                            "n.posterior_alpha = 0.7, n.posterior_beta = 0.3"),
        "clean_pair": ("SET n.posterior_alpha = 0.7, n.posterior_beta = 0.3"),
        "measured_half": ("SET n.posterior_alpha = 1.0, n.posterior_beta = 1.0"),
    }.items():
        pid = sdk.create_point("statement", f"ZQGRAPH {label}")["id"]
        proj.g.query(f"MATCH (n:Point {{id:$id}}) {setter}", params={"id": pid})
        pids[label] = pid

    signals = GraphRanker(projection=proj)._fetch_point_signals(
        list(pids.values()))
    assert signals, (
        "the signal map is empty — ONE unreadable mirror dropped the whole "
        "batch (the class cycle 16 closed in `annotate_ep_batch`)")
    for label, pid in pids.items():
        assert pid in signals, (label, sorted(signals))

    # The pair decides, on all three: an unreadable mirror is not a certainty.
    for label in ("bool_mirror_pair", "bad_mirror_pair", "clean_pair"):
        got = signals[pids[label]]["confidence"]
        assert got == pytest.approx(0.7, abs=1e-6), (
            f"{label}: GraphRanker reads {got!r} where annotate/StateRanker/"
            "GapsRanker read 0.7 for the same claim")
    assert signals[pids["measured_half"]]["confidence"] == pytest.approx(0.5)

    # …and the ranking ORDER follows the belief, not the unreadable mirror.
    rows = GraphRanker(projection=proj).rerank(
        [{"id": pid, "scores": {"rrf": 0.5}} for pid in pids.values()])
    by_id = {r["id"]: r for r in rows}
    assert by_id[pids["bool_mirror_pair"]]["graph_ranking"][
        "confidence"] == pytest.approx(0.7, abs=1e-6), by_id[pids["bool_mirror_pair"]]
    assert (by_id[pids["bool_mirror_pair"]]["graph_ranking"]["final_score"]
            == pytest.approx(
                by_id[pids["clean_pair"]]["graph_ranking"]["final_score"],
                abs=1e-9)), (
        "the Boolean mirror still outranks the identical clean claim — "
        "`float(True) == 1.0` is back")


def test_a_boolean_is_not_a_belief_on_either_twin(sdk):
    """Cycle 16: cycle 12 read `confidence=True` as 1.0. Cypher cannot.

    Measured: `toFloat(<Boolean>)` RAISES ("Type mismatch: expected String,
    Integer, Float, or Null but was Boolean") and every comparison against a
    stored Boolean answers NULL (`true >= 0` is null), so no Cypher surface can
    read a bool as a number — cycle 12's "symmetry with the rest of the tree"
    was therefore one-sided: Python said `measured / 1.0` where Cypher said
    `absent`. A Boolean belief is a writer defect; it is ABSENT on both.
    """
    from tortoise.search_engine import (
        ep_state,
        ep_state_cypher,
        numeric_operand,
        numeric_operand_cypher,
    )

    assert numeric_operand(True) is None
    assert numeric_operand(False) is None
    assert ep_state(posterior_alpha=True, ep_alpha=None,
                    posterior_beta=2.0) == EP_STATE_ABSENT

    # …and a Boolean CAN still be in the store: cycle 20 refuses it at the write
    # (`_sanitize_props`), but data written before that (or by a raw tool) is
    # still there, so the reader must be TOTAL for it. Pinned through the raw
    # door deliberately — the API now raises (see the next test).
    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "a claim with a boolean mirror")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.confidence = true",
                 params={"id": pid})
    stored = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN toString(n.confidence)",
        params={"id": pid}).result_set
    assert stored[0][0] == "true", stored
    state = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN " + ep_state_cypher("n"),
        params={"id": pid}).result_set[0][0]
    assert state == EP_STATE_ABSENT, state
    # …and the coercion itself does not RAISE on a Boolean, which the bare
    # `toFloat` does — that would take the whole query down.
    v = proj.g.query("MATCH (n:Point {id:$id}) RETURN "
                     + numeric_operand_cypher("n.confidence"),
                     params={"id": pid}).result_set[0][0]
    assert v is None, v
    # Nothing readable is left, so the state is ABSENT on the Python twin too —
    # a `baseline_set` FLAG does not turn an unreadable value into a prior (see
    # `ep_state`; before cycle 16 it did, and reported `baseline / 0.5` where
    # Cypher said `absent / null`).
    ann = annotate_ep_batch(proj.g, [pid])[pid]
    assert ann.ep_state == EP_STATE_ABSENT, ann
    assert ann.confidence_mean is None, ann


def test_a_non_numeric_operand_is_absent_on_every_surface(sdk):
    """Cycle 15: `ep_state_cypher`'s `NOT <predicate>` fell through to `measured`.

    An UNKNOWN predicate is NULL, `NOT NULL` is NULL, and a NULL CASE arm falls
    through to the NEXT arm — so a stored non-numeric α (reachable through
    `update_point(posterior_alpha="abc")`, which `reject_belief_props` does not
    cover) was reported as the STRONGEST state, `measured`, with
    `confidence: null` beside it. The Python twin RAISED instead, which
    discarded `annotate_ep_batch`, `why` and the rankers' signal fetch for the
    whole batch, and `belief_value` answered `None` — three answers for one
    claim, on the least-known input.
    """
    from tortoise.search_engine import ep_state, ep_state_cypher
    from tortoise.why import assemble_why_blocks

    proj = sdk._get_proj()
    pid = sdk.create_point("statement", "a claim with a string operand")["id"]
    proj.g.query('MATCH (n:Point {id:$id}) SET n.posterior_alpha = "abc", '
                 "n.posterior_beta = 1.0, n.confidence = null",
                 params={"id": pid})
    stored = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.posterior_alpha",
        params={"id": pid}).result_set
    assert stored[0][0] == "abc", stored

    # The Python twin is TOTAL and says absent — the cycle-11 decision for
    # `belief_value`, which this helper was the last hold-out against.
    assert ep_state(posterior_alpha="abc", ep_alpha=None,
                    posterior_beta=1.0) == EP_STATE_ABSENT
    assert _se.belief_value(posterior_alpha="abc", posterior_beta=1.0) is None

    # …and the Cypher twin now says the same, instead of `measured`.
    cypher_state = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN " + ep_state_cypher("n"),
        params={"id": pid}).result_set[0][0]
    assert cypher_state == EP_STATE_ABSENT, cypher_state

    # Every surface agrees: no belief, absent state, no exception swallowing
    # the batch.
    ann = annotate_ep_batch(proj.g, [pid])
    assert ann[pid].confidence_mean is None, ann[pid]
    assert ann[pid].ep_state == EP_STATE_ABSENT, ann[pid]
    rec = {r["id"]: r["recall_ranking"] for r in
           StateRanker(projection=proj).rerank(
               [{"id": pid, "scores": {"rrf": 1.0}}])}[pid]
    assert rec["confidence"] is None and rec["ep_state"] == EP_STATE_ABSENT, rec
    sub = SubgraphExpander(projection=proj).expand([pid], depth=1)
    nodes = sub.get("nodes") or (sub if isinstance(sub, list) else [])
    node = next(n for n in nodes if n.get("id") == pid)
    assert node["ep_state"] == EP_STATE_ABSENT, node
    assert node.get("confidence") is None, node
    # Cycle 16: this used to be `if why_block:` — and `why` returned `{}` for
    # the whole batch because IT still called a bare `float(mirror)`, so the
    # assertion never ran. A guard whose subject is the failure case is not a
    # guard: it asserts nothing exactly when the code is broken. `why` is now
    # total, so the block MUST exist, and its `ep` block must say absent.
    why_blocks = assemble_why_blocks(proj, [pid])
    assert why_blocks, (
        "`why` returned nothing for the batch — one malformed mirror discards "
        "the enrichment for every id in the response")
    assert why_blocks[pid].get("ep"), why_blocks[pid]
    assert why_blocks[pid]["ep"]["ep_state"] == EP_STATE_ABSENT, why_blocks[pid]["ep"]
    assert why_blocks[pid]["ep"]["confidence_mean"] is None, why_blocks[pid]["ep"]


def test_the_artifact_reader_agrees_with_the_shared_reader_where_both_are_total():
    """Cycle 13 (P3 follow-up): the seventh surface duplicates `belief_value`.

    `tools/longmem_eval/ep_activation.read_confidence` is the ONE read behind
    the rendered artefact, and it re-implements mirror-first precedence,
    non-negativity and the positive-sum rule instead of importing them. It
    documents WHY it stays separate (the census needs a non-finite posterior to
    arrive as `nan`, not `None` — #10210 tracks that cell), which is a real
    reason for the totality to differ — but the VALUES must not.

    This is the assertion that keeps the duplication honest: on every shape
    where both are total, they answer the same number (or both nothing).
    """
    from tools.longmem_eval.ep_activation import read_confidence

    shapes = {
        "absent": {},
        "mirror_only": {"confidence": 0.95},
        "posterior_pair": {"posterior_alpha": 3.0, "posterior_beta": 1.0},
        "legacy_prior": {"ep_alpha": 2.0, "ep_beta": 2.0},
        "posterior_wins": {"posterior_alpha": 1.0, "posterior_beta": 9.0,
                           "ep_alpha": 5.0, "ep_beta": 1.0},
        "half_alpha": {"posterior_alpha": 0.5},
        "half_beta": {"posterior_beta": 0.5},
        "zero_total": {"posterior_alpha": 0.0, "posterior_beta": 0.0},
        "negative": {"posterior_alpha": 3.0, "posterior_beta": -1.0},
        "mirror_leads": {"confidence": 0.2, "posterior_alpha": 9.0,
                         "posterior_beta": 1.0},
        # Cycle 18: the shapes where the two DID disagree, because a Boolean is
        # a number to `float()` and not to the shared reader. `subgraph_render`
        # printed `confidence: 1.00` for a claim every product surface reported
        # `absent`. A `mirror_leads` pair beside it is included so the mirror
        # branch is exercised on a row that is READABLE by the pair too.
        "boolean_mirror": {"confidence": True},
        "boolean_mirror_false": {"confidence": False},
        "boolean_mirror_with_pair": {"confidence": True,
                                     "posterior_alpha": 0.7,
                                     "posterior_beta": 0.3},
        "bad_mirror_with_pair": {"confidence": "abc",
                                 "posterior_alpha": 0.7,
                                 "posterior_beta": 0.3},
        "string_operands": {"posterior_alpha": "0.7", "posterior_beta": "0.3"},
    }
    disagreements = []
    for name, props in shapes.items():
        mine = _se.belief_value(
            confidence=props.get("confidence"),
            posterior_alpha=props.get("posterior_alpha"),
            posterior_beta=props.get("posterior_beta"),
            ep_alpha=props.get("ep_alpha"), ep_beta=props.get("ep_beta"))
        theirs = read_confidence(_Props(props))
        if (mine is None) != (theirs is None) or (
                mine is not None and abs(mine - theirs) > 1e-9):
            disagreements.append((name, mine, theirs))
    assert not disagreements, (
        "the artifact reader and the shared reader disagree — one of them is "
        "a second vocabulary: " + repr(disagreements))


class _Props(dict):
    """A property dict that also answers ``get``/attribute access the way the
    reader calls it (``p.get("posterior_alpha")``)."""


def test_a_variance_scan_never_invents_a_width_for_an_absent_belief(sdk):
    """Cycle 13: `get_contested_claims` admitted shapes that cannot have a width.

    `ep_pair_stored_cypher` alone is HALF the rule. The scan coalesced the
    operands to `1.0` "so a variance can be computed at all", and that default
    fabricated a width for every shape the presence rule calls absent:

        α-only          → β invented as 1.0 → variance 0.0889 → CONTESTED
        mirror-only     → both invented      → the Beta(1,1) MAXIMUM 1/12
        (3, -1)         → a NEGATIVE "variance" every threshold reads as certain
        (0, 0)          → 0/0

    Measured on the reviewed head: the α-only claim was listed as contested
    while `annotate_ep_batch`, all three rankers and `why` answered
    `absent / variance 0.0 / contested False` for the same node — one claim,
    two answers, which is the defect class this issue exists to remove. The
    scan now gates on `ep_width_cypher`, the composite (pair stored, both
    non-negative, positive total) the `analyze` templates and the PR prune use.
    """
    from tortoise.ep import TortoiseEP
    from tortoise.ranking import GapsRanker
    from tortoise.search_engine import annotate_ep_batch

    proj = sdk._get_proj()

    def _point(content, sql=None):
        pid = sdk.create_point("statement", content)["id"]
        if sql:
            proj.g.query(f"MATCH (n:Point {{id:$id}}) SET {sql}",
                         params={"id": pid})
        return pid

    alpha_only = _point("an alpha-only claim", "n.posterior_alpha = 0.5")
    mirror_only = _point("a mirror-only claim", "n.confidence = 0.95")
    negative = _point("a negative beta", "n.posterior_alpha = 3.0, "
                      "n.posterior_beta = -1.0")
    zero_total = _point("a degenerate pair", "n.posterior_alpha = 0.0, "
                        "n.posterior_beta = 0.0")
    really_wide = _point("a genuinely wide claim", "n.posterior_alpha = 1.0, "
                         "n.posterior_beta = 1.0")
    shapes = [alpha_only, mirror_only, negative, zero_total, really_wide]

    ep_view = TortoiseEP(projection=proj)
    listed = {c["id"] for c in ep_view.get_contested_claims()}
    for pid in (alpha_only, mirror_only, negative, zero_total):
        assert pid not in listed, (
            "the variance scan invented a width for a claim that cannot have "
            f"one — {pid} is listed as contested")
    assert really_wide in listed, (
        "the gate must not drop the real shape: a stored Beta(1,1) IS the "
        "maximum variance (1/12) and is legitimately contested")

    # …but the #169 arm is NOT narrowed: a claim with no persisted belief at
    # all still lists at the zero-information maximum, because for THAT claim
    # "maximally uncertain" is the truth and the pin exists for it.
    untracked = sdk.create_point("statement", "no belief at all zzq")["id"]
    listed_again = {c["id"] for c in ep_view.get_contested_claims()}
    assert untracked in listed_again, (
        "the #169 deliberate listing (an unmeasured claim IS the least certain "
        "thing in the graph) was narrowed away — that is a recorded decision, "
        "not part of this defect")

    # …and the scan now agrees with the surfaces that read the same claim.
    ann = annotate_ep_batch(proj.g, shapes)
    gaps = {r["id"]: r["gaps_ranking"] for r in GapsRanker(projection=proj).rerank(
        [{"id": p, "scores": {"rrf": 1.0}} for p in shapes])}
    for pid in (alpha_only, negative, zero_total):
        assert ann[pid].ep_state == EP_STATE_ABSENT, (pid, ann[pid])
    # A MIRROR-only claim is the one shape of the four that is NOT absent: a
    # served belief with no stored operands. It has no WIDTH (nothing to
    # compute a variance from), so it must not be listed as contested either —
    # that listing is the Beta(1,1) maximum the coalesce default invents.
    assert ann[mirror_only].ep_state == EP_STATE_BASELINE, ann[mirror_only]
    assert ann[mirror_only].confidence_mean == pytest.approx(0.95, abs=1e-9)
    for pid in (alpha_only, mirror_only, negative, zero_total):
        assert ann[pid].variance == 0.0, (pid, ann[pid])
        assert ann[pid].contested is False, (pid, ann[pid])
        assert gaps[pid]["contested"] is False, (pid, gaps[pid])


def test_an_overflowing_pair_is_not_maximum_disbelief(sdk):
    """Cycle 13: `belief_value` disagreed with `_beta_mean` on an overflow pair.

    `update_point(posterior_alpha=1e308, posterior_beta=1e308)` is a shape the
    public write accepts. `1e308 + 1e308` overflows to `inf`, so every reader
    that divided directly computed `1e308/inf == 0.0` — MAXIMUM disbelief for a
    perfectly symmetric claim — while `_beta_mean` (behind the `ep` block)
    answered 0.5 for the same node: the two halves of one response disagreeing,
    on an input the rankers then ORDER on (`base = rel × conf**exp`).

    Both now answer the true mean, and so do the Cypher twins every ranker uses
    (`belief_mean_cypher`, which falls back to the overflow-free identity
    `1/(1 + β/α)`).
    """
    from tortoise.search_engine import _beta_mean, belief_value

    proj = sdk._get_proj()
    symmetric = sdk.create_point("statement", "an overflowing symmetric pair")["id"]
    biased = sdk.create_point("statement", "an overflowing skewed pair")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 1e308, "
                 "n.posterior_beta = 1e308, n.confidence = null",
                 params={"id": symmetric})
    # Python's own `1e308 + 1e307` overflows to `inf` too, so the expected
    # value must be computed the same overflow-free way the readers do.
    A, B = 1.7e308, 1e307
    true_biased_mean = 1.0 / (1.0 + B / A)
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = $a, "
                 "n.posterior_beta = $b, n.confidence = null",
                 params={"id": biased, "a": A, "b": B})

    # The Python reader and the mean owner must agree, and be RIGHT.
    assert belief_value(posterior_alpha=1e308, posterior_beta=1e308) == \
        pytest.approx(0.5, abs=1e-9)
    assert _beta_mean(A, B) == pytest.approx(true_biased_mean, abs=1e-9)
    assert belief_value(posterior_alpha=A, posterior_beta=B) == \
        pytest.approx(true_biased_mean, abs=1e-9)

    # The Cypher expression must answer the same value the Python owner does.
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, coalesce(n.posterior_alpha, 1.0) AS a, "
        "       coalesce(n.posterior_beta, 1.0) AS b, "
        "       " + _se.belief_mean_cypher(
            "coalesce(n.posterior_alpha, 1.0)",
            "coalesce(n.posterior_beta, 1.0)") + " AS m",
        params={"ids": [symmetric, biased]}).result_set
    by_id = {r[0]: (r[1], r[2], r[3]) for r in rows}
    assert by_id[symmetric][2] == pytest.approx(0.5, abs=1e-9), by_id
    assert by_id[biased][2] == pytest.approx(true_biased_mean, abs=1e-9), by_id
    # Never 0.0 (maximum disbelief) and never NaN on the wire.
    for pid, (_a, _b, m) in by_id.items():
        assert m is not None and m > 0.0, (pid, m)
        json.dumps({"m": m}, allow_nan=False)  # raises on NaN/inf


def test_why_width_is_guarded_for_hostile_pairs_like_every_other_surface(sdk):
    """`why` carried its OWN `_beta_variance`/`_mean` — a second spelling.

    Cycle 11 hardened `search_engine._beta_variance` and left the local copy
    untouched, so the CANONICAL block (`/v1/context`, the volunteer reflex,
    webhooks) was the one surface still emitting, for a pair the public
    `update_point` accepts:

        (3, -1)        variance -0.25   (a "width" below zero)
        (1e200, 1e200) variance NaN     (Starlette renders with
                                         allow_nan=False → a 500 on the hosted
                                         path; why_snapshot persists a bare
                                         `NaN` token)
        (1e308, 1e308) confidence 0.0   (maximum disbelief)
        (1e-200, 1e-200) ZeroDivisionError → the WHOLE batch's enrichment is
                                         discarded (#8504's failure shape)

    Both names now come from `search_engine`, so the guard cannot drift again.
    """
    from tortoise.search_engine import _beta_mean, _beta_variance
    from tortoise.why import _assemble_ep_rows
    from tortoise.why import _beta_variance as why_var

    assert why_var is _beta_variance, (
        "why re-declares its own `_beta_variance` — a second spelling of the "
        "arithmetic the shared reader owns")

    # The arithmetic itself, over the hostile shapes.
    for alpha, beta in ((-1.0, 3.0), (3.0, -1.0), (float("nan"), 1.0),
                        (float("inf"), float("inf")), (1e200, 1e200),
                        (1e-200, 1e-200), (0.0, 0.0)):
        var = _beta_variance(alpha, beta)
        assert var == 0.0, (
            f"_beta_variance({alpha!r}, {beta!r}) = {var!r} — not a width")
    assert _beta_mean(1e308, 1e308) == pytest.approx(0.5, abs=1e-9), (
        "an overflowing pair must not report maximum disbelief")
    assert _beta_mean(1e-200, 1e-200) == pytest.approx(0.5, abs=1e-9)

    # …and through the block assembly, on a real graph.
    proj = sdk._get_proj()
    ids = {}
    for label, (a, b) in (("neg", (3.0, -1.0)), ("big", (1e200, 1e200)),
                          ("huge", (1e308, 1e308)),
                          ("tiny", (1e-200, 1e-200))):
        pid = sdk.create_point("statement", f"{label} pair claim")["id"]
        proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = $a, "
                     "n.posterior_beta = $b", params={"id": pid, "a": a, "b": b})
        ids[label] = pid
    blocks = {pid: {} for pid in ids.values()}
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, "
        "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "  coalesce(n.posterior_beta, n.ep_beta, 1.0), "
        "  n.ep_alpha IS NOT NULL AS measured, coalesce(n.status,'') AS st, "
        "  coalesce(n.outdated, false) AS od, false AS baseline_set, "
        "  true AS has_belief, n.posterior_alpha AS pa, n.ep_alpha AS ea, "
        "  n.confidence AS conf, n.posterior_beta AS pb, n.ep_beta AS eb",
        params={"ids": list(ids.values())}).result_set
    _assemble_ep_rows(rows, blocks)
    import math as _math
    for label, pid in ids.items():
        ep = blocks[pid]["ep"]
        assert ep["variance"] is not None, (label, ep)
        assert _math.isfinite(ep["variance"]), (
            f"{label}: the why block put a non-finite width on the wire — "
            f"Starlette renders with allow_nan=False: {ep}")
        assert ep["variance"] >= 0.0, (
            f"{label}: a negative width reached the canonical block: {ep}")
        assert ep["contested"] is False, (label, ep)
        if ep["confidence_mean"] is not None:
            assert 0.0 <= ep["confidence_mean"] <= 1.0, (
                f"{label}: out-of-range belief in the canonical block: {ep}")


# ── The volunteer reflex: a belief-less claim never fires (#11702) ──────────
#
# `config/ci-surfaces.yml` advertises THIS file as pinning "`volunteer.py`'s
# band/sort". It did not: `git grep volunteer` returned prose only, never an
# assertion — mutation-verified by the reviewing lane, which reverted
# `tortoise/volunteer.py` to `origin/main` and watched
# `test_volunteer_pipeline.py test_volunteer_contract.py
# test_w4_why_enrichment.py` report 70 passed. The two rows below are that pin.

def _volunteer_window() -> list[dict]:
    return [{"role": "user",
             "content": "What did we decide about the zq ledger rollout?"}]


def test_the_volunteer_reflex_never_fires_on_a_claim_with_no_belief(sdk):
    """`_eligible` used to substitute the neutral 0.5 for an absent mean.

    So a claim with NO persisted belief cleared ANY floor at or below 0.5 —
    "we never measured this" was surfaced as neutral evidence. The shipped 0.7
    default hid it (0.5 < 0.7), which is why no volunteer test caught it: every
    floor in the suite is 0.9/1.0/1.5, where the pre- and post-fix answers are
    identical. A floor in (0, 0.5] is what distinguishes the branch.

    MUTANT THAT REDS THIS: `mean = float(raw_mean) if raw_mean is not None else
    None` → `else NEUTRAL_CONFIDENCE` in `tortoise/volunteer.py::_eligible`.

    A MEASURED claim rides the same window/stub as the CONTROL: without it this
    test would pass just as happily if the resolve leg returned nothing at all.
    """
    from tortoise.volunteer import _band, run_volunteer_pipeline, why_entry

    unmeasured = sdk.create_point(
        "statement", "zq ledger rollout owners assigned")["id"]
    measured = sdk.create_point(
        "statement", "zq ledger rollout shipped to staging")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 12.0, "
        "n.posterior_beta = 1.0", params={"id": measured})

    def stub_search(query, **kwargs):
        hits = [
            {"id": unmeasured,
             "content": "zq ledger rollout owners assigned",
             "point_kind": "statement"},
            {"id": measured,
             "content": "zq ledger rollout shipped to staging",
             "point_kind": "statement"},
        ]
        return hits[: kwargs.get("limit", 10)]

    proj = sdk._get_proj()
    for floor in (0.0, 0.1, 0.3, 0.5):
        r = run_volunteer_pipeline(
            proj, _volunteer_window(), min_confidence=floor, why=True,
            _search_fn=stub_search)
        assert r["degraded_reason"] is None, (floor, r["degraded_reason"])
        surfaced = [p["id"] for p in r["pointers"]]
        assert unmeasured not in surfaced, (
            f"the reflex fired on a never-measured claim at floor {floor} — "
            f"the neutral-0.5 substitution is back: {surfaced}")
        assert measured in surfaced, (
            f"the CONTROL claim (α=12, β=1 ≈ 0.92) did not surface at floor "
            f"{floor}, so the assertion above could not fail: {surfaced}")

    # The canonical block's DEFAULT for a point with no `ep` sub-block is the
    # absent state, not the neutral 0.5 — and the band names it.
    entry = why_entry({"point_id": unmeasured})
    assert entry["ep"]["confidence_mean"] is None, entry["ep"]
    assert entry["ep"]["ep_state"] == EP_STATE_ABSENT, entry["ep"]
    assert entry["ep"]["has_ep"] is False, entry["ep"]
    # `_band(None)` is defence-in-depth: `_eligible` refuses the absent mean
    # before `kept`, so the sort's None tier is unreachable from the pipeline.
    # It is pinned because the OLD signature fell into numeric comparisons that
    # raise inside the pipeline's blanket `except`, which would have dropped
    # EVERY candidate in the response.
    assert _band(None) == "unmeasured", _band(None)
    assert _band(0.92) == "high", _band(0.92)


def test_min_confidence_refuses_an_absent_belief_at_a_floor_below_half(sdk):
    """The `sdk.search` floor compared a fabricated 0.5 against `min_confidence`.

    `config/surface-manifest.yml` advertises "an absent row fails any floor
    ABOVE 0.0 and is kept by the 0.0 default", and no row distinguished that
    branch: every existing floor test uses a floor above 0.5 over a measured
    0.5, where the pre- and post-fix answers agree. A floor in (0, 0.5] against
    a claim with NO belief is the discriminating shape.

    MUTANT THAT REDS THIS: reintroduce `_mean = 0.5 if ep.confidence_mean is
    None else ep.confidence_mean` and compare `_mean` against the floor.
    """
    pid = sdk.create_point("statement", "zqminconf floor absent claim")["id"]

    kept = sdk.tortoise_fts_query("zqminconf", limit=10, min_confidence=0.0)
    assert [r["id"] for r in kept] == [pid], (
        "the shipped 0.0 default is 'filter off' and must keep admitting an "
        "unmeasured claim: " + repr(kept))
    for floor in (0.1, 0.3, 0.5):
        got = sdk.tortoise_fts_query("zqminconf", limit=10,
                                     min_confidence=floor)
        assert got == [], (
            f"an absent belief has no number to clear a floor of {floor}, so "
            f"the row must be dropped — it was kept, i.e. compared as the "
            f"neutral 0.5: {got}")
def test_review_prune_lists_a_string_stored_contested_pair(sdk):
    """Cycle 30: `_review_prune`'s contested scan carried a SECOND gate,
    `a > 0 AND b > 0`, on the RAW coalesced operands.

    A string-stored operand (`posterior_alpha="2"` — the legacy population
    `numeric_operand_cypher` exists for) makes `"2" > 0` NULL in FalkorDB, so
    the whole conjunction was NULL and the pair was DROPPED, while its twin
    `ep.get_contested_claims` lists the same node from the same width rule (it
    has no such guard — it tests `v > $t` only). One graph, two contested
    scans, two answers for one claim: the #11702 defect this file exists for,
    one scan over.

    The guard was redundant as well as wrong: `ep_variance_admissible_cypher`
    already requires both operands READABLE and non-negative and bands the sum,
    so `v > $t` is the whole test.
    """
    from tortoise.ep import TortoiseEP

    a = sdk.create_point("statement", "string-stored contested claim")["id"]
    b = sdk.create_point("statement", "counter claim")["id"]
    sdk.create_operator("IMPL", a, [b])
    proj = sdk._get_proj()
    # STRING-stored pair: readable to `numeric_operand_cypher` as float32, and
    # Beta(2,2) has variance 0.05 > the 0.04 default threshold.
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = '2', "
        "n.posterior_beta = '2', n.confidence = null",
        params={"id": a})
    stored = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.posterior_alpha, n.posterior_beta",
        params={"id": a}).result_set
    assert stored and isinstance(stored[0][0], str), (
        "fixture did not store STRINGS — the raw-compare split cannot be "
        "exercised: " + repr(stored))

    listed = {c["id"] for c in TortoiseEP(projection=proj).get_contested_claims()}
    assert a in listed, (
        "the twin scan did not list the string-stored contested pair, so this "
        "test cannot discriminate the divergence: " + repr(sorted(listed)))

    out = sdk.review_connections(mode="prune", scope=None, prune_limit=50)
    contested = {e["detail"].get("contested_endpoint")
                 for e in out["prune"] if e["issue"] == "contested"}
    assert a in contested, (
        "_review_prune dropped a contested pair its twin lists — the raw "
        "`a > 0 AND b > 0` compared a STRING operand to an integer and made "
        "the conjunction NULL: " + repr(sorted(x for x in contested if x)))


def test_census_read_refuses_a_boolean_operand():
    """Cycle 30: the EP-activation census read a stored BOOLEAN posterior as
    `float(True) == 1.0`.

    `numeric_operand` (and `belief_mirror`, and every Cypher surface) retired
    that convention in cycles 12→16 — a Boolean belief is a writer defect, not
    certainty, and it is ABSENT on both twins. The census's own `_operand`
    dispatched only on `str`, so the pair branch kept the retired reading and
    published a rendered `confidence: 0.50` for a claim with no readable
    belief.
    """
    from tools.longmem_eval.ep_activation import read_confidence

    assert read_confidence({"posterior_alpha": True,
                            "posterior_beta": True}) is None, (
        "a Boolean PAIR still reads as certainty — the retired `True` → 1.0 "
        "convention survived in the census")
    assert read_confidence({"posterior_alpha": True,
                            "posterior_beta": 1.0}) is None, (
        "a Boolean operand beside a readable one must not be coerced to 1.0")
    # …and the READABLE shapes keep their values, so this is not a rule that
    # nulls everything: a mirror still leads, and a genuine float pair reads.
    assert read_confidence({"confidence": 0.9}) == pytest.approx(0.9)
    assert read_confidence({"posterior_alpha": 2.0,
                            "posterior_beta": 2.0}) == pytest.approx(0.5)
    # A string operand still reads as the float32 the store serves.
    assert read_confidence({"posterior_alpha": "2",
                            "posterior_beta": "2"}) == pytest.approx(0.5)


def test_f32_neighbour_is_sign_aware():
    """Cycle 30: `_f32_neighbour` added to the UNSIGNED bit pattern, which is
    monotonic in the VALUE only for non-negative floats.

    For a negative float the unsigned pattern runs the other way, so `bits + 1`
    moved toward ZERO — asked for the neighbour of `-1.0` on the `-1` side it
    answered `-0.99999994`, a value on the wrong side of the input. In
    `_f32_from_literal` that corrupted the midpoint test, so a NEGATIVE
    string-operand literal sitting exactly on a float32 midpoint fell back to
    the plain double rounding the function exists to remove.
    """
    from tortoise.search_engine import _f32_neighbour

    up = _f32_neighbour(-1.0, +1)      # toward +inf: magnitude DECREASES
    down = _f32_neighbour(-1.0, -1)    # toward -inf: magnitude increases
    assert up > -1.0 > down, (up, down)
    assert up == pytest.approx(-0.99999994, abs=1e-9), up
    assert down == pytest.approx(-1.0000001, abs=1e-6), down

    # Positive values keep the old (correct) behaviour.
    assert _f32_neighbour(1.0, +1) == pytest.approx(1.0000001, abs=1e-6)
    assert _f32_neighbour(1.0, -1) == pytest.approx(0.99999994, abs=1e-9)

    # Zero: the neighbour is the smallest subnormal on the requested side, not
    # an unsigned step across a sign bit.
    smallest = 1.401298464324817e-45
    assert _f32_neighbour(0.0, +1) == smallest
    assert _f32_neighbour(0.0, -1) == -smallest
    assert _f32_neighbour(-0.0, +1) == smallest
    assert _f32_neighbour(-0.0, -1) == -smallest

    # …and the NEGATIVE direction reaches the same decisions. Two shapes,
    # because they are two different branches: `-1.0000000596046448` is the
    # decimal whose DOUBLE is exactly the float32 midpoint (its exact text is a
    # hair INSIDE it), so the literal decides and the store's answer is the
    # larger magnitude; the full-precision midpoint literal is a genuine tie and
    # round-half-to-even keeps `rounded` (−1.0). The pre-fix neighbour swapped
    # the sides for BOTH, so each assertion below is discriminating.
    from tortoise.search_engine import _f32_from_literal
    inside = "-1.0000000596046448"        # double == midpoint; text inside it
    assert _f32_from_literal(inside, float(inside)) == pytest.approx(
        -1.0000001192092896, rel=0, abs=1e-9)
    tie = "-1.000000059604644775390625"   # the exact midpoint, half-to-even
    assert _f32_from_literal(tie, float(tie)) == -1.0


def test_the_inheritance_revert_clears_the_mirror_it_stamped(sdk):
    """Cycle 31 (reviewer P1): the revert removed the PAIR and left the MIRROR.

    `set_point_baseline` — which the inherited-baseline write path goes through —
    stamps `n.confidence` as well as the pair. The revert arm for a Point that
    loses all its sources REMOVEd `ep_alpha`, `ep_beta` and both posteriors and
    left `confidence`, and the journal record it emitted carried no
    `confidence` either. Before this PR that was harmless (no mirror involved,
    and the reads fabricated 0.5 anyway); AFTER it, every read is mirror-first,
    so a claim the writer deliberately returned to NEUTRAL served the old
    baseline value on every surface, permanently, with no pair behind it — and a
    rebuild reproduced it, because live and replay agreed.

    The revert's own WHERE already requires
    `baseline_source='inherited-from-source' AND n.credibility IS NULL`, so
    there is no authored prior to preserve: the clear is unconditional.
    """
    from tortoise.ranking import GraphRanker
    from tortoise.search_engine import annotate_ep_batch

    pid = sdk.create_point("statement", "inherited claim",
                           extractedFrom="https://s0.example",
                           status="live")["id"]
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (s:Source {url:$url}) SET s.credibilityTier='T0', "
        "s.sourceDate=$sd, s.ingestedAt=$sd",
        params={"url": "https://s0.example",
                "sd": "2024-01-01T00:00:00+00:00"})
    sdk._apply_source_inheritance(recency_decay=1.0)      # the write path
    stamped = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence, n.baseline_source",
        params={"id": pid}).result_set
    assert stamped and stamped[0][0] is not None, (
        "fixture did not stamp a mirror — the revert's orphan cannot be "
        "exercised: " + repr(stamped))
    assert stamped[0][1] == "inherited-from-source", stamped

    # Delete the only source edge -> no eligible source -> the revert branch.
    proj.g.query(
        "MATCH (n:Point {id:$id})-[r:extractedFrom]->(s:Source) DELETE r",
        params={"id": pid})
    sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)

    after = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence, n.posterior_alpha, "
        "n.ep_alpha, n.baseline_source",
        params={"id": pid}).result_set
    assert after, "the Point vanished"
    assert after[0][1] is None and after[0][2] is None, (
        "fixture: the revert did not clear the pair: " + repr(after))
    assert after[0][0] is None, (
        "the revert cleared the PAIR and left the MIRROR (`n.confidence` = "
        f"{after[0][0]!r}) — every read is mirror-first, so this claim now "
        "serves a baseline value that no pair backs, for ever: " + repr(after))

    # …and the READ agrees, which is the property that matters: a reverted
    # claim is absent, not a stale baseline.
    ann = annotate_ep_batch(proj.g, [pid])[pid]
    assert ann.confidence_mean is None, ann
    assert ann.has_ep is False, ann
    assert ann.ep_state == "absent", ann
    graph = GraphRanker(projection=proj)._fetch_point_signals([pid])[pid]
    assert graph["confidence"] is None, graph


def test_update_entity_clearing_the_pair_clears_the_mirror(sdk, tmp_path):
    """Cycle 31 (reviewer P2): `_update_entity` is the SECOND documented
    `:Point` update door, and it let a new pair ride through beside the old
    mirror.

    `_sanitize_props(reject_belief_props=True)` refuses `credibility` and the
    baseline quartet only — `posterior_alpha` / `posterior_beta` / `confidence`
    pass straight through, exactly as they did on `update_point` before this PR
    closed that door. Post-#11702 the mirror LEADS, so a tenant writing a pair
    through `update_entity` (or `update` on a Point, or `tortoise_update_entity`
    over MCP) is served a belief their own write contradicted, silently.

    Cycle 32: this door also had to JOURNAL what it applied. `journaled_updates`
    is the annotator set plus `mentions`, so the pair and the mirror clear landed
    LIVE and never reached the record — `rebuild_all` replayed the
    `set_point_baseline` belief still on the journal and served a DIFFERENT
    answer on recovery (measured: live 0.5 / "measured" vs rebuilt 0.9 /
    "baseline"; `check_consistency` → divergence "content", fields
    ["confidence"]). Both halves are pinned here, like the sibling tests that
    assert the record.
    """
    import json as _json

    from tortoise.sdk import TortoiseSDK
    from tortoise.search_engine import annotate_ep_batch

    events = tmp_path / "uevents34"
    events.mkdir()
    j = TortoiseSDK(str(tmp_path / "ue.db"),
                    event_log_path=str(events / "events.jsonl"))
    try:
        pid = j.create_point("statement", "entity-door claim")["id"]
        proj = j._get_proj()
        j.set_point_baseline(pid, 9.0, 1.0)          # mirror 0.9, pair (9, 1)
        assert annotate_ep_batch(proj.g, [pid])[pid].confidence_mean == \
            pytest.approx(0.9, abs=1e-3)

        j.update_entity(pid, posterior_alpha=10.0, posterior_beta=10.0)

        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.confidence, n.posterior_alpha, "
            "n.posterior_beta", params={"id": pid}).result_set
        assert row, "the Point vanished"
        assert row[0][1] == pytest.approx(10.0) and row[0][2] == pytest.approx(
            10.0), ("fixture: the pair did not land through `update_entity`: "
                    + repr(row))
        assert row[0][0] is None, (
            "`update_entity` wrote a new α/β pair and left the OLD mirror "
            f"({row[0][0]!r}) beside it — the mirror leads on every read, so the "
            "claim is served a belief its own write replaced: " + repr(row))

        ann = annotate_ep_batch(proj.g, [pid])[pid]
        assert ann.confidence_mean == pytest.approx(0.5), (
            "the read is still led by the stale mirror rather than the stored "
            f"pair: {ann}")

        # The RECORD must carry the belief the write applied, or a rebuild
        # replays the older `set_point_baseline` belief instead.
        recs = [r for r in (_json.loads(line)
                            for line in (events / "events.jsonl").read_text(
                                ).splitlines() if line.strip())
                if r.get("type") == "PointRevised" and r.get("id") == pid]
        assert recs, "`update_entity` journaled no PointRevised for the Point"
        carried = {k: recs[-1][k] for k in
                   ("confidence", "posterior_alpha", "posterior_beta")
                   if k in recs[-1]}
        assert carried.get("confidence", "MISSING") is None, (
            "the record omitted the mirror CLEAR while the statement performed "
            "it — a rebuild would restore the old mirror: " + repr(recs[-1]))
        assert carried.get("posterior_alpha") == pytest.approx(10.0), carried
        assert carried.get("posterior_beta") == pytest.approx(10.0), carried

        # …and the rebuild must AGREE with the live read.
        before = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.confidence, n.posterior_alpha, "
            "n.posterior_beta", params={"id": pid}).result_set[0]
        proj.rebuild_all(str(events), confirm_destructive=True)
        after = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.confidence, n.posterior_alpha, "
            "n.posterior_beta", params={"id": pid}).result_set[0]
        assert after == before, (
            "the belief state changed across rebuild_all — live and replay "
            f"disagree: {before} -> {after}")
    finally:
        j.close()

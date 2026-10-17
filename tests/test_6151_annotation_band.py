"""#6151 — the annotation band: a well-rated operator counts more, a default one is neutral.

Owner ruling, 2026-10-11 (option B of the posed question, relayed to #6151):

    "a well rated piece of evidence should count more than an unrated one, this
     allows a user to grade entities in confidence even if they haven't got a
     source. ... importantly the default should be neutral and only with a
     purposeful agent/user intention should we end up not in the default."

Two requirements, and every assertion below exists because one of them has a
failure mode that a merely-plausible implementation passes:

**The default must be EXACTLY neutral.** The dimensions were archived on
2026-07-29 by `1fa2fa7ce` to fix a 91% attenuation of EVERY operator in the
graph. That bug was not "a formula exists" — it was that the four coalesce
defaults were `0.5`, and the archived formula `(1-b*0.5)*p*c*d` had no neutral
point, so `0.5` read as `(1-0.5*0.5)*0.5*0.5*0.5 = 0.0938` and a silent default
penalised the whole graph. A test asserting only "an unrated operator scores
~1.0" would pass against a formula whose neutral point is a coincidence. So the
assertion is `== 1.0` exactly, and the 91% case is pinned alongside it as a
computed counter-example: the OLD defaults through the OLD formula must still
give 0.0938, so the difference between the two regimes is a property of the
SHAPE and not of a lucky number.

**Rating well must not be a penalty.** The archived combination multiplied three
dimensions, so three ratings of 0.9 gave `0.9**3 = 0.729` — a 27% penalty for
being rated well, with no neutral point short of a perfect 1.0 on all four.
Measured on the live graph: the single annotated operator carries `bias 0.1` and
`0.9` on the other three (the best reading available) and the archived formula
still took 30.7% of its weight. An assertion that "annotation moves the weight"
would pass against that: it moved it DOWN.

MUTATION THAT MUST RED THE CORE ASSERTIONS:
  * restore `annotation_factor = 1.0` -> ``test_the_band_is_read_from_the_graph``
    and every `annotation_factor` magnitude assertion fail (no movement, and the
    live case reads 1.0 not 1.7411);
  * change the combination back to a product -> ``test_three_good_ratings_...``
    fails (0.729 < 1.0);
  * set any coalesce default back to the archived identity (`bias 0.0`,
    `precision 1.0`) -> ``test_the_band_is_read_from_the_graph`` fails: an
    unrated operator reads 2.0, doubling every operator in the graph. This is a
    READ-SITE mutation, so it is the integration test that must catch it, not the
    pure tests — they never see the query, they are handed the neutral value
    directly. (`test_an_unrated_operator_is_EXACTLY_neutral` cannot fail on it; an
    earlier revision of this comment claimed otherwise and was wrong.)
  * make the factor one-sided (clamp the top to 1.0) -> the ruling's own
    requirement, ``test_a_well_rated_operator_counts_MORE``, fails.

Scope note: this module asserts the ANNOTATION mechanism only. `#2315`'s
MITIGATION band (`[0.10, 0.50]`, never > 0.50) is a separate mechanism with its
own tests and is deliberately untouched by this change.
"""

import itertools
import os
import uuid

import pytest

from tortoise.weights import (
    ANNOTATION_FACTOR_MAX,
    ANNOTATION_FACTOR_MIN,
    ANNOTATION_TERM_NEUTRAL,
    annotation_factor,
)

# ── pure-function properties (no graph needed) ────────────────────────────────

N = ANNOTATION_TERM_NEUTRAL


def _archived(bias, precision, consistency, directness):
    """The archived formula, kept here as a COMPUTED COUNTER-EXAMPLE.

    Its presence is the point of the contrast assertions below: the archived
    shape is what the same `0.5` default produced 91% attenuation through, so
    pinning both regimes shows the new one is safe by shape and not by value.
    """
    return (1.0 - bias * 0.5) * precision * consistency * directness


def test_an_unrated_operator_is_EXACTLY_neutral():
    """The owner's default requirement, asserted exactly rather than approximately.

    "the default should be neutral and only with a purposeful agent/user
    intention should we end up not in the default" — so an operator carrying no
    rating must score exactly 1.0. `==` and not `approx`: the coalesce defaults
    ARE the neutral term, so the exponent is exactly 0 and the float is exact.
    An `approx` here would accept a formula whose neutrality is a rounding
    coincidence, which is the class of bug this module exists to catch.
    """
    assert annotation_factor(N, N, N, N) == 1.0, (
        "an operator carrying no rating is not EXACTLY neutral, so the default is "
        "a penalty (or a bonus) rather than an absence of judgement"
    )
    # "rated as middling" and "never rated" must agree — they are the same value
    # at the read site, and both must be the neutral point.
    assert annotation_factor(0.5, 0.5, 0.5, 0.5) == 1.0
    # ...and the neutrality must be at the MIDDLE, not everywhere. Without this,
    # `return 1.0` satisfies every assertion above (review finding: the test was
    # passed by the constant the archive used).
    assert annotation_factor(0.0, 1.0, 1.0, 1.0) != 1.0, (
        "the factor is 1.0 at the best possible rating, so it is a constant rather "
        "than a band — the archive's `annotation_factor = 1.0` would pass the "
        "neutrality assertions"
    )


def test_the_archived_91_percent_attenuation_cannot_recur():
    """Pin BOTH regimes, so the fix is a property of the shape.

    The archived defaults through the archived formula gave 0.0938 — the 91%
    attenuation that got the mechanism archived. The current defaults through the
    current formula give 1.0. Asserting only the second would be satisfied by a
    lucky constant; asserting the first shows what the same number does through
    the shape we removed.
    """
    archived_identity = (0.0, 1.0, 1.0, 1.0)  # what the coalesce used to return
    assert _archived(*archived_identity) == 1.0, (
        "the archived formula is NOT neutral on the identity defaults, so the "
        "defaults-fix that archived this mechanism (1fa2fa7ce) is no longer the "
        "reason the archive worked — re-derive which half was load-bearing"
    )
    # The three-way contrast, which is where the bug actually lived:
    #   archived formula + identity defaults -> 1.0     (neutral: the defaults FIX)
    #   archived formula + 0.5 defaults      -> 0.09375 (the 91% attenuation)
    #   current  formula + 0.5 defaults      -> 1.0     (neutral by SHAPE)
    # The middle row is the bug; the first row is why the archive worked; the third
    # is why the same value is now safe.
    assert _archived(0.5, 0.5, 0.5, 0.5) == pytest.approx(0.09375, abs=1e-9), (
        "the 91% attenuation is not reproducible from the archived defaults and "
        "formula, so this module is no longer measuring the bug it guards"
    )
    # The SAME 0.5 defaults through the CURRENT shape are neutral.
    assert annotation_factor(0.5, 0.5, 0.5, 0.5) == 1.0, (
        "the current shape reintroduced the 91% attenuation through the archived "
        "defaults — the bug IS the shape, and it is back"
    )


def test_a_well_rated_operator_counts_MORE():
    """The ruling's positive half: rating up must be expressible at all.

    The archived formula's maximum over its whole input domain is 1.0, so it
    could never express "some weigh more" — this asserts the capability exists.
    """
    best = annotation_factor(0.0, 1.0, 1.0, 1.0)
    assert best == ANNOTATION_FACTOR_MAX == 2.0
    assert best > 1.0, (
        "a perfectly rated operator does not count for more than an unrated one, "
        "so the mechanism cannot express the owner's requirement at all"
    )
    # The maximum must be ATTAINED at the best rating, not everywhere: a constant
    # `2.0` satisfies the two assertions above (review finding).
    assert annotation_factor(0.0, 0.9, 0.9, 0.9) < ANNOTATION_FACTOR_MAX, (
        "a non-perfect rating already reaches the top of the band, so the factor is "
        "saturating and the band is not being computed"
    )


def test_the_band_is_symmetric_and_bounded():
    """Worst and best are exact opposites; nothing escapes the band."""
    worst = annotation_factor(1.0, 0.0, 0.0, 0.0)
    best = annotation_factor(0.0, 1.0, 1.0, 1.0)
    assert worst == ANNOTATION_FACTOR_MIN == 0.5
    assert best * worst == 1.0, (
        "the band is not symmetric in log space, so a rating costs and earns "
        "different amounts and the scale cannot be reasoned about"
    )
    # Nothing in the domain escapes [MIN, MAX].
    for bias in (0.0, 0.25, 0.5, 0.75, 1.0):
        for v in (0.0, 0.5, 1.0):
            f = annotation_factor(bias, v, v, v)
            assert ANNOTATION_FACTOR_MIN - 1e-12 <= f <= ANNOTATION_FACTOR_MAX + 1e-12


def test_three_good_ratings_are_not_a_penalty():
    """The archived product's defect: 0.9 three times must not cost 27%.

    `0.9**3 = 0.729`. An assertion that "annotation moves the weight" passes
    against that, because it moved it — downwards. The observable that
    distinguishes them is the SIGN.
    """
    f = annotation_factor(0.1, 0.9, 0.9, 0.9)
    assert f > 1.0, (
        f"three ratings of 0.9 plus a low-bias 0.1 produce {f:.4f}; a "
        "well-rated operator is being PENALISED, which is the archived defect"
    )
    # And the archived formula on the same input, as the contrast.
    assert _archived(0.1, 0.9, 0.9, 0.9) == pytest.approx(0.69255, abs=1e-9)


def test_monotone_in_every_dimension():
    """Better on any dimension must read strictly better.

    STRICT, not `<=`. A relaxed comparison is satisfied by a constant function of
    the dimension — every sampled value equal, so `a <= b` holds trivially — which
    is exactly the archived behaviour, so the non-strict form could not detect a
    regression to it (review finding).
    """
    sweep = [0.0, 0.2, 0.5, 0.8, 1.0]
    for i, name in enumerate(("bias", "precision", "consistency", "directness")):
        vals = []
        for v in sweep:
            args = [N, N, N, N]
            # `bias` enters INVERTED: lower bias is better.
            args[i] = (1.0 - v) if name == "bias" else v
            vals.append(annotation_factor(*args))
        # `bias` enters INVERTED, so the sweep is in ASCENDING quality for every
        # dimension — `v` only ever selects a better rating.
        assert all(a < b for a, b in itertools.pairwise(vals)), (
            f"the factor is not STRICTLY increasing in quality along {name}: {vals}"
        )


def test_a_partial_rating_moves_only_as_far_as_that_dimension():
    """An unset dimension contributes its neutral term, never an assumption.

    The owner: "only with a purposeful agent/user intention should we end up not
    in the default". A rating on one dimension is exactly one dimension's worth
    of intention, so it must move the factor by exactly one dimension's share and
    not by more — otherwise writing a single prop would swing the weight to the
    band edge.
    """
    one_dimension = annotation_factor(N, 1.0, N, N)
    all_four = annotation_factor(0.0, 1.0, 1.0, 1.0)
    assert 1.0 < one_dimension < all_four, (
        f"one dimension at its best gives {one_dimension:.4f}; it must be positive "
        f"(the intention is recorded) but strictly less than all four ({all_four})"
    )
    # Exactly one dimension's worth: the mean of one 1.0 and three neutrals.
    assert one_dimension == pytest.approx(2.0 ** (2.0 * 0.625 - 1.0), abs=1e-12)


# ── the graph read (needs live FalkorDB) ──────────────────────────────────────

_LIVE_URI_SAVED = os.environ.get("TORTOISE_DB_URI")
_LIVE_OK = False
try:
    from tests import _live_utils

    _LIVE_URI = _live_utils.docker_uri("tortoise_test_ann6151")
    os.environ["TORTOISE_DB_URI"] = _LIVE_URI
    from tortoise.sdk import TortoiseSDK as _Probe

    _p = _Probe()
    _p._get_proj().g.query("RETURN 1")
    _p.close()
    _LIVE_OK = True
except Exception:
    _LIVE_URI = None
finally:
    if _LIVE_URI_SAVED is not None:
        os.environ["TORTOISE_DB_URI"] = _LIVE_URI_SAVED
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


def test_an_uninterpretable_dimension_is_neutral_and_never_raises():
    """A stored value that is not a number must degrade to NEUTRAL, not raise.

    The stored dimensions are not trusted: the generic entity surface can write
    them without validation (`update_point(op, annotator_bias='oops')` persists
    the string). `compute_operator_weight` is called on the hot path with no
    handler, so raising here would let ONE malformed operator abort the whole
    propagation run. Before #6151 these values were unpacked but unused, so a
    malformed one was inert — reactivating them must not turn it into a crash.
    """
    for bad in ("oops", None, object(), float("nan"), 10**400):
        got = annotation_factor(bad, 0.5, 0.5, 0.5)
        assert got == 1.0, (
            f"a dimension of {bad!r} produced {got}, not the neutral 1.0 — an "
            "uninterpretable value is not a rating, and the run must survive it"
        )
        # ...and it must SKIP, not be silently treated as the worst rating.
        assert got != ANNOTATION_FACTOR_MIN


class _RaisesOnFloat:
    """A value whose own conversion raises — `float()` is not limited to a tuple."""

    def __init__(self, exc):
        self._exc = exc

    def __float__(self):
        raise self._exc


def test_a_value_whose_own_conversion_raises_cannot_abort_the_run():
    """`except Exception`, not a fixed tuple: the contract is "never abort the run".

    `float(value)` can raise anything the value's own `__float__` chooses, so a
    narrow tuple leaves a hole in a guard whose entire purpose is that one bad
    row cannot kill the propagation loop (`ep.py` calls `compute_operator_weight`
    with no handler). `BaseException` is deliberately NOT caught — control flow
    must still interrupt the run.
    """
    for exc in (RecursionError("deep"), MemoryError("big"), RuntimeError("odd"), Exception("plain")):
        got = annotation_factor(_RaisesOnFloat(exc), 0.5, 0.5, 0.5)
        assert got == 1.0, f"{type(exc).__name__} from __float__ gave {got}, not neutral 1.0"

    # Control flow is NOT swallowed (this is why the catch is Exception, not BaseException).
    with pytest.raises(KeyboardInterrupt):
        annotation_factor(_RaisesOnFloat(KeyboardInterrupt()), 0.5, 0.5, 0.5)
    with pytest.raises(SystemExit):
        annotation_factor(_RaisesOnFloat(SystemExit()), 0.5, 0.5, 0.5)


def test_an_out_of_range_dimension_cannot_escape_the_band():
    """The documented band must HOLD for stored values outside [0, 1].

    The band is documented as bounded (0.5 worst, 2.0 best) but the read does no
    validation, so without an explicit clamp an out-of-range value escapes it:
    `annotator_bias = 3.0` scored 0.42 — below the documented minimum — and
    `-1e6` raised OverflowError. The final weight clamp [0.1, 10.0] contains the
    damage but does not make the annotation contract true.
    """
    probes = [3.0, -1e6, 1e9, -5.0, float("inf"), float("-inf"), 0.0, 1.0]
    for i, dimension in enumerate(("bias", "precision", "consistency", "directness")):
        for v in probes:
            args = [0.5, 0.5, 0.5, 0.5]
            args[i] = v
            got = annotation_factor(*args)
            assert ANNOTATION_FACTOR_MIN <= got <= ANNOTATION_FACTOR_MAX, (
                f"{dimension}={v!r} produced {got}, outside the documented band "
                f"[{ANNOTATION_FACTOR_MIN}, {ANNOTATION_FACTOR_MAX}]"
            )


@pytest.mark.skipif(not _LIVE_OK, reason="Live FalkorDB (Docker) not available")
def test_the_band_is_read_from_the_graph(monkeypatch):
    """End-to-end through the reader: the dimensions on the node drive the weight.

    This is the assertion the archive kept green for months. With
    `annotation_factor = 1.0` hardcoded, every case below returned the base
    weight and nothing observed that the rating was inert — which is why the
    observable here is the RETURNED WEIGHT across three ratings, not the
    existence of the `annotator_*` props.
    """
    from tortoise.sdk import TortoiseSDK as SDK
    from tortoise.weights import compute_operator_weight

    monkeypatch.setenv("TORTOISE_DB_URI", _LIVE_URI)
    sdk = SDK(db_path=None, namespace=f"test_ann6151_{uuid.uuid4().hex[:8]}")
    try:
        src = sdk.create_point("statement", "source", status="live", dedup=False)
        tgt = sdk.create_point("statement", "claim", status="live", dedup=False)
        op = sdk.create_operator("IMPL", src["id"], [tgt["id"]])
        proj = sdk._get_proj()

        unrated = compute_operator_weight(proj, op["id"])
        assert unrated == 1.0, (
            f"an unrated IMPL operator reads {unrated}, not the base 1.0 — the "
            "default is moving the weight without any stated intention"
        )

        sdk.annotate_operator(op["id"], bias=0.0, precision=1.0, consistency=1.0, directness=1.0)
        best = compute_operator_weight(proj, op["id"])
        assert best == pytest.approx(2.0), (
            f"a perfectly rated IMPL operator reads {best}, not 2.0 — the graph "
            "rating is not reaching the weight"
        )

        sdk.annotate_operator(op["id"], bias=1.0, precision=0.0, consistency=0.0, directness=0.0)
        worst = compute_operator_weight(proj, op["id"])
        assert worst == pytest.approx(0.5), f"a worst-rated operator reads {worst}, not 0.5"

        # The live-graph case, as measured: bias 0.1 and 0.9 on the other three.
        sdk.annotate_operator(op["id"], bias=0.1, precision=0.9, consistency=0.9, directness=0.9)
        live_like = compute_operator_weight(proj, op["id"])
        assert live_like == pytest.approx(1.741101, abs=1e-6), (
            f"the reading measured on the live graph gives {live_like}, not "
            "1.741101 — the archived formula took 30.7% of this operator's weight"
        )
    finally:
        sdk.close()


@pytest.mark.skipif(not _LIVE_OK, reason="Live FalkorDB (Docker) not available")
def test_a_malformed_stored_value_cannot_abort_the_reader(monkeypatch):
    """A value the write path admits but the reader cannot parse must NOT raise.

    The store does not validate these properties, and `compute_operator_weight`
    runs on the hot path with no handler, so a single malformed row would abort
    the ENTIRE propagation run. Both properties are exercised here because both
    had the same hazard: the annotation dims (fixed in #6151 when they were
    reactivated — they were inert before, so a bad value used to be a no-op) and
    `mitigation_strength` (pre-existing; `projection/entities.py:82` documents it
    in-tree).

    The assertion is deliberately NOT "returns neutral": the point is that the
    call COMPLETES and stays inside the documented weight bounds, whatever the
    store holds.
    """
    from tortoise.sdk import TortoiseSDK as SDK
    from tortoise.weights import compute_operator_weight

    monkeypatch.setenv("TORTOISE_DB_URI", _LIVE_URI)
    sdk = SDK(db_path=None, namespace=f"test_ann6151_bad_{uuid.uuid4().hex[:8]}")
    try:
        src = sdk.create_point("statement", "source", status="live", dedup=False)
        tgt = sdk.create_point("statement", "claim", status="live", dedup=False)
        op = sdk.create_operator("IMPL", src["id"], [tgt["id"]])
        proj = sdk._get_proj()

        # 1. A non-numeric annotation dimension, written through the generic
        #    entity surface exactly as an agent could.
        sdk.update_point(op["id"], annotator_bias="oops")
        got = compute_operator_weight(proj, op["id"])
        assert 0.1 <= got <= 10.0, f"malformed annotation dim gave {got}, outside [0.1, 10.0]"
        assert got == 1.0, (
            f"a malformed annotation dimension gave {got}, not the neutral 1.0 — "
            "an unreadable value is not a rating"
        )

        # 2. An out-of-range dimension that passed through the same surface.
        sdk.update_point(op["id"], annotator_bias=3.0)
        got = compute_operator_weight(proj, op["id"])
        assert 0.5 <= got <= 2.0, (
            f"an out-of-range dim gave {got}, escaping the documented annotation band"
        )
        sdk.update_point(op["id"], annotator_bias=0.5)

        # 3. A non-numeric mitigation strength: the same hazard, on the other
        #    mechanism, reached through the documented mitigate path.
        mit = sdk.mitigate_operator(op["id"], "some reason", strength=0.25)
        mit_id = mit.get("id") or mit.get("mitigation_id")
        assert mit_id, f"mitigate_operator returned no id: {mit}"
        sdk.update_point(mit_id, mitigation_strength="oops")
        got = compute_operator_weight(proj, op["id"])
        assert 0.1 <= got <= 10.0, f"malformed mitigation strength gave {got}"

        # 4. A NON-FINITE strength reached through the STRING "nan": the store
        #    refuses a real non-finite number outright (UnrepresentableNumberError,
        #    even on a raw Cypher parameter), but a string is persisted unvalidated
        #    and converts to NaN. It matters because `max([nan, 0.25])` is `nan`,
        #    so ONE such row would return NaN as the weight of the whole operator.
        sdk.update_point(mit_id, mitigation_strength="nan")
        got = compute_operator_weight(proj, op["id"])
        assert got == got, (
            f"a NaN mitigation strength made the weight NaN ({got}) — one "
            "such row poisons every read of this operator"
        )
        assert 0.1 <= got <= 10.0, f"NaN mitigation strength gave {got}"

        # 5. An INFINITE strength must still CLAMP to the band edge, not be
        #    dropped to the mid-band default: `mitigation_dampening_factor`
        #    promises that, and dropping it would silently turn a maximum-damping
        #    `inf` into the milder fallback. This is the behaviour a NaN-only
        #    filter preserves and an isfinite filter does not.
        sdk.update_point(mit_id, mitigation_strength="inf")
        damped_hard = compute_operator_weight(proj, op["id"])
        sdk.update_point(mit_id, mitigation_strength="-inf")
        damped_soft = compute_operator_weight(proj, op["id"])
        assert damped_hard < damped_soft, (
            f"an infinite strength ({damped_hard}) did not damp harder than a "
            f"negative one ({damped_soft}) — infinities are being dropped to the "
            "mid-band default instead of clipped to the band edges"
        )
    finally:
        sdk.close()


@pytest.mark.skipif(not _LIVE_OK, reason="Live FalkorDB (Docker) not available")
def test_recall_state_survives_the_same_malformed_values(monkeypatch):
    """The STATE reader must hold the weight reader's contract, on the same props.

    `compute_operator_weight` is not the only reader of `annotator_*` and
    `mitigation_strength`: `recall_state` reads both, and it is an agent-facing
    path. Guarding the weight read alone leaves one malformed row aborting every
    state query that touches the operator — while the weight read on the identical
    input returns 1.0. Both readers must degrade, not raise.
    """
    from tortoise.sdk import TortoiseSDK as SDK

    monkeypatch.setenv("TORTOISE_DB_URI", _LIVE_URI)
    sdk = SDK(db_path=None, namespace=f"test_ann6151_state_{uuid.uuid4().hex[:8]}")
    try:
        src = sdk.create_point("decision", "a source claim", status="live", dedup=False)
        tgt = sdk.create_point("decision", "a target claim", status="live", dedup=False)
        op = sdk.create_operator("IMPL", src["id"], [tgt["id"]])
        mit = sdk.mitigate_operator(op["id"], "some reason", strength=0.25)
        mit_id = mit.get("id") or mit.get("mitigation_id")

        # Baseline: the state view works before anything is corrupted.
        sdk.recall_state("source claim")

        sdk.update_point(op["id"], annotator_precision="oops")
        sdk.recall_state("source claim")  # must not raise

        sdk.update_point(mit_id, mitigation_strength="oops")
        state = sdk.recall_state("source claim")  # must not raise
        assert state is not None

        # And the degraded values really are present, so the reader did not
        # simply drop the operator or the mitigation to dodge the parse.
        blob = repr(state)
        assert op["id"] in blob, (
            "the operator vanished from the state view instead of degrading — a "
            "malformed property must not silently hide the connection"
        )
    finally:
        sdk.close()

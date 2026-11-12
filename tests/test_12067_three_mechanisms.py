"""#12067 — the three epistemic mechanisms, as PROPERTIES (not band values).

The issue's acceptance names five behavioural claims. Four are pinned here as
properties over a SWEEP of inputs rather than as a number at one fixture, so a
regression cannot pass by landing inside a band:

  1. coherence one-way propagates **loss** (A falls ⇒ B falls);
  2. coherence does **not** raise the supported claim when A rises  (necessary,
     not sufficient — `Acc(b) => Acc(a)`, see the module note below);
  3. attack **never raises**;
  4. support never lowers a claim it is evidence FOR → stated with its exact
     boundary, and the boundary is ASSERTED (see
     `test_support_LOWERS_when_the_source_sits_BELOW_the_target`, the recorded
     gap that the L1c decision on #12067 is about);
  5. the closed set: a PAIR carries at most ONE mechanism, refused at the write
     door with both mechanisms named.

WHY `Acc(b) => Acc(a)` and not "they move together": bipolar argumentation
theory states necessary support as the DUAL of deductive support — the same edge
`a → b` carries the dependence in the opposite direction, so losing the
supporter propagates loss while ACCEPTING it does not establish the supported.
"Move together" would make coherence indistinguishable from support in the
rising direction, which is exactly the double count the closed set exists to
prevent. Sources are on #12067's research comment.

MUTATION SENSITIVITY (by construction, not by claim). The discriminating pairs
below fail if the mechanism mapping is wrong rather than if a number drifts:

  * `_MECHANISM_POTENTIALS["coherence"] = phi_impl` → the "does not raise" test
    fails (difference coupling RAISES a below-source target, and LOWERS a
    below-source target where coherence must too — but it also charges the
    opposite corner), and `test_support_LOWERS_when_the_source_sits_BELOW_the_target`
    would still pass, which is why both directions are asserted in ONE test;
  * dropping the closed-set gate in `sdk.create_operator` → every `test_refuses_*`
    fails with "DID NOT RAISE";
  * widening the gate to `composedOf` → `test_a_part_whole_opcode_is_not_a_second_mechanism`
    fails.

Runnable embedded:
  TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_12067_three_mechanisms.py -q
"""
from __future__ import annotations

import types

import pytest

from tortoise.ep import (
    _MECHANISM_POTENTIALS,
    MECHANISM_REL_TYPES,
    MECHANISM_VALUES,
    TortoiseEP,
    mechanism_rel_type,
)
from tortoise.pack_registry import (
    CORE_PREDICATES,
    SEMANTICS_MECHANISMS,
    SEMANTICS_VALUES,
)
from tortoise.quadrature import phi_coherence, phi_impl, phi_nand
from tortoise.sdk import TortoiseSDK

# Priors used throughout. The sweep matters: a single fixture at 0.5 cannot tell
# "the shape is right" from "the constant was tuned".
NEUTRAL = (1.0, 1.0)          # mean 0.500 — the neutral claim
WEAK = (1.1, 1.0)             # mean 0.524
HELD = (10.0, 1.0)            # mean 0.909 — a well-evidenced claim
CONFS = (0.05, 0.10, 0.20, 0.30, 0.5238, 0.70, 0.80, 0.909, 0.95)


def _beta(conf: float, strength: float = 10.0) -> tuple[float, float]:
    """A Beta prior with the given mean and `strength` total evidence."""
    return (conf * strength, (1.0 - conf) * strength)


# ── engine-level instrument: the REAL factor path, no database ────────────

def _mean_after(mechanism: str, source: tuple[float, float],
                target: tuple[float, float], *, direction: str = "->",
                weight: float = 1.0) -> float:
    """The target's posterior mean after ONE factor update, via `ep.py`.

    Uses `TortoiseEP._update_factor` itself (not the potentials directly), so a
    dispatch that prices the wrong mechanism, a message written to the wrong
    slot, or a direction that is not honoured all show up as a wrong number.
    The stub graph answers no rows, which is what a cold factor update sees.
    """
    stub = types.SimpleNamespace(
        g=types.SimpleNamespace(
            query=lambda *a, **k: types.SimpleNamespace(result_set=[])))
    ep = TortoiseEP(stub, damping=1.0, max_iter=1, tol=1e-9, n_quad=8)
    ep._node_cache = {"A": source, "B": target}
    ep._msg_cache = {}
    ep._update_factor("op", mechanism, ["A", "B"], weight=weight,
                      direction=direction)
    alpha, beta = target
    msg = ep._msg_cache.get(
        ("op", "B", mechanism_rel_type(mechanism)), (0.0, 0.0))
    post = (max(alpha + msg[0], 0.01), max(beta + msg[1], 0.01))
    return post[0] / (post[0] + post[1])


def _ep_stub(source=HELD, target=HELD) -> TortoiseEP:
    """A `TortoiseEP` with no database: the graph answers nothing, which is what
    a cold factor update sees."""
    stub = types.SimpleNamespace(
        g=types.SimpleNamespace(
            query=lambda *a, **k: types.SimpleNamespace(result_set=[])))
    ep = TortoiseEP(stub, damping=1.0, max_iter=1, tol=1e-9, n_quad=8)
    ep._node_cache = {"A": source, "B": target}
    ep._msg_cache = {}
    return ep


def _means_after(mechanism: str, source: tuple[float, float],
                 target: tuple[float, float], *, direction: str = "->",
                 weight: float = 1.0) -> tuple[float, float]:
    """(source mean, target mean) after ONE factor update on the REAL path."""
    stub = types.SimpleNamespace(
        g=types.SimpleNamespace(
            query=lambda *a, **k: types.SimpleNamespace(result_set=[])))
    ep = TortoiseEP(stub, damping=1.0, max_iter=1, tol=1e-9, n_quad=8)
    ep._node_cache = {"A": source, "B": target}
    ep._msg_cache = {}
    ep._update_factor("op", mechanism, ["A", "B"], weight=weight,
                      direction=direction)
    rel = mechanism_rel_type(mechanism)
    out = []
    for nid, prior in (("A", source), ("B", target)):
        msg = ep._msg_cache.get(("op", nid, rel), (0.0, 0.0))
        alpha = max(prior[0] + msg[0], 0.01)
        beta = max(prior[1] + msg[1], 0.01)
        out.append(alpha / (alpha + beta))
    return out[0], out[1]


def _delta(mechanism: str, source, target, **kw) -> float:
    before = target[0] / (target[0] + target[1])
    return _mean_after(mechanism, source, target, **kw) - before


# ── the closed set, as data ──────────────────────────────────────────────

def test_the_mechanism_set_is_three_and_closed():
    from tortoise.sdk import _MECHANISM_OP_TYPES

    assert frozenset({"IMPL", "NAND", "coherence"}) == MECHANISM_VALUES
    assert set(_MECHANISM_POTENTIALS) == MECHANISM_VALUES
    # Every mechanism is also a core predicate — one home, no second list…
    assert MECHANISM_VALUES <= CORE_PREDICATES
    # …and the sdk's ORDERED spelling (the tuple five call sites iterate) is that
    # same set, so a fourth mechanism cannot land in one home only (#12067 review
    # round 5, P2: it was a second declaration nothing compared).
    assert set(_MECHANISM_OP_TYPES) == MECHANISM_VALUES
    assert set(MECHANISM_REL_TYPES) == MECHANISM_VALUES


def test_every_semantics_mechanism_is_in_the_closed_set():
    assert set(SEMANTICS_MECHANISMS.values()) <= CORE_PREDICATES
    assert set(SEMANTICS_MECHANISMS) <= SEMANTICS_VALUES


def test_a_necessity_meaning_is_NOT_declared_as_a_mechanism_yet():
    """⛔ THE RECORDED LIMITATION, pinned so nobody "fixes" the semantics map
    without also teaching the bare-edge leg to carry the mechanism.

    `dependsOn`/`hasPart` MEAN necessity, and the mechanism that prices necessity
    is `coherence`. They are declared `IMPL` here because this map drives the
    OPERATOR-LESS leg, and a bare edge carries its mechanism as its EDGE TYPE —
    `create_direct_edge` accepts `IMPL`/`NAND` only. Measured before the revert:
    declaring them `coherence` made `relation_write._mechanism_for` return None
    and DROPPED nine pack relations on the ingest/capture path (a live write
    regression, #12067 review P1). Coherence IS reachable as an operator
    (`create_operator(op_type="coherence")`, which the extractor mints).

    So this test asserts the CURRENT truth, and names the follow-up. Flipping
    the values here without that carriage is not an improvement — it is the
    regression above.
    """
    for meaning in ("dependsOn", "hasPart"):
        assert SEMANTICS_MECHANISMS[meaning] == "IMPL", meaning
    # The mechanism itself is still first-class: it is a core predicate, it has
    # its own potential, and it is storable.
    assert "coherence" in CORE_PREDICATES
    assert mechanism_rel_type("coherence") == "IMPL"
    for meaning in ("supports", "mutuallySupportive", "addresses", "causes"):
        assert SEMANTICS_MECHANISMS[meaning] == "IMPL", meaning
    for meaning in ("refutes", "mutuallyExclusive"):
        assert SEMANTICS_MECHANISMS[meaning] == "NAND", meaning


def test_the_pack_leg_refuses_coherence_until_the_carriage_exists():
    """⛔ A pack DECLARATION is not a write path (#12067 review round 4, P2).

    A pack `relation`/`relationTemplate` mechanism becomes a BARE edge, and a
    bare edge carries its mechanism AS ITS EDGE TYPE — `IMPL`/`NAND` only. An
    editor that accepted `coherence` here let a pack LOAD carrying a value
    `relation_write` drops (or overrides through `SEMANTICS_MECHANISMS`), which
    reads exactly like shipped. The validators therefore stay at the CARRIABLE
    set, and the message says why — pinning the reason, not the spelling.

    Flipping this test to pass without teaching the bare-edge leg to carry the
    mechanism is the #12067 follow-up, not a fix.
    """
    from tortoise.pack_registry import CARRIABLE_PACK_MECHANISMS, PackRegistry

    assert frozenset({"IMPL", "NAND"}) == CARRIABLE_PACK_MECHANISMS
    errors = PackRegistry("/tmp/nonexistent")._validate({
        "namespace": "dev", "name": "Dev",
        "ontology": {
            "extends": "core",
            "objectKinds": ["issue", "code"],
            "relations": [{
                "predicate": "addresses",
                "fromKind": "dev:issue",
                "toKind": "dev:code",
                "mechanism": "coherence",
            }],
        },
    })
    assert any("must be IMPL or NAND" in e and "coherence" in e
               and "bare-edge" in e for e in errors), errors


def test_the_extraction_contract_states_the_coherence_SLOT_ORDER():
    """⛔ `phi_coherence` is ASYMMETRIC, so an emission contract that does not
    say which endpoint is the condition lets the model invert the mechanism
    silently (#12067 review round 4, P1).

    `phi_coherence(ca, cb)` prices `cb` off `ca`: slot 0 is the NECESSARY
    CONDITION, slot 1 the dependent. The extractor hard-codes
    `direction: unidirectional` for every mechanism (it does not swap), so
    nothing downstream can detect an inversion — an inverted pair looks like a
    perfectly ordinary loss flowing the wrong way. The contract therefore has to
    state the order, next to the op_type it governs.
    """
    from tortoise.extractor_v2 import OUTPUT_CONTRACT

    head = OUTPUT_CONTRACT[:OUTPUT_CONTRACT.index('"relations":')]
    op_block = head[head.index('"operators":'):]
    assert "NECESSARY CONDITION" in op_block, op_block
    assert "src" in op_block and "NECESSARY" in op_block.upper(), op_block


def test_the_mechanism_never_reaches_the_cypher_rel_position(monkeypatch):
    """⛔ THE SLOT IS THE STORAGE EDGE, and this is the pin (#12067 review P0).

    `_read_message`/`_write_message` interpolate `rel_type` into
    `MATCH (o)-[r:{rel_type}]->(c)` and pass the operator's `op_type` there. For
    IMPL/NAND that is accidentally right, because the mechanism IS the rel type.
    A `coherence` operator writes an `:IMPL` edge, so passing the MECHANISM made
    every read return (0, 0) and every write match 0 rows: the factor priced a
    belief while its message was silently dropped — and in cache mode the flush
    loop (which groups by rel type) never persisted or journaled it either.

    Pinned at the seam rather than through a number, because no assertion on a
    confidence value can see a message that was never stored.
    """
    seen: list[str] = []
    real_read = TortoiseEP._read_message
    real_write = TortoiseEP._write_message

    def _spy_read(self, op_id, claim_id, rel_type="IMPL"):
        seen.append(rel_type)
        return real_read(self, op_id, claim_id, rel_type)

    def _spy_write(self, op_id, claim_id, ma, mb, rel_type="IMPL"):
        seen.append(rel_type)
        return real_write(self, op_id, claim_id, ma, mb, rel_type)

    monkeypatch.setattr(TortoiseEP, "_read_message", _spy_read)
    monkeypatch.setattr(TortoiseEP, "_write_message", _spy_write)

    _mean_after("coherence", HELD, HELD)
    assert seen, "the factor addressed no message slot at all"
    assert set(seen) == {"IMPL"}, seen

    seen.clear()
    _mean_after("NAND", HELD, HELD)
    assert set(seen) == {"NAND"}, seen


def test_the_three_potentials_are_distinct():
    """A mechanism that reuses another's potential is not a mechanism — it is a
    second name for one behaviour, which is how meanings get priced as if they
    were the same relation (#12067's premise)."""
    assert len({id(phi_impl), id(phi_nand), id(phi_coherence)}) == 3


# ── property 1+2: coherence propagates loss, does not manufacture gain ───

def test_coherence_one_way_propagates_loss():
    """A falls ⇒ B falls. Swept over A's strength, in ONE direction: the target
    (B) must be strictly lower the weaker the necessary condition is."""
    deltas = [_delta("coherence", _beta(conf), HELD) for conf in CONFS]
    assert all(d < 0 for d in deltas), deltas
    # Monotone: a weaker A must never produce a SMALLER loss than a stronger A.
    ordered = [_delta("coherence", _beta(conf), HELD) for conf in sorted(CONFS)]
    assert ordered == sorted(ordered), ordered
    # The extreme case must be a materially larger loss than the mild one.
    assert abs(ordered[0]) > abs(ordered[-1]), ordered


def test_coherence_does_not_raise_when_the_condition_is_present():
    """Necessary is not sufficient: A being present must not push B UP.

    Asserted against B's OWN baseline, which is the only formulation that is not
    trivially true: `delta <= 1e-12` for EVERY A, i.e. coherence never adds
    confidence — and the fully-present case is the smallest charge, not a gain.
    """
    deltas = {conf: _delta("coherence", _beta(conf), HELD) for conf in CONFS}
    assert all(d <= 1e-12 for d in deltas.values()), deltas
    # The present case is far weaker than the absent case — the mechanism is
    # about loss, not about a symmetric pull.
    assert abs(deltas[0.909]) < 0.2 * abs(deltas[0.05]), deltas


def test_coherence_survives_the_direction_gate():
    """`->` moves the target only; `<->` also moves the source (mutual
    dependence). A direction that flows NOTHING must not move anything — the
    `-` leg is what distinguishes an assertion of relation from a transfer."""
    assert _delta("coherence", _beta(0.05), HELD, direction="->") < 0
    assert _delta("coherence", _beta(0.05), HELD, direction="<->") < 0
    # `-`: exists, transfers nothing.
    assert _delta("coherence", _beta(0.05), HELD, direction="-") == pytest.approx(0.0)


def _raw_message(mechanism: str, source: tuple[float, float],
                 target: tuple[float, float]) -> tuple[float, float]:
    """The UNBOOSTED message the factor would write, computed from the same
    pieces `ep.py` uses (the real potentials, the real natural-parameter
    conversion) — no boost applied."""
    from tortoise.ep import _MECHANISM_POTENTIALS
    from tortoise.quadrature import moments_to_beta, tilted_moments

    ep = _ep_stub()
    _, mom_b = tilted_moments(*source, *target, 1.0,
                              _MECHANISM_POTENTIALS[mechanism], 8)
    a2, b2 = moments_to_beta(*mom_b)
    cav = ep._natural_from_beta(*target)
    new = ep._natural_from_beta(a2, b2)
    return (new[0] - cav[0], new[1] - cav[1])


def test_the_likelihood_boost_is_IMPL_ONLY():
    """⛔ The `#855` proportional boost is SUPPORT-only, and it must stay that
    way for the THIRD mechanism (#12067 review, P2 — no test could see it).

    The boost multiplies the message by `1 + 2/max(α+β−1, 1)`, so on a NEUTRAL
    target (Beta(1,1)) it is exactly 3×. A revert of `if op_type != "IMPL"` to
    the old `if op_type == "NAND"` would boost coherence's LOSS message by 3× on
    exactly this fixture, turning a necessary-condition penalty into the #855
    crater — and every property test still passes, because the boost is constant
    across a sweep. So the assertion is on the MULTIPLIER: the written message,
    measured against the same computation with no boost.
    """
    msg = _msg_after("coherence", HELD, NEUTRAL)
    raw = _raw_message("coherence", HELD, NEUTRAL)
    assert msg == pytest.approx(raw, rel=1e-9), (msg, raw)

    # ... and the SUPPORT mechanism IS boosted by exactly 3× at Beta(1,1), so the
    # test above is not passing because the boost is applied to nobody.
    impl = _msg_after("IMPL", HELD, NEUTRAL)
    raw_impl = _raw_message("IMPL", HELD, NEUTRAL)
    assert impl == pytest.approx((3.0 * raw_impl[0], 3.0 * raw_impl[1]),
                                 rel=1e-9), (impl, raw_impl)

    # NAND is excluded too (the arm the rule was originally written for).
    nand = _msg_after("NAND", HELD, NEUTRAL)
    raw_nand = _raw_message("NAND", HELD, NEUTRAL)
    assert nand == pytest.approx(raw_nand, rel=1e-9), (nand, raw_nand)


def _msg_after(mechanism, source, target):
    ep = _ep_stub(source, target)
    ep._update_factor("op", mechanism, ["A", "B"], weight=1.0, direction="->")
    return ep._msg_cache.get(("op", "B", mechanism_rel_type(mechanism)),
                             (0.0, 0.0))


def test_coherence_reverse_direction_REVERSES_the_slots():
    """⛔ The potential is NOT symmetric, so `<-` cannot be "write the other
    endpoint's message" (#12067 review, P1).

    `phi_coherence(ca, cb) = exp(-w*(1-ca)*cb)` names slot 0 the NECESSARY
    CONDITION and slot 1 the DEPENDENT. Reading `<-` as the symmetric potentials
    need it published only the contrapositive — measured: the source gained
    +0.0059 and the dependent moved 0.0, i.e. the row's mechanism was inverted
    and never propagated loss. `<-` therefore swaps the slots: the SECOND input
    is the necessary condition, the FIRST is the one that falls.
    """
    # `<-` : B is the condition, A is the dependent.
    src, tgt = _means_after("coherence", HELD, _beta(0.05), direction="<-")
    assert src < HELD[0] / (HELD[0] + HELD[1]) - 1e-4, (src, tgt)
    assert tgt == pytest.approx(_beta(0.05)[0] / (_beta(0.05)[0] + _beta(0.05)[1]))

    # `->` : A is the condition, B is the dependent — the mirror image.
    src2, tgt2 = _means_after("coherence", _beta(0.05), HELD, direction="->")
    assert tgt2 < HELD[0] / (HELD[0] + HELD[1]) - 1e-4, (src2, tgt2)
    assert src2 == pytest.approx(_beta(0.05)[0] / (_beta(0.05)[0] + _beta(0.05)[1]))


# ── property 3: attack never raises ──────────────────────────────────────

def test_attack_never_raises_at_any_attacker_strength():
    for conf in CONFS:
        for target in (NEUTRAL, WEAK, HELD):
            d = _delta("NAND", _beta(conf), target)
            assert d <= 0, (conf, target, d)
    # ... and it is not inert: a strong attacker on a neutral target moves it.
    assert _delta("NAND", _beta(0.95), NEUTRAL) < -1e-3


# ── property 4: support — stated with its exact boundary ─────────────────

def test_support_raises_a_target_below_its_source():
    for conf in (0.60, 0.70, 0.80, 0.909, 0.95):
        d = _delta("IMPL", _beta(conf), WEAK)
        assert d > 0, (conf, d)


def test_support_LOWERS_when_the_source_sits_BELOW_the_target():
    """⛔ THE RECORDED GAP, pinned so it cannot be mistaken for compliance.

    Acceptance #4 on #12067 says "support never lowers". Measured on the code as
    it stands, that is FALSE: `phi_impl` is a DIFFERENCE coupling, so a weak
    supporter drags its target DOWN toward itself (a=0.30 → B(0.909) loses
    confidence). This test asserts the current behaviour and names it as the
    open decision, because the alternative — silently rewriting the assertion to
    "support never lowers *when the source is stronger*" and calling #4 met —
    would be a false PASS on an acceptance line.

    The fix is a change of `phi_impl`'s FORM (a one-sided coupling), which
    contradicts the settled "both forms are intended" finding on #855/#11935 and
    is therefore a reopen, not a lane change. L1c (equal-strength symmetry at a
    neutral claim) has the same root and is measured at 19 % on the current
    shapes.
    """
    for conf in (0.30, 0.20, 0.05):
        d = _delta("IMPL", _beta(conf), HELD)
        assert d < 0, (conf, d)


# ── property 5: the closed set, AT THE WRITE DOOR ────────────────────────

@pytest.fixture()
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "mech.db"))
    yield s
    s.close()


def _points(sdk, n=2):
    return [sdk.create_point("statement", f"claim {i}")["id"] for i in range(n)]


def _contents(sdk, *ids):
    """The stored ``content`` of each id — what a ref's point must repeat for the
    writer to dedup onto it (#12067)."""
    out = []
    for pid in ids:
        rows = sdk._get_proj().g.query(
            "MATCH (p:Point {id:$id}) RETURN p.content",
            params={"id": pid}).result_set
        out.append(rows[0][0])
    return out


@pytest.mark.parametrize("first,second", [
    ("IMPL", "NAND"), ("NAND", "IMPL"),
    ("IMPL", "coherence"), ("coherence", "IMPL"),
    ("NAND", "coherence"), ("coherence", "NAND"),
])
def test_refuses_a_second_mechanism_on_one_pair(sdk, first, second):
    """EVERY ordered combination, not just IMPL-vs-NAND: the closure is about
    the SET, so a third mechanism must inherit it for free."""
    a, b = _points(sdk)
    sdk.create_operator(first, a, [b])
    with pytest.raises(ValueError) as exc:
        sdk.create_operator(second, a, [b])
    message = str(exc.value)
    # It must NAME BOTH, or the caller cannot act on it. ⛔ Pin the PHRASED
    # prefix, not bare membership: the message's remediation clause lists all
    # three mechanisms, so `second in message` passed whether or not the
    # conflicting pair was named (a vacuous assertion, #12067 review P2).
    assert f"already carries the {first!r} mechanism" in message, message
    assert f"cannot also carry {second!r}" in message, message
    assert a in message or b in message, message


def test_the_same_mechanism_twice_is_not_two_mechanisms(sdk):
    """Re-stating ONE mechanism over a pair is a duplicate, not a second
    mechanism — the closure must not refuse it (the ingest path re-issues
    operators by design)."""
    a, b = _points(sdk)
    sdk.create_operator("IMPL", a, [b])
    sdk.create_operator("IMPL", a, [b])


def test_direction_is_not_the_flag(sdk):
    """Direction says how FAR an assertion reaches, never what it MEANS. Two
    opposite-facing wires over one pair are still one pair with two mechanisms
    (#12067's "direction as the only flag")."""
    a, b = _points(sdk)
    sdk.create_operator("IMPL", a, [b], direction="->")
    with pytest.raises(ValueError):
        sdk.create_operator("NAND", b, [a], direction="->")


def test_a_part_whole_opcode_is_not_a_second_mechanism(sdk):
    """`composedOf`/`contains` declare NO mechanism (§8's part/whole op_types
    map to `hasPart` storage), so pairing one with a NAND is not two mechanisms
    and must stay writable."""
    a, b = _points(sdk)
    sdk.create_operator("NAND", a, [b])
    sdk.create_operator("composedOf", a, [b])


def test_a_deleted_operator_frees_the_pair(sdk):
    """Deleting the operator is the only route to re-mechanise a pair, so it
    must actually free it.

    ⛔ The cost, stated rather than hidden: an operator has NO retract path
    today — `retract_point` refuses operators ("operators only go live via the
    R16 endpoint gate") and `update_point` refuses a non-draft→live transition.
    So `delete_point` is the only route, which loses the operator's mitigation
    history. A sanctioned "replace this pair's mechanism" verb does not exist;
    that gap is real and is filed as a follow-up rather than absorbed here. This
    test pins the REACHABLE behaviour so the refusal's own remedy text is true.
    """
    a, b = _points(sdk)
    first = sdk.create_operator("IMPL", a, [b])
    sdk.delete_point(first["id"])
    sdk.create_operator("NAND", a, [b])


def test_nary_pairwise_overlap_is_refused(sdk):
    """The pair is the unit, not the operator: a 3-input coherence that already
    covers {a,b} must refuse an IMPL over {a,b}."""
    a, b, c = _points(sdk, 3)
    sdk.create_operator("coherence", a, [b, c])
    with pytest.raises(ValueError):
        sdk.create_operator("IMPL", a, [b])
    # ... but a pair with no overlap is free.
    d, e = _points(sdk)
    sdk.create_operator("IMPL", d, [e])


def test_a_direct_edge_is_the_other_arm_of_the_same_invariant(sdk):
    """An operator-less edge carries its mechanism as the REL itself, so one pair
    would price BOTH functional forms against one belief — a confidence that
    looks exactly like strong evidence. Refusing is strictly better than that.

    (Not "they share one message slot": that is true only for coherence beside a
    bare `:IMPL`, and it is the least general of the reasons — ONTOLOGY states
    the slot geometry correctly, so the mechanism of harm is what this names.)"""
    a, b = _points(sdk)
    sdk.create_direct_edge("IMPL", a, b)
    with pytest.raises(ValueError):
        sdk.create_operator("NAND", a, [b])


def test_the_direct_edge_door_refuses_TOO(sdk):
    """⛔ The invariant needs BOTH doors, and it had only one (#12067 review
    P1): `create_operator` refused the second mechanism, but `create_direct_edge`
    wrote a bare edge beside an existing operator without a check — so
    `create_operator(IMPL)` then `create_direct_edge("NAND")` produced exactly
    the two-mechanism pair the refusal exists to make unwritable. Both directions
    are asserted: the door must not care which mechanism came first, and it must
    not care that one is stored as an edge and the other as a node.
    """
    a, b = _points(sdk)
    sdk.create_operator("IMPL", a, [b])
    with pytest.raises(ValueError) as exc:
        sdk.create_direct_edge("NAND", a, b)
    assert "already carries" in str(exc.value), str(exc.value)

    # ... the reverse order, and through the third mechanism (a coherence
    # operator is STORED as `:IMPL`, so a bare `:IMPL` beside it is the same
    # two-mechanism pair — the pre-fix probe compared it against its own
    # storage type and let it through).
    c, d = _points(sdk)
    sdk.create_direct_edge("IMPL", c, d)
    with pytest.raises(ValueError):
        sdk.create_operator("coherence", c, [d])

    # A pair carrying ONE mechanism stays writable through the same door — the
    # guard must not become a blanket refusal (the #8175 false-block class).
    e, f = _points(sdk)
    sdk.create_operator("IMPL", e, [f])
    sdk.create_direct_edge("IMPL", e, f)


def test_a_non_point_endpoint_is_still_checked(sdk):
    """⛔ The pair is a pair of IDS, not a pair of `:Point`s (#12067 review P1).

    `create_operator` admits any of :Point/:Subject/:Object/:Event (#7902), but
    the closed-set probe hardcoded `(p:Point)`/`(q:Point)` IN THE PATTERN, so a
    pair whose endpoint was a :Subject never matched and the second mechanism was
    written. Asserted on a Subject endpoint because that is the shape the #7902
    tests mint.
    """
    a = sdk.create_point("statement", "claim")["id"]
    subj = sdk.create_subject("Entity", "some entity")["id"]
    sdk.create_operator("NAND", a, [subj])
    with pytest.raises(ValueError):
        sdk.create_operator("IMPL", a, [subj])


def test_a_DRAFT_operator_is_part_of_the_population(sdk):
    """⛔ DRAFT is not a free pass (#12067 review round 2, P1).

    The first cut excluded drafts on the reasoning that "a draft prices no
    belief". That is wrong about the lifecycle this repo ships: a draft operator
    is PROMOTED when its endpoints go live, the promotion re-checks nothing, and
    `promote_source=False` is the ordinary posture of the commit/ingest paths —
    so two drafts over one pair become two LIVE mechanisms on one pair. Measured
    before the fix: both writes were accepted.
    """
    a, b = _points(sdk)
    sdk.create_operator("IMPL", a, [b], promote_source=False)
    with pytest.raises(ValueError):
        sdk.create_operator("coherence", a, [b], promote_source=False)
    # ... and the promotion really does keep both live, which is why the refusal
    # has to happen at the WRITE, not at the promotion.
    sdk._promote_incident_operators(sdk._get_proj(), a, "2026-01-01T00:00:00Z")
    ops = sdk._get_proj().g.query(
        "MATCH (o:Point) WHERE o.is_operator = true "
        "RETURN o.id, o.op_type").result_set
    assert len(ops) == 1, ops


def test_coherence_has_its_OWN_direction_default(sdk):
    """⛔ `coherence` must not inherit IMPL's `bidirectional` (#12067 review round
    2, P2): a direction-omitting necessity write would be priced as MUTUAL
    necessity. `->` is the reading the mechanism means — the FIRST input is the
    condition, the second the dependent."""
    a, b = _points(sdk)
    op = sdk.create_operator("coherence", a, [b])
    stored = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.direction",
        params={"id": op["id"]}).result_set
    assert stored and stored[0][0] == "->", stored
    # IMPL keeps the mutual default it has always had.
    c, d = _points(sdk)
    op2 = sdk.create_operator("IMPL", c, [d])
    stored2 = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.direction",
        params={"id": op2["id"]}).result_set
    assert stored2 and stored2[0][0] == "bidirectional", stored2


def test_the_derived_over_asserted_guard_covers_the_third_mechanism(sdk):
    """⛔ The #2351 guard enumerated IMPL/NAND only, so the SAME derived-over-
    asserted write was refused for support and accepted for coherence (#12067
    review round 2, P1) — and with the asymmetric potential's slot swap, the
    downgraded direction then pushed the DERIVED claim into the ASSERTED one."""
    src = sdk.create_point("statement", "src", claim_class="derived")["id"]
    tgt = sdk.create_point("statement", "tgt", claim_class="asserted")["id"]
    for mechanism in ("IMPL", "NAND", "coherence"):
        with pytest.raises(ValueError, match="DERIVED"):
            sdk.create_operator(mechanism, src, [tgt])


def test_an_ingest_retry_dedups_a_coherence_operator(sdk):
    """⛔ The ingest dedupe probe read `hasPart` for every non-IMPL/NAND spelling,
    while `create_operator` stores coherence on `:IMPL` — so a retried ingest
    minted a SECOND operator over the same pair, double-pricing the factor
    (#12067 review round 2, P1; the same class as the `commit_ops` probe)."""
    a = sdk.create_point("statement", "a")["id"]
    b = sdk.create_point("statement", "b")["id"]
    payload = {"connections": [{"operator": "coherence", "from": a, "to": b}]}
    first = sdk.ingest(payload)
    second = sdk.ingest(payload)
    assert first["created"]["connections"] == 1, first
    assert second["created"]["connections"] == 0, second
    ops = sdk._get_proj().g.query(
        "MATCH (o:Point) WHERE o.is_operator = true "
        "RETURN o.id").result_set
    assert len(ops) == 1, ops


def test_ingest_reports_the_conflict_as_a_VIOLATION_not_a_mid_bundle_abort(sdk):
    """⛔ Phase 1 must know the invariant, or the conflict aborts the bundle
    MID-WRITE (#12067 review round 3, P1).

    `create_operator`'s refusal is a `raise` — right for a direct caller, wrong
    for `ingest`, whose Phase 1 is documented as pure and aggregated. Measured
    before the fix: a payload with a new point, one valid connection and one
    conflicting `coherence` connection COMMITTED the point and the valid
    connection, then raised — no `violations` entry, nothing to skip, and the same
    error on every retry (the partial-mutation class Phase 1 exists to prevent).
    """
    from tortoise.exceptions import BundleValidationError

    a, b = _points(sdk)
    sdk.create_operator("IMPL", a, [b])
    payload = {
        "points": [{"content": "a brand new claim, queued behind the conflict"}],
        "connections": [{"operator": "coherence", "from": a, "to": b}],
    }
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest(payload)
    violations = getattr(exc.value, "violations", []) or []
    assert any("SECOND mechanism" in (v.get("message") or "") for v in violations), (
        violations)
    # NOTHING from the bundle committed — that is the whole point.
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.content = $c RETURN n.id",
        params={"c": "a brand new claim, queued behind the conflict"}).result_set
    assert rows == [], rows


def test_an_INTRA_BUNDLE_conflict_is_a_VIOLATION(sdk):
    """⛔ Phase 1 reads the GRAPH, so a conflict created by the bundle ITSELF was
    invisible (#12067 review round 4, P1 — measured on this head).

    `{"connections": [{operator: IMPL, a→b}, {operator: coherence, a→b}]}`
    passed validation, committed the points and the FIRST operator, then raised
    from the door — a bare `ValueError` with no `violations` entry to skip and the
    same failure on every retry. The pair map is now accumulated across the
    bundle, so the second statement of one pair in a different mechanism is what
    Phase 1 reports.
    """
    from tortoise.exceptions import BundleValidationError

    a, b = _points(sdk)
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest({"connections": [
            {"operator": "IMPL", "from": a, "to": b},
            {"operator": "coherence", "from": a, "to": b},
        ]})
    violations = getattr(exc.value, "violations", []) or []
    assert any("SECOND mechanism" in (v.get("message") or "")
               and "THIS bundle" in (v.get("message") or "")
               for v in violations), violations
    # ⛔ The message must NAME the prior connection and its mechanism, not just
    # say "a conflict" — they are what a caller acts on, and a swapped unpack in
    # this arm printed "connections[IMPL] … the 0 mechanism" (measured, #12067
    # review round 5, P1/P2). Pinned because the assertion above passes on it.
    msg = next(v["message"] for v in violations if "THIS bundle" in v["message"])
    assert "connections[0]" in msg, msg
    assert "'IMPL'" in msg, msg
    # Nothing from the bundle committed — not the first operator either.
    ops = sdk._get_proj().g.query(
        "MATCH (o:Point) WHERE o.is_operator = true RETURN count(o)"
    ).result_set
    assert ops[0][0] == 0, ops


def test_a_REF_endpoint_resolves_to_its_LANDING_node(sdk):
    """⛔ A ref passed straight into the reader is a SILENT no-op, because the
    reader seeks `p.id IN $ids` and a ref is not an id (#12067 review round 4,
    P1 — measured).

    The writer resolves a ref to the node its point DEDUPS onto, so when both refs
    fold onto a pair that already carries a mechanism the door raised a bare
    `ValueError` after the dedup write had landed. Phase 1 now resolves each ref
    with the writer's own resolver (`_resolve_dedup_hit`) before judging the pair.
    """
    from tortoise.exceptions import BundleValidationError

    a, b = _points(sdk)
    sdk.create_operator("IMPL", a, [b])
    ca, cb = _contents(sdk, a, b)
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest({
            "points": [{"content": ca, "ref": "rA"},
                       {"content": cb, "ref": "rB"}],
            "connections": [{"operator": "coherence", "from": "rA", "to": "rB"}],
        })
    violations = getattr(exc.value, "violations", []) or []
    assert any("SECOND mechanism" in (v.get("message") or "")
               for v in violations), violations


def test_an_NARY_sibling_pair_is_judged_at_Phase_1(sdk):
    """⛔ The DOOR judges every 2-subset of the operator's endpoint set, so Phase 1
    must too (#12067 review round 5, P1 — measured).

    `create_operator` tests an existing operator's endpoints against
    `set(inputs)` with `len(shared) >= 2`, so for `coherence a→[b, c]` the pair
    `(b, c)` is a pair it refuses — but Phase 1 derived only `(a, b)` and `(a, c)`.
    A conflict on the sibling pair therefore passed validation, committed the
    operator, and then raised mid-write with no violation to skip.
    """
    from tortoise.exceptions import BundleValidationError

    a, b, c = _points(sdk, 3)
    sdk.create_operator("IMPL", b, [c])
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest({"connections": [
            {"operator": "coherence", "from": a, "to": [b, c]}]})
    violations = getattr(exc.value, "violations", []) or []
    assert any("SECOND mechanism" in (v.get("message") or "")
               for v in violations), violations
    # The conflict is on the SIBLING pair, so nothing new was written.
    ops = sdk._get_proj().g.query(
        "MATCH (o:Point) WHERE o.is_operator = true "
        "AND o.op_type = 'coherence' RETURN count(o)").result_set
    assert ops[0][0] == 0, ops


def test_two_REFS_that_fold_onto_one_minted_node_are_ONE_pair(sdk):
    """⛔ `create_point` folds on `key` and on `(pointKind, content_hash)`, so two
    `points` items repeating the same content are ONE node — and two connections
    naming them are two mechanisms on one pair (#12067 review round 5, P1).

    Judged per REF, they were two different bundle-local names: the intra-bundle
    arm never fired, the graph arm was skipped (nothing to look up), and the
    bundle aborted MID-WRITE with the points already committed. The grouping is the
    writer's own fold (`_bundle_mint_groups`), not an invention.
    """
    from tortoise.exceptions import BundleValidationError

    z = sdk.create_point("statement", "shared target")["id"]
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest({
            "points": [{"content": "same claim, twice", "ref": "r1"},
                       {"content": "same claim, twice", "ref": "r2"}],
            "connections": [
                {"operator": "IMPL", "from": "r1", "to": z},
                {"operator": "coherence", "from": "r2", "to": z},
            ],
        })
    violations = getattr(exc.value, "violations", []) or []
    assert any("THIS bundle" in (v.get("message") or "")
               for v in violations), violations
    # Nothing minted: the dedup write the old code allowed to land is gone.
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.content = 'same claim, twice' RETURN count(p)"
    ).result_set
    assert rows[0][0] == 0, rows


def test_a_bare_edge_on_an_NARY_SUBPAIR_is_refused_by_BOTH_doors(sdk):
    """⛔ The door's rule is "share ≥2 endpoints", and the bare-edge arm must use
    it too (#12067 review round 6, P2 — measured).

    The bare arm required the bare edge's endpoints to EQUAL the whole endpoint
    set, so a bare `NAND b→c` sat beside `coherence a→[b, c]` — two mechanisms on
    the pair (b, c) — while the mirrored `ingest` refused the same state. The
    doc's claim ("one shared reader, one rule") has to be true of both arms, so
    this pins the SAME shape through `create_operator` and through `ingest`.
    """
    from tortoise.exceptions import BundleValidationError

    a, b, c = _points(sdk, 3)
    sdk.create_direct_edge("NAND", b, c)
    with pytest.raises(ValueError) as exc:
        sdk.create_operator("coherence", a, [b, c])
    assert "NAND" in str(exc.value), str(exc.value)
    # ... and the ingest door reports the mirror instead of raising mid-bundle.
    with pytest.raises(BundleValidationError) as exc2:
        sdk.ingest({"connections": [
            {"operator": "coherence", "from": a, "to": [b, c]}]})
    violations = getattr(exc2.value, "violations", []) or []
    assert any("direct edge" in (v.get("message") or "") for v in violations),         violations


def test_a_bare_edge_conflict_names_the_EDGE_not_an_operator(sdk):
    """⛔ The two arms need different words (#12067 review round 6, P2).

    A dropped kind-aware branch printed `it already carries the 'NAND' mechanism
    (operator None)` — telling the caller to look at an operator that does not
    exist, when the remedy is to remove an edge.
    """
    from tortoise.exceptions import BundleValidationError

    a, b = _points(sdk)
    sdk.create_direct_edge("NAND", a, b)
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest({"connections": [
            {"operator": "IMPL", "from": a, "to": b}]})
    msgs = [v.get("message") or "" for v in
            (getattr(exc.value, "violations", []) or [])]
    assert any("a direct edge" in m for m in msgs), msgs
    assert not any("operator None" in m for m in msgs), msgs


def test_a_key_hit_DISCARDS_the_content_so_it_cannot_bridge(sdk):
    """⛔ Order-aware folding (#12067 review round 6, P2 — a FALSE REFUSAL).

    `create_point` resolves the key FIRST and, on a key hit, DISCARDS the incoming
    content: it is never stored, so it never becomes a node. Grouping on the item's
    DECLARED content made it a bridge, so
    `{key: K, content: alpha}`, `{key: K, content: beta}`, `{content: beta}`
    collapsed onto ONE group and `ingest` refused a bundle the writer handles —
    two nodes (K/alpha, beta), two mechanisms, no conflict at all.
    """
    z1 = sdk.create_point("statement", "target one")["id"]
    z2 = sdk.create_point("statement", "target two")["id"]
    res = sdk.ingest({
        "points": [
            {"key": "K", "content": "alpha", "ref": "r1"},
            {"key": "K", "content": "beta", "ref": "r2"},
            {"content": "beta", "ref": "r3"},
        ],
        "connections": [
            {"operator": "IMPL", "from": "r1", "to": z1},
            {"operator": "coherence", "from": "r3", "to": z1},
            {"operator": "IMPL", "from": "r2", "to": z2},
        ],
    })
    assert res is not None
    # The writer minted TWO points (K/alpha and beta) — the shape the grouping
    # must model.
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.key = 'K' OR p.content = 'beta' "
        "RETURN p.key, p.content").result_set
    assert len(rows) == 2, rows


def test_an_absorbed_coherence_leg_lands_on_the_STORAGE_edge(sdk):
    """⛔ The ingest partial-absorb leg mapped every non-IMPL/NAND op_type to
    `hasPart`, so an absorbed coherence input landed as a structural edge the
    factor builder never reads: ingest answered SUCCESS with a warning and the leg
    was INERT (#12067 review round 3, P1 — the same class as the two replay maps).
    """
    a, b = _points(sdk)
    c = sdk.create_point("statement", "third input")["id"]
    sdk.create_operator("coherence", a, [b])
    # The absorb arm: same operator, widened input set.
    sdk.ingest({"connections": [
        {"operator": "coherence", "from": a, "to": [b, c]}]})
    rels = sdk._get_proj().g.query(
        "MATCH (o:Point {is_operator:true})-[r]->(t:Point) "
        "RETURN o.id, type(r), t.id").result_set
    typed = {(rel, tgt) for _oid, rel, tgt in rels if rel in ("IMPL", "NAND")}
    assert ("IMPL", c) in typed, rels
    assert not any(rel == "hasPart" for _oid, rel, _t in rels), rels


# ── END TO END: the mechanism must move a belief through the RUN, not just
#          through `_update_factor`. A mechanism that is priced correctly and
#          never reached is INERT — the failure #8169 records (a relation that
#          matched nowhere on the message path). ────────────────────────────

def _run(sdk):
    """One propagation run over every operator, returning {claim: confidence}."""
    proj = sdk._get_proj()
    op_ids = [r[0] for r in proj.g.query(
        "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id").result_set]
    ev = {r[0]: (r[1], r[2]) for r in proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta").result_set}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3, evidence=ev)
    ep.run(op_ids, max_hops=2)
    return {r[0]: r[1] for r in proj.g.query(
        "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN n.id, n.confidence"
    ).result_set}


def _evidence(sdk, pid, alpha, beta):
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=$al, n.ep_beta=$be, "
        "n.baseline_set=true", params={"id": pid, "al": alpha, "be": beta})


def test_coherence_reaches_the_run_loop_and_lowers_its_dependent(sdk):
    """A well-held dependent whose necessary condition is ABSENT falls, against
    a control dependent whose condition is PRESENT. Two claims, one run, one
    difference — the only shape that cannot be satisfied by an inert mechanism
    (an unreached factor leaves both at their baselines and the gap is 0)."""
    cond_absent = sdk.create_point("statement", "condition absent")["id"]
    cond_present = sdk.create_point("statement", "condition present")["id"]
    dep_a = sdk.create_point("statement", "dependent, condition absent")["id"]
    dep_p = sdk.create_point("statement", "dependent, condition present")["id"]
    sdk.create_operator("coherence", cond_absent, [dep_a])
    sdk.create_operator("coherence", cond_present, [dep_p])
    _evidence(sdk, cond_absent, 0.5, 9.5)     # 0.05 — absent
    _evidence(sdk, cond_present, 10.0, 1.0)   # 0.909 — present
    _evidence(sdk, dep_a, 10.0, 1.0)          # both dependents equally held
    _evidence(sdk, dep_p, 10.0, 1.0)

    res = _run(sdk)
    assert res[dep_a] < res[dep_p], (res[dep_a], res[dep_p])
    # ... and the gap is a real movement, not float noise.
    assert res[dep_p] - res[dep_a] > 1e-4, (res[dep_a], res[dep_p])


def test_support_and_attack_move_a_neutral_claim_the_same_WAY_they_should(sdk):
    """End-to-end direction check for the two pre-existing mechanisms: a
    supported neutral claim settles HIGHER than an attacked one. Pinned at the
    level of the RUN so a dispatch that swapped the potentials is caught."""
    sup_src = sdk.create_point("statement", "supporting evidence")["id"]
    atk_src = sdk.create_point("statement", "counter-evidence")["id"]
    sup_tgt = sdk.create_point("statement", "supported claim")["id"]
    atk_tgt = sdk.create_point("statement", "attacked claim")["id"]
    sdk.create_operator("IMPL", sup_src, [sup_tgt])
    sdk.create_operator("NAND", atk_src, [atk_tgt])
    for pid in (sup_src, atk_src):
        _evidence(sdk, pid, 10.0, 1.0)   # equally strong on both sides
    for pid in (sup_tgt, atk_tgt):
        _evidence(sdk, pid, 1.0, 1.0)    # equally neutral claims

    res = _run(sdk)
    assert res[sup_tgt] > 0.5 > res[atk_tgt], (res[sup_tgt], res[atk_tgt])


def test_coherence_is_not_support_in_the_RISING_direction(sdk):
    """The discriminating pair the module docstring promises.

    A claim sitting LOW (0.2) under a condition/evidential source held HIGH
    (0.909). SUPPORT pulls it up — that is what "evidence for" does. COHERENCE
    must not: a present necessary condition removes a downward force, it never
    adds an upward one. A wrong mechanism mapping (`coherence` priced as
    `phi_impl`) collapses this difference and the assertion fails.

    Measured against the claim's OWN baseline (0.2), not against a control node:
    a claim touched by no factor is outside the run's affected set and carries no
    written confidence at all, so a control would assert on an absent key.
    """
    src_sup = sdk.create_point("statement", "evidential source")["id"]
    src_coh = sdk.create_point("statement", "present precondition")["id"]
    t_sup = sdk.create_point("statement", "low claim, supported")["id"]
    t_coh = sdk.create_point("statement", "low claim, coerced")["id"]
    sdk.create_operator("IMPL", src_sup, [t_sup])
    sdk.create_operator("coherence", src_coh, [t_coh])
    for pid in (src_sup, src_coh):
        _evidence(sdk, pid, 10.0, 1.0)
    baseline = 0.2
    for pid in (t_sup, t_coh):
        _evidence(sdk, pid, 2.0, 8.0)      # mean 0.2

    res = _run(sdk)
    # Coherence never lifts it above its own baseline ...
    assert res[t_coh] <= baseline + 1e-6, (res[t_coh], baseline)
    # ... while support lifts the identical claim materially.
    assert res[t_sup] > baseline + 1e-3, (res[t_sup], baseline)
    assert res[t_sup] > res[t_coh] + 1e-3, (res[t_sup], res[t_coh])


# ── ROUND 4: the doors that could still let the third mechanism be SILENT ──
#          Each test below was written from a MEASURED defect, not from the
#          design: the assertions name the number/behaviour that was observed
#          before the fix.

class _OneRelationRegistry:
    """The minimum `pack_registry._declared_relation_rows` reads of a catalog."""

    def __init__(self, rows):
        self._rows = rows

    def list_relations(self):
        return list(self._rows)


def test_a_pack_relation_declaring_coherence_is_NOT_silently_written_as_IMPL():
    """⛔ THE PACK/CAPTURE ARM OF THE SAME SILENCE (#12067 review round 4, P1).

    A pack relation may declare ``mechanism: coherence`` only if the catalog
    reaches the write leg WITHOUT the manifest validator (which refuses it — see
    ``test_the_pack_leg_refuses_coherence_until_the_carriage_exists``); this arm
    is the defence in depth. The operator-less direct-edge leg that CONSUMES a
    pack relation carries only ``IMPL``/``NAND`` — and
    ``relation_write._mechanism_for`` used to
    scan the declaration list for the first *carriable* mechanism, so a row
    declaring ``coherence`` was replaced by whatever its ``semantics`` mapped to:

        measured, `{predicate: gatedBy, mechanism: coherence, semantics:
        dependsOn}` → a plan with ONE edge, ``write_path='direct'``,
        mechanism ``IMPL``, and ZERO drops — a pack's stated NECESSITY priced as
        SUPPORT, which is exactly the inheritance the closed set exists to make
        impossible. And nothing anywhere said so.

    The fix refuses the substitution: the declaration's OWN mechanism is the only
    candidate, so this relation is DROPPED, with a reason that names it.
    """
    from tortoise.relation_write import plan_relation_edges

    registry = _OneRelationRegistry([
        {"pack": "probe", "predicate": "gatedBy", "mechanism": "coherence",
         "semantics": "dependsOn"},
    ])
    edges, drops = plan_relation_edges(
        [{"src": "tranche", "dst": "condition", "predicate": "gatedBy",
          "semantics": "dependsOn", "pack": "probe"}],
        {"tranche": [("id-t", "probe:tranche")],
         "condition": [("id-c", "probe:condition")]},
        registry, declared=[])

    assert edges == [], [(e.predicate, e.write_path) for e in edges]
    assert drops, "the relation vanished with no drop record — that IS the defect"
    reason = drops[0].reason
    # The reason must name the REAL cause. The pre-fix generic text claimed the
    # verb was "neither … nor bound to a mechanism", which is false for this row.
    assert "coherence" in reason, reason
    assert "#12067" in reason, reason
    assert "nor bound to a mechanism" not in reason, reason


def test_a_pack_relation_declaring_IMPL_still_writes(sdk):
    """The guard above must not become a blanket refusal: an IMPL row is
    carriable and must keep landing a direct edge (the #8175 false-block class)."""
    from tortoise.relation_write import plan_relation_edges

    registry = _OneRelationRegistry([
        {"pack": "probe", "predicate": "gatedBy", "mechanism": "IMPL",
         "semantics": "dependsOn"},
    ])
    edges, drops = plan_relation_edges(
        [{"src": "tranche", "dst": "condition", "predicate": "gatedBy",
          "semantics": "dependsOn", "pack": "probe"}],
        {"tranche": [("id-t", "probe:tranche")],
         "condition": [("id-c", "probe:condition")]},
        registry, declared=[])
    assert [e.predicate for e in edges] == ["IMPL"], edges
    assert drops == [], drops


def test_ingest_refuses_two_mechanisms_from_the_SAME_bundle(sdk):
    """⛔ PHASE 1 ASKED THE GRAPH AND NOT ITSELF (#12067 review round 4, P1).

    `_check_pair_mechanisms` compared each connection against the graph, so a
    conflict between two connections IN THE SAME BUNDLE was invisible to it: the
    payload below passed Phase 1 clean, the bundle's point and the FIRST operator
    committed, and the second connection raised `create_operator`'s bare
    `ValueError` — no `violations` entry to skip, and a call that raised having
    left mutations behind. That is the partial-mutation class Phase 1 exists to
    prevent, one layer inside the door the previous round hardened.

    Measured before the fix: `operators on pair == ['IMPL']` and the bundle's own
    point present, on a call that RAISED.

    `_check_duplicates` does not cover it: it only fires on a REVERSED pair.
    """
    from tortoise.exceptions import BundleValidationError

    a, b = _points(sdk)
    payload = {
        "points": [{"content": "queued behind an intra-bundle conflict"}],
        "connections": [
            {"operator": "IMPL", "from": a, "to": b},
            {"operator": "coherence", "from": a, "to": b},
        ],
    }
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest(payload)
    violations = getattr(exc.value, "violations", []) or []
    assert any(("TWO mechanisms" in (v.get("message") or "") or "SECOND mechanism" in (v.get("message") or ""))
               for v in violations), violations
    # NOTHING from the bundle committed — that is the whole point.
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.content = $c RETURN n.id",
        params={"c": "queued behind an intra-bundle conflict"}).result_set
    assert rows == [], rows
    ops = sdk._get_proj().g.query(
        "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id").result_set
    assert ops == [], ops

    # ... and the SAME bundle re-stating ONE mechanism is not a conflict: the
    # ingest path re-issues operators by design (the closure must not refuse it).
    ok = sdk.ingest({"connections": [
        {"operator": "IMPL", "from": a, "to": b},
        {"operator": "IMPL", "from": a, "to": b},
    ]})
    assert ok["created"]["connections"] == 1, ok


def test_the_mechanism_mismatch_message_names_the_THIRD_mechanism(sdk):
    """⛔ AN AGENT-FACING WARNING THAT NAMES THE WRONG MECHANISM (#12067 review
    round 4, P2).

    The `#9963` disclosure's worked examples branched IMPL / else-NAND, so a
    coherence wire fell into the NAND arms. Measured on
    `create_operator("coherence", a, [b], semantics="refutes")`:

        "op_type 'coherence' contradicts … A NAND wire carrying an IMPL meaning
         (e.g. 'supports') LOWERS the target's confidence where the meaning says
         it must RAISE it"

    — wrong twice: the wire is not NAND, and this one was declared NAND. An agent
    acting on it would rewrite a correct coherence wire into a NAND one.
    """
    a, b = _points(sdk)
    warned = sdk.create_operator("coherence", a, [b], semantics="refutes")
    msgs = [w for w in (warned.get("warnings") or [])
            if w.get("code") == "mechanism_mismatch"]
    assert msgs, warned.get("warnings")
    text = msgs[0]["message"]
    assert "A NAND wire" not in text, text
    assert "coherence wire" in text, text
    # The declared mechanism it names must be the one that actually contradicted.
    assert "NAND declaration" in text or "declared NAND" in text.replace(
        "declares 'NAND'", "declared NAND"), text


# ── ROUND 4 continued: the two P1s the third round's fix left behind ──────

def _mean_before(prior):
    return prior[0] / (prior[0] + prior[1])


def test_mutual_coherence_never_RAISES_the_necessary_condition():
    """`<->` IS NOT "THE `->` MESSAGE SENT BOTH WAYS" (#12067 review round 4, P1).

    The round-1 fix swapped the potential's slots for `<-` only, on the sound
    reasoning that `phi_impl`/`phi_nand` are symmetric. `phi_coherence` is not,
    so for `<->` BOTH messages came out of the same one-corner potential and the
    back message was its CONTRAPOSITIVE — "the dependent is present, so raise its
    necessary condition" — which no reading of the mechanism licenses and which
    `docs/ONTOLOGY.md` forbids in words ("a met one never raises either side").

    Measured before the fix (condition Beta(0.5,9.5) = 0.05, dependent
    Beta(10,1) = 0.909): `->` left the condition at 0.050000 while `<->` RAISED
    it to 0.054178 (+8.4 %), and on the live run loop 0.0500 -> 0.0551.

    The route that reaches it by DEFAULT is the one the ONTOLOGY row invites: a
    necessity meaning (e.g. `semantics="hasPart"`) resolves to direction `<->`,
    so a caller who writes the shape the new table names lands here.
    """
    cond, dep = _beta(0.05), HELD
    cond_before, dep_before = _mean_before(cond), _mean_before(dep)

    for direction in ("->", "<-", "<->"):
        src, tgt = _means_after("coherence", cond, dep, direction=direction)
        # the state the two are in is genuinely bad: the dependent falls when
        # the condition is absent (`->` and `<->`), the condition falls when the
        # dependent is the necessary one (`<-`).
        if direction in ("->", "<->"):
            assert tgt < dep_before - 1e-4, (direction, src, tgt)
        if direction == "<-":
            assert src < cond_before - 1e-4, (direction, src, tgt)
        # ... and NOTHING ever RAISES the necessary condition.
        assert src <= cond_before + 1e-12, (
            f"direction {direction!r} RAISED the necessary condition "
            f"{cond_before:.6f} -> {src:.6f}", src, tgt)
    # non-vacuity: on `<->` the condition really does MOVE (downward), so
    # "did not raise" is not passing because the factor is inert.
    src_mutual, _ = _means_after("coherence", cond, dep, direction="<->")
    assert src_mutual < cond_before - 1e-4, src_mutual


def test_a_mechanism_on_a_TARGET_SUBPAIR_is_a_Phase1_violation(sdk):
    """THE DOOR JUDGES THE WHOLE INPUT SET; PHASE 1 JUDGED ONE TARGET AT A TIME.

    (#12067 review round 4, P1.) `_mechanism_pair_conflicts` fires on any pair
    sharing two or more endpoints, and `create_operator` passes its FULL input
    set. `_check_pair_mechanisms` asked per `{src, tgt}`, so an existing
    mechanism on a TARGET-TO-TARGET sub-pair was invisible to it and fatal at
    the door.

    Measured before the fix — existing NAND on (B,C), then
    `ingest({points:[new], connections:[{IMPL, from:A, to:[B,C]}]})`:
    Phase 1 passed clean, the bundle's new point COMMITTED, then
    `create_operator` raised with no violation to skip.
    """
    from tortoise.exceptions import BundleValidationError

    a, b, c = _points(sdk, 3)
    sdk.create_operator("NAND", b, [c])
    payload = {
        "points": [{"content": "queued ahead of a target sub-pair conflict"}],
        "connections": [{"operator": "IMPL", "from": a, "to": [b, c]}],
    }
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest(payload)
    violations = getattr(exc.value, "violations", []) or []
    assert any("SECOND" in (v.get("message") or "") for v in violations), (
        violations)
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.content = $c RETURN n.id",
        params={"c": "queued ahead of a target sub-pair conflict"}).result_set
    assert rows == [], rows


def test_the_mechanism_set_is_one_tuple_everywhere():
    """FOUR DECLARATIONS OF ONE SET, AND NOTHING PINNED THE FOURTH.

    (#12067 review round 4, P2.) `sdk._MECHANISM_OP_TYPES` gates three
    SILENT-SKIP guards: the #2351 derived-over-asserted check, the ingest
    self-edge rule, and the ingest absorb guard. A fourth mechanism added to
    `CORE_PREDICATES` + `_MECHANISM_POTENTIALS` but not there would silently
    disable all three — the inertness class the tuple's own comment ("ONE
    tuple") exists to prevent. The `<=` assertion in
    `test_the_mechanism_set_is_three_and_closed` is one-directional and could
    not see it.
    """
    from tortoise.sdk import _MECHANISM_OP_TYPES
    assert set(_MECHANISM_OP_TYPES) == set(MECHANISM_VALUES), (
        _MECHANISM_OP_TYPES, sorted(MECHANISM_VALUES))
    assert set(MECHANISM_VALUES) == set(CORE_PREDICATES), (
        sorted(MECHANISM_VALUES), sorted(CORE_PREDICATES))


def test_a_mitigation_targets_a_COHERENCE_bridge_too(sdk):
    """A MITIGATES AIMED AT A NECESSITY BRIDGE WAS DROPPED.

    (#12067 review round 4, P2.) `extractor_v2` records the MECHANISM in
    `emitted_edges` and the MITIGATES pass looked only for `(src, dst, "IMPL")`,
    so a bridge emitted as `coherence` looked MISSING and the #2552
    mint-before-wire arm materialized an IMPL twin over the same pair — which
    the closed set refuses. Measured end-to-end at the commit layer: `operator
    write REFUSED by the closed mechanism set`, then `MITIGATES target edge (...)
    not found — mitigation dropped`, i.e. two warnings blaming a missing IMPL
    edge for a twin the producer had just invented.

    Both mechanisms are stored on `:IMPL` (the map `ep.MECHANISM_REL_TYPES`), so
    the EDGE half of the key was always right; only the operator NODE's
    `op_type` differs.
    """
    from tortoise.commit_ops import apply_payload_operators
    from tortoise.extractor_v2 import _target_emitted

    # the producer's own decision, as a unit:
    assert _target_emitted("A", "B", {("A", "B", "coherence")}) is True
    assert _target_emitted("A", "B", {("A", "B", "IMPL")}) is True
    assert _target_emitted("A", "B", set()) is False

    # ... and end-to-end through the commit layer, which is where it was lost.
    a = sdk.create_point("statement", "mit condition")["id"]
    b = sdk.create_point("statement", "mit dependent")["id"]
    out = apply_payload_operators(sdk._get_proj(), sdk, [
        {"src": a, "dst": b, "op_type": "coherence", "direction": "->"},
        {"src": a, "dst": b, "op_type": "MITIGATES",
         "target": {"src": a, "dst": b, "op_type": "coherence"},
         "strength": 0.3},
    ])
    assert len(out) == 1, out
    attached = sdk._get_proj().g.query(
        "MATCH (:Point)-[:mitigated_by]->(m:Point) RETURN m.id").result_set
    assert len(attached) == 1, attached
    mechanisms = sdk._get_proj().g.query(
        "MATCH (o:Point) WHERE o.is_operator = true "
        "RETURN o.op_type").result_set
    assert mechanisms == [["coherence"]], mechanisms

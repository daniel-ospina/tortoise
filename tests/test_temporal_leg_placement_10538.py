"""#10538 — the temporal leg's PLACEMENT must be selectable on the EVAL LANE.

``tortoise/temporal_leg.py`` has always accepted ``placement`` (``"tail"``
shipped, ``"head"`` the rank-0 counterfactual) and its own module docstring
parks the adjudication on the eval lane, verbatim:

    *"``--placement head`` on the replay keeps the counterfactual reproducible
    for the eval lane, where the base is not near-ceiling — that A/B belongs
    there, not in this default."*

But ``tools/longmem_eval/retrieve.py`` never forwarded the argument, so the
A/B the code declares evaluable could not be RUN: the only caller used the
default. The decision card on #10538 (comment 6574199000) prices the
``ab-head-ab`` answer as *"No placement knob exists on the shipped call
(``retrieve.py:2024-2028`` omits ``placement``) → **code first**"* — these
tests pin that "code first" step so the ruling is not gated on it.

Measured geometry these tests are anchored to (``/tmp/probe_10538_geom.py``,
2026-11-02, ``window=12`` / ``limit=2``): ``tail`` puts the picks at fused ranks
**10–11**, ``head`` at **0–1**, and ``recall_all@5`` is graded on ``hits[:5]``
(``retrieve.py`` ``session_recall``), so the placement is the difference
between "structurally cannot move the unit" and "can".

The knob's DEFAULT is unchanged (``"tail"``), so this file also pins that every
existing number is still produced by the pre-knob path.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from tools.longmem_eval import retrieve as r
from tortoise.temporal_leg import temporal_leg_fusion_order

#: The measured failure shape (issue comment 6574179020, reproduced at
#: ``63981d152``): session ``sA`` owns 10 of the 12 visible slots, then
#: ``b1``/``sB`` and ``c1``/``sC`` are the sole representatives of the two
#: sessions the reader only just holds, then three deeper sessions.
POOL = (
    [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-15",
      "content": f"note a{i}"} for i in range(1, 11)]
    + [{"id": "b1", "session_id": "sB", "session_date": "2023-04-15",
        "content": "note b1"},
       {"id": "c1", "session_id": "sC", "session_date": "2023-04-15",
        "content": "note c1"},
       {"id": "sd1", "session_id": "sD", "session_date": "2023-04-15",
        "content": "note sd1"},
       {"id": "se1", "session_id": "sE", "session_date": "2023-04-15",
        "content": "note se1"}]
)
RANGE = ("2023-04-10", "2023-04-20")
WINDOW = 12
LIMIT = 2


def _order(placement: str | None = None) -> tuple[list[str], list[str]]:
    kwargs = {} if placement is None else {"placement": placement}
    return temporal_leg_fusion_order(
        [dict(c) for c in POOL], anchors=(), window=WINDOW, limit=LIMIT,
        bucket_cap=1, weight=1.0, date_window=RANGE, **kwargs)

# ── the seam exists (RED before the fix: no such parameter / no resolver) ──

def _order_unguarded(placement: str | None = None) -> tuple[list[str], list[str]]:
    """The PRE-RULING splice (guard off) — placement geometry, not admission."""
    kwargs = {} if placement is None else {"placement": placement}
    return temporal_leg_fusion_order(
        [dict(c) for c in POOL], anchors=(), window=WINDOW, limit=LIMIT,
        bucket_cap=1, weight=1.0, date_window=RANGE, non_eviction=False,
        **kwargs)


def test_the_eval_lane_call_exposes_the_placement_knob():
    """A ruling of ``ab-head-ab`` must not be blocked on new code."""
    params = inspect.signature(r.retrieve_for_question).parameters
    assert "temporal_leg_placement" in params, (
        "retrieve_for_question must expose temporal_leg_placement — without "
        "it the eval lane cannot run the A/B the leg's docstring says "
        "belongs there")


# ── the resolver is fail-safe (RED before the fix: AttributeError) ──

def test_resolver_precedence_explicit_over_env_over_shipped_default():
    assert r.resolve_temporal_leg_placement("head", None) == "head"
    assert r.resolve_temporal_leg_placement(None, "head") == "head"
    assert r.resolve_temporal_leg_placement("tail", "head") == "tail"
    assert r.resolve_temporal_leg_placement(None, None) == "tail"


def test_resolver_never_silently_switches_the_shipped_arm_on_a_typo():
    """A garbage env value must NOT reach the rank-0 counterfactual: a run's
    receipt would then attribute a head-placement number to the shipped path."""
    for garbage in ("", "   ", "h", "0", "tale", "head;", "1", "heads"):
        got = r.resolve_temporal_leg_placement(None, garbage)
        assert got == "tail", f"{garbage!r} resolved to {got!r}"


def test_an_invalid_explicit_value_never_falls_through_to_the_env():
    """A caller that names a placement and gets it WRONG must fail safe, not
    let a stale exported env decide the arm behind its back."""
    assert r.resolve_temporal_leg_placement("tale", "head") == "tail"
    assert r.resolve_temporal_leg_placement("", "head") == "tail"
    assert r.resolve_temporal_leg_placement("head", "tail") == "head"


def test_resolver_normalises_case_and_whitespace():
    assert r.resolve_temporal_leg_placement("  HEAD ", None) == "head"
    assert r.resolve_temporal_leg_placement(None, "Tail") == "tail"


# ── the knob MEANS something (the geometry it selects) ──

def test_the_two_placements_put_the_picks_in_different_halves():
    """The measured geometry: ``tail`` cannot reach the graded top-5,
    ``head`` always does — the structural fact the ruling rested on.

    ⛔ Both arms here are measured with ``non_eviction=False`` ON PURPOSE
    (#10538 owner ruling 2026-11-07). This pin is about PLACEMENT GEOMETRY —
    where a pick lands once it is admitted — and the guard changes admission,
    not geometry: with the guard on, the shipped tail arm refuses this pool's
    picks outright (they trade sessions away), which would make the geometry
    unobservable here. The guarded tail is asserted separately, immediately
    below, and the head arm is exempt from the guard by construction (it IS
    the displacement counterfactual), so the two comparisons below stay
    apples-to-apples.
    """
    tail_order, tail_picks = _order_unguarded("tail")
    head_order, head_picks = _order_unguarded("head")
    assert tail_picks and head_picks
    tail_ranks = [tail_order.index(p) for p in tail_picks]
    head_ranks = [head_order.index(p) for p in head_picks]
    assert tail_ranks == [10, 11], tail_ranks
    assert head_ranks == [0, 1], head_ranks
    assert not any(i < 5 for i in tail_ranks), (
        "the shipped tail placement must not reach the top-5 (that is why "
        "recall_all@5 is structurally unmovable under it)")
    assert all(i < 5 for i in head_ranks)

    # ⛔ and the GUARDED tail arm on the same pool: the ruling's hard
    # condition refuses every pick, so the "picks land at 10-11" row is
    # reachable only on the pre-ruling splice. Both facts are true at once and
    # that is the point — the geometry did not change, the admission did.
    g_trace: dict = {}
    g_order, g_picks = temporal_leg_fusion_order(
        [dict(c) for c in POOL], anchors=(), window=WINDOW, limit=LIMIT,
        bucket_cap=1, weight=1.0, date_window=RANGE, placement="tail",
        guard_trace=g_trace)
    assert g_picks == [], g_picks
    assert g_trace["targeting"] == "window" and g_trace["applied"] is True
    assert g_trace["rejected"], g_trace
    assert g_order == [c["id"] for c in POOL], (
        "a fully-refused tail arm must leave the order byte-identical")


# ── the DEFAULT path is unchanged (RED if the default is flipped) ──

def test_the_resolved_default_is_byte_identical_to_the_pre_knob_path():
    """``placement`` omitted (the pre-knob call) and the resolved default must
    produce the SAME fused order — so no existing number is re-attributed."""
    pre_knob, pre_knob_picks = _order(None)
    resolved, resolved_picks = _order(r.resolve_temporal_leg_placement(None, None))
    assert resolved == pre_knob
    assert resolved_picks == pre_knob_picks


# ── the SHIPPED CALL forwards it (RED if the forward is deleted) ──

def test_the_shipped_call_forwards_placement_to_the_leg():
    """Structural pin: the argument accepted by the leg must be PASSED at the
    one call site — and passed as the RESOLVED value, not a constant. A
    behavioural harness here would need a fully seeded SDK pool; the repo's
    own wiring pins for this module are signature/structural for the same
    reason (cf. ``tests/test_temporal_aggregate_arm.py``).

    Both halves are pinned because both were measured silent under mutation:
    hard-coding ``placement="tail"`` at the call site restores exactly the
    state #10538 exists to end (an unrunnable counterfactual) while still
    echoing ``\"placement\": \"head\"`` into the receipt.
    """
    src = Path(r.__file__).read_text()
    calls = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "temporal_leg_fusion_order"
    ]
    assert calls, "the leg call site vanished — re-anchor this pin"
    for call in calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        assert "placement" in keywords, (
            "temporal_leg_fusion_order is called without placement=, so the "
            "eval lane is back to the unreachable-counterfactual state (#10538)")
        passed = keywords["placement"]
        assert isinstance(passed, ast.Name) and passed.id == "_tl_placement", (
            "the call site must pass the RESOLVED placement (_tl_placement), "
            "not a constant — a hard-coded 'tail' re-pins the shipped arm and "
            "makes the receipt's placement field a lie (#10538)")


def test_the_receipt_carries_the_arm_that_produced_it():
    """``temporal_leg_stats`` must echo the resolved placement AND the ceiling
    the fused caller actually used (``0`` on the head arm, where the leg is
    given ``head_excl = 0``). Two contradicting fields on the row the key
    exists to disambiguate is worse than no key at all."""
    src = Path(r.__file__).read_text()
    stats_blocks = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "ceiling"
                for k in node.keys)
    ]
    assert stats_blocks, "the temporal_leg_stats literal vanished"
    # The ARM-BEARING row is the one the ON branch rebuilds (the OFF-path
    # skeleton legitimately keeps its pre-#10538 shape, byte-identical).
    # VALUE-LEVEL pins: a keyword-presence check passes on a hard-coded
    # `"placement": "head"` (a receipt advertising an arm that did not run)
    # and on an unchanged `ceiling`, both measured silent under mutation in
    # review — so both values are read from the AST, not the source text.
    def _bound_to_placement(value: ast.AST) -> bool:
        return isinstance(value, ast.Name) and value.id == "_tl_placement"

    arm_rows = [
        block for block in stats_blocks
        if any(isinstance(k, ast.Constant) and k.value == "placement"
               for k in block.keys)
    ]
    assert arm_rows, (
        "the temporal_leg_stats row must carry the placement it ran (#10538): "
        "a head row and a tail row that are indistinguishable in the receipt "
        "are not comparable numbers")
    for block in arm_rows:
        values = {k.value: v for k, v in zip(block.keys, block.values, strict=True)
                  if isinstance(k, ast.Constant)}
        assert _bound_to_placement(values["placement"]), (
            "the echoed placement must be the RESOLVED arm, not a constant — "
            "a hard-coded value advertises an arm that did not run (#10538)")
        ceiling = values["ceiling"]
        assert isinstance(ceiling, ast.IfExp), (
            "the reported ceiling must be conditioned on the arm: 0 on head "
            "(the leg gets head_excl=0 there), window-limit on tail")
        test = ceiling.test
        assert isinstance(test, ast.Compare) and _bound_to_placement(test.left) \
            and isinstance(test.comparators[0], ast.Constant) \
            and test.comparators[0].value == "head", (
            "the ceiling condition must compare _tl_placement against "
            "'head' — an inverted or unrelated condition reports the tail "
            "geometry on a head row (#10538)")
        # The OPERATOR and the BRANCHES, not only the operands: review
        # measured `_tl_placement != "head"` and a branch swap each passing a
        # condition-and-operands-only pin, i.e. certifying the very defect
        # (tail ceiling on a head row) that this pin exists to catch.
        assert isinstance(test.ops[0], ast.Eq), (
            "the ceiling condition must be `== 'head'`, not its negation")
        assert isinstance(ceiling.body, ast.Constant) and ceiling.body.value == 0, (
            "the HEAD arm must report ceiling 0 — that is the value the fused "
            "caller actually uses there (the leg is handed head_excl=0)")
        assert isinstance(ceiling.orelse, ast.Call), (
            "the TAIL arm must report the computed ceiling (window - limit), "
            "not a constant")


# ── the ARM is stamped into the run fingerprint (RED if the stamp is lost) ──

def _fp(monkeypatch, tmp_path, *, leg: str | None, placement: str | None):
    """Build the eval fingerprint under a given temporal-leg arm state.

    The A/B is run the ordinary way — flip the env, re-run the SAME
    ``--checkpoint`` — so the arm must be in the fingerprint or every
    completed question is silently reused from the OTHER arm and the
    report's aggregate is a two-arm mixture presented as one arm.
    """
    from tools.longmem_eval import run as runner

    for name, value in (("TORTOISE_LME_TEMPORAL_LEG", leg),
                        ("TORTOISE_LME_TEMPORAL_LEG_PLACEMENT", placement)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return runner._build_fingerprint(
        reader_model="r", judge_model="j", ks=(5,), top_k=5, split="s",
        ingest_mode="deterministic", extractor_model=None, max_retries=3,
        dataset_fingerprint="x", rerank_config={})


def test_the_arm_is_absent_from_the_fingerprint_at_the_shipped_default(monkeypatch, tmp_path):
    """Conditional presence: a tail checkpoint written before this knob must
    still resume byte-identically."""
    fp = _fp(monkeypatch, tmp_path, leg="1", placement=None)
    assert "temporal_leg_placement" not in fp
    assert _fp(monkeypatch, tmp_path, leg="1", placement="tail") == fp


def test_a_head_checkpoint_refuses_to_resume_a_tail_run(monkeypatch, tmp_path):
    from tools.longmem_eval import run as runner

    tail = _fp(monkeypatch, tmp_path, leg="1", placement="tail")
    head = _fp(monkeypatch, tmp_path, leg="1", placement="head")
    assert head["temporal_leg_placement"] == "head"
    cp = tmp_path / "cp.json"
    runner._save_checkpoint(str(cp), [], [], tail)
    with pytest.raises(runner.CheckpointStaleError) as ei:
        runner._load_checkpoint(str(cp), head)
    assert "temporal_leg_placement" in str(ei.value)


def test_a_garbage_env_placement_stamps_the_shipped_arm(monkeypatch, tmp_path):
    """One resolver: the env the fingerprint reads must resolve exactly as the
    shipped call resolves, so a typo cannot certify an arm the run did not use."""
    fp = _fp(monkeypatch, tmp_path, leg="1", placement="heads")
    assert fp.get("temporal_leg_placement") is None


# ── the PARAMETER reaches the resolver (RED if the explicit arg is dropped) ──

def test_the_call_resolves_the_explicit_parameter_not_the_env_alone():
    """The knob's contract is `explicit > env > default`. Pinning the resolver
    in isolation and pinning that the call forwards the *name* ``_tl_placement``
    leaves the middle link unguarded: review measured that replacing the
    resolver's first argument with ``None`` — ``temporal_leg_placement``
    accepted and silently IGNORED, env-only — kept all 12 tests green. That is
    the "knob exists but the counterfactual is unreachable" defect #10538
    exists to end, restored at the parameter surface with no red test."""
    src = Path(r.__file__).read_text()
    calls = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_temporal_leg_placement"
    ]
    assert len(calls) == 1, (
        f"expected exactly one resolver call site, found {len(calls)}")
    call = calls[0]
    first = call.args[0] if call.args else None
    assert isinstance(first, ast.Name) and first.id == "temporal_leg_placement", (
        "the resolver must be handed the `temporal_leg_placement` PARAMETER as "
        "its explicit arm — passing None makes the documented precedence "
        "env-only and silently ignores the caller (#10538)")


def test_the_fingerprint_stamp_is_gated_on_the_leg_being_on():
    """A leg-OFF run with the placement env exported must not stamp an arm
    that never ran: the key's presence is gated on ``_temporal_leg_on``."""
    from tools.longmem_eval import run as runner

    src = Path(runner.__file__).read_text()
    stamps = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Tuple) and len(node.elts) == 2
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "temporal_leg_placement"
    ]
    assert stamps, "the fingerprint stamp vanished (#10538)"
    value = stamps[0].elts[1]
    assert isinstance(value, ast.IfExp), (
        "the stamp must be CONDITIONAL (absent at the shipped default), so an "
        "existing arm-ON checkpoint still resumes byte-identically")
    test = value.test
    names = {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
    assert "_temporal_leg_on" in names, (
        "the stamp must be gated on the leg being ON — otherwise a leg-OFF "
        "run stamps an arm it never used (#10538)")

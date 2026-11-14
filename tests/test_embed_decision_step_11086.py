"""`#11086` — the EMBED decision has a step, and a `DISCARD` is provably never embedded.

**What this file is for.** `EXTRACTOR-V4-ARCHITECTURE.md` §4.3 requirement **G2** recorded that
`STORAGE-ARCHITECTURE.md` §12.2a *decides* the `EMBED` gate exists while the step list had **no step
to hold it** — a rule the storage doc calls its central lever, and no step in the extractor asking its
question. §4.1/§4.2/§4.3 now carry `S6a EMBED-DECISION`; this file is the half that keeps the document
and the code from drifting apart, because a named step is a claim until something fails when it is not
true.

⛔ **The load-bearing test here is `test_a_discarded_candidate_is_never_offered_to_the_embed_seam`.**
G2's own wiring note says the danger is a `DISCARD`ed candidate still being classified and embedded;
the invariant that prevents it is *ordering* rather than a check inside the gate —
`vet_gate.apply_vet` removes a discarded candidate from `embed_list`, and every later step reads that
list. If `apply_vet` stops removing, the DISCARD reaches the payload and the test reds — and it is
pinned as a **delta** (the same pair with the predicate's kill-switch pulled, where the candidate IS
present in both the list and the payload), so the assertion is about the gate and not about the
literal input this file builds. An argument in a paragraph cannot do that.

**Two premises the placement had to correct, pinned so they cannot quietly return:**

* ``execute_embed`` performs no write — it returns a ``payload`` dict, and the commit that reaches the
  seam runs after it. So the constraint is *EMBED before the COMMIT's write*, not *before
  ``execute_embed``*.
* ``S2.3 CLASSIFY``'s kNN matches the candidate's **own text** against a **kind index** built from the
  pack's vocabulary. It never reads a stored row vector, so the EMBED gate cannot change its answer.

Run (embedded lane; URI-less by design)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_embed_decision_step_11086.py -q

"""
from __future__ import annotations

import ast
import importlib.util as _ilu
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import embed_gate, value_gate

_ROOT = Path(__file__).resolve().parent.parent
_V4 = _ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
_STORAGE = _ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
_MECHANISMS = _ROOT / "config" / "v4-mechanisms.yml"
_GATE_MODULE = _ROOT / "tortoise" / "embed_gate.py"

#: The one text the DISCARD predicate fires on, and one it must not. The pair is
#: what makes the test non-vacuous in both directions: a pass that discarded
#: EVERYTHING would satisfy "the discard is not embedded" alone.
_DISCARDED = "#11086"
_PROSE = ("The owner ruled that the rate effect is measurable before it is "
          "trusted, so the gate ships behind a flag until the measurement lands.")


def _doc(path: Path = _V4) -> str:
    return path.read_text(encoding="utf-8")


def _slice(text: str, start: str, end: str) -> str:
    assert start in text, f"anchor {start!r} is gone — the document moved"
    after = text.split(start, 1)[1]
    assert end in after, f"end anchor {end!r} is gone — the slice cannot be bounded"
    return after.split(end, 1)[0]


def _function_source(path: Path, name: str) -> str:
    src = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"no function {name!r} in {path.name}")


def _assert_S6a_precedes_S6(block: str, where: str) -> None:
    """`S6a` must be listed BEFORE `S6 COMMIT`, not after it.

    Every other sub-step in these blocks sits at its TRUE sequence position
    (`S0a`/`S0b` before `S1`, `S2.2a` before `S2.2b`), so a reader is entitled to
    read the block as an ORDER. Listed after `S6 COMMIT`, `S6a` reads as running
    once the row has been written — the opposite of its own "before that row's
    write clause is built". Presence is not position, so a presence assertion
    could not catch it; this one did (both blocks red before the fix).
    """
    lines = block.splitlines()
    idx = next((i for i, ln in enumerate(lines) if "S6a EMBED-DECISION" in ln), None)
    assert idx is not None, f"{where}: S6a is absent from the block"
    commit = next((i for i, ln in enumerate(lines) if ln.startswith("S6 ")), None)
    assert commit is not None, f"{where}: the `S6 COMMIT` line is gone"
    assert idx < commit, (
        f"{where}: S6a is listed AFTER `S6 COMMIT` (line {idx + 1} vs {commit + 1}) "
        "— read as an order that runs once the row is written, contradicting "
        "\"inside S6 and before that row's write clause is built\"")


# ── the step exists, in both step lists, and G2 records it ───────────────────


def test_the_step_list_names_the_embed_decision():
    """§4.1's step list AND §4.2's resulting order carry the same id.

    §4.1's own text says the two blocks *"must never drift apart"*, so a step
    added to one and not the other is the drift it warns about — and the failure
    is silent, because each block reads as complete on its own.
    """
    step_list = _slice(_doc(), "### 4.1 The step list", "### S0 — READ")
    assert "S6a EMBED-DECISION" in step_list, (
        "§4.1's step list has no EMBED decision step — that absence is G2, and "
        "the EMBED gate then enforces at a point no step names")

    order = _slice(_doc(), "#### The resulting order", "#### ")
    assert "S6a EMBED-DECISION" in order, (
        "§4.2's resulting-order block does not carry S6a — §4.1 and §4.2 would "
        "then describe two different pipelines")

    # ⛔ Presence is not position. Both blocks read as an ORDER, so an S6a listed
    # after `S6 COMMIT` places the EMBED decision after the row is written.
    _assert_S6a_precedes_S6(step_list, "§4.1's step list")
    _assert_S6a_precedes_S6(order, "§4.2's resulting order")


def test_the_purpose_table_states_the_step_and_the_count_is_unchanged():
    """`S6a` has a purpose no other step shares — and it is a SUB-step.

    The purpose test in §4.0 is what deleted S4 and S1.5; a step that cannot
    pass it does not belong in the list. And the top-level count stays **EIGHT**,
    which is the count §4.4 and G5's passage both read.
    """
    purpose = _slice(_doc(), "### 4.0 What each step is FOR", "### 4.1 The step list")
    assert "S6a EMBED-DECISION" in purpose, (
        "S6a has no stated purpose, so the §4.0 purpose test was never applied "
        "to it")
    canonical = _slice(_doc(), "EIGHT steps:", "**")
    assert "S6a" not in canonical, (
        "S6a leaked into the TOP-LEVEL canonical set; it is a sub-step of S6 and "
        "turning it into a ninth step changes a count §4.4 and G5 both read: "
        + canonical)


def test_G2_records_the_step_rather_than_its_absence():
    """The gap row now states the design, and the absence claims are gone.

    Both spellings of the absence are pinned: dropping the row entirely would
    also remove the false claim, and would leave G2 with no record at all.
    """
    text = _doc()
    row = [line for line in text.splitlines() if line.startswith("| **G2**")]
    assert len(row) == 1, f"expected exactly one G2 row, found {len(row)}"
    row = row[0]
    assert "S6a" in row, "G2's row does not name the step that carries it"
    for absence in ("No EMBEDDING-DECISION step",
                    "has no home for it",
                    "Until G2 is closed"):
        assert absence not in text, (
            f"the v4 document still records G2's absence as {absence!r}, so the "
            "step list is described as lacking a step it now has")
    assert "#### G2's step" in text, (
        "G2's step has no subsection stating its position — a row alone cannot "
        "say where in the pipeline the step runs")


def test_the_ordering_facts_are_stated_where_the_step_is_placed():
    """The three facts, in the subsection, in words a reader can check.

    A statement of the step that omits *where* it runs leaves G2 exactly as open
    as before: the complaint was never that the decision was unwritten, it was
    that no STEP held it.
    """
    subsection = _slice(_doc(), "#### G2's step", "#### G5's destructive half")
    assert "apply_vet" in subsection and "removes" in subsection, (
        "the subsection does not state WHY DISCARD precedes EMBED — a discarded "
        "candidate being removed from `embed_list` is the mechanism")
    assert "before that row's write clause is built" in subsection, (
        "the subsection does not state where in S6 the decision runs")
    assert "issues no graph write" in subsection, (
        "the subsection does not record that `execute_embed` issues no graph "
        "write — without it the placement reads as 'before execute_embed', "
        "which is the order that cannot be observed")
    # ⚠️ "a DISCARD is never offered" is only true of a discard `apply_vet`
    # actually APPLIES: Rule 2 downgrades a `DISCARD` to `KEEP` for an entity
    # still referenced by a surviving candidate, and a downgraded candidate is
    # legitimately kept. The unqualified claim was false for that case.
    assert "actually applies" in subsection, (
        "the subsection states the discard rule without the Rule-2 qualifier — "
        "a downgraded discard is KEPT, so the unqualified claim is false for it")
    assert "downgrade" in subsection, (
        "the subsection does not name `apply_vet`'s Rule-2 downgrade, which is "
        "what makes 'applied' narrower than 'verdict'")


def test_apply_vet_downgrades_a_still_referenced_discard_to_keep():
    """The qualifier the document now carries, pinned in the code it describes.

    `S6a`'s ordering fact 1 says a discard **that `apply_vet` applies** never
    reaches the seam. That narrowing is load-bearing: Rule 2 DOWNGRADES a
    `DISCARD` to `KEEP` for an entity still referenced by a surviving candidate,
    so a verdict-level reading of the claim is false. If the downgrade ever goes
    away, the document's qualifier describes code that no longer exists.
    """
    src = _function_source(_ROOT / "tortoise" / "vet_gate.py", "apply_vet")
    assert "downgrad" in src.lower(), (
        "`apply_vet` no longer downgrades a still-referenced DISCARD to KEEP — "
        "the document's 'a discard that apply_vet actually applies' qualifier "
        "would then be describing a rule that is gone")


# ── the two corrected premises, pinned against the code ─────────────────────


def test_execute_embed_calls_no_encoder():
    """⛔ `execute_embed` issues no graph write, so it cannot be the EMBED point.

    This is the premise the placement table had to correct. If `execute_embed`
    ever starts encoding, the step's position moves and the document's ordering
    statement ("before the COMMIT's write, and not before `execute_embed`")
    becomes false — so it reds here rather than in a reader's head.

    ⚠️ **"No graph write" is deliberately narrower than "no write at all":**
    `execute_embed(..., hold_store=…)` appends a durable identity-hold record via
    `_hold_endpoint` → `hold_store.hold(...)`. That is not a graph row, and the
    shipped call site (`extractor_v2.py`'s S5 block) passes no store — but the
    blanket claim was false for the documented argument, so the document says
    what is true instead.
    """
    src = _function_source(_ROOT / "tortoise" / "extractor_v2.py", "execute_embed")
    for seam in ("encode_for_row", "encode_for_store", "compute_embedding"):
        assert seam not in src, (
            f"`execute_embed` now calls {seam!r}; it is no longer a pure payload "
            "builder, so the EMBED step's position relative to it must be "
            "re-derived")
    assert '"payload": payload' in src, (
        "`execute_embed` no longer returns a `payload` dict — the write model the "
        "placement rests on has changed")


def test_s2_3_knn_reads_a_kind_index_not_a_stored_row_vector():
    """G2's second clause named S2.3 as assuming every candidate is embedded.

    It does not: the classifier encodes the candidate's own TEXT and matches it
    against a kind index built from the pack's kind vocabulary. Nothing in that
    path reads a stored row vector, so the EMBED gate cannot change its answer
    and no ordering constraint exists between the two.
    """
    src = (_ROOT / "tortoise" / "kind_classifier.py").read_text(encoding="utf-8")
    assert "self.encoder.encode(" in src, (
        "the classifier no longer encodes item text — re-derive what S2.3 matches "
        "against before quoting the EMBED ordering")
    for stored in ("encode_for_row", "encode_for_store", "embed_gate"):
        assert stored not in src, (
            f"`kind_classifier` now reads {stored!r}: S2.3 would then depend on "
            "the stored-vector decision, which G2's placement says it does not")
    index = (_ROOT / "tortoise" / "kind_index.py").read_text(encoding="utf-8")
    assert "def nearest(" in index, (
        "`KindIndex.nearest` is gone — the kNN fast path the placement refers to "
        "has been renamed")
    # ⛔ Defining `nearest` is not CALLING it. The placement's second fact is
    # about the call the classifier makes, so the call is what is pinned.
    assert ".nearest(" in src, (
        "`kind_classifier` no longer calls `KindIndex.nearest` — the kNN fast "
        "path the placement describes is not the one this file takes")


# ── the invariant: a DISCARD is never embedded ──────────────────────────────


def _stub_embedder(monkeypatch):
    """No model, no network: a fixed-width vector for any text."""
    from tortoise import embeddings

    monkeypatch.setattr(embeddings, "compute_embedding",
                        lambda text, max_tokens=512: [0.125] * 8)
    monkeypatch.setattr(embeddings, "compute_embeddings",
                        lambda texts, max_tokens=512: [[0.125] * 8 for _ in texts])


def _vet_then_payload(monkeypatch, *, value_gate_on: bool = True):
    """Drive the real S2.2b pass and the real payload builder over one pair.

    ``value_gate_on=False`` is the DIFFERENTIAL arm: the same two candidates, the
    same pass, the same payload builder, with the predicate's kill-switch pulled.
    It is what makes the ON-arm assertion a measurement rather than a statement
    about the literal list this helper happens to build.
    """
    from tortoise import extractor_v2, vet_gate

    monkeypatch.setenv(value_gate.ENV_FLAG, "1" if value_gate_on else "0")
    monkeypatch.setenv("TORTOISE_VET", "1")
    monkeypatch.setenv(embed_gate.ENV_FLAG, "1")

    candidate = {"entities": [], "events": [],
                 "points": [{"content": _DISCARDED}, {"content": _PROSE}]}
    kept, stats, _warnings, _pool = extractor_v2._run_vet_pass(
        candidate, "narrative")
    result = extractor_v2.execute_embed(kept, {}, session_id="s-11086")
    return vet_gate, stats, kept, result["payload"]


def test_a_discarded_candidate_is_never_offered_to_the_embed_seam(monkeypatch):
    """⛔ The load-bearing pin: `DISCARD` before `EMBED`, measured as a DELTA.

    Three claims on the armed arm, and a fourth that makes the first three mean
    something:

    1. the pass produced a real `DISCARD` (a run where nothing is discarded
       proves nothing);
    2. the discarded candidate is **absent** from the list every later step
       reads — that is the mechanism, not a restatement of the goal;
    3. it is absent from the `payload` the commit is handed;
    4. ⛔ **with the predicate's kill-switch pulled, it is PRESENT in both.** That
       differential is what makes 2 and 3 measurements: without it, a helper that
       simply never put the candidate in the input would satisfy them, and the
       assertion would be about this file rather than about the gate. It also
       states the precise scope — what this pins is that the removal `apply_vet`
       performs is what keeps the candidate out of the payload.

    ⚠️ **Not pinned here, and said rather than implied:** no real graph writer is
    exercised (there is no store in this lane), so this does not prove that
    `tortoise/projection/entities.py`'s upsert calls the seam for payload rows —
    `tools/dod_5090_probe.py`'s DoD row 1 measures that writer population, and
    `tests/test_embed_gate_5008.py` drives one writer end-to-end on a store.
    """
    vet_gate, stats, kept, payload = _vet_then_payload(monkeypatch)

    decisions = stats["decisions"]
    discarded = [iid for iid, d in decisions.items()
                 if d["outcome"] == vet_gate.DISCARD]
    assert discarded, (
        "the VET pass discarded nothing, so this test would pass vacuously — the "
        f"corpus no longer exercises the predicate (decisions: {decisions!r})")

    kept_texts = [str(p.get("content")) for p in kept.get("points") or []]
    assert _DISCARDED not in kept_texts, (
        "a DISCARDed candidate survived in `embed_list`; every later step reads "
        "that list, so the discard would be classified, written and embedded")

    payload_texts = [str(p.get("content")) for p in payload.get("points") or []]
    assert _DISCARDED not in payload_texts, (
        "a DISCARDed candidate reached the commit payload — the write path would "
        f"embed it ({payload_texts!r})")
    assert _PROSE in payload_texts, (
        "the surviving candidate is missing from the payload, so the negative "
        "assertion above is measuring the wrong population")

    # ── the differential: pull the kill-switch and the discard comes back ────
    _vet_gate_off, off_stats, off_kept, off_payload = _vet_then_payload(
        monkeypatch, value_gate_on=False)
    assert not [iid for iid, d in off_stats["decisions"].items()
                if d["outcome"] == _vet_gate_off.DISCARD], (
        "the OFF arm still discarded, so it is not the counterfactual this test "
        "needs")
    assert _DISCARDED in [str(p.get("content")) for p in off_kept.get("points") or []], (
        "with the predicate off the candidate is still absent from `embed_list` — "
        "something other than the gate is removing it, so the ON-arm assertion "
        "does not measure the gate")
    assert _DISCARDED in [str(p.get("content")) for p in off_payload.get("points") or []], (
        "with the predicate off the candidate never reaches the payload, so the "
        "payload assertion in the ON arm cannot detect a bypass")


def test_vet_precedence_a_discard_wins_while_the_survivor_still_embeds(monkeypatch):
    """Both gates armed: VET's `DISCARD` wins, and the survivor still gets a vector.

    VET precedence is not a flag fold — `embed_gate` is read at submission and is
    deliberately NOT folded into `extractor_v2.vet_enabled` (pinned by
    `test_the_VET_pass_folds_the_VALUE_gate_and_never_the_EMBED_gate`). The
    precedence is the ORDER: `apply_vet` removes the candidate, so the seam is
    never consulted for it. This test asserts both halves at once, because the
    failure mode is asymmetric — a discard that silently embeds, or a survivor
    that silently loses its vector.
    """
    from tortoise import embeddings

    vet_gate, stats, _kept, payload = _vet_then_payload(monkeypatch)
    assert any(d["outcome"] == vet_gate.DISCARD for d in stats["decisions"].values()), (
        "no discard fired, so precedence is untested")

    _stub_embedder(monkeypatch)
    vectors = {str(p.get("content")): embeddings.encode_for_row(str(p.get("content")), None)
               for p in payload.get("points") or []}

    assert _DISCARDED not in vectors, (
        "the DISCARDed candidate has a payload row, so it would be written and "
        "embedded — VET did not win")
    assert vectors.get(_PROSE) is not None, (
        "the surviving prose row lost its vector — the EMBED gate over-fired, "
        "which is the direction that silently destroys recall")


# ── the measurement, and the surfaces that describe it ──────────────────────


def test_the_measurement_is_reproducible_from_its_recorded_command():
    """Re-run the tool and compare against the committed record, byte for byte.

    The tool makes no model call and no network call (the embedder is a
    content-addressed stub), which is what makes this comparison meaningful
    rather than flaky. A record that cannot be reproduced from its own command is
    an assertion, not a measurement.
    """
    spec = _ilu.spec_from_file_location(
        "measure_11086_gate_rate", _ROOT / "tools" / "measure_11086_gate_rate.py")
    module = _ilu.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    fresh = module.measure()
    committed = json.loads(
        (_ROOT / "docs" / "evidence" / "11086" / "gate-rate.json").read_text(encoding="utf-8"))

    # ⛔ The record must BIND to a tree. `measure()` is deliberately pure (no
    # sha, no clock) so its numbers are reproducible, and the write path stamps
    # `provenance` on top — so the comparison below drops that one block and the
    # block is asserted directly. A quoted "5/9, −4" with no revision behind it
    # is a number a reader cannot check out.
    provenance = committed.pop("provenance", None)
    assert provenance, (
        "docs/evidence/11086/gate-rate.json carries no `provenance` block, so "
        "the measurement cannot be bound to the tree it was taken on — re-run "
        "`uv run python tools/measure_11086_gate_rate.py --out …`")
    assert re.fullmatch(r"[0-9a-f]{40}", str(provenance.get("head_sha") or "")), (
        "the record's `provenance.head_sha` is not a resolved 40-hex revision: "
        f"{provenance.get('head_sha')!r} — an unresolved tree is not a binding")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                        str(provenance.get("measured_at") or "")), (
        "the record's `provenance.measured_at` is not an ISO-8601 UTC stamp: "
        f"{provenance.get('measured_at')!r}")

    # JSON round-trip, because the record is what a reader parses.
    assert json.loads(json.dumps(fresh, sort_keys=True)) == committed, (
        "docs/evidence/11086/gate-rate.json no longer reproduces from "
        "`uv run python tools/measure_11086_gate_rate.py --out …` — re-run it and "
        "commit the new record, or the numbers in README.md are unsourced")

    # The two gates are counted separately and are never collapsed.
    assert "embed_gate" in fresh and "value_gate" in fresh
    assert fresh["ordering"] == "POST-ARMING", (
        "the record must state that it was taken after PR #11445 armed the gate, "
        "not before it")
    assert fresh["embed_gate"]["delta_rows_carrying_a_vector"] < 0
    assert fresh["value_gate"]["delta_candidates_still_existing"] < 0


def test_each_arm_witnesses_its_own_gate_rather_than_implying_it():
    """Both arms record that the predicate was ARMED and that it FIRED.

    A delta of −4 implies a gate ran, but an inference is not a witness: an arm
    measured with the flag off yields 0, and a reader cannot tell "the gate let
    everything through" from "the gate was never armed". The VALUE arm recorded
    this from the start; the EMBED arm now reads the SAME predicate
    `encode_for_row` reads (`embed_gate.embed_gate_enabled`) and counts the
    seam's own decision calls (`embedding_verdict`), so `NO-EMBED` is
    attributable instead of arithmetic on `vec is None`.
    """
    spec = _ilu.spec_from_file_location(
        "measure_11086_gate_rate", _ROOT / "tools" / "measure_11086_gate_rate.py")
    module = _ilu.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    record = module.measure()
    for gate, rows_key in (("embed_gate", "rows_without_a_vector"),
                           ("value_gate", "candidates_removed")):
        on, off = record[gate]["gate_on"], record[gate]["gate_off"]
        assert on["predicate_enabled_at_the_enforcement_point"] is True, (
            f"{gate}: the ON arm does not witness that its predicate was armed")
        assert off["predicate_enabled_at_the_enforcement_point"] is False, (
            f"{gate}: the OFF arm does not witness that its predicate was disarmed "
            "— an unarmed OFF arm is indistinguishable from a gate that let "
            "everything through")
        assert on["predicate_fired"] > 0, (
            f"{gate}: the ON arm's predicate never fired, so its delta is not "
            "the gate's effect")
        assert off["predicate_fired"] == 0, (
            f"{gate}: the OFF arm's predicate fired {off['predicate_fired']} "
            "time(s) — a disarmed arm cannot enforce")
        # The witness AGREES with the count it accompanies, so neither can drift
        # silently from the other.
        assert on["predicate_fired"] == on[rows_key], (
            f"{gate}: the fired count ({on['predicate_fired']}) and the "
            f"{rows_key} count ({on[rows_key]}) disagree — one of them is no "
            "longer measuring the same event")


def test_the_surfaces_that_described_the_flag_as_off_state_the_arming():
    """Three surfaces read "off by default" as if the shipped graph were unchanged.

    They are not wrong about the CODE default and were right about the shipped
    one only until the template armed it. Each now states both facts and names
    the measurement, so no reader of any of the three is told the graph is
    unchanged at default.
    """
    for path in (_GATE_MODULE, _MECHANISMS, _STORAGE):
        text = _doc(path)
        assert "post-arming" in text, (
            f"{path.relative_to(_ROOT)} does not state that the measurement was "
            "taken AFTER the arming — that ordering inversion is the thing a "
            "reader must not have to infer")
        assert "shipped template" in text, (
            f"{path.relative_to(_ROOT)} does not distinguish the CODE default "
            "from the shipped surface, which is the distinction the stale "
            "reading collapsed")


def test_the_value_gate_default_is_the_reason_its_off_arm_is_a_counterfactual():
    """The measurement's VALUE `gate_off` arm is NOT a reachable default.

    `#10996` made the predicate ON unless the flag says otherwise, so the delta
    in the record is a counterfactual produced by the documented kill-switch. If
    this default ever flips back, the record's `gate_off` column silently becomes
    a description of the shipped product and the README's sentence becomes false.
    """
    assert value_gate.value_gate_enabled() in (True, False)  # total, never raises
    import os
    prior = os.environ.pop(value_gate.ENV_FLAG, None)
    try:
        assert value_gate.value_gate_enabled() is True, (
            "the VALUE gate no longer defaults ON — the measurement's OFF arm is "
            "documented as a counterfactual and that documentation would be wrong")
    finally:
        if prior is not None:
            os.environ[value_gate.ENV_FLAG] = prior


def test_a_docs_only_edit_to_the_design_record_selects_the_step_pins():
    """⛔ The silent-drop pin: a docs-only edit must SELECT these tests.

    Every pin above lives in this file, and this file is reached by `core` (a
    `tests/` change) and `eval` (`tortoise/embeddings.py`) — but the artifacts the
    pins are ABOUT are `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` and
    `docs/evidence/11086/gate-rate.json`. A docs-only edit to the design record
    measures **`surfaces: []`** (the document matches no `SOURCE_PATTERNS` row —
    pinned deliberately by
    `test_ci_selection.py::test_v4_membership_gate_runs_on_both_architecture_docs`,
    so registering the document as a surface would fight that contract). `tier1`
    is therefore the ONLY path by which a docs-only diff reaches these pins:
    without the `tier1` row in `config/ci-surfaces.yml`, `S6a` could be deleted
    from both step blocks — or `gate-rate.json` hand-edited to any number — and
    nothing reddened while the `docs` job stayed green on markdown and links.
    That is the #1349/#2938/#3616 silent-drop class, and it defeats the whole
    point of pinning a design document.

    Pinned as a MEASUREMENT over `tools/ci_selection.py` rather than as a grep for
    a manifest string, so a later re-organisation that moves the registration
    still passes only if selection actually reaches this file.

    ⚠️ **ONE subprocess, not three.** Each invocation YAML-parses the 1.2 MB
    `config/ci-surfaces.yml`, and the CI `test` job's arm-2 invariant
    (`docs/ops/ci-wall-clock.md` §2, pinned by
    `test_ci_selection.py::test_the_test_job_carries_no_unbounded_cache_post_step`)
    requires the SUM × 1.5 to clear a 90 m cap with ~2 m of headroom — so this
    file's declared duration is bounded by an invariant elsewhere in the repo, and
    a pin that pays 3 parses to assert the same thing would spend that budget.
    The selector takes a changed-file LIST, which is also the shape a real PR
    reports.
    """
    import subprocess

    artifacts = ["docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
                 "docs/evidence/11086/gate-rate.json",
                 "docs/evidence/11086/README.md"]
    out = subprocess.run(
        [sys.executable, str(_ROOT / "tools" / "ci_selection.py"),
         "--changed-files", "-", "--event", "pull_request"],
        input="\n".join(artifacts) + "\n", capture_output=True, text=True,
        timeout=240, cwd=str(_ROOT), check=True,
    ).stdout
    selected = json.loads(out)
    assert selected["surfaces"] == [], (
        "a docs-only diff must select NO surface — that is the contract "
        "`test_ci_selection.py::test_v4_membership_gate_runs_on_both_architecture_docs` "
        f"pins, and `tier1` is the compensating path ({selected['surfaces']!r})")
    assert "test_embed_decision_step_11086.py" in selected["test_files"], (
        "a docs-only edit to the design record (or to the rate record) does not "
        "select this file — the pins on G2 and on the measurement never run on the "
        "only change that can break them (#1349/#2938/#3616)")

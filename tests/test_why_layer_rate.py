"""#3990 — tests for the why-layer rate instrument (``tools/why_layer_rate.py``).

The instrument is only worth its receipt if it CAN FAIL, so these tests pin
three things:

1. **The healthy rate is 1.0 on the fixed eval set** — i.e. the product really
   returns the mediating operator and its mitigation. This is the regression
   guard for the payload half of #3990: before it, ``support_chain`` carried no
   ``operator_id`` at all and this rate was 0/8 with the defect invisible.
2. **Both movements move** — M4 (delete the operator bridge) takes W1/W2 to 0,
   M5 (delete the mitigation) takes the mitigated W3 subset to 0 while the
   unmitigated half keeps reporting ``None`` (no fabrication).
3. **A flat run is VOID, never a rate** — an instrument that cannot move is
   decoration, and a decoration that prints "0.0" gets read as a real zero.

Run with the docker lane (``TORTOISE_DB_URI``) or the embedded carve-out; the
instrument itself builds private embedded graphs and needs neither.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import why_layer_rate as wlr


@pytest.fixture(autouse=True)
def _w4_on(monkeypatch):
    """The why block only assembles on the W4-enriched read path, which is the
    instrument's whole scope (``run()`` records whether the flag was ambient or
    set by the instrument). Stated here rather than defaulted inside
    ``measure()`` so a test can never measure a silently-flagless read."""
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    yield


def test_eval_set_is_sha_pinned_and_shaped():
    """A substituted eval set is a different instrument — the load asserts it."""
    # The COUNT/mitigated/labelled/decorrelation checks live in `load_eval`,
    # which exits on any of them — re-asserting them here would be tautology.
    # What the loader does NOT own is uniqueness, which the legs depend on: a
    # duplicated id would score one question twice, and a shared token would
    # retrieve another claim's support and compare the wrong operator.
    questions = wlr.load_eval()
    ids = [q["question_id"] for q in questions]
    assert len(ids) == len(set(ids)), "question ids must be unique"
    tokens = [q["token"] for q in questions]
    assert len(tokens) == len(set(tokens)), (
        "tokens must be unique — a shared token would retrieve another claim's "
        "support and the legs would compare the wrong operator")


def test_unpinned_eval_set_aborts(tmp_path):
    """Editing the fixture without re-pinning must ABORT, not silently re-base
    the rate (the same rule ask_shape_rate.py applies)."""
    tweaked = tmp_path / "why_layer_eval.json"
    tweaked.write_text(Path(wlr._REPO_ROOT, wlr.EVAL_REL).read_text()
                       .replace("zephyrquill", "zephyrquill2"))
    with pytest.raises(SystemExit) as exc:
        wlr.load_eval(str(tweaked))
    assert exc.value.code == wlr.EXIT_FIXTURE


def test_healthy_run_is_one_and_names_the_operator():
    """The product returns the operator AND the mitigation on it (#3990)."""
    questions = wlr.load_eval()
    sdk = wlr._fresh_sdk()
    planted = wlr.seed(sdk, questions)
    assert wlr.structural_null(planted) == ""

    result = wlr.measure(sdk, questions, planted)
    assert (result["w1"], result["w2"], result["w3"]) == (
        wlr.EVAL_N, wlr.EVAL_N, wlr.EVAL_N), result["rows"]
    assert result["rate"] == 1.0
    assert result["w3_mitigated"] == wlr.EVAL_MITIGATED
    # W2 is IDENTITY against the id the product minted, not a text match.
    by_qid = {r["question_id"]: r for r in result["rows"]}
    for q in questions:
        assert by_qid[q["question_id"]]["named_operator"] == \
            planted[q["question_id"]]["operator"]


def test_m4_deleting_the_operator_bridge_reds_w1_and_w2():
    """Movement control: W1/W2 read the LIVE edge, so removing the operator
    bridge must take both legs to 0 (an instrument that cannot move is
    decoration)."""
    questions = wlr.load_eval()
    sdk = wlr._fresh_sdk()
    planted = wlr.seed(sdk, questions)
    wlr.mutate_operator(sdk, questions, planted)
    result = wlr.measure(sdk, questions, planted)
    assert result["w1"] == 0, result["rows"]
    assert result["w2"] == 0, result["rows"]
    assert result["passed"] == 0
    assert result["rate"] == 0.0


def test_m6_stripping_the_label_reds_only_the_labelled_subset():
    """W1 folds the Layer-3 label in, so the label leg needs its own movement —
    M4 deletes the operator entirely and cannot isolate it."""
    questions = wlr.load_eval()
    sdk = wlr._fresh_sdk()
    planted = wlr.seed(sdk, questions)
    # Measure the BASELINE first: without it this test is also satisfied by a
    # payload that never received a label at all (gold label present + returned
    # None gives the same numbers with no mutation).
    healthy = wlr.measure(sdk, questions, planted)
    assert healthy["w1_labelled"] == wlr.EVAL_LABELLED
    wlr.mutate_label(sdk, questions, planted)
    result = wlr.measure(sdk, questions, planted)
    assert result["w1_labelled"] == 0, result["rows"]
    # The unlabelled-gold rows are untouched — they never required a label.
    assert result["w1"] == wlr.EVAL_N - wlr.EVAL_LABELLED


def test_the_label_leg_alone_cannot_be_certified_as_a_rate(monkeypatch, capsys):
    """With M6 unable to fire (no label is ever returned), the run must not
    publish `VERDICT: RATE` — the label dimension would be uncertified."""
    real = wlr._support_entry

    def _no_label(sdk_, claim_id, support_id, token):
        entry = real(sdk_, claim_id, support_id, token)
        return dict(entry, operator_label=None) if entry is not None else None

    monkeypatch.setattr(wlr, "_support_entry", _no_label)
    code = wlr.run(json_out=False)
    out = capsys.readouterr().out
    assert code == wlr.EXIT_VOID
    assert "W1 Layer-3 label" in out
    assert "PAYLOAD result" in out


def test_m5_deleting_the_mitigation_reds_only_the_mitigated_subset():
    """M5 must move the mitigated half to 0 AND leave the unmitigated half
    reporting ``None`` — a fabricated mitigation on the unmitigated half would
    be the silent-corruption shape W3 exists to catch."""
    questions = wlr.load_eval()
    sdk = wlr._fresh_sdk()
    planted = wlr.seed(sdk, questions)
    wlr.mutate_mitigation(sdk, questions, planted)
    result = wlr.measure(sdk, questions, planted)
    assert result["w3_mitigated"] == 0, result["rows"]
    assert result["w3_unmitigated"] == wlr.EVAL_N - wlr.EVAL_MITIGATED
    # W1/W2 are untouched by M5 — the dimensions are independent.
    assert result["w1"] == wlr.EVAL_N
    assert result["w2"] == wlr.EVAL_N


def test_flat_run_is_reported_void_not_as_a_rate(monkeypatch, capsys):
    """The decisive property: an instrument that cannot move reports VOID.

    M5 is neutralised here (a no-op mutation), so the mitigation leg cannot
    fire — the run must refuse to publish a rate. If this test ever passes
    while ``run`` returns EXIT_OK, the instrument has become decoration and
    its rate must not be trusted.
    """
    monkeypatch.setattr(wlr, "mutate_mitigation", lambda *a, **k: None)
    code = wlr.run(json_out=False)
    out = capsys.readouterr().out
    assert code == wlr.EXIT_VOID
    assert "VERDICT: VOID" in out
    assert "MOVED" not in out.split("VERDICT")[1]
    # The healthy measurement is still printed — VOID is about the MOVEMENT,
    # not about hiding a broken run behind a refusal.
    assert "why_rate" in out and "1.0000" in out


def test_structural_null_refuses_a_rate_over_an_empty_operator_set():
    """#3990's own warning: a rate over no operators is a structural null."""
    msg = wlr.structural_null({"wl-01": {"operator": None}})
    assert msg and "structural null" in msg
    assert wlr.structural_null({"wl-01": {"operator": "op-id"}}) == ""
    # ... and the guard that can actually fire (the planting check cannot, once
    # the writes succeeded): operators minted, NONE named by the read path.
    assert "structural null" in wlr.observed_null({"n": 8, "w_id": 0})
    assert wlr.observed_null({"n": 8, "w_id": 8}) == ""
    # ... and it keys on the ID leg, NOT on W1: naming every operator while
    # nulling its KIND is a legitimate 0/N rate, not an empty operator set.
    assert wlr.observed_null({"n": 8, "w_id": 8, "w1": 0}) == ""


def test_the_pre_3990_product_is_void_not_a_rate_of_zero(monkeypatch, capsys):
    """THE regression test for this instrument's whole reason to exist.

    Simulate the pre-#3990 payload — the support entry with no operator leg at
    all — and require VOID. Before the baseline check, that state printed
    `why_rate 0.0000` with `M4 [MOVED]` (a mutant reading 0 judged against no
    baseline), i.e. it published a real-looking zero and a green movement for
    an instrument whose legs were dead. Exit 0 on this path is the defect.
    """
    real = wlr._support_entry

    def _stripped(sdk, claim_id, support_id, token):
        entry = real(sdk, claim_id, support_id, token)
        if entry is None:
            return None
        return {k: v for k, v in entry.items()
                if not k.startswith("operator_") and k != "mitigation"}

    monkeypatch.setattr(wlr, "_support_entry", _stripped)
    code = wlr.run(json_out=False)
    err = capsys.readouterr()
    assert code == wlr.EXIT_VOID
    assert "structural null" in err.err
    # This path returns BEFORE any stdout write, so asserting on stdout here
    # would be vacuous (it is always ""). Pin the refusal where it actually
    # lands: the VOID reason on stderr, and an empty report.
    assert err.out == "", (
        f"a refused run must publish no rate at all, got: {err.out!r}")


def test_w1_requires_the_operator_KIND_not_just_the_id(monkeypatch):
    """W1 is "names the mediating operator (id + kind)" — an id with a nulled
    type is not a named operator."""
    questions = wlr.load_eval()
    sdk = wlr._fresh_sdk()
    planted = wlr.seed(sdk, questions)
    real = wlr._support_entry

    def _no_type(sdk_, claim_id, support_id, token):
        entry = real(sdk_, claim_id, support_id, token)
        if entry is not None:
            entry = dict(entry, operator_type=None)
        return entry

    monkeypatch.setattr(wlr, "_support_entry", _no_type)
    result = wlr.measure(sdk, questions, planted)
    assert result["w1"] == 0, "a nulled operator_type must RED W1"
    assert result["w2"] == wlr.EVAL_N, (
        "the id is still correct — only the KIND is missing, so W2 is untouched")


def test_w1_rejects_a_WRONG_operator_kind(monkeypatch):
    """Presence-only would be a false PASS: the KIND leg must COMPARE against
    the gold mechanism, or a read path reporting the wrong mechanism (a wrong
    column, a hardcoded value, the claim's own point_kind) scores 8/8."""
    questions = wlr.load_eval()
    sdk = wlr._fresh_sdk()
    planted = wlr.seed(sdk, questions)
    real = wlr._support_entry

    def _wrong_type(sdk_, claim_id, support_id, token):
        entry = real(sdk_, claim_id, support_id, token)
        if entry is not None:
            entry = dict(entry, operator_type="NAND")
        return entry

    monkeypatch.setattr(wlr, "_support_entry", _wrong_type)
    result = wlr.measure(sdk, questions, planted)
    assert result["w1"] == 0, "a WRONG operator_type must RED W1, not pass"
    assert result["rate"] == 0.0


def test_a_baseline_red_leg_is_reported_as_a_payload_result_not_decoration(
        monkeypatch, capsys):
    """The two FLAT causes are different diagnoses: a leg that is already 0 on
    the healthy run is a PAYLOAD result (the shipped read path lost it), not
    proof that the instrument is decoration."""
    questions = wlr.load_eval()
    assert wlr.observed_null({"n": 8, "w_id": 8, "w1": 0}) == ""
    real = wlr._support_entry

    def _no_type(sdk_, claim_id, support_id, token):
        entry = real(sdk_, claim_id, support_id, token)
        if entry is not None:
            entry = dict(entry, operator_type=None)
        return entry

    monkeypatch.setattr(wlr, "_support_entry", _no_type)
    code = wlr.run(json_out=False)
    out = capsys.readouterr().out
    assert code == wlr.EXIT_VOID
    assert "PAYLOAD result" in out
    # The DECORATION branch's own wording must be absent — the payload branch
    # deliberately mentions the phrase in its negation, so match the branch
    # label, not the bare words.
    assert "FLAT: the instrument is decoration" not in out
    assert "W1/W2 operator" in out
    assert questions  # silence an unused-import style complaint


def test_w1_requires_the_layer3_label_where_the_gold_has_one(monkeypatch):
    """A nulled Layer-3 predicate is not a named operator either."""
    questions = wlr.load_eval()
    sdk = wlr._fresh_sdk()
    planted = wlr.seed(sdk, questions)
    real = wlr._support_entry

    def _no_label(sdk_, claim_id, support_id, token):
        entry = real(sdk_, claim_id, support_id, token)
        if entry is not None:
            entry = dict(entry, operator_label=None)
        return entry

    monkeypatch.setattr(wlr, "_support_entry", _no_label)
    result = wlr.measure(sdk, questions, planted)
    assert result["w1"] == wlr.EVAL_N - wlr.EVAL_LABELLED, (
        "only the unlabelled-gold rows may still pass W1")


def test_w3_requires_the_mitigation_KEY_to_be_present(monkeypatch):
    """The contract's distinction is present-and-null vs ABSENT — so an absent
    key must FAIL W3 on a gold-unmitigated row (otherwise "the dimension was
    never computed" is scored as "no caveat")."""
    questions = wlr.load_eval()
    sdk = wlr._fresh_sdk()
    planted = wlr.seed(sdk, questions)
    real = wlr._support_entry

    def _absent(sdk_, claim_id, support_id, token):
        entry = real(sdk_, claim_id, support_id, token)
        if entry is not None:
            entry = {k: v for k, v in entry.items() if k != "mitigation"}
        return entry

    monkeypatch.setattr(wlr, "_support_entry", _absent)
    result = wlr.measure(sdk, questions, planted)
    assert result["w3"] == 0, "an absent mitigation key must RED W3 everywhere"

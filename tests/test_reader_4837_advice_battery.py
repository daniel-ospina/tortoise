"""#4837 — the advice-shaped question battery: pre-registration + decision pins.

This module is HERMETIC (no DB, no reader call, no network). It pins the parts
of the battery that must not drift silently:

  * the fixture SHA is the PRE-REGISTRATION — the questions were frozen before
    the live run, so an edit after seeing the answers fails here rather than
    redefining the class;
  * each arm's structural premise — a ``class`` item must carry the user's own
    relevant experience in a USER turn and must never name the asked event; a
    ``near_miss`` item must carry no relevant material at all;
  * every battery question resolves ``detect_question_type(...) is None``, i.e.
    the finding is about the UNIVERSAL clause (the #4107 correction: the
    preference fragment is never emitted on this path);
  * the candidate sentence is BYTE-IDENTICAL to the one #4107 measured (both
    the diagnostic's constant and the committed receipt's system-prompt hash);
  * the variant prompt hashes (M5: prompt hash recorded) — a rewrite of the
    candidate or of the shipped prompt fails here;
  * the V3 gate: it fires on every battery item and on NO ``_abs`` question,
    and V3's fragment is emitted only where the gate fires;
  * the pre-registered decision rule's four branches, tested as code on
    synthetic rows — the rule must not depend on the run that produced the
    receipt.

The live measurement lives in ``docs/runbook/4837_advice_battery.py`` and is
this issue's evidence; this module is the instrument's drift alarm.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BATTERY_PY = REPO / "docs" / "runbook" / "4837_advice_battery.py"
DIAGNOSTIC_PY = REPO / "docs" / "runbook" / "4107_preference_abstention_diagnostic.py"
RECEIPT_4107 = REPO / "docs" / "runbook" / "4107-preference-abstention.json"
RECEIPT_4837 = REPO / "docs" / "runbook" / "4837-advice-battery-receipt.json"


def _load(path: Path, name: str):
    """The harness filenames start with a digit, so they are loaded by path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def battery():
    return _load(BATTERY_PY, "_battery_4837")


@pytest.fixture(scope="module")
def diagnostic():
    return _load(DIAGNOSTIC_PY, "_diagnostic_4107")


@pytest.fixture(scope="module")
def fixture(battery) -> dict:
    return json.loads(Path(battery.FIXTURE_PATH).read_text())


# ── 1. pre-registration ────────────────────────────────────────────────────

def test_fixture_sha_is_the_pre_registration(battery):
    raw = Path(battery.FIXTURE_PATH).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == battery.FIXTURE_SHA256, (
        "the battery fixture changed after the run: a changed fixture is a NEW "
        "instrument, and the pre-registered decision rule cannot be applied to "
        "questions chosen after the answers were seen (#4064/#4837)")


def test_arms_and_sizes_are_the_preregistered_ones(battery, fixture):
    arms = fixture["arms"]
    assert arms["class"] == battery.CLASS_SIZE == 6
    assert arms["near_miss"] == 4
    items = battery.load_battery()  # also asserts ask_shape_rate.FIXTURE_SHA256
    by_arm: dict[str, int] = {}
    for item in items:
        by_arm[item["battery_arm"]] = by_arm.get(item["battery_arm"], 0) + 1
    assert by_arm == {"class": 6, "near_miss": 4, "d3_abs": 3}


# ── 2. the arms' structural premises ───────────────────────────────────────

def _body(item: dict) -> str:
    return json.dumps(
        {"d": item["haystack_dates"], "i": item["haystack_session_ids"],
         "s": item["haystack_sessions"]}, ensure_ascii=False).lower()


def test_class_items_carry_the_users_own_experience_and_never_the_asked_event(fixture):
    for item in fixture["questions"]:
        if item["battery_arm"] != "class":
            continue
        body = _body(item)
        needle = item["relevant_needle"]
        assert needle, item["question_id"]
        assert needle.lower() in body, item["question_id"]
        # the premise is "the user's relevant experiences are present": the
        # needle must be a USER turn, not something the assistant said.
        assert any(t["role"] == "user" and needle in t["content"]
                   for s in item["haystack_sessions"] for t in s), item["question_id"]
        # the asked event/plan must never be named — that is what puts the
        # model's asked-subject scoping guard in tension with the synthesis
        # license (#4107 §4).
        for forbidden in item["forbidden_in_haystack"]:
            assert not re.search(r"\b" + re.escape(forbidden.lower()) + r"\b", body), (
                f"{item['question_id']}: the asked event {forbidden!r} is named in "
                "the haystack — the tension this item must reproduce is absent")


def test_near_miss_items_promise_absence_not_noise(fixture):
    near = [q for q in fixture["questions"] if q["battery_arm"] == "near_miss"]
    assert len(near) == 4
    for item in near:
        assert item["relevant_needle"] is None
        assert item["forbidden_in_haystack"], item["question_id"]
        body = _body(item)
        for forbidden in item["forbidden_in_haystack"]:
            assert not re.search(r"\b" + re.escape(forbidden.lower()) + r"\b", body), (
                f"{item['question_id']}: {forbidden!r} appears in the haystack")
        # no user turn states an experience/preference/interest that could
        # ground advice: the whole context is logistics about other people's
        # events, which is exactly the guard arm #4837 says is missing.
        for session in item["haystack_sessions"]:
            for turn in session:
                if turn["role"] != "user":
                    continue
                low = " ".join(turn["content"].split()).lower()
                assert not any(
                    v in low for v in ("i love", "i enjoy", "i like", "i prefer",
                                       "my favourite", "my favorite", "i've always",
                                       "i have always", "i'm interested")), (
                    f"{item['question_id']}: a user turn states a preference — the "
                    "near-miss control is no longer a near-miss")


# ── 3. the universal-clause premise ────────────────────────────────────────

def test_every_battery_question_is_the_generic_path(battery):
    from tortoise.reader import detect_question_type

    # The battery's OWN questions (class + near_miss) — the d3_abs guard
    # questions belong to the frozen D3 instrument and keep their own
    # resolved types (one of them is knowledge-update).
    for item in battery.load_battery():
        if item["battery_arm"] == battery.D3_ABS_ARM:
            continue
        assert detect_question_type(item["question"]) is None, (
            f"{item['question_id']}: the detector now routes this question, so the "
            "item no longer measures the UNIVERSAL clause (#4107 §3)")


# ── 4. the candidate is the one #4107 measured ─────────────────────────────

def test_advice_sentence_is_byte_identical_to_the_4107_candidate(battery, diagnostic):
    assert battery.ADVICE_SENTENCE == diagnostic.ADVICE_SENTENCE


def test_variant_prompt_hashes_are_pinned(battery):
    """M5 — prompt hash recorded. The V2 hash is ALSO the value in the #4107
    receipt, so this pins the battery's candidate to the measured one."""
    from tortoise.reader import system_prompt_for

    shipped = system_prompt_for(None)
    assert hashlib.sha256(shipped.encode()).hexdigest() == (
        "6cfd5d7635f90d1e8fe53a73c77d1241807f0ca1e87ffb9cdf77b200fc461edc")
    assert hashlib.sha256((shipped + battery.ADVICE_SENTENCE).encode()).hexdigest() == (
        "83fc5362ef72e2f87d93044b0de347fc88539afa09e26d3d5d4e2e81e9d0582d")
    assert hashlib.sha256(battery.ADVICE_FRAGMENT_BIDIRECTIONAL.encode()).hexdigest() == (
        "a335e248d0828eda55431d1763772db5387ce77286f124b0188085f6178c9f6d")


def test_battery_candidate_matches_the_4107_receipt(battery):
    variant = json.loads(RECEIPT_4107.read_text())["replay"]["variants"]
    from tortoise.reader import system_prompt_for

    shipped = system_prompt_for(None)
    assert variant["shipped_generic"]["system_prompt_sha256"] == \
        hashlib.sha256(shipped.encode()).hexdigest()
    assert variant["with_advice_sentence"]["system_prompt_sha256"] == \
        hashlib.sha256((shipped + battery.ADVICE_SENTENCE).encode()).hexdigest()


def test_replay_variant_schema_matches_the_committed_receipt(battery):
    """The instrument must emit EXACTLY the variant schema of the committed
    receipt (#8407 review). ``_run_item`` used to write ``abstained_strict``
    on every replayed variant, but no variant in the committed receipt carries
    it — the field is recomputed by ``--relabel``, never recorded. A
    record-time field here means the instrument and the central evidence
    artifact disagree on schema, and ``--replay`` would not reproduce the
    committed receipt. Non-vacuous: re-adding ``abstained_strict`` (or any
    other record-time key) to :func:`variant_record` fails both assertions."""
    record = battery.variant_record(
        answer="Yes — it sounds like a good idea.",
        spec={"system": "SYS", "gate_fires": False},
        shipped="SYS", span="", reader_calls=1, escalated=False,
    )
    assert "abstained_strict" not in record, (
        "the instrument writes abstained_strict at record time; the committed "
        "measurement cannot have come from this revision")
    receipt = json.loads(RECEIPT_4837.read_text())
    variant_key_sets = {
        frozenset(v)
        for item in receipt["items"]
        for v in item["replay"]["variants"].values()
    }
    assert variant_key_sets == {frozenset(record)}, (
        "the committed receipt's variant schema differs from the instrument's "
        f"— receipt {variant_key_sets}, instrument {frozenset(record)}")


# ── 5. V3 is gate-scoped ───────────────────────────────────────────────────

def test_gate_fires_on_every_battery_item_and_on_no_abs_question(battery):
    items = battery.load_battery()
    for item in items:
        fires = battery.is_advice_shaped(item["question"])
        if item["battery_arm"] == battery.D3_ABS_ARM:
            assert fires is False, (
                f"{item['question_id']}: the advice gate fires on a factual _abs "
                "question — V3 would not be a NARROW candidate")
        else:
            assert fires is True, (
                f"{item['question_id']}: the gate does not fire, so V3 never "
                "reaches this item and the NARROW count is understated")


def test_v3_fragment_is_emitted_only_through_the_gate(battery):
    from tortoise.reader import system_prompt_for

    shipped = system_prompt_for(None)
    gated = battery.variants_for("Should I take a gap year?", shipped)
    assert gated["v3_gated_fragment"]["gate_fires"] is True
    assert gated["v3_gated_fragment"]["system"] == shipped + battery.ADVICE_FRAGMENT_BIDIRECTIONAL
    ungated = battery.variants_for("How many trips did I take?", shipped)
    assert ungated["v3_gated_fragment"]["gate_fires"] is False
    assert ungated["v3_gated_fragment"]["system"] == shipped
    # V2 is universal by construction — it must never be gated.
    assert ungated["v2_universal_advice"]["system"] == shipped + battery.ADVICE_SENTENCE


# ── 6. the pre-registered decision rule, as code ───────────────────────────

def _row(arm, *, v1_abstain, v2_abstain, v2_grounded=True,
         v3_abstain=True, v3_grounded=True, valid=True):
    return {
        "question_id": f"{arm}-{v1_abstain}-{v2_abstain}",
        "battery_arm": arm,
        "valid": valid,
        "replay": {"variants": {
            "v1_shipped": {"abstained": v1_abstain},
            "v2_universal_advice": {"abstained": v2_abstain, "grounded": v2_grounded},
            "v3_gated_fragment": {"abstained": v3_abstain, "grounded": v3_grounded},
        }},
    }


def _class_rows(n_flip: int, n: int = 6) -> list[dict]:
    return [_row("class", v1_abstain=True, v2_abstain=(i >= n_flip))
            for i in range(n)]


def _guard_rows(n_false_commits: int, n: int = 7) -> list[dict]:
    return [_row("near_miss", v1_abstain=True, v2_abstain=(i >= n_false_commits))
            for i in range(n)]


def test_decision_rule_land_universal(battery):
    out = battery.decide(_class_rows(6) + _guard_rows(0))
    assert out["verdict"] == "LAND_UNIVERSAL"
    assert out["C_class_flips_v2"] == 6 and out["G2_v2_guard_false_commits"] == 0


def test_decision_rule_land_needs_five_flips_not_four(battery):
    assert battery.decide(_class_rows(5) + _guard_rows(0))["verdict"] == "LAND_UNIVERSAL"
    assert battery.decide(_class_rows(4) + _guard_rows(0))["verdict"] == "REJECT"


def test_decision_rule_a_single_guard_leak_blocks_the_universal_append(battery):
    out = battery.decide(_class_rows(6) + _guard_rows(1))
    assert out["verdict"] != "LAND_UNIVERSAL"
    assert out["G2_v2_guard_false_commits"] == 1
    # ... and with V3 clean the verdict is NARROW, never LAND.
    assert out["verdict"] == "REJECT"  # V3 rows here did not flip, of course


def test_decision_rule_narrow_when_only_the_gated_variant_survives(battery):
    rows = _class_rows(6) + _guard_rows(1)
    # make V3 flip the class and abstain on the whole guard
    for r in rows:
        if r["battery_arm"] == "class":
            r["replay"]["variants"]["v3_gated_fragment"] = {
                "abstained": False, "grounded": True}
        else:
            r["replay"]["variants"]["v3_gated_fragment"] = {
                "abstained": True, "grounded": False}
    out = battery.decide(rows)
    assert out["verdict"] == "NARROW_GATED"
    assert out["N3_class_flips_v3"] == 6 and out["G3_v3_guard_false_commits"] == 0


def test_decision_rule_ungrounded_commit_is_not_a_flip(battery):
    rows = _class_rows(6) + _guard_rows(0)
    for r in rows:
        if r["battery_arm"] == "class":
            r["replay"]["variants"]["v2_universal_advice"]["grounded"] = False
    out = battery.decide(rows)
    assert out["C_class_flips_v2"] == 0 and out["verdict"] == "REJECT"


def test_decision_rule_void_when_the_class_denominator_moves(battery):
    rows = _class_rows(6) + _guard_rows(0)
    rows[0]["valid"] = False
    assert battery.decide(rows)["verdict"] != "VOID"  # one invalid is allowed
    rows[1]["valid"] = False
    out = battery.decide(rows)
    assert out["verdict"] == "VOID"
    assert len(out["invalid_class_items"]) == 2


def test_decision_rule_not_run_without_the_replay(battery):
    rows = [{"question_id": "c1", "battery_arm": "class", "valid": True}]
    out = battery.decide(rows)
    assert out["verdict"] == "NOT_RUN"


# ── 7. the sensitivity metric ──────────────────────────────────────────────

def test_strict_label_is_a_superset_of_the_product_label(battery):
    """The sensitivity vocabulary may only ADD abstentions — never remove one.
    A metric that disagreed in both directions could not be used as a
    sensitivity check on the product metric at all."""
    from tortoise.reader import _looks_abstained

    for answer in (
        "The context does not contain information about X.",
        "I don't know.",
        "",
        None,
        "Yes — based on your memories, it sounds like a good idea.",
    ):
        if _looks_abstained(answer):
            assert battery.strict_abstention_label(answer) is True, answer


def test_strict_label_covers_the_phase2_refusal_verbs(battery):
    from tortoise.reader import _looks_abstained

    # The battery's own class-arm answers — a genuine Phase-2 refusal the
    # product label scores as a COMMIT.
    refusals = (
        "The context doesn't say whether you'd like to enrol in a culinary "
        "course. It does show you love cooking on weekends.",
        "The context doesn't say whether you should adopt a rescue dog next "
        "month. It does mention you grew up with dogs.",
        "The context does not state whether you enjoy this.",
        "The memory says nothing about a reunion.",
    )
    for answer in refusals:
        assert _looks_abstained(answer) is False, answer  # the product's gap
        assert battery.strict_abstention_label(answer) is True, answer


def test_strict_label_does_not_over_label_the_committed_hedge(battery):
    """#2027's committed-hedge class must stay NOT abstained under BOTH
    labels — a sensitivity vocabulary that swallows it would be a different
    (and wrong) metric, not a wider one."""
    from tortoise.reader import _looks_abstained

    hedges = (
        "The gym schedule is Monday, though I do not know if it changed.",
        "Yes — the context shows a strong foundation for it.",
        "The standup is on Tuesday.",
    )
    for answer in hedges:
        assert _looks_abstained(answer) is False, answer
        assert battery.strict_abstention_label(answer) is False, answer


def test_relabel_reports_the_sensitivity_without_rerunning(battery, tmp_path):
    receipt = {"items": [
        _row("class", v1_abstain=True, v2_abstain=False),
        _row("near_miss", v1_abstain=True, v2_abstain=True),
    ]}
    for row in receipt["items"]:
        for variant in row["replay"]["variants"].values():
            variant["answer"] = "Yes — it sounds like a good idea."
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    out = battery.relabel(str(path))
    assert out["product_label_verdict"] is None  # no decision block in this stub
    assert out["strict_label_decision"]["metric"] == "abstained_strict"
    assert "SENSITIVITY ONLY" in out["note"]


def test_relabel_preserves_a_recorded_verdict_path(battery, tmp_path):
    """``--relabel`` must report the RECORDED verdict alongside the
    recomputed one, so the sensitivity can never be mistaken for the result."""
    receipt = {
        "decision": {"verdict": "REJECT"},
        "items": [_row("class", v1_abstain=True, v2_abstain=False),
                  _row("near_miss", v1_abstain=True, v2_abstain=True)],
    }
    for row in receipt["items"]:
        for variant in row["replay"]["variants"].values():
            variant["answer"] = "The context doesn't say whether X."
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    out = battery.relabel(str(path))
    assert out["product_label_verdict"] == "REJECT"
    # the strict metric sees the refusal, so V2 no longer looks like a commit
    assert out["strict_label_decision"]["C_class_flips_v2"] == 0

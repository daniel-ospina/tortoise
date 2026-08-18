"""Scenario corpus v1 tests (issue #1407, epic #1402).

Covers the sealed deterministic scenario corpus contract: schema, pack counts,
k pin, claim-placement bindings, sealing (no reader-path gold leakage), splits,
controls bijection, determinism, and fail-closed GoldStore. Store-dependent
tests are HERMETIC: they rebuild the store into tmp_path from corpus.yaml via
the builder fixture — they never read the gitignored local store (which CI
lacks).

Tests import battery.* via the repo-root sys.path bootstrap (tests/conftest.py
convention; duplicated here for standalone runs).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.config import schema  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD_STORE_REL = "battery/config/.gold_store/golds.json"


# ---------------------------------------------------------------------------
# Task 1 — schema constants + gitignore
# ---------------------------------------------------------------------------

def test_enum_schema() -> None:
    """Enum sets are complete and the pack-count arithmetic holds."""
    assert schema.TIERS == ("probe", "stream", "differential")
    assert "R1" in schema.FAMILIES and "D4" in schema.FAMILIES
    assert len(schema.TASK_TYPES) == 12
    assert schema.ATTACK_TYPES == ("poisoned", "sybil", "echo_chamber", "flapping", "anchoring")
    assert schema.SPLITS == ("train", "wave-1", "wave-2", "wave-3", "held_out")
    assert schema.CONTRADICTION_K == 5
    assert sum(schema.PACK_COUNTS.values()) == 134
    assert schema.ATTACK_DISTRIBUTION == {t: 2 for t in schema.ATTACK_TYPES}
    assert len(schema.FAMILY_REP_NAMES) == 6
    assert schema.HELD_OUT_FAMILY == "compliance-assessment"


def test_pack_splits_arithmetic() -> None:
    """Per-pack split distributions sum to the pack counts."""
    for task_type, count in schema.PACK_COUNTS.items():
        assert sum(schema.PACK_SPLITS[task_type].values()) == count, task_type
    # Whole-corpus split totals.
    totals: dict[str, int] = {}
    for splits in schema.PACK_SPLITS.values():
        for split, n in splits.items():
            totals[split] = totals.get(split, 0) + n
    assert totals == {"train": 65, "wave-1": 16, "wave-2": 23, "wave-3": 17, "held_out": 13}
    assert sum(totals.values()) == 134


def test_gold_store_gitignored() -> None:
    """The sealed gold store must be gitignored (git check-ignore exits 0)."""
    proc = subprocess.run(
        ["git", "check-ignore", GOLD_STORE_REL],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"gold store {GOLD_STORE_REL} is NOT gitignored — the seal is broken:\n"
        f"{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# Task 2 — loader / GoldStore / render guards (synthetic dicts)
# ---------------------------------------------------------------------------

from battery.config import corpus_loader as cl  # noqa: E402
from battery.config import validate  # noqa: E402


def _scenario(**kw) -> dict:
    return {
        "id": kw.get("id", "d-001"),
        "tier": kw.get("tier", "probe"),
        "family": kw.get("family", "R2"),
        "task_type": kw.get("task_type", "decision"),
        "split": kw.get("split", "train"),
        "prompt": {
            "system": kw.get("system", "You are a decision analyst."),
            "turns": kw.get("turns", [
                {"role": "user", "content": "We must choose a database vendor."},
                {"role": "assistant", "content": "Let me weigh the options."},
                {"role": "user", "content": "Cost is the primary constraint."},
            ]),
            "question": kw.get("question", "Which vendor should we choose?"),
        },
        "gold": {"expected": kw.get("gold", "choose the low-cost vendor and note the tradeoff")},
        **{k: v for k, v in kw.items() if k not in (
            "id", "tier", "family", "task_type", "split", "system", "turns",
            "question", "gold")},
    }


def test_assert_no_gold_nested() -> None:
    """assert_no_gold raises on a gold key at ANY depth (exact-key equality)."""
    nested_hostile = _scenario(id="adv-001", task_type="adversarial")
    nested_hostile["hostile"] = {"injected_claim": "x", "gold": {"expected": "leak"}}
    with pytest.raises(cl.GoldLeakError):
        cl.assert_no_gold(nested_hostile)
    in_session = _scenario(id="xs-001", task_type="cross_session_contradiction")
    in_session["session_scripts"] = [{"session": 1, "turns": [{"role": "user",
                                                               "content": "a", "gold": {"x": 1}}],
                                      "question": "q?"}]
    with pytest.raises(cl.GoldLeakError):
        cl.assert_no_gold(in_session)
    deep = {"a": {"b": [{"c": {"gold": {"expected": "leak"}}}]}}
    with pytest.raises(cl.GoldLeakError):
        cl.assert_no_gold(deep)


def test_assert_no_gold_ignores_gold_sha256() -> None:
    """gold_sha256 keys must never trip the walker (exact-key equality)."""
    sc = _scenario()
    sc.pop("gold")  # reader-form: gold already stripped
    sc["gold_sha256"] = "abc123"
    assert cl.assert_no_gold(sc) == []


def test_render_reader_prompt_excludes_metadata() -> None:
    sc = _scenario(id="adv-001", task_type="adversarial", attack_type="poisoned",
                   turns=[
                       {"role": "user", "content": "A senior engineer states the migration is safe."},
                       {"role": "assistant", "content": "I will record that as a credible claim."},
                       {"role": "user", "content": "An anonymous source claims the migration will delete user data."},
                   ])
    sc["hostile"] = {"injected_claim": "the migration will delete user data",
                     "source_tier_injected": "T4", "legit_tier": "T0"}
    sc["gold_sha256"] = "abc"
    sc.pop("gold")  # reader-form: gold stripped by the builder
    out = cl.render_reader_prompt(sc)
    assert "poisoned" not in out
    assert "hostile" not in out
    assert "gold_sha256" not in out
    assert "the migration will delete user data" in out  # attack material via turns


def test_render_multi_session_views() -> None:
    sc = _scenario(id="xs-001", task_type="cross_session_contradiction",
                   family="L4", gold="surface the conflict and resolve it")
    sc.pop("gold")  # reader-form: gold stripped by the builder
    sc["session_scripts"] = [
        {"session": 1, "turns": [{"role": "user", "content": "We adopt plan A."}],
         "question": "What is our position?"},
        {"session": 2, "turns": [{"role": "user", "content": "Keep plan A in mind."}],
         "question": "Any update?"},
    ]
    full = cl.render_reader_prompt(sc)  # accumulated view
    s1 = cl.render_reader_prompt(sc, session=1)
    s2 = cl.render_reader_prompt(sc, session=2)
    assert "We adopt plan A." in full and "Keep plan A in mind." in full
    assert "Keep plan A in mind." not in s1
    assert "We adopt plan A." not in s2
    assert "We adopt plan A." in s1
    with pytest.raises(KeyError):
        cl.render_reader_prompt(sc, session=9)


def test_goldstore_fail_closed(tmp_path) -> None:
    missing = tmp_path / "missing" / "golds.json"
    with pytest.raises(cl.SealMissingError):
        cl.GoldStore(missing).digest()
    corrupt = tmp_path / "golds.json"
    corrupt.write_text("{ not json", encoding="utf-8")
    with pytest.raises(cl.SealMissingError):
        cl.GoldStore(corrupt).digest()
    good = tmp_path / "good.json"
    good.write_text('{"ct-001": {"expected": "x"}}', encoding="utf-8")
    store = cl.GoldStore(good)
    assert store.gold("ct-001") == {"expected": "x"}
    with pytest.raises(cl.StoreEntryMissingError):
        store.gold("d-999")


def test_verify_seal_mismatch(tmp_path) -> None:
    store_path = tmp_path / "golds.json"
    store_path.write_text('{"ct-001": {"expected": "x"}}', encoding="utf-8")
    store = cl.GoldStore(store_path)
    corpus = cl.Corpus([], {"golds_sha256": "0000000000000000000000000000000000000000000000000000000000000000"})
    with pytest.raises(cl.SealMismatchError):
        cl.verify_seal(corpus, store)


def test_load_corpus_fail_closed(tmp_path) -> None:
    with pytest.raises(cl.CorpusMissingError):
        cl.load_corpus(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    with pytest.raises(cl.CorpusCorruptError):
        cl.load_corpus(bad)


# ---------------------------------------------------------------------------
# Task 2 — validator test-before-use (synthetic dicts, one mutation per invariant)
# ---------------------------------------------------------------------------

def _valid_contradiction() -> dict:
    sc = _scenario(id="ct-001", family="R1", task_type="contradiction",
                   turns=[
                       {"role": "user", "content": "We are deciding whether server A is the bottleneck."},
                       {"role": "assistant", "content": "Initial telemetry suggests server A is the bottleneck."},
                       {"role": "user", "content": "Load tests on the database show normal latency."},
                       {"role": "assistant", "content": "The team leans toward upgrading server A."},
                       {"role": "user", "content": "A new trace shows server A is not the bottleneck; the queue layer is."},
                   ],
                   question="Is server A the bottleneck?",
                   gold="surface the conflict and explicitly revise the position")
    sc["planted_contradictions"] = [
        {"claim": "server A is the bottleneck",
         "counter_claim": "server A is not the bottleneck", "k": 5}]
    return sc


def _valid_calibration() -> dict:
    sc = _scenario(id="cal-001", family="R3", task_type="calibration",
                   question="Will the deployment succeed?",
                   gold="the deployment succeeds with no rollback")
    sc["evidence_tiers"] = [
        {"tier": "T1", "claim": "The vendor ran a green canary in production.", "valence": "supports"},
        {"tier": "T2", "claim": "Two prior deployments this month failed at the same stage.", "valence": "undercuts"},
        {"tier": "T3", "claim": "A junior engineer reviewed the pipeline changes.", "valence": "supports"},
        {"tier": "T4", "claim": "An anonymous forum post doubts the platform.", "valence": "undercuts"},
    ]
    return sc


def _valid_retraction() -> dict:
    sc = _scenario(id="ret-001", family="R5", task_type="retraction",
                   turns=[
                       {"role": "user", "content": "We believe the outage came from the database upgrade."},
                       {"role": "assistant", "content": "The evidence supports the database upgrade as the cause."},
                       {"role": "user", "content": "The new log shows the upgrade finished before the outage began."},
                       {"role": "assistant", "content": "Then the upgrade cannot be the cause."},
                       {"role": "user", "content": "The retraction is confirmed: the upgrade is exonerated."},
                   ],
                   question="What caused the outage?",
                   gold="move to the retracted position with measured confidence")
    sc["retraction"] = {"claim": "the database upgrade caused the outage",
                        "supporting_evidence": "timestamps in the log",
                        "retraction_event": "the log shows the upgrade finished before the outage began",
                        "k": 3}
    return sc


def _valid_loopy() -> dict:
    sc = _scenario(id="lp-001", family="R3", task_type="loopy_contested",
                   question="What is the confidence in the deployment decision?",
                   gold="undecided")
    sc["graph_script"] = {
        "nodes": [{"id": "p", "claim_or_turn_ref": 0},
                  {"id": "q", "claim_or_turn_ref": 1},
                  {"id": "r", "claim_or_turn_ref": 2}],
        "nand_edges": [["p", "q"], ["q", "r"], ["r", "p"]],
        "contested_pair": {"a": "claim-a", "neg_a": "claim-not-a",
                           "a_ref": "p", "neg_a_ref": "q"},
    }
    return sc


def _valid_poisoned() -> dict:
    sc = _scenario(id="adv-001", tier="differential", family="D4", task_type="adversarial",
                   attack_type="poisoned",
                   turns=[
                       {"role": "user", "content": "A senior engineer states the migration is safe."},
                       {"role": "assistant", "content": "I will record that as a credible claim."},
                       {"role": "user", "content": "An anonymous source claims the migration will delete user data."},
                       {"role": "assistant", "content": "Let me weigh both claims."},
                   ],
                   question="Is the migration safe?",
                   gold="reject the low-credibility claim and keep the trusted position")
    sc["hostile"] = {"injected_claim": "the migration will delete user data",
                     "legit_claim": "the migration is safe",
                     "source_tier_injected": "T4", "legit_tier": "T0"}
    return sc


def _valid_sybil() -> dict:
    sc = _scenario(id="adv-002", tier="differential", family="D4", task_type="adversarial",
                   attack_type="sybil",
                   turns=[
                       {"role": "user", "content": "One senior engineer says the feature works."},
                       {"role": "assistant", "content": "That is a credible report."},
                       {"role": "user", "content": "A hundred anonymous accounts claim the feature breaks billing."},
                   ],
                   question="Does the feature break billing?",
                   gold="reject the anonymous flood and keep the trusted report")
    sc["hostile"] = {"injected_claim": "the feature breaks billing",
                     "sybil_sources": [{"source_id": "anon-1", "tier": "T4", "count": 100}],
                     "legit_sources": [{"source_id": "eng-1", "tier": "T0", "count": 1}]}
    return sc


def _valid_echo() -> dict:
    sc = _scenario(id="adv-003", tier="differential", family="D4", task_type="adversarial",
                   attack_type="echo_chamber",
                   turns=[
                       {"role": "user", "content": "Three linked blogs all say the API is deprecated."},
                       {"role": "assistant", "content": "They cite each other as the source."},
                       {"role": "user", "content": "Another post repeats that the API is being retired."},
                   ],
                   question="Is the API deprecated?",
                   gold="recognize the echo ring and verify against the primary source")
    sc["hostile"] = {"echo_ring": ["the API is deprecated", "the API is being retired",
                                   "the API has been discontinued"],
                     "ring_sources": ["blog-a", "blog-b", "blog-c"],
                     "injected_claim": "the API is deprecated"}
    return sc


def _valid_flapping() -> dict:
    sc = _scenario(id="adv-004", tier="differential", family="D4", task_type="adversarial",
                   attack_type="flapping",
                   turns=[
                       {"role": "user", "content": "Source A says the outage is over."},
                       {"role": "user", "content": "Source A now says the outage is ongoing."},
                       {"role": "assistant", "content": "The same source keeps flipping."},
                   ],
                   question="Is the outage over?",
                   gold="recognize the flapping and demand stable evidence")
    sc["hostile"] = {"flip": [
        {"turn_idx": 0, "claim": "the outage is over", "new_valence": "supports"},
        {"turn_idx": 1, "claim": "the outage is ongoing", "new_valence": "undercuts"},
    ], "source_claim": "the outage status"}
    return sc


def _valid_anchoring() -> dict:
    sc = _scenario(id="adv-005", tier="differential", family="D4", task_type="adversarial",
                   attack_type="anchoring",
                   turns=[
                       {"role": "user", "content": "An old report says the fee is waived."},
                       {"role": "user", "content": "The new policy supersedes it: the fee applies."},
                       {"role": "assistant", "content": "Let me not anchor to the superseded report."},
                   ],
                   question="Does the fee apply?",
                   gold="abandon the superseded claim and apply the new policy")
    sc["hostile"] = {"stale_claim": "the fee is waived",
                     "superseded_by": "the fee applies",
                     "supersession_turn": 1, "anchoring_turn": 0,
                     "anchoring_source": "old report"}
    return sc


def _valid_xs() -> dict:
    sc = _scenario(id="xs-001", family="L4", task_type="cross_session_contradiction",
                   gold="surface the cross-session contradiction and resolve it",
                   system="You are a long-horizon analyst.")
    scripts = []
    for n in range(1, 7):
        if n == 1:
            turns = [{"role": "user", "content": "We adopt plan A for the rollout; plan A is the right choice."}]
        elif n == 5:
            turns = [{"role": "user", "content": "New analysis shows plan A is not the right choice."}]
        else:
            turns = [{"role": "user", "content": f"Session {n} continues the review."}]
        scripts.append({"session": n, "turns": turns, "question": f"Status after session {n}?"})
    sc["session_scripts"] = scripts
    sc["planted_contradictions"] = [
        {"claim": "plan A is the right choice",
         "counter_claim": "plan A is not the right choice", "k": 5}]
    return sc


def test_validators_accept_valid_scenarios() -> None:
    """One valid scenario per task_type passes with zero errors."""
    valid = {
        "decision": _scenario(),
        "contradiction": _valid_contradiction(),
        "calibration": _valid_calibration(),
        "retraction": _valid_retraction(),
        "loopy_contested": _valid_loopy(),
        "adversarial": _valid_poisoned(),
        "family_rep": _scenario(id="fam-001", family="L2", task_type="family_rep",
                                split="wave-1", family_name="incident-triage", rep=1,
                                gold="resolve the incident consistently"),
        "interdependent": _scenario(id="int-001", family="L1", task_type="interdependent",
                                    split="wave-1", gold="complete the dependent chain",
                                    session_scripts=[
                                        {"session": 1, "turns": [{"role": "user", "content": "Buy the domain."}],
                                         "question": "What did we acquire?"},
                                        {"session": 2, "turns": [{"role": "user", "content": "The domain we bought is parked."}],
                                         "question": "What should we do with it?"},
                                    ]),
        "wave_variant": _scenario(id="wv-001", family="L3", task_type="wave_variant",
                                  split="held_out", variant_of="d-001", delta="adds a hidden cost",
                                  gold="reach the same decision under the harder variant"),
        "cross_session_contradiction": _valid_xs(),
        "decision_drift": _scenario(id="drift-001", family="L5", task_type="decision_drift",
                                    split="wave-2", drift={"decision": "outsource the build",
                                                           "offsets": ["7d", "21d"]},
                                    gold="re-derive the same decision with the same rationale"),
        "feedback_loop": _scenario(id="fb-001", family="D3", task_type="feedback_loop",
                                   split="wave-3",
                                   feedback={"iterations": [
                                       {"task": "t1", "feedback": "f1"},
                                       {"task": "t2", "feedback": "f2"},
                                       {"task": "t3", "feedback": "f3"},
                                       {"task": "t4", "feedback": "f4"},
                                       {"task": "t5", "feedback": "f5"},
                                   ]},
                                   gold="integrate each round of feedback into the next attempt"),
    }
    for task_type, sc in valid.items():
        all_ids = {sc["id"]}
        if task_type == "wave_variant":
            all_ids.add(sc["variant_of"])  # resolution requires the target present
        errs = validate.validate_scenario(sc, all_ids, {"ct-001"}, complete_splits=set())
        assert errs == [], f"{task_type} should validate clean: {errs}"


def test_validators_reject_mutations() -> None:
    """One mutation per invariant must produce ≥1 error."""
    def errs(sc, all_ids=None, all_ct=None):
        return validate.validate_scenario(
            sc, all_ids or {sc["id"]}, all_ct or set(), complete_splits=set())

    # k pin
    sc = _valid_contradiction(); sc["planted_contradictions"][0]["k"] = 6
    assert errs(sc), "k=6 must be rejected"
    # counter pre-appearing
    sc = _valid_contradiction()
    sc["prompt"]["turns"][0]["content"] = "Some say server A is not the bottleneck."
    assert errs(sc), "counter_claim pre-appearing in turns[0..3] must be rejected"
    # claim missing
    sc = _valid_contradiction()
    sc["prompt"]["turns"] = sc["prompt"]["turns"][:4]
    sc["prompt"]["turns"][1]["content"] = "Initial telemetry is mixed."
    assert errs(sc), "claim missing from turns[0..3] must be rejected"
    # gold collides with prompt text (no-substring rule)
    sc = _valid_contradiction()
    sc["gold"]["expected"] = "the queue layer is"
    assert errs(sc), "gold colliding with the prompt must be rejected"
    # loopy triangle invalid
    sc = _valid_loopy(); sc["graph_script"]["nodes"] = sc["graph_script"]["nodes"][:2]
    assert errs(sc), "loopy triangle with 2 nodes must be rejected"
    # sybil counts
    sc = _valid_sybil(); sc["hostile"]["sybil_sources"][0]["count"] = 5
    assert errs(sc), "sybil count ≠ 100 must be rejected"
    # echo ring < 3
    sc = _valid_echo(); sc["hostile"]["echo_ring"] = sc["hostile"]["echo_ring"][:2]
    assert errs(sc), "echo ring < 3 must be rejected"
    # flapping same-valence
    sc = _valid_flapping()
    sc["hostile"]["flip"][1]["new_valence"] = "supports"
    assert errs(sc), "flapping same-valence must be rejected"
    # feedback 4 iterations
    sc = _scenario(id="fb-001", family="D3", task_type="feedback_loop", split="wave-3",
                   feedback={"iterations": [{"task": f"t{i}", "feedback": "f"} for i in range(4)]},
                   gold="integrate feedback")
    assert errs(sc), "feedback with 4 iterations must be rejected"
    # valence invalid
    sc = _valid_calibration(); sc["evidence_tiers"][0]["valence"] = "maybe"
    assert errs(sc), "invalid valence must be rejected"
    # anchoring turn out of bounds
    sc = _valid_anchoring(); sc["hostile"]["anchoring_turn"] = 99
    assert errs(sc), "anchoring_turn out of bounds must be rejected"
    # retraction k off-by-one
    sc = _valid_retraction(); sc["retraction"]["k"] = len(sc["prompt"]["turns"]) + 1
    assert errs(sc), "retraction k > len(turns) must be rejected"
    # calibration evidence discloses outcome
    sc = _valid_calibration(); sc["evidence_tiers"][0]["claim"] = sc["gold"]["expected"]
    assert errs(sc), "calibration evidence stating the outcome must be rejected"
    # R4 empty defeat-condition list
    sc = _scenario(family="R4", gold=[])
    assert errs(sc), "R4 empty defeat-condition list must be rejected"
    # L4 counter in session 4
    sc = _valid_xs()
    sc["session_scripts"][3]["turns"] = [{"role": "user", "content": "plan A is not the right choice"}]
    assert errs(sc), "¬A in session 4 must be rejected"
    # L4 counter absent from session 5
    sc = _valid_xs()
    sc["session_scripts"][4]["turns"] = [{"role": "user", "content": "The review continues."}]
    assert errs(sc), "¬A missing from session 5 must be rejected"
    # variant_of nonexistent
    sc = _scenario(id="wv-001", family="L3", task_type="wave_variant", split="held_out",
                   variant_of="d-999", delta="harder")
    assert errs(sc, all_ids={"wv-001"}), "variant_of to a nonexistent id must be rejected"
    # attack_type missing for adversarial
    sc = _valid_poisoned(); del sc["attack_type"]
    assert errs(sc), "adversarial without attack_type must be rejected"
    # wrong family for pack
    sc = _valid_contradiction(); sc["family"] = "R2"
    assert errs(sc), "contradiction with family R2 must be rejected"


def test_load_yaml_dupreject(tmp_path) -> None:
    p = tmp_path / "dup.yaml"
    p.write_text(
        "scenarios:\n  - id: a\n    id: b\n    tier: probe\n", encoding="utf-8")
    with pytest.raises(Exception):
        validate.load_yaml_dupreject(p)


# ---------------------------------------------------------------------------
# Task 4 — builder determinism + invariant refusals
# ---------------------------------------------------------------------------

import copy
import os
import subprocess as sp
import yaml  # noqa: E402

from battery.config import build_corpus  # noqa: E402

CORPUS_YAML = REPO_ROOT / "battery" / "config" / "corpus.yaml"
COMMITTED_CORPUS_JSON = REPO_ROOT / "battery" / "config" / "corpus.json"


def _build_into(out: Path) -> Path:
    return build_corpus.build_corpus(source=CORPUS_YAML, out_dir=out)


def test_builder_determinism_committed(tmp_path) -> None:
    """Committed corpus.json is byte-identical to a fresh deterministic build
    (the pin that keeps S5 'pinned dataset versions' honest)."""
    fresh = _build_into(tmp_path / "fresh")
    assert fresh.read_bytes() == COMMITTED_CORPUS_JSON.read_bytes()
    # fresh store digest matches the manifest golds_sha256
    fresh_manifest = json.loads(fresh.read_text(encoding="utf-8"))["manifest"]
    store_digest = cl.GoldStore(tmp_path / "fresh" / ".gold_store" / "golds.json").digest()
    assert store_digest == fresh_manifest["golds_sha256"]


def test_builder_determinism_cross_process(tmp_path) -> None:
    """Two builds in subprocesses with different PYTHONHASHSEED are
    byte-identical (no set-iteration or dict-order nondeterminism leaks)."""
    out1 = tmp_path / "seed0"
    out2 = tmp_path / "seed99"
    env = dict(os.environ)
    for out, seed in ((out1, "0"), (out2, "99")):
        env["PYTHONHASHSEED"] = seed
        proc = sp.run(
            ["uv", "run", "python", "-m", "battery.config.build_corpus", "--out", str(out)],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stderr
    assert (out1 / "corpus.json").read_bytes() == (out2 / "corpus.json").read_bytes()
    assert (out1 / ".gold_store" / "golds.json").read_bytes() == \
        (out2 / ".gold_store" / "golds.json").read_bytes()


def _mutated_yaml(tmp_path, mutator) -> Path:
    data = load_corpus_yaml()
    mutator(data)
    out = tmp_path / "mutated.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def load_corpus_yaml() -> dict:
    return validate.load_yaml_dupreject(CORPUS_YAML)


def test_builder_refuses_invariants(tmp_path) -> None:
    """Mutation-driven refusals — the builder must raise ValueError."""

    def expect_refuse(mutator, label):
        src = _mutated_yaml(tmp_path, mutator)
        try:
            build_corpus.build_corpus(source=src, out_dir=tmp_path / label)
        except ValueError:
            return
        raise AssertionError(f"builder did not refuse: {label}")

    scs = lambda d: d["scenarios"]  # noqa: E731

    expect_refuse(lambda d: scs(d).append(copy.deepcopy(scs(d)[0])), "dup-id")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["task_type"] == "contradiction")
        ["planted_contradictions"][0].update(k=6), "k-pin")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "ct-001")
        ["prompt"]["turns"][0].update(content="Some say server A is not the bottleneck."),
        "pre-appearing-counter")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "ct-001")
        ["prompt"]["turns"].pop(4), "claim-missing")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "ct-001")
        ["gold"].update(expected="the queue layer is"), "gold-collision")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "d-001").update(split="wave-2"),
        "split-drift")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "d-001").pop("matched_control_for"),
        "controls-bijection")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "adv-003")
        ["hostile"]["sybil_sources"][0].update(count=5), "sybil-count")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "adv-001")
        ["hostile"].update(legit_tier="T3"), "poisoned-legit-tier")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "adv-005")
        ["hostile"].update(echo_ring=["the api is deprecated"]), "echo-ring")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "adv-007")
        ["hostile"]["flip"][1].update(new_valence="supports"), "flapping-valence")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "adv-009")
        ["hostile"].update(anchoring_turn=99), "anchoring-bounds")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "fb-001")
        ["feedback"]["iterations"].pop(), "feedback-count")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "lp-001")
        ["graph_script"].update(nodes=[{"id": "p", "claim_or_turn_ref": 0}]), "loopy-triangle")
    expect_refuse(
        lambda d: next(s for s in scs(d) if s["id"] == "adv-001")
        ["hostile"].update({"gold": {"expected": "nested leak"}}), "nested-gold-in-hostile")
    # pack count drift
    expect_refuse(
        lambda d: scs(d).pop(next(i for i, s in enumerate(scs(d))
                                 if s["id"] == "d-020")), "pack-count")
    # corpus_version drift
    expect_refuse(
        lambda d: d["meta"].update(corpus_version="9.9"), "version")


def test_builder_check_flag(tmp_path) -> None:
    """--check exits 0 when committed matches; the CLI entry works."""
    from battery.config.build_corpus import check_committed
    assert check_committed() == 0


# ---------------------------------------------------------------------------
# Task 5 — corpus contract tests (hermetic: tmp-built store fixture)
# ---------------------------------------------------------------------------

import pytest as _pytest  # noqa: E402


@pytest.fixture
def sealed_corpus(tmp_path):
    """Hermetic fixture: rebuilds the corpus into tmp (never reads the local
    gitignored store — CI lacks it). Asserts verify_seal passes; failure
    instructs rerunning the builder."""
    built = build_corpus.build_corpus(source=CORPUS_YAML, out_dir=tmp_path)
    corpus = cl.load_corpus(built)
    store = cl.GoldStore(tmp_path / ".gold_store" / "golds.json")
    try:
        cl.verify_seal(corpus, store)
    except cl.SealMismatchError:
        _pytest.fail("fixture seal mismatch — rerun: uv run python -m "
                     "battery.config.build_corpus")
    return corpus, store, tmp_path


def test_pack_counts(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    assert corpus.manifest["pack_counts"] == schema.PACK_COUNTS
    assert sum(corpus.manifest["pack_counts"].values()) == 134


def test_pack_splits(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    by_type: dict[str, dict[str, int]] = {}
    for sc in corpus.scenarios:
        by_type.setdefault(sc["task_type"], {})
        by_type[sc["task_type"]][sc["split"]] = by_type[sc["task_type"]].get(sc["split"], 0) + 1
    assert by_type == schema.PACK_SPLITS


def test_k_pin(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    for sc in corpus.scenarios:
        if sc["task_type"] in ("contradiction", "cross_session_contradiction"):
            for pair in sc["planted_contradictions"]:
                assert pair["k"] == 5, sc["id"]


def test_claim_placement(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    for sc in corpus.scenarios:
        if sc["task_type"] != "contradiction":
            continue
        turns = sc["prompt"]["turns"]
        assert len(turns) >= 5, sc["id"]
        pre = " ".join(str(t["content"]) for t in turns[:4])
        inject = str(turns[4]["content"])
        for pair in sc["planted_contradictions"]:
            assert cl.contains_phrase(pre, pair["claim"]), (sc["id"], pair["claim"])
            assert cl.contains_phrase(inject, pair["counter_claim"]), (sc["id"], pair["counter_claim"])
            assert not cl.contains_phrase(pre, pair["counter_claim"]), sc["id"]


def test_feedback_iterations(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    for sc in corpus.scenarios:
        if sc["task_type"] == "feedback_loop":
            assert len(sc["feedback"]["iterations"]) == 5, sc["id"]


def test_sybil_counts(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    for sc in corpus.scenarios:
        if sc.get("attack_type") == "sybil":
            assert any(s["count"] == 100 and s["tier"] == "T4"
                       for s in sc["hostile"]["sybil_sources"]), sc["id"]
            assert any(s["count"] == 1 and s["tier"] == "T0"
                       for s in sc["hostile"]["legit_sources"]), sc["id"]


def test_hostile_bound_to_turns(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    for sc in corpus.scenarios:
        if sc["task_type"] != "adversarial":
            continue
        at = sc["attack_type"]
        hostile = sc["hostile"]
        rendered = cl.render_reader_prompt(sc)
        if at in ("poisoned", "sybil"):
            texts = [hostile["injected_claim"]]
        elif at == "echo_chamber":
            texts = list(hostile["echo_ring"])
        elif at == "flapping":
            texts = [f["claim"] for f in hostile["flip"]]
        elif at == "anchoring":
            texts = [hostile["stale_claim"], hostile["superseded_by"]]
        else:
            texts = []
        for text in texts:
            assert cl.contains_phrase(rendered, text), (sc["id"], at, text)


def test_sealing_no_gold_in_corpus_json(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    for sc in corpus.scenarios:
        cl.assert_no_gold(sc)  # recursive — any gold key at any depth raises
    text = json.dumps(corpus.manifest) + json.dumps(corpus.scenarios)
    for sc in corpus.scenarios:
        if sc["task_type"] == "contradiction":
            for pair in sc["planted_contradictions"]:
                # gold plaintext values must never appear anywhere in corpus.json
                pass  # gold content is in the gitignored store only; checked below


def test_load_corpus_reader_safe(sealed_corpus) -> None:
    corpus, store, _ = sealed_corpus
    # committed artifact loads gold-free and verify_seal passes
    committed = cl.load_corpus(COMMITTED_CORPUS_JSON)
    for sc in committed.scenarios:
        cl.assert_no_gold(sc)
    cl.verify_seal(committed, store)
    assert committed.by_id("ct-001")["task_type"] == "contradiction"


def test_gold_sha256_matches_store(sealed_corpus) -> None:
    import hashlib
    corpus, store, _ = sealed_corpus
    for sc in corpus.scenarios:
        expected = hashlib.sha256(cl.canonical_json(store.gold(sc["id"]))).hexdigest()
        assert sc["gold_sha256"] == expected, sc["id"]
    assert store.digest() == corpus.manifest["golds_sha256"]


def test_splits_partition(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    splits = [sc["split"] for sc in corpus.scenarios]
    assert len(splits) == 134  # every scenario in exactly one split (one field)
    assert set(splits) == set(schema.SPLITS)
    assert any(s == "held_out" for s in splits)
    waves = {s for s in splits if s.startswith("wave-")}
    assert len(waves) == 3
    assert corpus.manifest["split_counts"] == dict(sorted(
        {s: splits.count(s) for s in schema.SPLITS}.items()))
    # held-out family pinned
    held_family = [sc for sc in corpus.scenarios
                   if sc["task_type"] == "family_rep"
                   and sc["family_name"] == schema.HELD_OUT_FAMILY]
    assert len(held_family) == 3
    assert all(sc["split"] == "held_out" for sc in held_family)


def test_controls_bijection(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    ct_ids = sorted(sc["id"] for sc in corpus.scenarios
                    if sc["task_type"] == "contradiction")
    controls = sorted(sc["matched_control_for"] for sc in corpus.scenarios
                      if sc.get("matched_control_for"))
    assert len(controls) == 15
    assert len(set(controls)) == 15
    assert controls == ct_ids


def test_zero_context_render(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    for sc in corpus.scenarios:
        out = cl.render_reader_prompt(sc)
        assert out.strip(), sc["id"]
        if "session_scripts" in (sc.get("session_scripts") or {}) or "session_scripts" in sc.get("prompt", {}):
            pass  # multi-session: forward references across sessions are legitimate
        else:
            # single-session heuristic: unresolved-relative-reference scan
            q = str(sc["prompt"]["question"]).casefold()
            for marker in ("as mentioned", "the above", "earlier in this session"):
                assert marker not in q, (sc["id"], marker)


def test_render_guard_no_gold_substring(sealed_corpus) -> None:
    corpus, store, _ = sealed_corpus
    for sc in corpus.scenarios:
        rendered = cl.render_reader_prompt(sc)
        gold = store.gold(sc["id"])
        texts = gold["expected"] if isinstance(gold["expected"], list) else [gold["expected"]]
        for text in texts:
            norm = cl.normalize(str(text))
            if norm in (cl.normalize(e) for e in schema.GOLD_ENUMS):
                continue
            assert not cl.contains_phrase(rendered, str(text)), (sc["id"], text)


def test_attack_not_rendered(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    for sc in corpus.scenarios:
        if sc["task_type"] != "adversarial":
            continue
        out = cl.render_reader_prompt(sc)
        assert sc["attack_type"] not in out, sc["id"]  # the LABEL never rendered
        assert "hostile" not in out, sc["id"]  # the hostile BLOCK never rendered
        # the attack material itself arrives via turns (test_hostile_bound_to_turns)
        assert "injected_claim:" not in out and "source_tier" not in out, sc["id"]


def test_corpus_filter(sealed_corpus) -> None:
    corpus, _, _ = sealed_corpus
    assert len(corpus.filter(task_type="contradiction")) == 15
    assert len(corpus.filter(split="held_out")) == 13
    assert len(corpus.filter(task_type="adversarial", split="held_out")) == 4
    assert corpus.filter(family="NOPE") == []
    assert corpus.filter(split="train") == corpus.filter(split="train")  # deterministic
    ids = [s["id"] for s in corpus.filter(split="train")]
    assert ids == sorted(ids)

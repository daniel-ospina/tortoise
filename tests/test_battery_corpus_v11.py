"""Corpus v1.1 tests — bct-* benign FP surface twins (#2284, Task 3).

Covers the corpus-v1.1 contract through the REAL validation machinery:
the 6 bct-* benign contradiction controls (surface twins of ct-001..ct-006,
policy text verbatim except the benign question + ¬A turn), the per-set
control bijection, the v1.1 pack-count/split re-lock, the _ID_PATTERNS
alternation (validate.py — NOT schema.py), the control-set exemption from
the contradiction bindings, bct gold sealing (gold_sha256 ↔ store), the
SHARED ``surface_diff`` predicate (battery/config/control_diff.py — the
single home Tasks 3 AND 4 import), benignity (no ¬A fragment on the twin
surface), and the R1 FP-pool denominator.

Store-dependent tests are HERMETIC (tmp-built store fixture — mirrors
tests/test_battery_corpus.py::sealed_corpus): they never read the gitignored
local store (CI lacks it).
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.config import schema  # noqa: E402
from battery.config import build_corpus, corpus_loader as cl, validate  # noqa: E402
from battery.config.control_diff import (  # noqa: E402
    NEG_A_DELTA_SLOTS,
    surface_diff,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_YAML = REPO_ROOT / "battery" / "config" / "corpus.yaml"
ARMS_YAML = REPO_ROOT / "battery" / "config" / "arms.yaml"
BCT_IDS = [f"bct-{n:03d}" for n in range(1, 7)]
CT_SEEDS = [f"ct-{n:03d}" for n in range(1, 7)]


# ---------------------------------------------------------------------------
# Hermetic sealed-corpus fixture (tmp-built — never the local gitignored store)
# ---------------------------------------------------------------------------

@pytest.fixture()
def sealed(tmp_path):
    built = build_corpus.build_corpus(source=CORPUS_YAML, out_dir=tmp_path)
    corpus = cl.load_corpus(built)
    store = cl.GoldStore(tmp_path / ".gold_store" / "golds.json")
    cl.verify_seal(corpus, store)  # fails loudly on a stale/partial seal
    by_id = {sc["id"]: sc for sc in corpus.scenarios}
    return corpus, store, by_id


# ---------------------------------------------------------------------------
# Schema v1.1 re-lock (schema.py — the sanctioned surface)
# ---------------------------------------------------------------------------

def test_schema_v11_relock() -> None:
    """CORPUS_VERSION 1.1; contradiction 15→21; total 134→140; bct all train."""
    assert schema.CORPUS_VERSION == "1.1"
    assert schema.PACK_COUNTS["contradiction"] == 21
    assert sum(schema.PACK_COUNTS.values()) == 140
    assert schema.PACK_SPLITS["contradiction"] == {"train": 21}


# ---------------------------------------------------------------------------
# bct twins present + shaped (sealed corpus)
# ---------------------------------------------------------------------------

def test_bct_twins_present_and_shaped(sealed) -> None:
    corpus, store, by_id = sealed
    assert corpus.manifest["corpus_version"] == "1.1"
    for n, bct_id in enumerate(BCT_IDS, start=1):
        ct_id = f"ct-{n:03d}"
        sc = by_id[bct_id]
        assert sc["task_type"] == "contradiction", bct_id
        assert sc["family"] == "R1", bct_id
        assert sc["split"] == "train", bct_id
        assert sc["control_set"] == "bct", bct_id
        assert sc["matched_control_for"] == ct_id, bct_id
        # benign twin: no planted pair, no k (the ct twin's ¬A machinery)
        assert "planted_contradictions" not in sc, bct_id
        assert "k" not in sc, bct_id
        # surface-twin turn skeleton == the ct twin's turn count (5 turns)
        ct_sc = by_id[ct_id]
        assert len(sc["prompt"]["turns"]) == len(ct_sc["prompt"]["turns"]), bct_id
        # sealed gold entry present for every bct id
        gold = store.gold(bct_id)
        assert str(gold.get("expected", "")).strip(), bct_id


# ---------------------------------------------------------------------------
# Per-set control bijection over the sealed corpus (validate_controls contract)
# ---------------------------------------------------------------------------

def test_controls_bijection_per_set(sealed) -> None:
    corpus, _, by_id = sealed
    # planted ct population = contradiction scenarios WITH a planted pair
    planted_ct = sorted(
        sc["id"] for sc in corpus.scenarios
        if sc["task_type"] == "contradiction" and sc.get("planted_contradictions"))
    assert planted_ct == [f"ct-{n:03d}" for n in range(1, 16)]
    # decision controls: 1:1 exactly-once over the planted ct population;
    # the d-* ct-domain never includes benign bct ids
    decision = sorted(
        sc["matched_control_for"] for sc in corpus.scenarios
        if sc["task_type"] == "decision" and sc.get("matched_control_for"))
    assert len(decision) == 15 and len(set(decision)) == 15
    assert decision == planted_ct
    assert all(t in planted_ct for t in decision)  # d-* domain excludes bct
    # bct benign controls: exactly-once over the six smoke ct seeds
    bct_refs = sorted(
        sc["matched_control_for"] for sc in corpus.scenarios
        if sc.get("control_set") == "bct")
    assert bct_refs == CT_SEEDS
    assert len(bct_refs) == len(BCT_IDS) == 6  # every bct twin carries a control
    # ownership ceiling: ≤2 owners per ct across ALL sets (decision + bct twin)
    owners: dict[str, list[str]] = {}
    for sc in corpus.scenarios:
        mcf = sc.get("matched_control_for")
        if mcf:
            owners.setdefault(str(mcf), []).append(str(sc["id"]))
    for cid, owner_ids in owners.items():
        assert len(owner_ids) <= 2, (cid, owner_ids)
        assert cid in planted_ct, (cid, owner_ids)  # targets are planted cts


def test_pack_counts_and_splits_v11(sealed) -> None:
    corpus, _, _ = sealed
    assert corpus.manifest["pack_counts"]["contradiction"] == 21
    assert sum(corpus.manifest["pack_counts"].values()) == 140
    splits = [sc["split"] for sc in corpus.scenarios]
    assert len(splits) == 140
    assert splits.count("train") == 71


# ---------------------------------------------------------------------------
# _ID_PATTERNS alternation + control-set exemption (validate.py machinery)
# ---------------------------------------------------------------------------

def _benign_dict(bct_id: str = "bct-001", *, control_set: bool = True) -> dict:
    """A minimal valid benign twin shape (dict-level — validate_scenario's
    real input). Gold phrase-level + no collision with the prompt (the
    builder's no-substring rule runs inside validate_scenario)."""
    sc = {
        "id": bct_id,
        "tier": "probe",
        "family": "R1",
        "task_type": "contradiction",
        "split": "train",
        "prompt": {
            "system": "You are advising a team on a technical question.",
            "turns": [
                {"role": "user", "content": "We are deciding whether server A is the bottleneck."},
                {"role": "assistant", "content": "Initial telemetry suggests server A is the bottleneck."},
                {"role": "user", "content": "Load tests on the database show normal latency."},
                {"role": "assistant", "content": "The team leans toward upgrading server A."},
                {"role": "user", "content": "A follow-up trace confirms server A is the bottleneck."},
            ],
            "question": "What should the team do next?",
        },
        "gold": {"expected": "confirm the diagnosis and schedule the upgrade without raising a conflict"},
    }
    if control_set:
        sc["control_set"] = "bct"
        sc["matched_control_for"] = "ct-001"
    return sc


def test_id_pattern_alternation_accepts_ct_and_bct() -> None:
    """_ID_PATTERNS["contradiction"] alternation: ct-<nnn> AND bct-<nnn>
    fullmatch; bcx-<nnn> fails (validate.py — the pattern applies to every
    contradiction task_type id)."""
    ok = _benign_dict("bct-001")
    assert validate.validate_scenario(ok, {"bct-001"}, {"ct-001"}) == []
    ct = _benign_dict("ct-001", control_set=False)  # planted ct — never a control_set twin
    ct["prompt"]["turns"][4]["content"] = (  # real ¬A at the k=5 injection turn
        "A new trace shows server A is not the bottleneck; the queue layer is.")
    ct["question"] = "Is server A the bottleneck, and what should we do?"
    ct["planted_contradictions"] = [
        {"claim": "server A is the bottleneck",
         "counter_claim": "server A is not the bottleneck", "k": 5}]
    assert validate.validate_scenario(ct, {"ct-001"}, {"ct-001"}) == []
    bad = _benign_dict("bcx-001")
    errs = validate.validate_scenario(bad, {"bcx-001"}, set())
    assert any("does not match" in e for e in errs), errs
    # pattern literal (documentation lock): fullmatch parity with the validator
    pattern = r"(?:ct|bct)-\d{3}"
    assert re.fullmatch(pattern, "ct-001") and re.fullmatch(pattern, "bct-001")
    assert not re.fullmatch(pattern, "bcx-001")


def test_control_set_exempt_from_contradiction_bindings() -> None:
    """Benign twins (control_set) skip _validate_contradiction_bindings — no
    planted pair / no counter-claim at k-1 is REQUIRED of them."""
    sc = _benign_dict("bct-001")
    errs = validate.validate_scenario(sc, {"bct-001", "ct-001"}, {"ct-001"})
    assert errs == [], f"benign twin must validate clean: {errs}"
    # a control-set twin that IS planted is self-contradictory → refused
    planted = _benign_dict("bct-001")
    planted["planted_contradictions"] = [
        {"claim": "server A is the bottleneck",
         "counter_claim": "server A is not the bottleneck", "k": 5}]
    errs = validate.validate_scenario(planted, {"bct-001", "ct-001"}, {"ct-001"})
    assert any("planted_contradictions forbidden" in e for e in errs), errs


def test_exemption_never_applies_to_planted_ct() -> None:
    """The exemption keys on control_set — a ct-* planted scenario without
    the marker still needs its planted pair (no exemption leak)."""
    bare = _benign_dict("ct-777", control_set=False)  # benign SHAPE, no marker
    errs = validate.validate_scenario(bare, {"ct-777"}, {"ct-777"})
    assert any("no planted_contradictions" in e for e in errs), errs


# ---------------------------------------------------------------------------
# bct golds: authored + sealed + store-consistent (hermetic)
# ---------------------------------------------------------------------------

def test_bct_gold_sha256_matches_store(sealed) -> None:
    _, store, by_id = sealed
    for bct_id in BCT_IDS:
        expected = hashlib.sha256(cl.canonical_json(store.gold(bct_id))).hexdigest()
        assert by_id[bct_id]["gold_sha256"] == expected, bct_id


def test_bct_gold_no_render_collision(sealed) -> None:
    """bct golds never collide with the twin render (render-guard invariant
    applies to the bct population like every other scenario)."""
    _, store, by_id = sealed
    for bct_id in BCT_IDS:
        rendered = cl.render_reader_prompt(by_id[bct_id])
        gold = store.gold(bct_id)
        texts = gold["expected"] if isinstance(gold["expected"], list) else [gold["expected"]]
        for text in texts:
            assert not cl.contains_phrase(rendered, str(text)), (bct_id, text)


# ---------------------------------------------------------------------------
# surface_diff — the SHARED predicate (control_diff.py)
# ---------------------------------------------------------------------------

def test_surface_twins_match_all_six(sealed) -> None:
    """Every bct twin is a surface twin of its ct: policy text byte-identical
    EXCEPT the benign question + ¬A turn (shared predicate, never raw hash
    equality). Plan acceptance: surface_diff(ct-N, bct-N) <= NEG_A_DELTA_SLOTS."""
    for n in range(1, 7):
        ct_id, bct_id = f"ct-{n:03d}", f"bct-{n:03d}"
        diff = surface_diff(ct_id, bct_id)
        assert diff <= NEG_A_DELTA_SLOTS, (ct_id, bct_id, diff)
        # both deltas are REAL (the substitution actually happened): the
        # benign question differs AND the ¬A turn differs
        assert diff == NEG_A_DELTA_SLOTS, (ct_id, bct_id, diff)


def test_surface_diff_works_on_sealed_scenarios(sealed) -> None:
    """surface_diff accepts sealed corpus.json scenario dicts (id→dict) — the
    prompt slots survive the seal untouched (only gold is redacted)."""
    _, _, by_id = sealed
    diff = surface_diff("ct-002", "bct-002", scenarios=by_id)
    assert diff <= NEG_A_DELTA_SLOTS
    # sealed data includes gold_sha256 — never part of the policy surface
    assert "gold_sha256" not in diff


def test_surface_diff_rejects_extra_delta() -> None:
    """A non-twin mutation (a mid-conversation turn changed) is a delta OUTSIDE
    the allowed slots → the pair is not a valid twin."""
    ct = _benign_dict("ct-001")
    ct["planted_contradictions"] = [
        {"claim": "server A is the bottleneck",
         "counter_claim": "server A is not the bottleneck", "k": 5}]
    bct = _benign_dict("bct-001")
    bct["prompt"]["turns"][1]["content"] = "The team has not reached a view yet."
    src = {"ct-001": ct, "bct-001": bct}
    diff = surface_diff("ct-001", "bct-001", scenarios=src)
    assert "turn_1" in diff
    assert not diff <= NEG_A_DELTA_SLOTS


def test_surface_diff_missing_id_raises() -> None:
    with pytest.raises(ValueError):
        surface_diff("ct-001", "bct-999")


def test_benign_twin_surface_has_no_neg_a(sealed) -> None:
    """Benignity lock: the twin's rendered surface contains NONE of its ct
    twin's planted counter-claim fragments (no ¬A anywhere on the FP pool's
    surface — a conflict-free prompt the agent can only false-positive on)."""
    _, _, by_id = sealed
    for n in range(1, 7):
        ct_id, bct_id = f"ct-{n:03d}", f"bct-{n:03d}"
        ct_sc, bct_sc = by_id[ct_id], by_id[bct_id]
        counters = [p["counter_claim"] for p in ct_sc.get("planted_contradictions", [])]
        assert counters, ct_id  # the ct twin IS planted (non-vacuous)
        rendered = cl.render_reader_prompt(bct_sc)
        for counter in counters:
            assert not cl.contains_phrase(rendered, counter), (bct_id, counter)


# ---------------------------------------------------------------------------
# R1 FP-pool denominator (E2E-1.2 FP ≤5% gate needs n ≥ 36)
# ---------------------------------------------------------------------------

def test_fp_pool_denominator(sealed) -> None:
    """FP denominator = bct episodes pooled across arms × runs. 6 bct twins ×
    ≥2 arms × ≥3 runs ⇒ ≥36 episodes: at n=36 a single FP = 2.78% ≤ 5%
    (gate can pass); at n=18 a single FP = 5.56% > 5% — so the pool must
    never settle at 18. The ≥2 arms term is read from arms.yaml (real
    config), never a test-local constant."""
    from battery.config import load_arms
    arms = load_arms(ARMS_YAML)
    n_bct = len([sc for sc in sealed[0].scenarios if sc.get("control_set") == "bct"])
    assert n_bct == 6
    n_arms = len(arms)
    assert n_arms >= 2
    n_episodes = n_bct * n_arms * 3
    assert n_episodes >= 36, f"FP pool too small: {n_episodes}"
    assert 1 / 36 <= 0.05 < 1 / 18  # why 36 is the floor, never 18

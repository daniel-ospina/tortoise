"""The paired A/B of the #2518 expansion — statistics, snapshot, and the pair.

Three groups, deliberately separated so a reviewer can see which claim each one
carries:

* **The statistics** are pure and hermetic: the exact McNemar tail, the MDE
  solvers, the bootstrap, the correlation. They are pinned to values computed by
  hand (and, for the MDE, to the arithmetic `#13118` quoted: ~755 per arm for
  5 pp at @15 — reproduced here from the formula rather than trusted).
* **The frozen corpus and the drawn sample** are file-level: a snapshot must
  verify byte-for-byte, a tampered copy must be REFUSED, and the population file
  must be refused the moment it was drawn against a different corpus. These are
  the falsifiers "the snapshot exists with its digest" and "the same items are
  used in both arms".
* **The pair itself** is live (skips without a FalkorDB): a two-session synthetic
  corpus, both arms over the SAME items, and the claim the whole tool exists to
  support — the point arm reaches the mechanism and the object arm cannot.
"""
from __future__ import annotations

import json
import math
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tests import _live_utils
from tools import paired_expansion_ab_2518 as P


# ── 1. the statistics ────────────────────────────────────────────────────────

def test_mcnemar_exact_matches_hand_computed_tails():
    """Two-sided exact binomial at p=0.5, by hand.

    (0,0) is p=1 by construction: no discordant pair is no evidence of a
    difference — and, the point the tool keeps making elsewhere, no evidence of
    no difference either.
    """
    assert P.mcnemar_exact(0, 0) == 1.0
    # n=12, k=2: 2*(1 + 12 + 66)/4096
    assert P.mcnemar_exact(10, 2) == pytest.approx(158 / 4096)
    # symmetric in b and c
    assert P.mcnemar_exact(2, 10) == pytest.approx(P.mcnemar_exact(10, 2))
    # balanced => 1.0 (two identical one-sided tails sum past 1)
    assert P.mcnemar_exact(5, 5) == 1.0
    # all-discordant-one-way at n=5: 2*(1/32)
    assert P.mcnemar_exact(5, 0) == pytest.approx(2 / 32)
    # monotone: more one-way discordance is stronger evidence
    assert P.mcnemar_exact(6, 0) < P.mcnemar_exact(5, 0)


def test_norm_ppf_hits_the_canonical_quantiles():
    assert P._norm_ppf(0.975) == pytest.approx(1.959964, abs=5e-6)
    assert P._norm_ppf(0.80) == pytest.approx(0.841621, abs=5e-6)
    assert P._norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
    assert P._norm_ppf(0.025) == pytest.approx(-1.959964, abs=5e-6)


def test_detectable_effect_shrinks_with_n_and_grows_with_discordance():
    """The two directions that make the MDE a gate rather than a flourish."""
    wide = P.detectable_effect(100, 0.20)
    narrow = P.detectable_effect(400, 0.20)
    assert narrow < wide, "4x the items must resolve a smaller effect"
    assert P.detectable_effect(300, 0.10) < P.detectable_effect(300, 0.40), (
        "more discordance is more variance, so a LARGER delta is needed")


def test_the_briefs_sample_size_arithmetic_reproduces_from_the_formula():
    """`#13118` published "~755 per arm to detect 5 pp, ~2,100 for 3 pp".

    Those were quoted, not derived. Reproducing them from the independent-design
    formula is what makes the "n=300 cannot resolve 3 pp" statement evidence
    rather than assertion.
    """
    mde_at_15 = P.independent_effect(300, 0.865)   # the @15 rate regime
    assert mde_at_15 == pytest.approx(0.078, abs=0.002)
    n_for_5pp = 300 * (mde_at_15 / 0.05) ** 2
    n_for_3pp = 300 * (mde_at_15 / 0.03) ** 2
    assert 700 <= n_for_5pp <= 800, f"expected ~755, got {n_for_5pp:.0f}"
    assert 1900 <= n_for_3pp <= 2300, f"expected ~2100, got {n_for_3pp:.0f}"


def test_paired_design_needs_far_fewer_items_than_two_independent_arms():
    """The whole reason this lane is paired: same n, smaller MDE.

    At a discordance of 0.10 the paired MDE beats the independent one by a wide
    margin; the ratio is reported in the artifact so the claim is arithmetic.
    """
    paired = P.detectable_effect(300, 0.10)
    independent = P.independent_effect(300, 0.75)
    assert paired < independent, (paired, independent)
    assert independent / paired > 1.5


def test_bootstrap_ci_brackets_the_delta_and_is_seeded():
    off = [0] * 18 + [1] * 2
    on = [0] * 8 + [1] * 12
    a = P.paired_bootstrap(off, on, resamples=400, seed=11)
    b = P.paired_bootstrap(off, on, resamples=400, seed=11)
    assert a == b, "the same seed must give the same CI"
    assert a["ci95"][0] <= a["delta"] <= a["ci95"][1]
    assert a["delta"] == pytest.approx(0.5)


def test_correlation_is_undefined_when_an_arm_is_at_floor():
    """A class-conditional item set makes the OFF arm 0/ n by construction.

    A zero-variance arm has no correlation to measure — reported as nan, never as
    a large number that would silently overstate the pairing benefit.
    """
    out = P.outcome_correlation([0] * 20, [1] * 5 + [0] * 15)
    assert math.isnan(out["rho"])
    assert math.isnan(out["se_reduction"])


def test_analyze_reports_the_mde_beside_the_verdict():
    off = [0] * 30
    on = [1] * 8 + [0] * 22
    row = P.analyze(off, on, ks=P.KS, bootstrap=200, seed=3)
    assert row["n"] == 30
    assert row["discordant_miss_to_hit"] == 8
    assert row["discordant_hit_to_miss"] == 0
    assert row["mcnemar_exact_p"] == pytest.approx(2 / 256)
    assert row["mde_paired"] > 0
    assert row["mde_independent"] > 0


# ── 2. the frozen corpus and the drawn sample ───────────────────────────────

def _write_corpus(root: Path, *, names: list[str], sessions: int = 2) -> None:
    """A tiny two-session corpus in the shape `candidate_recall_11901.harvest` reads.

    ⛔ The name is followed by PUNCTUATION, never by another lowercase word. The
    harvest's ``_DD`` rule is greedy over up to four lowercase tokens, so a
    fixture that wrote "check the road bike repairs in run 0" harvested
    ``the road bike repairs in`` — a different name, and a false red in the
    item-building test.
    """
    root.mkdir(parents=True, exist_ok=True)
    for s in range(sessions):
        lines = []
        for i, name in enumerate(names):
            lines.append(json.dumps({
                "session_id": f"sess-{s}",
                "conversation": [
                    {"role": "user",
                     "content": f"What should we do about {name}?"},
                    {"role": "assistant",
                     "content": f"We reviewed {name} today, note {i} of session {s}."},
                ],
            }))
        # a record that mentions NO sampled name — the target pool
        lines.append(json.dumps({
            "session_id": f"sess-{s}",
            "conversation": [
                {"role": "user", "content": f"unrelated topic {s} about dinner plans"},
            ],
        }))
        (root / f"2026-01-0{s + 1}.jsonl").write_text("\n".join(lines) + "\n")


def test_snapshot_freeze_records_and_then_verifies(tmp_path):
    src = tmp_path / "live"
    _write_corpus(src, names=["the road bike repairs"])
    dst = tmp_path / "snap"
    man = P.freeze_snapshot(src, dst)
    assert man["file_count"] == 2
    assert man["digest"] == P.verify_snapshot(dst)["digest"]
    # a second freeze call VERIFIES rather than re-copies
    again = P.freeze_snapshot(src, dst)
    assert again["digest"] == man["digest"]
    # and the manifest is on disk with the file list
    on_disk = json.loads((dst / "MANIFEST.json").read_text())
    assert {r["name"] for r in on_disk["files"]} == {
        "2026-01-01.jsonl", "2026-01-02.jsonl"}
    assert all(r["sha256"] and r["bytes"] > 0 for r in on_disk["files"])


def test_a_tampered_snapshot_is_refused_never_renamed(tmp_path):
    src = tmp_path / "live"
    _write_corpus(src, names=["the road bike repairs"])
    dst = tmp_path / "snap"
    P.freeze_snapshot(src, dst)
    victim = dst / "2026-01-01.jsonl"
    victim.write_text(victim.read_text() + '{"session_id":"late"}\n')
    with pytest.raises(P.Refused, match="MODIFIED"):
        P.verify_snapshot(dst)


def test_snapshot_digest_is_order_independent():
    """The digest is a property of the CONTENT, not of ``glob`` order."""
    a = {"files": [{"name": "b.jsonl", "sha256": "22", "bytes": 2},
                   {"name": "a.jsonl", "sha256": "11", "bytes": 1}]}
    b = {"files": list(reversed(a["files"]))}
    assert P.snapshot_digest(a) == P.snapshot_digest(b)


def test_records_keep_turns_and_their_roles(tmp_path):
    src = tmp_path / "live"
    _write_corpus(src, names=["the road bike repairs"])
    man = P.freeze_snapshot(src, tmp_path / "snap")
    assert man["file_count"] == 2
    recs = P.read_records(tmp_path / "snap")
    assert recs and all(r["turns"] for r in recs)
    assert recs[0]["turns"][0]["role"] == "user"
    assert "lower" in recs[0]["turns"][0]


def test_population_is_drawn_once_and_refused_when_the_corpus_moved(tmp_path):
    from tools.candidate_recall_11901 import harvest
    src = tmp_path / "live"
    _write_corpus(src, names=["the road bike repairs", "the plan doc",
                              "the merge base", "PR #9999", "snake_case_name",
                              "tortoise/sdk.py"])
    man = P.freeze_snapshot(src, tmp_path / "snap")
    snap = tmp_path / "snap"
    harvested = harvest(snap)
    pop_file = tmp_path / "pop.json"
    rows, body = P.load_or_make_population(
        pop_file, harvested=harvested, snapshot_digest_=man["digest"],
        population=40, sample=6, seed=5)
    assert pop_file.exists()
    assert body["names_sha256"]
    # the SAME file re-read returns the SAME items, ids included
    rows2, body2 = P.load_or_make_population(
        pop_file, harvested=harvested, snapshot_digest_=man["digest"],
        population=40, sample=6, seed=5)
    assert [r["item_id"] for r in rows] == [r["item_id"] for r in rows2]
    assert [r["name"] for r in rows] == [r["name"] for r in rows2]
    assert body2["names_sha256"] == body["names_sha256"]
    # drawn against another corpus => refused, not silently reused
    with pytest.raises(P.Refused, match="drawn against snapshot"):
        P.load_or_make_population(pop_file, harvested=harvested,
                                  snapshot_digest_="0" * 64, population=40,
                                  sample=6, seed=5)
    # a hand-edited item list is refused
    tampered = json.loads(pop_file.read_text())
    tampered["items"][0]["name"] = "the invented name"
    pop_file.write_text(json.dumps(tampered))
    with pytest.raises(P.Refused, match="internally inconsistent"):
        P.load_or_make_population(pop_file, harvested=harvested,
                                  snapshot_digest_=man["digest"], population=40,
                                  sample=6, seed=5)


def test_draw_is_deterministic_for_a_seed_and_a_population(tmp_path):
    from tools.candidate_recall_11901 import harvest
    src = tmp_path / "live"
    names = [f"the item number {w}" for w in
             ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta",
              "theta", "iota", "kappa")] + ["PR #1234", "snake_case_name",
                                              "tortoise/sdk.py"]
    _write_corpus(src, names=names)
    snap = tmp_path / "snap"
    P.freeze_snapshot(src, snap)
    harvested = harvest(snap)
    a = P.draw_population(harvested, population=40, sample=12, seed=99)
    b = P.draw_population(harvested, population=40, sample=12, seed=99)
    c = P.draw_population(harvested, population=40, sample=12, seed=100)
    assert [r["name"] for r in a] == [r["name"] for r in b]
    assert a[0]["item_id"] == "item-000000"
    assert [r["name"] for r in a] != [r["name"] for r in c] or len(a) != len(c)


def test_build_items_targets_a_different_session_without_the_name(tmp_path):
    from tools.candidate_recall_11901 import harvest
    src = tmp_path / "live"
    _write_corpus(src, names=["the road bike repairs", "the plan doc",
                              "the merge base", "PR #9999", "snake_case_name",
                              "tortoise/sdk.py"])
    snap = tmp_path / "snap"
    P.freeze_snapshot(src, snap)
    recs = P.read_records(snap)
    harvested = harvest(snap)
    rows = P.draw_population(harvested, population=40, sample=6, seed=1)
    assert rows, harvested
    items, dropped = P.build_items(rows, recs, query_chars=200, key_tokens=4,
                                   min_query_tokens=3)
    assert items, f"no item built (dropped={dropped})"
    for it in items:
        assert it["query_session"] != it["target_session"]
        assert it["name"].lower() not in it["target_text"].lower(), (
            "the target must NOT contain the name — that is what makes it the "
            "key-only join the expansion exists for")
        assert it["target_keys"]
        assert it["query"]


def test_point_ids_are_deterministic_and_canonical():
    a = P._point_id("t", "item-000003")
    b = P._point_id("t", "item-000003")
    assert a == b, "a non-deterministic id would let a tie-break move a rank"
    assert a != P._point_id("q", "item-000003")
    head, tail = a.split("-")
    assert len(head) == 12 and len(tail) == 12
    int(head, 16) and int(tail, 16)   # both halves are hex


def test_displacement_counts_ids_the_merged_pass_moved_out():
    off = {"a": {"ids": ["x", "y", "z"]}, "b": {"ids": []}}
    on = {"a": {"ids": ["z", "x", "w"]}, "b": {"ids": ["q"]}}
    out = P._displacement(off, on, ["a", "b"], k=3)
    assert out["items_with_off_topk"] == 1
    assert out["displaced_ids"] == 1          # y fell out of the window
    assert out["abs_rank_shift_of_kept"] == 3  # x 0->1 (1), z 2->0 (2)


# ── 3. the pair, live ───────────────────────────────────────────────────────

def _falkordb_available() -> bool:
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if not uri.startswith(("docker://", "redis://")):
        return False
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri.rstrip('/')}_probe"
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()
        sdk._get_proj().g.query("RETURN 1")
        sdk.close()
        return True
    except Exception:
        return False
    finally:
        if old is not None:
            os.environ["TORTOISE_DB_URI"] = old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


LIVE = _falkordb_available()

live = pytest.mark.skipif(
    not LIVE, reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane)")


@live
def test_the_point_arm_reaches_the_lever_and_the_object_arm_cannot(tmp_path,
                                                                  monkeypatch):
    """The claim this tool exists for, end to end on a two-session corpus.

    * the POINT arm's ON side finds targets the OFF side cannot,
    * the OBJECT arm's two sides are the SAME ranked list for every item — the
      gate, not a null effect — and the tool says ``structurally_blind``,
    * every item id is identical across the arms (they read one sample).
    """
    uri = os.environ["TORTOISE_DB_URI"].rstrip("/")
    monkeypatch.setenv("TORTOISE_DB_URI", f"{uri}_{uuid.uuid4().hex[:10]}")
    monkeypatch.delenv("TORTOISE_ENTITY_KEY_DENSE", raising=False)
    src = tmp_path / "live"
    _write_corpus(src, names=["the road bike repairs", "the plan doc",
                              "the merge base", "PR #9999", "snake_case_name",
                              "tortoise/sdk.py", "the admin rail",
                              "the verdict doc"])
    out = tmp_path / "artifact.json"
    code = P.main([
        "--sessions-dir", str(src),
        "--freeze", str(tmp_path / "snap"),
        "--population", "40", "--sample", "12", "--distractors", "4",
        "--read-limit", "20", "--min-items", "1", "--bootstrap", "200",
        "--seed", "5", "--out", str(out),
    ])
    art = json.loads(out.read_text())
    assert code in (0, 3), art["refusals"]
    assert art["snapshot"]["digest"]
    assert art["items"]["with_item"] >= 1
    point, obj = art["arms"]["point"], art["arms"]["object"]
    assert point["structurally_blind"] is False
    assert obj["structurally_blind"] is True
    assert obj["expansion_identical_items"] == art["items"]["with_item"]
    # the same items on both sides
    assert set(point["per_item"]) == set(obj["per_item"])
    assert art["items"]["id_digest"]
    # and the mechanism demonstrably changed at least one point read
    assert point["expansion_fired_items"] >= 1

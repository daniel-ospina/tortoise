"""The paired A/B of the #2518 expansion — statistics, snapshot, and the pair.

Three groups, deliberately separated so a reviewer can see which claim each one
carries:

* **The statistics** are pure and hermetic: the exact McNemar tail, the MDE
  solvers, the bootstrap, the correlation. They are pinned to values computed by
  hand (and, for the MDE, against an earlier estimate of ~755 per arm for 5 pp at
  @15 — ⛔ quoted, and published nowhere this repo can point to: not in `#13118`'s
  body, its comments, or its artifacts. What is pinned is the DERIVATION, which
  is what replaces the quote).
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

import argparse
import itertools
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

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
    """An earlier estimate said "~755 per arm to detect 5 pp, ~2,100 for 3 pp".

    ⛔ That estimate is *quoted, not derived*, and it is published nowhere this
    repo can point to — not in `#13118`'s body, its comments, or its artifacts
    (`#13267` review 3). So this does not "reproduce" a source; it DERIVES the
    figures from the independent-design formula, which is what makes the "n=300
    cannot resolve 3 pp" statement evidence rather than assertion. The bounds are
    wide because the point is the order of magnitude, not the last digit.
    """
    mde_at_15 = P.independent_effect(300, 0.865)   # the @15 rate regime
    assert mde_at_15 == pytest.approx(0.078, abs=0.002)
    n_for_5pp = 300 * (mde_at_15 / 0.05) ** 2
    n_for_3pp = 300 * (mde_at_15 / 0.03) ** 2
    assert 700 <= n_for_5pp <= 800, (
        f"expected ~{round(300 * (mde_at_15 / 0.05) ** 2)}, got {n_for_5pp:.0f}")
    assert 1900 <= n_for_3pp <= 2300, (
        f"expected ~{round(300 * (mde_at_15 / 0.03) ** 2):,}, got {n_for_3pp:.0f}")


def test_paired_design_needs_far_fewer_items_than_two_independent_arms():
    """The whole reason this lane is paired: same n, smaller MDE.

    At a discordance of 0.10 the paired MDE beats the independent one by a wide
    margin; the ratio is reported in the artifact so the claim is arithmetic.
    """
    paired = P.detectable_effect(300, 0.10)
    independent = P.independent_effect(300, 0.75)
    assert paired < independent, (paired, independent)
    assert independent / paired > 1.5


def test_a_zero_discordance_run_reports_no_estimable_mde():
    """The defect this pins: two IDENTICAL arms produced ``MDE = 0.0000``.

    Printed beside a null it reads as "infinitely powered", which is the opposite
    of the truth — with no discordant pair the run carries no information about
    the difference. Measured on the object control, where both switch states are
    one code path and every item agrees. Undefined, and said so.
    """
    assert math.isnan(P.detectable_effect(300, 0.0))
    assert math.isnan(P.independent_effect(300, 0.0))
    assert math.isnan(P.independent_effect(300, 1.0))
    # a floor/ceiling rate has zero variance and therefore no estimable MDE
    row = P.analyze([0] * 60, [0] * 60, ks=P.KS, bootstrap=100, seed=1)
    assert math.isnan(row["mde_paired"])
    assert "NOT ESTIMABLE" in row["mde_note"]
    # …while a real all-discordant-one-way run still gets a number
    row2 = P.analyze([0] * 60, [1] * 60, ks=P.KS, bootstrap=100, seed=1)
    assert not math.isnan(row2["mde_paired"])


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
    # both halves are canonical lowercase hex — `int(part, 16)` alone would
    # also accept `0x…`/uppercase, and a bare expression asserts nothing
    assert all(c in "0123456789abcdef" for c in head + tail)


def test_displacement_counts_ids_the_merged_pass_moved_out():
    off = {"a": {"ids": ["x", "y", "z"]}, "b": {"ids": []}}
    on = {"a": {"ids": ["z", "x", "w"]}, "b": {"ids": ["q"]}}
    out = P._displacement(off, on, ["a", "b"], k=3)
    assert out["items_with_off_topk"] == 1
    assert out["displaced_ids"] == 1          # y fell out of the window
    assert out["abs_rank_shift_of_kept"] == 3  # x 0->1 (1), z 2->0 (2)


def test_hits_at_counts_only_ranks_inside_the_window():
    """⛔ `rank < k` is the ONLY place a recorded rank becomes a hit (`#13267` F2).

    Every `off_hits`/`on_hits` pair, hence every `b`/`c`, every exact McNemar tail
    and every MDE in every committed artifact, is a function of this one
    comparison — and before this test nothing exercised it: the mutant
    `rank < k` → `rank <= k` stayed green across the whole suite (every hermetic
    fixture set `"rank": None`, so the conversion only ever ran on the skipped
    live arm). Ranks are 0-based, so a window of `k` holds ranks `0..k-1`: `k-1`
    is IN, `k` is OUT, and `None` (an item the arm never returned) is OUT.
    """
    per_item = {"r0": {"rank": 0}, "r4": {"rank": 4}, "r5": {"rank": 5},
                "r6": {"rank": 6}, "absent": {"rank": None}}
    order = ["r0", "r4", "r5", "r6", "absent"]
    assert P._hits_at(per_item, order, k=5) == [1, 1, 0, 0, 0]
    # the boundary, spelled out k-1 / k / k+1
    assert P._hits_at(per_item, ["r4"], k=5) == [1]
    assert P._hits_at(per_item, ["r5"], k=5) == [0]
    assert P._hits_at(per_item, ["r6"], k=5) == [0]
    # a k of 1 keeps the top slot only, so a rank of 1 is already outside it
    assert P._hits_at(per_item, ["r0", "r4"], k=1) == [1, 0]


# ── 2b. "did the lever fire" — a comparison, not a constant ─────────────────

def test_read_delta_compares_the_recorded_ranked_lists():
    """The firing signal must move with the READS, on every arm."""
    off = {"a": {"ids": ["x", "y"]}, "b": {"ids": ["p"]}, "c": {"ids": []}}
    on = {"a": {"ids": ["x", "z"]}, "b": {"ids": ["p"]}, "c": {"ids": ["q"]}}
    out = P._read_delta(off, on, ["a", "b", "c"])
    assert out["fired"] == ["a", "c"]
    assert out["identical"] == ["b"]
    assert out["items_whose_read_grew"] == 1     # c: 0 ids -> 1 id
    assert out["compared"] == 3
    # the same reads for the same answers => nothing fired, and that IS measured
    assert P._read_delta(off, off, ["a", "b", "c"])["fired"] == []


def test_read_arm_records_the_ranked_list_for_every_arm():
    """⛔ The other half of F1: `read_arm` recorded ids for the POINT arm only.

    Driven through a stub SDK, so the mutation "put `ids if arm == "point" else
    None` back" reds here rather than only on a box with a database.
    """
    class _FakeSDK:
        def tortoise_fts_query(self, query, *, entity_type, limit,
                               include_terminal, _record_reads,
                               _valid_time_rerank, leg_trace,
                               entity_key_expansion):
            ids = (["on-1", "off-1"] if entity_key_expansion
                   else ["off-1", "off-2"])
            return [{"id": i} for i in ids]

    items = [{"item_id": "item-000000", "name": "the plan doc",
              "query": "what did the plan doc say",
              "target_point_id": "t1", "query_point_id": "q1",
              "object_id": "o1"}]
    off = P.read_arm(_FakeSDK(), items, arm="object", expansion=False,
                     read_limit=5)["per_item"]["item-000000"]
    on = P.read_arm(_FakeSDK(), items, arm="object", expansion=True,
                    read_limit=5)["per_item"]["item-000000"]
    assert off["ids"] == ["off-1", "off-2"], (
        "an arm with no recorded ranked list makes the ON==OFF claim `None == None`")
    assert on["ids"] == ["on-1", "off-1"]
    delta = P._read_delta({"item-000000": off}, {"item-000000": on},
                          ["item-000000"])
    assert delta["fired"] == ["item-000000"]
    assert P._ids_digest(off["ids"]) != P._ids_digest(on["ids"])


def test_read_delta_refuses_an_arm_with_no_recorded_ranked_list():
    """⛔ THE PRE-FIX SHAPE (`#13267` F1), reproduced as a refusal.

    `read_arm` recorded `ids` for the point arm only, so on the object arm both
    sides were `None`; scored as `(ids or [])` that is `[] == []` for every item,
    i.e. "the identical ranked list for all N items" from a constant that no
    retrieval behaviour can move. A side with no ranked list must be refused, not
    scored — this test reds if anything reintroduces the `or []` collapse.
    """
    unrecorded = {"a": {"ids": None}, "b": {"ids": None}}
    with pytest.raises(P.Refused, match="UNMEASURED"):
        P._read_delta(unrecorded, unrecorded, ["a", "b"])
    # ONE missing side is enough, and a RECORDED empty read is not missing
    with pytest.raises(P.Refused, match="UNMEASURED"):
        P._read_delta({"a": {"ids": []}, "b": {"ids": None}},
                      {"a": {"ids": ["x"]}, "b": {"ids": ["x"]}},
                      ["a", "b"])


def test_a_recorded_empty_read_is_not_an_unrecorded_one():
    assert P._ids_digest(None) is None
    assert P._ids_digest([]) == P._sha256_bytes(b"")
    assert P._ids_digest(["a", "b"]) != P._ids_digest(["b", "a"])


def test_the_item_id_digest_is_a_function_of_the_sample_size_only():
    """`id_digest` was published as the "same items in both arms" evidence.

    It is sha256 over the SYNTHETIC ids every draw assigns, so it is identical
    for ANY sample of the same size — two draws sharing no name agree on it. The
    discriminating datum one line below is `name_digest`.
    """
    ids_300 = [f"item-{i:06d}" for i in range(300)]
    assert P._sha256_bytes("\n".join(ids_300).encode()).startswith("4d6899d3d0b62543")
    ids_60 = [f"item-{i:06d}" for i in range(60)]
    assert P._sha256_bytes("\n".join(ids_60).encode()).startswith("9eeb9c4f")
    # same size, disjoint names: the id digest CANNOT tell them apart, the name
    # digest must
    a = P._sha256_bytes("\n".join(f"name {i}" for i in range(300)).encode())
    b = P._sha256_bytes("\n".join(f"other {i}" for i in range(300)).encode())
    assert a != b


# ── 2c. the refusals, and the flag that must not disable two of them ────────

def test_allow_mixed_modes_excuses_only_window_mode_refusals(capsys):
    """⛔ `--allow-mixed-modes` gated EVERY refusal class (`#13267` F7).

    A flag named for window-mode contamination also un-refused a structurally
    blind run and an inert harvest — the two properties the tool exists to refuse.
    """
    mixed = (P.MODE_REFUSAL, "arm point/off has MIXED window modes {}")
    blind = (P.STRUCTURAL_REFUSAL, "EVERY arm is structurally blind to the lever")
    inert = (P.INERT_REFUSAL, "the point arm reached the mechanism but ...")
    assert P._exit_code([], allow_mixed_modes=False) == 0
    assert P._exit_code([mixed], allow_mixed_modes=False) == 3
    assert P._exit_code([mixed], allow_mixed_modes=True) == 0
    assert P._exit_code([blind], allow_mixed_modes=True) == 3
    assert P._exit_code([inert], allow_mixed_modes=True) == 3
    assert P._exit_code([mixed, blind], allow_mixed_modes=True) == 3
    assert "REFUSED to publish a verdict" in capsys.readouterr().err


def test_freeze_and_snapshot_cannot_select_two_corpora_at_once(tmp_path, capsys):
    """⛔ `--freeze` + `--snapshot` composed into a digest for bytes never read.

    The two options are independent in `main()`, and with both passed the manifest
    — hence `snapshot.digest`, every per-file sha256 and
    `population.snapshot_digest` — came from the `--freeze` target while
    `read_records` read the `--snapshot` directory, on which `verify_snapshot`
    never ran. The artifact then attested *"this corpus, byte for byte"* over a
    corpus the run did not measure — the exact fail-open the tool's docstring
    promises to refuse (`#13267` F2). `argparse`'s mutually exclusive group
    refuses the composition BEFORE any copy happens — and `run()` refuses it too,
    for a caller that built the Namespace itself, which is the one path argparse
    never sees.
    """
    src = tmp_path / "live"
    _write_corpus(src, names=["the road bike repairs"])
    with pytest.raises(SystemExit) as exc:
        P.main(["--sessions-dir", str(src),
                "--freeze", str(tmp_path / "snap"),
                "--snapshot", str(tmp_path / "elsewhere")])
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    # it refused before touching either path — a copy here would have "frozen" a
    # corpus the run then did not read
    assert not (tmp_path / "snap").exists()
    assert not (tmp_path / "elsewhere").exists()
    # ⛔ and the INVARIANT holds at the layer that reads the corpus, not only at
    # the parser: a directly-built Namespace must fail closed
    with pytest.raises(P.Refused, match=r"--freeze and --snapshot both select"):
        P.run(argparse.Namespace(freeze=str(tmp_path / "snap"),
                                 snapshot=str(tmp_path / "elsewhere")))


def test_the_dense_switch_refusal_fires_before_any_database_is_needed(
        tmp_path, monkeypatch, capsys):
    """`TORTOISE_ENTITY_KEY_DENSE` is a SECOND lever — the tool refuses it.

    A refusal with no test is a refusal anyone can delete; this one also pins the
    ORDER (it fires before the DB probe, so it is a hermetic case).
    """
    monkeypatch.setenv("TORTOISE_ENTITY_KEY_DENSE", "1")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    src = tmp_path / "live"
    _write_corpus(src, names=["the road bike repairs"])
    code = P.main(["--sessions-dir", str(src), "--freeze", str(tmp_path / "snap"),
                   "--population", "10", "--sample", "5", "--min-items", "1",
                   "--out", str(tmp_path / "artifact.json")])
    assert code == 3
    assert "DENSE" in capsys.readouterr().err
    assert not (tmp_path / "artifact.json").exists()


def _fake_args(**over):
    import types
    base = dict(arms=["point", "object"], read_limit=20, query_chars=200,
                key_tokens=4, min_query_tokens=3, min_items=2, population=40,
                sample=4, seed=7, repeat_arm="point", bootstrap=100,
                distractors=4, include_per_item=True, graph=None, out=None,
                allow_mixed_modes=False, namespace="paired2518")
    base.update(over)
    return types.SimpleNamespace(**base)


def _fake_arm(ids_by_item, *, seed_ranks=None, mode="keyword_only"):
    ids_by_item = dict(ids_by_item)
    seed_ranks = seed_ranks or {}
    per_item = {iid: {"rank": None, "seed_rank": seed_ranks.get(iid),
                      "ids": ids, "provenance_entries": {}}
                for iid, ids in ids_by_item.items()}
    return {"per_item": per_item, "window_modes": {mode: len(per_item)},
            "provenance_counts": {}, "reads": len(per_item)}


def _build_fake_artifact(*, point_off, point_on, object_off, object_on,
                         args=None, repeat_ids=None, arms=None):
    item_ids = sorted(point_off)
    names = [f"the name {i}" for i in range(len(item_ids))]
    items = [{"item_id": iid, "name": name, "target_overlap_with_query": 0}
             for iid, name in zip(item_ids, names, strict=True)]
    manifest = {"schema": "s", "digest": "0" * 64, "file_count": 1,
                "total_bytes": 1, "total_lines": 1, "files": []}
    pop_body = {"schema": P.POPULATION_SCHEMA, "snapshot_digest": "0" * 64,
                "population": 40, "sample": len(item_ids), "seed": 7,
                "names_sha256": P._sha256_bytes("\n".join(names).encode())}
    if arms is None:
        arms = {
            "point": {"off": _fake_arm(point_off, seed_ranks={i: 0 for i in item_ids}),
                      "on": _fake_arm(point_on, seed_ranks={i: 0 for i in item_ids})},
            "object": {"off": _fake_arm(object_off), "on": _fake_arm(object_on)},
        }
        if repeat_ids is not None:
            # the repeat arm, so the determinism check has something to compare
            arms["point"]["repeat"] = _fake_arm(
                repeat_ids, seed_ranks={i: 0 for i in item_ids})
    art = P._build_artifact(
        args or _fake_args(), manifest, pop_body, items, {}, {"anchors": 1},
        arms, item_ids, load_start={"load_1m": 1.0, "load_5m": 1.0,
                                    "load_15m": 1.0, "cpus": 1},
        load_end={"load_1m": 1.0, "load_5m": 1.0, "load_15m": 1.0, "cpus": 1},
        graph_nodes=1, elapsed=1.0, pop_file=Path("/tmp/population.json"))
    return art, item_ids


@pytest.fixture()
def _no_embedder_probe(monkeypatch):
    """Every structural pin here asserts structure over a SYNTHETIC artifact, and
    `_build_artifact` calls `_has_embedder()` → `EmbeddingModel.get()`, which loads
    a model on first call (~2 min on a box that has one) — a cost that belongs to a
    real RUN, not to a structural pin.

    ⛔ No count of its consumers is quoted (`#13267` review 12 F2): it began as two
    and is nine now, and a count in a docstring re-stales silently."""
    monkeypatch.setattr(P, "_has_embedder", lambda: False)


def test_the_artifact_records_the_treatment_knobs_the_pairing_and_the_tool_bytes(
        _no_embedder_probe):
    """The three evidence fields the review found stating more than they held.

    * `construction` recorded `read_limit` but NOT `--query-chars`, so the arm the
      robustness claim leans on was not reproducible from its own artifact (F5);
    * `items.id_digest` was cited as pairing evidence although it is a function of
      the sample SIZE, and `ids_match_all_arms` was a tautology (F6);
    * `git_head` was the repo commit, not the code (F4).
    """
    point_ids = {"item-000000": ["a"], "item-000001": ["b"]}
    point_on = {"item-000000": ["a", "c"], "item-000001": ["b"]}
    art, item_ids = _build_fake_artifact(
        point_off=point_ids, point_on=point_on,
        object_off={"item-000000": ["a"], "item-000001": ["b"]},
        object_on={"item-000000": ["a"], "item-000001": ["b"]})
    c = art["construction"]
    assert c["query_chars"] == 200 and c["read_limit"] == 20
    assert c["key_tokens"] == 4 and c["min_query_tokens"] == 3
    assert art["items"]["id_digest"] != art["items"]["name_digest"]
    assert art["items"]["name_digest"] == art["population"]["names_sha256"]
    assert "NOT pairing evidence" in art["items"]["id_digest_note"]
    assert "ids_match_all_arms" not in art["items"]
    assert art["pairing"]["items_read_per_arm"] == {"point": len(item_ids),
                                                    "object": len(item_ids)}
    digest = P._sha256_bytes(Path(P.__file__).resolve().read_bytes())
    assert art["provenance"]["tool_sha256"] == digest
    assert art["provenance"]["tool_path"].endswith("paired_expansion_ab_2518.py")
    assert art["provenance"]["git_head"] == P._git_head()
    # the per-item identity claim is checkable FROM the artifact
    per_item = art["arms"]["object"]["per_item"]
    assert all(row["off_ids_digest"] and row["on_ids_digest"]
               for row in per_item.values())
    assert per_item["item-000000"]["off_ids_digest"] == \
        per_item["item-000000"]["on_ids_digest"]


def test_the_object_arms_blindness_is_a_measured_identity_and_the_point_arms_null_is_refused(
        _no_embedder_probe):
    """The PR's headline refusal, pinned: an INERT point arm stays exit 3.

    `_build_artifact` derived `expansion_identical_items` from `(ids or [])`. With
    both arms now recording their ranked lists, the object arm's identity is a
    real comparison, so mutating EITHER side of it moves the count.
    """
    same = {"item-000000": ["a"], "item-000001": ["b"]}
    art, _ = _build_fake_artifact(point_off=same, point_on=same,
                                  object_off=same, object_on=same)
    assert art["arms"]["object"]["structurally_blind"] is True
    assert art["arms"]["object"]["expansion_identical_items"] == 2
    assert art["arms"]["object"]["ranked_lists_compared"] == 2
    assert "element-wise" in art["arms"]["object"]["why"]
    # the point arm fired on nothing => the INERT refusal, which the mode flag
    # must NOT excuse
    assert art["refusal_kinds"] == [P.INERT_REFUSAL]
    assert len(art["refusals"]) == len(art["refusal_kinds"])
    assert P._exit_code(list(zip(art["refusal_kinds"], art["refusals"],
                                 strict=True)), allow_mixed_modes=True) == 3
    # and the same builder, once the point arm's read actually differs, is clean
    fired, _ = _build_fake_artifact(
        point_off=same, point_on={"item-000000": ["a", "z"],
                                  "item-000001": ["b"]},
        object_off=same, object_on=same)
    assert fired["arms"]["point"]["expansion_fired_items"] == 1
    assert fired["arms"]["point"]["items_whose_read_grew"] == 1
    assert P.MODE_REFUSAL not in fired["refusal_kinds"]
    assert P.INERT_REFUSAL not in fired["refusal_kinds"]


def test_the_object_arm_stops_being_blind_once_its_two_reads_differ(
        _no_embedder_probe):
    """⛔ `structurally_blind` must be MEASURED, not read off the arm's name.

    `structurally_blind = (arm != "point" and len(fired) == 0)` →
    `(arm != "point")` stayed green across the whole suite (`#13267` F3), because
    every `_build_fake_artifact` call passed `object_off is object_on` (and the
    committed control only exercises the identical case). Under that mutant an
    object arm whose two switch states differ on EVERY item still publishes "the
    two switch states produced the SAME ranked list for all N items" — the exact
    claim the code comment above that line warns is "a claim about the arm's
    NAME". A measured identity must stop being reported when the reads differ.
    """
    same = {"item-000000": ["a"], "item-000001": ["b"]}
    diverged, _ = _build_fake_artifact(
        point_off=same, point_on=same, object_off=same,
        object_on={"item-000000": ["a", "z"], "item-000001": ["b"]})
    obj = diverged["arms"]["object"]
    assert obj["structurally_blind"] is False, (
        "the two reads differ on one item, so the identity is not structural")
    assert obj["expansion_fired_items"] == 1
    assert obj["expansion_identical_items"] == 1
    assert obj["ranked_lists_compared"] == 2
    assert obj["why"] == ""
    assert P.STRUCTURAL_REFUSAL not in diverged["refusal_kinds"]
    # …and the blind claim still holds where it IS an identity
    blind, _ = _build_fake_artifact(point_off=same, point_on=same,
                                   object_off=same, object_on=same)
    assert blind["arms"]["object"]["structurally_blind"] is True


# ── 2d. the brief against its own artifacts (the documented figures) ─────────

_DOCS = (Path(__file__).resolve().parent.parent
         / "docs" / "research" / "2026-11-17-2518-paired-ab")
_ARTIFACTS = ("paired-point-300.json", "paired-point-300-short-query.json",
              "paired-object-60-control.json")


def _artifact(name: str) -> dict:
    return json.loads((_DOCS / name).read_text())


_KNOBS = ("query_chars", "key_tokens", "min_query_tokens")


def _assert_the_artifact_pins_hold(art: dict) -> None:
    """The artifact-shape invariants this brief's figures lean on.

    ⛔ Written to hold for the three COMMITTED artifacts *and* for an artifact the
    committed tool produces. The pins used to spell the legacy shape out as a
    REQUIREMENT — `assert "query_chars" not in construction`,
    `art["construction_unrecorded"]["fields"]`, `obj["identity_measured"] is
    False` — so a correct re-generation failed all three: the guard punished the
    fix it documents (`#13267` F5). Each invariant below is the FACT those three
    were proxying, and it is checkable on either shape.
    """
    construction = art["construction"]
    unrecorded = (art.get("construction_unrecorded") or {}).get("fields", [])
    for knob in _KNOBS:
        # a treatment knob is EITHER recorded as a construction parameter OR
        # named as unrecorded — never both (which claims the artifact records it
        # and does not), never neither (a knob the run used and nobody can see)
        assert (knob in construction) != (knob in unrecorded), (
            f"{knob!r}: in construction={knob in construction}, "
            f"in construction_unrecorded={knob in unrecorded}")
    for arm, block in art["arms"].items():
        measured = block["ranked_lists_compared"] > 0
        # ⛔ `#13267` review 4 F2: the determinism check must say WHAT it
        # compared. `repeat_identical` was computed from the target's RANK, which
        # is null on both sides for every point-arm item, so the field published
        # `None == None` as "identical for all 300 items". The basis is now part
        # of the record, and a legacy target-rank basis can never stand in for a
        # determinism result.
        check = block.get("repeat_check")
        assert check is not None, (
            f"arm {arm!r} records no `repeat_check` — a bare `repeat_identical` "
            "does not say what was compared")
        assert check["basis"] in (P.REPEAT_BASIS_DIGEST, P.REPEAT_BASIS_LEGACY)
        if check["basis"] == P.REPEAT_BASIS_LEGACY:
            assert block["repeat_identical"] is None, (
                f"arm {arm!r} publishes `repeat_identical` off a TARGET-RANK "
                "comparison — that is not a determinism check of the read")
            assert "not a determinism check" in (check["note"] or ""), (
                f"arm {arm!r}: a legacy basis must say in the record that it is "
                "not a determinism check")
        else:
            assert (block["repeat_identical"] is None) is (check["compared"] == 0), (
                f"arm {arm!r}: repeat_identical={block['repeat_identical']!r} "
                f"with {check['compared']} ranked list(s) compared")
            assert check["identical"] == block["repeat_identical"]
            if check["compared"] == 0:
                assert "UNMEASURED" in (check["note"] or ""), (
                    f"arm {arm!r} reports an unmeasured determinism with no note")
        # ⛔ `#13267` F1: the IDENTITY is a MEASUREMENT exactly when ranked lists
        # were compared — a number beside zero compared items is `None == None`
        # dressed up as a measurement, and `null` beside a non-zero count is a
        # measurement thrown away
        assert (block["expansion_identical_items"] is None) is not measured, (
            f"arm {arm!r}: ranked_lists_compared="
            f"{block['ranked_lists_compared']} but "
            f"expansion_identical_items={block['expansion_identical_items']!r}")
        if "identity_measured" in block:
            assert block["identity_measured"] is measured, (
                f"arm {arm!r} says identity_measured="
                f"{block['identity_measured']} with "
                f"{block['ranked_lists_compared']} ranked list(s) compared")
        if not measured:
            assert "UNMEASURED" in (block.get("identity_note") or ""), (
                f"arm {arm!r} reports an unmeasured identity with no note saying so")


def _assert_the_evidence_shape_the_brief_leans_on(art: dict) -> None:
    """The F5 claim on EITHER artifact shape — a tool-produced one included.

    ⛔ `#13267` F2: this block indexed `art["construction_unrecorded"]["fields"]`
    unconditionally, three lines under a comment saying regeneration cannot red
    the suite — and the committed tool emits NO such key (it records
    `construction.query_chars` instead), so a correct re-generation raised
    `KeyError` at the one line that stayed legacy-shaped (measured). The three
    committed files are hand-annotated and DO carry the block, which is why the
    defect was invisible here.

    So the legacy-only claim is gated on the shape that carries it, and the FACT
    both shapes must satisfy — the knob the brief publishes as prose
    (`--query-chars 200`, above) is visible in exactly one of the two places —
    lives in `_assert_the_artifact_pins_hold`, which is called for both.
    """
    _assert_the_artifact_pins_hold(art)
    construction = art["construction"]
    unrecorded = (art.get("construction_unrecorded") or {}).get("fields", [])
    assert ("query_chars" in construction) != ("query_chars" in unrecorded), (
        "`query_chars` is the robustness arm's treatment knob, so it is visible "
        f"in exactly ONE of the two places: construction="
        f"{'query_chars' in construction}, "
        f"construction_unrecorded={'query_chars' in unrecorded}")
    if unrecorded:
        # the committed runs predate the tool recording the knobs; where an
        # artifact says a knob was NOT recorded, the treatment knob must be one
        # of them — otherwise the brief's prose carries a value the artifact
        # claims to hold, and a reader cannot tell which is evidence
        assert "query_chars" in unrecorded

    # ⛔ `#13267` review 8: the NOTE's content is a truth-claim about another file,
    # and nothing read it. Restoring the pre-round-7 false pointer ("The values it
    # used are prose in §3 and §8 … key-tokens 4, min-query-tokens 3") left the
    # whole suite GREEN, while the brief carries no such values anywhere.
    # Only when the block EXISTS: a tool-produced artifact records the knobs and
    # carries no `construction_unrecorded` at all.
    cu = art.get("construction_unrecorded")
    if cu:
        note = cu.get("note", "")
        assert note, (
            "an unrecorded block must say what it means — the tool does not emit "
            "this block, so its presence implies a hand-written note")
        assert "argparse DEFAULT" in note, (
            "the note must name `key_tokens`/`min_query_tokens` as the tool's "
            "argparse DEFAULTS — they are not recorded run values, so they must "
            "not be presented as what the run used")
        for bogus in ("key-tokens 4,", "min-query-tokens 3)"):
            assert bogus not in note, (
                f"the note still presents {bogus!r} as a value the run used, but "
                "nothing in these artifacts records it")
        # every flag/value the note attributes to the brief must literally resolve there
        brief_txt = (_DOCS / "research-brief.md").read_text()
        quoted = set(re.findall(r"--[a-z-]+ \d+", note))
        assert quoted, "the note cites the flags each arm ran with"
        for flag in quoted:
            assert flag in brief_txt, (
                f"the note points at the brief for {flag!r}, which the brief lacks "
                "— a pointer that does not resolve is the defect this pin closes")
        assert "(500)" in note and "(500)" in brief_txt, (
            "the note cites the headline's query cap and the brief prints it")


def test_the_brief_quotes_the_digests_and_the_loads_its_artifacts_hold():
    """The four evidence defects of `#13267` F1/F3/F5/F6, pinned as a doc drift.

    A brief is prose and its artifacts are the evidence; when they disagree the
    brief has to move. Each assertion below is one of the review's findings:

    * F3 — the quoted load trace was in NO artifact (`10.36 → 10.27` was the
      object control's `9.44 → 9.36`, transposed);
    * F6 — `ids_match_all_arms` was a tautology and `id_digest` is a function of
      the sample SIZE, so it cannot be the pairing evidence;
    * F1 — the object control's "60 of 60 identical" was `None == None`, and the
      artifact now says so instead of publishing the number;
    * F5 — the robustness arm's treatment knob (`--query-chars`) was unrecorded,
      so the arm could not be reproduced from its own artifact.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    head = _artifact("paired-point-300.json")
    short = _artifact("paired-point-300-short-query.json")
    ctrl = _artifact("paired-object-60-control.json")

    # F3: the load trace must be the artifact's own
    assert f"{head['environment']['load_start']['load_1m']:.2f}" in brief
    assert f"{head['environment']['load_end']['load_1m']:.2f}" in brief
    for transposed in ("10.36", "10.27"):
        assert transposed not in brief, (
            f"{transposed} is in no committed artifact — a falsifier must quote "
            "the artifact it points at")

    # ⛔ F3, third generation: the "no retrieval behaviour changed" cell quoted a
    # `git diff --stat` TALLY, and a tally is a MOVING number — it grows with
    # every commit the evidence itself adds and every merge of `main`, so the
    # quoted value was already stale at the head that published it (measured: the
    # row said 8,454 while `git diff --stat origin/main...HEAD` printed 8,509; the
    # round before it quoted a 3-file list the tree does not produce either).
    # What the claim is about is the SET OF TOUCHED PATHS, and that set is stable,
    # so the cell names the set and the command that reproduces it. A quoted tally
    # is not a falsifier: nobody can reproduce it, and the re-stale class this pin
    # exists for would come back a fourth time.
    for tally in ("insertions(+)", "deletions(-)", "files changed"):
        assert tally not in brief, (
            f"the falsifier quotes a `git diff --stat` {tally!r} tally — that "
            "number moves with every commit the evidence adds and every merge of "
            "main, so the cell can never be reproduced; quote the touched-path "
            "set, which is what the claim is about")
    assert "git diff --name-only origin/main...HEAD" in brief, (
        "the cell must name the command whose OUTPUT it publishes — the whole "
        "#13267 F3 class is prose that quotes a command the tree does not print")

    # F6: the pairing datum is the NAME digest
    assert head["items"]["name_digest"] == head["population"]["names_sha256"]
    assert short["items"]["name_digest"] == head["items"]["name_digest"]
    assert head["items"]["name_digest"][:16] in brief
    assert head["items"]["id_digest"] != head["items"]["name_digest"]
    assert "NOT pairing evidence" in head["items"]["id_digest_note"]
    for art in (head, short, ctrl):
        assert "ids_match_all_arms" not in art["items"], (
            "that predicate was True for every reachable input")
        # the brief may name the retired predicate while recording the finding;
        # what it must never do again is cite it as evidence (`: true`)
        assert "ids_match_all_arms: true" not in brief
        assert art["pairing"]["items_read_per_arm"]

    # F1/F5: the object arm's identity is a CALL-SITE fact in this generation,
    # and the retired count cannot return in ANY of its spellings — the two pins
    # that existed matched two phrasings only, and the brief kept a third
    obj = ctrl["arms"]["object"]
    assert obj["identity_measured"] is False
    assert obj["expansion_identical_items"] is None
    assert obj["ranked_lists_compared"] == 0
    assert "UNMEASURED" in obj["identity_note"]
    for retired in ("60 of 60 items, element-wise",
                    "60 of 60 items returned the identical ranked list",
                    "60/60 items identical",
                    "60/60"):
        assert retired not in brief, (
            f"{retired!r} is the retired `None == None` count — a reader cannot "
            "tell a measurement from a constant, so it is not evidence")

    # F5: the robustness arm is reproducible from the brief, and the artifacts
    # say which knobs they did NOT record. The shape invariants are asserted by a
    # helper that a TOOL-PRODUCED artifact also satisfies, so regenerating the
    # evidence cannot red this suite
    assert "--query-chars 200" in brief
    assert short["construction"]["read_limit"] != head["construction"]["read_limit"]
    for art in (head, short, ctrl):
        _assert_the_evidence_shape_the_brief_leans_on(art)

    # ⛔ the FOURTH evidence file (review at `24737b02`): §5's first falsifier NAMES
    # `corpus-snapshot-manifest.json`, §3/§5 quote its digest/files/lines/bytes — and
    # no test read the file at all. Three separate substitutions measured GREEN: a
    # re-staled §3 figure, a re-staled 64-hex §5 digest, and a corrupted manifest
    # digest. The manifest is re-DERIVED here, then required to agree with a
    # committed artifact and with the prose that quotes it.
    man = _artifact("corpus-snapshot-manifest.json")
    assert P.snapshot_digest(man) == man["digest"], (
        "the manifest's aggregate digest does not re-derive from its own rows")
    for art in (head, short, ctrl):
        assert art["snapshot"]["digest"] == man["digest"], (
            "an artifact reads a snapshot the committed manifest does not describe")
        assert art["snapshot"]["files"] == man["files"], (
            "an artifact's snapshot file list differs from the manifest's")
    for quoted in (f"{man['digest'][:16]}\u2026", man["digest"],
                   f"{man['file_count']} files",
                   f"{man['total_lines']:,} lines",
                   f"{man['total_bytes']:,} bytes"):
        assert quoted in brief, (
            f"the brief must quote its own snapshot's {quoted!r}")

    # F4: `git_head` never stands alone as the code identity again
    for art in (head, short, ctrl):
        assert art["provenance"]["git_head"] == art["git_head"]
        assert "tool_sha256" in art["provenance"]
        assert art["provenance"]["git_head_note"]
        # F7: the published refusals carry their CLASS, so a reader can tell which
        # ones `--allow-mixed-modes` would excuse
        assert len(art["refusal_kinds"]) == len(art["refusals"])
    assert ctrl["refusal_kinds"] == [P.STRUCTURAL_REFUSAL]
    assert head["refusal_kinds"] == [] and head["refusals"] == []


def test_the_docs_index_row_repeats_only_what_the_evidence_carries():
    """The same four corrections, at the other place the figures are published.

    `docs/00_index.md` carries a full row for this brief, and it repeated both
    retired claims verbatim (`60 of 60 items returned the identical ranked list`,
    `id_digest … , ids_match_all_arms: true`) — the propagation the review warns
    about: the record other lanes read carries the error to artifacts that were
    correct. A doc-only edit of either file selects `tier1` and nothing else, so
    these pins are why the row is registered there.
    """
    index = (Path(__file__).resolve().parent.parent / "docs" / "00_index.md").read_text()
    head = _artifact("paired-point-300.json")
    rows = [ln for ln in index.splitlines() if "2026-11-17-2518-paired-ab" in ln]
    assert rows, "the brief's index row is where a reader first meets these figures"
    row = rows[0]
    assert "60 of 60 items returned the identical ranked list" not in row
    assert "60/60" not in row
    assert "ids_match_all_arms: true" not in row
    assert "id_digest 4d6899d3" not in row
    assert head["items"]["name_digest"][:8] in row
    for transposed in ("10.36", "10.27"):
        assert transposed not in row

    # ⛔ `#13267` review 4 F4: every assertion above is a NEGATIVE (a retired
    # claim that must not return), and `head["items"]["name_digest"][:8] in row`
    # was the only positive one. So the row could publish a lift the artifact does
    # not carry, a churn count it does not carry, a corpus digest matching no
    # artifact and a line count it does not carry — measured GREEN, each on its
    # own, whole suite passing. The row's figures are asserted against the
    # artifact the row cites, in the positions a reader reads them.
    n = head["population"]["sample"]
    per_k = head["arms"]["point"]["per_k"]
    k15, k10, k3 = per_k["k=15"], per_k["k=10"], per_k["k=3"]
    row_checks = [
        (f"{k15['on_hits']}/{n}", "the k=15 recovered-target count"),
        (f"{k10['on_hits']}/{n}", "the k=10 count"),
        (f"{k3['mde_paired'] * 100:.2f} pp", "the k=3 MDE"),
        (f"{k3['off_topk_displacement']['displaced_ids']} of "
         f"{k3['off_topk_displacement']['items_with_off_topk'] * 3}",
         "the k=3 OFF top-k displacement"),
        (head["snapshot"]["digest"][:16], "the frozen corpus digest"),
        (f"{head['snapshot']['total_lines']:,} lines", "the corpus line count"),
        (f"{head['snapshot']['file_count']} files", "the corpus file count"),
    ]
    delta15 = (k15["on_hits"] - k15["off_hits"]) / n * 100
    delta3 = (k3["on_hits"] - k3["off_hits"]) / n * 100
    row_checks.append((f"+{delta15:.2f} pp", "the k=15 paired delta (pp)"))
    # ⛔ `#13267` review 6 F3: `OFF = 0/300` — the number R1's class-conditional
    # disclosure is ABOUT — and the k=3 delta were both unpinned. Measured GREEN:
    # `OFF = 0/300 -> OFF = 7/300` left the row asserting "class-conditional
    # recall" beside a false count, and `+1.67 pp -> +9.99 pp` printed a k=3 delta
    # six times the artifact's in a sentence claiming it is BELOW the MDE.
    row_checks += [
        (f"OFF = {k3['off_hits']}/{n}", "the OFF count the disclosure is about"),
        # ⛔ the k=3 delta must be pinned IN THE SENTENCE, not anywhere in the row:
        # `1.67 pp` also appears in the short-query harm cell, so a bare
        # membership check let `at k=3 the +9.99 pp is BELOW` stay green.
        (f"at k=3 the +{delta3:.2f} pp is BELOW the "
         f"{k3['mde_paired'] * 100:.2f} pp MDE",
         "the k=3 delta in the sentence that calls it BELOW its MDE"),
    ]
    for shown, what in row_checks:
        assert shown in row, (
            f"the index row does not carry {what} as {shown!r}: {row[:400]}")
    # and the class-conditional caveat travels with the lift
    assert "class-conditional" in row
    assert f"{delta3:.2f} pp" in row and "BELOW" in row, (
        "the row's 'the k=3 delta is BELOW the MDE' claim must carry the "
        "artifact's k=3 delta")

    # ⛔ Measured survivors at `3ae4980b1d` (six substitutions, each green on the
    # whole suite): `p = 2 × 10⁻⁵ → 0.5`, `CI [+3.33, +8.33] → [+3.33, +9.99]`,
    # `5 of 300 items whose seed moved → 299 of 300`, and `3 out of the k-window
    # → 0`. The checks above cover the counts and the deltas; the exact McNemar
    # TAIL, the bootstrap CI and the harm cell are printed in shapes `row_checks`
    # does not parse, so they re-staled green. The tail is superscript
    # scientific notation, which `_printed` cannot read — `_sci_printed` reads it
    # at the same one-unit-in-the-last-printed-place tolerance.
    short = _artifact("paired-point-300-short-query.json")
    boot = k15["bootstrap"]
    assert (f"CI [+{boot['ci95'][0] * 100:.2f}, +{boot['ci95'][1] * 100:.2f}]"
            in row), (
        "the row's CI is not the artifact's own seeded bootstrap interval")
    printed = _sci_printed(row)
    assert len(printed) == 2, (
        "the row must print both exact McNemar tails in scientific notation; "
        f"it prints {len(printed)}")
    for (shown, tol), value, where in zip(
            printed, (k15["mcnemar_exact_p"], k10["mcnemar_exact_p"]),
            ("k=15", "k=10"), strict=True):
        assert abs(shown - value) <= tol + 1e-12, (
            f"the index row prints {shown:g} for {where} but its artifact "
            f"holds {value!r}")
    seed = short["arms"]["point"]["per_k"]["k=15"]["seed_effect"]
    moved = seed["seed_lost_from_window"] + seed["seed_demoted_but_kept"]
    assert (f"{moved} of {short['population']['sample']} items whose seed moved "
            f"({seed['seed_lost_from_window']} out of the k-window, "
            f"{seed['seed_demoted_but_kept']} demoted but kept)" in row), (
        "the harm side is not the short-query artifact's own seed cells — all "
        "three numbers, not just their sum")

    # ⛔ the k=3 delta, its MDE sentence, the sample size and the window mode: all
    # presence-only (or unread) at `d5be0e6840` — measured GREEN, each on its own:
    # `+1.67 pp → +9.99 pp` at EITHER of the row's two sites, `n = 300 → n = 900`,
    # `keyword_only 300/300 → 299/300`. Derived from the artifact, per site.
    delta3 = (k3["on_hits"] - k3["off_hits"]) / n * 100
    assert (f"at k=3 the +{delta3:.2f} pp is BELOW the "
            f"{k3['mde_paired'] * 100:.2f} pp MDE" in row), (
        "the row's k=3 winner sentence is not the artifact's delta and MDE")
    assert f"the gain (+{delta3:.2f} pp, ns)" in row, (
        "the row's short-arm gain is not the artifact's own k=3 delta")
    assert f"exact McNemar at n = {n}" in row, (
        "the row's sample size is not the artifact's drawn sample")
    for arm, block in head["arms"]["point"]["window_modes"].items():
        assert block["keyword_only"] == n, (
            f"the {arm} arm is not keyword_only for all {n} items")
    assert f"`keyword_only` {n}/{n}" in row, (
        "the row's window-mode cell is not the artifact's measured mode")
    # ⛔ the row's OPENING quotation is the retired comparison's own figures, and
    # they are not derivable from these artifacts (they come from the owner's
    # re-measurement comment on `#2518`, 6939661408 — `#13118`'s body carries only
    # the pre-baseline `28 / 88 % / 75 %`), so they are pinned as quoted CONSTANTS
    # — the row exists to retire that comparison, and a re-staled quote there
    # misstates it (measured GREEN at `64504c6d20`: `76.3 → 79.9 @3` and
    # `85.0 → 80.0 @15`). The brief's §0 copy is pinned elsewhere; the row's was not.
    assert '"75.0 → 76.3 @3, 88.0 → 85.0 @15"' in row, (
        "the index row must quote the `#2518` re-measurement's pair exactly as it was")


# ── 2e. the published RESULT figures, recomputed cell by cell ────────────────
#
# ⛔ `#13267` F1: the artifacts' identity / provenance / refusal fields were
# pinned; their RESULTS were not, and neither was the brief's copy of them. The
# measured survivors (each `32 passed, 1 skipped`, exit 0) were
# `"on_hits": 17 -> 3`, `"mcnemar_exact_p": 1.5e-05 -> 0.5`,
# `"seed_lost_from_window": 3 -> 0`, `17/300 -> 3/300`, `2.08 pp -> 0.20 pp` and
# `727 / 900 -> 700 / 900`. So a pin that only compares the brief against the
# artifact is not enough — a CONSISTENT re-staling of both stays green. Two
# independent checks close it:
#
#  1. the artifact's own per-item rank pairs must PRODUCE every per-`k` cell
#     (the tool's formulas, not the recorded summary);
#  2. the brief's tables must repeat those cells, in the cell the reader sees.
#
# The sibling row in `config/ci-surfaces.yml` (`test_window_quality_m2.py`)
# already describes this pattern as "recomputed CELL BY CELL".


def _same_cell(recorded, recomputed) -> bool:
    """NaN-aware equality across the artifact's STRICT-JSON encoding.

    The RECORDED side carries `null` for a value that is not estimable, because the
    artifact has to be valid JSON (`#13267` review 12 F1): RFC 8259 has no `NaN` in
    its number grammar, so a non-finite float is written as `null`, and what says which
    null it is DIFFERS by cell: an MDE's `mde_note` names its reason, while a
    `correlation` block carries no note and is separated from an unrecorded one by key
    PRESENCE (both keys are always written — see its own test). The RECOMPUTED side still RETURNS NaN for that
    cell — that is the value `analyze` computes — so this helper is the bridge
    between the two encodings of one value.

    ⛔ Deliberately NOT "None equals anything": a recorded `null` matches only a
    recomputed NaN, so nulling out a cell that has a finite recomputation still
    reds.
    """
    if recorded is None:
        # ⛔ "a recorded null matches only a recomputed NaN" — EXECUTABLE, not
        # prose: a null matches nothing else, so a cell nulled out over a finite
        # OR an already-None recomputation still reds (the fallback `==` below
        # would have said True for None/None and quietly certified it).
        return isinstance(recomputed, float) and math.isnan(recomputed)
    if isinstance(recorded, float) and isinstance(recomputed, float):
        if math.isnan(recorded) and math.isnan(recomputed):
            # a lenient re-read (or an in-memory `analyze` dict) can still carry
            # the NaN itself; the same rule has to reach it.
            return True
        return math.isclose(recorded, recomputed, rel_tol=0, abs_tol=1e-12)
    if isinstance(recorded, dict) and isinstance(recomputed, dict):
        # ⛔ `dict == dict` delegates to `==` per value and `nan != nan`, so two
        # IDENTICAL `correlation: {rho: nan, se_reduction: nan}` blocks compare
        # UNEQUAL. Recurse so the NaN rule reaches inside the block (measured:
        # adding `correlation` to `_RESULT_CELLS` reds here for that reason alone).
        return (recorded.keys() == recomputed.keys()
                and all(_same_cell(recorded[k], recomputed[k]) for k in recorded))
    return recorded == recomputed


def _estimable(value) -> bool:
    """A recorded figure a reader can compare — not `null`, not NaN.

    ⛔ Both spellings have to be excluded. The artifact encodes a value the tool
    could not estimate as strict-JSON `null` (`#13267` review 12 F1), so
    `value == value` is TRUE for it (`None == None`) and a filter built only on the
    NaN test multiplied `None` by 100. A lenient re-read of an older file can still
    hand back `float('nan')`, which `math.isfinite` rejects too.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _recomputed_entry(block: dict, k: int) -> dict:
    """The `analyze` entry the artifact's OWN recorded per-item ranks produce.

    `per_item[i]["off_rank"]`/`["on_rank"]` are the target's rank in each arm's
    read — the same quantity `_hits_at` converts — so this is a re-derivation, not
    a re-read of the summary. The bootstrap is seeded and over items in the
    artifact's own order, so its interval reproduces exactly too.
    """
    rows = block["per_item"].values()
    off = [1 if (r["off_rank"] is not None and r["off_rank"] < k) else 0
           for r in rows]
    on = [1 if (r["on_rank"] is not None and r["on_rank"] < k) else 0
          for r in rows]
    recorded = block["per_k"][f"k={k}"]
    return P.analyze(off, on, ks=(k,),
                     bootstrap=recorded["bootstrap"]["resamples"],
                     seed=recorded["bootstrap"]["seed"])


_RESULT_CELLS = ("n", "off_hits", "on_hits", "discordant_miss_to_hit",
                 "discordant_hit_to_miss", "mcnemar_exact_p", "mde_paired",
                 "mde_independent", "off_recall", "on_recall",
                 # ⛔ `#13267` review 8: these three are emitted by `analyze` and were
                 # in NO pin and in neither derived list — measured free parameters.
                 # The brief's §2 design claim ("the reduction is measured,
                 # `correlation.rho`") and §8's `mde_note: ""` provenance evidence
                 # both rest on cells a re-stale could contradict with the suite green.
                 "discordance", "correlation", "mde_note")


def test_every_published_result_cell_recomputes_from_the_artifacts_own_rows():
    """⛔ F1: not one published RESULT figure was pinned.

    Every cell below is recomputed from the 300 (60) rank pairs the artifact
    records per item, using the tool's own `analyze` — so a re-staled summary reds
    even when `b`, `p` and the recall are moved TOGETHER to stay self-consistent
    (measured: `17/300` with `b=17, p=1.53e-05` re-staled to `3/300, b=3, p=0.5`
    passed the whole suite). `off_topk_displacement` and `seed_effect` cannot be
    re-derived (the artifact records neither the ranked lists' ids nor the seed
    ranks), so they are held to the invariants they must satisfy and pinned
    against the brief in the next test.
    """
    for name in _ARTIFACTS:
        art = _artifact(name)
        for arm, block in art["arms"].items():
            assert block["per_item"], f"{name}/{arm} records no per-item rows"
            for key in sorted(block["per_k"]):
                k = int(key.split("=")[1])
                entry = block["per_k"][key]
                recomputed = _recomputed_entry(block, k)
                for cell in _RESULT_CELLS:
                    assert _same_cell(entry[cell], recomputed[cell]), (
                        f"{name}/{arm}/{key}: recorded {cell}={entry[cell]!r} "
                        f"but the artifact's own per-item ranks produce "
                        f"{recomputed[cell]!r}")
                assert entry["bootstrap"]["ci95"] == \
                    recomputed["bootstrap"]["ci95"], (
                        f"{name}/{arm}/{key}: the seeded bootstrap interval does "
                        "not reproduce from the recorded rows")
                assert entry["bootstrap"]["delta"] == \
                    recomputed["bootstrap"]["delta"], (
                        f"{name}/{arm}/{key}: the recorded bootstrap delta is not "
                        "`(on_hits - off_hits) / n`")
                disp = entry.get("off_topk_displacement")
                if disp is not None:
                    n = recomputed["n"]
                    assert disp["items_with_off_topk"] == n
                    assert 0 <= disp["displaced_ids"] <= n * k
                    assert 0 <= disp["off_top1_kept_in_window"] <= n
                    assert "CHURN" in disp["note"]
                seed = entry.get("seed_effect")
                if seed is not None:
                    n = recomputed["n"]
                    for field in ("seed_at_top1_off", "seed_at_top1_on",
                                  "seed_lost_from_window",
                                  "seed_demoted_but_kept"):
                        assert 0 <= seed[field] <= n, (
                            f"{name}/{arm}/{key}: {field}={seed[field]!r} is not "
                            f"a count of {n} items")


# ── the artifacts' own ENCODING: strict JSON, and a null that says why it is ──
_COMMITTED_JSON = ("paired-point-300.json", "paired-point-300-short-query.json",
                   "paired-object-60-control.json",
                   "corpus-snapshot-manifest.json")


def _strict_round_trip(text: str) -> dict:
    """Parse, then re-serialize with `allow_nan=False`; raise if a token survives.

    ⛔ This is the pin the repo's `#1747` strict-JSON convention already has on its
    sibling artifact writer (`tools/longmem_eval/report.py::_json_safe`, held by
    `tests/test_eval_report_integrity.py`) and this suite did not. A bare `NaN`
    token makes the document **not JSON**: RFC 8259 has no `NaN` in its number
    grammar, so a strict parser rejects the WHOLE file rather than the one cell,
    and a tolerant one reads the cell as `null` — the same value as a field that
    was never recorded.
    """
    parsed = json.loads(text)
    json.dumps(parsed, allow_nan=False)
    return parsed


def test_every_committed_artifact_is_strict_json():
    """F1 — the evidence carried cells no strict reader could load.

    Measured before the fix (`#13267` review 12): 16 bare `NaN` tokens in the
    control, 8 in each point artifact, and `node -e 'JSON.parse(...)'` on the
    control raised `SyntaxError: Unexpected token 'N', … "rho": NaN,`. The
    suite could not see it *by construction* — it reads through Python's lenient
    decoder — so the invalid encoding was invisible to every assertion here.

    ⛔ The blast radius is four *kinds* of cell, not "four cells per `k` in every
    file" (`#13267` review 13 F3): the control holds all four per `k` (4 × 4 = 16),
    while each point artifact holds only the two correlation cells per `k` (4 × 2 =
    8) because its `mde_paired` / `mde_independent` are finite. The counts below are
    the measured ones; `test_the_briefs_blast_radius_count_matches_the_cells_that_were_nulled`
    holds §8's prose to the same shape.
    """
    seen = set()
    for path in sorted(_DOCS.glob("*.json")):
        seen.add(path.name)
        try:
            _strict_round_trip(path.read_text())
        except ValueError as exc:  # pragma: no cover - only on a regression
            raise AssertionError(
                f"{path.name} is not strict JSON ({exc}) — a `NaN`/`Infinity` "
                "token makes the artifact unloadable by every strict parser"
            ) from exc
    assert seen == set(_COMMITTED_JSON), (
        f"the committed evidence is {sorted(_COMMITTED_JSON)}, found {sorted(seen)}")


def test_the_writer_encodes_a_not_estimable_mde_as_strict_null(tmp_path):
    """F1 — the value the artifact exists to publish honestly was the one token no
    strict reader accepts.

    A zero-discordance run is `mde_paired: nan` with `mde_note` "NOT ESTIMABLE…"
    (measured on the control, where the two switch states are one code path).
    `json.dumps` defaults to `allow_nan=True`, so the committed files carried the
    bare token: strict parsers rejected the file outright and `jq` read the cell as
    `null`, i.e. as an unrecorded field.

    The non-vacuity is asserted rather than described: the PRE-fix call shape emits
    the token, and re-serializing that text strictly is rejected.
    """
    row = P.analyze([0] * 60, [0] * 60, ks=P.KS, bootstrap=50, seed=7)
    assert math.isnan(row["mde_paired"])          # the value IS undefined
    assert "NOT ESTIMABLE" in row["mde_note"]     # … and says so in prose

    legacy = json.dumps({"per_k": {"k=3": row}}, indent=2, sort_keys=True)
    assert "NaN" in legacy, "the pre-fix writer must emit the token"
    with pytest.raises(ValueError):
        json.dumps(json.loads(legacy), allow_nan=False)

    out = tmp_path / "artifact.json"
    P._write_json(out, {"per_k": {"k=3": row}})
    text = out.read_text()
    assert "NaN" not in text and "Infinity" not in text
    cell = _strict_round_trip(text)["per_k"]["k=3"]
    assert cell["mde_paired"] is None             # "not estimable" …
    assert "NOT ESTIMABLE" in cell["mde_note"]    # … named by its carrier
    assert cell["correlation"] == {"rho": None, "se_reduction": None}


def test_a_null_cell_is_distinguishable_from_an_unrecorded_one(tmp_path):
    """F1's second half: the encoding must not collapse into "never recorded".

    `_ids_digest(None)` is *unrecorded* while `_ids_digest([]) == sha256(b"")` is
    *recorded empty*, and `identity_measured: false` + `identity_note` exist so a
    null count cannot read as a measured one. The MDE needs the same separation,
    and it has it: the cell is PRESENT and null, and the `mde_note` beside it names
    the reason — so an absent key and a not-estimable value are different facts.

    ⛔ `#13267` review 13 F1: the contrast used to be asserted with
    `assert "no_such_field" not in cell` — the absence of a key *literally named*
    `no_such_field`, a value no mutation of the fix can move, so it read as a
    presence-vs-absence guard while testing nothing. The contrast is now made where
    it can fail, in both places it lives: at the DATA level `cell.get(…, "ABSENT")`
    separates absent / recorded-null / the pre-fix lenient-parsed `nan`, and at the
    WRITER level a real cell (present, null) is written beside a copy with the field
    genuinely absent and the two must stay distinguishable in BOTH directions — the
    writer may neither drop the null (`{k: v for k, v in … if v is not None}` reds
    the first row with `KeyError`) nor manufacture the key for the stripped copy (a
    writer that back-fills known fields reds the third).
    """
    control = _artifact("paired-object-60-control.json")
    cell = control["arms"]["object"]["per_k"]["k=15"]
    # the separation is executable, not prose: the default is NOT None, so a
    # genuinely absent key reads as ABSENT here while a recorded-null cell reads
    # as None — and the pre-fix `NaN` bytes (lenient-parsed to nan) read as neither
    assert cell.get("mde_paired", "ABSENT") is None
    assert "NOT ESTIMABLE" in cell["mde_note"]

    stripped = {key: value for key, value in cell.items() if key != "mde_paired"}
    assert "mde_paired" not in stripped      # genuinely ABSENT, not null
    out = tmp_path / "null-vs-absent.json"
    P._write_json(out, {"present_null": cell, "absent": stripped})
    written = _strict_round_trip(out.read_text())
    assert written["present_null"]["mde_paired"] is None        # kept as null
    assert "mde_paired" not in written["absent"]                # not invented
    assert written["absent"] != written["present_null"]
    # a finite MDE keeps its number and needs no note
    point = _artifact("paired-point-300.json")
    finite = point["arms"]["point"]["per_k"]["k=15"]
    assert isinstance(finite["mde_paired"], float) and finite["mde_paired"] > 0
    assert finite["mde_note"] == ""

    # ⛔ The `correlation` block's nulls are the OTHER carrier: no note sits
    # beside them (the brief says so), so what separates "recorded, not
    # estimable" from "never recorded" is key PRESENCE. Pin that separation
    # where it is load-bearing, for the control and a point artifact alike —
    # a dropped key reds the key-set assertion, a restored `NaN` reds the
    # `is None` one (measured: every committed correlation null is the
    # zero-variance case, e.g. the pair artifacts' off-arm hits are all 0).
    for art, arm in ((control, "object"), (point, "point")):
        corr = art["arms"][arm]["per_k"]["k=15"]["correlation"]
        assert set(corr) == {"rho", "se_reduction"}, (
            f"{arm}: the correlation block is {sorted(corr)}, so a null rho is "
            "not distinguishable from an unrecorded one by key presence")
        assert corr.get("rho", "ABSENT") is None
        assert corr.get("se_reduction", "ABSENT") is None


def test_json_safe_maps_only_non_finite_floats_to_null():
    """The mapping is lossless for everything else — a finite float is not touched."""
    assert P._json_safe(float("nan")) is None
    assert P._json_safe(float("inf")) is None
    assert P._json_safe(float("-inf")) is None
    assert P._json_safe(0.0625) == 0.0625
    assert P._json_safe(3) == 3
    assert P._json_safe(True) is True
    assert P._json_safe({"a": [float("nan"), 1.5]}) == {"a": [None, 1.5]}
    assert P._json_safe((float("inf"),)) == [None]
    assert P._json_safe("NaN") == "NaN"   # a string is not a non-finite float


def test_no_artifact_note_claims_a_token_the_file_no_longer_carries():
    """F2 — the re-encoding left three `git_head_note`s describing a `nan` cell.

    ⛔ `git_head_note` is THE carrier a reader uses to decide *which* null a cell is
    — the job F1's cell-level encoding exists to do, one layer up — so a note naming
    a `NaN` the file no longer contains collapses exactly that distinction. Measured
    at the pre-fix head (`#13267` review 13 F2): all three artifacts said the
    control's populated `mde_note` "sits beside a `nan` `mde_paired`" while the cell
    alongside it is `null`.

    The pin is written against the defect's *shape* rather than one sentence: a note
    that still names the token must also say the file encodes it as strict `null`,
    so the two can never disagree again. A note that does not mention the token (a
    future run's generic writer text) is unaffected.
    """
    for name in _ARTIFACTS:
        note = _artifact(name)["provenance"]["git_head_note"]
        assert note, f"{name} records no `git_head_note`"
        if "nan" in note.lower():
            assert "encodes" in note and "`null`" in note, (
                f"{name}'s `git_head_note` names the `NaN` token without saying the "
                f"file encodes it as strict `null` — a reader cannot tell which "
                f"null the cell is: {note!r}")


#: The cell kinds the runs wrote `NaN` into (§8's blast radius).
_NAN_CELL_PATHS = ("mde_paired", "mde_independent",
                   "correlation.rho", "correlation.se_reduction")


def _null_cell_paths(cell: dict) -> list[str]:
    """The `_NAN_CELL_PATHS` entries this cell records as `null` (not estimable)."""
    nulled = []
    for path in _NAN_CELL_PATHS:
        value = cell
        for part in path.split("."):
            value = value[part]
        if value is None:
            nulled.append(path)
    return nulled


def test_the_briefs_blast_radius_count_matches_the_cells_that_were_nulled():
    """F3 — §8 said "four cells per `k`" for all three files; two of them held two.

    ⛔ Measured on the pre-fix blobs (`#13267` review 13 F3): 16 bare `NaN` tokens in
    the control and **8** in each point artifact, because a point artifact's
    `mde_paired` / `mde_independent` are finite (`0.0383…` at k=15). The paragraph
    exists so a reader can verify the re-encoding, which makes it the one place a
    50 % overstatement must not stand — and the same count was restated in this
    file's own docstring, so the shape is pinned here instead of living in prose.
    """
    measured = {}
    for name in _ARTIFACTS:
        art = _artifact(name)
        per_k = [cell for block in art["arms"].values()
                 for cell in block["per_k"].values()]
        shapes = {tuple(_null_cell_paths(cell)) for cell in per_k}
        assert len(shapes) == 1, f"{name}: the null cells are not the same in every `k`: {shapes}"
        measured[name] = len(next(iter(shapes)))
    assert measured == {"paired-object-60-control.json": 4,
                        "paired-point-300.json": 2,
                        "paired-point-300-short-query.json": 2}, measured

    brief = (_DOCS / "research-brief.md").read_text()
    assert "all four per `k` in the control (16" in brief, (
        "§8 must state the control's four-per-`k` count (16) where a reader verifying "
        "the re-encoding finds it")
    assert "the two correlation cells per `k` in each point artifact (8 each" in brief, (
        "§8 must state the point artifacts' two-per-`k` count (8 each) — the sentence "
        "that used to claim four per `k` for all three files")


# ── the brief's own tables, parsed as a reader sees them ────────────────────

def _tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Every markdown table in `text` as (header, rows), rule row dropped."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    out = []
    for block in blocks:
        rows = [[c.strip() for c in ln.strip().strip("|").split("|")]
                for ln in block]
        body = [r for r in rows[1:]
                if not all(c and set(c) <= set("-: ") for c in r)]
        out.append((rows[0], body))
    return out


def _table(text: str, *needles: str) -> tuple[list[str], list[list[str]]]:
    for header, body in _tables(text):
        flat = " ".join(header)
        if all(nd in flat for nd in needles) and body:
            return header, body
    raise AssertionError(f"the brief carries no table with header(s) {needles!r}")


def _col(header: list[str], *needles: str) -> int:
    for i, cell in enumerate(header):
        if all(nd in cell for nd in needles):
            return i
    raise AssertionError(f"no column matching {needles!r} in {header!r}")


def _row(rows: list[list[str]], needle: str) -> list[str]:
    for row in rows:
        if needle in row[0]:
            return row
    raise AssertionError(f"no row labelled {needle!r}")


def _plain(cell: str) -> str:
    return cell.replace("**", "").replace("`", "").replace(",", "").strip()


_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _printed(cell: str) -> list[tuple[float, float]]:
    """Every number a cell PRINTS, with the tolerance its own digits allow.

    10^-decimals, not half of it: the brief both rounds (`0.03125` → `0.0313`) and
    truncates (`0.000244140625` → `0.00024`), so the honest bound on "the printed
    value is the artifact's value" is one unit in the last printed place. A
    re-staled figure moves by whole orders of magnitude, not by one digit.
    """
    out = []
    for match in _NUMBER.finditer(_plain(cell)):
        token = match.group(0)
        decimals = len(token.split(".")[1]) if "." in token else 0
        out.append((float(token), 10.0 ** -decimals if decimals else 1.0))
    assert out, f"no number in {cell!r}"
    return out


def _assert_prints(cell: str, value: float, where: str) -> None:
    printed, tol = _printed(cell)[0]
    assert abs(printed - value) <= tol + 1e-12, (
        f"{where}: the brief prints {_plain(cell)!r} but its artifact holds "
        f"{value!r}")


def _assert_prints_all(cell: str, values: list[float], where: str) -> None:
    printed = _printed(cell)
    assert len(printed) == len(values), (
        f"{where}: the brief prints {len(printed)} number(s) for "
        f"{len(values)} recorded value(s): {_plain(cell)!r}")
    for (shown, tol), value in zip(printed, values, strict=True):
        assert abs(shown - value) <= tol + 1e-12, (
            f"{where}: the brief prints {_plain(cell)!r} but its artifact "
            f"holds {values!r}")


def _fraction(cell: str) -> tuple[int, int]:
    match = re.search(r"(\d+)\s*/\s*(\d+)", _plain(cell))
    assert match, f"no `a/b` fraction in {cell!r}"
    return int(match.group(1)), int(match.group(2))


_SUPERSCRIPT = str.maketrans("\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079",
                             "0123456789")


def _sci_printed(cell: str) -> list[tuple[float, float]]:
    """Every `<mantissa> × 10<n>` a cell prints, with the tolerance its digits allow.

    ⛔ `docs/00_index.md` is prose, and its McNemar tails are printed in
    scientific notation (`2 × 10⁻⁵`, `2.4 × 10⁻⁴`) — the shape `_printed` cannot
    read, because the exponent is supercripted rather than ASCII. Rounding is the
    row's own (`2` for `1.52587890625e-05`), so the honest bound is one unit in
    the last printed place of the MANTISSA, scaled by the exponent — the same
    rule `_printed` applies to every other cell.
    """
    out: list[tuple[float, float]] = []
    for mantissa, sign, digits in re.findall(
            r"(\d+(?:\.\d+)?)\s*×\s*10([\u207b+]?)([\u2070\u00b9\u00b2\u00b3\u2074-\u2079]+)",
            cell, flags=re.S):
        exp = int(digits.translate(_SUPERSCRIPT)) * (-1 if sign == "\u207b" else 1)
        decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
        out.append((float(mantissa) * 10.0 ** exp, 10.0 ** (exp - decimals)))
    return out


def test_the_briefs_result_tables_repeat_their_artifacts_cell_by_cell():
    """⛔ F1, the prose half: a figure re-staled in the BRIEF reds here.

    Measured survivors before this test (artifact and brief each mutated alone,
    both left green): `17/300` → `3/300` and → `99/300` in §4, `2.08 pp` →
    `0.20 pp` in the MDE column, `727 / 900` → `700 / 900` in §4.1, `293 of 300`
    → `1 of 300`, and `seed lost from window` `3` → `0` in the short-query table.
    Each cell below is compared against the artifact it claims to quote, in the
    cell position a reader reads it in — not against a substring of the file.

    ⛔ F1 (this round): a table's ROW SET is part of the record too. Comparing the
    rows the brief happens to carry cannot show an omission, and three omissions
    measured GREEN on the pre-fix pins — the short-query k=15 row (the only cell
    publishing `seed lost from window = 3` / `seed demoted = 2`) and either §5
    tail. A dropped row is an omission, not a shorter table, so the published k
    sets are declared and asserted here.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    head = _artifact("paired-point-300.json")
    short = _artifact("paired-point-300-short-query.json")
    n = head["population"]["sample"]

    # ── §4, the headline table ─────────────────────────────────────────────
    header, rows = _table(brief, "OFF hits", "McNemar")
    assert len(rows) == len(head["arms"]["point"]["per_k"]), (
        "the headline table must carry one row per published k")
    for row in rows:
        k = int(_plain(row[0]))
        where = f"brief §4 k={k}"
        cell = head["arms"]["point"]["per_k"][f"k={k}"]
        assert _fraction(row[_col(header, "OFF hits")]) == (cell["off_hits"], n)
        assert _fraction(row[_col(header, "ON hits")]) == (cell["on_hits"], n), where
        assert int(_plain(row[_col(header, "b (")])) == \
            cell["discordant_miss_to_hit"], where
        assert int(_plain(row[_col(header, "c (")])) == \
            cell["discordant_hit_to_miss"], where
        _assert_prints(row[_col(header, "McNemar")], cell["mcnemar_exact_p"],
                       where + " p")
        _assert_prints(row[_col(header, "paired Δ")],
                       (cell["on_hits"] - cell["off_hits"]) / n * 100,
                       where + " Δ")
        _assert_prints_all(row[_col(header, "CI")],
                           [v * 100 for v in cell["bootstrap"]["ci95"]],
                           where + " CI")
        _assert_prints(row[_col(header, "MDE")], cell["mde_paired"] * 100,
                       where + " MDE")

    # ── §5, the falsifier that repeats the tails in prose ──────────────────
    tails = re.findall(r"([0-9.]+)\*\* \(@([0-9]+)\)", brief)
    assert tails, "the falsifier table is where the exact tails are published"
    # ⛔ F1: the set of ks a table PUBLISHES is part of the record, so it is
    # declared and asserted — iterating whatever rows the brief happens to carry
    # left the harm-side tables unable to show an omission. Measured on the
    # pre-fix pins: deleting the §5 `(@15)` tail stayed GREEN
    shown_ks = sorted({int(k) for _, k in tails})
    assert shown_ks == [3, 5, 10, 15], (
        "the falsifier publishes a p at k = 3/5/10/15; the brief now carries "
        f"{shown_ks} — a dropped tail is an omission, not a shorter table")
    for shown, k in tails:
        cell = head["arms"]["point"]["per_k"][f"k={k}"]
        _assert_prints(shown, cell["mcnemar_exact_p"], f"brief §5 p @{k}")

    # ── §4.1, the harm table (only the ks it publishes) ────────────────────
    header, rows = _table(brief, "measure")
    kcols = {k: _col(header, f"k = {k}") for k in (3, 15)}
    displaced = _row(rows, "displaced")
    kept = _row(rows, "top-1 survived")
    lost = _row(rows, "lost from the window")
    demoted = _row(rows, "demoted but kept")
    for k, column in kcols.items():
        where = f"brief §4.1 k={k}"
        cell = head["arms"]["point"]["per_k"][f"k={k}"]
        disp = cell["off_topk_displacement"]
        shown = _printed(displaced[column])
        assert (int(shown[0][0]), int(shown[1][0])) == (
            disp["displaced_ids"], disp["items_with_off_topk"] * k), where
        _assert_prints(str(shown[2][0]) + " %",
                       disp["displaced_ids"] / (disp["items_with_off_topk"] * k) * 100,
                       where + " %")
        assert _fraction(kept[column]) == (disp["off_top1_kept_in_window"],
                                           disp["items_with_off_topk"]), where
        seed = cell["seed_effect"]
        assert int(_plain(lost[column])) == seed["seed_lost_from_window"], where
        assert int(_plain(demoted[column])) == seed["seed_demoted_but_kept"], where

    # ── §4.1, the robustness arm's table ────────────────────────────────────
    n_short = short["population"]["sample"]
    header, rows = _table(brief, "seed lost from window", "seed demoted")
    assert rows, "the short-query arm is where the harm side is measurable"
    # ⛔ F1: this table publishes k = 3/10/15 of the artifact's four, and it is
    # the only place two of the harm figures appear — deleting its k=15 row (the
    # `seed lost from window = 3` / `seed demoted = 2` cells) stayed GREEN before
    # this pin, on a docs-only diff, which is the change class `tier1` exists for
    published_ks = [int(_plain(row[0])) for row in rows]
    assert published_ks == [3, 10, 15], (
        "the short-query table publishes k = 3/10/15; the brief now carries "
        f"{published_ks} — a dropped row is an omission, not a shorter table")
    for row in rows:
        k = int(_plain(row[0]))
        where = f"brief §4.1 short-query k={k}"
        cell = short["arms"]["point"]["per_k"][f"k={k}"]
        assert _fraction(row[_col(header, "OFF")]) == (cell["off_hits"], n_short), where
        assert _fraction(row[_col(header, "ON")]) == (cell["on_hits"], n_short), where
        assert int(_plain(row[_col(header, "b")])) == \
            cell["discordant_miss_to_hit"], where
        assert int(_plain(row[_col(header, "c")])) == \
            cell["discordant_hit_to_miss"], where
        _assert_prints(row[_col(header, "p")], cell["mcnemar_exact_p"], where + " p")
        _assert_prints(row[_col(header, "Δ")],
                       (cell["on_hits"] - cell["off_hits"]) / n_short * 100,
                       where + " Δ")
        _assert_prints(row[_col(header, "MDE")], cell["mde_paired"] * 100,
                       where + " MDE")
        seed = cell["seed_effect"]
        assert int(_plain(row[_col(header, "seed lost")])) == \
            seed["seed_lost_from_window"], where
        assert int(_plain(row[_col(header, "seed demoted")])) == \
            seed["seed_demoted_but_kept"], where

    # ── the count of items the expansion fired on, in prose ────────────────
    fired = re.search(r"fired on \*\*(\d+) of (\d+)\*\*", brief)
    assert fired, "the brief states how many items the expansion fired on"
    assert (int(fired.group(1)), int(fired.group(2))) == (
        head["arms"]["point"]["expansion_fired_items"], n), (
            "the headline arm's firing count and sample size, not a re-staled pair")

    # ── the §5 / §7 RESTATEMENTS, not only the tables ──────────────────────
    # ⛔ measured GREEN at `d5be0e6840`, each on its own: the §5 MET row's MDE list
    # (`3.84 → 3.99`), §7's `+5.67 pp @15`, and §7's seed count (`5 → 9 of 300`).
    # A figure the tables pin can still be re-staled where the prose repeats it.
    per_k = head["arms"]["point"]["per_k"]
    mde_list = " / ".join(f"{per_k[f'k={k}']['mde_paired'] * 100:.2f}"
                          for k in (3, 5, 10, 15))
    assert f"**{mde_list} pp**" in brief, (
        f"the §5 MDE restatement must be the artifact's own ({mde_list} pp)")
    delta15 = (per_k["k=15"]["on_hits"] - per_k["k=15"]["off_hits"]) / n * 100
    assert f"+{delta15:.2f} pp @15" in brief, (
        "the §7 lift restatement must be the artifact's own k=15 delta")
    short_seed = short["arms"]["point"]["per_k"]["k=15"]["seed_effect"]
    n_short = short["population"]["sample"]
    moved_short = (short_seed["seed_lost_from_window"]
                   + short_seed["seed_demoted_but_kept"])
    assert f"{moved_short} of {n_short} items whose seed moved" in brief, (
        "the §7 cost restatement must be the short arm's own seed cells")

    # ⛔ §4.1 is where the COST side of the measurement actually lives — the
    # paragraph §7's third decision rests on — and every figure in it was unpinned
    # (measured GREEN at `64504c6d20`: `5 of 300 → 9 of 300`, `1.7 % → 9.9 %`,
    # `3 out of the k-window, 2 demoted` → `1 …, 4 …`, and `(1 % of items) → (9 %)`).
    # The copies in the index row and §7 were pinned; the brief, the document the
    # row links to, was the weaker copy.
    # the sentence wraps in the source, so it is compared against a
    # whitespace-collapsed view — the numbers are the assertion, not the line breaks
    flat_brief = " ".join(brief.split())
    assert (f"**{moved_short} of {n_short} items "
            f"({moved_short / n_short * 100:.1f} %) had their own best match moved "
            f"— {short_seed['seed_lost_from_window']} out of the k-window, "
            f"{short_seed['seed_demoted_but_kept']} demoted but kept**" in flat_brief), (
        "the §4.1 harm sentence must be the short arm's own seed cells, including "
        "the moved percentage")
    assert (f"the {short_seed['seed_lost_from_window']} seeds lost are a bare count "
            f"(1 % of items)") in brief, (
        "the §4.1 bare-count caveat must name the short arm's own lost count")

    # ⛔ and the two other prose sites that restate a measured figure with no pin
    # (measured GREEN at `64504c6d20`): `keyword_only 300/300` at §1 and in the §5
    # MET row (`→ 299/300` each), and `OFF is 0/300 at every k` (`→ 5/300`, which
    # contradicted the table four lines above it).
    modes = head["arms"]["point"]["window_modes"]
    for arm, block in modes.items():
        assert block["keyword_only"] == n, (
            f"the {arm} arm is not keyword_only for all {n} items")
    assert brief.count(f"`keyword_only` {n}/{n}") >= 2, (
        f"`keyword_only` {n}/{n} is stated at §1 and in the §5 MET row")
    k3_off = per_k["k=3"]["off_hits"]
    for k, cell in per_k.items():
        assert cell["off_hits"] == k3_off, (
            f"OFF is not uniform across k ({k} says {cell['off_hits']})")
    assert f"OFF is {k3_off}/{n} at every k BY CONSTRUCTION" in flat_brief, (
        "the §4.1 construction caveat must be the artifact's own OFF count")


def test_the_mde_direction_is_pinned_and_not_only_its_values():
    """⛔ `#13267` F7: the MDE **rises** with k, so the power claim was inverted.

    The brief said this run "can resolve a 3 pp effect at k ≥ 10 and cannot at
    k = 3". The artifact says the opposite: MDE is 2.08 pp at k = 3 and 3.84 pp at
    k = 15 — it *shrinks* as k falls, because the discordant count shrinks
    (b = 5 → 6 → 13 → 17). A table two lines above the sentence contradicted it
    and nothing compared the two, so the inversion survived a full review round.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    per_k = _artifact("paired-point-300.json")["arms"]["point"]["per_k"]
    mdes = {int(k.split("=")[1]): v["mde_paired"] for k, v in per_k.items()}
    ks = sorted(mdes)
    assert all(mdes[a] < mdes[b] for a, b in itertools.pairwise(ks)), (
        "this pin encodes the measured shape — the MDE rises with k; the artifact "
        f"now says {mdes}, so re-derive the brief's power claim before trusting it")
    # 3 pp is resolvable exactly where the MDE sits below it. Said by the brief as
    # a sentence, so the two must agree: a k the artifact powers is a k it names.
    can = [k for k in ks if mdes[k] < 0.03]
    cannot = [k for k in ks if mdes[k] >= 0.03]
    sentence = re.search(r"can\*\* resolve a 3 pp effect at (.*?) and\s+\*\*cannot\*\*",
                         brief, re.S)
    assert sentence, "the brief states which k can and cannot resolve a 3 pp effect"
    said_can = {int(x) for x in re.findall(r"k = (\d+)", sentence.group(1))}
    assert said_can == set(can), (
        f"the brief says 3 pp is resolvable at k = {sorted(said_can)} but the "
        f"artifact's MDE powers exactly k = {can}; 3 pp is NOT resolvable at "
        f"k = {cannot}")


def test_the_winner_rule_is_the_mde_one_and_applied_to_every_row():
    """⛔ `#13267` F7: k = 5 was a non-winner by MDE and read as a winner by p.

    The brief declares the rule for k = 3 — "+1.67 pp against a 2.08 pp MDE ⇒ no
    winner is claimable". Applying that same rule, k = 5 (+2.00 pp against
    2.28 pp) also fails, yet its p was bolded as significant with no caveat, so
    one of the two rows was described wrongly whichever rule was in force.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    head = _artifact("paired-point-300.json")
    n = head["population"]["sample"]
    per_k = head["arms"]["point"]["per_k"]
    no_winner = set()
    for key, cell in per_k.items():
        delta = (cell["on_hits"] - cell["off_hits"]) / n
        if abs(delta) < cell["mde_paired"]:
            no_winner.add(int(key.split("=")[1]))
    assert no_winner == {3, 5}, (
        f"the artifact's own numbers make k = {sorted(no_winner)} non-winners "
        "(|delta| < that row's MDE); the pin assumes {3, 5}")
    rule = re.search(r"neither does k = (\d+)", brief)
    assert rule, "the brief names the second non-winner explicitly, not just k = 3"
    assert {int(rule.group(1))} | {3} == no_winner, (
        "the brief's winner rule must be applied to every row the artifact makes "
        f"a non-winner ({sorted(no_winner)}), not to k = 3 alone")


def test_the_brief_does_not_claim_an_unmeasured_seed_absence():
    """⛔ `#13267` F7: `seed_at_top1` is a TOP-1 count; it cannot report a 30-wide window.

    The brief said the seed "does not enter a 30-wide window in either arm" and
    cited `seed_at_top1` 0 → 0 — a top-*1* count. `per_item` records no
    `seed_rank` at all, so the artifact's four zeroes are equally consistent with
    "never entered the window" and "entered at an unchanged rank". Dismissing the
    only labelled harm signal as measured was the same `None == None` shape as F1.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    art = _artifact("paired-point-300.json")
    arm = art["arms"]["point"]
    assert "seed_rank" not in next(iter(arm["per_item"].values())), (
        "the artifact now records per-item seed ranks — the brief can cite them, "
        "and this pin must be retired rather than silently kept")
    assert "does not enter a 30-wide window" not in brief, (
        "that is a claim about WINDOW MEMBERSHIP and the artifact records none; "
        "say the signal is unrecorded, not measured absent")
    assert "unrecorded at this scale" in brief, (
        "the brief must name the harm signal unrecorded, which is what the "
        "committed evidence supports")


def test_no_artifact_names_a_symbol_that_does_not_resolve():
    """⛔ `#13267` review 4: the control's structural claim cited `sdk.SDK.search_graph`.

    That symbol resolves to nothing — the class is `TortoiseSDK` and there is no
    `SDK` on it. It sat in the ONE committed field that grounds the control's
    "one code path" claim, and it survived four review rounds because every pin
    read the brief and none read the artifacts' prose. A citation that does not
    resolve is the same defect the brief's own predicate citation exists to avoid.
    """
    sdk = (Path(__file__).resolve().parent.parent / "tortoise" / "sdk.py").read_text()
    assert "class TortoiseSDK" in sdk
    assert "class SDK" not in sdk, (
        "an `SDK` class now exists — re-derive which symbol the gate sits in")
    for name in _ARTIFACTS:
        raw = (_DOCS / name).read_text()
        assert "sdk.SDK" not in raw, (
            f"{name} cites `sdk.SDK…`, which does not resolve — the class is "
            "`TortoiseSDK` (`#13267` review 4)")
    control = _artifact("paired-object-60-control.json")["arms"]["object"]
    assert "TortoiseSDK.tortoise_fts_query" in control["identity_note"], (
        "the control's structural claim must cite the symbol that resolves")


def test_the_index_row_calls_displacement_churn_not_harm():
    """⛔ `#13267` F7: the docs index presented the churn figure as the harm side.

    The artifact's own note on `off_topk_displacement` reads "CHURN, not harm,
    unless the ids carry qrels", and the brief §4.1 says the same — but the index
    row other lanes read first said "the harm side is measured" and led with the
    727/900 displacement. There are no qrels; that sentence was false.
    """
    index = (Path(__file__).resolve().parent.parent / "docs" / "00_index.md").read_text()
    row = next((ln for ln in index.splitlines()
                if "#2518" in ln and "PAIRED A/B" in ln), None)
    assert row, "the docs index carries the #2518 paired-A/B row"
    # ⛔ casefold: the first version of this pin compared lowercase only and the
    # row opens "The harm side...", so the mutant it exists to kill passed.
    low = row.lower()
    assert "the harm side is measured, not argued" not in low, (
        "displacement is churn — the artifact says so in its own `note` field")
    assert "churn, not harm" in low, (
        "the index row must label the displacement figure as churn")
    assert "displaced" in low and "727" in row, (
        "the displacement figure is still the one the row publishes")


def test_the_brief_parsers_are_not_vacuous():
    """The pins above can only kill a mutant if they actually read the cells.

    A table parser that silently returned no rows, or a `_printed` that found no
    number, would make every assertion above vacuous — the failure mode these
    pins exist to close, one level up.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    header, rows = _table(brief, "OFF hits", "McNemar")
    assert [int(_plain(r[0])) for r in rows] == [3, 5, 10, 15]
    assert _fraction(rows[0][_col(header, "ON hits")]) == (5, 300)
    assert _col(header, "McNemar") != _col(header, "MDE")
    # every brief result cell parses to at least one number, and `_printed`
    # carries the printed precision rather than a fixed tolerance
    assert _printed("**0.00024**")[0] == (0.00024, 1e-05)
    assert _printed("[+0.33, +3.33]") == [(0.33, 0.01), (3.33, 0.01)]
    assert _printed("**3,350 / 4,500 (74.4 %)**")[:2] == [(3350.0, 1.0), (4500.0, 1.0)]


def test_a_regenerated_artifact_satisfies_the_same_doc_pins(_no_embedder_probe):
    """⛔ The doc pins were assertions AGAINST the tool's own output (`#13267` F5).

    The three committed artifacts are hand-annotated legacy runs:
    `identity_measured` was written into them by hand, and
    `construction_unrecorded` names the knobs that run did not record. A doc pin
    that spells that legacy shape out as a requirement reds the suite on a correct
    re-generation — measured: the tool emits `construction.query_chars`, has no
    `construction_unrecorded`, and records BOTH arms' ranked lists, so
    `identity_measured` is TRUE for the same blind arm the legacy control reports
    as False.
    """
    same = {"item-000000": ["a"], "item-000001": ["b"]}
    art, _ = _build_fake_artifact(point_off=same, point_on=same,
                                  object_off=same, object_on=same)
    # ⛔ the SAME helper the three committed artifacts go through — this is the
    # assertion that reds on a correct re-generation if the doc pins ever spell
    # the legacy shape out as a requirement again (`#13267` F2/F5)
    _assert_the_evidence_shape_the_brief_leans_on(art)
    # the pre-fix pins, verbatim in shape, against the tool's own output: each one
    # is what made a regeneration red, and each is REVERSED here so the defect
    # cannot return quietly
    assert "query_chars" in art["construction"]           # pin used `not in`
    assert "construction_unrecorded" not in art           # pin indexed it
    assert art["arms"]["object"]["identity_measured"] is True   # pin said `is False`
    assert art["arms"]["object"]["ranked_lists_compared"] == 2
    assert art["arms"]["object"]["expansion_identical_items"] == 2


def test_the_briefs_call_site_citation_resolves_at_the_merge_target():
    """⛔ `tortoise/sdk.py:44955` / `:45453` were stale at the merge target.

    This PR does not touch `tortoise/`, so the lines the brief pinned drifted out
    from under it: both numbers landed in unrelated code, on the one claim the
    headline rests on, and no pin could see it (the doc pins cover the brief's
    FIGURES, never its `file:line`s — `#13267` F3). A citation by predicate and
    symbol cannot re-stale; this pins both halves of it.
    """
    sdk = (Path(__file__).resolve().parent.parent / "tortoise" / "sdk.py").read_text()
    predicate = 'entity_key_expansion and entity_type == "point"'
    assert sdk.count(predicate) >= 2, (
        "the object arm is gated out only if BOTH halves sit behind the predicate")
    # ⛔ counting occurrences is not enough — the predicate could appear twice and
    # arm nothing. The structural claim needs ONE gate that hands the query to
    # BOTH halves, so the window after the gate is checked for both call sites.
    lines = sdk.splitlines()
    armed = [i for i, ln in enumerate(lines) if predicate in ln
             and "_entity_key_expansion_pass" in "\n".join(lines[i:i + 40])
             and "_entity_key_dense_pass" in "\n".join(lines[i:i + 40])]
    assert armed, (
        "no single gate both arms the sparse half and the dense half — the "
        "object arm being gated out needs re-deriving before the brief says so")
    assert "def tortoise_fts_query(" in sdk, (
        "the predicate lives in TortoiseSDK.tortoise_fts_query — cite a symbol "
        "that resolves, not `sdk.SDK.search_graph`")
    brief = (_DOCS / "research-brief.md").read_text()
    assert "TortoiseSDK.tortoise_fts_query" in brief
    assert predicate in brief
    for stale in ("sdk.py:44955", ":45453"):
        assert stale not in brief, (
            f"{stale} pinned a LINE, and the line moved — cite the predicate")


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
    # ⛔ A refusal used to be ACCEPTED here (`code in (0, 3)`), which made the
    # only live assertions in this file unable to fail on a refused run. The one
    # class that may legitimately refuse on a loaded box is window-mode
    # contamination (an embedder present and a 30-wide window losing the dense
    # leg); anything else means the run did not measure what this test claims.
    mode_only = bool(art["refusals"]) and all(
        "MIXED window modes" in r for r in art["refusals"])
    assert code == 0 or (code == 3 and mode_only), art["refusals"]
    assert art["snapshot"]["digest"]
    assert art["items"]["with_item"] >= 1
    point, obj = art["arms"]["point"], art["arms"]["object"]
    assert point["structurally_blind"] is False
    assert obj["structurally_blind"] is True
    # the object arm's identity is a COMPARISON of two recorded ranked lists, not
    # `None == None` — the count of compared items is the reading that says so
    assert point["ranked_lists_compared"] == art["items"]["with_item"]
    assert obj["ranked_lists_compared"] == art["items"]["with_item"]
    assert obj["expansion_identical_items"] == art["items"]["with_item"]
    assert all(row["off_ids_digest"] and row["on_ids_digest"]
               for row in obj["per_item"].values())
    # the same items on both sides, recorded discriminatingly
    if art["items"]["with_item"] == art["items"]["sampled"]:
        assert art["items"]["name_digest"] == art["population"]["names_sha256"]
    # `id_digest` is a function of the sample SIZE only — pinned so it can never
    # be cited as pairing evidence again
    assert art["items"]["name_digest"] != art["items"]["id_digest"]
    # and the mechanism demonstrably changed at least one point read
    assert point["expansion_fired_items"] >= 1


# ── 2g. the pins review round 4 measured GREEN (`#13267` F1–F6) ─────────────
# ⛔ No count of them: the header said "four" over seven tests and no test can see
# it (`#13267` review 15 F2). A count in a section header is a figure like any other.
#
# Every test below exists because a mutation SURVIVED the suite at this head and
# was proven to by the reviewer, not because the property looked unprotected. The
# measured survivors: the re-freeze refusal deleted, `repeat_identical` flipped,
# the index row's lift re-staled to +9.99 pp, the brief's snapshot digest and its
# two load traces re-staled, the manifest tampered, and `tortoise/sdk.py` added to
# the touched-path cell. Each is now RED; each test names the mutant it kills.

def test_a_re_freeze_over_a_tampered_snapshot_is_refused(tmp_path):
    """F1 — the `--freeze` refusal, which had no pin at all.

    `test_a_tampered_snapshot_is_refused_never_renamed` covers `--snapshot`
    (`verify_snapshot`) only, and the brief's §1/§5 MET row leaned on it for BOTH
    paths. Measured by the reviewer: deleting the digest comparison inside
    `freeze_snapshot` (`if current["digest"] != recorded["digest"]:` → `if
    False:`) left `37 passed, 1 skipped`, and the mutant then returned the
    RECORDED manifest for a snapshot whose bytes no longer matched it — a run
    that attests `snapshot.digest` over a corpus it never verified.
    """
    src = tmp_path / "live"
    _write_corpus(src, names=["the road bike repairs"])
    dst = tmp_path / "snap"
    recorded = P.freeze_snapshot(src, dst)
    victim = dst / "2026-01-01.jsonl"
    victim.write_text(victim.read_text() + '{"session_id":"late"}\n')
    before = (dst / "MANIFEST.json").read_text()
    with pytest.raises(P.Refused, match="no longer matches its own manifest"):
        P.freeze_snapshot(src, dst)
    # the failed call recopies NOTHING and rewrites no manifest — a re-freeze
    # that "succeeds" turns a modified snapshot into fresh evidence
    assert (dst / "MANIFEST.json").read_text() == before, (
        "the refused re-freeze rewrote MANIFEST.json — it must leave the "
        "evidence exactly as it found it")
    assert victim.read_text().endswith('{"session_id":"late"}\n'), (
        "the refused re-freeze re-copied the source over the tampered file")
    assert recorded["digest"] != P._build_manifest(dst, source=str(dst))["digest"]


def test_the_determinism_check_compares_the_read_not_the_target_rank(
        _no_embedder_probe):
    """F2 — `repeat_identical` was `None == None` on every point-arm item.

    It compared the target's RANK, and the target is selected to be lexically
    unreachable, so on the point arms that rank is null in BOTH reads: the check
    was a constant that no retrieval behaviour could move, published in the brief
    as "the repeat returned identical outcomes for all 300 items". Two mutants
    died here:
      * a re-ORDERED repeat window (rank unchanged, read changed) must be
        `identical: False` — the old basis returned `True`;
      * a repeat with no recorded ranked list must be UNMEASURED, never `True`.
    """
    off = {"item-000000": ["a", "b"], "item-000001": ["c", "d"]}
    reordered = {"item-000000": ["b", "a"], "item-000001": ["d", "c"]}
    same = {"item-000000": ["a", "b"], "item-000001": ["c", "d"]}
    on = {"item-000000": ["a", "b", "z"], "item-000001": ["c", "d"]}
    ctrl_off = {"item-000000": ["a"], "item-000001": ["c"]}
    ctrl_on = {"item-000000": ["a"], "item-000001": ["c"]}

    def _point(repeat_ids):
        art, _ = _build_fake_artifact(
            point_off=off, point_on=on, object_off=ctrl_off, object_on=ctrl_on,
            repeat_ids=repeat_ids)
        return art["arms"]["point"]

    # the target rank is null on BOTH sides of every pair, exactly as in the
    # committed point artifacts — so a rank-based check cannot see the reorder
    reordered_arm = _point(reordered)
    assert reordered_arm["repeat_check"]["basis"] == P.REPEAT_BASIS_DIGEST
    assert reordered_arm["repeat_check"]["compared"] == len(off)
    assert reordered_arm["repeat_identical"] is False, (
        "a re-ordered repeat window is a DIFFERENT read; the retired "
        "target-rank basis reported it identical because both ranks were null")
    assert _point(same)["repeat_identical"] is True

    # no recorded ranked list ⇒ no determinism result
    unmeasured = _point({"item-000000": None, "item-000001": None})
    assert unmeasured["repeat_identical"] is None
    assert unmeasured["repeat_check"]["compared"] == 0
    assert "UNMEASURED" in unmeasured["repeat_check"]["note"]

    # no repeat block at all ⇒ the same, never a vacuous True
    art, _ = _build_fake_artifact(
        point_off=off, point_on=on, object_off=ctrl_off, object_on=ctrl_on)
    assert art["arms"]["point"]["repeat_identical"] is None
    assert art["arms"]["point"]["repeat_check"]["compared"] == 0

    # and the brief must not describe the legacy field as a determinism result
    brief = (_DOCS / "research-brief.md").read_text()
    assert "returned the **identical** outcome for every item" not in brief
    assert "repeat of the OFF side returned identical outcomes" not in brief
    for name in _ARTIFACTS:
        for arm, block in _artifact(name)["arms"].items():
            assert block["repeat_check"]["basis"] == P.REPEAT_BASIS_LEGACY, (
                f"{name}/{arm} is one of the committed (legacy) runs")
            assert block["repeat_identical"] is None
    assert "`repeat_identical: null`" in brief or \
           "repeat_identical: null" in brief, (
        "the brief must say the committed artifacts publish no determinism "
        "result, so a reader cannot take the retired `true` from them")


def test_no_k_below_its_own_mde_is_published_as_a_winner():
    """F3 — the MDE rule was applied to k=3 and not to k=5.

    Measured by the reviewer: at k = 5 the artifact's `mde_paired` is 0.022785
    (2.28 pp) against a delta of +2.00 pp — BELOW it — while the brief bolded
    `p = 0.0313` as significant. With `c = 0` the MDE rule is the same monotone
    threshold as `p > 0.05`, so applying it selectively adds no constraint and
    reads as a rule that only fires where it agrees. The convention is therefore
    mechanical and asserted here: **the p cell is bold exactly when the row's
    delta clears that row's own MDE** — the two columns of the same table.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    head = _artifact("paired-point-300.json")
    header, rows = _table(brief, "OFF hits", "McNemar")
    p_col = _col(header, "McNemar")
    mde_col = _col(header, "MDE")
    n = head["population"]["sample"]
    assert len(rows) == len(head["arms"]["point"]["per_k"])
    claimed = []
    for row in rows:
        k = int(_plain(row[0]))
        cell = head["arms"]["point"]["per_k"][f"k={k}"]
        delta_pp = (cell["on_hits"] - cell["off_hits"]) / n * 100
        mde_pp = cell["mde_paired"] * 100
        bold = "**" in row[p_col]
        # the MDE cell the rule is read against must be the artifact's own number:
        # the column was collected and never used, so it could re-stale freely
        _assert_prints(row[mde_col], mde_pp, f"brief §4 k={k}: the MDE column")
        resolves = delta_pp >= mde_pp - 1e-9
        assert bold is resolves, (
            f"brief §4 k={k}: the p-value is {'bold' if bold else 'plain'} but "
            f"the delta is {delta_pp:.2f} pp against an MDE of {mde_pp:.2f} pp — "
            "a row below its own MDE is an observation the design could not "
            "resolve, and must not be printed as a win")
        if bold:
            claimed.append(k)
    # the two rows that DO clear their MDE, and no others
    assert claimed == [10, 15], f"the brief claims wins at k={claimed}"


def test_the_snapshot_digest_and_every_load_trace_are_pinned_where_quoted():
    """F5 — the digest R2 hangs on was quoted three times and pinned nowhere.

    Measured by the reviewer, all GREEN: `docs/00_index.md`'s digest `…b3` →
    `…b4`, the brief's digest `…b3` → `…b4` at two sites, the manifest's aggregate
    digest tampered with its rows kept, a per-file sha256 tampered with the
    aggregate kept, and ONE of the brief's two load traces re-staled. The last one
    is the sharpest: the pin asserted `f"{load_1m:.2f}" in brief`, a presence
    check, so mutating a single site left the brief publishing `13.10 → 12.55` in
    §3 and `13.09 → 12.55` in §5 — two disagreeing figures in one document.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    index = (Path(__file__).resolve().parent.parent / "docs" / "00_index.md").read_text()
    head = _artifact("paired-point-300.json")
    manifest = json.loads((_DOCS / "corpus-snapshot-manifest.json").read_text())

    # the manifest is self-consistent AND is the one every arm read
    assert P.snapshot_digest(manifest) == manifest["digest"], (
        "corpus-snapshot-manifest.json's aggregate digest does not recompute "
        "from its own per-file rows")
    assert manifest["digest"] == head["snapshot"]["digest"]
    for name in _ARTIFACTS:
        assert _artifact(name)["snapshot"]["digest"] == manifest["digest"], (
            f"{name} attests a corpus the committed manifest does not describe")

    digest = manifest["digest"]
    # ⛔ EVERY place the digest is quoted must be the artifact's — a plain
    # `digest[:16] in brief` is vacuous here, because the brief quotes the short
    # prefix at §3 and the FULL digest at §5, so re-staling one site left the
    # other satisfying the assertion (measured: GREEN).
    quoted = re.findall(r"18ab[0-9a-f]*", brief)
    assert quoted, "the brief must quote the artifact's own digest"
    for fragment in quoted:
        assert digest.startswith(fragment), (
            f"the brief quotes the digest fragment {fragment!r}, which is not a "
            f"prefix of the artifact's {digest} — one site has been re-staled")
    assert brief.count(digest[:16]) >= 2, (
        "the digest is quoted at two sites (§3 as a prefix, §5 in full)")
    assert digest[:16] in index, "the index row must quote the same digest"
    # ⛔ EVERY site, not a list of shapes. A shape list kept in step with the prose
    # is the same defect one layer out: `f"{n} files" in brief` let a single site
    # re-stale (measured GREEN at `3ae4980b1d` at `:92` and `:138`), and the first
    # fix — two derived shapes compared by presence — still MISSED the §5 MET row,
    # where the bold sits inside the string (`**23 files**, 14,225 lines, …`), so
    # neither shape matched (measured GREEN: `**23 files** → **14 files**` and
    # `78,571,361 bytes → 99,999,999`). So: sweep every `<N> files … <M> lines`
    # pair and every `<N> files, <M> lines, <B> bytes` triple in the bold/backtick-
    # flattened text and require each to be the manifest's own numbers.
    flat = brief.replace("**", "").replace("`", "")
    want = (manifest["file_count"], manifest["total_lines"], manifest["total_bytes"])
    pair_form = f"{manifest['file_count']} files / {manifest['total_lines']:,} lines"
    trio_form = (f"{manifest['file_count']} files, {manifest['total_lines']:,} lines, "
                 f"{manifest['total_bytes']:,} bytes")
    # ⛔ PRESENCE and TRUTH are two questions, and the first fix answered only one.
    # The site counts below keep a DROPPED site from reading as a shorter document
    # (a `pairs`/`triples` sweep alone is satisfied by the other sites); the sweep
    # below keeps a RE-STALED site from hiding behind a correct neighbour. Both.
    assert flat.count(pair_form) == 1, (
        f"the pair-only site {pair_form!r} is printed once (§1); found "
        f"{flat.count(pair_form)}")
    assert flat.count(trio_form) == 2, (
        f"the trio {trio_form!r} is printed at TWO sites (§3 and the §5 falsifier); "
        f"found {flat.count(trio_form)}")

    def _num(text: str) -> int:
        return int(text.replace(",", ""))

    pairs = re.findall(r"(\d[\d,]*) files[^\n]{0,40}?(\d[\d,]*) lines", flat)
    assert pairs, "the brief prints the corpus file/line pair"
    for shown_files, shown_lines in pairs:
        assert (_num(shown_files), _num(shown_lines)) == want[:2], (
            f"a site prints {shown_files} files / {shown_lines} lines but the "
            f"manifest holds {want[0]} files / {want[1]:,} lines")
    triples = re.findall(
        r"(\d[\d,]*) files, (\d[\d,]*) lines, (\d[\d,]*) bytes", flat)
    assert len(triples) >= 2, (
        "the digest/files/lines/bytes trio is printed at TWO sites at least (§3 "
        f"and the §5 falsifier); found {len(triples)}")
    # ⛔ a `files` figure with NO companion on the line is invisible to the sweep
    # above (measured GREEN at `64504c6d20`: `23 files at the measurement → 14`),
    # and it is the paragraph whose whole point is that the corpus drifted, so at
    # `14` the sentence stops being a drift at all. Pinned as the one literal it is
    # — a blanket `(\d[\d,]*) files` sweep would false-red on §0's deliberate
    # quotation of `#13118`'s own `14 files`.
    assert (f"drifted ({manifest['file_count']} files at the measurement" in brief), (
        "the §7 reproducibility parenthetical must name the manifest's own file count")
    for shown_files, shown_lines, shown_bytes in triples:
        assert (_num(shown_files), _num(shown_lines), _num(shown_bytes)) == want, (
            f"a site prints {shown_files} files, {shown_lines} lines, "
            f"{shown_bytes} bytes but the manifest holds "
            f"{want[0]} / {want[1]:,} / {want[2]:,}")

    # ⛔ EVERY load trace the brief prints must be one an artifact recorded: a
    # presence check let one of the two headline sites re-stale. The object
    # control legitimately quotes its OWN pair, so the allowed set is every
    # artifact's `environment` block — a figure in no artifact is still RED.
    allowed = set()
    for name in _ARTIFACTS:
        env = _artifact(name)["environment"]
        allowed.add((f"{env['load_start']['load_1m']:.2f}",
                     f"{env['load_end']['load_1m']:.2f}"))
    traces = re.findall(r"(\d+\.\d+)\s*→\s*(\d+\.\d+)", brief)
    assert traces, "the brief quotes the load trace"
    for shown in traces:
        assert shown in allowed, (
            f"the brief prints the load trace {shown[0]} → {shown[1]}, which is in "
            f"NO committed artifact — the committed pairs are {sorted(allowed)}")
    want = (f"{head['environment']['load_start']['load_1m']:.2f}",
            f"{head['environment']['load_end']['load_1m']:.2f}")
    assert brief.count(want[0]) >= 2, (
        "the headline trace is quoted at two sites (§3 and §5); a single "
        "occurrence means one of them has been re-staled or dropped")

    # the item class is argued from this median, and nothing compared it
    overlap = head["items"]["median_target_overlap_with_query"]
    assert f"median query-token overlap **{overlap:.1f}**" in brief, (
        f"the brief argues the item class from a median query overlap of "
        f"{overlap} but that sentence no longer prints it — a bare "
        f"{overlap:.1f} appears ten times in this document, so its presence is "
        "not evidence")


def test_the_prose_restatements_of_pinned_figures_match_their_tables():
    """`#13267` review 7: the PROSE restatements of pinned figures were free.

    Measured GREEN on the whole suite, each a single substitution: `17 of 300` →
    `18 of 300`, `5 of 300 items (1.7 %)` → `6 of 300 (1.9 %)`, `+5.67 pp @15` →
    `+5.77 pp`, and `5 of 300 items whose seed moved` → `6 of 300`. Those same
    numbers ARE pinned in the §4/§4.1 tables and in the `docs/00_index.md` row, so
    the prose could contradict its own tables while the suite stayed green — the
    `#13267` class exactly. Derived here the way the tables are.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    head = _artifact("paired-point-300.json")["arms"]["point"]["per_k"]
    short = _artifact("paired-point-300-short-query.json")["arms"]["point"]["per_k"]
    h15, s15 = head["k=15"], short["k=15"]

    # §4 "At k = 15 it recovers 17 of 300 targets"
    m = re.search(r"it recovers (\d+) of\s+(\d+) targets", brief)
    assert m, "the §4 reading bullet states how many targets k=15 recovers"
    assert (int(m.group(1)), int(m.group(2))) == (h15["on_hits"], h15["n"])

    # §4.1 + §7.2: the moved count is lost-from-window PLUS demoted-but-kept
    moved = s15["seed_effect"]["seed_lost_from_window"] \
        + s15["seed_effect"]["seed_demoted_but_kept"]
    m = re.search(
        r"(\d+) of (\d+) items \(([\d.]+) %\) had their own best match moved",
        brief)
    assert m, "§4.1 states the moved count and its rate"
    assert (int(m.group(1)), int(m.group(2))) == (moved, s15["n"]), (
        "§4.1's moved count must be seed_lost_from_window + seed_demoted_but_kept")
    assert m.group(3) == f"{moved / s15['n'] * 100:.1f}", (
        "§4.1's percentage must be the same count over n, not an independent figure")
    m = re.search(r"(\d+) of (\d+) items whose seed moved", brief)
    assert m, "§7.2 restates the moved count against the short-query arm"
    assert (int(m.group(1)), int(m.group(2))) == (moved, s15["n"])

    # §7.2 "+5.67 pp @15" — the headline benefit, to the printed precision
    delta = (h15["on_hits"] - h15["off_hits"]) / h15["n"] * 100
    assert f"+{delta:.2f} pp @15" in brief, (
        f"§7.2 must quote the headline @15 delta as +{delta:.2f} pp")


def test_every_published_sample_size_and_load_delta_recomputes():
    """`#13267` review 5: the sample sizes and load DELTAS were quoted but unpinned.

    Mutation-verified by the reviewer at 680c469e83a7: `7.82 pp` → `7.92 pp`, and
    the 5 pp / 3 pp per-arm sizes → `~755`/`~2,100`, each left the suite GREEN, and
    so did `(Δ −0.54)` → `(Δ −0.44)` at BOTH sites. The suite pinned the TOOL's
    `independent_effect` and its own re-derivation inside a wide band, but never
    re-read the digits the brief prints — the same class as the digest gap F5
    closed one paragraph over.

    It also settles a rounding the brief had wrong for three review rounds: the
    tool's `independent_effect(300, 0.865)` is 0.07816874616…, so
    300·(mde/0.05)² = 733.24 → **733**, not 734.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    mde = P.independent_effect(300, 0.865)
    assert f"{mde * 100:.2f} pp" in brief, (
        f"the brief must print the independent-design MDE as {mde * 100:.2f} pp")
    sizes = {}
    for delta in (0.05, 0.03):
        n = round(300 * (mde / delta) ** 2)
        sizes[delta] = n
        forms = (f"~{n} per arm", f"~{n}/arm", f"~{n:,} per arm", f"~{n:,}/arm")
        assert any(f in brief for f in forms), (
            f"the brief must print the {delta * 100:.0f} pp per-arm size as ~{n} "
            "derived as 300·(mde/delta)²")
    assert (sizes[0.05], sizes[0.03]) == (733, 2037), sizes
    # both sites (§0 and §5) must carry each figure — one site is a drop/re-stale
    assert brief.count(f"~{sizes[0.05]}") == 2, (
        "the 5 pp per-arm size is quoted at two sites (§0 and §5)")
    assert brief.count(f"~{sizes[0.03]:,}") == 2, (
        "the 3 pp per-arm size is quoted at two sites (§0 and §5)")

    # ⛔ every load DELTA printed must be the artifact ITS OWN site quotes, not
    # merely SOME artifact's. A set-membership check let `Δ −0.54` become
    # `Δ −0.01` at BOTH headline sites and stayed GREEN — the brief then printed
    # `load 13.09 → 12.55 (Δ −0.01)`, arithmetic that cannot add up — and let one
    # site borrow the short-query arm's −0.98.
    recorded = {f"{abs(_artifact(n)['environment']['load_delta_1m']):.2f}"
                for n in _ARTIFACTS}
    shown = re.findall(r"Δ\s*[−-]\s*(\d+\.\d+)", brief)
    assert shown, "the brief prints at least one load delta"
    for d in shown:
        assert d in recorded, (
            f"the brief prints a load delta of -{d}, which NO committed artifact "
            f"recorded — the recorded deltas are {sorted(recorded)}")
    ctrl = _artifact("paired-object-60-control.json")
    head = _artifact("paired-point-300.json")
    head_delta = f"{abs(head['environment']['load_delta_1m']):.2f}"
    ctrl_delta = f"{abs(ctrl['environment']['load_delta_1m']):.2f}"
    assert shown.count(head_delta) == 2, (
        f"the headline delta -{head_delta} belongs to BOTH sites that quote the "
        f"headline trace (§3 and §5); found {shown.count(head_delta)}")
    assert shown.count(ctrl_delta) == 1, (
        f"the control's delta -{ctrl_delta} belongs to its ONE site; found "
        f"{shown.count(ctrl_delta)}")


def test_the_touched_path_cell_names_every_top_level_path_the_diff_carries():
    """F6 — the cell claimed a set pin that did not exist.

    The R3 falsifier publishes the touched-path set and ended "the pin in
    `tests/test_paired_expansion_ab_2518.py` keeps this cell to the path set".
    The pin's last assertion was `"git diff --name-only origin/main...HEAD" in
    brief` — it required the cell to NAME the command and banned the tally
    vocabulary; it never compared the set to the tree, and the reviewer measured
    `tortoise/sdk.py` added to the cell as GREEN.

    The fix is both halves: the sentence no longer claims a pin that does not
    exist, and a real set-level check runs wherever `origin/main` resolves. It is
    skipped on the suite job's shallow checkout (where `origin/main` is
    unresolvable and such a pin would SKIP anyway — the residual the previous
    lane recorded rather than shipping a vacuous guard), so the brief says the set
    is re-read by a reviewer at each head.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    assert "keeps this cell to the path set" not in brief, (
        "the cell must not claim a set-level pin — that sentence was `#13267` "
        "review 4 F6, and the set is re-read by a reviewer, not by the suite")
    # the cell must still say where the set IS checked, or the reversal is a loss
    # of information rather than a correction
    assert "re-read by a reviewer at each head" in brief, (
        "dropping the false pin claim must keep the honest one: the path set is "
        "re-read by a reviewer at each head")

    repo = Path(__file__).resolve().parent.parent
    try:
        base = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "origin/main"],
            capture_output=True, text=True, check=True).stdout.strip()
        diff = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only",
             f"{base}...HEAD"],
            capture_output=True, text=True, check=True).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("origin/main does not resolve here (shallow checkout) — the "
                    "path set is re-read by a reviewer at each head")
    if not diff:
        pytest.skip("origin/main == HEAD in this tree — nothing to compare")

    assert not [p for p in diff if p.startswith("tortoise/")], (
        "the R3 claim is 'no retrieval behaviour changed', and the diff reaches "
        f"the product: {[p for p in diff if p.startswith('tortoise/')]}")
    # ⛔ the cell's ENUMERATION is part of the record, both ways: a path it names
    # that the diff does not carry is a re-staled cell (the reviewer's mutant,
    # `tortoise/sdk.py` added to the cell, was GREEN), and a diff path it omits is
    # an incomplete set. Only the `touches exactly these paths:` span is an
    # enumeration — the `No change to \`tortoise/\`` clause after it is a
    # NEGATIVE and is deliberately not read as a member.
    marker = "touches exactly these paths:"
    at = brief.find(marker)
    assert at >= 0, "the cell must enumerate the paths it publishes"
    span = brief[at + len(marker):].split(". No change to")[0]
    tokens = set(re.findall(r"`([^`]+)`", span))
    assert tokens, f"no path tokens in the enumerated span: {span[:200]!r}"
    dirs = {t for t in tokens if t.endswith("/")}
    named = tokens - dirs
    extra = named - set(diff)
    assert not extra, (
        f"the cell names paths this diff does not carry: {sorted(extra)} — a "
        "path set that over-claims is as stale as one that under-claims")
    covered = set(named)
    for directory in dirs:
        covered |= {p for p in diff if p.startswith(directory)}
    missing = set(diff) - covered
    assert not missing, (
        "the cell does not name these touched paths, so its set is incomplete: "
        f"{sorted(missing)}")
    assert dirs and "docs/research/2026-11-17-2518-paired-ab/" in dirs, (
        "the cell must name the directory it summarises")


# ── 2h. the pins review round 5 measured GREEN (`#13267` F1–F6) ──────────────
#
# Same shape as 2g: each test names the mutant the reviewer PROVED survived at
# the head it reviewed, and the fix that now reds it.

# ⛔ `#13267` review 6 F4: this set was the FOUR names the first fix happened to
# classify, and the pin asserted `declared | unpinned == _DERIVED` — exhaustive
# over a hand-written constant, not over the artifact's derived figures.
# `expansion_identical_items` and `items_whose_read_grew` were derived from the
# same per-item reads and were in neither list, so `expansion_identical_items:
# 7 -> 0` together with `items_whose_read_grew: 0 -> 300` stayed green while the
# artifact contradicted its own `expansion_fired_items: 293`.
_DERIVED = {"off_topk_displacement", "seed_effect", "expansion_fired_items",
            "expansion_identical_items", "items_whose_read_grew",
            "ranked_lists_compared"}
#: ⛔ these two are emitted on the POINT arm only (`#13267` review 7 F6): the
#: object arm has no seed and no point-top-k, so declaring them there promises a
#: field a reader will not find — and the presence-free recompute silently
#: `continue`d every cell.
_POINT_ONLY_DERIVED = {"off_topk_displacement", "seed_effect"}


def _assert_derived_figures_recompute(art: dict, where: str) -> None:
    """Every derived figure the artifact DECLARES re-derivable must recompute.

    ⛔ `#13267` review 5 F1: `seed_effect`, `expansion_fired_items` and
    `ranked_lists_compared` were free parameters — held only to "the brief
    repeats the artifact", so a CONSISTENT re-staling of both stayed green
    (measured: `seed_lost_from_window: 3 → 0` and `expansion_fired_items: 293 → 1`
    each in the artifact AND the brief, 46 passed). A figure that cannot be
    recomputed must be DECLARED uncomputable, never left looking pinned.
    """
    for arm, block in art["arms"].items():
        declared = set(block["derived_recomputable"])
        unpinned = set(block["derived_unpinned"])
        assert declared <= _DERIVED and unpinned <= _DERIVED, (
            f"{where}/{arm}: unknown derived field name")
        assert not (declared & unpinned), (
            f"{where}/{arm}: {sorted(declared & unpinned)} is declared both "
            "recomputable and unpinned")
        expected = _DERIVED if arm == "point" else _DERIVED - _POINT_ONLY_DERIVED
        assert declared | unpinned == expected, (
            f"{where}/{arm}: every derived figure that arm emits must be "
            f"classified; missing {sorted(expected - declared - unpinned)}, "
            f"unexpected {sorted((declared | unpinned) - expected)}")
        if unpinned:
            assert "NOT RE-DERIVABLE" in (block.get("derived_note") or ""), (
                f"{where}/{arm}: an unpinned derived figure with no note saying so")

        # ⛔ `#13267` review 6 F4: fired + identical must be the compared reads, on
        # EVERY artifact generation — a derived count that contradicts the
        # artifact's own sibling counts is a re-stale no recomputation path is
        # needed to see (measured GREEN without this: `expansion_identical_items`
        # 7 -> 0 beside `expansion_fired_items: 293` and `ranked_lists_compared:
        # 300`, i.e. 293 + 0 != 300).
        fired_n = block.get("expansion_fired_items")
        ident_n = block.get("expansion_identical_items")
        compared_n = block.get("ranked_lists_compared")
        if None not in (fired_n, ident_n, compared_n):
            assert fired_n + ident_n == compared_n, (
                f"{where}/{arm}: fired {fired_n} + identical {ident_n} != "
                f"{compared_n} compared reads — a derived count has been "
                "re-staled against its siblings")

        per_item = block.get("per_item") or {}
        item_ids = sorted(per_item)
        if "seed_effect" in declared:
            assert item_ids, f"{where}/{arm}: declared seed_effect with no rows"
            off = {i: {"seed_rank": r["off_seed_rank"]} for i, r in per_item.items()}
            on = {i: {"seed_rank": r["on_seed_rank"]} for i, r in per_item.items()}
            for key, cell in block["per_k"].items():
                k = int(key.split("=")[1])
                # ⛔ a DECLARED figure must be PRESENT: skipping a deleted one is
                # how `#13267` review 7 F6's mutant survived
                assert "seed_effect" in cell, (
                    f"{where}/{arm}/{key}: `seed_effect` is declared recomputable "
                    "but the cell does not carry it — a deleted figure is not a "
                    "recomputable one")
                assert P._seed_effect(off, on, item_ids, k=k) == cell["seed_effect"], (
                    f"{where}/{arm}/{key}: the recorded seed_effect does not "
                    "recompute from the artifact's own per-item seed ranks")
        if "expansion_fired_items" in declared:
            assert item_ids, f"{where}/{arm}: declared firing with no rows"
            fired = sum(1 for r in per_item.values()
                        if r["off_ids_digest"] != r["on_ids_digest"])
            both = sum(1 for r in per_item.values()
                       if r["off_ids_digest"] and r["on_ids_digest"])
            assert fired == block["expansion_fired_items"], (
                f"{where}/{arm}: recorded {block['expansion_fired_items']} fired "
                f"items but the per-item ranked-list digests differ on {fired}")
            assert both == block["ranked_lists_compared"], (
                f"{where}/{arm}: recorded ranked_lists_compared="
                f"{block['ranked_lists_compared']} but {both} rows carry both "
                "sides' digests")
            assert block["expansion_identical_items"] == both - fired, (
                f"{where}/{arm}: recorded expansion_identical_items="
                f"{block['expansion_identical_items']} but {both} compared reads "
                f"leave {both - fired} identical — and a fired + identical split "
                "that does not sum to the compared reads is a re-staled count")
        # ⛔ the classification must be exhaustive over the ARTIFACT'S derived
        # fields, not over `_DERIVED` alone: a field nobody listed was invisible.
        present = (_DERIVED | set(declared) | set(unpinned)) & set(block)
        unclassified = {k for k in block
                        if k.startswith(("expansion_", "items_whose_", "ranked_",
                                         "off_topk_", "seed_"))
                        and k not in declared and k not in unpinned
                        and not k.endswith(("_note", "_entries", "_provenance"))}
        assert not unclassified, (
            f"{where}/{arm}: derived field(s) {sorted(unclassified)} are in the "
            "artifact but classified as neither recomputable nor unpinned")
        del present


def test_the_committed_artifacts_declare_which_derived_figures_they_pin():
    """The three published runs record their own unpinned set, and say so."""
    for name in _ARTIFACTS:
        art = _artifact(name)
        _assert_derived_figures_recompute(art, name)
        for arm, block in art["arms"].items():
            assert block["derived_recomputable"] == [], (
                f"{name}/{arm}: these runs recorded no per-item seed rank or id "
                "digest, so NOTHING may be declared recomputable")
            # the point runs emit both point-only figures; the control (an
            # OBJECT arm) emits neither, and must not claim to
            if arm == "point":
                assert "seed_effect" in block["derived_unpinned"]
                assert "off_topk_displacement" in block["derived_unpinned"]
            else:
                assert "seed_effect" not in block["derived_unpinned"]
                assert "off_topk_displacement" not in block["derived_unpinned"]


def test_a_tool_produced_artifact_recomputes_its_derived_figures(
        _no_embedder_probe):
    """The other half: where the datum EXISTS, the pin must actually bite.

    A new run records `per_item.off_seed_rank` / `on_seed_rank`, so the tool
    declares `seed_effect` recomputable — and a re-staled count now reds instead
    of surviving. Mutating the built artifact stands in for that re-stale.
    """
    off = {"item-000000": ["a", "b"], "item-000001": ["c", "d"]}
    on = {"item-000000": ["b", "a", "z"], "item-000001": ["c", "d"]}
    art, _ = _build_fake_artifact(
        point_off=off, point_on=on, object_off=off, object_on=off)
    block = art["arms"]["point"]
    assert set(block["derived_recomputable"]) == _DERIVED - {
        "off_topk_displacement", "items_whose_read_grew"}
    assert block["derived_unpinned"] == ["items_whose_read_grew",
                                         "off_topk_displacement"]
    _assert_derived_figures_recompute(art, "tool-produced")

    # ⛔ the mutant the reviewer measured GREEN at the reviewed head
    for cell in block["per_k"].values():
        cell["seed_effect"]["seed_lost_from_window"] += 1
        cell["seed_effect"]["seed_at_top1_off"] += 1
    block["expansion_fired_items"] = 1
    with pytest.raises(AssertionError):
        _assert_derived_figures_recompute(art, "mutant")

    # ⛔ `#13267` review 8: `_has_seed_ranks` was a key-PRESENCE test, so reverting
    # it declared `seed_effect` recomputable over a figure that is a CONSTANT ZERO
    # (keys present, every rank `None`) and the whole suite stayed GREEN. The datum
    # is a real rank, not a key.
    null_ranks, _ = _build_fake_artifact(
        point_off=off, point_on=on, object_off=off, object_on=off,
        arms={"point": {"off": _fake_arm(off, seed_ranks={}),
                         "on": _fake_arm(on, seed_ranks={})}})
    pblock = null_ranks["arms"]["point"]
    assert "seed_effect" in pblock["derived_unpinned"], (
        "an arm whose ranks are all `None` must declare `seed_effect` UNPINNED — "
        "a constant zero is not a re-derivable figure")
    assert "seed_effect" not in pblock["derived_recomputable"]


def test_the_refusal_producers_fire_not_only_the_exit_code(
        _no_embedder_probe):
    """F2 — the refusal CLASSES were pinned only over a hand-built list.

    `_exit_code`'s scoping is pinned at `test_..._class_scoping` over a list the
    test itself constructs, and the committed control's `refusal_kinds` pins the
    RECORD. Neither pins the producer, so deleting the `refusals.append(...)` for
    structural blindness — or for a mixed window mode — left the suite green
    (measured: both mutants SURVIVED, 46 passed). With either gone, a run the tool
    promises will exit 3 publishes exit 0.
    """
    same = {"item-000000": ["a"], "item-000001": ["b"]}
    diff = {"item-000000": ["a", "z"], "item-000001": ["b"]}

    # ── structural blindness: an object-only run reads no lever-armed leg
    # (both sides identical, which is what the gated-out leg LOOKS like — the
    # tool infers the structural claim from that identity, so a diverging object
    # arm is by definition NOT structurally blind and this fixture must match)
    blind, _ = _build_fake_artifact(
        point_off=same, point_on=same, object_off=same, object_on=same,
        arms={"object": {"off": _fake_arm(same), "on": _fake_arm(same)}})
    assert blind["arms"]["object"]["structurally_blind"] is True
    assert P.STRUCTURAL_REFUSAL in blind["refusal_kinds"], (
        "an object-only run must REFUSE to publish a verdict — every arm is "
        "blind to the lever")
    assert P._exit_code(list(zip(blind["refusal_kinds"], blind["refusals"], strict=True)),
                        allow_mixed_modes=True) == 3, (
        "`--allow-mixed-modes` must NOT excuse structural blindness")

    # ── a contaminated window: two legs in one arm
    mixed_arm = {"off": _fake_arm(same), "on": _fake_arm(diff)}
    mixed_arm["off"]["window_modes"] = {"keyword_only": 1, "hybrid": 1}
    mixed, _ = _build_fake_artifact(
        point_off=same, point_on=diff, object_off=same, object_on=same,
        arms={"point": mixed_arm,
              "object": {"off": _fake_arm(same), "on": _fake_arm(same)}})
    assert P.MODE_REFUSAL in mixed["refusal_kinds"], (
        "an arm whose two sides ran different legs is contaminated by the LEG, "
        "not the lever, and must refuse")
    assert P._exit_code(list(zip(mixed["refusal_kinds"], mixed["refusals"], strict=True)),
                        allow_mixed_modes=False) == 3
    assert P._exit_code(list(zip(mixed["refusal_kinds"], mixed["refusals"], strict=True)),
                        allow_mixed_modes=True) == 0, (
        "the mode refusal is the ONE class the opt-out is scoped to")


def test_the_instruments_own_front_page_does_not_restate_a_retracted_claim():
    """F3 — the module docstring kept the retracted pre-statement.

    `#13267` review 4 F3 killed "MDE stated BEFORE any verdict" in the function
    docstring and the brief; the MODULE docstring (the instrument's front page)
    still carried it, and no test reads it.
    """
    doc = P.__doc__ or ""
    flat = doc.replace("**", "").replace("``", "")
    assert "BEFORE any verdict" not in doc, (
        "the module docstring restates the retracted pre-statement")
    assert "not pre-stated" in flat, (
        "the front page must say the MDE is reported, not pre-stated")
    assert "minimum detectable effect" in doc


def test_the_control_cites_a_call_site_symbol_that_resolves():
    """F4 — the control's `identity_note` cited `sdk.SDK.search_graph`.

    Measured: `hasattr(tortoise.sdk, "SDK")` is False and `TortoiseSDK` has no
    `search_graph`; the method is `tortoise_fts_query`. The brief's own citation
    WAS pinned to the resolving symbol — the artifact carrying the same claim was
    not.
    """
    from tortoise import sdk as _sdk
    note = _artifact("paired-object-60-control.json")["arms"]["object"]["identity_note"]
    assert "TortoiseSDK.tortoise_fts_query" in note
    assert "sdk.SDK.search_graph" not in note, (
        "that symbol does not exist — the conclusion must cite the call site it "
        "actually leans on")
    assert hasattr(_sdk.TortoiseSDK, "tortoise_fts_query")
    assert not hasattr(_sdk, "SDK")


def test_the_brief_carries_no_duplicated_or_dangling_bullet():
    """F6 — a leftover from the MDE rewrite: the same bullet twice, one dangling."""
    brief = (_DOCS / "research-brief.md").read_text()
    lines = brief.splitlines()
    for i, (a, b) in enumerate(itertools.pairwise(lines), start=1):
        assert a != b or not a.strip(), (
            f"line {i} is duplicated verbatim (and one copy dangles): {a[:120]!r}")


def test_the_seed_effect_producer_matches_a_hand_computed_case():
    """F1, second half — re-deriving with the tool's OWN function is circular.

    `_assert_derived_figures_recompute` pins the artifact against the code: if the
    producer itself changes, the recompute changes with it and stays green
    (measured: `lost += 1` → `if False:` SURVIVED it). So the PRODUCER is pinned
    here against a hand-computed case, the way the statistics are, and the two
    together close both directions.
    """
    off = {"a": {"seed_rank": 0}, "b": {"seed_rank": 5}, "c": {"seed_rank": None},
           "d": {"seed_rank": 0}, "e": {"seed_rank": 3}}
    on = {"a": {"seed_rank": 2}, "b": {"seed_rank": None}, "c": {"seed_rank": None},
          "d": {"seed_rank": 0}, "e": {"seed_rank": 3}}
    # by hand, k = 15:
    #   top1 off: a(0), d(0)                        -> 2
    #   top1 on : d(0)                              -> 1   (a moved to rank 2)
    #   lost    : b (5 < 15, on None)               -> 1
    #   demoted : a (2 > 0, not lost)               -> 1
    #   shift   : a +2, d 0, e 0, b absent          -> 2
    assert P._seed_effect(off, on, sorted(off), k=15) == {
        "seed_at_top1_off": 2, "seed_at_top1_on": 1,
        "seed_lost_from_window": 1, "seed_demoted_but_kept": 1,
        "seed_rank_shift_sum": 2}
    # ⛔ a NARROWER window loses nothing here: at k = 3 `a` sits at rank 2 in both
    # arms (kept), and `b`'s OFF rank 5 was already outside the window, so it was
    # never IN it to be lost — the metric counts displacement, not absence
    assert P._seed_effect(off, on, sorted(off), k=3)["seed_lost_from_window"] == 0
    narrow = {"a": {"seed_rank": 0}, "b": {"seed_rank": 2}}
    assert P._seed_effect(narrow, {"a": {"seed_rank": 9}, "b": {"seed_rank": 2}},
                          ["a", "b"], k=3)["seed_lost_from_window"] == 1


def test_every_pp_and_every_scientific_p_the_brief_prints_is_a_recorded_one():
    """F1 — the brief's PROSE restatements were unpinned, only its tables were.

    `#13267` review 6 F1: four substitutions in prose (not tables) were GREEN at
    the reviewed head — `At k = 15 it recovers 17 of` → `3 of`, `p = 2 × 10⁻⁵` →
    `2 × 10⁻⁹`, `k = 10 (+4.33 against 3.35)` → `(+9.99 …)`, and §7's
    `(+5.67 pp @15, p = 2 × 10⁻⁵)` → `(+9.99 pp @15, p = 2 × 10⁻⁹)` — each while
    the §4 table above it still read `17/300` / `0.00002`. The pins walked tables
    and the `(@k)` tail shape, so prose was read by nothing, and the PR body's
    "reds if the prose re-stales" was false.

    So: EVERY `±X.XX pp` figure and EVERY `p = N × 10⁻ⁿ` tail the brief prints must
    be some artifact's recorded value. This is an allow-list, not a cell map — it
    does not care which sentence carries the figure, only that the figure exists
    in the evidence.
    """
    brief = (_DOCS / "research-brief.md").read_text()

    # ── every delta / MDE in percentage points ────────────────────────────
    allowed_pp = set()
    for name in _ARTIFACTS:
        art = _artifact(name)
        n = art["population"]["sample"]
        for block in art["arms"].values():
            for cell in block["per_k"].values():
                allowed_pp.add(round((cell["on_hits"] - cell["off_hits"]) / n * 100, 2))
                # a CI bound is a pp figure too, and it IS recorded
                for bound in cell["bootstrap"]["ci95"]:
                    allowed_pp.add(round(bound * 100, 2))
                for cell_key in ("mde_paired", "mde_independent"):
                    value = cell.get(cell_key)
                    # ⛔ not estimable is strict-JSON `null` in the artifact (never
                    # a `NaN` token, `#13267` review 12 F1), so it carries no figure
                    # to allow — `mde_note` names it in prose instead.
                    if _estimable(value):
                        allowed_pp.add(round(value * 100, 2))
    # ⛔ §0 is a QUOTATION of the owner's re-measurement comment on `#2518`
    # (6939661408) — 75.0/76.3 % @3 and 88.0/85.0 % @15 — so its two deltas are
    # those subtractions, its SE (2.8 pp) is DERIVED for an independent design at
    # n = 300, p̄ ≈ 0.865 (√(2·p̄·(1−p̄)/n); `#13118`'s body prints no SE, no after-
    # value and no z), and its 7.82 pp is THIS tool's independent-design MDE for
    # that design. They are pinned as quoted, not silently allowed: the four recalls
    # must be in the brief and the two deltas must be their difference.
    quoted = {"before3": 75.0, "after3": 76.3, "before15": 88.0, "after15": 85.0}
    for label, value in quoted.items():
        assert f"{value} %" in brief, f"§0 no longer quotes {label} = {value} %"
    allowed_pp |= {round(quoted["after3"] - quoted["before3"], 2),
                   round(quoted["after15"] - quoted["before15"], 2)}
    allowed_pp.add(round(P.independent_effect(300, 0.865) * 100, 2))
    # ⛔ §0's standard errors are DERIVED (independent design, n = 300; p̄ ≈ 0.865 at
    # @15 — the 88.0/85.0 recalls — and ≈ 0.757 at @3 — 75.0/76.3; the same formula
    # at a single p̄ would print one SE twice, which is the misreading §0 pre-empts),
    # not quoted from any PR, so
    # they are admitted ONLY where they re-derive as delta / z in the SAME sentence
    # — `z = 0.37` beside a single "SE 2.8" was arithmetic that cannot add up, and
    # an invented SE cannot ride in on an allow-list entry (`#13267` review 9 F5).
    quoted_se = set()
    for delta_q, se_q, z_q in re.findall(
            r"delta (?:is )?(\d+\.\d+) pp at SE (\d+\.\d+) pp\s*\(\*\*z = "
            r"(\d+\.\d+)\*\*\)", brief, flags=re.S):
        assert abs(float(delta_q) / float(se_q) - float(z_q)) < 0.02, (
            f"§0 prints z = {z_q} for delta {delta_q} at SE {se_q}: the arithmetic "
            f"gives {float(delta_q) / float(se_q):.2f}")
        quoted_se.add(float(se_q))
    assert quoted_se, "§0 quotes at least one standard error with its delta and z"

    shown_pp = {round(float(v), 2)
                for v in re.findall(r"[+]?(\d+\.\d+)\s*pp\b", brief)}
    assert shown_pp, "the brief prints percentage-point figures"
    # the brief prints magnitudes ("3.0 pp" for the -3.0 delta), so compare on
    # the absolute value — a sign flip is a re-stale of direction, caught above by
    # the §0 arithmetic assertion, not by this allow-list
    strays = {abs(v) for v in shown_pp} - {abs(v) for v in allowed_pp} - quoted_se
    assert not strays, (
        f"the brief prints {sorted(strays)} pp, which no artifact records as a "
        f"delta or an MDE (recorded: {sorted(allowed_pp)})")

    # ── every exact McNemar tail printed in superscript form ──────────────
    allowed_p = set()
    for name in _ARTIFACTS:
        for block in _artifact(name)["arms"].values():
            for cell in block["per_k"].values():
                allowed_p.add(round(cell["mcnemar_exact_p"], 12))
    tails = re.findall(r"([\d.]+)\s*×\s*10⁻([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", brief)
    assert tails, "the brief prints the exact tails in prose"
    sup = "⁰¹²³⁴⁵⁶⁷⁸⁹"
    for mantissa, exponent in tails:
        power = int("".join(str(sup.index(c)) for c in exponent))
        value = float(mantissa) * 10 ** -power
        # the brief ROUNDS for prose (`1.5e-05` -> `2 × 10⁻⁵`), so the honest
        # bound is one unit in the last printed place — the same rule `_printed`
        # uses for table cells. `2 × 10⁻⁹` and `7 × 10⁻⁵` are far outside it.
        tol = 10 ** -power
        assert any(abs(value - a) <= tol for a in allowed_p), (
            f"the brief prints p = {mantissa} × 10⁻{power} ({value!r}), which no "
            f"artifact records within its printed precision (recorded tails: "
            f"{sorted(allowed_p)})")

    # ── the §5 winner-rule cell prints "(delta against MDE)" with NO `pp` suffix,
    # so the allow-list above cannot see it (`#13267` review 6 F1: `+4.33 -> +9.99`
    # stayed green). Each pair must be a recorded (delta, mde) for the SAME k.
    pairs = {(round((c["on_hits"] - c["off_hits"]) / n * 100, 2),
              round(c["mde_paired"] * 100, 2))
             for a in _ARTIFACTS
             for n in [_artifact(a)["population"]["sample"]]
             for blk in _artifact(a)["arms"].values()
             for c in blk["per_k"].values()
             if _estimable(c.get("mde_paired"))}
    for shown_delta, shown_mde in re.findall(
            r"\(([+-]?\d+\.\d+) against (\d+\.\d+)\)", brief):
        assert (round(float(shown_delta.lstrip("+")), 2),
                round(float(shown_mde), 2)) in pairs, (
            f"the brief prints '({shown_delta} against {shown_mde})', which is not "
            f"a recorded (delta, MDE) pair for any k — recorded pairs: "
            f"{sorted(pairs)}")

    # ── and the headline prose figure, in the cell a reader reads it ───────
    head = _artifact("paired-point-300.json")
    n = head["population"]["sample"]
    k15 = head["arms"]["point"]["per_k"]["k=15"]
    assert f"recovers {k15['on_hits']} of" in brief, (
        f"§4's reading bullet must say the artifact's k=15 count "
        f"({k15['on_hits']} of {n})")


def test_a_population_file_drawn_for_another_sample_is_refused(tmp_path):
    """F5 — the loader checked the corpus, never the sample triple.

    `#13267` review 6 F5 measured GREEN at the reviewed head: a
    `population-300.json` (sample 300, seed 20261117) loaded by a run asking for
    `--sample 60 --seed 99` was ACCEPTED, and `construction.sample`/`seed`/
    `population` — written from argv — then described a run that did not happen,
    while the items actually measured came from the other sample. The file is the
    one the tool itself leaves in the snapshot directory, so this is the shape a
    reproduce command hits.
    """
    digest = "d" * 64
    rows = [{"name": f"the name {i}", "query": "q", "target": "t"}
            for i in range(5)]
    body = P.population_body(rows, snapshot_digest_=digest, population=1000,
                             sample=300, seed=20261117)
    path = tmp_path / "population-300.json"
    path.write_text(json.dumps(body, indent=2, sort_keys=True))
    assert P.load_or_make_population(path, harvested={},
                                     snapshot_digest_=digest, population=1000,
                                     sample=300, seed=20261117)[0] == rows
    for field, kwargs in (
            ("sample", dict(population=1000, sample=60, seed=20261117)),
            ("seed", dict(population=1000, sample=300, seed=99)),
            ("population", dict(population=7, sample=300, seed=20261117))):
        with pytest.raises(P.Refused, match=field):
            P.load_or_make_population(path, harvested={}, snapshot_digest_=digest,
                                      **kwargs)
    # the file is left exactly as it was — a refusal never rewrites evidence
    assert json.loads(path.read_text())["sample"] == 300


def test_the_window_modes_and_the_embedder_flag_are_pinned_where_quoted():
    """F1 — the contamination falsifier's entire OUTPUT was unpinned.

    `#13267` review 7 F1: the suite named `window_modes` only in the fake-arm
    builder and in a fixture that CONSTRUCTS a mixed mode; no assertion read a
    committed artifact's value, and nothing read the prose. Six substitutions were
    GREEN — flipping `keyword_only 300/300` to `hybrid` in the artifact, in the
    brief at TWO sites (§3 and the §5 row marked MET), and in the index row, plus
    `environment.embedder: false -> true`. That is the evidence under R1 (uniform
    sparse half) and R3 (the dense leg never ran).
    """
    brief = (_DOCS / "research-brief.md").read_text()
    index = (Path(__file__).resolve().parent.parent / "docs" / "00_index.md").read_text()
    row = next(ln for ln in index.splitlines() if "2026-11-17-2518-paired-ab" in ln)
    for name in _ARTIFACTS:
        art = _artifact(name)
        items = art["items"]["with_item"]
        assert art["environment"]["embedder"] is False, (
            f"{name} claims an embedder was present; this box has none, and the "
            "brief's 'sparse half only' conclusion rests on its absence")
        for arm, block in art["arms"].items():
            assert block["window_modes"] == {"off": {"keyword_only": items},
                                             "on": {"keyword_only": items}}, (
                f"{name}/{arm}: window_modes is {block['window_modes']} — the "
                "uniform `keyword_only` reading is the no-contamination claim")
    # the prose, at every site, off the headline artifact's own numbers
    head = _artifact("paired-point-300.json")
    n = head["items"]["with_item"]
    mode = next(iter(head["arms"]["point"]["window_modes"]["off"]))
    assert f"`{mode}` {n}/{n} in both arms" in brief, (
        "§3 must quote the artifact's own mode and count")
    assert f"`{mode}` {n}/{n} both arms" in row, (
        "the index row must quote the same mode and count")
    # §5's MET cell is a second site of the same figure
    assert brief.count(f"`{mode}` {n}/{n}") >= 2, (
        "the mode is quoted at two sites (§3 and the §5 row marked MET); a single "
        "occurrence means one of them was re-staled or dropped")


def test_the_constructed_graph_counts_are_self_consistent_and_quoted():
    """F3 — the method's graph sizes re-staled green in the brief and the artifact.

    `#13267` review 7 F3: the paragraph is a SELF-CHECK against the artifact
    ("it would read 1,400, against the artifact's `points_total: 1100`"), and both
    the brief's three numbers and the artifact's `construction.graph` could move
    without a red — under the combined mutant the warning nullified itself.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    for name in _ARTIFACTS:
        art = _artifact(name)
        graph = art["construction"]["graph"]
        items = art["items"]["with_item"]
        assert graph["anchors"] == items, (
            f"{name}: `seed_graph` sets anchors = len(items)")
        assert graph["linked_points"] == 2 * items, (
            f"{name}: `seed_graph` sets linked_points = 2 * len(items)")
        assert graph["points_total"] == graph["linked_points"] + graph["distractors"]
    head = _artifact("paired-point-300.json")
    graph = head["construction"]["graph"]
    assert f"{graph['anchors']} anchors" in brief
    assert f"= the {graph['linked_points']} points" in brief
    assert f"plus {graph['distractors']} distractors" in brief
    assert f"= {graph['points_total']:,} points" in brief or \
           f"= {graph['points_total']} points" in brief
    assert f"points_total: {graph['points_total']}" in brief, (
        "the self-check quotes the artifact's own total; if that moved with the "
        "prose the warning nullifies itself")


def test_the_controls_repeat_check_is_pinned_as_the_brief_describes_it():
    """F4 — §8 leans on the control's `compared: 60`; nothing read it.

    `#13267` review 7 F4: `compared 60 -> 0` and `identical true -> false` both
    stayed GREEN, while §8 says the control "records `compared: 60` and a real
    60-of-60 equality … **not** zero items compared".
    """
    brief = (_DOCS / "research-brief.md").read_text()
    ctrl = _artifact("paired-object-60-control.json")
    block = ctrl["arms"]["object"]
    check = block["repeat_check"]
    assert check["basis"] == P.REPEAT_BASIS_LEGACY
    assert check["compared"] == ctrl["items"]["with_item"], (
        f"the control compared {check['compared']} of "
        f"{ctrl['items']['with_item']} items — every one is present in both reads")
    assert check["identical"] is True
    assert f"compared: {check['compared']}" in brief, (
        "§8 must quote the block's own count")


def test_every_bare_percentage_the_brief_prints_is_a_recorded_ratio():
    """F5 — the round-6 allow-list read `pp` and `× 10⁻ⁿ`, not a bare `%`.

    `#13267` review 7 F5: `5 of 300 items (1.7 %)` -> `(9.7 %)` and
    `3 seeds … (1 % of items)` -> `9 %` both stayed GREEN, while the COUNTS beside
    them are pinned — so a re-stale produced a self-contradicting sentence, on the
    figure R4's "cost and benefit are the same order" conclusion rests on.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    allowed = set()
    for name in _ARTIFACTS:
        art = _artifact(name)
        n = art["population"]["sample"]
        for block in art["arms"].values():
            for kid, cell in block["per_k"].items():
                k = int(kid.split("=")[1])
                for frac in ((cell["on_hits"] - cell["off_hits"]) / n,
                             cell["off_hits"] / n, cell["on_hits"] / n):
                    allowed.add(round(frac * 100, 1))
                    allowed.add(round(frac * 100))
                # the churn cell: displaced ids over ids compared, per k
                disp = cell.get("off_topk_displacement") or {}
                denom = disp.get("items_with_off_topk", 0) * k
                if denom:
                    churn = disp["displaced_ids"] / denom
                    allowed.add(round(churn * 100, 1))
                    allowed.add(round(churn * 100))
                seed = cell.get("seed_effect") or {}
                moved = (seed.get("seed_lost_from_window", 0)
                         + seed.get("seed_demoted_but_kept", 0))
                if n:
                    allowed.add(round(moved / n * 100, 1))
                    allowed.add(round(moved / n * 100))
    shown = {v for v in re.findall(r"\((\d+(?:\.\d+)?)\s*%\)", brief)}
    shown |= {m for m in re.findall(r"(\d+(?:\.\d+)?)\s*% of items", brief)}
    assert shown, "the brief states at least one bare percentage"
    numeric = {float(v) for v in shown}
    strays = {v for v in numeric
              if round(v, 1) not in allowed and round(v) not in allowed}
    assert not strays, (
        f"the brief prints {sorted(strays)} %, which is not a ratio any artifact "
        f"records (allowed: {sorted(allowed)})")


def test_the_annotation_count_is_the_count_of_groups_actually_listed():
    """F2 — the count was corrected three times and pinned none of them.

    `#13267` reviews 6 F6 and 7 F2: §8 declared FIVE groups, listed five, then
    called the derived-figure declaration "the fifth" — two groups called fifth and
    a count one short. Corrected to SIX, the prose still survived `SIX -> FIVE`
    GREEN, because a count in a sentence is a figure like any other. So the count is
    derived from the ARTIFACTS (what each file actually carries) and every group it
    counts must be named in §8.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    #: each entry is (marker the prose must name, a detector over one artifact)
    groups = {
        "identity_measured": lambda a: any(
            "identity_measured" in b for b in a["arms"].values()),
        "ranked_lists_compared_note": lambda a: any(
            "ranked_lists_compared_note" in b for b in a["arms"].values()),
        "construction_unrecorded": lambda a: "construction_unrecorded" in a,
        "repeat_check": lambda a: any("repeat_check" in b
                                      for b in a["arms"].values()),
        "provenance": lambda a: "provenance" in a,
        "derived_note": lambda a: "derived_note" in next(
            iter(a["arms"].values())),
    }
    carried = {marker for marker, detected in groups.items()
               if any(detected(_artifact(name)) for name in _ARTIFACTS)}
    assert len(carried) == len(groups), (
        f"an annotation group named here is carried by no artifact: "
        f"{sorted(set(groups) - carried)}")
    words = {4: "FOUR", 5: "FIVE", 6: "SIX", 7: "SEVEN"}
    marker = re.search(r"\*\*And (FOUR|FIVE|SIX|SEVEN) field groups", brief)
    assert marker, "§8 states the annotation-group count"
    declared = {v: k for k, v in words.items()}[marker.group(1)]
    assert declared == len(groups), (
        f"§8 says {declared} annotation groups; the artifacts carry "
        f"{len(groups)}: {sorted(groups)}")
    # the whole §8 region, because the derived-figure group is named in the
    # paragraph that FOLLOWS the enumeration
    section = brief[brief.index(marker.group(0)):]
    section = section[:section.index("\n## ", 1)] if "\n## " in section[1:] else section
    for name in groups:
        assert f"`{name}`" in section or name in section.replace("`", ""), (
            f"§8 counts `{name}` as an annotation group but does not name it")
    ordinal = {4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh"}[declared]
    assert f"The {ordinal} and last annotation group is the derived-figure" in brief, (
        f"the enumeration is {declared} long, so the derived-figure declaration is "
        f"the {ordinal} — the summary calls it something else")


def test_the_tool_does_not_declare_point_only_figures_on_the_object_arm(
        _no_embedder_probe):
    """F6 — the artifacts said the object arm's `seed_effect` was recomputed.

    `#13267` review 7 F6: `_recomputable` / `_PER_ITEM_DERIVED` were built for
    every arm, so a tool-produced OBJECT artifact declared `seed_effect`
    recomputable and `off_topk_displacement` unpinned — figures that arm emits
    nowhere, which the helper then `continue`d over. Fixing the artifacts without
    pinning the TOOL left the generator free to re-introduce it.

    ⛔ `#13267` review 12 F2: this is a STRUCTURAL pin over a synthetic artifact, so
    it must not pay for an embedder. It was one of TWO `_build_fake_artifact`
    callers without the fixture (with `test_a_read_limit_below_every_k_...`), and
    `_build_artifact` calls `_has_embedder()` → `EmbeddingModel.get()`, which loads
    a model (~2 min) on any box that has one: measured 122.21 s of a 127.23 s file
    there, against `ci-surfaces.yml`'s "~5 s (measured)" for this surface. The
    fixture makes the cost independent of the host's model cache rather than of the
    code under test.
    """
    names = [f"item-{n:06d}" for n in range(8)]
    point_off = {i: [f"{i}-a"] for i in names}
    point_on = {i: [f"{i}-a", f"{i}-b"] for i in names}
    object_off = {i: [f"{i}-a"] for i in names}
    art, _ = _build_fake_artifact(point_off=point_off, point_on=point_on,
                                  object_off=object_off, object_on=object_off)
    # ⛔ and again with per-item seed ranks present on the OBJECT arm, so the
    # `arm == "point"` guard is itself witnessable: without it the object arm
    # sees `_has_seed_ranks` true and declares a figure it never emits
    seeded = _build_fake_artifact(
        point_off=point_off, point_on=point_on,
        object_off=object_off, object_on=object_off,
        arms={"point": {"off": _fake_arm(point_off, seed_ranks={i: 0 for i in names}),
                        "on": _fake_arm(point_on, seed_ranks={i: 0 for i in names})},
              "object": {"off": _fake_arm(object_off, seed_ranks={i: 0 for i in names}),
                         "on": _fake_arm(object_off, seed_ranks={i: 0 for i in names})}})[0]
    assert "seed_effect" not in set(seeded["arms"]["object"]["derived_recomputable"]) | \
        set(seeded["arms"]["object"]["derived_unpinned"]), (
        "the object arm carries seed ranks in this build, and still emits no "
        "`seed_effect` cell — the `arm == \"point\"` guard must hold")
    for arm in ("point", "object"):
        block = art["arms"][arm]
        declared = set(block["derived_recomputable"])
        unpinned = set(block["derived_unpinned"])
        emitted = block["per_k"]["k=3"]
        for field in _POINT_ONLY_DERIVED:
            if arm == "point":
                assert field in declared or field in unpinned, (
                    f"the point arm emits `{field}`, so the tool must classify it")
            else:
                assert field not in declared and field not in unpinned, (
                    f"the object arm emits `{field}` NOWHERE (cells: "
                    f"{sorted(emitted)}), so declaring it is a claim about a field "
                    "a reader will not find")


def test_every_artifacts_recorded_load_delta_is_its_own_end_minus_start():
    """F2 — the "arithmetic that cannot add up" was still reachable.

    `#13267` review 9 F2: the site-scoped pin compares the PRINTED delta to the
    RECORDED delta, but nothing compared the recorded delta to `load_end - start`,
    so `-0.54 -> -0.44` in the artifact plus both brief sites stayed GREEN and the
    brief printed `load 13.09 -> 12.55 (Δ -0.44)`. The identity is the one the
    tool is built on (`round(end - start, 2)`), so a delta that cannot add up
    becomes unreachable rather than merely unreprinted.
    """
    for name in _ARTIFACTS:
        art = _artifact(name)
        env = art["environment"]
        for window in ("load_1m", "load_5m", "load_15m"):
            recorded = env.get(f"{window}_delta_1m" if window == "load_1m"
                               else f"{window}_delta")
            if recorded is None:
                continue
            start = env["load_start"][window]
            end = env["load_end"][window]
            assert round(end - start, 2) == round(recorded, 2), (
                f"{name}: {window} {start} -> {end} is "
                f"{round(end - start, 2)}, recorded as {recorded}")


def test_every_artifacts_self_describing_counts_equal_the_rows_they_count():
    """F3 — `sampled`, `total_bytes` and `items_read_per_arm` were free parameters.

    `#13267` review 9 F3: `sampled 300 -> 250`, `total_bytes 78,571,361 -> …362`
    (in all three artifacts) and `items_read_per_arm.point 300 -> 30` were each
    GREEN. Each must equal another field in the SAME artifact, so the relation is
    the pin.
    """
    for name in _ARTIFACTS:
        art = _artifact(name)
        items = art["items"]
        drawn = items["with_item"] + sum(items["dropped"].values())
        assert items["sampled"] == drawn, (
            f"{name}: `sampled` {items['sampled']} but with_item "
            f"{items['with_item']} + dropped {sum(items['dropped'].values())}")
        assert items["sampled"] == art["population"]["sample"], (
            f"{name}: `sampled` disagrees with the population triple it was drawn from")
        snap = art["snapshot"]
        assert snap["total_bytes"] == sum(f["bytes"] for f in snap["files"]), (
            f"{name}: `total_bytes` is not the sum of its own file list")
        assert snap["total_lines"] == sum(f["lines"] for f in snap["files"]), (
            f"{name}: `total_lines` is not the sum of its own file list")
        assert snap["file_count"] == len(snap["files"])
        for arm, count in art["pairing"]["items_read_per_arm"].items():
            for side in ("off", "on"):
                assert art["arms"][arm]["reads"][side] == count, (
                    f"{name}/{arm}: `pairing.items_read_per_arm` says {count}, "
                    f"the {side} arm read {art['arms'][arm]['reads'][side]}")


def test_each_brief_figure_is_bound_to_the_k_cell_it_belongs_to():
    """F1 — the winner-rule and harm figures were an unordered allow-list.

    `#13267` review 9 F1: `allowed_pp`/`pairs` are built over EVERY k of EVERY
    artifact, so each figure could be swapped for ANOTHER RECORDED figure. Five
    mutants were GREEN at this head: `+1.67 pp` -> `+4.33 pp` at the k=3 rule
    sentence; the k=3/k=5 `(delta < MDE)` pairs swapped; §4.1's `p is 0.0625` ->
    `0.00002` (three lines under "neither side can be separated from noise");
    `(+1.67 < 2.08)` -> `(+4.33 < 2.08)`; and a swapped k-attribution in §5.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    head = _artifact("paired-point-300.json")["arms"]["point"]["per_k"]
    short = _artifact("paired-point-300-short-query.json")["arms"]["point"]["per_k"]

    def cell(block, k, key):
        return block[f"k={k}"][key]

    def pp(block, k):
        return f"{(cell(block, k, 'on_hits') - cell(block, k, 'off_hits')) / 300 * 100:.2f}"

    def mde(block, k):
        # the artifact records the MDE as a FRACTION; the brief prints pp
        return f"{cell(block, k, 'mde_paired') * 100:.2f}"

    # §4's rule: the k=3 sentence must carry k=3's OWN pair, in that order
    assert f"+{pp(head, 3)} pp against a {mde(head, 3)} pp minimum detectable effect" \
        in brief, "the k=3 rule sentence must carry k=3's own (delta, MDE)"
    # §5's winner row: every (delta, MDE) pair, bound to its own k
    for k in (10, 15):
        assert f"k = {k} ({pp(head, k)} > {mde(head, k)})" in brief, (
            f"k = {k} clears its own MDE; the row must print that pair")
    for k in (3, 5):
        assert f"k = {k} ({pp(head, k)} < {mde(head, k)}" in brief, (
            f"k = {k} does NOT clear its own MDE; the row must print that pair")
    # §4's "cannot at k = 10 or k = 15" sentence carries those MDEs only
    assert f"MDE {mde(head, 10)} / {mde(head, 15)} pp" in brief
    # §4.1's harm p is the SHORT arm's own k=15 cell, not any recorded p
    assert f"exact McNemar p is {cell(short, 15, 'mcnemar_exact_p'):g}" in brief, (
        "§4.1's harm-side p belongs to the short arm's k=15 cell")


def test_section_zero_quotes_are_pinned_and_its_z_scores_re_derive():
    """F5 — §0's `#13118` statistics were pinned as four recalls only.

    `#13267` review 9 F5: `| absent at any k | 28 | 25 |` -> `| 38 | 27 |` and
    `z = 1.08` -> `1.88` / `0.37` -> `1.88` were GREEN, and `z = 1.88` beside
    "Neither is distinguishable from noise" is a materially different claim.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    absent = re.search(r"\| absent at any k \| (\d+) \| (\d+) \|", brief)
    assert absent, "§0 prints the absent-at-any-k row"
    assert absent.group(1) == "28" and absent.group(2) == "25", (
        f"the published absent counts are 28 / 25, the brief prints "
        f"{absent.group(1)} / {absent.group(2)}")
    # z = delta / SE, and BOTH the delta and the SE are printed in the same
    # sentence — so a z that does not re-derive is the defect
    # ⛔ each z must divide by ITS OWN printed SE: the sentence used to print one
    # SE for two z-scores computed at different k, so `z = 0.37` beside
    # "SE 2.8" was arithmetic that cannot add up (`#13267` review 9 F5, found by
    # this pin: 1.3 / 2.8 = 0.46).
    triples = re.findall(
        r"delta (?:is )?(\d+\.\d+) pp at SE (\d+\.\d+) pp\s*\(\*\*z = (\d+\.\d+)\*\*\)",
        brief, flags=re.S)
    assert len(triples) >= 2, (
        f"§0 states the @15 and @3 moves with their OWN SE each; found {triples}")
    for delta, se_pp, printed in triples:
        want = float(delta) / float(se_pp)
        assert abs(float(printed) - want) < 0.02, (
            f"§0 prints z = {printed} for a delta of {delta} pp at SE {se_pp} pp; "
            f"the arithmetic gives {want:.2f}")


def test_every_z_score_the_brief_prints_divides_its_own_delta_by_its_own_se():
    """F5 — `z` was read by no parser at all.

    `#13267` review 9 F5: `z = 1.08` -> `1.88` was GREEN, and `z` beside
    "Neither is distinguishable from noise" is a materially different claim
    (1.88 is ~p 0.06). §0 printed ONE SE for TWO z-scores computed at different k,
    so `z = 0.37` divided 1.3 by 2.8 = 0.46 — arithmetic that cannot add up, which
    this pin found while being written.
    """
    brief = (_DOCS / "research-brief.md").read_text()
    triples = re.findall(
        r"delta (?:is )?(\d+\.\d+) pp at SE (\d+\.\d+) pp\s*\(\*\*z = "
        r"(\d+\.\d+)\*\*\)", brief, flags=re.S)
    assert len(triples) >= 2, (
        f"§0 states the @15 AND @3 moves, each with its own SE; found {triples}")
    for delta, se_pp, z in triples:
        assert abs(float(delta) / float(se_pp) - float(z)) < 0.02, (
            f"z = {z} does not equal {delta} / {se_pp}")


def test_a_read_limit_below_every_k_is_REFUSED_not_silently_empty(
        _no_embedder_probe):
    """F4 — a run that measured nothing exited 0 and recorded no refusal.

    `#13267` review 9 F4: `--read-limit 2` produced `per_k: []`, `refusals: []`
    and exit 0 — the artifact's machine-readable "clean" verdict — while the tool
    advertises exit 3 for "a measurement the tool will not take". No opt-out
    excuses this class, so `--allow-mixed-modes` cannot un-refuse it either.
    """
    names = [f"item-{n:06d}" for n in range(6)]
    ids = {i: [f"{i}-a"] for i in names}
    on = {i: [f"{i}-a", f"{i}-b"] for i in names}
    for limit, refused in ((2, True), (min(P.KS), False), (60, False)):
        art, _ = _build_fake_artifact(
            point_off=ids, point_on=on, object_off=ids, object_on=ids,
            args=_fake_args(read_limit=limit))
        assert art["refusal_kinds"] == ([P.NO_K_REFUSAL] if refused else []), (
            f"--read-limit {limit}: refused={refused}, kinds="
            f"{art['refusal_kinds']}")
        assert (art["arms"]["point"]["per_k"] == {}) is refused, (
            f"--read-limit {limit}: an empty `per_k` must be the REFUSED case")
        code = P._exit_code(list(zip(art["refusal_kinds"], art["refusals"],
                                     strict=True)), allow_mixed_modes=True)
        assert (code == 3) is refused, (
            f"--read-limit {limit}: exit {code}; --allow-mixed-modes must not "
            "excuse a run with no statistic in it")


def test_the_tool_really_has_the_omission_flag_its_docstring_promises():
    """F6 — the docstring promised `--no-include-per-item`; argparse had no such flag.

    `#13267` review 9 F6: `--include-per-item` was `store_true, default=True`, so
    it was always True, the `} if args.include_per_item else None` branch was
    unreachable, and the `has_per_item` gate could never be False.
    """
    import argparse
    p = argparse.ArgumentParser()
    src = (Path(P.__file__)).read_text()
    decl = next(ln for ln in src.splitlines()
                if 'add_argument("--include-per-item"' in ln)
    assert "BooleanOptionalAction" in decl or "store_false" in decl, (
        f"`--include-per-item` is declared {decl.strip()} — a `store_true` with "
        "`default=True` makes the omission flag its docstring promises "
        "nonexistent and its `else None` branch unreachable")
    # and the real parser accepts both spellings
    p.add_argument("--include-per-item", action=argparse.BooleanOptionalAction,
                   default=True)
    assert p.parse_args(["--no-include-per-item"]).include_per_item is False


def test_a_ci_that_excludes_zero_where_the_exact_test_does_not_is_CALLED_OUT():
    """F1 — a published interval contradicted the exact test printed beside it.

    `#13267` reviews 10 F1 and 11 F1–F3. The headline's k = 3 row prints
    `McNemar exact p 0.0625` beside 95 % CI `[+0.33, +3.33]`, and §4.1 called that
    cell "neither side can be separated from noise". The cause is mechanical: with
    b = 5, c = 0, n = 300 the percentile bootstrap's 2.5th percentile is ONE
    discordant item, so `(1 - 5/300)**300` of resamples are cut off below it — the
    interval is anti-conservative exactly where the exact test is conservative.

    ⛔ The FIRST version of this caveat named k = 3, 5 and 10 of the headline and
    stated one mechanism number for all of them; k = 5's exact p is 0.0313 (it DOES
    separate), k = 10's percentile is 7 items, and the §4.1 copy carried the k = 5
    row's `b`. So this pin derives the ROW SET and every NUMBER from the artifacts
    rather than asserting three phrases (`#13267` review 11 F5).
    """
    brief = (_DOCS / "research-brief.md").read_text()
    clash, not_clash = [], []
    for name in ("paired-point-300.json", "paired-point-300-short-query.json"):
        art = _artifact(name)
        n = art["population"]["sample"]
        for k, cell in art["arms"]["point"]["per_k"].items():
            b = cell["discordant_miss_to_hit"]
            c = cell["discordant_hit_to_miss"]
            lo_items = round(cell["bootstrap"]["ci95"][0] * n)
            p0 = (1 - (b + c) / n) ** n
            (clash if lo_items > 0 and cell["mcnemar_exact_p"] > 0.05
             else not_clash).append(
                {"artifact": name, "k": k, "b": b, "c": c, "n": n,
                 "lo_items": lo_items, "p0": p0,
                 "p": cell["mcnemar_exact_p"]})
    assert clash, ("this reproduction expects at least one clash; if none remain, "
                   "the caveat can be deleted rather than left as folklore")
    # ── the caveat's OWN mechanism must hold for every clashing cell ──────
    for cell in clash:
        assert cell["lo_items"] == 1, (
            f"{cell['artifact']}/{cell['k']}: the caveat says the interval excludes "
            f"zero because the 2.5th percentile is ONE discordant item; it is "
            f"{cell['lo_items']}")
        assert cell["p0"] < 0.025, (
            f"{cell['artifact']}/{cell['k']}: P(zero discordant in a resample) is "
            f"{cell['p0']:.5f}, so resamples showing no gain are NOT cut off below "
            "the 2.5th percentile and this mechanism does not explain the clash")
        assert cell["b"] == 5 and cell["c"] == 0, (
            f"{cell['artifact']}/{cell['k']}: the caveat states b = 5, c = 0; the "
            f"artifact records b = {cell['b']}, c = {cell['c']}")
    # ── and the NUMBERS the caveat prints must be those derived values ────
    cells = {c["k"] for c in clash if "point-300.json" in c["artifact"]}
    short = {c["k"] for c in clash if "short-query" in c["artifact"]}
    assert cells == {"k=3"}, (
        f"the caveat names the headline k = 3 only; derived clash set is {cells}")
    assert short == {"k=10", "k=15"}, (
        f"the caveat names the short-query arm's k = 10 and k = 15; derived set is "
        f"{short}")
    named = "the short-query arm's k = 10 and k = 15"
    # the brief hard-wraps, so compare on whitespace-normalised prose
    assert named in " ".join(brief.split()), (
        f"the caveat must NAME the short-query cells it covers ({sorted(short)}); "
        "naming them as a set from the artifacts is not the same as printing them")
    # ⛔ `#13267` review 12 F2: the row-set SENTENCE was checked for presence only,
    # so the retracted wording could be reinserted beside it, and `0.0313`,
    # `0.0625`, the k = 5 percentile count and the row COUNT were literals no test
    # read. Every one of them is derived from the artifacts now.
    flat = " ".join(brief.split())
    assert "At k = 3, 5 and 10" not in flat, (
        "the retracted row-set wording is back — k = 5 does separate, and k = 10's "
        "percentile is seven items")
    words = {1: "one", 2: "two", 3: "three", 4: "four"}
    assert f"{words[len(clash)].capitalize()} rows' bootstrap intervals" in flat, (
        f"the caveat opens with the derived clash count ({len(clash)}); it prints "
        "a different word")
    # the k = 5 exclusion's own figures, derived
    head5 = next(c for c in not_clash if "point-300.json" in c["artifact"]
                 and c["k"] == "k=5" and c["b"] == 6)
    # ⛔ parse + compare numerically: the brief prints 4 dp and `.4f` on 0.03125
    # rounds the other way, so a formatted-string match would fail on a tie
    printed5 = re.search(r"its exact p is (\d+\.\d+)", flat)
    assert printed5 and abs(float(printed5.group(1)) - head5["p"]) < 1e-4, (
        f"the k = 5 exclusion must quote its own p ({head5['p']}); it prints "
        f"{printed5.group(1) if printed5 else 'nothing'}")
    assert f"{words[head5['lo_items']]} discordant items, not one" in flat, (
        f"k = 5's 2.5th percentile is {head5['lo_items']} items; the caveat must "
        "say so rather than reusing the one-item story")
    # the duality sentence divides by the clashing cells' own exact p
    clash_ps = {f"{c['p']:g}" for c in clash}
    # ⛔ scoped to the CAVEAT PARAGRAPH, not the whole brief: the §3 table prints
    # the same 0.0625, so a document-wide search is satisfied by the table and the
    # duality sentence can carry any p at all (`#13267` review 12 F2)
    caveat_para = flat[flat.index("rows' bootstrap intervals"):
                       flat.index("nowhere else.")]
    assert any(p_ in caveat_para for p_ in clash_ps), (
        f"the duality sentence must quote the clashing cells' own exact p "
        f"({sorted(clash_ps)}); the caveat paragraph carries none of them")
    # b/c/n are stated once for all clashing cells, so they must be true of all
    assert "`b = 5`, `c = 0`, `n = 300`" in brief or \
        "`b = 5`, `c = 0`, `n = 300`" in flat, (
        "the caveat states b/c/n for every clashing cell at once; all three must "
        "hold for all of them")
    for cell in clash:
        assert (cell["b"], cell["c"], cell["n"]) == (5, 0, 300), cell
    assert "0.0065" in brief and "(1 − 5/300)³⁰⁰" in brief, (
        "the caveat must print the resample probability it derives")
    assert f"1/300 = {1 / 300 * 100:.2f} pp" in brief, (
        "the caveat must print the one-discordant-item lower bound")
    assert "cut off" in brief and "below the 2.5th percentile" in brief
    assert "the exact test governs" in brief
    assert "ANTI-CONSERVATIVE" in brief
    # ── k = 5 must be EXCLUDED explicitly, with its own true reason ───────
    assert "k = 5 is NOT one of these cells" in brief, (
        "k = 5's exact p = 0.0313 separates it; it is excluded from the win rows by "
        "the MDE rule, and the caveat must not claim the null failed to separate it")
    assert "+2.00` against `2.28 pp" in brief, (
        "the k = 5 exclusion must cite the MDE rule that actually excludes it")
    # ── §4.1's copy carries ITS OWN cell's b, not another row's ───────────
    para = brief[brief.index("neither side is separated from the null at"):]
    para = para[:para.index("\n\n")] if "\n\n" in para else para
    for cell in clash:
        if "short-query" in cell["artifact"]:
            assert f"with {cell['b']} of 300 discordant" in para, (
                f"§4.1's caveat must quote its own cell's b = {cell['b']}; it "
                f"carries {para[:400]}")
    assert "neither side can be separated from noise" not in brief, (
        "the retracted phrasing is back")

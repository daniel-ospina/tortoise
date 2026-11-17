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

import argparse
import json
import math
import os
import re
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
                         args=None):
    item_ids = sorted(point_off)
    names = [f"the name {i}" for i in range(len(item_ids))]
    items = [{"item_id": iid, "name": name, "target_overlap_with_query": 0}
             for iid, name in zip(item_ids, names, strict=True)]
    manifest = {"schema": "s", "digest": "0" * 64, "file_count": 1,
                "total_bytes": 1, "total_lines": 1, "files": []}
    pop_body = {"schema": P.POPULATION_SCHEMA, "snapshot_digest": "0" * 64,
                "population": 40, "sample": len(item_ids), "seed": 7,
                "names_sha256": P._sha256_bytes("\n".join(names).encode())}
    arms = {
        "point": {"off": _fake_arm(point_off, seed_ranks={i: 0 for i in item_ids}),
                  "on": _fake_arm(point_on, seed_ranks={i: 0 for i in item_ids})},
        "object": {"off": _fake_arm(object_off), "on": _fake_arm(object_on)},
    }
    art = P._build_artifact(
        args or _fake_args(), manifest, pop_body, items, {}, {"anchors": 1},
        arms, item_ids, load_start={"load_1m": 1.0, "load_5m": 1.0,
                                    "load_15m": 1.0, "cpus": 1},
        load_end={"load_1m": 1.0, "load_5m": 1.0, "load_15m": 1.0, "cpus": 1},
        graph_nodes=1, elapsed=1.0, pop_file=Path("/tmp/population.json"))
    return art, item_ids


@pytest.fixture()
def _no_embedder_probe(monkeypatch):
    """The artifact's structure is what these two tests assert, and
    `EmbeddingModel.get()` loads a model on first call (~2 min on this box) — a
    cost that belongs to a real RUN, not to a structural pin."""
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
        # the identity is a MEASUREMENT exactly when ranked lists were compared:
        # a number beside zero compared items is `None == None` dressed up as a
        # measurement, and `null` beside a non-zero count is a measurement
        # thrown away
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
        _assert_the_artifact_pins_hold(art)
        # the committed runs predate the tool recording the knobs — the artifacts
        # name them as unrecorded, and the brief carries their values as prose
        assert "query_chars" in art["construction_unrecorded"]["fields"]

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
    """NaN-aware equality — the control publishes `NaN` MDEs as a called shot."""
    if isinstance(recorded, float) and isinstance(recomputed, float):
        if math.isnan(recorded) and math.isnan(recomputed):
            return True
        return math.isclose(recorded, recomputed, rel_tol=0, abs_tol=1e-12)
    return recorded == recomputed


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
                 "mde_independent", "off_recall", "on_recall")


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


def test_the_briefs_result_tables_repeat_their_artifacts_cell_by_cell():
    """⛔ F1, the prose half: a figure re-staled in the BRIEF reds here.

    Measured survivors before this test (artifact and brief each mutated alone,
    both left green): `17/300` → `3/300` and → `99/300` in §4, `2.08 pp` →
    `0.20 pp` in the MDE column, `727 / 900` → `700 / 900` in §4.1, `293 of 300`
    → `1 of 300`, and `seed lost from window` `3` → `0` in the short-query table.
    Each cell below is compared against the artifact it claims to quote, in the
    cell position a reader reads it in — not against a substring of the file.
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
    _assert_the_artifact_pins_hold(art)
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

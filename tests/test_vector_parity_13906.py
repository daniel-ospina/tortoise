"""#13906 — the comparator that gates the vector move is itself gate-tested.

WHY THIS FILE IS DB-FREE AND NOT ANOTHER LIVE TEST. `tools/vector_parity_13906.py`
is the instrument that is allowed to say "the two dense legs agree". An
instrument is only evidence if its NEGATIVE exists too: a comparison that has
never returned `False` cannot distinguish parity from a blind spot, and the
brief's own rule puts it bluntly — *a harness that has never reported a
difference is not a harness*.

The live half (fresh pgvector + FalkorDB, real corpus) is run by hand and its
artifacts are committed under `docs/research/2026-11-20-13906-vector-parity/`.
What CI can pin without a database is the part that would silently make that
artifact meaningless: the diff itself, the tie policy, and the poisons.

⛔ THE ONE PROPERTY EVERY TEST HERE PROTECTS: **passing must be hard.** If
`compare` can be made to return `ids_equal=True` / `order="exact"` on a list
that differs from the oracle, then every green run of the live harness — this
lane's included — is unverified.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "tools" / "vector_parity_13906.py"


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("vector_parity_13906", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    # ⛔ IN `sys.modules` BEFORE `exec_module`, and not for tidiness: the harness
    # uses `from __future__ import annotations`, so `Row`'s field types are
    # STRINGS and `dataclasses` resolves them through
    # `sys.modules[cls.__module__]`. Without this line it is `None` and every
    # test errors with `AttributeError: 'NoneType' object has no attribute
    # '__dict__'` — measured, not theorised.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── the oracle ─────────────────────────────────────────────────────────────

def test_truth_is_ordered_by_distance_then_key(harness):
    rows = [
        harness.Row(key="b", embedding=[0.0, 1.0, 0.0, 0.0]),
        harness.Row(key="a", embedding=[0.0, 1.0, 0.0, 0.0]),
        harness.Row(key="far", embedding=[1.0, 0.0, 0.0, 0.0]),
    ]
    got = harness.truth(rows, [0.0, 1.0, 0.0, 0.0], 3, metric="euclidean")
    # Two EXACT duplicates (a, b) tie at distance 0 and the key breaks the tie;
    # `far` is orthogonal. A bare `sort` on distance alone would make the oracle
    # itself non-deterministic, and every parity claim derived from it too.
    assert [key for key, _ in got] == ["a", "b", "far"]
    assert got[0][1] == pytest.approx(0.0)
    assert got[2][1] == pytest.approx(2 ** 0.5)


def test_truth_excludes_the_populations_the_read_legs_exclude(harness):
    """Width, terminal status, the legacy flag — the three eligibility rules."""
    rows = [
        harness.Row(key="live", embedding=[0.0, 1.0, 0.0, 0.0]),
        harness.Row(key="wide", embedding=[0.0] * 768),
        harness.Row(key="none", embedding=None),
        harness.Row(key="retracted", embedding=[0.0, 1.0, 0.0, 0.0],
                    status="retracted"),
        harness.Row(key="outdated", embedding=[0.0, 1.0, 0.0, 0.0],
                    outdated=True),
        harness.Row(key="foreign", embedding=[0.0, 1.0, 0.0, 0.0],
                    tenant="other"),
    ]
    got = [key for key, _ in harness.truth(
        rows, [0.0, 1.0, 0.0, 0.0], 10, metric="cosine")]
    assert got == ["live"], (
        "an excluded population reached the oracle — the harness would then "
        "report a leg that correctly hides it as MISSING a row, i.e. it would "
        f"fail a correct leg. Got {got}")


def test_cosine_and_euclidean_disagree_when_norms_differ(harness):
    """The metric-drift premise, pinned rather than assumed.

    The record leg scores COSINE; the graph's brute-force SCAN scores EUCLIDEAN.
    On un-normalised vectors (`embeddings.compute_embeddings` stores the model's
    raw output) the two orderings can differ — this is the fact the live
    harness's `graph_scan_vs_cosine` column measures, and if it were false the
    harness's informational column would be meaningless.
    """
    query = [1.0, 0.0, 0.0, 0.0]
    rows = [
        # Same direction as the query, long: cosine 0 (best), euclidean 9.
        harness.Row(key="same_dir_long", embedding=[10.0, 0.0, 0.0, 0.0]),
        # Slightly off direction, short: cosine > 0, euclidean tiny.
        harness.Row(key="off_dir_short", embedding=[0.1, 0.02, 0.0, 0.0]),
    ]
    cos = [k for k, _ in harness.truth(rows, query, 2, metric="cosine")]
    euc = [k for k, _ in harness.truth(rows, query, 2, metric="euclidean")]
    assert cos == ["same_dir_long", "off_dir_short"]
    assert euc == ["off_dir_short", "same_dir_long"], (
        "the two metrics agreed on un-normalised vectors — either the fixture "
        "stopped exercising the drift or the distances were mixed up")


# ── the comparator ─────────────────────────────────────────────────────────

def _truth():
    return [("a", 0.10), ("b", 0.20), ("c", 0.30)]


def test_an_identical_list_is_reported_as_parity(harness):
    cmp_ = harness.compare(_truth(), ["a", "b", "c"])
    assert cmp_["ids_equal"] is True
    assert cmp_["order"] == "exact"
    assert cmp_["recall"] == 1.0


def test_a_within_tie_permutation_is_NOT_a_divergence(harness):
    """The research rung's tie-equivalence policy, as a decision.

    Two ids at the SAME distance are interchangeable: the stores tie-break by
    recency-then-id, the oracle by id, so a swap inside the tie group is the
    correct classification — not a pass, and not a failure.
    """
    truth_rows = [("a", 0.1), ("b", 0.1), ("c", 0.5)]
    cmp_ = harness.compare(truth_rows, ["b", "a", "c"])
    assert cmp_["order"] == "tie"
    assert cmp_["ids_equal"] is True


def test_a_cross_distance_permutation_IS_a_divergence(harness):
    cmp_ = harness.compare(_truth(), ["b", "a", "c"])
    assert cmp_["order"] == "diverged", (
        "a swap across DIFFERENT distances was called a tie — the tie policy "
        "is now wide enough to hide a real ranking change")
    assert cmp_["ids_equal"] is True
    assert cmp_["order_mismatches"][0] == {
        "position": 0, "truth": "a", "candidate": "b",
        "truth_distance": 0.10, "candidate_distance": 0.20}


def test_a_missing_id_is_reported_as_recall_loss(harness):
    cmp_ = harness.compare(_truth(), ["a", "b"])
    assert cmp_["ids_equal"] is False
    assert cmp_["recall"] == pytest.approx(2 / 3)
    assert cmp_["missing"] == ["c"]


def test_an_extra_id_is_reported_even_when_recall_is_full(harness):
    """A leg that invents a row is wrong in a way recall alone cannot see."""
    cmp_ = harness.compare(_truth(), ["a", "b", "c", "zzz"])
    assert cmp_["ids_equal"] is False
    assert cmp_["recall"] == 1.0
    assert cmp_["extra"] == ["zzz"]


# ── the falsifier ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["id", "order", "recall"])
def test_each_poison_makes_the_comparison_fail(harness, kind):
    """⛔ THE GATE ON THE GATE. Each poison must produce a red comparison."""
    truth_rows = [("a", 0.1), ("b", 0.2), ("c", 0.3), ("d", 0.4)]
    candidate = [("a", 0.1), ("b", 0.2), ("c", 0.3), ("d", 0.4)]
    poisoned = harness.poison(candidate, truth_rows, kind)
    cmp_ = harness.compare(truth_rows, [k for k, _d in poisoned],
                           candidate_distances=[d for _k, d in poisoned])
    assert cmp_["order"] != "exact" or cmp_["ids_equal"] is False, (
        f"poison {kind!r} produced NO divergence — the harness is blind to it")
    assert not (cmp_["ids_equal"] and cmp_["order"] == "exact")


def test_the_poisoned_verdict_is_always_red(harness):
    """A poisoned run is never a pass, including when it goes undetected."""
    report = {"queries": [{"query": "q0",
                           "record": {"ids_equal": True, "order": "exact",
                                      "recall": 1.0,
                                      "distance": {"ok": True, "max_delta": 0.0}},
                           "graph_index": {"ids_equal": True, "order": "exact",
                                           "recall": 1.0,
                                           "distance": {"ok": True, "max_delta": 0.0}},
                           "graph_scan_vs_euclidean": {
                               "ids_equal": True, "order": "exact",
                               "recall": 1.0,
                               "distance": {"ok": True, "max_delta": 0.0}},
                           "metric_divergence_ids": []}]}
    clean = harness._verdict(report, poisoned=None)
    assert clean["ok"] is True

    blind = harness._verdict(report, poisoned="id")
    assert blind["ok"] is False, (
        "a poisoned run that found nothing was reported GREEN — that is the "
        "exact failure mode the poison exists to expose")
    assert blind["poison_status"] == "POISON NOT DETECTED — HARNESS BLIND"


def test_a_divergent_report_is_red_for_the_required_arms(harness):
    report = {"queries": [{"query": "q0",
                           "record": {"ids_equal": False, "order": "diverged",
                                      "recall": 0.5,
                                      "distance": {"ok": False, "max_delta": 0.4}},
                           "graph_index": {"ids_equal": True, "order": "exact",
                                           "recall": 1.0,
                                           "distance": {"ok": True, "max_delta": 0.0}},
                           "graph_scan_vs_euclidean": {
                               "ids_equal": True, "order": "exact",
                               "recall": 1.0,
                               "distance": {"ok": True, "max_delta": 0.0}},
                           "metric_divergence_ids": ["x"]}]}
    v = harness._verdict(report, poisoned=None)
    assert v["ok"] is False
    assert any("record diverged" in f for f in v["failures"])


def test_metric_divergence_alone_does_not_fail_the_run(harness):
    """The graph scan's euclidean ranking is pre-existing behaviour.

    It is MEASURED and reported, never gated here: this issue moves the read to
    the record, it does not decide the graph scan's metric. A harness that
    failed on it would fail every run on an index-less store for a reason no PR
    in this issue changes.
    """
    report = {"queries": [{"query": "q0",
                           "record": {"ids_equal": True, "order": "exact",
                                      "recall": 1.0,
                                      "distance": {"ok": True, "max_delta": 0.0}},
                           "graph_index": {"ids_equal": True, "order": "exact",
                                           "recall": 1.0,
                                           "distance": {"ok": True, "max_delta": 0.0}},
                           "graph_scan_vs_euclidean": {
                               "ids_equal": True, "order": "exact",
                               "recall": 1.0,
                               "distance": {"ok": True, "max_delta": 0.0}},
                           "metric_divergence_ids": ["r001", "r002"]}]}
    v = harness._verdict(report, poisoned=None)
    assert v["ok"] is True
    assert v["informational"]["metric_divergence_ids_total"] == 2


def test_a_distance_change_with_identical_ids_is_a_divergence(harness):
    """⛔ The case an id-only comparison calls parity — measured, not invented.

    The first `--poison vector` run negated one stored vector: its distance to
    the query moved from 0.0 to 2.0 while every unrelated row in the corpus sat
    15-25 away, so the returned IDS and their ORDER were unchanged and the
    harness reported "no divergence". A store can hand back the same list with
    different values, and that is a different store.
    """
    truth_rows = [("a", 0.10), ("b", 0.20), ("c", 0.30)]
    same = harness.compare(truth_rows, ["a", "b", "c"],
                           candidate_distances=[0.10, 0.20, 0.30])
    assert same["distance"]["ok"] is True

    moved = harness.compare(truth_rows, ["a", "b", "c"],
                            candidate_distances=[2.00, 0.20, 0.30])
    assert moved["ids_equal"] is True and moved["order"] == "exact"
    assert moved["distance"]["ok"] is False
    assert moved["distance"]["worst"] == "a"
    assert moved["distance"]["max_delta"] == pytest.approx(1.9)


def test_the_verdict_reds_on_a_distance_only_divergence(harness):
    report = {"queries": [{"query": "q0",
                           "record": {"ids_equal": True, "order": "exact",
                                      "recall": 1.0,
                                      "distance": {"ok": False,
                                                   "max_delta": 1.9}},
                           "graph_index": {"ids_equal": True, "order": "exact",
                                           "recall": 1.0,
                                           "distance": {"ok": True,
                                                        "max_delta": 0.0}},
                           "graph_scan_vs_euclidean": {
                               "ids_equal": True, "order": "exact",
                               "recall": 1.0,
                               "distance": {"ok": True, "max_delta": 0.0}},
                           "metric_divergence_ids": []}]}
    v = harness._verdict(report, poisoned=None)
    assert v["ok"] is False
    assert any("distance_delta=1.9" in f for f in v["failures"])


def test_a_non_loopback_store_is_refused_without_an_explicit_opt_in(harness):
    """The harness WRITES. A fire-and-forget cleanup is not a safety property."""
    with pytest.raises(harness.CannotRun) as exc:
        harness._assert_loopback("db.example.com", "record database", False)
    assert "NON-LOCAL" in str(exc.value)
    assert "--allow-remote-writes" in str(exc.value)
    # …and the deliberate act is allowed, so the guard cannot wedge a real run.
    harness._assert_loopback("db.example.com", "record database", True)
    for host in ("127.0.0.1", "localhost", "::1"):
        harness._assert_loopback(host, "record database", False)


def test_a_reordered_poison_moves_the_distance_WITH_the_row(harness):
    """The misalignment the review caught: ids permuted, distances not.

    The first ids-only poison swapped `rec_ids[0]` with `rec_ids[j]` while
    `candidate_distances` stayed put, so `zip` paired id *i* with distance *j*
    and the comparator reported a 0.8967 `distance_delta` that no store ever
    produced. A re-order must move the row, distance included.
    """
    truth_rows = [("a", 0.1), ("b", 0.2), ("c", 0.3)]
    pairs = [("a", 0.1), ("b", 0.2), ("c", 0.3)]
    poisoned = harness.poison(pairs, truth_rows, "order")
    assert [k for k, _d in poisoned][0] == "b"
    cmp_ = harness.compare(truth_rows, [k for k, _d in poisoned],
                           candidate_distances=[d for _k, d in poisoned])
    assert cmp_["order"] == "diverged"
    assert cmp_["distance"]["ok"] is True, (
        "the re-order manufactured a distance delta — the pairs are misaligned "
        "again, and the falsifier's evidence would be an artifact")
    assert cmp_["distance"]["max_delta"] == pytest.approx(0.0)


def test_a_repeated_id_is_not_set_agreement(harness):
    """⛔ `set(cand) == set(truth)` is TRUE for `[a,b,c,a]`. Multiplicity counts.

    A leg returning `k+1` rows with one key repeated (a remainder appended to a
    primary answer, a fold join without DISTINCT) used to print PARITY with
    recall 1.0, because the positional loop is bounded by the shorter list.
    """
    truth_rows = [("a", 0.1), ("b", 0.2), ("c", 0.3)]
    cmp_ = harness.compare(truth_rows, ["a", "b", "c", "a"],
                           candidate_distances=[0.1, 0.2, 0.3, 0.1])
    assert cmp_["ids_equal"] is False
    assert cmp_["duplicates"] == ["a"]
    assert cmp_["count_equal"] is False


def test_a_short_distance_list_is_a_failure_not_a_truncated_pass(harness):
    """`zip` stops at the shorter side — so a length mismatch must fail loudly."""
    truth_rows = [("a", 0.1), ("b", 0.2), ("c", 0.3)]
    cmp_ = harness.compare(truth_rows, ["a", "b", "c"], candidate_distances=[0.1])
    assert cmp_["distance"]["ok"] is False
    assert "distances" in cmp_["distance"]["reason"]


def test_an_unmeasured_distance_fails_a_REQUIRED_arm(harness):
    """`None is False` is False — so `is False` was fail-open on absence."""
    report = {"queries": [{"query": "q0",
                           "record": {"ids_equal": True, "order": "exact",
                                      "recall": 1.0,
                                      "distance": {"ok": None}},
                           "graph_index": {"ids_equal": True, "order": "exact",
                                           "recall": 1.0},
                           "graph_scan_vs_euclidean": {
                               "ids_equal": True, "order": "exact",
                               "recall": 1.0,
                               "distance": {"ok": True, "max_delta": 0.0}},
                           "metric_divergence_ids": []}]}
    v = harness._verdict(report, poisoned=None)
    assert v["ok"] is False
    assert any("distance_ok=None" in f for f in v["failures"])


def test_an_empty_oracle_is_not_parity(harness):
    truth_rows: list[tuple[str, float]] = []
    cmp_ = harness.compare(truth_rows, [])
    assert cmp_["recall"] is None


def test_the_cli_refuses_argument_shapes_that_cannot_measure(harness):
    """`--k 0` used to report PARITY over an empty comparison."""
    class A:
        graph_port = 1
        record_dsn = "postgresql://u:p@127.0.0.1/db"
        k = 0
        queries = 1
        n_random = 48
    with pytest.raises(harness.CannotRun):
        harness._validate_args(A())
    A.k, A.queries = 10, 0
    with pytest.raises(harness.CannotRun):
        harness._validate_args(A())
    A.queries, A.n_random = 6, 0
    with pytest.raises(harness.CannotRun):
        harness._validate_args(A())


def test_a_record_leg_failure_is_a_named_divergence_not_a_traceback(harness):
    """`VectorShortfall`/`RecordVectorError` escape `main` as exit 1 — the code
    this module reserves for "a comparison diverged", with no diagnosis."""
    report = {"queries": [{"query": "q0",
                           "record_error": "VectorShortfall: 8 of 10",
                           "record": {"ids_equal": True, "order": "exact",
                                      "recall": 1.0,
                                      "distance": {"ok": True, "max_delta": 0.0}},
                           "graph_index": {"ids_equal": True, "order": "exact",
                                           "recall": 1.0},
                           "graph_scan_vs_euclidean": {
                               "ids_equal": True, "order": "exact",
                               "recall": 1.0,
                               "distance": {"ok": True, "max_delta": 0.0}},
                           "metric_divergence_ids": []}]}
    v = harness._verdict(report, poisoned=None)
    assert v["ok"] is False
    assert any("record leg FAILED" in f for f in v["failures"])


def test_the_scan_arm_tolerates_the_inversion_it_performs(harness):
    """`1/(1+d)` inverted is ill-conditioned for large d — a false RED otherwise.

    A relative float32 error `eps` in the score becomes ~`(1+d)^2 * eps` in the
    distance: at d = 100 that is ~1e-3, so the record arm's 1e-4 tolerance would
    fail a CORRECT scan on any corpus whose rows are far apart.
    """
    truth_rows = [("a", 100.0), ("b", 101.0)]
    ok = harness.compare(truth_rows, ["a", "b"],
                         candidate_distances=[100.0008, 101.0008], distance_tol=1e-3)
    assert ok["distance"]["ok"] is True
    strict = harness.compare(truth_rows, ["a", "b"],
                             candidate_distances=[100.0008, 101.0008], distance_tol=1e-4)
    assert strict["distance"]["ok"] is False


def test_a_none_distance_is_not_a_verified_one(harness):
    """The second branch of the same fail-open the short-list guard closes.

    `(embedding_vec <=> %s)` is NULL for a row whose generated column is NULL, and
    `ORDER BY … ASC` puts those last — so an all-None distance list is reachable
    exactly when the answer is SHORT, and it used to publish `ok: True, max_delta:
    0.0`.
    """
    truth_rows = [("a", 0.1), ("b", 0.2), ("c", 0.3)]
    cmp_ = harness.compare(truth_rows, ["a", "b", "c"],
                           candidate_distances=[None, None, None])
    assert cmp_["distance"]["ok"] is False
    assert cmp_["distance"]["unmeasured"] == 3

    partly = harness.compare(truth_rows, ["a", "b", "c"],
                             candidate_distances=[0.1, None, 0.3])
    assert partly["distance"]["ok"] is False
    assert partly["distance"]["unmeasured"] == 1


def test_the_order_poison_crosses_a_distance_bucket(harness):
    """On a one-tie-group answer a same-bucket swap is classified `tie` — the
    falsifier would be masked by the comparator's own policy (measured live:
    `q_tie` and `q01` both reported `order="tie"` under poison-order)."""
    truth_rows = [("a", 0.1), ("b", 0.1), ("c", 0.9)]
    pairs = [("a", 0.1), ("b", 0.1), ("c", 0.9)]
    poisoned = harness.poison(pairs, truth_rows, "order")
    cmp_ = harness.compare(truth_rows, [k for k, _d in poisoned],
                           candidate_distances=[d for _k, d in poisoned])
    assert cmp_["order"] == "diverged", (
        "the swap stayed inside the tie group, so the poison is invisible")


def test_the_vector_poison_moves_the_victim_beyond_the_whole_corpus(harness):
    """The one poison whose mechanism is not in `poison()` — pinned here because
    the CI weight row claims it is covered."""
    rows = harness.build_corpus(n_random=8)
    victim = next(r for r in rows if r.key == "anchor")
    perturbed = harness.perturb_for_vector_poison(rows)
    moved = next(r for r in perturbed if r.key == "anchor")
    assert moved.embedding != victim.embedding
    query = harness._unit(0)
    far = harness._cosine_distance(moved.embedding, query)
    others = [harness._cosine_distance(r.embedding, query) for r in rows
              if r.key not in ("anchor",) and r.eligible(len(query))]
    assert far > max(others), (
        "the perturbed row is still inside the corpus' distance range, so it "
        "would not leave the window and the poison would be invisible")
    # …and it does not touch any other row.
    assert [r.key for r in perturbed] == [r.key for r in rows]
    assert all(a.embedding == b.embedding for a, b in zip(perturbed, rows)
               if a.key != "anchor")

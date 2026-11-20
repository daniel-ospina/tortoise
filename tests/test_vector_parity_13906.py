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

import ast
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
    # ONE assertion, not two spellings of the same disjunction: `ids_equal and
    # order == "exact"` is exactly the PARITY the falsifier must never produce.
    assert not (cmp_["ids_equal"] and cmp_["order"] == "exact"), (
        f"poison {kind!r} produced NO divergence — the harness is blind to it")


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
    the query moved from 0.0 to 2.000, while the eligible rows that are not
    near-copies sat 7.224-47.653 away under euclidean (the scan's metric), so the
    returned IDS and their ORDER were unchanged and the harness reported "no
    divergence". A store can hand back the same list with different values, and
    that is a different store.
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


def test_a_dsn_whose_host_cannot_be_parsed_is_refused_not_skipped(harness):
    """⛔ THE DECISION, not the leaf — the guard must DECIDE, not skip.

    `_assert_loopback` was already pinned; the `if host:` in front of it was not,
    and `host` is `None` for two legal libpq spellings that both reach a REMOTE
    host — so the guard was skipped exactly when it could not decide, on the path
    where `run()` seeds that store and `teardown()` deletes rows by `org_id`.
    """
    unresolvable = (
        # URI with the host in the query string (PostgreSQL §34.1.1)
        "postgresql:///tortoise_record?host=db.prod.example.com",
        # …and the whitespace conninfo, the other legal spelling
        "host=db.prod.example.com dbname=tortoise_record",
    )
    for dsn in unresolvable:
        assert harness.urlsplit(dsn).hostname is None, dsn  # ← the SKIP condition
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value)
        # …and the deliberate act still works, so a real remote run is possible.
        harness._assert_local_record_dsn(dsn, True)

    for dsn in ("postgresql://u:p@127.0.0.1:5432/tortoise_record",
                "postgresql://u:p@localhost/tortoise_record"):
        harness._assert_local_record_dsn(dsn, False)
    harness._assert_local_record_dsn(None, False)

    # The CALL SITE too: `run()` must go through the decision, or the guard is
    # one `if host:` away from being skipped again.
    tree = ast.parse(MODULE_PATH.read_text())
    run_node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "run")
    called = {(c.func.attr if isinstance(c.func, ast.Attribute)
               else getattr(c.func, "id", ""))
              for c in ast.walk(run_node) if isinstance(c, ast.Call)}
    assert "_assert_local_record_dsn" in called, sorted(called)


def test_a_host_in_the_query_string_overrides_the_authority_and_is_refused(harness):
    """⛔ THE AUTHORITY IS NOT THE ANSWER — libpq dials the QUERY STRING.

    H1 replaced `if host:` with a decided refusal, but it still decided from
    `urlsplit(...).hostname` alone. libpq's URI form lets a `host=`/`hostaddr=` in
    the QUERY STRING override the authority, and `hostaddr` is the address
    actually dialled — so `postgresql://127.0.0.1/db?host=db.prod.example.com`
    read as LOCAL here while `psycopg2.connect()` reached a remote record. The
    same DSN then seeds an organization, a graph row, node identities and `point`
    rows into that store, and `teardown()` deletes rows by `org_id`: a guard that
    waves it through is a no-op on the destructive path R1 advertises.

    Measured on libpq's own resolver (`PQconninfoParse`), which is why the
    precedence below is the expected value and not a reading of the docs:

        postgresql://127.0.0.1/db?host=db.prod.example.com -> host=db.prod.example.com
        postgresql://127.0.0.1/db?hostaddr=203.0.113.9     -> hostaddr=203.0.113.9

    ⛔ The mutant this pins: make `_assert_local_record_dsn` read
    `urlsplit(dsn).hostname` again (the pre-fix body) and the `remote` loop below
    goes green on every entry — the two `?host=`/`?hostaddr=` cases are the ones
    no pre-existing test could see.
    """
    assert harness._record_dsn_hosts(
        "postgresql://127.0.0.1/db?host=db.prod.example.com"
    ) == ["db.prod.example.com", "127.0.0.1"]
    assert harness._record_dsn_hosts(
        "postgresql://127.0.0.1/db?hostaddr=203.0.113.9"
    ) == ["203.0.113.9", "127.0.0.1"]

    remote = (
        # the two shapes this pin exists for: loopback AUTHORITY, remote DIAL
        "postgresql://127.0.0.1:5432/tortoise_record?host=db.prod.example.com",
        "postgresql://localhost/tortoise_record?hostaddr=203.0.113.9",
        # a failover list — the remote member is a store this run may reach
        "postgresql://127.0.0.1/tortoise_record?host=127.0.0.1,db.prod.example.com",
        # …and H1's two spellings, now refused by the host's own name
        "postgresql:///tortoise_record?host=db.prod.example.com",
        "host=db.prod.example.com dbname=tortoise_record",
        "hostaddr=203.0.113.9 dbname=tortoise_record",
        # AND A `service` IS A HOST THIS CANNOT SEE. `pg_service.conf` fills
        # every UNSET option, `hostaddr` included, so a loopback DSN with
        # `service=` dials whatever that entry says - measured with libpq itself
        # (`psql` names the host it tried, service file `hostaddr=203.0.113.9`):
        #   ?service=prod  /  ?host=127.0.0.1&service=prod  -> dials 203.0.113.9
        # ...and `PGSERVICE` in the environment does it to a DSN that never
        # mentions it. All three PASSED the guard before this pin (review
        # round 10), the same fail-open class as K1 on the same destructive path.
        "postgresql://127.0.0.1:5432/tortoise_record?service=prod",
        "postgresql://127.0.0.1:5432/tortoise_record?host=127.0.0.1&service=prod",
        "service=prod dbname=tortoise_record",
    )
    for dsn in remote:
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value), dsn
        # …and the deliberate act still reaches a real remote run.
        harness._assert_local_record_dsn(dsn, True)

    local = (
        "postgresql://127.0.0.1:5432/tortoise_record?host=127.0.0.1",
        "postgresql://tortoise@localhost/tortoise_record?hostaddr=127.0.0.1&sslmode=disable",
        "postgresql:///tortoise_record?host=localhost",
        "postgresql://u:p@localhost/tortoise_record?host=[::1]",
        "dbname=tortoise_record host=127.0.0.1",
    )
    for dsn in local:
        harness._assert_local_record_dsn(dsn, False)

    # A DSN naming NO host is not a local one — it is one this cannot PROVE.
    for dsn in ("postgresql:///tortoise_record", "dbname=tortoise_record"):
        with pytest.raises(harness.CannotRun):
            harness._assert_local_record_dsn(dsn, False)

    # The DECISION must remain the helper — an inlined authority read is exactly
    # the shape that shipped the fail-open, so it cannot come back silently.
    tree = ast.parse(MODULE_PATH.read_text())
    guard = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "_assert_local_record_dsn")
    called = {(c.func.attr if isinstance(c.func, ast.Attribute)
               else getattr(c.func, "id", ""))
              for c in ast.walk(guard) if isinstance(c, ast.Call)}
    assert "_record_dsn_hosts" in called, sorted(called)


def test_a_service_dsn_cannot_prove_it_is_local(harness, monkeypatch):
    """A `service=` HANDS THE DIAL TO A FILE THIS HARNESS CANNOT READ.

    Found in review round 10, after the K1 fix had been described as closing the
    guard: `host`/`hostaddr` are not the only sources of the dialled address.
    `parseServiceInfo` fills every option the conninfo left UNSET from
    `pg_service.conf`, so a LOOPBACK DSN with `service=prod` dials that entry's
    `hostaddr`. Measured with libpq itself (a service file carrying
    `hostaddr=203.0.113.9`, `psql` naming the host it tried):

        postgresql://127.0.0.1:1/db?service=prod                -> 203.0.113.9
        postgresql://127.0.0.1:1/db?host=127.0.0.1&service=prod -> 203.0.113.9
        PGSERVICE=prod  + a plain loopback DSN                  -> 203.0.113.9

    while `_record_dsn_hosts` returned the loopback host and the guard PASSED. It
    was also inconsistent with itself: the conninfo spelling `service=prod ...`
    was refused, but only because it named no host.
    """
    monkeypatch.delenv("PGSERVICE", raising=False)
    service_dsns = (
        "postgresql://127.0.0.1:5432/tortoise_record?service=prod",
        "postgresql://127.0.0.1:5432/tortoise_record?host=127.0.0.1&service=prod",
        "service=prod dbname=tortoise_record",
        "service=prod host=127.0.0.1 dbname=tortoise_record",
    )
    for dsn in service_dsns:
        assert harness.RECORD_DSN_SERVICE in harness._record_dsn_hosts(dsn), dsn
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value), dsn
        # ...and a deliberate remote run still works.
        harness._assert_local_record_dsn(dsn, True)

    # AND `PGSERVICE` DOES IT TO A DSN THAT NEVER MENTIONS A SERVICE - libpq
    # expands it from the ENVIRONMENT, so the DSN alone is not the question.
    monkeypatch.setenv("PGSERVICE", "prod")
    for dsn in ("postgresql://127.0.0.1:5432/tortoise_record",
                "host=127.0.0.1 dbname=tortoise_record"):
        assert harness.RECORD_DSN_SERVICE in harness._record_dsn_hosts(dsn), dsn
        with pytest.raises(harness.CannotRun):
            harness._assert_local_record_dsn(dsn, False)
        harness._assert_local_record_dsn(dsn, True)

    # ...and WITHOUT it the same DSNs are local again - so the refusal is about
    # the service, not a blanket block on loopback DSNs.
    monkeypatch.delenv("PGSERVICE")
    for dsn in ("postgresql://127.0.0.1:5432/tortoise_record",
                "host=127.0.0.1 dbname=tortoise_record"):
        harness._assert_local_record_dsn(dsn, False)


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
    assert next(k for k, _d in poisoned) == "b"
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


def test_the_cli_refuses_a_corpus_too_small_to_fill_k(harness):
    """A run whose oracle cannot fill `k` measures a width it never asked for.

    `--n-random 4 --k 10` seeds **8** eligible rows: `truth()` returns 8,
    `compare()` reads `k` only to default it, and both legs then report
    `ids_equal / order=exact / recall=1.0` while `_verdict` returns `ok: True` —
    measured in review before this refusal existed. That is the under-return
    shape (#13911) the harness exists to EXPOSE, printed as PARITY.
    """
    rows = harness.build_corpus(13906, 4)
    eligible = [r.key for r in rows
                if r.tenant == "ours" and r.eligible(harness.DIM)]
    assert eligible == ["r000", "r001", "r002", "r003",
                        "tie0", "tie1", "tie2", "anchor"], eligible

    class A:
        graph_port = 1
        record_dsn = "postgresql://u:p@127.0.0.1/db"
        k = 10
        queries = 1
        n_random = 4
        seed = 13906
    with pytest.raises(harness.CannotRun) as refused:
        harness._validate_args(A())
    assert "eligible" in str(refused.value), str(refused.value)

    # The refusal is about the WIDTH, not the corpus size: the same 8 rows are
    # enough for `--k 8`, and the shipped defaults are enough for `--k 10`.
    A.k = 8
    harness._validate_args(A())
    A.k, A.n_random = 10, 48
    harness._validate_args(A())


def test_the_tie_tolerance_is_tight_enough_to_see_a_re_rank(harness):
    """`TIE_TOL` decides whether a re-rank is a `tie` (a PASS) or a divergence.

    Widening it 1e-6 → 1e-3 left every pin green (measured in review), so the
    tie policy could be made wide enough to excuse a genuine cross-distance
    re-rank with no falsifier asleep. Two distances 2e-6 apart are BELOW the
    bucket width and must still report `diverged`.
    """
    truth_rows = [("a", 0.1), ("b", 0.1 + 2e-6), ("c", 0.5)]
    assert harness.TIE_TOL < (0.1 + 2e-6) - 0.1
    cmp_ = harness.compare(truth_rows, ["b", "a", "c"])
    assert cmp_["order"] == "diverged", (
        "a re-rank across two distances just outside TIE_TOL was excused as a "
        f"tie (TIE_TOL={harness.TIE_TOL!r}) — the tie bucket is now wide enough "
        "to hide a genuine ranking change")
    # ...and the policy must NOT be tightened into reddening exact ties: this is
    # the same assertion pair `test_a_within_tie_permutation_is_NOT_a_divergence`
    # makes, restated here so the two bounds sit side by side.
    same = harness.compare([("a", 0.1), ("b", 0.1), ("c", 0.5)],
                           ["b", "a", "c"])
    assert same["order"] == "tie"


def test_the_scan_arm_reads_its_tolerance_from_the_one_constant(harness):
    """The REQUIRED scan arm's tolerance is a GATE, pinned at its call site.

    `test_the_scan_arm_tolerates_the_inversion_it_performs` pins `compare()` at
    two tolerances but never reads the literal `run()` passes — so widening that
    literal to 10.0 made the arm's value check inert with 30/30 green (measured
    in review). `_scan_arm` is the seam `run()` calls; `compare` reports the
    tolerance it was given, so this is assertable without a store.
    """
    truth_rows = [("a", 100.0), ("b", 101.0)]
    got = harness._scan_arm(truth_rows, ["a", "b"],
                            [100.0008, 101.0008], k=2)
    assert got["distance"]["tolerance"] == harness.SCAN_DISTANCE_TOL
    assert harness.SCAN_DISTANCE_TOL <= 1e-2, (
        "the scan arm's tolerance left the `1/(1+d)` inversion's arithmetic "
        f"({harness.SCAN_DISTANCE_TOL!r}) — past 1e-2 the value check stops "
        "being a check")

    # DISCRIMINATING, not decorative: a delta INSIDE the tolerance passes, a
    # delta FAR inside what a widened tolerance would swallow must be red.
    inside = harness._scan_arm(truth_rows, ["a", "b"],
                              [100.0001, 101.0001], k=2)
    assert inside["distance"]["ok"] is True, inside["distance"]
    far = harness._scan_arm(truth_rows, ["a", "b"],
                            [100.5, 101.5], k=2)
    assert far["distance"]["ok"] is False, (
        "a half-unit distance error passed the required scan arm — its "
        f"tolerance ({far['distance']['tolerance']!r}) no longer discriminates")


def test_run_routes_the_required_scan_arm_through_the_seam(harness):
    """The CALL SITE, not only the seam — the pre-fix mutant, one function over.

    Round 5 of #13914 reintroduced the widened tolerance at `run()`'s call site
    instead of inside `_scan_arm` and measured **33/33 green**: the pin above
    reaches the constant and the seam, so a `compare(..., distance_tol=10.0)`
    spelled directly in `run()` is invisible to it. That is the same defect the
    round filed against the `CORE_ALSO` entry — a claim of coverage the pin does
    not have — so `run()`'s body is READ, the way this repo's other
    production-consumer pins read production source rather than restating it.
    """
    tree = ast.parse(MODULE_PATH.read_text())
    run = next(node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name == "run")
    seams, spelled = 0, []
    for node in ast.walk(run):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", ""))
        if name == "_scan_arm":
            seams += 1
        elif name == "compare":
            spelled += [kw.arg for kw in node.keywords if kw.arg == "distance_tol"]
    assert seams == 1, (
        f"`run()` must route the REQUIRED graph-scan arm through `_scan_arm` "
        f"exactly once, found {seams} call(s) — inlining `compare()` here is how "
        f"the tolerance leaves the pinned constant")
    assert spelled == [], (
        f"`run()` passes `distance_tol` itself ({spelled}) — the number then has "
        f"two spellings and only one is pinned; read it from SCAN_DISTANCE_TOL "
        f"via `_scan_arm`")


def test_a_none_distance_is_not_a_verified_one(harness):
    """The second branch of the same fail-open the short-list guard closes.

    ⛔ The `None` arm is DEFENSIVE, not reachable through the record leg:
    `record_vector.vector_search` builds `WHERE t.embedding_vec IS NOT NULL`
    (`tortoise/record_vector.py:2671`), so `(embedding_vec <=> %s)` cannot be NULL
    for a served row. (An earlier draft of this docstring claimed it was
    reachable, and an `ORDER BY … ASC` that supposedly sorted the NULLs last —
    false, and it invited "fixing" a correct guard on a false premise.) The
    REACHABLE unmeasurable input is NaN; see the test below.
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
    # ⛔ ASSERT THE EUCLIDEAN LEG TOO — a cosine-only assertion cannot tell `-100`
    # from the pre-fix negation. Cosine is scale-invariant, so EVERY negative
    # multiple of the victim scores exactly 2.0 (measured: `-1` and `-100` both
    # 2.000 cosine), while only `-100` clears the corpus under euclidean
    # (`-1` = 2.000, inside the 7.224-47.653 range the scan ranks against).
    # A cosine-only pin left the pre-fix mutant 28/28 green.
    others = [r for r in rows
              if r.key != "anchor" and r.eligible(len(query))]
    far_eu = harness._euclidean_distance(moved.embedding, query)
    far_co = harness._cosine_distance(moved.embedding, query)
    assert far_co > max(harness._cosine_distance(r.embedding, query) for r in others), (
        "the perturbed row is still inside the corpus' COSINE range, so the "
        "index arm would keep it in the window")
    assert far_eu > max(harness._euclidean_distance(r.embedding, query) for r in others), (
        "the perturbed row is still inside the corpus' EUCLIDEAN range — the "
        "metric the gated scan arm ranks by — so the poison is invisible there")
    # …and it does not touch any other row.
    assert [r.key for r in perturbed] == [r.key for r in rows]
    assert all(a.embedding == b.embedding
               for a, b in zip(perturbed, rows, strict=True)
               if a.key != "anchor")


def test_a_nan_distance_is_not_a_verified_one(harness):
    """The `None` twin, one input type over — and the reachable one.

    `delta > worst_delta` is False for NaN, so an all-NaN arm published
    `ok: True, max_delta: 0.0`. Not hypothetical: pgvector answers **NaN**, not
    NULL, for a zero-norm vector (measured: `SELECT '[0,0,0]'::vector <=>
    '[1,0,0]'::vector` → `NaN`), so one zero-norm `embedding_vec` in a tenant
    turned a REQUIRED arm green on the one value it cannot score.
    """
    truth_rows = [("a", 0.1), ("b", 0.2), ("c", 0.3)]
    cmp_ = harness.compare(truth_rows, ["a", "b", "c"],
                           candidate_distances=[float("nan")] * 3)
    assert cmp_["distance"]["ok"] is False
    assert cmp_["distance"]["unmeasured"] == 3

    inf = harness.compare(truth_rows, ["a", "b", "c"],
                          candidate_distances=[0.1, float("inf"), 0.3])
    assert inf["distance"]["ok"] is False
    assert inf["distance"]["unmeasured"] == 1

    verdict = harness._verdict({"queries": [{
        "query": "q0",
        "record": {"ids_equal": True, "order": "exact", "recall": 1.0,
                   "distance": cmp_["distance"]},
        "graph_scan_vs_euclidean": {"ids_equal": True, "order": "exact",
                                    "recall": 1.0,
                                    "distance": {"ok": True,
                                                 "max_delta": 0.0}},
        "metric_divergence_ids": []}], "arms": {}}, poisoned=None)
    assert verdict["ok"] is False


def _reachable_entry(harness, *, record_error=None, corrupt_record=False,
                    nan_distances=False, query="q0"):
    """An entry in the shape `run()` ACTUALLY produces — built by `compare` itself.

    ⛔ GENERATED, NOT HAND-WRITTEN, and that is the whole lesson of this pin. The
    first version of it paired `record_error` with a GREEN record arm (`ids_equal
    True, recall 1.0`) — a state `run()` never emits while that key is set, so it
    proved nothing about the reachable path. `run()` answers a raised record leg
    with `rec_answers[qid] = []` (then `compare(t_cos, [])`), and THIS fixture was
    green while that real state still printed POISON DETECTED (measured in review,
    round 8). The fixture must be produced by the comparator, not imagined.
    """
    rows = harness.build_corpus(13906, 4)
    qvec = harness._queries(rows, 2)[0][1]
    t_cos = harness.truth(rows, qvec, 8, metric="cosine")
    t_euc = harness.truth(rows, qvec, 8, metric="euclidean")
    cos_ids = [key for key, _ in t_cos]
    cos_dists = [d for _, d in t_cos]
    if record_error is not None:
        # exactly what `run()` does when `record.read` raises
        rec = harness.compare(t_cos, [], candidate_distances=[])
    elif nan_distances:
        rec = harness.compare(t_cos, cos_ids,
                              candidate_distances=[float("nan")] * len(cos_ids))
    elif corrupt_record:
        swapped = [cos_ids[1], cos_ids[0], *cos_ids[2:]]
        rec = harness.compare(t_cos, swapped, candidate_distances=cos_dists)
    else:
        rec = harness.compare(t_cos, cos_ids, candidate_distances=cos_dists)
    entry = {"query": query, "record": rec, "graph_index": rec,
             "graph_scan_vs_euclidean": harness.compare(
                 t_euc, [key for key, _ in t_euc],
                 candidate_distances=[d for _, d in t_euc]),
             "graph_scan_vs_cosine": harness.compare(t_cos, cos_ids),
             "metric_divergence_ids": []}
    if record_error is not None:
        entry["record_error"] = record_error
    return entry


def test_each_cannot_measure_signal_makes_an_arm_unmeasured(harness):
    """The three signals `_arm_failure` decides on, one case each.

    ⛔ EACH IS PINNED SEPARATELY because they BACK EACH OTHER UP: a raised leg is
    also an empty answer (no rows compared), so removing one guard alone still
    leaves the reported property intact — and a pin on the property alone would
    stay green, i.e. the guard it just lost would be untested. Here each signal is
    observed in the state where it alone decides.
    """
    rows = harness.build_corpus(13906, 4)
    qvec = harness._queries(rows, 2)[0][1]
    t_cos = harness.truth(rows, qvec, 8, metric="cosine")
    cos_ids = [key for key, _ in t_cos]
    cos_dists = [d for _, d in t_cos]

    # (1) A RAISED LEG OUTRANKS A MEASURED-LOOKING COMPARISON. `run()` answers a
    # raise with `[]` today, so the two coincide — but a leg that returned partial
    # answers and THEN raised produces exactly this, and the divergence is a
    # consequence of the failure, not evidence the poison was seen.
    lookalike = harness.compare(t_cos, [cos_ids[1], cos_ids[0], *cos_ids[2:]],
                                candidate_distances=cos_dists)
    msg, measured = harness._arm_failure(
        "q0", "record", lookalike, oracle="truth_cosine", error="VectorShortfall:")
    assert msg and "FAILED" in msg
    assert measured is False, "a raised leg was scored as a MEASURED divergence"

    # (2) EVERY DISTANCE UNMEASURABLE (pgvector's NaN on a zero-norm vector).
    nan = harness.compare(t_cos, cos_ids,
                          candidate_distances=[float("nan")] * len(cos_ids))
    msg, measured = harness._arm_failure("q0", "record", nan, oracle="truth_cosine")
    assert msg and measured is False, nan["distance"]

    # (3) ZERO ROWS COMPARED.
    empty = harness.compare(t_cos, [], candidate_distances=[])
    msg, measured = harness._arm_failure("q0", "record", empty, oracle="truth_cosine")
    assert msg and measured is False, empty["distance"]

    # (4) An EMPTY ORACLE is an absence of an answer to compare against.
    _, measured = harness._arm_failure("q0", "record",
                                      harness.compare([], []), oracle="truth_cosine")
    assert measured is False

    # (5) …and a genuine divergence IS measured, so (1)-(4) cannot be satisfied
    # by a function that answers False to everything.
    real = harness.compare(t_cos, [cos_ids[1], cos_ids[0], *cos_ids[2:]],
                           candidate_distances=cos_dists)
    msg, measured = harness._arm_failure("q0", "record", real, oracle="truth_cosine")
    assert msg and measured is True, real


def test_a_poisoned_run_that_could_not_measure_is_not_a_detection(harness):
    """⛔ THE FALSIFIER'S PROOF may only be minted by a MEASURED arm.

    `poison_status` used to be `"POISON DETECTED" if not ok`, so EVERY failure
    counted as a detection — including the cannot-measure failures the verdict
    already names. Three readings, three answers, and the entries below are the
    states `run()` really emits (a raised record leg, an all-NaN distance list),
    not a hand-built approximation of them.
    """
    raised = {"k": 8, "queries": [
        _reachable_entry(harness, record_error="VectorShortfall: 8 of 10")] * 6}
    v = harness._verdict(raised, poisoned="id")
    assert v["ok"] is False
    assert v["poison_status"].startswith("POISON UNMEASURED"), (
        f"the state a RAISED record leg actually produces was reported as "
        f"{v['poison_status']!r} with failures {v['failures']}")
    assert v["unmeasured"] is True

    # The other reachable unmeasured input: every distance NaN (pgvector answers
    # NaN for a zero-norm vector), with the ids coincidentally intact.
    nan = {"k": 8, "queries": [_reachable_entry(harness, nan_distances=True)]}
    nv = harness._verdict(nan, poisoned="order")
    assert nv["poison_status"].startswith("POISON UNMEASURED"), nv["poison_status"]
    assert nv["unmeasured"] is True

    measured = {"k": 8, "queries": [_reachable_entry(harness, corrupt_record=True)]}
    hit = harness._verdict(measured, poisoned="id")
    assert hit["poison_status"] == "POISON DETECTED", hit["failures"]
    # …and the flag is ABSENT, not False, so the five committed artifacts keep
    # replaying bit-identically through this verdict (checked: all five do).
    assert "unmeasured" not in hit, hit

    # A MIXED run still counts as a detection: a real divergence on a measured
    # arm is a detection however many other queries could not be measured.
    mixed = {"k": 8, "queries": [
        _reachable_entry(harness, corrupt_record=True),
        _reachable_entry(harness, record_error="VectorShortfall: 8 of 10",
                         query="q1")]}
    both = harness._verdict(mixed, poisoned="order")
    assert both["poison_status"] == "POISON DETECTED", both["poison_status"]
    assert "unmeasured" not in both, both


def test_an_empty_answer_is_not_a_passed_distance_check(harness):
    """ZERO rows compared is not a value check that passed (#13914 round 8).

    `zip([], [])` runs zero times, so `worst_delta` stayed `0.0` and an empty
    candidate list published `distance.ok: True, max_delta: 0.0`. That is what
    made a raised record leg look like a MEASURED divergence: `ids_equal False`
    with a GREEN distance check.
    """
    rows = harness.build_corpus(13906, 4)
    qvec = harness._queries(rows, 2)[0][1]
    t_cos = harness.truth(rows, qvec, 8, metric="cosine")
    empty = harness.compare(t_cos, [], candidate_distances=[])
    assert empty["distance"]["ok"] is not True, empty["distance"]
    assert "no candidate row" in empty["distance"]["reason"]


def test_a_poisoned_run_that_could_not_measure_exits_2(harness, monkeypatch):
    """The three readings must differ in the EXIT CODE, not only in the word.

    README §6's falsifier recipe is `for p in …; do … --poison $p; echo "exit=$?";
    done` — one bit per run. With an unmeasured run exiting 1 (the code for "a
    comparison diverged"), that one bit reads as "the poison was confirmed" for a
    run that never looked, which is exactly how a falsifier certifies itself.
    """
    def report(**entry_over):
        entry = _reachable_entry(harness, **entry_over)
        return {"k": 8, "queries": [entry],
                "verdict": harness._verdict({"k": 8, "queries": [dict(entry)]},
                                            poisoned="id")}

    argv = ["--record-dsn", "postgresql://u:p@127.0.0.1/db",
            "--graph-port", "1", "--poison", "id"]

    could_not_measure = report(record_error="VectorShortfall: 8 of 10")
    monkeypatch.setattr(harness, "run", lambda _args: could_not_measure)
    assert harness.main(argv) == 2, (
        "a poisoned run that could not measure exited 1 — the falsifier's recipe "
        "reads that as 'poison confirmed'")

    detected = report(corrupt_record=True)
    monkeypatch.setattr(harness, "run", lambda _args: detected)
    assert harness.main(argv) == 1, "a MEASURED detection must keep exit 1"


def test_the_indexed_arm_refusal_is_not_inert(harness):
    """\"Index ready\" may not be a conjunct of the refusal (#13914 round 6).

    `if ready and mechanisms != ["index"]` was a SHIPPED defect: a timed-out
    `wait_index_ready` returned False, the conjunct was False, and the arm was
    published as `run_vector_query HNSW` while measuring the `#8429` scan
    fall-through — exit 0, PARITY, about the scan. A timed-out readiness is the
    case the guard exists for, so it must refuse there.
    """
    harness._require_index_arm(["index"], True)
    # the arm IS the index: readiness only picks the message
    harness._require_index_arm(["index"], False)
    for mechanisms, ready in ((["scan_fallback"], False),
                              (["scan_fallback"], True),
                              (["index", "scan_fallback"], True),
                              ([], True)):
        with pytest.raises(harness.CannotRun) as refused:
            harness._require_index_arm(mechanisms, ready)
        assert "not measuring" in str(refused.value) or "scan" in str(refused.value)

    # …and the CALL SITE: `run()` must go through the seam, or the inert form can
    # return one `if` away from it (the `_scan_arm` lesson, same round).
    tree = ast.parse(MODULE_PATH.read_text())
    run_node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "run")
    called = {(c.func.attr if isinstance(c.func, ast.Attribute)
               else getattr(c.func, "id", ""))
              for c in ast.walk(run_node) if isinstance(c, ast.Call)}
    assert "_require_index_arm" in called, sorted(called)


def test_an_empty_report_is_not_parity(harness):
    """`--queries < 1` is refused at the CLI, but the VERDICT must refuse it too:
    zero measurements is the strongest possible absence of evidence."""
    verdict = harness._verdict({"queries": [], "arms": {}}, poisoned=None)
    assert verdict["ok"] is False
    assert any("no queries" in f for f in verdict["failures"])


def test_a_verdict_over_an_oracle_shorter_than_k_is_not_parity(harness):
    """`_validate_args` refuses it, and the VERDICT refuses it too (#13914 P2).

    Measured before this guard: a `run()` reached with a short oracle reported
    `ids_equal / order=exact / recall=1.0` → `ok: True`, the under-return shape
    (#13911) printed as PARITY. Not reachable through `main()` today — which is
    exactly why it belongs in the verdict, per the empty-report rule below: the
    next caller of `run()` that skips validation must not inherit a green.
    """
    entry = {
        "query": "q0",
        "truth_cosine": ["a", "b", "c"],
        "record": {"ids_equal": True, "order": "exact", "recall": 1.0,
                   "distance": {"ok": True, "max_delta": 0.0}},
        "graph_scan_vs_euclidean": {"ids_equal": True, "order": "exact",
                                    "recall": 1.0,
                                    "distance": {"ok": True,
                                                 "max_delta": 0.0}},
        "metric_divergence_ids": [],
    }
    short = harness._verdict({"k": 10, "queries": [entry]}, poisoned=None)
    assert short["ok"] is False, (
        "an oracle of 3 rows against `--k 10` reported parity — the answer was "
        "compared over a width neither leg was asked for")
    assert any("the oracle served 3 rows" in f for f in short["failures"])

    # ...and it does not fire when the width is genuinely met, or when the report
    # carries no width to check (a caller-built fixture), so it cannot mask a
    # real verdict on the shipped shape.
    ok = harness._verdict({"k": 3, "queries": [entry]}, poisoned=None)
    assert ok["ok"] is True, ok["failures"]
    no_width = harness._verdict({"queries": [entry]}, poisoned=None)
    assert no_width["ok"] is True, no_width["failures"]


def test_the_float64_oracle_agrees_with_a_float32_store(harness):
    """The README's PRECISION cell had no producer — this is the producer.

    ⛔ MEASURED, NOT ASSERTED. The README claimed "float32 vs float64 agreement
    was checked explicitly (the fp32 oracle and the fp64 oracle returned the same
    top-10 on the probe query)" while `truth()` has ONE numeric path —
    `np.asarray(a, dtype=float)` — and no field in any committed artifact
    recorded the comparison: a measurement named in prose and performed nowhere.
    The claim is not cosmetic either: the stores do NOT share the oracle's
    precision (the graph writes `vecf32(...)`, the record column is
    `vector(1024)`), so a near tie that float32 rounds the other way would
    reorder the top-k and the record arm would then red for a PRECISION reason
    while the harness reported it as a store defect.

    So the round trip is performed here, on the corpus this harness actually
    seeds and every query it actually asks, in the one posture CI can run it —
    no stores. The comparison goes through the harness's OWN `compare()`, so
    the acceptable outcome is not "bit-identical" (it is not, and must not be
    asserted) but the rung's tie policy: the same ids, and any permutation
    inside one distance bucket rather than across buckets.

    ⛔ WHICH OF ITS TWO ASSERTIONS CARRIES THE POWER — recorded because the README
    cell cites this test as the tie-policy gate, and the `order != "diverged"`
    line CANNOT fire from the mechanism under test. `compare()` buckets at
    `TIE_TOL = 1e-6` while the float32-induced distance deltas on this corpus
    measure 2.06e-9 … 4.40e-9 — roughly 250x BELOW one bucket — so any permutation
    rounding can cause lands in the SAME bucket and is classified `tie` at worst.
    That line reds only on a corpus-scale move (measured: 0.9458), i.e. a fixture
    change. The real power is the `ids_equal` assertion (mutant: an extra
    near-query row → RED), and the tie-vs-cross-bucket discrimination is pinned
    by `test_a_within_tie_permutation_is_NOT_a_divergence` and
    `test_a_cross_distance_permutation_IS_a_divergence` — both named here so a
    reader does not take a defensive guard for the load-bearing one.

    ⛔ WHY IT CANNOT BE VACUOUS: `as_f32` is guarded by a non-vacuity assert
    (float32 rounding must change at least one coordinate), so the identity
    mutant — `as_f32` returning its argument, which would compare float64 with
    float64 and pass while measuring nothing — reds. What the assert buys is the
    same thing `--poison vector` buys on the live stores: a comparison that has
    only ever seen agreement has not been shown able to see disagreement, and
    `compare()`'s ability to red on a changed id set is pinned separately
    (`test_a_missing_id_is_reported_as_recall_loss`) — a different question from
    the tie policy named above, which those two tests cover.
    """
    import numpy as np

    rows = harness.build_corpus(seed=13906)
    queries = harness._queries(rows, 6)
    k = 10

    def as_f32(vec):
        # A float32 store does not keep float64: cast down and read back as the
        # float64 the oracle consumes, i.e. exactly what the store holds.
        return np.asarray(vec, dtype=np.float32).astype(float).tolist()

    rounded = [
        harness.Row(key=row.key,
                    embedding=None if row.embedding is None else as_f32(row.embedding),
                    status=row.status, outdated=row.outdated,
                    updated_at=row.updated_at, tenant=row.tenant)
        for row in rows
    ]
    assert any(row.embedding is not None and len(row.embedding) == harness.DIM
               for row in rounded), "the rounded corpus is empty — nothing measured"
    # ⛔ NON-VACUITY: `as_f32` must actually CAST. If it degenerated to a no-op
    # the whole test would compare float64 with float64 and pass while measuring
    # nothing — the "green about nothing" shape this file keeps closing.
    assert any(a != b for a, b in zip(
        [r.embedding for r in rows if r.embedding is not None],
        [r.embedding for r in rounded if r.embedding is not None], strict=True)), (
        "float32 rounding changed no coordinate — the comparison measured "
        "float64 against itself")

    for name, query in queries:
        reference = harness.truth(rows, query, k, metric="cosine")
        candidate = harness.truth(rounded, as_f32(query), k, metric="cosine")
        diff = harness.compare(
            reference,
            [key for key, _ in candidate],
            candidate_distances=[distance for _, distance in candidate])
        assert diff["ids_equal"], (
            f"{name}: rounding the corpus and the query to float32 changed WHICH "
            f"rows the oracle ranks, so a float32 store reds the record arm for a "
            f"precision reason rather than a defect: {diff}")
        # ⛔ A DEFENSIVE GUARD, NOT THE LOAD-BEARING ASSERTION — said here because
        # the README cell cites this line as the tie-policy gate. It CANNOT fire
        # from the mechanism under test: `compare()` buckets at `TIE_TOL = 1e-6`
        # and the float32-induced deltas on this corpus measure 2.06e-9…4.40e-9,
        # ~250x BELOW one bucket, so any permutation rounding causes lands in the
        # SAME bucket → `tie` at worst. It reds only on a corpus-scale move
        # (measured: 0.9458), i.e. a fixture change. The real power of this test
        # is `ids_equal` above (mutant: an extra near-query row → RED), and the
        # tie-vs-cross-bucket discrimination is pinned by
        # `test_a_within_tie_permutation_is_NOT_a_divergence` and
        # `test_a_cross_distance_permutation_IS_a_divergence` (review round 9).
        assert diff["order"] != "diverged", (
            f"{name}: float32 rounding moved a row ACROSS a distance bucket — a "
            f"re-order, not the tie permutation the rung allows: {diff}")


def _graph_store(harness, *, delete_error=None, catalogue=()):
    """A `GraphStore` whose transport is a fake, so teardown is testable DB-free.

    Built with `object.__new__` on purpose: `GraphStore.__init__` opens a real
    FalkorDB connection, and the property under test is what `reset()` does with
    a FAILING delete — which is exactly the state a live store cannot be asked
    to produce on demand.
    """
    store = object.__new__(harness.GraphStore)
    store.graph_name = "tortoise_parity_13906"

    class _Graph:
        def delete(self):
            if delete_error is not None:
                raise delete_error

    class _Client:
        def select_graph(self, _name):
            return _Graph()

        def list_graphs(self):
            if isinstance(catalogue, Exception):
                raise catalogue
            return list(catalogue)

    store.client = _Client()
    store.g = _Graph()
    return store


def test_a_graph_that_survives_teardown_is_loud_not_silent(harness):
    """⛔ R1 SAYS "TEARS THE TENANT DOWN"; A SWALLOWED DELETE VERIFIES NOTHING.

    `reset()` wrapped `GRAPH.DELETE` in `contextlib.suppress(Exception)` with the
    comment "a missing graph is the goal". The comment is true and the code was
    still a fail-open: a delete that failed for ANY other reason — a transient
    module error, a concurrent writer — was suppressed identically, so the run
    reported as normal and left the corpus graph AND its HNSW index behind.
    README §5.1 B1's only evidence for "no graph left behind" was a human's
    out-of-band `GRAPH.LIST`, which is not a property the tool holds.

    Three states, three answers. If the suppression comes back, the FIRST case
    goes green (no exception) and this test reds — verified by restoring the
    suppression and watching exactly this test fail.
    """
    # (1) the delete raised AND the graph is still there ⇒ refuse to proceed
    store = _graph_store(
        harness,
        delete_error=RuntimeError("concurrent write during GRAPH.DELETE"),
        catalogue=("tortoise_parity_13906", "someone_elses_graph"),
    )
    with pytest.raises(harness.CannotRun) as refused:
        store.reset()
    assert "SURVIVED" in str(refused.value), str(refused.value)
    # …and the diagnosis keeps the delete's own error, so the next reader is not
    # told "survived" by a tool that threw the reason away
    assert "concurrent write during GRAPH.DELETE" in str(refused.value)

    # (2) the delete raised because the graph was ABSENT (the common case, and
    # the one the suppression was written for) ⇒ that IS the post-condition
    store = _graph_store(
        harness,
        delete_error=RuntimeError("ERR Invalid graph operation on empty key"),
        catalogue=("someone_elses_graph",),
    )
    store.reset()  # must not raise

    # (3) the delete looked fine but absence cannot be PROVEN ⇒ cannot claim R1
    store = _graph_store(harness, catalogue=RuntimeError("connection reset"))
    with pytest.raises(harness.CannotRun) as refused:
        store.reset()
    assert "cannot verify" in str(refused.value), str(refused.value)

    # The CALL SITE: `run()`'s teardown must go through `reset()` — a guard the
    # teardown path does not call protects nothing.
    tree = ast.parse(MODULE_PATH.read_text())
    run_node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "run")
    called = {(c.func.attr if isinstance(c.func, ast.Attribute)
               else getattr(c.func, "id", ""))
              for c in ast.walk(run_node) if isinstance(c, ast.Call)}
    assert "reset" in called, sorted(called)

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
import re
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "tools" / "vector_parity_13906.py"
RECORD_MODULE_PATH = ROOT / "tortoise" / "record_vector.py"


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
        assert urlsplit(dsn).hostname is None, dsn  # ← the SKIP condition
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value)
        # …and the deliberate act still works, so a real remote run is possible.
        harness._assert_local_record_dsn(dsn, True)

    for dsn in ("postgresql://u:p@127.0.0.1:5432/tortoise_record",
                "postgresql://u:p@localhost/tortoise_record"):
        harness._assert_local_record_dsn(dsn, False)
    # ⛔ `None` is "no record DSN configured", which is local by construction. The
    # EMPTY string is NOT: libpq reads the environment for it, so the guard must
    # consult `PGHOST`/`PGHOSTADDR`/`PGSERVICE` rather than return early.
    harness._assert_local_record_dsn(None, False)
    with pytest.raises(harness.CannotRun) as refused:
        harness._assert_local_record_dsn("", False)
    assert "NON-LOCAL" in str(refused.value)

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

    # ⛔ libpq has NO FRAGMENT CONCEPT: Python ends the URL at `#` and reports no
    # query, while libpq reads on and dials the query string's `hostaddr`
    # (`PQconninfoParse` on this exact string returns hostaddr=203.0.113.9, and a
    # real connect attempts that address). The two parsers disagree about where
    # the host is, so this is REFUSED rather than resolved one way.
    hash_shape = "postgresql://127.0.0.1/tortoise_record#?hostaddr=203.0.113.9"
    assert urlsplit(hash_shape).query == ""             # ← what Python sees
    assert harness._record_dsn_hosts(hash_shape) == [harness.RECORD_DSN_AMBIGUOUS]
    with pytest.raises(harness.CannotRun):
        harness._assert_local_record_dsn(hash_shape, False)

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


def test_a_path_less_uri_still_has_a_QUERY(harness, monkeypatch):
    """⛔ REGRESSION, caught by review at `09c9b1ab`: bound-at-`/` was fail-open.

    libpq ends the AUTHORITY at the first of `/`, `?` or `#` after the userinfo.
    While this module ended it only at `/`, a PATH-LESS URI swallowed its own
    query into the authority and the guard saw no query at all:

        postgresql://127.0.0.1:5432?hostaddr=203.0.113.9

    — libpq parses `{host: 127.0.0.1, hostaddr: 203.0.113.9}` and DIALS
    203.0.113.9 (measured: `psql` reports `connection to server at "203.0.113.9"`),
    while the guard PASSED. That is a remote record certified local on the path
    where `run()` seeds org/graph/node_identity/point and `teardown()` deletes by
    `org_id`. The rewrite that fixed the `'?'`-in-userinfo over-block traded it
    for this fail-open, which is why every remote shape is listed here.
    """
    monkeypatch.delenv("PGSERVICE", raising=False)
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGHOST", raising=False)

    remote = (
        "postgresql://127.0.0.1:5432?hostaddr=203.0.113.9",
        "postgresql://127.0.0.1:5432?host=203.0.113.9",
        "postgresql://u:p@127.0.0.1:5432?hostaddr=203.0.113.9",
        "postgresql://127.0.0.1:5432?sslmode=require&hostaddr=203.0.113.9",
        "postgres://127.0.0.1:5432?hostaddr=203.0.113.9",
        "postgresql://localhost:5432?host=203.0.113.9",
        "postgresql://127.0.0.1:5432?service=prod",
    )
    for dsn in remote:
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value), dsn
        harness._assert_local_record_dsn(dsn, True)   # the deliberate act works
        # …and the QUERY is what carried it, not the authority
        assert harness._record_dsn_hosts(dsn) != ["127.0.0.1"], dsn

    # THE CONTROL: the same URI WITH a path was already refused (which is exactly
    # why the path-less shape hid), and a plain local URI still passes.
    with pytest.raises(harness.CannotRun):
        harness._assert_local_record_dsn(
            "postgresql://127.0.0.1:5432/db?hostaddr=203.0.113.9", False)
    for dsn in ("postgresql://127.0.0.1:5432", "postgresql://127.0.0.1:5432/db",
                "postgresql:///tortoise_record?host=localhost"):
        harness._assert_local_record_dsn(dsn, False)


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


def test_the_app_role_dsn_keeps_the_spelling_it_was_given(harness):
    """TWO spellings, TWO syntaxes — `urlunsplit` produced DSNs libpq rejects.

    The guard now ACCEPTS the authority-less URI and the whitespace conninfo, so
    `app_role_dsn` must be able to rebuild them. It emitted
    `postgresql:/tortoise_record?…` for the first (`postgresql` is not in
    `urllib.parse.uses_netloc`, so an empty netloc loses a slash: libpq answers
    `invalid connection option "postgresql:/tortoise_record?host"`) and glued
    `?options=…` onto the LAST keyword's VALUE for the second
    (`host=127.0.0.1?options=…`, unresolvable). Either way the tool certified a
    store it then could not read a single row from.
    """
    uri = harness.app_role_dsn("postgresql:///tortoise_record?host=localhost")
    assert uri.startswith("postgresql:///tortoise_record?"), uri
    assert "options=-c%20role%3D" in uri, uri

    conninfo = harness.app_role_dsn("dbname=tortoise_record host=127.0.0.1")
    assert conninfo.startswith("dbname=tortoise_record host=127.0.0.1 "), conninfo
    assert "options='-c role=" in conninfo, conninfo
    assert "127.0.0.1?" not in conninfo, conninfo  # ← the glued-suffix defect

    # An `options=` already present is REPLACED, never duplicated.
    replaced = harness.app_role_dsn("postgresql://127.0.0.1/db?options=-c%20x%3D1")
    assert replaced.count("options=") == 1, replaced
    assert "x%3D1" not in replaced, replaced


    # ⛔ A conninfo VALUE carrying whitespace must stay QUOTED: re-joining bare
    # `shlex.split` tokens emitted `password=p w`, which libpq rejects outright
    # (`missing "=" after "w"`) — a DSN the guard had certified as local.
    quoted = harness.app_role_dsn(
        "dbname=tortoise_record host=127.0.0.1 password='p w'")
    assert "password='p w'" in quoted, quoted

    # ⛔ And a literal `+` in a URI value must NOT be decoded to a space
    # (`parse_qsl` does; libpq does not).
    plus = harness.app_role_dsn("postgresql://127.0.0.1/db?password=ab+cd")
    assert "ab%2Bcd" in plus, plus

def test_an_empty_leg_is_not_a_perfect_cross_recall(harness):
    """`1.0` for an empty leg published "perfect agreement" for a leg that
    answered nothing — in the same entry that reports its recall as 0.000.

    `compare()` was already fixed for this class (an empty truth set is an empty
    MEASUREMENT, not a perfect one); this is the informational arm of the same
    mistake, and §3 of the artifact quotes this number as evidence.
    """
    assert harness._cross_recall([], ["a", "b"])["recall"] is None
    assert harness._cross_recall([], [])["recall"] is None
    assert harness._cross_recall(["a"], ["a", "b"])["recall"] == 1.0
    assert harness._cross_recall(["a"], ["b"])["recall"] == 0.0


def test_an_unpoisoned_run_that_could_not_measure_is_not_a_divergence(
        harness, monkeypatch):
    """The round-7 conflation, ONE BRANCH OVER: `unmeasured` was only ever set on
    the POISONED branch, so an ordinary run whose every failure was a
    cannot-measure one exited **1** — "a required comparison diverged", a claim
    about a measurement it never made.
    """
    entry = _reachable_entry(harness, record_error="VectorShortfall: 8 of 10")
    report = {"k": 8, "queries": [entry],
              "verdict": harness._verdict({"k": 8, "queries": [dict(entry)]},
                                          poisoned=None)}
    assert report["verdict"]["ok"] is False, report["verdict"]
    assert report["verdict"].get("unmeasured") is True, report["verdict"]

    argv = ["--record-dsn", "postgresql://u:p@127.0.0.1/db", "--graph-port", "1"]
    monkeypatch.setattr(harness, "run", lambda _args: report)
    assert harness.main(argv) == 2, (
        "an unmeasured UNPOISONED run exited 1 — the code that means 'a required "
        "comparison diverged'")

    # …and a MEASURED divergence still exits 1: the arm this must not swallow.
    entry = _reachable_entry(harness, corrupt_record=True)
    report = {"k": 8, "queries": [entry],
              "verdict": harness._verdict({"k": 8, "queries": [dict(entry)]},
                                          poisoned=None)}
    assert report["verdict"]["ok"] is False, report["verdict"]
    assert "unmeasured" not in report["verdict"], report["verdict"]
    monkeypatch.setattr(harness, "run", lambda _args: report)
    assert harness.main(argv) == 1, "a measured divergence must keep exit 1"

def test_a_conninfo_value_that_LOOKS_like_a_host_is_not_one(harness, monkeypatch):
    """⛔ THE GRAMMAR, NOT A REGEX — and the environment is where the host was.

    A flat `(host|hostaddr|service)`-with-`\\s*=\\s*` regex invented a `host=`
    libpq never parses, because libpq SKIPS whitespace after `=` and honours
    backslash escapes. Both shapes below are legal, both are what a templated DSN
    produces when one variable is empty or a password carries the text, and BOTH
    let a remote record be certified local — the regex reported a loopback host
    AND suppressed the environment fallback, so the `PGHOST`/`PGHOSTADDR` that
    libpq actually dials was never consulted:

        dbname= host=127.0.0.1       libpq: dbname='host=127.0.0.1'  (NO host)
                                     PGHOST=203.0.113.9 -> dials 203.0.113.9
        dbname=x\\ host=127.0.0.1     libpq: dbname='x host=127.0.0.1' (NO host)
        user= hostaddr=127.0.0.1     libpq: user='hostaddr=127.0.0.1' (NO hostaddr)

    Measured with `PQconninfoParse` and `psql` (which names the host it dials):
    1,111 of 49,171 conninfo × environment pairs bypassed. The guard and the
    rebuild now share ONE grammar walker (`_conninfo_pairs`), so an interpretation
    cannot drift from the other.
    """
    for var in ("PGHOST", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(var, raising=False)

    # the shapes as DSNs, with the environment supplying the host libpq dials
    cases = (
        ("dbname= host=127.0.0.1", {"PGHOST": "203.0.113.9"}),
        ("dbname=x\\ host=127.0.0.1", {"PGHOST": "203.0.113.9"}),
        ("user= hostaddr=127.0.0.1", {"PGHOSTADDR": "203.0.113.9"}),
        ("dbname= host=127.0.0.1", {"PGSERVICE": "prod"}),
    )
    for dsn, env in cases:
        for var in ("PGHOST", "PGHOSTADDR", "PGSERVICE"):
            monkeypatch.delenv(var, raising=False)
        for var, value in env.items():
            monkeypatch.setenv(var, value)
        # the DSN names NO host …
        assert harness._conninfo_hosts(dsn)[0] == [], (dsn, harness._conninfo_hosts(dsn))
        # …so the environment decides, and the guard must refuse
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value), dsn
        harness._assert_local_record_dsn(dsn, True)   # the deliberate act works

    # CONTROL: with no environment host, the same DSNs name no host at all — which
    # is ALSO a refusal (`RECORD_DSN_NO_HOST`), not a pass.
    for var in ("PGHOST", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(var, raising=False)
    for dsn in ("dbname= host=127.0.0.1", "dbname=x\\ host=127.0.0.1"):
        with pytest.raises(harness.CannotRun):
            harness._assert_local_record_dsn(dsn, False)

    # CONTROL: a DSN that really DOES set a loopback host PASSES, and a remote
    # environment must NOT override it — the DSN's own keyword wins, as libpq has it.
    monkeypatch.setenv("PGHOST", "203.0.113.9")
    for dsn in ("host=127.0.0.1 dbname=x", "host = 127.0.0.1",
                "host='127.0.0.1' dbname='a b'", "hostaddr=127.0.0.1 host=127.0.0.1"):
        harness._assert_local_record_dsn(dsn, False), dsn

    # …and a real remote keyword in either position still refuses.
    monkeypatch.setenv("PGHOST", "203.0.113.9")
    for dsn in ("host=203.0.113.9", "hostaddr=203.0.113.9 dbname=x",
                "host=127.0.0.1 hostaddr=203.0.113.9"):
        with pytest.raises(harness.CannotRun):
            harness._assert_local_record_dsn(dsn, False)


def test_the_guard_answers_what_libpq_would_DIAL_not_what_this_string_says(
        harness, monkeypatch):
    """The third indirection of one class: parse like libpq, and read its env.

    Three rounds each closed ONE indirection of this guard (the query string
    overriding the authority, then `service=`/`pg_service.conf`). Round 11 found
    three more, every one of them measured with libpq itself (`psql` names the
    host it dials):

        # a FRAGMENT to urlsplit, NOTHING to libpq — the query is live
        postgresql://127.0.0.1:1/db#?hostaddr=203.0.113.9   -> 203.0.113.9
        # libpq's conninfo grammar allows spaces/newlines around `=`
        host=127.0.0.1 hostaddr = 203.0.113.9              -> 203.0.113.9
        # and libpq fills UNSET options from the environment
        PGHOSTADDR=203.0.113.9 + postgresql://127.0.0.1/db  -> 203.0.113.9

    All three PASSED the guard (45/45 green) while a remote record was dialled —
    the store `run()` seeds and `teardown()` deletes by `org_id`. The structural
    answer is not a fourth special case: the guard now parses the QUERY with
    libpq's rule (first `?` to end of string, `#` refused outright, spaces
    allowed around `=` in conninfo) and applies `PGHOSTADDR`/`PGHOST`/`PGSERVICE`
    by libpq's own precedence — a variable supplies an option only when the DSN
    left it unset.
    """
    for var in ("PGHOSTADDR", "PGHOST", "PGSERVICE"):
        monkeypatch.delenv(var, raising=False)

    remote = (
        # (a) the fragment
        "postgresql://127.0.0.1:1/db#?hostaddr=203.0.113.9",
        # (b) libpq's conninfo whitespace
        "host=127.0.0.1 port=1 hostaddr = 203.0.113.9 dbname=db",
        "host=127.0.0.1\nhostaddr\n=\n203.0.113.9 dbname=db",
        "host=127.0.0.1 service = prod dbname=db",
        # (c) the environment supplies what the DSN leaves unset
        "postgresql://127.0.0.1:1/db",            # + PGHOSTADDR below
        "host=127.0.0.1 port=1 dbname=db",        # + PGHOSTADDR below
    )
    for dsn in remote[:4]:
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value), dsn
        harness._assert_local_record_dsn(dsn, True)   # the deliberate act works

    monkeypatch.setenv("PGHOSTADDR", "203.0.113.9")
    for dsn in remote[4:]:
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value), dsn
        harness._assert_local_record_dsn(dsn, True)

    # ⛔ AND IT IS NOT A BLANKET BLOCK. Three controls:
    # 1. a DSN that SETS hostaddr beats the environment — libpq's precedence;
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.9")
    harness._assert_local_record_dsn(
        "postgresql://127.0.0.1:1/db?hostaddr=127.0.0.1", False)
    # 2. a loopback value in the environment is fine;
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    harness._assert_local_record_dsn("postgresql://127.0.0.1:1/db", False)
    # 3. and with the variable gone, the same DSNs are local again.
    monkeypatch.delenv("PGHOSTADDR")
    for dsn in ("postgresql://127.0.0.1:1/db", "host=127.0.0.1 port=1 dbname=db",
                "host='127.0.0.1' dbname=db"):
        harness._assert_local_record_dsn(dsn, False)

    # PGHOST is the same lever for a DSN that names NO host at all.
    monkeypatch.setenv("PGHOST", "203.0.113.9")
    with pytest.raises(harness.CannotRun):
        harness._assert_local_record_dsn("postgresql:///db", False)


@pytest.mark.timeout(10)
def test_a_conninfo_token_starting_with_equals_cannot_spin_forever(harness):
    """⛔ A NON-PROGRESS BRANCH IN `_drop_options_pairs` IS AN UNBOUNDED SPIN.

    The word scan stops immediately when the character at the word start IS `=`
    (`=x host=127.0.0.1`). The old branch then re-emitted the empty word
    (`dsn[word_start:i]` is `""`) and `continue`d WITHOUT advancing `i`, so
    `while i < n` spun forever — no refusal, no traceback, no exit, and a lane
    watchdog kills it with no diagnosis. Measured: `app_role_dsn` on
    `"host=127.0.0.1 dbname=x = "` never returned and was killed by
    `timeout 5` (exit 124).

    ⛔ WHY THE `timeout` MARK: on the mutation this test must FAIL, not hang the
    suite. pytest-timeout is a declared test dependency (pyproject `test` extra),
    so a spin here is reported as a failure with the stack, which is the only
    readable form of this defect.

    The guard certifies these strings — `_conninfo_hosts` still finds
    `host=127.0.0.1` in all three — so they reach `app_role_dsn`; through `run()`
    libpq rejects them at connect (`invalid connection option ""`), which is why
    the spin was reachable from the module's API and from this suite rather than
    from a live run.
    """
    for dsn in (
        "host=127.0.0.1 dbname=x = ",      # trailing bare `=`
        "=x host=127.0.0.1",               # leading `=`
        "host=127.0.0.1  =  dbname=x",     # bare `=` in the middle
    ):
        rebuilt = harness.app_role_dsn(dsn)
        # the pair was ADDED and nothing was dropped
        assert "options=" in rebuilt, rebuilt
        assert "role=tortoise_record_app" in rebuilt or "%2Dc" in rebuilt, rebuilt
        # …and the rest of the spelling survived (the `=` tokens are kept, not
        # silently swallowed) — a rebuild that DELETED them would be the M/N
        # class: a wrong DSN handed to psycopg2
        assert rebuilt.startswith(dsn), (dsn, rebuilt)


@pytest.mark.parametrize(
    ("trailing", "padding"),
    [(1, ""), (3, ""), (1, " "), (1, "  "), (1, "\t"), (1, "\n")],
    ids=["one", "three", "one+space", "one+2spaces", "one+tab", "one+newline"])
def test_a_value_ending_in_an_ODD_backslash_is_CLOSED_before_options_is_appended(
        harness, trailing, padding):
    """⛔ THE APPEND IS PART OF THE REBUILD, SO THE LAST VALUE MUST BE CLOSED.

    `app_role_dsn` appends ` options='-c role=…'` to whatever `_drop_options_pairs`
    returns, and that function used to return the caller's bytes VERBATIM when
    there was no `options` pair and no empty value. In libpq's whitespace form a
    trailing `\` in an UNQUOTED value escapes the very next character — the
    separating space — so `dbname=x\ options='-c role=…'` parses as
    `dbname = "x options='-c"` plus a bogus keyword `role=…'`, and libpq rejects
    the whole string. Measured on this box with real libpq (psql, exact bytes):

        in      host=127.0.0.1 dbname=x\                -> PARSES (Connection refused)
        out     host=127.0.0.1 dbname=x\ options='…'    -> invalid connection option "role"
        out     host=127.0.0.1 dbname='x\\' options='…' -> PARSES (Connection refused)

    The guard CERTIFIES the input (`_assert_local_record_dsn` passes it), so the
    operator is told the DSN is good while every record query carries a
    `record_error` — no measurement at all. Fail-closed toward the artifact, which
    is why it is not a false PASS; the empty-value sibling of the same mechanism
    was already fixed.

    ⛔ WHY THE ORACLE IS THE MODULE'S OWN PAIR PARSER, NOT `shlex`: `shlex` cannot
    even tokenise this shape (`shlex.split("host=… dbname=x\\")` raises
    `No escaped character`), which is exactly how the spelling stayed out of the
    existing byte-preservation pin's `spellings` tuple.
    """
    # ⛔ `padding` IS PART OF THE SHAPE, not decoration. `app_role_dsn` appends
    # after `dsn.strip()`, so a guard counted on the UNSTRIPPED tail sees
    # whitespace (an even, zero-length run) and returns the bytes verbatim — then
    # the `.strip()` removes exactly the space the trailing backslash was
    # escaping, reinstating the corruption. Measured in review on all of
    # `" "`, `"  "`, `"\t"`, `"\n"`; the guard now counts the STRIPPED tail, and
    # each spelling is asserted here so the whitespace form cannot regress alone.
    dsn = "host=127.0.0.1 dbname=x" + "\\" * trailing + padding
    rebuilt = harness.app_role_dsn(dsn)

    before = harness._conninfo_pairs(dsn)
    after = harness._conninfo_pairs(rebuilt)
    # every keyword the caller gave survives, with the SAME value …
    after_map = {k: v for k, v in after if k is not None}
    for key, value in before:
        if key is None:
            continue
        assert after_map.get(key) == value, (key, value, rebuilt, after_map)
    # … and the app role was ADDED
    assert "role=tortoise_record_app" in (after_map.get("options") or ""), after_map
    # ⛔ THE SPECIFIC HOLE: the value must be CLOSED before the appended pair, so
    # the rebuild cannot leave a value ending in a bare odd backslash. Two legal
    # closures, and which one applies depends on what the odd run MEANS: a quoted
    # value (`dbname='x\\'`, `dbname='x '`) or — for a DANGLING backslash, which
    # libpq DROPS — no escape at all (`dbname=x`, round 22). Asserting only the
    # quoting form would have pinned the pre-round-22 defect in place.
    dbname_segment = rebuilt[rebuilt.index("dbname="):rebuilt.index("options=")].rstrip()
    if not dbname_segment.endswith("'"):
        assert (len(dbname_segment) - len(dbname_segment.rstrip("\\"))) % 2 == 0, (
            f"the rebuilt value is left OPEN before the appended pair: {rebuilt!r}")


def test_the_pair_parser_reads_a_TRAILING_escape_the_way_libpq_does(harness):
    """⛔ THE ORACLE HERE IS LIBPQ ITSELF, and that is the whole point: the pin
    above asks THIS MODULE's `_conninfo_pairs` what the caller's string means, so
    it cannot see a value the module and libpq read DIFFERENTLY.

    It could not: `dbname=x` followed by ONE backslash is `dbname='x'` to libpq
    (`PQconninfoParse`, measured on this box) because a backslash with nothing left
    to escape is dropped, while `_conninfo_pairs` kept it. The rebuild then closed
    the value as `dbname='x' + an escaped backslash` — parseable, and naming a
    database `x` + a backslash where the caller named `x`: a DSN the guard
    certified, the harness connected with, and a DIFFERENT database answered.
    Fail-closed (the read fails), but invisible to every assertion whose oracle was
    this module's own scanner.

    ⚠️ Skipped only where libpq genuinely cannot be loaded; the module is then the
    sole authority and the pin above covers the shape it can see.
    """
    parse = _libpq()
    if parse is None:
        pytest.skip("libpq is not loadable on this box")
    for backslashes in (1, 2, 3, 4):
        for padding in ("", " ", "\t"):
            dsn = "host=127.0.0.1 dbname=x" + "\\" * backslashes + padding
            want = parse(dsn)
            assert "__error__" not in want, (dsn, want)
            got = {k: v for k, v in harness._conninfo_pairs(dsn) if k is not None}
            assert got == want, (dsn, got, want)
            # …and the REBUILD must name the SAME database, through libpq's eyes
            rebuilt = harness.app_role_dsn(dsn)
            after = parse(rebuilt)
            assert "__error__" not in after, (dsn, rebuilt, after)
            for key, value in want.items():
                assert after.get(key) == value, (dsn, key, value, rebuilt, after)
            assert "role=tortoise_record_app" in (after.get("options") or ""), (
                dsn, rebuilt, after)


def test_the_app_role_dsn_changes_the_options_PAIR_and_nothing_else(harness):
    """⛔ BYTE-PRESERVATION, asserted structurally — because every tokenising
    rebuild here was wrong in a different way.

    `" ".join(shlex.split(dsn))` lost the quotes carrying a whitespace value
    (`dbname='tortoise record'` → `dbname=tortoise record`: libpq answers
    `missing "=" after "record"`). Then a pairs loop that understood `key = value`
    still destroyed the ONE-SIDED spacings libpq also allows — measured with real
    `PQconninfoParse`, 8 of 15 legal spellings broke: `host =127.0.0.1` came back
    as `host= =127.0.0.1` (host `"=127.0.0.1"`), `host= 127.0.0.1` as
    `host='' 127.0.0.1=` (`invalid connection option "127.0.0.1"`),
    `dbname ='tortoise record'` as `missing "=" after "record'"`.

    The earlier version of THIS test asserted `" = " not in out`, which only
    catches the doubly-spaced form: deleting the spaced-`=` handling entirely
    kept all 59 pins green while libpq rejected the rebuilt DSN. So the property
    asserted here is the one the function actually promises — the caller's string
    is returned intact apart from the `options` pair, which is why no spelling a
    test never imagined can be corrupted. `app_role_dsn` no longer tokenises:
    it deletes the `options` pair with a whitespace-tolerant matcher and appends
    its own.
    """
    import re as _re
    import shlex

    def pairs(text: str, *, drop_options: bool = True) -> list[tuple[str, str]]:
        """(keyword, value) pairs by a parser INDEPENDENT of the one under test.

        ⛔ NOT the same regex the code uses. The first version of this pin did
        exactly that, so it certified a rebuild libpq REJECTS (`password='options=zzz'`
        came back as `password=' options='-c role=…'` while the helper called it
        "equal outside the options pair"). Normalising `\s*=\s*` and letting
        `shlex` unquote gives an oracle the scanner has to AGREE with, not one it
        defines.
        """
        normalised = _re.sub(r"\s*=\s*", "=", text)
        out = []
        for token in shlex.split(normalised):
            key, _, value = token.partition("=")
            if not drop_options or key.lower() != "options":
                out.append((key, value))
        return sorted(out)

    spellings = (
        # the plain form, and every spacing libpq admits around `=`
        "host=127.0.0.1 dbname=x",
        "host =127.0.0.1 dbname=x",
        "host= 127.0.0.1 dbname=x",
        "host = 127.0.0.1 dbname=x",
        "host\t=\n127.0.0.1 dbname=x",
        # …with values that NEED quoting
        "dbname ='tortoise record' host=127.0.0.1",
        "password='a b' host=127.0.0.1",
        "application_name ='my app' host=127.0.0.1",
        # …and with an `options` pair already present, in both spacings
        "options ='-c search_path=a' host=127.0.0.1",
        "options='-c search_path=a' host=127.0.0.1",
        "host=127.0.0.1 options='-c role=old' dbname=x",
        # ⛔ TWO SPELLINGS A REGEX CANNOT SURVIVE (measured with libpq in review):
        # an ESCAPED SPACE in an unquoted value, which `(\S+)` truncated so its
        # remainder became a bogus keyword; and `options=` INSIDE another
        # keyword's quoted value, which a quote-blind match deleted — mutating a
        # password in one case and unbalancing its quote in the other.
        "host=127.0.0.1 options=-c\\ search_path=a",
        "host=127.0.0.1 password='options=zzz'",
        "host=127.0.0.1 password='a options=b c'",
        # ⛔ AN EMPTY VALUE AT THE END. `dbname=` is legal (libpq: `dbname=''`),
        # but re-emitting it BARE and then appending ` options=…` makes libpq skip
        # the whitespace after `=` and read `options='-c` as dbname's value, so
        # the tail becomes the bogus keyword `role=…'` (measured: `invalid
        # connection option "role"`). The quoted form is the control.
        "host=127.0.0.1 dbname=",
        "host=127.0.0.1 dbname=x sslmode=",
        "host=127.0.0.1 user=",
    )
    for dsn in spellings:
        out = harness.app_role_dsn(dsn)
        assert out.rstrip().endswith(
            f"options='-c role={harness.APP_ROLE}'"), (dsn, out)
        # the harness owns `options` and NOTHING else: same keywords, same values
        assert pairs(out) == pairs(dsn), (
            "the rebuilt conninfo differs from the caller's outside the options "
            f"pair: {dsn!r} -> {out!r}")
        # exactly ONE top-level `options` pair — `str.count` would also see the
        # word inside a password value (`password='options=zzz'`), which is the
        # case the deletion must NOT touch
        assert sum(1 for k, _v in pairs(out, drop_options=False)
                   if k.lower() == "options") == 1, (dsn, out)
        assert harness._conninfo_hosts(out)[0] == harness._conninfo_hosts(dsn)[0], (
            f"the rebuild changed the hosts the guard would re-check: {dsn!r} -> {out!r}")

    # A URI keeps its shape too, including the authority-less spelling that
    # `urlunsplit` used to collapse to `postgresql:/db`.
    for dsn in ("postgresql:///tortoise_record?host=localhost",
                "postgresql://127.0.0.1:5432/db?host=127.0.0.1&sslmode=disable",
                "postgresql://u:p@localhost/db"):
        out = harness.app_role_dsn(dsn)
        assert out.startswith(dsn.split("?")[0] + "?"), (dsn, out)
        assert "options=-c%20role%3D" in out, out
        assert out.count("options=") == 1, out


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


def test_a_straddling_pair_fails_CLOSED_never_as_a_tie(harness):
    """The tie GROUPS are rounded buckets, and their error is one-directional.

    `compare()` groups truth rows by `round(dist / TIE_TOL)`, which is STRICTER
    (finer) than "within `TIE_TOL` of each other" — bucket equality is a strict
    SUBSET of that relation, so it excuses FEWER pairs, never more: two distances
    2e-10 apart that
    straddle a bucket boundary (`4.999e-7` -> bucket 0, `5.001e-7` -> bucket 1)
    are placed in DIFFERENT groups, so a swap between them is reported
    `diverged` even though the two are far inside the tolerance. Measured, not
    assumed, and NOT exotic: a same-bucket span is at most `TIE_TOL`, while
    about half of the in-tolerance pairs straddle a boundary (99,771 of 200,000
    samples).

    ⛔ The pin holds BOTH ends of the policy, with two probes that catch
    DIFFERENT mutations — measured, not assumed: the straddling probe above is
    what reds a WIDENED bucket (at `round(dist / (2 * TIE_TOL))` its two
    distances land in the same bucket, so the expected `diverged` becomes a
    `tie`), and the far-apart probe below is what reds a tolerance that is not a
    width at all — a RELATIVE one (`1e-4 * max(da, db)`) or a bucket widened far
    enough (10x) that a `2 * TIE_TOL` gap is swallowed. So `abs(da - db) <=
    TIE_TOL` directly, the repair a reader reaches for, only ever ADDS `tie`s for
    pairs already inside the tolerance: it cannot excuse a re-rank wider than
    `TIE_TOL`, and it leaves every genuine cross-distance pin in this file green
    (measured) — it reds only the straddling probe, which is why the prose around
    the bucket names WIDENING as the dangerous repair and not that one.
    """
    near_a, near_b = 4.999e-7, 5.001e-7
    assert abs(near_a - near_b) < harness.TIE_TOL, (
        "this pin only says anything while the two distances are INSIDE the "
        f"tolerance; they are {abs(near_a - near_b)!r} apart")
    assert round(near_a / harness.TIE_TOL) != round(near_b / harness.TIE_TOL), (
        "the two distances no longer straddle a bucket boundary, so the case "
        "this pin exists for is not being exercised")
    cmp_ = harness.compare([("a", near_a), ("b", near_b), ("c", 0.5)],
                           ["b", "a", "c"])
    assert cmp_["order"] == "diverged", (
        "a sub-tolerance pair is now excused as a `tie` — the bucket rounding "
        "was replaced by something more permissive than a `TIE_TOL`-wide "
        "bucket, so pairs that are the same distance by this module's own "
        "definition now read as a PASS")
    # ...and a pair this far apart must NEVER be a `tie`. Measured: this probe
    # stays `diverged` under a 2x/3x/4x WIDENED bucket (that mutation is caught
    # by the straddling probe above), and it is the probe that reds a tolerance
    # which is not a fixed width at all — relative (`1e-4 * max(da, db)`) or a
    # bucket wide enough (10x) to swallow a `2 * TIE_TOL` gap. Both are a PASS
    # over a re-rank two whole tolerances wide.
    wide = harness.compare([("a", 0.1), ("b", 0.1 + 2 * harness.TIE_TOL),
                            ("c", 0.5)], ["b", "a", "c"])
    assert wide["order"] == "diverged", (
        "two distances `2 * TIE_TOL` apart were excused as a `tie` — the tie "
        "relation is no longer a fixed width (a relative tolerance, or a bucket "
        "widened far enough to swallow the gap), which turns a genuine "
        "cross-distance re-rank into a PASS")


def test_the_vector_poison_is_applied_at_SEED_time(harness):
    """`run()` must perturb the SEEDED rows, and no DB-free pin could see it.

    The comment at `run()` states why the `--poison vector` mutation is applied
    before `store.seed(...)` rather than after `scan_answers` is taken: a
    post-read divergence leaves that arm measuring the UNPOISONED data and the
    poison goes undetected (measured on this harness's first attempt — `vector`
    reported POISON NOT DETECTED while the other three were caught).

    That literal is inside `run()`, which no DB-free test executes, so replacing
    `perturb_for_vector_poison(rows)` with `rows` left **80/80 green** while the
    runtime consequence is a poison that can never be detected. It is the same
    defect this module already closed for `_scan_arm` and `require_index` — "a
    literal inside `run()` no DB-free pin can reach" — so it is closed the same
    way: the body is READ.

    ⛔ PRESENCE IS NOT PLACEMENT, and this pin learned that the hard way. A first
    draft collected the names handed to ANY `*.seed(...)` and intersected them
    with the perturbation's result — so it could not see the RECEIVER or the
    ORDER, and three mutants stayed 82/82 green:
      B3  move the perturbation AFTER `store.seed(...)` (the arm re-reads the
          unpoisoned rows the moment the store is already seeded)
      B4  send the perturbation to `record.seed(...)` and seed the GRAPH with the
          unperturbed rows (the oracle is poisoned, the arm under test is not)
      B5  drop the `args.poison == "vector"` guard (the store is poisoned on
          EVERY run, so `--poison none` reports `diverged` against an
          unperturbed baseline — invisible to receiver, order and oracle)
    All three are the defect this pin names. So it now asserts the RECEIVER is the
    graph `store`, that the assignment PRECEDES the `store.seed(...)` statement,
    that the ORACLE still receives the unperturbed `rows`, and that the binding
    is GUARDED by `args.poison == "vector"`. This is the same
    rule the teardown pin states: "`run()` calls `reset()` SOMEWHERE" is not
    "the teardown runs".

    ⛔ THE GUARD IS CHECKED BY EVALUATING IT, not by matching substrings. Reading
    `"poison" in guard and "vector" in guard` cannot tell a condition from its
    negation, so an INVERTED guard (`rows if args.poison == "vector" else
    perturb_for_vector_poison(rows)` — the store poisoned on exactly the wrong
    run) and a VACUOUS one (`... if args.poison == "vector" or True else rows`)
    both stayed 82/82 green. The guard expression is now compiled and run under
    each poison value against sentinel inputs: under `vector` it must yield the
    PERTURBED rows and under anything else the UNPERTURBED ones. That is the
    contract, so it is what is tested.
    """
    tree = ast.parse(MODULE_PATH.read_text())
    run = next(node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name == "run")

    perturbs = [node for node in ast.walk(run) if isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "perturb_for_vector_poison"]
    assert len(perturbs) == 1, (
        "`run()` must call `perturb_for_vector_poison` exactly once, found "
        f"{len(perturbs)} — zero means the poison is never applied")

    # the name the perturbation is bound to, and WHERE that binding happens
    carrier: str | None = None
    carrier_line = -1
    carrier_node: ast.Assign | None = None
    for node in ast.walk(run):
        if not isinstance(node, ast.Assign):
            continue
        if not any(sub is perturbs[0] for sub in ast.walk(node.value)):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                carrier, carrier_line, carrier_node = target.id, node.lineno, node
    assert carrier is not None and carrier_node is not None, (
        "`perturb_for_vector_poison(...)` is not assigned to a name in `run()` — "
        "its result has nowhere to go, so no seed can receive it")

    # ⛔ ...and it must be GUARDED by `args.poison == "vector"`. That is the
    # condition `run()`'s own comment says the mutation is applied under, and no
    # other assertion here can see it: dropping the guard leaves the carrier on
    # the graph `store`, before `store.seed(...)`, with the oracle untouched —
    # every check above stays green while the store is poisoned on EVERY run and
    # the harness reports `diverged` for `--poison none`.
    #
    # Evaluated, not string-matched: `"poison" in guard and "vector" in guard`
    # passes for an INVERTED guard and for a VACUOUS one. This compiles the
    # guarding expression and runs it under each poison value with sentinels, so
    # what is asserted is the contract — `vector` perturbs, everything else does
    # not — rather than the shape of the source that implements it.
    if not isinstance(carrier_node.value, ast.IfExp):
        raise AssertionError(
            f"`{carrier}` is assigned from a "
            f"{type(carrier_node.value).__name__}, not from the single guarded "
            "expression this pin evaluates. If the guard has been re-spelled as "
            "a statement (`store_rows = rows` then `if args.poison == \"vector\": "
            "store_rows = perturb_for_vector_poison(rows)`), that is a legitimate "
            "shape and this pin must be EXTENDED to read it — do not delete the "
            "assertion, because it is what proves the store is poisoned ONLY "
            "under `--poison vector`. A store poisoned on every run makes each "
            "`--poison none` run report `diverged` against an unperturbed "
            "baseline, i.e. the falsifier fires on the data it exists to compare "
            "against")

    perturbed = object()
    unperturbed = object()

    def _seed_rows_for(poison: str | None):
        """What `run()` binds the carrier to when `--poison` is `poison`."""
        env = {
            "args": types.SimpleNamespace(poison=poison),
            "rows": unperturbed,
            "perturb_for_vector_poison": lambda _rows: perturbed,
        }
        expr = ast.Expression(body=carrier_node.value)
        ast.fix_missing_locations(expr)
        return eval(compile(expr, "<seed-pin>", "eval"), {"__builtins__": {}}, env)

    assert _seed_rows_for("vector") is perturbed, (
        f"under `--poison vector`, `{carrier}` is bound to the UNPERTURBED rows — "
        "the guard is inverted (or vacuous), so the poison is never applied on "
        f"the one run that asks for it. Guard: `{ast.unparse(carrier_node.value)!r}`")
    for other in ("none", None):
        assert _seed_rows_for(other) is unperturbed, (
            f"under `--poison {other!r}`, `{carrier}` is bound to PERTURBED rows "
            "— the store is poisoned on a run that did not ask for it, so every "
            f"`--poison none` run reports `diverged`. Guard: "
            f"`{ast.unparse(carrier_node.value)!r}`")

    # every `.seed(<name>)` call in `run()`, by RECEIVER, with its position
    seeds: list[tuple[str, str | None, int]] = [
        (getattr(node.func.value, "id", ""),
         node.args[0].id if node.args and isinstance(node.args[0], ast.Name) else None,
         node.lineno)
        for node in ast.walk(run)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "seed"
        and node.args
    ]
    graph_seeds = [(arg, line) for recv, arg, line in seeds if recv == "store"]
    assert graph_seeds, (
        "`run()` never seeds the graph `store` — the arm under test would measure "
        f"nothing. Seeding calls found: {seeds!r}")
    assert [arg for arg, _line in graph_seeds] == [carrier], (
        f"the graph `store` is seeded with {[arg for arg, _ in graph_seeds]!r} "
        f"but the vector poison lands in {carrier!r} — the perturbation must be "
        "the value the STORE is seeded with, not a result computed elsewhere")
    assert graph_seeds[0][1] > carrier_line, (
        f"the perturbation (line {carrier_line}) is applied AFTER "
        f"`store.seed(...)` (line {graph_seeds[0][1]}) — the arm then measures "
        "the UNPOISONED rows, so the vector poison can never be detected "
        "(measured on this harness's first attempt: POISON NOT DETECTED)")
    oracle = [(arg, line) for recv, arg, line in seeds if recv == "record"]
    assert oracle and all(arg == "rows" for arg, _line in oracle), (
        f"the ORACLE must stay on the unperturbed corpus; `record.seed` receives "
        f"{[arg for arg, _ in oracle]!r} — poisoning the oracle too measures "
        "nothing, because both legs are then read from the same wrong data")
    # ⛔ ...AND THE ORACLE MUST NOT BE SEEDED FROM POISONED DATA. That assertion
    # alone is a check on a NAME: rebinding `rows` to the perturbation's result
    # above the seeding block keeps `record.seed(rows)` literal while handing the
    # oracle the POISONED corpus, and every other assertion in this pin stays
    # green (measured on the pre-fix pin: 82 passed with both legs seeded from the
    # rebound name).
    #
    # ⛔ A NODE-IDENTITY SCAN IS NOT ENOUGH, and four review rounds proved it one
    # alias at a time. Two easy spellings walked straight through it and left the
    # oracle poisoned with the pin green:
    #     perturbed = perturb_for_vector_poison(rows) if ... else rows
    #     rows = perturbed                         # rebinding an INTERMEDIATE name
    #     p = perturb_for_vector_poison ; rows = p(rows)      # a CALLABLE ALIAS
    # So the scan below is a small TRANSITIVE dataflow over `run()`'s bindings —
    # seed it with the perturbation's call and any alias of it, then close over
    # assignments until a fixpoint — and the assertion is on the SET of names that
    # hold poisoned rows, not on one shape of expression.
    #
    # ⛔ IT ALSO REFUSES WHAT IT CANNOT READ. A rebinding this scan cannot follow
    # (`rows: list = …`, `rows += …`, `rows[:] = …`, a loop target) is reported as
    # exactly that, instead of falling through to the carrier assertion's message
    # ("its result has nowhere to go"), which was FALSE about those programs.
    perturb_name = getattr(perturbs[0].func, "id", "")
    oracle_line = min(line for _arg, line in oracle)

    # every binding in `run()` as (line, [unparsed targets], value)
    binds: list[tuple[int, list[str], ast.AST]] = []
    for node in ast.walk(run):
        if isinstance(node, ast.Assign):
            binds.append((node.lineno,
                          [ast.unparse(t) for t in node.targets], node.value))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            binds.append((node.lineno, [ast.unparse(node.target)], node.value))
        elif isinstance(node, ast.For):
            binds.append((node.lineno, [ast.unparse(node.target)], node.iter))
    binds.sort(key=lambda b: b[0])

    # ⛔ LINE-AWARE, not a whole-body fixpoint: a name poisoned at line 1810 must
    # not retro-poison a binding at line 1700 that merely mentions it. Getting
    # this wrong produces false POSITIVES (`queries`, `report` were reported as
    # holding the perturbation's output) and, worse, a red the author cannot act on.
    callables = {perturb_name}
    poisoned_at: dict[str, int] = {}
    for _ in range(len(binds) + 1):
        grew = False
        for line, targets, value in binds:
            if line >= oracle_line:
                continue
            ids = set(targets)
            if isinstance(value, ast.Name) and value.id in callables:
                if not ids <= callables:
                    callables |= ids
                    grew = True
                continue
            carrier_hit = False
            for sub in ast.walk(value):
                if (sub is perturbs[0]
                        or (isinstance(sub, ast.Name)
                            and poisoned_at.get(sub.id, 10**9) < line)):
                    carrier_hit = True
                elif isinstance(sub, ast.Call):
                    fn = (getattr(sub.func, "id", "")
                          or getattr(sub.func, "attr", ""))
                    if fn in callables and fn != perturb_name:
                        carrier_hit = True
            if carrier_hit:
                for name in ids:
                    if name not in poisoned_at or line < poisoned_at[name]:
                        poisoned_at[name] = line
                        grew = True
        if not grew:
            break

    # ⛔ REFUSE SHAPES THIS SCAN CANNOT FOLLOW, instead of letting them fall
    # through to a message that is false about them.
    unreadable = sorted({
        target for line, targets, _value in binds if line < oracle_line
        for target in targets
        if target != "rows" and re.split(r"[\[. ]", target)[0] == "rows"})
    assert not unreadable, (
        f"this pin cannot follow the rebinding(s) of `rows` at {unreadable!r} "
        "(a subscript, attribute, annotated or augmented target). That is a "
        "LIMITATION OF THE PIN, not necessarily a defect in `run()` — extend the "
        "scan to read this shape rather than deleting the assertion, because it "
        "is what proves the ORACLE is seeded from the corpus and not from the "
        "perturbation's output")

    poisoned_at_oracle = sorted(
        (line, arg) for arg, line in oracle
        if poisoned_at.get(arg, 10**9) < line)
    assert not poisoned_at_oracle, (
        f"the ORACLE is seeded from POISONED rows at {poisoned_at_oracle!r} — "
        f"{sorted(poisoned_at)!r} hold the perturbation's output by then (this "
        "includes via an intermediate name or a callable alias). Both legs then "
        "read the same wrong data and `--poison vector` can never diverge, which "
        "is the blindness this pin exists to prevent")


def test_a_none_distance_is_not_a_verified_one(harness):
    """The second branch of the same fail-open the short-list guard closes.

    ⛔ The `None` arm is DEFENSIVE, not reachable through the record leg:
    `record_vector.vector_search` builds `WHERE t.embedding_vec IS NOT NULL`
    (`tortoise/record_vector.py:2815`), so `(embedding_vec <=> %s)` cannot be NULL
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
                     nan_distances=False, corrupt_scan=False, query="q0"):
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
    euc_ids = [key for key, _ in t_euc]
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
                 t_euc,
                 ([euc_ids[1], euc_ids[0], *euc_ids[2:]] if corrupt_scan
                  else euc_ids),
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


def test_a_poison_is_only_detected_on_the_arm_it_reaches(harness):
    """⛔ A DIVERGENCE ON AN UNREACHED ARM CERTIFIES NOTHING (#13914 round 10).

    `detected = any(f for f in failures if f not in unmeasured)` answered "was this
    MEASURED", never "was it measured ON THE THING THE POISON MOVED". `--poison
    id|order|recall` rewrite only the RECORD candidate list (`rec_pairs =
    poison(…, rec_ids, …)`), and `--poison vector` perturbs the rows the GRAPH is
    seeded with — so a real, measured divergence on the OTHER required arm printed
    `POISON DETECTED` for a corruption the run never applied to it. Same
    self-certification the round-7 fix closed one door over.

    Both directions are pinned, because a one-sided pin is satisfied by the old
    behaviour half the time.
    """
    scan_bad = {"k": 8, "queries": [_reachable_entry(harness, corrupt_scan=True)]}
    record_bad = {"k": 8, "queries": [_reachable_entry(harness, corrupt_record=True)]}

    # (1) `--poison id` cannot move the graph arm, so a scan divergence is NOT it.
    v = harness._verdict(scan_bad, poisoned="id")
    assert v["poison_status"].startswith("POISON NOT DETECTED"), (
        f"a graph_scan divergence certified --poison id, which only rewrites the "
        f"record candidate list: {v['poison_status']!r} / {v['failures']}")

    # (2) …and the arm it DOES reach is still a detection (so (1) cannot be
    # satisfied by a verdict that answers BLIND to everything).
    assert harness._verdict(record_bad, poisoned="id")["poison_status"] == \
        "POISON DETECTED"

    # (3) `--poison vector` perturbs the graph store, so a RECORD divergence is
    # not evidence about it.
    vv = harness._verdict(record_bad, poisoned="vector")
    assert vv["poison_status"].startswith("POISON NOT DETECTED"), (
        f"a record divergence certified --poison vector, which perturbs the graph "
        f"side: {vv['poison_status']!r} / {vv['failures']}")

    # (4) …and the graph arm it DOES reach is.
    assert harness._verdict(scan_bad, poisoned="vector")["poison_status"] == \
        "POISON DETECTED"


def test_a_blind_run_does_not_share_the_detected_exit_code(harness, monkeypatch):
    """§6's one-bit recipe must not read a BLIND harness as a confirmed one.

    README §6 runs each poison and reads `echo exit=$?`. BLIND and DETECTED both
    exited 1, so "every arm measured and the poison moved nothing" — a verdict
    AGAINST the instrument — was byte-identical to "the poison was caught".
    """
    entry = _reachable_entry(harness)
    blind = {"k": 8, "queries": [dict(entry)],
             "verdict": harness._verdict({"k": 8, "queries": [dict(entry)]},
                                          poisoned="vector")}
    assert blind["verdict"]["poison_status"].startswith("POISON NOT DETECTED"), \
        blind["verdict"]["poison_status"]
    monkeypatch.setattr(harness, "run", lambda _args: blind)
    argv = ["--record-dsn", "postgresql://u:p@127.0.0.1/db",
            "--graph-port", "1", "--poison", "vector"]
    code = harness.main(argv)
    assert code == 3, (
        f"a BLIND run exited {code}; 1 is the code §6's recipe reads as "
        f"'the poison was confirmed'")


def test_an_empty_candidate_list_is_not_perfect_agreement(harness):
    """`_cross_recall` over zero rows published `recall: 1.0` (round 10).

    `compare()`'s recall was already fixed to answer `None` over an empty set; the
    cross-leg column kept the old shape, so a raised record leg's `[]` wrote
    `record_vs_graph_scan.recall = 1.0` into the artifact — perfect agreement over
    nothing. It cannot turn a run green (the required record arm fails first), but
    the field is a claim the data does not support.
    """
    assert harness._cross_recall([], ["a"])["recall"] is None
    assert harness._cross_recall(["a"], [])["recall"] == 0.0, (
        "an empty SECOND list is zero overlap, which IS a measurement")
    assert harness._cross_recall(["a", "b"], ["a"])["recall"] == 0.5


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
    `public.point.embedding_vec vector(384)`), so a near tie that float32 rounds
    the other way would reorder the top-k and the record arm would then red for
    a PRECISION reason while the harness reported it as a store defect.

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


def test_the_record_arm_refuses_a_database_without_its_hnsw_index(harness):
    """⛔ THE COLUMN IS NOT THE INDEX, AND pgvector DOES NOT ERROR WITHOUT ONE.

    `require_schema()` checked only that `public.point.embedding_vec` exists, and
    `arms.record.driver` was the literal string "pgvector HNSW, cosine". pgvector
    answers `ORDER BY embedding_vec <=> q LIMIT k` over an index-less column with
    an EXACT SEQUENTIAL SCAN — no error — and this harness's oracle is itself an
    exact scan, so an index-less record returns the oracle VERBATIM: `recall
    1.000, order exact`, exit 0, published under the index's name. On a database
    where the column exists and the index does not (creation failed after the
    column, the index was dropped, a hand-built schema) the harness would certify
    "pgvector HNSW parity" from a scan. `record_error` cannot catch it — an exact
    scan raises no `VectorShortfall`.

    The GRAPH side already refuses exactly this (`_require_index_arm`), which is
    why the record side refusing it is consistency, not extra ceremony.
    """
    hnsw = ("CREATE INDEX idx_point_embedding_vec_hnsw ON public.point "
            "USING hnsw (embedding_vec public.vector_cosine_ops)")
    # 1. the real index shape the migration creates ⇒ accepted, and returned so
    #    the artifact can record WHICH index the numbers came from
    assert harness._require_hnsw_indexdef([hnsw]) == hnsw
    # quotation form too: `indexdef` is normalized before matching
    quoted = ('CREATE INDEX "idx" ON public."point" USING hnsw ("embedding_vec" '
              '"public"."vector_cosine_ops")')
    assert harness._require_hnsw_indexdef([quoted]) == quoted

    # 2. NO index at all ⇒ refuse (this is the fail-open being closed)
    with pytest.raises(harness.CannotRun) as refused:
        harness._require_hnsw_indexdef([])
    assert "EXACT SEQUENTIAL SCAN" in str(refused.value), str(refused.value)

    # 3. ⛔ AN INDEX IS NOT ENOUGH — it must be the HNSW index on the ANN COLUMN.
    #    A btree on the same column, an HNSW on a DIFFERENT column, and a plain
    #    pgvector-compatible index all leave the read as a scan.
    # ⛔ AND "USING HNSW ON THE COLUMN" IS STILL NOT "AN INDEX THIS READ CAN USE".
    #    HNSW is built for ONE operator class, and the record arm reads with `<=>`
    #    (cosine): an `vector_l2_ops` / `vector_ip_ops` index cannot serve that
    #    ORDER BY, so pgvector does not use it and the read falls back to the EXACT
    #    SCAN this guard exists to refuse. The repo states the hazard on the same
    #    index (`20261109000001_record_embedding_vector.sql:88-90`). A PARTIAL index
    #    (`… WHERE status IS NULL`) covers a subset for the same reason. The first
    #    cut of this guard matched `using hnsw` + `(embedding_vec` only and
    #    accepted all three — measured in review.
    l2 = ("CREATE INDEX i ON public.point USING hnsw (embedding_vec "
          "public.vector_l2_ops)")
    ip = ("CREATE INDEX i ON public.point USING hnsw (embedding_vec "
          "public.vector_ip_ops)")
    partial = ("CREATE INDEX i ON public.point USING hnsw (embedding_vec "
               "public.vector_cosine_ops) WHERE (status IS NULL)")
    for decoys in (
        ["CREATE INDEX p_key ON public.point USING btree (embedding_vec)"],
        ["CREATE INDEX x ON public.point USING hnsw (other_vec vector_cosine_ops)"],
        ["CREATE INDEX p_id ON public.point USING btree (id)"],
        [l2],
        [ip],
        [partial],
        ["CREATE INDEX i ON public.point USING hnsw (embedding_vec)"],
    ):
        with pytest.raises(harness.CannotRun):
            harness._require_hnsw_indexdef(decoys)
    # …and a genuine one alongside decoys still wins (no false block)
    assert harness._require_hnsw_indexdef(
        [l2, ip, partial, decoys[0],
         "CREATE INDEX p_id ON public.point USING btree (id)", hnsw]) == hnsw

    # 4. The CALL SITE and the RECORD: `require_schema` must route through the
    #    decision (not re-inline an existence check), and the run must publish the
    #    index it verified — a driver name is a claim, the indexdef is evidence.
    tree = ast.parse(MODULE_PATH.read_text())
    schema = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "require_schema")
    called = {(c.func.attr if isinstance(c.func, ast.Attribute)
               else getattr(c.func, "id", ""))
              for c in ast.walk(schema) if isinstance(c, ast.Call)}
    # ⛔ ROUTED THROUGH THE VALIDITY SEAM (`_require_valid_hnsw`), which is what
    # makes `indisvalid` part of the decision — and that seam must reach the shape
    # decision, or the chain is broken one level up instead of re-inlined.
    assert "_require_valid_hnsw" in called, sorted(called)
    valid_seam = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "_require_valid_hnsw")
    seam_calls = {(c.func.attr if isinstance(c.func, ast.Attribute)
                   else getattr(c.func, "id", ""))
                  for c in ast.walk(valid_seam) if isinstance(c, ast.Call)}
    assert "_require_hnsw_indexdef" in seam_calls, sorted(seam_calls)
    run_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                    and n.name == "run")
    keys = _arms_dict_keys(run_node, "record")
    assert keys, "run() no longer assigns report[\"arms\"][\"record\"]"
    assert "index" in keys, (
        "arms.record does not record the index it measured — the driver name is "
        "an unverified claim without it")
    assert "plan" in keys, (
        "arms.record does not record the PLAN the read got — an index that is "
        "listed (or even valid) is not an index that answered")


HNSW_COSINE = ("CREATE INDEX record_hnsw_index ON public.point USING hnsw "
               "(embedding_vec public.vector_cosine_ops)")


def test_an_INVALID_index_is_not_an_index_the_record_arm_may_use(harness):
    """`pg_index.indisvalid` is the field the SERVER consults; `indexdef` is not.

    ⛔ THE GUARD PROVED A LISTING AND CALLED IT A MEASUREMENT. `pg_indexes` exposes
    only `indexdef` (PG 16 §54.11) — no validity column — while `pg_index.indisvalid`
    is what the planner consults: *"False means the index is possibly incomplete …
    it cannot safely be used for queries"* (§53.26). An index left invalid by a
    failed or crashed `CREATE INDEX CONCURRENTLY` carries an `indexdef` that is
    byte-identical to a healthy one's, so the pre-fix guard ACCEPTED it and the
    record arm published `"pgvector HNSW, cosine"` over whatever plan it got.

    The remedy differs from the absent-index case (REINDEX, not CREATE INDEX),
    so the refusal must NAME the invalidity rather than say "no index".
    """
    assert harness._require_valid_hnsw(
        [("record_hnsw_index", HNSW_COSINE, True)]) == (
            "record_hnsw_index", HNSW_COSINE)

    # ⛔ THE DEFECT: a healthy-looking indexdef with indisvalid = false.
    with pytest.raises(harness.CannotRun) as refused:
        harness._require_valid_hnsw([("record_hnsw_index", HNSW_COSINE, False)])
    message = str(refused.value)
    assert "INVALID" in message and "indisvalid" in message, message
    assert "REINDEX" in message, (
        f"the refusal must name the remedy that differs from a missing index: {message}")

    # an INVALID decoy must not make an absent index look like a validity problem
    with pytest.raises(harness.CannotRun) as absent:
        harness._require_valid_hnsw(
            [("bt", "CREATE INDEX bt ON public.point USING btree (id)", False)])
    assert "INVALID" not in str(absent.value), str(absent.value)

    # …and a VALID index wins even when an invalid one is also present: the
    # decision is per-index, not "any invalid row poisons the database".
    assert harness._require_valid_hnsw(
        [("stale", HNSW_COSINE, False),
         ("live", HNSW_COSINE.replace("record_hnsw_index", "live"), True)]) == (
            "live", HNSW_COSINE.replace("record_hnsw_index", "live"))


def test_a_SEQ_SCAN_is_not_an_index_measurement(harness):
    """A listed, VALID, correctly-shaped index is still not an index that ANSWERED.

    ⛔ pgvector's own README (Troubleshooting → *"Why isn't a query using an
    index?"*): *"if the table is small, a table scan may be faster"*, and *"By
    default, pgvector performs exact nearest neighbor search, which provides
    perfect recall."* This harness seeds ~55 rows — the small-table case — and its
    ORACLE is an exact scan, so a Seq Scan reproduces the oracle verbatim and would
    publish `recall 1.000` under the index's name.

    Measured in review before this pin: a verified `record_hnsw_index` plus a
    record leg answering the oracle gave `EXIT: 0`, PARITY, with the ANN leg
    possibly never running. Grading the plan is what closes it, and the direction
    is fail-CLOSED: an unmeasurable arm becomes `cannot run`/exit 2, never green.
    """
    index = ("Index Scan", "record_hnsw_index")
    # the shape pgvector produces, under the Limit node — NOT at the root
    served = [
        {"Node Type": "Limit", "Plan": {
            "Node Type": "Index Scan", "Index Name": "record_hnsw_index",
            "Relation Name": "point"}},
    ]
    assert harness._require_index_plan(
        harness._plan_nodes(served), "record_hnsw_index") == (
            "Index Scan on record_hnsw_index")

    # ⛔ THE DEFECT: the index is verified, and the plan does not use it.
    seq_scan = [
        {"Node Type": "Limit", "Plan": {
            "Node Type": "Seq Scan", "Relation Name": "point"}},
    ]
    with pytest.raises(harness.CannotRun) as refused:
        harness._require_index_plan(harness._plan_nodes(seq_scan),
                                    "record_hnsw_index")
    assert "SEQ SCAN" in str(refused.value), str(refused.value)

    # a Seq Scan UNDER an index scan is still a scan of the table
    nested = [
        {"Node Type": "Index Scan", "Index Name": "record_hnsw_index",
         "Relation Name": "point", "Plans": [
             {"Node Type": "Seq Scan", "Relation Name": "point"}]},
    ]
    with pytest.raises(harness.CannotRun):
        harness._require_index_plan(harness._plan_nodes(nested),
                                    "record_hnsw_index")

    # ⛔ AND A DIFFERENT INDEX IS NOT THE VERIFIED ONE. A btree/pkey index that
    # happens to answer the ORDER BY would report the ANN index's name over a
    # non-ANN plan.
    other = [{"Node Type": "Index Scan", "Index Name": "point_pkey",
              "Relation Name": "point"}]
    with pytest.raises(harness.CannotRun) as wrong:
        harness._require_index_plan(harness._plan_nodes(other),
                                    "record_hnsw_index")
    assert "record_hnsw_index" in str(wrong.value), str(wrong.value)

    # the honest residual: with no verified name the check cannot bind, and saying
    # "Index Scan on <something>" is the most it can claim
    assert harness._require_index_plan(
        harness._plan_nodes(served), None) == "Index Scan on record_hnsw_index"
    assert index in {(n["Node Type"], n["Index Name"])
                     for n in harness._plan_nodes(served) if "Index Name" in n}


def test_vector_search_builds_its_statement_through_knn_statement():
    """⛔ THE PLAN CHECK IS ONLY EVIDENCE IF THE LEG RUNS THE STATEMENT IT EXPLAINS.

    `RecordStore.assert_index_scan` EXPLAINs `record_vector.knn_statement(...)` and
    the pin above asserts the harness asks for it. Nothing pinned the PRODUCTION
    side: re-inlining the kNN `SELECT` back into `vector_search` would leave that
    pin green (the harness still calls the builder) and leave every DB-free
    assertion green, while the read executed a statement the plan check never saw
    — the reviewed defect (an instrument proving something about its own spelling)
    restored with no red anywhere.

    So this is an AST pin, on the module the read lives in: `vector_search` must
    CALL `knn_statement`, and must not re-spell the kNN `SELECT` itself. Mutants:
    replacing the call with an inline `SELECT … ORDER BY … <=> …` reds BOTH
    assertions here.
    """
    tree = ast.parse(RECORD_MODULE_PATH.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "vector_search")
    called = {(c.func.id if isinstance(c.func, ast.Name)
               else getattr(c.func, "attr", ""))
              for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "knn_statement" in called, sorted(called)
    # ⛔ …AND NO LOCAL RE-SPELLING. `<=>` is the kNN operator: its presence in a
    # string literal inside this function means the SELECT is built here again.
    spellings = [node.value for node in ast.walk(fn)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not [text for text in spellings if "<=>" in text], [
        text[:80] for text in spellings if "<=>" in text]
    assert not [text for text in spellings if "ORDER BY" in text], [
        text[:80] for text in spellings if "ORDER BY" in text]


def test_the_plan_check_explains_the_RECORD_LEG_s_own_statement(harness):
    """⛔ AN EXPLAIN OF A RE-SPELLED SELECT PROVES SOMETHING ABOUT THE RE-SPELLING.

    The reviewed defect is one indirection out of K5: `require_schema` proved an
    index was LISTED while a seq scan answered. A plan check that re-builds the
    SELECT inside the harness would repeat it exactly — it would pin the harness's
    spelling, not the leg's statement. So `RecordStore.assert_index_scan` must
    ask `tortoise.record_vector` for the statement (`knn_statement`, the ONE
    definition `vector_search` also runs).
    """
    tree = ast.parse(MODULE_PATH.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "assert_index_scan")
    called = {(c.func.attr if isinstance(c.func, ast.Attribute)
               else getattr(c.func, "id", ""))
              for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "knn_statement" in called, sorted(called)
    # …and the EXPLAIN is of THAT text, never of a string literal SELECT
    assert "EXPLAIN (FORMAT JSON) " in MODULE_PATH.read_text()

    # the record store must also refuse through the seam, not inline the decision
    assert "_require_index_plan" in called, sorted(called)


def test_the_uri_authority_is_a_failover_list_not_one_host(harness):
    """`urlsplit().hostname` answers HALF the question libpq asks.

    To libpq the URI authority is a comma-separated FAILOVER LIST: for
    `postgresql://127.0.0.1:1,203.0.113.9/db` its `host` is
    `'127.0.0.1,203.0.113.9'` and it dials the SECOND entry when the first
    refuses (measured: `PQhostaddr` → `203.0.113.9`) — while `urlsplit().hostname`
    returns `127.0.0.1` and `.port` even RAISES. The realistic trigger is the
    failover itself: the local store is down, so the run seeds and then deletes in
    the remote one.
    """
    assert harness._record_dsn_hosts(
        "postgresql://127.0.0.1:1,203.0.113.9/tortoise_record"
    ) == ["127.0.0.1", "203.0.113.9"]
    for dsn in ("postgresql://127.0.0.1:1,203.0.113.9/tortoise_record",
                "postgresql://localhost:5432,db.prod.example.com/tortoise_record"):
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value), dsn
        harness._assert_local_record_dsn(dsn, True)   # the deliberate act survives
    # …and a single-host authority is still accepted, so this is about the LIST.
    harness._assert_local_record_dsn("postgresql://127.0.0.1:5432/db", False)


def test_a_unix_socket_path_is_local_not_a_remote_host(harness):
    """A socket PATH is local by construction — refusing it was an over-block.

    `--allow-remote-writes` is the flag for a deliberate REMOTE run; requiring it
    for `host=/var/run/postgresql` tells the operator the opposite of what they
    are doing.
    """
    for dsn in ("postgresql:///tortoise_record?host=/var/run/postgresql",
                "host=/var/run/postgresql dbname=tortoise_record"):
        harness._assert_local_record_dsn(dsn, False)


def test_the_vector_poison_arm_rereads_its_own_index_params(harness):
    """The arm's answers are re-read after `rebuild_with_ef`; its PARAMETERS must be too.

    Otherwise `poison-vector.json` publishes `efRuntime: 200` answers under
    `efRuntime: 10` parameters — the "a driver name is a claim, the artifact is
    the evidence" rule broken from the other side. Caught by an AST read of the
    branch, because building the real stores is a live action.
    """
    tree = ast.parse(MODULE_PATH.read_text())
    run_node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "run")
    reread = False
    for node in ast.walk(run_node):
        if not isinstance(node, ast.If):
            continue
        src = ast.unparse(node.test)
        if "poison" in src and "vector" in src:
            reread = reread or "index_params" in ast.unparse(node)
    assert reread, ("the vector-poison branch must re-read "
                    "`report[\"arms\"][\"graph_index\"][\"index_params\"]` after "
                    "the ef-diagnostic rebuild")
    # ⛔ AND `run()` MUST CALL THE GUARD. The decision function above can be
    # perfectly pinned while nothing calls it: `require_schema()` is the ONLY
    # caller of `_require_hnsw_indexdef`, and deleting the one line
    # `record.require_schema()` from `run()` left all 53 pins green (measured in
    # review). Every other guard here carries a call-site pin for this reason.
    assert "require_schema" in _called_names(run_node), sorted(_called_names(run_node))


def _called_names(node):
    """Names of every function called anywhere under `node`."""
    return {(c.func.attr if isinstance(c.func, ast.Attribute)
             else getattr(c.func, "id", ""))
            for c in ast.walk(node) if isinstance(c, ast.Call)}


def _arms_dict_keys(run_node, arm):
    """Keys of the dict assigned to `report["arms"][<arm>]` — BY ITS TARGET.

    ⛔ NOT "the first dict in `run()` that has a `driver` key". That locator
    matched the `graph_scan` arm too, so moving `"index": …` out of the record
    arm and into the scan arm still satisfied the assertion (measured in review).
    """
    for node in ast.walk(run_node):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Subscript):
            continue
        inner, outer = target.value, target
        if (isinstance(inner.slice, ast.Constant) and inner.slice.value == "arms"
                and isinstance(outer.slice, ast.Constant) and outer.slice.value == arm):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return set()


def _finally_called_names(run_node):
    """Names called inside `run()`'s `finally` blocks — the teardown path."""
    names = set()
    for node in ast.walk(run_node):
        if isinstance(node, ast.Try) and node.finalbody:
            for block in node.finalbody:
                names |= _called_names(block)
    return names


def test_the_teardown_is_pinned_IN_THE_FINALLY_not_merely_present(harness):
    """⛔ "run() calls reset() SOMEWHERE" IS NOT "the teardown runs".

    `run()` calls `store.reset()` TWICE — once before seeding (:1228) and once in
    the `finally` (:1392) — so an `assert "reset" in called` assertion is already
    satisfied by the PRE-SEED reset and cannot see the teardown removed. Measured
    in review: replacing the finally's `store.reset()` with `pass` left all 53
    pins green, and so did replacing `record.teardown()`. Both silent, and both
    are declaration R1 verbatim: the run prints PARITY, exits 0, and leaves a
    55-node cypher graph plus its HNSW index and a whole record tenant behind —
    the residue README §5.1 B1 says can no longer happen, invisible to every gated
    comparison because cleanup is outside all of them.
    """
    run_node = next(n for n in ast.walk(ast.parse(MODULE_PATH.read_text()))
                    if isinstance(n, ast.FunctionDef) and n.name == "run")
    in_finally = _finally_called_names(run_node)
    assert {"teardown", "reset"} <= in_finally, (
        "run()'s finally block no longer calls both record.teardown() and "
        f"store.reset() — R1 is unheld. Called in finally: {sorted(in_finally)}")


def test_the_graph_store_locality_guard_is_called_by_run(harness):
    """⛔ The RECORD-side locality guard got a call-site pin; the GRAPH side did not.

    `_assert_loopback(args.graph_host, "graph store", …)` is the only thing
    between a mistyped `--graph-host` (or an env-derived port pointed at a shared
    instance) and seeding a 55-node corpus into a graph this run does not own,
    then deleting it. Measured in review: deleting that call left all 53 pins
    green — `test_a_non_loopback_store_is_refused_without_an_explicit_opt_in`
    exercises the function, never the wiring.
    """
    run_node = next(n for n in ast.walk(ast.parse(MODULE_PATH.read_text()))
                    if isinstance(n, ast.FunctionDef) and n.name == "run")
    called = _called_names(run_node)
    assert {"_assert_loopback", "_assert_local_record_dsn"} <= called, sorted(called)


def test_a_conninfo_value_containing_a_url_is_not_parsed_as_a_uri(harness):
    """libpq dispatches URI-vs-conninfo by PREFIX; `"://" in dsn` asked the wrong thing.

    A whitespace-conninfo DSN whose ANY VALUE contains `://` —
    `host=db.prod.example.com password=vault://localhost/secret` — was parsed as a
    URI, so the "authority" was read out of the PASSWORD and the real remote host
    was never examined: the guard passed a DSN that dials, seeds and then deletes
    in a remote record. Measured with libpq (`PQconninfoParse` + `PQhostaddr`):
    libpq reads `host='db.prod.example.com'` from that string.
    """
    remote = (
        "host=db.prod.example.com dbname=tortoise_record password=vault://localhost/secret",
        "hostaddr=203.0.113.9 dbname=db application_name=postgresql://localhost/db",
        "host=db.prod.example.com sslrootcert=x://localhost/y",
    )
    for dsn in remote:
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value), dsn
        harness._assert_local_record_dsn(dsn, True)   # the deliberate act survives

    # …and the dispatch itself: a URI is a PREFIX, not a substring.
    assert harness._is_uri_dsn("postgresql://127.0.0.1/db")
    assert harness._is_uri_dsn(" postgres://127.0.0.1/db")   # leading space
    assert not harness._is_uri_dsn("host=127.0.0.1 dbname=x password=vault://localhost/y")
    # controls, so this is about the dispatch and not a blanket refusal
    for dsn in ("postgresql://127.0.0.1:5432/db", "host=127.0.0.1 dbname=x"):
        harness._assert_local_record_dsn(dsn, False)

    # `app_role_dsn` must dispatch the same way, or a certified conninfo DSN is
    # rebuilt in URI syntax and libpq cannot read it.
    rebuilt = harness.app_role_dsn(
        "host=127.0.0.1 dbname=x password=vault://localhost/secret")
    assert "options='" in rebuilt and "?options=" not in rebuilt, rebuilt

def test_a_question_mark_inside_userinfo_is_not_a_query_separator(harness):
    """libpq resolves the AUTHORITY first; the `?` separator is searched AFTER it.

    Everything from `://` to the first `/` is the authority, and a `?` inside it
    belongs to the userinfo/password — so `postgresql://127.0.0.1:p?ss@203.0.113.9:1/db`
    has host `203.0.113.9` (measured: `PQconninfoParse` gives
    user='127.0.0.1', password='p?ss', host='203.0.113.9', and `psql` DIALS it),
    while a split at the first `?` anywhere truncates the authority at `127.0.0.1`
    and certifies the remote store as local. The same rule fixes the inverse
    over-block: a loopback DSN whose PASSWORD carries a `?` is local.
    """
    remote = (
        "postgresql://127.0.0.1:p?ss@203.0.113.9:1/db",
        "postgresql://127.0.0.1?@db.prod.example.com/db",
        "postgresql://127.0.0.1:p?ss@db.prod.example.com:5432/db",
    )
    for dsn in remote:
        hosts = harness._record_dsn_hosts(dsn)
        assert hosts and all(host != "127.0.0.1" for host in hosts), (dsn, hosts)
        with pytest.raises(harness.CannotRun) as refused:
            harness._assert_local_record_dsn(dsn, False)
        assert "NON-LOCAL" in str(refused.value), dsn
        harness._assert_local_record_dsn(dsn, True)   # the deliberate act survives

    # the SPLIT ITSELF, so the rule is stated once and pinned
    head, query = harness._split_uri("postgresql://u:pa?ss@127.0.0.1:5432/db?host=x")
    assert head == "postgresql://u:pa?ss@127.0.0.1:5432/db", head
    assert query == "host=x", query

    # …and the over-block it removes: a `?` in the password of a LOOPBACK DSN.
    harness._assert_local_record_dsn("postgresql://u:pa?ss@127.0.0.1:5432/db", False)

def test_a_pathless_uri_still_carries_a_query(harness):
    """The authority ends at the first `/` after the userinfo — **or** at a `?`.

    ⛔ THE SECOND BOUNDARY, and the half-fix that misses it is a bypass: with no
    `/dbname` before the separator, "authority to the first `/` (or the end)"
    swallows the query whole, so `?host=` / `?hostaddr=` / `?service=` are never
    examined and the guard certifies a remote store as local. Measured against
    libpq's own `PQconninfoParse`:

        postgresql://127.0.0.1:5432?host=db.prod.example.com  -> host='db.prod.example.com'
        postgresql://[::1]?host=db.prod.example.com            -> host='db.prod.example.com'
        postgresql://127.0.0.1:5432?hostaddr=10.1.2.3          -> hostaddr='10.1.2.3'

    (`psql 'postgresql://127.0.0.1:1?host=203.0.113.9&connect_timeout=3'` fails
    with `connection to server at "203.0.113.9", port 1 failed`, which is the
    dial.) The userinfo-first rule pinned above must survive: a `?` BEFORE the
    `@` is userinfo, not a query.
    """
    cases = {
        "postgresql://127.0.0.1:5432?host=db.prod.example.com": "db.prod.example.com",
        "postgresql://localhost:5432?host=db.prod.example.com": "db.prod.example.com",
        "postgresql://[::1]?host=db.prod.example.com": "db.prod.example.com",
        "postgresql://127.0.0.1:5432?hostaddr=10.1.2.3&sslmode=disable": "10.1.2.3",
        "postgresql://127.0.0.1:5432?service=prod": None,   # service => unknowable
    }
    for dsn, remote in cases.items():
        hosts = harness._record_dsn_hosts(dsn)
        assert remote is None or remote in hosts, (dsn, hosts)
        with pytest.raises(harness.CannotRun):
            harness._assert_local_record_dsn(dsn, False)

    # …and the userinfo rule is not regressed by the second boundary
    harness._assert_local_record_dsn("postgresql://u:pa?ss@127.0.0.1:5432/db", False)
    with pytest.raises(harness.CannotRun):
        harness._assert_local_record_dsn("postgresql://127.0.0.1:p?ss@203.0.113.9:1/db", False)

    # THE SPLIT, both shapes, so neither half can drift silently
    assert harness._split_uri("postgresql://127.0.0.1:5432?host=x") == (
        "postgresql://127.0.0.1:5432", "host=x")
    assert harness._split_uri("postgresql://u:pa?ss@127.0.0.1:5432/db") == (
        "postgresql://u:pa?ss@127.0.0.1:5432/db", "")

    # app_role_dsn must not hand libpq two `?` (it rejects the result outright)
    built = harness.app_role_dsn("postgresql://127.0.0.1:5432?host=x")
    assert built.count("?") == 1, built
    assert "options=" in built and built.startswith("postgresql://127.0.0.1:5432?host=x&"), built

def _libpq():
    """libpq via ctypes, or None — the ONLY authority this file trusts for grammar."""
    import ctypes
    import ctypes.util
    from pathlib import Path

    candidates = []
    found = ctypes.util.find_library("pq")
    if found:
        candidates.append(found)
    # psycopg2-binary bundles libpq in site-packages, where find_library is blind
    for pattern in ("psycopg2_binary.libs/libpq*.so*", "psycopg2_binary.libs/libpq*.dylib"):
        candidates.extend(str(path) for path in Path(sys.prefix).rglob(pattern))
    for candidate in candidates:
        try:
            lib = ctypes.CDLL(candidate)
        except OSError:
            continue
        lib.PQconninfoParse.restype = ctypes.c_void_p
        lib.PQconninfoParse.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p)]
        lib.PQconninfoFree.argtypes = [ctypes.c_void_p]

        class Opt(ctypes.Structure):
            _fields_ = [("keyword", ctypes.c_char_p), ("envvar", ctypes.c_char_p),
                        ("compiled", ctypes.c_char_p), ("val", ctypes.c_char_p),
                        ("label", ctypes.c_char_p), ("dispchar", ctypes.c_char_p),
                        ("dispsize", ctypes.c_int)]

        def parse(dsn: str, lib=lib) -> dict:
            err = ctypes.c_char_p()
            handle = lib.PQconninfoParse(dsn.encode(), ctypes.byref(err))
            if not handle:
                return {"__error__": (err.value or b"").decode()}
            out = {}
            try:
                for option in ctypes.cast(handle, ctypes.POINTER(Opt)):
                    if option.keyword is None:
                        break
                    if option.val:
                        out[option.keyword.decode()] = option.val.decode()
            finally:
                lib.PQconninfoFree(ctypes.c_void_p(handle))
            return out

        return parse
    return None


def test_the_guard_agrees_with_libpq_across_the_dsn_grammar_axes(harness, monkeypatch):
    """A DIFFERENTIAL GATE, because sixteen rounds of hand-parsing libpq each found
    the next spelling by review rather than by test.

    The axes below are the ones that actually moved the boundary in those rounds —
    scheme dispatch, userinfo, the `?` separator on both sides of `@`, failover
    lists, brackets, percent-encoding, query overrides, and the noise a password
    may contain. The question each case asks is the one that matters: **does the
    guard see every host libpq would dial?** A shape the guard passes while libpq
    answers a non-loopback `host`/`hostaddr`/`service` is a bypass, and it fails
    here rather than in a review thread.

    Skipped only where libpq genuinely cannot be loaded (the guard is then the
    sole authority, and `test_*` above pin its decisions case by case).
    """
    parse = _libpq()
    if parse is None:
        pytest.skip("libpq is not loadable on this box")
    for env in ("PGHOST", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(env, raising=False)

    loopback = {"", "127.0.0.1", "localhost", "::1"}
    shapes = ["postgresql://127.0.0.1:5432/db", "postgresql://localhost/db",
              "postgresql://[::1]:5432/db", "postgresql://127.0.0.1:5432",
              "postgresql:///db", "postgresql://u@localhost/db",
              "postgresql://u:p@127.0.0.1/db", "postgresql://u@b@127.0.0.1/db",
              "postgresql://u:p@ss@127.0.0.1/db", "postgresql://127.0.0.1:p?ss@h/db",
              "postgresql://127.0.0.1:1,203.0.113.9/db", "postgresql://%2Ftmp/db",
              "postgresql://127.0.0.1/db@203.0.113.9/x",
              # PATHLESS + USERINFO — the axis that hid the last-`@` bypass: with no
              # `/` the "authority" spans the query, so a last-`@` boundary lands
              # inside a query value and the `?` looks like userinfo.
              "postgresql://u@127.0.0.1:5432", "postgresql://u@localhost",
              "postgresql://u@[::1]:5432"]
    overrides = ["?host=db.prod.example.com", "?hostaddr=10.1.2.3",
                 "?host=db.prod.example.com&password=p@localhost",
                 "?hostaddr=10.1.2.3&user=u@db.prod.example.com",
                 "?service=prod&application_name=a@b", "?host=127.0.0.1+",
                 "?sslmode=require", "?host=a@b"]

    bypasses = []
    for shape in shapes:
        for override in overrides:
            dsn = shape + override
            answer = parse(dsn)
            if "__error__" in answer:
                continue                      # libpq refuses it: nothing to dial
            dialled = [host for host in
                       (answer.get("hostaddr") or answer.get("host") or "").split(",")
                       if host and host not in loopback and not host.startswith("/")]
            if answer.get("service") is not None:
                dialled.append("<service>")   # matched against the guard's sentinel below
            if not dialled:
                continue
            seen = harness._record_dsn_hosts(dsn)
            def covered(want: str, seen=seen) -> bool:
                if want == "<service>":
                    return any("service" in str(got) for got in seen)
                return any(want == str(got) for got in seen)
            if not all(covered(want) for want in dialled):
                bypasses.append((dsn, dialled, seen))
            try:
                harness._assert_local_record_dsn(dsn, False)
            except harness.CannotRun:
                pass
            else:
                bypasses.append((dsn, dialled, "GUARD PASSED IT"))
    assert not bypasses, "the guard disagrees with libpq:\n" + "\n".join(map(str, bypasses))

def test_an_at_sign_in_a_query_value_does_not_move_the_userinfo_boundary(harness):
    """libpq ends userinfo at the FIRST `@`; a later `@` — in a password, in a host —
    is part of what follows, so the query separator after it is still live.

    ⛔ Measured with `PQconninfoParse`, and `psql` dials it:

        postgresql://u@localhost?host=203.0.113.9&password=p@localhost
            -> user='u', password='p@localhost', host='203.0.113.9'
        postgresql://u@127.0.0.1?hostaddr=203.0.113.9&password=p@127.0.0.1
            -> host='127.0.0.1', hostaddr='203.0.113.9'   (hostaddr is dialled)
        postgresql://a@b@127.0.0.1/db
            -> user='a', host='b@127.0.0.1'

    Choosing the LAST `@` moves the boundary into the query: the `?host=` then reads
    as userinfo, the authority is re-derived from the tail, and the guard sees only
    the loopback name it was told to expect.
    """
    shapes = (
        ("postgresql://u@localhost?host=203.0.113.9&password=p@localhost", "203.0.113.9"),
        ("postgresql://u@127.0.0.1?hostaddr=203.0.113.9&password=p@127.0.0.1", "203.0.113.9"),
        ("postgresql://u@127.0.0.1?host=db.prod.example.com&password=p@127.0.0.1",
         "db.prod.example.com"),
        ("postgresql://a@b@127.0.0.1/db", "b@127.0.0.1"),
    )
    for dsn, remote in shapes:
        assert remote in harness._record_dsn_hosts(dsn), (dsn, harness._record_dsn_hosts(dsn))
        with pytest.raises(harness.CannotRun):
            harness._assert_local_record_dsn(dsn, False)
    # the userinfo-first rule still holds in the other direction
    harness._assert_local_record_dsn("postgresql://u:p@127.0.0.1:5432/db", False)
    assert harness._split_uri("postgresql://u@127.0.0.1:5432?host=x") == (
        "postgresql://u@127.0.0.1:5432", "host=x")

def test_the_index_name_comes_from_the_index_relation(harness):
    """⛔ THE TABLE NAME IS NOT THE INDEX NAME, and the guard compares against the PLAN's.

    `require_schema` joins `pg_class` on `i.indrelid` — the INDEXED TABLE — so
    selecting that alias's `relname` returns `'point'` for every row. The name is
    then compared with the `Index Name` an `EXPLAIN` reports, which is the INDEX
    relation (`idx_point_embedding_vec_hnsw`); relations share one namespace, so a
    table and an index cannot share a name and the comparison **cannot** succeed.
    Fail-closed, and therefore invisible to every DB-free assertion about the
    verdict — it refused every database, so the record arm was unreachable and the
    tool could not produce its measurement at all (measured on a fresh
    `pgvector/pgvector:pg16`: `record_hnsw_index_name = 'point'`, plan
    `[('Index Scan','idx_point_embedding_vec_hnsw')]`, exit 2).

    A DB-free pin cannot run the SQL, so it pins the SQL's SHAPE: the name must
    come from the index relation, and that relation must be joined by
    `i.indexrelid`. Restoring `c.relname` alone, or dropping the `ic` join, reds
    this.
    """
    source = MODULE_PATH.read_text()
    start = source.index("SELECT ic.relname")
    query = source[start:source.index('")', start) + 2] if "\")" not in source[start:start + 400] else source[start:start + 400]
    assert "ic.relname" in query, query
    assert "JOIN pg_class ic ON ic.oid = i.indexrelid" in query, query
    assert "JOIN pg_class c ON c.oid = i.indrelid" in query, query
    assert "SELECT c.relname" not in source, "the indexed TABLE's name is not the index name"


# ── the exit-code contract, tied to the code the tool actually exits ───────

#: The tool run under a faked pre-3.12 interpreter. `version_info` is replaced
#: before `runpy` executes the file, so the file's OWN guard node runs and
#: nothing below it does — no old interpreter needed.
_FORCED_39 = (
    "import runpy, sys\n"
    "sys.version_info = (3, 9, 6, 'final', 0)\n"
    "runpy.run_path(sys.argv[1], run_name='__main__')\n"
)

README_PATH = ROOT / "docs" / "research" / "2026-11-20-13906-vector-parity" / "README.md"


def _documented_exit_codes(docstring: str) -> tuple[set[int], set[int]]:
    """The code table in the module docstring, and the one in §6 of the artifact README."""
    block = docstring[docstring.index("Exit codes:"):]
    in_docstring = {int(code) for code in re.findall(r"``(\d+)``", block)}
    readme = README_PATH.read_text()
    section = readme[readme.index("Falsifier exit codes"):]
    section = section[: section.index("\n## ")]
    in_readme = {int(code) for code in re.findall(r"^\| \*\*(\d+)\*\* \|", section, re.M)}
    return in_docstring, in_readme


def test_every_exit_code_the_tool_can_take_is_documented_in_both_tables(harness):
    """★ §6's repro loop reads ONE BIT — `echo exit=$?` — so an undocumented code is unreadable.

    The artifact states the falsifier's exit codes are *"part of the contract, not
    decoration"*, and the repro recipe in §6 is one `echo exit=$?` per poison. A
    code the tool can exit but no table lists is therefore an answer the recipe's
    own reader has to guess at — which is the class of harm this harness exists to
    close (a cannot-run wearing a verdict's number, #13906 rounds I1/O1).

    MEASURED, and the reason this row exists: the #5128 interpreter guard at
    `tools/vector_parity_13906.py:96` exits **4**, and neither the module's own
    exit-code block nor §6's table carried a 4 — the guard's fix was pinned in
    code and left the two tables that document the contract one row short.

    Pinned as an EQUALITY between the two tables plus the code a REAL pre-3.12 run
    exits, not as a literal `{0,1,2,3,4}`: a new code may be added deliberately,
    but it cannot be added to one table only, and it cannot be left out of both
    while the tool exits it.
    """
    doc_codes, readme_codes = _documented_exit_codes(harness.__doc__ or "")
    assert doc_codes == readme_codes, (
        "the module docstring's exit table and §6 of the artifact README disagree: "
        f"docstring={sorted(doc_codes)} readme={sorted(readme_codes)}"
    )
    probe = subprocess.run(
        [sys.executable, "-c", _FORCED_39, str(MODULE_PATH)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert probe.returncode != 0, (
        f"the interpreter guard must refuse an old interpreter, exited 0: {probe.stderr!r}"
    )
    assert probe.returncode in doc_codes, (
        f"the tool exits {probe.returncode} under a pre-3.12 interpreter and no table "
        f"documents it — documented: {sorted(doc_codes)}; stderr={probe.stderr!r}"
    )

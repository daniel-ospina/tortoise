"""#10017 — the purge gate's own pins: parity rules, refusal arms, exit codes.

`tools/turn_parity_census.py` is the executable gate the destructive half of
#10000 (#10017) waits on. A gate that has only ever been observed passing is
decoration, so these tests pin the NEGATIVE arms first — a record missing a
turn row, a mismatched text, a lost vector each REFUSE — and then the pass
shape, the anomaly partition, and the exit-code contract.

The live end-to-end demonstration (real capture → real journal replay into a
throwaway record → census exit 0 → one row removed → exit 2) is a lane-run
artifact on throwaway stores, not a CI test; what CI can and must hold is the
comparison core and the CLI contract, which is everything the verdict is made
of. The STORE LEGS are pinned separately — `read_record_turns` through a
STUBBED connection (its scoped door, its SQL column contract and the refusal
each failure raises), `read_graph_turns` through a fake `g` (paging, the
aggregate cross-check, and the classification of every row the store returns),
AND through four real throwaway EMBEDDED redislite stores (#11112/#11113: the
presence check, the write-freedom pins and the LOST-store arm can only be
measured against a real `GRAPH.LIST` — a fake cannot show that a typo'd
`--graph-name` mints a graph). An unpinned door is exactly how an unscoped read
(#10024) would certify vacuously while every core case stayed green.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools.turn_parity_census as tpc

# ── fixtures: the two dictionaries the store legs produce ──────────────────

def _graph_node(tid: str, sid: str, **over) -> dict:
    node = {
        "id": tid,
        "content": f"[user] body of {tid}",
        "speaker": "user",
        "status": "draft",
        "createdAt": "2026-10-25T00:00:00+00:00",
        "pointKind": "event",
        "embedding": None,
        "sessions": {sid},
    }
    node.update(over)
    return node


def _record_row(tid: str, **over) -> dict:
    row = {
        "node_key": tid,
        "content": f"[user] body of {tid}",
        "speaker": "user",
        "status": "draft",
        "created_at": "2026-10-25T00:00:00+00:00",
        "point_kind": "event",
        "is_episodic": True,
        "embedding": None,
    }
    row.update(over)
    return row


@pytest.mark.parametrize("graph_over,record_over,field", [
    # ⛔ THE P0 SHAPE OF THE EXACT-FIELD `None` POLARITY (review P2, measured).
    # A record row PRESENT with a NULL transcript against a graph turn that
    # holds text is NOT parity: the row has the right key and no text, so a
    # delete destroys the only copy. The mutation that makes it fail open is
    # `gval is None **or** rval is None -> continue`, which kept all 94 tests
    # green — none of the five field-mismatch cases carries a `None` on one
    # side. Only `graph None AND record None` may be skipped.
    ({"content": "[user] SECRET REAL TEXT"}, {"content": None}, "text"),
    ({"speaker": "assistant"}, {"speaker": None}, "speaker"),
])
def test_a_record_row_PRESENT_with_a_NULL_field_is_a_mismatch_not_parity(
        graph_over, record_over, field):
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", **graph_over)}
    rows = {"s1_t0": _record_row("s1_t0", **record_over)}
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert _problems(report, "s1")[0]["field"] == field
    assert _problems(report, "s1")[0]["problem"] == "field_mismatch"


def test_the_exact_fields_skip_ONLY_when_BOTH_sides_are_None():
    """The other half of the polarity above: `None` on BOTH sides is a real
    match (a turn that carried nothing), and must keep passing — so the pin
    above cannot be satisfied by refusing every `None`."""
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content=None, speaker=None)}
    rows = {"s1_t0": _record_row("s1_t0", content=None, speaker=None)}
    report = _census(nodes, rows)
    assert report["verdict"] == "CLEAN"
    assert _problems(report, "s1") == []


def _stores(nodes: dict[str, dict], rows: dict[str, dict]) -> tuple[dict, dict]:
    return ({"nodes": nodes},
            {"rows_by_key": rows, "non_turn_shaped": 0})


def _census(nodes, rows):
    graph, record = _stores(nodes, rows)
    return tpc.census(graph, record, sample=5)


def _census_titled(graph_name):
    """The same census, with the graph identity the report must now carry."""
    graph, record = _stores({"s1_t0": _graph_node("s1_t0", "s1")},
                            {"s1_t0": _record_row("s1_t0")})
    return tpc.census(graph, record, sample=5, graph_name=graph_name)


def _problems(report: dict, session: str) -> list[dict]:
    for v in report["parity"]["failed_sessions"]:
        if v["session"] == session:
            return v["problems"]
    return []


# ── the negative arms: a gate that cannot refuse is decoration ─────────────

def test_a_missing_record_row_refuses_and_names_the_key():
    """THE #10017 precondition: a graph turn the record does not hold."""
    nodes = {"s1_t0": _graph_node("s1_t0", "s1"),
             "s1_t1": _graph_node("s1_t1", "s1")}
    rows = {"s1_t0": _record_row("s1_t0")}  # s1_t1 deliberately absent
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert report["dry_run"]["would_delete_turn_nodes"] == 0
    assert report["dry_run"]["blocked_sessions"] == ["s1"]
    problems = _problems(report, "s1")
    assert problems == [{"node": "s1_t1", "field": "key",
                         "problem": "missing_in_record",
                         "graph": {"id": "s1_t1"}, "record": None}]


@pytest.mark.parametrize("graph_over,record_over,field", [
    ({"content": "[user] real text"}, {"content": "[user] TAMPERED"}, "text"),
    ({"speaker": "user"}, {"speaker": "assistant"}, "speaker"),
    ({"status": "promoted"}, {"status": "draft"}, "status"),
    ({"createdAt": "2026-10-25T00:00:00+00:00"},
     {"created_at": "2026-10-26T00:00:00+00:00"}, "createdAt"),
    ({"pointKind": "event"}, {"point_kind": "claim"}, "pointKind"),
])
def test_field_mismatches_refuse_and_name_the_field(graph_over, record_over,
                                                    field):
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", **graph_over)}
    rows = {"s1_t0": _record_row("s1_t0", **record_over)}
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    problems = _problems(report, "s1")
    assert problems and problems[0]["field"] == field
    assert problems[0]["problem"] == "field_mismatch"


def test_a_lost_vector_refuses():
    """The graph holds a vector the record does not — the purge would lose it."""
    vec = [0.1, 0.2, 0.3]
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=vec)}
    rows = {"s1_t0": _record_row("s1_t0", embedding=None)}
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert _problems(report, "s1")[0]["field"] == "embedding"


def test_vector_length_mismatch_refuses():
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=[0.1, 0.2])}
    rows = {"s1_t0": _record_row("s1_t0", embedding=[0.1, 0.2, 0.3])}
    assert _census(nodes, rows)["verdict"] == "REFUSED"


def test_a_component_mismatch_refuses():
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=[0.1, 0.2])}
    rows = {"s1_t0": _record_row("s1_t0", embedding=[0.1, 0.9])}
    assert _census(nodes, rows)["verdict"] == "REFUSED"


# ── the pass shape ──────────────────────────────────────────────────────────

def test_exact_parity_passes_and_the_dry_run_counts():
    nodes = {f"s1_t{i}": _graph_node(f"s1_t{i}", "s1") for i in range(3)}
    rows = {f"s1_t{i}": _record_row(f"s1_t{i}") for i in range(3)}
    report = _census(nodes, rows)
    assert report["verdict"] == "CLEAN"
    # ⛔ THE ARTIFACT NAMES ITSELF (#11004 review, gate-fail-open). The consumer
    # (`tortoise/cache_eviction.py::load_parity_report`) REFUSES a report that names no
    # instrument, because a nameless one is attributable to any population by whoever
    # hands it over — so the honest report must carry this field or requiring it would
    # be a gate that can never open.
    assert report["instrument"] == tpc.INSTRUMENT == "tools/turn_parity_census.py"
    assert report["dry_run"]["would_delete_turn_nodes"] == 3
    assert report["parity"]["failed_sessions"] == []
    session = report["dry_run"]["per_session_sample"][0]
    assert session["count"] == 3
    # The sampled turn's own fields. `content_head` is a 60-CHARACTER HEAD of
    # the transcript, emitted on purpose for the operator's eyeball comparison —
    # so this artifact is NOT content-free; what it never emits is the whole
    # transcript of any turn.
    assert session["sample"][0]["id"] == "s1_t0"
    assert session["sample"][0]["speaker"] == "user"
    assert session["sample"][0]["createdAt"] == "2026-10-25T00:00:00+00:00"


def test_the_sample_carries_a_60_CHARACTER_HEAD_not_the_transcript():
    """The exposure claim, PINNED — the previous assert could not hold it.

    It was `len(content_head) <= 60` against a fixture whose content is 20
    characters, so widening the slice to `[:600]` (a 10x-wider transcript head)
    or replacing it with `""` left the suite green: the property the comment
    above states was unpinned. The body here is longer than the slice, and the
    assertion is EXACT — the head, not a bound.
    """
    body = "[user] " + ("0123456789" * 30)  # 307 characters
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content=body)}
    rows = {"s1_t0": _record_row("s1_t0", content=body)}
    assert _census(nodes, rows)["verdict"] == "CLEAN"
    sample = _census(nodes, rows)["dry_run"]["per_session_sample"][0]["sample"][0]
    assert sample["content_head"] == body[:60]
    assert len(sample["content_head"]) == 60
    assert sample["content_head"] != body


def test_binary64_record_vector_matches_binary32_graph_vector():
    """The record's jsonb floats are binary64; the graph's vecf32 is binary32.

    Equality through binary32 rounding — the storage the graph itself applies
    — is parity, or every real capture would fail the census.
    """
    f32 = lambda v: tpc._f32(float(v))  # noqa: E731
    vec64 = [0.1, 0.2, 0.3]  # not exactly representable in binary32
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=[f32(v) for v in vec64])}
    rows = {"s1_t0": _record_row("s1_t0", embedding=vec64)}
    assert _census(nodes, rows)["verdict"] == "CLEAN"


def test_the_embedding_band_is_EXACT_literals_not_the_function_under_test():
    """Discriminating literals: one binary32 ULP apart must REFUSE.

    The sibling test builds its graph side with `tpc._f32`, and the comparison
    applies `tpc._f32` — so a mutation that WIDENS the band ("floating point,
    use a tolerance") stayed green, which is the one change that turns a
    different vector into parity on an irreversible delete. These cases carry
    literal values on both sides, so no helper can satisfy both:

        f32(0.1)              = 0.10000000149011612   (the graph's value)
        next f32 up           = 0.10000000894069672   (~6e-9 away)

    A tolerance of 1e-6 would call that second pair equal. Parity must not.
    """
    graph_vec = [0.10000000149011612]   # exactly f32(0.1)
    same_at_binary64 = [0.1]            # binary64 0.1 rounds to the same f32
    one_ulp_up = [0.10000000894069672]  # the NEXT binary32 value

    assert _census({"s1_t0": _graph_node("s1_t0", "s1", embedding=graph_vec)},
                   {"s1_t0": _record_row("s1_t0", embedding=same_at_binary64)}
                   )["verdict"] == "CLEAN"

    report = _census(
        {"s1_t0": _graph_node("s1_t0", "s1", embedding=graph_vec)},
        {"s1_t0": _record_row("s1_t0", embedding=one_ulp_up)})
    assert report["verdict"] == "REFUSED"
    problem = _problems(report, "s1")[0]
    assert problem["field"] == "embedding"
    assert "binary32" in problem["detail"]


def test_record_superset_passes_even_though_counts_differ():
    """The post-cutover append: record ⊇ graph per key is the purge criterion.

    A session captured before the cutover and appended to after it holds graph
    nodes for the old window and record rows for the whole stream; deleting
    the graph copies loses nothing, so the census passes and reports the
    count fact instead of refusing on it.
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1"),
             "s1_t1": _graph_node("s1_t1", "s1")}
    rows = {"s1_t0": _record_row("s1_t0"), "s1_t1": _record_row("s1_t1"),
            "s1_t2": _record_row("s1_t2")}  # appended post-cutover
    report = _census(nodes, rows)
    assert report["verdict"] == "CLEAN"
    assert report["dry_run"]["would_delete_turn_nodes"] == 2
    clean = report["parity"]["clean_sessions"]
    assert clean and clean[0]["counts_equal"] is False

def test_graph_without_vector_passes_when_record_holds_one():
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=None)}
    rows = {"s1_t0": _record_row("s1_t0", embedding=[0.1])}
    assert _census(nodes, rows)["verdict"] == "CLEAN"


# ── the population rule: nothing silently invisible ─────────────────────────

def test_anomalies_refuse_the_census_and_are_reported():
    """Orphans, non-decimal suffixes, cross-wirings: never deleted, never hidden.

    An unclassifiable node is OUTSIDE the deterministic id space the sweep
    touches (`_capture_turn_ids` retains-but-cannot-place), so it is excluded
    from the purge set and REPORTED — and, because a shape-based sweep
    (`is_episodic AND pointKind='event'`) would delete it anyway, its presence
    REFUSES the census (exit 2). Excluding a node from the purge set is not the
    same as certifying it: `CLEAN` must mean "measured the whole population and
    it matched", never "measured part of it and the part matched". This test
    previously pinned `CLEAN` here — the arm was pinned in the WRONG
    DIRECTION, which is how a gate opens over a node nothing compared.
    """
    nodes = {
        "s1_t0": _graph_node("s1_t0", "s1"),          # in population
        "s2_t0": _graph_node("s2_t0", "s2"),          # in population
        "orphan_t0": _graph_node("orphan_t0", "orphan", sessions=set()),
        "s1_t3x": _graph_node("s1_t3x", "s1"),        # suffix not all-decimal
        # wired to a session the id does not name — reported, never guessed
        "sec_t0_t3": _graph_node("sec_t0_t3", "sec"),
    }
    nodes["s1_t0"]["sessions"] = {"s1", "s1_t3x"}     # cross-wired shape
    rows = {"s1_t0": _record_row("s1_t0"), "s2_t0": _record_row("s2_t0")}
    report = _census(nodes, rows)
    # ONLY the unambiguous s2 node is a purge candidate; s1's cross-wired
    # node, the orphan and the mismatched wiring are excluded and reported —
    # and their presence is what makes the verdict REFUSED.
    assert report["verdict"] == "REFUSED"
    assert report["refused_for"] == ["outside_population"]
    assert report["dry_run"]["would_delete_turn_nodes"] == 1
    assert report["dry_run"]["would_delete_ids"] == ["s2_t0"]
    anomalies = report["anomalies"]
    assert any(a["id"] == "orphan_t0" for a in anomalies["orphan"])
    assert any(a["id"] == "s1_t3x" for a in anomalies["non_decimal_suffix"])
    assert any(a["id"] == "s1_t0" for a in anomalies["cross_wired"])
    mismatch = [a for a in anomalies["session_mismatch"]
                if a["id"] == "sec_t0_t3"]
    assert mismatch and mismatch[0]["wired_session"] == "sec"
    assert mismatch[0]["derived_session"] == "sec_t0"
    # ...and the population count still sees every turn-shaped node.
    assert report["population"]["turn_shaped_nodes"] == 5
    assert report["population"]["outside_population"] == 4


def test_an_operator_flagged_node_is_never_counted_as_purgeable():
    """`is_operator` is an anomaly, not a parity field — say it in BOTH lines.

    The report used to print "1 operator_flagged (excluded from the purge set)"
    and "would delete 1 turn node(s)" for the same node, because
    `in_population` never consulted `is_operator`. One of the two had to be
    wrong, and the delete count is the one that costs a transcript.
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", is_operator=True)}
    rows = {"s1_t0": _record_row("s1_t0")}
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert report["dry_run"]["would_delete_turn_nodes"] == 0
    assert [a["id"] for a in report["anomalies"]["operator_flagged"]] == ["s1_t0"]


def test_turn_shaped_nodes_are_counted_whatever_the_record_says():
    nodes = {"s1_t0": _graph_node("s1_t0", "s1")}
    report = _census(nodes, {})
    assert report["population"]["turn_shaped_nodes"] == 1
    assert report["population"]["sessions_with_graph_turns"] == 1


# ── the exit-code contract (a caller keys on it) ───────────────────────────

def _argv(*extra):
    return ["--uri", "docker://:@127.0.0.1:1/g", "--record-dsn",
            "postgresql://x/y", "--org-id", "o", "--graph-id", "g", *extra]


def test_usage_errors_exit_one_not_two(monkeypatch):
    """Exit 2 is REFUSED; a flag mistake must never read as a parity failure."""
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    # A missing DSN is a usage/configuration miss: exit 1, a plain return.
    assert tpc.main(["--uri", "docker://:@127.0.0.1:1/g"]) == 1
    # An invalid flag VALUE takes argparse's error() path — overridden to 1 so
    # "you typed --sample -1" is distinguishable from "the record is missing
    # turns" for any caller keying on the exit code.
    with pytest.raises(SystemExit) as exc:
        tpc.main(_argv("--sample", "-1"))
    assert exc.value.code == 1


def test_missing_tenant_refuses_to_guess(monkeypatch):
    monkeypatch.delenv("TORTOISE_RECORD_ORG_ID", raising=False)
    monkeypatch.delenv("TORTOISE_RECORD_GRAPH_ID", raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://x/y")
    assert tpc.main(["--uri", "docker://:@127.0.0.1:1/g"]) == 1


def test_unreadable_side_exits_three_never_zero(monkeypatch, tmp_path):
    """The P0 shape: an unreadable record must not certify parity vacuously.

    ⛔ THE AXIS IS THE RECORD, NOT THE GRAPH (review observation, fixed here).
    With an EMPTY stub graph this passed for the wrong reason: the zero-node
    guard returns 3 before the record leg is ever opened, so a record that
    raises was never reached and the docstring named an axis the body did not
    exercise. The graph now holds one turn, so the only path to exit 3 is the
    raising record read — asserted below rather than assumed.
    """
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise tpc.CensusError("the record could not be opened")
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {"s1_t0": _graph_node("s1_t0", "s1")}})
    monkeypatch.setattr(tpc, "read_record_turns", boom)
    assert tpc.main(_argv()) == 3
    assert calls, "the RECORD leg was never opened — exit 3 came from elsewhere"


def test_graph_name_help_names_the_graph_leg_only(capsys):
    """A flag's help must not claim a scope the code does not have (review P3).

    `--graph-name` is passed to `read_graph_turns` only — the record tenant is
    `--org-id`/`--graph-id` — while the argparse help read "applies to BOTH
    legs", contradicting the module contract at the same head. The help is the
    stale half, so it is pinned here against the real parser output rather than
    the docstring: on the pre-fix string this fails on `BOTH legs`.
    """
    with pytest.raises(SystemExit) as exc:
        tpc.main(["--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    at = text.index("  --graph-name")
    rest = text[at + 1:]
    block = " ".join(rest[:rest.index("\n  --")].split())
    assert "BOTH legs" not in block
    assert "GRAPH leg only" in block
    assert "--org-id/--graph-id" in block


def test_the_report_carries_the_graph_it_measured():
    """⛔ THE ARTIFACT MUST NAME ITS TARGET, OR THE CONSUMER'S CHECK CANNOT FAIL.

    Review P1 on PR #11004: `census()` emitted no graph identity, so
    `cache_eviction.load_parity_report` filled the caller's own name in — and the
    binding that exists to stop a CLEAN census of graph A licensing a sweep of graph B
    proved only that the operator typed one string. The identity is authored HERE, by
    the leg that read the graph, so the consumer has something to compare.
    """
    report = _census_titled("live-v4")

    assert report["graph"] == "live-v4"


def test_an_unattributed_census_says_SO_instead_of_omitting_the_field():
    """`None` is emitted, never dropped: a shape from before the field existed must not
    be indistinguishable from an artifact that knows it has no target."""
    nodes = {"s1_t0": _graph_node("s1_t0", "s1")}
    report = _census(nodes, {"s1_t0": _record_row("s1_t0")})

    assert "graph" in report
    assert report["graph"] is None


def test_the_cli_writes_the_measured_graph_into_the_json_artifact(
        monkeypatch, tmp_path, capsys):
    """End-to-end: `--uri` → the JSON an operator hands to `--parity-report` names the
    graph the run actually addressed (derived by the canonical resolver, not the argv)."""
    out = tmp_path / "r.json"
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {"s1_t0": _graph_node("s1_t0", "s1")}})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {"s1_t0": _record_row("s1_t0")},
        "non_turn_shaped": 0})

    assert tpc.main(_argv("--json", str(out))) == 0
    assert json.loads(out.read_text())["graph"] == "g"
    assert "graph      : g" in capsys.readouterr().out


def test_resolve_graph_name_uses_the_canonical_uri_derivation():
    """Not a second URI parser: the URI arm is `resolve_db_endpoint`, the derivation
    `FalkorProjection.from_uri` itself calls — so the emitted name is the one the
    connection addressed. A hand-rolled parse here would be the same defect one layer
    down (the `--graph-name` override and the embedded default must both still work)."""
    assert tpc.resolve_graph_name("docker://h:1/live-v4", None, None) == "live-v4"
    assert tpc.resolve_graph_name("docker://h:1/live-v4", None, "team_acme") \
        == "team_acme"
    assert tpc.resolve_graph_name(None, "/tmp/x.db", None) == "tortoise"
    assert tpc.resolve_graph_name("docker://h:1/", None, None) == "tortoise"


def test_resolve_graph_name_never_treats_an_EMPTY_uri_as_no_uri():
    """`--uri "$UNSET_VAR"` must not resolve to the embedded store's graph name.

    Measured before the fix (review P2 on PR #11097): the empty value fell through the
    truthiness test to the embedded default, so a run that read NOTHING but the local
    embedded graph emitted `graph: "tortoise"` — a name the cache census accepts for a
    hosted graph of the same name. A shell accident licensed a sweep of an instance the
    census never touched, which is the `different instance` shape the binding exists to
    refuse.
    """
    with pytest.raises(ValueError):
        tpc.resolve_graph_name("", None, None)


@pytest.mark.parametrize("flag,extra", [
    ("--uri", []),
    ("--db-path", []),
    # ⛔ `--graph-name ""` won the `graph_name is not None` branch and was written
    # into the artifact as `graph: ""` while the EMBEDDED read fell back to
    # `tortoise` — an artifact that misstates its own target AND can never be
    # loaded, because the consumer refuses a blank name (review P2, cycle 2).
    ("--graph-name", ["--uri", "docker://:@127.0.0.1:1/g"]),
])
def test_an_EMPTY_graph_target_flag_refuses_by_name(flag, extra, capsys):
    """An empty flag value is NOT "no flag" — the rule the record flags already state.

    Wrong outcome before the fix: `--uri ""` fell through to the EMBEDDED store, exited
    0 CLEAN, and (once this tool authors the identity) wrote a report naming a graph on
    an instance the run never opened (review P2 on #11097).
    """
    code = tpc.main([flag, "", *extra, "--record-dsn", "postgresql://x/y",
                     "--org-id", "o", "--graph-id", "g"])

    assert code == 1
    err = capsys.readouterr().err
    assert "EMPTY" in err and flag in err
    assert "EMBEDDED" in err


def test_a_broken_tortoise_import_is_a_CensusError_not_a_traceback(monkeypatch):
    """The URI arm's import is guarded, like `read_graph_turns`' own.

    Measured before the fix (review P2, cycle 2): the new unguarded
    `from tortoise.projection import resolve_db_endpoint` raised ImportError straight
    out of `main()`, which exits the interpreter with **1** — the documented USAGE
    code — so a broken install read as a mistyped flag and the operator retried flags
    instead of repairing the environment.
    """
    import sys as _sys

    monkeypatch.setitem(_sys.modules, "tortoise.projection", None)

    with pytest.raises(tpc.CensusError) as excinfo:
        tpc.resolve_graph_name("docker://h:1/g", None, None)

    assert "tortoise import failed" in str(excinfo.value)


def test_main_maps_clean_to_zero_and_refused_to_two(monkeypatch, tmp_path):
    nodes = {"s1_t0": _graph_node("s1_t0", "s1")}
    graph = {"nodes": nodes}
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: graph)
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {}, "non_turn_shaped": 0})
    assert tpc.main(_argv("--json", str(tmp_path / "r.json"))) == 2

    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {"s1_t0": _record_row("s1_t0")},
        "non_turn_shaped": 0})
    assert tpc.main(_argv()) == 0


def test_a_zero_turn_graph_is_not_evidence(monkeypatch):
    """A drained graph and a wrong --graph-name are the same read.

    `FalkorProjection.from_uri` does not verify the graph exists, so a typo
    opens a fresh EMPTY graph and a `CLEAN`/exit 0 there would certify parity
    over a target the operator never reached — the mirror of the wrong-tenant
    record guard the docstring defends. The operator asserts the drainage with
    `--expect-empty` (the post-purge re-run, #10017's own last step).
    """
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {"nodes": {}})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {}, "non_turn_shaped": 0})
    assert tpc.main(_argv()) == 3
    assert tpc.main(_argv("--expect-empty")) == 0


def test_expect_empty_never_blesses_a_record_side_that_holds_nothing(
        monkeypatch, capsys):
    """The flag relaxes the GRAPH axis only, and it is the axis to get wrong.

    `--expect-empty` exists so a DRAINED target can certify after the purge. It
    is therefore the one flag a purge can be run under, and the one flag whose
    scope decides whether the gate protects anything on that run. A graph that
    still holds turn nodes while the record holds zero rows is the P0 shape —
    deleting on that reading loses every turn — and it must stay REFUSED (2)
    with the flag passed, not become a pass because the operator asserted an
    empty side for the OTHER axis.

    Measured on prod 2026-10-27 and the reason this pin exists: the record's
    every table held 0 rows while `/health` reported `record.armed=true,
    ok=true` (the counters reset on deploy, so they do not report volume), and
    a tenant at its capture ceiling writes nothing at all. "The record is
    armed" is not "the record holds the turns", so the purge run above is a
    reachable state, not a hypothetical.
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1"),
             "s1_t1": _graph_node("s1_t1", "s1")}
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {"nodes": nodes})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {}, "non_turn_shaped": 0})

    assert tpc.main(_argv("--expect-empty")) == 2
    out = capsys.readouterr()
    # NOT `"CLEAN" not in out`: the output legitimately contains the word as a
    # COUNT (`0 session(s) CLEAN`), so that assertion is a substring of both the
    # passing and the inverted reading and pins nothing. The verdict line is the
    # discriminating one, and the clean-session count is asserted explicitly.
    assert "verdict    : REFUSED" in out.out
    assert "0 session(s) CLEAN" in out.out
    assert "REFUSED  session s1" in out.out
    assert "would delete 0 turn node(s)" in out.out


def test_expect_empty_does_not_suppress_a_PARTIALLY_copied_session(
        monkeypatch):
    """One row of two is the half-copied state `--expect-empty` must not hide.

    A copy that stopped midway leaves the record holding SOME of a session's
    turns — the state a purge gate is most likely to meet in practice, and the
    one a coarse "the record has rows" check would pass.
    """
    nodes = {"s2_t0": _graph_node("s2_t0", "s2"),
             "s2_t1": _graph_node("s2_t1", "s2")}
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {"nodes": nodes})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {"s2_t0": _record_row("s2_t0")},
        "non_turn_shaped": 0})
    assert tpc.main(_argv("--expect-empty")) == 2


def test_an_ALL_unidentifiable_graph_is_not_reported_as_an_EMPTY_graph(
        monkeypatch, capsys):
    """The population is "episodic nodes", not "keyable episodic nodes".

    Every `unidentifiable` node was invisible to the zero-node guard, so a
    graph whose episodic nodes ALL lacked a usable `t.id` exited 3 with "the
    graph holds ZERO is_episodic nodes" — factually wrong about the target,
    and it sends the operator at a wrong `--graph-name` instead of at the nodes
    the graph actually holds. Those nodes are `non_string_id` anomalies, which
    REFUSE the census: exit 2, with the ids listed and nothing certified.
    """
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {}, "unidentifiable": [{"id": "2", "id_type": "int"}]})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {}, "non_turn_shaped": 0})
    assert tpc.main(_argv()) == 2
    assert "ZERO is_episodic nodes" not in capsys.readouterr().err


def test_a_graph_read_row_of_the_wrong_shape_is_an_unreadable_side(
        monkeypatch):
    """An unclassifiable row must not be skipped into a smaller population."""
    class _G:
        def query(self, cypher, params=None):
            class _R:
                result_set = [["only", "a", "partial"]] if params else [[1]]
            return _R()

    class _Proj:
        g = _G()

        class _DB:
            @staticmethod
            def list_graphs():
                return ["g"]

        db = _DB()

        # `from_uri('docker://…/g')` resolves the graph name from the URI path;
        # the fake carries it so the presence pre-check asks about the graph the
        # real projection would have addressed, not the default.
        graph_name = _graph_name = "g"

        @classmethod
        def from_uri(cls, *a, **k):
            return cls()

    from tortoise.projection import FalkorProjection
    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _Proj()))
    with pytest.raises(tpc.CensusError):
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)


def test_a_non_sequence_graph_vector_refuses_rather_than_reading_as_absent(
        monkeypatch):
    """Polarity: in a gate, "cannot parse the vector" must REFUSE, not pass.

    The graph leg used to narrow an unexpected container to `None` ("no
    vector"), which `_embedding_parity` reads as the record holding a
    superset — a PASS. A gate may not pass on evidence it could not read.
    """
    class _G:
        def query(self, cypher, params=None):
            class _R:
                result_set = ([[1, "s1_t0", "c", "user", "draft", None,
                                "event", "not-a-vector", False, ["s1"]]]
                              if params else [[1]])
            return _R()

    class _Proj:
        g = _G()

        class _DB:
            @staticmethod
            def list_graphs():
                return ["g"]

        db = _DB()
        graph_name = _graph_name = "g"

    from tortoise.projection import FalkorProjection
    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _Proj()))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "not a sequence" in str(exc.value)


def test_a_DRIVER_vector_type_is_READ_not_refused(monkeypatch):
    """A `vecf32` read-back is NOT contractually a `list` — accept what the
    repo's own sanitizer accepts, or the gate can never open.

    `tortoise/embeddings.py::stamp_journal_embedding` accepts ANY non-string
    iterable for the same value from the same store, and says why: "a graph
    read-back (`t.embedding` after a `vecf32` write, #5004 round-3) can arrive
    as a numpy array or a driver vector type". The graph leg accepted only
    `list`/`tuple`, so on such a driver EVERY vector-carrying turn raised
    `CensusError` → exit 3 for the whole graph: the census could never certify
    on exactly the graphs where vector parity is the risk, and the gate could
    never open. Refusing a container the store itself hands back is a
    MISCLASSIFICATION, not caution.
    """
    class _DriverVector:  # no list base, no `.shape`: iteration is the contract
        def __init__(self, values):
            self._values = values

        def __iter__(self):
            return iter(self._values)

        def __len__(self):
            return len(self._values)

    value = tpc._f32(0.1)
    rows = [[1, "s1_t0", "[user] body of s1_t0", "user", "draft",
             "2026-10-25T00:00:00+00:00", "event",
             _DriverVector([value]), False, ["s1"]]]
    _patch_proj(monkeypatch, _PagedG(rows))
    graph = tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert graph["nodes"]["s1_t0"]["embedding"] == [value]


@pytest.mark.parametrize("container", [{"a": 1.0}, 3.0])
def test_a_numeric_NON_sequence_is_still_REFUSED(monkeypatch, container):
    """Widening the acceptance to iterables must not widen it to anything.

    A `dict` is a container (not a sequence of numbers) and a float is not
    iterable; the sanitizer refuses both for the same reason, and so does the
    census — the refusal direction is preserved, never a silent `None`.
    """
    rows = [[1, "s1_t0", "[user] body of s1_t0", "user", "draft",
             "2026-10-25T00:00:00+00:00", "event", container, False,
             ["s1"]]]
    _patch_proj(monkeypatch, _PagedG(rows))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "not a sequence" in str(exc.value)


def test_a_container_whose_ITERATION_RAISES_REFUSES_it_does_not_crash(monkeypatch):
    """A pathological container is an unreadable side (exit 3), not a traceback.

    `list(embedding)` caught only `TypeError`, so a container whose `__iter__`
    raised anything else escaped as the exit-1 "usage/IO" code — a code the
    module docstring reserves for bad flags. `BaseException` still propagates,
    so a `KeyboardInterrupt` is not swallowed.
    """
    class _Exploding:
        def __iter__(self):
            raise RuntimeError("driver blew up mid-iteration")

    rows = [[1, "s1_t0", "[user] body of s1_t0", "user", "draft",
             "2026-10-25T00:00:00+00:00", "event", _Exploding(), False,
             ["s1"]]]
    _patch_proj(monkeypatch, _PagedG(rows))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "not a sequence" in str(exc.value)


def test_an_OVERLARGE_graph_component_REFUSES_it_does_not_crash(monkeypatch):
    """`float(v)` overflows for an int past binary64's range: refuse, do not crash.

    The record leg catches `OverflowError` (`_embedding_parity`); the graph leg
    caught only `TypeError`/`ValueError`, so a component it could not convert
    escaped as a traceback with exit 1 ("usage / IO error") instead of the
    `CensusError` → exit 3 that an unclassifiable side must produce.
    """
    rows = [[1, "s1_t0", "[user] body of s1_t0", "user", "draft",
             "2026-10-25T00:00:00+00:00", "event", [10 ** 400], False,
             ["s1"]]]
    _patch_proj(monkeypatch, _PagedG(rows))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "non-numeric" in str(exc.value)


# ── the graph leg's TOTALITY (RESULTSET_SIZE is silent — #5062's class) ────

class _PagedG:
    """A FalkorDB stand-in: pages of the census query + the store's own count."""

    def __init__(self, rows: list[list], *, truncate_to: int | None = None):
        self._rows = rows
        self._truncate_to = truncate_to
        self.page_calls = 0

    def query(self, cypher, params=None):
        class _R:
            def __init__(self, rs):
                self.result_set = rs

        if params is None:  # the one-row aggregate
            return _R([[len(self._rows)]])
        self.page_calls += 1
        served = (self._rows if self._truncate_to is None
                  else self._rows[: self._truncate_to])
        page = [r for r in served
                if int(r[0]) >= params["cursor"]][: params["batch"]]
        return _R(page)


def _turn_row(inner_id: int, tid: str, sid: str, et=None) -> list:
    # 11 columns: the 10 the census has always read, plus `t.embeddingText`
    # (#8488). Two spellings of the row shape is how the enriched column would
    # silently drop out of the read (review cycle 4, P2).
    return [inner_id, tid, f"[user] body of {tid}", "user", "draft",
            "2026-10-25T00:00:00+00:00", "event", None, False, [sid], et]


def _patch_proj(monkeypatch, g, graphs=("g", "tortoise")):
    from tortoise.projection import FalkorProjection

    class _DB:
        # The presence pre-check asks the STORE (`GRAPH.LIST`) before the leg
        # addresses the graph it was handed — a projection without a store
        # handle is not a projection, and a fake that omits it would silently
        # exercise the "store unreadable" refusal instead of the read.
        @staticmethod
        def list_graphs():
            return list(graphs)

    class _Proj:
        # The pre-check asks about the projection's OWN resolved name (a redirect
        # or a URI path can change which graph is addressed), so the fake has to
        # carry one like the real projection does — without it the leg refuses a
        # graph it cannot name (fail-closed) instead of exercising the read.
        graph_name = _graph_name = "g"

        def __init__(self, inner):
            self.g = inner
            self.db = _DB()

        @classmethod
        def from_uri(cls, *a, **k):
            return cls(g)

    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _Proj(g)))


def test_the_graph_leg_PAGES_and_collects_every_node(monkeypatch):
    """One unpaged RETURN sees at most RESULTSET_SIZE (10 000) — silently.

    The population this gate exists for is ~20 000 turn nodes, i.e. twice the
    cap, so a single query would compare half of it and report CLEAN over the
    rest. Pin that the leg takes MORE THAN ONE page (a page size small enough
    to force it) and that both pages reach the node map.
    """
    rows = [_turn_row(i, f"s1_t{i}", "s1") for i in range(1, 6)]
    g = _PagedG(rows)
    _patch_proj(monkeypatch, g)
    monkeypatch.setattr(tpc, "_GRAPH_PAGE_SIZE", 2)
    graph = tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert sorted(graph["nodes"]) == [f"s1_t{i}" for i in range(1, 6)]
    assert g.page_calls >= 3, "the leg did not page — a 20k population truncates"


def test_a_TRUNCATED_graph_read_refuses_instead_of_certifying(monkeypatch):
    """THE fail-open this guard exists for: a short read is silence, not an error.

    The fake serves 2 of 3 rows while its aggregate counts 3 — exactly what
    FalkorDB's RESULTSET_SIZE does to a real 20 000-node population. The leg
    must refuse (exit 3: an absence is NOT established), never hand `census()`
    a smaller population that then matches a smaller record and passes.
    """
    rows = [_turn_row(i, f"s1_t{i}", "s1") for i in range(1, 4)]
    g = _PagedG(rows, truncate_to=2)
    _patch_proj(monkeypatch, g)
    monkeypatch.setattr(tpc, "_GRAPH_PAGE_SIZE", 10)
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert "TRUNCATED" in str(exc.value)
    assert "counts 3" in str(exc.value)


# ── the population IS the recorded delete predicate (#10017) ────────────────
#
# #10017's delete set and completion test are `MATCH (t:Point) WHERE
# is_episodic = true` — NO kind filter. `is_episodic` is not co-extensive with
# "turn" (graph-scripts/backfill_is_episodic.py stamps it on every
# session-wired point, "capture turn + regex-extracted points"; create_point /
# update_point accept it on any kind), so a gate that read only
# `pointKind = 'event'` left a class that the purge WOULD delete in neither the
# comparison nor the anomaly partition. These cases pin the widened read.

def test_an_episodic_node_that_is_not_a_turn_refuses_and_is_named():
    """In the DELETE set, outside the turn shape: reported by id, and REFUSED."""
    nodes = {
        "s1_t0": _graph_node("s1_t0", "s1"),
        "ex1": _graph_node("ex1", "s1", pointKind="claim",
                           content="regex-extracted claim, not a turn"),
    }
    rows = {"s1_t0": _record_row("s1_t0")}
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert report["refused_for"] == ["outside_population"]
    # The purge SET is still the turn shape — nothing uncompared is counted.
    assert report["dry_run"]["would_delete_turn_nodes"] == 1
    assert report["dry_run"]["would_delete_ids"] == ["s1_t0"]
    assert ([a["id"] for a in report["anomalies"]["non_turn_point_kind"]]
            == ["ex1"])
    # ...and the read population is the delete set itself.
    assert report["population"]["episodic_graph_nodes"] == 2
    assert report["population"]["turn_shaped_nodes"] == 1


def test_the_graph_leg_reads_the_whole_delete_set_not_only_the_turn_shape(
        monkeypatch):
    """The QUERY must not re-narrow the population to `pointKind = 'event'`.

    A row for an episodic-but-not-turn node must come back from the store and
    reach `census()`; and the predicate itself is pinned, because a future
    edit that re-adds the kind filter restores the silent exclusion without
    reddening any classification test.
    """
    rows = [_turn_row(1, "s1_t0", "s1"),
            [2, "ex1", "claim text", "user", "draft",
             "2026-10-25T00:00:00+00:00", "claim", None, False, ["s1"]]]
    g = _PagedG(rows)
    _patch_proj(monkeypatch, g)
    graph = tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert sorted(graph["nodes"]) == ["ex1", "s1_t0"]
    assert graph["nodes"]["ex1"]["pointKind"] == "claim"
    assert "t.is_episodic = true" in tpc._GRAPH_EPISODIC_CYPHER
    assert "pointKind = " not in tpc._GRAPH_EPISODIC_CYPHER, (
        "the read is narrowed to the turn shape again — the delete set is "
        "`is_episodic = true` (#10017)")
    assert "pointKind" not in tpc._GRAPH_EPISODIC_COUNT_CYPHER


def test_a_node_with_no_usable_key_is_REPORTED_not_silently_dropped(
        monkeypatch):
    """The one shape that used to be invisible in BOTH `nodes` and `anomalies`.

    It was dropped with a bare `continue`, and the aggregate cross-check then
    reported it as `TRUNCATED (RESULTSET_SIZE) or torn` — a misleading
    diagnosis that sends the operator at the wrong problem. It must be its own
    reported class, and the count guard must not blame truncation for it.
    """
    rows = [_turn_row(1, "s1_t0", "s1"),
            [2, 7, "no string id", "user", "draft", None, "event", None,
             False, ["s1"]]]
    g = _PagedG(rows)  # its aggregate counts both rows
    _patch_proj(monkeypatch, g)
    graph = tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, None)
    assert graph["unidentifiable"] == [{"id": "2", "id_type": "int"}]
    report = tpc.census(graph, {"rows_by_key": {"s1_t0": _record_row("s1_t0")},
                                "non_turn_shaped": 0}, sample=5)
    assert report["verdict"] == "REFUSED"
    assert [a["id"] for a in report["anomalies"]["non_string_id"]] == ["2"]


def test_an_overlarge_record_component_REFUSES_it_does_not_crash():
    """A field-VALUE problem is exit 2, never the exit-1 "bad flags" crash.

    `struct.pack('<f', 1e300)` raises OverflowError, which used to escape both
    `_embedding_parity` and `census()` and surface as a traceback with exit 1 —
    a code the docstring defines as "usage / IO error".
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", embedding=[1.0])}
    rows = {"s1_t0": _record_row("s1_t0", embedding=[1e300])}
    report = _census(nodes, rows)  # must not raise
    assert report["verdict"] == "REFUSED"
    problem = _problems(report, "s1")[0]
    assert problem["field"] == "embedding"
    assert "binary32" in problem["detail"]


def test_the_population_counts_the_nodes_with_no_usable_key():
    """`episodic_graph_nodes` is labelled "(the delete set)" — so it must
    include the nodes whose `t.id` is not a string.

    Those nodes carry no dict key, so they live in `unidentifiable`; counting
    `len(nodes)` alone printed a population SMALLER than the delete set the
    line names (an all-unkeyable graph printed 0). They still refuse the census
    as `non_string_id`, so no verdict changes — the number just has to be about
    the population it says it is about.
    """
    graph = {"nodes": {"s1_t0": _graph_node("s1_t0", "s1")},
             "unidentifiable": [{"id": "2", "id_type": "int"},
                                {"id": "3", "id_type": "int"}]}
    report = tpc.census(
        graph,
        {"rows_by_key": {"s1_t0": _record_row("s1_t0")},
         "non_turn_shaped": 0}, sample=5)
    assert report["verdict"] == "REFUSED"
    assert report["population"]["episodic_graph_nodes"] == 3
    assert report["population"]["turn_shaped_nodes"] == 1


def test_a_refused_session_reports_the_turn_nodes_it_HELD_BACK():
    """`would_delete_turn_nodes` is CLEAN sessions only — the gate refuses what
    it could not verify — so a REFUSED run must also state what it is holding
    back, or "would delete 0" reads as "there is nothing to delete".
    """
    nodes = {"s1_t0": _graph_node("s1_t0", "s1"),
             "s1_t1": _graph_node("s1_t1", "s1")}
    rows = {"s1_t0": _record_row("s1_t0")}  # s1_t1 absent -> s1 REFUSED
    report = _census(nodes, rows)
    assert report["verdict"] == "REFUSED"
    assert report["dry_run"]["would_delete_turn_nodes"] == 0
    assert report["dry_run"]["held_back_turn_nodes"] == 2
    assert report["dry_run"]["blocked_sessions"] == ["s1"]


def test_the_CLI_states_the_held_back_turn_nodes(capsys, monkeypatch):
    """The operator reads stdout, not the JSON: the held-back line must print."""
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {"s1_t0": _graph_node("s1_t0", "s1"),
                  "s1_t1": _graph_node("s1_t1", "s1")},
        "unidentifiable": []})
    monkeypatch.setattr(tpc, "read_record_turns", lambda *a, **k: {
        "rows_by_key": {"s1_t0": _record_row("s1_t0")},
        "non_turn_shaped": 0})
    assert tpc.main(_argv()) == 2
    out = capsys.readouterr().out
    assert "would delete 0 turn node(s)" in out
    assert "held back  : 2 turn node(s)" in out
    assert "NOT eligible" in out


def test_a_node_in_two_anomaly_classes_is_counted_ONCE():
    """`outside_population` must be a NODE count, not a classification count."""
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", is_operator=True,
                                  sessions=set())}
    report = _census(nodes, {})
    anomalies = report["anomalies"]
    assert [a["id"] for a in anomalies["orphan"]] == ["s1_t0"]
    assert [a["id"] for a in anomalies["operator_flagged"]] == ["s1_t0"]
    assert report["population"]["outside_population"] == 1


# ── the embedded leg honours --graph-name (a wrong-target read) ─────────────

def test_the_embedded_leg_honours_graph_name(monkeypatch):
    """`--db-path x.db --graph-name team_acme` must read team_acme, not the default.

    `FalkorProjection(path, graph_name=...)` accepts the keyword and the
    embedded store selects the graph by it; the leg used to drop it, so the run
    censused the default `tortoise` graph and still printed a verdict for the
    graph nobody asked about.
    """
    seen: dict = {}

    class _G:
        def query(self, cypher, params=None):
            class _R:
                result_set = [] if params else [[0]]
            return _R()

    class _Proj:
        def __init__(self, path=None, **kwargs):
            seen["path"] = path
            seen.update(kwargs)
            self.g = _G()
            # the presence pre-check asks about the projection's OWN resolved
            # name, so the fake must carry one like the real projection does
            self.graph_name = self._graph_name = kwargs.get("graph_name")

        class _DB:
            @staticmethod
            def list_graphs():
                return ["team_acme", "tortoise"]

        db = _DB()

    import tortoise.projection as projection
    monkeypatch.setattr(projection, "FalkorProjection", _Proj)

    graph = tpc.read_graph_turns(None, "/tmp/x.db", "team_acme")
    assert graph["nodes"] == {}
    # the bind is asked to WRITE NOTHING (#11112): the health probe and the
    # schema DDL are both `GRAPH.QUERY` statements, and on a graph that does not
    # exist yet the first one creates it.
    assert seen == {"path": "/tmp/x.db", "graph_name": "team_acme",
                    "skip_health_check": True, "ensure_indexes": False}

    seen.clear()
    tpc.read_graph_turns(None, "/tmp/x.db", None)
    assert seen == {"path": "/tmp/x.db", "graph_name": "tortoise",
                    "skip_health_check": True, "ensure_indexes": False}


# ── the graph must ALREADY EXIST: the gate never creates its own target ──────
#
# #11112. Measured on `origin/main` before this pin: `--graph-name tortise`
# (typo) on a store holding only `tortoise` exited 3 with the correct verdict,
# and `GRAPH.LIST` afterwards held `['tortise', 'tortoise']` — the first
# `GRAPH.QUERY` on a selected graph is the creating operation on BOTH legs, so
# a wrong name wrote a stub graph into the store the run was certifying. The
# verdict was safe; the store was not, and the exit-3 message then described a
# graph the previous typo had created (wrong name vs drained graph stopped
# being distinguishable in practice, not just in the message).

def _embedded_store_with_tortoise(tmp_path, monkeypatch):
    """A real embedded store: graph `tortoise`, with one Point and one Meta node.

    Measured on the result: `MATCH (n) RETURN count(n)` → 2 — the `(:Point)` this
    helper creates, plus the `(:Meta)` the bind's schema DDL writes. So the store
    holds exactly ONE TURN node, which is the population the census reads; a
    "ONE node" shorthand would be wrong in the same way the EMPTY twin's
    docstring was (see its note below).
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "census_presence.db")
    proj = FalkorProjection(db_path, graph_name="tortoise",
                            skip_health_check=True)
    try:
        proj.g.query("CREATE (:Point {id:'s1_t1', is_episodic:true, "
                     "pointKind:'event'})")
        assert sorted(proj.db.list_graphs()) == ["tortoise"]
    finally:
        proj.close()
    return db_path


def _embedded_store_with_EMPTY_tortoise(tmp_path, monkeypatch):
    """A real embedded store holding exactly one graph, `tortoise`, and zero POINTS.

    #11113 review cycle 3, finding 2: the earlier wording said "ZERO nodes" and
    "no node is ever written", and both are false — the default bind issues the
    schema DDL, which on a fresh graph writes one `(:Meta)` node (measured:
    `MATCH (n) RETURN count(n)` → 1, `Point` → 0). The property this fixture is
    for is the CENSUS predicate, `is_episodic`, which really is 0 here.

    This is #10017's own post-purge state: the drain has happened, the graph
    still EXISTS. It is the state the presence check must NOT refuse.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "census_empty.db")
    # default bind kwargs: the graph is created and its DDL writes one (:Meta),
    # but no Point — which is the population the census counts.
    proj = FalkorProjection(db_path, graph_name="tortoise")
    try:
        assert sorted(proj.db.list_graphs()) == ["tortoise"]
        n = proj.g.query("MATCH (p:Point) RETURN count(p)").result_set[0][0]
        assert n == 0, f"fixture is not empty: {n} node(s)"
    finally:
        proj.close()
    return db_path


def test_a_wrong_graph_name_is_REFUSED_before_it_creates_the_graph(
        tmp_path, monkeypatch):
    """The gate must not mint the target it certifies (#11112)."""
    db_path = _embedded_store_with_tortoise(tmp_path, monkeypatch)

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortise")
    assert "does not exist" in str(exc.value)
    assert "tortoise" in str(exc.value)      # names what IS there

    from tortoise.projection import FalkorProjection
    proj = FalkorProjection(db_path, graph_name="tortoise",
                            skip_health_check=True)
    try:
        # the ROW is the measurement: the typo did not become a graph.
        assert sorted(proj.db.list_graphs()) == ["tortoise"]
    finally:
        proj.close()


def test_a_NAMELESS_target_is_REFUSED_not_verified_as_some_other_graph(
        tmp_path, monkeypatch):
    """⛔ The guard must ask about the name the run will OPEN (#11113 review P2).

    Before the fix `wanted` was an `or`-chain, so a FALSY resolved name fell
    through to `requested` / `"tortoise"`: the guard verified a graph this run
    would not open, PASSED, and the read then created the nameless one. Measured
    on this exact fixture: `FalkorProjection(path, graph_name="")` keeps
    `_graph_name == ""` (and `resolve_db_endpoint(uri, "")` returns `""`), the
    guard passed for `"tortoise"`, and `GRAPH.LIST` went `['tortoise']` →
    `['tortoise', '']` — the #11112 defect one layer down.

    Unreachable from either CLI (both pre-refuse an empty `--graph-name`), so
    this pins the LIBRARY arm, where an environment lookup like
    `os.environ.get("TORTOISE_GRAPH", "")` produces exactly this name.
    """
    db_path = _embedded_store_with_tortoise(tmp_path, monkeypatch)
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection(db_path, graph_name="", skip_health_check=True,
                            ensure_indexes=False)
    try:
        with pytest.raises(tpc.CensusError) as exc:
            tpc._refuse_unless_graph_present(proj, "")
        assert "no NAME" in str(exc.value)

        # And it is the HANDLE, not the argument, that decides: a caller that
        # passes a perfectly good name must still be refused, because the
        # projection addresses `""` regardless of what the caller believes.
        with pytest.raises(tpc.CensusError):
            tpc._refuse_unless_graph_present(proj, "tortoise")

        # The ROW is the measurement: the nameless graph was never created.
        assert sorted(proj.db.list_graphs()) == ["tortoise"]
    finally:
        proj.close()


def test_an_ABSENT_name_refuses_rather_than_assuming_the_default(
        monkeypatch):
    """No name anywhere is not `tortoise` — the same rule, one spelling over.

    A fake without a name and a caller without an argument used to fall through
    to `"tortoise"` and verify THAT, which is a presence claim about a graph the
    object never proved it addresses.
    """

    class _DB:
        @staticmethod
        def list_graphs():
            return ["tortoise"]

    class _Proj:
        db = _DB()

    with pytest.raises(tpc.CensusError) as exc:
        tpc._refuse_unless_graph_present(_Proj(), None)
    assert "no NAME" in str(exc.value)


def test_a_present_graph_is_never_refused_by_the_presence_check(
        tmp_path, monkeypatch):
    """A present graph holding a turn node reads — the check does not gate on content.

    Scope, stated so this pin is not read as more than it is: THIS test proves
    presence does not refuse a graph that has something in it. The
    presence-vs-emptiness case (#11112/#11113 review cycle 1, finding 1) is
    pinned separately, on a DRAINED graph, by
    `test_a_present_but_EMPTY_graph_passes_presence_and_reaches_the_emptiness_verdict`
    — this file's other half of the same property.
    """
    db_path = _embedded_store_with_tortoise(tmp_path, monkeypatch)
    graph = tpc.read_graph_turns(None, db_path, "tortoise")
    assert "s1_t1" in graph["nodes"]


def test_a_present_but_EMPTY_graph_passes_presence_and_reaches_the_emptiness_verdict(
        tmp_path, monkeypatch, capsys):
    """⛔ PRESENCE IS NOT EMPTINESS, and #10017's post-purge re-run depends on it.

    #11113 review cycle 1, finding 1: the pin that NAMED this property seeded a
    node, so it never exercised the drained graph — a future guard that refused
    an existing-but-empty graph passed it green, and the run #10017 ends on
    (delete happened, graph now empty, `--expect-empty`) would start exiting 3.
    This test is that graph: it exists, it holds zero POINTS (the bind's DDL
    writes one `(:Meta)`, not a Point — measured, #11113 review cycle 3).

    Two assertions, because the failure has two halves:
      (a) the READ must not raise — presence, not content, is the question;
      (b) the CLI with `--expect-empty` must reach the EMPTINESS verdict and must
          NOT print the presence refusal ("does not exist").
    """
    db_path = _embedded_store_with_EMPTY_tortoise(tmp_path, monkeypatch)

    # (a) read side: no CensusError, and it really is empty.
    graph = tpc.read_graph_turns(None, db_path, "tortoise")
    assert graph["nodes"] == {}
    assert graph.get("unidentifiable") == []

    # (b) CLI side: with --expect-empty the drained graph certifies (exit 0),
    # and the message it would print if presence had refused is absent.
    rc = tpc.main([
        "--db-path", db_path, "--graph-name", "tortoise",
        "--local-record-dir", str(tmp_path / "no-record"),
        "--expect-empty",
    ])
    err = capsys.readouterr().err
    assert "does not exist in this store" not in err, (
        "the presence check refused a graph that EXISTS and is drained — this "
        "is exactly #10017's post-purge re-run: " + err)
    assert rc == 0, err


def test_write_freedom_needs_the_flag_PAIR_not_ensure_indexes_alone(
        tmp_path, monkeypatch):
    """⛔ `ensure_indexes=False` ALONE still WRITES — the pair is the guarantee.

    #11113 review cycle 2, finding 1: the comment on the new keyword said
    `ensure_indexes=False` is "for a caller that must address the store WITHOUT
    writing to it". It is not — it skips only the schema DDL, while the
    open-time health probe (`_auto_health_recover`) is a `GRAPH.QUERY` too, and
    on a graph the store does not hold the FIRST ONE CREATES IT. Measured
    before this pin existed: `ensure_indexes=False` with `skip_health_check` at
    its default ADDED the missing graph to `GRAPH.LIST`.

    This is the trap the comment now names, so it is pinned rather than
    described: half the rule looks like it works (it does skip the DDL) and the
    caller who needs it most — a gate that certifies a store it must not
    mutate — would still mint its target.

    RED under the mutation this defends: make `_auto_health_recover` a no-op
    (the pin's first half then observes NO new graph, so its assertion fails —
    that half exists precisely because the health probe, not the DDL, is what
    still writes here).
    """
    db_path = _embedded_store_with_tortoise(tmp_path, monkeypatch)
    from tortoise.projection import FalkorProjection

    # (a) the HALF rule still writes: `ensure_indexes=False` ALONE, health probe
    # at its default. The DDL is skipped and the graph is created anyway — by
    # `_auto_health_recover`'s `_probe_ok()` (`MATCH (n) RETURN count(n) LIMIT 1`),
    # the first `GRAPH.QUERY` on the new graph. Measured on a spy: exactly ONE
    # graph-level query, and `GRAPH.LIST` gains the name.
    half = FalkorProjection(db_path, graph_name="half_rule",
                            ensure_indexes=False)
    try:
        assert sorted(half.db.list_graphs()) == ["half_rule", "tortoise"], (
            "`ensure_indexes=False` alone did NOT create the graph — if that is "
            "true by design, the comment on the flag must be re-derived, "
            "because the census's write-freedom would then come from one flag "
            "and not the pair")
    finally:
        half.close()

    # (b) the PAIR does not: same store, a name it does not hold, nothing minted.
    pair = FalkorProjection(db_path, graph_name="pair_rule",
                            skip_health_check=True, ensure_indexes=False)
    try:
        assert sorted(pair.db.list_graphs()) == ["half_rule", "tortoise"]
    finally:
        pair.close()


def _embedded_store_with_TWO_graphs(tmp_path, monkeypatch):
    """A store holding `live` (one turn node) AND `tortoise` (present, empty).

    The residual case: a wrong `--graph-name` whose value the store HAPPENS to
    hold. Presence cannot tell it from the graph the operator meant, because
    both exist.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "census_two.db")
    live = FalkorProjection(db_path, graph_name="live", skip_health_check=True)
    try:
        live.g.query("CREATE (:Point {id:'live_t1', is_episodic:true, "
                     "pointKind:'event'})")
    finally:
        live.close()
    drained = FalkorProjection(db_path, graph_name="tortoise",
                               skip_health_check=True)
    try:
        assert sorted(drained.db.list_graphs()) == ["live", "tortoise"]
    finally:
        drained.close()
    return db_path


def test_expect_empty_STILL_certifies_a_wrong_but_EXISTING_graph_name(
        tmp_path, monkeypatch, capsys):
    """⛔ PIN ON A LIMIT, not on a guarantee (#11113 review cycle 2, P1).

    The presence check proves a NAME EXISTS. It does not prove it is the graph
    the operator meant: another tenant's graph of the same name, a typo an
    earlier run created, or a wrong --db-path pointing at another store all
    pass it. Measured here: `--graph-name tortoise` on a store whose real
    target is `live` certifies CLEAN under `--expect-empty`.

    This test exists so the help text can never again claim that `--expect-empty`
    "can never be used to pass a target that does not exist" — an assurance that
    is FALSE for a target that exists under the wrong name, which is the class
    this gate is most likely to be pointed at by accident. If the gate ever
    grows the intent check that would close this, this pin must be updated
    deliberately rather than deleted quietly.
    """
    db_path = _embedded_store_with_TWO_graphs(tmp_path, monkeypatch)

    rc = tpc.main([
        "--db-path", db_path, "--graph-name", "tortoise",
        "--local-record-dir", str(tmp_path / "no-record"),
        "--expect-empty",
    ])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "graph      : tortoise" in out.out, out.out
    assert "verdict    : CLEAN" in out.out, out.out


def test_the_expect_empty_help_NAMES_the_intent_residual():
    """The false assurance that this pin replaces, asserted absent.

    Truthiness is one thing; a docstring that promises safety the code does not
    provide is how an operator arms `--expect-empty` on a suspicious run
    (#11113 review cycle 2, P1 — it was green because nothing pinned the text).
    """
    import ast
    import inspect

    src = inspect.getsource(tpc)
    tree = ast.parse(src)
    helps = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == \
                "add_argument":
            flags = [a.value for a in node.args
                     if isinstance(a, ast.Constant)
                     and isinstance(a.value, str)]
            for kw in node.keywords:
                if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                    for f in flags:
                        helps[f] = kw.value.value

    expect_help = helps["--expect-empty"]
    graph_help = helps["--graph-name"]
    # the false assurance: names the absent case only, and calls it safety.
    assert "cannot be blessed" not in expect_help
    assert "refuses those regardless" not in expect_help
    assert "cannot be blessed" not in graph_help
    # the true statement, in both operator-visible sites.
    assert "not the intended" in expect_help.replace("\n", " "), expect_help
    # the USAGE block is the other operator-visible site (the argparse help for
    # --graph-name is one line; the full text there is the usage preamble).
    usage = src.split('"""', 2)[1]
    flat_usage = " ".join(usage.split())
    assert "not prove it is the graph you meant" in flat_usage, flat_usage
    # and the USAGE block must not repeat the old claim either.
    assert "cannot be blessed" not in usage
    assert "NOT the same answer as a drained graph" not in usage


def test_an_EMPTY_graph_name_is_refused_by_the_LIBRARY_not_substituted(
        tmp_path, monkeypatch):
    """#11113 review cycle 3, P2: `graph_name or "tortoise"` pre-empted the guard.

    The CLI refuses `--graph-name ""` before this point (`empty_graph_target_flag`),
    so the defect was reachable only from the library — the arm `read_graph_turns`
    is. Measured on the pre-fix code: `read_graph_turns(None, db, "")` on a store
    holding `['live','tortoise']` returned `tortoise`'s turns, i.e. it CERTIFIED a
    graph the caller never asked about, and `_refuse_unless_graph_present` (which
    refuses a nameless handle outright) never saw the name. `None` still means
    "the default"; `""` is a name that cannot be verified.
    """
    db_path = _embedded_store_with_TWO_graphs(tmp_path, monkeypatch)

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "")
    assert "no NAME" in str(exc.value), str(exc.value)
    # ... and the default is still the default, so the refusal is about "" and
    # not about an absent argument: `None` resolves to `tortoise`, which in this
    # fixture is the drained graph, so it reads (nothing) instead of raising.
    assert tpc.read_graph_turns(None, db_path, None).get("nodes") == {}
    # the non-default line is reachable, so the fixture really holds `live_t1`.
    graph = tpc.read_graph_turns(None, db_path, "live")
    assert sorted(graph["nodes"]) == ["live_t1"], graph["nodes"]


def _write_replayable_log(tmp_path):
    """A non-empty adjacent event log beside the store (the LOST-store signal)."""
    path = tmp_path / "session-events.jsonl"
    path.write_text('{"event_type": "SessionStarted", "session_id": "s1"}\n')
    return str(tmp_path)


def _embedded_store_with_NO_nodes(tmp_path, monkeypatch):
    """A store whose graph EXISTS but holds zero nodes of ANY label.

    This is what a LOST graph looks like (redislite starts fresh on a corrupt
    RDB, an interrupted restore, a manual `MATCH (n) DETACH DELETE n`) — NOT
    what a purged graph looks like, because a purge leaves the non-turn nodes
    the bind wrote.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "census_lost.db")
    proj = FalkorProjection(db_path, graph_name="tortoise")
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        assert _tpc_total_nodes(proj.g) == 0, _tpc_total_nodes(proj.g)
    finally:
        proj.close()
    return db_path


def _tpc_total_nodes(g):
    return int(g.query("MATCH (n) RETURN count(n)").result_set[0][0])


def test_a_LOST_graph_with_a_replayable_log_is_REFUSED_not_certified(
        tmp_path, monkeypatch):
    """⛔ #11113 review cycle 4, P1: `--expect-empty` must not bless a lost store.

    The write-free bind skips the health probe, and the probe was also a
    DETECTOR: `recover_from_log` rebuilds only when the graph has 0 TOTAL nodes
    and the adjacent log has events, so pre-PR this state was replayed and the
    census refused. With the probe skipped it read as "drained" and certified
    CLEAN — a false PASS about a store that lost its graph.

    Measured red-first: on the pre-fix module this call RETURNS (nodes {}).
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    _write_replayable_log(tmp_path)

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortoise")
    message = str(exc.value)
    assert "LOST-STORE shape" in message, message
    assert "ACTS on that log" in message, message


def test_an_UNANSWERABLE_total_node_count_REFUSES_not_certifies(
        tmp_path, monkeypatch):
    """⛔ #11113 review cycle 11, P1: "I could not ask" is not "not lost".

    This arm's first condition is the graph's TOTAL node count, and
    `recover_from_log` REFUSES when that count cannot be answered
    ("graph unresponsive — recovery requires a live DB"). Returning instead
    delegated the diagnosis to the read arm — but that arm runs a DIFFERENT
    query over a DIFFERENT population (`MATCH (t:Point) WHERE t.is_episodic =
    true`), so it can succeed while the all-label aggregate fails, and the
    store then certified CLEAN under `--expect-empty`.

    The patch targets the ONE call site in the LOST arm; the aggregate that a
    healthy store is read through (`_GRAPH_EPISODIC_COUNT_CYPHER`) is a
    different query, so this cannot mask the population the census reads.

    Measured red-first: on the pre-fix module this call RETURNS `nodes={}`
    (and the CLI exits 0 with `verdict: CLEAN`).
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    _write_replayable_log(tmp_path)

    def _unanswerable(_g):
        raise RuntimeError("count query failed mid-flight")

    monkeypatch.setattr(tpc, "_graph_total_node_count", _unanswerable)

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortoise")
    message = str(exc.value)
    assert "total-node count could not be read" in message, message
    assert "RuntimeError" in message, message      # the cause is named
    assert "UNKNOWN, not answered" in message, message
    # the state that must never be blessed: this is NOT a "nothing there" pass
    assert "LOST-STORE shape" not in message, message


def test_a_DRAINED_graph_is_never_mistaken_for_a_LOST_one(
        tmp_path, monkeypatch):
    """The other half, and the one #10017's terminal run depends on.

    A PURGED graph keeps its non-turn nodes (the bind's `(:Meta)`), so its TOTAL
    count is non-zero even with zero episodic nodes — exactly the line
    `recover_from_log` draws. The lost-store refusal must therefore NOT fire
    here, or the post-purge re-run the issue ends on would be blocked by the
    gate that is supposed to bless it.
    """
    db_path = _embedded_store_with_EMPTY_tortoise(tmp_path, monkeypatch)
    _write_replayable_log(tmp_path)          # same log, same directory

    graph = tpc.read_graph_turns(None, db_path, "tortoise")
    assert graph["nodes"] == {}, graph["nodes"]


def test_a_FIFO_named_like_a_log_does_NOT_hang_the_refusal_arm(
        tmp_path, monkeypatch):
    """⛔ A planted FIFO must not wedge the census with no verdict (#11113 c5, P2).

    `_one_replayable_log` reads a file beside the store, which the operator may
    share; a plain `O_RDONLY` open on a FIFO blocks FOREVER, and this runs on
    exactly the empty-graph state #10017 ends on — so the gate would hang
    silently instead of refusing. What protects it is `O_NONBLOCK` + `S_ISREG`
    on the fd; `O_NOFOLLOW` is deliberately NOT used (recovery FOLLOWS a
    symlinked log, so refusing to look through it failed open — see the sibling
    `test_a_SYMLINKED_log_is_still_replayable_because_recovery_follows_it`).
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    fifo = tmp_path / "session-events.jsonl"
    os.mkfifo(fifo)

    # A FIFO is not a replayable log: the arm must read PAST it (no refusal, no
    # hang). The pin is that this call RETURNS at all — on the pre-fix code the
    # plain open() blocked here and the test would time out, not fail.
    graph = tpc.read_graph_turns(None, db_path, "tortoise")
    assert graph["nodes"] == {}, graph["nodes"]


def test_a_GARBAGE_log_is_not_called_replayable(tmp_path, monkeypatch):
    """The arm must match `recover_from_log`'s boundary, not approximate it.

    #11113 review cycle 5, P2: recovery replays only from a log with at least
    one PARSABLE event, so a garbage-only log is `nothing to recover`. Claiming
    "recovery would REPLAY this" there tells the operator a false cause — the
    same class of misdiagnosis this PR fixes elsewhere. Measured on the sibling:
    `recover_from_log` → `{'recovered': False, 'reason': 'event log empty or
    unreadable — nothing to recover'}`.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    (tmp_path / "session-events.jsonl").write_text("not json at all\n{\n")

    graph = tpc.read_graph_turns(None, db_path, "tortoise")
    assert graph["nodes"] == {}, graph["nodes"]


def test_TWO_adjacent_logs_are_ambiguous_so_the_arm_does_not_claim_a_replay(
        tmp_path, monkeypatch):
    """Recovery refuses an ambiguous pair; the arm must not assert otherwise."""
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    (tmp_path / "a.jsonl").write_text('{"event_type": "SessionStarted"}\n')
    (tmp_path / "b.jsonl").write_text('{"event_type": "SessionStarted"}\n')

    graph = tpc.read_graph_turns(None, db_path, "tortoise")
    assert graph["nodes"] == {}, graph["nodes"]


def test_an_UNREADABLE_log_REFUSES_rather_than_certifying_the_store(
        tmp_path, monkeypatch):
    """⛔ #11113 review cycle 9, P2: the loader's THIRD answer, one record over.

    The sidecar branch was fixed to distinguish "nothing here" (`None`) from
    "here but untrustworthy" (a refusal); the log branch collapsed the same two
    answers. `os.listdir` has just proved there IS exactly one `.jsonl`, so a
    log this process cannot READ is not an ABSENT one — reporting it as `None`
    let a ZERO-node store certify `CLEAN` under `--expect-empty`, the exact
    false PASS the arm exists to prevent, while the identical store with the
    same bytes at a readable mode REFUSED.

    Measured red-first: on the parent of the fixing commit this call RETURNS
    (`nodes={}`) instead of raising.
    """
    if os.geteuid() == 0:                       # root reads through the mode
        pytest.skip("chmod 000 does not deny a root reader")
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    log = tmp_path / "session-events.jsonl"
    log.write_text('{"event_type": "SessionStarted", "session_id": "s1"}\n')
    os.chmod(log, 0o000)
    try:
        with pytest.raises(tpc.CensusError) as exc:
            tpc.read_graph_turns(None, db_path, "tortoise")
    finally:
        os.chmod(log, 0o600)                    # let tmp_path teardown clean up
    message = str(exc.value)
    assert "could not be OPENED" in message, message
    assert "UNKNOWN, not answered" in message, message
    # It must NOT claim the LOST-STORE shape: that verdict needs a record the
    # arm could actually read, and this one was never established.
    assert "LOST-STORE shape" not in message, message


def test_a_log_read_that_DIES_mid_file_REFUSES_too(tmp_path, monkeypatch):
    """The same third answer one frame down: the read, not just the open.

    A short read (`EIO`, a truncated FUSE mount) is "could not ask" exactly as
    an `EACCES` open is; swallowing it into `None` certifies a 0-node store on
    evidence the arm never obtained. Pinned at the seam the code actually
    crosses (`os.fdopen`), because the failure is not reproducible from user
    space on a healthy filesystem.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    _write_replayable_log(tmp_path)

    real_fdopen = os.fdopen

    def _failing_fdopen(fd, *a, **kw):
        handle = real_fdopen(fd, *a, **kw)

        def _boom(*_a, **_kw):
            raise OSError(5, "Input/output error")

        handle.read = _boom                # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(os, "fdopen", _failing_fdopen)
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortoise")
    message = str(exc.value)
    assert "could not be READ to completion" in message, message
    assert "UNKNOWN, not answered" in message, message
    assert "LOST-STORE shape" not in message, message


def test_an_UNLISTABLE_log_directory_REFUSES_rather_than_reading_as_empty(
        tmp_path):
    """⛔ #11113 review cycle 9, P2: `[]` means "no logs", so it must not be
    reachable from a directory the gate could not LIST.

    `_jsonl_names` answers `[]` on any error, which is the right answer for the
    DIAGNOSIS callers. But `_one_replayable_log` refuses on `len(names) != 1`,
    so an unlistable directory collapsing to `[]` made "I could not ask"
    indistinguishable from "nothing is adjacent". The strict mode is the pin:
    drop it and this test reds.

    Pinned at the helper, not through `read_graph_turns`: the store's own
    redislite sidecars live in this directory, so denying the listing by mode
    also denies the BIND and the arm is never reached (measured — a `0o111`
    directory fails at `census_lost.db.settings` first).
    """
    if os.geteuid() == 0:                       # root lists through the mode
        pytest.skip("chmod 0111 does not deny a root lister")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "session-events.jsonl").write_text(
        '{"event_type": "SessionStarted", "session_id": "s1"}\n')
    os.chmod(shared, 0o111)                     # traverse, cannot list
    try:
        with pytest.raises(tpc.CensusError) as exc:
            tpc._jsonl_names(str(shared), strict=True)
        assert "could not be LISTED" in str(exc.value), str(exc.value)
        # The lenient default is UNCHANGED: the diagnosis callers still word a
        # message rather than raising out of an unrelated code path.
        assert tpc._jsonl_names(str(shared)) == []
        # A directory that is GONE is still a genuine negative in BOTH modes.
        assert tpc._jsonl_names(str(tmp_path / "gone"), strict=True) == []
    finally:
        os.chmod(shared, 0o700)                 # let teardown clean up
    # ...and the ARM must actually ask strictly, or the mode above is dead code.
    src = (Path(__file__).resolve().parent.parent
           / "tools" / "turn_parity_census.py").read_text(encoding="utf-8")
    assert "_jsonl_names(events_dir, strict=True)" in src


def test_a_DANGLING_symlink_log_is_an_ABSENCE_not_a_refusal(tmp_path, monkeypatch):
    """⛔ #11113 review cycle 9: "unreadable" and "gone" are different answers.

    Cycle 9 made every `OSError` from the open refuse, to stop an `EACCES` log
    certifying a 0-node store CLEAN. But `ENOENT` is the opposite case: the name
    `listdir` reported has nothing behind it (a dangling symlink, or a log the
    writer rotated or deleted in the window between the listing and the open) —
    so there IS no record, and the correct answer is the arm's NEGATIVE. The
    sibling `_jsonl_names` already answers `[]` for that same errno one frame
    up; refusing here was an over-block whose message pointed the operator at a
    file that does not exist.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    link = tmp_path / "session-events.jsonl"
    try:
        link.symlink_to(tmp_path / "gone-away.jsonl")     # target never exists
    except (OSError, NotImplementedError) as exc:          # pragma: no cover
        pytest.skip(f"symlinks unavailable here: {exc}")

    graph = tpc.read_graph_turns(None, db_path, "tortoise")
    assert graph["nodes"] == {}, graph["nodes"]


def test_a_SYMLINKED_log_is_still_replayable_because_recovery_follows_it(
        tmp_path, monkeypatch):
    """⛔ `O_NOFOLLOW` made this arm FAIL OPEN — measured, #11113 review cycle 6.

    Recovery follows a symlinked `.jsonl` and replays it, so an arm that refused
    to look through the link certified `CLEAN` (rc 0) on a 0-node store whose
    log was a symlink, while the IDENTICAL file under a regular name refused
    (rc 3). The FIFO protection is `O_NONBLOCK` + `S_ISREG`, not `O_NOFOLLOW`,
    so the link is followed and the fd is still checked.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    # the real log lives OUTSIDE the store's directory, so the directory holds
    # exactly ONE `.jsonl` — the symlink. (Two adjacent logs are "ambiguous" and
    # recovery refuses, which is a different case with its own pin.)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = elsewhere / "real.jsonl"
    real.write_text('{"event_type": "PointAdded", "id": "p1"}\n')
    os.symlink(os.path.join("elsewhere", real.name),
               tmp_path / "session-events.jsonl")

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortoise")
    assert "LOST-STORE shape" in str(exc.value), str(exc.value)


def test_an_OVERSIZED_log_is_treated_as_replayable_without_reading_it(
        tmp_path, monkeypatch):
    """A huge log must REFUSE — and must do it WITHOUT reading the file.

    #11113 review cycle 7, P2: the first version of this pin wrote a PARSABLE
    40-byte file, so the non-capped path also returned `path` — the pin passed
    on a mutant with the cap branch deleted, i.e. it did not measure its own
    name. The file here is UNPARSABLE, so only the cap can produce a refusal;
    and `os.fdopen` is spied to prove nothing was read.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    (tmp_path / "session-events.jsonl").write_bytes(b"x" * 4096)  # unparsable
    monkeypatch.setattr(tpc, "_LOST_LOG_MAX_BYTES", 4)   # smaller than the file

    opened = []
    real_fdopen = __import__("os").fdopen

    def spy(*a, **k):
        opened.append(a)
        return real_fdopen(*a, **k)

    monkeypatch.setattr(os, "fdopen", spy)
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortoise")
    assert "LOST-STORE shape" in str(exc.value), str(exc.value)
    assert opened == [], f"the cap did not prevent a read: {opened}"


def test_a_CR_separated_log_is_replayable_like_the_reader_treats_it(
        tmp_path, monkeypatch):
    """⛔ CR-only records: recovery replays them, so the arm must refuse.

    #11113 review cycle 7, P2, measured: the arm split on `\n` only while
    `EventLog.read_all` normalises a bare `\r`, so a 0-node store whose record
    used CR separators certified `CLEAN` (rc 0) while `recover_from_log`
    refilled it (`log_points: 2`). Same fail-open class as the symlink fix.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    (tmp_path / "session-events.jsonl").write_bytes(
        b'{"event_type": "PointAdded", "id": "p1"}\r'
        b'{"event_type": "PointAdded", "id": "p2"}\r')

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortoise")
    assert "LOST-STORE shape" in str(exc.value), str(exc.value)


def test_a_BOM_log_does_not_refuse_where_the_reader_finds_nothing(
        tmp_path, monkeypatch):
    """The reader parses TEXT; `json.loads(bytes)` strips a BOM and it does not.

    #11113 review cycle 7, P3: refusing there named a replay recovery would not
    perform. Measured: `read_all` → 0 events → 'nothing to recover'.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    (tmp_path / "session-events.jsonl").write_bytes(
        b'\xef\xbb\xbf{"event_type": "PointAdded", "id": "p1"}\n')

    graph = tpc.read_graph_turns(None, db_path, "tortoise")
    assert graph["nodes"] == {}, graph["nodes"]


def test_a_pending_prewipe_SNAPSHOT_alone_is_the_LOST_shape_too(
        tmp_path, monkeypatch):
    """`recover_from_log` checks the sidecar before its 'no logs' return (#2943).

    #11113 review cycle 7, P3, measured: with zero `.jsonl` files, a pending
    snapshot is replayed by recovery (`recovered: True`), so a 0-node store whose
    only record is that sidecar must not be certified `CLEAN` either.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    import json as _json

    from tortoise.projection import prewipe_snapshot_path

    # the loader's own section vocabulary (`_SNAPSHOT_SECTIONS`): an unknown
    # key would make this an INVALID sidecar, i.e. a different refusal path.
    # the loader validates shape AND value types, and the `type` must be one the
    # replay dispatches on (`PointAdded`/`OperatorAdded`) — an unknown shape is a
    # DIFFERENT refusal path, not the pending-sidecar this pin is about.
    with open(prewipe_snapshot_path(str(tmp_path)), "w") as fh:
        _json.dump(
            {"synthetic_events": [{
                "type": "PointAdded",
                "point": {"id": "p-synthetic", "content": "x",
                          "pointKind": "point", "is_episodic": False},
            }]},
            fh)

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortoise")
    assert "LOST-STORE shape" in str(exc.value), str(exc.value)


def test_an_UNTRUSTWORTHY_prewipe_snapshot_REFUSES_instead_of_reading_as_absent(
        tmp_path, monkeypatch):
    """⛔ The loader's third answer is a REFUSAL, not an absence (#11113 cycle 8).

    `_load_prewipe_snapshot` raises for a sidecar that exists but cannot be
    trusted — unparsable, a symlink, a FIFO, absurdly large — because it may be
    the only surviving record of a rebuild's graph-only Points, and
    `recover_from_log` turns that into its own refusal. Swallowing it into None
    made `--expect-empty` certify `CLEAN` against a state recovery refuses on.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    from tortoise.projection import prewipe_snapshot_path

    path = prewipe_snapshot_path(str(tmp_path))
    with open(path, "w") as fh:
        fh.write("this is not the snapshot you are looking for\n")

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortoise")
    message = str(exc.value)
    assert "cannot be trusted" in message, message
    assert "LOST-STORE" not in message, message   # a different refusal, on purpose


def test_a_pending_sidecar_with_TWO_logs_says_RECOVERY_REFUSES_not_replays(
        tmp_path, monkeypatch):
    """The diagnosis must match what recovery does on THIS state (#11113 c8).

    Measured on the sibling: with a pending sidecar AND two adjacent logs,
    `recover_from_log` returns `recovered: False` — "ambiguous log set".
    Refusing to certify is right; claiming "recovery would REPLAY" is not.
    """
    db_path = _embedded_store_with_NO_nodes(tmp_path, monkeypatch)
    import json as _json

    from tortoise.projection import prewipe_snapshot_path

    with open(prewipe_snapshot_path(str(tmp_path)), "w") as fh:
        _json.dump({"synthetic_events": [{
            "type": "PointAdded",
            "point": {"id": "p-synthetic", "content": "x",
                      "pointKind": "point", "is_episodic": False},
        }]}, fh)
    for name in ("a.jsonl", "b.jsonl"):
        (tmp_path / name).write_text('{"event_type": "SessionStarted"}\n')

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, db_path, "tortoise")
    message = str(exc.value)
    # 2 logs + pending snapshot: recovery declines ("ambiguous log set"). The
    # assertion is the PHRASE, not the bare word — "unambiguous" appears in the
    # trailing remedy sentence and silently satisfied a bare `in` check
    # (#11113 review cycle 10, P3).
    assert "ambiguous log set" in message, message
    assert "REBUILDS" not in message, message
    assert "would REPLAY" not in message, message


def test_a_HEALTHY_store_is_never_refused_over_a_sidecar_recovery_ignores(
        tmp_path, monkeypatch):
    """⛔ Order is part of the mirror: count FIRST (#11113 review cycle 9, P2).

    `recover_from_log` returns early on `db_count > 0` — "graph already has
    nodes, no rebuild" — so on a healthy store it never reads the records at
    all. Probing (and raising about) an untrustworthy sidecar before the count
    refused the #10017 PRE-purge census on a perfectly good store, and the
    message invited the operator to delete a rescue file. Measured
    `recover_from_log` on this exact state: `{'recovered': False, 'db_points':
    2, 'reason': 'graph already has nodes — no rebuild'}`.
    """
    db_path = _embedded_store_with_tortoise(tmp_path, monkeypatch)
    from tortoise.projection import prewipe_snapshot_path

    with open(prewipe_snapshot_path(str(tmp_path)), "w") as fh:
        fh.write("garbage, not a snapshot\n")

    graph = tpc.read_graph_turns(None, db_path, "tortoise")
    assert "s1_t1" in graph["nodes"], graph["nodes"]


def test_an_unlistable_store_REFUSES_rather_than_reading_through(
        monkeypatch):
    """Fail closed: "I could not ask" is not "the graph is there"."""
    class _DB:
        @staticmethod
        def list_graphs():
            raise RuntimeError("store went away")

    class _Proj:
        db = _DB()
        g = object()

    import tortoise.projection as projection
    monkeypatch.setattr(projection, "FalkorProjection", lambda *a, **k: _Proj())
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, "/tmp/x.db", "tortoise")
    assert "could not be listed" in str(exc.value)
    # NON-VACUITY for the credential diagnosis below: an ordinary store failure
    # is NOT reported as a credential problem. Without this, "the refusal names
    # the credential" would hold for every unlistable store and would say
    # nothing about the NOPERM arm.
    assert "credential" not in str(exc.value)
    assert "GRAPH.LIST" not in str(exc.value)


def _refusal_from_listing(monkeypatch, error):
    """The CensusError `_refuse_unless_graph_present` raises for *error*."""
    class _DB:
        @staticmethod
        def list_graphs():
            raise error

    class _Proj:
        db = _DB()
        g = object()   # ⛔ never touched: a refusal must not open the graph
        _graph_name = "tortoise"

    import tortoise.projection as projection
    monkeypatch.setattr(projection, "FalkorProjection", lambda *a, **k: _Proj())
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns(None, "/tmp/x.db", "tortoise")
    return str(exc.value)


def test_a_permission_refusal_is_diagnosed_as_the_CREDENTIAL(monkeypatch):
    """#11113 review P2: "the store could not be listed" blamed the wrong side.

    `GRAPH.LIST` is individually granted, and the repo's per-graph tenant user is
    deliberately forbidden it (`tortoise/acl_graph_users.py`, #2113/#8617) — so a
    census on the `--uri` leg with a tenant credential is a REFUSAL the operator
    can only act on if the message says whose problem it is. Measured on the
    pinned client (redis 7.4.1): the vendor parser maps `NOPERM` to
    `NoPermissionError`, whose MESSAGE the ACL predicate matches — the
    classification is a text test (`tortoise/acl_graph_users.py`
    `_is_permission_refusal`: `"noperm" in msg or "no permissions" in msg`),
    NOT a type test, and there is no type arm anywhere in this pair. This case
    therefore pins the wording an operator actually sees, not a behaviour the
    class alone would buy (#11113 review cycle 11, P2).
    """
    from redis.exceptions import NoPermissionError

    err = _refusal_from_listing(
        monkeypatch,
        NoPermissionError("NOPERM this user has no permissions to run the "
                          "'graph.list' command"))

    assert "credential" in err, err
    assert "GRAPH.LIST" in err, err
    assert "does not exist" not in err, err      # the store is NOT at fault
    assert "nothing was created" in err, err
    # the remedy an operator will otherwise reach for is the forbidden one
    assert "Do NOT grant" in err, err
    assert "per-graph tenant user" in err, err
    assert "#2113/#8617" in err, err


def test_the_permission_arm_also_reads_the_vendor_TEXT(monkeypatch):
    """The text arm, for a client/version or double that hands back a bare
    `ResponseError` — the code is the contract, not the class."""
    from redis.exceptions import ResponseError

    err = _refusal_from_listing(
        monkeypatch,
        ResponseError("NOPERM User tenant_acme has no permissions to run the "
                      "'graph.list' command"))
    assert "credential" in err, err
    assert "GRAPH.LIST" in err, err


def test_a_transport_failure_is_NOT_reported_as_a_permission_refusal(
        monkeypatch):
    """⛔ The answered-refusal / outage split, both directions.

    On the pinned client `AuthenticationError` and `AuthorizationError` SUBCLASS
    `ConnectionError`, so a type-or-message test that admitted the transport
    family would send an operator after their credentials when the box was
    down (`tortoise/retry.py` records the same trap, #8827).
    """
    from redis.exceptions import ConnectionError as RedisConnectionError

    err = _refusal_from_listing(
        monkeypatch, RedisConnectionError("Error connecting to localhost:6379"))
    assert "could not be listed" in err, err
    assert "credential" not in err, err


def test_a_REFUSED_BIND_is_diagnosed_as_the_credential_too(monkeypatch):
    """#11113 review P3: the tenant user is refused EARLIER than the listing.

    Measured against a real server with the ACL layer's own grant recipe
    (`-@all +GRAPH.QUERY +GRAPH.RO_QUERY +PING`): the pinned client's constructor
    issues `INFO`, the grant denies it, and the run dies in the BIND — so it
    never reached `_refuse_unless_graph_present` and reported "the graph could
    not be read", the same blame-the-wrong-side shape the listing arm repaired.
    """
    from redis.exceptions import NoPermissionError

    import tortoise.projection as projection

    def _refuse(*a, **k):
        raise NoPermissionError("User tenant_x has no permissions to run the "
                                "'info' command")

    monkeypatch.setattr(projection, "FalkorProjection",
                        types.SimpleNamespace(from_uri=staticmethod(_refuse)))

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://tenant_x:pw@127.0.0.1:1/g", None, "g")
    err = str(exc.value)
    assert "credential" in err, err
    assert "INFO" in err, err
    assert "GRAPH.LIST" in err, err
    assert "could not be read" not in err, err   # the STORE is not at fault
    assert "#2113/#8617" in err, err


def test_a_transport_failure_IN_THE_BIND_keeps_the_generic_wording(monkeypatch):
    """NON-VACUITY for the arm above: an outage must stay an outage.

    Without this, "a refused bind names the credential" would hold for every
    bind failure and would say nothing about the NOPERM arm — the same false PASS
    the listing arm's twin test closes.
    """
    from redis.exceptions import ConnectionError as RedisConnectionError

    import tortoise.projection as projection

    def _down(*a, **k):
        raise RedisConnectionError("Error connecting to 127.0.0.1:1")

    monkeypatch.setattr(projection, "FalkorProjection",
                        types.SimpleNamespace(from_uri=staticmethod(_down)))

    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://:@127.0.0.1:1/g", None, "g")
    err = str(exc.value)
    assert "could not be read" in err, err
    assert "credential" not in err, err


def test_the_permission_predicate_is_the_ACL_layer_s_own():
    """⛔ ONE rule, not a second spelling — pinned STRUCTURALLY.

    `tortoise/acl_graph_users.py` mints the per-graph user AND owns the predicate
    that recognises its refusal; a copy here would drift from the module that
    defines the grant (`_quote_identifier`, `_bounded_server_socket_kwargs` are
    the repo's same move in the sibling censuses).

    ⛔ `not hasattr(tpc, "_is_permission_refusal")` ALONE IS VACUOUS — MEASURED
    (#11113 review P2): replacing the import with an INLINE respelling inside
    `_refuse_unless_graph_present`

        refused_by_credential = (isinstance(exc, NoPermissionError)
                                 or "noperm" in str(exc).lower()
                                 or "no permissions" in str(exc).lower())

    left this suite fully green, i.e. the property the test names could be
    violated while every pin still passed — the same false-PASS class the
    `_RECORD_TURNS_SQL` pin above was written with `ast` to close. So the
    structural half is the pin: the handler that CLASSIFIES the listing failure
    must import the ACL layer's predicate by name.

    (No pass count is quoted: the file's collection size drifts with every pin
    added, and a stale figure reads as a measurement of THIS head — see the
    currency rule the `ci-surfaces.yml` duration rows state.)
    """
    assert not hasattr(tpc, "_is_permission_refusal"), (
        "import tortoise.acl_graph_users._is_permission_refusal instead of "
        "respelling it — a second spelling drifts from the rule that defines "
        "the grant")

    source = (Path(__file__).resolve().parent.parent
              / "tools" / "turn_parity_census.py").read_text(encoding="utf-8")
    handlers = [
        node
        for fn in ast.walk(ast.parse(source))
        if isinstance(fn, ast.FunctionDef)
        and fn.name == "_refuse_unless_graph_present"
        for node in ast.walk(fn)
        if isinstance(node, ast.ExceptHandler)
    ]
    imports = [
        alias.name
        for handler in handlers
        for node in ast.walk(handler)
        if isinstance(node, ast.ImportFrom)
        and node.module == "tortoise.acl_graph_users"
        for alias in node.names
    ]
    assert "_is_permission_refusal" in imports, (
        "`_refuse_unless_graph_present` must IMPORT "
        "tortoise.acl_graph_users._is_permission_refusal to classify a refused "
        "listing; no such import was found in its exception handlers, so the "
        "rule is being respelled here (found imports: "
        f"{imports or 'none'})")


def test_the_uri_leg_binds_without_WRITING_either(monkeypatch):
    """The URI leg is the one that can mint a graph on a LIVE server.

    #11113 review, non-blocking observation (i): the flags were pinned only on
    the embedded branch, because every `from_uri` fake swallowed `**k` — so a
    future edit dropping `ensure_indexes=False` there would have been silent on
    the leg that writes to a customer instance.
    """
    seen = {}

    class _G:
        def query(self, cypher, params=None):
            class _R:
                result_set = [[0]] if "count" in cypher else []
            return _R()

    from tortoise.projection import FalkorProjection

    class _Proj:
        g = _G()
        _graph_name = "g"

        class _DB:
            @staticmethod
            def list_graphs():
                return ["g"]

        db = _DB()

        @classmethod
        def from_uri(cls, *a, **k):
            seen.update(k)
            seen["positional"] = a
            return cls()

    monkeypatch.setattr(FalkorProjection, "from_uri", _Proj.from_uri)
    assert tpc.read_graph_turns("docker://h:1/g", None, "g")["nodes"] == {}
    assert seen["positional"] == ("docker://h:1/g",)
    assert seen["skip_health_check"] is True
    assert seen["ensure_indexes"] is False


def test_presence_is_asked_about_the_RESOLVED_name_not_the_argument(
        monkeypatch):
    """#11113 review, non-blocking observation (ii): nothing covered this.

    A redirect or a test-name derivation can change which graph is addressed,
    so the question is asked about the projection's OWN name (`_graph_name`) —
    refusing on the raw argument would reject a graph the store really holds,
    and accepting on it would open a graph the store does not.
    """
    seen = {}

    class _G:
        def query(self, cypher, params=None):
            seen["queries"] = seen.get("queries", 0) + 1

            class _R:
                result_set = [[0]] if "count" in cypher else []
            return _R()

    from tortoise.projection import FalkorProjection

    def _proj_for(graphs):
        class _DB:
            @staticmethod
            def list_graphs():
                return list(graphs)

        class _Proj:
            g = _G()
            db = _DB()
            _graph_name = "resolved_acme"     # the redirect's name, not the arg

        return _Proj

    # the STORE holds the RESOLVED name while the ARGUMENT names something else:
    # that is not a refusal — the argument is a selector, not an address.
    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _proj_for(
                            ["resolved_acme"])()))
    assert tpc.read_graph_turns("docker://h:1/team_acme", None,
                                "team_acme")["nodes"] == {}
    assert seen.get("queries")                      # it really read

    # …and when only the ARGUMENT's name is present, refusing is correct:
    # opening `resolved_acme` is what would create it.
    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _proj_for(
                            ["team_acme"])()))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_graph_turns("docker://h:1/team_acme", None, "team_acme")
    assert "'resolved_acme'" in str(exc.value), str(exc.value)


def test_the_graph_list_requirement_is_documented_at_the_flag(capsys):
    """#11113 review P2(b): the requirement is stated where the operator reads it.

    The module documented no permission requirement at all, so the natural
    remedy an operator reached for was the one the ACL layer forbids. A help
    string and a docstring are the only places an operator reads BEFORE the
    flag is chosen, so both name it.
    """
    with pytest.raises(SystemExit) as exit_:
        tpc.main(["--help"])
    assert exit_.value.code == 0
    help_text = capsys.readouterr().out
    assert "GRAPH.LIST" in help_text, help_text
    assert "credential" in help_text, help_text

    doc = " ".join((tpc.__doc__ or "").split())
    assert "GRAPH.LIST" in doc
    assert "per-graph tenant user" in doc


def test_a_caller_holding_the_handle_does_not_re_ask_the_store(monkeypatch):
    """The delete-time re-check addresses a handle it ALREADY holds.

    `purge_legacy_turns` re-reads a session through the projection it is about
    to delete from (`g=proj.g`, #10483 review P2-1). Listing the store there
    would ask a second connection about the graph the caller is already
    addressing — and refuse work that is provably addressable.
    """
    class _G:
        def query(self, cypher, params=None):
            class _R:
                result_set = [[0]] if "count" in cypher else []
            return _R()

    class _Proj:
        class _DB:
            @staticmethod
            def list_graphs():
                raise AssertionError("the store was listed for a bound handle")

        db = _DB()
        g = _G()

    import tortoise.projection as projection
    monkeypatch.setattr(projection, "FalkorProjection", lambda *a, **k: _Proj())
    assert tpc.read_graph_turns(None, None, None, node_ids=["s1_t1"],
                                g=_Proj.g)["nodes"] == {}

# ── the record leg: its door, its SQL contract, and its refusals ───────────
#
# Nothing in the file above calls `read_record_turns`; these cases do, against
# a stubbed driver, so the arms the PR body claims are pinned (scoped door,
# exit 3 on an unreadable side) really are: deleting the `scope_connection`
# call, or a renamed `public.point` column, reds here.

def _stub_record(monkeypatch, *, columns=None, rows=(), refuse_scope=False):
    """A stubbed record leg: the door is a spy, the connection has no cursor.

    `_Conn.cursor()` raises, so a leg that reaches the connection directly
    (bypassing `scope_connection`) cannot pass — it must go through the door.
    """
    import tortoise.projection  # noqa: F401 - imported for its side effects
    from tortoise import record_projection, record_serve

    monkeypatch.setitem(sys.modules, "psycopg2", types.ModuleType("psycopg2"))
    calls: dict = {}

    class _Cursor:
        def __init__(self):
            self.description = [(c,) for c in (columns
                                               or _RECORD_COLUMNS_EXPECTED)]

        def execute(self, sql, params):
            calls["sql"], calls["params"] = sql, params

        def fetchall(self):
            return list(rows)

    class _Conn:
        def cursor(self):  # a bypass must not be a way through
            raise AssertionError("the record leg must use the SCOPE door")

        def close(self):
            calls["closed"] = True

    conn = _Conn()

    def _open(dsn):
        calls["dsn"] = dsn
        return conn

    def _scope(opened, org_id):
        calls["scope"] = (opened, org_id)
        if refuse_scope:
            raise RuntimeError("tenant scope refused (BYPASSRLS role)")
        return _Cursor()

    monkeypatch.setattr(record_serve, "open_record", _open)
    monkeypatch.setattr(record_projection, "scope_connection", _scope)
    return calls, conn


#: The record columns the census consumes, spelled out HERE rather than read
#: from the tool: a stub built from the tool's own constant cannot catch the
#: constant shrinking together with the query.
_RECORD_COLUMNS_EXPECTED = ("node_key", "content", "speaker", "status",
                           "created_at", "embedding", "point_kind",
                           "is_episodic")


def _record_row_tuple(tid: str, *, kind="event", content=None) -> tuple:
    return (tid, content or f"[user] body of {tid}", "user", "draft",
            "2026-10-25T00:00:00+00:00", None, kind, True)


def test_the_record_leg_goes_through_the_scoped_door(monkeypatch):
    """The #10024 shape: an unscoped read returns zero rows and would certify.

    Asserts the DOOR is the one used (it is called, with the tenant org, and
    the connection's own `cursor()` is never reached), that the tenant pair is
    bound as SQL parameters, and that the SQL selects every column the
    comparison and the classifier consume.
    """
    calls, conn = _stub_record(
        monkeypatch, rows=[_record_row_tuple("s1_t0"),
                           _record_row_tuple("claim-1", kind="claim")])
    out = tpc.read_record_turns("postgresql://x/y", "org-1", "graph-1")
    assert calls["scope"] == (conn, "org-1")
    assert calls["dsn"] == "postgresql://x/y"
    assert calls["sql"] == tpc._RECORD_TURNS_SQL
    assert calls["params"] == ("org-1", "graph-1")
    # The SQL really selects every column the comparison and the classifier
    # consume, and filters on the same predicate the graph leg reads.
    assert calls["sql"] == tpc._RECORD_TURNS_SQL
    assert tuple(tpc._RECORD_TURNS_COLUMNS) == _RECORD_COLUMNS_EXPECTED
    for column in _RECORD_COLUMNS_EXPECTED:
        assert column in calls["sql"], f"the SQL omits {column}"
    assert "FROM public.point" in calls["sql"]
    assert "is_episodic IS TRUE" in calls["sql"]
    assert calls["closed"] is True
    # The turn key is grouped; the non-turn episodic row is counted, not lost.
    assert sorted(out["rows_by_key"]) == ["s1_t0"]
    assert out["non_turn_shaped"] == 1


def test_the_executed_record_SQL_is_DERIVED_from_the_column_contract():
    """ONE declaration: the executed query is built from `_RECORD_TURNS_COLUMNS`.

    That tuple is documented as the single source of the SELECT list AND as the
    contract checked against the cursor's `description`. The module carried a
    second, hand-spelled `_RECORD_TURNS_SQL` BELOW the derived one, and Python
    keeps the LAST assignment — so the executing statement was the handwritten
    copy while the comment above it described an invariant the query did not
    use. The two were SQL-equivalent, so nothing red; the day the tuple gained
    or renamed a column the query would keep selecting the old set, and the
    moved-schema guard could not see it (the old column is still `description`'d
    — it is the census that stops naming it).
    """
    source = (Path(__file__).resolve().parent.parent
              / "tools" / "turn_parity_census.py").read_text(encoding="utf-8")
    bindings = [node for node in ast.parse(source).body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name)
                        and target.id == "_RECORD_TURNS_SQL"
                        for target in node.targets)]
    assert len(bindings) == 1, (
        f"_RECORD_TURNS_SQL is bound {len(bindings)} times at module level — "
        "a later binding shadows the one derived from _RECORD_TURNS_COLUMNS")
    assert tpc._RECORD_TURNS_SQL.split(" FROM ")[0] == (
        "SELECT " + ", ".join(tpc._RECORD_TURNS_COLUMNS))


def test_a_scope_refusal_is_an_unreadable_side_not_an_empty_record(monkeypatch):
    """A refused tenant scope must exit 3, never read as "the record is empty"."""
    _stub_record(monkeypatch, refuse_scope=True)
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_record_turns("postgresql://x/y", "org-1", "graph-1")
    assert "tenant scope refused" in str(exc.value)


def test_a_moved_record_column_refuses_rather_than_certifying_vacuously(
        monkeypatch):
    """A dropped `node_key` would leave every row unkeyed — an EMPTY comparison.

    The census would then compare a full graph against zero rows and report a
    verdict for a record it never read; the schema gap must be exit 3.
    """
    columns = tuple(c for c in _RECORD_COLUMNS_EXPECTED if c != "node_key")
    _stub_record(monkeypatch, columns=columns,
                 rows=[_record_row_tuple("s1_t0")])
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_record_turns("postgresql://x/y", "org-1", "graph-1")
    assert "node_key" in str(exc.value)


def test_a_record_leg_refusal_reaches_exit_three(monkeypatch):
    """End to end through `main`: the door's refusal is the exit CODE, not a 0."""
    _stub_record(monkeypatch, refuse_scope=True)
    monkeypatch.setattr(tpc, "read_graph_turns",
                        lambda *a, **k: {"nodes": {"s1_t0": _graph_node(
                            "s1_t0", "s1")}})
    assert tpc.main(_argv()) == 3


# ── the empty key set: an empty read, and nothing leaked ──────────────────
#
# `node_keys=[]` is a SUPPORTED input ("an EMPTY key set is an empty read"),
# but its early `return` sat between `open_record` and the `try/finally` that
# closes the connection — so the one input that reads nothing leaked a bounded
# record connection every time it was called (#10483 review round 4, P2).

def test_an_empty_key_set_reads_nothing_and_opens_no_connection(monkeypatch):
    """Hoisted above the open: with no connection there is nothing to close."""
    calls, _conn = _stub_record(monkeypatch)
    from tortoise import record_serve

    def _never(dsn):
        raise AssertionError(
            "an empty key set must not open a record connection")

    monkeypatch.setattr(record_serve, "open_record", _never)

    out = tpc.read_record_turns("postgresql://x", "org", "graph",
                                node_keys=[])

    assert out == {"rows_by_key": {}, "non_turn_shaped": 0}
    assert calls == {}       # no dsn, no scope, no close — nothing happened


def test_a_generator_key_set_is_consumed_exactly_once(monkeypatch):
    """The emptiness decision must not consume a one-shot iterable and leave
    the query with an empty key list (which would read nothing, silently)."""
    calls, _conn = _stub_record(monkeypatch,
                                rows=[_record_row_tuple("s1_t0")])

    out = tpc.read_record_turns("postgresql://x", "org", "graph",
                                node_keys=(k for k in ["s1_t0"]))

    assert "s1_t0" in out["rows_by_key"]
    assert calls["params"][2] == ["s1_t0"]


# ── the LOCAL record leg (owner ruling 2026-11-01: the local JSONL IS the
#    authority while hosted setup is postponed) ───────────────────────────────

def _local_record(tmp_path, sessions: dict[str, list[dict]],
                  name: str = "2026-11-02.jsonl") -> Path:
    """One day-addressed capture-log file, shaped exactly as the real one."""
    path = tmp_path / name
    lines = []
    for sid, conversation in sessions.items():
        lines.append(json.dumps({
            "session_id": sid,
            "conversation": conversation,
            "metadata": {"capturedAt": "2026-11-02T00:00:00Z"},
            "harness": "pi", "machine_id": "m", "model": "x",
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _conversation(*turns):
    return [{"role": role, "content": text} for role, text in turns]


def test_the_local_leg_keys_by_position_and_uses_the_writers_own_frame(
        tmp_path):
    """`entry i` IS `t<i>`, and the text is the WRITER's frame, not a copy."""
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"),
                                                 ("assistant", "yes"))})
    record = tpc.read_local_record_turns(tmp_path)
    assert sorted(record["rows_by_key"]) == ["s1_t0", "s1_t1"]
    assert record["rows_by_key"]["s1_t0"]["content"] == "[user] hi"
    assert record["rows_by_key"]["s1_t1"]["speaker"] == "assistant"
    # The one declaration this leg makes, stated by name and never faked.
    assert record["declared_absent"] == ["status", "created_at",
                                        "point_kind", "embedding"]


def test_local_parity_passes_when_the_record_holds_every_turn(tmp_path):
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"),
                                                 ("assistant", "yes"))})
    record = tpc.read_local_record_turns(tmp_path)
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi"),
             "s1_t1": _graph_node("s1_t1", "s1", content="[assistant] yes",
                                  speaker="assistant")}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "CLEAN"
    assert report["dry_run"]["would_delete_turn_nodes"] == 2
    # The weaker comparison is VISIBLE in the artifact, never a silent pass.
    assert report["declared_absent"] == ["created_at", "embedding",
                                         "point_kind", "status"]
    assert report["compared_fields"] == ["key", "content", "speaker"]
    # WHICH authority produced this verdict is in the artifact (review P2).
    assert report["record"]["leg"] == "local"
    assert report["record"]["dir"].endswith(str(tmp_path.resolve())[-8:])


def test_a_record_may_not_declare_the_transcript_absent():
    """`content`/`speaker` ARE the transcript — declining them is not a leg."""
    record = {"rows_by_key": {"s1_t0": _record_row("s1_t0")},
              "non_turn_shaped": 0, "declared_absent": ["content"]}
    with pytest.raises(tpc.CensusError, match="IS the transcript"):
        tpc.census({"nodes": {"s1_t0": _graph_node("s1_t0", "s1")}}, record,
                   sample=5)


def test_local_parity_refuses_a_turn_the_record_does_not_hold(tmp_path):
    """A graph turn with no local entry is the backfill case, never a delete."""
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    record = tpc.read_local_record_turns(tmp_path)
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi"),
             "s1_t1": _graph_node("s1_t1", "s1", content="[user] second")}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "REFUSED"
    assert report["dry_run"]["would_delete_turn_nodes"] == 0
    assert [p["problem"] for p in _problems(report, "s1")] == \
        ["missing_in_record"]


def test_a_shifted_window_mismatches_on_text_and_never_certifies(tmp_path):
    """A re-capture that prepends a turn shifts every index — it must FAIL."""
    _local_record(tmp_path, {"s1": _conversation(("user", "EXTRA"),
                                                 ("user", "hi"))})
    record = tpc.read_local_record_turns(tmp_path)
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi")}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "REFUSED"
    assert _problems(report, "s1")[0]["field"] == "text"


def test_an_interior_unparsable_line_is_DROPPED_disclosed_and_still_FAILS_CLOSED(
        tmp_path):
    """Review P1: dropping the line is fail-closed; refusing the LEG was not.

    The old posture refused the whole read, and on the authority store it was
    measured against (ten lost-newline merges) that meant #10017's gate could
    never open — an instrument that answers nothing is not a guard. The
    replacement must satisfy BOTH halves, and this pin asserts both, because
    the disclosure alone is not a safety property:

      (a) the line is dropped, NAMED in `unparsable_lines`, and the readable
          sessions of the same record still read; and
      (b) a graph turn for the session the dropped line described is
          `missing_in_record` -> REFUSED. That is what makes the drop safe,
          and it holds because the POPULATION COMES FROM THE GRAPH.
    """
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))},
                  name="2026-11-01.jsonl")
    bad = tmp_path / "2026-11-02.jsonl"
    good = json.dumps({"session_id": "s3",
                       "conversation": _conversation(("user", "x"))})
    # `s2`'s record lost its newline, so it merged into `s3`'s line.
    bad.write_text('{"session_id": "s2", "conversation": [{"role": "user",\n'
                   + good + "\n", encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    # `s1` and `s3` are readable; only the truncated `s2` line was dropped.
    assert list(record["rows_by_key"]) == ["s1_t0", "s3_t0"]
    assert record["torn_tail"] == []
    assert [b["at"] for b in record["unparsable_lines"]] == [
        "2026-11-02.jsonl:1"]

    # (b) the fail-closed consequence: s2 has turns in the graph, its record
    # line was dropped, so the session is missing_in_record -> REFUSED. A drop
    # can never reach CLEAN over a turn the graph holds.
    nodes = {"s2_t0": _graph_node("s2_t0", "s2", content="[user] lost")}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "REFUSED"
    assert "missing_in_record" in [p["problem"] for p in _problems(report, "s2")]


def test_a_dropped_interior_line_is_never_PARTIALLY_recovered(tmp_path):
    """The parseable half of a merged line is NOT used.

    A lost newline between two concurrent appends produces ONE line holding two
    session records: the leading one is truncated and the trailing one parses.
    Recovering that trailing record would certify a session whose sibling
    record was lost — a false pass — so the whole line is dropped, and BOTH
    sessions are then absent (the graph's copy is what decides them).
    """
    # A SETTLED day, so this is corruption and not the tolerance-able tail.
    # `s2`'s record lost its newline, so it merged into `s3`'s line: TWO
    # records on ONE line, the leading one truncated and the trailing one
    # perfectly parseable.
    truncated = '{"session_id": "s2", "conversation": [{"role": "user"'
    trailing = json.dumps({"session_id": "s3",
                           "conversation": _conversation(("user", "b"))})
    merged = truncated + trailing
    (tmp_path / "2026-11-01.jsonl").write_text(merged + "\n", encoding="utf-8")
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    record = tpc.read_local_record_turns(tmp_path)
    # NEITHER half is recovered: not the truncated `s2`, and not the
    # parseable `s3` that shares its line.
    assert list(record["rows_by_key"]) == ["s1_t0"]
    assert len(record["unparsable_lines"]) == 1
    assert record["unparsable_lines"][0]["at"] == "2026-11-01.jsonl:1"


def test_a_torn_tail_is_COUNTED_and_reported_never_silently_dropped(tmp_path):
    path = _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"session_id": "s2", "conversa')  # mid-write, no newline
    record = tpc.read_local_record_turns(tmp_path)
    assert list(record["rows_by_key"]) == ["s1_t0"]
    assert len(record["torn_tail"]) == 1
    assert record["torn_tail"][0]["at"] == "2026-11-02.jsonl:2"


def test_an_interior_torn_line_in_the_NEWEST_file_is_dropped_not_a_tail(
        tmp_path):
    """Tail tolerance is the LAST line only — not every line of that file.

    An interior unparsable line is now DROPPED and named (review P1) rather than
    refusing the leg, but the distinction the old pin drew is still held: it is
    NOT a `torn_tail`, so the artifact never calls corruption a mid-write tip.
    """
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))},
                  name="2026-11-01.jsonl")
    newest = tmp_path / "2026-11-02.jsonl"
    newest.write_text('{"session_id": "s2", "conversa\n'
                      '{"session_id": "s3", "conversation": []}\n',
                      encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert record["torn_tail"] == []
    assert [b["at"] for b in record["unparsable_lines"]] == [
        "2026-11-02.jsonl:1"]


def test_a_duplicated_session_is_HELD_BACK_not_a_whole_record_refusal(
        tmp_path):
    """Review P2: one resumed session must not gate the WHOLE census.

    Two windows for one session cannot be placed positionally — but that is a
    RECOGNISED state (`tools/build_window_transcript.py` refuses it by name, and
    #3870 forbids deleting the record lines that would clear it), so refusing
    the entire population made the gate unopenable over ONE resumed session with
    no legitimate exit. It is held back into its own partition instead.

    Mutation that goes RED here: restoring the whole-record `raise` (the leg
    would never return, so the assertions below cannot run).
    """
    _local_record(tmp_path, {"s1": _conversation(("user", "a"))},
                  name="2026-11-01.jsonl")
    _local_record(tmp_path, {"s1": _conversation(("user", "b")),
                            "s2": _conversation(("user", "c"))},
                  name="2026-11-02.jsonl")
    record = tpc.read_local_record_turns(tmp_path)
    # The duplicated session leaves the positional key space…
    assert sorted(record["rows_by_key"]) == ["s2_t0"]
    # …and is DISCLOSED, with the window count, rather than silently dropped.
    assert record["duplicate_window"] == [{"session": "s1", "windows": 2}]
    assert record["sessions"] == 1


def test_a_duplicated_session_the_graph_holds_REFUSES_by_name(tmp_path):
    """…and when the graph really holds its turns, the cause is the DUPLICATE.

    Reported as `duplicate_window`, never as `missing_in_record`: the latter
    points the operator at a graph/backfill, which would write the wrong
    window's turns into the record under keys that do not describe them.

    Mutation that goes RED here: dropping the `held_duplicates` arm (the turns
    then surface as `missing_in_record`).
    """
    _local_record(tmp_path, {"s1": _conversation(("user", "a"))},
                  name="2026-11-01.jsonl")
    _local_record(tmp_path, {"s1": _conversation(("user", "a"))},
                  name="2026-11-02.jsonl")
    record = tpc.read_local_record_turns(tmp_path)
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] a")}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "REFUSED"
    assert report["refused_for"] == ["duplicate_window"]
    problem = _problems(report, "s1")[0]
    assert problem["problem"] == "duplicate_window"
    assert "NOT a graph/backfill gap" in problem["detail"]
    # The graph's turn is IN the delete set but NOT eligible: a REFUSED run must
    # never read as "there is nothing to delete".
    assert report["dry_run"]["would_delete_turn_nodes"] == 0
    assert report["dry_run"]["held_back_turn_nodes"] == 1
    assert report["record"]["duplicate_window"] == [
        {"session": "s1", "windows": 2, "graph_turn_nodes": 1}]
    # ⛔ NO SYNTHETIC NODE ID (review P2). `node` is what every consumer treats
    # as a turn id — `purge_legacy_turns.classify_sessions` collects it into
    # `sample_ids` — so a placeholder like `s1_t*` names a node nothing can
    # resolve. Mutation that goes RED here: `"node": f"{sid}_t*"`.
    assert problem["node"] is None


def test_the_duplicate_refusal_reaches_the_consumer_as_DUPLICATE_WINDOW(
        tmp_path):
    """The census's own warning must survive into the plan's classification.

    `classify_sessions` used to fall through to MISMATCH ("the record holds a
    DIFFERENT transcript"), which INVERTS this arm's advice — the remedy for
    MISMATCH is a field repair and for RECORD-BEHIND a backfill, and both write
    the wrong window over a duplicate. This drives the REAL census report into
    the REAL classifier, so it cannot pass on a hand-built fixture.

    Mutations that go RED here: `"node": f"{sid}_t*"` (a fabricated id reaches
    `sample_ids`), and deleting the `duplicate` branch in `classify_sessions`.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import purge_legacy_turns as purge

    _local_record(tmp_path, {"s1": _conversation(("user", "a"))},
                  name="2026-11-01.jsonl")
    _local_record(tmp_path, {"s1": _conversation(("user", "a"))},
                  name="2026-11-02.jsonl")
    record = tpc.read_local_record_turns(tmp_path)
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] a")}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "REFUSED"

    out = purge.classify_sessions(report)
    assert out["eligible"] == []
    entry = next(e for e in out["excluded"] if e["session"] == "s1")
    assert entry["class"] == purge.CLASS_DUPLICATE_WINDOW
    # The fabricated placeholder is GONE from the surface a consumer reads.
    assert entry["sample_ids"] == []
    assert out["counts"][purge.CLASS_DUPLICATE_WINDOW] == 1
    assert out["counts"][purge.CLASS_MISMATCH] == 0


def test_a_duplicated_session_with_NO_graph_turn_does_NOT_block_the_census(
        tmp_path):
    """A refusal with no repair path is not a guard: a duplicate no graph node
    needs placed stays disclosed, and the census still reaches CLEAN."""
    _local_record(tmp_path, {"s_dup": _conversation(("user", "a"))},
                  name="2026-11-01.jsonl")
    _local_record(tmp_path, {"s_dup": _conversation(("user", "a")),
                            "s1": _conversation(("user", "hi"))},
                  name="2026-11-02.jsonl")
    record = tpc.read_local_record_turns(tmp_path)
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi")}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "CLEAN"
    assert report["record"]["duplicate_window"] == [
        {"session": "s_dup", "windows": 2, "graph_turn_nodes": 0}]


def test_an_empty_or_absent_local_record_refuses(tmp_path):
    with pytest.raises(tpc.CensusError, match="the local record is"):
        tpc.read_local_record_turns(tmp_path)
    with pytest.raises(tpc.CensusError, match="does not exist"):
        tpc.read_local_record_turns(tmp_path / "nope")


def test_an_EMPTY_local_record_dir_never_falls_back_to_the_hosted_leg(
        tmp_path, monkeypatch, capsys):
    """Review P2: `--local-record-dir "$UNSET_VAR"` is a SET-BUT-EMPTY flag
    value, not an absent one — the empty-vs-unset distinction every CLI
    convention (and `os.LookupEnv` / `${VAR-}` vs `${VAR:-}`) exists to keep.

    Truthiness sent it down the hosted branch, so an ambient
    `TORTOISE_RECORD_DSN` — the store the 2026-11-01 ruling says is NOT the
    authority — was measured instead, and could exit 0 CLEAN over it.

    Mutation that goes RED here: restoring `if args.local_record_dir:` (the run
    then reaches the graph guard / the hosted leg and exits 3, not 1).
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://x/y")
    monkeypatch.setenv("TORTOISE_RECORD_ORG_ID", "o")
    monkeypatch.setenv("TORTOISE_RECORD_GRAPH_ID", "g")
    code = tpc.main(["--db-path", str(tmp_path / "g.db"),
                     "--local-record-dir", ""])
    assert code == 1
    err = capsys.readouterr().err
    assert "EMPTY" in err
    assert "NOT the authority" in err
    # Whitespace-only is the same state: no path is being named.
    assert tpc.main(["--db-path", str(tmp_path / "g.db"),
                     "--local-record-dir", "   "]) == 1


def test_an_EMPTY_record_dsn_never_falls_back_to_the_AMBIENT_one(
        tmp_path, monkeypatch, capsys):
    """Review P1: the SAME empty-vs-unset rule the local flag already had.

    `--record-dsn "$UNSET_VAR"` is a SET-BUT-EMPTY value. `args.record_dsn or
    os.environ.get(...)` treated it as absent, so an ambient
    `TORTOISE_RECORD_DSN` (a drill/scratch/stale record, `docs/infra-runbook.md`
    treats that as a real category) was measured under the OPERATOR'S OWN
    tenant and could exit 0 CLEAN over a store they never named.

    Mutation that goes RED here: restoring `args.record_dsn or os.environ.get(`
    (the run then takes the hosted leg and exits 3 at the unreachable graph,
    not 1 at the flag).
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://ambient/db")
    code = tpc.main(["--db-path", str(tmp_path / "g.db"),
                     "--record-dsn", "",
                     "--org-id", "o", "--graph-id", "g"])
    assert code == 1
    err = capsys.readouterr().err
    assert "EMPTY" in err
    assert "TORTOISE_RECORD_DSN" in err
    # Whitespace-only is the same state: no store is being named.
    assert tpc.main(["--db-path", str(tmp_path / "g.db"),
                     "--record-dsn", "   ",
                     "--org-id", "o", "--graph-id", "g"]) == 1
    # …and an ABSENT flag still honours the documented ambient default.
    assert tpc.main(["--db-path", str(tmp_path / "g.db"),
                     "--org-id", "o", "--graph-id", "g"]) != 1


def test_the_local_leg_reads_only_the_keys_it_was_asked_for(tmp_path):
    _local_record(tmp_path, {"s1": _conversation(("user", "a"),
                                                 ("user", "b"))})
    record = tpc.read_local_record_turns(tmp_path, node_keys={"s1_t1"})
    assert list(record["rows_by_key"]) == ["s1_t1"]


def test_the_cli_refuses_an_explicitly_ambiguous_record_choice(tmp_path):
    """Two record legs is not a preference — it is a wrong-authority risk."""
    code = tpc.main(["--db-path", str(tmp_path / "g.db"),
                     "--record-dsn", "postgresql://x/y",
                     "--org-id", "o", "--graph-id", "g",
                     "--local-record-dir", str(tmp_path)])
    assert code == 1


def test_an_AMBIENT_record_dsn_does_not_block_the_local_leg(tmp_path,
                                                            monkeypatch):
    """Review P1: the flag must work on a box that exports the documented DSN."""
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://x/y")
    monkeypatch.delenv("TORTOISE_RECORD_ORG_ID", raising=False)
    monkeypatch.delenv("TORTOISE_RECORD_GRAPH_ID", raising=False)
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    # The graph side is unreachable here, so the leg is reached (exit 3);
    # what matters is that it is NOT the usage refusal (exit 1).
    assert tpc.main(["--db-path", str(tmp_path / "g.db"),
                     "--local-record-dir", str(tmp_path)]) == 3


def test_a_sealed_archive_day_is_read_not_silently_invisible(tmp_path):
    """Review P1: the seal MOVES old days to archive/<day>.jsonl.gz."""
    import gzip
    archive = tmp_path / "archive"
    archive.mkdir()
    sealed = {"session_id": "s_old",
              "conversation": _conversation(("user", "old hi"))}
    (archive / "2026-10-01.jsonl.gz").write_bytes(
        gzip.compress((json.dumps(sealed) + "\n").encode()))
    _local_record(tmp_path, {"s_new": _conversation(("user", "new hi"))})
    record = tpc.read_local_record_turns(tmp_path)
    assert sorted(record["rows_by_key"]) == ["s_new_t0", "s_old_t0"]
    assert record["files"] == ["2026-11-02.jsonl",
                              "archive/2026-10-01.jsonl.gz"]


def test_both_shapes_in_both_directories_are_read_not_just_two(tmp_path):
    """Review P2: the local record is FOUR surfaces, not two.

    The substrate's own enumerator reads both shapes in both directories
    (`session_events_drain._candidate_files`: `for directory in (root /
    "archive", root)`), because a plain file under `archive/` and a `.gz` left in
    `root` are both readable substrates. This leg read only
    `root/*.jsonl` + `archive/*.jsonl.gz`, so the other two were dropped
    without a diagnostic while `files` reported itself as the coverage.
    """
    import gzip

    def _gz(path, sid):
        path.write_bytes(gzip.compress(
            (json.dumps({"session_id": sid,
                         "conversation": _conversation(("user", sid))})
             + "\n").encode()))

    archive = tmp_path / "archive"
    archive.mkdir()
    _local_record(tmp_path, {"s_plain_root": _conversation(("user", "a"))},
                  name="2026-11-02.jsonl")
    _gz(tmp_path / "2026-11-01.jsonl.gz", "s_gz_root")
    _local_record(tmp_path,
                  {"s_plain_archive": _conversation(("user", "c"))},
                  name="archive/2026-10-31.jsonl")
    _gz(archive / "2026-10-30.jsonl.gz", "s_gz_archive")

    record = tpc.read_local_record_turns(tmp_path)

    assert sorted(record["rows_by_key"]) == [
        "s_gz_archive_t0", "s_gz_root_t0",
        "s_plain_archive_t0", "s_plain_root_t0"]
    assert record["files"] == [
        "2026-11-01.jsonl.gz", "2026-11-02.jsonl",
        "archive/2026-10-30.jsonl.gz", "archive/2026-10-31.jsonl"]


def test_a_day_in_both_shapes_with_the_same_session_is_HELD_BACK(tmp_path):
    """A sealed `.gz` plus a re-created plain file is read once per shape, as
    the drain reads it — and where their session ids OVERLAP the leg cannot tell
    which window is `t0`, so the session is held back (never merged, never
    picked) instead of refusing the whole record (review P2)."""
    import gzip
    archive = tmp_path / "archive"
    archive.mkdir()
    same = {"session_id": "s1",
            "conversation": _conversation(("user", "hi")),
            "metadata": {"capturedAt": "2026-10-01T00:00:00Z"}}
    _local_record(tmp_path, {"s1": same["conversation"]},
                  name="2026-10-01.jsonl")
    (archive / "2026-10-01.jsonl.gz").write_bytes(
        gzip.compress((json.dumps(same) + "\n").encode()))

    record = tpc.read_local_record_turns(tmp_path)
    assert record["rows_by_key"] == {}
    assert record["duplicate_window"] == [{"session": "s1", "windows": 2}]


def test_a_corrupt_archive_refuses_rather_than_hiding_a_session(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-10-01.jsonl.gz").write_bytes(b"not gzip")
    with pytest.raises(tpc.CensusError, match="could not be read"):
        tpc.read_local_record_turns(tmp_path)


def test_a_turn_past_the_capture_cap_uses_the_writers_clipped_text(tmp_path):
    """Review P2: the WINDOW half of the stored-text rule must be pinned."""
    from tortoise.sdk import _capture_turn_texts_with_redactions
    long_body = "x" * 6001
    conversation = _conversation(("user", long_body))
    _local_record(tmp_path, {"s1": conversation})
    record = tpc.read_local_record_turns(tmp_path)
    stored = _capture_turn_texts_with_redactions(
        __import__("tortoise.sdk", fromlist=["_capture_turn_window"])
        ._capture_turn_window(conversation))[0][0]
    assert len(stored) < len(long_body)  # the window really clipped it
    assert record["rows_by_key"]["s1_t0"]["content"] == stored
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content=stored)}
    assert tpc.census({"nodes": nodes}, record, sample=5)["verdict"] == "CLEAN"


def test_the_cli_reaches_exit_three_when_the_local_record_refuses(tmp_path,
                                                                 monkeypatch):
    """A refusal on the RECORD axis is exit 3 — an absence is NOT established."""
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    code = tpc.main(["--db-path", str(tmp_path / "g.db"),
                     "--local-record-dir", str(tmp_path / "no-such-dir")])
    assert code == 3


def test_a_turn_past_the_SESSION_cap_uses_the_FILED_window(tmp_path):
    """Review P2: the graph's `t<i>` are minted over the POSTed list, not the
    file's — a session past `MAX_SESSION_TURNS` must be keyed through the SAME
    window the filing leg applies.

    Pre-fix, this record read `t0` = the file's FIRST turn while the graph's
    `t0` is the window's first turn (the drain drops the oldest past the cap),
    so every turn of every over-cap session mismatched on TEXT — a permanent
    false refusal on exactly the population this gate exists for, blaming the
    record for the filing leg's transform.

    Mutation that goes RED here: rendering `conversation` instead of
    `_filing_window(conversation)` (every key becomes `field_mismatch text`).
    """
    from tortoise.quota import MAX_SESSION_TURNS

    conversation = _conversation(
        *[("user", f"t{i}") for i in range(MAX_SESSION_TURNS + 1)])
    _local_record(tmp_path, {"s1": conversation})
    record = tpc.read_local_record_turns(tmp_path)

    assert len(record["rows_by_key"]) == MAX_SESSION_TURNS
    # `t0` is the WINDOW's first turn (record entry 1) — the first turn is gone.
    assert record["rows_by_key"]["s1_t0"]["content"] == "[user] t1"
    assert record["rows_by_key"][f"s1_t{MAX_SESSION_TURNS - 1}"]["content"] \
        == f"[user] t{MAX_SESSION_TURNS}"
    # Disclosed, never silent: the artifact says which keys came from a window.
    assert record["filed_window"] == [
        {"session": "s1", "record_turns": MAX_SESSION_TURNS + 1,
         "filed_turns": MAX_SESSION_TURNS}]

    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] t1"),
             "s1_t1": _graph_node("s1_t1", "s1", content="[user] t2")}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "CLEAN", report["parity"]["failed_sessions"]
    assert report["record"]["filed_window"] == record["filed_window"]


def test_a_BLANK_turn_is_dropped_by_the_filing_leg_and_the_keys_shift(
        tmp_path):
    """The other half of the same transform: `_window` DROPS a turn whose
    content is blank, so the graph's `t1` is the record's THIRD entry."""
    conversation = [{"role": "user", "content": "a"},
                    {"role": "user", "content": "   "},
                    {"role": "user", "content": "b"}]
    _local_record(tmp_path, {"s1": conversation})
    record = tpc.read_local_record_turns(tmp_path)

    assert sorted(record["rows_by_key"]) == ["s1_t0", "s1_t1"]
    assert record["rows_by_key"]["s1_t0"]["content"] == "[user] a"
    assert record["rows_by_key"]["s1_t1"]["content"] == "[user] b"
    assert record["filed_window"] == [
        {"session": "s1", "record_turns": 3, "filed_turns": 2}]

    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] a"),
             "s1_t1": _graph_node("s1_t1", "s1", content="[user] b")}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "CLEAN", report["parity"]["failed_sessions"]


def test_the_CLI_prints_the_filed_window_disclosure(tmp_path, capsys,
                                                    monkeypatch):
    """The transform must be VISIBLE on stdout, not only in the code path — an
    undisclosed re-keying reads like a clean positional match.

    Mutation that goes RED here: deleting the `filed window:` print loop.
    """
    conversation = [{"role": "user", "content": "a"},
                    {"role": "user", "content": "  "},
                    {"role": "user", "content": "b"}]
    _local_record(tmp_path, {"s1": conversation})
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {"s1_t0": _graph_node("s1_t0", "s1", content="[user] a"),
                  "s1_t1": _graph_node("s1_t1", "s1", content="[user] b")},
        "unidentifiable": []})
    code = tpc.main(["--uri", "docker://:@127.0.0.1:1/g",
                     "--local-record-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "filed window: session s1" in out
    assert "POSTed 2 of the record's 3 turn(s)" in out


def test_the_local_leg_redacts_like_the_writer_so_a_secret_is_not_a_mismatch(
        tmp_path):
    """Review P1: the stored text is windowed+SCRUBBED, then framed."""
    from tortoise.sdk import _capture_turn_texts_with_redactions
    conversation = _conversation(
        ("user", "the uri is docker://:falkordb@localhost:6379/tortoise"))
    _local_record(tmp_path, {"s1": conversation})
    record = tpc.read_local_record_turns(tmp_path)
    stored = _capture_turn_texts_with_redactions(conversation)[0][0]
    assert record["rows_by_key"]["s1_t0"]["content"] == stored
    # …and it is NOT the raw re-frame, which is what made this a false refusal.
    assert "falkordb@localhost" not in record["rows_by_key"]["s1_t0"]["content"]


def test_a_vector_bearing_graph_node_certifies_because_embedding_is_declared(
        tmp_path):
    """Review P1: the local record carries no vector; that is DECLARED, not a
    per-turn field_mismatch on every embedded deployment."""
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    record = tpc.read_local_record_turns(tmp_path)
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi",
                                  embedding=[0.1, 0.2])}
    report = tpc.census({"nodes": nodes}, record, sample=5)
    assert report["verdict"] == "CLEAN"
    assert "embedding" in report["declared_absent"]


def test_a_declared_field_that_is_not_compared_refuses():
    """Review P2: an invented narrowing claim must not reach the artifact."""
    record = {"rows_by_key": {"s1_t0": _record_row("s1_t0")},
              "non_turn_shaped": 0, "declared_absent": ["widget"]}
    with pytest.raises(tpc.CensusError, match="not a field this census"):
        tpc.census({"nodes": {"s1_t0": _graph_node("s1_t0", "s1")}}, record,
                   sample=5)


def test_a_sealed_day_of_NO_RECORDS_is_tolerated_and_DISCLOSED(tmp_path):
    """P1 (re-pinned): a COMPLETE archive of an empty payload is a day that
    provably held no records — the seal writes exactly that (`eligible()` has
    no size floor, `seal_one` compresses whatever it read), so refusing it
    blocked the census permanently on a state only deleting evidence could
    clear. Tolerated, and COUNTED so it cannot read as a silent skip.

    Mutation that goes RED here: restoring the old `not any(line.strip())`
    refusal in the `.gz` branch.
    """
    import gzip
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-10-01.jsonl.gz").write_bytes(gzip.compress(b""))
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    record = tpc.read_local_record_turns(tmp_path)
    assert list(record["rows_by_key"]) == ["s1_t0"]
    assert record["empty_days"] == ["archive/2026-10-01.jsonl.gz"]
    report = tpc.census({"nodes": {"s1_t0": _graph_node("s1_t0", "s1",
                                                         content="[user] hi")}},
                        record, sample=5)
    assert report["verdict"] == "CLEAN"
    # …and the tolerance is IN THE ARTIFACT, not just in the code path.
    assert report["record"]["empty_days"] == ["archive/2026-10-01.jsonl.gz"]


def test_a_tolerated_empty_day_still_cannot_hide_a_graph_turn(tmp_path):
    """The non-vacuity of the tolerance: the population comes from the GRAPH,
    so a day that is provably empty cannot certify a turn it should have
    carried — that turn is `missing_in_record` and the census REFUSES."""
    import gzip
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-10-01.jsonl.gz").write_bytes(gzip.compress(b""))
    # The record needs at least one real session: a record with NO session at
    # all is refused by the ZERO-sessions guard before comparison, which would
    # make this test pass for the wrong reason.
    _local_record(tmp_path, {"s2": _conversation(("user", "other"))})
    record = tpc.read_local_record_turns(tmp_path)
    report = tpc.census({"nodes": {"s1_t0": _graph_node("s1_t0", "s1",
                                                         content="[user] hi")}},
                        record, sample=5)
    assert report["verdict"] == "REFUSED"
    assert report["parity"]["failed_sessions"][0]["problems"][0]["problem"] \
        == "missing_in_record"


def test_a_ZERO_BYTE_archive_refuses_because_the_write_never_completed(
        tmp_path):
    """The REAL hazard, separated from the empty-day state by SIZE: a 20-byte
    `gzip.compress(b"")` is complete, a 0-byte file is a torn write whose day
    is now unreadable (the seal already unlinked the plain source).

    Mutation that goes RED here: dropping the `st_size == 0` arm (a 0-byte
    file decompresses to `b""` WITHOUT raising, so it would be swallowed by
    the empty-day tolerance).
    """
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-10-01.jsonl.gz").write_bytes(b"")
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    with pytest.raises(tpc.CensusError, match="ZERO bytes"):
        tpc.read_local_record_turns(tmp_path)


def test_a_zero_byte_PLAIN_day_is_TOLERATED_and_DISCLOSED_like_its_archive(
        tmp_path):
    """Review P1: an empty day is ONE state, not two.

    `reflect-hook.writeFallback` does `appendFileSync(filePath, ...)` — create
    AND write in ONE call — so a 0-byte day file has never held a record. And
    the seal compresses that very file to a 20-byte empty archive which the
    `.gz` arm TOLERATES. Refusing here made the identical provenance flip
    REFUSED -> tolerated after `DEFAULT_KEEP_DAYS`, i.e. the verdict an owner
    signs changed with the wall clock.

    Tolerated is safe because the POPULATION COMES FROM THE GRAPH (pinned by
    the non-vacuity test below), and it is DISCLOSED rather than silent.

    Mutation that goes RED here: restoring the `raise CensusError(...EMPTY...)`
    arm for a settled plain day.
    """
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))},
                  name="2026-11-01.jsonl")
    (tmp_path / "2026-10-30.jsonl").write_text("", encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert list(record["rows_by_key"]) == ["s1_t0"]
    # DISCLOSED, not a silent skip — this is the half that makes it honest.
    assert record["empty_days"] == ["2026-10-30.jsonl"]


def test_a_tolerated_empty_PLAIN_day_still_cannot_hide_a_graph_turn(tmp_path):
    """The non-vacuity of the plain-day tolerance: the population comes from
    the GRAPH, so an empty day whose turn WAS captured cannot certify it —
    that turn is `missing_in_record` and the census REFUSES."""
    _local_record(tmp_path, {"s2": _conversation(("user", "other"))},
                  name="2026-11-01.jsonl")
    (tmp_path / "2026-10-30.jsonl").write_text("", encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    report = tpc.census({"nodes": {"s1_t0": _graph_node("s1_t0", "s1",
                                                         content="[user] hi")}},
                        record, sample=5)
    assert report["verdict"] == "REFUSED"
    assert report["parity"]["failed_sessions"][0]["problems"][0]["problem"] \
        == "missing_in_record"
    assert report["dry_run"]["would_delete_turn_nodes"] == 0


def test_a_line_with_NO_conversation_key_is_DROPPED_and_still_fails_CLOSED(
        tmp_path):
    """Review P2: ABSENT is not EMPTY.

    The writer always emits `conversation`, so coercing a missing key to `[]`
    would make a LOST transcript read as `missing_in_record` — pointing the
    operator at the graph/backfill for a turn whose transcript had been dropped.
    It is NOT coerced. Nor does it refuse the whole leg: the filing leg
    DEAD-LETTERS `no_conversation` (`session_events_drain`), so this line never
    reached the graph and contributes no key. It is dropped and named, and the
    affected session reads as `missing_in_record` → REFUSED.

    Mutation that goes RED here: restoring `if conversation is None:
    conversation = []` (the `malformed_lines` disclosure disappears, so the
    first assert raises), or restoring the whole-leg `raise CensusError` (the
    two asserts below). Note what the coercion does NOT do: it cannot reach a
    false CLEAN. `CLEAN` is only ever assigned to sessions the GRAPH holds, and
    the graph holds `s1_t0` here, so a coerced-empty `s1` still reports
    `missing_in_record` -> REFUSED. The regression this pin catches is the LOSS
    OF THE DISCLOSURE — the operator is told nothing was dropped — not a
    passing verdict.
    """
    (tmp_path / "2026-11-02.jsonl").write_text(
        json.dumps({"session_id": "s1"}) + "\n"
        + json.dumps({"session_id": "s2",
                      "conversation": _conversation(("user", "hi"))}) + "\n",
        encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert record["malformed_lines"][0]["at"] == "2026-11-02.jsonl:1"
    assert "NO `conversation` key" in record["malformed_lines"][0]["error"]
    # ... and the drop is fail-CLOSED for the session it described. The SIBLING
    # session is still certifiable, which is the whole point: one lost
    # transcript no longer makes the leg unopenable.
    assert list(record["rows_by_key"]) == ["s2_t0"]
    report = tpc.census(
        {"nodes": {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi")}},
        record, sample=5)
    assert report["verdict"] == "REFUSED"
    assert report["parity"]["failed_sessions"][0]["problems"][0]["problem"] \
        == "missing_in_record"


def test_an_explicitly_EMPTY_conversation_is_a_real_session_with_no_turns(
        tmp_path):
    """The other half of the same distinction: `[]` is the writer saying this
    session held no turns, which is data, not loss."""
    (tmp_path / "2026-11-02.jsonl").write_text(
        json.dumps({"session_id": "s1", "conversation": []}) + "\n",
        encoding="utf-8")
    (tmp_path / "2026-11-01.jsonl").write_text(
        json.dumps({"session_id": "s2",
                    "conversation": _conversation(("user", "hi"))}) + "\n",
        encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert record["sessions"] == 2
    assert list(record["rows_by_key"]) == ["s2_t0"]


def test_the_NEWEST_plain_day_may_be_empty_while_a_writer_creates_it(
        tmp_path):
    """The newest file is the one a live writer is in the middle of creating
    (create → write window), and it is disclosed like any other empty day."""
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))},
                  name="2026-11-01.jsonl")
    (tmp_path / "2026-11-02.jsonl").write_text("", encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert list(record["rows_by_key"]) == ["s1_t0"]
    assert record["empty_days"] == ["2026-11-02.jsonl"]


def test_an_unlistable_surface_refuses_instead_of_reading_as_empty(
        tmp_path, monkeypatch):
    """`Path.glob` swallows scandir OSError; the leg must not."""
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    archive = tmp_path / "archive"
    archive.mkdir()
    real_iterdir = Path.iterdir

    def boom(self):
        if self.name == "archive":
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", boom)
    with pytest.raises(tpc.CensusError, match="could not be LISTED"):
        tpc.read_local_record_turns(tmp_path)


def test_a_torn_line_is_NOT_A_TAIL_outside_the_newest_plain_file(tmp_path):
    """The tail exception is the append-only tip, not every settled day.

    Review P1 changed the CONSEQUENCE (drop + disclose, not refuse-the-leg) but
    not this distinction: a settled day's unparsable line is corruption, so it
    lands in `unparsable_lines`, never in `torn_tail`.
    """
    old = tmp_path / "2026-11-01.jsonl"
    old.write_text(json.dumps({"session_id": "s1",
                               "conversation": _conversation(("user", "a"))})
                   + '\n{"session_id": "s2", "conversa', encoding="utf-8")
    _local_record(tmp_path, {"s3": _conversation(("user", "c"))})
    record = tpc.read_local_record_turns(tmp_path)
    assert record["torn_tail"] == []
    assert [b["at"] for b in record["unparsable_lines"]] == [
        "2026-11-01.jsonl:2"]


def test_a_torn_line_inside_a_sealed_archive_is_DROPPED_and_NAMED(tmp_path):
    """A `.gz` day is written whole and verified by the seal: never a tail.

    Dropped + disclosed (review P1), and the disclosed path is ROOT-RELATIVE so
    it is addressable — `archive/2026-10-01.jsonl.gz`, not a bare file name that
    also matches a root day (review P2).
    """
    import gzip
    archive = tmp_path / "archive"
    archive.mkdir()
    body = (json.dumps({"session_id": "s1",
                        "conversation": _conversation(("user", "a"))})
            + '\n{"session_id": "s2", "conversa')
    (archive / "2026-10-01.jsonl.gz").write_bytes(gzip.compress(body.encode()))
    _local_record(tmp_path, {"s3": _conversation(("user", "c"))})
    record = tpc.read_local_record_turns(tmp_path)
    assert record["torn_tail"] == []
    assert [b["at"] for b in record["unparsable_lines"]] == [
        "archive/2026-10-01.jsonl.gz:2"]


def test_a_torn_PLAIN_line_under_archive_is_corruption_not_a_tail(tmp_path):
    """The tail exemption is the LIVE directory only — and this is the pin
    that holds that conjunct (review P2).

    `plain` is filtered on BOTH `f.parent == root` and `not ...".gz"`, and the
    only archive-side torn-line pin uses a `.gz`, which the second conjunct
    excludes anyway — so deleting `f.parent == root` left all 94 tests green
    while a torn line in a SETTLED `archive/<day>.jsonl` became a tail.
    A plain file under `archive/` is not an in-flight append: the writer never
    touches it, so an unparsable line there is corruption.
    """
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-10-31.jsonl").write_text(
        json.dumps({"session_id": "s1",
                    "conversation": _conversation(("user", "a"))})
        + '\n{"session_id": "s2", "conversa', encoding="utf-8")
    # A newer plain day in the LIVE directory, so the newest-plain term alone
    # would have allowed a tail — only the parent filter can separate this.
    _local_record(tmp_path, {"s3": _conversation(("user", "c"))})
    record = tpc.read_local_record_turns(tmp_path)
    assert record["torn_tail"] == []
    assert [b["at"] for b in record["unparsable_lines"]] == [
        "archive/2026-10-31.jsonl:2"]


def test_a_same_day_plain_collision_is_disclosed_not_silently_dropped(tmp_path):
    """One queue key per day+shape: the displaced copy is NAMED (review P2).

    `found` is keyed on `(day-without-suffix, suffix)` with no directory
    component and `root` is scanned second, so `archive/<day>.jsonl` loses to
    the root's own file of the same day. The verdict stays fail-closed, but a
    narrowed surface the artifact does not name reads as full coverage — the
    class round 9 closed for the shape axis.
    """
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-11-02.jsonl").write_text(
        json.dumps({"session_id": "s_arch",
                    "conversation": _conversation(("user", "arch"))}) + "\n",
        encoding="utf-8")
    _local_record(tmp_path, {"s_root": _conversation(("user", "root"))})
    record = tpc.read_local_record_turns(tmp_path)
    assert record["files"] == ["2026-11-02.jsonl"]
    assert record["skipped_files"] == ["archive/2026-11-02.jsonl"]
    assert "s_root_t0" in record["rows_by_key"]
    assert "s_arch_t0" not in record["rows_by_key"]


def test_an_enriched_turn_refuses_because_no_record_can_restore_it(tmp_path):
    """#8488: `embeddingText` is an LLM rewrite the conversation cannot rebuild."""
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    record = tpc.read_local_record_turns(tmp_path)
    nodes = {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi",
                                  embeddingText="ctx: hi (rewritten)")}
    with pytest.raises(tpc.CensusError, match="embeddingText"):
        tpc.census({"nodes": nodes}, record, sample=5)


def test_both_graph_cyphers_SELECT_the_enriched_text(monkeypatch):
    """Cycle-4 P2: dropping `t.embeddingText` restored a false CLEAN silently."""
    assert "t.embeddingText" in tpc._GRAPH_EPISODIC_CYPHER
    assert "t.embeddingText" in tpc._GRAPH_EPISODIC_IDS_CYPHER


def test_the_graph_leg_carries_embedding_text_onto_the_node(monkeypatch):
    """…and the READ must actually put it on the node dict."""
    class _G:
        def query(self, cypher, params=None):
            is_count = "count(t)" in cypher
            class R:
                result_set = ([[1]] if is_count else
                              [_turn_row(1, "s1_t0", "s1",
                                         et="ctx rewrite of s1_t0")])
            return R()

    _patch_proj(monkeypatch, _G())
    graph = tpc.read_graph_turns("docker://x", None, "g")
    assert graph["nodes"]["s1_t0"]["embeddingText"] == "ctx rewrite of s1_t0"
    with pytest.raises(tpc.CensusError, match="does not compare that field"):
        tpc.census(graph, {"rows_by_key": {"s1_t0": _record_row("s1_t0")},
                           "non_turn_shaped": 0}, sample=5)


def test_a_whitespace_only_SEALED_day_holds_no_records_and_is_tolerated(
        tmp_path):
    """Cycle-4 P2 re-pinned to the corrected model: `raw` is `['']`-truthy, so
    a blank sealed day slipped the RECORD-level guard. It holds no records and
    its archive is complete, so it is tolerated and disclosed here — the
    zero-records record is caught by the ZERO-sessions guard instead."""
    import gzip
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-10-01.jsonl.gz").write_bytes(gzip.compress(b"\n"))
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    record = tpc.read_local_record_turns(tmp_path)
    assert record["empty_days"] == ["archive/2026-10-01.jsonl.gz"]
    assert list(record["rows_by_key"]) == ["s1_t0"]


def test_a_record_that_parses_ZERO_sessions_refuses(tmp_path):
    """The guard that makes the empty-day tolerance safe at the RECORD level:
    days may be tolerably empty, but a record with no session anywhere is not
    evidence of parity."""
    (tmp_path / "2026-11-02.jsonl").write_text("\n\n", encoding="utf-8")
    with pytest.raises(tpc.CensusError, match="ZERO sessions"):
        tpc.read_local_record_turns(tmp_path)


def test_a_record_of_ONLY_malformed_lines_refuses_NAMING_the_line_not_emptiness(
        tmp_path):
    """Review P3: the zero-session guard fires for TWO different states, and it
    said the wrong one for both.

    A record whose every line was DROPPED is not an empty record — the lines
    were read and discarded, so the record is DAMAGED and its transcripts are
    uncertifiable. The per-line `malformed :` disclosure cannot carry that
    news: the report object is built after the guard raises, so the refusal is
    the ONLY place the dropped lines can be named. Saying "parsed ZERO
    sessions … an empty record" over a damaged one tells the operator a healthy
    empty store is in front of them — on the gate #10017 waits on, that is
    misreport, not a safety margin.

    Mutation that goes RED here: restoring the single `parsed ZERO sessions`
    refusal for this state (both `malformed` asserts), or dropping the line
    from the message (the `at` assert).
    """
    # An explicit `[]` conversation is REAL data and is placed, so it must be a
    # malformed line of another class: no usable `session_id`.
    (tmp_path / "2026-11-02.jsonl").write_text(
        json.dumps({"conversation": [], "session_id": "   "}) + "\n",
        encoding="utf-8")
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_local_record_turns(tmp_path)
    message = str(exc.value)
    assert "DROPPED every line" in message
    assert "DAMAGED, not empty" in message
    assert "malformed 2026-11-02.jsonl:1" in message
    # …and it must NOT read as the empty-record refusal, which is a different
    # on-disk state with a different repair.
    assert "ZERO sessions" not in message
    assert "an empty record" not in message


def test_a_record_of_ONLY_unparsable_lines_refuses_NAMING_the_line_not_emptiness(
        tmp_path):
    """The unparsable half of the same guard (review P3).

    The line is placed in a SEALED day on purpose: the newest PLAIN file's last
    line is the legitimate torn tail (`torn_tail`, tolerated), so a plain file
    here would exercise the tail arm instead of this one. A `.gz` day has no
    tail exemption, which is exactly the corruption this arm names.

    Mutation that goes RED here: the single `parsed ZERO sessions` refusal, or
    a message that omits which line was dropped.
    """
    import gzip
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-10-01.jsonl.gz").write_bytes(
        gzip.compress(b'{"session_id": "s1", "conversation": [\n'))
    with pytest.raises(tpc.CensusError) as exc:
        tpc.read_local_record_turns(tmp_path)
    message = str(exc.value)
    assert "DROPPED every line" in message
    assert "DAMAGED, not empty" in message
    assert "unparsable archive/2026-10-01.jsonl.gz:1" in message
    assert "ZERO sessions" not in message
    assert "an empty record" not in message


# ── the four thin shape arms (review P3: none had a pin; then review P2 turned
#    them from whole-leg refusals into DISCLOSED DROPS — fail-closed by
#    construction, because the filing leg dead-letters every one of them) ─────

def test_a_line_that_is_not_a_JSON_OBJECT_is_DROPPED_not_crashed(tmp_path):
    """`not isinstance(entry, dict)` guarded an uncaught `AttributeError` from
    `entry.get`, which would escape `main`'s `except CensusError` as a traceback
    + exit 1 — indistinguishable from a tool bug. The guard now DROPS and names
    the line instead of refusing the leg; the crash must not come back.

    Mutation that goes RED here: deleting the `isinstance(entry, dict)` arm.
    """
    (tmp_path / "2026-11-02.jsonl").write_text(
        "[1, 2]\n"
        + json.dumps({"session_id": "s1",
                      "conversation": _conversation(("user", "hi"))}) + "\n",
        encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert "not a JSON object" in record["malformed_lines"][0]["error"]
    assert list(record["rows_by_key"]) == ["s1_t0"]


def test_a_line_with_NO_session_id_is_DROPPED(tmp_path):
    """A turn's key cannot be derived — and the filing leg dead-letters
    `no_session_id`, so the line never reached the graph and can hide no turn.
    """
    (tmp_path / "2026-11-02.jsonl").write_text(
        json.dumps({"conversation": []}) + "\n"
        + json.dumps({"session_id": "s1",
                      "conversation": _conversation(("user", "hi"))}) + "\n",
        encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert "no usable `session_id`" in record["malformed_lines"][0]["error"]
    assert record["sessions"] == 1


def test_a_WHITESPACE_ONLY_session_id_is_BLANK_not_a_session_named_spaces(
        tmp_path):
    """Review P2: truthiness is not the platform's rule.

    `session_events_drain` normalises
    `raw_sid if isinstance(raw_sid, str) and raw_sid.strip() else None` and then
    dead-letters the record, so `"   "` can never name a session in the graph.
    Plain truthiness ACCEPTED it, inventing a session no door can mint — its
    keys could never match a graph turn, so it was a silently uncomparable
    session rather than a refusal.

    Mutation that goes RED here: restoring `if not isinstance(sid, str) or not
    sid` (the `"   "` file is then read as one session named three spaces).
    """
    (tmp_path / "2026-11-02.jsonl").write_text(
        json.dumps({"session_id": "   ", "conversation": []}) + "\n"
        + json.dumps({"session_id": "s1",
                      "conversation": _conversation(("user", "hi"))}) + "\n",
        encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert record["sessions"] == 1
    assert list(record["rows_by_key"]) == ["s1_t0"]
    assert "no usable `session_id`" in record["malformed_lines"][0]["error"]


def test_a_conversation_that_is_not_a_LIST_is_DROPPED(tmp_path):
    """A dict where the array belongs is a malformed record, not a transcript."""
    (tmp_path / "2026-11-02.jsonl").write_text(
        json.dumps({"session_id": "s1", "conversation": {"role": "user"}})
        + "\n"
        + json.dumps({"session_id": "s2",
                      "conversation": _conversation(("user", "hi"))}) + "\n",
        encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert "not a list" in record["malformed_lines"][0]["error"]
    assert record["sessions"] == 1
    assert list(record["rows_by_key"]) == ["s2_t0"]


def test_expect_empty_reaches_a_verdict_when_the_local_record_is_absent(
        tmp_path, monkeypatch):
    """Cycle-5 P2: a drained graph must be confirmable on the local leg too."""
    from tortoise.projection import FalkorProjection

    class _Empty:
        def __init__(self, inner=None):
            self.g = _G()

        class _DB:
            @staticmethod
            def list_graphs():
                return ["g"]

        db = _DB()

    class _G:
        def query(self, cypher, params=None):
            class R:
                result_set = ([[0]] if "count(t)" in cypher else [])
            return R()

    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _Empty()))
    out = tmp_path / "out.json"
    code = tpc.main(["--uri", "docker://x", "--graph-name", "g",
                     "--local-record-dir", str(tmp_path / "absent"),
                     "--expect-empty", "--json", str(out)])
    assert code == 0               # the record leg was SKIPPED, not an absence
    import json as _json
    report = _json.loads(out.read_text())
    assert report["record"]["leg"] == "local-skipped"
    # ⛔ Review P3: a skipped leg compared NOTHING. It used to report all seven
    # fields compared (`declared_absent` was empty on the skipped dict) and
    # suppressed the NOT COMPARED line — an artifact claiming a comparison that
    # never happened, on the shape the branch below it polices.
    assert report["record"]["skipped"] is True
    assert report["compared_fields"] == []
    assert report["declared_absent"] == sorted(tpc._LOCAL_RECORD_DECLARED_ABSENT)


def test_expect_empty_does_NOT_skip_the_hosted_leg(tmp_path, monkeypatch):
    """Cycle-6 P1: an unreachable hosted DSN must not certify via the skip."""
    from tortoise.projection import FalkorProjection

    class _G:
        def query(self, cypher, params=None):
            class R:
                result_set = ([[0]] if "count(t)" in cypher else [])
            return R()

    class _Empty:
        def __init__(self, *a, **k):
            self.g = _G()

        class _DB:
            @staticmethod
            def list_graphs():
                return ["g"]

        db = _DB()

    monkeypatch.setattr(FalkorProjection, "from_uri",
                        classmethod(lambda cls, *a, **k: _Empty()))
    code = tpc.main(["--uri", "docker://x", "--graph-name", "g",
                     "--record-dsn", "postgresql://nowhere.invalid:1/x",
                     "--org-id", "o", "--graph-id", "g", "--expect-empty"])
    assert code == 3               # the record leg was DIALED, and it refused
    # …and without the flag the same empty graph still refuses.
    assert tpc.main(["--uri", "docker://x", "--graph-name", "g",
                     "--local-record-dir", str(tmp_path / "absent")]) == 3


def test_the_USAGE_block_documents_every_flag_the_parser_accepts():
    """Review P2: the module docstring IS this gate's definition, and it had
    drifted — it documented only the hosted flags, so the flag the owner ruling
    makes the AUTHORITY (`--local-record-dir`) was absent from the contract a
    reader decides on.

    Scoped to the USAGE block on purpose: checking `flag in __doc__` passed
    while a flag was cited in prose and missing from the contract (measured —
    deleting the USAGE entry left a mention in the PARITY DEFINITION, so the
    weaker assertion stayed green).

    Mutation that goes RED here: removing EVERY mention of
    `--local-record-dir` from the USAGE block (both the flag entry and the
    authority example). Removing only the entry stays green because the
    example above it still documents the flag — which is the true statement,
    and why the mutation is spelled out this way rather than as "delete the
    line".
    """
    import re
    source = Path(tpc.__file__).read_text(encoding="utf-8")
    doc = tpc.__doc__ or ""
    assert "USAGE" in doc, "the contract has no USAGE block — the scan is broken"
    usage = doc.split("USAGE", 1)[1]
    flags = sorted(set(re.findall(r'add_argument\(\s*"(--[a-z][a-z0-9-]*)"',
                                  source)))
    assert flags, "no parser flags found — the scan is broken, not the doc"
    undocumented = []
    for f in flags:
        # ⛔ A SUBSTRING TEST OVER THE WHOLE BLOCK IS VACUOUS (review P2).
        # `--graph-name` passed it on prose inside `--expect-empty`
        # ("a drained graph and a wrong `--graph-name` / `--db-path` are
        # indistinguishable") while having no flag ENTRY at all — meaning and
        # default undocumented. Scoping to a *flag position* closes it: a flag
        # counts on an ENTRY line (4-space indent, aliases such as
        # `--org-id/--graph-id` allowed) or in the SYNOPSIS (8-space indent),
        # never inside an entry's explanatory prose (deeply indented).
        entry = re.search(rf"^    \S*(?<![\w-]){re.escape(f)}(?![\w-])",
                          usage, re.M)
        # EXACTLY 8 spaces: `^        .*` would also match a deeply indented
        # continuation line, which is prose — that reopened the very hole this
        # pin exists to close (measured: renaming the `--graph-name` entry to
        # `--NOT-A-FLAG` still passed, on the "A wrong --graph-name is
        # indistinguishable" sentence inside the entry body).
        synopsis = re.search(rf"^ {{8}}[^ \n].*(?<![\w-]){re.escape(f)}(?![\w-])",
                             usage, re.M)
        if not (entry or synopsis):
            undocumented.append(f)
    assert not undocumented, (
        f"flags accepted but with no USAGE ENTRY of their own (a prose "
        f"mention does not count): {undocumented}")


def test_the_docstring_states_the_local_legs_DECLARED_ABSENT_fields():
    """The other half of the same drift: the artifact declares these absent, so
    the contract must name them — a reader must not expect them compared."""
    doc = tpc.__doc__ or ""
    for name in tpc._LOCAL_RECORD_DECLARED_ABSENT:
        assert name in doc, f"declared-absent field {name!r} is not in the doc"
    assert "DECLARED ABSENT" in doc or "declared_absent" in doc
    # The exact contradiction finding 2 named: the contract listed `kind`,
    # `status` and `createdAt` under COMPARED EXACTLY for BOTH legs while the
    # local leg declares all three absent. The qualification that resolves it
    # must be in the contract, not only in the code.
    assert "HOSTED leg ONLY" in doc, (
        "the contract must qualify which leg compares kind/status/createdAt")


def test_the_contract_states_that_an_ARMED_graph_holds_POST_cutover_turns():
    """#10627 review (P3): the contract claimed the opposite of what the writer
    does, on the clause a purge-scoping lane reasons from.

    The old sentence was "on an ARMED deployment the writer stops minting turn
    nodes (#10000), so turn nodes present in an armed target's graph are
    pre-cutover by construction". The writer does no such thing: `capture_session`
    reads each record write's OUTCOME and sends every turn the record did not
    take through `_graph_write_with_retry` ("the record did not take N of M
    turn(s) — writing them to the GRAPH so the transcript is not lost"). An
    armed graph therefore holds POST-cutover turns the record DECLINED — and
    they are `missing_in_record` by construction (backfill, never delete).

    The pin is anchored to the WRITER, not to prose: it reads `tortoise/sdk.py`
    and requires the fallback's own names to exist there, so the contract
    cannot be corrected into a claim about code that no longer exists either.

    Mutations that go RED here: restoring the old "pre-cutover by construction"
    sentence; deleting the `_unrecorded`/`_graph_write_with_retry` sentence.
    """
    import tortoise.sdk as sdk
    doc = tpc.__doc__ or ""
    sdk_src = Path(sdk.__file__).read_text(encoding="utf-8")
    # The code path the contract describes really exists.
    assert "_unrecorded" in sdk_src and "_graph_write_with_retry" in sdk_src, (
        "the writer no longer has the #10000 graph fallback this contract "
        "describes — the contract must be re-derived, not patched")
    assert "the record did not take" in sdk_src
    # …and the contract states it, by name, with the repair.
    assert "pre-cutover by construction" not in doc, (
        "the falsified premise is back: an armed graph holds post-cutover "
        "turns the record declined")
    assert "_unrecorded" in doc and "_graph_write_with_retry" in doc
    assert "missing_in_record" in doc.split("_graph_write_with_retry", 1)[1][:1200], (
        "the contract must say where a declined turn lands and that its repair "
        "is a backfill, never a delete")


def test_the_exit_code_table_names_BOTH_arms_of_exit_three():
    """#10627 review (P3): exit 3 was documented as ONE arm ("a side could not
    be READ"), and the operator's action table therefore pointed at a retry.

    `census()` raises `CensusError` on three arms where BOTH sides were read
    fine and only the COMPARISON was incomplete — an undeclinable
    `declared_absent` name, an invented one, and any `embeddingText`-bearing
    episodic node. An operator (or a monitor rule) keyed on the documented
    table retries store reachability forever on a state whose only repair is a
    code change to the comparison.

    Mutations that go RED here: restoring the one-arm row; restoring the
    one-arm `CensusError` docstring; dropping the "different repairs" warning.
    """
    doc = tpc.__doc__ or ""
    start = doc.find("\n    3  ")
    end = doc.find("THE NEGATIVE ARM")
    assert start != -1 and end != -1 and end > start, (
        "the exit-code table's row 3 is not where the scan expects it — the "
        "scan is broken, not the contract")
    row = " ".join(doc[start:end].split())
    assert "be READ" in row, "row 3 no longer names the unreadable-side arm"
    assert "COMPARISON could not be completed" in row, (
        "row 3 must name the uncompleted-comparison arm, or the operator "
        "retries a read that was never wrong")
    assert "DIFFERENT repairs" in row, (
        "the two arms have different repairs and the table must say so")
    # The exception's own contract must agree with the table it is raised under.
    err_doc = tpc.CensusError.__doc__ or ""
    # ⛔ ASSERT THE FRAMING, NOT ONLY THE WORDS (measured: a mutant that restored
    # the one-arm summary left READ/COMPARISON alive in the paragraph below it and
    # the weaker assertion stayed GREEN). The SUMMARY is the sentence a reader
    # takes away, so the claim is scoped to it.
    summary = err_doc.split("\n\n")[0]
    assert "Two arms" in err_doc, (
        "CensusError's contract no longer states that it has two arms — the "
        "one-arm framing is back")
    assert "a side could not be read" not in summary.lower(), (
        f"CensusError's summary still claims a single arm: {summary.strip()!r}")
    assert "READ" in err_doc and "COMPARISON" in err_doc, (
        "CensusError's docstring must name both arms by their repair")
    # …and the arms it describes are the arms the code raises.
    src = Path(tpc.__file__).read_text(encoding="utf-8")
    assert "declared " in src and "undeclinable" in src
    assert "embeddingText" in src


def test_the_CLI_prints_the_tolerated_empty_days(tmp_path, capsys, monkeypatch):
    """The tolerance must be VISIBLE on stdout, not just in the code path: an
    undisclosed skip reads exactly like a day that went missing.

    Mutation that goes RED here: deleting the `empty day  :` print loop.
    """
    import gzip
    archive = tmp_path / "session-events" / "archive"
    archive.mkdir(parents=True)
    (archive / "2026-10-01.jsonl.gz").write_bytes(gzip.compress(b""))
    _local_record(tmp_path / "session-events",
                  {"s1": _conversation(("user", "hi"))})
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi"),
                  }, "unidentifiable": []})
    code = tpc.main(["--uri", "docker://:@127.0.0.1:1/g",
                     "--local-record-dir", str(tmp_path / "session-events")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "empty day  : archive/2026-10-01.jsonl.gz" in out
    assert "NOT a parity pass" in out


# ── the exit-code contract for a DROPPED interior line (review P3) ───────────
#
# The PR body's arm table claimed an unparsable INTERIOR line is a `CensusError`
# (exit 3). It is not: the line is dropped and named, and the verdict is then
# decided by the POPULATION — `missing_in_record` -> exit 2 when the graph holds
# a turn for it, exit 0 when it does not. That is the same three-way contract
# `diff` publishes (0 = no differences, 1 = differences found, 2 = trouble):
# "the comparison found a difference" is NOT "the instrument could not read".
# Collapsing the two on the authority store pointed the operator at a leg
# refusal that does not exist there (the real record carries such lines) and hid
# that the run had in fact reached a verdict.
#
# Both halves are pinned at the CLI boundary on purpose — the exit code is the
# datum the artifact reports and the only thing an operator acts on.

def _record_with_one_DROPPED_interior_line(tmp_path) -> Path:
    """A SETTLED day holding `s2`'s merged line plus a readable `s3`."""
    root = tmp_path / "session-events"
    root.mkdir()
    _local_record(root, {"s1": _conversation(("user", "hi"))},
                  name="2026-11-01.jsonl")
    good = json.dumps({"session_id": "s3",
                       "conversation": _conversation(("user", "x"))})
    # `s2`'s record lost its newline, so it merged into `s3`'s line: the whole
    # line is unparsable and is dropped (never partially recovered).
    (root / "2026-11-02.jsonl").write_text(
        '{"session_id": "s2", "conversation": [{"role": "user",\n'
        + good + "\n", encoding="utf-8")
    return root


def test_a_DROPPED_interior_line_reaches_a_VERDICT_at_exit_TWO_never_exit_three(
        tmp_path, capsys, monkeypatch):
    """The graph holds a turn for the dropped session: REFUSED, exit 2.

    `CensusError`/exit 3 is the arm for "the record could not be read", and it
    is what this state used to produce — on the authority store, whose real
    record carries lost-newline merges, that made the gate unopenable and
    misreported a reached verdict as an unreadable leg.

    Mutation that goes RED here: restoring the whole-leg refusal for an interior
    unparsable line (a `CensusError` raised in `read_local_record_turns`) — the
    run then exits 3 with the refusal text, not 2 with a parity refusal.
    """
    root = _record_with_one_DROPPED_interior_line(tmp_path)
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {"s2_t0": _graph_node("s2_t0", "s2", content="[user] lost")},
        "unidentifiable": []})
    code = tpc.main(["--uri", "docker://:@127.0.0.1:1/g",
                     "--local-record-dir", str(root)])
    captured = capsys.readouterr()
    out, err = captured.out, captured.err
    assert code == 2, out + err
    assert code != 3, ("exit 3 means the record could not be READ — the line "
                       "was dropped and a verdict WAS reached")
    assert "unparsable : 2026-11-02.jsonl:1" in out
    assert "DROPPED, not recovered" in out
    assert "verdict    : REFUSED" in out


def test_a_DROPPED_interior_line_whose_turn_the_graph_never_held_is_exit_ZERO(
        tmp_path, capsys, monkeypatch):
    """The other half: nothing in the population needs the dropped line.

    The population comes from the GRAPH, so a line holding no graph turn cannot
    hide one — the run stays CLEAN (exit 0) and says what it dropped. A pin that
    only covered the refusal half would pass over a fix that just downgraded
    exit 3 to exit 2 unconditionally.
    """
    root = _record_with_one_DROPPED_interior_line(tmp_path)
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi"),
                  "s3_t0": _graph_node("s3_t0", "s3", content="[user] x")},
        "unidentifiable": []})
    code = tpc.main(["--uri", "docker://:@127.0.0.1:1/g",
                     "--local-record-dir", str(root)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "unparsable : 2026-11-02.jsonl:1" in out
    assert "NOT a parity pass" in out
    assert "verdict    : CLEAN" in out


def test_an_EMPTY_filed_window_is_not_a_SECOND_capture_window(tmp_path):
    """A `conversation` that windows to NOTHING is not a second window.

    Review P2. The filing leg windows the same list and DEAD-LETTERS the session
    as `no_conversation` when the window is empty
    (`tortoise/session_events_drain.py`, `turns = _window(...)` →
    `if not turns: return dead("no_conversation", ...)`), so an empty line filed
    no turn and can hide none. Counting it as an `occurrence` made ONE real
    window plus one empty line look like two capture windows of one index space,
    which popped the session from the positional key space and REFUSED it as
    `duplicate_window` — a false refusal, with a wrong cause and the wrong repair
    advice, on exactly the class the duplicate arm exists to keep open.

    Mutation that goes RED here: restoring the unconditional
    `occurrences[sid] = occurrences.get(sid, 0) + 1`.
    """
    # An explicit `[]`, and a conversation whose only turn is blank (the window
    # drops it) — the two shapes an empty FILED window comes in.
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))},
                  name="2026-11-01.jsonl")
    (tmp_path / "2026-11-02.jsonl").write_text(
        json.dumps({"session_id": "s1", "conversation": []}) + "\n"
        + json.dumps({"session_id": "s2",
                      "conversation": _conversation(("user", "   "))}) + "\n",
        encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert record["duplicate_window"] == [], record["duplicate_window"]
    # s1 keeps its one real window and is certifiable; s2 held no turn at all.
    assert list(record["rows_by_key"]) == ["s1_t0"]
    assert record["sessions"] == 2
    report = tpc.census(
        {"nodes": {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi")}},
        record, sample=5)
    assert report["verdict"] == "CLEAN", report["parity"]
    # …and the empty line does not overwrite the real window when it comes FIRST.
    (tmp_path / "2026-11-03.jsonl").write_text(
        json.dumps({"session_id": "s3", "conversation": []}) + "\n"
        + json.dumps({"session_id": "s3",
                      "conversation": _conversation(("user", "yo"))}) + "\n",
        encoding="utf-8")
    record = tpc.read_local_record_turns(tmp_path)
    assert record["duplicate_window"] == []
    assert record["rows_by_key"]["s3_t0"]["content"] == "[user] yo"


def test_a_record_SHAPED_path_that_is_not_a_file_is_DISCLOSED(tmp_path):
    """`is_file()` follows symlinks; a bare `continue` was a silent narrowing.

    Review P3. A dangling `<day>.jsonl` (a seal that unlinked its target, a
    half-finished rotate), a FIFO, or a directory named `<day>.jsonl` was dropped
    and named in NO list — so `files` read as full coverage of a surface the leg
    had quietly skipped, which is the one thing its own `skipped_files` contract
    forbids ("a narrowed surface the artifact does not name is a silent claim of
    coverage"). A plain directory (`archive/`) is not a narrowing and stays
    unnamed.

    Mutation that goes RED here: restoring the bare `if not entry.is_file():
    continue`.
    """
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))},
                  name="2026-11-01.jsonl")
    (tmp_path / "2026-11-02.jsonl").mkdir()          # a directory, record-shaped
    (tmp_path / "2026-11-03.jsonl").symlink_to(tmp_path / "gone.jsonl")
    record = tpc.read_local_record_turns(tmp_path)
    assert list(record["rows_by_key"]) == ["s1_t0"]
    # ⛔ ITS OWN LIST, not `skipped_files` (cycle-2 review P3): that one is the
    # day+shape COLLISION disclosure, and its stdout sentence states a collision
    # cause per entry — so sharing it printed "not a regular file … same
    # day+shape as a file the root store already supplies", a self-contradicting
    # line naming a repair (reconcile a displaced copy) that does not exist for
    # this class. The collision list must stay collision-only.
    assert record["skipped_files"] == [], record["skipped_files"]
    named = record["unreadable_paths"]
    assert any(s.startswith("2026-11-02.jsonl") for s in named), named
    assert any(s.startswith("2026-11-03.jsonl") for s in named), named
    assert all("not a regular file" in s for s in named), named


def test_a_record_SHAPED_path_that_is_not_a_file_gets_its_OWN_reason_on_stdout(
        tmp_path, capsys, monkeypatch):
    """The disclosure must name the cause that is TRUE for this class.

    Cycle-2 review P3: the printer appends the COLLISION cause to every
    `skipped_files` entry, so a not-a-regular-file path in that list printed a
    sentence that contradicted itself and pointed at the wrong repair.
    """
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))},
                  name="2026-11-01.jsonl")
    (tmp_path / "2026-11-02.jsonl").symlink_to(tmp_path / "gone.jsonl")
    monkeypatch.setattr(tpc, "read_graph_turns", lambda *a, **k: {
        "nodes": {"s1_t0": _graph_node("s1_t0", "s1", content="[user] hi")},
        "unidentifiable": []})
    code = tpc.main(["--uri", "docker://:@127.0.0.1:1/g",
                     "--local-record-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "unreadable : 2026-11-02.jsonl" in out
    assert "not a regular file" in out
    # The collision sentence must NOT be glued onto this class.
    assert "same day+shape" not in out


def test_a_record_shaped_path_is_not_reported_as_an_EMPTY_record(tmp_path):
    """EMPTY vs DAMAGED, on the path axis (cycle-2 review P3).

    A directory whose only entry is a record-shaped path that cannot be read is
    NOT an empty record: the previous message told the operator to check the
    directory/path, when the truth is an unreadable day file.

    Mutation that goes RED here: dropping the `unreadable_paths` clause from the
    `not files` refusal.
    """
    (tmp_path / "2026-11-01.jsonl").symlink_to(tmp_path / "gone.jsonl")
    with pytest.raises(tpc.CensusError) as excinfo:
        tpc.read_local_record_turns(tmp_path)
    message = str(excinfo.value)
    assert "EMPTY" in message
    assert "DAMAGED" in message, message
    assert "2026-11-01.jsonl" in message, message


def test_an_archive_symlink_that_does_not_resolve_REFUSES(tmp_path):
    """An unmounted archive volume is not an archive that holds nothing.

    Cycle-2 review P3: `exists()` follows links, so a dangling `archive/` was
    skipped in silence while the artifact reported the four shape x directory
    surfaces as covered. A GENUINELY missing `archive/` stays silent (the
    common fresh-directory state); only an unresolvable symlink refuses.

    Mutation that goes RED here: restoring the bare `if not scan_dir.exists():
    continue`.
    """
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    (tmp_path / "archive").symlink_to(tmp_path / "unmounted-volume")
    with pytest.raises(tpc.CensusError) as excinfo:
        tpc.read_local_record_turns(tmp_path)
    assert "does not resolve" in str(excinfo.value)
    # …and a genuinely absent archive directory is still normal.
    (tmp_path / "archive").unlink()
    assert list(tpc.read_local_record_turns(tmp_path)["rows_by_key"]) == ["s1_t0"]


def test_an_EMPTY_record_dsn_alongside_the_local_dir_is_REFUSED(tmp_path,
                                                               capsys):
    """Presence, not truthiness — on the MUTUAL-EXCLUSION arm too.

    Review P3. `if args.record_dsn:` let `--local-record-dir DIR --record-dsn ""`
    through, so the documented "an explicit flag combination is refused" guard
    did not exist for the exact spelling an unset variable produces. No wrong
    verdict followed (the local leg is the one named), but the guard was absent.

    Mutation that goes RED here: restoring `if args.record_dsn:`.
    """
    _local_record(tmp_path, {"s1": _conversation(("user", "hi"))})
    code = tpc.main(["--db-path", str(tmp_path / "g.db"),
                     "--local-record-dir", str(tmp_path),
                     "--record-dsn", ""])
    err = capsys.readouterr().err
    assert code == 1, err
    assert "mutually exclusive" in err

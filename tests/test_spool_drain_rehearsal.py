"""Pins for `tools/spool_drain_rehearsal.py` — the #14232 rehearsal instrument.

#14429 review (P2, "neither headline invariant has an assertion"): the instrument
shipped with no test anywhere in the tree, so its two load-bearing claims were
asserted by nothing at all —

* **recorded decision 1** — the source spool is READ ONLY, and the run fails if
  its fingerprint moved (`_source_unchanged` / `_source_fingerprint`);
* **recorded decision 2** — the drain is never pointed at production
  (`_gate_uri`).

`_gate_uri` reduced to `return uri` would have let a rehearsal POST the user's
only spool copy at production with every check in the tree green. These tests are
hermetic (`_gate_uri` / `_source_unchanged` / `_observations` / the summary
renderer are pure or file-local): no FalkorDB, no docker, no server.

They also pin the three behaviour fixes the same review raised, each of which is
observable without a drain:

* the stage reader must not fold `None` in when only the PROSE `stage=` form
  matches (it crashed with `TypeError` on the one body shape it was widened for);
* the replay row's delta is measured from the SETTLED graph, not from
  `graph_after_pass_1` (the settle poll sits between them, so the old row
  credited the replay with extraction that landed while waiting);
* `summary` re-renders the prose from the recorded `corpus-cost.json` and never
  re-measures the live spool.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import ci_selection as cs
from tools import spool_drain_rehearsal as rehearsal

# ── recorded decision 2: never point the drain at production ─────────────────


@pytest.mark.parametrize("uri", [
    "docker://:pw@127.0.0.1:6380/tortoise_test_lane",
    "docker://:pw@localhost:6379/tortoise_test_matrix",
    "bolt://localhost:16379",
    "bolt://127.0.0.1:6379/tortoise",
])
def test_gate_uri_accepts_loopback_targets(uri: str) -> None:
    """A private lane instance is exactly what the rehearsal is for."""
    assert rehearsal._gate_uri(uri) == uri


@pytest.mark.parametrize("uri", [
    # a real host that is not loopback — this is the production-drain direction
    "bolt://falkordb.prod:6379/g",
    "docker://:pw@10.0.0.7:6379/g",
    # the production API host, named explicitly by the recorded decision
    "bolt://api.premiselabs.co:6379/g",
    # the production graph, named explicitly
    "bolt://127.0.0.1:6379/live-v5",
    "docker://:pw@localhost:6379/LIVE-V5",
])
def test_gate_uri_refuses_non_loopback_and_production(uri: str) -> None:
    """⛔ The invariant that stops a rehearsal from writing to production.

    Mutation: `return uri` at the top of `_gate_uri` reds every case here.
    """
    with pytest.raises(SystemExit) as exc:
        rehearsal._gate_uri(uri)
    assert "refusing" in str(exc.value)


def test_gate_uri_refuses_an_unset_uri() -> None:
    """No target is a refusal, not a default to whatever `hosted_api` reads."""
    with pytest.raises(SystemExit) as exc:
        rehearsal._gate_uri(None)
    assert "TORTOISE_DB_URI" in str(exc.value)


# ── recorded decision 1: the source spool is never written ───────────────────


def _spool(root: Path, name: str = "a") -> Path:
    entries = root / "entries"
    entries.mkdir(parents=True, exist_ok=True)
    (entries / f"{name}.meta.json").write_text(
        json.dumps({"session_id": name, "attempts": 0}), encoding="utf-8")
    (entries / f"{name}.turns.jsonl").write_text(
        '{"role":"user","content":"hello"}\n', encoding="utf-8")
    return root


def test_source_fingerprint_is_stable_for_an_identical_copy(tmp_path: Path) -> None:
    """The invariant is only meaningful if an unchanged spool compares EQUAL."""
    src = _spool(tmp_path / "src")
    copy = tmp_path / "copy"
    rehearsal._copy_spool(src, copy)
    assert rehearsal._source_fingerprint(src) == rehearsal._source_fingerprint(copy)
    assert rehearsal._source_unchanged(
        rehearsal._source_fingerprint(src), rehearsal._source_fingerprint(copy))


@pytest.mark.parametrize("mutate", ["append", "rewrite-meta", "add-entry"])
def test_source_fingerprint_detects_every_kind_of_change(
        tmp_path: Path, mutate: str) -> None:
    """Transcript bytes, bookkeeping bytes and membership all move the digest."""
    before = rehearsal._source_fingerprint(_spool(tmp_path / "src"))
    src = _spool(tmp_path / "src")
    if mutate == "append":
        with (src / "entries" / "a.turns.jsonl").open("a", encoding="utf-8") as fh:
            fh.write('{"role":"assistant","content":"more"}\n')
    elif mutate == "rewrite-meta":
        (src / "entries" / "a.meta.json").write_text(
            json.dumps({"session_id": "a", "attempts": 3}), encoding="utf-8")
    else:
        (src / "entries" / "b.turns.jsonl").write_text("{}\n", encoding="utf-8")
        (src / "entries" / "b.meta.json").write_text("{}\n", encoding="utf-8")
    after = rehearsal._source_fingerprint(src)
    assert after != before
    assert rehearsal._source_unchanged(before, after) is False


@pytest.mark.parametrize("field, value", [
    ("entries_meta", 99), ("entries_turns", 99), ("files", 99),
    ("bytes", 99), ("aggregate_sha256", "0" * 64),
])
def test_source_unchanged_is_false_for_any_single_field_difference(
        tmp_path: Path, field: str, value: object) -> None:
    """The acceptance claim must survive a mutated spool, field by field.

    Mutation: `return True` in `_source_unchanged` reds every case here — and it
    is the exact mutant that would have made a broken run report the invariant
    held (the rehearsal's exit code keys on this value).
    """
    before = rehearsal._source_fingerprint(_spool(tmp_path / "src"))
    assert before["entries_meta"] == 1
    after = dict(before)
    after[field] = value
    assert rehearsal._source_unchanged(before, after) is False


# ── the observer reads BOTH refusal body shapes ──────────────────────────────


def _observations_for(tmp_path: Path, bodies: list[str]) -> dict:
    log = tmp_path / "requests.jsonl"
    rows = [{"t": 100.0 + i, "method": "POST", "path": "/v1/sessions",
             "status": 504, "dur_ms": 10000.0 + i, "body": body}
            for i, body in enumerate(bodies)]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return rehearsal._observations(log)


def test_observations_reads_the_prose_stage_form_without_crashing(
        tmp_path: Path) -> None:
    """`stage=unmarked spent_ms={}` — the shape production actually reported.

    Pre-fix this raised `TypeError: sequence item 0: expected str instance,
    NoneType found`: the comprehension collected `m.group(1)` unconditionally, so
    the prose alternative put `None` into the set. The comment above the regex
    names this exact shape as the reason both alternatives exist.
    """
    obs = _observations_for(tmp_path, ["stage=unmarked spent_ms={}"])
    assert obs["stage_strings"] == ["unmarked"], obs["stage_strings"]


def test_observations_reads_the_json_stage_form(tmp_path: Path) -> None:
    obs = _observations_for(
        tmp_path, ['{"stage":"capture_extract","stages_ms":{"capture_extract":10000}}'])
    assert obs["stage_strings"] == ["capture_extract"], obs["stage_strings"]


def test_observations_reads_both_forms_in_one_run(tmp_path: Path) -> None:
    """Both shapes can appear in one corpus; neither may erase the other."""
    obs = _observations_for(tmp_path, [
        '{"stage":"capture_extract"}', "stage=unmarked spent_ms={}"])
    assert obs["stage_strings"] == ["capture_extract", "unmarked"], obs["stage_strings"]


# ── the replay row must not blame the replay for the settle ──────────────────


def _graph(sessions: int, extracted: int, turns: int, *,
           capture_ok: int, in_flight: int) -> dict:
    return {"sessions": sessions, "turn_points": turns,
            "extracted_points": extracted, "capture_ok_true": capture_ok,
            "phase_in_flight": in_flight, "entities": 0}


def _result(*, mode: str = "head-mock",
            graph_1: dict | None = None, settled: dict | None = None,
            graph_2: dict | None = None) -> dict:
    """A `head-mock.json`-shaped result: the shape this PR actually shipped."""
    g1 = graph_1 or _graph(264, 526, 2083, capture_ok=261, in_flight=3)
    st = settled or _graph(264, 528, 2085, capture_ok=264, in_flight=0)
    return {
        "mode": mode,
        "corpus": {"copied_entries": 265},
        "invariant_source_untouched": True,
        "passes": [
            {"label": "drain-1", "exit": 0, "seconds": 197.9,
             "stdout": "", "stderr": ""},
            {"label": "drain-2 (replay)", "exit": 0, "seconds": 0.19,
             "stdout": "", "stderr": ""},
        ],
        "spool_after_pass_1": {
            "entries": 265, "filed_key_present": 265, "terminal_failure": 0,
            "terminal_reasons": {}, "attempts_min": 1, "attempts_p50": 1,
            "attempts_max": 1, "attempts_gt_0": 265, "backoff_waiting": 0,
            "backoff_max_seconds": 0, "backoff_p50_seconds": 0,
        },
        "spool_after_pass_2": {
            "entries": 265, "filed_key_present": 265, "terminal_failure": 0,
            "terminal_reasons": {}, "attempts_min": 1, "attempts_p50": 1,
            "attempts_max": 1, "attempts_gt_0": 265, "backoff_waiting": 0,
            "backoff_max_seconds": 0, "backoff_p50_seconds": 0,
        },
        "graph_after_pass_1": g1,
        "settle": {"reached_zero": True, "seconds": 2.0, **st},
        "graph_settled": st,
        "graph_after_pass_2": graph_2 or st,
        "observations": {
            "posts": 264, "status_counts": {"200": 264}, "posts_per_min": 80.6,
            "rate_limited": 0, "ack_ms_p50": 707.0, "ack_ms_p95": 916.0,
            "ack_ms_max": 1200.0, "stage_strings": [], "refusal_bodies": [],
        },
    }


_COST = {
    "encoder": "tiktoken:o200k_base", "sessions": 265,
    "tokens_per_session": {"n": 265, "min": 100, "p50": 1615, "p90": 2977,
                           "p99": 4759, "max": 5486, "sum": 423021,
                           "mean": 1596.3},
    "turns_per_session": {"n": 265, "min": 1, "p50": 7, "p90": 20, "p99": 40,
                          "max": 62, "sum": 2000, "mean": 7.5},
    "chars_per_session": {"n": 265, "min": 400, "p50": 6400, "p90": 12000,
                          "p99": 19000, "max": 22000, "sum": 1_700_000,
                          "mean": 6415.1},
    "top_decile_share_of_tokens": 0.217,
}


def test_replay_row_measures_its_delta_from_the_settled_graph() -> None:
    """The idempotency row must not report a delta the replay did not cause.

    In the shipped `head-mock.json` the settle poll runs between pass 1 and the
    replay, so `graph_after_pass_1` is 526 extracted points, `graph_settled` and
    `graph_after_pass_2` are both 528. Rendering `g2 − g1` printed `Δ 2` under
    the label "replay pass" — reading as duplicate extraction in the one row whose
    job is to prove the replay filed nothing.
    """
    lines = rehearsal._summarise([_result()], _COST).splitlines()
    replay = [line for line in lines if line.startswith("- replay pass")]
    assert len(replay) == 1, lines
    row = replay[0]
    assert "Δ 0 vs settled" in row, row
    assert "Δ 2" not in row, row


def test_replay_row_still_reports_a_real_replay_delta() -> None:
    """Non-vacuity: a replay that DID add sessions/points still shows it."""
    g1 = _graph(264, 526, 2083, capture_ok=261, in_flight=3)
    settled = _graph(264, 528, 2085, capture_ok=264, in_flight=0)
    g2 = _graph(270, 540, 2100, capture_ok=270, in_flight=0)
    row = next(line for line in
               rehearsal._summarise(
                   [_result(graph_1=g1, settled=settled, graph_2=g2)], _COST).splitlines()
               if line.startswith("- replay pass"))
    assert "Δ 6 vs settled" in row, row
    assert "Δ 12 vs settled" in row, row


# ── `summary` re-renders; it never re-measures ───────────────────────────────


def _artefacts(out: Path) -> dict[str, bytes]:
    out.mkdir(parents=True, exist_ok=True)
    for mode in ("head-keyless", "head-mock", "pre-14261", "backpressure"):
        (out / f"{mode}.json").write_text(
            json.dumps(_result(mode=mode), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    cost = out / "corpus-cost.json"
    cost.write_text(json.dumps(_COST, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}


def _live_spool(root: Path) -> Path:
    """A spool that has GROWN since the run the artefacts describe."""
    for name in ("a", "b", "c"):
        _spool(root, name)
    return root


def test_summary_verb_renders_from_the_recorded_cost(tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    """A re-render must never rewrite `corpus-cost.json` from the live spool.

    Commit `07b407e2` is titled "a `summary` verb that never re-measures", and
    `SUMMARY.md`'s own header says every number is *read from* the artefacts. The
    pre-fix branch called `_corpus_cost(args.spool_src)` and wrote the result, so
    re-rendering after the spool changed rewrote the committed cost to describe a
    corpus NO mode measured (measured: run on 265 entries, +30 spooled,
    `summary` -> SUMMARY.md says 295 beside `head-keyless.json`'s 265).
    """
    out = tmp_path / "evidence"
    before = _artefacts(out)
    spool = _live_spool(tmp_path / "live-spool")       # 3 entries, not 265

    assert rehearsal._run(["summary", "--out", str(out),
                           "--spool-src", str(spool)]) == 0

    assert (out / "corpus-cost.json").read_bytes() == before["corpus-cost.json"], (
        "`summary` re-measured the corpus and overwrote the recorded evidence")
    # The prose WAS re-rendered, from the recorded numbers.
    assert "sessions: 265" in capsys.readouterr().out
    assert (out / "SUMMARY.md").exists()
    assert "sessions: 265" in (out / "SUMMARY.md").read_text(encoding="utf-8")


def test_summary_verb_refuses_when_the_recorded_cost_is_absent(
        tmp_path: Path) -> None:
    """Absent evidence is a refusal, never a silent re-measure."""
    out = tmp_path / "evidence"
    _artefacts(out)
    (out / "corpus-cost.json").unlink()
    spool = _live_spool(tmp_path / "live-spool")
    with pytest.raises(SystemExit) as exc:
        rehearsal._run(["summary", "--out", str(out), "--spool-src", str(spool)])
    assert "corpus-cost.json" in str(exc.value)
    assert not (out / "corpus-cost.json").exists()


# ── the CI-selection half: a tool-only diff must RUN these pins ──────────────


def test_a_tool_only_diff_selects_this_pin() -> None:
    """The `CORE_ALSO` entry is LOAD-BEARING, so it is pinned here.

    `tools/` is a flat `NON_PYTHON_PREFIXES` entry and no `SOURCE_PATTERNS` row
    matches `tools/spool_drain_rehearsal.py`, so WITHOUT the entry a tool-only
    diff is filtered out BEFORE the "not matched -> core" fallback: `select()`
    returns no surface and this file never runs on the edit it exists to judge.
    Measured with the entry removed: `surfaces == []` and this file is absent from
    `test_files`.
    """
    selection = cs.select(
        ["tools/spool_drain_rehearsal.py"], "pull_request", cs.load_manifest())
    assert selection["surfaces"] == ["core"], selection
    assert "test_spool_drain_rehearsal.py" in selection["test_files"], (
        "the CORE_ALSO entry is missing: a tool-only diff would run no guard at "
        f"all — got {len(selection['test_files'])} files")

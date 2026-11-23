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

The next round's findings are pinned below as well: the committed `SUMMARY.md`
being the byte projection of the committed JSONs (it had gone stale), the
frontmatter parsing as YAML, a single-mode `run` keeping the other three modes and
a sliced run refusing to state a cost, every mode stating all four capture knobs,
`head-keyless` refusing to start when a provider key is configured (a knob cannot
pin that away), and `_org_id`'s 12-character tail.
"""
from __future__ import annotations

import inspect
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


# ── the committed artefact must BE the renderer's output ────────────────────
#
# ⛔ Measured across two review rounds: the shipped `SUMMARY.md` still printed
# `extracted points 528 (Δ 2)` under "replay pass" — the exact pre-fix row the
# replay-delta fix exists to remove — while the generator and its unit test were
# correct. The suite asserted the RENDERER against a synthetic result and NOTHING
# asserted the committed bytes, so the deliverable's own evidence drifted green.
# Same class (`generated-file drift`) as the frontmatter defect below: the durable
# control is a hermetic byte-equality pin, not a second reading.

EVIDENCE = rehearsal.REPO_ROOT / "docs" / "evidence" / "14232-spool-drain-rehearsal"


def _committed_artefacts() -> tuple[list[dict], dict, str]:
    results = [json.loads((EVIDENCE / f"{mode}.json").read_text(encoding="utf-8"))
               for mode in rehearsal.MODES]
    cost = json.loads((EVIDENCE / "corpus-cost.json").read_text(encoding="utf-8"))
    summary = (EVIDENCE / "SUMMARY.md").read_text(encoding="utf-8")
    return results, cost, summary


def test_the_committed_summary_is_the_render_of_the_committed_artefacts() -> None:
    """Byte equality, not a re-read: the ship artefact must equal the renderer."""
    results, cost, committed = _committed_artefacts()
    created = rehearsal._committed_created(EVIDENCE / "SUMMARY.md")
    rendered = rehearsal._summary_document(results, cost, created=created)
    assert rendered == committed, (
        "docs/evidence/14232-spool-drain-rehearsal/SUMMARY.md is NOT what the "
        "renderer produces from the artefacts beside it — re-render it with "
        "`tools/spool_drain_rehearsal.py summary --out <that dir>` and commit the "
        "result. No re-measure is needed; that is what the verb is for.")


def test_the_committed_summary_carries_no_pre_fix_replay_row() -> None:
    """The one line a reader would misread, stated as its own falsifier."""
    _, _, committed = _committed_artefacts()
    replay = [ln for ln in committed.splitlines() if ln.startswith("- replay pass")]
    assert replay, committed
    for row in replay:
        assert "vs settled" in row, row


def test_a_rerender_preserves_the_created_date(tmp_path: Path) -> None:
    """`summary` twice must be a no-op, or the committed bytes cannot be pinned.

    `created` is the one field the artefacts cannot supply (no `<mode>.json`
    carries a wall-clock instant), so it is read back from the file being
    replaced. Before that, every re-render restamped the document to today and no
    byte-equality pin could ever hold. ⛔ The date is set to a PAST value here on
    purpose: a same-day re-render would pass the weaker assertion even with the
    stamp broken, which is how this pin was caught being vacuous.
    """
    out = tmp_path / "evidence"
    _artefacts(out)
    results = [json.loads((out / f"{mode}.json").read_text(encoding="utf-8"))
               for mode in rehearsal.MODES]
    (out / "SUMMARY.md").write_text(
        rehearsal._summary_document(results, _COST, created="2020-01-01"),
        encoding="utf-8")
    spool = _live_spool(tmp_path / "live-spool")
    args = ["summary", "--out", str(out), "--spool-src", str(spool)]
    assert rehearsal._run(args) == 0
    first = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert "created: 2020-01-01" in first, first[:300]
    assert rehearsal._run(args) == 0
    assert (out / "SUMMARY.md").read_text(encoding="utf-8") == first


# ── the frontmatter must be YAML, not a quoted scalar followed by ", …" ─────


def test_the_generator_emits_parseable_frontmatter() -> None:
    """F2: every `run`/`summary` reproduced an unreadable frontmatter."""
    import yaml

    doc = rehearsal._summary_document(
        [_result(mode=mode) for mode in rehearsal.MODES], _COST)
    front, sep, _ = doc.partition("\n---\n")
    assert sep, doc[:80]
    assert front.startswith("---\n"), doc[:80]
    parsed = yaml.safe_load(front[4:])
    assert parsed["aboutObjects"].startswith("tortoise,"), parsed["aboutObjects"]


def test_the_committed_summary_frontmatter_parses_as_yaml() -> None:
    """The committed evidence must parse, not merely the generator's output."""
    import yaml

    _, _, committed = _committed_artefacts()
    front, sep, _ = committed.partition("\n---\n")
    assert sep, committed[:80]
    parsed = yaml.safe_load(front[4:])
    assert parsed["aboutObjects"].startswith("tortoise,"), parsed["aboutObjects"]


def test_the_frontmatter_pin_is_not_vacuous() -> None:
    """Non-vacuity: the shipped spelling MUST fail the reader above."""
    import yaml

    broken = ('aboutObjects: "#14232, #14261, #14342", tortoise, '
              "tools/spool_drain_rehearsal.py, tortoise/hosted_api.py")
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(broken)


# ── `run --mode X` may not delete the modes it did not re-measure ───────────


def _stub_one_mode(monkeypatch: pytest.MonkeyPatch,
                   digest: str | None = None) -> list[tuple[str, int | None]]:
    """Drive `_run`'s tail without a graph, a server or a drain.

    `digest` stamps the provenance a REAL mode records (`corpus.source_before`),
    so a caller grading the cost guard can make its stubs describe the spool they
    were handed rather than a corpus nobody drained.
    """
    calls: list[tuple[str, int | None]] = []

    def fake(mode: str, *, spool_src: Path, out_dir: Path, limit: int | None,
             replay: bool, nonce: str = "") -> dict:
        calls.append((mode, limit))
        result = _result(mode=mode)
        if digest is not None:
            result["corpus"] = {**result["corpus"],
                                "source_before": {"aggregate_sha256": digest}}
        (out_dir / f"{mode}.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    monkeypatch.setattr(rehearsal, "_one_mode", fake)
    return calls


def test_a_single_mode_run_keeps_the_other_modes_evidence(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F3: one `run --mode X` used to replace four sections with one.

    Measured before the fix: after `run --mode head-mock`, SUMMARY.md held
    `['head-mock']` only, while the other three `<mode>.json` files stayed on
    disk — prose and artefacts disagreed, and the evidence the runbook points the
    operator at was gone. `pre-14261` is used here because it is also the
    limited-or-not axis: its default `--limit 3` drains a SLICE, so the same run
    must not rewrite the recorded corpus cost either.
    """
    out = tmp_path / "evidence"
    before = _artefacts(out)
    _stub_one_mode(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:6379/tortoise_test")
    spool = _live_spool(tmp_path / "live-spool")

    assert rehearsal._run(["run", "--mode", "pre-14261", "--out", str(out),
                           "--spool-src", str(spool)]) == 0

    summary = (out / "SUMMARY.md").read_text(encoding="utf-8")
    for mode in rehearsal.MODES:
        assert f"## mode `{mode}`" in summary, (
            f"`run --mode pre-14261` deleted `{mode}`'s section: {summary[:400]}")
    assert (out / "corpus-cost.json").read_bytes() == before["corpus-cost.json"], (
        "a run that drained a SLICE rewrote the recorded corpus cost, so the "
        "artefacts now describe a corpus no mode drained")


def test_a_sliced_run_refuses_when_no_corpus_cost_is_recorded(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent cost on a sliced run is a refusal, never a live re-measure."""
    out = tmp_path / "evidence"
    _artefacts(out)
    (out / "corpus-cost.json").unlink()
    _stub_one_mode(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:6379/tortoise_test")
    spool = _live_spool(tmp_path / "live-spool")
    with pytest.raises(SystemExit) as exc:
        rehearsal._run(["run", "--mode", "backpressure", "--out", str(out),
                        "--spool-src", str(spool)])
    assert "corpus cost" in str(exc.value)
    assert not (out / "corpus-cost.json").exists()


def test_the_default_all_mode_writes_the_corpus_cost(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`all` drains the corpus, so `all` must be ABLE to state its cost.

    The predicate was `all(limit is None for ...)` while `all`'s own plan always
    contains the two SLICED modes (`pre-14261` 3, `backpressure` 4) — so the two
    full-corpus modes were outvoted by the two slices and `writes_cost` was False
    on EVERY `all` invocation. Measured before the fix, with `_one_mode` stubbed:
    a clean out dir ran all four modes and then exited with "records no corpus
    cost … run `all`" — the remedy it prints is the command that just failed —
    and wrote no `SUMMARY.md` either, so the runbook's documented regeneration
    command ("re-run `all`") could never refresh the evidence.
    """
    out = tmp_path / "evidence"
    out.mkdir()
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:6379/tortoise_test")
    spool = _live_spool(tmp_path / "live-spool")          # 3 entries, not 265
    calls = _stub_one_mode(
        monkeypatch, digest=rehearsal._source_fingerprint(spool)["aggregate_sha256"])

    assert rehearsal._run(["all", "--out", str(out),
                           "--spool-src", str(spool)]) == 0, (
        "the DEFAULT `all` run exited non-zero: it drained all four modes and then "
        "refused to state a corpus cost")

    # Non-vacuity: the run really did drive every mode (so the cost is not being
    # written by a path that skipped the drains it claims to describe).
    assert [m for m, _ in calls] == list(rehearsal.MODES), calls
    assert (out / "corpus-cost.json").exists(), (
        "`all` drained the whole corpus and still wrote no corpus cost")
    cost = json.loads((out / "corpus-cost.json").read_text(encoding="utf-8"))
    assert cost["sessions"] == 3, (
        f"the cost describes a different corpus than the one drained: {cost}")
    assert (out / "SUMMARY.md").exists(), (
        "`all` wrote no SUMMARY.md — the evidence cannot be regenerated by the "
        "command the runbook names")


def test_a_cost_describing_a_corpus_no_mode_drained_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A spool that grows mid-run must not be costed as if the modes drained it.

    The cost is taken from the LIVE spool after every mode ran, while each mode
    copied the corpus at its own start. A capture box is writing continuously, so
    `all` on a live spool can price a corpus NO mode drained — printed beside each
    mode's own `corpus copied: N` with nothing red. The guard compares each
    full-corpus mode's recorded `source_before` digest with the live one.
    """
    out = tmp_path / "evidence"
    out.mkdir()
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:6379/tortoise_test")
    spool = _live_spool(tmp_path / "live-spool")
    # A digest that is deliberately NOT this spool's: the modes copied something
    # the live spool no longer is (an entry landed while they ran).
    _stub_one_mode(monkeypatch, digest="0" * 64)

    with pytest.raises(SystemExit) as exc:
        rehearsal._run(["all", "--out", str(out), "--spool-src", str(spool)])

    assert "changed while the run was in flight" in str(exc.value), exc.value
    assert not (out / "corpus-cost.json").exists(), (
        "a cost that describes a corpus no mode drained was written anyway")


# ── every mode states its capture seams, so none inherits the operator's ────


def test_every_mode_states_all_four_capture_knobs() -> None:
    """F5: an unset knob is the operator's shell, not the default.

    All three are read at module import (or per call) inside `hosted_api`, so a
    child process cannot undo an inherited value. Measured: ambient
    `LLM_MOCK=1` made `head-keyless` mock-extract, ambient `IN_FLIGHT=1` put
    `head-mock` on a 1-slot ceiling with `capture_capacity_saturated` 429s, and
    ambient `EXTRACTOR=m2` left `pre-14261`'s v2 seam inert.
    """
    assert set(rehearsal.MODES) == {"head-keyless", "head-mock", "pre-14261",
                                    "backpressure"}
    for mode in rehearsal.MODES:
        knobs = rehearsal._mode_knobs(mode)
        assert set(knobs) == {"TORTOISE_SESSION_LLM_MOCK",
                              "TORTOISE_CAPTURE_IN_FLIGHT",
                              "TORTOISE_SESSION_EXTRACTOR",
                              "RATE_LIMIT_DISABLED"}, mode
        assert knobs["TORTOISE_SESSION_LLM_MOCK"] == (
            "0" if mode == "head-keyless" else "1"), mode
        assert knobs["TORTOISE_CAPTURE_IN_FLIGHT"] == (
            "1" if mode == "backpressure" else "8"), mode
        assert knobs["TORTOISE_SESSION_EXTRACTOR"] == "v2", mode
        # EVERY mode measures the 100/min key budget, so the limiter must be ON.
        # `hosted_api.RateLimitMiddleware.__init__` sets `_disabled` from
        # `RATE_LIMIT_DISABLED == "1"` (`hosted_api.py:5331`), and
        # `tests/conftest.py:26` sets that by default — inherited, it erases the
        # 50-entry park this PR exists to report.
        assert knobs["RATE_LIMIT_DISABLED"] == "0", mode
    # Non-vacuity: the two modes that must NOT get the rehearsal's aggressive
    # value are the ones an ambient environment polluted.
    assert rehearsal._mode_knobs("head-keyless")["TORTOISE_SESSION_LLM_MOCK"] == "0"
    assert rehearsal._mode_knobs("head-mock")["TORTOISE_CAPTURE_IN_FLIGHT"] == "8"
    # The limiter pin is not a mode-specific value: an ambient `1` (the value the
    # repo's own conftest exports) must be overridden for EVERY mode, not just one.
    for mode in rehearsal.MODES:
        assert rehearsal._mode_knobs(mode)["RATE_LIMIT_DISABLED"] != "1", mode


def test_the_knobs_are_APPLIED_before_the_app_is_imported() -> None:
    """The pin above asserts the dict; this asserts it is USED, in the right order.

    Measured on a scratch copy: deleting `os.environ.update(_mode_knobs(...))`
    from `_serve` left the whole suite green, and so did moving it AFTER
    `import tortoise.hosted_api` — the exact regression the module-import comment
    in `_serve` warns about (`_CAPTURE_MAX_IN_FLIGHT` is bound at import, so a
    late set silently grades the default). `capture_env` cannot catch it either:
    it records `_mode_knobs(mode)`, the same pure function the other test
    asserts, not the child's effective environment.
    """
    lines = inspect.getsource(rehearsal._serve).splitlines()
    applied = next(i for i, ln in enumerate(lines)
                   if ln.strip().startswith("os.environ.update(_mode_knobs("))
    imported = next(i for i, ln in enumerate(lines)
                    if ln.strip().startswith("import tortoise.hosted_api"))
    assert applied < imported, (
        "`_serve` applies the mode's knobs after importing `hosted_api`, where the "
        "module-level reads have already happened — the mode then grades the "
        "operator's environment, not its own label")


def test_head_keyless_refuses_to_start_when_a_provider_key_is_configured() -> None:
    """The FOURTH consequence of ambient pollution: no knob can pin it away.

    `TORTOISE_SESSION_LLM_MOCK=0` says "do not mock"; it does not say "there is no
    provider". With a provider key in the environment the drain runs REAL
    extraction (real spend) under a label whose whole claim is "no extraction
    provider", and `head-keyless.json` stops reproducing — so the child must stop
    rather than produce a measurement of a different mode.

    `hosted_api._llm_provider_available()` is the product's one reading of that
    question; the refusal takes its answer, so this pin needs no provider key, no
    app import and no server.
    """
    with pytest.raises(SystemExit) as exc:
        rehearsal._assert_keyless(True)
    assert "head-keyless requires NO extraction provider" in str(exc.value)
    assert "head-mock" in str(exc.value), (
        "the refusal must name the remedy, not just the condition")
    # Non-vacuity: the keyless box the mode exists for is NOT refused.
    rehearsal._assert_keyless(False)
    # ...and the refusal is WIRED: a helper nothing calls refuses nothing. A
    # mutant that deletes the call from `_serve` leaves this line red instead of
    # leaving a `head-keyless` that happily extracts on a keyed box.
    import inspect

    assert "_assert_keyless(ha._llm_provider_available())" in inspect.getsource(
        rehearsal._serve), "_serve no longer asserts the keyless label"


# ── the tenant id must always be a UUID-shaped tail ─────────────────────────


def test_org_id_tail_is_twelve_characters_where_decimal_is_not() -> None:
    """F6: `:012d` overflows 40 bits for ~9 % of runs; hex cannot.

    `n` is uniform on `[0, 2**40-1]` and `2**40 - 10**12 = 99 511 627 776`, so
    8.99 % of `(mode, nonce)` pairs need a THIRTEENTH decimal digit — and a `:012`
    minimum width pads, it never truncates. Measured over 20 000 pairs: 1 798 bad
    tails (e.g. `head-mock/nonce15 -> …-8000-1021145458251`).
    """
    import hashlib
    import re

    shape = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-8[0-9a-f]{3}-[0-9a-f]{12}$")
    overflowing = 0
    for i in range(2000):
        mode, nonce = "head-mock", f"nonce{i}"
        org = rehearsal._org_id(mode, nonce)
        assert shape.match(org), (mode, nonce, org)
        n = int(hashlib.sha256(f"{mode}:{nonce}".encode()).hexdigest()[:10], 16)
        if len(f"{n:012d}") > 12:
            overflowing += 1
    assert overflowing, (
        "no swept nonce reaches the decimal overflow, so this pin cannot show "
        "that the hex field is what fixes the shape")

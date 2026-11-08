"""#1779 — the cross-question haystack-overlap census tests.

Hermetic except the ``@slow`` pin: no DB, no network, no LLM, no dataset in
CI. The synthetic cases pin the census's ARITHMETIC and both arms of the
granularity verdict (a repeated session with the SAME date must read
``realizable`` — otherwise "prompt reuse is 0" would be unfalsifiable); the
``@slow`` case re-derives the pinned ``CENSUS`` from the cached real S split.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import dataset as lme_dataset  # noqa: E402, RUF100
from tools.longmem_eval.haystack_overlap import (  # noqa: E402, RUF100
    CENSUS,
    census_overlap,
    main,
)
from tools.longmem_eval.ingest import (  # noqa: E402, RUF100
    _session_chunks,
    _turn_text,
)


def _session(text: str, role: str = "user") -> list[dict]:
    return [{"role": role, "content": text}]


def _instance(qid: str, sessions: list[list[dict]], dates: list[str],
              ids: list[str] | None = None) -> dict:
    return {
        "question_id": qid,
        "haystack_sessions": sessions,
        "haystack_session_ids": ids if ids is not None else [
            f"{qid}-s{i}" for i in range(len(sessions))],
        "haystack_dates": dates,
    }


def test_identical_content_different_dates_is_not_prompt_reuse():
    """The real dataset's shape: same session text, re-dated per question.

    The S1/S2/S4 prompt carries the session date (`extractor_v2._date_anchor`),
    so two occurrences render DIFFERENT prompts — the response-cache avenue is
    measured shut rather than assumed shut.
    """
    instances = [
        _instance("q1", [_session("I moved to Lisbon")], ["2023-01-01"]),
        _instance("q2", [_session("I moved to Lisbon")], ["2023-02-02"]),
    ]
    census = census_overlap(instances)
    s = census["sessions"]
    assert s["slots"] == 2
    assert s["distinct_contents"] == 1          # same rendered transcript
    assert s["duplicate_content_slots"] == 1
    assert s["repeated_pairs"] == 1
    assert s["same_date_pairs"] == 0            # ← the one that closes the lever
    assert s["distinct_content_date"] == 2
    assert s["prompt_reuse_factor"] == 1.0
    # the date population is produced, not asserted in prose: the verdict above
    # would be an ARTEFACT if every slot carried a unique date
    assert census["dates"] == {"slots": 2, "distinct": 2, "reuse_factor": 1.0}
    for name in ("session_level_extraction_reuse", "prompt_level_response_cache"):
        row = census["decision"][name]
        assert row["realizable"] is False
        # the refusal NAMES the measurement, not a convention
        assert "0 of 1 repeated pairs share a session date" in row["blocked_by"]


def test_identical_content_and_date_is_prompt_reuse():
    """Falsifier for the case above: identity IS reachable — when the date
    matches, the avenue must read realizable. Without this arm the "0 reuse"
    verdict would be an assumption no input could contradict."""
    instances = [
        _instance("q1", [_session("I moved to Lisbon")], ["2023-01-01"]),
        _instance("q2", [_session("I moved to Lisbon")], ["2023-01-01"]),
    ]
    census = census_overlap(instances)
    s = census["sessions"]
    assert s["same_date_pairs"] == 1
    assert s["distinct_content_date"] == 1
    assert s["prompt_reuse_factor"] == 2.0
    # 2 slots over 1 distinct date — the readout fires in the OTHER arm too, so
    # a hard-coded 1.0 would be caught
    assert census["dates"] == {"slots": 2, "distinct": 1, "reuse_factor": 2.0}
    for name in ("session_level_extraction_reuse", "prompt_level_response_cache"):
        row = census["decision"][name]
        assert row["realizable"] is True
        assert row["blocked_by"] is None


def test_encode_units_use_the_product_renderers():
    """The census counts the text the ingest EMBEDS — turn Points
    (`_turn_text`) plus raw chunks (`_session_chunks`) — not a re-rendering.

    Four turns at `chunk_turns=2` give TWO chunk windows, so `distinct == slots`
    is reachable: a mutant that renders both windows from one text reds it
    (review finding, PR #11577 — one window made that assertion `1 == 1`).
    """
    session = [{"role": "user", "content": f"turn {i}"} for i in range(4)]
    instances = [_instance("q1", [session], ["2023-01-01"])]

    census = census_overlap(instances, chunk_turns=2)
    e = census["encode_units"]
    assert e["turn"]["slots"] == 4
    assert e["chunk"]["slots"] == len(_session_chunks(session, 2)) == 2
    assert e["total"]["slots"] == 4 + 2
    # a chunk renders `_session_transcript(window)` while its turn Points render
    # `_turn_text` — two DISJOINT string populations, so a chunk unit can never
    # be served from a turn unit's cache entry. `distinct` per leg summing to
    # `distinct` for the total is the numeric witness for that disjointness,
    # and `distinct == slots` per leg is the witness that no two units collide.
    assert e["chunk"]["distinct"] == e["chunk"]["slots"] == 2
    assert e["turn"]["distinct"] == e["turn"]["slots"] == 4
    assert (e["turn"]["distinct"] + e["chunk"]["distinct"]
            == e["total"]["distinct"])
    assert e["total"]["chunk_share_of_units"] == round(2 / 6, 4)


def test_turn_text_is_the_ingest_rendering():
    """`_turn_text` is the FORMAT of the turn Point's text."""
    assert _turn_text("user", "hello") == "[user] hello"


def test_both_ingest_legs_CALL_turn_text_and_keep_no_inline_twin():
    """The SHARING, not just the format — the half a format pin cannot see.

    Reverting `ingest.py` / `ingest_v2.py` to their inline `f"[{role}] {content}"`
    twins is byte-identical output, so every other test in this file stays
    green while the census could measure a rendering the ingest no longer
    writes (review finding, PR #11577 — the agent-infra#3442 rule: a guard must
    red on the edit it claims to prevent). Mechanical, like the repo's other
    enumeration ratchets: each leg must CALL `_turn_text`, and may carry at most
    the ONE `f"[{role}]` occurrence that is the definition's own body.
    """
    root = Path(__file__).resolve().parent.parent.parent / "tools" / "longmem_eval"
    # (file, `_turn_text` DEFINITIONS, `_turn_text` CALL SITES)
    for name, defs, calls in (("ingest.py", 1, 1), ("ingest_v2.py", 0, 1)):
        source = (root / name).read_text(encoding="utf-8")
        assert source.count("_turn_text(") >= defs + calls, (
            f"{name} no longer renders its turn Points via _turn_text")
        assert source.count('f"[{role}]') == defs, (
            f"{name} carries an inline turn rendering — that is a second "
            "definition of an encode-cache key, and it renders byte-identically, "
            "so nothing else would notice")


def test_a_none_date_is_the_same_prompt_as_an_absent_date():
    """The identity function is the PRODUCT's: `_date_anchor`
    (`extractor_v2.py:1466-1467`) renders `""` for any falsy date, so `None`, `""`
    and an absent entry are the SAME (undated) prompt — `str(date)` would read
    `"None"` as a date the product never renders (review finding, PR #11577).
    """
    instances = [
        _instance("q1", [_session("undated")], [None]),
        _instance("q2", [_session("undated")], [""]),
        _instance("q3", [_session("undated")], []),   # no date list at all
    ]
    census = census_overlap(instances)
    s = census["sessions"]
    assert s["distinct_contents"] == 1
    assert s["distinct_content_date"] == 1          # None == "" == absent
    assert s["prompt_reuse_factor"] == 3.0
    assert s["same_date_pairs"] == 3
    assert census["decision"]["prompt_level_response_cache"]["realizable"] is True


def test_chunk_turns_MUST_be_honoured_not_merely_echoed():
    """The window width the census MEASURES, not the one it reports.

    The previous fixture was a 2-turn session with `chunk_turns=2`, where any
    window >= 2 yields exactly one chunk — so "honours the argument" and
    "ignores it and uses the default" were indistinguishable, and a mutant
    threading `DEFAULT_CHUNK_TURNS` instead of `chunk_turns` survived every
    test (review finding, PR #11577). The echoed `census["chunk_turns"]` is the
    ARGUMENT, so it cannot witness this either: only the slot count can.
    """
    session = [{"role": "user", "content": f"turn {i}"} for i in range(3)]
    instances = [_instance("q1", [session], ["2023-01-01"])]

    per_turn = census_overlap(instances, chunk_turns=1)["encode_units"]["chunk"]
    whole = census_overlap(instances, chunk_turns=3)["encode_units"]["chunk"]
    assert per_turn["slots"] == 3      # 3 one-turn windows
    assert whole["slots"] == 1         # one window covering all 3 turns
    assert per_turn["slots"] != whole["slots"]


def test_cli_chunk_turns_reaches_the_census(tmp_path, capsys):
    """And the FLAG reaches the measurement (the same class the review found on
    `--download`): `main(["--chunk-turns", "1"])` must change the chunk leg."""
    data = tmp_path / "mini.json"
    data.write_text(json.dumps([_instance(
        "q1",
        [[{"role": "user", "content": f"turn {i}"} for i in range(3)]],
        ["d1"])]), encoding="utf-8")

    assert main(["--data", str(data), "--chunk-turns", "1", "--json"]) == 0
    per_turn = json.loads(capsys.readouterr().out)
    assert per_turn["chunk_turns"] == 1
    assert per_turn["encode_units"]["chunk"]["slots"] == 3

    assert main(["--data", str(data), "--chunk-turns", "3", "--json"]) == 0
    whole = json.loads(capsys.readouterr().out)
    assert whole["chunk_turns"] == 3
    assert whole["encode_units"]["chunk"]["slots"] == 1


def test_hits_are_counted_across_questions_not_within_one():
    """A hit is a REPEAT: one occurrence is never its own hit, and a
    cross-question repeat is counted once per extra occurrence."""
    shared = _session("shared turn")
    instances = [
        _instance("q1", [shared, _session("only q1")], ["d1", "d2"]),
        _instance("q2", [shared], ["d3"]),
    ]
    census = census_overlap(instances)
    e = census["encode_units"]["turn"]
    assert e["slots"] == 3
    assert e["distinct"] == 2
    assert e["hit_slots"] == 1
    assert e["reuse_factor"] == 1.5
    assert census["sessions"]["questions_with_repeated_content"] == 2


def test_within_question_duplicate_is_not_a_cross_question_hit():
    instances = [_instance("q1", [_session("ditto"), _session("ditto")],
                           ["d1", "d2"])]
    census = census_overlap(instances)
    assert census["sessions"]["within_question_duplicate_slots"] == 1
    # a within-question repeat IS a repeat (the field counts both kinds); the
    # kind is named separately by `within_question_duplicate_slots`
    assert census["sessions"]["questions_with_repeated_content"] == 1
    # a same-content pair with different dates still renders two prompts
    assert census["sessions"]["same_date_pairs"] == 0


def test_misaligned_ids_and_dates_are_data_not_errors():
    """A short id/date list is a datum about the instance handed to the
    reader — the census records it (blank identity) instead of refusing."""
    instances = [{
        "question_id": "q1",
        "haystack_sessions": [_session("x"), _session("y")],
        "haystack_session_ids": ["only-one"],
        # no "haystack_dates" at all
    }]
    census = census_overlap(instances)
    assert census["sessions"]["slots"] == 2
    # both undated → same prompt identity for the two distinct transcripts
    assert census["sessions"]["distinct_content_date"] == 2
    # an entirely empty population is not a division error
    empty = census_overlap([])
    assert empty["sessions"]["slots"] == 0
    assert empty["encode_units"]["total"]["reuse_factor"] == 0.0
    assert empty["decision"]["encode_cache_text_keyed"]["realizable"] is False


def test_chunk_turns_must_be_positive():
    with pytest.raises(ValueError):
        census_overlap([_instance("q1", [_session("x")], ["d"])], chunk_turns=0)


def test_cli_json_roundtrip(tmp_path, capsys):
    """The CLI is a producer, not a second implementation: its --json output IS
    `census_overlap`'s dict (the docs quote this payload)."""
    data = tmp_path / "mini.json"
    data.write_text(json.dumps([
        _instance("q1", [_session("a")], ["d1"]),
        _instance("q2", [_session("a")], ["d2"]),
    ]), encoding="utf-8")
    out = tmp_path / "census.json"
    assert main(["--data", str(data), "--json", "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(out.read_text(encoding="utf-8"))
    assert printed["sessions"]["same_date_pairs"] == 0
    assert printed["sessions"]["distinct_contents"] == 1


def test_cli_passes_download_through(tmp_path, monkeypatch, capsys):
    """`--split m` is only reachable WITH `--download` (the census refuses to
    pull hundreds of MB implicitly), so the flag must actually reach the loader
    — the research doc names this invocation as the way to answer the M-split
    question (review finding, PR #11577)."""
    seen: dict = {}

    def _fake_load(**kwargs):
        seen.update(kwargs)
        return [_instance("q1", [_session("a")], ["d1"])]

    monkeypatch.setattr(lme_dataset, "load_dataset", _fake_load)
    assert main(["--split", "m", "--download", "--json"]) == 0
    assert seen["split"] == "m"
    assert seen["download"] is True
    capsys.readouterr()
    # and the DEFAULT is still no download: a census must never pull implicitly
    assert main(["--json"]) == 0
    assert seen["download"] is False
    capsys.readouterr()


def test_the_research_doc_publishes_this_files_REAL_test_count():
    """§5 of the research note states this suite's size, so the size is DERIVED.

    Two fix rounds published a stale count after adding tests ("10 tests",
    then 13 while the file collected 15) — the same "a published number with no
    instrument standing behind it" class this module exists to retire, and a
    number no test could red. So the count is parsed from THIS file's AST (not
    from collection, which depends on the pytest invocation) and compared to
    the sentence §5 publishes: a new test without a §5 update reds here.
    """
    doc = (Path(__file__).resolve().parents[2]
           / "docs/research/2026-11-08-1779-eval-session-dedup.md")
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    tests = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    slow = [n for n in tests
            if any(isinstance(d, ast.Attribute) and d.attr == "slow"
                   for d in n.decorator_list)]
    assert len(slow) == 1, (
        f"expected exactly one @pytest.mark.slow (the real-split pin), found "
        f"{len(slow)} — §5 says the count is `N hermetic + the real-split pin`")
    published = f"{len(tests)} tests: {len(tests) - len(slow)} hermetic"
    assert published in doc.read_text(encoding="utf-8"), (
        f"§5 of {doc.name} must publish `{published}` (this file defines "
        f"{len(tests)} tests, {len(slow)} of them @slow) — the count is stale")


@pytest.mark.slow
def test_census_on_real_s_split():
    """The pinned CENSUS re-derived from the cached real S split.

    Skipped when the dataset is not cached (CI has no dataset). A dataset
    refresh that changes the overlap must RED this pin — the numbers are
    quoted in ``tools/longmem_eval/README.md`` and the module docstrings, and a
    silently re-based reuse factor is the "5-10×" fiction all over again.

    ⛔ The skip is bound to `FileNotFoundError` ONLY. `DatasetDigestError` means the
    cache EXISTS and no longer matches `SPLIT_DIGESTS` (`dataset.py:113-122`) — the
    drift event this pin advertises as a red — so it must propagate, not be
    swallowed. Measured (PR #11577 review, two contexts independently): folding it
    into the skip made a drifted cache report a green SKIP whose reason asserted
    something false, while three docs claimed the pin reds on exactly that event.
    """
    try:
        instances = lme_dataset.load_dataset(split="s", download=False)
    except FileNotFoundError:
        pytest.skip("real S split not cached (CI)")
    census = census_overlap(instances)
    for key in ("findings_date", "n_instances", "chunk_turns",
                "sessions", "dates", "encode_units"):
        assert census[key] == CENSUS[key], key
    # the numbers the verdict turns on, restated so a change reads as itself
    assert census["sessions"]["same_date_pairs"] == 0
    assert census["sessions"]["prompt_reuse_factor"] == 1.0
    assert census["decision"]["prompt_level_response_cache"]["realizable"] is False
    assert census["decision"]["encode_cache_text_keyed"]["realizable"] is True
    # the verdict's reuse figure IS the measured encode reuse — not a second
    # number a doc could drift from
    assert (census["decision"]["encode_cache_text_keyed"]["reuse_factor"]
            == census["encode_units"]["total"]["reuse_factor"])

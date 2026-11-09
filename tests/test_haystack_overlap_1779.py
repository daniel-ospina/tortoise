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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval import dataset as lme_dataset  # noqa: E402, RUF100
from tools.longmem_eval.haystack_overlap import (  # noqa: E402, RUF100
    CENSUS,
    MATERIAL_HIT_SHARE,
    _decision,
    _summary,
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
        assert row["reuse_factor"] == 1.0
    # each avenue refuses with a message naming ITS OWN measured cell — the
    # session row counts session pairs, the prompt row counts S1 chunk prompts
    assert "0 of 1 repeated pairs share a session date" in (
        census["decision"]["session_level_extraction_reuse"]["blocked_by"])
    assert "only 2 distinct (chunk text, session date) identities" in (
        census["decision"]["prompt_level_response_cache"]["blocked_by"])


def test_prompt_cache_verdict_is_derived_from_the_CHUNK_cell_not_the_session():
    """The prompt cache is settled on the unit the harness RENDERS, not on the
    session (PR #11577 review).

    `extract_session_v2` chunks each session and calls `run_s1` per chunk with
    the session date, so the stored-response identity is (chunk text, date).

    The two sessions here share a chunk (`alpha`) under the SAME date but differ
    overall, so:

      * the SESSION cell never repeats (distinct transcripts) → session reuse
        1.000×, avenue not realizable;
      * the CHUNK cell repeats (`alpha` twice, same date) → 4 chunk prompts
        over 3 identities → avenue realizable.

    Deriving the prompt verdict from the session cell reports `False` and reds
    here — the mutant this test exists to kill. (The real S split lands the
    other way round: 1.0002× / 27 of 124,345, immaterial — the magnitude is
    pinned by `test_census_on_real_s_split`.)
    """
    instances = [
        _instance("q1", [[{"role": "user", "content": "alpha"},
                          {"role": "user", "content": "beta"}]],
                  ["2024-05-05"]),
        _instance("q2", [[{"role": "user", "content": "alpha"},
                          {"role": "user", "content": "gamma"}]],
                  ["2024-05-05"]),
    ]
    census = census_overlap(instances, chunk_turns=1)
    s = census["sessions"]
    assert s["distinct_contents"] == 2        # two different transcripts
    assert s["prompt_reuse_factor"] == 1.0
    assert census["decision"]["session_level_extraction_reuse"][
        "realizable"] is False

    chunk = census["encode_units"]["chunk"]
    assert chunk["slots"] == 4
    assert chunk["distinct"] == 3             # 'alpha' repeats
    assert chunk["distinct_content_date"] == 3  # ... under the same date
    assert chunk["prompt_reuse_factor"] == round(4 / 3, 4)
    prompt_row = census["decision"]["prompt_level_response_cache"]
    assert prompt_row["realizable"] is True
    assert prompt_row["hit_slots"] == 1
    assert prompt_row["identity"] == "rendered S1 chunk text + session_date"


def test_an_immaterial_repeat_is_published_but_not_realizable():
    """Repeating is not the same as being worth building.

    An identity that repeats twice in 900 prompts is arithmetically realizable
    and practically worthless, so `realizable` also requires
    `MATERIAL_HIT_SHARE` — and the measured magnitude stays published, so the
    boolean is never the only evidence a reader gets.

    Both arms run the SAME shape with only the repeat count changed, so the
    floor's action is mechanical rather than an assertion about one fixture.
    """
    def _corpus(repeats: int) -> dict:
        # 300 sessions x 3 turns, chunk_turns=1 → one chunk per turn, so the
        # chunk leg has 900 slots and exactly `repeats` of them carry the same
        # text under the same date; every other turn text is unique.
        sessions, dates = [], []
        n = 0
        for _ in range(300):
            turns = []
            for _ in range(3):
                content = "shared" if n < repeats else f"unique filler {n}"
                turns.append({"role": "user", "content": content})
                n += 1
            sessions.append(turns)
            dates.append("2024-05-05")
        return census_overlap([_instance("q1", sessions, dates)], chunk_turns=1)

    thin = _corpus(3)
    thin_row = thin["decision"]["prompt_level_response_cache"]
    assert thin["encode_units"]["chunk"]["slots"] == 900
    assert thin_row["hit_slots"] == 2          # the identity DOES repeat
    assert thin_row["reuse_factor"] > 1.0
    assert thin_row["hit_share"] < thin_row["materiality_floor"]
    assert thin_row["realizable"] is False
    assert "below the" in thin_row["blocked_by"]

    dense = _corpus(20)                        # same shape, 20 repeats
    dense_row = dense["decision"]["prompt_level_response_cache"]
    assert dense_row["hit_slots"] == 19
    assert dense_row["hit_share"] >= dense_row["materiality_floor"]
    assert dense_row["realizable"] is True
    assert dense_row["blocked_by"] is None


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


def test_every_NOT_realizable_row_carries_its_cause_and_its_magnitude():
    """No row may reach a reader unexplained — the SHIPPED cache included.

    ``encode_cache_text_keyed`` is passed no ``blocked_by`` and is rejected on
    MATERIALITY alone, so on a small population it published
    ``realizable: false`` with ``blocked_by: null``, and the default CLI printed
    `encode_cache_text_keyed reuse 1.0009x — NOT realizable` **about the cache
    the repo actually ships**, with no cause and no magnitude beside it
    (measured: `--limit 3`). A missing explanation reads as "nothing to say",
    which is the opposite of the verdict, and it lands on the one row a reader
    is most likely to act on.

    The two assertions are the contract ``_avenue``'s docstring states: the
    cause is always present on a not-realizable row, and the magnitude is
    always published beside the boolean.
    """
    # 200 sessions, one turn each, TWO carrying the same text: the identity
    # DOES repeat (1 hit per leg) but the share is ~0.5% — below the 1% floor,
    # which is the state that used to publish with no cause.
    sessions = [[{"role": "user", "content": "shared"}],
                [{"role": "user", "content": "shared"}]]
    sessions += [[{"role": "user", "content": f"unique {n}"}]
                 for n in range(198)]
    dates = ["2024-01-01"] * 200
    census = census_overlap([_instance("q1", sessions, dates)], chunk_turns=1)

    encode_row = census["decision"]["encode_cache_text_keyed"]
    assert encode_row["hit_slots"] == 2, (
        f"fixture must produce exactly one repeated identity in EACH leg "
        f"(2 encode-unit hits), got {encode_row['hit_slots']} — the assertions "
        f"below would be vacuous")
    assert encode_row["hit_share"] < encode_row["materiality_floor"]
    assert encode_row["realizable"] is False
    # ★ the defect: this used to be None, and the summary printed no cause.
    assert encode_row["blocked_by"], (
        "a not-realizable row must name its cause — the shipped cache is "
        "rejected on materiality, not for want of an explanation")
    assert "materiality floor" in encode_row["blocked_by"]
    assert str(encode_row["hit_slots"]) in encode_row["blocked_by"]

    # and the magnitude is on EVERY row of the rendered summary, so the boolean
    # is never the only evidence in the default CLI output either.
    summary = _summary(census)
    for name in ("session_level_extraction_reuse",
                 "prompt_level_response_cache", "encode_cache_text_keyed"):
        row = census["decision"][name]
        assert f"{row['hit_slots']} hits" in summary, (
            f"the summary must publish the magnitude for {name}: {summary!r}")
        assert f"{row['hit_share']:.2%} share" in summary


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
    root = Path(__file__).resolve().parent.parent / "tools" / "longmem_eval"
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


def test_cli_refuses_an_empty_population_instead_of_publishing_a_verdict(
    tmp_path, capsys,
):
    """A census over ZERO instances must refuse, not publish a verdict.

    An empty (or mistyped) read used to exit 0 while printing a complete,
    authoritative-looking verdict — `encode_cache… reuse 0.0x — NOT realizable`
    plus a *fabricated* explanation ("0 S1 chunk prompts render from the chunk
    leg … so a repeated chunk text carried by a differently-dated session is a
    different prompt") for a population holding no chunk texts at all. That
    inverts the built cache's published `realizable` on no evidence, which is
    the fail-open direction: `--limit 0`, an empty `--data` file and a mistyped
    path all reach it. The refusal must be exit 2, name the cause, and print NO
    census body.
    """
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")

    rc = main(["--data", str(empty), "--json"])
    captured = capsys.readouterr()

    assert rc == 2, f"an empty population must exit 2, not {rc}"
    assert "refusing" in captured.err, (
        f"the refusal must say what it refused: {captured.err!r}")
    assert "0 instances" in captured.err, (
        f"the refusal must name the population size: {captured.err!r}")
    # ★ the point: no verdict may reach stdout. A caller that pipes this into a
    # report must get nothing to report, never a `NOT realizable` it can quote.
    assert captured.out.strip() == "", (
        f"an empty population must publish NO census body: {captured.out!r}")


def test_cli_refuses_a_zero_UNIT_population_even_when_instances_got_read(
    tmp_path, capsys,
):
    """An INSTANCE count is not a UNIT count — the second fail-open door.

    A `--data` file of questions with no `haystack_sessions` passes the
    instance guard with N >= 1 and still yields ZERO encode units, so the
    original (instance-count) refusal let it through at exit 0 with a full
    verdict plus the same FABRICATED chunk-prompt explanation — inverting the
    built cache's published `realizable` on a population with no sessions at
    all. Measured before this test existed (live head, 2 instances / 0 slots):
    `encode_cache_text_keyed … 0.0x — NOT realizable`, EXIT=0.
    """
    sessions_less = tmp_path / "no_sessions.json"
    sessions_less.write_text(
        json.dumps([{"question_id": "q1"}, {"question_id": "q2"}]),
        encoding="utf-8")

    rc = main(["--data", str(sessions_less), "--json"])
    captured = capsys.readouterr()

    assert rc == 2, (
        f"a 0-unit population must exit 2 even with instances read, not {rc}")
    assert "refusing" in captured.err, (
        f"the refusal must say what it refused: {captured.err!r}")
    assert "0 sessions" in captured.err, (
        f"the refusal must name the unit count: {captured.err!r}")
    assert captured.out.strip() == "", (
        f"a 0-unit population must publish NO census body: {captured.out!r}")


def test_cli_refuses_a_zero_ENCODE_UNIT_population_even_when_sessions_were_read(
    tmp_path, capsys,
):
    """A SESSION count is not an ENCODE-UNIT count — the third fail-open door.

    Sessions that are PRESENT but hold no turns pass both earlier guards (N
    instances, N slots — `_session_transcript([])` renders `""`) while the
    population carries zero turn text and zero chunk text. Measured before this
    refusal existed, live at the head (2 questions / 2 empty sessions):

      session_level_extraction_reuse   reuse 2.0x — REALIZABLE      EXIT=0

    — i.e. it published the SESSION-level cache as worth building on a
    population where there is no text at all to extract, because the empty
    transcript hashes to one repeated identity, and it printed the same
    fabricated chunk-prompt explanation the earlier refusals exist to retire.
    A reuse factor computed over zero encode units is not a measurement, so the
    refusal is keyed on that measured unit count.
    """
    empty_sessions = tmp_path / "empty_sessions.json"
    empty_sessions.write_text(json.dumps([
        {"question_id": "q1", "haystack_sessions": [[]],
         "haystack_dates": ["2023-01-01"]},
        {"question_id": "q2", "haystack_sessions": [[]],
         "haystack_dates": ["2023-01-01"]},
    ]), encoding="utf-8")

    rc = main(["--data", str(empty_sessions), "--json"])
    captured = capsys.readouterr()

    assert rc == 2, (
        f"a 0-encode-unit population must exit 2 even with sessions read, "
        f"not {rc}")
    assert "refusing" in captured.err, (
        f"the refusal must say what it refused: {captured.err!r}")
    assert "0 encode units" in captured.err, (
        f"the refusal must name the measurable quantity: {captured.err!r}")
    # ★ the point: the fabricated verdict must never reach stdout, and
    # `REALIZABLE` in particular must not be published over no text at all.
    assert captured.out.strip() == "", (
        f"a 0-encode-unit population must publish NO census body: "
        f"{captured.out!r}")
    assert "REALIZABLE" not in captured.out.upper(), (
        f"no avenue may be published as realizable over no evidence: "
        f"{captured.out!r}")


def test_a_one_hit_cell_past_4dp_Never_publishes_a_FALSE_cause():
    """The cause must be derived from the INTEGER witness, not the rounded ratio.

    ``round(100000/99999, 4) == 1.0``, so a ``reuse_factor <= 1.0`` test fires
    while ``slots - distinct == 1`` and the row publishes a **self-
    contradicting, false** reason. Measured before the fix (100,000 sessions,
    the two repeats sharing content AND date):

      session row -> "1 session slots repeat another slot's content, but 1 of 1
        repeated pairs share a session date ... so no two repeated sessions
        render the same prompt"                          (they DO)
      chunk row   -> "... so a repeated chunk text carried by a
        differently-dated session is a different prompt"  (the repeat is
        same-dated)

    The verdict polarity was right and the published REASON was not — the
    "published claim with no producer" class this module retires. Crafting the
    cells directly keeps the regression cheap (a real 100,000-session corpus
    reproduces it but costs seconds); the counts below are the measured ones.
    """
    def cell(slots: int, distinct: int) -> dict:
        # the loader-level shape: reuse_factor is the ROUNDED value, which is
        # exactly why it must not be the branch key
        return {"slots": slots, "distinct": distinct,
                "reuse_factor": round(slots / distinct, 4)}

    d = _decision(
        duplicate_content_slots=1, repeated_pairs=1, same_date_pairs=1,
        session_prompt=cell(100000, 99999),
        chunk_prompt=cell(100000, 99999),
        encode=cell(200000, 199998),
    )

    # the rounded value really does read 1.0 — the branch key the bug used
    assert d["session_level_extraction_reuse"]["reuse_factor"] == 1.0
    assert d["session_level_extraction_reuse"]["hit_slots"] == 1

    s = d["session_level_extraction_reuse"]["blocked_by"]
    # ★ the defect: it used to claim NO pair shares a date while `same_date_pairs=1`
    assert "no two repeated sessions render the same prompt" not in s, (
        f"the session cause claims the repeats are re-dated, but "
        f"same_date_pairs=1 — a false published reason: {s!r}")
    assert "materiality floor" in s, f"expected the floor cause: {s!r}"

    c = d["prompt_level_response_cache"]["blocked_by"]
    assert "differently-dated" not in c, (
        f"the chunk cause blames a different date for a same-dated repeat: {c!r}")
    assert "materiality floor" in c, f"expected the floor cause: {c!r}"


def test_the_research_doc_publishes_this_files_REAL_test_count():
    """§5 of the research note states this suite's size, so the size is DERIVED.

    Two fix rounds published a stale count after adding tests ("10 tests",
    then 13 while the file collected 15) — the same "a published number with no
    instrument standing behind it" class this module exists to retire, and a
    number no test could red. So the count is parsed from THIS file's AST (not
    from collection, which depends on the pytest invocation) and compared to
    the sentences the live docs publish: a new test without an update reds here.

    ⛔ EVERY live site is checked, not just §5. A round-4 fix published `19
    tests: 18 hermetic` in §5 while the same commit left `17` in the manifest
    comment, `docs/00_index.md` and §8 — one commit, two contradictory readings
    of the same run, and a §5-only pin cannot see it. That is precisely the
    "published number with no instrument" this pin exists to prevent, so the
    coverage is widened to every place the count is quoted — including §5's own
    `measured: N passed, 1 skipped` sentence, which the five-site version of
    this check still left unguarded.
    """
    doc = (Path(__file__).resolve().parents[1]
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
    total, hermetic = len(tests), len(tests) - len(slow)
    root = Path(__file__).resolve().parents[1]
    # (file, the substring that must be present) — every LIVE site quoting the
    # count. A §5-only check let this drift twice in one PR.
    sites = [
        (doc, f"{total} tests: {hermetic} hermetic"),
        (doc, f"{hermetic} hermetic pass"),
        # §5's unarmed-pin sentence quotes the SAME run (`measured: N passed, 1
        # skipped`) and was the one live site the first five-site check missed —
        # a published count with no instrument, one line below a checked one.
        (doc, f"measured: {hermetic} passed"),
        # §5 ALSO restates the count as `N hermetic ratchets GATE`, two lines
        # below the `N tests: M hermetic` sentence. The six-site check still
        # only matched that phrase in `00_index.md`, so mutating §5's own copy
        # to the previous count left this pin GREEN (measured). Both files are
        # checked for it now.
        (doc, f"{hermetic} hermetic ratchets"),
        (root / "docs" / "00_index.md", f"{hermetic} hermetic ratchets"),
        (root / "config" / "ci-surfaces.yml",
         f"The {hermetic} hermetic tests are therefore GATED"),
        (root / "config" / "ci-surfaces.yml",
         f"measured: {hermetic} passed"),
    ]
    stale = [(p.name, want) for p, want in sites
             if want not in p.read_text(encoding="utf-8")]
    assert not stale, (
        f"these sites must publish the REAL count (this file defines {total} "
        f"tests, {len(slow)} of them @slow → {hermetic} hermetic): "
        f"{stale}")


def test_the_gated_file_carries_a_durations_row_and_is_really_in_the_fast_pool():
    """The manifest SIDE of the move: gated means in `fast_pool()` AND given a weight.

    This file lives at `tests/` ROOT on purpose — `on_demand`'s `longmem_eval/**`
    would subtract it under the old path (#10194), so the ratchets would never run
    on the edit they exist to prevent. That move has a second consequence the
    earlier `on_demand` rebuttal missed: a `tests/`-root file is a **fast-pool**
    member, and the `#3400/#4348` convention is that a fast file a PR ADDS must
    carry a `durations:` row — "a default is not a reading". Absence reds no gate
    (`duration_coverage_issues()` is a ratio against the pool with headroom), so
    without this pin a missing row is invisible until a human notices it.

    Both halves are asserted, because either alone can pass vacuously: a file that
    is NOT in the pool needs no weight, and a row for a file that is not in the
    pool records nothing. Proven non-vacuous at the pre-fix head — there the pool
    assertion passed and the row assertion failed.
    """
    from tools import ci_selection

    manifest = ci_selection.load_manifest()
    name = Path(__file__).name
    assert name in ci_selection.fast_pool(manifest), (
        f"{name} must be in `fast_pool()` — that is the whole point of living at "
        "`tests/` root instead of the subtracted `tests/longmem_eval/` subtree")
    assert name in (manifest.get("durations") or {}), (
        f"{name} is a fast-pool file, so it must carry a `durations:` row "
        "(`#3400/#4348`: a default is not a reading, and a hand-written row is "
        "the only record of a file a PR added)")


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
    # ⛔ The prompt-cache verdict is settled on the S1 CHUNK cell, not the
    # session cell: the harness renders one prompt per EDU chunk. Its measured
    # magnitude is the finding (27 of 124,345 = 0.02%), so it is restated here
    # — a verdict asserted without its cell is what this pin exists to prevent.
    chunk = census["encode_units"]["chunk"]
    assert chunk["distinct_content_date"] == 124318
    assert chunk["prompt_reuse_factor"] == 1.0002
    prompt_row = census["decision"]["prompt_level_response_cache"]
    assert prompt_row["hit_slots"] == 27
    assert prompt_row["hit_share"] < MATERIAL_HIT_SHARE
    assert prompt_row["realizable"] is False
    assert prompt_row["identity"] == "rendered S1 chunk text + session_date"
    assert census["decision"]["session_level_extraction_reuse"][
        "realizable"] is False
    assert census["decision"]["encode_cache_text_keyed"]["realizable"] is True
    # the verdict's reuse figure IS the measured encode reuse — not a second
    # number a doc could drift from
    assert (census["decision"]["encode_cache_text_keyed"]["reuse_factor"]
            == census["encode_units"]["total"]["reuse_factor"])

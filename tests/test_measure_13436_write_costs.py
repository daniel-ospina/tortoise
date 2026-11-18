"""#13436 P7 — `tools/measure_13436_write_costs.py`'s own pins (its refusals included).

WHAT THIS SUITE IS FOR
======================
The P7 artifacts (`docs/evidence/13436-write-path-costs/`) publish numbers a later
decision is meant to start from — `#11796` owns the `ingest` half, the
`create_entity`/`create_edge` half is UNOWNED (that absence is the finding), `#8977`
owns cost 2 and `#5444` cost 3. An instrument whose only observable outcome is "a JSON
file appeared" cannot tell a reader whether the number in it was the one it measured,
and this repo has already paid for that twice (`#11859`'s vacuous pin; `#13436`'s own
review, which found the tool shipped with **no test at all**, so none of its four
runtime refusals could red anything — P5).

So the arms below are pinned, each paired with the mutation that reverts it:

1. **the statement pin** — `_assert_statement_matches_source()` refuses when the
   statement the tool times is not the one `sdk._nudge_candidates` ships. ⛔ Mutant:
   drop `n.content` from that `RETURN` list — the very "fix" the docstring forbids.
2. **the payload axis** — `_content(chars, i)` returns a body of EXACTLY `chars`
   characters, for every index. ⛔ Mutant: un-padded `" ".join(out)[:chars]`, which
   returned one character SHORT on some rows — the two committed runs publish
   59.84 / 299.81 / 1,199.98 for a declared 60 / 300 / 1,200 (the first cut of this
   instrument was 7 characters short, which is where the irreconcilable "22×" came
   from; #13436 review P3).
3. **the gate-count composition** — the per-entity term comes from the shape NAMED
   `object_ns`, never from `rows[-1]`, and a missing shape REFUSES rather than
   publishing a neighbour's count or a zero. ⛔ Mutants: `rows[-1]` (the original,
   which published 1 + 2 = 3 while its own `rule` string said 2 — review P2), and
   `_reads_of` returning `None` (which would publish a single-entity total of 1).
4. **the refusal path** — an `ArmRefused` leaves `main()` at exit **3** with NO
   artifact written, so a refused arm can never reach a `docs/` path as a number.
5. **the joinery** — `_median_ms` / `_time_call`, where a published millisecond is
   manufactured: a median that drops a sample, or counts a warm-up, shifts every
   number in the artifact invisibly.

HERMETIC, AND WHY THAT IS ENOUGH
--------------------------------
No DB, no graph, no docker, no network, no model call: the statement pin reads
`tortoise/sdk.py` as text, and every other case drives an in-process function with the
SDK construction and the engine probe stubbed. The MEASURED arms stay a human/CI action
(the README's reproduce block) — what is pinned here is that they cannot report a number
they did not derive.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import measure_13436_write_costs as measure  # noqa: E402

EVIDENCE = ROOT / "docs" / "evidence" / "13436-write-path-costs"
ARTIFACTS = (EVIDENCE / "write-path-costs.json", EVIDENCE / "write-path-costs-run2.json")


# --------------------------------------------------------------------------- #
# 1 — the statement pin: what is timed must what ships
# --------------------------------------------------------------------------- #
def test_the_shipped_statement_is_the_one_this_tool_times():
    """The pin passes against the tree it ships with (a control, not a tautology)."""
    measure._assert_statement_matches_source()

    sdk = (ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    assert "n.content" in measure.NUDGE_SQL
    assert "n.content" in sdk, (
        "`_nudge_candidates` no longer returns `content` — if that is deliberate, "
        "re-measure and update the artifacts and the README WITH the code"
    )


def test_a_drifted_statement_is_refused_rather_than_timed(monkeypatch):
    """⛔ THE MUTANT THE DOCSTRING FORBIDS: a statement that is not the shipped one."""
    monkeypatch.setattr(
        measure, "NUDGE_SQL",
        "MATCH (n:Point) WHERE NOT n.id IN $ex "
        "RETURN n.id, n.name, n.label, n.is_operator LIMIT 400")
    with pytest.raises(measure.ArmRefused) as exc:
        measure._assert_statement_matches_source()
    assert "not the one this tool measures" in str(exc.value)


# --------------------------------------------------------------------------- #
# 2 — the payload axis: the seeded row IS the declared length
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("chars", [1, 2, 5, 17, 18, 60, 300, 1200])
def test_the_content_axis_seeds_exactly_the_declared_length(chars):
    """EVERY index, not one sample: the shortfall hit only some joins.

    ⛔ THE #13436 P3 DEFECT (second round): `" ".join(out)[:chars]` returns the whole
    join when the join is a character short, so the seeded length depended on where
    the word boundary fell — which is why sampling one index would have passed.
    """
    lengths = {len(measure._content(chars, i)) for i in range(0, 200)}
    assert lengths == {chars}, (
        f"declared {chars} chars/row but seeded lengths were {sorted(lengths)} — the "
        "axis a reader multiplies by is not the axis the run used"
    )


def test_the_unpadded_join_is_observably_short():
    """The expression that must not come back, evaluated on the real word bank.

    Kept as an executable record of WHY the pad exists: recomputing the un-padded
    slice shows it lands short for a large share of indices, so the defect is
    reproducible from the test rather than only described in a comment.
    """
    short = 0
    for i in range(0, 200):
        body = measure._content(60, i)
        words = measure._WORD_BANK
        out, used, k = [], 0, i
        while used < 60:
            w = words[k % len(words)]
            out.append(w)
            used += len(w) + 1
            k += 1
        if len(" ".join(out)[:60]) != 60:
            short += 1
        assert len(body) == 60
    assert short > 0, "the un-padded slice is now exact — re-derive the README's note"


def test_the_committed_artifacts_publish_the_measured_axis():
    """Both runs carry the MEASURED chars/row, not only the requested one."""
    for path in ARTIFACTS:
        rows = json.loads(path.read_text(encoding="utf-8"))["arms"]["nudges"]["by_content_length"]
        assert rows, f"{path.name} published no payload axis"
        for row in rows:
            assert "content_chars_requested" in row, (
                f"{path.name}: the axis publishes a declared length with no measured "
                "one beside it (#13436 review P3)"
            )
            measured = row["content_bytes_per_row"]
            assert row["content_chars_per_row"] == pytest.approx(measured, abs=0.01)
            assert row["content_chars_requested"] >= row["content_chars_per_row"]
            # the shortfall the un-padded seeder produced: < 1 char, never >= 7
            assert row["content_chars_requested"] - row["content_chars_per_row"] < 1


# --------------------------------------------------------------------------- #
# 3 — the gate-count composition: named shape, never a position
# --------------------------------------------------------------------------- #
def _shape(shape: str, reads: int) -> dict:
    return {"shape": shape, "gate_reads_per_request": reads}


def test_the_composition_reads_the_named_shape_not_the_last_row():
    """``object_ns`` is 1 read; ``object_ns_twice`` is 2 and sits LAST.

    ⛔ THE MUTANT: `rows[-1]`, the original derivation, which published
    `per_entity_writes: 2` / `single_entity_commit_total_reads: 3` — numbers the
    composition's own `rule` string denies. Ordering the worst case last must not
    move the answer.
    """
    rows = [
        _shape("point_ns", 1),
        _shape("object_ns", 1),
        _shape("point_ns_twice", 2),
        _shape("object_ns_twice", 2),
    ]
    comp = measure._hosted_commit_composition(rows)
    assert comp["derived_from_shape"] == measure.PER_ENTITY_SHAPE == "object_ns"
    assert comp["per_entity_writes"] == 1
    assert comp["single_entity_commit_total_reads"] == 1 + 1 == 2
    assert comp["rule"].startswith("1 pre-check read per request + 1 read per")


def test_the_composition_refuses_rather_than_guessing_a_neighbour_or_a_zero():
    """A missing part is not a zero: `None` would publish a total of 1."""
    with pytest.raises(measure.ArmRefused) as exc:
        measure._hosted_commit_composition([_shape("point_ns", 1), _shape("object_ns_twice", 2)])
    assert "object_ns" in str(exc.value)
    with pytest.raises(measure.ArmRefused):
        measure._reads_of([], "object_ns")


@pytest.mark.parametrize("path", ARTIFACTS)
def test_the_committed_artifacts_match_the_shipped_derivation(path):
    """The published composition is what the SHIPPED function derives from its own rows."""
    kindgate = json.loads(path.read_text(encoding="utf-8"))["arms"]["kindgate"]
    published = kindgate["hosted_commit_composition"]
    derived = measure._hosted_commit_composition(kindgate["by_shape"])
    for key, value in derived.items():
        assert published[key] == value, f"{path.name}: {key}"
    assert published["per_entity_writes"] == 1
    assert published["single_entity_commit_total_reads"] == 2


# --------------------------------------------------------------------------- #
# 4 — the refusal path: exit 3, and NO artifact
# --------------------------------------------------------------------------- #
def test_a_refused_arm_exits_3_and_writes_no_artifact(tmp_path, monkeypatch, capsys):
    class _Stub:
        def _get_proj(self):  # the engine probe's only use of the SDK
            return None

    monkeypatch.setattr(measure, "_fresh_sdk", lambda graph_name: _Stub())
    monkeypatch.setattr(measure, "_engine_version", lambda proj: "stub")

    def _refuse(*a, **k):
        raise measure.ArmRefused("seeded population did not land")

    monkeypatch.setattr(measure, "arm_nudges", _refuse)
    out = tmp_path / "costs.json"
    rc = measure.main(["--arm", "nudges", "--json", str(out)])

    assert rc == 3
    assert not out.exists(), "a refused arm must never reach a --json path"
    assert "REFUSED" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 5 — the joinery a published millisecond is made of
# --------------------------------------------------------------------------- #
def test_median_ms_reports_the_median_and_the_edges():
    assert measure._median_ms([9.0, 1.0, 5.0, 3.0]) == {
        "median_ms": 4.0, "min_ms": 1.0, "max_ms": 9.0, "n": 4}
    # even sample -> the mean of the two middles, not one of them (a median that
    # picked a middle element would read 2.0 or 3.0 here)
    assert measure._median_ms([1.0, 2.0, 3.0, 10.0])["median_ms"] == 2.5
    # rounded once, where it is published
    assert measure._median_ms([1.23456])["median_ms"] == 1.235


def test_time_call_drops_warmups_and_counts_only_the_repeats():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1

    out = measure._time_call(fn, repeats=3, warmups=2)
    assert calls["n"] == 5, "warm-ups must run AND must not be counted as samples"
    assert out["n"] == 3

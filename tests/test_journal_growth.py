"""#5612 — `tools/journal_growth.py` measures a journal's growth, and its
accounting is exact.

Hermetic: temp dirs and committed fixtures only — no Docker, no FalkorDB, no
network. Nothing is monkeypatched; the property under test is the tool's
arithmetic over bytes that are really on disk.

Why these tests exist
=====================
`docs/architecture/STORAGE-ARCHITECTURE.md` §5.1 says *"No measurement of the
journal's growth exists yet"*, and every plan to bound the journal (rotate,
compact, checkpoint-then-truncate) depends on that number. A measurement a
decision turns on has to be checkable, so each class of byte is pinned here:

* ``test_per_record_sizes_sum_to_the_file_size`` — ``bytes + other_bytes`` is
  the file size EXACTLY, on a clean journal and on one with junk in it. A
  measurement that silently drops bytes understates growth, which is the
  direction that gets a volume filled.
* ``test_malformed_line_is_measured_not_raised`` — a mid-file malformed line
  makes `EventLog.read_all` REFUSE (#3316), and that refusal is correct. The
  measurer must not share it: a journal that can no longer be replayed is
  exactly the journal whose size an operator needs to see.
* ``test_embedding_bytes_are_attributed_inside_the_record`` — the vector rides
  nested inside the writer's snapshot (``point.embedding``, #5004), so a
  top-level scan reports 0% on the records that dominate the journal.

Run:
    uv run pytest tests/test_journal_growth.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.journal_growth import (
    SEAL_SENTINEL,
    journal_files,
    main,
    measure,
)

FIXTURE = Path(__file__).resolve().parent.parent / (
    "supabase/tests/fixtures/record_projection_journal.jsonl")


def _write(path: Path, records: list[dict], *, raw: str = "") -> bytes:
    """Write a journal the way `EventLog.append` does: one JSON line each."""
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    body += raw
    path.write_bytes(body.encode("utf-8"))
    return path.read_bytes()


def test_per_record_sizes_sum_to_the_file_size(tmp_path: Path) -> None:
    """`bytes + other_bytes` accounts for every byte on disk, exactly.

    Pre-fix this tool did not exist: an operator had no measured number for the
    journal at all (§5.1). The assertion that makes the number trustworthy is
    this one — a measurer whose parts do not add up to the file is the wrong
    input for choosing a bound.
    """
    a = tmp_path / "team_a_tortoise.jsonl"
    b = tmp_path / "team_b_tortoise.jsonl"
    data_a = _write(a, [
        {"type": "PointAdded", "id": "p1", "point": {"id": "p1",
                                                     "content": "héllo — “quoted”"}},
        {"type": "PointRevised", "id": "p1", "props": {"confidence": 0.8}},
        {"type": "EntityLinked", "from": "p1", "to": "e1"},
    ])
    data_b = _write(b, [{"type": "PointAdded", "id": "p2",
                         "point": {"id": "p2", "content": "second"}}])
    report = measure(tmp_path)
    by_path = {Path(f["path"]).name: f for f in report["files"]}
    assert by_path[a.name]["file_bytes"] == len(data_a)
    assert by_path[a.name]["records"] == 3
    assert by_path[a.name]["bytes"] + by_path[a.name]["other_bytes"] == len(data_a)
    assert by_path[a.name]["other_bytes"] == 0        # clean journal: all record bytes
    assert by_path[b.name]["bytes"] == len(data_b)
    total = report["total"]
    assert total["records"] == 4
    assert total["bytes"] + total["other_bytes"] == len(data_a) + len(data_b)
    # Multi-byte UTF-8 is counted as BYTES, which is what the volume bills.
    assert total["by_type"]["PointAdded"]["count"] == 2
    assert total["by_type"]["PointRevised"]["count"] == 1


def test_malformed_line_is_measured_not_raised(tmp_path: Path) -> None:
    """Junk mid-file is counted; the measurer never refuses the journal.

    `EventLog.read_all` raises on a mid-file malformed line (#3316 — a silent
    skip could resurrect state). That is the journal READER's contract and it
    is untouched. The MEASURER has the opposite need: the journal an operator
    most needs a size for is the one that no longer replays.
    """
    path = tmp_path / "graph.jsonl"
    good = json.dumps({"type": "PointAdded", "id": "p1"}, ensure_ascii=False)
    torn = '{"type": "PointAdded", "id": "p2"'
    data = (good + "\n" + torn + "\n" + SEAL_SENTINEL + "\n"
            + json.dumps({"type": "PointRetracted", "id": "p1"}) + "\n"
            + "\n").encode("utf-8")
    path.write_bytes(data)
    report = measure(tmp_path)
    total = report["total"]
    assert total["records"] == 2                 # the two well-formed records
    assert total["malformed"] == 1               # the torn fragment, mid-file
    assert total["seal_annotations"] == 1        # the #5917 seal line
    assert total["empty_lines"] == 1
    assert total["bytes"] + total["other_bytes"] == len(data)
    assert total["other_bytes"] > 0
    assert set(total["by_type"]) == {"PointAdded", "PointRetracted"}


def test_embedding_bytes_are_attributed_inside_the_record(tmp_path: Path) -> None:
    """The vector's bytes are found NESTED in the writer's snapshot (#5004).

    This is the term that dominates a hosted journal — 384 float64 decimal
    reprs inside ``record['point']['embedding']`` — and a top-level-only scan
    would report zero for exactly the records that carry it.
    """
    vec = [round(0.001 * i, 6) for i in range(384)]
    path = tmp_path / "graph.jsonl"
    data = _write(path, [
        {"type": "PointAdded",
         "point": {"id": "p1", "content": "c", "embedding": vec}},
        {"type": "PointRevised", "id": "p1", "props": {"confidence": 0.5}},
    ])
    report = measure(tmp_path)
    total = report["total"]
    expected = len(json.dumps(vec).encode("utf-8"))
    assert total["embedding_records"] == 1
    assert total["embedding_bytes"] == expected
    assert total["embedding_bytes"] < total["bytes"]     # it fits inside the record
    assert total["bytes"] + total["other_bytes"] == len(data)


def test_directory_enumeration_matches_rebuild_all(tmp_path: Path) -> None:
    """A dir measures its direct ``*.jsonl`` children only — non-recursive.

    `projection.rebuild_all` lists with ``os.listdir`` + ``endswith('.jsonl')``,
    so a nested file is NOT part of that graph's journal. Counting it here
    would report growth the replay never reads (and vice versa).
    """
    _write(tmp_path / "graph.jsonl", [{"type": "PointAdded", "id": "p1"}])
    (tmp_path / "notes.txt").write_text("not a journal\n", encoding="utf-8")
    nested = tmp_path / "archive"
    nested.mkdir()
    _write(nested / "old.jsonl", [{"type": "PointAdded", "id": "p0"}] * 5)
    assert [p.name for p in journal_files(tmp_path)] == ["graph.jsonl"]
    report = measure(tmp_path)
    assert report["total"]["records"] == 1


def test_real_fixture_corpus_measures() -> None:
    """The committed 29-record-type corpus measures, with no unaccounted bytes."""
    size = FIXTURE.stat().st_size
    report = measure(FIXTURE)
    total = report["total"]
    assert total["records"] == 39
    assert total["malformed"] == 0
    assert total["bytes"] + total["other_bytes"] == size
    assert len(total["by_type"]) == 29
    assert total["record_bytes"]["min"] <= total["record_bytes"]["p50"]
    assert total["record_bytes"]["p95"] <= total["record_bytes"]["max"]


def test_projection_uses_the_measured_mean(tmp_path: Path) -> None:
    """The growth projection is arithmetic on the measured mean, not a guess."""
    _write(tmp_path / "graph.jsonl", [
        {"type": "PointAdded", "id": "p1", "point": {"content": "x" * 100}},
        {"type": "PointRevised", "id": "p1", "props": {"confidence": 0.5}},
    ])
    report = measure(tmp_path, project_records=1000)
    mean = report["total"]["record_bytes"]["mean"]
    proj = report["projection"]
    assert proj["records"] == 1000
    assert proj["bytes"] == round(mean * 1000)
    assert proj["mb"] == round(mean * 1000 / (1024 * 1024), 2)


def test_cli_json_is_a_stable_report(tmp_path: Path, capsys) -> None:
    path = tmp_path / "graph.jsonl"
    _write(path, [{"type": "PointAdded", "id": "p1"}])
    assert main([str(path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"files", "total", "projection"}
    assert set(out["total"]) >= {"records", "bytes", "other_bytes", "malformed",
                                 "by_type", "field_bytes", "record_bytes",
                                 "embedding_bytes", "embedding_records"}
    assert out["total"]["records"] == 1


def test_text_form_renders(tmp_path: Path, capsys) -> None:
    path = tmp_path / "graph.jsonl"
    _write(path, [{"type": "PointAdded", "id": "p1"}])
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "journal growth — 1 file(s)" in out
    assert "a measurement, not a bound" in out


def test_missing_path_is_a_usage_error(tmp_path: Path, capsys) -> None:
    assert main([str(tmp_path / "absent.jsonl")]) == 2
    assert "no journal to measure" in capsys.readouterr().err


def test_seal_sentinel_is_the_journals_own() -> None:
    """The tool reads the marker from `tortoise.log` — never a second spelling."""
    from tortoise.log import SEAL_SENTINEL as JOURNAL_SENTINEL
    assert SEAL_SENTINEL == JOURNAL_SENTINEL


@pytest.mark.parametrize("bad", ["not json at all", "[1, 2, 3]", "42", '"s"'])
def test_non_record_lines_are_malformed_not_records(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "graph.jsonl"
    path.write_bytes((bad + "\n").encode("utf-8"))
    total = measure(tmp_path)["total"]
    assert total["records"] == 0
    assert total["malformed"] == 1
    assert total["other_bytes"] > 0

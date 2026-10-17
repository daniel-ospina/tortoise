"""#5612 — `tools/journal_growth.py` measures a journal's growth, and its
accounting is exact.

Hermetic: temp dirs and committed fixtures only — no Docker, no FalkorDB, no
network. The accounting tests are not monkeypatched; the property under test is
the tool's arithmetic over bytes that are really on disk. The one exception is
`test_probe_vector_width_is_derived_from_the_embedder`, which monkeypatches
`tortoise.embeddings.EMBEDDING_DIM` on purpose — that rotation IS the property.

Why these tests exist
=====================
`docs/architecture/STORAGE-ARCHITECTURE.md` §5.1 says *"No measurement of the
journal's growth exists yet"*, and every plan to bound the journal (rotate,
compact, checkpoint-then-truncate) depends on that number. A measurement a
decision turns on has to be checkable, so each class of byte is pinned here:

* ``test_per_record_sizes_sum_to_the_file_size`` — ``bytes + other_bytes`` is
  the file size EXACTLY, asserted against the bytes the test itself wrote. A
  measurement that silently drops bytes understates growth, which is the
  direction that gets a volume filled. (``other_bytes`` is ACCUMULATED from the
  lines actually classified, not a residual: as a residual the equality would
  be an arithmetic identity and could not fail.)
* ``test_malformed_line_is_measured_not_raised`` — a mid-file malformed line
  makes `EventLog.read_all` REFUSE (#3316), and that refusal is correct. The
  measurer must not share it: a journal that can no longer be replayed is
  exactly the journal whose size an operator needs to see.
* ``test_embedding_bytes_are_attributed_inside_the_record`` — the vector rides
  nested inside the writer's snapshot (``point.embedding``, #5004), so a
  top-level scan reports 0% on the records that dominate the journal — and
  ``test_null_embedding_is_not_a_carried_vector`` pins the mirror case, the
  forced-present ``"embedding": null`` that means *no* vector.
* ``test_live_base_dir_layout_is_measured`` — the live journal is
  ``<base>/<graph>/events.jsonl``, not a flat dir; measuring the base dir
  reported "no journal to measure" on the only journal an operator sizes.

Run:
    uv run pytest tests/test_journal_growth.py -q
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tools import journal_growth
from tools.journal_growth import (
    journal_files,
    main,
    measure,
)
from tortoise.embeddings import EMBEDDING_DIM
from tortoise.log import SEAL_SENTINEL

FIXTURE = Path(__file__).resolve().parent.parent / (
    "supabase/tests/fixtures/record_projection_journal.jsonl")
PROBE = Path(__file__).resolve().parent.parent / (
    "docs/evidence/5612-journal-growth/probes/corpus_growth_probe.py")


def _write(path: Path, records: list[dict], *, raw: str = "") -> bytes:
    """Write a journal the way `EventLog.append` does: one JSON line each."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
    # EXACT, not `> 0`: pins the terminator accounting on the one fixture where
    # dropping a byte is plausible (a record sitting next to a torn fragment).
    assert total["other_bytes"] == ((len(torn.encode()) + 1)
                                    + (len(SEAL_SENTINEL.encode()) + 1) + 1)
    assert total["bytes"] == len(data) - total["other_bytes"]
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

    The nested case is asserted under BOTH names, because the name is what the
    old unconditional ``<subdir>/events.jsonl`` scan turned on: ``old.jsonl``
    fails the ``JOURNAL_NAME`` test and stayed out, while ``events.jsonl`` — the
    natural name for a retained/archived slice of a journal, and what an
    operator reaches for while sizing one — WAS counted, in the tool that exists
    to size exactly that. Over-reporting is the direction that argues for a
    tighter bound than the journal requires, whose discarded prefix is state
    loss on the next ``rebuild_all`` (`STORAGE-ARCHITECTURE.md` §5.2).
    """
    _write(tmp_path / "graph.jsonl", [{"type": "PointAdded", "id": "p1"}])
    (tmp_path / "notes.txt").write_text("not a journal\n", encoding="utf-8")
    nested = tmp_path / "archive"
    nested.mkdir()
    _write(nested / "old.jsonl", [{"type": "PointAdded", "id": "p0"}] * 5)
    assert [p.name for p in journal_files(tmp_path)] == ["graph.jsonl"]
    report = measure(tmp_path)
    assert report["total"]["records"] == 1

    # The invariant its docstring names, for the name that actually slipped
    # through: a retained journal called `events.jsonl` under a per-graph dir.
    _write(nested / "events.jsonl", [{"type": "PointAdded", "id": "p2"}] * 4)
    assert [p.name for p in journal_files(tmp_path)] == ["graph.jsonl"]
    assert [f["path"] for f in measure(tmp_path)["files"]] == [
        str(tmp_path / "graph.jsonl")]
    assert measure(tmp_path)["total"]["records"] == 1


def test_real_fixture_corpus_measures() -> None:
    """The committed 29-record-type corpus measures, with no unaccounted bytes."""
    size = FIXTURE.stat().st_size
    report = measure(FIXTURE)
    total = report["total"]
    assert total["records"] == 39
    assert total["malformed"] == 0
    assert total["file_bytes"] == size
    assert total["other_bytes"] == 0        # a clean corpus: nothing unaccounted
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
    err = capsys.readouterr().err
    assert "no journal to measure" in err
    # Both layouts the tool accepts are named, so "no journal" cannot be a
    # mis-invocation reported as an empty journal.
    assert "*.jsonl" in err
    assert "<graph>/events.jsonl" in err


def test_live_base_dir_layout_is_measured(tmp_path: Path) -> None:
    """A live BASE dir measures ``<graph>/events.jsonl``, one journal per graph.

    `config._resolve_event_log_path` returns
    ``os.path.join(base, key, "events.jsonl")``, so every direct child of
    `TORTOISE_EVENT_LOG_BASE_DIR` is a graph DIRECTORY. Enumerating only
    ``*.jsonl`` children found nothing, raised ``FileNotFoundError`` and exited
    2 — "no journal to measure" on the very journal #5612 exists to size.

    Negative control: ``archive/old.jsonl`` is not a ``events.jsonl`` and
    `rebuild_all` never reads it, so it stays unmeasured.
    """
    base = tmp_path / "events"
    _write(base / "graph_a" / "events.jsonl",
           [{"type": "PointAdded", "id": "a"}])
    _write(base / "graph_b" / "events.jsonl",
           [{"type": "PointAdded", "id": "b1"},
            {"type": "PointRevised", "id": "b1"}])
    _write(base / "archive" / "old.jsonl",
           [{"type": "PointAdded", "id": "old"}] * 3)
    (base / "notes.txt").write_text("not a journal\n", encoding="utf-8")

    files = journal_files(base)
    assert [p.parent.name for p in files] == ["graph_a", "graph_b"]
    report = measure(base)
    assert report["total"]["records"] == 3
    assert sorted(f["graph"] for f in report["files"]) == ["graph_a", "graph_b"]
    assert (report["total"]["bytes"] + report["total"]["other_bytes"]
            == report["total"]["file_bytes"])
    # A per-graph dir keeps working as a single-graph target.
    assert measure(base / "graph_b")["total"]["records"] == 2


def test_null_embedding_is_not_a_carried_vector(tmp_path: Path) -> None:
    """``"embedding": null`` is the ABSENT shape, not a vector-bearing record.

    PRESENCE IS OWNERSHIP (#5004): `sdk._emit_event` forces the key present
    (`clean.setdefault("embedding", None)`) and `_drop_journalled_embedding`
    sets it to `None` when there is no usable vector — which is what every
    snapshot record looks like on the keyword-only deployment. Counting the key
    as a vector reported every snapshot as carrying one.
    """
    vec = [0.5, 0.25]
    path = tmp_path / "graph.jsonl"
    data = _write(path, [
        {"type": "PointAdded", "point": {"id": "p1", "embedding": None}},
        {"type": "PointAdded", "point": {"id": "p2", "embedding": []}},
        {"type": "OperatorAdded", "point": {"id": "o1", "embedding": None}},
        {"type": "PointAdded", "point": {"id": "p3", "embedding": vec}},
    ])
    total = measure(tmp_path)["total"]
    assert total["records"] == 4
    assert total["embedding_records"] == 1          # only the real vector
    assert total["embedding_bytes"] == len(json.dumps(vec).encode())
    assert total["bytes"] + total["other_bytes"] == len(data)


def test_torn_tail_is_accounted(tmp_path: Path) -> None:
    """The UNTERMINATED final line is the terminator branch production hits.

    A crash leaves the last record without its newline (#5917's seal machinery
    exists for that tail). Every other fixture here is newline-terminated, so
    without this case the terminator rule is never exercised — and an off-by-one
    there understates growth by one byte per journal, silently.
    """
    first = json.dumps({"type": "PointAdded", "id": "p1"})
    # (a) a valid record whose terminator did not survive the crash
    path = tmp_path / "torn_valid.jsonl"
    data = (first + "\n" + json.dumps({"type": "PointRevised", "id": "p1"})).encode()
    path.write_bytes(data)
    total = measure(path)["total"]
    assert total["records"] == 2
    assert total["bytes"] == len(data)      # no phantom terminator added
    assert total["other_bytes"] == 0
    # (b) a record torn MID-JSON, also unterminated
    fragment = '{"type": "PointAdded", "id": "p2"'
    path_b = tmp_path / "torn_fragment.jsonl"
    data_b = (first + "\n" + fragment).encode()
    path_b.write_bytes(data_b)
    total_b = measure(path_b)["total"]
    assert total_b["records"] == 1
    assert total_b["malformed"] == 1
    assert total_b["other_bytes"] == len(fragment.encode())
    assert total_b["bytes"] + total_b["other_bytes"] == len(data_b)


@pytest.mark.parametrize("separator", [b"\r\n", b"\r"])
def test_foreign_newlines_measure_their_records(
        tmp_path: Path, separator: bytes) -> None:
    """CRLF and bare CR separate records, exactly as `log.py::read_all` reads them.

    `read_all` normalises `\\r\\n` and `\\r` to `\\n` ("a foreign journal that
    separates records with a bare CR keeps reading"). Splitting on `\\n` alone
    measured a bare-CR journal as ONE malformed line: `records 0`, `projection
    .bytes 0` — a replayable journal reported as zero growth.
    """
    path = tmp_path / "graph.jsonl"
    records = [{"type": "PointAdded", "id": "p1"},
               {"type": "PointRevised", "id": "p1"}]
    data = separator.join(json.dumps(r).encode() for r in records) + separator
    path.write_bytes(data)
    total = measure(tmp_path)["total"]
    assert total["records"] == 2
    assert total["malformed"] == 0
    assert total["other_bytes"] == 0
    assert total["bytes"] == len(data)      # the separator bytes are counted


@pytest.mark.parametrize("marker", [SEAL_SENTINEL, SEAL_SENTINEL[:7]])
def test_seal_marker_is_an_annotation_even_when_torn(
        tmp_path: Path, marker: str) -> None:
    """A TORN seal is an annotation, never a malformed record.

    A crash can leave a prefix of the marker where the complete annotation was
    intended; `log.py::_is_seal_prefix` tolerates it because counting it would
    inflate the malformed figure the operator reads.
    """
    path = tmp_path / "graph.jsonl"
    fragment = '{"type": "PointAdded"'
    data = (json.dumps({"type": "PointAdded", "id": "p1"}) + "\n"
            + fragment + "\n" + marker + "\n").encode()
    path.write_bytes(data)
    total = measure(tmp_path)["total"]
    assert total["records"] == 1
    assert total["malformed"] == 1
    assert total["seal_annotations"] == 1
    assert total["other_bytes"] == ((len(fragment.encode()) + 1)
                                    + (len(marker.encode()) + 1))


def test_seal_prefix_is_classified_as_the_reader_classifies_it(
        tmp_path: Path) -> None:
    """A bare sentinel PREFIX is an annotation to the reader too — not a tear.

    `_is_seal` applies the prefix test to every non-empty line, while
    `log.py::_is_seal_prefix`'s docstring describes only the line FOLLOWING a
    torn fragment. The two look divergent and are not: `read_all` skips a
    COMPLETE marker before parsing, and every line it fails to parse reaches
    `_tolerate_or_raise`, whose FIRST arm is this same prefix test. So a stray
    prefix — no fragment before it — is tolerated by the reader as an
    annotation, and the measurer must agree or the `malformed` column
    over-reports corruption that a replay would not see.

    Pinned rather than narrated: a context-dependent `_is_seal` (the rule the
    prefix test's own docstring implies) would report ``malformed == 1`` here
    while `read_all` still replays the journal, so this test reds on the
    divergence instead of a comment asserting it does not exist.
    """
    from tortoise.log import EventLog

    path = tmp_path / "graph.jsonl"
    stray = SEAL_SENTINEL[:3]                    # a prefix, NOT valid JSON
    data = (json.dumps({"type": "PointAdded", "id": "p1"}) + "\n"
            + stray + "\n").encode()
    path.write_bytes(data)

    total = measure(path)["total"]
    assert total["records"] == 1
    assert total["malformed"] == 0               # not a torn RECORD fragment
    assert total["seal_annotations"] == 1        # the reader's verdict too
    assert total["other_bytes"] == len(stray.encode()) + 1
    assert total["bytes"] + total["other_bytes"] == len(data)

    # The same bytes, read by the journal's own reader: the record replays and
    # the stray line is tolerated (no ValueError, nothing extra emitted).
    assert EventLog(path).read_all() == [{"type": "PointAdded", "id": "p1"}]


def test_empty_journal_has_no_phantom_line(tmp_path: Path) -> None:
    """A 0-byte journal has no lines at all — not one empty line."""
    path = tmp_path / "graph.jsonl"
    path.write_bytes(b"")
    total = measure(tmp_path)["total"]
    assert total["records"] == 0
    assert total["empty_lines"] == 0
    assert total["file_bytes"] == 0
    assert total["bytes"] + total["other_bytes"] == 0


def test_the_tools_seal_grammar_is_the_journals_own() -> None:
    """The marker AND its classifier are imported from `tortoise.log`.

    A re-spelled literal of the same value would satisfy an equality pin while
    the grammar drifted, so the source itself is checked: no second copy of the
    marker, and the classification delegated to the journal's own predicate.
    """
    from tortoise.log import is_seal_annotation

    source = Path(journal_growth.__file__).read_text(encoding="utf-8")
    assert SEAL_SENTINEL not in source
    assert "_is_seal_prefix" in source
    assert is_seal_annotation(SEAL_SENTINEL)
    assert not is_seal_annotation(SEAL_SENTINEL[:7])


def test_text_form_total_is_the_sum_of_its_rows(
        tmp_path: Path, capsys) -> None:
    """The TOTAL row's `bytes` column is the sum of the rows above it.

    The rows print FILE bytes; `total['bytes']` counts only well-formed records,
    so on any journal with a torn tail / seal / blank line the total printed was
    SMALLER than the rows it totals — under a column headed `bytes`.
    """
    _write(tmp_path / "graph_a.jsonl", [{"type": "PointAdded", "id": "a"}])
    torn = '{"type": "PointAdded", "id": "c"'
    (tmp_path / "graph_b.jsonl").write_bytes(
        (json.dumps({"type": "PointAdded", "id": "b"}) + "\n"
         + torn + "\n").encode())
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    report = measure(tmp_path)
    file_total = sum(f["file_bytes"] for f in report["files"])
    assert report["total"]["bytes"] < file_total        # junk is present
    total_line = next(ln for ln in out.splitlines() if ln.startswith("TOTAL"))
    assert int(total_line.split()[2].replace(",", "")) == file_total


def test_probe_vector_width_is_derived_from_the_embedder(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe's vector width comes from `tortoise.embeddings` — never a literal.

    The probe STAMPS a vector and then PRINTS
    ``f"vector: {VECTOR_WIDTH}-d, {EMBEDDING_MODEL}"``. With ``VECTOR_WIDTH = 384``
    as a literal, rotating the embedder (``tortoise/embeddings.py``: "Rotating the
    embedder = editing this line") keeps stamping 384 floats while printing the
    NEW model's name — so the README, `STORAGE-ARCHITECTURE.md` §5.2 and the
    `docs/00_index.md` row publish a growth figure attributed to a model that does
    not produce it, ~2.7x low for a 1024-d model (the understating direction this
    instrument exists to avoid).

    Discriminating in the right direction: the width is read from the SINGLE
    SOURCE OF TRUTH after it is rotated, so a literal cannot pass. Pinning
    ``VECTOR_WIDTH == 384`` (literal against literal) could not fail this way —
    it is exactly what a rotated embedder leaves green.
    """
    from tortoise import embeddings

    def load_probe():
        spec = importlib.util.spec_from_file_location("corpus_growth_probe", PROBE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    assert load_probe().VECTOR_WIDTH == embeddings.EMBEDDING_DIM

    monkeypatch.setattr(embeddings, "EMBEDDING_DIM", 1024)
    rotated = load_probe()
    assert rotated.VECTOR_WIDTH == 1024
    # The width the probe stamps IS the width it prints and labels the report
    # with, so the three published figures cannot be attributed to the wrong
    # model.
    assert len(rotated.unit_vector()) == 1024
    assert rotated.stamp({"type": "PointAdded", "point": {"id": "p1"}},
                          rotated.unit_vector())["point"]["embedding"]


def test_probe_pins_its_published_outputs(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The evidence probe is a pinned artifact, not a dead file.

    `config/ci-surfaces.yml` selects `core` on a probe-only change, and the
    numbers `docs/evidence/5612-journal-growth/README.md` publishes are the
    probe's output. Without this pin the probe could drift while CI stayed green
    — the silent-drop class the CORE_ALSO entries exist to close.

    Pins the PUBLISHED FIGURES, not the helper shapes: every number the README,
    `STORAGE-ARCHITECTURE.md` §5.2 and the `docs/00_index.md` row quote is
    asserted here, so a probe change that moves one of them reds instead of
    leaving three documents publishing a figure the probe no longer produces.

    The `richest_by_type` pin is the one with a measured history: b08d526b6
    replaced last-wins (which published the 246 B minimal `PointAdded` — ~3.4%
    LOW, the understating direction this measurement exists to avoid) with
    fullest-wins. Nothing else in the repo calls that function, so without this
    assertion reverting it stays green.
    """
    assert PROBE.is_file()
    spec = importlib.util.spec_from_file_location("corpus_growth_probe", PROBE)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    assert probe.VECTOR_WIDTH == EMBEDDING_DIM    # the pinned embedder's width
    assert probe.CORPUS.is_file()                # the committed corpus

    records = [json.loads(line) for line in
               probe.CORPUS.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    by_type = probe.richest_by_type(records)
    vec = probe.unit_vector()

    # FULLEST-wins vs last-wins: last-wins measures `PointAdded`'s MINIMAL shape.
    last_wins: dict[str, dict] = {}
    for rec in records:
        if isinstance(rec.get("type"), str) and rec.get("type"):
            last_wins[rec["type"]] = rec
    assert probe.line_bytes(last_wins["PointAdded"]) == 246   # what the fix rejects
    assert probe.line_bytes(by_type["PointAdded"]) == 570     # what production writes

    # The published per-operation table (README / §5.2).
    plain_point = by_type["PointAdded"]
    stamped_point = probe.stamp(plain_point, vec)
    assert probe.line_bytes(stamped_point) == 9_176
    assert probe.line_bytes(by_type["PointRevised"]) == 196
    assert probe.line_bytes(by_type["EntityLinked"]) == 249
    assert probe.line_bytes(by_type["OperatorAdded"]) == 319
    assert probe.line_bytes(probe.stamp(by_type["OperatorAdded"], vec)) == 8_859
    assert probe.line_bytes(by_type["PointPromoted"]) == 228
    assert probe.line_bytes(probe.stamp(by_type["PointPromoted"], vec)) == 8_657

    # The vector's bytes, and the published decomposition of the 8,606 B
    # growth over the `as committed` record: the 8,414 B of vector text
    # REPLACES the committed 10 B placeholder `[0.1, 0.2]` (+8,404 B) and the
    # three #5004 identity keys add the remaining 202 B — not 8,414 + 204,
    # which would exceed the growth it claims to decompose.
    emb = stamped_point["point"]["embedding"]
    assert len(json.dumps(emb)) == 8_414
    growth = probe.line_bytes(stamped_point) - probe.line_bytes(plain_point)
    assert growth == 8_606
    vector_growth = (len(json.dumps(emb))
                     - len(json.dumps(plain_point["point"]["embedding"])))
    assert vector_growth == 8_404
    keys_added = sum(
        len(f', "{k}": {json.dumps(stamped_point["point"][k])}')
        for k in ("embedding_model", "embedding_revision", "embedding_text_hash"))
    assert keys_added == 202
    assert growth == vector_growth + keys_added

    # WHERE those 202 B come from, and why they are not a second charge for
    # keys the baseline already has: the committed record carries #5004's three
    # identity keys at its TOP LEVEL (the fixture's stub values), while `point`
    # carries none — and `stamp_journal_embedding` writes the identity INSIDE
    # the payload, which is where the producers put it. Pinning the placement is
    # what makes the NOTE in the probe's own output (`"... plus #5004's identity
    # keys at the record's TOP LEVEL ..."`) a checked claim instead of prose: a
    # fixture edit that dropped the baseline stubs, or a stamp that stopped
    # attesting inside `point`, reds here.
    for key in ("embedding_model", "embedding_revision", "embedding_text_hash"):
        assert key in plain_point               # baseline: at the record level
        assert key not in plain_point["point"]  # ... and NOT in the payload
        assert key in stamped_point["point"]    # the stamp adds the payload copy

    # The published whole-corpus figures, measured through the tool the probe
    # itself uses (`measure`), on the bytes the probe itself writes.
    committed_dir = tmp_path / "committed"
    hosted_dir = tmp_path / "hosted"
    probe.write_journal(committed_dir / "graph.jsonl", records)
    probe.write_journal(hosted_dir / "graph.jsonl",
                        [probe.stamp(rec, vec) for rec in records])
    base = measure(committed_dir)["total"]
    hosted = measure(hosted_dir)["total"]
    assert (base["records"], base["bytes"], base["record_bytes"]["p50"],
            base["record_bytes"]["p95"], base["record_bytes"]["max"]) == (
                39, 8_711, 218, 336, 570)
    assert (hosted["records"], hosted["bytes"], hosted["record_bytes"]["p50"],
            hosted["record_bytes"]["p95"], hosted["record_bytes"]["max"]) == (
                39, 51_346, 219, 8_877, 9_176)
    assert hosted["embedding_bytes"] == 42_070

    # Only the creating/promoted types are stamped (`PointRevised` is a delta).
    revised = probe.stamp({"type": "PointRevised", "point": {"id": "p1"}}, vec)
    assert "embedding" not in revised["point"]

    # And the probe's own printed report carries those figures, so a reader can
    # reproduce them from the console block the README pastes.
    assert probe.main() == 0
    out = capsys.readouterr().out
    assert "PointAdded           9,176 B  (as committed: 570 B)" in out
    assert "p50    219 B" in out                 # the corpus p50, published 3×
    assert "8,414 B (21.9 chars/float)" in out
    assert "42,070 / 51,346 B (81.9%)" in out
    # The NOTE the README pastes states WHERE the baseline's identity keys sit —
    # the placement the 202 B attribution above depends on.
    assert "#5004's identity keys at the record's TOP LEVEL" in out


@pytest.mark.parametrize("bad", ["not json at all", "[1, 2, 3]", "42", '"s"'])
def test_non_record_lines_are_malformed_not_records(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "graph.jsonl"
    path.write_bytes((bad + "\n").encode("utf-8"))
    total = measure(tmp_path)["total"]
    assert total["records"] == 0
    assert total["malformed"] == 1
    assert total["other_bytes"] > 0

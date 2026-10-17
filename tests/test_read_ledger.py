"""#7983 — the READ LEDGER: the instrument `f` (the hot fraction) never had.

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.4 closes the two-store arithmetic
except for two magnitudes. `f` — *the share of a tenant's stock the cache actually
holds* — is the split's **entire** saving (`(1 − f) × cache`), and a census of
this tree found nothing that could measure it: FalkorDB exposes no per-node
access statistics, the residency declaration is a fact about the projection's
WRITE work, `read_status.hit_count` is in flight and per request, and the
retrieval meter counts how MANY reads an org made, never WHICH bytes.

These tests pin the instrument's load-bearing properties — that `f` is a
division, that the zero-read mass IS the claim, and that the denominator cannot
be quietly shrunk:

MUTATION PROOF — each guard has been shown to MOVE (revert → verbatim pytest
output is in the PR body):

* **Let a window shrink the denominator** — fold only stock written inside the
  window → ``test_a_window_bounds_reads_and_never_shrinks_the_denominator`` RED
  (f would rise as the window shortens: the denominator error §17.4.2 records).
* **Let an unknown byte count be a zero** — treat ``nbytes is None`` as ``0`` →
  ``test_a_key_without_bytes_does_not_enter_the_byte_fraction`` RED (a byte
  fraction over a pretend denominator is not a measurement).
* **Drop reads outside the stock** — ignore a read the ledger never registered →
  ``test_unregistered_reads_are_counted_not_dropped`` RED.
* **Collapse the coordinate** — key rows by ``key`` alone →
  ``test_label_is_part_of_the_coordinate`` RED (``Subject.name`` and
  ``Object.name`` become one row, the fail-open ``declared_key`` forbids).
* **Let the bound read as quiet** — stop appending at the cap without a ``capped``
  line → ``test_the_size_bound_stops_recording_and_says_so`` RED.
* **Let the ledger break the read path** — re-raise an append error →
  ``test_a_failed_append_never_raises_into_the_read_path`` RED.
* **Stop recording reads from the funnel** — drop the call in
  ``sdk.tortoise_fts_query`` → ``test_the_funnel_records_each_returned_key`` RED.

They run on the embedded carve-out (``TORTOISE_TEST_CARVE_OUT=1``) or a
``TORTOISE_DB_URI`` (``tests/conftest.py`` session gate).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from tortoise import read_ledger
from tortoise.read_ledger import (
    KEY_FIELDS,
    LEDGER_FLAG_ENV,
    LEDGER_MAX_BYTES_ENV,
    LEDGER_PATH_ENV,
    fold,
    hot_fraction,
    record_read,
    record_stock,
    row_key,
)

ROOT = Path(__file__).resolve().parents[1]


def _no_embedder(monkeypatch):
    """The dense leg cannot run — the #2573/#2898 degrade class."""
    from tortoise.embeddings import EmbeddingModel

    monkeypatch.setattr(EmbeddingModel, "get", classmethod(lambda cls, load_timeout=None: None))


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """An isolated ledger file per test — no test can read another's window."""
    path = tmp_path / "read_ledger.jsonl"
    monkeypatch.setenv(LEDGER_PATH_ENV, str(path))
    return path


# ── `f` is a DIVISION ───────────────────────────────────────────────────────


def test_f_is_a_division_with_its_zero_read_mass(ledger):
    """f, both denominators, and the mass the split is actually paid for."""
    for key, nbytes in (("p1", 1000), ("p2", 1000), ("p3", 1000), ("p4", 1000)):
        record_stock("Point", key, nbytes=nbytes)
    record_read("Point", "p1")

    f = hot_fraction(fold(ledger))
    assert f.stock_keys == 4 and f.read_keys == 1
    assert f.f_keys == pytest.approx(0.25)
    assert f.stock_bytes == 4000 and f.read_bytes == 1000
    assert f.f_bytes == pytest.approx(0.25)
    # THE CLAIM, stated as bytes: three quarters of the stock costs RAM and was
    # never read in the window.
    assert f.zero_read_keys == 3
    assert f.zero_read_bytes == 3000


def test_no_reads_at_all_is_f_zero_not_f_missing(ledger):
    """A ledger that observed nothing but reads has to say `0`, not `n/a`.

    That is the honest reading of "the stock exists and none of it was read
    inside the window"; the WINDOW is what the caller must state alongside it.
    """
    record_stock("Point", "p1", nbytes=10)
    f = hot_fraction(fold(ledger))
    assert f.f_keys == 0.0
    assert f.f_bytes == 0.0
    assert f.zero_read_bytes == 10


def test_an_empty_stock_has_no_fraction(ledger):
    f = hot_fraction(fold(ledger))
    assert f.stock_keys == 0
    assert f.f_keys is None and f.f_bytes is None


# ── the DENOMINATOR cannot be quietly shrunk ────────────────────────────────


def test_a_window_bounds_reads_and_never_shrinks_the_denominator(ledger):
    """An old read exits the NUMERATOR; the key never exits the DENOMINATOR.

    Otherwise f would rise as the window shortens — a cache would look hotter
    the less it was observed, which is the denominator error §17.4.2 records
    three times in this very document.
    """
    record_stock("Point", "old", nbytes=500, at="2026-01-01T00:00:00+00:00")
    record_stock("Point", "new", nbytes=500, at="2026-10-17T00:00:00+00:00")
    record_read("Point", "old", at="2026-01-02T00:00:00+00:00")
    record_read("Point", "new", at="2026-10-17T12:00:00+00:00")

    whole = hot_fraction(fold(ledger))
    assert (whole.stock_keys, whole.read_keys) == (2, 2)

    windowed = hot_fraction(fold(ledger, window_days=7, now=datetime(2026, 10, 17, 13, tzinfo=UTC)))
    assert windowed.stock_keys == 2, "the stock is everything that EXISTS"
    assert windowed.read_keys == 1, "only the in-window read counts"
    assert windowed.stock_bytes == 1000 and windowed.read_bytes == 500


def test_unregistered_reads_are_counted_not_dropped(ledger):
    """A read of a key outside the stock is a HOLE, and it is reported."""
    record_stock("Point", "known", nbytes=100)
    record_read("Point", "known")
    record_read("Point", "never-registered")

    f = hot_fraction(fold(ledger))
    assert f.stock_keys == 1 and f.read_keys == 1
    assert f.unregistered_reads == 1


def test_a_key_without_bytes_does_not_enter_the_byte_fraction(ledger):
    """`bytes=None` is NOT zero — the key fraction still counts it."""
    record_stock("Point", "sized", nbytes=900)
    record_stock("Point", "unsized")
    record_read("Point", "unsized")

    f = hot_fraction(fold(ledger))
    assert f.stock_keys == 2 and f.f_keys == pytest.approx(0.5)
    assert f.bytes_known_fraction == pytest.approx(0.5)
    assert f.stock_bytes == 900  # only the sized key
    assert f.read_bytes == 0  # the read key has no byte figure
    # `f_bytes` is 0.0 over its own sub-population — and it is NOT a reading of
    # the reads, because the one key that WAS read carries no bytes. The flag is
    # what stops that 0.0 being quoted as `f`.
    assert f.f_bytes == 0.0
    assert f.f_bytes_qualified is False

    # …and with NO sized key at all there is no byte fraction to quote — a
    # distinct ledger, because a byte fraction is a property of its own keys.
    other = ledger.with_name("no-bytes.jsonl")
    record_stock("Point", "another-unsized", path=other)
    f2 = hot_fraction(fold(other))
    assert f2.stock_keys == 1
    assert f2.f_bytes is None and f2.bytes_known_fraction == 0.0


# ── the coordinate ─────────────────────────────────────────────────────────


def test_a_read_recorded_before_its_stock_still_counts(ledger):
    """File order must not decide the answer.

    Reads accumulate from the moment the flag is set while a stock census is a
    separate, later step — so the natural order is read-first. A single-pass fold
    would drop that read and report the key as "never read", moving `f` in the
    FLATTERING direction (more zero-read mass = a bigger claimed saving).
    """
    record_read("Point", "p1", at="2026-10-17T00:00:01+00:00")
    record_stock("Point", "p1", nbytes=1000, at="2026-10-17T00:00:02+00:00")

    folded = fold(ledger)
    assert folded.rows[(None, "Point", "p1")].read_count == 1
    f = hot_fraction(folded)
    assert f.f_keys == 1.0 and f.zero_read_bytes == 0
    assert f.unregistered_reads == 0


def test_a_read_for_a_key_never_registered_still_reports_unregistered(ledger):
    """The join is per ident — an absent stock row stays a hole, not a pass."""
    record_read("Point", "ghost")
    record_stock("Point", "real", nbytes=10)
    folded = fold(ledger)
    assert folded.unregistered_reads == 1
    f = hot_fraction(folded)
    assert f.read_keys == 0 and f.zero_read_keys == 1


def test_a_subject_row_keyed_only_by_id_is_unresolved_not_mis_keyed(ledger):
    """`Subject`/`Object` are keyed by `name` in the record; an `id`-only row is a HOLE.

    Recording such a row under `id` would put it outside the stock coordinate
    (registered by `name`) and leave the key at `read_count = 0` — silently
    inflating the zero-read mass for exactly the labels the label coordinate
    exists for. It is counted instead.
    """
    assert row_key("Subject", {"id": "abc", "content": "x"}) is None
    assert row_key("Subject", {"name": "acme", "id": "abc"}) == "acme"
    recorded = read_ledger.record_rows("Subject", [{"id": "abc", "content": "x"}])
    assert recorded == 0
    assert fold(ledger).unresolved_reads == 1


def test_the_ledger_file_is_created_unreadable_to_others(ledger):
    """It holds tenant identifiers — 0600 on create, not the umask's default."""
    record_stock("Subject", "acme", nbytes=10)
    assert (ledger.stat().st_mode & 0o777) == 0o600


def test_a_batch_is_one_append(ledger, monkeypatch):
    """The instrument may not slow the path it measures: ONE write per batch."""
    writes: list[bytes] = []
    real_write = read_ledger.os.write

    def counting(fd, blob):
        writes.append(blob)
        return real_write(fd, blob)

    monkeypatch.setattr(read_ledger.os, "write", counting)
    read_ledger.record_rows("Point", [{"id": f"p{i}"} for i in range(200)])
    assert len(writes) == 1
    # All 200 lines landed in that one write; none of the keys was ever
    # registered as stock, so every one is a reported hole.
    assert fold(ledger).unregistered_reads == 200


def test_rotation_restarts_recording(ledger, monkeypatch):
    """The cap is a warn-once latch on the FILE, never a permanent stop.

    The warning tells an operator to rotate the ledger. If the latch outlived
    the file, the rotated ledger would record nothing for the rest of the
    process and its first report would read `f = 0` — "nothing has been read
    since we started watching" misread as "nothing is read".
    """
    monkeypatch.setattr(read_ledger, "_max_bytes", lambda: 400)
    for i in range(50):
        read_ledger.record_stock("Point", f"p{i}", nbytes=10, path=ledger)
    assert fold(ledger).capped is True  # the cap fired and said so
    # ── rotate: the operator truncates/replaces the file ────────────────────
    ledger.write_text("")
    assert read_ledger.record_stock("Point", "p1", nbytes=10, path=ledger) is True
    assert read_ledger.record_read("Point", "p1", path=ledger) is True
    assert fold(ledger).rows[(None, "Point", "p1")].read_count == 1


def test_an_existing_world_readable_ledger_is_tightened(ledger):
    """0600 on create cannot tighten an inode that already exists."""
    ledger.write_text("")  # created by the OS default mode here
    os.chmod(ledger, 0o644)
    read_ledger.record_stock("Subject", "acme", nbytes=10, path=ledger)
    assert (ledger.stat().st_mode & 0o777) == 0o600


def test_an_operator_is_recorded_as_a_point():
    """`operator` is a caller-facing alias, not a graph label.

    Operators are `:Point` nodes with `is_operator = true`. Recording them under
    `("Operator", id)` would be a coordinate the stock (registered as `Point`)
    can never join, so their reads would land in `unregistered` and the nodes
    would stay in the never-read mass — the flattering direction.
    """
    assert read_ledger.label_for_entity_type("operator") == "Point"
    assert read_ledger.label_for_entity_type("document") == "Source"
    # …and the aliases land on a label the key contract knows.
    assert "Point" in read_ledger.KEY_FIELDS
    assert "Source" in read_ledger.KEY_FIELDS
    # Every other caller-facing type is its own label.
    for et in ("point", "event", "subject", "object", "source"):
        assert read_ledger.label_for_entity_type(et) == et.capitalize()


@pytest.fixture
def hooked_sdk(ledger, monkeypatch):
    """An SDK whose store is a stub, with the ledger flag ON.

    The three structural read paths are ordinary SDK methods over
    `proj.g.query(...).result_set`, so a stub projection is enough to prove the
    HOOK fires — which is the part that rots silently if a path is refactored.
    """
    from tortoise import sdk as sdk_module

    class _Res:
        def __init__(self, rows):
            self.result_set = rows

    class _Graph:
        rows: ClassVar[list] = []

        def query(self, cypher, params=None, **kwargs):
            if "count(n)" in cypher:
                return _Res([[2]])
            return _Res([(dict(row),) for row in self.rows])

    class _Proj:
        g = _Graph()

    monkeypatch.setenv("TORTOISE_READ_LEDGER", "1")
    inst = sdk_module.TortoiseSDK.__new__(sdk_module.TortoiseSDK)
    monkeypatch.setattr(inst, "_get_proj", lambda: _Proj, raising=False)
    monkeypatch.setattr(inst, "_expand_kind", lambda kind: [kind], raising=False)
    return inst, _Graph


def test_every_row_returning_read_path_feeds_the_ledger(hooked_sdk):
    """A hook that is not exercised is a hook that will be lost in a refactor.

    Each of `query()`, `get_point()` and `query_points_by_tag()` returns Point
    rows, so each must record exactly ONE read per returned key — no more (a
    double record inflates `f`) and no fewer (a missed record inflates the
    never-read mass, which flatters the split).
    """
    inst, graph = hooked_sdk
    # The keys must exist as STOCK first, or every read is (correctly) reported
    # as `unregistered` and there is no row to count on.
    for key in ("p1", "p2", "p3", "p7", "p8", "p9"):
        record_stock("Point", key, nbytes=100)

    graph.rows = [{"id": "p1"}, {"id": "p2"}]
    got = inst.query()
    assert [r["id"] for r in got] == ["p1", "p2"]

    graph.rows = [{"id": "p3"}]
    assert inst.paginated_query()["results"] == [{"id": "p3"}]

    graph.rows = [{"id": "p7"}]
    assert inst.get_point("p7") == {"id": "p7"}

    graph.rows = [{"id": "p8"}, {"id": "p9"}]
    inst.query_points_by_tag("t")

    folded = fold()
    counts = {k[2]: row.read_count for k, row in folded.rows.items()}
    # read_count == 1, not >= 1: a DOUBLE record would inflate `f`.
    assert counts == {"p1": 1, "p2": 1, "p3": 1, "p7": 1, "p8": 1, "p9": 1}
    assert folded.unresolved_reads == 0


def test_the_ledger_module_is_not_imported_when_the_flag_is_off():
    """ "Off by default" must mean NOT IMPORTED, not "imported and idle".

    Checked in a subprocess because any earlier test may have imported it: the
    claim is about a deployment that never sets the flag, not about this process.
    """
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, tortoise.sdk; print('tortoise.read_ledger' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "TORTOISE_READ_LEDGER": ""},
        cwd=str(ROOT),
        check=True,
    )
    assert out.stdout.strip() == "False", out.stderr


def test_label_is_part_of_the_coordinate(ledger):
    """One name, two labels, two rows — the `declared_key` rule, for `f`."""
    record_stock("Subject", "acme", nbytes=10)
    record_stock("Object", "acme", nbytes=10)
    record_read("Subject", "acme")

    f = hot_fraction(fold(ledger))
    assert f.stock_keys == 2 and f.read_keys == 1
    assert f.f_keys == pytest.approx(0.5)


def test_key_fields_match_the_record_identity_contract():
    """The ledger's key map is the RECORD's `node_identity.key_property`.

    One home beats a copy: if the record's contract changes, this test fails
    rather than the ledger silently recording a key nobody can join on.
    """
    sql = (ROOT / "supabase/migrations/20261012000001_record_event_stream.sql").read_text()
    block = sql.split("key_property text NOT NULL GENERATED ALWAYS AS (", 1)[1]
    block = block.split(") STORED", 1)[0]
    pairs = dict(re.findall(r"WHEN\s+'(\w+)'\s+THEN\s+'(\w+)'", block))
    assert pairs == KEY_FIELDS


def test_row_key_resolves_each_label_and_reports_the_unaddressable():
    assert row_key("Point", {"id": "p1"}) == "p1"
    assert row_key("Event", {"eventId": "e1"}) == "e1"
    assert row_key("Subject", {"name": "acme"}) == "acme"
    assert row_key("Source", {"url": "https://x/y"}) == "https://x/y"
    # A row carrying no key at all is not silently dropped: it is `None`, which
    # the fold counts as unresolved rather than as a read of nothing.
    assert row_key("Point", {"content": "no key here"}) is None


# ── failure directions ─────────────────────────────────────────────────────


def test_the_size_bound_stops_recording_and_says_so(ledger, monkeypatch):
    """A capped ledger is TRUNCATED, never quiet."""
    monkeypatch.setenv(LEDGER_MAX_BYTES_ENV, "300")
    for i in range(40):
        record_stock("Point", f"p{i}", nbytes=1)
        record_read("Point", f"p{i}")
    assert ledger.exists()
    lines = [json.loads(line) for line in ledger.read_text().splitlines() if line]
    assert any(line["event"] == read_ledger.EVENT_CAPPED for line in lines)
    assert ledger.stat().st_size < 2000, "the bound held"
    assert fold(ledger).capped is True


def test_a_failed_append_never_raises_into_the_read_path(tmp_path):
    """A measurement may not break the thing it measures."""
    # A directory is not appendable — the write fails, the caller does not.
    d = tmp_path / "not-a-file"
    d.mkdir()
    assert read_ledger.record_read("Point", "p1", path=d) is False
    assert read_ledger.record_rows("Point", [{"id": "p1"}], path=d) == 0


def test_a_row_with_no_key_is_counted_as_unresolved_not_dropped(ledger):
    """A read the ledger cannot ADDRESS is a hole, and it is published as one.

    Dropping it silently would let the report claim a completeness it never had:
    the row was returned by a read, it simply has no coordinate to record.
    """
    recorded = read_ledger.record_rows("Point", [{"id": "p1"}, {"content": "no key on this row"}])
    assert recorded == 1
    folded = fold(ledger)
    assert folded.stock_keys == 0  # nothing was registered as stock
    assert folded.unresolved_reads == 1
    assert folded.unregistered_reads == 1  # the resolvable read, not registered
    kinds = {json.loads(line)["event"] for line in ledger.read_text().splitlines()}
    assert read_ledger.EVENT_UNRESOLVED in kinds


def test_a_torn_line_is_skipped_and_counted(ledger):
    record_stock("Point", "p1", nbytes=10)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    folded = fold(ledger)
    assert folded.lines_skipped == 1
    assert folded.stock_keys == 1


# ── the funnel that feeds it ───────────────────────────────────────────────


class TestTheReadFunnel:
    def test_the_funnel_records_each_returned_key(self, sdk_factory, ledger, monkeypatch):
        monkeypatch.setenv(LEDGER_FLAG_ENV, "1")
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            created = sdk.create_point("statement", "alpha beta gamma waves")
            read_ledger.record_stock("Point", created["id"], nbytes=1672)
            rows = sdk.tortoise_fts_query("alpha", limit=5)
            assert rows
            folded = fold(ledger)
            assert folded.stock_keys == 1
            assert folded.rows[(None, "Point", created["id"])].read_count >= 1
            f = hot_fraction(folded)
            assert f.f_keys == 1.0 and f.f_bytes == 1.0
        finally:
            sdk.close()

    def test_the_ledger_writes_nothing_with_the_flag_off(self, sdk_factory, ledger, monkeypatch):
        monkeypatch.delenv(LEDGER_FLAG_ENV, raising=False)
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            rows = sdk.tortoise_fts_query("alpha", limit=5)
            assert rows
            assert not ledger.exists(), "off means off — not one line is written"
        finally:
            sdk.close()

    def test_the_answer_is_byte_identical_with_the_ledger_on(
        self, sdk_factory, ledger, monkeypatch
    ):
        """The instrument may not change what it measures."""
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            monkeypatch.delenv(LEDGER_FLAG_ENV, raising=False)
            off = sdk.tortoise_fts_query("alpha", limit=5)
            monkeypatch.setenv(LEDGER_FLAG_ENV, "1")
            on = sdk.tortoise_fts_query("alpha", limit=5)
            assert [r["id"] for r in off] == [r["id"] for r in on]
            assert [r["content"] for r in off] == [r["content"] for r in on]
        finally:
            sdk.close()


def test_the_cli_reports_f_with_its_window(ledger, capsys):
    record_stock("Point", "p1", nbytes=100)
    record_stock("Point", "p2", nbytes=100)
    record_read("Point", "p1")
    rc = read_ledger._main(["--ledger", str(ledger), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["f_keys"] == pytest.approx(0.5)
    assert payload["zero_read_bytes"] == 100
    assert payload["window_started"] is None


def test_a_missing_ledger_is_not_an_empty_reading(tmp_path, capsys):
    """`stock 0 keys` from a path typo reads exactly like "nothing registered"."""
    rc = read_ledger._main(["--ledger", str(tmp_path / "absent.jsonl")])
    assert rc == 2
    out = capsys.readouterr().out
    assert "no ledger at" in out
    assert "stock" not in out


def test_a_torn_line_is_counted_and_reported(ledger, capsys):
    """A half-written line is a LOST EVENT, not a quiet one."""
    record_stock("Point", "p1", nbytes=100)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write('{"at": "2026-10-17T00:00:00+00:00", "event": "st')  # torn
    rc = read_ledger._main(["--ledger", str(ledger)])
    assert rc == 0
    assert "1 unreadable line(s) skipped" in capsys.readouterr().out


def test_a_second_cap_on_a_rotated_ledger_still_says_so(ledger, monkeypatch):
    """Rotation must not silence the marker: the SECOND cap has to report too.

    Otherwise a rotated ledger that fills again stops recording with
    `capped: false` — a truncated window reported as a quiet one.
    """
    monkeypatch.setattr(read_ledger, "_max_bytes", lambda: 400)
    for i in range(50):
        read_ledger.record_stock("Point", f"p{i}", nbytes=10, path=ledger)
    assert fold(ledger).capped is True
    ledger.write_text("")  # rotate
    for i in range(50):
        read_ledger.record_stock("Point", f"q{i}", nbytes=10, path=ledger)
    assert fold(ledger).capped is True, "the second cap must write its own marker"

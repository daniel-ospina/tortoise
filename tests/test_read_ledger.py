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


def test_the_three_caller_facing_row_paths_feed_the_ledger(hooked_sdk):
    """A hook that is not exercised is a hook that will be lost in a refactor.

    ⛔ THIS PINS THREE OF THE HOOKED SDK METHODS, and its name says so. The other
    SDK methods recorded in §17.4.6 are not exercised here, and neither are the
    two NON-SDK hooks (the session-start read, and the MCP tag tool's served
    page): deleting any of those calls leaves this file green, no test drives
    them, and §17.4.6 names the set. The former name
    (`test_every_row_returning_read_path_feeds_the_ledger`) promised coverage it
    did not have, so the claim is narrowed to what the body drives rather than
    the guard being trusted on its title.

    Each of `query()`, `paginated_query()` and `query_points_by_tag()` returns
    Point rows to a CALLER, so each must record exactly ONE read event per
    returned key — no fewer (a missed record inflates the never-read mass, which
    flatters the split) and no more (a double record overstates how many reads a
    key gets, which is the same class of wrong as a lost one).
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
    # `p7` stays at ZERO: `get_point` is the internal accessor the write paths
    # use, so it must record NOTHING — if it ever starts recording, a
    # written-but-never-read node looks read and `f` is biased HIGH.
    assert counts == {"p1": 1, "p2": 1, "p3": 1, "p7": 0, "p8": 1, "p9": 1}
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
            # The WHOLE row, not two fields: a hook that added, dropped or
            # mutated any key would still pass an id/content comparison, and the
            # claim this test names is byte-identity.
            assert json.dumps(off, sort_keys=True, default=str) == json.dumps(
                on, sort_keys=True, default=str
            )
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


def test_a_corrupt_bytes_value_is_a_skipped_line_not_a_crash(ledger):
    """A hand-edited field must not take the report down.

    The module's contract is that a bad line is COUNTED (`lines_skipped`) and the
    window declared incomplete — not that one non-numeric `bytes` raises out of
    `fold()` and out of the CLI.
    """
    read_ledger.record_stock("Point", "p1", nbytes=10, path=ledger)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(
            '{"at":"2026-01-01T00:00:00+00:00","event":"stock","label":"Point","key":"p2","bytes":"lots"}\n'
        )
    folded = fold(ledger)
    assert folded.stock_keys == 2, "the EVENT is not lost: both keys are registered"
    assert folded.rows[(None, "Point", "p2")].nbytes is None, "unknown width, not a zero"
    assert folded.lines_skipped == 0, "the line itself was readable"
    assert hot_fraction(folded).bytes_known_stock_keys == 1, (
        "the byte denominator reports its own coverage rather than absorbing the field"
    )


def test_each_store_target_gets_its_own_default_ledger(tmp_path, monkeypatch):
    """Two stores on one host must not share a default ledger.

    `resolve_db_path()` has no local file for a server URI and falls through to
    the embedded default, so a path derived from run name alone would pool every
    graph into one file with `tenant = None` — and `fold` would union their stocks
    and read sets, biasing `f` DOWN (the flattering direction).
    """
    monkeypatch.delenv("TORTOISE_READ_LEDGER_PATH", raising=False)
    import tortoise.config as config

    monkeypatch.setattr(config, "resolve_db_path", lambda *a, **k: tmp_path / "graph.db")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@localhost:6379/one")
    first = read_ledger.ledger_path()
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@localhost:6379/two")
    second = read_ledger.ledger_path()
    assert first != second, "two targets, two files"
    assert first.parent == second.parent == tmp_path
    # A shared SERVER holds many graphs and `graph_name=` never reaches
    # `resolve_db_path()`, so the env names the graph when a deployment states it.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@localhost:6379/one")
    monkeypatch.setenv("TORTOISE_GRAPH_NAME", "graph-a")
    graph_a = read_ledger.ledger_path()
    monkeypatch.setenv("TORTOISE_GRAPH_NAME", "graph-b")
    assert read_ledger.ledger_path() != graph_a, "two graphs on one server, two files"
    monkeypatch.delenv("TORTOISE_GRAPH_NAME", raising=False)

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    embedded = read_ledger.ledger_path()
    assert embedded != first, "the embedded store is its own target"
    assert read_ledger.ledger_path() == embedded, "and it is STABLE across calls"


def test_f_is_folded_per_tenant(ledger):
    """`f` is a PER-TENANT ratio: `(1 − f) × cache` is one tenant's saving.

    A ledger that unions tenants answers a question nobody asked, and no field
    said which tenant the number described.
    """
    for tenant in ("a", "b"):
        for i in range(4):
            read_ledger.record_stock(
                "Point", f"{tenant}{i}", nbytes=10, tenant_id=tenant, path=ledger
            )
    read_ledger.record_rows("Point", [{"id": "a0"}, {"id": "a1"}], tenant_id="a", path=ledger)
    read_ledger.record_rows("Point", [{"id": "b0"}], tenant_id="b", path=ledger)

    a = hot_fraction(fold(ledger, tenant="a"))
    b = hot_fraction(fold(ledger, tenant="b"))
    assert (a.stock_keys, a.read_keys) == (4, 2)
    assert (b.stock_keys, b.read_keys) == (4, 1)
    assert a.f_keys == 0.5 and b.f_keys == 0.25
    both = hot_fraction(fold(ledger))
    assert both.stock_keys == 8, "no filter still folds the whole file"


def test_the_report_names_the_files_own_start(ledger, capsys):
    """A rotated ledger must not read as a long, quiet window.

    Rotation restarts the file, and the only evidence of the earlier window left
    behind is that the earliest event is recent. The report has to say so.
    """
    read_ledger.record_stock("Point", "p1", nbytes=10, path=ledger)
    first = fold(ledger).first_event_at
    assert first is not None
    assert read_ledger._main(["--ledger", str(ledger)]) == 0
    out = capsys.readouterr().out
    assert "file spans from" in out and first in out


def test_a_capped_file_still_says_so_when_one_tenant_is_folded(ledger, monkeypatch):
    """The `capped` marker is a FILE fact and carries no tenant.

    Filtering it out with everything else would report a truncated ledger as a
    quiet one on exactly the per-tenant path `--tenant` added.
    """
    monkeypatch.setattr(read_ledger, "_max_bytes", lambda: 300)
    for i in range(40):
        read_ledger.record_stock("Point", f"p{i}", nbytes=10, tenant_id="t1", path=ledger)
    assert fold(ledger).capped is True
    assert fold(ledger, tenant="t1").capped is True, "the truncation is file-level"
    assert fold(ledger, tenant="someone-else").capped is True


def test_an_internal_population_scan_is_not_recorded_as_a_read(hooked_sdk):
    """`recall_gaps` / `issue_insight` / the FTS fallback scan the WHOLE stock.

    They call `query()` to do it, so recording them would mark every Point read
    after one `recall_gaps()` call — `f` → ~1, the exact contamination excluding
    `get_point` prevented, and it would silently invalidate the one-directional
    "the gaps bias `f` LOW" caveat the docs rest on.
    """
    inst, graph = hooked_sdk
    record_stock("Point", "p1", nbytes=100)
    record_stock("Point", "p2", nbytes=100)

    graph.rows = [{"id": "p1"}, {"id": "p2"}]
    assert [r["id"] for r in inst.query(_record_reads=False)] == ["p1", "p2"]
    assert {row.read_count for row in fold().rows.values()} == {0}, (
        "an internal scan must record NOTHING"
    )

    graph.rows = [{"id": "p1"}]
    inst.query()
    counts = {k[2]: row.read_count for k, row in fold().rows.items()}
    assert counts == {"p1": 1, "p2": 0}, "the caller-facing default still records"


def test_the_mode_tightening_skips_a_directory(tmp_path, monkeypatch):
    """Pointing the env var at a DIRECTORY must not rewrite that directory's mode.

    `chmod 0600` on a directory removes group/other traverse — an unrelated,
    destructive side effect of a best-effort measurement.
    """
    victim = tmp_path / "not-a-ledger"
    victim.mkdir()
    victim.chmod(0o755)
    assert read_ledger.record_stock("Point", "p1", nbytes=10, path=victim) is False
    assert victim.stat().st_mode & 0o777 == 0o755, "someone else's directory, untouched"


def test_a_json_line_that_is_not_an_object_is_skipped_not_fatal(ledger, capsys):
    """`null`, `[1,2]` and `7` are valid JSON and not EVENTS.

    A merged or hand-edited file can leave one; `.get` on it would raise straight
    out of a read-only report (and, in the tail probe, out of the APPEND path).
    """
    read_ledger.record_stock("Point", "p1", nbytes=10, path=ledger)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write("null\n[1,2,3]\n7\n")
    folded = fold(ledger)
    assert folded.stock_keys == 1
    assert folded.lines_skipped == 3
    assert read_ledger._main(["--ledger", str(ledger)]) == 0
    assert "unreadable line(s) skipped" in capsys.readouterr().out


def test_an_unknown_tenant_is_not_an_empty_reading(ledger, capsys):
    """A typo'd `--tenant` must not report "0 keys, f n/a" over someone's events."""
    read_ledger.record_stock("Point", "p1", nbytes=10, tenant_id="real", path=ledger)
    read_ledger.record_rows("Point", [{"id": "p1"}], tenant_id="real", path=ledger)
    assert read_ledger._main(["--ledger", str(ledger), "--tenant", "typo"]) == 2
    out = capsys.readouterr().out
    assert "no events for tenant 'typo'" in out
    assert "other tenant(s)" in out


def test_a_short_write_reports_only_the_whole_lines(ledger, monkeypatch):
    """A partial `os.write` must not be reported as a full batch.

    The module's failure story is "a truncated window is never reported as
    quiet": returning `len(lines)` for a batch whose tail never reached the file
    is exactly that lie.
    """
    real_write = os.write
    calls = {"n": 0}

    def half_write(fd, data):  # write at most 20 B per call, then "fail"
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError(28, "No space left on device")
        return real_write(fd, data[:20])

    monkeypatch.setattr(read_ledger.os, "write", half_write)
    written = read_ledger.record_rows("Point", [{"id": f"p{i}"} for i in range(10)], path=ledger)
    assert written == 0, "20 B is a torn first line — no whole event was recorded"


def test_short_writes_are_looped_so_nothing_is_lost(ledger, monkeypatch):
    """A filesystem that accepts 7 B at a time must still get the whole batch."""
    real_write = os.write

    def dribble(fd, data):
        return real_write(fd, data[:7])

    monkeypatch.setattr(read_ledger.os, "write", dribble)
    assert read_ledger.record_rows("Point", [{"id": f"p{i}"} for i in range(5)], path=ledger) == 5
    folded = fold(ledger)
    assert folded.lines_read == 5, "every line reached the file"
    assert folded.unregistered_reads == 5, "and all five are READ events (no stock here)"


def test_a_torn_batch_reports_no_provable_key(ledger, monkeypatch):
    """A write that STOPS (0 bytes, no exception) must not be reported as recorded.

    The caller cannot attribute whole lines to keys, so the honest answer is 0 —
    and the flip back to the intended count is what this pins.
    """
    real_write = os.write
    state = {"n": 0}

    def stop_after_one(fd, data):
        state["n"] += 1
        return real_write(fd, data[:9]) if state["n"] == 1 else 0

    monkeypatch.setattr(read_ledger.os, "write", stop_after_one)
    assert read_ledger.record_rows("Point", [{"id": f"p{i}"} for i in range(5)], path=ledger) == 0


def test_an_unresolvable_home_in_the_env_never_breaks_the_read_path(monkeypatch):
    """`~nosuchuser` must be a swallowed warning, not a `RuntimeError` in a read."""
    monkeypatch.setenv("TORTOISE_READ_LEDGER", "1")
    monkeypatch.setenv("TORTOISE_READ_LEDGER_PATH", "~nosuchuser-7983/ledger.jsonl")
    assert read_ledger.record_rows("Point", [{"id": "p1"}]) == 0


def test_an_over_long_line_is_dropped_loudly_and_not_counted(ledger):
    """MAX_LINE_BYTES is a torn-record bound; a dropped line is not a written one."""
    assert read_ledger.record_rows("Point", [{"id": "x" * 5000}], path=ledger) == 0
    assert fold(ledger).stock_keys == 0


def test_the_funnel_does_not_import_the_ledger_unless_the_flag_is_set():
    """The BEHAVIOURAL half of "off means not imported": flag read first.

    A source census cannot see a pool site that omits the flag; this pins the
    ordering the module promises (one env lookup, then a guarded import).
    """
    src = (Path(__file__).resolve().parents[1] / "tortoise" / "sdk.py").read_text()
    # rindex: the helper BEFORE the funnel has its own flag check, so `index`
    # resolved to that one and the assertion held whatever the funnel did.
    flag_at = src.rindex('is_truthy(os.environ.get("TORTOISE_READ_LEDGER"))')
    import_at = src.rindex("from . import read_ledger as _read_ledger")
    assert flag_at < import_at, "the flag must be read before the import"
    assert "_read_ledger_enabled:" in src, "the import is guarded by the flag"
    assert flag_at < import_at


def test_the_internal_readers_pass_record_reads_false():
    """A CENSUS, not an invariant: the call sites are not driven by fixtures.

    Deleting one of these arguments leaves every behavioural test green — the
    sites are population scans and candidate pools — so their presence is pinned
    here. The count is a FLOOR on purpose: a new pool reader should add a site,
    not turn this red.
    """
    root = Path(__file__).resolve().parents[1] / "tortoise"
    src = (root / "sdk.py").read_text()
    sites = [ln for ln in src.splitlines() if ln.strip().startswith("_record_reads=False")]
    assert len(sites) >= 10, sites
    # The three functions that accept the flag (query, the FTS funnel, the tag
    # accessor): a new one is fine, losing one is not.
    assert src.count("_record_reads: bool = True") >= 3, "query / funnel / tag accessor"
    assert (
        "session_continuity.py"
        and "_record_reads=False" in (root / "session_continuity.py").read_text()
    )
    # The extractor's link-before-create prior is a write-path self-read.
    assert "_record_reads=False" in (root / "extractor_v2.py").read_text()


def test_an_unknown_tenant_with_the_env_path_does_not_crash(tmp_path, monkeypatch, capsys):
    """The guard must run on the RESOLVED path — `--ledger` is optional.

    Round 9 passed `a.ledger` (None by default) to `Path()` and crashed on the
    DEFAULT invocation, i.e. on exactly the case the guard exists for.
    """
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv(LEDGER_PATH_ENV, str(path))
    read_ledger.record_stock("Point", "p1", nbytes=10, tenant_id="real", path=path)
    assert read_ledger._main(["--tenant", "typo"]) == 2
    assert "no events for tenant 'typo'" in capsys.readouterr().out


def test_a_torn_batch_reports_the_whole_lines_that_landed(ledger, monkeypatch):
    """A PREFIX that lands must be counted — not rounded to zero, not to the batch.

    The earlier test's write RAISED on the second call, which short-circuits to
    the `except` and returns 0 without ever exercising the prefix arithmetic
    (`blob[:written].count(b"\\n")`). Here the second write returns 0, which is
    the real short-write shape.
    """
    real_write = os.write
    state = {"n": 0}

    def two_lines_then_stop(fd, data):
        state["n"] += 1
        if state["n"] == 1:
            second_nl = data.index(b"\n", data.index(b"\n") + 1) + 1
            return real_write(fd, data[:second_nl])
        return 0

    monkeypatch.setattr(read_ledger.os, "write", two_lines_then_stop)
    torn = read_ledger.record_rows("Point", [{"id": f"q{i}"} for i in range(4)], path=ledger)
    monkeypatch.setattr(read_ledger.os, "write", real_write)
    assert torn == 2, "two whole read lines landed; the other two did not"
    # Both landed lines are readable — the count is not a guess about the batch.
    assert fold(ledger).lines_read == 2


def test_a_numeric_key_is_stored_as_its_string(ledger):
    """`row_key` coerces with `str()`, so the stock writer must too.

    A raw number would make `fold` skip the stock line while every read of that
    key resolved to a string — an EMPTY denominator and all reads `unregistered`.
    """
    read_ledger.record_stock("Point", 12345, nbytes=10, path=ledger)
    read_ledger.record_rows("Point", [{"id": "12345"}], path=ledger)
    folded = fold(ledger)
    assert folded.stock_keys == 1 and folded.lines_skipped == 0
    assert hot_fraction(folded).read_keys == 1


def test_a_rotated_ledger_is_tightened_again(ledger):
    """0600 must survive a rotation: the latch is per process, the inode is new."""
    import stat as _stat

    read_ledger.record_stock("Point", "p1", nbytes=10, path=ledger)
    # ROTATE: truncate and loosen the mode, as an operator's rotation may.
    ledger.write_text("")
    ledger.chmod(0o644)
    read_ledger.record_stock("Point", "p2", nbytes=10, path=ledger)
    assert _stat.S_IMODE(ledger.stat().st_mode) == 0o600


def test_a_torn_tail_is_repaired_before_the_next_batch(ledger):
    """A crash leaves a fragment with no newline; the NEXT batch must survive it.

    Without the repair the batch's first line is concatenated onto the fragment,
    so that record is destroyed while the return value counts it (the newline it
    wrote is the batch's own) — the module's "a torn write damages at most one
    record" guarantee failing in the recovery case it exists for, and the lost
    read biasing `f` LOW.
    """
    ledger.write_bytes(b'{"at":"t","event":"read","label":"Point","key":"torn"')
    got = read_ledger.record_rows("Point", [{"id": "p2"}, {"id": "p3"}], path=ledger)
    assert got == 2
    folded = fold(ledger)
    # Both served keys are readable (no stock registered, so they fold as
    # unregistered reads — the point is that they are READ, not lost).
    assert folded.unregistered_reads == 2, folded
    assert folded.lines_skipped == 1, "the fragment is one damaged line, not three"


def test_a_non_utc_stamp_is_compared_as_a_time_not_a_string(ledger):
    """`at=` is public, and lexical order is chronological only on ONE offset.

    With `now` = 2026-10-18T18:00Z the 7-day cutoff is 2026-10-11T18:00Z, so
    `2026-10-11T23:00:00+09:00` (= 14:00Z) is OUTSIDE even though its string sorts
    after every UTC stamp of that day, and `2026-10-12T03:00:00+09:00` (= 18:00Z)
    is exactly ON the cutoff even though its string sorts after it.
    `2026-10-17T23:00:00+09:00` (= 14:00Z) is EARLIER than
    `2026-10-17T15:00:00+00:00`, so the "last read" must be the latter — a string
    compare picks the former.
    """
    read_ledger.record_stock("Point", "p1", nbytes=10, path=ledger, at="2026-10-01T00:00:00+00:00")
    read_ledger.record_read("Point", "p1", path=ledger, at="2026-10-11T12:00:00+00:00")  # before
    read_ledger.record_read("Point", "p1", path=ledger, at="2026-10-11T23:00:00+09:00")  # 14:00Z
    read_ledger.record_read("Point", "p1", path=ledger, at="2026-10-12T03:00:00+09:00")  # 18:00Z
    read_ledger.record_read("Point", "p1", path=ledger, at="2026-10-17T23:00:00+09:00")  # 14:00Z
    read_ledger.record_read("Point", "p1", path=ledger, at="2026-10-17T15:00:00+00:00")  # latest
    ref = datetime.fromisoformat("2026-10-18T18:00:00+00:00")
    folded = fold(ledger, window_days=7, now=ref)
    row = next(iter(folded.rows.values()))
    assert row.read_count == 3, "only the three at/after the cutoff"
    assert row.last_read_at == "2026-10-17T15:00:00+00:00", row.last_read_at


def test_a_union_of_tenants_is_flagged_in_the_report(ledger, capsys):
    """Forgetting `--tenant` pools tenants and biases `f` DOWN — say so."""
    read_ledger.record_stock("Point", "a", nbytes=1, path=ledger, tenant_id="t1")
    read_ledger.record_stock("Point", "b", nbytes=1, path=ledger, tenant_id="t2")
    assert read_ledger._main(["--ledger", str(ledger)]) == 0
    out = capsys.readouterr().out
    assert "UNION" in out and "2 tenants" in out
    assert read_ledger._main(["--ledger", str(ledger), "--tenant", "t1"]) == 0
    assert "UNION" not in capsys.readouterr().out


def test_the_capped_first_batch_still_creates_a_0600_file(tmp_path, monkeypatch):
    """`open(mode="ab")` would create it at the umask's mode on this path."""
    import stat as _stat

    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv(LEDGER_MAX_BYTES_ENV, "10")
    monkeypatch.setenv("TORTOISE_READ_LEDGER_PATH", str(path))
    ledger_path = read_ledger.ledger_path()
    assert ledger_path == path
    read_ledger.record_rows("Point", [{"id": f"p{i}"} for i in range(5)], path=path)
    assert path.exists()
    assert _stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_ledger.fold(path).capped is True


def test_the_tenant_coordinate_is_reachable_from_the_request_context(monkeypatch):
    """Nothing sets the env var per request, so `None` would pool every org.

    A hosted process serves many tenants from ONE process; if the ledger could
    only be partitioned by an env var, its per-tenant `f` would be unreachable in
    the shape the product actually produces — and the union would bias `f` DOWN
    with nothing to notice.
    """
    from tortoise import mcp_auth

    monkeypatch.delenv(read_ledger.LEDGER_TENANT_ENV, raising=False)
    # The REAL ContextVar, set/reset — not a lambda. `_current_org_id` is a
    # ContextVar in production, so mocking it as a callable pins a shape the
    # callee never has (and would pass over a `.get()` that is missing).
    token = mcp_auth._current_org_id.set("org-42")
    try:
        assert read_ledger.tenant() == "org-42"
    finally:
        mcp_auth._current_org_id.reset(token)
    monkeypatch.setenv(read_ledger.LEDGER_TENANT_ENV, "explicit")
    assert read_ledger.tenant() == "explicit", "the env override still wins"


def test_an_undated_read_under_a_window_is_counted_not_dropped(ledger):
    """Every other loss is counted; a silent one makes the report read complete."""
    read_ledger.record_stock("Point", "p1", nbytes=10, path=ledger, at="2026-10-01T00:00:00+00:00")
    read_ledger.record_read("Point", "p1", path=ledger, at="not-a-date")
    read_ledger.record_read("Point", "p1", path=ledger, at="2026-10-17T00:00:00+00:00")
    folded = fold(ledger, window_days=7, now=datetime.fromisoformat("2026-10-18T18:00:00+00:00"))
    assert folded.reads_undated == 1
    assert next(iter(folded.rows.values())).read_count == 1


def test_an_unpartitioned_reading_says_so(ledger, capsys):
    """`tenant=None` everywhere is one namespace — and MAY be a union."""
    read_ledger.record_stock("Point", "a", nbytes=1, path=ledger)
    assert read_ledger._main(["--ledger", str(ledger)]) == 0
    assert "NOT PARTITIONED" in capsys.readouterr().out


def test_a_mixed_null_and_named_tenant_file_is_flagged(ledger, capsys):
    """`None` + ONE named tenant is a union, and it is the production shape.

    A context-free write lands on `tenant=None` while a request-scoped one lands
    on an org, so a file holding both is normal — and counting only the NAMED
    tenants reported "1 tenant" and flagged nothing over a pooled reading.
    """
    read_ledger.record_stock("Point", "a", nbytes=1, path=ledger)
    read_ledger.record_stock("Point", "b", nbytes=1, path=ledger, tenant_id="t1")
    assert read_ledger._main(["--ledger", str(ledger)]) == 0
    out = capsys.readouterr().out
    assert "MIXED" in out, out


def test_first_event_at_is_the_earliest_instant_not_the_earliest_string(ledger):
    """`2026-10-11T01:00:00+09:00` is 2026-10-10T16:00Z, EARLIER than 20:00Z.

    A string compare picks `2026-10-10T20:00:00+00:00` (its date sorts first), so
    the report would name an event four hours after the file's true start.
    """
    read_ledger.record_stock("Point", "a", nbytes=1, path=ledger, at="2026-10-11T01:00:00+09:00")
    read_ledger.record_stock("Point", "b", nbytes=1, path=ledger, at="2026-10-10T20:00:00+00:00")
    folded = fold(ledger)
    assert folded.first_event_at == "2026-10-11T01:00:00+09:00", folded.first_event_at


def test_a_falsy_filters_value_still_means_no_filters():
    """`0`, `False`, `""`, `[]` were "no filters" before the private-key drop."""
    from tortoise.mcp_server import _public_filters

    for value in (None, 0, False, "", [], {}):
        assert _public_filters(value) == {}, value
    assert _public_filters({"_record_reads": False, "status": "live"}) == {"status": "live"}
    # A tenant property may START with an underscore (validate_filter_key allows
    # it), so only the known switches are dropped.
    assert _public_filters({"_foo": 1}) == {"_foo": 1}
    import pytest

    with pytest.raises(TypeError):
        _public_filters("true")


def test_an_undated_unresolved_event_is_counted_not_dropped(ledger):
    """The coverage-hole counter may not be the one thing that vanishes."""
    read_ledger.record_rows("Subject", [{"id": "no-name-key"}], path=ledger, at="not-a-date")
    folded = fold(ledger, window_days=7, now=datetime.fromisoformat("2026-10-18T18:00:00+00:00"))
    assert folded.unresolved_reads == 1


def test_enabled_matches_the_funnels_own_flag_read(monkeypatch):
    """`enabled()` and the funnel must agree, or the two gates can drift."""
    from tortoise.sdk import is_truthy

    for value in (None, "0", "1", "true"):
        if value is None:
            monkeypatch.delenv(read_ledger.LEDGER_FLAG_ENV, raising=False)
        else:
            monkeypatch.setenv(read_ledger.LEDGER_FLAG_ENV, value)
        assert read_ledger.enabled() is is_truthy(value)


def test_a_naive_now_is_read_as_utc_not_a_crash(ledger):
    """`_stamp` reads a naive stamp as UTC, so `now` must follow the same rule."""
    read_ledger.record_stock("Point", "p1", nbytes=10, path=ledger, at="2026-10-17T12:00:00+00:00")
    read_ledger.record_read("Point", "p1", path=ledger, at="2026-10-17T12:00:00+00:00")
    folded = fold(ledger, window_days=7, now=datetime(2026, 10, 17, 13))  # naive
    assert next(iter(folded.rows.values())).read_count == 1


def test_tenants_in_file_counts_the_file_even_with_tenant_given(ledger):
    """It is a fact about the FILE — a `--tenant` run must not report 0."""
    read_ledger.record_stock("Point", "a", nbytes=1, path=ledger, tenant_id="t1")
    read_ledger.record_stock("Point", "b", nbytes=1, path=ledger, tenant_id="t2")
    read_ledger.record_stock("Point", "c", nbytes=1, path=ledger)
    import io
    import json as _json
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert read_ledger._main(["--ledger", str(ledger), "--tenant", "t1", "--json"]) == 0
    payload = _json.loads(buf.getvalue())
    assert payload["tenants_in_file"] == 3, payload["tenants_in_file"]


def test_an_infinite_bytes_value_does_not_brick_the_report(ledger):
    """`record_stock(nbytes=float("inf"))` is writable through the public API."""
    read_ledger.record_stock("Point", "p1", nbytes=float("inf"), path=ledger)
    read_ledger.record_read("Point", "p1", path=ledger, at="2026-10-17T00:00:00+00:00")
    folded = fold(ledger)  # must not raise OverflowError
    row = next(iter(folded.rows.values()))
    assert row.nbytes is None, "unknown width, not a pretend number"
    assert row.read_count == 1


def test_a_non_string_at_does_not_raise_out_of_the_fold(ledger):
    """A merged/torn file may carry an epoch integer under `at`."""
    ledger.write_text(
        '{"at":1697000000,"event":"stock","key":"p1","label":"Point","tenant":null}\n'
        '{"at":["x"],"event":"stock","key":"p2","label":"Point","tenant":null}\n'
    )
    folded = fold(ledger)
    assert folded.stock_keys == 2

"""#10465 — the DoD instrument must be able to come back RED.

`#10465` scores `#5090`'s four `Definition of done` bullets at a named head, so
the instrument is the deliverable and its **falsifiers are the proof**. A probe
that can only ever print `MET` is machinery, not measurement — the same failure
`#4999`'s own test file calls out (*"a test asserting ``reason == 'ok'`` on a
healthy path PASSES on the pre-fix code"*).

Each test below is a falsifier for one of the instrument's two load-bearing
claims:

| test | claim it falsifies | it goes RED when … |
|---|---|---|
| `test_the_four_bullets_are_scored_verbatim` | nothing is silently dropped, and nothing is re-worded | `BULLETS` (or a scored row) drifts from the epic strings pinned as LITERALS below — never compared against the module under test, which would pass on any re-wording |
| `test_a_reworded_bullet_reds_the_verbatim_pin` | the pin above can FAIL | it cannot — i.e. the comparison has regressed to a self-comparison of `probe.BULLETS` with itself |
| `test_the_embed_decision_has_exactly_one_predicate` | §12.2a's target of ONE predicate | a second pattern-owning module appears on the embed path, or the gate copies `value_gate.RULES` instead of sharing it |
| `test_a_second_pattern_list_in_the_embed_gate_reads_as_drift` | the predicate count can FAIL | the count is hard-wired to 1 (mutation: a fake tree WITH a second pattern list must read `DRIFT`) |
| `test_every_embedding_write_path_reaches_the_gate_seam` | bullet 1's `enforced` half, REMOVAL direction | a writer stops calling `encode_for_row` (the point of #5008) |
| `test_a_new_write_path_that_bypasses_the_seam_is_reported` | bullet 1's `enforced` half, BYPASS direction | a writer reaches a raw primitive instead — and is not reported as `BYPASS(<fn>)` |
| `test_a_module_qualified_call_still_counts_as_a_seam` | the seam walk sees qualified calls | the walk matches only bare `ast.Name` calls, so `mod.encode_for_row(...)` vanishes |
| `test_an_undesigned_bypass_holds_bullet_one_at_partial` | the bypass consequence | a bypass stops holding bullet 1 below `MET` |
| `test_the_designed_bypasses_are_the_documented_ones` | the exemption list is deliberate | a third write path starts bypassing the gate without an explicit edit here |
| `test_the_instrument_is_selected_by_its_own_subjects` | the pins actually RUN in CI | a subject diff stops selecting this file (the #10465 review's P1) |
| `test_every_embedded_label_has_a_query_path` | bullet 2 | an embedding write path serves a label `run_vector_query` cannot query |
| `test_all_three_mechanisms_are_visible_to_the_caller` | bullet 3 (`#4999`) | the fallback stops announcing itself, or two paths report the same mechanism |
| `test_the_census_refuses_to_report_zero_for_an_unreadable_store` | the 2026-09-24 error (`stored`-only / silent 0) | a store that could not be read is reported as `0` anything |
| `test_an_unmeasurable_run_exits_two` | the documented exit-code contract | a run that could not measure exits 0 or 1, so a caller reads "could not measure" as "a verdict was NOT MET" |

⛔ **These tests never write a graph and never flip a gate.** `#10465` is
measurement-only; the instrument is read-only and its pin must be too.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import dod_5090_probe as probe

REPO_ROOT = Path(__file__).resolve().parent.parent

#: `#5090`'s four `## Definition of done` bullets, pinned HERE as LITERALS.
#: ⛔ Not read from `probe.BULLETS`: `measure()` builds every row FROM that
#: constant, so asserting `[r["text"] for r in rows] == list(probe.BULLETS)` is a
#: self-comparison — it passes after `BULLETS[0]` is re-worded to anything, which
#: is precisely the drift the table above claims to catch. Two artifacts lean on
#: this pin (the `BULLETS` comment in `tools/dod_5090_probe.py` and `#5090`'s own
#: "bullet (verbatim)" table), so it has to read the epic's words and not the
#: module's. A re-wording on either side is a deliberate edit, made in both
#: places — which is what makes it visible.
EPIC_BULLETS_5090: tuple[str, ...] = (
    "A stated, enforced rule for what earns an embedding.",
    "No vector is stored without a path to query it.",
    "A fallback query reports the fallback.",
    "V1 is decided and implemented.",
)


def _assert_verbatim(bullets) -> None:
    assert tuple(bullets) == EPIC_BULLETS_5090, (
        "the scored text drifted from #5090's `## Definition of done`: "
        f"tool={tuple(bullets)!r} epic={EPIC_BULLETS_5090!r}"
    )


@pytest.fixture(autouse=True)
def _no_ambient_gate_flags():
    """A lane's own env must not be able to make a default-off gate read as
    enforced — `measure()` clears these, and so must the fixture."""
    before = {k: os.environ.get(k) for k in ("TORTOISE_EMBED_GATE", "TORTOISE_VALUE_GATE")}
    for k in before:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── The four rows ──────────────────────────────────────────────────────────


def test_the_four_bullets_are_scored_verbatim():
    """Every bullet the epic states is scored, and none is invented."""
    _assert_verbatim(probe.BULLETS)
    report = probe.measure()
    rows = report["rows"]
    assert len(rows) == 4, rows
    assert [r["bullet"] for r in rows] == [1, 2, 3, 4], rows
    _assert_verbatim([r["text"] for r in rows])
    for r in rows:
        assert r["verdict"] in probe.VERDICTS, r
        # A bullet without a command is UNSCORED, not met (#10465 target 1).
        assert r["command"].strip(), r
        assert r["observed"].strip(), r


def test_a_reworded_bullet_reds_the_verbatim_pin(monkeypatch):
    """The pin above can FAIL — proved here, because a falsifier that cannot
    come back red is machinery. Before this fix the assertion compared
    `probe.BULLETS` with itself, so a re-worded constant passed it silently."""
    reworded = list(probe.BULLETS)
    reworded[0] = "Not a bullet from the epic."
    monkeypatch.setattr(probe, "BULLETS", tuple(reworded))
    assert probe.BULLETS != EPIC_BULLETS_5090, "the mutation did not take"
    with pytest.raises(AssertionError, match="drifted from #5090"):
        _assert_verbatim(probe.BULLETS)
    with pytest.raises(AssertionError, match="drifted from #5090"):
        _assert_verbatim([r["text"] for r in probe.measure()["rows"]])


def test_the_report_names_the_head_it_measured():
    """A verdict without a head is unquotable a week later — #5087's staleness."""
    report = probe.measure()
    assert report["head"] and not report["head"].startswith("UNMEASURED"), report["head"]
    assert len(report["head"]) >= 7, report["head"]


# ── §12.2a — one predicate ─────────────────────────────────────────────────


def test_the_embed_decision_has_exactly_one_predicate():
    """§12.2a: ONE rule set, ONE predicate, shared by identity."""
    counted = probe.count_embed_predicates()
    assert counted["count"] == 1, (
        "a second predicate that can answer NO-EMBED is the drift §12.2a "
        f"forbids — measured owners: {counted['owners']}"
    )
    assert counted["owners"] == [probe.PREDICATE_MODULE], counted
    assert counted["rule_set_is_shared_identity"] is True, (
        "embed_gate.RULE_SET must BE value_gate.RULES, not an equal copy"
    )
    assert counted["verdict"] == "MET", counted


def test_a_second_pattern_list_in_the_embed_gate_reads_as_drift(tmp_path, monkeypatch):
    """The mutation check, made executable: the count CAN fail.

    A fake tree whose embed gate owns its own patterns must read `DRIFT` with
    two owners. If the count were hard-wired to 1 (a probe that cannot come
    back red), this test fails — which is the point.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_A = re.compile(r'#\\d+')\nRULES = ('value.identifier_only.reference',)\n"
        "def identifier_only(t):\n    return None\n",
        encoding="utf-8",
    )
    # A SECOND, independently-derived rule set — exactly the drift.
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "import re\n_B = re.compile(r'\\b\\d{4}-\\d{2}-\\d{2}\\b')\n"
        "RULE_SET = ('value.identifier_only.reference',)\n"
        "def embedding_verdict(t):\n    return None\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "PREDICATE_MODULE", "tortoise/value_gate.py")
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert counted["verdict"] == "DRIFT", counted


# ── Bullet 1 / 2 — the seam and the served labels ──────────────────────────


def test_every_embedding_write_path_reaches_the_gate_seam():
    """#5008's seam is reached by the entity writers — not by the raw primitive.

    This is the REMOVAL direction only: a writer that stops calling the seam
    leaves this map. The BYPASS direction is the sibling test below, because a
    map built from seam CALLS cannot see a function that never called it.
    """
    seams = probe._write_seams_that_embed()
    for writer in ("_upsert_point_props", "_upsert_subject", "_upsert_object",
                   "_upsert_document", "_upsert_event"):
        assert writer in seams, (
            f"{writer} embeds a written row without reaching encode_for_row — "
            f"the ungated-primitive state #5008 closed. Measured seams: {sorted(seams)}"
        )
    # And the derived labels must not invent one.
    written = probe.written_labels(seams)
    assert not [v for v in written.values() if v.startswith("UNKNOWN")], written


def test_a_new_write_path_that_bypasses_the_seam_is_reported(tmp_path):
    """The BYPASS direction: a NEW writer calling a raw primitive is reported.

    `#5008`'s docstring names `encode_for_store` / `encode_batch_for_store` as
    the ungated primitives, and the seam map is built from calls of the SEAM —
    so without this the one change that makes "every embedding write path
    reaches the gate" false again (a new writer that never called it) adds
    nothing to the map and the instrument keeps reporting the same verdict.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "writers.py").write_text(
        "def _upsert_thing(text):\n"
        "    return encode_for_store(text, None)\n"
        "\n"
        "def _upsert_other(text):\n"
        "    return encode_batch_for_store([text], None)\n"
        "\n"
        "def _upsert_gated(text):\n"
        "    return encode_for_row(text, None)\n",
        encoding="utf-8",
    )
    seams = probe._write_seams_that_embed(root=tmp_path)
    bypasses = probe.write_bypasses(seams, root=tmp_path)
    assert set(seams) == {"_upsert_gated"}, seams
    # Both directions of reaching a raw primitive are reported, not just one.
    assert set(bypasses) == {"_upsert_thing", "_upsert_other"}, bypasses
    assert bypasses["_upsert_thing"] == "tortoise/writers.py:2", bypasses
    # Neither is a documented exemption, so bullet 1 reads an UNDESIGNED bypass.
    assert set(probe.undesigned_bypasses(bypasses)) == {"_upsert_thing", "_upsert_other"}
    assert probe.rule_verdict(stated=True, shared=True, seams=seams,
                              undesigned=probe.undesigned_bypasses(bypasses),
                              on_by_default=True) == "PARTIAL"


def test_a_module_qualified_call_still_counts_as_a_seam(tmp_path):
    """`mod.encode_for_row(...)` is an `ast.Attribute` (`.attr`), not a
    `Name` (`.id`) — https://docs.python.org/3/library/ast.html — so a walk that
    reads only `.id` silently DROPS every qualified call site, and a writer that
    imported the module rather than the name would vanish from the seam map."""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "qualified.py").write_text(
        "from tortoise import embeddings\n"
        "\n"
        "def _upsert_object(text):\n"
        "    return embeddings.encode_for_row(text, None)\n",
        encoding="utf-8",
    )
    seams = probe._write_seams_that_embed(root=tmp_path)
    assert "_upsert_object" in seams, seams
    assert seams["_upsert_object"] == "tortoise/qualified.py:4", seams


def test_an_undesigned_bypass_holds_bullet_one_at_partial():
    """A bypass is reported, but only an UNDESIGNED one is evidence of
    regression: the designed ones are `encode_for_row`'s documented boundary."""
    seam = {"_upsert_subject": "tortoise/x.py:1"}
    assert probe.rule_verdict(stated=True, shared=True, seams=seam,
                              undesigned={}, on_by_default=True) == "MET"
    assert probe.rule_verdict(
        stated=True, shared=True, seams=seam,
        undesigned={"_new_writer": "tortoise/y.py:9"},
        on_by_default=True,
    ) == "PARTIAL"
    # The other inputs can only make the row worse, never better.
    assert probe.rule_verdict(stated=False, shared=True, seams=seam,
                              undesigned={}, on_by_default=True) == "NOT MET"
    assert probe.rule_verdict(stated=True, shared=False, seams=seam,
                              undesigned={}, on_by_default=True) == "NOT MET"
    assert probe.rule_verdict(stated=True, shared=True, seams={},
                              undesigned={}, on_by_default=True) == "NOT MET"
    assert probe.rule_verdict(stated=True, shared=True, seams=seam,
                              undesigned={}, on_by_default=False) == "PARTIAL"


def test_the_designed_bypasses_are_the_documented_ones():
    """The exemption list is DELIBERATE: the real tree has exactly these two
    bypasses and no others. A third one is an ungated write path, so it reds
    here until someone edits `DESIGNED_BYPASSES` on purpose — which is the whole
    reason the list is not derived."""
    measured = probe.write_bypasses()
    assert set(measured) == set(probe.DESIGNED_BYPASSES), (
        f"measured bypasses {sorted(measured)} != the documented exemptions "
        f"{sorted(probe.DESIGNED_BYPASSES)} — a write path reaches a raw "
        "primitive without the gate (#5008)'s subject"
    )
    assert probe.undesigned_bypasses(measured) == {}, measured
    # And the row REPORTS them: a bypass that is not printed is not measured.
    notes = probe.score_rule_bullet().notes
    assert any(n.startswith("BYPASS (raw primitive, no gate): 2") for n in notes), notes
    assert not any(n.startswith("UNDESIGNED") for n in notes), notes


def test_the_instrument_is_selected_by_its_own_subjects():
    """A falsifier that never executes is not a falsifier (#10465 review P1).

    Registration alone is classification (#4348): it satisfies
    `ci_selection.py --integrity` but does not make a diff SELECT the file. Each
    subject below must select it, or the pins this instrument exists to install
    never run on the change that can break them. Two named-surface matches
    REPLACE the `core` fallback (#2938), which is why the file is registered in
    `eval` as well as `core`, and why `tools/dod_5090_probe.py` needs a
    `CORE_ALSO` entry (a `tools/`-only diff is swallowed by
    NON_PYTHON_PREFIXES before the fallback runs).
    """
    import ci_selection

    manifest = ci_selection.load_manifest()
    for subject in ("tortoise/embed_gate.py", "tortoise/value_gate.py",
                    "tortoise/embeddings.py", "tortoise/search_engine.py",
                    "tortoise/security.py", "tools/dod_5090_probe.py"):
        selected = ci_selection.select([subject], "pull_request", manifest)
        assert "test_dod_5090_probe.py" in selected["test_files"], (
            f"a {subject}-only diff does not select this file — its falsifiers "
            f"never run: surfaces={selected['surfaces']} full={selected['full']}"
        )


def test_every_embedded_label_has_a_query_path():
    """Bullet 2: nothing is STORED that `run_vector_query` cannot query."""
    row = probe.score_path_bullet()
    assert row.verdict == "MET", row.observed
    written = set(probe.written_labels().values())
    assert written <= probe.served_labels(), written - probe.served_labels()


# ── Bullet 3 — the fallback announces itself ───────────────────────────────


def test_all_three_mechanisms_are_visible_to_the_caller():
    """`#4999`: an indexed read, a scan, and a scan_fallback are distinguishable."""
    observed = probe.fallback_probe()
    assert observed == {"index": "index", "scan": "scan",
                        "scan_fallback": "scan_fallback"}, observed
    assert probe.score_fallback_bullet().verdict == "MET"


# ── The census ─────────────────────────────────────────────────────────────


class _UnreadableGraph:
    """A store whose catalogue cannot be read — NOT an empty store."""

    def query(self, cypher, params=None, timeout=None):
        raise RuntimeError("Timeout reading from socket")


def test_the_census_refuses_to_report_zero_for_an_unreadable_store():
    """`stored=0` and `could not read` are different facts (2026-09-24's error)."""
    census = probe.vector_census(_UnreadableGraph(), "some_graph",
                                at="2026-11-01T00:00:00Z",
                                graph_names=["some_graph"])
    assert census["labels"] == {}
    assert census["catalogue"].startswith("UNMEASURED("), census["catalogue"]
    assert "Timeout reading from socket" in census["catalogue"]


class _MaterialisingGraph:
    """A handle that would CREATE the graph if anything queried it (measured:
    a catalogue read on a missing graph name materialises an empty graph)."""

    def query(self, cypher, params=None, timeout=None):
        raise AssertionError(
            "a catalogue read was issued for a graph that does not exist — that "
            "CREATES it server-side, and the census is read-only by doctrine"
        )


def test_the_census_never_touches_a_graph_that_does_not_exist():
    """The read-only doctrine: a missing name is `UNMEASURED`, not a write and
    not a measured-looking `stored=0`."""
    census = probe.vector_census(_MaterialisingGraph(), "absent_graph",
                                at="2026-11-01T00:00:00Z",
                                graph_names=["dod5090_probe"])
    assert census["labels"] == {}
    assert "does not exist" in census["catalogue"], census["catalogue"]
    assert census["command"].startswith("uv run python tools/dod_5090_probe.py")


def test_the_census_refuses_an_embedded_engine():
    """Embedded has no index API — a census there would read `indexed: False`
    for every label, which is not the same fact as "nothing is indexed"."""
    with pytest.raises(SystemExit) as raised:
        probe._connect("file:///tmp/whatever.db")
    # The documented contract: 2 means "could not measure", never a verdict.
    # A non-int SystemExit argument exits 1, so the code is passed explicitly.
    assert raised.value.code == 2, raised.value.code


def test_an_unmeasurable_run_exits_two(monkeypatch):
    """`0 = ran` (whatever the verdicts), `2 = could not measure`.

    The docstring promised `2` and no path returned it, which is the same
    class of defect as the epic body this PR repaired: a contract in prose the
    code never implements. Both failure halves are pinned here."""
    # (a) a gate module the instrument scores that will not import.
    def _no_gate():
        raise ImportError("no module named 'tortoise.embed_gate'")

    monkeypatch.setattr(probe, "score_rule_bullet", _no_gate)
    assert probe.main(["--json"]) == 2
    monkeypatch.undo()

    # (b) a census URI whose engine has no index API.
    with pytest.raises(SystemExit) as raised:
        probe.main(["--census", "--uri", "file:///tmp/whatever.db"])
    assert raised.value.code == 2, raised.value.code

    # And a run that DID measure is 0 even when a row is NOT MET — a caller
    # must never be able to read a red verdict as "the tool failed".
    assert probe.main(["--json"]) == 0

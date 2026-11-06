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
| `test_the_four_bullets_are_scored_verbatim` | nothing is silently dropped | a bullet stops being scored, or is re-worded so the table scores something the epic does not say |
| `test_the_embed_decision_has_exactly_one_predicate` | §12.2a's target of ONE predicate | a second pattern-owning module appears on the embed path, or the gate copies `value_gate.RULES` instead of sharing it |
| `test_a_second_pattern_list_in_the_embed_gate_reads_as_drift` | the predicate count can FAIL | the count is hard-wired to 1 (mutation: a fake tree WITH a second pattern list must read `DRIFT`) |
| `test_the_seam_module_is_scanned_for_its_own_patterns` | §12.2a's population reaches the module that DECIDES whether to consult the gate | `tortoise/embeddings.py` leaves `predicate_population()` — a forward walk of the gate's imports can never enter its own caller, so a second rule set there becomes invisible |
| `test_a_pattern_list_in_the_seam_module_reads_as_drift` | the same hole, made executable | a seam module that both imports the gate AND owns `re.compile` reads `count: 1 / MET` — the fail-open shape, on the tree most likely to grow one |
| `test_an_unparseable_module_is_a_hole_in_the_predicate_count` | a hole is a hole here too, not a zero | a module the count could not parse contributes no owner and the verdict still says `MET`, while the sibling writer row holds `PARTIAL` for the identical condition |
| `test_every_embedding_write_path_reaches_the_gate_seam` | bullet 1's `enforced` half, REMOVAL direction only | a named writer stops calling `encode_for_row` (the point of #5008) |
| `test_an_unresolved_writer_is_reported_and_holds_bullet_two_at_partial` | bullet 2's honesty, on an INJECTED map | an unresolved write path is dropped from the written set, or stops holding the row at `PARTIAL` |
| `test_every_embedded_label_has_a_query_path` | bullet 2 | an embedding write path serves a label `run_vector_query` cannot query |
| `test_all_three_mechanisms_are_visible_to_the_caller` | bullet 3 (`#4999`) | the fallback stops announcing itself, or two paths report the same mechanism |
| `test_the_census_refuses_to_report_zero_for_an_unreadable_store` | the 2026-09-24 error (`stored`-only / silent 0) | a store that could not be read is reported as `0` anything |
| `test_the_census_reads_through_the_READ_ONLY_verb` | "the census stores nothing", enforced by the ENGINE | a census read goes back to the write-capable `graph.query` |
| `test_a_backtick_in_a_graph_label_cannot_re_purpose_the_census_query` | a graph-controlled label is QUOTED, not concatenated | a label carrying a backtick closes the identifier and appends Cypher |
| `test_the_TEXT_path_PRINTS_an_unreadable_catalogue_row` | the text path renders what `--json` renders | the printer raises `TypeError` on the `(label, why)` tuples and a measured census exits 2 |
| `test_an_unmeasurable_run_exits_two` | the documented exit-code contract | a run that could not measure exits 0 or 1, so a caller reads "could not measure" as "a row was NOT MET" |
| `test_the_instrument_is_selected_by_its_own_subjects` | the pins above actually RUN in CI | a subject diff stops selecting this file — a falsifier that never executes |
| `test_two_package_inits_keep_distinct_writer_identities` | the writer map's exhaustiveness | two writers in one package collapse into one key, so a label bullet 2 compares vanishes |
| `test_a_collision_is_classified_whichever_exit_writes_it` | the duplicate guard itself, DRIVEN rather than injected | the collision rule is consulted at only one of the four exits, so a later `unresolved_write`/two-label definition silently overwrites a `gated` one and the collision reads as an ordinary unresolved producer (order-dependent, and no test drove it) |
| `test_a_collided_writer_is_not_REPORTED_as_an_override` | the OTHER half of the guard — the `overridden` bookkeeping `_put()` withdraws on a collision | a collision replaces a writer's override-supplied label while the key stays in `overridden`, so the note that promises to distinguish ASSIGNED labels from DERIVED ones reports an override the row did not use (in either `ast.walk` order) |
| `test_a_duplicate_writer_identity_holds_bullet_two_at_partial` | bullet 2's honesty | a writer whose label could not be resolved stops holding the row, while bullet 1 still reports the same hole |
| `test_the_tool_parses_under_the_floor` | the `#5128` guard is PROTECTIVE, not documentary | 3.12-only syntax (PEP 701) creeps back in, so the file dies in the tokenizer before its own guard can refuse |
| `test_a_line_that_merely_mentions_the_flag_is_not_a_shipped_default` | bullet 1's `on_by_default` input | a line that *documents* the flag (prose, a workflow step name, a commented list item) is scored as a shipped ON |
| `test_an_assignment_shaped_line_inside_a_block_scalar_is_not_a_shipped_default` | the same input, on the one shape whose error direction is toward `MET` | a `>-` body line shaped like `KEY: 1` (documentation) reads as a shipped ON |
| `test_a_real_mapping_after_a_block_scalar_still_reads_as_shipped_ON` | the block-scalar fix, in the other direction | ending the scalar drops the assignments that follow it from the walk |
| `test_a_function_local_pattern_is_not_a_second_predicate_owner` | §12.2a's count is "module scope", as its docstring says | a helper's LOCAL `re.compile` in the population reads `count: 2 / DRIFT`, redding a required check on a correct change |
| `test_a_class_level_pattern_still_counts_as_an_owner` | the same pruning must not become a false `MET` | a pattern list owned at IMPORT time (class body) is skipped by the module-scope walk |
| `test_a_surface_that_quotes_the_dod_bullet_does_not_publish_a_ruling` | bullet 4 is the OWNER GATE — nothing may publish a ruling the owner did not make | a doc that restates `BULLETS[3]` (which contains the marker itself) scores `PARTIAL` |
| `test_every_real_assignment_shape_still_reads_as_shipped_ON` | the same input, in the other direction | tightening the test makes a REAL shipped ON (compose map/array, `.env`, Dockerfile, `export`) read as a false `False` |
| `test_searched_is_the_walk_not_the_list_of_mentions` | the documented `searched` contract | `searched` reports the mentions, so "what 'nowhere' covered" is `[]` for the case the function exists for |
| `test_an_unreadable_surface_is_named_not_silently_excluded` | the `UNMEASURED` rule, applied to the walk | a file the walk could not read is folded into a measured `False` |
| `test_every_unresolved_writer_carries_a_census_class` | the census and the unresolved set are the SAME SET, in both directions, LIVE | a writer this tree holds is unresolved and nothing says which of the three honest classes it is in — or a census entry outlives its hole, so `exempt` counts an instrument false positive that no longer exists |
| `test_a_prose_MENTION_of_an_embedding_write_is_classed_exempt_not_a_hole` | `exempt`'s own definition — *"merely MENTIONS one in a comment/docstring (the instrument's own false positives)"* | `main` lands a fold whose COMMENT quotes `embedding=…` and the census, which must be an EXACT set match, has no class for the writer the needle then reports (measured: `#8488`/`#10523` → `tortoise.projection::_apply_one`) — or the fold grows a REAL vector write, so `exempt` stops describing it |
| `test_the_census_guard_has_teeth_on_an_INJECTED_map` | the same guard's teeth, without pinning this head | the guard goes vacuous when #5090's remainder lands and this tree holds no unresolved writer |
| `test_a_duplicate_identity_paints_no_census_note` | #10541's census population | a `duplicate` identity is counted as a census member, so a census is painted over a population that cannot hold a key for it |
| `test_the_census_note_names_only_the_unresolved_producers` | the same population, in the other direction | a classed unresolved producer stops being named, or a duplicate is counted alongside it |
| `test_the_census_note_does_not_break_the_markdown_table` | the row the table emits keeps ONE cell count, header included | a note carries a `|` and `_cell` STOPS escaping it, so GFM splits the 4-column row — ⚠️ this fixture's note carries none today (the note joins with ` · `), so the emitter's escaping is pinned by `test_the_table_escapes_a_pipe_in_every_row_not_only_row_two` instead |
| `test_an_out_of_vocabulary_census_class_is_unclassified_not_invisible` | an out-of-vocabulary class is NAMED as UNCLASSIFIED, and the census segments still ACCOUNT for the population | a class outside `CENSUS_CLASSES` falls through both `by_class` and `unclassified`, so the writer is named nowhere and the segments sum to `0` against a header claiming `N` |
| `test_the_census_note_names_the_population_it_counted` | the total does not claim a population wider than it counted | a duplicate is listed as an unresolved writer while the note claims to cover the whole unresolved set and silently omits it |
| `test_every_census_citation_re_derives_at_this_head` | `WRITER_CENSUS`'s own promise — *"the entries below were READ at their seam"* — which `#5090`/`#10541` quote as evidence | a citation names a symbol that file does not define, or a needle that is no longer inside the symbol it is cited for (the `file:line` form drifted silently when `sdk.py` grew) |
| `test_the_citation_check_has_teeth_on_an_INJECTED_drift` | the same guard can FAIL | a drifted symbol, a missing needle, an unclassed entry or orphan evidence passes |
| `test_an_executed_workflow_run_body_is_a_shipped_setting` | bullet 1 reads what the CI EXECUTES | a `run: |` body carrying `export FLAG=1` is counted as documentation, so row 1 reports `set ON in: none` |
| `test_a_workflow_prose_block_is_still_documentation` | the same fix must not call every workflow block a command | a `note: >-` body (text) inside a workflow starts voting on `on_by_default` |
| `test_a_run_key_outside_the_workflow_surface_is_not_executed` | the executed-key rule is SCOPED to the surface that runs it | any `.yml` with a `run:` block is scanned as shell, so a config tool's own field completes a DoD row |
| `test_a_MATCH_label_is_not_credited_as_the_writers_label` | the writer map credits the label the WRITE names | a read's `MATCH (n:Point)` resolves a writer's stored label — how a label the writer never writes can read as "covered" |

⛔ **These tests never write a graph and never flip a gate.** `#10465` is
measurement-only; the instrument is read-only and its pin must be too.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import dod_5090_probe as probe


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
    report = probe.measure()
    rows = report["rows"]
    assert len(rows) == 4, rows
    assert [r["bullet"] for r in rows] == [1, 2, 3, 4], rows
    # A LITERAL, not `probe.BULLETS` on both sides: comparing the tool's
    # constant to itself could only detect a dropped row, never a re-worded
    # one (this file's own review, P3). These strings are `#5090`'s
    # `## Definition of done`, verbatim.
    assert [r["text"] for r in rows] == [
        "A stated, enforced rule for what earns an embedding.",
        "No vector is stored without a path to query it.",
        "A fallback query reports the fallback.",
        "V1 is decided and implemented.",
    ], "the scored text drifted from #5090's `## Definition of done`"
    for r in rows:
        assert r["verdict"] in probe.VERDICTS, r
        # A bullet without a command is UNSCORED, not met (#10465 target 1).
        assert r["command"].strip(), r
        assert r["observed"].strip(), r


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


def test_the_seam_module_is_scanned_for_its_own_patterns():
    """§12.2a's population must include the module that DECIDES to consult the gate.

    ⛔ `tortoise/embeddings.py` imports `embed_gate`, so a walk of the GATE's own
    imports can never enter it: an import makes the IMPORTER depend on the
    IMPORTEE (Python Language Reference §5), and this edge runs the other way.
    Excluding the seam on the grounds that "it is the CALLER, and it imports half
    the tree" answers a question about its IMPORTS — not about its own
    module-scope assignments, which is what this count exists to find.

    Real tree, no monkeypatch: this is the pin that reds if `OWNERSHIP_ONLY` is
    ever "tidied" away, which would silently re-open the hole below.
    """
    population = probe.predicate_population()
    assert "tortoise/embeddings.py" in population, (
        "the seam that decides whether the gate is consulted is not scanned for "
        f"its own patterns — population: {population}"
    )
    # …and the widening must NOT drag in the imports it was excluded for:
    assert "tortoise/api.py" not in population, population


def test_a_function_local_pattern_is_not_a_second_predicate_owner(tmp_path, monkeypatch):
    """A helper's LOCAL `re.compile` is not a second rule set — module scope is.

    `count_embed_predicates`'s docstring has always said *`re.compile` at module
    scope*, while the code ran `ast.walk()` over each top-level statement — which
    descends into `FunctionDef.body`. One ordinary helper-local pattern in any
    module of the population therefore read `count: 2 / verdict: DRIFT` and
    reddened `test_the_embed_decision_has_exactly_one_predicate`, a required
    `core` leg, on a change that is NOT drift: a guard that refutes a correct
    tree, whose cheapest-looking repair is to delete it (review, P3).
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_A = re.compile(r'#\\d+')\nRULES = ('r',)\n"
        "def identifier_only(t):\n    return None\n",
        encoding="utf-8",
    )
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nRULE_SET = value_gate.RULES\n"
        "def embedding_verdict(t):\n    return None\n",
        encoding="utf-8",
    )
    # The shape a helper grows: compiled when CALLED, not when imported.
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "from . import embed_gate\n"
        "def _is_precomputed(text):\n"
        "    import re\n"
        "    pat = re.compile(r'^sha256:[0-9a-f]{64}$')\n"
        "    return bool(pat.match(text))\n"
        "def encode_for_row(t, d):\n    return embed_gate.embedding_verdict(t)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "PREDICATE_MODULE", "tortoise/value_gate.py")
    counted = probe.count_embed_predicates()
    assert counted["count"] == 1, (
        f"a function-LOCAL pattern is not a module-scope predicate owner: {counted}")
    assert counted["verdict"] == "MET", counted


def test_a_class_level_pattern_still_counts_as_an_owner(tmp_path, monkeypatch):
    """The module-scope prune must not become the false `MET` it guards against.

    A class body executes at IMPORT time, so a pattern list there IS owned then
    and is a real second rule set. Pruning function bodies must not take the
    class with it — over-reporting drift is this file's stated safe direction,
    and a false `MET` is the failure the whole instrument exists to remove.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_A = re.compile(r'#\\d+')\nRULES = ('r',)\n",
        encoding="utf-8",
    )
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nRULE_SET = value_gate.RULES\n",
        encoding="utf-8",
    )
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "from . import embed_gate\n"
        "class _Skip:\n"
        "    import re\n"
        "    _PATS = [re.compile(r'^sha256:')]\n"
        "def encode_for_row(t, d):\n    return embed_gate.embedding_verdict(t)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "PREDICATE_MODULE", "tortoise/value_gate.py")
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert counted["verdict"] == "DRIFT", counted


def test_a_pattern_list_in_the_seam_module_reads_as_drift(tmp_path, monkeypatch):
    """A second rule set at the SEAM is the drift §12.2a forbids, and counts.

    ⛔ Non-vacuity: on the pre-fix tool this tree reads `{"count": 1,
    "verdict": "MET"}` — the seam is not in the population, so its pattern list
    is invisible AND the writer map skips the same module, leaving this the only
    detector for the shape. The module is written so it genuinely could not be
    reached by walking the gate's imports: `embeddings.py` imports `embed_gate`,
    never the reverse.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_V = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nRULE_SET = value_gate.RULES\n", encoding="utf-8")
    # The seam: it IMPORTS the gate (so no forward walk of the gate's imports can
    # ever enter it) and owns a second, independently-derived rule set.
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "import re\nfrom . import embed_gate\n"
        "_SKIP = re.compile(r'^sha256:[0-9a-f]{64}$')\n"
        "def encode_for_row(t, d):\n"
        "    if embed_gate.embed_gate_enabled():\n        return None\n"
        "    return _SKIP.match(t)\n",
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "PREDICATE_MODULE", "tortoise/value_gate.py")
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert "tortoise/embeddings.py" in counted["owners"], counted
    assert counted["verdict"] == "DRIFT", counted


def test_an_unparseable_module_is_a_hole_in_the_predicate_count(
        tmp_path, monkeypatch):
    """A module the count cannot parse is a HOLE, never a silent zero.

    The writer map already says so (`UNPARSEABLE:<path>` entries; the note "a
    module that will not parse is a HOLE in the measurement, not a zero"). The
    predicate count used to `continue` instead or report the hole, so a broken
    `embed_rules.py` carrying a second pattern list left `count: 1 / MET` —
    the one verdict this instrument exists to withhold.

    ⛔ Non-vacuity: on the pre-fix tool `verdict` reads `"MET"` here.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_V = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nfrom . import embed_rules\n"
        "RULE_SET = value_gate.RULES\n", encoding="utf-8")
    # Reachable ONLY because the PARSEABLE importer names it: `_embed_path_modules`
    # skips an unparseable file when building its edge map but keeps it in the
    # population, which is exactly the reachable shape.
    (tmp_path / "tortoise" / "embed_rules.py").write_text(
        "import re\n_SKIP = re.compile(r'^sha256:')\ndef oops(:\n",
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    # The primary claim first, so the pre-fix failure is the VERDICT (it reads
    # `MET`), not merely the absent key: a hole cannot certify `MET`.
    assert counted["verdict"] != "MET", counted
    # `UNDETERMINED`, not `DRIFT`: a hole is not evidence of a second rule set
    # either, and claiming one would be the same unfounded verdict inverted.
    assert counted["verdict"] == "UNDETERMINED", counted
    assert counted["unparseable"] == ["tortoise/embed_rules.py"], counted


# ── Bullet 1 / 2 — the seam and the served labels ──────────────────────────


def test_every_embedding_write_path_reaches_the_gate_seam():
    """#5008's seam is reached by the entity writers — not by the raw primitive."""
    seams = probe._write_seams_that_embed()
    for writer in ("tortoise.projection.entities::_upsert_point_props",
                   "tortoise.projection.entities::_upsert_subject",
                   "tortoise.projection.entities::_upsert_object",
                   "tortoise.projection.entities::_upsert_document",
                   "tortoise.projection.entities::_upsert_event"):
        assert writer in seams, (
            f"{writer} embeds a written row without reaching encode_for_row — "
            f"the ungated-primitive state #5008 closed. Measured seams: {sorted(seams)}"
        )
    # ⛔ This test claims the REMOVAL direction only, and deliberately asserts
    # nothing about TODAY's unresolved set: `#5090`'s remaining work is to
    # resolve those writers, so `assert unresolved` would red on a CORRECT
    # change and its cheapest-looking repair would be to delete it (review, P2).
    # The BYPASS direction is bullet 1's own `OUTSIDE the gate` population
    # (`score_rule_bullet` counts it and holds bullet 1 at `PARTIAL` for it),
    # and the "an unresolved writer is REPORTED, never
    # dropped" property is pinned on an injected map in
    # `test_an_unresolved_writer_is_reported_and_holds_bullet_two_at_partial`
    # and `test_a_duplicate_writer_identity_holds_bullet_two_at_partial`.


def test_an_unresolved_writer_is_reported_and_holds_bullet_two_at_partial(monkeypatch):
    """Bullet 2 must never read `MET` over a write path whose label it lost.

    The map is INJECTED, so this is a durable property rather than a reading of
    this head: resolving every unresolved writer in the tree turns the real row
    `MET` without touching this test (review, P2 — the sibling assertion that
    read the live tree was exactly that trap).
    """
    writers = {
        "tortoise.z::_store_without_a_producer": {
            "callee": "unrecognised", "at": "tortoise/z.py:3",
            "kind": "unresolved_write",
            "label": "UNKNOWN(unresolved producer)",
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    written = probe.written_labels()
    assert written == {
        "tortoise.z::_store_without_a_producer": "UNKNOWN(unresolved producer)"
    }, f"an unresolved write path must be REPORTED, never dropped: {written}"
    row = probe.score_path_bullet()   # every resolved label IS served here
    assert row.verdict == "PARTIAL", row.observed
    assert any("unresolved writers" in n for n in row.notes), row.notes


def test_every_embedded_label_has_a_query_path():
    """Bullet 2: nothing is STORED that `run_vector_query` cannot query.

    The assertions below are an IMPLICATION, never this head's classification:
    the row can only be `PARTIAL` while a write path's label is unresolved, and
    resolving them all (`#5090`) turns it `MET` without reddening this test.
    """
    row = probe.score_path_bullet()
    assert row.verdict in ("MET", "PARTIAL"), row.observed
    resolved = {v for v in probe.written_labels().values()
                if not v.startswith("UNKNOWN")}
    assert resolved <= probe.served_labels(), resolved - probe.served_labels()
    if any(v.startswith("UNKNOWN") for v in probe.written_labels().values()):
        assert row.verdict == "PARTIAL", row.verdict


# ── Bullet 3 — the fallback announces itself ───────────────────────────────


def test_all_three_mechanisms_are_visible_to_the_caller():
    """`#4999`: an indexed read, a scan, and a scan_fallback are distinguishable."""
    observed = probe.fallback_probe()
    assert observed == {"index": "index", "scan": "scan",
                        "scan_fallback": "scan_fallback"}, observed
    assert probe.score_fallback_bullet().verdict == "MET"


def test_the_fallback_probe_reads_a_HEALTHY_scan():
    """Each probe must take its HEALTHY branch — not a failed read that reports `ok`.

    The fake rows are `(id, score)` pairs and that is load-bearing: the
    brute-force scan reads `row[1]`, so a one-element row raises
    `IndexError: tuple index out of range` — a message containing "index" — and
    the leg records `degraded=True, reason="index_missing"`, returns `[]`, and
    STILL reports `mechanism="scan"`. The probe therefore looked healthy while
    measuring a failure (`scan: degraded=True reason='index_missing' count=0`,
    measured pre-fix), which is why `#5090` row 3's `degraded` clause could not
    be scored from the row's own command (review round 13, P3).
    """
    degraded: dict = {}
    observed = probe.fallback_probe(degraded=degraded)
    assert set(degraded) == set(observed), (sorted(degraded), sorted(observed))
    for name, d in degraded.items():
        assert d["degraded"] is False, (
            f"the {name!r} probe took a DEGRADED path, so it measured a failed "
            f"read rather than the mechanism it reports: {d}")
        assert d["reason"] == "ok", (name, d)
        assert d["declared"] is None, (name, d)


def test_bullet_three_SCORES_the_2952_clause_its_evidence_cites(monkeypatch):
    """`degraded` keeping `#2952`'s meaning is part of row 3's claim, so it is read.

    `#5090` row 3's evidence cell asserts "`degraded` keeps `#2952`'s meaning"
    while the row's own command read only the mechanism key — half the cell was
    unscored, the defect class `#10465` exists to remove (review round 13, P3).
    This pins the SCORING: the same three mechanisms, with a `degraded` reading
    injected as True, must NOT be `MET`.
    """
    clean: dict = {}
    probe.fallback_probe(degraded=clean)
    assert probe.score_fallback_bullet().verdict == "MET", clean

    def _degraded_probe(degraded=None):
        out = {"index": "index", "scan": "scan",
               "scan_fallback": "scan_fallback"}
        if degraded is not None:
            degraded.update({k: {"degraded": False, "reason": "ok",
                                 "declared": None} for k in out})
            # ONE mechanism declares a degraded read (#2952).
            degraded["scan_fallback"]["degraded"] = True
        return out

    monkeypatch.setattr(probe, "fallback_probe", _degraded_probe)
    row = probe.score_fallback_bullet()
    assert row.verdict == "NOT MET", row.observed
    assert any("#2952 untouched on every mechanism: False" in n
               for n in row.notes), row.notes


# ── The census ─────────────────────────────────────────────────────────────


class _UnreadableGraph:
    """A store whose catalogue cannot be read — NOT an empty store."""

    def query(self, cypher, params=None, timeout=None):
        raise RuntimeError("Timeout reading from socket")

    # The census reads through the read-only verb (`GRAPH.RO_QUERY`); a fake that
    # implements only `query` would let the tool quietly keep the write-capable
    # one, so both verbs answer the same way here.
    def ro_query(self, cypher, params=None, timeout=None):
        raise RuntimeError("Timeout reading from socket")


class _SpyDB:
    """A handle whose only job is to let `main` reach the census."""

    def select_graph(self, name):
        return object()


def test_a_pathless_uri_does_not_put_its_userinfo_in_the_census(
        monkeypatch, capsys):
    """`docker://:SECRET@host:6379` names NO graph, and its password is not a name.

    The pre-fix derivation was the last `/`-segment of the RAW URI, so for a
    path-less URI that segment is the URI's userinfo — carried into
    `report["census"]["graph"]` and printed (twice on the text path). Reachable
    with NO flag at all, because `--uri` defaults to `$TORTOISE_DB_URI`: the
    same credential class as #3039 and `tests/_embedded.py`'s warning (review
    round 13, P2).
    """
    monkeypatch.setattr(probe, "_connect", lambda uri: (_SpyDB(), ["g1"]))
    rc = probe.main(["--census", "--uri", "docker://:s3cr3t@127.0.0.1:6399",
                     "--json"])
    out = capsys.readouterr().out
    assert "s3cr3t" not in out, (
        f"the census echoed the URI's userinfo: {out[-2000:]}")
    assert "no graph name" in out, out[-2000:]
    assert rc == 2, "an unmeasurable census must not exit 0"


def test_the_uri_path_graph_is_the_PATH_not_the_authority(monkeypatch, capsys):
    """With `--graph`, the recorded `uri_path_graph` is the path — or None.

    `''` would itself be a lie (`the URI names an empty graph`), so a path-less
    URI records None; the pre-fix code recorded the userinfo here too.
    """
    seen: dict = {}

    def _census(graph, name, graph_names=None, uri_graph_name=None):
        seen["name"] = name
        seen["uri_path_graph"] = uri_graph_name
        return {"graph": name, "at": "t", "labels": {},
                "catalogue": "UNMEASURED(spy)"}

    monkeypatch.setattr(probe, "_connect", lambda uri: (_SpyDB(), ["g1"]))
    monkeypatch.setattr(probe, "vector_census", _census)
    rc = probe.main(["--census", "--uri", "docker://:s3cr3t@127.0.0.1:6399",
                     "--graph", "safe_graph", "--json"])
    out = capsys.readouterr().out
    assert seen["name"] == "safe_graph", seen
    assert seen["uri_path_graph"] is None, seen
    assert "s3cr3t" not in out, out[-2000:]
    assert rc == 2, rc

    # ...and a URI that DOES carry a path still records it.
    rc = probe.main(["--census", "--uri", "docker://:s3cr3t@127.0.0.1:6399/real",
                     "--json"])
    capsys.readouterr()
    assert seen["name"] == "real", seen
    assert seen["uri_path_graph"] == "real", seen
    assert rc == 2, rc


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

    def ro_query(self, cypher, params=None, timeout=None):
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
    # ⛔ The documented contract, not merely "it raised": a non-int SystemExit
    # argument exits 1, and this refusal MEANS "could not measure" (review P3).
    assert raised.value.code == 2, raised.value.code


# ── The exit contract the docstring promises ───────────────────────


def test_an_unmeasurable_run_exits_two(monkeypatch):
    """

    `0 = ran` (whatever the verdicts), `2 = could not measure`. The docstring
    promised `2` and two of the three routes returned `1`, which is the same
    class of defect as the epic body this PR repaired: a contract in prose the
    code never implements (review P3). All three routes are pinned here.
    """
    # (a) a gate module the instrument scores that will not import.
    def _no_gate():
        raise ImportError("no module named 'tortoise.embed_gate'")

    monkeypatch.setattr(probe, "score_rule_bullet", _no_gate)
    assert probe.main(["--json"]) == 2
    monkeypatch.undo()

    # (b) a census URI whose engine has no index API (the `_connect` refusal).
    with pytest.raises(SystemExit) as raised:
        probe.main(["--census", "--uri", "file:///tmp/whatever.db"])
    assert raised.value.code == 2, raised.value.code

    # (c) AND a run that DID measure is `0` even when a row is NOT MET or
    # PARTIAL — a caller must never read a red verdict as "the tool failed".
    assert probe.main(["--json"]) == 0


# ── The pins must actually RUN (review P1) ─────────────────────────


def test_the_instrument_is_selected_by_its_own_subjects():
    """

    A falsifier that never executes is not a falsifier (review P1).

    Registration alone is CLASSIFICATION (#4348): it satisfies
    `ci_selection.py --integrity` but does not make a diff SELECT this file. Each
    subject below must select it, or the pins this instrument exists to install
    never run on the change that can break them. Two named-surface matches
    REPLACE the `core` fallback (#2938), which is why the file is registered in
    `eval` as well as `core` (`tortoise/embeddings.py` — the seam — selects
    `eval`), and why `tools/dod_5090_probe.py` needs a `CORE_ALSO` entry (a
    `tools/`-only diff is swallowed by NON_PYTHON_PREFIXES before the fallback
    runs, so it selected 70 unrelated files and not this one).
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


# ── The falsifiers the review asked for ────────────────────────────────────


def test_a_write_path_outside_the_gate_is_counted(tmp_path, monkeypatch):
    """Bullet 1 must SEE an ungated writer (review P1 #4) — a map that only
    looks for the gated call cannot report the bypass it is meant to catch."""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n"
        "def compute_session_embedding(t):\n    return [0.0]\n", encoding="utf-8")
    (tmp_path / "tortoise" / "writer.py").write_text(
        "from .embeddings import compute_session_embedding\n"
        "def main(event):\n"
        "    emb = compute_session_embedding('x')\n"
        "    set_embedding_clause('e')\n"
        "    return emb\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    writers = probe.embedding_writers()
    assert writers["tortoise.writer::main"]["kind"] == "ungated", writers


def test_the_predicate_count_searches_the_embed_path_not_a_whitelist(
        tmp_path, monkeypatch):
    """A THIRD pattern-owning module on the embed path is drift (review P1 #2).

    A two-file whitelist reports this tree as drift-free; the import-graph walk
    cannot.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_A = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nfrom . import embed_rules\n"
        "RULE_SET = value_gate.RULES\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embed_rules.py").write_text(
        "import re\n_B = re.compile(r'\\d{4}')\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embeddings.py").write_text("# seam\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert "tortoise/embed_rules.py" in counted["owners"], counted
    assert counted["verdict"] == "DRIFT", counted


def test_measure_restores_the_gate_flags(monkeypatch):
    """A read-only instrument must not leave the caller's process un-gated."""
    monkeypatch.setenv("TORTOISE_EMBED_GATE", "1")
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    probe.measure()
    assert os.environ["TORTOISE_EMBED_GATE"] == "1"
    assert os.environ["TORTOISE_VALUE_GATE"] == "1"


def test_measure_pins_the_gate_flags_to_an_explicit_off(monkeypatch):
    """Step 0 of the instrument — pinned by the state the ROWS are scored in.

    `#10996` made `TORTOISE_VALUE_GATE` default ON, so a `measure()` that merely
    POPPED the switches produced rows measured against a gate that was ARMED
    while the instrument declared it idle. `measure()` now writes `"0"`
    explicitly, and until this pin existed **nothing observed it**: the mutation
    the review ran (delete the explicit write, keep the pop) left the suite
    green, because `test_measure_restores_the_gate_flags` above only reads the
    environment AFTER the run, which the `finally` block restores either way.

    The assertion is on the RESOLVED predicate, not on the spelling: the
    instrument's claim is "the gates were off while these rows were measured",
    and only the resolver can say whether that was true. A future default flip
    reds this test instead of silently re-arming the instrument.
    """
    from tortoise import embed_gate, value_gate

    flags = ("TORTOISE_EMBED_GATE", "TORTOISE_VALUE_GATE")
    seen: dict = {}

    def _spy():
        # The state the rows are actually scored in — sampled INSIDE the run.
        seen["env"] = {var: os.environ.get(var) for var in flags}
        seen["value_gate"] = value_gate.value_gate_enabled()
        seen["embed_gate"] = embed_gate.embed_gate_enabled()
        return []

    # An ambient ON value must not survive into the measurement: a lane's own
    # shell is exactly what Step 0 exists to neutralise.
    monkeypatch.setenv("TORTOISE_EMBED_GATE", "1")
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    monkeypatch.setattr(probe, "_measure_rows", _spy)
    probe.measure()

    assert seen["env"] == {var: "0" for var in flags}, (
        "measure() did not PIN the gate flags to an explicit OFF — a pop-only "
        "Step 0 reads as OFF only while the flag defaults OFF, and "
        "TORTOISE_VALUE_GATE now defaults ON (#10996)")
    assert seen["value_gate"] is False, (
        "the rows were measured with the S2.2 mechanical predicate ARMED while "
        "the report declares it idle")
    assert seen["embed_gate"] is False, (
        "the rows were measured with the embed gate ARMED while the report "
        "declares it idle")


# ── The census, on its POSITIVE path (all three tests above were negative) ──


class _CensusGraph:
    """A fake engine: a catalogue, a label list, and per-label vector counts.

    Exists because every census test before it asserted a FAILURE path, which
    is how three false-`MET` shapes survived a review (the `unindexed` column,
    the population, and the payload arithmetic had no positive coverage).
    """

    def __init__(self, catalogue, labels, vectors, dim=None):
        self._catalogue = catalogue
        self._labels = labels
        self._vectors = vectors  # {label: (count, [1.0]*width | None)}
        self._dim = dim

    # ⛔ BOTH verbs answer through `_answer`, because the census must issue its
    # reads through `ro_query` (the engine's read-only verb) and a fake that
    # implemented only the write-capable `query` would make a revert invisible.
    # Subclasses override `_answer`, never one verb, so neither can go untested.
    def query(self, cypher, params=None, timeout=None):
        return self._answer(cypher)

    def ro_query(self, cypher, params=None, timeout=None):
        return self._answer(cypher)

    def _answer(self, cypher):
        if "db.indexes()" in cypher:
            rows = self._catalogue
        elif "db.labels()" in cypher:
            rows = [[lab] for lab in self._labels]
        elif "RETURN count(n)" in cypher:
            label = cypher.split("MATCH (n:")[1].split(")")[0].strip("`")
            rows = [[self._vectors.get(label, (0, None))[0]]]
        elif "RETURN n.embedding" in cypher:
            label = cypher.split("MATCH (n:")[1].split(")")[0].strip("`")
            rows = [[self._vectors.get(label, (0, None))[1]]]
        else:
            rows = []
        return type("_R", (), {"result_set": rows})()


def _index_row(label, field, dim):
    return [label, [field], {field: ["VECTOR"]},
            {field: {"dimension": dim}}, "english", [], "NODE", "OPERATIONAL", {}]


def test_the_census_reports_an_index_on_ANOTHER_field_as_unindexed():
    """An index on the LABEL is not an index on `embedding` (review P1-1).

    Measured failure this pins: a `(Source, vec)` index made a stored
    `Source.embedding` vector report `indexed=True, unindexed=0`.
    """
    graph = _CensusGraph(
        catalogue=[_index_row("Source", "vec", 384)],
        labels=["Source", "Chunk"],
        vectors={"Source": (1, [0.0] * 384), "Chunk": (2, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                graph_names=["g"])
    assert census["labels"]["Source"]["indexed"] is False
    assert census["labels"]["Source"]["unindexed"] == 1
    # …and the population comes from the GRAPH's labels, so a stored-and-
    # neither-indexed-nor-served label is REPORTED, not omitted (review P1-2).
    assert "Chunk" in census["labels"], sorted(census["labels"])
    assert census["labels"]["Chunk"]["unindexed"] == 2


def test_the_census_payload_needs_the_probe_and_the_index_to_agree():
    """The byte figure is derived, so it must not multiply a different width."""
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"],
        vectors={"Point": (2, [0.0] * 4)},  # stored width 4, index dim 384
    )
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                graph_names=["g"])
    payload = census["labels"]["Point"]["payload"]["derived_payload_B"]
    assert isinstance(payload, str) and payload.startswith("UNMEASURED"), payload
    # Agreeing width/dim DOES derive it.
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"],
        vectors={"Point": (2, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                graph_names=["g"])
    assert census["labels"]["Point"]["payload"]["derived_payload_B"] == 2 * 384 * 4


def test_the_census_does_not_claim_coverage_it_could_not_measure():
    """`unindexed = 0` needs BOTH numbers; with one unreadable it is a claim.

    Measured failure this pins (review round 7, P2): a covering `embedding`
    index plus an unreadable stored width printed `unindexed=0` — full coverage,
    measured-looking — beside `payload=UNMEASURED(stored width not probeable)`
    in the same row. `0` is the one column this instrument quotes into `#5090`'s
    body as the epic's cost figure, so it is the last column allowed to say `0`
    without having measured anything.
    """
    # (a) the width probe returned nothing (a vecf32 read that yielded no row)
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"],
        vectors={"Point": (5, None)},
    )
    row = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                              graph_names=["g"])["labels"]["Point"]
    assert row["indexed"] is True, row
    assert str(row["unindexed"]).startswith("UNMEASURED"), row
    assert "stored width" in row["unindexed"], row

    # (b) the width probe RAISED — the same branch via the swallowed exception
    class _RaisingWidth(_CensusGraph):
        def _answer(self, cypher):
            if "RETURN n.embedding" in cypher:
                raise RuntimeError("Timeout reading from socket")
            return super()._answer(cypher)

    graph = _RaisingWidth([_index_row("Point", "embedding", 384)], ["Point"],
                          {"Point": (5, [0.0] * 384)})
    row = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                              graph_names=["g"])["labels"]["Point"]
    assert str(row["unindexed"]).startswith("UNMEASURED"), row

    # (c) the catalogue row carries no dimension — `dimension` is REQUIRED in a
    # vector index's OPTIONS, so its absence is an unreadable cell, not a label
    # with no dimension.
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", None)],
        labels=["Point"],
        vectors={"Point": (5, [0.0] * 384)},
    )
    row = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                              graph_names=["g"])["labels"]["Point"]
    assert str(row["unindexed"]).startswith("UNMEASURED"), row
    assert "index dimension" in row["unindexed"], row


def test_the_census_still_reports_zero_when_there_is_nothing_to_cover():
    """The refusal above must not over-refuse: 0 stored vectors needs no width.

    `stored == 0` is a measured zero (`MATCH … RETURN count(n)` answered), so
    `unindexed = 0` is provable without a probe — pinning it keeps the new
    `UNMEASURED` branch from turning an empty label into an unmeasured one.
    """
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"],
        vectors={"Point": (0, None)},
    )
    row = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                              graph_names=["g"])["labels"]["Point"]
    assert row["stored"] == 0, row
    assert row["unindexed"] == 0, row
    assert row["payload"]["derived_payload_B"] == 0, row


def test_the_census_measures_the_graph_its_row_NAMES(monkeypatch):
    """`--graph B` with a `--uri …/A` must query B (review P1, no falsifier)."""
    selected: list[str] = []

    class _Db:
        def select_graph(self, name):
            selected.append(name)
            return _CensusGraph([], [name], {})

    monkeypatch.setattr(probe, "_connect", lambda uri: (_Db(), ["A", "B"]))
    rc = probe.main(["--census", "--json",
                     "--uri", "docker://:@127.0.0.1:1/A", "--graph", "B"])
    assert rc == 0, rc
    assert selected == ["B"], selected


def test_an_unparseable_module_holds_the_rows(tmp_path, monkeypatch):
    """A tree that will not fully parse must not read as clean (review P2-1),
    and more than one of them must not collapse into a single opaque entry
    (review round 5, P3)."""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    (tmp_path / "tortoise" / "writer.py").write_text(
        "from .embeddings import encode_for_row\n"
        "def _upsert_point(ev):\n"
        "    return 'MERGE (n:Point {id: 1}) SET n.embedding = $e'\n",
        encoding="utf-8")
    (tmp_path / "tortoise" / "broken.py").write_text(
        "def oops(:\n", encoding="utf-8")
    (tmp_path / "tortoise" / "broken_two.py").write_text(
        "def oops_again(:\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.unparsed_modules() == [
        "tortoise/broken.py", "tortoise/broken_two.py"
    ], probe.unparsed_modules()
    assert probe.score_path_bullet().verdict == "PARTIAL"


def test_the_predicate_walk_follows_subpackages_and_absolute_imports(
        tmp_path, monkeypatch):
    """The two import shapes that made a second predicate read MET (review P1-3)."""
    t = tmp_path / "tortoise"
    (t / "pkg").mkdir(parents=True)
    (t / "pkg" / "__init__.py").write_text(
        "import re\n_P = re.compile(r'x')\n", encoding="utf-8")
    (t / "abs_rules.py").write_text("import re\n_A = re.compile(r'y')\n",
                                    encoding="utf-8")
    (t / "value_gate.py").write_text(
        "import re\n_V = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (t / "embed_gate.py").write_text(
        "from . import value_gate, pkg\n"
        "from tortoise.abs_rules import _A\n"
        "RULE_SET = value_gate.RULES\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    assert counted["count"] == 3, counted
    assert counted["verdict"] == "DRIFT", counted


def test_the_fallback_probe_restores_the_circuit_breakers():
    """A read-only probe must not un-trip a breaker a live process opened.

    Pinned because the FIRST attempt at this restore (`deepcopy` of
    `_BREAKERS`) raised `TypeError: cannot pickle '_thread.lock' object` the
    moment a breaker was registered — i.e. in exactly the state it existed to
    protect, and only in a process that had already run a real search. A fresh
    pytest process hid it.
    """
    from tortoise.search_engine import _breaker

    b = _breaker("vector")
    b.record_failure()
    before = (b._fails, b._open_until, b._probing)
    probe.fallback_probe()
    after = (b._fails, b._open_until, b._probing)
    assert after == before, (before, after)


def test_the_fallback_probe_leaves_no_breaker_it_CREATED_behind():
    """A FRESH process starts with an EMPTY `_BREAKERS` — leave it empty.

    The other direction is `…_restores_the_circuit_breakers` above, and it is the
    only state that direction can see: it PRE-CREATES the breaker, so the probe's
    snapshot is non-empty. A fresh process has no such entry — that is the CLI's
    state, `measure()`'s state, and this file's first test's — so the restore
    iterated an empty snapshot and the breaker `_probe_mechanisms` registers
    lazily (`_breaker_record("vector", …)` → `search_engine._breaker`) was never
    restored by anything.

    Measured at `37fa883bd` before the fix (review P3, reproduced verbatim):
    `before: {}` → `after: {'vector': (0, 0.0, False)}` — an entry this read-only
    instrument created and left in the caller's module. The slot values alone
    would not have caught it, which is why the assertion is over the MAP.
    """
    import tortoise.search_engine as se

    saved = dict(se._BREAKERS)
    se._BREAKERS.clear()
    try:
        probe.fallback_probe()
        after = {name: (b._fails, b._open_until, b._probing)
                 for name, b in se._BREAKERS.items()}
        assert after == {}, after
    finally:
        se._BREAKERS.clear()
        se._BREAKERS.update(saved)


def test_a_failed_population_read_is_unmeasured_not_a_narrowed_population():
    """`CALL db.labels()` failing must not fall back to `indexed | served`."""

    class _NoLabels(_CensusGraph):
        def _answer(self, cypher):
            if "db.labels()" in cypher:
                raise RuntimeError("Timeout reading from socket")
            return super()._answer(cypher)

    graph = _NoLabels([_index_row("Point", "embedding", 384)], ["Point"],
                      {"Point": (1, [0.0] * 384)})
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                 graph_names=["g"])
    assert census["labels"] == {}
    assert census["catalogue"].startswith("UNMEASURED"), census["catalogue"]


def test_an_unreadable_catalogue_row_is_not_an_unindexed_label():
    """A row we could not read must not assert `indexed: False`."""
    graph = _CensusGraph(
        catalogue=[["Point", ["embedding"], "not-a-dict"]],
        labels=["Point"], vectors={"Point": (7, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                 graph_names=["g"])
    row = census["labels"]["Point"]
    assert isinstance(row["indexed"], str) and row["indexed"].startswith("UNMEASURED"), row
    assert str(row["unindexed"]).startswith("UNMEASURED"), row


def test_an_UNATTRIBUTABLE_catalogue_row_holds_EVERY_label_UNMEASURED():
    """A catalogue row with no readable label cell is every label's row.

    Measured failure this pins (round-23 review, P3): `unreadable` holds
    `(label-or-None, why)` entries and the per-label filter was
    `lab == label`. `label` is ALWAYS a str (the population is
    `graph_labels | indexed | served_labels()`), so an entry keyed `None`
    matched NOTHING — `unreadable_here` was empty for every label, both columns
    took the MEASURED branch, and a graph whose `CALL db.indexes()` answered one
    empty row reported `indexed=False, unindexed=N` for `Point` — which in this
    test carries a VECTOR index on `embedding` — and for every other label.
    That is the exact inversion of the comment beside the column ("An unreadable
    catalogue row is NOT 'this label has no index'") and of the function's own
    docstring ("either measured or `UNMEASURED(<why>)` — never `0`").

    The empty row is the only SHAPE that reaches a `None` label: the
    pre-existing falsifier drives `["Point", ["embedding"], "not-a-dict"]`,
    whose label IS recoverable, so the `UNMEASURED` branch was taken there and
    this hole went unnoticed.
    """
    graph = _CensusGraph(
        catalogue=[[]],
        labels=["Point", "Source"],
        vectors={"Point": (3, [0.0] * 384), "Source": (2, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                graph_names=["g"])
    assert census["unreadable_catalogue_rows"] == [(None, "short row []")], (
        census["unreadable_catalogue_rows"])
    for label in ("Point", "Source"):
        row = census["labels"][label]
        assert isinstance(row["indexed"], str), (label, row)
        assert row["indexed"].startswith("UNMEASURED"), (label, row)
        assert isinstance(row["unindexed"], str), (label, row)
        assert row["unindexed"].startswith("UNMEASURED"), (label, row)
        # The reason is NAMED…
        assert "short row []" in row["indexed"], (label, row)
        # …and it is NOT attributed to a label literally called `None`, which
        # is what a `lab == label or lab is None` fix would have printed.
        assert "None:" not in row["indexed"], (label, row)
        # The STORED count is still MEASURED: the fix poisons the two
        # catalogue-derived columns, not the whole census (a fix that returned
        # UNMEASURED everywhere would hide a figure that DID measure).
        assert not isinstance(row["stored"], str), (label, row)
        assert row["stored"] == {"Point": 3, "Source": 2}[label], (label, row)


def test_a_backtick_in_a_graph_label_cannot_re_purpose_the_census_query():
    """A label is QUOTED by the repo's identifier rule, never concatenated raw.

    `label` comes from `CALL db.labels()` / the index catalogue — the graph's own
    schema, i.e. graph-controlled input reaching Cypher by interpolation (a label
    cannot be a query parameter). The pre-fix `f"MATCH (n:`{label}`) …"` let a
    label carrying a backtick close the quoted identifier and append Cypher of
    its own. The repo's one rule for this shape is
    `graph_key_restore._quote_identifier` (bare when bare, else backtick-quoted
    with interior backticks DOUBLED) — round 15 review, P3.

    The payload is EXECUTED through the census rather than read off a source
    line, so a later rewrite that stops quoting reds here instead of shipping.
    """
    hostile = "x`) DETACH DELETE n //"
    emitted: list[str] = []

    class _Hostile(_CensusGraph):
        def _answer(self, cypher):
            emitted.append(cypher)
            return super()._answer(cypher)

    probe.vector_census(_Hostile([], [hostile], {}), "g", at="t",
                        graph_names=["g"])
    counts = [q for q in emitted if "RETURN count(n)" in q]
    target = [q for q in counts if "x" in q]
    assert target, counts
    quoted = "`" + hostile.replace("`", "``") + "`"
    assert quoted in target[0], (
        f"the label reached Cypher unquoted — the payload's backtick closes the "
        f"identifier: {target[0]}")
    # ⛔ The RAW form is what closed the identifier, and it is emitted nowhere:
    # the payload's backtick exists only inside a doubled pair.
    assert f"`{hostile}`" not in " ".join(counts), counts
    # …and the payload's own backtick is the ONLY doubled one, so the doubling is
    # the escape and not an artefact of the payload happening to contain it.
    assert target[0].count("``") == 1, target[0]


def test_the_census_reads_through_the_READ_ONLY_verb():
    """The census reads through `GRAPH.RO_QUERY`, not the write-capable verb.

    "This module stores nothing" was a convention held only by writing
    read-shaped statements through `graph.query` — the verb that CAN write.
    `ro_query` is rejected server-side for a write, so the doctrine becomes the
    engine's to enforce (round 15 review, P3). A fake that implemented only
    `query` could never show this: the handle below REFUSES `query` and the
    census must still measure it.
    """

    class _WriteVerbForbidden(_CensusGraph):
        def query(self, cypher, params=None, timeout=None):
            raise AssertionError(
                f"the census read through the write-capable verb: {cypher}")

    row = probe.vector_census(
        _WriteVerbForbidden(catalogue=[_index_row("Point", "embedding", 384)],
                            labels=["Point"],
                            vectors={"Point": (3, [0.0] * 384)}),
        "g", at="2026-11-01T00:00:00Z", graph_names=["g"],
    )["labels"]["Point"]
    assert row["stored"] == 3, row
    assert row["indexed"] is True, row
    assert row["payload"]["derived_payload_B"] == 3 * 384 * 4, row


def test_the_TEXT_path_PRINTS_an_unreadable_catalogue_row(monkeypatch, capsys):
    """A census that MEASURED fine must not exit 2 on its own print statement.

    Measured failure this pins (round 15 review, P2):
    `unreadable_catalogue_rows` holds `(label-or-None, why)` TUPLES and the text
    printer joined them as if they were strings, so `TypeError: sequence item 0:
    expected str instance, tuple found` unwound into `cli()`'s blanket handler —
    a catalogue that was read perfectly reported `UNMEASURED(TypeError: …)` and
    exit 2, the instrument's "could not measure" code. `--json` renders tuples
    fine, and `test_an_unreadable_catalogue_row_is_not_an_unindexed_label` calls
    `vector_census()` directly, which is why nothing caught it: the defect lives
    on the PRINT path.
    """

    class _Db:
        def select_graph(self, name):
            return _CensusGraph(
                catalogue=[["Point", ["embedding"], "not-a-dict"]],
                labels=["Point"], vectors={"Point": (7, [0.0] * 384)},
            )

    monkeypatch.setattr(probe, "_connect", lambda uri: (_Db(), ["g"]))
    rc = probe.cli(["--census", "--uri", "docker://:@127.0.0.1:1/g",
                    "--graph", "g"])
    out, err = capsys.readouterr()
    assert "UNMEASURED(TypeError" not in err, err
    assert "types cell is str" in out, out[-2000:]
    assert rc == 0, (rc, err)


def test_the_predicate_walk_follows_plain_import_and_multiline_from(
        tmp_path, monkeypatch):
    """`import tortoise.X` and `from .pkg import (\n a,\n)` — both in this tree."""
    t = tmp_path / "tortoise"
    (t / "pkg").mkdir(parents=True)
    (t / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (t / "pkg" / "sub_rules.py").write_text("import re\n_S = re.compile(r'z')\n",
                                            encoding="utf-8")
    (t / "abs_rules.py").write_text("import re\n_A = re.compile(r'y')\n",
                                    encoding="utf-8")
    (t / "value_gate.py").write_text("import re\n_V = re.compile(r'#\\d+')\nRULES=('r',)\n",
                                     encoding="utf-8")
    (t / "embed_gate.py").write_text(
        "import tortoise.abs_rules as ar\n"
        "from .pkg import (\n    sub_rules,\n)\n"
        "from . import value_gate\nRULE_SET = value_gate.RULES\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    assert counted["count"] == 3, counted
    assert counted["verdict"] == "DRIFT", counted


def test_the_shipped_default_is_searched_not_assumed(monkeypatch):
    """The default is READ from the shipped surfaces, and the row reports it.

    ⛔ Deliberately NOT `assert shipped["on"] is False`. That pins TODAY's
    answer to a question `#5090` intends to change — row 1's recorded remainder
    is "turn the flag on and measure the rate on real rows" — so the day it is
    done this falsifier would red on a CORRECT change, and the cheapest-looking
    repair would be to weaken it: a pinned answer to a moving question is the
    stale-flag class `#10465` exists to end (review round 7, P3). What is pinned
    here is the real-tree MECHANISM the synthetic roots cannot cover: the walk
    reaches the repository's own shipped surfaces, an ON claim is sourced from a
    file the walk actually read, and `score_rule_bullet` reports the SAME read
    rather than only the ambient env — `measure()` clears the env, so an
    env-only read would report `False` forever and bullet 1 could never see a
    shipped default.
    """
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert isinstance(shipped["on"], bool), shipped
    # ⛔ `searched` is the WALK (every file this repo's shipped surfaces hold),
    # `mentions` is the subset that names the flag. The previous version of this
    # falsifier asserted `shipped["searched"]` was non-empty and justified it as
    # "the flag is mentioned in the tree, so an empty list means the walk missed
    # a path" — false, because `searched` then held only the mentions, so an
    # empty list meant only "nothing mentions it", which is the expected state
    # for an unset flag. It passed on a prose sentence in
    # `config/v4-mechanisms.yml`; reflow that sentence and it red with a message
    # sending the fixer after a non-existent walk bug.
    assert shipped["searched"], shipped
    assert set(shipped["mentions"]) <= set(shipped["searched"]), shipped
    assert not set(shipped["unreadable"]) & set(shipped["searched"]), shipped
    for hit in shipped["files"]:  # an ON claim is SOURCED, never asserted
        assert hit.split(":")[0] in shipped["searched"], hit
    row = probe.score_rule_bullet()
    note = next(n for n in row.notes if n.startswith("gate enabled by default:"))
    assert f"gate enabled by default: {shipped['on']}" in note, (note, shipped)


@pytest.mark.parametrize("text", [
    # Plain prose in a `note: >-` folded scalar — the natural documentation
    # form of the flag this repo's own `config/v4-mechanisms.yml` describes.
    "note: >-\n  Set TORTOISE_EMBED_GATE=1 to enable the gate.\n",
    # The same sentence with the flag in backticks (today's shape in
    # `config/v4-mechanisms.yml:431`).
    "note: >-\n  Set `TORTOISE_EMBED_GATE=1` to enable the gate.\n",
    # A GitHub-Actions step NAME. `stripped[0]` is `-`, so the pre-fix guard
    # did not skip it, `lstrip("-")` left `name: verify …`, and the value
    # token after the flag was `1`.
    "jobs:\n  t:\n    steps:\n      - name: verify TORTOISE_EMBED_GATE=1 takes effect\n",
    # A COMMENTED list item: the `#` test ran BEFORE the dash strip, so the
    # comment marker was the second character and this read ON.
    "environment:\n  - # TORTOISE_EMBED_GATE=1\n",
    # A bare mention with no separator at all.
    "note: >-\n  (`TORTOISE_EMBED_GATE`), off by default\n",
    # A compose ARRAY declaration with no value: the spec says the variable is
    # then unset, so it is not an ON.
    "environment:\n  - TORTOISE_EMBED_GATE\n",
    # An explicitly OFF value, including compose's own `false` spelling.
    "environment:\n  TORTOISE_EMBED_GATE: \"false\"\n",
])
def test_a_line_that_merely_mentions_the_flag_is_not_a_shipped_default(
        text, tmp_path, monkeypatch):
    """Only an ASSIGNMENT of the flag can read as shipped ON.

    Measured on the pre-fix code: `Set TORTOISE_EMBED_GATE=1 to enable`,
    `- name: verify TORTOISE_EMBED_GATE=1 takes effect` and
    `- # TORTOISE_EMBED_GATE=1` each returned `on=True`. That is the one input
    that can move bullet 1 toward `MET` (`score_rule_bullet` → `on_by_default`),
    so once `#5090`'s stated remainder (turn the flag on, resolve the writers) is
    done, a DOCUMENTATION edit alone would complete a DoD row — the class
    `#10465` indicator 1 exists to prevent.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "x.yml").write_text(text, encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    # It is still RECORDED — a mention is reported, just not scored as a value.
    assert shipped["mentions"] == ["config/x.yml"], shipped


@pytest.mark.parametrize("text", [
    "environment:\n  - TORTOISE_EMBED_GATE=1\n",            # compose array
    "environment:\n  TORTOISE_EMBED_GATE: \"true\"\n",      # compose map
    "environment:\n  TORTOISE_EMBED_GATE : 1\n",           # YAML spaced key
    "TORTOISE_EMBED_GATE=yes\n",                            # .env / dotenv
    "ENV TORTOISE_EMBED_GATE=on\n",                          # Dockerfile
    "export TORTOISE_EMBED_GATE=1\n",                        # shell
    "TORTOISE_EMBED_GATE=1  # enable it\n",                  # trailing comment
])
def test_every_real_assignment_shape_still_reads_as_shipped_ON(
        text, tmp_path, monkeypatch):
    """The tighter test must not turn a REAL shipped ON into a false `False`.

    These are the shapes `container_spec.md` §environment defines (map and
    array) plus the shell/Dockerfile spellings; a walk that stopped seeing them
    would let bullet 1 report `off by default` for a flag that ships ON.
    """
    (tmp_path / ".env.example").write_text(text, encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is True, shipped
    assert shipped["files"], shipped


def test_an_assignment_shaped_line_inside_a_block_scalar_is_not_a_shipped_default(
        tmp_path, monkeypatch):
    """A `>-` body is SCALAR CONTENT, so `KEY: 1` there is prose, not a setting.

    YAML 1.2.2 models block scalars as a *scalar node style* (§8.1), separate
    from *block mappings* (§8.2.2) — *"Block scalar formats … all express
    strings"* — so nothing inside the body is a mapping entry. The line scan had
    no notion of structure and read it as a shipped ON, and `on_by_default` is
    the clause that separates bullet 1's `PARTIAL` from `MET`.

    This is the one shape in the file whose error direction is toward `MET`, and
    the natural edit is the one that trips it: this repo already documents THIS
    flag inside a `>-` block (`config/v4-mechanisms.yml:426-433`), so adding a
    usage line there must not complete a DoD row (review, P3).
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "x.yml").write_text(
        "meta:\n"
        "  note: >-\n"
        "    TORTOISE_EMBED_GATE: 1\n"
        "    A usage line here is documentation, not a shipped default.\n"
        "  off_by_default: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    # …and the exclusion is REPORTED, because it is a decision about a line that
    # looks exactly like a setting. A silent skip would be a new hole.
    assert shipped["documented_only"] == 1, shipped


def test_an_executed_workflow_run_body_is_a_shipped_setting(
        tmp_path, monkeypatch):
    """A `run: |` body is a SHELL SCRIPT, not documentation (round 17, P3).

    YAML models a block scalar as a scalar node (§8.1), so no line inside it is
    a mapping entry — but that says nothing about whether the text is a VALUE.
    On `.github/workflows` a `run:` body is executed by the shell (*"each line
    runs in the same shell"*), so `export FLAG=1` there IS a shipped setting.
    Counting it into `documented_only` printed `set ON in: none` over a workflow
    that sets it — the measured-looking-nowhere shape this instrument refuses
    elsewhere, and the one surface where a gate flag is actually TURNED ON.
    """
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "jobs:\n"
        "  t:\n"
        "    steps:\n"
        "      - name: run the suite with the embed gate ON\n"
        "        run: |\n"
        "          export TORTOISE_EMBED_GATE=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is True, shipped
    assert shipped["files"] == [
        ".github/workflows/ci.yml: export TORTOISE_EMBED_GATE=1"], shipped
    # It is a SETTING, so it must not be double-reported as documentation.
    assert shipped["documented_only"] == 0, shipped


def test_a_workflow_prose_block_is_still_documentation(
        tmp_path, monkeypatch):
    """The over-correction guard: not every workflow block scalar is a command.

    Only the KEY decides. A `note:` / `description:` body is text even inside a
    workflow, so scanning every block body as shell would let a documentation
    edit move `on_by_default` — the exact fail-open shape round 8 closed.
    """
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "jobs:\n"
        "  t:\n"
        "    steps:\n"
        "      - name: x\n"
        "        note: >-\n"
        "          TORTOISE_EMBED_GATE: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    assert shipped["documented_only"] == 1, shipped


def test_a_run_key_outside_the_workflow_surface_is_not_executed(
        tmp_path, monkeypatch):
    """`run:` is a shell command only where this repo's CI runs it.

    `config/*.yml` is read by tools, not by GitHub, so a `run:` key there is
    that tool's own field. Treating it as shell would let an unrelated config
    schema complete a DoD row.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "x.yml").write_text(
        "run: |\n"
        "  export TORTOISE_EMBED_GATE=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    assert shipped["documented_only"] == 1, shipped


def test_a_real_mapping_after_a_block_scalar_still_reads_as_shipped_ON(
        tmp_path, monkeypatch):
    """The block predicate must END where the scalar does.

    Skipping a body is only correct if the walk resumes at the next real
    mapping key; a scanner that never ends the block would drop genuine
    assignments and report `off by default` for a flag that ships ON.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  app:\n"
        "    note: >-\n"
        "      TORTOISE_EMBED_GATE is documented here, not set.\n"
        "    environment:\n"
        "      TORTOISE_EMBED_GATE: \"true\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is True, shipped
    assert shipped["files"] == [
        'docker-compose.yml: TORTOISE_EMBED_GATE: "true"'], shipped


def test_searched_is_the_walk_not_the_list_of_mentions(tmp_path, monkeypatch):
    """`searched` must answer "what did 'nowhere' cover?" — the documented contract.

    Pre-fix, this file (which does not mention the flag) was absent from
    `searched`, so for the ordinary case the function is built for — a flag set
    NOWHERE and mentioned NOWHERE — the promised coverage was `[]`. That is the
    same defect class as the exit-code contract `test_an_unmeasurable_run_exits_two`
    was added for: a documented promise the code does not implement.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "quiet.yml").write_text("nothing here\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    assert shipped["searched"] == ["config/quiet.yml"], shipped
    assert shipped["mentions"] == [], shipped


def test_an_unreadable_surface_is_named_not_silently_excluded(tmp_path, monkeypatch):
    """A file the walk could not read must not read as a measured `False`."""
    (tmp_path / ".env.example").write_text("TORTOISE_EMBED_GATE=1\n",
                                           encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    real_read_text = Path.read_text

    def _boom(self, *a, **kw):
        if self.name == ".env.example":
            raise OSError("unreadable")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    assert shipped["unreadable"] == [".env.example"], shipped
    assert shipped["searched"] == [], shipped


def test_the_reader_answers_from_the_4097_TRUTHY_contract(tmp_path, monkeypatch):
    """A SIXTH mirror of the truthiness vocabulary would fail bullet 1 OPEN.

    `tortoise/env_truthy.py` is *"The single declared env-truthiness contract
    (#4097)"*, and `embed_gate.embed_gate_enabled()` (the gate itself) reads it.
    A local vocabulary here is unpinned to that contract, so if the contract
    narrowed — `env_truthy`'s own docstring records V3, truthy-minus-`on`, as a
    divergence that made `=on` silently do nothing — `FLAG=on` would read ON to
    this instrument and OFF to the gate. That is the one input that moves
    bullet 1 toward `MET` without measuring (review round 10, P3).
    """
    import tortoise.env_truthy as env_truthy

    (tmp_path / ".env.example").write_text("TORTOISE_EMBED_GATE=on\n",
                                           encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")["on"] is True

    # Narrow the CONTRACT (V3: `on` is not truthy there any more) and the
    # instrument must narrow with it, because the gate reads the same set.
    monkeypatch.setattr(env_truthy, "TRUTHY", frozenset({"1", "true", "yes"}))
    assert probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")["on"] is False, (
        "the reader answered from its own copy of the vocabulary, not #4097's: "
        "`FLAG=on` reads ON here and OFF to embed_gate_enabled()`"
    )
    assert not hasattr(probe, "TRUTHY"), "the tool carries a second copy of the contract"


def test_a_writer_that_names_an_unserved_label_scores_NOT_MET(tmp_path, monkeypatch):
    """The VERDICT, not just the set difference (review round 3, P3)."""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    (tmp_path / "tortoise" / "writer.py").write_text(
        "from .embeddings import encode_for_row\n"
        "def _upsert_document(ev):\n"
        "    emb = encode_for_row('x', 3)\n"
        "    return 'MERGE (n:Document {id: 1}) SET n.embedding = $emb', emb\n",
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.score_path_bullet().verdict == "NOT MET"


def test_a_commented_or_blank_flag_line_is_not_a_shipped_default(tmp_path, monkeypatch):
    """`# TORTOISE_EMBED_GATE=1` is not a default; `FLAG=` is not an IndexError.

    Measured before this pin: the commented line read ON (the one input that can
    flip bullet 1 to MET without measuring), and a blank value raised
    `IndexError` out of the whole instrument.
    """
    (tmp_path / ".env.example").write_text(
        "# TORTOISE_EMBED_GATE=1  # not shipped yet\n"
        "TORTOISE_EMBED_GATE=\n"
        "# see TORTOISE_EMBED_GATE\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")["on"] is False
    (tmp_path / ".env.example").write_text("TORTOISE_EMBED_GATE=true\n",
                                           encoding="utf-8")
    assert probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")["on"] is True


def test_a_flag_at_end_of_line_does_not_raise(tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text("see TORTOISE_EMBED_GATE\n",
                                           encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")["on"] is False


def test_a_collection_of_patterns_is_a_second_rule_set(tmp_path, monkeypatch):
    """`_PATS = [re.compile(…), re.compile(…)]` is the most natural second list."""
    t = tmp_path / "tortoise"
    t.mkdir()
    (t / "value_gate.py").write_text(
        "import re\n_V = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (t / "embed_gate.py").write_text(
        "import re\n_PATS = [re.compile(r'\\d{4}'), re.compile(r'\\bID\\b')]\n"
        "from . import value_gate\nRULE_SET = value_gate.RULES\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert counted["verdict"] == "DRIFT", counted


def test_served_labels_are_the_RUNNERS_declaration():
    """`VALID_ENTITY_TYPES` + `entity_label` — pinned to the ANSWER, not its clone.

    ⛔ The assertion used to be `probe.served_labels() == {entity_label(t) for t
    in VALID_ENTITY_TYPES}` — the implementation's own right-hand side, so it
    could not go red except by editing `served_labels()` to something
    VALUE-UNEQUAL. The declaration it claims to forbid
    (`ENTITY_TYPE_LABELS.values()`) is value-equal today, so even switching to
    the thing the docstring warns about stayed green: a falsifier with no
    discriminating power (review round 10, P3). It matters because a type the
    runner serves but the map does not would empty `served`, make
    `orphans = labels - served` non-empty, and flip bullet 2 to a false
    `NOT MET` — a figure this PR publishes into `#5090` — with the "pin" green.

    The literal reds on any widening of the runner's vocabulary and sends the
    reader to the one place that must agree.
    """
    assert probe.served_labels() == {
        "Point", "Event", "Subject", "Source", "Object"
    }



# ── Writer identity, and the guard that has to be able to RUN ───────────────


def test_two_package_inits_keep_distinct_writer_identities(tmp_path, monkeypatch):
    """`path.stem` is a BASENAME: every package `__init__.py` collapsed to it.

    Two writers in two packages — one per `__init__.py` — used to share the key
    `__init__::_upsert_point`, so the second became `duplicate`, which
    `written_labels()` excluded. The map reported ONE writer and bullet 2
    compared a label set with both writers missing from it (review round 5, P2).
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    for pkg, label in (("alpha", "Point"), ("beta", "Object")):
        (tmp_path / "tortoise" / pkg).mkdir()
        (tmp_path / "tortoise" / pkg / "__init__.py").write_text(
            "from ..embeddings import encode_for_row\n"
            "def _upsert_point(ev):\n"
            "    vec = encode_for_row('x', None)\n"
            f"    return 'MERGE (n:{label} {{id: 1}}) SET n.embedding = $vec'\n",
            encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    writers = probe.embedding_writers()
    assert "tortoise.alpha::_upsert_point" in writers, sorted(writers)
    assert "tortoise.beta::_upsert_point" in writers, sorted(writers)
    assert not [k for k in writers if k.startswith("__init__::")], sorted(writers)
    written = probe.written_labels(writers)
    assert written["tortoise.alpha::_upsert_point"] == "Point", written
    assert written["tortoise.beta::_upsert_point"] == "Object", written


def test_a_same_named_point_writer_in_another_module_gets_no_override(
        tmp_path, monkeypatch):
    """`WRITER_LABEL_OVERRIDES` is keyed by `module::name`, not by a bare name.

    The comment claimed `module::name` scoping while the LOOKUP was
    `node.name` — so a `_point` in ANY module was ASSIGNED `Point` with no
    Cypher evidence, handed to bullet 2 as the writer's own label, and reported
    as a documented override (review round 10, P3). There is one `_point`
    today, so nothing read wrong yet; `_point` is a plausible name (this tree
    carries `_point_key`, `_point_raw_refs`, …) and `embedding_writers()`
    promises "otherwise `UNKNOWN` — reported, never guessed".
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    for module in ("api", "other"):
        (tmp_path / "tortoise" / f"{module}.py").write_text(
            "from .embeddings import encode_for_row\n"
            "def _point(p):\n"
            "    vec = encode_for_row('x', None)\n"
            "    return vec\n",
            encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    writers = probe.embedding_writers()
    assert writers["tortoise.api::_point"]["label"] == "Point", writers
    assert writers["tortoise.other::_point"]["label"] == "UNKNOWN", (
        "a same-named writer in another module inherited the override: "
        f"{writers['tortoise.other::_point']}"
    )
    # ...and only the documented writer is REPORTED as overridden.
    assert probe._overridden_writers() == {"tortoise.api::_point"}, (
        probe._overridden_writers()
    )


def test_a_collision_is_classified_whichever_exit_writes_it(tmp_path, monkeypatch):
    """Every exit applies the COLLISION rule — the guard must not be order-dependent.

    `_embedding_writers` has four exits per function and only ONE of them
    consulted the guard, so the result depended on `ast.walk` order: when the
    FIRST definition of a key was `gated`/`ungated`/`producer` and a later one
    was an `unresolved_write` or a two-label writer, the later entry silently
    OVERWROTE the earlier one and `kind` never became `"duplicate"` — bullet
    1's duplicate note never fired and bullet 2's map carried the wrong
    producer class for a name that collided (this PR's review, P3).

    ⛔ This test DRIVES the detector. Every other duplicate test in this file
    `monkeypatch.setattr`s the writers map, so deleting the guard left the
    whole suite green — the removal direction had no falsifier at all.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    gated = (
        "def _upsert_point(ev):\n"
        "    vec = encode_for_row('x', None)\n"
        "    return 'MERGE (n:Point {id: 1}) SET n.embedding = $vec'\n"
    )
    # No recognised producer call, but the _EMBED_WRITE needle sees the clause,
    # so this is the exit that used to assign the map DIRECTLY (`unresolved_write`).
    unresolved = (
        "def _upsert_point(ev):\n"
        "    return 'MATCH (n:Point) SET n.embedding = $v'\n"
    )
    # Two labels in one writer's Cypher: the OTHER exit that assigned directly.
    twolabel = (
        "def _upsert_point(ev):\n"
        "    vec = encode_for_row('x', None)\n"
        "    a = 'MERGE (n:Point {id: 1}) SET n.embedding = $vec'\n"
        "    b = 'MERGE (m:Object {id: 1}) SET m.embedding = $vec'\n"
        "    return a + b\n"
    )
    (tmp_path / "tortoise" / "gated_then_unresolved.py").write_text(
        "from .embeddings import encode_for_row\n" + gated + unresolved,
        encoding="utf-8")
    (tmp_path / "tortoise" / "unresolved_then_gated.py").write_text(
        "from .embeddings import encode_for_row\n" + unresolved + gated,
        encoding="utf-8")
    (tmp_path / "tortoise" / "gated_then_twolabel.py").write_text(
        "from .embeddings import encode_for_row\n" + gated + twolabel,
        encoding="utf-8")
    # Control, in the same tree: two DISTINCT names must both survive, so a
    # guard that simply marked everything `duplicate` would fail here.
    (tmp_path / "tortoise" / "distinct.py").write_text(
        "from .embeddings import encode_for_row\n"
        "def _upsert_point(ev):\n"
        "    vec = encode_for_row('x', None)\n"
        "    return 'MERGE (n:Point {id: 1}) SET n.embedding = $vec'\n"
        "def _upsert_object(ev):\n"
        "    vec = encode_for_row('x', None)\n"
        "    return 'MERGE (o:Object {id: 1}) SET o.embedding = $vec'\n",
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    writers = probe.embedding_writers()
    collided = [
        f"tortoise.{module}::_upsert_point"
        for module in ("gated_then_unresolved", "unresolved_then_gated",
                       "gated_then_twolabel")
    ]
    for key in collided:
        assert key in writers, sorted(writers)
        assert writers[key]["kind"] == "duplicate", (
            f"{key} was not classified as a collision: {writers[key]}")
        assert writers[key]["label"] == probe.DUPLICATE_LABEL, writers[key]
        # ...and bullet 2 reads the SAME classification, instead of the
        # `unresolved producer` sentence the overwritten entry used to carry.
        assert probe.written_labels(writers)[key] == probe.DUPLICATE_LABEL, (
            probe.written_labels(writers)[key])
    assert writers["tortoise.distinct::_upsert_point"]["kind"] == "gated"
    assert writers["tortoise.distinct::_upsert_object"]["kind"] == "gated"
    assert not [k for k, d in writers.items()
                if d["kind"] == "duplicate" and k.startswith("tortoise.distinct")]


def test_a_collided_writer_is_not_REPORTED_as_an_override(tmp_path, monkeypatch):
    """A collision REPLACES the label, so its override must stop being reported.

    `_embedding_writers` adds a key to `overridden` when
    `WRITER_LABEL_OVERRIDES` supplies its label, and `score_path_bullet`
    PRINTS `_overridden_writers() & set(written_labels())` — "labels assigned
    by the DOCUMENTED override, not derived from Cypher". A `duplicate` entry
    keeps its key in `written_labels()`, so an override whose label a later
    collision replaced stayed in that note: the row reported a label it did
    NOT use, in the one place that promises to distinguish assigned labels
    from derived ones.

    `_put()`'s `overridden.discard(key)` is what keeps the two halves agreeing,
    and until now nothing drove it — deleting that line left this file green,
    the same removal-direction hole the round-26 falsifier closed for the
    guard itself (this PR's review, P3).

    ⛔ Both orders are driven: the override's definition first, then the one
    that collides with it, AND the reverse — the `ast.walk` order is what the
    guard must not depend on.
    """
    def point(blackticks_label: str | None) -> str:
        """One `_point`: gated, optionally naming a label in its own Cypher."""
        cypher = (
            "    return vec\n" if blackticks_label is None else
            f"    return 'MERGE (n:{blackticks_label} {{id: 1}})"
            " SET n.embedding = $vec'\n"
        )
        return (
            "def _point(p):\n"
            "    vec = encode_for_row('x', None)\n" + cypher
        )

    roots: dict[str, object] = {}
    for name, defs in (
        # The override fires FIRST, then a labelled `_point` collides with it.
        ("override_then_labelled", (point(None), point("Object"))),
        # ...and the reverse: the labelled one lands, then the override.
        ("labelled_then_override", (point("Object"), point(None))),
        # Control: NO collision, so the override must still be reported.
        ("uncollided", (point(None),)),
    ):
        root = tmp_path / name
        (root / "tortoise").mkdir(parents=True)
        (root / "tortoise" / "embeddings.py").write_text(
            "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
        (root / "tortoise" / "api.py").write_text(
            "from .embeddings import encode_for_row\n" + "\n\n".join(defs),
            encoding="utf-8")
        roots[name] = root

    key = "tortoise.api::_point"
    for name in ("override_then_labelled", "labelled_then_override"):
        monkeypatch.setattr(probe, "REPO_ROOT", roots[name])
        writers = probe.embedding_writers()
        assert writers[key]["kind"] == "duplicate", (name, writers[key])
        assert writers[key]["label"] == probe.DUPLICATE_LABEL, (name, writers[key])
        # The key IS in `written_labels()` (a `duplicate` maps onto the sentinel),
        # which is exactly why the override half has to be withdrawn.
        assert probe.written_labels(writers)[key] == probe.DUPLICATE_LABEL, name
        assert key not in probe._overridden_writers(), (
            f"{name}: the collision replaced the label, so reporting "
            f"`{key}` as a DOCUMENTED override claims a label the row did not "
            f"use: {sorted(probe._overridden_writers())}"
        )
        # ...which is the exact expression `score_path_bullet` prints from.
        assert probe._overridden_writers() & set(
            probe.written_labels(writers)) == set(), name

    # Control, so a guard that simply never recorded an override cannot pass:
    # with no collision, the documented override is still reported.
    monkeypatch.setattr(probe, "REPO_ROOT", roots["uncollided"])
    writers = probe.embedding_writers()
    assert writers[key]["kind"] == "gated", writers[key]
    assert writers[key]["label"] == "Point", writers[key]
    assert probe._overridden_writers() == {key}, probe._overridden_writers()


def test_a_duplicate_writer_identity_holds_bullet_two_at_partial(monkeypatch):
    """Bullet 2 must not read `MET` over a writer whose label it lost.

    A `duplicate` entry carries no label, so including it as `UNKNOWN` is the
    only honest option — excluding it made the two rows disagree about the same
    hole: bullet 1 held `PARTIAL` on `duplicates` while bullet 2 said `MET`
    (review round 5, P2).
    """
    writers = {
        "tortoise.x::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/x.py:1",
            "kind": "gated", "label": "Point",
        },
        "tortoise.y::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/y.py:2",
            "kind": "duplicate", "label": "UNKNOWN(duplicate writer name)",
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    row = probe.score_path_bullet()   # every label IS served here
    assert row.verdict == "PARTIAL", row.observed
    assert any("duplicate writer identities" in n for n in row.notes), row.notes
    assert probe.written_labels()["tortoise.y::_upsert_point"].startswith("UNKNOWN")


def _pre_312_interpreter() -> str | None:
    """A real `python3` older than the floor, or None (as the guard suite does)."""
    for candidate in ("/usr/bin/python3", shutil.which("python3")):
        if not candidate or not Path(candidate).exists():
            continue
        run = subprocess.run(
            [candidate, "-c",
             "import sys; sys.exit(0 if sys.version_info < (3, 12) else 1)"],
            capture_output=True, text=True,
        )
        if run.returncode == 0:
            return candidate
    return None


def test_the_tool_parses_under_the_floor():
    """`#5128`'s guard is only worth having if the file can REACH it.

    `tests/test_entry_point_python_guard.py` requires the refusal to be the
    first statement, which is worthless for a file the older interpreter cannot
    tokenize: nested same-quote f-strings are PEP 701 **syntax** (3.12), so the
    module dies at `f"…{d['at']}…"` before the guard runs. This pins the
    remedy, not the guard.

    Declared bound: a box with no pre-3.12 interpreter SKIPS visibly rather
    than passing.
    """
    old = _pre_312_interpreter()
    if old is None:
        pytest.skip("no <3.12 interpreter on this box: cannot re-derive the parse")
    run = subprocess.run(
        [old, "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())",
         str(Path(probe.__file__))],
        capture_output=True, text=True,
    )
    assert run.returncode == 0, (
        f"{old} cannot parse {probe.__file__}, so its `#5128` guard never runs "
        f"and the refusal is documentary (use different quote characters):\n"
        f"{run.stderr}"
    )


def _census_violations(unresolved, census, classes, bypass_cls) -> list[str]:
    """Every way the `#10541` census can fail over ``unresolved``, as data.

    A helper so the property is provable on an INJECTED set as well as on the
    live tree — a guard whose only input is the head either goes vacuous or
    pins the head, and this file refuses both.
    """
    out: list[str] = []
    unclassified = [w for w in unresolved if w not in census]
    if unclassified:
        out.append(f"unclassified by the #10541 census: {unclassified}")
    bad = {w: census[w][0] for w in unresolved
           if w in census and census[w][0] not in classes}
    if bad:
        # The class string comes from the tool's OWN constant, never a local
        # mirror: a hardcoded "bypass" here would stop firing the day the
        # constant is renamed, and nothing else guards it (measured: this
        # module refuses the same mirror-duplication elsewhere).
        out.append(f"census class outside {classes}: {bad}")
    bypasses = [w for w in unresolved
                if w in census and census[w][0] == bypass_cls]
    if bypasses:
        out.append("a REAL bypass must be fixed or filed, not parked in the "
                   f"census: {bypasses}")
    # The STALE direction, which `unclassified` above cannot see: a census entry
    # whose writer is no longer unresolved. A census keyed by writer NAME is
    # never re-checked against the finding it was recorded for, so an entry can
    # outlive its hole — the `unused suppression` case Ruff (RUF100) and
    # ESLint's `reportUnusedDisableDirectives` report explicitly, and the one
    # Semgrep keeps as an `Ignored` triage state. Left unchecked it inflates the
    # `exempt` count a reader takes as "these are the instrument's false
    # positives". EQUALITY on purpose: the missing direction alone is a guard
    # that is satisfied by never deleting an entry, and the stale direction
    # alone is satisfied by never adding one.
    live = set(unresolved)
    stale = sorted(w for w in census if w not in live)
    if stale:
        out.append(
            "census entries with no unresolved writer behind them — either the "
            "hole was fixed (re-measure before deleting), the writer was "
            "re-kinded and is STILL a hole (became `duplicate`, or "
            "`gated`/`ungated` with an unresolved label), or the key is "
            f"stale/typo'd: {stale}")
    return out


def test_every_unresolved_writer_carries_a_census_class() -> None:
    """#10541 — every unresolved writer THIS TREE holds is classed.

    The instrument is deliberately over-inclusive: a three-name callee
    vocabulary cannot tell a legitimate exemption from a bypass, so bullet 2
    used to be held at PARTIAL over every function it could not resolve. #10541
    resolves each into one of three honest classes, so this falsifier goes RED
    when a writer is unresolved and NOTHING says which class it is in — the
    measurement defect the issue exists to clear.

    Pre-fix this is RED: `WRITER_CENSUS` does not exist, every one of the
    unresolved writers is unclassified, and `unclassified` is the whole set.

    ⛔ `assert unresolved` is deliberately ABSENT. Resolving those writers IS
    `#5090`'s remainder, so requiring the tree to KEEP having unresolved
    writers reds on a CORRECT change — and its cheapest-looking repair is to
    delete the assertion, which disarms the guard exactly when the epic
    completes (review at `adde2763f`, blocking). The shape this file already
    refuses at `test_every_embedding_write_path_reaches_the_gate_seam`.

    ⛔ NOR does an empty live set ALONE make the tree green. `_census_violations`
    reports the STALE direction too (`no unresolved writer behind them`), so
    the census entry must be trimmed in the same change that resolves its
    writer; until it is, this test is RED over every un-trimmed entry — the
    `unused suppression` contract Ruff's RUF100 and ESLint's unused-suppression
    reporting prescribe, reached by `#5090`'s remainder. Read that failure
    message before touching anything: it names the three reasons an entry can
    look stale and only ONE of them is a delete.

    Once the entries ARE trimmed the live set and the census are both empty and
    this test is GREEN — that is the FINISHED state, not a disarmed guard, and
    it is the state `#5090`'s remainder is aiming at. Nor is the guard vacuous
    while the set is empty: the injected-map test carries both directions on an
    injected census, and `test_an_untrimmed_census_over_a_resolved_set_is_red`
    executes the trim contract this paragraph states.
    """
    census = getattr(probe, "WRITER_CENSUS", {})
    classes = getattr(probe, "CENSUS_CLASSES", ())
    unresolved = sorted(
        w for w, d in probe.embedding_writers().items()
        if d["kind"] == "unresolved_write"
    )
    violations = _census_violations(
        unresolved, census, classes, probe.CENSUS_BYPASS)
    assert not violations, "\n".join(violations)


def test_a_prose_MENTION_of_an_embedding_write_is_classed_exempt_not_a_hole() -> None:
    """`main`'s `#8488` fold MENTIONS `embedding=` in a COMMENT — not a write.

    The needle (`_EMBED_WRITE`) is applied to a function's SOURCE TEXT, and a
    comment is source text, so this census necessarily carries a class for the
    false positives the needle produces — `exempt` names that case itself
    (*"merely MENTIONS one in a comment/docstring (the instrument's own false
    positives)"*). `#8488`/`#10523` landed on `main` AFTER this census was read,
    and it documented the `embeddingText` clear with the prose
    `#   2. a CALLER-VECTOR write (``embedding=[…]`` / ``embedding=None``)` — which
    the needle matched inside `tortoise.projection::_apply_one`, a fold that
    writes no vector, and which the census therefore had no class for. Because
    the live guard is an EXACT set match, that reddened it on the `origin/main`
    integration (measured: `unclassified by the #10541 census:
    ['tortoise.projection::_apply_one']`).

    ⛔ The repair is `exempt`, NOT `recognised_producer`, and NOT a change to
    the needle: `_EMBED_WRITE` is deliberately blind — `#4094`'s `SET n += $p`
    map-merge is invisible to it, and the `_update_entity` entry depends on
    exactly that over-detection to keep a REAL write path in the map. Dropping
    comment text from the scan would silence this false positive AND silently
    lose `_update_entity`, which is the fail-open direction. So the match is
    READ and classified, which is what this census is for.

    Both directions, so the entry cannot rot into a lie: the class must be
    `exempt`, and every needle match in that function must sit inside a COMMENT
    — a fold that grows a real vector write reds HERE and has to be re-read.
    """
    import ast
    import io
    import textwrap
    import tokenize

    key = "tortoise.projection::_apply_one"
    assert probe._census_class(key) == probe.CENSUS_EXEMPT, (
        f"{key} is an instrument false positive (a comment quoting "
        f"`embedding=…`); classing it as anything but {probe.CENSUS_EXEMPT!r} "
        "claims a stored vector this fold never writes")

    src = (probe.REPO_ROOT / "tortoise/projection/__init__.py").read_text(
        encoding="utf-8")
    lines = src.splitlines()
    bodies = [
        "\n".join(lines[n.lineno - 1:n.end_lineno])
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name == "_apply_one"
    ]
    assert bodies, (
        "the fold this census entry names is gone from "
        "tortoise/projection/__init__.py — re-measure the entry")

    def _comment_blind(text: str) -> str:
        """`text` with every COMMENT token BLANKED — strings and layout kept.

        Blanked, not deleted: the offsets stay put, and the string literals
        (where the Cypher lives, so where a real write would live) survive, so
        this cannot pass by removing the evidence it is checking.
        """
        dedented = textwrap.dedent(text)
        rows = dedented.splitlines(keepends=True)
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(dedented).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return dedented
        for tok in toks:
            if tok.type != tokenize.COMMENT:
                continue
            row, col = tok.start
            end_col = tok.end[1]
            line = rows[row - 1]
            rows[row - 1] = line[:col] + " " * (end_col - col) + line[end_col:]
        return "".join(rows)

    matching = [b for b in bodies if probe._EMBED_WRITE.search(b)]
    assert matching, (
        "the needle no longer matches this fold at all — the census entry is "
        "STALE and must be trimmed in the same change, not carried")
    for body in matching:
        assert not probe._EMBED_WRITE.search(_comment_blind(body)), (
            "`_apply_one` now matches the embedding-write needle in CODE, not "
            "prose — re-read the census entry: `exempt` no longer describes it")


def test_an_untrimmed_census_over_a_resolved_set_is_red() -> None:
    """Resolving every writer does NOT license a stale census entry.

    `#5090`'s remainder reaches a tree with NO unresolved writers, and the trim
    contract the sibling docstring states is measured here rather than
    restated: an entry whose writer is no longer a hole makes
    `_census_violations` report `no unresolved writer behind them`.

    ⛔ The census is INJECTED and the unresolved set is empty ON PURPOSE. An
    earlier version of this test fed the LIVE `WRITER_CENSUS` into the same
    assertion with an empty live set, which made the pair UNSATISFIABLE at the
    very state it named: with the entries trimmed as the sibling docstring
    demands, `_census_violations([], {}, …)` is `[]` and `assert stale` failed
    on a CORRECT tree — while leaving the entries in place reddened the live
    falsifier instead. No tree satisfied both tests (review on this PR, P2).
    The contract is about an UNTRIMMED census, not about today's literal, so an
    injected map keeps it executable after the real census is emptied — the
    same shape `test_the_census_guard_has_teeth_on_an_INJECTED_map` uses.

    ⛔ What is measured here is the HELPER's contract, never another test's
    PROSE. A previous version also asserted that the live falsifier's docstring
    did (or did not) contain three given phrases; that was deleted (review on
    this PR, P3) because a fragment like `is a GREEN tree` is a TRUE sentence in
    this file's own voice — `a trimmed census over an empty live set is a GREEN
    tree` is exactly right — so the pin would red the lane that finishes
    `#5090` on a CORRECT prose edit, and it is vacuous under `-OO`, where
    `__doc__` is `None`. Do not re-add it: the two assertions below are the
    contract, and `_census_violations` is what a prose claim is checked against.
    """
    resolved = "tortoise.q::_resolved_since"
    also_resolved = "tortoise.q::_also_resolved"
    census = {
        resolved: (probe.CENSUS_RECOGNISED, "injected for this test"),
        also_resolved: (probe.CENSUS_EXEMPT, "injected for this test"),
    }
    violations = _census_violations(
        [], census, probe.CENSUS_CLASSES, probe.CENSUS_BYPASS)
    stale = [v for v in violations
             if "no unresolved writer behind them" in v]
    assert stale, (
        "an untrimmed census over a resolved set must NOT read as green: the "
        f"census outlives its holes, and that is where the trim is demanded — "
        f"{violations}")
    unnamed = [w for w in census if w not in stale[0]]
    assert not unnamed, (
        "the stale violation names a subset only, so the repair it demands "
        f"is not derivable from it: {unnamed}")
    # The TRIMMED census over the same resolved set is the finished state and it
    # is GREEN. Without this the assertion above would be satisfied by a helper
    # that redded unconditionally — the "pin the head" failure this file
    # refuses, and the one that made the previous version unsatisfiable.
    assert _census_violations(
        [], {}, probe.CENSUS_CLASSES, probe.CENSUS_BYPASS) == [], (
        "a trimmed census over a resolved set is the FINISHED state and must "
        "be green, or the trim contract can never be satisfied")


def test_every_census_citation_re_derives_at_this_head() -> None:
    """`WRITER_CENSUS` promises *"READ at their seam"* — so re-derive it.

    The first version cited `file:line`. `sdk.py` then grew ~43 lines on `main`
    and every `tortoise.sdk::*` citation pointed at unrelated code, silently:
    `_census_class` reads `entry[0]` only, so nothing ever executed them while
    `#5090`/`#10541` quote this census as their justification (round 17, P3 —
    the same defect class the whole PR exists to remove, in the evidence it
    ships). The citations are `(path, symbol, needle)` now and this falsifier
    resolves each one, so an ordinary edit cannot rot them and a seam that
    leaves the named function still reds.
    """
    violations = probe.census_evidence_violations()
    assert not violations, "\n".join(violations)


def test_the_citation_check_has_teeth_on_an_INJECTED_drift() -> None:
    """The citation guard's RED directions, on injected evidence.

    Durable on purpose: it does not depend on today's tree still holding a
    drifted citation — an entry with no evidence, evidence for no entry, a
    symbol that file does not define, and a needle that is not inside that
    symbol must each be reported, or the check greens a census nobody
    re-derived. Every case is self-contained (`overrides={}`), so it measures
    the checker and not the live tree.
    """
    tree = Path(probe.__file__).resolve().parent.parent
    clean = probe.WRITER_CENSUS["tortoise.vector_write::set_embedding_clause"]
    key = "tortoise.q::_classed"

    def violations(census, evidence):
        return probe.census_evidence_violations(
            census=census, evidence=evidence, root=tree, overrides={})

    orphan = {f"{key}_orphan": (("tortoise/api.py", "_point", "x"),)}
    assert violations({key: clean}, orphan) == [
        f"{key}: classed with no re-derivable evidence",
        f"{key}_orphan: evidence for an entry that does not exist",
    ]
    # A symbol the cited file does not define.
    assert "which that file does not define" in str(violations(
        {key: clean}, {key: (("tortoise/api.py", "_pointX", "x"),)}))
    # A real symbol whose needle has moved out of it.
    assert "does not carry" in str(violations(
        {key: clean}, {key: (("tortoise/api.py", "_point", "NOPE"),)}))
    # A cited file that is gone.
    assert "cannot be read" in str(violations(
        {key: clean}, {key: (("tortoise/nope.py", "_point", "x"),)}))
    # The GREEN direction: the real citation for this entry resolves.
    assert violations({key: clean}, {
        key: probe.CENSUS_EVIDENCE["tortoise.vector_write::set_embedding_clause"],
    }) == []


def test_a_MATCH_label_is_not_credited_as_the_writers_label(
        tmp_path, monkeypatch):
    """A `MATCH` is a READ; the map must credit the label the WRITE names.

    The comment said `MERGE`/`CREATE` while the pattern also took `MATCH`
    (round 17, P3). The direction that fails is the bad one: on the module-stem
    fallback a read's label is the one path that can RESOLVE a label the writer
    never writes, and bullet 2 then reports it as covered.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    (tmp_path / "tortoise" / "alpha.py").write_text(
        "from .embeddings import encode_for_row\n"
        "def _upsert_document(x):\n"
        "    vec = encode_for_row('x', None)\n"
        "    return 'MERGE (d:Document {id: $id})'\n"
        "def _upsert_point(x):\n"
        "    vec = encode_for_row('x', None)\n"
        "    return 'MATCH (n:Point {id: $id}) RETURN n'\n",
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    writers = probe.embedding_writers()
    assert writers["tortoise.alpha::_upsert_document"]["label"] == "Document", writers
    assert writers["tortoise.alpha::_upsert_point"]["label"] == "UNKNOWN", (
        "a MATCH's label was credited as the writer's stored label: "
        f"{writers['tortoise.alpha::_upsert_point']}"
    )


def test_the_census_guard_has_teeth_on_an_INJECTED_map() -> None:
    """The `#10541` guard's RED and GREEN directions, both on an injected set.

    Durable on purpose: green for one writer carrying a real class, red for one
    that carries none, red for a class outside the vocabulary, red for a
    REAL bypass, and red for a STALE entry — measured without depending on
    whether this head still has unresolved writers (the `assert unresolved`
    trap at `adde2763f`).
    """
    classes = probe.CENSUS_CLASSES
    bypass_cls = probe.CENSUS_BYPASS
    classed = "tortoise.q::_classed"
    unclassed = "tortoise.q::_unclassed"
    good = {classed: (probe.CENSUS_RECOGNISED, "injected for this test")}
    assert _census_violations([classed], good, classes, bypass_cls) == []
    assert "unclassified by the #10541 census" in str(_census_violations(
        [classed, unclassed], good, classes, bypass_cls))
    assert "census class outside" in str(_census_violations(
        [classed], {classed: ("not-a-class", "injected")}, classes,
        bypass_cls))
    assert "REAL bypass" in str(_census_violations(
        [classed], {classed: (bypass_cls, "injected")}, classes, bypass_cls))
    # The STALE direction: a census entry with no unresolved writer behind it.
    assert "no unresolved writer behind them" in str(_census_violations(
        [classed], {classed: (probe.CENSUS_RECOGNISED, "injected"),
                    "tortoise.q::_resolved_since": (probe.CENSUS_EXEMPT,
                                                    "injected")},
        classes, bypass_cls))


def test_a_duplicate_identity_paints_no_census_note(monkeypatch):
    """A `duplicate` is NOT a `#10541` population member, and says nothing here.

    `unknown` (the row's UNKNOWN-label set) also carries the duplicate
    identities, so a census driven off it printed a census over a population of
    ZERO — and then reported the collision as "`#10541`'s own defect", which a
    duplicate can never be: it has no key it could hold in `WRITER_CENSUS`.
    That line is quoted into `#5090`'s body as evidence (review at `adde2763f`).
    """
    writers = {
        "tortoise.x::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/x.py:1",
            "kind": "gated", "label": "Point",
        },
        "tortoise.y::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/y.py:2",
            "kind": "duplicate", "label": "UNKNOWN(duplicate writer name)",
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    row = probe.score_path_bullet()
    assert not [n for n in row.notes if "#10541 census" in n], row.notes
    assert not [n for n in row.notes if "UNCLASSIFIED" in n], row.notes
    # ...and the duplicate itself is still REPORTED, by its own note.
    assert any("duplicate writer identities" in n for n in row.notes), row.notes


def test_the_census_note_names_only_the_unresolved_producers(monkeypatch):
    """The census counts the `unresolved_write` set — never a duplicate."""
    writers = {
        "tortoise.x::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/x.py:1",
            "kind": "gated", "label": "Point",
        },
        "tortoise.y::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/y.py:2",
            "kind": "duplicate", "label": "UNKNOWN(duplicate writer name)",
        },
        "tortoise.z::_store_without_a_producer": {
            "callee": "unrecognised", "at": "tortoise/z.py:3",
            "kind": "unresolved_write",
            "label": "UNKNOWN(unresolved producer)",
        },
    }
    census = dict(probe.WRITER_CENSUS)
    census["tortoise.z::_store_without_a_producer"] = (
        probe.CENSUS_EXEMPT, "injected for this test")
    monkeypatch.setattr(probe, "WRITER_CENSUS", census)
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    row = probe.score_path_bullet()
    notes = [n for n in row.notes if n.startswith("#10541 census")]
    assert notes, row.notes
    assert "tortoise.z::_store_without_a_producer" in notes[0], notes[0]
    assert "tortoise.y::_upsert_point" not in notes[0], (
        f"a duplicate identity was counted as a #10541 population member: {notes[0]}")
    assert "exempt=1" in notes[0], notes[0]
    assert not [n for n in row.notes if "UNCLASSIFIED" in n], row.notes


def _cell_count(line: str) -> int:
    """Pipes that SEPARATE cells in a GFM row: an escaped `\\|` is literal text.

    `line.count("|")` counts the escape too, so a guard built on it either
    passes an unescaped pipe's neighbour or — once the emitter escapes — reds a
    correct row. Both directions were wrong, so the count is on the separator.
    """
    return line.replace("\\|", "").count("|")


def test_the_census_note_does_not_break_the_markdown_table(monkeypatch):
    """`_table` emits `Row.observed` as ONE cell, so a note must not split it.

    GFM: an unescaped `|` inside a table cell is a cell separator. The row that
    carries the most evidence rendered with three spurious columns on the one
    artifact this file calls "the one place a reader actually looks" (review at
    `adde2763f`). This derives the cell count from the header instead of
    pinning pipes per line, so any future note is covered too.
    """
    writers = {
        "tortoise.z::_store_without_a_producer": {
            "callee": "unrecognised", "at": "tortoise/z.py:3",
            "kind": "unresolved_write",
            "label": "UNKNOWN(unresolved producer)",
        },
    }
    census = dict(probe.WRITER_CENSUS)
    census["tortoise.z::_store_without_a_producer"] = (
        probe.CENSUS_EXEMPT, "injected for this test")
    monkeypatch.setattr(probe, "WRITER_CENSUS", census)
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    table = probe._table([probe.score_path_bullet()])
    lines = [ln for ln in table.splitlines() if ln.startswith("|")]
    widths = {_cell_count(ln) for ln in lines}
    assert len(widths) == 1, (
        f"the table's row has a different cell count than its header: {widths} — "
        f"a note split a cell: {lines}")


def test_the_table_escapes_a_pipe_in_every_row_not_only_row_two():
    """The docstring above says "any future note is covered"; this is that claim.

    It was not: the guard built exactly ONE row (`score_path_bullet`), while
    three of the four rows carry text the tool does not author — row 1's
    `shipped["files"]` config lines and row 4's `open:` / `restates the DoD
    bullet` lines read out of `V1_SURFACES`, one of which,
    `docs/architecture/STORAGE-ARCHITECTURE.md`, keeps its V1 discussion in
    markdown TABLE rows (pipe-bearing by construction). Measured at `d0f31cfa4`:
    no live `open:` line carries a `|`, so this is a latent hole and an
    over-claiming guard, not a current malformation — which is exactly why the
    falsifier drives the EMITTER with all four bullet numbers instead of
    trusting today's note text.
    """
    rows = [
        probe.Row(bullet=n, text=f"a bullet | with a pipe {n}", verdict="MET",
                  command=f"uv run python tools/dod_5090_probe.py --row {n}",
                  observed=f"a note | with a pipe {n}")
        for n in (1, 2, 3, 4)
    ]
    table = probe._table(rows)
    lines = [ln for ln in table.splitlines() if ln.startswith("|")]
    widths = {_cell_count(ln) for ln in lines}
    assert len(widths) == 1, (
        f"a pipe in a row's text or note split a cell: {widths} — {lines}")
    # Escaped, not silently DROPPED: the pipe survives as text, so the rendered
    # cell still reads what the note says.
    assert all("\\|" in ln for ln in lines[2:]), lines


@pytest.mark.parametrize("span", ["{p}", "`{p}`", "**{p}**", "_{p}_", "[{p}](u)"])
def test_the_table_escape_holds_inside_every_inline_span_a_note_carries(span):
    """Escaping at the emitter is only correct because the CELL is the exception.

    The general GFM rule is that a backslash escape does NOT work in a code
    span, an autolink or raw HTML — so "escape every pipe" is a bug outside a
    table. GFM §4.10 (*Tables*) carves the cell out explicitly: *"Include a pipe
    in a cell's content by escaping it, including inside other inline spans"*,
    and its own examples escape a pipe in prose, in a code span and in
    `<strong>`. That is what makes `_cell` sound rather than lucky, and it is
    the half that a future “fix” skipping spans would silently trade away.

    The shapes are not hypothetical: `Row.observed` carries row 1's
    `path: <config line>` (prose, and a config line can carry a `|` — a
    compose map or a shell pipe), row 2's backticked counts, row 3's
    ``mechanism: value`` pairs, and row 4's `open: docs/...:1234: | **V1** |`
    — a markdown table row read out of a `docs/` document.
    """
    note = span.format(p="a|b")
    cell = probe._cell(note)
    assert cell.count("|") == note.count("|"), cell  # a pipe is CARRIED, never dropped
    assert "\\|" in cell, f"the pipe inside {span!r} was left unescaped: {cell}"
    row = probe.Row(bullet=1, text="a bullet", verdict="PARTIAL",
                    command="c", observed=note)
    lines = [ln for ln in probe._table([row]).splitlines() if ln.startswith("|")]
    assert len({_cell_count(ln) for ln in lines}) == 1, (
        f"a pipe inside {span!r} split the cell: {lines}")


def test_an_out_of_vocabulary_census_class_is_unclassified_not_invisible(
        monkeypatch):
    """A class outside `CENSUS_CLASSES` must read as UNCLASSIFIED, not vanish.

    `by_class` is gated on `cls in CENSUS_CLASSES` and `unclassified` was gated
    on `_census_class(fn) is None`, so an entry whose class string is a typo (or
    a retired class) matched NEITHER: the writer was named nowhere and every
    class total read `0` over a population of one — the instrument printing
    totals that account for nothing it was asked about.

    `test_the_census_guard_has_teeth_on_an_INJECTED_map` already catches the
    typo in CI (it asserts a class outside the vocabulary is a violation), but
    that guard reads the DICT. The row the reader actually sees said `0` of
    everything, and this diff's equality guard makes editing census entries a
    routine act — so the instrument must not lie while CI is the only one
    objecting.

    Two properties, because naming the writer is not enough: the writer must be
    NAMED on the UNCLASSIFIED line, and the census line's segments must still
    RECONCILE to the `N of M` in its own header. Without the second, the line
    read `1 of 1 … recognised_producer=0 · exempt=0 · bypass=0` — a header
    claiming one writer and totals summing to zero, i.e. the same
    silently-omitted-writer shape the note was re-worded to remove.
    """
    writers = {
        "tortoise.consistency::_compare_views": {
            "callee": probe.SEAM_CALL, "at": "tortoise/consistency.py:1",
            "kind": "unresolved_write",
            "label": "UNKNOWN(unresolved producer)",
        },
    }
    # A class string that is NOT in CENSUS_CLASSES — the typo/retired shape.
    assert "exmpt" not in probe.CENSUS_CLASSES
    monkeypatch.setitem(
        probe.WRITER_CENSUS, "tortoise.consistency::_compare_views",
        ("exmpt", "MUTATION: an out-of-vocabulary class"))
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    row = probe.score_path_bullet()
    unclassified = [n for n in row.notes if "UNCLASSIFIED" in n]
    assert unclassified, (
        "a census entry whose class is outside CENSUS_CLASSES disappeared from "
        f"the instrument's output entirely: {row.notes}")
    assert "tortoise.consistency::_compare_views" in unclassified[0], unclassified
    # ...and the census line still ACCOUNTS for it: the segments reconcile to
    # the `1 of 1` in the header instead of summing to zero.
    census = [n for n in row.notes if n.startswith("#10541 census")]
    assert len(census) == 1, row.notes
    assert "unclassified=1 (tortoise.consistency::_compare_views)" in census[0], (
        f"the census segments do not reconcile to its own header: {census[0]}")
    assert "1 of 1 UNKNOWN-labelled" in census[0], census[0]


def test_the_census_note_names_the_population_it_counted(monkeypatch):
    """The census total must not claim a population wider than it counted.

    The note said "over the unresolved set", but the counted population is
    `kind == "unresolved_write"` while the note four lines above ("unresolved
    writers") prints `unknown` — which also carries duplicates. With one of
    each present a reader saw TWO listed writers and totals summing to ONE,
    while the note asserted coverage of both. Naming the population, and
    stating `N of M`, makes any future under-coverage visible instead of
    plausible.

    ⛔ The noun matters too. An earlier wording was "the unresolved PRODUCERS",
    which the same line falsifies: `exempt` is defined as "the function is NOT a
    producer of a stored vector", so `... PRODUCERS ... exempt=8` says "8 of
    these producers are not producers" in output quoted into `#5090`'s body.
    The note now names the LABEL the population is keyed on, which no class on
    the line can contradict.
    """
    writers = {
        "tortoise.consistency::_compare_views": {
            "callee": probe.SEAM_CALL, "at": "tortoise/consistency.py:1",
            "kind": "unresolved_write",
            "label": "UNKNOWN(unresolved producer)",
        },
        "tortoise.y::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/y.py:2",
            "kind": "duplicate", "label": "UNKNOWN(duplicate writer name)",
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    row = probe.score_path_bullet()
    census = [n for n in row.notes if n.startswith("#10541 census")]
    assert len(census) == 1, row.notes
    # It states how many of the UNKNOWN-labelled writers it actually counted.
    assert "1 of 2 UNKNOWN-labelled" in census[0], census[0]
    # ...and names the population by the LABEL, not by a class it cannot hold.
    assert "writers labelled UNKNOWN(unresolved producer)" in census[0], census[0]
    assert "PRODUCERS" not in census[0], (
        "the note claims a population the same line contradicts: `exempt` is "
        f"defined as NOT being a producer of a stored vector: {census[0]}")


# ── The two verdicts that had no falsifier (fresh-context review, P2-1) ──────
#
# `grep -c score_v1_bullet` in this file was 0 and row 1's mapping was asserted
# only as "the map SEES the ungated writer", never as "the row HOLDS". The
# measured mutation — `score_v1_bullet`'s `verdict` forced to `"MET"` — left the
# suite green at 65 passed / 1 skipped, i.e. an instrument that publishes
# "V1 is decided and implemented" (fabricating the owner ruling the epic stays
# open for) could ship green. These two tests are that hole closed.


def test_row_four_is_NOT_MET_while_no_decided_marker_exists(tmp_path, monkeypatch):
    """Bullet 4's verdict, asserted rather than membership-checked.

    Two surfaces, zero V1-decided markers, one marker that still says the
    decision is OPEN — the epic's own state. `PARTIAL` or `MET` here would
    publish a ruling nobody made, so this reds on the exact mutation the
    reviewer showed surviving.
    """
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "projection").mkdir()
    for rel in probe.V1_SURFACES:
        (tmp_path / rel).write_text(
            "the VECTOR index on `Point.embedding` ONLY — see the owner's "
            "OPEN decision V1 below\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    row = probe.score_v1_bullet()
    assert row.verdict == "NOT MET", (
        f"0 decided markers must not publish a V1 ruling: {row.verdict!r} "
        f"({row.observed})")


def test_row_four_never_reaches_MET_even_with_a_decided_marker(tmp_path, monkeypatch):
    """A decision RECORD cannot prove what shipped — so PARTIAL is the ceiling.

    Without this, "add one decided marker and it goes MET" reads as progress on
    the one bullet the epic is open for, when the implementation half is
    precisely what a record cannot measure.
    """
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "projection").mkdir()
    for rel in probe.V1_SURFACES:
        (tmp_path / rel).write_text(
            "V1 decided: the vector index carries embeddings (see the ruling)\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    row = probe.score_v1_bullet()
    assert row.verdict == "PARTIAL", row.verdict
    assert "decided markers found: 2" in row.observed, row.observed


def test_a_surface_that_quotes_the_dod_bullet_does_not_publish_a_ruling(
        tmp_path, monkeypatch):
    """Quoting the bullet is NOT a record — row 4 is the epic's owner gate.

    One marker (`v1 is decided`) is a substring of the requirement being checked
    (`BULLETS[3]` = "V1 is decided and implemented."), so a doc that **restates
    the bullet** — the most likely edit to `V1_SURFACES`, and the style this PR
    uses everywhere — registered as a ruling and flipped row 4 from `NOT MET` to
    `PARTIAL`: publishing "a V1 ruling exists, implementation unproven" when the
    owner has not ruled. The code comment at the marker anticipated only the
    harmless direction; this one fabricates a decision (review, P3).

    Four quotation SHAPES, because each defeats a different naive test: the
    bullet verbatim, an emphasised/checklisted copy, a backticked copy, and a
    WRAPPED copy whose first line is a PREFIX of the bullet (a same-line
    substring test cannot see that one).
    """
    shapes = [
        probe.BULLETS[3],
        f"- [x] **{probe.BULLETS[3]}**",
        f"`{probe.BULLETS[3].rstrip('.')}` (the epic's fourth bullet)",
        "V1 is decided and\n   implemented.",
    ]
    for shape in shapes:
        for rel in probe.V1_SURFACES:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# DoD\n{shape}\n", encoding="utf-8")
        monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
        row = probe.score_v1_bullet()
        assert row.verdict == "NOT MET", (
            f"a doc QUOTING the bullet published a V1 ruling: {row.verdict!r} "
            f"for shape {shape!r} ({row.observed})")
        assert "decided markers found: 0" in row.observed, row.observed
        # Reported, not silently dropped — the read says what it dismissed.
        assert "restates the DoD bullet" in row.observed, row.observed


def _fake_rule_tree(tmp_path) -> None:
    """A tree where the rule is stated AND wired, so only the row's own
    enforcement clauses decide the verdict."""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "ENV_FLAG = 'TORTOISE_EMBED_GATE'\n"
        "def embedding_verdict(t):\n    return None\n",
        encoding="utf-8",
    )
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "import tortoise.embed_gate as embed_gate\n"
        "def encode_for_row(content):\n"
        "    verdict = embed_gate.embedding_verdict(content)\n"
        "    return verdict\n",
        encoding="utf-8",
    )


def _fake_shipped(on: bool) -> dict:
    return {"on": on, "surfaces": ["Dockerfile"], "searched": ["Dockerfile"],
            "unreadable": [], "mentions": [], "files": ["Dockerfile"] if on else []}


def _gated_writer(name: str) -> dict:
    return {name: {"callee": "embed_text", "at": f"{name.split('::')[0].replace('.', '/')}.py:1",
                   "kind": "gated", "label": "Point"}}


def test_row_one_is_PARTIAL_when_the_gate_is_off_although_every_writer_is_gated(
        tmp_path, monkeypatch):
    """The flag is the operator's; a fully-gated population with the gate OFF is
    still not the write path's operative rule. `MET` here would publish
    enforcement the shipped config does not set."""
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "embedding_writers", lambda: _gated_writer("tortoise.a::_w"))
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(False))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", row.verdict
    assert "gate enabled by default: False" in row.observed, row.observed


def test_row_one_reaches_MET_only_when_every_clause_holds(tmp_path, monkeypatch):
    """The positive pole: without it, a probe that can only print `PARTIAL` is
    machinery, not measurement — the same objection, mirrored."""
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "embedding_writers", lambda: _gated_writer("tortoise.a::_w"))
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "MET", f"{row.verdict}: {row.observed}"


def test_row_one_is_NOT_MET_when_no_writer_reaches_the_gated_seam(
        tmp_path, monkeypatch):
    """A stated rule with no gated writer is a comment, not enforcement."""
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "embedding_writers", lambda: {})
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "NOT MET", f"{row.verdict}: {row.observed}"


def _writer(name: str, kind: str) -> dict:
    return {"callee": "embed_text", "at": f"{name.replace('::', '.py:')}", "kind": kind, "label": "Point"}


def test_row_one_is_PARTIAL_because_ONE_writer_embeds_outside_the_gate(
        tmp_path, monkeypatch):
    """The epic body's load-bearing sentence: *"Either fact alone holds this row
    at `PARTIAL`"*. Re-review (P2) measured that dropping `ungated or` from the
    verdict left the suite GREEN (70 passed, 1 skipped) — so the clause the epic
    cites as the reason for row 1's PARTIAL had no falsifier at all. A gate that
    is ON and a population that is fully gated must still not publish `MET`
    while one writer embeds outside it: that writer is a path the rule never
    sees, and turning the flag on does not fix it.
    """
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "embedding_writers", lambda: {
        "tortoise.a::_gated": _writer("tortoise.a::_gated", "gated"),
        "tortoise.session_indexer::main": _writer(
            "tortoise.session_indexer::main", "ungated"),
    })
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", (
        f"gate ON + one ungated writer must not publish MET: {row.verdict!r}")
    assert "embed OUTSIDE the gate: 1" in row.observed, row.observed


def test_row_one_is_PARTIAL_because_ONE_writer_has_no_resolved_producer(
        tmp_path, monkeypatch):
    """The same argument for the other clause the epic names: an unresolved
    writer's label cannot be resolved, so the row cannot claim a complete
    measurement. Dropping `unresolved_writes or` measured GREEN (70 passed)
    before this test existed."""
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "embedding_writers", lambda: {
        "tortoise.a::_gated": _writer("tortoise.a::_gated", "gated"),
        "tortoise.z::_store": _writer("tortoise.z::_store", "unresolved_write"),
    })
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", (
        f"an unresolved writer must hold the row at PARTIAL: {row.verdict!r}")


def test_row_one_is_NOT_MET_when_the_rule_is_not_STATED(tmp_path, monkeypatch):
    """`not stated` is the `NOT MET` pole's first clause. Re-review measured that
    dropping it leaves 72 passed / 1 skipped GREEN — row 1 then publishes `MET`
    ("the rule is stated and wired") for a tree where no predicate exists."""
    (tmp_path / "tortoise").mkdir()
    # The predicate is ABSENT from the gate module.
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "ENV_FLAG = 'TORTOISE_EMBED_GATE'\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "import tortoise.embed_gate as embed_gate\n"
        "def encode_for_row(content):\n    return None\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "embedding_writers", lambda: _gated_writer("tortoise.a::_w"))
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "NOT MET", f"no stated predicate must not publish {row.verdict!r}"
    assert "stated: False" in row.observed, row.observed


def test_row_one_is_NOT_MET_when_the_rule_set_is_a_COPY(tmp_path, monkeypatch):
    """`not shared` — the identity check §12.2a exists for. A COPY of the rule
    set is the drift; dropping this clause measured GREEN (72 passed)."""
    import tortoise.embed_gate as embed_gate

    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "embedding_writers", lambda: _gated_writer("tortoise.a::_w"))
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(True))
    monkeypatch.setattr(embed_gate, "RULE_SET", (*tuple(embed_gate.RULE_SET), "x"))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "NOT MET", f"a copied rule set must not publish {row.verdict!r}"
    assert "one rule set: False" in row.observed, row.observed


def test_row_one_is_PARTIAL_when_an_unparseable_module_holds_the_row(
        tmp_path, monkeypatch):
    """`unparsed` — "a module that will not parse is a HOLE in the measurement,
    not a zero". Dropping this clause measured GREEN (72 passed)."""
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "embedding_writers", lambda: {
        **_gated_writer("tortoise.a::_w"),
        "tortoise.broken": _writer("tortoise.broken::", "unparsed"),
    })
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"an unparseable module must hold: {row.verdict!r}"


def test_row_one_is_PARTIAL_when_a_duplicate_identity_collided(
        tmp_path, monkeypatch):
    """`duplicates` — a collided writer name means the later definition
    overwrote the earlier one's label, so the population is not fully known.
    Dropping this clause measured GREEN (72 passed)."""
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "embedding_writers", lambda: {
        **_gated_writer("tortoise.a::_w"),
        "tortoise.__init__::": _writer("tortoise.__init__::", "duplicate"),
    })
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"a duplicate identity must hold: {row.verdict!r}"

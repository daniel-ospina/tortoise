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
| `test_the_census_reports_BYTES_for_an_UNINDEXED_label` | `#11088`'s Indicator — *"vector count **and bytes** per label"* — on the labels it is about | the byte figure is derived from the covering index's `dimension`, so the four UNINDEXED labels (the removable payload, and the whole reason the issue exists) report `UNMEASURED(no VECTOR index)` while the stored width sits already-probed one branch above |
| `test_every_affected_entity_type_is_derived_from_the_ONE_mapping` | the surface question is a SET of `entity_type` values, not a label count | `document`+`source` → `:Source` and `point`+`operator` → `:Point` collapse to one name each (or a label no type reaches starts claiming one), so the consequence is stated for the wrong population. ⚠️ It pins the VALUES, so a second table with identical content would pass — the regression it can see is a mapping collapse, which is the likely one |
| `test_the_census_states_the_consequence_of_removing_each_label` | the row's `affected_entity_types` / `removal_changes_query_results`, and `surface_consequence.requested_not_performed` | a census reads as an authorisation (`requested_not_performed` absent or `false`), a no-query-path label is listed as a surface consequence, or the whole thing goes through the write-capable verb |
| `test_an_unreadable_catalogue_row_is_not_an_unindexed_label` | `#11088` round-1 P1/P2: a row's three consequence cells must AGREE | `removal_changes_query_results` is derived from pre-override locals, so a row whose `indexed`/`unindexed` read `UNMEASURED` publishes a `True` consequence (and enters `surface_consequence`), and its payload says "NO covering index" for a catalogue it could not read |
| `test_an_UNMEASURED_consequence_is_CARRIED_not_dropped` | the under-reporting channel, `surface_consequence.unmeasured_labels` | a label whose consequence is `UNMEASURED` is skipped like a `False`, so `entity_types_whose_query_results_change` reads as the whole population while it is only the measured part |
| `test_an_unresolvable_leg_method_reports_UNMEASURED_not_an_EMPTY_surface` | round-2 P2: a rename must not read as "no tool is affected" | `VECTOR_LEG_SDK_METHOD` stops resolving, both sets empty, and the surface reports `[]` — a measured-looking "no MCP tool can pass `entity_type`" — with no `UNMEASURED` channel |
| `test_the_surface_named_is_the_one_that_can_pass_entity_type` | the CALLERS, derived from the registry **and** the handler AST **and** the SDK's own fixed-type call sites | the `sdk_method` binding alone is used, so `tortoise_query` (bound to `paginated_query`, yet forwarding a `text=` argument to `tortoise_fts_query(entity_type=…)`) is missing — the direction that UNDERSTATES a surface change |
| `test_a_tool_whose_SDK_method_fixes_entity_type_is_named` | the THIRD entrant source, and the default it must resolve before reading any call site | a tool whose SDK method names the type itself (`tortoise_recall` → `object`, `tortoise_search_sessions` → `event`) is named by neither the registry nor the handler scan, and a one-pass walk reports a defaulted call as `UNKNOWN` |
| `test_a_label_with_MIXED_stored_widths_refuses_the_byte_figure` | `#11088` round 7 P2: one row's width is not the label's | `stored × width × 4` multiplies ONE sampled row across every row, so a mixed-width label (an `EMBEDDING_DIM` rotation) publishes a wrong byte figure with no UNMEASURED signal |
| `test_the_census_reports_EVERY_index_KIND_not_only_VECTOR` | §3.1's *"the same run settles it"* — the FULLTEXT datum the run promised to answer | the catalogue parse keeps VECTOR rows only, so whether a lexically-matching query survives a removal is not in the answer, while the doc says it is |
| `test_the_LIVE_census_names_the_unindexed_labels_and_writes_NOTHING` | E2E D9 (`04-plan.md` §7) on a real engine: `Point` indexed, the four served labels unindexed **with bytes**, and the graph's visible shape unchanged across the call (⚠️ a corroborating observation — the READ-ONLY verb is what forbids the write) | the census mutates the graph it measures (labels / node counts / edges / the index catalogue / per-label embedding counts), or an unindexed label loses its `entity_type` set |
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
| `test_a_refusal_with_TWO_causes_names_BOTH_of_them` | `#11337`: a refusal must not repair one cause and hide the other | a covering index with an unreadable `dimension` over a mixed-width label refuses naming only the width, so the dimension cause is silently dropped and a reader who fixes the width is refused again with new text |
| `test_the_width_probe_report_says_the_rows_were_ENGINE_CHOSEN` | `#11337`: the probe query has no `ORDER BY`, so "the first 5" is not a property it has — asserted against the ISSUED statement, not the prose | `how` counts the sampled rows but says nothing about which rows they were, so `derived_payload_B` can move between two runs of the same graph with no explanation in the report — or the probe silently GAINS an `ORDER BY` while the report and the census doc keep asserting a bound the query no longer has |
| `test_the_census_doc_states_the_same_probe_bound_the_report_does` | the doc must state the bound the CODE emits, not a parallel wording that agrees only with a deterministic test fake | the doc keeps claiming an order the query does not impose, or the doc and `how` drift apart with neither failing (the positive assertion is derived from the emitted fragment, so a rewording has to move both) |
| `test_a_leg_reach_passed_BY_REFERENCE_is_declared_not_dropped` | `#11337`: the closure is over NAME-RESOLVED calls only, and its bound is DECLARED rather than implied — for every named shape, through to the published report | a method passes a leg-reaching function by reference (`_safe(self._review_pool, …)`, a bare name, literal or dynamic `getattr`, or an alias), the scan drops it with no entry anywhere, the caller list of an approval request reads short, or the declaration never reaches `vector_leg_surface()` |
| `test_a_DECLARED_bound_does_not_swallow_the_DERIVED_failure` | `#11337`: the two channels stay SEPARATE — a declared bound says *"this reach was not traversed"*, never *"the derivation could not run"* | a tree that BOTH takes a leg-reaching function by reference AND fails to derive the surface reports only the declaration, so the derived failure is absorbed into a caveat and the surface reads as merely bounded instead of unknown |
| `test_a_scan_FAILURE_is_not_a_DECLARED_bound` | the same separation, in the other direction: a scan failure must not be published as a declared bound | a failed walk is reported as `UNMEASURED(declared bound: …)`, so an entry that means *"nothing was measured"* is indistinguishable from one that means *"this named shape was measured and skipped"*, and the `declared bound:` marker stops identifying the by-reference class |
| `test_an_ERROR_line_does_not_mask_the_derived_FAILURE` | `derived_tool_rows` is read from the scan BEFORE the error lines reach `out`, so "derived nothing" and "published an error" stay different facts | the emptiness check reads `not out` AFTER the handler/leg error entries were appended, so in the error-present, nothing-derived case the derived-failure line is silently dropped and an underivable surface reads as a measured one beside an unrelated failure |

⛔ **These tests never write a graph and never flip a gate.** `#10465` is
measurement-only; the instrument is read-only and its pin must be too.
"""
from __future__ import annotations

import ast
import contextlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import dod_5090_probe as probe

from tortoise.embed_writer_register import LABELS_AT_RUNTIME, NO_LIVE_VECTOR


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

    _no_register(monkeypatch)
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

    ⛔ `#11492`: read the map the row SCORES (`probe.scored_labels()`), never
    `written_labels()`. The latter is the pure MEASUREMENT — it does not read the
    product register — so on the tree this row is aiming at (every writer resolved,
    row `MET`) it would still hand back `UNKNOWN` for the register-declared writers
    and this pin would red on a CORRECT change. Resolving in two places is how a row
    and its own pin disagree; there is one combinator and this test comes through it.
    """
    row = probe.score_path_bullet()
    assert row.verdict in ("MET", "PARTIAL"), row.observed
    scored = probe.scored_labels()
    assert scored["labels"] <= probe.served_labels(), (
        scored["labels"] - probe.served_labels())
    if scored["unresolved"]:
        assert row.verdict in ("PARTIAL", "NOT MET"), row.verdict


#: ── `#11747`: the write path the DISCOVERY cannot see ─────────────────────────────────
#: `cache_projection.node_statement` stores its vector through an INTERPOLATED property name
#: (`f", n.{prop} = {form}"`), which is one of the shapes `_EMBED_WRITE` and the AST pass cannot
#: decide — so the writer was ABSENT from `embedding_writers()` and a `:Session` vector reached the
#: graph while this row printed `MET` / `stored-without-a-query-path: none`. The tests below are that
#: hole's closure: the first pins that the row MEASURES the projection at all (and that the
#: measurement is non-vacuous); the second that emptying the guard turns the row RED; the third that
#: the probe's THIRD (directly-built `RecordNode`) door is measured; the fourth that the PLANTED
#: property set is derived rather than the literal `embedding`; and the fifth that an unbuildable
#: label HOLDS the row instead of crashing it.


def test_row_two_MEASURES_the_cache_projection_by_running_it():
    """`#11747` — measured by EXECUTING the writer, because reading it is the blind spot.

    ⚠️ Non-vacuity is the load-bearing half. A derivation that simply returned nothing (or that
    the import broke) would satisfy `labels <= served` on the live tree while seeing no store at
    all — the fail-open shape every row in this file refuses. So the assertion is that the
    projection DOES store vectors, that every label it stores on is served, and that `Session`
    is not among them.
    """
    measured = probe.cache_projection_store_labels()
    assert measured["error"] is None, measured["error"]
    assert measured["unmeasured"] == {}, measured["unmeasured"]
    assert measured["labels"], (
        "the projection reports NO vector store at all — the probe is measuring nothing, so a "
        "`labels <= served` assertion below would pass vacuously")
    assert measured["labels"] <= probe.served_labels(), (
        sorted(measured["labels"] - probe.served_labels()))
    assert "Session" not in measured["labels"]
    assert probe.score_path_bullet().verdict == "MET"


def test_re_introducing_the_session_vector_flips_row_two_to_NOT_MET(monkeypatch):
    """The falsifier: delete the guard and this row must go RED by itself.

    ⛔ Mutating the GATE (not the probe's inputs) is the point: it exercises the real derivation
    path end to end — `node_from_row` → `node_statement` → `stmt.params` → served-set comparison
    — so the test reds on the failure a future lane would actually introduce, rather than on a
    mock the probe was told to believe.
    """
    import tortoise.cache_projection as cp

    monkeypatch.setattr(cp, "UNSERVED_VECTOR_LABELS", frozenset())
    assert "Session" in probe.cache_projection_store_labels()["labels"]
    row = probe.score_path_bullet()
    assert row.verdict == "NOT MET", row.observed
    assert "stored-without-a-query-path: ['Session']" in row.observed


def test_the_PROBES_direct_writer_door_is_what_this_pin_measures(monkeypatch):
    """The falsifier for the probe's THIRD door (`#11747` review, finding 5).

    ⛔ WHAT THIS PIN MEASURES, stated plainly (`#11747` round-3 review, finding 4b): the probe's
    DIRECTLY-BUILT `RecordNode` door, and nothing else. `record_plan` is public, so a caller may
    hand the writer a node that never passed `node_from_row` — a row that cannot see that door
    claims more than it measures.

    ⚠️ It is NOT a falsifier for `node_statement`'s own check, which its old name and docstring
    claimed. The `monkeypatch` below empties `UNSERVED_VECTOR_LABELS`, which disables BOTH gates at
    once, and the wrapper re-applies the row→property drop by hand — so deleting
    `node_statement`'s check from the source leaves this test GREEN. That check is pinned where it
    is actually attributable:
    `test_cache_projection.py::test_the_writer_refuses_a_directly_built_unserved_node`.
    """
    import tortoise.cache_projection as cp

    real_unserved = cp.UNSERVED_VECTOR_LABELS
    real_from_row = cp.node_from_row
    assert "Session" in real_unserved, sorted(real_unserved)

    def _from_row_with_its_drop_intact(label, row):
        """`node_from_row` with the gate PINNED, so only the writer's is removed."""
        node = real_from_row(label, row)
        if label not in real_unserved:
            return node
        props = dict(node.props)
        dropped = dict(node.dropped_vectors)
        for prop in sorted(cp.stored_vector_props()):
            if props.pop(prop, None) is not None:
                dropped[prop] = "pinned by this test"
        return cp.RecordNode(label=label, key=node.key, props=props,
                             dropped_vectors=dropped)

    monkeypatch.setattr(cp, "node_from_row", _from_row_with_its_drop_intact)
    monkeypatch.setattr(cp, "UNSERVED_VECTOR_LABELS", frozenset())

    measured = probe.cache_projection_store_labels()
    assert measured["error"] is None, measured["error"]
    assert "Session" in measured["labels"], (
        "the DIRECT writer door is unmeasured: with `node_statement`'s check deleted and the "
        "row→property drop intact, a `:Session` vector can only be seen through a node the "
        f"caller built itself. Measured: {sorted(measured['labels'])}")
    row = probe.score_path_bullet()
    assert row.verdict == "NOT MET", row.observed
    assert "stored-without-a-query-path: ['Session']" in row.observed


def test_the_PLANTED_property_set_is_DERIVED_not_the_literal_embedding(monkeypatch):
    """`#11747` round-3 review, finding 4a — the falsifier the derived planting was missing.

    `planted = {prop: vector for prop in vector_props}` reads `cp.stored_vector_props()`, but that
    set had exactly ONE member (`embedding`), so reverting the derivation to the old literal
    `{"embedding": vector}` red NO row in the tree: the derivation was unfalsifiable and the
    round-2 defect could have come back silently the day a second vector property joined the set.

    So this pin MOVES the stored set to a property the literal would never plant, and keeps the
    label served (otherwise the writer's own gate hides the store the probe must see). Nothing is
    mocked into belief: the probe, `node_from_row` and `node_statement` all read the same patched
    set, so the row's answer comes from the real derivation path.

    ⛔ What reds: the assertion that `Session` is MEASURED. With the literal planting the row would
    carry `embedding`, which is no longer a stored property — it lands in the `props` map, never in
    `stmt.params` as a store clause — so the probe would see nothing and row 2 would keep printing
    `stored-without-a-query-path: none`. The control at the end proves that half explicitly.
    """
    import tortoise.cache_projection as cp
    import tortoise.record_projection as rp

    monkeypatch.setattr(cp, "STORED_VECTOR_PROPS", frozenset({"embedding_v2"}))
    monkeypatch.setattr(cp, "UNSERVED_VECTOR_LABELS", frozenset())

    measured = probe.cache_projection_store_labels()
    assert measured["error"] is None, measured["error"]
    assert "Session" in measured["labels"], (
        "the probe planted only the literal `embedding`: the projection's stored property is "
        f"{sorted(cp.stored_vector_props())} and the store it emits was not inspected. "
        f"Measured: {sorted(measured['labels'])}")
    row = probe.score_path_bullet()
    assert row.verdict == "NOT MET", row.observed
    assert "stored-without-a-query-path: ['Session']" in row.observed, row.observed

    # The control: the LITERAL planting is invisible to this row — it never reaches
    # `stmt.params` as a store clause, which is what makes the assertion above a falsifier.
    key_column = rp.snake(cp.KEY_PROPERTY["Session"])
    literal = cp.node_statement(cp.RecordNode(
        label="Session", key="dod-literal",
        props={key_column: "dod-literal", "embedding": [0.5, 0.25]}))
    assert "embedding_v2" not in literal.params
    assert "embedding" not in literal.params
    assert literal.params["props"]["embedding"] == [0.5, 0.25]


def test_the_row_two_MEASURES_the_EDGE_vector_door(monkeypatch):
    """`#12015` — the projection's EDGE store is measured, not assumed away.

    `#11747` made row 2 measure the projection's NODE doors by running them. The same
    writer has an EDGE door (`relation_from_row` → `relation_statement`, plus a
    directly-built `RecordRelation`), and a node-only measurement keeps printing
    `stored-without-a-query-path: none` over an edge vector — the identical blind spot,
    one object over.

    ⛔ Non-vacuity is the load-bearing half, and it is asserted by the FALSIFIER below
    (`test_re_introducing_an_EDGE_vector…`), not here: on the fixed tree the correct
    answer IS the empty set, so "nothing seen" and "nothing measurable" look alike. What
    this test can assert is that the doors were actually BUILT for every allowlisted
    predicate — an unbuildable door is reported as unmeasured, and unmeasured is not
    clean.
    """
    measured = probe.cache_projection_store_labels()
    assert measured["error"] is None, measured["error"]
    assert measured["relationships"] == frozenset(), sorted(measured["relationships"])
    edge_unmeasured = {k: v for k, v in measured["unmeasured"].items()
                       if k.startswith("<relationship")}
    assert edge_unmeasured == {}, (
        "an edge door could not be built, so the empty set above is not a measurement: "
        f"{edge_unmeasured}")
    # ⛔ The POSITIVE signal (`#12015` review): an empty `relationships` is the SAME value
    # an arm that planted on nothing would return, so the population itself is asserted —
    # deleting the edge block now reds here instead of leaving this pin green.
    from tortoise.security import KNOWN_REL_TYPES

    assert measured["relationships_probed"] == frozenset(KNOWN_REL_TYPES), (
        "the edge arm did not run over every allowlisted predicate: "
        f"{sorted(measured['relationships_probed'])}")
    row = probe.score_path_bullet()
    assert row.verdict == "MET", row.observed
    assert "cache projection RELATIONSHIP vector targets" in row.observed, row.observed
    assert "stored-without-a-query-path: none" in row.observed, row.observed
    # ⛔ The POPULATION is PRINTED and an EMPTY one holds the row at PARTIAL — and both
    # halves are pinned here (`#12015` round 4: measured UNPINNED by removing the
    # `not projection["relationships_probed"]` arm and watching every test in this file
    # stay green — a green run OF THE FILE AS IT STOOD THEN, which is why no count is
    # quoted: this file collects a different number today, so any unscoped figure here would
    # be the stale-measurement defect the §6 note below forbids — the reason this note
    # names no live count at all is that a forward merge moves it, `#12281`'s own
    # `183 -> 187` being the demonstration). Without it, "41 edge types
    # measured, none stores a vector" and "the edge arm never ran" are the SAME printed
    # evidence — `stored-without-a-query-path: none`
    # / `MET` — which is the rule the neighbouring notes already state: a `MET` must not
    # rest on an unprinted (or unmeasured) set.
    assert "PLANTED ON and saw REFUSE" in row.observed, row.observed
    assert "BELONGS_TO" in row.observed, row.observed

    empty = dict(measured, relationships=frozenset(), relationships_probed=frozenset())
    monkeypatch.setattr(probe, "cache_projection_store_labels", lambda: empty)
    unrun = probe.score_path_bullet()
    assert unrun.verdict != "MET", (
        "an edge arm that measured NOTHING reported the row clean — 'no edge stores a "
        f"vector' and 'no edge was probed' are indistinguishable: {unrun.observed}")
    assert unrun.verdict == "PARTIAL", unrun.observed


def test_re_introducing_an_EDGE_vector_flips_row_two_to_NOT_MET(monkeypatch):
    """The falsifier: restore the old edge writer's behaviour and row 2 must go red.

    ⛔ Mutating the GATE (not the probe's inputs) is the point, exactly as the `#11747`
    pin above does: the real derivation path runs end to end — `relation_from_row` →
    `relation_statement` → `stmt.params["props"]` → the orphan comparison — so this
    reds on the failure a future lane would actually introduce.

    It is ALSO the non-vacuity proof for the test above: a probe that could not see an
    edge store at all would report `relationships: []` here too, and this assertion
    would fail.
    """
    import tortoise.cache_projection as cp

    monkeypatch.setattr(cp, "relation_vector_declined_properties", lambda: frozenset())
    measured = probe.cache_projection_store_labels()
    assert measured["error"] is None, measured["error"]
    assert "IMPL" in measured["relationships"], (
        "the EDGE door is unmeasured: with the decline removed, the planted vector reaches "
        f"`SET r += $props` and must be seen. Measured: {sorted(measured['relationships'])}")
    assert "IMPL" not in measured["labels"], (
        "a relationship type is NOT a node label — it must be reported under its own key")
    row = probe.score_path_bullet()
    assert row.verdict == "NOT MET", row.observed
    assert "<relationship:IMPL>" in row.observed, row.observed
    assert "stored-without-a-query-path: none" not in row.observed, row.observed


def test_an_edge_arm_that_stops_PLANTING_cannot_report_full_coverage(monkeypatch):
    """The population is DROP-derived, not INPUT-derived (#12015 review, finding C).

    `cache_projection_store_labels` documents that `relationships_probed` is *"recorded only
    where a door RECORDED A DROP"*, so that an arm which stops planting cannot still report
    full coverage. That promise was pinned by NOTHING: deriving the population from the input
    list (`edge_probed.add(rel_type)` at the top of the per-type loop — the exact variant the
    probe's own comment calls MEASURED wrong) left the file as it stood then green at
    `171 passed, 2 skipped` (=173, SCOPED to that head — this file collects a different
    number today, so the figure is not re-cut). What CANNOT be reproduced here is that
    GREEN observation: the
    variant is CAUGHT now — measured, it leaves the population pin above SATISFIED
    (input-derived equals `KNOWN_REL_TYPES`, so `:607` is not the assert that reds) while
    THIS test's own `relationships_probed == frozenset()` assertion below does red. Only
    the empty-the-integer direction was pinned by the test above.

    So this disables the PLANT: with no stored-vector property to plant, no door can record a
    drop, and the population must collapse — while the shipped code stays green (the plant is
    empty, not the door missing).
    """
    import tortoise.cache_projection as cp

    monkeypatch.setattr(cp, "stored_vector_props", frozenset)
    measured = probe.cache_projection_store_labels()
    assert measured["error"] is None, measured["error"]
    assert measured["relationships_probed"] == frozenset(), (
        "an arm that planted nothing still reported a PROBED population, so the population "
        "is derived from its input rather than from an observed refusal: "
        f"{sorted(measured['relationships_probed'])}")
    # …and the row must not then rest on that emptiness as if it were a measurement.
    monkeypatch.setattr(probe, "cache_projection_store_labels", lambda: measured)
    assert probe.score_path_bullet().verdict != "MET", (
        "a row reported MET over an edge arm that never planted a vector")


def test_a_record_label_without_a_key_HOLDS_the_row_instead_of_crashing(monkeypatch):
    """`#11747` review, finding 4 — the per-label derivation is inside the envelope.

    ⛔ MEASURED pre-fix: only the imports and the two door-builds were wrapped, so a label in
    `cp.NODE_TABLES` that `cp.KEY_PROPERTY` does not know made the WHOLE DoD report raise
    `KeyError` — instead of holding row 2 at `PARTIAL`, which is what this function's own
    contract says an unbuildable shape does ("a label where NEITHER shape could be built is
    reported as unmeasured"). Before `#11747` row 2 could not be taken down by a projection-side
    `KeyError` at all.
    """
    import tortoise.cache_projection as cp

    monkeypatch.setitem(cp.NODE_TABLES, "NoSuchRecordLabel", "no_such_record_label")

    measured = probe.cache_projection_store_labels()
    assert measured["error"] is None, measured["error"]
    assert "NoSuchRecordLabel" in measured["unmeasured"], measured
    assert "KeyError" in measured["unmeasured"]["NoSuchRecordLabel"], measured["unmeasured"]
    # …and the ROW holds rather than reporting a verdict it could not measure.
    assert probe.score_path_bullet().verdict == "PARTIAL"
    # The other labels are still measured: a hold on one must not blind the rest.
    assert "Session" not in measured["labels"]
    assert measured["labels"], "nothing measured at all — the pin above would be vacuous"


#: ── `#11492`: row 2 resolves labels from the SAME product register row 1 reads ──────────────
#: The issue required the reading to be DECIDED, not assumed: the register classified writers but
#: carried NO label (Case B), and the label is not derivable per writer either (15 of the 20
#: outside-gate writers name no USABLE label in their own Cypher, and they fail for SEVERAL
#: different reasons — a `MATCH` the derivation does not credit, a module constant, a property-map
#: merge over a constant tuple, a delegated store (`_index_process_unit` → `_session_event_write`),
#: a clause builder (`set_embedding_clause`), or ONE carrying the caller's
#: statement, and those six are a SAMPLE rather than the set: the other NINE are a carrier a later
#: pass stores (`add_point`, `_journal_event_raw_write`), a body that issues no store at all — a
#: read, a read into a dict, comment PROSE (`_compare_views`, `_graph_fingerprint`,
#: `turn_prior_vectors`, `_index_gate_read`, `_apply_one`), or a docstring QUOTING one
#: (`_prewipe_synthetic_write_scope`, whose whole body is a `return`; the `SET n.embedding = NULL` it
#: quotes is `_rebuild_all_replay`'s, and that writer is NOT among these fifteen), and a
#: runtime-interpolated label (`restore_graph`) — so no single one of them may be named as "the"
#: reason). So the product DECLARES it (`WriterClass.stores`) and this row reads that declaration.
#: The tests below pin the three falsifiers the issue names, and the two guards implementation made
#: necessary.
def _store_declared(name: str, stores, *, cls: str = probe.CENSUS_EXEMPT) -> dict:
    """One register entry in the shape row 2 reads — with an explicit `stores`."""
    return {**_declared(name, cls), "stores": stores}


#: A writer map for the row-2 tests: one GATED writer (its label is DERIVED from its Cypher, so the
#: register must stay a supplement to it) and two outside-gate writers whose label no derivation can
#: reach — the population the declaration exists for.
def _row_two_population() -> dict:
    return {
        "tortoise.x::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/x.py:1",
            "kind": "gated", "label": "Point", "callees": frozenset(),
        },
        **_unresolved_writer("tortoise.y::_store_event"),
        **_unresolved_writer("tortoise.z::_reads_a_vector"),
    }


def _row_two_declarations(**overrides) -> dict:
    declared = {
        "tortoise.y::_store_event": _store_declared("tortoise.y::_store_event", ("Event",)),
        "tortoise.z::_reads_a_vector": _store_declared(
            "tortoise.z::_reads_a_vector", NO_LIVE_VECTOR),
    }
    declared.update(overrides)
    return declared


def test_row_two_reads_MET_when_the_register_resolves_every_writer(monkeypatch):
    """`#11492` — row 2 `MET` once every writer's vector is on a SERVED label.

    Three ways a writer gets there, all present at once: through the gate (label derived), from a
    declared label, or by a declaration that it stores no live graph vector. ⛔ The third is what
    makes `stored-without-a-query-path` a statement about STORES rather than about NAMES: ten of
    this tree's twenty outside-gate writers issue no vector store of their own (reads, comment
    prose — `_compare_views`, `_apply_one` — carriers, a store DELEGATED to `_session_event_write`
    — `_index_process_unit` — a docstring QUOTING a wipe, the clause builder), and two more store on
    a label chosen at RUNTIME — those two are held as
    `PARTIAL`, never as `no-live-vector`, so twelve is the size of the two holds together and NOT
    the count of writers that never store. A row that held those ten forever was measuring its own
    vocabulary.
    """
    monkeypatch.setattr(probe, "embedding_writers", _row_two_population)
    monkeypatch.setattr(probe, "declared_writers", _row_two_declarations)
    row = probe.score_path_bullet()
    assert row.verdict == "MET", row.observed
    assert "unresolved writers" not in " ".join(row.notes), row.notes
    assert "#11492" in " ".join(row.notes), (
        f"a MET resting on DECLARED labels must say so: {row.notes}")


def test_row_two_returns_to_a_hold_when_a_writer_stores_on_an_UNSERVED_label(monkeypatch):
    """`#11492`'s falsifier — `stored-without-a-query-path` must be able to go non-empty again.

    ⛔ Mutation-proved, and the mutation is INSIDE this test: the same population and the same
    scoring path read `MET` with `("Event",)` and hold with `("Batch",)` — the ONLY difference is
    one declared label. That is what a declaration is for, and it means the row cannot be greened
    by declaring the hole away: a label outside `run_vector_query`'s served set is reported as an
    ORPHAN, which is `NOT MET` (stronger than `PARTIAL`), and the label is named in the note.

    Proof the test is a real discriminator (run in-lane, recorded on the PR): the same test against
    `origin/main`'s `score_path_bullet` FAILS — the pre-fix row 2 cannot produce an orphan from a
    declaration at all, because it never reads one.
    """
    monkeypatch.setattr(probe, "embedding_writers", _row_two_population)
    monkeypatch.setattr(probe, "declared_writers", _row_two_declarations)
    assert probe.score_path_bullet().verdict == "MET"
    # MUTATION: one writer's declared label moves off the served set. Nothing else changes.
    monkeypatch.setattr(
        probe, "declared_writers",
        lambda: _row_two_declarations(**{
            "tortoise.y::_store_event": _store_declared(
                "tortoise.y::_store_event", ("Batch",))}))
    row = probe.score_path_bullet()
    assert row.verdict == "NOT MET", row.observed
    assert "stored-without-a-query-path: ['Batch']" in " ".join(row.notes), row.notes
    assert "Batch" not in probe.served_labels()


def test_a_bypass_in_the_register_holds_row_two(monkeypatch):
    """`#11492` — a non-empty `bypass` holds the row.

    ⛔ Before this change a bypass was only NOTED: `real_bypasses` was computed and printed, and it
    never reached the verdict, so a row could read `MET` while naming a defect in its own evidence.
    The class is read from the DECLARATION as well as the census, because the register is where a
    class is declared — `register.load()` refuses `bypass`, so this shape only reaches the row from
    an injected or tampered register, which is exactly why the row must not assume it is well-formed.
    """
    monkeypatch.setattr(probe, "embedding_writers", _row_two_population)
    monkeypatch.setattr(
        probe, "declared_writers",
        lambda: _row_two_declarations(**{
            "tortoise.y::_store_event": _store_declared(
                "tortoise.y::_store_event", ("Event",), cls=probe.CENSUS_BYPASS)}))
    row = probe.score_path_bullet()
    assert row.verdict == "PARTIAL", row.observed
    assert any("REAL BYPASSES" in n for n in row.notes), row.notes
    assert any("tortoise.y::_store_event" in n for n in row.notes if "BYPASS" in n), row.notes


def test_a_census_bypass_holds_row_two_even_when_the_register_RESOLVES_the_writer(monkeypatch):
    """The bypass hold is a property of the WRITER, not of the row's ability to resolve its label.

    ⛔ `#11492`'s round-2 review (both reviewers) measured the inverse: `real_bypasses` was derived
    from the still-unresolved population, so a register declaration that resolved a writer's label
    also removed it from the population a `bypass` is read from — the better the declaration, the
    fewer defects the row could see, and a census-class `bypass` on a declared writer would read
    `MET` in no note. It is now computed over every writer.
    """
    writers = _row_two_population()
    census = dict(probe.WRITER_CENSUS)
    census["tortoise.y::_store_event"] = (
        probe.CENSUS_BYPASS, "injected for this test: a REAL bypass")
    monkeypatch.setattr(probe, "WRITER_CENSUS", census)
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    # …and the register DOES resolve that writer, which is the case that used to hide it.
    monkeypatch.setattr(
        probe, "declared_writers",
        lambda: _row_two_declarations(**{
            "tortoise.y::_store_event": _store_declared(
                "tortoise.y::_store_event", ("Event",)),
            "tortoise.z::_reads_a_vector": _store_declared(
                "tortoise.z::_reads_a_vector", NO_LIVE_VECTOR)}))
    row = probe.score_path_bullet()
    assert row.verdict == "PARTIAL", row.observed
    assert any("REAL BYPASSES" in n for n in row.notes), row.notes
    assert any("tortoise.y::_store_event" in n for n in row.notes if "BYPASS" in n), row.notes


def test_a_declaration_that_CONTRADICTS_a_measured_label_holds_row_two(monkeypatch):
    """A declared label the writer's own Cypher does not name is a HOLD, never a silent skip.

    ⛔ `#11492`'s last review measured the false pass: the candidate filter admits only writers
    whose derivation FAILED, so a declaration on a successfully-derived writer was never read at
    all — a live-register writer declared `stores=("Event", "Batch")` (accepted by
    `register.load()`) left `Batch` out of the served-set comparison and the row read `MET`.
    Both sides are now reported: the row holds, names the writer, and shows the two label sets, so
    a reviewer can rule on which side is wrong (part of the derivation is a heuristic, so the
    declaration is not assumed wrong either).

    ⛔ `LABELS_AT_RUNTIME` is the THIRD contradictory shape and the one this check first shipped
    without: it is a `str`, so it satisfied neither the `NO_LIVE_VECTOR` arm nor the label-tuple arm
    and was silently DROPPED — a contradiction read as agreement, leaving the row free to read
    `MET`. It contradicts a measured label exactly as the other two do (the derivation resolved
    `Point`; the declaration says the label is not statically knowable), so it is exercised here in
    the same loop (review finding, P1).
    """
    writers = {
        "tortoise.x::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/x.py:1",
            "kind": "gated", "label": "Point", "callees": frozenset(),
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    assert "Batch" not in probe.served_labels()
    for stores in (("Point", "Batch"), NO_LIVE_VECTOR, LABELS_AT_RUNTIME):
        monkeypatch.setattr(
            probe, "declared_writers",
            lambda stores=stores: {"tortoise.x::_upsert_point": _store_declared(
                "tortoise.x::_upsert_point", stores)})
        row = probe.score_path_bullet()
        assert row.verdict == "PARTIAL", (stores, row.observed)
        note = [n for n in row.notes if "CONTRADICT" in n]
        assert note, (stores, row.notes)
        assert "tortoise.x::_upsert_point" in note[0], note
    # …and an UNUSABLE declaration on the same writer is a HOLD too, not a silent drop. The
    # contradiction loop discarded `_store_reading`'s violation, so its `None` reading matched no
    # arm and fell through: `("Point", "Batch", 3)` names a second, UNSERVED label but was dropped
    # before the served-set comparison because one member is malformed, and the row read `MET`.
    monkeypatch.setattr(
        probe, "declared_writers",
        lambda: {"tortoise.x::_upsert_point": _store_declared(
            "tortoise.x::_upsert_point", ("Point", "Batch", 3))})
    row = probe.score_path_bullet()
    assert row.verdict == "PARTIAL", row.observed
    assert any("tortoise.x::_upsert_point" in n for n in row.notes), row.notes
    # …and the AGREEING declaration is not a hold: the check reports disagreement, not difference.
    monkeypatch.setattr(
        probe, "declared_writers",
        lambda: {"tortoise.x::_upsert_point": _store_declared(
            "tortoise.x::_upsert_point", ("Point",))})
    assert probe.score_path_bullet().verdict == "MET"


def test_a_PARTIAL_measurement_cannot_be_narrowed_by_a_declaration(monkeypatch):
    """A two-label `UNKNOWN` is a MEASUREMENT, not a gap — a declaration may not replace it.

    ⛔ `#11492`'s review reproduced the inverse of the whole point: for an `ungated` writer whose
    own Cypher names TWO labels (`UNKNOWN(two labels: Batch,Event)`, the shape
    `_embedding_writers` produces), a declaration of `("Event",)` — or `NO_LIVE_VECTOR` — REPLACED
    the measurement, dropped the measured `Batch` before the served-set comparison, and the row
    read `MET` with `stored-without-a-query-path: none` while an unserved label had been stored.
    A PARTIAL measurement has already measured; a declaration's job is to fill a GAP, so these
    writers hold the row and are named as partial.

    ⛔ The VERDICT assertion above is not enough on its own (round-3 review, P1): measured at
    `b8ada30ce1b`, deleting the `writer not in out["partial"]` exclusion — i.e. ALLOWING the
    narrowing — left this pin and 13 siblings GREEN, because a redundant second net
    (`or resolved["partial"]` in the verdict) still held the row at `PARTIAL`. The verdict was
    right for a reason the pin never named. So the pin now also asserts the MEASUREMENT survives
    the declaration: the writer's resolved label must still be the measured two-label string (not
    the declared single label), and it must still be in the PARTIAL hold. Those two assertions are
    what a deleted exclusion reds — verified by mutation in both directions.
    """
    writers = {
        "tortoise.x::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/x.py:1",
            "kind": "gated", "label": "Point", "callees": frozenset(),
        },
        "tortoise.y::_two_labels": {
            "callee": "encode_for_store", "at": "tortoise/y.py:2",
            "kind": "ungated", "label": "UNKNOWN(two labels: Batch,Point)",
            "callees": frozenset(),
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    assert "Batch" not in probe.served_labels()
    for stores in (("Point",), NO_LIVE_VECTOR, None):
        declared = {} if stores is None else {
            "tortoise.y::_two_labels": _store_declared("tortoise.y::_two_labels", stores)}
        monkeypatch.setattr(probe, "declared_writers", lambda declared=declared: declared)
        row = probe.score_path_bullet()
        assert row.verdict == "PARTIAL", (stores, row.observed)
        assert any("MORE THAN ONE label" in n for n in row.notes), (stores, row.notes)
        # ⛔ The MEASUREMENT must survive the declaration — this is the property the verdict alone
        # could not discriminate (see the docstring). Under a deleted `partial` exclusion the
        # declared single label replaces it and `Batch` is dropped before the served-set compare.
        scored = probe.scored_labels()
        assert scored["by_writer"].get("tortoise.y::_two_labels") == \
            "UNKNOWN(two labels: Batch,Point)", (
                "a declaration REPLACED a MEASURED two-label label — the measured `Batch` was "
                "dropped before the served-set comparison, so an unserved label could be stored "
                f"with `stored-without-a-query-path: none` (got "
                f"{scored['by_writer'].get('tortoise.y::_two_labels')!r}, stores={stores!r})"
            )
        assert "tortoise.y::_two_labels" in scored["resolution"]["partial"], (
            "a measured two-label writer left the PARTIAL hold — a declaration may fill a GAP, "
            f"never replace a MEASUREMENT (resolution={scored['resolution']})"
        )


def test_the_two_label_DERIVATION_carries_the_partial_prefix(tmp_path):
    """The `partial` hold above is reached by a `startswith(PARTIAL_LABEL_PREFIX)` test — so the
    DERIVATION must produce a label carrying that prefix, and nothing else pinned it.

    ⛔ THE HOLE THIS CLOSES (`#11495`'s review, measured). Every fixture that exercises the
    `partial` hold — the one above, `::test_a_gated_writer_with_an_UNSINGLE_label_is_NAMED_not_just_held`,
    and the unreadable-register fixtures — INJECTS the literal `"UNKNOWN(two labels: …)"` rather
    than deriving it, while `PARTIAL_LABEL_PREFIX` was a HAND-RETYPED copy of the literal the
    derivation built. Nothing tied the two together: retyping the derivation to
    `"UNKNOWN(2 labels: "` left **194 passed, 2 skipped** across
    `tests/test_dod_5090_probe.py` + `tests/test_embed_writer_register.py` — the whole suite green
    while a two-label MEASUREMENT silently stopped being held, so a declaration could narrow it
    before the served-set comparison and read `MET` on a stored unserved label. The derivation is
    now BUILT from the constant (making the drift structurally impossible); this pin is what
    catches a future restructure that stops deriving from it.

    Drives the REAL discovery over a scratch tree, so it exercises `_embedding_writers` itself —
    not an injected label map, which is the blind spot that let the drift ship.
    """
    (tmp_path / "tortoise").mkdir(parents=True)
    # The seam module is skipped by discovery; it only has to EXIST for the import to be real.
    (tmp_path / "tortoise" / "embeddings.py").write_text("", encoding="utf-8")
    (tmp_path / "tortoise" / "two_labels.py").write_text(
        "from tortoise.embeddings import encode_for_row\n"
        "\n"
        "def _two_labels():\n"
        "    EVENT = 'MERGE (e:Event {id:$i}) SET e.embedding = $emb'\n"
        "    BATCH = 'MERGE (b:Batch {id:$b}) SET b.embedding = $emb'\n"
        "    return encode_for_row(EVENT + BATCH)\n",
        encoding="utf-8")
    entry = probe._embedding_writers(tmp_path)["tortoise.two_labels::_two_labels"]
    assert entry["label"].startswith(probe.PARTIAL_LABEL_PREFIX), (
        "the derivation's two-label label does NOT carry PARTIAL_LABEL_PREFIX, so row 2's "
        f"`partial` hold can never match it: {entry['label']!r} vs "
        f"{probe.PARTIAL_LABEL_PREFIX!r}")
    assert "Batch" in entry["label"] and "Event" in entry["label"], entry["label"]


def test_a_gated_writer_with_an_UNSINGLE_label_is_NAMED_not_just_held(monkeypatch):
    """The `unexplained` note needs its own falsifier, or a `PARTIAL` can name nobody.

    ⛔ Measured by the reviewer: replacing the `if unexplained:` block with a no-op left the whole
    probe suite green, so the note that exists so a `PARTIAL` NAMES a `gated` writer whose own
    Cypher yielded a plain `UNKNOWN` could be deleted with nothing red — while the verdict still
    reads `PARTIAL` (via `unknown`) and therefore names nobody. That is the round-1 defect the row's
    own comments cite as the reason the note exists. Its sibling branch ("MORE THAN ONE label") IS
    pinned; this pins the other half AND the distinction between them, which the comments claim.
    """
    writers = {
        # The shape this note owns: a GATED writer whose own Cypher named no single label.
        "tortoise.x::_gated_unknown": {
            "callee": probe.SEAM_CALL, "at": "tortoise/x.py:1",
            "kind": "gated", "label": "UNKNOWN", "callees": frozenset(),
        },
        # The shape it must NOT own: a two-label label is a MEASUREMENT (its own note).
        "tortoise.y::_two_labels": {
            "callee": probe.SEAM_CALL, "at": "tortoise/y.py:2",
            "kind": "gated", "label": "UNKNOWN(two labels: Batch, Point)",
            "callees": frozenset(),
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    monkeypatch.setattr(probe, "declared_writers", lambda: {})
    row = probe.score_path_bullet()
    assert row.verdict == "PARTIAL", row.observed
    unexplained = [n for n in row.notes if "does not resolve from a declaration" in n]
    assert unexplained, f"the plain-UNKNOWN gated writer must be NAMED: {row.notes}"
    assert "tortoise.x::_gated_unknown" in unexplained[0], unexplained
    assert "tortoise.y::_two_labels" not in unexplained[0], (
        f"a two-label MEASUREMENT belongs to its own note, not this one: {unexplained}")
    assert any("MORE THAN ONE label" in n and "tortoise.y::_two_labels" in n
               for n in row.notes), row.notes


def test_an_ungated_plain_UNKNOWN_writer_is_resolved_by_its_declaration(monkeypatch):
    """`#11492` review (P2): the `ungated` arm of row 2's candidate filter is DRIVEN.

    The filter admits `kind in ("unresolved_write", "ungated")`, and every other row-2 fixture
    exercises the `unresolved_write` arm only: the sole `ungated` fixtures carry a two-label
    `UNKNOWN(two labels: …)` MEASUREMENT, which the `partial` exclusion removes *before* the
    filter is consulted, so they never reach it. Measured at the head this test was added to:
    narrowing the tuple to `("unresolved_write",)` left this file at `161 passed, 2 skipped` —
    the arm could be deleted with nothing red.

    Why deleting it is not cosmetic: an `ungated` writer whose own Cypher named NO single label
    (the derivation yields the plain `UNKNOWN`) that the register DOES declare would stay in
    `unknown`, and the note the row prints for that set says *"nothing in the product says which
    label they store on"* — a false statement about the product, on a `PARTIAL` that names the
    wrong cause. That is the misattribution this row fixed in two other branches, and the arm is
    the only thing that keeps it out of this one.
    """
    writers = {
        "tortoise.x::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/x.py:1",
            "kind": "gated", "label": "Point", "callees": frozenset(),
        },
        # `ungated`, and its OWN Cypher names no single label — the plain `UNKNOWN` shape.
        "tortoise.y::_ungated_unknown": {
            "callee": "compute_session_embedding", "at": "tortoise/y.py:2",
            "kind": "ungated", "label": "UNKNOWN",
            "callees": frozenset({"compute_session_embedding"}),
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)

    # 1. UNDECLARED — the writer is in the candidate set, so the row names it as a product gap.
    monkeypatch.setattr(probe, "declared_writers", lambda: {})
    row = probe.score_path_bullet()
    assert row.verdict == "PARTIAL", row.observed
    unresolved = [n for n in row.notes if "unresolved writers" in n]
    assert unresolved and "tortoise.y::_ungated_unknown" in unresolved[0], (
        "an undeclared `ungated` writer with a plain UNKNOWN label must be NAMED as a product "
        f"gap: {row.notes}")

    # 2. DECLARED — the SAME writer is now scored from its declaration, and the "nothing in the
    #    product says" note must not name it. Deleting the `ungated` arm reds this assertion,
    #    because the declaration would never be consulted and the writer would still be blamed.
    monkeypatch.setattr(
        probe, "declared_writers",
        lambda: {"tortoise.y::_ungated_unknown": _store_declared(
            "tortoise.y::_ungated_unknown", ("Event",))})
    row = probe.score_path_bullet()
    scored = probe.scored_labels()
    assert scored["by_writer"].get("tortoise.y::_ungated_unknown") == "Event", (
        "the `ungated` arm of row 2's candidate filter did not admit the writer, so its "
        f"declaration was never consulted (by_writer={scored['by_writer']})")
    assert not any("tortoise.y::_ungated_unknown" in n
                   for n in row.notes if "unresolved writers" in n), (
        "a writer the register DECLARES was reported as one nothing in the product answers "
        f"for: {row.notes}")


def test_a_runtime_label_declaration_holds_row_two_without_calling_it_unresolved(monkeypatch):
    """`#11492` — `labels-at-runtime` holds the row, and it is NOT the "unresolved writers" hold.

    This is the measurement's honest outcome on the live tree: `tortoise.hosted_backup::restore_graph`
    recreates nodes from a DUMP, so its vector's label is decided at restore time. The row cannot
    claim the served-set comparison, and it must not confuse that with a writer nothing declares —
    those are two different defects with two different fixes.
    """
    monkeypatch.setattr(probe, "embedding_writers", _row_two_population)
    monkeypatch.setattr(
        probe, "declared_writers",
        lambda: _row_two_declarations(**{
            "tortoise.y::_store_event": _store_declared(
                "tortoise.y::_store_event", LABELS_AT_RUNTIME)}))
    row = probe.score_path_bullet()
    assert row.verdict == "PARTIAL", row.observed
    assert any("decided at RUNTIME" in n for n in row.notes), row.notes
    assert not any("unresolved writers" in n for n in row.notes), row.notes
    # ⛔ …and the WRITER must carry the runtime reason, not the "nothing in the product says"
    # one (live-head review, P2). The bookkeeping that does it was deletable with the suite green
    # (measured: replacing it with `pass` left 162 passed, 1 skipped), and without it a writer the
    # register DOES declare `LABELS_AT_RUNTIME` is reported as an UNDECLARED one — blaming the
    # product for a label its own declaration says is decided at restore time.
    scored = probe.scored_labels()
    assert scored["unresolved"].get("tortoise.y::_store_event") == probe.AT_RUNTIME_LABEL, (
        "a declared `LABELS_AT_RUNTIME` writer is reported as an undeclared one — that note says "
        "'nothing in the product says which label they store on', which its own declaration "
        f"contradicts (got {scored['unresolved'].get('tortoise.y::_store_event')!r})"
    )
    assert "tortoise.y::_store_event" not in scored["by_writer"], (
        "a runtime-label writer was resolved to a serving label — its label is not the register's "
        f"to declare (by_writer={scored['by_writer']})"
    )


def test_a_DERIVED_label_beats_a_declaration(monkeypatch):
    """The register is a SUPPLEMENT to the measurement, never a replacement for it.

    A `gated` writer's label is derived from its own Cypher, so a declaration that disagreed with
    it must not be able to move the row: otherwise the register could overwrite what the tree
    MEASURES, which is the second-rule-set failure §12.2a forbids.

    ⛔ …and it must not be DISCARDED either (`#11492`'s last review, P2): the first version of this
    behaviour skipped any declaration on a successfully-derived writer, so a live-register writer
    declared `stores=("Event", "Batch")` — a shape `register.load()` accepts — reached the
    served-set comparison with `Batch` never mentioned, and the row could read `MET` over a
    declared unserved label. Measured is still not overwritten (the label shown to the comparison is
    the DERIVED one), and the disagreement is now the row's finding.
    """
    monkeypatch.setattr(probe, "embedding_writers", _row_two_population)
    monkeypatch.setattr(
        probe, "declared_writers",
        lambda: _row_two_declarations(**{
            # The GATED writer is derived as `Point`; the declaration claims an unserved label.
            "tortoise.x::_upsert_point": _store_declared(
                "tortoise.x::_upsert_point", ("Batch",))}))
    row = probe.score_path_bullet()
    assert "written labels: ['Event', 'Point']" in " ".join(row.notes), (
        f"the MEASURED label must still be what the row compares (this fixture also carries the "
        f"resolved `Event` and the no-live-vector writer): {row.notes}")
    assert "tortoise.x::_upsert_point=Point" not in " ".join(row.notes), (
        f"the declaration must NOT replace the measured label: {row.notes}")
    assert row.verdict == "PARTIAL", row.observed
    assert any("CONTRADICT" in n for n in row.notes), row.notes
    assert any("tortoise.x::_upsert_point" in n for n in row.notes if "CONTRADICT" in n), row.notes


def test_an_UNREADABLE_register_is_UNMEASURED_not_a_green_row_two(monkeypatch):
    """A register that cannot be read holds the row — the misattribution row 1 refuses, one row over.

    An empty answer here would render every outside-gate writer `unresolved` and blame the PRODUCT
    for the instrument's own failure; a `MET` would be worse still, because the one input that can
    flip the row would be missing. Neither is allowed.

    ⛔ `#11492` round-2 review (P2): the first version of this test used a population with NO
    unresolved writer, so it could not see that the row printed BOTH `…none is blamed on the
    product` AND `unresolved writers … nothing in the product says which label they store on` about
    the same writers — a self-contradicting note that asserts a product defect which does not exist.
    The fixture now carries an outside-gate writer whose own label derivation failed — kind
    `unresolved_write`, label `UNKNOWN` (`_unresolved_writer`) — which is the shape that produced
    the second note. ⛔ Name it precisely (round-3 review): it is NOT the `ungated` kind, and a
    rebuild that swapped in `kind: "ungated"` with a RESOLVED label would put the writer outside
    `unknown` altogether, so the note under test would never print and this pin would go vacuous.
    """
    def _unreadable():
        raise RuntimeError("register exploded")

    # ⛔ The population matters: with only GATED writers there is nothing to mis-attribute, and
    # the first version of this test injected exactly that — so it stayed green while the row
    # named all nineteen outside-gate writers as "nothing in the product says which label they
    # store on" in the same cell that said none was blamed on the product (`#11492`'s round-2
    # review, blocking). `_row_two_population()` carries the unresolved writers this must not
    # blame.
    #
    # ⛔ Round-2 review (P2), second pass: emptying `unexplained` on the error path ALSO dropped the
    # `gated` writer whose own Cypher named no single label — a subject the register is never
    # consulted for, so its evidence is unaffected by the register being unreadable. That writer is
    # added below, and its note must be PRESENT: a `PARTIAL` that names nobody is the defect round 1
    # fixed, one branch over.
    population = dict(_row_two_population())
    population["tortoise.w::_gated_two_labels"] = {
        "callee": probe.SEAM_CALL, "at": "tortoise/w.py:3",
        "kind": "gated", "label": "UNKNOWN(two labels: Batch, Point)", "callees": frozenset(),
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: population)
    monkeypatch.setattr(probe, "declared_writers", _unreadable)
    row = probe.score_path_bullet()
    assert row.verdict == "PARTIAL", row.observed
    assert any("UNMEASURED" in n for n in row.notes), row.notes
    assert any("register exploded" in n for n in row.notes), row.notes
    joined = " ".join(row.notes)
    assert "unresolved writers" not in joined, (
        "an unreadable register resolves NOTHING, so it must not also assert that the product "
        f"declares nothing for these writers: {row.notes}")
    # The gated writer's own derivation named TWO labels, which the register could not have helped
    # with — so it must still NAME it, or the row holds `PARTIAL` without saying why. ⛔ Which note
    # names it is the PARTIAL one: a two-label `UNKNOWN` is a MEASUREMENT, not a gap, and `#11492`
    # moved it out of `unexplained` (and out of every declaration's reach) so no declaration can
    # narrow it away. The assertion is therefore on the WRITER being named by a hold note, not on
    # one sentence's wording.
    assert "tortoise.w::_gated_two_labels" in joined, row.notes
    assert any("MORE THAN ONE label" in n for n in row.notes), row.notes

    # ⛔ …and the case the docstring above names as WORSE than the misattribution — an unreadable
    # register over a population whose labels ALL DERIVE, where there is no `unknown` writer left to
    # hold the row. Measured (live-head review, P1): removing `or resolved["error"]` from the
    # verdict left the WHOLE file green (162 passed, 1 skipped), and on this fixture the row then
    # read `MET` while still printing `UNMEASURED` — the self-contradicting note row 1 refuses, one
    # row over, and a false PASS. The arm is the only thing standing between an unread register and
    # a `MET` here, so it is exercised explicitly rather than inherited.
    derived = {
        "tortoise.a::_store_event": {
            "callee": probe.SEAM_CALL, "at": "tortoise/a.py:1",
            "kind": "gated", "label": "Event", "callees": frozenset(),
        },
        "tortoise.b::_store_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/b.py:2",
            "kind": "gated", "label": "Point", "callees": frozenset(),
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: derived)
    all_derived = probe.score_path_bullet()
    assert any("UNMEASURED" in n for n in all_derived.notes), all_derived.notes
    assert all_derived.verdict == "PARTIAL", (
        "an unreadable register over a fully-derived population did not hold the row — the one "
        f"input that can flip it was missing, so a `MET` here is a false pass: {all_derived.observed}"
    )


def test_a_malformed_stores_declaration_holds_row_two(monkeypatch):
    """A `stores` that does not answer the question is a NAMED hold, never an unresolved label.

    `register.load()` refuses every shape below, so these reach the row only from an injected or
    tampered register — and the row must grade the shape itself rather than inherit an invariant it
    does not test (`declaration_violations`' own discipline, one row over). An empty tuple in
    particular is the shrug that would otherwise read as "nothing to compare".

    ⛔ `("Ev ent",)` and `("Event\n",)` are here for the SHAPE branch specifically (final review,
    P2). Every other member of this list is caught by an EARLIER arm — `empty` / `not tuple` /
    `bare string` / a non-string member — so the label-shape regex was reached by nothing, and
    deleting it left the whole suite green (measured: 189 passed, 1 skipped, identical to the
    unmutated run). A space and a trailing newline are the two shapes `register._LABEL` and
    `_STORE_LABEL` reject that no earlier arm sees, so they are what makes that branch
    load-bearing. The direction is fail-CLOSED either way (a bad shape can never collide with a
    served label), so this pins the NAMED hold rather than a false pass.
    """
    for bad in ((), [], "Event", 3, ("",), ("Event", 3), ("Ev ent",), ("Event\n",)):
        monkeypatch.setattr(probe, "embedding_writers", _row_two_population)
        monkeypatch.setattr(
            probe, "declared_writers",
            lambda bad=bad: _row_two_declarations(**{
                "tortoise.y::_store_event": _store_declared(
                    "tortoise.y::_store_event", bad)}))
        row = probe.score_path_bullet()
        assert row.verdict == "PARTIAL", (bad, row.observed)
        assert any("malformed stores declarations" in n for n in row.notes), (bad, row.notes)
        assert any("tortoise.y::_store_event" in n for n in row.notes), (bad, row.notes)
        # ⛔ NOT `monkeypatch.undo()`: that also reverts the autouse fixture and every hermetic
        # patch for the remaining iterations (broader than this test's subject). `setattr` below
        # simply replaces the previous value, which is all the loop needs.


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

    ⛔ `#11444`: `tortoise/embed_writer_register.py` is now a SUBJECT of this
    file's own scoring — row 1 reads it — so it must SELECT this file too. It
    currently selects through the `core` fallback (it matches no named
    SOURCE_PATTERNS entry, which `ci-surfaces.yml` records at its
    `test_embed_writer_register.py` row); pinned here so a future named match
    that REPLACES the fallback cannot silently stop selecting the instrument
    whose row now depends on it. Taken from `#11463`'s tree when this PR was kept
    over it in the `#11444` dedupe, so its one unique guard is not lost.
    """
    import ci_selection

    manifest = ci_selection.load_manifest()
    for subject in ("tortoise/embed_gate.py", "tortoise/value_gate.py",
                    "tortoise/embeddings.py", "tortoise/search_engine.py",
                    "tortoise/security.py", "tools/dod_5090_probe.py",
                    "tortoise/embed_writer_register.py"):
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
        #: Every statement the census issued, in order. Recorded so a claim
        #: ABOUT the query (e.g. "the probe carries no `ORDER BY`") can be
        #: asserted against the statement itself instead of against prose that
        #: happens to agree with it (#11337 review).
        self.queries: list[str] = []

    # ⛔ BOTH verbs answer through `_answer`, because the census must issue its
    # reads through `ro_query` (the engine's read-only verb) and a fake that
    # implemented only the write-capable `query` would make a revert invisible.
    # Subclasses override `_answer`, never one verb, so neither can go untested.
    def query(self, cypher, params=None, timeout=None):
        return self._answer(cypher)

    def ro_query(self, cypher, params=None, timeout=None):
        return self._answer(cypher)

    def _answer(self, cypher):
        self.queries.append(cypher)
        if "db.indexes()" in cypher:
            rows = self._catalogue
        elif "db.labels()" in cypher:
            rows = [[lab] for lab in self._labels]
        elif "RETURN count(n)" in cypher:
            label = cypher.split("MATCH (n:")[1].split(")")[0].strip("`")
            rows = [[self._vectors.get(label, (0, None))[0]]]
        elif "RETURN n.embedding" in cypher:
            label = cypher.split("MATCH (n:")[1].split(")")[0].strip("`")
            samples = self._vectors.get(label, (0, None))[1]
            # A LIST OF VECTORS is several sampled rows (the width probe reads
            # more than one); a single vector is one row. Without this a test
            # could not model the mixed-width label `WIDTH_PROBE_SAMPLES`
            # exists to refuse (#11088 review round 7, P2).
            if samples and isinstance(samples[0], list):
                rows = [[v] for v in samples]
            else:
                rows = [[samples]]
            # ⛔ HONOUR THE `LIMIT` the production query issues. Returning every
            # sample regardless made the mixed-width test pass with
            # `WIDTH_PROBE_SAMPLES = 1` — i.e. with the exact single-row bug it
            # exists to catch — so the test could not fail on a regression
            # (review round 9, P2: a verification gate with no discriminating
            # power).
            limit = cypher.rsplit("LIMIT", 1)[-1].strip()
            if limit.isdigit():
                rows = rows[:int(limit)]
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


def test_a_refusal_with_TWO_causes_names_BOTH_of_them():
    """A refusal must not repair one cause and hide the other (#11337).

    Measured failure this pins: a covering `embedding` index whose `dimension`
    cell is unreadable, on a label that ALSO holds two stored widths, refused
    with `UNMEASURED(coverage not provable: the stored width (stored widths
    disagree (384, 1024)))` — the dimension cause was computed and then
    REASSIGNED away. A reader repairs the width probe, re-runs, is refused
    again with different text, and is never told the second cause exists.

    BOTH arms of the width cause are covered (a disagreeing sample and an
    unprobeable one), because the assignment that dropped the dimension cause
    lived in the disagreeing-arms branch while the plain arm joined correctly —
    a fix in one arm leaves the other invisible.
    """
    # (a) unreadable dimension + sampled widths that DISAGREE
    disagreeing = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", None)],
        labels=["Point"],
        vectors={"Point": (6, [[0.0] * 384] + [[0.0] * 1024] * 5)},
    )
    row = probe.vector_census(disagreeing, "g", at="2026-11-07T00:00:00Z",
                              graph_names=["g"])["labels"]["Point"]
    refused = str(row["unindexed"])
    assert refused.startswith("UNMEASURED"), row
    assert "index dimension" in refused, (
        "the unreadable-dimension cause vanished from a two-cause refusal: "
        f"{refused}")
    assert "stored width" in refused, refused
    assert "disagree" in refused, refused

    # (b) unreadable dimension + a width probe that yielded NO row
    unprobeable = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", None)],
        labels=["Point"],
        vectors={"Point": (5, None)},
    )
    other = str(probe.vector_census(
        unprobeable, "g", at="2026-11-07T00:00:00Z",
        graph_names=["g"])["labels"]["Point"]["unindexed"])
    assert "index dimension" in other, other
    assert "stored width" in other, other


    # (c) unreadable dimension + an UNREADABLE STORED COUNT (#11389 review, P2:
    # the two-cause rule was applied to the dim/width pair only, so this arm —
    # one branch up in `vector_census` — still reported the count alone while
    # `indexed: true` sat beside it, and repairing the count refuses again on
    # the dimension. Measured before the fix with this fake.)
    class _CountUnreadable(_CensusGraph):
        def _answer(self, cypher):
            if "RETURN count(n)" in cypher:
                raise RuntimeError("count unreadable")
            return super()._answer(cypher)

    uncountable = _CountUnreadable(
        catalogue=[_index_row("Point", "embedding", None)],
        labels=["Point"],
        vectors={"Point": (6, [[0.0] * 384])},
    )
    third = probe.vector_census(uncountable, "g", at="2026-11-07T00:00:00Z",
                                graph_names=["g"])["labels"]["Point"]
    refusal = str(third["unindexed"])
    assert third["indexed"] is True, third
    assert "stored count unreadable" in refusal, refusal
    assert "index dimension" in refusal, (
        f"the unreadable-dimension cause is masked by the count cause: {refusal}")
    # ⛔ THE SINGLE-CAUSE TEXT IS BYTE-IDENTICAL: a count failure with a READABLE
    # dimension still refuses in exactly the old words, because the third
    # single-cause text is part of this change's contract.
    readable_dim = _CountUnreadable(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"],
        vectors={"Point": (6, [[0.0] * 384])},
    )
    alone = str(probe.vector_census(
        readable_dim, "g", at="2026-11-07T00:00:00Z",
        graph_names=["g"])["labels"]["Point"]["unindexed"])
    assert alone == "UNMEASURED(stored count unreadable)", alone

    # (d) THE BYTE FIGURE refuses with BOTH causes too (#11389 review, P2). The
    # rule reached `unindexed` and stopped one branch short of the payload, so
    # this row named both causes in one field and the width alone in the other —
    # repair the width, re-run, be refused again on the dimension.
    for label, samples in (("disagreeing", [[0.0] * 384] + [[0.0] * 1024] * 5),
                           ("unprobeable", None)):
        mixed = _CensusGraph(
            catalogue=[_index_row("Point", "embedding", None)],
            labels=["Point"],
            vectors={"Point": (5 if samples is None else 6, samples)},
        )
        pay = probe.vector_census(
            mixed, "g", at="2026-11-07T00:00:00Z",
            graph_names=["g"])["labels"]["Point"]["payload"]
        figure = str(pay["derived_payload_B"])
        assert "index dimension" in figure, (
            f"the byte figure ({label}) drops the unreadable-dimension cause: "
            f"{figure}")
        assert ("stored width" in figure or "stored widths" in figure), figure
        # ⛔ …and `how` — the REMEDY channel — must name it too, or a reader
        # fixes only the width and is refused again (#11389 review, P2).
        assert "index dimension" in str(pay["how"]), (
            f"`how` ({label}) names only the width while the refusal names "
            f"both: {pay['how']}")
    # ⛔ …and the single-cause text is byte-identical: a label with NO covering
    # index has no index dimension to name, so the figure refuses in the old
    # words.
    no_index = _CensusGraph(
        catalogue=[], labels=["Point"],
        vectors={"Point": (5, [[0.0] * 384] + [[0.0] * 1024] * 4)},
    )
    solo = str(probe.vector_census(
        no_index, "g", at="2026-11-07T00:00:00Z",
        graph_names=["g"])["labels"]["Point"]["payload"][
            "derived_payload_B"])
    assert solo == ("UNMEASURED(stored widths disagree (384, 1024), so stored "
                    "x width is not a figure)"), solo

    # (e) A FAILED CATALOGUE READ DOES NOT CANCEL THE WIDTH THE PROBE MEASURED
    # (#11389 review, P2). An unattributable catalogue row held BOTH columns at
    # `catalogue row unreadable`, with `disagree` appearing nowhere in the row —
    # though the probe had been issued and had returned both widths. Repair the
    # catalogue, re-run, be refused again on the width: the same round trip.
    blind = _CensusGraph(
        catalogue=[[]], labels=["Point"],
        vectors={"Point": (6, [[0.0] * 384] + [[0.0] * 1024] * 5)},
    )
    row_e = probe.vector_census(blind, "g", at="2026-11-07T00:00:00Z",
                                graph_names=["g"])["labels"]["Point"]
    for field in ("unindexed", "payload"):
        blob = str(row_e["unindexed"] if field == "unindexed"
                   else row_e["payload"]["derived_payload_B"])
        assert ("catalogue row" in blob and "unreadable" in blob), (field, blob)
        assert "disagree" in blob, (
            f"{field} discards the disagreement the width probe measured "
            f"because a DIFFERENT read failed: {blob}")


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
    # ⛔ #11088 review round 1, P1: `changes` was computed from the LOCALS, and
    # an unreadable catalogue row leaves `covering` empty — so the `else:
    # unindexed = stored` branch fabricated full coverage and this row asserted
    # a surface consequence (and entered `surface_consequence`) while its own
    # `unindexed` cell three lines up read UNMEASURED. Same for the byte figure,
    # whose `not covering` arm then said "NO covering index" (P2): `covering` is
    # empty both because there is no index AND because the row could not be
    # read, and those are different facts.
    assert str(row["removal_changes_query_results"]).startswith("UNMEASURED"), row
    assert str(row["payload"]["derived_payload_B"]).startswith("UNMEASURED"), row
    assert "NO covering index" not in str(row["payload"]), row
    assert "Point" not in {a["label"] for a in
                           census["surface_consequence"]["labels"]}, (
        "a label whose coverage is unreadable must not be published as a "
        f"measured surface consequence: {census['surface_consequence']}")


def test_an_UNMEASURED_consequence_is_CARRIED_not_dropped():
    """A hole in one label must not silently shrink the affected set.

    ⛔ #11088 review round 1, P1: `surface_consequence` skipped every row whose
    `removal_changes_query_results` was not `True`, so an unmeasurable label
    simply vanished — `entity_types_whose_query_results_change` printed `[]` —
    while the doc calls UNDER-reporting the dangerous direction (a surface
    request whose consequence is understated is one the owner can approve by
    mistake). Reachable on a live engine: a per-label count that times out, and
    a backtick-bearing label (which `_cypher_label`'s own docstring records as
    failing closed into `stored=UNMEASURED(...)`).
    """
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Object"],
        vectors={"Object": ("boom", None)},  # the count query could not answer
    )
    census = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                                 graph_names=["g"])
    row = census["labels"]["Object"]
    assert str(row["removal_changes_query_results"]).startswith("UNMEASURED"), row
    sc = census["surface_consequence"]
    assert sc["entity_types_whose_query_results_change"] == [], sc
    assert [e["label"] for e in sc["unmeasured_labels"]] == ["Object"], (
        f"an unmeasured consequence was DROPPED, so the affected set reads as "
        f"complete when it is not: {sc}")
    assert sc["unmeasured_labels"][0]["entity_types"] == ["object"], sc


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

    ⛔ `#11455`: the value the ROW reports is the DEPLOYMENT reading
    (`shipped["deployment"]["on"]`), not the any-surface bool — so this falsifier
    is written against that field and against the per-surface census the row must
    carry. Asserting `shipped["on"]` here would re-pin the exact single-bool
    reading the issue exists to remove.
    """
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert isinstance(shipped["on"], bool), shipped
    deployment = shipped["deployment"]
    assert isinstance(deployment["on"], bool), shipped
    assert set(deployment["surfaces"]) <= set(shipped["surfaces"]), shipped
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
    assert f"gate enabled by default: {deployment['on']}" in note, (note, shipped)
    # The census the issue asks for: EVERY surface this repo ships is named with
    # its state, and the armed/silent split is stated — so a reader can tell a
    # measured `True` from a sampled one without re-running the walk.
    assert "per surface:" in note, note
    for rel in probe.CONFIG_SURFACES:
        assert f"{rel}=" in note, (rel, note)
    assert "deployment surfaces that can arm a deployed process:" in note, note
    for rel in deployment["surfaces"]:
        assert f"{rel}=" in note, (rel, note)


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


#: ── `#11455`: the default is a PER-SURFACE reading, not any-one-surface ──────
def test_the_gate_is_not_read_True_off_ONE_armed_surface(tmp_path, monkeypatch):
    """The measured shape: `.env.example` arms it, `docker-compose.yml` is silent.

    This is exactly this repo's tree after `#11445`, reproduced on a synthetic root
    (the falsifier `#11455` states). The row read one bool over all six surfaces, so
    the armed TEMPLATE published `gate enabled by default: True` while the surface
    `docker compose up` actually consumes declares nothing — and `#11086` item 4
    (*"row-1's condition reads True"*) could then be ticked and read as item 1
    (*"a deployment built from the shipped config rejects an identifier-only row"*),
    which is false for the compose path.

    So: the any-surface read is REPORTED (`on` — a template that arms the flag is a
    fact), the deployment read is `False` and NAMES the silent surface, and the row
    says so in its own text rather than reading a bare `True`.
    """
    (tmp_path / ".env.example").write_text("TORTOISE_EMBED_GATE=1\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  tortoise:\n    environment:\n      FOO: bar\n", encoding="utf-8"
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is True, shipped
    assert shipped["armed"] == [".env.example"], shipped
    assert shipped["deployment"]["on"] is False, shipped
    assert shipped["deployment"]["silent"] == ["docker-compose.yml"], shipped
    note = _gate_note(shipped)
    assert "gate enabled by default: False" in note, note
    assert ".env.example=ON" in note, note
    assert "docker-compose.yml=OFF" in note, note
    assert "UNARMED deployment file(s) ['docker-compose.yml']" in note, note
    # The OFF branch states a PER-FILE fact, not a deployment consequence (fresh
    # review of PR #12016, P3): a silent compose file that builds from an armed
    # `Dockerfile*` inherits the image's `ENV`, so the deployment IS armed and the
    # unqualified "a deployment built from them runs with the gate OFF" published the
    # opposite of what the walk measured. Pin the qualification, not just the claim.
    assert "read from that file ALONE the gate is off" in note, note
    assert "INHERITS that image's `ENV`" in note, note
    # ⛔ The OFF branch closes on PER-FILE semantics, never on a negation of the
    # sentence above it (fresh review of PR #12016, P2): the clause used to read
    # "This condition is SCOPED to the surface(s) that arm it (.env.example) and is
    # NOT a claim about a deployment built from the silent path(s)" — naming the
    # ANY-SURFACE template as the population of a DEPLOYMENT reading, and telling the
    # reader the row says nothing about `docker compose up` (the `#11455` reading).
    assert "this reading is PER FILE" in note, note
    assert "is NOT a claim about a deployment built from the silent path" not in note, note
    # ⛔ …and neither spelling of the wrong-population scope may come back: the clause
    # named `shipped["armed"]` (the ANY-SURFACE template list) as the population of a
    # DEPLOYMENT reading. `.env.example` cannot arm a deployment on its own.
    assert "SCOPED to the surface(s) that arm it" not in note, note


def test_the_row_VERDICT_reads_the_DEPLOYMENT_not_the_any_surface_bool(
        tmp_path, monkeypatch):
    """The `#11455` CLAUSE, SCORED — one armed TEMPLATE must not complete row 1.

    ⛔ Fresh review of PR #12016 (P2): nearly every `probe.score_rule_bullet()` call
    site in this file feeds a reading in which `shipped["on"]` and
    `shipped["deployment"]["on"]` AGREE — `_fake_shipped` sets both from one argument,
    and `_row_one_fixture` arms `docker-compose.yml`, which is both an any-surface and a
    deployment surface. ⛔ NO COUNT IS STATED HERE ON PURPOSE: every commit that adds a
    call site moves it, and this PR's own fix commits already did, three times over. Do
    not substitute `31 + 1` either — measure. And a bare `grep -c` over this file also
    matches THIS sentence, so it over-reports by one (fresh review of PR #12016, P3).
    The call sites that DIVERGE are this test, the `#11455` row-holds test below, and
    `test_the_shipped_default_is_searched_not_assumed` — which scores the REAL tree but
    asserts only the note, never `row.verdict`. So reverting the one line
    `on_by_default = deployment["on"]` to `shipped["on"]`, or folding `ambient` back
    into it, left the WHOLE suite green — and `row.verdict` is the artefact `#11086`
    item 4 is ticked on, so the clause `#11455` exists for was pinned nowhere.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    # The divergent shape: the TEMPLATE arms the flag, the deployment surface does not.
    (tmp_path / ".env.example").write_text("TORTOISE_EMBED_GATE=1\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    _no_register(monkeypatch)
    monkeypatch.setattr(probe, "embedding_writers",
                        lambda: _gated_writer("tortoise.a::_w"))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is True, shipped
    assert shipped["deployment"]["on"] is False, shipped
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    hold = next(n for n in row.notes if n.startswith("shipped default NOT armed"))
    assert "docker-compose.yml" in hold, hold
    # ⛔ The TEMPLATE branch, pinned (round-12 review, P3): with `.env.example` the only
    # armed surface the intersection with the deployment surfaces is EMPTY, so the note
    # must carry the template clause and NOT the deployment one. Without this the branch
    # selection could be inverted (`if shipped["armed"] else`) and the whole suite stayed
    # green — measured — while the real tree, the shape a reader actually reads, printed
    # "the surface(s) that do arm it () are themselves deployment files, so a build that
    # uses one of them carries the flag" (EMPTY parentheses: the clause interpolates the
    # intersection, which the mutated condition never computes).
    assert "do not deploy the container's environment on their own" in hold, hold
    assert "the flag is armed in deployment file(s)" not in hold, hold
    # ⛔ The `#11455` sentence lives in THIS arm and must be PINNED here (fresh review
    # of PR #12016, P3): it was moved out of the shared tail, and nothing asserted its
    # presence — deleting it left the whole file green while the real tree, which takes
    # this very arm, lost the reading from the published row.
    assert "no armed non-deployment surface makes this a shipped default" in hold, hold
    assert "`#11455` reading" in hold, hold
    # ⛔ …and the noun is population-neutral ON PURPOSE: this arm is reached by ANY
    # armed non-deployment surface, and `config/` and `.github/workflows` are IN that
    # population, so calling it "a TEMPLATE" would name a shape narrower than the arm's.
    # ⛔ Stated as a SHAPE, not a measurement: on this tree both read OFF, so asserting
    # that they arm it would be a measurement the walk never made (fresh review of PR
    # #12016, P3 — this comment previously said they "arm the flag", which is false).
    assert "one armed TEMPLATE" not in hold, hold

    # ⛔ And the THIRD shape, where NOTHING arms the flag at all (fresh review of PR
    # #12016, P3). The two-arm form published "the surfaces that do arm it (none) do
    # not deploy the container's environment on their own" and closed "one armed
    # TEMPLATE is not a shipped default" — naming a set as `none` while still speaking
    # about the surfaces that DO arm it, and asserting a template where zero templates
    # arm the flag. A published diagnostic must name ITS OWN state, not a neighbour's;
    # the `#11455` reading now lives in the arm where a template really does arm it.
    (tmp_path / ".env.example").write_text("SOME_OTHER_VAR=1\n", encoding="utf-8")
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["armed"] == [], shipped
    assert shipped["deployment"]["armed"] == [], shipped
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    hold = next(n for n in row.notes if n.startswith("shipped default NOT armed"))
    assert "no shipped surface arms it at all" in hold, hold
    assert "(none)" not in hold, hold

    # ⛔ And the FOURTH shape, where the only candidate surface could not be READ
    # (fresh review of PR #12016, P3). `shipped["armed"]` holds only `ON` rels, so an
    # UNMEASURED surface is absent from it too — and the arm above would then publish
    # "no shipped surface arms it at all" over a path the walk never read, the exact
    # absence-over-UNREAD claim `flag_in_shipped_configs` forbids ("a file that could
    # not be read is not a file that is silent"). The 2 MB bound is the walk's own.
    (tmp_path / ".env.example").write_text(
        "#" + "x" * 2_100_000, encoding="utf-8")
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["armed"] == [], shipped
    assert shipped["unmeasured"] == [".env.example"], shipped
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    hold = next(n for n in row.notes if n.startswith("shipped default NOT armed"))
    assert "could not be read" in hold, hold
    assert "UNMEASURED here, not a measurement" in hold, hold
    assert "no shipped surface arms it at all" not in hold, hold

    # ⛔ And a HIT in the same surface root as an unreadable file must still read
    # ARMED (fresh review of PR #12016, P3). `UNMEASURED` used to SUPERSEDE `ON`, so
    # such a root was dropped from `armed` entirely and the note then published "no
    # surface the walk could READ arms it" over a file it had just READ arming it — an
    # absence asserted over a positive measurement. A hit is a positive measurement; an
    # unreadable sibling adds uncertainty about the files not read, it cannot
    # un-measure one the walk did read. The unreadable file must nonetheless stay
    # REPORTED, which is the arm that keeps this from becoming a quiet OFF.
    (tmp_path / ".env.example").write_text("SOME_OTHER_VAR=1\n", encoding="utf-8")
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "armed.yml").write_text(
        "value_gate:\n  TORTOISE_EMBED_GATE: 1\n", encoding="utf-8")
    (cfg / "big.yml").write_text("#" + "x" * 2_100_000, encoding="utf-8")
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["armed"] == ["config"], shipped
    assert shipped["unmeasured"] == [], shipped
    assert shipped["unreadable"] == ["config/big.yml"], shipped
    row = probe.score_rule_bullet()
    hold = next(n for n in row.notes if n.startswith("shipped default NOT armed"))
    assert "the surface(s) that do arm it (config)" in hold, hold
    assert "could not be read" not in hold, hold

    # …and the `ambient` HALF of the same clause: a lane's exported flag is not a
    # DEFAULT. With the switch armed in the ENVIRONMENT and the deployment still
    # silent, an `ambient or deployment["on"]` reading turns this row `MET` — the
    # `#11455` defect in its other direction, which is why `measure()` pins the
    # switches OFF before scoring.
    monkeypatch.setenv("TORTOISE_EMBED_GATE", "1")
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"


def test_every_shipped_surface_is_named_with_a_state(tmp_path, monkeypatch):
    """ON / OFF / ABSENT / UNMEASURED per surface — never a single bool.

    `#11455`'s Targets: "for EVERY surface in `CONFIG_SURFACES` the row note states
    ON / OFF / absent". The four states are distinct on purpose: an absent compose
    file and a silent one are different facts, and an UNREADABLE file is neither —
    silence is a measurement, and a surface that could not be read must not be read
    as silent (that is the UNMEASURED arm, tested separately below).
    """
    (tmp_path / ".env.example").write_text("TORTOISE_EMBED_GATE=1\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "x.yml").write_text("nothing here\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    states = {rel: shipped["per_surface"][rel]["state"] for rel in probe.CONFIG_SURFACES}
    assert states == {
        ".env.example": "ON",
        "docker-compose.yml": "OFF",
        "docker-compose.yaml": "ABSENT",
        "Dockerfile": "ABSENT",
        "fly.toml": "ABSENT",
        "entrypoint": "ABSENT",
        "config": "OFF",
        ".github/workflows": "ABSENT",
    }, states
    note = _gate_note(shipped)
    for rel, state in states.items():
        assert f"{rel}={state}" in note, (rel, state, note)


def test_an_unreadable_deployment_surface_is_UNMEASURED_not_silent(
        tmp_path, monkeypatch):
    """A file the walk could not read makes the surface UNMEASURED, never OFF.

    `#11455`'s posture (`#11465`'s too): a row that cannot be read is UNMEASURED,
    never a pass. "Silent" is a MEASUREMENT; a surface half of which was never read
    cannot supply it, and reading it as OFF would blame the product's config for the
    instrument's own blindness.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "environment:\n  TORTOISE_EMBED_GATE: \"1\"\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    real_read_text = Path.read_text

    def _boom(self, *a, **kw):
        if self.name == "docker-compose.yml":
            raise OSError("unreadable")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["docker-compose.yml"]["state"] == "UNMEASURED", shipped
    assert shipped["deployment"]["on"] is False, shipped
    assert shipped["deployment"]["unmeasured"] == ["docker-compose.yml"], shipped
    note = _gate_note(shipped)
    # ⛔ The leading value is UNMEASURED, never `False`: `False` is an answer about a
    # population this run could not read (fresh review of PR #12016, P3).
    assert "gate enabled by default: UNMEASURED" in note, note
    assert "gate enabled by default: False" not in note, note
    assert "docker-compose.yml=UNMEASURED" in note, note


def test_a_tree_with_NO_deployment_surface_is_UNMEASURED(tmp_path, monkeypatch):
    """No compose file and no Dockerfile: nothing can answer "what a deployment gets".

    The pre-`#11455` bool reported `False` here, which reads as "the config sets
    nothing" — a claim about a population that does not exist. The row must say
    UNMEASURED and hold, and must not claim a default either way.
    """
    _row_one_fixture(tmp_path, monkeypatch)  # rule tree + one armed compose surface
    (tmp_path / "docker-compose.yml").unlink()  # …and now no deployment surface at all
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    assert shipped["deployment"]["present"] == [], shipped
    assert shipped["deployment"]["measured"] is False, shipped
    assert shipped["deployment"]["on"] is False, shipped
    note = _gate_note(shipped)
    assert "gate enabled by default: UNMEASURED" in note, note
    assert "no deployment surface exists" in note, note
    # …and the ROW holds on it, naming UNMEASURED rather than "the config sets
    # nothing" — the misattribution `#11455`'s third requirement forbids.
    _no_register(monkeypatch)
    monkeypatch.setattr(probe, "embedding_writers", lambda: _gated_writer("tortoise.a::_w"))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    hold = next(n for n in row.notes if n.startswith("shipped default UNMEASURED"))
    assert "present none" in hold, hold  # names the empty POPULATION, not a verdict
    assert "never True" in hold, hold


def test_an_ARMED_deployment_surface_reads_ON(tmp_path, monkeypatch):
    """The positive pole: the tighter reading must not become unable to say True.

    Without this case a probe that could never report an armed deployment would pass
    every negative above — the mirror objection `#11444`'s MET-pole test exists for.
    """
    (tmp_path / ".env.example").write_text("TORTOISE_EMBED_GATE=1\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  tortoise:\n    environment:\n      TORTOISE_EMBED_GATE: \"1\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["deployment"]["present"] == ["docker-compose.yml"], shipped
    assert shipped["deployment"]["on"] is True, shipped
    assert shipped["deployment"]["silent"] == [], shipped
    note = _gate_note(shipped)
    assert "gate enabled by default: True" in note, note
    assert "every deployment file that builds the Tortoise process is armed" in note, note


def test_an_armed_compose_file_does_not_stand_for_the_IMAGE_build_files(
        tmp_path, monkeypatch):
    """`Dockerfile` is a CLASS, and every variant is a deployment input of its own.

    Fresh review of PR #12016 (P2), measured on THIS tree: the image build files are
    `Dockerfile.hosted` (`deploy-hosted.yml` builds it) and `Dockerfile.selfhost`
    (`docker-compose.yml`'s `build.dockerfile`) — there is no plain `Dockerfile`, so
    an exact-filename walk read NEITHER and reported the surface `ABSENT`. That left
    the same false PASS one surface-pair over: once `#11086` arms the compose file,
    `deployment["on"]` would read `True` off it while the hosted image ships the gate
    off, and the note's "every existing deployment surface is armed" would tell a
    reader the population was complete when two of its members were never walked.

    So the deployment reading is per FILE (compose files and every `Dockerfile*`),
    and one armed compose file cannot stand in for a silent image build.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  t:\n    environment:\n      TORTOISE_EMBED_GATE: \"1\"\n",
        encoding="utf-8")
    (tmp_path / "Dockerfile.hosted").write_text(
        "FROM python:3.12\nENV HF_HOME=/app\n", encoding="utf-8")
    (tmp_path / "Dockerfile.selfhost").write_text(
        "FROM python:3.12\nENV TORTOISE_DB_PATH=/data\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["Dockerfile"]["state"] == "OFF", shipped
    assert shipped["per_surface"]["Dockerfile"]["searched"] == [
        "Dockerfile.hosted", "Dockerfile.selfhost"], shipped
    assert shipped["deployment"]["present"] == [
        "docker-compose.yml", "Dockerfile.hosted", "Dockerfile.selfhost"], shipped
    assert shipped["deployment"]["armed"] == ["docker-compose.yml"], shipped
    assert shipped["deployment"]["on"] is False, shipped
    note = _gate_note(shipped)
    assert "gate enabled by default: False" in note, note
    assert "Dockerfile.hosted=OFF" in note and "Dockerfile.selfhost=OFF" in note, note


def test_the_row_holds_at_PARTIAL_because_the_deployment_is_unarmed(
        tmp_path, monkeypatch):
    """The VERDICT, not just the note: an unarmed deployment surface holds row 1.

    `#11455` exists because the row reached `MET` on one armed template while the
    compose path shipped the gate off; the row's own hold note must name that path.
    ⛔ WHAT MOVES ON THIS TREE IS THE CLAUSE, NOT THE VERDICT (fresh review, P3,
    measured): row 1 is `PARTIAL` on BOTH sides of this change, and only
    `gate enabled by default` moves `True` → `False`, which is the field `#11086`
    item 4 is ticked on. An earlier draft of this docstring said the row
    "moves `MET` → `PARTIAL`", which misleads the other way too: arming
    `docker-compose.yml` ALONE does not flip it back, because the two `Dockerfile*`
    files stay silent (measured at this head). The VERDICT flip is pinned on the
    divergent fixture in
    `test_the_row_VERDICT_reads_the_DEPLOYMENT_not_the_any_surface_bool`.
    ⛔ NO attribution of WHICH writer held the row at `origin/main` is stated here,
    on purpose. Two review rounds each caught a wrong one — the first draft named
    `session_indexer::main`, which the register declares as a `recognised_producer`;
    the second named the mechanism's history, which is itself a claim that rots.
    This test does not read that writer, so it does not claim it: the shape is
    pinned where it is measured, in `test_the_LIVE_register_credits_every_writer…`
    and the census tests.

    This fixture is also WHY the verdict cannot move here: `_row_one_fixture` arms
    `docker-compose.yml` and deliberately no `.env.example`, so overwriting the
    compose file with `services: {}` leaves the tree with no armed surface under
    EITHER clause — the hold this test asserts is the deployment one, and it says so.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    _no_register(monkeypatch)
    monkeypatch.setattr(probe, "embedding_writers", lambda: _gated_writer("tortoise.a::_w"))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    hold = next(n for n in row.notes if n.startswith("shipped default NOT armed"))
    assert "docker-compose.yml" in hold, hold
    # ⛔ The hold must not state a DEPLOYMENT consequence it did not measure (fresh
    # review of PR #12016, P2): a silent compose file that BUILDS from an armed image
    # build file inherits that image's `ENV`, so "a deployment built from them runs
    # with the rule off" was false for exactly the shape below — the same
    # per-file-vs-merged-view conflation the note builder above had already been
    # corrected for. Both notes now qualify it.
    assert "read from that file ALONE the deployment is unarmed" in hold, hold
    assert "INHERITS that image's `ENV`" in hold, hold

    # The latent shape, executed: the compose file is silent while a DEPLOYMENT file
    # (`Dockerfile.selfhost`) arms the flag — the two facts the qualification exists
    # for. ⛔ The `build:` edge itself is MEASURED now (`deployment["builds_from"]`,
    # `#12022`/`#12070` item 3): the synthetic compose file here declares no
    # `build.dockerfile`, so the caveat says `measured build edge(s): none` instead
    # of asserting an inheritance this tree's fixture cannot express. A fixture that
    # DOES declare the edge is pinned in
    # `test_the_per_file_caveat_names_the_MEASURED_build_edge`.
    (tmp_path / "Dockerfile.selfhost").write_text(
        'FROM python:3.12-slim\nENV TORTOISE_EMBED_GATE="1"\n', encoding="utf-8")
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["deployment"]["on"] is False, shipped
    assert shipped["deployment"]["armed"] == ["Dockerfile.selfhost"], shipped
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    hold = next(n for n in row.notes if n.startswith("shipped default NOT armed"))
    assert "read from that file ALONE the deployment is unarmed" in hold, hold
    # ⛔ And the SIBLING note carries the same defect class (round-13 review, P3): the
    # gate note led with "a deployment built from that file ALONE runs with the gate
    # OFF" and retracted it a sentence later — and this shape makes it reachable, not
    # hypothetical: `docker-compose.yml` really sets
    # `build.dockerfile: Dockerfile.selfhost`, so a compose deployment can carry the
    # image's `ENV`. The note must state the PER-FILE fact and name the merge as its
    # scope, never assert the deployment consequence it then takes back.
    gate_note = probe.shipped_default_note(
        probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE"),
        "TORTOISE_EMBED_GATE", False)
    assert "read from that file ALONE the gate is off" in gate_note, gate_note
    assert "INHERITS that image's `ENV`" in gate_note, gate_note
    assert "a Tortoise deployment built from that file" not in gate_note, gate_note
    # ⛔ And the OTHER half of the same conflation (round-11 review, P2): with an armed
    # surface that IS a deployment file, the note must NOT claim that surface "does
    # not deploy the container's environment on their own" — a build using it carries
    # the flag. The clause is scoped to templates; here it states the opposite fact.
    assert "do not deploy the container's environment on their own" not in hold, hold
    # ⛔ The arm-1 wording, pinned by a phrase ONLY it produces. The old pin
    # (`"themselves deployment files" in hold`) could not discriminate, because the
    # old noun made the sentence a tautology — every rewording kept the pin green, so
    # reverting to the over-claiming form went unnoticed (fresh review of PR #12016,
    # P3). The negative arm is what stops that noun returning.
    assert "the flag is armed in deployment file(s) (Dockerfile.selfhost)" in hold, hold
    assert "the surface(s) that do arm it" not in hold, hold
    # ⛔ …and the clause must name the armed VARIANT, not the surface ROOT (fresh
    # review of PR #12016, P3). The population this clause speaks about is per FILE,
    # so interpolating the root intersection published "(Dockerfile) … a build that
    # uses one of them carries the flag" while the walk had armed exactly one
    # variant — a class claim over a per-file measurement, which is the R7/R10
    # false-PASS this PR removes. `(Dockerfile)` cannot be a substring of
    # `(Dockerfile.selfhost)`, so the negative arm is exact rather than fuzzy.
    assert "(Dockerfile.selfhost)" in hold, hold
    assert "(Dockerfile)" not in hold, hold

    # ⛔ And no clause on THIS shape may scope the reading to `shipped["armed"]` — the
    # ANY-SURFACE armed list is the TEMPLATE here, while this note's subject is the
    # DEPLOYMENT population (`silent` = ['docker-compose.yml'], `armed` =
    # ['Dockerfile.selfhost']), which the note names correctly four words away. That
    # mix is the P2 this round removes, so pin it OUT on the shape that prints both.
    shipped_default = _gate_note(shipped)
    assert "UNARMED deployment file(s) ['docker-compose.yml']" in shipped_default, shipped_default
    assert "Dockerfile.selfhost=ON" in shipped_default, shipped_default
    assert "SCOPED to the surface(s) that arm it" not in shipped_default, shipped_default


#: ── `#12022`: the deployment channels are DISCOVERED, not a path list ─────────
def test_fly_toml_ENV_table_arms_the_deployment_and_build_args_do_not(
        tmp_path, monkeypatch):
    """`fly.toml`'s `[env]` is a deployed process's environment; `[build.args]` is not.

    `#12022`: the walk did not read `fly.toml` at all, so a `[env]` entry that the Fly
    machine's process really gets was outside the population the row note called
    "every existing deployment file". ⛔ The table SCOPING is the load-bearing half:
    a line scan reads `[build.args]` exactly like `[env]` (both are `KEY = "value"`),
    and a build argument never reaches the process — so the wrong reader here would
    be a false PASS of the same class, one format over.
    """
    (tmp_path / "fly.toml").write_text(
        '[build.args]\n  TORTOISE_EMBED_GATE = "1"\n\n'
        '[env]\n  TORTOISE_LOOP_STALL_HARD_EXIT_S = "600"\n',
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["fly.toml"]["state"] == "OFF", shipped["per_surface"]
    assert shipped["deployment"]["on"] is False, shipped["deployment"]
    # …and the ignored assignment is NAMED, never silently dropped: a reader has to
    # be able to see that the walk found the line and decided it is not runtime env.
    assert shipped["outside_runtime"], shipped
    assert shipped["outside_runtime"][0].startswith("fly.toml:2:"), shipped["outside_runtime"]

    # The SAME line inside `[env]` does arm it — so the OFF above is the table, not
    # an inability to read TOML.
    (tmp_path / "fly.toml").write_text(
        '[env]\n  TORTOISE_EMBED_GATE = "1"\n', encoding="utf-8")
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["fly.toml"]["state"] == "ON", shipped["per_surface"]
    assert shipped["deployment"]["on"] is True, shipped["deployment"]
    note = _gate_note(shipped)
    assert "fly.toml=ON(toml:[env])" in note, note


def test_the_fly_manifest_is_DISCOVERED_by_platform_name_not_a_fixed_path(
        tmp_path, monkeypatch):
    """A second Fly manifest is walked without editing a list (`#12022`).

    A fixed `("fly.toml",)` entry is how the channel was missed in the first place;
    Fly's own convention is `fly.toml` / `fly.<app>.toml`, so the glob is the
    discovery and a `fly.staging.toml` that arms the flag must move the reading.
    """
    (tmp_path / "fly.toml").write_text("[env]\n  FOO = \"1\"\n", encoding="utf-8")
    (tmp_path / "fly.staging.toml").write_text(
        '[env]\n  TORTOISE_EMBED_GATE = "1"\n', encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["fly.toml"]["searched"] == [
        "fly.staging.toml", "fly.toml"], shipped["per_surface"]["fly.toml"]
    assert shipped["deployment"]["files"] == {
        "fly.staging.toml": "ON", "fly.toml": "OFF"}, shipped["deployment"]


def test_the_entrypoint_is_DISCOVERED_from_the_image_and_its_exports_are_read(
        tmp_path, monkeypatch):
    """`entrypoint.sh` is found through `ENTRYPOINT`, and its `export` is a setting.

    `#12022`: the image RUNS this script, so an `export FLAG=1` in it arms every
    deployment the image runs — and the walk never read it. Two facts are pinned:
    the script is discovered from the manifest (no `entrypoint.sh` literal here), and
    `export VAR="${VAR:-default}"` is read as the DEFAULT it is, not as an
    unresolvable token that silently reads OFF.
    """
    (tmp_path / "Dockerfile.hosted").write_text(
        'FROM python:3.12\nCOPY entrypoint.sh /entrypoint.sh\n'
        'ENTRYPOINT ["/entrypoint.sh"]\nCMD ["uvicorn", "app:app"]\n',
        encoding="utf-8")
    (tmp_path / "entrypoint.sh").write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        'export HF_HOME="${HF_HOME:-/app/model}"\n',
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert [p.name for p in probe._entrypoint_scripts()] == ["entrypoint.sh"]
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["entrypoint"]["searched"] == ["entrypoint.sh"], shipped
    assert shipped["deployment"]["on"] is False, shipped["deployment"]

    (tmp_path / "entrypoint.sh").write_text(
        "#!/bin/bash\n"
        'export TORTOISE_EMBED_GATE="${TORTOISE_EMBED_GATE:-1}"\n',
        encoding="utf-8")
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["entrypoint"]["state"] == "ON", shipped["per_surface"]
    # ⛔ The row still holds, and the reason is NAMED: the IMAGE BUILD file this tree
    # ships declares no gate of its own, so `deployment["on"]` is `False` even though
    # the entrypoint arms it — one channel arming is not every channel arming.
    assert shipped["deployment"]["armed"] == ["entrypoint.sh"], shipped["deployment"]
    assert shipped["deployment"]["silent"] == ["Dockerfile.hosted"], shipped["deployment"]
    assert shipped["deployment"]["on"] is False, shipped["deployment"]
    note = _gate_note(shipped)
    assert "entrypoint.sh=ON(shell:export)" in note, note


def test_an_unexported_shell_assignment_is_not_a_shipped_environment_entry(
        tmp_path, monkeypatch):
    """A shell VARIABLE is not the process environment; only `export` reaches Python.

    The entrypoint's own idiom elsewhere is `export NAME` as a separate statement, so
    both spellings have to be understood: `export FLAG=1` and `FLAG=1` + `export FLAG`
    arm it, while a bare `FLAG=1` that is never exported does NOT — reading it as a
    shipped default would be a false PASS, and reading an EXPORTED one as silence
    would be the false HOLD this walk exists to avoid.
    """
    (tmp_path / "Dockerfile.hosted").write_text(
        'FROM python:3.12\nENTRYPOINT ["/entrypoint.sh"]\n', encoding="utf-8")
    (tmp_path / "entrypoint.sh").write_text(
        "#!/bin/bash\nTORTOISE_EMBED_GATE=1\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["entrypoint"]["state"] == "OFF", shipped["per_surface"]
    assert any(hit.startswith("entrypoint.sh:2:") for hit in shipped["outside_runtime"]), \
        shipped["outside_runtime"]

    # …exported by a LATER statement, which is `entrypoint.sh`'s own house style.
    (tmp_path / "entrypoint.sh").write_text(
        "#!/bin/bash\nTORTOISE_EMBED_GATE=1\nexport TORTOISE_EMBED_GATE\n",
        encoding="utf-8")
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["entrypoint"]["state"] == "ON", shipped["per_surface"]


def test_a_shell_indirection_is_UNMEASURED_never_silent(tmp_path, monkeypatch):
    """A value the walk cannot resolve is UNMEASURED — the unreadable-file doctrine.

    `export FLAG="${OTHER}"` may arm the flag; reading it as OFF would publish "this
    file declares nothing" over a line that may declare it (the fail-open direction).
    Reading it as ON would be a measurement never made. UNMEASURED is the only
    honest state, and it holds the row rather than passing it.
    """
    (tmp_path / "Dockerfile.hosted").write_text(
        'FROM python:3.12\nENTRYPOINT ["/entrypoint.sh"]\n', encoding="utf-8")
    (tmp_path / "entrypoint.sh").write_text(
        '#!/bin/bash\nexport TORTOISE_EMBED_GATE="${SOME_OTHER_FLAG}"\n',
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["per_surface"]["entrypoint"]["state"] == "UNMEASURED", shipped
    assert shipped["deployment"]["files"] == {
        "Dockerfile.hosted": "OFF", "entrypoint.sh": "UNMEASURED"}, shipped
    assert shipped["deployment"]["measured"] is False, shipped["deployment"]
    assert shipped["deployment"]["on"] is False, shipped["deployment"]
    assert shipped["indeterminate"], shipped
    note = _gate_note(shipped)
    assert "gate enabled by default: UNMEASURED" in note, note
    assert "indeterminate assignments: [" in note, note


def test_a_deployment_channel_that_arms_NOTHING_holds_row_one_and_is_NAMED(
        tmp_path, monkeypatch):
    """`#12022`'s falsifier: an armed compose file must not cover a silent channel.

    The shape `#12022` names as the false PASS: `docker-compose.yml` is armed (so the
    old population reads `True`) while `fly.toml`'s `[env]` and the entrypoint say
    nothing — and `fly.toml` is what the hosted deployment actually respects. The row
    must HOLD, and the output must name every channel it walked, so a reader can see
    the population rather than trusting a phrase.
    """
    _row_one_fixture(tmp_path, monkeypatch)          # docker-compose.yml armed
    (tmp_path / "fly.toml").write_text(
        '[env]\n  TORTOISE_LOOP_STALL_HARD_EXIT_S = "600"\n', encoding="utf-8")
    (tmp_path / "Dockerfile.hosted").write_text(
        'FROM python:3.12\nENTRYPOINT ["/entrypoint.sh"]\n', encoding="utf-8")
    (tmp_path / "entrypoint.sh").write_text(
        "#!/bin/bash\nexport HF_HOME=\"/app/model\"\n", encoding="utf-8")
    _no_register(monkeypatch)
    monkeypatch.setattr(probe, "embedding_writers", lambda: _gated_writer("tortoise.a::_w"))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    assert "UNARMED deployment file(s)" in row.observed, row.observed
    hold = next(n for n in row.notes if n.startswith("shipped default NOT armed"))
    assert "fly.toml" in hold, hold
    assert "entrypoint.sh" in hold, hold
    note = next(n for n in row.notes if n.startswith("gate enabled by default:"))
    # The CHANNELS are named, not just the filenames: a reader can tell that a TOML
    # `[env]` table and a shell `export` were actually read (#12022).
    assert "fly.toml=OFF(toml:[env])" in note, note
    assert "entrypoint.sh=OFF(shell:export)" in note, note
    assert "docker-compose.yml=ON(compose)" in note, note


def test_the_per_file_caveat_is_ONE_string_BOTH_notes_call(tmp_path, monkeypatch):
    """`#12054`: one inference, one string — the hold note and the gate note agree.

    The measured cost: the gate note was corrected to a per-file reading while the
    hold note 500 lines below still said a deployment built from the silent files
    "runs with the rule off" — two opposite standards for one inference in one report.
    The pin is structural, not a phrase list: BOTH notes must carry the SAME helper's
    output, so reverting either one to its own wording reds here.
    """
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "fly.toml").write_text("[env]\n  FOO = \"1\"\n", encoding="utf-8")
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    caveat = probe.per_file_scope_note(shipped["deployment"])
    # The caveat itself states the per-file fact and the inheritance, and asserts NO
    # deployment consequence it then has to take back (`#12070` item 4).
    assert "this reading is PER FILE" in caveat, caveat
    assert "INHERITS that image's `ENV`" in caveat, caveat
    assert "runs with the rule off" not in caveat, caveat
    gate_note = _gate_note(shipped)
    assert caveat in gate_note, gate_note
    _no_register(monkeypatch)
    monkeypatch.setattr(probe, "embedding_writers", lambda: _gated_writer("tortoise.a::_w"))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    hold = next(n for n in row.notes if n.startswith("shipped default NOT armed"))
    assert caveat in hold, hold
    assert "runs with the rule off" not in hold, hold


def test_the_per_file_caveat_names_the_MEASURED_build_edge(tmp_path, monkeypatch):
    """`#12070` item 3: the compose→image edge is READ, so the caveat can name it.

    The caveat's whole point is that a silent compose file can still yield an armed
    deployment by INHERITING an armed image. Nothing read the `build:` stanza, so the
    qualification named an edge the walk had never checked on the tree it described —
    and a fixture writing `services: {}` could not express it at all. Both are fixed
    by reading `build:`/`dockerfile:` and reporting the edge.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  tortoise:\n    build:\n      context: .\n"
        "      dockerfile: Dockerfile.selfhost\n", encoding="utf-8")
    (tmp_path / "Dockerfile.selfhost").write_text(
        'FROM python:3.12\nENV TORTOISE_EMBED_GATE="1"\n', encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["deployment"]["builds_from"] == {
        "docker-compose.yml": "Dockerfile.selfhost"}, shipped["deployment"]
    # The per-FILE reading still holds the row: the compose file itself declares
    # nothing, and the walk does not model the merge (`#12022` keeps that boundary).
    assert shipped["deployment"]["files"] == {
        "docker-compose.yml": "OFF", "Dockerfile.selfhost": "ON"}, shipped
    assert shipped["deployment"]["on"] is False, shipped["deployment"]
    caveat = probe.per_file_scope_note(shipped["deployment"])
    assert "docker-compose.yml BUILDS from Dockerfile.selfhost" in caveat, caveat



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

    _no_register(monkeypatch)
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

    _no_register(monkeypatch)
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

    _no_register(monkeypatch)
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

    _no_register(monkeypatch)
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
    # ⛔ `#11492`: row 2 now resolves an unresolved writer from the PRODUCT register, and
    # `_compare_views` IS declared there (no live vector) — so without this the injected
    # writer is resolved and the census note this test is about never prints. The register
    # is neutralised for the same reason the file's `_no_register` exists: a test that
    # injects a tiny writer map must not silently inherit this tree's declarations.
    _no_register(monkeypatch)
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
    The note now names the PROPERTY the population is keyed on.

    ⛔ AND IT MAY NOT NAME ONE LABEL FOR A POPULATION THAT CARRIES TWO
    (`#11495`'s review, measured). `unresolved_writers` is
    `kind == "unresolved_write" and fn in unknown`, and `scored_labels()` puts a
    writer declared `labels-at-runtime` into `unknown` with `AT_RUNTIME_LABEL`. The
    header said `STILL labelled UNKNOWN(unresolved producer)`, and on the LIVE tree
    BOTH of its two subjects carried `UNKNOWN(labels decided at runtime)` — so the
    row's own published evidence contradicted the runtime note four cells to its
    left. The old fixture carried only `UNKNOWN(unresolved producer)`, which is why
    it could not see the fold; this one carries one of each.
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
        # ⛔ THE FOLD: an `unresolved_write` writer the register declares
        # `labels-at-runtime`, so its census label is `AT_RUNTIME_LABEL` — a census
        # subject that does NOT carry `UNKNOWN(unresolved producer)`.
        **_unresolved_writer("tortoise.y::_at_runtime_store"),
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    # ⛔ The register is REPLACED, not merely neutralised: `_compare_views` IS declared in
    # the live register, so row 2 would resolve it and shrink the population this test is
    # about — and the at-runtime subject needs a declaration or it is just an unresolved one.
    monkeypatch.setattr(
        probe, "declared_writers",
        lambda: {"tortoise.y::_at_runtime_store": _store_declared(
            "tortoise.y::_at_runtime_store", LABELS_AT_RUNTIME)})
    row = probe.score_path_bullet()
    census = [n for n in row.notes if n.startswith("#10541 census")]
    assert len(census) == 1, row.notes
    # It states how many of the UNKNOWN-labelled writers it actually counted: the two
    # `unresolved_write` subjects, out of all three UNKNOWN-labelled writers (the
    # `duplicate` identity is never a census subject, which is what `N of M` exposes).
    assert "2 of 3 UNKNOWN-labelled" in census[0], census[0]
    # ...and names the population by the PROPERTY, not by a label it may not carry.
    # `#11492`: the header says STILL — row 2 resolves writers from the product register, so
    # the census covers the ones whose label neither the derivation NOR a declaration resolved.
    assert "writers whose label is STILL UNKNOWN" in census[0], census[0]
    assert "PRODUCERS" not in census[0], (
        "the note claims a population the same line contradicts: `exempt` is "
        f"defined as NOT being a producer of a stored vector: {census[0]}")
    # ⛔ THE PIN FOR WHICH THIS FIXTURE EXISTS: the population carries TWO labels, so a
    # header naming one of them is false about the other — which is exactly what shipped.
    assert "UNKNOWN(unresolved producer)" not in census[0], (
        "the census header names ONE label while its population carries two: the "
        f"`labels-at-runtime` subject carries {probe.AT_RUNTIME_LABEL!r}: {census[0]}")


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
    """A shipped-config reading in the SHAPE `flag_in_shipped_configs` returns.

    ⛔ `#11455` moved row 1 onto `deployment["on"]` — the every-existing-deployment-
    surface reading — so this fake MUST carry a `deployment` block. Without it the
    row-1 tests would be measuring a `KeyError` (or a `shipped["on"]` fallback this
    change exists to remove), not the clause they name.

    `Dockerfile` is the deployment surface the fake ships: the same name the
    pre-`#11455` fake used, now with the state that makes the reading real.
    """
    state = "ON" if on else "OFF"
    return {
        "on": on,
        "surfaces": ["Dockerfile"],
        "per_surface": {"Dockerfile": {"state": state, "hits": [],
                                       "files": {"Dockerfile": {"state": state, "hits": []}},
                                       "searched": ["Dockerfile"], "unreadable": []}},
        "armed": ["Dockerfile"] if on else [],
        "silent": [] if on else ["Dockerfile"],
        "absent": [],
        "unmeasured": [],
        "deployment": {
            "surfaces": ["Dockerfile"],
            "files": {"Dockerfile": state},
            "present": ["Dockerfile"],
            "armed": ["Dockerfile"] if on else [],
            "silent": [] if on else ["Dockerfile"],
            "absent": [],
            "unmeasured": [],
            "measured": True,
            "on": on,
        },
        "searched": ["Dockerfile"],
        "unreadable": [],
        "mentions": [],
        "files": ["Dockerfile"] if on else [],
        "documented_only": 0,
    }


def _gate_note(shipped: dict) -> str:
    """Row 1's shipped-default note, built from a shipped reading.

    Calls the tool's OWN note builder (the function the row calls), so the tests
    below pin the text a reader sees rather than a copy of it — and they can do it
    on a root holding only config surfaces, without standing up row 1's whole rule
    tree and writer population.
    """
    return probe.shipped_default_note(shipped, "TORTOISE_EMBED_GATE", False)


def _gated_writer(name: str) -> dict:
    return {name: {"callee": "embed_text", "at": f"{name.split('::')[0].replace('.', '/')}.py:1",
                   "kind": "gated", "label": "Point"}}


#: ── `#11444`: the product register, as a row-1 input ────────────────
def _no_register(monkeypatch) -> None:
    """Neutralise the product register so a row-1 test measures ONE clause.

    ⛔ `#11444` made row 1 READ `tortoise/embed_writer_register.py`. A test that
    injects a tiny writer map and no register would otherwise see all twenty of
    this tree's real declarations as STALE (the injected population is not the
    tree's), and pass or fail for a reason it never names. Tests that ARE about
    the register inject it explicitly — so the closure is explicit in both
    directions rather than inherited from whichever fixture ran first.
    """
    monkeypatch.setattr(probe, "declared_writers", lambda: {})


def _declared(name: str, cls: str = probe.CENSUS_EXEMPT, *,
              reason: str | None = None, owner: str = "#11085") -> dict:
    """One register entry, in the shape `declared_writers()` reads.

    The `reason` cites the writer ITSELF by default, because the probe credits a
    declaration only when that citation re-derives (`declaration_violations`).
    """
    return {"cls": cls, "owner_issue": owner,
            "reason": reason if reason is not None else f"`{name}` for this test"}


def _unresolved_writer(name: str, *callees: str) -> dict:
    """A writer discovery finds OUTSIDE the gate, with its own callee names.

    ``callees`` is what a `recognised_producer` declaration is CORROBORATED
    against — a route claim the body does not make is not credited.
    """
    return {name: {"callee": "unrecognised",
                   "at": f"{name.replace('::', '.py:')}",
                   "kind": "unresolved_write",
                   "label": "UNKNOWN(unresolved producer)",
                   "callees": frozenset(callees)}}


def test_row_one_is_PARTIAL_when_the_gate_is_off_although_every_writer_is_gated(
        tmp_path, monkeypatch):
    """The flag is the operator's; a fully-gated population with the gate OFF is
    still not the write path's operative rule. `MET` here would publish
    enforcement the shipped config does not set."""
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    _no_register(monkeypatch)
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
    _no_register(monkeypatch)
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
    _no_register(monkeypatch)
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

    ⛔ `#11444` narrowed the clause to the form reading (b) can keep: an
    outside-gate writer holds the row **while the product does not name it**.
    The injected writer here is UNDECLARED on purpose (the register is empty),
    so this falsifier keeps its teeth; the DECLARED direction — the same writer,
    named — is `test_naming_a_LIVE_undeclared_writer_is_the_acceptance_reading_b_describes`
    below. Dropping `undeclared` from the verdict reds this test.
    """
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    _no_register(monkeypatch)
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
    before this test existed.

    ⛔ `#11444`: the clause is `undeclared` now — an unresolved writer the
    PRODUCT names is the accepted exception reading (b) describes, and it holds
    this row only while nothing names it. The injected name is undeclared on
    purpose (the register is empty).
    """
    _fake_rule_tree(tmp_path)
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    _no_register(monkeypatch)
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
    _no_register(monkeypatch)
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
    _no_register(monkeypatch)
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
    _no_register(monkeypatch)
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
    _no_register(monkeypatch)
    monkeypatch.setattr(probe, "embedding_writers", lambda: {
        **_gated_writer("tortoise.a::_w"),
        "tortoise.__init__::": _writer("tortoise.__init__::", "duplicate"),
    })
    monkeypatch.setattr(probe, "flag_in_shipped_configs", lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"a duplicate identity must hold: {row.verdict!r}"


# ── #11444: row 1 reads the PRODUCT's declared writer register ───────────────


def _registered_population(monkeypatch, declarations, writers) -> None:
    """Inject BOTH halves row 1 now reads: the discovered population and the register."""
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    monkeypatch.setattr(probe, "declared_writers", lambda: declarations)
    monkeypatch.setattr(probe, "flag_in_shipped_configs",
                        lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)


def _row_one_fixture(tmp_path, monkeypatch):
    """A tree where the rule holds and the gate is shipped ON, so only the WRITER
    clauses of row 1 can decide the verdict.

    ⛔ `#11455`: the flag must be armed in a DEPLOYMENT surface, not only in a
    template — row 1 reads the deployment subset, so an `.env.example` here would
    (correctly) leave the row at `PARTIAL` and every writer-clause test below would
    then pass for a reason it never names. `docker-compose.yml` is the file this
    repo's own deployment path uses, so the fixture arms that one.
    """
    _fake_rule_tree(tmp_path)
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  tortoise:\n    environment:\n"
        '      TORTOISE_EMBED_GATE: "1"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)


def test_row_one_reads_MET_when_every_writer_routes_or_is_named(
        tmp_path, monkeypatch):
    """`#11444` target: reading (b) makes `MET` REACHABLE without weakening anything.

    Reading (b), recorded on `#11085`: *"Every embedding writer must route through
    the gate or be a named, accepted exception in the product."* Here one writer
    reaches the gated seam and the other is NAMED — with its route corroborated at
    its own seam — so every writer is classified and the row is `MET`.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {
        **_gated_writer("tortoise.a::_gated"),
        **_unresolved_writer("tortoise.b::_routes", "set_embedding_clause"),
    }
    declarations = {
        "tortoise.b::_routes": _declared(
            "tortoise.b::_routes", probe.CENSUS_RECOGNISED,
            reason="`tortoise.b::_routes` reaches `set_embedding_clause`"),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "MET", f"{row.verdict}: {row.observed}"
    assert "1 of 1 outside-gate writers" in row.observed, row.observed


def test_row_one_goes_back_to_PARTIAL_when_ONE_declaration_is_removed(
        tmp_path, monkeypatch):
    """The MUTATION the issue names, executed: remove ONE name, nothing else.

    ⛔ This is the falsifier `#11444`'s body asks for — *"row 1 reads `PARTIAL` again
    the moment one writer is removed from the register"* — and it is written as a
    mutation rather than as two fixtures so the ONLY difference between the `MET`
    read and the `PARTIAL` read is the removed declaration. Deleting the
    `undeclared` clause from the verdict, or crediting a writer discovery does not
    find outside the gate, reds HERE.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {
        **_gated_writer("tortoise.a::_gated"),
        **_unresolved_writer("tortoise.b::_routes", "set_embedding_clause"),
        **_unresolved_writer("tortoise.c::_named"),
    }
    declarations = {
        "tortoise.b::_routes": _declared(
            "tortoise.b::_routes", probe.CENSUS_RECOGNISED,
            reason="`tortoise.b::_routes` reaches `set_embedding_clause`"),
        "tortoise.c::_named": _declared("tortoise.c::_named"),
    }
    _registered_population(monkeypatch, declarations, writers)
    assert probe.score_rule_bullet().verdict == "MET", (
        "the un-mutated population must be MET, or the mutation below measures "
        "something other than the declaration it removes")

    # ── the mutation: exactly one declaration removed, product unchanged ──
    trimmed = {k: v for k, v in declarations.items() if k != "tortoise.c::_named"}
    monkeypatch.setattr(probe, "declared_writers", lambda: trimmed)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", (
        f"removing one declaration must hold the row: {row.verdict!r} {row.observed}")
    undeclared = next(n for n in row.notes if n.startswith("undeclared writers"))
    assert "tortoise.c::_named" in undeclared, undeclared


def test_a_bypass_declaration_holds_row_one_at_PARTIAL(tmp_path, monkeypatch):
    """A `bypass` is a DEFECT, never an exemption — and the instrument NAMES it.

    `#11085`'s `load()` refuses to BUILD a register containing one (``bypass`` is not
    in ``CLASSES``), so this can only arrive from a tampered or injected register.
    The row must not assume the register is well-formed: it holds, and it says what
    it found. Same rule the census already applies to its own third class.

    ⛔ Both halves of the refusal are asserted. The class check lives in
    `declaration_violations` AND the verdict has a separate `bypass_declarations`
    term, so removing either one alone would still hold the row — which is exactly
    how one half could be deleted by a refactor with nothing reddening.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.b::_bypass")}
    declarations = {
        "tortoise.b::_bypass": _declared(
            "tortoise.b::_bypass", probe.CENSUS_BYPASS),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"a bypass must hold: {row.verdict!r}"
    assert "REAL BYPASSES" in row.observed, row.observed
    assert "tortoise.b::_bypass" in next(
        n for n in row.notes if n.startswith("REAL BYPASSES")), row.observed
    assert "declares `bypass`" in row.observed, (
        "the class check in `declaration_violations` must also refuse it — the verdict "
        f"term alone leaves one half unpinned: {row.observed}")


def test_a_bypass_on_a_GATED_writer_also_holds_row_one_at_PARTIAL(
        tmp_path, monkeypatch):
    """A `bypass` parked on a GATED writer is still a DEFECT — and BOTH holds catch it.

    ⛔ `#11473` round-1 review: measured by mutation, then corrected by a fresh-context
    reviewer that caught this docstring claiming more than the run showed.

    The sibling test above parks its `bypass` on an *unresolved* (outside-gate) writer,
    and its two holds are `violations` + `bypass_declarations` (its own docstring says
    so). A `bypass` on a GATED writer leaves the SCORED population entirely —
    `violations` is computed over `set(declared) & set(outside_gate)`, so it has nothing
    to look at there — which changes WHICH terms hold the row:

      * `bypass_declarations` is built over EVERY declaration (``declared.items()``),
        not over ``declared ∩ outside_gate``;
      * ``stale`` is ``set(declared) - set(outside_gate)``, and its note names this
        exact case ("the writer now ROUTES THROUGH THE GATE").

    Either term ALONE still holds the row at PARTIAL; only narrowing BOTH greens it —
    which is why this test asserts both notes and not just the verdict. Both are a
    natural-looking simplification in a function otherwise entirely about the
    outside-gate population, and neither was pinned for this case.

    Measured with both narrowed for the `bypass` class only (the surgical form — a
    broader ``stale = declared & outside_gate`` also reds
    `test_a_STALE_declaration_holds_row_one_at_PARTIAL`, so it does not isolate this
    gap): WITHOUT this arm — on the pre-arm tree, i.e. with this file's §6 figure
    re-cut to the 150 it then collected — the whole file is green (**148 passed,
    2 skipped** on the `TORTOISE_TEST_CARVE_OUT=1` lane; the second skip is the
    URI-less live check, where §6's docker-lane figure has one) while this population
    reads `MET`; with it, exactly ONE test reds — this one
    (**1 failed, 148 passed, 2 skipped**).
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {
        **_gated_writer("tortoise.a::_gated"),
        **_unresolved_writer("tortoise.b::_routes", "set_embedding_clause"),
    }
    declarations = {
        # The `MET` population of `test_row_one_reads_MET_...`, plus a DEFECT parked
        # on the writer the gate already covers.
        "tortoise.a::_gated": _declared(
            "tortoise.a::_gated", probe.CENSUS_BYPASS),
        "tortoise.b::_routes": _declared(
            "tortoise.b::_routes", probe.CENSUS_RECOGNISED,
            reason="`tortoise.b::_routes` reaches `set_embedding_clause`"),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", (
        "a bypass on a GATED writer must hold the row: "
        f"{row.verdict!r} {row.observed}")
    assert "tortoise.a::_gated" in next(
        (n for n in row.notes if n.startswith("REAL BYPASSES")), ""), (
        "`bypass_declarations` must be built over EVERY declaration, not over the "
        f"outside-gate population: {row.observed}")
    assert "tortoise.a::_gated" in next(
        (n for n in row.notes if n.startswith("stale declarations")), ""), (
        "a gated writer's declaration is STALE (it now routes through the gate), so "
        f"`stale` must not be narrowed to the outside-gate population: {row.observed}")


def test_the_over_inclusive_enumeration_is_still_printed_when_everything_is_named(
        tmp_path, monkeypatch):
    """`#11444`: the register CLASSIFIES the population, it never REPLACES it (*What the fix must
    preserve*, third bullet — counted/classified, not replaced — and its third Verification item).

    With every writer credited — the `MET` state — the AST enumeration and the discovery
    location of each writer are still in the evidence. A change that swaps the enumeration for
    the register would green this row while making the measured population invisible, which is
    the failure mode the register exists to fix rather than to commit.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {
        **_gated_writer("tortoise.a::_gated"),
        **_unresolved_writer("tortoise.b::_routes", "set_embedding_clause"),
        **_unresolved_writer("tortoise.c::_named"),
    }
    declarations = {
        "tortoise.b::_routes": _declared(
            "tortoise.b::_routes", probe.CENSUS_RECOGNISED,
            reason="`tortoise.b::_routes` reaches `set_embedding_clause`"),
        "tortoise.c::_named": _declared("tortoise.c::_named"),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "MET", f"{row.verdict}: {row.observed}"
    enumeration = next(
        n for n in row.notes
        if n.startswith("functions writing an embedding with no recognised producer call:"))
    assert "deliberately OVER-INCLUSIVE" in enumeration, enumeration
    assert "tortoise.b::_routes @ tortoise.b.py:_routes" in enumeration, enumeration
    assert "tortoise.c::_named @ tortoise.c.py:_named" in enumeration, enumeration


def test_a_STALE_declaration_holds_row_one_at_PARTIAL(tmp_path, monkeypatch):
    """A name with **no writer discovery can see** behind it HOLDS the row.

    ⛔ This is the falsifier answer, stated exactly and not more broadly than the code
    supports: it makes adding a name for a writer **the product does not have**
    insufficient. On this tree every visible outside-gate writer is already named, so an
    added name can only be stale and a naming-only edit moves the row toward `PARTIAL`.
    Same contract ESLint's suppressions file enforces (*"There are suppressions left that do
    not occur anymore … re-run with `--prune-suppressions`"*, a non-zero exit).

    ⛔ What it does NOT claim (and an earlier draft of this docstring overclaimed): naming
    a LIVE writer that discovery finds outside the gate DOES green the row — that naming is
    the acceptance reading (b) asks for. That direction is pinned, on purpose, by
    `test_naming_a_LIVE_undeclared_writer_is_the_acceptance_reading_b_describes`.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.b::_named")}
    declarations = {
        "tortoise.b::_named": _declared("tortoise.b::_named"),
        # ── the added name: a writer the product does NOT have ──
        "tortoise.ghost::_nobody": _declared("tortoise.ghost::_nobody"),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", (
        f"a declaration with no live writer must hold the row, not green it: "
        f"{row.verdict!r} {row.observed}")
    stale = next(n for n in row.notes if n.startswith("stale declarations"))
    assert "tortoise.ghost::_nobody" in stale, stale


def test_naming_a_LIVE_undeclared_writer_is_the_acceptance_reading_b_describes(
        tmp_path, monkeypatch):
    """The OTHER direction, pinned rather than implied: naming a live writer DOES green it.

    Reading (b), recorded on `#11085`: *"Every embedding writer must route through the gate
    or be a named, accepted exception in the product."* A writer discovery finds outside the
    gate, carrying an `exempt` declaration with an owning issue, IS that exception — so the
    row credits it. This is not a hole this lane left open; it is the recorded decision's own
    consequence, and the reason `tortoise/embed_writer_register.py` is product code that needs
    review rather than a suppression file.

    ⛔ The alternative — refusing it — would reverse the decision, and would re-make row 1
    unreachable, which is the defect `#11444` exists to fix. Pinned so the honest limit is a
    measured fact instead of a sentence in a comment that the next lane reads as a promise.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.b::_accepted")}
    declarations = {
        "tortoise.b::_accepted": _declared(
            "tortoise.b::_accepted", probe.CENSUS_EXEMPT, owner="#10541"),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "MET", (
        f"a NAMED writer is reading (b)'s accepted exception: {row.verdict!r} {row.observed}")
    assert "1 of 1 outside-gate writers" in row.observed, row.observed


def test_a_path_spelled_declaration_KEY_is_one_writer_not_stale_plus_undeclared(
        tmp_path, monkeypatch):
    """The register admits two spellings for a citation; its KEYS are normalised the same way.

    ⛔ Measured before this: a key written as `tortoise/projection/__init__.py::_apply_one`
    produced `stale: [<that key>]` AND `undeclared: [tortoise.projection::_apply_one]` for the
    SAME live function — the row blamed the product for not declaring a writer it had just
    declared, and named two symptoms of one spelling difference. Normalising only the CITATION
    side (the first version) could not see it.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.projection::_apply_one")}
    declarations = {
        "tortoise/projection/__init__.py::_apply_one": _declared(
            "tortoise.projection::_apply_one"),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "MET", f"{row.verdict}: {row.observed}"
    assert "stale declarations" not in row.observed, row.observed
    assert "undeclared writers" not in row.observed, row.observed


def test_the_ROUTE_seams_are_the_register_OWN_exported_definition():
    """No second spelling of `recognised_producer`'s definition lives in this file.

    A copy drifts in the FALSE-HOLD direction: a seam added to the register would leave every
    correct declaration reading as uncorroborated, so the row would hold on a correct tree —
    the opposite of the defect `#11444` fixes. The instrument reads the declaration, so it
    reads this too.
    """
    from tortoise import embed_writer_register as register

    assert probe.route_calls() == tuple(register.RECOGNISED_PRODUCER_SEAMS)
    assert probe.route_calls(), "an empty seam set would corroborate every route claim"
    assert "set_embedding_clause" in probe.route_calls(), probe.route_calls()


def test_TWO_spellings_of_ONE_writer_are_refused_not_silently_merged():
    """`_normalise_declared_keys` refuses a collision, and refuses a non-`module::symbol`.

    ⛔ Both refusals are DOCUMENTED contracts with no other pin: the live register carries one
    spelling per writer, so a regression to "the later key silently wins" would leave every
    test green — the same hole the register's own `test_a_duplicate_key_raises` closes one
    normalisation earlier. The second half matters for the same reason `load()` refuses a bare
    function name: a key that is not `module::symbol` cannot be matched to a writer at all.
    """
    entry = {"cls": probe.CENSUS_EXEMPT, "owner_issue": "#1", "reason": "x"}
    with pytest.raises(ValueError) as two_spellings:
        probe._normalise_declared_keys({
            "tortoise.projection::_apply_one": entry,
            "tortoise/projection/__init__.py::_apply_one": entry,
        })
    assert "both name" in str(two_spellings.value), two_spellings.value
    with pytest.raises(ValueError) as not_a_key:
        probe._normalise_declared_keys({"tortoise.z": entry})
    assert "module::symbol" in str(not_a_key.value), not_a_key.value
    # The GREEN direction: one spelling normalises to itself and survives intact.
    assert probe._normalise_declared_keys({
        "tortoise/projection/__init__.py::_apply_one": entry,
    }) == {"tortoise.projection::_apply_one": entry}


def test_the_row_carries_its_OWN_scope_so_MET_is_not_a_closure_proof(
        tmp_path, monkeypatch):
    """`MET` means "gated or NAMED", never "no vector escapes the rule" — on the row itself.

    `tortoise/embed_writer_register.py` says a `gated` writer may still store a caller-supplied
    `embedding` VERBATIM (`create_point`/`update_point`, the recorded `#5004`/PR `#3018`
    decision) and that `#11086` "must not read it as a closure proof". A `MET` this instrument
    starts producing would be exactly where that gets lost, so the scope travels in the row's
    own evidence rather than only in a docstring.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.b::_named")}
    declarations = {"tortoise.b::_named": _declared("tortoise.b::_named")}
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "MET", f"{row.verdict}: {row.observed}"
    assert "measures the WRITER population, not every stored vector" in row.observed, (
        row.observed)
    assert "create_point" in row.observed and "update_point" in row.observed, row.observed


def test_a_malformed_entry_is_NAMED_not_a_crash(tmp_path, monkeypatch):
    """Register corruption becomes a named hold — never a traceback that kills all four rows.

    `main()`'s exit contract makes a traceback (exit 1) indistinguishable from "a row was NOT
    MET" (exit 1 is also not the documented `2`), and a crash aborts rows 2–4 as well. So the
    register boundary coerces and names: an inadmissible class is a VIOLATION for that writer
    (the row holds and says why), not an exception.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.b::_weird")}
    declarations = {
        "tortoise.b::_weird": _declared(
            "tortoise.b::_weird", ["not", "a", "class"]),  # type: ignore[arg-type]
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()  # must not raise
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    assert "outside" in row.observed, row.observed
    assert "uncorroborated declarations" in row.observed, row.observed


def test_a_declaration_with_NO_owning_issue_is_not_credited(tmp_path, monkeypatch):
    """Reading (b) says *"a named, accepted exception"* — the owning issue IS the acceptance.

    The register's `load()` enforces an `#<digits>` owner, but the instrument re-checks it rather than
    inheriting an invariant in a module it does not test: a tampered or injected entry with an
    empty owner used to be credited and printed `owners: <writer>=` with nothing after it.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.b::_unowned")}
    declarations = {
        "tortoise.b::_unowned": _declared(
            "tortoise.b::_unowned", probe.CENSUS_EXEMPT, owner=""),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    assert "carries no owning issue" in row.observed, row.observed


def test_a_seam_REMOVED_from_the_export_holds_the_declarations_that_need_it(
        tmp_path, monkeypatch):
    """The FALSE-HOLD direction: dropping a seam must HOLD, not silently stop corroborating.

    `route_calls()` reads the register's export, so a lane that trims the tuple changes what the
    probe can corroborate. This pin makes that consequence MEASURED — every declaration whose body
    reached only the dropped seam becomes uncorroborated and the row holds — instead of silent.
    It is the direction the register-suite test's `any(...)` check cannot see (a reason citing two
    seams stays green when one is dropped), which is why the behaviour is pinned here too.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.b::_routes", "set_embedding_clause")}
    declarations = {
        "tortoise.b::_routes": _declared(
            "tortoise.b::_routes", probe.CENSUS_RECOGNISED,
            reason="`tortoise.b::_routes` reaches `set_embedding_clause`"),
    }
    _registered_population(monkeypatch, declarations, writers)
    assert probe.score_rule_bullet().verdict == "MET"
    # ── the mutation: the seam this declaration relies on is no longer exported ──
    monkeypatch.setattr(probe, "route_calls", lambda: ("_session_embedding",))
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", (
        f"a declaration whose only route is not exported must HOLD: {row.verdict!r} {row.observed}")
    assert "claims a route to a recognised producer" in row.observed, row.observed


def test_a_declaration_for_a_GATED_writer_is_stale_and_says_WHY(tmp_path, monkeypatch):
    """A `gated` writer is outside the population a declaration is OWED for — and the note says so.

    ⛔ Measured before this: declaring `tortoise.sdk::create_point` produced a `stale` note listing
    only "gone or renamed", "module the walk skips", "module would not parse" — so the row named
    the writer in one note (it reaches the gated seam) and declared it nonexistent in the next.
    That writer is exactly the one `ROW_SCOPE_NOTE` is about, i.e. what a future `#11086` lane is
    most likely to try to declare, and it was told the writer was gone.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {"tortoise.sdk::create_point": {
        "callee": probe.SEAM_CALL, "at": "tortoise/sdk.py:1",
        "kind": "gated", "label": "Point",
        "callees": frozenset({probe.SEAM_CALL}),
    }}
    declarations = {"tortoise.sdk::create_point": _declared("tortoise.sdk::create_point")}
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    stale = next(n for n in row.notes if n.startswith("stale declarations"))
    assert "tortoise.sdk::create_point" in stale, stale
    assert "discovery classes it `gated`" in stale, (
        f"the note must name the `gated`/`producer` cause, not only 'gone or renamed': {stale}")


def test_a_ROUTE_claim_its_body_does_not_make_is_not_credited(tmp_path, monkeypatch):
    """Corroboration: `recognised_producer` is a claim about a ROUTE.

    The class's own definition (the register's `RECOGNISED_PRODUCER` docstring) is
    *"a writer whose vector reaches a recognised producer — the canonical clause
    builder or the `#5008` session producer"*. A declaration that says so while the
    function's own body makes no such call is contradicted by the seam that is
    supposed to support it, so it is NOT credited and the writer stays unrecognised.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               # deliberately NO recognised-producer callee
               **_unresolved_writer("tortoise.b::_claims")}
    declarations = {
        "tortoise.b::_claims": _declared(
            "tortoise.b::_claims", probe.CENSUS_RECOGNISED,
            reason="`tortoise.b::_claims` reaches `set_embedding_clause`"),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    assert "uncorroborated declarations" in row.observed, row.observed
    assert "claims a route to a recognised producer" in row.observed, row.observed


def test_a_declaration_parked_on_ANOTHER_writers_seam_is_not_credited(
        tmp_path, monkeypatch):
    """A citation must be the writer's OWN seam, not a resolvable one anywhere.

    The register's own citation test requires *a* `module::symbol` that resolves at
    HEAD; it cannot tell whose seam it is. The instrument can: the citation must name
    the writer the declaration is about, or the declaration is parked on someone
    else's evidence and is not credited.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.b::_named")}
    declarations = {
        "tortoise.b::_named": _declared(
            "tortoise.b::_named",
            reason="`tortoise.a::_gated` is exempt for this test"),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    assert "does not cite its own" in row.observed, row.observed


def test_a_named_UNGATED_writer_is_the_accepted_exception_reading_b(
        tmp_path, monkeypatch):
    """Reading (b), in the direction that used to be impossible to express.

    `tortoise.session_indexer::main` is a REAL write path outside the gate and it is
    NAMED in the register (owner `#5008`). Under reading (b) that is a named,
    accepted exception, so it is credited — while the row STILL REPORTS it outside
    the gate, because "the rule never sees it" remains a fact the reader must see.
    Before `#11444` this writer held row 1 unconditionally, which is exactly why the
    rung was unreachable.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {
        **_gated_writer("tortoise.a::_gated"),
        "tortoise.session_indexer::main": {
            "callee": "compute_session_embedding",
            "at": "tortoise/session_indexer.py:1",
            "kind": "ungated", "label": "Event",
            "callees": frozenset({"compute_session_embedding"}),
        },
    }
    declarations = {
        "tortoise.session_indexer::main": _declared(
            "tortoise.session_indexer::main", probe.CENSUS_RECOGNISED,
            owner="#5008",
            reason="`tortoise.session_indexer::main` uses "
                   "`compute_session_embedding`"),
    }
    _registered_population(monkeypatch, declarations, writers)
    row = probe.score_rule_bullet()
    assert row.verdict == "MET", f"{row.verdict}: {row.observed}"
    assert "embed OUTSIDE the gate: 1" in row.observed, row.observed


def test_an_UNREADABLE_register_holds_row_one_as_UNMEASURED(tmp_path, monkeypatch):
    """A register that cannot be read is a HOLE, never "nothing is declared".

    Reading it as an empty mapping would render every outside-gate writer `undeclared`
    and blame the PRODUCT for the instrument's own failure — the misattribution shape
    this file refuses on the census side, applied to the new input.
    """
    _row_one_fixture(tmp_path, monkeypatch)
    writers = {**_gated_writer("tortoise.a::_gated"),
               **_unresolved_writer("tortoise.b::_named")}

    def _unreadable():
        raise ImportError("no register module")

    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    monkeypatch.setattr(probe, "declared_writers", _unreadable)
    monkeypatch.setattr(probe, "flag_in_shipped_configs",
                        lambda flag: _fake_shipped(True))
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    row = probe.score_rule_bullet()
    assert row.verdict == "PARTIAL", f"{row.verdict}: {row.observed}"
    assert "UNREADABLE" in row.observed, row.observed
    assert "undeclared writers" not in row.observed, (
        "an unreadable register must not be reported as the product declaring "
        f"nothing: {row.observed}")


def test_the_LIVE_reader_plumbs_the_stores_declaration_through():
    """`#11492`: the register's `stores` field must actually REACH the row's reader.

    ⛔ Found by mutation, not by review: deleting `"stores"` from `declared_writers()`'s dict —
    i.e. disconnecting the ONE field this whole change exists to read — left the entire probe suite
    GREEN (157 passed, 1 skipped) while the live instrument reported
    `0 label-resolved · 0 declared no-live-vector · 0 labels-at-runtime · 0 undeclared · 19
    malformed`: every row-two test injects BOTH inputs via `monkeypatch`, so none of them ever
    exercises the product-register → reader seam. This is the mirror of the `#11492`-recorded
    `injected-map-inherits-live-declaration` lesson: a fixture that replaces half its world measures
    the other half's, and the seam between them goes unpinned.

    Asserted against the LIVE register and the LIVE reader, so it cannot be satisfied by injecting
    a declaration.
    """
    from tortoise import embed_writer_register as register

    declared = probe.declared_writers()
    assert set(declared) == set(register.EMBED_WRITER_CLASSES), (
        "the reader and the register disagree about which writers are declared: "
        f"missing={sorted(set(register.EMBED_WRITER_CLASSES) - set(declared))} "
        f"extra={sorted(set(declared) - set(register.EMBED_WRITER_CLASSES))}"
    )
    for key, entry in sorted(register.EMBED_WRITER_CLASSES.items()):
        assert "stores" in declared[key], (
            f"`declared_writers()` dropped `stores` for {key} — row 2 reads the declaration for "
            "exactly this field, and without it every writer reads `malformed` while the suite "
            "stays green"
        )
        assert declared[key]["stores"] == entry.stores, (
            f"{key} reaches row 2 with stores={declared[key]['stores']!r}, but the register "
            f"declares {entry.stores!r} — the reader must pass it through UNINTERPRETED"
        )


def test_the_LIVE_register_credits_every_writer_outside_the_gate(monkeypatch):
    """On THIS tree: every writer discovery finds outside the gate is CREDITED.

    The real register and the real discovery, through the ROW rather than through the
    register's own arithmetic. With the gate flag read as ON — the other half of row 1,
    owned by `#11086` — the row reaches `MET`, which is the reachability `#11444` is
    about. A writer added to `tortoise/` without a declaration reds HERE, from the
    instrument's side, not only from the register's.
    """
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    monkeypatch.setattr(probe, "flag_in_shipped_configs",
                        lambda flag: _fake_shipped(True))
    row = probe.score_rule_bullet()
    for hold in ("undeclared writers", "stale declarations", "REAL BYPASSES",
                 "uncorroborated declarations", "writer register UNREADABLE"):
        assert hold not in row.observed, f"{hold}: {row.observed}"
    # ⛔ NOT a hard-coded 20: the counts are RE-DERIVED from the row's own note and
    # from the discovery the row scored, so a writer added tomorrow reds on "not
    # everything was credited" rather than on a figure someone had to remember to
    # re-cut — the rot class this file deletes elsewhere.
    credited, total = re.search(
        r"credited from the product register .*?: (\d+) of (\d+) "
        r"outside-gate writers", row.observed).groups()
    outside = {w for w, d in probe.embedding_writers().items()
               if d["kind"] in ("unresolved_write", "ungated")}
    assert int(total) == len(outside), (
        f"the row scored {total} outside-gate writers and discovery finds "
        f"{len(outside)}: {row.observed}")
    assert credited == total, (
        f"every writer discovery finds outside the gate must be CREDITED — "
        f"{int(total) - int(credited)} is not, so row 1 would hold: {row.observed}")
    assert row.verdict == "MET", (
        f"row 1 must be REACHABLE once the gate flag is on and every writer is "
        f"classified — that is the defect #11444 fixes: {row.verdict} {row.observed}")


# ── #11088: the census names the `entity_type` values, not just the labels ──


class _NoWriteCensusGraph(_CensusGraph):
    """A fake on which the write-capable verb is a FAILURE.

    The census's doctrine is "stores nothing", and `graph.query` is
    write-CAPABLE — the read-only verb is what makes the doctrine the engine's
    rule instead of this file's memory. A census that reaches for `query` on
    this fake raises instead of quietly storing.
    """

    def query(self, cypher, params=None, timeout=None):
        raise AssertionError(
            f"the census issued a read through the WRITE-CAPABLE verb: {cypher[:90]}")


def test_every_affected_entity_type_is_derived_from_the_ONE_mapping():
    """`label` is not `entity_type`, in BOTH directions (#11088).

    The mapping is one-to-many and that is the whole finding: `:Source` answers
    for `document` AND `source`, `:Point` for `point` AND `operator`. A census
    that reported only labels would state the wrong population for the surface
    question it exists to answer — and it must come from
    `security.entity_label`, never from a second table here.
    """
    assert probe.affected_entity_types("Event") == ["event"]
    assert probe.affected_entity_types("Object") == ["object"]
    assert probe.affected_entity_types("Source") == ["document", "source"], (
        "two entity types reach :Source — a label-only census under-counts them")
    assert probe.affected_entity_types("Point") == ["operator", "point"], (
        "`operator` is served from :Point (#172), so removing Point's vectors "
        "would change a query that names no point")
    assert probe.affected_entity_types("Chunk") == [], (
        "a label no entity_type translates to has NO query path — it must read "
        "as affected-for-nobody, not be omitted")


def test_the_census_states_the_consequence_of_removing_each_label():
    """The count is not the consequence; the SET of entity types is (#11088).

    Every row carries `affected_entity_types` and
    `removal_changes_query_results`, the surface block names the tools derived
    from the registry — and the whole census goes through the READ-ONLY verb.
    """
    graph = _NoWriteCensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point", "Object", "Source", "Chunk"],
        vectors={"Point": (3, [0.0] * 384),
                 "Object": (2, [0.0] * 384),
                 "Source": (1, [0.0] * 384),
                 "Chunk": (4, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                                 graph_names=["g"])

    obj = census["labels"]["Object"]
    assert obj["stored"] == 2, obj
    assert obj["unindexed"] == 2, obj
    assert obj["affected_entity_types"] == ["object"], obj
    assert obj["removal_changes_query_results"] is True, obj

    # `:Source` is served by TWO entity types, so BOTH change.
    src = census["labels"]["Source"]
    assert src["affected_entity_types"] == ["document", "source"], src

    # The indexed label changes nothing.
    point = census["labels"]["Point"]
    assert point["indexed"] is True and point["unindexed"] == 0, point
    assert point["removal_changes_query_results"] is False, point

    # A stored, unindexed label NO entity_type reaches: reported, and it is not
    # a surface change because nothing queries it.
    chunk = census["labels"]["Chunk"]
    assert chunk["stored"] == 4 and chunk["unindexed"] == 4, chunk
    assert chunk["affected_entity_types"] == [], chunk
    assert chunk["removal_changes_query_results"] is False, chunk

    sc = census["surface_consequence"]
    assert sc["requested_not_performed"] is True, (
        "a census must never read as an authorisation — the removal is an "
        "MCP/SDK surface change (#11088 / AGENTS.md)")
    assert sc["entity_types_whose_query_results_change"] == \
        ["document", "object", "source"], sc
    assert {a["label"] for a in sc["labels"]} == {"Object", "Source"}, sc
    assert "Chunk" not in {a["label"] for a in sc["labels"]}, (
        "a label with no query path is not a surface consequence")

    # ⛔ EVERY entrant, and WHICH of them actually change (review round 7, P1):
    # `tortoise_recall` reaches `:Object` (affected) and `:Point` (indexed, so
    # not); the `:Point`-only tools are entrants with NO change — printing them
    # as affected would over-report as surely as dropping them under-reported.
    by_tool = {e["tool"]: e for e in sc["surface"] if "tool" in e}
    assert by_tool["tortoise_recall"]["reaches_affected_labels"] == ["object"], \
        by_tool["tortoise_recall"]
    assert by_tool["tortoise_suggest_entry_points"]["reaches_affected_labels"] == \
        [], by_tool["tortoise_suggest_entry_points"]
    assert by_tool["tortoise_search"]["reaches_affected_labels"] == \
        "any (the caller chooses)", by_tool["tortoise_search"]
    # `:Event` is not part of THIS fixture's affected set, so the sessions tool —
    # which asks the leg for `event` — correctly reads NO change here.
    assert by_tool["tortoise_search_sessions"]["reaches_affected_labels"] == [], \
        by_tool["tortoise_search_sessions"]

    # …and with an affected `:Event`, it does — the same derivation, one graph
    # over, so the pair is pinned rather than assumed.
    event_graph = _NoWriteCensusGraph(
        catalogue=[], labels=["Event"],
        vectors={"Event": (2, [0.0] * 384)},
    )
    event_sc = probe.vector_census(event_graph, "g2",
                                   at="2026-11-05T00:00:00Z",
                                   graph_names=["g2"])["surface_consequence"]
    event_tools = {e["tool"]: e for e in event_sc["surface"] if "tool" in e}
    assert event_tools["tortoise_search_sessions"]["reaches_affected_labels"] == \
        ["event"], event_tools["tortoise_search_sessions"]


def test_the_census_reports_BYTES_for_an_UNINDEXED_label():
    """⛔ The population this issue exists to quantify had no byte figure.

    Measured failure this pins: the payload was derived from the covering
    index's `dimension`, so the four UNINDEXED labels — the removable payload,
    and the whole reason #11088 is filed — reported
    `UNMEASURED(no VECTOR index on embedding)`. The stored WIDTH is already
    probed one line above (the `#4280` cross-check needs it), so the number was
    in hand and not printed. `#11088`'s own Indicator asks for "vector count and
    bytes per label"; before this, the labels it is about had no bytes at all.

    The guard is NOT weakened: an unmeasurable width still refuses, and an
    indexed label whose width disagrees with the index dimension still refuses.
    """
    graph = _NoWriteCensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point", "Object"],
        vectors={"Point": (1, [0.0] * 384), "Object": (2, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                                 graph_names=["g"])
    obj = census["labels"]["Object"]["payload"]
    assert obj["derived_payload_B"] == 2 * 384 * 4, (
        f"an unindexed label has no bytes figure: {obj!r} — the stored width "
        "was probed and ignored")
    assert "probed width" in obj["how"], obj

    # …and the refusal survives: an unprobeable width is still UNMEASURED.
    graph = _NoWriteCensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Object"],
        vectors={"Object": (2, None)},
    )
    censored = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                                   graph_names=["g"])
    assert str(censored["labels"]["Object"]["payload"]["derived_payload_B"]
               ).startswith("UNMEASURED"), censored

    # …and a COVERING index whose `dimension` cell is unreadable is NOT the
    # "no index" case: the row reports `indexed: true`, so its `how` must not
    # say "NO covering index" (review finding, P2 — `dim is None` conflated the
    # two, and the row contradicted its own `vector_indexes_on_label`).
    graph = _NoWriteCensusGraph(
        catalogue=[_index_row("Point", "embedding", None)],
        labels=["Point"],
        vectors={"Point": (5, [0.0] * 384)},
    )
    covered = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                                  graph_names=["g"])["labels"]["Point"]
    assert covered["indexed"] is True, covered
    assert "NO covering index" not in str(covered["payload"]), (
        f"a row reporting indexed=True claims it has no covering index: {covered}")
    assert str(covered["payload"]["derived_payload_B"]).startswith("UNMEASURED"), (
        f"an unreadable index dimension must not yield a verified byte figure: {covered}")


def test_the_surface_named_is_the_one_that_can_pass_entity_type():
    """The callers, derived from the registry and the handler AST (#11088).

    ⛔ The `sdk_method` binding ALONE under-reports: `tortoise_query` binds
    `paginated_query` (its structural arm) and still forwards a `text=` argument
    to `tortoise_fts_query(entity_type=…)`. A census built on the binding names
    ONE tool where the surface has two — the direction that UNDERSTATES the
    consequence of a removal, which is the direction that gets a surface change
    approved by mistake.
    """
    surface = probe.vector_leg_surface()
    named = {t["tool"] for t in surface if "tool" in t}
    assert {"tortoise_search", "tortoise_query"} <= named, (
        f"the surface census missed a tool that can pass entity_type: {surface}")
    # ⛔ AND the third entrant class (#11088 review round 7, P1): a tool whose
    # SDK method names the type itself. `tortoise_recall`'s DEFAULT mode calls
    # `recall_state`, which asks the leg for `:Object`; `tortoise_search_sessions`
    # asks for `:Event`. Neither forwards `entity_type`, so both sources above
    # miss them — and dropping them understates the approval request.
    recall = next(t for t in surface if t.get("tool") == "tortoise_recall")
    assert "object" in recall["entity_types"], (
        f"tortoise_recall's default state read queries :Object and is not named: {recall}")
    sessions = next(t for t in surface
                    if t.get("tool") == "tortoise_search_sessions")
    assert sessions["entity_types"] == ["event"], sessions
    for entry in surface:
        if "tool" in entry:
            assert entry["takes_entity_type"] is True, entry
    search = next(t for t in surface if t.get("tool") == "tortoise_search")
    assert search["rest"] == "GET /v1/search", search


def test_a_tool_whose_SDK_method_fixes_entity_type_is_named():
    """The THIRD source, and the default it must resolve first (#11088 round 7, P1).

    A handler that never forwards `entity_type` can still reach the leg, because
    the SDK method it calls names the type (`recall_state` → `object`,
    `search_sessions` → `event`). A call that OMITS the kwarg runs the SDK's own
    default, which is an entrant too — so the derivation must read the default
    from the leg's SIGNATURE before it reads any call site. A one-pass walk
    reached `suggest_entry_points` first and reported
    `UNKNOWN(no declared default found)` for a call whose type is `point`: an
    order-dependent answer is not a measurement.
    """
    methods, error, declared = probe._sdk_methods_on_vector_leg()
    assert error is None, error
    # ⛔ A DECLARED BOUND is not a scan failure — the distinction is what keeps a
    # legitimate by-reference shape from reading as "the scan could not run" and
    # getting the declaration deleted (#11337 review, P1). The real tree DOES
    # declare one at this head (`volunteer_context` passes the leg itself as
    # `_search_fn`), so only the marker SPELLING is pinned here. The declared
    # PASSERS are pinned by NAME, not by count, in
    # `test_the_census_doc_states_the_same_probe_bound_the_report_does`: a
    # declared passer that §3 of the census doc does not name reds it, while a
    # second passer whose name already appears IN §3 would pass — the recorded
    # residual of the `#11397` class, and the reason the census doc states the
    # pin's shape instead of claiming the population is pinned.
    assert declared is not None and declared.startswith(
        "UNMEASURED(declared bound:"), declared
    # ⛔ THE LEG ITSELF must be mentionable (#11389 review, P1). `direct` excludes
    # the leg, so a walk built only on `direct` missed `volunteer_context`
    # (`_search_fn=self.tortoise_fts_query`) — the ONE real shape in this tree —
    # while three artifacts certified that no such shape existed. Pinned on the
    # real tree, because that is where the claim was false.
    assert "tortoise_fts_query" in declared, (
        f"a mention OF THE LEG is not declared: {declared}")
    assert methods["recall_state"] == {"object", "point"}, methods
    assert methods["search_sessions"] == {"event"}, methods
    assert methods["suggest_entry_points"] == {"point"}, (
        "a call that omits entity_type runs the SDK default, and it must be "
        f"resolved from the signature, not reported as UNKNOWN: {methods}")
    assert probe.VECTOR_LEG_SDK_METHOD not in methods, (
        "the leg was named as its own entrant")


def _byref_sdk_source() -> str:
    """A synthetic `sdk.py` with one method per by-reference SHAPE.

    Each is a reach the `Call.func` scan cannot resolve: the function is
    MENTIONED without being called. The positions are deliberately spread —
    argument, keyword, `partial`, `getattr` (literal and dynamic), plain and
    annotated assignment, walrus, container, bare `return` — because the rule
    is the mention, so a pin covering only call arguments would leave the
    claim wider than the pin.
    """
    return (
        "class S:\n"
        "    def tortoise_fts_query(self, q, entity_type='point'):\n"
        "        return q\n"
        "    def _review_pool(self, t):\n"
        "        return self.tortoise_fts_query('x', entity_type=t)\n"
        "    def passes_an_attribute(self):\n"
        "        return _safe(self._review_pool, 'object')\n"
        "    def passes_a_bare_name(self):\n"
        "        return _safe(_review_pool, 'object')\n"
        "    def passes_a_literal_getattr(self):\n"
        "        return _safe(getattr(self, '_review_pool'), 'object')\n"
        "    def passes_a_dynamic_getattr(self, which):\n"
        "        return _safe(getattr(self, which), 'object')\n"
        "    def passes_an_alias(self):\n"
        "        f = self._review_pool\n"
        "        return _safe(f, 'object')\n"
        "    def passes_a_keyword_callback(self):\n"
        "        return _safe(1, key=self._review_pool)\n"
        "    def passes_a_partial(self):\n"
        "        return _safe(functools.partial(self._review_pool, 'object'))\n"
        "    def passes_a_FOREIGN_receiver(self):\n"
        "        return _safe(self.conn._review_pool, 'object')\n"
        "    def passes_an_annotated_assign(self):\n"
        "        f: object = self._review_pool\n"
        "        return _safe(f, 'object')\n"
        "    def passes_a_walrus(self):\n"
        "        return _safe(f := self._review_pool)\n"
        "    def passes_a_container(self):\n"
        "        return _safe([self._review_pool], 'object')\n"
        "    def returns_it_bare(self):\n"
        "        return self._review_pool\n"
        "    def calls_one_and_passes_another(self):\n"
        "        _safe(self._second_pool, 'event')\n"
        "        return self._review_pool('object')\n"
        "    def _second_pool(self, t):\n"
        "        return self.tortoise_fts_query('y', entity_type=t)\n"
        "    def calls_it_normally(self):\n"
        "        return self._review_pool('object')\n"
        "    def calls_a_bare_name(self):\n"
        "        return _review_pool('object')\n"
        "    def passes_two(self):\n"
        "        _safe(self._review_pool, 'object')\n"
        "        return _safe(self._second_pool, 'event')\n"
        "    def passes_a_DEFAULTED_getattr(self):\n"
        "        return _safe(getattr(self, '_review_pool', None))\n"
        "    def shadows_an_entrant_name(self):\n"
        "        _review_pool = 5\n"
        "        return _review_pool\n"
        "    def mentions_past_a_NESTED_shadow(self):\n"
        "        def inner():\n"
        "            _review_pool = 5\n"
        "            return _review_pool\n"
        "        return _safe(_review_pool, 'object')\n"
        "    def CALLS_a_getattr(self):\n"
        "        return getattr(self, '_review_pool')('object')\n"
        "    def passes_from_super(self):\n"
        "        return _safe(super()._review_pool, 'object')\n"
        "    def shadows_via_EXCEPT(self):\n"
        "        try:\n"
        "            pass\n"
        "        except Exception as _review_pool:\n"
        "            pass\n"
        "        return _safe(_review_pool)\n"
        "    def shadows_via_IMPORT(self):\n"
        "        import _review_pool\n"
        "        return _safe(_review_pool)\n"
        "    def mention_past_a_COMPREHENSION_shadow(self):\n"
        "        _safe(_review_pool)\n"
        "        return [x for _review_pool in (1, 2)]\n"
        # ⛔ The OUTERMOST iterable is evaluated in the ENCLOSING scope, so a
        # mention there is a real reach (#11389 review, P3).
        "    def mentions_the_OUTERMOST_iterable(self):\n"
        "        return [x for _review_pool in _review_pool]\n"
        "    def binds_it_in_a_COMPREHENSION_only(self, rows):\n"
        "        return [x for _review_pool in rows]\n"
        # The comprehension's own target, REFERENCED inside it: the `Load`
        # belongs to the comprehension's scope, not the method's, so it is not a
        # mention of the entrant (#11389 review, P2).
        "    def references_a_COMPREHENSION_TARGET_in_its_element(self, rows):\n"
        "        return [_review_pool for _review_pool in rows]\n"
        "    def references_a_COMPREHENSION_TARGET_in_its_if(self, rows):\n"
        "        return [r for _review_pool in rows if _review_pool]\n"
        # ⛔ Every generator AFTER the first is evaluated in the comprehension's
        # OWN scope, so the later target's `Load` is the comprehension's local,
        # not the method's (#11389 review round 2, P3). The element and `if`
        # halves were pinned; this one was not, so over-generalising the fix to
        # "generator iterables are the enclosing scope's" left the doc's
        # sentence false with the suite still green.
        "    def references_a_LATER_generators_target(self, rows):\n"
        "        return [r for _ in rows for _review_pool in _review_pool]\n"
        "    def assigns_the_attr(self, v):\n"
        "        self._review_pool = v\n"
        "    def augments_the_attr(self, v):\n"
        "        self._review_pool += v\n"
        "    def deletes_the_attr(self):\n"
        "        del self._review_pool\n"
        "    def mentions_inside_a_nested_CLASS(self):\n"
        "        class K:\n"
        "            cb = _safe(self._review_pool)\n"
        "        return K\n"
        # ⛔ A CALL in a class body, not a mention: it must be MEASURED by the
        # closure, not left in neither channel (#11389 review, P3).
        "    def calls_directly_inside_a_nested_CLASS(self):\n"
        "        class K:\n"
        "            hit = self._review_pool('point')\n"
        "        return K\n"
        # The shape that pins `_scan` specifically: the leg called DIRECTLY, with
        # a literal `entity_type`, inside a class body. Membership comes from the
        # closure edge, so only the TYPE SET can distinguish the walks.
        "    def calls_the_leg_directly_inside_a_nested_CLASS(self):\n"
        "        class K:\n"
        "            hit = self.tortoise_fts_query('q', entity_type='object')\n"
        "        return K\n"
        # ── `#11397`: the OVER-report half — each of these BINDS the name in this scope, so a
        # later mention of it is not a reach of the method, and declaring one is a caveat on a
        # complete table (the cry-wolf direction).
        "    def shadows_via_STAR_ARGS(self, *args, **kwargs):\n"
        "        return _safe(_review_pool)\n"
        "    def star_args_named_like_an_entrant(self, *_review_pool, **kw):\n"
        "        return _safe(_review_pool)\n"
        "    def lambda_param_named_like_an_entrant(self):\n"
        "        return (lambda _review_pool: _safe(_review_pool))(1)\n"
        "    def nested_def_name_named_like_an_entrant(self):\n"
        "        def _review_pool():\n"
        "            return 1\n"
        "        return _safe(_review_pool)\n"
        "    def match_pattern_named_like_an_entrant(self, v):\n"
        "        match v:\n"
        "            case _review_pool:\n"
        "                return _safe(_review_pool)\n"
        "        return None\n"
        # ⛔ `del` ALONE binds the name (`del x` makes `x` local in CPython's compiler), so this
        # arm is not satisfied by a preceding `x = v` — with the assignment present, the `Store`
        # shadows the name and the `Del` half of the model goes UNPINNED (measured).
        "    def del_binding_named_like_an_entrant(self):\n"
        "        del _review_pool\n"
        "        return _safe(_review_pool)\n"
        # ── `#11397`: the UNDER-report half — each of these is a REAL in-module receiver the walk
        # used to drop with no entry anywhere: `cls`, the enclosing CLASS NAME, and `self.__class__`
        # (directly and through `getattr`).
        "    def classmethod_cls_receiver(cls):\n"
        "        return _safe(cls._review_pool)\n"
        "    def class_name_receiver(self):\n"
        "        return _safe(S._review_pool)\n"
        "    def dunder_class_receiver(self):\n"
        "        return _safe(self.__class__._review_pool)\n"
        "    def dunder_class_getattr(self):\n"
        "        return _safe(getattr(self.__class__, '_review_pool'))\n"
        "    def cls_is_not_a_receiver_here(self, cls):\n"
        "        return _safe(cls._review_pool)\n"
        # ── `#11397` review: a LAMBDA parameter binds in the LAMBDA's scope, so it must not shade
        # the enclosing body (that suppression is the under-report direction, and it was LIVE: see
        # `lambda_param_does_not_shade_the_body`), while its DEFAULTS are evaluated in the enclosing
        # scope and are real mentions (`lambda_default_is_the_enclosing_scope`).
        "    def lambda_param_does_not_shade_the_body(self):\n"
        "        f = lambda _review_pool: 1\n"
        "        return _safe(_review_pool)\n"
        "    def lambda_default_is_the_enclosing_scope(self):\n"
        "        f = lambda v=_review_pool: v\n"
        "        return f()\n"
        # ⛔ `#12020` round 8 cycle 6 (P3): the LAMBDA BODY is its own scope too, and the walk
        # descended into it — a walrus INSIDE a lambda landed in the METHOD's shadow set and
        # suppressed a genuine by-reference mention further down (the under-report direction). `dis`
        # confirms `lambda: (x := 1)` binds `x` as the lambda's own fast local.
        "    def walrus_inside_a_LAMBDA_does_not_shade_the_body(self):\n"
        "        f = lambda: (_review_pool := 1)\n"
        "        return _safe(_review_pool)\n"
        # ⛔ …but a lambda's DEFAULTS are evaluated in the ENCLOSING scope, so a walrus there DOES
        # bind in the method (`symtable`: this method's locals include `_review_pool`, the lambda's
        # do not) — the mention after it is not a reach. Pinned because the body fix skipped the
        # whole `ast.Lambda` node and silently turned this arm into an over-report.
        "    def walrus_in_a_LAMBDA_DEFAULT_named_like_an_entrant(self):\n"
        "        f = lambda v=(_review_pool := 1): v\n"
        "        return _safe(_review_pool)\n"
        # ⛔ and the lambda BODY's own walrus shadows INSIDE the lambda, so a mention of that name
        # later IN THE SAME LAMBDA is the lambda's binding, not the method's (`#12182` review P3:
        # parameters were threaded and body targets were not, which published a spurious caveat).
        "    def mention_inside_a_LAMBDA_after_its_own_walrus_named_like_an_entrant(self):\n"
        "        f = lambda: (_safe((_review_pool := 1)), _safe(_review_pool))\n"
        "        return f()\n"
        # ⛔ …and a NESTED lambda is its own scope AGAIN, so a walrus in the INNER body must not
        # suppress the OUTER lambda's mention of the method's binding — nor the method's own. Without
        # this arm, descending into a nested lambda body left every other test green while dropping
        # a real reach (the under-report direction), which the sibling comment above says is the
        # dangerous one.
        "    def nested_lambda_body_does_not_shade_the_enclosing_lambda(self):\n"
        "        f = lambda: (lambda: (_review_pool := 1), _safe(_review_pool))\n"
        "        return f()\n"
        # ⛔ …and a COMPREHENSION's walrus binds in the scope that CONTAINS the comprehension — here
        # that is the lambda, not the method — so a comprehension inside a lambda body must still be
        # walked and its targets attributed to the LAMBDA. Skipping comprehensions inside the lambda
        # body published the outer mention as a reach (the cry-wolf direction), and nothing reds.
        "    def comprehension_inside_a_lambda_body_shadows_only_the_lambda(self):\n"
        "        f = lambda: ([(_review_pool := i) for i in range(3)], _safe(_review_pool))\n"
        "        return f()\n"
        # ⛔ A KEYWORD-ONLY default is still a DEFAULT: `lambda *, v=(_p := 1): v` evaluates it in
        # the enclosing scope exactly as a positional one does, and the walk has two separate lists
        # (`defaults` and `kw_defaults`). Dropping the second left every other test green while
        # publishing a spurious reach — so both lists are pinned, one arm each.
        "    def walrus_in_a_KEYWORD_ONLY_LAMBDA_DEFAULT_named_like_an_entrant(self):\n"
        "        f = lambda *, v=(_review_pool := 1): v\n"
        "        return _safe(_review_pool)\n"
        # ── `#11397` review: `*. __class__` is only the ENCLOSING class when the base is an
        # in-module receiver; a foreign object's class is not this module's method.
        "    def foreign_dunder_class_receiver(self, other):\n"
        "        return _safe(other.__class__._review_pool)\n"
        # ── `#12020` round 6 review (P3): `type(…)` is the SAME receiver rule written a second way,
        # and the arm accepted ANY call to `type` while its twin above refuses a foreign base. The
        # pair is pinned together: `type(self)` is the enclosing class, `type(other)` is a FOREIGN
        # object's class and must not be declared.
        "    def type_of_self_is_a_receiver(self):\n"
        "        return _safe(type(self)._review_pool, 'object')\n"
        "    def type_of_a_FOREIGN_object(self, other):\n"
        "        return _safe(type(other)._review_pool, 'object')\n")


def test_a_leg_reach_passed_BY_REFERENCE_is_declared_not_dropped(
        tmp_path, monkeypatch):
    """The closure claim is over name-resolved calls, and says so (#11337).

    `_safe(self._review_pool, …)` passes a leg-reaching function as an
    **argument**. The callee extraction follows `func.attr` / `func.id`, so a
    reach through a passed callable, a `key=`/callback, `functools.partial`,
    `getattr` or an alias is dropped **with no entry anywhere** — the same
    silent-hole class the handler scan declares for itself and reports through
    an `UNMEASURED` entry. Under-reporting is the dangerous direction here: the
    caller list is the surface an owner approves a removal against.

    `tortoise/sdk.py` holds exactly ONE such shape at this head —
    `volunteer_context` passes the leg itself as `_search_fn` — which is why the
    fix is a NARROWER CLAIM plus a declaration, not a widening of the closure:
    the class is real and cannot be traversed. The fixture covers each syntactic
    position with a synthetic tree, because a bound that only ever fires on the
    shapes one tree happens to contain would otherwise be untestable, and wastes
    the report on a shape the crew does not have. The NEGATIVE cases are pinned
    too (a resolved call, a shadowing local in several binding forms, a foreign
    receiver, an attribute binding): an instrument that declares a reach it
    already traversed puts a caveat on a complete table, which is how a real
    declaration stops being read.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "sdk.py").write_text(
        _byref_sdk_source(), encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)

    methods, error, declared = probe._sdk_methods_on_vector_leg()
    assert error is None, (
        "a DECLARED BOUND is not a scan failure — overloading the error slot "
        f"made a legitimate shape read as a broken scan: {error}")
    # ⛔ Every SYNTACTIC POSITION the class can appear in, not only the ones the
    # first fixture happened to write: the reach is a mention of the function,
    # so an annotated assignment, a walrus, a container and a bare `return` must
    # be declared exactly like a call argument (#11337 review, P3).
    shapes = ("passes_an_attribute", "passes_a_bare_name",
              "passes_a_literal_getattr", "passes_a_dynamic_getattr",
              "passes_an_alias", "passes_a_keyword_callback",
              "passes_a_partial", "passes_an_annotated_assign",
              "passes_a_walrus", "passes_a_container", "returns_it_bare",
              "passes_a_DEFAULTED_getattr",
              # A NESTED def's local is a different scope: it must not suppress
              # the ENCLOSING body's genuine mention (#11337 review, P2).
              "mentions_past_a_NESTED_shadow",
              # A `getattr(...)(…)` func is NOT resolvable by the closure, so its
              # mention must stay visible; `super()` is an in-module receiver.
              "CALLS_a_getattr", "passes_from_super",
              # A comprehension target is its own scope, so it must NOT suppress
              # the body's genuine mention (#11389 review).
              "mention_past_a_COMPREHENSION_shadow",
              # A nested class BODY runs with the enclosing method, so a mention
              # there is a real reach (#11389 review, P3).
              "mentions_inside_a_nested_CLASS",
              # The outermost iterable is the ENCLOSING scope's, so this is a
              # real reach and must be declared (#11389 review, P3).
              "mentions_the_OUTERMOST_iterable",
              # ⛔ `#11397`: every in-module RECEIVER that is not literally `self`
              # (`cls`, the enclosing class name, `self.__class__` and
              # `getattr(self.__class__, …)`) is a real reach the walk dropped with no
              # entry anywhere — the two `cls` arms measure both that the receiver is
              # honoured when it IS the classmethod's, and that a plain parameter named
              # `cls` is honoured the same way (it is the same spelling, and a receiver
              # set of only `self` made BOTH invisible).
              "classmethod_cls_receiver", "class_name_receiver",
              "dunder_class_receiver", "dunder_class_getattr",
              # ⛔ `#11397` review: a LAMBDA parameter binds in the LAMBDA's own scope, so a mention
              # of the same name in the ENCLOSING body is still a reach — function-wide shadowing
              # suppressed it (the silent-short-list direction). And a lambda DEFAULT is evaluated
              # in the enclosing scope, so it is a reach too.
              "lambda_param_does_not_shade_the_body",
              "lambda_default_is_the_enclosing_scope",
              # ⛔ and the lambda BODY is its own scope: a walrus there is the lambda's local, so a
              # mention of the same name in the enclosing method is still a reach (round 8 cycle 6).
              "walrus_inside_a_LAMBDA_does_not_shade_the_body",
              # ⛔ and a NESTED lambda body is a third scope: the inner walrus is the INNER lambda's,
              # so the outer lambda's mention is still a reach into the method.
              "nested_lambda_body_does_not_shade_the_enclosing_lambda",
              # ⛔ and a comprehension inside a lambda body binds in the LAMBDA (its containing
              # scope), which is NOT the method — so the mention after it is the lambda's binding,
              # not a reach. This arm asserts the NEGATIVE direction: skipping comprehensions inside
              # a lambda body published it as a reach (cry-wolf), which nothing else reds.
              # ⛔ `#12020` round 6 review (P3): `type(self)` is the receiver spelled through the
              # type — the positive half of the pair whose NEGATIVE (`type(other)`) is asserted
              # beside `other.__class__` above.
              "type_of_self_is_a_receiver",
              # the CONTROL for the star-arg half: `*args`/`**kwargs` bind THEIR names, and a
              # mention of a different name is still a reach — without this row, "add every
              # vararg name to `shadowed`" could shade the whole body and stay green.
              "shadows_via_STAR_ARGS")
    for name in shapes:
        assert name not in methods, (
            f"{name} reaches the leg through a reference this scan cannot "
            f"resolve, and naming it would be a guess dressed as a "
            f"measurement: {methods}")
    # ⛔ An ENTRANT can also pass a DIFFERENT leg-reaching helper BY REFERENCE
    # (#11337 review, P2). Skipping entrant functions kept it out of the
    # declaration, so its caller-list entry was present while the entity-type
    # SET published from `direct` silently missed the passed helper's types —
    # the under-report direction, in the artifact an owner reads. It must be
    # BOTH named and declared.
    assert "calls_one_and_passes_another" in methods, methods
    assert "calls_one_and_passes_another" in declared, (
        "an entrant that also passes a leg-reaching helper by reference was "
        f"declared nowhere, so its type set reads complete when it is not: {declared}")
    assert declared, (
        "the scan dropped every by-reference reach with no entry anywhere: the "
        "caller list is silently short and nothing says so")
    assert "by reference" in declared, declared
    # ⛔ A machine consumer must be able to tell a DECLARED BOUND from a scan
    # FAILURE: both publish `{"tools": "UNMEASURED(…)"}`, so the marker is what
    # separates them (#11337 review).
    assert "declared bound:" in declared, (
        f"the declaration is indistinguishable from a scan failure: {declared}")
    for name in shapes:
        assert name in declared, f"{name} is not in the declaration: {declared}"
    # ⛔ The OVER-REPORT directions, both of them. A spurious caveat on a
    # COMPLETE caller table is the cry-wolf failure that makes the real
    # declaration ignorable, and it was live: matching a `Call`'s own `func` made
    # every ordinary `self.<entrant>(…)` read as an unresolved reference, which
    # is what published five false "LIVE" entries on the real tree (#11337
    # review, P0). A resolved call is the closure's, not this channel's.
    for resolved in ("calls_it_normally", "calls_a_bare_name"):
        # ⛔ MEASURED, not merely "not declared" (#11389 review, P2). The
        # one-sided assertion was satisfied by dropping the reach from BOTH
        # channels: deleting the bare-`Name` branch of the callee extraction
        # took `_review_pool('object')` out of the caller list with the suite
        # still green. A resolved call must be in the table this channel
        # declines to declare.
        assert resolved in methods, (
            f"a NAME-RESOLVED call was measured by NEITHER channel, so the "
            f"caller list reads short with nothing saying so: {resolved} not "
            f"in {methods}")
        assert resolved not in declared, (
            f"a NAME-RESOLVED call was declared as an unresolved reference: "
            f"{resolved} in {declared}")
    # ⛔ A CALL in a nested class body is the CLOSURE's, not this channel's, and
    # it must actually be measured there (#11389 review, P3): the call walks
    # skipped `ClassDef` while the mention walk descended into it, so such a
    # call was in NEITHER artifact — not an edge, and excluded here as a call
    # the closure "owns". Asserted as MEASURED, because "not declared" alone is
    # satisfied by the silent hole.
    assert "calls_directly_inside_a_nested_CLASS" in methods, (
        "a leg call in a nested class body was measured by neither channel: "
        f"the caller list reads short with nothing saying so: {methods}")
    assert "calls_directly_inside_a_nested_CLASS" not in declared, declared
    # …and its ENTITY TYPE, because membership alone is supplied by the closure
    # edge while the type set comes from the `_scan` walk: mutating only the
    # latter left this test green (measured), which is how a short type set ships
    # under a correct caller list.
    assert (methods["calls_directly_inside_a_nested_CLASS"]
            == methods["calls_it_normally"]), (
        "a class-body call was measured DIFFERENTLY from the same call in the "
        "plain body, so its entity-type set is short: "
        f"{methods['calls_directly_inside_a_nested_CLASS']} != "
        f"{methods['calls_it_normally']}")
    # A DIRECT leg call in a class body is where the `_scan` walk is load-bearing:
    # membership is the closure edge either way, so only the type set can tell
    # whether the walk descended (#11389 review, P3 — measured: mutating only
    # `_scan` left a membership-only assertion green).
    assert (methods.get("calls_the_leg_directly_inside_a_nested_CLASS")
            == {"object"}), (
        "a direct leg call in a nested class body was not measured, so its "
        "entity-type set is short or absent: "
        f"{methods.get('calls_the_leg_directly_inside_a_nested_CLASS')}")
    # …and it is a RESOLVED call, so it must never be declared a second time: a
    # caveat on a table the closure already covers (measured: without this the
    # `_scan` mutation was pinned by the type set alone and a resolved
    # class-body leg call could be declared with the suite green).
    assert "calls_the_leg_directly_inside_a_nested_CLASS" not in declared, declared

    # A LOCAL that reuses an entrant's name is not a reach: `_review_pool = 5`
    # binds the NAME without referring to the method, and declaring it would
    # caveat a complete table — the same cry-wolf failure as the resolved call.
    for shadowing in ("shadows_an_entrant_name", "shadows_via_EXCEPT",
                      "shadows_via_IMPORT",
                      # A comprehension TARGET is a binding, not a mention: it
                      # read as a reach once the scope skip went in (#11389).
                      "binds_it_in_a_COMPREHENSION_only",
                      # …and a `Load` of that target INSIDE the comprehension is
                      # the comprehension's own binding, not the method's
                      # (#11389 review, P2).
                      "references_a_COMPREHENSION_TARGET_in_its_element",
                      "references_a_COMPREHENSION_TARGET_in_its_if",
                      # …and a LATER generator's iterable is the
                      # comprehension's own scope too — the natural
                      # over-generalisation of the outermost-iterable rule
                      # (#11389 review round 2, P3).
                      "references_a_LATER_generators_target",
                      # An attribute BINDING is not a mention either.
                      "assigns_the_attr", "augments_the_attr", "deletes_the_attr",
                      # ⛔ `#11397`: the scope model's remaining OVER-report holes. Each
                      # BINDS the name in the method's own scope, so publishing a later
                      # mention as a by-reference reach puts a spurious caveat on a
                      # complete caller table.
                      "star_args_named_like_an_entrant",
                      "lambda_param_named_like_an_entrant",
                      "nested_def_name_named_like_an_entrant",
                      "match_pattern_named_like_an_entrant",
                      "del_binding_named_like_an_entrant",
                      # ⛔ and a lambda DEFAULT binds in the METHOD (`symtable`), unlike its BODY.
                      "walrus_in_a_LAMBDA_DEFAULT_named_like_an_entrant",
                      # ⛔ and the KEYWORD-ONLY spelling of the same rule (a separate list in the walk).
                      "walrus_in_a_KEYWORD_ONLY_LAMBDA_DEFAULT_named_like_an_entrant",
                      # ⛔ a comprehension walrus inside a lambda binds in the LAMBDA, so a mention
                      # after it is the lambda's binding — not a method reach (the NEGATIVE half).
                      "comprehension_inside_a_lambda_body_shadows_only_the_lambda",
                      # ⛔ a mention of the lambda's OWN walrus target, later in the SAME lambda.
                      "mention_inside_a_LAMBDA_after_its_own_walrus_named_like_an_entrant",
                      # …and the boundary of the `cls` receiver: a plain parameter named `cls`
                      # on a method whose FIRST argument is `self` is a foreign object, not the
                      # class — only the classmethod's own first parameter is the class receiver.
                      "cls_is_not_a_receiver_here",
                      # …and the `__class__` receiver only names the ENCLOSING class when its BASE
                      # is an in-module receiver: `other.__class__._review_pool` is a foreign
                      # object's class, and accepting any `*.__class__` put a spurious caveat on a
                      # complete table (`#11397` review).
                      "foreign_dunder_class_receiver"):
        assert shadowing not in declared, (
            f"a local shadowing an entrant's name was declared as a reach: "
            f"{shadowing} in {declared}")
    # A foreign receiver is not an in-module method either.
    assert "passes_a_FOREIGN_receiver" not in declared, (
        f"a call on a FOREIGN receiver was declared as an in-module reach: {declared}")
    # ⛔ `#12020` round 6 review (P3): the receiver rule applies to `type(…)` exactly as it does to
    # `*.__class__`. `type(self)` IS the enclosing class; `type(other)` is a FOREIGN object's class,
    # and the arm above (`func.id in ("super", "type")` → True) accepted both — a spurious caveat
    # on a complete caller table. Pre-fix this arm is DECLARED, so the assertion is non-vacuous.
    assert "type_of_self_is_a_receiver" in declared, declared
    assert "type_of_a_FOREIGN_object" not in declared, (
        f"a call on `type(<a foreign object>)`'s helper was declared as an in-module reach — its "
        f"twin `other.__class__._helper` is refused for exactly that reason: {declared}")
    # …and a method taking TWO helpers by reference must name BOTH: keeping one
    # target per method dropped the second with the entry still looking complete.
    assert "passes_two" in declared, declared
    assert "_second_pool" in declared.split("passes_two")[1][:80], (
        f"only one of a method's two by-reference targets is named: {declared}")

    # ⛔ The DECLARATION must reach the REPORT, not just the helper's tuple: a
    # route that drops it reinstates the defect in full (this mutation left the
    # suite green before this assertion existed — #11337 review, P1).
    surface = probe.vector_leg_surface()
    published = [e["tools"] for e in surface if "tools" in e]
    assert any(t.startswith("UNMEASURED(") and "by reference" in t
               and "passes_an_attribute" in t for t in published), (
        "the by-reference declaration never reached the published surface, so "
        f"a caller list read short with nothing saying so: {published}")


def test_a_DECLARED_bound_does_not_swallow_the_DERIVED_failure(
        tmp_path, monkeypatch):
    """The two refusal channels must be publishable TOGETHER (#11389 review, P2).

    The declared bound is appended to the same list the emptiness check reads, so
    `not out` became UNREACHABLE whenever a declaration existed: a surface that
    could not be derived AT ALL published only the by-reference caveat — which
    says "the scan RAN and this is its known edge" — and nothing said the tool
    table was empty. The separation #11337 established in one direction
    collapsed in the other, and the dangerous reading is the one a consumer
    takes from a lone caveat: a complete surface with an edge.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "sdk.py").write_text(
        "class S:\n"
        "    def tortoise_fts_query(self, q, entity_type='point'):\n"
        "        return q\n"
        "    def _pool(self, t):\n"
        "        return self.tortoise_fts_query('x', entity_type=t)\n"
        "    def passes_it(self):\n"
        "        return _safe(self._pool)\n",
        encoding="utf-8")
    # No handler forwards `entity_type`, so NOT ONE tool row can be derived.
    (tmp_path / "tortoise" / "mcp_server.py").write_text(
        "def tortoise_health():\n    return {'ok': True}\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    import tortoise.tool_registry as registry
    monkeypatch.setattr(registry, "TOOL_REGISTRY", [])

    published = [e["tools"] for e in probe.vector_leg_surface() if "tools" in e]
    assert any("declared bound:" in line for line in published), (
        "the fixture no longer declares a by-reference bound, so this test "
        f"cannot see the swallow it exists to catch: {published}")
    assert any("could not be derived" in line for line in published), (
        "the declared bound swallowed the derived-failure line, so an "
        "underivable surface reads as a measured one with a known edge: "
        f"{published}")
    # …and the FAILURE comes FIRST: presence alone is satisfied by an entry
    # appended after the reader has already taken the surface as derived.
    failure = next(i for i, line in enumerate(published)
                   if "could not be derived" in line)
    bound = next(i for i, line in enumerate(published)
                 if "declared bound:" in line)
    assert failure < bound, (
        f"the derived FAILURE is published after the caveat: {published}")


def test_an_ERROR_line_does_not_mask_the_derived_FAILURE(
        tmp_path, monkeypatch):
    """`derived_tool_rows` must be read BEFORE the error lines reach the list.

The guard at `derived_tool_rows = bool(out)` exists because the error channel
appends to `out` FIRST (handler/leg scan failures, a few lines above the
emptiness check). Reading `not out` after that append conflates two different
facts: `out` non-empty because a TOOL ROW was derived, versus `out` non-empty
because an ERROR was published. In the error-present, nothing-derived case the
two spellings disagree, and `not out` silently DROPS the derived-failure line —
the under-reporting direction this channel exists to prevent.

⛔ This case is invisible to `test_a_DECLARED_bound_does_not_swallow_the_
DERIVED_failure`, whose fixture produces no error entry: with `out` still empty
at the check, `not out` and `not derived_tool_rows` agree and a revert to
`not out` stays GREEN. Measured: reverting to `if not out` leaves the whole
file green without this test. The error arm is what makes the guard
attributable.
"""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "sdk.py").write_text(
        "class S:\n"
        "    def tortoise_fts_query(self, q, entity_type='point'):\n"
        "        return q\n"
        "    def _pool(self, t):\n"
        "        return self.tortoise_fts_query('x', entity_type=t)\n"
        "    def passes_it(self):\n"
        "        return _safe(self._pool)\n",
        encoding="utf-8")
    (tmp_path / "tortoise" / "mcp_server.py").write_text(
        "def tortoise_health():\n    return {'ok': True}\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    import tortoise.tool_registry as registry
    monkeypatch.setattr(registry, "TOOL_REGISTRY", [])
    # ⛔ An ERROR entry, so `out` is NON-EMPTY by the time the emptiness check
    # runs — the only state in which `not out` and `not derived_tool_rows`
    # disagree. No tool row can be derived either (no handler forwards the arg).
    monkeypatch.setattr(
        probe, "_handlers_forwarding_entity_type",
        lambda: (set(), "UNMEASURED(handler scan failed: synthetic)"))

    published = [e["tools"] for e in probe.vector_leg_surface() if "tools" in e]
    assert any("handler scan failed: synthetic" in line for line in published), (
        f"the fixture no longer publishes the error entry: {published}")
    assert any("could not be derived" in line for line in published), (
        "an error entry in the list masked the derived-failure line, so an "
        "underivable surface reads as a measured one beside an unrelated "
        f"failure: {published}")


def test_a_scan_FAILURE_is_not_a_DECLARED_bound(tmp_path, monkeypatch):
    """The separation's other half: a scan that could not be TAKEN (#11389).

    The separation #11337 promised is two-facts-or-nothing, and only the
    declared half was pinned. An unparseable `sdk.py` must report `error` and
    NO declaration — otherwise a by-reference caveat ("the scan ran") can stand
    in for "the scan failed", which is the shape that reads as a complete table
    with an edge.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "sdk.py").write_text(
        "def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)

    methods, error, declared = probe._sdk_methods_on_vector_leg()
    assert methods == {} and error and "SDK scan failed" in error, (
        f"an unparseable SDK source did not report the scan failure: "
        f"{methods} {error}")
    assert declared is None, (
        "a scan that could not be TAKEN also published a DECLARED bound, so a "
        f"consumer cannot tell the two channels apart: {declared}")


def test_a_label_with_MIXED_stored_widths_refuses_the_byte_figure():
    """One row's width is not the label's (#11088 review round 7, P2).

    A graph that went through an `EMBEDDING_DIM` rotation holds vectors of two
    widths. `stored × width × 4` then multiplies ONE sampled row's size across
    every row — measured 5×1024 + 1×384 → 24,576 B reported for a true 22,016 B —
    which is wrong in an unbounded direction, in the figure the issue exists to
    produce, with nothing marking it as estimated. The module's posture is
    UNMEASURED when the inputs disagree, and here they do.
    """
    # The minority width FIRST, so the `LIMIT WIDTH_PROBE_SAMPLES` window
    # contains it — the fake honours the LIMIT, so where the odd row sits
    # decides whether the probe can see it (the residual is pinned below).
    graph = _CensusGraph(
        catalogue=[],
        labels=["Object"],
        vectors={"Object": (6, [[0.0] * 384] + [[0.0] * 1024] * 5)},
    )
    row = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                              graph_names=["g"])["labels"]["Object"]
    assert str(row["payload"]["derived_payload_B"]).startswith("UNMEASURED"), (
        f"a mixed-width label published a byte figure extrapolated from one row: {row}")
    assert "disagree" in str(row["payload"]["derived_payload_B"]), row
    # …and the COUNT is unaffected: it is a count of rows, not a width.
    assert row["unindexed"] == 6, row


def test_the_mixed_width_refusal_needs_MORE_THAN_ONE_sample(monkeypatch):
    """The width fix's own discriminating power (#11088 review round 9, P2).

    With `WIDTH_PROBE_SAMPLES = 1` the single-row bug returns: the first sampled
    row's width is multiplied across the label and the figure reads as measured.
    Pinning BOTH directions is what makes the refusal attributable to the
    multi-row probe rather than to the fixture.
    """
    vectors = {"Object": (6, [[0.0] * 384] + [[0.0] * 1024] * 5)}
    graph = _CensusGraph(catalogue=[], labels=["Object"], vectors=vectors)
    row = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                              graph_names=["g"])["labels"]["Object"]
    assert str(row["payload"]["derived_payload_B"]).startswith("UNMEASURED"), row

    # ⚠️ THE DECLARED RESIDUAL: a sample window that misses the minority width
    # cannot refuse (5 of 6, minority last). The figure then stands, and `how`
    # must say how many rows it came from — the sample size is what keeps it an
    # observation rather than an implied exhaustive check.
    late = {"Object": (6, [[0.0] * 1024] * 5 + [[0.0] * 384])}
    residual = probe.vector_census(
        _CensusGraph(catalogue=[], labels=["Object"], vectors=late),
        "g", at="2026-11-05T00:00:00Z", graph_names=["g"])["labels"]["Object"]
    assert residual["payload"]["derived_payload_B"] == 6 * 1024 * 4, residual
    assert "5 sampled row(s)" in residual["payload"]["how"], residual["payload"]
    assert "minority" in residual["payload"]["how"], residual["payload"]

    # …and with a SINGLE sample the same fixture reports a measured-looking
    # figure from one row: the regression this test must be able to see.
    monkeypatch.setattr(probe, "WIDTH_PROBE_SAMPLES", 1)
    one = probe.vector_census(
        _CensusGraph(catalogue=[], labels=["Object"], vectors=vectors),
        "g", at="2026-11-05T00:00:00Z", graph_names=["g"])["labels"]["Object"]
    assert one["payload"]["derived_payload_B"] == 6 * 384 * 4, (
        "with a single sample the first row's width is multiplied across the "
        f"label — that is the regression this test must be able to see: {one}")


def _residual_width_census():
    """The census of a label whose minority width falls OUTSIDE the probe window.

    5x1024 + 1x384, the minority LAST: the window sees one width, so the figure
    stands — the case that must explain itself. Returns the `Object` row AND the
    graph, because the claim is about the statement that produced the row.
    """
    graph = _CensusGraph(
        catalogue=[], labels=["Object"],
        vectors={"Object": (6, [[0.0] * 1024] * 5 + [[0.0] * 384])})
    row = probe.vector_census(graph, "g", at="2026-11-07T00:00:00Z",
                              graph_names=["g"])["labels"]["Object"]
    return row, graph


def _residual_width_row():
    return _residual_width_census()[0]


def test_the_width_probe_report_says_the_rows_were_ENGINE_CHOSEN():
    """The probe imposes no order, so the report must say who chose (#11337).

    `S3-VECTOR-CENSUS.md` §2 read *"the first `WIDTH_PROBE_SAMPLES` (5) of
    them"*, while the query carries **no `ORDER BY`** — so "the first 5" is not
    a property it has, and a re-run of the same graph can sample different rows
    and move `derived_payload_B`. The test fake models a deterministic slice,
    which is why the doc and the fake agreed with each other rather than with
    the engine.

    The fix is NOT `ORDER BY`: the window is 5 rows either way, so sorting
    cannot close the residual blindness, and it would have to sort on a property
    every label carries — which the census does not establish — so it would
    claim a stability it cannot guarantee. What the report does instead is say
    the rows were engine-chosen, so a figure that moves has an explanation.
    """
    row, graph = _residual_width_census()
    how = row["payload"]["how"]
    assert "5 sampled row(s)" in how, how
    assert "chosen by the engine" in how, (
        "the report does not say the sampled rows were engine-chosen, so a "
        f"figure that moves between two runs has no explanation: {how}")
    assert "no ORDER BY" in how, (
        f"the report claims rows the query never ordered: {how}")

    # ⛔ AND THE QUERY ITSELF (#11337 review, P3): the sentence above is about a
    # PROPERTY of the statement, so it is asserted against the statement. Pinning
    # only the prose let `ORDER BY n.id` be added to the probe — the natural
    # attempt to stabilise the window — while `how` and the census doc went on
    # asserting a bound the query no longer had: the defect this issue exists to
    # remove, mirrored.
    probe_statements = [q for q in graph.queries if "RETURN n.embedding" in q]
    assert probe_statements, f"the width probe was never issued: {graph.queries}"
    for statement in probe_statements:
        assert "ORDER BY" not in statement.upper(), (
            "the width probe gained an ORDER BY, so the report and the census "
            f"doc now understate what it does — update all three: {statement}")


def _doc_section(text: str, start: str, end: str) -> str:
    r"""The slice of a markdown doc between two H2 headings, both halves required.

    A pin that claims "the doc's §3 states this" must read §3, not the whole
    file: every string in a pasted transcript is also "in the document", so a
    whole-file search cannot falsify a deleted statement (#11389 review, P2).

    ⛔ ANCHORED TO A LINE, AND UNIQUE (#11389 review, P2). A bare substring
    boundary is not a heading: `"## 4."` is INSIDE `"### 4.1"`, so renaming the
    §4 H2 — a one-character edit that leaves `### 4.1` alone — kept the slice
    running past §4's provenance note and the pasted transcript, i.e. widened
    the pin back to precisely the mentions this slice exists to exclude.
    Measured: `## 4. The measurement taken here` -> `## 4 The measurement…`
    kept a DELETED §3 statement green. `^## 3\.` cannot match `### 3.1` (it
    wants a space where the third `#` sits), and requiring exactly ONE match
    makes a heading that is gone, renamed, or repeated inside a pasted block
    red the pin loudly instead of silently moving the boundary.
    """
    def _boundary(heading: str) -> int:
        found = [m.start()
                 for m in re.finditer(rf"(?m)^{re.escape(heading)}", text)]
        assert len(found) == 1, (
            f"the census doc has {len(found)} line-anchored {heading!r} "
            "heading(s), so the §-slice boundary is not unique — a renamed or "
            "duplicated heading must red this pin, never widen it")
        return found[0]

    begin = _boundary(start)
    stop = _boundary(end)
    assert stop > begin, f"{end!r} precedes {start!r} in the census doc"
    return text[begin:stop]


# ⛔ THE RESIDUAL PIN READS THREE FACTS, NOT ONE SUBSTRING (#11389 review, P3).
# The pin's job is to red the census doc when §3 stops saying that the walk does
# not visit a nested `def`'s decorators/defaults — including when it says the
# exact INVERSE. `"not" in sentence` cannot do that: it is a substring test, so
# it is satisfied by `noted`, by a sentence about something else, and by a
# sentence asserting the opposite. Measured GREEN under it, on this doc:
#
#   "…are noted as reported"                        (substring `not`)
#   "…are not exempt — reached from inside the walk   (asserts the INVERSE)
#    and therefore reported"
#   "…are never skipped by this walk"               (inverse)
#   real clause DELETED + "This section does not discuss
#    decorators or defaults"                        (no walk at all)
#
# So the polarity is read as three separate facts: a negation cue that must be a
# WORD, coverage vocabulary the claim is actually about, and the absence of the
# positive reading.
# ⛔ THE NEGATION MUST GOVERN THE CLAIM, not merely occur in the sentence
# (#11437 review, round 3, P1). `_NEGATION.search(sentence)` only asked that a
# `not`/`never`/`outside` token EXIST somewhere, so an inverse passed on the
# strength of a stray negation: measured, all of these were GREEN —
#   "The walk does visit a nested `def`'s decorators and parameter defaults;
#    this is not a gap."
#   "Reported by the walk: a nested `def`'s decorators and parameter defaults,
#    which is not what an earlier draft said."
#   "outside the method body, the walk reports a nested `def`'s …"
# Requiring the negation to sit within two words of a COVERAGE verb closes THOSE
# three — the strays whose cue governed NO coverage verb at all. ⛔ IT CLOSES ONLY
# THOSE, and two later review rounds have had to narrow this claim (#11437 review,
# rounds 6 and 14, P1 and P2). It does not close an inverse whose negation DOES
# govern a coverage verb: a negation that governs a SUBORDINATE clause still
# satisfies the window while the sentence asserts coverage —
# measured GREEN, and now the four rows of the clause-scope class in
# `declared_open`:
#   "…it is not the closure walk that reaches them"   (`walk` is ITSELF a
#                                                      coverage word, so the
#                                                      window is satisfied by
#                                                      the subject of the
#                                                      pseudo-cleft)
#   "it is not the body walk that leaves … unvisited"
#   "it is not true that the walk does not visit …"   (double negation)
#   "the walk does not visit the body without reaching …"
# Those need the predicate to know the sentence's SUBJECT, to read a second
# negation, or to resolve a preposition — scope, not vocabulary — so they are
# DECLARED and not claimed closed.
# Window measured at THIS head over all 49 `must_red` rows: 0 and 2 leave the
# must_red set fully RED (0/49 leak) and keep the real doc a HIT; **3 opens 2 of
# 49**, starting with "are not missed by the walk". ⛔ Window 0 is not free
# either, one set over: it wrongly REDS **10** of the 22 `declared_open` rows.
# And since #11437's round-6 clause-scope rows landed, **window 1 is no longer
# clean at all** — it wrongly REDS 4 of the 22: the two PSEUDO-CLEFT rows (`walk` is
# itself a coverage word, so the cleft's own SUBJECT satisfies the window and the
# predicate needs the second word to find the real verb), the `only ever` ADVERB
# row, and the NOUN complement (`not only the walk's business`). ⚠️ The double-negation and `without`-framed rows are GREEN at window
# 1 AND at window 0 — their coverage verb sits adjacent to a negation (`not
# visit`, `not true … not visit`), so they never depended on the extra word; an
# earlier wording named them and was WRONG, which is why it is corrected here with
# the identity rather than only the count (#11437 review, round 7, P2 — the count
# reproduced and the identity did not, and the fix was claimed in a commit
# message while the text was left unchanged). ⚠️ Window 3 also wrongly GREENs 1
# `declared_blocks` row (`coverage verb outside the walk/visit/reach/report`). So
# at this head **2 is the ONLY clean window**: 0 and 1 each cost `declared_open`
# rows, 3 costs `must_red` rows. (An earlier draft cited "13/13 inverse rows";
# the row set has since grown to 29, and the denominator re-derived against it —
# #11437 review, P3, re-measured round 8.)
# ⛔ EVERY figure in this paragraph was re-derived by MEASUREMENT at the head that
# added the round-9/10/11 rows, because the counts above are inputs and a stale
# input misreports coverage even when the verdict is right (#11437 review, P3).
# The `must_red` set is **49** at this head, not the 29 those rounds inherited: the
# four verdicts reproduced unchanged at 49/22/16 — window 0 leaks 0/49 and reds
# 10/22 `declared_open`; window 1 reds 4/22 (the same four identities named
# above); window 3 opens exactly the two rows named and wrongly GREENs exactly
# 1/16 `declared_blocks` (`coverage verb outside the walk/visit/reach/report`).
# ⛔ Round 12 added seven rows of `must_red` (the six cue-only pseudo-negations and
# `_DEFEATED`'s `incorrect`), round 13 six more (the `un…` privative alternation's
# unrowed words) and round 14 nine more (two `must_red`, six `declared_open` and
# one `declared_blocks`), each previously pinned by pattern text alone — see
# `must_red` — and the sweep was RE-RUN over all four sets at BOTH 41/13/16/15 and
# 47/13/16/15: every verdict above is byte-for-byte the same, only the
# denominators moved (0/34 -> 0/41 -> 0/47 -> 0/49, 2 of 34 -> 2 of 41 ->
# 2 of 47 -> 2 of 49). Stated because "the rows grew and the verdicts did not" is
# exactly the kind of claim this paragraph exists to have measured rather than
# assumed. ⚠️ The historical deltas are written as history, not as live counts
# (the file's convention: a quotation must not match the live-figure pin below).
# Re-derive with the substring substitution on `_NEGATION_GOVERNS`'s window
# (`{0,2}?` -> `{0,N}?`), re-evaluating ALL FOUR sets — never `must_red` alone.
# ⛔ AND THIS PARAGRAPH NOW HAS AN EXECUTOR (#11437 review, round 14, P2): the
# four lines below are parsed by the pin in
# `test_the_residual_pin_separates_the_claim_from_its_inverse` and compared to a
# sweep it RE-RUNS. Before that pin, fifteen single-figure mutations of this
# paragraph (0/47 -> 0/48, 3 opens 2 -> 3 opens 3, 10/16 -> 11/16, 4/16 -> 5/16,
# 1/15 -> 2/15, 47 -> 48, …) each left the whole file GREEN — the denominators
# ARE pinned by the `N `must_red` rows` sentence, but the verdicts read off them
# were not, in the paragraph whose whole purpose is to record why 2 is the only
# clean window. Edit a figure here and the pin reds; re-derive it by measurement:
#   window 0: must_red leak 0/49; declared_open wrongly red 10/22; declared_blocks wrongly green 0/16; real doc HIT
#   window 1: must_red leak 0/49; declared_open wrongly red 4/22; declared_blocks wrongly green 0/16; real doc HIT
#   window 2: must_red leak 0/49; declared_open wrongly red 0/22; declared_blocks wrongly green 0/16; real doc HIT
#   window 3: must_red leak 2/49; declared_open wrongly red 0/22; declared_blocks wrongly green 1/16; real doc HIT
# ⛔ COST, taken deliberately: window 2 makes "…are never TOUCHED by the walk"
# RED, because `touched` is not in the coverage list — a false block bought for
# that false pass, which is the trade this predicate always takes.
# ⛔ `_NEGATION` (a bare `\b(?:not|never|outside)\b`) was DELETED here: after the
# governance rule below it is read nowhere, so a reader who edited it expecting an
# effect got a silent no-op (#11437 review, P3). The past-tense mentions in the
# comments above and beside `must_red` describe the OLD predicate and stay as the
# record of what the governance rule replaced — they are history, not a symbol.
# ⛔ `(?:s|es|ed)?` — NOT `ed?`. `ed?` binds the `?` to the `d`, so it matches only
# `e` or `ed`: the bare words this vocabulary is named for (`walk`, `visit`,
# `reach`, `report`) did NOT match, and an honest present-tense rewording
# ("This walk does not visit …") went RED where the merged predicate was green
# (#11437 review, P2). Measured: 5 present-tense rewordings 1->0 under `ed?`.
# ⛔ `es` is in the alternation for the same reason, one inflection further out:
# without it `(?:s|ed)?` matched `visit`/`visits`/`reach`/`reports` but NOT
# `reaches` — `reach` is the one verb here whose third person is `-ches`, and the
# alternation then carries no match for it. Measured on the row that isolates it
# (`"it never reaches a nested `def`'s decorators and parameter defaults"`, no
# `walk` to fall back on): `False` under `(?:s|ed)?`, `True` under
# `(?:s|es|ed)?`, i.e. an honest statement of the claim was a false RED
# (#11437 review, round 2, P2). ⚠️ A phrasing that also says `the walk …` is NOT
# the evidence: `walk` is itself a coverage word, so that sentence matches under
# either spelling — citing it as proof of `es` was a measurement that does not
# reproduce (#11437 review, P3), corrected to the row above.
# ⛔ AND `ing`, one inflection further again (#11437 review, round 7, P2): without
# it the present CONTINUOUS is unreadable to both the coverage test and the
# governance window, so three honest restatements of the claim were false REDs.
# Measured — `"the walk is not visiting"` / `"…is not reporting"` / `"…is not
# reaching a nested def's decorators and parameter defaults"` were all RED under
# `(?:s|es|ed)?` and are all GREEN with `ing` present. Zero rows in any of the
# four sets move and the real doc is still a HIT.
_COVERAGE = re.compile(r"\b(?:walk|visit|reach|report)(?:s|es|ed|ing)?\b")
# The negation must GOVERN a coverage verb: within two words of one.
_NEGATION_GOVERNS = re.compile(
    r"\b(?:not|never|outside)\b(?:\s+\w+){0,2}?\s+"
    r"(?:walk|visit|reach|report)(?:s|es|ed|ing)?\b")
# ⛔ `_COVERAGE` is RETAINED DELIBERATELY AND IS CURRENTLY IMPLIED, and saying so
# is the point (#11437 review, P3). Measured: `_NEGATION_GOVERNS` already requires
# one of the same four verbs AFTER the negation, so on all 133 candidate sentences
# (the 100 rows across the four sets — 49 / 13 / 22 / 16 — plus the 33 sentences of
# the real §3 under
# this predicate's own `split(". ")` segmentation) there is no case where the
# governance test hits and this one does not — dropping the term from
# `_residual_sentences` changes no verdict. It stays because it is the INDEPENDENT
# check of the "the sentence must carry coverage vocabulary" fact: if a later
# change loosens the governance window (a 0-2 word window, and the comment above
# records that window 3 re-opens "are not missed by the walk"), the coverage
# requirement must not vanish with it. ⚠️ It cannot cause a false PASS — an AND
# term can only reject — so this is belt-and-braces, not the inert guard this
# file reds elsewhere.
# The positive readings. The first three are the INVERSE phrasings that §3's own
# clause is the NEGATION of — the doc says "outside the walk, and therefore NOT
# reported" (measured: `inside the walk` and `therefore reported` appear **0**
# times in the doc), so these three name the sentence that would CONTRADICT it;
# the rest refuse the INVERSE stated in other vocabulary (#11437 review, P1).
# The first cut listed four literals (`not outside` / `not exempt` / `not left`
# / `except`) and the next review measured four MORE that it let through —
# `not excluded`, `not out of scope`, `not omitted from`, `does not fail to
# report` — so the negated half is written as the CLASS it was always meant to
# be: a negation governing a REVERSER. `not outside the walk` reads as "inside
# the walk", so a negated reverser has to be refused as explicitly as the
# affirmatives are. ⛔ This is still a vocabulary list, not a parser: a reverser
# outside it still false-GREENs, which is one of the residuals
# `_residual_sentences` declares.
# ⛔ `except THAT`, not bare `except`: the reverser is the concessive *clause*
# ("visited, except that … are not reported"), while a bare `except` also reds
# the honest "…, except when the outer method runs" — which is the real doc's
# own argument, one rewording away (#11437 review, P3). Measured: the inverse
# row still reds, the honest `except when` row greens.
# ⛔ And the NEGATED PRIVATIVE — `never unvisited`, `not unreported` — is a
# double negative that asserts coverage. #11443 measured these GREEN.
# ⚠️ WHAT REFUSES THEM IS THE GOVERNANCE TEST, not the `un…` list (#11437 review,
# P3, the same correction made beside the rows): the privative is not a coverage
# word, so the negation governs nothing and the sentence is refused BEFORE
# `_AFFIRMED` is consulted — measured, `_NEGATION_GOVERNS.search` is None on both.
# The `un…` list has SEVEN executors, one per word — the dedicated
# "inverse: privative after a governing negation" row (`unreported`) plus the six
# `inverse: privative `un…`` rows added in round 13 — each of which goes GREEN when
# ITS word is ablated from both alternations (#11437 review, round 13, P3:
# `unvisited` had no row, so deleting it from `_AFFIRMED` and from the ablation
# entry left the whole file GREEN while "…, which are never unvisited" became an
# accepted residual). Saying the list "closes them" was a causal claim the
# artifact did not verify; saying it with SEVEN rows is one it does.
# ⛔ And a HEDGE is not a negation of the CLAIM, which is the same shape one step
# out from finding 2's stray `not` (#11437 review, round 2, P2's class, measured
# again here): a pseudo-negation governs a coverage verb while asserting NEITHER
# the exclusion NOR its inverse, because it negates the MODALITY —
# `"it is not sure the walk reaches …"`, `"…does not always visit …"`,
# `"…does not necessarily report …"` were all GREEN, so a §3 re-written into
# hedges went on satisfying the pin. The CLASS is the standard's: NegEx carries an
# explicit PSEUDO-NEGATION termset (marked `pseudo_negations` in `negspacy`'s
# `termsets.py`, `en_clinical` by default), which exists precisely because a cue
# like `not necessarily` does NOT negate the finding it precedes.
# ⚠️ WHAT IS THE STANDARD'S AND WHAT IS OURS, measured on the source rather than
# assumed (#11437 review, P3): of this alternation's nine cues, **exactly one**
# (`not necessarily`) is in that termset. `not sure`, `not always`, `not certain`,
# `not certainly`, `not clear`, `definitely`, `conclusively` and `guaranteed` —
# the other eight — are this file's own EXTENSION of the class; NegEx's own terms
# are `not certain if` / `not certain whether`, not `not certain`. So this is a
# NegEx-STYLE list, not a copy of it; call it "the STANDARD's answer, not an
# invention here" and the claim is
# the opposite of what the source measures. (Source read for #11437:
# `negspacy==1.1.0`, `negspacy/termsets.py`; the pypi page documents the class
# and the default, not the membership.)
# Measured before this alternation: 3/3 hedges GREEN; after: 3/3 RED, with every
# `must_stay_green`, `declared_open` and `declared_blocks` row unmoved.
_AFFIRMED = re.compile(
    r"\binside the walk\b|\btherefore reported\b|\bskipped\b|\bexcept that\b"
    # ⛔ THE CUE IS `(?:not|never)`, LIKE EVERY SIBLING ARM (#11437 review,
    # round 14, P2). This arm was the only one in the alternation that spelled a
    # single literal cue, so a reverser that IS in the vocabulary, governed by
    # `never`, was accepted as a valid residual while asserting coverage —
    # measured GREEN, the false-PASS direction this file says matters:
    #   "a nested `def`'s decorators and parameter defaults are never outside
    #    the walk"                                    (`never outside` = inside)
    #   "the walk never fails to report a nested `def`'s decorators and
    #    parameter defaults"                           (`never fails` = always)
    # Both are RED with this spelling and GREEN with the old one; zero rows move
    # in any of the four sets, because the arm is an AND-side refusal. The two
    # shapes are now `must_red` rows, so a revert reds instead of re-opening them.
    r"|\b(?:not|never)\s+(?:outside|exempt|excluded|omitted|left|out\s+of\s+scope|fail)"
    r"|\b(?:not|never)\s+un(?:visited|reported|reached|walked|covered|mentioned|seen)\b"
    r"|\b(?:not|never)\s+(?:always|necessarily|certain|certainly|sure|clear"
    r"|definitely|conclusively|guaranteed)\b"
    # ⛔ `might not` — A CUE THE TERMSET ALREADY CARRIED, omitted here until now
    # (#11437 review, P3, F4). `negspacy==1.1.0`'s `en['pseudo_negations']`
    # carries **`might not`** and `not only`; this alternation carried neither
    # (the concessive arm below closes `not only`'s shape — measured, `"the walk
    # not only visits …"` is RED). Measured GREEN before this arm: `"the walk
    # might not visit a nested `def`'s decorators and parameter defaults"` — a
    # HEDGE satisfying a pin that protects a TOTAL exclusion, which is the same
    # false PASS the nine cues above exist to refuse. ⛔ SCOPED TO A COVERAGE VERB
    # for the reason the concessive arm is: a bare `might not` fires on an honest
    # trailing aside (`"…; this might not be the only gap"`, measured GREEN under
    # the bare pair and GREEN here).
    r"|\bmight\s+not\s+(?:walk|visit|reach|report)(?:s|es|ed|ing)?\b"
    # ⛔ The CONCESSIVE-SCOPE class (#11437 review, round 6, P1). `not only
    # visit` negates a DEGREE, not the visit — and the sentence's own second
    # clause then ASSERTS the coverage (`… — it also evaluates them`), which is
    # the inverse. Measured GREEN before this arm, on both a bare concessive
    # and one carrying its affirmative clause. It is the word-order twin of the
    # `not the only` row in `must_red`: that row reds only because `walk` sits
    # four words from `not`, so flipping the clause to `the walk does not only
    # visit …` satisfies the governance window and the shape was open one
    # word-order away from a row claiming to close it.
    # ⛔ THE CUE MUST GOVERN A COVERAGE VERB — do not drop that tail (#11437
    # review, round 7, P1). The first cut of this arm was the bare pair
    # `\b(?:not|never)\s+(?:only|merely|just|simply)\b`, and a bare pair fires
    # ANYWHERE in the sentence: the inclusive `not only … but also`, which is how
    # one HONESTLY states an exclusion, then reads as an affirmation. Measured
    # RED-that-should-be-GREEN under the bare pair, GREEN under this one:
    #   "not only the decorators but also the parameter defaults of a nested
    #    `def` sit outside the walk, and are therefore not reported"
    #   "it is not only the decorators that sit outside the walk; the parameter
    #    defaults do too"
    #   "…are not merely outside the walk — they are never reached at all"
    # The claim that made the bare pair look free ("`does not only visit` ALWAYS
    # implies `also visits`, so the arm adds no false block") is true of that
    # PHRASE and was false of the REGEX — a scoped measurement generalised to the
    # pattern, which is the error this file records twice elsewhere.
    # ⛔ AND THIS ARM IS NOT A CLOSURE OF THE CONCESSIVE CLASS — it is a
    # NARROWING, and the narrowest honest state of it is what ships here (#11437
    # review, round 8, P1+P3). Three rounds have now tightened this one arm and
    # each tightening traded a false block for a false pass in the other
    # direction: the bare pair blocked the honest INCLUSIVE, and adding the verb
    # tail stops blocking it while leaving every concessive whose complement is a
    # REVERSER, an ADVERB or a NOUN unred. Measured at this head (RED is the
    # wanted verdict for an inverse):
    #   GREEN, asserted coverage — inverses this arm does NOT reach:
    #     "…are not only outside the walk — they are reported by it"
    #     "…are not merely outside the walk; the walk reports them"
    #     "the walk does not only ever visit …; it also reports them"
    #     "…are not simply outside the walk — the walk visits them"
    #     -> the five `declared_open` rows labelled `open concessive: …`
    #   RED, the claim stated honestly — the false blocks this arm DOES buy:
    #     "…are never merely visited: they are never visited at all"
    #     "the gap is not merely walked past: … are not reported"
    #     "the instrument does not only walk the body: … sit outside every walk"
    #     -> the three `declared_blocks` rows labelled `false block: concessive …`
    # ⛔ CLOSING THESE NEEDS ASSERTION STRUCTURE, NOT A LONGER LIST. Measured, a
    # clause-scope negative lookahead (`(?!\s*[^.;]*\b(?:not|never|outside)\b)`)
    # does rescue the reverser complement, but it leaves `not only EVER visit`
    # GREEN and still reds all three honest shapes above — so it buys three
    # correct verdicts and loses one, which is a wash, not a fix. A vocabulary
    # arm over English prose does not converge; what keeps this honest is that
    # EVERY measured shape in both directions is a ROW in
    # `test_the_residual_pin_separates_the_claim_from_its_inverse`.
    r"|\b(?:not|never)\s+(?:only|merely|just|simply)\s+"
    r"(?:walk|visit|reach|report)(?:s|es|ed|ing)?\b")


# ⛔ THE DEFEATER GATE (#11437 review, P3, F5). The three AND terms above read
# VOCABULARY, so three sentence SHAPES satisfied the pin while asserting
# something else entirely — each measured GREEN at the head that reported them:
#
#   "an earlier draft of this document said the walk does not visit a nested
#    `def`'s decorators and parameter defaults, which is false"
#   "the walk does not visit a nested `def`'s decorators and parameter defaults;
#    in fact it does visit them"
#   "if the walk does not visit a nested `def`'s decorators and parameter
#    defaults, the census would under-report"
#
# The first is not hypothetical in THIS document: §3.1 is built out of exactly
# that construction ("An earlier draft of this document … **That is false**"), so
# a §3 revision note QUOTING the old claim satisfied the pin while the same
# paragraph refuted it — a false PASS in the direction that matters, in this
# file's own house style. A sentence carrying a DEFEATER does not ASSERT the
# claim: it quotes-and-refutes it, reverses it, or states it as a premise. So a
# defeater refuses the sentence outright.
#
# ⚠️ ITS PRICE IS A FALSE BLOCK, taken deliberately (the file's stated policy:
# where the two directions conflict, take the false block) and PINNED as a row —
# an honest `"…are not visited if the outer method does not run"` reds, on the
# same footing as `"…are never touched by the walk"`: the negation must reach a
# coverage verb, and a conditional clause is not the pin's business.
_DEFEATED = re.compile(r"\bis false\b|\bincorrect\b|\bin fact\b|\bif\b")

def _residual_sentences(s3: str) -> list[str]:
    r"""Sentences of §3 that state the walk does NOT visit a nested def's
    decorators/defaults — the claim the pin exists to protect.

    ⛔ THE RESIDUALS THIS CANNOT CLOSE, stated where they are taken. This reads
    VOCABULARY, not assertion structure, so it is wrong in BOTH directions and
    each shape below was measured in #11437's review rather than assumed:

    FALSE-GREEN (it misses the inverse):

    * a DECOY about a walk that is not the body walk — the doc names **three**
      walks (`"the three walks — `direct`, the closure, the mention scan —"`), and
      `"Parameter defaults and decorators are not reported against the retrieval
      walk."` reds nothing while saying nothing about the body walk. ⚠️ "the
      retrieval walk" is a name this doc NEVER uses (measured: 0 occurrences in
      §3 and 0 in `tools/dod_5090_probe.py`), which is why it is a decoy rather
      than a quote — the predicate cannot know which walk names exist, so it has
      to accept any of them. An earlier wording said the doc distinguished FOUR
      walks; it distinguishes three (#11437 review, P3);
    * a sentence about the INSTRUMENT'S OUTPUT rather than the walk's visit —
      `report` is in the coverage list because §3's real clause says "and
      therefore NOT reported", so `"This section has not reported on `def`
      decorators or parameter defaults"` satisfies the pin (#11443, P2).
      Dropping `report` would fix this decoy and red every honest "the scan
      never reports …" rewording — a false block for a false pass, paid for in
      the wrong direction here, so it is declared rather than closed;
    * a CLAUSE-SCOPE inverse — a negation that governs a SUBORDINATE clause, a
      second negation, or a preposition rather than the claim. Governance asks
      only that SOME negation sit within two words of SOME coverage verb, and
      `walk` is ITSELF a coverage word, so a pseudo-cleft satisfies the window on
      its own subject: measured GREEN, `"…the body walk visits them; it is not
      the closure walk that reaches them"`, `"it is not true that the walk does
      not visit them"` (double negation) and `"the walk does not visit the body
      without reaching them"`. Closing these needs the predicate to know the
      sentence's SUBJECT — scope, not vocabulary — so all four are ROWS in
      `declared_open` below (#11437 review, round 6, P1);
    * an inverse whose REVERSER is outside `_AFFIRMED`'s vocabulary — the list is
      a class pattern for negated reversers and negated privatives now, not four
      literals, but it is still a list. A ROW in `declared_open` below
      (#11437 review, P3);
    * a QUOTE-AND-REFUTE, a self-contradiction, or a CONDITIONAL — the three
      shapes `_DEFEATED` now refuses (#11437 review, P3, F5; each measured GREEN
      before it, and each a ROW in `must_red` below). This is the one shape class
      the predicate closes rather than declares, because a defeater is assertion
      structure the vocabulary can see: `is false` / `incorrect` / `in fact` /
      `if`. ⚠️ It does NOT make the predicate sound — a refutation that uses none
      of those four markers (`"…, so the earlier wording was wrong"`) is still
      GREEN, which stays in this list on the same footing as the rest;
    * a HEDGE whose cue is outside `_AFFIRMED`'s pseudo-negation termset. The
      NegEx-style list closes `not always` / `not necessarily` / `not sure` /
      `not certain` / `not clear` …, but an adverb it does not name still reads
      as an exclusion — measured GREEN with that list in place:
      `"the walk does not substantially visit …"`, `"…does not routinely
      report …"`, `"…does not generally reach …"`. Closing this needs SCOPE
      rather than a longer list, so it is declared instead — and all three are
      ROWS in `declared_open` below, not prose (#11437 review, P3).
    * a negation that DOES govern a coverage verb but not the CLAIM — the other
      half of the stray-negation class, which the governance window was claimed
      to have closed and has not (#11437 review, round 14, P2). Four shapes were
      measured GREEN at the head that reported them: a negation governing a
      DIFFERENT object of the verb (`"the body walk does not report the closure's
      frames, but it does report …"`), two walks named in one sentence
      (`"the closure walk does not report …; the body walk reports …"`), a
      TEMPORAL bound (`until` / `unless` / `once` …`the outer method runs`,
      which asserts that the walk does report them later), and an EXCLUSIVE
      complement (`"does not visit anything but …"`). All six are ROWS in
      `declared_open` below. Closing them needs the predicate to know which
      OBJECT a negation scopes over, which is not vocabulary.

    FALSE-BLOCK (it reds the claim, stated honestly):

    * an exclusion stated with none of `not`/`never`/`outside`, or one that says
      `skipped`, or one phrased `"…are never inside the walk"` — `inside the
      walk` is an affirm marker and `never` does not outrank it. All three are
      ROWS in `declared_blocks` below (#11437 review, P3);
    * an honest exclusion whose negation cue is spelled `cannot` (or a
      contraction), which the cue list does not carry — measured RED, and until
      round 14 no row or bullet named the spelling, so a reader had to INFER that
      the three-token bullet excluded a fourth spelling. The census doc's own
      voice uses `cannot` three times, so a §3 reword in its register lands here.
      A ROW in `declared_blocks` below (#11437 review, round 14, P3);
    * an HONEST sentence that uses a marker as an ASIDE — both measured RED
      (#11437 review, round 2, P3): a concessive `"…except that a default is
      evaluated when the outer method runs"`, which trips the `except that`
      reverser that exists to red its inverse twin, and `"…from inside the walk's
      enclosing frame"`, where the `\binside the walk\b` marker matches the
      possessive `walk's`. Declared rather than paid for: narrowing either marker
      re-opens the inverse it was added for. Both are ROWS in `declared_blocks`
      below, not only this prose (#11437 review, round 2, P3);
    * a statement whose coverage verb is outside the `walk/visit/reach/report`
      list — measured, `"…are never TOUCHED by the walk"` reds, because the
      negation must reach one of those four verbs to be read as governing it
      (#11437 review, round 3, P1);
    * an honest restatement split in two by an INLINE ABBREVIATION — `"…, e.g.
      `def inner(cb=self._pool)`, are not reported by the walk"` reds, because
      segmentation is `split(". ")` and `e.g. ` contains it: the fragment that
      keeps the cue loses decorator+default, and the fragment that keeps those
      loses the cue (#11437 review, P3, F3). The real §3 ships that example as a
      parenthetical for exactly this reason, and collapsing it inline — the doc's
      own style elsewhere — turns a doc+code pair that still agrees into a red.
      Declared rather than fixed: an abbreviation-aware split is a bigger change
      than the framing it protects, and the row is in `declared_blocks` below.
      ⚠️ This is the CONCRETE instance of the sentence-UNIT gap declared under
      `_COVERAGE`'s note above ("THE SENTENCE UNIT — `lowered.split('. ')` — IS
      STILL UNPINNED", #11541 D): that declaration covers the unit being too
      COARSE, this row covers the same unit being too FINE on an abbreviation.
      One gap with two measured faces — not two gaps;
    * an honest restatement that carries a DEFEATER MARKER — `"…are not visited
      if the outer method does not run"` reds through `_DEFEATED`, which is the
      price of closing the quote-and-refute/conditional class in the other
      direction (#11437 review, P3, F5). A row in `declared_blocks` below;
    * an HONEST statement whose negation and in-vocabulary verb are separated by
      PUNCTUATION or by ≥3 words — the governance window is 0–2 `\w+`, and `\w+`
      cannot span a comma, so the honest `"…are never, in the body walk,
      visited"` and `"the walk does not, as an earlier draft said, visit …"` both
      red (#11437 review, round 6, P2). Both are ROWS in `declared_blocks` below.
      ⛔ This is the price of the window itself, and widening it to 3 is measured
      to open 2 of the 49 `must_red` rows (`not` + a non-coverage verb, and `not`
      governing an adjective) and to wrongly GREEN 1 `declared_blocks` row, so it
      is declared rather than paid for in the other direction;
    * an HONEST statement stated inside a CONCESSIVE whose cue happens to sit next
      to a coverage verb — `"…are never merely visited: they are never visited at
      all"`, `"the gap is not merely walked past: … are not reported"`, `"the
      instrument does not only walk the body: … sit outside every walk it
      performs"` — all measured RED, and GREEN with the `_AFFIRMED` concessive arm
      ablated, so the arm is the sole cause (#11437 review, round 8, P3). All
      three are ROWS in `declared_blocks` below. ⛔ This is the other half of that
      arm's trade: it stops blocking the honest INCLUSIVE (`not only … but also`)
      and starts blocking the honest CONCESSIVE, and the four inverses it therefore
      leaves GREEN are the `open concessive:` rows in `declared_open`.

    ⛔ NOT a false block, and listed here only to correct an earlier claim
    (#11437 review, P3): a PARTIAL exclusion stated as a HEDGE whose cue is one of
    `_AFFIRMED`'s nine pseudo-negations — `"…are not
    always visited"` — reds, and that is CORRECT, not a price paid. The pin
    protects a TOTAL claim (the walk does not visit them); a hedge asserts a
    weakened one, which is not the claim, so satisfying the pin with it would be
    the 3/3 hedge false-GREEN this change closes. A doc that honestly narrows the
    exclusion must say so as a clause (`"…except when the outer method runs"`,
    which stays green) — if the total claim has actually stopped being true, the
    pin SHOULD red and both the doc and the predicate need updating. This shape
    is pinned in `must_red`, not here.
    ⛔ AND ONLY THAT SHAPE (#11437 review, round 6, P2). This paragraph used to
    say the HEDGE class reds, full stop, 25 lines under the bullet above that
    declares three hedge rows GREEN — `not substantially` / `not routinely` /
    `not generally`. Measured, both halves are true and neither is the class:
    `not always` reds because `always` is IN the termset, `not substantially`
    satisfies the pin because it is not. The limit is the TERMSET, not the
    hedge; the three rows in `declared_open` are where it is pinned, and a reader
    who took this paragraph at face value would delete them as contradictions.

    ⛔ The false-GREEN direction is the one that matters: a missed inverse is a
    false PASS in the file whose purpose is to be false-PASS-free, while a false
    block is loud and cheap. Where the two conflict, take the false block.

    ⛔ AND THE APPROACH ONLY NARROWS. The shapes have been closed one at a time —
    literals → class → privatives → governance → pseudo-negation — and the next
    one (the out-of-termset hedge above) is already measurable. A vocabulary
    predicate over English prose is
    not going to converge; what keeps it honest is that every measured shape is
    a ROW in `test_the_residual_pin_separates_the_claim_from_its_inverse`, so
    each tightening is a test result rather than a claim.

    Pinning any of these exactly would mean pinning §3's wording again, which is
    the failure this file's history is a chain of (#11389 P2/P3): the doc+code
    pair agrees, the pin reds, and the cheapest repair is to guess the phrasing
    from the test body. What is pinned instead is the ROW SET in
    `test_the_residual_pin_separates_the_claim_from_its_inverse`.

    ⛔ AND THE TWO-WORD SUBJECT FILTER IS LOAD-BEARING, SO IT IS ROWED (#11437
    review, round 10, P2). `"decorator" in sentence and "default" in sentence`
    is the only thing that makes this reject a §3 stating HALF its subject, and
    until now NO row exercised either conjunct: every row carries both words, so
    deleting ` and "default" in sentence` (or the `decorator` one) left the whole
    file GREEN while a §3 that silently dropped "and parameter defaults" became a
    false PASS. Both halves are now `declared_blocks` rows (`false block: the
    claim stated with only HALF its subject …`), each RED here and GREEN under
    the matching deletion — so neither conjunct can be removed silently. That is
    the same class as the `ing` executor one commit earlier, in the worse
    direction: that one produced false blocks, this one produces a false PASS.

    ⚠️ THE SENTENCE UNIT — `lowered.split(". ")` — IS STILL UNPINNED, and this
    is DECLARED rather than fixed (#11437 review, round 10, P3). Measured:
    replacing it with `lowered.split("\n")` moves 0 of the rows and reds nothing,
    so a paragraph-sized unit would go unnoticed and weaken the same-sentence
    property this docstring relies on. ⛔ There is NO measured leak today: the
    real-doc deletion and reversal arms still red under the coarser unit. It is
    left to **#11541 D**, which already owns the sentence-granularity question
    (in the opposite direction — that finding is about the split being too FINE),
    and pinning it here would mean extracting the unit into a seam that exists
    only to test itself. Stated so a reader knows it is a known gap, not an
    examined default.
    """
    lowered = s3.lower()
    return [sentence for sentence in lowered.split(". ")
            if "decorator" in sentence and "default" in sentence
            and _NEGATION_GOVERNS.search(sentence)
            and _COVERAGE.search(sentence)
            and not _AFFIRMED.search(sentence)
            and not _DEFEATED.search(sentence)]


def _assert_census_states_the_residual(text: str) -> None:
    """Red the census doc unless some sentence of §3 states the exclusion.

    ⛔ THIS IS A FUNCTION, NOT AN INLINE ASSERTION, FOR A MEASURED REASON
    (#11437 review, round 2, P1). It lived inside the `declared is None` arm of
    `test_the_census_doc_states_the_same_probe_bound_the_report_does`, and on
    this tree that arm is DEAD — `probe._sdk_methods_on_vector_leg()` declares a
    bound (`volunteer_context`), so `declared` is not None. Measured before the
    hoist: deleting §3's residual sentence from the doc left every test in this
    file GREEN, because the predicate returned `[]` into a branch nobody ran.
    That is a false PASS in the artifact whose whole purpose is to be
    false-PASS-free.

    As a function it can be handed a MUTATED document, so the pin's ability to
    fail is itself pinned by `test_the_residual_pin_separates_the_claim_from_its
    _inverse`. An inline assertion can only be "tested" by reading it.
    """
    residual = _residual_sentences(_doc_section(text, "## 3.", "## 4."))
    assert residual, (
        "the doc claims the declaration is total; the walk does not visit a "
        "nested def's defaults/decorators, and no sentence of §3 says so")


def test_the_doc_slice_requires_a_UNIQUE_LINE_ANCHORED_H2():
    """`_doc_section`'s anchoring and uniqueness must be PINNED, not just prose.

    The `#11389` round-3 fix replaced a bare-substring boundary with
    `(?m)^<heading>` plus an exactly-one-match rule, because `"## 4."` is a
    substring of `"### 4.1"` — so renaming the §4 H2 kept the slice running past
    §4's note and the pasted transcript, and a DELETED §3 statement stayed green.
    That measurement lived only in the docstring: reverting `_boundary` to
    `assert heading in text; return text.index(heading)` left the whole file
    green (measured), i.e. the improvement was unpinned and a one-line refactor
    silently reinstated the defect.

    So this test pins the MECHANISM on synthetic text, which needs no edit to the
    real doc and cannot be satisfied by the transcript mentions the slice exists
    to exclude.
    """
    # ⛔ A heading that has been RENAMED away must red, not widen. `## 4.` is a
    # substring of `### 4.1`, so the old form returned here and sliced to EOF.
    renamed = "## 3. a\nstet\n### 4.1 sub\nstet\n"
    with pytest.raises(AssertionError):
        _doc_section(renamed, "## 3.", "## 4.")
    # ⛔ A heading repeated inside a pasted block must red, not pick the first.
    duplicated = "## 3. a\n## 3. pasted\n## 4. b\n"
    with pytest.raises(AssertionError):
        _doc_section(duplicated, "## 3.", "## 4.")
    # ⛔ A DEEPER heading must never satisfy a shallower one: `#### 4.1` is not
    # the §4 H2, so with no real §4 H2 present the boundary is ABSENT and must
    # red (this is the `### 4.1` case that made the substring form widen).
    with pytest.raises(AssertionError):
        _doc_section("## 3. a\n#### 4.1 deep\n", "## 3.", "## 4.")
    # …and the ordinary case still cuts exactly this section.
    assert _doc_section("x\n## 3. a\nb\n## 4. c\n", "## 3.",
                        "## 4.") == "## 3. a\nb\n"


def test_the_census_doc_states_the_same_probe_bound_the_report_does():
    """The doc must state the bound the CODE emits, not a parallel wording.

    The defect was a doc that agreed with a deterministic test fake instead of
    with the engine, so the positive half is derived from the ACTUAL `how`
    string: a fragment of it must appear in §2. Doc and report can then not
    drift apart — and a legitimate rewording of either must move both.
    """
    how = _residual_width_row()["payload"]["how"]
    assert "chosen by the engine" in how and "ORDER BY)" in how, (
        f"the report no longer carries the bound this test pins: {how}")
    fragment = how[how.index("chosen by the engine"):how.index("ORDER BY)") + 9]

    doc = (Path(probe.__file__).resolve().parent.parent / "docs" / "epics"
           / "2026-11-04-5090-vector-layer" / "S3-VECTOR-CENSUS.md")
    assert doc.exists(), f"the census doc moved: {doc}"
    text = doc.read_text(encoding="utf-8")
    assert "the first `WIDTH_PROBE_SAMPLES`" not in text, (
        "the census doc still claims the probe takes 'the first 5' rows, which "
        "is not a property the query has (no ORDER BY)")
    assert fragment in text, (
        f"the census doc does not state the bound the report does: {fragment!r}")

    # ⛔ The BY-REFERENCE half, derived from the RUN rather than restated: when the
    # scan declares a bound, every method it names must appear in the doc's §3;
    # when it declares nothing, the doc must say so. Otherwise deleting the
    # declaration (the cheapest wrong repair) leaves every test green while the
    # census goes on describing a bound the instrument stopped reporting.
    _methods, _error, declared = probe._sdk_methods_on_vector_leg()
    assert _error is None, (
        "the scan could not be TAKEN on the real tree, so the declaration read "
        f"below is measuring the wrong channel: {_error}")
    # ⛔ THE RESIDUAL IS A PROPERTY OF THE WALK, NOT OF THE DECLARATION — so it
    # runs on EVERY tree (#11437 review, round 2, P1). It used to live inside the
    # `declared is None` arm below; on this tree `probe._sdk_methods_on_vector_
    # leg()` DOES declare a bound (`volunteer_context`), so that arm never ran.
    # Measured before this hoist: deleting §3's residual sentence from the census
    # doc left BOTH this test and the predicate's row-set test GREEN — the
    # predicate returned `[]` and nothing asserted on it. That is a false PASS in
    # the artifact whose purpose is to be false-PASS-free, and it is the same
    # dormancy the parent commit named as its third defect while giving only the
    # PREDICATE an executor. This gives the PIN one.
    #
    # ⛔ THE PROPERTY RULE (#11389 review, P2): the arm used to require ONE exact
    # sentence ("nested `def`'s decorators and parameter defaults"), so any
    # legitimate rewording of that clause red a doc+code pair that still agreed.
    # Assert the CLAIM instead, in `_assert_census_states_the_residual`. The
    # measurements that chose its shape:
    #  · same-sentence keeps it falsifiable — a whole-document word search stays
    #    green with the clause deleted, because both words appear elsewhere;
    #  · NOT `"nested" in sentence` either: pinning that ADJECTIVE still red a
    #    rewording to "an **inner** `def`'s decorators and parameter defaults",
    #    which states the same claim (#11389 review, P2);
    #  · and NOT a loosened `(def|function) + (decorator|default)`: measured, that
    #    false-GREENS with the clause deleted — two other §3 sentences hit it
    #    ("…runs the SDK's own default …", "…`:Object` on its default path");
    #  · the polarity is why the reversed doc reds: `"decorator" and "default"`
    #    alone stays GREEN on a sentence asserting the exact inverse (#11389
    #    review, P3);
    #  · and the polarity is read as three separate facts, because
    #    `"not" in sentence` is a substring test that any of these defeats — all
    #    four measured GREEN on this doc before the split (#11389 review, P3):
    #      "…are noted as reported"                       (substring `not`)
    #      "…are not exempt — reached from inside the walk  (asserts the
    #       and therefore reported"                         INVERSE)
    #      "…are never skipped by this walk"                (inverse)
    #      real clause DELETED + "This section does not discuss
    #       decorators or defaults"                        (no walk at all)
    # Measured on this doc: 1 hit with the clause, 1 with the `inner def`
    # rewording, 0 with the clause deleted, 0 with the clause REVERSED
    # ("Inside the walk, and therefore reported").
    _assert_census_states_the_residual(text)

    if declared is None:
        # ⛔ A PROPERTY, not a magic sentence (#11389 review, P3): requiring one
        # exact phrase the doc never documented made this arm red on a doc+code
        # pair that agreed, with the repair being to guess the wording from the
        # test body. The property is that the doc does not claim this method is
        # declared when the scan declares nothing.
        assert "volunteer_context" not in text, (
            "the scan declares nothing and the census doc still presents "
            "`volunteer_context` as a declared reach")
    else:
        assert ("no such shape exists" not in text
                and "no such body mention exists" not in text), (
            "the run declares a by-reference bound while the census doc denies "
            "that any such shape exists — the two describe different instruments")
        # Derived from the DECLARATION's PASSERS, not from the entrant map. A
        # hand-rolled split on "," / "(" dropped every target but the last and
        # injected `getattr` as a name (#11337 review, P2), so this reads the
        # emitted shape directly and pins what it can honestly pin: the passers.
        # A target is already pinned by the two-target assertion above.
        body = declared.split(": ", 1)[1].split(" —")[0]
        passers = [n for n in re.findall(r"(\w+) \(", body) if n != "getattr"]
        assert passers, f"no passer could be read out of the declaration: {declared}"
        # ⛔ SCOPED TO §3, because that is what the doc CLAIMS (#11389 review,
        # P2). `assert name in text` searched the WHOLE document, and
        # `volunteer_context` appears three times in it — §3's doctrine
        # sentence, §4's provenance note, and the pasted transcript block that
        # this PR itself added. So deleting §3's statement left this green on
        # the other two mentions (measured), which is exactly the residual the
        # docstring concedes for a SECOND passer while claiming the primary one
        # is pinned. The slice makes the falsifier match the claim: §3 is where
        # the doc says a passer is declared, so that is what must name it.
        section = _doc_section(text, "## 3.", "## 4.")
        for name in passers:
            assert name in section, (
                f"the run declares {name!r} and §3 of the census doc does not "
                f"name it (it appears elsewhere in the document: "
                f"{name in text}) — a mention in the transcript or §4 is not "
                f"the doc stating the bound")


def test_the_residual_pin_separates_the_claim_from_its_inverse(monkeypatch):
    r"""The residual predicate must RED the inverse, and must not red the claim.

    ⛔ THIS TEST EXISTS BECAUSE THE PIN WAS DORMANT (#11437 review, P2). The
    predicate it guards **lived** in
    `test_the_census_doc_states_the_same_probe_bound_the_report_does`, inside the
    `declared is None` arm, so on a tree whose scan declares a bound — every tree
    where `volunteer_context` is taken by reference, `tortoise/sdk.py` holding
    exactly one such shape — that arm never ran. Measured then: reverting the
    predicate to `"not" in sentence` left the whole file GREEN, so the nine-row
    table in the PR body was a hand-measurement with no executor behind it. Same
    shape as `test_the_doc_slice_requires_a_UNIQUE_LINE_ANCHORED_H2`, which
    exists for the same reason ("that measurement lived only in the docstring").
    ⛔ IT IS NO LONGER DORMANT, and saying so in the present tense would be the
    defect this PR is about: the pin now runs through
    `_assert_census_states_the_residual` from
    `test_the_census_doc_states_the_same_probe_bound_the_report_does` on EVERY
    tree, outside the `if declared is None` branch — measured, deleting §3's
    residual clause reds both that test and this one. This test still earns its
    keep: the doc pin's ability to FAIL is what the mutations at the end of THIS
    TEST pin (not at the end of the file — eight unrelated tests follow it), and
    a row set that names the measured defeats is what keeps the helper's declared
    surface honest. The sets cover every shape four review rounds have measured,
    plus the residuals deliberately left open in both directions.

    Every row — `must_red`, `must_stay_green`, `declared_open` and
    `declared_blocks` alike — is asserted to differ from the original sentence,
    so no row can pass by being the original mistyped (#11437 review, round 2,
    P3).
    """
    def _red(sentence: str) -> bool:
        return _residual_sentences(f"## 3.\n\n{sentence}\n\n## 4.\n") == []

    original = (
        "⚠️ **outside the walk, and therefore not reported:** a nested `def`'s "
        "decorators and parameter defaults (`def inner(cb=self._review_pool)` "
        "*is* a real reach, evaluated when the outer method runs)")
    assert not _red(original), (
        "the claim itself does not satisfy the residual pin, so every row below "
        "is measuring the wrong predicate")

    # ⛔ The substring and inverse rows the split exists to catch. All four were
    # GREEN under `"not" in sentence`, and the last four were still GREEN under
    # the first cut of the split (#11437 review, P1) — a negated REVERSER reads
    # as the inverse, not the claim.
    must_red = {
        "substring `not` inside `noted`":
            "a nested `def`'s decorators and parameter defaults are noted as "
            "reported",
        "inverse: reached from inside the walk":
            "a nested `def`'s decorators and parameter defaults are not exempt "
            "— reached from inside the walk and therefore reported",
        "inverse: never skipped by this walk":
            "a nested `def`'s decorators and parameter defaults are never "
            "skipped by this walk",
        "decoy: no walk at all":
            "this section does not discuss decorators or defaults",
        "inverse: not outside the walk, they ARE reported":
            "a nested `def`'s decorators and parameter defaults are not outside "
            "the walk; they are reported by the enclosing body scan",
        "inverse: except that they are not reported":
            "every decorator and default is visited, except that a nested "
            "`def`'s decorators and defaults are not reported",
        "inverse: not exempt — they are walked and reported":
            "a nested `def`'s decorators and parameter defaults are not exempt "
            "— they are walked and reported",
        "inverse: not left unreported, so they are reported":
            "a nested `def`'s decorators and parameter defaults are not left "
            "unreported by the body walk, so they are reported",
        # ⛔ The next four were GREEN through the first cut of this fix, which
        # listed four literal phrases instead of the CLASS `_AFFIRMED` now is
        # (#11437 review, round 2, P1): a negation governing a reverser reads as
        # the inverse, whichever reverser word it picks.
        # ⚠️ WHAT IS MEASURED TODAY, because the paragraph above is a history and
        # not the current mechanism (#11437 review, P3). Of these four rows, THREE
        # red through `_NEGATION_GOVERNS` and not through this class at all:
        # `not excluded`, `not out of scope` and `not omitted from` all return None
        # from the governance test (the em-dash or `from` puts the coverage verb
        # outside the 0-2 word window), so they red with the class present OR
        # absent. Only `does not fail to report` depends on it. Branch ablation
        # across the whole must_red set: `outside` and `fail` each carry exactly
        # one row; `exempt`, `excluded`, `omitted`, `left` and `out of scope` are
        # INERT today. The class is RETAINED deliberately, on the same footing as
        # `_COVERAGE` above: it is the belt for a loosened governance window, and
        # an AND-side marker cannot cause a false PASS.
        "inverse: not excluded — the walk does report them":
            "a nested `def`'s decorators and parameter defaults are not excluded "
            "— the walk does report them",
        "inverse: not out of scope — the walk reports them":
            "a nested `def`'s decorators and parameter defaults are not out of "
            "scope — the walk reports them",
        "inverse: does not fail to report (double negative)":
            "the walk does not fail to report a nested `def`'s decorators and "
            "parameter defaults",
        "inverse: not omitted from the walk":
            "a nested `def`'s decorators and parameter defaults are not omitted "
            "from the walk; the walk reports them",
        # ⛔ THE SAME CLASS, CUED WITH `never` (#11437 review, round 14, P2).
        # Both were measured GREEN at the previous head because this arm's cue was
        # the single literal `not` while every sibling spells `(?:not|never)` — so
        # its closure of the negated-reverser class was a claim about one cue
        # word, not about the class. Each row goes GREEN when `not` is restored in
        # place of `(?:not|never)`, which is the mutant that re-opened them.
        "inverse: `never outside the walk` (negated reverser, `never` cue)":
            "a nested `def`'s decorators and parameter defaults are never "
            "outside the walk",
        "inverse: `never fails to report` (negated reverser, `never` cue)":
            "the walk never fails to report a nested `def`'s decorators and "
            "parameter defaults",
        # ⛔ The NEGATED PRIVATIVE — a double negative that asserts coverage.
        # #11443 measured all three of these GREEN (#11437 review, P1).
        # ⚠️ MEASURED, and the correction matters (#11437 review, P3): the two
        # rows BELOW do not red through `_AFFIRMED` at all. `_NEGATION_GOVERNS`
        # returns None on both (`never unvisited` / `not unreported`), because the
        # privative is not a coverage word — the negation governs nothing, so the
        # sentence never reaches the affirm test. The earlier wording, "the `un…`
        # alternation in `_AFFIRMED` closes them", was therefore a causal claim
        # the artifact did not verify. The rows that DO exercise those three
        # previously-unexercised alternations are the three that follow it — one
        # per arm: `\btherefore reported\b` (row 1), `\bskipped\b` (row 2), and
        # the `un…` privative (row 3).
        "inverse: never unvisited":
            "a nested `def`'s decorators and parameter defaults are never "
            "unvisited: the closure walk visited them",
        "inverse: not unreported":
            "a nested `def`'s decorators and parameter defaults are not "
            "unreported: the walk reported them",
        # ⛔ ONE ROW PER `_AFFIRMED` ALTERNATIVE THAT HAD NO EXECUTOR (#11437
        # review, P3). `\btherefore reported\b`, `\bskipped\b` and the `un…`
        # privative alternation were each load-bearing and each unexercised: with
        # any one of them replaced by a never-matching pattern, every row and the
        # real-doc assertion stayed GREEN, so that alternation's closure of its
        # shape was a claim rather than a result — the exact defect this test
        # exists to refuse. Measured: each row below is GREEN with its arm
        # removed from `_AFFIRMED` and RED with the arm in place.
        "inverse: `therefore reported` after a non-visit":
            "a nested `def`'s decorators and parameter defaults are not visited "
            "by the body walk, and are therefore reported by the closure walk",
        "inverse: `skipped` by another walk":
            "the body walk does not report a nested `def`'s decorators and "
            "parameter defaults; they are skipped by the closure walk",
        "inverse: privative after a governing negation":
            "the body walk does not report a nested `def`'s decorators and "
            "parameter defaults, which are not unreported by it",
        # ⛔ THE OTHER SIX PRIVATIVE WORDS, EACH GIVEN A ROW (#11437 review,
        # round 13, P3). The `un\u2026` arm lists SEVEN words and only
        # `unreported` was exercised by the row above \u2014 the other six were held
        # up by the pattern text alone, so the same two-place edit round 12 found
        # for `clear` was still silent here. Measured at the head that reported it:
        # deleting `visited` from `_AFFIRMED` AND from its ablation entry left the
        # whole file `151 passed, 1 skipped, rc=0`, byte-identical to baseline,
        # while "\u2026, which are never unvisited" moved from REFUSED to ACCEPTED as
        # a valid residual \u2014 a false PASS, the direction this file says matters.
        # The row must be the COMPOUND shape: a bare "never unvisited" does not
        # exercise the arm (`_NEGATION_GOVERNS` is None on it, as the docstring
        # says), so each row below carries a governing `does not visit` clause as
        # well. Each dies on ITS word's removal and no other's.
        "inverse: privative `unvisited`":
            "the walk does not visit a nested `def`'s decorators and parameter "
            "defaults, which are never unvisited",
        "inverse: privative `unreached`":
            "the walk does not visit a nested `def`'s decorators and parameter "
            "defaults, which are never unreached",
        "inverse: privative `unwalked`":
            "the walk does not visit a nested `def`'s decorators and parameter "
            "defaults, which are never unwalked",
        "inverse: privative `uncovered`":
            "the walk does not visit a nested `def`'s decorators and parameter "
            "defaults, which are never uncovered",
        "inverse: privative `unmentioned`":
            "the walk does not visit a nested `def`'s decorators and parameter "
            "defaults, which are never unmentioned",
        "inverse: privative `unseen`":
            "the walk does not visit a nested `def`'s decorators and parameter "
            "defaults, which are never unseen",
        # ⛔ And the STRAY-NEGATION class (#11437 review, round 3, P1): the
        # negation only has to OCCUR under the old `_NEGATION.search`, so an
        # inverse passed on the strength of a `not` that governed nothing. All
        # five were measured PASSING before `_NEGATION_GOVERNS`.
        "inverse: stray `not` in a trailing clause":
            "the walk does visit a nested `def`'s decorators and parameter "
            "defaults; this is not a gap",
        "inverse: stray `not` in a relative clause":
            "reported by the walk: a nested `def`'s decorators and parameter "
            "defaults, which is not what an earlier draft said",
        "inverse: stray `outside` naming something else":
            "outside the method body, the walk reports a nested `def`'s "
            "decorators and parameter defaults",
        "inverse: `not` + a non-coverage verb":
            "a nested `def`'s decorators and parameter defaults are not missed "
            "by the walk",
        "inverse: a reach the walk takes, not a gap":
            "a reach the walk takes, not a gap: a nested `def`'s decorators and "
            "parameter defaults are visited and reported",
        # ⛔ The two shapes the round-2 reviewer measured GREEN (#11437 review,
        # round 2, P2): a cue governing NEITHER the claim NOR a reverser — a `not`
        # about an adjective (`not surprising`) and one about the walk's OBJECT
        # LIST (`not the only … things`). Closed by `_NEGATION_GOVERNS`
        # (`027742ceb`) and PINNED here, because a shape that leaves the
        # docstring's false-GREEN list has to become a ROW or its closure is a
        # claim again — the failure mode this whole file is a chain of.
        "inverse: `not` governing an adjective, not the walk":
            "it is not surprising that the walk visits a nested `def`'s "
            "decorators and parameter defaults and reports them",
        "inverse: `not the only` governing the object list":
            "a nested `def`'s decorators and parameter defaults are not the "
            "only things the walk visits, and it reports them",
        # ⛔ The CONCESSIVE-SCOPE class (#11437 review, round 6, P1), which is
        # the row above one word-order over: `does not only visit` puts the
        # negation WITHIN the window of the coverage verb, so governance is
        # satisfied and the whole tabulation above reads it as the claim. Two
        # rows because the bare form and the form carrying its own affirmative
        # clause were each measured GREEN separately; the second is the one a
        # real document would actually contain.
        "inverse: concessive `does not only visit` (bare)":
            "the walk does not only visit a nested `def`'s decorators and "
            "parameter defaults",
        "inverse: concessive `does not only visit` + its own affirmative":
            "the body walk does not only visit a nested `def`'s decorators and "
            "parameter defaults — it also evaluates them",
        # ⛔ And the HEDGE class — the same shape one step out, where the cue is a
        # PSEUDO-NEGATION: `not sure` / `not always` / `not necessarily` govern a
        # coverage verb without asserting the exclusion, because they negate the
        # MODALITY. All three were GREEN; the pseudo-negation termset in
        # `_AFFIRMED` (NegEx's own class) reds them, and they are pinned so a
        # `§3` re-written into hedges cannot satisfy the pin again.
        "hedge: `not sure` governs the walk":
            "it is not sure the walk reaches a nested `def`'s decorators and "
            "defaults",
        "hedge: `not always`":
            "the walk does not always visit a nested `def`'s decorators and "
            "defaults",
        "hedge: `not necessarily`":
            "the walk does not necessarily report a nested `def`'s decorators "
            "and defaults",
        # ⛔ `might not` — THE CUE THE TERMSET ALREADY CARRIED (#11437 review,
        # P3, F4). Measured GREEN at the head that reported it, because the
        # pseudo-negation alternation listed nine cues and this is NegEx's tenth
        # (`negspacy`'s `en['pseudo_negations']` carries `might not`, read at the
        # source, not assumed). A hedge satisfying a pin that protects a TOTAL
        # exclusion is the false PASS the whole class exists to refuse, so the
        # omission sat in the direction that matters.
        "hedge: `might not`":
            "the walk might not visit a nested `def`'s decorators and parameter "
            "defaults",
        # ⛔ THE OTHER SIX CUES OF THE PSEUDO-NEGATION CLASS, EACH GIVEN ITS OWN
        # EXECUTOR (#11437 review, round 12, P3). `_AFFIRMED`'s termset carries
        # nine cues and only THREE of them (`sure`, `always`, `necessarily`) had
        # a row: `certain`, `certainly`, `clear`, `definitely`, `conclusively` and
        # `guaranteed` were held up by the pattern-presence literal in
        # `_unasserted_arms` and by nothing else. Measured before this round: with
        # `clear` deleted from `_AFFIRMED` AND from that literal, the whole file
        # stayed GREEN and `"it is not clear the walk reports …"` went back to
        # being a valid residual — a two-place edit, which is the `ing` /
        # `decorator`-`default` class this file already reds, one spelling out. The
        # row set is this class's stated defence and it could not see it, so each
        # cue now has a row that dies on ITS ablation and no other's.
        "hedge: `not certain`":
            "it is not certain the walk visits a nested `def`'s decorators and "
            "defaults",
        "hedge: `not certainly`":
            "the walk does not certainly visit a nested `def`'s decorators and "
            "defaults",
        "hedge: `not clear`":
            "it is not clear the walk reports a nested `def`'s decorators and "
            "parameter defaults",
        "hedge: `not definitely`":
            "the walk does not definitely visit a nested `def`'s decorators and "
            "defaults",
        "hedge: `not conclusively`":
            "the walk does not conclusively report a nested `def`'s decorators "
            "and defaults",
        "hedge: `not guaranteed`":
            "the walk is not guaranteed to visit a nested `def`'s decorators "
            "and defaults",
        # ⛔ THE CONCESSIVE WORD ORDER WITHOUT `does` (#11437 review, P3, F4's own
        # example, which the rows above did not cover): the arm requires the cue
        # to govern a coverage verb and `not only visits` satisfies it, while
        # every concessive row above is spelled `does not only …`. Measured RED
        # with the arm and GREEN with it ablated, so it is this row's executor.
        "inverse: concessive `not only visits` (no `does`)":
            "the walk not only visits a nested `def`'s decorators and parameter "
            "defaults, it reports them",
        # ⛔ THE THREE DEFEATER SHAPES (#11437 review, P3, F5), each measured GREEN
        # at the head that reported it and RED through `_DEFEATED`. The first is
        # this document's OWN house style — §3.1 is built on "An earlier draft of
        # this document … **That is false**" — so a §3 revision note quoting the
        # old claim satisfied the pin while the same paragraph refuted it.
        "inverse: quote-and-refute — the doc quoting its own false claim":
            "an earlier draft of this document said the walk does not visit a "
            "nested `def`'s decorators and parameter defaults, which is false",
        "inverse: self-contradiction — `in fact` reverses it":
            "the walk does not visit a nested `def`'s decorators and parameter "
            "defaults; in fact it does visit them",
        "inverse: CONDITIONAL — the claim is a premise, not an assertion":
            "if the walk does not visit a nested `def`'s decorators and "
            "parameter defaults, the census would under-report",
        # ⛔ `_DEFEATED`'s `incorrect` MARKER, GIVEN A ROW OF ITS OWN (#11437
        # review, round 12, P3). The marker loop below asserts each of the four
        # markers is still IN `_DEFEATED`, so the ONE-place edit was visible — but
        # the two-place edit (delete the marker AND its presence pin) red nothing
        # and re-opened `"…, which is incorrect"` as a valid residual. `is false`,
        # `in fact` and `if` each already carried a row; `incorrect` did not. This
        # one is red only through `\bincorrect\b`: it carries none of the other
        # three markers and no `_AFFIRMED` cue, so it cannot pass on a sibling arm.
        "inverse: quote-and-refute — `which is incorrect`":
            "an earlier draft of this document said the walk does not visit a "
            "nested `def`'s decorators and parameter defaults, which is incorrect",
    }
    for label, sentence in must_red.items():
        assert sentence != original, f"row {label!r} merely restates the claim"
        assert _red(sentence), (
            f"the residual pin is GREEN on an inverse statement ({label}): "
            f"{sentence!r} — the pin's whole purpose is to red this")

    # ⛔ The other direction: honest restatements of the SAME claim must stay
    # GREEN, or the pin is tied to this doc's exact wording — the doc+code-agrees
    # -but-reds class in this file's own history (#11389 review, P2/P3). The
    # present-tense rows are why the coverage alternation is `(?:s|es|ed)?` and not
    # `ed?` (#11437 review, P2; the `es` alternative added in round 2 for
    # `reaches`): under `ed?` they were all RED.
    must_stay_green = {
        "honest: are not reported by this walk":
            "a nested `def`'s decorators and parameter defaults are not reported "
            "by this walk",
        "honest, present tense: does not visit":
            "this walk does not visit an inner def's decorators or defaults",
        "honest, present tense: does not report":
            "the walk does not report nested def decorators or defaults",
        "honest, present tense: does not reach":
            "the walk does not reach a nested def's decorators and defaults",
        "honest, present tense singular: never visits":
            "the walk never visits a nested def's decorators and defaults",
        "honest, present tense singular: never reaches":
            "the walk never reaches a nested def's decorators and defaults",
        "honest, concessive: except when the outer method runs":
            "the walk does not visit a nested def's decorators and defaults, "
            "except when the outer method runs",
        "honest, past tense: are never reached":
            "nested def decorators and defaults are never reached by the walk",
        # `reach` is the one verb whose third person is `-ches`, so the coverage
        # alternation needs the `es` branch — `(?:s|ed)?` leaves `reaches`
        # unmatched and turns this honest row (which names no other coverage word
        # to fall back on) into a false RED (#11437 review, round 2, P2).
        "honest, third person of `reach`":
            "it never reaches a nested `def`'s decorators and parameter defaults",
        "honest, third person of `report`":
            "the scan never reports a nested `def`'s decorators or defaults",
        # ⛔ THE `ing` INFLECTION'S EXECUTOR (#11437 review, round 9, P2). The
        # comment on the alternation quotes these three as measured RED under
        # `(?:s|es|ed)?` and GREEN with `ing` — and until now that was the whole
        # pin, so reverting ONE TOKEN reinstated three false blocks and the file
        # stayed green (measured: the mutant passes 151/1 exactly like the head).
        # `es` got exactly this treatment one inflection earlier (the `reach`
        # row above); `ing` did not, and that asymmetry is the tell. Each row is
        # GREEN here and RED under the `ing`-reverted mutant, so the revert now
        # reds the row-set test.
        "honest, present continuous: is not visiting":
            "the walk is not visiting a nested `def`'s decorators and parameter "
            "defaults",
        "honest, present continuous: is not reporting":
            "the walk is not reporting a nested `def`'s decorators and parameter "
            "defaults",
        "honest, present continuous: is not reaching":
            "the walk is not reaching a nested `def`'s decorators and parameter "
            "defaults",
    }
    for label, sentence in must_stay_green.items():
        assert sentence != original, f"green row {label!r} merely restates the claim"
        assert not _red(sentence), (
            f"the residual pin reds an honest statement of its own claim "
            f"({label}): {sentence!r}")

    # ⛔ THE DECLARED RESIDUALS, pinned AS residuals so they cannot be silently
    # believed closed. If a later change closes one, this assertion reds and the
    # docstring gets updated — the point being that a residual nobody can see is
    # indistinguishable from a fix.
    declared_open = {
        "decoy: retrieval walk":
            "parameter defaults and decorators are not reported against the "
            "retrieval walk",
        "decoy: closure walk":
            "decorator and default nodes are not visited by the closure walk",
        "decoy: the instrument's OUTPUT, not the walk's visit":
            "this section has not reported on `def` decorators or parameter "
            "defaults",
        # ⛔ The out-of-termset HEDGE, declared and now EXECUTED (#11437 review,
        # P3). The docstring names these three as measured GREEN; naming them as
        # prose is not the same as pinning them, so each is a row. They stay open
        # because closing them needs SCOPE, not a longer list: the cue negates the
        # MODALITY and an adverb outside the termset still reads as an exclusion.
        "open hedge: `not substantially` (outside the termset)":
            "the walk does not substantially visit a nested `def`'s decorators "
            "and parameter defaults",
        "open hedge: `not routinely` (outside the termset)":
            "the walk does not routinely report a nested `def`'s decorators "
            "and parameter defaults",
        "open hedge: `not generally` (outside the termset)":
            "the walk does not generally reach a nested `def`'s decorators "
            "and parameter defaults",
        # ⛔ And an inverse whose REVERSER is outside `_AFFIRMED`'s vocabulary
        # (#11437 review, P3). The list is a class pattern now rather than four
        # literals, but it is still a list, so a reverser it does not name is
        # GREEN — measured. Pinned so the limit is a result, not a sentence.
        "open inverse: reverser outside `_AFFIRMED`'s vocabulary":
            "a nested `def`'s decorators and parameter defaults are not visited "
            "by the body walk; the closure walk handles them",
        # ⛔ THE CLAUSE-SCOPE CLASS (#11437 review, round 6, P1). Governance asks
        # only that SOME negation sit within two words of SOME coverage verb — it
        # does not ask that the negation govern the CLAIM. Every row below
        # satisfies the window while asserting the INVERSE. Measured GREEN. They
        # are rows rather than prose because a residual nobody can see is
        # indistinguishable from a fix.
        # ⛔ AND THE REASON THEY ARE OPEN IS A TRADE, NOT AN IMPOSSIBILITY (#11437
        # review, round 7, P3). The round-6 wording here said "none is a
        # vocabulary problem, so no arm in `_AFFIRMED` closes them" — measured
        # FALSE. Each is closed by ONE `_AFFIRMED` alternative, with zero rows
        # moving in any of the four sets and the real doc still a HIT:
        #   double negation  <- \b(?:not|never)\b(?:\s+\w+){0,8}?\s+(?:not|never)\b
        #   `without`-framed <- \bwithout\s+(?:\w+\s+){0,2}?(?:walk|visit|reach|report)...
        #   both pseudo-clefts <- \b(?:not|never)\s+(?:the|a)\s+(?:\w+\s+)?walk\b
        # They are held OPEN deliberately, because each closure buys a false block
        # on an honest phrasing — measured: the walk-noun arm reds "nested def
        # decorators and defaults are not the walk's business", and the `without`
        # arm reds "…does not reach a nested def's decorators or defaults without
        # also walking the body". That trade is this file's stated policy when a
        # shape is in front of it, and it is NOT taken here: these four inverses
        # are contrived phrasings, while the honest twins are ones a real editor
        # would write. Declared is the honest state, but it is a CHOICE with a
        # price, not a limit of the instrument.
        "open scope: pseudo-cleft — `walk` is itself a coverage word":
            "the body walk visits a nested `def`'s decorators and parameter "
            "defaults; it is not the closure walk that reaches them",
        "open scope: pseudo-cleft, negated subject, privative":
            "it is not the body walk that leaves a nested `def`'s decorators "
            "and parameter defaults unvisited",
        "open scope: DOUBLE negation — `not true that … does not visit`":
            "it is not true that the walk does not visit a nested `def`'s "
            "decorators and parameter defaults",
        "open scope: `without`-framed inverse":
            "the walk does not visit the body without reaching a nested "
            "`def`'s decorators and parameter defaults",
        # ⛔ THE CONCESSIVE RESIDUALS (#11437 review, round 8, P1). The
        # `_AFFIRMED` concessive arm requires the cue to govern a COVERAGE VERB,
        # which is what stops it blocking the honest inclusive `not only … but
        # also`. The price is that a concessive whose complement is a REVERSER, an
        # ADVERB or a NOUN is unred — measured GREEN, and with the arm removed
        # entirely still GREEN, so no other arm covers them. Pinned as rows because
        # a residual nobody can see is indistinguishable from a fix, and because
        # this arm has now been tightened three rounds without converging.
        "open concessive: reverser complement — `not only outside … reported`":
            "a nested `def`'s decorators and parameter defaults are not only "
            "outside the walk — they are reported by it",
        "open concessive: reverser complement, second clause names the walk":
            "a nested `def`'s decorators and parameter defaults are not merely "
            "outside the walk; the walk reports them",
        "open concessive: an ADVERB between the cue and the verb (`only ever`)":
            "the walk does not only ever visit a nested `def`'s decorators and "
            "parameter defaults; it also reports them",
        "open concessive: reverser complement, privative second clause":
            "a nested `def`'s decorators and parameter defaults are not simply "
            "outside the walk — the walk visits them",
        "open concessive: a NOUN complement (`not only the walk's business`)":
            "a nested `def`'s decorators and parameter defaults are not only the "
            "walk's business — the walk visits them",
        # ⛔ THE STRAY-NEGATION CLASS IS NOT CLOSED BY THE GOVERNANCE WINDOW, and
        # the paragraph that claims it is has been corrected beside this set
        # (#11437 review, round 14, P2). The window asks only that SOME negation
        # sit within two words of SOME coverage verb — it does not ask that the
        # negation govern the CLAIM'S object, nor that the claim be unqualified.
        # Every row below was measured GREEN at the head that reported it, and
        # each ASSERTS that the walk covers the subject, so each is an inverse
        # the pin accepts — the direction that matters.
        "open scope: negation governs a DIFFERENT object of the coverage verb":
            "the body walk does not report the closure's frames, but it does "
            "report a nested `def`'s decorators and parameter defaults",
        "open scope: two walks named, the second asserts coverage":
            "the closure walk does not report decorators and parameter "
            "defaults; the body walk reports a nested `def`'s decorators and "
            "parameter defaults",
        "open scope: a TEMPORAL marker bounds the negation (`until`)":
            "the walk does not report a nested `def`'s decorators and parameter "
            "defaults until the outer method runs",
        "open scope: a TEMPORAL marker bounds the negation (`unless`)":
            "the walk does not report a nested `def`'s decorators and parameter "
            "defaults unless the outer method runs",
        "open scope: a TEMPORAL marker bounds the negation (`once`)":
            "the walk does not report a nested `def`'s decorators and parameter "
            "defaults once the outer method runs",
        "open scope: an EXCLUSIVE complement (`anything but`)":
            "the walk does not visit anything but a nested `def`'s decorators "
            "and parameter defaults",
    }
    for label, sentence in declared_open.items():
        assert sentence != original, f"decoy row {label!r} merely restates the claim"
        assert not _red(sentence), (
            f"the declared residual ({label}) is no longer open — the predicate "
            "now tells WHICH walk or WHICH subject a sentence is about. That is "
            "better than this test assumed: tighten `must_red` to include it "
            "and update `_residual_sentences`'s docstring")

    # ⛔ AND THE DECLARED FALSE-BLOCKS, pinned the other way. These are honest
    # statements of the claim that the pin REJECTS, each paid for deliberately:
    # `never touched by the walk` is the price of requiring the negation to
    # GOVERN a coverage verb (#11437 review, round 3, P1).
    declared_blocks = {
        "false block: coverage verb outside the walk/visit/reach/report list":
            "nested def decorators and defaults are never touched by the walk",
        # ⛔ AND THE CUE'S SPELLING (#11437 review, round 14, P3). The cue list
        # is `\b(?:not|never|outside)\b`, so an honest exclusion whose negation
        # is `cannot` (or a contraction) has none of those tokens and reds. The
        # bullet above enumerates three SPELLINGS, so a fourth was neither
        # declared nor closed — an inference, not a result. The census doc's own
        # voice uses `cannot` three times, so a §3 rewording in its register lands
        # here; the row makes that a measured false block rather than a gap.
        "false block: the negation cue is spelled `cannot` (outside not/never/outside)":
            "the walk cannot reach a nested `def`'s decorators and parameter "
            "defaults",
        "false block: `never inside the walk`":
            "nested def decorators and defaults are never inside the walk",
        # ⛔ Both measured RED and declared open in the docstring above (#11437
        # review, round 2, P3); pinned AS blocks here for the same reason
        # `declared_open` is pinned — a residual nobody can see is
        # indistinguishable from a fix. The first trips the concessive reverser
        # that exists to red its inverse twin; the second matches the POSSESSIVE
        # `walk's`. Narrowing either marker re-opens the inverse it was added for.
        "false block: concessive `except that …`":
            "the walk does not visit nested `def` decorators and defaults, "
            "except that a default is evaluated when the outer method runs",
        "false block: `inside the walk's` matches the possessive":
            "the walk does not visit nested `def` decorators and defaults "
            "reached from inside the walk's enclosing frame",
        # ⛔ The two shapes the docstring's FALSE-BLOCK list declares as prose
        # without a row (#11437 review, P3): an exclusion stated with NONE of
        # `not`/`never`/`outside`, and one that says `skipped`. Both measured RED,
        # and a residual nobody can see is indistinguishable from a fix.
        "false block: exclusion with no negation cue at all":
            "nested def decorators and parameter defaults are excluded from the "
            "walk",
        "false block: exclusion stated as `skipped`":
            "nested def decorators and parameter defaults are skipped by this "
            "walk",
        # ⛔ THE TWO-WORD SUBJECT FILTER'S EXECUTORS (#11437 review, round 10, P2).
        # `"decorator" in sentence and "default" in sentence` is the only thing
        # that rejects a claim stating HALF its subject, and 0 of the 69 rows the
        # set then held exercised either conjunct — so deleting one left the file GREEN while a
        # §3 that dropped "and parameter defaults" satisfied the pin. Each row
        # below is RED here and GREEN under the matching deletion, which makes the
        # deletion detectable.
        "false block: the claim stated with only HALF its subject (decorators)":
            "a nested `def`'s decorators are not reported by the walk",
        "false block: the claim stated with only HALF its subject (defaults)":
            "a nested `def`'s parameter defaults are not reported by the walk",
        # ⛔ The governance WINDOW's own price (#11437 review, round 6, P2), pinned
        # as rows because prose is what a maintainer deletes: both statements are
        # HONEST and use only in-vocabulary forms, and both red because `\w+`
        # cannot span a comma. Measured RED.
        "false block: honest, negation set off by PUNCTUATION":
            "nested def decorators and defaults are never, in the body walk, "
            "visited",
        "false block: honest, an ASIDE between negation and verb":
            "the walk does not, as an earlier draft said, visit a nested def's "
            "decorators and defaults",
        # ⛔ THE CONCESSIVE ARM'S OWN FALSE BLOCKS (#11437 review, round 8, P3),
        # measured RED at this head and GREEN with the arm ablated — so the arm is
        # the sole cause. Each is the claim stated honestly inside a concessive
        # whose cue happens to sit next to a coverage verb. Rows, not prose, for
        # the reason the whole file exists: a measured shape with no row is a shape
        # the next editor will delete as a contradiction.
        "false block: concessive over a coverage verb (`never merely visited`)":
            "a nested `def`'s decorators and parameter defaults are never merely "
            "visited: they are never visited at all",
        "false block: concessive over a coverage verb (`not merely walked past`)":
            "the gap is not merely walked past: a nested `def`'s decorators and "
            "parameter defaults are not reported",
        "false block: concessive about the INSTRUMENT'S walk":
            "the instrument does not only walk the body: a nested `def`'s "
            "decorators and parameter defaults sit outside every walk it performs",
        # ⛔ THE SEGMENTATION PRICE (#11437 review, P3, F3), measured RED: the split
        # is `split(". ")`, so an inline abbreviation that contains `. ` severs the
        # cue from the words it governs. The real §3 keeps its example in a
        # parenthetical for exactly this reason; the row exists so the limit is a
        # result rather than prose, and so a later abbreviation-aware split can
        # move it into `must_stay_green`.
        "false block: an inline `e.g.` example splits the sentence":
            "a nested `def`'s decorators and parameter defaults, e.g. `def "
            "inner(cb=self._pool)`, are not reported by the walk",
        # ⛔ THE DEFEATER GATE'S PRICE (#11437 review, P3, F5), the other direction
        # of the same rule: closing the conditional may not be free, so the price
        # is measured and pinned instead of discovered later by an editor.
        "false block: an honest conditional about the outer method":
            "the walk does not visit a nested `def`'s decorators and parameter "
            "defaults if the outer method does not run",
    }
    for label, sentence in declared_blocks.items():
        assert sentence != original, (
            f"false-block row {label!r} merely restates the claim")
        assert _red(sentence), (
            f"the declared false-block ({label}) is not a block any more — the "
            "predicate got more tolerant than this test assumed: move the row "
            "into `must_stay_green` and update `_residual_sentences`'s docstring")

    # ⛔ THE ROW SETS MUST NOT BE ABLE TO VANISH (#11437 review, P2 — category A,
    # a false PASS). Measured: replacing the whole 27-row `must_red` literal with
    # `must_red = {}` left this file **FULLY GREEN** — 149 passed, 2 skipped,
    # byte-identical to baseline. Every assertion above ITERATES the set, so an
    # empty set is a loop that runs zero times and the file silently returns to
    # having no discriminating power at all: the pre-#11389 state, where a
    # polarity regression was invisible. "One row per arm" was therefore a claim
    # the artifact could not verify.
    for _set_name, _rows in (("must_red", must_red),
                             ("must_stay_green", must_stay_green),
                             ("declared_open", declared_open),
                             ("declared_blocks", declared_blocks)):
        assert _rows, (
            f"the `{_set_name}` row set is EMPTY: every loop over it runs zero "
            f"times, so this test asserts nothing. A set that can be deleted is "
            f"a set that cannot pin anything")
    # A FLOOR WAS HERE (`len(must_red) >= 27`) AND IT IS RETIRED, NOT RENUMBERED
    # (#11437 review, round 12, P3). It was written when the set held 27 rows;
    # rounds 9-11 grew the set to 34 and the floor never moved, so it left a 7-row
    # hole in the middle of the one guard whose stated job is to make a
    # disappearing falsifier loud. Measured: deleting 7 non-sole-carrier rows AND
    # re-cutting the three stated figures below left this test GREEN (rc=0) — 20 %
    # of the falsifiers could go. Its stated licence, "adding rows is free", was
    # never true either: the figure pins below red on ANY change to the set size,
    # in both directions, so a row cannot be added without the prose being re-cut
    # alongside it. A hand-count figure that no executor derives is precisely the
    # class this file exists to remove, so the floor is deleted rather than
    # re-numbered: the emptiness loop above still reds on total deletion, and the
    # figure pins below red on any partial one until each stated figure is re-cut
    # with it — which is a VISIBLE edit, and visibility is what the pin buys.

    # ⛔ THE STATED DENOMINATORS GET AN EXECUTOR (#11437 review, round 11, P3).
    # It was the retired floor's licence ("adding rows is free") that let the
    # PROSE'S figures drift, and it is why the floor is gone: rounds 9-11 added
    # rows and the paragraph that says "measured at THIS head over all 29 rows of
    # `must_red`" kept its denominator, so one file carried TWO sizes for one set
    # (34 there, 29 there) and the verdicts it reports looked calibrated when they
    # were not. A stated figure
    # whose only executor is a reader is the false-PASS shape this whole file is
    # about, so the two sentences are pinned against the artifact itself: the
    # figures are read out of THIS FILE's own text and compared to the real set
    # sizes. Adding a row therefore costs one re-measurement of the window sweep
    # and the figures — not nothing, which is the point (#11437 review, P3: the
    # same input was corrected one round earlier and drifted again).
    # ⚠️ The pin reads ONE spelling of the live claim — the figure immediately
    # before `` `must_red` rows ``. History that QUOTES an old figure must write it
    # the other way round (`N rows of `must_red``), as this comment does; otherwise
    # a quotation would be read as a claim. That keeps the failure loud and cheap:
    # the pin never guesses which occurrence is the live one.
    _own_source = Path(__file__).read_text(encoding="utf-8")
    _sizes = (len(must_red), len(must_stay_green),
              len(declared_open), len(declared_blocks))
    _stated_sizes = re.findall(
        r"the (\d+) rows across the four sets — "
        r"(\d+) / (\d+) / (\d+) / (\d+)", _own_source)
    assert _stated_sizes, (
        "the row-set census sentence ('the N rows across the four sets — a / b "
        "/ c / d') is gone from this file, so the pin below checks nothing. "
        "That sentence is the one place the four sizes are stated; restore it "
        "or retire this pin deliberately")
    for _total, _a, _b, _c, _d in _stated_sizes:
        assert (int(_total), int(_a), int(_b), int(_c), int(_d)) == \
            (sum(_sizes), *_sizes), (
            f"the census sentence states {_total} rows ({_a}/{_b}/{_c}/{_d}) "
            f"and the artifact holds {sum(_sizes)} ({'/'.join(map(str, _sizes))}). "
            f"Re-derive the figure — and, because the governance-window "
            f"paragraph quotes the same set, re-measure that sweep too "
            f"(`{{0,2}}?` -> `{{0,N}}?` on `_NEGATION_GOVERNS`, ALL FOUR sets)")
    _stated_must_red = re.findall(r"(\d+) `must_red` rows", _own_source)
    assert _stated_must_red, (
        "no sentence in this file states 'N `must_red` rows', so the "
        "governance-window paragraph's denominator is unpinned — restore the "
        "figure or retire this pin deliberately")
    for _figure in _stated_must_red:
        assert int(_figure) == len(must_red), (
            f"a sentence in this file states {_figure} `must_red` rows and the "
            f"set holds {len(must_red)}. The window sweep's denominators and "
            f"its verdicts ('3 opens 2 of N', '0/N leak') are read off this "
            f"count, so re-measure the sweep at {len(must_red)} before "
            f"restating it")

    # ⛔ THE WINDOW SWEEP IS RE-RUN HERE, AND THE PARAGRAPH IS READ AS A RESULT
    # (#11437 review, round 14, P2 — category A, a false PASS on the record that
    # justifies the window). The paragraph above states a sweep whose verdicts
    # are the reason window 2 ships; measured before this pin, FIFTEEN
    # single-figure mutations of it stayed GREEN — `0/47 -> 0/48`,
    # `3 opens 2 -> 3 opens 3`, `10/16 -> 11/16`, `4/16 -> 5/16`, `1/15 -> 2/15`,
    # `**47** -> **48**` and the rest — because the only figures the pins read were
    # the two `N `must_red` rows` sentences and the census tuple. The denominators
    # were pinned; the VERDICTS read off them were a hand-measurement. So the
    # sweep is not restated, it is re-derived: the `{0,N}?` substitution is applied
    # to `_NEGATION_GOVERNS` and every window is re-evaluated over ALL FOUR sets,
    # then compared to the four lines the paragraph states.
    _census_doc_path = (Path(__file__).resolve().parent.parent / "docs" / "epics"
                        / "2026-11-04-5090-vector-layer" / "S3-VECTOR-CENSUS.md")
    _real_s3_text = _doc_section(
        _census_doc_path.read_text(encoding="utf-8"), "## 3.", "## 4.")
    _window_pattern = _NEGATION_GOVERNS.pattern
    assert "{0,2}?" in _window_pattern, (
        "the governance window is no longer `{0,2}?`, so the substitution below "
        "would replace nothing and measure the SAME verdict four times under "
        "four names — the vacuous-pin shape this file reds elsewhere")
    _measured_sweep = {}
    for _window in (0, 1, 2, 3):
        monkeypatch.setattr(
            sys.modules[__name__], "_NEGATION_GOVERNS",
            re.compile(_window_pattern.replace("{0,2}?", f"{{0,{_window}}}?")))
        _measured_sweep[_window] = (
            sum(1 for _s in must_red.values() if not _red(_s)),
            sum(1 for _s in declared_open.values() if _red(_s)),
            sum(1 for _s in declared_blocks.values() if not _red(_s)),
            "HIT" if _residual_sentences(_real_s3_text) else "MISS",
        )
    monkeypatch.setattr(sys.modules[__name__], "_NEGATION_GOVERNS",
                        re.compile(_window_pattern))
    _stated_sweep = re.findall(
        r"window (\d): must_red leak (\d+)/(\d+); declared_open wrongly red "
        r"(\d+)/(\d+); declared_blocks wrongly green (\d+)/(\d+); real doc "
        r"(HIT|MISS)", _own_source)
    assert len(_stated_sweep) == 4, (
        f"the window-sweep table in this file has {len(_stated_sweep)} of the "
        f"four lines the pin parses (windows 0-3 in the form 'window N: "
        f"must_red leak a/b; declared_open wrongly red c/d; declared_blocks "
        f"wrongly green e/f; real doc HIT|MISS'), so the verdicts that justify "
        f"this window are unpinned — restore the table or retire this pin "
        f"deliberately")
    for (_window, _leak, _leak_of, _open, _open_of,
         _blocked, _blocked_of, _hit) in _stated_sweep:
        _want = (int(_leak), int(_open), int(_blocked), _hit)
        assert _want == _measured_sweep[int(_window)], (
            f"window {_window} is stated as must_red leak {_leak}/{_leak_of}, "
            f"declared_open wrongly red {_open}/{_open_of}, declared_blocks "
            f"wrongly green {_blocked}/{_blocked_of}, real doc {_hit} — and the "
            f"re-run measures {_measured_sweep[int(_window)]}. Re-measure, do "
            f"not re-word (#11437 review, round 14, P2)")
        assert (int(_leak_of), int(_open_of), int(_blocked_of)) == \
            (len(must_red), len(declared_open), len(declared_blocks)), (
            f"window {_window}'s denominators are stated as "
            f"{_leak_of}/{_open_of}/{_blocked_of} and the sets hold "
            f"{len(must_red)}/{len(declared_open)}/{len(declared_blocks)}")
    # …and the paragraph's CONCLUSION — that 2 is the only clean window — is a
    # measurement, not a summary. A window is clean when no `must_red` row leaks,
    # no `declared_open` row is wrongly red and the real doc is still a HIT.
    _clean_windows = [
        _w for _w, (_l, _o, _b, _h) in _measured_sweep.items()
        if (_l, _o, _h) == (0, 0, "HIT")]
    assert _clean_windows == [2], (
        f"the paragraph claims 2 is the ONLY clean window and the re-run finds "
        f"{_clean_windows} — recompute the sweep before shipping the window")

    # ⛔ AND THE CANDIDATE-SENTENCE TOTAL, THE ONE POPULATION THE `_COVERAGE`
    # KEEP-DECISION RESTS ON (#11437 review, round 14, P3). The paragraph above
    # justifies retaining `_COVERAGE` with "on all N candidate sentences there is
    # no case where the governance test hits and this one does not", where N is
    # the four sets PLUS the real §3's `split(". ")` sentences. Both figures were
    # re-cut by ARITHMETIC each round and read by no assertion: measured, `121 ->
    # 122` and `30 -> 31` each left the file GREEN. They are derived here, so a
    # row or a §3 sentence moves them visibly.
    _s3_sentence_count = len(_real_s3_text.lower().split(". "))
    _stated_candidates = re.findall(r"on all (\d+) candidate sentences", _own_source)
    assert _stated_candidates, (
        "the `_COVERAGE` justification sentence ('on all N candidate sentences') "
        "is gone, so the population it claims is unpinned")
    for _figure in _stated_candidates:
        assert int(_figure) == sum(_sizes) + _s3_sentence_count, (
            f"the `_COVERAGE` paragraph states {_figure} candidate sentences and "
            f"the derivation gives {sum(_sizes)} rows + {_s3_sentence_count} §3 "
            f"sentences = {sum(_sizes) + _s3_sentence_count} — re-measure, do "
            f"not re-add")
    _stated_s3_sentences = re.findall(r"plus the (\d+) sentences of", _own_source)
    assert _stated_s3_sentences, (
        "the candidate-total sentence no longer states its §3 addend")
    for _figure in _stated_s3_sentences:
        assert int(_figure) == _s3_sentence_count, (
            f"the paragraph states {_figure} §3 sentences and the real doc's own "
            f"`split('. ')` segmentation gives {_s3_sentence_count}")

    # ⛔ AND THE TWO ALTERNATION SIZES THE PROSE USES AS ARGUMENTS (#11437
    # review, round 14, P3). "The `un…` list has SEVEN executors, one per word"
    # and "of this alternation's nine cues, exactly one is the termset's" are
    # counts a reader uses to decide whether an arm is already covered — the same
    # class as the NINE/FIVE tally above, and unpinned until now (measured: every
    # one of them could be changed by one and the file stayed GREEN). They are
    # DERIVED out of the shipped pattern, so adding or removing a cue without
    # touching the prose reds.
    _count_words = {3: "THREE", 4: "FOUR", 5: "FIVE", 6: "SIX", 7: "SEVEN",
                    8: "EIGHT", 9: "NINE", 10: "TEN"}
    _word_counts = {_w: _n for _n, _w in _count_words.items()}
    _privative = re.search(r"un\(\?:(\w+(?:\|\w+)*)\)", _AFFIRMED.pattern)
    assert _privative, (
        "the `un…` privative alternation is no longer a single group of "
        "`word|word|…`, so its size cannot be derived — the 'SEVEN executors' "
        "claim is then unpinned")
    _privative_count = len(_privative.group(1).split("|"))
    _pseudo = re.search(r"\(\?:(always\|necessarily\|[^)]*)\)", _AFFIRMED.pattern)
    assert _pseudo, (
        "the pseudo-negation alternation is no longer a single group, so the "
        "'nine cues' claims cannot be derived")
    _pseudo_count = len(_pseudo.group(1).split("|"))
    for _pattern, _derived in (
            (r"`un…` list has (\w+) executors", _privative_count),
            (r"alternation's (\w+) cues", _pseudo_count),
            (r"listed (\w+) cues", _pseudo_count),
            (r"`_AFFIRMED`'s (\w+) pseudo-negations", _pseudo_count),
            (r"the other (\w+) — are this file", _pseudo_count - 1)):
        _stated = re.findall(_pattern, _own_source)
        assert _stated, (
            f"the sentence matching {_pattern!r} is gone, so the count it "
            f"states is unpinned — restore it or retire this pin deliberately")
        for _figure in _stated:
            assert _word_counts.get(_figure.upper()) == _derived, (
                f"a sentence in this file states {_figure} where the pattern "
                f"holds {_derived} — re-derive the count from `_AFFIRMED`")

    # …and the FIVE alternatives this test claims an executor for are asserted to
    # HAVE one: with that alternative removed from `_AFFIRMED`, at least one
    # `must_red` row must go back to GREEN. `_AFFIRMED` carries NINE top-level
    # alternatives and this loop asserts FIVE, so FOUR are unasserted — and they
    # are not one class: `inside the walk`, `except that`, the negated-reverser
    # class and the pseudo-negation class. (Round 7 corrected SEVEN→EIGHT and
    # three→four: the concessive arm added in round 6 was invisible to both the
    # count and the guard while being the arm that round's whole justification
    # rested on — the arm added most recently is exactly the one a hand-count
    # misses, #11437 review, round 7, P2.)
    # ⛔ THE SAME DEFECT RECURRED ONE ROUND LATER, in this very sentence: the
    # `might not` arm was then added to BOTH `_AFFIRMED` and this loop, and the two
    # inputs above were left at eight/four while the artifact became nine/five
    # (#11437 review, round 11, P3). The derived claim survived — `9 - 5 = 4`, the
    # same four names — which is why the error was invisible: a count can be wrong
    # on both sides and still subtract to the right answer. Hand-counting an
    # alternation is exactly the operation this file reds elsewhere, so the INPUTS
    # are now DERIVED BY THE ASSERTIONS BELOW rather than eyeballed:
    # `_top_level_alternatives` counts the top-level `|` in `_AFFIRMED.pattern`
    # (respecting `\\`-escapes and `[`/`(` nesting) and `len()` reads the ablation
    # tuple, so a hand-count that drifts reds instead of misreporting coverage —
    # and the four unasserted names are pinned by their PATTERNS, not their
    # labels, so a fifth ablation cannot quietly close one of them. The two
    # numbers AS WRITTEN ("carries NINE … asserts FIVE") are pinned against the
    # same derivation, so the sentence itself is now a test result — reverting it
    # to the pre-round-11 EIGHT/four reds.
    # ⚠️ NONE of those four is "inert belt". Measured by ablating each arm to
    # `\bZZZNEVERMATCH\b` and re-evaluating ALL FOUR row sets (not just
    # `must_red`): the concessive row (label `inverse: except that they are not
    # reported`) reds through `except that`; the
    # negated-reverser class carries `does not fail to report` (measured beside
    # that row); the three hedges red through the pseudo-negation class; and
    # `inside the walk` takes TWO `declared_blocks` rows green (labels `false
    # block: `never inside the walk`` and `false block: `inside the walk's`
    # matches the possessive`) — it is INERT FOR
    # `must_red` ONLY, so an ablation scoped to that one set reads the arm as dead.
    # ⚠️ The pointers here are ROW LABELS, not line numbers: the previous two
    # revisions cited `:4948` / `:5164` / `:5175`, and every one of them was
    # invalidated the moment a commit inserted lines above the sets — twice
    # (#11437 review, round 7, P3). A label survives an insertion.
    # ⚠️ An earlier wording here said "the other five negated-reverser branches are
    # deliberately NOT asserted … they are inert today": the count (five, for four)
    # and the class label were both wrong, and telling a reader an arm is unmeasured
    # belt invites exactly the deletion this PR exists to prevent (#11437 review,
    # P3 — corrected twice, because the first correction kept the scoped ablation
    # but dropped its scope).
    def _top_level_alternatives(pattern: str) -> int:
        """Top-level alternatives in a regex source — `|` at nesting depth 0.

        Counted rather than eyeballed because the count IS the derived claim:
        `_AFFIRMED`'s arm tally minus the ablation tuple's length is the number
        of unasserted arms, and both inputs have now been wrong once (#11437
        review, round 11, P3). A `\\`-escape consumes the next character (so
        `\\|` is a literal pipe, not an alternation) and `[...]` is a character
        class, where `|` is literal too.
        """
        depth = 0
        in_class = False
        count = 1
        i = 0
        while i < len(pattern):
            ch = pattern[i]
            if ch == "\\":
                i += 2
                continue
            if in_class:
                if ch == "]":
                    in_class = False
            elif ch == "[":
                in_class = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "|" and depth == 0:
                count += 1
            i += 1
        return count

    _this_module = sys.modules[__name__]
    _pinned_affirmed = _AFFIRMED
    _affirmed_ablations = (
            ("therefore reported", r"\btherefore reported\b"),
            ("skipped", r"\bskipped\b"),
            ("concessive-scope",
             r"\b(?:not|never)\s+(?:only|merely|just|simply)\s+"
             r"(?:walk|visit|reach|report)(?:s|es|ed|ing)?\b"),
            ("negated privative",
             r"\b(?:not|never)\s+un(?:visited|reported|reached|walked|covered"
             r"|mentioned|seen)\b"),
            # ⛔ The `might not` arm added for F4 is asserted by the SAME loop, so
            # the cue that closed a measured false PASS cannot be deleted silently
            # (#11437 review, P3). Measured: ablating it re-GREENs the
            # `hedge: `might not`` row.
            ("`might not` (pseudo-negation)",
             r"\bmight\s+not\s+(?:walk|visit|reach|report)(?:s|es|ed|ing)?\b"))

    # ⛔ THE TWO INPUTS DERIVED, AND THE FOUR UNASSERTED ARMS NAMED BY PATTERN.
    # 9 - 5 = 4 held even while both inputs were wrong, which is why subtraction
    # never caught it; the assertions below pin each input AND the identity of
    # the four, so a fifth ablation (or a new arm) cannot pass as the same claim.
    _unasserted_arms = (
        r"\binside the walk\b",
        r"\bexcept that\b",
        r"\b(?:not|never)\s+(?:outside|exempt|excluded|omitted|left|out\s+of\s+scope|fail)",
        r"\b(?:not|never)\s+(?:always|necessarily|certain|certainly|sure|clear"
        r"|definitely|conclusively|guaranteed)\b")
    assert _top_level_alternatives(_pinned_affirmed.pattern) == 9, (
        f"`_AFFIRMED` now carries "
        f"{_top_level_alternatives(_pinned_affirmed.pattern)} top-level "
        f"alternatives, not the 9 the tally above states — re-derive BOTH inputs "
        f"(this count and the ablation tuple's length) and re-derive the "
        f"derived figure `9 - 5 = 4`. A stale input misreports coverage even "
        f"when the verdicts are right (#11437 review, round 11, P3)")
    assert len(_affirmed_ablations) == 5, (
        f"the `_AFFIRMED` ablation loop has {len(_affirmed_ablations)} entries, "
        f"not the 5 the tally above states — the stated inputs are `NINE` "
        f"alternatives and `FIVE` asserted, so both must be re-derived together")
    assert (_top_level_alternatives(_pinned_affirmed.pattern)
            - len(_affirmed_ablations)) == len(_unasserted_arms), (
        "the unasserted-arm count no longer equals `9 - 5 = 4`: the derived "
        "claim and the four patterns below have diverged")
    for _arm in _unasserted_arms:
        assert _arm in _pinned_affirmed.pattern, (
            f"the unasserted arm `{_arm}` has left `_AFFIRMED`, so the tally "
            f"above now names an arm that does not exist")
        # ⛔ THE COVERAGE TEST IS BEHAVIOURAL, NOT A SUBSTRING (#11437 review,
        # round 12, P2). This asked `_arm in _ablated` — "is the arm's pattern "
        # text a substring of the ablation's pattern text" — which is the wrong
        # DIRECTION: an ablation that NEUTRALISES an arm need not contain it.
        # Measured: changing the `skipped` ablation to `r"\bexcept"` (a PREFIX of
        # the `\bexcept that\b` arm) left this assert GREEN, and the suite GREEN
        # with it, while `except that` silently gained an executor and `skipped`
        # silently lost one — the prose still said "FOUR are unasserted" and
        # `9 - 5 = 4` still held, so the count could not see it. The test is now
        # the one the loop below already performs: apply the ablation and ask
        # whether the arm is still there afterwards.
        for _abl_name, _ablated in _affirmed_ablations:
            _after_ablation = _pinned_affirmed.pattern.replace(
                _ablated, r"\bZZZNEVERMATCH\b")
            assert _arm in _after_ablation, (
                f"`{_arm}` is listed above as UNASSERTED but the `{_abl_name}` "
                f"ablation NEUTRALISES it — the prose and the loop disagree "
                f"about which four arms have no executor")
    # ⛔ AND THE TWO FIGURES AS WRITTEN — the ones a reader actually reads. The
    # derivation above pins the ARTIFACT; without this the SENTENCE could still
    # say EIGHT/four over a nine/five artifact, which is precisely the round-11
    # finding (the counts were fixed in the code two rounds earlier, in prose
    # never). Both word-forms are read out of this file and mapped back to
    # integers, so a wording change the pin cannot parse reds instead of passing.
    # ⚠️ History that QUOTES an old pair writes it as `SEVEN→EIGHT` (arrow) or
    # omits `carries`/`this loop asserts`, so only the live sentence matches.
    _word_numbers = {4: "FOUR", 5: "FIVE", 6: "SIX", 7: "SEVEN", 8: "EIGHT",
                     9: "NINE", 10: "TEN", 11: "ELEVEN", 12: "TWELVE"}
    _number_words = {word: value for value, word in _word_numbers.items()}
    _stated_carries = re.findall(r"`_AFFIRMED` carries (\w+) top-level",
                                 _own_source)
    _stated_asserts = re.findall(r"this loop asserts (\w+)", _own_source)
    assert _stated_carries and _stated_asserts, (
        "the sentence stating `_AFFIRMED`'s arm tally ('`_AFFIRMED` carries "
        "NINE top-level alternatives and this loop asserts FIVE') is gone, so "
        "the two figures a reader reads are unpinned — restore it or retire "
        "this pin deliberately")
    for _figure in _stated_carries:
        assert _number_words.get(_figure.upper()) == \
            _top_level_alternatives(_pinned_affirmed.pattern), (
            f"the tally says `_AFFIRMED` carries {_figure} top-level "
            f"alternatives and the pattern carries "
            f"{_top_level_alternatives(_pinned_affirmed.pattern)} — the count "
            f"and the artifact must be re-derived together")
    for _figure in _stated_asserts:
        assert _number_words.get(_figure.upper()) == len(_affirmed_ablations), (
            f"the tally says this loop asserts {_figure} alternatives and it "
            f"asserts {len(_affirmed_ablations)} — re-derive the pair, not just "
            f"the wrong side (#11437 review, round 11, P3: a count can be wrong "
            f"on BOTH sides and still subtract to the right answer)")

    for _arm_name, _arm in _affirmed_ablations:
        assert _arm in _pinned_affirmed.pattern, (
            f"the `{_arm_name}` alternative has left `_AFFIRMED`, so the guard "
            f"below would silently check nothing")
        monkeypatch.setattr(
            _this_module, "_AFFIRMED",
            re.compile(_pinned_affirmed.pattern.replace(
                _arm, r"\bZZZNEVERMATCH\b")))
        _carried = [lbl for lbl, sent in must_red.items() if not _red(sent)]
        monkeypatch.setattr(_this_module, "_AFFIRMED", _pinned_affirmed)
        assert _carried, (
            f"no `must_red` row depends on `_AFFIRMED`'s `{_arm_name}` "
            f"alternative: with it removed every row still reds, so that "
            f"alternative's closure of its shape is a claim rather than a result")

    # ⛔ …and the DEFEATER GATE gets the same executor (#11437 review, P3, F5).
    # `_DEFEATED` is an AND-side term like `_COVERAGE`, but unlike `_COVERAGE` it
    # is NOT inert: it carries the three measured shapes above. Both facts are
    # asserted — the markers must still be in the pattern, and at least one
    # `must_red` row must go GREEN with the gate removed — because a gate whose
    # markers were deleted in a rewrite would otherwise keep passing on the rows
    # that red through `_AFFIRMED` instead.
    _pinned_defeated = _DEFEATED
    for _marker in (r"\bis false\b", r"\bincorrect\b", r"\bin fact\b", r"\bif\b"):
        assert _marker in _pinned_defeated.pattern, (
            f"the `{_marker}` marker has left `_DEFEATED`, so the shapes it "
            f"was added for are GREEN again with nothing red")
    monkeypatch.setattr(_this_module, "_DEFEATED",
                        re.compile(r"\bZZZNEVERMATCH\b"))
    _carried_defeated = [lbl for lbl, sent in must_red.items() if not _red(sent)]
    monkeypatch.setattr(_this_module, "_DEFEATED", _pinned_defeated)
    assert _carried_defeated, (
        "no `must_red` row depends on `_DEFEATED`: with the gate removed every "
        "row still reds, so the quote-and-refute / self-contradiction / "
        "conditional closures are a claim rather than a result")

    # ⛔ THE PIN ITSELF MUST BE ABLE TO FAIL (#11437 review, round 2, P1).
    # Everything above pins `_residual_sentences`; this pins the ASSERTION built
    # on it, against the REAL document. Without it the assertion can drift back
    # into a branch that does not run and every test here stays GREEN — which is
    # exactly what the round-1 fix shipped, and what was measured before the
    # hoist (deleting §3's sentence red nothing at all).
    root = Path(probe.__file__).resolve().parent.parent
    doc = (root / "docs" / "epics" / "2026-11-04-5090-vector-layer"
           / "S3-VECTOR-CENSUS.md")
    real = doc.read_text(encoding="utf-8")
    _assert_census_states_the_residual(real)  # GREEN on the doc as committed

    # ⛔ DERIVE THE CUE FROM THE PREDICATE, never from the doc's wording (#11437
    # review, round 3, P2). Hard-coding
    # "Outside the walk, and therefore NOT reported:" made this test re-pin the
    # exact phrasing the predicate exists to be TOLERANT of: an honest rewording
    # of §3 kept the PIN green and turned THIS test red — the same
    # doc+code-agrees-but-reds class the row set above is a monument to. The
    # sentences the predicate SELECTS are the cue, whichever words they use.
    s3 = _doc_section(real, "## 3.", "## 4.")
    selected = _residual_sentences(s3)
    # ⚠️ IMPLIED TODAY, RETAINED DELIBERATELY (#11437 review, P3).
    # `_assert_census_states_the_residual(real)` above raises on this same
    # condition first, so as the file stands this assert cannot fire — measured:
    # deleting §3's residual clause reds the suite at the helper's own
    # `assert residual`, never here. It stays because it is what stops the TWO
    # MUTATION LOOPS below from silently running zero iterations if that call is
    # ever removed: an empty `selected` makes both arms vacuous, which is the
    # false-PASS shape this whole test is about. Same footing as `_COVERAGE`.
    assert selected, (
        "this test cannot derive a cue to delete because the predicate selects "
        "nothing on the real doc — with no cue the two mutation arms below run "
        "zero iterations and this test asserts nothing")

    def _mutate(sentence: str, new: str) -> str:
        """Replace ONE occurrence in the DOC's own casing, and prove it landed.

        ⛔ `real.replace(sentence, ...)` is a silent no-op here (#11437 review,
        round 3, P2): `_residual_sentences` returns sentences from the
        LOWERCASED §3, so the lowercased cue never matches the document's own
        casing — measured, `residual after mutation` came back UNCHANGED, i.e.
        the arm below was asserting that an unmutated document still passes. The
        guard that "the sentence is in §3" passed because it was also checked
        against the lowercased slice. Case-insensitive substitution plus an
        assert on the replacement count is what makes this arm mean anything.
        """
        out, hits = re.subn(re.escape(sentence), new, real, count=1,
                            flags=re.IGNORECASE)
        assert hits == 1, (
            f"the selected sentence could not be located in the document "
            f"(case-insensitively), so this arm would test nothing: "
            f"{sentence!r}")
        assert out != real, f"the substitution did not change the document: {new!r}"
        return out

    # ⛔ THE MUTATION IS BUILT OUTSIDE THE `raises` BLOCK (#11437 review, P2).
    # `_mutate` proves itself with `assert hits == 1` / `assert out != real`, and
    # both raise `AssertionError` — the very type `pytest.raises` is catching.
    # Python evaluates call arguments INSIDE the `with` body, so `_mutate(...)`
    # nested there is swallowed as the expected exception: a mutation that does
    # NOT land passes the arm, and the file stays green while asserting that an
    # unmutated document still passes. That is the exact false PASS `_mutate`
    # was added to remove, so the call is evaluated first and only the PREDICATE
    # runs under `raises` (the shape pytest's own docs prescribe: assertions
    # inside the block are interceptable, so a guard must not live there).
    for sentence in selected:
        mutated = _mutate(sentence, "mutated away entirely")
        with pytest.raises(AssertionError):
            _assert_census_states_the_residual(mutated)

    # …and the clause REVERSED, the case #11389 exists for: the sentence KEPT
    # but asserting the inverse must red. ⛔ The replacement KEEPS a negation
    # (#11437 review, round 3, P3): the first version swapped in "Inside the
    # walk, and therefore reported", which DROPS `not`, so the arm fired on the
    # missing negation token exactly like the deletion arm above and proved
    # nothing about polarity. Keeping `NOT` makes this the real test of the
    # affirm markers, and the re-measurement below is why it is kept.
    for sentence in selected:
        flipped = re.sub(r"\b(not|never|outside)\b", "", sentence).strip()
        reversed_doc = _mutate(
            sentence,
            "inside the walk, and therefore NOT reported: " + flipped)
        with pytest.raises(AssertionError):
            _assert_census_states_the_residual(reversed_doc)

    # ⛔ …and the ARMS are pinned against RE-NESTING (#11437 review, P2). Hoisting
    # `_mutate` out of the `with` block is the fix; this assertion is what keeps a
    # later edit from putting it back. The regression is SILENT — the arm keeps
    # passing, asserting that an unmutated document does, and no other test in the
    # file can see it — so "the fix is unpinned and a one-line refactor reinstates
    # the defect" is exactly the class this file exists to refuse (see
    # `test_the_doc_slice_requires_a_UNIQUE_LINE_ANCHORED_H2`, whose own
    # docstring records the same shape being reverted with the file staying green).
    source = Path(__file__).read_text(encoding="utf-8")
    owner = next(
        (node for node in ast.parse(source).body
         if isinstance(node, ast.FunctionDef)
         and node.name == "test_the_residual_pin_separates_the_claim_from_its_inverse"),
        None)
    assert owner is not None, (
        "the mutation arms' own test is not findable by name, so the guard below "
        "would silently check nothing")

    # ⛔ `raises` BOTH WAYS (#11437 review, P3). Matching only the attribute form
    # (`pytest.raises`) left the guard FAILING OPEN on the same regression written
    # with a directly imported `raises`: measured, `with raises(AssertionError):`
    # around a nested `_mutate(...)` reported `[]`, i.e. "no nesting", and the arm
    # went back to passing while proving nothing. A guard that cannot see the
    # shape it exists for is the defect this whole test is about.
    def _is_raises(func: ast.expr) -> bool:
        return ((isinstance(func, ast.Attribute) and func.attr == "raises")
                or (isinstance(func, ast.Name) and func.id == "raises"))
    nested = [
        node.lineno for node in ast.walk(owner)
        if isinstance(node, ast.With)
        and any(isinstance(item.context_expr, ast.Call)
                and _is_raises(item.context_expr.func)
                for item in node.items)
        and any(isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "_mutate"
                for stmt in node.body for inner in ast.walk(stmt))
    ]
    assert not nested, (
        f"`_mutate` is called INSIDE a `pytest.raises(AssertionError)` block at "
        f"line(s) {nested}: `_mutate` itself raises `AssertionError` when the "
        f"substitution does not land, so the block catches its own guard and the "
        f"arm passes while proving nothing. Evaluate `_mutate` first and pass the "
        f"result into the block")

    # ⛔ …and the HOIST itself is pinned, the same way and for the same measured
    # reason (#11437 review, P3, F1). `_assert_census_states_the_residual(text)`
    # runs unconditionally in `test_the_census_doc_states_the_same_probe_bound_the
    # _report_does` — that hoist IS the round-2 P1 fix for #11443's dormancy. It
    # was also the ONE fix in this PR with no distinguishing mutant: at the head
    # that was measured — this branch BEFORE the rebase, when the file collected
    # 152 — deleting that line left `3 failed, 148 passed, 1 skipped` in a scratch
    # copy, byte-identical to the UNMUTATED copy's three failures at that head,
    # i.e. the removal redded NOTHING. ⛔ That head's three are left
    # cause-agnostic on purpose: they are NOT #12045's — at that head neither
    # writer that issue names was in the instrument's population
    # (`_verify_record_vector_width` did not exist until `#11835`; `_emit`
    # existed, but the note that enrols it did not until `#12000`'s F1b note),
    # and a provenance asserted here would be the false record this note exists
    # to prevent. ⛔ The count is SCOPED
    # to that head rather than re-cut, because this file collects a different number now and
    # no run of it can report 152; an unscoped figure here would be the stale-measurement
    # defect §6 of the
    # census doc exists to forbid. What closed the gap is the AST guard below —
    # measured at this tree, deleting the same line ADDS exactly one failure over
    # the same run's own baseline, namely
    # `test_the_residual_pin_separates_the_claim_from_its_inverse` (measured at
    # `origin/main@8300c102c8`, docker lane: baseline `170 passed, 1 skipped` →
    # mutated `1 failed, 169 passed, 1 skipped`, the added failure being that
    # named test). ⛔ That cut SUPERSEDES one taken at `origin/main@0dca7fcad`
    # (`3 failed, 167 passed, 1 skipped` → `4 failed, 166 passed, 1 skipped`):
    # those three were **#12045**'s reds in THIS file (its shard-(d) red proper is
    # the register test, one failure; this file was the second surface the issue
    # records) — `tortoise.api::_emit` and
    # `tortoise.api::_verify_record_vector_width`, both in the instrument's
    # population (both enrolled by `#12000`: `_emit` via its F1b note, the verifier
    # via the docstring example and two body comments that commit rewrote — the
    # verifier itself is `#11835`'s) and both undeclared in the writer register — so
    # a baseline taken while they were live
    # is not a CLEAN baseline: the mutation still adds exactly one failure, but
    # three reds sit beside it and the old text reported them as "repo context",
    # which is the false provenance here. `#12042` declared both, and the
    # figures above are measured at the tree that carries it; the three reds are
    # therefore historical, not repo context, and this note cites them by issue
    # so the next lane does not "correct" one cut into a false record. ⛔ Stated as a
    # DELTA on purpose, not as an absolute split: the split moves with the
    # interpreter and the LANE — the docstring of
    # `test_the_census_doc_collects_what_the_file_collects` (below) says so
    # itself — so an absolute MUTATED split here is the same rot this row exists
    # to forbid. The reason was
    # honest and unstated:
    # the row-set test above performs the same assertion on the real doc, so the
    # residual stays protected while the DORMANCY does not — a one-line refactor
    # can put the call back inside an `if` and this file goes on pinning a claim
    # from a branch that does not run, which is exactly the false PASS the hoist
    # was written to remove.
    # ⛔ TWO facts, both AST rather than prose: (a) a TOP-LEVEL call exists in
    # that test's body, so a `declared is None`-only call cannot satisfy this; and
    # (b) no call to it anywhere in that test sits inside an `If`, so a
    # belt-and-braces duplicate beside the dormant one does not launder it.
    def _is_residual_pin_call(node: ast.AST) -> bool:
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_assert_census_states_the_residual")
    pin_owner = next(
        (node for node in ast.parse(source).body
         if isinstance(node, ast.FunctionDef)
         and node.name ==
         "test_the_census_doc_states_the_same_probe_bound_the_report_does"),
        None)
    assert pin_owner is not None, (
        "the test that owns the hoisted residual pin is not findable by name, "
        "so the guard below would silently check nothing")
    hoisted = [stmt.lineno for stmt in pin_owner.body
               if isinstance(stmt, ast.Expr) and _is_residual_pin_call(stmt.value)]
    conditional = [inner.lineno for node in ast.walk(pin_owner)
                   if isinstance(node, ast.If)
                   for inner in ast.walk(node)
                   if _is_residual_pin_call(inner)]
    assert hoisted, (
        "the residual pin is no longer called at the TOP LEVEL of "
        "`test_the_census_doc_states_the_same_probe_bound_the_report_does`: "
        "either it was deleted, or it went back inside a branch (the "
        "`declared is None` arm is DEAD on this tree, so a call there never runs "
        "and the file pins a claim from a branch nobody executes — #11443's "
        "dormancy, reinstated with the whole file green)")
    assert not conditional, (
        f"`_assert_census_states_the_residual` is called INSIDE an `if` at "
        f"line(s) {conditional} of "
        f"`test_the_census_doc_states_the_same_probe_bound_the_report_does`: the "
        f"residual is a property of the WALK, not of whether the scan declares a "
        f"bound, so the call must run on every tree. On this tree that branch is "
        f"dead, which is the false PASS this hoist removed")


def test_the_census_doc_collects_what_the_file_collects():
    """§6's run record must describe THIS tree, not a branch head (#11389 review).

    The row stated *"collects 128 at #11337's head … six added"* while the tree it
    sat in collected **129**: the figure was stated against a **moving** reference,
    so it went stale on the next test added without any line of the doc changing —
    the defect this PR exists to remove (a figure that describes a measurement it
    no longer matches), one artifact over.

    Bound to the tree instead: the count is re-derived here from the file's own
    `--collect-only`, so adding a test reds this row until the figure is re-cut.
    The passed/skipped **split** is deliberately NOT pinned — it moves with the
    interpreter and the LANE, so pinning it would red on a box rather than on a
    defect. Measured at `eee465029a`: the docker lane reads `185 passed, 1 skipped`
    (the skip is the `<3.12` re-derive; the three `main` reds this docstring used
    to record — `#12045`'s undeclared `tortoise.api::_emit` /
    `_verify_record_vector_width` writers — are cleared by `#12042`, landed as
    `b367c54214a2`) and the carve-out lane reads `184 passed, 2 skipped` (that
    skip plus `tests/_live_utils.py`'s live-graph fixture, which only a lane with
    a `TORTOISE_DB_URI` satisfies) — one more skip and one fewer pass than the docker
    lane, which is the invariant that lasts; the two figures above are bound to the sha
    for exactly that reason. The two splits therefore add to the tree's OWN collected
    count: `186 + 1` and `185 + 2` at THIS tree, both equal to the `187` the pin re-derives
    below (the `eee465029a` pair, `185/1` and `184/2`, adds to that tree's 186 instead),
    which is why the doc states its split as
    lane-bound: a carve-out run must not "correct" the docker lane's figure.
    """
    root = Path(probe.__file__).resolve().parent.parent
    doc = (root / "docs" / "epics" / "2026-11-04-5090-vector-layer"
           / "S3-VECTOR-CENSUS.md")
    assert doc.exists(), f"the census doc moved: {doc}"
    text = doc.read_text(encoding="utf-8")

    run = subprocess.run(
        [sys.executable, "-m", "pytest", str(Path(__file__).resolve()),
         "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=str(root),
    )
    assert run.returncode == 0, (
        f"`--collect-only` on this file failed, so the doc's count cannot be "
        f"checked:\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}")
    match = re.search(r"(\d+) tests? collected", run.stdout)
    assert match, (
        "`--collect-only` printed no collected count, so this pin measured "
        f"nothing: {run.stdout[-2000:]}")
    actual = int(match.group(1))

    # ⛔ EVERY statement of the count, not just the transcript line (`#12015` review):
    # §6 states the figure FOUR times (the run line, the bold prose total, the
    # `122 + … = N` arithmetic, and the `to the same `N` the pin re-derives` clause).
    # Pinning one left the rest free to be re-cut to a different number with this gate
    # green — a doc contradicting itself, which is the defect this pin exists to
    # prevent. ⚠️ The fourth arm was added by a `#12015` review that MEASURED the gap:
    # §6 named `999` at that line and this pin still passed with `3` statements found,
    # so the comment's claim to cover every statement was itself false — the exact
    # "a guard that cannot see the shape it exists for" class this file keeps citing.
    statements = [int(n) for n in re.findall(r"this tree: (\d+) collected", text)]
    statements += [int(n) for n in
                   re.findall(r"collects \*\*(\d+) in the tree", text)]
    # ⛔ The arithmetic total is written ``(`122 + … = 187`)`` — a BACKTICK follows the
    # digits, never a paren (`#12015` review MEASURED the first spelling: it matched
    # nothing, and a dead arm shrinks to the arms that still match, so `= 999` stayed
    # green while the comment claimed all three were pinned).
    statements += [int(n) for n in re.findall(r"= (\d+)`", text)]
    # The splits paragraph's `to the same `187` the pin re-derives` clause — the fourth
    # statement of the same figure, and the one an earlier revision of this pin missed.
    statements += [int(n) for n in
                   re.findall(r"to the same `(\d+)` the pin re-derives", text)]
    assert len(statements) == 4, (
        f"§6 states the collected count of THIS tree four times — the run line, the "
        f"bold prose total, the `122 + … = N` arithmetic, and the `to the same `N` the "
        f"pin re-derives` clause — and this pin found "
        f"{len(statements)}: {statements}. A pattern that matches nothing is a DEAD ARM "
        f"(it shrinks to the arms that still match), and a count stated against a branch "
        f"head ('at #11337's head') describes a measurement the row no longer matches as "
        f"soon as a test is added")
    wrong = [n for n in statements if n != actual]
    assert not wrong, (
        f"§6 states {wrong} where the file collects {actual} (every statement must "
        f"agree: {statements}): the row describes a measurement it no longer matches "
        f"— re-cut EVERY figure, or delete them and let this pin stand alone")


def test_a_pass_through_tool_changes_NOTHING_when_the_affected_set_is_EMPTY():
    """No removable vector, nothing changes — including for the caller-chosen
    tools (#11088 review round 9, P2).

    A graph whose only label is indexed yields an empty affected set, and the
    pass-through branch used to print "any (the caller chooses)" anyway, naming
    `tortoise_search`/`tortoise_query` as changing on a graph where a removal
    would change nothing.
    """
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"], vectors={"Point": (3, [0.0] * 384)},
    )
    sc = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                             graph_names=["g"])["surface_consequence"]
    assert sc["entity_types_whose_query_results_change"] == [], sc
    assert {e["tool"]: e["reaches_affected_labels"] for e in sc["surface"]
            if "tool" in e and e["reaches_affected_labels"]} == {}, (
        "a tool was reported as changing on a graph with nothing to remove: "
        f"{sc['surface']}")


def test_a_non_literal_entity_type_is_UNMEASURED_not_a_measured_no(
        monkeypatch):
    """An `UNKNOWN(...)` member must not intersect to `[]` (#11088 round 9, P2).

    `_types_for` reports a non-literal argument as `UNKNOWN(<expr>)`, never
    guessed. Intersecting that string with the affected types yields `[]`, which
    the printer drops — an unmeasured consequence published as a measured "does
    not change", the direction the module calls dangerous.
    """
    graph = _CensusGraph(
        catalogue=[], labels=["Object"], vectors={"Object": (2, [0.0] * 384)},
    )
    monkeypatch.setattr(
        probe, "_sdk_methods_on_vector_leg",
        lambda: ({"recall_state": {"UNKNOWN(entity_type_var)"}}, None, None))
    sc = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                             graph_names=["g"])["surface_consequence"]
    recall = next(e for e in sc["surface"] if e.get("tool") == "tortoise_recall")
    assert str(recall["reaches_affected_labels"]).startswith("UNMEASURED"), recall


def test_a_tool_that_reaches_the_leg_through_a_HELPER_is_named():
    """A private helper must not hide a tool (#11088 review round 9, P2).

    `review_connections` → `_review_add`/`_review_prune` → `_review_pool` → the
    leg. Keying the scan on the ENCLOSING function named only `_review_pool`,
    which is not a `TOOL_REGISTRY.sdk_method`, so the tool vanished from the
    caller list with no `UNMEASURED` entry anywhere.
    """
    methods, error, _declared = probe._sdk_methods_on_vector_leg()
    assert error is None, error
    assert methods["review_connections"] == {"point"}, methods
    named = {e["tool"] for e in probe.vector_leg_surface() if "tool" in e}
    assert "tortoise_review_connections" in named, sorted(named)


def test_the_census_reports_EVERY_index_KIND_not_only_VECTOR():
    """§3.1 says the run settles the FTS question — so the run must answer it.

    Whether `:Source` carries a FULLTEXT index decides if a lexically-matching
    query survives a vector removal, and `STORAGE-ARCHITECTURE.md` §12.1b's
    dated inventory disagrees with the declared DDL on exactly that. The parse
    kept VECTOR rows only, so the census promised a datum it never read
    (#11088 review round 7, P2).
    """
    fulltext = ["Source", ["_searchText"], {"_searchText": ["FULLTEXT"]},
                {}, "english", [], "NODE", "OPERATIONAL", {}]
    graph = _CensusGraph(
        catalogue=[_index_row("Object", "embedding", 384), fulltext],
        labels=["Object", "Source"],
        vectors={"Object": (1, [0.0] * 384), "Source": (2, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-05T00:00:00Z",
                                 graph_names=["g"])
    assert census["labels"]["Source"]["indexes_on_label"] == {
        "FULLTEXT": ["_searchText"]}, census["labels"]["Source"]
    assert census["labels"]["Object"]["indexes_on_label"] == {
        "VECTOR": ["embedding"]}, census["labels"]["Object"]
    # The vector coverage columns are UNCHANGED by the fuller record: a
    # FULLTEXT index is not a vector index.
    assert census["labels"]["Source"]["indexed"] is False
    assert census["labels"]["Source"]["unindexed"] == 2

    # ⛔ The TEXT line, not only the field: the first version put the closing
    # paren on the whole join instead of on each entry, so two non-VECTOR kinds
    # printed `FULLTEXT(a, RANGE(b)` — malformed in the one output a reader
    # quotes into an approval request, and invisible to a single-kind fixture
    # (verification gate, P2).
    assert probe._non_vector_indexes_line(
        {"FULLTEXT": ["name"], "RANGE": ["created"]}
    ) == "other indexes on this label: FULLTEXT(name), RANGE(created)"
    assert probe._non_vector_indexes_line({"VECTOR": ["embedding"]}) == "", (
        "a VECTOR-only label needs no extra line — `indexed=` already says it")
    assert probe._non_vector_indexes_line("UNMEASURED(x)").startswith("⛔ indexes:")


@pytest.mark.parametrize("dotted", [probe.VECTOR_LEG_RUNNER,
                                    f"{probe.VECTOR_LEG_SDK_METHOD}"])
def test_every_name_the_census_hardcodes_still_RESOLVES(dotted):
    """A hardcoded name must not outlive the thing it names (review, P3).

    `VECTOR_LEG_SDK_METHOD` and `VECTOR_LEG_RUNNER` are the two strings the
    census prints as the PATH a surface change travels. A rename in
    `tortoise/sdk.py` or `tortoise/search_engine.py` would leave the census
    quoting a method that no longer exists — a stale claim in exactly the place
    a reader goes to check the consequence. `takes_entity_type` already reds on
    the SDK rename; this pins both, and the runner had no pin at all.
    """
    import importlib

    if dotted.startswith("tortoise."):
        module_name, _, attr = dotted.rpartition(".")
        module = importlib.import_module(module_name)
    else:
        from tortoise.sdk import TortoiseSDK as module  # the class, not a module
        attr = dotted
    assert hasattr(module, attr), (
        f"the census prints `{dotted}` as the vector leg path, and it does not "
        "resolve — a rename landed without updating the instrument")


def test_an_unresolvable_leg_method_reports_UNMEASURED_not_an_EMPTY_surface(
        monkeypatch):
    """A broken name must not read as "no tool is affected" (review round 2, P2).

    An unresolvable `VECTOR_LEG_SDK_METHOD` empties BOTH the binding set and the
    handler set, so `out` was `[]` — while `takes`, correctly computed to
    `UNMEASURED(...)` in that case, had no entry to ride on and was discarded.
    The surface then read as a measured "no MCP tool can pass `entity_type`"
    beside a non-empty `entity_types_whose_query_results_change`: the
    under-reporting direction, produced by the very drift the resolution pin
    above exists to catch (a pin protects the repo, not the tool as shipped).
    """
    monkeypatch.setattr(probe, "VECTOR_LEG_SDK_METHOD", "no_such_sdk_method")
    surface = probe.vector_leg_surface()
    assert surface, "a broken leg method produced an EMPTY surface (unmeasured reads as empty)"
    assert any("tool" not in entry and str(entry.get("tools", "")).startswith(
        "UNMEASURED") for entry in surface), surface


def test_the_LIVE_census_names_the_unindexed_labels_and_writes_NOTHING():
    """E2E D9 (`04-plan.md` §7): the census, on a real engine (#11088).

    Three claims, none of which a fake can carry:

    1. `Point` reports **indexed** and the served-but-unindexed labels report
       **unindexed with a byte figure** — the population the issue exists to
       quantify, measured rather than inferred from an inventory.
    2. the census **writes nothing**: the label set, the node counts, the edge
       count, the engine's catalogue and every per-label embedding count are
       IDENTICAL across the call (verification row 1 of #11088). ⚠️ This
       snapshot is a corroborating observation, not the proof — it cannot see a
       same-shape property mutation. The proof is the READ-ONLY verb:
       `GRAPH.RO_QUERY` is rejected server-side for a write, and
       `_NoWriteCensusGraph` fails the test on any use of `graph.query`;
    3. each label's `entity_type` values are named, and the surface block lists
       exactly the ones whose query results would change.

    Skips VISIBLY without a live URI (`_live_utils._skip_unless_live_uri`), never
    early-return-green (#942).
    """
    from tests import _live_utils

    _live_utils._skip_unless_live_uri()

    import uuid

    from tortoise.embeddings import EMBEDDING_DIM

    uri = os.environ["TORTOISE_DB_URI"]
    db, _names = probe._connect(uri)
    name = f"census11088_{uuid.uuid4().hex[:12]}"
    graph = db.select_graph(name)
    labelled = ("Point", "Event", "Object", "Source", "Subject")
    vector = [0.1] * EMBEDDING_DIM

    def _create_vector_index() -> None:
        """The product's own two index forms, in its own order (`_ensure_indexes`)."""
        try:
            graph.query(
                f"CALL db.idx.vector.createNodeIndex('Point', 'embedding', "
                f"{EMBEDDING_DIM}, 'cosine')")
        except Exception:
            graph.query(
                "CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                f"OPTIONS {{dimension: {EMBEDDING_DIM}, "
                "similarityFunction: 'cosine'}")

    def _snapshot() -> dict:
        """Everything the census must leave untouched."""
        return {
            "labels": sorted(r[0] for r in graph.ro_query(
                "CALL db.labels()").result_set or []),
            "nodes": graph.ro_query("MATCH (n) RETURN count(n)").result_set[0][0],
            "edges": graph.ro_query("MATCH ()-[e]->() RETURN count(e)"
                                    ).result_set[0][0],
            "catalogue": sorted(
                [r[0], str(r[2])] for r in
                graph.ro_query("CALL db.indexes()").result_set or []),
            "per_label": {
                lab: graph.ro_query(
                    f"MATCH (n:{lab}) WHERE n.embedding IS NOT NULL "
                    "RETURN count(n)").result_set[0][0]
                for lab in labelled
            },
        }

    try:
        _create_vector_index()
        for lab in labelled:
            graph.query(
                f"CREATE (n:{lab} {{id: $id}}) SET n.embedding = vecf32($v)",
                params={"id": f"census-{lab.lower()}", "v": vector})
        names_after = db.list_graphs()

        before = _snapshot()
        census = probe.vector_census(graph, name, graph_names=names_after)
        after = _snapshot()

        # ── 2. the census writes NOTHING ──────────────────────────────────
        assert before == after, (
            "the census changed the graph — the one thing it must never do:\n"
            f"before={before}\nafter={after}")
        assert census["catalogue"] == "CALL db.indexes()", census

        # ── 1. indexed vs unindexed, with BYTES on both ───────────────────
        point = census["labels"]["Point"]
        assert point["indexed"] is True, point
        assert point["unindexed"] == 0, point
        assert point["payload"]["derived_payload_B"] == EMBEDDING_DIM * 4, point

        for lab, ets in (("Event", ["event"]), ("Object", ["object"]),
                         ("Source", ["document", "source"]),
                         ("Subject", ["subject"])):
            row = census["labels"][lab]
            assert row["stored"] == 1, row
            assert row["indexed"] is False, row
            assert row["unindexed"] == 1, row
            assert row["payload"]["derived_payload_B"] == EMBEDDING_DIM * 4, (
                f"an unindexed label has no bytes figure: {row!r}")
            # ── 3. the entity_type values, not just the label ─────────────
            assert row["affected_entity_types"] == ets, row
            assert row["removal_changes_query_results"] is True, row

        sc = census["surface_consequence"]
        assert sc["requested_not_performed"] is True, sc
        # ⛔ FOUR labels, and `Source` counts TWICE (`document` + `source`) while
        # `Point` counts ZERO (it is indexed, so removing nothing changes
        # nothing). `subject` is in the set because the LABEL is measured to
        # hold a vector — #11088 leaves *"what Source/Subject actually hold"* to
        # this census rather than assuming the 2026-09-24 inventory, and the
        # answer is that they hold vectors and are served, so both are affected.
        assert sc["entity_types_whose_query_results_change"] == [
            "document", "event", "object", "source", "subject"], sc
        assert {a["label"] for a in sc["labels"]} == {
            "Event", "Object", "Source", "Subject"}, sc
    finally:
        # Cleanup must not mask a failed assertion (a fake on the failure path
        # would replace the real one). `contextlib.suppress`, not try/pass.
        with contextlib.suppress(Exception):
            graph.delete()


# ── the census half's duplicate-key detector (`#12182` review cycle 7, P2) ────────────────────────
def test_the_census_has_no_DUPLICATE_dict_key_in_its_source() -> None:
    """`WRITER_CENSUS` / `CENSUS_EVIDENCE` / `WRITER_LABEL_OVERRIDES` must not declare a key twice.

    ⛔ WHY THIS EXISTS, NOT AS ADVICE BUT AS A DETECTOR (`#12182` review cycle 7, P2). The register
    half catches a duplicate itself: `load()` raises `DuplicateWriterDeclaration`, and because
    `EMBED_WRITER_CLASSES = load(_DECLARATIONS)` runs at import, a merged tree carrying two entries
    for one key cannot even be imported. Measured on PR #12182: `main`'s `#12016` and that branch each
    added a `tortoise.sdk::create_source` declaration, both branches were green, and the forward merge
    produced a duplicate that the guard rejected at import — the failure was CAUGHT only there.

    The census half has no such guard. A dict literal accepts a repeated key silently and keeps the
    LAST entry, so the same merge shape here does not fail — it quietly selects one of two
    contradicting reasons, and `WRITER_CENSUS`'s class is what row-2 scoring reads (`bypass` /
    `real_bypasses`). Measured: appending a second `create_source` row still imports, still has 23
    keys (re-read at this tree, `#12182` cycle 9 — the figure read 22 on the tree that authored the
    sentence, and `main`'s merge has added rows since), and leaves the census↔evidence key-set
    equality green, with the appended — false — prose as the survivor.

    Read from SOURCE, not from the imported dicts: the import has already collapsed a duplicate, so
    the evidence it could report is gone by the time this test could look at it.
    """
    import ast

    src = Path(probe.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"WRITER_CENSUS", "CENSUS_EVIDENCE", "WRITER_LABEL_OVERRIDES"}
    seen: dict[str, list[str]] = {}
    # ⛔ MODULE TOP LEVEL ONLY (`tree.body`), NOT `ast.walk`. These three tables are module-level
    # declarations, so a top-level scan is both sufficient and the only scope where "the table" is a
    # single object: a walk also visits a same-named literal inside a function, and that literal is
    # not the table.
    # ⚠️ What this scope is NOT: a duplicate fail-open fix. `assert not dupes` below fires PER
    # VISITED NODE, so a duplicate in the real table reds whether or not a decoy literal exists —
    # re-measured on the pre-`tree.body` version: real duplicate plus a 23-key decoy → RED, not
    # clean. `seen` is last-wins, so a decoy CAN shadow the TRAILING checks that read it — the
    # key-count comparison against the runtime dict AND the per-literal key floors below — while a
    # decoy naming a table ABSENT from the top level also satisfies the vacuity `set(seen) == wanted`.
    # Narrowing the scan removes that window; it does not close a duplicate fail-open, because there
    # was none. What a walk DOES get wrong in the other direction: a 5-key function-local literal
    # bearing the same name FALSE-BLOCKS on the floors below (measured), so the narrowing is not
    # cosmetic either.
    # ⚠️ And this is a DIRECTIVE, not a pin: restoring `ast.walk` leaves this file GREEN (measured),
    # so nothing reds if a later lane widens it back. Re-derive the reason before changing the scope.
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
        names = [t.id for t in targets if isinstance(t, ast.Name) and t.id in wanted]
        if not names or not isinstance(node.value, ast.Dict):
            continue
        # ⛔ A key that is NOT a string literal (an alias like `_ALIAS_KEY = "…"` used as a key, a
        # splat, any computed expression) must FAIL here rather than be filtered out. Filtering made
        # the same name declared twice under one literal and once behind the alias collapse to "no
        # duplicate" in BOTH checks below — the alias is skipped, and the literal-vs-runtime count
        # still agrees, because the runtime dict collapses identically. Measured on a copy of this
        # tree (`#12182` cycle 10): `_ALIAS_KEY = "tortoise.api::_emit"` plus `_ALIAS_KEY: (…)` in
        # `WRITER_CENSUS` stayed GREEN here while `main`'s dropped `#9314` pin, which called
        # `ast.literal_eval`, raised on the same file. That is the one shape where this detector was
        # NARROWER than the pin it replaced: fail on it, never re-filter it.
        keys: list[str] = []
        for k in node.value.keys:
            assert isinstance(k, ast.Constant) and isinstance(k.value, str), (
                f"{names[0]} carries a key that is not a string LITERAL ({type(k).__name__}). This "
                "pin reads literal keys, so an aliased or computed key is invisible to it — and a "
                "duplicate hidden behind one is exactly the fail-open this detector exists to stop "
                "(#9314). Spell the key as a literal, or re-derive the detector")
            keys.append(k.value)
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        assert not dupes, (
            f"{names[0]} declares the same key more than once: {dupes}. A dict literal keeps the "
            "LAST silently, so the earlier entry's reason is discarded with nothing red — which is "
            "how a forward merge picks the wrong copy. Delete the stale row and say in a comment "
            "which one survived and why")
        seen[names[0]] = keys
    # A vacuous pass is the failure mode of a source-reading test: if none of the three literals was
    # found AT TOP LEVEL, nothing was checked. All three exist there today, so a MISS means a table
    # was moved into a scope this detector no longer reads — re-derive it deliberately, do not widen
    # it back to `ast.walk` (a decoy literal would then also feed `seen`, masking the vacuity check
    # below and the floor/count assertions that read it, and a smaller one would false-block on the
    # floors).
    assert set(seen) == wanted, (
        f"these census dict literals were not found in {probe.__file__}: "
        f"{sorted(wanted - set(seen))} — the detector checked nothing for them")
    # …and a literal found with a HANDFUL of keys is the same vacuity one level down: the walk above
    # would happily report "no duplicates" over a population that is not the real table. The floors
    # are well under the measured sizes (23 / 24 / 1 at this tree) on purpose — this is a
    # still-the-real-table check, not a pin on a count that moves with every merge.
    floors = {"WRITER_CENSUS": 10, "CENSUS_EVIDENCE": 10, "WRITER_LABEL_OVERRIDES": 1}
    thin = {n: len(keys) for n, keys in seen.items() if len(keys) < floors[n]}
    assert not thin, (
        f"these census literals parsed to fewer literal keys than the real tables carry: {thin} "
        f"(floors {floors}) — the detector would be checking a fragment, not the table")
    # …and the collapsed dict the rest of the probe actually uses must still agree on size, so a
    # delete-one-of-two edit cannot silently shrink the population instead of de-duplicating it.
    for name in sorted(wanted):
        assert len(seen[name]) == len(getattr(probe, name)), (
            f"the {name} literal and the imported dict disagree on their key count — a literal key "
            "is being shadowed by something other than a duplicate")
# ── #12265 (root: #9314) — a duplicated declaration key is a SILENT loser ────
#
# `#11996` landed two duplicate `"tortoise.sdk::create_source"` rows in
# `tools/dod_5090_probe.py`'s census dicts and a third in
# `tortoise/embed_writer_register.py`. The two DICT rows were silently resolved by
# Python's last-wins semantics — the earlier entry, the one a review had pinned,
# simply stopped existing, and no test went red FOR THEM (the register's third copy
# is what red the tree, at import — below). ⛔ The duplicate was born AT THE MERGE,
# not inside either PR: each PR's own tree held ONE row (`#12016` landed at 20:43Z;
# `#11996`'s base predated it), so no run of either PR could see two.
# `main-lint.yml`'s ruff gate (`#2174`, `on: push: branches: [main]`) then red the
# pushed tree — `F601` twice, 16 minutes after the merge — and that lint job was the
# whole detection this class had. The register's own copy raised
# `DuplicateWriterDeclaration` at IMPORT, which is loud but is a different shape (a
# tuple of pairs, checked by `load()` — see
# `tests/test_embed_writer_register.py::test_a_duplicate_key_raises`).
#
# So this is the DICT half: one scan, run wherever a diff SELECTS this module. ⛔ Not
# on every tree that holds a duplicate — selection is the real bound, and it is
# NARROWER than "every file under the scanned roots". Measured per file on the tree that
# measured them, with `select([p], "pull_request")` over every module the scan walks:
#
#   * `tools/` — 140 of 195 DO reach it; 55 do not (measured:
#     `select(["tools/min_signal.py"], "pull_request")` → 91 test files, this one
#     absent — a `tools/`-only diff matching NO pattern is swallowed by
#     NON_PYTHON_PREFIXES before the `core` fallback runs, and
#     `tools/merge_throughput.py` measures the same 91; `tools/ask_spotcheck.py`
#     misses by the OTHER mechanism (`tools/ci_selection.py:1147` makes it an `sdk`
#     named surface, which REPLACES the fallback → 198 test files, this one absent)).
#     ⛔ Do NOT name `tools/who_is_on.py` here — this bullet did, and it is the
#     OPPOSITE of a miss: an UNCLASSIFIED path selects in FULL, so this test DOES run
#     on that diff (measured: `full=True`, `test_files == "ALL"`). `tools/claim_lanes.py`
#     is not a file in this repo (agent-infra's is `tools/fleet/claim_lanes.py`), so it
#     could not have been the one measured. `tools/dod_5090_probe.py`
#     itself does — it is a `CORE_ALSO` entry in `tools/ci_selection.py` (the
#     instrument has to be selected by its own subject; 833 test files, this one
#     PRESENT) — which is the file `#11996`'s duplicate lived in, and why a
#     duplicate born there at a merge IS caught. It is pinned by
#     `test_the_instrument_is_selected_by_its_own_subjects` in this module.
#   * `tortoise/` — 31 of 287 do NOT reach it (`tortoise/ranking.py` → `ep`,
#     `tortoise/hosted_api.py` → `api`+`onboarding`, `tortoise/reader.py` → `sdk`, …).
#     The mechanism is the one this module already documents: a named-surface match
#     REPLACES the `core` fallback that would otherwise sweep this file in.
#   * `tests/` — 1,493 of 1,494 reach it; the exception is `tests/_html_links.py`
#     (`onboarding`).
#
# So the honest bound is per root and NOT a rule you can generalise: any full-lane run
# (push and schedule select in FULL, which is what makes the born-at-merge case
# catchable), plus a per-file `pull_request` selection that covers every `tests/`
# module but one, 140 of 195 `tools/` modules (`tools/dod_5090_probe.py` — the file
# `#11996`'s duplicate lived in — among them), and 256 of 287 `tortoise/` modules.
# Anything outside that is lint's alone.

#: Roots the declaration dicts live under today. ⛔ NOT the whole repo: `battery/`,
#: `graph-scripts/`, `docs/`, `eval/` and several smaller roots sit outside these three,
#: so this scan does not reach a duplicate declared there. Stated rather than called
#: "repo-wide" because the difference is what a reader would rely on.
#:
#: ⛔ Outside these roots a duplicate is not reliably caught by LINT either: `ruff`'s
#: `F601` lands on the repeated (surviving) key, and a `noqa` marker naming `F601` on
#: that line suppresses it — which is exactly the live shape this change found and
#: removed in `tests/test_commit_schema.py`. So "lint would still catch it there" is
#: false for the one shape we have actually observed.
#:
#: ⛔ THE CANONICAL ALTERNATIVE, stated so this scan is not read as the only catcher
#: and not read as a substitute for the linter: the standard answer to a suppressible
#: rule is to run the rule UNSUPPRESSED — `ruff check --select F601 --ignore-noqa`
#: (measured on this tree: `All checks passed`, and no `noqa` directive naming F601
#: survives to un-suppress, so it costs nothing). What this scan adds is what lint
#: cannot: a red that no marker can defuse, one that runs in the pytest shard without
#: depending on the lint step's selection, and one whose RED arm is provable by a test
#: — so it is a COMPLEMENT to lint, never a replacement. ⛔ That step must stay
#: rule-SCOPED: a repo-wide `--ignore-noqa` would un-baseline the WHOLE suppression
#: baseline — measured on the tree this note was written on, **4,452** suppressed
#: findings (the 242 markers `#1685` added on 2026-08-25 are where it started, not the
#: size it reached) — i.e. it would reverse that decision. Do not propose it.
#:
#: (⛔ Neither sentence above spells the suppressive marker prefix itself: a comment
#: whose text contains that prefix followed by the word `noqa` is PARSED by ruff as a
#: directive, so prose about suppression emits "invalid directive" warnings on exactly
#: the cache-cold lint run that is supposed to report findings — measured: this paragraph
#: cost 2 spurious warnings in its first draft.)
#:
#: ⛔ NO FILE COUNT IS ASSERTED, here or for the roots outside. A count is a
#: measurement of the tree it was taken on: a draft of this comment said "254", and by
#: the time the text landed the tree held 255 outside these roots, in directories the
#: enumeration had also missed — the drift class this very comment is about. The
#: figures elsewhere in this comment are bound to the tree that measured them. The
#: population an assertion should pin is the one it ENFORCES (`_MIN_MODULES` below),
#: not a figure in prose.
#:
#: It is one scan over ALL three roots on purpose: the class is not specific to the
#: probe, and naming files here would re-open the hole the moment a declaration moves
#: into one not named.
#:
#: ⛔ IT IS NOT FREE, measured on the tree that measured it: 1,976 modules (`tortoise/` 287,
#: `tools/` 195, `tests/` 1,494), **19.1 s** of `ast.parse`+walk, inside a file whose
#: own run is ~2 min. Recorded so the next reader does not assume it is cheap — and so
#: a future budget discussion has a figure. There is no sound cheap pre-filter:
#: narrowing the roots narrows the guard, which is the hole the scan exists to close.
_DUP_KEY_ROOTS = ("tortoise", "tools", "tests")

#: Per-root FLOOR for the scan's own non-vacuity check, deliberately per root and not
#: as an aggregate: measured 287 / 195 / 1,494 on the tree above, so an aggregate floor
#: (>1,000) still passes if the `tortoise/` AND `tools/` walks silently yield nothing —
#: and `tools/` is exactly where this class's original instance lived. Set well under
#: what each root holds so ordinary churn does not red it, but non-zero, so a renamed or
#: vanished root fails instead of quietly narrowing the guard.
_MIN_MODULES = {"tortoise": 100, "tools": 50, "tests": 500}


class _JoinedKey:
    """An F-STRING dict key that CARRIES a placeholder, compared by its source.

    Two byte-identical f-string keys are ONE key at runtime for any referentially
    transparent expression (shown by `ruff`'s `F601`, measured:
    `{f"{x}": 1, f"{x}": 2}`), and `ast.unparse` normalises quoting, so `f"{x}"` and
    `f'{x}'` are one key here as they are at runtime.
    ⛔ The transparency qualifier is MEASURED, not caution: `c = iter(["a", "b"]);
    D = {f"{next(c)}": 1, f"{next(c)}": 2}` builds `{'a': 1, 'b': 2}` — two DISTINCT
    keys — while this arm reports a duplicate. That is a false RED, never a false
    PASS, and `ruff 0.16.4 --select F601` flags the same shape (verified), so the
    guard is not weaker than the lint it backs; skipping impure JoinedStrs would
    diverge from the oracle, so the residual is stated instead.

    ⛔ THIS CLASS IS REACHED ONLY BY A PLACEHOLDER-BEARING F-STRING. A placeholder-free
    one (`f"a"`, `f""`) is FOLDED to the plain `str` key CPython compiles it to,
    inside `_constant_key_value` — and that distinction is the difference between a
    FALSE PASS and a caught duplicate. `{f"a": 1, "a": 2}` IS one key at runtime
    (`{'a': 2}`), it needs NO evaluation to prove (CPython has already dropped the
    `FormattedValue`s, so the key is the `str` constants joined in order), and `ruff
    0.16.4` does not flag that CROSS-FORM pair (measured; it flags only the
    same-source `{f"a": 1, f"a": 2}`) — so an arm that refused to compare it caught
    nothing on the one shape whose only other catcher can be a `noqa` on the
    survivor, while reading as a deliberate limit. The earlier text here said proving
    it "needs evaluation"; that was FALSE, and it is why the false PASS survived a
    review round. Only a PLACEHOLDER-BEARING f-string genuinely needs evaluation to
    be equated with a `Constant`, and that is the comparison refused here.
    """
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def __hash__(self) -> int:
        return hash((_JoinedKey, self.text))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _JoinedKey) and other.text == self.text

    def __repr__(self) -> str:
        return f"f-string key {self.text}"


def _constant_key_value(node: ast.expr) -> tuple[bool, object]:
    """`(True, value)` when the key is a literal Python can hash — else `(False, None)`.

    Recurses through a TUPLE literal, because `{(1, 2): …}` is a real declaration
    shape (`ruff`'s `F601` catches it; measured, 390 tuple-literal dict keys under
    these roots) and a `Constant`-only test would silently skip it — a hole in a
    guard that sells itself as closing holes. Only the 308 of those whose elements
    are ALL literal are compared; the other 82 carry a computed element and are
    skipped like any other non-literal key. Element values are compared by Python's
    own `==`/hash, so `(1, 2)` and `(True, 2)` are ONE key, exactly as a dict treats
    them.

    Recurses through a SIGNED numeric literal too (`{-1: …}`, `{+1: …}`, and
    `{(-1, 2): …}` via the tuple arm), because `ruff`'s `F601` flags all three and
    `{+1: 'a', 1: 'b'}` is a real duplicate — Python collapses it to one key. A
    `Constant`-only reading would skip the class, and a skipped duplicate is one that
    no lane catches at all when the survivor carries a `noqa` marker.

    An F-STRING key is read too, in the two arms its own AST separates:

      * PLACEHOLDER-FREE (`f"a"`, `f""`) is folded to the `str` key CPython compiles it
        to, so `{f"a": 1, "a": 2}` — one key at runtime, `{'a': 2}` — is caught. This
        FOLD is the repaired arm: the scan used to answer `[]` for it, justified by a
        claim that proving the equality "needs evaluation". That claim was FALSE (the
        `ast` has no `FormattedValue` left to evaluate) and `ruff 0.16.4 --select F601`
        does not flag the cross-form pair either, so the guard was WEAKER than nothing
        on it: a FALSE PASS nobody else caught.
      * PLACEHOLDER-BEARING (`f"{x}"`) is compared by SOURCE (`_JoinedKey`), because
        equating that with a `Constant` genuinely does need evaluation. The same-source
        duplicate was MEASURED missing by a fresh review — the scan reported `[]` where
        `ruff --select F601` reported a duplicate.
    """
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, ast.JoinedStr):
        # A placeholder-free f-string is a CONSTANT FOLD, not a computation: `ast` has
        # already removed every `FormattedValue`, so the key is the `str` constants
        # joined in order. Reading it is the difference between catching
        # `{f"a": 1, "a": 2}` (`{'a': 2}` at runtime, and NOT flagged by `ruff`) and
        # missing it.
        if all(isinstance(v, ast.Constant) and isinstance(v.value, str)
               for v in node.values):
            return True, "".join(v.value for v in node.values)
        # A placeholder-bearing f-string cannot be equated with a `Constant` without
        # evaluating it (see `_JoinedKey`), so it is compared by SOURCE — and two
        # byte-identical sources are one key for a referentially transparent
        # expression, which is exactly what `ruff` reports.
        return True, _JoinedKey(ast.unparse(node))
    if isinstance(node, ast.Tuple):
        values = []
        for element in node.elts:
            ok, value = _constant_key_value(element)
            if not ok:
                return False, None
            values.append(value)
        return True, tuple(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        ok, operand = _constant_key_value(node.operand)
        # Numeric only: `-`/`+` on anything else is a TypeError at runtime, so there
        # is no key to compare. `bool` is an `int` here on purpose — `+True` IS `1`,
        # so `{+True: 'a', True: 'b'}` is a real duplicate.
        if ok and isinstance(operand, (int, float, complex)):
            return True, (-operand if isinstance(node.op, ast.USub) else +operand)
    return False, None


def _duplicate_constant_key_sites(
        source: str | bytes, filename: str = "<string>") -> list[tuple[int, int, str]]:
    """`(first_lineno, duplicate_lineno, key)` for every duplicated dict-literal key.

    The `seen` map is keyed on the VALUE, not on a type-prefixed spelling of it, so
    the comparison IS Python's own: `1`, `1.0` and `True` collapse to ONE key (they
    hash equal), `None` is a key like any other, and `b"a"` stays distinct from
    `"a"`. A type-prefixed marker would read `{1: 'a', 1.0: 'b'}` as two keys and
    miss a real shadow — the exact class this guard exists for (found in this
    guard's own first draft by a fresh review).

    Only LITERAL keys can be compared without evaluating anything: a computed key
    (`SOME_NAME`) is not a declaration this can read, and guessing would report a
    duplicate that is not one. An F-STRING key is read in its two arms, and neither is
    a guess: a PLACEHOLDER-FREE one (`f"a"`, `f""`) is the `str` constant CPython
    folds it to, and a PLACEHOLDER-BEARING one is compared by SOURCE — two byte-identical
    sources are ONE key for a referentially transparent expression (`_JoinedKey`; an
    impure one is a stated false-RED residual there, and `ruff` shares it), which is
    also exactly what `ruff`'s `F601` reports.
    `**splat` in a
    literal parses as a `None` entry in
    `node.keys`, not a Constant, so it is skipped rather than mistaken for the key
    `None`; `_constant_key_value` recurses through tuple and signed-numeric literals
    so a fully-literal `{(1, 2): …}` is compared too (390 tuple-literal keys under
    these roots, 308 of them fully literal).

    ⚠️ Still outside this population, stated so it is not mistaken for a total, AND
    stated as a HOLE rather than as parity with the lint: a COMPUTED key (`SOME_NAME`),
    a PLACEHOLDER-BEARING f-string compared against a DIFFERENT expression or against a
    plain literal — that comparison needs the evaluation this refuses — any tuple
    containing one, and a key built by an operator outside `USub`/`UAdd`. ⛔ The
    placeholder-bearing cross-form pair is NOT harmless: `{f"{'a'}": 1, "a": 2}` is
    `{'a': 2}` at runtime, its first entry is DEAD, and BOTH this scan and
    `ruff 0.16.4 --select F601` report nothing for it (measured). The earlier text here
    supported the exclusion with `{f"{x}": 1, "{x}": 2}`, which is a near-non-example
    (`x` would have to evaluate to the literal string `"{x}"`), so a reader testing the
    boundary that way concluded the residual was unreachable. It is reachable; closing
    it needs evaluating the placeholder, which is the one step this function does not take.
    ⛔ The PLACEHOLDER-FREE cross-form pair, `{f"a": 1, "a": 2}`, USED to be listed
    here as "needs evaluation" — that was FALSE and it hid a duplicate no other
    instrument reported (see `_constant_key_value`); it is IN this population and is
    pinned by its own case below.

    ⚠️ `filename` is threaded into `ast.parse` so a `SyntaxError` carries the module's
    REAL path rather than `<string>` — it does NOT affect any message. The
    UNPARSEABLE text is built by `_scan_declaration_roots` from `rel` and `exc.msg`,
    so dropping this argument leaves every message byte-identical (measured by
    mutation: removing `filename=filename` keeps all three tests green, and the
    `{rel}` PREFIX is what makes an unparseable module name ITSELF instead of
    `<unknown>` — the "a hole is a hole, not a zero" rule this file's
    predicate-count test states). Stated so the next reader does not mistake an
    inert argument for the mechanism.

    ⛔ `source` may be BYTES on purpose: the scan hands it the file's raw bytes, so
    CPython's own source decoding applies (a PEP 263 coding cookie and a UTF-8 BOM are
    legal source). Decoding as UTF-8 first would report a file the interpreter reads
    fine as a hole — measured: a `latin-1` cookie read `UNREADABLE (UnicodeDecodeError)`
    and a BOM read `UNPARSEABLE (U+FEFF at line 1)`, both of which `ast.parse(bytes)`
    accepts.
    """
    sites: list[tuple[int, int, str]] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Dict):
            continue
        seen: dict[object, int] = {}
        for key in node.keys:
            if key is None:  # `**splat`, not a key literal
                continue
            ok, value = _constant_key_value(key)
            if not ok:
                continue
            if value in seen:
                sites.append((seen[value], key.lineno, repr(value)))
            else:
                seen[value] = key.lineno
    return sites


def test_the_duplicate_key_detector_catches_a_duplicate():
    """The detector must be able to come back RED, or the scan below proves nothing.

    This module's own contract (see the file docstring): an instrument that can
    only print `MET` is machinery. A guard whose scan silently returned `[]` for
    every input would pass the assertion below forever.
    """
    source = (
        "DECLARATIONS = {\n"
        "    'a::b': ('first', 1),\n"
        "    'c::d': ('other', 2),\n"
        "    'a::b': ('second', 3),\n"
        "}\n"
    )
    assert _duplicate_constant_key_sites(source) == [(2, 4, "'a::b'")]
    # …and a clean map reports nothing, so the positive case above is the ONLY
    # reason a red is possible.
    assert _duplicate_constant_key_sites("D = {'a': 1, 'b': 2}\n") == []
    # A computed key is not a declaration this reads: no false positive.
    assert _duplicate_constant_key_sites("D = {K: 1, K: 2}\n") == []
    # The comparison must BE Python's own key equality, in the MISS direction too:
    # `1`, `1.0` and `True` are ONE key, so a numeric/alias shadow must be caught
    # (a type-prefixed marker misses it — this guard's own first draft did).
    assert _duplicate_constant_key_sites("D = {1: 'a', 1.0: 'b'}\n") == [(1, 1, "1.0")]
    assert _duplicate_constant_key_sites("D = {1: 'a', True: 'b'}\n") == [(1, 1, "True")]
    assert _duplicate_constant_key_sites("D = {None: 1, None: 2}\n") == [(1, 1, "None")]
    # …while genuinely distinct keys stay silent: bytes are not text, and a
    # `**splat` is not the key `None`.
    assert _duplicate_constant_key_sites("D = {b'a': 1, 'a': 2}\n") == []
    assert _duplicate_constant_key_sites("D = {None: 1, **x}\n") == []
    # A TUPLE-literal key is compared too (390 under the scanned roots, 308 fully
    # literal), and
    # element values follow dict equality: `(1, 2)` and `(True, 2)` are ONE key.
    assert _duplicate_constant_key_sites("D = {(1, 2): 'a', (1, 2): 'b'}\n") == [
        (1, 1, "(1, 2)")]
    assert _duplicate_constant_key_sites("D = {(1, 2): 'a', (True, 2): 'b'}\n") == [
        (1, 1, "(True, 2)")]
    # …while a 1-tuple is NOT the bare key, and a non-literal element is skipped.
    assert _duplicate_constant_key_sites("D = {(1,): 'a', 1: 'b'}\n") == []
    assert _duplicate_constant_key_sites("D = {(K, 2): 'a', (K, 2): 'b'}\n") == []
    # A SIGNED numeric key is still a key, and `ruff`'s `F601` flags all three of
    # these shapes — so a guard that skipped them would be WEAKER than the lint it
    # exists to backstop, on the one shape that has no other catcher when the
    # survivor carries a noqa marker.
    assert _duplicate_constant_key_sites("D = {-1: 'a', -1: 'b'}\n") == [(1, 1, "-1")]
    assert _duplicate_constant_key_sites("D = {+1: 'a', 1: 'b'}\n") == [(1, 1, "1")]
    assert _duplicate_constant_key_sites("D = {(-1, 2): 'a', (-1, 2): 'b'}\n") == [
        (1, 1, "(-1, 2)")]
    # ⛔ `bool` is an `int` in this arm ON PURPOSE (`+True` IS `1`), and that is what
    # makes `{+True: 'a', True: 'b'}` a real duplicate — `ruff F601` flags it too. Nothing
    # pinned it until a review MEASURED the narrowing: `isinstance(operand, (int, float,
    # complex))` → `type(operand) in (int, float, complex)` reads like a tidy-up, makes the
    # scan return `[]`, and leaves the whole suite GREEN — a false PASS on a shape lint
    # catches, i.e. exactly the weakness this arm exists to avoid.
    assert _duplicate_constant_key_sites("D = {+True: 'a', True: 'b'}\n") == [(1, 1, "True")]
    # …and the sign is compared, not stripped: `-1` and `1` are DIFFERENT keys.
    assert _duplicate_constant_key_sites("D = {-1: 'a', 1: 'b'}\n") == []
    # An F-STRING key is a gap a fresh review MEASURED in this guard: the scan returned
    # `[]` where `ruff --select F601` reported a duplicate, so the guard was WEAKER than
    # the lint it backstops — a false PASS on the one shape whose only other catcher can
    # be a `noqa` marker on the survivor.
    # A PLACEHOLDER-FREE f-string FOLDS to the plain `str` key CPython compiles, so `f"a"`
    # and `f'a'` are ONE key and render as the `str` they are rather than as a
    # `_JoinedKey`. This arm used to refuse the fold on a FALSE claim that proving the
    # equality "needs evaluation" — `ast` has no `FormattedValue` left to evaluate, and
    # `ruff 0.16.4` does not flag the cross-form pair, so nothing else caught it.
    assert _duplicate_constant_key_sites('D = {f"a": 1, f"a": 2}\n') == [
        (1, 1, "'a'")]
    assert _duplicate_constant_key_sites("D = {f\"a\": 1, f'a': 2}\n") == [
        (1, 1, "'a'")]
    assert _duplicate_constant_key_sites('D = {f"": 1, "": 2}\n') == [(1, 1, "''")]
    # ⛔ …and the CROSS-FORM pair specifically, which is the shape with NO other catcher
    # at all: `{f"a": 1, "a": 2}` IS `{'a': 2}` at runtime and `ruff --select F601` does
    # NOT flag it (measured on 0.16.4), so refusing it was a false PASS reading as a
    # deliberate limit. Pinned so the wording cannot drift back.
    assert _duplicate_constant_key_sites('D = {f"a": 1, "a": 2}\n') == [(1, 1, "'a'")]
    # A PLACEHOLDER-BEARING f-string is compared by SOURCE instead, because equating it
    # with a `Constant` genuinely needs evaluation: two byte-identical sources are one
    # key for a referentially transparent expression, and
    # `ast.unparse` normalises the quoting (`f"{x}"` and `f'{x}'` are one key, as at runtime).
    assert _duplicate_constant_key_sites('D = {f"{x}": 1, f"{x}": 2}\n') == [
        (1, 1, "f-string key f'{x}'")]
    # …while a DIFFERENT placeholder expression is a different key. ⛔ A
    # PLACEHOLDER-BEARING f-string is NOT compared with a plain literal, and that is a
    # RESIDUAL HOLE IN BOTH INSTRUMENTS — not parity with the lint, which the earlier
    # wording here claimed: `{f"{'a'}": 1, "a": 2}` is `{'a': 2}` at runtime (the first
    # entry is DEAD) and NEITHER this scan NOR `ruff 0.16.4 --select F601` reports it
    # (both measured). Equating it needs the placeholder EVALUATED, which is the one step
    # this function refuses; the hole is pinned here so the next lane inherits the truth
    # rather than a claim that nothing is missed.
    assert _duplicate_constant_key_sites('D = {f"{x}": 1, f"{y}": 2}\n') == []
    # the residual HOLE itself, pinned so its absence cannot be read as coverage:
    assert _duplicate_constant_key_sites("D = {f\"{'a'}\": 1, \"a\": 2}\n") == []

    # ⛔ The one direction this arm gets WRONG, pinned so the wording cannot drift back
    # to "whatever the expression is": an IMPURE expression is re-evaluated per key, so
    # two byte-identical sources build two DISTINCT keys — and this reports a duplicate.
    # It is a false RED, not a false PASS, and `ruff --select F601` flags the same shape
    # (verified), so the guard stays no weaker than the oracle it backstops.
    assert _duplicate_constant_key_sites(
        'D = {f"{next(c)}": 1, f"{next(c)}": 2}\n') == [
        (1, 1, "f-string key f'{next(c)}'")]


def _duplicate_key_report(violations: list[str]) -> str:
    """The guard's failure message — separated so a test can DRIVE it.

    It used to be composed inline in the guard's `assert`, where it is evaluated only
    when that assert FAILS — so no run ever built it, and changing the prefix or the
    newline-and-indent separator would have left every test green. A message nobody renders is
    not a guard's evidence.

    ⛔ The prose covers BOTH arms this prefixes, because `_scan_declaration_roots` routes
    three kinds of violation through it: a duplicated key, an UNREADABLE module and an
    UNPARSEABLE one. A prefix that named only the duplicate would send a reader holding a
    parse failure looking for a duplicate key that is not there — the misattribution this
    guard exists to stop, one layer up (review of `#12265`).
    """
    return (
        "a duplicate declaration is a SILENT loser: a dict literal that declares the same "
        "key twice keeps only the LAST binding, so the entry that loses the last-wins race "
        "is DEAD while every test stays green (#12265, root #9314) — and a module this scan "
        "could not READ or PARSE is a HOLE in the same guard, not a pass. Delete the stale "
        "row (or correct the reason) rather than adding `# noqa: F601`, and make an "
        "UNREADABLE/UNPARSEABLE module readable:\n  "
        + "\n  ".join(violations)
    )


def _guard_root() -> Path:
    """The repo root the duplicate-key scan walks, resolved from THIS FILE.

    ⛔ NOT `Path.cwd()`. `Path(root).rglob(...)` resolves against the invocation
    directory, so a CWD-derived root scans 0 modules when pytest is launched from
    `tests/` and reds this guard on a CORRECT tree. Hoisted out of the test body
    only so `test_the_scan_root_is_the_REPO_not_the_process_cwd` can be the
    FALSIFIER for it: a `chdir` is the one thing that tells a file-derived root
    apart from a CWD-derived one, and comparing without it passes under the repo
    root, which is how CI and `tools/test_lane.py` both invoke pytest.
    """
    return Path(probe.__file__).resolve().parent.parent


def _scan_declaration_roots(root: Path) -> tuple[list[str], dict[str, int]]:
    """`(violation messages, modules FOUND per root)` over `_DUP_KEY_ROOTS` under `root`.

    ⚠️ "found", not "read": the per-root counter is incremented BEFORE the read, so a
    module that cannot be READ is counted here while its own message says
    `UNREADABLE (…), not scanned`. Stated because these counts are what satisfy the
    per-root floors — i.e. the guard's non-vacuity claim. A module that cannot be
    DECODED is a different case now that the read hands `ast.parse` raw bytes: it
    surfaces as a `SyntaxError` and says `UNPARSEABLE`, never `UNREADABLE`.

    ⛔ KNOWN SILENT HOLE in the WALK (measured, filed as `#12385`): `rglob` swallows a
    `PermissionError` from `scandir` and does not descend a symlinked directory, so an
    unlistable (`chmod 000`) subtree or one reachable only through a symlink is a
    directory the scan never ENUMERATES — no violation, and the per-root floors are too
    coarse to notice a partial loss (losing 20 of `tortoise/`'s 287 still clears the
    floor of 100). Neither shape exists under these roots today (no tracked symlinks;
    every file git-readable), which is why the hole is stated rather than repaired in a
    review lane: the repair replaces this walk with `os.walk(..., onerror=…)` plus a
    symlinked-directory violation, and that belongs with the follow-up, not here.

    Hoisted out of the test so the REPORTING path can be driven: on a real tree
    `violations` is empty, so the message formatting, the `relative_to` naming and the
    `"\n  ".join(...)` would otherwise be code no run ever executes. A guard whose
    only exercised arm is the green one is not evidence the red one works.
    """
    violations: list[str] = []
    per_root: dict[str, int] = {}
    for name in _DUP_KEY_ROOTS:
        scanned_here = 0
        for path in sorted((root / name).rglob("*.py")):
            rel = path.relative_to(root)
            scanned_here += 1
            try:
                # ⛔ BYTES, not text: `read_text(encoding="utf-8")` is STRICTER than
                # CPython — a PEP 263 coding cookie and a UTF-8 BOM are legal source, and
                # decoding it ourselves reported both as holes (measured). Handing the
                # bytes to `ast.parse` makes the interpreter the decoder, so an
                # UNREADABLE verdict now means what its name says: the file could not be
                # READ (permissions, a directory), not that it is unusual source.
                source = path.read_bytes()
            except OSError as exc:  # a hole is a hole, not a zero
                violations.append(f"{rel}: UNREADABLE ({exc.__class__.__name__}), not scanned")
                continue
            try:
                for first, duplicate, key in _duplicate_constant_key_sites(source, str(rel)):
                    violations.append(
                        f"{rel}:{duplicate} re-declares key {key} (first at :{first})")
            except SyntaxError as exc:  # name the file, never `<unknown>`
                # `lineno` is `None` for a NUL byte and `0` for a codec/cookie failure
                # (`# coding: unknown-xyz`), so a `is not None` test still renders
                # "at line 0" — a located-LOOKING defect pointing at a line that cannot
                # exist, which is the misattribution this message exists to prevent, one
                # field down. Both mean "no line", so test the VALUE, not `is None`.
                where = exc.lineno if exc.lineno else "?"
                violations.append(
                    f"{rel}: UNPARSEABLE ({exc.msg} at line {where}), not scanned")
        per_root[name] = scanned_here
    return violations, per_root


def test_no_module_declares_the_same_dict_key_twice():
    """#12265: a duplicated literal key silently disables the entry that loses.

    Measured at `origin/main` `9828250732`: three sites — two in
    `tools/dod_5090_probe.py` (`WRITER_CENSUS`, `CENSUS_EVIDENCE`) and one in
    `tests/test_commit_schema.py` (a fixture `op_type` that last-wins discarded,
    silenced with `# noqa: F601`). This assertion is the lane-independent catch
    `#9314` asks for: it reds in the pytest shard, not only where lint runs.

    ⛔ Its coverage is the three roots `_DUP_KEY_ROOTS` names, not the whole repo —
    see that constant: `battery/`, `graph-scripts/`, `docs/`, `eval/` and the smaller
    roots sit outside them.
    """
    # ⛔ A root with no floor is a walk nothing checks: adding a fourth entry to
    # `_DUP_KEY_ROOTS` without adding a `_MIN_MODULES` floor would leave it counted
    # and unchecked, so it could yield zero and the guard would silently narrow —
    # exactly what the floors exist to prevent.
    assert set(_MIN_MODULES) == set(_DUP_KEY_ROOTS), (
        "every scanned root needs a non-vacuity floor — "
        f"roots={sorted(_DUP_KEY_ROOTS)}, floors={sorted(_MIN_MODULES)}")
    # ⛔ …and a floor of ZERO is the same hole: `per_root[name] >= 0` is true for a walk
    # that yielded nothing, so `{n: 0 for n in _DUP_KEY_ROOTS}` satisfies the loop below
    # while making the non-vacuity claim vacuous — measured GREEN before this assert
    # existed. The comment says "non-zero"; this is the line that enforces it. ⛔ And an
    # EMPTY population is the same vacuity one level up: `all(...)` over no values is True
    # and the key-set equality is satisfied by two empty containers, so the scan walks
    # nothing and passes — measured GREEN with `_DUP_KEY_ROOTS = ()`.
    assert _MIN_MODULES and all(floor > 0 for floor in _MIN_MODULES.values()), (
        f"every floor must be non-zero, or a root that yields nothing passes — {_MIN_MODULES}")
    # ⛔ THE REPO ROOT COMES FROM THE FILE, NOT THE CWD (#12265 review):
    # `Path(root).rglob` resolves against the invocation directory, so a run from
    # `tests/` scanned 0 modules and red this guard on a CORRECT tree. This is the
    # same derivation the module's other repo-root reads use
    # (`Path(probe.__file__).resolve().parent.parent`); `probe.REPO_ROOT` is that
    # same value, but several reads here already spell it out for the same reason.
    violations, per_root = _scan_declaration_roots(_guard_root())
    # ⛔ THE VIOLATION IS ASSERTED FIRST, deliberately: a floor failure means the
    # guard is narrower than it claims, but a live duplicate in the same run is the
    # finding the reader actually needs, and asserting floors first would hide it.
    # ⚠️ The ORDER itself is not separately asserted — both asserts fire on their own
    # (each is proven RED by its own mutation), but no test builds a tree holding a
    # duplicate AND a broken walk, so swapping these two lines leaves the suite green
    # (measured by mutation). The sentence above is the RATIONALE for the order, not a
    # claim that the order is pinned.
    assert not violations, _duplicate_key_report(violations)
    # A scan that read nothing would satisfy the assertion above vacuously — and it
    # must be checked PER ROOT: rglob returns zero silently on a root that was
    # renamed or removed, and an aggregate floor lets two empty roots hide behind
    # the third. `tools/` is where this class's original instance lived, so losing
    # that walk while still counting `tests/` is the regression that matters most.
    for name, floor in _MIN_MODULES.items():
        assert per_root[name] >= floor, (
            f"the duplicate-key scan found {per_root[name]} module(s) under {name}/, below "
            f"its floor of {floor} — the root was renamed, removed, or its walk broke, "
            "so the guard is now narrower than it claims. Re-derive the floor (or fix "
            f"the root) rather than lowering it. Full counts: {per_root}")


def test_the_scan_root_is_the_REPO_not_the_process_cwd(tmp_path, monkeypatch):
    """The FALSIFIER for `_guard_root` — a `chdir` is the only thing that tells them apart.

    ⛔ WHY THIS EXISTS (#12265 review cycle 3). `_guard_root` is a fix for a measured
    defect (a run from `tests/` scanned 0 modules and red this guard on a CORRECT
    tree), and until this test the fix had NO driving case: mutating the return to
    `Path.cwd()` kept the whole guard GREEN when pytest runs from the repo root —
    which is exactly how CI and `tools/test_lane.py` invoke it — so the defect could
    return with every assertion here still passing.

    ⛔ The `chdir` is the discriminating half, not decoration. Comparing
    `_guard_root()` against `probe.REPO_ROOT` WITHOUT moving the CWD passes under
    either implementation, because at the repo root the two derivations agree. Only
    after `monkeypatch.chdir(tmp_path)` do they diverge — a CWD-derived root answers
    `tmp_path` and reds here.
    """
    before = _guard_root()
    assert (before / "tortoise").is_dir() and (before / "tools").is_dir(), (
        f"_guard_root() returned {before}, which is not this checkout — the scan would "
        "walk roots that do not exist and satisfy its floors vacuously")
    monkeypatch.chdir(tmp_path)
    after = _guard_root()
    assert after == before, (
        f"_guard_root() moved with the CWD ({before} -> {after}): it is derived from "
        "`Path.cwd()` (or another process-relative source) instead of this file, so "
        "every invocation from a directory other than the repo root scans 0 modules "
        "and the duplicate-key guard silently narrows to nothing")


def test_the_scan_REPORTS_a_duplicate_it_finds(tmp_path):
    """The scan's RED arm, driven — the reporting path no real tree exercises.

    `test_no_module_declares_the_same_dict_key_twice` passes on every healthy tree
    with `violations == []`, so its message formatting, its `relative_to` naming and
    its `"\n  ".join(...)` never run. A guard whose only demonstrated arm is the green
    one is not evidence the red one works, so this drives the same helper over a
    planted tree and asserts the report names the file, the duplicate line and the
    first line.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tools" / "clean.py").write_text("D = {'a': 1}\n", encoding="utf-8")
    (tmp_path / "tortoise" / "dup.py").write_text(
        "DECLARATIONS = {\n"
        "    'x::y': 1,\n"
        "    'x::y': 2,\n"
        "}\n", encoding="utf-8")
    # A module that cannot be parsed must be NAMED, never skip silently to a green.
    (tmp_path / "tests" / "broken.py").write_text("def (:\n", encoding="utf-8")
    # …and neither may a module that cannot be READ: the UNREADABLE arm is the one whose
    # own comment states the doctrine ("a hole is a hole, not a zero"), so leaving it
    # undriven would make that comment the only thing behind it — and its failure mode
    # is a FALSE GREEN (the file is counted as scanned, contributes no violation, and
    # satisfies the per-root floor). A DIRECTORY named `*.py` is the readable-shape case
    # that survives the bytes fix: `read_bytes` raises `IsADirectoryError`, an OSError.
    (tmp_path / "tools" / "adir.py").mkdir()
    # …and non-UTF-8 BYTES are legal source shapes, not holes: CPython decodes them, so
    # this one must land in UNPARSEABLE (its own decode error), never UNREADABLE.
    (tmp_path / "tools" / "binary.py").write_bytes(b"\xff\xfe\n")
    # …and a PARSE failure that carries no line number must read as UNKNOWN, never as a
    # located defect: a NUL byte raises a `SyntaxError` whose `lineno` is None, and a
    # codec failure raises one whose `lineno` is 0. Both mean "no line", and rendering
    # either prints a location that is not there (`at line None` / `at line 0`).
    (tmp_path / "tools" / "nul.py").write_bytes(b"D = {}\x00\n")
    (tmp_path / "tools" / "codec.py").write_bytes(b"# coding: unknown-xyz\nD = {}\n")

    violations, per_root = _scan_declaration_roots(tmp_path)

    # `binary.py` still counts as SCANNED (the counter runs before the read), which is
    # exactly why the report — not the count — is what has to name it.
    assert per_root == {"tortoise": 1, "tools": 5, "tests": 1}, per_root
    # The key is rendered with `repr`, so `1` and `'1'` stay distinguishable in the
    # report — the whole reason that spelling was chosen over `str(value)`.
    assert any(
        v.startswith("tortoise/dup.py:3 re-declares key 'x::y' (first at :2)") for v in violations
    ), violations
    # ⛔ The TRUTHY branch — a real line number — must still render it: without this pin
    # `where = "?"` unconditionally is GREEN on every other assertion here (measured), so
    # the one branch that prints a usable location would be the one the suite cannot see.
    # `def (:` yields `lineno == 1`; so does `binary.py`'s UTF-8 decode failure below
    # (the COOKIE failure in `codec.py` yields 0 — a different fixture, a different case).
    assert any(
        v.startswith("tests/broken.py: UNPARSEABLE") and "at line 1" in v for v in violations
    ), violations
    assert any(
        v.startswith("tools/adir.py: UNREADABLE (IsADirectoryError)") for v in violations
    ), violations
    # ⛔ A file CPython CAN decode is never reported as unreadable: the bytes arm above
    # means a non-UTF-8 file is a PARSE failure with its own message, not a hole.
    assert any(v.startswith("tools/binary.py: UNPARSEABLE") for v in violations), violations
    assert not any("binary.py: UNREADABLE" in v for v in violations), violations
    # ⛔ A parse failure with no line number must say UNKNOWN, never `None`: a NUL byte's
    # `SyntaxError.lineno` is None, and "at line None" reads as a location that is not
    # there — the same misattribution, one field down (#12265 round 4).
    assert any(
        v.startswith("tools/nul.py: UNPARSEABLE") and "at line ?" in v for v in violations
    ), violations
    assert any(
        v.startswith("tools/codec.py: UNPARSEABLE") and "at line ?" in v for v in violations
    ), violations
    assert not any("at line None" in v for v in violations), violations
    assert not any("at line 0" in v for v in violations), violations
    # …and the clean module contributes nothing: the report is specific, not a roll-up.
    assert not any("clean.py" in v for v in violations), violations
    # ⛔ And the guard's OWN message — the one `test_no_module_declares_the_same_dict_key_twice`
    # asserts with — must carry both sites. It used to be composed inline in that
    # `assert`, so it was evaluated only on FAILURE and no run ever built it; a
    # change to the prefix or the `"\n  "` separator left every test green. This
    # drives the real builder, not a local copy of it.
    report = _duplicate_key_report(violations)
    assert "tortoise/dup.py:3 re-declares key 'x::y' (first at :2)" in report, report
    assert "tests/broken.py: UNPARSEABLE" in report, report
    assert report.count("\n  ") >= 2, report
    # ⛔ The wrapper's PROSE must describe EVERY arm it prefixes, not just the duplicate
    # one: `broken.py`/`binary.py` are not duplicate keys, and a red run whose first line
    # says "a dict literal declares the same key twice" sends the reader hunting for a
    # duplicate that does not exist. Asserted on a PARSE-ONLY set, so the arm that has no
    # duplicate in it is the one the prose has to hold for.
    parse_only = _duplicate_key_report([v for v in violations if "broken.py" in v])
    assert "could not READ or PARSE" in parse_only.split("\n  ")[0], parse_only


#: ── `#12312`: the citations this family makes are CHECKED, not trusted ───────
#: The defect: a docstring cited `test_a_named_ungated_writer_is_the_accepted_
#: exception_reading_b`, which no `def` under `tests/` declares — the test it meant
#: is `test_naming_a_LIVE_undeclared_writer_is_the_acceptance_reading_b_describes`,
#: named correctly nineteen lines away. Nothing red, and the stale stem had already
#: survived one rename on `main`.
#:
#: ⛔ The checker asserts RESOLVABILITY, never truth — the same thing every citation
#: checker in the field asserts (rustdoc's `broken_intra_doc_links`, Sphinx
#: `-n/--nitpicky` + `intersphinx`, mkdocs `strict`): a reference, once recognised,
#: must resolve to a symbol that exists. It is deliberately lexical and cheap.
#:
#: ⚠️ THE BOUND, declared rather than implied, because the honest measurement is
#: that repo-WIDE is NOT cheap today: over `tools/`, `tortoise/`, `tests/` and
#: `docs/` there are **157** node-id citations that name an existing test file and
#: a test that file does not define — almost all of them a line-WRAPPED citation
#: whose stem is truncated (`…test_the_INDENT_pops_DECLARED_`), so the fix is a
#: continuation-aware extractor plus a triage of 157 rows, not a rename. This check
#: therefore covers the FAMILY that produced the defect — `tools/dod_5090_probe.py`,
#: this file, and this epic's two artifacts — and reports 0 violations there. The
#: repo-wide item stays open in `#12312` with that cost stated.
CITATION_SURFACES = (
    "tools/dod_5090_probe.py",
    "tests/test_dod_5090_probe.py",
    "docs/epics/2026-11-04-5090-vector-layer/S3-VECTOR-CENSUS.md",
    "docs/epics/2026-11-04-5090-vector-layer/TEST-DESIGN-surface-map.md",
)

_NODE_ID_CITATION = re.compile(r"([\w./-]+\.py)::(test_[A-Za-z0-9_]+)")
_BARE_CITATION = re.compile(r"`(test_[A-Za-z0-9_]+)`")


def _test_symbols(root: Path) -> tuple[set[str], set[str], dict[str, set[str]]]:
    """``(every test def name, every test MODULE stem, {path: names})``.

    The module stems are part of the universe on purpose: a backticked
    ``test_lane`` is a citation of `tools/test_lane.py`, not a test that has gone
    missing, and a checker that could not tell those apart would report hundreds of
    false REDs. Both trees this repo keeps test tooling in are scanned — `tests/`
    for the defs, `tests/` and `tools/` for the module stems.
    """
    defined: set[str] = set()
    modules: set[str] = set()
    by_file: dict[str, set[str]] = {}
    for tree in ("tests", "tools"):
        for path in sorted((root / tree).glob("**/test_*.py")):
            modules.add(path.stem)
    for path in sorted((root / "tests").glob("**/*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            tree_ast = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        names = {
            node.name for node in ast.walk(tree_ast)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        }
        defined |= names
        modules.add(path.stem)
        by_file[rel] = names
    return defined, modules, by_file


def _unresolved_citations(text: str, defined: set[str], modules: set[str],
                          by_file: dict[str, set[str]]) -> list[str]:
    """Every citation in ``text`` that names no test this tree declares.

    Two citation shapes are checked, and the skip rules are what keep it honest:

    * `path/to/test_x.py::test_name` — checked ONLY when that file resolves in this
      tree. A documentation EXAMPLE naming a file that does not exist here
      (`tests/test_x.py::test_y`) is not a broken citation of this repo, and
      flagging it would make the check unusable.
    * a backticked BARE test name — resolves if some test declares it OR a test
      MODULE carries the stem.
    """
    unresolved: list[str] = []
    for path, name in _NODE_ID_CITATION.findall(text):
        candidates = [k for k in by_file if k == path or k.endswith("/" + path)]
        if not candidates:
            continue
        if not any(name in by_file[k] for k in candidates):
            unresolved.append(f"{path}::{name}")
    for name in _BARE_CITATION.findall(text):
        if name in defined or name in modules:
            continue
        unresolved.append(name)
    return sorted(set(unresolved))


def test_every_citation_this_family_makes_resolves_to_a_real_test():
    """`#12312`: a docstring that names a test must name one that EXISTS.

    The pin is the check, and the check is self-tested below on the exact stale stem
    this issue was filed for — so the guard cannot silently degrade into a
    tautology that passes on any text (the class this file reds elsewhere).
    """
    root = Path(probe.__file__).resolve().parent.parent
    defined, modules, by_file = _test_symbols(root)
    assert defined, "the symbol universe came back empty — the check asserts nothing"
    unresolved: dict[str, list[str]] = {}
    for rel in CITATION_SURFACES:
        text = (root / rel).read_text(encoding="utf-8")
        found = _unresolved_citations(text, defined, modules, by_file)
        if found:
            unresolved[rel] = found
    assert not unresolved, (
        "a citation names a test this tree does not declare — fix the stem (and, "
        f"if a test was renamed, the citations with it): {unresolved}")
    # …and the CITATION FOR THE GUARD ITSELF resolves, so a later rename of this
    # test cannot leave `#12312`'s own reference pointing nowhere.
    assert "test_every_citation_this_family_makes_resolves_to_a_real_test" in defined


def test_the_citation_check_REDS_on_a_deliberately_broken_citation():
    """The falsifier: red on a broken citation, green once it is fixed.

    ⛔ Against the REAL universe, not a toy one: the broken name is the one `#12312`
    was filed for, so this also pins the rename that fixed it. Without this arm the
    check above could compare two sets that never intersect and stay green forever.
    """
    root = Path(probe.__file__).resolve().parent.parent
    defined, modules, by_file = _test_symbols(root)
    # ⛔ The broken citations are ASSEMBLED from parts, not written as literals: this
    # checker scans RAW TEXT, so a broken citation written in one piece would red the
    # very check the falsifier is testing — a self-reference, not a defect.
    stale_stem = "test_a_named_ungated_writer_is" + "_the_accepted_exception_reading_b"
    real_stem = "test_naming_a_LIVE_undeclared_writer_is" + "_the_acceptance_reading_b_describes"
    broken = f"…the declared direction is `{stale_stem}` below."
    assert _unresolved_citations(broken, defined, modules, by_file) == [stale_stem], broken
    fixed = f"…the declared direction is `{real_stem}` below."
    assert _unresolved_citations(fixed, defined, modules, by_file) == [], fixed
    # A NODE-ID citation is checked against the FILE's defs, not the whole universe:
    # `<this file>::<a name this file does not declare>` is wrong even when the name
    # exists elsewhere, and that is the shape a rename produces.
    misplaced_name = "test_the_working_tree_report_records" + "_every_suite"
    misplaced = f"`tests/test_dod_5090_probe.py::{misplaced_name}`"
    assert _unresolved_citations(misplaced, defined, modules, by_file) == [
        f"tests/test_dod_5090_probe.py::{misplaced_name}"], misplaced
    # …and a citation of a file this tree does not ship is SKIPPED, not flagged: a
    # documentation EXAMPLE is not a claim about this repo.
    example = f"`tests/test_x.py::{'test' + '_y'}`"
    assert _unresolved_citations(example, defined, modules, by_file) == []

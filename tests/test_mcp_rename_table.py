"""The MCP rename table must not drift from the maps and code it describes (#4282).

`tools/mcp_rename_table.py` reads every `file:line` straight from the AST and every
count from authored data, so a citation in the generated doc cannot be wrong. What it
CAN still do is go *stale* — someone edits `tool_registry.py`, `tools/bridge_table.py`'s
destination map, or PR #4031's redirect snapshot and never regenerates — and that is the
drift the generator's own docstring promises cannot happen. Nothing runs `--check` unless
a test does. This is that enforcement.

WHY EVERY EXPECTED VALUE HERE IS A LITERAL OR AN INDEPENDENT WALK
-----------------------------------------------------------------
The sibling 0.1 test was red-green for SEVEN review rounds because an assertion imported
the very constant it was verifying (`from tools.bridge_table import C1_FINDING`, then
`assert rendered == C1_FINDING`): editing the constant moved both sides together, so the
assertion could not fail. The same trap is available here for the #4031 redirect snapshot
and for every rendered count. So:

  * the #4031 redirects are pinned as LITERALS in this file, never imported;
  * the registry's tool set and `file:line`s are re-derived here with a second AST walk;
  * every prose count is recomputed from those two independent oracles.

The generator is only ever imported to exercise its BEHAVIOUR (does it exit non-zero on a
disagreement?), never to supply an expected value.

Registered in `config/ci-surfaces.yml` under both `api` and `core`, mirroring
`test_bridge_table.py`: `api` carries the arm that fires when only the generator is edited
(`tools/mcp_rename_table.py` is named in `SOURCE_PATTERNS["api"]`), `core` is the docs
fallback.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "tools" / "mcp_rename_table.py"
DOC = ROOT / "docs" / "product" / "mcp-rename-table.md"
REGISTRY = ROOT / "tortoise" / "tool_registry.py"

# ── LITERAL ORACLES — never imported from the generator under test ───────────
# Read from PR #4031's `tortoise/tool_registry.py` at
# origin/feat/3883-retired-name-warning @ c01ad93b569e514e94d0d813c0216de9cb746d3b.
# If the generator's snapshot drifts from this, the doc reds.
RETIRED_LITERAL: dict[str, str] = {
    "tortoise_get_point": 'tortoise_get(id, type="point")',
    "tortoise_get_entity": 'tortoise_get(id, type="entity")',
    "tortoise_get_events": 'tortoise_get(None, type="events")',
    "tortoise_get_operator": 'tortoise_get(id, type="operator")',
    "tortoise_get_governance": 'tortoise_get(id, type="governance")',
    "tortoise_list_pointkinds": 'tortoise_overview(section="pointkinds")',
    "tortoise_list_tags": 'tortoise_overview(section="tags")',
    "tortoise_list_sources": 'tortoise_overview(section="sources")',
    "tortoise_taxonomy": 'tortoise_overview(section="taxonomy")',
    "tortoise_health": 'tortoise_overview(section="health")',
    "tortoise_status": 'tortoise_overview(section="status")',
    "tortoise_stale": 'tortoise_overview(section="stale")',
    "tortoise_paginated_query": "tortoise_query(offset=..., limit=...)",
    "tortoise_query_points_by_tag": "tortoise_query(tag=...)",
    "tortoise_index_sessions": "tortoise_index_files(directory)",
    "tortoise_ingest_corpus": "tortoise_index_files(directory)",
}

# The names whose 0.1 destination differs from the destination of their #4031
# redirect. These are FINDINGS, and they are the artifact's whole point — so the
# exact set is pinned here rather than recomputed only.
DISAGREEMENTS_LITERAL = {
    "tortoise_get_events",
    "tortoise_list_pointkinds",
    "tortoise_list_sources",
    "tortoise_list_tags",
    "tortoise_paginated_query",
    "tortoise_query_points_by_tag",
}

NO_REPLACEMENT = "**no replacement**"
RETIRE_WARN = "warning shim"
RETIRE_ABSENT = "absent"


def _registry_names() -> dict[str, int]:
    """`ToolDefinition(name=…)` → its call line, from an AST walk of the registry.

    Derived here rather than from `tools.bridge_table._registry_rows` (the
    generator's own source for the same fact), so the citation column is
    compared against an independent oracle instead of a second reading of the
    generator.
    """
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "ToolDefinition":
            continue
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                out[kw.value.value] = node.lineno
    assert out, "no `ToolDefinition(name=…)` calls found in tool_registry.py"
    return out


def _destination_map() -> dict[str, str]:
    """The authored Phase 0.1 destination map — the table's INPUT, not its output.

    This is data in `tools/bridge_table.py` (reviewed by a human), so importing
    it is comparing the rendered doc against its source, exactly as the sibling
    suite does. It is NOT the generator's render.
    """
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import DESTINATION

    assert DESTINATION, "the 0.1 destination map is empty"
    return dict(DESTINATION)


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _parse_part_a() -> dict[str, dict]:
    doc = _doc()
    part = doc.split("## Part A")[1].split("### Destination counts")[0]
    rows: dict[str, dict] = {}
    for line in part.splitlines():
        m = re.match(
            r"^\| (\d+) \| `([a-z_][a-z0-9_]*)` \| `tool_registry\.py:(\d+)` \| "
            r"(.+?) \| `([A-Za-z_:]+)` \| (warning shim|absent) \|$",
            line,
        )
        if m:
            idx, name, lineno, instead, dest, retirement = m.groups()
            rows[name] = {
                "idx": int(idx),
                "lineno": int(lineno),
                "instead": instead.strip(),
                "destination": dest,
                "retirement": retirement,
            }
    return rows


def _parse_part_b() -> dict[str, dict]:
    doc = _doc()
    part = doc.split("## Part B")[1].split("### B1")[0]
    rows: dict[str, dict] = {}
    for line in part.splitlines():
        m = re.match(
            r"^\| `([a-z_][a-z0-9_]*)` \| `([A-Za-z_:]+)` \| `(.+?)` \| "
            r"(`[A-Za-z_:]+`|—) \| (\*\*DISAGREE\*\*|AGREE) \|$",
            line,
        )
        if m:
            _, d_old, redirect, d_new, verdict = m.groups()
            rows[m.group(1)] = {
                "destination": d_old,
                "redirect": redirect,
                "redirect_destination": d_new,
                "verdict": verdict,
            }
    return rows


# ── Part A: coverage, citations, and every free-standing column ──────────────

def test_part_a_covers_exactly_the_registry() -> None:
    """One row per registry tool, numbered 1..N, citing the tool's real line."""
    truth = _registry_names()
    rows = _parse_part_a()
    assert rows, "Part A rendered no parseable rows — did its column shape change?"
    assert set(rows) == set(truth), (
        "Part A does not render exactly the registry tools.\n"
        f"  only in doc ({len(set(rows) - set(truth))}): {sorted(set(rows) - set(truth))}\n"
        f"  only in registry ({len(set(truth) - set(rows))}): {sorted(set(truth) - set(rows))}"
    )
    assert len(rows) == len(truth)
    idx = [r["idx"] for r in rows.values()]
    assert sorted(idx) == list(range(1, len(rows) + 1)), (
        f"Part A's `#` column is not 1..{len(rows)}: got {sorted(idx)[:5]}…{sorted(idx)[-3:]}"
    )
    wrong = [
        f"{name}: cited tool_registry.py:{rows[name]['lineno']}, defined at :{truth[name]}"
        for name in rows
        if rows[name]["lineno"] != truth[name]
    ]
    assert not wrong, (
        "Part A cites the wrong line for these tools; every citation is supposed to "
        "be read from the AST at build time:\n  " + "\n  ".join(wrong[:10])
    )


def test_part_a_call_instead_and_retirement_are_the_snapshot() -> None:
    """The `Call instead today` and `Retirement` columns come from the #4031 snapshot.

    Both are computed from ONE authored dict in the generator, so a mutation of
    the snapshot moves both columns together — and neither is read by any other
    test. The literal oracle below is what stops that mutation being free.
    """
    rows = _parse_part_a()
    assert rows, "Part A rendered no parseable rows"

    for name, row in rows.items():
        if name in RETIRED_LITERAL:
            assert row["instead"] == f"`{RETIRED_LITERAL[name]}`", (
                f"Part A says {name} should call {row['instead']!r} instead; the #4031 "
                f"snapshot says `{RETIRED_LITERAL[name]}`"
            )
            assert row["retirement"] == RETIRE_WARN, (
                f"Part A says {name} retires as {row['retirement']!r}; it is in the "
                f"#4031 snapshot, so it must be {RETIRE_WARN!r}"
            )
        else:
            assert row["instead"] == NO_REPLACEMENT, (
                f"Part A says {name} should call {row['instead']!r} instead; it is not in "
                f"the #4031 snapshot, so it must be {NO_REPLACEMENT!r}"
            )
            assert row["retirement"] == RETIRE_ABSENT, (
                f"Part A says {name} retires as {row['retirement']!r}; it is not in the "
                f"#4031 snapshot, so it must be {RETIRE_ABSENT!r}"
            )

    # The snapshot's keys must be exactly the registry tools that are redirected:
    # a name in the snapshot with no row, or a row marked `warning shim` while not
    # in the literal snapshot, is a drift the per-row loop above cannot see.
    warned = {n for n, r in rows.items() if r["retirement"] == RETIRE_WARN}
    assert warned == set(RETIRED_LITERAL), (
        f"Part A marks {len(warned)} tools `warning shim`; the #4031 snapshot has "
        f"{len(RETIRED_LITERAL)} entries.\n"
        f"  extra: {sorted(warned - set(RETIRED_LITERAL))}\n"
        f"  missing: {sorted(set(RETIRED_LITERAL) - warned)}"
    )


def test_part_a_destination_column_is_the_0_1_map() -> None:
    """Every `Destination (target surface)` cell equals the 0.1 map, exactly.

    Without this the whole column is free: overwriting every cell with `REMOVED`
    contradicts the doc's own counts table and headline while every other test
    stays green.
    """
    dest = _destination_map()
    rows = _parse_part_a()
    assert rows, "Part A rendered no parseable rows"
    for name, row in rows.items():
        assert row["destination"] == dest.get(name), (
            f"Part A says {name} → {row['destination']!r}; the 0.1 map says "
            f"{dest.get(name)!r}"
        )


def test_destination_counts_table_and_prose_are_arithmetic() -> None:
    """The counts table, its total cell, and every prose count must all agree."""
    dest = _destination_map()
    truth: dict[str, int] = {}
    for v in dest.values():
        truth[v] = truth.get(v, 0) + 1
    n_tools = len(_registry_names())
    assert sum(truth.values()) == n_tools

    doc = _doc()
    table = dict(re.findall(r"^\| `([A-Za-z_:]+)` \| (\d+) \|$", doc, re.M))
    assert table, "the destination-counts table did not parse — the surface is unguarded"
    assert len(table) == len(truth), (
        f"the counts table has {len(table)} rows; the 0.1 map has {len(truth)} destinations"
    )
    for cell, rendered in table.items():
        assert int(rendered) == truth.get(cell), (
            f"the counts table says {cell} has {rendered} sources; the map says "
            f"{truth.get(cell)}"
        )
    total_cell = re.search(r"^\| \*\*total\*\* \| \*\*(\d+)\*\* \|$", doc, re.M)
    assert total_cell, "the destination-counts table has no `**total**` row"
    assert int(total_cell.group(1)) == n_tools, (
        f"the total cell says {total_cell.group(1)}; the registry has {n_tools} tools"
    )

    # Headline sentence 1: "N current MCP tools, M destinations."
    m = re.search(r"^\*\*(\d+) current MCP tools, (\d+) destinations\.\*\*", doc, re.M)
    assert m, "the `N current MCP tools, M destinations` headline is missing or changed shape"
    assert int(m.group(1)) == n_tools, f"headline says {m.group(1)} tools; there are {n_tools}"
    assert int(m.group(2)) == len(truth), (
        f"headline says {m.group(2)} destinations; the map has {len(truth)}"
    )

    # Sentence 2: the retirement split.
    m2 = re.search(
        r"(\d+) retire \*\*with a warning shim\*\*; the other \*\*(\d+)\*\* are simply absent",
        doc,
    )
    assert m2, "the retirement-split sentence is missing or changed shape"
    assert int(m2.group(1)) == len(RETIRED_LITERAL), (
        f"the doc says {m2.group(1)} retire with a warning shim; the snapshot has "
        f"{len(RETIRED_LITERAL)}"
    )
    assert int(m2.group(2)) == n_tools - len(RETIRED_LITERAL), (
        f"the doc says {m2.group(2)} are simply absent; "
        f"{n_tools - len(RETIRED_LITERAL)} are"
    )

    # Sentence 3: the destination-bucket breakdown.
    m3 = re.search(
        r"\*\*(\d+)\*\* absorbed into the \*\*(\d+)\*\* MCP targets, "
        r"\*\*(\d+)\*\* into a builder-only SDK method \(not on the MCP\), "
        r"\*\*(\d+)\*\* tenancy \(SDK/REST only\), \*\*(\d+)\*\* removed\.",
        doc,
    )
    assert m3, "the destination-bucket sentence is missing or changed shape"
    on_mcp, n_targets, sdk_only, tenancy, removed = (int(g) for g in m3.groups())
    assert sdk_only == sum(n for d, n in truth.items() if d.startswith("sdk:"))
    assert tenancy == sum(n for d, n in truth.items() if d.startswith("tenancy:"))
    assert removed == truth.get("REMOVED", 0)
    assert on_mcp == n_tools - sdk_only - tenancy - removed, (
        "the bucket sentence's `absorbed into MCP targets` figure does not match the "
        "other four buckets"
    )
    assert n_targets == 26, (
        f"the bucket sentence says {n_targets} MCP targets; the approved target surface is 26"
    )
    assert on_mcp + sdk_only + tenancy + removed == n_tools


# ── Part B: the #4031 agreement check ────────────────────────────────────────

def test_part_b_rows_are_exactly_the_snapshot_and_verdicts_recompute() -> None:
    """Part B covers the 16 snapshot names, and each verdict is the real comparison."""
    dest = _destination_map()
    rows = _parse_part_b()
    assert rows, "Part B rendered no parseable rows — did its column shape change?"
    assert set(rows) == set(RETIRED_LITERAL), (
        "Part B does not render exactly the #4031 snapshot.\n"
        f"  only in doc: {sorted(set(rows) - set(RETIRED_LITERAL))}\n"
        f"  only in snapshot: {sorted(set(RETIRED_LITERAL) - set(rows))}"
    )

    for name, row in rows.items():
        assert row["destination"] == dest[name], (
            f"Part B says {name}'s 0.1 destination is {row['destination']!r}; the map says "
            f"{dest[name]!r}"
        )
        assert row["redirect"] == RETIRED_LITERAL[name], (
            f"Part B says {name} redirects to {row['redirect']!r}; the snapshot says "
            f"{RETIRED_LITERAL[name]!r}"
        )
        target = re.match(r"(tortoise_[a-z0-9_]+)", RETIRED_LITERAL[name]).group(1)
        assert row["redirect_destination"] == f"`{dest[target]}`", (
            f"Part B says {name}'s redirect lands on {row['redirect_destination']!r}; "
            f"{target} maps to `{dest[target]}`"
        )
        assert row["verdict"] == ("AGREE" if dest[name] == dest[target] else "**DISAGREE**"), (
            f"Part B's verdict for {name} is {row['verdict']!r}; "
            f"`{dest[name]}` vs `{dest[target]}` says otherwise"
        )

    # The verdict-tally prose is a count, so assert it against the recomputed set.
    agree = {n for n, r in rows.items() if r["verdict"] == "AGREE"}
    m = re.search(r"\*\*(\d+) of (\d+) agree; (\d+) disagree\.\*\*", _doc())
    assert m, "the agreement-tally sentence is missing or changed shape"
    assert int(m.group(1)) == len(agree)
    assert int(m.group(2)) == len(rows)
    assert int(m.group(3)) == len(rows) - len(agree)


def test_b1_lists_exactly_the_disagreements() -> None:
    """B1 is the artifact's finding list; it may not silently empty itself.

    The expected set is the LITERAL disagreement set, plus an independent
    recomputation — a generator whose comparison logic inverted would still
    render a self-consistent Part B, so the literal is the anchor.
    """
    dest = _destination_map()
    recomputed = {
        name for name, expr in RETIRED_LITERAL.items()
        if dest[name] != dest[re.match(r"(tortoise_[a-z0-9_]+)", expr).group(1)]
    }
    assert recomputed == DISAGREEMENTS_LITERAL, (
        "the #4282 disagreement set changed:\n"
        f"  recomputed: {sorted(recomputed)}\n"
        f"  pinned here: {sorted(DISAGREEMENTS_LITERAL)}"
    )

    b1 = _doc().split("### B1")[1].split("## Reproduce")[0]
    listed = set(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \|", b1, re.M))
    assert listed, "B1 rendered no parseable rows — the finding list is unguarded"
    # The Part B intro's snapshot size is rendered from the same dict, but nothing
    # read it: `the same 16 names` could drift to any number with the suite green.
    assert f"the same {len(RETIRED_LITERAL)} names" in _doc(), (
        "the Part B intro no longer states the snapshot size the redirects were read from"
    )
    # The 16 snapshot redirects are pinned as literals so an in-repo change reds.
    assert len(RETIRED_LITERAL) == 16, (
        f"the snapshot is {len(RETIRED_LITERAL)} names, not the pinned 16"
    )
    assert listed == DISAGREEMENTS_LITERAL, (
        "B1 does not list exactly the disagreements.\n"
        f"  listed but agreeing: {sorted(listed - DISAGREEMENTS_LITERAL)}\n"
        f"  missing from B1: {sorted(DISAGREEMENTS_LITERAL - listed)}"
    )
    m = re.search(r"\*\*(\d+) findings\.\*\*", b1)
    assert m, "B1's findings count is missing or changed shape"
    assert int(m.group(1)) == len(DISAGREEMENTS_LITERAL)

    # B1's three FACTUAL columns are pinned against Part B cell-for-cell. Without
    # this, only the first cell is read and a row can state a redirect that
    # contradicts the Part B row it came from — internally contradictory, green.
    part_b = _parse_part_b()
    b1_rows = {}
    for line in b1.splitlines():
        row = re.match(
            r"^\| `([a-z_][a-z0-9_]*)` \| `([A-Za-z_:]+)` \| `(.+?)` \| "
            r"(`[A-Za-z_:]+`|—) \|$",
            line,
        )
        if row:
            b1_rows[row.group(1)] = (
                row.group(2), row.group(3), row.group(4),
            )
    assert set(b1_rows) == DISAGREEMENTS_LITERAL, (
        "B1's rows are not parseable as four columns, so its findings are unchecked:\n"
        f"  parsed {sorted(b1_rows)} vs pinned {sorted(DISAGREEMENTS_LITERAL)}"
    )
    for name, (d_old, redirect, d_new) in b1_rows.items():
        ref = part_b[name]
        assert d_old == ref["destination"], (
            f"B1's `0.1 destination` for {name} is {d_old!r}; Part B says "
            f"{ref['destination']!r} — the finding list contradicts the table"
        )
        assert redirect == ref["redirect"], (
            f"B1's `#4031 redirect` for {name} is {redirect!r}; Part B says "
            f"{ref['redirect']!r}"
        )
        assert d_new == ref["redirect_destination"], (
            f"B1's `Redirect's 0.1 destination` for {name} is {d_new!r}; Part B says "
            f"{ref['redirect_destination']!r}"
        )
    # The finding's tracking reference is a LITERAL here, not imported from the
    # generator, so dropping or renumbering it reds. That the issue itself still
    # exists is NOT verifiable offline — see the residual note in the PR body.
    assert "tortoise #4475" in b1, (
        "B1 no longer names the tracking issue for the disagreements"
    )


def test_snapshot_provenance_is_pinned() -> None:
    """The snapshot's branch+commit are pinned as LITERALS, not read from the generator.

    The 16 redirect VALUES are pinned, but the commit they were claimed to be read
    from was provenance no test read — a wrong SHA survived every assertion. The
    branch may move (the redirects are recorded as a snapshot, honestly), so only
    the SHA is pinned: it names the exact tree the snapshot was taken from.
    """
    doc = _doc()
    sha = "c01ad93b569e514e94d0d813c0216de9cb746d3b"
    assert sha in doc, (
        "the document no longer records the commit the #4031 snapshot was read from"
    )
    assert "origin/feat/3883-retired-name-warning" in doc, (
        "the document no longer names the branch the snapshot came from"
    )


# ── The generator's failure paths ────────────────────────────────────────────


def test_generator_exits_nonzero_when_the_map_and_registry_disagree(monkeypatch, capsys) -> None:
    """The generator must FAIL, not paper over, a registry/map disagreement.

    Behavioural: `main()` is driven with `--check` and a phantom registry row,
    so the assertion is on the exit code the CI form produces — not on a
    private helper's return value.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    real = mrt._registry_rows()
    phantom = {
        "name": "tortoise_zzz_phantom", "lineno": 1, "sdk_method": "",
        "resolves": False, "read_only": True,
    }
    monkeypatch.setattr(mrt, "_registry_rows", lambda: [*real, phantom])
    monkeypatch.setattr(sys, "argv", ["mcp_rename_table.py", "--check"])
    assert mrt.main() == 1, "the generator exited 0 with an unmapped registry tool"
    err = capsys.readouterr().err
    assert "tortoise_zzz_phantom" in err, (
        "the failure message does not name the offending tool, so it reads like a bug "
        "rather than a fixable finding"
    )


def test_rendezvous_hint_fires_only_for_the_4031_collision() -> None:
    """Landing #4031 first turns 16 map keys stale; the failure must say so.

    A red that reads like a bug gets worked around; a red that reads like a
    rendezvous gets acted on. This pins that the hint is produced for exactly
    that case and not for an arbitrary stale key.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    real = mrt._registry_rows()
    pruned = [r for r in real if r["name"] not in RETIRED_LITERAL]
    hint = mrt._rendezvous_hint(pruned)
    assert hint, "pruning the 16 #4031 names produced no rendezvous hint"
    assert "#4031" in hint and "RETIRED_TOOL_REGISTRY" in hint, (
        f"the rendezvous hint does not name the PR or the fix: {hint!r}"
    )
    # An unrelated stale key must NOT be described as the #4031 rendezvous.
    # (Dropping a row that is NOT in the snapshot leaves the 16 #4031 names stale
    # PLUS one more, so the set is no longer exactly the snapshot.)
    stray = [r for r in real if r["name"] != "tortoise_audit"]
    assert mrt._rendezvous_hint(stray) is None, (
        "an unrelated stale map key was mislabelled as the #4031 rendezvous"
    )


def test_validate_rejects_a_bad_snapshot_redirect(monkeypatch) -> None:
    """A #4031 redirect that names no registry tool must fail the build.

    The snapshot is authored data, so it is the one input nothing else checks;
    without this a typo'd redirect renders an agreement row comparing a
    destination to `None`.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    assert mrt._validate(mrt._rows(mrt._registry_rows())) == [], (
        "the live map/snapshot is not internally clean"
    )
    monkeypatch.setitem(mrt.RETIRED_USE_INSTEAD, "tortoise_get_point", "not_a_tool_call")
    errs = mrt._validate(mrt._rows())
    assert any("tortoise_get_point" in e for e in errs), (
        f"a redirect naming no tool was accepted: {errs!r}"
    )


def test_redirect_target_extracts_the_tool_name() -> None:
    """The redirect parser is the comparison's only oracle, so pin it on the snapshot."""
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    for expr, want in [
        ('tortoise_get(id, type="point")', "tortoise_get"),
        ('tortoise_overview(section="tags")', "tortoise_overview"),
        ("tortoise_index_files(directory)", "tortoise_index_files"),
        ("nothing_here", None),
    ]:
        assert mrt._redirect_target(expr) == want, (
            f"_redirect_target({expr!r}) returned {mrt._redirect_target(expr)!r}, "
            f"expected {want!r}"
        )


# ── Drift gate ───────────────────────────────────────────────────────────────

def test_mcp_rename_table_not_drifted() -> None:
    """The committed table matches what the generator produces now."""
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, (
        "The MCP rename table is stale, or the map/snapshot disagrees with the registry. "
        "Regenerate with:\n"
        "    uv run python tools/mcp_rename_table.py\n"
        f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_check_exits_nonzero_on_drift() -> None:
    """`--check` must FAIL on drift, not merely succeed when clean.

    The sibling suite's only `--check` test asserted a zero exit, so a mutation
    making `main()` return 0 unconditionally — disarming the entire drift gate
    — would have passed. This is the negative arm.
    """
    with tempfile.TemporaryDirectory() as td:
        backup = Path(td) / "mcp-rename-table.md"
        shutil.copy2(DOC, backup)
        try:
            DOC.write_text(_doc() + "\nDRIFT\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(GENERATOR), "--check"],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            assert proc.returncode != 0, (
                "--check exited 0 on a deliberately drifted document; the drift gate "
                "is disarmed"
            )
        finally:
            shutil.copy2(backup, DOC)

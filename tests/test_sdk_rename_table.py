"""The SDK rename table must not drift from the code and docs it describes (#4282).

`tools/sdk_rename_table.py` reads every `sdk.py:N` straight from the AST, parses the
40 target names out of `docs/product/beta-sdk-surface.md`, and parses the R/W/N group
partition out of `docs/product/canonical-sdk-methods.md`. What it can still do is go
*stale* — someone edits `sdk.py` or either doc and never regenerates — which is exactly
the drift the generator's own docstring promises cannot happen. This is the enforcement.

What this file deliberately does NOT do is import the generator's data and compare it to
itself. The sibling Phase 0.1 PR was red-green for rounds because a test did
`from tools.bridge_table import C1_FINDING` and then asserted the rendered doc equalled
that imported constant: both sides moved together, so the assertion proved nothing. Here
every expected value is either a **literal** or derived from an **independent oracle** —
a fresh AST walk, or a fresh parse of the source doc.

Registered in `config/ci-surfaces.yml` (`manifest-integrity` fails on an unregistered
test file; `test_bridge_table.py` is the precedent for the dual `api` + `core` entry).
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "tools" / "sdk_rename_table.py"
DOC = ROOT / "docs" / "product" / "sdk-rename-table.md"
BETA = ROOT / "docs" / "product" / "beta-sdk-surface.md"
CANON = ROOT / "docs" / "product" / "canonical-sdk-methods.md"

# One Part A row: `| 1 | \`name\` | \`sdk.py:123\` | R1 | \`target\` | stated | citation |`.
# The citation cell is captured greedily to the final pipe so escaped (`\|`) pipes
# inside a quoted markdown row do not split it.
ROW_RE = re.compile(
    r"^\| \d+ \| `([a-z_][a-z0-9_]*)` \| `sdk\.py:(\d+)` \| ([A-Z]\d+|ARCHIVE) \| "
    r"`([^`]+)` \| (stated|derived|unbacked) \| (.*) \|$"
)
CITE_RE = re.compile(r"^`([a-z-]+\.md)` — “(.*)”$")


# ─────────────────────────────────────────────────────────────────────
# INDEPENDENT ORACLES — derived here, not imported from the generator
# ─────────────────────────────────────────────────────────────────────
def public_methods_independently() -> dict[str, int]:
    """Public method name -> `def` line, by walking `tortoise/sdk.py` here."""
    tree = ast.parse((ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            return {
                s.name: s.lineno
                for s in node.body
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not s.name.startswith("_")
            }
    raise AssertionError("no `class TortoiseSDK` found in tortoise/sdk.py")


def targets_independently() -> list[str]:
    """The 40 target names, parsed here from the beta doc's surface table."""
    out: dict[int, str] = {}
    for line in BETA.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        out[int(cells[0])] = re.sub(r"\(.*\)$", "", cells[1].strip("`").strip())
    return [out[i] for i in sorted(out)]


def part_a_rows() -> list[dict]:
    rows = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            rows.append({
                "name": m.group(1), "line": int(m.group(2)), "group": m.group(3),
                "target": m.group(4), "basis": m.group(5), "cite": m.group(6),
            })
    return rows


def _section(name: str) -> str:
    """The text of the `## {name}` / `### {name}` section up to the next heading."""
    text = DOC.read_text(encoding="utf-8")
    marker = f"## {name}"
    assert marker in text, f"the generated doc has no `{marker}` section"
    body = text.split(marker, 1)[1]
    return re.split(r"\n###? ", body)[0]


def _first_column(section: str) -> list[str]:
    out = []
    for line in section.splitlines():
        m = re.match(r"^\| `([A-Za-z_][A-Za-z0-9_]*)` \|", line)
        if m:
            out.append(m.group(1))
    return out


def canonical_groups_independently() -> dict[str, list[str]]:
    """The canonical inventory partition, parsed here from the source doc.

    Scoped to the `## The groups (the earlier sketch)` section: the doc reuses the
    R/W labels for a second, differently-shaped table further down.
    """
    text = CANON.read_text(encoding="utf-8")
    body = text[text.index("## The groups (the earlier sketch)"):text.index(
        "## What each group does")]
    out: dict[str, list[str]] = {}
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not re.fullmatch(r"[RW]\d+|N\d+", cells[0]):
            continue
        idx = 2 if cells[0].startswith("N") else 3
        out[cells[0]] = (re.findall(r"`([a-z_][a-z0-9_]*)`", cells[idx])
                         if len(cells) > idx else [])
    return out


@pytest.fixture(scope="module")
def generator_module():
    """Import the generator once, for the validation-path tests only.

    The constants that describe the surface are never imported: they are exactly what
    these tests exist to check.
    """
    sys.path.insert(0, str(ROOT))
    from tools import sdk_rename_table

    return sdk_rename_table


# ─────────────────────────────────────────────────────────────────────
# THE GATE — `--check` must be clean, and must RED on drift
# ─────────────────────────────────────────────────────────────────────
def test_check_mode_is_clean() -> None:
    """The committed doc must equal a fresh render. This is the CI form."""
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "the generated SDK rename table is stale — run "
        f"`uv run python tools/sdk_rename_table.py`.\n{proc.stderr}"
    )
    assert "is current" in proc.stdout


def test_check_mode_detects_a_drifted_copy(tmp_path: Path) -> None:
    """A deliberately drifted copy must give a NON-ZERO exit.

    Without this, a mutation that makes `main()` return 0 unconditionally disarms the
    whole drift gate while `test_check_mode_is_clean` stays green. Run in-process (not
    as a second subprocess) purely to avoid paying a second ~16s `tortoise.sdk` import;
    it exercises the same `--check` branch.

    The drift is a **count in the prose** — the mutation class the sibling PR's review
    found unguarded (the table's rows were checked; its sentences were not).
    """
    sys.path.insert(0, str(ROOT))
    from tools import sdk_rename_table

    drifted = tmp_path / "sdk-rename-table.md"
    text = DOC.read_text(encoding="utf-8")
    assert "150 public methods" in text
    drifted.write_text(text.replace("150 public methods", "151 public methods"),
                       encoding="utf-8")

    rc = sdk_rename_table.main(["--check", "--out", str(drifted)])
    assert rc != 0, "a drifted copy passed `--check` — the drift gate is disarmed"


# ─────────────────────────────────────────────────────────────────────
# PART A — the table
# ─────────────────────────────────────────────────────────────────────
def test_part_a_covers_exactly_the_public_surface() -> None:
    """One row per public method, and nothing else.

    Expected values come from a fresh AST walk here, so a generator change that drops,
    duplicates or invents a method fails even though `--check` would agree with itself.
    """
    rows = part_a_rows()
    names = [r["name"] for r in rows]
    assert len(rows) == 150, f"Part A has {len(rows)} rows, expected the 150-method surface"
    assert len(set(names)) == len(names), (
        "Part A lists a method twice — `--check` cannot see a duplicated row because the "
        "duplicate is in the constant too"
    )
    assert sorted(names) == sorted(public_methods_independently())


def test_part_a_line_numbers_point_at_the_right_method() -> None:
    """Every `sdk.py:N` citation must equal the method's real `def` line.

    This closes the artifact's central promise — that the citations cannot drift — and
    it is compared against a **fresh AST walk**, not a second reading of the generator.
    A constant offset added to every emitted line number makes all 150 citations wrong
    while every other test stays green.
    """
    truth = public_methods_independently()
    bad = [
        f"{r['name']}: cited :{r['line']}, defined at :{truth.get(r['name'])}"
        for r in part_a_rows()
        if truth.get(r["name"]) != r["line"]
    ]
    assert not bad, "Part A cites the wrong `sdk.py` line for:\n  " + "\n  ".join(bad)


def test_every_target_is_on_the_approved_surface() -> None:
    """The Target column may name a target, or be one of the three dispositions."""
    approved = set(targets_independently())
    assert len(approved) == 40, (
        f"parsed {len(approved)} target names from beta-sdk-surface.md, expected 40"
    )
    allowed = approved | {"UNCHANGED", "DISCARDED", "UNBACKED"}
    bad = sorted({r["target"] for r in part_a_rows()} - allowed)
    assert not bad, f"Part A names destinations that are not on the approved surface: {bad}"


def test_every_citation_still_resolves_in_the_doc_it_names() -> None:
    """A citation is a claim; the quote must still be in the cited doc.

    The generator validates this too, but a mutation that drops that check would leave
    every row pointing at prose that has since been reworded — and `--check` would not
    notice, because both sides are stale together.
    """
    docs = {
        "beta-sdk-surface.md": BETA.read_text(encoding="utf-8"),
        "canonical-sdk-methods.md": CANON.read_text(encoding="utf-8"),
    }
    bad = []
    for r in part_a_rows():
        if r["basis"] == "unbacked":
            assert r["cite"] == "**no doc states a destination**", (
                f"{r['name']}: an unbacked row must not carry a citation"
            )
            continue
        m = CITE_RE.match(r["cite"])
        assert m, f"{r['name']}: unparseable citation cell: {r['cite']!r}"
        doc, quote = m.group(1), m.group(2).replace("\\|", "|")
        assert doc in docs, f"{r['name']}: cites an unknown doc {doc!r}"
        if quote not in docs[doc]:
            bad.append(f"{r['name']} → {doc}: {quote[:60]!r}")
    assert not bad, "Part A cites text that is no longer in the cited doc:\n  " + \
                    "\n  ".join(bad)


def test_basis_and_target_agree() -> None:
    """`unbacked` is exactly the `UNBACKED` target; every other row is stated/derived."""
    for r in part_a_rows():
        if r["target"] == "UNBACKED":
            assert r["basis"] == "unbacked", f"{r['name']}: UNBACKED must have basis=unbacked"
        else:
            assert r["basis"] in ("stated", "derived"), (
                f"{r['name']}: a row with a destination must be stated or derived"
            )


def test_the_resolved_partition_never_emits_the_wildcard_group() -> None:
    """`W16` is the canonical doc's wildcard spelling of N1–N6; it has no own members."""
    groups = {r["group"] for r in part_a_rows()}
    assert "W16" not in groups, (
        "a row carries group W16 — its members are wildcards that duplicate N1–N6, so the "
        "partition must resolve each method to its family group"
    )
    assert groups <= {f"R{i}" for i in range(1, 10)} | {f"W{i}" for i in range(1, 18)} \
        | {f"N{i}" for i in range(1, 7)} | {"ARCHIVE"}


# ─────────────────────────────────────────────────────────────────────
# THE PROSE COUNTS — every one is a claim
# ─────────────────────────────────────────────────────────────────────
def test_prose_counts_are_the_literals_the_doc_claims() -> None:
    text = DOC.read_text(encoding="utf-8")
    for literal in (
        "150 public methods",
        "40 target methods",
        "110 of the 150 are renames",
        "**4** are already targets (unchanged)",
        "**33** are discarded with a rationale",
        "**3** have no destination",
        "only **4** of them exist on `TortoiseSDK` today",
        "**36** are Phase 2 work",
    ):
        assert literal in text, f"the headline no longer says {literal!r}"

    rows = part_a_rows()
    assert sum(1 for r in rows if r["target"] == "UNCHANGED") == 4
    assert sum(1 for r in rows if r["target"] == "DISCARDED") == 33
    assert sum(1 for r in rows if r["target"] == "UNBACKED") == 3


def test_part_a_rows_are_well_formed_markdown() -> None:
    r"""Every Part A row must carry exactly 7 cells when split on UNESCAPED pipes.

    The citation column quotes markdown table rows, so their `|` must be escaped as
    `\|` — otherwise the row silently grows extra columns and the quoted evidence
    renders as table syntax. Nothing else in the suite can see that: the row regex is
    greedy to the final pipe, so a broken table still parses and still round-trips.
    """
    bad = []
    for line in _section("Part A — every public method and its migration row").splitlines():
        if not re.match(r"^\| \d+ \| ", line):
            continue
        cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
        if len(cells) != 7:
            bad.append((line[:80], len(cells)))
    assert not bad, (
        "Part A rows have the wrong number of markdown cells (escaped pipes are "
        f"required in the citation column): {bad}"
    )


def test_group_column_matches_the_canonical_inventory() -> None:
    """Each row's Group must be the canonical group that names the method.

    Expected values come from a fresh parse of `canonical-sdk-methods.md` here.
    `W16` is the doc's wildcard spelling of N1–N6, so it contributes no distinct
    member; `backfill_v25` lives in the Archived table and is labelled ARCHIVE.
    """
    group_of = {m: g for g, ms in canonical_groups_independently().items() for m in ms}
    group_of["backfill_v25"] = "ARCHIVE"
    wrong = {
        r["name"]: (r["group"], group_of.get(r["name"]))
        for r in part_a_rows() if r["group"] != group_of.get(r["name"])
    }
    assert not wrong, f"Part A's Group column disagrees with the inventory: {wrong}"


def test_part_b_counts_match_the_rows_they_list() -> None:
    """Part B's counts must equal the members it lists, and the total must be 150."""
    rows = []
    total = None
    for line in _section("Part B — destination counts").splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        dest, names, count = cells
        if not count.strip("*").isdigit():
            continue  # the header row: `| Destination | Current methods | Count |`
        if dest.startswith("**total**"):
            total = int(count.strip("*"))
            continue
        rows.append((dest.strip("`"), re.findall(r"`([a-z_][a-z0-9_]*)`", names),
                     int(count)))
    assert rows, "parsed no Part B rows"
    assert total == 150, f"Part B's total row says {total}, expected 150"
    bad = [(d, len(ms), c) for d, ms, c in rows if len(ms) != c]
    assert not bad, f"Part B counts disagree with the members listed: {bad}"
    assert sum(c for _, _, c in rows) == 150

    by_target: dict[str, set[str]] = {}
    for r in part_a_rows():
        by_target.setdefault(r["target"], set()).add(r["name"])
    assert {d: set(ms) for d, ms, _ in rows} == by_target, (
        "Part B and Part A disagree about which methods reach each destination"
    )


def test_basis_split_is_the_literal() -> None:
    """`derived` is the 19 rows backed only by a wildcard/namespace statement.

    `stated` vs `derived` is the column that says *how strongly* a mapping is backed.
    A mutation that forces every row to `stated` leaves every other test green, so
    the split is pinned here by name.
    """
    derived = {r["name"] for r in part_a_rows() if r["basis"] == "derived"}
    assert derived == {
        "apikey_create", "apikey_list", "apikey_revoke", "graph_count",
        "invitation_accept", "invitation_create", "invitation_get_by_id",
        "invitation_get_by_token", "invitation_list", "invitation_revoke",
        "membership_create", "membership_delete", "membership_list",
        "org_get", "org_list", "signup_token_lookup", "signup_token_recover",
        "signup_token_revoke", "stale_points",
    }


def test_part_c1_lists_exactly_the_targets_with_no_def() -> None:
    """C1 is the finding Part A exists to produce; it must not empty itself silently.

    Expected values are recomputed here from the beta doc + a fresh AST walk.
    """
    expected = set(targets_independently()) - set(public_methods_independently())
    assert len(expected) == 36
    listed = set(_first_column(_section("C1 — target methods with no `def` on `TortoiseSDK`")))
    assert listed == expected, (
        "Part C1 does not list exactly the target methods with no `def`.\n"
        f"  listed but has a def: {sorted(listed - expected)}\n"
        f"  missing from C1 ({len(expected - listed)}): {sorted(expected - listed)}"
    )


def test_part_c2_is_exactly_the_unbacked_rows() -> None:
    """The unbacked list is a literal: three methods, by name.

    A mutation that makes the section render "None." would leave the tests above green,
    so this pins it against a hand-written constant.
    """
    listed = set(_first_column(_section("C2 — rows with NO doc backing")))
    assert listed == {"org_create", "compute_reputation", "record_calibration"}
    assert listed <= set(public_methods_independently()), (
        "the C2 names must be real public methods"
    )
    unbacked_in_table = {r["name"] for r in part_a_rows() if r["target"] == "UNBACKED"}
    assert listed == unbacked_in_table


def test_part_c4_lists_the_doc_code_name_mismatches() -> None:
    """Seven names the docs use that are not `def`s, two with no referent at all."""
    section = _section("C4 — names the disposition docs use that are NOT SDK methods")
    names = re.findall(r"^\| `([a-z_][a-z0-9_]*)` \|", section, re.M)
    assert set(names) == {
        "recall_legs", "stale", "count_memory_graphs", "set_memory_graph_name",
        "set_memory_graph_backend", "index_sources", "withdraw_knowledge",
    }
    public = set(public_methods_independently())
    assert not (set(names) & public), (
        "a C4 'phantom' is now a real method — the finding is resolved and the row must go"
    )
    no_referent = re.findall(r"^\| `[a-z_]+` \| \*\*none\*\* \|", section, re.M)
    assert len(no_referent) == 2, (
        "exactly two of the doc's names have no referent: `set_memory_graph_backend` and "
        "`withdraw_knowledge`"
    )


def test_load_bearing_mappings_are_literal() -> None:
    """A handful of rows pinned by hand — the ones a rename must not silently reverse."""
    got = {r["name"]: r["target"] for r in part_a_rows()}
    expected = {
        # The load-bearing SDK names from canonical-sdk-methods.md's caller list.
        "create_point": "create_entity",
        "create_operator": "link_entities",
        "create_event": "create_entity",
        "get_point": "get_entity",
        "ingest": "write_knowledge_batch",
        "recall_state": "check_confidence",
        "promote_point": "DISCARDED",
        "mitigate_operator": "adjust_relationship",
        "compute_confidence": "refresh_confidence",
        "retract_point": "update_knowledge",
        "dream": "refresh_confidence",
        "tortoise_fts_query": "search_knowledge",
        "close": "UNCHANGED",
        # Rows where the two docs disagree, or a capricious rewrite is easy.
        "restore_point_at": "get_historical_knowledge",
        "list_sources": "list_knowledge",
        "test_guard": "DISCARDED",
        "recall_subgraph": "DISCARDED",
        "file_decision": "write_question",
        "file_human_approval": "record_decision",
        "graph_set_recording": "update_memory_graph",
        "graph_count": "list_memory_graphs",
        "backfill_v25": "DISCARDED",
        "supersede_point": "supersede_knowledge",
        "invalidate_point": "update_knowledge",
        "list_batches": "list_knowledge",
        "create_or_update_point": "create_entity",
    }
    assert set(expected) <= set(got), f"missing rows: {sorted(set(expected) - set(got))}"
    wrong = {k: (got[k], v) for k, v in expected.items() if got[k] != v}
    assert not wrong, f"load-bearing mappings changed: {wrong}"


def test_every_group_has_a_pinned_representative() -> None:
    """One non-overridden member per canonical group, pinned by target.

    A mutation that flips a whole group's default target changes every one of its
    un-overridden members at once — the readable load-bearing list above only covers
    the names a caller is most likely to grep for, so it can miss a group whose
    members are all "quiet". This closes that hole for every group in the partition.
    """
    got = {r["name"]: r["target"] for r in part_a_rows()}
    representatives = {
        "R1": ("annotate_ask_hits", "search_knowledge"),
        "R2": ("query", "list_knowledge"),
        "R3": ("recall_state", "check_confidence"),
        "R4": ("get_events", "get_entity"),
        "R5": ("traverse", "explore_connections"),
        "R6": ("audit", "graph_overview"),
        "R7": ("review_connections", "review_link_candidates"),
        "R8": ("events_poll", "poll_events"),
        "R9": ("list_batch", "list_knowledge"),
        "W1": ("create_point", "create_entity"),
        "W2": ("ingest", "write_knowledge_batch"),
        "W3": ("create_source", "register_source"),
        "W4": ("index_file", "index_sources_from_directory"),
        "W5": ("checkpoint", "DISCARDED"),
        "W6": ("capture_session", "mine_knowledge_from_session"),
        "W7": ("commit_session", "mine_knowledge_from_session"),
        "W8": ("assess_source", "manage_source_trust"),
        "W9": ("create_operator", "link_entities"),
        "W10": ("file_human_approval", "record_decision"),
        "W11": ("update_point", "update_knowledge"),
        "W12": ("delete_point", "delete_knowledge"),
        "W13": ("compute_confidence", "refresh_confidence"),
        "W14": ("approve_merge", "UNCHANGED"),
        "W15": ("mitigate_operator", "adjust_relationship"),
        "W17": ("ulid", "DISCARDED"),
        "N1": ("org_create", "UNBACKED"),
        "N2": ("graph_count", "list_memory_graphs"),
        "N3": ("membership_create", "add_member"),
        "N4": ("apikey_create", "create_key"),
        "N5": ("invitation_create", "DISCARDED"),
        "N6": ("signup_token_lookup", "DISCARDED"),
        "ARCHIVE": ("backfill_v25", "DISCARDED"),
    }
    wrong = {
        g: (got.get(m), want) for g, (m, want) in representatives.items()
        if got.get(m) != want
    }
    assert not wrong, f"a group's default target changed: {wrong}"


# ─────────────────────────────────────────────────────────────────────
# THE GENERATOR MUST FAIL, NOT PAPER OVER
# ─────────────────────────────────────────────────────────────────────
def test_validate_rejects_an_unmapped_method(generator_module) -> None:
    """A public method with no disposition must FAIL the build."""
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites.update(generator_module.TENSION_CITES)
    cites.update(generator_module.PHANTOM_CITES)
    assert generator_module._validate(methods, groups, targets, cites) == []

    methods["a_brand_new_method"] = 99999
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("UNMAPPED public method" in e and "a_brand_new_method" in e for e in errs), (
        f"a method with no map entry did not fail the build: {errs}"
    )


def test_validate_rejects_a_resolved_phantom(generator_module) -> None:
    """If a doc's phantom name becomes a real method, the build must fail."""
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites.update(generator_module.TENSION_CITES)
    cites.update(generator_module.PHANTOM_CITES)

    methods["set_memory_graph_backend"] = 88888
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("PHANTOM RESOLVED" in e for e in errs), (
        f"a resolved phantom did not fail the build: {errs}"
    )


def test_validate_rejects_a_drifted_citation(generator_module) -> None:
    """A citation whose quote is no longer in its doc must FAIL the build."""
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites["drift"] = ("beta-sdk-surface.md", "this sentence is in no doc at all", set())
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("CITATION DRIFT" in e and "drift" in e for e in errs), (
        f"a drifted citation did not fail the build: {errs}"
    )


def test_validate_rejects_a_broken_partition(generator_module) -> None:
    """If a doc claims a method the code does not have, the build must fail."""
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    # Simulate the canonical doc growing a member that is not a public def.
    groups["R1"] = groups["R1"] + ["a_method_the_code_lacks"]
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites.update(generator_module.TENSION_CITES)
    cites.update(generator_module.PHANTOM_CITES)
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("STALE canonical member" in e for e in errs), (
        f"a canonical member with no `def` did not fail the build: {errs}"
    )


def test_doc_header() -> None:
    """The doc must announce what it is and that editing it by hand is a mistake."""
    text = DOC.read_text(encoding="utf-8")
    assert text.startswith("# Phase 0.3b — the SDK rename table\n")
    assert "**GENERATED — do not edit.**" in text

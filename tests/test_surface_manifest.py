"""The agent-facing surface list and the expansion gate (#3863).

These tests are the machine half of the acceptance criteria:

* AC1  — the list is generated from the declaration and is in sync with it
* AC11 — the baseline covers the declaration, and the gate fails closed
* AC13 — the ordering lint's twelve properties hold, including the twelfth: the artifact is
         verified against a fresh DERIVATION, not only against itself

They are deliberately fast and dependency-free: they execute the declaration and
compare it to the checked-in baseline. No database, no network, no subprocess
pytest run.
"""

from __future__ import annotations

import argparse
import copy
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "config" / "surface-manifest.yml"
RENDERED = ROOT / "docs" / "product" / "mcp-sdk-surface.md"


def _public_sdk_methods() -> set[str]:
    from tortoise.sdk import TortoiseSDK

    return {
        name
        for name in dir(TortoiseSDK)
        if not name.startswith("_") and callable(getattr(TortoiseSDK, name))
    }


def _manifest() -> dict:
    with MANIFEST.open() as fh:
        return yaml.safe_load(fh)


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_baseline_exists_and_is_a_frozen_snapshot():
    doc = _manifest()
    assert doc["issue"] == 3863
    assert doc["cut_at_commit"], "the baseline must record the commit it was cut at"
    assert doc["rows"], "the baseline cannot be empty"


def test_every_registered_tool_is_in_the_baseline():
    from tortoise.tool_registry import TOOL_REGISTRY

    declared = {entry.name for entry in TOOL_REGISTRY}
    baseline = {r["name"] for r in _manifest()["rows"] if not str(r["name"]).startswith("sdk:")}
    assert declared == baseline, (
        "the registry and the approved baseline have diverged — a new tool needs an "
        f"explicit human decision (#3863). added={sorted(declared - baseline)} "
        f"removed={sorted(baseline - declared)}"
    )


def test_every_public_sdk_method_is_in_the_baseline():
    baseline = {
        r["method"] for r in _manifest()["rows"] if str(r["name"]).startswith("sdk:")
    }
    exempt = {
        r["method"]
        for r in _manifest()["rows"]
        if str(r["name"]).startswith("sdk:") and r.get("exemption") is True
    }
    declared = _public_sdk_methods()
    assert declared == baseline - exempt | (declared & exempt), (
        "a public SDK method is an endpoint; the baseline must carry every one. "
        f"added={sorted(declared - baseline - exempt)} removed={sorted(baseline - declared)}"
    )


def test_the_guard_passes_on_the_checked_in_baseline():
    result = _run("tools/surface-guard.py")
    assert result.returncode == 0, f"the gate reds on the checked-in baseline:\n{result.stdout}\n{result.stderr}"


def test_the_order_lint_passes():
    result = _run("tools/surface_manifest.py", "check")
    assert result.returncode == 0, f"the ordering lint fails:\n{result.stdout}\n{result.stderr}"


def test_the_guard_fails_closed_when_the_baseline_is_missing(tmp_path):
    result = _run("tools/surface-guard.py", "--manifest", str(tmp_path / "nope.yml"))
    assert result.returncode == 1, "a gate that cannot read its evidence must not report success"
    assert "not found" in result.stdout


def test_the_guard_reds_when_a_tool_is_added(tmp_path):
    """The whole point: a registry entry the owner never approved is a failure."""
    doc = _manifest()
    doc["rows"] = [r for r in doc["rows"] if r["name"] != "tortoise_search"]
    stripped = tmp_path / "stripped.yml"
    stripped.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(stripped))
    assert result.returncode == 1, "an added tool did not red the gate"
    assert "tortoise_search" in result.stdout


def test_the_guard_reds_when_a_public_method_is_added(tmp_path):
    doc = _manifest()
    doc["rows"] = [r for r in doc["rows"] if r.get("method") != "close"]
    stripped = tmp_path / "stripped.yml"
    stripped.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(stripped))
    assert result.returncode == 1, "an added public SDK method did not red the gate"
    assert "close" in result.stdout


def test_an_exemption_cannot_outlive_the_fact_it_records(tmp_path):
    """A method marked exempt that has since become reachable must be un-exempted."""
    doc = _manifest()
    target = next(r for r in doc["rows"] if r.get("method") == "create_point")
    target["exemption"] = True
    modified = tmp_path / "exempt.yml"
    modified.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(modified))
    assert result.returncode == 1
    assert "exemption" in result.stdout


def test_approval_cannot_be_claimed_without_recording_it(tmp_path):
    doc = _manifest()
    doc["approval_status"] = "approved"
    modified = tmp_path / "approved.yml"
    modified.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(modified))
    assert result.returncode == 1, "`approved` with no per-row approval must red"
    assert "approval" in result.stdout


def test_unknown_approval_status_is_a_failure(tmp_path):
    doc = _manifest()
    doc["approval_status"] = "mostly-fine"
    modified = tmp_path / "odd.yml"
    modified.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(modified))
    assert result.returncode == 1


def _baseline_tool_count() -> int:
    """The approved tool count, read from the baseline rather than hardcoded."""
    return sum(1 for r in _manifest()["rows"] if not str(r["name"]).startswith("sdk:"))


def test_the_rendered_list_agrees_with_the_manifest():
    """AC1: the document the owner reads is generated from the manifest and stays in sync."""
    assert RENDERED.exists(), "docs/product/mcp-sdk-surface.md must exist"
    doc = _manifest()
    text = RENDERED.read_text(encoding="utf-8")
    tools = [r for r in doc["rows"] if not str(r["name"]).startswith("sdk:")]
    sdk = [r for r in doc["rows"] if str(r["name"]).startswith("sdk:")]
    assert doc["cut_at_commit"][:9] in text
    assert f"**{len(tools)} MCP tools · {len(sdk)} SDK methods.**" in text
    missing = [r["name"] for r in tools if f"`{r['name']}`" not in text]
    assert not missing, f"tools in the manifest but not in the rendered list: {missing[:10]}"
    missing_sdk = [r["method"] for r in sdk if f"`TortoiseSDK.{r['method']}`" not in text]
    assert not missing_sdk, f"SDK methods missing from the rendered list: {missing_sdk[:10]}"

    # The DEPENDENCY column, and every marker the legend promises. An earlier version
    # computed `reaches` and `flags` and then never emitted them, so the owner-facing
    # document documented a column that did not exist and the five false declarations were
    # only discoverable by reading free text.
    assert "| Reaches |" in text, "the dependency column is not rendered"
    for row in tools:
        expected = row.get("sdk_method") or "handler-served"
        assert expected in text or f"~~{expected}~~" in text, (
            f"{row['name']}'s declared dependency ({expected}) never renders"
        )
    if any("WHICH DOES NOT EXIST" in (r.get("dependency") or "") for r in tools):
        assert "⚠ FALSE DECLARATION" in text, "a false declaration renders with no marker"
    if any(r.get("lifecycle") == "deprecated alias" for r in tools):
        assert "DEPRECATED" in text, "a deprecated alias renders with no marker"

    # #3883: the retired block renders, names each replacement, and the legend must not
    # still claim that nothing warns a caller — a stale generated file kept exactly that
    # now-false sentence until an independent verifier regenerated it.
    for r in doc.get("retired") or []:
        assert f"`{r['name']}`" in text, f"{r['name']} missing from the retired section"
        assert f"`{r['use_instead']}`" in text, f"{r['name']}'s replacement never renders"
    assert "Nothing warns a caller today" not in text, (
        "the legend claims nothing warns a caller, but retired names now warn (#3883)"
    )
    if doc.get("retired"):
        assert "warn the caller with the" in text, "the retired legend is missing"


def test_the_retired_table_does_not_assert_a_nonexistent_sdk_method():
    """A retired alias may have declared an SDK method that never existed
    (`tortoise_health` -> `health`). The retired table must mark it, not describe it
    as public — the live table's `\u26a0 FALSE DECLARATION` distinction must survive."""
    from tortoise.sdk import TortoiseSDK

    text = RENDERED.read_text(encoding="utf-8")
    for r in _manifest().get("retired") or []:
        method = r.get("sdk_method")
        if method and not hasattr(TortoiseSDK, method):
            assert f"~~{method}~~" in text, (
                f"{r['name']} renders `{method}` as if it were a public SDK method, "
                "but TortoiseSDK has no such method"
            )


def test_split_clusters_carry_one_proposed_family_each():
    """AC13's cluster-family agreement, stated as a property over the declared set."""
    doc = _manifest()
    rows = [r for r in doc["rows"] if not str(r["name"]).startswith("sdk:")]
    clusters: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("cluster"):
            clusters.setdefault(row["cluster"], []).append(row)
    for cluster, members in clusters.items():
        families = {m["family"] for m in members}
        if len(families) > 1:
            proposals = {m.get("recommended_family") for m in members}
            assert len(proposals) == 1 and None not in proposals, (
                f"cluster {cluster!r} is split across {sorted(families)} but its members do "
                f"not agree on one recommended family: {proposals}"
            )
            assert all(m.get("proposed") for m in members)


def test_every_row_declares_whether_it_decides_or_merely_observes() -> None:
    """A recommendation that rests on usage is evidence, never a verdict.

    The distinction is load-bearing (owner ruling: an owner decision outranks a convergent
    standard). If a `keep` were ever presented as a decision, or a `merge` as an observation,
    the list would start arguing from what is done instead of what we decided to be.
    """
    manifest = _manifest()
    rows = [r for r in manifest["rows"] if not str(r["name"]).startswith("sdk:")]
    assert rows, "no tool rows"
    for row in rows:
        expected = "decided" if row["recommendation"] in ("merge", "kill", "fix-declaration") else "observed"
        assert row.get("basis") == expected, f"{row['name']}: {row['recommendation']} must be {expected}"
    decided = [r for r in rows if r["basis"] == "decided"]
    observed = [r for r in rows if r["basis"] == "observed"]
    assert len(decided) + len(observed) == len(rows)
    assert decided, "at least one recommendation must derive from a decision already made"


def _write(doc: dict, tmp_path) -> str:
    p = tmp_path / "mutated.yml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    return str(p)


def test_approved_status_requires_approval_on_SDK_rows_too(tmp_path):
    """P1 found by review: the approval check skipped every `sdk:` row.

    Half the surface (152 of 251 rows) was exempt from the one human-approval control
    the gate was built to enforce, so `approved` could be set with no SDK approval at all.
    """
    doc = _manifest()
    doc["approval_status"] = "approved"
    for row in doc["rows"]:
        if not str(row["name"]).startswith("sdk:"):
            row["approval"] = "PR#1 @owner"          # tools approved …
        else:
            row["approval"] = None                   # … SDK rows deliberately not
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "an unapproved SDK row did not red `approved`"
    assert "sdk:" in result.stdout


def test_the_guard_reds_when_a_tools_SDK_binding_moves(tmp_path):
    """P2 found by review: CONTRIBUTING promised this; the guard did not do it.

    `sdk_method` could be silently re-pointed on any tool while the required check stayed green.
    """
    doc = _manifest()
    for row in doc["rows"]:
        if row["name"] == "tortoise_search":
            row["sdk_method"] = "definitely_not_the_real_binding"
            break
    else:
        raise AssertionError("tortoise_search missing from the baseline")
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "a moved SDK binding did not red the gate"
    assert "binding" in result.stdout


def _load_guard():
    """Import tools/surface-guard.py (hyphenated filename) as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("surface_guard", ROOT / "tools" / "surface-guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_manifest_tool():
    """Import tools/surface_manifest.py (hyphenated filename) as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "surface_manifest", ROOT / "tools" / "surface_manifest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_retired_block_matches_the_declaration():
    """The baseline's `retired:` block is the approved retirement list."""
    from tortoise.tool_registry import RETIRED_TOOL_REGISTRY

    doc = _manifest()
    declared = {t.name: t.retired_use_instead for t in RETIRED_TOOL_REGISTRY}
    baseline = {r["name"]: r.get("use_instead") for r in doc["retired"]}
    assert declared == baseline
    # And none of them is a live surface row.
    live = {r["name"] for r in doc["rows"] if not str(r["name"]).startswith("sdk:")}
    assert not (live & set(declared))


def test_the_guard_reds_when_a_retired_name_stops_resolving(monkeypatch, capsys):
    """#3883's core contract: a retired name that no longer resolves is a SILENT
    removal — the exact failure the mechanism exists to prevent."""
    from tortoise import mcp_server

    guard = _load_guard()
    transform = next(
        t for t in mcp_server.mcp._transforms
        if isinstance(t, mcp_server._RetiredToolTransform)
    )
    victim = next(iter(transform._shims))
    saved = transform._shims.pop(victim)
    try:
        assert guard.main([]) == 1, "an unresolvable retired name did not red the gate"
    finally:
        transform._shims[victim] = saved
    out = capsys.readouterr().out
    assert "does NOT resolve" in out and victim in out


def test_the_guard_reds_when_a_retirement_is_in_the_baseline_only(tmp_path):
    """A retirement that is not in the declaration but IS approved is a change;
    the gate must not pass by ignoring the difference."""
    doc = _manifest()
    doc["retired"] = [
        *doc["retired"],
        {"name": "tortoise_not_real", "use_instead": "tortoise_query()", "sdk_method": None},
    ]
    path = tmp_path / "extra-retired.yml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(path))
    assert result.returncode == 1, "a baseline-only retirement did not red the gate"
    assert "tortoise_not_real" in result.stdout


def test_the_guard_reds_when_a_new_retirement_is_not_approved(tmp_path):
    """Retiring a name shrinks the surface; it needs the same approval an add does."""
    doc = _manifest()
    doc["retired"] = doc["retired"][:-1]
    path = tmp_path / "missing-retired.yml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    result = _run("tools/surface-guard.py", "--manifest", str(path))
    assert result.returncode == 1, "an unapproved retirement did not red the gate"
    assert "NEW RETIRED TOOL" in result.stdout


def test_the_order_lint_reds_on_a_retired_mismatch(tmp_path, monkeypatch, capsys, derived_baseline):
    """`check` owns the same contract on the generated artifact.

    Asserts the PROPERTY-9 message, not just the exit code: property 10 re-derives the same
    input and would make this pass on its own, so an exit-code assertion alone stopped
    isolating the retired-name contract (review finding).
    """
    sm = _load_manifest_tool()
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: derived_baseline)
    doc = _manifest()
    removed = doc["retired"][-1]["name"]
    doc["retired"] = doc["retired"][:-1]
    path = tmp_path / "manifest.yml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    rc = sm.cmd_check(argparse.Namespace())
    assert rc == 1, "the order lint passed a manifest whose retired block was incomplete"
    out = capsys.readouterr().out
    assert f"retired name {removed!r} is declared but not in the manifest" in out, out


# --- AC13 property 10: the artifact is verified against the CODE, not itself -----
# The lint's first nine properties compare the baseline to itself (order, clusters,
# families) and to two hand-authored tables. None compared it to the declaration, so the
# baseline's headline numbers and every derived column could be hand-edited while both
# this lint and the D2 expansion gate stayed green — measured: `counts.tools: 999` plus a
# doctored `keyword_distribution` passed both. These tests are the mutation evidence for
# the property that closes it.


@pytest.fixture(scope="module")
def derived_baseline() -> dict:
    """The real derivation, computed ONCE for the whole module (~20s).

    The mutation tests patch `build_doc` with this cache so each one costs milliseconds;
    the unpatched, end-to-end derivation is exercised by the subprocess
    `test_the_order_lint_passes` above, which reds the moment the committed artifact and
    the code part company.
    """
    return _load_manifest_tool().build_doc()


@pytest.fixture
def checker(monkeypatch, derived_baseline):
    """`check` bound to a redirected manifest, against the real derivation."""
    sm = _load_manifest_tool()
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))
    return sm


def _check(sm, doc: dict, tmp_path) -> tuple[int, str]:
    """Run `cmd_check` with a redirected manifest, capturing stdout AND stderr.

    stderr is captured because a traceback goes there: an assertion that the output carries
    no traceback is vacuous against a stdout-only buffer (review finding). `MANIFEST_FILE`
    is restored, so a test cannot leak the redirect into another one.
    """
    path = tmp_path / "manifest.yml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    import io
    from contextlib import redirect_stderr, redirect_stdout

    previous = sm.MANIFEST_FILE
    sm.MANIFEST_FILE = path
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = sm.cmd_check(argparse.Namespace())
    finally:
        sm.MANIFEST_FILE = previous
    return rc, buf.getvalue()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d["counts"].__setitem__("tools", 999), id="headline-count"),
        pytest.param(
            lambda d: d["counts"]["keyword_distribution"].__setitem__("fetch", 999),
            id="keyword-distribution",
        ),
    ],
)
def test_the_check_reds_on_a_hand_edited_count(checker, tmp_path, mutate):
    """Hand-edits the baseline exists to freeze that NO property compared.

    Both are the same comparison (`counts`) on different fields, so they are one
    parametrized case rather than two tests that exercise one code path (review finding).
    """
    doc = _manifest()
    mutate(doc)
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "a hand-edited count did not red the lint"
    assert "counts" in out and "999" in out, out


@pytest.mark.parametrize(
    "row_name, field, value",
    [
        pytest.param("tool", "job", "a description no derivation produces", id="rendered-cell"),
        pytest.param("sdk", "class", "definitely-not-derived", id="caller-scan-column"),
    ],
)
def test_the_check_reds_on_a_hand_edited_derived_cell(checker, tmp_path, row_name, field, value):
    """MEMBERSHIP is not CONTENT, for both row kinds.

    Every name-only check passes while the description that renders into the document the
    owner reviews — `job`, the cell the table is built from — says something else, and `class`
    is derived by an AST caller scan that NO other gate reads (measured: `grep class
    tools/surface-guard.py` finds only prose). A guard that only proves a row EXISTS does not
    protect what the row says. (Two tests that each mutated one key and asserted the same two
    substrings were one code path with two constants; parametrized — review finding.)
    """
    doc = _manifest()
    prefix = "sdk:" if row_name == "sdk" else ""
    row = next(r for r in doc["rows"] if str(r["name"]).startswith(prefix))
    row[field] = value
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, f"a hand-edited `{field}` did not red the lint"
    assert field in out and row["name"] in out, out


def test_the_check_reds_on_an_unclassified_new_column(checker, tmp_path):
    """A key the generator grows later is compared by DEFAULT, so it cannot escape.

    The exclusion is a deny-list applied to both sides: a column that is present on one
    side only reds until it is deliberately classified as non-derivable.
    """
    doc = _manifest()
    doc["rows"][0]["sneaky_new_column"] = "x"
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "an unclassified column was accepted"
    assert "sneaky_new_column" in out, out


def test_the_declared_non_derivable_keys_all_exist_on_a_derived_row(derived_baseline):
    """The exclusion set cannot rot into a list of keys nobody emits.

    If a key is renamed, its exclusion must be renamed with it — otherwise the exclusion
    silently stops excluding anything and the comparison reds (loudly, which is the point)
    or, worse, the key it was written for is no longer covered by the reason recorded for it.
    """
    sm = _load_manifest_tool()
    row_keys = {k for r in derived_baseline["rows"] for k in r}
    assert row_keys >= sm.NON_DERIVABLE_ROW_KEYS, (
        "these exclusions name keys no derived row carries: "
        f"{sorted(sm.NON_DERIVABLE_ROW_KEYS - row_keys)}"
    )
    assert set(derived_baseline) > sm.NON_DERIVABLE_DOC_KEYS, (
        "these doc-level exclusions name keys the derivation does not emit: "
        f"{sorted(sm.NON_DERIVABLE_DOC_KEYS - set(derived_baseline))}"
    )


def test_the_check_refuses_when_the_baseline_is_missing(tmp_path, monkeypatch, capsys):
    """A check that cannot read its evidence must REFUSE, not traceback and not pass."""
    sm = _load_manifest_tool()
    monkeypatch.setattr(sm, "MANIFEST_FILE", tmp_path / "does-not-exist.yml")
    assert sm.cmd_check(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "missing" in out, out


def test_the_check_refuses_when_the_ordering_table_is_missing(tmp_path, monkeypatch, capsys):
    sm = _load_manifest_tool()
    monkeypatch.setattr(sm, "ORDER_FILE", tmp_path / "no-order.yml")
    assert sm.cmd_check(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "ordering table" in out, out


def test_the_derivation_resets_approvals_BY_DESIGN(derived_baseline):
    """The reset is the CONTROL, not an oversight — CONTRIBUTING.md makes it so.

    "To propose an addition" steps 2-4: `cut` folds the change in and marks the baseline
    `pending-owner-approval`, and the owner then records `approval` per row. The reset is
    what forces re-approval of the whole baseline, so a changed `served_from` cannot ride
    an old approval into `approved`.

    A carry-over of the previous approvals across a re-cut (drafted because a naive cut
    zeroes six recorded approvals and their rationale comments, which reads like data
    loss) was REFUSED against that documented decision rather than adopted. This test
    exists so the behaviour is not "fixed" later by someone reading only the code.

    Its SCOPE is the DERIVATION. `build_doc` cannot read the artifact, so a carry-over
    reintroduced at the WRITE site — where it would actually live — leaves these
    assertions green; that path is pinned separately, against the file `cut` leaves
    behind, by `test_cut_resets_approvals_at_the_WRITE_SITE` (review finding).
    """
    assert derived_baseline["approval_status"] == "pending-owner-approval"
    assert derived_baseline["approval_principal"] is None
    assert derived_baseline["approval_pr"] is None
    reset = [
        r for r in [*derived_baseline["rows"], *derived_baseline["retired"]]
        if r.get("approval") is not None
    ]
    assert reset == [], f"a re-cut carried approvals forward: {reset[:5]}"


def test_each_declared_non_derivable_row_key_is_actually_excluded(derived_baseline):
    """Every key the comparison excludes must be excluded for a REASON that is tested.

    The exclusion set is the only place the drift gate can be weakened without touching
    the comparison itself: drop one key and a hand-edit to that key stops reding. So the
    expected set is a LITERAL here — iterating `NON_DERIVABLE_ROW_KEYS` would let a dropped
    key delete its own coverage, which is exactly the survivor this test first recorded
    (mutation M3: `reason` dropped from the set, suite still green).
    """
    sm = _load_manifest_tool()
    assert {
        "used_by",
        "recommendation",
        "basis",
        "reason",
        "approval",
        "exemption",
    } == sm.NON_DERIVABLE_ROW_KEYS, "the exclusion set changed — every key must be justified where it is declared"
    row = copy.deepcopy(derived_baseline["rows"][0])
    for key in sorted(sm.NON_DERIVABLE_ROW_KEYS):
        mutated = copy.deepcopy(row)
        mutated[key] = "a value no derivation produces"
        assert sm._derivation_problems(
            {"rows": [mutated], "retired": []}, {"rows": [row], "retired": []}
        ) == [], f"`{key}` is declared non-derivable but a change to it reds the check"


def test_each_declared_non_derivable_doc_key_is_actually_excluded(derived_baseline):
    sm = _load_manifest_tool()
    assert {
        "cut_at_commit",
        "approval_status",
        "approval_principal",
        "approval_pr",
        "response_fields",
    } == sm.NON_DERIVABLE_DOC_KEYS
    for key in sorted(sm.NON_DERIVABLE_DOC_KEYS):
        mutated = dict(derived_baseline)
        mutated[key] = "a value no derivation produces"
        assert sm._derivation_problems(mutated, derived_baseline) == [], (
            f"`{key}` is declared non-derivable but a change to it reds the check"
        )


def test_a_nonderivable_key_is_still_seen_when_it_is_the_ONLY_difference(derived_baseline):
    """The exclusion must not swallow the keys around it.

    `used_by` is excluded; a change to the row's NAME is not. If the comparison stripped
    whole rows instead of named keys, this would pass silently.
    """
    sm = _load_manifest_tool()
    row = copy.deepcopy(derived_baseline["rows"][0])
    renamed = copy.deepcopy(row)
    renamed["name"] = "tortoise_not_a_real_tool"
    problems = sm._derivation_problems(
        {"rows": [renamed], "retired": []}, {"rows": [row], "retired": []}
    )
    assert problems, "a renamed row slipped through the projection"


def test_the_guard_reds_on_a_duplicate_registry_entry():
    """P3 from review: a duplicate NAME was invisible to a set-based comparison.

    `declared_tools` is a set, so appending a second ToolDefinition reusing an approved
    name left the guard green while the registry grew. The cardinality check closes it.
    Every other test mutates the MANIFEST; this one must mutate the live registry, which
    is why it runs the guard in-process rather than via subprocess.
    """
    from tortoise.tool_registry import TOOL_REGISTRY

    guard = _load_guard()
    original = list(TOOL_REGISTRY)
    try:
        # ToolDefinition is a frozen dataclass, so a duplicate NAME is produced by
        # appending the entry again — exactly the shape (a name-set comparison sees
        # nothing) the cardinality check exists to catch.
        TOOL_REGISTRY.append(TOOL_REGISTRY[0])
        rc = guard.main([])
        assert rc == 1, "a duplicate registry entry did not red the gate"
    finally:
        TOOL_REGISTRY[:] = original
    assert len(TOOL_REGISTRY) == _baseline_tool_count(), "the registry was not restored"


def test_the_guard_reds_on_a_tool_served_outside_the_registry():
    """The hole that made the gate's own claim false.

    D2 says "the MCP tool surface cannot be expanded without explicit human approval",
    but the guard compared only TOOL_REGISTRY. A tool registered straight on the FastMCP
    instance — a `@mcp.tool()` decorator in mcp_server.py, or any `mcp.add_tool(...)` —
    is served to agents while the registry is untouched. Every other assertion only ever
    checked `registry subset-of registered`, never the reverse, so the expanded surface
    reached a green gate. The guard now enumerates the SERVED set.
    """
    from tortoise import mcp_server

    guard = _load_guard()

    async def tortoise_tool_served_outside_the_registry() -> str:
        return "hi"

    mcp_server.mcp.add_tool(tortoise_tool_served_outside_the_registry)
    try:
        assert guard.main([]) == 1, "a tool served outside TOOL_REGISTRY did not red the gate"
    finally:
        components = mcp_server.mcp._local_provider._components
        for key in [
            k for k in components if k.startswith("tool:tortoise_tool_served_outside_the_registry@")
        ]:
            components.pop(key)
        # If the provider layout changes, fail loudly rather than leaking a served tool
        # into the rest of the suite.
        assert guard.main([]) == 0, "the served-surface check did not recover after cleanup"


def test_the_guard_reds_on_a_tool_injected_by_a_server_transform():
    """The bypass that a registry comparison and `add_tool` both miss.

    A server-level transform (`mcp.add_transform`) can append a tool in `list_tools` and
    route it in `get_tool`, so it is advertised to a real MCP client and callable over the
    protocol while `TOOL_REGISTRY` is untouched. `mcp_server.py` already uses this API for
    `_HTTPToolFilter`, so it is a live pattern here, not a hypothetical.

    Reading `mcp._list_tools()` misses it — that is the pre-transform aggregate. Only
    `mcp.list_tools()` (the protocol `tools/list` path) sees what agents actually receive:
    verified 99 vs 100 against a live `fastmcp.Client`.
    """
    from fastmcp.tools import Tool

    from tortoise import mcp_server

    guard = _load_guard()

    async def tortoise_tool_injected_by_transform() -> str:
        return "hi"

    class _Inject:
        async def list_tools(self, tools):
            tools.append(Tool.from_function(tortoise_tool_injected_by_transform))
            return tools

        async def get_tool(self, name, call_next):
            return await call_next(name)

    transform = _Inject()
    mcp_server.mcp.add_transform(transform)
    try:
        assert guard.main([]) == 1, "a transform-injected tool did not red the gate"
    finally:
        mcp_server.mcp._transforms.remove(transform)
        assert guard.main([]) == 0, "the served-surface check did not recover after cleanup"


def test_the_guard_reds_on_a_transform_that_only_routes_get_tool():
    """A transform that intercepts `get_tool` without listing anything.

    `tools/call` resolves through `get_tool`, so such a transform makes a tool callable
    over the protocol while it appears in NEITHER `_list_tools()` nor `list_tools()` —
    the union alone could not see it (verified: a live `fastmcp.Client` called it while the
    gate exited 0). Intercepting resolution requires a registered transform, so the guard
    gates the transform SET.
    """

    class _Phantom:
        async def list_tools(self, tools):
            return tools  # lists nothing extra — that is what makes this one invisible

        async def get_tool(self, name, call_next):
            return await call_next(name)

    from tortoise import mcp_server

    guard = _load_guard()
    phantom = _Phantom()
    mcp_server.mcp.add_transform(phantom)
    try:
        assert guard.main([]) == 1, "a get_tool-only transform did not red the gate"
    finally:
        mcp_server.mcp._transforms.remove(phantom)
        assert guard.main([]) == 0, "the transform check did not recover after cleanup"


def test_the_guard_reds_on_a_same_name_replacement_of_an_approved_tool():
    """A count-preserving substitution of an approved tool's implementation.

    `mcp.add_tool(fn)` where `fn` reuses an approved tool's name silently REPLACES it —
    fastmcp logs "Component already exists". The name set and the entry count are both
    unchanged, so every set- and count-based check stayed green while a live client
    invoked the shadow implementation. Comparing the identity of the component behind
    each name is what catches it.
    """
    from tortoise import mcp_server

    guard = _load_guard()
    components = mcp_server.mcp._local_provider._components
    key = next(k for k in components if k.startswith("tool:tortoise_search@"))
    original = components[key]
    original_count = len([k for k in components if k.startswith("tool:")])

    async def tortoise_search() -> str:
        """shadow implementation of an approved tool"""
        return "shadow"

    mcp_server.mcp.add_tool(tortoise_search)
    try:
        assert len([k for k in components if k.startswith("tool:")]) == original_count, (
            "this test is meaningless unless the count is preserved"
        )
        assert guard.main([]) == 1, "a same-name replacement did not red the gate"
    finally:
        components[key] = original
        assert guard.main([]) == 0, "the served-implementation check did not recover"


def test_code_digest_is_move_invariant_and_constant_sensitive():
    """The freeze-gate identity must ignore a pure line shift and differ when a
    constant differs. `sha256(co_code)` alone did neither once the line was
    dropped (98 tools collapsed to 62 digests)."""
    guard = _load_guard()

    def const_x() -> str:
        return "X"

    def const_y() -> str:
        return "Y"

    assert guard._code_digest(const_x.__code__) != guard._code_digest(const_y.__code__)

    src = "def f():\n    return 'X'\n"
    spaced = "\n\n\n\n\n" + src
    g1: dict = {}
    g2: dict = {}
    exec(compile(src, "<t>", "exec"), g1)
    exec(compile(spaced, "<t>", "exec"), g2)
    assert g1["f"].__code__.co_firstlineno != g2["f"].__code__.co_firstlineno
    assert guard._code_digest(g1["f"].__code__) == guard._code_digest(g2["f"].__code__)


def test_the_guard_reds_when_the_served_component_has_no_code_object():
    """A PRESENT-but-unfingerprintable component is malformed evidence, not
    absence: a substitution whose callable has no `__code__` must red, not be
    skipped as if the tool were simply not served."""
    import functools

    from tortoise import mcp_server

    guard = _load_guard()
    components = mcp_server.mcp._local_provider._components
    key = next(k for k in components if k.startswith("tool:tortoise_search@"))
    original = components[key]

    def shadow() -> str:
        """shadow implementation of an approved tool"""
        return "shadow"

    partial = functools.partial(shadow)
    partial.__name__ = "tortoise_search"
    mcp_server.mcp.add_tool(partial)
    try:
        assert guard.main([]) == 1, "a no-__code__ substitution did not red the gate"
    finally:
        for k in [k for k in components
                  if k.startswith("tool:tortoise_search@") and k != key]:
            components.pop(k, None)
        components[key] = original
        assert guard.main([]) == 0, "the served-implementation check did not recover"


def test_no_SDK_row_is_classed_unreachable_while_its_own_row_names_an_agent_path():
    """The unverified-negative class, pinned.

    `derive_class` once computed `agent-reachable` from declared `sdk_method` bindings
    alone. That classified CLI-reached methods (apikey_create, close, reconcile_sessions,
    session_index_health, volunteer_context) and handler-reached methods (recall_gaps,
    recall_subgraph, topic_summarize) as `no-caller-found` or `internal` — so the rendered
    doc asserted "reached by no agent path at all (no MCP tool, no CLI verb)" over rows
    whose own text named a CLI verb. A count derived by hand is a prediction until
    something executes it; this test executes the agreement.
    """
    from tortoise.sdk import TortoiseSDK

    doc = yaml.safe_load((ROOT / "config" / "surface-manifest.yml").read_text())
    sdk = [r for r in doc["rows"] if str(r["name"]).startswith("sdk:")]
    assert sdk, "no SDK rows found"

    for row in sdk:
        if row["class"] not in ("no-caller-found", "internal"):
            continue
        names_a_path = any(
            tok in (row.get("reason") or "") or tok in (row.get("dependency") or "")
            for tok in ("cli", "mcp-handler")
        )
        assert not names_a_path, (
            f"{row['name']} is classed {row['class']!r} but its own row names an agent path: "
            f"reason={row.get('reason')!r} dependency={row.get('dependency')!r}"
        )

    by_class = {}
    for row in sdk:
        by_class[row["class"]] = by_class.get(row["class"], 0) + 1
    declared_bindings = {
        r["sdk_method"]
        for r in doc["rows"]
        if not str(r["name"]).startswith("sdk:") and r.get("sdk_method")
    }
    assert by_class["agent-reachable"] >= len(
        {m for m in declared_bindings if hasattr(TortoiseSDK, m)}
    ), (
        "agent-reachable fell below the declared-binding count; the CLI and tool-handler legs are "
        f"not being derived (got {by_class['agent-reachable']})"
    )


def test_the_transform_check_reads_the_source_not_only_the_runtime_list():
    """The hole that made the transform gate inert in CI.

    The only `add_transform` in the tree sits INSIDE `create_http_app()`, which the guard
    never calls — so `mcp._transforms` is `[]` at guard time and a runtime-only check
    cannot fire in CI at all. The gate therefore also scans the SOURCE. Without that, a
    transform added to `mcp_server.py` (which can append to `list_tools` or route
    `get_tool` without listing anything) was invisible.
    """
    import tempfile


    guard = _load_guard()
    # The runtime list is lifecycle-dependent: `_HTTPToolFilter` is registered only when
    # the HTTP app is built (`create_http_app` never called at guard time in CI), so a
    # runtime-only check cannot see it there at all — the source scan is what CI relies
    # on. `_RetiredToolTransform` IS registered at import (#3883), which is why the
    # source scan must still list BOTH: the allowed set is the union the guard uses.
    assert guard._source_transforms() == {"_HTTPToolFilter", "_RetiredToolTransform"}, (
        "the source scan must see every transform the declaration registers"
    )

    sample = pathlib.Path(tempfile.mkdtemp()) / "sample.py"
    sample.write_text("def f():\n    mcp.add_transform(_Sneaky())\n")
    assert guard._source_transforms(sample) == {"_Sneaky"}, (
        "a transform added to the declaration's source must be visible without importing it"
    )


def test_the_guard_reds_when_an_approved_tool_stops_being_served():
    """The inverse of the union check: a DECLARED tool that is no longer offered.

    `undeclared_served` only asked whether anything undeclared is served. A tool silently
    ceasing to be offered is a removal — which the guard's own docstring promises to catch
    — but nothing computed it, so popping the component left the guard green.
    """
    from tortoise import mcp_server

    guard = _load_guard()
    components = mcp_server.mcp._local_provider._components
    key = next(k for k in components if k.startswith("tool:tortoise_search@"))
    saved = components.pop(key)
    try:
        assert guard.main([]) == 1, "a declared-but-unserved tool did not red the gate"
    finally:
        components[key] = saved
        assert guard.main([]) == 0, "the served-set check did not recover after cleanup"


def test_a_null_served_from_is_treated_as_malformed_not_as_nothing_to_check():
    """A null fingerprint must fail, not skip.

    Treating `served_from: null` as "nothing to check" re-opened the exact same-name
    substitution the fingerprint exists to close, reachable by editing only the baseline.
    """
    import copy
    import tempfile

    from tortoise import mcp_server

    doc = _manifest()
    mutated = copy.deepcopy(doc)
    for row in mutated["rows"]:
        if row.get("name") == "tortoise_search":
            row["served_from"] = None
    tmp = pathlib.Path(tempfile.mkdtemp()) / "m.yml"
    tmp.write_text(yaml.safe_dump(mutated, sort_keys=False, width=110))

    async def tortoise_search_shadow() -> str:
        """shadow"""
        return "shadow"

    mcp_server.mcp.add_tool(tortoise_search_shadow)
    try:
        # Subprocess, because that is how CI runs it — argparse in-process is a different path.
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "surface-guard.py"), "--manifest", str(tmp)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, "a null served_from let a same-name replacement through"
        assert "no `served_from` fingerprint" in result.stdout, (
            "the guard redded for some other reason than the missing fingerprint"
        )
    finally:
        components = mcp_server.mcp._local_provider._components
        for k in [k for k in components if k.startswith("tool:tortoise_search_shadow@")]:
            components.pop(k)


def test_the_usage_markers_are_not_inverted_and_every_row_is_well_formed_markdown():
    """Two defects the independent review found in the rendered artifact.

    1. The `in use` / `never called` markers were INVERTED: the 64 rows with zero observed
       calls were labelled "in use" and the 35 that actually appear in the call log were
       labelled "never called" — contradicting both the legend and each row's own `used_by`
       cell. The variable holding the observed names was misnamed `never_called`, which is
       how the inversion survived review.
    2. A literal `|` in a tool's description was escaped in `rationale` but not in the
       `does` cell, so six rows split into extra table cells and shifted every later column.
    """
    doc = _manifest()
    tools = [r for r in doc["rows"] if not str(r["name"]).startswith("sdk:")]
    text = RENDERED.read_text(encoding="utf-8")

    true_never = {r["name"] for r in tools if "never called" in (r.get("used_by") or "")}
    true_in_use = {r["name"] for r in tools} - true_never
    assert true_never and true_in_use, "the fixture is degenerate — no usage signal at all"

    # Scope to the MCP-tool tables. The RETIRED names render in their own 4-column
    # table below (#3883); it is not a tool row and must not be counted or checked
    # as one here.
    region = text.split("## The MCP tools", 1)[-1]
    region = region.split("## Retired names", 1)[0].split("## The SDK methods", 1)[0]
    rows = [ln for ln in region.split("\n") if ln.startswith("| `tortoise_")]
    assert len(rows) == len(tools), f"rendered {len(rows)} tool rows for {len(tools)} tools"

    malformed = [ln for ln in rows if ln.count("|") != 7]
    assert not malformed, (
        "these rows are not well-formed 6-column tables (a literal pipe in a cell splits "
        f"the row): {[m[:70] for m in malformed[:5]]}"
    )

    marked_never = {ln.split("`")[1] for ln in rows if "never called" in ln}
    marked_in_use = {ln.split("`")[1] for ln in rows if "in use" in ln}
    assert marked_never == true_never, (
        "the 'never called' marker does not match the manifest's usage evidence "
        f"(rendered-only={sorted(marked_never - true_never)[:5]}, "
        f"manifest-only={sorted(true_never - marked_never)[:5]})"
    )
    assert marked_in_use == true_in_use, (
        "the 'in use' marker does not match the manifest's usage evidence "
        f"(rendered-only={sorted(marked_in_use - true_in_use)[:5]})"
    )


# --- ROUND-2 REVIEW FINDINGS: fail-open content paths, closed with mutations ---------
# Four fresh-context reviewers ran on this change. Their P1s were both real: (1) a
# DUPLICATE row name silently dropped content from the drift comparison AND from the D2
# guard, reproducibly green with a doctored duplicate; (2) the approval-reset test could
# not observe the write site it claimed to pin. The P2s were malformed evidence escaping
# as tracebacks, an uncompared row order, and the retired-name contract no longer being
# isolated. Every test below is the mutation evidence for one of those fixes.


def test_cut_resets_approvals_at_the_WRITE_SITE(monkeypatch, tmp_path, derived_baseline):
    """The reset is the CONTROL (CONTRIBUTING.md, "To propose an addition" steps 2-4).

    `cut` folds a change in and marks the baseline `pending-owner-approval`; the owner then
    re-records `approval` per row. A carry-over of the previous approvals — drafted because
    a naive re-cut zeroes six recorded approvals and reads like data loss — was REFUSED
    against that documented decision, and the tool says so where it writes.

    Asserting this on `build_doc` alone (as the sibling test did) is VACUOUS for this
    contract: `build_doc` cannot read the artifact, so a carry-over reintroduced at the
    WRITE site — which is where it would live — left the suite green (review finding). So
    the previous artifact, carrying approvals, is placed AT THE PATH `cut` WRITES and the
    file it leaves behind is read back.

    The reset is now gated by `--allow-approval-reset` (#4598): it still happens, but only
    deliberately, and it names what it drops. The refusal half is
    `test_cut_REFUSES_to_blank_recorded_approvals` below.
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    prior = copy.deepcopy(_manifest())
    for row in [*prior["rows"], *prior["retired"]]:
        row["approval"] = "PR#1 @owner"
    prior["approval_status"] = "approved"
    path.write_text(yaml.safe_dump(prior, sort_keys=False, allow_unicode=True, width=110))

    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))
    # Since the blanked-approval guard (#4598), the reset the decision relies on is
    # performed only when it is asked for out loud. The reset is unchanged; the flag is
    # what keeps it from being SILENT (see the two guard tests below).
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef", allow_approval_reset=True)) == 0

    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["approval_status"] == "pending-owner-approval"
    carried = [
        r["name"]
        for r in [*written["rows"], *written["retired"]]
        if r.get("approval") is not None
    ]
    assert carried == [], (
        "the write site carried the previous approvals across the re-cut, reversing the "
        f"control CONTRIBUTING.md steps 2-4 rely on: {carried[:5]}"
    )


def _manifest_with_one_approval(tmp_path, name="tortoise_recall", value="#4173 @daniel-ospina"):
    """A baseline-shaped fixture whose ONLY recorded approval sits on `name`."""
    prior = copy.deepcopy(_manifest())
    for row in [*prior["rows"], *prior["retired"]]:
        row["approval"] = None
    next(r for r in prior["rows"] if r["name"] == name)["approval"] = value
    path = tmp_path / "surface-manifest.yml"
    path.write_text(yaml.safe_dump(prior, sort_keys=False, allow_unicode=True, width=110))
    return path, name, value


def test_cut_refuses_to_blank_a_recorded_approval(monkeypatch, tmp_path, capsys, derived_baseline):
    """A re-cut that would discard a recorded approval REFUSES, and names the row.

    `build_doc` cannot read the artifact, so every row it emits carries `approval: null`.
    The artifact is the only carrier of the owner's per-row approvals, which makes this
    write the one that silently destroys them — it did exactly that on this branch,
    blanking six approvals recorded on `origin/main` (#4598). Fail closed at the write
    site; the reset remains available, but only on request.
    """
    sm = _load_manifest_tool()
    path, name, value = _manifest_with_one_approval(tmp_path)
    before = path.read_text(encoding="utf-8")

    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))
    rc = sm.cmd_cut(argparse.Namespace(commit="deadbeef"))
    out = capsys.readouterr().out

    assert rc == 1, "a re-cut that blanks a recorded approval must refuse"
    assert "::error::" in out, out
    assert name in out and value in out, f"the refusal must name the row it would blank: {out}"
    assert "allow-approval-reset" in out, out
    assert path.read_text(encoding="utf-8") == before, "a refused cut wrote the baseline anyway"


def test_cut_allow_approval_reset_proceeds_and_PRINTS_the_loss(
    monkeypatch, tmp_path, capsys, derived_baseline
):
    """The reset stays the control — authorised, but never silent.

    CONTRIBUTING.md ("To propose an addition", steps 2-4) makes the reset what forces
    re-approval, so `--allow-approval-reset` keeps the behaviour and drops only the
    silence: the transcript must record what the reset cost.
    """
    sm = _load_manifest_tool()
    path, name, value = _manifest_with_one_approval(tmp_path)

    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))
    rc = sm.cmd_cut(argparse.Namespace(commit="deadbeef", allow_approval_reset=True))
    out = capsys.readouterr().out

    assert rc == 0, out
    assert "blanking 1 recorded owner approval(s)" in out, out
    assert name in out and value in out, f"the authorised reset must still say what it cost: {out}"
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["approval_status"] == "pending-owner-approval"
    assert [r for r in written["rows"] if r.get("approval")] == []


def test_cut_is_not_blocked_when_no_approval_is_recorded(monkeypatch, tmp_path, derived_baseline):
    """The guard gates the LOSS, not the tool.

    An approval-free baseline is the ordinary re-cut CONTRIBUTING.md steps 2-4 describe
    (and the first cut): it must still run without the flag. Without this test the guard
    could red the documented workflow while the refusal tests stayed green.
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    prior = copy.deepcopy(_manifest())
    for row in [*prior["rows"], *prior["retired"]]:
        row["approval"] = None
    path.write_text(yaml.safe_dump(prior, sort_keys=False, allow_unicode=True, width=110))

    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef")) == 0


def test_cut_still_cuts_when_the_baseline_does_not_exist(tmp_path, monkeypatch, derived_baseline):
    """The very first cut has no artifact to read — and no approval to lose."""
    sm = _load_manifest_tool()
    missing = tmp_path / "surface-manifest.yml"
    monkeypatch.setattr(sm, "MANIFEST_FILE", missing)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef")) == 0
    assert missing.exists(), "a cut with no prior artifact wrote nothing"


def test_cut_REFUSES_to_blank_recorded_approvals(monkeypatch, tmp_path, capsys, derived_baseline):
    """A re-cut must not be able to destroy an owner approval SILENTLY (#4598).

    On 2026-09-23 a re-cut landed on `main` through PR #4043 and carried six recorded owner
    approvals to zero. Nothing went red, because `approval` is NON_DERIVABLE: no derived
    property compares it, so the loss is invisible to every check that exists. The reset
    itself is still the documented control — what this pins is that it cannot happen
    without the operator saying so and seeing which rows they are dropping.
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    prior = copy.deepcopy(_manifest())
    for row in prior["rows"]:
        row["approval"] = None
    approved_row = prior["rows"][0]["name"]
    prior["rows"][0]["approval"] = "#4173 @daniel-ospina"
    prior["approval_status"] = "pending-owner-approval"
    path.write_text(yaml.safe_dump(prior, sort_keys=False, allow_unicode=True, width=110))
    before = path.read_text(encoding="utf-8")

    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))

    # No flag -> refuse, and the artifact is left EXACTLY as it was found.
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef")) == 1
    out = capsys.readouterr().out
    assert approved_row in out, f"the row it would drop is not named: {out}"
    assert "#4173 @daniel-ospina" in out, f"the approval value is not shown: {out}"
    assert path.read_text(encoding="utf-8") == before, "the refusal WROTE the manifest"

    # With the flag -> it proceeds, and STILL names every row it drops.
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef", allow_approval_reset=True)) == 0
    out = capsys.readouterr().out
    assert "DROPPED" in out and approved_row in out, out
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert all(r.get("approval") is None for r in written["rows"]), (
        "the flag was passed, so the reset is deliberate and expected — but a row kept an "
        "approval the re-cut should have cleared"
    )


def test_the_guard_REFUSES_an_UNREADABLE_baseline_rather_than_overwriting_it(
    monkeypatch, tmp_path, capsys, derived_baseline
):
    """An unreadable artifact is REFUSED — never silently overwritten.

    This flips the earlier reading. The artifact is the ONLY carrier of the owner's per-row
    approvals, and an unreadable one may well carry approvals that cannot be enumerated, so
    writing over it destroys them without ever naming them — the #4598 wipe again. "There is
    nothing to refuse for" was wrong: the refusal is precisely that the set is NOT enumerable.
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    broken = "this: [is not\n  a manifest\n"
    path.write_text(broken, encoding="utf-8")

    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef")) == 1
    assert path.read_text(encoding="utf-8") == broken, "the refusal WROTE the manifest"


def test_a_MISSING_baseline_still_cuts(monkeypatch, tmp_path, derived_baseline):
    """The first cut has no approvals to lose, and must keep working.

    The pair to the refusal above: the guard distinguishes MISSING (nothing to lose) from
    UNREADABLE (may carry something we cannot see). Conflating them would either block the
    first cut or reopen the silent overwrite.
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    assert not path.exists()

    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef")) == 0
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["counts"]["tools"] >= 1


def test_the_guard_sees_a_RETIRED_row_approval(monkeypatch, tmp_path, capsys, derived_baseline):
    """The mutation evidence for the RETIRED half of the artifact.

    `retired:` rows carry `approval` too — retiring a name shrinks the surface and needs the
    same human approval, and `build_doc` blanks it there as well. A guard that walks only
    `rows:` passes this case GREEN while a recorded retirement approval is destroyed, which
    is the same silent wipe through the other door. Assert on the retired row specifically:
    a guard reading only `rows:` fails here.
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    prior = copy.deepcopy(_manifest())
    for row in [*prior["rows"], *prior["retired"]]:
        row["approval"] = None
    assert prior["retired"], "fixture has no retired rows to test the retired arm with"
    retired_row = prior["retired"][0]["name"]
    prior["retired"][0]["approval"] = "#4598 @daniel-ospina"
    path.write_text(yaml.safe_dump(prior, sort_keys=False, allow_unicode=True, width=110))
    before = path.read_text(encoding="utf-8")

    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))

    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef")) == 1, (
        "a recorded approval on a RETIRED row did not stop the re-cut — the guard is "
        "reading `rows:` only"
    )
    out = capsys.readouterr().out
    assert retired_row in out and "#4598 @daniel-ospina" in out, out
    assert path.read_text(encoding="utf-8") == before, "the refusal WROTE the manifest"


def test_cut_refuses_when_the_declaration_cannot_be_IMPORTED(monkeypatch, capsys, tmp_path):
    """The REAL import branch — not a stand-in for it.

    The sibling test substitutes a raising `build_doc`, so the import inside it never runs.
    Here the declaration is unimportable in the interpreter, which is the failure the
    refusal contract was written for, and the artifact must still be left alone.
    """
    sm = _load_manifest_tool()
    target = tmp_path / "surface-manifest.yml"
    monkeypatch.setattr(sm, "MANIFEST_FILE", target)
    monkeypatch.setitem(sys.modules, "tortoise.tool_registry", None)
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef")) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "could not import the surface declaration" in out, out
    assert not target.exists(), "a refused cut wrote the baseline anyway"


@pytest.mark.parametrize(
    "document, needle",
    [
        pytest.param({"retired": []}, "rows", id="no-rows-list"),
        pytest.param({"rows": [], "retired": "tortoise_old"}, "retired", id="retired-not-a-list"),
    ],
)
def test_the_check_refuses_a_malformed_document_shape(
    tmp_path, monkeypatch, capsys, document, needle
):
    sm = _load_manifest_tool()
    path = tmp_path / "manifest.yml"
    path.write_text(yaml.safe_dump(document))
    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    assert sm.cmd_check(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and needle in out, out
    assert "Traceback" not in out


def test_the_check_refuses_an_order_table_missing_family_rank(tmp_path, monkeypatch, capsys):
    """`family_rank` is required by the DERIVATION, not only by this lint.

    Validating `keywords`/`tokens` alone let a table missing it pass the read and then
    escape as a bare KeyError out of `build_doc` — the traceback the refusal contract
    exists to prevent.
    """
    sm = _load_manifest_tool()
    table = dict(sm.load_order())
    table.pop("family_rank")
    path = tmp_path / "surface-order.yml"
    path.write_text(yaml.safe_dump(table))
    monkeypatch.setattr(sm, "ORDER_FILE", path)
    assert sm.cmd_check(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "family_rank" in out, out


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda r: r.pop("keyword"), id="missing-column"),
        pytest.param(lambda r: r.__setitem__("family_rank", "first"), id="non-integer-rank"),
        pytest.param(lambda r: r.__setitem__("cluster", ["a", "list"]), id="non-string-cluster"),
    ],
)
def test_the_check_reports_a_malformed_row_instead_of_tracing(checker, tmp_path, damage):
    """A row that HAS a name but lacks a column the properties index is malformed EVIDENCE.

    It escaped as a bare KeyError/TypeError (`r["keyword"]`, then the family_rank sort)
    instead of the `::error::` refusal the contract promises.
    """
    doc = _manifest()
    row = next(r for r in doc["rows"] if not str(r["name"]).startswith("sdk:"))
    damage(row)
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "a malformed row did not red the check"
    assert "malformed row" in out, out
    assert "Traceback" not in out


def test_the_check_refuses_a_duplicate_row_name(checker, tmp_path):
    """ADVERSARIAL P1: a duplicated `sdk:` row made the artifact's CONTENT unverified.

    `_derivation_problems` keys rows by name, so a second row reusing an existing name is
    silently DROPPED. A doctored duplicate inserted BEFORE the true row — carrying a
    fabricated `class` and `job` — left both the drift check and the D2 guard GREEN
    (reproduced through the real CLI). No structural property sees `sdk:` rows at all, so
    nothing else noticed. Content that a name-keyed comparison cannot show must be refused.
    """
    doc = _manifest()
    sdk = next(r for r in doc["rows"] if str(r["name"]).startswith("sdk:"))
    doctored = dict(sdk, **{"class": "internal", "job": "fabricated by a duplicate row"})
    doc["rows"].insert(0, doctored)
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "a duplicate row name did not red the check"
    assert "duplicate" in out, out


def test_the_guard_refuses_a_duplicate_row_name(tmp_path):
    """The D2 expansion gate keys `baseline_sdk` / `baseline_retired_map` by name too."""
    doc = _manifest()
    sdk = next(r for r in doc["rows"] if str(r["name"]).startswith("sdk:"))
    doc["rows"].insert(0, dict(sdk, **{"class": "internal"}))
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "a duplicate row name did not red the guard"
    assert "duplicate" in result.stdout, result.stdout


def test_the_guard_refuses_a_duplicate_retired_name(tmp_path):
    doc = _manifest()
    doc["retired"].insert(0, dict(doc["retired"][0], **{"use_instead": "fabricated"}))
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "a duplicate retired name did not red the guard"
    assert "duplicate" in result.stdout, result.stdout


def test_the_check_reds_on_reordered_rows(checker, tmp_path):
    """ORDER is content.

    The nine structural properties pin only the NON-`sdk:` subsequence of `rows`, so moving
    every `sdk:` row to the FRONT of the frozen artifact passed `check` reporting all ten
    properties holding (adversarial finding). The derived order is compared like any other
    derived value.
    """
    doc = _manifest()
    sdk = [r for r in doc["rows"] if str(r["name"]).startswith("sdk:")]
    rest = [r for r in doc["rows"] if not str(r["name"]).startswith("sdk:")]
    assert sdk and rest, "the baseline must carry both kinds of row for this to mean anything"
    doc["rows"] = [*sdk, *rest]
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "reordering the artifact did not red the check"
    assert "not in the derived order" in out, out


# --- CYCLE 2: the gate's own fail-opens, found adversarially --------------------------
# A second review cycle attacked the D2 gate itself rather than the drift check, and found
# the strongest defect of the whole change: `surface-guard.py` keys SDK rows by `method`
# into a set, so a fabricated row for a brand-new public SDK method — new name, an
# existing method, `exemption: true` — passed the gate (verified: the same method WITHOUT
# the row was correctly refused; WITH it, exit 0). Renaming a real SDK row also passed.
# Every test below pins one close, and each has a mutation in the battery.


def test_the_guard_refuses_an_sdk_row_whose_name_is_not_its_method(tmp_path):
    """The SDK-row fail-open: a set of `method`s cannot stand in for the row SET.

    `baseline_sdk = {r["method"] for sdk rows}` means a row that renames the name while
    keeping a real method contributes nothing to the comparison. Measured before the fix:
    a fabricated `name: sdk:totally_new_and_unapproved` carrying an existing `method` and
    `exemption: true` → `OK … 151 public SDK methods (1 exempt)`, exit 0. The derivation
    emits `sdk:<method>` for every one of the 150 rows, so that identity is the check.
    """
    doc = _manifest()
    real = next(r for r in doc["rows"] if str(r["name"]).startswith("sdk:"))
    doc["rows"].append(dict(real, name="sdk:totally_new_and_unapproved", exemption=True))
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "a fabricated SDK row passed the expansion gate"
    assert "do not identify their method" in result.stdout, result.stdout


def test_the_guard_refuses_a_renamed_sdk_row(tmp_path):
    """The weaker form of the same fail-open: renaming a real row, method untouched."""
    doc = _manifest()
    for row in doc["rows"]:
        if str(row["name"]).startswith("sdk:"):
            row["name"] = row["name"] + "_ghost"
            break
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "a renamed SDK row passed the expansion gate"
    assert "do not identify their method" in result.stdout, result.stdout


@pytest.mark.parametrize(
    "bad_name",
    [
        pytest.param(None, id="null"),
        pytest.param(1, id="int"),
        pytest.param(["a", "b"], id="list-unhashable"),
    ],
)
def test_the_guard_refuses_a_malformed_row_name_instead_of_tracing(tmp_path, bad_name):
    """A non-string name crashed the gate OUTSIDE its own fail-closed contract.

    Measured before the fix: `name: null` → `AttributeError: 'NoneType' object has no
    attribute 'startswith'`; `name: [a, b]` → `TypeError: unhashable type: 'list'` from
    `{r["name"] for r in rows}`. A traceback exits 1, but it is not the refusal this gate
    promises, and `git grep` shows no other check reads those rows.
    """
    doc = _manifest()
    doc["rows"][0]["name"] = bad_name
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "a malformed row name did not red the gate"
    assert "malformed row name" in result.stdout, result.stdout
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "bad_served",
    [pytest.param(None, id="null"), pytest.param("", id="empty"), pytest.param("grpc", id="unknown")],
)
def test_the_guard_refuses_a_served_value_it_cannot_classify(tmp_path, bad_served):
    """A FALSY `served` turned a check OFF and the gate still printed OK.

    `if name.startswith("sdk:") or not row.get("served"): continue` skipped the
    served-surface comparison for `served: null` / `""` — the same defect class this file
    already fixed for a broken import — and printed `OK`. Only `http` / `stdio-only` occur
    on tool rows and only `sdk` on SDK rows (measured over all 232 rows).
    """
    doc = _manifest()
    row = next(r for r in doc["rows"] if not str(r["name"]).startswith("sdk:"))
    row["served"] = bad_served
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, f"`served: {bad_served!r}` skipped the check"
    assert "cannot classify" in result.stdout or "must be `served: sdk`" in result.stdout, result.stdout


def test_the_guard_refuses_an_sdk_row_not_served_as_sdk(tmp_path):
    doc = _manifest()
    row = next(r for r in doc["rows"] if str(r["name"]).startswith("sdk:"))
    row["served"] = "http"
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "an SDK row claiming to be an HTTP tool was accepted"
    assert "must be `served: sdk`" in result.stdout, result.stdout


def test_the_exemption_control_is_usable_without_reding_the_drift_check(checker, tmp_path):
    """`exemption` is a HUMAN RECORDING — excluding it is what keeps the control reachable.

    `build_doc` emits `exemption: False` for every row, so while `exemption` was compared as
    a derived value, recording one (the path `tools/surface-guard.py` reads and CONTRIBUTING
    documents) made the REQUIRED check red with no legitimate way back — `cut` resets it to
    False forever. Measured before the fix: guard `OK … (1 exempt)`, check
    `FAIL row 'sdk:annotate_ask_hits' was hand-edited — exemption: recorded {True}, derived {False}`.
    """
    doc = _manifest()
    row = next(r for r in doc["rows"] if str(r["name"]).startswith("sdk:"))
    row["exemption"] = True
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 0, f"a recorded exemption is refused: {result.stdout}"
    assert "1 exempt" in result.stdout, result.stdout
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 0, f"a recorded exemption red the drift check: {out}"


def test_the_check_reds_on_reordered_retired_rows(checker, tmp_path):
    """The ROW order was pinned; the `retired` order was not (surviving mutation).

    Reversing `retired` in the baseline left `check` GREEN while the same edit to `rows`
    reds — the comparison is one line with two call sites, and only one was covered.
    """
    doc = _manifest()
    doc["retired"] = list(reversed(doc["retired"]))
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "reversing the retired block did not red the check"
    assert "retired" in out and "not in the derived order" in out, out


def test_the_check_refuses_a_duplicate_RETIRED_name(checker, tmp_path):
    """The reader's duplicate refusal covers both lists; only `rows` was asserted."""
    doc = _manifest()
    doc["retired"].insert(0, dict(doc["retired"][0], use_instead="fabricated"))
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "a duplicate retired name did not red the check"
    assert "duplicate" in out and "retired" in out, out


def test_the_check_refuses_a_baseline_with_no_cut_at_commit(checker, tmp_path):
    """`cut_at_commit` is excluded from the drift comparison, so this is all that pins it.

    It moved into `_read_manifest` because `cmd_render` slices it: a hand-edited
    `cut_at_commit: null` was a `TypeError` in the renderer and a mere problem in the check.
    """
    doc = _manifest()
    doc["cut_at_commit"] = None
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "a baseline with no cut_at_commit did not red the check"
    assert "::error::" in out and "cut_at_commit" in out, out


def test_the_render_refuses_instead_of_tracing(tmp_path, monkeypatch, capsys):
    """`cmd_render` indexed `family_rank` / `cut_at_commit` with no guard at all."""
    sm = _load_manifest_tool()
    for label, damage in (
        ("missing-family_rank", lambda d: next(r for r in d["rows"] if not str(r["name"]).startswith("sdk:")).pop("family_rank")),
        ("null-cut_at_commit", lambda d: d.__setitem__("cut_at_commit", None)),
    ):
        doc = _manifest()
        damage(doc)
        path = tmp_path / f"{label}.yml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
        monkeypatch.setattr(sm, "MANIFEST_FILE", path)
        assert sm.cmd_render(argparse.Namespace()) == 1, label
        out = capsys.readouterr().out
        assert "::error::" in out, (label, out)
        assert "Traceback" not in out, (label, out)


@pytest.mark.parametrize(
    "damage, needle",
    [
        pytest.param(lambda t: t.__setitem__("tokens", []), "tokens", id="tokens-empty-list"),
        pytest.param(lambda t: t.__setitem__("tokens", {"fetch": "fetch"}), "LIST", id="token-scalar"),
        pytest.param(lambda t: t.__setitem__("keywords", {}), "keywords", id="keywords-empty"),
        pytest.param(lambda t: t.__setitem__("keywords", {"fetch": "1"}), "INTEGER", id="str-rank"),
        pytest.param(lambda t: t.__setitem__("family_rank", {}), "family_rank", id="family-rank-empty"),
        pytest.param(lambda t: t.__setitem__("baseline_counts", []), "baseline_counts", id="counts-deleted"),
        pytest.param(lambda t: t.__setitem__("baseline_counts", {"fetch": 14}), "exactly one entry", id="counts-partial"),
        pytest.param(lambda t: t.pop("baseline_counts"), "baseline_counts", id="counts-absent"),
        pytest.param(lambda t: t.__setitem__("tokens", {**t["tokens"], "nosuchkeyword": ["x"]}), "unknown", id="unknown-keyword"),
        pytest.param(lambda t: t["keywords"].__setitem__("fetch", 2), "1..", id="rank-collision"),
    ],
)
def test_load_order_refuses_a_malformed_table(tmp_path, monkeypatch, damage, needle):
    """KEY PRESENCE IS NOT SHAPE — each of these passed the old validation and then broke.

    Measured against /tmp copies before the fix: `tokens: []` → `AttributeError: 'list'
    object has no attribute 'items'`; `tokens: {fetch: fetch}` silently iterated the STRING's
    characters so every real token fell through to `operate`; `keywords: {}` → `KeyError:
    'fetch'`; string ranks → `TypeError: '<' not supported` (and lexicographic order, where
    `'10' < '2'`); `family_rank: {}` → `KeyError` out of `build_doc`; an EMPTY
    `baseline_counts` short-circuited property 4's `if baseline and ...`, so deleting the
    frozen expectation turned the distribution check OFF with every gate green.
    """
    sm = _load_manifest_tool()
    table = copy.deepcopy(sm.load_order())
    damage(table)
    path = tmp_path / "surface-order.yml"
    path.write_text(yaml.safe_dump(table, sort_keys=False))
    monkeypatch.setattr(sm, "ORDER_FILE", path)
    with pytest.raises(sm.SurfaceEvidenceUnreadable) as excinfo:
        sm.load_order()
    assert needle in str(excinfo.value), str(excinfo.value)


def test_the_check_refuses_an_unreadable_evidence_file(tmp_path, monkeypatch, capsys):
    """The parse-failure branch: garbage bytes, not a shape the reader can classify."""
    sm = _load_manifest_tool()
    path = tmp_path / "manifest.yml"
    path.write_bytes(b"\x00\x01not: [valid: yaml")
    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    assert sm.cmd_check(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "could not read" in out, out


def test_the_derivation_refuses_when_a_tracked_file_cannot_be_read(monkeypatch):
    """A tracked-but-absent file escaped as `FileNotFoundError` out of the caller scan.

    `git ls-files` lists INDEX entries, so `rm foo.py` (without `git rm`) leaves it listed;
    `scan_callers` read it with only `except (SyntaxError, UnicodeDecodeError)`, so the
    REQUIRED gate traced back. A silently OMITTED file would be worse: a missed caller
    changes a row's derived class.
    """
    sm = _load_manifest_tool()
    monkeypatch.setattr(sm, "tracked_python", lambda: ["tortoise/definitely_absent_xyz.py"])
    with pytest.raises(sm.SurfaceEvidenceUnreadable) as excinfo:
        sm.build_doc()
    assert "could not read the tracked file" in str(excinfo.value), str(excinfo.value)


def test_the_check_reds_on_a_hand_added_null_doc_key(checker, tmp_path):
    """ABSENCE and `null` are different, and `doc.get(key) != derived.get(key)` conflated them.

    A hand-added top-level key whose value was `null` compared `None == None` and was
    accepted, contradicting `_derivation_problems`' contract that an unclassified key reds by
    DEFAULT. The sentinel makes absence itself a difference.
    """
    doc = _manifest()
    doc["sneaky_null_top_key"] = None
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "a hand-added null-valued top-level key passed the drift check"
    assert "sneaky_null_top_key" in out, out


# ── the `response_fields` record: shape, carry-forward, and the two defences ──
# The block records response FIELDS that are deliberately OUTSIDE the gate (the freeze is
# on tools and endpoints). It is hand-authored, so `cut` must carry it forward and
# `check` must defend it against both deletion and disconnection from the surface.


def _render_scratch() -> Path:
    """A repo-relative path `render` can write to during a test, then be removed."""
    return ROOT / "docs" / "product" / "_test-render-scratch.md"


def _manifest_at(sm, doc: dict, tmp_path, name: str = "manifest.yml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110))
    return path


def test_a_malformed_response_fields_entry_is_reported_and_does_not_crash_render(tmp_path, monkeypatch):
    """`check` must REPORT a malformed `response_fields` entry; `render` must not crash.

    Both halves were real defects when the block was introduced: a non-mapping entry
    crashed `render` with a bare AttributeError while `check` reported it cleanly, so the
    two disagreed about the same input.
    """
    sm = _load_manifest_tool()
    doc = _manifest()
    doc["response_fields"] = [{"bad_entry": True}, "not-a-mapping"]
    monkeypatch.setattr(sm, "MANIFEST_FILE", _manifest_at(sm, doc, tmp_path, "malformed.yml"))
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(doc))
    out = _render_scratch()
    monkeypatch.setattr(sm, "RENDERED_FILE", out)
    try:
        assert sm.cmd_check(argparse.Namespace()) != 0, "a malformed entry must be reported"
        assert sm.cmd_render(argparse.Namespace()) == 0, "render must not crash on a malformed entry"
    finally:
        out.unlink(missing_ok=True)


def test_a_response_field_value_cannot_break_the_generated_table(tmp_path, monkeypatch):
    """A `|` or a newline in a recorded field must not split the generated markdown row."""
    sm = _load_manifest_tool()
    doc = _manifest()
    doc["response_fields"] = [
        {
            "response": "tortoise_analyze",
            "field": "why",
            "emitted_when": "flag on | piped\nand multiline",
            "unchanged_when_off": "yes",
        }
    ]
    monkeypatch.setattr(sm, "MANIFEST_FILE", _manifest_at(sm, doc, tmp_path, "piped.yml"))
    out = _render_scratch()
    monkeypatch.setattr(sm, "RENDERED_FILE", out)
    try:
        assert sm.cmd_render(argparse.Namespace()) == 0
        # Match the FIELD cell, not just the response name: the tool list below also has a
        # row beginning `| `tortoise_analyze``, so the looser filter picked up both.
        rows = [
            ln
            for ln in out.read_text().splitlines()
            if ln.startswith("| `tortoise_analyze` | `why`")
        ]
        assert len(rows) == 1, f"the recorded row was split across lines: {rows}"
        assert rows[0].count("|") == 5, f"a pipe leaked into the row: {rows[0]}"
    finally:
        out.unlink(missing_ok=True)


def test_a_recut_carries_response_fields_forward(tmp_path, monkeypatch):
    """`cut` must not drop the hand-authored `response_fields` block.

    Those entries record response FIELDS — outside the gate, and not derivable from the
    declaration — so a re-cut that dropped them would silently empty the table the
    carve-out depends on, and the obligation to record a field would evaporate the first
    time anyone regenerated the manifest.
    """
    sm = _load_manifest_tool()
    doc = _manifest()
    doc["response_fields"] = [{"response": "tortoise_analyze", "field": "why", "emitted_when": "flag"}]
    monkeypatch.setattr(sm, "MANIFEST_FILE", _manifest_at(sm, doc, tmp_path))
    assert sm._carried_response_fields() == doc["response_fields"], (
        "a re-cut must carry `response_fields` forward, not drop the record"
    )


def test_a_non_list_response_fields_block_crashes_neither_command(tmp_path, monkeypatch):
    """A `response_fields:` value that is not a list must not crash `check` or `render`.

    `cut` only ever writes a list, so this shape is not reachable from the generator —
    but iterating a scalar raised `TypeError` in both commands, which is a crash path the
    fail-closed surface should not have for a block it merely ignores.
    """
    sm = _load_manifest_tool()
    out = _render_scratch()
    for block in (5, "ask", {"a": 1}, None, []):
        doc = _manifest()
        doc["response_fields"] = block
        monkeypatch.setattr(sm, "MANIFEST_FILE", _manifest_at(sm, doc, tmp_path, f"block-{type(block).__name__}.yml"))
        # `_doc=doc` BINDS the value. A bare closure over `doc` would read the
        # variable at CALL time, which is the next loop iteration's manifest —
        # ruff's B023, and a real aliasing hazard rather than a style nit.
        monkeypatch.setattr(sm, "build_doc", lambda *a, _doc=doc, **k: copy.deepcopy(_doc))
        monkeypatch.setattr(sm, "RENDERED_FILE", out)
        try:
            assert sm.cmd_render(argparse.Namespace()) == 0, f"render crashed on block={block!r}"
            assert sm.cmd_check(argparse.Namespace()) in (0, 1), f"check crashed on block={block!r}"
        finally:
            out.unlink(missing_ok=True)


def test_a_recut_carries_response_fields_forward_when_the_block_is_absent(tmp_path, monkeypatch):
    """The carry-forward helper must return `[]` — not raise — when there is no record."""
    sm = _load_manifest_tool()
    monkeypatch.setattr(sm, "MANIFEST_FILE", tmp_path / "does-not-exist.yml")
    assert sm._carried_response_fields() == []

    doc = _manifest()
    doc.pop("response_fields", None)
    monkeypatch.setattr(sm, "MANIFEST_FILE", _manifest_at(sm, doc, tmp_path, "no-block.yml"))
    assert sm._carried_response_fields() == []


def test_check_reds_when_the_recorded_fields_block_is_empty_or_missing(tmp_path, monkeypatch):
    """The record must not be able to VANISH.

    Before these properties the whole block could be deleted and `check` still said OK —
    so the doc's promise that an off-by-default addition "cannot quietly become the way the
    surface grows" was prose with nothing behind it.
    """
    sm = _load_manifest_tool()
    for block in (None, []):
        doc = _manifest()
        if block is None:
            doc.pop("response_fields", None)
        else:
            doc["response_fields"] = block
        monkeypatch.setattr(sm, "MANIFEST_FILE", _manifest_at(sm, doc, tmp_path, f"empty-{block is None}.yml"))
        monkeypatch.setattr(sm, "build_doc", lambda *a, _doc=doc, **k: copy.deepcopy(_doc))
        assert sm.cmd_check(argparse.Namespace()) == 1, f"check passed with response_fields={block!r}"


def test_check_reds_when_a_recorded_field_is_not_anchored_to_the_surface(tmp_path, monkeypatch):
    """Every entry must name a tool or endpoint that exists in the manifest."""
    sm = _load_manifest_tool()
    doc = _manifest()
    doc["response_fields"] = [{"response": "no_such_endpoint", "field": "x", "emitted_when": "never"}]
    monkeypatch.setattr(sm, "MANIFEST_FILE", _manifest_at(sm, doc, tmp_path, "unanchored.yml"))
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(doc))
    assert sm.cmd_check(argparse.Namespace()) == 1, "an unanchored entry must red check"


def test_check_accepts_a_recorded_field_anchored_to_a_real_endpoint(tmp_path, monkeypatch):
    """The anchor property must accept the real shape — `tortoise_analyze` is a live row."""
    sm = _load_manifest_tool()
    doc = _manifest()
    doc["response_fields"] = [
        {"response": "tortoise_analyze", "field": "why", "emitted_when": "truthy", "unchanged_when_off": "yes"}
    ]
    monkeypatch.setattr(sm, "MANIFEST_FILE", _manifest_at(sm, doc, tmp_path, "anchored.yml"))
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(doc))
    assert sm.cmd_check(argparse.Namespace()) == 0, "the live `tortoise_analyze` row anchors the entry"


def test_check_anchor_sees_an_sdk_endpoint_row_not_just_tool_rows(tmp_path, monkeypatch):
    """An endpoint response is anchored by its `sdk:<method>` row.

    `_partition_rows` — the row list the comparisons read — DROPS every `sdk:` row, so an
    anchor check written against it could never resolve an endpoint and would reject a
    legitimate `sdk:`-anchored entry. This pins the anchor set to the FULL `rows:` list.
    """
    sm = _load_manifest_tool()
    doc = _manifest()
    doc["response_fields"] = [
        {"response": "volunteer_context", "field": "x", "emitted_when": "always"}
    ]
    monkeypatch.setattr(sm, "MANIFEST_FILE", _manifest_at(sm, doc, tmp_path, "sdk-anchored.yml"))
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(doc))
    assert sm.cmd_check(argparse.Namespace()) == 0, "an `sdk:`-anchored entry must be accepted"


def test_a_non_mapping_document_fails_cleanly_everywhere(tmp_path, monkeypatch):
    """A top-level YAML list must not crash any command with a bare TypeError."""
    sm = _load_manifest_tool()
    manifest = tmp_path / "not-a-mapping.yml"
    manifest.write_text("- just\n- a\n- list\n")
    monkeypatch.setattr(sm, "MANIFEST_FILE", manifest)
    out = _render_scratch()
    monkeypatch.setattr(sm, "RENDERED_FILE", out)
    assert sm._carried_response_fields() == []
    assert sm.cmd_check(argparse.Namespace()) == 1, "check must fail cleanly, not raise"
    assert sm.cmd_render(argparse.Namespace()) == 1, "render must fail cleanly, not raise"


def test_unparseable_yaml_is_not_fatal_to_the_carry_forward(tmp_path, monkeypatch):
    """`_carried_response_fields` must return `[]` on a YAML error, not raise."""
    sm = _load_manifest_tool()
    manifest = tmp_path / "broken.yml"
    manifest.write_text("rows: [unclosed\n")
    monkeypatch.setattr(sm, "MANIFEST_FILE", manifest)
    assert sm._carried_response_fields() == []


def test_cmd_cut_is_actually_wired_to_carry_the_recorded_fields(tmp_path, monkeypatch):
    """`_carried_response_fields` must be CALLED by `cut`, not merely defined.

    The helper test alone does not pin the wiring: deleting the `response_fields:` line from
    `build_doc` left every other test passing, so a re-cut would silently empty the table —
    the exact way the recording obligation dies. Patch the helper with a sentinel and assert
    the sentinel reaches the document `cut` writes.
    """
    sm = _load_manifest_tool()
    sentinel = [{"response": "tortoise_analyze", "field": "why", "emitted_when": "truthy"}]
    monkeypatch.setattr(sm, "_carried_response_fields", lambda: sentinel)
    out = ROOT / "config" / "_scratch_recut_test.yml"
    monkeypatch.setattr(sm, "MANIFEST_FILE", out)
    monkeypatch.setattr(sm, "RENDERED_FILE", _render_scratch())
    try:
        assert sm.cmd_cut(argparse.Namespace(commit="test")) == 0
        written = yaml.safe_load(out.read_text())
    finally:
        out.unlink(missing_ok=True)
        _render_scratch().unlink(missing_ok=True)
    assert written.get("response_fields") == sentinel, (
        "cmd_cut did not carry the recorded fields into the manifest it wrote"
    )


def test_the_render_is_identical_with_no_machine_local_call_log(tmp_path, monkeypatch):
    """The drift step must be satisfiable OFF the machine that committed the file.

    The CI step is `python3 tools/surface_manifest.py render` followed by
    `git diff --exit-code -- docs/product/mcp-sdk-surface.md`, and it runs where
    `~/.tortoise/analytics_fallback.jsonl` does not exist. A render that reads that log
    emits DIFFERENT bytes there, so the step could only ever pass on one laptop: measured
    with the log absent, `_never` counted all 82 tools ("82 of 82" rather than "55 of 82")
    and every row lost its `in use` / `never called` flag — a 166-line diff against the
    committed document.

    The render reads COMMITTED inputs only. `used_by` is the checked-in cell the document
    already prints, so the flag is derived from it; the log keeps driving the artifact
    through `build_doc`, which refreshes the MANIFEST and lands as a reviewed diff.
    """
    sm = _load_manifest_tool()
    out = _render_scratch()
    monkeypatch.setattr(sm, "RENDERED_FILE", out)
    empty_home = tmp_path / "home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    try:
        assert sm.cmd_render(argparse.Namespace()) == 0
        assert out.read_text(encoding="utf-8") == RENDERED.read_text(encoding="utf-8"), (
            "the render produced different bytes with no machine-local call log — the CI "
            "drift step compares this output against the committed document, so it can "
            "never pass on a runner"
        )
    finally:
        out.unlink(missing_ok=True)

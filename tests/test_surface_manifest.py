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
        "the registry and the frozen baseline have diverged — a new tool needs an "
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


def test_approval_cannot_be_claimed_without_the_canonical_doc_recording_it(tmp_path):
    """A manifest may not claim `approved` while the canonical doc says pending (#4185).

    The approval is READ from `docs/product/canonical-mcp-tools.md`. A manifest that
    hand-sets `approved` against a doc that does not approve it is exactly the old
    hand-maintained field this fix removes — it must red.
    """
    doc = _manifest()
    doc["approval_status"] = "approved"
    doc["approval_principal"] = "daniel-ospina"
    doc["approval_pr"] = "4120"
    pending_doc = tmp_path / "canonical-mcp-tools.md"
    pending_doc.write_text(
        "---\napproval_status: pending-owner-approval\n---\n\n# fixture\n",
        encoding="utf-8",
    )
    result = _run(
        "tools/surface-guard.py",
        "--manifest", _write(doc, tmp_path),
        "--approval-doc", str(pending_doc),
    )
    assert result.returncode == 1, "`approved` against a pending canonical doc must red"
    # Assert the SPECIFIC branch, not a substring a sibling also emits (#4185 review P2):
    # `assert "approval_status" in stdout` also passes from the `not backed by` branch, and
    # `"in the manifest but"` is NOT safe either — the per-field comparison loop emits the SAME
    # phrase (`\`approval_principal\` is ... in the manifest but ...`), so it still passed with
    # the stale-copy comparison disabled by `if False:`. `"stale COPY"` is emitted by the
    # stale-copy branch ALONE, so this assertion isolates it.
    assert "stale COPY" in result.stdout, (
        "the stale-copy branch did not fire — the red came from elsewhere, so this test is "
        f"not exercising what it claims:\n{result.stdout}"
    )


def test_the_green_banner_reports_the_CLAIMED_approval_and_the_documents_separately():
    """A green run must report the manifest's approval AND the document it mirrors (#4185).

    The banner used to print `approval_status=<status> is READ from <doc>`, which was wrong
    twice over: the value printed came from the manifest, and a staged
    `pending-owner-approval` approves nothing. Now that the flip has landed (#8144) the
    committed manifest CLAIMS an approval, so a green run must say so and name the document
    the claim is checked against.
    """
    result = _run("tools/surface-guard.py")
    assert result.returncode == 0, result.stdout
    assert "The manifest CLAIMS an approval (approval_status=approved)" in result.stdout, (
        f"the banner did not report the manifest's claimed approval:\n{result.stdout}"
    )
    assert "which records 'approved'" in result.stdout, (
        "the banner did not report the DOCUMENT's recorded status separately — the manifest "
        f"is not the file that recorded it:\n{result.stdout}"
    )
    assert "it matches" in result.stdout, (
        f"the banner did not say the claim was compared against the document:\n{result.stdout}"
    )


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


def test_approved_status_requires_principal_and_pr_in_the_canonical_doc(tmp_path):
    """An `approved` claim needs a principal and a PR in the doc it is READ from (#4185).

    The old check demanded a per-row approval; the new one demands the canonical doc
    actually carry the two facts approval consists of. A doc that says `approved` but
    names no principal (or no PR) is not an approval record, and the gate must not
    launder it into one.
    """
    doc = _manifest()
    # Make the manifest a FAITHFUL MIRROR of the fixture doc, so the ONLY possible red is the
    # completeness branch. If the mirror is inexact the derived-field comparison below it fires
    # first, and this test would then pass even with the completeness check deleted — it would
    # assert a behaviour it does not exercise (found by review, 2026-10-10).
    doc["approval_status"] = "approved"
    doc["approval_principal"] = "daniel-ospina"   # matches approvedBy in the fixture
    doc["approval_pr"] = None                      # the fact under test: absent
    doc["approval_date"] = None                    # matches the fixture's absent `approved:`
    incomplete = tmp_path / "canonical-mcp-tools.md"
    incomplete.write_text(
        "---\napproval_status: approved\napprovedBy: daniel-ospina\n---\n\n# fixture\n",
        encoding="utf-8",
    )
    result = _run(
        "tools/surface-guard.py",
        "--manifest", _write(doc, tmp_path),
        "--approval-doc", str(incomplete),
    )
    assert result.returncode == 1, "an `approved` doc with no PR did not red the gate"
    # Assert the SPECIFIC branch, not a substring its siblings also emit: `assert "approval" in
    # stdout` passes on any of the three approval reds, which is how this went unnoticed.
    assert "not backed by" in result.stdout, (
        "the completeness branch did not fire — the red came from elsewhere, so this test is not\n"
        f"exercising what it claims:\n{result.stdout}"
    )


def test_approval_principal_cannot_disagree_with_the_canonical_doc(tmp_path):
    """No hand-set field may disagree with the document it is DERIVED from (#4185)."""
    doc = _manifest()
    doc["approval_principal"] = "a-hand-typed-imposter"
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "a hand-edited approval principal did not red the gate"
    assert "approval_principal" in result.stdout


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


def _fn_from_source(src: str, name: str = "f"):
    """Compile ONE function from source, so a test can vary a default or an annotation.

    `compile`-and-`exec` (not a literal `def` in the test body) because the variants differ
    only in their source text, which is what the surface's own declarations look like.
    """
    namespace: dict = {}
    exec(compile(src, "<surface-declaration>", "exec"), namespace)
    return namespace[name]


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
    """The baseline's `retired:` block is the frozen-baseline retirement list."""
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
        # #4192: the served DECLARATION cell (defaults / annotations). It is derived by
        # `_served_attributes`, so a hand-edited value is the same class of unrecorded
        # change as a hand-edited `job` — and the cell is what makes a changed default
        # visible at all.
        pytest.param("tool", "served_attributes", "0" * 16, id="served-declaration-cell"),
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


def _shadow_of(fn, **overrides):
    """A function object sharing `fn.__code__`, with tampered function ATTRIBUTES.

    `types.FunctionType` over the same code object is the tamper #4192 verified: the code
    object is byte-identical (`served_from` cannot see it), while the defaults/annotations
    the MCP inputSchema publishes are different.
    """
    import types

    shadow = types.FunctionType(
        fn.__code__, fn.__globals__, fn.__name__, fn.__defaults__, fn.__closure__
    )
    shadow.__annotations__ = dict(fn.__annotations__)
    shadow.__kwdefaults__ = dict(fn.__kwdefaults__ or {})
    for name, value in overrides.items():
        setattr(shadow, name, value)
    return shadow


def test_the_check_reds_when_a_served_default_changes_behind_an_unchanged_code_object(
    capsys,
):
    """#4192 end to end, on the REAL manifest and the REAL derivation.

    The `checker` fixture patches `build_doc` with a module-scoped cache, so this loads the
    tool fresh: the point is that the DERIVATION itself sees the changed default. `served`
    `_from` is byte-identical — the code object did not change — which is exactly why the
    pre-fix required job stayed green on a changed served behaviour (measured before the
    fix: the guard exited 0 on this same tamper).
    """
    sm = _load_manifest_tool()
    from tortoise import mcp_server

    components = mcp_server.mcp._local_provider._components
    key = next(k for k in components if k.startswith("tool:tortoise_search@"))
    original = components[key]
    fn = original.fn
    assert fn.__defaults__[3] == 10, "this test is pinned to tortoise_search's `limit` default"

    tampered = _shadow_of(fn, __defaults__=(*fn.__defaults__[:3], 999, *fn.__defaults__[4:]))
    assert sm._code_digest(tampered.__code__) == sm._code_digest(fn.__code__), (
        "the code object must be identical, or this is not the #4192 case"
    )

    mcp_server.mcp.add_tool(tampered)
    try:
        rc = sm.cmd_check(argparse.Namespace())
        out = capsys.readouterr().out
    finally:
        components[key] = original
    assert rc == 1, "a changed served default did not red the derivation check (#4192)"
    assert "served_attributes" in out and "tortoise_search" in out, out


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


def test_the_derivation_READS_the_document_approval_and_resets_ROW_approvals(derived_baseline):
    """Two different things live in this artifact, with different rules (#4185).

      * the per-row `approval` field is a recorded owner decision a re-cut cannot
        reproduce, so `build_doc` blanks it — the reset drops the AUDIT REFERENCE, and
        nothing requires re-recording it (CONTRIBUTING.md step 4); carrying it over was
        REFUSED;
      * the top-level `approval_status` / `approval_principal` / `approval_pr` /
        `approval_date` are READ from `docs/product/canonical-mcp-tools.md` by
        `read_approval`, so a re-cut carries the owner's REAL approval forward instead of
        re-asking for it — the five-times-re-asked defect.

    Its SCOPE is the DERIVATION. The write-site half is pinned by
    `test_cut_resets_approvals_at_the_WRITE_SITE` (review finding); the derivation of the
    approval from a FIXTURE doc is pinned by
    `test_the_manifest_approval_is_DERIVED_from_the_canonical_doc`.
    """
    assert derived_baseline["approval_status"] == "approved"
    assert derived_baseline["approval_principal"] == "daniel-ospina"
    assert derived_baseline["approval_pr"] == "4120"
    assert derived_baseline["approval_date"] == "2026-09-18"
    reset = [
        r for r in [*derived_baseline["rows"], *derived_baseline["retired"]]
        if r.get("approval") is not None
    ]
    assert reset == [], f"a re-cut carried ROW approvals forward: {reset[:5]}"


def test_the_manifest_approval_is_DERIVED_from_the_canonical_doc(tmp_path):
    """#4185: approving the canonical doc must be READ INTO the manifest by a `cut`, not typed.

    Point `read_approval` at a FIXTURE doc and prove the returned approval follows it.
    No `26`/`40` and no approval value is typed into the manifest by hand: `cut` copies the
    four values from the doc. `check` now compares them too (they are out of
    `NON_DERIVABLE_DOC_KEYS` since #8144), and the guard's own provenance comparison reds a
    manifest that CLAIMS an approval the doc does not record. This test pins the READING,
    not the `check`-side comparison.
    """
    sm = _load_manifest_tool()
    fixture = tmp_path / "canonical-mcp-tools.md"
    fixture.write_text(
        "---\n"
        "approval_status: approved\n"
        "approved: 2030-01-02\n"
        "approvedBy: someone-else\n"
        "approval_pr: 9999\n"
        "---\n\n# fixture\n",
        encoding="utf-8",
    )
    assert sm.read_approval(fixture) == {
        "status": "approved",
        "principal": "someone-else",
        "pr": "9999",
        "date": "2030-01-02",
    }


def test_an_unapproved_canonical_doc_yields_an_unapproved_manifest(tmp_path):
    """A re-cut cannot invent approval: with a pending doc, the derivation is pending.

    This is the test for requirement 3 — the approval fields are a function of the DOC,
    never of the code a re-cut reads, so regenerating the manifest cannot mark an
    unapproved surface approved.
    """
    sm = _load_manifest_tool()
    fixture = tmp_path / "canonical-mcp-tools.md"
    fixture.write_text(
        "---\napproval_status: pending-owner-approval\n---\n\n# fixture\n",
        encoding="utf-8",
    )
    approval = sm.read_approval(fixture)
    assert approval["status"] == "pending-owner-approval"
    assert approval["principal"] is None
    assert approval["pr"] is None
    assert approval["date"] is None


def test_build_doc_READS_the_approval_from_the_canonical_doc(monkeypatch, tmp_path):
    """The MANIFEST derivation — not just `read_approval` — follows the fixture doc.

    A `build_doc` that inlined the real doc's values would pass every other test in this
    module (they call `read_approval` directly, or assert the real doc's literals); this
    is the one that catches it. It also pins requirement 3: a pending doc yields a pending
    MANIFEST, so a re-cut cannot mark an unapproved surface approved.
    """
    sm = _load_manifest_tool()
    fixture = tmp_path / "canonical-mcp-tools.md"
    fixture.write_text(
        "---\napproval_status: pending-owner-approval\n---\n\n# fixture\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sm, "CANONICAL_DOC", fixture)
    derived = sm.build_doc()
    assert derived["approval_status"] == "pending-owner-approval"
    assert derived["approval_principal"] is None
    assert derived["approval_pr"] is None
    assert derived["approval_date"] is None


def test_a_doc_without_frontmatter_is_a_refusal_not_a_pending_approval(tmp_path):
    """Absent provenance must not degrade to `pending`.

    Returning the pending default for a document with no frontmatter would let a re-cut
    mark the baseline `pending` (which the guard PASSES) after the approval record was
    deleted — a green gate over vanished evidence. FAIL CLOSED instead.
    """
    sm = _load_manifest_tool()
    fixture = tmp_path / "canonical-mcp-tools.md"
    fixture.write_text("# a doc with no frontmatter at all\n", encoding="utf-8")
    with pytest.raises(sm.SurfaceEvidenceUnreadable):
        sm.read_approval(fixture)


def test_read_approval_refuses_an_approved_doc_without_a_principal_or_pr(tmp_path):
    """The GENERATOR applies the same completeness test as its two consumers.

    `surface-guard.py` and `tools/surface-target-check.py` both refuse `approved` with no
    principal/PR. If `read_approval` did not, `cut` could write a manifest that its own gate
    reds while `check` (derived vs committed) stayed green — one fact, three readers, two
    answers. The requirement is on `approved` only: `pending-owner-approval` legitimately
    carries neither.
    """
    sm = _load_manifest_tool()
    fixture = tmp_path / "canonical-mcp-tools.md"
    fixture.write_text(
        "---\napproval_status: approved\n---\n\n# fixture\n", encoding="utf-8"
    )
    with pytest.raises(sm.SurfaceEvidenceUnreadable):
        sm.read_approval(fixture)
    # …while the pending status without a principal/PR is still readable.
    fixture.write_text(
        "---\napproval_status: pending-owner-approval\n---\n\n# fixture\n",
        encoding="utf-8",
    )
    assert sm.read_approval(fixture)["status"] == "pending-owner-approval"
    # An EMPTY-STRING `approval_pr` is falsy to both consumers, so the generator must refuse
    # it too (testing `is None` here let `cut` write `approval_pr: ''` that the guard then reds).
    fixture.write_text(
        "---\napproval_status: approved\napprovedBy: someone\napproval_pr: ''\n---\n\n# fixture\n",
        encoding="utf-8",
    )
    with pytest.raises(sm.SurfaceEvidenceUnreadable):
        sm.read_approval(fixture)
    # An OUT-OF-VOCABULARY status must refuse (the guard reds it; the generator must too).
    fixture.write_text(
        "---\napproval_status: maybe\n---\n\n# fixture\n", encoding="utf-8"
    )
    with pytest.raises(sm.SurfaceEvidenceUnreadable):
        sm.read_approval(fixture)


def test_approval_status_is_the_TARGET_approval_not_a_baseline_stamp(
    monkeypatch, tmp_path, derived_baseline
):
    """The decision #4185 encodes, pinned: the field describes the TARGET, not this snapshot.

    A re-cut over a baseline that is OVER target still writes `approved`, because the owner
    approved the TARGET (26/40), not the frozen 82/150. This is deliberate, and what makes an
    expansion visible is NOT this field — it is (a) the guard reding BEFORE the re-cut when a
    tool is added, and (b) `tools/surface-target-check.py` reding on the gap. Pinned here so
    the semantics are a decision rather than an untested consequence.
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    prior = copy.deepcopy(_manifest())
    path.write_text(yaml.safe_dump(prior, sort_keys=False, allow_unicode=True, width=110))
    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    grown = copy.deepcopy(derived_baseline)
    grown["rows"].append({"name": "tortoise_brand_new", "approval": None})
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(grown))
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef", allow_approval_reset=True)) == 0
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["approval_status"] == "approved"  # the TARGET's approval, not the baseline's
    new_row = next(r for r in written["rows"] if r["name"] == "tortoise_brand_new")
    assert new_row["approval"] is None  # the per-row field stays an audit record, not the gate


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
    # The top-level `approval_*` keys are deliberately NOT here: they are DERIVED from
    # `docs/product/canonical-mcp-tools.md` (#4185; the flip landed in #8144), so `check`
    # compares them and a hand-set field cannot disagree with the owner's approval record.
    # `high_water_counts` is the hand-set growth pin (also not derived), and
    # `response_fields` is the carried-forward hand-authored block.
    assert {
        "cut_at_commit",
        "high_water_counts",
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


# The two `check`-based provenance tests that #8026 deferred to this PR (#4185, second PR).
# They live here now that the four approval keys are out of NON_DERIVABLE_DOC_KEYS, so `check`
# compares the committed copy against `read_approval`'s derivation. `tools/surface-guard.py`
# pins the same behaviour independently —
# `test_approval_cannot_be_claimed_without_the_canonical_doc_recording_it` and
# `test_approval_principal_cannot_disagree_with_the_canonical_doc` exercise the guard side.


def test_the_check_reds_when_the_manifest_approval_disagrees_with_the_doc(checker, tmp_path):
    """#4185: the approval is DERIVED, so `check` must catch a hand-edited copy.

    Removing the approval keys from `NON_DERIVABLE_DOC_KEYS` is what arms this: before,
    `check` could not see a doctored `approval_principal`, which is how the manifest said
    `pending` for an approved surface for a month.
    """
    doc = _manifest()
    doc["approval_principal"] = "a-hand-typed-imposter"
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "a hand-edited approval principal did not red the lint"
    assert "approval_principal" in out, out


def test_the_check_reds_when_the_manifest_claims_an_approval_the_doc_does_not(checker, tmp_path):
    """The `approval_status` copy is compared too — the field that caused the defect."""
    doc = _manifest()
    doc["approval_status"] = "pending-owner-approval"
    rc, out = _check(checker, doc, tmp_path)
    assert rc == 1, "a hand-edited approval status did not red the lint"
    assert "approval_status" in out, out


def test_the_guard_reds_when_the_live_surface_grows_past_the_high_water_pin(tmp_path):
    """#4185 review P1: a re-cut over a GROWN surface must not be green by construction.

    `counts:` is re-derived by every `cut`, so it cannot be its own growth control — a PR
    that adds a tool AND re-cuts moves the number every drift check compares against. The
    `high_water_counts` pin is carried forward instead, and this pins that the guard reds
    when the live surface EXCEEDS it. On the pre-fix guard there is no such field and no
    such check, so the run is green and this test fails — the check is non-vacuous.
    """
    doc = _manifest()
    doc["high_water_counts"] = {"tools": 81, "sdk_public_methods": 150}
    result = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert result.returncode == 1, "growth past the frozen pin did not red the gate"
    assert "high-water pin" in result.stdout, result.stdout


def test_the_guard_reds_when_the_high_water_pin_is_missing_or_malformed(tmp_path):
    """Fail closed: a baseline whose growth pin is gone has no growth control left."""
    doc = _manifest()
    doc.pop("high_water_counts")
    missing = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert missing.returncode == 1, "a missing growth pin did not red the gate"
    assert "high_water_counts" in missing.stdout, missing.stdout
    doc["high_water_counts"] = {"tools": "eighty-two", "sdk_public_methods": 150}
    malformed = _run("tools/surface-guard.py", "--manifest", _write(doc, tmp_path))
    assert malformed.returncode == 1, "a malformed growth pin did not red the gate"
    assert "high_water_counts" in malformed.stdout, malformed.stdout


def test_a_re_cut_CARRIES_the_high_water_pin_forward(monkeypatch, tmp_path):
    """The pin is NOT re-derived: `cut` copies it, so growth needs an explicit edit.

    If a re-cut recomputed it from the live counts, the pin would move with the growth and
    the guard's growth check would be defeated. The pin is set to a value DISTINCT from the
    live counts and the REAL `cmd_cut` runs: patching `build_doc` here would satisfy the
    assertion from a canned dict while `_carried_high_water_counts` never executed at all
    (#4185 review).
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    prior = copy.deepcopy(_manifest())
    prior["high_water_counts"] = {"tools": 7, "sdk_public_methods": 9}
    path.write_text(yaml.safe_dump(prior, sort_keys=False, allow_unicode=True, width=110))
    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef", allow_approval_reset=True)) == 0
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["high_water_counts"] == {"tools": 7, "sdk_public_methods": 9}, (
        "a re-cut recomputed the growth pin from the live counts — the growth is now "
        "invisible to the guard"
    )


def test_a_re_cut_REFUSES_a_manifest_that_lost_the_high_water_pin(monkeypatch, tmp_path, capsys):
    """Absent-from-an-existing-manifest is NOT a first cut (#4185 review).

    Returning the freshly derived counts there re-bases the ceiling silently — which is the
    failure the pin exists to prevent. The fallback belongs to a MISSING manifest.
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    prior = copy.deepcopy(_manifest())
    prior.pop("high_water_counts")
    path.write_text(yaml.safe_dump(prior, sort_keys=False, allow_unicode=True, width=110))
    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef", allow_approval_reset=True)) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "high_water_counts" in out, out
    # And the refusal wrote NOTHING: the pin-less manifest is exactly as it was.
    assert "high_water_counts" not in yaml.safe_load(path.read_text(encoding="utf-8"))


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


def test_served_attributes_sees_what_the_code_object_cannot():
    """#4192 — the two halves of a tool's recorded identity disagree about a default.

    Measured before the fix: a `types.FunctionType` over a served tool's `__code__` with a
    tampered `__defaults__` served different behaviour (`limit=10` -> `limit=999`) while the
    gate exited 0 — the code object is byte-identical, and a default is not in it.
    """
    guard = _load_guard()
    src = "def f(q, limit=10):\n    return q, limit\n"
    base = _fn_from_source(src)
    changed = _fn_from_source(src.replace("limit=10", "limit=99"))

    # the code object cannot tell them apart (this IS the defect) …
    assert guard._code_digest(base.__code__) == guard._code_digest(changed.__code__)
    # … and the served declaration can.
    assert guard._served_attributes(base) != guard._served_attributes(changed)
    # the same source compiled twice is the same declaration (no address, no dict order)
    assert guard._served_attributes(base) == guard._served_attributes(_fn_from_source(src))


def test_served_attributes_covers_every_caller_visible_part_outside_the_code_object():
    """Defaults, keyword-only defaults and annotations all move the cell — none of them is
    in the code object, and the MCP `tools/list` schema publishes all three."""
    guard = _load_guard()
    src = "def f(q: str, limit=10, *, mode='x') -> dict:\n    return {}\n"
    baseline = guard._served_attributes(_fn_from_source(src))
    baseline_code = guard._code_digest(_fn_from_source(src).__code__)

    for label, variant_src in (
        ("positional default", src.replace("limit=10", "limit=11")),
        ("keyword-only default", src.replace("mode='x'", "mode='y'")),
        ("parameter annotation", src.replace("q: str", "q: bytes")),
        ("return annotation", src.replace("-> dict", "-> list")),
    ):
        variant = _fn_from_source(variant_src)
        assert guard._code_digest(variant.__code__) == baseline_code, (
            f"the code object should NOT see a changed {label} — that is the whole point"
        )
        assert guard._served_attributes(variant) != baseline, f"a changed {label} was invisible"


def test_served_attributes_is_stable_for_a_code_bearing_default():
    """A lambda default must be RECURSED, never repr'd: its repr carries an address.

    Two live copies of the same source hold two distinct function objects, so a repr-based
    encoding would give them different fingerprints — and the gate would then red between
    two runs over an UNCHANGED tree.
    """
    guard = _load_guard()
    src = "def f(cb=lambda x: x, limit=3):\n    return cb, limit\n"
    first = _fn_from_source(src)
    second = _fn_from_source(src)
    assert first.__defaults__[0] is not second.__defaults__[0], (
        "the two lambdas must be distinct objects, or this proves nothing"
    )
    assert repr(first.__defaults__[0]) != repr(second.__defaults__[0]), (
        "a repr'd lambda default differs between two live copies — the reason it is recursed"
    )
    assert guard._served_attributes(first) == guard._served_attributes(second)
    # …and a different lambda BODY is still a different declaration.
    other = _fn_from_source(src.replace("lambda x: x", "lambda x: x + 1"))
    assert guard._served_attributes(other) != guard._served_attributes(first)


def test_the_guard_reds_on_a_tampered_default_behind_an_approved_tool():
    """#4192, the guard's own half: same `__code__`, different `__defaults__`.

    A live client gets the tampered default while the code object — and therefore
    `served_from` — is untouched, so the same-name-substitution check cannot see it.
    """
    from tortoise import mcp_server

    guard = _load_guard()
    components = mcp_server.mcp._local_provider._components
    key = next(k for k in components if k.startswith("tool:tortoise_search@"))
    original = components[key]
    fn = original.fn
    assert fn.__defaults__[3] == 10, "this test is pinned to tortoise_search's `limit` default"

    tampers = {
        # `limit` is the 4th positional default of `tortoise_search`.
        "default": {"__defaults__": (*fn.__defaults__[:3], 999, *fn.__defaults__[4:])},
        "annotation": {"__annotations__": {**fn.__annotations__, "query": "int"}},
    }
    for label, override in tampers.items():
        shadow = _shadow_of(fn, **override)
        assert guard._code_digest(shadow.__code__) == guard._code_digest(fn.__code__), label
        mcp_server.mcp.add_tool(shadow)
        try:
            assert guard.main([]) == 1, f"a tampered {label} did not red the gate (#4192)"
        finally:
            components[key] = original
            assert guard.main([]) == 0, f"the check did not recover after the {label} tamper"


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


def test_a_null_served_attributes_is_treated_as_malformed_not_as_nothing_to_check():
    """The same doctrine as `served_from`, on the served-declaration cell (#4192).

    A null is malformed EVIDENCE: skipping it would let a changed default ride the baseline,
    and the cell is derived, so no legitimate cut produces one.
    """
    import copy
    import tempfile

    doc = _manifest()
    mutated = copy.deepcopy(doc)
    for row in mutated["rows"]:
        if row.get("name") == "tortoise_search":
            row["served_attributes"] = None
    tmp = pathlib.Path(tempfile.mkdtemp()) / "m.yml"
    tmp.write_text(yaml.safe_dump(mutated, sort_keys=False, width=110))

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "surface-guard.py"), "--manifest", str(tmp)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, "a null served_attributes was skipped instead of refused"
    assert "no `served_attributes` fingerprint" in result.stdout, (
        "the guard redded for some other reason than the missing declaration cell"
    )


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
    # Scope the row scan to the MCP tools section. The `response_fields` table
    # (the #3863 carve-out record) also renders rows whose first cell is a
    # backticked tool name, so scanning the whole document counted a recorded
    # FIELD as a tool and made this check lie about the surface size (B6 #3892).
    # The RETIRED names render in their own 4-column table below (#3883); it is
    # not a tool row and must not be counted or checked as one here.
    region = text.split("## The MCP tools", 1)[-1]
    region = region.split("## Retired names", 1)[0].split("## The SDK methods", 1)[0]

    true_never = {r["name"] for r in tools if "never called" in (r.get("used_by") or "")}
    true_in_use = {r["name"] for r in tools} - true_never
    assert true_never and true_in_use, "the fixture is degenerate — no usage signal at all"

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


# ── the `response_fields` record (the #3863 carve-out; B6 #3892) ──────────────
# An off-by-default field on an existing response is not a new tool or endpoint,
# so it does not gate as an addition — but it must still be RECORDED. These pin
# the record's existence, its anchoring, and its survival across a re-cut.

def test_response_fields_block_is_present_and_anchored():
    doc = _manifest()
    block = doc.get("response_fields")
    assert isinstance(block, list) and block, (
        "the response_fields block is the single source of truth for "
        "off-by-default response additions and must not be empty or missing"
    )
    row_names = {str(r["name"]) for r in doc["rows"] if isinstance(r, dict) and "name" in r}
    for rf in block:
        assert rf.get("response") and rf.get("field"), f"malformed entry: {rf!r}"
        assert rf["response"] in row_names or f"sdk:{rf['response']}" in row_names, (
            f"response_fields entry {rf!r} is not anchored to a tool/endpoint in the manifest"
        )


def test_response_fields_block_is_carried_across_a_re_cut():
    """`cut` must carry the hand-authored record forward, not drop it."""
    sys.path.insert(0, str(ROOT / "tools"))
    import surface_manifest

    assert surface_manifest._carried_response_fields() == _manifest()["response_fields"]


def test_recorded_response_field_is_rendered_in_the_list():
    text = RENDERED.read_text(encoding="utf-8")
    assert "### Recorded response fields" in text, (
        "the record is not rendered into the list the owner reviews"
    )
    for rf in _manifest()["response_fields"]:
        assert f"`{rf['field']}`" in text


# --- ROUND-2 REVIEW FINDINGS: fail-open content paths, closed with mutations ---------
# Four fresh-context reviewers ran on this change. Their P1s were both real: (1) a
# DUPLICATE row name silently dropped content from the drift comparison AND from the D2
# guard, reproducibly green with a doctored duplicate; (2) the approval-reset test could
# not observe the write site it claimed to pin. The P2s were malformed evidence escaping
# as tracebacks, an uncompared row order, and the retired-name contract no longer being
# isolated. Every test below is the mutation evidence for one of those fixes.


def test_cut_resets_approvals_at_the_WRITE_SITE(monkeypatch, tmp_path, derived_baseline):
    """The reset is the CONTROL for the per-row AUDIT REFERENCE (CONTRIBUTING.md step 4).

    `cut` folds a change in, carries the TOP-LEVEL approval forward (it is READ from the
    canonical doc, #4185), and blanks the per-row `approval` audit reference. A carry-over of
    the previous row references — drafted because
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
    ns = argparse.Namespace(commit="deadbeef", allow_approval_reset=True)
    assert sm.cmd_cut(ns) == 0

    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    # The TOP-LEVEL approval is READ from the canonical doc (#4185), so a re-cut
    # carries the owner's real approval forward — that is the defect fixed; the
    # per-row reset below is the separate #4598 control, kept.
    assert written["approval_status"] == "approved"
    assert written["approval_principal"] == "daniel-ospina"
    assert written["approval_pr"] == "4120"
    carried = [
        r["name"]
        for r in [*written["rows"], *written["retired"]]
        if r.get("approval") is not None
    ]
    assert carried == [], (
        "the write site carried the previous approvals across the re-cut, reversing the "
        f"control CONTRIBUTING.md steps 2-4 rely on: {carried[:5]}"
    )


def test_a_re_cut_re_emits_the_OVERRIDES_header(monkeypatch, tmp_path, derived_baseline):
    """`yaml.safe_dump` carries no comments, so a lazy re-cut would DELETE the marker.

    The tool docstrings are the durable home, but the manifest is the artifact a lane
    actually reads. `cut` must put the marker back rather than silently dropping it —
    otherwise the first re-cut after this PR erases the record of what was overridden.
    """
    sm = _load_manifest_tool()
    path = tmp_path / "surface-manifest.yml"
    prior = copy.deepcopy(_manifest())
    path.write_text(yaml.safe_dump(prior, sort_keys=False, allow_unicode=True, width=110))
    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    monkeypatch.setattr(sm, "build_doc", lambda *a, **k: copy.deepcopy(derived_baseline))
    assert sm.cmd_cut(argparse.Namespace(commit="deadbeef", allow_approval_reset=True)) == 0
    written = path.read_text(encoding="utf-8")
    assert written.startswith(sm.MANIFEST_HEADER)
    assert "OVERRIDES: the frozen-snapshot-as-approval-baseline practice" in written
    # The approval-provenance comment must survive too: the committed manifest carries it,
    # and a re-cut that dropped it would erase the record of WHERE the approval is read from.
    assert "The top-level approval is READ from docs/product/canonical-mcp-tools.md" in written
    # The growth pin's rationale must survive too — a re-cut that dropped it would leave the
    # next reader with no idea why `high_water_counts` cannot be edited by hand.
    assert "`high_water_counts` is a GROWTH PIN" in written
    # …and the header must not carry a FORMATTED count of the target (`26 MCP tools` / `40 SDK`):
    # the target lives in the product docs, read by `tools/surface-target-check.py`, and a
    # restated literal here would go stale silently. The mandated `OVERRIDES:` marker above is
    # the only place the owner's `26/40` appears, quoted verbatim as the approval record.
    assert "26 MCP tools" not in sm.MANIFEST_HEADER
    assert "40 SDK" not in sm.MANIFEST_HEADER


def test_the_committed_manifest_header_is_exactly_what_cut_re_emits():
    """`cut` re-emits MANIFEST_HEADER verbatim, so the committed header must match it.

    Otherwise the next re-cut silently re-wraps the comments and the artifact a reviewer
    diffs is not the artifact `cut` produces (found by review). `check` cannot see this:
    YAML drops comments, so the header is outside every derived comparison.
    """
    import difflib

    sm = _load_manifest_tool()
    committed = MANIFEST.read_text(encoding="utf-8").split("manifest_version:")[0]
    assert committed == sm.MANIFEST_HEADER, "\n".join(
        difflib.unified_diff(
            sm.MANIFEST_HEADER.splitlines(),
            committed.splitlines(),
            "MANIFEST_HEADER",
            "config/surface-manifest.yml",
            lineterm="",
        )
    )


def test_the_committed_manifest_carries_the_DERIVED_approval():
    """#8144: the flip has landed, so the committed copy carries the owner's approval.

    The staged two-PR split is over: `read_approval` derives the four values from
    `docs/product/canonical-mcp-tools.md`, `cut` copies them in, and `check` compares them
    (they are no longer in `NON_DERIVABLE_DOC_KEYS`). Asserting EQUALITY against the live
    derivation — rather than hardcoding the doc's values — is what makes this the invariant
    rather than a second copy of the document: if the doc's approval moves, this reds until
    a re-cut copies it.
    """
    sm = _load_manifest_tool()
    doc = _manifest()
    derived = sm.read_approval()
    assert doc["approval_status"] == derived["status"] == "approved", (
        "the committed manifest is not the approved copy — the flip (#8144) is missing"
    )
    for key, want in (
        ("approval_principal", derived["principal"]),
        ("approval_pr", derived["pr"]),
        ("approval_date", derived["date"]),
    ):
        assert doc[key] == want, (
            f"{key} is {doc[key]!r} in the committed copy but {want!r} derived from "
            "docs/product/canonical-mcp-tools.md — re-cut to copy the document"
        )


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


def _render_manifest(sm, doc, tmp_path, monkeypatch) -> str:
    """Render `doc`, and return the bytes `cmd_render` wrote.

    Both paths are redirected and `_read_manifest` resolves `MANIFEST_FILE` at call time,
    so the text asserted on is the RENDER's own output — never the committed document,
    which is what made an earlier form of this check vacuous.
    """
    path = _manifest_at(sm, doc, tmp_path)
    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    out = _render_scratch()
    monkeypatch.setattr(sm, "RENDERED_FILE", out)
    try:
        assert sm.cmd_render(argparse.Namespace()) == 0
        return out.read_text(encoding="utf-8")
    finally:
        out.unlink(missing_ok=True)


def _with_first_used_by_token(doc: dict, token: str) -> dict:
    """Rewrite EVERY row's first `used_by` token — unconditionally, so the fixture does
    not depend on the state it is about to remove."""
    out = copy.deepcopy(doc)
    for row in out["rows"]:
        rest = [p.strip() for p in (row.get("used_by") or "").split(",") if p.strip()]
        row["used_by"] = ", ".join([token, *[p for p in rest[1:] if p not in ("agents", "never called")]])
    return out


def test_the_render_prints_a_measured_zero_rather_than_none_at_all(tmp_path, monkeypatch):
    """A count of ZERO that WAS measured must still be printed (guard on the signal, not
    on the count).

    Every row carrying `agents` and none `never called` is a legitimate measurement whose
    answer is "0 of N" — `_never` is empty, yet a signal exists. This is the one state
    that separates the shipped guard from a wrong `if _never:` guard, which would report
    the measurement as absent and silently swap a real zero for no answer at all.
    """
    sm = _load_manifest_tool()
    doc = _with_first_used_by_token(_manifest(), "agents")
    assert all(sm.observed_usage(r) == "in use" for r in doc["rows"])

    text = _render_manifest(sm, doc, tmp_path, monkeypatch)

    tools_total = len([r for r in doc["rows"] if not str(r.get("name", "")).startswith("sdk:")])
    assert f"0 of the {tools_total} tools we advertise" in text, (
        "a MEASURED zero was suppressed — the guard keyed on the count being non-zero "
        "instead of on a signal existing"
    )
    assert "is asserted here" not in text, (
        "the render claimed it was not asserting a count while it had a measurement"
    )


def test_the_render_asserts_no_count_when_the_baseline_is_only_partly_measured(
    tmp_path, monkeypatch
):
    """A PARTLY flagged baseline must not print a count over its unfagged rows.

    This is the state that separates the shipped guard (`len(_measured) == len(tools)`) from
    the `any(observed_usage(r) is not None ...)` guard it replaced — and from every other
    "some row is measured" guard. Both agree on the all-flagged and none-flagged extremes,
    so without this test the guard can regress silently. `used_by` is in
    NON_DERIVABLE_ROW_KEYS (excluded from the drift comparison), so a partly flagged
    baseline is reachable through the gate, not merely constructible.

    It also pins the absence prose to what is observed: the render may say how many rows
    carry a flag, and must NOT claim that NO row does.
    """
    import re

    sm = _load_manifest_tool()
    base = _with_first_used_by_token(_manifest(), "tooling")
    tool_rows = [r for r in base["rows"] if not str(r.get("name", "")).startswith("sdk:")]
    # Flag every second tool row, so the flags are genuinely partial — AND drop one unmeasured
    # row, so the population is not the committed 82: a hardcoded denominator, or an
    # `_unassessed` that forgot to subtract the measured rows, would otherwise pass.
    for row in tool_rows[::2]:
        row["used_by"] = ", ".join(["agents", *[p.strip() for p in (row["used_by"] or "").split(",") if p.strip() and p.strip() != "tooling"]])
    dropped = next(r for r in tool_rows if sm.observed_usage(r) is None)
    base["rows"] = [r for r in base["rows"] if r is not dropped]
    tool_rows = [r for r in base["rows"] if not str(r.get("name", "")).startswith("sdk:")]
    total = len(tool_rows)
    measured = sum(1 for r in tool_rows if sm.observed_usage(r) is not None)
    assert 0 < measured < total and total != 82, "the fixture must leave the baseline only partly measured, at a non-default population"

    text = _render_manifest(sm, base, tmp_path, monkeypatch)

    assert re.search(r"\d+ of the \d+ tools we advertise", text) is None, (
        "the render printed an uncalled count over a population it never measured in full"
    )
    assert re.search(r"no count\s+of uncalled tools is asserted here", text), (
        "the render neither measured the count nor said it was not asserting one"
    )
    assert f"flag on {measured} of its {total} tool rows" in text, (
        "the render must report the measurement it actually has, not a universal"
    )
    assert f"the other {total - measured} carry none" in text, (
        "the unassessed count must be derived (total minus measured), not the population "
        "or the measured count"
    )
    assert "No row" not in text and "no row" not in text, (
        "the render claimed a universal absence while some rows DO carry a flag"
    )


WHOLE_PARAGRAPH_MARKER = "**The case for pinning a small advertised set.**"


def _paragraph(text: str) -> str:
    """The advertised-set paragraph, exactly as rendered (marker through the blank line)."""
    start = text.index(WHOLE_PARAGRAPH_MARKER)
    return text[start : text.index("\n\n", start)]


def test_the_render_reports_only_what_it_can_observe_when_no_row_is_measured(
    tmp_path, monkeypatch
):
    """With no flag anywhere, the WHOLE paragraph is pinned verbatim — including what it omits.

    Three things previous iterations got wrong, each of which this pin catches wherever it
    appears in the paragraph (a substring pin did not — a count or a cause sentence appended
    past the pinned chunk slipped through): the count is gone ("Two thirds ... (0 of 82)"
    was a negative nobody measured); no CAUSE is named for the missing flag
    (`observed_usage` cannot observe one — "it was cut without the call log" was the same
    defect in new prose); and no clause refers to flags that may not exist.

    The cost is deliberate: this paragraph IS the contract, so a rewording reds and forces a
    re-read of the diff. Keep it in step with `cmd_render`.
    """
    import re

    sm = _load_manifest_tool()
    doc = _with_first_used_by_token(_manifest(), "tooling")
    tool_rows = [r for r in doc["rows"] if not str(r.get("name", "")).startswith("sdk:")]
    total = len(tool_rows)
    assert total and all(sm.observed_usage(r) is None for r in tool_rows), (
        "the fixture must leave NO tool row with a usage signal"
    )

    text = _render_manifest(sm, doc, tmp_path, monkeypatch)

    expected = (
        "**The case for pinning a small advertised set.** This baseline carries an `in use` / `never called`\n"
        f"flag on 0 of its {total} tool rows — the other {total} carry none — so **no count\n"
        "of uncalled tools is asserted here**: a count over all of them would include rows nobody assessed.\n"
        "The flag records one thing: whether a call appears in our own tool-call log (`in use`) or none\n"
        "does (`never called`) — evidence about our usage, not about whether a tool is useful to a\n"
        "customer. A row carrying no flag records neither. Mainstream clients cap the tools they will\n"
        "show — a\n"
        f"reported 40 in Cursor — so a client that caps there sees at most 40 of these {total}, while we\n"
        "pay context for all of them on every turn. Every comparable we studied pins a smaller set,\n"
        "and the pattern is not novel here: `tortoise_recall` is already one tool with four modes and\n"
        "`tortoise_get_entity` already absorbed five of the six fetch-by-id getters — the sixth,\n"
        "`tortoise_get_session`, is proposed for merge. Deferring the rest keeps all " + f"{total} callable."
    )
    assert _paragraph(text) == expected, (
        "the no-measurement paragraph changed — it may report only the observation, name no "
        "cause, and carry no count; re-read the diff before updating this pin"
    )
    assert re.search(r"\d+ of the \d+ tools we advertise", text) is None, (
        "a count reappeared in the no-measurement state"
    )


def test_the_render_refuses_a_baseline_with_no_tool_rows(tmp_path, monkeypatch):
    """A tool-less baseline is a re-cut gone wrong, not a document to render.

    Every section of the rendered list quantifies over `tools` — the surface size ("all 0 tools
    at once"), the 14-of-N contrast, the uncalled count — so rendering one publishes arithmetic
    about a surface that does not exist. The earlier attempt to make only the usage paragraph
    empty-safe left the rest of the document asserting "the other -14" and "all 0 tools", which
    is why the render now refuses instead. `check` already rejects such a baseline.
    """
    sm = _load_manifest_tool()
    doc = copy.deepcopy(_manifest())
    doc["rows"] = [r for r in doc["rows"] if str(r.get("name", "")).startswith("sdk:")]
    assert doc["rows"], "the fixture must keep the SDK rows"

    path = _manifest_at(sm, doc, tmp_path)
    monkeypatch.setattr(sm, "MANIFEST_FILE", path)
    out = _render_scratch()
    monkeypatch.setattr(sm, "RENDERED_FILE", out)
    try:
        assert sm.cmd_render(argparse.Namespace()) == 1, (
            "a baseline with no tool rows must be REFUSED, not rendered"
        )
        assert not out.exists(), "the refusal must not write a document"
    finally:
        out.unlink(missing_ok=True)


def test_the_render_derives_the_uncalled_count_from_the_baseline(tmp_path, monkeypatch):
    """Both halves of the count are read from the baseline, proven on values no literal matches.

    Reading the committed manifest would prove nothing: its count (55 of 82) can be — and once
    was — a literal in the generator, so the assertion passes against a hardcode. So the
    fixture perturbs BOTH the numerator (one `never called` row flipped to `agents`) and the
    population (one `in use` row dropped), keeping every remaining row measured so the measured
    branch is still taken, and asserts the render follows to both. Read from the RENDER's own
    output, never from the committed document: an earlier form of this check asserted on
    `RENDERED.read_text()`, so it passed even under a guard that always reported the
    measurement as absent.
    """
    sm = _load_manifest_tool()
    doc = _manifest()
    tool_rows = [r for r in doc["rows"] if not str(r.get("name", "")).startswith("sdk:")]
    committed_never = sum(1 for r in tool_rows if sm.observed_usage(r) == "never called")
    victim = next(r for r in tool_rows if sm.observed_usage(r) == "never called")
    rest = [p.strip() for p in (victim.get("used_by") or "").split(",") if p.strip()]
    victim["used_by"] = ", ".join(["agents", *[p for p in rest[1:] if p not in ("agents", "never called")]])
    dropped = next(r for r in tool_rows if sm.observed_usage(r) == "in use")
    assert dropped is not victim, "the flipped row and the dropped row must differ"
    doc["rows"] = [r for r in doc["rows"] if r is not dropped]

    # Recompute from the PERTURBED document — assigning these above would make the check a
    # tautology, which is how an earlier version of this test verified nothing.
    remaining = [r for r in doc["rows"] if not str(r.get("name", "")).startswith("sdk:")]
    total = len(remaining)
    never = sum(1 for r in remaining if sm.observed_usage(r) == "never called")
    assert total == len(tool_rows) - 1 and never == committed_never - 1, (
        "the fixture must move BOTH the numerator and the population"
    )
    assert all(sm.observed_usage(r) is not None for r in remaining), (
        "the perturbation must not leave any row unmeasured"
    )

    text = _render_manifest(sm, doc, tmp_path, monkeypatch)

    assert f"{never} of the {total} tools we advertise" in text, (
        "the measured branch did not print the count its own baseline carries — a hardcoded "
        "numerator or denominator would contradict the measurement at any other count"
    )
    assert "Two thirds" not in text, "the magnitude must be derived, not asserted"


def test_the_render_does_not_claim_a_client_cap_binds_a_smaller_surface(tmp_path, monkeypatch):
    """The cap and comparable clauses are arithmetic over the advertised set, so both need a set
    on the right side of their threshold.

    "a client that caps there sees at most 40 of these {N}" is false for any surface the cap does
    not bind — and "40 of these 10" is arithmetic over a population with no 40 in it — while
    "every comparable we studied pins a smaller set" is false whenever N <= the largest comparable
    (Letta, 18). Both are the direction this document itself argues for (it proposes shrinking the
    advertised set), so the small cases are on the intended path. Every threshold is rendered on
    BOTH sides, including the boundary value itself, which is the only thing that pins the
    comparator: an off-by-one at 40 or 18 would otherwise survive.
    """
    sm = _load_manifest_tool()
    base = _manifest()
    tool_idx = [
        i for i, r in enumerate(base["rows"]) if not str(r.get("name", "")).startswith("sdk:")
    ]
    assert len(tool_idx) > 41, "the fixture needs a surface on both sides of the cap"

    for n in (10, 18, 19, 40, 41):
        doc = copy.deepcopy(base)
        keep = set(tool_idx[:n])
        doc["rows"] = [
            r for i, r in enumerate(doc["rows"])
            if i in keep or str(r.get("name", "")).startswith("sdk:")
        ]
        assert len([r for r in doc["rows"] if not str(r.get("name", "")).startswith("sdk:")]) == n

        text = _render_manifest(sm, doc, tmp_path, monkeypatch)

        assert "the other -" not in text, f"a {n}-tool surface rendered a negative remainder"
        if n > 40:
            assert f"sees at most 40 of these {n}" in text, (
                "a surface larger than the cap must say the cap hides part of it"
            )
        else:
            assert "at most 40 of these" not in text, (
                "the cap cannot hide part of a surface it fits, and '40 of these 10' is "
                "arithmetic over a population that has no 40 in it"
            )
            assert "a cap the whole surface fits inside" in text, (
                "the small surface must say the cap does not bind"
            )
        if n > sm.LARGEST_COMPARABLE:
            assert "Every comparable we studied pins a smaller set," in text, (
                f"{n} exceeds the largest comparable, so the comparison holds"
            )
        else:
            assert "Every comparable we studied pins a smaller set," not in text, (
                f"{n} is not larger than the largest comparable (Letta pins "
                f"{sm.LARGEST_COMPARABLE}), so 'pins a smaller set' is false here"
            )
            assert f"the biggest comparable, Letta, pins {sm.LARGEST_COMPARABLE}" in text


# --- the surface TARGET compliance check (#4185) -----------------------------------
# `tools/surface-guard.py` compares the live declaration to the FROZEN BASELINE — a
# snapshot of the implementation. It therefore cannot see a surface 3x over the owner's
# approved target, which is the defect this file's sibling answers:
# `tools/surface-target-check.py` compares the LIVE surface to the TARGET (26/40) read
# from the two product docs. Co-located here rather than in a new test module so the
# CI-surfaces manifest does not need a second registration for the same surface.


def _load_target_check():
    """Import `tools/surface-target-check.py` (hyphenated filename) as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "surface_target_check", ROOT / "tools" / "surface-target-check.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_canonical_fixture(path: Path, *, status: str = "approved", mcp: int = 7) -> None:
    path.write_text(
        "---\n"
        f"approval_status: {status}\n"
        "approved: 2030-01-02\n"
        "approvedBy: someone-else\n"
        "approval_pr: 9999\n"
        "---\n\n"
        f"# Canonical MCP tool list — 5 proposed tools (the beta target is {mcp})\n\n"
        f"**MCP target count is unchanged at {mcp}**\n",
        encoding="utf-8",
    )


def _write_beta_fixture(path: Path, *, mcp: int = 7, sdk: int = 9) -> None:
    path.write_text(
        "---\napproval_status: approved\napprovedBy: someone-else\n---\n\n"
        "# Beta SDK surface\n\n"
        f"**{sdk} methods** against **150** today. **MCP: {mcp} tools**\n",
        encoding="utf-8",
    )


def test_the_live_surface_is_the_baseline_the_guard_froze():
    """The live counts are the same numbers the manifest's `counts:` records.

    A compliance check that measured a DIFFERENT surface than the drift gate would be
    two instruments disagreeing about what "the surface" is.
    """
    tc = _load_target_check()
    live = tc.live_surface()
    manifest = _manifest()
    assert live["mcp"] == manifest["counts"]["tools"] == 82
    assert live["sdk"] == manifest["counts"]["sdk_public_methods"] == 150


def test_the_compliance_check_is_RED_on_todays_gap(capsys):
    """#4185's whole point: the live surface does NOT match the approved target.

    Asserts the REAL numbers and the deltas, not just a non-zero exit — a check that
    reds without naming the gap is the wall of noise this replaces.
    """
    tc = _load_target_check()
    live = tc.live_surface()
    targets = tc.read_targets()
    # The owner's ruling is the invariant; the live numbers are MEASURED, not restated, so
    # the migration that closes the gap only needs this test's premise updated, not its
    # arithmetic (#4185 review finding).
    assert (targets["mcp"], targets["sdk"]) == (26, 40)
    assert (live["mcp"], live["sdk"]) != (26, 40), (
        "the gap has closed — update this test's premise (it exists to pin the RED)"
    )
    rc = tc.main(["surface-target-check.py"])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert f"{targets['mcp']} MCP tools / {targets['sdk']} SDK methods" in out, out
    assert f"{live['mcp']} MCP tools / {live['sdk']} SDK methods" in out, out
    assert f"Δ {live['mcp'] - targets['mcp']:+d}" in out, out
    assert f"Δ {live['sdk'] - targets['sdk']:+d}" in out, out
    assert "OVER TARGET" in out, out


def test_a_surface_UNDER_the_target_is_labelled_UNDER_not_OVER(monkeypatch, capsys):
    """The migration can overshoot; an UNDER-target surface must not read as OVER TARGET.

    The tool is meant to be run DURING the migration, when an over-aggressive cut lands
    exactly this state — `"OK" if delta == 0 else "OVER TARGET"` labelled it wrongly.
    """
    tc = _load_target_check()
    monkeypatch.setattr(
        tc,
        "live_surface",
        lambda: {"mcp": 20, "sdk": 30, "tools": [], "sdk_methods": []},
    )
    rc = tc.main(["surface-target-check.py"])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "UNDER TARGET" in out and "OVER TARGET" not in out, out


def test_the_compliance_check_is_GREEN_when_the_live_surface_equals_the_target(
    monkeypatch, capsys
):
    """The fixture is the migrated surface: 26 tools, 40 methods → exit 0.

    `live_surface` is the seam, so the test does not have to delete 56 tools to prove
    the green path (which the task explicitly forbids anyway).
    """
    tc = _load_target_check()
    monkeypatch.setattr(
        tc,
        "live_surface",
        lambda: {"mcp": 26, "sdk": 40, "tools": [], "sdk_methods": []},
    )
    rc = tc.main(["surface-target-check.py"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "OK — the live surface matches the owner-approved target." in out, out


def test_the_target_is_read_from_the_docs_not_a_constant(tmp_path):
    """A changed doc changes the target — the property a duplicated `26`/`40` lacks."""
    tc = _load_target_check()
    canonical = tmp_path / "canonical-mcp-tools.md"
    beta = tmp_path / "beta-sdk-surface.md"
    _write_canonical_fixture(canonical, mcp=11)
    _write_beta_fixture(beta, mcp=11, sdk=13)
    targets = tc.read_targets(canonical, beta)
    assert targets["mcp"] == 11
    assert targets["sdk"] == 13
    assert targets["approval"]["principal"] == "someone-else"
    assert targets["approval"]["pr"] == "9999"


def test_the_canonical_docs_beta_target_number_is_not_read_as_the_mcp_target(tmp_path):
    """#4185 review P3: each pattern is scoped to the document that OWNS the phrase.

    `beta target (is|becomes) N` names the BETA list's own count when it appears in the
    canonical doc, not the MCP target. Running the full pattern set over both docs meant a
    future amendment that moved only the canonical `MCP target count` looked ambiguous and
    the tool refused a correctly-readable target. Pre-fix this fixture raises
    `TargetUnreadable` (canonical yields {26, 40}); post-fix the canonical doc's own pattern
    reads 26 and the `beta target` phrase is ignored.
    """
    tc = _load_target_check()
    canonical = tmp_path / "canonical-mcp-tools.md"
    beta = tmp_path / "beta-sdk-surface.md"
    canonical.write_text(
        "---\n"
        "approval_status: approved\n"
        "approved: 2030-01-02\n"
        "approvedBy: someone-else\n"
        "approval_pr: 9999\n"
        "---\n\n"
        "# Canonical MCP tool list — 23 proposed tools (the beta target is 40)\n\n"
        "**MCP target count is unchanged at 26**\n",
        encoding="utf-8",
    )
    _write_beta_fixture(beta, mcp=26, sdk=40)
    targets = tc.read_targets(canonical, beta)
    assert targets["mcp"] == 26, "the canonical doc's `beta target` number leaked into the MCP target"
    assert targets["sdk"] == 40


def test_an_unapproved_canonical_doc_is_a_refusal(tmp_path):
    """There is no owner-approved target when the doc is not approved."""
    tc = _load_target_check()
    canonical = tmp_path / "canonical-mcp-tools.md"
    beta = tmp_path / "beta-sdk-surface.md"
    _write_canonical_fixture(canonical, status="pending-owner-approval", mcp=7)
    _write_beta_fixture(beta, mcp=7, sdk=9)
    with pytest.raises(tc.TargetUnreadable):
        tc.read_targets(canonical, beta)


def test_an_unapproved_beta_doc_is_a_refusal(tmp_path):
    """The beta doc is the SOLE source of the SDK target — it must be approved too."""
    tc = _load_target_check()
    canonical = tmp_path / "canonical-mcp-tools.md"
    beta = tmp_path / "beta-sdk-surface.md"
    _write_canonical_fixture(canonical, mcp=7)
    beta.write_text(
        "---\napproval_status: pending-owner-approval\napprovedBy: someone-else\n---\n\n"
        "**9 methods** against **150** today. **MCP: 7 tools**\n",
        encoding="utf-8",
    )
    with pytest.raises(tc.TargetUnreadable):
        tc.read_targets(canonical, beta)
    # …and a beta with `approved` but no principal is refused the same way.
    beta.write_text(
        "---\napproval_status: approved\n---\n\n"
        "**9 methods** against **150** today. **MCP: 7 tools**\n",
        encoding="utf-8",
    )
    with pytest.raises(tc.TargetUnreadable):
        tc.read_targets(canonical, beta)


def test_two_docs_that_disagree_on_the_mcp_target_are_a_refusal(tmp_path):
    """No guessing: two numbers is not a target."""
    tc = _load_target_check()
    canonical = tmp_path / "canonical-mcp-tools.md"
    beta = tmp_path / "beta-sdk-surface.md"
    _write_canonical_fixture(canonical, mcp=7)
    _write_beta_fixture(beta, mcp=8, sdk=9)
    with pytest.raises(tc.TargetUnreadable):
        tc.read_targets(canonical, beta)


def test_a_doc_that_yields_no_mcp_target_is_a_refusal(tmp_path):
    """The redundancy IS the control: a doc with no MCP number must refuse, not fall back.

    A UNION across the two docs tolerated exactly this — deleting the MCP phrase from one
    doc still produced the other doc's number, so the "cross-checked in both docs" claim
    was decorative (review finding). Each doc must carry the number.
    """
    tc = _load_target_check()
    canonical = tmp_path / "canonical-mcp-tools.md"
    beta = tmp_path / "beta-sdk-surface.md"
    _write_canonical_fixture(canonical, mcp=7)
    beta.write_text(
        "---\napproval_status: approved\napprovedBy: someone-else\n---\n\n"
        "# Beta SDK surface\n\n**9 methods** against **150** today.\n",
        encoding="utf-8",
    )
    with pytest.raises(tc.TargetUnreadable):
        tc.read_targets(canonical, beta)


def test_an_approved_doc_without_a_principal_or_pr_is_a_refusal(tmp_path):
    """`approved` with no principal and no PR is not an approval record.

    surface-guard already rejects that doc when the manifest claims `approved`; the
    compliance instrument must apply the same completeness test, or it honours an approval
    the gate would refuse.
    """
    tc = _load_target_check()
    canonical = tmp_path / "canonical-mcp-tools.md"
    beta = tmp_path / "beta-sdk-surface.md"
    canonical.write_text(
        "---\napproval_status: approved\n---\n\n**MCP target count is unchanged at 7**\n",
        encoding="utf-8",
    )
    _write_beta_fixture(beta, mcp=7, sdk=9)
    with pytest.raises(tc.TargetUnreadable):
        tc.read_targets(canonical, beta)


def test_a_missing_target_doc_is_a_refusal(tmp_path):
    """A check that cannot read its evidence must not report success."""
    tc = _load_target_check()
    with pytest.raises(tc.TargetUnreadable):
        tc.read_targets(tmp_path / "nope.md", tmp_path / "also-nope.md")


def test_the_target_tool_refuses_rather_than_pass_when_evidence_is_unreadable(
    capsys, tmp_path
):
    """The CLI path: an unreadable target is `::error::` + exit 1, not a traceback."""
    tc = _load_target_check()
    rc = tc.main(
        ["surface-target-check.py", "--canonical-doc", str(tmp_path / "missing.md")]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "::error::" in out and "REFUSED" in out, out
    assert "Traceback" not in out

"""#13899 — the privatisation sweep's verifier must be able to say NO.

`tools/sdk_privatise.py` took 66 off-surface `TortoiseSDK` methods off the public surface
and rewrote every reference it could prove. Two review findings were about the VERIFIER,
not the sweep, and both are the fail-open shape a sweep cannot afford:

  * **F3 — the vacuous check.** `--check` derived the rename map from
    `git merge-base HEAD origin/main`. On this branch that resolves to a commit whose
    class still holds the 66 public names, so the map was populated and the check looked
    meaningful. The moment this branch MERGES, the merge-base is a commit whose class
    holds the 39 approved names, the map is `{}`, and `--check` prints
    `typed references still pointing at a public name: 0` and exits 0 — over any tree at
    all, swept or not. A verifier whose answer cannot vary is not a verifier.
  * **F2 — the file-wide typing.** `sdk_bound_names` built a PER-FILE set of SDK-typed
    names. `tortoise/hosted_api.py` binds `reg` from `_registry_sdk()` (a real
    `TortoiseSDK`) in one function and from `sdk._get_registry()` (a FalkorDB `Graph`) in
    ~78 others, so the file-wide set typed every `reg.` in the file and 80 graph-handle
    calls were rewritten `reg.query(` → `reg._query(` — the control plane now depends on
    a third-party PRIVATE method. That is exactly the "ordinary word another object also
    answers to" sabotage the tool's own docstring warns about, and the tool performed it.

Every test below asks a question the tool answered WRONGLY at the review head, so each is
a mutation the fix has to keep red. Nothing here runs the tree-wide walk: the pins are
read from the map and from the typing, and the two synthetic modules carry the shape.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_tool():
    """Load `tools/sdk_privatise.py` as a module (`tools/` is a namespace package here)."""
    spec = importlib.util.spec_from_file_location(
        "sdk_privatise_under_test", ROOT / "tools" / "sdk_privatise.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves its module through sys.modules
    spec.loader.exec_module(module)
    return module


privatise = _load_tool()


def _module(tmp_path: Path, src: str) -> Path:
    path = tmp_path / "subject.py"
    path.write_text(src, encoding="utf-8")
    return path


def _base_is_readable(rev: str) -> bool:
    """Is `rev`'s `tortoise/sdk.py` readable in THIS clone?

    ⛔ The `test` job checks out at the default `fetch-depth: 1` (no `with:` block on
    `actions/checkout`, and no `unshallow` anywhere under `.github/`), so a historical
    `SWEEP_BASE` is ABSENT by construction there and `_sdk_source()` exits the session
    (`SystemExit`, not a skip) — which reds the very job that runs this file. The arm
    below therefore asks the question first and skips on ABSENCE, the same idiom as
    `tests/test_7949_capstone_measure.py` (`git show <sha>:…` → `pytest.skip`) and
    `tests/test_11739_quote_population_probe.py` (which names `fetch-depth: 1` for it).
    The real measurement is carried at depth 0 by the `surface-guard` job's
    `python3 tools/sdk_privatise.py --check` step, which resolves `SWEEP_BASE` through
    `git show` — so a shallow clone reports UNCHECKED here, never a pass.
    """
    try:
        shown = subprocess.run(
            ["git", "show", f"{rev}:tortoise/sdk.py"],
            cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:  # no `git` on this host at all
        return False
    return shown.returncode == 0


# ── F3: the pin, and the refusal that makes it a verifier ───────────────────────


def test_the_pinned_base_still_derives_the_whole_batch():
    """The map is derived from `SWEEP_BASE`, so it cannot drift with `origin/main`.

    A grep-able count, not a vibe: 66 off-surface methods is the batch, and it is what the
    PR body, `tortoise/sdk_retired.py` and the manifest all claim.
    """
    if not _base_is_readable(privatise.SWEEP_BASE):
        pytest.skip(
            f"{privatise.SWEEP_BASE[:12]} is not carried by this clone — a `fetch-depth: 1` "
            "checkout lacks it by construction, so the map is UNCHECKED here, not empty. "
            "The depth-0 `surface-guard` step runs `tools/sdk_privatise.py --check`, which "
            "resolves the same pinned base; re-arm locally with "
            f"`git fetch origin {privatise.SWEEP_BASE}`.")
    entries = privatise.rename_map(rev=privatise.SWEEP_BASE)
    assert [e.name for e in entries], "the pinned base derived an EMPTY map — `--check` is vacuous"
    assert len(entries) == privatise.SWEEP_NAMES
    assert privatise.SWEEP_NAMES == 66, (
        "SWEEP_NAMES is the non-vacuity floor; changing it is a deliberate re-pin, not drift")
    assert len({e.private for e in entries}) == len(entries), "two names share a private spelling"


def test_check_refuses_a_base_whose_map_is_empty(monkeypatch, capsys):
    """⛔ THE F3 REGRESSION ARM: an empty map is a RED (exit 2), never a passing zero.

    `HEAD` is the swept tree, so `off_surface_names` there is empty — the exact state
    `--check` used to read as "0 references left" after this branch merges. Before the fix
    this returned 0 (after a two-minute tree walk); the assertion is on the exit code, so
    it stays honest either way.
    """
    monkeypatch.setattr(
        sys, "argv", ["sdk_privatise.py", "--check", "--base", "HEAD"]
    )
    assert privatise.rename_map(rev="HEAD") == [], (
        "premise: the swept class holds no off-surface name, so a HEAD-derived map is empty")
    assert privatise.main() == 2, "an empty rename map must refuse, not report a vacuous 0"
    assert "VACUOUS CHECK" in capsys.readouterr().err


def test_the_residue_pin_is_a_reading_of_the_whole_set():
    """F6: the residue is pinned as (rows, digest), so a NEW untyped shape reds the check.

    The digest has to be a function of the SET, not of a count: two different shapes with
    the same number of rows are the case a count cannot see.
    """
    rows, digest = privatise.SWEEP_RESIDUE
    assert rows > 0, "an unpinned (0, …) residue means the drift arm is a no-op"
    assert re.fullmatch(r"[0-9a-f]{16}", digest), digest
    assert privatise.residue_digest(Counter({"x.query": 1})) != privatise.residue_digest(
        Counter({"x.update": 1})
    ), "the digest must distinguish shapes, or the pin only counts"
    assert privatise.residue_digest(Counter({"x.query": 2})) != privatise.residue_digest(
        Counter({"x.query": 1})
    ), "the digest must carry the counts, or a new site is invisible"


# ── F2: typing is per SCOPE; ambiguity is not rewritten ─────────────────────────


def test_a_typed_receiver_reference_is_still_detected(tmp_path):
    """Non-vacuity of the detection arm itself: the sweep must SEE a proved reference."""
    src = (
        "def make():\n"
        "    sdk = TortoiseSDK()\n"
        "    return sdk.create_point('statement', 'a claim')\n"
    )
    edits = privatise.edits_for(_module(tmp_path, src), {"create_point": "_create_point"})
    assert [repl for *_span, repl in edits.attrs] == ["._create_point"]
    assert not edits.residue


def test_the_typing_follows_the_scope_not_the_file(tmp_path):
    """⛔ THE F2 REGRESSION ARM: `reg` is an SDK in one function and a graph handle in the next.

    This is `tortoise/hosted_api.py` in miniature, and it is the shape that produced
    `reg._query(` at 80 sites. A per-FILE typing rewrites BOTH calls; the fix rewrites only
    the one whose own function binds `reg` from an SDK, and reports the other as residue.
    """
    src = (
        "def registry_handle():\n"
        "    reg = _registry_sdk()\n"
        "    return reg.query('k')\n"
        "\n"
        "def graph_handle():\n"
        "    reg = sdk._get_registry()\n"
        "    return reg.query('MATCH (n) RETURN n')\n"
    )
    edits = privatise.edits_for(_module(tmp_path, src), {"query": "_query"})
    rewritten = [line for line, *_rest in edits.attrs]
    assert rewritten == [3], (
        "only the function that binds `reg` from an SDK may be rewritten; a file-wide "
        f"typing rewrote {rewritten} and that is F2")
    assert edits.residue["reg.query"] == 1, (
        "the untyped graph-handle call must be RESIDUE (reported, never guessed at)")


def test_a_name_bound_both_ways_in_one_scope_is_not_rewritten(tmp_path):
    """Ambiguity inside ONE scope resolves to "do not touch it", not to the first match."""
    src = (
        "def handle(flag):\n"
        "    reg = _registry_sdk()\n"
        "    if flag:\n"
        "        reg = sdk._get_registry()\n"
        "    return reg.query('MATCH (n) RETURN n')\n"
    )
    edits = privatise.edits_for(_module(tmp_path, src), {"query": "_query"})
    assert edits.attrs == [], "a name bound from an SDK AND a graph handle is ambiguous"
    assert edits.residue["reg.query"] == 1

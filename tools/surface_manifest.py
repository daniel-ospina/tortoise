#!/usr/bin/env python
"""The agent-facing surface list for #3863 — cut, render, check.

The list is NOT hand-written. Every mechanically-knowable column is obtained by
EXECUTING the declaration (importing TOOL_REGISTRY, introspecting TortoiseSDK)
and by scanning the tree, never by copying text. The manifest is cut once at a
stated commit and then frozen; the Markdown the owner reads is generated from
the manifest. That ordering matters: were the manifest regenerated from the
declaration on every run, a new registry entry would enter the baseline by
itself and the expansion gate could never go red.

    uv run python tools/surface_manifest.py cut      # declaration -> manifest
    uv run python tools/surface_manifest.py render   # manifest -> docs/product/mcp-sdk-surface.md
    uv run python tools/surface_manifest.py check    # the AC13 ordering lint
    uv run python tools/surface-guard.py             # the D2 expansion gate (#3863);
                                                     # a SEPARATE file, so a PR that
                                                     # re-cuts the baseline cannot
                                                     # quietly relax the gate itself.

`cut` is run by a human, once, with an explicit --commit. `render`, `check` and
`guard` are safe to run anywhere, including CI.

WHAT `check` DOES NOT PROVE — APPROVAL IS NOT MACHINE-CARRIED
    `check` verifies CONSISTENCY between the declaration and the frozen baseline.
    It cannot prove that Daniel approved an expansion: a change that updates the
    registry and the baseline together satisfies every property here. The approval
    carrier is the #4282 mandate — ask Daniel FIRST, before the re-cut — and his
    review. A green run is not consent. (The repository ruleset the #3863 scope doc
    proposed as the carrier was REJECTED on #4282 as over-engineering.)

    PROVENANCE IS DERIVED AND COMPARED (#4185; flip landed by #8144). `read_approval`
    reads the owner's approval from `docs/product/canonical-mcp-tools.md` at cut time, and
    the manifest's top-level `approval_status` / `approval_principal` / `approval_pr` /
    `approval_date` are compared by `check` — a hand-edit that disagrees with the document
    reds the lint. They were briefly excluded while the flip was staged in a two-PR split
    (#8026 landed the tooling; #8144 flipped the copy and removed the exclusion).
    `tools/surface-guard.py` independently re-reads the same document and reds a manifest
    that CLAIMS an approval disagreeing with it. Compliance with the owner-approved TARGET
    recorded in `docs/product/canonical-mcp-tools.md` and
    `docs/product/beta-sdk-surface.md` is a separate question, answered against the live
    surface by `tools/surface-target-check.py`.

    OVERRIDES: the frozen-snapshot-as-approval-baseline practice — a snapshot of the
    implementation cannot be the approved target; the target is read from
    docs/product/canonical-mcp-tools.md (owner, 27 MCP tools) and from the declared SDK
    surface in tortoise/__all__.py (owner, 40 methods; cross-checked against
    docs/product/beta-sdk-surface.md) — never from a count beside them.

THE BASELINE IS VERIFIED AGAINST THE CODE, NOT AGAINST ITSELF
    `check` re-derives the whole baseline with the SAME function `cut` writes
    (`build_doc`) and fails on any difference outside the keys whose value is not a
    function of the code alone (`NON_DERIVABLE_ROW_KEYS` / `NON_DERIVABLE_DOC_KEYS`,
    each with its reason stated where it is declared). Without that, the artifact's
    own numbers were the one thing nothing checked: a hand-edited `counts.tools: 999`
    passed both this lint and the D2 expansion gate (measured).

UNAVAILABLE EVIDENCE IS A REFUSAL, NEVER A SKIP
    Every read of the baseline, the order table or the declaration raises
    `SurfaceEvidenceUnreadable` and exits non-zero with `::error::`. A gate that cannot
    read its evidence must not report success (the #1382 class), and a `cut` that cannot
    read the artifact it is about to overwrite must not silently discard the approvals
    recorded in it.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE every other import. The POSITION is
# the property this exists for, not this file's current import list: the corpus
# rule (`tests/test_entry_point_python_guard.py`, property 1) requires `import
# sys` + the guard to precede all other imports, because a module-level
# 3.11+-only import — `from datetime import UTC`, present in sibling tools —
# raises ImportError on an old interpreter before any LATER-placed guard could
# run. This file imports no `datetime` today; the guard is hoisted anyway so the
# rule holds uniformly instead of being re-litigated per import list.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/surface_manifest.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/surface_manifest.py`"
    )

import argparse
import ast
import os
import re
import subprocess
import types
from pathlib import Path

import yaml

# #4828: the served-identity helpers below — `_code_digest`, `_attribute_digest`,
# `_served_attributes` AND `_component_fingerprint` — are DELIBERATE duplicates of the same
# helpers in `tools/surface-guard.py`, and this duplication must NOT be "tidied" into a
# shared module. CI pins the guard to the base ref by OVERWRITING that one file in the PR's
# tree, so a module it imported would be PR-authored — a PR could then hand the gate that
# measures it a digest returning the baseline's recorded values, and the expansion gate
# would go green on a real surface change (#4606, #5132). What holds the copies together
# instead is `tests/test_surface_manifest.py`:
# `test_the_two_drivers_agree_on_the_served_identity_helpers` (code parity, all four),
# `test_both_drivers_fingerprint_the_same_component_identically` and
# `test_the_attribute_encoding_is_pinned_to_measured_values` — a change applied to ONE copy
# reds there. Edit BOTH, or it reds. The inventory is FOUR: `_frontmatter` is legitimately
# divergent (different exception types per side) and `main()` is not shared. That net is
# armed for a `tools/`-only diff by the `tools/ci_selection.py::CORE_ALSO` entries for both
# driver paths (pinned by
# `tests/test_ci_selection.py::test_surface_tools_change_selects_core_not_tier1`); without
# them `tools/` is swallowed by `NON_PYTHON_PREFIXES` and a driver-only diff selects no
# surface at all, so `tests/test_surface_manifest.py` (a `slow_files` member) never runs.


def _code_digest(code) -> str:
    """Move-invariant, implementation-complete digest of a code object.

    `co_code` alone is NOT enough: a constant is loaded as `LOAD_CONST <index>`,
    so two handlers with the same opcode shape but different constants or names
    hash identically (verified: 98 tools collapsed to 62 digests, and a read
    tool shared one with a destructive write tool).  `co_firstlineno` is
    excluded on purpose — a pure code move is not a served change.

    This is the CODE OBJECT only, and it is the `served_from` half of a tool's recorded
    identity. The other half — the parts of the function that live outside its code object
    and are still caller-visible — is `_served_attributes`; a tool is frozen by BOTH cells.
    """
    import hashlib

    h = hashlib.sha256()
    h.update(code.co_code)
    for attr in ("co_names", "co_varnames", "co_freevars", "co_cellvars"):
        h.update(repr(getattr(code, attr)).encode())
    for const in code.co_consts:
        # a nested code object must be recursed, never repr'd (its repr carries
        # a memory address and is not stable)
        if isinstance(const, types.CodeType):
            h.update(_code_digest(const).encode())
        elif isinstance(const, (set, frozenset)):
            # #10602: a SET's iteration order is `PYTHONHASHSEED`-dependent for str
            # elements, so `repr(const)` differs between two runs over an UNCHANGED
            # tree — the same instability as a nested code object's address, and it
            # makes the frozen baseline un-pinnable: `served_from` flipped on every
            # invocation, so `surface_manifest.py check` and this suite failed at
            # random. Encode order-INDEPENDENTLY instead. (Measured: a tool whose
            # body carries `frozenset({'kind','limit','skip'})` produced three
            # different digests across three runs.)
            h.update(b"{" + b",".join(sorted(repr(v).encode() for v in const)) + b"}")
        else:
            h.update(repr(const).encode())
    return h.hexdigest()[:16]


def _attribute_digest(value) -> bytes:
    """A STABLE encoding of one function attribute: a default, a kw-default, an annotation.

    `repr` is the fallback, and it is stable for every value on this surface today (all 53
    defaulted tool functions carry only `None`/`int`/`float`/`str`/`bool`, and every
    annotation is a string under `from __future__ import annotations`). A code-bearing value
    must be RECURSED, never repr'd: a lambda default's repr carries a memory address
    (`<function <lambda> at 0x…>`), so repr'ing it would make the fingerprint differ between
    two runs over an UNCHANGED tree — the same reason `_code_digest` recurses into `co_consts`
    rather than repr'ing a nested code object.
    """
    if isinstance(value, types.CodeType):
        return _code_digest(value).encode()
    if isinstance(value, types.FunctionType):
        # A callable default has BOTH halves too, and its code object is in NEITHER cell by
        # itself: a `def` statement's default is evaluated in the DEFINING scope, so a lambda's
        # code lands in the MODULE's `co_consts` — which nothing here digests. Recursing into
        # both halves is what makes a changed lambda BODY visible; `_served_attributes` alone
        # does not (measured: a test that only compared it stayed green on a changed body).
        return f"{_code_digest(value.__code__)}:{_served_attributes(value)}".encode()
    return repr(value).encode()


def _served_attributes(fn) -> str:
    """The parts of a SERVED function that live OUTSIDE its code object, which a caller sees.

    `_code_digest` reads the code object — bytecode, names, constants. A function's
    `__defaults__`, `__kwdefaults__` and `__annotations__` are NOT in it: they hang off the
    function object, built by the `def` statement's `MAKE_FUNCTION` in the DEFINING module's
    code object. All three are served behaviour — the MCP `tools/list` JSON schema publishes
    each of them as a `type` / `default` / `anyOf` — so

        def tortoise_search(q, limit=10): ...
        def tortoise_search(q, limit=99): ...

    is a different advertised surface under ONE code object. #4192 (verified on this tree: a
    `types.FunctionType` over a served tool's `__code__` with a tampered `__defaults__`
    replaced the handler behind `tortoise_search` while the gate stayed at exit 0).

    WHY ITS OWN MANIFEST CELL, NOT FOLDED INTO `served_from` (#4192). The two halves answer two
    questions — "which implementation" and "which declaration" — so recording them apart
    keeps a diff readable: an edit inside a handler moves `served_from` alone, a changed
    default or annotation moves `served_attributes` alone. The load-bearing reason is CI:
    `tools/surface-guard.py` is PINNED TO THE BASE REF (#5132/#4606), so the run that measures
    a PR uses the BASE ref's copy of that file and cannot know an input added here. Folding
    these attributes into `served_from` would re-stamp all 82 rows, and the pinned guard would
    then red on every one of them — measured on this change: 82 findings and exit 1 while the
    tree's OWN guard was green, i.e. the required check was unsatisfiable by ANY PR. A new
    cell leaves `served_from` byte-identical, so the pin keeps enforcing the implementation
    half while this half is enforced by `check` (this file's derivation against the artifact)
    at PR time and by the guard on the tree itself.
    """
    import hashlib

    h = hashlib.sha256()
    # Positional defaults, in order: the order IS served (the schema's parameters follow the
    # signature), so the tuple is hashed as given.
    for value in getattr(fn, "__defaults__", None) or ():
        h.update(b"\x00default\x00")
        h.update(_attribute_digest(value))
    # Keyword-only defaults, in DECLARATION order (`__kwdefaults__` is a dict, and the order
    # the schema publishes is the signature's).
    for key, value in (getattr(fn, "__kwdefaults__", None) or {}).items():
        h.update(b"\x00kwdefault\x00")
        h.update(repr(key).encode())
        h.update(b"\x1f")
        h.update(_attribute_digest(value))
    for key, value in (getattr(fn, "__annotations__", None) or {}).items():
        h.update(b"\x00annotation\x00")
        h.update(repr(key).encode())
        h.update(b"\x1f")
        h.update(_attribute_digest(value))
    return h.hexdigest()[:16]


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ORDER_FILE = ROOT / "config" / "surface-order.yml"
MANIFEST_FILE = ROOT / "config" / "surface-manifest.yml"

# #11057: every git probe in this file uses ONE environment. `GIT_NO_LAZY_FETCH` keeps a
# PARTIAL (promisor) clone from trying the network inside a lint, and `GIT_TERMINAL_PROMPT`
# keeps a credential prompt from hanging a gate: without either, an absent object asks the
# remote and the failure arrives as a transport error, which would be read as a verdict.
_GIT_ENV = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
RENDERED_FILE = ROOT / "docs" / "product" / "mcp-sdk-surface.md"

# The header `cut` RE-EMITS above the dumped document. `yaml.safe_dump` carries no
# comments, so without this a re-cut would silently delete the OVERRIDES marker (and every
# other explanatory line above the first key). The marker's durable homes are the tool
# docstrings; this is the copy a reader of the ARTIFACT sees, kept across re-cuts.
MANIFEST_HEADER = (
    "# OVERRIDES: the frozen-snapshot-as-approval-baseline practice — a snapshot of the implementation cannot be the approved target; the target is read from docs/product/canonical-mcp-tools.md (owner, 27 MCP tools) and from the declared SDK surface in tortoise/__all__.py (owner, 40 methods; cross-checked against docs/product/beta-sdk-surface.md) — never from a count beside them.\n"
    "#\n"
    "# ROW SET vs APPROVAL. The `sdk:` rows are the frozen baseline of the public SDK surface\n"
    "# as it exists today (`counts.sdk_public_methods`, derived \u2014 never typed into a comment)\n"    "# because the required `surface-guard` job compares the live class against them and is run\n"
    "# from the BASE REF's copy \u2014 the set shrinks only when #4282 Phase 2 removes a method. The\n"
    "# APPROVAL is the per-row `approved` flag, cut from tortoise/__all__.py (40 names): a row\n"
    "# with `approved: false` is migration backlog, not consent. `approved` is a DERIVED row\n"
    "# key, so a re-cut over a grown surface writes `approved: false` for the new method and\n"
    "# property 12 compares two identical derived values — green by construction. What reds an\n"
    "# unapproved addition in required CI is the `high_water_counts` growth pin below, enforced\n"
    "# INDEPENDENTLY by the BASE-REF-pinned `tools/surface-guard.py` (its step 7); the full\n"
    "# `tools/sdk_surface_declaration.py check` names the addition too, but its gap half is\n"
    "# report-only in the required job.\n"
    "#\n"
    "# The top-level approval is READ from docs/product/canonical-mcp-tools.md (#4185) by\n"
    "# `tools/surface_manifest.py::read_approval` and copied into this file by a re-cut.\n"
    "# Do not hand-edit the four values: `check` compares them against the document, so a\n"
    "# hand-edit that disagrees reds the lint. Edit the document, then re-cut.\n"
    "#\n"
    "# `high_water_counts` is a GROWTH PIN, not a derivation: `cut` carries it forward and\n"
    "# `tools/surface-guard.py` reds when the live surface EXCEEDS it. Raising it is a\n"
    "# deliberate, reviewable act in the approving PR \u2014 so a re-cut over a grown surface\n"
    "# cannot be green by construction.\n"
    "#\n"
    "# RAISED 149 \u2192 150 on 2026-10-26 by PR #10053 (#4282 Phase 2.2 \u2014 the four merges as\n"
    "# dispatchers). The three NEW public methods are `link_entities`, `update_knowledge`\n"
    "# and `delete_knowledge`, and every one of them is a row of the owner's 40\n"
    "# (`tortoise/__all__.py`, owner ruling 2026-09-21, approval record\n"
    "# `docs/product/canonical-mcp-tools.md` \u2014 unchanged by this raise). So the approved\n"
    "# list did NOT grow and the 138-name migration backlog did NOT grow: what grew is the\n"
    "# IMPLEMENTATION of three names the owner already approved, which is exactly what\n"
    "# #4282 Phase 2 is for. The arithmetic is 149 + 3 \u2212 2: `main` retired `graph_count`\n"
    "# (now the internal `_graph_count`) and `org_list` in PR #10012 while this branch was\n"
    "# open, and neither was ever an approved name, so the backlog is unchanged at 138.\n"
    "# The alternative \u2014 land the dispatchers only once the narrow\n"
    "# methods they supersede are retired \u2014 would make this phase wait on Phase 3 and is\n"
    "# why the pin is raised HERE, explicitly, rather than silently by a re-cut (which\n"
    "# carries a pin forward by design and can never raise it).\n"
    "#\n"
    "# \u26a0\ufe0f A re-cut emits ONLY this header, then the `yaml.safe_dump` of the derived document.\n"
    "# YAML dumps no comments, so any comment below this block is SILENTLY DROPPED. Keep\n"
    "# rationale here or in the docs, never in an ad-hoc comment in the body.\n"
    "#\n"
    "# LOWERED 82 \u2192 76 on 2026-11-04 by PR #10906 (#4282 Phase 3.2, the first tool batch:\n"
    "# the five `run_onboarding` tools deleted), then 76 \u2192 72 by #10914 (the `graph_overview`\n"
    "# container, five advertised names retired into one). The pin is lowered for the same\n"
    "# reason it is raised elsewhere \u2014 it is only meaningful at the live surface: left at 82\n"
    "# it would carry ten slots of slack, and left at 76 four, so re-adding one of the retired\n"
    "# names (the regression `tests/test_tool_registry.py` pins) would pass\n"
    "# `tools/surface-guard.py` green. \u26d4 72 IS DELIBERATE, NOT AN ANOMALY: if you are\n"
    "# reading `tools: 72` next to a live 72 and wondering whether to \"restore\" 82 or 76, do\n"
    "# not \u2014 that re-opens the slack. #10906 and #10914 compose because their retired name\n"
    "# sets are disjoint (77 \u2212 5 = 72 advertised at #10914's head).\n"
    "#\n"
    "# THE BASELINE BELOW IS A FREEZE OF WHAT EXISTS, NOT THE APPROVED TARGET. It exists so\n"
    "# drift is visible (tools/surface-guard.py). The approved TARGET is recorded in\n"
    "# docs/product/canonical-mcp-tools.md and docs/product/beta-sdk-surface.md; compliance\n"
    "# with it is checked by tools/surface-target-check.py, which reads those docs rather than\n"
    "# a constant here.\n"
    "#\n"
    "# `cut_at_commit` IS a DEFAULT-BRANCH COMMIT, never a lane one (#11057 review F1). It names\n"
    "# the `main` commit this baseline was cut AGAINST, so `cut --commit <sha-in-main>` — not the\n"
    "# worktree HEAD it happened to run in. This repo squash-merges and deletes the source\n"
    "# branch, so a lane SHA resolves in the PR's own CI (which fetches every head) and exists\n"
    "# NOWHERE once it lands; a required provenance check that accepted one would red `main`\n"
    "# permanently. `check` therefore requires the record to be an ANCESTOR of the default\n"
    "# branch, not merely present in the clone.\n"
    "#\n"
    "# ⛔ ONE ROW ONLY. A forward merge can leave this key TWICE (the merge of `origin/main` did\n"
    "# exactly that: this branch's line and #10914's sat adjacent) and a YAML loader SILENTLY\n"
    "# last-wins — the file then reads as ONE value while the diff shows two. The value must be a\n"
    "# commit of `main`: measured with `gh api repos/<owner>/<repo>/compare/<sha>...main`,\n"
    "# `status=ahead, behind=0` is an ancestor and `status=diverged` is a lane commit that will\n"
    "# exist nowhere once its branch is deleted (a lane SHA still RESOLVES today, so a check that\n"
    "# only asks that question calls it fine — which is why this record also asks about ancestry).\n"
)


class SurfaceEvidenceUnreadable(Exception):
    """The evidence a check needs could not be read — a failure, never a skip.

    The sibling `tools/sdk_surface.py` refuses in exactly this contract. This tool used
    to raise a bare `FileNotFoundError` / `yaml` traceback out of `open()`, which is a
    non-zero exit that says nothing about which evidence was missing — and, from `cut`,
    a way to overwrite the baseline having read none of it.
    """


def _refuse(exc: SurfaceEvidenceUnreadable) -> int:
    print(f"::error::{exc}")
    print(f"REFUSED {exc}")
    return 1


def commit_present(sha: str, root: Path | None = None) -> bool | None:
    """Whether `sha` resolves to a commit in THIS checkout — `None` when it cannot say.

    Three answers, and the third is the one that matters: `True` (the object is here),
    `False` (the object database is complete and does not have it) and `None` (this
    checkout cannot answer — a SHALLOW clone only knows the commits inside its depth).

    ⛔ `False` must never be reported for a shallow checkout. `actions/checkout@v4`
    defaults to `fetch-depth: 1`, so every CI job that does not ask for history is
    shallow by construction, and an ancestor commit is simply absent from it — reading
    that absence as "the recorded commit does not exist" would red every PR for a
    correct record. The remedy for `None` is a deeper checkout, not a verdict.

    `GIT_NO_LAZY_FETCH` keeps a PARTIAL (promisor) clone from trying the network inside
    a lint: without it, `git cat-file -e` on an absent object asks the remote and the
    failure arrives as `fatal: remote error: upload-pack: not our ref` — a transport
    error that would be read as a verdict about the record.
    """
    where = ROOT if root is None else root
    env = _GIT_ENV
    try:
        probe = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=where, capture_output=True, text=True, env=env,
        )
    except (OSError, ValueError):  # no git binary, or an unusable argument
        return None
    if probe.returncode == 0:
        return True
    # ONE shallow probe, shared with `on_default_branch` — two readers that disagreed about
    # what "shallow" means would be one gate with a gap.
    shallow = is_shallow(root)
    if shallow is None:
        return None  # not a git work tree at all: this checkout cannot answer either
    return None if shallow else False


def is_shallow(root: Path | None = None) -> bool | None:
    """Whether this checkout has truncated history — `None` when it is not a git work tree.

    One question, one implementation: `commit_present` and `on_default_branch` both need it,
    and a shallow repository can answer NEITHER. `git merge-base --is-ancestor` does not
    complain about truncated history — it walks what it has and exits 1, which would be read
    as "not an ancestor" and red a record that is correct.
    """
    where = ROOT if root is None else root
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=where, capture_output=True, text=True, env=_GIT_ENV,
        )
    except (OSError, ValueError):
        return None
    if probe.returncode != 0:
        return None
    return probe.stdout.strip() == "true"


def default_branch_ref(root: Path | None = None) -> str | None:
    """The ref this repository's history is DURABLE in — or `None` when it is not here.

    `origin/main` first, because CI checks out a PR head and a local `main` need not exist;
    then `main` (the push-to-main run, and the common local clone); then the remote HEAD, so
    a repo whose default branch is not called `main` still answers. No answer is a refusal
    (`None`), never a guess about which branch is the default one.
    """
    where = ROOT if root is None else root
    for ref in ("refs/remotes/origin/main", "refs/heads/main", "refs/remotes/origin/HEAD"):
        try:
            probe = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", ref],
                cwd=where, capture_output=True, text=True, env=_GIT_ENV,
            )
        except (OSError, ValueError):
            return None
        if probe.returncode == 0 and probe.stdout.strip():
            return ref
    return None


def on_default_branch(sha: str, root: Path | None = None) -> bool | None:
    """Whether `sha` is IN the default branch's history — `True` / `False` / `None`.

    #11057 review F1: RESOLUTION IS NOT SURVIVAL. `commit_present` answers "is the object
    here", and on a PR checkout it IS here because the checkout fetches every head — so a
    lane commit resolves right up to the moment it lands. This repo squash-merges
    (`.mergify.yml::merge_method: squash`, `delete_branch_on_merge: true`), so the lane SHA
    is never created on `main` and its only ref is then deleted: every later clone fails to
    resolve it, and a gate that requires resolution reds `main` permanently.

    The question that survives the merge is ANCESTRY — a squash-merged lane commit is
    neither present nor an ancestor, while a `main` commit stays an ancestor forever.

    `None` (cannot answer) for a shallow checkout, a missing default-branch ref, or a ref that
    is BEHIND the record: truncated history makes `merge-base --is-ancestor` exit 1 rather than
    complain, and a false "not on main" would red a correct record.

    ⛔ #11057 review P2: `exit 1` is NOT by itself proof of absence. A remote-tracking ref is
    only as fresh as the last fetch, so on a reviewer's clone whose `origin/main` lags, a commit
    that IS on `main`'s tip answers 1 here — and the caller renders that as "lives only on a lane
    branch … correct the field", i.e. it tells a reader to hand-edit a CORRECT record. The
    discriminator is the reverse question: if the ref is an ancestor of the record, the record is
    AHEAD of the ref, which is equally consistent with a stale ref and with an unmerged lane
    commit. That is ambiguous, so it is `None` (`git fetch` and re-run), never `False`.
    """
    if is_shallow(root) is not False:
        return None
    if commit_present(sha, root) is None:
        return None
    ref = default_branch_ref(root)
    if ref is None:
        return None
    where = ROOT if root is None else root
    try:
        probe = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, ref],
            cwd=where, capture_output=True, text=True, env=_GIT_ENV,
        )
    except (OSError, ValueError):
        return None
    if probe.returncode == 0:
        return True
    if probe.returncode != 1:
        return None
    # Ahead of the ref ⇒ the ref is stale or the commit is unmerged. Both answer "fetch first".
    try:
        behind = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ref, sha],
            cwd=where, capture_output=True, text=True, env=_GIT_ENV,
        )
    except (OSError, ValueError):
        return None
    return None if behind.returncode == 0 else False


# A commit sha, not a revision expression. Deliberately hand-rolled rather than `re`: the
# pinned copy of this gate (tools/surface-guard.py) may not import beyond its allow-list, and
# two copies that share one predicate must not disagree about it.
_HEX = frozenset("0123456789abcdef")


def _is_object_id(sha: str) -> bool:
    """Whether the recorded value is an OBJECT ID — not merely a valid revision.

    ⛔ #11057 review P1: `cat-file -e <rev>` and `merge-base --is-ancestor <rev> <ref>` accept
    ANY revision expression, so a REF NAME (`main`, `origin/main`, `HEAD~1`, a tag) answers BOTH
    provenance questions "yes": the object resolves, and a branch is always an ancestor of
    itself. `provenance_report` then returns no problem while naming no commit at all, and
    `cmd_render` publishes `Cut against main` to `docs/product/mcp-sdk-surface.md`. It is
    reachable honestly — `cmd_cut` writes `--commit` verbatim. So the field is refused unless it
    is a hex OID: that is the one shape whose answer cannot move under the record.

    ⛔ #11057 review P2 — the FULL id, never a 7-39 char PREFIX, and ONE predicate everywhere.
    This used to accept 7-40 while the RESOLUTION probe (`cut_commit_verdict`) required exactly
    40, so the two disagreed about the same string in three artifacts: `cut --commit af36e92`
    passed this gate, `build_doc` wrote it verbatim, and the required
    `surface_manifest.py check --provenance-strict` then refused the baseline as naming a commit
    that "does not exist" — about a commit that resolves and is an ancestor of `main`. An
    abbreviation is not the narrow shape: `gitrevisions(7)` resolves a prefix only while it is
    UNIQUE, so the same 7 hex can name a different object after the repository grows — the
    record's answer would move underneath it, which is what "a commit, not a revision" exists to
    forbid. The full object id has no defender of the abbreviated form: `cut` records
    `git rev-parse HEAD`, which is always 40.

    The SIBLING COPY in `tools/surface-guard.py` must stay this same predicate (it is pinned
    from the base ref, so it cannot import this one).
    """
    return len(sha) == 40 and all(c in _HEX for c in sha)


def _unprovable(sha: str, question: str) -> str:
    """The one wording for "this checkout cannot answer" — a remedy, never a verdict."""
    return (
        f"`cut_at_commit` {sha} is UNPROVABLE in this checkout (shallow or partial "
        f"clone): it cannot answer whether the commit {question}. Not a verdict on the "
        "record — re-run with full history (`git fetch --unshallow`, or `fetch-depth: 0`) "
        "to check it"
    )


def _unprovable_ancestry(sha: str, ref: str) -> str:
    """The ancestry arm's "cannot answer", naming BOTH causes and their two remedies.

    Two different states produce this `None` and they are not fixed the same way: a truncated
    clone needs `--unshallow`, while a remote-tracking ref that is merely BEHIND the record needs
    `git fetch origin`. Naming only the shallow one is how #11057 review P2's false red got read
    as a defect in a CORRECT record.
    """
    return (
        f"`cut_at_commit` {sha} is UNPROVABLE in this checkout: it cannot answer whether the "
        f"commit is in the default branch's history (`{ref}`). Either history is truncated "
        "(shallow or partial clone), or the ref is only as fresh as the last fetch and is "
        f"BEHIND the record — `{ref}` being an ancestor of it is ambiguous between the two, "
        "never proof of absence. Not a verdict on the record — `git fetch origin` (and "
        "`git fetch --unshallow` if shallow) and re-run to check it"
    )


def provenance_report(
    doc: dict, *, resolve=None, reachable=None, strict: bool = False,
) -> tuple[list[str], str | None]:
    """#11057 reviews P2 and F1: the recorded `cut_at_commit` must RESOLVE **and SURVIVE**.

    Two questions, both required, because either alone is green over a record that is
    already broken: RESOLUTION is what a PR checkout can answer (and it answers `True` for a
    lane SHA, because the checkout fetched every head), while ANCESTRY is what `main` can
    still answer after this branch is squash-merged and deleted.

    The verdict must also say which question it answered.

    `cut_at_commit` is excluded from the drift comparison (`NON_DERIVABLE_DOC_KEYS`) and
    `_read_manifest` only asserts it is a non-empty string, so a baseline whose recorded
    commit exists NOWHERE passed every gate for eight review rounds while `cmd_render`
    published the uncheckable sha to `docs/product/mcp-sdk-surface.md` and the owner read
    it. Measured at `3a95ee6b9`: `9542024cb…` was absent from this object database AND
    from the remote (`upload-pack: not our ref`), while the value it replaced resolved —
    the mechanism is `cut` writing `git rev-parse HEAD` in a lane worktree whose head was
    then rebased away (`cut` is the one documented human step).

    Returns `(problems, unprovable)`: an unresolvable commit in a COMPLETE checkout is a
    problem (red), and a checkout that cannot answer yields no problem plus the reason, so
    the caller can print it as a remedy instead of pretending it checked (external
    practice: a recorded generating commit is evidence only when independently
    resolvable; truncated history is ambiguous, never proof of absence).

    `strict=True` inverts the second arm: there, UNPROVABLE is itself the problem. That is
    for a step that guarantees its own history, so it fails CLOSED — a gate that quietly
    degrades to a notice when someone drops `fetch-depth: 0` is a no-op gate.
    """
    sha = str(doc.get("cut_at_commit") or "")
    if not sha:
        # ⛔ DO NOT "unify" this with `tools/surface-guard.py::_provenance_problems`, which
        # REDS on absence (#11057 review FINDING 1). The deferral is correct HERE only
        # because every caller reaches this function through `_read_manifest`, which refuses
        # an absent/empty field before this line runs. The guard has no such caller and is
        # the copy a PR cannot edit, so there the absence IS the defect — a pin that defers
        # the presence half of its own rule to the un-pinned copy enforces nothing.
        return [], None  # its SHAPE is `_read_manifest`'s refusal, one verdict
    if not _is_object_id(sha):
        # #11057 reviews P1 + P2: a REF NAME is a valid revision, so both probes answer "yes"
        # for it and this function would verify a record that names no commit; and an
        # ABBREVIATION passes every probe while the resolution arm below calls it fabricated,
        # which is how the answered questions disagreed. Both are clone-shape independent, so
        # both are PROVEN defects in both arms (never an "unprovable").
        return [
            f"`cut_at_commit` {sha!r} is not an OBJECT ID (a full 40-char lowercase hex "
            "commit sha). Any other shape names no commit this record can be verified "
            "against: a ref resolves and is its own ancestor, and an abbreviation's meaning "
            "moves as the repository grows (git resolves a prefix only while it is unique). "
            "Record the full commit id: `cut --commit <full-sha-in-main>`, never a ref name "
            "or a short prefix"
        ], None
    # ⛔ ONE resolution implementation (`cut_commit_verdict`), not a second ad-hoc probe:
    # two probes that disagree about what "cannot answer" means is one gate with a gap.
    probe = cut_commit_resolves if resolve is None else resolve
    reach = on_default_branch if reachable is None else reachable

    def _cannot_answer(unprovable: str) -> tuple[list[str], str | None]:
        if strict:
            return [f"{unprovable} — and this step requires a checkout that CAN answer"], None
        return [], unprovable

    present = probe(sha)
    if present is None:
        return _cannot_answer(_unprovable(sha, "resolves"))
    if present is False:
        return [
            f"`cut_at_commit` {sha} does not resolve to a commit in this repository — the "
            "baseline records a provenance commit that does not exist, so no reader can "
            "verify it. Re-cut at a commit in the default branch's history "
            "(`cut --commit <sha>`), or correct the field explicitly to one that resolves"
        ], None
    in_history = reach(sha)
    if in_history is None:
        return _cannot_answer(
            _unprovable_ancestry(sha, default_branch_ref() or "the default branch")
        )
    if in_history is False:
        ref = default_branch_ref() or "the default branch"
        return [
            f"`cut_at_commit` {sha} resolves here, but it is NOT in {ref}'s history — it "
            "lives only on a lane branch, and this repo squash-merges and deletes that "
            "branch on merge, so the commit will exist NOWHERE the moment this lands and "
            "this property will red `main` permanently. The field names the default-branch "
            "commit the baseline was cut AGAINST, not the lane commit it was cut at: "
            "`cut --commit <sha-in-main>`"
        ], None
    return [], None


#: `cut_commit_verdict`'s answers. `UNPROVABLE` is NOT a pass — the reader that gets it
#: says so out loud (`cmd_check` prints it); it is the arm for a clone that cannot answer.
CUT_RESOLVED = "resolved"
CUT_FABRICATED = "fabricated"
CUT_UNPROVABLE = "unprovable"

#: What git says when a PROMISOR REMOTE (`blob:none`, the fleet's default clone) was
#: asked for an object and answered that it does not have it. That is a DISPROOF, not a
#: fetch failure: the remote replied. Without this arm a partial clone — which is most
#: of the fleet's checkouts — could only ever answer UNPROVABLE.
#: ⛔ Deliberately ONLY the two messages in which the REMOTE states it has no such ref.
#: git also says "could not read <sha>" for an object missing from a promisor clone's
#: LOCAL store, which proves nothing about whether the commit exists — treating it as a
#: disproof would red a healthy tree, which is the failure mode this arm exists to avoid.
_REMOTE_HAS_NO_SUCH_OBJECT = re.compile(
    r"not our ref|couldn't find remote ref", re.IGNORECASE)


def _git(*args: str) -> tuple[int, str, str]:
    """(rc, stdout, stderr) for one `git` run rooted at ROOT; rc 127 when git is absent."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        )
    except OSError as exc:  # no git binary, or no repository to run it in
        return 127, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def cut_commit_verdict(sha: str) -> tuple[str, str]:
    """(verdict, why) for a `cut_at_commit` value — `resolved` / `fabricated` / `unprovable`.

    `cut_at_commit` is PROVENANCE and is excluded from the drift comparison
    (`NON_DERIVABLE_DOC_KEYS`), so this is the ONLY thing that constrains it. Before this
    function it was constrained by nothing: `_read_manifest` asserted a non-empty string,
    so a value naming no object at all passed `check`, `render`, `cut` and
    `tools/surface-guard.py` — and `cmd_render` slices the value to nine characters for
    the document's header, so the corruption was invisible in the rendered doc (measured
    on #10914: `911a1e240c16c37a1a8e0f2bc46dba9dd8ae2a90`, an abbreviation whose 30-char
    tail had been invented; the real merge commit is
    `911a1e240c2415b094b370f32a2a24f88a5fb1ea`).

    Three verdicts, because a checkout that does not hold the object cannot distinguish
    "fabricated" from "not fetched" — and turning that absence into a refusal would red
    the required `surface-guard` job (default `fetch-depth: 1`) on a HEALTHY tree, since
    `cut_at_commit` names an ancestor of the head rather than the head itself:

    * `resolved`   — the object is a commit THIS clone holds. Provenance carried.
    * `fabricated` — the value is not a full 40-char lowercase sha, OR the object is absent
      and its absence is PROVEN: either the repository is not shallow (nothing is missing
      for it to hide in), or a promisor remote was asked and answered that it has no such
      object. A fabricated provenance is a refusal.
    * `unprovable` — git is unavailable, or this clone is shallow and no remote answered.
      NOT a pass, and never silent: `cmd_check` prints the arm it took.
    """
    # ⛔ ONE shape predicate, not two (#11057 review P2): this arm has its own history — an
    # inline 40-char regex — while `cmd_cut`'s write gate and `provenance_report`'s arm used
    # `_is_object_id`'s 7-40. `cut` records `git rev-parse HEAD`, so an abbreviated,
    # upper-cased or invented value never came from the generator, and a prefix is refused by
    # both halves now: the write gate will not write one and this will not grade one.
    if not _is_object_id(sha):
        return CUT_FABRICATED, (
            f"{sha!r} is not a full 40-character lowercase commit sha — `cut` records "
            "`git rev-parse HEAD`, which always is one, and an abbreviation only resolves "
            "while it stays unique in this repository"
        )
    rc, _, stderr = _git("cat-file", "-e", f"{sha}^{{commit}}")
    if rc == 0:
        return CUT_RESOLVED, ""
    if rc == 127:
        return CUT_UNPROVABLE, f"git is not runnable here ({stderr.splitlines()[-1:] or ['']})"
    if _REMOTE_HAS_NO_SUCH_OBJECT.search(stderr):
        return CUT_FABRICATED, (
            "`git cat-file` asked the remote for it and the remote answered that it has no "
            f"such object — git said: {stderr.splitlines()[0] if stderr else '(no output)'}"
        )
    rc_shallow, shallow, _ = _git("rev-parse", "--is-shallow-repository")
    if rc_shallow == 0 and shallow == "true":
        return CUT_UNPROVABLE, (
            "this clone is SHALLOW, so the object may simply not have been fetched (git "
            f"said: {stderr.splitlines()[0] if stderr else '(no output)'})"
        )
    return CUT_FABRICATED, (
        "this clone is not shallow and holds no such commit, so nothing is missing for it "
        f"to be hiding in (git said: {stderr.splitlines()[0] if stderr else '(no output)'})"
    )


def cut_commit_resolves(sha: str) -> bool | None:
    """`cut_commit_verdict` as the lint's three-valued RESOLUTION probe.

    ONE implementation of "does a `cut_at_commit` resolve here, and can this clone even
    answer?" (#11057's merge with #10914): before it there were two, and they disagreed.

    * the READER (`_read_manifest`) refuses only `fabricated` — an absence that is PROVEN;
    * this lint's resolution arm must red an unresolvable record and stay silent on a
      clone that cannot answer, so it needs the third value: `UNPROVABLE` → `None`.

    Reading the verdict rather than re-probing keeps the two from drifting apart, and it is
    what makes a stubbed `_git` control BOTH paths (a test that stubs only one of two
    implementations pins neither: measured — `test_a_shallow_clone_that_cannot_answer_is_
    UNPROVABLE_and_NEVER_SILENT` red on the merged tree because the lint's own probe reached
    the real remote while the reader's was stubbed).
    """
    verdict, _why = cut_commit_verdict(sha)
    if verdict == CUT_RESOLVED:
        return True
    if verdict == CUT_UNPROVABLE:
        return None
    return False

def _read_manifest(path: Path | None = None, *, check_provenance: bool = True) -> dict:
    """Read the frozen baseline, or refuse. Never returns a partial document.

    `path` is resolved at CALL time (never bound as a default), so a caller that
    redirects `MANIFEST_FILE` — the tests do, and so would any future tool — reads the
    file it redirected to. A default argument bound at import time silently read the
    original, which is how a redirection can look effective and verify nothing.

    `check_provenance=False` is for `cut` ONLY: a re-cut is the REPAIR path for a
    `cut_at_commit` that resolves to nothing, so refusing to read the baseline there
    would refuse the only way to fix it. Every gating reader keeps the default.
    """
    if path is None:
        path = MANIFEST_FILE
    if not path.exists():
        raise SurfaceEvidenceUnreadable(
            f"the frozen surface baseline is missing at {path}. The tool cannot verify "
            "(or re-cut) the surface without it; a check that cannot read its evidence "
            "must not report success."
        )
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # any read/parse failure is a refusal
        raise SurfaceEvidenceUnreadable(f"could not read {path}: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        raise SurfaceEvidenceUnreadable(
            f"{path} is malformed: expected a mapping carrying a `rows:` list."
        )
    if doc.get("retired") is not None and not isinstance(doc.get("retired"), list):
        raise SurfaceEvidenceUnreadable(
            f"{path} is malformed: `retired:` must be a list (got "
            f"{type(doc.get('retired')).__name__})."
        )
    # `cut_at_commit` is PROVENANCE, not a derived value (`cut` records the commit it ran
    # at), so it is excluded from the drift comparison — which makes this shape check the
    # ONLY thing that pins it. It lives HERE rather than in one consumer because
    # `cmd_render` slices it for the document's header: a hand-edited `cut_at_commit: null`
    # escaped as `TypeError: 'NoneType' object is not subscriptable` out of the renderer
    # while `cmd_check` reported the missing value as a mere problem — two readers, two
    # opinions, and one of them a traceback.
    if not isinstance(doc.get("cut_at_commit"), str) or not doc["cut_at_commit"]:
        raise SurfaceEvidenceUnreadable(
            f"{path} records no `cut_at_commit`: it must name the DEFAULT-BRANCH commit the "
            "baseline was cut against, as a non-empty string. Re-cut the baseline."
        )
    # A FABRICATED record is refused HERE, by every reader; a clone-dependent
    # question is not. `cut_commit_verdict` answers `fabricated` only when the absence
    # is PROVEN (not a 40-hex sha, or a non-shallow clone with no such object, or a
    # promisor remote that answered it has none) — so this arm can never red a healthy
    # tree for a record that is merely unfetched. RESOLUTION and ANCESTRY beyond that
    # are `provenance_report`'s question, asked from `cmd_check`: a shallow CI checkout
    # cannot answer them, and a refusal raised from the reader there would red
    # `render`/`guard` for a record they do not own (`check` is the lint that owns
    # provenance, and `--provenance-strict` is where "cannot answer" fails closed).
    if check_provenance:
        verdict, why = cut_commit_verdict(doc["cut_at_commit"])
        if verdict == CUT_FABRICATED:
            raise SurfaceEvidenceUnreadable(
                f"{path} records `cut_at_commit: {doc['cut_at_commit']}`, which is NOT a commit "
                f"this record can be verified against ({why}). The field is PROVENANCE and is "
                "excluded from the drift comparison, "
                "so this check is the only gate on it — and a value naming no object is a "
                "fabricated provenance, not a stale one: restore the commit the baseline was "
                "actually cut at, or re-cut the baseline."
            )
    # A DUPLICATE NAME is unverified content, not a harmless repetition. Every comparison
    # in this file — here and in tools/surface-guard.py — keys rows by NAME, so a second
    # row with an existing name is silently DROPPED: a doctored duplicate inserted before
    # the true one left the drift check green, the rendered document claiming a fabricated
    # `class`, and the guard green too (verified adversarially). Reject it as malformed
    # evidence; a name set cannot show it, so it must be refused before any comparison.
    for field in ("rows", "retired"):
        names = [str(r.get("name")) for r in doc.get(field) or [] if isinstance(r, dict)]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise SurfaceEvidenceUnreadable(
                f"{path} carries duplicate `{field}` name(s): {duplicates[:5]} "
                f"({len(names) - len(set(names))} row(s) over). A name-keyed comparison drops "
                "all but the last, so the duplicate is UNVERIFIED content. Re-cut the baseline."
            )
    return doc


CANONICAL_DOC = ROOT / "docs" / "product" / "canonical-mcp-tools.md"


def _frontmatter(path: Path) -> dict:
    """The YAML frontmatter of a product doc, or raises when it carries none
    (missing / unclosed / non-mapping / unparseable).

    Deliberately NOT a `{}` fallback: a gate that cannot read its evidence must
    not report success, and an empty mapping reads as "nothing to check".
    """
    if not path.exists():
        raise SurfaceEvidenceUnreadable(
            f"{path} is missing — the owner's approval lives there, and a check that "
            "cannot read its evidence must not report success."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SurfaceEvidenceUnreadable(f"could not read {path}: {exc}") from exc
    if not text.startswith("---"):
        raise SurfaceEvidenceUnreadable(
            f"{path} carries no YAML frontmatter — the owner's approval is recorded there, "
            "and a document whose approval cannot be read is not an approval record. "
            "Restore the frontmatter rather than letting the approval silently degrade."
        )
    end = text.find("\n---", 3)
    if end < 0:
        raise SurfaceEvidenceUnreadable(
            f"{path} opens a frontmatter block that is never closed — its approval cannot "
            "be read."
        )
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError as exc:
        raise SurfaceEvidenceUnreadable(
            f"could not parse the frontmatter of {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise SurfaceEvidenceUnreadable(
            f"{path}'s frontmatter is not a mapping — its approval cannot be read."
        )
    return data


def read_approval(path: Path | None = None) -> dict:
    """The owner's approval — READ from `docs/product/canonical-mcp-tools.md`.

    OVERRIDES: the frozen-snapshot-as-approval-baseline practice — a snapshot of the
    implementation cannot be the approved target; the target is read from
    docs/product/canonical-mcp-tools.md (owner, 27 MCP tools) and from the declared SDK
    surface in tortoise/__all__.py (owner, 40 methods; cross-checked against
    docs/product/beta-sdk-surface.md) — never from a count beside them.

    The manifest used to carry `approval_status: pending-owner-approval` while the
    canonical list already recorded `approval_status: approved`, `approvedBy:
    daniel-ospina` and `approval_pr: 4120`. Two files disagreed about one fact, and the
    gate read the wrong one — so the owner was re-asked for the same approval. Reading
    it here is the fix: the canonical list is the single place the approval is recorded,
    and `cut` copies it into the manifest.

    `path` is resolved at CALL time so a test can point it at a fixture doc.
    """
    if path is None:
        path = CANONICAL_DOC
    fm = _frontmatter(path)
    # FAIL CLOSED ON A MISSING `approval_status`: synthesising the pending default here
    # would let a doc whose approval provenance was deleted re-cut the manifest to
    # `pending`, which the guard then PASSES — a green gate over vanished evidence. The
    # document must say what its approval is; absence is a refusal.
    if not fm.get("approval_status"):
        raise SurfaceEvidenceUnreadable(
            f"{path} records no `approval_status`, so it is not an approval record. "
            "The manifest's approval is READ from this document; absence must not be "
            "silently treated as `pending-owner-approval`."
        )
    pr = fm.get("approval_pr")
    approved = fm.get("approved")
    status = str(fm["approval_status"])
    # THE SAME VOCABULARY THE CONSUMERS ENFORCE. `surface-guard.py` reds an unknown status;
    # if the generator merely carried it through, `cut` would write a manifest its own gate
    # reds while `check` stayed green.
    if status not in ("pending-owner-approval", "approved"):
        raise SurfaceEvidenceUnreadable(
            f"{path} records `approval_status: {status}` — not one of "
            "'pending-owner-approval' or 'approved'. The manifest's approval is READ from "
            "this document, so an unrecognised status must not be carried into it."
        )
    # FAIL CLOSED ON AN INCOMPLETE `approved`. `surface-guard.py` and
    # `tools/surface-target-check.py` both refuse an `approved` with no principal and no PR;
    # if `read_approval` did not, `cut` could write a manifest that its own gate reds while
    # `check` (which compares derived vs committed) stayed green — the generator disagreeing
    # with its consumers about what "approved" means. `pending-owner-approval` legitimately
    # carries neither, so the requirement is on `approved` only. TRUTHINESS, not `is None`:
    # an empty-string `approval_pr` is falsy to both consumers, so it must be here too.
    # THIS reader raises where `surface-guard.py` reports the missing fields and
    # `surface-target-check.py` also refuses — all three fail closed; only the reporting
    # differs (this one must refuse to write, the gate must name the field).
    if status == "approved" and (not fm.get("approvedBy") or not pr):
        raise SurfaceEvidenceUnreadable(
            f"{path} records `approval_status: approved` but "
            f"approvedBy={fm.get('approvedBy')!r} / approval_pr={pr!r} — an approval "
            "record must carry a principal and a PR."
        )
    return {
        "status": status,
        "principal": str(fm["approvedBy"]) if fm.get("approvedBy") else None,
        "pr": str(pr) if pr else None,
        "date": str(approved) if approved is not None else None,
    }


# --- what a re-derivation CANNOT reproduce -----------------------------------
# `check` re-derives the baseline from code and reds on any difference, which is what
# makes a hand-edited manifest a failure rather than a fact. These keys are excluded
# from that comparison because their value is not a function of the code alone:
#
#   * `used_by` / `recommendation` / `basis` / `reason` read a MACHINE-LOCAL call log
#     (`~/.tortoise/analytics_fallback.jsonl`): it supplies the `agents` / `never called`
#     prefix of `used_by` and the wording of `reason`, so neither column is reproducible on
#     another checkout — measured with the log absent, 132 `used_by` and 25 `reason` rows
#     differ, and those are the ONLY row keys that do. `recommendation` and `basis` are
#     computed from the same observed-call signal, so they are excluded even where today's
#     rows happen to agree.
#     ⛔ A re-cut does NOT re-derive them: `cut` CARRIES the committed values forward
#     (`_carried_row_evidence`), and the log is consulted only for a row that has no
#     committed evidence or under a deliberate `--refresh-observations` (#10914 review P2).
#     Before that, every `cut` re-stamped these columns from whichever box ran it — measured
#     on #10914, two untouched rows flipped `agents` -> `never called` while a tool the PR had
#     just introduced was stamped `called by agents` off the lane's own test call, and every
#     gate stayed green over it (they are not compared). So the residual is now narrower and
#     explicit: a re-cut preserves the evidence it cannot reproduce, a hand edit or
#     `--refresh-observations` is what moves it, and that edit lands in a reviewed diff.
#   * `approval` is an unvalidated AUDIT reference: a PR number and a principal. NO check
#     validates its shape, and none verifies that the review it names exists — the approval
#     itself is READ from `docs/product/canonical-mcp-tools.md` (#4185). The consent carrier is
#     the #4282 mandate (approval from Daniel FIRST; see tortoise/tool_registry.py,
#     tortoise/sdk.py and CONTRIBUTING.md) and Daniel's review, NOT a machine control. The
#     repository ruleset proposed by the #3863 scope doc was REJECTED by the owner on #4282
#     as over-engineering — do not re-introduce one here as the carrier.
#   * `exemption` is a HUMAN RECORDING too — an owner-approved flag that a row's
#     reachability is deliberately excused (`tools/surface-guard.py` reads it). It was
#     missing from this list while `build_doc` emits `exemption: False` for every row, so
#     recording one — the path CONTRIBUTING documents — made the REQUIRED check red with no
#     way back (`cut` resets it to False forever). Verified: `exemption: true` on an SDK row
#     makes the guard report "1 exempt" and the check FAIL "recorded {True}, derived {False}".
#     `cut` now CARRIES it forward (`CARRIED_TOOL_ROW_KEYS` / `CARRIED_SDK_ROW_KEYS`), so the
#     recording survives a re-cut and `surface-guard.py` — not the re-cut — is what judges it.
#   * `cut_at_commit` is provenance: `cut` records the commit it ran at.
#   * the four top-level `approval_*` keys are deliberately NOT here. They are DERIVED
#     (#4185): `read_approval` reads them from `docs/product/canonical-mcp-tools.md` at
#     cut time, and `check` therefore compares them — a hand-edit that disagrees with the
#     document reds the lint. They were excluded only while the staged flip was pending
#     (#8144 was the second half of the two-PR split); the exclusion is gone with the
#     staging. `tools/surface-guard.py` independently re-reads the same document and reds
#     a manifest that CLAIMS an approval disagreeing with it.
#   * `high_water_counts` is a hand-set GROWTH PIN, not a derivation: `cut` carries it
#     forward and `tools/surface-guard.py` reds when the live surface EXCEEDS it. It is
#     excluded here because it is not a function of the code — it is the ceiling the
#     owner froze, raised only by a deliberate edit in an approving PR.
#
# The exclusion is a DENY-list applied to BOTH sides, so a key the generator grows later is
# compared by default (present on one side only, and ABSENCE is compared as absence — a
# hand-added top-level key whose value is `null` used to match `None` and slip through) and
# reds until it is deliberately classified here.
NON_DERIVABLE_ROW_KEYS = frozenset(
    {"used_by", "recommendation", "basis", "reason", "approval", "exemption"}
)

# WHICH non-derivable row keys a re-cut must CARRY FORWARD verbatim (#10914 review P2).
#
# Being non-derivable (excluded from the drift comparison) is what makes a key safe from
# `check`, not what makes it safe from `cut`. `build_doc` used to RE-DERIVE these from
# `~/.tortoise/analytics_fallback.jsonl` — this box's own call log — so a routine re-cut
# silently rewrote the evidence the owner reads. Measured on #10914: `tortoise_query` and
# `tortoise_search` flipped `agents` -> `never called` (two rows the change never touched,
# because that box's log carried no slice for them) while a tool the lane had just WRITTEN
# was stamped `called by agents` (from the lane's own test call). `check`, `surface-guard`
# and the render-diff all stayed green over it, because these keys are not compared.
#
# So `cut` now carries the committed value forward, exactly as `high_water_counts` and
# `response_fields` are carried: the columns are EVIDENCE the manifest records, refreshed
# only by a deliberate `--refresh-observations`.
#
# `approval` is DELIBERATELY NOT here: `cmd_cut` resets the per-row audit reference on
# purpose and CONTRIBUTING.md makes that reset the control — carrying it would silently
# reverse a recorded decision.
CARRIED_TOOL_ROW_KEYS = ("used_by", "recommendation", "basis", "reason", "exemption")
# An `sdk:` row carries no observed-usage columns, and its `reason` is DERIVED from the
# caller scan (it must follow the code, so freezing it would report a stale call graph
# forever). Only the human recording travels.
CARRIED_SDK_ROW_KEYS = ("exemption",)
NON_DERIVABLE_DOC_KEYS = frozenset(
    {
        "cut_at_commit",
        # NOTE: the four top-level `approval_*` keys are NOT in this set. They are DERIVED
        # from `docs/product/canonical-mcp-tools.md` by `read_approval` (#4185), so `check`
        # compares them — see the block above. They were excluded only while the staged
        # flip was pending; #8144 landed the flip and removed the exclusion.
        # The growth pin (#4185 review P1): carried by `cut`, enforced by the guard.
        "high_water_counts",
        # `response_fields` records hand-authored response FIELDS, outside the gated
        # surface. `build_doc` carries the committed block forward (see
        # `_carried_response_fields`), so a derivation of it is the artifact compared
        # with itself — a no-op that LOOKS like verification. Classified here so the
        # comparison does not pretend to check it; the block's real defences are the
        # non-empty and anchor properties in `cmd_check`.
        "response_fields",
    }
)

# The sentinel for "this key is not in the document at all". `doc.get(key)` cannot express
# absence separately from a `null` value, and the difference is exactly what a hand-added
# top-level `sneaky_key: null` exploited.
_ABSENT = object()

# The four consumer-class values the DERIVATION computes from caller file paths. The
# fifth, `control-plane`, is a judgement (it is about what a method DOES —
# provision or destroy keys, orgs, instances, tenants) and is therefore authored
# and baseline-protected, not derived.
DERIVED_CLASSES = ("agent-reachable", "eval-only", "internal", "no-caller-found")
# The largest always-loaded tool count in the comparable research (Letta 18 · Graphiti 13 ·
# Mem0 9 · Cognee 4). Named because the rendered paragraph compares OUR advertised count
# against it: "Every comparable we studied pins a smaller set" is a comparison, true only
# while we advertise more than this — below it the sentence is false (Letta pins 18).
LARGEST_COMPARABLE = 18
AUTHORED_CONTROL_PLANE = frozenset({"apikey_revoke", "graph_delete"})

# §6.1 item 3. The catch-all clause is what makes this single-valued; the two
# subpath carve-outs are what keep it from being two-valued.
EVAL_PREFIXES = ("battery/", "tools/longmem_eval/", "tools/ask_", "tests/longmem_eval/")
SURFACE_FILES = ("tortoise/mcp_server.py", "tortoise/hosted_api.py", "tortoise/selfhost_api.py")
NON_LEG_SURFACE = ("tortoise/hosted_api.py", "tortoise/selfhost_api.py")
ENGINE_PREFIXES = ("tortoise/",)
DOC_PREFIXES = ("docs/", "skills/")
TEST_PREFIXES = ("tests/",)


def category(path: str) -> str:
    """Classify a caller by file path. Total and single-valued — see §6.1 item 3.

    `tortoise/mcp_server.py` carries a `#tool` / `#other` suffix from `scan_callers`: a
    reference in that file is a HANDLER leg only when it sits inside a function that is a
    registered tool. A `tortoise_*` function that is not in `TOOL_REGISTRY` is not a leg
    (AC11) — it is not a live surface — so its references are engine-internal.
    """
    base, _, ctx = path.partition("#")
    if base == "tortoise/mcp_server.py":
        return "mcp-handler" if ctx == "tool" else "engine"
    if path == "tortoise/__main__.py":
        return "cli"
    if path in NON_LEG_SURFACE:
        return "tenant-rest"
    if path.startswith(EVAL_PREFIXES):
        return "eval-harness"
    if path.startswith(TEST_PREFIXES):
        return "tests"
    if path.startswith(DOC_PREFIXES):
        return "documentation"
    if path.startswith(ENGINE_PREFIXES):
        return "engine"
    return "other-tooling"  # tools/, graph-scripts/, apps/, benchmarks/, ..., and any dir not named above


def tracked_python() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except Exception as exc:  # an unlistable tree is a refusal
        raise SurfaceEvidenceUnreadable(
            f"could not list the tracked python files (`git ls-files *.py`): "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return [line for line in out.stdout.splitlines() if line]


def load_order() -> dict:
    """The ordering/keyword table, or a refusal — never a bare traceback.

    The lint's pass/fail must not be satisfiable by editing the rows it checks, so this
    table is a separate file; that makes it evidence, and evidence that cannot be read is
    a failure (see `SurfaceEvidenceUnreadable`).
    """
    if not ORDER_FILE.exists():
        raise SurfaceEvidenceUnreadable(
            f"the ordering table is missing at {ORDER_FILE}. The lint cannot derive a keyword "
            "or an order without it."
        )
    try:
        table = yaml.safe_load(ORDER_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SurfaceEvidenceUnreadable(f"could not read {ORDER_FILE}: {exc}") from exc
    if not isinstance(table, dict):
        raise SurfaceEvidenceUnreadable(
            f"{ORDER_FILE} is malformed: expected a mapping carrying `keywords:`, `tokens:` "
            f"and `family_rank:` (got {type(table).__name__})."
        )
    required = ("keywords", "tokens", "family_rank", "baseline_counts")
    missing = [k for k in required if k not in table]
    if missing:
        # `family_rank` is required by `build_doc`, not only by this lint: validating a
        # SUBSET let a table missing it pass the read and then escape as a bare KeyError
        # out of the derivation — the traceback the refusal contract exists to prevent.
        raise SurfaceEvidenceUnreadable(
            f"{ORDER_FILE} is malformed: missing {missing}. The derivation cannot order or "
            "label a row without them."
        )
    # KEY PRESENCE IS NOT SHAPE. The checks below exist because a plausible-looking table
    # passed the presence test and then escaped as a bare traceback or produced a WRONG
    # derivation — each measured against a copy of this file:
    #   tokens: []                   -> AttributeError: 'list' object has no attribute 'items'
    #   tokens: {fetch: fetch}       -> iterated the STRING's characters, so every real token
    #                                   fell through and the keyword silently became `operate`
    #   keywords: {}                 -> KeyError: 'fetch'
    #   keywords/family_rank values  -> `TypeError: '<' not supported` when sorted, and string
    #                                   ranks sort LEXICOGRAPHICALLY ('10' < '2')
    #   family_rank: {}              -> KeyError out of `build_doc`
    #   baseline_counts empty/absent -> property 4's `if baseline and ...` short-circuited, so
    #                                   deleting the frozen expectation turned the check OFF
    def _refuse_shape(what: str, why: str) -> SurfaceEvidenceUnreadable:
        return SurfaceEvidenceUnreadable(f"{ORDER_FILE} is malformed: {what} {why}.")

    for key in ("keywords", "family_rank"):
        table_key = table[key]
        if not isinstance(table_key, dict) or not table_key:
            raise _refuse_shape(f"`{key}:` must be a non-empty mapping", f"(got {table_key!r:.120})")
        if any(
            not isinstance(k, str) or not isinstance(v, int) or isinstance(v, bool)
            for k, v in table_key.items()
        ):
            raise _refuse_shape(f"every `{key}:` entry must map a name to an INTEGER", "")
        ranks = sorted(table_key.values())
        if ranks != list(range(1, len(ranks) + 1)):
            # Ranks that are not a clean 1..N let two entries share a rank (an order the
            # emitted list cannot express) or leave a gap (a rank nothing can reach).
            # Nothing else checks this: property 5 compares each row to its OWN stored
            # rank, which is self-consistent under either defect.
            raise _refuse_shape(
                f"`{key}:` ranks must be exactly 1..{len(ranks)}", f"(got {ranks})"
            )
    toks = table["tokens"]
    if not isinstance(toks, dict) or not toks:
        raise _refuse_shape("`tokens:` must be a non-empty mapping", f"(got {toks!r:.120})")
    bad_tokens = [
        k
        for k, v in toks.items()
        if not isinstance(v, list) or not all(isinstance(t, str) for t in v)
    ]
    if bad_tokens:
        raise _refuse_shape(
            "every `tokens:` entry must map a keyword to a LIST of token strings",
            f"(offenders: {bad_tokens[:5]})",
        )
    unknown = sorted(set(toks) - set(table["keywords"]))
    if unknown:
        raise _refuse_shape(
            "every `tokens:` key must be one of the `keywords:`", f"(unknown: {unknown[:5]})"
        )
    counts = table["baseline_counts"]
    if not isinstance(counts, dict) or not counts:
        raise _refuse_shape(
            "`baseline_counts:` must be a non-empty mapping — it is the frozen keyword "
            "distribution the derivation is checked against, and an empty one turns that "
            "check off",
            f"(got {counts!r:.120})",
        )
    if any(
        not isinstance(k, str) or not isinstance(v, int) or isinstance(v, bool)
        for k, v in counts.items()
    ):
        raise _refuse_shape("every `baseline_counts:` entry must map a keyword to an INTEGER", "")
    if set(counts) != set(table["keywords"]):
        raise _refuse_shape(
            "`baseline_counts:` must have exactly one entry per `keywords:` entry — a keyword "
            "with no frozen count is a keyword whose distribution nothing checks",
            f"(diff: {sorted(set(counts) ^ set(table['keywords']))[:5]})",
        )
    return table


def derive_keyword(name: str, description: str, tokens_to_keyword: dict[str, str]) -> str:
    """Leftmost token of the tool name that is present in the ORDER FILE's token
    table; else the first description word that is present; else `operate`.

    Implemented exactly as config/surface-order.yml states. A token absent from
    the table is as if it were not there — that is the whole rule.
    """
    for token in (t for t in name.split("_") if t != "tortoise"):
        if token in tokens_to_keyword:
            return tokens_to_keyword[token]
    for raw in description.split():
        word = raw.strip(".,;:()[]`'\"").lower()
        if word in tokens_to_keyword:
            return tokens_to_keyword[word]
    return "operate"


def _walk_with_functions(node, stack):
    """Yield (node, enclosing-function-name chain) so a call site can be attributed."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        stack = [*stack, node.name]
    yield node, stack
    for child in ast.iter_child_nodes(node):
        yield from _walk_with_functions(child, stack)


# ── the caller scan's parse cache (#9017) ───────────────────────────────────
# `scan_callers` is the whole cost of a derivation: it reads and parses every
# tracked `.py` file (1,706 of them; measured 2026-10-18 — 16.8-18.6s CPU of a
# ~19s `build_doc()` on this host). A derivation is cheap to REPEAT once those
# parses are cached, and repeating it is exactly what the suite does:
# `tests/test_surface_manifest.py` spends ~103s of its 245s CPU in SIX tests that
# each ask for a REAL derivation — the ones that prove the derivation itself
# (#4192's served-default tamper, #4185's high-water pin, `cmd_cut`'s wiring),
# and which therefore CANNOT be pointed at a canned dict. Memoizing changes
# nothing for the shipped tool (one process, one derivation); it exists so the
# SECOND and later in-process derivation is not a second full tree walk.
# Measured on the file: 245s CPU before, 181s after, same host and load.
#
# MEMORY, stated so nobody has to discover it: the cache holds every parsed
# file's attribute->stacks map for the life of the process (~265k stack lists
# over ~60k attribute names), which is ~120 MB at peak in a full derivation.
# That is acceptable because this tool is a CLI/CI gate — nothing under
# `tortoise/` imports it — and the alternative is re-parsing 1.7k files.
#
# Keyed on `(st_mtime_ns, st_size)`, not on the content: a file edited under a
# running process is re-parsed, and an unreadable one still refuses on the first
# attempt. That key is a HEURISTIC, not a proof — a same-size write landing on
# the same mtime tick, or a restored timestamp (`os.utime`, `rsync -t`), would
# serve the previous parse. No consumer in this repo re-derives after mutating a
# tracked source file (the suite patches `build_doc` or redirects the MANIFEST,
# never the tree), so it cannot bite today; callers that ever need a guarantee
# must drop the cache entry (or the process) themselves.
_PARSE_CACHE_KEY = "_surface_manifest_parse_cache"


def _parse_cache() -> dict[str, tuple[tuple[int, int], dict[str, list[tuple[str, ...]]]]]:
    """The process-wide parse cache.

    Deliberately NOT a module global. The suite loads this file as a module per
    call (`importlib.util.spec_from_file_location(...); exec_module(...)` in
    `tests/test_surface_manifest.py::_load_manifest_tool`), so a module-level
    dict is re-created for EVERY test and the cache never hits — measured, that
    is what the first cut of this change did: the file's session CPU did not
    move (245s before, 273s after, same load). A `sys` attribute survives
    re-execution, so it is the object with the lifetime this cache is for.
    Do not "tidy" it back into a module global.

    Scoped by `str(ROOT)` so two checkouts in one process cannot serve each
    other's parses.
    """
    per_process = getattr(sys, _PARSE_CACHE_KEY, None)
    if per_process is None:
        per_process = {}
        setattr(sys, _PARSE_CACHE_KEY, per_process)
    return per_process.setdefault(str(ROOT), {})


def _attributes_with_stacks(rel: str) -> dict[str, list[tuple[str, ...]]]:
    """`attribute name -> every enclosing function chain it appears under`, per file.

    The per-file half of `scan_callers`, memoized. Returns an empty dict for a
    file that does not parse, which is what the scan does with it. Keyed by
    attribute so a caller can look up the ~190 method names it cares about
    instead of walking every attribute in the file on every derivation.
    """
    path = ROOT / rel
    try:
        st = path.stat()
    except OSError as exc:
        raise SurfaceEvidenceUnreadable(
            f"could not read the tracked file {rel}: {exc}. The caller scan must not omit "
            "a tracked file."
        ) from exc
    cache = _parse_cache()
    key = (st.st_mtime_ns, st.st_size)
    cached = cache.get(rel)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        # `git ls-files` lists INDEX entries, so a tracked file removed from the working
        # tree (or unreadable) is still listed and this read raised `FileNotFoundError`
        # out of the derivation — a bare traceback in the REQUIRED gate, the same defect
        # class this file refuses for every other piece of evidence. A silently OMITTED
        # file is worse than a refusal: a missed caller changes a row's derived class.
        raise SurfaceEvidenceUnreadable(
            f"could not read the tracked file {rel}: {exc}. The caller scan must not omit "
            "a tracked file."
        ) from exc
    try:
        tree = ast.parse(text)
    except (SyntaxError, UnicodeDecodeError):
        cache[rel] = (key, {})
        return {}
    found: dict[str, list[tuple[str, ...]]] = {}
    for node, stack in _walk_with_functions(tree, []):
        if isinstance(node, ast.Attribute):
            found.setdefault(node.attr, []).append(stack)
    cache[rel] = (key, found)
    return found


def scan_callers(method_names: set[str], registered_handlers: set[str] | None = None) -> dict[str, set[str]]:
    """method -> set of files that reference it as `<expr>.<method>`.

    AST, never regex. We deliberately do not resolve the receiver type: a
    reference on a non-SDK object with a colliding name is a known false
    positive, and the artifact records it as such. What this scan must not do is
    MISS a caller, so it is deliberately over-inclusive.

    The per-file parse is memoized (`_attributes_with_stacks`); the caller SET
    is rebuilt on every call, because `registered_handlers` decides which side
    of the handler seam a `tortoise/mcp_server.py` reference falls on.
    """
    callers: dict[str, set[str]] = {name: set() for name in method_names}
    registered = registered_handlers or set()
    for rel in tracked_python():
        attributes = _attributes_with_stacks(rel)
        if not attributes:
            continue
        for attr in callers:
            stacks = attributes.get(attr)
            if not stacks:
                continue
            if rel == "tortoise/mcp_server.py":
                # The HANDLER leg is per-registered-tool, not per-file: only a call
                # inside a function that is itself a registered tool handler counts.
                # A method called BOTH inside and outside a handler carries both
                # markers, which is what the per-occurrence scan produced before the
                # parse cache (do not collapse this to one marker).
                inside = any(fn in registered for stack in stacks for fn in stack)
                outside = any(not any(fn in registered for fn in stack) for stack in stacks)
                if inside:
                    callers[attr].add(rel + "#tool")
                if outside:
                    callers[attr].add(rel + "#other")
            else:
                callers[attr].add(rel)
    return callers


def derive_class(name: str, callers: set[str], agent_reachable: bool) -> str:
    if agent_reachable:
        return "agent-reachable"
    if name in AUTHORED_CONTROL_PLANE:
        return "control-plane"
    non_test = {p for p in callers if not p.startswith(TEST_PREFIXES)}
    if not non_test:
        return "no-caller-found"
    cats = {category(p) for p in non_test}
    if cats <= {"eval-harness"}:
        return "eval-only"
    if cats & {"engine", "other-tooling", "documentation"}:
        return "internal"
    # Only non-leg surface categories (tenant REST / the MCP handler file itself):
    # reachable, but by no agent path inside the gate.
    return "no-caller-found"


# --- the cluster seeders -----------------------------------------------------
# A seeder is an AID, not the rule: `cluster` is an authored, baseline-protected
# row field. Completeness of the cluster set is a human review step, not a lint
# check — the lint checks properties OVER the declared set.
SEED_CLUSTERS: list[tuple[str, list[str]]] = [
    # `tortoise_get_entity` is the CANONICAL fetch-by-id tool — an owner decision in
    # `docs/product/canonical-mcp-tools.md` (approved, approval_pr 4120), which rules
    # that `tortoise_get_entity` must not be retired and that `tortoise_get` retires
    # into it instead. The canonical member is the first listed, so it is listed first
    # here; the five per-type getters and `tortoise_get` are the names that fold in.
    # (An earlier cut seeded the cluster with `tortoise_get` first; that put the
    # canonical flag on the name the decision retires.)
    ("fetch-by-id", ["tortoise_get_entity", "tortoise_get", "tortoise_get_point", "tortoise_get_operator",
                     "tortoise_get_events", "tortoise_get_session", "tortoise_get_governance"]),
    ("create", ["tortoise_create_entity", "tortoise_create_subject", "tortoise_create_object", "tortoise_create_event", "tortoise_create_document"]),
    ("delete", ["tortoise_delete", "tortoise_delete_point", "tortoise_delete_entity"]),
    ("update", ["tortoise_update", "tortoise_update_point", "tortoise_update_entity"]),
    ("deprecated-index", ["tortoise_index_files", "tortoise_ingest_corpus", "tortoise_index_sessions"]),
    ("query", ["tortoise_query", "tortoise_paginated_query", "tortoise_query_points_by_tag"]),
    ("operator-action", ["tortoise_operator_action", "tortoise_mitigate_operator", "tortoise_annotate_operator"]),
]


def seed_clusters() -> dict[str, tuple[str, str]]:
    """tool name -> (cluster, canonical tool name). Canonical is the oldest /
    primary member; for these seeds it is the first listed."""
    out: dict[str, tuple[str, str]] = {}
    for cluster, members in SEED_CLUSTERS:
        for member in members:
            out[member] = (cluster, members[0])
    return out


# The four names the declaration marked as deprecated aliases (#3876). All four are
# now RETIRED (#3883), so no live row carries this lifecycle any more — the set is
# kept as the alias vocabulary `recommend_tool` still reads, not as a claim about
# the current registry.
DEPRECATED_ALIASES = frozenset(
    {"tortoise_paginated_query", "tortoise_query_points_by_tag", "tortoise_ingest_corpus", "tortoise_index_sessions"}
)



# --- what USES an entry ------------------------------------------------------
# The owner's second question is "what uses it — an eval, a skill, something?".
# That is a different question from "what does it depend on": dependencies are
# what an entry calls, usage is who calls it. Both are measured, by scanning the
# tree, never asserted.
def _blob(paths: list[Path]) -> str:
    parts = []
    for path in paths:
        try:
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(parts)


def usage_index() -> dict[str, str]:
    """Four text corpora, each answering "is this name used by an eval / skill /
    doc / piece of tooling?". Substring matching is deliberate: a name that
    appears anywhere in a skill or an eval is evidence of use, and a false
    positive here is far cheaper than a missed one."""
    def py(*globs: str) -> list[Path]:
        out: list[Path] = []
        for g in globs:
            out.extend(ROOT.glob(g))
        return [p for p in out if p.is_file()]

    return {
        "eval-harness": _blob(py("battery/**/*.py", "tools/longmem_eval/**/*.py", "tools/ask_*.py")),
        "tooling": _blob(py("tools/**/*.py", "graph-scripts/**/*.py", "apps/**/*.py", "benchmarks/**/*.py")),
        "skill-docs": _blob(py("skills/**/*.md", ".pi/**/*.md")),
        "docs": _blob(py("docs/**/*.md", "*.md")),
    }


def used_by(name: str, corpora: dict[str, str], called: bool | None) -> str:
    hits = [label for label, blob in corpora.items() if name in blob]
    if called is True:
        hits.insert(0, "agents")
    elif called is False:
        hits.insert(0, "never called")
    return ", ".join(hits) if hits else "— nothing found"


def recommend_tool(row: dict, called: bool | None, used_by_str: str, canonical: str | None) -> tuple[str, str]:
    """A PROPOSAL, not a verdict. The owner decides; this is the evidence laid
    out so the decision is cheap. Vocabulary is deliberately small:
    keep / kill / merge / fix-declaration / review."""
    if "WHICH DOES NOT EXIST" in (row.get("dependency") or ""):
        return (
            "fix-declaration",
            "declares an SDK method that does not exist; the tool itself is handler-served and "
            "still works, so this is a false declaration to correct (or a tool to retire — "
            "that part is the owner's call)",
        )
    if row.get("lifecycle") == "deprecated alias":
        target = ""
        desc = row.get("job") or ""
        for marker in ("thin alias for ", "DEPRECATED — use ", "DEPRECATED — use `"):
            if marker in desc:
                target = desc.split(marker, 1)[1].split("(")[0].split(".")[0].strip().strip("`")
                break
        return (
            "kill",
            f"already deprecated and no warning is emitted to callers today (#3876); "
            f"its own description names the replacement: {target or 'see the description'}",
        )
    if canonical and row["name"] != canonical:
        return (
            "merge",
            f"`{canonical}` in the same group already does this job (its own description names what it "
            "consolidates), so this is one name too many — the caller keeps working either way",
        )
    if called is True:
        return ("keep", "called by agents")
    tags = [t for t in used_by_str.split(", ") if t != "never called"]
    real = [t for t in tags if t in ("eval-harness", "tooling", "skill-docs")]
    if real:
        return ("keep", f"not called over MCP, but used by {', '.join(real)} — removing it would break that")
    return (
        "review",
        "never called and referenced only from documentation, so nothing observable uses it. "
        "This is the residue that needs your judgement — I am not calling it fat on `never called` alone",
    )


def telemetry_tools() -> set[str]:
    """Tool names that appear in our own tool-call log. Absence is evidence about
    OUR usage only — the doc says so, because it is not evidence about customers."""
    import json as _json

    log = Path.home() / ".tortoise" / "analytics_fallback.jsonl"
    seen: set[str] = set()
    if not log.exists():
        return seen
    for line in log.read_text(errors="ignore").splitlines():
        if '"mcp_tool_call"' not in line:
            continue
        try:
            rec = _json.loads(line)
        except Exception:
            continue
        name = (rec.get("properties") or {}).get("tool_name")
        if name:
            seen.add(name)
    return seen


def _component_fingerprint(fn) -> str | None:
    """A fingerprint of the IMPLEMENTATION behind a tool component.

    Deliberately built from the CODE OBJECT (`co_filename` and a digest of the
    bytecode, names and constants — see `_code_digest`), not from `__module__` /
    `__qualname__`. Those two attributes are plain
    writable strings, so a shadow function can copy a frozen-baseline tool's identity exactly
    and pass a name-based comparison while serving different code (verified: a shadow that set
    `__module__ = "tortoise.mcp_server"` and `__qualname__ = "tortoise_search"` replaced a
    frozen-baseline tool with the gate green). A code object cannot be forged so cheaply — a
    different implementation means a different file/digest.  `co_firstlineno` is
    deliberately NOT part of the identity: a pure code move is not a served change.

    The half of a served tool that is NOT a code object — its defaults and annotations —
    is fingerprinted separately, by `_served_attributes` and the row's `served_attributes`
    cell (#4192); `served_from` keeps this cell's exact meaning.

    NOT a security boundary: code running in-process can patch `__code__` too. This catches
    the accident and the casual substitution, which is what the gate is for; see the
    threat-surface note in tools/surface-guard.py.
    """
    code = getattr(fn, "__code__", None)
    if code is None:
        return None
    # REPO-RELATIVE, never absolute: the baseline is a checked-in artifact and CI checks
    # out to a different path, so an absolute `co_filename` would make every fingerprint
    # differ there and red the gate on an unchanged tree.
    try:
        rel = Path(code.co_filename).resolve().relative_to(ROOT.resolve())
    except Exception:
        rel = Path(code.co_filename).name
    return f"{rel}:{_code_digest(code)}"


def _carried_row_evidence() -> dict[str, dict]:
    """Row name -> the COMMITTED evidence a re-cut must not re-derive (#10914 review P2).

    Reads `MANIFEST_FILE` at call time (never a bound default) and returns only the keys a
    row of that kind carries (`CARRIED_TOOL_ROW_KEYS` / `CARRIED_SDK_ROW_KEYS`), each
    accepted only when its type is the one the derivation emits — a hand-mangled cell is
    ignored and the row is re-derived rather than copied into the artifact.

    Absence is not an error here: a tool introduced by this very change has no committed
    row, and that is exactly the case the observed-usage columns must not invent. An
    UNREADABLE manifest returns `{}` too, because `cmd_cut` refuses that case itself
    (`_recorded_approvals`) — this function must not be the thing that raises first.
    """
    try:
        with MANIFEST_FILE.open() as fh:
            doc = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return {}
    types = {"used_by": str, "recommendation": str, "basis": str, "reason": str, "exemption": bool}
    carried: dict[str, dict] = {}
    for row in doc["rows"]:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            continue
        keys = CARRIED_SDK_ROW_KEYS if name.startswith("sdk:") else CARRIED_TOOL_ROW_KEYS
        kept = {
            key: row[key]
            for key in keys
            if key in row and isinstance(row[key], types[key])
        }
        if kept:
            carried[name] = kept
    return carried


def _carried_response_fields() -> list:
    """Preserve the hand-authored `response_fields` block across a re-cut.

    These entries record response FIELDS, which the gate deliberately does not compare
    (the freeze is on tools and endpoints). They cannot be derived from the declaration,
    so `cut` must carry them forward: a re-cut that dropped them would silently empty the
    table the carve-out depends on, and the obligation to record a field would evaporate
    the first time anyone regenerated the manifest.
    """
    try:
        with MANIFEST_FILE.open() as fh:
            doc = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(doc, dict):
        return []
    fields = doc.get("response_fields")
    return fields if isinstance(fields, list) else []


def _carried_high_water_counts(fallback: dict) -> dict:
    """The frozen GROWTH PIN, carried forward verbatim across a re-cut (#4185).

    `counts:` is re-derived on every `cut`, so a re-cut over a surface that GREW moves
    the very number a drift check would compare against and the growth disappears. This
    pin is NOT re-derived: `cut` copies the committed value forward, and
    `tools/surface-guard.py` reds when the live counts EXCEED it. Raising it is therefore
    a deliberate, reviewable edit in the approving PR — the machine half of "a re-cut
    over a grown surface cannot be green by construction" (review P1).

    No manifest at all (the first cut): fall back to the fresh counts so the field is
    always emitted. The manifest EXISTS but has no pin, or holds a malformed one: refuse
    rather than silently reset the ceiling — a silent reset is exactly the failure this pin
    exists to prevent.
    """
    try:
        with MANIFEST_FILE.open() as fh:
            doc = yaml.safe_load(fh)
    except FileNotFoundError:
        return dict(fallback)
    except (OSError, yaml.YAMLError) as exc:
        raise SurfaceEvidenceUnreadable(
            f"could not read {MANIFEST_FILE} to carry the `high_water_counts` pin: {exc}. "
            "Refusing to reset the growth ceiling silently."
        ) from exc
    if not isinstance(doc, dict) or "high_water_counts" not in doc:
        raise SurfaceEvidenceUnreadable(
            f"{MANIFEST_FILE} exists but carries no `high_water_counts` pin — refusing to "
            "re-base the growth ceiling silently. The fallback above is for a MISSING manifest "
            "(the first cut); one that exists and lost the pin must not quietly adopt the "
            "freshly derived counts as its ceiling — that is the failure the pin exists to "
            "prevent. Restore the pin (or delete the manifest for a first cut)."
        )
    pin = doc.get("high_water_counts")
    if not isinstance(pin, dict):
        raise SurfaceEvidenceUnreadable(
            f"{MANIFEST_FILE}'s `high_water_counts` is {pin!r}, not a mapping — refusing "
            "to reset the growth ceiling silently."
        )
    out: dict[str, int] = {}
    for key in ("tools", "sdk_public_methods"):
        value = pin.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise SurfaceEvidenceUnreadable(
                f"{MANIFEST_FILE}'s `high_water_counts.{key}` is {value!r}, not an "
                "integer — refusing to reset the growth ceiling silently."
            )
        out[key] = value
    return out


def build_doc(commit: str | None = None, refresh_observations: bool = False) -> dict:
    """Derive the baseline from the declaration. THE single derivation.

    `cut` writes what this returns; `check` compares the committed artifact against it.
    One function, so the generator and the verifier cannot hold two opinions about what
    the surface is — the failure mode the sibling `tools/sdk_surface.py` names when it
    explains why it imports `_sdk_targets` instead of re-implementing it.
    """
    try:
        from tortoise.mcp_server import __name__ as _  # noqa: F401  (import check)
        from tortoise.sdk import TortoiseSDK
        from tortoise.tool_registry import GROUP_BY_NAME, RETIRED_TOOL_REGISTRY, TOOL_REGISTRY
    except Exception as exc:  # an unimportable declaration is a refusal
        raise SurfaceEvidenceUnreadable(
            f"could not import the surface declaration: {type(exc).__name__}: {exc}"
        ) from exc

    order = load_order()
    approval = read_approval()
    keywords = order["keywords"]
    tokens_to_keyword = {t: kw for kw, toks in order["tokens"].items() for t in toks}
    family_rank = order["family_rank"]
    verbs = keywords

    public = sorted(
        name for name in dir(TortoiseSDK) if not name.startswith("_") and callable(getattr(TortoiseSDK, name))
    )
    # #10044: the `sdk:` ROW SET is the frozen baseline of what EXISTS (the `dir()` walk
    # above), and each row records whether that method is on the owner's approved surface
    # (`approved`, cut from `tortoise/__all__.py`).
    #
    # The row set cannot be the approved 40 yet, and pretending otherwise would be worse
    # than the gap: `tools/surface-guard.py` — the required gate that compares the live
    # class against these rows — is restored from the BASE REF in CI
    # (`.github/workflows/python-ci.yml` → `surface-guard`), so a baseline cut down to the
    # 40 would red the required job on the 138 live methods that still exist until Phase 2
    # removes them. So the baseline keeps describing reality, the `approved` flag makes the
    # gap visible per row, and the honest red lives in `tools/sdk_surface_declaration.py
    # check` + `tools/surface-target-check.py`. An unreadable declaration is still a REFUSAL:
    # a baseline cut beside a declaration nobody could read is the "snapshot is not the
    # target" defect (#4185) one level down.
    from tools.sdk_surface_declaration import DeclarationUnreadable, load_declaration

    try:
        approved = set(load_declaration()["__all__"])
    except DeclarationUnreadable as exc:
        raise SurfaceEvidenceUnreadable(str(exc)) from exc
    # Retired names are STILL HANDLERS (they are served through the #3883 shim), so
    # they count for the caller scan: a call inside `tortoise_get_point` is a call
    # inside a tool handler, not free-floating code.
    callers = scan_callers(
        set(public),
        {t.name for t in TOOL_REGISTRY} | {t.name for t in RETIRED_TOOL_REGISTRY},
    )
    corpora = usage_index()
    called_names = telemetry_tools()
    clusters = seed_clusters()
    # The COMMITTED observed-usage evidence, carried forward into this derivation (#10914
    # review P2). Empty on a first cut (no manifest), and empty under
    # `--refresh-observations`, which is the one deliberate way to re-stamp it.
    carried_evidence = {} if refresh_observations else _carried_row_evidence()

    served_http = None
    try:
        from tortoise.mcp_auth import HTTP_ALLOWED

        served_http = set(HTTP_ALLOWED)
    except Exception:  # pragma: no cover - HTTP_ALLOWED is stable
        served_http = set()

    bound_by: dict[str, list[str]] = {name: [] for name in public}
    tools_out = []
    keyword_counts: dict[str, int] = {k: 0 for k in verbs}

    # The REGISTRATION SURFACE, not just the listing. A server-level transform can route
    # `get_tool` WITHOUT appending to `list_tools`, making a tool callable over the
    # protocol while it appears in no enumeration at all; and `mcp.add_tool(fn, name=<a
    # frozen-baseline name>)` silently REPLACES a frozen-baseline tool's implementation while
    # leaving
    # both the name set and the count unchanged. Neither is visible to a listing-only
    # comparison, so the baseline also records what actually does the registering: the
    # transform set, and the implementation identity behind each tool component.
    from tortoise import mcp_server as _mcp_server

    fingerprints: dict[str, str] = {}
    declarations: dict[str, str | None] = {}
    for _key, _tool in _mcp_server.mcp._local_provider._components.items():
        if not _key.startswith("tool:"):
            continue
        _name = getattr(_tool, "name", None) or _key.split(":", 1)[1].split("@")[0]
        _fn = getattr(_tool, "fn", None)
        fingerprints[_name] = _component_fingerprint(_fn)
        declarations[_name] = (
            _served_attributes(_fn) if getattr(_fn, "__code__", None) is not None else None
        )
    # Derived from the SOURCE, not from `mcp._transforms` at this instant: the runtime list
    # is lifecycle-dependent (`_HTTPToolFilter` is registered only when the HTTP app is
    # built), so a baseline cut at import time reads `[]` and then reds spuriously in any
    # process that has since built the app. The declaration is what the source registers.
    import ast as _ast

    _allowed_transforms: set[str] = set()
    _tree = _ast.parse((ROOT / "tortoise" / "mcp_server.py").read_text())
    for _node in _ast.walk(_tree):
        if (
            isinstance(_node, _ast.Call)
            and isinstance(_node.func, _ast.Attribute)
            and _node.func.attr == "add_transform"
            and _node.args
        ):
            _arg = _node.args[0]
            _allowed_transforms.add(
                _arg.func.id
                if isinstance(_arg, _ast.Call) and isinstance(_arg.func, _ast.Name)
                else _ast.unparse(_arg)
            )
    transform_names = sorted(_allowed_transforms)

    # Which entries declare their group in the SOURCE. `ToolDefinition.group` defaults to
    # "memory" (tool_registry.py:34) and `_apply_groups()` then overwrites every entry
    # (tool_registry.py:1321), so by import time `entry.group` is never None and a
    # runtime test of it marks every row "explicit". Reading the literal is the only way
    # to tell a declared group from an assigned one.
    _explicit_group_names: set[str] = set()
    for _node in _ast.walk(_ast.parse((ROOT / "tortoise" / "tool_registry.py").read_text())):
        if isinstance(_node, _ast.Call) and getattr(_node.func, "id", None) == "ToolDefinition":
            if not any(_k.arg == "group" for _k in _node.keywords):
                continue
            for _k in _node.keywords:
                if _k.arg == "name" and isinstance(_k.value, _ast.Constant):
                    _explicit_group_names.add(_k.value.value)
            if _node.args and isinstance(_node.args[0], _ast.Constant):
                _explicit_group_names.add(_node.args[0].value)

    for entry in TOOL_REGISTRY:
        applied = GROUP_BY_NAME.get(entry.name) or getattr(entry, "group", None) or "memory"
        explicit = entry.name in _explicit_group_names
        keyword = derive_keyword(entry.name, entry.description or "", tokens_to_keyword)
        keyword_counts[keyword] += 1
        declared = getattr(entry, "sdk_method", "") or ""
        resolves = bool(declared) and hasattr(TortoiseSDK, declared)
        if resolves:
            bound_by.setdefault(declared, []).append(entry.name)
        cluster, canonical = clusters.get(entry.name, (None, None))
        # #10914 review P2 — the observed-usage columns are CARRIED, not re-derived.
        #
        # A row with committed evidence is written from it verbatim (the fresh derivation
        # below is discarded). A row WITHOUT committed evidence is NEW to this artifact, so
        # no production call can exist for it: `called=False` records that honestly instead
        # of letting this box's log stamp the container `called by agents` off the lane's
        # own test call. The log only drives these columns when the operator asks for it
        # (`--refresh-observations`), so a re-cut can no longer re-stamp the evidence.
        committed = carried_evidence.get(entry.name) or {}
        if committed:
            called = None
        elif refresh_observations:
            called = (entry.name in called_names) if called_names else None
        else:
            called = False
        used_by_str = used_by(entry.name, corpora, called)
        lifecycle = "deprecated alias" if entry.name in DEPRECATED_ALIASES else "active"
        # The full description, verbatim. An earlier cut took the first sentence,
        # which truncated "…incl. provenance, …" to "…incl" — a sentence split is
        # not safe on text that contains abbreviations.
        first_sentence = " ".join((entry.description or "").split())
        _rec = recommend_tool(
            {
                "name": entry.name,
                "dependency": "WHICH DOES NOT EXIST" if (declared and not resolves) else "",
                "lifecycle": lifecycle,
                "job": first_sentence,
            },
            called,
            used_by_str,
            canonical,
        )
        tools_out.append(
            {
                "name": entry.name,
                "served_from": fingerprints.get(entry.name),
                # The OTHER half of a tool's served identity (#4192): a parameter default or
                # an annotation is not in the code object, and the MCP inputSchema publishes
                # both. Its own cell rather than part of `served_from` — the reason is in
                # `_served_attributes` (the guard that measures this cell is pinned to the
                # base ref, so folding it into `served_from` would re-stamp all 82 rows and
                # no PR could satisfy the pinned guard).
                "served_attributes": declarations.get(entry.name),
                "family": applied,
                "family_rank": family_rank[applied],
                "served": "http" if entry.name in served_http else "stdio-only",
                "sdk_method": declared,
                "group_source": "explicit" if explicit else ("mapped" if entry.name in GROUP_BY_NAME else "defaulted"),
                "keyword": keyword,
                "keyword_rank": verbs[keyword],
                "cluster": cluster,
                "canonical": (entry.name == canonical) if cluster else None,
                "job": first_sentence,
                "dependency": (
                    f"declares TortoiseSDK.{declared} (resolves)" if resolves
                    else (f"declares TortoiseSDK.{declared}, WHICH DOES NOT EXIST — see the #3838 note" if declared
                          else "handler-served: no declared SDK link")
                ),
                "lifecycle": lifecycle,
                "used_by": committed.get("used_by", used_by_str),
                "recommendation": committed.get("recommendation", _rec[0]),
                "basis": committed.get(
                    "basis",
                    "decided" if _rec[0] in ("merge", "kill", "fix-declaration") else "observed",
                ),
                "reason": committed.get("reason", _rec[1]),
                "recommended_family": None,
                "proposed": False,
                "exemption": committed.get("exemption", False),
                "approval": None,
            }
        )

    sdk_out = []
    # A RETIRED name still calls its handler, and the handler still calls the SDK
    # method, so a method must not be reported as "reached by no registered MCP
    # tool" when a retired name reaches it — that is the unverified-negative class
    # this column was already fixed for once. The `(retired)` suffix keeps the
    # distinction visible rather than hiding it.
    for entry in RETIRED_TOOL_REGISTRY:
        declared = getattr(entry, "sdk_method", "") or ""
        if declared:
            bound_by.setdefault(declared, []).append(f"{entry.name} (retired)")
    for name in public:
        sdk_committed = carried_evidence.get(f"sdk:{name}") or {}
        reached = bool(bound_by.get(name))
        reached_paths = sorted(
            {category(p) for p in callers.get(name, set()) if not p.startswith(TEST_PREFIXES)}
        )
        # `agent-reachable` needs ALL THREE legs. Deriving it from declared `sdk_method`
        # bindings alone classified a CLI-reached method (apikey_create, close,
        # reconcile_sessions, session_index_health, volunteer_context) and a
        # handler-reached one (recall_gaps, recall_subgraph) as `no-caller-found` or
        # `internal` — so the rendered doc asserted "reached by no agent path at all (no
        # MCP tool, no CLI verb)" over rows whose own `callers` column listed `cli`. That
        # is the same unverified-negative class the `reason` column was just fixed for.
        # AC11/§6.1 item 4(i)/§6.3 declare this union; this is what makes it true.
        agent_reachable = reached or bool({"mcp-handler", "cli"} & set(reached_paths))
        cls = derive_class(name, callers.get(name, set()), agent_reachable)
        # `bound_by` now includes RETIRED names (marked `(retired)`), so the count
        # cannot be read as advertised-surface usage: a retired name is explicitly NOT
        # a registered tool. Splitting the count keeps the column honest — a method
        # reached only by retired names says so instead of claiming registered
        # reachers it does not have.
        reachers = sorted(bound_by[name])
        n_retired = sum(1 for r in reachers if r.endswith("(retired)"))
        reach_phrase = f"{len(reachers) - n_retired} registered tool(s)"
        if n_retired:
            reach_phrase += f" and {n_retired} retired name(s)"
        sdk_out.append(
            {
                "name": f"sdk:{name}",
                "method": name,
                # ON THE OWNER'S APPROVED SURFACE? Cut from `tortoise/__all__.py`, both
                # directions checked by `tools/sdk_surface_declaration.py`. `False` is the
                # migration backlog: it exists today, it is not approved, Phase 2 removes it.
                "approved": name in approved,
                "family": None,
                "family_rank": None,
                "served": "sdk",
                "sdk_method": None,
                "group_source": None,
                "keyword": None,
                "keyword_rank": None,
                "cluster": None,
                "canonical": None,
                "job": f"Public SDK method TortoiseSDK.{name}.",
                "dependency": (
                    f"reached by {reach_phrase}: {', '.join(reachers)}"
                    if reached
                    else f"reached by no registered MCP tool; caller categories: {', '.join(reached_paths) or 'none outside tests'}"
                ),
                "lifecycle": "active",
                "used_by": used_by(name, corpora, None),
                "recommendation": ("keep" if reached else "review"),
                # Sentence must agree with the `dependency` column beside it. It previously
                # hardcoded "no CLI verb calls it", which was FALSE for the five SDK methods
                # the CLI does call (apikey_create, close, reconcile_sessions,
                # session_index_health, volunteer_context) — the row said "callers: cli" while
                # the reason denied it. Never assert a negative the caller scan did not verify.
                "reason": (
                    f"reached by {reach_phrase}"
                    if reached
                    else (
                        f"no MCP tool binds it; callers: {', '.join(reached_paths)}"
                        if reached_paths
                        else "no MCP tool binds it, and no caller outside tests was found"
                    )
                ),
                "recommended_family": None,
                "proposed": False,
                "exemption": sdk_committed.get("exemption", False),
                "approval": None,
                "class": cls,
            }
        )

    # A tool row is always agent-reachable: it IS the MCP surface. The consumer
    # classes exist to describe the SDK methods that no tool reaches.
    for row in tools_out:
        row["class"] = "agent-reachable"

    # Split clusters: where a declared cluster's members sit in more than one
    # family, every member carries the SAME PROPOSED family — the canonical
    # member's. AC13 reds per member if any member lacks one, so a cluster
    # cannot be half-resolved. `proposed` is defined as `recommended_family is
    # not None`, which is why it is set here and not from `recommendation`
    # (which is non-null for every row). These are PROPOSALS: they wait for the
    # owner, and `proposed: true` is what keeps them out of the gate.
    span: dict[str, set[str]] = {}
    for row in tools_out:
        if row["cluster"]:
            span.setdefault(row["cluster"], set()).add(row["family"])
    canonical_family = {r["cluster"]: r["family"] for r in tools_out if r["cluster"] and r["canonical"]}
    canonical_name = {r["cluster"]: r["name"] for r in tools_out if r["cluster"] and r["canonical"]}
    for row in tools_out:
        row["cluster_canonical"] = canonical_name.get(row["cluster"]) if row["cluster"] else None
    for row in tools_out:
        cluster = row["cluster"]
        if cluster and len(span[cluster]) > 1:
            row["recommended_family"] = canonical_family[cluster]
            row["proposed"] = True
            row["cluster_note"] = (
                f"group `{cluster}` is split across {sorted(span[cluster])}; "
                f"proposed family `{canonical_family[cluster]}` — waits for your approval"
            )

    # The emitted order IS the key order: the manifest stores rows in the order
    # AC13 checks, so the check is a comparison rather than a re-sort.
    tools_out.sort(
        key=lambda r: (
            r["family_rank"],
            r["keyword_rank"],
            r["cluster"] or r["name"],
            0 if r["canonical"] in (True, None) else 1,
            r["name"],
        )
    )
    sdk_rows = sdk_out
    sdk_rows.sort(key=lambda r: (r["method"],))

    # Retired names (#3883) are NOT surface rows — they are advertised nowhere. They
    # are recorded separately so the gate can hold the other half of the contract:
    # each one must still RESOLVE and WARN, not silently disappear. A retirement is
    # as much a surface change as an addition, so entering or leaving this list is a
    # re-cut in a PR that carries the decision.
    retired_out = [
        {
            "name": e.name,
            "use_instead": e.retired_use_instead,
            "sdk_method": getattr(e, "sdk_method", None) or None,
            "http_policy": bool(getattr(e, "http_policy", False)),
            # An unvalidated AUDIT reference, exactly as on a live row: the approval itself
            # is READ from docs/product/canonical-mcp-tools.md (#4185). Retiring a name
            # shrinks the surface and is a decision, but this field records, it does not gate.
            "approval": None,
        }
        for e in RETIRED_TOOL_REGISTRY
    ]
    retired_out.sort(key=lambda r: r["name"])

    doc = {
        "manifest_version": 1,
        "issue": 3863,
        "cut_at_commit": commit or subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip(),
        "allowed_transforms": transform_names,
        # Hand-authored, and deliberately NOT gated — carried forward, never re-derived.
        "response_fields": _carried_response_fields(),
        # READ from `docs/product/canonical-mcp-tools.md`, never typed here (#4185).
        # This is the one field group the manifest does not maintain itself: the `cut`
        # COPIES it from the canonical doc at cut time, so an approval recorded after the
        # cut needs a re-cut to land. `check` DOES compare them — the four keys are not in
        # `NON_DERIVABLE_DOC_KEYS` (see the header block) — so a hand-edit that disagrees
        # with the doc reds the lint. `tools/surface-guard.py` independently re-reads the
        # same document and reds a manifest that CLAIMS an approval disagreeing with it.
        "approval_status": approval["status"],
        "approval_principal": approval["principal"],
        "approval_pr": approval["pr"],
        "approval_date": approval["date"],
        "counts": {
            "tools": len(tools_out),
            "sdk_public_methods": len(sdk_out),
            "retired": len(retired_out),
            "keyword_distribution": keyword_counts,
        },
        # A GROWTH PIN, not a derivation (see `NON_DERIVABLE_DOC_KEYS` and
        # `_carried_high_water_counts`): carried forward by `cut` so a re-cut over a
        # grown surface cannot raise it silently; the guard reds when the live surface
        # exceeds it.
        "high_water_counts": _carried_high_water_counts(
            {"tools": len(tools_out), "sdk_public_methods": len(sdk_out)}
        ),
        "retired": retired_out,
        "rows": tools_out + sdk_rows,
    }
    return doc


def _display(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise.

    `cmd_cut` writes to `MANIFEST_FILE`, which a test (or a future `--out`) may redirect
    outside the checkout; `relative_to` raises there, and a print must not be what fails.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _recorded_approvals(doc: dict) -> list[tuple[str, str]]:
    """Every non-null row-level `approval` in a manifest, in file order.

    `approval` is NON_DERIVABLE: it records an OWNER decision about a row, and no re-cut can
    reproduce it. This is precisely the list a re-cut destroys.

    `retired:` rows carry the field too — retiring a name shrinks the surface and needs the
    same human approval, and `build_doc` blanks it there as well — so BOTH lists are walked.
    Reading only `rows:` leaves the retired half of the artifact unguarded, which is the same
    silent wipe through a different door (#4598).
    """
    found: list[tuple[str, str]] = []
    for row in [*(doc.get("rows") or []), *(doc.get("retired") or [])]:
        if not isinstance(row, dict):
            continue
        approval = row.get("approval")
        if approval:
            name = row.get("name") or row.get("key") or row.get("tool") or "<unnamed>"
            found.append((str(name), str(approval)))
    return found


# ── the REMOVAL direction (#12237) ──────────────────────────────────────────────
#
# `tools/archive_surface.py check` answers ONE direction: an ARCHIVED symbol that is still
# live is RED. It iterates the archive MANIFEST's entries, never the surface, so code that
# is simply gone from BOTH — deleted, never archived — is invisible to it (measured: the
# expected violation list is empty for a surface the manifest does not mention).
#
# `cut` is the tool that performs a removal: a re-cut re-derives the row set from the code,
# so a row that disappeared from the declaration disappears from the baseline here. The
# other direction is therefore enforced at THAT seam, where the row actually goes away —
# not in a lint over two artifacts that both lost it. Without this, a batch that deletes 10
# tools and archives 0 re-cuts cleanly, `surface-guard.py` reds only until a human re-cuts,
# and then everything is green with the removed code gone from the repo — which is exactly
# what the #11609 owner ruling forbids ("we archive it in case we made a mistake").

ARCHIVE_KIND_BY_CLASS = {"tool": "mcp-tool", "sdk": "sdk-method"}

REMOVAL_WAYS = (
    "an `archive/MANIFEST.json` entry of the matching kind (freeze the code FIRST: "
    "`uv run python tools/archive_surface.py archive <mcp-tool|sdk-method> <symbol> "
    "--batch <slug> --reason <text>`) — for an `sdk-method` this is ONE HALF: it must be "
    "paired with `RETIRED_SDK_METHODS` / `REMOVED_SDK_METHODS`, because a frozen method "
    "without the map entry strips the caller with a bare `AttributeError` instead of a "
    "recorded departure (the freeze and the map entry are ONE record, and `CONTRIBUTING.md` "
    "says so)",
)

# ⛔ THE MAP IS **NOT** A ROUTE ON ITS OWN, and advertising it as one was a hole, not a
# courtesy: `RETIRED_SDK_METHODS` / `REMOVED_SDK_METHODS` records the DEPARTURE (a fail-closed
# `AttributeError`), it keeps no code — and `archive_surface.check` iterates the archive
# manifest, so a method that was never frozen is invisible to every gate. The refusal that
# listed it as a standalone route therefore told the operator to delete the method and move on
# (review round 14 of PR #12313: reproduced with the real `cmd_cut` — map-only, no archive
# entry, exit 0, baseline rewritten).
SDK_PAIR_RULE = (
    "`RETIRED_SDK_METHODS` / `REMOVED_SDK_METHODS` is NOT a route on its own: it records the "
    "departure but keeps no code, and removed code is what the #11609 ruling archives. For an "
    "SDK method, freeze it AND record it — the two halves are ONE record and this cut "
    "requires BOTH."
)

# ⛔ `RETIRED_USE_INSTEAD` IS NOT A ROUTE HERE, and listing it as one sent lanes to a dead end.
# A name that is in that map WITH its declaration is a RETIREMENT: `TOOL_REGISTRY` excludes it
# and `RETIRED_TOOL_REGISTRY` includes it, so the name is still SERVED, it appears in the
# baseline's `retired:` half, and the surface never lost it — not a removal at all. A name
# whose declaration is GONE is the only state that can be a removal, and there the map key
# dangles: `removal_report` refuses it with "delete the stale forward key, or restore the tool",
# the exact opposite of the "account for it here" instruction this line used to give
# (review of PR #12313, round 9 — reproduced, and the docstring one screen up already said the
# accepting arm "could not fire in production").
NOT_A_REMOVAL_ROUTE = (
    "A `RETIRED_USE_INSTEAD` entry is NOT a route here: with the declaration still live the "
    "symbol is RETIRED, not removed (it is still served, and the `retired:` half of the "
    "baseline is compared); with the declaration gone the map key dangles and the cut refuses "
    "it as a contradiction. In that dangling state NEITHER route above works as written — "
    "`archive_symbol` reads the declaration and refuses without one, and `check` refuses to "
    "archive a name that is in that map — so restore the declaration from git FIRST, then "
    "either keep it (a retirement) or freeze the code and delete it (a removal)."
)


def surface_symbols(doc: dict) -> set[tuple[str, str]]:
    """`{(symbol, surface-class)}` for every row of a manifest document — BOTH halves.

    The symbol is what a removal RECORD names: a tool's registry name, an SDK method's
    name. `sdk:` is the manifest's ROW-NAME prefix, not part of the method name.

    ⛔ THE `retired:` HALF IS READ TOO, and that is not symmetry for its own sake. Retired
    names are STILL SERVED — `RETIRED_TOOL_REGISTRY` is derived from `RETIRED_USE_INSTEAD`
    and every one of those handlers still answers (#3883) — so deleting a name from that
    map removes BOTH the `retired:` row and the forward record in one ordinary edit. A gate
    that read only `rows:` would watch that happen and report nothing, which is the same
    silent wipe through a different door the approvals reset guards against (#4598).
    Reading the half changes nothing on an ordinary re-cut (those rows are in the derived
    document too) and nothing on a legitimate retirement (`RETIRED_USE_INSTEAD` still
    accounts for it).

    The key is `(symbol, class)`, not the symbol alone: a tool whose registry name collides
    with a live SDK method name would otherwise be overwritten in the mapping and its
    removal would be invisible.
    """
    symbols: set[tuple[str, str]] = set()
    for key in ("rows", "retired"):
        half = doc.get(key)
        if half is None and key == "retired":
            # NOT "an older manifest may predate the half" — no such manifest can be committed
            # green: `check` reds a baseline with no `retired:` list from both directions. The
            # only tree that reaches this is one `cut` is REPAIRING, so refusing would block
            # the repair; but an absence must be SAID, because a retired name dropped in the
            # same PR is otherwise invisible. `cmd_cut` prints the warning.
            continue
        if not isinstance(half, list):
            raise SurfaceEvidenceUnreadable(
                f"a manifest document carries no `{key}:` list, so its removed symbols "
                "cannot be enumerated — a refusal, never an empty removal set"
            )
        for row in half:
            if not isinstance(row, dict):
                # A row the gate cannot read is UNREADABLE EVIDENCE, not an absent row. The
                # `continue` this replaces graded it as "no such row existed", so a prior
                # baseline carrying e.g. `- tortoise_x` (a hand-edit or a `yq` append that
                # dropped the `name:` key) lost the symbol with `cut` exiting 0 — the exact
                # deletion this gate exists to refuse. `_read_manifest` accepts such a row
                # (it only requires `rows` to be a LIST), and `check`'s malformed-row finding
                # needs a committed tree, which a cut never gives it: the cut is what erases
                # the shape. Measured by review round 4 of PR #12313.
                raise SurfaceEvidenceUnreadable(
                    f"a manifest `{key}:` row is not a mapping ({type(row).__name__}: "
                    f"{row!r:.60}) — the removal gate cannot read it, and grading an "
                    "unreadable row as absent is how a symbol leaves the surface unrecorded"
                )
            name = str(row.get("name") or row.get("tool") or row.get("key") or "")
            if not name:
                raise SurfaceEvidenceUnreadable(
                    f"a manifest `{key}:` row carries no name: the removal gate cannot tell "
                    "which symbol disappeared, and guessing one would grade the wrong record"
                )
            if name.startswith("sdk:"):
                # The row's NAME is its identity; `method` is a DERIVED cell beside it, and a
                # hand-edit can move it while the name still says which method departed. The
                # suffix is preferred (falling back to `method`) so the gate reads what the row
                # says rather than what a splicer left in a derived column (review of PR #12313).
                symbols.add((name[len("sdk:"):] or str(row.get("method") or ""), "sdk"))
            else:
                symbols.add((name, "tool"))
    return symbols


def removal_records(archive_manifest: Path | None = None) -> dict:
    """The three records a removal may be accounted by, or a REFUSAL.

    Never a partial answer: an unreadable archive and an unimportable retirement map are
    both refusals. "I could not see the record" and "there is no record" must not be the
    same verdict for a gate whose failure mode is losing code permanently.

    `archive_manifest` is a parameter so a test can read the manifest it means to read
    rather than a path bound at import. There is deliberately NO `root`: the retirement maps
    come from the imported package, so a caller asking about a different checkout would get
    this one's answer — a parameter that cannot change the verdict is a trap, not an option.
    """
    try:
        from tools import archive_surface
    except Exception as exc:  # pragma: no cover - an import failure is an environment fault
        raise SurfaceEvidenceUnreadable(
            f"could not import tools/archive_surface.py to read the archive record: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    path = Path(archive_manifest) if archive_manifest else Path(archive_surface.MANIFEST_PATH)
    try:
        doc = archive_surface.load_manifest(path)
    except Exception as exc:
        raise SurfaceEvidenceUnreadable(
            f"could not read the archive manifest {_display(path)}: {exc}"
        ) from exc
    archived: set[tuple[str, str]] = set()
    for entry in doc.get("entries") or []:
        if not isinstance(entry, dict):
            # A frozen entry the gate cannot read is UNREADABLE EVIDENCE, not an entry that
            # is not there — the same asymmetry `surface_symbols` refuses one function over,
            # fixed in the row reader by review round 4 of PR #12313 and left here. Skipping
            # it can only LOSE records, so nothing passes wrongly; it sends the operator to
            # archive a symbol that is already archived, and it hides a corrupt manifest.
            raise SurfaceEvidenceUnreadable(
                f"an `archive/MANIFEST.json` entry is not a mapping ({type(entry).__name__}: "
                f"{entry!r:.60}) — the removal gate cannot read it, and grading an "
                "unreadable entry as 'not archived' is how a frozen symbol is reported as "
                "unrecorded"
            )
        kind = str(entry.get("kind") or "")
        symbol = str(entry.get("symbol") or "")
        # ⛔ KIND + SYMBOL ALONE IS NOT A RECORD. `archive_symbol` always writes `batch`,
        # `reason` and `file`, and `archive_surface.file_violations` reds a row without them
        # ("manifest row carries no `file`") - so a hand-typed row of just kind+symbol was a
        # record `cut` accepted and `check` refused, and the cheapest way to clear that red
        # BEFORE the deleted code can be frozen is to delete the row: then prior == derived and
        # no gate can see the removal again, which is the #12237 loss reached THROUGH this gate
        # (review of PR #12313).
        for field in ("batch", "reason", "file"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise SurfaceEvidenceUnreadable(
                    f"an `archive/MANIFEST.json` entry ({kind}:{symbol}) carries no `{field}` "
                    "- `archive_surface.py check` reds a row without it, so it is not a record "
                    "this gate may accept: a hand-typed row would let `cut` forget the symbol, "
                    "and the cheapest way to clear the red afterwards is deleting the row"
                )
        if not kind or not symbol:
            # A mapping that names no symbol is the same defect one branch over: skipping it
            # grades "unreadable" as "not archived", so `cut` tells the operator to archive
            # a symbol that IS archived and a corrupt manifest stays quiet. `check` reds this
            # shape through the file metadata, but the cut never gives it a committed tree.
            raise SurfaceEvidenceUnreadable(
                f"an `archive/MANIFEST.json` entry names no {'symbol' if kind else 'kind'} "
                f"({sorted(entry)[:6]}) — the removal gate cannot tell which symbol it "
                "freezes, and an entry it cannot read is not an entry that is not there"
            )
        archived.add((kind, symbol))
    # ⛔ DELEGATE THE DEFINITION OF "A RECORD", DO NOT RE-DERIVE IT. A field list here is a
    # SECOND opinion about what makes an archive row a record, and the two opinions drifted:
    # the round-8 check ("`file` must name a file that exists") accepted a row naming
    # `MEMORY.md`, a row pointing at a REAL frozen file under a DIFFERENT symbol, a row with
    # no `archived_at`, a mutated block sha, and the same `(kind, symbol)` twice — every one of
    # them RED by `archive_surface.py check`, so `cut` wrote a baseline whose archive has
    # already lost the code and the cheapest way out of the red is deleting the row, after
    # which no gate sees the removal at all (review of PR #12313, round 9). The sibling owns
    # this question; the record reader now asks it instead of answering it again.
    try:
        # ⛔ ONLY WHERE THERE IS A MANIFEST TO GRADE. `manifest_violations`'s first arm reds a
        # MISSING `archive/MANIFEST.json` (correctly, for `check`, which is asked whether the
        # archive is intact) — but `load_manifest`'s contract is the opposite one this reader
        # needs: "missing file is EMPTY, not an error (first run)". Delegating unconditionally
        # refused EVERY cut on a tree with no archive at all, removal or not — a false refusal
        # of the ordinary path, which is worse than the forged-record hole it closed
        # (review of PR #12313, round 10). An absent manifest holds no records either way, so
        # there is nothing here to grade.
        violations: list[str] = []
        if path.exists():
            violations += archive_surface.manifest_violations(doc, path.parent)
            violations += archive_surface.file_violations(
                [e for e in (doc.get("entries") or []) if isinstance(e, dict)], path.parent, ROOT
            )
    except Exception as exc:  # pragma: no cover - an environment fault is a refusal
        raise SurfaceEvidenceUnreadable(
            f"could not grade the archive manifest against its own checker: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if violations:
        raise SurfaceEvidenceUnreadable(
            "the archive manifest does not hold up under `archive_surface.py check`, so its "
            "rows are not records this gate may accept (a cut that trusted them would forget "
            "a symbol whose code was never frozen): " + "; ".join(violations[:3])
            + (f" (+{len(violations) - 3} more)" if len(violations) > 3 else "")
        )
    try:
        from tortoise.sdk_retired import REMOVED_SDK_METHODS, RETIRED_SDK_METHODS
        from tortoise.tool_registry import RETIRED_USE_INSTEAD
    except Exception as exc:
        raise SurfaceEvidenceUnreadable(
            f"could not import the retirement records (RETIRED_USE_INSTEAD / "
            f"RETIRED_SDK_METHODS / REMOVED_SDK_METHODS): {type(exc).__name__}: {exc}"
        ) from exc
    return {
        "archived": archived,
        # ⛔ THE RAW MAP, AND THAT IS THE ONLY FORWARD SET. There used to be a second field,
        # `forward`, holding the SERVED subset (`_RETIRED_SHIMS`, built from
        # `RETIRED_TOOL_REGISTRY`) — but `removal_report` reads `forward_raw`, so the served
        # set was inert, kept alive only by a `dict.get` fallback that could never run
        # (`removal_records` always sets `forward_raw`) and by a test asserting the dead field.
        # A defence nothing consults is worse than no defence: it reads as protection while a
        # later change could consume `forward` and reopen the dangling-key hole below with the
        # suite green (review of PR #12313, round 11).
        #
        # `archive_surface.check` reads the raw map: a KEY whose `ToolDefinition` was deleted is invisible to `check`'s
        # `forward`-is-still-served arm and reds its ARCHIVE arm instead ("is a
        # RETIRED_USE_INSTEAD forward entry — a forward entry is STILL SERVED … must not be
        # archived"). With only the served subset here, `cut` told the operator to archive a
        # dangling name and `check` refused that same archive: two gates, two verdicts, one
        # tree, and the only way out of the red was deleting the frozen entry. The raw set is
        # what lets `removal_report` refuse in `check`'s words (review of PR #12313).
        "forward_raw": set(RETIRED_USE_INSTEAD),
        "sdk_retired": set(RETIRED_SDK_METHODS) | set(REMOVED_SDK_METHODS),
    }


def parse_waiver(entry: str) -> tuple[str | None, str]:
    """`tool:<symbol>` / `sdk:<symbol>` → `("tool", "<symbol>")`; a bare name → `(None, name)`.

    ONE parser for ONE grammar. Two implementations of it disagreed: `removal_report` treated
    `foo:bar` as a BARE symbol while `cmd_cut` keyed on any `:` and read it as the qualified
    `bar`, so a bare symbol containing a colon was accepted by one and mangled by the other
    (review of PR #12313). `None` means "whichever surface this cut removes".
    """
    text = str(entry)
    head, sep, tail = text.partition(":")
    if sep and head in ARCHIVE_KIND_BY_CLASS and tail:
        return head, tail
    return None, text


def waiver_matches(entry: str, symbol: str, cls: str) -> bool:
    """Does this waiver entry account for the removal of `symbol` on surface `cls`?"""
    want_cls, want_symbol = parse_waiver(entry)
    return want_symbol == symbol and (want_cls is None or want_cls == cls)


class RemovalProblem(str):
    """A refusal line that also carries WHICH class of problem it is.

    `cmd_cut` printed ONE header for every problem - "cut would drop N surface row(s) with no
    usable record", the three archive routes, and the `--allow-removal` remedy - but
    `removal_report` also produces problems that are NOT "no record": a redundant override on
    a symbol that IS recorded, an ambiguous bare waiver, and a contradiction. So a refusal told
    the operator to add a record to a row that had one, and for the ambiguity it prescribed the
    very flag that had just been refused ("logged below", on a cut that returns 1 and logs
    nothing below). A `str` subclass keeps every existing message match - `in`, `problems[0]`,
    equality - working while the caller branches on `.kind` (review of PR #12313).
    """

    kind: str

    def __new__(cls, kind: str, message: str) -> RemovalProblem:
        obj = super().__new__(cls, message)
        obj.kind = kind
        return obj


def removal_report(
    prior: set[tuple[str, str]],
    derived: set[tuple[str, str]],
    records: dict,
    *,
    allowed: set[str] | None = None,
) -> tuple[list[RemovalProblem], dict[tuple[str, str], str]]:
    """`(problems, accounting)` for the symbols that disappeared from the surface.

    A symbol present in `prior` and absent from `derived` is a REMOVAL. It is accounted for
    by EXACTLY ONE record: an `archive/MANIFEST.json` entry of the symbol's own kind — and
    for an SDK method that entry is ONE HALF, paired with the retirement map entry (BOTH are
    required, and together they are one record, not two). Two edges follow from what those
    artifacts actually are:

    * a TOOL in `RETIRED_USE_INSTEAD` is REFUSED, and that refusal is REACHABLE: the map is
      read RAW here (`forward_raw`), not as the served subset, because a key whose tool
      declaration was deleted serves nothing (so the surface lost it and it IS a removal)
      while `archive_surface.check` still reds archiving it — "a forward entry is STILL
      SERVED … must not be archived". A served forward name can never be a removal at all
      (`build_doc` writes those names into `retired:`, which `surface_symbols` reads), so the
      old arm that ACCEPTED `RETIRED_USE_INSTEAD` as an accounting record could not fire in
      production, and its "two records" sibling fired only on a document the derivation
      cannot emit. Both are replaced by this one arm, which fires on the state that really
      happens: retired-name cleanup that forgot the map key;
    * an SDK method archived AND in `RETIRED_SDK_METHODS` / `REMOVED_SDK_METHODS` is ONE
      record, not two: the archive freezes the code, the map records the departure, and
      counting the pair apart refused the DOCUMENTED procedure (freeze the code, record the
      departure) — found by review round 5 of PR #12313.
      **BOTH halves are required, and NEITHER alone is the record.** The map without the
      freeze loses the code permanently — the #11609 ruling keeps removed code "in case we
      made a mistake" — and nothing else can see that loss, because `archive_surface.check`
      iterates the ARCHIVE MANIFEST: a method that was never frozen is invisible to it, so a
      map-only cut exited 0 over a deletion no gate would ever report (review round 14 of
      PR #12313). The freeze without the map strips a caller with a bare `AttributeError`
      instead of a recorded departure, which `archive_surface.check` reds from its own side.

    `allowed` is the deliberate-rename escape hatch (`cut --allow-removal`), applied to a
    NAMED symbol only — there is no blanket flag, because a blanket flag is how a gate
    becomes decoration.

    A waiver entry is either a BARE symbol or a SURFACE-QUALIFIED one (`tool:<symbol>` /
    `sdk:<symbol>`). The qualification exists because `accounting` is keyed `(symbol,
    surface-class)` — the same key the gate itself uses — so a bare symbol that would waive
    a dropped TOOL and a dropped SDK METHOD of the same name at once is ambiguous and is
    refused rather than counted as one record for two removals (review of PR #12313).
    """
    entries = [parse_waiver(e) for e in (allowed or ())]

    def _waived(symbol: str, cls: str) -> bool:
        """Is this removal waived — by a bare waiver, or by one naming THIS surface?"""
        return any(c is None or c == cls for c, s in entries if s == symbol)

    def _waived_by_bare(symbol: str) -> bool:
        return any(c is None and s == symbol for c, s in entries)

    def _waived_by_qualified(symbol: str, cls: str) -> bool:
        """Does a waiver that NAMES this surface cover it?"""
        return any(c == cls and s == symbol for c, s in entries)
    problems: list[RemovalProblem] = []
    accounting: dict[tuple[str, str], str] = {}
    # Bare waivers that met a RECORDED removal, held until every removal is graded: a bare
    # entry is a false record only if it waives nothing anywhere.
    recorded_bare: dict[str, list[tuple[str, str]]] = {}
    # `{(symbol, class): …}` — ONE entry per REMOVAL, not per symbol. Keyed by symbol (as it
    # was) two removals of the same name collapsed into one entry and the `accounted N
    # removal(s)` line understated what the cut did; the printed record is the audit the
    # #11609 ruling keeps, so it may not undercount.
    waived: dict[str, list[str]] = {}
    for symbol, cls in sorted(prior):
        if (symbol, cls) in derived:
            continue
        found: list[str] = []
        kind = ARCHIVE_KIND_BY_CLASS[cls]
        archived = (kind, symbol) in records.get("archived", ())
        if cls == "sdk" and symbol in records.get("sdk_retired", ()):
            # ONE record (round 5: counting the pair apart refused this tree's own procedure),
            # whose BOTH halves are REQUIRED (round 14: the map alone accepted a deletion of
            # code that nothing else can see — `archive_surface.check` iterates the archive
            # manifest, so a method that was never frozen is invisible to every other gate).
            # The map records the DEPARTURE; it does not keep the code.
            if not archived:
                problems.append(RemovalProblem(
                    "no-record",
                    f"{symbol} (sdk) is in RETIRED_SDK_METHODS / REMOVED_SDK_METHODS but its "
                    "code is NOT archived — the map records the DEPARTURE, it does not keep "
                    "the code, and the #11609 ruling keeps removed code so a mistake can be "
                    "inspected later. FREEZE IT FIRST: `uv run python "
                    f"tools/archive_surface.py archive sdk-method {symbol} --batch <slug> "
                    "--reason <text>` (the freeze and the map entry are ONE record, and this "
                    "cut requires BOTH halves)"
                ))
                continue
            found.append("RETIRED_SDK_METHODS/REMOVED_SDK_METHODS + archived code")
        elif cls == "sdk" and archived:
            # The OTHER half of the pair, and NOT a pass.
            # `surface_violations` reds this exact shape — "archived but absent from
            # RETIRED_SDK_METHODS / REMOVED_SDK_METHODS — a caller would then hit a bare
            # AttributeError instead of the recorded departure" — so accepting it here told
            # the operator the removal was accounted for and then reddened CI, and the
            # cheapest way to clear that red without knowing the map requirement is to
            # DELETE the frozen entry: the loss this archive exists to prevent. Measured on
            # both sides (review of PR #12313).
            problems.append(RemovalProblem(
                # NOT "no-record": the row HAS a record — it is missing its OTHER half — and
                # counting it as a plain drop printed "cut would drop 1 surface row(s) with no
                # usable record" over a row the very next line says is archived
                # (review of PR #12313, round 12).
                "incomplete-record",
                f"{symbol} (sdk) is archived but absent from RETIRED_SDK_METHODS / "
                "REMOVED_SDK_METHODS — `archive_surface.py check` reds that shape, so the "
                "freeze alone is not the record: record the departure too (for an SDK method "
                "the freeze and the map entry are ONE record)"
            ))
            continue
        elif archived:
            found.append(f"archive entry ({kind})")
        if cls == "tool" and symbol in records.get("forward_raw", ()):
            # REACHABLE, and refused in `check`'s own words. "Archived AND forward" is TWO
            # records only when an archive entry is also present; on its own the forward key
            # is the whole problem: the tool declaration is gone (so the name serves nothing
            # and the surface lost it) while the map still names it, and `archive_surface.check`
            # reads the RAW map and reds archiving a forward entry. Accepting the archive here
            # — or claiming the symbol has "no record" — sends the operator to a red whose
            # cheapest clearing is deleting the frozen code. (review of PR #12313)
            if archived:
                problems.append(RemovalProblem(
                    "contradiction",
                    f"{symbol} (tool) is accounted by 2 records (archive entry ({kind}), "
                    "RETIRED_USE_INSTEAD) — and the archive one is a CONTRADICTION: "
                    "`archive_surface.py check` reads the raw forward map and reds archiving "
                    "a forward entry (\"a forward entry is STILL SERVED … must not be "
                    "archived\"). Delete the stale forward key, or restore the tool; do not "
                    "archive it"
                ))
            else:
                problems.append(RemovalProblem(
                    "contradiction",
                    f"{symbol} (tool) is still a `RETIRED_USE_INSTEAD` entry whose tool "
                    "declaration is gone: it serves nothing (which is why the surface lost "
                    "it), and `archive_surface.py check` refuses to archive a name that is "
                    "in that map at all. Delete the stale forward key, or restore the tool — "
                    "the archive route is not available for it"
                ))
            continue
        if found:
            # ONLY a waiver that NAMES this surface can replace its record. `_waived` was used
            # here, so a BARE `--allow-removal x` was refused as a redundant override when the
            # removal it matched was the recorded one on the OTHER surface — while the same
            # bare entry was exactly what the unrecorded class needed, and waives. The
            # "sole authority" rule the ambiguity counter already applies applies here too
            # (review of PR #12313, round 11). A bare entry's verdict is DEFERRED, because
            # whether it waives anything depends on removals still to come: if it covers no
            # unrecorded removal it is the false record round 8 refused.
            if _waived_by_qualified(symbol, cls):
                # A record ALREADY accounts for this removal, so the waiver waives nothing —
                # and honouring it would REPLACE that record in the printed log, telling the
                # next reader to look for a rename that did not happen. Same false-record
                # class as a reason with no symbol (refused in `cmd_cut`), and the same
                # unused-permission argument as an override for a symbol this cut does not
                # remove (review of PR #12313).
                problems.append(RemovalProblem(
                    "redundant-override",
                    f"{symbol} ({cls}) is already accounted for by {found[0]}, so "
                    "`--allow-removal` waives nothing — an override that replaces a real "
                    "record with a rename claim is a false record"
                ))
                continue
            if _waived_by_bare(symbol):
                recorded_bare.setdefault(symbol, []).append((cls, found[0]))
            accounting[(symbol, cls)] = found[0]
            continue
        if _waived(symbol, cls):
            if _waived_by_bare(symbol) and not _waived_by_qualified(symbol, cls):
                # ONLY a bare waiver can be ambiguous — and only for the classes it is the
                # sole authority over. `--allow-removal x --allow-removal sdk:x` names the
                # sdk surface explicitly, so the bare entry covers ONLY the tool removal:
                # counting both against it refused a legitimate invocation and printed
                # "one `--allow-removal x` would waive 2 removals", which is false for that
                # command and whose prescribed remedy the operator had already applied
                # (review of PR #12313).
                waived.setdefault(symbol, []).append(cls)
            accounting[(symbol, cls)] = (
                "`--allow-removal` (deliberate rename, stated in the PR)"
            )
            continue
        problems.append(RemovalProblem(
            "no-record",
            f"{symbol} ({cls}) was DROPPED with no record — it is gone from the "
            "declaration and from the archive both, so there is nothing left to read"
        ))
    for symbol, pairs in sorted(recorded_bare.items()):
        if symbol in waived:
            # The bare entry really does waive an unrecorded removal, so it is not a false
            # record — the recorded classes keep their records and it accounts the rest.
            continue
        for cls, label in pairs:
            problems.append(RemovalProblem(
                "redundant-override",
                f"{symbol} ({cls}) is already accounted for by {label}, so "
                f"`--allow-removal {symbol}` waives nothing — an override that replaces a "
                "real record with a rename claim is a false record",
            ))
            # The refusal is the verdict, so the record it names is not part of an `accounted`
            # block that will never print — and a caller reading `accounting` must not see a
            # record the cut refused.
            accounting.pop((symbol, cls), None)
    for symbol, classes in sorted(waived.items()):
        if len(classes) > 1:
            # The same invariant the redundant-override arm enforces: a caller reading
            # `accounting` must not see a record the cut REFUSED. The ambiguity counts every
            # class the bare entry waives, so all of them come out (review of PR #12313,
            # round 12).
            for cls in classes:
                accounting.pop((symbol, cls), None)
            problems.append(RemovalProblem(
                "ambiguous-waiver",
                f"one `--allow-removal {symbol}` would waive {len(classes)} removals "
                f"({', '.join(classes)}) — a named waiver is ONE symbol on ONE surface, and "
                "collapsing them into one record understates what this cut removed. Name "
                f"the surface: `--allow-removal tool:{symbol}` / `sdk:{symbol}`"
            ))
    return problems, accounting


def cmd_cut(args: argparse.Namespace) -> int:
    # ⛔ #11057 review P1: `--commit` was written into the record with NO validation, so
    # `cut --commit main` landed a record that `check` could not distinguish from a verified
    # one — a ref resolves, and a branch is its own ancestor. The gate refuses the shape at
    # read time; refusing it at WRITE time is what stops a broken baseline being committed
    # in the first place.
    recorded = str(getattr(args, "commit", None) or "")
    if recorded and not _is_object_id(recorded):
        print(f"::error::--commit {recorded!r} is not an object id")
        print(
            f"REFUSED `--commit` must be a full object id (40 lowercase hex), not a revision "
            f"or a prefix: {recorded!r} may well resolve, but the baseline records this value "
            "as its cut point and the read path refuses anything shorter — an abbreviation "
            "also means something different once the repository grows. Use "
            "`--commit $(git rev-parse <rev>)`."
        )
        return 1
    try:
        doc = build_doc(
            args.commit, refresh_observations=getattr(args, "refresh_observations", False)
        )
    except SurfaceEvidenceUnreadable as exc:
        return _refuse(exc)

    # FAIL CLOSED ON A SILENT APPROVAL WIPE (#4598). `build_doc` writes `approval: null` on
    # every row, and CONTRIBUTING.md makes that reset the CONTROL for the per-row AUDIT
    # REFERENCE: a re-cut cannot carry an old reference onto a changed row. The TOP-LEVEL
    # approval is separate — it is READ from `docs/product/canonical-mcp-tools.md` and carried
    # forward (#4185), so a re-cut no longer re-asks for it. That control is KEPT — what is
    # removed here is only its SILENCE. On
    # 2026-09-23 a re-cut landed on `main` through PR #4043 and carried six recorded owner
    # approvals to zero, and nothing went red: `approval` is NON_DERIVABLE, so no derived
    # property compares it and no check can see the loss. Refusing here costs one flag; not
    # refusing costs an owner decision, with no artifact left to recover it from.
    #
    # A MISSING artifact has no approvals to lose — that is the first cut, and it must keep
    # working. An UNREADABLE one is different in kind: it may well carry approvals that cannot
    # be enumerated, so overwriting it destroys them without ever naming them, which is the
    # #4598 wipe again. Refuse. Never a silent overwrite of a document we could not read.
    doomed: list[tuple[str, str]] = []
    prior_doc: dict | None = None
    if MANIFEST_FILE.exists():
        try:
            prior_doc = _read_manifest(check_provenance=False)
        except SurfaceEvidenceUnreadable as exc:
            return _refuse(exc)
        doomed = _recorded_approvals(prior_doc)
    if doomed and not getattr(args, "allow_approval_reset", False):
        print(f"::error::cut would reset {len(doomed)} recorded approval(s) to null")
        print(f"REFUSED {_display(MANIFEST_FILE)} carries {len(doomed)} recorded approval(s) "
              f"that a re-cut would destroy:")
        for name, approval in doomed:
            print(f"  {name}: {approval}")
        print("  These per-row `approval` audit references are dropped; the approval itself is "
              "READ from docs/product/canonical-mcp-tools.md (#4185), so no per-row "
              "re-recording is required. Pass --allow-approval-reset to drop them deliberately.")
        return 1

    # FAIL CLOSED ON A REMOVAL THAT LEAVES NO RECORD (#12237). Runs BEFORE the write: a
    # refusal must not leave a baseline that has already forgotten the symbol, because the
    # committed baseline is the only list of what USED to be there. A first cut has no prior
    # to lose, so the ROW comparison is skipped — but the override flags are validated on
    # EVERY cut, INCLUDING the symbol-vs-removal check below (which therefore refuses any
    # override on a first cut): "no prior document" is the one tree where a waiver cannot be
    # checked against a removal, which is exactly when it should be refused.
    accounting: dict[tuple[str, str], str] = {}
    allowed = [str(s) for s in (getattr(args, "allow_removal", None) or [])]
    reason = str(getattr(args, "allow_removal_reason", "") or "").strip()
    if allowed and not reason:
        print("::error::--allow-removal requires --allow-removal-reason")
        print("REFUSED a removal override without a stated reason is an unexplained deletion; "
              "the reason is recorded in the cut output and the PR.")
        return 1
    if reason and not allowed:
        print("::error::--allow-removal-reason given with no --allow-removal")
        print("REFUSED a reason with no symbol names an override that did not happen — an "
              "operator who wrote a justification believes something was waived.")
        return 1
    problems: list[str] = []
    removals: set[tuple[str, str]] = set()
    if prior_doc is not None:
        try:
            prior_symbols = surface_symbols(prior_doc)
            derived_symbols = surface_symbols(doc)
            # What THIS cut actually removes. The override validator below must compare the
            # waiver against this set, never against `accounting`: `accounting` is also empty
            # for a symbol a REAL record already accounts for, and for a symbol whose
            # removal was refused — so a symbol-keyed comparison against it reported
            # "this cut does not remove <symbol>" for a symbol the cut DID remove, and hid
            # the specific refusal `removal_report` had produced (review of PR #12313).
            removals = prior_symbols - derived_symbols
            # ⛔ NO REMOVALS, NO RECORD TO GRADE. `removal_records()` grades the WHOLE archive
            # (`manifest_violations` + `file_violations`), so calling it unconditionally made
            # an archive defect the cut neither reads nor aggravates refuse a cut that drops
            # nothing — with a message about forgetting a symbol — e.g. the unindexed `.py`
            # left behind by an interrupted `archive_symbol` (the file is written before the
            # row is appended). Round 10 fixed the same false refusal for a MISSING manifest
            # only; gating on the removals fixes the class (review of PR #12313, round 11).
            records = (
                removal_records()
                if removals
                else {"archived": set(), "forward_raw": set(), "sdk_retired": set()}
            )
            problems, accounting = removal_report(
                prior_symbols, derived_symbols, records, allowed=set(allowed)
            )
        except SurfaceEvidenceUnreadable as exc:
            return _refuse(exc)
    # The override is validated before the removal problems, and on EVERY cut: a bogus
    # `--allow-removal` is a defect in the invocation, and reporting it as "a removal had no
    # record" would send the operator to archive a symbol instead of fixing the flag. On a
    # first cut `accounting` is empty, so every override is unexplained and refused — which
    # is the point: nothing was removed, so a waiver for it is a false record.
    # A waiver entry may be SURFACE-QUALIFIED (`tool:<symbol>` / `sdk:<symbol>`), so it is
    # matched against the removal SET (symbol AND class) through the ONE parser both callers
    # share — comparing bare symbols let a `sdk:x` waiver be "explained" by a TOOL removal of
    # the same name, and printed an override that never applied (review of PR #12313).
    unexplained = sorted(
        e for e in allowed if not any(waiver_matches(e, s, c) for (s, c) in removals)
    )
    if unexplained:
        print(f"::error::--allow-removal named symbol(s)/surface(s) this cut does not "
              f"remove: {', '.join(unexplained)}")
        print("REFUSED an override must name a symbol that is actually going away, on the "
              "surface named — an unused override is a permission granted for something "
              "else later.")
        return 1
    if problems:
        # ⛔ ONE HEADER FOR EVERY PROBLEM CLASS MADE THE REFUSAL CONTRADICT ITS OWN LINES.
        # `removal_report` also produces problems that are NOT "no record" — a redundant
        # override on a symbol that IS recorded, an ambiguous bare waiver, a contradiction —
        # and this block told the operator the row had no record and to add one, or (for the
        # ambiguity) prescribed the exact flag that had just been refused. "logged below" was
        # false too: a refused cut writes nothing below. So the record-remedy block is printed
        # only when a `no-record` problem is present; otherwise each line's own instruction is
        # the whole refusal (review of PR #12313).
        # ROWS THAT NEED A RECORD, whatever half they are missing: a plain drop, and a
        # half-record (an SDK method whose freeze or map entry is absent). Both are "no USABLE
        # record", which is what the header says, so both belong under it — typing the
        # half-record apart was only half the round-12 fix: the heading below also named one
        # of the two shapes, so a mixed refusal still described a row as something it was not
        # (review of PR #12313).
        recordable = [
            p for p in problems if getattr(p, "kind", "no-record") in ("no-record", "incomplete-record")
        ]
        if recordable:
            print(
                f"::error::cut would drop {len(recordable)} surface row(s) with no usable record"
            )
            print("REFUSED a removal must leave a record — the #11609 owner ruling archives "
                  "removed code so a mistake can be inspected later, and a re-cut cannot "
                  "tell an archived removal from a silent deletion:")
            for problem in problems:
                print(f"  {problem}")
            # NEUTRAL WORDING ON PURPOSE: the rows above may be plain drops OR half-records,
            # so a heading that names one of them mis-describes the other.
            print("  Account for each row above by exactly one of:")
            for way in REMOVAL_WAYS:
                print(f"    - {way}")
            print(f"  {SDK_PAIR_RULE}")
            print(f"  {NOT_A_REMOVAL_ROUTE}")
            # ⛔ ONLY WHERE IT CAN CLEAR. `--allow-removal` is consulted on the no-record
            # path; a contradiction row is `continue`d before the waiver is read, so printing
            # the rename remedy under a mixed refusal prescribed a flag that changes nothing
            # for the row it appears to address (review of PR #12313, round 12).
            if all(p.kind == "no-record" for p in problems):
                print("  A genuine RENAME (the old name is gone by design) passes "
                      "`--allow-removal <symbol> --allow-removal-reason <why>`; it is logged "
                      "in the cut output and belongs in the PR body.")
            return 1
        print(f"::error::cut refused this removal: {len(problems)} problem(s)")
        print("REFUSED — each line names what is wrong, and the record it names is the "
              "problem. Nothing was written:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    # An ABSENT retired half and an EMPTY one are the same absence in two spellings — the
    # `is None` test saw only the first, so a baseline whose `retired:` list had been emptied
    # (a splice or merge artefact) was compared silently while `tortoise_get` leaving the
    # declaration in that same cut went ungraded (review of PR #12313). The disclosure is what
    # that arm exists for, so it fires on emptiness, not on a missing key.
    if prior_doc is not None and not prior_doc.get("retired") and doc.get("retired"):
        print("  WARNING the committed baseline carries no `retired:` list, so the retired "
              "half could not be compared — a retired name that also left the declaration is "
              "NOT graded by this cut (and `check` reds the shape; reconcile, then re-cut)")

    MANIFEST_FILE.write_text(
        MANIFEST_HEADER
        + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110, default_flow_style=False),
        encoding="utf-8",
    )

    # NOT CARRIED FORWARD, DELIBERATELY: the per-row `approval` field is written `null`
    # exactly as `build_doc` produces it. Carrying the previous AUDIT REFERENCES over a re-cut
    # looks like protecting the audit record, and it was drafted and then REFUSED:
    # CONTRIBUTING.md step 4 makes the reset the control for the reference — a re-cut cannot
    # carry an old reference onto a changed row. Preserving them would silently reverse that
    # decision, so the reset stays
    # and the tool SAYS so, because otherwise the next reader sees the wipe as a bug and "fixes"
    # it back.
    #
    # THE TOP-LEVEL APPROVAL IS DIFFERENT (#4185): `approval_status` / `approval_principal` /
    # `approval_pr` / `approval_date` are READ from `docs/product/canonical-mcp-tools.md` by
    # `read_approval`, so a re-cut copies the owner's real approval forward without a second
    # human step. They are NOT a hand-maintained field a re-cut can blank.
    print(f"wrote {_display(MANIFEST_FILE)}")
    print(f"  tools={doc['counts']['tools']} sdk={doc['counts']['sdk_public_methods']} "
          f"retired={doc['counts']['retired']}")
    print(f"  keyword distribution: {doc['counts']['keyword_distribution']}")
    print(f"  distinct declared bindings: "
          f"{len({r['sdk_method'] for r in doc['rows'] if r.get('sdk_method')})}")
    if doomed:
        # Named, not summarised: the operator asked for this, and the rows are the loss.
        print(f"  DROPPED {len(doomed)} recorded approval(s) (--allow-approval-reset):")
        for name, approval in doomed:
            print(f"    {name}: {approval}")
    else:
        print(
            "  per-row `approval` audit references reset to null; the approval itself is READ "
            "from docs/product/canonical-mcp-tools.md (#4185)"
        )
    if accounting:
        # Named, not summarised: this is the record that a removal was accounted for, and a
        # count alone cannot be checked against the archive afterwards. The surface class is
        # printed ONLY where one symbol was removed from more than one surface, so the
        # ordinary single-surface line keeps its `symbol: record` shape.
        print(f"  accounted {len(accounting)} removal(s):")
        by_symbol: dict[str, list[tuple[str, str]]] = {}
        for (symbol, cls), label in accounting.items():
            by_symbol.setdefault(symbol, []).append((cls, label))
        for symbol in sorted(by_symbol):
            entries = by_symbol[symbol]
            if len(entries) == 1:
                print(f"    {symbol}: {entries[0][1]}")
            else:
                for cls, label in sorted(entries):
                    print(f"    {symbol} ({cls}): {label}")
    if allowed:
        print("  REMOVAL OVERRIDE (--allow-removal): "
              f"{', '.join(sorted(set(allowed)))}")
        print(f"    reason: {getattr(args, 'allow_removal_reason', '')}")
    return 0


def observed_usage(row: dict) -> str | None:
    """The checked-in usage label for a row: `in use` / `never called`, or None.

    Read from the row's OWN `used_by`, which is COMMITTED to
    `config/surface-manifest.yml` — the same value the `Used by` column prints.

    Deliberately NOT read from `~/.tortoise/analytics_fallback.jsonl`. That log is a
    machine-local artifact, so a render that consults it emits DIFFERENT bytes on a
    CI runner than on the machine that committed the file. Measured with the log
    absent (an empty `HOME`): every row lost its flag and `_never` counted all 82
    tools, so the committed document stopped matching its own generator and
    `render` + `git diff --exit-code` — the drift step — could only ever pass on one
    laptop. A plain re-cut does not consult it either (#10914 review P2): `build_doc`
    CARRIES the committed `used_by` forward and reads the log only for a row with no
    committed evidence, or under a deliberate `--refresh-observations`.
    """
    first = (row.get("used_by") or "").split(",")[0].strip()
    if first == "agents":
        return "in use"
    if first == "never called":
        return "never called"
    return None


def response_field_rows(doc: dict) -> list[str]:
    """The `recorded response fields` table's data rows, rendered from the manifest.

    ONE definition, read by both `cmd_render` and the test that holds the committed
    document to it. The table had no in-repo comparison — only the `surface-guard` CI
    job's render+diff noticed a hand-edit — so a sentence written straight into
    `docs/product/mcp-sdk-surface.md` survived review after review and went red only in
    CI (#9658). A second copy of this normalisation in the test would drift from this
    one and clear exactly that hand-edit, so the test imports this function.
    """
    rows: list[str] = []
    block = doc.get("response_fields")
    for rf in (block if isinstance(block, list) else []):
        # `check` REPORTS a malformed block or entry; skip it here rather than crashing the
        # renderer (the two must not disagree about the same input) or emitting a blank
        # placeholder row for an entry that is missing `response` or `field`.
        if not isinstance(rf, dict) or not rf.get("response") or not rf.get("field"):
            continue
        # Normalise whitespace AND pipes in every cell: an unescaped `|` splits the markdown
        # row, and an embedded newline (e.g. a YAML literal block) breaks it the same way.
        cells = [
            " ".join(str(rf.get(k, d) or d).split()).replace("|", "/")
            for k, d in (
                ("response", ""),
                ("field", ""),
                ("emitted_when", ""),
                ("unchanged_when_off", "yes"),
            )
        ]
        rows.append(f"| `{cells[0]}` | `{cells[1]}` | {cells[2]} | {cells[3]} |")
    return rows


def cmd_render(args: argparse.Namespace) -> int:
    """manifest -> the list the owner reviews.

    Grouped so that SIMILAR ENTRIES SIT TOGETHER — that is the whole point of the
    document. Families are emitted in rank order; inside a family, rows are
    ordered by job keyword and then by cluster, so a group of near-duplicates
    lands on consecutive lines and can be judged as a group rather than hunted
    for across the whole list.
    """
    try:
        doc = _read_manifest()
    except SurfaceEvidenceUnreadable as exc:
        return _refuse(exc)
    # ROWS THE RENDERER CANNOT READ ARE A REFUSAL, not a traceback: `cmd_render` indexes
    # `family_rank` / `keyword_rank` / `cluster` / `family` directly, and it used to raise
    # `KeyError: 'family_rank'` on a manifest that `check` merely reported as malformed.
    _, malformed = _partition_rows(doc["rows"])
    if malformed:
        return _refuse(
            SurfaceEvidenceUnreadable(
                "the baseline carries rows this tool cannot render (re-cut it): "
                + "; ".join(malformed[:5])
            )
        )
    rows = doc["rows"]
    tools = [r for r in rows if not r["name"].startswith("sdk:")]
    sdk = [r for r in rows if r["name"].startswith("sdk:")]
    # A TOOL-LESS BASELINE IS A RE-CUT GONE WRONG, not a document: every section below
    # quantifies over `tools` (the surface size, the 14-of-N contrast, the uncalled count),
    # so rendering one would publish arithmetic over a surface that does not exist — "all 0
    # tools at once", "the other -14", "0 of 0". `check` already rejects such a baseline.
    if not tools:
        return _refuse(
            SurfaceEvidenceUnreadable(
                "the baseline carries no tool rows, so there is no advertised surface to "
                "render — re-cut it from the registry"
            )
        )

    tools.sort(key=lambda r: (r["family_rank"], r["keyword_rank"], r["cluster"] or r["name"], 0 if r["canonical"] in (True, None) else 1, r["name"]))

    families: dict[str, list[dict]] = {}
    for r in tools:
        families.setdefault(r["family"], []).append(r)
    classes: dict[str, list[dict]] = {}
    for r in sdk:
        classes.setdefault(r.get("class") or "?", []).append(r)

    out: list[str] = []
    add = out.append
    # Front matter is emitted HERE, not written into the .md, because the file is
    # regenerated: anything hand-added to it is stripped on the next render.
    add("---")
    add('title: "The agent-facing surface — every MCP tool and public SDK method"')
    add("type: synthesis")
    add("domain: capability")
    add("doc_status: live")
    add(f"created: {str(doc.get('issue') and '2026-09-17') or '2026-09-17'}")
    add("ownedBy: epistemic-team")
    add("generated_from: config/surface-manifest.yml")
    add(f"issue: {doc.get('issue')}")
    add("---")
    add("")
    add("# The agent-facing surface — for review")
    add("")
    add(f"Cut against `{doc['cut_at_commit'][:9]}`, the default-branch commit this baseline "
        f"was cut from. **{len(tools)} MCP tools · {len(sdk)} SDK methods.**")
    add("")
    add("Generated from `config/surface-manifest.yml`. **Do not edit this file** — change the manifest.")
    add("")
    add("## ⛔ Changing this surface needs Daniel's approval")
    add("")
    add("**You may not add, remove, or rename an MCP tool or a public SDK method without approval")
    add("from Daniel.** The surface is the contract every agent and customer integration is built")
    add("on, so a change materially affects customer outcomes. Ask Daniel first — repo `AGENTS.md`")
    add("→ \"USER QUESTIONS\" / \"DECISION RELAY\" — then follow `CONTRIBUTING.md` ('The MCP tool")
    add("surface and public SDK methods cannot grow by accident').")
    add("")
    add("**The gates are drift controls, not approval gates.** `tools/surface-guard.py` and")
    add("`tools/surface_manifest.py check` compare the live declaration against this baseline; a")
    add("change that updates the registry and this baseline together **passes both**. They catch an")
    add("*unrecorded* change — they cannot tell an approved addition from an unapproved one.")
    add("Daniel's review is what carries the approval.")
    add("")
    add("## How to read this")
    add("")
    add("- **Grouped, not alphabetised.** Tools are grouped by the part of the system they belong to,")
    add("  and inside each group near-duplicates sit next to each other, so a cluster can be judged as")
    add("  a group.")
    add("- **`reaches` is the dependency question answered:** which SDK method the tool actually calls,")
    add("  or `handler-served` when it declares no SDK call at all.")
    add("- **`⚠ FALSE DECLARATION`** marks an entry that names an SDK method which does not exist. The")
    add("  tool still works; the declaration is wrong.")
    add("- **`DEPRECATED`** marks an alias kept for compatibility. **Retired** names are listed at")
    add("  the end: they are off the advertised surface, still answer, and warn the caller with the")
    add("  replacement (#3883).")
    add("- **`used by`** answers *what is this for*: which eval harness, piece of tooling, skill, or")
    add("  doc references it by name — and whether agents actually call it.")
    add("- **`recomm.`** is a vocabulary of five values, and it is a **proposal**, not a verdict:")
    add("  `keep` · `kill` · `merge` · `fix-declaration` · `review`. `review` means *look at this*,")
    add("  not *cut this*.")
    add("- **`never called`** means no call appears in our own tool-call log. That is evidence about")
    add("  *our* usage, not about whether the tool is useful to a customer.")
    add("")
    add("## At a glance")
    add("")
    add("| Group | Tools |")
    add("|---|---|")
    for fam in sorted(families, key=lambda f: families[f][0]["family_rank"]):
        add(f"| {fam} | {len(families[fam])} |")
    add(f"| **total** | **{len(tools)}** |")
    retired_rows = [r for r in (doc.get("retired") or []) if isinstance(r, dict)]
    if retired_rows:
        add(f"| **retired — callable, warns (#3883)** | **{len(retired_rows)}** |")
    add("")
    add("| SDK methods, by who reaches them | Count |")
    add("|---|---|")
    for cls in ("agent-reachable", "internal", "eval-only", "control-plane", "no-caller-found", "?"):
        if classes.get(cls):
            add(f"| {cls} | {len(classes[cls])} |")
    add(f"| **total** | **{len(sdk)}** |")
    add("")
    add("---")
    add("")
    add("## What the gate compares — and what it does not")
    add("")
    add("The freeze this document serves is on **tools and endpoints**. The gate compares the live")
    add("declaration against the frozen baseline below. It fails on three kinds of name-level change:")
    add("")
    add("1. **A new tool** — an MCP tool that is not in the frozen baseline.")
    add("2. **A new endpoint** — a public SDK method that is not in the frozen baseline.")
    add("3. **A frozen-baseline entry disappearing** — a tool or endpoint that is on the list and is no")
    add("   longer offered.")
    add("")
    add("It also fails when the implementation behind a frozen-baseline name is swapped for a different")
    add("one, because that changes the endpoint while leaving the name intact.")
    add("")
    add("**This frozen baseline is a snapshot of what EXISTS, not the owner-approved TARGET.** The")
    add("target is recorded in [`canonical-mcp-tools.md`](canonical-mcp-tools.md) and")
    add("[`beta-sdk-surface.md`](beta-sdk-surface.md); `tools/surface-target-check.py` reds while the")
    add("live surface differs from it.")
    add("")
    add("Those are the name-level checks. The gate **additionally** fails on several served-surface and")
    add("evidence-integrity checks — an unapproved server transform, a moved SDK binding, a changed")
    add("HTTP-vs-stdio serving, a registry count that no longer matches the baseline, an exemption that")
    add("became reachable, and a missing or malformed baseline. They are listed in full in")
    add("[`CONTRIBUTING.md`](../../CONTRIBUTING.md); read them there rather than inferring the gate's whole")
    add("scope from this summary.")
    add("")
    add("**What is not a gate failure: an added field on an existing response.** A field that is off")
    add("by default, and leaves the response unchanged when it is off, is neither a new tool nor a new")
    add("endpoint, so it is not a name-level change and does not gate **as an addition**. The precedent")
    add("is in the tree: the W4 why-layer key on the `tortoise_analyze` response is written only when")
    add("`TORTOISE_W4_ENRICHMENT` is truthy (1/true/yes/on; unset or `0` means off) — and every")
    add("other field stays byte-identical when it is absent (`tortoise/mcp_server.py`,")
    add("`tortoise/why.py`). That is a different `why` key from")
    add("the one on `volunteer_context`, which is present by default.")
    add("")
    add("**One qualification, because the gate also fingerprints implementations.** The gate records a")
    add("digest of each registered tool's own code object, so a field added *inside a tool's handler*")
    add("changes that tool's fingerprint — and, since the fingerprint covers the function's source")
    add("position, the fingerprint of every tool defined after it — and reds the gate, correctly,")
    add("as a changed implementation rather than a new tool. Add response fields in the SDK or")
    add("assembly layer, not inside a tool function, and the carve-out holds.")
    add("")
    add("**And it must still be recorded.** Every such addition goes in the table below, so this")
    add("document stays the single source of truth. Two of `check`'s properties defend that record: an")
    add("empty or missing `response_fields` block is a failure, and every entry must name a tool or")
    add("endpoint that exists in this manifest — so the record can be neither deleted nor left")
    add("unanchored. What no check *can* see is an off-by-default field that nobody recorded at all:")
    add("nothing inspects response bodies at runtime, so that half is a reviewing obligation and this")
    add("document says so rather than implying the machine guarantees it. The carve-out is about what")
    add("the gate *fails* on — not about what goes *unrecorded*.")
    add("")
    add("### Recorded response fields")
    add("")
    add("| Response | Field | Emitted when | Unchanged when off |")
    add("|---|---|---|---|")
    for _row in response_field_rows(doc):
        add(_row)
    add("")
    add("A field belongs in that table from the moment it is added — an off-by-default field that is")
    add("not recorded here has no approval behind it, and the carve-out does not cover it.")
    add("")
    add("---")
    add("")
    add("## The MCP tools")
    add("")

    for fam in sorted(families, key=lambda f: families[f][0]["family_rank"]):
        members = families[fam]
        add(f"### {fam} — {len(members)}")
        add("")
        add("| Tool | Does | Reaches | Used by | Recomm. | Status and rationale |")
        add("|---|---|---|---|---|---|")
        for r in members:
            flags = []
            if "WHICH DOES NOT EXIST" in (r.get("dependency") or ""):
                flags.append("⚠ FALSE DECLARATION")
            if r.get("lifecycle") == "deprecated alias":
                flags.append("DEPRECATED")
            if r.get("cluster"):
                flags.append(f"group: `{r['cluster']}`" if r.get("canonical") else f"in group `{r['cluster']}`")
            # From the row's checked-in `used_by` — never from the machine-local call
            # log, so the flag is identical on every checkout (see `observed_usage`).
            # The markers were once inverted: the 64 tools with zero observed calls were
            # labelled "in use" and the 35 that actually appear were labelled "never
            # called" — contradicting both the legend and each row's own `used_by` cell.
            usage_label = observed_usage(r)
            if usage_label:
                flags.append(usage_label)
            if r.get("proposed"):
                flags.append(f"**proposed: move to `{r['recommended_family']}`**")
            reaches = r.get("sdk_method") or "handler-served"
            if r.get("sdk_method") and "WHICH DOES NOT EXIST" in (r.get("dependency") or ""):
                reaches = f"~~{r['sdk_method']}~~ (missing)"
            rec = r.get("recommendation") or "keep"
            # Escape pipes HERE too: a description containing `|` (e.g. tortoise_get's
            # "type: point|operator|entity|event|session") split the row into extra cells and
            # shifted every column after it — 6 of 99 rows were malformed for this reason.
            does = " ".join((r.get("job") or "").split()).replace("|", "/")
            if len(does) > 180:
                does = does[:177].rstrip() + "…"
            rationale = " ".join((r.get("reason") or "").split()).replace("|", "/")
            if r.get("cluster_note"):
                rationale += " · " + r["cluster_note"]
            status = " · ".join(flags) if flags else "—"
            add(
                f"| `{r['name']}` | {does} | {reaches} | {r.get('used_by') or ''} | **{rec}** "
                f"| {status} — {rationale} |"
            )
        add("")

    retired_rows = [r for r in (doc.get("retired") or []) if isinstance(r, dict)]
    if retired_rows:
        add("---")
        add("")
        add(f"## Retired names — {len(retired_rows)} (off the advertised surface, not out of the product)")
        add("")
        add("A retired name is **no longer advertised**: it is absent from `tools/list`, so it is not")
        add("counted above. It is **not gone** — calling it still works and now returns an answer that")
        add("carries a warning naming the replacement (#3883). The ability to warn is what makes a")
        add("retirement safe; a retired name that stopped resolving would be a silent removal.")
        add("")
        add("| Retired name | Use instead | SDK method it declared | Was |")
        add("|---|---|---|---|")
        from tortoise.sdk import TortoiseSDK as _SDK

        for r in sorted(retired_rows, key=lambda x: str(x.get("name"))):
            was = "http" if r.get("http_policy") else "stdio-only"
            declared = r.get("sdk_method") or "—"
            if r.get("sdk_method") and not hasattr(_SDK, str(r["sdk_method"])):
                # The retired alias declared an SDK method that never existed
                # (`tortoise_health` -> `health`). Do not assert it is public: the
                # live table marked this \u26a0 FALSE DECLARATION, and the retired
                # table must not silently upgrade a false declaration to a fact.
                declared = f"~~{declared}~~ (no such method)"
            add(
                f"| `{r.get('name')}` | `{r.get('use_instead')}` | "
                f"{declared if declared.startswith('~~') else '`' + str(declared) + '`'} | {was} |"
            )
        add("")

    add("---")
    add("")
    add("## The SDK methods")
    add("")
    add("Grouped by **who reaches them**. This is the ranking that matters for cutting: everything in")
    add("`agent-reachable` is called by an agent today; everything below it is not.")
    add("")
    for cls in ("agent-reachable", "internal", "eval-only", "control-plane", "no-caller-found", "?"):
        members = classes.get(cls)
        if not members:
            continue
        add(f"### {cls} — {len(members)}")
        add("")
        add("| Method | Does / depends on | Used by | Recomm. | Rationale |")
        add("|---|---|---|---|---|")
        for r in sorted(members, key=lambda x: (x.get("dependency") or "", x["method"])):
            add(
                f"| `TortoiseSDK.{r['method']}` | {(r.get('dependency') or '').replace('|', '/')} | "
                f"{r.get('used_by') or ''} | **{r.get('recommendation') or 'keep'}** | "
                f"{(r.get('reason') or '').replace('|', '/')} |"
            )
        add("")

    add("---")
    add("")
    by_rec: dict[str, list[dict]] = {}
    for r in tools:
        by_rec.setdefault(r.get("recommendation") or "keep", []).append(r)

    add("## What I recommend, in one place")
    add("")
    add("Every row above carries its own recommendation. This is the same thing gathered up, because")
    add(f"deciding {len(tools)} rows one at a time is not a reasonable thing to ask of you.")
    add("")
    add(f"**But these {len(tools)} lines are not all the same kind of statement, and the difference matters:**")
    add("")
    add(f"- **{len([r for r in tools if r.get('basis') == 'decided'])} rows execute a decision Tortoise has already made.** The declaration itself")
    add("  names one tool canonical and the other a duplicate. Correcting these follows from what we")
    add("  already decided to be. **These are recommendations in the strong sense.**")
    add(f"- **{len([r for r in tools if r.get('basis') == 'observed'])} rows only describe what is being done** — called, referenced, or not.")
    add("  Usage is not a decision, and it does not get to decide what we are. A tool nobody calls may")
    add("  be exactly what we decided Tortoise is, for a user we have not reached yet; a tool everyone")
    add("  calls may still be the wrong capability. **These rows are evidence for your decision, not a")
    add("  substitute for it.** I have stopped labelling them keep/kill, because that was me treating")
    add("  what is as what ought to be.")
    add("")

    kills = by_rec.get("kill", [])
    if kills:
        add(f"### Cut — {len(kills)} tools that are already replaced")
        add("")
        for r in kills:
            add(f"- `{r['name']}` — {r.get('reason')}")
        add("")
        add(f"**These {len(kills)} must not be removed silently. The caller is told first.** Today they emit no")
        add("warning at all (that gap is #3876). That is the same failure as a retention promise that is")
        add("quietly incomplete: a caller whose tool stops existing is being told something untrue by")
        add("omission. So the order is **deprecation warning first (#3876), removal second** — and if the")
        add("warning does not land, these stay. A silent removal is not an option this lane will propose.")
        add("")

    merges = by_rec.get("merge", [])
    if merges:
        add(f"### Fold in — {len(merges)} tools that duplicate a sibling")
        add("")
        add("Each of these is already covered by the tool named after the arrow, because that tool's")
        add("**own description** says it consolidates this one. The caller keeps working; we stop")
        add("shipping two names for one job. This is the single biggest reduction available and the")
        add("only one where the evidence is the code's own words.")
        add("")
        by_target: dict[str, list[str]] = {}
        for r in merges:
            by_target.setdefault(r.get("cluster_canonical") or r.get("cluster") or "?", []).append(r["name"])
        for target, names in by_target.items():
            add(f"- **`{target}` absorbs {len(names)}:** " + ", ".join(f"`{n}`" for n in sorted(names)))
        add("")

    fixes = by_rec.get("fix-declaration", [])
    if fixes:
        add(f"### Correct — {len(fixes)} tools whose declaration is wrong")
        add("")
        for r in fixes:
            add(f"- `{r['name']}` — {r.get('reason')}")
        add("")

    reviews = by_rec.get("review", [])
    if reviews:
        add(f"### Nothing observable uses these — {len(reviews)} tools (observed, not a verdict)")
        add("")
        add("These are never called over MCP **and** referenced by nothing except documentation. That")
        add("is the weakest evidence in this document: our telemetry only sees *our* usage, and a tool")
        add("can be documentation-only because it is new, because it is for a customer we have not")
        add("reached, or because it is genuinely dead. I cannot tell those apart from here.")
        add("")
        add(", ".join(f"`{r['name']}`" for r in reviews) + ".")
        add("")

    kept = by_rec.get("keep", [])
    add(f"### In use today — {len(kept)} tools (observed, not a verdict)")
    add("")
    add("Called by agents, or referenced by an eval harness, internal tooling, or a skill.")
    add("**This says we use them; it does not say we should.** Which of these we keep is a statement")
    add("about what Tortoise is, and that is yours to make, not a reading of our own logs.")
    add("")
    # The count of CONCRETE-ACTION sections that actually RENDER — Cut (kills),
    # Fold in (merges), Correct (fixes). It was hardcoded to "three", which went
    # false the moment a bucket emptied and left the sentence referring to
    # actions the document does not contain.
    _n_actions = sum(1 for _bucket in (kills, merges, fixes) if _bucket)
    _action_word = {0: "no", 1: "the one", 2: "the two", 3: "the three"}[_n_actions]
    add(f"**Net effect if you accept {_action_word} concrete "
        f"action{'s' if _n_actions != 1 else ''} and none of the judgement calls:**")
    add(f"{len(tools)} tool names → **{len(tools) - len(kills) - len(merges)}**. Nothing an agent can")
    add("call disappears — the folded names are the same capability under a name the code already")
    add("designates as canonical.")
    add("")
    add("## On the numbers alone")
    add("")
    dead = [r for r in tools if "WHICH DOES NOT EXIST" in (r.get("dependency") or "")]
    if dead:
        add(f"- **{len(dead)} entries declare an SDK method that does not exist:** "
            + ", ".join(f"`{r['name']}`" for r in dead) + ".")
    else:
        # Guard the empty case: `", ".join([])` is `""`, which rendered a
        # dangling sentence tail — "...does not exist:** ." (#4583).
        add("- **0 entries declare an SDK method that does not exist.**")
    _ncf = classes.get("no-caller-found", [])
    _rest = [r for r in _ncf if "tenant-rest" in (r.get("dependency") or "")]
    add(f"- **{len(_ncf)} SDK methods are reached by no agent path** — no MCP tool, no CLI verb, no "
        f"tool handler, which is what `no-caller-found` means here. Of those, **{len(_rest)}** are "
        "called from the tenant REST surface, so they are reachable by a client but not by an agent "
        "inside the gate; the remaining {0} have no caller outside tests at all.".format(len(_ncf) - len(_rest)))
    add(f"- **{len(classes.get('internal', []))} SDK methods are reachable only from our own engine, our "
        "tooling, or the tenant REST surface** — reachable by something, but by no agent path inside "
        "the gate. (Methods reachable ONLY from tenant REST are counted in the class above, not here.)")
    evals = [r["name"] for r in tools if "eval-harness" in (r.get("used_by") or "")]
    add(f"- **Only {len(evals)} tools are referenced by an eval harness** ({', '.join('`'+e+'`' for e in evals)}) "
        "— so almost none of this surface is covered by an evaluation.")
    add("")
    add("## The surface size — measured, and argued to you")
    add("")
    add(f"The comparable research measured one thing: **we advertise all {len(tools)} tools at once**, where no")
    add(f"comparable agent-memory system advertises more than {LARGEST_COMPARABLE} (Cognee 4 · Mem0 9 · Graphiti 13 ·")
    add(f"Letta {LARGEST_COMPARABLE}). That is a fact about the field and a fact about us.")
    add("")
    add("**I first read it as contradicting a decision, and that was wrong — in the direction that is")
    if len(tools) > 14:
        add("easy to miss.** I said an agent shown 14 of our tools and not told about the other "
            f"{len(tools) - 14} is being told")
    else:
        # The contrast needs a remainder to name: with a small advertised set (or none) the
        # subtraction renders "the other -4", a quantity about no surface at all.
        add("easy to miss.** I said an agent shown only part of our tools is being told")
    add("something \"quietly incomplete\", and dropped the candidate as one that could not be adopted at")
    add("all. But the decision I cited — *\"delete what we can · de-identify what must persist · disclose")
    add("what nobody can remove\"* — governs **promises about user data**. It says nothing about how many")
    add("tools we advertise. I got there by analogy, not by a decision, which is the same fault in the")
    add("opposite direction: reaching for a ruling as authority where the ruling's scope does not reach.")
    add("**No decision governs advertised tool count. The contradiction test returns nothing to")
    add("contradict.**")
    add("")
    add("So this is a live question, and the honest thing is to argue it rather than drop it. Here is the")
    add("argument, both ways, with the weak parts named.")
    add("")
    _never = [r for r in tools if observed_usage(r) == "never called"]
    _measured = [r for r in tools if observed_usage(r) is not None]
    # THE COUNT IS A CLAIM ABOUT A MEASUREMENT, so it is made only when EVERY tool row carries
    # a flag. (A tool-less baseline never reaches here — `cmd_render` refuses it above, since
    # every section quantifies over `tools`.)
    # `observed_usage()` reads the row's own committed `used_by`; its first token names which
    # of the two things the flag records — that a call appears in our own tool-call log
    # (`agents`) or that none does (`never called`). It returns None when the row carries
    # neither token, which is what a baseline cut with no call log available looks like.
    # `any(...)` was not enough, because the denominator is ALL rows: `used_by` sits in
    # NON_DERIVABLE_ROW_KEYS (it is excluded from the drift comparison precisely because it is
    # machine-influenced), so a PARTIALLY flagged baseline can reach this render, and a count of
    # flagged rows divided by every row states a quantity over rows nobody assessed. The old
    # unconditional form was worse still: with no flag anywhere it published "Two thirds of what
    # we advertise has never been called by anything, including us (0 of 82)" — a negative
    # nobody measured, in the document the owner reads to decide what to cut, and
    # self-contradicting besides. The prose also now reports the measured quantity instead of
    # the hardcoded "Two thirds", which contradicted its own parenthetical at any other count.
    #
    # The wording is SCOPED TO THE EVIDENCE: the flag says whether a call appears in OUR
    # tool-call log (docs/product/mcp-sdk-surface.md's own glossary says so), which is evidence
    # about our usage and not about whether a tool is useful to a customer — and it says that
    # about the rows that carry a flag, never about the ones that do not. Neither branch may
    # name a CAUSE for a flag's absence — `observed_usage` cannot observe one, and asserting an
    # unmeasured cause is the same defect this guard removes. (Residual carried from the #5456
    # draft; the stance is CONTRIBUTING.md's own — "stated as one rather than implied to be
    # automatic".)
    if len(_measured) == len(tools):
        add(f"**The case for pinning a small advertised set.** {len(_never)} of the {len(tools)} tools we advertise have no")
        add("call in our own tool-call log. Mainstream clients cap the tools they will show — a")
    else:
        _unassessed = len(tools) - len(_measured)
        add("**The case for pinning a small advertised set.** This baseline carries an `in use` / `never called`")
        add(f"flag on {len(_measured)} of its {len(tools)} tool rows — the other {_unassessed} carry none — so **no count")
        add("of uncalled tools is asserted here**: a count over all of them would include rows nobody assessed.")
        add("The flag records one thing: whether a call appears in our own tool-call log (`in use`) or none")
        add("does (`never called`) — evidence about our usage, not about whether a tool is useful to a")
        add("customer. A row carrying no flag records neither. Mainstream clients cap the tools they will")
        add("show — a")
    _our_set_is_larger = len(tools) > LARGEST_COMPARABLE
    # The comparable claim is a comparison against `len(tools)`, so it is guarded like the cap
    # clause: below the largest comparable it would be FALSE (Letta pins 18, against our N).
    _comparable_clause = (
        "Every comparable we studied pins a smaller set,"
        if _our_set_is_larger
        else f"We are not the largest: the biggest comparable, Letta, pins {LARGEST_COMPARABLE}, against our {len(tools)},"
    )
    if len(tools) > 40:
        add(f"reported 40 in Cursor — so a client that caps there sees at most 40 of these {len(tools)}, while we")
        add(f"pay context for all of them on every turn. {_comparable_clause}")
    else:
        # The cap argument needs a surface bigger than the cap: with 40 or fewer advertised tools a
        # client that caps at 40 sees ALL of them, so "at most 40 of these 10" is arithmetic over a
        # population with no 40 in it. This document argues for shrinking the surface, so the small
        # case is on the intended path, not hypothetical.
        add("reported 40 in Cursor — a cap the whole surface fits inside, so no tool is hidden by it,")
        add(f"and we pay context for all {len(tools)} of them on every turn. {_comparable_clause}")
    add("and the pattern is not novel here: `tortoise_recall` is already one tool with four modes and")
    add("`tortoise_get_entity` already absorbed five of the six fetch-by-id getters — the sixth,")
    add(f"`tortoise_get_session`, is proposed for merge. Deferring the rest keeps all {len(tools)} callable.")
    add("")
    add("**The case against, which is real and not a formality.** Tortoise is genuinely broader than the")
    add("comparables — a graph memory *and* a reasoning engine with sessions, sources and mining — so some")
    add("of the gap is real scope, not fat. Tool search adds a hop and a failure mode: an agent that does")
    add("not know a capability exists may not think to look for it, and \"the tool existed but was not")
    add("advertised\" is a worse failure than a long list. And the fix is not obviously worth its cost —")
    # Computed, not hardcoded: only the `kill` + `merge` rows REMOVE a name. The
    # `fix-declaration` rows correct a dead `sdk_method` string and take no name off the
    # surface, so counting every `decided` row would overstate the shrink. Both this
    # count and the "→ N" figure above are derived from the rows, never literals — an
    # earlier hardcoded version went stale the moment 4035 repaired the declarations and
    # the `fix-declaration` bucket emptied.
    _name_removing = [r for r in tools if r.get("recommendation") in ("kill", "merge")]
    add(f"the {len(_name_removing)} rows above remove names where the declaration already says a name is redundant, which")
    add("shrinks the surface without inventing a discovery mechanism.")
    add("")
    add("**The evidence does not settle it.** The comparison is solid; the thresholds behind \"too many")
    add("tools\" are practitioner opinion, not specification — the MCP spec says nothing about tool count.")
    add("So this goes to you as an argument, which is the whole point: I am not adopting it, and I am not")
    add("quietly dropping it either.")
    add("")
    add("**Whichever way you decide, the measurement stands.** Refusing a change is not a verdict on the")
    add(f"finding — the numbers do not stop being true because {len(tools)} stays advertised, and they do not become")
    add("an instruction because the surface is tiered. They are what a later reopen of this question would")
    add("be argued from, and they are recorded here so that argument does not have to be rebuilt. Full")
    add("evidence: `docs/research/2026-09-17-3863-agent-memory-surfaces.md`.")
    add("")
    RENDERED_FILE.parent.mkdir(parents=True, exist_ok=True)
    RENDERED_FILE.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {RENDERED_FILE.relative_to(ROOT)} ({len(tools)} tools, {len(sdk)} SDK methods)")
    return 0


REQUIRED_ROW_COLUMNS = ("family", "family_rank", "keyword", "keyword_rank", "cluster")


def _partition_rows(rows: list) -> tuple[list[dict], list[str]]:
    """(the non-`sdk:` rows the comparisons can read, why each other row cannot be read).

    A row that HAS a name but lacks a column the properties index is malformed EVIDENCE,
    not a comparison result: it used to escape as a bare KeyError/TypeError (`r["keyword"]`,
    then the family_rank sort) instead of the `::error::` refusal the contract promises.
    Both readers share this one predicate — `cmd_check` reports what it cannot compare and
    `cmd_render` refuses — because two readers with two opinions is how `cmd_render` came to
    trace back (`KeyError: 'family_rank'`) on a manifest the check merely reported.
    """
    problems: list[str] = []
    usable: list[dict] = []
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("name"), str) or not r["name"]:
            problems.append(f"malformed row (not a mapping with a non-empty string name): {r!r}")
            continue
        if r["name"].startswith("sdk:"):
            continue
        missing = [k for k in REQUIRED_ROW_COLUMNS if k not in r]
        if missing:
            problems.append(f"malformed row {r['name']!r}: missing {missing} — re-cut the baseline")
            continue
        bad = [k for k in ("family_rank", "keyword_rank") if not isinstance(r[k], int)]
        if r["cluster"] is not None and not isinstance(r["cluster"], str):
            bad.append("cluster")
        if bad:
            problems.append(
                f"malformed row {r['name']!r}: {bad} have types the check cannot order — "
                "re-cut the baseline"
            )
            continue
        usable.append(r)
    return usable, problems


def cmd_check(args: argparse.Namespace) -> int:
    """AC13's lint: eleven structural properties, a twelfth derived from the code, a
    thirteenth that the DECLARED surface is the approved one (#10034), and a fourteenth
    that the recorded `cut_at_commit` RESOLVES (#11057).

    The fourteenth is `--provenance-strict`-gated on purpose: `check` is documented as safe
    to run anywhere, and a `fetch-depth: 1` checkout cannot answer it — so the default arm
    reports the reason (`::notice::`) and the one step that owns its own git depth asks for
    the strict verdict."""
    try:
        order = load_order()
        doc = _read_manifest()
        from tortoise.tool_registry import GROUP_BY_NAME, RETIRED_TOOL_REGISTRY, TOOL_REGISTRY
    except SurfaceEvidenceUnreadable as exc:
        return _refuse(exc)
    except Exception as exc:  # an unimportable declaration is a refusal
        return _refuse(
            SurfaceEvidenceUnreadable(
                f"could not import the surface declaration: {type(exc).__name__}: {exc}"
            )
        )

    # `cut_at_commit` is PROVENANCE, excluded from the drift comparison, so
    # `_read_manifest` is its only gate — and there it can answer `unprovable` when this
    # clone does not hold the object. That arm is NAMED here rather than left silent: a
    # verification that did not happen must never read as one that passed. (("a check
    # nobody runs reports to nobody") — the same reason `changes` prints that the capstone
    # ordering gate is UNPROVABLE on a shallow checkout rather than quietly skipping.)
    verdict, why = cut_commit_verdict(doc["cut_at_commit"])
    if verdict == CUT_UNPROVABLE:
        print(
            f"surface_manifest: `cut_at_commit: {doc['cut_at_commit']}` was NOT VERIFIED — "
            f"{why}. Re-run `check` in a full clone (or with the object fetched) to prove "
            "the provenance resolves."
        )

    verbs = order["keywords"]
    tokens_to_keyword = {t: kw for kw, toks in order["tokens"].items() for t in toks}
    # Malformed rows must fail CLEANLY, and this must run before any row access. The
    # predicate is shared with `cmd_render` (`_partition_rows`), so neither reader can
    # half-fix it. Properties 1-9 index these keys directly, so a row that HAS a name but
    # lacks one of them is malformed EVIDENCE, not a comparison result — it is reported and
    # excluded rather than allowed to raise (verified: a row missing `keyword` or carrying a
    # non-integer `family_rank` escaped as KeyError/TypeError instead of the refusal).
    rows, malformed = _partition_rows(doc["rows"])
    problems: list[str] = list(malformed)

    # The `response_fields` block is rendered by `cmd_render` and records fields that are
    # deliberately outside the gate. Validate its shape here so a malformed entry is
    # REPORTED rather than crashing the renderer with a bare KeyError.
    _rf_block = doc.get("response_fields")
    malformed_rf = [
        rf
        for rf in (_rf_block if isinstance(_rf_block, list) else [])
        if not isinstance(rf, dict) or not rf.get("response") or not rf.get("field")
    ]
    for rf in malformed_rf:
        problems.append(f"malformed response_fields entry (needs `response` and `field`): {rf!r}")
    # The `response_fields` record must not be able to VANISH, and must be ANCHORED to the
    # surface it describes. Without these two the doc's promise — that an off-by-default
    # addition "cannot quietly become the way the surface grows" — is prose only: the block
    # can be deleted to empty and every command in this repo stays green. Reproduced before
    # writing this: delete the block, and `surface-guard.py` exits 0, `check` says OK, every
    # test passes.
    if not isinstance(_rf_block, list) or not _rf_block:
        problems.append(
            "the `response_fields` block is missing or empty — it is the single source of truth "
            "for off-by-default response additions and must not be deletable"
        )
    else:
        # EVERY row is a candidate anchor, not only the non-`sdk:` rows the comparisons
        # read — an endpoint response is anchored by its `sdk:<method>` row, and
        # `_partition_rows` drops exactly those. Reading `rows` here made the anchor
        # property unable to see any SDK endpoint at all.
        _row_names = {
            str(r["name"]) for r in doc["rows"] if isinstance(r, dict) and "name" in r
        }
        for rf in _rf_block:
            if not isinstance(rf, dict) or not rf.get("response"):
                continue  # its shape is already reported above
            _resp = str(rf["response"])
            if _resp not in _row_names and f"sdk:{_resp}" not in _row_names:
                problems.append(
                    f"response_fields entry names {_resp!r}, which is not a tool or endpoint in "
                    f"this manifest — the record must be anchored to the surface it describes"
                )

    # 1. totality — every tool row carries exactly one derived keyword
    # 2. derivation agreement
    counts: dict[str, int] = {k: 0 for k in verbs}
    for r in rows:
        entry = next((e for e in TOOL_REGISTRY if e.name == r["name"]), None)
        if entry is None:
            problems.append(f"row {r['name']} is in the manifest but not in the declaration")
            continue
        want = derive_keyword(entry.name, entry.description or "", tokens_to_keyword)
        counts[want] += 1
        if r["keyword"] != want:
            problems.append(f"{r['name']}: stored keyword {r['keyword']!r} != derived {want!r}")
        if r["keyword_rank"] != verbs[want]:
            problems.append(f"{r['name']}: keyword_rank {r['keyword_rank']} != {verbs[want]}")

    # 3. no dead keyword — 4. distribution agreement
    for kw in verbs:
        if counts[kw] == 0:
            problems.append(f"dead keyword: no row derives {kw!r}")
    baseline = order.get("baseline_counts") or {}
    if baseline and baseline != counts:
        problems.append(f"distribution moved: computed {counts} != baseline {baseline}")

    # 5. order/contiguity for the key
    def key(r):
        return (
            r["family_rank"],
            r["keyword_rank"],
            r["cluster"] or r["name"],
            0 if r.get("canonical") in (True, None) else 1,
            r["name"],
        )

    want_order = sorted(rows, key=key)
    if [r["name"] for r in want_order] != [r["name"] for r in rows]:
        first = next(
            (a["name"] for a, b in zip(rows, want_order, strict=False) if a["name"] != b["name"]), "?"
        )
        problems.append(f"row order is not the declared key order (first divergence at {first})")

    # 6. cluster-family agreement — 7. cluster adjacency
    for cluster in {r["cluster"] for r in rows if r["cluster"]}:
        members = [r for r in rows if r["cluster"] == cluster]
        fams = {r["family"] for r in members}
        if len(fams) > 1 and not all(r.get("recommended_family") for r in members):
            problems.append(f"cluster {cluster!r} is split across {sorted(fams)} but members lack recommended_family")
        if len({r.get("recommended_family") for r in members}) > 1:
            problems.append(f"cluster {cluster!r}: members disagree on recommended_family")
    # (No separate adjacency property.) Adjacency is guaranteed by CONSTRUCTION: the
    # emitted order is sorted by `cluster or name` inside each (family, keyword) block,
    # and property 5 pins the emitted order to that key. A separate adjacency check was
    # written, found to be implied by property 5, and REMOVED rather than kept — a check
    # that cannot fire is a hollow claim, which is what this lint just spent a cycle
    # removing. Verified empirically: reverting property 5's order makes a scrambled
    # cluster red, and adjacency follows from it.
    # 8. family agreement — the live group must match the baseline's family.
    # GROUP_BY_NAME was imported and never used, so editing it (which changes the
    # live MCP grouping at mcp_server.py) drifted from the baseline silently.
    for r in rows:
        live = GROUP_BY_NAME.get(r["name"]) or getattr(
            next((e for e in TOOL_REGISTRY if e.name == r["name"]), None), "group", None
        ) or "memory"
        if live != r["family"]:
            problems.append(
                f"`{r['name']}` is in group {live!r} in the declaration but family "
                f"{r['family']!r} in the baseline"
            )

    # 9. retired names (#3883) — the manifest's `retired:` block must match the
    # declaration exactly. A retired name is not a surface row, but it IS a contract
    # (it must resolve and warn), so a name that silently enters or leaves the retired
    # set is the same class of defect as a row that appears or disappears.
    retired = doc.get("retired")
    if not isinstance(retired, list):
        problems.append("the manifest carries no `retired:` list — re-cut the baseline")
    else:
        declared_retired = {
            e.name: (getattr(e, "retired_use_instead", None) or "") for e in RETIRED_TOOL_REGISTRY
        }
        manifest_retired = {
            str(r.get("name")): str(r.get("use_instead") or "")
            for r in retired
            if isinstance(r, dict) and r.get("name")
        }
        for name in sorted(set(declared_retired) - set(manifest_retired)):
            problems.append(f"retired name {name!r} is declared but not in the manifest")
        for name in sorted(set(manifest_retired) - set(declared_retired)):
            problems.append(f"retired name {name!r} is in the manifest but not declared")
        for name in sorted(set(declared_retired) & set(manifest_retired)):
            if declared_retired[name] != manifest_retired[name]:
                problems.append(
                    f"retired name {name!r}: use_instead {manifest_retired[name]!r} in the "
                    f"manifest != {declared_retired[name]!r} in the declaration"
                )
        row_names = {r["name"] for r in rows}
        for name in sorted(set(declared_retired) & row_names):
            problems.append(f"{name!r} is BOTH a surface row and a retired name")

    # 10. the artifact matches a fresh derivation from the CODE -------------------
    # Properties 1-11 check the artifact against ITSELF (order, clusters, families, the
    # `response_fields` record) and against two hand-authored tables (`surface-order.yml`,
    # `retired`). Before property 12 none of them compared it to the declaration, so the
    # baseline's headline numbers and every
    # derived column could be edited by hand while BOTH this lint and the D2 expansion
    # gate stayed green (measured: `counts.tools: 999` plus a doctored
    # `keyword_distribution` passed both). The baseline is frozen — freezing it is what
    # `cut` did — so a hand-edit is a failure to report, not a fact to accept.
    #
    # COST: this is the whole derivation, ~14 s CPU against ~0.4 s for properties 1-11,
    # because it imports the declaration and scans every tracked module for callers. That
    # is the price of the artifact being verified against the code at all; the CI job that
    # runs this has a 10-minute bound and the check is required.
    # The `cut_at_commit` presence check MOVED into `_read_manifest`: it is provenance and
    # the renderer slices it, so a null value was a TypeError there and a mere problem here.
    # One reader, one verdict.
    try:
        derived = build_doc()
    except SurfaceEvidenceUnreadable as exc:
        return _refuse(exc)
    problems.extend(_derivation_problems(doc, derived))

    # 13. the DECLARED surface is the APPROVED one (#10034) ----------------------
    # Property 12 (`build_doc` vs the committed artifact) does NOT prove the declaration is
    # the OWNER'S list, and it cannot: `build_doc` walks `dir(TortoiseSDK)` for the row set
    # and loads the declaration only to ANNOTATE each row (`approved: name in approved`).
    # It never compares the two sets, so a live-but-undeclared method gets `approved: false`
    # and a declared-but-absent name gets no row at all — a re-cut over a grown or
    # re-derived declaration reproduces it faithfully, and `_derivation_problems` then
    # compares the artifact against that same annotated derivation (a tautology after a
    # re-cut, not a check against the class). The check that the declaration IS the owner's
    # lives in `tools/sdk_surface_declaration.py`, and it is run HERE — from the required
    # `python-ci.yml::surface-guard` step that already runs this lint — rather than from a
    # new workflow step, so the gate is enforced by the existing required job. (A separate
    # step was the first draft; it needs the `workflow` scope on the pushing credential,
    # and the enforcement does not.)
    #
    # ⚠️ THE RESIDUAL BYPASS, STATED: this half runs from the PR's OWN copy of this file. Only
    # `tools/surface-guard.py` is restored from the base ref by CI (pinning this file too was a
    # deadlock — see the workflow comment), so a PR that edits `tools/surface_manifest.py` can
    # drop or neuter this call in the same commit. The pinned expansion gate still catches a new
    # public SDK method; it cannot see a re-cut approved LIST. That gap is why the declaration
    # half is also asserted by tests on every code change (#10044), and why
    # `tools/sdk_surface_declaration.py` is claimed by `SOURCE_PATTERNS["api"]` in
    # `tools/ci_selection.py`: without that entry a gate-only diff selected NO surface and the
    # required job executed the PR's own edited gate with none of its proofs having run.

    # The CONTRACT half runs here, in the required `surface-guard` job: the declaration must
    # still be the owner's exact 40, in both directions. The GAP half (138 live methods off
    # the surface + 30 approved but unimplemented, today) is deliberately NOT a required-CI
    # failure — a permanently red required job is a permanently ignored one, and the gap is
    # already red in `tools/sdk_surface_declaration.py check` and in
    # `tools/surface-target-check.py`. It is PRINTED here so a green job cannot be read as
    # "on target" (#10044).
    from tools import sdk_surface_declaration as declaration_gate

    try:
        problems.extend(declaration_gate.check(contract_only=True))
        # The gap document is a TRACKING artifact, so staleness IS a required-CI failure: a
        # surface change that does not re-render it would leave the gap understated.
        problems.extend(declaration_gate.baseline_problem())
        _declared = declaration_gate.load_declaration()["__all__"]
        _live = declaration_gate.measure_live()
    except declaration_gate.DeclarationUnreadable as exc:
        return _refuse(SurfaceEvidenceUnreadable(str(exc)))
    print(
        f"GAP (expected until #4282 Phase 2) — {len(_live - set(_declared))} live method(s) "
        f"off the approved surface, "
        f"{len([n for n in _declared if not declaration_gate.resolves(n, _live)])} approved "
        f"method(s) not implemented; target {len(_declared)}. Full red: "
        "`uv run python tools/sdk_surface_declaration.py check`."
    )

    # 14. the recorded `cut_at_commit` RESOLVES and SURVIVES (#11057 reviews P2, F1) ---
    # Property 12 skips this key (it is provenance, not a derivation) and `_read_manifest`
    # only asserts it is a non-empty string, so an unresolvable sha was green everywhere
    # while `render` published it to the owner. Red where the object database can answer;
    # say so loudly where it cannot (a shallow checkout is a remedy, not a red).
    #
    # RESOLVING IS NOT SURVIVING: on a PR checkout every head is fetched, so a LANE commit
    # resolves right up to the squash merge that never creates it on `main` and the branch
    # deletion that removes its only ref — and this property, made required, would then red
    # `main` forever. So the field must name a commit in the DEFAULT BRANCH's history.
    _prov_problems, _unprovable = provenance_report(
        doc, strict=bool(getattr(args, "provenance_strict", False))
    )
    problems.extend(_prov_problems)
    if _unprovable:
        print(f"::notice::{_unprovable}")

    for p in problems:
        print(f"FAIL {p}")
    if problems:
        print(f"\n{len(problems)} problem(s)")
        return 1
    print(
        f"OK — {len(rows)} tool rows, {len(retired) if isinstance(retired, list) else 0} retired, "
        "fourteen properties hold (eleven structural, one derived-from-code, "
        "one declared-vs-approved, one provenance)"
    )
    return 0


def _project_row(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in NON_DERIVABLE_ROW_KEYS}


def _derivation_problems(doc: dict, derived: dict) -> list[str]:
    """Every difference between the committed baseline and a fresh derivation.

    Compared by row NAME, so a rename is one removal plus one addition rather than a
    silent re-point. The projected dict is compared WHOLE, so a key the generator grows
    later is present on one side only and reds by default — an unclassified column cannot
    escape verification by being new.
    """
    problems: list[str] = []
    for key in sorted(set(doc) | set(derived)):
        if key in NON_DERIVABLE_DOC_KEYS or key in ("rows", "retired"):
            continue
        # A MISSING key and a `null` ONE ARE NOT THE SAME THING. `doc.get(key) !=
        # derived.get(key)` treated a hand-added top-level key whose value was `null` as a
        # match (both sides `None`), so it slipped through — contradicting this function's
        # own contract that an unclassified key reds by DEFAULT, and contradicting the
        # deny-list comment that says absence is compared as absence.
        if doc.get(key, _ABSENT) != derived.get(key, _ABSENT):
            problems.append(
                f"the baseline's `{key}` does not match the code: recorded "
                f"{doc.get(key, _ABSENT)!r}, derived {derived.get(key, _ABSENT)!r}"
            )
    for label, key in (("row", "rows"), ("retired row", "retired")):
        derived_rows = {str(r.get("name")): r for r in derived.get(key) or [] if isinstance(r, dict)}
        recorded_rows = {str(r.get("name")): r for r in doc.get(key) or [] if isinstance(r, dict)}
        # ORDER FIRST. `cut` emits both lists in a defined order (the tools by family /
        # keyword / cluster, the SDK rows and retired names alphabetically) and the
        # nine structural properties only pin the non-`sdk:` subsequence of `rows` — so
        # moving every `sdk:` row to the front of the frozen artifact passed the check
        # with "ten properties hold" (verified). The emitted order is part of the
        # artifact, so it is compared like any other derived content.
        recorded_order = [str(r.get("name")) for r in doc.get(key) or [] if isinstance(r, dict)]
        derived_order = [str(r.get("name")) for r in derived.get(key) or [] if isinstance(r, dict)]
        if recorded_order != derived_order:
            at = next(
                (i for i, (a, b) in enumerate(zip(recorded_order, derived_order, strict=False)) if a != b),
                min(len(recorded_order), len(derived_order)),
            )
            problems.append(
                f"the baseline's `{key}` are not in the derived order — first divergence at "
                f"index {at}: recorded {recorded_order[at:at + 1]}, derived {derived_order[at:at + 1]}"
            )
        for name in sorted(set(recorded_rows) | set(derived_rows)):
            recorded, fresh = recorded_rows.get(name), derived_rows.get(name)
            if recorded is None:
                problems.append(f"{label} `{name}` is derived from the code but is not in the baseline")
                continue
            if fresh is None:
                problems.append(f"{label} `{name}` is in the baseline but is not derived from the code")
                continue
            a, b = _project_row(recorded), _project_row(fresh)
            if a == b:
                continue
            keys = sorted(set(a) | set(b))
            differing = [k for k in keys if a.get(k) != b.get(k)]
            problems.append(
                f"{label} `{name}` was hand-edited — {', '.join(differing)}: recorded "
                f"{{{', '.join(f'{k}={a.get(k)!r}' for k in differing)}}}, derived "
                f"{{{', '.join(f'{k}={b.get(k)!r}' for k in differing)}}}"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_cut = sub.add_parser("cut", help="declare->manifest, once, by a human")
    p_cut.add_argument(
        "--commit",
        help="the full commit sha (40 lowercase hex) to cut the baseline at — the same shape "
             "`check` will verify, so `$(git rev-parse <rev>)`, never a prefix",
    )
    p_cut.add_argument(
        "--allow-approval-reset",
        action="store_true",
        help="permit dropping recorded per-row approvals (default: refuse and name every row)",
    )
    p_cut.add_argument(
        "--allow-removal",
        action="append",
        default=[],
        metavar="[tool:|sdk:]SYMBOL",
        help=(
            "permit ONE named symbol to disappear from the baseline with no archive or "
            "retirement record (a genuine rename). Repeatable, requires "
            "--allow-removal-reason, and logged in the cut output (#12237). A bare SYMBOL "
            "waives whichever surface this cut removes it from, and is refused if that is "
            "BOTH — name the surface as `tool:SYMBOL` / `sdk:SYMBOL` when the same name is "
            "removed from two of them."
        ),
    )
    p_cut.add_argument(
        "--allow-removal-reason",
        help="why each --allow-removal symbol is going away (required with it; goes in the PR)",
    )
    p_cut.add_argument(
        "--refresh-observations",
        action="store_true",
        help=(
            "re-derive the observed-usage columns (used_by/recommendation/basis/reason) "
            "from THIS machine's tool-call log instead of carrying the committed values. "
            "Without it a re-cut carries the evidence forward (#10914 review P2)."
        ),
    )
    p_cut.set_defaults(func=cmd_cut)
    sub.add_parser("render").set_defaults(func=cmd_render)
    p_check = sub.add_parser("check")
    # #11057 review P2: `strict` is for the ONE step that owns its own git depth (a
    # `fetch-depth: 0` checkout, run BEFORE any `--depth` fetch can mark it shallow).
    # Every other caller keeps the default, because `check` is documented as safe to
    # run anywhere and a shallow clone cannot answer the question at all.
    p_check.add_argument(
        "--provenance-strict",
        action="store_true",
        help="red when the recorded `cut_at_commit` cannot be PROVEN to resolve AND to be "
             "in the default branch's history (a shallow clone counts as unproven, not as "
             "a pass)",
    )
    p_check.set_defaults(func=cmd_check)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

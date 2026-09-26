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

THE RESET IS THE CONTROL, BUT IT IS NOT SILENT
    `cut` writes `approval: null` for every row — CONTRIBUTING.md ("To propose an
    addition", steps 2-4) makes that reset the control, so a changed `served_from`
    cannot ride an old approval into `approved`. The reset is therefore NOT reversed
    here. It is gated: the artifact is the only carrier of the owner's per-row
    approvals, so a re-cut that would blank any of them refuses, names every row it
    would blank, and proceeds only under an explicit `--allow-approval-reset` (#4598).
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import types
from pathlib import Path

import yaml


def _code_digest(code) -> str:
    """Move-invariant, implementation-complete digest of a code object.

    `co_code` alone is NOT enough: a constant is loaded as `LOAD_CONST <index>`,
    so two handlers with the same opcode shape but different constants or names
    hash identically (verified: 98 tools collapsed to 62 digests, and a read
    tool shared one with a destructive write tool).  `co_firstlineno` is
    excluded on purpose — a pure code move is not a served change.
    """
    import hashlib

    h = hashlib.sha256()
    h.update(code.co_code)
    for attr in ("co_names", "co_varnames", "co_freevars", "co_cellvars"):
        h.update(repr(getattr(code, attr)).encode())
    for const in code.co_consts:
        # a nested code object must be recursed, never repr'd (its repr carries
        # a memory address and is not stable)
        h.update(_code_digest(const).encode() if isinstance(const, types.CodeType)
                 else repr(const).encode())
    return h.hexdigest()[:16]


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ORDER_FILE = ROOT / "config" / "surface-order.yml"
MANIFEST_FILE = ROOT / "config" / "surface-manifest.yml"
RENDERED_FILE = ROOT / "docs" / "product" / "mcp-sdk-surface.md"


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


def _read_manifest(path: Path | None = None) -> dict:
    """Read the frozen baseline, or refuse. Never returns a partial document.

    `path` is resolved at CALL time (never bound as a default), so a caller that
    redirects `MANIFEST_FILE` — the tests do, and so would any future tool — reads the
    file it redirected to. A default argument bound at import time silently read the
    original, which is how a redirection can look effective and verify nothing.
    """
    if path is None:
        path = MANIFEST_FILE
    if not path.exists():
        raise SurfaceEvidenceUnreadable(
            f"the approved surface baseline is missing at {path}. The tool cannot verify "
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
            f"{path} records no `cut_at_commit`: it must name the commit the baseline was cut "
            "at, as a non-empty string. Re-cut the baseline."
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
#     rows happen to agree. NOTE: a PR author can therefore rewrite the evidence the owner
#     READS (the rendered `Used by` / `Recomm.` columns) while every gate stays green. That
#     is a documented residual — the columns are not a function of the code — FILED, not hidden.
#   * `approval` is the owner's reference: a PR number and a principal. The gate checks its
#     SHAPE only — it cannot verify that the review it names exists. The consent carrier is
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
#   * `cut_at_commit` is provenance: `cut` records the commit it ran at.
#
# The exclusion is a DENY-list applied to BOTH sides, so a key the generator grows later is
# compared by default (present on one side only, and ABSENCE is compared as absence — a
# hand-added top-level key whose value is `null` used to match `None` and slip through) and
# reds until it is deliberately classified here.
NON_DERIVABLE_ROW_KEYS = frozenset(
    {"used_by", "recommendation", "basis", "reason", "approval", "exemption"}
)
NON_DERIVABLE_DOC_KEYS = frozenset(
    {
        "cut_at_commit",
        "approval_status",
        "approval_principal",
        "approval_pr",
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


def scan_callers(method_names: set[str], registered_handlers: set[str] | None = None) -> dict[str, set[str]]:
    """method -> set of files that reference it as `<expr>.<method>`.

    AST, never regex. We deliberately do not resolve the receiver type: a
    reference on a non-SDK object with a colliding name is a known false
    positive, and the artifact records it as such. What this scan must not do is
    MISS a caller, so it is deliberately over-inclusive.
    """
    callers: dict[str, set[str]] = {name: set() for name in method_names}
    for rel in tracked_python():
        path = ROOT / rel
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
            continue
        registered = registered_handlers or set()
        for node, stack in _walk_with_functions(tree, []):
            if isinstance(node, ast.Attribute) and node.attr in callers:
                if rel == "tortoise/mcp_server.py":
                    # The HANDLER leg is per-registered-tool, not per-file: only a call
                    # inside a function that is itself a registered tool handler counts.
                    inside = any(fn in registered for fn in stack)
                    callers[node.attr].add(rel + ("#tool" if inside else "#other"))
                else:
                    callers[node.attr].add(rel)
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
    writable strings, so a shadow function can copy an approved tool's identity exactly and
    pass a name-based comparison while serving different code (verified: a shadow that set
    `__module__ = "tortoise.mcp_server"` and `__qualname__ = "tortoise_search"` replaced an
    approved tool with the gate green). A code object cannot be forged so cheaply — a
    different implementation means a different file/digest.  `co_firstlineno` is
    deliberately NOT part of the identity: a pure code move is not a served change.

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


def build_doc(commit: str | None = None) -> dict:
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
    keywords = order["keywords"]
    tokens_to_keyword = {t: kw for kw, toks in order["tokens"].items() for t in toks}
    family_rank = order["family_rank"]
    verbs = keywords

    public = sorted(
        name for name in dir(TortoiseSDK) if not name.startswith("_") and callable(getattr(TortoiseSDK, name))
    )
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
    # protocol while it appears in no enumeration at all; and `mcp.add_tool(fn, name=<an
    # approved name>)` silently REPLACES an approved tool's implementation while leaving
    # both the name set and the count unchanged. Neither is visible to a listing-only
    # comparison, so the baseline also records what actually does the registering: the
    # transform set, and the implementation identity behind each tool component.
    from tortoise import mcp_server as _mcp_server

    fingerprints: dict[str, str] = {}
    for _key, _tool in _mcp_server.mcp._local_provider._components.items():
        if not _key.startswith("tool:"):
            continue
        fingerprints[getattr(_tool, "name", None) or _key.split(":", 1)[1].split("@")[0]] = (
            _component_fingerprint(getattr(_tool, "fn", None))
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
        called = (entry.name in called_names) if called_names else None
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
                "used_by": used_by_str,
                "recommendation": _rec[0],
                "basis": "decided" if _rec[0] in ("merge", "kill", "fix-declaration") else "observed",
                "reason": _rec[1],
                "recommended_family": None,
                "proposed": False,
                "exemption": False,
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
                "exemption": False,
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
            # Carried so `approval_status: approved` has a place to record the
            # decision for a retirement, exactly as it does for a live row: a
            # retirement shrinks the surface and needs the same human approval.
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
        "approval_status": "pending-owner-approval",
        "approval_principal": None,
        "approval_pr": None,
        "counts": {
            "tools": len(tools_out),
            "sdk_public_methods": len(sdk_out),
            "retired": len(retired_out),
            "keyword_distribution": keyword_counts,
        },
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

def cmd_cut(args: argparse.Namespace) -> int:
    # READ THE ARTIFACT BEFORE DERIVING ANYTHING. `build_doc` cannot read it, so it emits
    # `approval: null` for every row: this write is the one that destroys the approvals,
    # and the artifact is their only carrier. The gate therefore lives at the write site.
    # A MISSING artifact has no approvals to lose (the first cut, which must keep
    # working); an UNREADABLE one is a refusal, never a silent overwrite. The absent-flag
    # default is `False` — an unstated permission is not a permission.
    blanked: list[tuple[str, str]] = []
    if MANIFEST_FILE.exists():
        try:
            prior = _read_manifest()
        except SurfaceEvidenceUnreadable as exc:
            return _refuse(exc)
        blanked = _recorded_approvals(prior)
        if blanked and not getattr(args, "allow_approval_reset", False):
            listed = "; ".join(f"{name} = {value!r}" for name, value in blanked)
            return _refuse(
                SurfaceEvidenceUnreadable(
                    f"refusing to overwrite {_display(MANIFEST_FILE)}: the re-cut would blank "
                    f"{len(blanked)} recorded owner approval(s) — {listed}. Re-run with "
                    "--allow-approval-reset to perform the reset deliberately "
                    '(CONTRIBUTING.md, "To propose an addition", steps 2-4).'
                )
            )
        if blanked:
            # The reset is authorised — but still say WHAT it costs, so a deliberate
            # reset is as visible in the transcript as the refusal is.
            print(
                f"--allow-approval-reset: blanking {len(blanked)} recorded owner "
                f"approval(s) from {_display(MANIFEST_FILE)}:"
            )
            for name, value in blanked:
                print(f"    {name} = {value!r}")

    try:
        doc = build_doc(args.commit)
    except SurfaceEvidenceUnreadable as exc:
        return _refuse(exc)

    MANIFEST_FILE.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110, default_flow_style=False),
        encoding="utf-8",
    )

    # NOT CARRIED FORWARD, DELIBERATELY: this writes `approval_status: pending-owner-approval`
    # and `approval: null` on every row, exactly as `build_doc` produces them. Carrying the
    # previous approvals over a re-cut looks like protecting the audit record, and it was
    # drafted and then REFUSED: CONTRIBUTING.md ("To propose an addition", steps 2-4) makes
    # the reset the control — a re-cut forces the owner to re-approve the baseline, so a
    # changed `served_from` cannot ride an old approval into `approved`. Preserving them
    # would silently reverse that decision, so the reset stays and the tool SAYS so, because
    # otherwise the next reader sees the wipe as a bug and "fixes" it back.
    # The guard above does NOT reverse that decision — it makes it non-silent: the reset
    # still happens, but only once `--allow-approval-reset` says so out loud.
    print(f"wrote {_display(MANIFEST_FILE)}")
    print(f"  tools={doc['counts']['tools']} sdk={doc['counts']['sdk_public_methods']} "
          f"retired={doc['counts']['retired']}")
    print(f"  keyword distribution: {doc['counts']['keyword_distribution']}")
    print(f"  distinct declared bindings: "
          f"{len({r['sdk_method'] for r in doc['rows'] if r.get('sdk_method')})}")
    if blanked:
        # Named, not summarised: the operator asked for this, and the rows are the loss.
        print(f"  DROPPED {len(blanked)} recorded approval(s) (--allow-approval-reset):")
        for name, approval in blanked:
            print(f"    {name}: {approval}")
    else:
        print("  approvals reset to null — re-record them per row (CONTRIBUTING.md, step 4)")
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
    laptop. The log still drives the artifact, through `build_doc`, which refreshes
    the MANIFEST; that lands as a reviewed, committed diff instead of as whichever
    machine happens to run the render.
    """
    first = (row.get("used_by") or "").split(",")[0].strip()
    if first == "agents":
        return "in use"
    if first == "never called":
        return "never called"
    return None


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
    add(f"Cut at `{doc['cut_at_commit'][:9]}`. **{len(tools)} MCP tools · {len(sdk)} SDK methods.**")
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
    add("declaration against the approved list below. It fails on three kinds of name-level change:")
    add("")
    add("1. **A new tool** — an MCP tool that is not on the approved list.")
    add("2. **A new endpoint** — a public SDK method that is not on the approved list.")
    add("3. **An approved entry disappearing** — a tool or endpoint that is on the list and is no")
    add("   longer offered.")
    add("")
    add("It also fails when the implementation behind an approved name is swapped for a different")
    add("one, because that changes the endpoint while leaving the name intact.")
    add("")
    add("Those are the name-level checks. The gate **additionally** fails on several served-surface and")
    add("evidence-integrity checks — an unapproved server transform, a moved SDK binding, a changed")
    add("HTTP-vs-stdio serving, a registry count that no longer matches the baseline, an exemption that")
    add("became reachable, and a missing or malformed baseline. They are listed in full in")
    add("[`CONTRIBUTING.md`](CONTRIBUTING.md); read them there rather than inferring the gate's whole")
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
    _rf_block = doc.get("response_fields")
    for _rf in (_rf_block if isinstance(_rf_block, list) else []):
        # `check` REPORTS a malformed block or entry; skip it here rather than crashing the
        # renderer (the two must not disagree about the same input) or emitting a blank
        # placeholder row for an entry that is missing `response` or `field`.
        if not isinstance(_rf, dict) or not _rf.get("response") or not _rf.get("field"):
            continue
        # Normalise whitespace AND pipes in every cell: an unescaped `|` splits the markdown
        # row, and an embedded newline (e.g. a YAML literal block) breaks it the same way.
        _cells = [
            " ".join(str(_rf.get(k, d) or d).split()).replace("|", "/")
            for k, d in (
                ("response", ""),
                ("field", ""),
                ("emitted_when", ""),
                ("unchanged_when_off", "yes"),
            )
        ]
        add(f"| `{_cells[0]}` | `{_cells[1]}` | {_cells[2]} | {_cells[3]} |")
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
    add("comparable agent-memory system advertises more than 18 (Cognee 4 · Mem0 9 · Graphiti 13 ·")
    add("Letta 18). That is a fact about the field and a fact about us.")
    add("")
    add("**I first read it as contradicting a decision, and that was wrong — in the direction that is")
    add(f"easy to miss.** I said an agent shown 14 of our tools and not told about the other "
        f"{len(tools) - 14} is being told")
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
    add("**The case for pinning a small advertised set.** Two thirds of what we advertise has never been")
    _never = [r for r in tools if observed_usage(r) == "never called"]
    add(f"called by anything, including us ({len(_never)} of {len(tools)}). Mainstream clients cap the tools they will show — a")
    add("reported 40 in Cursor — so a large part of our surface is not merely unused, it is invisible")
    add("anyway, and we pay context for it on every turn. Every comparable we studied pins a smaller set,")
    add("and the pattern is not novel here: `tortoise_recall` is already one tool with four modes and")
    add(f"`tortoise_get_entity` already absorbed the six fetch-by-id getters. Deferring the rest keeps all {len(tools)} callable.")
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
    """AC13's lint: eleven structural properties, plus a twelfth derived from the code."""
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

    for p in problems:
        print(f"FAIL {p}")
    if problems:
        print(f"\n{len(problems)} problem(s)")
        return 1
    print(
        f"OK — {len(rows)} tool rows, {len(retired) if isinstance(retired, list) else 0} retired, "
        "twelve properties hold (eleven structural, one derived-from-code)"
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
    p_cut.add_argument("--commit", help="the commit to cut the baseline at")
    p_cut.add_argument(
        "--allow-approval-reset",
        action="store_true",
        help=(
            "perform a re-cut that blanks recorded per-row owner approvals. Without it, "
            "`cut` refuses when the baseline it would overwrite carries any non-null "
            "`approval`, and names every row it would blank."
        ),
    )
    p_cut.set_defaults(func=cmd_cut)
    sub.add_parser("render").set_defaults(func=cmd_render)
    sub.add_parser("check").set_defaults(func=cmd_check)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

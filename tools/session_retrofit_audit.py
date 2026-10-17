#!/usr/bin/env python3
"""#3554 — the ``:Session`` writer inventory (re-runnable) + the D9 retrofit audit.

Two verbs, one instrument, because both answer the same question — *who writes
a* ``:Session`` *node, and what does the graph hold because of them*:

``inventory``
    Re-runs the RECORDED search predicate over the TRACKED tree — minus the
    path prefixes :data:`PREDICATE` declares ``excluded`` (``tests/``,
    ``docs/``) and the third-party/cache directory names it declares in
    ``skip_parts`` — and diffs the sites it finds against the declared
    inventory in ``tortoise/session_schema.py``. The corpus is ``git ls-files``
    (tracked, reviewable scope), not "whatever is on disk": a linked worktree
    under ``.worktrees/<lane>/`` is a full second copy of the source tree and
    would otherwise report every writer at a second, undeclared path. The
    scope is the predicate, not a private allow-list in this tool, so a
    ``:Session`` writer under ANY other tracked directory is a site the
    inventory must declare. The completeness claim is a predicate, never a
    number: run this on every rebase instead of trusting the table. Exit 1 on
    any undeclared site (a new writer) or any declared site the predicate no
    longer finds (a stale row).

    Two halves, per #3554's Target:

    * **graph writers** — Python string literals (AST nodes, so comments are
      excluded by construction) that mention ``:Session`` *and* carry a write
      verb against the Session alias (``MERGE (a:Session`` / ``SET a.``).
      A DOCSTRING is an AST ``Constant`` too, so it is excluded by identity:
      ``seed_capture_turn_store`` quotes the Session MERGE shape in its
      docstring without writing it, and the declaration module's ``note=``
      strings quote sites rather than being sites — neither is a writer;
    * **HTTP clients** — call-shaped POSTs to the DECLARED route
      (``PREDICATE['client_route']``, compiled at scan time) in TS / shell /
      Python entrypoints (prose mentions of the route are NOT sites: the
      matcher requires a call expression). A handler DECORATOR
      (``@app.post("/v1/sessions")``) is the route's DEFINITION, not a caller,
      and the route must be the full terminal segment — ``/v1/sessions/commit``
      and ``/v1/sessions/install-probe`` are different routes.

``retrofit`` (D9 — the audit #3554 owns)
    Quantifies, per graph, the three divergences the epic pushed down:

    * **(a) short turn count under** ``capture_ok = true`` — stored
      ``s.turn_count`` vs the Session's live episodic turn members;
    * **(b)** the pre- vs post-verification-read ``capture_ok`` ordering —
      measured as an UPPER BOUND, because the computation lane is not stored
      (the verdict and the reason are in ``session_schema.RETROFIT_VERDICTS``);
    * **(c)** ``entity_links_created`` under-count — the recorded counter vs
      the live ``aboutObject`` edge count, the drift the per-capture
      (``_capture_session_impl`` / ``capture_session``) and per-index-run
      (``_relink_sessions_after_index``) writers produce.

    A graph URI is never invented: pass ``--db`` (embedded) or ``--uri`` with
    ``TORTOISE_DB_URI`` set, exactly like ``tools/subject_binding_audit.py``.
    Without either, the verb REFUSES TO RUN — ``SystemExit``, non-zero, nothing
    printed on stdout — rather than printing a zero for a graph nobody read.

Usage:
    uv run python tools/session_retrofit_audit.py inventory [--root .]
    uv run python tools/session_retrofit_audit.py retrofit --db /tmp/memory.db
    uv run python tools/session_retrofit_audit.py retrofit --uri --namespace my-graph
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/session_retrofit_audit.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/session_retrofit_audit.py`"
    )

import argparse
import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.session_schema import (
    PREDICATE,
    RETROFIT_VERDICTS,
    SESSION_CLIENTS,
    SESSION_WRITERS,
    audit_session_retrofit,
)

#: The declaration module itself. Every string in it is a declaration ABOUT a
#: site — a ``note=`` quoting a writer's Cypher, a ``marker=`` quoting a
#: client's call — never a site. The module is not a ``:Session`` writer and
#: cannot be its own client, so scanning it could only manufacture false
#: positives the inventory can never clear.
DECLARATION_MODULE = "tortoise/session_schema.py"
PY_EXTENSIONS = (".py",)
CLIENT_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".sh", ".bash")

#: A string literal carries the Session alias for the write-verb test.
_SESSION_ALIAS_RE = re.compile(r"\(\s*(\w+)\s*:\s*Session\s*[{)$\s]")
_SESSION_PROP_RE = re.compile(
    r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=(?!=)")


def _label_pattern() -> re.Pattern[str]:
    """Compile the DECLARED label into a whitespace-tolerant matcher.

    Every stage AFTER the prefilter already accepts ``(s: Session {`` — legal
    Cypher (``oC_NodeLabel : ':' SP? oC_LabelName``), matched by
    :data:`_SESSION_ALIAS_RE` and by both declared write verbs, each of which
    spells the colon and the name with ``\\s*``. The prefilter that GATES them
    used the declaration as an EXACT SUBSTRING (``PREDICATE['label'] not in
    raw`` / ``":Session" not in text``), so the tolerance was dead and a
    writer spelled with whitespace after the colon was invisible: ``inventory``
    printed INVENTORY CLEAN with an undeclared writer present — the fail-OPEN
    mirror of the false-positive class the tracked-corpus fix removed (#3554
    review). The pattern is derived from :data:`PREDICATE`'s ``label`` (the
    declaration), never a private literal here, and it is a SUPERSET of what
    the alias/verb matchers accept, so the prefilter can only ever ADMIT.
    """
    head, _, name = PREDICATE["label"].partition(":")
    return re.compile(rf"{re.escape(head)}:\s*{re.escape(name)}\b")


#: An HTTP POST to the sessions route — a CALL, never a prose mention. The
#: ROUTE is compiled from :data:`PREDICATE`'s ``client_route`` at scan time (see
#: :func:`_client_matchers`), so editing the declaration changes what is
#: matched and a private edit here cannot widen the scan. Each alternative is a
#: call shape that appears in the tree today; a new transport the matcher cannot
#: see is exactly what the inventory diff is for (add the shape AND the site
#: together, in one reviewed diff).


def _client_matchers() -> tuple[tuple[re.Pattern[str], ...],
                                tuple[re.Pattern[str], ...]]:
    """Compile the DECLARED client route into the known call shapes.

    The route comes from :data:`PREDICATE`'s ``client_route`` — the same
    binding contract as :func:`_write_matchers` for ``write_verbs`` — so the
    declaration, not a private literal in this tool, decides what a client site
    is. The route is anchored to its FULL terminal segment (``(?![-/\\w])``):
    ``/v1/sessions/commit`` and ``/v1/sessions/install-probe`` are DIFFERENT
    routes served by different handlers, so a caller of one is not a capture
    client — a bare substring test turns the commit endpoint's ``_post_commit``
    into an undeclared "client".

    Returns ``(call_matchers, line_matchers)``. The first group needs a
    balanced CALL span (:func:`_call_spans`) or an AST call span, because its
    route and its POST evidence may sit on different lines. The second is the
    ``curl`` transport — a shell command with no parens to balance, matched
    against a backslash-continued LOGICAL line (:func:`_logical_lines`). The
    two groups must NOT be unioned over a whole line: see
    :func:`scan_http_clients`.
    """
    route = re.escape(PREDICATE["client_route"]) + r"(?![-/\w])"
    # ``fetch``/``Request`` default to GET, so the route ALONE is not a capture
    # CALL: ``fetch("/v1/sessions")`` is a READ of the route (the dashboard's
    # session-list call has exactly this shape) and ``urllib.request.Request(
    # url)`` is a GET too, so counting either as a client is the same false
    # positive the decorator/sibling-route matchers already exclude — DRIFT on
    # a non-site whose only clearable answer is to declare a read as a client.
    # Both matchers therefore require the POST evidence: ``method: "POST"`` for
    # ``fetch`` (either side of the route, so source order cannot hide it), and
    # ``method="POST"`` or ``data=`` for urllib's ``Request`` (``data=``
    # implies POST) (#3554 review).
    # The gaps are line-scanning (``[\s\S]*?``) rather than line-bounded
    # (``[^)]*``) because the CALL is what is scoped, not the line: a
    # Prettier-formatted ``fetch(url, {\n  method: "POST",\n …\n})`` puts the
    # POST evidence on its own line, and ``[^)]*`` also refuses to cross a
    # nested call's ``)`` (``fetch(buildUrl(route), {method: "POST"})``). The
    # caller slices a BALANCED call span for JS/TS (:func:`_call_spans`) and
    # the whole source span for Python, so the matchers run over the call —
    # a per-line loop made a multi-line ``fetch`` POST invisible and
    # ``inventory`` print INVENTORY CLEAN with an undeclared client present
    # (#3554 review).
    gap = r"[\s\S]*?"
    # ``(?i:post)`` on the METHOD evidence, never on the call token: the Fetch
    # standard normalizes ``method`` to byte-uppercase, so ``method: "post"``
    # is a working client, and matching only the literal ``POST`` made it
    # invisible — INVENTORY CLEAN with an undeclared client present, the
    # fail-open direction this guard exists to close (#3554 review).
    call = (
        # TS/JS: post(cfg, "/v1/sessions", ...)
        re.compile(rf"""\bpost\s*\({gap}["'`][^"'`]*{route}"""),
        # fetch(url, {method: "POST"/"post", …}) — the method after the route
        re.compile(rf"""\bfetch\s*\({gap}["'`][^"'`]*{route}{gap}"""
                   rf"""method\s*:\s*["'`](?i:post)"""),
        # … and the same call with the method spelled before the route.
        re.compile(rf"""\bfetch\s*\({gap}method\s*:\s*["'`](?i:post){gap}"""
                   rf"""["'`][^"'`]*{route}"""),
        # Python: requests.post(...) / Request(url, data=… | method="POST")
        re.compile(rf"""\.post\s*\({gap}["'`f][^"'`]*{route}"""),
        re.compile(rf"""Request\s*\({gap}["'`f][^"'`]*{route}{gap}"""
                   rf"""(?:method\s*=\s*["'](?i:post)|data\s*=)"""),
    )
    # ``curl`` is a shell command with no call parens, so it is the ONE group
    # matched against a LOGICAL line; the call group must never run over a
    # line, because its widened gap crosses the call's own ``)`` and fuses
    # unrelated statements (#3554 review).
    line = (
        # curl with an explicit POST (the method is case-insensitive too)
        re.compile(rf"""curl\b[^\n]*(-X\s*(?i:post)"""
                   rf"""|--request\s+(?i:post))[^\n]*{route}"""),
        re.compile(rf"""curl\b[^\n]*{route}[^\n]*"""
                   rf"""(-X\s*(?i:post)|--request\s+(?i:post))"""),
    )
    return call, line


#: The call tokens the non-Python half scopes its matchers on. A ``fetch`` /
#: ``post`` / ``Request`` call is sliced as a BALANCED SPAN (see
#: :func:`_call_spans`) rather than matched on a single physical line, so the
#: route and its POST evidence may sit on DIFFERENT lines — the shape a
#: formatter produces for ``fetch(url, {\n  method: "POST",\n …\n});``, and
#: the shape that made the POST requirement a per-line gate (#3554 review).
_CALL_TOKEN_RE = re.compile(r"\b(?:fetch|post|Request)\s*\(")


def _balanced_span(text: str, open_idx: int) -> int:
    """Index one past the ``)`` that closes ``text[open_idx] == '('``.

    String-literal aware (single, double and backtick quotes), so a ``)``
    inside a body or a URL does not close the call early —
    ``fetch(url, {body: ")"})`` is one call, not one and a half. Returns
    ``-1`` for an unbalanced tail (a truncated file), which the caller skips
    rather than mis-slicing.

    Comments and apostrophes are skipped. Pre-fix ANY ``'``/``"``/backtick from
    the call's open paren onward opened a string, so a multi-line call whose
    span contained an ODD number of those characters inside a comment or a
    regex literal (``// the server's session endpoint``, ``// don't retry``,
    ``/don't/``) never balanced: :func:`_call_spans` dropped the call and the
    per-logical-line net could not rescue it (the route and the POST evidence
    sit on different physical lines), so ``inventory`` printed INVENTORY CLEAN
    with an undeclared client present — the fail-open direction this guard
    exists to close (#3554 review). ``//`` and ``/* */`` are skipped to their
    end, and a ``'`` between two word characters is an apostrophe, not a
    string opener.
    """
    depth = 0
    quote = ""
    i = open_idx
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch == "/" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == "/":                 # line comment → end of line
                nl = text.find("\n", i)
                i = len(text) if nl < 0 else nl + 1
                continue
            if nxt == "*":                 # block comment → closing */
                end = text.find("*/", i + 2)
                i = len(text) if end < 0 else end + 2
                continue
        if ch in "\"'`":
            before = text[i - 1] if i > open_idx else ""
            after = text[i + 1] if i + 1 < len(text) else ""
            if not (ch == "'" and before.isalnum() and after.isalpha()):
                quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _call_spans(text: str) -> list[tuple[int, str]]:
    """Every balanced ``fetch(``/``post(``/``Request(`` call in ``text``.

    Yields ``(1-based line of the token, the call's own source)``. This is
    what makes the JS/TS half CALL-scoped: pre-fix it matched each PHYSICAL
    line, so a multi-line POST — the idiomatic Prettier spelling — was
    invisible and ``inventory`` printed INVENTORY CLEAN with an undeclared
    client present, the fail-OPEN direction of a guard whose whole purpose is
    to be trusted (#3554 review).
    """
    spans: list[tuple[int, str]] = []
    for match in _CALL_TOKEN_RE.finditer(text):
        end = _balanced_span(text, match.end() - 1)
        if end < 0:
            continue
        spans.append((text.count("\n", 0, match.start()) + 1,
                      text[match.start():end]))
    return spans


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Shell logical lines: a ``\\``-continued command is ONE line.

    ``curl`` is the one transport with no parens to balance, so a wrapped
    command (``curl \\\n  -X POST \\\n  …/v1/sessions``) hid the POST evidence
    from the per-line matchers exactly the way the multi-line ``fetch`` hid it
    (#3554 review). The line number reported is the command's FIRST line.
    """
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for idx, raw in enumerate(text.splitlines(), start=1):
        if not buf:
            start = idx
        if raw.rstrip().endswith("\\"):
            buf += raw.rstrip()[:-1] + " "
            continue
        out.append((start, buf + raw))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


@dataclass
class Site:
    """One predicate hit: where, who owns it, and the properties it writes."""
    kind: str                     # "graph-writer" | "http-client"
    path: str                     # repo-relative
    owner: str                    # enclosing function / module-level name
    line: int
    properties: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.path}::{self.owner}"

    @property
    def cid(self) -> str:
        return f"{self.path}:{self.line}"

    def __str__(self) -> str:
        props = ",".join(self.properties) or "-"
        return f"{self.kind} {self.cid} owner={self.owner} props=[{props}]"


def _owner_of(scopes: tuple[tuple[int, int, str, int], ...], lineno: int) -> str:
    """The narrowest declaring scope: the enclosing FUNCTION, else the
    module-level name an assignment binds the literal to, else ``<module>``.

    A function always beats an assignment, because a module-level constant
    carries statements that would otherwise be attributed to whatever local
    binding happens to span the line: ``_TURN_WRITE_CYPHER`` (the ONE turn
    store), ``BACKFILL_STATEMENTS`` (the R-18 migration). Naming those is what
    makes the rows reviewable rather than "somewhere in a 24k-line module".

    ``scopes`` is :attr:`_AstIndex.scopes` — built in the ONE walk, so this is
    a scan of the file's few hundred scopes, not another whole-tree walk.
    """
    func: tuple[int, str] | None = None
    assign: tuple[int, str] | None = None
    for start, end, name, kind in scopes:
        if not (start <= lineno <= end):
            continue
        span = end - start
        if kind == 0:               # function — beats any assignment
            if func is None or span < func[0]:
                func = (span, name)
        elif assign is None or span < assign[0]:
            assign = (span, name)
    if func is not None:
        return func[1]
    return assign[1] if assign is not None else "<module>"


class _AstIndex(NamedTuple):
    """Everything the two halves need, built in ONE ``ast.walk`` of a file.

    Three separate walks (docstrings, then every literal, then one more per
    candidate for its owning scope) made ``inventory`` take the better part of
    a minute on this tree — and the inventory test runs it, so that cost is a
    gate rather than an inconvenience. One pass builds all of it.

    * ``docstrings`` — the ``id()`` of every DOCSTRING ``Constant``. A
      docstring is an AST ``Constant``, so the "comments are excluded by
      construction" framing does not cover it; it DESCRIBES a write shape
      (``seed_capture_turn_store`` quotes the Session MERGE) without being one.
      Excluded by identity, so a sibling literal on the same statement stays;
    * ``scopes`` — ``(start, end, name, kind)`` for every function (kind 0) and
      every assignment (kind 1), consumed by :func:`_owner_of`;
    * ``literals`` — every ``str`` ``Constant``, in ``ast.walk`` order;
    * ``calls`` — every ``Call``, in ``ast.walk`` order;
    * ``decorators`` — the ``id()`` of every ``Call`` inside a DECORATOR.
      ``@app.post("/v1/sessions")`` is the route's DEFINITION — the server
      that serves it — not a caller of it.
    """
    docstrings: frozenset[int]
    scopes: tuple[tuple[int, int, str, int], ...]
    literals: tuple[ast.Constant, ...]
    calls: tuple[ast.Call, ...]
    decorators: frozenset[int]


def _ast_index(tree: ast.AST) -> _AstIndex:
    docstrings: set[int] = set()
    scopes: list[tuple[int, int, str, int]] = []
    literals: list[ast.Constant] = []
    calls: list[ast.Call] = []
    decorators: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", ())
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scopes.append(
                    (node.lineno, node.end_lineno or node.lineno, node.name, 0))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            end = node.end_lineno or node.lineno
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names:
                scopes.append((node.lineno, end, names[0], 1))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node)
        if isinstance(node, ast.Call):
            calls.append(node)
        for dec in getattr(node, "decorator_list", ()) or ():
            decorators.update(id(sub) for sub in ast.walk(dec)
                              if isinstance(sub, ast.Call))
    return _AstIndex(frozenset(docstrings), tuple(scopes), tuple(literals),
                     tuple(calls), frozenset(decorators))


def _excluded(rel: str) -> bool:
    """True when a repo-relative path sits under a DECLARED excluded prefix.

    The prefixes come from :data:`PREDICATE`'s ``excluded`` (the declaration),
    never from a literal in this tool — so the scan's scope is part of the same
    reviewed predicate as its verbs and route.
    """
    return any(rel == prefix.rstrip("/") or rel.startswith(prefix)
               for prefix in PREDICATE["excluded"])


def _write_matchers(alias: str) -> tuple[re.Pattern[str], ...]:
    """Compile the DECLARED write verbs with ``<alias>`` bound.

    The verbs come from :data:`PREDICATE`'s ``write_verbs``: a widened
    predicate is a reviewed diff to ``tortoise/session_schema.py``, not a
    private edit here. Until this was read, the guarantee the declaration
    stated ("the tool cannot silently widen its own predicate") did not hold —
    the scanner hardcoded ``MERGE``/``SET`` while claiming to key on the dict.
    """
    return tuple(re.compile(verb.replace("<alias>", re.escape(alias)))
                 for verb in PREDICATE["write_verbs"])


def _skip_parts() -> frozenset[str]:
    """The DECLARED directory-name parts the walk never descends into.

    Read from :data:`PREDICATE`'s ``skip_parts`` (the declaration), never from
    a literal in this tool — so the scan's scope is part of the same reviewed
    predicate as its verbs and route. This is the ONLY thing that can narrow
    the walk besides the declared ``excluded`` prefixes.
    """
    return frozenset(PREDICATE["skip_parts"])


def _skipped(rel: str) -> bool:
    """True when a repo-relative path sits under a DECLARED skipped directory.

    The directory NAMES come from :data:`PREDICATE`'s ``skip_parts`` (the
    declaration), never from a literal in this tool — so the scan's scope is
    part of the same reviewed predicate as its verbs and route. This applies to
    the TRACKED branch too: a tracked file under a declared-out directory is
    still out of scope, and pre-fix the ``git ls-files`` branch consulted the
    declared parts only in its non-git fallback, so a tracked vendor bundle
    under ``dist/`` (``website/apps/blog-admin/dist/assets/*.js`` is tracked
    today) carrying a ``:Session`` shape or the capture route would read as
    INVENTORY DRIFT on a non-site (#3554 review).
    """
    skip = _skip_parts()
    return any(part in skip for part in Path(rel).parts)


def _git_toplevel(root: Path) -> Path | None:
    """The work tree toplevel for ``root``, or ``None`` when not a repo.

    The ONE source of the repo-relative base: the declaration's rows are
    written repo-root-relative, so a scan must key its sites against the
    toplevel, never against whatever directory it was pointed at (#3554
    review).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, check=False)
    except OSError:                                    # git not on PATH
        return None
    if proc.returncode != 0:                           # not a work tree
        return None
    out = proc.stdout.decode("utf-8", "surrogateescape").strip()
    return Path(out) if out else None


def _tracked_files(root: Path) -> frozenset[str] | None:
    """The TRACKED REPO-relative paths under ``root``, or ``None`` if not a repo.

    The scan corpus is tracked, reviewable scope — not "whatever is on disk".
    A linked worktree under ``.worktrees/<lane>/`` is a full copy of the source
    tree, so every writer inside it appears at a second, undeclared path
    (``.worktrees/<lane>/tortoise/sdk.py::…``) that by construction is not in
    :data:`SESSION_WRITERS`; on the repo's own hub checkout (189 lanes) the
    walk reports INVENTORY DRIFT on a tree whose tracked content is clean.
    Intersecting the corpus with ``git ls-files`` — the pattern
    ``tests/test_entry_point_python_guard.py`` uses for a whole-tree scope —
    keeps the fail-closed property for every tracked directory while excluding
    parallel checkouts and any untracked scratch tree, neither of which can be
    a merged writer (#3554 review).

    ``--full-name`` is load-bearing, not cosmetic: without it ``git ls-files``
    prints paths relative to the INVOCATION directory, so a scan rooted at a
    subdirectory (``--root tortoise``) minted ``sdk.py`` where the declaration
    says ``tortoise/sdk.py``. Every found site then read as undeclared AND
    every declared row as stale, so a clean subtree printed INVENTORY DRIFT
    (#3554 review).

    ``None`` (``root`` is not a git work tree, e.g. a synthetic test root)
    makes :func:`_iter_source` fall back to the filesystem walk.
    """
    if _git_toplevel(root) is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--full-name", "-z", "--"],
            capture_output=True, check=False)
    except OSError:                                    # git not on PATH
        return None
    if proc.returncode != 0:                           # not a work tree
        return None
    return frozenset(
        rel for rel in
        proc.stdout.decode("utf-8", "surrogateescape").split("\0") if rel)


def _is_declaration_module(path: Path) -> bool:
    """True for the declaration module, independent of the scan ``root``.

    The module's repo-relative path is fixed, but a scan may be rooted
    anywhere (``--root tortoise``), so compare the path's trailing parts rather
    than ``path.relative_to(root)``: a root-relative literal matched only when
    the root WAS the repo root, so a scan from any other root reported the
    declaration's own ``note=``/``marker=`` strings as sites (#3554 review).
    """
    parts = Path(DECLARATION_MODULE).parts
    return path.parts[-len(parts):] == parts


def _iter_source(root: Path, extensions: tuple[str, ...]):
    """Walk the TRACKED tree minus what ``PREDICATE`` declares out of scope.

    Yields ``(absolute path, repo-relative key)``. The KEY is what the
    declaration's rows are written in — repo-root-relative — so a scan rooted
    at a subdirectory (``--root tortoise``) mints ``tortoise/sdk.py::…``, not
    ``sdk.py::…``, and its found set is comparable to the declaration; see
    :func:`_tracked_files` for the pre-fix wrong-base defect (#3554 review).

    The scope is the predicate, not a private allow-list: an allow-list let a
    ``:Session`` writer under any unlisted tracked directory stay invisible, so
    ``inventory`` printed INVENTORY CLEAN with an undeclared writer present —
    the fail-open direction of a guard whose whole purpose is to be trusted
    (#3554 review). The corpus is intersected with ``git ls-files`` (see
    :func:`_tracked_files`) so parallel checkouts and untracked scratch trees —
    which can never be a merged writer — are not sites; only the declared
    ``excluded`` path prefixes and ``skip_parts`` directory names are then
    skipped.

    ``os.walk`` with PRUNED ``dirnames`` rather than ``Path.rglob``: rglob
    descends into ``.venv``/``.git``/``node_modules`` and filters afterwards,
    which costs ~25s on this tree; pruning never opens them. A root that is not
    a git work tree (a synthetic test root) falls back to that walk with the
    same declared pruning; its keys are root-relative because there is no
    toplevel to key against.
    """
    tracked = _tracked_files(root)
    if tracked is not None:
        toplevel = _git_toplevel(root)
        assert toplevel is not None     # tracked implies a work tree
        for rel in sorted(tracked):
            if (Path(rel).suffix not in extensions or _excluded(rel)
                    or _skipped(rel)):
                continue
            path = toplevel / rel
            if _is_declaration_module(path):
                continue
            if not path.is_file() or path.is_symlink():
                continue
            yield path, rel
        return
    skip_parts = _skip_parts()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_parts)
        top = Path(dirpath)
        rel_dir = top.relative_to(root).as_posix()
        if rel_dir != "." and _excluded(rel_dir + "/"):
            dirnames[:] = []          # an excluded subtree is not walked
            continue
        for name in sorted(filenames):
            if Path(name).suffix not in extensions:
                continue
            path = top / name
            if path.is_symlink():
                continue
            if _is_declaration_module(path):
                continue
            rel = path.relative_to(root).as_posix()
            if _skipped(rel):
                continue
            yield path, rel


def scan_graph_writers(root: Path) -> list[Site]:
    """Half 1 — every Python literal that WRITES a ``:Session`` node."""
    sites: list[Site] = []
    # Compiled from the DECLARED label (whitespace-tolerant) so the prefilter
    # can only ever admit a superset of what the alias/verb matchers below
    # accept — an exact-substring test here was the binding stage and made the
    # predicate's own tolerance dead (#3554 review).
    label = _label_pattern()
    for path, rel in _iter_source(root, PY_EXTENSIONS):
        try:
            raw = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # The site is a LITERAL containing the label, so a file whose text
        # never mentions it cannot hold one. Without this prefilter the
        # scan ``ast.parse``s every ``.py`` under the roots (hundreds of files)
        # on every run — the dominant cost of a scan the inventory test runs.
        if not label.search(raw):
            continue
        try:
            tree = ast.parse(raw)
        except SyntaxError:
            continue
        index = _ast_index(tree)
        for node in index.literals:
            if id(node) in index.docstrings:
                continue
            text = node.value
            if not label.search(text):
                continue
            match = _SESSION_ALIAS_RE.search(text)
            if not match:
                continue
            alias = match.group(1)
            # The write test reads the DECLARED verbs (PREDICATE), so widening
            # the predicate is a reviewed diff to the declaration rather than a
            # hardcoded regex here.
            writes = any(rx.search(text) for rx in _write_matchers(alias))
            # ``SET a += $props`` (the rebuild_all snapshot restore) carries no
            # property NAME at the site — it is the one writer whose property
            # set is the whole schema, and it is declared as such.
            if not writes:
                continue
            props = tuple(sorted({
                prop for owner_alias, prop in _SESSION_PROP_RE.findall(text)
                if owner_alias == alias}))
            sites.append(Site(
                kind="graph-writer", path=rel,
                owner=_owner_of(index.scopes, node.lineno), line=node.lineno,
                properties=props))
    return sorted(sites, key=lambda s: (s.path, s.owner, s.line))


def scan_http_clients(root: Path) -> list[Site]:
    """Half 2 — every FILE carrying a POST-to-``/v1/sessions`` CALL.

    File-level on purpose: a line number is not a stable identity (a comment
    above the call moves it), and the declaration's job is to make a new
    TRANSPORT a reviewed diff. The per-file :class:`SessionClient.marker`
    pins the call site inside the file, so a row cannot survive the call
    being deleted — the marker assertion is in the test, and the file-level
    diff here catches a file nobody declared.

    The match itself is CALL-scoped, not line-scoped: a Python site is sliced
    from its AST call span and a JS/TS site from its balanced call
    (:func:`_call_spans`), so a POST whose route and ``method: "POST"`` sit
    on different lines is still a site — pre-fix only the physical line was
    tested and a formatted multi-line ``fetch`` POST was invisible
    (#3554 review).
    """
    sites: list[Site] = []
    call_matchers, line_matchers = _client_matchers()
    all_matchers = call_matchers + line_matchers
    for path, rel in _iter_source(root, CLIENT_EXTENSIONS):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if PREDICATE["client_route"] not in text:
            continue
        line = 0
        comment = ""
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            index = _ast_index(tree)
            # ``ast.get_source_segment`` re-splits the WHOLE file on every
            # call, so using it in this loop is O(calls × file length) — on
            # ``tortoise/sdk.py`` alone that is minutes, for a scan the
            # inventory test runs. Slice the pre-split lines instead: each
            # call costs only its own span.
            lines = text.splitlines(keepends=True)
            for node in index.calls:
                if id(node) in index.decorators:
                    continue
                if node.end_lineno is None:
                    continue
                src = "".join(lines[node.lineno - 1:node.end_lineno])
                if any(rx.search(src) for rx in all_matchers):
                    line = node.lineno
                    comment = _owner_of(index.scopes, node.lineno)
                    break
        else:
            # CALL-scoped for the parenthesised transports, LOGICAL-line
            # scoped for `curl` (a shell command with no parens to balance).
            # The two must NOT be unioned over a line: a call matcher's gap is
            # ``[\s\S]*?`` (needed for call spans), so over a line span it
            # crosses the call's own ``)`` and fuses unrelated statements — a
            # GET of the route followed by an unrelated ``{method: "POST"}``
            # object on the same physical line then reads as one capture
            # client, a false INVENTORY DRIFT on a non-site (#3554 review).
            candidates = [
                (span_line, span) for span_line, span in _call_spans(text)
                if any(rx.search(span) for rx in call_matchers)]
            candidates += [
                (span_line, span) for span_line, span in _logical_lines(text)
                if any(rx.search(span) for rx in line_matchers)]
            for span_line, span in sorted(candidates):
                line = span_line
                comment = span.strip()[:60]
                break
        if not line:
            continue
        sites.append(Site(
            kind="http-client", path=rel,
            owner=comment, line=line))
    return sorted(sites, key=lambda s: s.path)


def _scan_scope(root: Path) -> str | None:
    """The repo-relative prefix ``root`` selects, or ``None`` when unscoped.

    The declaration is repo-wide, so a scan rooted at a SUBDIRECTORY can only
    verify the declared rows UNDER that root. Comparing a subtree scan against
    the full declaration made every row outside it read as STALE — a clean
    subtree reported INVENTORY DRIFT (#3554 review). ``''`` means the root IS
    the toplevel (the whole declaration is verifiable); ``'tortoise/'`` means
    the scan is that subtree; ``None`` means ``root`` is not a git work tree at
    all (a synthetic test root), where keys are root-relative and the full
    declaration is compared exactly as before.
    """
    toplevel = _git_toplevel(root)
    if toplevel is None:
        return None
    try:
        rel = Path(os.path.realpath(root)).relative_to(
            Path(os.path.realpath(toplevel)))
    except ValueError:                      # a root outside the work tree
        return None
    as_posix = rel.as_posix()
    return "" if as_posix == "." else as_posix + "/"


def _in_scan_scope(key: str, scope: str) -> bool:
    """True when a declared row's repo-relative path sits under ``scope``."""
    if not scope:
        return True
    return key.startswith(scope) or key == scope.rstrip("/")


def inventory_report(root: Path, *, writers: list[Site] | None = None,
                    clients: list[Site] | None = None) -> dict:
    """The predicate's verdict: declared vs found, both directions.

    ``writers``/``clients`` let a caller that already scanned (the test
    module's module-scoped fixture) reuse the sites instead of paying for a
    second full-tree walk. Omitted, each half is scanned here as before.

    The comparison universe is narrowed to the scan's own scope when ``root``
    is a subdirectory of a work tree, so a subtree scan is judged on the rows
    it can actually see (see :func:`_scan_scope`).
    """
    writers = scan_graph_writers(root) if writers is None else writers
    clients = scan_http_clients(root) if clients is None else clients
    writers_expected = {w.key for w in SESSION_WRITERS}
    declared_clients_expected = {c.path for c in SESSION_CLIENTS
                                 if c.transport == "http"}
    scope = _scan_scope(root)
    if scope is not None:
        writers_expected = {k for k in writers_expected
                            if _in_scan_scope(k, scope)}
        declared_clients_expected = {
            p for p in declared_clients_expected if _in_scan_scope(p, scope)}
    found_writers = {s.key for s in writers}
    found_clients = {c.path for c in clients}
    return {
        "graph_writers_found": sorted(found_writers),
        "graph_writers_declared": sorted(writers_expected),
        "graph_writers_undeclared": sorted(found_writers - writers_expected),
        "graph_writers_stale": sorted(writers_expected - found_writers),
        "http_clients_found": sorted(found_clients),
        "http_clients_declared": sorted(declared_clients_expected),
        "http_clients_undeclared": sorted(found_clients - declared_clients_expected),
        "http_clients_stale": sorted(declared_clients_expected - found_clients),
        "ok": not (found_writers - writers_expected
                   or writers_expected - found_writers
                   or found_clients - declared_clients_expected
                   or declared_clients_expected - found_clients),
    }


def _cmd_inventory(args) -> int:
    report = inventory_report(Path(args.root).resolve())
    if args.json:
        # Machine-readable means the payload is the ONLY thing on stdout. The
        # verdict is `report["ok"]` (and the process exit code); appending the
        # human verdict line here is what made `--json` unparseable.
        print(json.dumps(report, indent=2))
    else:
        for row in SESSION_WRITERS:
            print(f"  {row.key}\n      kind={row.kind} "
                  f"props={sorted(row.properties) or '(whole schema)'}")
        for row in SESSION_CLIENTS:
            print(f"  {row.path}\n      transport={row.transport} "
                  f"lane={row.lane}")
        for bucket in ("graph_writers_undeclared", "graph_writers_stale",
                       "http_clients_undeclared", "http_clients_stale"):
            for key in report[bucket]:
                print(f"  ⛔ {bucket}: {key}")
        print(f"  graph writers: {len(report['graph_writers_found'])} found / "
              f"{len(report['graph_writers_declared'])} declared")
        print(f"  http clients:  {len(report['http_clients_found'])} found / "
              f"{len(report['http_clients_declared'])} declared")
        print("INVENTORY CLEAN" if report["ok"] else "INVENTORY DRIFT")
    return 0 if report["ok"] else 1


def _cmd_retrofit(args) -> int:
    from tortoise.sdk import TortoiseSDK

    if args.uri:
        if not os.environ.get("TORTOISE_DB_URI"):
            raise SystemExit(
                "--uri requires TORTOISE_DB_URI to be set in the environment")
        sdk = TortoiseSDK(namespace=args.namespace or None)
    elif args.db:
        sdk = TortoiseSDK(args.db)
    else:
        raise SystemExit(
            "refusing to invent a graph: pass --db <path> or --uri "
            "(with TORTOISE_DB_URI set). A zero printed for a graph nobody "
            "read is a lie with a number on it.")
    proj = sdk._get_proj()
    # Name the graph that was actually READ. On the ``--uri`` path with no
    # ``--namespace`` the SDK resolves the graph name from ``TORTOISE_DB_URI``
    # (``_get_proj`` → ``_derive_graph_name``), so ``args.namespace or
    # args.db`` was ``None`` and the report's first line read ``graph: None``
    # — the one field a reader uses to know WHAT was measured (#3554 review).
    graph_name = (getattr(proj, "graph_name", None)
                  or args.namespace or args.db)
    try:
        report = audit_session_retrofit(proj.g, graph=graph_name)
    finally:
        sdk.close()
    report["verdicts"] = RETROFIT_VERDICTS
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"graph: {report['graph']}")
        print(f"  sessions total          : {report['sessions']}")
        print(f"  (a) capture_ok=true, short turn count : "
              f"{report['short_turn_count']['capture_ok_true']} "
              f"(of {report['short_turn_count']['candidates_measured']} measured)")
        print(f"  (b) capture_ok=true (ordering-ambiguous upper bound): "
              f"{report['ordering_ambiguous']['sessions']} — "
              f"retro_quantifiable="
              f"{report['ordering_ambiguous']['retro_quantifiable']}")
        print(f"  (c) entity_links_created under-count : "
              f"{report['entity_links_under_count']['sessions']} "
              f"(under-counted edges: "
              f"{report['entity_links_under_count']['edges']})")
        for name, verdict in RETROFIT_VERDICTS.items():
            print(f"  verdict[{name}] = {verdict['decision']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)

    inv = sub.add_parser("inventory", help="re-run the writer predicate")
    inv.add_argument("--root", default=".")
    inv.add_argument("--json", action="store_true")
    inv.set_defaults(func=_cmd_inventory)

    ret = sub.add_parser("retrofit", help="D9 per-graph retrofit audit")
    ret.add_argument("--db", help="embedded SDK graph path")
    ret.add_argument("--uri", action="store_true",
                     help="connect via TORTOISE_DB_URI")
    ret.add_argument("--namespace", default=None)
    ret.add_argument("--json", action="store_true")
    ret.set_defaults(func=_cmd_retrofit)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

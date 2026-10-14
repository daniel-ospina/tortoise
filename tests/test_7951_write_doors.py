"""#7951 — the write-door inventory: the enumerated `apply` doors are classified.

PR #8127 (merge `9201b731b`) landed the **chokepoint** for the path it covered —
journal-first, then project, through `TortoiseSDK._apply_and_journal` — and
deliberately left #7951 OPEN, because doors to the fold remained reachable
**without** passing the chokepoint. That record listed the class by FILE
(`connectors/github.py` · `connectors/linear.py` · `connectors/slack.py` ·
`indexer/github_indexer.py` · `mining.py` · `sdk.py`) at line numbers that had
already moved twice (`sdk.py` shifted +77 in #7884 alone). This suite is the
**executable form of that enumeration**: it re-derives every `.apply(` call
site in the scan roots and refuses an **unclassified** one.

WHY AN INVENTORY TEST, AND NOT ONLY BEHAVIOURAL PINS. §17.1's defect was not one
bad writer; it was that a *new* unjournaled writer could appear **silently** —
nothing in the codebase or the suite made the class visible. A per-door pin
cannot catch the next door. So the load-bearing assertion here is structural:
`test_every_apply_call_site_is_classified` enumerates, in the scan roots,
EXACTLY THREE node kinds — (1) a `.apply(...)` attribute call, (2) a `.apply`
used as a value (`fn = proj.apply`, an attribute not consumed as a call's func),
and (3) a **compile-time constant `"apply"` passed as a call argument, a
keyword argument, or a subscript slice** (a placeholder-free f-string is the
same constant, and so is a single-element `*`-unpacked list/tuple) — and
asserts the `(file, enclosing function)` pairs and per-function call counts
equal `_DOORS`, with any non-call mention classified or RED. A receiver heuristic was tried and ABANDONED (round 3 of review): walrus,
rebind and attribute-target spellings escaped it, so the scan takes no receiver
filter at all.

⛔ IT IS A DRIFT GUARD OVER THOSE THREE NODE KINDS — NOT A COMPLETENESS CLAIM,
and NOT A SOUNDNESS PROOF. Stated at this length deliberately: eleven review
rounds each falsified an absolute claim written here, and every falsification
was the SAME defect — a sentence promising more than the scan does. **No lexical
or AST scan can enumerate every way Python reaches a method.** These escape by
design and are NOT claimed: a literal `"apply"` in any OTHER position (a bare
assignment, a dict value, a `**{…}` key); a name read from a variable or dict; a
renamed/aliased class or binding; a `getattr`/`__dict__` lookup whose key is not
written as the literal at that call; and `eval`/`globals()`. The guard's actual
guarantee is: **a change to one of the three enumerated node kinds requires the
map to be re-declared.** The SOUND form is #7951's endpoint —
`FalkorProjection.apply` refusing a live write its caller did not journal
(§17.1 mechanism 1, "a direct cache write FAILS"), which needs the atomic write
and is still OPEN.

WHAT IS *NOT* CLAIMED. This suite does **not** enforce SCHEMA §17.1's endpoint
("a direct cache write FAILS"), and it is not a claim that the fold is now
reachable only through one chokepoint — `FalkorProjection.apply` is still
directly callable, and the remaining doors are still open (documented, not
fixed). A non-projection `.apply` must be declared as `not-a-projection`.
Doors with a
reversibility-risky reason carry a dedicated pin: the connector `ingest` class,
the two connector webhooks, the indexer backfill, mining, `_cmd_verify`, and the
`_create_entity` deviation. The **replay** sites are carried by the map itself —
their pin is membership plus the exact call count, not a separate behavioural
test.

The six remaining doors, and what this lane did with each:

======================  ==========================================  ==================
door                    why it cannot route today                    disposition
======================  ==========================================  ==================
``sdk.py::_doc_write``  it could — the record is built pre-fold     **CLOSED** (journal-first)
``indexer`` backfill    production passes ``sdk=``; the ``None``     **DOCUMENTED** (test seam)
                        arm is a test helper's seam
``mining.py``           it already appends before it folds         **DOCUMENTED** (order-compliant)
``connectors/github``   ``ingest(proj)`` holds no journal handle     **DOCUMENTED** (no journal handle)
``connectors/linear``   same                                        **DOCUMENTED**
``connectors/slack``    same                                        **DOCUMENTED**
``connectors`` webhooks  dead branch: no production caller of        **DOCUMENTED** (no caller)
                        ``start_webhook``; only a test double
                        defines ``get_proj``
======================  ==========================================  ==================

The `connectors/*.ingest` class is the one that would need a
**connector-surface change** (thread a journal handle into ``ingest``, and give
its only production caller — `pipeline_cli`, which builds a bare journal-less
``FalkorProjection`` — a journal) to close. The two **webhook** sites are a
different case: their write branch runs only under ``connector.api``, and
``start_webhook`` has **no production caller** and no production object defines
``get_proj`` (only the test double in `tests/test_github_connector.py`) — so
they are dead in production, and were they revived they would carry an
``EventAPI`` (which holds ``.log``) and could journal through it. Closing the
`ingest` class is recorded on #7951 as the next unit, not silently absorbed here.

Replay engines (`backup.restore`, `consistency.recover_from_log`,
`__main__._cmd_reconcile`) are classified EXEMPT: the map says a replay fold must
**not** journal (the record came FROM the journal; journalling it again grows the
log on every rebuild). Separately, `__main__._cmd_verify` is EXEMPT as a
test-seam: `tortoise verify` constructs a journal-less projection, writes a
scratch `test-verify` Point and deletes it again in the same command — it is a
health probe, not a user write, and it has no journal to write to.
`TortoiseSDK._create_entity` is a **recorded known
deviation** in the module-top contract of `tortoise/sdk.py` (apply-first for
Object/Subject/Event registration), pinned here so the documentation cannot be
deleted without the deviation becoming undeclared.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from tortoise.__main__ import _cmd_verify

REPO_ROOT = Path(__file__).resolve().parent.parent
TORTOISE = REPO_ROOT / "tortoise"

#: The directories/files scanned. `tortoise/` minus the fold engine
#: (`tortoise/projection/` IS the fold) plus the one historical seeder script
#: under `graph-scripts/`. Deliberately NOT receiver-filtered: **every**
#: `.apply(...)` call in these files is a candidate, so no receiver spelling
#: can escape — a projection reached through a walrus (`(p := ...).apply`), a
#: rebind (`store = proj`), a bare function parameter, or an attribute target
#: is caught the same as `proj.apply(...)`. A `.apply` that is NOT a projection
#: (e.g. a dataframe's) must therefore be DECLARED, with class
#: `not-a-projection` — fail-closed by design, because the alternative (a
#: receiver heuristic) is the thing that let four spellings escape in review.
_SCAN_ROOTS = (TORTOISE, REPO_ROOT / "graph-scripts" / "setup.py")

#: `.apply` mentions that are NOT a write: a bound method used only for
#: signature introspection (`inspect.signature(proj.apply).parameters`, which
#: decides which replay kwargs to pass). Same shape as `_DOORS`.
_APPLY_VALUE_USES: dict[tuple[str, str], tuple[str, int]] = {
    ("tortoise/backup.py", "restore"): ("signature-introspection", 1),
    ("tortoise/consistency.py", "recover_from_log"):
        ("signature-introspection", 1),
}

#: Every `.apply(` call site in the scan roots, with its disposition and the
#: number of calls in that function. `(file, enclosing-qualname)` → (class, n).
_DOORS: dict[tuple[str, str], tuple[str, int]] = {
    # ── JOURNAL-FIRST DOORS — each appends before it folds ───────────────
    # `EventAPI._emit`: `self.log.append(event)` precedes `projection.apply`.
    # It is a journal-first door in its OWN right — the contract names it
    # alongside `_apply_and_journal` — and it does NOT route through that
    # method (so it is not labelled `chokepoint`).
    ("tortoise/api.py", "EventAPI._emit"): ("journal-first-door", 1),
    # `TortoiseSDK._apply_and_journal`: the CHOKEPOINT itself. The append is the
    # commit point; a failed append returns before the fold.
    ("tortoise/sdk.py", "TortoiseSDK._apply_and_journal"): ("chokepoint", 1),

    # ── CLOSED BY THIS LANE: journal-first, then the fold ─────────────────
    # `_doc_write` built its `DocumentCreated` record entirely BEFORE the fold
    # and journalled AFTER it — apply-first on a record that qualifies for
    # neither contract exception. Now `_emit_event` runs first and its failure
    # counter gates the fold (the append is the commit point).
    ("tortoise/sdk.py", "TortoiseSDK._doc_write"): ("closed-door", 1),

    # ── RECORDED KNOWN DEVIATION (module-top contract in `tortoise/sdk.py`) ─
    # `_create_entity`'s Object/Subject/Event registration blocks are apply-first
    # although their payload is built before the fold; they are left as-is in
    # the #8127 increment because reordering rides the #2194/#2295/#2309
    # first-registration hardening and its pinned tests.
    ("tortoise/sdk.py", "TortoiseSDK._create_entity"): ("documented-deviation", 1),

    # ── EXEMPT: REPLAY — a fold driven by READING the journal must not journal
    ("tortoise/backup.py", "restore"): ("exempt:replay", 2),
    ("tortoise/consistency.py", "recover_from_log"): ("exempt:replay", 2),
    ("tortoise/__main__.py", "_cmd_reconcile"): ("exempt:replay", 1),

    # ── EXEMPT: TEST SEAM / NON-USER WRITE ────────────────────────────────
    # `_cmd_verify` (`tortoise verify`) writes a scratch `test-verify` Point and
    # deletes it again in the same command — a health probe, not a user write.
    ("tortoise/__main__.py", "_cmd_verify"): ("exempt:test-seam", 1),
    # `backfill_legacy_closed` routes through `sdk._apply_and_journal` whenever
    # an SDK is supplied, which production always does
    # (`hosted_api._run_indexing`). The `sdk is None` arm is a TEST HELPER's
    # seam (tests/test_github_indexer.py drives the indexer directly) that
    # falls back to a bare `proj.apply`. It is NOT "pinned as folding without
    # journalling": on a JOURNAL-BACKED projection §17.1's guard now REFUSES
    # it (`DirectCacheWriteRefused`) and the journal stays untouched —
    # `test_indexer_sdk_less_fallback_fails_closed_on_a_journal_backed_projection`
    # pins exactly that. It folds only where there is no journal to derive from
    # (a journal-less projection, outside §17.1's reach), which is why the
    # production route is pinned to pass an explicit `sdk=`
    # (`test_indexer_production_backfill_route_passes_an_sdk`).
    ("tortoise/indexer/github_indexer.py", "GitHubIndexer.backfill_legacy_closed"):
        ("exempt:test-seam", 1),

    # ── EXEMPT: ALREADY JOURNAL-FIRST, ITS OWN ENVELOPE ───────────────────
    # `ConversationMiner._emit_event` does `api.log.append(record)` BEFORE
    # `api.projection.apply(record)` — the contract's ORDER holds. It is not
    # routed through `_emit_event` because its envelope differs (`version`,
    # nested `event`), and re-shaping it would change the journal line.
    ("tortoise/mining.py", "ConversationMiner._emit_event"):
        ("exempt:journal-first-own-envelope", 1),

    # ── EXEMPT: NO JOURNAL HANDLE — the connector `ingest` class ──────────
    # (the two WEBHOOK sites below are exempt for a different reason:
    # `exempt:no-production-caller`)
    ("tortoise/connectors/github.py", "GitHubConnector.ingest"):
        ("exempt:no-journal-handle", 4),
    ("tortoise/connectors/github.py", "GitHubConnector.start_webhook.Handler.do_POST"):
        ("exempt:no-production-caller", 1),
    ("tortoise/connectors/linear.py", "LinearConnector.ingest"):
        ("exempt:no-journal-handle", 1),
    ("tortoise/connectors/slack.py", "SlackConnector.ingest"):
        ("exempt:no-journal-handle", 1),
    ("tortoise/connectors/slack.py", "SlackConnector.start_webhook.Handler.do_POST"):
        ("exempt:no-production-caller", 1),

    # ── EXEMPT: HISTORICAL ONE-SHOT SCRIPT (outside `tortoise/`) ──────────
    # `graph-scripts/setup.py::step11e` seeds a demo Person/Product straight
    # into a bare embedded `FalkorProjection`; the file's own header declares
    # it historical and forbids running it against production. Named here so
    # the scan covers it rather than leaving it an unrecorded door.
    ("graph-scripts/setup.py", "step11e"): ("exempt:historical-script", 2),
}


def _is_apply_literal(node: ast.AST) -> bool:
    """True for a compile-time constant `apply`: a plain string, or a
    placeholder-free f-string, which the compiler folds to `LOAD_CONST` — and
    the same constant delivered through a single-element list/tuple, with or
    without a `*` unpack (`getattr(proj, *["apply"])`, `getattr(proj,
    *("apply",))`). The value handed to the helper is still the compile-time
    constant, so the spelling is the same door, not an escape. A MULTI-element
    unpack is NOT a single constant and stays out (it is not one `"apply"`
    argument); so does an empty one."""
    if isinstance(node, ast.Starred):
        node = node.value
    if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) == 1:
        return _is_apply_literal(node.elts[0])
    if isinstance(node, ast.Constant):
        return node.value == "apply"
    if isinstance(node, ast.JoinedStr):
        return (len(node.values) == 1
                and isinstance(node.values[0], ast.Constant)
                and node.values[0].value == "apply")
    return False


class _Scan(ast.NodeVisitor):
    """Collect the enumerated `apply` mentions and their enclosing scope.

    Three shapes are collected: the `.apply(...)` attribute CALL (the doors), a
    `.apply` used as a VALUE (`fn = proj.apply; fn(ev)` / `map(proj.apply, …)`
    — an attribute not consumed as a call's func), and a compile-time constant
    `"apply"` used as a positional/keyword call argument, a subscript slice, or
    a placeholder-free f-string (`f"apply"`, which the compiler folds to a
    constant). A single-element `*`-unpacked list/tuple counts as the same
    constant (`getattr(proj, *["apply"])`). That covers `getattr`,
    `builtins.getattr`, `__getattribute__`, `operator.methodcaller`,
    `functools.partial`, `attrgetter`, `__dict__[…]`, and a custom helper that
    is CALLED with the literal.

    What it does NOT see, by design (this is a drift guard, not a completeness
    claim — see the module docstring): a `"apply"` literal in any other
    position (a bare assignment or dict value), a name read from a variable or
    a dict, a `**{…}` key, a renamed/aliased class or binding, or
    `eval`/`globals()`."""

    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.stack: list[str] = []
        self.sites: list[tuple[str, str, int, str]] = []
        self.apply_attrs: dict[int, tuple[str, int, str]] = {}
        self.suspicious: list[tuple[str, str, int, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_fn(self, node) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_fn
    visit_AsyncFunctionDef = _visit_fn

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "apply":
            self.apply_attrs[id(node)] = (
                ".".join(self.stack), node.lineno, ast.unparse(node.value))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_apply_literal(node.slice):
            self.suspicious.append(
                (self.rel, ".".join(self.stack), node.lineno,
                 f"{ast.unparse(node.value)}[…] — a named-attribute access"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        is_apply_call = isinstance(func, ast.Attribute) and func.attr == "apply"
        if is_apply_call:
            self.sites.append(
                (self.rel, ".".join(self.stack), node.lineno,
                 ast.unparse(func.value)))
        elif any(
            _is_apply_literal(a)
            for a in (*node.args, *(k.value for k in node.keywords))
        ):
            # ANY call handed the string `apply` (a positional OR keyword
            # argument) is surfaced. Deliberately NOT a list of helper names:
            # that list was the whack-a-mole, and a future helper must be
            # declared rather than silently allowed.
            self.suspicious.append(
                (self.rel, ".".join(self.stack), node.lineno,
                 f"{ast.unparse(func)}(…, 'apply', …) — an indirect door"))
        self.generic_visit(node)
        # Pop AFTER the children are visited (generic_visit will have recorded
        # the call's own `.apply` Attribute); only value-uses remain.
        if is_apply_call:
            self.apply_attrs.pop(id(func), None)


def _enumerate_apply_sites() -> tuple[
        list[tuple[str, str, int, str]], list[tuple[str, str, int, str]]]:
    """Every `.apply(...)` attribute call in the scan roots (no receiver
    filter), plus every `.apply` used as a value and every
    `getattr(…, "apply")` — which must be declared, not skipped."""
    found: list[tuple[str, str, int, str]] = []
    suspicious: list[tuple[str, str, int, str]] = []
    for root in _SCAN_ROOTS:
        paths = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for path in paths:
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("tortoise/projection/"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            scan = _Scan(rel)
            scan.visit(tree)
            found.extend(scan.sites)
            suspicious.extend(scan.suspicious)
            for qualname, lineno, receiver in scan.apply_attrs.values():
                suspicious.append(
                    (rel, qualname, lineno,
                     f"{receiver}.apply — bound method used as a value"))
    return found, suspicious


def test_every_apply_call_site_is_classified():
    """The load-bearing assertion: no `.apply` CALL in the scan roots escapes
    the map, and no indirect `.apply` mention (used as a value, or reached via
    `getattr`) passes silently — there is no receiver heuristic, so a walrus,
    rebind, bare-parameter or attribute-target receiver cannot evade it.

    FAILS IF a new door appears (a new `(file, function)` pair), if a declared
    function gains or loses a call (the per-function count), if a declared
    file/function is renamed, or if `.apply` is used as a value / via
    `getattr` (classify or remove it). The message names the `file:line`, so
    the fix is a classification, never a silence."""
    sites, suspicious = _enumerate_apply_sites()
    assert sites, "the AST scan found no .apply() sites — the scan broke"

    observed_value: dict[tuple[str, str], list[int]] = {}
    for rel, qualname, lineno, _desc in suspicious:
        observed_value.setdefault((rel, qualname), []).append(lineno)
    unclassified_value = {k: v for k, v in observed_value.items()
                          if k not in _APPLY_VALUE_USES}
    assert not unclassified_value, (
        "indirect `apply` mention(s) — a bound method or `getattr` can reach "
        "the fold without an `.apply(...)` call node; classify or remove:\n"
        + "\n".join(f"  {f}:{lines}  {q}"
                    for (f, q), lines in sorted(unclassified_value.items()))
    )
    stale_value = {k: v for k, v in _APPLY_VALUE_USES.items()
                   if k not in observed_value}
    assert not stale_value, (
        "`_APPLY_VALUE_USES` names a mention that no longer exists: "
        + ", ".join(f"{f} {q}" for f, q in sorted(stale_value))
    )
    value_mismatch = {
        k: (declared_n, len(observed_value[k]))
        for k, (_cls, declared_n) in _APPLY_VALUE_USES.items()
        if len(observed_value.get(k, [])) != declared_n
    }
    assert not value_mismatch, (
        "an indirect `apply` mention count changed — re-classify: "
        + ", ".join(f"{f} {q}: declared {d} observed {o}"
                    for (f, q), (d, o) in sorted(value_mismatch.items()))
    )

    observed: dict[tuple[str, str], list[int]] = {}
    for rel, qualname, lineno, _recv in sites:
        observed.setdefault((rel, qualname), []).append(lineno)

    missing = {k: v for k, v in observed.items() if k not in _DOORS}
    assert not missing, (
        "UNCLASSIFIED .apply() call site(s) — a writer reached the fold "
        "without a journal-first route or a recorded exemption:\n"
        + "\n".join(f"  {f}:{lines}  {q}" for (f, q), lines in sorted(missing.items()))
    )

    stale = {k: v for k, v in _DOORS.items() if k not in observed}
    assert not stale, (
        "the door map names call site(s) that no longer exist — the map drifted:\n"
        + "\n".join(f"  {f}  {q}" for f, q in sorted(stale))
    )

    count_mismatch = {
        k: (declared_n, len(observed[k]))
        for k, (_cls, declared_n) in _DOORS.items()
        if len(observed[k]) != declared_n
    }
    assert not count_mismatch, (
        "a declared writer gained or lost a projection .apply() call — "
        "re-classify the new site:\n"
        + "\n".join(
            f"  {f}  {q}: declared {d}, observed {o} at {observed[(f, q)]}"
            for (f, q), (d, o) in sorted(count_mismatch.items())
        )
    )

    assert all(cls for cls, _n in _DOORS.values()), "every door carries a class"
    known_classes = {
        "chokepoint", "journal-first-door", "closed-door",
        "documented-deviation", "not-a-projection",
    }
    for cls, _n in _DOORS.values():
        if cls.startswith("exempt:"):
            assert cls.count(":") == 1, (
                f"exemption {cls!r} must name its reason (`exempt:<reason>`)"
            )
        else:
            assert cls in known_classes, (
                f"unknown door class {cls!r} — use one of {sorted(known_classes)} "
                "or `exempt:<reason>`"
            )


# ── the indirect spellings the kind-3 matcher must keep covering ─────────

def _apply_mentions(source: str) -> list[str]:
    """Run the inventory's OWN scanner over `source` and return the mention
    descriptions it surfaces (call sites + suspicious), so a matcher change is
    exercised end-to-end rather than at `_is_apply_literal` alone."""
    scan = _Scan("probe.py")
    scan.visit(ast.parse(source))
    return [desc for _rel, _qual, _line, desc in (*scan.sites, *scan.suspicious)]


def test_apply_literal_matcher_covers_starred_and_collection_spellings():
    """Kind 3 covers a compile-time constant `"apply"` handed to a call —
    INCLUDING the same constant delivered through a single-element list/tuple,
    `*`-unpacked or not (`getattr(proj, *["apply"])(ev)`). The value reaching
    the helper is the same compile-time constant, so it is the same door; the
    scanner must not be silent about it.

    FAILS IF `_is_apply_literal` stops unwrapping `ast.Starred` / a one-element
    `List`/`Tuple`: that re-opens the spelling the class claims to cover (the
    guard/doc gap this test pins). MUTATION: with both unwrap branches removed,
    the `*["apply"]` / `*("apply",)` / `__dict__[*["apply"]]` spellings produce
    no mention and this test goes RED."""
    indirect = (
        'getattr(proj, "apply")(ev)',
        'getattr(proj, *["apply"])(ev)',
        'getattr(proj, *("apply",))(ev)',
        'proj.__dict__["apply"](ev)',
        'proj.__dict__[*["apply"]](ev)',
    )
    for spelling in indirect:
        mentions = _apply_mentions(spelling)
        assert any("indirect door" in m or "named-attribute access" in m
                   for m in mentions), (
            f"{spelling!r} produced no apply mention — `_is_apply_literal` no "
            f"longer covers its spelling (the guard/doc gap re-opened); "
            f"mentions={mentions!r}"
        )
    # The boundary that keeps the unwrap from over-reaching: an unpack of TWO
    # elements passes two arguments, not the single constant `"apply"`, and an
    # empty one passes none — neither is this door, and neither may be flagged
    # (a matcher widened to any list would red this half).
    for not_a_door in ('f(*["apply", "other"])', 'f(*[])'):
        assert not _apply_mentions(not_a_door), (
            f"{not_a_door!r} was flagged although it hands the helper no single "
            "`apply` constant — the matcher over-reached"
        )


# ── the CLOSED door: `_doc_write` is journal-first ────────────────────────

def _journal_line_types(journal_path: Path) -> list[str]:
    import json
    if not journal_path.exists():
        return []
    out = []
    for raw in journal_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            out.append(json.loads(raw).get("type"))
    return out


@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK whose JSONL journal is a temp file.

    The fixture passes an explicit temp `db_path` and depends on nothing else
    in the process environment, so it runs in EITHER lane: embedded when no
    `TORTOISE_DB_URI` is configured, and — in a URI-set session — the SDK's
    projection construction is REDIRECTED to the docker server, exactly like
    any other non-carve-out test's (the normal shape; this stem is deliberately
    in neither `config/ci-surfaces.yml`'s `carve_out` nor
    `tests/_embedded.TEST_NO_REDIRECT_STEMS`). It needs no URI, but it is NOT
    embedded-only, and the assertions below are lane-agnostic — so do not add
    the stem to those two registries to "match" an embedded-only reading of
    this fixture."""
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "doors.db"), event_log_path=str(journal))
    sdk._journal_path_for_test = journal  # convenience for the assertions
    try:
        yield sdk
    finally:
        sdk.close()


def test_doc_write_journals_before_it_folds(sdk_with_journal, monkeypatch):
    """RED before the fix: `_doc_write` folded THEN journalled.

    The assertion is ORDER, observed at the fold: when `proj.apply` is entered,
    the journal must ALREADY hold the `DocumentCreated` record. Apply-first
    leaves the derived store ahead of the truth for the whole fold — the window
    no replay can undo (contract, `tortoise/sdk.py`)."""
    sdk = sdk_with_journal
    journal = sdk._journal_path_for_test
    proj = sdk._get_proj()
    original = proj.apply
    seen_at_fold: list[list[str]] = []

    def spy(event, **kwargs):
        seen_at_fold.append(_journal_line_types(journal))
        return original(event, **kwargs)

    monkeypatch.setattr(proj, "apply", spy)

    sdk._doc_write({}, "doc-7951", "Doors", "/tmp/doors.md", "https://x/doors")

    assert seen_at_fold, "the fold never ran — the write is broken, not reordered"
    assert "DocumentCreated" in seen_at_fold[0], (
        "the fold ran BEFORE the journal held the record — `_doc_write` is "
        "apply-first. The contract requires journal-first (the append is the "
        f"commit point); observed journal at fold time: {seen_at_fold[0]!r}"
    )
    assert "DocumentCreated" in _journal_line_types(journal)


def test_doc_write_does_not_fold_when_the_append_fails(
        sdk_with_journal, monkeypatch):
    """The append is the COMMIT POINT: a failed append must not fold.

    FAILS IF the reorder keeps `_emit_event` first but folds regardless of its
    outcome — that is journal-first in *order* only, and still leaves the
    derived store ahead of the truth when the journal write fails (#4240's
    fail-soft path)."""
    sdk = sdk_with_journal
    proj = sdk._get_proj()
    folded: list[str] = []

    class _FailingLog:
        def append(self, _event):
            raise OSError("journal is on a full disk")

    monkeypatch.setattr(sdk, "_get_event_log", lambda: _FailingLog())
    monkeypatch.setattr(proj, "apply", lambda event, **kw: folded.append("folded"))

    sdk._doc_write({}, "doc-7951-fail", "Doors", "/tmp/d.md", "https://x/d")

    assert folded == [], (
        "the fold ran although the journal append FAILED — the derived store "
        "is now ahead of the truth (contract window (a), unrecoverable)"
    )
    assert sdk._journal_write_failures >= 1, (
        "the append failure was not counted — the commit-point gate cannot "
        "have read it"
    )


# ── EXEMPTION PINS — the reason each exemption holds, so it cannot drift ──

def test_connector_ingest_holds_no_journal_handle():
    """The `connectors/*` exemption's REASON, pinned.

    `ingest(proj)` receives only a projection — no journal handle — and its
    only production caller (`pipeline_cli.cmd_run`) builds a bare
    `FalkorProjection` with no journal configured. Closing the class therefore
    needs a connector-surface change (owner approval, recorded on #7951).

    FAILS IF someone adds an `ingest` parameter (any journal/sdk/event-log
    handle), or uses the connector's own `self.api` handle inside `ingest` —
    either way the door map's `exempt:no-journal-handle` class is no longer true
    and must be revisited. (Two exact checks, not a completeness claim: a
    handle reached by another attribute name is not claimed here — see the
    module docstring's boundary.)"""
    from tortoise.connectors.github import GitHubConnector
    from tortoise.connectors.linear import LinearConnector
    from tortoise.connectors.slack import SlackConnector

    for cls in (GitHubConnector, LinearConnector, SlackConnector):
        params = list(inspect.signature(cls.ingest).parameters)
        assert params == ["self", "proj"], (
            f"{cls.__name__}.ingest now takes {params} — if a journal/sdk handle "
            "was added, the `exempt:no-journal-handle` classification must be "
            "replaced by a routed-through-the-chokepoint one and its .apply "
            "sites closed"
        )
        # The reason also reverses if `ingest` starts journaling through the
        # connector's own `self.api` — the handle it already holds (`.log`,
        # `_emit`, and every `add_*`/`emit_belief` write method, all of which
        # reach `self.log.append`). A substring list of method names was the
        # whack-a-mole; the handle itself is the boundary, so its mere use in
        # `ingest` is the reversal.
        source = inspect.getsource(cls.ingest)
        assert "self.api" not in source, (
            f"{cls.__name__}.ingest now uses `self.api` (the journal handle) — "
            "its `exempt:no-journal-handle` reason has reversed and the "
            "connector-surface change must route `ingest` through the "
            "chokepoint"
        )


def test_connector_webhook_has_no_production_caller():
    """The two WEBHOOK sites are exempt for a different reason than `ingest`:
    their write branch runs only under ``connector.api``, and `start_webhook`
    has no reference in the scan roots while no object there carries a
    `get_proj` attribute (or method).

    FAILS IF a reference appears (which makes the branch potentially live) or an
    object starts carrying `get_proj` — at which point the branch must be routed
    through the connector's `api` (which carries `.log`) rather than left
    unjournaled. Scope is the SAME roots the inventory scans — `tortoise/` plus
    `graph-scripts/setup.py` — not a subset.

    Note the exact claim: ``get_proj`` counts as an *object* carrying it only
    when it is a method (`def get_proj`) or an attribute target
    (``obj.get_proj = …``) or a name bound in a **class body** at any nesting
    depth within it. A plain
    local ``get_proj = getattr(sdk, "_get_proj", None)`` — which
    ``tortoise/extractor_v2.py`` has — is a local variable, not an object with a
    webhook ``get_proj``, and is deliberately not flagged."""
    hits: list[str] = []
    get_proj_defs: list[str] = []
    for root in _SCAN_ROOTS:
        paths = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for path in paths:
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("tortoise/projection/"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                # ANY reference to `start_webhook` — a call, a bound-method
                # value (`serve = gh.start_webhook`), or a `getattr` literal.
                if isinstance(node, ast.Attribute) and node.attr == "start_webhook":
                    hits.append(f"{rel}:{node.lineno}")
                if isinstance(node, ast.Constant) and node.value == "start_webhook":
                    hits.append(f"{rel}:{node.lineno} (literal)")
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == "get_proj"):
                    get_proj_defs.append(f"{rel}:{node.lineno}")
            # A `get_proj` *attribute* target (`obj.get_proj = …`) anywhere,
            # and a bare `get_proj = …` bound DIRECTLY in a class body.
            for target in _get_proj_targets(tree):
                get_proj_defs.append(f"{rel}:{target}")
    assert not hits, (
        "a reference to `start_webhook` appeared in the scan roots — the "
        "webhook write branch may now be live and unjournaled: " + ", ".join(hits)
    )
    assert not get_proj_defs, (
        "an object in the scan roots now carries `get_proj` — revisit the "
        "webhook exemption: " + ", ".join(get_proj_defs)
    )


def _get_proj_targets(tree: ast.Module) -> list[int]:
    """Line numbers of `obj.get_proj = …` targets anywhere, and bare
    `get_proj = …` targets bound in a CLASS scope (a class attribute) — the
    latter found at any nesting depth inside the class body (``if``/``with``/
    ``for`` blocks included), but NOT inside a function body, where such a name
    is a local variable and not a webhook ``get_proj`` (``tortoise/extractor_v2.py``
    has two such locals)."""
    found: list[int] = []

    def visit(node: ast.AST, in_class: bool, in_func: bool) -> None:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            targets = []
        for target in targets:
            is_attr = (isinstance(target, ast.Attribute)
                       and target.attr == "get_proj")
            is_class_bare = (isinstance(target, ast.Name)
                             and target.id == "get_proj"
                             and in_class and not in_func)
            if is_attr or is_class_bare:
                found.append(node.lineno)

        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda)):
                visit(child, in_class, True)
            elif isinstance(child, ast.ClassDef):
                # A class body is a class scope again, even nested in a method.
                visit(child, True, False)
            else:
                visit(child, in_class, in_func)

    visit(tree, in_class=False, in_func=False)
    return found


def test_pipeline_cli_builds_a_journal_less_projection():
    """The other half of the connector exemption: the production caller has no
    journal to hand over. FAILS IF `pipeline_cli` stops constructing a bare
    `FalkorProjection` — then the connector class CAN be closed and this
    exemption has expired.

    Asserted at the AST, per call, so that attaching a journal to ONE
    construction branch is not masked by the other branch's text still being
    present (the earlier string-OR form had exactly that hole)."""
    module = ast.parse((REPO_ROOT / "tortoise" / "pipeline_cli.py").read_text(
        encoding="utf-8"))
    ctor_calls = [
        node for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name)
             and node.func.id == "FalkorProjection")
            or (isinstance(node.func, ast.Attribute)
                and node.func.attr in ("from_uri",))
        )
    ]
    assert ctor_calls, (
        "pipeline_cli no longer constructs a FalkorProjection at all — the "
        "connector exemption's premise (no journal handle at the caller) may "
        "no longer hold"
    )
    for call in ctor_calls:
        kwargs = {kw.arg for kw in call.keywords}
        offending = kwargs & {"event_log_path", "journal", "log"}
        assert not offending, (
            f"pipeline_cli:{call.lineno} now passes {sorted(offending)} to its "
            "FalkorProjection — the connector door class is closable and this "
            "exemption has expired"
        )
    calls = [
        node for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ingest"
    ]
    assert any(
        c.args and ast.unparse(c.args[0]) == "proj"
        and not ({kw.arg for kw in c.keywords} & {"journal", "sdk", "event_log"})
        and not any(kw.arg is None for kw in c.keywords)
        for c in calls
    ), "pipeline_cli no longer calls `connector.ingest(proj)` with no journal"
    # The reason also reverses if the connector is CONSTRUCTED with a
    # journal-bearing handle (its `api` carries `.log`). The handle can be
    # passed by POSITION as well as by keyword, so the forbidden parameter
    # positions are resolved from each connector's own `__init__` signature —
    # a keyword-only check was bypassable by `conn_cls(cfg, api_obj)`. The
    # pipeline resolves the class dynamically (`conn_cls = getattr(mod, …)`),
    # so its call is matched by the binding name `conn_cls` AND by the three
    # concrete class names, against the union of the connectors' forbidden
    # positions.
    import inspect as _inspect

    from tortoise.connectors.github import GitHubConnector
    from tortoise.connectors.linear import LinearConnector
    from tortoise.connectors.slack import SlackConnector

    connector_classes = (GitHubConnector, LinearConnector, SlackConnector)
    class_names = {c.__name__ for c in connector_classes}
    forbidden_names = {"api", "journal", "log"}
    forbidden_positions: set[int] = set()
    for cls in connector_classes:
        params = list(_inspect.signature(cls.__init__).parameters)[1:]
        forbidden_positions |= {
            i for i, p in enumerate(params) if p in forbidden_names
        }
    for call in ast.walk(module):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if called not in class_names and called != "conn_cls":
            continue
        offending = {kw.arg for kw in call.keywords} & forbidden_names
        if any(kw.arg is None for kw in call.keywords):
            # A `**{…}` unpack — a keyword pass the AST does not name.
            offending.add("<**unpack>")
        if any(i < len(call.args) for i in forbidden_positions):
            offending.add(f"<positional at {sorted(forbidden_positions)}>")
        assert not offending, (
            f"pipeline_cli:{call.lineno} now constructs {called or func} with "
            f"{sorted(offending)} — the `exempt:no-journal-handle` reason has "
            "reversed"
        )


def test_verify_health_check_remains_a_self_deleting_scratch_write():
    """`__main__._cmd_verify` is exempt as a TEST SEAM: it constructs its
    projection via `FalkorProjection.from_uri` (whose signature takes no journal
    argument, so the projection is structurally journal-less) and writes then
    deletes one scratch `test-verify` Point in the same command. FAILS IF the
    write, the `from_uri` construction, or the delete goes away — any of those
    turns the scratch write into a durable, unjournaled user write."""
    path = Path(inspect.getsourcefile(_cmd_verify))
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    fn = next((n for n in ast.walk(module)
               if isinstance(n, ast.FunctionDef) and n.name == "_cmd_verify"),
              None)
    assert fn is not None, "`_cmd_verify` no longer exists in __main__"
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]

    def _text(call: ast.Call) -> str:
        return ast.get_source_segment(source, call) or ""

    assert any(isinstance(c.func, ast.Attribute) and c.func.attr == "from_uri"
               for c in calls), (
        "_cmd_verify no longer builds a journal-less `from_uri` projection"
    )
    assert any(isinstance(c.func, ast.Attribute) and c.func.attr == "apply"
               and "test-verify" in _text(c) for c in calls), (
        "_cmd_verify no longer WRITES its scratch `test-verify` Point — the "
        "health check can print success over a no-op (#3600)"
    )
    assert any(isinstance(c.func, ast.Attribute) and c.func.attr == "query"
               and "test-verify" in _text(c) and "DELETE" in _text(c)
               for c in calls), (
        "_cmd_verify no longer DELETES its own scratch `test-verify` Point — "
        "it is no longer a self-cleaning health probe"
    )


def test_indexer_production_backfill_route_passes_an_sdk():
    """`backfill_legacy_closed`'s production caller supplies the SDK, so its
    write reaches the journal through `_apply_and_journal`; the `sdk is None`
    arm is a test-helper seam, NOT a live door.

    FAILS IF the production call drops `sdk=` — that would turn the test-seam
    fallback into a live unjournaled production door."""
    tree = ast.parse((REPO_ROOT / "tortoise" / "hosted_api.py").read_text(
        encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "backfill_legacy_closed"
    ]
    assert calls, "hosted_api no longer calls backfill_legacy_closed"
    for call in calls:
        sdk_kw = next((kw for kw in call.keywords if kw.arg == "sdk"), None)
        assert sdk_kw is not None, (
            f"hosted_api:{call.lineno} calls backfill_legacy_closed without "
            "`sdk=` — the sdk-less proj.apply fallback would become a live "
            "unjournaled production door"
        )
        assert not (isinstance(sdk_kw.value, ast.Constant)
                    and sdk_kw.value.value is None), (
            f"hosted_api:{call.lineno} passes `sdk=None` — the function "
            "dispatches on the VALUE (`if sdk is not None`), so this selects "
            "the unjournaled proj.apply fallback; the kwarg NAME being present "
            "is not enough"
        )


_LEGACY_CREATED = {
    "type": "EventRecorded",
    "eventId": "github-issue-a/b-1-created",
    "eventKind": "github.issue.closed",
    "subject": "s",
    "object": "o",
    "startedAt": "2026-01-01T00:00:00Z",
    "endedAt": "2026-01-02T00:00:00Z",
    "source": "github:a/b",
    "sourceKind": "github_issue",
    "participants": [],
}


def _seed_legacy_created(proj, rec: dict) -> None:
    """Seed a PRE-FIX node WITHOUT a journal record.

    A pre-fix binary wrote this node through the raw graph handle with no
    journal bound, so `_seed_legacy_created` issues the equivalent raw write.
    It cannot use `proj.apply` any more: §17.1's guard (this lane) refuses a
    direct fold on a journal-backed projection — which is the point, and the
    raw handle it uses instead is the surface that guard does not yet cover
    (recorded as still-open on #7951). The thing under test here — the
    backfill's JOURNALING — is unaffected by which path seeded the node.
    """
    proj.g.query(
        "CREATE (e:Event {eventId:$eventId, eventKind:$eventKind, "
        "subject:$subject, object:$object, startedAt:$startedAt, "
        "endedAt:$endedAt, source:$source, sourceKind:$sourceKind})",
        params={k: rec[k] for k in (
            "eventId", "eventKind", "subject", "object",
            "startedAt", "endedAt", "source", "sourceKind")},
    )


def test_indexer_sdk_less_fallback_fails_closed_on_a_journal_backed_projection(
        sdk_with_journal):
    """The sdk-less arm is a TEST SEAM and §17.1 now makes that true by force.

    It falls back to the bare `proj.apply` the pre-#7951 signature exposed —
    NOT routed through `_apply_and_journal`, so it neither appends nor proves
    a record. On a JOURNAL-BACKED projection that is exactly a direct cache
    write, so `#7951`'s guard refuses it (`DirectCacheWriteRefused`) and the
    journal stays untouched. FAILS IF the arm starts folding silently again —
    a production caller that forgot `sdk=` would then write the cache with no
    record, which is the hole the AST pin above only catches lexically.
    """
    from tortoise.indexer.github_indexer import GitHubIndexer
    from tortoise.projection import DirectCacheWriteRefused

    sdk = sdk_with_journal
    proj = sdk._get_proj()
    journal = sdk._journal_path_for_test
    # Seed the legacy node WITHOUT journalling (the pre-fix binary's state).
    _seed_legacy_created(proj, _LEGACY_CREATED)
    before = _journal_line_types(journal)

    with pytest.raises(DirectCacheWriteRefused):
        GitHubIndexer(token="t").backfill_legacy_closed(proj)

    assert _journal_line_types(journal) == before, (
        "the sdk-less arm wrote the journal — it has no journal handle, so "
        "this can only be a test double; the seam's shape changed"
    )


def test_indexer_backfill_with_sdk_reaches_the_journal(sdk_with_journal):
    """The sdk-bearing arm — the one production uses — DOES append, through
    `_apply_and_journal`. FAILS IF the production route stops journaling the
    mint, which is what makes the sdk-less arm the only unjournaled one."""
    from tortoise.indexer.github_indexer import GitHubIndexer

    sdk = sdk_with_journal
    proj = sdk._get_proj()
    _seed_legacy_created(proj, _LEGACY_CREATED)

    minted = GitHubIndexer(token="t").backfill_legacy_closed(proj, sdk=sdk)

    assert minted == 1
    types = _journal_line_types(sdk._journal_path_for_test)
    assert "EventRecorded" in types, (
        f"the sdk-bearing backfill did not reach the journal: {types!r}"
    )


def test_mining_emit_is_journal_first():
    """`ConversationMiner._emit_event` is exempt because its ORDER already
    complies — it appends before it folds. FAILS IF that order is swapped."""
    from tortoise.mining import ConversationMiner

    order: list[str] = []

    class _Log:
        def append(self, _record):
            order.append("journal")

    class _Proj:
        def apply(self, _record):
            order.append("fold")

    class _API:
        log = _Log()
        projection = _Proj()

    ConversationMiner._emit_event(
        _API(), {"eventId": "e-1", "eventKind": "meeting", "subject": "s"})

    assert order == ["journal", "fold"], (
        "mining's emitter is no longer journal-first — it may no longer be "
        f"exempt: observed order {order}"
    )


def test_create_entity_deviation_is_documented_in_the_contract():
    """`_create_entity` is a MIXED-order funnel: its Object/Subject/Event
    blocks fold apply-first, while the #2296 `DocumentCreated` arm journals
    FIRST. Both orders are a RECORDED deviation in the module-top contract,
    not a silent door. FAILS IF the documentation is deleted, if the
    Object/Subject/Event blocks stop folding first (the deviation goes stale),
    or if the Document arm stops journalling first (the #2296 durability fix
    has regressed to live-only)."""
    server = REPO_ROOT / "tortoise" / "sdk.py"
    source = server.read_text(encoding="utf-8")
    head = source[:source.index("_JOURNAL_ENVELOPE_KEYS")]
    assert "KNOWN DEVIATIONS" in head, (
        "the journal ordering contract's KNOWN DEVIATIONS block is gone"
    )
    deviation = head.split("KNOWN DEVIATIONS", 1)[1]
    assert "_create_entity" in deviation, (
        "`_create_entity` is no longer named as a known deviation although it "
        "still folds its Object/Subject/Event blocks apply-first"
    )
    assert "apply-first" in deviation, (
        "the `_create_entity` deviation no longer states its form (apply-first)"
    )
    for ref in ("#2194", "#2295", "#2309"):
        assert ref in deviation, (
            f"the deviation no longer records {ref} as the work it rides"
        )
    # The #2296 Document arm is the OTHER order in the same funnel, and the
    # contract says so. Without this, the arm could revert to live-only while
    # the deviation's "Object/Subject/Event" scoping stayed true and BOTH the
    # text and the code-order check below stayed green (the false negative
    # this assertion closes).
    assert "DocumentCreated" in deviation and "#2296" in deviation, (
        "the deviation no longer records the #2296 DocumentCreated arm's "
        "journal-first order"
    )
    # The claim is CONDITIONAL on the CODE ORDER, so pin BOTH orders:
    # documentation presence alone cannot tell whether the deviation is
    # current, and a stale deviation is exactly the "records a past state and
    # is never revalidated" failure class.
    tree = ast.parse(source)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_create_entity"),
              None)
    assert fn is not None, "`_create_entity` no longer exists"

    def _calls(attr: str) -> list:
        return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == attr]

    applies = _calls("apply")
    emits = _calls("_emit_event")
    journal_first = _calls("_journal_entity_event")
    assert applies and emits and journal_first, (
        "`_create_entity` no longer folds, emits AND journal-first-creates — "
        "revisit whether the KNOWN DEVIATIONS entry is still current"
    )
    first_fold = min(a.lineno for a in applies)
    assert first_fold < min(e.lineno for e in emits), (
        "`_create_entity`'s Object/Subject/Event blocks are no longer "
        "apply-first — the KNOWN DEVIATIONS entry is stale and must be removed"
    )
    assert min(j.lineno for j in journal_first) < first_fold, (
        "`_create_entity`'s DocumentCreated arm no longer journals before it "
        "folds — the Document is live-only again (#2296) and the deviation's "
        "'DocumentCreated arm DOES append first' claim is false"
    )

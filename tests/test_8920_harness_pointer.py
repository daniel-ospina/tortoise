"""#8920 review P3 — a renamed harness must keep every pointer to it resolvable.

THE DEFECT THIS PINS
--------------------
PR #8920 moved the `TestClient` background-work quiesce OUT of
`tests/test_export_delete.py` into `tests/_http_fixtures.py` and dropped its
leading underscore (`_quiesce_testclient_background_work` →
`quiesce_testclient_background_work`; `_quiet_boot_sweeps` →
`quiet_boot_sweeps`). The DEFINITIONS moved and were renamed. The REFERENCES
were not, so five of them named symbols that no longer existed anywhere. The
moved docstring had the same problem one level down: it named its callee
`_purge_deleted_teams`, and the callee is `_purge_deleted_orgs` (the
non-vacuity note at the end of this docstring gives the exact count) — a
docstring that misnames the function under test is the worst
case of the same defect, because the enumeration argument below is what keeps
the seam complete.

The one that matters is production source. `tortoise/hosted_api.py` records
this helper as THE harness pattern for the #3472 duplicate-purge overlap — it
is the stated reason the helper is a canonical seam rather than a per-file
copy — and it still read `tests/test_export_delete._quiesce_testclient_background_work`.
A lane following that pointer found nothing, and the enumeration argument the
helper's own docstring makes ("keep it complete: a new sweep reachable from
either entry point belongs in this list") depends on a reader being able to
FIND the list. This is the "relay the answer to the artifact the next lane
reads" discipline, in code: a record that names a deleted symbol is not a
record.

WHAT THIS PINS
--------------
1. Every `tests.<module>.<symbol>` / `tests/<module>.py.<symbol>` pointer in
   production source RESOLVES — the module imports and the attribute exists.
   Scoped to the shape a RENAME breaks; `tests/<file>.py` existence pointers
   and `::node_id` pointers are not resolved here.
2. No source file still names the pre-move symbols — a stale name in a
   comment or docstring is exactly what made (1) un-followable, and prose is
   not reachable by any other guard.
3. The seam is SHARED, not re-copied: the helper is DEFINED exactly once
   (`tests/_http_fixtures.py`) and each consumer IMPORTS it (AST, not grep).
   The consumer list included a FALSE premise until the second review: it
   excluded `test_writer_inventory.py` on the claim that the boot sweep cannot
   reach that module's fake. It can — the same #4816 `rpc_calls` race, merely
   never asserted on. `CONSUMERS` now names it, so this check reddens on the
   pre-fix module (no import) and on any future consumer that copies instead.

NON-VACUITY: on the pre-fix head (`f0d5144`) check 1 reddens on the
`hosted_api.py` pointer (`tests.test_export_delete` has no attribute
`_quiesce_testclient_background_work`) and check 2 reddens on five stale
`_quiesce...`/`_quiet...` occurrences plus eight `_purge_deleted_teams` ones
(seven in `_http_fixtures.py`, one in `test_export_delete.py`) across
`tortoise/hosted_api.py`, `tests/test_export_delete.py` and
`tests/_http_fixtures.py`. Verified by restoring those three files from
`f0d5144` and re-running this module against it. Check 3 reddens with
`test_writer_inventory.py` restored from the same head (`imported=False
called=False`).

Static: no network, no DB, no graph.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
PRODUCTION_DIR = ROOT / "tortoise"

#: `tests.<module>.<symbol>` or `tests/<module>[.py].<symbol>`, inside
#: backticks. Deliberately narrow: a bare file pointer
#: (`tests/test_x.py` — the `.py` lands in the symbol group, filtered by
#: `_NOT_A_SYMBOL`), a data file **whose target is there** (`tests/x.json`), an
#: ellipsis (`tests/...boundary.py`) and a node id (`tests/test_x.py::node`) are
#: out of scope, so this guard needs no per-token escape hatch. ⚠️ Read that
#: data-file clause with `_classify` in front of you: a data file is suppressed
#: by the EVIDENCE conjunct (the file exists), not by an extension list, so the
#: silence it promises holds only while the target is on disk — a pointer to a
#: generated-or-absent fixture is asserted, and reds. That is the same residual
#: `_NOT_A_SYMBOL`'s note states for the `.py` shape.
_POINTER = re.compile(
    r"`(?:tests\.(?P<dotted>[A-Za-z0-9_]+)\.(?P<dsym>[A-Za-z0-9_]+)"
    r"|tests/(?P<path>[A-Za-z0-9_/]+?)(?:\.py)?\.(?P<psym>[A-Za-z0-9_]+))`"
)

#: A trailing token that is an EXTENSION, not a symbol. It covers ONLY the bare
#: file pointer (`tests/test_x.py`, where `.py` lands in the symbol group), in
#: BOTH spellings; every other pointer — either spelling — is recognised by
#: EVIDENCE: `tests/<written>.<symbol>` naming a file that IS THERE
#: (`tests/x.mjs`, `tests/x.jsonl`, `tests/x.c`, or an extension nobody has
#: invented yet), never by an enumeration of extensions, which is only ever as
#: complete as today's citations. Both spellings go through the ONE classifier
#: (`_classify`); the dotted branch is not a second rule.
#:
#: ⛔ MEASURED (#13760 review): `tests/test_middleware_host_consolidation.mjs`,
#: named by `tortoise/hosted_api.py`'s `#13636` note, was being IMPORTED as a
#: Python module because the then-extension-list did not name `mjs` — and such a
#: list is only ever as complete as today's citations (`tests/` also carries
#: `.jsonl`, `.sh`, `.c`, `.html`). A JS harness is not a module in any Python
#: sense, so that assertion had no ground. The token is now classified as a FILE
#: pointer and is LISTED, not asserted — see `_classify`.
#: ⛔ AND THE FIX MUST NOT FAIL OPEN. A first cut of this split classified a token
#: as a FILE pointer whenever `tests/<written>.py` did NOT exist — which is
#: exactly the population a RENAME produces, so the guard stopped catching the
#: one failure it exists for (measured: a synthetic `tests/test_renamed_away
#: .SomeSymbol` was listed, never imported, never reddened, where the pre-split
#: code raised `ModuleNotFoundError`). The classification is therefore
#: EVIDENCE-POSITIVE: a FILE pointer must name a file that IS THERE. Absence is
#: not evidence of non-modulehood.
#: ⚠️ RESIDUAL, stated rather than hidden — and MEASURED, because the first cut
#: of this note named the WRONG case. A `.mjs` pointer whose file is renamed or
#: deleted DOES red: it stops naming a file, so it reclassifies as a SYMBOL
#: pointer and the import fails. The gap is the `.py`-TAILED bare file pointer,
#: which `_NOT_A_SYMBOL` fixes to FILE unconditionally with NO existence check —
#: so a pointer to a deleted `.py` test stays green. That is the reach question
#: filed as #13765, whose title is about exactly this shape.
#:
#: Two `.py` pointers exist in production prose and are NOT citations, so neither
#: population should carry them: `tortoise/referentiality.py` illustrates an
#: identifier-only surface with `tests/x.py` (beside `#4118` and `feat/branch` —
#: there is no `x` module and no rename for this guard to catch), and
#: `tortoise/sdk.py` names `tests/test_backfill_sources.py` as a file #4282
#: DELETED, a sentence that exists to record that it is gone. Both are already
#: handled by `_NOT_A_SYMBOL` alone, so they need no separate escape hatch;
#: #13765 owns removing the second name from that prose.
_NOT_A_SYMBOL = frozenset({"py"})

#: Names the #8920 move renamed away or never existed: the two pre-rename
#: helpers, and the callee the moved docstring misspelled (`_purge_deleted_orgs`
#: is the real function — see `tortoise/hosted_api.py`). A tree that still
#: mentions any of them carries a reference the rename left behind.
STALE_NAMES = (
    "_quiesce_testclient_background_work",
    "_quiet_boot_sweeps",
    "_purge_deleted_teams",
)

SHARED_MODULE = "_http_fixtures"
HELPER = "quiesce_testclient_background_work"
#: The fixture modules that APPLY this seam today. This is NOT a completeness
#: claim, and the distinction is the point: MANY suites open `TestClient(app)`
#: under a monkeypatched fake control plane and let the boot sweep run, e.g.
#: `test_session_login`, `test_onboarding_email_http`,
#: `test_free_team_entitlement` — they tolerate it because NO PIN IN THEM READS
#: `fake.rpc_calls` at all (verified: zero `rpc_calls` references in each). The
#: criterion here is the stronger one — a module that keeps, or may legitimately
#: add, a pin that DOES read the recording (`rpc_calls` index,
#: `len(rpc_calls)`, or exclusive ownership of a shared temp store) must
#: quiesce instead of tolerating it. Modules that already select by name and
#: still open a client (`test_agent_signup_idempotency`, `test_claim_endpoints`,
#: `test_embed_metering`) sit on the tightrope rather than off it.
#:
#: `test_writer_inventory.py` was excluded until the second review on a FALSE
#: premise ("the boot sweep does not reach their fake"). It does — its
#: `supabase_env` fixture monkeypatches `supabase_control.get_control_plane` to
#: `FakeControlPlane` and its `client` fixture enters a real `TestClient(app)`,
#: so the sweep's `metering_repair_period_bounds` landed in `fake.rpc_calls`
#: there too; that module simply never had a pin that noticed. The premise is
#: the defect a future reader would trust before adding an index assertion, so
#: the module now applies the seam and this guard covers it.
#: `test_supabase_control.py` is the one real exclusion AMONG THE MODULES THIS
#: GUARD CLASSIFIES: those tests drive `provision_org` / `_session_user_org`
#: directly with no `TestClient`, so no lifespan, so no sweep (its own index pin
#: is exclusive by construction, not by quiescing).
#:
#: The specific FIXTURES that must apply the seam, per consumer module. This is
#: deliberately FIXTURE-granular: module granularity was a hole a review caught,
#: because a module that keeps one quiescing fixture and loses another (e.g.
#: `sb_client` drops it while `reg_client` keeps it) still has an import and a
#: call somewhere, so a module-level check stays green — and the lost fixture's
#: index pin silently returns to the #4816 1-in-N flake. The index is the fixture
#: FUNCTION name; every one of these is where `TestClient(app)` is entered.
CONSUMER_FIXTURES: dict[str, tuple[str, ...]] = {
    "test_export_delete.py": ("sb_client", "reg_client"),
    "test_pack_state.py": ("supabase_client",),
    # #10067 CI: `sel_client` seeds the registry and then asserts on it, so it
    # is the criterion above in its strongest form — EXCLUSIVE OWNERSHIP of the
    # store the entry's armed sweeps also write. It was the one unquiesced
    # `TestClient(app)` entry in the packs family (`test_pack_state.py`'s
    # sibling fixture already applied the seam), and the cost was measured: a
    # `PUT /v1/graphs/{graph_id}/packs` answered `{"detail":"Unknown
    # organization"}` on PR #10199's `test (c)` with `hosted_api.py`
    # byte-identical to `main`. Named here so losing the call reds this guard
    # rather than silently returning that fixture to the #4816 1-in-N flake.
    "test_pack_selection_by_graph.py": ("sel_client",),
    "test_writer_inventory.py": ("client",),
}

#: The consumer MODULES — the modules above, in a stable order, for the cheaper
#: module-level half of the check (does the module import the seam at all).
CONSUMERS = tuple(CONSUMER_FIXTURES)


def _classify(
    written: str, symbol: str, tests_dir: Path | None = None
) -> tuple[str, str]:
    """Classify a SLASH-form pointer: ("symbol", dotted) or ("file", named path).

    EVIDENCE-POSITIVE, and that is the whole point. A token is a FILE pointer
    only when `tests/<written>.<symbol>` IS a file — any extension, so the rule
    never needs extending — or when its tail is the bare-file extension
    (`_NOT_A_SYMBOL`, the `tests/x.py` shape). Every other token is a SYMBOL
    pointer and IS imported and looked up, INCLUDING a token whose module does
    not exist: that is precisely the renamed-away shape this guard exists to
    red, so its absence must not be what silences the check.

    `tests_dir` is a parameter only so the rule can be pinned on a synthetic
    tree; production callers take the default.
    """
    tests_dir = TESTS_DIR if tests_dir is None else tests_dir
    named = f"tests/{written}.{symbol}"
    if symbol in _NOT_A_SYMBOL or (tests_dir / f"{written}.{symbol}").is_file():
        return "file", named
    return "symbol", f"tests.{written.replace('/', '.')}"


def _production_pointers() -> tuple[
    list[tuple[Path, str, str, str]], list[tuple[Path, str, str]]
]:
    """(symbol pointers, file pointers) written into production source.

    A SYMBOL pointer is (file, token, module, symbol) and is resolved by
    importing the module and looking the symbol up. A FILE pointer is
    (file, token, named path) and is OUT OF SCOPE for the import assertion — it
    is returned so the caller can NAME it (and so the split is testable), not so
    a claim can be made about it.

    The slash form's split is `_classify`'s verdict, which is EVIDENCE-POSITIVE
    on the target (`tests/<written>.<symbol>` exists ⇒ FILE), so a renamed
    module stays a SYMBOL pointer and reds. **The dotted form goes through the
    SAME `_classify`**, so the two spellings cannot drift: a dotted file pointer
    (`tests.x.json`, `tests.x.py`) is LISTED, and a dotted module pointer is
    imported and looked up — one rule, not two.
    """
    symbols: list[tuple[Path, str, str, str]] = []
    files: list[tuple[Path, str, str]] = []
    for path in sorted(PRODUCTION_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _POINTER.finditer(text):
            dotted = match.group("dotted")
            if dotted:
                dsym = match.group("dsym")
                kind, target = _classify(dotted, dsym)
                if kind == "file":
                    files.append((path, match.group(0), target))
                else:
                    symbols.append((path, match.group(0), target, dsym))
                continue
            written = match.group("path")
            symbol = match.group("psym")
            kind, target = _classify(written, symbol)
            if kind == "symbol":
                symbols.append((path, match.group(0), target, symbol))
            else:
                files.append((path, match.group(0), target))
    return symbols, files


def test_production_pointers_into_the_test_tree_resolve():
    pointers, file_pointers = _production_pointers()
    assert pointers, (
        "no `tests.<module>.<symbol>` pointer found under tortoise/ — the "
        "scan itself broke, so this guard would pass vacuously"
    )
    broken: list[str] = []
    for path, token, modname, symbol in pointers:
        try:
            module = importlib.import_module(modname)
        except Exception as exc:
            broken.append(f"{path.relative_to(ROOT)}: {token} → {modname}: {exc}")
            continue
        if not hasattr(module, symbol):
            broken.append(
                f"{path.relative_to(ROOT)}: {token} → {modname}.{symbol} "
                f"does not exist (rename without a relay?)"
            )
    assert not broken, (
        "production source names test symbols that do not resolve:\n  "
        + "\n  ".join(broken)
        + "\n⛔ file pointers are OUT OF SCOPE here and are listed for the next "
        "reader rather than asserted: " + ", ".join(sorted(t for _, _, t in file_pointers))
    )


def test_the_file_pointer_split_is_what_makes_the_mjs_pointer_sane():
    """The `.mjs` pointer that motivated the split is classified as a FILE, not a module.

    A class-complete check cannot be a list of extensions: such a set is only as
    complete as today's citations (`tests/` carries `.jsonl`, `.sh`, `.c`,
    `.html`). So this pins the DECISION for the pointer that broke first: it is
    classified as a FILE pointer, and the import assertion therefore never sees
    it. (Whether a file pointer's target exists is a different question, and is
    NOT asserted here.)
    """
    _, file_pointers = _production_pointers()
    targets = {target for _, _, target in file_pointers}
    assert "tests/test_middleware_host_consolidation.mjs" in targets, (
        "the `.mjs` harness pointer is no longer classified as a FILE pointer — "
        f"file pointers now are {sorted(targets)}"
    )


def test_the_split_is_evidence_positive_so_a_renamed_module_still_reds(tmp_path: Path):
    """A pointer to a module that no longer exists is a SYMBOL pointer, not a silent FILE.

    This is the regression the split's FIRST cut introduced: classifying by
    "`tests/<written>.py` is absent ⇒ FILE pointer" sent exactly the renamed-away
    population to the un-asserted side, so the guard stopped catching the one
    shape it exists for. Evidence-positive means absence proves nothing.
    """
    assert _classify("test_renamed_away", "SomeSymbol", tests_dir=tmp_path) == (
        "symbol",
        "tests.test_renamed_away",
    )


def test_the_split_covers_both_branches_at_this_head(tmp_path: Path):
    """Both classification branches are exercised on a synthetic tree.

    The real tree carries no slash-form SYMBOL pointer at this head (all four
    come from the dotted branch), so `assert pointers` in the resolve test is
    satisfied WITHOUT the slash rule ever deciding True. A unit test is the only
    thing that pins that branch's rule rather than trusting it.
    """
    (tmp_path / "test_present.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "harness.mjs").write_text("// js\n", encoding="utf-8")
    # module file exists ⇒ SYMBOL pointer (imported + looked up)
    assert _classify("test_present", "X", tests_dir=tmp_path) == (
        "symbol",
        "tests.test_present",
    )
    # a file that IS there ⇒ FILE pointer, whatever its extension
    assert _classify("harness", "mjs", tests_dir=tmp_path) == (
        "file",
        "tests/harness.mjs",
    )
    # the bare-file shape ⇒ FILE pointer, by `_NOT_A_SYMBOL` alone (which is why
    # the `.py`-tailed non-citations need no separate escape hatch)
    assert _classify("test_present", "py", tests_dir=tmp_path) == (
        "file",
        "tests/test_present.py",
    )
    assert _classify("x", "py", tests_dir=tmp_path) == ("file", "tests/x.py")
    # a dotted file pointer goes through the SAME rule, and is listed not asserted
    assert _classify("harness", "mjs", tests_dir=tmp_path) == (
        "file",
        "tests/harness.mjs",
    )


def test_no_source_still_names_the_pre_move_symbols():
    # This guard must SPELL the names it forbids, so it is the one file
    # exempt from its own scan; every other module — and the production tree —
    # is checked.
    itself = Path(__file__).name
    hits: list[str] = []
    for root in (PRODUCTION_DIR, TESTS_DIR):
        for path in sorted(root.rglob("*.py")):
            if path.name == itself:
                continue
            text = path.read_text(encoding="utf-8")
            if not any(name in text for name in STALE_NAMES):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for name in STALE_NAMES:
                    if name in line:
                        hits.append(f"{path.relative_to(ROOT)}:{lineno}: {name}")
    assert not hits, (
        "the #8920 move renamed these symbols; a comment or docstring still "
        "naming the old ones points a reader at nothing:\n  "
        + "\n  ".join(hits)
    )


def test_the_seam_is_defined_once_and_imported_by_its_consumers():
    shared = importlib.import_module(f"tests.{SHARED_MODULE}")
    assert callable(getattr(shared, HELPER, None)), (
        f"tests.{SHARED_MODULE}.{HELPER} must exist and be callable — "
        "tortoise/hosted_api.py records it as the canonical harness pattern"
    )

    define = re.compile(rf"^[ \t]*def[ \t]+{HELPER}[ \t]*\(", re.MULTILINE)
    definers = [
        path.name
        for path in sorted(TESTS_DIR.rglob("*.py"))
        if define.search(path.read_text(encoding="utf-8"))
    ]
    assert definers == [f"{SHARED_MODULE}.py"], (
        f"{HELPER} must be DEFINED exactly once (tests/{SHARED_MODULE}.py); "
        f"found definitions in {definers}. A per-consumer copy stops being "
        "updated when `_lifespan` grows a new background caller — the "
        "#3472/#3505 defect."
    )

    missing: list[str] = []
    for consumer in CONSUMERS:
        source = (TESTS_DIR / consumer).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = any(
            isinstance(node, ast.ImportFrom)
            and node.module == f"tests.{SHARED_MODULE}"
            and any(alias.name == HELPER for alias in node.names)
            for node in ast.walk(tree)
        )
        # AST, not a grep: a commented-out call, or a docstring code sample,
        # would satisfy a substring search and pass a fixture that imports the
        # seam and never applies it — the exact inert-patch shape this exists
        # to prevent.
        called = any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == HELPER)
                or (isinstance(node.func, ast.Attribute)
                    and node.func.attr == HELPER)
            )
            for node in ast.walk(tree)
        )
        if not imported or not called:
            missing.append(
                f"{consumer}: imported={imported} called={called}"
            )
    assert not missing, (
        "these fixtures must IMPORT the shared quiesce and apply it:\n  "
        + "\n  ".join(missing)
    )

    # FIXTURE-granular half (a review caught the hole): the loop above is
    # satisfied by ONE call anywhere in the module, so a consumer that keeps
    # `reg_client`'s call and loses `sb_client`'s stays green — and that lost
    # fixture's index pin silently returns to the #4816 1-in-N flake. Each
    # named fixture must itself call the seam.
    unquiesced: list[str] = []
    for module, fixtures in CONSUMER_FIXTURES.items():
        tree = ast.parse((TESTS_DIR / module).read_text(encoding="utf-8"))
        bodies = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for fixture in fixtures:
            node = bodies.get(fixture)
            if node is None:
                unquiesced.append(f"{module}:{fixture} — fixture not found")
                continue
            applies = any(
                isinstance(call, ast.Call)
                and (
                    (isinstance(call.func, ast.Name)
                     and call.func.id == HELPER)
                    or (isinstance(call.func, ast.Attribute)
                        and call.func.attr == HELPER)
                )
                for call in ast.walk(node)
            )
            if not applies:
                unquiesced.append(
                    f"{module}:{fixture} — does not call {HELPER}"
                )
    assert not unquiesced, (
        "these fixtures open `TestClient(app)` on a shared store and must each "
        "apply the quiesce themselves (a module-level call is not enough):\n  "
        + "\n  ".join(unquiesced)
    )

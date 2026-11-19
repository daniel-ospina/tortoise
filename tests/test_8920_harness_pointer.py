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
#: `_NOT_A_SYMBOL`), a data file (`tests/x.json`), an ellipsis
#: (`tests/...boundary.py`) and a node id (`tests/test_x.py::node`) are out of
#: scope, so this guard needs no per-token escape hatch.
_POINTER = re.compile(
    r"`(?:tests\.(?P<dotted>[A-Za-z0-9_]+)\.(?P<dsym>[A-Za-z0-9_]+)"
    r"|tests/(?P<path>[A-Za-z0-9_/]+?)(?:\.py)?\.(?P<psym>[A-Za-z0-9_]+))`"
)

#: A trailing token that is an EXTENSION, not a symbol. It survives only for the
#: BARE file pointer (`tests/test_x.py`, where `.py` lands in the symbol group);
#: every other file pointer is classified by `_production_pointers`'s own rule —
#: a token is a SYMBOL pointer only if `tests/<written>.py` exists and its tail
#: is not an extension — so this set is ONE short conjunct, not the classifier:
#: a new extension never has to be added to it for a file pointer to be
#: recognised (the existence test does that), which is what stops the treadmill.
#:
#: ⛔ MEASURED (#13760 review): `tests/test_middleware_host_consolidation.mjs`,
#: named by `tortoise/hosted_api.py`'s `#13636` note, was being IMPORTED as a
#: Python module because the then-extension-list did not name `mjs` — and such a
#: list is only ever as complete as today's citations (`tests/` also carries
#: `.jsonl`, `.sh`, `.c`, `.html`). A JS harness is not a module in any Python
#: sense, so that assertion had no ground. The token is now classified as a FILE
#: pointer and is LISTED, not asserted — see `_production_pointers`.
#: ⚠️ RESIDUAL, stated rather than hidden: file pointers are not existence-checked
#: here (a `tests/x.py` placeholder in prose and a genuinely deleted test file
#: look the same to this guard). That is a separate reach question, filed as
#: #13765 — with the two lines it would red measured there, which is why it is not
#: switched on here.
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


def _production_pointers() -> tuple[
    list[tuple[Path, str, str, str]], list[tuple[Path, str, str]]
]:
    """(symbol pointers, file pointers) written into production source.

    A SYMBOL pointer is (file, token, module, symbol) and is resolved by
    importing the module and looking the symbol up. A FILE pointer is
    (file, token, named path) and is OUT OF SCOPE for the import assertion — it
    is returned so the caller can NAME it (and so the split is testable), not so
    a claim can be made about it.

    ⛔ The split is decided by whether `tests/<written>.py` exists, with a
    one-member extension set (`_NOT_A_SYMBOL = {"py"}`) covering the bare
    `tests/x.py` shape — i.e. by the TARGET, not by an enumeration of
    extensions, which is only ever as complete as today's citations. `tests/_embedded._projection_host` is a
    symbol pointer (the module file exists); `tests/x.mjs`, `tests/x.json` and a
    bare `tests/x.py` are file pointers (no module file to import).
    """
    symbols: list[tuple[Path, str, str, str]] = []
    files: list[tuple[Path, str, str]] = []
    for path in sorted(PRODUCTION_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _POINTER.finditer(text):
            dotted = match.group("dotted")
            if dotted:
                symbols.append(
                    (path, match.group(0), f"tests.{dotted}", match.group("dsym"))
                )
                continue
            written = match.group("path")
            symbol = match.group("psym")
            if symbol not in _NOT_A_SYMBOL and (TESTS_DIR / f"{written}.py").is_file():
                symbols.append(
                    (path, match.group(0), f"tests.{written.replace('/', '.')}", symbol)
                )
            else:
                files.append((path, match.group(0), f"tests/{written}.{symbol}"))
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

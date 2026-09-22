"""Wave-3 lint tripwire (issue #2143, follow-on to #2127): fail loud at
pytest COLLECTION on any tests/ module that monkeypatches
``TortoiseSDK.__init__`` without a ``TORTOISE_DB_PATH`` pin — the #2090
keepalive-churn shape this umbrella exists to eliminate.

Why a `test_*.py` module and NOT a conftest hook: an edit to
``tests/conftest.py`` ∈ SHARED_MODULES (tools/ci_selection.py) flips the
shipping PR to the full docker matrix. Registering THIS module in
config/ci-surfaces.yml ``tier1`` makes pytest collect it on every run —
tier-1 smoke is the base of every tier-2 PR selection and of full-matrix
runs (docs-only PRs run tier 1 too) — while the changed-file set stays
``config/*`` + ``tests/*`` → tier-2 ``full=false`` (verified via
``tools/ci_selection.py``; see the PR body). Conftest never imports this
module; pytest collects it as an ordinary test file.

The check runs at module IMPORT (collection time) and reads SOURCE files
from disk — never git history: CI test jobs use shallow fetch-depth:1, so
a vcs-diff failure at collection must never fail the suite (settled
decision 2, #2090 scoping doc). git-diff is at most a local refinement.

The pattern (settled, #2143 decision 4): a module that patches the
``TortoiseSDK.__init__`` CLASS METHOD (any module alias — tortoise/
hosted_api.py:60 aliases the class from tortoise.sdk, so
``hosted_api.TortoiseSDK is tortoise.sdk.TortoiseSDK`` and hosted_api's
keepalive constructions at hosted_api.py:141/:165/:174/:191/:206 route
through that ONE class object) WITHOUT a ``TORTOISE_DB_PATH`` env-pin
write anywhere in the module (the #1950/#2090 churn-prevention
mechanism). The alias SPELLING (ha_mod. vs sdk_mod. vs tortoise.sdk. vs
bare) is NOT a sound discriminator — every spelling reaches the same
class method — so the pin is the scope discriminator. sdk_props_coercion
(#451 spy) and pack_manifest_store(+extraction) (namespace _route_patch)
patch via the sdk alias and are pre-existing DELIBERATE non-helper blocks
→ baseline allowlist, warn-only (wave-1b audit, #2127).

Detection is AST-based so comments/docstrings never count as patch sites
or pins. Enforcement is runtime allowlist membership: pattern ∧ ∉
config/test-lint-allowlist.yml → collection-time error (fail-loud);
pattern ∧ ∈ allowlist → warn (non-blocking) via ``warnings.warn`` → the
pytest warnings summary (the visible channel — CI output is buffered to
/tmp/pytest.log). The allowlist is a config/ DATA file so allowlist
changes select tier-2 ``full=false`` (config changes select the core
surface), per the #2090 scoping doc's "Lint tripwire" section.

Fail-loud semantics (code-review corrected): a RuntimeError at module
import is a pytest COLLECTION error → pytest aborts the containing run
by default (no --continue-on-collection-errors in CI; observed
"Interrupted: 1 error during collection"). The error message enumerates
EVERY violating module + its first patch-site line, so diagnosis is
complete even though nothing else in that run executes.

Known limitations (stated so the gate is not over-trusted):
- Enforcement fires only when pytest collects this module (tier1 = the
  base of every CI selection and of full local runs). Per-file dev runs
  (``pytest tests/test_foo.py``) bypass it — the tripwire is a CI gate,
  red in CI but possibly green locally until the full suite is collected.
- The patch/pin forms recognized are enumerated (see the helpers): a
  spelling outside the enumeration is invisible. Current coverage spans
  the repo's actual idioms (object + string ``mock.patch``/``setattr``/
  ``patch.object`` targets, bare assignments, ``os.environ[...]=`` /
  ``setdefault`` / ``putenv`` / bare-``setenv`` / ``update`` /
  ``mock.patch.dict`` / ``setitem`` pins). A class-LEVEL alias (``from
  tortoise.sdk import TortoiseSDK as X`` then ``X.__init__ = …``) or a
  helper variable (``INIT = "__init__"``) is NOT tracked.
- The boundary is the ``TortoiseSDK.__init__`` patch — patching a method
  that runs inside ``__init__`` (e.g. ``FalkorProjection.__init__``) or
  a plain unpinned real ``TortoiseSDK()`` construction is out of scope
  (test_sdk_props_coercion.py:337/:355 already patches
  ``FalkorProjection.__init__`` legitimately).
- Unparseable test modules are skipped (they red their own collection; a
  new unlisted file reds the ci_selection --integrity drift gate).
- Baselines warn only; nothing turns warnings into errors in CI
  (no -W error / filterwarnings in pyproject or workflows).
"""
from __future__ import annotations

import ast
import warnings
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TESTS = _REPO / "tests"
_ALLOWLIST_FILE = _REPO / "config" / "test-lint-allowlist.yml"

# The #2090/#1950 pin: an env WRITE of TORTOISE_DB_PATH anywhere in the
# module. Recognized forms (AST): os.environ["TORTOISE_DB_PATH"] = …;
# os.environ.setdefault / os.putenv / monkeypatch.setenv("TORTOISE_DB_PATH",
# …). Restores (os.environ.pop / monkeypatch.delenv) deliberately do NOT
# count — a file that patches and pops without ever pinning is churn-shaped.
_PIN_NAME = "TORTOISE_DB_PATH"


def _chain_contains(node: ast.expr, ident: str) -> bool:
    """True when an expression's attribute chain carries the identifier.

    ``sdk_mod.TortoiseSDK`` -> chain components sdk_mod, TortoiseSDK: the
    class component appears as an Attribute attr, not the base Name, so
    check every component (base Name + each Attribute attr).
    """
    while isinstance(node, ast.Attribute):
        if node.attr == ident:
            return True
        node = node.value
    return isinstance(node, ast.Name) and node.id == ident


def _is_pin_write(node: ast.AST) -> bool:
    """True when *node* is a TORTOISE_DB_PATH env-pin WRITE (never a pop)."""
    # os.environ["TORTOISE_DB_PATH"] = <path>
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        return (isinstance(target, ast.Subscript)
                and _chain_contains(target.value, "environ")
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == _PIN_NAME)
    if isinstance(node, ast.Call):
        fn = node.func
        args = node.args
        # os.environ.setdefault("TORTOISE_DB_PATH", …) / os.putenv(…)
        if (isinstance(fn, ast.Attribute)
                and fn.attr in ("setdefault", "putenv", "setenv")
                and args and isinstance(args[0], ast.Constant)
                and args[0].value == _PIN_NAME):
            return True
        # bare setenv("TORTOISE_DB_PATH", …) (monkeypatch.setenv etc.)
        if (isinstance(fn, ast.Name) and fn.id == "setenv" and args
                and isinstance(args[0], ast.Constant)
                and args[0].value == _PIN_NAME):
            return True
        # os.environ.update({"TORTOISE_DB_PATH": …}) — kwargs form too
        # (os.environ.update(TORTOISE_DB_PATH=…)) and any receiver whose
        # .update takes a dict carrying the literal key.
        if (isinstance(fn, ast.Attribute) and fn.attr == "update"
                and _call_dict_has_key(node)):
            return True
        # mock.patch.dict(os.environ, {"TORTOISE_DB_PATH": …}) / kwargs form
        if (isinstance(fn, ast.Attribute) and fn.attr == "dict"
                and _chain_contains(fn.value, "patch")
                and _call_dict_has_key(node)):
            return True
        # monkeypatch.setitem(os.environ, "TORTOISE_DB_PATH", …)
        if (isinstance(fn, ast.Attribute) and fn.attr == "setitem"
                and len(args) >= 2 and isinstance(args[1], ast.Constant)
                and args[1].value == _PIN_NAME):
            return True
    return False


def _call_dict_has_key(node: ast.Call) -> bool:
    """True when any positional/keyword arg of the call is a dict literal
    (or kwargs-call) whose keys include the TORTOISE_DB_PATH literal.

    Covers os.environ.update({"TORTOISE_DB_PATH": …}), the kwargs form
    os.environ.update(TORTOISE_DB_PATH=…), and
    mock.patch.dict(os.environ, {"TORTOISE_DB_PATH": …}, …).
    """
    for arg in node.args:
        if isinstance(arg, ast.Dict) and _dict_keys_contain(arg, _PIN_NAME):
            return True
    for kw in node.keywords:
        if kw.arg == _PIN_NAME:  # os.environ.update(TORTOISE_DB_PATH=…)
            return True
        if (isinstance(kw.value, ast.Dict)
                and _dict_keys_contain(kw.value, _PIN_NAME)):
            return True
    return False


def _dict_keys_contain(node: ast.Dict, key: str) -> bool:
    """True when the dict literal's keys include *key* (a string literal)."""
    return any(isinstance(k, ast.Constant) and k.value == key
               for k in node.keys)


def _is_patch_site(node: ast.AST) -> int | None:
    """Return the line of a TortoiseSDK.__init__ monkeypatch, else None.

    Covered forms (any module alias — the class object is shared):
    - ``<x>.TortoiseSDK.__init__ = <fn>``  (assignment + restore)
    - ``monkeypatch.setattr(<x>TortoiseSDK, "__init__", …)`` / setattr(…)
    - ``mock.patch.object(<x>TortoiseSDK, "__init__", …)``
    - string targets: ``setattr("tortoise.sdk.TortoiseSDK.__init__", …)``
      and ``mock.patch("…TortoiseSDK.__init__", …)``
    """
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if (isinstance(target, ast.Attribute) and target.attr == "__init__"
                and _chain_contains(target.value, "TortoiseSDK")):
            return node.lineno
        return None
    if isinstance(node, ast.Call) and len(node.args) >= 1:
        a0 = node.args[0]
        # String target containing the dotted method path is self-validating
        # even with keyword-only extras: mock.patch("…TortoiseSDK.__init__",
        # new=…/return_value=…) — the second positional is NOT required.
        if (isinstance(a0, ast.Constant) and isinstance(a0.value, str)
                and "TortoiseSDK.__init__" in a0.value):
            return node.lineno
        # Split class-string target: setattr("…TortoiseSDK", "__init__", fn)
        # / patch.object("…TortoiseSDK", "__init__", …) — the repo's
        # dominant string-mocking idiom (test_enforcement.py etc.).
        if (len(node.args) >= 2
                and isinstance(a0, ast.Constant) and isinstance(a0.value, str)
                and "TortoiseSDK" in a0.value
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "__init__"):
            return node.lineno
        # Object target: chain containing TortoiseSDK + a1 == "__init__"
        # (setattr(ha_mod.TortoiseSDK, "__init__", fn) etc.).
        if (len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "__init__"
                and _chain_contains(a0, "TortoiseSDK")):
            return node.lineno
    return None


def _module_matches(tree: ast.Module) -> tuple[list[int], bool]:
    """(patch-site lines, pinned) for one module's AST.

    Callers pre-filter on the raw text (a module that never mentions
    TortoiseSDK cannot patch the method) so the ~440 TortoiseSDK-free test
    files skip the AST walk entirely.
    """
    patch_lines: list[int] = []
    pinned = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Call)):
            if not patch_lines:
                hit = _is_patch_site(node)
                if hit is not None:
                    patch_lines.append(hit)
            if not pinned and _is_pin_write(node):
                pinned = True
    return patch_lines, pinned


def _load_allowlist() -> list[str]:
    """Load the allowlist data file — FAIL CLOSED on any loading error.

    A missing/corrupt allowlist must never silently disable the tripwire:
    with no data the check cannot tell baseline from new violation, so the
    safe direction is a loud collection error with a fix instruction.
    """
    try:
        import yaml  # lazy: pyyaml is a dev dependency (uv group)
        raw = yaml.safe_load(_ALLOWLIST_FILE.read_text())
    except Exception as exc:  # fail-closed by design (see docstring)
        raise RuntimeError(
            f"test-lint tripwire (#2143): cannot load the allowlist data "
            f"file {_ALLOWLIST_FILE}: {exc!r}. The tripwire never runs "
            "silently-disabled — restore the file (or empty its allowlist "
            "list to flip every baseline to fail-loud)."
        ) from exc
    if not isinstance(raw, dict) or "allowlist" not in raw:
        raise RuntimeError(
            f"test-lint tripwire (#2143): {_ALLOWLIST_FILE} must be a YAML "
            "mapping with an `allowlist:` list of repo-relative tests/ paths."
        )
    entries = raw["allowlist"]
    if not isinstance(entries, list) or not all(
            isinstance(e, str) and e.startswith("tests/") for e in entries):
        raise RuntimeError(
            f"test-lint tripwire (#2143): {_ALLOWLIST_FILE} `allowlist` must "
            "be a list of repo-relative paths starting with 'tests/'."
        )
    return entries


def _scan() -> dict:
    """Scan every tests/ module (recursive, tests/e2e excluded) for the
    churn shape. Returns {scanned, violations, baselines, stale}.

    - violation: matches the pattern ∧ NOT allowlisted (fail-loud).
    - baseline: matches the pattern ∧ allowlisted (warn-only).
    - stale: allowlisted but no longer matches (or the file is gone) —
      remove the entry (a migrated baseline flips to fail on removal).
    """
    allowlist = set(_load_allowlist())
    violations: list[tuple[str, int]] = []
    baselines: list[str] = []
    stale: list[str] = []
    scanned = 0
    matched_paths: set[str] = set()
    for path in sorted(_TESTS.rglob("*.py")):
        if "e2e" in path.parts:
            continue  # browser suite (separate jobs, excluded from python CI)
        if path.name == "test_lint_tripwire.py":
            # Self-skip: this module embeds the churn shapes as inert string
            # DATA for its detector unit tests (ast.parse'd, never executed),
            # so its own text legitimately contains "TortoiseSDK.__init__".
            # It cannot be a churn violator by construction.
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        rel = path.relative_to(_REPO).as_posix()
        scanned += 1
        # Text prefilter: a module that never mentions TortoiseSDK cannot
        # patch the method; a mention without the literal "__init__"
        # cannot patch it either (every recognized form carries it) — so
        # only files with BOTH pay an ast.parse+walk (54 of 452; a scan
        # is ~0.3s, collected on every CI run incl. docs-only tier-1).
        if "TortoiseSDK" not in text or "__init__" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            # An unparseable test module is its own loud failure elsewhere;
            # never let the tripwire's parser red the suite (settled
            # decision 2's spirit: the gate must not self-fail).
            continue
        patch_lines, pinned = _module_matches(tree)
        if not patch_lines:
            continue
        if pinned:
            continue  # the #1950/#2090 pin is the scope discriminator
        matched_paths.add(rel)
        if rel in allowlist:
            baselines.append(rel)
        else:
            violations.append((rel, patch_lines[0]))
    for entry in sorted(allowlist):
        # Stale: allowlisted but the module no longer matches the pattern
        # (migrated onto the helper/pinned) or the file is gone — removing
        # the entry is how a migrated baseline flips to fail-loud.
        if (_REPO / entry).exists() is False or entry not in matched_paths:
            stale.append(entry)
    return {"scanned": scanned, "violations": violations,
            "baselines": baselines, "stale": stale}


def _run_scan(*, warn: bool) -> dict:
    """Scan + enforce. Called at import (warn=True) and from the test.

    Raises on violations (fail-loud at collection when called from the
    module body); emits warnings for baselines and stale allowlist entries
    (the warn channel — pytest warnings summary / /tmp/pytest.log).
    """
    report = _scan()
    for rel in report["baselines"]:
        if warn:
            warnings.warn(
                f"{rel}: #2143 test-lint baseline — patches TortoiseSDK.__init__ "
                "without a TORTOISE_DB_PATH pin (allowlisted in "
                "config/test-lint-allowlist.yml → warn-only). Migrate onto "
                "tests._http_fixtures.patched_tortoise_sdk and remove the "
                "allowlist entry to clear this warning.",
                stacklevel=2,
            )
    for rel in report["stale"]:
        if warn:
            warnings.warn(
                f"{rel}: stale #2143 test-lint allowlist entry — the file no "
                "longer matches the unpinned TortoiseSDK.__init__ patch "
                "pattern (or is gone). Remove it from "
                "config/test-lint-allowlist.yml.",
                stacklevel=2,
            )
    violations = report["violations"]
    if violations:
        listed = "\n".join(
            f"  - {rel} (first patch site at line {line})"
            for rel, line in violations)
        raise RuntimeError(
            f"test-lint tripwire (#2143): {len(violations)} tests/ module(s) "
            f"patch TortoiseSDK.__init__ without a TORTOISE_DB_PATH pin (the "
            f"#2090 keepalive-churn shape):\n{listed}\n"
            "Fix: migrate the module onto "
            "tests._http_fixtures.patched_tortoise_sdk (see "
            "tests/test_export_delete.py / tests/test_import_endpoint.py) — "
            "or, for a deliberate non-helper patch (namespace routing, "
            "pass-through spy), add the file to config/test-lint-allowlist.yml "
            "with a rationale comment (config/ changes select tier-2 "
            "full=false)."
        )
    return report


# ── Enforcement at collection ──────────────────────────────────────────────
# Module-body call: pytest imports this module while collecting the suite
# (it rides `tier1`, the base of every selection) → a NEW unpinned patch
# module anywhere in tests/ fails THIS module's import → pytest treats the
# import-time RuntimeError as a COLLECTION error and ABORTS the run
# (default; no --continue-on-collection-errors in CI) — fail-loud with the
# error message naming every violating module + line.
_SCAN_REPORT = _run_scan(warn=True)


def test_lint_tripwire_sweep_clean():
    """Sweep-clean backstop. The import-time scan already enforced; this
    asserts the stored report is clean at run time. If a future edit removes
    the module-body call, _SCAN_REPORT is undefined → NameError here (and
    nothing warns) — still loud, never silent. (The runtime re-scan variant
    was dropped in code review: it doubled the ~0.3s scan cost on every CI
    run for no enforcement gain.)"""
    if _SCAN_REPORT is None:  # pragma: no cover - defensive
        raise AssertionError(
            "test-lint tripwire (#2143): module-body _run_scan(warn=True) "
            "call was removed — restore it so enforcement fires at collection.")
    assert not _SCAN_REPORT["violations"], _SCAN_REPORT["violations"]


def _src(py: str) -> tuple[list[int], bool]:
    """classify one synthetic module source: (patch_lines, pinned)."""
    return _module_matches(ast.parse(py))


class TestPatchSiteDetection:
    """Synthetic-source pins for the detector (green-tree unit tests — the
    tree must be clean for this module to import at all)."""

    def test_churn_assignment_form(self):
        lines, pinned = _src(
            'import os\n'
            'def _orig(db): return None\n'
            'ha_mod.TortoiseSDK.__init__ = _orig\n')
        assert lines == [3] and not pinned

    def test_pinned_env_write_is_not_a_violation_shape(self):
        _, pinned = _src(
            'import os\n'
            'os.environ["TORTOISE_DB_PATH"] = db\n'
            'ha_mod.TortoiseSDK.__init__ = _orig\n')
        assert pinned

    def test_setattr_object_target(self):
        lines, pinned = _src(
            'monkeypatch.setattr(ha_mod.TortoiseSDK, "__init__", _orig)\n')
        assert lines == [1] and not pinned

    def test_setattr_string_class_target(self):
        lines, _ = _src(
            'monkeypatch.setattr("tortoise.hosted_api.TortoiseSDK",'
            ' "__init__", _orig)\n')
        assert lines == [1]

    def test_mock_patch_string_method_target_keyword_extras(self):
        # P1 code-review regression: keyword-only extras must not hide the
        # self-validating dotted string target.
        lines, _ = _src(
            'with mock.patch("tortoise.sdk.TortoiseSDK.__init__",'
            ' return_value=None):\n    pass\n')
        assert lines == [1]

    def test_patch_object_object_target(self):
        lines, _ = _src(
            'mock.patch.object(sdk_mod.TortoiseSDK, "__init__",'
            ' return_value=None)\n')
        assert lines == [1]

    def test_comments_and_docstrings_never_count(self):
        lines, pinned = _src(
            '"""docstring: TortoiseSDK.__init__ pinned via os.environ... """\n'
            '# comment: monkeypatch.setattr(ha_mod.TortoiseSDK, "__init__"...)\n'
            'x = 1\n')
        assert not lines and not pinned

    def test_unrelated_patch_is_ignored(self):
        lines, _ = _src(
            'monkeypatch.setattr(ha_mod.FalkorProjection, "__init__", _orig)\n')
        assert not lines


class TestPinDetection:
    """Pin-form pins (code-review P2 regressions: update / patch.dict /
    setitem / kwargs forms must count as pins)."""

    def test_environ_update_dict(self):
        _, pinned = _src(
            'os.environ.update({"TORTOISE_DB_PATH": db_path})\n'
            'ha_mod.TortoiseSDK.__init__ = _orig\n')
        assert pinned

    def test_environ_update_kwargs(self):
        _, pinned = _src(
            'os.environ.update(TORTOISE_DB_PATH=db_path)\n'
            'ha_mod.TortoiseSDK.__init__ = _orig\n')
        assert pinned

    def test_patch_dict_env(self):
        _, pinned = _src(
            'with mock.patch.dict(os.environ, '
            '{"TORTOISE_DB_PATH": db_path}):\n    pass\n'
            'ha_mod.TortoiseSDK.__init__ = _orig\n')
        assert pinned

    def test_setitem_env(self):
        _, pinned = _src(
            'monkeypatch.setitem(os.environ, "TORTOISE_DB_PATH", db_path)\n'
            'ha_mod.TortoiseSDK.__init__ = _orig\n')
        assert pinned

    def test_pop_restore_does_not_count_as_pin(self):
        _, pinned = _src(
            'os.environ.pop("TORTOISE_DB_PATH", None)\n'
            'monkeypatch.delenv("TORTOISE_DB_PATH")\n'
            'ha_mod.TortoiseSDK.__init__ = _orig\n')
        assert not pinned

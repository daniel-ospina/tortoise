"""#4097 — the declared env-truthiness contract, and the guards that keep it single.

Three things are pinned here:

  1. **The contract** (`tortoise/env_truthy.py`) — the vocabulary and the full input
     matrix, including the cells an operator actually writes (`0`/`false`/`""`/absent/
     garbage -> False).
  2. **The migration property** — every migrated site must agree with the predicate it
     replaced. The pre-change predicates are transcribed below from `git show
     HEAD:<path>` as an INDEPENDENT copy (never an import of the new leaf): comparing a
     resolver to the new contract cannot detect a behaviour change, so this is the only
     mechanism that can. A site may not narrow; it may only widen at its declared cell.
  3. **Non-divergence** — an AST scan that reds on a new ad-hoc vocabulary literal or a
     new/extra narrow `== "1"` read outside the frozen ledger of reads deferred to
     #4128, *within its declared scan surface*.

**Declared scan boundary.** The scan covers `tortoise/**/*.py` + `tests/_embedded.py`.
It does NOT police:

- V5 presence reads (`if os.environ.get(X)`);
- a vocabulary with no `"1"`/`"0"` anchor (e.g. `{"true","yes","on"}`);
- a vocabulary composed at RUNTIME (`"1 true yes on".split()`, `"".join(...)`);
- `getattr(os.environ, ...)` reads;
- a `match` statement (`match os.environ.get(X): case "1":`) — only `Compare` nodes are
  inspected;
- a **two-step** alias chain (`_a = os.environ.get(X); _r = _a`) — one step
  (`_r = ...`, `_r: str = ...`, `(_r := ...)`) IS resolved;
- a name whose binding is SHADOWED across scopes: the constant maps are file-global and
  first-wins (plain assignments before annotated ones), so a same-named LOCAL plain
  binding can defeat a module-level ANNOTATED anchor (`_ONE: str = "1"`). The two are
  the same configuration — the fix that makes a module-level plain binding beat an
  earlier local annotated one is what makes this miss — so no global first-wins map
  resolves both, and `test_scanner_declares_the_cross_scope_shadowing_residual` pins it;
- `tests/` beyond `_embedded.py`, `tools/`, `graph-scripts/`, `apps/` — which already
  carry their own literals (`tests/test_product_rerank.py`, `tests/test_monitoring.py`,
  `tests/test_reaper.py`, `tests/test_eval_ingest_cache.py`,
  `tests/test_extractor_v2.py`, `tests/longmem_eval/test_assembly_arm.py`,
  `tests/eval/why_suite/test_why_suite_ab.py`, `tests/test_email_signup.py`,
  `apps/graph-viz/server/connection.py`).

A split comparison (`raw == "1" or raw == "true"`) IS caught whenever its operands
resolve — each half is its own `Compare`. Two clauses deliberately OVER-approximate, in
the fail-closed direction: the alias map is module-global (a name ever bound to an env
read is treated as that read for the whole module, so an unrelated reuse of the name can
red; conversely, `setdefault` means the FIRST env name bound to a reused local name wins,
so a second one in another scope is invisible), and the `Dict`-key clause flags any dict
whose KEYS look like a vocabulary even when it is a non-env label map. Both are fixable by
raising/adding the relevant ledger entry, or by making the shape unambiguous. The claim
this file supports is therefore "no new divergence **inside the declared surface**, modulo
the shapes listed above" — not "anywhere in the repo, however written". A JS/TS scan of
`website/functions/`, `supabase/functions/`, `client/` and `menu-bar/` found no boolean
env-truthiness parsing, so there is no cross-language duplication to guard.

The scan resolves **at least** the following shapes — this list is illustrative, not an
exhaustive grammar: a shape absent from it and from the boundary list above is unpoliced.
Module-level string constants as env names (plain or annotated), `.strip().lower()`
chains, one level of alias indirection (assignment, annotated assignment and walrus), the
`"1"`/`"0"` anchor on either side of the comparison — written literally or as a
module-level constant bound to it (scalar or Tuple/Set/List) — `environ.get(...)` after
`from os import environ [as X]`, and both `Set`/`Tuple`/`List` and `Dict`-key vocabulary
literals.

The migrated-module imports are deliberately resolved **inside** the tests that need
them, so the pure-AST guard tests below never load the heavy hosted stack.
"""
from __future__ import annotations

import ast
import builtins
import os
import pathlib
import runpy
import sys
from collections import Counter

import pytest

from tortoise.env_truthy import FALSY, TRUTHY, env_flag, is_truthy

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The full input matrix. `None` = the variable is absent.
_MATRIX = [None, "", " ", "\t", "\n", "  \t", "0", "1", " 1 ", "1 ", " 1", "1\t",
           "\t1", "0 ", "true", "TRUE", "True", "yes", "YES", "Yes", "on", "ON", "On",
           "false", "FALSE", "no", "NO", "off", "OFF", "garbage", "2"]

# A site's "declared cell" is a PREDICATE over the raw value, not an enumeration: the
# whitespace forms are a class (`"1\t"`, `" 1"`, `"\n"` all widened at the no-strip
# `== "1"` sites), and an enumerated set silently under-declares them. Inspect the old
# predicate to see which class applies.


def _decl_none(raw: str | None) -> bool:
    """The site was already cell-exact — nothing may newly resolve True."""
    return False


def _decl_strict1(raw: str | None) -> bool:
    """Old: `os.environ.get(N, "") == "1"` (no strip) -> newly True for every other
    declared truthy spelling, including whitespace-trimmed `"1"` forms."""
    return raw is not None and raw != "1" and raw.strip().lower() in TRUTHY


def _decl_narrow1(raw: str | None) -> bool:
    """Old: `... .strip().lower() == "1"` -> newly True only for the OTHER spellings."""
    return raw is not None and raw.strip().lower() in (TRUTHY - {"1"})


def _decl_only_on(raw: str | None) -> bool:
    """Old: `... .strip().lower() in ("1","true","yes")` (V3) -> only "on" was missing."""
    return raw is not None and raw.strip().lower() == "on"


def _decl_blank(raw: str | None) -> bool:
    """Old `cimd._env_flag` carried `""` in its falsy tuple -> blank/whitespace-only
    (but NOT unset, which was already the default) now falls back to the default."""
    return raw is not None and raw.strip() == ""


def _set(monkeypatch, name: str, raw: str | None) -> None:
    """Set `name` to `raw`, or DELETE it when `raw is None` (absent)."""
    if raw is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, raw)


# ── 1. The contract ────────────────────────────────────────────────────────


def test_vocabulary_is_pinned():
    assert frozenset({"1", "true", "yes", "on"}) == TRUTHY
    assert frozenset({"0", "false", "no", "off"}) == FALSY
    assert not (TRUTHY & FALSY)


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "True", "yes", "YES", "on", "On",
                                 " 1 ", "yEs"])
def test_is_truthy_accepts(raw):
    assert is_truthy(raw) is True


@pytest.mark.parametrize("raw", [None, "", " ", "0", "false", "FALSE", "no", "off",
                                 "garbage", "2", "y", "t"])
def test_is_truthy_rejects(raw):
    """`0`/`false`/`""`/absent/garbage are all False — the whole point of #4097."""
    assert is_truthy(raw) is False


@pytest.mark.parametrize("raw", _MATRIX)
def test_env_flag_matrix(monkeypatch, raw):
    _set(monkeypatch, "TORTOISE_T_CONTRACT", raw)
    if raw is None or raw.strip() == "":
        assert env_flag("TORTOISE_T_CONTRACT", True) is True      # blank == unset
        assert env_flag("TORTOISE_T_CONTRACT", False) is False
    elif raw.strip().lower() in TRUTHY:
        assert env_flag("TORTOISE_T_CONTRACT", False) is True
    elif raw.strip().lower() in FALSY:
        assert env_flag("TORTOISE_T_CONTRACT", True) is False
    else:                                                          # garbage -> the default
        assert env_flag("TORTOISE_T_CONTRACT", True) is True
        assert env_flag("TORTOISE_T_CONTRACT", False) is False


# ── 2. The migration property (pre-change oracle) ──────────────────────────


def _old_truthy(name: str, default: str = "") -> bool:
    """The wide-set read as it was before #4097."""
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _old_cimd_flag(name: str) -> bool:
    """`cimd._env_flag` exactly as it was before #4097 (its falsy tuple includes "")."""
    raw = os.environ.get(name)
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _old_tristate(name: str, default: bool) -> bool:
    """`retrieval.ask_env_bool` exactly as it was before #4097."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _old_env_bool(name: str, default: bool = False) -> bool:
    """`backup_config._env_bool` exactly as it was before #4097."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


#: Static label list — parametrizing over LABELS (not over resolver objects) keeps the
#: heavy module imports out of collection; `_site_spec` resolves one lazily.
_SITE_LABELS = (
    "why.w4_enrichment_enabled",
    "monitoring._healthz_required",
    "sdk._ep_require_calibration_default",
    "sdk._index_no_network_enabled",
    "hosted_api._volunteer_slo_enforced",
    "hosted_api._linking_available",
    "hosted_api._telemetry_strict",
    "hosted_api._signup_email_confirm",
    "frontmatter_validator.validation_enabled",
    "model_adapters._should_send_json_mode",
    "extractor_v2._classify_later_enabled",
    "embeddings._embedder_warmup_enabled",
    "backup_config.env_flag_false_shape",
    "retrieval.ask_env_bool",
    "rerank.rerank_enabled",
    "projection._embedded_aof_enabled",
    "cimd.cimd_enabled",
    "cimd.same_origin_redirects_required",
    "embedded_lifecycle._fast_atexit_enabled",
    "tests._embedded._carve_out_opted_in",
)


def _site_spec(label: str):
    """(env-name, new, old, declared-newly-True) for one migrated site.

    The modules are imported HERE so the AST guards above never load them.
    """
    from tests._embedded import _carve_out_opted_in
    from tortoise import (
        cimd,
        extractor_v2,
        frontmatter_validator,
        hosted_api,
        model_adapters,
        monitoring,
        projection,
        rerank,
        retrieval,
        sdk,
        why,
    )
    from tortoise.embedded_lifecycle import _fast_atexit_enabled

    if label == "why.w4_enrichment_enabled":
        return (why.W4_FLAG_ENV, why.w4_enrichment_enabled,
                lambda: _old_truthy(why.W4_FLAG_ENV), _decl_none)
    if label == "monitoring._healthz_required":
        return ("TORTOISE_HEALTHZ_REQUIRED", monitoring._healthz_required,
                lambda: _old_truthy("TORTOISE_HEALTHZ_REQUIRED"), _decl_none)
    if label == "sdk._ep_require_calibration_default":
        return ("TORTOISE_EP_REQUIRE_CALIBRATION", sdk._ep_require_calibration_default,
                lambda: _old_truthy("TORTOISE_EP_REQUIRE_CALIBRATION", "1"), _decl_none)
    if label == "sdk._index_no_network_enabled":
        return ("TORTOISE_INDEX_NO_NETWORK", sdk._index_no_network_enabled,
                lambda: os.environ.get("TORTOISE_INDEX_NO_NETWORK", "").strip().lower()
                in ("1", "true", "yes"), _decl_only_on)
    if label == "hosted_api._volunteer_slo_enforced":
        return ("TORTOISE_VOLUNTEER_ENFORCE_SLO", hosted_api._volunteer_slo_enforced,
                lambda: _old_truthy("TORTOISE_VOLUNTEER_ENFORCE_SLO"), _decl_none)
    if label == "hosted_api._linking_available":
        return ("TORTOISE_MANUAL_LINKING_ENABLED", hosted_api._linking_available,
                lambda: os.environ.get("TORTOISE_MANUAL_LINKING_ENABLED", "") == "1",
                _decl_strict1)
    if label == "hosted_api._telemetry_strict":
        return ("TORTOISE_TELEMETRY_STRICT", hosted_api._telemetry_strict,
                lambda: os.environ.get("TORTOISE_TELEMETRY_STRICT") == "1",
                _decl_strict1)
    if label == "hosted_api._signup_email_confirm":
        return ("TORTOISE_SIGNUP_EMAIL_CONFIRM", hosted_api._signup_email_confirm,
                lambda: os.environ.get("TORTOISE_SIGNUP_EMAIL_CONFIRM", "true")
                .strip().lower() not in ("false", "0", "no", "off"), _decl_none)
    if label == "frontmatter_validator.validation_enabled":
        return ("TORTOISE_VALIDATE_FRONTMATTER", frontmatter_validator.validation_enabled,
                lambda: os.environ.get("TORTOISE_VALIDATE_FRONTMATTER", "")
                .strip().lower() == "1", _decl_narrow1)
    if label == "model_adapters._should_send_json_mode":
        return ("TORTOISE_JSON_MODE",
                lambda: model_adapters._should_send_json_mode("", "please return json"),
                lambda: os.environ.get("TORTOISE_JSON_MODE", "1") == "1",
                _decl_strict1)
    if label == "extractor_v2._classify_later_enabled":
        return ("TORTOISE_CLASSIFY_LATER", extractor_v2._classify_later_enabled,
                lambda: _old_truthy("TORTOISE_CLASSIFY_LATER"), _decl_none)
    if label == "embeddings._embedder_warmup_enabled":
        from tortoise.embeddings import _embedder_warmup_enabled
        return ("TORTOISE_EMBEDDER_WARMUP", _embedder_warmup_enabled,
                lambda: os.environ.get("TORTOISE_EMBEDDER_WARMUP", "1")
                .strip().lower() not in ("0", "false", "no", "off"), _decl_none)
    if label == "backup_config.env_flag_false_shape":
        # `backup_config._env_bool` was deleted in favour of `env_flag(name, False)`;
        # this pins that the SHAPE reproduces the deleted helper cell-for-cell.
        return ("TORTOISE_T_BACKUP_BOOL", lambda: env_flag("TORTOISE_T_BACKUP_BOOL", False),
                lambda: _old_env_bool("TORTOISE_T_BACKUP_BOOL", False), _decl_none)
    if label == "retrieval.ask_env_bool":
        return ("TORTOISE_T_ASK_BOOL", lambda: retrieval.ask_env_bool("TORTOISE_T_ASK_BOOL", False),
                lambda: _old_tristate("TORTOISE_T_ASK_BOOL", False), _decl_none)
    if label == "rerank.rerank_enabled":
        return ("TORTOISE_ASK_RERANK", rerank.rerank_enabled,
                lambda: _old_truthy("TORTOISE_ASK_RERANK"), _decl_none)
    if label == "projection._embedded_aof_enabled":
        return ("TORTOISE_EMBEDDED_AOF", projection._embedded_aof_enabled,
                lambda: os.environ.get("TORTOISE_EMBEDDED_AOF", "").strip().lower()
                in ("1", "true", "yes"), _decl_only_on)
    if label == "cimd.cimd_enabled":
        return ("TORTOISE_OAUTH_CIMD", cimd.cimd_enabled,
                lambda: _old_cimd_flag("TORTOISE_OAUTH_CIMD"), _decl_blank)
    if label == "cimd.same_origin_redirects_required":
        return ("TORTOISE_OAUTH_CIMD_SAME_ORIGIN", cimd.same_origin_redirects_required,
                lambda: _old_cimd_flag("TORTOISE_OAUTH_CIMD_SAME_ORIGIN"),
                _decl_blank)
    if label == "embedded_lifecycle._fast_atexit_enabled":
        return ("TORTOISE_FAST_ATEXIT", _fast_atexit_enabled,
                lambda: os.environ.get("TORTOISE_FAST_ATEXIT") == "1", _decl_strict1)
    if label == "tests._embedded._carve_out_opted_in":
        return ("TORTOISE_TEST_CARVE_OUT", _carve_out_opted_in,
                lambda: os.environ.get("TORTOISE_TEST_CARVE_OUT") == "1", _decl_strict1)
    raise AssertionError(f"unregistered migrated-site label: {label!r}")


@pytest.mark.parametrize("label", _SITE_LABELS)
def test_migration_only_widens_at_the_declared_cells(monkeypatch, label):
    """No migrated site may narrow; it may only widen at its declared cell.

    This is the mechanism the plan's behaviour-change table rests on: a regression is a
    `was True, now False` on some input, and an undeclared widening is a `now True` on an
    input the table does not claim.
    """
    env_name, new, old, declared = _site_spec(label)
    for raw in _MATRIX:
        _set(monkeypatch, env_name, raw)
        was, now = bool(old()), bool(new())
        if was:
            assert now, f"{label} REGRESSED at {raw!r}: was True, now False"
        elif now:
            assert declared(raw), (
                f"{label} newly True at undeclared input {raw!r} — either the migration "
                "changed behaviour unexpectedly, or the site's `declared` predicate is stale"
            )


def test_embeddings_warmup_oracle_agrees_with_the_old_skip_predicate(monkeypatch):
    """`_embedder_warmup_enabled` keeps `start_warm_up`'s exact skip truth table.

    The skip predicate is the NEGATION of this resolver, so the oracle is inverted: the
    two must agree on every input (no declared widening at all).
    """
    from tortoise.embeddings import _embedder_warmup_enabled

    for raw in _MATRIX:
        _set(monkeypatch, "TORTOISE_EMBEDDER_WARMUP", raw)
        old_skips = os.environ.get("TORTOISE_EMBEDDER_WARMUP", "1").strip().lower() \
            in ("0", "false", "no", "off")
        assert (not _embedder_warmup_enabled()) == old_skips, raw


# ── 3. The reaper: a pinned mirror + the purity it exists to protect ────────


@pytest.mark.parametrize("raw", _MATRIX)
def test_reaper_mirror_matches_the_contract(raw):
    """The reaper cannot import the leaf (see its module docstring), so it mirrors
    `TRUTHY`. Nothing asserted the two agreed before #4097 — now this does."""
    from tortoise.embedded_reaper import _env_truthy
    assert _env_truthy(raw) is is_truthy(raw)


def test_reaper_mirror_is_the_contract_vocabulary():
    """The mirror must BE the contract, not merely agree with it over `_MATRIX`.

    Matrix parity alone is blind to a mirror that gains a spelling the contract rejects
    (`_ENV_TRUTHY` + `"y"` resolves True while `is_truthy("y")` is False, and every other
    guard test still passes), so pin the vocabulary identity directly.
    """
    from tortoise.embedded_reaper import _ENV_TRUTHY
    assert _ENV_TRUTHY == TRUTHY, (
        "the reaper's mirror drifted from the declared truthy vocabulary — it must be the "
        f"same set ({sorted(TRUTHY)}), not {sorted(_ENV_TRUTHY)} (#4097)"
    )


def test_reaper_standalone_import_stays_dependency_free():
    """The reaper's own promise: runnable with the heavy chain ABSENT.

    `tortoise/embedded_reaper.py` has no intra-package module-level imports by design, so
    a module-level `from tortoise.env_truthy import ...` would make it import
    `tortoise/__init__.py` -> redislite. That is why the reaper mirrors the vocabulary
    instead of delegating.

    `"tortoise"` is in the blocklist (review catch): this test module imports
    `tortoise.env_truthy` at the top, so `tortoise.env_truthy` is already in
    `sys.modules` — without blocking the whole package, a module-level delegate in the
    reaper would never trigger a fresh import and the probe would pass vacuously. The
    probe also CALLS the resolver with the guard still active, so a lazy in-function
    delegate (which loads fine and only fails at call time) reds too — verified against
    both shapes.
    """
    blocked = {"tortoise", "redislite", "falkordb", "redis", "httpx", "anyio",
               "torch", "numpy"}
    real = builtins.__import__

    def _guard(name, *args, **kwargs):
        if name.split(".")[0] in blocked:
            raise ImportError(f"blocked for the reaper purity probe: {name}")
        return real(name, *args, **kwargs)

    sys.modules.pop("tortoise.env_truthy", None)
    globals_ = None
    builtins.__import__ = _guard
    try:
        globals_ = runpy.run_path(str(REPO_ROOT / "tortoise" / "embedded_reaper.py"),
                                 run_name="reaper_purity_probe")
        assert globals_["_env_truthy"]("1") is True      # module-load AND call-time purity
        assert globals_["_env_truthy"]("0") is False
    finally:
        builtins.__import__ = real


def test_leaf_imports_only_the_stdlib():
    """The leaf must not be able to drag anything in — that is what makes it a leaf."""
    tree = ast.parse((REPO_ROOT / "tortoise" / "env_truthy.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in sys.stdlib_module_names, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module and node.module.split(".")[0] in sys.stdlib_module_names, \
                node.module


def test_team_sweep_gate_is_narrow_by_design(monkeypatch):
    """The ONE deliberate departure from the contract this file declares.

    `tests/_embedded.py::_team_sweep_allowed` keeps `== "1"` because it is the sole
    authorization for an irreversible journal-blind delete of the real-tenant
    `org_*`/`team_*` namespace (its `uri` parameter is dead — the #1884 URI inference was
    retracted — so no containment check compensates). See the OVERRIDES line on #4097
    and in the function's body. Widening a destructive opt-in surface is not a
    vocabulary-coherence win, so this exception is PINNED here rather than hidden.
    """
    from tests._embedded import _team_sweep_allowed

    for raw in ("1",):
        _set(monkeypatch, "TORTOISE_TEST_SWEEP_TEAM_STRAYS", raw)
        assert _team_sweep_allowed("x") is True, raw
    for raw in ("true", "TRUE", "yes", "on", "ON", " 1 ", "", "garbage", None):
        _set(monkeypatch, "TORTOISE_TEST_SWEEP_TEAM_STRAYS", raw)
        assert _team_sweep_allowed("x") is False, raw


# ── 4. The non-divergence guard ────────────────────────────────────────────


_SCAN_SURFACE = (REPO_ROOT / "tortoise", REPO_ROOT / "tests" / "_embedded.py")

#: Files permitted to declare a truthy/falsy vocabulary literal -> (expected COUNT,
#: reason). COUNT-pinned (not line-pinned): line drift must not break the guard, but a
#: SECOND literal in the same file must.
_LEDGER_LITERAL_OWNERS: dict[str, tuple[int, str]] = {
    "tortoise/env_truthy.py": (2, "the declaration (TRUTHY + FALSY)"),
    "tortoise/embedded_reaper.py": (
        1, "pinned mirror — the reaper must stay standalone-importable"),
    "tortoise/backup_sweep.py": (
        1, "deferred to #4128: `=on` would suppress the ENUM_DELTA incident guard, so its "
           "widening needs its own decision"),
}

#: Narrow `== "1"` reads that REMAIN after #4097, with their site COUNT as an UPPER
#: BOUND. Widening the #4128 ones is a fail-open loosening (truthy relaxes a guard,
#: grants trust, or silences a protective mechanism), so each needs its own decision.
#: THE GOVERNING RULE: the ledger shrinks by DEFAULT — adding an entry requires its own
#: recorded decision (`OVERRIDES:`) and is itself a change to this contract. A new
#: `(module, name)` pair reds; an EXTRA read of a listed pair reds (the count); a pair
#: with ZERO sites left reds as closed and must be deleted, and deleting it is part of
#: closing #4128.
#:
#: THE DELIBERATE EXCEPTIONS — the ledger has GROWN twice, both under a recorded
#: `OVERRIDES` (not by accident): the tenant-namespace opt-in pinned above
#: (`TORTOISE_TEST_SWEEP_TEAM_STRAYS`; #1686/#1884) and `TORTOISE_TEST_SWEEP_LEGACY`
#: (#3634 Task 3). Each read is the SOLE authorization for an irreversible
#: journal-blind DETACH DELETE + GRAPH.DELETE of a cohort the journal cannot
#: attribute — exactly the fail-open surface the ledger exists to freeze — so
#: narrowing it is the safe policy. The #3634 entry is recorded on issue #3634 and in
#: the epic CI-Fix Changelog, and pinned by
#: tests/test_wipe_server.py::test_legacy_sweep_gate_is_narrow_by_design. A future
#: entry needs the same three: a recorded decision, an `OVERRIDES:` reason, and its
#: changelog row.
_KNOWN_NARROW_READS: dict[tuple[str, str], tuple[int, str]] = {
    ("tests/_embedded.py", "TORTOISE_TEST_SWEEP_TEAM_STRAYS"):
        (1, "OVERRIDES — sole authorization for an irreversible tenant-namespace delete"),
    ("tests/_embedded.py", "TORTOISE_TEST_SWEEP_LEGACY"):
        (1, "OVERRIDES (#3634) — sole authorization for an irreversible journal-blind "
            "residue delete"),
    ("tortoise/sdk.py", "TORTOISE_ALLOW_PRODUCTION"): (1, "#4128 — grants production access"),
    ("tortoise/mcp_server.py", "TORTOISE_ALLOW_EMBEDDED"): (1, "#4128 — grants embedded mode"),
    ("tortoise/projection/__init__.py", "TORTOISE_ALLOW_NONSTANDARD_PATH"):
        (2, "#4128 — relaxes path containment"),
    ("tortoise/projection/__init__.py", "TORTOISE_TEST_ALLOW_REMOTE"):
        (1, "#4128 — relaxes the remote-URI guard"),
    ("tortoise/projection/__init__.py", "TORTOISE_TEST_MODE"):
        (3, "#4128 — relaxes path/remote guards"),
    ("tortoise/pack_state.py", "TORTOISE_TEST_MODE"): (2, "#4128 — relaxes path/remote guards"),
    ("tortoise/hosted_api.py", "TORTOISE_TRUST_FLY_CLIENT_IP"):
        (2, "#4128 — trusts a caller-supplied header"),
    ("tortoise/hosted_api.py", "TORTOISE_TRUST_X_FORWARDED_PROTO"):
        (1, "#4128 — trusts a caller-supplied header"),
    ("tortoise/hosted_api.py", "RATE_LIMIT_DISABLED"): (8, "#4128 — disables rate limiting"),
    ("tortoise/mcp_auth.py", "RATE_LIMIT_DISABLED"): (1, "#4128 — disables rate limiting"),
    ("tortoise/abuse.py", "TORTOISE_ABUSE_DISABLED"): (1, "#4128 — disables abuse detection"),
    ("tortoise/hosted_api.py", "BACKUP_WATCHER_DISABLED"): (1, "#4128 — disables backup watching"),
    ("tortoise/pack_state.py", "PACK_STATE_DISABLE_SELF_HEAL"): (1, "#4128 — disables self-heal"),
    ("tortoise/sdk.py", "TORTOISE_SESSION_LLM_MOCK"): (2, "#4128 — swaps the LLM for a mock"),
    ("tortoise/hosted_api.py", "TORTOISE_SESSION_LLM_MOCK"):
        (1, "#4128 — swaps the LLM for a mock"),
    ("tortoise/__main__.py", "TORTOISE_SESSION_LLM_MOCK"):
        (1, "#4128 — swaps the LLM for a mock"),
}

def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level names bound to a string constant: `NAME = "X"` and `NAME: str = "X"`."""
    out: dict[str, str] = {}
    for value, targets in _module_const_assigns(tree):
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, value.value)
    return out


def _module_const_assigns(tree: ast.Module):
    """Yield `(value, targets)` for the assignment NODES that may define a constant.

    Plain assignments first, then ANNOTATED ones (the house idiom —
    `TRUTHY: frozenset[str] = ...`), which the scalar/collection constant maps did not
    resolve at all until review cycle 5. Both are walked over the whole file, so the
    order matters: the consumers use first-wins `setdefault`, and yielding the two forms
    interleaved let an EARLIER annotated binding (e.g. a function-local `x: T = ...`)
    shadow a LATER module-level `x = ...`, silently losing a narrow anchor. Yielding
    every plain assignment first keeps the pre-cycle-5 precedence for that form and makes
    the annotated form a pure fallback.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            yield node.value, node.targets
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            yield node.value, [node.target]


def _unwrap_chain(node: ast.expr) -> ast.expr:
    """`os.environ.get(X, "").strip().lower()` -> the `os.environ.get(...)` call.

    Also unwraps a WALRUS operand, so `if (_r := os.environ.get(X)) == "1"` resolves:
    the compare's own operand is the `NamedExpr`, not the `Name` the assignment binds.
    """
    while True:
        if isinstance(node, ast.NamedExpr):
            node = node.value
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr not in ("get", "getenv"):
            node = node.func.value
            continue
        break
    return node


def _env_var_name(node: ast.expr, constants: dict[str, str],
                  aliases: dict[str, str],
                  environ_names: frozenset[str] = frozenset()) -> str | None:
    """The env var this expression reads, or None.

    Resolves: `os.environ.get(...)` / `os.getenv(...)` (including a bare `environ`
    after `from os import environ`, aliased or not), `os.environ[...]`, a module-level
    STRING constant used as the name, and one level of local indirection
    (`_r = os.environ.get("X")` / `_r: str = ...` / `if (_r := ...)` -> the compare
    on `_r`).
    """
    node = _unwrap_chain(node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in ("get", "getenv"):
        base = node.func.value
        is_os = (isinstance(base, ast.Name)
                 and (base.id in ("os", "_os", "environ") or base.id in environ_names)) \
            or (isinstance(base, ast.Attribute) and base.attr == "environ")
        if is_os and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
            if isinstance(arg, ast.Name):
                return constants.get(arg.id) or aliases.get(arg.id, "<dynamic>")
            return "<dynamic>"
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
            and node.value.attr == "environ" \
            and isinstance(node.slice, ast.Constant) \
            and isinstance(node.slice.value, str):
        return node.slice.value
    if isinstance(node, ast.Name) and node.id in aliases:
        return aliases[node.id]
    return None


def _os_environ_aliases(tree: ast.Module) -> frozenset[str]:
    """Names bound to `os.environ` by `from os import environ [as X]`."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "environ":
                    out.add(alias.asname or alias.name)
    return frozenset(out)


def _env_aliases(tree: ast.Module, constants: dict[str, str],
                 environ_names: frozenset[str] = frozenset()) -> dict[str, str]:
    """Local names bound to an env read.

    Covers `_r = os.environ.get("X")`, `_r: str = os.environ.get("X")` (AnnAssign)
    and `if (_r := os.environ.get("X")) == ...` (walrus). A TWO-step chain
    (`_a = <env read>; _r = _a`) is still missed; the map is also module-global and
    so OVER-approximates (a name ever bound to an env read is treated as that read
    for the whole module) — both residuals are declared in the module docstring.
    """
    out: dict[str, str] = {}

    def _bind(target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Name):
            name = _env_var_name(value, constants, {}, environ_names)
            if name and name != "<dynamic>":
                out.setdefault(target.id, name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                _bind(target, node.value)
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None:
                _bind(node.target, node.value)
        elif isinstance(node, ast.NamedExpr):
            _bind(node.target, node.value)
    return out


def _is_vocabulary(values: set[str]) -> bool:
    """A DECLARED vocabulary literal: anchored on "1" or "0" AND carrying at least
    one further truthy/falsy SPELLING.

    The anchor plus the extra spelling keeps single-element collections such as
    `dict.get("page", ["1"])[0]` (`tortoise/indexer/github_indexer.py`), stopword sets
    ({"on","yes"}) and bare binary-digit collections (`{"0","1"}`, or a
    `{"1": "one", "0": "zero"}` label map) out of the scan. A near-vocabulary with a
    stray extra member (`{"1","true","yes","t"}`) IS flagged — that is a new
    divergence, not a different thing.
    """
    if "1" not in values and "0" not in values:
        return False
    return bool((values & (TRUTHY | FALSY)) - {"0", "1"})


def _is_binary_anchor(node: ast.expr, constants: dict[str, str]) -> bool:
    """A `"1"`/`"0"` anchor: the literal itself, or a module-level constant bound to it."""
    if isinstance(node, ast.Constant):
        return node.value in ("1", "0")
    return isinstance(node, ast.Name) and constants.get(node.id) in ("1", "0")


def _collection_values(node: ast.expr) -> set[str] | None:
    """The string members of a literal Tuple/Set/List, else None."""
    if not isinstance(node, (ast.Tuple, ast.Set, ast.List)):
        return None
    return {e.value for e in node.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def _module_collection_constants(tree: ast.Module) -> dict[str, set[str]]:
    """Module-level names bound to a string-constant Tuple/Set/List literal.

    Resolved so a narrow read written `_ONE = ("1",); ... in _ONE` is visible, not just
    its inline-literal form (code review caught the same blind spot for the scalar form).
    """
    out: dict[str, set[str]] = {}
    for value, targets in _module_const_assigns(tree):
        values = _collection_values(value)
        if values is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out.setdefault(target.id, values)
    return out


def _scan_source(rel: str, source: str) -> tuple[list[tuple[str, int]],
                                                list[tuple[str, str, int]]]:
    """Scan ONE module's source. Split out from `_scan()` so the alias/env-alias
    machinery has a synthetic-source self-test (it is exercised by nothing in-tree)."""
    literals: list[tuple[str, int]] = []
    narrow: list[tuple[str, str, int]] = []
    tree = ast.parse(source)
    constants = _module_string_constants(tree)
    collections = _module_collection_constants(tree)
    environ_names = _os_environ_aliases(tree)
    aliases = _env_aliases(tree, constants, environ_names)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
            values = {e.value for e in node.elts
                      if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if _is_vocabulary(values):
                literals.append((rel, node.lineno))
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if _is_vocabulary(keys):
                literals.append((rel, node.lineno))
        if isinstance(node, ast.Compare):
            sides = (node.left, *node.comparators)
            names = {n for n in (_env_var_name(s, constants, aliases, environ_names)
                                 for s in sides) if n}
            if not names:
                continue
            hit = False
            for op, comparator in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, (ast.Eq, ast.NotEq)) and any(
                        _is_binary_anchor(side, constants)
                        for side in (node.left, comparator)):
                    # a "1"/"0" anchor on EITHER side (reversed operands count), whether
                    # written literally or as a module-level constant bound to it
                    hit = True
                if isinstance(op, (ast.In, ast.NotIn)):
                    values = _collection_values(comparator)
                    if values is None and isinstance(comparator, ast.Name):
                        values = collections.get(comparator.id)
                    # A NARROW membership test only: `("1",)` counts; the wide
                    # vocabulary does not (the literal clause catches that).
                    if values is not None and ("1" in values or "0" in values) \
                            and not _is_vocabulary(values):
                        hit = True
            if hit:
                for name in names:
                    narrow.append((rel, name, node.lineno))
    return literals, narrow


def _scan() -> tuple[list[tuple[str, int]], list[tuple[str, str, int]]]:
    """(vocabulary literals, narrow reads) over the declared surface."""
    literals: list[tuple[str, int]] = []
    narrow: list[tuple[str, str, int]] = []
    for root in _SCAN_SURFACE:
        files = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for path in files:
            rel = path.relative_to(REPO_ROOT).as_posix()
            file_literals, file_narrow = _scan_source(rel, path.read_text())
            literals.extend(file_literals)
            narrow.extend(file_narrow)
    return literals, narrow


def _scanned_rel_paths() -> list[str]:
    """Every path in the declared scan surface, repo-relative (for the ratchet)."""
    out: list[str] = []
    for root in _SCAN_SURFACE:
        if root.is_dir():
            out.extend(p.relative_to(REPO_ROOT).as_posix() for p in sorted(root.rglob("*.py")))
        else:
            out.append(root.relative_to(REPO_ROOT).as_posix())
    return out


def test_no_adhoc_vocabulary_literal_outside_the_contract():
    """A new ad-hoc copy is exactly how the divergence grew to five conventions."""
    literals, _ = _scan()
    counts = Counter(rel for rel, _line in literals)
    expected = {rel: n for rel, (n, _why) in _LEDGER_LITERAL_OWNERS.items()}
    assert dict(counts) == expected, (
        "truthy/falsy vocabulary ownership drifted (#4097): "
        f"found {dict(counts)}, expected {expected} — import TRUTHY/FALSY from "
        "tortoise.env_truthy instead of declaring another literal, or, for a "
        "deliberate/kept literal owner, add a `_LEDGER_LITERAL_OWNERS` entry above "
        "with its reason"
    )


def test_narrow_env_reads_are_the_frozen_ledger():
    _, narrow = _scan()
    seen = Counter((rel, name) for rel, name, _line in narrow)
    grown = {key: (n, _KNOWN_NARROW_READS.get(key, (0, ""))[0])
             for key, n in seen.items() if n > _KNOWN_NARROW_READS.get(key, (0, ""))[0]}
    assert not grown, (
        "new narrow env read site(s) outside the declared contract (#4097) — use "
        "tortoise.env_truthy, or raise the ledger entry with a reason: " + repr(grown)
        + "   (widening a listed name is #4128's decision, not a drive-by edit)"
    )
    closed = sorted(key for key in _KNOWN_NARROW_READS if key not in seen)
    assert not closed, (
        "closed _KNOWN_NARROW_READS entr(ies) — the read is gone, delete the entry so the "
        "ledger keeps shrinking by default (#4128): " + ", ".join(f"{r}::{n}" for r, n in closed)
    )


def test_guard_is_registered_in_every_surface_that_owns_a_scanned_module():
    """#4097: the guard's CI reachability is DERIVED here, not remembered by hand.

    `tools/ci_selection.select()` replaces the `core` fallback with a matched named
    surface, so a surface that owns a module under the declared scan root but does not list
    this file makes an isolated change to that module run NO part of the guard — the
    #1349/#3332/#3616 silent-drop class (code review found exactly this for `eval`).
    Recomputing the owning set from the selector is what keeps six hand-written manifest
    entries from being a mirror that rots the next time a surface is added or split.
    """
    yaml = pytest.importorskip("yaml")
    from tools import ci_selection as cs

    manifest = yaml.safe_load((REPO_ROOT / "config" / "ci-surfaces.yml").read_text())
    owners = {surface for surface, patterns in cs.SOURCE_PATTERNS.items()
              if any(rel.startswith(p) for rel in _scanned_rel_paths() for p in patterns)} \
        | {"core"}
    missing = sorted(s for s in owners
                     if "test_env_truthy.py" not in manifest["surfaces"].get(s, []))
    assert not missing, (
        f"the env-truthiness guard scans all of tortoise/** (and tests/_embedded.py) but is "
        f"not registered in surface(s) that own a scanned path: {missing} — add "
        "`- test_env_truthy.py` to each (config/ci-surfaces.yml)"
    )


_SYNTHETIC_SHAPES = [
    ('_r = os.environ.get("TORTOISE_SYNTH")\n_r == "1"\n', "plain assignment"),
    ('_r: str = os.environ.get("TORTOISE_SYNTH")\n_r == "1"\n', "annotated assignment"),
    ('if (_r := os.environ.get("TORTOISE_SYNTH")) == "1":\n    pass\n', "inline walrus"),
    ('if (os.environ.get("TORTOISE_SYNTH")) == "1":\n    pass\n', "direct call"),
    ('if os.environ["TORTOISE_SYNTH"] == "1":\n    pass\n', "subscript"),
    ('if os.getenv("TORTOISE_SYNTH") == "1":\n    pass\n', "os.getenv"),
    ('if "1" == os.environ.get("TORTOISE_SYNTH"):\n    pass\n', "reversed operands"),
    ('from os import environ as env\nif env.get("TORTOISE_SYNTH") == "1":\n    pass\n',
     "aliased environ import"),
    ('NAME = "TORTOISE_SYNTH"\nif os.environ.get(NAME) == "1":\n    pass\n',
     "module constant name"),
    ('if os.environ.get("TORTOISE_SYNTH", "").strip().lower() == "1":\n    pass\n',
     "normalised chain"),
    ('_ONE = "1"\nif os.environ.get("TORTOISE_SYNTH") == _ONE:\n    pass\n',
     "module constant anchor"),
    ('_ONES = ("1",)\nif os.environ.get("TORTOISE_SYNTH") in _ONES:\n    pass\n',
     "module constant membership anchor"),
    ('_ONE: str = "1"\nif os.environ.get("TORTOISE_SYNTH") == _ONE:\n    pass\n',
     "annotated module constant anchor"),
    ('_ONES: tuple = ("1",)\nif os.environ.get("TORTOISE_SYNTH") in _ONES:\n    pass\n',
     "annotated module constant membership anchor"),
    ('NAME: str = "TORTOISE_SYNTH"\nif os.environ.get(NAME) == "1":\n    pass\n',
     "annotated module constant name"),
    ('_ONE: str = "true"\n_ONE = "1"\n'
     'if os.environ.get("TORTOISE_SYNTH") == _ONE:\n    pass\n',
     "plain anchor wins over an earlier annotated shadow"),
    ('_ONES: tuple = ("yes", "no")\n_ONES = ("1",)\n'
     'if os.environ.get("TORTOISE_SYNTH") in _ONES:\n    pass\n',
     "plain membership anchor wins over an earlier annotated shadow"),
]


@pytest.mark.parametrize("source,shape", _SYNTHETIC_SHAPES,
                         ids=[s[1] for s in _SYNTHETIC_SHAPES])
def test_scanner_resolves_every_declared_shape(source, shape):
    """The alias/env-alias machinery is exercised by NOTHING in-tree (`tortoise/` has no
    `from os import environ` and no aliased narrow read), so without this self-test a
    regression that deletes it passes all of the above. Code review found the inline
    walrus was claimed-covered but missed; this pins every shape the docstring lists."""
    _, narrow = _scan_source("synthetic.py", source)
    assert {name for _rel, name, _line in narrow} == {"TORTOISE_SYNTH"}, \
        f"{shape} was not resolved by the scanner: {narrow}"


def test_scanner_declares_the_cross_scope_shadowing_residual():
    """The declared residual: the constant maps are file-global and first-wins (plain
    assignments yielded before annotated ones), so a same-named LOCAL plain binding
    defeats a module-level ANNOTATED anchor.

    Pinned rather than hidden. The cycle-6 fix — a later module-level plain binding must
    beat an earlier LOCAL annotated one — is the SAME configuration as this miss, so no
    global first-wins map resolves both; scope-aware resolution would be the real fix.
    If this starts resolving, either delete this pin and the matching boundary bullet, or
    say so in the boundary — do not leave the trade-off undeclared.
    """
    source = ('def f():\n    _ONE = "true"\n_ONE: str = "1"\n'
              'if os.environ.get("TORTOISE_SYNTH") == _ONE:\n    pass\n')
    _, narrow = _scan_source("synthetic.py", source)
    assert narrow == [], (
        "the cross-scope shadowing residual is resolved now — update the declared boundary "
        f"in the module docstring and this pin (narrow={narrow})"
    )


def test_scanner_flags_a_synthetic_vocabulary_literal():
    literals, _ = _scan_source("synthetic.py", '_V = {"1", "true", "yes", "t"}\n')
    assert literals, "an extra-token vocabulary literal must be flagged"
    literals, _ = _scan_source("synthetic.py", '_V = {"1": "on", "true": "off"}\n')
    assert literals, "a Dict-keyed vocabulary must be flagged"
    literals, _ = _scan_source("synthetic.py", '_V = {"0", "1"}\n_L = {"1": "one", "0": "zero"}\n')
    assert not literals, "binary digits / a non-env label map must NOT be flagged"

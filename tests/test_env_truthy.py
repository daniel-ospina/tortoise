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
It does NOT police: V5 presence reads; split comparisons (`raw == "1" or raw == "true"`);
a partial vocabulary with no `"1"`/`"0"` anchor (e.g. `{"true","yes","on"}`);
`getattr(os.environ, ...)` reads; or `tests/` beyond `_embedded.py`, `tools/`,
`graph-scripts/`, `apps/` — which already carry their own literals
(`tests/test_product_rerank.py`, `tests/test_monitoring.py`, `tests/test_reaper.py`,
`tests/test_eval_ingest_cache.py`, `tests/test_extractor_v2.py`,
`tests/longmem_eval/test_assembly_arm.py`, `tests/eval/why_suite/test_why_suite_ab.py`,
`tests/test_email_signup.py`, `apps/graph-viz/server/connection.py`). The claim this
file supports is "no new divergence **inside the declared surface**", not "anywhere in
the repo". A JS/TS scan of `website/functions/`, `supabase/functions/`, `client/` and
`menu-bar/` found no boolean env-truthiness parsing, so there is no cross-language
duplication to guard.

The scan resolves: module-level string constants as env names, `.strip().lower()`
chains, one level of `_alias = os.environ.get(NAME)` indirection, a constant on either
side of the comparison, `environ.get(...)` after `from os import environ`, and both
`Set`/`Tuple`/`List` and `Dict`-key vocabulary literals.

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
_MATRIX = [None, "", " ", "0", "1", " 1 ", "1 ", "0 ", "true", "TRUE", "True",
           "yes", "YES", "Yes", "on", "ON", "On", "false", "FALSE", "no", "NO",
           "off", "OFF", "garbage", "2"]

#: The declared truthy spellings beyond the exact string "1" (case variants).
_TRUTHY_SPELLINGS = frozenset({"true", "TRUE", "True", "yes", "YES", "Yes", "on", "ON", "On"})

#: Widening set for a `== "1"` read that does NOT strip/lower the value: the spellings
#: above PLUS the whitespace-padded "1" forms.
_WIDENED_STRICT1 = _TRUTHY_SPELLINGS | {" 1 ", "1 "}

#: Widening set for a `in ("1","true","yes")` (V3) read: only "on" was missing.
_WIDENED_ONLY_ON = frozenset({"on", "ON", "On"})


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
                lambda: _old_truthy(why.W4_FLAG_ENV), frozenset())
    if label == "monitoring._healthz_required":
        return ("TORTOISE_HEALTHZ_REQUIRED", monitoring._healthz_required,
                lambda: _old_truthy("TORTOISE_HEALTHZ_REQUIRED"), frozenset())
    if label == "sdk._ep_require_calibration_default":
        return ("TORTOISE_EP_REQUIRE_CALIBRATION", sdk._ep_require_calibration_default,
                lambda: _old_truthy("TORTOISE_EP_REQUIRE_CALIBRATION", "1"), frozenset())
    if label == "sdk._index_no_network_enabled":
        return ("TORTOISE_INDEX_NO_NETWORK", sdk._index_no_network_enabled,
                lambda: os.environ.get("TORTOISE_INDEX_NO_NETWORK", "").strip().lower()
                in ("1", "true", "yes"), _WIDENED_ONLY_ON)
    if label == "hosted_api._volunteer_slo_enforced":
        return ("TORTOISE_VOLUNTEER_ENFORCE_SLO", hosted_api._volunteer_slo_enforced,
                lambda: _old_truthy("TORTOISE_VOLUNTEER_ENFORCE_SLO"), frozenset())
    if label == "hosted_api._linking_available":
        return ("TORTOISE_MANUAL_LINKING_ENABLED", hosted_api._linking_available,
                lambda: os.environ.get("TORTOISE_MANUAL_LINKING_ENABLED", "") == "1",
                _WIDENED_STRICT1)
    if label == "hosted_api._telemetry_strict":
        return ("TORTOISE_TELEMETRY_STRICT", hosted_api._telemetry_strict,
                lambda: os.environ.get("TORTOISE_TELEMETRY_STRICT") == "1",
                _WIDENED_STRICT1)
    if label == "hosted_api._signup_email_confirm":
        return ("TORTOISE_SIGNUP_EMAIL_CONFIRM", hosted_api._signup_email_confirm,
                lambda: os.environ.get("TORTOISE_SIGNUP_EMAIL_CONFIRM", "true")
                .strip().lower() not in ("false", "0", "no", "off"), frozenset())
    if label == "frontmatter_validator.validation_enabled":
        return ("TORTOISE_VALIDATE_FRONTMATTER", frontmatter_validator.validation_enabled,
                lambda: os.environ.get("TORTOISE_VALIDATE_FRONTMATTER", "")
                .strip().lower() == "1", _TRUTHY_SPELLINGS)
    if label == "model_adapters._should_send_json_mode":
        return ("TORTOISE_JSON_MODE",
                lambda: model_adapters._should_send_json_mode("", "please return json"),
                lambda: os.environ.get("TORTOISE_JSON_MODE", "1") == "1",
                _WIDENED_STRICT1)
    if label == "extractor_v2._classify_later_enabled":
        return ("TORTOISE_CLASSIFY_LATER", extractor_v2._classify_later_enabled,
                lambda: _old_truthy("TORTOISE_CLASSIFY_LATER"), frozenset())
    if label == "embeddings._embedder_warmup_enabled":
        from tortoise.embeddings import _embedder_warmup_enabled
        return ("TORTOISE_EMBEDDER_WARMUP", _embedder_warmup_enabled,
                lambda: os.environ.get("TORTOISE_EMBEDDER_WARMUP", "1")
                .strip().lower() not in ("0", "false", "no", "off"), frozenset())
    if label == "backup_config.env_flag_false_shape":
        # `backup_config._env_bool` was deleted in favour of `env_flag(name, False)`;
        # this pins that the SHAPE reproduces the deleted helper cell-for-cell.
        return ("TORTOISE_T_BACKUP_BOOL", lambda: env_flag("TORTOISE_T_BACKUP_BOOL", False),
                lambda: _old_env_bool("TORTOISE_T_BACKUP_BOOL", False), frozenset())
    if label == "retrieval.ask_env_bool":
        return ("TORTOISE_T_ASK_BOOL", lambda: retrieval.ask_env_bool("TORTOISE_T_ASK_BOOL", False),
                lambda: _old_tristate("TORTOISE_T_ASK_BOOL", False), frozenset())
    if label == "rerank.rerank_enabled":
        return ("TORTOISE_ASK_RERANK", rerank.rerank_enabled,
                lambda: _old_truthy("TORTOISE_ASK_RERANK"), frozenset())
    if label == "projection._embedded_aof_enabled":
        return ("TORTOISE_EMBEDDED_AOF", projection._embedded_aof_enabled,
                lambda: os.environ.get("TORTOISE_EMBEDDED_AOF", "").strip().lower()
                in ("1", "true", "yes"), _WIDENED_ONLY_ON)
    if label == "cimd.cimd_enabled":
        return ("TORTOISE_OAUTH_CIMD", cimd.cimd_enabled,
                lambda: _old_cimd_flag("TORTOISE_OAUTH_CIMD"), frozenset({"", " "}))
    if label == "cimd.same_origin_redirects_required":
        return ("TORTOISE_OAUTH_CIMD_SAME_ORIGIN", cimd.same_origin_redirects_required,
                lambda: _old_cimd_flag("TORTOISE_OAUTH_CIMD_SAME_ORIGIN"),
                frozenset({"", " "}))
    if label == "embedded_lifecycle._fast_atexit_enabled":
        return ("TORTOISE_FAST_ATEXIT", _fast_atexit_enabled,
                lambda: os.environ.get("TORTOISE_FAST_ATEXIT") == "1", _WIDENED_STRICT1)
    if label == "tests._embedded._carve_out_opted_in":
        return ("TORTOISE_TEST_CARVE_OUT", _carve_out_opted_in,
                lambda: os.environ.get("TORTOISE_TEST_CARVE_OUT") == "1", _WIDENED_STRICT1)
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
            assert raw in declared, (
                f"{label} newly True at undeclared input {raw!r} — either the migration "
                "changed behaviour unexpectedly, or the site's `declared` set is stale"
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
#: grants trust, or silences a protective mechanism), so each needs its own decision; the
#: first entry is the deliberate OVERRIDES exception above. A new `(module, name)` pair
#: reds; an EXTRA read of a listed pair reds (the count); a pair with ZERO sites left reds
#: as closed and must be deleted — so the ledger can only shrink, and shrinking it is
#: part of closing #4128.
_KNOWN_NARROW_READS: dict[tuple[str, str], tuple[int, str]] = {
    ("tests/_embedded.py", "TORTOISE_TEST_SWEEP_TEAM_STRAYS"):
        (1, "OVERRIDES — sole authorization for an irreversible tenant-namespace delete"),
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

#: Total narrow-read LINES ceiling — a delete-then-re-add elsewhere cannot mask growth
#: that the per-key upper bounds miss.
_LEDGER_CEILING = 30


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, node.value.value)
    return out


def _unwrap_chain(node: ast.expr) -> ast.expr:
    """`os.environ.get(X, "").strip().lower()` -> the `os.environ.get(...)` call."""
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr not in ("get", "getenv"):
        node = node.func.value
    return node


def _env_var_name(node: ast.expr, constants: dict[str, str],
                  aliases: dict[str, str]) -> str | None:
    """The env var this expression reads, or None.

    Resolves: `os.environ.get(...)` / `os.getenv(...)` (including a bare `environ`
    after `from os import environ`), `os.environ[...]`, a module-level STRING
    constant used as the name, and one level of local indirection
    (`_r = os.environ.get("X")` -> the compare on `_r`).
    """
    node = _unwrap_chain(node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in ("get", "getenv"):
        base = node.func.value
        is_os = (isinstance(base, ast.Name) and base.id in ("os", "_os", "environ")) \
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


def _env_aliases(tree: ast.Module, constants: dict[str, str]) -> dict[str, str]:
    """Local names bound to an env read: `_r = os.environ.get("X")` -> `{"_r": "X"}`.

    One level is enough for the shape a refactor produces (`raw = os.environ.get(...)`
    then `raw == "1"`); the alias map is built before the compare walk, so a two-step
    chain is still missed and that residual is declared in the module docstring.
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            name = _env_var_name(node.value, constants, {})
            if name and name != "<dynamic>":
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out.setdefault(target.id, name)
    return out


def _is_vocabulary(values: set[str]) -> bool:
    """A DECLARED vocabulary literal: anchored on "1" or "0" and carrying >= 2
    truthy/falsy spellings.

    The anchor and the >= 2 rule keep single-element collections such as
    `dict.get("page", ["1"])[0]` (`tortoise/indexer/github_indexer.py`) and stopword
    sets ({"on","yes"}) out of the scan. A near-vocabulary with a stray extra member
    (`{"1","true","yes","t"}`) IS flagged — that is a new divergence, not a
    different thing.
    """
    if "1" not in values and "0" not in values:
        return False
    return len(values & (TRUTHY | FALSY)) >= 2


def _scan() -> tuple[list[tuple[str, int]], list[tuple[str, str, int]]]:
    """(vocabulary literals, narrow reads) over the declared surface."""
    literals: list[tuple[str, int]] = []
    narrow: list[tuple[str, str, int]] = []
    for root in _SCAN_SURFACE:
        files = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for path in files:
            rel = path.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text())
            constants = _module_string_constants(tree)
            aliases = _env_aliases(tree, constants)
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
                    names = {n for n in (_env_var_name(s, constants, aliases)
                                         for s in sides) if n}
                    if not names:
                        continue
                    hit = False
                    for op, comparator in zip(node.ops, node.comparators, strict=True):
                        if isinstance(op, (ast.Eq, ast.NotEq)):
                            # a "1"/"0" literal on EITHER side (reversed operands count)
                            for side in (node.left, comparator):
                                if isinstance(side, ast.Constant) \
                                        and side.value in ("1", "0"):
                                    hit = True
                        if isinstance(op, (ast.In, ast.NotIn)) \
                                and isinstance(comparator, (ast.Tuple, ast.Set, ast.List)):
                            values = {e.value for e in comparator.elts
                                      if isinstance(e, ast.Constant)
                                      and isinstance(e.value, str)}
                            # A NARROW membership test only: `("1",)` counts; the wide
                            # vocabulary does not (the literal clause catches that).
                            if ("1" in values or "0" in values) and not _is_vocabulary(values):
                                hit = True
                    if hit:
                        for name in names:
                            narrow.append((rel, name, node.lineno))
    return literals, narrow


def test_no_adhoc_vocabulary_literal_outside_the_contract():
    """A new ad-hoc copy is exactly how the divergence grew to five conventions."""
    literals, _ = _scan()
    counts = Counter(rel for rel, _line in literals)
    expected = {rel: n for rel, (n, _why) in _LEDGER_LITERAL_OWNERS.items()}
    assert dict(counts) == expected, (
        "truthy/falsy vocabulary ownership drifted (#4097): "
        f"found {dict(counts)}, expected {expected} — import TRUTHY/FALSY from "
        "tortoise.env_truthy instead of declaring another literal"
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
    assert sum(seen.values()) <= _LEDGER_CEILING, (
        f"total narrow-read lines grew to {sum(seen.values())} (ceiling {_LEDGER_CEILING})"
    )
    closed = sorted(key for key in _KNOWN_NARROW_READS if key not in seen)
    assert not closed, (
        "closed _KNOWN_NARROW_READS entr(ies) — the read is gone, delete the entry so the "
        "ledger can only shrink (#4128): " + ", ".join(f"{r}::{n}" for r, n in closed)
    )

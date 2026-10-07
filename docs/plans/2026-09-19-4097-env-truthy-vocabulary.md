<!-- research-path: docs/plans/2026-09-19-4097-env-truthy-vocabulary.md -->

# Env-truthiness: one declared contract Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Declare the env-truthiness contract once in a stdlib-only leaf module, route every call
site in `tortoise/` whose widening is a fix (not a security change) through it, and add a guard that
reds when the vocabularies diverge again.

**Team:** organisation-design-team
**Role:** implementer

**Architecture:** `tortoise/env_truthy.py` is a new stdlib-only leaf (`os` only, zero intra-package
imports — the `tortoise/security.py` / `tortoise/transport.py` precedent). API, after plan review
(**two functions fewer than the scoping comment proposed** — see "Review corrections" at the end):
`TRUTHY`, `FALSY`, `is_truthy(raw)`, `env_flag(name, default)`. Call sites delegate; every name with a
referent is preserved as an alias. The reaper keeps a **pinned mirror** (a delegate would make it
import `tortoise/__init__.py` → redislite, destroying a property its docstring promises and which the
new test suite measures). The remaining narrow reads whose widening is a fail-open loosening are
**not** touched here; they are frozen in an AST-checked ledger in the guard test and handed to #4128.

### Pattern Research

Skipped — the plan touches **zero third-party dependencies** (`os` only). The prior-research intake
(scoping `### Axis Research` / `### Integration Docs`) is consumed from the #4097 scoping comment.

### Integration Surface Map

**Skipped** — no integration boundaries: pure config/logic refactor plus tests.

**Tech Stack:** Python 3.12, pytest, ruff (`ruff check` only — `ruff format` is forbidden, the tree is
not format-clean on `main`). Interpreter: `/Users/danielospina/Documents/GitHub/tortoise/.venv/bin/python`
(the worktree has no `.venv`). `ruff check .` is **clean on `main`** (verified, ruff 0.16.4 = the
version CI pins at `.github/workflows/ci.yml:1199`).

---

### Task 1: The leaf module — the single declaration

**Intent:** Give the tree one place that declares what "truthy" means, importable by any module
(including a dependency-free one) without creating a cycle.
**Acceptance:** `tortoise/env_truthy.py` exists, imports only the stdlib, and exports
`TRUTHY`/`FALSY`/`is_truthy`/`env_flag` whose full input matrix is pinned by tests.
**Files:**
- Create: `tortoise/env_truthy.py`
- Test: `tests/test_env_truthy.py`

**Step 1: write `tortoise/env_truthy.py`**

```python
"""The single declared env-truthiness contract (#4097).

Before #4097 the tree carried five divergent conventions for reading a boolean
environment variable, and nothing asserted they agreed — so an operator's
documented ``true``/``TRUE`` silently did nothing wherever a narrow read happened
to exist:

  V1  truthy set {"1","true","yes","on"}     (the de-facto operational standard:
      .github/workflows/deploy-hosted.yml sets BACKUP_SWEEP_ENABLED=true, and
      .env.example documents BACKUP_LOCK_ENABLED as "operator sets TRUE")
  V2  narrow ``== "1"``                       (reads whose widening would relax a
      guard are frozen in tests/test_env_truthy.py::_KNOWN_NARROW_READS, #4128)
  V3  truthy-minus-``on`` {"1","true","yes"}  (``=on`` silently did nothing)
  V4  falsy list {"0","false","no","off"}     (blank meant OFF in one module, the
      default in another)
  V5  presence (``if os.environ.get(X)``)     (out of scope: for a key/URI/path,
      present-or-absent is genuinely the question)

Stdlib-only by design — no imports from ``tortoise`` — so any module may import
these helpers without creating an import cycle (the ``tortoise/security.py`` /
``tortoise/transport.py`` precedent). This does NOT make an import of this module
cheap for a *standalone* consumer: importing any ``tortoise.<sub>`` runs
``tortoise/__init__.py``, which imports redislite unconditionally.
``tortoise/embedded_reaper.py`` therefore mirrors ``TRUTHY`` instead of delegating,
and ``tests/test_env_truthy.py`` pins both the mirror and the reaper's standalone
import purity.

Two shapes, deliberately distinct:

  * ``is_truthy(raw)``      — a raw-VALUE predicate. Unset/blank/falsy/garbage ->
                              False. Pair it with an explicit default VALUE when a
                              knob is default-ON and a typo must read OFF:
                              ``is_truthy(os.environ.get("X", "1"))``.
  * ``env_flag(name, default)`` — a tristate RESOLVER. unset/blank/garbage ->
                              ``default`` (a typo never flips a knob); explicit
                              truthy -> True; explicit falsy -> False.
"""
from __future__ import annotations

import os

#: The one truthy vocabulary. Case-insensitive; surrounding whitespace ignored.
TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})

#: The one falsy vocabulary — the explicit OFF spellings, as opposed to "unset".
FALSY: frozenset[str] = frozenset({"0", "false", "no", "off"})


def _normalized(raw: object | None) -> str:
    """`None`/non-str -> the empty string; else strip + casefold."""
    return "" if raw is None else str(raw).strip().lower()


def is_truthy(raw: object | None) -> bool:
    """Is this *value* a truthy spelling? Unset/blank/falsy/garbage -> False."""
    return _normalized(raw) in TRUTHY


def env_flag(name: str, default: bool) -> bool:
    """Resolve env var `name` as a caller-defaulted flag.

    unset/blank/garbage -> `default`; explicit truthy -> True; explicit falsy ->
    False. A blank value is *unset*, not a statement — the contract
    ``retrieval.ask_env_bool`` implemented.
    """
    raw = _normalized(os.environ.get(name))
    if not raw:
        return default
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    return default
```

**Step 2: write `tests/test_env_truthy.py`'s contract half** — the docstring from Task 5 Step 1, and:

```python
def test_vocabulary_is_pinned():
    assert TRUTHY == frozenset({"1", "true", "yes", "on"})
    assert FALSY == frozenset({"0", "false", "no", "off"})
    assert not (TRUTHY & FALSY)


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "True", "yes", "YES", "on", "On", " 1 ", "yEs"])
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
        assert env_flag("TORTOISE_T_CONTRACT", True) is True
        assert env_flag("TORTOISE_T_CONTRACT", False) is False
    elif raw.strip().lower() in TRUTHY:
        assert env_flag("TORTOISE_T_CONTRACT", False) is True
    elif raw.strip().lower() in FALSY:
        assert env_flag("TORTOISE_T_CONTRACT", True) is False
    else:                                     # garbage -> the caller's default
        assert env_flag("TORTOISE_T_CONTRACT", True) is True
        assert env_flag("TORTOISE_T_CONTRACT", False) is False


def _set(monkeypatch, name: str, raw: str | None) -> None:
    """Set `name` to `raw`, or DELETE it when `raw is None` (absent)."""
    if raw is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, raw)
```

**Step 3: run** — `TORTOISE_TEST_CARVE_OUT=1 $PY -m pytest tests/test_env_truthy.py -q` → PASS.

---

### Task 2: Route the V1/V3/V4 call sites through the contract

**Intent:** Remove every in-tree duplicate of the vocabulary, preserving every name that has a
referent as an alias so no importer breaks.
**Acceptance:** the AST scan finds no truthy/falsy vocabulary literal outside the three declared
owners; every migrated site agrees with the predicate it replaced on the whole `_MATRIX` except at the
declared cells in Step 3.
**Files:**
- Modify: `tortoise/backup_config.py`, `retrieval.py`, `rerank.py`, `why.py`, `monitoring.py`,
  `sdk.py`, `hosted_api.py`, `extractor_v2.py`, `projection/__init__.py`, `embeddings.py`,
  `cimd.py`, `frontmatter_validator.py`, `model_adapters.py`
- Test: `tests/test_env_truthy.py`, `tests/test_cimd_ssrf.py`, `tests/test_frontmatter_validator.py`

**Step 1: the shared-helper dispositions**

| Existing name | Action | Why |
|---|---|---|
| `backup_config._env_bool` | **delete**; call `env_flag(name, False)` at its 6 sites (`:252, :253, :274, :289, :428, :429`) | no referent outside `backup_config.py`; every call passes `default=False`, where `env_flag` is equivalent cell-for-cell |
| `retrieval.ask_env_bool` | **keep**, body becomes `return env_flag(name, default)` | imported by `ask_lane.py:407`, `tests/test_evidence_assembly.py:53` |
| `retrieval._ASK_TRUTHY` / `_ASK_FALSY` | **delete** (code review, cycle 1) | the names had **no referent outside `ask_env_bool`** — nothing else in the tree named them, and inlining that one reader into `env_flag` is what `_old_tristate` pins — so the "documented knob surface" rationale was wrong: keeping them would have shipped dead aliases advertising a knob nothing reads |
| `rerank._TRUTHY` | **keep** as `from tortoise.env_truthy import TRUTHY as _TRUTHY` (referenced by `rerank_enabled`, so F401 does not fire) | imported by `tools/longmem_eval/rerank.py:38` and `retrieve.py` (6 sites) |
| `cimd._env_flag` | **delete**; replace BOTH call sites with `env_flag` — `cimd.py:119` (`cimd_enabled`) **and `cimd.py:127` (`same_origin_redirects_required`)** | `client_id_metadata_document_supported` (`cimd.py:130`) delegates to `cimd_enabled()` and is left alone |

**Step 2: site migrations** (line anchors are the statement/`def`, not a leading comment)

| File | Site (HEAD) | Replacement |
|---|---|---|
| `why.py:99-102` | `w4_enrichment_enabled`, `why.W4_FLAG_ENV` | `return is_truthy(v)` |
| `monitoring.py:1660-1665` | `_healthz_required` | `return is_truthy(os.environ.get("TORTOISE_HEALTHZ_REQUIRED"))` |
| `sdk.py:1300-1301` | `_ep_require_calibration_default` | `return is_truthy(os.environ.get("TORTOISE_EP_REQUIRE_CALIBRATION", "1"))` |
| `sdk.py:17212-17215` | `TortoiseSDK._index_no_network` (a **method**) | add module-level `def _index_no_network_enabled() -> bool: return env_flag("TORTOISE_INDEX_NO_NETWORK", False)` and have the method `return _index_no_network_enabled()`; **delete the orphaned local `import os as _os`** |
| `hosted_api.py:17771-17772` | `volunteer_context` SLO flag | add module-level `def _volunteer_slo_enforced() -> bool: return is_truthy(os.environ.get("TORTOISE_VOLUNTEER_ENFORCE_SLO"))` and call it |
| `hosted_api.py:5971-5980` | `_signup_email_confirm` | `return env_flag("TORTOISE_SIGNUP_EMAIL_CONFIRM", True)` |
| `hosted_api.py:16726-16731` | `_linking_available` | `return is_truthy(os.environ.get("TORTOISE_MANUAL_LINKING_ENABLED"))` |
| `hosted_api.py:19657-19662` | `_telemetry_strict` | `return is_truthy(os.environ.get(_TELEMETRY_STRICT_ENV))` |
| `extractor_v2.py:510-512` | `_classify_later_enabled` | `return is_truthy(os.environ.get("TORTOISE_CLASSIFY_LATER"))` |
| `frontmatter_validator.py:92-97` | `validation_enabled` | `return is_truthy(os.environ.get(TORTOISE_VALIDATE_FRONTMATTER))` |
| `model_adapters.py:886-887` | `_should_send_json_mode` | `return (is_truthy(os.environ.get("TORTOISE_JSON_MODE", "1")) and _prompt_requests_json(system, user))` |
| `projection/__init__.py:1783-1786` | `TORTOISE_EMBEDDED_AOF` | add module-level `def _embedded_aof_enabled() -> bool: return env_flag("TORTOISE_EMBEDDED_AOF", False)` and use it for `aof_enabled` |
| `embeddings.py:195-199` | `EmbeddingModel.start_warm_up` | `if not env_flag("TORTOISE_EMBEDDER_WARMUP", True): return None`, **delete the orphaned function-local `import os`** |

Exactly three imports become orphaned and must be deleted: `cimd.py:63`, `embeddings.py:196`,
`sdk.py:17213`. The four new module-level seams (`_index_no_network_enabled`,
`_volunteer_slo_enforced`, `_embedded_aof_enabled`, plus the Task 3 pair) follow an established
in-repo pattern (`extractor_v2._classify_later_enabled`, `sdk._session_llm_mock_enabled`,
`pack_state._self_heal_disabled`).

**Step 3: the declared behaviour changes — from the V1/V3/V4 migrations AND Task 3**

The complete list, stated as a **class** (which is what Step 4's property test asserts): *for each
migrated site the new predicate agrees with the old one on every input, except that any **declared
truthy spelling** now resolves True for the enable-direction sites, and **blank/whitespace-only**
now resolves to the default for the two `cimd` sites.*

| Site class | Sites | Newly-True inputs |
|---|---|---|
| V1 sites — already wide, **cell-exact** | `why`, `monitoring`, `sdk._ep_require_calibration_default`, `hosted_api._volunteer_slo_enforced`, `extractor_v2`, `retrieval.ask_env_bool`, `rerank` | none |
| V4 sites — falsy-list, **cell-exact** | `embeddings` warm-up, `hosted_api._signup_email_confirm` | none |
| `backup_config` — wide literal replaced by `env_flag(name, False)` | 6 call sites | none |
| V3 sites — `=on` was inert | `sdk._index_no_network_enabled`, `projection._embedded_aof_enabled` | `on`/`ON`/`On` |
| Narrow `== "1"` (default OFF) | `frontmatter_validator`, `hosted_api._linking_available`, `hosted_api._telemetry_strict`, `embedded_lifecycle._fast_atexit_enabled`, `tests._embedded._carve_out_opted_in` | every declared truthy spelling (case variants + surrounding whitespace) |
| Narrow `== "1"` (default **ON**) | `model_adapters._should_send_json_mode` | same — and note this site's silent *disable* today: `TORTOISE_JSON_MODE=true` currently returns False |
| `cimd._env_flag` (falsy list **including** `""`) | `cimd.cimd_enabled`, `cimd.same_origin_redirects_required` | blank/whitespace-only (`""`, `" "`) → the default. For `TORTOISE_OAUTH_CIMD` this is the disclosed **fail-open**; for `TORTOISE_OAUTH_CIMD_SAME_ORIGIN` it is fail-**closed** (blank no longer relaxes the same-origin SSRF guard) |

No migrated site resolves False where it previously resolved True, and no site's newly-True set goes
beyond the declared truthy spellings plus the two `cimd` blank cells.

Two pre-existing tests pin the OLD semantics and are updated:

- `tests/test_cimd_ssrf.py:545` — drop `""` from `test_cimd_can_be_disabled`'s parametrize list; add

```python
@pytest.mark.parametrize("name", ["TORTOISE_OAUTH_CIMD", "TORTOISE_OAUTH_CIMD_SAME_ORIGIN"])
@pytest.mark.parametrize("blank", ["", " "])
def test_cimd_blank_is_unset_not_a_statement(monkeypatch, name, blank):
    """#4097: a blank value (`""` or whitespace-only) is UNSET, not "off".

    The pre-#4097 `_env_flag` carried `""` in its falsy tuple, so a blank value —
    what an unset CI secret materialises as — flipped BOTH default-ON levers. For
    `TORTOISE_OAUTH_CIMD_SAME_ORIGIN` that direction RELAXED the same-origin SSRF
    guard; after #4097 blank falls back to the default (required). `0`/`false`/
    `no`/`off` remain explicit OFF.
    """
    monkeypatch.setenv(name, blank)
    resolver = (cimd.same_origin_redirects_required if name.endswith("SAME_ORIGIN")
                else cimd.cimd_enabled)
    assert resolver() is True
```

- `tests/test_frontmatter_validator.py:206-211` — delete the "ONLY \"1\" enables" comment and move
  `"TRUE"`/`"true"` to the *enabled* side (`test_validation_enabled_flag_on`), leaving the
  `("0", "", "false", "no", "off")` loop as the off case.

**Step 4: the migration property test** — the pre-change oracle, transcribed from `git show HEAD:<path>`
and kept as an **independent copy** (never an import of the new leaf). This is the only mechanism that
can detect an unintended behaviour change: comparing resolvers to the *new* contract cannot.

```python
def _old_truthy(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _old_cimd_flag(name: str) -> bool:
    """`cimd._env_flag` exactly as it was before #4097 (falsy tuple includes "")."""
    raw = os.environ.get(name)
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


_WIDENED = frozenset({"true", "TRUE", "True", "yes", "YES", "Yes", "on", "ON", "On", " 1 ", "1 "})
```

The table is **built lazily inside the test** (Task 5 Step 5) so that the pure-AST guard tests in the
same file never import the hosted stack. The rows:

| label | env name | `new` | `old` | declared |
|---|---|---|---|---|
| `why.w4_enrichment_enabled` | `why.W4_FLAG_ENV` | `why.w4_enrichment_enabled` | `_old_truthy(why.W4_FLAG_ENV)` | ∅ |
| `monitoring._healthz_required` | `TORTOISE_HEALTHZ_REQUIRED` | seam | `_old_truthy(N)` | ∅ |
| `sdk._ep_require_calibration_default` | `TORTOISE_EP_REQUIRE_CALIBRATION` | seam | `_old_truthy(N, "1")` | ∅ |
| `sdk._index_no_network_enabled` | `TORTOISE_INDEX_NO_NETWORK` | seam | `in ("1","true","yes")` | `{on,ON,On}` |
| `hosted_api._volunteer_slo_enforced` | `TORTOISE_VOLUNTEER_ENFORCE_SLO` | seam | `_old_truthy(N)` | ∅ |
| `extractor_v2._classify_later_enabled` | `TORTOISE_CLASSIFY_LATER` | seam | `_old_truthy(N)` | ∅ |
| `retrieval.ask_env_bool` | `TORTOISE_ASK_EVIDENCE_BOOST` | `lambda: retrieval.ask_env_bool(N, False)` | the old tristate body | ∅ |
| `rerank.rerank_enabled` | `TORTOISE_ASK_RERANK` | `rerank.rerank_enabled` | `os.environ.get(N,"").strip().lower() in ("1","true","yes","on")` | ∅ |
| `projection._embedded_aof_enabled` | `TORTOISE_EMBEDDED_AOF` | seam | `in ("1","true","yes")` | `{on,ON,On}` |
| `hosted_api._linking_available` | `TORTOISE_MANUAL_LINKING_ENABLED` | seam | `== "1"` | `_WIDENED` |
| `hosted_api._telemetry_strict` | `TORTOISE_TELEMETRY_STRICT` | seam | `== "1"` | `_WIDENED` |
| `frontmatter_validator.validation_enabled` | `TORTOISE_VALIDATE_FRONTMATTER` | seam | `== "1"` | `_WIDENED` |
| `model_adapters._should_send_json_mode` | `TORTOISE_JSON_MODE` | `lambda: model_adapters._should_send_json_mode("", "please return json")` | `== "1"` (default `"1"`) | `_WIDENED` |
| `hosted_api._signup_email_confirm` | `TORTOISE_SIGNUP_EMAIL_CONFIRM` | seam | `not in ("false","0","no","off")` | ∅ |
| `cimd.cimd_enabled` | `TORTOISE_OAUTH_CIMD` | seam | `_old_cimd_flag(N)` | `{"", " "}` |
| `cimd.same_origin_redirects_required` | `TORTOISE_OAUTH_CIMD_SAME_ORIGIN` | seam | `_old_cimd_flag(N)` | `{"", " "}` |
| `embedded_lifecycle._fast_atexit_enabled` | `TORTOISE_FAST_ATEXIT` | seam | `== "1"` | `_WIDENED` |
| `tests._embedded._carve_out_opted_in` | `TORTOISE_TEST_CARVE_OUT` | seam | `== "1"` | `_WIDENED` |

```python
@pytest.mark.parametrize("label,env_name,new,old,declared", _sites(),
                         ids=[row[0] for row in _sites()])
def test_migration_only_widens_at_the_declared_cells(monkeypatch, label, env_name, new, old, declared):
    """No migrated site may narrow; it may only widen at the declared cells."""
    for raw in _MATRIX:
        _set(monkeypatch, env_name, raw)
        was, now = bool(old()), bool(new())
        if was:
            assert now, f"{label} REGRESSED at {raw!r} (was True, now False)"
        elif now:
            assert raw in declared, (
                f"{label} newly True at undeclared input {raw!r} — add it to the Step 3 table "
                "and to this row's `declared` set, or fix the migration"
            )
```

The `embeddings` warm-up site is asserted separately (its predicate is a *skip*): for every matrix
value, `(not env_flag("TORTOISE_EMBEDDER_WARMUP", True)) == _old_warmup_skips()`, where
`_old_warmup_skips()` reproduces `os.environ.get(..., "1").strip().lower() in ("0","false","no","off")`.
The `backup_config` sites are covered structurally (its literal must be gone — Task 5's literal clause)
plus `tests/test_backup_config.py` in Task 6.

---

### Task 3: The two narrow sites that move, plus the one that deliberately does not

**Intent:** Fix the fail-closed silent no-op at the two non-destructive narrow sites; keep the
destructive one narrow and *mark* that departure so it cannot be tidied away.
**Acceptance:** `TORTOISE_FAST_ATEXIT=yes` and `TORTOISE_TEST_CARVE_OUT=yes` now take effect;
`TORTOISE_TEST_SWEEP_TEAM_STRAYS=yes` still does NOT; the exception is pinned by a test that cites the
OVERRIDES line.
**Files:**
- Modify: `tortoise/embedded_lifecycle.py`, `tests/_embedded.py`
- Test: `tests/test_env_truthy.py`

**Step 1: named seams**

```python
# tortoise/embedded_lifecycle.py
def _fast_atexit_enabled() -> bool:
    """The TORTOISE_FAST_ATEXIT opt-in (#1371), through the declared contract (#4097).

    Truthy spellings (1/true/yes/on) enable; unset/blank/falsy/garbage stay OFF.
    The fast path is additionally gated by `_is_ephemeral_test_server` + an
    ephemeral socket dir, so widening cannot reach a production data file.
    """
    return is_truthy(os.environ.get("TORTOISE_FAST_ATEXIT"))
```
then `if not _fast_atexit_enabled(): return False` in `atexit_fast_close` (was `!= "1"`).

```python
# tests/_embedded.py
def _carve_out_opted_in() -> bool:
    """The TORTOISE_TEST_CARVE_OUT opt-in, through the declared contract (#4097)."""
    return is_truthy(os.environ.get("TORTOISE_TEST_CARVE_OUT"))
```
then `if _carve_out_opted_in(): return` in `_assert_p4_uri_required` (was `== "1"`).

**Step 2: the deliberate exception** — `_team_sweep_allowed` keeps `== "1"`:

```python
    # OVERRIDES (#4097): env-truthiness truthy-set parsing ("1"/"true"/"yes"/"on").
    # This gate requires the exact value "1": it is the SOLE authorization for an
    # irreversible journal-blind DETACH DELETE + GRAPH.DELETE of the real-tenant
    # org_*/team_* namespace (the `uri` parameter is dead — the #1884 URI inference
    # was retracted — so no containment check compensates), and widening a
    # destructive opt-in surface is not a vocabulary-coherence win. The refusal is
    # logged with the exact required spelling, so the narrowing is discoverable.
    # Pinned by tests/test_env_truthy.py::test_team_sweep_gate_is_narrow_by_design.
    return os.environ.get("TORTOISE_TEST_SWEEP_TEAM_STRAYS") == "1"
```

**Step 3: the exception's own test** — `_team_sweep_allowed("x")` True only for `"1"`, False for
`"true"`/`"yes"`/`"on"`/`" 1 "`/`""`/absent, citing the OVERRIDES line and #4097.

---

### Task 4: The reaper keeps a pinned mirror + its previously-untested purity

**Intent:** Keep the reaper standalone-runnable (measured property) while making the *parity* with the
shared vocabulary a test, not a hope.
**Acceptance:** `_env_truthy` agrees with `is_truthy` over the full matrix; the reaper executes
standalone with `redislite`/`falkordb`/`redis`/`httpx` blocked (both PASS today — verified
independently in three review cycles).
**Files:**
- Modify: `tortoise/embedded_reaper.py` (the resolver body changes to use the new constant — **not**
  comment-only)
- Test: `tests/test_env_truthy.py`

**Step 1: hoist the vocabulary into a named constant.** Today it is an inline tuple inside the
function (`embedded_reaper.py:2643-2644`); after this step the file has **exactly one** vocabulary
literal, which is what the guard counts:

```python
# Dependency-free mirror of tortoise.env_truthy.TRUTHY (#4097). This module has NO
# intra-package module-level imports by design (see the module docstring), so it
# cannot import the shared leaf without dragging in tortoise/__init__.py ->
# redislite. A module-level delegate was measured to break the standalone import
# purity pinned by
# tests/test_env_truthy.py::test_reaper_standalone_import_stays_dependency_free;
# the mirror is held in lockstep with the contract by
# tests/test_env_truthy.py::test_reaper_mirror_matches_the_contract.
_ENV_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_truthy(raw: str | None) -> bool:
    """Truthy env value: {1,true,yes,on}, case-insensitive.

    #4068: `None` (unset) is False. Mirrors `tortoise.env_truthy.is_truthy` (see
    `_ENV_TRUTHY` above); the `--full-scan` flag takes precedence over the env var
    (the `_parse_timeout` CLI > env > default shape).
    """
    return raw is not None and str(raw).strip().lower() in _ENV_TRUTHY
```

**Step 2: three tests** — mirror parity over the matrix; the `runpy` + `builtins.__import__`
blocklist purity probe; and an AST assertion that every module `tortoise/env_truthy.py` imports is in
`sys.stdlib_module_names`.

---

### Task 5: The guard

**Intent:** Make the *next* ad-hoc copy (the reaper's #4068 resolver was the 9th one) fail CI.
**Acceptance:** a new vocabulary literal, or a new/extra narrow `== "1"` read, reds the suite and names
the file.
**Files:**
- Modify: `tests/test_env_truthy.py`

**Step 1: the scanner — the implementation, verbatim** (a regex cannot do this: it misses
`.strip().lower() == "1"`, module-constant env names, `not in (...)` falsy forms, and reversed
operands):

```python
_SCAN_SURFACE = (REPO_ROOT / "tortoise", REPO_ROOT / "tests" / "_embedded.py")

#: Files permitted to declare a truthy/falsy vocabulary literal -> (expected COUNT, reason).
#: COUNT-pinned (not line-pinned): line drift must not break the guard, but a second
#: literal in the same file must.
_LEDGER_LITERAL_OWNERS: dict[str, tuple[int, str]] = {
    "tortoise/env_truthy.py": (2, "the declaration (TRUTHY + FALSY)"),
    "tortoise/embedded_reaper.py": (1, "pinned mirror — the reaper must stay standalone-importable"),
    "tortoise/backup_sweep.py": (
        1, "deferred to #4128: `=on` would suppress the ENUM_DELTA incident guard, so its "
           "widening needs its own decision"),
}


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


def _env_var_name(node: ast.expr, constants: dict[str, str]) -> str | None:
    node = _unwrap_chain(node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in ("get", "getenv"):
        base = node.func.value
        is_os = (isinstance(base, ast.Name) and base.id in ("os", "_os")) \
            or (isinstance(base, ast.Attribute) and base.attr == "environ")
        if is_os and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
            if isinstance(arg, ast.Name):
                return constants.get(arg.id, "<dynamic>")
            return "<dynamic>"
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
            and node.value.attr == "environ":
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return node.slice.value
    return None


def _is_vocabulary(values: set[str]) -> bool:
    """A DECLARED vocabulary literal: >= 2 members, all truthy/falsy spellings, anchored
    on "1" or "0". The anchor and the >= 2 rule keep single-element collections such as
    `dict.get("page", ["1"])[0]` (tortoise/indexer/github_indexer.py:258) and stopword
    sets ({on, yes}) out of the scan."""
    if len(values) < 2 or not values <= (TRUTHY | FALSY):
        return False
    return ("1" in values and bool(values & {"true", "yes", "on"})) \
        or ("0" in values and bool(values & {"false", "no", "off"}))


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
            for node in ast.walk(tree):
                if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
                    values = {e.value for e in node.elts
                              if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                    if _is_vocabulary(values):
                        literals.append((rel, node.lineno))
                if isinstance(node, ast.Compare):
                    names = {n for n in (_env_var_name(s, constants)
                                         for s in (node.left, *node.comparators)) if n}
                    if not names:
                        continue
                    hit = False
                    for op, comparator in zip(node.ops, node.comparators):
                        if isinstance(op, (ast.Eq, ast.NotEq)) \
                                and isinstance(comparator, ast.Constant) \
                                and comparator.value in ("1", "0"):
                            hit = True
                        if isinstance(op, (ast.In, ast.NotIn)) \
                                and isinstance(comparator, (ast.Tuple, ast.Set, ast.List)):
                            values = {e.value for e in comparator.elts
                                      if isinstance(e, ast.Constant)
                                      and isinstance(e.value, str)}
                            # a NARROW membership test only: `("1",)` counts; the wide
                            # vocabulary does not (the literal clause catches that).
                            if ("1" in values or "0" in values) and not _is_vocabulary(values):
                                hit = True
                    if hit:
                        for name in names:
                            narrow.append((rel, name, node.lineno))
    return literals, narrow
```

**Step 2: the ledger — generated by `_scan()` on the tree AFTER Tasks 2–3** (17 keys / 30 lines;
a `(module, name)` key alone cannot catch a *ninth* `RATE_LIMIT_DISABLED == "1"` read). Provenance
note: running the scan on the **pre-change** tree yields 23 keys / 36 lines, which would fail the
zero-check below.

```python
#: Narrow `== "1"` reads that REMAIN after this PR, with their site COUNT as an UPPER
#: BOUND. Widening the #4128 ones is a fail-open loosening, so each needs its own
#: decision; the first is the deliberate OVERRIDES exception. A new (module, name) reds;
#: an EXTRA read of a listed pair reds (the count); a pair with ZERO sites left reds as
#: closed and must be deleted from the ledger — so the ledger can only shrink, and
#: shrinking it is part of closing #4128.
_KNOWN_NARROW_READS: dict[tuple[str, str], tuple[int, str]] = {
    ("tests/_embedded.py", "TORTOISE_TEST_SWEEP_TEAM_STRAYS"):
        (1, "OVERRIDES — sole authorization for an irreversible tenant-namespace delete"),
    ("tortoise/sdk.py", "TORTOISE_ALLOW_PRODUCTION"): (1, "#4128 — grants production access"),
    ("tortoise/mcp_server.py", "TORTOISE_ALLOW_EMBEDDED"): (1, "#4128 — grants embedded mode"),
    ("tortoise/projection/__init__.py", "TORTOISE_ALLOW_NONSTANDARD_PATH"):
        (2, "#4128 — relaxes path containment"),
    ("tortoise/projection/__init__.py", "TORTOISE_TEST_ALLOW_REMOTE"):
        (1, "#4128 — relaxes the remote-URI guard"),
    ("tortoise/projection/__init__.py", "TORTOISE_TEST_MODE"): (3, "#4128 — relaxes path/remote guards"),
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
    ("tortoise/hosted_api.py", "TORTOISE_SESSION_LLM_MOCK"): (1, "#4128 — swaps the LLM for a mock"),
    ("tortoise/__main__.py", "TORTOISE_SESSION_LLM_MOCK"): (1, "#4128 — swaps the LLM for a mock"),
}
_LEDGER_CEILING = 30   # total lines; a delete-then-re-add elsewhere cannot mask growth
```

**Step 3: the assertions** (exact-equality on literal owners; an *upper-bound* per key on the ledger,
so a legitimate consolidation that removes site lines without removing the read does not produce a
spurious failure):

```python
def test_no_adhoc_vocabulary_literal_outside_the_contract():
    literals, _ = _scan()
    counts = Counter(rel for rel, _line in literals)
    expected = {rel: n for rel, (n, _why) in _LEDGER_LITERAL_OWNERS.items()}
    assert dict(counts) == expected, (
        "truthy/falsy vocabulary ownership drifted (#4097): "
        f"found {dict(counts)}, expected {expected} — import TRUTHY/FALSY instead of "
        "declaring another literal"
    )


def test_narrow_env_reads_are_the_frozen_ledger():
    _, narrow = _scan()
    seen = Counter((rel, name) for rel, name, _line in narrow)
    grown = {key: (n, _KNOWN_NARROW_READS[key][0]) for key, n in seen.items()
             if n > _KNOWN_NARROW_READS.get(key, (0, ""))[0]}
    assert not grown, (
        "new narrow env read site(s) outside the declared contract (#4097) — use "
        "tortoise.env_truthy, or raise the ledger entry with a reason: " + repr(grown)
        + "  (widening a listed name is #4128's decision, not a drive-by edit)"
    )
    assert sum(seen.values()) <= _LEDGER_CEILING, (
        f"total narrow-read lines grew to {sum(seen.values())} (ceiling {_LEDGER_CEILING})"
    )
    closed = sorted(key for key in _KNOWN_NARROW_READS if key not in seen)
    assert not closed, (
        "closed _KNOWN_NARROW_READS entr(ies) — the read is gone, delete the entry so the "
        "ledger can only shrink (#4128): " + ", ".join(f"{r}::{n}" for r, n in closed)
    )
```

**Step 4: the declared guard boundary** (in the test docstring and #4128): the scan covers
`tortoise/**/*.py` + `tests/_embedded.py`. It does **not** police V5 presence reads, split comparisons
(`raw == "1" or raw == "true"`), a partial vocabulary without `"1"`/`"0"`, `getattr(os.environ, …)`
reads, or `tests/` beyond `_embedded.py`, `tools/`, `graph-scripts/`, `apps/` — which already carry
their own literals (`tests/test_product_rerank.py:66`, `tests/test_monitoring.py:2233`,
`tests/test_reaper.py:3425`, `tests/test_eval_ingest_cache.py:243`,
`tests/test_extractor_v2.py:3009`, `tests/longmem_eval/test_assembly_arm.py:145`,
`tests/eval/why_suite/test_why_suite_ab.py:71`, `tests/test_email_signup.py:127`,
`apps/graph-viz/server/connection.py:30`). The supported claim is "no new divergence **inside the
declared surface**", not "anywhere in the repo". A JS/TS scan of `website/functions/`,
`supabase/functions/`, `client/`, `menu-bar/` found **no** boolean env-truthiness parsing, so there is
no cross-language duplication to guard.

**Step 5: keep the guard file import-light.** The `_sites()` table from Task 2 Step 4 imports
`hosted_api`/`sdk`/`projection` (~20 s). Build it **inside** the test (or a fixture), never at module
scope, so `test_no_adhoc_vocabulary_literal_outside_the_contract` /
`test_narrow_env_reads_are_the_frozen_ledger` import only `ast`, `pathlib`, `collections` and the leaf.

**Step 6: run the whole file** → PASS.

---

### Task 6: Verification and evidence

**Intent:** Prove the change rather than assert it.
**Acceptance:** every command is run and its result recorded in the PR body.
**Files:** none (evidence only)

```bash
PY=/Users/danielospina/Documents/GitHub/tortoise/.venv/bin/python

# 1. the guard suite (the carve-out is required: tests/conftest.py:159-166 fails URI-less)
TORTOISE_TEST_CARVE_OUT=1 $PY -m pytest tests/test_env_truthy.py -q

# 2. every suite that exercises a migrated site
TORTOISE_TEST_CARVE_OUT=1 $PY -m pytest \
  tests/test_reaper.py tests/test_embedded_lifecycle_fast_close.py tests/test_embedded_lifecycle.py \
  tests/test_markers.py tests/test_wipe_server.py tests/test_ci_selection.py \
  tests/test_evidence_assembly.py tests/test_product_rerank.py tests/test_monitoring.py \
  tests/test_extractor_v2.py tests/test_eval_ingest_cache.py tests/test_cimd_ssrf.py \
  tests/test_oauth_mcp.py tests/test_backup_config.py tests/test_backup_sweep.py \
  tests/test_backup_graph_enum.py tests/test_2952_degraded_read.py \
  tests/test_embedded_durability_claim.py tests/test_embedded_concurrency.py \
  tests/test_w4_why_enrichment.py tests/test_ranking_w4b.py tests/test_cli_signup.py \
  tests/test_email_signup.py tests/test_frontmatter_validator.py \
  tests/test_model_adapters_routing.py tests/test_calibration.py tests/test_probe_json_mode.py \
  tests/test_uri_env_mutations_declared.py -q

# 3. lint — the WHOLE tree (CI runs `ruff check .`); never `ruff format`
$PY -m ruff check .

# 4. the measurement that justified the mirror
TORTOISE_TEST_CARVE_OUT=1 $PY -m pytest \
  tests/test_env_truthy.py::test_reaper_standalone_import_stays_dependency_free \
  tests/test_env_truthy.py::test_reaper_mirror_matches_the_contract -q

# 5. the guard itself, against the real tree
TORTOISE_TEST_CARVE_OUT=1 $PY -m pytest \
  tests/test_env_truthy.py::test_no_adhoc_vocabulary_literal_outside_the_contract \
  tests/test_env_truthy.py::test_narrow_env_reads_are_the_frozen_ledger -q
```

---

## Review corrections (this plan supersedes the scoping comment on these points)

Plan review ran 4 cycles (tier bound 3; the 4th was run because cycle 3's exit left two P1s, and it
is disclosed in the PR body). What changed:

1. **`env_bool` and `is_falsy` are DROPPED** from the API, halving it to `is_truthy` + `env_flag`.
   `env_bool`'s distinguishing cell (garbage with `default=True`) is unreachable — every
   production `env_bool` caller passes `default=False`, where `env_flag` is identical cell-for-cell —
   and `is_falsy` has no call site at all. The scoping comment's "both contracts are in real use" was
   wrong on this point; the "typo reads OFF" contract is still expressible as
   `is_truthy(os.environ.get(name, <default-value>))`.
2. **Two P1s found in plan review were fixed before implementation**: `sdk._index_no_network` is a
   `TortoiseSDK` **method**, not a module-level name (referencing it in the property table would raise
   `AttributeError` at collection), and `tests/test_frontmatter_validator.py:206-211` is a **second**
   pre-existing pin of the narrow semantics that the `frontmatter_validator` migration would red.
3. The four re-bucketed reads (`TORTOISE_VALIDATE_FRONTMATTER`, `TORTOISE_JSON_MODE`,
   `TORTOISE_MANUAL_LINKING_ENABLED`, `TORTOISE_TELEMETRY_STRICT`) are now in the migration table —
   the scoping comment claimed they were in scope but never listed them.
4. `TORTOISE_SUPPRESS_ENUM_DELTA` was moved OUT of scope (it is unchanged in the tree, keeps its V3
   literal, and is the third literal owner) and added to #4128.
5. **Code review (after implementation), not plan review, corrected this plan's Step-1 row for
   `retrieval._ASK_TRUTHY`/`_ASK_FALSY`: they are DELETED, not kept.** The row above is corrected;
   the names had no referent outside `ask_env_bool`, so keeping them would have shipped dead aliases
   advertising a knob nothing reads. The reaper's mirror was also corrected in the same pass: it is
   pinned to the contract by vocabulary **identity**, not only by matrix parity. A later code-review
   cycle also widened the guard's constant-anchor resolution to the ANNOTATED assignment form
   (`_ONE: str = "1"`), which the scanner's own docstring already claimed to resolve.

## Deliberately NOT in this plan

1. The 12 deferred narrow names (30 ledger lines) → **#4128**; plus `TORTOISE_SUPPRESS_ENUM_DELTA`
   (`backup_sweep.py:137`).
2. V5 presence reads (`=0` reads as enabled) → recorded in #4128.
3. `apps/graph-viz/server/connection.py:30` (separate deployable, already on the wide set).
4. Making `tortoise/__init__.py` lazy — rejected: it would defeat the redislite relative-path guard's
   ordering contract.
5. The dead `uri` parameter on `tests/_embedded.py::_team_sweep_allowed` → noted in #4128.

## Risks

| Risk | Mitigation |
|---|---|
| A migrated site changes behaviour at an undeclared cell | `test_migration_only_widens_at_the_declared_cells` compares each site against a copy of its **pre-change** predicate over the full matrix: narrowing reds; a widening outside the declared set reds |
| The two `cimd` blank cells flip a security-relevant lever | Both pinned by `test_cimd_blank_is_unset_not_a_statement` (incl. whitespace-only); SAME_ORIGIN's flip is fail-closed, CIMD's is the disclosed fail-open |
| The guard becomes a maintenance trap | AST-based; literal owners count-pinned; ledger counts are upper bounds (legitimate consolidation passes); new (module, name) reds; zero-site entries must be deleted; boundary declared |
| The guard's claim is over-read | The docstring says "inside the declared surface" and enumerates the out-of-surface literals |
| The reaper regresses to a heavy import | `test_reaper_standalone_import_stays_dependency_free` measures the property directly |
| Orphaned imports turn CI red | Step 2 names all three (`cimd.py:63`, `embeddings.py:196`, `sdk.py:17213`); Task 6 lints the whole tree, whose baseline is clean |
| A test is URI-less-fragile | Every Task 6 command sets `TORTOISE_TEST_CARVE_OUT=1` |
| The guard file becomes slow/fragile | The migrated-module imports are lazy (Task 5 Step 5) |

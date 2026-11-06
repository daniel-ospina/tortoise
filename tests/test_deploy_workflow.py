"""Deploy-workflow ↔ runtime-registry parity guard (#1197, PR #1220 review P2 c70).

The provider gate and Fly secrets-propagation in
``.github/workflows/deploy-hosted.yml`` hardcode the LLM provider key names
(``OPENROUTER/DEEPSEEK/OPENAI/GEMINI_API_KEY``). The runtime registry
(``hosted_api._LLM_PROVIDER_KEYS``, derived from ``ingest._PROVIDERS`` /
``analyze._LLM_PROVIDERS``) is the source of truth the provider check
actually consumes. A rename in the registry that is not mirrored in the
workflow drifts SILENTLY: the deploy gate keeps passing (the GH secret name
still matches) but the key never propagates to Fly — every capture STORES its
turns and extracts nothing into memory, with zero failing tests.

These tests read the workflow file and assert BOTH the verify-secrets gate and
the secrets-set propagation use EXACTLY the runtime registry key set (both
directions: no missing key, no extra key), AND that each key is actually
checked in the gate (warn-only since #1346) and actually appended to the Fly
secrets ARGS — a key merely *referenced* (echoed, commented, gated on with
wrong semantics) passes name parity but still ships a deploy whose captures
never extract. Anchored to the
#1197 marker comments so a moved block fails loudly instead of silently
passing.

The shape assertions were updated for #4334. GitHub substitutes ``${{ … }}``
into a ``run:`` script's TEXT before bash parses it, so interpolating a secret
there mangles values (a JSON catalog's ``"`` characters are read as quote
toggles and REMOVED — the STRIPE_PRICE_IDS outage) and executes content like
``$(…)`` / backticks. The fix binds every secret in the step's ``env:`` and
references it as a quoted shell variable, passing flyctl a bash ARRAY. These
tests assert that safe shape: every secret is bound in ``env:``
(``KEY: ${{ secrets.KEY }}``) AND appended to the array (``ARGS+=(KEY="$KEY")``),
and that no ``${{ secrets.X }}`` remains in any step's run text.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deploy-hosted.yml"

# Anchors inside the workflow (the #1197 blocks). Slicing between markers keeps
# the scan scoped: RESEND_API_KEY etc. live in other blocks and must not leak in.
_GATE_START = "# Session capture LLM provider (#1197, #1346)"
_GATE_END = 'if [ -z "$STRIPE_PRICE_IDS" ]; then'
_PROP_START = "# Session-capture LLM provider (#1197)"
_PROP_END = "# Optional (env-gated at runtime)"

# The two steps whose run bodies carry secrets (#4334). Both must bind every
# secret in `env:` and reference it as a quoted shell variable.
_VERIFY_STEP = "Verify secrets exist"
_SET_STEP = "Set all app secrets on Fly.io (keeps in sync with GitHub/Supabase)"

# FLY_API_TOKEN is consumed by flyctl from the environment — it is never a
# `KEY=value` argv element, so it is the one env-bound secret exempt from the
# "must reach the flyctl array" check.
_NON_SYNCED = {"FLY_API_TOKEN"}

# Shell test against a variable in a run region: `[ -z "$KEY" ]` / `[ -n "$KEY" ]`.
_SHELL_TEST = re.compile(r'\[ -[zn] "\$([A-Z0-9_]+)" \]')
# A flyctl argv array: ARGS=( … ) / ARGS+=( … ) and each FLYVAR="$SHELLVAR"
# (or "${SHELLVAR}") pair. The brace spelling is accepted so an argv element
# cannot hide from the coverage guards by using it (the workflow already uses
# it for TORTOISE_GIT_SHA).
_ARRAY_ELEMENT = re.compile(r"ARGS\+?=\(([^)]*)\)")
_ARRAY_PAIR = re.compile(r'([A-Z0-9_]+)="\$\{?([A-Z0-9_]+)\}?"')
# The argv pairs whose value is NOT an env-bound secret: `GITHUB_SHA` is a GitHub
# built-in, and `TORTOISE_GIT_DIRTY` is workflow-computed from the build's own tree
# (#10447) and can never be empty (the payload defaults to `unknown`, which /health
# reads as null). Exempted BY NAME, so the parser can accept the brace spelling
# without the exemption depending on a regex that misses it — and this set also
# authorises an UNCONDITIONALLY staged value (no `-n` guard); the exemptions are
# NAMED in `test_optional_fly_appends_are_n_guarded`'s docstring, so keep the two
# in step (a numbered count is what drifted twice already).
_NON_SECRET_PAIRS = {("TORTOISE_GIT_SHA", "GITHUB_SHA"),
                     # #10447: workflow-COMPUTED from the build's own tree, never an
                     # env-bound secret, and never empty — an unreadable tree stages
                     # `unknown` (which /health reads as null), so an `-n` guard would
                     # buy nothing and the guard exists to stop a SECRET clobber.
                     ("TORTOISE_GIT_DIRTY", "TORTOISE_GIT_DIRTY_STAMP")}
# The only argv keys whose FLY-side name deliberately differs from the env var
# it reads. Every other key must equal the variable it reads — the app resolves
# its config by the env NAME, so a fly-side typo ships the secret under a name
# nothing reads while the verify-secrets gate still passes.
_FLY_RENAMES = {
    "GITHUB_CLIENT_ID": "GH_CLIENT_ID",
    "GITHUB_CLIENT_SECRET": "GH_CLIENT_SECRET",
    "GITHUB_CALLBACK_URL": "GH_CALLBACK_URL",
    # #4126: the Fly variable keeps the RuntimeEnv name the app reads, while the
    # GitHub secret keeps its legacy name (see fly-managed-secrets.txt).
    "SUPABASE_SERVICE_ROLE_KEY": "SUPABASE_SERVICE_KEY",
}

# #4126: names whose flyctl assignment is deliberately UNCONDITIONAL. The value
# is versioned NON-SECRET config (a model id), so it MUST overwrite a hand-set
# Fly value on every deploy — an unset GitHub secret pushes the recorded default,
# never an empty string, so the clobber risk the `-n` guard exists for cannot
# occur. Kept as an explicit name→default map so the exemption also pins the
# constant: a dropped default would leave `KEY=` to clobber the Fly value.
_UNCONDITIONAL_BY_DECISION = {
    "TORTOISE_SESSION_LLM_MODEL": "openrouter:google/gemini-2.5-flash",
}
# A step env binding to a single secret: `KEY: ${{ secrets.KEY }}`.
_ENV_SECRET = re.compile(r"\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}")
# Any secret interpolation at all — the #4334 hazard.
_SECRET_INTERP = re.compile(r"\$\{\{[^}]*secrets\.[A-Z0-9_]+[^}]*\}\}")


def _region(text: str, start: str, end: str) -> str:
    """Slice ``text`` between two unique anchor substrings (exclusive end)."""
    s = text.index(start)  # ValueError → anchor moved; the test must fail loudly
    e = text.index(end, s)
    return text[s:e]


def _steps() -> dict[str, dict]:
    """The workflow's named steps, keyed by name.

    A duplicate name is a hard error, never a silent overwrite. Two steps that
    share a name collapse to one here, so a guard that iterates this mapping
    inspects only the survivor — the `deploy-api` and `post-deploy-verify`
    audits were both `Deploy gate audit (#4759)`, which hid the `deploy-api`
    audit from every guard and made one of them vacuous (#4802 review). Keying
    by name is kept because callers look steps up by name; the raise is what
    makes that safe.
    """
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps: dict[str, dict] = {}
    for job_name, job in doc["jobs"].items():
        for step in job.get("steps", []):
            name = step.get("name")
            if not name:
                continue
            assert name not in steps, (
                f"duplicate step name {name!r} (also in job {job_name!r}) — "
                "rename the steps, or a name-keyed guard silently inspects only one"
            )
            steps[name] = step
    return steps


def _env_bindings(step: dict) -> dict[str, str]:
    """Map each env var name bound to a GitHub secret → that secret's name."""
    bindings: dict[str, str] = {}
    for name, value in (step.get("env") or {}).items():
        m = _ENV_SECRET.fullmatch(str(value).strip())
        if m:
            bindings[name] = m.group(1)
    return bindings


def _array_pairs(run: str) -> dict[str, str]:
    """Map each flyctl argv key → the quoted shell variable it reads."""
    pairs: dict[str, str] = {}
    for group in _ARRAY_ELEMENT.findall(run):
        for fly_name, shell_var in _ARRAY_PAIR.findall(group):
            pairs[fly_name] = shell_var
    return pairs


def _secret_array_pairs(run: str) -> dict[str, str]:
    """`_array_pairs` minus the argv pairs sourced from GitHub built-ins."""
    return {k: v for k, v in _array_pairs(run).items() if (k, v) not in _NON_SECRET_PAIRS}


def _prop_region(text: str) -> str:
    return _region(text, _PROP_START, _PROP_END)


@pytest.fixture(scope="module")
def registry_keys() -> frozenset[str]:
    """The runtime registry — imported lazily so merely collecting this test
    module never imports the FastAPI app (review P2: keep collection cheap)."""
    from tortoise.hosted_api import _LLM_PROVIDER_KEYS

    return frozenset(_LLM_PROVIDER_KEYS)


@pytest.fixture(scope="module")
def workflow_text() -> str:
    assert _WORKFLOW.is_file(), f"deploy workflow not found: {_WORKFLOW}"
    return _WORKFLOW.read_text(encoding="utf-8")


# #1358: TORTOISE_SESSION_LLM_MODEL is a MODEL OVERRIDE knob (selects the
# flash-class model when a provider key is present), NOT a provider key — it
# has no provider-gate requirement and does not belong in _LLM_PROVIDER_KEYS, but
# it IS propagated to Fly. The provider-parity tests must allow it as the
# known non-provider extra in the propagation block.
_PROP_EXTRA_KEYS = {"TORTOISE_SESSION_LLM_MODEL"}


def test_verify_secrets_gate_matches_runtime_registry(workflow_text, registry_keys):
    """The deploy gate's key set == _LLM_PROVIDER_KEYS, with the
    warn-only shape (::warning::, no exit 1) intact — the #1346 decision:
    session capture is optional and, with no key, stores turns without
    extracting them (#3892), so
    a missing key must warn, NOT block the whole API deploy.

    A rename in the registry must force an update here, else the gate checks
    a stale name while the app never extracts from a stored capture (gate
    passes, key never consumed)."""
    gate = _region(workflow_text, _GATE_START, _GATE_END)
    gate_keys = set(_SHELL_TEST.findall(gate))
    assert gate_keys, (
        'no shell `[ -z "$KEY" ]` checks found in the verify-secrets provider '
        "block — marker drift"
    )
    assert gate_keys == set(registry_keys), (
        f"deploy gate keys {sorted(gate_keys)} != runtime registry "
        f"{sorted(registry_keys)} — a registry rename not mirrored here lets "
        f"the deploy gate pass while the app never extracts a stored capture "
        f"(docs/infra-runbook.md §4.6)"
    )
    # Semantics (#1346/#1347): the LLM provider gate is WARN-ONLY — a missing
    # key must NOT block the API deploy (session capture is optional; with no
    # key the turns are stored and extraction is skipped, #3892). Assert the ::warning:: shape, not the old
    # fail-closed exit-1 shape.
    assert "::warning::" in gate and "::error::" not in gate, (
        "verify-secrets LLM gate must be warn-only (::warning::, no ::error::) "
        "— a fail-closed gate here blocks ALL API deploys (#1346)"
    )
    # #4334: a gate key referenced as "$KEY" must actually be BOUND in the
    # step's env: block. An unbound variable expands to the empty string, so
    # the check would silently pass (or falsely warn) without ever reading the
    # secret — the same class of "reference it but never wire it" drift.
    bindings = _env_bindings(_steps()[_VERIFY_STEP])
    missing = gate_keys - set(bindings)
    assert not missing, (
        f"verify-secrets gate references {sorted(missing)}, which are not bound "
        f"in the step's env: block — the check reads an empty variable and the "
        f"provider gate silently stops working (#4334)"
    )


def test_secrets_set_propagation_matches_runtime_registry(workflow_text, registry_keys):
    """The secrets-set step propagates the runtime registry key set (plus
    the #1358 model-override extra, which is a non-provider knob).

    The gate and propagation must agree with the registry — a key gated on but
    never propagated ships a deploy that extracts nothing despite a passing gate."""
    prop = _prop_region(workflow_text)
    pairs = _secret_array_pairs(prop)
    prop_keys = set(pairs)
    assert prop_keys, (
        'no ARGS+=(KEY="$KEY") appends found in the secrets-set provider block '
        "— marker drift"
    )
    assert prop_keys == set(registry_keys) | _PROP_EXTRA_KEYS, (
        f"Fly secrets propagation keys {sorted(prop_keys)} != runtime registry "
        f"{sorted(registry_keys)} (+ {sorted(_PROP_EXTRA_KEYS)} extra) — a "
        f"registry rename not mirrored here ships the gate passing while the "
        f"key never reaches Fly (a stored capture that never extracts)"
    )
    # #4334 semantics: each registry key is APPENDED to the flyctl array and
    # reads the shell variable of the SAME name (the env-bound value), never
    # `${{ secrets.KEY }}` re-interpolated into the run text.
    for key in registry_keys:
        assert pairs.get(key) == key, (
            f"{key} is not appended as ARGS+=({key}=\"${key}\") in the "
            f"secrets-set block — the key would stay on GitHub secrets and never "
            f"reach Fly, or would re-interpolate the secret into the shell text "
            f"and be mangled/executed (#4334)"
        )
    # #4334: the shell variable an append reads must be BOUND in the step's
    # env: block — an append reading an unbound variable sends an empty value.
    bindings = _env_bindings(_steps()[_SET_STEP])
    missing = (set(registry_keys) | _PROP_EXTRA_KEYS) - set(bindings)
    assert not missing, (
        f"secrets-set appends read {sorted(missing)}, which are not bound in "
        f"the step's env: block — Fly would receive an empty value (#4334)"
    )


def test_gate_and_propagation_agree_with_each_other(workflow_text, registry_keys):
    """Gate ⊆ propagation ⊆ gate — the two blocks can never diverge
    (modulo the #1358 model-override extra, which is propagated but not
    gated: it is a tuning knob, not a provider-gate requirement)."""
    gate = _region(workflow_text, _GATE_START, _GATE_END)
    prop = _prop_region(workflow_text)
    assert set(_SHELL_TEST.findall(gate)) == set(registry_keys), (
        "verify-secrets gate references a key set that drifted from the "
        "runtime registry — a key gated on but never propagated (or vice "
        "versa) is a deploy hazard"
    )
    assert set(_secret_array_pairs(prop)) == set(registry_keys) | _PROP_EXTRA_KEYS, (
        "verify-secrets gate and secrets-set propagation reference DIFFERENT key "
        "sets — a key gated on but not propagated (or vice versa) is a deploy hazard"
    )


def test_secrets_never_interpolated_into_run_text(workflow_text):
    """#4334 root cause: GitHub substitutes ``${{ … }}`` into a ``run:`` script's
    TEXT before bash parses it. A secret value containing ``"`` is read as a
    quote toggle and REMOVED (the STRIPE_PRICE_IDS catalog reached Fly as
    501 chars with zero quotes), and a value containing ``$(…)`` / backticks
    EXECUTES. No step at all may interpolate a secret into its run text — the
    secret must be bound in ``env:`` and expanded as ``"$VAR"``, whose
    contents are data, never re-parsed."""
    steps = _steps()
    for name, step in steps.items():
        run = step.get("run", "")
        found = _SECRET_INTERP.findall(run)
        assert not found, (
            f"{name} interpolates {found} into the run script. GitHub substitutes "
            "'${{ … }}' into the shell TEXT before bash parses it: a JSON value's "
            "quotes are stripped (the #4334 outage) and '$(…)'/backticks execute. "
            'Bind the secret in `env:` and reference "$VAR" instead.'
        )


def test_every_env_bound_secret_reaches_fly(workflow_text):
    """#4334 — bidirectional coverage for the secrets-set step.

    Every secret bound in the step's ``env:`` must be appended to the flyctl
    array (else it is wired to nothing), every argv element sourced from a
    secret must read an env-bound secret (else it sends a stale/empty value),
    and every secret-bearing fly-side argv key must be the env name the app
    reads — or a declared `_FLY_RENAMES` rename, whose presence is asserted too,
    since a de-renamed key would otherwise ship the secret under a name nothing
    reads while the gate passes. ``FLY_API_TOKEN`` (flyctl consumes it from the
    environment, never as argv) and the `_NON_SECRET_PAIRS` entries
    (`TORTOISE_GIT_SHA`←`GITHUB_SHA`, and `TORTOISE_GIT_DIRTY`←
    `TORTOISE_GIT_DIRTY_STAMP`, which the payload defaults to `unknown` so it is
    never empty) are the exemptions."""
    step = _steps()[_SET_STEP]
    run = step.get("run", "")
    bindings = set(_env_bindings(step))
    shell_vars = set(_secret_array_pairs(run).values())
    assert shell_vars == bindings - _NON_SYNCED, (
        "the secrets-set step's env: bindings and its flyctl array diverge — "
        f"bound-but-not-sent: {sorted(bindings - _NON_SYNCED - shell_vars)}; "
        f"sent-but-not-bound: {sorted(shell_vars - bindings)}"
    )
    # The FLY-side argv name is what the app resolves config by. Pin it to the
    # env name it reads (or a declared rename); a fly-side typo/swap otherwise
    # passes every value-based guard above.
    for fly_name, shell_var in _secret_array_pairs(run).items():
        expected = _FLY_RENAMES.get(fly_name, fly_name)
        assert expected == shell_var, (
            f"fly argv key {fly_name} reads ${shell_var}, but the expected env "
            f"name is {expected} — a fly-side typo/undeclared rename would ship "
            f"the secret under a name nothing reads while the gate passes (#4334)"
        )
    # The forward check above accepts the IDENTITY spelling for a renamed pair,
    # so a declared rename must ALSO be present: de-renaming
    # `GITHUB_CLIENT_ID="$GH_CLIENT_ID"` to `GH_CLIENT_ID="$GH_CLIENT_ID"`
    # otherwise passes every guard while the app (which reads GITHUB_CLIENT_ID)
    # is silently disabled.
    raw_pairs = _array_pairs(run)
    for fly_name, env_name in _FLY_RENAMES.items():
        assert raw_pairs.get(fly_name) == env_name, (
            f"declared rename {fly_name}=\"${env_name}\" is missing from the "
            f"flyctl array — the Fly key was de-renamed to the identity name, so "
            f"the app reads nothing and the feature is silently disabled (#4334)"
        )


def test_env_bindings_use_the_same_named_secret(workflow_text):
    """#4334 — an env binding must read the SAME-named GitHub secret.

    `_env_bindings` records only that a name is bound to *some* secret; without
    this check a copy-paste swap (`DR_ISSUES_PAT: ${{ secrets.TELEGRAM_BOT_TOKEN }}`)
    would reach Fly as the wrong value and pass every other guard."""
    for step_name in (_VERIFY_STEP, _SET_STEP):
        for env_name, secret_name in _env_bindings(_steps()[step_name]).items():
            assert env_name == secret_name, (
                f"{step_name}: env {env_name} is bound to secrets.{secret_name} — a "
                f"cross-wired value reaches Fly/the gate (#4334)"
            )


def test_optional_fly_appends_are_n_guarded(workflow_text):
    """#4334 — every single-key `ARGS+=(K="$K")` append stays `[ -n "$K" ]`-guarded.

    The guard is what keeps an unset optional secret OUT of the argv; dropping
    it would push `K=` to Fly and clobber any out-of-band value. The exemptions,
    named rather than numbered so a stale count cannot hide a deliberate one:
    the multi-pair appends (the base three-key array and the Stripe pair);
    `TORTOISE_GIT_SHA="${GITHUB_SHA}"` — a GitHub built-in that is always set, not
    an env-bound secret (`_NON_SECRET_PAIRS`); `TORTOISE_GIT_DIRTY=` (#10447) — also
    workflow-computed and never empty, because the payload defaults an absent or
    EMPTY stamp file to `unknown`; and the #4126 versioned default
    (`_UNCONDITIONAL_BY_DECISION`), which pushes a recorded non-secret constant,
    never an empty value."""
    run = _steps()[_SET_STEP]["run"]
    for line in run.splitlines():
        m = re.search(r"ARGS\+=\(([^)]*)\)", line)
        if not m:
            continue
        pairs = _ARRAY_PAIR.findall(m.group(1))
        if len(pairs) != 1:
            continue
        if pairs[0] in _NON_SECRET_PAIRS:
            continue  # GITHUB_SHA is always set by GitHub — deliberately unconditional
        _fly, shell_var = pairs[0]
        if shell_var in _UNCONDITIONAL_BY_DECISION:
            # #4126: the service must overwrite a hand-set Fly value with the
            # versioned default, so this assignment is unconditional BY
            # DECISION. The eligibility test (a GitHub secret exists) still
            # chooses the value, and the default branch pushes the constant.
            default = _UNCONDITIONAL_BY_DECISION[shell_var]
            assert f"ARGS+=({_fly}={default})" in run, (
                f"{_fly} is assigned unconditionally (#4126), but the recorded "
                f"default {default!r} is missing from the run — with the GitHub "
                f"secret absent the deploy would push an empty value and clobber "
                f"the Fly secret (and the provenance gate blocks on precisely "
                f"that state)."
            )
            continue
        assert f'[ -n "${shell_var}" ]' in line, (
            f"optional append ARGS+=({pairs[0][0]}=\u0022${shell_var}\u0022) is not "
            f'guarded by [ -n "${shell_var}" ] — an unset secret would be pushed '
            f"to Fly as an empty value, clobbering an out-of-band value (#4334)"
        )


def test_flyctl_receives_the_quoted_array(workflow_text):
    """#4334 — the CALL SITE must expand the array, quoted.

    Reverting `--stage "${ARGS[@]}"` to the pre-fix `--stage $ARGS` sends only
    element 0 of a now-array ARGS (1 of 36 vars) while the construction guards
    stay green — the silent partial-sync class this fix exists to close."""
    run = _steps()[_SET_STEP]["run"]
    assert 'flyctl secrets set --stage "${ARGS[@]}" --app tortoise-y4mjjq' in run, (
        'the secrets-set step must end on flyctl secrets set --stage "${ARGS[@]}" '
        "--app tortoise-y4mjjq"
    )
    assert "--stage $ARGS" not in run, "unquoted `$ARGS` would word-split / send 1 element (#4334)"
    assert '--stage "${ARGS[*]}"' not in run, (
        '"${ARGS[*]}" joins every element into ONE argv word — a malformed value (#4334)'
    )


def test_array_pair_parser_accepts_the_brace_spelling(workflow_text):
    """#4334 regression — the parser must not be evadable via `${VAR}`.

    A brace-spelled element reading an unbound variable would otherwise be
    invisible to every coverage guard and send an empty value to Fly."""
    assert _array_pairs('ARGS+=(EVIL="${EVIL}")') == {"EVIL": "EVIL"}
    # The one non-secret pair is exempted BY NAME (not by the regex missing it).
    step = _steps()[_SET_STEP]
    assert "GITHUB_SHA" not in set(_secret_array_pairs(step["run"]).values())
    assert _array_pairs(step["run"]).get("TORTOISE_GIT_SHA") == "GITHUB_SHA"


# ══════════════════════════════════════════════════════════════════════════════
# #4759 — bypass VISIBILITY and the machine-checked window
#
# Before this change a bypassed gate and a passed gate were indistinguishable to
# a reader of the run summary (the only trace was a `::warning::` inside one
# step's log, and the four gates did not even render that trace the same way),
# and nothing checked how long a `SKIP_*` variable had been left set — the
# incident window closed by prose alone (#4605). These tests pin both halves:
# every bypass is reported through ONE helper into `$GITHUB_STEP_SUMMARY`, every
# deploy job ends with an audit that states each of its gates as bypassed or not,
# and a scheduled workflow ages every set lane.
# ══════════════════════════════════════════════════════════════════════════════

_REPO = Path(__file__).resolve().parent.parent
_BYPASS_SCRIPT = _REPO / ".github" / "scripts" / "deploy-bypass.sh"
_EXPIRY_WORKFLOW = _REPO / ".github" / "workflows" / "skip-bypass-expiry.yml"


def _bypass_gates() -> list[tuple[str, str, str, str]]:
    """The helper's gate table: (variable, input, label, kind) — THE single
    declaration of the bypassable gate set.

    Every guard below DERIVES from this table and validates the workflow PER
    SITE, comparing each surface to it by set equality. That is the fix for the
    round-3 defect class: the old guards restated the gate set by hand (an
    input-name ``expected`` set, an ``expected_jobs`` dict, a per-key count), so
    each drifted from what it claimed to check. A gate added here now forces its
    wiring — dispatch input, report site, audit ``--state``, expiry env, lane
    condition; one added to the workflow alone fails the same equalities. The
    table is not count-pinned: a count is exactly the hand-written assertion
    this section replaces.
    """
    table = _region(_BYPASS_SCRIPT.read_text(encoding="utf-8"), "GATES=(", "\n)")
    rows = [tuple(m.groups()) for m in re.finditer(r'"([^"|]+)\|([^"|]+)\|([^"|]+)\|([^"|]+)"', table)]
    assert rows, "the GATES table in deploy-bypass.sh is empty or unparsable"
    return rows


def _gates_by_var() -> dict[str, tuple[str, str, str]]:
    return {v: (i, label, k) for v, i, label, k in _bypass_gates()}


def _gates_by_input() -> dict[str, str]:
    return {i: v for v, i, _l, _k in _bypass_gates()}


def _jobs() -> dict[str, list[dict]]:
    """Every job's steps, by job name. Steps are iterated positionally, so a
    duplicate step name cannot hide a site from a guard (round-1 defect);
    ``_steps()`` keeps its duplicate-name raise for name-keyed callers."""
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n: j.get("steps", []) for n, j in doc["jobs"].items()}


def _step_runs() -> dict[str, str]:
    """Every named step that has a ``run:`` body → that body."""
    return {name: step["run"] for name, step in _steps().items() if step.get("run")}


# ── structural readers (derived, never hand-written per surface) ─────────────
_ACTION_EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_GATE_VAR = re.compile(r"\bSKIP_[A-Z0-9_]+\b")
_GATE_IN = re.compile(r"\bskip-[a-z0-9-]+\b")
# vars.NAME / vars.NAME == 'true' / inputs.NAME / inputs.NAME || false
_LANE = re.compile(r"^(vars|inputs)\.([A-Za-z0-9_-]+?)(?:\s*==\s*'true'|\s*\|\|\s*false)?$")
_SET_AT = "_SET_AT"


def _gate_of_var(token: str, by_var: dict) -> str | None:
    """A ``SKIP_*`` token as its gate variable; the ``_SET_AT`` companion belongs
    to the same gate, anything else is ``None``."""
    if token in by_var:
        return token
    stripped = token[: -len(_SET_AT)]
    return stripped if token.endswith(_SET_AT) and stripped in by_var else None


def _flag_args(run: str, flag: str) -> list[str]:
    """Values passed to ``--<flag>``, unquoted, across line continuations."""
    out = []
    for m in re.finditer(rf"--{re.escape(flag)}\s+(\"[^\"]*\"|'[^']*'|\S+)", run):
        v = m.group(1)
        out.append(v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v)
    return out


def _only(run: str, flag: str) -> str:
    vals = _flag_args(run, flag)
    assert len(vals) == 1, f"expected exactly one --{flag}, got {len(vals)}"
    return vals[0]


def _lane(step: dict, value: str) -> tuple[str, str]:
    """A ``--flag`` argument as ``(kind, name)`` from the expression it reads.

    A ``$VAR`` reference must be bound in the step's ``env:`` — an unbound
    variable expands to the empty string, which is how a lane reads as "not set"
    (a bypassed gate audited as NOT bypassed) while every name-based guard stays
    green. Evaluated for EVERY lane argument, whatever the shell variable is
    called, this replaces the old ``*_SET_AT``-only binding check.
    """
    if not value.startswith("${{"):
        m = re.fullmatch(r"\$([A-Za-z_][A-Za-z0-9_]*)", value)
        assert m, f"unsupported --flag argument {value!r} (expected $VAR or an expression)"
        env = step.get("env") or {}
        assert m.group(1) in env, (
            f"step {step.get('name')!r} reads ${m.group(1)} but never binds it in env: — "
            "an unbound variable expands to the empty string, so the lane silently "
            "reads as unset"
        )
        value = str(env[m.group(1)])
    expr = _ACTION_EXPR.fullmatch(value.strip())
    assert expr, f"{step.get('name')!r}: {value!r} is not a single Actions expression"
    m = _LANE.fullmatch(expr.group(1).strip())
    assert m, f"{step.get('name')!r}: unrecognised lane expression {expr.group(1).strip()!r}"
    return m.group(1), m.group(2)


def _assert_lane(step: dict, value: str, kind: str, name: str, label: str) -> None:
    got = _lane(step, value)
    assert got == (kind, name), (
        f"step {step.get('name')!r}: {label} must read {kind}.{name} — got {got[0]}.{got[1]}"
    )


def _assert_step_names_only(step: dict, allowed: set[str], by_var: dict, by_input: dict) -> None:
    """The step must name no gate outside ``allowed`` — the ASSOCIATION that makes
    a ``--key`` / ``--state`` swap between sites fail (a name-keyed count cannot)."""
    blob = json.dumps(step, sort_keys=True, default=str)
    for tok in _GATE_VAR.findall(blob):
        assert _gate_of_var(tok, by_var) in allowed, (
            f"step {step.get('name')!r} references gate variable {tok!r}, not in its "
            f"declared gate set {sorted(allowed)} — a cross-wired lane"
        )
    for tok in _GATE_IN.findall(blob):
        assert by_input.get(tok) in allowed, (
            f"step {step.get('name')!r} references input {tok!r}, not in its declared "
            f"gate set {sorted(allowed)}"
        )


def _sites(call: str, flag: str) -> list[tuple[str, dict, list[str], str]]:
    """Every ``deploy-bypass.sh <call>`` site: (job, step, ``--<flag>`` values, run)."""
    out = []
    for job_name, steps in _jobs().items():
        for step in steps:
            run = step.get("run") or ""
            if f"deploy-bypass.sh {call}" in run:
                out.append((job_name, step, _flag_args(run, flag), run))
    return out


def test_every_bypass_reports_through_the_shared_helper():
    """Every bypass renders through ONE reporter, each bound to ITS OWN gate.

    Each site is checked by ASSOCIATION — its ``--key`` must be the gate whose
    dispatch input / repo variable / dated companion that same step binds. A
    per-key count cannot see two steps swapping their ``--key``s (round-3); this
    can, and it forces a fifth gate to add its own report site. No step may
    hand-roll a bypass warning either (the pre-#4759 inconsistency).
    """
    by_var, by_input = _gates_by_var(), _gates_by_input()
    assert len(_sites("report", "key")) == len(by_var), "one report call per bypassable gate"
    seen: dict[str, str] = {}
    for job_name, step, keys, run in _sites("report", "key"):
        assert len(keys) == 1, f"report in {job_name!r} has {len(keys)} --key arguments"
        key = keys[0]
        assert key in by_var, (
            f"report in {step.get('name')!r} names undisclosed gate {key!r} — every "
            "gate must be declared in deploy-bypass.sh's GATES table"
        )
        assert key not in seen, f"{key} is reported twice (jobs {seen[key]} and {job_name})"
        seen[key] = job_name
        _assert_step_names_only(step, {key}, by_var, by_input)
        _assert_lane(step, _only(run, "variable-fired"), "vars", key, "--variable-fired")
        _assert_lane(step, _only(run, "input-fired"), "inputs", by_var[key][0], "--input-fired")
        _assert_lane(step, _only(run, "set-at"), "vars", f"{key}{_SET_AT}", "--set-at")
    assert set(seen) == set(by_var), f"no report site for {sorted(set(by_var) - set(seen))}"
    # No step may still hand-roll a bypass warning in its own run text.
    for name, run in _step_runs().items():
        hit = re.search(r"::warning::[^\n]*(?:BYPASSED|SKIPPED)", run)
        assert hit is None, (
            f"step {name!r} still hand-rolls a bypass warning ({hit.group(0)!r}) — "
            "route it through deploy-bypass.sh report so all four render alike"
        )


def test_bypass_visibility_is_a_run_summary_write():
    """The visibility mechanism is a real ``$GITHUB_STEP_SUMMARY`` append.

    The acceptance is that a bypass is detectable WITHOUT reading a step log, so
    the helper must append to the summary — not merely log. A comment mentioning
    the variable does not count: the append is asserted on a non-comment line.

    Every ``deploy-bypass.sh`` step must also bind the per-job audit marker the
    helper records a fired bypass into — and every such step in a JOB must bind
    the SAME path, because the report step writes that marker and the audit step
    reads it (a divergence makes the audit under-report a live bypass).
    """
    text = _BYPASS_SCRIPT.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert '>>"$GITHUB_STEP_SUMMARY"' in body, (
        "deploy-bypass.sh must APPEND the bypass block to $GITHUB_STEP_SUMMARY "
        "(the run summary), not only echo it to the log"
    )
    # Each report/audit step must leave the summary to the helper, and must bind
    # the per-job audit marker the helper records a fired bypass into. Iterate
    # the jobs directly (as ``test_deploy_jobs_audit_every_bypassable_gate``
    # does) so EVERY such step in EVERY job is examined — a name-keyed mapping
    # would drop one of two same-named steps (#4802 review round 1).
    for job_name, steps in _jobs().items():
        markers = []
        for step in steps:
            run = step.get("run") or ""
            if "deploy-bypass.sh" not in run:
                continue
            name = step.get("name") or f"unnamed step in job {job_name!r}"
            assert "GITHUB_STEP_SUMMARY" not in run, (
                f"step {name!r} must leave $GITHUB_STEP_SUMMARY to the helper"
            )
            env = step.get("env") or {}
            assert "DEPLOY_BYPASS_MARKER" in env, (
                f"step {name!r} in job {job_name!r} must bind "
                "DEPLOY_BYPASS_MARKER for the per-job audit"
            )
            markers.append(str(env["DEPLOY_BYPASS_MARKER"]))
        # The marker path is a WRITE in the report step and a READ in the job's
        # audit step, so the value must be ONE path per job. Asserting only that
        # each step BINDS the variable let a divergence survive: with the audit
        # pointed at another file it reads a marker nobody writes and states NOT
        # bypassed for a gate the report block says BYPASSED (mutation-verified
        # at this sha — `runner.temp}/deploy-bypass-audit.txt` left every #4759
        # case green).
        assert len(set(markers)) <= 1, (
            f"job {job_name!r} binds {sorted(set(markers))} for DEPLOY_BYPASS_MARKER — the "
            "report step writes this marker and the job's audit step reads it, so every "
            "deploy-bypass.sh step in a job MUST bind the same path"
        )


def test_deploy_jobs_audit_every_bypassable_gate():
    """Each deploy job ends by stating every gate IT OBSERVES as bypassed or not.

    Derived, not restated: a gate is audited by the job whose report site
    observes it (the pack-smoke gate is reported in ``deploy-api`` because the
    skipped ``packaging-smoke`` job cannot run its own audit). Every ``--state``
    entry is validated PER SITE — its ``<in>`` half must read that gate's input,
    its ``<var>`` half that gate's variable — so swapping two gates between jobs
    (the #4538 class a flattened union could not see) fails here.
    """
    by_var, by_input = _gates_by_var(), _gates_by_input()
    report_job = {key: job for job, _s, keys, _r in _sites("report", "key") for key in keys}
    audited: dict[str, str] = {}
    for job_name, step, specs, run in _sites("audit", "state"):
        assert step.get("if") == "always()", f"{job_name} audit must run on failure too"
        assert not re.search(r"\$\{\{\s*vars\.", run), (
            "audit values must be env-bound, not interpolated"
        )
        assert len(_flag_args(run, "job")) == 1, f"audit in {job_name} must name exactly one --job"
        keys = []
        for spec in specs:
            key, eq, rhs = spec.partition("=")
            in_half, slash, var_half = rhs.partition("/")
            assert eq and slash and key in by_var, f"malformed/undisclosed --state {spec!r}"
            keys.append(key)
            _assert_lane(step, in_half, "inputs", by_var[key][0], f"--state {spec!r} input half")
            _assert_lane(step, var_half, "vars", key, f"--state {spec!r} variable half")
        _assert_step_names_only(step, set(keys), by_var, by_input)
        for key in keys:
            assert key not in audited, f"{key} is audited twice ({audited[key]} and {job_name})"
            audited[key] = job_name
    assert set(audited) == set(by_var), (
        f"every bypassable gate must be audited exactly once; audited "
        f"{sorted(audited)}, declared {sorted(by_var)}"
    )
    for key, job in report_job.items():
        assert audited[key] == job, (
            f"{key} is reported in {job!r} but audited in {audited[key]!r} — a job's audit "
            "must state the gates its own job observes"
        )


def test_skip_bypass_inputs_are_re_armed_by_default():
    """#4605 — every `skip-*` dispatch input defaults to `false`, and the input
    set is EXACTLY the declared gate set.

    A bypass is an incident-window STATE — set per run with the input, or per
    window with the `SKIP_*` repo variable (docs/infra-runbook.md §8.2) — never
    a committed default. `skip-db-health-gate` shipped `default: 'true'` as the
    RC3 FalkorDB-restore mitigation and so skipped the release-health
    verification on EVERY manual dispatch; #4605 re-armed it once the data plane
    was healthy, which is why the incident default must not be "restored".

    The old guard asserted only that the four known names were PRESENT (a
    subset), so a FIFTH bypassable gate could be added with no report, audit or
    expiry wiring and it stayed green (round-3). SET EQUALITY closes both
    directions.
    """
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare `on:` key as the boolean True (YAML 1.1 truthiness).
    on = doc.get("on") or doc.get(True)
    inputs = on["workflow_dispatch"]["inputs"]
    skips = {name: spec for name, spec in inputs.items() if name.startswith("skip-")}
    by_input = _gates_by_input()
    assert set(skips) == set(by_input), (
        "the workflow_dispatch skip-* inputs and deploy-bypass.sh's GATES table "
        "must be the SAME set — a bypassable gate added on either side alone is "
        "the unguarded-gate gap. "
        f"workflow-only: {sorted(set(skips) - set(by_input))}; "
        f"table-only: {sorted(set(by_input) - set(skips))}"
    )
    for name, spec in skips.items():
        assert spec.get("type") == "boolean", f"{name} must stay a boolean input"
        assert str(spec.get("default")).lower() == "false", (
            f"{name} defaults to {spec.get('default')!r} — a bypass must be an "
            "incident-window state (docs/infra-runbook.md §8.2), never the default"
        )


def test_bypass_expiry_is_machine_checked():
    """The ``vars.SKIP_*`` window is checked by a MACHINE, daily (#4759).

    The window start is the dated companion ``SKIP_<VAR>_SET_AT`` read through
    the ordinary ``vars`` context — `gh variable list --json updatedAt` is not
    usable with the workflow token (the endpoint needs the fine-grained
    "Variables" permission) and `vars` exposes no `updatedAt`. The check is a
    SEPARATE scheduled workflow, so a red run can never block a deploy.
    """
    assert _EXPIRY_WORKFLOW.is_file(), f"missing scheduled check: {_EXPIRY_WORKFLOW}"
    doc = yaml.safe_load(_EXPIRY_WORKFLOW.read_text(encoding="utf-8"))
    on = doc.get("on") or doc.get(True)
    assert [entry["cron"] for entry in on["schedule"]], (
        "the bypass-expiry check must be SCHEDULED, not dispatch-only"
    )
    # SET EQUALITY, both lanes and both companions: a name-keyed lookup would
    # cover a gate whose binding was dropped or typo'd, and a containment check
    # would let a fifth gate be declared with no binding at all — never aged.
    expected = {}
    for key, *_ in _bypass_gates():
        expected[key] = f"${{{{ vars.{key} }}}}"
        expected[f"{key}{_SET_AT}"] = f"${{{{ vars.{key}{_SET_AT} }}}}"
    env = dict(doc["jobs"]["check"]["env"])
    assert env == expected, (
        "skip-bypass-expiry must bind EXACTLY the declared lanes and their dated "
        "companions (an extra or missing binding means a lane is aged by name but "
        "not by state, or a fifth gate is never aged at all). "
        f"extra: {sorted(set(env) - set(expected))}; missing: {sorted(set(expected) - set(env))}"
    )
    runs = " ".join((s.get("run") or "") for s in doc["jobs"]["check"]["steps"])
    assert "deploy-bypass.sh expiry" in runs, "the job must run the machine check"

    # The window itself is stated ONCE, in the helper, and pinned here.
    m = re.search(r"^WINDOW_DAYS_DEFAULT=(\d+)$", _BYPASS_SCRIPT.read_text(encoding="utf-8"), re.M)
    assert m, "WINDOW_DAYS_DEFAULT must be a literal in deploy-bypass.sh"
    assert m.group(1) == "7", (
        "the bypass window is 7 days — changing it is a deliberate act, so this pin "
        "must be updated with the justification, not silently re-tuned"
    )
    # ...and it can never block a deploy: the deploy workflow does not reference
    # it as a dependency. A prose mention is fine (it documents the check); a
    # `needs:`/`uses:`/`workflow_run` wiring would not be.
    deploy_body = "\n".join(
        line for line in _WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "skip-bypass-expiry" not in deploy_body, (
        "the expiry check must never be wired into the deploy workflow"
    )
    assert "workflow_run" not in deploy_body, (
        "deploy-hosted must not be triggered by another workflow"
    )


@pytest.mark.parametrize("key", [v for v, _i, _l, k in _bypass_gates() if k == "wrapper"])
def test_exit2_still_cannot_be_bypassed_and_precedes_the_skip(key):
    """``exit 2`` (could-not-determine) is never bypassable, and stays FIRST.

    The wrapper gates translate ONLY the checker's exit 1. This pins the order —
    the exit-2 branch must be tested before the skip branch — and that the exit-2
    branch consults no skip lane at all. Derived from the declaration, so a fifth
    wrapper gate forces the same check. #4759's changes are reporting-only and
    must not have weakened this.
    """
    sites = [(step, run) for _job, step, keys, run in _sites("report", "key") if keys == [key]]
    assert len(sites) == 1, f"{key}: expected exactly one report site, got {len(sites)}"
    run = sites[0][1]
    i2 = run.index('if [ "$RC" -eq 2 ]')
    i1 = run.index('if [ "$RC" -eq 1 ]')
    assert i2 < i1, "the exit-2 branch must be handled BEFORE the bypass branch"
    exit2 = run[i2:i1]
    assert "exit 2" in exit2, "the exit-2 branch must still block the deploy"
    assert "skip-" not in exit2 and "SKIP_" not in exit2, (
        "the exit-2 (could-not-determine) branch must never consult a skip lane"
    )
    assert key in run[i1:], "the bypass branch must still require this gate's lane"


def test_bypass_lanes_are_exactly_the_declared_pairs():
    """Every lane condition pairs a gate's input WITH ITS OWN variable, in one of
    the two allowed shapes — and the dated companion never gates (#4759).

    Derived from the declaration, so a fifth gate forces its lane condition and a
    widened condition (`… || true`) or a cross-wired pair (A's input with B's
    variable) fails. Replaces the previous verbatim four-condition pin, which
    could not cover a gate it did not name.
    """
    by_var, by_input = _gates_by_var(), _gates_by_input()
    seen: dict[str, set[str]] = {var: set() for var in by_var}
    for raw in _ACTION_EXPR.findall(_WORKFLOW.read_text(encoding="utf-8")):
        expr = raw.strip()
        vars_ = {v for tok in _GATE_VAR.findall(expr) if (v := _gate_of_var(tok, by_var))}
        ins = {v for tok in _GATE_IN.findall(expr) if (v := by_input.get(tok))}
        if not vars_ and not ins:
            continue
        assert len(vars_ | ins) <= 1, (
            f"lane expression {expr!r} names more than one gate — a cross-wired lane"
        )
        gate = next(iter(vars_ | ins))
        inp = by_var[gate][0]
        pos = f"inputs.{inp} || vars.{gate} == 'true'"
        neg = f"! (inputs.{inp} || vars.{gate} == 'true')"
        if vars_ and ins:
            assert expr in (pos, neg), (
                f"lane expression {expr!r} is neither {pos!r} nor {neg!r} — a widened "
                "or reworded bypass condition"
            )
            seen[gate].add("pos" if expr == pos else "neg")
        elif vars_:
            # A window-start read, or the lane's own `== 'true'` test. The date
            # companion must never appear in a condition.
            assert expr in (f"vars.{gate}", f"vars.{gate}{_SET_AT}", f"vars.{gate} == 'true'"), (
                f"expression {expr!r} reads {gate} in a form that is neither a plain "
                "data read nor the `== 'true'` lane test — `_SET_AT` must never "
                "enable a bypass"
            )
        else:
            assert expr in (f"inputs.{inp}", f"inputs.{inp} || false"), (
                f"expression {expr!r} reads input {inp} in an unexpected form"
            )
    for var, _inp, _label, kind in _bypass_gates():
        assert "pos" in seen[var], f"{var} has no positive lane condition"
        if kind == "if":
            assert "neg" in seen[var], (
                f"{var} is an `if`-kind gate and needs its negated guard (the step/job "
                "`if:` that skips the whole gate)"
            )


def test_bypass_values_are_env_bound_never_interpolated_into_shell():
    """#4334 discipline: a `vars` VALUE is never substituted into shell source.

    A repo variable is operator-controlled, so `${{ vars.X }}` inside run text
    would be re-parsed by bash — a value containing `$(…)` would EXECUTE and one
    containing `"` would lose its quoting, the exact class of the STRIPE_PRICE_IDS
    outage. The bypass steps bind each value in `env:` and read it as a quoted
    shell variable. (A COMPARISON, `${{ vars.X == 'true' }}`, is evaluated by the
    Actions expression engine to a literal `true`/`false` before bash sees it, so
    it is not interpolation of the value and is allowed.)
    """
    for name, step in _steps().items():
        run = step.get("run") or ""
        if "deploy-bypass.sh" not in run:
            continue
        # A bare value interpolation (`${{ vars.X }}`) re-parses the operator's
        # value as shell source. A COMPARISON (`${{ vars.X == 'true' }}`) is
        # evaluated by the Actions expression engine to a literal true/false and
        # is safe — that is the pre-existing lane-condition form.
        hit = re.search(r"\$\{\{\s*vars\.[A-Z0-9_]+\s*\}\}", run)
        assert hit is None, (
            f"step {name!r} interpolates a vars VALUE into its run text ({hit.group(0)!r})"
        )
        # Every `--flag` argument that names a shell variable is checked for its
        # env binding (and the gate it belongs to) by `_lane` / `_assert_lane` —
        # the generic per-site association check, of which the old `*_SET_AT`-only
        # loop was one case.


# ── #2240: the deploy job's out-of-band failure alert ──────────────────────
#
# `deploy-api` had NO failure observability: no `if: failure()` step anywhere in
# the workflow, `post-deploy-verify` skipped exactly when the deploy fails, the
# availability watchdog reads a stale build as UP (a stale build still answers
# 401), and `deploy-api` is not a required context. Measured cost: 4.6 days of a
# dead deploy lane (2026-08-30 → 09-04, 22 runs) with zero notifications, and
# again ~2 days (2026-09-29 → 10-01).
#
# These three guards are deliberately DERIVED, never restated: the step-id map
# lives in the alert script and the labels live in the workflow, so a rename on
# either side has to fail here rather than silently file an alert under a stale
# key (or a key with no step behind it).

# Three alert sites, one script. They are mutually exclusive at runtime —
# `deploy-api` is gated on `packaging-smoke`'s result and `post-deploy-verify` on
# `deploy-api`'s, so a skipped job cannot also fail — which is exactly why a
# notifier in `deploy-api` alone would have left a failed PACK SMOKE (which
# silently blocks the whole deploy) unobserved.
_ALERT_SITE_PREFIX = "Alert on failure (out-of-band)"
# #8841: the ledger's other half — one resolve site per alert site, in the SAME
# job, immediately BEFORE its alert step.
_RESOLVE_SITE_PREFIX = "Resolve recovered alerts (out-of-band)"
_ALERT_JOBS = ("packaging-smoke", "deploy-api", "post-deploy-verify")
_ALERT_SCRIPT = (
    Path(__file__).resolve().parent.parent / ".github" / "scripts" / "deploy-api-alert.sh"
)

# Jobs that may fail alone WITHOUT an out-of-band alert, each with the reason.
# Empty on purpose: every job here can fail alone and block the deploy, so every
# job ends with the alert. A new job must therefore either carry the alert or be
# added here WITH a reason — the point is that adding a silent job is a decision
# someone makes, not an oversight (round-3 review, P2-4).
_ALERT_EXEMPT_JOBS: dict[str, str] = {}


def _shell_code(run: str) -> str:
    """`run` with comment-only lines dropped.

    A guard must read the COMMAND, not the prose around it: a body whose only
    `set -o pipefail` is inside a comment (or on a commented-out branch) is not
    armed, and would otherwise satisfy an `in run` assertion silently.
    """
    return "\n".join(
        line for line in run.splitlines() if not line.lstrip().startswith("#")
    )


def _alert_script_text() -> str:
    assert _ALERT_SCRIPT.is_file(), f"alert script not found: {_ALERT_SCRIPT}"
    return _ALERT_SCRIPT.read_text(encoding="utf-8")


def _step_label_map() -> dict[str, str]:
    """`id -> human label` as the ALERT SCRIPT declares it (the source of truth)."""
    text = _alert_script_text()
    body = _region(text, "step_label() {", "\n}\n")
    return dict(re.findall(r"^\s{4}([a-z][a-z-]*)\)\s+printf '%s' '(.*)' ;;$", body, re.M))


def _job_note_map() -> dict[str, str]:
    """`job -> what a red in it means`, as the alert script declares it."""
    body = _region(_alert_script_text(), "job_block_note() {", "\n}\n")
    return {m: "" for m in re.findall(r"^\s{4}([a-z][a-z-]*)\)$", body, re.M)}


def _alert_sites() -> list[tuple[str, str, dict]]:
    """`(job, step-name, step)` for every out-of-band alert step in the workflow."""
    return _sites_with_prefix(_ALERT_SITE_PREFIX)


def _resolve_sites() -> list[tuple[str, str, dict]]:
    """`(job, step-name, step)` for every recover-and-resolve step (#8841)."""
    return _sites_with_prefix(_RESOLVE_SITE_PREFIX)


def _sites_with_prefix(prefix: str) -> list[tuple[str, str, dict]]:
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    sites = []
    for job_name, job in doc["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("name", "")).startswith(prefix):
                sites.append((job_name, step["name"], step))
    return sites


def _workflow_step_ids() -> dict[str, str]:
    """`id -> name` for every step in the workflow that declares an `id`."""
    return {s["id"]: n for n, s in _steps().items() if "id" in s}


def _alert_passed_ids(run: str) -> list[str]:
    """The step ids the alert step passes, in order.

    Parsed as `<id>=${{ … }}` pairs, NOT by whitespace: the outcome interpolation
    contains spaces (`${{ steps.drift.outcome }}`), so a naive `.split()` reads
    `steps.drift.outcome` and `}}` as ids. Pinning the `${{` shape here is also
    what the `steps.<id>.outcome` assertion below relies on.
    """
    m = re.search(r"--steps\s+\"([^\"]*)\"", run)
    assert m, "the alert step must pass --steps (the per-step attribution)"
    return re.findall(r"([A-Za-z][\w-]*)=\$\{\{", m.group(1))


def test_failed_deploy_jobs_alert_out_of_band():
    """#2240 — every job of `deploy-hosted` that can fail alone ends with an alert.

    The three things that must hold TOGETHER at EVERY site, and each of which
    alone is insufficient: the step exists on `failure()`; the job may actually
    write the issue (`issues: write` + the spelled-out `contents: read`, because
    declaring ANY permission sets the unspelled ones to `none`); and it uses the
    ACTIONS token, never a PAT — a PAT writes as its own owner, the dedupe search
    keys on `author:app/github-actions`, and every failing run would file a
    duplicate (#2706), silently.

    The three SITES matter as much as the mechanism: `packaging-smoke` and
    `post-deploy-verify` each fail while the other jobs are SKIPPED, so a
    notifier only inside `deploy-api` would leave a failed pack smoke — which
    silently blocks the whole deploy — exactly as unobserved as the 4.6-day
    stall this issue is about.
    """
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    sites = _alert_sites()
    assert {job for job, _n, _s in sites} == set(_ALERT_JOBS), (
        f"every job that can fail while its siblings are skipped must carry the "
        f"alert; got {sorted(job for job, _n, _s in sites)}, want {sorted(_ALERT_JOBS)}"
    )
    # DERIVED from the workflow, not restated: every job of this workflow must
    # either end with the alert or be an explicit, reasoned exemption. Without
    # this, a 4th job added tomorrow can fail alone in total silence — which is
    # the defect #2240 exists to remove (round-3 review, P2-4).
    assert set(_jobs()) == set(_ALERT_JOBS) | set(_ALERT_EXEMPT_JOBS), (
        f"deploy-hosted jobs {sorted(_jobs())} != alert sites "
        f"{sorted(_ALERT_JOBS)} + exemptions {sorted(_ALERT_EXEMPT_JOBS)} — a new "
        f"job must either carry the out-of-band alert or be added to "
        f"_ALERT_EXEMPT_JOBS with a reason"
    )
    for job_name, step_name, step in sites:
        assert step.get("if") == "failure()", (
            f"{step_name!r} must be `if: failure()` — not `always()` (which would "
            f"fire on cancelled and green runs) and not a bare step (which is "
            f"ANDed with success())"
        )
        run = step.get("run", "")
        # The EXACT path, not a bare basename: `bash scripts/deploy-api-alert.sh`
        # would resolve through the `scripts/` symlink, which does not exist on a
        # fresh runner — the step would die with "No such file" and the alert would
        # be silently absent.
        assert "bash .github/scripts/deploy-api-alert.sh" in run, (
            f"{step_name!r} must invoke `bash .github/scripts/deploy-api-alert.sh` "
            f"by its exact path (a symlinked or basename form is not on the runner)"
        )
        # `--job` must name THIS job: it is half of the dedupe key AND it selects
        # the "what a red here means" sentence, so a copy-paste neighbour would
        # file a pack-smoke failure under `deploy-api`'s key and tell the reader
        # the wrong consequence.
        m = re.search(r"--job\s+(\S+)", run)
        assert m and m.group(1) == job_name, (
            f"{step_name!r} passes --job {m.group(1) if m else '<none>'!r}, but it lives "
            f"in job {job_name!r} — the key and the consequence sentence would be "
            f"another job's"
        )

        perms = doc["jobs"][job_name].get("permissions") or {}
        assert perms.get("issues") == "write", (
            f"{job_name} must declare `issues: write` — the repo's default "
            f"workflow permission is read, and the substrate files an issue"
        )
        assert perms.get("contents") == "read", (
            f"{job_name}: `contents: read` must be spelled out — specifying ANY "
            f"permission sets every unspecified one to `none`, which would strip "
            f"the token actions/checkout and the job's `gh` reads rely on"
        )
        env = step.get("env") or {}
        assert env.get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}", (
            f"{job_name}: the alert must write with the ACTIONS token. A PAT writes "
            f"as its own owner, so the `author:app/github-actions` dedupe search "
            f"never matches and EVERY failing run files a fresh duplicate (#2706)"
        )
        # The page leg: dropping these bindings would leave the issue as the only
        # signal, and an issue alone has demonstrably not reached a human here (the
        # watchdog records 11h19m of silence with one incident issue).
        for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            assert env.get(var) == f"${{{{ secrets.{var} }}}}", (
                f"{step_name!r} must bind {var} (same-named secret): without it the "
                f"page leg silently disappears and the issue is the only signal"
            )

    # ORDERING: the alert is the job's LAST step, so a failure of the `if:
    # always()` gate audit is reported too. An alert placed before the audit can
    # only see failures that already happened — an audit-only failure would then
    # be a red run with no issue and no page, the exact shape #2240 removes.
    for job_name, step_name, _step in sites:
        names = [s.get("name") or "" for s in doc["jobs"][job_name]["steps"]]
        assert names[-1] == step_name, (
            f"{step_name!r} must be the LAST step of {job_name} (it is at index "
            f"{names.index(step_name)} of {len(names)}) — otherwise a failure of a "
            f"later `if: always()` step is never reported"
        )


def test_alert_step_ids_match_the_script_and_the_workflow():
    """The `--steps` ids, the script's map and the workflow's `id:`s agree EXACTLY.

    Three surfaces have to line up for per-step attribution to work: each site
    passes `id=${{ steps.<id>.outcome }}`, the script maps `<id>` to a human label,
    and the step carrying `<id>` has that `name`. A rename on any side would
    otherwise let the alert file under a key with no step behind it (or attribute
    a failure to the wrong step), and both directions are checked here — over the
    UNION of the sites, so dropping a site's id list is caught too.
    """
    declared = _step_label_map()
    wf_ids = _workflow_step_ids()
    passed: list[str] = []
    for _job, step_name, step in _alert_sites():
        run = step.get("run", "")
        site_ids = _alert_passed_ids(run)
        assert site_ids, f"{step_name!r} must pass --steps (the per-step attribution)"
        assert len(site_ids) == len(set(site_ids)), f"duplicate ids in --steps: {site_ids}"
        # The outcome interpolation must be `steps.<id>.outcome` for the SAME id —
        # not a copy-pasted neighbour, which would attribute every failure to one
        # step.
        for sid in site_ids:
            assert f"steps.{sid}.outcome" in run, (
                f"{step_name!r} passes {sid!r} but never reads `steps.{sid}.outcome`"
            )
        passed.extend(site_ids)

    assert len(passed) == len(set(passed)), f"an id is passed by two sites: {passed}"
    assert set(passed) == set(declared), (
        f"--steps and step_label() disagree — passed-not-declared: "
        f"{sorted(set(passed) - set(declared))}, declared-not-passed: "
        f"{sorted(set(declared) - set(passed))}"
    )
    assert set(passed) == set(wf_ids), (
        f"the workflow's `id:` set and the sites' `--steps` set must be EQUAL in "
        f"both directions — passed-not-declared: {sorted(set(passed) - set(wf_ids))}, "
        f"declared-not-passed: {sorted(set(wf_ids) - set(passed))}. A new `id:` on an "
        f"alert-job step that is not added to that site's `--steps` would file under "
        f"the job-level key with 'failed step: not reported' — silently losing the "
        f"per-step attribution this change exists for (round-5 review, P3-3)"
    )
    for sid in passed:
        assert declared[sid] == wf_ids[sid], (
            f"step {sid!r}: the script labels it {declared[sid]!r} but the workflow "
            f"names that step {wf_ids[sid]!r} — the alert would name a step that "
            f"does not exist"
        )


def test_recovered_deploy_alerts_are_resolved_out_of_band():
    """#8841 — every alert site is RETIRED when its condition clears.

    Filing without resolving is a ledger that can only grow. Measured on this
    workflow's own `drift` key (#7634, filed 2026-10-07T20:11Z): **21 runs then
    SUCCEEDED** (2026-10-09T23:20Z … 2026-10-11T00:36Z) and the issue was still
    OPEN ten days later carrying 238 `Recurrence` comments — for that window the
    standing alert claimed a condition that was no longer true, and each later
    recurrence read as noise. Nothing mechanical ever ended a streak; the body
    only ever told a human to.

    So the resolve leg is pinned here exactly as the alert leg is: one site per
    alert site, same job, the SAME per-step map (a key cannot be alerted on that
    cannot be retired), the job-level key armed only by `job.status`, and
    `always() && !cancelled()` so a partially red run still retires the steps
    that passed.
    """
    sites = _resolve_sites()
    assert {job for job, _n, _s in sites} == set(_ALERT_JOBS), (
        f"every alert site needs a resolve site in the same job (an alert that is "
        f"never retired is the stale-ledger defect #8841); got "
        f"{sorted(job for job, _n, _s in sites)}, want {sorted(_ALERT_JOBS)}"
    )

    alert_sites = {job: step for job, _n, step in _alert_sites()}
    for job_name, step_name, step in sites:
        # ORDER: resolved BEFORE the alert, so the alert stays the job's LAST step
        # (`test_failed_deploy_jobs_alert_out_of_band` pins that, and it must stay
        # true: on a red run the alert is the last act, not bookkeeping).
        names = [s.get("name") or "" for s in _jobs()[job_name]]
        assert names.index(step_name) + 1 == names.index(alert_sites[job_name].get("name")), (
            f"{step_name!r} must immediately PRECEDE {job_name}'s alert step — the "
            f"alert has to remain the job's last act (a later `if: always()` failure "
            f"would otherwise be reported by nothing)"
        )

        cond = str(step.get("if") or "")
        assert "always()" in cond and "cancelled()" in cond, (
            f"{step_name!r} must run on `always() && !cancelled()`: on a partially "
            f"red run an EARLIER step that passed should still retire its stale "
            f"alert, while a cancel observed nothing (got {cond!r})"
        )

        run = step.get("run", "")
        assert "bash .github/scripts/deploy-api-alert.sh" in run, (
            f"{step_name!r} must invoke the alert script by its exact path (a "
            f"basename/symlinked form is not on a fresh runner)"
        )
        assert "--resolve" in run, f"{step_name!r} must pass --resolve (the retire mode)"

        m = re.search(r"--job\s+(\S+)", run)
        assert m and m.group(1) == job_name, (
            f"{step_name!r} passes --job {m.group(1) if m else '<none>'!r} but lives in "
            f"{job_name!r} — it would retire ANOTHER job's ledger keys, leaving its "
            f"own stale"
        )

        # `job.status` is the ONLY evidence that the whole job is green, and the
        # job-level fallback key (`… failed`, no step) must be retired on nothing
        # less: a single step's `success` says nothing about a job that died later.
        assert re.search(r'--job-status\s+"\$\{\{\s*job\.status\s*\}\}"', run), (
            f"{step_name!r} must pass --job-status \"${{{{ job.status }}}}\" — without it "
            f"the job-level failure key can never be retired, and a stale job-level "
            f"alert outlives the fault"
        )

        env = step.get("env") or {}
        assert env.get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}", (
            f"{step_name!r} must close with the ACTIONS token: the ledger lookup keys "
            f"on `author:app/github-actions`, so a PAT would find nothing and retire "
            f"nothing (the #2706 trap, inverted)"
        )

        # PARITY: the resolve site reads the SAME `id=outcome` map as the alert.
        # Any drift means a key can be filed and never retired (or vice versa).
        assert _alert_passed_ids(run) == _alert_passed_ids(alert_sites[job_name].get("run", "")), (
            f"{step_name!r} and {job_name}'s alert step must pass the SAME --steps ids "
            f"in the same order — a key the alert files must be one the resolver can "
            f"retire"
        )


def test_alert_job_notes_cover_every_alert_site():
    """The script's per-job "what a red here means" table covers exactly the sites.

    The three sites are NOT interchangeable: a failed `deploy-api` means nothing
    shipped, a failed `packaging-smoke` means the deploy was SKIPPED, and a failed
    `post-deploy-verify` means the release is LIVE and unhealthy. A single
    hard-coded sentence would be wrong at two of the three, and a stale entry
    after a job rename would be wrong silently — so the set is pinned both ways.
    """
    notes = _job_note_map()
    site_jobs = {job for job, _n, _s in _alert_sites()}
    assert site_jobs == set(notes), (
        f"job_block_note() and the alert sites disagree — sites-not-noted: "
        f"{sorted(site_jobs - set(notes))}, noted-not-a-site: {sorted(set(notes) - site_jobs)}"
    )
    assert site_jobs == set(_ALERT_JOBS), (
        f"the alert sites moved: {sorted(site_jobs)} vs {sorted(_ALERT_JOBS)}"
    )


def test_drift_report_seam_carries_the_gates_own_report():
    """The drift case must carry the gate's OWN output, not a second reading.

    The gate already prints the blocking versions, the OUT-OF-ORDER subset and the
    ordered remediation; re-running it from the alert could name a DIFFERENT
    blocking set than the run being reported. So the gate's stdout is teed to a
    file in the SAME step, and the alert is handed that path.
    """
    steps = _steps()
    drift = steps["Check migration drift (fail-closed)"]
    alert = next(s for _j, n, s in _alert_sites() if n.endswith("deploy-api (#2240)"))
    run = drift.get("run", "")

    assert drift.get("id") == "drift", "the drift step needs `id: drift` for attribution"
    assert "check-migration-drift" in run, "the drift step must still run the gate"
    assert "tee" in run and "DRIFT_REPORT_FILE" in run, (
        "the gate's stdout must be teed to $DRIFT_REPORT_FILE so the alert carries "
        "the report the RUN produced"
    )
    # `set -o pipefail` is what makes the STEP fail when the gate fails: without it
    # the step's status is `tee`'s (0), so a blocked deploy reads GREEN and the
    # alert never fires — a fail-OPEN, not merely a silence.
    #
    # ⛔ Pinned as the FIRST EXECUTABLE LINE, not as a substring. `re.search` takes
    # the first match, so ANY non-executed occurrence ahead of the pipeline — a
    # trailing comment (`true  # set -o pipefail`), a heredoc body, `echo "set -o
    # pipefail"` — satisfied the substring form while pipefail was NOT armed
    # (round-4 review, P2-2). Requiring the arming to be the first line of code
    # also subsumes the ordering check: nothing can precede it, the pipeline
    # included.
    code = _shell_code(run)
    first = next((line.strip() for line in code.splitlines() if line.strip()), "")
    assert first == "set -o pipefail", (
        f"the drift step's FIRST executable line must be exactly `set -o pipefail` "
        f"(found {first!r}) — without it armed first the step's status is tee's "
        f"(0) and a blocked deploy reads GREEN. A comment, a heredoc body or an "
        f"echo does not arm it."
    )
    assert not re.search(r"set\s+\+o\s+pipefail", code), (
        "`set +o pipefail` would disarm the seam it exists to arm"
    )

    drift_path = (drift.get("env") or {}).get("DRIFT_REPORT_FILE")
    alert_path = (alert.get("env") or {}).get("DRIFT_REPORT_FILE")
    assert drift_path and alert_path, "both steps must bind DRIFT_REPORT_FILE"
    assert drift_path == alert_path, (
        f"the alert reads {alert_path!r} but the gate writes {drift_path!r} — the "
        f"report would silently never reach the issue"
    )
    assert "--drift-report" in alert.get("run", ""), (
        "the alert step must pass the report path as --drift-report"
    )


# ═══════════════════════════════════════════════════════════════════════════
# The billing-reconcile driver's VERDICT table (#8878)
# ═══════════════════════════════════════════════════════════════════════════
#
# `billing-reconcile-cron.yml` is the only thing that compares a paying org's
# stored tier against Stripe on a cadence, and its EXIT CODE is the page. Its
# verdicts were documented in a header comment and verified by hand, which made
# them exactly as durable as the comment: a later edit could turn "a billed org
# failed to reconcile" into a green run with every required check still green —
# a false PASS in the one component whose whole job is to alert.
#
# So the table is asserted here, by RUNNING the step's own `run:` script against
# a stub server that returns each response shape. Deliberately in this file (the
# repo's workflow-semantics home, already collected by CI) rather than a new
# harness surface: no new required check, no .mergify change.

_BILLING_WORKFLOW = (
    Path(__file__).resolve().parent.parent
    / ".github" / "workflows" / "billing-reconcile-cron.yml"
)


def _billing_steps() -> dict[str, dict]:
    doc = yaml.safe_load(_BILLING_WORKFLOW.read_text(encoding="utf-8"))
    steps: dict[str, dict] = {}
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name"):
                steps[step["name"]] = step
    return steps


_CLEAN = {
    "checked": 1, "repaired": 0, "unchanged": 1, "unverified": 0,
    "skipped": 0, "drift": [], "unrepaired": [], "failed": [],
}


def _billing_case(**overrides):
    payload = dict(_CLEAN)
    payload.update(overrides)
    return payload


#: (case, http status, body, expected exit code, why, trigger)
#:
#: The TRIGGER is part of a 202's verdict, not decoration: the endpoint answers
#: 202 `already_running` for a concurrent pass, which its docstring calls benign
#: (nothing queues; the next scheduled run retries). A SCHEDULED run that lands
#: inside an operator's manual pass is therefore a no-op, not an incident — while
#: a `workflow_dispatch` that did no work must not look like a completed pass.
#: Default trigger is `schedule` (the cadence this job exists for).
_BILLING_VERDICTS = [
    ("clean", 200, _billing_case(), 0,
     "a clean pass is green"),
    ("drift-down", 200, _billing_case(
        repaired=1, unchanged=0,
        drift=[{"org_id": "o1", "direction": "down"}]), 0,
     "a late downgrade is Stripe truth arriving late, not a customer short-changed"),
    ("drift-unrankable", 200, _billing_case(
        repaired=1, unchanged=0,
        drift=[{"org_id": "o2", "direction": None}]), 0,
     "an unrankable pair is not provably an upgrade, so it must never page"),
    ("drift-up", 200, _billing_case(
        repaired=1, unchanged=0,
        drift=[{"org_id": "o3", "direction": "up"}]), 1,
     "a paying org WAS under-delivered — this is #8878's own symptom"),
    ("failed", 200, _billing_case(
        unchanged=0,
        failed=[{"org_id": "o4", "error": "StripeAPIError: boom"}]), 1,
     "a billed org could not be reconciled; a pass that verified nothing is not clean"),
    ("unrepaired", 200, _billing_case(
        unchanged=0,
        unrepaired=[{"org_id": "o5", "expected_tier": "pro"}]), 1,
     "a read-only reconciler is a BROKEN reconciler"),
    ("renamed-field", 200,
     {k: v for k, v in _CLEAN.items() if k != "unverified"}, 1,
     "a response that omits a summary field must fail closed, never read as clean"),
    ("not-json", 200, "<html>502 Bad Gateway</html>", 1,
     "a body that is not a summary cannot verify anything"),
    ("http-500", 500, {"detail": "boom"}, 1,
     "a non-200 is a failed pass"),
    ("already-running-scheduled", 202, {"status": "already_running"}, 0,
     "a scheduled run overlapping a manual pass is benign — nothing queues and "
     "the next scheduled run retries; paging it would be a false billing incident"),
    ("already-running-dispatched", 202, {"status": "already_running"}, 1,
     "a DISPATCHED run that did NO work must not read as a completed pass",
     "workflow_dispatch"),
    ("enumeration-incomplete", 503,
     {"status": "enumeration_incomplete", "detail": "not certified"}, 1,
     "a pass that could not certify the whole fleet compared an unknown "
     "fraction of it — never a clean pass"),
]

#: Every field the verdict keys on. Shared by the workflow's check and the
#: rename case above, so adding a field cannot leave one of them behind.
_BILLING_SUMMARY_FIELDS = (
    "checked", "repaired", "unchanged", "unverified", "skipped",
    "drift", "unrepaired", "failed",
)


class TestBillingReconcileDriverVerdicts:
    """#8878: the driver's exit code IS the page — pin every verdict."""

    def test_the_verdict_table_is_complete(self):
        """A response missing ANY field the verdict reads must be a failure.

        Guards the guard: if the workflow stopped requiring a field, the
        ``renamed-field`` case below could still exit non-zero by accident (a
        later ``data[...]`` raises KeyError) while the run's own message blamed
        the wrong thing. So the contract is parsed from the ``required = {...}``
        literal rather than grepped for — a bare substring search also matches
        the unrelated ``if data["unverified"]:`` line and would pass while the
        field had been dropped from the required set.
        """
        script = _billing_steps()["Run billing reconcile pass"]["run"]
        match = re.search(r"required\s*=\s*\{(.*?)\}", script, re.S)
        assert match is not None, (
            "the driver no longer states a required-field set — a summary that "
            "omits a field would read as a clean pass"
        )
        declared = set(re.findall(r'"([a-z_]+)"', match.group(1)))
        assert declared == set(_BILLING_SUMMARY_FIELDS), (
            f"the driver requires {sorted(declared)} but its verdict reads "
            f"{sorted(_BILLING_SUMMARY_FIELDS)} — a summary omitting "
            f"{sorted(set(_BILLING_SUMMARY_FIELDS) - declared)} would read as clean"
        )

    @pytest.mark.parametrize(
        "case,status,body,expected,why,trigger",
        [(*case, "schedule") if len(case) == 5 else case
         for case in _BILLING_VERDICTS],
        ids=[c[0] for c in _BILLING_VERDICTS],
    )
    def test_exit_code_matches_the_documented_verdict(
            self, case, status, body, expected, why, trigger):
        import subprocess
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        payload = body if isinstance(body, str) else json.dumps(body)

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload.encode())

            def log_message(self, *a):  # silence the default stderr trace
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            script = _billing_steps()["Run billing reconcile pass"]["run"]
            proc = subprocess.run(
                ["bash", "-c", script],
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "INTERNAL_API_URL": f"http://127.0.0.1:{port}",
                    "FASTAPI_INTERNAL_KEY": "test-internal-key",
                    "HOME": "/tmp",
                    # The workflow binds this from `${{ github.event_name }}`;
                    # the 202 verdict depends on it (see _BILLING_VERDICTS).
                    "EVENT_NAME": trigger,
                },
                capture_output=True, text=True, timeout=120,
            )
        finally:
            server.shutdown()
            server.server_close()

        assert proc.returncode == expected, (
            f"driver verdict for {case!r} was exit {proc.returncode}, expected "
            f"{expected} — {why}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    def test_the_shell_and_python_redactors_agree_on_one_corpus(self):
        """#8904 review round 7: two redactors, one rule — or they drift.

        The driver redacts the body in shell; the endpoint redacts the summary in
        python. A shell rule without the python one's trailing boundary guard
        ate the prefix of ordinary prose (``cus_balance_units`` became
        ``<stripe-id>_units``) inside the very text ``show_body`` exists to make
        readable — and nothing noticed, because only the python side was tested.
        """
        import subprocess

        from tortoise import billing as bl

        corpus = [
            # ordinary prose that only LOOKS like an id
            "cus_balance_units sub_balance_reported",
            # real ids, bare and inside provider prose
            "x cus_abc123def y sub_xyz789ghi z",
            "stripe GET customers/cus_9f8e7d6c5b4a/subscriptions -> 404",
            "no such customer: 'cus_9f8e7d6c5b4a'",
        ]
        script = _billing_steps()["Run billing reconcile pass"]["run"]
        redactor = script[script.index("redact_once() {"):script.index("show_body() {")]
        harness = (redactor +
                   '\nwhile IFS= read -r line; do printf "%s\\n" "$line" '
                   '| redact_stream; done\n')
        proc = subprocess.run(
            ["bash", "-c", harness], input="\n".join(corpus) + "\n",
            capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        shell_lines = proc.stdout.splitlines()
        assert len(shell_lines) == len(corpus), (
            f"the shell redactor changed the line count:\n{proc.stdout}")

        for line, shell_out in zip(corpus, shell_lines, strict=True):
            py_out = bl._scrub_secrets(line)
            if "_balance_" in line:
                # Both sides must leave ordinary prose ALONE.
                assert py_out == line, (
                    f"the python redactor mangled prose: {line!r} -> {py_out!r}")
                assert shell_out == line, (
                    "the shell redactor mangled ordinary prose the python one "
                    f"leaves intact: {line!r} -> {shell_out!r}")
                continue
            # Both sides must remove every real id.
            for fragment in ("cus_abc123def", "cus_9f8e7d6c5b4a",
                             "sub_xyz789ghi"):
                assert fragment not in py_out, (fragment, py_out)
                assert fragment not in shell_out, (fragment, shell_out)

    def test_an_unrecognised_503_cannot_forge_a_workflow_command(self):
        """#8904 review round 4: ``status`` is REMOTE data in a PUBLIC log.

        The unrecognised-cause arm prints the response body's ``status`` so an
        operator can see the shape of an unknown 503. Interpolated raw, a value
        carrying a newline emits a SECOND line, and a line beginning
        ``::error::`` is parsed by the runner as a workflow command — an
        injected annotation from a response body. The value is redacted and
        flattened before it is printed; this pins both halves.
        """
        import subprocess
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        payload = json.dumps({"status": "evil\n::error::FORGED"})

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload.encode())

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            script = _billing_steps()["Run billing reconcile pass"]["run"]
            proc = subprocess.run(
                ["bash", "-c", script],
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "INTERNAL_API_URL": f"http://127.0.0.1:{server.server_port}",
                    "FASTAPI_INTERNAL_KEY": "test-internal-key",
                    "HOME": "/tmp",
                    "EVENT_NAME": "workflow_dispatch",
                },
                capture_output=True, text=True, timeout=120,
            )
        finally:
            server.shutdown()
            server.server_close()

        assert proc.returncode == 1, (
            "an unrecognised 503 is a failed pass\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        lines = proc.stdout.splitlines()
        assert not any(ln.startswith("::error::FORGED") for ln in lines), (
            "a newline in the response body forged a workflow command:\n"
            f"{proc.stdout}")
        assert "UNRECOGNISED" in proc.stdout, (
            "the arm must still name the cause it could not match"
            f"\n{proc.stdout}")

    def test_the_public_log_never_carries_a_customer_org_id(self):
        """#8904 review round 3: this repository's Actions log is PUBLIC.

        A bare UUID shape matches NEITHER id this fleet mints — hosted orgs are
        ``uuid4().hex[:26]`` (26 hex, no dashes) and registry orgs are
        ``ulid()`` = ``<hex-ts>-<12hex>``. The ids reach the log inside the
        provider's own prose (``no organizations row matched id='<id>'``), not
        only in the ``org_id`` key, so a key-only filter publishes them anyway.
        Both real shapes are exercised, and the fingerprints must DIFFER —
        hashing a value an earlier redaction already replaced collapses every
        org onto one fingerprint and kills the correlation it exists for.
        """
        import subprocess
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        hosted = "f69f0773cfa54c62b900c13c6a"
        registry = "0006553f0ff-0123456789ab"
        # A registry id that is NOT any row's own ``org_id``: only the SHAPE
        # rules can catch this one, so it proves the net, not the by-value pass.
        other_ulid = "0006559a1ff-aaaabbbbcccc"
        payload = json.dumps(_billing_case(
            unchanged=0,
            failed=[
                {"org_id": hosted,
                 "error": "RuntimeError: update_org_billing: no organizations "
                          f"row matched id='{hosted}' (also saw {other_ulid})"},
                {"org_id": registry,
                 "error": f"BillingError: reconcile_org: org '{registry}' "
                          "not found"},
            ],
        ))

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload.encode())

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            script = _billing_steps()["Run billing reconcile pass"]["run"]
            proc = subprocess.run(
                ["bash", "-c", script],
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "INTERNAL_API_URL": f"http://127.0.0.1:{server.server_port}",
                    "FASTAPI_INTERNAL_KEY": "test-internal-key",
                    "HOME": "/tmp",
                    "EVENT_NAME": "schedule",
                },
                capture_output=True, text=True, timeout=120,
            )
        finally:
            server.shutdown()
            server.server_close()

        emitted = proc.stdout + proc.stderr
        assert proc.returncode == 1, f"a failed row must red the run: {emitted}"
        for label, raw in (("hosted", hosted), ("registry", registry),
                           ("other registry", other_ulid)):
            assert raw not in emitted, (
                f"the {label} org id reached the PUBLIC log unredacted:\n{emitted}"
            )
        fingerprints = set(re.findall(r"org-[0-9a-f]{10}", emitted))
        assert len(fingerprints) == 2, (
            "the two orgs must keep DISTINCT fingerprints, or the log cannot "
            "correlate a row across runs"
        )

    def test_a_body_cannot_forge_a_workflow_annotation(self):
        """#8904 review P2: a PAYLOAD line must not become a workflow command.

        ``show_body`` printed the response body line by line, so a body
        containing a newline followed by ``::error::`` emitted a real GitHub
        annotation from this arm — the payload writing the run's diagnostics.
        The text must still be shown (it is the operator's only evidence on a
        non-200), so the fix indents every line instead of stripping it; the
        test asserts the forged line is inert rather than absent.
        """
        import subprocess
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        forged = "::error::FORGED-BY-PAYLOAD billing is fine"
        # A RAW newline, NOT json.dumps: the encoder escapes a newline to the two
        # characters ``\n``, so an encoded payload cannot forge anything and this
        # test passed with AND without the fix (measured). The body is what the
        # driver prints, so it is passed through byte-faithfully here.
        payload = b"reconcile_stuck\n" + forged.encode() + b"\n"

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            script = _billing_steps()["Run billing reconcile pass"]["run"]
            proc = subprocess.run(
                ["bash", "-c", script],
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "INTERNAL_API_URL": f"http://127.0.0.1:{server.server_port}",
                    "FASTAPI_INTERNAL_KEY": "test-internal-key",
                    "HOME": "/tmp",
                    "EVENT_NAME": "schedule",
                },
                capture_output=True, text=True, timeout=120,
            )
        finally:
            server.shutdown()
            server.server_close()

        emitted = proc.stdout + proc.stderr
        assert "FORGED-BY-PAYLOAD" in emitted, (
            "the body text is the operator's evidence and must still be shown:\n"
            f"{emitted}")
        for line in emitted.splitlines():
            assert not line.startswith("::error::FORGED-BY-PAYLOAD"), (
                "a payload line was emitted as a workflow annotation:\n"
                f"{emitted}")

    def test_a_reshaped_summary_counter_fails_closed_and_cannot_forge(self):
        """#8904 review P2: the 200 arm printed payload scalars RAW.

        `checked` went into the summary and was compared with `== 0` without a
        type check, so a string count printed as-is and skipped the empty-fleet
        warning — a reshaped response passed as a clean pass. A value carrying a
        newline could also emit a real workflow command from THIS printer, the
        forgery `show_body` indents away. Counters are now required to be plain
        integers, and anything else — non-numeric, float-shaped, negative — fails
        closed rather than being reinterpreted.
        """
        import subprocess
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        def _run(summary):
            body = json.dumps(summary).encode()

            class _Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    n = int(self.headers.get("Content-Length") or 0)
                    if n:
                        self.rfile.read(n)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *a):
                    pass

            srv = HTTPServer(("127.0.0.1", 0), _Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                step = _billing_steps()["Run billing reconcile pass"]["run"]
                proc = subprocess.run(
                    ["bash", "-c", step],
                    env={
                        "PATH": "/usr/local/bin:/usr/bin:/bin",
                        "INTERNAL_API_URL": f"http://127.0.0.1:{srv.server_port}",
                        "FASTAPI_INTERNAL_KEY": "test-internal-key",
                        "HOME": "/tmp",
                        "EVENT_NAME": "schedule",
                    },
                    capture_output=True, text=True, timeout=120,
                )
            finally:
                srv.shutdown()
                srv.server_close()
            return proc, proc.stdout + proc.stderr

        base = {"checked": 1, "repaired": 0, "unchanged": 0, "unverified": 0,
                "skipped": 0, "drift": [], "failed": [], "unrepaired": [],
                "alerted": 0}

        # A non-numeric counter must fail the run, not pass as a clean one.
        proc, out = _run({**base, "checked": "lots"})
        assert proc.returncode == 1, f"a reshaped counter passed: {out}"
        assert "checked" in out and "not an integer" in out, out

        # A float/negative-shaped counter must NOT be reinterpreted: filtering
        # digits would turn 2.0 into 20 and -3 into 3 (measured), a silent 10x
        # on a green pass.
        for bad in (2.0, -3):
            proc, out = _run({**base, "checked": bad})
            assert proc.returncode == 1, (
                f"a non-integer counter {bad!r} passed as a clean count: {out}")
            assert "checked=" not in out, out

        # A newline inside a counter must not become a workflow command.
        proc, out = _run({**base, "unverified": "1\n::error::FORGED-BY-PAYLOAD"})
        for line in out.splitlines():
            assert not line.startswith("::error::FORGED-BY-PAYLOAD"), out

    def test_a_wedged_reconcile_is_named_stuck_not_enumeration_incomplete(self):
        """#8904 round 4: the two named 503 statuses want OPPOSITE responses.

        A wedged lock was paged as "the control plane is unreadable, retry once
        it is readable" — a cause that clears itself — while the real cause (a
        dead pass holding the lock until the process restarts) went unsaid, so
        the operator retries forever. The body also carries ids on the non-200
        path, through a shell redactor whose boundary classes consume their own
        delimiter: two adjacent ids on one line used to leave the second raw.
        """
        import subprocess
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        first = "f69f0773cfa54c62b900c13c6a"
        second = "aaaabbbbccccddddeeeeffff11"
        # A registry ULID on the NON-200 path, which only the shell shape rule
        # can redact (no row is about it, so the by-value pass cannot help).
        registry = "0006559a1ff-aaaabbbbcccc"
        payload = json.dumps({
            "status": "reconcile_stuck",
            "detail": f"a previous pass is wedged (orgs {first},{second},{registry})",
        })

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload.encode())

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            script = _billing_steps()["Run billing reconcile pass"]["run"]
            proc = subprocess.run(
                ["bash", "-c", script],
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "INTERNAL_API_URL": f"http://127.0.0.1:{server.server_port}",
                    "FASTAPI_INTERNAL_KEY": "test-internal-key",
                    "HOME": "/tmp",
                    "EVENT_NAME": "schedule",
                },
                capture_output=True, text=True, timeout=120,
            )
        finally:
            server.shutdown()
            server.server_close()

        emitted = proc.stdout + proc.stderr
        assert proc.returncode == 1, emitted
        # Assert on the MESSAGE, not the token: the body is printed by
        # ``show_body`` in this same branch, so `reconcile_stuck` appears even
        # when the naming branch is deleted. The misdiagnosis to kill is the
        # ENUMERATION text — a cause that clears itself, sending the operator to
        # retry forever — so its ABSENCE, and the STUCK wording, are the test.
        assert "is STUCK" in emitted, (
            f"a wedged pass must be NAMED as stuck, not reported as an "
            f"unreadable control plane:\n{emitted}"
        )
        assert "enumeration_incomplete" not in emitted, (
            f"a wedged pass was paged as an enumeration failure — the operator "
            f"would retry a cause that never clears:\n{emitted}"
        )
        assert "retry once the control plane is readable" not in emitted, emitted
        for raw in (first, second, registry):
            assert raw not in emitted, (
                f"{raw} reached the PUBLIC log through the non-200 body:\n{emitted}"
            )

    def test_unset_secret_fails_before_calling_the_endpoint(self):
        """A missing INTERNAL_API_URL/FASTAPI_INTERNAL_KEY is a red run.

        Otherwise the request goes nowhere, curl fails with a DNS error the
        reader has to decode, and the cause (unconfigured secrets) is invisible.
        """
        import subprocess

        script = _billing_steps()["Run billing reconcile pass"]["run"]
        for missing in ("INTERNAL_API_URL", "FASTAPI_INTERNAL_KEY"):
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp",
                "INTERNAL_API_URL": "http://127.0.0.1:1",
                "FASTAPI_INTERNAL_KEY": "test-internal-key",
            }
            env.pop(missing)
            proc = subprocess.run(["bash", "-c", script], env=env,
                                  capture_output=True, text=True, timeout=60)
            assert proc.returncode != 0, f"an unset {missing} read as a pass"
            assert "::error::" in proc.stdout + proc.stderr

    def test_a_failed_run_pages_telegram(self):
        """The header says the job "PAGES". A red run alone is not a page.

        This asserts the delivery leg exists and is wired to the failing paths
        (``if: failure()``) using the repo's single Telegram sender — AND that
        the sender can actually be sourced, which is the half that was missing:
        string-presence alone is satisfied by a workflow whose page step dies
        on `. .github/scripts/telegram-send.sh` because the runner never
        checked the repo out. ``test_the_page_step_can_succeed`` below proves
        the file is on the runner; this test pins the wiring.
        """
        page = _billing_steps().get("Page on failure")
        assert page is not None, (
            "no failure step — the run would be red only in the Actions tab, "
            "which is not a page"
        )
        assert page.get("if") == "failure()", (
            f"the page step must run on every failing path (if: {page.get('if')!r})"
        )
        run = page.get("run", "")
        assert ".github/scripts/telegram-send.sh" in run, (
            "the page must use the repo's ONE Telegram sender (#4574)"
        )
        assert "tg_send" in run
        env = page.get("env") or {}
        assert "TELEGRAM_BOT_TOKEN" in env and "TELEGRAM_CHAT_ID" in env, (
            "the page step must bind both Telegram secrets in env: (#4361)"
        )


#: The org-id shape this fleet ACTUALLY mints. ``_new_org_org_id`` and the
#: Supabase control plane both use ``uuid.uuid4().hex[:26]``, and
#: ``supabase/migrations/0006_teams.sql`` documents the column as
#: ``ULID or 26-hex`` — never a hyphenated UUID. A redaction test written with a
#: hyphenated UUID (or with ``"o1"``) exercises a shape no customer id has, and
#: passes while every real id is published.
_REAL_ORG_ID = "a1b2c3d4e5f6a7b8c9d0e1f2a3"
_OTHER_ORG_ID = "41ed698615f8a7b6c5d4e3f2a1"
_UUID_ORG_A = "550e8400-e29b-41d4-a716-446655440000"
_UUID_ORG_B = "11111111-2222-3333-4444-555555555555"


def _fingerprint(org_id: str) -> str:
    import hashlib

    return "org-" + hashlib.sha256(org_id.encode()).hexdigest()[:10]


def _run_billing_driver(body, status: int = 200, trigger: str = "schedule"):
    """Run the workflow's own reconcile step against a stub server."""
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    payload = body if isinstance(body, str) else json.dumps(body)

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload.encode())

        def log_message(self, *a):  # silence the default stderr trace
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        script = _billing_steps()["Run billing reconcile pass"]["run"]
        return subprocess.run(
            ["bash", "-c", script],
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "INTERNAL_API_URL": f"http://127.0.0.1:{port}",
                "FASTAPI_INTERNAL_KEY": "test-internal-key",
                "HOME": "/tmp",
                "EVENT_NAME": trigger,
            },
            capture_output=True, text=True, timeout=120,
        )
    finally:
        server.shutdown()
        server.server_close()


class TestBillingReconcileLogRedaction:
    """#8895 review P2 (round 3): the PUBLIC log must not carry a customer id.

    The summary prints ``failed[].error``/``unrepaired``/``drift_up`` rows, and
    that error text embeds the org id in PROSE (``update_org_billing: no
    organizations row matched id='<org_id>'``) as well as carrying Stripe
    object ids (``stripe GET customers/<cus_…>/subscriptions``). The previous
    round redacted by hyphenated-UUID shape, which no real org id matches, so
    every real id reached the log intact while a UUID-shaped test would pass.

    These run the step's own script, so they are anchored to the shipped text.
    """

    def _failing_body(self):
        return _billing_case(
            unchanged=0,
            failed=[
                {"org_id": _REAL_ORG_ID,
                 "error": "RuntimeError: update_org_billing: no organizations row "
                          f"matched id='{_REAL_ORG_ID}' — the billing write was "
                          "dropped (fail-closed)"},
                {"org_id": _UUID_ORG_A, "error": "StripeAPIError: boom"},
                {"org_id": _UUID_ORG_B,
                 "error": "StripeAPIError: stripe GET "
                          "customers/cus_PqR3xyz9/subscriptions -> 404: {}"},
            ],
            unrepaired=[{"org_id": _OTHER_ORG_ID, "expected_tier": "pro"}],
        )

    def test_no_real_customer_id_reaches_the_log(self):
        proc = _run_billing_driver(self._failing_body())
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1, (
            "a pass with failed rows must be red — the redaction test needs the "
            f"::error:: path to run\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        for raw in (_REAL_ORG_ID, _OTHER_ORG_ID, _UUID_ORG_A, _UUID_ORG_B,
                    "cus_PqR3xyz9"):
            assert raw not in out, (
                f"the public Actions log published the customer id {raw!r}:\n{out}"
            )

    def test_the_id_is_redacted_where_it_appears_in_prose(self):
        """Redaction must follow the id INTO the error sentence.

        A key-based filter (or a shape list that skips 26-hex) leaves the id in
        ``failed[].error``; the row then stays readable in the log. The prose
        copy is replaced by the SAME fingerprint as the row's ``org_id``, so the
        row is still correlatable across runs.
        """
        proc = _run_billing_driver(self._failing_body())
        out = proc.stdout + proc.stderr
        fingerprint = _fingerprint(_REAL_ORG_ID)
        assert f"id='{fingerprint}'" in out, (
            "the error prose no longer names the row by its fingerprint — either "
            f"the id was redacted to something uncorrelatable, or not at all:\n{out}"
        )
        assert fingerprint in out

    def test_fingerprints_stay_distinct_for_every_id_shape(self):
        """A fingerprint that collapses ids cannot correlate anything.

        Pre-fix, the id was shape-substituted BEFORE it was hashed, so every
        UUID-shaped org hashed to one constant (``hash('<org-id>')``) and two
        different customers were indistinguishable. The digest must come from the
        ORIGINAL id, for every shape.
        """
        proc = _run_billing_driver(self._failing_body())
        out = proc.stdout + proc.stderr
        fingerprints = {
            org: _fingerprint(org)
            for org in (_REAL_ORG_ID, _OTHER_ORG_ID, _UUID_ORG_A, _UUID_ORG_B)
        }
        assert len(set(fingerprints.values())) == len(fingerprints)
        for org, fp in fingerprints.items():
            assert fp in out, (
                f"{org} was not summarised under its own fingerprint {fp}\n{out}"
            )

    def test_a_subscription_id_is_redacted_too(self):
        body = _billing_case(
            unchanged=0,
            failed=[{"org_id": _REAL_ORG_ID,
                     "error": "StripeAPIError: stripe POST "
                              "subscriptions/sub_1AbCdEfGh9 failed: 400"}],
        )
        out = _run_billing_driver(body).stdout
        assert "sub_1AbCdEfGh9" not in out, out
        assert "<stripe-id>" in out, out

    def test_a_non_200_body_is_redacted_by_the_same_shapes(self):
        """``show_body`` prints a failure body straight to the same public log.

        It carried the same UUID-only assumption, so a 26-hex id or a Stripe
        object id inside ``detail`` was published verbatim.
        """
        body = {"status": "boom",
                "detail": f"could not enumerate org {_REAL_ORG_ID} "
                          "(customer cus_PqR3xyz9)"}
        proc = _run_billing_driver(body, status=500)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1, out
        assert _REAL_ORG_ID not in out, out
        assert "cus_PqR3xyz9" not in out, out


def _live_script_refs(job: dict) -> list[str]:
    """Step names in ``job`` whose ``run:`` sources a repo script, LIVE.

    Comment lines are stripped first: naming a script inside a comment (the
    ``ai-review-gate.yml`` shape) delivers nothing and must not be read as a
    dependency, or the guard below would demand a pointless checkout.
    """
    refs: list[str] = []
    for step in job.get("steps") or []:
        body = str(step.get("run", ""))
        live = "\n".join(
            line for line in body.splitlines()
            if not line.lstrip().startswith("#")
        )
        if re.search(r"(^|[\s;.])`?\.github/scripts/", live):
            refs.append(step.get("name") or step.get("uses") or "<unnamed>")
    return refs


_WORKFLOWS_DIR = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows"
)


class TestWorkflowScriptDependenciesAreOnTheRunner:
    """A `run:` that sources `.github/scripts/<x>` needs a checkout first.

    A GitHub-hosted runner's ``$GITHUB_WORKSPACE`` starts EMPTY; only
    ``actions/checkout`` puts the repo there. A step that sources a repo script
    without checking out dies on the ``.`` builtin — and under
    ``set -euo pipefail`` it dies BEFORE whatever the step was supposed to do,
    so the step's real work (delivering a page, recording a verdict) silently
    never happens while every string-presence assertion about it still passes.

    Scope: EVERY job of EVERY workflow file, so the class cannot come back in a
    new driver. Jobs that only NAME a script in a comment are excluded (see
    ``_live_script_refs``).
    """

    @staticmethod
    def _cases() -> list[tuple[str, str, list[str], bool]]:
        cases = []
        for path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(doc, dict):
                continue
            for job_name, job in (doc.get("jobs") or {}).items():
                if not isinstance(job, dict):
                    continue
                refs = _live_script_refs(job)
                if not refs:
                    continue
                checked_out = any(
                    "actions/checkout" in str(s.get("uses", ""))
                    for s in job.get("steps") or []
                )
                cases.append((path.name, job_name, refs, checked_out))
        return cases

    @pytest.mark.parametrize(
        "workflow,job,refs,checked_out",
        _cases(),
        ids=[f"{w}::{j}" for w, j, _refs, _co in _cases()],
    )
    def test_a_job_sourcing_a_repo_script_checks_out_first(
            self, workflow, job, refs, checked_out):
        assert checked_out, (
            f"{workflow} job {job!r} sources a repo script from {refs} but "
            "never runs `actions/checkout` — $GITHUB_WORKSPACE is empty on a "
            "hosted runner, so the source fails and the step's output is "
            "never produced (#8878: the 'PAGES' leg could never fire)"
        )

    def test_the_case_list_is_not_empty(self):
        """Guards the guard: a broken glob would empty the parametrization."""
        cases = self._cases()
        assert cases, "no workflow job sources a repo script — the glob broke"
        assert any(w == "billing-reconcile-cron.yml" for w, _j, _r, _c in cases), (
            "the billing reconcile driver must be among the covered jobs"
        )


# ── R1 (#9548, epic #7879): the Postgres record must be ARMABLE from the deploy ─
# The record's seam is presence-based on `TORTOISE_RECORD_DSN`: unset ⇒
# NOT_ARMED ⇒ zero statements, no connection attempt, nothing red (§A8 of
# `docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md` measured
# exactly that state on the live deployment). Every other part of the record was
# already built and CI-green, so the remaining defect was that NO deploy surface
# named the variable — a capability that could only be switched on with an
# out-of-band `fly secrets set`, which the provenance gate then correctly reports
# as unsourced. These two tests pin the surfaces R1 changed, because each one can
# be reverted with the rest of the suite still green.

_RECORD_DSN_ENV = "TORTOISE_RECORD_DSN"
_PROVENANCE_STEP = "Check Fly secret provenance (fail-closed)"
_REPO_ROOT = _WORKFLOW.parents[2]
_SECRET_MANIFEST = _REPO_ROOT / ".github" / "scripts" / "fly-managed-secrets.txt"
_RUNBOOK = _REPO_ROOT / "docs" / "infra-runbook.md"
_EPIC_DOC = (
    _REPO_ROOT
    / "docs"
    / "epics"
    / "2026-10-10-7879-postgres-record"
    / "08-hosted-prerequisites.md"
)

#: A Secrets Matrix row, same shape the #8614 guard reads.
_MATRIX_ROW = re.compile(
    r"^\|\s*([A-Z][A-Z0-9_]*)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$", re.MULTILINE
)


def _drift_gate():
    """The shipped provenance gate, imported as a module.

    ONE reader per surface: `read_manifest` (the manifest) and
    `fly_toml_env_keys` (what counts as an `[env]` ASSIGNMENT) are the gate's
    own, so this test cannot disagree with the deploy about what a declaration
    means. The local copy this replaced did `line.split("#", 1)[0].split()`,
    which is the parse bug the gate documents — `REGISTRY_STREAM_KEY
    fly-only:#661` truncated to `fly-only:` — i.e. the test meant to prove "the
    manifest and the gate agree" used a weaker parser than the gate (#9548
    review, non-blocking B).
    """
    import importlib.util

    sys.path.insert(0, str(_REPO_ROOT / ".github" / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "check_fly_secret_drift_r1", _REPO_ROOT / ".github" / "scripts" / "check-fly-secret-drift.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _manifest_declarations() -> dict[str, str]:
    """`{Fly variable: source token}` for every shipped manifest declaration."""
    return _drift_gate().read_manifest(_SECRET_MANIFEST)


def test_the_manifest_reader_keeps_a_fly_only_reference_intact():
    """#9548 review B: the manifest is read with the GATE's parser, not a `#` split.

    A plain `line.split("#", 1)[0]` cuts at the `#` INSIDE a `fly-only:#661`
    reference, so `REGISTRY_STREAM_KEY` parsed to `fly-only:` — the issue number
    silently lost, the exact spelling `check-fly-secret-drift.py` records as a
    past bug. The reader is now the gate's own `read_manifest`, and this asserts
    the READING (the reference keeps its `#<N>`), not merely that the name
    appears, so a future hand-rolled copy turns this red.

    The reference is validated with the GATE's own `_FLY_ONLY_REF_RE`, not a
    locally retyped `fly-only:#\\d+` (#9585 review, non-blocking): the gate's
    contract admits `<owner>/<repo>#<N>` as well as `#<N>`, so the narrower copy
    would have red a future cross-repo `fly-only:` entry on prose, not on a bug —
    the same "one reader per surface" rule the module applies to `read_manifest`.
    """
    gate = _drift_gate()
    ref_re = gate._FLY_ONLY_REF_RE
    # The relaxed pin must still reject the EXACT truncation it exists to catch,
    # and still accept both documented spellings — otherwise "use the gate's
    # regex" could widen to nothing (#9585 review r5).
    assert ref_re.fullmatch("#661") and ref_re.fullmatch("acme/repo#661"), (
        "the gate's fly-only reference no longer accepts the documented `#<N>` / "
        "`<owner>/<repo>#<N>` spellings — this pin's scope has changed, re-derive it"
    )
    assert not ref_re.fullmatch(""), (
        "the gate's fly-only reference accepts the EMPTY argument, so a `#`-split "
        "reader (`fly-only:#661` → `fly-only:`) would pass this pin"
    )
    declared = _manifest_declarations()
    fly_only = {n: s for n, s in declared.items() if s.startswith("fly-only:")}
    assert fly_only, "the shipped manifest declares no fly-only: name — the reader is wrong"
    for name, source in fly_only.items():
        argument = source.split(":", 1)[1]
        assert ref_re.fullmatch(argument), (
            f"{name} parsed as {source!r} (reference {argument!r}) — the `#` inside "
            "the issue reference was read as a comment marker (read the manifest "
            "with the gate's `read_manifest` instead of a `split('#')` copy), or "
            "the entry carries no well-formed issue reference at all"
        )


def _matrix_rows() -> dict[str, tuple[str, str, str]]:
    """`{name: (fly, falkordb, github)}` for the runbook's Secrets Matrix rows."""
    section = _RUNBOOK.read_text(encoding="utf-8").split("## Secrets Matrix", 1)
    assert len(section) == 2, "docs/infra-runbook.md has no `## Secrets Matrix` section"
    body = section[1].split("\n## ", 1)[0]
    return {
        name: (fly.strip(), fdb.strip(), gh.strip())
        for name, fly, fdb, gh in _MATRIX_ROW.findall(body)
    }


_RECORD_ARMING_HEADING = "### Arming the Postgres record"

# The numeral words a count-bearing runbook list may carry, so a pin can compare the
# LIST'S LABEL to the members that actually follow it instead of pinning one word
# (#10083 review F1: a literal `THREE` reds an honest `FOUR` relabel while a
# fourth member stays invisible to a fixed-name alternation).
_NUMERALS = {"TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6}


def _record_keys_missing_a_row(heads: list[str], names: set[str]) -> set[str]:
    """The `names` that NO row's HEAD CELL in the arming table documents.

    Split out of the pin so its non-vacuity can be MEASURED against the real head
    cells instead of asserted: the same predicate applied to a coherently renamed
    payload must report every renamed key missing, which is the state the pin
    exists to catch (#9548 review P3).
    """
    return {name for name in names if not any(f"`{name}`" in head for head in heads)}


def _record_arming_section() -> str:
    """The runbook's `Arming the Postgres record` section, verbatim.

    Scoped to the section, so a `/metrics` mention elsewhere in the 2.7k-line
    runbook (§6.9's `serve_health` discussion legitimately has one) can neither
    satisfy nor fail these assertions by accident.
    """
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert _RECORD_ARMING_HEADING in text, (
        f"docs/infra-runbook.md has no {_RECORD_ARMING_HEADING!r} section — the "
        "operator's only arming recipe moved or was deleted"
    )
    body = text.split(_RECORD_ARMING_HEADING, 1)[1]
    return body.split("\n## ", 1)[0].split("\n### ", 1)[0]


def test_the_record_arming_verify_recipe_reads_a_surface_that_exists():
    """#9548 review P1: the verify recipe must not read a route the app never serves.

    The recipe shipped with this section was
    `fly ssh console … -C "curl -s localhost:9090/metrics | grep record_write_failures"`,
    and it CANNOT return a reading in any state: the hosted app's 9090 listener is
    `monitoring.start_health_listener` → `_HealthzHandler`, which answers `/healthz`
    and 404s every other path, and `serve_health` (the only `/metrics` server) is not
    a `fly.toml` process. `curl -s` prints the 404 body, `grep` matches none of it,
    and the recipe emits NOTHING — byte-identical to "0 failures", the reading the
    next line told the operator to want. A fail-open verification step, in the
    section that exists because the record can be silently inert.

    Falsifying on the pre-fix text: `9090/metrics` and the absolute bar both appear
    there, and neither may come back.
    """
    section = _record_arming_section()
    # ⛔ The `no_tenant` pins below read a WHITESPACE-NORMALIZED copy (#10071 review
    # F3). The repo declines to enforce hard wraps (`.markdownlint-cli2.jsonc`:
    # `MD013: false`), so a reflow is an expected edit — and pinning the raw text
    # made three of those four pins red on a refill that changed no word (measured:
    # widths 76/78/82 red the `circuit_open`, `unbound` and `⛔ **Do` pins
    # respectively). Pinning the flat text makes a pin measure the CLAIM.
    flat = re.sub(r"\s+", " ", section)
    # No PRESCRIBED command block may read the route: the section may (and does)
    # QUOTE the broken recipe in prose to warn an operator off it, but a fenced
    # `bash` block is a command the operator is told to run.
    blocks = re.findall(r"```bash\n(.*?)```", section, re.DOTALL)
    assert blocks, "the arming section prescribes no command — the reader is wrong"
    for block in blocks:
        assert "9090/metrics" not in block, (
            "a prescribed arming command greps `localhost:9090/metrics`, which the "
            "hosted app does NOT serve (`_HealthzHandler` 404s every path but "
            "/healthz`) — it prints nothing in every state, so an empty grep reads "
            "as a pass"
        )
    # ⛔ The PHRASE is not the READ SURFACE: `the record write did not happen`
    # occurs four times in this section, three of them in prose, so the membership
    # pin below is satisfied with the fenced command DELETED — measured
    # (#10083 review cycle 2, P2: replacing the block with `echo 'no recipe here'`
    # left the suite at 100 passed). The class the message names is "a command the
    # operator can run", so pin one.
    assert any(
        re.search(r"grep[^\n]*the record write did not happen", b) for b in blocks
    ), (
        "no PRESCRIBED command greps the refused-write line — the section may quote "
        "the phrase in prose, but then the reader who follows the recipe greps "
        "nothing and the section has no read surface for a refused write"
    )
    # ... and the check that DOES exist is named: the seam logs every non-write at
    # WARN with its reason (`tortoise/record_writer.py`).
    # `flat`, not `section`: the phrase is wrappable prose (the ⚠️ `no_tenant`
    # bound below cites it as "it greps `the record write did not happen`"), and the
    # repo disables MD013 (`.markdownlint-cli2.jsonc`), so a refill may legally
    # split it across a line break and red a pin that changed no word — the F3
    # class, which this pin still carried after every other text pin had moved to
    # `flat` (#10083 review, P2, measured on the width-76 refill).
    assert "the record write did not happen" in flat, (
        "the section names no read surface for a refused write — the seam logs "
        "'the record write did not happen (<reason>)' and nothing greps it"
    )
    # The pass bar must state the EXPECTED non-zero baseline instead of "0":
    # a `no_durable_journal` decline is the healthy M2 capture path, and #9264's
    # `DatatypeMismatch` refusals are counted on `write_failed`.
    assert "must stay **0**" not in flat, (
        "the section sets an absolute `record_write_failures == 0` bar, which a "
        "HEALTHY armed deployment fails: #9264's DatatypeMismatch refusals are "
        "counted on write_failed (and the R1-before-R2 ordering expects them)"
    )
    for expected in ("#9264", "no_durable_journal", "record_non_journal_skips"):
        assert expected in section, (
            f"the verify section does not name {expected!r} — an operator reading a "
            "non-zero counter has no way to tell the documented baseline from a "
            "real arming failure"
        )
    # ... and the bar must require PRESENCE, and must not let an empty reading
    # pass. `write_live_record` returns BEFORE counting anything when unarmed
    # (`tortoise/record_writer.py`: `if not armed: return None`), so an unarmed
    # deployment yields `record_write_failures: {}`,
    # `record_non_journal_skips: {}` and an empty `fly logs` grep — the "no
    # unexpected failure" half is then VACUOUSLY satisfied.
    #
    # These pin CLAUSES (a sentence), never a bare token: `record_position_unproven`
    # and `journal_event` already appear elsewhere in the section (the recipe-2
    # counter list, the SQL block), so membership tests on them are satisfied
    # without the bar saying anything (#9585 reviews; three assertions were
    # vacuous that way). Each is mutation-proven against a meaning inversion.
    assert re.search(r"An all-empty reading is NOT a pass", flat), (
        "the bar never says an all-empty reading is not a pass — an unarmed "
        "deployment counts nothing at all, so a bar written only as 'no growth' "
        "is vacuously green on the silent-inert deployment this exists to detect"
    )
    assert re.search(r"After the capture, require\s+at least one reading \*\*before\*\* you interpret anything:", flat), (
        "the bar states no presence requirement — name the reading that must "
        "EXIST after a capture before the reason breakdown means anything"
    )
    # The presence LIST must be the six qualifying readings, in one clause — not
    # merely a lead-in that some other, always-true reading could be appended to.
    # (`record_writes_applied` is the POSITIVE witness added by #9548: without it
    # required here, deleting that clause from the runbook left this pin green —
    # the one reading the bar exists to add would have been the one reading the
    # pin did not demand.)
    # ⛔ Read the FLAT copy, like its siblings above: this pin used to read `section`,
    # and its pattern spells the sentence out with literal single spaces, so a
    # wording-identical refill at widths 76/78/82 broke that clause across a line
    # boundary and reded it — measured here after the F3 fix, i.e. the file still
    # reds a reflow F3 exists to make harmless (#10083 review P2). Normalizing is
    # NOT a relaxation: the six readings are still required verbatim below.
    presence = re.search(
        r"After the capture, require\s+at least one reading \*\*before\*\* you interpret anything:(.*?)\.\s",
        flat,
    )
    assert presence, "the presence requirement does not enumerate its readings"
    for reading in ("record_writes_applied", "record_write_failures",
                    "record_non_journal_skips",
                    "record_position_unproven", "journal_event", "point"):
        assert reading in presence.group(1), (
            f"the presence list omits {reading!r} — an armed deployment whose only "
            "signal is that reading is then indistinguishable from an inert one"
        )
    # The closure pin's PROPERTY: presence must be evidence of a WRITE ATTEMPT that
    # could reach the record. A `capture_session` capture declines at
    # `write_live_record`'s `if not durable` branch — before tenant resolution, the
    # breaker, or any socket — so it grows `no_durable_journal` on a deployment
    # armed against nothing. The bar must therefore say the proving capture is a
    # JOURNALING one, and no route/probe reading may qualify.
    assert not re.search(r"/health|GET /|probe", presence.group(1)), (
        "the presence list admits a health/liveness probe — that answers on an "
        "app whose record seam is completely inert, so presence becomes "
        "trivially satisfiable and the bar greens the state it exists to catch"
    )
    assert re.search(r"The capture that proves presence must be a\s+JOURNALING one", flat), (
        "the bar does not require the proving capture to be a JOURNALING one — a "
        "`capture_session` capture grows `no_durable_journal` at "
        "`write_live_record`'s `if not durable` branch, before any tenant "
        "resolution or socket, so it satisfies presence on a deployment that "
        "never reaches Postgres"
    )
    # The BEFORE-and-after baseline: "growth" has no referent without it.
    # ⛔ The noun is pinned as *every record counter*, not "both counters": the
    # presence list below requires FOUR counters (plus two row reads), so a
    # "both" baseline told the operator to take two of them — and the positive
    # witness this bar exists to add (#9548's `record_writes_applied`) was one it
    # omitted, leaving the delta it demands uncomputable from the reading it
    # prescribed (#9548 review P2).
    # ⛔ The clause is pinned from its lead-in through the RULE it states — `… the
    # \*difference\* between the two reads, never an absolute count` — and that end
    # point is deliberate: a pattern stopping at "between the two reads" was
    # measured GREEN on the runbook dropping the very rule this assertion's message
    # defends ("an absolute count left from earlier in the process's life then reads
    # as this capture's growth"). Two earlier forms also failed, both measured: a
    # bare fragment matched inside a sentence that FORBADE the baseline ("So ⛔ Do
    # not **read every record counter …**" stayed green), and a negation-vocabulary
    # guard bolted onto the fragment false-blocked accurate wording ("So never skip
    # the baseline: **read every…**" red). Pinning the clause needs no vocabulary:
    # any negation inserted before the imperative breaks the match, and a rewording
    # reds — the trade the sibling clauses in this test make too. `flat` still
    # tolerates a reflow.
    assert re.search(
        r"So \*\*read every record counter BEFORE\s+the capture and again after\*\*:"
        r"\s+presence and growth are the \*difference\* between the two reads,"
        r" never an absolute count",
        flat,
    ), (
        "the bar says 'growth' but never tells the operator to take a reading "
        "BEFORE the capture — an absolute count left from earlier in the "
        "process's life then reads as this capture's growth — or it names a "
        "subset of the counters the presence list then requires"
    )
    assert re.search(r"If nothing appeared at all, do not conclude the record is healthy", flat), (
        "the bar has no arm for an empty reading — an operator landing there is "
        "told nothing, and that state is the whole point of the section"
    )
    # ... and item 2 must survive, anchored on its imperative the same way the
    # presence lead-in is. A phrase-only pin accepted "*Then*, if convenient:" and
    # "*Then*: you need not care about …" (both GREEN), i.e. the half the bar
    # depends on could be voided without a red.
    assert re.search(r"\*Then\*:\s*\*\*no growth in a reason other than", flat), (
        "the bar drops or softens item 2 — with it gone, a `circuit_open` / "
        "`no_tenant` reading passes on the #9264 baseline alone, which is the "
        "state this section exists to catch"
    )
    # "The capture's OWN" is load-bearing: item 2 is the bar a reader sees first,
    # and an unqualified "none at all for `no_tenant`" reds a healthy deployment on
    # the ordinary unbound traffic the ⚠️ bound below excuses — the two statements
    # would contradict each other inside one section (#10071 review F1). The pin
    # covers the WHOLE clause, `no_tenant` through `circuit_open`: pinning only the
    # qualifier left the other two always-fail reasons deletable with nothing red
    # (#10083 review F2). The qualifier is pinned EXACTLY: `[^)]*` admitted a
    # qualifier of any wording, so the pointer to the ⚠️ bound could be replaced by
    # anything (including nothing useful) with the pin still green (#10083 review).
    assert re.search(
        r"none at all for `no_tenant` \(the capture's OWN — see the ⚠️ bound below\) / "
        r"`malformed_dsn` / `circuit_open`",
        flat,
    ), (
        "the bar reads absolute on `no_tenant` while the ⚠️ bound below says the "
        "growth is a fail-and-fix only for the three cases the ⚠️ bound names — "
        "a reader who "
        "stops at item 2 fails a healthy deployment on its default traffic"
    )
    # ⛔ The SAME absolute, one paragraph lower (third review cycle, P1): the reason
    # definitions were introduced by "Those three are always a fail-and-fix", which
    # re-asserts the unqualified bar the qualifier above just removed — so a reader
    # who stops at the definitions fails a healthy deployment on the ordinary
    # unbound traffic the ⚠️ bound excuses, the exact defect #10071 exists to
    # remove. Measured: the sentence could be reverted with every other pin green.
    # ⛔ PIN THE CLAIM, NOT ONE IDIOM (#10083 review P2, measured): the positive
    # half was a single literal phrasing, so the same ruling in another shape —
    # `Those three are not exemptions in their own right` — red the suite with a
    # message accusing the section of the absolute it had just kept (the F3 class
    # this PR exists to remove). The POLARITY is what is load-bearing: the three
    # reasons are NOT exempt on their own. Both idioms below state it; the
    # `always` detector after them catches the contradictory absolute, which is
    # the claim this pin actually measures.
    assert re.search(
        r"(?:None of those three is an exemption of its own"
        r"|Those three are not exemptions? in their own right)",
        flat,
    ), (
        "the reason definitions still introduce the three as an unconditional "
        "fail-and-fix, contradicting the ⚠️ bound that qualifies `no_tenant`"
    )
    assert not re.search(r"Those three are always a fail-and-fix", flat), (
        "the unqualified absolute is back — a `no_tenant` growth is a fail-and-fix "
        "only on the three cases the ⚠️ bound names"
    )
    # The #9264 `write_failed` exemption bound: without it the documented
    # R1-without-R2 state (writes nothing, counts only `write_failed`) satisfies
    # item 1's *any reason* clause AND the exemption — green with zero rows.
    assert re.search(r"a\s+deployment that writes no row at all is not passing this bar", flat), (
        "the `write_failed` exemption is unbounded — the R1-without-R2 deployment "
        "writes no row, grows only `write_failed`, and then passes both halves"
    )
    assert re.search(r"a new `journal_event` / `point` row must still appear", flat), (
        "the bar never requires a row to exist, only that no unexpected reason "
        "grew — a deployment writing nothing satisfies that"
    )
    # The reasons must NOT be given a common basis: a later review found the
    # section asserting "each is counted only after the DSN resolved, so any of
    # them proves the seam exists and ran", which `no_journal` (journal-less
    # lane, `write_live_record` never entered) and `malformed_dsn` (counted when
    # the DSN fails the scheme check) both contradict. Pin the MEANING, not the
    # old phrase: the section must send the reader to the counting site instead.
    assert re.search(r"its counting site in the code is the\s+authoritative statement", flat), (
        "the section states no authority for the meanings it does not give — "
        "without it a common basis for the reasons is the natural (wrong) reading"
    )
    # `record_dsn()` checks the SCHEME and the HOST, and nothing else, so a
    # "parseable DSN" definition of `malformed_dsn` is still false and
    # self-contradictory by the section's own §The value, which says the scheme
    # check cannot catch a bad password. But pinning the scheme clause ALONE is
    # not enough: #9548 added the HOST rule to `record_dsn()` (#9548 review P1),
    # and this test stayed GREEN while the section went false — it still said
    # "every other malformation surfaces as `circuit_open` / `write_failed`",
    # which a hostless value no longer does. So pin BOTH refusal causes, or the
    # prose re-stales the moment the predicate grows.
    assert "not a parseable DSN" not in flat, (
        "the section defines `malformed_dsn` as a parse failure — `record_dsn()` "
        "does no parsing, only the scheme + host checks"
    )
    assert re.search(r"does \*\*not start with\*\*\s+`postgresql://`", flat), (
        "`malformed_dsn` is not defined by the scheme check it actually is"
    )
    assert re.search(r"names no host", flat), (
        "the section's `malformed_dsn` definition omits the HOST rule — a "
        "hostless `TORTOISE_RECORD_DSN` (`postgresql://user:pw@`, a clipped "
        "paste) is counted `malformed_dsn`, not `circuit_open`, so a reader "
        "debugging that counter is sent to the wrong cause"
    )
    assert re.search(r"Nothing else is parsed", flat), (
        "the section does not bound the host rule, so its reader cannot tell "
        "which faults `record_dsn()` catches from the ones only a connection "
        "can find (wrong password / paused project)"
    )
    # `no_tenant` is also the DESIGNED outcome for a credential that names no
    # graph the record can key on — the self-host registry lane
    # (`record_tenant_for` returns None there because `control_plane_ids` is
    # False, #9188/#9192) — and these counters are process-global, so an absolute
    # "none at all for `no_tenant`" reds a healthy armed deployment on ordinary
    # traffic and offers no legitimate remedy. The bar must bound that arm to the
    # CAPTURE and **rule the per-request attribution out** (`record_writer` logs no
    # request identity), keeping it a fail-and-fix for the capture's own writes.
    # ⛔ #10046 CLOSED the session/OAuth half: `hosted_api._session_user_org` and
    # `oauth.resolve_oauth_access_token` now stamp `default_graph_id`, so neither
    # credential class is a designed `no_tenant` source any more and the prose
    # must not list them as one (review P2, rebase onto main's #10090).
    assert re.search(r"BOUND to the proving capture, not to the process", flat), (
        "the `no_tenant` arm is absolute — it fails a healthy deployment on the "
        "unbound-credential `no_tenant` the code counts by design, and the only "
        "lever the section offers (`TORTOISE_RECORD_SINGLE_TENANT`) is the "
        "#9192 cross-tenant hazard it forbids below"
    )
    # ⛔ NOT the bare token `process-global`: the section already says "env is
    # process-global" about the #9192 env pair ("a process-global tenant on a
    # multi-tenant process", below), so a bare-token arm
    # passed on the PRE-fix wording — a vacuous assertion, caught by the verifier
    # and re-anchored to this clause.
    assert re.search(r"These counters are \*\*process-global\*\*", flat), (
        "the section does not say the counters are process-global, so a "
        "before/after delta reads as if it measured only the capture"
    )
    # ⛔ NOT the bare token `graph-bound`: the section contains that word in the
    # SELF-HOST registry lane's sentence too ("the one graph-bound key that resolves
    # through no control plane"), so the token was satisfied by a sentence about a
    # different lane while the executable REMEDY could be replaced by main's
    # impossible "attribute … through recipe 1's log line" and stay green
    # (#10083 review P1, measured on two variants). Pin the remedy CLAUSE, and add
    # a negative pin so the attribution instruction cannot come back beside the ⛔
    # prohibition below — a section that says both is green under a presence-only
    # ⛔ pin, which is the class this PR exists to close.
    # ⛔ The list must NOT include an org-wide API key: since #10025 that key IS
    # named (`resolve_api_key` puts the org's active `kind='default'` row id on the
    # credential as `default_graph_id`, `record_tenant_for` returns it —
    # `tortoise/hosted_api.py`, `tortoise/record_writer.py`), so listing it as
    # DESIGNED residue lets an operator excuse real `no_tenant` growth that the only
    # remaining designed sources (#10047's shapes) do not explain — a fail-open read
    # of the very signal this section exists to expose (#10083 review P1). The
    # control-plane-cannot-name-a-default-row case is ONE of the TWO org-wide ones —
    # the other is a resolution FAULT, which the fail-soft resolver turns into the
    # same `no_tenant` — and the prose must say BOTH. ⛔ NOT "the ONLY": a comment
    # demanding the exhaustive reading its own pin below FORBIDS is the F4 class
    # (a lane obeying it edits `**or** when the resolution itself faults` out of the
    # prose and reds the suite), so this comment names the arm the pin protects.
    assert re.search(r"An \*\*org-wide API KEY is NOT one of those sources\*\* since #10025", flat), (
        "the ⚠️ list files org-wide API keys under the DESIGNED `no_tenant` sources, "
        "which since #10025 is false — the key is named from `default_graph_id`"
    )
    assert not re.search(r"an org-wide / session", flat), (
        "REVERT-DETECTOR (one draft's exact wording — the message names a class "
        "this pin cannot see): the pre-#10025 list is back, reading an org-wide key "
        "as designed residue beside the session / OAuth shapes. The CLAIM is "
        "measured by the `NOT one of those sources` pin above; keep this exact, "
        "cheap, and honestly labelled"
    )
    assert re.search(
        r"A session/JWT credential and an OAuth `oat_` token are \*\*NOT\*\* one of "
        r"those\s+sources",
        flat,
    ), (
        "the ⚠️ list files a session/JWT or OAuth `oat_` credential under the "
        "DESIGNED `no_tenant` sources, which #10046 made false — "
        "`hosted_api._session_user_org` and `oauth.resolve_oauth_access_token` "
        "both stamp `default_graph_id`, so a `no_tenant` from either is the "
        "default row failing to be NAMED, i.e. a fail-and-fix"
    )
    # ⛔ The claim is not the VERDICT. Measured at the merge head: inverting
    # `a **fail-and-fix**, not a designed state` to `a **designed state**, not a
    # fail-and-fix`, deleting it with the mechanism, or deleting ONLY the
    # mechanism all left the suite green — so the paragraph's operative
    # conclusion (and the evidence it rests on) could be reversed while the
    # pinned claim sentence survived, which is how an operator gets told to
    # IGNORE genuine `no_tenant` growth from a session/JWT or `oat_` credential.
    # This is the class the sibling no-nameable-row VERDICT pin already closes
    # ("so it is a fail-and-fix as well"), and the same reason the `circuit_open`
    # EVIDENCE is pinned above: a claim whose consequence is unpinned is prose.
    # The alternation accepts the synonyms a truthful edit may pick (`designed
    # outcome` / `designed state`) — the F3 class in assertion form is an
    # improving reword that reds a pin whose message names a claim it kept.
    assert re.search(
        r"a \*\*fail-and-fix\*\*, (?:not|rather than)\s+a designed\s+(?:state|outcome) "
        r"\(#10046\)",
        flat,
    ), (
        "the #10046 arm's VERDICT is unpinned — 'a **designed state**, not a "
        "fail-and-fix' leaves every other pin green (measured), which tells an "
        "operator to IGNORE genuine `no_tenant` growth from a session/JWT or "
        "OAuth `oat_` credential"
    )
    # The EVIDENCE, as PRESENCE plus CONTENT — never as symbol order or grammar.
    # Measured (#10083 review, P2): an order-anchored pattern red BOTH a reorder
    # and a `both put the` insertion, i.e. two improving edits, while accepting
    # `row NAME on the credential` — an assertion of the opposite of #10025's
    # ruling (`default_graph_id_for` reads `.id` and filters `status='active'`;
    # `record_tenant_for` refuses the `default` sentinel and demands a real
    # `g_<hex>`), which would send a reader to check the row's NAME — always
    # `default` on the healthy row — and clear real growth. Each symbol occurs
    # exactly once in the section, so presence cannot be satisfied elsewhere —
    # but the FIELD CLAUSE does not: it is word-for-word the ORG-WIDE arm's
    # sentence too (`resolve_api_key` puts the org's active `kind='default'` row
    # id on the credential as `default_graph_id`, line ~3515), so a bare
    # section-wide search let the #10046 arm's clause be DELETED, reworded to
    # `row on the credential`, or inverted to `puts no default_graph_id on the
    # credential at all` with the suite GREEN (measured, #10083 review P2).
    # Bind the clause to ITS OWN arm by a WINDOW after the one symbol only that
    # arm names — 56 chars to the clause, 538 to the org-wide copy — and check the
    # window's CONTENT order-free. An order-anchored pattern red a claim-preserving
    # reorder (`… stamp `default_graph_id` on the credential: the org's active
    # `kind='default'` row id …`) while its message said the arm "does not say
    # WHICH field is stamped" when it did (measured, #10083 review P2). Reorder,
    # insertion and grammar are all legitimate here; what must hold is that the
    # arm's own sentence names `row id` AND `default_graph_id`, never the NAME.
    assert "`hosted_api._session_user_org`" in flat, (
        "the #10046 arm names no session-lane resolution site, so a reader cannot "
        "check WHY a session/JWT credential is no longer designed residue"
    )
    assert "`oauth.resolve_oauth_access_token`" in flat, (
        "the #10046 arm names no OAuth resolution site, so a reader cannot check "
        "WHY an `oat_` credential is no longer designed residue"
    )
    _oauth_arm = flat.partition("`oauth.resolve_oauth_access_token`")[2][:260]
    assert re.search(r"\brow id\b", _oauth_arm) and "`default_graph_id`" in _oauth_arm, (
        "the #10046 arm does not say WHICH field is stamped (bound to that arm's "
        "own sentence, not the org-wide one) — #10025's ruling is that the id is "
        "identity and the name is a label, so 'the row' without `row id` sends "
        "the reader to qualify on the name"
    )
    # `re.I`: the inversion is the CLAIM (`name` instead of `id`), not the case
    # of the word. Case-sensitive, the same inversion in ordinary lowercase read
    # GREEN (measured: the pattern alone, on the lowercase reword). Scoped to the
    # same window so it cannot red a truthful mention of the NAME elsewhere (the
    # doc's own `never a row NAME` sits ~2 500 chars away, in the `g_<hex>`
    # sentence — the pattern alone is GREEN on the real doc; a truthful id-vs-NAME
    # clause INSERTED into this arm's own 260-char window is what red a bare-noun
    # `row name` (measured, #10083 review P2) — and the fix is to read the claim,
    # not to drop the pin).
    # The claim is a STAMPING claim: the NAME is put on the credential. So the
    # negation-governed mentions are stripped first, the way the ruling pin below
    # strips its noun-negated phrase: "— never the row's name on the credential —"
    # is the RULING, and a bare-noun pin red it with a message accusing the arm of
    # the inversion it had just refused (measured, #10083 review P2).
    # Span-bounded rather than preposition-gated, because the inversion does not
    # always keep the preposition in front: "… `default_graph_id` — that is, what
    # the credential actually carries is the row's NAME, not the id" read GREEN on
    # the preposition-gated form (measured, #10083 review P2), and an inversion
    # like "some lanes stamp the row's NAME onto the credential" keeps `row id`
    # earlier in the sentence, so the presence pin above cannot see it.
    _oauth_claim = re.sub(
        r"\b(?:never|not|no)\s+(?:an?\s+|the\s+)?row(?:['\u2019]s)?\s+name",
        " ",
        _oauth_arm,
        flags=re.I,
    )
    assert not re.search(
        # TWO-SIDED span, because the inversion is written both ways round: the
        # claim "... carries is the row's NAME, not the id" puts `credential`
        # BEFORE the name, so an after-only span read it GREEN (measured).
        r"\brow(?:['\u2019]s)?\s+name\b(?=[^.]{0,60}?\bthe\s+credential)"
        r"|\bthe\s+credential\b[^.]{0,60}?\brow(?:['\u2019]s)?\s+name\b",
        _oauth_claim,
        re.I,
    ), (
        "the #10046 arm says the row's NAME is stamped — the opposite of #10025's "
        "id-vs-name ruling, and the healthy row is NAMED `default`, so a reader "
        "checking the name clears real growth"
    )
    # ... and the graph-bound remedy's own EVIDENCE, for the same reason the
    # `circuit_open` evidence is pinned above: deleting the fail-soft clause leaves
    # the qualifier `except when tenant resolution itself faults` standing on
    # nothing, with the suite green (measured, #10083 review P2).
    assert re.search(r"`hosted_api\._resolve_record_tenant` is fail-soft, #5148", flat), (
        "the graph-bound remedy's fault exception states no evidence, so a reader "
        "cannot check that a resolution fault is what makes a bound capture land "
        "in `no_tenant`"
    )
    # SCOPED to the session/OAuth arm the pre-#10046 claim was about. Section-wide
    # this forbade a TRUE sentence about the self-host registry lane, whose
    # credential carries no `default_graph_id` at all (only the Supabase credential
    # resolvers stamp it) — a lane clarifying that lane red the suite with a
    # message describing the opposite (#10083 review P2, measured).
    assert not re.search(
        r"(?:session/JWT|`oat_`).{0,240}carries no\s+`default_graph_id` at all",
        flat,
    ), (
        "REVERT-DETECTOR (one draft's exact wording, not a class): the pre-#10046 "
        "'RESOLUTION gap at the credential' claim is back. The CLAIM is measured by "
        "the `session/JWT credential and an OAuth` pin above; keep this exact, "
        "cheap, and honestly labelled"
    )
    assert re.search(
        r"So make the capture \*\*graph-bound\*\* — a graph-bound \*\*control-plane\*\* "
        r"credential cannot land in `no_tenant` except when tenant resolution itself "
        r"faults",
        flat,
    ), (
        "the section gives no way to make the capture's own `no_tenant` "
        "impossible — a graph-bound control-plane credential is the discriminator, "
        "and dropping its fault exception restores the absolute that the fail-soft "
        "resolver (#5148) contradicts"
    )
    # ⛔ `graph-bound` alone is NOT the discriminator, and the assertion above is
    # satisfied by the FALSE claim. The control plane is. A graph-bound SELF-HOST
    # registry credential carries a Falkor `g_<hex>` node id, `record_tenant_for`
    # returns None for it by design (`control_plane_ids` is False for the
    # self-host resolver — record_writer.py:648-649), and no `public.graphs` row
    # exists to file it under — so "a graph-bound credential
    # cannot land in `no_tenant`" is false, and the paragraph above it lists that
    # very lane as a designed `no_tenant` producer. Measured (#10071 review P2):
    # restoring that absolute wording — and separately deleting the counter-example
    # — left the suite at `99 passed`. The qualifier and the lane that proves it
    # are the claim, so pin both. The `control-plane` qualifier is pinned by the
    # assertion above; this is the COUNTER-EXAMPLE half, which had no pin of its
    # own — merged in from `origin/main`, whose single pattern anchored both halves
    # on the pre-#10083 wording of the sentence (`… cannot land in `no_tenant` (the
    # self-host registry lane above is a graph-bound key that resolves through no
    # control plane …`), which this PR's tighter bound rewrote.
    assert re.search(
        r"The self-host registry lane above is the (?:one|only) graph-bound key that "
        r"resolves through no control plane",
        flat,
    ), (
        "the `no_tenant` bound is stated as an absolute again — a graph-bound "
        "self-host registry credential is REFUSED and counted `no_tenant` by design "
        "(`record_tenant_for` returns None for it, and it can never satisfy the "
        "`public.graphs` FK), so the section tells an operator to read a designed "
        "outcome as a fault; the counter-example that makes the qualifier "
        "checkable is missing or unravelled"
    )
    # ⛔ The #9192 declaration is a POLARITY, and only the clause's SHAPE was pinned:
    # `unless` is what makes a DECLARED self-host single-tenant process stop counting
    # `no_tenant` (`tortoise/mcp_auth.py:99-110` arms it by declaration;
    # `record_writer.record_tenant_for` then names the pair), so inverting it to
    # `when` — i.e. asserting the declaration CAUSES `no_tenant`, the opposite of the
    # code — left every pin green (#10083 review, P2, measured). Pin the direction
    # AND forbid the inversion, or F5's correction is unpinned prose.
    assert re.search(
        r"counted there\s+\*\*unless the process (?:has )?declare[sd] the "
        r"single-tenant pair\*\*",
        flat,
    ), (
        "the self-host lane's `unless` is unpinned — the #9192 declaration is what "
        "CLEARS its `no_tenant`, so a reword that drops or inverts it makes the "
        "section state the opposite of `mcp_auth.py:99-110`"
    )
    assert not re.search(
        r"counted there\s+\*\*(?:when|if) the process has declared", flat
    ), (
        "the self-host arm is INVERTED: a declared single-tenant process is the one "
        "case #9192 ARMS with the pair (it stops counting `no_tenant`), so 'when "
        "declared' reads the ruling backwards"
    )
    # The "do not attribute growth per request" ruling is pinned ONCE, below, by a
    # sentence-scoped scan — the narrow negative pin that lived here
    # (`attribute (?:any|a|each|the) `no_tenant` growth`) required an article and the
    # backticked counter, so it stayed green on `Attribute any `no_tenant` growth …`
    # and on the document's own `Then attribute that growth per request …`
    # (#10083 review cycle 2, P2). One stronger pin, not two overlapping ones.
    # ⛔ `only` is not exhaustive without the fault arm: `_resolve_record_tenant`
    # is fail-soft (#5148) and returns `None` on ANY exception, so an org-wide key
    # whose resolution faults also lands in `no_tenant`. Dropping that clause
    # restored the absolute this PR exists to remove, with every other pin green
    # (#10083 review P2, measured) — so it is pinned here.
    assert re.search(
        r"such row \(absent, or the `default` sentinel\) — \*\*or\*\* when the\s+"
        r"resolution itself faults, since the fail-soft resolver below turns any\s+"
        r"exception into `None`",
        flat,
    ), (
        "the org-wide-key sentence reads `only` when the control plane could name "
        "no row — but the fail-soft resolver turns any exception into `None`, so "
        "an org-wide key can also land in `no_tenant` by fault"
    )
    # Each COUNTING SITE the arm names, pinned individually. `record_tenant_for|
    # _bind_record_tenant` was one alternation, and the bare `_bind_record_tenant`
    # token is satisfied by this section's own seed/indexing sentence — so the
    # self-host clause could be deleted with the pin still green (#10083 review,
    # measured). Both halves are now anchored on their own clause.
    assert re.search(r"`record_writer\.record_tenant_for` returns `None` by design", flat), (
        "the bounded arm no longer names the self-host lane's counting site, so a "
        "reader cannot check the claim that `no_tenant` is designed there"
    )
    # ⛔ The seed and background-indexing runners are NOT designed `no_tenant`
    # sources: #10087 bound them to the org's DEFAULT-graph tenant at their own
    # construction site (`hosted_api._bind_default_graph_record_tenant`, called
    # from `_run_onboarding_seed` / `_run_starter_seed` / the indexing jobs), and
    # `tests/test_10047_record_every_lane.py::test_the_onboarding_seed_runner_binds_
    # the_default_graph_tenant` proves it. Filing them under "designed residue" is
    # the same fail-open the org-wide-key correction above removes — it excuses
    # real growth — and nothing pinned the list's MEMBERS, only its header
    # (#10083 review P1).
    assert re.search(
        r"The \*\*onboarding-seed and background-indexing runners are NOT one of\s+"
        r"those sources either\*\*, since #10087",
        flat,
    ), (
        "the ⚠️ list files the seed / background-indexing runners as DESIGNED "
        "`no_tenant` sources, which #10087 made false: they are bound to the org's "
        "default-graph tenant, so a `no_tenant` there is a control-plane fault"
    )
    assert not re.search(r"deliberately NOT bound", flat), (
        "REVERT-DETECTOR (one draft's exact wording, not a class): the pre-#10087 "
        "'deliberately NOT bound' claim is back. The claim it stood on is pinned "
        "positively by the `NOT one of those sources either` assertion above"
    )
    # ⛔ The runners arm's VERDICT and MECHANISM, for the reason the #10046 arm
    # needed both (#10083 review P2, measured): the stance sentence above survived
    # `i.e. the same fail-and-fix as an org-wide key` → `i.e. a designed outcome,
    # not a fault`, `they carry the org's DEFAULT-graph tenant` → `they carry no
    # tenant at all`, and deletion of the `_bind_default_graph_record_tenant`
    # evidence — every one GREEN. That inverts the arm's conclusion while its
    # stance sentence still reads correctly, i.e. it tells an operator to IGNORE a
    # real control-plane fault on the seed / indexing lanes.
    assert re.search(
        r"is therefore a control-plane (?:fault|failure) \(no[^)]*\), i\.e\. "
        r"the same fail-and-fix as\s+an org-wide key",
        flat,
    ), (
        "the runners arm's VERDICT is unpinned — 'i.e. a designed outcome, not a "
        "fault' leaves every other pin green (measured), which reads a real "
        "control-plane fault on the seed / indexing lanes as designed residue"
    )
    # Redundant AND false-reding as a section-wide detector: the ⚠️ bound itself
    # calls the self-host lane's `no_tenant` "the designed outcome", so a truthful
    # "a designed outcome, not a fault, for that lane" red it with a message about
    # the RUNNERS arm (measured, #10083 review P2). The inversion it was for is
    # already red by the positive verdict pin above; this keeps the explicit
    # forbidding but binds it to the runners arm's own `control-plane` clause.
    assert not re.search(
        r"control-plane (?:fault|failure)[^.]{0,300}designed (?:outcome|residue), not a fault",
        flat,
    ), (
        "REVERT-DETECTOR (one measured inversion, not a class): the runners arm is "
        "stated backwards — a `no_tenant` there is a control-plane fault, so "
        "calling it designed residue is the fail-open read the bound exists to close"
    )
    assert re.search(
        r"bound by\s+`hosted_api\._bind_default_graph_record_tenant` \(#10047\) "
        r"from the SAME\s+`kind='default'` `public\.graphs` row an org-wide key is "
        r"named from",
        flat,
    ), (
        "the runners arm names no binding site, so a reader cannot check WHY those "
        "lanes are bound — deleting the mechanism leaves the verdict resting on "
        "nothing (measured green at the previous head)"
    )
    assert re.search(
        r"so they carry the org's DEFAULT-graph tenant from\s+their own construction "
        r"site",
        flat,
    ), (
        "the runners arm stops saying those lanes CARRY a tenant — the fact the "
        "arm's verdict rests on. Inverted to 'they carry no tenant at all' it reads "
        "as designed residue with every other pin green (measured)"
    )
    # The ⚠️ list files the self-host registry lane under "a credential that names
    # no graph", while the sentence below calls that same lane "the one graph-bound
    # key" — the two cannot both be read as true, and the CODE says the second is:
    # the lane's credential DOES carry a `graph_id` (a Falkor `:Graph` node id,
    # `g_<hex>`), which is exactly why `record_tenant_for` refuses it — registry
    # graphs are never inserted into `public.graphs`, so the record cannot key on
    # it (`tortoise/record_writer.py`, `control_plane_ids=False`; #9192's table
    # agrees). Measured: without this pin the qualifier could be dropped back to
    # "names no graph" and every other assertion stayed green (#10083 review F5).
    assert re.search(r"a credential that names no graph \*\*the record can key on\*\*", flat), (
        "the ⚠️ list calls the self-host registry lane a credential that names NO "
        "graph while the remedy below calls it the one GRAPH-BOUND key — that "
        "lane's credential does carry a graph_id, just not one the record can key on"
    )
    # The bounded arm must be EXECUTABLE. The first draft sent the operator to
    # "attribute any `no_tenant` growth … through recipe 1's log line", but that
    # line (`record_writer.write_live_record`, the `no_tenant` branch) logs only
    # the env-pair note — no request identity, no org/graph id, not even the
    # record type — so per-request attribution is impossible from the log and the
    # arm's only remaining branch reds a healthy deployment (#9585 review P2).
    assert re.search(r"\*\*no\*\* request identity", flat), (
        "the section claims the `no_tenant` log line can attribute growth to a "
        "request — it carries no request identity, and of the four "
        "`record_writer` reasons only `circuit_open` carries the record type, so "
        "that step cannot be performed"
    )
    # The message above states the EVIDENCE for the impossibility (which reasons
    # carry what), so pin the evidence too — otherwise the parenthetical can be
    # deleted and the claim stands on an assertion that no longer measures it
    # (#10071 review P2, verified: `circuit_open` logs `record.get("type")` in its
    # WARN; `malformed_dsn` and `write_failed` log the exception. Line numbers are
    # deliberately not cited — they rot, and the earlier attempt cited the
    # `no_durable_journal` DEBUG message at that line instead, #10083 review P2).
    # (`origin/main` re-pointed this same citation at record_writer.py:1218 while
    # this branch removed the citation instead; the removal supersedes the
    # re-point, so no line is named here and the correction cannot rot again).
    assert re.search(r"`circuit_open` is the only one that carries the record type", flat), (
        "the section gives no evidence for why the log cannot attribute growth — "
        "only `circuit_open` carries the record type, so a reader cannot check "
        "the claim it is asked to act on"
    )
    # ⛔ Pin the CLAUSE, not the token: `capture ran **unbound**` alone survives
    # both "it is **never** a fail-and-fix when the capture ran **unbound**" and
    # "fail-and-fix whether or not the capture ran **unbound**" — the arm inverted
    # with the pin still green (#10071 review P1). `only when` is load-bearing —
    # and so is the DISJUNCTION: dropping `, when item 1's row did not appear`
    # leaves every other pin green while the paragraph stops covering the bound
    # capture whose row never landed (#10071 review F2), and the third disjunct is
    # what keeps item 2 (`the capture's OWN` — see above) from contradicting a bound
    # capture whose tenant RESOLUTION faulted, since such a write does count
    # `no_tenant` (#10083 review F3). Pin all three parts.
    assert re.search(
        r"fail-and-fix only when the capture ran \*\*unbound\*\*, when item 1's "
        r"row did not appear, or when the capture's credential was refused a "
        r"tenant the record CAN key on",
        flat,
        re.I,
    ), (
        "the section gives no executable discriminator for `no_tenant` growth — "
        "an unbound capture makes the growth the capture's, a bound capture whose "
        "item-1 row never appeared is the second case, and a credential the record "
        "CAN key on being REFUSED a tenant is the third"
    )
    # ⛔ The third arm must be WINDOW-scoped, not OWNERSHIP-scoped: the sentence used
    # to read "when the capture's OWN tenant resolution faulted", which asks the
    # operator to decide that a fault line was the capture's — the ownership
    # judgement the ⛔ ruling below ("no request identity") declares impossible
    # (#10083 review cycle 3, P2, measured: inverting it left all 99 green).
    assert not re.search(r"the capture's OWN tenant resolution", flat), (
        "REVERT-DETECTOR (one draft's exact wording, not a class): the ownership-scoped "
        "third arm is back. A window-scoped reword ('when the capture's tenant "
        "resolution faulted') stays green here by design — this pin cannot see that "
        "class; the WINDOW scoping is measured by the `fail-and-fix only when` pin "
        "above, whose third disjunct names the credential, not a request"
    )
    # The third disjunct must name a command that actually FINDS the fault. Recipe 1
    # greps `the record write did not happen`, and this fault is logged under a
    # DIFFERENT MESSAGE — "could not resolve the record tenant for this request"
    # (`hosted_api.py`, `_resolve_record_tenant`) — so that grep never matches it:
    # an operator who greps recipe 1, sees nothing and clears the capture passes a
    # capture-own `no_tenant` growth as other traffic — a fail-open in the very
    # verification this section exists to provide (#10083 review P1).
    # ⛔ TWO causes, TWO readings — and the second one is SILENT. `no nameable
    # default row` is a refusal of a tenant the record CAN key on, so it is a
    # fail-and-fix; but `_bind_default_graph_record_tenant` returns without logging
    # (`if not default_graph_id: return`, hosted_api.py), so on the seed/indexing
    # lane NO line exists for the operator to find. A section that offered only the
    # fault grep would present an empty read as "no fault" on a fail-and-fix case —
    # the same fail-open, one cause further out (#10083 review cycle 3, P1).
    # ⛔ The VERDICT is the load-bearing half, and it was pinned by a bare 3-word
    # token (`No nameable default row`) that cannot see it (measured at this head:
    # `so it is a fail-and-fix as well` → `so it is NOT a fail-and-fix at all`
    # left all 100 green — #10083 review P2, unfixed across cycles 3-4). A
    # reading whose verdict can be inverted silently is the exact class this PR
    # closes: the arm would then contradict the summary sentence above it
    # (`… it is a fail-and-fix only when … a tenant the record CAN key on`).
    # The LEAD-IN is pinned with it: the count over the causes is what makes the
    # SILENT readings executable — a lead-in that reads fewer causes lets the
    # operator skip one and read an empty grep as 'no fault'. The count was WRONG
    # (`TWO readings, because it has two causes`, pre-fix) while the arm had a
    # third source of the same refusal, which is the fail-open this bound exists to
    # close one cause further out (#10083 review, P2).
    # ⛔ These two pins anchor the RULING and stop short of the rest of the
    # sentence (#10083 review cycle 5, P3, measured): the parenthetical is an
    # ENUMERATION (`absent, inactive, or the `default` sentinel`) and the tail of
    # the lead-in is prose, neither of which carries the verdict — so a strictly
    # IMPROVING edit, e.g. adding `tombstoned` (the word the code's own docstring
    # uses, `hosted_api._bind_record_tenant`), reddened a verdict pin that was
    # still intact and told the next lane to restore a verdict it never removed.
    # A pin must red on the class its message names.
    # ⛔ The lead-in's numeral is DERIVED from the bullets that follow it, not
    # pinned as a WORD and not counted through a fixed name list (#10083 review F1,
    # P2, measured). Both halves of that pin were fail-open in one direction and
    # false-red in the other:
    #   - a fixed-name alternation (`A resolution FAULT|No nameable default row|A
    #     resolution ABANDONED`) can only ever return names from that set, so
    #     `len(...) == 3` meant "the three original names are still present". A
    #     FOURTH bullet — e.g. `A malformed CARRIED tenant`, a cause this section
    #     itself describes — stayed GREEN while the lead-in still promised an
    #     exhaustive THREE;
    #   - a lane that adds a cause AND honestly relabels to `FOUR readings … read
    #     all FOUR` was RED against the literal `THREE`, with a message telling it
    #     the lead-in had stopped saying to read every cause — the false-red class
    #     this PR spent cycles 7-16 removing.
    # So parse the numerals out of the lead-in and compare them to the members that
    # actually follow it. An honest FOUR is green; a silent fourth member is not.
    # The LEAD-IN's casing is not part of its claim, so all three numerals are
    # read case-insensitively (#10083 review cycle 18, P3, measured). The middle
    # group alone was `([a-z]+)` while the two around it were `([A-Z]+)` and the
    # very next line `.upper()`ed every group for the `_NUMERALS` lookup — i.e.
    # any-casing was the intent and the pattern said otherwise. An honest relabel
    # in the section's OWN count style (`FOUR readings, because it has FOUR
    # causes; read all FOUR`) therefore missed `([a-z]+) causes`, and the pin
    # reddened on a strict strengthening with a message accusing the lead-in of
    # dropping the very clause it had just added — the F2 casing false-red this
    # PR spent cycle 17 removing ten lines below. Casing is not the claim: the
    # agreement of the three numerals with each other and with the member count
    # below is, and that is untouched by this widening.
    _lead_in = re.search(
        r"That last arm has ([A-Za-z]+) readings, because it has ([A-Za-z]+) causes; "
        r"read all ([A-Za-z]+) before you call the growth other traffic:",
        flat,
    )
    assert _lead_in, (
        "the no-nameable-row lead-in no longer says to read all N causes before "
        "calling the growth other traffic, so a SILENT reading can be skipped and "
        "an empty grep read as 'no fault'"
    )
    _lead_nums = {_NUMERALS.get(_word.upper()) for _word in _lead_in.groups()}
    assert len(_lead_nums) == 1 and None not in _lead_nums, (
        "the bound's own lead-in counts its causes inconsistently "
        f"{_lead_in.groups()!r} — 'N readings … N causes … read all N' is the claim "
        "that makes every reading executable"
    )
    # Count the bullets from the LIST ITSELF, scoped to the block the lead-in
    # introduces: a `- **…**` bullet elsewhere in the section is not a cause and
    # must not be able to red this.
    # ⛔ And read that list off `flat`, not the RAW section (#10083 review cycle 19,
    # P3, measured). This was the one piece of the counting machinery that had not
    # moved to the normalised copy, although its own sibling states the principle
    # (`_c_msgs`: "read off `flat`, so a word-preserving refill cannot move it").
    # Measured: changing ONLY the leading whitespace of the three bullets (3 → 4
    # spaces, not one word touched) enumerated 0 and red with "the lead-in says
    # THREE readings but the list enumerates 0" — a claim-preserving indentation
    # edit accused of a defect it does not have, and the indent was ALSO part of
    # the raw window marker (`"\n   ⛔"`), where an indent change silently WIDENS
    # the window instead of failing. Indent is a rendering-level choice, not the
    # claim (CommonMark 0.31.2 §5.2: a list item is `<1-4 spaces><marker>`, so the
    # nesting indent below the marker is layout), and the claim this pin measures
    # is "the lead-in's numeral equals the number of members". So the window and
    # the bullet pattern both come from `flat`, where any indent is a single space
    # and the count cannot move on a reflow or a re-indent.
    _bound_list = flat.partition("before you call the growth other traffic:")[2]
    _bound_list, _closed, _ = _bound_list.partition("⛔")
    if not _closed:
        _bound_list = _bound_list[:2500]
    _bound_bullets = re.findall(r"- \*\*(.+?)\*\*", _bound_list)
    assert _lead_nums == {len(_bound_bullets)}, (
        f"the `no_tenant` bound's lead-in says {_lead_in.group(1)} readings but the "
        f"list enumerates {len(_bound_bullets)} ({_bound_bullets!r}) — the lead-in's "
        "count and its members disagree, so an operator reads an exhaustive list "
        "that is not exhaustive and skips a cause"
    )
    assert re.search(
        r"\*\*No nameable default row\*\* \(absent[^)]*\) is also a refusal of a "
        r"tenant the record CAN key on, so it (?:too )?is a fail-and-fix as well",
        flat,
    ), (
        "the no-nameable-row arm's VERDICT is unpinned — it must be a refusal of "
        "a tenant the record CAN key on, and therefore a fail-and-fix"
    )
    assert re.search(
        r"on the seed / indexing lane it logs \*\*NOTHING\*\*\s+at all", flat
    ), (
        "the section does not say the no-nameable-row arm is SILENT on the "
        "seed / indexing lane, so an empty grep read is taken as 'no fault'"
    )
    assert re.search(r"empty read of that grep does not clear\s+this arm", flat), (
        "the section does not rule out the empty-read misreading — read the "
        "`kind='default'` row, not the log"
    )
    # ⛔ And the THIRD cause's verdict, which is the one no other reading can
    # reach: the resolution was ABANDONED at the seam, so the row reading PASSES on
    # it. Without this the bound is again a two-reading procedure over a three-cause
    # arm, and an operator who reads BOTH as prescribed is left with the paragraph's
    # own verdict — "other traffic" — for a fail-and-fix state (#10083 review, P2).
    assert re.search(
        r"\*\*A resolution ABANDONED at the seam\*\* is (?:the [A-Za-z]+|a) "
        r"cause, and it is "
        r"the one the row reading above CANNOT clear",
        flat,
    ), (
        "the abandoned-at-the-seam cause is not named as one the row reading cannot "
        "clear — its fault is logged BEFORE the row is read, so a healthy "
        "`kind='default'` row is exactly what that cause looks like"
    )
    assert re.search(
        r"the `kind='default'` row the reading above inspects is \*\*untouched and "
        r"healthy\*\*",
        flat,
    ), (
        "the section does not say WHY the row reading passes on this cause — an "
        "operator who finds a healthy row concludes the growth is other traffic"
    )
    assert re.search(
        r"and the growth is still the deployment's\s+\(control-plane saturation, "
        r"or a refused submission\), never another tenant's",
        flat,
    ), (
        "the abandoned-at-the-seam arm's VERDICT is unpinned — the growth must be "
        "the deployment's own fault, not another tenant's traffic"
    )
    # ⛔ `_cp_offload` has TWO outcomes and they differ in what the row reading
    # above can SEE, so one consequence cannot cover both (#10083 review P2,
    # measured — the last-cycle finding). `ControlPlaneOffloadError.refused` is
    # `True` only for a refusal or a cancellation before any worker ran the
    # callable (`monitoring.py:2317-2322`), where the callable did not and will
    # not run; a plain BOUND MISS on a submission a worker already picked up is
    # `refused=False`, `_cp_offload` returns `None` (`hosted_api.py:8734-8736`),
    # and that worker runs the resolver to completion — `asyncio.wait_for`
    # cancels the await, never the thread (CPython #87185). The pre-fix paragraph
    # collapsed both into "the resolution never ran", which told an operator that
    # one of the four fault lines (c) enumerates cannot coexist with the offload
    # warning, and that the default row is untouched when this lane's resolver is
    # `default_graph_id_for_credential`, which MINTS on a miss — so the row
    # reading's PASS can be produced by the very resolution the seam stopped
    # waiting for. Pin both branches; a single-branch claim reds here.
    _seam = flat.partition("**A resolution ABANDONED at the seam**")[2]
    _seam, _closed, _ = _seam.partition("⛔ **Do not try to attribute")
    if not _closed:
        _seam = _seam[:3000]
    assert re.search(r"before any\s+worker\s+picked it up", _seam), (
        "the abandoned-at-the-seam cause does not name the REFUSED / cancelled-\n"
        "before-a-worker-ran branch, which is the only one where the callable\n"
        "genuinely did not run (`ControlPlaneOffloadError.refused`)"
    )
    assert re.search(r"a worker\s+already PICKED UP", _seam) and re.search(
        r"still runs\s+the resolver to completion", _seam
    ), (
        "the section collapses a plain BOUND MISS into 'the resolution never ran'\n"
        "— on a submission already picked up the worker still runs the resolver\n"
        "to completion (`hosted_api.py:8734-8736`, CPython #87185), so a\n"
        "resolution-fault line CAN coexist with the offload warning and an\n"
        "operator told otherwise stops looking"
    )
    assert re.search(r"\*\*mints on a miss\*\*", _seam), (
        "the section does not say the row reading can PASS because the abandoned\n"
        "resolution CREATED the row — this lane's resolver is\n"
        "`default_graph_id_for_credential`, which mints on a miss, so 'untouched\n"
        "and healthy' is only true of the refused branch"
    )
    # ...and the false ABSOLUTE must not come back: a bound miss is the branch
    # where the resolution DID run. The pre-fix wording paired an unconditional
    # "wait bound expires" with "never ran" in ONE sentence, which is the defect
    # this pin measures (it reds on that revert, not on a reword that keeps the
    # two branches apart).
    assert not re.search(
        r"wait bound\s+expires[^.]{0,300}\*\*never ran\*\*", _seam, re.I
    ), (
        "the section again states that an EXPIRED WAIT BOUND means the resolution\n"
        "never ran — it does not: the bound abandons only the await, and a worker\n"
        "that picked the submission up runs it to completion (CPython #87185)"
    )
    # ⛔ And the REMEDY itself, not just its lead-in: "read the row" is the only
    # executable step for the silent arm, and it was unpinned (measured: replacing it
    # with "read whatever the operator finds most convenient" left the suite green,
    # #10083 review cycle 4 P2). The QUALIFIER is on the `id`, not the name: the
    # minted row is NAMED `default` (`supabase_control.ensure_default_graph_row`,
    # and the migration's own backfill), and the `default` sentinel is a pre-#10025
    # value of the ID field (`record_tenant_for` refuses `default_graph_id ==
    # "default"`). Qualifying on the name would reject the healthy row.
    assert re.search(
        r"read the (?:org|organi[sz]ation)'s `kind='default'` `public.graphs` row "
        r"itself", flat
    ), (
        "the silent arm's remedy is not named — the operator is left with the "
        "empty grep the paragraph above says does not clear it"
    )
    assert re.search(r"its `id` must be a real `g_<hex>`", flat), (
        "the remedy does not say what a healthy default row looks like, so "
        "'read the row' is unactionable"
    )
    # ...and the OTHER half of "healthy": a default row that is present but not
    # `active` is precisely the no-nameable-default-row case the arm is about, so a
    # remedy naming only the id form leaves the operator free to read a paused row as
    # healthy. Measured: inverting it to `inactive` left every pin green (#10083
    # review, P3).
    assert re.search(r"its `status` (?:must be|must read) `active`", flat), (
        "the remedy never says the default row must be `active` — the silent arm's "
        "whole point is a row that exists but names nothing, so an inactive row read "
        "as healthy clears the growth a control-plane fault produced"
    )
    # The BOUND's own asymmetry: `no_tenant` is qualified and the other two reasons
    # are not, and the section must say WHY rather than leave a reader to infer it
    # from the process-global sentence above. Without the clause, `circuit_open` /
    # `malformed_dsn` read as absolute reds on a before/after delta the section has
    # just conceded covers other traffic — the same contradiction F1 removes one
    # paragraph up (#10083 review, P3).
    assert re.search(r"That excuse covers\s+`no_tenant` and nothing else", flat), (
        "the ⚠️ bound never says the process-global excuse is `no_tenant`-only, so "
        "item 2's absolute bar for `circuit_open` / `malformed_dsn` reads as the "
        "same unbounded claim the bound just qualified"
    )
    assert re.search(
        r"`circuit_open` reads a process-wide breaker\s+and `malformed_dsn` a "
        r"process-wide setting",
        flat,
    ), (
        "the section gives no reason the other two arms are NOT bound — the BREAKER "
        "and the DSN are process-wide, so other traffic cannot excuse a growth in "
        "either (it runs behind the same breaker and the same DSN)"
    )
    assert not re.search(r"not the `default` name sentinel", flat), (
        "REVERT-DETECTOR (one draft's exact wording, not a class): the pre-fix sentence "
        "is back. A genuinely name-qualifying rewrite stays green here, so the "
        "asymmetry is pinned POSITIVELY by the `never a row NAME` assertion below"
    )
    # ... and the positive half, so the asymmetry is a claim rather than only the
    # absence of one draft's sentence (#10083 review, P3: the negative pin above
    # reds on a revert, not on a rewrite that makes the same false qualification).
    assert re.search(
        r"the `default` SENTINEL is a pre-#10025\s+id value, (?:never|not) a row "
        r"NAME", flat
    ), (
        "the remedy no longer says WHICH field the sentinel lives in — the minted "
        "`kind='default'` row IS named `default` and its id is a real `g_<hex>`, so "
        "without the id/name asymmetry an operator rejects the healthy row"
    )
    # ⛔ The command must match EVERY message that carries a resolution fault, not
    # just the per-request one. There are three: `hosted_api._resolve_record_tenant`
    # ("could not resolve the record tenant for this request"),
    # `hosted_api._bind_default_graph_record_tenant` ("could not resolve the
    # DEFAULT-graph record tenant for org …", the seed / indexing lanes), and
    # `supabase_control.default_graph_id_for` ("graphs default-row read failed …"),
    # which SWALLOWS its exception and returns None — so on a default-row read
    # fault neither hosted_api line is emitted at all. The mint failures warn under
    # their own message ("default graph(s) row NOT minted …", the org create/rename
    # paths). A narrower grep sends the operator to an empty read on some of them,
    # i.e. "no fault" on a fail-and-fix `no_tenant` growth (#10083 review cycle 2 P1,
    # widened in cycle 3).
    # A SET of required alternatives, not the whole command: a lane WIDENING the
    # grep with another reachable message (`could not bind the record tenant for
    # this request`, `hosted_api.py:6992`) is the improvement this bullet exists to
    # encourage, and the full-command form of this pin red it while its message
    # claimed a message was MISSING (#10083 review P3, measured).
    # Quoting style and flag letters are NOT the claim: `grep -Ei`, `grep -nE` and a
    # single-quoted body keep every required message, and each red the fixed
    # `grep -E "` form of this capture with a message claiming the grep is absent
    # (measured, #10083 review P3). Capture the pattern body regardless of where the
    # flags sit or how the body is quoted.
    _fault_grep = next(
        (g for _q, g in re.findall(
            r"""grep\s+(?:-[a-z]*\s+)*-[a-z]*E[a-z]*\s*(["'])(.+?)\1""", flat)
         if "could not resolve the record tenant" in g or "graphs default-row" in g),
        "",
    )
    assert _fault_grep, (
        "the fault disjunct carries no `grep -E` whose pattern names a resolution-"
        "fault message"
    )
    for _alt in (
        "could not resolve the record tenant",
        "could not resolve the DEFAULT-graph record tenant",
        "graphs default-row read failed",
        "default graphs? row NOT minted",
        "control-plane offload 'default_graph_row' failed",
    ):
        assert re.search(re.escape(_alt), _fault_grep), (
            f"the fault disjunct's grep misses {_alt!r}, so that fault reads as "
            "'no fault'"
        )
    # ⛔ Scoped to the (c) PARAGRAPH, not the whole section: the same literals
    # appear elsewhere in this section, so a section-wide search let (c) stop naming
    # a member while the pin stayed green (#10083 review cycle 3, P2, measured).
    # ALL THREE members are pinned — the per-request one was missing from this
    # tuple, so it could be deleted (or reworded to a literal the code never emits)
    # with the suite green (#10083 review cycle 4, P2, measured). A FOURTH (real)
    # message is not a defect and is not forbidden; what is pinned is that these
    # three — the ones `hosted_api`/`supabase_control` actually emit — are named,
    # and that the count word still agrees with the member count.
    # `partition`, NEVER `.index()`: the closing heading is prose no assertion
    # pins, so a reword of it made this a `ValueError: substring not found`
    # ERROR at collection — before any assertion ran, on an edit that changed no
    # claim (measured, #10083 review P2). Absent marker now WIDENS the window
    # (all the member pins still measure, and a widened window can only make a
    # `not re.search` red for inspection) instead of crashing.
    _excl_c = flat.partition("(c) The tenant-RESOLUTION faults")[2]
    _excl_c, _closed, _ = _excl_c.partition(
        "⛔ **`no_journal` and `seam_unimportable` ARE in it**"
    )
    if not _closed:
        _excl_c = _excl_c[:2500]
    # ⛔ The label's COUNT is DERIVED from the members it counts, not compared as
    # two test constants against the doc's literal (#10083 review cycle 18, P3,
    # measured). `len(_msgs) == 4` is THIS file's own tuple and `four MESSAGES`
    # was the doc's own word, so neither side was read from the section — the
    # exact F1 shape cycle 17 removed from the bound's bullets
    # (`_lead_nums == {len(_bound_bullets)}`) and from the exclusion list's
    # markers (`_NUMERALS[...] == len(_members)`). Both directions were wrong:
    #   - a TRUTHFUL fifth message plus an honest `four`→`five` relabel red the
    #     suite (here and at the member-presence pin below, and again on the two
    #     inversion pins, which also hard-coded the word `four`), accusing a
    #     section of disagreeing with a count it had just corrected — the
    #     false-red class this PR spent cycles 7-17 removing;
    #   - a SILENT fifth message (label untouched) stayed GREEN, so `four
    #     MESSAGES` counted four over five members — the fail-open the exclusion
    #     list exists to close, since an operator reads an "exhaustive" list that
    #     is not exhaustive and skips a cause.
    # What a MEMBER is, mechanically: a backticked log message followed by the
    # parenthetical naming the emitter that logs it (`_resolve_record_tenant`,
    # `_bind_default_graph_record_tenant`, `default_graph_id_for`, `_cp_offload`).
    # That shape is what distinguishes a member from the identifiers sharing this
    # paragraph (`None`, `grep -E`, `no_journal`, `tortoise.hosted_api`), and it is
    # read off `flat`, so a word-preserving refill cannot move it.
    # ⛔ CASE is not the claim (#10083 review P2, measured): `MESSAGES` → `messages`
    # is a pure typographic edit that keeps the count, and it red this label pin
    # with a message accusing the section of not counting its messages. The
    # NUMERAL is what is read (and `.upper()`ed below), so the WORD's casing must
    # not be able to red it.
    _c_label = re.search(
        r"\(c\) The tenant-RESOLUTION faults — ([A-Za-z]+) MESSAGES", flat, re.I
    )
    assert _c_label and _c_label.group(1).upper() in _NUMERALS, (
        "the exclusion (c) label no longer counts its emitted messages, so a "
        "reader cannot tell how many fault lines to look for"
    )
    _c_count_word = re.escape(_c_label.group(1))
    _c_msgs = re.findall(r"`([^`]+)` \(", _excl_c)
    assert _NUMERALS[_c_label.group(1).upper()] == len(_c_msgs), (
        f"the exclusion (c) is labelled {_c_label.group(1).upper()} MESSAGES but "
        f"enumerates {len(_c_msgs)} ({_c_msgs!r}) — a label that says FOUR over "
        "five members is the same self-contradiction class this section exists "
        "to remove, so a reader takes an exhaustive enumeration that is not "
        "exhaustive and skips a cause"
    )
    # ⛔ ... and the two CROSS-REFERENCES that point AT (c) carry its count in words,
    # and they were the last FREE-FLOATING numerals in this section (#10083 review
    # cycle 19, P2, measured). The label's numeral is derived from its members
    # (`_c_label` vs `_c_msgs`) and the bound's lead-in from its bullets
    # (`_lead_nums` vs `_bound_bullets`), but nothing read `logs one of FOUR
    # messages` (the bound's fault bullet) or `which matches all four` (the tail of
    # (c) itself), so:
    #   - relabelling (c) honestly to FIVE after adding a fifth reachable fault
    #     message — a strictly IMPROVING widening, the very edit the fault-grep pin
    #     above says it encourages — left both cross-references saying four, and
    #     the operator reads the fifth fault line as "no fault" on a fail-and-fix
    #     arm; and
    #   - `FOUR` → `TWO` / `matches all four` → `matches three of them` stayed
    #     GREEN with the members untouched, i.e. the numeral could go stale on its
    #     own.
    # The numeral is DERIVED from the same source the label is (`len(_c_msgs)`),
    # never hard-coded, so an honest relabel keeps the three agreeing and stays
    # GREEN — the agreement is the claim, and the wording around the numeral is
    # not pinned beyond what identifies which set it counts.
    _fault_refs = (
        ("the ⚠️ `no_tenant` bound's fault bullet",
         _bound_list, r"\bone of ([A-Za-z]+) messages\b"),
        ("the tail of recipe 1's exclusion (c)",
         _excl_c, r"\bwhich matches all ([A-Za-z]+)\b"),
    )
    for _where, _text, _num_re in _fault_refs:
        _ref = re.search(_num_re, _text)
        assert _ref and _ref.group(1).upper() in _NUMERALS, (
            f"{_where} no longer states how many fault messages the fault grep "
            "matches in a readable form (`one of N messages` / `which matches "
            "all N`) — an unpinned count can go stale against the list it "
            "points at and a real fault line reads as 'no fault'"
        )
        assert _NUMERALS[_ref.group(1).upper()] == len(_c_msgs), (
            f"{_where} says {_ref.group(1).upper()} but recipe 1's exclusion (c) "
            f"enumerates {len(_c_msgs)} ({_c_msgs!r}) — the cross-reference is "
            "stale, so an operator grepping for the messages (c) names is told "
            "the set is smaller than it is and a fault line the list names "
            "reads as 'no fault'"
        )
    # Deletion of any ONE member still reds on its own literal, independently of
    # the count above.
    _msgs = (r"could not resolve the record tenant for this request",
             r"could not resolve the DEFAULT-graph record tenant for org",
             r"graphs default-row read failed — the record tenant stays unnamed",
             r"control-plane offload 'default_graph_row' failed")
    for _msg in _msgs:
        assert re.search(re.escape(_msg), _excl_c), (
            f"recipe 1's exclusion (c) does not name {_msg!r} among the resolution "
            "faults, so a fault line an operator must grep is missing from the "
            "list that claims to enumerate them"
        )
    # ⛔ The ENUMERATION is not the ARGUMENT. Measured (#10083 review P2): the
    # count and the three literals were pinned, but the two sentences that make
    # the enumeration do any work were not — `none of which this grep matches` →
    # `which this grep matches`, `NEITHER of the other two is logged at all` →
    # `BOTH … are logged as well`, and `IS in the grep` → `is NOT in the grep`
    # were all GREEN. Each inversion tells an operator that an empty recipe-1 read
    # clears a real resolution fault, which is the fail-open the widened `grep -E`
    # in the bound exists to prevent.
    # The CLASS, not one idiom: `none of which this grep matches` / `which this
    # grep does not match` / `of which this grep matches none` / `none of which
    # are matched by this grep` all state the same ruling, and each reword red
    # the single-idiom pin with a message accusing the doc of the inversion it
    # had just kept (measured, #10083 review P2 — the F3 class again). The
    # inversion is caught by the `(?:all|each) of which` detector below, so
    # accepting the class costs no discrimination.
    # The COUNT WORD is taken from the label that is there (`_c_count_word`), not
    # hard-coded `four`: the inversion these two pins measure is a property of the
    # sentence, not of the numeral, and a literal numeral reddened an honest
    # relabel for no claim change (#10083 review cycle 18, P3).
    assert re.search(
        _c_count_word + r" MESSAGES,?\s+(?:none of which\s+this grep\s+matches"
        r"|which\s+this grep\s+does not match"
        r"|of which\s+this grep\s+matches none"
        r"|none of which\s+are matched by this grep)",
        _excl_c,
        re.I,
    ), (
        "exclusion (c) no longer says these faults are OUTSIDE recipe 1's "
        "grep — inverted, it tells the reader an empty recipe-1 read clears them"
    )
    assert not re.search(
        _c_count_word + r" MESSAGES, (?:all|each) of which this grep", _excl_c, re.I
    ), (
        "exclusion (c) claims the three resolution faults ARE in recipe 1's grep, "
        "which is false — recipe 1 greps `the record write did not happen`"
    )
    assert re.search(
        r"so on a default-row read fault neither of the (?:other three|two\s+"
        r"`could not resolve` lines(?: above)?)\s+is\s+logged at all",
        _excl_c,
        re.I,
    ), (
        "exclusion (c) drops the consequence that makes `graphs default-row read "
        "failed` special — `default_graph_id_for` swallows its exception, so on "
        "that fault NEITHER hosted_api `could not resolve` line is emitted and a "
        "grep of just those two reads empty on a real fault. Either truthful form "
        "is accepted: `the other three` (arithmetic against the (c) label) or the "
        "named `two `could not resolve` lines`; the pre-fix `other two` over a "
        "FOUR-message label is the self-contradiction this pin exists to catch"
    )
    assert re.search(
        r"\(`the record write did not happen \(no_tenant\)`\) IS in the grep", _excl_c
    ), (
        "exclusion (c) stops distinguishing the refusal (which IS in the grep) "
        "from the fault (which is not) — inverted, a reader counts the growth as "
        "the fault or dismisses the line as missing"
    )
    # ⛔ POLARITY, not vocabulary — a TWO-SIDED test, and both sides are POLARITY
    # CLASSES rather than shape lists. The first fix pinned only the `would be
    # unexecutable` SHAPE, so a TRUTHFUL clarification in another shape red the suite
    # while asserting exactly the ruling ("Per-request attribution is impossible from
    # this log line.", "There is no per-request attribution in this log line." —
    # #10083 review, P2, measured on both), with a message telling the next lane the
    # section "presents per-request attribution of growth as possible" when it did
    # not. Then the accept half became a NEGATION WORD LIST, which failed the other
    # way: seven evasions that negate something OTHER than the claim passed both
    # halves — "Nothing prevents per-request attribution", "It is not hard to
    # attribute that growth per request", "not out of reach", "The log does not
    # prevent you from attributing", "not unachievable", "No obstacle prevents", and
    # "You can attribute that growth per request, no correlation needed" (all
    # GREEN before this pass; measured, #10083 review). Hence:
    #
    #   ACCEPT — the sentence carries NEGATIVE POLARITY: a prohibition, an
    #   infeasibility, an absence, a failure, a barrier, or an inability. Any truthful
    #   way of stating the ruling is one of those, and the class is closed under
    #   rewording in a way a list of shapes is not.
    #   REJECT — the sentence carries POSITIVE feasibility about the claim (bounded
    #   to a claim word, so a truth like "no request identity is available" is not
    #   red), or a DOUBLE NEGATIVE, i.e. negative polarity applied to a
    #   NEGATIVE-MARKED predicate ("not impossible", "nothing prevents", "does not
    #   prevent", "not hard", "not out of reach") — the shape that lets a sentence
    #   carry the accept half's marker and still assert the opposite.
    #
    # SELECTION is by CLAIM NOUN OR VERB, not by verb alone: "A per-request breakdown
    # of `no_tenant` growth is available from the log" and "The log line can be
    # correlated with a request" carry no `attribut`/`assign` and state the opposite
    # of the ruling, so a verb-only filter never scanned them (measured GREEN, #10083
    # review). `per request` alone is deliberately NOT a selector: the hosted-lane
    # sentence "carries its tenant **per request**, which always wins" is not a claim
    # about attributing growth, and selecting it made the accept half red a truthful
    # sentence (measured).
    _claim = (r"attribut|assign|breakdown|break\s+\w+\s+down|identif|identit|"
              r"distinguish|correlat|tells?\b[^.]{0,40}?\bapart")
    # ⛔ SELECTION and the CHUNK BOUNDARY are two separate defects, and only the
    # first was closed by widening `_claim`. Splitting only on `(?<=\.)\s+` fuses a
    # sentence that FOLLOWS an emphasized ruling into the ruling's own chunk, because
    # the ruling ends `… from the log.**` — the whitespace after it is preceded by
    # `*`, not `.` — and the accept half then needs its negation only SOMEWHERE in
    # the chunk, which the ruling itself supplies. Measured (#10083 review, P2):
    # appending ` Then attribute that growth per request from the log.` — or ` **Do**
    # attribute …` — immediately after the ⛔ ruling left the whole suite GREEN, i.e.
    # the one placement this comment says it closes ("a section that says both is
    # green under a presence-only ⛔ pin") was the one placement the scan could not
    # read. `**` is markdown emphasis, not sentence content, so stripping it before
    # the split makes the ruling its own sentence and the appended instruction its
    # own; `!` and `?` are sentence boundaries too. This does not weaken the scan —
    # it only makes every chunk SHORTER, so no chunk can gain a negation it did not
    # have, and the baseline ruling stays green on its own text.
    _prose = flat.replace("**", "")
    _sentences = [s for s in re.split(r"(?<=[.!?])\s+", _prose)
                  if re.search(_claim, s, re.I) and re.search(r"\brequest", s, re.I)]
    assert _sentences, (
        "the section never rules per-request attribution out at all — the ruling "
        "below is the only thing that makes the arm executable"
    )
    for _sentence in _sentences:
        # A NEGATION ON THE CLAIM NOUN IS NOT AN EVASION — it IS the ruling:
        # "No per-request attribution is possible from this log line" red the
        # positive-feasibility family with a message claiming the section presents
        # attribution as possible (measured, #10083 review). So the noun-negated
        # phrase ALONE is removed before the feasibility check. "There is no doubt
        # that per-request attribution is possible" keeps its nouns (nothing sits
        # between `no` and `doubt`) and stays RED, which is what that family exists
        # for. The strip cannot smuggle: it removes only `<negation> <claim>`, and
        # every family below reads the sentence either side of that pair.
        _reject_target = re.sub(
            r"\bno\s+(?:per-request\s+|per\s+request\s+)?"
            r"(?:attribut|assign|breakdown|identif|distinguish|correlat)\w*",
            " ",
            _sentence,
            flags=re.I,
        )
        assert not re.search(
            # (b) POSITIVE FEASIBILITY about the claim, subject-bounded so an
            # unrelated truth ("no request identity is available") cannot red it.
            r"\b(?:attribut|assign|breakdown|identif|distinguish|correlat)\w*"
            r"[^.]{0,60}?\b(?:is|are|was|were|remains?|can\s+be|could\s+be|"
            r"may\s+be|might\s+be|will\s+be|would\s+be|stays?)\s+"
            r"(?:possible|feasible|available|achievable|attainable|reachable|"
            r"doable|workable|executable|straightforward|trivial)\b|"
            # (a) DOUBLE NEGATIVE: negative polarity applied to a negative-marked
            # predicate. This is the family the lexeme-list accept half could not
            # see — "Nothing prevents …", "does not prevent you from …",
            # "not unachievable", "not hard to …", "not out of reach".
            r"\b(?:not|never|nothing|none|no)\b[^.]{0,60}?\b(?:impossible|"
            r"infeasible|unexecutable|unavailable|unachievable|unattainable|"
            r"unreachable|unworkable|undoable|hard|difficult|tricky|prevent\w*|"
            r"block\w*|stop\w*|hinder\w*|obstruct\w*|prohibit\w*|forbid\w*|"
            r"barrier|obstacle|problem|beyond|out\s+of\s+(?:reach|the\s+question))\b|"
            # (c) NEGATED FEASIBILITY, the double negative's softer twin:
            # "attribution is not merely possible but required".
            r"\bimpossible\s+to\s+(?:avoid|prevent)\b|"
            r"\bnot\s+(?:merely|simply|only|just|solely)\b[^.]{0,40}?\b"
            r"(?:possible|feasible|doable|achievable)\b|"
            # (d) MODAL feasibility about the claim: "you can attribute that growth
            # per request". Tempered so a negated modal ("can never attribute") is
            # not red.
            r"\b(?:can|could|may|might|are\s+able\s+to|is\s+able\s+to)\s+"
            r"(?:(?!\b(?:not|never)\b)[\w'’]+\s+){0,4}"
            r"(?:attribut|assign|identif|distinguish|correlat)\w*|"
            # (e) the tail that re-asserts the false premise.
            r"\b(?:log|line)\b[^.]*\bnames\s+the\s+request\b",
            _reject_target, re.I,
        ), (
            "the section asserts that per-request attribution IS possible — through "
            "a positive feasibility predicate, a DOUBLE NEGATIVE ('not impossible', "
            "'nothing prevents', 'not hard'), a negated feasibility predicate ('not "
            "merely possible'), a modal ('you can attribute …'), or a tail that "
            f"re-asserts that the log names the request: {_sentence.strip()!r}"
        )
        assert re.search(
            # The ACCEPT half: NEGATIVE POLARITY, in the class form — a prohibition,
            # an infeasibility, an absence, a failure, a barrier, an inability.
            r"\b(?:no|not|never|cannot|can't|none|nobody|nothing|neither|nor|denied|"
            r"without|unable|incapable|impossible|infeasible|unexecutable|unavailable|"
            r"unachievable|unattainable|unreachable|unworkable|undoable|fails?|failed|"
            r"failing|precludes?|precluded|rules?\s+out|ruled\s+out|omits?|omitted|"
            r"lacks?|lacking|excludes?|excluded|opaque|silent|absent)\b",
            _sentence, re.I,
        ), (
            "the section presents per-request attribution of growth as possible — "
            "no prohibition, infeasibility, absence, failure or barrier governs the "
            "claim, and the log carries no request identity: "
            f"{_sentence.strip()!r}"
        )
    # ⛔ THE RESIDUAL, stated so it is not a silent gap: an inversion written with a
    # verb OUTSIDE the claim list is not SELECTED at all — "The growth can be filed
    # per request", "Grouping the growth by request is a one-line join on the request
    # id", "the growth can be split per request without guesswork" are all GREEN
    # (measured). Widening the list does not close the class, because there is always
    # another verb for the same claim; what closes it is a human reading the
    # paragraph, which is why the ⛔ instruction and the bar sentence it qualifies are
    # pinned on their own text elsewhere in this test.
    # REVERT-DETECTOR, not a claim pin: this reds only if one draft's exact wording
    # ("recipe 1 logs …") comes back — the phrase exists nowhere else in the tree,
    # so the assertion is green by construction and its message describes a class it
    # cannot see (#10083 review P2). It is kept because it is exact and free; the
    # CLAIM is measured by the `does NOT match` pin below, which a rewording of the
    # false instruction would have to delete.
    assert not re.search(r"recipe\s+1\s+logs", flat), (
        "the pre-fix 'recipe 1 logs the fault' sentence is back"
    )
    # ⛔ The ⚠️ list's count is EXHAUSTIVE and it now has three members: (a)
    # `no_durable_journal`, (b) `position_unproven`, (c) the hosted_api
    # tenant-resolution fault this PR's bound cites 147 lines below. A list labelled
    # TWO while naming a third member is the same self-contradiction class as the
    # rest of this PR, and `record_position_unproven` / `tenant resolution` appear
    # elsewhere in the section too, so this pins the enumerated clause, not a token.
    # Measured: reverting it to "TWO" left every other assertion green (#10083
    # review P2).
    # ⛔ The LABEL's numeral is compared to the members, in both directions
    # (#10083 review F1, P2, measured). A literal `THREE` was RED against an honest
    # relabel to FOUR, and the `\(([a-c])\)` membership pin could not see a `(d)`,
    # so a fourth member of a list still labelled THREE was GREEN — both are the
    # self-contradiction class this section exists to remove.
    _excl_label = re.search(
        r"(TWO|THREE|FOUR|FIVE|SIX) readings are not in this grep", flat, re.I
    )
    assert _excl_label, (
        "recipe 1's exclusion list's exhaustive label is gone, so a reader takes "
        "the `no_tenant` bound's extra readings as covered by the fault grep"
    )
    # Member presence only — the label's NUMERAL is derived at the (c) paragraph
    # above, so a literal `four` here would red an honest `five` relabel for no
    # claim change (#10083 review cycle 18, P3). CASE is not the claim either:
    # `MESSAGES` → `messages` keeps the member and the count, so this presence pin
    # must be `re.I` (#10083 review P2, measured).
    assert re.search(
        r"\(c\) The tenant-RESOLUTION faults — [A-Za-z]+ MESSAGES", flat, re.I
    ), (
        "the exclusion list does not name the resolution-fault member, so the "
        "count and the members disagree"
    )
    # ⛔ The COUNT is not the ENUMERATION. Pinning `THREE readings` and the (c)
    # member left (a) and (b) DELETABLE with the suite green (measured, #10083
    # review P3): the only other mention of `no_durable_journal` in this function is
    # the bare-token loop at `for expected in (…)`, and that token occurs four times
    # elsewhere in the section, so it cannot see the deletion — while the label
    # still said THREE. Members (a) and (b) are the two exclusions an operator most
    # needs: (a) is the healthy M2 decline, the one line a BROKEN record also
    # produces, and (b) is the `position_unproven` line item 2's own counter counts.
    # So derive the membership from the markers AND pin each member's own clause.
    _excl_list = re.split(
        r"(?:TWO|THREE|FOUR|FIVE|SIX) readings are not in this grep",
        flat,
        maxsplit=1,
        flags=re.I,
    )[-1].partition("⛔ **`no_journal` and `seam_unimportable` ARE in it**")[0]
    _members = re.findall(r"\(([a-z])\) The ", _excl_list)
    assert _members == [chr(ord("a") + _i) for _i in range(len(_members))], (
        "recipe 1's exclusion list's member markers are not a run from (a) "
        f"({_members!r}) — a gap or a renumbering hides a member from the count"
    )
    assert _NUMERALS[_excl_label.group(1).upper()] == len(_members), (
        "recipe 1's exclusion list is labelled "
        f"{_excl_label.group(1)} readings but enumerates {len(_members)} "
        f"({_members!r}) — a label that says THREE over four members is the same "
        "self-contradiction class this section exists to remove, so a reader takes "
        "an exhaustive enumeration that is not exhaustive"
    )
    for _clause, _why in (
        (r"`the record write was declined \(no_durable_journal\)`",
         "the healthy-M2 decline an operator must not read as a failure"),
        (r"`no layer-1 position row for this record \(position_unproven\)`",
         "the once-per-window line item 2's own counter is counting"),
    ):
        assert re.search(_clause, _excl_list), (
            f"recipe 1's exclusion list no longer names {_why} — its label still "
            "counts the member, so a reader takes a list that is missing it as "
            "exhaustive"
        )
    # The reason the fault escapes the grep is the MESSAGE, not the logger: the
    # section's own ⛔ paragraph names `tortoise/sdk.py` — a different module — as
    # emitting the identical prefix, so "a different module's logger" is refuted by
    # the sentence five lines below it (#10083 review P2). Pin the MESSAGE reason,
    # and pin that the logger claim is gone.
    assert re.search(r"The discriminator is the message text, not\s+the logger", flat), (
        "the list does not say WHY the faults escape the grep (the MESSAGE is the "
        "discriminator, not the module), which is the fact that stops a reader "
        "from checking recipe 1 and concluding 'no fault'"
    )
    assert not re.search(r"different\s+module's\*\* logger, so it does not match", flat), (
        "the list attributes the escape to the LOGGER again — the section's own ⛔ "
        "paragraph says `tortoise/sdk.py` (another module) IS in the grep, so the "
        "stated reason contradicts it"
    )
    # The evidence half of the third-disjunct fix: naming the working command is
    # only half of it — the sentence must also say recipe 1 CANNOT show this, or an
    # operator greps recipe 1, sees nothing and clears the capture (the fail-open
    # the finding described). Measured: deleting just this clause left the suite
    # green, i.e. the reading existed but was unpinned (#10083 review P2).
    assert re.search(
        r"\*\*recipe 1's grep does NOT match\*\* \(it greps "
        r"`the record write did not happen`\)",
        flat,
    ), (
        "the section names the working command but never says recipe 1 cannot "
        "show the fault — the reader who checks recipe 1 first concludes 'no fault'",
    )
    # ⛔ The two pins above are NOT enough on their own: they survive an
    # inverted prohibition, because a section can say "the line carries **no**
    # request identity" and "**Do** attribute that growth per request from the
    # log" at the same time — self-contradictory, and green. Pin the ⛔ clause
    # itself, which is the sentence that carries the ruling (#10071 review P2).
    # The ⛔ marker is pinned WITH the clause, not by a bare `⛔` search: this section
    # carries 10 ⛔ markers, so a bare search is satisfied by any of them and leaves
    # the ruling unmarked-and-green (#10083 review F1 — the first attempt did exactly
    # that, making the pin weaker than the one it replaced). `(?:Do not|Never)`
    # admits the equivalent prohibition and `try to` is OPTIONAL, because requiring
    # it made this pin DISAGREE with the polarity scan ~60 lines above: `Do not
    # attribute that growth per request from the log.` and `Never attribute …` RED
    # here while that scan accepted both, so this pin fired "the section does not
    # forbid the impossible step" at a section that forbids it (measured, #10083
    # review P2). The INVERTED twin (`**Do try to attribute …**`) still reds, here
    # and in the polarity scan. Only the WRAP is neutralised by `flat` (#10071
    # review F3).
    # The casing is part of the alternation because `Do NOT` is this section's OWN
    # house style (13 and 19 lines below: `⛔ **Do NOT put it in `fly.toml`'s
    # `[env]`.**`, `⛔ **Do NOT set TORTOISE_RECORD_SINGLE_TENANT**`), so requiring
    # `Do not` red a pure STRENGTHENING of the ruling with a message accusing the
    # section of not making it — and it disagreed with the polarity scan ~60 lines
    # above, whose accept/reject halves are `re.I` (measured, #10083 review F2).
    assert re.search(
        r"⛔ \*\*(?:Do not|Do NOT|Never)\*{0,2} (?:try to )?(?:attribute|assign) "
        r"that growth per request",
        flat,
    ), (
        "the section does not forbid the impossible step it describes — "
        "per-request attribution from a line that carries no request identity"
    )
    # The section's closing RULING, which is what bounds the arm and is the reason it
    # declines the only lever it names: deleting it left every other pin green
    # (#10083 review P2). The config assertions live in
    # `test_the_record_is_not_armed_single_tenant_on_the_hosted_app` (named, not
    # cited by line — the ranges rot, as the sibling comment above already says),
    # and they pin the CONFIG, not this prose, so without this the arm's remedy has
    # no stated boundary — and the only remaining "fix" for an unbound capture is
    # the #9192 hazard.
    assert re.search(r"The route is never `TORTOISE_RECORD_SINGLE_TENANT`", flat, re.I), (
        "the section no longer rules out the cross-tenant route, so the one "
        "remaining way to 'fix' an unbound capture is the #9192 hazard it forbids"
    )


def _guarded_append(name: str) -> str:
    """The exact guard + append line an optional secret must carry (`-n`-guarded)."""
    return f'[ -n "${name}" ] && ARGS+=({name}="${name}")'


def test_record_arming_secret_is_staged_from_its_own_github_secret():
    """#9548 R1 target (c): every deploy surface names `TORTOISE_RECORD_DSN`.

    Three surfaces, and each one is load-bearing on its own:

    * the payload step must BIND and APPEND it (from its own same-named GitHub
      secret) — otherwise the value stays on GitHub and the app keeps answering
      `NOT_ARMED`;
    * the provenance gate must PROBE it into `GH_SECRETS_PRESENT` — the fleet's
      `probed == declared_gh` lockstep derives that from the manifest
      (`test_fly_secret_drift.py`). ⚠️ **Do not write "the deploy hard-fails
      without the probe" as a present-tense fact (#9585 review P3, measured).**
      With the probe removed and the name absent from Fly, the gate still exits
      **0**: rule (3)'s `UNSOURCED` fires only when the name is already ON Fly
      (`check-fly-secret-drift.py`), so before the secret is provisioned nothing
      deploy-time holds the probe in place — the lockstep guard and the assertion
      below are the whole pin. Once the value is provisioned it is load-bearing.
    * the manifest must DECLARE it and the runbook's Secrets Matrix must show the
      GitHub home — a declaration the runbook contradicts is how an operator gets
      told to rotate Fly-only, which the next deploy then silently reverts
      (#8614).

    ⚠️ **Ownership (#9585 review P2), because a duplicated pin is process weight.**
    The bind / probe / declaration / append assertions below are BELT-AND-BRACES.
    Their owner is `tests/test_fly_secret_drift.py`, which DERIVES each of them
    from the shipped manifest (`_declared_gh_names(REAL_MANIFEST)`), so it covers
    a newly declared `gh-secret:` name with no edit:

    * `::test_gh_secret_declarations_are_bound_from_their_own_github_secret` —
      both steps bind `secrets.<name>` (same two steps);
    * `::test_probe_block_and_manifest_gh_secret_names_are_in_lockstep` —
      `probed == declared_gh` and every probe env-bound to its own secret;
    * `::test_shipped_manifest_matches_the_real_workflow_and_recorded_debt` — the
      gate's own state machine over the real artifacts (`STALE` / `UNDECLARED`);
    * `::test_secrets_matrix_github_column_agrees_with_the_manifest` — the ✅ cell.

    They are kept here because the failure they name is cheap to read next to the
    workflow, not because they are its only pin — and the closing block asserts
    those owners still exist under those names, so this note cannot rot into a
    pointer at a renamed test. What this test ALONE owns is the matrix **row's
    existence**: the derived matrix guard only inspects rows that exist, so a
    `gh-secret:` declaration landing with no row is unpinned there.
    """
    gate = _steps()[_PROVENANCE_STEP]
    payload = _steps()[_SET_STEP]
    # Bound in BOTH steps, from the SAME-named GitHub secret (a cross-wired
    # binding passes name presence while shipping the wrong value, #4334).
    for step in (gate, payload):
        assert _env_bindings(step).get(_RECORD_DSN_ENV) == _RECORD_DSN_ENV, (
            f"{_RECORD_DSN_ENV} is not bound to secrets.{_RECORD_DSN_ENV} in the "
            "step that must carry it — the record stays unarmed in production "
            "while every test here is green"
        )
    assert f'[ -n "${_RECORD_DSN_ENV}" ] && GH_PRESENT="$GH_PRESENT {_RECORD_DSN_ENV}"' in gate["run"], (
        "the provenance gate does not probe TORTOISE_RECORD_DSN into "
        "GH_SECRETS_PRESENT — the manifest's `gh-secret:` declaration then has no "
        "probe beside it, and ONLY the lockstep guard catches that: the gate itself "
        "exits 0 until the name is also on Fly, so do not claim a deploy-time "
        "failure that does not exist before provisioning"
    )
    # Appended to the flyctl argv, AND guarded. Membership alone is guard-blind:
    # `_array_pairs` scans the whole `run:` text, so it maps a guarded append and
    # a bare one identically, and the provenance gate does not refuse the bare one
    # either (in the all-present sample the name is `assigned`). An unguarded
    # append pushes `TORTOISE_RECORD_DSN=` on every deploy — a silent DISARM — so
    # the guard is asserted too, and
    # `test_the_record_arming_append_is_guarded_not_merely_present` proves that
    # assertion discriminates.
    assert _array_pairs(payload["run"]).get(_RECORD_DSN_ENV) == _RECORD_DSN_ENV, (
        f"the secrets-set step never appends {_RECORD_DSN_ENV} to the flyctl "
        "array — the GitHub secret would never reach Fly"
    )
    assert _guarded_append(_RECORD_DSN_ENV) in payload["run"], (
        f"the {_RECORD_DSN_ENV} append is not `-n`-guarded — an absent GitHub "
        "secret would stage an EMPTY DSN and silently disarm the record while "
        "every guard here stays green"
    )
    assert _manifest_declarations().get(_RECORD_DSN_ENV) == f"gh-secret:{_RECORD_DSN_ENV}", (
        f"{_RECORD_DSN_ENV} is not declared gh-secret:{_RECORD_DSN_ENV} in "
        ".github/scripts/fly-managed-secrets.txt — the provenance gate then "
        "reports it UNDECLARED the moment it exists on Fly"
    )
    rows = _matrix_rows()
    assert _RECORD_DSN_ENV in rows, (
        f"{_RECORD_DSN_ENV} has no Secrets Matrix row in docs/infra-runbook.md — "
        "an operator cannot find where the record is armed"
    )
    assert "✅" in rows[_RECORD_DSN_ENV][2], (
        "the Secrets Matrix shows TORTOISE_RECORD_DSN with no GitHub Actions home, "
        "while the manifest declares it `gh-secret:` — the Fly-only rotation recipe "
        "looks correct for a name the next deploy overwrites"
    )
    # The belt-and-braces note in the docstring names four owners; assert they
    # still exist under those names, so a rename or a deletion reds HERE rather
    # than leaving this test's pointer aimed at nothing (#9585 review P2).
    owners = (_REPO_ROOT / "tests" / "test_fly_secret_drift.py").read_text(
        encoding="utf-8"
    )
    for owner in (
        "test_gh_secret_declarations_are_bound_from_their_own_github_secret",
        "test_probe_block_and_manifest_gh_secret_names_are_in_lockstep",
        "test_shipped_manifest_matches_the_real_workflow_and_recorded_debt",
        "test_secrets_matrix_github_column_agrees_with_the_manifest",
    ):
        assert f"def {owner}(" in owners, (
            f"tests/test_fly_secret_drift.py no longer defines {owner!r} — that is "
            "the manifest-derived owner of an assertion kept here for local "
            "readability, so this test's belt-and-braces claim is now false and "
            "the invariant may have lost its only deriving reader"
        )


def _build_block_runbook_section() -> str:
    """The runbook's `/health` `build` block entry, verbatim (#10467 review P3).

    Every other `/health` addition of this kind landed with an operator entry —
    `record` (#9548, `### Arming the Postgres record`), `change_feed` (#9208, in
    the flap-register row) — because "what `/health` publishes and whether it
    folds into `status`" already has its home in this document (AGENTS.md →
    Documentation Filing Protocol: update the file that owns the topic).

    Scoped to the section, and the section is located by its TITLE TEXT rather
    than its number: a renumber (4.3 -> 4.4) must not red this, while a deletion
    or a rename must.
    """
    text = _RUNBOOK.read_text(encoding="utf-8")
    marker = "Which build is serving"
    assert marker in text, (
        f"docs/infra-runbook.md has no {marker!r} section — the operator's only "
        "reading of the `build` block (#10467) is gone, and the four subtleties "
        "it documents (no `status` fold, `build_identified: false`, "
        "`commit_dirty: null`, carried-vs-applied migrations) go with it"
    )
    body = text.split(marker, 1)[1]
    return body.split("\n## ", 1)[0].split("\n### ", 1)[0]


def test_the_health_build_block_is_documented_for_operators():
    """#10467 review P3: the additive `/health` block must be readable WITHOUT
    reading `tortoise/hosted_api.py`.

    Four subtleties ride on this block and every one of them is a misread an
    operator will make under pressure: it deliberately does not fold into
    `status` (so `status: ok` is not "current"), `build_identified: false` is the
    "cannot check" refusal (not "no build"), `commit_dirty: null` means **NOT
    STATED** — the deploy now stamps `TORTOISE_GIT_DIRTY` from its own tree
    (#10447 reopen), so `null` means the reading is missing (selfhost / local dev /
    an unreadable tree), never "clean" — and `migrations_head` is the population
    the IMAGE carries — not the set applied to the database. A deployment whose
    `commit_dirty` is null reports `build_identified: false` with
    `build_identified_reason: tree_dirt_unstated`, which is the case that reads as
    clean if the runbook is silent.

    Pre-fix this reds at the marker: the runbook's §4 has 4.1 (client effect) and
    4.2 (billing mirror) and nothing about `build`.
    """
    section = _build_block_runbook_section()
    # Reflow-proof: the assertions below are about CLAUSES, and markdown is free
    # to wrap them (the #10083 F3 lesson — a literal single-space pattern reds a
    # wording-identical reflow).
    flat = " ".join(section.split())
    assert "curl" in section and "/health" in section, (
        "the entry has no runnable /health recipe — the whole point is that this "
        "reading needs no shell, no key and no database"
    )
    for field in ("version", "commit_sha", "commit_dirty", "build_identified",
                  "build_identified_reason", "image",
                  "started_at", "migrations_head", "migrations_source"):
        # A ROW, not a token (review P2): `field in section` is satisfied by prose
        # that happens to say "image" (the `migrations_head` row does), so deleting
        # the row the operator actually reads left the pin green. Anchored to a
        # table row so a reflow cannot satisfy it by accident.
        assert re.search(rf"^\|\s*`{re.escape(field)}`\s*\|", section, re.M), (
            f"the field TABLE has no `{field}` row — a reader who has the field but "
            "no meaning reads it wrong (a `null` `commit_dirty` as clean, an "
            "`unknown` `migrations_source` as the checkout)"
        )
    assert re.search(r"does NOT fold into `status`", flat), (
        "the entry never says the block does not fold into `status` — an operator "
        "who reads `status: ok` as 'current' restarts a healthy machine, and a "
        "future change that DID fold staleness in would look documented-ok"
    )
    assert re.search(r"`null` means \*\*NOT STATED\*\*", flat), (
        "the entry does not say `commit_dirty: null` means NOT STATED — an "
        "unstamped deployment now reports `build_identified: false` with "
        "`build_identified_reason: tree_dirt_unstated`, so `null` is silence, "
        "never 'clean'"
    )
    assert re.search(r"NOT \"which migration has been APPLIED", flat), (
        "the entry does not refuse the applied reading — `migrations_head` is the "
        "highest migration the IMAGE CARRIES, and `supabase-deploy` applies the "
        "schema, so publishing it as 'applied' is a false PASS on the question "
        "this block exists to answer"
    )
    assert "schema_migrations" in section, (
        "the entry refuses the applied reading without saying where the applied "
        "set actually lives — a reader with no pointer substitutes this field"
    )


#: Depths at which the census's historical tree is sought in a SHALLOW clone, mirroring
#: `tests/test_9397_about_edge_properties.py::_SHALLOW_DEPTHS`. Bounded on purpose: the cost is
#: one shallow branch fetch and the bound is the point at which the tree is genuinely too old to
#: reach — reported as a SKIP that says so, never as a measurement of zero.
_CENSUS_SHALLOW_DEPTHS = (120, 400, 1500)


def _census_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """A read-only git command in `repo`; a git that cannot run reports a NON-ZERO exit.

    Never raises: every caller decides what a failure means, and none of them may read it as
    "the command returned nothing" (#4309's fail-open shape).
    """
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - no git at all
        return subprocess.CompletedProcess([*args], 127, stdout="", stderr=str(exc))


def _census_commit_available(repo: Path, rev: str) -> bool:
    """True when `rev` resolves to a commit in THIS checkout's history."""
    return _census_git(repo, "cat-file", "-e", f"{rev}^{{commit}}").returncode == 0


def _census_is_shallow(repo: Path) -> bool:
    """True when this checkout is a SHALLOW clone (`--is-shallow-repository`)."""
    done = _census_git(repo, "rev-parse", "--is-shallow-repository")
    return done.returncode == 0 and done.stdout.strip() == "true"


def _census_deepen_to(
    repo: Path, rev: str, depths: tuple[int, ...] = _CENSUS_SHALLOW_DEPTHS
) -> bool:
    """Make `rev` reachable IF the checkout is shallow — bounded, best effort.

    ⛔ Gated exactly like `tests/test_9397_about_edge_properties.py::_ensure_commit`, whose
    docstring records the measurement: `git fetch --depth=N` on a NON-shallow clone turns
    `--is-shallow-repository` from `false` to `true` and cuts `git rev-list --count
    origin/main` from 6106 to 120 — it TRUNCATES a developer's full history. So a full clone
    that lacks the rev is never shallow-ified: this returns False and the caller SKIPS with a
    message, rather than failing on a tree that structurally cannot carry the object.

    The refspec is explicit so the fetch does not depend on `remote.origin.fetch` being
    configured — a `actions/checkout@v4` clone of a PR does not set it up the way a developer
    clone does, and `git fetch origin main` alone would then leave no `refs/remotes` entry.
    """
    if _census_commit_available(repo, rev):
        return True
    if not _census_is_shallow(repo):
        return False
    for depth in depths:
        _census_git(
            repo,
            "fetch",
            "--quiet",
            f"--depth={depth}",
            "origin",
            "+refs/heads/main:refs/remotes/origin/main",
        )
        if _census_commit_available(repo, rev):
            return True
    return False


def _census_count_at(repo: Path, rev: str, paths: list[str]) -> int:
    """The census's line count at `rev` — FAIL-CLOSED on a git failure.

    `git grep` exits **1** when it selects nothing (the legitimate "0 lines" reading) and
    **128** when the object is missing. Summing stdout without looking at the exit code reads
    the second as the first: a command that never ran would then satisfy a note that states 0,
    which is the #4309 shape this whole block exists to close, reproduced inside its own pin.
    """
    done = _census_git(repo, "grep", "-c", _RECORD_DSN_ENV, rev, "--", *paths)
    assert done.returncode in (0, 1), (
        f"`git grep` at {rev} failed (exit {done.returncode}) instead of selecting nothing "
        f"(exit 1), so its stdout is NOT a census of zero lines:\n{done.stderr.strip()}"
    )
    measured = 0
    for line in done.stdout.splitlines():
        tail = line.rsplit(":", 1)[-1]
        if tail.isdigit():
            measured += int(tail)
    return measured


def _superseded_census_note() -> str:
    """The epic's #9548 supersession note for the first census command, verbatim.

    Scoped to that paragraph (through its fenced command), so a
    `TORTOISE_RECORD_DSN` mention elsewhere in the doc — the §A8 rows — can
    neither satisfy nor fail these assertions by accident.
    """
    text = _EPIC_DOC.read_text(encoding="utf-8")
    marker = "The first census line is superseded by #9548"
    assert marker in text, (
        "docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md "
        "no longer carries the #9548 supersession note — the census claim the "
        "operator reads is then gone or unattributed"
    )
    return text.split(marker, 1)[1].split("\n```", 1)[0]


def test_the_superseded_census_note_only_claims_what_its_path_set_returns():
    """#9548 review P2: the provenance note must not credit a path it never greps.

    The epic's census block records the "not in place" claim as
    `grep -rn TORTOISE_RECORD_DSN fly.toml entrypoint.sh .github/workflows/\
    docs/infra-runbook.md`. #9548 supersedes that line, and the note said the
    command "now returns … the manifest declaration" — but
    `.github/scripts/fly-managed-secrets.txt` is NOT in that path set, so it is
    not returned. In a note whose whole purpose is provenance honesty about
    which evidence moved, that is the sentence a later lane relies on before
    re-running the census. Fail-closed on rewording: the check needs the note's
    "it now returns …" clause and its "comment." terminus, so deleting either
    (or softening the claim into prose) reds rather than passes.
    """
    note = _superseded_census_note()
    # The census command's path set, read from the doc rather than retyped.
    block = _EPIC_DOC.read_text(encoding="utf-8").split("Census commands", 1)[1]
    cmd = re.search(r"grep -rn TORTOISE_RECORD_DSN(.*?)→", block, re.S)
    assert cmd, "the doc's baseline census command is gone — the note has no referent"
    paths = [t for t in cmd.group(1).replace("\\", " ").split() if t]
    assert set(paths) == {
        "fly.toml", "entrypoint.sh", ".github/workflows/", "docs/infra-runbook.md"
    }, paths
    manifest = ".github/scripts/fly-managed-secrets.txt"
    assert manifest not in paths, (
        "the baseline census command grew the manifest path, so the note's scope "
        "claim has changed — re-derive it instead of trusting this assertion"
    )
    # ⛔ NEITHER THE WORD NOR THE SENTENCE WINDOW IS THE CLAIM. Two reword
    # bypasses were measured here: naming the FILE instead of the word, and
    # ending the "it now returns …" sentence early so the false provenance sits
    # in the NEXT sentence. A window-scoped assertion passed both, and it
    # REJECTED a truthful "…and NOT the manifest declaration". So stop scoping on
    # a window: bind every MENTION of the manifest in the note to a clause that
    # DENIES it is one of the census command's returns. The note may name the
    # file as often as it likes; what it may not do is credit the baseline
    # command with it (#9548 review r2).
    sentences = [s for s in re.split(r"(?<=\.)\s+", note) if s.strip()]
    naming = [
        s for s in sentences
        if "manifest" in s or manifest in s or Path(manifest).name in s
    ]
    assert naming, (
        "the note no longer names the manifest declaration at all — the fourth "
        "arming surface is then undocumented in the provenance record"
    )
    unnegated = [
        s for s in naming
        # ⛔ Case-insensitive: emphasis variants are legitimate prose — a
        # "…and NOT the manifest declaration" is exactly the truthful shape this
        # assertion must ACCEPT, and a case-sensitive `\bnot\b` rejected it
        # (measured while checking that this pin does not over-block).
        if not re.search(r"\b(?:not|never|isn't|is not|are not)\b", s, re.I)
    ]
    assert not unnegated, (
        "the note names the manifest declaration in a sentence that does not "
        "deny it is in the baseline census command's path set:\n  "
        + "\n  ".join(unnegated)
        + "\n`.github/scripts/fly-managed-secrets.txt` is NOT in that command's "
        "path set, so crediting the command with it is the pre-#9548-P2 false "
        "claim however it is reworded (#9548 review r2)"
    )
    # The denial has to be about THAT command's path set — a negation elsewhere
    # in the sentence is not the correction.
    assert re.search(r"\bnot\b[^.]{0,12}in that path set", note, re.I), (
        "the note no longer states that the manifest is NOT in the baseline "
        "census command's path set — that denial is the whole correction"
    )
    # Claim 2: the surface it DOES live on is named, so the reader has a command
    # that reaches it ...
    assert manifest in note, (
        "the note does not name where the manifest declaration lives — the "
        "fourth arming surface is then undocumented in the provenance record"
    )
    # ... and that command really returns one declaration (executed, not asserted).
    hits = [
        line
        for line in (_REPO_ROOT / manifest).read_text(encoding="utf-8").splitlines()
        if _RECORD_DSN_ENV in line
    ]
    assert len(hits) == 1, (
        f"`grep -rn {_RECORD_DSN_ENV} {manifest}` returns {len(hits)} lines, not "
        "the 1 the note promises"
    )
    # Claim 3 — the BASELINE reading the note's provenance rests on. The note
    # said the census was empty before #9548; it was not. #9556/R8 landed the
    # runbook's §9 `record-restore` operator example (`export TORTOISE_RECORD_DSN=`),
    # so the same path set returned ONE line before #9548 — an invocation, not an
    # arming surface. Without this arm the note can go back to claiming an empty
    # baseline and nothing reds: the manifest grep above is executed, the baseline
    # census grep never was (#9548 review P1/P2).
    assert re.search(r"NOT empty", note), (
        "the note claims the pre-#9548 census was empty — at `047c91f7a` (the "
        "arming commit's parent) the same path set returns 10 lines, and a later "
        "lane needs them named to tell them apart from a #9548 arming surface "
        "(#11064 review r17, P3: this message still published the retired "
        "\"one line\" reading)"
    )
    assert re.search(r"#9556", note), (
        "the note does not attribute the pre-existing census line to #9556/R8, so "
        "the reader cannot tell which cause put it there"
    )
    # ... and that attribution is TRUE of the runbook it names: the operator
    # example really is in the runbook's §9, at the line the note cites.
    runbook = (_REPO_ROOT / "docs/infra-runbook.md").read_text(encoding="utf-8")
    assert re.search(r"export TORTOISE_RECORD_DSN=", runbook), (
        "the note blames #9556/R8 for a runbook line that is not there — the "
        "census hit it explains has no referent"
    )
    # Claim 4 — the COUNT, and it is where this note was wrong last time (#9585
    # review P2): it said "1 line", while at the merge base it is 3 (#9600's two
    # documentation hits arrived on top of #9556/R8's invocation). The stated
    # number and the enumerated hits must be ONE measurement, or a later lane
    # re-runs the census and gets a different answer than the record of record.
    # ⛔ The tree matters at least as much as the number, and this pin had the WRONG
    # TREE (#11064 review r11, P2): it forced the pre-#9548 reading to be the
    # `ff6379eb2` one. `ff6379eb2` is the #9601 merge — an ancestor of the base, 12
    # commits and three runbook-touching commits BEFORE the #9548 arming (`785bb2b59`)
    # — so the reading "immediately before #9548" is that commit's parent,
    # `047c91f7a`, where the same path set returns 10. The pin therefore asserted a
    # number that was true of a different tree than the sentence claimed, which is
    # the stale-flag class this whole note exists to close. It now REQUIRES the tree
    # to be named, MEASURES the count at that tree with git rather than trusting a
    # numeral, and keeps the `ff6379eb2` enumeration pinned separately.
    stated = re.search(
        r"the pre-#9548 reading is\s+\*\*(\d+) lines?\*\* at `([0-9a-f]{7,40})`",
        note,
        re.I,
    )
    assert stated, (
        "the note no longer states how many lines the pre-#9548 census returned AT A "
        "NAMED TREE, so `NOT empty` is all it claims and both the count and the tree "
        "can drift"
    )
    pre_count, pre_sha = int(stated.group(1)), stated.group(2)
    assert pre_sha != "ff6379eb2", (
        "the note is back to calling `ff6379eb2` the tree immediately before #9548 — "
        "that is the #9601 merge, three runbook-touching commits before the arming "
        "commit `785bb2b59`"
    )
    # ... and the number is EXECUTED at the tree it names, not asserted: the same
    # census the doc publishes, over the same path set (`paths`, read out of the doc
    # above rather than retyped here), against the named sha.
    #
    # ⛔ AND IT MUST NOT RED ON A TREE THAT CANNOT SEE THE OBJECT (#11064 review, HIGH).
    # This file is `tier1` (its `- test_deploy_workflow.py` entry under `tier1:` in
    # `config/ci-surfaces.yml`), so it runs in python-ci's `test`
    # job, and that job's ONLY checkout is a bare `actions/checkout@v4`
    # (`.github/workflows/python-ci.yml`'s `test:` job header at `:680`, its checkout step at
    # `:968`) — the default `fetch-depth: 1`. No step in that job unshallows it; the workflow's `fetch-depth: 0`
    # belongs to the separate `changes` job (`:279`), which does not share a working tree.
    # `047c91f7a` is far behind `origin/main` — `git rev-list --count 047c91f7a..origin/main`
    # read 388 at 2026-11-06, and that figure only GROWS, so it is not the claim: the claim is
    # that a depth-1 checkout does not carry it. `git grep` there exits 128 with EMPTY stdout,
    # and the un-guarded form summed that to `measured == 0` and failed
    # `measured == pre_count` on a CORRECT tree on every PR — a
    # false RED produced by a guard whose purpose is to stop false GREENs. The repo already
    # records this environment fact (`tests/test_capstone_preregistration_9075.py:550-566`:
    # "CI test jobs check out at `fetch-depth: 1`") and carries five `--is-shallow-repository`
    # guards for it; this is the sixth, in `_ensure_commit`'s shape (bounded deepen when
    # SHALLOW only, else an explicit skip — never a silent pass, never a false red).
    # ... and the MECHANISM above is executed, not merely named: this file's `tier1`
    # membership is the premise for the whole shallow-checkout guard below. If this
    # file ever leaves `tier1` it can be scheduled onto a lane with a full clone, and
    # the bound it is guarding against stops existing while the guard keeps passing —
    # the premise has to red, not the silent-pass path (#11064 review r15, P3).
    from tools.ci_selection import load_manifest
    assert "test_deploy_workflow.py" in set(load_manifest()["tier1"]), (
        "this file is no longer a `tier1` entry, so the `fetch-depth: 1` premise the "
        "census guard below rests on has changed — re-derive which job runs this file "
        "instead of trusting the comment above (#11064 review r15, P3)"
    )
    if not _census_deepen_to(_REPO_ROOT, pre_sha):
        pytest.skip(
            f"`{pre_sha}` is not an object in this checkout (shallow: "
            f"{_census_is_shallow(_REPO_ROOT)}) and a bounded fetch to depth "
            f"{_CENSUS_SHALLOW_DEPTHS[-1]} did not reach it, so the census cannot be "
            "MEASURED here. The textual pins above still ran; this SKIP is not a pass, and "
            "the executed half of this pin needs a checkout carrying its history"
        )
    measured = _census_count_at(_REPO_ROOT, pre_sha, paths)
    assert measured == pre_count, (
        f"the note states {pre_count} line(s) at {pre_sha}, but running the census "
        f"there returns {measured} — the number and the tree are then two different "
        "measurements of the same claim"
    )
    # The `ff6379eb2` reading the note ENUMERATES is pinned to the enumeration below,
    # so the two numbers cannot be quietly swapped back.
    ff = re.search(r"that tree reads\s+\*\*(\d+) lines\*\*", note)
    assert ff, (
        "the note no longer states the `ff6379eb2` reading it enumerates, so the "
        "enumeration below has no number to agree with"
    )
    count = int(ff.group(1))
    # \d{3,5}, not \d{4,5}: the runbook's early sections cite THREE-digit lines
    # (`docs/infra-runbook.md:382` is R9's `record-backup` section, and :528 its
    # drill commentary), and the narrower pattern silently dropped them while the
    # count kept claiming them — a pin blind in exactly the direction that lets
    # the enumeration and the number disagree (#9585 review r3).
    cites = re.findall(r"docs/infra-runbook\.md:(\d{3,5})", note)
    assert len(cites) == count, (
        f"the note states {count} line(s) but enumerates {len(cites)} "
        f"({', '.join(cites)}) — the number and the enumerated hits are then two "
        "different measurements of the same census"
    )
    # ... and every enumerated hit is REAL in the tree it describes, by the snippet
    # the item names rather than by its line number (the numbers move as the
    # runbook grows; the mentions do not).
    runbook_text = (_REPO_ROOT / "docs" / "infra-runbook.md").read_text(encoding="utf-8")
    for snippet in (
        "export TORTOISE_RECORD_DSN=",
        "| TORTOISE_RECORD_DSN |",
        "TORTOISE_RECORD_DSN=postgresql://tortoise_record_app.",
    ):
        assert snippet in note, (
            f"the note does not enumerate the census hit {snippet!r} — a hit that "
            "predates #9548 is then invisible to whoever re-runs the census"
        )
        assert runbook_text.count(snippet) == 1, (
            f"{snippet!r} occurs {runbook_text.count(snippet)} times in the runbook, "
            "not the 1 hit the note's baseline attributes to it"
        )
    # The two documentation hits arrived with #9600, and that attribution is what
    # separates them from #9556/R8's operator invocation (#9585 review P2).
    assert "#9600" in note, (
        "the note does not attribute the two documentation hits to #9600, so a "
        "reader cannot tell them apart from a `record-restore` invocation"
    )
    # Claim 5 — a count is only meaningful with its TREE: the same path set reads 0
    # at this branch's start point, 1 after #9556/R8 and 3 after #9600, so pinning
    # one number to no commit is the stale-flag class this note exists to close.
    for sha in ("c5cbcbb4f", "d5c71a470", "7761252f"):
        assert sha in note, (
            f"the note does not pin the tree {sha!r} its count is measured at — a "
            "count without a tree is exactly the stale flag; a migration or a "
            "doc landing elsewhere silently re-numbers it"
        )
    # ⚠️ The SAME claim is published a second time, ~600 lines earlier, in §A8's
    # evidence cell — and the note extractor above is deliberately scoped to exclude
    # it ("so a TORTOISE_RECORD_DSN mention elsewhere in the doc can neither satisfy
    # nor fail these assertions"). That exclusion is a hole, not a guard: #9585
    # review r4 found the row still stating the "3 lines immediately before #9548"
    # reading at c5cbcbb4f's line numbers while the note said 5 at ff6379eb2, and
    # both passed. Pin the row to the same number and tree the note states.
    row = next(
        (
            line
            for line in _EPIC_DOC.read_text(encoding="utf-8").splitlines()
            if line.startswith("| **the record, in the hosted deployment**")
        ),
        None,
    )
    assert row, (
        "§A8's arming row is gone — the census it republishes has no reader"
    )
    assert f"**{count} lines at `ff6379eb2`**" in row, (
        f"§A8's evidence cell does not state the same {count}-line reading at "
        "ff6379eb2 that the census note states, so the document answers \"how many "
        "lines immediately before #9548\" twice, with different numbers"
    )
    assert "3 lines immediately before #9548" not in row, (
        "§A8's evidence cell still carries the retired 3-line reading — the "
        "figure the census note's own last paragraph calls stale"
    )
    # ⛔ AND THE ROW MUST NOT CALL `ff6379eb2` THE TREE BEFORE #9548 (#11064 review
    # r16, P1). The §G note was corrected — `ff6379eb2` is the #9601 merge, an
    # ANCESTOR of this branch's base, and the commit immediately before the #9548
    # arming (`785bb2b59`) is its parent `047c91f7a`, where the same path set
    # returns 10 lines, not 5 — but the identical sentence survived HERE, 893 lines
    # away, where this pin read only the count and the tree NAME. Measured:
    # `git rev-list --count ff6379eb2..047c91f7a` → 12, and the row's own parenthetical
    # already said "an ancestor of this branch's base", so the clause contradicted
    # the sentence it was attached to.
    # ⛔ AND NEITHER MAY THE PHRASE ITSELF BE BANNED, nor the claim be re-keyed past
    # (#11064 review r17, P3 — the two directions the r16 shape got wrong that the
    # r17 sha-exemption above does NOT cover). Both were measured by running the arm
    # as it stood against candidate cells:
    #   * the blanket `assert not re.search(r'what "?immediately before #9548"?
    #     means')` that opened this pin REDDED `ff6379eb2 is not what "immediately
    #     before #9548" means; that tree is `047c91f7a`` — a repair that BOTH denies
    #     the equivalence AND names the true tree. Banning a phrasing is not the same
    #     check as banning a claim: a guard that reds the fix teaches the next lane to
    #     delete the guard, and this file warns about exactly that false-RED class.
    #   * the predicate keyed on `immediately`, so `ff6379eb2 — the tree before #9548
    #     — reads 5 lines` — the same FALSE claim with one word paraphrased away —
    #     PASSED. A fail-open is the worse direction, so the key is `before #9548`.
    #
    # ⛔ THE STRUCTURE IS DEFAULT-DENY, AND THAT IS THE POINT (#11191 review, A1, P1).
    # Eight heads in one afternoon flipped this predicate between two shapes: keyed on
    # TOKENS (a fail-open — mentioning the true sha bought a pass) and keyed on DISTANCE
    # (a false RED — a truthful attribution longer than 60 characters was refused). Both
    # were the same mistake: the question is not "which token is present" or "which is
    # nearer" — it is WHICH TREE THE PHRASE REFERS TO, and whether the sentence says so.
    # So a phrase-bearing sentence now passes ONLY if the phrase's own claim is
    # CORRECTED — the true tree is its referent or its predicate, or a closed correction
    # idiom applies — and a phrase with NO named referent at all is the defect (that is
    # the accidental form a lane writes when it pastes a fresh figure: "the census
    # returns 3 lines immediately before #9548"). `main` refused that family; the
    # previous head accepted it, which is why this arm exists.
    # ⛔ AND EVERY ARM THAT CAN BE SATISFIED BY A DIFFERENT PROPOSITION IS CLAUSE-SCOPED
    # (#11191 review, A2, P1). The correction arms used to `return` for the WHOLE
    # sentence, so the §A8 cell's own correct denial exempted a false equivalence
    # injected beside it, and the `;`-closed denial was still open on `, and` and `—`.
    # A denial is a claim about the clause it sits in; `;`, `.`, `—`/`–` and the
    # coordinating `, and|but` end it. A plain comma does NOT — apposition ("10 lines at
    # `047c91f7a`, the tree immediately before #9548") depends on the comma.
    #: The tree that IS immediately before #9548 (the arming commit `785bb2b59`'s
    #: parent), and the tree the defect falsely calls it.
    _TRUE_TREE = "047c91f7a"
    _FALSE_TREE = "ff6379eb2"
    _PHRASE_RE = r"\bbefore\s+#9548"
    #: A figure spelled in WORDS. Hoisted here so every arm that asks "is a figure stated"
    #: asks it the same way — the earlier-clause guard below missed "three lines" while the
    #: no-referent arm already refused it (#11191 review r4, P2 — measured).
    #: ⛔ AND THE SPELLING IS NOT A CLOSED LIST THAT STOPS AT `twelve` (#11191 review,
    #: F4, P2, re-confirmed live — MEASURED). Every figure above twelve (and `zero`)
    #: walked past the list, so a phrase-dated figure with no referent was graded
    #: "attributes nothing" one word past the guard:
    #:   "The census returns thirteen lines immediately before #9548." -> ACCEPTED
    #:   "The census returns zero lines immediately before #9548."     -> ACCEPTED
    #: which is the A1 fail-open the comment above claims to close. The list is now the
    #: FIGURE GRAMMAR a writer actually uses (0-99 compounds, hundreds, thousands,
    #: dozens) rather than the first twelve, because a closed list that runs out is a
    #: fail-open whose boundary is whatever number the next writer picks. Hoisting is
    #: also what makes the widening reach BOTH arms that ask this question — this one and
    #: the earlier-clause guard below — so they cannot disagree about what a figure is.
    _FIG_ONES = r"one|two|three|four|five|six|seven|eight|nine"
    _FIG_TEENS = (
        r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen"
    )
    _FIG_TENS = r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    _WORD_FIGURE = (
        r"\b(?:(?:" + _FIG_ONES + r")|(?:" + _FIG_TEENS + r")"
        r"|(?:" + _FIG_TENS + r")(?:[-\s](?:" + _FIG_ONES + r"))?"
        r"|hundred|thousand|dozen|dozens|hundreds|thousands|zero)\b"
    )
    #: ⛔ …AND A FIGURE IS A FIGURE ONLY IN A FIGURE POSITION (#11191 review, F4 — the
    #: mirror direction, MEASURED). The grammar alone fires on any occurrence, so an
    #: ordinary pointer was REFUSED: "Re-read the census note before #9548 ONE more
    #: time." dates no figure at all, and `origin/main` accepts it. A word becomes a
    #: FIGURE when a counted noun binds it or a counting verb takes it as its object;
    #: `one more time` is neither. Both callers (the no-referent arm and the post-phrase
    #: referent's `_between` guard) ask it THIS way.
    _WORD_FIGURE_IN_POSITION = re.compile(
        r"\b(?:returns?|returned|reads?|states?|stated|gives?|gave|shows?|"
        r"showed|sees?|saw|yields?|yielded|reports?|reported|counts?|counted|"
        r"has|have|holds?|held)\s+"
        r"(?:about\s+|exactly\s+|only\s+|still\s+|just\s+|the\s+same\s+)?"
        + _WORD_FIGURE
        + r"|"
        + _WORD_FIGURE
        + r"\s+(?:lines?|rows?|entries|matches|occurrences|hits|results)\b",
        re.I,
    )
    #: ⛔ A FIGURE MAY BE DENIED, AND A DENIAL IS NOT A READING (#11191 review r10, P2 —
    #: MEASURED at `870951926e0`, `origin/main` ACCEPTS both):
    #:   "The census was not one line immediately before #9548."            -> REFUSED
    #:   "It was NOT empty immediately before #9548 — and it was not one line either."
    #:                                                                       -> REFUSED
    #: The spelled figure `one` in a FIGURE POSITION bound to a negated copula, so the
    #: no-referent arm read a DENIAL of the reading as the reading itself: a truthful
    #: sentence in the §A8 cell ("… it was not one line either") reds, which is the
    #: false-RED class this file names as the way a pin gets deleted. The cue is the
    #: negator DIRECTLY in front of the figure (one optional adverb allowed) — a closed
    #: set, never a window, precisely so an assertion cannot borrow it: "no fewer than 3
    #: lines" and "not more than 3 lines" keep their figure and stay refused, because
    #: `fewer` / `more than` are not in the admitted set.
    _DENIED_FIGURE = re.compile(
        r"\b(?:not|never|nor)\s+(?:even\s+|just\s+)?$"
        r"|\b\w+n['\u2019]t\s+(?:even\s+|just\s+)?$",
        re.I,
    )
    #: …and the FRAMES THAT REVERSE IT: a denial of the denial is an assertion, so a
    #: negator inside one may not buy the admission above (#11191 review r10, P2).
    _POLARITY_FRAME = re.compile(
        r"\b(?:nothing|no\s+one|nobody)\s+(?:suggests?|says?|shows?|indicates?)\b"
        r"|\bit\s+is\s+(?:not\s+the\s+case|not\s+true|false|not\s+correct|untrue)\s+that\b"
        r"|\bit\s+would\s+be\s+(?:wrong|false)\s+to\s+say\b"
        r"|\bno\s+(?:evidence|indication|basis|data|signs?|hints?|traces?|proof)\b"
        r"|\bno\s+reason\s+to\s+(?:think|believe|say|suggest|conclude)\b",
        re.I,
    )

    def _states_a_figure(text: str) -> bool:
        """True when `text` STATES a figure (a reading dated to the phrase).

        A figure the sentence DENIES — "it was not one line either" — is a correction of
        the reading, not a reading, so it does not date anything to the phrase. Only a
        negator directly in front of the figure counts as a denial (see `_DENIED_FIGURE`),
        and only while no frame in front of it REVERSES the polarity (#11191 review r10,
        P2 — MEASURED: "Nothing suggests the census was not 3 lines immediately before
        #9548" and "It is not the case that the census was not 3 lines …" both ASSERT the
        retirement and both borrowed the admission as first written). The frame check
        reuses `_DOUBT_FRAME`, the arm this predicate already applies to the sha channel,
        so the two channels cannot disagree about what a doubt frame is.
        """
        for _figure in list(re.finditer(_WORD_FIGURE_IN_POSITION, text)) + list(
            re.finditer(r"\d", text)
        ):
            _cue_start = max(0, _figure.start() - 16)
            _cue = _DENIED_FIGURE.search(text[_cue_start : _figure.start()])
            if not _cue:
                return True
            _frame_end = _cue_start + _cue.start()
            _frame = text[max(0, _frame_end - 60) : _frame_end]
            if _DOUBT_FRAME.search(_frame) or _POLARITY_FRAME.search(_frame):
                return True
        return False

    #: The clause boundaries, for every arm that must not cross one (#11191 review, A2).
    #: ⛔ THE CONJUNCTION IS THE BOUNDARY, NOT THE COMMA (#11191 review A2's class,
    #: measured on THIS head). With `,\s+and` only, the comma-less spelling was still one
    #: clause, so "`ff6379eb2` is not the arming commit and the tree before #9548 is
    #: `ff6379eb2`." was denied by its first clause and the false equivalence in the
    #: second passed — and the same hole disabled the pin on the §A8 cell itself, because
    #: the reviewer's injection ("… the arming commit's parent) and `ff6379eb2` is the
    #: tree before #9548") lands in the SAME clause as the cell's own "**NOT** the tree
    #: immediately before #9548", so `_DENIED_IN_PREDICATE` exempted the injected claim.
    #: The comma is optional because writers omit it; the referent rule reads the whole
    #: sentence, so cutting here cannot detach a phrase from the tree it attributes to.
    _CLAUSE_DELIM = re.compile(
        r"[.;\u2014\u2013]|,?\s*\b(?:and|but|while|whereas|yet)\b", re.I
    )
    #: ⛔ AN ANCESTRY RELATION IS NOT THE EQUIVALENCE (#11191 review r11, P1 — MEASURED
    #: false RED, and a REGRESSION against `origin/main`). The defect is the COPULA —
    #: "`ff6379eb2` IS the tree before #9548". A sentence that instead states that
    #: `ff6379eb2` is an ANCESTOR OF the true tree makes a different, TRUE claim:
    #: `git merge-base --is-ancestor ff6379eb2 047c91f7a` exits 0 and
    #: `git rev-list --count ff6379eb2..047c91f7a` is 12. The referent rule reads the
    #: bare sha before the phrase and refused it anyway — `origin/main` accepts it, so
    #: this head was strictly worse than the guard it replaces, which is the false-RED
    #: class this file names as the way a pin gets deleted. Measured REFUSED at
    #: `c281ea64a` (the r11 head), i.e. the r10/r11 rounds did not reach this one.
    #: The kinship vocabulary is CLOSED on purpose (never an open `\w+`), and the
    #: exemption additionally requires the TRUE tree in the SAME CLAUSE — so a bare
    #: "`ff6379eb2` is an ancestor of the tree before #9548" with no referent stated is
    #: still refused, and the relation cannot become an alibi for the equivalence.
    _ANCESTRY_OF_TREE = re.compile(
        r"\b(?:is|was)\s+(?:\d+\s+commits?\s+before"
        r"|(?:an?|the)\s+(?:ancestor|predecessor)\s+(?:of|to)"
        r"|the\s+parent\s+of)\s+the\s+tree"
        r"|\b(?:an?|the)\s+(?:ancestor|predecessor)\s+(?:of|to)\s+the\s+tree"
        # ⛔ …AND THE COMPACT SPELLING, which names the true tree DIRECTLY instead of
        # saying "the tree" and hoping the sha follows (#11191 review r11, P1 — MEASURED
        # false RED and a regression against `origin/main`). Requiring the literal words
        # "the tree" accepted the long form and refused its twin one word shorter:
        #   "`ff6379eb2` is the #9601 merge, an ancestor of `047c91f7a`, the tree
        #    immediately before #9548."                              -> REFUSED (must pass)
        # Same relation, same referent; a reword must not flip the verdict.
        r"|\b(?:an?|the)\s+(?:ancestor|predecessor)\s+(?:of|to)\s+`?" + _TRUE_TREE
        + r"|\b(?:is|was)\s+\d+\s+commits?\s+before\s+`?" + _TRUE_TREE,
        re.I,
    )
    #: A parenthetical, dash-delimited or comma-delimited INCISE — an ASIDE, i.e. a
    #: different proposition from the one the phrase is predicated of. BLANKED before the
    #: REFERENT is read, so naming the true tree inside an aside cannot become the referent
    #: of a claim whose subject is `ff6379eb2` (#11191 review r4, P2 — measured ACCEPTED at
    #: `46c01c324e8`: "`ff6379eb2` is (as `047c91f7a` shows) the tree before #9548").
    #: Length-preserving on purpose — the caller's offsets are positions in `norm`.
    _INCISE = re.compile(
        r"\([^()]*\)"
        # ⛔ BOTH DASHES — `_CLAUSE_DELIM` recognises `–` as a clause boundary, so a lane
        # that writes the EN dash re-admitted the defect one character from the arm's own
        # control (#11191 review r4, P1 — measured).
        r"|[\u2014\u2013][^\u2014\u2013]*[\u2014\u2013]"
        # …and the SPACED HYPHEN-MINUS is the ASCII rendering of the same aside (#11191
        # review r5, P2 — measured): `_CLAUSE_DELIM` does not split on it, so without this
        # the clause tier sees the aside's sha as the nearest referent.
        r"|\s-\s[^-]{1,200}?\s-\s"
        r"|, [^,;.]{1,200}, ",
        re.I,
    )
    #: Any SHA-SHAPED token is a candidate REFERENT — not only the two trees, because
    #: naming a THIRD tree as the pre-#9548 tree is the same defect (#11191 review, A1):
    #: "`c5cbcbb4f` is the tree immediately before #9548." was REFUSED by `main` and
    #: ACCEPTED at the previous head, whose referent rule could only see the two shas it
    #: knew. A letter AND a digit are required so a digits-only token (a migration
    #: version such as `20261102000002`, a date) is never read as a tree.
    _SHA_LIKE_RE = re.compile(
        r"\b(?=[0-9a-f]{7,40}\b)(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b", re.I
    )
    #: A CLOSED set of post-phrase refutations — the sentence quotes the false claim and
    #: then calls it false. Closed on purpose: a bare `not` after the phrase is how a
    #: FIGURE gets corrected ("the census there returns 5, not 10"), so admitting that
    #: would be a fail-open; `"<phrase> is false|wrong|…"` can only be a refutation.
    #: ⛔ And it is matched AT the phrase occurrence (`match`, not a sentence-wide
    #: search), so a refutation attached to one phrase cannot exempt another in the same
    #: sentence (#11191 review, A2).
    #: ⛔ AND CLOSED IN THE OTHER HALF TOO — the subject must be a DEMONSTRATIVE or a
    #: named claim word, and the span may not be a free-floating clause (#11191 review r3,
    #: P1 — measured). An open subject window re-admitted the exact claim this arm exists
    #: to refuse, because ANY `<subject> is wrong` within reach bought the exemption:
    #:   "`ff6379eb2` is the tree before #9548, but the 5-line figure is wrong."  -> PASS
    #:   "`ff6379eb2` is the tree before #9548; the census prose is wrong."       -> PASS
    #: A correction of a FIGURE or of the PROSE is not a refutation of the EQUIVALENCE,
    #: and both of those shapes are controls below.
    _REFUTED_AFTER = re.compile(
        _PHRASE_RE
        + r"\s*(?:[,;:\u2014\u2013-]\s*)?(?:(?:and|but)\s+)?"
        + r"(?:(?:which|that|this)\s+|the\s+(?:claim|reading|statement)\s+)?"
        # ⛔ AND IT MAY NOT TAKE AN OBJECT (#11191 review r4, P1 — measured):
        # "…before #9548; the cell is wrong about the month" and "…, which is wrong
        # about the month it shipped in" PASSED, because `wrong` was enough. A refutation
        # of the EQUIVALENCE ends its claim there; one that goes on to qualify WHAT is
        # wrong is refuting something else.
        + r"(?:is|was)\s+(?:(?:false|wrong|mistaken|incorrect)\b|not\s+true\b|untrue\b)"
        # ⛔ …AND THE SAME QUALIFIER SET AS THE NEXT-CLAUSE ARM (#11191 review r4, P3 —
        # measured): "wrong WITH RESPECT TO the count" / "wrong REGARDING the count" is
        # a refutation of the FIGURE, and this arm is reached FIRST for a `;`-joined
        # refutation, so leaving the prepositional synonyms out of ONE of the two arms
        # leaves the class open (extending only the next-clause arm changed nothing).
        + r"(?!\s+(?:about|on|in|for|at|as\s+to|regarding|with\s+respect\s+to|"
        r"concerning|as\s+regards|in\s+respect\s+of)\b)",
        re.I,
    )
    #: …and the same refutation carried into the NEXT clause by a DEMONSTRATIVE — "The
    #: idea that `ff6379eb2` is the tree before #9548 — that is wrong". Without this arm
    #: clause-scoping reds the natural refutation (#11191 review, A2); with an OPEN
    #: subject it would re-admit "…, and the 5-line figure is wrong" — so the subject is
    #: the closed demonstrative set and nothing else.
    #: ⛔ AND IT MAY NOT TAKE AN OBJECT EITHER — the guard `_REFUTED_AFTER` already
    #: carries (#11191 review, P1, re-confirmed live — MEASURED). Moving the same
    #: predicate one clause ahead LOST the guard, so a refutation of something else
    #: bought the exemption one clause later:
    #:   "`ff6379eb2` is the tree before #9548 — this is wrong about the month it
    #:    shipped in."  ->  `_REFUTED_AFTER` rejects it, `_REFUTED_NEXT_CLAUSE` matched
    #: `origin/main` refuses that sentence; the previous head ACCEPTED it. A refutation
    #: of the EQUIVALENCE ends its claim there; one that goes on to qualify WHAT is wrong
    #: is refuting something else, whichever clause it sits in.
    _REFUTED_NEXT_CLAUSE = re.compile(
        r"\A\W*(?:(?:and|but)\s+)?(?:that|this|which)\s+(?:is|was)\s+"
        # ⛔ …AND THE OBJECT GUARD APPLIES HERE TOO (#11191 review r4, P1 — measured
        # ACCEPTED at `46c01c324e8`): "`ff6379eb2` is the tree before #9548; this is wrong
        # ON THE CENSUS COUNT" refutes the FIGURE, not the predication, and this arm
        # exempted the equivalence it sits beside. `_REFUTED_AFTER` already carries this
        # exact qualifier guard, so without it here that arm is the one open door in the
        # set — and a control below pins it.
        r"(?:false|wrong|mistaken|incorrect)\b"
        r"(?!\s+(?:about|on|in|for|at|as\s+to|regarding|with\s+respect\s+to|"
        r"concerning|as\s+regards|in\s+respect\s+of)\b)",
        re.I,
    )
    #: The COMPLEMENT names the false sha as NOT the tree — "the tree before #9548 is
    #: `047c91f7a`, NOT `ff6379eb2`". This is the fixer's most natural correction, and the
    #: symmetric arm below would otherwise refuse it (#11191 review r4, P1).
    _NOT_FALSE_SHA = re.compile(
r"\b(?:not|never|rather\s+than)\s*[`*_]{0,3}" + re.escape(_FALSE_TREE), re.I
    )
    #: The denial tokens a correction can carry BETWEEN the sha and the phrase. `isn't`,
    #: `cannot be` and `rather than` were dropped at `d4ba94671e` along with their
    #: controls, and the whole truthful family went RED (#11191 review, A3, P2 —
    #: measured). One alternation, so a spelling cannot be "temporarily" lost again.
    #: ⛔ …AND A BARE `not` MAY NOT TAKE A SHA-SHAPED TOKEN AS ITS OBJECT (#11191
    #: review, P1 — MEASURED, and it is the same ordering defect this PR already fixed
    #: for the refutation arms, surviving one arm further along). `<sha>, not <sha>, is
    #: the tree before #9548` is a CLEFT: the `not` negates the APPOSITIVE COMPLEMENT,
    #: not the predication, so the sentence ASSERTS the equivalence about whichever sha
    #: FRONTS it. Unguarded, the bare alternative decided both directions by surface form
    #: and the phrase's owner became the appositive:
    #:   "`ff6379eb2`, not `047c91f7a`, is the tree before #9548."  -> ACCEPTED (defect)
    #:   "`047c91f7a`, not `ff6379eb2`, is the tree before #9548."  -> ACCEPTED (truth)
    #: One arm, two OPPOSITE propositions — which is why a one-directional probe cannot
    #: see it. Guarded, a bare `not` can only deny a PREDICATE ("not the tree …"); the
    #: cleft is decided explicitly by `_CLEFT_PREDICATION` below, on the sha that is the
    #: SUBJECT. The `is not` / `isn't` / `cannot be` / `rather than` alternatives are
    #: untouched — each already binds the negator to a copula, which IS the predication.
    #: ⛔ …AND THE OTHER TWO COMPLEMENT MARKERS TAKE THE SAME GUARD (#11191 review r7,
    #: P1 — MEASURED). `rather than` and `instead of` were left unguarded, so a
    #: sha-shaped OBJECT after them was read as a denial of the predication and the cleft
    #: arm below was unreachable — the claim accepted, and its truth twin accepted too:
    #:   "`ff6379eb2` rather than `047c91f7a`, is the tree before #9548."   -> ACCEPTED
    #:   "`ff6379eb2` instead of `047c91f7a`, the tree before #9548, reads 5 lines."
    #:                                                                        -> ACCEPTED
    #: A sha-shaped object is the APPENDIX in every one of these spellings, so all three
    #: markers must leave it to `_CLEFT_PREDICATION`, which reads the FRONTED sha. The
    #: genuine denials (`rather than the tree before #9548`, `rather than the false sha`)
    #: are untouched: their object is a phrase or the FALSE tree, neither true-sha-shaped.
    #: ⛔ THE OBJECT MAY BE DECORATED (#11191 review r8, P2 — MEASURED). A bare `\s*`
    #: lookahead only saw the UNDECORATED spelling, so a markdown link or a parenthetical
    #: restored the very fail-open this guard closes — measured ACCEPTED at r7:
    #:   "`ff6379eb2` rather than [`047c91f7a`](https://x/047c91f7a), is the tree before
    #:    #9548."                                                          -> ACCEPTED
    #:   "`ff6379eb2` rather than (see `047c91f7a`), is the tree before #9548." -> ACCEPTED
    #: A `\b` cannot hold after `[` or `(`, so the sha set is preceded by the same closed
    #: decoration set the appositive arm admits, not by raw whitespace.
    #: A parenthesised span, blanked wherever an arm must not read an aside as prose
    #: (mirrors `_INCISE`, which does the same for dash asides).
    _PAREN = re.compile(r"\([^)]*\)")
    _SHA_OBJECT = (
        # ⛔ THE DECORATION IS A CLOSED WORD SET, UP TO THREE WORDS (#11191 review r9, P2 —
        # MEASURED): a fixed list of four spellings ran out at "the tree sha `X`" /
        # "its parent `X`" / "the true tree `X`", each of which restored the fail-open.
        # A closed VOCABULARY with a length bound admits the decorations writers actually
        # use and still cannot absorb a predicate (the words are nouns/determiners, not
        # verbs).
        r"[\s(\[]*(?:(?:see|namely|the|its|their|a|an|true|last|only|same|own|parent|"
        r"tree|commit|sha|revision|tip|merge|arming)\s+){0,3}[`*_]{0,3}"
        + _SHA_LIKE_RE.pattern
    )
    _NEG = (
        r"(?:(?:is|was|are|were)\s*(?:n't|\s+not\b)"
        r"|(?:cannot|can\s*not|could\s*not|can't)\s+be"
        r"|never|neither"
        r"|(?:rather\s+than|instead\s+of)(?!" + _SHA_OBJECT + r")"
        # ⛔ `not` IS A WORD, NOT A SUBSTRING (#11191 review r11, P2 — MEASURED):
        # without the boundaries, "the `ff6379eb2` commit is NOTABLE as the tree before
        # #9548" and "per the NOTE, is the tree before #9548" read as denials and a false
        # equivalence passed — the closed negation SET was matching inside ordinary prose
        # words, which is the opposite of closed.
        r"|\bnot\b(?!\s*" + _SHA_LIKE_RE.pattern + r"))"
    )
    #: A DOUBT FRAME — the sentence denies DOUBT, not the claim (#11191 review r4, P2 —
    #: measured ACCEPTED at `46c01c324e8`). "Nothing suggests X is not Y" ASSERTS X is Y,
    #: and `_DENIED_BY_SHA` sees only the inner `is not` and reads it as the correction.
    #: Closed set, and consulted only BEFORE the false sha, so a denial that carries its
    #: frame AFTERWARDS ("… is not the tree before #9548, no doubt") is untouched.
    _DOUBT_FRAME = re.compile(
        # ⛔ ONLY A NEGATIVE-QUANTIFIER FRAME **WITH A NEGATIVE-POLARITY VERB** (#11191
        # review r4/r5, P1/P2 — measured): "nothing suggests X is NOT Y" asserts X is Y,
        # because the frame cancels the complement's negation. But "Nobody DOUBTS that X is
        # not Y" is AFFIRMATIVE (it endorses the denial), and a quantifier-only set reded
        # it — so the quantifier must take a suggestive verb, never `doubt`.
        r"(?:\b(?:nothing|no\s+one|nobody|nowhere)\s+"
        r"(?:suggests?|says?|shows?|indicates?|implies|establishes|has\s+shown)\b"
        r"|\bno\s+(?:evidence|indication|basis|data|signs?|hints?|traces?|proof)\b"
        r"|\bno\s+reason\s+to\s+(?:think|believe|say|suggest|conclude)\b"
        r"|\b(?:cannot|can't)\s+be\s+ruled\s+out\b)",
        re.I,
    )
    #: A denial whose SUBJECT is a sha — "`ff6379eb2` is not the tree before #9548",
    #: `isn't`, `cannot be`, `is an ancestor rather than`, or `is not what "before
    #: #9548" means`. Required as an arm of its own because the generic adjacent denial
    #: cannot see past `is not what …`. ⛔ Evaluated against the PHRASE'S CLAUSE only
    #: (#11191 review, A2, P1 — measured): with the previous sentence-wide window,
    #: `"ff6379eb2` is not the arming commit; the tree before #9548 is `ff6379eb2`."
    #: passed, because the denial of one proposition exempted the assertion in the next.
    _DENIED_BY_SHA = re.compile(
        r"\b(?:047c91f7a|ff6379eb2)\b[^.;\u2014\u2013]{0,30}?" + _NEG
        + r"[^.;\u2014\u2013]{0,40}?" + _PHRASE_RE,
        re.I,
    )
    #: A denial IN THE PREDICATE POSITION: `not` must be followed directly by the
    #: predicate ("**NOT** the tree immediately before #9548" — the real §A8 cell's own
    #: phrasing). ⛔ This is deliberately NOT "a `not` somewhere in the previous 60
    #: characters": that window also swallows "It is not disputed that `ff6379eb2` is
    #: the tree before #9548", which ASSERTS the equivalence inside a denial of doubt —
    #: a fail-open no control would notice.
    _DENIED_IN_PREDICATE = re.compile(
        r"\b(?:not|never)\b\s*(?:the\s+)?(?:tree\s+)?(?:immediately\s+)?"
        + _PHRASE_RE,
        re.I,
    )
    #: THE CLEFT — `<sha>, not <sha>, is the tree before #9548` (#11191 review, P1 —
    #: MEASURED). A fronted appositive negation does NOT negate the predication: it
    #: negates the APPENDIX, so the sha that FRONTS the sentence is the SUBJECT of the
    #: claim. That makes the two spellings OPPOSITE propositions decided by the same
    #: surface form, which is why the arm is written on the fronted sha and not on the
    #: negator:
    #:   "`ff6379eb2`, not `047c91f7a`, is the tree before #9548."  -> the CLAIM
    #:   "`047c91f7a`, not `ff6379eb2`, is the tree before #9548."  -> the correction
    #: ⛔ The fronted sha is compared as a PREFIX, like the referent rule, so a full
    #: 40-hex subject is the same tree as its 9-char spelling. A THIRD tree fronted is
    #: the A1 defect (an invented pre-#9548 tree), so only the TRUE tree may front it.
    #: ⛔ AND THE COMMA IS OPTIONAL (#11191 review, F1 — MEASURED on this head): the
    #: arm as first written required it, so the comma-less spelling of the same cleft was
    #: still ACCEPTED while the comma form was refused —
    #:   "`ff6379eb2` not `047c91f7a` is the tree before #9548."  ->  PASSED
    #: — and an incise rule cannot see that form either (there is no aside to blank), so
    #: nothing else in the predicate reaches it. Writers omit the comma; the arm reads the
    #: FRONTED sha, which is the subject in both spellings.
    #: ⛔ AND THE GAP IS THE CLAUSE, NOT A CHARACTER COUNT (#11191 review r8, P2 —
    #: MEASURED): a DECORATED complement (a markdown link, "the tree <sha>") puts the
    #: phrase 31+ characters after the marker, so the 30-char window silently skipped the
    #: sentence and it fell through to the referent rule — the same fail-open, one spelling
    #: further out:
    #:   "`ff6379eb2` rather than (the census row's own measured value) `047c91f7a`, is
    #:    the tree before #9548."                                       -> ACCEPTED at 30
    #: ⛔ …AND A PARENTHESISED `and` MAY NOT CLOSE THE CLAUSE ON IT (#11191 review r9, P2 —
    #: MEASURED): `_clause_containing` cuts at `, and`, so
    #:   "`ff6379eb2` rather than `047c91f7a` (and the census at both trees), is the tree
    #:    before #9548."                                                -> ACCEPTED
    #: handed the arm a FRAGMENT with the fronted sha outside it. The caller therefore
    #: searches from the last HARD delimiter (`.`, `;`, a dash), which is the unit the
    #: subject lives in; the cap stays only as a runaway guard (400) because the
    #: fronted-sha anchor is what bounds this arm.
    _CLEFT_PREDICATION = re.compile(
        r"\b([0-9a-f]{7,40})\b\s*,?\s*(?:(?:and|but)\s+)?"
        r"(?:not|rather\s+than|instead\s+of)\b"
        r"(?:(?!,?\s*\b(?:and|but|while|whereas|yet)\b)[^.;\u2014\u2013]){0,400}?"
        + _PHRASE_RE,
        re.I,
    )
    #: ⛔ THE PHRASE PREDICATED OF A NAMED NON-TREE OBJECT (#11191 review r7, P1 —
    #: MEASURED). Step 5 decides "attributes nothing" by looking for a FIGURE, so a wrong
    #: tree named in PROSE carried no digit and passed:
    #:   "The tree immediately before #9548 is the arming commit itself."   -> ACCEPTED
    #:   "The last tree before #9548 is the arming commit's son."          -> ACCEPTED
    #: The doc's own cell names the true pre-#9548 tree as *"`047c91f7a`, the arming
    #: commit's parent"*, so dropping `'s parent` is the one-word decay this pin exists to
    #: catch — and it is the same A1 class the numeral channel already reds. Closed on
    #: purpose: the object must be a tree-shaped noun, and the PARENT relation is the only
    #: exemption (see `_PREDICATE_NAMES_TRUE_TREE`), so "the arming commit itself" and
    #: "the arming commit's son" stay refused.
    #: ⛔ THE ADVERB SLOT IS A GRAMMAR, NOT THE LIST OF WORDS THE EARLIER ROUNDS HAPPENED
    #: TO PROBE, AND NOT A COUNT EITHER (#11191 review r10 residual 4, P2; r11 F2, P1 —
    #: MEASURED). The closed set stopped at nine words, so the claim walked past it one
    #: adverb beyond it:
    #:   "…is, IN PRACTICE, the arming commit itself"        -> ACCEPTED
    #:   "…is INDISPUTABLY the arming commit itself"          -> ACCEPTED
    #:   "…is EFFECTIVELY the arming commit's son"            -> ACCEPTED
    #: — and the first cut of the widening bounded the class with `{0,2}`, so the next
    #: stack past two was open again, as `origin/main` REFUSES and this head accepted:
    #:   "…is NOW QUITE CLEARLY the arming commit itself"     -> ACCEPTED
    #:   "…is ALMOST CERTAINLY STILL the arming commit itself" -> ACCEPTED
    #: That is the same list-runs-out fail-open with the list spelled as a number: an
    #: adverb is an OPEN lexical category in English (the `-ly` derivation is its
    #: productive subclass), so the slot is a REPETITION, not a count. The repeat is
    #: linear, never catastrophic: the two alternatives are disjoint at their first
    #: character (a comma vs a letter).
    #: ⛔ AND THE NEGATIVE-POLARITY ADVERBS ARE EXCLUDED — as a CLASS, by the same rule
    #: `never` is excluded by, not by adding words to a list (#11191 review r11 F3, P2 —
    #: MEASURED): `hardly` / `scarcely` / `barely` deny the predication (which is a
    #: correction, so the arm must not read them as the claim), and an `-ly` class that
    #: swallowed them REFUSED a truthful denial that the previous head accepted — a
    #: regression in the false-RED direction.
    #: ⛔ AND AN INTENSIFIER MAY PRECEDE THE ADVERB (#11191 review r11 F2, P1 — MEASURED):
    #: `almost certainly` is ordinary English and the `-ly` alternative alone cannot reach
    #: it, so "is ALMOST CERTAINLY still the arming commit itself" was ACCEPTED while
    #: `origin/main` refuses it. Intensifiers are a genuinely CLOSED class in English
    #: (unlike adverbs), which is why this one is spelled out and the adverb itself is not.
    _INTENSIFIER = (
        r"(?:almost|nearly|very|so|too|much|more|most|less|least|far|quite|rather|really|"
        r"even|just|simply|entirely|completely|wholly|utterly|partly)\s+"
    )
    _ADVERB_WORD = (
        r"(?:(?!(?:hardly|scarcely|barely|rarely|seldom)\b)(?:"
        + _INTENSIFIER
        + r")?(?:[A-Za-z][A-Za-z-]*ly"
        r"|still|now|later|also|already|actually|really|"
        r"apparently|instead|then|even|indeed|only|just|again|always|nonetheless|"
        r"nevertheless|however|therefore|thus|hence|rather|quite|too|"
        r"in\s+fact|as\s+a\s+matter\s+of\s+fact|to\s+date))\b"
    )
    _PREDICATE_ADVERB = (
        r"(?:(?:"
        + _ADVERB_WORD
        + r"\s*)"
        # …and a COMMA-DELIMITED adverbial incise is the same slot at word-phrase length
        # ("is, in practice, the arming commit itself"), in ANY position relative to the
        # adverbs. It must close on a comma, so it can never swallow the object.
        r"|,[^,.;\u2014\u2013]{1,40},\s*)*"
    )
    #: ⛔ THE MIRROR OF `_PREDICATED_OBJECT`: a PROSE TREE SUBJECT predicated OF the
    #: phrase — "The arming commit itself IS the tree immediately before #9548"
    #: (#11191 review, P1 — MEASURED, and a REGRESSION against `origin/main`, which
    #: refuses all three). `_PREDICATED_OBJECT` reads the object AFTER the phrase, so
    #: the pre-posed word order fell through every arm to step 5, which counts figures
    #: only and saw nothing to refuse. The doc's own cell names the true pre-#9548 tree
    #: as *"`047c91f7a`, the arming commit's parent"*, so `… itself` / `…'s son` / a bare
    #: `… commit` is exactly the one-word decay this pin exists to catch.
    #: The PARENT relation is the exemption, in either word order, so the truthful
    #: sentences stay green: "the arming commit's parent is the tree immediately before
    #: #9548" and "the parent commit of `785bb2b59` is …" (the latter does not match this
    #: arm's `the <tree-noun>` anchor at all).
    _PREPOSED_PREDICATED = re.compile(
        r"(?:^|[,;:]|\band\b|\bbut\b)\s*(?:the|a|an)\s+"
        r"(?P<subj>(?:(?:arming|merge|revert|fix|feature|squash|base)\s+)?"
        r"(?:commit|merge|tree|branch|tip|revision))\b"
        r"(?P<rel>\s+itself|[\u2019']s\s+(?:son|child|ancestor|offspring|parent))?"
        r"\s*,?[^,.;]{0,30}?,?\s+(?:is|was|remains|stays|becomes)\s+"
        r"(?:(?:still|now|definitely|actually|in\s+practice)\s+)?"
        r"(?:the\s+)?(?:last\s+|only\s+|exact\s+)?(?:tree\s+)?(?:immediately\s+)?$",
        re.I,
    )
    _PREDICATED_OBJECT = re.compile(
        r"\s*(?:is|was|are|were|means|denotes|refers\s+to|stays?|stayed|remains?|"
        r"remained|becomes?|became|continues?\s+to\s+be|continued\s+to\s+be|"
        # ⛔ …AND THE MODAL SPELLINGS (#11191 review r11, P1 — MEASURED): the verb
        # alternation covered the copula and its stative cousins but not `has to be` /
        # `must be` / `would be`, so "The tree immediately before #9548 HAS TO BE the
        # arming commit." was ACCEPTED while "…IS the arming commit." refused.
        r"(?:has|have|had)\s+to\s+be|(?:must|should|would|will|can|could|may|might)\s+be)"
        # ⛔ …AND A COMMA-DELIMITED ADVERBIAL SITS AS AN INCISE between the copula and
        # the object ("is, in practice, the arming commit itself"), so a slot for it is
        # required beside the one-word adverbs; a bounded comma incise cannot swallow the
        # object, because it must CLOSE on a comma (#11191 review r10 residual 4).
        r"\s*(?:,[^,.;\u2014\u2013]{1,40},\s*)?"
        # ⛔ ONE ADVERB IS ADMISSIBLE (#11191 review r9, P1 — MEASURED): with the copula
        # followed IMMEDIATELY by the object, "is STILL the arming commit itself" and
        # "stayed the arming commit's son" were ACCEPTED — the closure claim was one word
        # from failing open. Never an open `\w+` (that swallows the object itself); the
        # productive `-ly` class above is the widening that does not.
        r"\s*"
        + _PREDICATE_ADVERB
        + r"(?:the\s+|a\s+|an\s+)?"
        + r"(?:(?:arming|merge|revert|fix|feature|squash|base|parent)\s+)?"
        + r"(?:commit|merge|tree|branch|sha|tip|subject|revision)\b",
        re.I,
    )
    #: …and the EXEMPTION: a predication that goes on to name the TRUE tree by the
    #: PARENT RELATION — "the arming commit's parent", "the parent commit of
    #: `785bb2b59`". Naming the true tree is the correction, which is why this arm must
    #: not refuse it (the false-RED direction this file names as the way a pin gets
    #: deleted).
    #: ⛔ THE RELATION IS NAMED BY THE WORD "parent", NOT BY A TRAILING SHA (#11191
    #: review r8, P2 — MEASURED). Keying the `of …` spelling on "a sha follows `of`" made
    #: the arm RELATION-BLIND, so every other relation bought the exemption:
    #:   "The tree immediately before #9548 is the merge commit of `785bb2b59`."  -> ACCEPTED
    #:   "The tree immediately before #9548 is the revert commit of `785bb2b59`." -> ACCEPTED
    #: so the `of <sha>` form is admitted only when the PREDICATE already says "parent".
    _PARENT_RELATION = re.compile(
        r"^(?:['\u2019]s\s+parent\b|\s+of\s+(?:the\s+)?parent\b)", re.I
    )
    #: …and the `of <sha>` spelling, admissible ONLY behind a "parent" predicate.
    #: ⛔ THE MIRROR OF `_PREDICATED_OBJECT`: the PHRASE may be the PREDICATE of a
    #: tree-shaped SUBJECT (#11191 review r11, P1 — MEASURED). The object arm reads only
    #: AFTER the phrase, so the same claim written the other way round named no tree, dated
    #: no figure, and passed the default-deny digit test — ACCEPTED at `b0e8e2006d4` and
    #: still ACCEPTED at `77d4a3eadd4`:
    #:   "The arming commit is the tree immediately before #9548."
    #:   "The arming commit itself is the tree immediately before #9548."
    #:   "The arming commit's son is the tree immediately before #9548."
    #:   "The arming commit must be the tree immediately before #9548."
    #: All four assert a tree that is NOT the pre-#9548 tree — the A1 defect spelled
    #: without a sha, which is why the numeral channel never saw it. The exemptions are the
    #: object arm's: a subject carrying the PARENT relation ("the arming commit's parent is
    #: the tree before #9548") is the true tree and stays accepted, and a sha subject is the
    #: referent rule's business, not this arm's.
    _PREDICATED_SUBJECT = re.compile(
        r"\b(?:the|a|an)\s+"
        r"(?:(?:arming|merge|revert|fix|feature|squash|base)\s+)?"
        r"(?:commit|merge|tree|branch|tip|subject|revision|sha)\b"
        r"(?:\s+['\u2019]s\s+(?:son|child|parent|successor|predecessor|descendant|"
        r"ancestor))?"
        r"(?:[^.;\u2014\u2013]{0,40}?)"
        r"\b(?:is|was|are|were|means|denotes|refers\s+to|continues?\s+to\s+be|"
        r"(?:has|have|had)\s+to\s+be|(?:must|should|would|will|can|could|may|might)\s+be)\b"
        r"\s+(?:the\s+)?(?:tree\s+)?(?:immediately\s+)?$",
        re.I,
    )
    _PARENT_OF_SHA = re.compile(
        r"^\s+of\s+`?(?:785bb2b59|" + _TRUE_TREE + r")`?", re.I
    )
    #: A DIFFERENT, NAMED SUBJECT predicated of a sha — the only thing that makes a
    #: trailing tree a statement about ANOTHER object rather than about the phrase's own.
    #: A `#<n>` reference is a named subject in this repo's prose ("the #9601 merge"), and
    #: the phrase's own `#9548` does not count (the caller filters it). Read in both word
    #: orders, because `<sha> is the #9601 merge` and `the #9601 merge is <sha>` are the
    #: same proposition (#11191 review r11 F1/F4, P1 — MEASURED: the punctuation-keyed
    #: test admitted `; the tree is <false sha>` and refused `, and the #9601 merge is …`).
    _OTHER_SUBJECT_LEFT = re.compile(
        r"#(\d+)\b[^;:.!?\u2014\u2013]{0,80}?"
        r"\b(?:is|was|are|were|means|denotes|refers\s+to)\b",
        re.I,
    )
    _OTHER_SUBJECT_RIGHT = re.compile(
        r"\b(?:is|was|are|were|means|denotes|refers\s+to)\b"
        r"[^;:.!?\u2014\u2013]{0,80}?#(\d+)\b",
        re.I,
    )
    #: A correction that RETIRES the attribution — "§A8 no longer calls `ff6379eb2` the
    #: tree before #9548", "… does not call it …". Closed on purpose: the verb set is
    #: attributive, so "It is not disputed that `ff6379eb2` is the tree before #9548"
    #: (which ASSERTS the equivalence inside a denial of doubt) cannot use it.
    _RETIRED_ATTRIBUTION = re.compile(
        r"\b(?:(?:no\s+longer|never)\s+"
        r"(?:calls|called|describes|described|names|named|states|stated|says|said|"
        r"holds|held|treats|treated|reads|records|recorded)"
        r"|(?:does|do|did)\s+not\s+(?:call|describe|name|state|say|hold|treat|read|record))\b"
        r"[^.;\u2014\u2013]{0,30}?" + re.escape(_FALSE_TREE),
        re.I,
    )
    #: A denial of the INFERENCE the claim is used to draw — "That `ff6379eb2` is the
    #: tree before #9548 does not mean the census is 5". Closed verb set; returns with
    #: its own control, because the previous head lost it and went RED on the shape.
    #: ⛔ AND IT IS SUBJECT-BOUND (#11191 review, P1, re-confirmed live — MEASURED). A
    #: clause-wide `search` for the cue fires on the ASSERTION, not on the quotation:
    #:   "`ff6379eb2` is the tree before #9548, which does not mean the census is 5."
    #: `origin/main` refuses that sentence; the previous head ACCEPTED it, because a
    #: denial of the INFERENCE was read as a denial of the EQUIVALENCE. Every other
    #: correction arm in this predicate is subject-bound for exactly this reason
    #: (`_RETIRED_ATTRIBUTION`'s verb set, `_REFUTED_AFTER`'s demonstrative/claim-word
    #: subject); this one was a blank cheque. The binding is the `that`-clause the arm was
    #: written for: the claimed equivalence must be the SUBJECT of the denied inference.
    _INFERENCE_DENIAL = re.compile(
        r"\bthat\s+[^.;\u2014\u2013]{0,60}?"
        + _PHRASE_RE
        + r"[^.;\u2014\u2013]{0,40}?\b(?:does|do|did)\s+not\s+"
        r"(?:mean|imply|establish|show|prove)\b",
        re.I,
    )
    #: A CORRECTION VERB, admissible as a correction only when the clause also names the
    #: TRUE tree — "the row that said … is now corrected to `047c91f7a`", "replaced with
    #: `047c91f7a`", "is disproven by `047c91f7a`". Requiring the true tree is what
    #: keeps the verb from buying a pass for a stale figure on its own.
    _CORRECTION_VERB = re.compile(
        r"\b(?:corrected|replaced|superseded|refuted|disproven|disproved|retracted|"
        r"falsified)\b",
        re.I,
    )
    #: The INVERTED form: "the tree (immediately) before #9548 IS `ff6379eb2`", and its
    #: truthful twin (… IS `047c91f7a`, the most natural fix). `[^.;—–]` keeps the
    #: predicate inside the clause — the copula may admit insertions ("the tree before
    #: #9548 is, per the note, `ff6379eb2`" is the same claim) but not a new clause.
    _INVERTED_FALSE = re.compile(
        _PHRASE_RE
        + r"\s*(?:is|was|means|denotes)\b[^.;\u2014\u2013]{0,40}?"
        + re.escape(_FALSE_TREE),
        re.I,
    )
    _INVERTED_TRUE = re.compile(
        _PHRASE_RE
        + r"\s*(?:is|was|means|denotes)\b[^.;\u2014\u2013]{0,40}?"
        + re.escape(_TRUE_TREE),
        re.I,
    )
    #: The false sha ADJACENT to the phrase, in either order — the claim, with no copula
    #: required ("5 lines immediately before #9548 at `ff6379eb2`"). ⛔ `;` and `,` are
    #: NOT in the separator class (#11191 review, A3, P2 — measured): with `;` in it, the
    #: `;`-separated two-tree comparison "`047c91f7a` is the tree immediately before
    #: #9548; `ff6379eb2` is the #9601 merge" was REFUSED while the adjacent comment
    #: claimed it stayed green. The caller searches the phrase's CLAUSE, so a comparison
    #: in the next clause is out of this arm's reach by construction.
    _FALSE_SHA_ADJACENT = re.compile(
        _PHRASE_RE
        + r"(?:[\s`*_\u2014\u2013-]|\b(?:the|tree|at|of|is|was)\b){0,6}"
        + re.escape(_FALSE_TREE)
        + r"|"
        + re.escape(_FALSE_TREE)
        + r"(?:[\s`*_\u2014\u2013-]|\b(?:the|tree|at|of|is|was)\b){0,6}"
        + _PHRASE_RE,
        re.I,
    )

    def _normalise(sentence: str) -> str:
        """Strip markdown emphasis/code ticks and collapse whitespace.

        Emphasis is TYPOGRAPHY, not meaning, so it is removed before any claim is read.

        ⛔ It is load-bearing for the INVERTED arm specifically, and that is measured,
        not assumed: this doc bolds fragments as house style (the real §A8 row carries
        `**NOT** the tree immediately before #9548`), and with this call reduced to a
        whitespace collapse the control `"The tree immediately before #9548 is
        `ff6379eb2`, not `047c91f7a`"` stops being refused — the backticks sit between
        the copula and the sha. The ATTRIBUTIVE arm does not need it: it compares
        which sha precedes the phrase, and asterisks around a copula change nothing
        there (also measured — the emphasis controls stay green under that same
        mutation, so the two arms are independently covered).

        A markdown LINK TARGET is metadata, not prose (#11191 review
        r8, P2 — MEASURED): a dotted URL carries a `.` that ends every clause class in this
        predicate, and often a sha of its own — so `[`047c91f7a`](https://example.invalid/…)`
        was read as a clause BOUNDARY, each arm saw a different sentence from the author's,
        and the cleft arm went unreachable. Collapsing to the link's TEXT is the same rule
        the emphasis strip already follows.
        """
        return re.sub(
            r"\[([^\]]*)\]\([^)]*\)",
            r"\1",
            re.sub(
                r"\s+", " ", sentence.replace("*", "").replace("_", "").replace("`", "")
            ),
        )

    def _refuses_before_9548(sentence: str) -> bool:
        """True when the sentence NAMES "before #9548" and does not correct it.

        Kept as a predicate (not inlined) so every arm can be exercised in BOTH
        directions on fixed strings — the red direction (the bare equivalence, its
        paraphrase, a figure dated to the phrase with no tree named, a THIRD tree
        named as the pre-#9548 tree, every shape that names the true sha while keeping
        the false claim, and every correction that would have to cross a clause to
        apply) and the green one (a correction naming the true pre-#9548 tree in its
        positive, denial, comparison, refutation and true-tree-first phrasings). A
        predicate that is only ever run against the artefact it guards can go vacuous
        without anything reding; the control blocks are what make its greenness a
        measurement.

        ⛔ THREE STRUCTURAL DECISIONS, each measured by a review finding:

        1. DEFAULT-DENY (A1, P1). A phrase-bearing sentence passes only when the
           phrase's OWN CLAIM is corrected. Treating "no named referent" as "not this
           defect" was the fail-open that let "The census returns 3 lines immediately
           before #9548" — the accidental form of the r4 defect — pass at the previous
           head while `main` refused 15 of 15 of that family.
        2. CLAUSE-SCOPED CORRECTIONS (A2, P1). The correction arms used to return for
           the whole sentence, so any correct denial anywhere exempted the false
           equivalence — including the §A8 cell's own `**NOT** the tree immediately
           before #9548`, which disabled the pin on the exact sentence it guards. A
           denial is a claim about its own clause; `;`, `.`, `—`/`–` and `, and|but`
           end it. Only the refutation arms may look one clause ahead, and only through
           a demonstrative ("— that is wrong"), because that is the shape the
           refutation of a quoted claim actually takes.
        3. REFERENCE, NOT TOKENS OR DISTANCE (P2). The referent of the phrase is the
           nearest sha written BEFORE it, either tree's name being equally readable;
           the predicate position ("… IS <sha>") is read explicitly. A bare
           `"047c91f7a" in sentence` exemption passed sentences that only MENTIONED the
           true sha; a distance cutoff refused the sentence a fixer should write.
        """

        def _clause_containing(norm: str, pos: int) -> tuple[str, int]:
            """The clause around `pos`, and its end offset — the unit a denial lives in."""
            start = 0
            for _delim in _CLAUSE_DELIM.finditer(norm):
                if _delim.end() <= pos:
                    start = _delim.end()
                elif _delim.start() > pos:
                    return norm[start : _delim.start()], _delim.start()
            return norm[start:], len(norm)

        def _clause_before_phrase(norm: str, pos: int) -> str:
            """The part of the phrase's OWN clause that sits BEFORE it.

            The clause boundary is `_clause_containing`'s — one definition of "clause" for
            the whole predicate (#11191 r3).
            """
            start = 0
            for _delim in _CLAUSE_DELIM.finditer(norm):
                if _delim.end() <= pos:
                    start = _delim.end()
                elif _delim.start() >= pos:
                    break
            # ⛔ THE PHRASE ITSELF IS NOT "BEFORE" IT. Returning the clause up to its
            # END (a first cut of this helper did) puts `#9548` — four digits — into the
            # text the digit guard reads, so EVERY phrase-bearing clause looked like a
            # figure dated to the phrase and every truthful attribution went RED.
            return norm[start:pos]

        def _sentence_tail_hard(text: str) -> str:
            """`text` cut at its first SENTENCE-ENDING full stop, abbreviations protected.

            ⛔ ONE helper, not two (#11191 review r11 F5, P3): a `_sentence_tail` that also
            cut at `:` `;` and the dashes lived here until r11 and was **never called** —
            dead code presented as a live sibling by this docstring, so a later reader could
            "restore" the arm that relied on it and see nothing red. It is deleted rather
            than kept for symmetry: the arms that read past a clause use THIS one.

            ⛔ AND IT DELIBERATELY STILL READS ACROSS `;`/`:` — DO NOT "fix" that by
            cutting here (#11191 review r10 residual 2). The `;`-separated two-tree
            comparison this file requires to stay green ("…is the arming commit's parent;
            the #9601 merge is `ff6379eb2`") is refused by the CALLER's `_admits`, which
            admits a sha in a clause that PREDICATES it of a different, named subject
            while still refusing a co-referential one ("; the tree is `ff6379eb2`").
            Cutting the tail at `;` would accept the second.
            """
            sentinel = "\ue000"
            src = re.sub(
                r"\b(i\.e|e\.g|viz|cf|vs)\.",
                lambda _m: _m.group(1) + sentinel,
                text,
                flags=re.I,
            )
            return re.split(r"\.(?=\s|$)", src, maxsplit=1)[0].replace(sentinel, ".")

        def _occurrence_span(norm: str, phrase: re.Match[str], clause: str, clause_end: int) -> str:
            """The clause text that belongs to THIS phrase occurrence alone.

            ⛔ A DENIAL BELONGS TO THE OCCURRENCE IT CORRECTS (#11191 review r11, P1 —
            MEASURED). The per-occurrence loop above decides each phrase on its own, but
            the denial arms were still handed the WHOLE clause — and a comma is not a
            clause boundary (deliberately: that is what closes the A2 conjunction hole), so
            the false equivalence SPLICED onto the artefact's own "**NOT** the tree
            immediately before #9548" sat in the clause that carries the denial and was
            exempted by it. Measured on the REAL §A8 row before this arm existed:
              "…the arming commit's parent), `ff6379eb2` is the tree before #9548"  -> accepted
            i.e. the pin was disabled on the cell it exists to guard by the cell's own
            correction — the exact A2 class the contract's first rule declares ("every
            phrase occurrence is decided on its own"), reached through the comma splice
            the `and`-keyed fix does not cover. The span starts after the PREVIOUS
            occurrence in the same clause, so a denial may exempt only the predication it
            sits in front of, never a second one after a comma.
            """
            clause_start = clause_end - len(clause)
            previous_end = clause_start
            next_start = clause_end
            for _other in re.finditer(_PHRASE_RE, norm[clause_start:clause_end], re.I):
                _pos = clause_start + _other.start()
                if _pos < phrase.start():
                    previous_end = clause_start + _other.end()
                elif _pos > phrase.start() and next_start == clause_end:
                    next_start = _pos
            # ⛔ BOTH SIDES (#11191 review r11, P1 — MEASURED): a left-only bound still hands the

            # FIRST occurrence every denial written after it, so the mirror of the splice above
            # passed — "`ff6379eb2` is the tree before #9548, **NOT** the tree immediately before
            # #9548" was ACCEPTED, exempted by the SECOND occurrence's denial. The span ends at
            # the next occurrence's start for the same reason it begins after the previous one's
            # end: a correction belongs to the predication it sits beside.
            return norm[previous_end:next_start]

        def _is_corrected(norm: str, phrase: re.Match[str]) -> bool:
            """True when THIS phrase occurrence's claim is corrected, denied or refuted."""
            clause, clause_end = _clause_containing(norm, phrase.start())
            own = _occurrence_span(norm, phrase, clause, clause_end)

            def _asserts_false_tree(_text: str) -> bool:
                """True when `_FALSE_TREE` is ASSERTED in `_text`, not merely REFUTED in it.

                A correction names the false tree in order to reject it — "…reads 10
                lines, not the 5 stated at `ff6379eb2`" — and that sentence is the one
                this pin's own failure message recommends, so refusing it is the
                false-RED class this file exists to avoid (#11191 review r11, P1). A
                mention with no refutation in front of it is still the claim.
                """
                for _m in re.finditer(re.escape(_FALSE_TREE), _text, re.I):
                    _lead = _text[max(0, _m.start() - 44) : _m.start()].lower()
                    if re.search(
                        r"\b(?:not|never|rather\s+than|instead\s+of|unlike"
                        r"|as\s+opposed\s+to|was\s+wrong\s+about)\b"
                        r"[^.;\u2014\u2013]{0,34}$",
                        _lead,
                    ):
                        continue
                    return True
                return False

            def _copulated_to_phrase(_clause: str) -> bool:
                """True when a NON-true sha-shaped token in `_clause` is copulated to the phrase.

                Used to withhold the ancestry exemption: a TRUE kinship written beside a
                false copula is an alibi, not a correction (#11191 review r11, P2 —
                MEASURED: it took the pin GREEN on the real §A8 cell). The copula's object
                must be a TREE NOUN, so "`ff6379eb2` IS AN ANCESTOR OF the tree before
                #9548" — the truthful kinship this exemption exists for — is not mistaken
                for a copula it is not.
                """
                for _m in _SHA_LIKE_RE.finditer(_clause):
                    _tok = _m.group(0).lower()
                    if _TRUE_TREE.startswith(_tok) or _tok.startswith(_TRUE_TREE):
                        continue
                    if re.match(
                        r"`?\s*(?:,?\s*[^.;\u2014\u2013]{0,20}?)?\s*"
                        r"(?:is|was|means|denotes|refers?\s+to)\b"
                        r"\s+(?:the\s+|a\s+|an\s+)?(?:tree|commit|merge|branch|sha|tip)\b"
                        r"[^.;\u2014\u2013]{0,40}?\bbefore\s+#9548",
                        _clause[_m.end() :],
                        re.I,
                    ):
                        return True
                return False
            # 1. A REFUTATION of the predication — "… is false|wrong|…", or a
            #    demonstrative carrying it into the next clause ("— that is wrong").
            if _REFUTED_AFTER.match(norm, phrase.start()):
                return True
            if _REFUTED_NEXT_CLAUSE.match(norm[clause_end:]):
                return True
            # 2. A DENIAL, in the phrase's own clause. Each arm is a CLOSED set: an open
            #    "any `not` nearby" is how a denial of doubt came to excuse a sentence
            #    that asserts the equivalence.
            #    ⛔ AND A DOUBT FRAME IS NOT A DENIAL OF THE CLAIM (#11191 review r4, P2 —
            #    measured): "nothing suggests `ff6379eb2` is NOT the tree before #9548"
            #    asserts the equivalence, and the arm below would read that inner `is not`
            #    as the correction. Checked FIRST, and only where the frame opens the
            #    clause BEFORE the false sha.
            _false_at = clause.lower().find(_FALSE_TREE)
            if _false_at >= 0 and _DOUBT_FRAME.search(clause[: _false_at]):
                return False
            # ⛔ EVERY denial arm reads the OCCURRENCE span, not the clause: leaving
            # `_DENIED_BY_SHA` clause-wide re-opened the same hole one arm over (#11191
            # review r11, P1 — measured: the prepend splice was exempted by this arm while
            # `_DENIED_IN_PREDICATE` had already been narrowed).
            if _DENIED_BY_SHA.search(own):  # "`ff6379eb2` is not / isn't / cannot be"
                return True
            if _DENIED_IN_PREDICATE.search(own):  # "… NOT the tree before #9548"
                return True
            # 2b. THE CLEFT — `<sha>, not <sha>, is the tree before #9548` (#11191
            #     review, P1 — MEASURED). It is decided BEFORE the complement arm, because
            #     the complement arm (`_NOT_FALSE_SHA`) also fires on the appositive half
            #     of a cleft and would answer `True` for a THIRD tree fronted
            #     ("`c5cbcbb4f`, not `ff6379eb2`, is the tree before #9548" — the A1
            #     invented-tree defect). A fronted appositive negation does not negate the
            #     predication; the sha that FRONTS the sentence is its SUBJECT, so the
            #     fronted sha, not the negator, decides:
            #       `ff6379eb2`, not `047c91f7a`, is the tree before #9548  -> REFUSE
            #       `047c91f7a`, not `ff6379eb2`, is the tree before #9548  -> accept
            #     `_NEG`'s bare-`not` alternative is guarded against a sha-shaped object,
            #     which is what leaves the cleft to THIS arm instead of being swallowed by
            #     the generic denial one arm above. Both directions and the third-tree case
            #     are pinned by controls: one arm producing opposite verdicts from the same
            #     surface form is exactly the fail-open a single-direction probe cannot see.
            # ⛔ FROM THE LAST HARD DELIMITER, not from `_clause_containing`'s clause
            # (#11191 review r9, P2 — MEASURED): a parenthesised `and` inside the
            # complement ends the clause and hands this arm a fragment whose FRONTED sha is
            # outside it, so the cleft went unread and the referent rule accepted the claim.
            _hard = max(
                norm.rfind(";", 0, phrase.start()),
                norm.rfind(".", 0, phrase.start()),
                norm.rfind("\u2014", 0, phrase.start()),
                norm.rfind("\u2013", 0, phrase.start()),
            )
            # ⛔ …AND A PARENTHESISED SPAN IS BLANKED FIRST (#11191 review r10, P1 —
            # MEASURED). The conjunction bound above stops the gap at `, and` — which is
            # what keeps a truthful later clause from being read as part of this claim —
            # but an aside's `(and …)` is not a clause boundary, so the bound must not see
            # it. Blanking the aside is the same rule `_INCISE` applies to a dash aside.
            _gap_src = _PAREN.sub(lambda _m: " " * len(_m.group(0)), norm[_hard + 1 : phrase.end()])
            _cleft = _CLEFT_PREDICATION.search(_gap_src)
            if _cleft:
                _fronted = _cleft.group(1).lower()
                if not (_TRUE_TREE.startswith(_fronted) or _fronted.startswith(_TRUE_TREE)):
                    return False
                # ⛔ A TRUE-FRONTED CLEFT MAY NOT SHORT-CIRCUIT A FALSE SHA IN THE CLAUSE
                # (#11191 review r10, P1 — MEASURED): a sentence can carry a correction of
                # a DIFFERENT issue in its first clause and the §A8 defect in its second,
                # and returning True on the first match masked the second:
                #   "`047c91f7a` rather than `ff6379eb2` is the tree before #9601, and
                #    `ff6379eb2` is the tree before #9548."              -> ACCEPTED
                # Falling through lets step 3 (the adjacency arm) refuse it.
                if not _FALSE_SHA_ADJACENT.search(clause):
                    return True
            if _NOT_FALSE_SHA.search(own):  # "… is `047c91f7a`, not `ff6379eb2`"
                return True
            if _RETIRED_ATTRIBUTION.search(own):  # "no longer calls it"
                return True
            if _INFERENCE_DENIAL.search(own):  # "does not mean the census is 5"
                return True
            if _CORRECTION_VERB.search(own) and re.search(_TRUE_TREE, own, re.I):
                return True  # "… is now corrected to `047c91f7a`"
            # 3. THE CLAIM ITSELF — the false sha adjacent to the phrase, in either order,
            #    with no copula required ("5 lines immediately before #9548 at
            #    `ff6379eb2`" — the pre-fix row's own idiom). Checked BEFORE the referent
            #    rule, because a sentence can name the true tree as the referent of one
            #    clause and still assert the false equivalence in another. Clause-scoped,
            #    so the `;`-separated two-tree comparison is out of its reach (#11191
            #    review, A3, P2 — measured REFUSED while `;` sat in the separator class).
            if _FALSE_SHA_ADJACENT.search(clause):
                return False
            # THE INVERTED PREDICATE names the false tree as the phrase's predicate —
            # but only when the TRUE tree is not named before it in the same span
            # (#11191 review r4, P1: with the wide window, "the tree immediately
            # before #9548 is `047c91f7a`, not `ff6379eb2`" was refused — a false RED
            # on the natural correction).
            for _hit in _INVERTED_FALSE.finditer(clause):
                _span = _hit.group(0)
                if _TRUE_TREE not in _span[: _span.lower().index(_FALSE_TREE)]:
                    return False
            if _INVERTED_TRUE.search(clause):
                return True  # "the tree before #9548 IS `047c91f7a`"
            # 4. A REFERENT. The phrase refers to the nearest sha written before it — that
            #    is what apposition and attribution mean in prose, with no distance
            #    cutoff (a truthful attribution is not disqualified by being long).
            #    ⛔ AN INCISE IS NOT A REFERENT (#11191 review r4, P2 — measured): an aside
            #    names a different proposition, so the text inside one is BLANKED before
            #    the referent is read. Without this, "`ff6379eb2` is (as `047c91f7a`
            #    shows) the tree before #9548" took the aside's sha as the referent and
            #    the claim passed. In the sentence the aside replaces spaces only, so the
            #    offsets `phrase.start()` / `clause_end` still address `norm`.
            _unincised = _INCISE.sub(lambda _m: " " * len(_m.group(0)), norm)
            _own_before = _clause_before_phrase(norm, phrase.start())
            _own_start = phrase.start() - len(_own_before)
            # ⛔ THREE-TIER REFERENT, and the tiers are what make the aside rule safe
            # (#11191 review r5, P1/P2 — each tier fixes a MEASURED false RED or false
            # PASS the previous two-tier rule produced):
            #   1. the phrase's OWN CLAUSE, OUTSIDE any aside — the subject the clause
            #      actually predicates. This is the defect discriminator: "`ff6379eb2` is
            #      (as `047c91f7a` shows) the tree before #9548" keeps `ff6379eb2` here.
            #   2. the phrase's OWN CLAUSE, inside an aside — when the clause's only
            #      attribution is the aside, it IS the attribution: "the arming commit's
            #      parent, `047c91f7a`, is the tree immediately before #9548" (the §A8
            #      cell's own idiom) and "The census (taken at `047c91f7a`) returns 10
            #      lines immediately before #9548" both stay ACCEPTED.
            #   3. the whole SENTENCE, ignoring asides — a true tree named in an earlier
            #      clause is still a correction for the pinned shape "`047c91f7a` is the
            #      arming commit's parent and the tree before #9548 reads 10 lines".
            preceding = [
                m
                for m in _SHA_LIKE_RE.finditer(_unincised)
                if m.end() <= phrase.start() and m.start() >= _own_start
            ]
            if not preceding:
                preceding = [
                    m
                    for m in _SHA_LIKE_RE.finditer(norm)
                    if m.end() <= phrase.start() and m.start() >= _own_start
                ]
            if not preceding:
                # ⛔ TIER 3 — THE WHOLE SENTENCE, but asides are blanked only from the LAST
                # `;`/`.` forward (#11191 review r6, P1 — measured false RED: the flat
                # `_unincised` read lost an aside-borne attribution in an EARLIER clause:
                # "The census (measured at `047c91f7a`) is the one that matters; the tree
                # before #9548 reads 10 lines" was REFUSED while base and `origin/main`
                # accept it). A dash aside between the sha and the phrase is still blanked
                # — there is no `;`/`.` between them — so the em/en-dash and hyphen
                # variants stay refused. Offsets are preserved, so `phrase.start()` and
                # `clause_end` still address `norm`.
                _hard = max(norm.rfind(";", 0, phrase.start()), norm.rfind(".", 0, phrase.start()))
                _tail_from = max(0, _hard)
                _tier3 = norm[:_tail_from] + _unincised[_tail_from:]
                preceding = [
                    m for m in _SHA_LIKE_RE.finditer(_tier3) if m.end() <= phrase.start()
                ]
            # ⛔ A PREDICATION IS CHECKED BEFORE THE REFERENT (#11191 review r7, P1 —
            # MEASURED): inside the "named referent" branch below, a TRUE tree named in an
            # EARLIER clause returned first and exempted it — the alibi class this change
            # closes for figures, reproduced in prose:
            #   "`047c91f7a` is the arming commit's parent; the tree immediately before
            #    #9548 is the arming commit itself."                          -> ACCEPTED
            # Placing it here also keeps the row's own truthful idiom green: the tree
            # before #9548 IS "the arming commit's parent", and the exemption is keyed on
            # the PARENT relation, so "… itself" / "…'s son" / "… the merge tip" are
            # refused while "…'s parent" and "the parent commit of `785bb2b59`" pass.
            # ⛔ …AND THE SAME CLAIM WRITTEN THE OTHER WAY ROUND (#11191 review r11, P1 —
            # MEASURED): the object arm reads only AFTER the phrase, so
            #   "The arming commit is the tree immediately before #9548."
            #   "The arming commit itself is the tree immediately before #9548."
            #   "The arming commit's son is the tree before #9548."
            # named no tree, dated no figure, and fell through to step 5's digit test —
            # ACCEPTED. The subject is read from the phrase's OWN CLAUSE with asides
            # blanked (an aside names a different proposition), and the exemptions are the
            # object arm's: the PARENT relation is the true tree, and a sha subject is the
            # referent rule's business.
            _subject_span = _INCISE.sub(
                lambda _m: " " * len(_m.group(0)),
                _clause_before_phrase(norm, phrase.start()),
            )
            if (
                _PREDICATED_SUBJECT.search(_subject_span)
                and "parent" not in _subject_span.lower()
                and _TRUE_TREE not in _subject_span.lower()
                and not _SHA_LIKE_RE.search(_subject_span)
            ):
                return False
            _predicated = _PREDICATED_OBJECT.match(norm[phrase.end() : clause_end])
            if _predicated:
                # ⛔ THE RELATION MUST BE THE PARENT ONE, AND NOTHING ELSE MAY BE NAMED BESIDE
                # IT (#11191 review r8, P1/P2 — MEASURED). The first cut of this exemption
                # matched `'s parent` at its START and returned, so the sha that FOLLOWS the
                # relation was never read — and that is the A1 claim itself, riding the
                # exemption:
                #   "The tree immediately before #9548 is the arming commit's parent, i.e.
                #    `ff6379eb2`."                                        -> ACCEPTED (must red)
                #   "…is the arming commit's parent, `785bb2b59`."         -> ACCEPTED (must red)
                # (`_INVERTED_FALSE` cannot catch either: its span class stops at the `.`
                # inside `i.e.`, so no other arm reaches the trailing sha.) So the exemption
                # is admitted only for the PARENT relation, and any sha named AFTER it must
                # be the true tree.
                _pred_span = _predicated.group(0)
                _after = norm[phrase.end() + _predicated.end() :]
                # ⛔ THE TAIL IS THE REST OF THE SENTENCE, not the rest of the CLAUSE
                # (#11191 review r9, P1 — MEASURED). A tail that also cut at `:` `;` and
                # the dashes stopped reading at the first of them, so a wrong tree named
                # after a DASH — the house style of
                # this very cell — was never read: "…is the arming commit's parent —
                # `ff6379eb2`, as the note says." was ACCEPTED while the comma spelling
                # reddened. Only a sentence-ENDING full stop cuts this tail.
                _after_sentence = _sentence_tail_hard(_after)
                # (a) the object's sha is named OUTRIGHT — "…the arming commit, i.e.
                #     `047c91f7a`" is the correction, and this file's own controls admit
                #     it, so the exemption must not depend on the word "parent" alone.
                _named_true = re.match(
                    r"^\s*,?\s*(?:i\.e\.|that\s+is)?\s*`?" + _TRUE_TREE, _after_sentence, re.I
                )
                # (b) the PARENT relation, or `of <sha>` behind a "parent" predicate.
                _relation = _PARENT_RELATION.match(_after_sentence)
                if not _relation and "parent" in _pred_span.lower():
                    _relation = _PARENT_OF_SHA.match(_after_sentence)
                _resolved = _named_true or _relation
                if not _resolved:
                    return False
                # ⛔ AN APPOSITIVE NAMED RIGHT AFTER THE RELATION IS THE OBJECT'S *NAME*
                # (#11191 review r10 residual 1, P1 — MEASURED). The r10 cut read the
                # co-reference with `_INCISE` blanked, and `_INCISE` blanks a
                # COMMA-delimited incise — which is exactly how apposition is written — so
                # the appositive became invisible and a `since`-rationale beside it bought
                # the exemption:
                #   "…is the arming commit's parent, `785bb2b59`, since `785bb2b59` is the
                #    arming commit."                                     -> ACCEPTED (must red)
                # A PARENTHESIS or a DASH aside names a different proposition; a COMMA
                # incise immediately behind the relation is the relation's object, so the
                # token there must be the true tree. This is checked on the UNBLANKED
                # text, before any aside is removed.
                _apposed = re.match(
                    r"^\s*,?\s*(?:(?:i\.e\.|that\s+is|namely|viz\.)\s*)?`?([0-9a-f]{7,40})\b",
                    _after_sentence[_resolved.end() :],
                    re.I,
                )
                if _apposed:
                    _apposed_tok = _apposed.group(1).lower()
                    if not (
                        _TRUE_TREE.startswith(_apposed_tok)
                        or _apposed_tok.startswith(_TRUE_TREE)
                    ):
                        return False
                # ⛔ A RELATION OF A RELATION IS A DIFFERENT TREE (#11191 review r9, P1 —
                # MEASURED): "…the arming commit's parent's parent" passed, because the arm
                # stopped reading at `'s parent`. The grandparent is not the pre-#9548 tree.
                if re.match(r"^['\u2019]s\s+", _after_sentence[_resolved.end() :], re.I):
                    return False
                # ⛔ …AND THE CO-REFERENCE IS READ WITH ASIDES BLANKED, like every other arm
                # (#11191 review r9, P2 — MEASURED false RED): "…the parent commit of
                # `785bb2b59` (the arming commit is `785bb2b59`)" was REFUSED because the
                # aside's sha was read as a rival tree. An aside names a different
                # proposition. The arming commit's OWN sha is admissible beside the true
                # tree when the sentence says so explicitly: naming the parent AS the
                # arming commit is the defect, identifying the arming commit SEPARATELY is
                # the truth.
                _rest = _INCISE.sub(lambda _m: " " * len(_m.group(0)), _after_sentence[_resolved.end() :])
                _arming_id = re.compile(
                    r"\b`?785bb2b59`?\s*(?:is|was)\s+the\s+arming\s+commit\b", re.I
                )
                def _admits(_named: re.Match[str]) -> bool:
                    """True for the true tree, or for a tree the sentence attributes to a
                    DIFFERENT SUBJECT, or for `785bb2b59` where the sentence IDENTIFIES it
                    as the arming commit in a SEPARATE assertion.

                    ⛔ IDENTIFYING IT SEPARATELY IS THE TRUTH; NAMING IT AS THE PARENT IS THE
                    DEFECT (#11191 review r10, P1 — MEASURED). The first cut admitted any
                    `785bb2b59` in the remainder as soon as `_arming_id` matched ANYWHERE, so
                    the appositive the r8 control refuses rode the identification beside it:
                      "…is the arming commit's parent, `785bb2b59`, since `785bb2b59` is the
                       arming commit."                                -> ACCEPTED (must red)
                    while the truthful separate assertion must still pass. The separator is
                    the CONNECTIVE: an appositive is introduced by punctuation alone, a new
                    assertion by `and` / `since` / `because` / a `;`.
                    ⛔ …AND A CLAUSE THAT PREDICATES THE SHA OF A **DIFFERENT, NAMED**
                    SUBJECT IS A DIFFERENT TREE (#11191 review r10 residual 2, P2 — FALSE
                    RED, MEASURED; corrected by r11 F1/F4, P1). Reading the whole sentence
                    tail made the `;`-separated two-tree comparison this file requires to
                    stay green REFUSED:
                      "…is the arming commit's parent; the #9601 merge is `ff6379eb2`."
                    ⛔ THE FIRST CUT OF THIS ADMISSION TESTED "is there ANY non-stopword
                    word before the sha in this clause", which is a PUNCTUATION-keyed test
                    on a proposition: it admitted every CO-REFERENTIAL subject, so the
                    equivalence came back one semicolon from the form this file refuses —
                    measured ACCEPTED at r11, and REFUSED by `origin/main` and by r10:
                      "…parent; the tree is `ff6379eb2`."
                      "…parent; its parent is `ff6379eb2`."
                      "…parent; the merge is `ff6379eb2`."
                    (and `, and` / `; `ff6379eb2` is the #9601 merge` were REFUSED while
                    saying the same true thing as the admitted `;` spelling — one arm, two
                    verdicts keyed on punctuation). So the test is the PROPOSITION: the
                    clause must attribute the sha to a DIFFERENT, NAMED subject — a `#<n>`
                    reference other than the phrase's own `#9548`, joined to the sha by a
                    copula in either order. A co-referential noun has no such reference,
                    whichever punctuation it follows.
                    """
                    _tok = _named.group(0).lower()
                    if _TRUE_TREE.startswith(_tok) or _tok.startswith(_TRUE_TREE):
                        return True
                    _lead = _rest[: _named.start()]
                    # The clause the sha sits in, found with the SAME boundary set the
                    # arms above use (`;` `:` and the coordinating conjunction), then
                    # split at the sha so either word order can be read.
                    _bounds = [
                        (_m.start(), _m.end()) for _m in _CLAUSE_DELIM.finditer(_rest)
                    ]
                    _c_start, _c_end = 0, len(_rest)
                    for _bs, _be in _bounds:
                        if _be <= _named.start():
                            _c_start = _be
                        elif _bs >= _named.end():
                            _c_end = _bs
                            break
                    _left = _rest[_c_start : _named.start()]
                    _right = _rest[_named.start() : _c_end]
                    _other = None
                    _hit = _OTHER_SUBJECT_LEFT.search(_left)
                    if _hit:
                        _other = _hit.group(1)
                    else:
                        _hit = _OTHER_SUBJECT_RIGHT.search(_right)
                        if _hit:
                            _other = _hit.group(1)
                    if _other and _other != "9548":
                        return True
                    if not "785bb2b59".startswith(_tok):
                        return False
                    return bool(
                        re.search(r";|,?\s*\b(?:and|since|because|as|which|given)\b", _lead, re.I)
                    ) and _arming_id.search(_rest) is not None

                return all(_admits(_named) for _named in _SHA_LIKE_RE.finditer(_rest))
            if preceding:
                # ⛔ PREFIX, not equality (#11191 review round 2, P1 — measured): a
                # FULL 40-hex referent (what `git rev-parse` prints) was REFUSED,
                # because the rule compared a 40-char token to the 9-char `_TRUE_TREE`.
                # Comparing both directions keeps the short and long spellings equal.
                _ref = preceding[-1].group(0).lower()
                # ⛔ …BUT A FIGURE DATED TO THE PHRASE IS NOT EXCUSED BY A TREE NAMED IN AN
                # EARLIER CLAUSE (#11191 review r4, P1 — measured ACCEPTED at
                # `46c01c324e8`): the referent rule answered for the SENTENCE, so
                # `047c91f7a` named before a `;` exempted "the census returns 3 lines
                # immediately before #9548" in the NEXT clause — a figure dated to the
                # phrase in a clause that corrects nothing, i.e. the A1 defect with an
                # alibi beside it. Fail-open is the worse direction, so the ambiguous case
                # reds. The sentence-wide referent is NOT withdrawn: a figure AFTER the
                # phrase is the measurement OF it, not an alibi beside it, so the pinned
                # green shape ("`047c91f7a` is the arming commit's parent and the tree
                # before #9548 reads 10 lines") is untouched.
                _own_before = _clause_before_phrase(norm, phrase.start())
                # ⛔ THE EARLIER-CLAUSE FIGURE GUARD IS WITHDRAWN AGAIN, and this time the
                # reason is a MEASUREMENT rather than a taste in spellings (#11191 review
                # r6, P1). It cannot distinguish the alibi from the honest reading: the
                # sentence it was added to refuse — "`{T}` is the arming commit; the census
                # returns 3 lines immediately before #9548" — and the truth —
                # "`{T}` is the arming commit's parent; it returns 10 lines immediately
                # before #9548" — differ in NOTHING but whether the figure is the census's
                # answer, and refusing both (which is what it did: base and `origin/main`
                # accept the second) is the false-RED class this file names as the way a
                # pin gets deleted. Settling this shape needs the MEASURED count, not
                # another guard: see the issue referenced in the residual note below.
                # The class therefore stays OPEN and DOCUMENTED (a characterisation test
                # pins the current behaviour so a future fix cannot move it silently).
                return (
                    _TRUE_TREE.startswith(_ref)
                    or _ref.startswith(_TRUE_TREE)
                    # ⛔ AN ANCESTRY RELATION NAMING THE TRUE TREE IS A DIFFERENT, TRUE
                    # PROPOSITION (#11191 review r11, P1 — MEASURED false RED and a
                    # REGRESSION against `origin/main`). Without this arm the referent
                    # rule refuses two truthful sentences `main` accepts:
                    #   "`ff6379eb2` is an ancestor of the tree before #9548, `047c91f7a`."
                    #   "`ff6379eb2`, an ancestor of the tree before #9548 (`047c91f7a`),
                    #    reads 5 lines."
                    # Both halves are required — the kinship relation AND the true tree
                    # in THIS clause — so the relation cannot be used as an alibi for a
                    # bare equivalence: "`ff6379eb2` is an ancestor of the tree before
                    # #9548" with no referent stated is still refused, and so is the same
                    # clause that re-asserts the false sha as the tree.
                    or (
                        _ANCESTRY_OF_TREE.search(clause) is not None
                        and _TRUE_TREE in clause.lower()
                        and _FALSE_TREE not in clause.lower().replace(_ref, "", 1)
                        # ⛔ …AND IT MAY NOT SHELTER A COPULA (#11191 review r11, P2 —
                        # MEASURED, and this arm is what introduced it). The kinship
                        # exemption is an ALIBI when the clause asserts the equivalence
                        # first and then adds a true kinship beside it:
                        #   "`ff6379eb2` is the tree immediately before #9548, an
                        #    ancestor of the tree `047c91f7a`."                -> ACCEPTED
                        # — and that sentence injected into the REAL §A8 cell left the pin
                        # GREEN, i.e. the exemption disabled the pin on the one artefact it
                        # guards. A third tree rides the same alibi. So the exemption is
                        # withheld when ANY non-true tree-shaped token in this clause is
                        # copulated to the phrase; the truthful ancestries all name their
                        # tree OUTSIDE such a copula, so none of them is touched.
                        and not _copulated_to_phrase(clause)
                    )
                )
            # ⛔ …AND THE REFERENT MAY FOLLOW THE PHRASE, but ONLY as an APPOSITIVE
            # immediately after it (#11191 review r3, P1 — measured). An unanchored
            # "any true-sha later in the clause" window re-admitted the equivalence it
            # sits under: "the tree immediately before #9548 (`047c91f7a`) is actually
            # `ff6379eb2`" passed, because `(` is not in `_FALSE_SHA_ADJACENT`'s
            # separator class. So the sha must be the token right after the phrase
            # (optionally parenthesised), and no false sha may follow it in the clause.
            # ⛔ AND IT NEED NOT BE THE VERY NEXT TOKEN (#11191 review, F3, P2 — measured
            # REFUSED at `46c01c324e`, while `origin/main` accepts both):
            #   "The census returns 10 lines before #9548, measured at `047c91f7a`."
            #   "The tree immediately before #9548 is the arming commit, i.e. `047c91f7a`."
            # Anchoring to `match` red the two most natural truthful corrections — the
            # false-RED class this file names, and a regression against `main`. The
            # referent may sit later, but the FIRST sha-like token after the phrase must
            # be it, with no figure between the phrase and it (a figure there is a reading
            # dated to the phrase with no referent — the A1 defect), and no false sha
            # anywhere after it. SIM103 shape: one expression, so the arm reads as a rule.
            _tail = norm[phrase.end() :]
            _appositive = _SHA_LIKE_RE.search(_tail)
            if _appositive and (
                _TRUE_TREE.startswith(_appositive.group(0).lower())
                or _appositive.group(0).lower().startswith(_TRUE_TREE)
            ):
                _between = re.sub(r"#\d+", " ", _tail[: _appositive.start()])
                # ⛔ …and a DENIED figure is not a reading here either (r10, P2): the
                # denial spelling is asked through the same helper as the no-referent
                # arm so the two cannot disagree about what a figure IS.
                return not (
                    _states_a_figure(_between) or _asserts_false_tree(_tail)
                )
            # 4b. A PROSE SUBJECT PREDICATED OF THE PHRASE — the pre-posed mirror of
            #     `_PREDICATED_OBJECT` (#11191 review, P1 — MEASURED). Keyed on the
            #     subject of the phrase's OWN clause (the A2 doctrine every other arm
            #     obeys), with the PARENT relation as the only exemption.
            _hard_pre = max(
                norm.rfind(";", 0, phrase.start()),
                norm.rfind(".", 0, phrase.start()),
                norm.rfind("\u2014", 0, phrase.start()),
                norm.rfind("\u2013", 0, phrase.start()),
            )
            _preposed = _PREPOSED_PREDICATED.search(norm[_hard_pre + 1 : phrase.start()])
            if _preposed and "parent" not in (_preposed.group("rel") or "").lower():
                return False
            # 5. NO TREE IS NAMED. A figure dated to the phrase with no referent is the
            #    r4 defect verbatim (#11191 review, A1); a sentence that attributes
            #    NOTHING to the phrase — no figure and no tree anywhere in it — is not
            #    this defect, and reding it would be the false-RED class this file names.
            attribution = re.sub(_PHRASE_RE, " ", norm, flags=re.I)
            # ⛔ A `#…` PR/issue reference is a SUBJECT, not noise (#11191 review
            # round 2, P1 — measured): stripping `#\d+` made "The #9601 merge is the
            # tree immediately before #9548" read as attributing NOTHING and pass.
            # ⛔ …and a figure may be SPELLED (#11191 review round 2, P1 — measured):
            # `re.search(r"\d")` alone let "The census returns five lines immediately
            # before #9548" pass, which is the A1 defect with the numeral written out.
            # The set is `_WORD_FIGURE`, shared with the earlier-clause guard above so the
            # two arms cannot disagree about what a figure IS (#11191 review r4, P2);
            # `_WORD_FIGURE_IN_POSITION` decides where one COUNTS, so an ordinary pointer
            # ("one more time") is not a reading (#11191 review, F4 — measured).
            # ⛔ …AND A DENIED FIGURE DATES NOTHING (r10, P2 — MEASURED false RED on the
            # doc's own prose: "it was not one line either" was REFUSED while base and
            # `origin/main` accept it). `_states_a_figure` is the one place that decides,
            # so the denial set cannot drift between the two arms.
            return not _states_a_figure(attribution)

        norm = _normalise(sentence)
        # ⛔ EVERY phrase occurrence is decided on its own. A sentence can carry the
        # corrected claim AND the defect (that is exactly the §A8 injection case), so an
        # early `return` for the sentence is the A2 hole in another spelling.
        return any(
            not _is_corrected(norm, _phrase)
            for _phrase in re.finditer(_PHRASE_RE, norm, re.I)
        )
    # ... and a cell that still NAMES the phrase must CORRECT it — deny the equivalence,
    # or name the tree the phrase actually refers to. Deleting the clause is equally
    # correct, so all three shapes pass — what may not pass is the bare equivalence,
    # which is what the cell carried before this pin existed, and what a figure dated to
    # the phrase with no tree named re-introduces. The correction must sit in the
    # phrase's own CLAUSE (a `not` anywhere in a 900-word cell sentence is not the
    # correction — measured: the pre-fix sentence contains "not empty and not one" AFTER
    # the false clause and satisfied a whole-sentence test, so this arm is windowed like
    # the manifest denial above; and #11191 review A2 measured that the cell's own denial
    # exempted an equivalence injected beside it while the window was the whole sentence).
    #
    # ⛔ AND NAMING THE TRUE TREE AS THE REFERENT IS THE CORRECTION, not a violation of it
    # (#11064 review r17, P2). Requiring a denial in EVERY sentence that named the phrase
    # refused the most natural fix — "the tree immediately before #9548 is
    # `047c91f7a`" — a false RED produced by a guard whose purpose is to stop false
    # GREENs, the same class this file warns about ~130 lines above. ⚠️ But MENTIONING the
    # true sha is not enough: the exemption is the REFERENT (or the predicate), which is
    # why the substring form and the distance form were both wrong and the shapes are
    # EXECUTED below.
    offenders = [
        sentence
        for sentence in re.split(r"(?<=\.)\s+", row)
        if _refuses_before_9548(sentence)
    ]
    assert not offenders, (
        "§A8's evidence cell dates a figure to \"before #9548\" without either "
        "denying it applies to `ff6379eb2` or naming `047c91f7a` (the arming "
        "commit `785bb2b59`'s parent, where the same path set returns 10 lines, "
        "not the 5 stated at `ff6379eb2`):\n  " + "\n  ".join(offenders)
    )
    # ⛔ …AND THE FIGURE THE CELL STATES AT THE TRUE TREE IS MEASURED THERE, not
    # merely printed (#11187 review, F1 "stronger"). The §G note above is EXECUTED —
    # `pre_count` is read out of `_census_count_at(_REPO_ROOT, pre_sha, paths)` — but
    # §A8's evidence cell publishes the SAME claim a second time, and its figure was a
    # bare numeral checked at most against the OTHER tree's count. So the cell could
    # name `047c91f7a` and state a number no tree returns: the stale-flag class this
    # cell exists to close, one layer down from where it was closed. The census is run
    # at the cell's OWN tree over the cell's OWN path set (`paths`, read out of the
    # doc's baseline command above rather than retyped).
    #
    # The check collects EVERY figure the cell dates to that tree and requires the SET to
    # agree with the measurement — it does not "find the measured value" (the previous
    # arm did, and one right occurrence bought a pass for a second, different one: the
    # cell stated 7 and 10 at the same tree, #11191 review A4, P2 — measured). It also
    # accepts a cell that states NO figure there (#11191 review A5, P3): the failure
    # message has always offered "or state no figure at all" as a remedy, and the arm
    # used to refuse exactly that, which is how a fixer takes the offered remedy and gets
    # the same red. The claim is "if a figure is dated to this tree, it is the reading
    # that tree returns" — a figure-less cell states no claim to be wrong.
    #
    # A scan for "any number 12 characters from the sha" would happily return `#9548`
    # itself (measured — the first cut of this arm did exactly that), so the window
    # excludes a `#`-prefixed integer — and, with `(?<![\w#])`, a digit glued to a word:
    # measured, with only `[\d#]` excluded, the `2` at the end of `ff6379eb2` was read as
    # a stated figure once the text between it and the true sha was edited.
    if not _census_deepen_to(_REPO_ROOT, _TRUE_TREE):
        pytest.skip(
            f"`{_TRUE_TREE}` is not an object in this checkout (shallow: "
            f"{_census_is_shallow(_REPO_ROOT)}) and a bounded fetch to depth "
            f"{_CENSUS_SHALLOW_DEPTHS[-1]} did not reach it, so §A8's figure cannot "
            "be MEASURED here. The textual pins above still ran; this SKIP is not a "
            "pass"
        )
    _row_measured = _census_count_at(_REPO_ROOT, _TRUE_TREE, paths)
    #: A figure stated AT `_TRUE_TREE` — either order, within 12 non-word characters.
    #: ⛔ A DIGIT RUN IS NOT A STATED FIGURE (#11191 review round 2, P1 — measured): the
    #: doc's own house-style DATE beside the tree was read as a census reading
    #: (`047c91f7a` (2026-10-23) -> figures=['2026']), and the leading digits of a SECOND
    #: sha were read as one (`047c91f7a` and `047c91f7a` -> figures=['047']). So a figure
    #: may not continue into a date tail (`-10-23`) nor into a hex tail (`c91f7a`).
    _FIGURE_NEAR_TRUE_TREE = re.compile(
        rf"(?<![\w#])(\d{{1,5}})(?!\d)(?!-\d{{2}}-\d{{2}})(?![0-9a-f]{{2,}}\b)"
        rf"\D{{0,12}}`?{_TRUE_TREE}`?"
        rf"|`?{_TRUE_TREE}`?\D{{0,12}}(?<![\w#])(\d{{1,5}})(?!\d)"
        rf"(?!-\d{{2}}-\d{{2}})(?![0-9a-f]{{2,}}\b)",
        re.I,
    )

    def _figures_at_true_tree(text: str) -> set[int]:
        """EVERY figure `text` states at `_TRUE_TREE` — a SET, not one occurrence."""
        return {
            int(_hit.group(1) or _hit.group(2))
            for _hit in _FIGURE_NEAR_TRUE_TREE.finditer(text)
        }

    # ⛔ CONTROLS FOR THE FIGURE REGEX ITSELF (#11191 review r3, P1 — MEASURED). The §A8
    # row yields `figures=[10]` under the old regex AND the new one, so the artefact
    # exercised neither exclusion: reverting the fix left the suite green. These two
    # shapes are the measured defects, and they are pinned here.
    assert _figures_at_true_tree(f"`{_TRUE_TREE}` (2026-10-23) is the arming commit") == set(), (
        "the figure regex reads the doc's house-style DATE beside the tree as a census "
        "reading — reverting the date-tail exclusion leaves the suite green "
        "(#11191 review r3, P1)"
    )
    assert _figures_at_true_tree(f"`{_TRUE_TREE}` and `{_TRUE_TREE}`") == set(), (
        "the figure regex reads the leading digits of a SECOND sha as a stated figure "
        "— reverting the hex-tail exclusion leaves the suite green "
        "(#11191 review r3, P1)"
    )

    def _figure_gate(text: str) -> bool:
        """THE GATE — a cell may state the measurement, or no figure at all, and nothing else.

        Factored out of the assertion below so it can be driven by its own inputs
        (#11191 review, F5, P2 — measured: with the predicate INLINED, `<=` → `==`,
        `_row_measured in _row_figures` and deleting the gate statement outright each
        left the whole suite GREEN, because the controls exercised only the helper). The
        behaviour the gate controls is its SET relation, so the controls below call it
        with a strict subset, the full set and a superset.
        """
        return _figures_at_true_tree(text) <= {_row_measured}

    _row_figures = _figures_at_true_tree(row)
    assert _figure_gate(row), (
        f"§A8's evidence cell states a figure at `{_TRUE_TREE}` that its own census "
        f"does not return there — running that census with the cell's own path set "
        f"returns {_row_measured} line(s), and the cell states "
        f"{sorted(_row_figures)}: a figure that no tree returns is exactly what this "
        "cell was corrected for, and the §G note executes the same reading, so the "
        "document would otherwise answer one question twice with two different "
        f"answers. Either state {_row_measured} — and nowhere else in the cell — or "
        "state no figure at all: the derivation pointer is the durable form, and "
        "re-correcting a numeral is what produced the decay chain (#11187)"
    )
    # ⛔ …and that arm is EXECUTED in both directions on the REAL row, because a set test
    # that can only pass is not a gate. A4: change ONE of the cell's two figures and the
    # SET must show both — a single-occurrence arm stays green here, which is the
    # measured defect.
    _one_figure_changed = row.replace(
        f"not one: {_row_measured}**", f"not one: {_row_measured + 1}**"
    )
    if _one_figure_changed == row:
        # ⛔ THE CELL TAKING THIS ARM'S OWN OFFERED REMEDY IS NOT A FAILURE (#11191
        # review round 2, FINDING 1 — measured: deleting the numeral, which the failure
        # message offers and #11187 chose, made this assert red). The control's job is to
        # prove the SET arm reads every occurrence, so build its input when the artefact
        # no longer carries the shape — never red on the artefact's legitimate content.
        _one_figure_changed = (
            f"`{_TRUE_TREE}` reads {_row_measured}, and `{_TRUE_TREE}` also reads "
            f"{_row_measured + 1}"
        )
    assert _figures_at_true_tree(_one_figure_changed) == {
        _row_measured,
        _row_measured + 1,
    }, (
        "the arm reads ONE occurrence of the figure, so a cell that states the right "
        "number once and a wrong number once passes — the cell would answer its own "
        "question twice with two different numbers (#11191 review A4, P2)"
    )
    # A5: and a cell that states NO figure must pass — that is the alternative the old
    # failure message explicitly offered ("or state no figure at all") and then REFUSED,
    # which is the "remedy does not fit" class this file names (#11191 review A5, P3).
    _no_figure = _FIGURE_NEAR_TRUE_TREE.sub(f"`{_TRUE_TREE}`", row)
    if _no_figure == row:
        # Same rule as A4 above: the cell already states no figure here, which is the
        # remedy this arm exists to ACCEPT — synthesise the input instead of reding
        # (#11191 review round 2, FINDING 1).
        _no_figure = f"`{_TRUE_TREE}` reads the derivation, the numeral lives above"
        assert _figures_at_true_tree(_no_figure) == set(), (
            "the A5 control's own synthetic input is not figure-free, so it proves "
            "nothing (#11191 review round 2)"
        )
    assert _figures_at_true_tree(_no_figure) == set(), (
        "the arm refuses a cell that states NO figure at the true tree, which is the "
        "remedy its own failure message offers — reding the fix is the false-RED class "
        "this pin exists to remove, and #11187's chosen option is exactly to delete the "
        "numerals (#11191 review A5, P3)"
    )
    # ⛔ …and the GATE ITSELF is driven with its own inputs (#11191 review, F5, P2 —
    # measured: three rewrites of the gate statement left the whole suite GREEN while the
    # A4/A5 blocks above only exercised the HELPER). These two rows red when the set
    # relation narrows to equality or to membership.
    assert _figure_gate(_no_figure), (
        "the GATE refuses a cell that states NO figure at the true tree — the remedy its "
        "own failure message offers, and #11187's chosen option. A `==` gate turns the "
        "offered fix red, which is how a fixer takes the remedy and gets the same red "
        "(#11191 review, F5)"
    )
    assert not _figure_gate(_one_figure_changed), (
        "the GATE accepts a cell that states a second, WRONG figure at the true tree — "
        "measured on the gate itself, not on its helper (#11191 review, F5)"
    )
    # Controls, executed — the arm must still RED on the shape it was written for …
    assert _refuses_before_9548(
        "`grep -rn TORTOISE_RECORD_DSN …` → 5 lines at `ff6379eb2`, which is "
        'what "immediately before #9548" means.'
    ), (
        "the denial arm no longer reds on the bare equivalence it exists to catch — "
        "the exemption above has swallowed the finding (#11064 review r17, P2)"
    )
    # … and on the PARAPHRASE that drops `immediately` — the fail-open the r16 key
    # left open (#11064 review r17, P3).
    assert _refuses_before_9548(
        "ff6379eb2 — the tree before #9548 — reads 5 lines."
    ), (
        "the arm is keyed on `immediately before` again: the same false claim with "
        "that one word paraphrased away passes, which is a fail-open (#11064 review "
        "r17, P3)"
    )
    # … and must NOT red on a DENIAL-shaped correction that also names the true tree
    # — the false RED the r16 blanket substring ban produced (#11064 review r17, P3).
    _denial_shaped = (
        'ff6379eb2 is not what "immediately before #9548" means; that tree is `047c91f7a`.',
    )
    _refused_denials = [s for s in _denial_shaped if _refuses_before_9548(s)]
    assert not _refused_denials, (
        "the arm refuses a DENIAL-shaped correction — `is not what ... means` is a "
        "repair, and banning that phrasing reds the fix (#11064 review r17, P3):\n  "
        + "\n  ".join(_refused_denials)
    )
    # ⛔ … and the exemption may not be a bare SUBSTRING test (#11191 round 2, P2).
    # Naming the true sha while still asserting the false equivalence is the defect,
    # not the correction, and both shapes below were measured PASS under
    # `if "047c91f7a" in sentence: return False`.
    _named_but_still_false = (
        "`047c91f7a` is the arming commit's parent, and `ff6379eb2` is the tree before #9548.",
        "The arming commit's parent is `047c91f7a`; `ff6379eb2` is immediately before #9548.",
    )
    _still_refused_offenders = [
        s for s in _named_but_still_false if not _refuses_before_9548(s)
    ]
    assert not _still_refused_offenders, (
        "the exemption is the sha's PRESENCE, not its proximity to the phrase: a "
        "sentence that names `047c91f7a` somewhere and then calls `ff6379eb2` the tree "
        "before #9548 passes, which is the fail-open the r17 exemption introduced "
        "(#11191 round 2, P2):\n  " + "\n  ".join(_still_refused_offenders)
    )
    # … while a COMPARISON that names both, the true one nearest the phrase, is a
    # correction and must stay GREEN — the other direction the proximity rule could
    # get wrong by treating any `ff6379eb2` mention as an equivalence.
    assert not _refuses_before_9548(
        "`ff6379eb2` returns 5 lines; `047c91f7a`, immediately before #9548, returns 10."
    ), (
        "the proximity rule refuses a COMPARISON that names both trees with the true "
        "one nearest the phrase — that is a correction, not an equivalence assertion "
        "(#11191 round 2, P2)"
    )
    # ⛔ … and the FALSE equivalence is refused EVEN WHEN the true sha is also named and
    # sits NEARER the phrase (#11191 review, P2). Every shape below predicated
    # `ff6379eb2` of the phrase and merely APPENDED the true sha; the four marked
    # EMPHASIS/PARAPHRASE/APPOSITIVE were measured PASS under the token-path veto this
    # rewrite replaced, so they are the regression controls for THAT defect, not padding.
    _asserted_false_with_true_appended = (
        # plain
        "`ff6379eb2` is the tree immediately before #9548 (see also `047c91f7a`).",
        "`ff6379eb2` is the tree before #9548 — note `047c91f7a` for contrast.",
        "The tree immediately before #9548 is `ff6379eb2`, not `047c91f7a`.",
        # EMPHASIS — the doc's own house style (`**NOT** the tree immediately before …`)
        "`ff6379eb2` **is** the tree immediately before #9548 (see also `047c91f7a`).",
        "`ff6379eb2` is **the tree immediately before #9548** (see also `047c91f7a`).",
        # PARAPHRASE — one adverb away from the shape the other controls pin
        "`ff6379eb2` is the tree just before #9548 (see `047c91f7a`).",
        "`ff6379eb2` is the tree that comes immediately before #9548 (see `047c91f7a`).",
        # APPOSITIVE — the PRE-FIX row's own idiom, re-admitted by naming the true sha
        "**5 lines at `ff6379eb2`** — the `origin/main` this branch merges, i.e. the "
        "tree immediately before #9548 — and `047c91f7a` is the arming commit's parent.",
        "`ff6379eb2` (the tree immediately before #9548) reads 5 lines; `047c91f7a` returns 10.",
    )
    # ⛔ …AND AN INCISE IS NOT A REFERENT (#11191 review r4, P2 — measured ACCEPTED at
    # `46c01c324e8`). The referent rule takes the nearest sha written before the phrase,
    # so a parenthetical/dash/comma ASIDE naming the true tree became the referent of a
    # claim whose subject is `ff6379eb2` — and `(` is in no separator class either, so no
    # other arm saw the false sha. An aside is a different proposition from the one the
    # phrase is predicated of; the file's own clause-scoping rule says so.
    _incise_is_not_a_referent = (
        "`ff6379eb2` is (as `047c91f7a` shows) the tree before #9548.",
        "`ff6379eb2` (see `047c91f7a`) is the tree before #9548.",
        "`ff6379eb2` \u2014 see `047c91f7a` \u2014 is the tree before #9548.",
        "`ff6379eb2` is, as `047c91f7a` shows, the tree before #9548.",
    )
    _incise_passed = [
        s for s in _incise_is_not_a_referent if not _refuses_before_9548(s)
    ]
    assert not _incise_passed, (
        "an INCISE naming the true tree is read as the phrase's referent, so a claim "
        "whose subject is `ff6379eb2` passes — naming the true sha in an aside is not "
        "correcting the predication (#11191 review r4, P2):\n  "
        + "\n  ".join(_incise_passed)
    )
    # … while the SAME aside is still a correction when the phrase is predicated of the
    # true tree, so the rule cannot be "any incise reds".
    for _s in _incise_is_not_a_referent:
        assert not _refuses_before_9548(_s.replace(_FALSE_TREE, _TRUE_TREE)), (
            "the incise rule reds a sentence that predicated the phrase of `047c91f7a`, "
            "so the control above proves nothing about the subject "
            "(#11191 review r4, P2):\n  " + _s.replace(_FALSE_TREE, _TRUE_TREE)
        )
    # ⛔ …BUT AN ASIDE MAY BE THE ONLY ATTRIBUTION (#11191 review r4, P1 — measured RED
    # with the unconditional blanking): the tree is named INSIDE the aside and nowhere
    # else, so blanking destroys the referent and a TRUTHFUL sentence is refused. These
    # stay green, and they are what the blanking had to be made conditional on.
    _aside_is_the_attribution = (
        "The census (taken at `047c91f7a`) returns 10 lines immediately before #9548.",
        "The census, measured at `047c91f7a`, returns 10 lines immediately before #9548.",
    )
    _aside_reds = [s for s in _aside_is_the_attribution if _refuses_before_9548(s)]
    assert not _aside_reds, (
        "the incise blanking is unconditional, so a sentence whose ONLY attribution is "
        "inside the aside loses its referent and is refused — a FALSE RED on a truthful "
        "census citation (#11191 review r4, P1):\n  " + "\n  ".join(_aside_reds)
    )
    # ⛔ …AND AN ASIDE IN THE PHRASE'S OWN CLAUSE *IS* THE ATTRIBUTION even when a FALSE
    # sha survives outside it in an EARLIER clause (#11191 review r5, P1 — measured false
    # RED: base ACCEPT, the first two-tier fix REFUSED). This is §A8's own idiom — the row
    # names both trees — and the file's rule is that a guard must not red the fix.
    _aside_subject_of_the_clause = (
        "`ff6379eb2` is the #9601 merge; the arming commit's parent, `047c91f7a`, is the "
        "tree immediately before #9548.",
        "`ff6379eb2` is the #9601 merge; 10 lines at `047c91f7a`, which is the tree "
        "immediately before #9548.",
    )
    _aside_subject_reds = [
        s for s in _aside_subject_of_the_clause if _refuses_before_9548(s)
    ]
    assert not _aside_subject_reds, (
        "the aside tier only applies when NO sha survives outside it, so a truth that names "
        "the false tree as something else in an earlier clause is refused — the §A8 row's "
        "own idiom (#11191 review r5, P1):\n  " + "\n  ".join(_aside_subject_reds)
    )
    # … and the HYPHEN-MINUS aside is the same aside as the em dash: `_CLAUSE_DELIM` does
    # not split on it either, so the clause tier is what refuses it (#11191 review r5, P2).
    assert _refuses_before_9548(
        "`ff6379eb2` - as `047c91f7a` shows - is the tree before #9548."
    ), (
        "the ASCII hyphen aside re-admits the defect while the typographic dashes are "
        "refused, and it is the most common ASCII rendering of the same aside "
        "(#11191 review r5, P2)"
    )
    # … and the EN dash is the same aside as the em dash (`_CLAUSE_DELIM` already treats
    # both as clause boundaries), so it may not re-admit the defect.
    assert _refuses_before_9548(
        "`ff6379eb2` \u2013 see `047c91f7a` \u2013 is the tree before #9548."
    ), (
        "the incise rule keys on the EM dash alone, so the same aside written with the EN "
        "dash re-admits the defect one character from the arm's own control "
        "(#11191 review r4, P1)"
    )
    # ⛔ RESIDUAL, PINNED AS A CHARACTERISATION TEST — NOT AS CORRECT BEHAVIOUR. A tree
    # named in an EARLIER clause still exempts a figure dated to the phrase in the next
    # one, and this is DOCUMENTED rather than guarded because the guard that closed it
    # could not tell the defect from the truth and began refusing truthful sentences
    # (#11191 review r4 → r6: three fix rounds, three false-RED regressions, all on this
    # one shape). The two sentences below differ in NOTHING but whether the figure is the
    # census's answer, so settling it needs the MEASURED count — the arm already has
    # `_census_count_at` — not another regex. Until that lands, the fail-open is
    # pre-existing (base and `origin/main` accept all four shapes) and the assertions here
    # state exactly what the code does today, so a future fix must come with a measurement
    # and must move these deliberately.
    _referent_alibi = (
        "`047c91f7a` is the arming commit; the census returns 3 lines immediately before #9548.",
        "`047c91f7a` is the arming commit, and the census returns 3 lines immediately before #9548.",
        "`047c91f7a` is the arming commit \u2014 the census returns 5 lines immediately "
        "before #9548.",
    )
    # … and its truthful twin, which a figure guard COULD NOT tell it apart from and
    # therefore REFUSED — the false RED that cost the guard its place (#11191 review r6, P1).
    _alibi_truthful_twins = (
        "`047c91f7a` is the arming commit's parent; it returns 10 lines immediately "
        "before #9548.",
        "`047c91f7a` is the arming commit; as #9601 records, the tree before #9548 reads "
        "10 lines.",
        "`047c91f7a` is the arming commit; measured on 2026-10-23, the tree before #9548 "
        "returns 10 lines.",
    )
    _twin_reds = [s for s in _alibi_truthful_twins if _refuses_before_9548(s)]
    assert not _twin_reds, (
        "the earlier-clause figure guard refuses a TRUTHFUL reading that differs from the "
        "alibi only in its figure — a false RED on base and `origin/main`-accepted text, "
        "which is how a pin gets deleted (#11191 review r6, P1):\n  " + "\n  ".join(_twin_reds)
    )
    assert all(not _refuses_before_9548(s) for s in _referent_alibi), (
        "the earlier-clause alibi is a KNOWN, DOCUMENTED RESIDUAL (see the note above), not "
        "a closed class: this assertion pins today's behaviour so the fix that measures the "
        "census cannot change it silently. If it now refuses, a figure guard came back — "
        "check that it does not also refuse the truthful twins above "
        "(#11191 review r6, P1)"
    )
    # … and the sentence-wide referent is NOT withdrawn: a figure AFTER the phrase is a
    # measurement OF it, not an alibi beside it.
    assert not _refuses_before_9548(
        "`047c91f7a` is the arming commit's parent and the tree before #9548 reads 10 lines."
    ), (
        "the referent rule reds a sentence whose figure sits AFTER the phrase — that is a "
        "measurement of the phrase, not an alibi, and refusing it is the false-RED class "
        "this pin exists to remove (#11191 review r4, P1)"
    )
    # …and an aside-borne attribution in an EARLIER clause is still a referent
    # (#11191 review r6, P1 — measured false RED while tier 3 blanked sentence-wide).
    assert not _refuses_before_9548(
        "The census (measured at `047c91f7a`) is the one that matters; the tree before "
        "#9548 reads 10 lines."
    ), (
        "tier 3 blanks asides for the whole sentence, so an attribution carried by an "
        "aside in an EARLIER clause is lost and a truthful sentence is refused "
        "(#11191 review r6, P1)"
    )
    _appended_still_passed = [
        s for s in _asserted_false_with_true_appended if not _refuses_before_9548(s)
    ]
    assert not _appended_still_passed, (
        "a sentence that predicates `ff6379eb2` of the pre-#9548 tree passes — naming "
        "the true sha anywhere buys a pass for the false claim, which is this arm's own "
        "P2 one shape further on (#11191 review, P2):\n  "
        + "\n  ".join(_appended_still_passed)
    )
    # … and the referent rule is what refuses them, not an incidental denial: with the
    # false sha REPLACED BY THE TRUE ONE in the referent position the same strings are
    # exempt, so the refusal is attributable to which tree the phrase refers to. (The
    # previous control swapped in NEUTRAL text instead, which was green only because a
    # named-but-empty referent was treated as "not this defect" — the A1 fail-open. Under
    # the default-deny rule that swap is correctly REFUSED too, and it proves nothing.)
    for _s in _asserted_false_with_true_appended:
        _swapped = _s.replace(_FALSE_TREE, _TRUE_TREE)
        assert not _refuses_before_9548(_swapped), (
            "the predicate reds a sentence whose phrase refers to `047c91f7a`, so the "
            "control above proves nothing about the REFERENT (#11191 review, A1):\n  "
            + _swapped
        )
    # … and a DENIAL is still a correction, not the defect.
    assert not _refuses_before_9548(
        "`ff6379eb2` is not the tree before #9548; that is `047c91f7a`."
    ), (
        "the referent rule refuses a DENIAL (`is not the tree before #9548`), which is a "
        "repair — reding the fix is the false-RED class this pin exists to remove "
        "(#11191 review, P2)"
    )
    # ⛔ … and the TRUE-TREE-FIRST correction — the sentence the fix SHOULD write — must
    # stay GREEN (#11191 review, P2, measured REFUSED under the distance rule this
    # replaces, because the false sha sat nearer the phrase than the true one it plainly
    # refers to). The third case is the same defect with no distance involved at all:
    # under the old `_LINK_WINDOW = 60` a truthful attribution was refused purely for
    # being long.
    _true_tree_first = (
        "`047c91f7a` is the tree immediately before #9548 (10 lines); "
        "`ff6379eb2` is the #9601 merge (5 lines).",
        "`047c91f7a` is the tree immediately before #9548, and `ff6379eb2` is an "
        "ancestor that reads 5.",
        "10 lines at `047c91f7a`, the tree immediately before #9548; 5 lines at "
        "`ff6379eb2`, an ancestor.",
        "The census reads 10 lines at `047c91f7a`, the arming commit's parent, which is "
        "what comes immediately before #9548 here.",
    )
    _true_first_reds = [s for s in _true_tree_first if _refuses_before_9548(s)]
    assert not _true_first_reds, (
        "the phrase's REFERENT is `047c91f7a` and the sentence says so — refusing it is "
        "the false RED that teaches the next fixer to delete this pin, and it is what "
        "distance-based proximity produced (#11191 review, P2):\n  "
        + "\n  ".join(_true_first_reds)
    )
    # ⛔ A REFUTATION that quotes the false claim and then calls it false is a
    # CORRECTION, and it is refused without a CLOSED post-phrase set (#11191 review,
    # P2 — measured REFUSED before that arm existed, because the referent of the quote
    # is `ff6379eb2`). Both spellings are pinned so the set cannot be narrowed away.
    _refutations = (
        "The claim that `ff6379eb2` is the tree before #9548 is false; the true tree is `047c91f7a`.",
        "The idea that `ff6379eb2` is the tree before #9548 — that is wrong; `047c91f7a` is.",
    )
    _refuted_reds = [s for s in _refutations if _refuses_before_9548(s)]
    assert not _refuted_reds, (
        "the arm refuses a REFUTATION of the false claim — quoting a claim in order to "
        "call it false is the correction, and the referent rule reads the quotation as "
        "the defect (#11191 review, P2):\n  " + "\n  ".join(_refuted_reds)
    )
    # ⛔ …AND A REFUTATION WITH AN OBJECT REFUTES SOMETHING ELSE (#11191 review r4, P1 —
    # measured ACCEPTED at `46c01c324e8`): "wrong ON THE CENSUS COUNT" corrects the
    # FIGURE, not the predication, and the `;`-closed arm took it as a refutation of the
    # equivalence. `_REFUTED_AFTER` already carries this qualifier guard; the next-clause
    # arm did not, which made it the one open door in the refutation set.
    _refutation_with_object = (
        "`ff6379eb2` is the tree before #9548; this is wrong on the census count.",
        "`ff6379eb2` is the tree before #9548, and that is wrong on the count.",
        # ⚠️ NOT "…, which is wrong about the month it shipped in" — `_REFUTED_AFTER`
        # already carries the same guard, so that shape was REFUSED at `46c01c324e8` and
        # including it here overstated what THIS arm adds (#11191 review r4, P3 — measured).
        "`ff6379eb2` is the tree before #9548; this is wrong with respect to the count.",
        "`ff6379eb2` is the tree before #9548; this is wrong concerning the count.",
    )
    _object_refuted_passed = [
        s for s in _refutation_with_object if not _refuses_before_9548(s)
    ]
    assert not _object_refuted_passed, (
        "a refutation carrying an OBJECT refutes something else (the figure, the month), "
        "so it may not exempt the equivalence it sits beside — the next-clause arm lacks "
        "the qualifier guard `_REFUTED_AFTER` already has (#11191 review r4, P1):\n  "
        + "\n  ".join(_object_refuted_passed)
    )
    # ⛔ … and that set is CLOSED on purpose: a bare `not` after the phrase is how a
    # FIGURE gets corrected, so admitting it would be a fail-open. Both directions are
    # pinned: the bare `not` does not exempt a false predication, and a figure dated to
    # the phrase with no tree named is the A1 defect whether or not a `not` follows.
    assert _refuses_before_9548(
        "`ff6379eb2` is the tree before #9548 and returns 5 lines, not 10."
    ), (
        "a post-phrase `not` is being read as a refutation of the EQUIVALENCE — it is "
        "how a FIGURE gets corrected, so admitting it lets a cell state the false "
        "equivalence and then say `not 10` (#11191 review r4, P1)"
    )
    # A sentence that attributes NOTHING to the phrase — no figure, no tree — is not
    # this defect (reding it is the false-RED class this file names); dating a figure to
    # the phrase flips it, which is the A1 family below.
    assert not _refuses_before_9548("Re-read the census note before #9548."), (
        "the default-deny rule reds a sentence that attributes NOTHING to the phrase — "
        "a bare pointer to the census note is not a stale claim (#11191 review, A1)"
    )
    # ⛔ …and an ordinary pointer that merely CONTAINS a spelled number dates no figure,
    # so the spelled-figure rule may not fire outside a figure position (#11191 review, F4 —
    # measured REFUSED at `46c01c324e`, accepted by `origin/main`).
    assert not _refuses_before_9548(
        "Re-read the census note before #9548 one more time."
    ), (
        "the spelled-figure arm fires outside a figure POSITION — `one more time` is not "
        "a reading, and refusing an ordinary pointer is the false-RED class this pin "
        "exists to remove (#11191 review, F4, P2)"
    )
    assert _refuses_before_9548("Before #9548 the census returned 3 lines."), (
        "the \"attributes nothing\" exemption is swallowing a FIGURE dated to the "
        "phrase, which is exactly the accidental stale-reading shape this pin exists to "
        "catch (#11191 review, A1, P1)"
    )
    # ⛔ …AND A DENIED FIGURE IS NOT A READING (#11191 review r10, P2 — MEASURED at
    # `870951926e0`: all three below were REFUSED while base and `origin/main` accept
    # them). The doc's OWN prose says "it was not one line either"; a fixer who writes
    # that inside the §A8 cell got a red for a true sentence, which is the false-RED
    # class that gets a pin deleted. The admission is the negator DIRECTLY in front of
    # the figure — the two assertions after these pin that an assertion cannot borrow it.
    _denied_figures = (
        "It was NOT empty immediately before #9548 — and it was not one line either.",
        "The census was not one line immediately before #9548.",
        "The census was not 3 lines immediately before #9548.",
        "It was not even one line immediately before #9548.",
    )
    _denied_figure_reds = [s for s in _denied_figures if _refuses_before_9548(s)]
    assert not _denied_figure_reds, (
        "a DENIAL of a reading is refused as if it were the reading — refusing it is a "
        "false RED on a truthful sentence the doc itself uses (#11191 review r10, P2):\n  "
        + "\n  ".join(_denied_figure_reds)
    )
    _asserted_figure_behind_a_negation = (
        "The census returns no fewer than 3 lines immediately before #9548.",
        "The census returns not more than 3 lines immediately before #9548.",
        # ⛔ …AND A REVERSED POLARITY IS AN ASSERTION TOO (#11191 review r10, P2 —
        # MEASURED: every shape below was ACCEPTED by the first cut of this change, i.e.
        # the retirement rode a doubt frame or a double negation into the exemption).
        "Nothing suggests it is not 3 lines immediately before #9548.",
        "Nothing suggests that the census was not 3 lines immediately before #9548.",
        "It is not the case that the census was not 3 lines immediately before #9548.",
        "It is false that the census was not 3 lines immediately before #9548.",
        "It is not true that the census was not 3 lines immediately before #9548.",
        "It is not correct that the census was not 3 lines immediately before #9548.",
        "There is no evidence that the census was not 3 lines immediately before #9548.",
        "There is no indication that the census was not 3 lines immediately before #9548.",
    )
    _borrowed_denial = [
        s for s in _asserted_figure_behind_a_negation if not _refuses_before_9548(s)
    ]
    assert not _borrowed_denial, (
        "an ASSERTION borrowed the denial cue (`fewer than` / `more than` is not a "
        "negation of the figure), so the denial admission is a fail-open — the cue set "
        "is closed for exactly this reason (#11191 review r10, P2):\n  "
        + "\n  ".join(_borrowed_denial)
    )
    # ⛔ … and DENIAL is not a blanket escape: "not disputed that <sha> IS the tree"
    # ASSERTS the equivalence inside a denial of doubt. The previous `not`-anywhere-in-60
    # window exempted it — a fail-open no control would have noticed (#11191 review, P2).
    _denial_of_doubt = (
        "It is not disputed that `ff6379eb2` is the tree before #9548.",
    )
    _doubt_passed = [s for s in _denial_of_doubt if not _refuses_before_9548(s)]
    assert not _doubt_passed, (
        "the denial escape is a DISTANCE window again, not a predicate position — a "
        "sentence that denies DOUBT while asserting the equivalence passes "
        "(#11191 review, P2):\n  " + "\n  ".join(_doubt_passed)
    )
    # ⛔ …AND THE DOUBT FRAME IS THE ASSERTION WHICHEVER SIDE ITS NEGATION SITS ON
    # (#11191 review r4, P2 — measured ACCEPTED at `46c01c324e8`). The arm above catches
    # "not disputed that <sha> IS the tree", where the negation is in the FRAME. Move the
    # same negation INSIDE the complement — "nothing suggests <sha> IS NOT the tree" —
    # and the claim is identical, but `_DENIED_BY_SHA` reads that inner `is not` as the
    # correction and exempts it. Every shape below asserts the equivalence.
    _doubt_frame_with_inner_not = (
        "Nothing suggests `ff6379eb2` is not the tree immediately before #9548.",
        "It cannot be ruled out that `ff6379eb2` is not the tree before #9548.",
        "No evidence says `ff6379eb2` is not the tree before #9548.",
        "There is no reason to think `ff6379eb2` is not the tree before #9548.",
    )
    _inner_not_passed = [
        s for s in _doubt_frame_with_inner_not if not _refuses_before_9548(s)
    ]
    assert not _inner_not_passed, (
        "a DOUBT FRAME with the negation inside the complement passes — \"nothing "
        "suggests X is not Y\" asserts X IS Y, and `_DENIED_BY_SHA` reads the inner "
        "`is not` as the correction (#11191 review r4, P2):\n  "
        + "\n  ".join(_inner_not_passed)
    )
    # … and the frame set is NEGATIVE-QUANTIFIER only: an AFFIRMATIVE frame in front of the
    # same denial says the opposite (it endorses the correction), so it must stay green.
    # Adding `not in doubt`/`undisputed`/`no doubt` to the set REDed all three
    # (#11191 review r4, P2 — measured).
    _fronted_affirmative_frame = (
        "It is not in doubt that `ff6379eb2` is not the tree before #9548.",
        "Undisputed: `ff6379eb2` is not the tree before #9548.",
        "No doubt `ff6379eb2` is not the tree before #9548.",
        # ⛔ AND A QUANTIFIER MUST NOT TURN AN AFFIRMATIVE FRAME INTO A NEGATIVE ONE
        # (#11191 review r5, P1 — measured false RED): "nobody DOUBTS that X is not Y"
        # endorses the denial; only a suggestive verb makes the quantifier a doubt frame.
        "Nobody doubts that `ff6379eb2` is not the tree immediately before #9548.",
        "No one doubts that `ff6379eb2` is not the tree before #9548.",
        "There is no reason to doubt that `ff6379eb2` is not the tree before #9548.",
    )
    _affirmative_reds = [
        s for s in _fronted_affirmative_frame if _refuses_before_9548(s)
    ]
    assert not _affirmative_reds, (
        "the doubt-frame arm treats an AFFIRMATIVE frame as a doubt frame, so a denial that "
        "fronts its certainty (\"it is not in doubt that X is NOT Y\") is refused — that is "
        "a correction, and reding it is the false-RED class this pin exists to remove "
        "(#11191 review r4, P2):\n  " + "\n  ".join(_affirmative_reds)
    )
    # … and the NEGATIVE-QUANTIFIER set covers the synonym that is the commonest spelling:
    # "no indication" is "no evidence" (#11191 review r4, P3 — measured ACCEPTED).
    assert _refuses_before_9548(
        "There is no indication that `ff6379eb2` is not the tree before #9548."
    ), (
        "the doubt-frame set lists `no evidence` and `no reason` but not `no indication`, "
        "so the same construction spelled the way writers usually spell it passes "
        "(#11191 review r4, P3)"
    )
    # … and the SCOPE (frame before the false sha only) is load-bearing, not decoration
    # (#11191 review r5, P3 — measured: deleting the scope left the suite green, because
    # the only control carried its frame after an em dash, which already ends the clause).
    assert not _refuses_before_9548(
        "The cell says `ff6379eb2` is not the tree before #9548, no evidence suggests it."
    ), (
        "the doubt-frame arm is unscoped: a frame AFTER the denial (…\"is not the tree "
        "before #9548, no evidence suggests it\") is read as the defect, so the scope is "
        "load-bearing and must be measured (#11191 review r5 P3, r6 P2 — the earlier "
        "control carried its frame after an EM DASH, which already ends the clause, so it "
        "passed with the scope deleted and measured nothing)"
    )
    # … and the frame is scoped to what comes BEFORE the false sha, so a correction that
    # carries a doubt frame AFTER its denial is untouched.
    assert not _refuses_before_9548(
        "`ff6379eb2` is not the tree before #9548 — no doubt about it; it is `047c91f7a`."
    ), (
        "the doubt-frame arm reds a DENIAL that carries its frame after the sha, which is "
        "a correction — the false-RED class this pin exists to remove "
        "(#11191 review r4, P2)"
    )
    # ⛔ ARM-DISCRIMINATION CONTROLS (#11191 review round 2, P1 — MEASURED). On this head
    # `_NOT_FALSE_SHA` and `_FALSE_SHA_ADJACENT` could each be DELETED with the whole
    # suite still green, so neither was a control: an arm whose deletion no test notices
    # protects nothing. Each shape below was chosen by MUTATION, not inspection — it
    # flips exactly when that one arm is removed.
    assert not _refuses_before_9548(
        "The tree before #9548 is not `ff6379eb2`; it is `047c91f7a`."
    ), (
        "the COMPLEMENT arm (`… is not `ff6379eb2``) is dead: with its regex deleted "
        "this sentence is refused, so no control executed it (#11191 review r2, P1)"
    )
    assert _refuses_before_9548(
        "10 lines at `047c91f7a` or 5 lines immediately before #9548 at `ff6379eb2`"
    ), (
        "the ADJACENCY arm no longer reds a false sha sitting in the phrase's own clause "
        "while the true tree is named beside it — with the arm deleted this passes, so "
        "the arm was unreachable (#11191 review r2, P1)"
    )
    # ⛔ CONTROLS FOR THE r3 FIXES (#11191 review r3, P1). Each of these was MEASURED
    # unguarded: reverting its fix left the whole suite green, so the fix had no test.
    assert _refuses_before_9548(
        "The census returns five lines immediately before #9548."
    ), (
        "a SPELLED figure dated to the phrase passes — reverting the `_WORD_FIGURE` "
        "guard leaves the suite green, so nothing pinned it (#11191 review r3, P1)"
    )
    assert _refuses_before_9548(
        "The #9601 merge is the tree immediately before #9548."
    ), (
        "an ISSUE-NUMBER subject passes — stripping `#\\d+` from the attribution made "
        "this read as 'attributes nothing' (#11191 review r3, P1)"
    )
    assert not _refuses_before_9548(
        "The tree immediately before #9548 (`047c91f7a`) returns 10 lines."
    ), (
        "a post-phrase APPOSITIVE naming the true tree is refused — reversing the "
        "prefix/appositive fix leaves the suite green (#11191 review r3, P1)"
    )
    assert _refuses_before_9548(
        "The tree immediately before #9548 (`047c91f7a`) is actually `ff6379eb2`."
    ), (
        "the appositive arm is UNANCHORED: naming the true tree in passing exempts the "
        "false equivalence that follows it in the same clause (#11191 review r3, P1)"
    )
    assert not _refuses_before_9548(
        "047c91f7a34e44a989cf47cb1541c62aad1b818b is the tree immediately before #9548 "
        "and returns 10 lines."
    ), (
        "a FULL 40-hex referent (what `git rev-parse` prints) is refused — equality "
        "against the 9-char `_TRUE_TREE` instead of a prefix compare (#11191 review "
        "r3, P1)"
    )
    # ⛔ CONTROLS FOR THE r7 FIXES (#11191 review r7, P1/P2 — each MEASURED wrong at
    # `5d0ad4f57ae`). Same rule as the block above: a fix with no control that flips when
    # the arm is reverted has no test, and these arms were each reachable in BOTH the
    # fail-open and the false-RED direction, so both are pinned.
    assert _refuses_before_9548(
        "`ff6379eb2` not `047c91f7a` is the tree before #9548."
    ), (
        "a COMMA-LESS cleft is read as a correction — reverting the optional comma in "
        "`_CLEFT_PREDICATION` leaves the suite green, and the sentence falls through to "
        "the REFERENT rule whose nearest sha is the appositive (#11191 review r7, P1)"
    )
    assert not _refuses_before_9548(
        "`047c91f7a` not `ff6379eb2` is the tree before #9548."
    ), (
        "the comma-less cleft's TRUTH TWIN is refused — the arm is one-direction blind "
        "again, so the same surface form decides both propositions (#11191 review r7, P1)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` rather than `047c91f7a`, is the tree before #9548."
    ), (
        "a `rather than` cleft is read as a denial of the predication — `_NEG`'s "
        "`rather than` alternative has no sha-object guard, so `_DENIED_BY_SHA` answers "
        "before the cleft arm (#11191 review r7, P1)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` instead of `047c91f7a`, the tree before #9548, reads 5 lines."
    ), (
        "an `instead of` cleft is read as a denial of the predication — same missing "
        "sha-object guard in `_NEG` (#11191 review r7, P1)"
    )
    # ── #11191 review: the classes the r12 head still measured ACCEPTED, pinned ──
    # Every assert below is RED on the head this round reviewed (`5a97fcf`): 7 fail-opens
    # accepted and 2 false REDs. Reverting any one of the four arms this round adds turns
    # one of them red again, which is what makes each arm load-bearing rather than
    # decorative — the "declared rule with no control" finding previous rounds raised.
    for _claim in (
        "`ff6379eb2`, and not `047c91f7a`, is the tree before #9548.",
        "`ff6379eb2`, but not `047c91f7a`, is the tree before #9548.",
        "The arming commit itself is the tree immediately before #9548.",
        "The arming commit itself was the tree immediately before #9548.",
        "The arming commit's son is the tree before #9548.",
        "There is no sign that `ff6379eb2` is not the tree immediately before #9548.",
        "There is no hint that `ff6379eb2` is not the tree immediately before #9548.",
    ):
        assert _refuses_before_9548(_claim), (
            f"ACCEPTED: {_claim!r}\n"
            "the claim dates the false sha to the phrase and no arm refused it — a CLEFT "
            "whose conjunction (`and`/`but`) defeats the fronted-sha separator, a PROSE "
            "subject in the PRE-POSED word order (`_PREPOSED_PREDICATED`), or a "
            "doubt-frame synonym one word past the closed set (#11191 review, P1 — "
            "measured; `origin/main` refuses the prose rows, so they are a REGRESSION)"
        )
    for _truth in (
        "`047c91f7a`, and not `ff6379eb2`, is the tree before #9548.",
        "The arming commit's parent is the tree immediately before #9548.",
        "The claim that `ff6379eb2` is the tree before #9548 is not true; the true tree is `047c91f7a`.",
        "The claim that `ff6379eb2` is the tree before #9548 is untrue; it is `047c91f7a`.",
    ):
        assert not _refuses_before_9548(_truth), (
            f"REFUSED: {_truth!r}\n"
            "a TRUE sentence reds — the cleft must read the FRONTED sha, the pre-posed "
            "arm must exempt the PARENT relation, and `is not true` / `is untrue` ARE "
            "refutations (#11191 review, P1 — measured)"
        )
    assert _refuses_before_9548(
        "The tree immediately before #9548 is the arming commit itself."
    ), (
        "a WRONG tree named in PROSE passes — step 5 decides 'attributes nothing' on "
        "digits alone, so the one-word decay of the doc's own true cell (`047c91f7a`, "
        "the arming commit's parent) carries no figure and is accepted (#11191 review "
        "r7, P1)"
    )
    assert _refuses_before_9548(
        "The last tree before #9548 is the arming commit's son."
    ), (
        "a wrong tree named by RELATION passes — `_PREDICATED_OBJECT` must red every "
        "tree-shaped object that is not the parent relation (#11191 review r7, P1)"
    )
    assert _refuses_before_9548(
        "`047c91f7a` is the arming commit's parent; the tree immediately before #9548 "
        "is the arming commit itself."
    ), (
        "a TRUE tree named in an EARLIER clause exempts a wrong tree named in prose — "
        "the predication check must sit BEFORE the referent branch (the same alibi the "
        "numeral channel closes) (#11191 review r7, P1)"
    )
    assert not _refuses_before_9548(
        "The tree immediately before #9548 is the arming commit's parent."
    ), (
        "naming the true tree by the PARENT RELATION is refused — a false RED produced "
        "by the predication arm, on the doc's own true cell (#11191 review r7, P1)"
    )
    assert not _refuses_before_9548(
        "The tree immediately before #9548 is the parent commit of `785bb2b59`."
    ), (
        "the same true tree named as a PARENT COMMIT is refused — `_PREDICATE_NAMES_"
        "TRUE_TREE` must read the relation, not only the `'s parent` spelling "
        "(#11191 review r7, P1)"
    )
    assert not _refuses_before_9548(
        "The census returns 10 lines before #9548, measured at `047c91f7a`."
    ), (
        "a SPELLED-OUT attribution is refused while its bare twin passes — only the "
        "bare appositive was admitted, and the refusal message tells the author to name "
        "`047c91f7a`, which they did (#11191 review r7, P2)"
    )
    assert not _refuses_before_9548(
        "The census returns 10 lines before #9548, i.e. at `047c91f7a`."
    ), (
        "an `i.e. at` attribution is refused — `_CLAUSE_DELIM` splits on `.`, so the "
        "clause ends INSIDE the abbreviation and the appositive arm can never see it "
        "(#11191 review r7, P2)"
    )
    #: ⛔ AND THIS SHAPE IS THE ONE THAT DISCRIMINATES THE POST-PHRASE GUARD (#11191 review
    #: r8, P2 — MEASURED). The first cut of this control put the false sha INSIDE the
    #: clause as well, so both readings of "the tail" agreed on it and the control passed
    #: with the guard narrowed — it tested a branch it could not tell apart. A `, and`
    #: boundary ends `_CLAUSE_DELIM`'s clause but NOT the sentence, so the false sha sits
    #: outside the clause and only the sentence-wide tail sees it.
    assert _refuses_before_9548(
        "The tree immediately before #9548, measured at `047c91f7a`, and `ff6379eb2` is "
        "what the note said."
    ), (
        "the post-phrase appositive guard is narrowed to the clause: naming the true tree in "
        "an attribution exempts the false equivalence denied beside it in the same sentence "
        "(#11191 review r8, P2)"
    )
    assert _refuses_before_9548(
        "The tree immediately before #9548 is the arming commit's parent, i.e. "
        "`ff6379eb2`."
    ), (
        "a trailing sha AFTER the parent relation is never read — the exemption returns on "
        "`'s parent`, so the A1 claim rides it and the false tree is asserted as the "
        "relation's referent (#11191 review r8, P1)"
    )
    assert _refuses_before_9548(
        "The tree immediately before #9548 is the arming commit's parent, `785bb2b59`."
    ), (
        "the parent relation followed by the ARMING COMMIT's own sha passes — that names "
        "the parent as the arming commit, which is the defect the relation exemption must "
        "not buy (#11191 review r8, P1)"
    )
    assert _refuses_before_9548(
        "The tree immediately before #9548 is the merge commit of `785bb2b59`."
    ), (
        "a NON-PARENT relation is exempted as long as a sha follows `of` — the `of <sha>` "
        "spelling keys on the sha instead of the relation (#11191 review r8, P2)"
    )
    assert not _refuses_before_9548(
        "The tree immediately before #9548 is the arming commit\u2019s parent."
    ), (
        "the parent relation written with a CURLY APOSTROPHE is refused — dropping the "
        "U+2019 width from `_PARENT_RELATION` flips only this shape (#11191 review r8, P2)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` rather than [`047c91f7a`](https://example.invalid/047c91f7a), is the "
        "tree before #9548."
    ), (
        "a DECORATED complement (`[sha](url)`) restores the cleft fail-open — the sha-object "
        "guard only saw the bare spelling, and a `\\b` cannot hold after `[` "
        "(#11191 review r8, P2)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` rather than (see `047c91f7a`), is the tree before #9548."
    ), (
        "a PARENTHESISED-WITH-WORDS complement restores the cleft fail-open — the sha-object "
        "guard's decoration set is the closed `see|namely|the tree|the commit` list, and "
        "dropping it flips only this shape (#11191 review r8, P2)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` rather than (the census row's own measured value) `047c91f7a`, is "
        "the tree before #9548."
    ), (
        "the cleft's gap is capped below prose length — narrowing the window to 30 characters "
        "flips only this shape, which is the fail-open a bare character count produces on a "
        "complement that is spelled out (#11191 review r8, P2)"
    )
    # ⛔ CONTROLS FOR THE r9 FIXES (#11191 review r9 — each MEASURED wrong at the head the
    # previous round pushed). Same rule as every block above: no control, no fix.
    assert _refuses_before_9548(
        "The tree immediately before #9548 is the arming commit's parent \u2014 "
        "`ff6379eb2`, as the note says."
    ), (
        "a wrong tree named after an EM DASH passes — the exemption read a clause tail that "
        "ends at the dash, and this cell writes its prose with dashes (#11191 review r9, P1)"
    )
    assert _refuses_before_9548(
        "The tree immediately before #9548 is the arming commit's parent's parent."
    ), (
        "a RELATION OF A RELATION passes — the grandparent is not the pre-#9548 tree, and "
        "the arm stopped reading at `'s parent` (#11191 review r9, P1)"
    )
    assert _refuses_before_9548(
        "The tree immediately before #9548 is still the arming commit itself."
    ), (
        "one adverb defeats the predication arm — the copula had to be followed IMMEDIATELY "
        "by the object, so `is STILL the arming commit itself` passed (#11191 review r9, P1)"
    )
    assert _refuses_before_9548(
        "The last tree before #9548 stayed the arming commit's son."
    ), (
        "the predication arm only knows COPULAS — `stayed the arming commit's son` passed "
        "because the verb set had no stative verb (#11191 review r9, P1)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` rather than `047c91f7a` (and the census at both trees), is the tree "
        "before #9548."
    ), (
        "a PARENTHESISED `and` inside the complement closes the clause, so the cleft arm is "
        "handed a fragment without the fronted sha and the referent rule accepts the claim "
        "(#11191 review r9, P2)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` rather than the tree sha `047c91f7a`, is the tree before #9548."
    ), (
        "one more decoration than the closed list admits restores the cleft fail-open — the "
        "sha-object set had four fixed spellings (#11191 review r9, P2)"
    )
    assert not _refuses_before_9548(
        "The tree immediately before #9548 is the arming commit's parent, and `785bb2b59` "
        "is the arming commit."
    ), (
        "a sentence that co-references the ARMING COMMIT beside the true parent is refused — "
        "the trailing-sha rule has no truthful direction pinned (#11191 review r9, P2)"
    )
    assert not _refuses_before_9548(
        "The tree immediately before #9548 is the parent commit of `785bb2b59` (the arming "
        "commit is `785bb2b59`)."
    ), (
        "a parenthetical that identifies the arming commit is read as a rival tree — every "
        "other arm blanks an incise and this one did not (#11191 review r9, P2)"
    )
    # ⛔ … AND THE REFUTATION SET IS CLOSED IN ITS SUBJECT TOO (#11191 review r3, P1 —
    # measured PASS before this): an open `<subject> is wrong` window re-admits the very
    # claim the arm refuses, by correcting something ELSE in the same sentence. A figure
    # or the prose is not the equivalence, so neither may buy the exemption.
    _unrelated_refutation = (
        "`ff6379eb2` is the tree before #9548, but the 5-line figure is wrong.",
        "`ff6379eb2` is the tree before #9548; the census prose is wrong.",
        "`ff6379eb2` is the tree before #9548, and the epic's guess about the month it "
        "shipped in is wrong.",
    )
    _unrelated_passed = [
        s for s in _unrelated_refutation if not _refuses_before_9548(s)
    ]
    assert not _unrelated_passed, (
        "a refutation of something ELSE in the sentence exempts the false equivalence — "
        "the refuted subject must be the claim itself (a demonstrative or a named claim "
        "word), never any `is wrong` within reach (#11191 review r3, P1):\n  "
        + "\n  ".join(_unrelated_passed)
    )
    # ⛔ … and the DENIAL arm may not cross a CLAUSE boundary — same class, same reason
    # (#11191 review, A-grade false PASS — measured; and #11191 review A2, P1 for the
    # `, and` and `—` spellings the `;` fix left open). Denying one proposition in the
    # first clause must not exempt the equivalence asserted in the second. All three
    # boundaries `_CLAUSE_DELIM` recognises are pinned here, because the previous head
    # closed `;` alone and the reviewer measured the other two still passing.
    _denial_across_clause = (
        "`ff6379eb2` is not the arming commit; the tree before #9548 is `ff6379eb2`.",
        "`ff6379eb2` is not the arming commit, and the tree before #9548 is `ff6379eb2`.",
        "`ff6379eb2` is not the arming commit — the tree before #9548 is `ff6379eb2`.",
        "**NOT** the tree immediately before #9548, and `ff6379eb2` is the tree before #9548.",
        # ⛔ …AND THE COMMA-LESS CONJUNCTION IS THE SAME BOUNDARY (#11191 review A2's
        # class, re-measured on this head): with `,\s+and` only, the denial's clause ran
        # through the conjunction into the next proposition, so the first clause exempted
        # the false equivalence in the second. The comma was never the boundary.
        "`ff6379eb2` is not the arming commit and the tree before #9548 is `ff6379eb2`.",
        "`ff6379eb2` is not the arming commit but the tree before #9548 is `ff6379eb2`.",
    )
    _denial_crossed = [s for s in _denial_across_clause if not _refuses_before_9548(s)]
    assert not _denial_crossed, (
        "the denial's window crosses a CLAUSE boundary, so a denial of a DIFFERENT "
        "proposition in the first clause exempts the false equivalence in the second — a "
        "false PASS in gate code (#11191 review, A2, P1):\n  " + "\n  ".join(_denial_crossed)
    )
    # ⛔ … AND THE A2 CASE ON THE ARTEFACT ITSELF (#11191 review, A2, P1 — measured
    # PASS at the previous head): because the correction arms returned for the WHOLE
    # sentence, §A8's own correct `NOT the tree immediately before #9548` exempted a
    # false equivalence injected beside it — the pin was disabled on the exact sentence
    # it guards. ⚠️ The injection goes into the SAME SENTENCE (a `, and` clause), not
    # after the period: a new sentence is caught by the referent rule even at the pre-fix
    # head (measured), so it would demonstrate nothing about the clause scope.
    _injected_row = row.replace(
        "the tree that answers this question.",
        "the tree that answers this question, and `ff6379eb2` is the tree before #9548.",
    )
    assert _injected_row != row, (
        "the A2 control no longer injects into §A8's evidence cell, so it proves nothing"
    )
    _injected_offenders = [
        sentence
        for sentence in re.split(r"(?<=\.)\s+", _injected_row)
        if _refuses_before_9548(sentence)
    ]
    assert _injected_offenders, (
        "a false equivalence injected into §A8's own evidence sentence passes, because "
        "the cell's own denial exempts the whole sentence — the pin is disabled on the "
        "artefact it exists to guard (#11191 review, A2, P1)"
    )
    # ⛔ …AND THE INJECTION MUST ALSO BE CAUGHT IN THE SAME CLAUSE, which is where the
    # reviewer's own reproduction put it (#11191 review, A2 — measured PASS at every head
    # before this one). The control above appends a whole new sentence, so it exercises
    # the sentence split, not the clause scoping: appended with no full stop and no comma
    # before the conjunction, the false equivalence sits INSIDE the cell's phrase-bearing
    # clause, beside the cell's own `**NOT** the tree immediately before #9548`. With the
    # clause boundary keyed on the `, and` comma, `_DENIED_IN_PREDICATE` then exempted it —
    # the artefact's own correct denial disabling the pin on the artefact.
    _same_clause_injected = row.replace(
        "the arming commit's parent)",
        "the arming commit's parent) and `ff6379eb2` is the tree before #9548",
    )
    assert _same_clause_injected != row, (
        "the A2 in-clause control no longer injects beside §A8's own denial, so it "
        "proves nothing (#11191 review, A2)"
    )
    assert any(
        _refuses_before_9548(sentence)
        for sentence in re.split(r"(?<=\.)\s+", _same_clause_injected)
    ), (
        "a false equivalence injected INSIDE the same clause as §A8's own `NOT the tree "
        "immediately before #9548` passes — the denial is clause-scoped but the clause is "
        "delimited on the COMMA, so a comma-less conjunction lets it reach the injected "
        "claim (#11191 review, A2)"
    )
    # ⛔ …AND THE COMMA SPLICE, WHICH IS THE SAME INJECTION WITH NO CONJUNCTION AT ALL
    # (#11191 review r11, P1 — MEASURED ACCEPTED on this row at `b0e8e2006d4`). The
    # control above joins the injected claim with `and`, so it exercises the CLAUSE
    # BOUNDARY; that boundary is deliberately keyed on the conjunction, so the splice —
    # `… the arming commit's parent), `ff6379eb2` is the tree before #9548` — stayed in ONE
    # clause with the cell's own `**NOT** the tree immediately before #9548`, and the
    # occurrence-wide denial arm exempted the injected second occurrence with it. The pin
    # was disabled on the cell it exists to guard, by the cell's own correction.
    _comma_spliced = row.replace(
        "the arming commit's parent)",
        "the arming commit's parent), `ff6379eb2` is the tree before #9548",
        1,
    )
    assert _comma_spliced != row, (
        "the A2 comma-splice control no longer injects into §A8's evidence cell, so it "
        "proves nothing (#11191 review r11, A2)"
    )
    assert any(
        _refuses_before_9548(sentence)
        for sentence in re.split(r"(?<=\.)\s+", _comma_spliced)
    ), (
        "a false equivalence COMMA-SPLICED beside §A8's own `NOT the tree immediately "
        "before #9548` passes — the denial arm is handed the whole clause, so the cell's "
        "own correction exempts a second occurrence after the comma. The contract's first "
        "rule is that EVERY occurrence is decided on its own (#11191 review r11, A2)"
    )
    # …and the splice control cannot be satisfied by refusing the truthful splice: the
    # same position, carrying the tree the phrase actually refers to, must stay green.
    _comma_spliced_true = row.replace(
        "the arming commit's parent)",
        "the arming commit's parent), `047c91f7a` is the tree before #9548",
        1,
    )
    assert not any(
        _refuses_before_9548(sentence)
        for sentence in re.split(r"(?<=\.)\s+", _comma_spliced_true)
    ), (
        "the comma-splice control refuses the TRUTHFUL splice as well, so it measures the "
        "comma rather than the claim — a false RED in the same arm that closes the "
        "fail-open (#11191 review r11, A3)"
    )
    # ⭕ THE MIRROR OF THE SPLICE — the false claim FIRST, the cell's denial second (#11191
    # review r11, P1 — MEASURED ACCEPTED while the span above was left-bounded only). A
    # denial is a claim about the predication it sits beside, so it must not reach
    # BACKWARDS any more than it may reach forwards: the denial here belongs to the SECOND
    # occurrence, and it was exempting the first.
    assert _refuses_before_9548(
        f"`{_FALSE_TREE}` is the tree before #9548, **NOT** the tree immediately before #9548."
    ), (
        "a false equivalence placed BEFORE §A8's own `NOT the tree immediately before #9548` "
        "passes — the occurrence span is left-bounded only, so a LATER occurrence's denial "
        "exempts an EARLIER one (the mirror of the comma splice above) "
        "(#11191 review r11, A2)"
    )
    assert not _refuses_before_9548(
        f"`{_FALSE_TREE}` is the tree before #9601, **NOT** the tree immediately before #9548."
    ), (
        "the mirror control refuses the truthful spelling too — a tree denied the #9548 "
        "predication after denying a DIFFERENT claim is a correction, and refusing it is "
        "the false-RED direction of the same arm (#11191 review r11, A3)"
    )
    # ⭕ `not` IS A WORD (#11191 review r11, P2 — MEASURED ACCEPTED). The closed negation
    # set matched inside ordinary prose, so a false equivalence passed on the word
    # NOTABLE / NOTE alone: the arm read "is notable" as "is not" and "the note" as "not".
    for _negation_lookalike in (
        f"The `{_FALSE_TREE}` commit is notable as the tree before #9548.",
        f"The row at `{_FALSE_TREE}`, per the note, is the tree before #9548.",
    ):
        assert _refuses_before_9548(_negation_lookalike), (
            "a false equivalence passes because a word CONTAINING `not` (notable, note) "
            "was read as the negation token — the closed negation set is matching inside "
            f"prose: {_negation_lookalike!r} (#11191 review r11, P2)"
        )
    assert not _refuses_before_9548(
        f"`{_FALSE_TREE}` is notable, but it is not the tree before #9548."
    ), (
        "the `not`-is-a-word control refuses a truthful denial that merely shares a "
        "sentence with the word `notable` — the word boundary must not cost the "
        "correction it protects (#11191 review r11, A3)"
    )
    # … and the conjunction boundary does not detach a phrase from the tree it attributes
    # to: the referent rule reads the whole sentence, so this truthful attribution stays
    # green with the boundary in place. Without this control the fix above could tighten
    # into the false-RED direction of the same rule.
    assert not _refuses_before_9548(
        "`047c91f7a` is the arming commit's parent and the tree before #9548 reads 10 lines."
    ), (
        "cutting at the conjunction detached the phrase from the tree it attributes to — "
        "the boundary is for DENIALS, and the referent rule must stay sentence-wide "
        "(#11191 review, A3: a false RED on a correction is how this pin gets deleted)"
    )
    # ⛔ CONTROLS FOR THE r11 ANCESTRY FIX (#11191 review r11, P1 — each MEASURED REFUSED
    # at `c281ea64a`, the head the r10/r11 rounds left, and each ACCEPTED by `origin/main`,
    # so the arm they pin closes a REGRESSION rather than a hardening nit). The kinship
    # relation is a TRUE claim and the defect is the COPULA; `git merge-base
    # --is-ancestor ff6379eb2 047c91f7a` exits 0, `git rev-list --count
    # ff6379eb2..047c91f7a` is 12.
    assert not _refuses_before_9548(
        "`ff6379eb2` is an ancestor of the tree before #9548, `047c91f7a`."
    ), (
        "a TRUE ancestry relation is refused — the referent rule reads the bare sha "
        "before the phrase and stops there, so the one sentence that CORRECTS the "
        "misreading by stating what `ff6379eb2` actually is to the true tree reads as the "
        "defect (#11191 review r11, P1)"
    )
    assert not _refuses_before_9548(
        "`ff6379eb2`, an ancestor of the tree before #9548 (`047c91f7a`), reads 5 lines."
    ), (
        "the APPOSITIVE spelling of the same true ancestry is refused — it is the natural "
        "way to state a lineage without fronting a copula, and `origin/main` accepts it "
        "(#11191 review r11, P1)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` is an ancestor of the tree before #9548."
    ), (
        "the ancestry exemption becomes an ALIBI: with the true tree absent from the clause "
        "the kinship relation must not buy a pass — every truthful sentence above names the "
        "tree it is an ancestor OF, and that referent is what makes the claim checkable "
        "(#11191 review r11, P1)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` is an ancestor of the tree before #9548, `047c91f7a`, and the tree "
        "immediately before #9548 is `ff6379eb2`."
    ), (
        "the ancestry relation exempts a clause that ALSO re-asserts the equivalence — the "
        "exemption is per OCCURRENCE like every other arm, and a true sha named beside the "
        "false claim does not make the claim true (#11191 review r11, P1)"
    )
    assert _refuses_before_9548(
        "`ff6379eb2` is the tree immediately before #9548, an ancestor of the tree "
        "`047c91f7a`."
    ), (
        "the ancestry exemption shelters a COPLA asserted first — a true kinship added "
        "beside the equivalence bought it a pass, and injected into the REAL §A8 cell it "
        "took the pin GREEN, i.e. the exemption disabled the pin on the artefact it "
        "guards (#11191 review r11, P2). This control REPLACES one that could not fail "
        "for its stated reason: `_CLAUSE_DELIM` split the earlier spelling at `, and`, so "
        "the equivalence landed in a different clause and the assertion was red whether "
        "or not the arm existed — which is how this class survived a round that "
        "believed it was covered"
    )
    assert _refuses_before_9548(
        "`c5cbcbb4f` is the tree immediately before #9548, an ancestor of the tree "
        "`047c91f7a`."
    ), (
        "a THIRD tree rides the same ancestry alibi — the exemption must be withheld for "
        "any non-true sha copulated to the phrase, not only the two the class names "
        "(#11191 review r11, P2)"
    )
    # …and the truthful kinships must stay green in every spelling, or the copula gate
    # becomes the false RED that costs the pin its place. The object test is what keeps
    # `IS AN ANCESTOR OF the tree` from being read as the copula it is not.
    assert not _refuses_before_9548(
        "`ff6379eb2` is the #9601 merge, an ancestor of `047c91f7a`, the tree immediately "
        "before #9548."
    ), (
        "the COMPACT kinship spelling is refused — the relation required the literal words "
        "\"the tree\" after the kinship noun, so its twin one word shorter red while the "
        "long form passed; a reword must not flip the verdict (#11191 review r11, P1)"
    )
    assert not _refuses_before_9548(
        "The tree before #9548, `047c91f7a`, reads 10 lines, not the 5 stated at "
        "`ff6379eb2`."
    ), (
        "the sentence this pin's OWN failure message recommends is refused — naming the "
        "false tree in order to REFUTE it is a correction, and a lane that follows the "
        "pin's advice must not land RED (#11191 review r11, P1)"
    )
    assert _refuses_before_9548(
        "The tree before #9548 returns 5 lines at `ff6379eb2`, and `047c91f7a` is "
        "unrelated."
    ), (
        "the refutation test becomes an ALIBI in the other direction: a genuinely ASSERTED "
        "false tree must still be refused once `_asserts_false_tree` replaces the bare "
        "substring test — the exemption is for a REFUTATION in front of the token, not for "
        "any sentence that mentions it (#11191 review r11, P1)"
    )
    # ⛔ THE ANAPHORA RESIDUAL, PINNED AS A CHARACTERISATION — NOT AS CORRECT BEHAVIOUR
    # (#11191 review r11, P3 — the PR body states this residual but the CODE did not, and
    # an unpinned residual cannot red when a future fix moves it). A sentence that names
    # BOTH trees with the false one predicated of the phrase is refused even when anaphora
    # makes it truthful. No word-level rule separates it from the defect, because the
    # defect IS `ff6379eb2` predicated of that phrase; the remedy is a reword, which is
    # what the epic's own §A8 row already does ("that is `047c91f7a`, the arming commit's
    # parent"). `origin/main` accepts it — that is the blunt-substring exemption the rest
    # of this predicate exists to remove, not a behaviour worth restoring.
    _anaphora_known_refused = (
        "`047c91f7a` (10 lines) versus `ff6379eb2` (5 lines): the former is the tree "
        "before #9548.",
    )
    _anaphora_moved = [
        s for s in _anaphora_known_refused if not _refuses_before_9548(s)
    ]
    assert not _anaphora_moved, (
        "the anaphora residual is a KNOWN, DOCUMENTED RESIDUAL (see the note above), not a "
        "closed class: this assertion pins today's behaviour so a future fix — the one that "
        "resolves the anaphor to the tree it names — must move it deliberately rather than "
        "silently. If it now refuses... it already did; if it now PASSES, delete this pin "
        "and the residual note together (#11191 review r11, P3):\n  "
        + "\n  ".join(_anaphora_moved)
    )
    # ⛔ A1 — A FIGURE DATED TO THE PHRASE WITH NO TREE NAMED IS THE DEFECT (#11191
    # review, A1, P1 — measured: `main` refused 15 of 15 of this family, the previous
    # head refused 0). It is the accidental form: a lane pastes a fresh number and dates
    # it to the phrase without naming a tree, which is the r4 defect verbatim. It is also
    # the case the referent-empty `return False` waved through, and the case a THIRD tree
    # slipped past because the referent rule only knew the two shas it names.
    _dated_with_no_referent = (
        "The census returns 3 lines immediately before #9548.",
        "The census returns 5 lines immediately before #9548.",
        "It returns 5 lines before #9548.",
        "The tree before #9548 returns 5 lines, not 10.",
        "`c5cbcbb4f` is the tree immediately before #9548.",
    )
    _no_referent_passed = [
        s for s in _dated_with_no_referent if not _refuses_before_9548(s)
    ]
    assert not _no_referent_passed, (
        "a sentence dates a figure (or a third tree) to \"before #9548\" without naming "
        "`047c91f7a`, so the stale reading re-enters §A8 and the pin goes green — the "
        "default is DENY here, not accept (#11191 review, A1, P1):\n  "
        + "\n  ".join(_no_referent_passed)
    )
    # ⛔ A3 — the correction SPELLINGS that were dropped at `d4ba94671e` together with
    # their controls (#11191 review, A3, P2 — each one measured REFUSED at the previous
    # head). Restored as ONE `_NEG` alternation plus the two idiom arms, with a control
    # each, so no spelling can be "temporarily" lost without its control flipping — which
    # is how the previous round's fix deleted the truthful half and went RED on it.
    _restored_corrections = (
        # the `;`-separated two-tree comparison (REFUSED while `;` sat in
        # `_FALSE_SHA_ADJACENT`'s separator class, while the comment claimed it was green)
        "`047c91f7a` is the tree immediately before #9548; `ff6379eb2` is the #9601 merge.",
        "`ff6379eb2` is the #9601 merge, and the tree before #9548 is `047c91f7a`.",
        # the contraction / modal / `rather than` denials
        "`ff6379eb2` isn't the tree immediately before #9548.",
        "`ff6379eb2` cannot be the tree immediately before #9548.",
        "`ff6379eb2` is an ancestor rather than the tree before #9548.",
        # a correction that RETIRES the attribution
        "§A8 no longer calls `ff6379eb2` the tree before #9548.",
        "§A8 does not call `ff6379eb2` the tree before #9548.",
        "The row never said `ff6379eb2` is the tree before #9548.",
        # the correction idioms: corrected / replaced / disproven / inference denied
        'The row that said "the tree before #9548 is `ff6379eb2`" is now corrected to `047c91f7a`.',
        "The retired reading was `ff6379eb2` for the tree before #9548, replaced with "
        "`047c91f7a`.",
        "That `ff6379eb2` is the tree before #9548 is disproven by `047c91f7a`.",
        "That `ff6379eb2` is the tree before #9548 does not mean the census is 5.",
    )
    _restored_reds = [s for s in _restored_corrections if _refuses_before_9548(s)]
    assert not _restored_reds, (
        "the pin refuses a TRUTHFUL correction — this file's own remedy for a false RED "
        "is to reword or delete the pin, so a correction spelling must never be the shape "
        "it rejects (#11191 review, A3, P2):\n  " + "\n  ".join(_restored_reds)
    )
    # … and the idiom arms are CLOSED: a correction VERB alone must not buy a pass, or it
    # would exempt a stale figure that merely mentions a replacement.
    assert _refuses_before_9548("The census reading before #9548 was replaced with 3 lines."), (
        "a correction verb exempts a FIGURE dated to the phrase with no true tree named — "
        "the idiom arm is then a blank cheque (#11191 review, A3)"
    )
    # ⛔ … and the INVERTED arm admits a few words between the copula and the sha: with
    # the sha required to follow `is` immediately, a five-word insertion walked past it and
    # the sentence that ASSERTS `ff6379eb2` of the phrase passed (#11191 review r3, P1).
    assert _refuses_before_9548(
        "`047c91f7a` — the tree before #9548 is, per the note, `ff6379eb2`."
    ), (
        "the inverted arm requires the false sha to follow the copula immediately, so "
        "`the tree before #9548 is, per the note, <false sha>` passes — the same "
        "fail-open with one insertion (#11191 review r3, P1)"
    )
    # … and must NOT red on any of the three truthful corrections the r17 review
    # measured RED before the exemption.
    _truthful = (
        "The tree immediately before #9548 is `047c91f7a`, where the same path set returns 10.",
        "`047c91f7a` is the tree immediately before #9548.",
        "Measured at `047c91f7a` — the tree immediately before #9548 — the census returns 10.",
        # ⛔ …and the referent may FOLLOW the phrase without being its very next token
        # (#11191 review, F3, P2 — measured REFUSED at `46c01c324e` while `origin/main`
        # accepts both). These two are also the controls for the anchoring: re-anchoring
        # the post-phrase referent arm to `re.match` reds them.
        "The census returns 10 lines before #9548, measured at `047c91f7a`.",
        "The tree immediately before #9548 is the arming commit, i.e. `047c91f7a`.",
    )
    _false_reds = [s for s in _truthful if _refuses_before_9548(s)]
    assert not _false_reds, (
        "the denial arm refuses a TRUTHFUL correction — naming the true pre-#9548 "
        "tree is the fix, so it may not be the shape this pin rejects:\n  "
        + "\n  ".join(_false_reds)
    )
    # … and those strings are load-bearing in that direction: none of them carries a
    # denial, so without the `047c91f7a` exemption the arm above would red them. A
    # control that already denied the phrase would prove nothing.
    _denial_arm = re.compile(
        r"\b(?:not|never|isn't|is not)\b[^.]{0,40}?immediately\s+before\s+#9548", re.I
    )
    assert not any(_denial_arm.search(s) for s in _truthful), (
        "a control string carries a denial, so it no longer demonstrates that the "
        "exemption is what makes a truthful correction pass (review r17, P2)"
    )
    # ⛔⛔ CONTROLS FOR THE FAIL-OPENS THIS REVIEW MEASURED (#11191 review, F1-F4 — each
    # one RE-CONFIRMED live on the head it reviewed, each one graded differently by the
    # shipped predicate than by `origin/main`). Every block below is measured, not
    # written from the fix: the string was run through the PRE-fix predicate and
    # produced the bad verdict its comment names, so reverting the fix reds the arm.
    # Both directions are pinned wherever the defect is one arm producing OPPOSITE
    # verdicts from the same surface form, because a single-direction probe cannot see
    # that class.
    # ⚠️ F2's arm was already hardened in this branch before this commit landed (the
    # `_REFUTED_NEXT_CLAUSE` object guard, with a WIDER qualifier set than the fix here
    # proposed); what follows therefore PINS its em-dash boundary rather than claiming
    # the fix, and it is kept because the sibling `;`-joined shapes are pinned beside it.
    #
    # F1 — a BARE `not` binds to the COMPLEMENT, so a fronted cleft that predicates the
    # FALSE tree read as a correction. `_NEG`'s bare alternative is guarded against a
    # sha-shaped object and the cleft is decided by `_CLEFT_PREDICATION` on the sha that
    # FRONTS the sentence. Pre-fix, ALL THREE shapes below were ACCEPTED (one arm, three
    # different propositions), which is why the hole was invisible to a one-sided probe.
    _cleft_shapes = (
        # the CLAIM, in a natural corrective register — must REFUSE
        ("`ff6379eb2`, not `047c91f7a`, is the tree before #9548.", True),
        # …and the A1 third-tree variant of the same cleft — must REFUSE, because the
        # fronted sha is the SUBJECT and it is not the true tree
        ("`c5cbcbb4f`, not `ff6379eb2`, is the tree before #9548.", True),
        # its TRUTHFUL twin — must stay GREEN (pre-fix it passed by the same arm, which
        # is exactly what made the fail-open invisible)
        ("`047c91f7a`, not `ff6379eb2`, is the tree before #9548.", False),
        # …and the complement order, which `_NOT_FALSE_SHA` must keep deciding
        ("The tree before #9548 is `047c91f7a`, not `ff6379eb2`.", False),
    )
    # ⛔ …and the COMMA-LESS spelling of the same cleft, both directions: the comma form
    # above is what an incise rule sees, so without this pair the arm could lose its
    # optional-comma coverage and stay green (#11191 review, F1).
    assert _refuses_before_9548(
        "`ff6379eb2` not `047c91f7a` is the tree before #9548."
    ), (
        "the comma-less FRONTED CLEFT passes: the fronted sha is the SUBJECT, so the "
        "sentence asserts `ff6379eb2` of the phrase and the appositive's true sha becomes "
        "the referent (#11191 review, F1, P2)"
    )
    assert not _refuses_before_9548(
        "`047c91f7a` not `ff6379eb2` is the tree before #9548."
    ), (
        "the comma-less cleft's TRUTHFUL twin is refused — the arm must decide by which "
        "tree is PREDICATED of the phrase, never by the presence of a negation "
        "(#11191 review, F1, P2)"
    )
    _cleft_wrong = [
        (s, want, _refuses_before_9548(s))
        for s, want in _cleft_shapes
        if _refuses_before_9548(s) is not want
    ]
    assert not _cleft_wrong, (
        "the CLEFT is decided by the negator instead of by the sha that FRONTS the "
        "sentence, so `<false sha>, not <true sha>, is the tree before #9548` — the "
        "claim this pin exists to refuse — passes, and its truthful twin is decided by "
        "the same arm (one arm, opposite verdicts; a one-directional probe cannot see "
        "it) (#11191 review, F1, P2):\n  "
        + "\n  ".join(
            f"REFUSED={got} (wanted {want}) {s}" for s, want, got in _cleft_wrong
        )
    )
    # …and the guard that makes the cleft reachable is load-bearing in its OWN right: a
    # bare `not` must still deny a PREDICATE (the `_NEG` alternative is narrowed, not
    # deleted), or the fix above would trade this fail-open for a false RED on every
    # `is not the tree …` correction.
    assert not _refuses_before_9548("`ff6379eb2` is not the tree before #9548."), (
        "narrowing `_NEG`'s bare-`not` alternative to reject a sha-shaped OBJECT also "
        "removed the PREDICATE denial — `is not the tree before #9548` is the plainest "
        "correction a fixer writes, and reding it is the false-RED class this pin "
        "exists to remove (#11191 review, F1)"
    )
    # F2 — `_REFUTED_NEXT_CLAUSE` lost the object guard `_REFUTED_AFTER` carries, so a
    # refutation of the WRONG proposition exempted the false equivalence one clause
    # later. Pre-fix every string below was ACCEPTED while `origin/main` refused it.
    # ⚠️ The guard itself landed in this branch before this commit (see the note above);
    # these shapes pin the EM-DASH boundary, which the sibling `;`-joined controls do not
    # reach, so a future narrowing of the qualifier set reds here first.
    _unrelated_refutation_next_clause = (
        "`ff6379eb2` is the tree before #9548 — this is wrong about the month it shipped in.",
        "`ff6379eb2` is the tree before #9548 — which is wrong on the tree it names.",
        "`ff6379eb2` is the tree before #9548 — that is incorrect in the census prose.",
    )
    _next_clause_passed = [
        s for s in _unrelated_refutation_next_clause if not _refuses_before_9548(s)
    ]
    assert not _next_clause_passed, (
        "the NEXT-CLAUSE refutation arm takes an OBJECT, so a refutation of something "
        "else exempts the false equivalence — it is the same predicate as "
        "`_REFUTED_AFTER`, which carries `(?!\\s+(?:about|on|in|for|at)\\b)` for exactly "
        "this reason, and losing the guard one clause later is a regression against "
        "`origin/main` on the phrase this pin guards (#11191 review, F2, P1):\n  "
        + "\n  ".join(_next_clause_passed)
    )
    # …while the demonstrative refutation it was written for stays GREEN — the guard
    # must not take the shape the arm exists to accept.
    assert not _refuses_before_9548(
        "The idea that `ff6379eb2` is the tree before #9548 — that is wrong; `047c91f7a` is."
    ), (
        "the object guard on the next-clause refutation also removed the DEMONSTRATIVE "
        "refutation the arm exists for — the fix must not red its own purpose "
        "(#11191 review, F2)"
    )
    # ⛔ …AND THE ARM ITSELF IS PINNED, not just the em-dash shape `_REFUTED_AFTER`
    # already admits (#11191 review r10, P2 — MEASURED). Deleting `_REFUTED_NEXT_CLAUSE`
    # left the suite GREEN while turning truthful refutations into false REDs, so the rule
    # the Review Scope Contract lists had no control of its own; the em-dash spelling
    # above never reaches it. ⚠️ Of the three strings below, ONLY the first depends on the
    # arm — the other two are admitted by `_REFUTED_AFTER` and are here as the shape's
    # neighbours, not as its controls (measured by deleting the arm and reading each
    # verdict: the first flips accept→REFUSE, the other two stay accept). Asserting them
    # is still what makes the block red when the arm goes: the arm's own string is in it.
    _next_clause_refutation = (
        "The row calls `ff6379eb2` the tree before #9548 in the note, and that is wrong.",
        "The row calls `ff6379eb2` the tree before #9548, and that is wrong.",
        "The note says `ff6379eb2` is the tree before #9548, and that is wrong.",
    )
    _next_clause_reds = [s for s in _next_clause_refutation if _refuses_before_9548(s)]
    assert not _next_clause_reds, (
        "the next-clause demonstrative refutation (`_REFUTED_NEXT_CLAUSE`) is refused — "
        "that arm is the only thing that admits it, so deleting the arm must red HERE "
        "instead of leaving the suite green (#11191 review r10, P2):\n  "
        + "\n  ".join(_next_clause_reds)
    )
    # F3 — `_INFERENCE_DENIAL` was a clause-wide `search` with no subject binding, so a
    # denial of an INFERENCE attached to the ASSERTION exempted the equivalence. Pre-fix
    # both shapes below were ACCEPTED while `origin/main` refused them.
    _unbound_inference_denial = (
        "`ff6379eb2` is the tree before #9548, which does not mean the census is 5.",
        "`ff6379eb2` is the tree before #9548, and it does not imply the count grew.",
    )
    _inference_passed = [
        s for s in _unbound_inference_denial if not _refuses_before_9548(s)
    ]
    assert not _inference_passed, (
        "the INFERENCE-DENIAL arm is a blank cheque: a `does not mean` anywhere in the "
        "clause exempts the false equivalence, because the denial of what the claim is "
        "USED FOR is read as a denial of the claim. Every other correction arm here is "
        "subject-bound for this reason (#11191 review, F3, P1):\n  "
        + "\n  ".join(_inference_passed)
    )
    # …and the QUOTED-claim shape the arm was written for stays GREEN: the denied
    # inference's subject must be the claimed equivalence.
    assert not _refuses_before_9548(
        "That `ff6379eb2` is the tree before #9548 does not mean the census is 5."
    ), (
        "binding the inference denial to its `that`-clause subject also removed the "
        "quoted-claim correction it exists for — the fix must not red its own purpose "
        "(#11191 review, F3)"
    )
    # …and the binding is genuinely load-bearing: without it BOTH spellings pass, so the
    # pair above is a discrimination test, not two independent assertions.
    assert _INFERENCE_DENIAL.search(
        "That `ff6379eb2` is the tree before #9548 does not mean the census is 5."
    ) and not _INFERENCE_DENIAL.search(
        "`ff6379eb2` is the tree before #9548, which does not mean the census is 5."
    ), (
        "the inference-denial binding does not discriminate the quoted claim from the "
        "bare assertion, so the arms above prove nothing (#11191 review, F3)"
    )
    # F4 — the spelled-figure list was CLOSED at `twelve`, so every figure above twelve
    # (and `zero`) walked past it. Pre-fix all four shapes below were ACCEPTED, which is
    # the A1 fail-open one word past the guard.
    _spelled_figures_past_twelve = (
        "The census returns thirteen lines immediately before #9548.",
        "The census returns zero lines immediately before #9548.",
        "The census returns twenty-five lines immediately before #9548.",
        "The census returns a hundred lines immediately before #9548.",
    )
    _spelled_passed = [
        s for s in _spelled_figures_past_twelve if not _refuses_before_9548(s)
    ]
    assert not _spelled_passed, (
        "a SPELLED figure above twelve (or `zero`) dated to the phrase with no referent "
        "passes — the guard is a closed list that runs out, so its boundary is whatever "
        "number the next writer picks, and the A1 fail-open it closes is one word away "
        "(#11191 review, F4, P2):\n  " + "\n  ".join(_spelled_passed)
    )
    # …and the list must still not fire on prose that names NO figure: the sensitivity
    # above is exactly what could turn this arm into a false RED on a bare pointer.
    assert not _refuses_before_9548("Re-read the census note before #9548."), (
        "the widened spelled-figure grammar reds a sentence that states no figure at "
        "all — the false-RED direction of the F4 fix (#11191 review, F4)"
    )
    # ⛔ …BUT THE EARLIER-CLAUSE FIGURE GUARD IS WITHDRAWN IN THIS COMMIT, on a MEASURED
    # basis rather than a preference: it cannot separate the alibi from the truth, and
    # every spelling of it refused TRUTHS that `origin/main` accepts —
    #   "`047c91f7a` is the arming commit's parent; it returns 10 lines immediately before
    #   #9548."                                        main ACCEPT · guard REFUSE
    #   "`047c91f7a` is the arming commit; as #9601 records, the tree before #9548 reads 10
    #   lines."                                         main ACCEPT · guard REFUSE
    # so F4's spelling widening reached an arm whose problem was never the spelling. The
    # shapes below are therefore a CHARACTERISATION of today's behaviour, not a claim of
    # correctness: the class stays OPEN and documented, and the fix is the MEASURED census
    # (`_census_count_at`), not a fourth list. This overrides the F4 expectation ON PURPOSE
    # — see the PR comment; the grammar F4 introduced is kept for the no-referent arm,
    # which asks the same question about a sentence that names NO tree.
    _alibi_known_open = (
        "`047c91f7a` is the arming commit; the census returns thirteen lines immediately "
        "before #9548.",
        "`047c91f7a` is the arming commit; the census returns 3 lines immediately before "
        "#9548.",
    )
    assert all(not _refuses_before_9548(s) for s in _alibi_known_open), (
        "the earlier-clause alibi is a DOCUMENTED RESIDUAL: if it now refuses, a figure "
        "guard came back and the truthful twins (pinned just above) must be re-checked for "
        "the false RED it produced three rounds running (#11191 review r6, P1 / F4)"
    )
    _alibi_truthful = (
        "`047c91f7a` is the arming commit's parent; it returns 10 lines immediately "
        "before #9548.",
        "`047c91f7a` is the arming commit; as #9601 records, the tree before #9548 reads "
        "10 lines.",
    )
    _alibi_truthful_reds = [s for s in _alibi_truthful if _refuses_before_9548(s)]
    assert not _alibi_truthful_reds, (
        "a figure guard is refusing a TRUTHFUL reading that differs from the alibi only in "
        "its figure — `origin/main` accepts it, and reding the fix is how this pin gets "
        "deleted (#11191 review r6, P1):\n  " + "\n  ".join(_alibi_truthful_reds)
    )
    # ⛔⛔ THE FOUR r10 RESIDUALS — closed HERE, each one RE-MEASURED against the r10
    # predicate (`b0e8e2006d4`) before it was fixed, so reverting a fix reds this block.
    # The r10 review listed them as a CAP EXIT ("NOT CLEAN … not fixed"), so they are the
    # difference between this head and an honest `clean` attestation.
    #
    # 1 (P1, FAIL-OPEN) — a comma APPOTITIVE behind the predicted relation is the object's
    #   *NAME*, not an aside. It was blanked by `_INCISE` before the co-reference read, so
    #   the appositive became invisible and a `since`-rationale beside it bought the
    #   exemption: the sentence says the pre-#9548 tree is `785bb2b59` (the arming commit)
    #   and calls it the arming commit's parent in the same breath.
    # 2 (P2, FALSE RED) — the co-reference read the whole tail, so the `;`-separated
    #   two-tree comparison this file requires to stay green was refused. A `;`/`:` clause
    #   that NAMES its subject is a different tree; one that opens with the sha or a
    #   DEMONSTRATIVE is still the phrase's object — both directions are pinned, because a
    #   one-sided fix here would trade the false RED for the fail-open the `;`-blanket
    #   exemption used to be.
    # 4 (P2, FAIL-OPEN) — the adverb slot was a nine-word list, so the claim walked past it
    #   one adverb further on ("in practice", "indisputably", "effectively") and
    #   "continues to be" had no copula at all. The slot is now the productive `-ly` class
    #   plus a bounded comma incise, and `never` is deliberately excluded — it DENIES the
    #   predication.
    _r10_residuals = (
        (
            "The tree immediately before #9548 is the arming commit's parent, "
            "`785bb2b59`, since `785bb2b59` is the arming commit.",
            True,
        ),
        (
            "The tree immediately before #9548 is, in practice, the arming commit itself.",
            True,
        ),
        (
            "The tree immediately before #9548 is indisputably the arming commit itself.",
            True,
        ),
        (
            "The tree immediately before #9548 is effectively the arming commit's son.",
            True,
        ),
        (
            "The tree immediately before #9548 continues to be the arming commit itself.",
            True,
        ),
        (
            "The tree immediately before #9548 is the arming commit's parent; "
            "the #9601 merge is `ff6379eb2`.",
            False,
        ),
        (
            "The tree immediately before #9548 is the arming commit's parent; "
            "`ff6379eb2` is the parent.",
            True,
        ),
        (
            "The tree immediately before #9548 is the arming commit's parent; "
            "that is `ff6379eb2`.",
            True,
        ),
        (
            "The tree immediately before #9548 is the arming commit's parent "
            "(the arming commit is `785bb2b59`).",
            False,
        ),
        (
            "The tree immediately before #9548 is never the arming commit itself; "
            "it is `047c91f7a`.",
            False,
        ),
    )
    _r10_wrong = [
        (s, want, _refuses_before_9548(s))
        for s, want in _r10_residuals
        if _refuses_before_9548(s) is not want
    ]
    assert not _r10_wrong, (
        "the r10 cap-exit residuals are open: a comma APPOSITIVE behind the predicted "
        "relation (a false equivalence — the appositive NAMES the object), a `;`-clause "
        "with its own named subject (a false RED on the two-tree comparison), or a "
        "predicating ADVERB / `continues to be` outside a nine-word list (a false "
        "equivalence). Each was measured on `b0e8e2006d4` before it was fixed "
        "(#11191 review r10):\n  "
        + "\n  ".join(
            f"REFUSED={got} (wanted {want}) {s}" for s, want, got in _r10_wrong
        )
    )
    # ⛔⛔ THE r11 FINDINGS — the four defects the FIRST fix of the r10 residuals introduced
    # or left one spelling short. Each was measured on `ebb8f840e6` (the r11 head) before it
    # was fixed, and each is a shape `origin/main` REFUSES while that head accepted (F1, F2)
    # or a truthful denial that head refused (F3).
    #   F1/F4 — the r10 residual-2 fix tested PUNCTUATION ("is there any noun before the sha
    #     in this `;` clause") instead of the PROPOSITION, so every co-referential subject
    #     bought the admission and the equivalence came back one semicolon from the form the
    #     file already refuses — while `, and the #9601 merge is …`, which says the same true
    #     thing as the admitted `;` spelling, was still refused. One arm, verdicts keyed on a
    #     punctuation mark. The admission now requires the clause to predicate the sha of a
    #     DIFFERENT NAMED subject (a `#<n>` other than the phrase's own `#9548`).
    #   F2 — the r10 residual-4 fix bounded an OPEN class with a COUNT (`{0,2}`), so the next
    #     stack past two was open again; and an `-ly` adverb behind an intensifier (`almost
    #     certainly`) was unreachable. The slot is a repetition, and the intensifier is a
    #     closed class named on purpose.
    #   F3 — the same widening swallowed the NEGATIVE-POLARITY `-ly` adverbs, so a denial was
    #     read as the claim and REFUSED (a regression against the previous head).
    _r11_findings = (
        (
            "The tree immediately before #9548 is the arming commit's parent; "
            "the tree is `ff6379eb2`.",
            True,
        ),
        (
            "The tree immediately before #9548 is the arming commit's parent; "
            "its parent is `ff6379eb2`.",
            True,
        ),
        (
            "The tree immediately before #9548 is the arming commit's parent; "
            "the merge is `ff6379eb2`.",
            True,
        ),
        (
            "The tree immediately before #9548 is the arming commit's parent, "
            "and the #9601 merge is `ff6379eb2`.",
            False,
        ),
        (
            "The tree immediately before #9548 is the arming commit's parent; "
            "`ff6379eb2` is the #9601 merge.",
            False,
        ),
        (
            "The tree immediately before #9548 is now quite clearly the arming commit itself.",
            True,
        ),
        (
            "The tree immediately before #9548 is almost certainly still the arming "
            "commit itself.",
            True,
        ),
        (
            "The tree immediately before #9548 is really quite clearly the arming commit "
            "itself.",
            True,
        ),
        # ⛔ THREE ITEMS, not two: the count `{0,2}` that the first widening used let the
        # two-item controls above pass while the three-item stack was open again, so the
        # discrimination this block claims is only real with a stack ONE PAST the count.
        (
            "The tree immediately before #9548 is really quite clearly still the arming "
            "commit itself.",
            True,
        ),
        (
            "The tree immediately before #9548 is indisputably, in practice, the arming "
            "commit itself.",
            True,
        ),
        (
            "The tree immediately before #9548 is hardly the arming commit itself.",
            False,
        ),
        (
            "The tree immediately before #9548 is scarcely the arming commit itself.",
            False,
        ),
        (
            "The tree immediately before #9548 is barely the arming commit itself.",
            False,
        ),
    )
    _r11_wrong = [
        (s, want, _refuses_before_9548(s))
        for s, want in _r11_findings
        if _refuses_before_9548(s) is not want
    ]
    assert not _r11_wrong, (
        "the r11 findings are open: a CO-REFERENTIAL subject in a `;`/`and` clause buys the "
        "named-subject admission (F1/F4, a false equivalence `origin/main` refuses), an "
        "adverb STACK past the count or behind an intensifier is unread (F2), or a "
        "NEGATIVE-POLARITY `-ly` adverb is read as the claim instead of as its denial (F3). "
        "Each was measured on `ebb8f840e6` before it was fixed (#11191 review r11):\n  "
        + "\n  ".join(
            f"REFUSED={got} (wanted {want}) {s}" for s, want, got in _r11_wrong
        )
    )
    # ⛔⛔ CONTROLS FOR THE r11 FAIL-OPENS (#11191 review r11, P1 — every string below was
    # run through THIS branch's own PRE-fix predicate, extracted with `ast` and exec'd out
    # of process, and produced the verdict the comment names there). Same rule as every
    # block above: a fix whose revert leaves the suite green has no test. Both directions
    # are pinned wherever one arm decides OPPOSITE propositions from the same surface form.
    #
    # (1) THE COMMA SPLICE — the pin disabled by the cell's own correction. The predicate
    # decides each phrase OCCURRENCE on its own, but a plain comma is deliberately not a
    # clause boundary, so a denial was handed the whole clause and exempted a second,
    # spliced occurrence beside it. PRE-FIX both claim splices were ACCEPTED.
    _comma_splice = (
        (
            "It was NOT the tree immediately before #9548, `ff6379eb2` is the tree before "
            "#9548.",
            True,
        ),
        (
            "`047c91f7a` is the arming commit's parent and NOT the tree immediately "
            "before #9548, `ff6379eb2` is the tree before #9548.",
            True,
        ),
        # …and the SAME splice of the TRUE tree — must stay GREEN, so the control measures
        # the CLAIM and not the comma.
        (
            "It was NOT the tree immediately before #9548, `047c91f7a` is the tree before "
            "#9548.",
            False,
        ),
    )
    _splice_wrong = [
        (s, want, _refuses_before_9548(s))
        for s, want in _comma_splice
        if _refuses_before_9548(s) is not want
    ]
    assert not _splice_wrong, (
        "a false equivalence COMMA-SPLICED beside a denial passes — the denial arms are "
        "handed the whole clause, so the artefact's own `NOT the tree immediately before "
        "#9548` exempts a second occurrence after a comma. The contract's first rule is "
        "that EVERY occurrence is decided on its own (#11191 review r11, P1):\n  "
        + "\n  ".join(
            f"REFUSED={got} (wanted {want}) {s}" for s, want, got in _splice_wrong
        )
    )
    # …and it must land on the artefact this pin exists to guard: spliced into the row's
    # OWN sentence, the injection has to red, or the controls above prove something about
    # a string and nothing about §A8.
    _spliced_row = row.replace(
        "the arming commit's parent)",
        "the arming commit's parent), `ff6379eb2` is the tree before #9548",
        1,
    )
    assert _spliced_row != row, (
        "the comma-splice control no longer injects into §A8's evidence cell, so it proves "
        "nothing (#11191 review r11, P1)"
    )
    assert any(
        _refuses_before_9548(sentence)
        for sentence in re.split(r"(?<=\.)\s+", _spliced_row)
    ), (
        "a false equivalence spliced onto §A8's own `NOT the tree immediately before "
        "#9548` passes ON THE REAL ROW — the pin is disabled on the cell it exists to guard "
        "by the cell's own correction (#11191 review r11, P1)"
    )
    #
    # (2) THE MIRROR PREDICATION. The object arm reads only AFTER the phrase, so the same
    # claim written the other way round named no tree, dated no figure and passed the
    # default-deny digit test. PRE-FIX all six were ACCEPTED.
    _subject_predications = (
        "The arming commit is the tree immediately before #9548.",
        "The arming commit itself is the tree immediately before #9548.",
        "The arming commit was the tree immediately before #9548.",
        "The arming commit must be the tree immediately before #9548.",
        "The arming commit's son is the tree immediately before #9548.",
        "The arming commit is the tree before #9548.",
    )
    _subject_passed = [
        s for s in _subject_predications if not _refuses_before_9548(s)
    ]
    assert not _subject_passed, (
        "a tree-shaped SUBJECT predicated of the phrase is accepted — `_PREDICATED_OBJECT` "
        "reads only the text after the phrase, so the A1 defect spelled without a sha (and "
        "without a figure) reaches step 5's digit test and passes (#11191 review r11, "
        "P1):\n  " + "\n  ".join(_subject_passed)
    )
    # …and the mirror arm must not red the TRUE tree in the same position: the parent
    # relation is the doc's own spelling of it.
    assert not _refuses_before_9548(
        "The arming commit's parent is the tree immediately before #9548."
    ), (
        "the mirror arm refuses the doc's own TRUE cell written subject-first — a false RED "
        "in the arm that closes the fail-open (#11191 review r11, P1)"
    )
    #
    # (3) THE MODAL VERB. The verb alternation carried the copula and its stative cousins
    # but not `has to be` / `must be`, so the object arm never reached the object.
    # PRE-FIX ACCEPTED.
    assert _refuses_before_9548(
        "The tree immediately before #9548 has to be the arming commit."
    ), (
        "a MODAL spelling of the copula defeats the predication arm — `has to be` / "
        "`must be` / `would be` were outside the verb alternation, so the same claim was "
        "refused with `is` and accepted with `has to be` (#11191 review r11, P1)"
    )


def test_the_census_git_guard_fails_closed_and_never_reds_a_shallow_checkout(tmp_path):
    """#11064 review (HIGH): the executed census must not read a git failure as zero lines.

    Two defects, both MEASURED here on a throwaway repo — no network, no DB, no docker:

    1. FAIL-OPEN — `git grep` at an object this checkout does not carry exits **128** with
       EMPTY stdout. The pre-fix block summed stdout and got `0`, so a note that ever states
       `0` would be satisfied by a command that never ran: the `#4309` shape this block exists
       to close, reproduced inside its own pin.
    2. FALSE RED — that same empty stdout was compared against a real count, so on python-ci's
       depth-1 `test` job (its bare `actions/checkout@v4` — `.github/workflows/python-ci.yml:968`,
       inside the `test:` job header at `:680`) this pin failed on a CORRECT tree on every PR. The
       depth-1 clone below reproduces the shape: the old tree is absent, and `_census_deepen_to` reaches it
       with a bounded fetch instead of reddening — while a FULL clone that lacks the rev is
       never shallow-ified, because `tests/test_9397_about_edge_properties.py::_ensure_commit`
       measured that `git fetch --depth=N` truncates a developer's history.

    Non-vacuity: on the pre-fix tree none of these helpers exists (the logic was inline and
    unguarded), so this test reds at its first call — the defect, not the fixture. The two
    `pytest.raises` arms are the ones the pre-fix code could not produce at all.
    """
    # ⛔ THE "FULL clone" FIXTURE IS A REAL CLONE, not a `git init` (review at
    # `fb79abca4`, Finding 2, P2). With no `origin`, a `git fetch --depth=1 origin …`
    # exits **128** instead of truncating, so the "never shallow-ify a full clone"
    # arm at the end of this test stayed GREEN with the shallow gate DELETED — an
    # inert arm under the exact mutation its comment documents. Cloning from a local
    # seed gives `origin` a real target, so dropping that gate performs the fetch and
    # really flips `--is-shallow-repository` to `true`, reddening the arm.
    seed = tmp_path / "census-seed"
    repo = tmp_path / "census-repo"
    runbook = seed / "docs" / "infra-runbook.md"
    runbook.parent.mkdir(parents=True)
    # A path in the census set that selects NOTHING (the legitimate exit-1 reading), committed
    # so the "git grep exits 1 when it selects nothing" arm is measured on a real path.
    (seed / "fly.toml").write_text('[app]\nname = "fixture"\n', encoding="utf-8")
    assert _census_git(seed, "init", "-q", "-b", "main").returncode == 0

    def commit(n_lines: int, message: str) -> str:
        runbook.write_text(
            "".join(f"export TORTOISE_RECORD_DSN=line{i}\n" for i in range(n_lines)),
            encoding="utf-8",
        )
        for args in (
            ("add", "-A"),
            (
                "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                "commit", "-q", "-m", message,
            ),
        ):
            done = _census_git(seed, *args)
            assert done.returncode == 0, f"fixture git {args} failed: {done.stderr}"
        head = _census_git(seed, "rev-parse", "HEAD")
        assert head.returncode == 0, head.stderr
        return head.stdout.strip()

    census_paths = ["docs/infra-runbook.md"]
    old_sha = commit(2, "two census lines")
    commit(5, "five census lines")
    # … and `repo` is that seed's FULL clone, so `origin` RESOLVES (the Finding-2 fix).
    full_clone = _census_git(
        tmp_path, "clone", "-q", f"file://{seed.resolve()}", str(repo)
    )
    assert full_clone.returncode == 0, full_clone.stderr
    assert _census_git(repo, "remote", "get-url", "origin").returncode == 0, (
        "the FULL-clone fixture has no `origin` — the never-shallow-ify arm below is "
        "then inert, because a `git fetch --depth=1 origin …` with no remote exits 128 "
        "instead of truncating (review at `fb79abca4`, Finding 2)"
    )

    # The reachability predicate the old block never asked.
    assert _census_commit_available(repo, old_sha)
    assert not _census_commit_available(repo, "0" * 40)
    # A legitimate "selects nothing" (exit 1) is a measurement of ZERO, not a failure...
    assert _census_count_at(repo, old_sha, ["fly.toml"]) == 0
    # ...while an object the checkout does not have is a FAILURE, never a zero.
    with pytest.raises(AssertionError) as excinfo:
        _census_count_at(repo, "0" * 40, census_paths)
    assert "failed" in str(excinfo.value) and "NOT a census of zero" in str(excinfo.value)
    # The real counts, at the tree each was measured at.
    assert _census_count_at(repo, old_sha, census_paths) == 2
    assert _census_count_at(repo, "HEAD", census_paths) == 5

    # ── the CI shape: a depth-1 checkout ────────────────────────────────────
    shallow = tmp_path / "shallow"
    clone = _census_git(
        tmp_path, "clone", "-q", "--depth", "1", f"file://{repo.resolve()}", str(shallow)
    )
    assert clone.returncode == 0, clone.stderr
    assert _census_is_shallow(shallow)
    assert not _census_commit_available(shallow, old_sha), (
        "the depth-1 fixture must not carry the old tree — otherwise it does not reproduce "
        "the environment the HIGH finding was measured in"
    )
    # The PRE-FIX reading of that state: `git grep` fails, stdout is empty, and the sum is 0.
    with pytest.raises(AssertionError):
        _census_count_at(shallow, old_sha, census_paths)
    # The guard's answer is to DEEPEN (bounded), not to red: measured, not asserted.
    assert _census_deepen_to(shallow, old_sha, depths=(2, 4, 8))
    assert _census_count_at(shallow, old_sha, census_paths) == 2

    # ── and the deepen arm must never touch a FULL clone ────────────────────
    # A bogus rev on a full clone returns False WITHOUT fetching: if the shallow gate were
    # dropped, `git fetch --depth=1` would truncate this repo's history and
    # `--is-shallow-repository` would flip to true — the destructive side effect
    # `_ensure_commit` documents. `repo` is a CLONE (not a `git init`) so `origin`
    # resolves and this arm actually reds under that mutation (review at
    # `fb79abca4`, Finding 2: with no remote the fetch exited 128 and the arm could
    # not fail).
    assert not _census_deepen_to(repo, "0" * 40, depths=(1,))
    assert not _census_is_shallow(repo), (
        "the deepen arm shallow-ified a FULL clone — that truncates a developer's history"
    )


def test_the_census_migration_denominator_agrees_with_the_docs_own_head_count():
    """#9585 review P2: `over all 70 migrations` contradicted the same document's 71.

    The §A5 pgvector census says the grep "over all N migrations" returns 0, so N is
    the DENOMINATOR of a measurement. When main landed a migration it became 71 while
    the note still said 70 — and §A7 of this same document already said 71, so the
    document contradicted itself and the number that was read depended on which line
    the reader hit.

    Asserted as SELF-CONSISTENCY on purpose: a pin against the tree's live count would
    red every unrelated PR that adds a migration and does not touch this epic's record,
    which is friction, not correctness. What must not happen is the two current-day
    figures in the document disagreeing.

    Falsified on the pre-fix text: §A5 says 70, §A7 says 71.
    """
    text = _EPIC_DOC.read_text(encoding="utf-8")
    a5 = re.search(r"`grep -riE 'pgvector\|vector\\\('` over all\s+(\d+)\s+migrations", text)
    assert a5, (
        "the epic's §A5 pgvector census no longer states a migration denominator — "
        "the measurement then has no scope that can be checked"
    )
    at_head = re.findall(r"at this head, (\d+) migrations", text)
    assert at_head, (
        "the epic states no `at this head, N migrations` figure, so there is no "
        "second, dated denominator for §A5 to agree with"
    )
    for other in at_head:
        assert a5.group(1) == other, (
            f"the epic contradicts itself about the migration count: §A5's pgvector "
            f"census is scoped to {a5.group(1)} migrations while the same document "
            f"states {other} at this head — the pgvector denominator is then stale "
            "(#9600's R6 migration is the 71st; #9585 review P2)"
        )


def _census_block() -> str:
    """The epic doc's `Census commands` block, verbatim (through its fence)."""
    text = _EPIC_DOC.read_text(encoding="utf-8")
    assert "Census commands" in text, (
        "docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md no "
        "longer carries the census block — the provenance record is gone"
    )
    return text.split("Census commands", 1)[1].split("```text", 1)[1].split("```", 1)[0]


def test_the_census_freshness_claim_is_scoped_to_the_lines_it_actually_re_measured():
    """#9585 review P2: a "re-measured and unchanged" claim must not cover a line
    the same document corrects — and every corrected line must carry its ⚠️.

    The epic's note said *"The other six lines were re-measured 2026-10-23 and are
    unchanged"* while, two paragraphs below, the same document states the
    `narrative|raw_turn|raw_text|transcript` line is **no longer 0** — and since
    #9554 landed the tenant GUC the `app.current_org_id` line is no longer 0
    either. That sentence is the one a later lane trusts before re-running the
    census, so an unscoped "unchanged" over a line that moved is the stale-flag
    class, not wording: it re-certifies the pre-#9555 / pre-R6 zeros.

    Falsified on the pre-fix text: it carries the unscoped sentence, and the
    `app.current_org_id` line carries no marker at all.
    """
    block = _census_block()
    text = _EPIC_DOC.read_text(encoding="utf-8")
    # 1. No UNIVERSAL freshness claim. Both spellings measured in review — the
    # note above the fence and the one under the #9555 correction — said "the
    # other six", which is exactly the set that contains a corrected line.
    universal = re.findall(
        r"The other six (?:lines|commands)[^.]*?(?:unchanged|exactly as stated)[^.]*\.", text
    )
    assert not universal, (
        "the census note claims EVERY other line was re-measured and is unchanged, "
        "while two of them have moved (the `narrative|…` grep since #9555, the "
        "`app.current_org_id` setter count since #9554):\n  "
        + "\n  ".join(universal)
    )
    # 2. The scoped replacement exists and names BOTH exceptions, so the reader
    # is not left to discover them 40 lines apart.
    scoped = re.search(
        r"The remaining census\s+lines were re-measured at this head(.*?)(?:\n\n|$)",
        text,
        re.S,
    )
    assert scoped, (
        "the note no longer states which lines were re-measured at this head — the "
        "scoping is the fix, and deleting it silently restores the universal claim"
    )
    for issue in ("#9555", "#9554"):
        assert issue in scoped.group(1), (
            f"the scoped freshness sentence does not name {issue} as an exception, so "
            "a later lane reads the block's pre-correction count as live"
        )
    # 3. Each line that moved carries its own marker IN the block: a correction
    # that lives only in prose below is invisible to whoever copies the command.
    for needle in ("narrative|raw_turn", "app.current_org_id"):
        line = [ln for ln in block.splitlines() if needle in ln]
        assert len(line) == 1, f"the census block has {len(line)} lines for {needle!r}"
        assert "⚠️" in line[0], (
            f"the census line for {needle!r} carries no ⚠️ marker although it no "
            "longer returns the count beside it — the block reads as a current "
            "measurement"
        )
    # 4. The markers are TRUE of this tree (so the ⚠️ is not an invented
    # exception): the GUC the note blames really is shipped, and the narrative
    # migration the #9555 note blames really exists.
    projection = (_REPO_ROOT / "tortoise" / "record_projection.py").read_text(encoding="utf-8")
    assert "TENANT_GUC = \"app.current_org_id\"" in projection, (
        "the note attributes the census delta to a shipped `app.current_org_id` "
        "setter that `tortoise/record_projection.py` does not carry"
    )
    assert "set_config('app.current_org_id'" in projection, (
        "the note names a transaction-local `set_config('app.current_org_id', …)` "
        "that the record projection does not install"
    )
    assert (
        _REPO_ROOT / "supabase" / "migrations"
        / "20261023000001_record_session_narrative.sql"
    ).is_file(), (
        "the #9555 note blames a narrative migration that is not in the tree — the "
        "count it corrects has no referent"
    )
    # 5. The `app.current_org_id` count is MEASURED here, not merely read (#9585
    # review P2, round 9): the note stated "2 lines" that reproduce at `ff6379eb2`
    # but not at the head it shipped on (a later merge added three
    # `record_backup.py` mentions and moved the cited offsets), i.e. the exact
    # stale-count class the note narrates twice. Pinning the tree is half the fix;
    # re-deriving the live count is the half that cannot re-stale, because main
    # moving under the number now reddens THIS test instead of the document.
    live = [
        f"{path.relative_to(_REPO_ROOT)}:{lineno}"
        for base in ("tortoise", "tools")
        for path in sorted((_REPO_ROOT / base).rglob("*.py"))
        for lineno, text_line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        )
        if "app.current_org_id" in text_line
    ]
    assert f"**{len(live)} lines at this head**" in text, (
        "the census note's `app.current_org_id` count is not the "
        f"{len(live)} this tree returns ({', '.join(live)}): a count with no "
        "tree pin re-stales on the next main-side edit — the failure this note "
        "exists to close"
    )
    assert "**2 lines at `ff6379eb2`**" in text, (
        "the note dropped the sha its '2 lines' was measured at — a count with no "
        "tree is the stale-flag class, not a correction of it"
    )


def test_the_record_arming_append_is_guarded_not_merely_present():
    """#9548 review P2: the arming append's GUARD is asserted, not just its presence.

    `_array_pairs` runs its regexes over the whole `run:` text with no awareness
    of the enclosing shell test, so `[ -n "$X" ] && ARGS+=(X="$X")` and a bare
    `ARGS+=(X="$X")` produce the SAME mapping. The sibling test's membership
    assertion therefore pinned presence, not guardedness.

    This is the discrimination proof for the guard assertion: the shipped text
    satisfies it, and the SAME text with the guard stripped does not — while the
    guard-blind membership reading is identical for both. (The global
    `test_optional_fly_appends_are_n_guarded` covers this name too; it is pinned
    here as well because that test's per-line scan exempts anything it cannot
    classify, so a change there must not silently drop this one.)
    """
    run = _steps()[_SET_STEP]["run"]
    guard_prefix = f'[ -n "${_RECORD_DSN_ENV}" ] && '
    guarded = _guarded_append(_RECORD_DSN_ENV)
    assert guard_prefix in run, (
        f"the payload step carries no `{guard_prefix.strip()}` guard at all"
    )
    assert guarded in run, f"the guarded append {guarded!r} is not in the payload step"

    unguarded = run.replace(guard_prefix, "", 1)
    assert unguarded != run, "the guard this test pins was not stripped — the probe is vacuous"
    assert guarded not in unguarded, "the stripped text still carries the guarded line"
    # ... and the guard-blind reader cannot tell the two apart, which is exactly
    # why the assertion above has to read the TEXT.
    assert _array_pairs(unguarded).get(_RECORD_DSN_ENV) == _RECORD_DSN_ENV, (
        "the guard-stripped step no longer maps the name — then `_array_pairs` "
        "would have caught an unguarded append and this test would be redundant"
    )


def test_the_record_is_not_armed_single_tenant_on_the_hosted_app():
    """#9548 R1: `TORTOISE_RECORD_SINGLE_TENANT` and the env tenant pair stay UNSET.

    The hosted lane is MULTI-tenant and carries its tenant **per request**, which
    always wins (`record_writer.record_tenant`). The `_ORG_ID`/`_GRAPH_ID` pair is
    PROCESS-GLOBAL, so on this app it is not a weaker answer than the request
    scope — it is a WRONG one: with the declaration absent the pair is refused
    (`no_tenant`, counted), and with it present every tenant's rows are filed
    under one graph (#9192) — a well-formed row under the wrong organisation,
    which nothing downstream can detect. So the arming act must not grow a
    "make it work" declaration.

    Asserted on ASSIGNMENTS, never on mentions: the runbook and `fly.toml`'s
    `[env]` comment both NAME these variables precisely to forbid setting them,
    and a prose-mention test would force that warning out of the tree.
    """
    # The gate's OWN parsers, so this test and the deploy cannot disagree about
    # what counts as an assignment (a comment does not).
    gate = _drift_gate()
    env_keys = gate.fly_toml_env_keys((_REPO_ROOT / "fly.toml").read_text(encoding="utf-8"))

    declared = _manifest_declarations()
    argv_keys = _array_pairs(_steps()[_SET_STEP]["run"])
    env_bound = {
        name
        for step in _steps().values()
        for name in _env_bindings(step)
    }
    for name in (
        "TORTOISE_RECORD_SINGLE_TENANT",
        "TORTOISE_RECORD_ORG_ID",
        "TORTOISE_RECORD_GRAPH_ID",
    ):
        assert name not in env_keys, f"{name} is an assigned key in fly.toml [env]"
        assert name not in argv_keys, f"the deploy stages {name} to Fly"
        assert name not in env_bound, f"the deploy binds {name} from a GitHub secret"
        assert name not in declared, f"{name} is declared in fly-managed-secrets.txt"


def test_the_readiness_request_bound_clears_the_servers_own_bound() -> None:
    """The gate's per-REQUEST bound must sit ABOVE the bound the SERVER enforces.

    `deploy-health-gate.sh` bounds each request because a `N tries × S sleep`
    phase budget is only a bound if the request is one — but the number has to be
    derived from the endpoint's own server-side bound, not chosen. `/health/ready`
    runs BOTH plane coordinators SEQUENTIALLY in Supabase mode
    (`hosted_api.health_ready`): `_READY_PROBE`, bounded at
    ``DB_PROBE_HARD_TIMEOUT`` (which ALIASES ``monitoring.PROBE_HARD_TIMEOUT`` =
    3.1 + 2.0 + 0.5 = 5.6 s — NOT the 1.5 s inner ``PROBE_TIMEOUT``), then
    `_CONTROL_PLANE_PROBE`, bounded at ``CONTROL_PLANE_HARD_TIMEOUT`` (5.0 phases
    + 1.0 margin = 6.0 s).

    A client bound below that sum is the defect #10155's review found live: the
    gate shipped a shared 5 s knob, so a healthy-but-slow release answering
    between 5 s and 11.6 s was cut off, curl printed `000`, and phase 3 reported
    it as "no response from the app" — the indistinguishable-failure class the
    gate exists to avoid. `_READY_PROBE` is `fresh_only=True`, so a completed
    result is served only while it is younger than its own read budget
    (``monitoring.HealthProbe._view_locked``: ``max_age = self._timeout``), and
    the polls are ``READY_SLEEP`` (10 s) apart — older than that budget — so
    EVERY poll re-probes and a sustained slow data plane fails the whole phase.
    """
    gate = (_REPO_ROOT / ".github" / "scripts" / "deploy-health-gate.sh").read_text(
        encoding="utf-8")
    # The READINESS knob specifically — a single shared knob cannot satisfy this
    # and the `/health` snapshot bound (5 s) at the same time, which is why the
    # pin reads the ready knob by NAME.
    match = re.search(r'CURL_MAX_TIME_READY="\$\{CURL_MAX_TIME_READY:-(\d+)\}"', gate)
    assert match, (
        "the readiness request bound is no longer a numeric default of "
        "CURL_MAX_TIME_READY in deploy-health-gate.sh — the derivation this test "
        "pins has nothing to read"
    )
    shipped = int(match.group(1))

    import tortoise.hosted_api as hosted_api
    import tortoise.monitoring as monitoring

    # Guard the derivation itself first: if the server's hard bound ever stops
    # ALIASING the coordinator constant, `DB_PROBE_HARD_TIMEOUT` silently becomes
    # a restated literal and the sum below would measure the wrong thing.
    assert hosted_api.DB_PROBE_HARD_TIMEOUT == monitoring.PROBE_HARD_TIMEOUT, (
        "DB_PROBE_HARD_TIMEOUT no longer aliases monitoring.PROBE_HARD_TIMEOUT — "
        "re-derive CURL_MAX_TIME_READY against whatever now bounds the data plane"
    )
    server_worst = (hosted_api.DB_PROBE_HARD_TIMEOUT
                    + hosted_api.CONTROL_PLANE_HARD_TIMEOUT)
    assert server_worst > 5.0, (
        f"the server's own readiness worst case reads {server_worst}s; this test "
        "assumes it exceeds the /health snapshot bound, which is why one knob "
        "cannot serve both endpoints"
    )
    assert shipped > server_worst, (
        f"CURL_MAX_TIME_READY is {shipped}s but /health/ready's server-side worst "
        f"case is {server_worst}s (DB_PROBE_HARD_TIMEOUT "
        f"{hosted_api.DB_PROBE_HARD_TIMEOUT} + CONTROL_PLANE_HARD_TIMEOUT "
        f"{hosted_api.CONTROL_PLANE_HARD_TIMEOUT}) — a client bound below it reds "
        "a healthy-but-slow release and reports it as `000 = no response`"
    )


def _gate_knob(gate: str, name: str) -> int:
    """The SHIPPED default of a `NAME="${NAME:-<int>}"` knob in the gate."""
    match = re.search(rf'{name}="\$\{{{name}:-(\d+)\}}"', gate)
    assert match, f"{name} is not a numeric default in deploy-health-gate.sh"
    return int(match.group(1))


def test_the_deploy_retry_loop_fits_under_the_cap_that_kills_it() -> None:
    """`deploy-api`'s cap is derived ABOVE its retry loop, and nothing pinned it.

    The loop is the reason that cap is hand-derived rather than a round multiple
    of a measured duration: ``5 x (attempt bound + kill grace) + 4 x retry
    sleep`` is a worst case nobody re-derives by eye. The health gate has a pin
    of exactly this shape (`test_the_health_gate_budget_stays_under_the_cap_that
    _kills_it`); this job had none, so `timeout -k 30s 11m` could be edited to
    `15m` — making the loop's worst case 80.5 min against a 65-minute cap — with
    every test still green.

    Why it matters more here than anywhere else: a `timeout-minutes` kill ends
    the job BEFORE `if: failure()` is evaluated, so the #2240 out-of-band alert
    never runs and a LIVE, unhealthy release reports only as a red run. If the cap
    is what kills attempt 5, the per-attempt bound — the whole point of the loop —
    is no longer the bound, and the alert goes with it.

    Read off the SHIPPED workflow rather than restated: the attempt count, the
    per-attempt `timeout -k <grace> <bound>`, the retry sleep, and the cap.
    """
    raw = _WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(raw)

    loop = re.search(
        r"for attempt in ([0-9 ]+); do\n.*?timeout -k ([0-9]+)s ([0-9]+)m .*?\n"
        r".*?sleep ([0-9]+)\n",
        raw,
        re.S,
    )
    assert loop, (
        "the deploy-api retry loop is no longer a `for attempt in …; do` block with "
        "`timeout -k <grace>s <bound>m` followed by a retry `sleep <n>` — the "
        "arithmetic this pin recomputes has nothing to read. Restate the derivation "
        "here if the loop is reshaped."
    )
    attempts = len(loop.group(1).split())
    grace_min = int(loop.group(2)) / 60
    bound_min = int(loop.group(3))
    sleep_min = int(loop.group(4)) / 60

    # No sleep is charged after the LAST attempt — the loop exits immediately, so
    # only `attempts - 1` retries are paid for.
    worst = attempts * (bound_min + grace_min) + (attempts - 1) * sleep_min

    cap = workflow["jobs"]["deploy-api"].get("timeout-minutes")
    assert isinstance(cap, int), (
        f"deploy-api has no integer timeout-minutes ({cap!r}) — the retry loop's "
        "worst case would be bounded by GitHub's 360-minute default, and a kill at "
        "that point still skips if: failure() and silences the #2240 alert"
    )
    assert worst < cap, (
        f"the deploy retry loop's worst case is {worst:g} min "
        f"({attempts} x ({bound_min:g} + {grace_min:g}) + {attempts - 1} x "
        f"{sleep_min:g}) but deploy-api is capped at {cap} min — the CAP would be "
        "what kills the last attempt, so the per-attempt bound stops being the "
        "bound and the `if: failure()` alert never runs. Either tighten the attempt "
        "bound or raise the cap in the same commit."
    )
    # The other direction is a defect too: a cap that only BARELY clears the loop
    # leaves nothing for the job's other steps, and one of those raises the alert.
    assert worst + 0.5 < cap, (
        f"deploy-api's {cap}-minute cap leaves {cap - worst:g} min over the retry "
        "loop's worst case for the job's other thirteen steps (one of which raises "
        "the #2240 alert) — the headroom is the point, not padding"
    )


def test_the_health_gate_budget_stays_under_the_cap_that_kills_it() -> None:
    """The gate's worst case is DERIVED from its knobs and must fit the job cap.

    `post-deploy-verify`'s `timeout-minutes` is the only thing that can end a
    hung gate, and a `timeout-minutes` kill ends the job BEFORE `if: failure()`
    is evaluated — so the #2240 out-of-band alert never runs and a LIVE,
    unhealthy release reports as a red run with a truncated log. That makes the
    relationship `gate worst case < cap` load-bearing, and it was PROSE ONLY:
    the budget appears in `deploy-health-gate.sh`'s comment and in the job's
    comment, and nothing recomputed it from the knobs. Raising `READY_TRIES`
    (or adding a phase) would silently make the cap the bound again — the exact
    failure the per-request bounds were added to remove.

    The harness cannot catch this: it overrides `APP_PROBES` / `DB_TRIES` /
    `READY_TRIES` / the sleeps per case, so it never sees the shipped defaults.
    """
    gate_path = _REPO_ROOT / ".github" / "scripts" / "deploy-health-gate.sh"
    gate = gate_path.read_text(encoding="utf-8")

    worst = (
        _gate_knob(gate, "APP_PROBES") * (
            _gate_knob(gate, "CURL_MAX_TIME_APP") + _gate_knob(gate, "APP_PROBE_SLEEP"))
        + _gate_knob(gate, "DB_TRIES") * (
            _gate_knob(gate, "CURL_MAX_TIME_HEALTH") + _gate_knob(gate, "DB_SLEEP"))
        + _gate_knob(gate, "READY_TRIES") * (
            _gate_knob(gate, "CURL_MAX_TIME_READY") + _gate_knob(gate, "READY_SLEEP"))
    )

    job = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["post-deploy-verify"]
    cap_minutes = job.get("timeout-minutes")
    assert isinstance(cap_minutes, int), (
        f"post-deploy-verify has no integer timeout-minutes ({cap_minutes!r}) — the "
        "gate's worst case would be bounded by GitHub's 360-minute default, and a "
        "kill at that point still skips if: failure()"
    )
    assert worst < cap_minutes * 60, (
        f"the gate's worst case is {worst} s ({worst / 60:.2f} min) but "
        f"post-deploy-verify is capped at {cap_minutes} min — the CAP would be the "
        "thing that ends a hung probe, and a timeout-minutes kill skips the #2240 "
        "alert. Either bound the gate tighter or raise the cap in the same commit."
    )


def test_the_health_gate_header_states_the_budgets_its_knobs_derive() -> None:
    """The gate's BEHAVIOUR header must state the windows its knobs DERIVE.

    The header said "poll for up to 3 min" for BOTH phase 2 and phase 3. That
    was true while a phase's window was ``tries x sleep`` (``18 x 10 s = 180 s``)
    and the per-request bounds added alongside this pin made it false: the
    window is ``tries x (sleep + request bound)``, i.e. 270 s (4.5 min) and
    414 s (6.9 min). The file then stated a poll window and its own refutation
    ~100 lines apart, and the header understated phase 3 by 2.3x — the same
    "a diagnosis that understates its own wait" defect the runtime messages
    were fixed for, in the copy a maintainer reads when deciding whether a
    phase budget is too tight.

    Nothing pinned it, so it could be reverted green, and any knob change
    re-opened it silently. This reads the SHIPPED defaults, recomputes each
    window, and requires the header to state that window.
    """
    gate_path = _REPO_ROOT / ".github" / "scripts" / "deploy-health-gate.sh"
    gate = gate_path.read_text(encoding="utf-8")

    header = gate[gate.index("# BEHAVIOUR"):gate.index("# CONTRACT (#4771)")]
    # `BEHAVIOUR` numbers its phases `#   1. …`, `#   2. …`, `#   3. …`; the split
    # keeps each phase's (possibly wrapped) prose in its own segment.
    phases = re.split(r"\n#   \d\. ", header)
    assert len(phases) == 4, f"expected 3 numbered phases in the header, got {phases}"

    windows = [
        _gate_knob(gate, "APP_PROBES") * (
            _gate_knob(gate, "APP_PROBE_SLEEP") + _gate_knob(gate, "CURL_MAX_TIME_APP")),
        _gate_knob(gate, "DB_TRIES") * (
            _gate_knob(gate, "DB_SLEEP") + _gate_knob(gate, "CURL_MAX_TIME_HEALTH")),
        _gate_knob(gate, "READY_TRIES") * (
            _gate_knob(gate, "READY_SLEEP") + _gate_knob(gate, "CURL_MAX_TIME_READY")),
    ]

    # `strict=True` guards the WINDOW side, which nothing else does: the `len(phases) == 4` assert
    # above already reds a header that gains a numbered phase (it fires before the zip runs), but a
    # fourth entry added to `windows` — a new phase's window written while its header bullet was
    # forgotten — would make a plain `zip` stop at the three phases it has and leave that window
    # unchecked, silently. With `strict=True` the two SIDES cannot be edited apart. ⛔ It does not
    # seal the pairing: an extra phase written WITHOUT `#   N. ` numbering is not split out (it stays
    # inside the previous segment), so it slips both zip forms and the assert — a pre-existing gap
    # in this pin's shape, not closed here.
    for segment, seconds in zip(phases[1:], windows, strict=True):
        stated = f"up to {seconds} s ({seconds / 60:.1f} min)"
        assert stated in segment, (
            f"the gate's BEHAVIOUR header does not state {stated!r} — the window "
            "its own knobs derive. The header had to be corrected by hand once "
            "(it claimed 3 min for a 270 s and a 414 s phase); restate the derived "
            f"window, or change the knob and the header together. Header segment:\n{segment}"
        )
        # The stale claim, in the spelling that was there, must not return.
        assert "3 min" not in segment and "3-minute" not in segment, (
            "the gate's BEHAVIOUR header still says 3 min for a phase whose "
            "derived window is not 180 s"
        )


def test_the_runbook_field_row_documents_the_positive_pair():
    """#9548: the operator's FIELD ROWS must name the fields `metrics()` publishes.

    ⛔ Registered on `tier1` — that is the whole point. `docs/infra-runbook.md` is under
    `docs/`, which is in `NON_PYTHON_PREFIXES` and matches no `SOURCE_PATTERNS` entry, so
    a runbook-only diff selects NO surface and runs `tier1` only (#4309). The assertion
    that first tried to guard this was a whole-file `name in runbook` membership test in
    `tests/test_9548_record_arming_health.py` — and it failed on BOTH counts, each measured:
    that suite is not selected on a docs-only diff, and the membership form stays green
    under the rename anyway (the row's own explanation still names both fields further
    along it). So this pin names the row's HEAD CELL, in the suite the diff actually runs.

    Scoped to the section's TABLE ROWS — and to each row's **HEAD CELL**, not its prose — so it
    pins the operator's row without pinning its punctuation: a head cell that annotates the types
    (`record_writes_applied` (int) / …) or a pair split across two rows is a better doc and still
    passes, while a rename (or a deletion) reds. ⚠️ Head-cell scope is not fussiness: a same-row
    substring test was measured GREEN on a rename of the pair in the head cell, because the row's
    own explanation still names both fields further along it.

    ⛔ **The set of names this pin DEMANDS is DERIVED FROM `metrics()`** — never a duplicate of it.
    That is the finding this pin was rewritten for (#9548 review P3): the earlier form iterated two
    LITERALS, so a *coherent* rename — payload keys, docstring, `test_9548`'s literals and this
    file's literals all renamed together — left `docs/infra-runbook.md` documenting a field the
    payload no longer published with **every test green**, while this docstring claimed it pinned
    "the two fields the payload publishes". Reading the demanded set from `metrics()` is what reds
    the runbook-stale half, and it is also the reason this pin is in `tier1` rather than beside the
    docstring pin.

    ⚠️ **What is still spelled out by hand, and why that is not a contradiction** (#11128 review P3):
    the positive pair is hand-spelled at **four sites below, in three roles** —
    (1) the `<= record_keys` **precondition**; (2) the non-vacuity **mutation**, both its `.replace(...)`
    SEARCH strings and the `== {…}` EXPECTATION naming what the mutation must leave reported missing;
    and (3) the **survivor** assertion that the unmutated cells satisfy the pair. Role (1) is the only
    duplicate-in-the-harmful-sense, and it is deliberate: naming `record_writes_applied` /
    `record_last_write_at` IS the #9548 claim (this pair exists, is published, and must be documented),
    so the pin has to fail if either disappears rather than quietly demanding one fewer name.
    Roles (2) and (3) are not second sources of truth: (2)'s search strings say which cells to mutate
    and its expectation says which names must then be missing (they have to be the payload's names —
    that is what makes the mutation mean "the runbook kept the OLD row"), and (3) asserts those same
    names are satisfied before the mutation. So a coherent rename does require editing these
    spellings too — and the pin still reds when the runbook keeps the old row; what it never does is
    reduce the set it demands on its own.
    """
    from tortoise import monitoring  # local: this file keeps heavy imports inside tests

    section = _record_arming_section()
    rows = [line for line in section.splitlines() if line.lstrip().startswith("|")]
    assert rows, "the arming section has no table rows at all — the field docs vanished"
    heads = [row.split("|")[1] for row in rows if len(row.split("|")) > 1]
    published = monitoring.metrics()
    record_keys = {name for name in published if name.startswith("record_")}
    assert {"record_writes_applied", "record_last_write_at"} <= record_keys, (
        "`metrics()` no longer publishes the positive pair #9548 exists to add, so "
        f"an empty failure object cannot be read as a write — record keys: {sorted(record_keys)}"
    )
    missing = _record_keys_missing_a_row(heads, record_keys)
    assert not missing, (
        f"the arming section's field table documents no row for {sorted(missing)} — "
        "the operator has no reading to take for a counter the payload publishes, "
        "however correct the `/health` payload is; a renamed key leaves this row "
        "naming a field that no longer exists"
    )
    # Non-vacuity MEASURED, and it must be a measurement that can FAIL (#11128 review
    # P3): the earlier form asserted `_record_keys_missing_a_row(heads, {f"{n}_renamed"})
    # == {f"{n}_renamed"}` — which holds for ANY `heads` that mention no `*_renamed` cell,
    # including `heads=[]`, so it could not red on the mutation its message described.
    # This mutates the REAL head cells instead: rename the pair where the section
    # documents it, and require the predicate to report exactly those two names
    # missing and to stop reporting them once the mutation is reverted. Vacuous
    # passes are impossible by construction — the mutation is asserted to have
    # changed a cell, and the survivor check proves the cells are LIVE input, not an
    # empty list the predicate would agree with.
    mutated = [
        head.replace("`record_writes_applied`", "`record_applied_writes`").replace(
            "`record_last_write_at`", "`record_last_write_instant`"
        )
        for head in heads
    ]
    assert mutated != heads, (
        "the rename mutation changed no head cell — the arming section no longer "
        "documents either name in a head cell, so this measurement has no subject"
    )
    assert _record_keys_missing_a_row(mutated, record_keys) == {
        "record_writes_applied",
        "record_last_write_at",
    }, (
        "this pin would stay GREEN on a coherent rename of `metrics()`'s record "
        "keys — the runbook would keep documenting the retired names while the "
        "suite passed, the paper-only claim #9548 exists to end"
    )
    # …and the predicate reads the cells given to it (so it is not a constant):
    # with the REAL head cells, neither name is missing.
    assert not _record_keys_missing_a_row(heads, {"record_writes_applied",
                                                "record_last_write_at"})


def test_any_ci_surfaces_line_citation_here_resolves_to_this_files_own_row():
    """#11064 review (P3), executed: a line citation into the manifest must RESOLVE.

    The sentence establishing WHERE this file's `tier1` registration lives cited
    `config/ci-surfaces.yml` by LINE NUMBER, and the number had rotted onto another
    test's row — on `main` it was `- test_cli_serve.py`; on this branch it was
    `- test_s2_decision_record_5087.py`. It is now cited by mechanism, which the manifest
    itself requires ("Named by mechanism, not by line number"). A mechanism citation
    cannot decay; a line citation decays on the next edit to a 7,600-line file, which is
    precisely how the wrong one got there.

    So this pin does not ban a line citation, it CHECKS one: every
    `config/ci-surfaces.yml:<line>` pointer in THIS file must resolve to a row naming
    this file. ⚠️ If a later lane needs to QUOTE a rotten pointer as history, quote it
    without the `:<n>` suffix (the surviving row's own name), or this pin will treat the
    quotation as a live claim — the distinction the rest of this file draws between a
    live instruction and a `SUPERSEDED` record.

    ⚠️ **The pin is CONDITIONAL, and says so** (#11064 review r17, P3): it checks the
    citations that EXIST, and at the head that added it this file carries NONE (the
    sentence it was written for cites the manifest by mechanism now). A green run
    therefore means *"no rotten pointer here"*, not *"a pointer was verified"* — the
    distinction matters because a scanner that matches nothing looks identical to one
    that checked everything. It reds the moment a `:<n>` pointer is added and rots.

    Falsified on the pre-fix text: the pointer resolved to another test's row.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    manifest = (_REPO_ROOT / "config" / "ci-surfaces.yml").read_text(encoding="utf-8").splitlines()
    stale = []
    for match in re.finditer(r"config/ci-surfaces\.yml:(\d+)", source):
        number = int(match.group(1))
        row = manifest[number - 1].strip() if 0 < number <= len(manifest) else "<beyond the file>"
        if "test_deploy_workflow.py" not in row:
            stale.append(f"{match.group(0)} -> {row!r}")
    assert not stale, (
        "a `config/ci-surfaces.yml:<line>` citation in this file resolves to a row that is "
        "not this file's registration:\n  " + "\n  ".join(stale) + "\n"
        "Cite the entry by mechanism (`its `- test_deploy_workflow.py` entry under "
        "`tier1:` in `config/ci-surfaces.yml``) — a line number here has already rotted "
        "onto a sibling test's row (#11064 review P3)"
    )


def test_any_workflow_line_citation_here_resolves_to_a_real_line_and_the_test_job_is_bare():
    """#11064 review (P2), executed: a `.github/workflows/*.yml:<line>` citation must RESOLVE.

    The census guard's premise — *the `test` job's only checkout is a bare `actions/checkout@v4`,
    so the job runs at the default `fetch-depth: 1`* — was cited as `.github/workflows/python-ci.yml`
    line 898. ⚠️ Quoted WITHOUT the `:<n>` suffix on purpose: this pin reads this file's own source,
    so a live-looking `python-ci.yml:898` in a docstring would be treated as a claim (the sibling
    `ci-surfaces` pin documents the same convention). That number was CORRECT at the head the
    sentence was
    written on and rotted **during this PR's own merge of `origin/main`**: at `fc92a8e3c` the line
    was `      - uses: actions/checkout@v4`, and at the merge commit `ce0bb3d44` it is a prose
    comment (*"published to every later step through `$GITHUB_ENV` …"*) — a `main` edit higher in
    the file moved every later line down. The claim stayed true and its evidence pointed at
    unrelated text, which is the exact "a past state quoted as a current one" decay this PR exists
    to close, reproduced inside the PR that closes it.

    So this pin executes the class rather than trusting the numeral:

    1. every `.github/workflows/<name>.yml:<line>` pointer in THIS file must land inside its
       workflow at a line that is real CONTENT — not blank, and not a `#` comment. That is what
       catches a rotted pointer without needing to know what the line is *supposed* to say; a
       citation that lands on a comment has provably moved.
    2. the SEMANTIC half, asserted where the sentence makes it: the `test` job's checkout carries
       no `with:`/`fetch-depth`. If a later lane unshallows that job, the shallow-checkout bound
       this file guards against stops existing and the guard would keep passing — the premise has
       to red, not the silent-pass path.

    Falsified on the pre-fix text: line 898 resolved to a comment. Falsified on an unshallowed
    `test` job: the second assertion names the added `with:` key.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    workflows = _REPO_ROOT / ".github" / "workflows"
    stale = []
    for match in re.finditer(r"\.github/workflows/([a-z0-9_.-]+\.ya?ml):(\d+)", source):
        name, number = match.group(1), int(match.group(2))
        lines = (workflows / name).read_text(encoding="utf-8").splitlines()
        line = lines[number - 1] if 0 < number <= len(lines) else None
        if line is None:
            stale.append(f"{match.group(0)} -> beyond the file ({len(lines)} lines)")
        elif not line.strip() or line.lstrip().startswith("#"):
            stale.append(f"{match.group(0)} -> {line.strip()!r} (blank or a comment)")
    assert not stale, (
        "a `.github/workflows/<name>.yml:<line>` citation in this file no longer lands on "
        "anything:\n  " + "\n  ".join(stale) + "\n"
        "A workflow edit shifts every later line, so re-derive the number or cite the step by "
        "mechanism — a pointer that lands on a comment has provably rotted (#11064 review P2)"
    )

    # ── the SEMANTIC half: the `test` job's checkout must stay BARE ─────────────────────
    workflow = (workflows / "python-ci.yml").read_text(encoding="utf-8").splitlines()
    header = next(
        (i for i, ln in enumerate(workflow) if re.fullmatch(r"  test:\s*", ln)),
        None,
    )
    assert header is not None, (
        "`python-ci.yml` no longer declares a top-level `test:` job — the census guard's "
        "shallow-checkout bound is derived from it"
    )
    end = next(
        (i for i in range(header + 1, len(workflow)) if re.fullmatch(r"  [A-Za-z0-9_-]+:\s*", workflow[i])),
        len(workflow),
    )
    checkouts = [
        i for i in range(header, end)
        if workflow[i].strip().startswith("- uses: actions/checkout@")
    ]
    assert len(checkouts) == 1, (
        "the `test:` job no longer has exactly ONE `actions/checkout@` step "
        f"(found {len(checkouts)}) — the guard's premise is that a single bare checkout "
        "gives the job the default `fetch-depth: 1` (#11064 review P2)"
    )
    # The step's own body is the checkout line plus any `with:`/`env:` continuation lines up to
    # the next step. `fetch-depth` anywhere in it means the job is no longer shallow.
    step = [workflow[checkouts[0]]]
    for ln in workflow[checkouts[0] + 1:end]:
        if ln.strip().startswith("- "):
            break
        step.append(ln)
    assert not any("fetch-depth" in ln for ln in step), (
        "the `test:` job's checkout now sets `fetch-depth`, so the job is no longer shallow — "
        "the false-RED bound `test_the_census_git_guard_fails_closed_and_never_reds_a_shallow"
        "_checkout` guards against no longer exists, while the guard would keep passing "
        "(#11064 review P2):\n  " + "\n  ".join(ln for ln in step if "fetch-depth" in ln)
    )

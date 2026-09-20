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

import re
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
# The one argv pair whose value is a GitHub BUILT-IN, not an env-bound secret.
# Exempted by name, so the parser can accept the brace spelling without the
# exemption depending on a regex that misses it.
_NON_SECRET_PAIRS = {("TORTOISE_GIT_SHA", "GITHUB_SHA")}
# The only argv keys whose FLY-side name deliberately differs from the env var
# it reads. Every other key must equal the variable it reads — the app resolves
# its config by the env NAME, so a fly-side typo ships the secret under a name
# nothing reads while the verify-secrets gate still passes.
_FLY_RENAMES = {
    "GITHUB_CLIENT_ID": "GH_CLIENT_ID",
    "GITHUB_CLIENT_SECRET": "GH_CLIENT_SECRET",
    "GITHUB_CALLBACK_URL": "GH_CALLBACK_URL",
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
    """The workflow's named steps, keyed by name."""
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps: dict[str, dict] = {}
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            name = step.get("name")
            if name:
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
    array (else it is wired to nothing), every array element must read an
    env-bound secret (else it sends a stale/empty value), and every fly-side
    argv key must be the env name the app reads (or a declared `_FLY_RENAMES`
    rename — a fly-side typo ships the secret under a name nothing reads while
    the gate passes). ``FLY_API_TOKEN`` is the one exemption: flyctl consumes it
    from the environment, never as argv."""
    step = _steps()[_SET_STEP]
    bindings = set(_env_bindings(step))
    shell_vars = set(_secret_array_pairs(step.get("run", "")).values())
    assert shell_vars == bindings - _NON_SYNCED, (
        "the secrets-set step's env: bindings and its flyctl array diverge — "
        f"bound-but-not-sent: {sorted(bindings - _NON_SYNCED - shell_vars)}; "
        f"sent-but-not-bound: {sorted(shell_vars - bindings)}"
    )
    # The FLY-side argv name is what the app resolves config by. Pin it to the
    # env name it reads (or a declared rename); a fly-side typo/swap otherwise
    # passes every value-based guard above.
    for fly_name, shell_var in _secret_array_pairs(step.get("run", "")).items():
        expected = _FLY_RENAMES.get(fly_name, fly_name)
        assert expected == shell_var, (
            f"fly argv key {fly_name} reads ${shell_var}, but the expected env "
            f"name is {expected} — a fly-side typo/undeclared rename would ship "
            f"the secret under a name nothing reads while the gate passes (#4334)"
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
    it would push `K=` to Fly and clobber any out-of-band value. Two append
    shapes are deliberately exempt: the two multi-pair appends (the base
    three-key array and the Stripe pair), and
    `TORTOISE_GIT_SHA="${GITHUB_SHA}"` — GITHUB_SHA is a GitHub built-in that is
    always set, not an env-bound secret, so it needs no guard (`_NON_SECRET_PAIRS`)."""
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

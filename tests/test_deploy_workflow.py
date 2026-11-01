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
    environment, never as argv) and the `TORTOISE_GIT_SHA="${GITHUB_SHA}"`
    built-in pair (`_NON_SECRET_PAIRS`) are the two exemptions."""
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
    it would push `K=` to Fly and clobber any out-of-band value. Two append
    shapes are deliberately exempt: the two multi-pair appends (the base
    three-key array and the Stripe pair), and
    `TORTOISE_GIT_SHA="${GITHUB_SHA}"` — GITHUB_SHA is a GitHub built-in that is
    always set, not an env-bound secret, so it needs no guard (`_NON_SECRET_PAIRS`).
    A third is the #4126 versioned default (`_UNCONDITIONAL_BY_DECISION`), which
    pushes a recorded non-secret constant, never an empty value."""
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
    # The presence LIST must be the five qualifying readings, in one clause — not
    # merely a lead-in that some other, always-true reading could be appended to.
    # ⛔ Read the FLAT copy, like its siblings above: this pin used to read `section`,
    # and its pattern spells the sentence out with literal single spaces, so a
    # wording-identical refill at widths 76/78/82 broke that clause across a line
    # boundary and reded it — measured here after the F3 fix, i.e. the file still
    # reds a reflow F3 exists to make harmless (#10083 review P2). Normalizing is
    # NOT a relaxation: the five readings are still required verbatim below.
    presence = re.search(
        r"After the capture, require\s+at least one reading \*\*before\*\* you interpret anything:(.*?)\.\s",
        flat,
    )
    assert presence, "the presence requirement does not enumerate its readings"
    for reading in ("record_write_failures", "record_non_journal_skips",
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
    assert re.search(r"read both counters BEFORE the\s+capture and again after", flat), (
        "the bar says 'growth' but never tells the operator to take a reading "
        "BEFORE the capture — an absolute count left from earlier in the "
        "process's life then reads as this capture's growth"
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
    "cannot check" refusal (not "no build"), `commit_dirty` is **`null` on
    production today** because nothing stamps `TORTOISE_GIT_DIRTY` (`null` is NOT
    "clean"), and `migrations_head` is the population the IMAGE carries — not the
    set applied to the database. A stale deploy with `build_identified: true` and
    a null `commit_dirty` is exactly the case that reads as clean if the runbook
    is silent.

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
                  "started_at", "migrations_head", "migrations_source"):
        assert field in section, (
            f"the field table omits `{field}` — a reader who has the field but no "
            "meaning reads it wrong (a `null` `commit_dirty` as clean, an "
            "`unknown` `migrations_source` as the checkout)"
        )
    assert re.search(r"does NOT fold into `status`", flat), (
        "the entry never says the block does not fold into `status` — an operator "
        "who reads `status: ok` as 'current' restarts a healthy machine, and a "
        "future change that DID fold staleness in would look documented-ok"
    )
    assert re.search(r"`null` means \*\*NOT STAMPED\*\*", flat), (
        "the entry does not say `commit_dirty: null` means NOT STAMPED — nothing "
        "stamps `TORTOISE_GIT_DIRTY` yet, so `null` is silence, never 'clean'"
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
        "the note claims the pre-#9548 census was empty — it returned one line, "
        "#9556/R8's §9 operator example, and a later lane needs that line named to "
        "tell it apart from a #9548 arming surface"
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
    stated = re.search(r"the pre-#9548 reading is\s+\*\*(\d+) lines?\*\*", note)
    assert stated, (
        "the note no longer states how many lines the pre-#9548 census returned, "
        "so `NOT empty` is all it claims and the count can drift to any number"
    )
    count = int(stated.group(1))
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

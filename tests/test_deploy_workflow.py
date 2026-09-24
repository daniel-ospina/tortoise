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


def test_skip_bypass_inputs_are_re_armed_by_default():
    """#4605 — every `skip-*` dispatch input defaults to `false`.

    A bypass is an incident-window STATE — set per run with the input, or per
    window with the `SKIP_*` repo variable (docs/infra-runbook.md §8.2) — never
    a committed default. `skip-db-health-gate` shipped `default: 'true'` as the
    RC3 FalkorDB-restore mitigation and so skipped the release-health
    verification on EVERY manual dispatch; #4605 re-armed it on 2026-09-22 once
    the data plane was healthy, which is why the incident default must not be
    "restored". A `'true'` default disarms a gate without any run saying so.
    """
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare `on:` key as the boolean True (YAML 1.1 truthiness).
    on = doc.get("on") or doc.get(True)
    inputs = on["workflow_dispatch"]["inputs"]
    skips = {name: spec for name, spec in inputs.items() if name.startswith("skip-")}
    # Pin the NAMES, not just the prefix: prefix discovery alone would shrink
    # silently if a gate were renamed out of the `skip-` namespace (or removed),
    # leaving this guard green while covering one gate fewer (#4760 review).
    expected = {
        "skip-db-health-gate",
        "skip-pack-smoke",
        "skip-fly-machines-guard",
        "skip-fly-secret-provenance",
    }
    missing = sorted(expected - set(skips))
    assert not missing, (
        f"bypass input(s) missing or renamed out of the skip-* namespace: {missing} "
        "— update this guard deliberately, do not let it shrink silently"
    )
    for name, spec in skips.items():
        assert spec.get("type") == "boolean", f"{name} must stay a boolean input"
        assert str(spec.get("default")).lower() == "false", (
            f"{name} defaults to {spec.get('default')!r} — a bypass must be an "
            "incident-window state (docs/infra-runbook.md §8.2), never the "
            "committed default"
        )


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
    """The helper's canonical gate table: (variable, input, label, kind).

    Read from the SCRIPT, so a gate added or renamed there cannot silently leave
    these guards covering one gate fewer — the same reason
    ``test_skip_bypass_inputs_are_re_armed_by_default`` pins names rather than
    the ``skip-`` prefix.
    """
    text = _BYPASS_SCRIPT.read_text(encoding="utf-8")
    table = _region(text, "GATES=(", "\n)")
    rows = [tuple(m.groups()) for m in re.finditer(r'"([^"|]+)\|([^"|]+)\|([^"|]+)\|([^"|]+)"', table)]
    assert len(rows) == 4, f"expected FOUR bypassable gates in the helper table, found {rows!r}"
    return rows


def _step_runs() -> dict[str, str]:
    """Every named step that has a ``run:`` body → that body."""
    return {name: step["run"] for name, step in _steps().items() if step.get("run")}


def test_every_bypass_reports_through_the_shared_helper():
    """All four bypasses render through ONE reporter (#4759).

    The old shape was inconsistent: two gates had a dedicated warning step and
    two emitted an inline ``echo … >&2`` from inside a larger step, which is part
    of why a bypass was easy to miss. Every site must now call the shared helper
    exactly once, and no step may hand-roll its own bypass warning any more.
    """
    wf, gates = _WORKFLOW.read_text(encoding="utf-8"), _bypass_gates()
    for key, _inp, _label, _kind in gates:
        assert wf.count(f"--key {key}") == 1, (
            f"{key} must be reported exactly once through deploy-bypass.sh — "
            f"found {wf.count(f'--key {key}')}"
        )
    assert wf.count("deploy-bypass.sh report") == len(gates), (
        "one report call per bypassable gate"
    )
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
    # would drop one of two same-named steps (#4802 review).
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    for job_name, job in doc["jobs"].items():
        for step in job.get("steps", []):
            run = step.get("run") or ""
            if "deploy-bypass.sh" not in run:
                continue
            name = step.get("name") or f"unnamed step in job {job_name!r}"
            assert "GITHUB_STEP_SUMMARY" not in run, (
                f"step {name!r} must leave $GITHUB_STEP_SUMMARY to the helper"
            )
            assert "DEPLOY_BYPASS_MARKER" in (step.get("env") or {}), (
                f"step {name!r} in job {job_name!r} must bind "
                "DEPLOY_BYPASS_MARKER for the per-job audit"
            )


def test_deploy_jobs_audit_every_bypassable_gate():
    """Each deploy job ENDS by stating every gate it holds as bypassed or not.

    Without this, the ABSENCE of a bypass block is itself ambiguous — exactly the
    "failure mode looks like its success mode" defect #4759 fixes. The audit runs
    ``if: always()`` so an earlier failure cannot suppress it.
    """
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    audits: dict[str, dict] = {}
    for job_name, job in doc["jobs"].items():
        for step in job.get("steps", []):
            if "deploy-bypass.sh audit" in (step.get("run") or ""):
                audits[job_name] = step
    per_job: dict[str, set[str]] = {}
    for job_name, step in audits.items():
        assert step.get("if") == "always()", f"{job_name} audit must run on failure too"
        run = step["run"]
        assert not re.search(r"\$\{\{\s*vars\.", run), (
            "audit values must be env-bound, not interpolated"
        )
        per_job[job_name] = set(re.findall(r'--state\s+"?([A-Z0-9_]+)=', run))
    # The per-JOB mapping, not the flattened union. A union cannot see a gate
    # MOVE between jobs — swapping the four `--state` lines across the two
    # audits leaves the multiset identical, so the guard passes while
    # `post-deploy-verify`'s audit stops stating its own gate and starts
    # claiming one it does not hold (exactly the #4538 DB-health move).
    expected_jobs = {
        "deploy-api": {
            "SKIP_PACK_SMOKE",
            "SKIP_FLY_MACHINES_GUARD",
            "SKIP_FLY_SECRET_PROVENANCE",
        },
        "post-deploy-verify": {"SKIP_DB_HEALTH_GATE"},
    }
    assert set(audits) == set(expected_jobs), (
        f"both deploy jobs must audit their bypassable gates, got {sorted(audits)}"
    )
    assert per_job == expected_jobs, (
        f"each job's audit must name exactly the bypassable gates THAT JOB holds — got "
        f"{ {k: sorted(v) for k, v in per_job.items()} }, expected "
        f"{ {k: sorted(v) for k, v in expected_jobs.items()} }. A flattened union cannot "
        "see a gate moving between jobs (#4538)."
    )
    all_named = set().union(*per_job.values())
    assert all_named == {key for key, *_ in _bypass_gates()}, (
        "every bypassable gate must be named by exactly one job's audit"
    )


def test_bypass_expiry_is_machine_checked():
    """The ``vars.SKIP_*`` window is checked by a MACHINE, daily (#4759 ask 2).

    The window start is the dated companion ``SKIP_<VAR>_SET_AT`` read through
    the ordinary ``vars`` context — `gh variable list --json updatedAt` is not
    usable with the workflow token (the endpoint needs the fine-grained
    "Variables" permission) and `vars` exposes no `updatedAt`. The check is a
    SEPARATE scheduled workflow, so a red run can never block a deploy.
    """
    assert _EXPIRY_WORKFLOW.is_file(), f"missing scheduled check: {_EXPIRY_WORKFLOW}"
    doc = yaml.safe_load(_EXPIRY_WORKFLOW.read_text(encoding="utf-8"))
    on = doc.get("on") or doc.get(True)
    crons = [entry["cron"] for entry in on["schedule"]]
    assert crons, "the bypass-expiry check must be SCHEDULED, not dispatch-only"
    env = doc["jobs"]["check"]["env"]
    for key, *_ in _bypass_gates():
        assert env.get(key) == f"${{{{ vars.{key} }}}}", f"{key} must be read from vars"
        assert env.get(f"{key}_SET_AT") == f"${{{{ vars.{key}_SET_AT }}}}", (
            f"{key}_SET_AT must be read from vars — it is the window start"
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


@pytest.mark.parametrize(
    "step_name,key",
    [
        ("Check Fly secret provenance (fail-closed)", "SKIP_FLY_SECRET_PROVENANCE"),
        ("Check Fly machines (orphan + crash-loop guard, fail-closed)", "SKIP_FLY_MACHINES_GUARD"),
    ],
)
def test_exit2_still_cannot_be_bypassed_and_precedes_the_skip(step_name, key):
    """``exit 2`` (could-not-determine) is never bypassable, and stays FIRST.

    The wrapper gates translate ONLY the checker's exit 1. This pins the order —
    the exit-2 branch must be tested before the skip branch — and that the exit-2
    branch consults no skip lane at all. #4759's changes are reporting-only and
    must not have weakened this.
    """
    run = _steps()[step_name]["run"]
    i2 = run.index('if [ "$RC" -eq 2 ]')
    i1 = run.index('if [ "$RC" -eq 1 ]')
    assert i2 < i1, "the exit-2 branch must be handled BEFORE the bypass branch"
    exit2 = run[i2:i1]
    assert "exit 2" in exit2, "the exit-2 branch must still block the deploy"
    assert "skip-" not in exit2 and "SKIP_" not in exit2, (
        "the exit-2 (could-not-determine) branch must never consult a skip lane"
    )
    assert key in run[i1:], "the bypass branch must still require this gate's lane"


def test_bypass_lane_conditions_are_unchanged_and_never_date_gated():
    """No bypass was widened, and the dated companion cannot ENABLE one (#4759).

    The four lane conditions are pinned verbatim: the change is reporting-only.
    ``SKIP_<VAR>_SET_AT`` is a window start, never a switch — if it could satisfy
    a lane condition it would be a new way to bypass a gate.
    """
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert doc["jobs"]["packaging-smoke"]["if"] == (
        "${{ ! (inputs.skip-pack-smoke || vars.SKIP_PACK_SMOKE == 'true') }}"
    )
    steps = _steps()
    assert steps["Post-deploy DB health verification (post-release; does not gate the deploy)"]["if"] == (
        "${{ ! (inputs.skip-db-health-gate || vars.SKIP_DB_HEALTH_GATE == 'true') }}"
    )
    for step_name, expr in (
        ("Check Fly machines (orphan + crash-loop guard, fail-closed)",
         "${{ inputs.skip-fly-machines-guard || vars.SKIP_FLY_MACHINES_GUARD == 'true' }}"),
        ("Check Fly secret provenance (fail-closed)",
         "${{ inputs.skip-fly-secret-provenance || vars.SKIP_FLY_SECRET_PROVENANCE == 'true' }}"),
    ):
        assert expr in steps[step_name]["run"], f"{step_name}: lane condition changed"

    # Every `_SET_AT` occurrence must be a window-start DATA use: an env binding,
    # a `--set-at` argument, or prose. Never a condition.
    for line in _WORKFLOW.read_text(encoding="utf-8").splitlines():
        if "_SET_AT" not in line:
            continue
        stripped = line.strip()
        assert (
            stripped.startswith("#")
            or re.fullmatch(r"[A-Z0-9_]+_SET_AT: \$\{\{ vars\.[A-Z0-9_]+_SET_AT \}\}", stripped)
            or re.fullmatch(r'--set-at "\$[A-Z0-9_]+_SET_AT" \\?', stripped)
        ), f"unexpected _SET_AT use (must never gate a bypass): {stripped!r}"


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
        env = step.get("env") or {}
        for var in re.findall(r"\$([A-Z0-9_]+_SET_AT)", run):
            assert var in env, f"step {name!r} reads ${var} but never binds it in env:"

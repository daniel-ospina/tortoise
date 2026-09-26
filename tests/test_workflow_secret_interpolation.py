"""Workflow ``run:`` secret-interpolation guard (#4361, same class as #4334).

GitHub substitutes ``${{ secrets.X }}`` into a step's shell **TEXT before bash
parses it**, so a secret value is *code*, not data:

- a value containing ``"`` is read as quote toggles and **removed** — exactly
  how the ``STRIPE_PRICE_IDS`` catalog was mangled (checkout dead, #4334);
- a value containing ``$(…)`` or backticks would **execute**.

The fix binds each secret in the step's ``env:`` block (``KEY: ${{ secrets.X }}``)
and reads it as a QUOTED shell variable (``"$KEY"``). An expanded variable's
contents are data, never re-parsed. ``env:`` blocks themselves are safe —
there the substitution is a YAML scalar, not shell source.

#4361 repaired every remaining site in ``blog-write-e2e.yml``,
``deploy-pages.yml``, ``attach-tortoise-domain.yml`` and
``e2e-live-reconcile.yml``. This module is the standing guard for the class: no
step in any ``.github/workflows/*.{yml,yaml}`` may interpolate a secret into its
``run:`` body, and the #4361 steps are pinned to the safe shape (bound in
``env:``, referenced quoted) so a partial revert fails loudly. The carriers the
sweep resolves into run text are the ``secrets`` context, secret-bound ``env``
keys (workflow-root, job and step scope, transitively), and job outputs defined
from a secret; out of its bounded scope are arbitrary data flow through a step's
own output (``steps.<id>.outputs.<name>``) and dynamic index contexts
(``env[matrix.k]``, ``needs[matrix.j].outputs[...]``). The only steps it
does not report are the two ``deploy-hosted.yml`` steps whose run bodies still
carry a pre-fix interpolation, pinned by (job, step) in ``_PENDING_FIX``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# A `secrets` context token inside an expression. Matched BARE (`\bsecrets\b`)
# and case-insensitively: the runner resolves context names with
# OrdinalIgnoreCase, and the bare token is what `toJSON(secrets)` /
# `format('{0}', secrets)` use to dump the whole context into shell source.
# Member access (`secrets.X`, `secrets['X']`, `secrets[matrix.name]`) is the
# common subset. `MY_SECRETS` is a different identifier and is not matched.
_SECRET_CONTEXT = re.compile(r"\bsecrets\b", re.IGNORECASE)
# `env` context references — dot and index form, both case-insensitive like the
# runner's context lookup: `env.KEY`, `env['KEY']`, `env["KEY"]`.
_ENV_DOT_REF = re.compile(r"\benv\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)", re.IGNORECASE)
_ENV_INDEX_REF = re.compile(r"\benv\s*\[\s*(['\"])([^'\"]+)\1\s*\]", re.IGNORECASE)
# Bare-context references: `${{ env }}` / `${{ toJSON(env) }}` dump every env
# value, and `toJSON(needs)` / `${{ needs.j.outputs }}` dump job outputs — so a
# bare `env` is a hazard whenever any visible key is secret-bound, and a bare
# `needs`/`outputs` is one when a referenced job has a secret-derived output.
_ENV_BARE_REF = re.compile(r"\benv\b(?!\s*[.\[])", re.IGNORECASE)
_NEEDS_BARE_REF = re.compile(r"\bneeds\b(?!\s*[.\[])", re.IGNORECASE)
_OUTPUTS_BARE_REF = re.compile(r"\boutputs\b(?!\s*['\"]?\s*\]?\s*[.\[])", re.IGNORECASE)
# `needs.<job>.outputs.<name>` — a job output consumed downstream, dot and index
# form (`needs['job'].outputs['name']`), case-insensitive like the runner. The
# accessors tolerate whitespace: the Actions lexer skips whitespace between
# tokens, so `env . TOKEN` / `needs [ 'j' ] . outputs [ 'o' ]` are valid reads.
_NEEDS_JOB_DOT_REF = re.compile(r"\bneeds\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)", re.IGNORECASE)
_NEEDS_JOB_INDEX_REF = re.compile(r"\bneeds\s*\[\s*(['\"])([^'\"]+)\1\s*\]", re.IGNORECASE)
_OUTPUTS_DOT_REF = re.compile(r"\boutputs\b\s*['\"]?\s*\]?\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)", re.IGNORECASE)
# The name extractor is deliberately position-independent (`\boutputs`, no leading
# `.`) and tolerates the closing quote/bracket of a quoted key, so every accessor
# chaining of dot/index resolves: `needs.j.outputs.o`, `needs['j'].outputs['o']`,
# `needs.j['outputs']['o']`, `needs.j['outputs'].o`, `needs['j']['outputs']['o']`, …
_OUTPUTS_INDEX_REF = re.compile(r"\boutputs\b\s*['\"]?\s*\]?\s*\[\s*(['\"])([^'\"]+)\1\s*\]", re.IGNORECASE)
# A step env binding to a single secret: `KEY: ${{ secrets.KEY }}`.
_ENV_SECRET = re.compile(r"\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}", re.IGNORECASE)


def _expression_spans(text: str) -> list[str]:
    """Every ``${{ … }}`` expression span in ``text``.

    Span extraction mirrors the Actions runner rather than a non-greedy regex:
    a ``}}`` inside a single-quoted string literal does NOT terminate the
    expression, so a secret following such a literal (e.g.
    ``${{ fromJSON('{"a":{"b":1}}').x + secrets.BAR }}``) is still seen. The
    non-greedy ``\\$\\{\\{.*?\\}\\}`` form truncated the span at the inner
    ``}}`` and never re-scanned the remainder — a fail-open for that spelling.
    """
    spans: list[str] = []
    i = 0
    while True:
        start = text.find("${{", i)
        if start == -1:
            return spans
        j = start + 3
        in_string = False
        while j < len(text):
            if text[j] == "'":
                in_string = not in_string
            elif not in_string and text.startswith("}}", j):
                break
            j += 1
        spans.append(text[start : j + 2] if j < len(text) else text[start:])
        i = max(j + 2, start + 3)


def _secret_interpolations(text: str) -> list[str]:
    """Every expression span in ``text`` that reads the ``secrets`` context."""
    return [s for s in _expression_spans(text) if _SECRET_CONTEXT.search(s)]


def _env_refs(text: str) -> list[str]:
    """``env`` context references in ``text`` — dot and index form."""
    return _ENV_DOT_REF.findall(text) + [m[1] for m in _ENV_INDEX_REF.findall(text)]


def _resolve_secret_env_keys(
    *envs: dict | None, secret_outputs: set[tuple[str, str]] = frozenset()
) -> set[str]:
    """Env keys bound, directly or transitively, to a secret in the given maps.

    A key is secret-bound when its value carries a secret through ANY of the
    declared carriers (``_span_reads_secret``), including a whole-context dump
    (``toJSON(env)``) — a fixpoint, so chains of any length resolve. ``envs``
    should carry every scope visible to the step (workflow root, job, step): the
    ``env`` context unions them. Scopes are unioned conservatively (fail-closed):
    a narrower scope that shadows a secret-bound key with a plain value is still
    treated as secret-bound.
    """
    keys: set[str] = set()
    changed = True
    while changed:
        changed = False
        for env in envs:
            for name, value in (env or {}).items():
                if str(name) in keys:
                    continue
                if any(
                    _span_reads_secret(s, keys, secret_outputs)
                    for s in _expression_spans(str(value))
                ):
                    keys.add(str(name))
                    changed = True
    return keys


def _secret_output_keys(doc: dict) -> set[tuple[str, str]]:
    """``(job, output_name)`` whose job-output definition reads a secret.

    A definition reads a secret through ANY of the declared carriers
    (``_span_reads_secret``): the ``secrets`` context, a secret-bound ``env``
    key visible to the job, a whole-context dump, or another secret-derived job
    output. Env keys and outputs are MUTUALLY recursive (an output defined from
    an env key that is itself bound to an output), so they are resolved in one
    joint fixpoint. A ``steps.<id>.outputs`` chain is outside the guard's scope.
    """
    root_env = doc.get("env")
    jobs = {str(n): (j or {}) for n, j in (doc.get("jobs") or {}).items()}
    outputs: set[tuple[str, str]] = set()
    job_keys: dict[str, set[str]] = {name: set() for name in jobs}
    changed = True
    while changed:
        changed = False
        for job_name, job in jobs.items():
            keys = _resolve_secret_env_keys(root_env, job.get("env"), secret_outputs=outputs)
            if keys != job_keys[job_name]:
                job_keys[job_name] = keys
                changed = True
            for name, value in (job.get("outputs") or {}).items():
                if (job_name, str(name)) in outputs:
                    continue
                if any(
                    _span_reads_secret(s, keys, outputs) for s in _expression_spans(str(value))
                ):
                    outputs.add((job_name, str(name)))
                    changed = True
    return outputs


def _needs_output_refs(span: str) -> set[tuple[str, str]]:
    """``(job, output)`` referenced by a ``needs`` span, dot or index form."""
    jobs = _NEEDS_JOB_DOT_REF.findall(span) + [
        m[1] for m in _NEEDS_JOB_INDEX_REF.findall(span)
    ]
    outs = _OUTPUTS_DOT_REF.findall(span) + [m[1] for m in _OUTPUTS_INDEX_REF.findall(span)]
    return {(j.lower(), o.lower()) for j in jobs for o in outs}


def _span_reads_secret(
    span: str,
    secret_env_keys: set[str] | frozenset[str],
    secret_outputs: set[tuple[str, str]] | frozenset[tuple[str, str]],
) -> bool:
    """Does this single ``${{ … }}`` span carry a secret value into its text?

    The single definition of the declared carriers, shared by all three layers
    (run bodies, env values, job-output definitions) so a dump at a DEFINITION
    layer resolves exactly like one at the point of consumption:

    - the ``secrets`` context (bare/member/indexed/wrapped/case);
    - a secret-bound ``env`` key, dot or index form;
    - a secret-derived job output via ``needs.<job>.outputs.<name>``;
    - a whole-context dump of a carrier that holds a secret (``env``,
      ``toJSON(env)``, ``toJSON(needs)``, ``needs.<job>.outputs``).
    """
    if _SECRET_CONTEXT.search(span):
        return True
    lowered_env = {k.lower() for k in secret_env_keys}
    lowered_out = {(job.lower(), name.lower()) for job, name in secret_outputs}
    if any(r.lower() in lowered_env for r in _env_refs(span)):
        return True
    if _needs_output_refs(span) & lowered_out:
        return True
    if _ENV_BARE_REF.search(span) and secret_env_keys:
        return True
    if _NEEDS_BARE_REF.search(span) and secret_outputs:
        return True
    if _OUTPUTS_BARE_REF.search(span):
        job_refs = {j.lower() for j in _NEEDS_JOB_DOT_REF.findall(span)}
        job_refs |= {m[1].lower() for m in _NEEDS_JOB_INDEX_REF.findall(span)}
        if job_refs & {job for job, _name in lowered_out}:
            return True
    return False


def _run_secret_uses(
    run: str,
    secret_env_keys: set[str],
    secret_outputs: set[tuple[str, str]] = frozenset(),
) -> list[str]:
    """Expressions in ``run`` that put a secret into the shell SOURCE.

    Each re-interpolates the secret into the run text exactly like the direct
    form (the #4334 hazard); the env form is one token away from the fix shape
    this repo prescribes.
    """
    uses: list[str] = []
    for span in _expression_spans(run):
        if _span_reads_secret(span, secret_env_keys, secret_outputs) and span not in uses:
            uses.append(span)
    return uses


def _scrub_run(run: str) -> str:
    """Drop shell comment-only lines from a ``run:`` body.

    A body whose only mention of a variable is a comment (e.g.
    `# TODO: restore -H "Bearer $TOKEN"`) must not satisfy the reference pin,
    and a stray quote or apostrophe in a comment line must not desync the quote
    scanner. Inline trailing comments are deliberately not stripped: telling one
    from a `#` inside a string needs a shell parser, and a variable on a line
    that also carries the consuming command is exactly what the pin asserts.
    """
    return "\n".join(line for line in run.splitlines() if not line.lstrip().startswith("#"))


def _references_outside_double_quotes(run: str, var: str) -> list[str]:
    """Shell lines where ``$var``/``${var}`` is NOT inside double quotes.

    A double-quoted expansion is data; a bare expansion is re-split on IFS and
    glob-expanded, so a value with spaces/specials corrupts the command. A
    single-quoted ``'$var'`` is also returned: a shell does not expand there, so
    the literal text is sent and the secret silently never arrives.

    Quote state is tracked per PHYSICAL LINE (reset at each newline) and
    comment-only lines are skipped, so a stray quote or apostrophe in an
    unrelated comment/here-doc line can neither hide an unquoted expansion nor
    falsely flag a quoted one. Inside single quotes a backslash is literal, per
    POSIX; elsewhere it escapes the next character.
    """
    pattern = re.compile(rf"\$\{{{var}\}}|\${var}(?![A-Za-z0-9_])")
    bad: list[str] = []
    for line in _scrub_run(run).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        in_single = in_double = escaped = False
        i = 0
        while i < len(line):
            ch = line[i]
            if escaped:
                escaped = False
                i += 1
                continue
            if ch == "\\" and not in_single:
                escaped = True
                i += 1
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
                i += 1
                continue
            if ch == '"' and not in_single:
                in_double = not in_double
                i += 1
                continue
            if not in_double:
                match = pattern.match(line, i)
                if match:
                    if stripped not in bad:
                        bad.append(stripped)
                    i = match.end()
                    continue
            i += 1
    return bad


# Two ``deploy-hosted.yml`` steps still carry a pre-fix interpolation; the sweep
# defers exactly those two ``(job, step)`` identities so they are not reported as
# fresh regressions. The deferral is
# exact: it applies only while that file's offenders are these two (job, step)
# identities — exact set AND count. A new interpolating step there, a duplicate
# reusing a pinned name, or a pinned name in another job changes the identity
# set or count and is reported, so the exemption cannot hide a fresh regression.
# Once the file has no offenders the condition no longer matches and the entry is
# inert; the general path then sweeps the file like any other.
_PENDING_FIX: dict[str, frozenset[tuple[str, str]]] = {
    "deploy-hosted.yml": frozenset(
        {
            ("deploy-api", "Verify secrets exist"),
            ("deploy-api", "Set all app secrets on Fly.io (keeps in sync with GitHub/Supabase)"),
        }
    ),
}

# The #4361 fix sites: (workflow filename, step-name PREFIX) → env vars the
# step must bind. A prefix (not an exact name) is used because a step name may
# end at an inline ``#`` YAML comment (e.g. the e2e-live-reconcile steps).
_FIXED_STEPS: dict[tuple[str, str], tuple[str, ...]] = {
    ("blog-write-e2e.yml", "Require the E2E agent key"): ("BLOG_E2E_AGENT_KEY",),
    (
        "deploy-pages.yml",
        "Sync DNS — tortoise.premiselabs.co → premise-labs Pages project (best-effort)",
    ): ("CLOUDFLARE_API_TOKEN",),
    (
        "deploy-pages.yml",
        "Build blog admin SPA (vite) → stage into dist/admin/ (#4171)",
    ): ("SUPABASE_URL", "SUPABASE_ANON_KEY"),
    (
        "deploy-pages.yml",
        "Pre-flight — token can see tortoise-dashboard project (P2-1)",
    ): ("CLOUDFLARE_API_TOKEN",),
    ("attach-tortoise-domain.yml", "Attach custom domain"): ("CLOUDFLARE_API_TOKEN",),
    (
        "e2e-live-reconcile.yml",
        "File or update the bleed issue",
    ): ("GITHUB_TOKEN",),
    (
        "e2e-live-reconcile.yml",
        "File or update the could-not-read issue",
    ): ("GITHUB_TOKEN",),
}


def _workflow_docs(directory: Path = _WORKFLOWS_DIR) -> dict[str, dict]:
    """Every workflow file in ``directory``, parsed (name → document).

    BOTH ``.yml`` and ``.yaml`` are enumerated — GitHub Actions executes either
    extension, so globbing only ``.yml`` would leave a silently unscanned file.
    """
    paths = sorted(set(directory.glob("*.yml")) | set(directory.glob("*.yaml")))
    docs = {path.name: yaml.safe_load(path.read_text(encoding="utf-8")) for path in paths}
    assert docs, f"no workflow files found under {directory}"
    return docs


def _step(doc: dict, prefix: str) -> dict:
    """The single step whose name starts with ``prefix`` (fails loudly)."""
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            name = step.get("name") or ""
            if name.startswith(prefix):
                return step
    raise AssertionError(f"no step named {prefix!r} — the guard's anchor moved")



def _offending_identities(doc: dict) -> list[tuple[str, str]]:
    """``(job, step)`` for every step whose run body puts a secret into the shell."""
    found: list[tuple[str, str]] = []
    root_env = doc.get("env")
    secret_outputs = _secret_output_keys(doc)
    lowered_out = frozenset((j.lower(), n.lower()) for j, n in secret_outputs)
    for job_name, job in (doc.get("jobs") or {}).items():
        job = job or {}
        for step in job.get("steps", []):
            run = step.get("run")
            if not isinstance(run, str):
                continue
            keys = _resolve_secret_env_keys(
                root_env, job.get("env"), step.get("env"), secret_outputs=lowered_out
            )
            if _run_secret_uses(run, keys, secret_outputs):
                found.append((job_name, step.get("name") or "<unnamed>"))
    return found


def _offending_steps(doc: dict) -> list[str]:
    """Step names whose run body interpolates a secret into the shell text."""
    return [name for _job, name in _offending_identities(doc)]


@pytest.fixture(scope="module")
def workflow_docs() -> dict[str, dict]:
    return _workflow_docs()


def test_offender_scan_is_not_vacuous():
    """The sweep must actually flag an interpolated secret — a guard that can
    never fire is worse than none (it reads as coverage)."""
    unsafe = {
        "jobs": {
            "j": {
                "steps": [
                    {"name": "bad", "run": 'x="${{ secrets.SECRET_A }}"'},
                    {"name": "also-bad", "run": "echo ${{ secrets.SECRET_B }}"},
                    {"name": "safe", "run": 'x="$SECRET_A"'},
                ]
            }
        }
    }
    assert _offending_steps(unsafe) == ["bad", "also-bad"]


def test_offender_scan_catches_indexed_and_wrapped_secret_spellings():
    """Evasion pin: the indexed/wrapped spellings interpolate identically, so a
    `secrets\\.` regex that misses them would under-report the hazard."""
    assert _secret_interpolations('x="${{ secrets.FOO }}"')
    assert _secret_interpolations("x=\"${{ secrets['FOO'] }}\"")
    assert _secret_interpolations('x="${{ secrets[matrix.name] }}"')
    assert _secret_interpolations("x=\"${{ format('{0}', secrets.FOO) }}\"")
    assert not _secret_interpolations('x="${{ inputs.foo }}"')
    assert not _secret_interpolations('x="$FOO"')


def test_offender_scan_survives_a_brace_pair_inside_a_string_literal():
    """A `}}` inside a single-quoted literal does not end the expression for the
    Actions runner, so a secret after it must still be seen — a non-greedy
    regex truncates the span at the inner `}}` and returns no offender."""
    run = (
        "curl -H \"Authorization: Bearer "
        "${{ fromJSON('{\"a\":{\"b\":1}}').a.b + secrets.BAR }}\""
    )
    assert _secret_interpolations(run)
    assert _offending_steps({"jobs": {"j": {"steps": [{"name": "off", "run": run}]}}}) == ["off"]


def test_offender_scan_is_case_insensitive_on_the_secret_context():
    """The runner resolves context names case-insensitively, so a mixed-case
    `SECRETS.FOO` is a real interpolation the guard must not miss."""
    for spelling in ("secrets.FOO", "SECRETS.FOO", "Secrets.FOO"):
        assert _secret_interpolations(f'x="${{{{ {spelling} }}}}"'), spelling


def test_offender_scan_catches_a_bare_secrets_context():
    """`toJSON(secrets)` / `format('{0}', secrets)` dump the whole context into
    the shell source — the bare token must be matched, not just member access."""
    run = 'echo "${{ toJSON(secrets) }}"'
    assert _secret_interpolations(run)
    assert _offending_steps({"jobs": {"j": {"steps": [{"name": "dump", "run": run}]}}}) == [
        "dump"
    ]
    assert _secret_interpolations("x=\"${{ format('{0}', secrets) }}\"")


def test_offender_scan_catches_a_secret_reached_through_env():
    """`${{ env.KEY }}` where KEY is bound to a secret re-interpolates the value
    into the run text exactly like `${{ secrets.X }}` — one token from the fix
    shape, so the sweep must resolve secret-bound env keys (step- and job-scope)."""
    doc = {
        "jobs": {
            "j": {
                "env": {"JOB_TOKEN": "${{ secrets.JOB_TOKEN }}"},
                "steps": [
                    {
                        "name": "via step env",
                        "env": {"TOKEN": "${{ secrets.CLOUDFLARE_API_TOKEN }}"},
                        "run": 'curl -H "Authorization: Bearer ${{ env.TOKEN }}" https://x',
                    },
                    {
                        "name": "via env index form",
                        "env": {"TOKEN": "${{ secrets.CLOUDFLARE_API_TOKEN }}"},
                        "run": "curl -H \"Authorization: Bearer ${{ env['TOKEN'] }}\" https://x",
                    },
                    {
                        "name": "via case-mismatched env",
                        "env": {"TOKEN": "${{ secrets.CLOUDFLARE_API_TOKEN }}"},
                        "run": 'curl -H "Authorization: Bearer ${{ env.token }}" https://x',
                    },
                    {
                        "name": "via job env",
                        "run": 'curl -H "Authorization: Bearer ${{ env.JOB_TOKEN }}" https://x',
                    },
                    {
                        "name": "plain env is fine",
                        "env": {"PLAIN": "value"},
                        "run": 'echo "${{ env.PLAIN }}"',
                    },
                    {
                        "name": "a key only bound in another step is not in scope",
                        "run": 'echo "${{ env.TOKEN }}"',
                    },
                ],
            }
        }
    }
    assert _offending_steps(doc) == [
        "via step env",
        "via env index form",
        "via case-mismatched env",
        "via job env",
    ]


def test_offender_scan_resolves_root_env_and_env_chains():
    """The `env` context unions workflow-root, job and step scope, and a key may
    be bound to another secret-bound key — all must resolve."""
    doc = {
        "env": {"ROOT_TOKEN": "${{ secrets.ROOT_TOKEN }}"},
        "jobs": {
            "j": {
                "env": {"JOB_TOKEN": "${{ env.ROOT_TOKEN }}"},
                "steps": [
                    {"name": "root env", "run": 'echo "${{ env.ROOT_TOKEN }}"'},
                    {
                        "name": "env chain",
                        "env": {"CHAIN": "${{ env.JOB_TOKEN }}"},
                        "run": 'echo "${{ env.CHAIN }}"',
                    },
                    {"name": "unbound env is not a secret", "run": 'echo "${{ env.PLAIN }}"'},
                ],
            }
        },
    }
    assert _offending_steps(doc) == ["root env", "env chain"]


def test_offender_scan_follows_a_secret_through_job_outputs():
    """A job output can be defined from a secret; consuming it via
    `${{ needs.<job>.outputs.<name> }}` re-interpolates the value into run text."""
    doc = {
        "jobs": {
            "prep": {
                "runs-on": "ubuntu-latest",
                "env": {"T": "${{ secrets.CLOUDFLARE_API_TOKEN }}"},
                "outputs": {
                    "token": "${{ secrets.CLOUDFLARE_API_TOKEN }}",
                    "env_derived": "${{ env.T }}",
                    "safe": "x",
                },
                "steps": [{"name": "noop", "run": "echo ok"}],
            },
            "use": {
                "needs": "prep",
                "steps": [
                    {
                        "name": "leak",
                        "run": 'curl -H "Bearer ${{ needs.prep.outputs.token }}" https://x',
                    },
                    {
                        "name": "leak index form",
                        "run": "curl -H \"Bearer ${{ needs['prep'].outputs['token'] }}\" https://x",
                    },
                    {
                        "name": "leak env-derived output",
                        "run": 'curl -H "Bearer ${{ needs.prep.outputs.env_derived }}" https://x',
                    },
                    {
                        "name": "safe output",
                        "run": 'echo "${{ needs.prep.outputs.safe }}"',
                    },
                ],
            },
        }
    }
    assert _offending_steps(doc) == ["leak", "leak index form", "leak env-derived output"]


def test_offender_scan_is_whitespace_insensitive_on_accessors():
    """The Actions lexer skips whitespace between tokens, so `env . TOKEN`,
    `env [ 'TOKEN' ]` and `needs [ 'prep' ] . outputs [ 'token' ]` are valid
    reads — an accessor regex that requires adjacency misses them."""

    def env_doc(ref: str) -> dict:
        return {
            "jobs": {
                "j": {
                    "steps": [
                        {
                            "name": "leak",
                            "env": {"TOKEN": "${{ secrets.CLOUDFLARE_API_TOKEN }}"},
                            "run": f'curl -H "Bearer ${{{{ {ref} }}}}" https://x',
                        }
                    ]
                }
            }
        }

    for ref in ("env.TOKEN", "env .TOKEN", "env . TOKEN", "env  .  TOKEN", "env[ 'TOKEN' ]"):
        assert _offending_steps(env_doc(ref)) == ["leak"], ref

    outputs_doc = {
        "jobs": {
            "prep": {
                "outputs": {"token": "${{ secrets.X }}"},
                "steps": [{"run": "echo ok"}],
            },
            "use": {
                "steps": [
                    {
                        "name": "leak",
                        "run": "curl -H \"Bearer ${{ needs [ 'prep' ] . outputs [ 'token' ] }}\" https://x",
                    },
                    {
                        "name": "leak spaced dot",
                        "run": 'curl -H "Bearer ${{ needs.prep . outputs . token }}" https://x',
                    },
                    {
                        "name": "leak all-index",
                        "run": "curl -H \"Bearer ${{ needs['prep']['outputs']['token'] }}\" https://x",
                    },
                    {
                        "name": "leak mixed index",
                        "run": "curl -H \"Bearer ${{ needs.prep['outputs']['token'] }}\" https://x",
                    },
                    {
                        "name": "leak indexed key dotted name",
                        "run": "curl -H \"Bearer ${{ needs.prep['outputs'].token }}\" https://x",
                    },
                    {
                        "name": "leak indexed job and key dotted name",
                        "run": "curl -H \"Bearer ${{ needs['prep']['outputs'].token }}\" https://x",
                    },
                ]
            },
        }
    }
    assert _offending_steps(outputs_doc) == [
        "leak",
        "leak spaced dot",
        "leak all-index",
        "leak mixed index",
        "leak indexed key dotted name",
        "leak indexed job and key dotted name",
    ]

    # A secret-bound env read spelled with whitespace in the OUTPUT DEFINITION
    # must also resolve (the second layer of carrier 3).
    defined_via_env = {
        "env": {"T": "${{ secrets.X }}"},
        "jobs": {
            "prep": {"outputs": {"token": "${{ env . T }}"}, "steps": [{"run": "echo ok"}]},
            "use": {
                "steps": [
                    {
                        "name": "leak",
                        "run": 'x="${{ needs.prep.outputs.token }}"',
                    }
                ]
            },
        },
    }
    assert _offending_steps(defined_via_env) == ["leak"]


def test_offender_scan_follows_a_secret_output_into_env_and_output_chains():
    """A secret-derived job output used as the source of an `env` key, or of
    another job output, must still resolve — the carriers compose."""
    doc = {
        "jobs": {
            "prep": {
                "outputs": {"token": "${{ secrets.CLOUDFLARE_API_TOKEN }}"},
                "steps": [{"run": "echo ok"}],
            },
            "relay": {
                "needs": "prep",
                "outputs": {"forwarded": "${{ needs.prep.outputs.token }}"},
                "steps": [{"run": "echo ok"}],
            },
            "use": {
                "needs": "relay",
                "env": {"TOKEN": "${{ needs.prep.outputs.token }}"},
                "steps": [
                    {"name": "env from output", "run": 'curl "${{ env.TOKEN }}"'},
                    {"name": "chain output", "run": 'curl "${{ needs.relay.outputs.forwarded }}"'},
                ],
            },
        }
    }
    assert _offending_steps(doc) == ["env from output", "chain output"]


def test_offender_scan_resolves_output_env_output_recursion():
    """An output defined from an env key that is itself bound to a secret output
    must resolve — env keys and outputs are mutually recursive."""
    doc = {
        "jobs": {
            "prep": {
                "outputs": {"token": "${{ secrets.DEPLOY_TOKEN }}"},
                "steps": [{"run": "echo ok"}],
            },
            "mid": {
                "needs": "prep",
                "env": {"E": "${{ needs.prep.outputs.token }}"},
                "outputs": {"forwarded": "${{ env.E }}"},
                "steps": [{"run": "echo ok"}],
            },
            "use": {
                "needs": "mid",
                "steps": [
                    {
                        "name": "leak",
                        "run": 'curl -H "Authorization: Bearer ${{ needs.mid.outputs.forwarded }}" https://x',
                    }
                ],
            },
        }
    }
    assert ("mid", "forwarded") in _secret_output_keys(doc)
    assert _offending_steps(doc) == ["leak"]


def test_offender_scan_covers_hyphenated_env_keys_and_whole_context_dumps():
    """A hyphenated env key is a valid read (`env.MY-TOKEN`), and a whole-context
    dump (`env`, `toJSON(needs)`, `needs.j.outputs`) leaks every secret it holds."""
    hyphen = {
        "jobs": {
            "j": {
                "env": {"MY-TOKEN": "${{ secrets.CLOUDFLARE_API_TOKEN }}"},
                "steps": [{"name": "leak", "run": 'curl "${{ env.MY-TOKEN }}"'},
                          {"name": "plain", "run": 'curl "${{ env.OTHER }}"'},
                ],
            }
        }
    }
    assert _offending_steps(hyphen) == ["leak"]

    dumps = {
        "jobs": {
            "prep": {
                "outputs": {"token": "${{ secrets.DEPLOY_TOKEN }}"},
                "steps": [{"run": "echo ok"}],
            },
            "use": {
                "needs": "prep",
                "env": {"K": "${{ secrets.DEPLOY_TOKEN }}"},
                "steps": [
                    {"name": "dump env", "run": 'echo "${{ toJSON(env) }}"'},
                    {"name": "dump needs", "run": 'echo "${{ toJSON(needs) }}"'},
                    {"name": "dump outputs", "run": 'echo "${{ needs.prep.outputs }}"'},
                ],
            },
        }
    }
    assert _offending_steps(dumps) == ["dump env", "dump needs", "dump outputs"]
    # A non-secret env key read as a normal reference is not a dump and not flagged.
    plain = {
        "jobs": {
            "j": {
                "env": {"K": "value"},
                "steps": [{"name": "plain", "run": 'echo "${{ env.K }}"'},
                          {"name": "plain outputs", "run": 'echo "${{ toJSON(needs) }}"'},
                ],
            }
        }
    }
    assert _offending_steps(plain) == []

    # A whole-context dump at a DEFINITION layer must resolve too.
    dump_defs = {
        "env": {"S": "${{ secrets.DEPLOY_TOKEN }}"},
        "jobs": {
            "prep": {
                "outputs": {"dump": "${{ toJSON(env) }}"},
                "steps": [{"run": "echo ok"}],
            },
            "use": {
                "needs": "prep",
                "steps": [
                    {"name": "leak", "run": 'curl "${{ needs.prep.outputs.dump }}"'},
                ],
            },
        },
    }
    assert _offending_steps(dump_defs) == ["leak"]

    env_from_dump = {
        "env": {"S": "${{ secrets.DEPLOY_TOKEN }}"},
        "jobs": {
            "j": {
                "env": {"K": "${{ toJSON(env) }}"},
                "steps": [{"name": "leak", "run": 'echo "${{ env.K }}"'},
                          {"name": "plain", "run": 'echo "${{ env.OTHER }}"'},
                ],
            }
        },
    }
    assert _offending_steps(env_from_dump) == ["leak"]


def test_sweep_enumerates_both_workflow_extensions(tmp_path):
    """GitHub executes `.yaml` too; globbing only `.yml` is an unscanned file."""
    (tmp_path / "a.yml").write_text("jobs: {}\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("jobs: {}\n", encoding="utf-8")
    assert set(_workflow_docs(tmp_path)) == {"a.yml", "b.yaml"}


def test_pending_fix_defers_only_the_exact_pinned_steps():
    """The deferral must not become a shield: it applies only when the file's
    offenders are exactly the pinned (job, step) identities — a new offender,
    even one reusing a pinned name, changes the set/count and fails."""

    def doc(job: str, names: list[str]) -> dict:
        return {
            "jobs": {
                job: {"steps": [{"name": n, "run": 'x="${{ secrets.A }}"'} for n in names]}
            }
        }

    pinned = _PENDING_FIX["deploy-hosted.yml"]
    exact = sorted(n for _job, n in pinned)
    assert len(set(exact)) == len(exact)
    good = _offending_identities(doc("deploy-api", exact))
    assert len(good) == len(pinned) and set(good) == pinned
    # A NEW offending step, a duplicate reusing a pinned name, and a pinned name
    # moved to another job are all NOT the pinned identity set.
    assert set(_offending_identities(doc("deploy-api", [*exact, "a brand new step"]))) != pinned
    assert len(_offending_identities(doc("deploy-api", [*exact, exact[0]]))) != len(pinned)
    assert set(_offending_identities(doc("other-job", exact))) != pinned


def test_no_secret_interpolated_into_run_text(workflow_docs):
    """No ``run:`` body may interpolate ``${{ … secrets.X … }}``.

    GitHub substitutes the value into the shell SOURCE before bash parses it: a
    JSON catalog's quotes are stripped (the #4334 outage) and ``$(…)`` /
    backticks execute. Bind the secret in ``env:`` and use ``"$VAR"`` instead.
    The sweep resolves the ``secrets`` context, secret-bound ``env`` keys
    (root/job/step, transitively) and secret-defined job outputs; the only
    deferral is the two ``deploy-hosted.yml`` steps pinned in ``_PENDING_FIX``,
    and only while that file's offenders are exactly those steps.
    """
    offenders: dict[str, list[str]] = {}
    for filename, doc in workflow_docs.items():
        bad = _offending_steps(doc)
        identities = _offending_identities(doc)
        pinned = _PENDING_FIX.get(filename)
        if (
            pinned is not None
            and identities
            and len(identities) == len(pinned)
            and set(identities) == pinned
        ):
            continue
        if bad:
            offenders[filename] = bad
    assert not offenders, (
        "these steps interpolate a secret into their run script: "
        + "; ".join(f"{f}: {names}" for f, names in sorted(offenders.items()))
        + ". GitHub substitutes '${{ … }}' into the shell TEXT before bash parses "
        "it: a JSON value's quotes are stripped (the #4334 outage) and "
        "'$(…)'/backticks execute. Bind the secret in `env:` and reference "
        '"$VAR" instead (#4361).'
    )


def test_fixed_steps_bind_secret_in_env_and_reference_it_quoted(workflow_docs):
    """Every #4361 site keeps the safe shape: bound in ``env:``, quoted in ``run:``.

    Four independent reverts are caught: re-interpolating the secret into the
    run text; binding the name to a DIFFERENT secret (cross-wired value);
    binding it but never reading the variable in the command (expands empty,
    secret silently dropped — a comment mention does not count); and reading it
    outside double quotes (word-splitting/globbing, or a single-quoted literal
    that never expands).
    """
    for (filename, prefix), env_vars in _FIXED_STEPS.items():
        step = _step(workflow_docs[filename], prefix)
        run = step.get("run", "")
        code = _scrub_run(run)

        secret_env = _resolve_secret_env_keys(step.get("env"))
        assert not _run_secret_uses(run, secret_env), (
            f"{filename}: {prefix!r} interpolates a secret into its run text again "
            "— bind it in `env:` and use \"$VAR\" (#4361)"
        )

        env = step.get("env") or {}
        for var in env_vars:
            bound = _ENV_SECRET.fullmatch(str(env.get(var, "")).strip())
            assert bound is not None, (
                f"{filename}: {prefix!r} must bind {var} in `env:` as "
                f"'${{{{ secrets.{var} }}}}' — got {env.get(var)!r}"
            )
            assert bound.group(1) == var, (
                f"{filename}: {prefix!r} binds env {var} to secrets."
                f"{bound.group(1)} — a cross-wired value reaches the command "
                "(#4361)"
            )
            assert re.search(rf"\$\{{{var}\}}|\${var}(?![A-Za-z0-9_])", code), (
                f"{filename}: {prefix!r} binds {var} in `env:` but its run "
                f"text never reads it — the secret is silently unused (#4361)"
            )
            unquoted = _references_outside_double_quotes(code, var)
            assert not unquoted, (
                f"{filename}: {prefix!r} reads {var} outside double quotes on "
                f"{unquoted!r} — an unquoted expansion is re-split/globbed, and a "
                f'single-quoted one never expands; use "${{{var}}}" (#4361)'
            )


def test_quote_scanner_flags_unquoted_and_single_quoted_expansions():
    """Regression on the scanner above — a silent mis-parse would let an
    unquoted expansion through the #4361 guard (same discipline as the #4334
    parser test: the guard must not be evadable by a spelling it misses)."""
    assert _references_outside_double_quotes('x "$V"', "V") == []
    assert _references_outside_double_quotes('x "${V}"', "V") == []
    assert _references_outside_double_quotes("x $V", "V") == ["x $V"]
    assert _references_outside_double_quotes("x '$V'", "V") == ["x '$V'"]
    assert _references_outside_double_quotes('x "$V" $V', "V") == ['x "$V" $V']
    # A longer identifier is a different variable — no false positive.
    assert _references_outside_double_quotes("x $VFOO", "V") == []
    # An escaped dollar is literal text, not an expansion.
    assert _references_outside_double_quotes("x \\$V", "V") == []
    # A double quote inside a SINGLE-quoted span must not desync the state.
    assert _references_outside_double_quotes('x \'a"b\' $V', "V") == ['x \'a"b\' $V']
    # An ESCAPED quote inside a double-quoted span is not a closing quote.
    assert _references_outside_double_quotes('x "a\\"b" $V', "V") == ['x "a\\"b" $V']
    # A comment-only line is skipped in both directions …
    assert _references_outside_double_quotes('# "$V" $V\ncmd "$V"', "V") == []
    # … and an unclosed quote on one line must not desync the next.
    assert _references_outside_double_quotes('echo "unclosed\ncmd "$V"', "V") == []


def test_comment_lines_cannot_satisfy_the_reference_pin():
    """A comment-only mention is not a use — a body whose only `$VAR` mention is
    a TODO comment must fail the reference pin, not satisfy it."""
    run = '# TODO restore -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"\ncurl -s "$URL"\n'
    assert "$CLOUDFLARE_API_TOKEN" not in _scrub_run(run)
    assert _references_outside_double_quotes(run, "CLOUDFLARE_API_TOKEN") == []

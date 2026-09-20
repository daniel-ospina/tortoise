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
``env:``, referenced quoted) so a partial revert fails loudly. The one deferral
is the two ``deploy-hosted.yml`` steps pinned in ``_PENDING_FIX`` until the
in-flight #4334 fix (PR #4357) lands.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# A secret context inside an expression. The runner resolves context names
# case-insensitively (OrdinalIgnoreCase), so `SECRETS.FOO`/`Secrets.FOO` are
# matched too; indexed syntax (`secrets['FOO']`, `secrets[matrix.name]`) is
# matched by the `[.\[]` class.
_SECRET_CONTEXT = re.compile(r"\bsecrets\s*[.\[]", re.IGNORECASE)
# A step env binding to a single secret: `KEY: ${{ secrets.KEY }}`.
_ENV_SECRET = re.compile(r"\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}", re.IGNORECASE)


def _secret_interpolations(text: str) -> list[str]:
    """Every ``${{ … }}`` span in ``text`` that reads a secret context.

    Span extraction mirrors the Actions runner rather than a non-greedy regex:
    a ``}}`` inside a single-quoted string literal does NOT terminate the
    expression, so a secret following such a literal (e.g.
    ``${{ fromJSON('{"a":{"b":1}}').x + secrets.BAR }}``) is still seen. The
    non-greedy ``\\$\\{\\{.*?\\}\\}`` form truncated the span at the inner
    ``}}`` and never re-scanned the remainder — a fail-open for that spelling.
    """
    found: list[str] = []
    i = 0
    while True:
        start = text.find("${{", i)
        if start == -1:
            return found
        j = start + 3
        in_string = False
        while j < len(text):
            if text[j] == "'":
                in_string = not in_string
            elif not in_string and text.startswith("}}", j):
                break
            j += 1
        span = text[start : j + 2] if j < len(text) else text[start:]
        if _SECRET_CONTEXT.search(span):
            found.append(span)
        i = max(j + 2, start + 3)


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


# ``deploy-hosted.yml`` is owned by the in-flight #4334 fix (PR #4357) and this
# change deliberately does not touch it. The sweep defers only while that file's
# offenders are EXACTLY these two steps — identified by (job, step-name) and
# matched as an exact set AND count. A new interpolating step there, even one
# reusing a pinned NAME, changes the identity set or the count and is flagged,
# so the exemption cannot hide a fresh regression. Once #4357 lands the file has
# no offenders and is checked by the general path like any other workflow; the
# entry then becomes inert and is deleted by whichever PR lands second. (#4357
# also adds a dedicated step-level guard in ``tests/test_deploy_workflow.py``
# that walks deploy-hosted.yml's steps.)
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
        "Auto-file issue on bleed detection",
    ): ("GITHUB_TOKEN",),
    (
        "e2e-live-reconcile.yml",
        "Auto-file issue when the reconcile could not read counts",
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


def _run_bodies(doc: dict):
    """Yield ``(job_name, step_name, run_text)`` for every step with a run body."""
    for job_name, job in doc.get("jobs", {}).items():
        for step in job.get("steps", []):
            run = step.get("run")
            if isinstance(run, str):
                yield job_name, step.get("name") or "<unnamed>", run


def _offending_identities(doc: dict) -> list[tuple[str, str]]:
    """``(job, step)`` for every step whose run body interpolates a secret."""
    return [
        (job, name)
        for job, name, run in _run_bodies(doc)
        if _secret_interpolations(run)
    ]


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
    """#4361 re-review: a `}}` inside a single-quoted literal does not end the
    expression for the Actions runner, so a secret after it must still be seen —
    the non-greedy regex truncated the span and returned no offender."""
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


def test_sweep_enumerates_both_workflow_extensions(tmp_path):
    """GitHub executes `.yaml` too; globbing only `.yml` is an unscanned file."""
    (tmp_path / "a.yml").write_text("jobs: {}\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("jobs: {}\n", encoding="utf-8")
    assert set(_workflow_docs(tmp_path)) == {"a.yml", "b.yaml"}


def test_pending_fix_defers_only_the_exact_pinned_steps():
    """The in-flight exemption must not become a shield: it defers only when the
    file's offenders are EXACTLY the pinned (job, step) identities — a new
    offender, even one reusing a pinned name, changes the set/count and fails."""

    def doc(job: str, names: list[str]) -> dict:
        return {
            "jobs": {
                job: {"steps": [{"name": n, "run": 'x="${{ secrets.A }}"'} for n in names]}
            }
        }

    pinned = _PENDING_FIX["deploy-hosted.yml"]
    exact = sorted(n for _job, n in pinned)
    assert len(exact) == len(pinned)
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
    The only deferral is the two ``deploy-hosted.yml`` steps pinned in
    ``_PENDING_FIX`` (owned by in-flight PR #4357) — and only while that file's
    offenders are exactly those steps.
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

        assert not _secret_interpolations(run), (
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

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
``e2e-live-reconcile.yml``. This module is the standing guard for the class:
no step in ANY workflow may interpolate a secret into its ``run:`` body, and
the #4361 steps are pinned to the safe shape (bound in ``env:``, referenced
quoted) so a partial revert fails loudly.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# A GitHub Actions expression span: `${{ … }}`. Match the SPAN first (non-greedy,
# DOTALL) and then test its contents, so indexed/wrapped spellings are caught —
# `secrets['FOO']`, `secrets[matrix.name]`, `format('{0}', secrets.FOO)` all
# interpolate exactly like `secrets.FOO` but a `secrets\.` regex misses them.
_ACTION_EXPR = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)
_SECRET_CONTEXT = re.compile(r"\bsecrets\s*[.\[]")
# A step env binding to a single secret: `KEY: ${{ secrets.KEY }}`.
_ENV_SECRET = re.compile(r"\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}")


def _secret_interpolations(text: str) -> list[str]:
    """Every ``${{ … }}`` span in ``text`` that reads a secret context."""
    return [m.group(0) for m in _ACTION_EXPR.finditer(text) if _SECRET_CONTEXT.search(m.group(0))]


def _scrub_run(run: str) -> str:
    """Drop shell comment-only lines from a ``run:`` body.

    Prose must not be able to satisfy the reference pin, nor desync the quote
    scanner — a reviewer-verified evasion was a body whose only mention of the
    variable was `# TODO: restore -H "Bearer $TOKEN"`, which passed every
    assertion while the secret was never sent. Inline trailing comments are
    deliberately not stripped: telling one from a `#` inside a string needs a
    shell parser, and a variable on a line that also carries the consuming
    command is exactly what the pin asserts.
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
# change deliberately does not touch it. The sweep shields ONLY the two pre-fix
# steps that PR rewrites — a NEW interpolating step in that file is still
# flagged, so the exemption cannot hide a fresh regression. Once #4357 lands the
# file has no offenders and is checked by the general path like any other
# workflow; the entries then become inert and are deleted by whichever PR lands
# second. (#4357 also adds a dedicated step-level guard in
# ``tests/test_deploy_workflow.py`` that walks deploy-hosted.yml's steps.)
_PENDING_FIX: dict[str, frozenset[str]] = {
    "deploy-hosted.yml": frozenset(
        {
            "Verify secrets exist",
            "Set all app secrets on Fly.io (keeps in sync with GitHub/Supabase)",
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
    """Yield ``(step_name, run_text)`` for every step that has a run body."""
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            run = step.get("run")
            if isinstance(run, str):
                yield step.get("name") or "<unnamed>", run


def _offending_steps(doc: dict) -> list[str]:
    """Steps whose run body interpolates a secret into the shell text."""
    return [name for name, run in _run_bodies(doc) if _secret_interpolations(run)]


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


def test_sweep_enumerates_both_workflow_extensions(tmp_path):
    """GitHub executes `.yaml` too; globbing only `.yml` is an unscanned file."""
    (tmp_path / "a.yml").write_text("jobs: {}\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("jobs: {}\n", encoding="utf-8")
    assert set(_workflow_docs(tmp_path)) == {"a.yml", "b.yaml"}


def test_pending_fix_shields_only_the_pinned_steps():
    """The in-flight exemption must not become a shield for a NEW offender in
    the same file — only the exact pre-fix step names are deferred."""
    pinned = _PENDING_FIX["deploy-hosted.yml"]
    shielded = {
        "jobs": {"j": {"steps": [{"name": n, "run": 'x="${{ secrets.A }}"'} for n in pinned]}}
    }
    assert set(_offending_steps(shielded)) <= pinned
    with_new = {
        "jobs": {
            "j": {
                "steps": [{"name": n, "run": 'x="${{ secrets.A }}"'} for n in pinned]
                + [{"name": "a brand new step", "run": "echo ${{ secrets.B }}"}]
            }
        }
    }
    assert not set(_offending_steps(with_new)) <= pinned


def test_no_secret_interpolated_into_run_text(workflow_docs):
    """No ``run:`` body in ANY workflow may interpolate ``${{ … secrets.X … }}``.

    GitHub substitutes the value into the shell SOURCE before bash parses it: a
    JSON catalog's quotes are stripped (the #4334 outage) and ``$(…)`` /
    backticks execute. Bind the secret in ``env:`` and use ``"$VAR"`` instead.
    """
    offenders: dict[str, list[str]] = {}
    for filename, doc in workflow_docs.items():
        bad = _offending_steps(doc)
        pinned = _PENDING_FIX.get(filename)
        # Skip only the file's pinned pre-fix steps (see _PENDING_FIX); a clean
        # file needs no skip, and a new offender is never shielded.
        if bad and pinned is not None and set(bad) <= pinned:
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
    """A comment-only mention is not a use — the reviewer-verified evasion where
    a body whose only `$VAR` mention was a TODO comment passed every pin."""
    run = '# TODO restore -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"\ncurl -s "$URL"\n'
    assert "$CLOUDFLARE_API_TOKEN" not in _scrub_run(run)
    assert _references_outside_double_quotes(run, "CLOUDFLARE_API_TOKEN") == []

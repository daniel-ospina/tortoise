"""Workflow ``run:`` secret-interpolation guard (#4361, same class as #4334).

GitHub substitutes ``${{ secrets.X }}`` into a step's shell **TEXT before bash
parses it**, so a secret value is *code*, not data:

- a value containing ``"`` is read as quote toggles and **removed** — exactly
  how the ``STRIPE_PRICE_IDS`` catalog was mangled (checkout dead, #4334);
- a value containing ``$(…)`` or backticks would **execute**.

The fix binds each secret in the step's ``env:`` block (``KEY: ${{ secrets.X }}``)
and reads it as a QUOTED shell variable (``"$KEY"``). An expanded variable's
contents are data, never re-parsed. ``env:`` blocks themselves are safe —
the substitution there is a YAML scalar, not shell source.

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

# Any secret interpolation at all — the #4334/#4361 hazard.
_SECRET_INTERP = re.compile(r"\$\{\{[^}]*secrets\.[A-Z0-9_]+[^}]*\}\}")
# A step env binding to a single secret: `KEY: ${{ secrets.KEY }}`.
_ENV_SECRET = re.compile(r"\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}")


def _references_outside_double_quotes(run: str, var: str) -> list[str]:
    """Shell lines where ``$var``/``${var}`` is NOT inside double quotes.

    A double-quoted expansion is data; a bare expansion is re-split on IFS and
    glob-expanded, so a value with spaces/specials corrupts the command. A
    single-quoted ``'$var'`` is also returned: a shell does not expand there,
    so the literal text is sent and the secret silently never arrives. Walks
    the text tracking single/double quote state and backslash escapes (inside
    single quotes a backslash is literal, per POSIX).
    """
    pattern = re.compile(rf"\$\{{{var}\}}|\${var}(?![A-Za-z0-9_])")
    bad: list[str] = []
    in_single = in_double = escaped = False
    i = 0
    while i < len(run):
        ch = run[i]
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
            match = pattern.match(run, i)
            if match:
                start = run.rfind("\n", 0, i) + 1
                end = run.find("\n", i)
                bad.append(run[start : end if end != -1 else len(run)].strip())
                i = match.end()
                continue
        i += 1
    return bad

# ``deploy-hosted.yml`` is owned by the in-flight #4334 fix (PR #4357) and is
# deliberately NOT touched here. While it still carries the pre-fix shape it is
# skipped by the sweep below; once that PR lands the file has no offenders and
# the general path checks it like any other. It additionally has a dedicated
# step-level guard in ``tests/test_deploy_workflow.py`` (#4334/#4357), so the
# class stays covered there in every state.
_PENDING_FIX = {
    "deploy-hosted.yml": "#4334 — PR #4357 (in flight; dedicated guard in tests/test_deploy_workflow.py)",
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


def _workflow_docs() -> dict[str, dict]:
    """Every workflow file, parsed (name → document)."""
    docs: dict[str, dict] = {}
    for path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        docs[path.name] = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert docs, f"no workflows found under {_WORKFLOWS_DIR}"
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


@pytest.fixture(scope="module")
def workflow_docs() -> dict[str, dict]:
    return _workflow_docs()


def _offending_steps(doc: dict) -> list[str]:
    """Steps whose run body interpolates a secret into the shell text."""
    return [name for name, run in _run_bodies(doc) if _SECRET_INTERP.search(run)]


def test_offender_scan_is_not_vacuous():
    """The sweep must actually flag an interpolated secret — a guard that
    can never fire is worse than none (it reads as coverage)."""
    unsafe = {
        "jobs": {
            "j": {
                "steps": [
                    {"name": "bad", "run": 'x="${{ secrets.SECRET_A }}"'},
                    {"name": "also-bad", "run": 'echo ${{ secrets.SECRET_B }}'},
                    {"name": "safe", "run": 'x="$SECRET_A"'},
                ]
            }
        }
    }
    assert _offending_steps(unsafe) == ["bad", "also-bad"]


def test_no_secret_interpolated_into_run_text(workflow_docs):
    """No ``run:`` body in ANY workflow may interpolate ``${{ … secrets.X … }}``.

    GitHub substitutes the value into the shell SOURCE before bash parses it: a
    JSON catalog's quotes are stripped (the #4334 outage) and ``$(…)`` /
    backticks execute. Bind the secret in ``env:`` and use ``"$VAR"`` instead.
    """
    offenders: dict[str, list[str]] = {}
    for filename, doc in workflow_docs.items():
        # A file owned by an in-flight fix is skipped only while it still
        # offends; once clean it is checked like any other.
        if filename in _PENDING_FIX and _offending_steps(doc):
            continue
        bad = _offending_steps(doc)
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

    Three independent reverts are caught: re-interpolating the secret into the
    run text, referencing a variable the step never bound (expands empty, so
    the secret is silently dropped), and dropping the quotes (word-splitting /
    glob expansion at the call site).
    """
    for (filename, prefix), env_vars in _FIXED_STEPS.items():
        step = _step(workflow_docs[filename], prefix)
        run = step.get("run", "")

        assert not _SECRET_INTERP.search(run), (
            f"{filename}: {prefix!r} interpolates a secret into its run text again "
            "— bind it in `env:` and use \"$VAR\" (#4361)"
        )

        env = step.get("env") or {}
        for var in env_vars:
            assert _ENV_SECRET.fullmatch(str(env.get(var, "")).strip()), (
                f"{filename}: {prefix!r} must bind {var} in `env:` as "
                f"'${{{{ secrets.{var} }}}}' — got {env.get(var)!r}"
            )
            assert re.search(rf"\$\{{{var}\}}|\${var}(?![A-Za-z0-9_])", run), (
                f"{filename}: {prefix!r} binds {var} in `env:` but its run "
                f"text never reads it — the secret is silently unused (#4361)"
            )
            unquoted = _references_outside_double_quotes(run, var)
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

"""Standing guard: a step that calls ``gh`` must have a token in scope (#3092).

`gh` does not inherit the Actions token implicitly. It reads ``GH_TOKEN`` (or
``GITHUB_TOKEN``) from the **environment**, and Actions exposes the token only as
a **context** (``github.token`` / ``secrets.GITHUB_TOKEN``) — never as an env var.
A job that calls ``gh`` with no ``env:`` binding therefore fails every
invocation with

    gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN
    environment variable.

``ci-timing.yml``'s ``refresh`` job was exactly that job: ``gh pr list`` and
``gh pr create`` with no token anywhere in its scope. ``gh`` exited non-zero on
every run, and because the result was consumed as
``if [ "$(gh pr list …)" = "0" ]`` the failure was read as "*a PR is already
open*" — so the job stayed GREEN and **no refresh PR was ever opened in the
workflow's history** (#3092). Two other workflows in this repo had already hit
the same class (``python-ci.yml`` and ``registry-backup-cron.yml``).

Fixing one file would leave the class open, so this module is the repo-wide
invariant: **every `gh` invocation, in every ``.github/workflows/*.{yml,yaml}``
job, must have a token in scope** — workflow-level ``env``, job ``env``, step
``env``, or an ``export`` inside the step body. The scanner is exercised against
synthetic documents (positive, negative and commented-out cases) so it cannot
pass vacuously.

The sibling ``test_no_token_expression_in_run_bodies`` covers the *opposite*
mistake, which the fix must not make: **spelling the token expression out inside
a `run:` body prints the live token into the job log.** GitHub substitutes
``${{ }}`` in a run body before bash parses it — including inside shell comments
and single quotes — so a helpful error message like

    echo "keep env: GH_TOKEN: ${{ github.token }} ..."

leaks the credential. The existing ``test_workflow_secret_interpolation.py``
guard bans the ``secrets`` context in run text but does NOT cover
``github.token``, which is a different context; that is the gap this covers.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# `gh <subcommand>` at a COMMAND POSITION: start of a line, or after a shell
# operator / substitution opener. Command position is required because these
# workflows legitimately spell `gh pr list` inside quoted `::error::` messages
# and inside prose comments — neither is a call. `command -v gh` (a capability
# probe, which needs no credential) does not match, since `-v` is not a
# subcommand.
#
# ⛔ CONTROL KEYWORDS ARE PART OF THE COMMAND POSITION. `if gh api …` and
# `while gh run download …` are real, token-requiring calls; a matcher without
# them misses both live sites (ai-review-gate.yml's `if gh api -H …` and
# python-ci.yml's `if gh run download …`). A guard with a hole in its matcher
# passes vacuously on exactly what it exists to catch.
#
# ⛔ A BACKTICK SUBSTITUTION IS ALSO A COMMAND POSITION — but only an UNESCAPED
# one. Bash executes `` `gh pr list` `` exactly as it executes `$(gh pr list)`, so
# dropping backticks from the position class leaves a real call site invisible.
# Escape is decided by an ODD run of backslashes (bash consumes them in pairs), so
# the lookbehind must skip `\`` but NOT `\\`` — a one-char lookbehind gets that
# backwards and misses every even-backslash site.
# ⛔ THE POSITION CLASS MUST COVER EVERY WAY BASH REACHES A COMMAND.
# Beyond the control keywords and backtick substitution, a command also begins
# after a `case`-arm/subshell `)`, after a redirection, and after a PREFIX WORD
# (`command`, `sudo`, `time`, `exec`, `nohup`, `builtin`, `nice`, `env`) or a
# `VAR=value` assignment prefix. A matcher missing those reports a token-less
# `gh` call as absent — a false NEGATIVE in a guard whose whole job is that
# call's credential. (`command -v gh` still does not match: `-v` is not a
# subcommand, and the position class only supplies what comes BEFORE `gh`.)
_GH_CALL = re.compile(
    r"(?:^|[|&;({)]|\$\(|(?<!\\)(?:\\\\)*`)\s*"
    r"(?:(?:if|then|elif|else|while|until|do|!)\s+"
    r"|(?:command|sudo|time|exec|nohup|builtin|nice|env)[^\S\n]+"
    # A redirection is a PREFIX, not a boundary: `< /dev/null gh api` has the
    # redirect TARGET where a command would be, so `<`/`>` cannot be a position.
    r"|\d*[<>]{1,2}&?[^\s]*[^\S\n]+"
    # A prefix assignment must be on the SAME line as the call: `\s+` here would
    # let the group swallow an intervening command and match its `gh`.
    r"|[A-Za-z_][A-Za-z0-9_]*=[^\s]*[^\S\n]+)*"
    r"gh\s+"
    r"(api|pr|issue|run|release|repo|secret|variable|workflow|auth|label|"
    r"milestone|search|gist|project|codespace|extension|cache|attestation)\b",
    re.M,
)
_TOKEN_KEYS = {"GH_TOKEN", "GITHUB_TOKEN"}
# A token EXPORTED inside the body itself is in scope — a non-exported assignment
# is not, because bash does not put it in a child's environment, so a later `gh`
# would run unauthenticated while the guard claimed it was covered.
_TOKEN_EXPORT = re.compile(r"^\s*export\s+(GH_TOKEN|GITHUB_TOKEN)=", re.M)
# ...but a PREFIX assignment on the same command IS the child's environment
# (`GH_TOKEN=x gh pr list`).
_TOKEN_PREFIX = re.compile(
    r"(?:^|[;&|(]|\$\()[^\S\n]*(GH_TOKEN|GITHUB_TOKEN)=[^\s]*[^\S\n]+"
    r"(?:\S+[^\S\n]+)*gh[^\S\n]",
    re.M,
)
# The token CONTEXTS must never appear in run text (see module docstring).
# Case-insensitive and index-form aware, because the runner resolves context names
# case-insensitively and `github['token']` is the same context as `github.token` —
# a dot-form-only pattern misses both.
_TOKEN_CONTEXT = re.compile(
    r"\bgithub\s*\.\s*token\b"
    r"|\bgithub\s*\[\s*['\"]token['\"]\s*\]"
    r"|\bsecrets\s*\.\s*GITHUB_TOKEN\b"
    r"|\bsecrets\s*\[\s*['\"]GITHUB_TOKEN['\"]\s*\]",
    re.IGNORECASE,
)

# ⛔ SECOND-ORDER LEAK: the token can also be reached through the `env` CONTEXT.
# Binding the token in `env:` is correct; *reading it back* as `${{ env.GH_TOKEN }}`
# inside a `run:` body expands into the shell text exactly like naming the token
# context directly, so the binding is what matters and not the name.
# The `env` context is read INSIDE an Actions expression, so the expression is
# delimited first and the context looked for within it. Matching `env.NAME` in the
# raw body instead would read the FILENAME `.env.example` (`python-ci.yml`'s
# concurrency guard) as a context read.
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_ENV_IN_EXPR = re.compile(
    r"\benv\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\b"
    # only when it is the whole expression
    r"|\benv\s*$"
    # a dynamic or quoted index cannot be resolved statically — treat it as
    # reaching the whole context
    r"|\benv\s*\["
    r"|\btoJSON\s*\(\s*env\s*\)",
    re.IGNORECASE,
)
# The same, for the LHS of an env chain (`A: ${{ env.B }}`). Kept in step with
# `_ENV_IN_EXPR`: a chain whose value uses an expression or index form must resolve
# too, or the binding is invisible and a body read of it is not flagged.
_ENV_REF_IN_VALUE = re.compile(
    r"\benv\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\b"
    r"|\benv\s*\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]",
    re.IGNORECASE,
)


def _env_names_read_in_body(body: str) -> tuple[set[str], bool]:
    """(env keys a run body reaches through the `env` CONTEXT, whole-context read)."""
    names: set[str] = set()
    dump = False
    for expr in _EXPRESSION.finditer(body):
        for m in _ENV_IN_EXPR.finditer(expr.group(1)):
            key = m.group(1)
            if key:
                names.add(key.upper())
            else:
                dump = True
    return names, dump


def _env_refs_in_value(value: str) -> set[str]:
    """Env keys one env VALUE reads — the edge of the chain graph."""
    refs: set[str] = set()
    for expr in _EXPRESSION.finditer(value):
        for m in _ENV_REF_IN_VALUE.finditer(expr.group(1)):
            refs.add((m.group(1) or m.group(2)).upper())
    return refs

# A secret/passed-input whose NAME marks it as a credential. The binding resolver
# must treat these as token-bearing, or `env: GH_TOKEN: ${{ secrets.GH_TOKEN }}`
# (the ordinary reusable-workflow spelling) resolves to no token at all.
_TOKEN_BEARING_VALUE = re.compile(
    r"\bsecrets\s*\.\s*[A-Za-z0-9_]*TOKEN[A-Za-z0-9_]*\b"
    r"|\binputs\s*\.\s*[A-Za-z0-9_]*token[A-Za-z0-9_]*\b",
    re.IGNORECASE,
)


def _token_bound_env_keys(*mappings: dict | None) -> set[str]:
    """Env keys whose VALUE resolves to the job token — the spellings that make an
    `env`-context read of that key a leak.

    Resolved to a FIXPOINT so an env chain (`env: A: ${{ env.B }}`, B token-bound)
    and a credential-named secret/input both count; a one-level check misses them.
    """
    flat = {str(k).upper(): str(v) for mapping in mappings for k, v in (mapping or {}).items() if v is not None}
    bound = {
        k for k, v in flat.items()
        if _TOKEN_CONTEXT.search(v) or _TOKEN_BEARING_VALUE.search(v)
    }
    changed = True
    while changed:
        changed = False
        for k, v in flat.items():
            if k in bound:
                continue
            if _env_refs_in_value(v) & bound:
                bound.add(k)
                changed = True
    return bound


# SCOPE BOUNDARY — what this guard deliberately does NOT cover, and why:
#   * `gh` calls inside `.github/scripts/*.sh`. Their credential comes from the
#     INVOKING step's env, so in principle a token-less step running a
#     gh-calling script has this defect. In practice a static reference scan is
#     NOT a reliable detector: the scripts mostly *mention* `gh` in human-facing
#     messages (deploy-bypass.sh prints "clear it: gh variable delete X"),
#     workflows reference script NAMES as pattern strings and `isfile()` lists
#     (ci.yml:285-287, :390), and some gh-calling scripts are test HARNESSES that
#     stub `gh` and need no credential. A name-match scan of `.github/scripts/*.sh`
#     yields ~12 false positives on this tree, which is worse than no check — a
#     noisy guard gets bypassed. Detecting it properly needs invocation parsing
#     (bash/sh/source plus a resolved path), not a name match.
#   * The SWALLOW shape itself — consuming a `gh` result inside a bare command
#     substitution used as a truth test. That is a per-CALL-SITE property, not
#     per-call: a token-covered call can still be misread, so it cannot be
#     decided from the call alone. The control for it is per-site
#     (`tests/test_ci_timing.py::test_refresh_step_does_not_infer_a_skip_from_a_
#     failed_query`, which replays the real step body). THIS guard covers the
#     TRIGGER — a `gh` call with no token in scope — which is the property that
#     made the whole class possible at once.
#   * The `with:` inputs of third-party actions (e.g. a shared workflow action
#     taking a `github-token`), which are not `run:` bodies at all.


def _workflow_paths() -> list[Path]:
    """`.yaml` is executed by GitHub too, so globbing only `.yml` would leave a
    silently unscanned file (same reasoning as the secret-interpolation guard)."""
    return sorted(set(WORKFLOW_DIR.glob("*.yml")) | set(WORKFLOW_DIR.glob("*.yaml")))


def _shell_code(body: str) -> str:
    """Executable lines only — full-line shell comments removed, so a commented-out
    `gh` call is not mistaken for a live one."""
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))


def _has_token(mapping: dict | None) -> bool:
    return bool({str(k).upper() for k in (mapping or {})} & _TOKEN_KEYS)


def _token_in_scope(doc_env: dict | None, job: dict, step: dict, body: str) -> bool:
    """Workflow-root env, job env, step env, an in-body export, or a prefix
    assignment on the same command."""
    code = _shell_code(body)
    return (
        _has_token(doc_env)
        or _has_token(job.get("env"))
        or _has_token(step.get("env"))
        or bool(_TOKEN_EXPORT.search(code))
        or bool(_TOKEN_PREFIX.search(code))
    )


def gh_call_sites(doc: dict, *, source: str = "<doc>") -> list[dict]:
    """Every `gh` call written directly in a `run:` body, with whether a token is
    in scope.

    Takes a PARSED document so the scanner itself can be tested against synthetic
    workflows (mutation tests below) without touching the real tree. See the
    SCOPE BOUNDARY note above for what it deliberately does not scan.
    """
    sites: list[dict] = []
    doc_env = doc.get("env")
    for job_name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            body = step.get("run")
            if not body:
                continue
            code = _shell_code(body)
            for match in _GH_CALL.finditer(code):
                sites.append({
                    "source": source,
                    "job": job_name,
                    "step": step.get("name") or "(unnamed)",
                    "subcommand": match.group(1),
                    "match": match.group(0),
                    "covered": _token_in_scope(doc_env, job or {}, step, body),
                })
    return sites


def _all_sites() -> list[dict]:
    sites: list[dict] = []
    for path in _workflow_paths():
        doc = yaml.safe_load(path.read_text()) or {}
        sites.extend(gh_call_sites(doc, source=path.name))
    return sites


# ── the invariant ────────────────────────────────────────────────────────

def test_every_gh_call_has_a_token_in_scope() -> None:
    """THE INVARIANT. A `gh` call with no credential in scope is a call that can
    only ever fail — and in ci-timing.yml's refresh job that failure was then read
    as success for the workflow's entire history (#3092)."""
    offenders = [s for s in _all_sites() if not s["covered"]]
    detail = "\n".join(
        f"  {s['source']} :: job={s['job']} :: step={s['step']} :: gh {s['subcommand']}"
        for s in offenders
    )
    assert offenders == [], (
        "these `gh` calls have no GH_TOKEN/GITHUB_TOKEN in scope (workflow env, job "
        "env, step env, or an in-body export), so `gh` will refuse to run inside "
        "Actions:\n" + detail
    )


def test_the_scanner_finds_the_known_call_sites() -> None:
    """Vacuity guard: a scanner that matched nothing would pass the invariant
    above trivially. Pin the sites the repo actually has."""
    found = {(s["source"], s["job"]) for s in _all_sites() if s["covered"]}
    assert ("ci-timing.yml", "measure") in found, sorted(found)
    assert ("ci-timing.yml", "refresh") in found, (
        "the refresh job's gh calls must be found AND covered (#3092)"
    )
    assert ("ai-review-gate.yml", "ai-review-gate") in found
    assert ("finding-provenance.yml", "provenance") in found
    assert ("python-ci.yml", "canary-streak") in found
    total = len(_all_sites())
    # Vacuity floor at today's count (15), not an equality pin: the count was 13
    # before the control-keyword fix, so a regression to the old matcher fails
    # here. Adding a NEW `gh` call legitimately raises this number without
    # re-pinning; the POSITION pins
    # (`test_the_two_control_keyword_sites_exist_in_the_real_tree`, below) are what
    # guard the matcher itself.
    assert total >= 15, (
        f"scanner found only {total} gh call sites — the pattern has lost coverage "
        "(the pre-fix pattern found 13 and missed `if gh …` positions)"
    )


def test_ci_timing_refresh_specifically_has_the_token() -> None:
    """Pin the issue's own site by name so a revert of the `env:` block fails here
    even if another job happens to keep the invariant true."""
    doc = yaml.safe_load((WORKFLOW_DIR / "ci-timing.yml").read_text())
    assert _has_token((doc["jobs"]["refresh"]).get("env")), (
        "ci-timing.yml's refresh job lost its GH_TOKEN env binding (#3092)"
    )


# ── scanner mutation tests (the guard must actually bite) ────────────────

def _doc(step_body: str, *, wf_env=None, job_env=None, step_env=None) -> dict:
    step: dict = {"name": "s", "run": step_body}
    if step_env is not None:
        step["env"] = step_env
    job: dict = {"steps": [step]}
    if job_env is not None:
        job["env"] = job_env
    doc: dict = {"jobs": {"j": job}}
    if wf_env is not None:
        doc["env"] = wf_env
    return doc


def test_scanner_flags_a_gh_call_with_no_token() -> None:
    """The pre-fix shape of the refresh job must be REPORTED."""
    doc = _doc(
        "set -e\n"
        "if [ \"$(gh pr list --head b --state open --json number --jq 'length')\" = \"0\" ]; then\n"
        "  gh pr create --base main --head b\n"
        "fi\n"
    )
    sites = gh_call_sites(doc)
    assert len(sites) == 2, sites
    assert all(not s["covered"] for s in sites), sites


@pytest.mark.parametrize(
    "kwargs",
    [
        {"wf_env": {"GH_TOKEN": "${{ github.token }}"}},
        {"wf_env": {"GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}},
        {"job_env": {"GH_TOKEN": "${{ github.token }}"}},
        {"step_env": {"GH_TOKEN": "${{ github.token }}"}},
        # lower-case key: the runner's context lookup is case-insensitive
        {"job_env": {"gh_token": "${{ github.token }}"}},
    ],
)
def test_scanner_accepts_a_token_from_every_scope(kwargs: dict) -> None:
    sites = gh_call_sites(_doc("gh api repos/o/r/pulls\n", **kwargs))
    assert len(sites) == 1, sites
    assert sites[0]["covered"] is True, sites


def test_scanner_accepts_a_token_exported_in_the_body() -> None:
    sites = gh_call_sites(_doc("export GH_TOKEN=abc\ngh api repos/o/r\n"))
    assert sites and sites[0]["covered"] is True, sites


def test_token_scope_distinguishes_export_from_a_bare_assignment() -> None:
    """`export GH_TOKEN=…` reaches the child; a bare assignment on its own line does
    NOT, so a later `gh` runs unauthenticated — the guard must not call that
    covered. A PREFIX assignment on the SAME command IS the child's environment."""
    bare = _doc("GH_TOKEN=abc\necho hi\ngh api repos/o/r\n")
    sites = gh_call_sites(bare)
    assert len(sites) == 1 and sites[0]["covered"] is False, sites

    prefix = _doc("GH_TOKEN=abc gh api repos/o/r\n")
    sites = gh_call_sites(prefix)
    assert len(sites) == 1 and sites[0]["covered"] is True, sites


def test_scanner_ignores_a_commented_out_gh_call() -> None:
    """A commented invocation cannot fail, so it must not be reported."""
    doc = _doc("# gh pr create --base main\n#   gh pr list --head b\necho hi\n")
    assert gh_call_sites(doc) == []


@pytest.mark.parametrize(
    "body",
    [
        "if gh api -H x repos/o/r/pulls\n",
        "if ! gh run download 42 -n a\n",
        "then gh pr list --head b\n",
        "elif gh api repos/o/r\n",
        "while gh api repos/o/r\n",
        "until gh pr view 1\n",
        "do gh api repos/o/r\n",
        "echo hi && gh api repos/o/r\n",
        "x=$(gh api repos/o/r)\n",
        # a BACKTICK substitution is a command position too (bash runs it)…
        "x=`gh api repos/o/r`\n",
        # …and escaping is decided by an ODD run of backslashes: one backslash
        # escapes, two leave the backtick live and bash substitutes it.
        "x=\\\\`gh api repos/o/r\\\n",
        # …a `)` (case arm / subshell close), a redirect, and the common command
        # PREFIXES — each of which bash treats as a command boundary.
        "case $x in a) gh api repos/o/r ;; esac\n",
        "command gh api repos/o/r\n",
        "sudo gh api repos/o/r\n",
        "time gh api repos/o/r\n",
        "env FOO=1 gh api repos/o/r\n",
        "</dev/null gh api repos/o/r\n",
    ],
)
def test_scanner_catches_a_gh_call_behind_a_control_keyword(body: str) -> None:
    """`if gh api …` and `while gh run download …` are REAL, token-requiring calls.

    A matcher that requires the call to start the line or follow an operator
    misses a whole position class, so it passes vacuously on the very thing it
    exists to catch: ai-review-gate.yml's `if gh api -H "Accept: …diff" …` and
    python-ci.yml's `if gh run download "$rid" …`.
    """
    sites = gh_call_sites(_doc(body))
    assert len(sites) == 1, f"control-keyword call not detected: {body!r} -> {sites}"
    assert sites[0]["covered"] is False, sites


def test_the_two_control_keyword_sites_exist_in_the_real_tree() -> None:
    """Pin the two real sites that motivated the control-keyword fix BY POSITION,
    not by count.

    A count equality is weaker than a text pin: `len(ai) >= 2` / `len(ci) >= 3`
    are exactly today's counts, so one NEW `gh` call in either file would mask a
    regression of the `if gh` position — the very thing the fix added. The
    assertion is therefore on the matched text.
    """
    sites = _all_sites()
    ai = [s for s in sites if s["source"] == "ai-review-gate.yml"]
    ci = [s for s in sites if s["source"] == "python-ci.yml"]
    assert any("if gh" in s["match"] for s in ai), (
        f"ai-review-gate.yml's `if gh api …` was not matched by position: {ai}"
    )
    assert any("if gh" in s["match"] for s in ci), (
        f"python-ci.yml's `if gh run download …` was not matched by position: {ci}"
    )
    assert all(s["covered"] for s in ai + ci), "both are token-covered"


def test_scanner_distinguishes_escaped_from_live_backticks() -> None:
    """Bash escapes on an ODD run of backslashes, consuming them in pairs, so a
    one-character lookbehind classifies a two-backslash run as escaped and misses a
    call bash really runs. The repo's own diagnostics use a single backslash."""
    bs = "\\"
    # one backslash: the backtick is literal, no call
    assert gh_call_sites(_doc('echo "' + bs + '`gh pr list' + bs + '`"\n')) == []
    # two: the run is even, so bash substitutes — this is a real call site
    assert len(gh_call_sites(_doc('echo "' + bs * 2 + '`gh pr list`"\n'))) == 1
    # three: odd again, escaped
    assert gh_call_sites(_doc('echo "' + bs * 3 + '`gh pr list' + bs * 3 + '`"\n')) == []


def test_scanner_ignores_a_quoted_mention_and_a_capability_probe() -> None:
    """Both appear in the real tree: the refresh step names `gh pr list` in its own
    ::error:: diagnostics, and ai-review-gate.yml probes `command -v gh`."""
    doc = _doc(
        # The repo's own diagnostics escape the backticks, so bash never runs the
        # named command; an UNESCAPED backtick in a double-quoted string would be a
        # real substitution and is covered by the parametrised positive case below.
        "echo \"::error:: \\`gh pr list\\` returned nothing\"\n"
        'echo "keep env: GH_TOKEN on the job"\n'
        "if command -v gh >/dev/null 2>&1; then :; fi\n"
    )
    assert gh_call_sites(doc) == []
    # ...but the same text at a command position IS a call
    assert len(gh_call_sites(_doc('echo hi\ngh pr list --head b\n'))) == 1


# ── the opposite mistake: leaking the token into the log ─────────────────

def test_no_token_expression_in_run_bodies() -> None:
    """GitHub substitutes `${{ }}` into a `run:` body BEFORE bash parses it —
    including inside shell comments and single quotes. So writing the token
    expression in a run body (even inside an `echo` explaining how to fix a
    missing token) prints the live credential into the public job log.

    The token expression belongs in an `env:`/`with:` value, where substitution
    is a YAML scalar rather than shell source."""
    offenders: list[str] = []
    for path in _workflow_paths():
        doc = yaml.safe_load(path.read_text()) or {}
        root_env = doc.get("env")
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                body = step.get("run") or ""
                where = f"  {path.name} :: job={job_name} :: {step.get('name') or '(unnamed)'} :: "
                for hit in {m.group(0) for m in _TOKEN_CONTEXT.finditer(body)}:
                    offenders.append(where + hit)
                # Second-order form: the token is bound in `env:` and the body
                # reads that KEY back through the `env` context.
                token_keys = _token_bound_env_keys(root_env, job.get("env"), step.get("env"))
                if token_keys:
                    names, dump = _env_names_read_in_body(body)
                    for hit in sorted(names & token_keys):
                        offenders.append(f"{where}${{{{ env.{hit} }}}} (reads a token-bound env key)")
                    if dump:
                        offenders.append(
                            f"{where}dumps the whole `env` context, which carries "
                            f"{sorted(token_keys)}"
                        )
    assert offenders == [], (
        "these `run:` bodies contain the job-token expression, which GitHub "
        "expands into the step's shell text — printing the live token into the log. "
        "Reference a variable bound in `env:` instead:\n" + "\n".join(offenders)
    )


def test_token_context_guard_catches_its_own_mistake() -> None:
    """Mutation check: the leak detector must flag the exact string the fix's error
    message was ALMOST written with, in every spelling, and must not flag the safe
    form."""
    for leak in [
        'echo "keep env: GH_TOKEN: ${{ github.token }}"',
        'echo "keep secret ${{ secrets.GITHUB_TOKEN }}"',
        # index form and case variants — the runner resolves contexts
        # case-insensitively, and these are the same two contexts
        "echo \"${{ github['token'] }}\"",
        'echo "${{ github[ \'token\' ] }}"',
        'echo "${{ GITHUB.TOKEN }}"',
        'echo "${{ secrets.github_token }}"',
    ]:
        assert _TOKEN_CONTEXT.search(leak), f"leak not detected: {leak}"
    # the safe form binds the key in `env:` and names the KEY in the message
    assert not _TOKEN_CONTEXT.search('echo "keep env: GH_TOKEN on the job"')


def test_the_env_context_reader_catches_the_second_order_leak() -> None:
    """Mutation check for the second-order detector: a token bound in `env:` and
    then read back through the `env` context is the same leak in another spelling,
    and the safe form must not be flagged."""
    assert _token_bound_env_keys({"GH_TOKEN": "${{ github.token }}"}) == {"GH_TOKEN"}
    assert _token_bound_env_keys({"GH_TOKEN": "stub"}) == set()
    assert _token_bound_env_keys(None) == set()
    # A credential-named secret/input is token-bearing even though it is not
    # literally `github.token` — the ordinary reusable-workflow spelling.
    assert _token_bound_env_keys({"TOK": "${{ secrets.GH_TOKEN }}"}) == {"TOK"}
    assert _token_bound_env_keys({"TOK": "${{ secrets.MY_TOKEN }}"}) == {"TOK"}
    # ...and an env CHAIN resolves to a fixpoint: B is bound, so A (which reads B)
    # is bound too — including when the chain's VALUE is a larger expression or an
    # index, which must resolve exactly as a body read does.
    assert _token_bound_env_keys(
        {"A": "${{ env.B }}", "B": "${{ github.token }}"}
    ) == {"A", "B"}
    assert _token_bound_env_keys(
        {"A": "${{ env.B || '' }}", "B": "${{ github.token }}"}
    ) == {"A", "B"}
    assert _token_bound_env_keys(
        {"A": "${{ env['B'] }}", "B": "${{ secrets.MY_TOKEN }}"}
    ) == {"A", "B"}
    # a self-reference must not spin forever
    assert _token_bound_env_keys({"A": "${{ env.A }}"}) == set()
    for leak in [
        'echo "${{ env.GH_TOKEN }}"',
        "echo \"${{ env['GH_TOKEN'] }}\"",
        'echo "${{ env.gh_token }}"',
        'echo "${{ toJSON(env) }}"',
        'echo "${{ env }}"',
        # the key as a TOKEN inside a larger expression, not the whole of one
        "echo \"${{ env.GH_TOKEN || '' }}\"",
        "echo \"${{ format('{0}', env.GH_TOKEN) }}\"",
        "echo \"${{ env[env.NAME] }}\"",
    ]:
        names, dump = _env_names_read_in_body(leak)
        assert dump or "GH_TOKEN" in names, f"second-order leak not detected: {leak}"
    # the safe form never reads the env context at all
    names, dump = _env_names_read_in_body('echo "keep env: GH_TOKEN on the job"')
    assert (names, dump) == (set(), False)
    # an unrelated env key is reported but is not a token key
    assert _env_names_read_in_body('echo "${{ env.SAFE_FLAG }}"') == ({"SAFE_FLAG"}, False)
    # ⛔ A FILENAME IS NOT A CONTEXT READ. `python-ci.yml`'s concurrency guard greps
    # `.env.example`; reading `env.NAME` out of the raw body would invent the key
    # `EXAMPLE` and fail the guard on an unrelated change.
    assert _env_names_read_in_body('grep -q .env.example <<< "$x"') == (set(), False)
    assert _env_names_read_in_body('cat .env.production') == (set(), False)
    assert not _TOKEN_CONTEXT.search('echo "the token context must not be inlined"')

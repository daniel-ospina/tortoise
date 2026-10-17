"""The e2e `Pre-warm wrangler toolchain` steps stay pinned AND retry-protected.

WHY THIS EXISTS
---------------
On 2026-10-15 the required `legal-e2e` check went red before it ran a single
assertion: the pre-warm step's first `npx --yes wrangler@4 --version` died with

    npm error network Invalid response body while trying to fetch
    https://registry.npmjs.org/wrangler: read ECONNRESET

a transient npm-registry reset on a cold runner. The step had no retry, so the
flakiest part of the job — a network fetch — was also its least protected, and
the retry that DOES exist lives in the boot loop below, which the failed
pre-warm never reached. A cold-runner fetch is exactly the thing a bounded retry
is for, so the step now retries (3 attempts, best-effort: the boot loop is the
real gate and surfaces wrangler's own log).

The pre-warm also drifted from what the boot fetched, in two shapes. In
`welcome-e2e` the pre-warm floated `wrangler@4` while the boot step pinned
`4.127.0`; in `dashboard-e2e` BOTH the pre-warm and its two boot steps floated
`wrangler@4` (`legal-e2e` pinned `4.127.0` on both sides). Either shape warms a
DIFFERENT tarball than the one the boot fetches — the agent-infra#414 drift
class — and a range also made npm download the whole packument to resolve it.
The two versions must agree.

WHAT THIS PINS (a gate is worth what its wiring is worth — the #4311 convention)
  * every `Pre-warm wrangler toolchain` step pins an EXACT version
    (`wrangler@X.Y.Z`), never a floating `wrangler@4`;
  * the pinned version matches the version EVERY boot step of the SAME job
    starts, so the warm-up warms the tarball that runs (`legal-e2e` and
    `dashboard-e2e` each start TWO previews — a union lets a still-pinned
    sibling mask a drifted one);
  * the step is bounded-retry-protected (`for attempt in 1 2 3`);
  * the three e2e jobs that serve `website/` still HAVE the step — deleting it
    would silently push the cold fetch back onto the boot budget that #2178
    added it to protect.

This is parsed YAML, not a raw-text scan: the step's `run` string is read from
the loaded document, so a commented-out or renamed step cannot satisfy it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

PREWARM_STEP = "Pre-warm wrangler toolchain"

#: A token is classified by its DOT COUNT, never by a lookahead that backtracks
#: mid-token (a lookahead happily matched `4.12` inside `4.127.0`): an EXACT pin
#: is `4.127.0`, and anything else — `4`, `4.127`, `latest`, `next` — is floating.
#: A dist-tag is an OFFENDER, not a non-match: the numeric-only form of this
#: check could not see `wrangler@latest` at all, so such a step contributed
#: nothing to the float/parity checks and was invisible to them.


#: A shell line CONTINUATION removes the backslash AND the newline, so the two
#: lines are one command line: an invocation split across them is still one
#: invocation and must be matched as one.
_CONTINUATION = re.compile(r"\\\n")

#: A whole-line comment is not a command. Only WHOLE lines are skipped — a
#: trailing `# …` is left in the text (see the declared over-report below).
_COMMENT_LINE = re.compile(r"^[ \t]*#")

#: ── The pin is matched as an INVOCATION, never as a bare mention ─────────────
#: A command word (`npx`/`npm`/`pnpm`/`yarn`/`bunx`/`bun x`), then
#: `wrangler@<spec>` on the same line — or `wrangler@<spec>` itself at a command
#: position (line start, or after `;` `&&` `|` `(` a backtick or `$(`), possibly
#: behind `VAR=value` assignments.
#:
#: This replaced a hand-written shell lexer that STRIPPED comments. Every shell
#: form that lexer had not modelled — `<<'EOF'`, `<<\EOF`, two here-docs on one
#: line, an escaped quote, `$'…'`, `;#`, a name with an apostrophe in the body —
#: could suppress a real floating invocation from a required check, i.e. report a
#: green step while shell ran a float. The arms below pin all of them.
#:
#: Matching the invocation is not "no suppression is possible"; it is narrower
#: and measurable: what the matcher sees is not rewritten to drop comments, so a
#: construct can only make it report MORE than the command line it sits on, never
#: less. Over-reports are declared with the arms; all are fail-closed, which
#: is the direction an approximation is allowed to fail in here.
_CMD_WORD = r"(?:npx|npm|pnpm|yarn|bunx|bun[ \t]+x)"
_CMD_POSITION = r"(?:^|[;&|(`]|\$\()"
#: An INDIRECT spec (`wrangler@$V`, `wrangler@"${V}"`) is reported as the EMPTY
#: spec on purpose: whatever the variable holds, the token is not an exact pin,
#: so it counts as floating — the same fail-closed read the lexer this replaced
#: produced, and what keeps a variable-versioned pre-warm or boot step red.
_SPEC = r"([A-Za-z0-9][A-Za-z0-9._-]*|(?=[\"'$({]))"
_INVOCATION = re.compile(
    r"(?:\b" + _CMD_WORD + r"\b[^\n]*?"
    r"|" + _CMD_POSITION + r"[ \t]*(?:[A-Za-z_][A-Za-z0-9_]*=[^ \t\n;&|]*[ \t]+)*"
    r")wrangler@" + _SPEC,
    re.M,
)

#: DECLARED RESIDUAL — a version supplied indirectly (`wrangler@$V`,
#: `wrangler@"${V}"`) cannot be RESOLVED by a text check, so it is reported as an
#: empty spec, i.e. as floating (see `_SPEC`). What is genuinely out of reach is
#: only the opposite direction: an indirect spec cannot be proved EQUAL to an
#: exact pin, so a step that pins indirectly and a pre-warm that also does still
#: cannot be checked for parity — a text check would have to resolve the variable.


def _wrangler_invocations(run: str) -> set[str]:
    """Every `wrangler@<spec>` a COMMAND LINE in `run` invokes."""
    logical = _CONTINUATION.sub("", run).splitlines()
    return set(
        _INVOCATION.findall("\n".join(line for line in logical if not _COMMENT_LINE.match(line)))
    )


def _exact_wranglers(run: str) -> set[str]:
    """An EXACT pin (`4.127.0`) — the version that actually boots."""
    return {v for v in _wrangler_invocations(run) if v.count(".") == 2}


def _floating_wranglers(run: str) -> set[str]:
    """A range (`4`, `4.127`) or dist-tag (`latest`) — never acceptable here."""
    return {v for v in _wrangler_invocations(run) if v.count(".") != 2}

#: The e2e jobs that serve this repo's `website/` (marketing + dashboard) and so
#: share the pre-warm. A rename shows up here as a failure, not as silent loss.
PREWARM_JOBS = frozenset({"welcome-e2e", "legal-e2e", "dashboard-e2e"})


#: The step that actually STARTS the preview server. The pre-warm exists to
#: warm the tarball THIS step fetches, so parity is measured against it and
#: nothing else. A union over every step in the job is NOT equivalent:
#: `welcome-e2e` also runs `npm install -g wrangler@X`, so a union lets the
#: pre-warm name a version that is installed but never booted. That is not a
#: hypothetical — a boot step drifted to `4.130.0` while the pre-warm and the
#: `npm install -g` step stayed on `4.127.0`, and the union comparison passed
#: (the install step supplied `4.127.0` to the union).
BOOT_STEP_PREFIX = "Start wrangler pages dev preview"


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text())["jobs"]


def _steps_of(jobs: dict) -> list[tuple[str, dict]]:
    return [(job, step)
            for job, spec in jobs.items()
            for step in (spec.get("steps") or [])]


def _steps() -> list[tuple[str, dict]]:
    return _steps_of(_jobs())


def _prewarm_steps_of(jobs: dict) -> list[tuple[str, dict]]:
    return [(job, step) for job, step in _steps_of(jobs)
            if step.get("name") == PREWARM_STEP]


def _prewarm_steps() -> list[tuple[str, dict]]:
    return _prewarm_steps_of(_jobs())


def _boot_steps_of(jobs: dict, job: str) -> list[dict]:
    """The steps in `job` that actually START wrangler — the pre-warm's targets."""
    return [step for other_job, step in _steps_of(jobs)
            if other_job == job
            and str(step.get("name") or "").startswith(BOOT_STEP_PREFIX)]


def _parity_mismatches(jobs: dict) -> dict[str, tuple[set[str], set[str]]]:
    """job → (pre-warm versions, offending boot-step versions) on any DISAGREE.

    Scoped to the BOOT step(s) deliberately — see `BOOT_STEP_PREFIX`. EVERY
    boot step must pin exactly what the pre-warm pins: a UNION over the job's
    boot steps lets a still-pinned sibling supply the pre-warm's version and
    mask a drifted one, and a step that pins nothing (bare `wrangler`, a
    dist-tag) contributes the empty set to that union. An empty boot set is
    itself a mismatch: the pre-warm pins something and nothing boots it.
    """
    out: dict[str, tuple[set[str], set[str]]] = {}
    for job, step in _prewarm_steps_of(jobs):
        warm = _exact_wranglers(step.get("run") or "")
        boots = _boot_steps_of(jobs, job)
        offenders: set[str] = set()
        mismatch = not boots
        for boot in boots:
            got = _exact_wranglers(boot.get("run") or "")
            if got != warm:
                mismatch = True
                offenders.update(got)
        if mismatch:
            out[job] = (warm, offenders)
    return out


def test_the_three_website_e2e_jobs_still_prewarm() -> None:
    """Deleting the step re-exposes the boot budget #2178 added it to protect."""
    assert {job for job, _ in _prewarm_steps()} == PREWARM_JOBS


def test_every_prewarm_pins_an_exact_wrangler_and_never_floats() -> None:
    steps = _prewarm_steps()
    assert steps, "no pre-warm step found — the guard has nothing to check"
    for job, step in steps:
        run = step.get("run") or ""
        assert _exact_wranglers(run), (
            f"{job}: pre-warm does not pin an exact `wrangler@X.Y.Z` — a "
            f"floating range warms a different tarball than the boot step. run={run!r}")
        assert not _floating_wranglers(run), (
            f"{job}: pre-warm still floats a wrangler range — {sorted(_floating_wranglers(run))}")


def test_every_prewarm_retries_the_registry_fetch() -> None:
    """A single ECONNRESET reded the required legal-e2e check (2026-10-15)."""
    for job, step in _prewarm_steps():
        run = step.get("run") or ""
        assert "for attempt in 1 2 3" in run, (
            f"{job}: pre-warm is not bounded-retry-protected. run={run!r}")


def test_prewarm_pins_the_same_version_the_job_boots() -> None:
    """Warming `@4` while booting `@4.127.0` misses the cold fetch entirely.

    Compared against the step(s) that actually START wrangler, never the union
    of every exact pin in the job — see `BOOT_STEP_PREFIX`.
    """
    mismatches = _parity_mismatches(_jobs())
    assert not mismatches, "; ".join(
        f"{job}: pre-warm pins {sorted(warm)} but the boot step(s) start {sorted(boots)}"
        for job, (warm, boots) in sorted(mismatches.items()))


def test_parity_scope_catches_boot_drift_a_job_union_missed() -> None:
    """The drift that beat the pre-fix union scope, reproduced on the real job.

    `welcome-e2e`'s boot step moves to `4.130.0` while BOTH the pre-warm and the
    `npm install -g` step stay on `4.127.0`. A union over every other step in
    the job still contains `4.127.0`, so the pre-fix `warm <= boots` comparison
    reports no mismatch on a genuinely drifted job. The boot-only scope must.
    """
    jobs = _jobs()
    steps = jobs["welcome-e2e"]["steps"]
    boot = next(s for s in steps
                if str(s.get("name") or "").startswith(BOOT_STEP_PREFIX))
    assert _exact_wranglers(boot["run"]) == {"4.127.0"}, (
        "ci.yml fixture drifted — update this pin to whatever the boot step now starts")
    boot["run"] = boot["run"].replace("wrangler@4.127.0", "wrangler@4.130.0")

    # The pre-fix scope: union every other step of the job. It still contains
    # `4.127.0` (the pre-warm itself and the global install), so it passed.
    union: set[str] = set()
    for step in steps:
        if step.get("name") != PREWARM_STEP:
            union.update(_exact_wranglers(step.get("run") or ""))
    assert "4.127.0" in union, "the pre-fix union scope would NOT have passed this drift"

    assert set(_parity_mismatches(jobs)) == {"welcome-e2e"}


def test_parity_catches_one_of_two_boot_steps_the_union_missed() -> None:
    """`legal-e2e` / `dashboard-e2e` each start TWO wrangler previews.

    A union over the job's boot steps lets the still-pinned sibling supply the
    pre-warm's version and mask the drifted one — the same masking hole the
    `welcome-e2e` pin closed, one level down. Reproduced on `dashboard-e2e`'s
    second preview step: mutate it to a dist-tag (or drop the pin entirely),
    and the pre-fix union still equals the pre-warm; the per-step scope does
    not.
    """
    for drifted in ("wrangler@latest", "wrangler"):
        jobs = _jobs()
        steps = jobs["dashboard-e2e"]["steps"]
        boots = [s for s in steps
                 if str(s.get("name") or "").startswith(BOOT_STEP_PREFIX)]
        assert len(boots) == 2, (
            "ci.yml fixture drifted — dashboard-e2e must start two wrangler previews")
        target = boots[-1]
        assert _exact_wranglers(target["run"]) == {"4.127.0"}, (
            "ci.yml fixture drifted — update this pin to whatever the boot step now starts")
        target["run"] = target["run"].replace("wrangler@4.127.0", drifted)

        # The pre-fix comparison: UNION every boot step of the job. The untouched
        # sibling still supplies `4.127.0`, so the drifted step vanished.
        union: set[str] = set()
        for boot in boots:
            union.update(_exact_wranglers(boot.get("run") or ""))
        assert union == {"4.127.0"}, (
            f"the pre-fix union scope would NOT have caught `{drifted}` drift")

        assert set(_parity_mismatches(jobs)) == {"dashboard-e2e"}, drifted


def test_floating_detector_catches_a_dist_tag_not_only_a_numeric_range() -> None:
    """`wrangler@latest` is floating too, but the numeric-only token regex could
    not see it at all — so `test_no_e2e_boot_step_floats…` passed on a dist-tag.
    """
    assert _floating_wranglers("npx --yes wrangler@latest pages dev") == {"latest"}
    assert _floating_wranglers("npx --yes wrangler@next --version") == {"next"}
    assert _floating_wranglers("npx --yes wrangler@4 --version") == {"4"}
    assert _floating_wranglers("npx --yes wrangler@4.127 --version") == {"4.127"}
    assert _floating_wranglers("npx --yes wrangler@4.127.0 --version") == set()
    assert _floating_wranglers("wrangler@4 pages dev") == {"4"}

    # A MENTION is not an INVOCATION. A note naming a floated version must red
    # nothing, whole-line or trailing — and one naming an EXACT version must not
    # satisfy a parity check for a floating boot.
    assert _floating_wranglers("# was: wrangler@4\nnpx --yes wrangler@4.127.0 --version") == set()
    assert _exact_wranglers("# was: wrangler@4.127.0\nnpx --yes wrangler@latest --version") == set()
    assert _floating_wranglers("npx --yes wrangler@4.127.0 --version  # was wrangler@4") == set()
    assert _floating_wranglers("echo wrangler@4 --version") == set()
    assert _floating_wranglers('echo "refs #8743" && npx --yes wrangler@4 --version') == {"4"}

    # A shell CONTINUATION makes the two lines one command line.
    assert _floating_wranglers("npx --yes \\\n  wrangler@4 --version") == {"4"}

    # REGRESSION ARMS — each of A1–A6 HID a real float on at least one revision
    # of the shell lexer this matcher replaced (a quote in a here-doc body or an
    # ANSI-C string left its quote state open, so the later `npx` line read as a
    # comment while bash ran it, and the guard passed). A2 and A3 were still
    # hidden at the head that merged (#8743, 9378ba130) — the two the merge
    # shipped. A4 and A7 never hid on any revision and stand as coverage only.
    for run in (
        "cat <<'EOF'\nDon't\nEOF\necho 'see #8745' && npx --yes wrangler@4 --version",  # A1
        "cat <<\\EOF\nDon't\nEOF\necho 'see #8745' && npx --yes wrangler@4 --version",  # A2
        "cat <<'A' <<'B'\nA\nDon't\nB\necho 'see #1' && npx --yes wrangler@4 --version",  # A3
        "cat <<'1'\nDon't\n1\nnpx --yes wrangler@4 --version",  # A4 (coverage)
        "echo \"escaped \\\" # note\" && npx --yes wrangler@4 --version",  # A5
        "echo a\\;# x && npx --yes wrangler@4 --version",  # A6
        "cat <<< word\n# was wrangler@4\nnpx --yes wrangler@4 --version",  # A7 (coverage)
        "echo x $(cat <<\\EOF\nDont\nEOF\n) && npx --yes wrangler@4 --version",  # A8
    ):
        assert _floating_wranglers(run) == {"4"}, run

    # NO REGRESSION against the lexer this replaced: a spaced `bun x`, a
    # command-position `wrangler@` after a metacharacter, the other package
    # runners and the `VAR=value` prefix were ALL reported by that lexer and must
    # still be. (A review of the first version of this matcher found the first
    # two MISSING — a false PASS in the making: `bun x wrangler@4 pages dev` and
    # `echo hi; wrangler@4 pages dev` both really run.)
    assert _floating_wranglers("bun x wrangler@4 pages dev") == {"4"}
    assert _floating_wranglers("pnpm dlx wrangler@4 pages dev") == {"4"}
    assert _floating_wranglers("yarn dlx wrangler@4 pages dev") == {"4"}
    assert _floating_wranglers("bunx wrangler@4 pages dev") == {"4"}
    assert _floating_wranglers("npm exec -- wrangler@4 pages dev") == {"4"}
    assert _floating_wranglers("echo hi; wrangler@4 pages dev") == {"4"}
    assert _floating_wranglers("true && wrangler@4 pages dev") == {"4"}
    assert _floating_wranglers("cat log | wrangler@4 pages dev") == {"4"}
    assert _floating_wranglers("(wrangler@4 pages dev)") == {"4"}
    assert _floating_wranglers("WRANGLER_VERSION=4 wrangler@4 pages dev") == {"4"}
    assert _floating_wranglers("echo hi && \\\n  npx --yes wrangler@4 --version") == {"4"}

    # DECLARED OVER-REPORT (fail-closed, the safe direction): text that merely
    # NAMES an invocation in a way the matcher cannot tell from a command — a
    # TRAILING comment, a quoted mention, a here-doc body — is reported, because
    # the text is not rewritten. Refusing a correct step is recoverable by
    # rewording it; hiding a real invocation from a required check is not — and
    # the old lexer's attempt to tell those two apart is what produced A1–A6.
    assert _floating_wranglers(
        "npx --yes wrangler@4.127.0 --version  # was: npx --yes wrangler@4 --version") == {"4"}
    assert _floating_wranglers('echo "npx --yes wrangler@4 --version"') == {"4"}
    assert _floating_wranglers("cat <<'EOF'\nnpx --yes wrangler@4 --version\nEOF") == {"4"}

    # An INDIRECT spec is floating, not invisible: the empty spec is never exact.
    assert _floating_wranglers("WRANGLER=latest\nnpx --yes wrangler@$WRANGLER --version") == {""}
    assert _floating_wranglers('npx --yes wrangler@"${WRANGLER}" --version') == {""}
    assert _exact_wranglers("npx --yes wrangler@$WRANGLER --version") == set()


def test_no_e2e_boot_step_floats_the_wrangler_version() -> None:
    """The dashboard boot steps floated `@4` — as did its pre-warm — the drift
    class the parity check above exists to catch."""
    for job in sorted(PREWARM_JOBS):
        for other_job, other in _steps():
            if other_job != job:
                continue
            floats = _floating_wranglers(other.get("run") or "")
            assert not floats, (
                f"{job}: step {other.get('name')!r} floats wrangler {sorted(floats)}")

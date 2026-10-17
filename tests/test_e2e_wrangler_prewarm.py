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

#: A whole-line comment is not a command — but a `#` line INSIDE a here-doc body
#: or a multi-line quoted string is DATA, and shell still runs command
#: substitution in it (`cat <<EOF` / `# $(npx --yes wrangler@4 --version)` / `EOF`
#: executes the npx). So there is NO comment filter: every line is read. A pin
#: named in a comment is therefore reported, which is the declared over-report
#: below — the safe direction. Dropping the line instead is what hid a real
#: invocation from the third review round.

#: ── The pin is read as a LITERAL OCCURRENCE ─────────────────────────────────
#: Every `wrangler@<spec>` in the step is read. There is no lookbehind, no
#: command-word test, no position test and no line filter: the text is not
#: interpreted at all.
#:
#: This replaced a hand-written shell lexer that STRIPPED comments before reading
#: the token. Every shell form that lexer had not modelled — `<<'EOF'`, `<<\EOF`,
#: two here-docs on one line, an escaped quote, `$'…'`, `;#`, an apostrophe in a
#: here-doc body — could suppress a real floating invocation from a required
#: check, i.e. report a green step while shell ran a float. The arms below pin
#: them.
#:
#: WHY THIS IS THE FLOOR. Each earlier draft added a rule to tell a pin from a
#: mention, and every rule could be defeated by text shell interprets but the
#: rule does not:
#:   - round 1 — a command word or an enumerated command position: missed
#:     `bun x wrangler@4 pages dev`, then `wrangler@4` after `then`/`do`/`{`/
#:     `time`/`-exec` (11 spellings, all of them run by bash);
#:   - round 3 — a whole-line comment filter: a `#` line inside a here-doc body
#:     or a multi-line quoted string is DATA, and shell substitutes into it, so
#:     `cat <<EOF` / `# $(npx --yes wrangler@4 --version)` / `EOF` ran the npx
#:     while the filter dropped the line;
#:   - round 4 — a word-boundary lookbehind: `$@wrangler@4` runs `wrangler@4`
#:     (bash removes an empty `$@`), and the lookbehind read it as mid-word.
#: A substring search cannot be defeated by any of THOSE: each is a line-level
#: construct, and the scan does not read what a line means. What it can do is
#: over-report — a mention in prose, a quoted string, a commented-out line, a
#: longer word containing the token — and every over-report is declared and
#: pinned with the arms, because refusing a correct step is recoverable and
#: passing a floating one is not. Quoting that SPLITS the word itself is handled
#: by `_UNQUOTE` below; an EMPTY EXPANSION inside it is the one form no text scan
#: can read, and it is declared at `_exact_wranglers`.
#:
#: Shell removes quoting and escapes BEFORE it runs a word, so a token spelled
#: `wrangler\@4`, `wran\gler@4`, `wrangler'@'4` or `"wrangler"@4` executes
#: `wrangler@4` while the literal text holds no `wrangler@` at all. Deleting those
#: characters is sound in the fail-closed direction ONLY because `wrangler@`
#: itself contains none of them: the deletion can JOIN two fragments into a token
#: (an over-report, which this guard accepts) and can never split one apart.
#: Order matters — the continuation fold runs FIRST, or deleting the backslash
#: would leave `wrangler` and `@4` on two lines.
_UNQUOTE = re.compile(r"[\\'\"`]")
_INVOCATION = re.compile(r"wrangler@")

#: The spec that follows — or `""` when it is INDIRECT: no literal at all
#: (`wrangler@$V`, `wrangler@"${V}"`) or a literal cut short by an interpolation
#: (`wrangler@4.127.${PATCH}` is not the pin `4.127.`). An empty spec is never
#: exact, so it counts as FLOATING: whatever the variable holds, this token is not
#: a pin. (The lexer this replaced could not match such a spec at all and so
#: returned nothing, which read as no float at all.)
_SPEC_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_INDIRECT_AFTER = ("$", "{")


def _wrangler_invocations(run: str) -> set[str]:
    """Every `wrangler@<spec>` written in `run`, as a plain substring."""
    # Shell removes a backslash-newline pair; nothing else is removed and no line
    # is filtered — a pin inside a here-doc body or a quoted string is read too.
    text = _UNQUOTE.sub("", _CONTINUATION.sub("", run))
    specs: set[str] = set()
    for at in _INVOCATION.finditer(text):
        literal = _SPEC_RUN.match(text, at.end())
        # A TUPLE, not a string: `"" in "${"` is True, so an empty slice at the
        # end of the text would read a real `wrangler@4.127.0` as indirect.
        if literal is None or text[literal.end():literal.end() + 1] in _INDIRECT_AFTER:
            specs.add("")
        else:
            specs.add(literal.group(0))
    return specs
#: DECLARED RESIDUAL (review round 5, NARROWED by round 6): an EMPTY EXPANSION
#: inside the word — `wrangler$X@4`, `wrangler${X}@4` with `X` empty — executes
#: `wrangler@4` while no text scan can see a literal `wrangler@`. The quoting and
#: escaping forms the same review named (`wrangler\@4`, `wran\gler@4`,
#: `wrangler'@'4`, `"wrangler"@4`) are NOT residual: `_UNQUOTE` deletes those
#: characters before the scan, which turns each of them into a literal the guard
#: reports (pinned by the arms in
#: `test_floating_detector_catches_a_dist_tag_not_only_a_numeric_range`). Closing
#: the expansion case needs the shell's own variables, so the honest fix is at the
#: lane: assert the wrangler version that actually resolved, rather than read it
#: out of the script.

#: DECLARED RESIDUAL — what a text check cannot reach at all: two INDIRECT pins
#: cannot be proved EQUAL to each other, so parity between a pre-warm and a boot
#: step that both take their version from a variable is not checkable here. Each
#: is reported as floating (an empty spec), so the pin is refused, not resolved:
#: resolving it needs an install in the lane, not a regex.


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

    # There is NO comment filter, and that is deliberate. A `#` line inside a
    # here-doc body or a multi-line quoted string is DATA, and shell still
    # substitutes commands in it — review round 3 measured
    # `cat <<EOF` / `# $(npx --yes wrangler@4 --version)` / `EOF` running the npx
    # while a comment filter dropped the line. So a note naming a version is a
    # DECLARED OVER-REPORT (both assertions below), and a floating boot is caught
    # by the float check rather than by parity: an exact named in a note supplies
    # an exact.
    assert _floating_wranglers("# was: wrangler@4\nnpx --yes wrangler@4.127.0 --version") == {"4"}
    assert _exact_wranglers("# was: wrangler@4.127.0\nnpx --yes wrangler@latest --version") == {"4.127.0"}
    assert _floating_wranglers('echo "refs #8743" && npx --yes wrangler@4 --version') == {"4"}

    # A shell CONTINUATION removes the backslash AND the newline, so the halves
    # are ONE WORD: this token is split by the line break, not merely preceded by
    # it, which is what makes the arm discriminate — dropping `_CONTINUATION`
    # reads nothing here, so that removal is now caught.
    assert _floating_wranglers("npx --yes \\\n  wrangler@4 --version") == {"4"}
    assert _floating_wranglers("npx --yes wrangler\\\n@4 --version") == {"4"}

    # REGRESSION ARMS — inputs where a previous draft of this matcher, or the
    # shell lexer it replaced, reported nothing while bash executed a floating
    # `wrangler@4`. (The per-revision history of which draft hid which is NOT
    # asserted here: it was measured inconsistently across review rounds, so the
    # arms pin the input, not the provenance.)
    for run in (
        "cat <<'EOF'\nDon't\nEOF\necho 'see #8745' && npx --yes wrangler@4 --version",  # A1
        "cat <<\\EOF\nDon't\nEOF\necho 'see #8745' && npx --yes wrangler@4 --version",  # A2
        "cat <<'A' <<'B'\nA\nDon't\nB\necho 'see #1' && npx --yes wrangler@4 --version",  # A3
        "cat <<'1'\nDon't\n1\nnpx --yes wrangler@4 --version",  # A4
        "echo \"escaped \\\" # note\" && npx --yes wrangler@4 --version",  # A5
        "echo a\\;# x && npx --yes wrangler@4 --version",  # A6
        "cat <<< word\n# was wrangler@4\nnpx --yes wrangler@4 --version",  # A7
        "echo x $(cat <<\\EOF\nDont\nEOF\n) && npx --yes wrangler@4 --version",  # A8
        "echo $'x' # note\nnpx --yes wrangler@4 --version",  # A9  ANSI-C, then `#`
        "echo a;# (note)\nnpx --yes wrangler@4 --version",  # A10 `;#`, npx on the NEXT line
        'echo "refs \\\n#8743" && npx --yes wrangler@4 --version',  # A11 quote over a continuation
        # The round-3 class: a `#` line inside a here-doc body or a quoted string,
        # which a COMMENT FILTER dropped while shell substituted into it.
        'echo "prewarm\n# $(npx --yes wrangler@4 --version)\ndone"',  # A12 multi-line double quote
        "cat <<EOF\n# $(npx --yes wrangler@4 --version)\nEOF",  # A13 unquoted here-doc body
        "cat <<EOF\n\t# $(npx --yes wrangler@4 --version)\nEOF",  # A14 tab-indented
        'echo "a\n# `npx --yes wrangler@4 --version`\nb"',  # A15 backticks in a quoted body
        # The round-4 class: the token was preceded by something a lookbehind read
        # as "mid-word", but bash removes it and runs `wrangler@4`. `$@` is empty
        # in a step (bash is invoked with no arguments), so this really runs.
        "$@wrangler@4 --version",  # A16
    ):
        assert _floating_wranglers(run) == {"4"}, run

    # REGRESSION ARMS from the review of an earlier draft of this matcher, which
    # required a command word or an ENUMERATED command position and therefore
    # missed every one of these: each really runs a float, and the lexer this
    # replaced reported each. Enumerating where shell may start a command is what
    # produced them, so the rule no longer enumerates (see `_INVOCATION`).
    for run in (
        "bun x wrangler@4 pages dev",
        "pnpm dlx wrangler@4 pages dev",
        "yarn dlx wrangler@4 pages dev",
        "bunx wrangler@4 pages dev",
        "npm exec -- wrangler@4 pages dev",
        "echo hi; wrangler@4 pages dev",
        "true && wrangler@4 pages dev",
        "cat log | wrangler@4 pages dev",
        "(wrangler@4 pages dev)",
        "{ wrangler@4 pages dev; }",
        "! wrangler@4 pages dev",
        "time wrangler@4 pages dev",
        "if true; then wrangler@4 pages dev; fi",
        "if false; then :; else wrangler@4 pages dev; fi",
        "for i in 1; do wrangler@4 pages dev; done",
        "while true; do wrangler@4 pages dev; break; done",
        "until false; do wrangler@4 pages dev; break; done",
        "case x in x) wrangler@4 pages dev;; esac",
        "find . -maxdepth 0 -exec wrangler@4 pages dev ';'",
        "WRANGLER_VERSION=4 wrangler@4 pages dev",
        "sh -c 'npx --yes wrangler@4 --version'",
        "echo hi && \\\n  npx --yes wrangler@4 --version",
    ):
        assert _floating_wranglers(run) == {"4"}, run

    # DECLARED OVER-REPORT (fail-closed, the safe direction): text that merely
    # NAMES a pin is reported as well as a pin that runs — a TRAILING comment, a
    # quoted string, a here-doc body, and a longer word CONTAINING the token
    # (`xwrangler@4`, where bash runs a command of that name, not `wrangler@4`).
    # Refusing a correct step is recoverable by rewording it; hiding a real
    # invocation from a required check is not, and every draft that tried to tell
    # the two apart was defeated by a form shell runs and the rule did not.
    assert _floating_wranglers(
        "npx --yes wrangler@4.127.0 --version  # was: npx --yes wrangler@4 --version") == {"4"}
    assert _floating_wranglers("echo wrangler@4 --version") == {"4"}
    assert _floating_wranglers('echo "npx --yes wrangler@4 --version"') == {"4"}
    assert _floating_wranglers("cat <<'EOF'\nnpx --yes wrangler@4 --version\nEOF") == {"4"}
    assert _floating_wranglers("xwrangler@4 pages dev") == {"4"}

    # QUOTING INSIDE THE WORD is READ, not missed (round 6): shell removes these
    # characters before it runs the word, so `_UNQUOTE` removes them before the
    # scan and the token is reported as the float it is. Four forms that round 5
    # had declared unreadable are caught here.
    for run in ("wrangler\\@4 --version", "wran\\gler@4 --version",
                "wrangler'@'4 --version", '"wrangler"@4 --version'):
        assert _floating_wranglers(run) == {"4"}, run
    # The one form left: an EMPTY EXPANSION inside the word. bash runs
    # `wrangler@4`; there is no literal to read, and no text scan can know that
    # `X` is empty. Pinned so the limit is stated, not hidden — see the residual
    # note above `_exact_wranglers`.
    for run in ("wrangler$X@4 --version", "wrangler${X}@4 --version"):
        assert _wrangler_invocations(run) == set(), run

    # An INDIRECT spec is floating, never exact — including a literal CUT SHORT by
    # an interpolation, which must not be read as the pin `4.127.`:
    assert _floating_wranglers("WRANGLER=latest\nnpx --yes wrangler@$WRANGLER --version") == {""}
    assert _floating_wranglers('npx --yes wrangler@"${WRANGLER}" --version') == {""}
    assert _floating_wranglers("npx --yes wrangler@4.127.${PATCH} --version") == {""}
    assert _floating_wranglers("npx --yes wrangler@4.127.0${SUFFIX} --version") == {""}
    assert _exact_wranglers("npm install -g wrangler@4.127.0") == {"4.127.0"}


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

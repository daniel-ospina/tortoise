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

#: Captures a NUMERIC version token after `wrangler@` (`4`, `4.127`, `4.127.0`).
#: A token is classified by its dot count, never by a lookahead that backtracks
#: mid-token — a lookahead happily matched `4.12` inside `4.127.0`.
WRANGLER_REF = re.compile(r"wrangler@(\d+(?:\.\d+)*)")

#: Captures ANY `wrangler@<token>` specifier, numeric or not. The numeric-only
#: ref above cannot see a dist-tag (`wrangler@latest`/`@next`), so such a step
#: contributed NOTHING to the float/parity checks and was invisible to them.
#: This one makes the non-numeric specifier an OFFENDER instead of a non-match.
WRANGLER_SPEC = re.compile(r"wrangler@([A-Za-z0-9][A-Za-z0-9._-]*)")


#: YAML block scalars carry their `#` lines as TEXT, so a comment that merely
#: mentions `wrangler@4` (a historical note, say) read as a floating pin and would
#: red an otherwise clean required check. These checks are about what a step DOES,
#: so comments are stripped before matching. The strip is QUOTE-AWARE and scans the
#: WHOLE block: a word-leading `#` opens a comment only outside a quoted string,
#: and shell quoting spans a backslash-newline continuation, so a per-line scan
#: would reset `quote`, cut a real command short and hide the float on it.
#: A `#` opens a comment at a WORD BOUNDARY, which in shell is not only
#: whitespace — the metacharacters below end a word too (`;#`, `&&#`).
_QUOTE_CHARS = "\"'"
_WORD_START = " \t\n;&|()<>"
#: A here-doc opener: `<<`, an optional `-` (tab-stripping) and the delimiter,
#: which may be quoted. The lookbehind keeps `<<<` (a here-STRING, whose
#: following lines are ordinary CODE) from matching its second and third `<`.
_HEREDOC = re.compile(r"(?<!<)<<(?P<tabs>-?)[ \t]*(?P<q>['\"]?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P=q)")


def _heredoc_delimiter(line: str) -> tuple[str, bool] | None:
    """The first here-doc delimiter declared on `line`, with its tab flag.

    A false positive is safe by construction: entering here-doc mode only stops
    the scanner from STRIPPING text, so it can over-report and never hide.
    """
    match = _HEREDOC.search(line)
    return (match.group("name"), match.group("tabs") == "-") if match else None


def _commands(run: str) -> str:
    r"""The executable text of a step's `run` block — comments are not commands.

    Shell rules modelled here, each one because an earlier version got it wrong
    and hid a real `wrangler@` float (or invented one): quoting spans a
    backslash-newline continuation; a backslash ESCAPES the next character, so
    an escaped quote does not end a double-quoted string — and an escaped
    character is a LITERAL, so it cannot end a word either (`\;#` is not a
    comment, and a `\`+newline keeps the word open); `$'…'` is its own quoting
    form, where `\'` escapes; a `#` opens a comment after any word boundary, not
    only after whitespace; and a HERE-DOC body is data — it is passed through
    verbatim, so a quote inside it cannot desync the scanner. Measured on
    `cat <<'EOF'` / `Don't` / `EOF` followed by
    `echo 'see #8745' && npx --yes wrangler@4 --version`: before this rule the
    npx line read as a comment and NO float was reported while shell ran it.

    DECLARED RESIDUALS — unmodelled, and each can only OVER-report (the guard
    refuses a clean check), never hide a pin: command substitution (backticks),
    and a SECOND here-doc delimiter declared on the same line. Pinned by the
    residual arms in
    `test_floating_detector_catches_a_dist_tag_not_only_a_numeric_range`.
    """
    out: list[str] = []
    quote = ""
    ansi_c = False
    escaped = False
    at_word_start = True
    heredoc: str | None = None
    strip_tabs = False
    for line in run.splitlines(keepends=True):
        if heredoc is not None:
            # the body and its delimiter line are passed through untouched
            out.append(line)
            if line.rstrip("\n").lstrip("\t" if strip_tabs else "") == heredoc:
                heredoc = None
            continue
        declared = _heredoc_delimiter(line)
        i, n = 0, len(line)
        while i < n:
            ch = line[i]
            nxt = line[i + 1] if i + 1 < n else ""
            i += 1
            if escaped:
                escaped = False
                out.append(ch)
                if ch != "\n":
                    # a backslash-newline is removed and the word continues; any
                    # other escaped character is a literal word character
                    at_word_start = False
                continue
            if ansi_c:
                if ch == "'":
                    ansi_c, quote = False, ""
                elif ch == "\\":
                    escaped = True
                out.append(ch)
                at_word_start = False
                continue
            if quote:
                if ch == "\\" and quote == '"':
                    escaped = True
                elif ch == quote:
                    quote = ""
                out.append(ch)
                at_word_start = False
                continue
            if ch == "$" and nxt == "'":
                ansi_c, quote = True, "'"
                out.append(ch)
                out.append(nxt)  # the opening quote is part of the construct
                at_word_start = False
                i += 1  # consumed `nxt` as well
                continue
            if ch in _QUOTE_CHARS:
                quote = ch
            elif ch == "\\":
                escaped = True
            elif ch == "#" and at_word_start:
                if line.endswith("\n"):
                    out.append("\n")
                break
            out.append(ch)
            at_word_start = ch in _WORD_START
        if declared is not None:
            heredoc, strip_tabs = declared
    return "".join(out)


def _exact_wranglers(run: str) -> set[str]:
    """An EXACT pin (`4.127.0`) — the version that actually boots."""
    return {v for v in WRANGLER_REF.findall(_commands(run)) if v.count(".") == 2}


def _floating_wranglers(run: str) -> set[str]:
    """A range (`4`, `4.127`) or dist-tag (`latest`) — never acceptable here."""
    return {v for v in WRANGLER_SPEC.findall(_commands(run)) if v.count(".") != 2}

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
    # A COMMENT IS NOT A COMMAND. A historical note naming a floated version must
    # not read as an offender (it red a clean required check), and a comment
    # naming an exact pin must not satisfy the parity check for a floating boot.
    # Both the whole-line and the trailing form are stripped — the trailing one was
    # the surviving hole when only whole lines were dropped.
    assert _floating_wranglers("# was: wrangler@4\nnpx --yes wrangler@4.127.0 --version") == set()
    assert _exact_wranglers("# was: wrangler@4.127.0\nnpx --yes wrangler@latest --version") == set()
    assert _floating_wranglers("npx --yes wrangler@4.127.0 --version  # was wrangler@4") == set()
    # …and a REAL float on the command line is still an offender, comment or not —
    # including when an earlier quoted string on the line carries a `#`.
    assert _floating_wranglers("npx --yes wrangler@4 --version  # pinned in #8743") == {"4"}
    assert _floating_wranglers('echo "refs #8743" && npx --yes wrangler@4 --version') == {"4"}
    # …including when the quoted string SPANS a backslash-newline continuation:
    # shell removes the backslash-newline inside the quotes, so this is one real
    # command line, and a per-line strip would cut it at the quote and lose the pin.
    assert _floating_wranglers('echo "refs \\\n#8743" && npx --yes wrangler@4 --version') == {"4"}
    # An ESCAPED quote does not end the string, so that `#` is literal and the
    # float after it is on the executable line.
    assert _floating_wranglers('echo "escaped \\" # note" && npx --yes wrangler@4 --version') == {"4"}
    # A `#` also opens a comment after a metacharacter: reading `;#…` as a
    # command invents a pin out of a comment.
    assert _floating_wranglers("echo a;# was wrangler@4\nnpx --yes wrangler@4.127.0 --version") == set()
    assert _floating_wranglers("echo a &&# was wrangler@4\nnpx --yes wrangler@4.127.0 --version") == set()
    # An ESCAPED metacharacter is a literal, so it does not end the word and the
    # `#` after it does NOT open a comment (shell echoes the text) — and a
    # backslash-newline keeps the word open rather than starting a comment.
    assert _floating_wranglers("echo a\\;# x\nnpx --yes wrangler@4 --version") == {"4"}
    assert _commands("echo hi\\\n# not a comment") == "echo hi\\\n# not a comment"
    # `$'…'` is its own quoting form, where `\'` escapes: reading that `'` as a
    # plain single-quote desyncs the scanner, inventing a pin or hiding one.
    assert _floating_wranglers("echo $'\\'' # was wrangler@4\nnpx --yes wrangler@4.127.0 --version") == set()
    assert _floating_wranglers("echo $'\\' # c' && npx --yes wrangler@4 --version") == {"4"}
    # A `#` opens a comment after EVERY word-boundary metacharacter, and after
    # none of them when the metacharacter is ESCAPED (then it is a literal).
    for metachar in (";", "&", "|", "(", ")", "<", ">"):
        assert _floating_wranglers(
            f"echo a{metachar}# was wrangler@4\nnpx --yes wrangler@4.127.0 --version"
        ) == set(), f"{metachar}# was not read as a comment"
        # the float sits on the SAME line, so the arm discriminates: a scanner
        # that wrongly ends the word at an escaped metacharacter swallows it
        assert _floating_wranglers(
            f"echo a\\{metachar}# x && npx --yes wrangler@4 --version"
        ) == {"4"}, f"\\{metachar}# was wrongly read as a comment"
    # A HERE-DOC body is data, passed through verbatim: an apostrophe in it must
    # not desync the scanner, which read the following real float as a comment
    # before this rule (measured — the guard went green while shell ran the npx).
    assert _floating_wranglers(
        "cat <<'EOF'\nDon't\nEOF\necho 'see #8745' && npx --yes wrangler@4 --version"
    ) == {"4"}
    # …and the body itself is kept, so a mention inside it is still reported
    # (a real `bash <<'EOF'` body executes, so silence there would be the danger).
    assert _floating_wranglers("cat <<'EOF'\nwrangler@4\nEOF\n") == {"4"}
    assert _floating_wranglers("cat <<-'EOF'\n\twrangler@4\n\tEOF\n") == {"4"}
    # `<<<` is a here-STRING: the lines after it are CODE and stay comment-stripped
    assert _floating_wranglers(
        "cat <<< word\n# was wrangler@4\nnpx --yes wrangler@4.127.0 --version"
    ) == set()
    # An UNBALANCED quote leaves the block unstripped — the fail-closed direction:
    # the scanner cannot know where the string ends, so a comment mention stays
    # and is read as a pin (the guard refuses) rather than being guessed away.
    for unbalanced in ('echo "unterminated\n# was wrangler@4',
                       "echo 'unterminated\n# was wrangler@4",
                       "echo $'unterminated\n# was wrangler@4"):
        assert _commands(unbalanced) == unbalanced
        assert _floating_wranglers(unbalanced) == {"4"}
    # DECLARED RESIDUALS — see the helper's docstring. A `#` inside a BACKTICK
    # substitution reads as command text, so a mention is reported (over-report:
    # the guard refuses); substitutions suppress no text, so no pin can hide.
    # `$(…)` needs no modelling here: shell treats a `#` inside it as a comment
    # too, and so does the scanner (the `(` ends the word).
    assert _floating_wranglers("echo `# was wrangler@4`") == {"4"}
    assert _floating_wranglers("echo $(# was wrangler@4)") == set()
    assert _floating_wranglers("echo $(npx --yes wrangler@4 --version)") == {"4"}


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

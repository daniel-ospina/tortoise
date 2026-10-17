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

# A token is classified by its SHAPE, never by a lookahead that backtracks
# mid-token (a lookahead happily matched `4.12` inside `4.127.0`): an EXACT pin
# is three numeric components and nothing else — `4.127.0`. Anything else is
# floating: `4`, `4.127`, `latest`, `next`, the X-RANGES `4.127.x` / `4.x.x`
# (npm reads those as a moving window, `>=4.127.0 <4.128.0-0`), and a
# prerelease, which this rule refuses as an over-report rather than resolve.
# A dist-tag is an OFFENDER, not a non-match: the numeric-only form of this
# check could not see `wrangler@latest` at all, so such a step contributed
# nothing to the float/parity checks and was invisible to them. (The rule itself
# is `_EXACT_SPEC` below; this block is the POLICY note, not attribute docs.)


#: A shell line CONTINUATION removes the backslash AND the newline, so the two
#: lines are one command line: an invocation split across them is still one
#: invocation and must be matched as one. The fold is UNCONDITIONAL, so inside
#: single quotes — or a quoted here-doc body — it removes a pair shell keeps: the
#: word then reads as the pin while bash passes `4.127.0\<newline>`, which npm
#: rejects as a range (measured `null`). That is fail-LOUD — npx errors instead of
#: installing something else, and the step cannot pass — so it is declared rather
#: than tracked: the fold runs before quoting is known, and moving it inside
#: `_shell_words` would put it after the token scan that depends on it.
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
#: the token, and whose here-doc model MISSED real invocations. Measured against
#: `origin/main`'s copy, exactly two classes changed a verdict:
#:   - `<<\EOF` and two here-docs on ONE line (A2, A3): NOTHING on `main`,
#:     `{'4'}` here. Bash really RUNS that npx, so these are the fail-open fixes.
#:   - a same-line `;#` / `&#` tail (the DELTA ARMS block below): NOTHING on
#:     `main`, `{'4'}` here — an OVER-report, because shell also reads that `#`
#:     as a comment to end of line. Fail-closed, this file's declared direction.
#: Everything else below is a CONTRACT pin: it holds a reading that keeps an input
#: reported, so dropping a modelled form fails an arm. No arm claims a fail-open
#: fix it did not measure — the tail-cut spelling in particular is NOT one (the
#: earlier "`main` missed a real float there" narrative was false, see the DELTA
#: ARMS note).
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
#: A substring search cannot be defeated by any of THOSE, because none of them
#: removes a character from the token itself (round 4's `$@` PREPENDS; it does not
#: split). What it can do is over-report — a mention in prose, a quoted string, a
#: commented-out line, a longer word containing the token — and every over-report
#: is declared and pinned with the arms, because refusing a correct step is
#: recoverable and passing a floating one is not. Quoting that SPLITS the word is
#: read by `_shell_words` below, which resolves quoting the way shell does. What is
#: left is an INTERPOLATION inside the word
#: — `$X`, `${X}`, `$(…)`, `` `…` `` — which no text scan can read; it is declared
#: and pinned as MISSED at `_exact_wranglers`.
#:
#: Shell removes quoting and escapes BEFORE it runs a word, so a token spelled
#: `wrangler\@4`, `wran\gler@4`, `wrangler'@'4` or `"wrangler"@4` executes
#: `wrangler@4` while the literal text holds no `wrangler@` at all. The WORD scan
#: below resolves them the way shell does, which is what makes each of them a
#: reported float rather than a residual. That includes the `$`-prefixed QUOTING
#: forms — `$'…'` (ANSI-C, where `\'` is a literal quote) and `$"…"` (a double
#: quote) — whose `$` belongs to the quoting and not to the word: `wrangler$'@'4`
#: and `wrangler$"@"4.127.0` run `wrangler@4` / `wrangler@4.127.0`. Reading their
#: `$` as a word character is a MISS (`wrangler$@4` holds no `wrangler@`), and in
#: the ANSI-C case it also desynchronises the scan, so a `\'` opened a quote that
#: swallowed every later pin.
#:
#: DECLARED RESIDUAL: an INTERPOLATION inside the word — `wrangler$X@4`,
#: `wrangler${X}@4`, `wran$(echo gler)@4`, `` wran`echo gler`@4 `` — executes
#: `wrangler@4` while no text scan can see a literal `wrangler@`. Closing that needs
#: the shell's own evaluation, so the honest fix is at the lane: assert the wrangler
#: version that actually resolved, rather than read it out of the script.
#:
#: The spec run that follows is for DIAGNOSTICS — the value a failure message
#: names. It is not what decides exactness (the word is): a literal cut short by an
#: interpolation (`wrangler@4.127.${PATCH}`) reports `4.127.`, which is never an
#: exact pin, so it counts as FLOATING: whatever the variable holds, this token is
#: not a pin. (The lexer this replaced could not match such a spec at all and so
#: returned nothing, which read as no float at all.)
_SPEC_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: An UNQUOTED character that ends a shell word. Everything else — including a
#: newline inside quotes — belongs to the same argument.
_WORD_END = " \t\n|&;()<>"
_DQUOTE_ESCAPES = '$`"\\\n'

#: ANSI-C (`$'…'`) escapes. The set is CLOSED — bash documents it — and it is
#: TRANSLATED rather than skipped because an escape can spell the token's `@`:
#: `wrangler$'\100'4` runs `wrangler@4`, so reading the escape as its letter (`1`)
#: made a real float invisible to the guard while every check stayed green
#: (measured end to end on a parsed job body). Every arity bash accepts is
#: covered: `\nnn` is 1–3 OCTAL digits, `\xHH` 1–2 hex, and `\uHHHH`/`\UHHHHHHHH`
#: 1–4 / 1–8 hex. A fixed arity matched only the LONG spellings, so `$'\u40'` fell
#: through to a placeholder with the digits left behind — the token then vanished
#: instead of being refused (measured on bash 5). The same table keeps a CORRECT pin
#: correct: `wrangler@$'\064'.127.0` is `wrangler@4.127.0`.
#:
#: bash does THREE things with an escape, and all three are modelled (measured on
#: bash 5.2 by running the word and reading argv): it TRANSLATES it to a character;
#: it DROPS it when the character is NUL (`\0`, `\x00`, `\u0000`, `\c@`, and any
#: `\U` at or above 0x80000000) — and a NUL TRUNCATES THE SEGMENT, so everything
#: between it and the closing quote is discarded and the characters on either side
#: of it are adjacent: `wran$'\0gler@4'` is `wrangler@4`; or it keeps it LITERAL
#: (an unknown escape, a surrogate, an out-of-range `\u`/`\U`). Two classes bash
#: keeps literal that this table TRANSLATES instead — `\u`/`\U` at or above 0x80,
#: and `\c?` (bash 0x7F, here 0x1F) — can only over-report: neither produces a
#: character a spec can hold, so the word stops being the pin either way.
#: Writing anything OTHER than the drop into the word — a placeholder, a sentinel —
#: splits the token and hides it, which is how a float went unreported while every
#: check stayed green.
_ANSI_C = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f",
           "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\",
           "'": "'", '"': '"', "?": "?"}
_ANSI_OCTAL = re.compile(r"[0-7]{1,3}")
_ANSI_HEX = re.compile(r"[0-9A-Fa-f]{1,2}")
_ANSI_UNICODE = {4: re.compile(r"[0-9A-Fa-f]{1,4}"), 8: re.compile(r"[0-9A-Fa-f]{1,8}")}


def _ansi_translate(body: str) -> str:
    r"""What bash puts in the word for a `$'…'` BODY.

    A `$'…'` body is a C string, so a NUL TRUNCATES it: everything after the NUL is
    discarded and the characters on either side of it are adjacent in the argument
    (`$'a\0b'c` is `ac`). bash translates an escape, OMITS one whose character is
    NUL — and any `\U` at or above 0x80000000, omitted WITHOUT cutting the body
    because it is not a NUL — and keeps LITERAL the ones it does not know, an
    out-of-range `\u`/`\U`, and a surrogate. Two classes bash keeps literal are
    translated here instead (`\u`/`\U` at or above 0x80, and `\c?` — bash 0x7F,
    here 0x1F), which can only over-report: neither yields a character a spec can
    hold, so the word stops being the pin either way.
    """
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\" or i + 1 >= len(body):
            out.append(ch)
            i += 1
            continue
        nxt = body[i + 1]
        octal = _ANSI_OCTAL.match(body, i + 1)
        if octal:
            code = int(octal.group(0), 8) & 0xFF
            if code == 0:
                return "".join(out)
            out.append(chr(code))
            i = octal.end()
            continue
        if nxt == "x":
            m = _ANSI_HEX.match(body, i + 2)
            if m:
                code = int(m.group(0), 16)
                if code == 0:
                    return "".join(out)
                out.append(chr(code))
                i = m.end()
                continue
        elif nxt in ("u", "U"):
            m = _ANSI_UNICODE[4 if nxt == "u" else 8].match(body, i + 2)
            if m:
                code = int(m.group(0), 16)
                if code >= 0x80000000:
                    i = m.end()  # omitted, and NOT a NUL: the body continues
                    continue
                if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                    out.append(body[i:m.end()])  # bash keeps it literal
                elif code == 0:
                    return "".join(out)
                else:
                    out.append(chr(code))
                i = m.end()
                continue
        elif nxt == "c" and i + 2 < len(body):
            code = ord(body[i + 2]) & 0x1F
            if code == 0:
                return "".join(out)
            out.append(chr(code))
            i += 3
            continue
        out.append(_ANSI_C[nxt] if nxt in _ANSI_C else "\\" + nxt)
        i += 2
    return "".join(out)


def _shell_words(text: str) -> list[str]:
    """Every shell WORD in `text`, as shell would present each one.

    A word ends at an unquoted blank or shell metacharacter; inside single or
    double quotes it continues across both, so `"4.127.0 || 4.130.0"` is ONE
    argument. Quoting characters are removed and a backslash escapes the next
    character — except before an ordinary character inside double quotes, where
    shell keeps it. Word boundaries and quoting are therefore decided together,
    which is the whole point: deleting quotes FIRST (an earlier draft) threw away
    the only signal that separates one argument from several.

    The `$`-prefixed quoting forms are quoting too, and reading them as an
    ordinary `$` character followed by a quote DESYNCHRONISES the scan: in `$'…'`
    a `\'` escapes the quote (so `echo $'\'' # note` reads as an OPEN quote and
    swallows every pin after it), and in `$'@'` / `$"@"` the `$` is not part of
    the word (so `wrangler$'@'4` runs `wrangler@4` while no `wrangler@` appears in
    the text at all — a MISS, not an over-report). Both forms are therefore
    modelled: `$'` opens an ANSI-C quote where a backslash escapes the next
    character, and `$"` opens an ordinary double quote.
    """
    words: list[str] = []
    out: list[str] = []
    quote = ""
    comment = False
    # `started` is NOT `out`: an empty quoted word (`""#tok`) still STARTS a word,
    # so shell reads `#tok` and not a comment. Using `out` let a `#` that shell
    # keeps be dropped, and the pin read out of a word that is not the pin.
    started = False
    i = 0
    while i < len(text):
        ch = text[i]
        ended = False
        if comment:
            # A comment carries PROSE, so it has no quoting to track — an
            # apostrophe in `boot loop's` would otherwise open a quote that spans
            # the rest of the script and hide every pin in it. The tokens are still
            # READ (a pin named in a comment is reported), just split on blanks.
            if ch == "\n":
                comment = False
                ended = True
            elif ch in " \t":
                ended = True
            else:
                out.append(ch)
        elif quote == "'":
            if ch == "'":
                quote = ""
            else:
                out.append(ch)
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch == "\\" and i + 1 < len(text) and text[i + 1] in _DQUOTE_ESCAPES:
                out.append(text[i + 1])
                i += 2
                continue
            else:
                out.append(ch)
        elif quote == "$'":
            # The SEGMENT is found BEFORE it is translated: bash's boundary scan
            # skips exactly one character after each backslash, and `\c` is no
            # exception — no escape reaches past the quote that closes its segment.
            # Doing both in one pass let an escape's ARITY decide where the quote
            # was: `$'\c\\'` then swallowed the closing quote, left the scan in
            # `$'…'` mode for the rest of the run, and a later `\0` truncated
            # everything after it, so three spellings of a real `wrangler@4` were
            # reported as nothing at all (measured against bash 5).
            started = True
            j = i
            while j < len(text):
                if text[j] == "\\":
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    j += 1
            out.append(_ansi_translate(text[i:j]))
            quote = ""
            i = j + 1
            continue
        elif text.startswith("$'", i):
            quote = "$'"
            started = True
            i += 2
            continue
        elif text.startswith('$"', i):
            # `$"…"` is a double-quoted string (locale-translated by bash); the
            # `$` is part of the QUOTING, never of the word.
            quote = '"'
            started = True
            i += 2
            continue
        elif ch in "'\"":
            quote = ch
            started = True
        elif ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            started = True
            i += 2
            continue
        elif ch == "#" and not started:
            comment = True
        elif ch in _WORD_END:
            ended = True
        else:
            out.append(ch)
            started = True
        if ended and out:
            words.append("".join(out))
            out = []
        if ended:
            started = False
        i += 1
    if out:
        words.append("".join(out))
    return words

#: THE WHOLE ARGUMENT IS THE SPEC — this is what closes the range class.
#:
#: npm reads the WHOLE argument as a semver spec, so `"4.127.0 || 4.130.0"` is one
#: argument containing a range and resolves to `4.130.0`, while a scan that stopped
#: at the first literal run called `4.127.0` the pin. Four rounds widened a list of
#: continuation operators to catch that, and each widening left its symmetric
#: neighbour open — an empty operand after `||` (node-semver reads `*`), a newline
#: on the operand side, a `v`-prefixed hyphen bound. So the enumeration is GONE:
#: the word is read
#: as shell presents it (`_shell_words`), and the token is the pin only when that
#: whole word IS `wrangler@X.Y.Z`. An unknown tail is then floating by
#: construction, not by being on a list.
#:
#: The over-report a wrong reading would cost is gone too, because the word scan
#: knows what the enumeration could not: an UNQUOTED `||`, `>`, `;` or blank is a
#: shell separator, so `wrangler@4.127.0 || true`, `... >/dev/null` and
#: `... pages dev` are the pin — while the same characters INSIDE quotes are one
#: argument and are refused.


def _wrangler_invocations(run: str) -> set[str]:
    """Every `wrangler@<spec>` INVOCATION in `run`, keyed by its whole word.

    No line is filtered — a pin inside a here-doc body, a quoted string or a
    trailing comment is read too, because refusing a correct step is recoverable
    by rewording it and hiding a real invocation from a required check is not.
    """
    # The continuation fold runs FIRST: `wrangler@4.12\<newline>7.0` is one word.
    text = _CONTINUATION.sub("", run)
    specs: set[str] = set()
    for word in _shell_words(text):
        at = word.find("wrangler@")
        if at < 0:
            continue
        literal = _SPEC_RUN.match(word, at + len("wrangler@"))
        spec = literal.group(0) if literal else ""
        if not _EXACT_SPEC.fullmatch(spec):
            specs.add(spec)  # already inexact: a float, a dist-tag, an X-range…
        elif word == "wrangler@" + spec:
            specs.add(spec)  # the argument IS the pin
        else:
            specs.add("")  # the argument CONTINUES past it — a range, or unreadable
    return specs
#: DECLARED RESIDUAL (review round 5; NARROWED by round 6; widened to the whole
#: INTERPOLATION class by round 7): anything that puts a substitution INSIDE the
#: word — `wrangler$X@4`, `wrangler${X}@4`, `wran$(echo gler)@4`,
#: `` wran`echo gler`@4 `` — executes `wrangler@4` while no text scan can see a
#: literal `wrangler@`. The quoting and escaping forms an earlier round also called
#: residual (`wrangler\@4`, `wran\gler@4`, `wrangler'@'4`, `"wrangler"@4`) are NOT:
#: `_shell_words` resolves those characters the way shell does, which turns each of
#: them into a literal the guard reports (pinned by the arms in
#: `test_floating_detector_catches_a_dist_tag_not_only_a_numeric_range`). Closing
#: the interpolation class needs the shell's own evaluation, so the honest fix is
#: at the lane: assert the wrangler version that actually resolved, rather than
#: read it out of the script.
#:
#: DECLARED RESIDUAL (round 8): a step whose EXECUTED invocation carries NO spec at
#: all — `npx --yes wrangler pages dev` — has no `wrangler@` token for a text check,
#: so it reads as nothing. If a pin is named anywhere else in the same `run` (a
#: comment, a note), the exact check AND parity are both satisfied while npm
#: installs the `latest` tag (measured: 4.151.1). Treating a bare `wrangler` as an
#: offender would refuse the CORRECT steps whose prose names it (`# wrangler pinned
#: to 4.127.0`), the over-report this file treats as a defect, so the limit is
#: declared and pinned as MISSED instead. Same honest fix as above: assert what
#: the lane resolved.

#: DECLARED RESIDUAL — what a text check cannot reach at all: two INDIRECT pins
#: cannot be proved EQUAL to each other, so parity between a pre-warm and a boot
#: step that both take their version from a variable is not checkable here. Each
#: is reported as floating (an empty spec), so the pin is refused, not resolved:
#: resolving it needs an install in the lane, not a regex.


#: Three NUMERIC components and nothing else. A dot count alone is not enough:
#: npm reads `4.127.x` / `4.x.x` / `4.127.X` as X-RANGES — node-semver resolves
#: `4.127.x` to `>=4.127.0 <4.128.0-0`, a moving tarball — so a dot count called
#: them EXACT and the guard passed a step whose fetch is not a pin at all.
_EXACT_SPEC = re.compile(r"\d+\.\d+\.\d+")


def _exact_wranglers(run: str) -> set[str]:
    """An EXACT pin (`4.127.0`) — the version that actually boots."""
    return {v for v in _wrangler_invocations(run) if _EXACT_SPEC.fullmatch(v)}


def _floating_wranglers(run: str) -> set[str]:
    """Everything that is not an exact pin: a range (`4`, `4.127`, `4.127.x`), a
    dist-tag (`latest`), a prerelease, or an empty (indirect) spec."""
    return {v for v in _wrangler_invocations(run) if not _EXACT_SPEC.fullmatch(v)}

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
    steps = _prewarm_steps()
    assert {job for job, _ in steps} == PREWARM_JOBS
    jobs = _jobs()
    for job, step in steps:
        assert _prewarm_is_unconditional(step), (
            f"{job}: pre-warm is gated by `if: {step.get('if')}` — a skipped step "
            f"warms nothing, which is deletion by another name")
        # The JOB may carry the path filter (`needs.changes.…`), which is why this
        # is a constant-false test and not an unconditional one. `continue-on-error`
        # is NOT read: it swallows a failure rather than skipping the step, so it is
        # a limit of this check, named here rather than implied to be covered.
        assert not _is_constant_false_gate(jobs[job].get("if")), (
            f"{job}: job-level `if: {jobs[job].get('if')}` is constant false — the "
            f"whole pre-warm never runs")
    assert not _prewarm_is_unconditional({"if": "${{ false }}"})
    assert _prewarm_is_unconditional({})
    assert _prewarm_is_unconditional({"if": "  "})
    assert _is_constant_false_gate("${{ false }}")
    assert _is_constant_false_gate("false")
    assert _is_constant_false_gate(" 0 ")
    # YAML hands over a REAL boolean/int for `if: false` / `if: 0` — schema-legal
    # spellings — so the value must be stringified as it is. `or ""` folds `False`
    # to "", which reads a step that can never run as unconditional.
    assert _is_constant_false_gate(False)
    assert _is_constant_false_gate(0)
    assert not _prewarm_is_unconditional({"if": False})
    assert not _prewarm_is_unconditional({"if": 0})
    assert not _is_constant_false_gate("needs.changes.outputs.website == 'true'")
    assert not _is_constant_false_gate(None)


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


def _prewarm_is_unconditional(step: dict) -> bool:
    """A pre-warm step with an `if:` gate never runs — deletion by another name."""
    return not str(step.get("if") if step.get("if") is not None else "").strip()


#: A gate that is CONSTANT false skips the step no matter what the workflow knows,
#: which is deletion wearing a condition. A gate that reads `needs.changes` is not
#: this: it is the path filter that keeps the e2e jobs off unrelated diffs, and the
#: three pre-warm jobs use it deliberately. YAML may hand over a real boolean
#: (`if: false`) or an int (`if: 0`) — schema-legal spellings — so the value is
#: stringified as it is, never with `or ""`, which folds `False` to "" and reads a
#: step that can never run as unconditional.
_CONSTANT_FALSE = re.compile(r"^\s*(?:\$\{\{\s*)?(?:false|0)\.?0*\s*(?:\}\})?\s*$", re.I)


def _is_constant_false_gate(expr: object) -> bool:
    return bool(_CONSTANT_FALSE.match(str(expr if expr is not None else "")))


def _cut_comment(line: str) -> str:
    """`line` with a COMMENT TAIL cut off — quoting honoured, so `\"a#b\"` is data.

    A `#` starts a comment only at the start of a word and outside quotes, which
    is shell's own rule — and a word starts after a blank OR a metacharacter, so a
    `;#` tail is a comment too. ANSI-C and `$"…"` quoting are NOT modelled here (this
    keeps the CODE TEXT, not its words), so a `#` after a `\'` inside `$'…'` is cut
    where bash would keep it: that direction only ever OVER-reports.
    """
    quote = ""
    at_word_start = True
    i = 0
    while i < len(line):
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = ""
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch == "\\" and i + 1 < len(line):
                i += 1
        elif ch in "'\"":
            quote = ch
            at_word_start = False
        elif ch == "\\" and i + 1 < len(line):
            at_word_start = False
            i += 1
        elif ch == "#" and at_word_start:
            return line[:i]
        elif ch in " \t;&|()<>":
            # A METACHARACTER also starts a word, so `;#` and `(#` are comments.
            # Counting blanks alone left `npx … --version ;# for attempt in 1 2 3`
            # reading as code and satisfied the retry check on a single fetch.
            at_word_start = True
        else:
            at_word_start = False
        i += 1
    return line


def _has_retry_loop_in_code(run: str) -> bool:
    """Does the loop TEXT appear in CODE (not in a comment)?

    That is what this proves — not that the loop WRAPS the fetch. Comments are
    cut, whole-line AND trailing: a step keeping `# old body used `for attempt in
    1 2 3`` as a note while making a single fetch passed every invariant, and so
    did the trailing spelling `npx … --version  # for attempt in 1 2 3` while the
    filter dropped whole lines only. DECLARED RESIDUAL: a here-doc body, an
    `echo`, or an empty loop (`for attempt in 1 2 3; do :; done`) still satisfies
    the substring test; the arms below pin those misses so the limit is stated
    rather than implied. The honest fix is at the lane: assert that the fetch
    actually retried.
    """
    lines = [_cut_comment(line) for line in str(run or "").splitlines()]
    return any("for attempt in 1 2 3" in line for line in lines)


def test_every_prewarm_retries_the_registry_fetch() -> None:
    """A single ECONNRESET reded the required legal-e2e check (2026-10-15)."""
    for job, step in _prewarm_steps():
        run = step.get("run") or ""
        assert _has_retry_loop_in_code(run), (
            f"{job}: pre-warm is not bounded-retry-protected. run={run!r}")


def test_the_retry_check_ignores_a_commented_out_loop() -> None:
    """A comment naming the loop must not satisfy the retry check.

    Measured on an earlier draft: a step that kept "# old body used `for attempt
    in 1 2 3`" but made a single fetch passed every invariant. That is a false
    PASS of the exact protection the 2026-10-15 ECONNRESET motivated.
    """
    assert _has_retry_loop_in_code(
        "for attempt in 1 2 3; do\n  npx --yes wrangler@4.127.0 --version\ndone")
    assert not _has_retry_loop_in_code(
        "# old body used `for attempt in 1 2 3`; now a single fetch\n"
        "npx --yes wrangler@4.127.0 --version")
    assert not _has_retry_loop_in_code("npx --yes wrangler@4.127.0 --version")
    # …and a `#` after a METACHARACTER starts a comment like any other, or the loop
    # text survives in `;# for attempt in 1 2 3` and satisfies the check on one fetch.
    for run in ("npx --yes wrangler@4.127.0 --version ;# for attempt in 1 2 3",
                "(# for attempt in 1 2 3\nnpx --yes wrangler@4.127.0 --version)"):
        assert not _has_retry_loop_in_code(run), run
    # A TRAILING comment is a comment too, and it was the surviving spelling while
    # only whole lines were dropped: a single unprotected fetch with the loop text
    # kept as a note passed every invariant.
    assert not _has_retry_loop_in_code(
        "npx --yes wrangler@4.127.0 --version  # for attempt in 1 2 3")
    # …while quoting is honoured, so a `#` INSIDE a string is data. A cut at the
    # first `#` on the line (the naive form) would drop the loop and fail here.
    assert _has_retry_loop_in_code(
        'x="a#b" && for attempt in 1 2 3; do :; done')
    # DECLARED RESIDUAL, pinned: these still satisfy the substring test. Stated so
    # the limit is not mistaken for coverage.
    for run in ("cat <<'EOF'\nfor attempt in 1 2 3\nEOF\nnpx --yes wrangler@4.127.0 --version",
                'echo "for attempt in 1 2 3; do"\nnpx --yes wrangler@4.127.0 --version',
                "for attempt in 1 2 3; do :; done\nnpx --yes wrangler@4.127.0 --version"):
        assert _has_retry_loop_in_code(run), run


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

    # ARMS — spellings this matcher must report, each of which really runs a
    # floating `wrangler@4`. They pin the reading (drop a modelled form and the arm
    # fails), NOT a history: the per-revision story was measured inconsistently
    # across review rounds, so a PRE-FIX verdict is claimed only where it was
    # measured against `origin/main`'s copy. Those are A2 and A3 — `<<\EOF` and two
    # here-docs on ONE line both read NOTHING on the replaced lexer while bash really
    # runs the npx. A10 is marked NOT a pre-fix miss on purpose: it keeps the npx on
    # the NEXT line, so the tail cut still reported it, and claiming otherwise was
    # the round-8 finding; the same-line tail spellings are pinned in the DELTA ARMS
    # block below.
    for run in (
        "cat <<'EOF'\nDon't\nEOF\necho 'see #8745' && npx --yes wrangler@4 --version",  # A1
        "cat <<\\EOF\nDon't\nEOF\necho 'see #8745' && npx --yes wrangler@4 --version",  # A2 pre-fix MISS
        "cat <<'A' <<'B'\nA\nDon't\nB\necho 'see #1' && npx --yes wrangler@4 --version",  # A3 pre-fix MISS (one line)
        "cat <<'1'\nDon't\n1\nnpx --yes wrangler@4 --version",  # A4
        "echo \"escaped \\\" # note\" && npx --yes wrangler@4 --version",  # A5
        "echo a\\;# x && npx --yes wrangler@4 --version",  # A6
        "cat <<< word\nnpx --yes wrangler@4 --version",  # A7 `<<<` is a HERE-STRING, not a
        #     here-doc: a draft that read it as `<<word` took the next line as body
        #     and reported NOTHING. The comment-mention variant this replaces could
        #     not fail — the head reports a mention, so either line supplied its
        #     expected value.
        "echo x $(cat <<\\EOF\nDont\nEOF\n) && npx --yes wrangler@4 --version",  # A8
        "echo $'x' # note\nnpx --yes wrangler@4 --version",  # A9  ANSI-C, then `#`
        "echo a;# (note)\nnpx --yes wrangler@4 --version",  # A10 `;#`, npx on the NEXT line —
        #     NOT a pre-fix miss: the tail cut still saw the next line's npx. It pins
        #     the SHAPE only; the real `;#` miss is pinned in the REGRESSION ARMS below.
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

    # DELTA ARMS — the spellings a reintroduced COMMENT-TAIL CUT would read as
    # nothing. Measured against `origin/main`'s copy: each reads `set()` there and
    # `{'4'}` here, so putting the tail cut back fails this block (A10 above does
    # NOT discriminate — it keeps the npx on the next line — which is the round-8
    # finding).
    #
    # DIRECTION, stated rather than implied: in both rows shell really DOES treat
    # `#` as a comment to end of line, so bash runs no `wrangler` — the delta is an
    # OVER-report here, which is this file's declared fail-closed direction, NOT a
    # fix for a fail-open miss (`main`'s tail cut was correct on these two lines).
    # The fail-OPEN class a comment filter CAN produce is the substituted/quoted
    # bodies pinned as A12–A15 above, where shell DOES run the command (`main`'s
    # copy already reports those, so they are contract pins, not deltas).
    for run in (
        "echo a;# x && npx --yes wrangler@4 --version",  # the docstring's tail-cut spelling
        "echo a&# x && npx --yes wrangler@4 --version",
    ):
        assert _floating_wranglers(run) == {"4"}, run
    # NON-VACUITY: the cut these arms forbid really changes the verdict — apply it
    # by hand (the deleted lexer cut the tail at the `#`) and the float is gone.
    for run in ("echo a;# x && npx --yes wrangler@4 --version",
                "echo a&# x && npx --yes wrangler@4 --version"):
        cut_at = min(i for i in (run.find(";#"), run.find("&#")) if i >= 0)
        assert _floating_wranglers(run[:cut_at]) == set(), run

    # REGRESSION ARMS from the review of an earlier draft of this matcher, which
    # required a command word or an ENUMERATED command position and therefore
    # missed every one of these: each really runs a float, and the lexer this
    # replaced reported each. Enumerating where shell may start a command is what
    # produced them, so the rule no longer enumerates (see `_wrangler_invocations`).
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

    # QUOTING INSIDE THE WORD is READ, not missed: shell removes these characters
    # before it runs the word, and `_shell_words` resolves them the same way, so
    # the token is reported as the float it is. Four forms an earlier round had
    # declared unreadable are caught here.
    for run in ("wrangler\\@4 --version", "wran\\gler@4 --version",
                "wrangler'@'4 --version", '"wrangler"@4 --version'):
        assert _floating_wranglers(run) == {"4"}, run
    # …INCLUDING the `$`-prefixed quoting forms, where the `$` is part of the
    # QUOTING and not of the word. Reading it as a word character MISSES the token
    # entirely (`wrangler$@4` holds no `wrangler@`), which is a fail-open: bash runs
    # `wrangler@4`. Both are floats and must be REPORTED.
    assert _floating_wranglers("wrangler$'@'4 --version") == {"4"}
    assert _floating_wranglers('wrangler$"@"4 --version') == {"4"}
    assert _exact_wranglers('wrangler$"@"4.127.0 --version') == {"4.127.0"}
    # In ANSI-C quoting `\'` is a LITERAL quote, so it does not open one: reading it
    # as an ordinary single quote desynchronised the scan for the rest of the block
    # and swallowed the following pin (main pinned this arm; it had been replaced
    # by one whose expected value the npx line supplied either way).
    _ansi = "echo $'\\'' # was wrangler@4\nnpx --yes wrangler@4.127.0 --version"
    assert _exact_wranglers(_ansi) == {"4.127.0"}, _ansi
    assert _floating_wranglers(_ansi) == {"4"}, _ansi  # the comment mention only
    assert _floating_wranglers("echo $'\\' # c' && npx --yes wrangler@4 --version") == {"4"}
    # An EMPTY quoted word still STARTS a word, so `""#tok` reaches bash as the word
    # `#tok` — not a comment. Testing the buffer instead of "a word has started"
    # dropped the `#` and read the pin out of a word that is not the pin.
    for run in ('npx --yes ""#wrangler@4.127.0 --version',
                "npx --yes ''#wrangler@4.127.0 --version"):
        assert _exact_wranglers(run) == set(), run
        assert _floating_wranglers(run) == {""}, run
    # An ANSI-C escape can SPELL the token's `@`: `wrangler$'\100'4` is
    # `wrangler@4`. Reading the escape as its letter made that float invisible.
    for run in ("npx --yes wrangler$'\\100'4 --version",
                "npx --yes wrangler$'\\x40'4 --version"):
        assert _floating_wranglers(run) == {"4"}, run
    # …and the same escapes can spell a CORRECT pin, which must stay exact.
    assert _exact_wranglers("npx --yes wrangler@$'\\064'.127.0 --version") == {"4.127.0"}
    # bash takes 1–4 (`\u`) and 1–8 (`\U`) hex digits, so the SHORT spellings must
    # be translated too: with a fixed arity the escape left its digits behind, the
    # word held no `wrangler@`, and the float was invisible (measured on bash 5).
    for run in ("npx --yes wrangler$'\\u40'4 --version",
                "npx --yes wrangler$'\\u040'4 --version",
                "npx --yes wrangler$'\\U0040'4 --version"):
        assert _floating_wranglers(run) == {"4"}, run
    assert _exact_wranglers("npx --yes wrangler@4.1$'\\u32'7.0 --version") == {"4.127.0"}
    # A DROPPED escape contributes NOTHING — bash removes a NUL from the word — so
    # the fragments join. Writing a placeholder there split the token and hid a real
    # float (`wran$'\0'gler@4` runs `wrangler@4`) while every check stayed green.
    for run in ("npx --yes wran$'\\0'gler@4 --version",
                "npx --yes wran$'\\000'gler@4 --version",
                "npx --yes wran$'\\x00'gler@4 --version",
                "npx --yes wran$'\\u0000'gler@4 --version",
                "npx --yes wran$'\\c@'gler@4 --version"):
        assert _floating_wranglers(run) == {"4"}, run
    # A NUL also TRUNCATES the rest of its segment, so the tail is DISCARDED rather
    # than kept: keeping it stitched a pin out of `4` + `.127.0` while bash ran
    # `wrangler@4` (a moving window), and hid the spellings that split the token.
    assert _floating_wranglers("npx --yes wrangler@4$'\\0.127.0' --version") == {"4"}
    for run in ("npx --yes wran$'\\0abc'gler@4 --version",
                "npx --yes wran$'\\0000'gler@4 --version",
                "npx --yes wran$'\\x000'gler@4 --version",
                "npx --yes wran$'\\u00000'gler@4 --version",
                "npx --yes wran$'\\400abc'gler@4 --version"):
        assert _floating_wranglers(run) == {"4"}, run
    # A `\U` at or above 0x80000000 is OMITTED but does NOT cut the segment (it is
    # not a NUL), so the letters on both sides stay and the word is no longer the
    # token under bash either: nothing to report, nothing to refuse.
    assert _wrangler_invocations("npx --yes wran$'\\UFFFFFFFFabc'gler@4 --version") == set()
    # …and a truncated tail after a CORRECT pin leaves the pin exact, not a float.
    assert _exact_wranglers("npx --yes wrangler@4.127.0$'\\0abc' --version") == {"4.127.0"}
    # …and a `\U` at or above 0x80000000 is dropped too, so that word IS the pin.
    assert _exact_wranglers("npx --yes wrangler@4.127.0$'\\UFFFFFFFF' --version") == {"4.127.0"}
    # A surrogate or an out-of-range `\u`/`\U` is kept LITERAL by bash (never
    # empty), so it cannot be the pin — and `chr` must not be asked, which raised.
    for run in ("npx --yes wrangler@4.127.0$'\\uD800' --version",
                "npx --yes wrangler@4.127.0$'\\U00110000' --version",
                "npx --yes wrangler@4.127.0$'\\q' --version"):
        assert _floating_wranglers(run) == {""}, run
    # `\c` with nothing left inside its segment is kept LITERAL by bash and the
    # quote closes it. Consuming that quote left the scan inside `$'…'` for the rest
    # of the run, where a later `\0` swallowed everything after it — three spellings
    # of a real `wrangler@4` then reported NOTHING.
    assert _wrangler_invocations("npx --yes wrangler@4$'\\c' --version") == {"4"}
    for run in ("echo $'\\c' \\0 ; npx --yes wrangler@4 --version",
                "npx --yes $'\\c' ; wrangler\\@4 --version",
                'npx --yes $\'\\c\' ; wrangler"@"4 --version'):
        assert _floating_wranglers(run) == {"4"}, run
    # …and a backslash inside a TRUNCATED segment still escapes, so `\'` does NOT
    # close it: the discard runs to the real quote and `wran` joins `gler@4`, which
    # is the float `wrangler@4` under bash (measured).
    assert _wrangler_invocations("npx --yes wran$'\\0ab\\'cd'gler@4 --version") == {"4"}
    # A translated escape that is not a spec character makes the argument continue
    # past the pin, which is FLOATING — `\cA` is translated, not unreadable.
    assert _floating_wranglers("npx --yes wrangler@4.127.0$'\\cA' --version") == {""}
    # A BACKSLASH BEFORE AN ORDINARY CHARACTER inside double quotes is KEPT by
    # shell, so `wrangler@"4.\\127.0"` runs the invalid word `wrangler@4.\\127.0`
    # (npm rejects it). Reading the quoting is what lets this be REFUSED rather
    # than read as the pin `4.127.0`, which an earlier draft declared as its
    # over-read.
    assert _floating_wranglers('npx wrangler@"4.\\127.0" --version') == {"4."}
    # An X-RANGE is not an exact pin: npm resolves `4.127.x` to a moving window
    # (`>=4.127.0 <4.128.0-0`), so a dot-count classifier passed it as exact.
    for spec in ("4.127.x", "4.x.x", "4.127.X", "4.127.0-rc.1"):
        assert _floating_wranglers(f"npx --yes wrangler@{spec} --version") == {spec}, spec
        assert _exact_wranglers(f"npx --yes wrangler@{spec} --version") == set(), spec
    # THE ARGUMENT IS THE SPEC, so a range is a range however it is spaced — and
    # quoting is what makes one argument out of it. Under npm's own semver these
    # resolve away from the pin — `||` with an operator or wildcard operand, an
    # EMPTY operand (node-semver reads `*` → the newest 4.x), a newline on either
    # side of the operator, a `v`-prefixed hyphen bound (`- v4` → newest 4.x) —
    # except `|| =4.127.0`, which resolves TO `4.127.0` and is refused anyway: the
    # any-continuation rule does not read node-semver, so it over-reports an
    # argument that happens to pin. Stated because the assertion above covers the
    # value, not the reason.
    for run in ('npx --yes wrangler@"4.127.0 || 4.130.0" --version',
                'npx --yes wrangler@"4.127.0 ||" --version',
                'npx --yes wrangler@"4.127.0 || " --version',
                'npx --yes wrangler@"4.127.0 || >=4.130.0" --version',
                'npx --yes wrangler@"4.127.0 || <4.130.0" --version',
                'npx --yes wrangler@"4.127.0 || =4.127.0" --version',  # over-report
                'npx --yes wrangler@"4.127.0 || ^4" --version',
                'npx --yes wrangler@"4.127.0 || x" --version',
                'npx --yes wrangler@"4.127.0 || *" --version',
                'npx --yes wrangler@"4.127.0 - 4.130.0" --version',
                'npx --yes wrangler@"4.127.0 - v4" --version',
                'npx --yes wrangler@"4.127.0 - v4.130.0" --version',
                'npx --yes wrangler@"4.127.0 - x" --version',
                'npx --yes wrangler@"4.127.0\n|| >=4.130.0" --version',
                'npx --yes wrangler@"4.127.0 ||\n>=4.130.0" --version',
                'npx --yes wrangler@"4.127.0 ||\n*" --version',
                'npx --yes wrangler@"4.127.0 -\n4.130.0" --version',
                'npx --yes wrangler@"4.127.0 - \nv4" --version'):
        assert _exact_wranglers(run) == set(), run
        assert _floating_wranglers(run) == {""}, run
    # …while the SAME characters UNQUOTED are shell separators, so the argument is
    # the pin and the step is CORRECT. Refusing these was the over-report an
    # operator list produced; the word scan tells them apart by construction.
    for run in ("npx --yes wrangler@4.127.0 --version",
                "npx --yes wrangler@4.127.0 --flag=4",
                "npx --yes wrangler@4.127.0 pages dev . --port 8788 --ip 127.0.0.1",
                "npm install -g wrangler@4.127.0 >/dev/null",
                "npx --yes wrangler@4.127.0 || true",
                "npx --yes wrangler@4.127.0||4.130.0 --version",
                "npx --yes wrangler@4.127.0 >4.130.0 --version",
                "npx --yes wrangler@4.127.0 - 4.130.0 --version",
                "npx --yes wrangler@4.127.0; echo done",
                "npx --yes wrangler@4.127.0\n> /tmp/log 2>&1"):
        assert _exact_wranglers(run) == {"4.127.0"}, run

    # The residual class: an INTERPOLATION inside the word. bash runs
    # `wrangler@4`; there is no literal to read, and no text scan can evaluate the
    # substitution. Pinned as MISSED so the limit is stated, not hidden — see the
    # residual note above `_exact_wranglers`.
    for run in ("wrangler$X@4 --version", "wrangler${X}@4 --version",
                "wran$(echo gler)@4 --version", "wran`echo gler`@4 --version"):
        assert _wrangler_invocations(run) == set(), run
    # DECLARED RESIDUAL: a step whose EXECUTED invocation carries NO spec at all
    # (`npx --yes wrangler pages dev`) — a pin named in a note then satisfies the
    # exact check and parity while npm installs the `latest` tag. Measured; see the
    # residual note above `_exact_wranglers`. Pinned as MISSED so the limit is
    # stated, not implied to be covered.
    miss = ("# was: npx --yes wrangler@4.127.0 pages dev\n"
            "npx --yes wrangler pages dev . --port 8788")
    assert _exact_wranglers(miss) == {"4.127.0"}, miss
    assert _floating_wranglers(miss) == set(), miss
    # …and the EXACT side of an empty spec, so "an empty spec is never exact" is
    # pinned from BOTH directions: mutating `_exact_wranglers` to accept "" fails.
    for run in ("npx --yes wrangler@$WRANGLER --version",
                "npx --yes wrangler@4.127.${PATCH} --version"):
        assert _exact_wranglers(run) == set(), run

    # An INDIRECT spec is floating, never exact — including a literal CUT SHORT by
    # an interpolation, which must not be read as the pin `4.127.`:
    assert _floating_wranglers("WRANGLER=latest\nnpx --yes wrangler@$WRANGLER --version") == {""}
    assert _floating_wranglers('npx --yes wrangler@"${WRANGLER}" --version') == {""}
    assert _floating_wranglers("npx --yes wrangler@4.127.${PATCH} --version") == {"4.127."}
    assert _exact_wranglers("npx --yes wrangler@4.127.${PATCH} --version") == set()
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

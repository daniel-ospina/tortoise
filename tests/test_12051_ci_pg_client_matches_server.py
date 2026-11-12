# tests/test_12051_ci_pg_client_matches_server.py
"""#12051: the record drill's PostgreSQL **client** major must equal the servers' major.

The D8 drill dumps the record with `pg_dump` and restores it with `pg_restore`,
and `tortoise/record_backup.py` resolves BOTH from `PATH` (the parameters are
the bare names — `pg_dump: str = "pg_dump"`). The archive is not
version-neutral: `pg_dump` writes a preamble the RESTORE SERVER must be able to
execute, so a client whose major is newer than the restore target produces an
artifact that raises on its own header.

Measured 2026-11-12, job `130998952949` (PR #12030, head `3408c76c2b`):

    pg_restore: error: could not execute query: ERROR:  unrecognized
      configuration parameter "transaction_timeout"
    Command was: SET transaction_timeout = 0;

`transaction_timeout` is a **PostgreSQL 17** GUC, the runner's inherited client
was 17, and both provisioned servers are `pgvector/pgvector:pg16`. Nothing in
the diff caused it, and the message reads as a defect in the record path — which
is why the pin is ASSERTED in the job (`pg_dump --version` / `pg_restore
--version`, `::error::` + `exit 1`) rather than merely hoped for.

This guard pins three things about `test-record-postgres`, none of which the
other CI-surface guards cover:

1. **The two image pins agree** (the record server and D8's scratch server must
   be one major — the drill restores across them).
2. **The client pin names that same major**, and prepends its versioned
   `/usr/lib/postgresql/<major>/bin` to `$GITHUB_PATH` — an unversioned install
   would leave the runner's own, newer client first on `PATH`.
3. **The pin runs BEFORE the leg**, because `$GITHUB_PATH` only affects
   subsequent steps: a pin placed after the pytest step is inert, and the job
   would red for a reason no diff explains.

A behavioural test of the mismatch itself would need two live servers and two
client majors, so the assertion is made where the version is decided: the
workflow. Reproduced by hand (documented on #12051): a PG17 client dumping into
`pgvector/pgvector:pg16` fails with exactly the error quoted above, and the same
server's own PG16 client dumps and restores clean.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PYTHON_CI = ROOT / ".github" / "workflows" / "python-ci.yml"
JOB = "test-record-postgres"

#: `pgvector/pgvector:pg16` -> 16, and a plain `postgres:16` -> 16. The
#: `pgvector` prefix is not incidental: the record's vector migration needs the
#: extension (#11642), so a plain `postgres:NN` pin cannot substitute for one of
#: these — but reading BOTH forms keeps this guard's message about the major
#: rather than about the spelling.
IMAGE_RE = re.compile(r":(?:pg)?(\d+)$")
CLIENT_PKG_RE = re.compile(r"postgresql-client-(\d+)\b")

#: The `$GITHUB_PATH` write as ONE unit: the versioned dir IS the value written.
#: `echo <dir> >> "$GITHUB_PATH"` — read the major from the
#: write line itself, never from an `export` elsewhere in the step.
GITHUB_PATH_PREPEND_RE = re.compile(
    r"echo\s+(?P<quote>['\"]?)/usr/lib/postgresql/(?P<major>\d+)/bin(?P=quote)"
    r"\s*>>\s*['\"]?\$GITHUB_PATH"
)


@pytest.fixture(scope="module")
def job() -> dict:
    workflow = yaml.safe_load(PYTHON_CI.read_text())
    assert JOB in workflow["jobs"], f"{JOB} is the record's only armed Postgres job"
    return workflow["jobs"][JOB]


def _steps(job: dict) -> list[dict]:
    return [step for step in job.get("steps", []) if isinstance(step, dict)]


def _run_text(step: dict) -> str:
    return str(step.get("run") or "")


def _pin_step(steps: list[dict]) -> tuple[int, dict]:
    """The one step that installs a PostgreSQL client package."""
    hits = [
        (index, step)
        for index, step in enumerate(steps)
        if CLIENT_PKG_RE.search(_run_text(step))
    ]
    assert len(hits) == 1, (
        "expected exactly ONE PostgreSQL-client pin step in "
        f"`{JOB}`; found {len(hits)} — a second installer makes the client "
        "whichever one ran last, which is the class #12051 closed"
    )
    return hits[0]


def _postgres_image_majors(job: dict) -> list[int]:
    """The major of every provisioned-Postgres image pin in this job."""
    majors: list[int] = []
    for step in _steps(job):
        image = str((step.get("with") or {}).get("image") or "")
        # `pgvector/pgvector:pg16` / `postgres:16` — matched by REPOSITORY, not
        # by a `"postgres" in image` substring: the pinned image this job uses
        # is named `pgvector`, which does not contain `postgres`.
        if "pgvector" not in image and "postgres" not in image:
            continue
        match = IMAGE_RE.search(image)
        assert match, (
            f"postgres image pin {image!r} does not name its major "
            "(`:pg<major>` or `:<major>`); an unpinned major cannot be matched "
            "by the client pin, which is how #12051 shipped"
        )
        majors.append(int(match.group(1)))
    return majors


def test_the_two_provisioned_servers_are_one_major(job: dict) -> None:
    """D8 restores ACROSS the two servers, so their majors must not diverge."""
    majors = _postgres_image_majors(job)

    assert len(majors) == 2, (
        "expected TWO provisioned Postgres servers in "
        f"`{JOB}` (the record, and D8's scratch target — the drill REFUSES a "
        f"scratch target that is the record itself); found {len(majors)}"
    )
    assert len(set(majors)) == 1, (
        "the record server and the D8 scratch server must be the SAME major: "
        f"the drill restores one into the other, found {majors}"
    )


def test_the_client_pin_names_the_servers_major_and_prepends_it(job: dict) -> None:
    """`postgresql-client-<major>` AND `/usr/lib/postgresql/<major>/bin` ON `$GITHUB_PATH`.

    Both halves are load-bearing: the package supplies the binaries, and the
    prepend is what makes them win over the runner image's own (newer) client
    and over `postgresql-common`'s unversioned wrapper.

    ⛔ THE PREPEND IS ASSERTED AS ONE FACT, NOT TWO (#12051 review P2). The
    first cut checked "the versioned dir appears somewhere in the step" and
    "`GITHUB_PATH` is written somewhere in the step" — two independent
    disjunctions. Measured: a step whose `$GITHUB_PATH` write says `/usr/bin`
    while an intra-step `export PATH=/usr/lib/postgresql/16/bin:…` stays behind
    passed all four tests, and on the LEG that is the full revert — `/usr/bin`
    resolves through `pg_wrapper` to the image's newest client (17), while the
    in-step check still prints 16 because it runs under the export. The write
    line is therefore matched as a unit below.
    """
    steps = _steps(job)
    image_majors = _postgres_image_majors(job)
    assert image_majors, "no pinned Postgres server image found — see the test above"
    server_major = image_majors[0]

    _, pin = _pin_step(steps)
    text = _run_text(pin)

    pkg = CLIENT_PKG_RE.search(text)
    assert pkg, f"the pin step does not install `postgresql-client-<major>`: {text!r}"
    assert int(pkg.group(1)) == server_major, (
        f"the client pin installs major {pkg.group(1)} but the provisioned "
        f"servers are pg{server_major}. A client NEWER than the target server "
        "dumps a preamble the target cannot execute — `SET transaction_timeout` "
        "from a PG17 client into a PG16 server is #12051"
    )

    # ONE regex over the WHOLE command: the versioned dir must be the value
    # WRITTEN TO `$GITHUB_PATH`. A bare `CLIENT_PATH_RE`/`"GITHUB_PATH" in text`
    # pair is satisfiable by two different lines and by an inert `export`.
    prepend = GITHUB_PATH_PREPEND_RE.search(text)
    assert prepend, (
        "the pin step never writes a `/usr/lib/postgresql/<major>/bin` entry to "
        "`$GITHUB_PATH`; a prepend that only exists as an `export` inside this "
        "step cannot reach the pytest leg, so the leg would resolve `pg_dump` "
        f"from the runner image instead (step text: {text!r})"
    )
    assert int(prepend.group("major")) == server_major, (
        f"the `$GITHUB_PATH` prepend names major {prepend.group('major')} but the "
        f"provisioned servers are pg{server_major} — the versioned binaries "
        "would lose to the runner image's own client, which is #12051"
    )


def test_the_client_pin_fails_closed_on_a_mismatch(job: dict) -> None:
    """An inherited client must RED the job, not silently produce a bad artifact.

    Without this the pin degrades on a runner-image change: the artifact would
    still be dumped by the newer client, and D8 would red with a message that
    blames the record path (#12051's whole complaint).

    ⛔ THE CONDITION IS ASSERTED, NOT JUST ITS MESSAGE (#12051 review P3).
    `"::error::" in text and "exit 1" in text` cannot tell an ARMED branch from
    a disarmed one: measured, `if [ "$major" != "16" ]; then` → `if false; then`
    left all four tests green while the job would never fail closed. The
    comparison literal is therefore pinned to the servers' major, and the fail
    ACTION is read from that branch's own BODY (`_version_branch`) rather than
    from the step: this PR's package precondition carries `::error::`/`exit 1`
    too, so a whole-step search is satisfied by the precondition alone
    (measured 2026-11-12 on `eb8e4d7268` — gutting the branch body to `:` left
    all six tests in this file green, and a client-major mismatch then reached
    the record leg: the exact silent-unrestorable-artifact failure #12051 is).

    STATED LIMIT: this is a hermetic text guard, so it cannot prove the
    version-EXTRACTION arithmetic — a `sed` rewritten to a constant (`s/.*/16/`)
    still passes. The alternative is arming two real client majors in CI, which
    this job cannot do without provisioning a second client version; the
    in-job assertion (`pg_dump --version` printed and checked) is what makes a
    constant extraction visible in the log instead.
    """
    _, pin = _pin_step(_steps(job))
    text = _run_text(pin)
    server_major = _postgres_image_majors(job)[0]

    branch = _version_branch(_normalised_code(text), server_major)
    assert "::error::" in branch, (
        "the client pin's VERSION BRANCH asserts nothing about the version it "
        "actually got — the branch's own body must `echo \"::error::…\"`, so a "
        "runner image change surfaces as this step, not as an unrestorable "
        "artifact. Read from the BRANCH, not the step: the package precondition "
        "above it carries a `::error::` of its own"
    )
    assert re.search(r"\bexit\s+1\s*(?:;|\}|\)|$)", branch), (
        "the version branch does not `exit 1`: a named error with no exit status "
        "lets the mismatch carry on into the record leg, which is the silent "
        "unrestorable artifact #12051 exists to prevent. Any separator is "
        "accepted — the fact is the exit status, not the punctuation after it"
    )
    assert f'"$major" != "{server_major}"' in text, (
        "the pin never COMPARES the client's major with the servers' "
        f"({server_major}) — a branch that cannot fire is the same symptom as no "
        "branch at all, which is what left #12051 invisible for a day"
    )
    for tool in ("pg_dump", "pg_restore"):
        assert tool in text, (
            f"the pin must assert `{tool} --version`: the drill resolves BOTH "
            "from PATH, and pinning only one leaves the other on the image's "
            "client"
        )


def test_the_pin_runs_before_the_record_leg(job: dict) -> None:
    """`$GITHUB_PATH` affects SUBSEQUENT steps only — position is part of the fix."""
    steps = _steps(job)
    pin_index, _ = _pin_step(steps)

    leg_indexes = [
        index
        for index, step in enumerate(steps)
        if "test_9567_record_capstone.py" in _run_text(step)
    ]
    assert leg_indexes, "the record capstone (D8) is not in any run step of this job"
    assert pin_index < min(leg_indexes), (
        f"the client pin is at step {pin_index}, the record leg at "
        f"{min(leg_indexes)} — a `$GITHUB_PATH` prepend after the leg is inert, "
        "so the leg would run on the runner image's own client"
    )

#: A (runner label -> PostgreSQL client major) fact table, PROVEN and not assumed.
#: ⚠️ WHAT THIS TABLE DECIDES, AFTER THE PGDG CHANGE: it records that a NAMED
#: release is a sound place to run this job — measured by that release's
#: `<codename>-pgdg` suite carrying the client major. The release's OWN ARCHIVE is
#: no longer part of the measurement (and cannot be: `ubuntu-26.04`'s archive
#: carries 18 and no 16, while `resolute-pgdg` carries 16).
#: It does NOT decide where the client comes from: since #12051 round 3 the step
#: INSTALLS `postgresql-client-<major>`, resolving it from `apt.postgresql.org`, and the live
#: invariant is the in-step `pg_dump`/`pg_restore` assertion (major == the two
#: `pgvector/pgvector:pg16` servers'), which no image can quietly change.
#: ⛔ Do not read this table as "the archive supplies the client, so the PGDG
#: block is redundant": it is not. Before `test_the_client_comes_from_pgdg_not_the_distro_archive`
#: existed, deleting that block left this file GREEN on `ubuntu-24.04` (whose archive
#: happens to carry 16) while reverting the fix — that guard is what reds it now.
#: Which client a release's archive carries IS #12051 round 2: `ubuntu-latest`
#: moved to `ubuntu-26.04`, whose archive ships `postgresql-client-18` and NO 16,
#: so the pin died `E: Unable to locate package` before a single test ran. A new
#: release must be MEASURED and added here before it is adopted — this map is the
#: record of a measurement, not a shape check.
KNOWN_GOOD_IMAGE_MAJOR = {
    # `docker run --rm ubuntu:24.04 apt-cache policy postgresql-client-16`
    #   -> Candidate: 16.15-0ubuntu0.24.04.1
    # and the PGDG suite the step actually installs from:
    # `noble-pgdg` -> 16.15-1.pgdg24.04+2
    "ubuntu-24.04": 16,
}

#: A commented-out line is inert shell that reads EXACTLY like live code, so the
#: content assertions below read the step's code. `tests/test_ci_selection.py`
#: refuses a substring pin for the same reason: a substring pin is satisfied by
#: commenting the body out.
_COMMENT_ONLY_RE = re.compile(r"^\s*#")


def _code_only(text: str) -> str:
    """The step's shell with comment-only lines dropped."""
    return "\n".join(
        line for line in text.splitlines() if not _COMMENT_ONLY_RE.match(line)
    )


#: The precondition, in either shell shape that actually fails closed: an `if !`
#: guard around the probe, or the probe short-circuiting into a `{ … }` / `( … )`
#: group. FACTS are extracted (probe verb either way, quoted or not, any redirect
#: spelling); the SPELLING is deliberately not pinned, because equivalent
#: rewrites are the same behaviour and a guard that reds them is a false block —
#: which teaches the next lane to widen the assertion instead of trusting it.
#:
#: `_normalised_code` erases `${…}` expansions FIRST. Without that, a `}` inside
#: the shipped message (`${ImageOS:-unknown}`) truncates a `.*?\}` body match, so
#: the exact rewrite this guard promises to accept false-red as "does not
#: `exit 1`" — the most misleading possible failure.
_IF_PRECONDITION_RE = re.compile(
    r"^if\s+!\s*(?:sudo\s+)?apt-cache\s+(?:show|policy)\s+['\"]?"
    r"postgresql-client-(?P<major>\d+)['\"]?[^;]*;\s*then(?P<body>.*?)^fi\s*$",
    re.DOTALL | re.MULTILINE,
)
_OR_PRECONDITION_RE = re.compile(
    r"(?:sudo\s+)?apt-cache\s+(?:show|policy)\s+['\"]?"
    r"postgresql-client-(?P<major>\d+)['\"]?[^|\n]*\|\|\s*[({]"
    # The failing branch as a BOUNDED WINDOW of following lines, never up to a
    # matched brace: the shipped message contains `( )` and `{ }` of its own, so
    # brace-matching truncates the body and then reports the guard as missing —
    # the most misleading failure this file could produce.
    r"(?P<body>(?:[^\n]*\n){0,5}[^\n]*)",
    re.MULTILINE,
)

_PARAM_EXPANSION_RE = re.compile(r"\$\{[^}]*\}")


def _normalised_code(text: str) -> str:
    """Comment-stripped shell with `${…}` expansions erased.

    Erasing them is control-flow-neutral (an expansion never opens or closes a
    block) and it is what makes a `}` inside a message string invisible to the
    block matchers.
    """
    return _PARAM_EXPANSION_RE.sub("", _code_only(text))


def _precondition(code: str) -> re.Match[str]:
    """The package-resolvability precondition, matched as a BLOCK, not a token.

    A bare `"apt-cache show" in text` is satisfied by a commented-out line and by
    a block that is present but unreachable, so the caller passes
    `_normalised_code(...)`. Reachability itself is NOT provable from text — that
    is what `test_the_precondition_actually_fires_when_the_package_is_absent`
    executes the committed step for.
    """
    match = _IF_PRECONDITION_RE.search(code) or _OR_PRECONDITION_RE.search(code)
    assert match, (
        "the pin does not precondition on the package being RESOLVABLE, so an "
        "image whose archive lacks the major reds as `E: Unable to locate "
        "package` (exit 100) — an opaque failure that reads as a typo in this "
        "step, which is how the 2026-11-12 image move hid behind a "
        "#12051-shaped red. Read from the step's CODE (comment lines stripped), "
        "because a substring pin is satisfied by commenting the body out."
    )
    return match


#: The version branch as a BLOCK, for the same reason `_precondition` is a block:
#: a substring pin cannot tell an ARMED branch from a gutted one. The step now
#: contains SIX `::error::`/`exit 1` pairs — the five install-path guards and this
#: branch — where `origin/main` carried TWO (the package precondition and this
#: branch). So a whole-step search is satisfied by ANY of the five, which is why
#: the fail action is read from this branch's own body.
#: MEASURED 2026-11-12 (fresh review of `eb8e4d7268`): the assertion this replaces
#: stayed green with `if [ "$major" != "16" ]; then :; fi`.
#:
#: TWO patterns for the same FACT, the idiom `_precondition` already uses: a
#: `[ … ]` test and a `test …` test are the same behaviour, and a guard that reds
#: one is a false block — which teaches the next lane to widen the assertion
#: instead of trusting it (MEASURED, review of `92155a0c5d`: a single-pattern
#: version red-ed both `if test "$major" != "16"; then` and a `then` on the line
#: after the condition, with a message asserting an unrestorable artifact that
#: spelling change cannot cause). Quoting, `;`, and a newline before `then` are
#: all accepted for the same reason.
_VERSION_BRANCH_RES = (
    re.compile(
        r"^(?P<ind>[ \t]*)if\s+\[\s*\"?\$major\"?\s*!=\s*\"?(?P<major>\d+)\"?\s*\]"
        r"\s*;?\s*then[ \t]*$",
        re.MULTILINE,
    ),
    re.compile(
        r"^(?P<ind>[ \t]*)if\s+test\s+\"?\$major\"?\s*!=\s*\"?(?P<major>\d+)\"?"
        r"\s*;?\s*then[ \t]*$",
        re.MULTILINE,
    ),
)


def _closing_fi(indent: str) -> re.Pattern[str]:
    """The branch END at the HEADER's own indent, so a nested `fi` cannot end it early."""
    return re.compile("^" + re.escape(indent) + r"fi[ \t]*$", re.MULTILINE)


def _version_branch(code: str, server_major: int) -> str:
    """The `if [ "$major" != "<server_major>" ]; then … fi` BODY.

    The fail action is read from this body and never from the step as a whole:
    the package precondition above it also emits `::error::` and exits 1, so a
    whole-step search reports a gutted branch as armed.

    ⚠️ STATED LIMIT: reachability of THIS branch is not provable from text, and
    nothing in this file executes it — the executable arm below
    (`test_the_precondition_actually_fires_when_the_package_is_absent`) runs the
    step's PREFIX for the precondition only, so a fail action wrapped in an
    `if false` INSIDE this branch stays green here (measured, review of
    `92155a0c5d`). Text cannot distinguish armed from present-but-dead, which is
    why the step also prints `pg_dump --version` and fails on it: a dead branch
    is visible in the job log even when no test reds.
    """
    match = None
    for header in _VERSION_BRANCH_RES:
        match = header.search(code)
        if match:
            break
    assert match, (
        "the pin has no `if [ \"$major\" != \"<major>\" ]; then … fi` block — a "
        "branch that cannot be FOUND is the same symptom as no branch at all: "
        "the job would carry on with a client whose major differs from the "
        f"servers' ({server_major}) and the artifact would be unrestorable"
    )
    end = _closing_fi(match.group("ind")).search(code, match.end())
    assert end, (
        "the version branch's `if` has no closing `fi` — the branch is not a "
        "block, so nothing in it is guaranteed to run"
    )
    body = code[match.end() : end.start()]
    assert int(match.group("major")) == server_major, (
        "the version branch compares the client's major with "
        f"{match.group('major')}, but BOTH provisioned servers are "
        f"{server_major} — a branch pinned to any other major can never fire"
    )
    return body


def _runs_on_labels(job: dict) -> list[str]:
    """`runs-on` as a label list — a one-element list is the same request."""
    runs_on = job.get("runs-on")
    if isinstance(runs_on, str):
        return [runs_on]
    assert isinstance(runs_on, list), (
        f"`{JOB}` must name its image(s) literally; found runs-on={runs_on!r}. An "
        "expression makes the client major depend on what the expression "
        "resolved to, which no assertion in this file can see."
    )
    return [str(label) for label in runs_on]


def _has_live_substitution(text: str) -> bool:
    r"""True when `text` runs a command substitution rather than printing the span.

    ⛔ `\\$(` IS LIVE, and this is why a substring test for `\\$(` is wrong: bash
    consumes one backslash, prints one, and then executes the substitution. Only an
    ODD number of preceding backslashes escapes it. MEASURED, review cycle 8:
    `echo "value: \\$(uname -s)"` stayed GREEN under the substring version of this
    guard while bash executed `uname -s` and printed its output. The run is counted,
    so `$(` (0) and `\\$(` (2) are LIVE, while `\$(` (1) is not.
    """
    index = 0
    while True:
        found = text.find("$(", index)
        if found < 0:
            return False
        backslashes = found
        while backslashes > 0 and text[backslashes - 1] == "\\":
            backslashes -= 1
        if (found - backslashes) % 2 == 0:
            return True
        index = found + 2


def test_no_reserved_error_message_asks_the_shell_to_do_something(job: dict) -> None:
    r"""⛔ A BACKTICK INSIDE `echo "…"` IS COMMAND SUBSTITUTION, WHICH SILENTLY EDITS THE MESSAGE.

    MEASURED TWICE WHILE BUILDING #12051 — cycle 1 and again in cycle 5. A message like
    ``echo "::error::… via `/etc/os-release`'s …"`` does not print the filename: bash RUNS
    it, the span is replaced by its (empty) stdout, and the diagnostic loses the very token
    a reader needs. When the span happens to be a command that exists, the step EXECUTES it.

    Nothing else in this file could see that: every other assertion only asks whether
    `::error::` and `exit 1` are PRESENT, so a mangled message passes all of them. This is
    the one property of the printed text no evidence-shaped assertion covers, so it is
    asserted structurally over the step's CODE:

      · a BACKTICK anywhere is refused — there is no legitimate one in this step, and a
        backtick in a double-quoted `echo` is the exact defect this test exists for;
      · `$(` is refused when it appears AFTER an `echo`/`printf` on a CODE line, at
        any indentation or position (`… && echo "…"`, `printf '%s\n' "…"`), while
        staying legal where the step substitutes on purpose:
        `major="$(printf '%s\n' "$version" | sed …)"` puts the `printf` INSIDE a
        substitution whose output is wanted — the opposite case, and the first cut of this
        test false-redded exactly that line (measured, cycle 6).
      · DELIBERATELY CONSERVATIVE, and said so here so it reads as a choice rather than a
        bug: an ESCAPED `\$(` (an ODD run of backslashes) prints literally and is
        exempted — `\\$(` is NOT, because bash consumes one backslash, prints it, and then
        runs the substitution
        (MEASURED, review cycle 8) — but text after a trailing `#` on a printing line is
        still refused (the scan cannot tell a comment from a `#` inside quotes without a
        parser, and this step has no legitimate `$(` in any printed message). A future
        lane that needs one should move the message to a single-quoted string instead of
        widening this guard.

    ⛔ A LINE-INITIAL `echo` SCAN IS NOT ENOUGH, and that is a correction this test needed: an
    earlier version checked only lines whose stripped form BEGAN with `echo `, so a backticked
    `… && echo "…"` or a `printf "…`cmd`…"` passed — MEASURED, review cycle 5 of
    `850e25c349`: both spellings stayed GREEN with a live backtick in the step.
    """
    _, pin = _pin_step(_steps(job))
    code = _code_only(_run_text(pin))
    offenders: list[str] = []
    for line in code.splitlines():
        stripped = line.strip()
        if "`" in stripped:
            offenders.append(stripped[:140])
            continue
        for match in re.finditer(r"\b(?:echo|printf)\b", stripped):
            if _has_live_substitution(stripped[match.end():]):
                offenders.append(stripped[:140])
                break
    assert not offenders, (
        "a message this step PRINTS asks the shell to substitute something — a backtick "
        "(anywhere in the step) or `$(` on a printing line runs as a command and its "
        "output replaces the text, so the diagnostic silently loses (or executes) that "
        "span. Name the thing in plain words or single quotes: " + repr(offenders)
    )


def test_the_pin_runs_on_a_named_image_that_carries_the_servers_major(job: dict) -> None:
    """The image is NAMED, and its release has been measured for this major.

    Round 2 of #12051. MEASURED 2026-11-12 (job `131022562781`, head
    `58b5a417ba`), `ubuntu-latest` had migrated to `Image: ubuntu-26.04`, whose
    archive ships `postgresql-client-18` and no 16 — so the install died
    `E: Unable to locate package` (exit 100) BEFORE the version assertion, and
    every leg below skipped. A floating label lets the runner image change under
    a drill whose artifact depends on the client's major, with no diff to blame.

    ⚠️ WHAT THIS TEST STILL OWNS, now that the install comes from PGDG: the image
    must be a NAMED release, and that release must be one somebody measured. It
    does NOT own "the archive supplies the client" any more — that claim is what
    made the distro-archive pin fragile in the first place, and the live
    invariant is the in-step `pg_dump`/`pg_restore` major assertion. The
    (release -> major) pair is asserted rather than the label's SHAPE, because
    `ubuntu-26.04` is just as version-pinned as `ubuntu-24.04` and is the release
    that broke.
    """
    server_major = _postgres_image_majors(job)[0]
    labels = _runs_on_labels(job)

    assert "ubuntu-latest" not in labels, (
        "`ubuntu-latest` is a MOVING label and it moved: on 2026-11-12 it became "
        "ubuntu-26.04, whose archive has no `postgresql-client-16`, so this job "
        "reds before a single test runs (#12051). Name the release."
    )
    assert len(labels) == 1 and labels[0].startswith("ubuntu-"), (
        f"`{JOB}` asks for {labels!r}. This job needs ONE named hosted image: any "
        "second label (e.g. `self-hosted`) makes the box's release an inherited "
        "fact of whichever machine answers — the exact inheritance #12051 "
        "removed, and the #10289 routing drift that the in-step checks exist to "
        "name. This assertion is the contract."
    )
    label = labels[0]
    assert label in KNOWN_GOOD_IMAGE_MAJOR, (
        f"`{JOB}` runs on {label!r}, which nobody has measured for this drill. "
        "PROVE it — run the pin's own ORIGIN check on that image (`apt-cache "
        f"policy postgresql-client-{server_major}` after adding its `-pgdg` "
        "suite): the candidate must be listed under apt.postgresql.org — then "
        "add the pair to KNOWN_GOOD_IMAGE_MAJOR. Adopting an unmeasured release "
        "is how #12051 round 2 shipped."
    )
    assert KNOWN_GOOD_IMAGE_MAJOR[label] == server_major, (
        f"`{JOB}`'s two servers are pg{server_major}, but {label}'s `-pgdg` suite "
        f"is measured for a pg{KNOWN_GOOD_IMAGE_MAJOR[label]} client: the drill "
        "would install a client whose major differs from the restore target "
        "(#12051)."
    )

    _, pin = _pin_step(_steps(job))
    code = _normalised_code(_run_text(pin))
    precondition = _precondition(code)
    assert int(precondition.group("major")) == server_major, (
        f"the precondition probes postgresql-client-"
        f"{precondition.group('major')} but the servers are pg{server_major}"
    )
    body = precondition.group("body")
    assert "::error::" in body, (
        "the precondition fails silently: an absent package must be reported as "
        "its OWN named failure, not as the archive's opaque apt error"
    )
    assert re.search(r"\bexit\s+1\s*(?:;|\}|\)|$)", body), (
        "the precondition does not `exit 1`, so a missing client would fall "
        "through to the install and surface as exit 100 again. Any separator is "
        "accepted — the fact is the exit status, not the punctuation around it."
    )

    # ⛔ ORDER IS PART OF THE FIX. A precondition placed AFTER the install is
    # inert: the install has already died with the opaque `exit 100` this block
    # exists to replace.
    assert code.index("apt-cache") < code.index("apt-get install"), (
        "the package precondition runs AFTER `apt-get install`, so on an image "
        "whose archive lacks the major the job dies with the archive's opaque "
        "`E: Unable to locate package` and never reaches the named error"
    )

    # ⛔ THE INSTALL LINE IS WHAT PICKS THE CLIENT. Asserted on the install
    # itself, not on `CLIENT_PKG_RE.search(text)`: the precondition above names a
    # package too and sits earlier, so a first-match read would satisfy an
    # install-major claim by reading the PROBE (measured: switching the install
    # to -18 stayed green under the old read).
    install = re.search(
        r"apt-get\s+install[^\n]*postgresql-client-(?P<major>\d+)", code
    )
    assert install, "the pin never runs `apt-get install postgresql-client-<major>`"
    assert int(install.group("major")) == server_major, (
        f"the pin INSTALLS postgresql-client-{install.group('major')} while the "
        f"servers are pg{server_major}: the versioned PATH prepend would then "
        "point at a directory the install never created"
    )


def test_the_client_comes_from_pgdg_not_the_distro_archive(job: dict) -> None:
    """⛔ DELETING THE PGDG BLOCK USED TO LEAVE THIS ENTIRE FILE GREEN.

    #12051 round 3 exists because Ubuntu's own archive carries only the major its
    RELEASE ships: `ubuntu-latest` rolled to 26.04 (client 18, no 16) and the pin
    died `E: Unable to locate package`, exit 100, before a single test ran. The
    remedy is to install from `apt.postgresql.org`, which publishes every
    supported major for every supported release.

    A guard is needed because the OLD mechanism still works on the label that is
    pinned today: `ubuntu-24.04`'s archive carries `postgresql-client-16` too, so
    deleting the five PGDG lines — or moving `apt-get update` above them — would
    revert this fix while leaving every other assertion in this file green
    (MEASURED 2026-11-12, fresh review of `f55bf421ec`: both mutations →
    `6 passed`). The regression would then surface only when a label moves again,
    which is the class #12051 removes.

    Read from `_code_only` (comments stripped, `${…}` INTACT — the suite name is
    an expansion, so `_normalised_code` would erase the fact under test).
    """
    _, pin = _pin_step(_steps(job))
    code = _code_only(_run_text(pin))

    assert "https://www.postgresql.org/media/keys/ACCC4CF8.asc" in code, (
        "the step no longer imports the PGDG signing key (ACCC4CF8.asc). Without "
        "it the `signed-by` on the sources line points at nothing and every "
        "install from that repo fails closed — or, worse, the archive is used "
        "instead and the client major is inherited from the image again (#12051)."
    )

    sources = [
        line for line in code.splitlines()
        if "apt.postgresql.org/pub/repos/apt" in line
    ]
    assert len(sources) == 1, (
        "expected exactly ONE PGDG sources line, found "
        f"{len(sources)}: {sources!r}. #12051 round 3's whole mechanism is that "
        "one line."
    )
    sources_line = sources[0]
    assert "signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc" in sources_line, (
        "the PGDG sources line does not bind the keyring it just installed, so "
        "apt would have to accept the repo unsigned (or refuse it): "
        f"{sources_line!r}"
    )
    assert "${VERSION_CODENAME}-pgdg" in sources_line, (
        "the PGDG suite is not derived from the box's own codename, so the step "
        "would pin one release's suite and die on every other one — the exact "
        "moving-target class this change removes: "
        f"{sources_line!r}"
    )
    assert ". /etc/os-release" in code, (
        "the step reads `${VERSION_CODENAME}` but never sources `/etc/os-release`, "
        "so under `set -u` it aborts on an unset variable instead of naming the "
        "cause"
    )
    assert '${VERSION_CODENAME:-}' in code, (
        "the codename is used unguarded, so a box whose os-release carries none "
        "aborts `set -u` at the sources line with no `::error::` — the exact "
        "unnamed failure the guards exist to replace. Guard it explicitly."
    )
    # ⛔ THE KEYRING'S DIRECTORY: the FACT is that it exists before the key is written
    # into it, so this asserts the ORDER and accepts every equivalent spelling.
    # PGDG publishes that directory inside `postgresql-common`, which a bare runner
    # image does not carry, so `install -m 0644` into a missing directory fails
    # `No such file or directory` — unnamed, one line before the guard.
    # MEASURED, review cycle 3 of `c86a3efde`: pinning the SPELLING instead (a bare
    # `"install -d …" in code`) was BOTH under- and over-tight — swapping the two
    # `install` lines (which reproduces the exact runtime failure the message names)
    # stayed GREEN, while the equivalent `install -D -m 0644 …` (which creates the
    # leading components; verified rc=0) FALSE-REDDED.
    key_install = re.search(
        r"install\s+[^\n]*apt\.postgresql\.org\.asc", code
    )
    assert key_install, (
        "the PGDG keyring is never installed with `install … apt.postgresql.org.asc` — "
        "the `signed-by` on the sources line would point at nothing"
    )
    # ⛔ `-D` IS SEARCHED ACROSS EVERY OPTION TOKEN on that line, not just the first.
    # `install -D -m 0644 …` and `install -m 0644 -D …` are the SAME behaviour (both
    # create the leading components; verified rc=0 in a container), and reading only
    # the first token false-redded the second spelling — measured, review cycle 4 of
    # `3c2946ce9c`. The lowercase `-d` of the `mkdir` form is a different flag and
    # does not appear on the key-install line, so `D` alone is the discriminator.
    creates_dirs = re.search(r"(?:^|\s)-[A-Za-z]*D", key_install.group(0))
    if not creates_dirs:
        # Without `-D` the parent must be created FIRST, and that ordering is the fact.
        # Every equivalent spelling is accepted — `install -d` (options on either side of
        # `-d`) and `mkdir -p` — because the runtime fact is "the directory exists before
        # the file is written", not which verb created it. MEASURED, review cycle 5 of
        # `850e25c349`: a `mkdir -p` rewrite and `install -d -m 0755` both FALSE-REDDED
        # under the previous spelling-only match, and `mkdir` was missing from the
        # executed arm's stub set, so that arm redded with it.
        mkdir = re.search(
            r"(?:install\b[^\n]*?-d\b[^\n]*|mkdir\b[^\n]*)"
            r"/usr/share/postgresql-common/pgdg",
            code,
        )
        assert mkdir, (
            "the keyring's DIRECTORY is not created before the key is written into it "
            "(and this is not the `install -D` form, which would create it): PGDG "
            "publishes that directory inside `postgresql-common`, so on a bare runner "
            "image `install -m 0644` fails `No such file or directory` — unnamed, one "
            "line before the guard"
        )
        assert mkdir.start() < key_install.start(), (
            "`install -d` of the keyring directory runs AFTER the key is written into "
            "it, which reproduces exactly the unnamed `No such file or directory` abort "
            "this guard exists to prevent. Order is the fact here, not presence."
        )

    # ⛔ ORDER IS THE MECHANISM. Sources added, THEN the indexes refreshed, THEN the
    # install. Any other order installs from the distro archive (or from an index
    # that does not know the PGDG package yet) and the pin silently reverts.
    assert code.index("apt.postgresql.org/pub/repos/apt") < code.index("apt-get update"), (
        "the PGDG source is added AFTER `apt-get update`, so the package index "
        "never learns about `postgresql-client-16` and the install falls back to "
        "the distro archive — #12051 round 3 reverted with every check green"
    )
    assert code.index("apt-get update") < code.index("apt-get install"), (
        "`apt-get install` runs BEFORE `apt-get update`, so it installs against a "
        "stale index that predates the PGDG source"
    )

    # ⛔ THE ORIGIN, NOT THE PROBE'S EXIT STATUS. `apt-get update` only WARNS (exit 0)
    # when a repo cannot be FETCHED, so a bare `apt-cache show` passes on any release
    # whose archive also carries the major — the mechanism gone, the step green.
    # MEASURED, review cycle 7 of `fc9d4204aa`: PGDG unresolvable -> update exit 0,
    # `apt-cache show postgresql-client-16` exit 0 from the distro archive.
    probes = [
        line for line in code.splitlines()
        if re.search(r"apt-cache\s+(?:show|policy)\s+['\"]?postgresql-client-\d+", line)
        # ⛔ NOT the message that NAMES the probe: the guard's own `::error::` quotes
        # `apt-cache policy postgresql-client-16` and the package's origin, so without
        # this exclusion the assertion is satisfied by PROSE and survives removing the
        # origin predicate (MEASURED, review cycle 8 mutation b).
        and "::error::" not in line
    ]
    assert probes, (
        "the step has no availability probe before the install, so a failed index "
        "fetch reaches `apt-get install` and reads as apt's own exit 100"
    )
    assert any("apt.postgresql.org" in line for line in probes), (
        "the availability probe never asserts WHERE the candidate came from, so on a "
        "release whose own archive carries the major it passes with PGDG never applied "
        "(`apt-get update` only WARNS on a failed fetch) — the fix silently reverted "
        f"with every check green. The probe must prove apt.postgresql.org lists this major: "
        f"{probes!r}"
    )
    # ⚠️ DELIBERATELY NOT scoped to apt's `Candidate:` row. A release whose own archive
    # carries a HIGHER revision of the same major still installs the archive's build, and
    # that is a VALID state — the artifact contract is the major, held by the in-step
    # `pg_dump`/`pg_restore` assertion. Scoping this predicate to `Candidate:` would red the
    # job on a correct tree, which is a false block, not a stronger guard. If the contract
    # ever moves to "the PGDG build specifically", this is where it must be re-cut.

    # ⛔ AND THE INSTALL ITSELF IS NAMED. It is the one pre-version branch the executable
    # arm cannot reach (that arm stops at the install line by construction), so text is all
    # there is — and without this assertion adding the guard would itself be the
    # unguarded-claim class: the branch could be deleted and every test would stay green.
    # MEASURED, review cycle 5 of `850e25c349`: a POOL box can hold the dpkg frontend lock or
    # run out of disk, failing the INSTALL after a clean probe — which would otherwise surface
    # as apt's own text, i.e. the un-named class these guards replace.
    guarded_install = re.search(
        r"^(\s*)if\s+!\s*(?:sudo\s+)?apt-get\s+install[^\n]*;\s*then(?P<body>.*?)^\1fi\s*$",
        code,
        re.DOTALL | re.MULTILINE,
    )
    assert guarded_install, (
        "the install itself is unguarded: `apt-get install` must be wrapped in an "
        "`if ! … then … exit 1; fi` that NAMES the failure, because a resolved package "
        "that cannot be installed (dpkg frontend lock, .deb fetch, disk) is not the same "
        "defect as a missing one"
    )
    install_body = guarded_install.group("body")
    assert "::error::" in install_body, (
        "the install guard stops the step but names nothing, so a dpkg-lock or disk "
        f"failure still reads as apt's own text: {install_body!r}"
    )
    assert re.search(r"\bexit\s+1\s*(?:;|\}|\)|$)", install_body), (
        "the install guard does not `exit 1`, so a failed install carries on into the "
        f"version assertion with no client at all: {install_body!r}"
    )


def test_the_precondition_actually_fires_when_the_package_is_absent(
    job: dict, tmp_path: Path
) -> None:
    """REACHABILITY, executed — text cannot prove an armed guard from a dead one.

    Every other assertion in this file reads the step's text, and text does not
    distinguish "the guard is armed" from "the guard is present but unreachable".
    MEASURED, all of these satisfied the text assertions:
      · `... >/dev/null 2>&1 || true; then` — the probe's status thrown away
      · `exit 1` moved inside `if false; then … fi` — the token is still there
      · the whole block wrapped in `if [ -n "${NEVER_SET:-}" ]; then … fi` — the
        DISARMING spelling, which REDs this test (measured: absent stub → rc=0,
        `REACHED_PAST_PRECONDITION` printed, no `::error::`).

    ⚠️ NOT in that list: the same wrapper spelled `-z`. `[ -z "" ]` is TRUE, so
    the wrapper's body still runs and the guard stays ARMED — measured: absent
    stub → rc=1 with `::error::`, no marker, so BOTH arms stay GREEN. A wrapper
    the guard still runs is a blind spot in this docstring's framing, not a
    bypass; it is named here so nobody reads `-z` as a way past the guard.

    So this test RUNS the committed step's prefix — verbatim, from `run:`, up to
    (not including) the `apt-get install` line — under `bash`, with `sudo`,
    `apt-get`, `apt-cache`, `install`, `mkdir`, `curl` and `tee` stubbed.
    Hermetic: no network, no apt, no sudo, and no write outside `tmp_path`.

    ⛔ THE STUB SET MUST COVER EVERY PRIVILEGED/EFFECTFUL COMMAND IN THE PREFIX,
    not only the ones the guard itself calls. MEASURED 2026-11-12 on the rebase of
    the PGDG change onto #12071: the prefix had grown a repo setup
    (`sudo install -d /usr/share/postgresql-common/pgdg`, a `curl` of the PGDG
    key, `| sudo tee /etc/apt/sources.list.d/pgdg.list`), and with `sudo`
    forwarding to the real command the absent-stub run died on
    `install: cannot create directory '/usr/share/postgresql-common': Permission
    denied` — rc≠0, no `::error::`, so this test reported the guard as disarmed
    when it was armed. On a box running the suite as root it would instead have
    written the HOST's apt sources and fetched from the network — which is why
    the stubs below are part of the contract this test claims, not decoration.

    ⛔ AND THE HOST's `/etc/os-release` IS THE SAME CLASS ONE STEP FURTHER. The
    prefix contains `. /etc/os-release` (the PGDG suite is derived from the box's
    codename), so running it verbatim reads a HOST file — the one thing a suite
    that claims hermeticity must not need. MEASURED 2026-11-12: on a box without
    that file both arms die at the source line, and the ABSENT arm then fails on
    `assert "::error::" in combined` — i.e. it reports an ARMED guard as silent,
    about a missing host file. The single `. /etc/os-release` is therefore
    REPLACED with a fixture this test writes (`VERSION_CODENAME=noble`), and the
    substitution asserts it matched exactly once, so a future rename reds here
    instead of silently restoring the host dependency.

    ⏔ AND THE ORIGIN CHECK'S POLICY FILE IS THE SAME CLASS A THIRD TIME. The
    guard writes `apt-cache policy` to a `mktemp` file (a FIXED `/tmp` path was the
    first cut; this job is pool-eligible and a shared box can pre-create that path
    as a symlink), so the prefix run verbatim would create the HOST's temp file —
    outside this test's hermeticity claim. The `mktemp` CALL is REPLACED with a
    path under `tmp_path`; the substitution asserts it matched exactly once, and
    that `"$policy_file"` is used exactly twice (the redirect and the `grep`
    argument), so a future rewrite reds here instead of silently restoring the
    host dependency.

    Six arms over the step's FOUR named pre-install branches — the key fetch, the
    codename, `apt-get update`, and the ORIGIN check (THREE arms: an `apt-cache`
    that fails, an index that loaded but does not name apt.postgresql.org, and
    the control that reaches past) — plus TWO branches no arm executes: the
    install guard (unreachable by construction, since this arm stops AT the install line;
    it is pinned in text by `test_the_client_comes_from_pgdg_not_the_distro_archive`) and
    the version branch (its reachability is a stated limit, see `_version_branch`).
    A branch nothing executes is the unguarded-claim class this file exists to refuse, so
    this sentence is the AUDIT of that: it names every branch and says which ones are
    executed, and it is re-cut whenever the branch or arm count moves rather than left to
    drift. (It was already stale once — review cycle 6 of `5ed3b1950b` — which is why the
    install guard is named here explicitly rather than folded into "the version branch is
    the only unexecuted one".)
    """
    _, pin = _pin_step(_steps(job))
    lines = _run_text(pin).splitlines()
    install_at = next(
        (i for i, line in enumerate(lines) if re.search(r"apt-get\s+install", line)),
        None,
    )
    assert install_at is not None, "the pin has no `apt-get install` line to run up to"
    prefix = "\n".join(lines[:install_at]).rstrip()
    assert "apt-cache" in prefix, (
        "the precondition is not before the install in the committed step, so "
        "there is nothing to execute ahead of it (the order assertion above "
        "should already have caught this)"
    )

    os_release = tmp_path / "os-release"
    # ⛔ COUNT ON THE CODE, SUBSTITUTE IN THE PREFIX. The prefix is run VERBATIM (that is the
    # point of this arm), so it carries comments too — and a comment that merely MENTIONS
    # `. /etc/os-release` made a raw count see two sources and red an armed step (MEASURED,
    # review cycle 6 of `5ed3b1950b`: the guard's own explanatory comment tripped it). The
    # executable occurrence is the one in CODE; substituting in the raw prefix as well is
    # harmless because a comment's copy is inert either way.
    substituted = _code_only(prefix).count(". /etc/os-release")
    assert substituted == 1, (
        "the prefix's CODE is expected to source `/etc/os-release` exactly once so this "
        "test can point it at a fixture; found "
        f"{substituted}. Re-derive the substitution rather than letting the arm "
        "fall back to reading the HOST's file"
    )
    prefix = prefix.replace(". /etc/os-release", f". {os_release}")

    # The key file is `mktemp` too (a shared box can pre-create a FIXED `/tmp` path as a
    # symlink and `curl -o` follows symlinks), so that call is substituted the same way.
    key_file = tmp_path / "pgdg.asc"
    key_sites = _code_only(prefix).count('key_file="$(mktemp)"')
    assert key_sites == 1, (
        "the key file is expected to be created by exactly one `key_file=\"$(mktemp)\"` in "
        f"the prefix's CODE; found {key_sites}. Re-derive the substitution rather than letting "
        "the arm write the HOST's temp dir"
    )
    prefix = prefix.replace('key_file="$(mktemp)"', f'key_file="{key_file}"')
    for use in ('-o "$key_file"', 'install -m 0644 "$key_file"'):
        assert use in _code_only(prefix), (
            f"expected `{use}` in the committed step — the key file must be the one `curl` "
            "wrote and the one `install` copies"
        )

    policy_file = tmp_path / "pgdg-policy.txt"
    mktemp_sites = _code_only(prefix).count('policy_file="$(mktemp)"')
    assert mktemp_sites == 1, (
        "the origin guard's policy file is expected to be created by exactly one "
        f"`policy_file=\"$(mktemp)\"` in the prefix's CODE; found {mktemp_sites}. "
        "Re-derive the substitution rather than letting the arm write the HOST's temp dir"
    )
    prefix = prefix.replace('policy_file="$(mktemp)"', f'policy_file="{policy_file}"')
    uses = _code_only(prefix).count('"$policy_file"')
    assert uses == 2, (
        "the policy file is expected to be READ twice (the `apt-cache policy` redirect "
        f"and the `grep` argument); found {uses}. A rewrite that inlines another path "
        "would make this arm read something the guard does not"
    )

    def run_with(
        stub: str,
        apt_get: str = "apt-get() { return 0; }",
        curl_stub: str = "curl() { return 0; }",
        codename: str = "noble",
    ) -> subprocess.CompletedProcess[str]:
        # The fixture is rewritten per arm, so the codename guard can be exercised
        # without ever reading the host's file.
        os_release.write_text(
            "ID=ubuntu\n" + (f"VERSION_CODENAME={codename}\n" if codename else ""),
            encoding="utf-8",
        )
        script = tmp_path / "precondition.sh"
        # ⛔ `sudo`, `apt-*`, `curl` AND THE REPO-SETUP COMMANDS are stubbed. Without
        # the first two the prefix runs the HOST's real `sudo apt-cache` / `sudo apt-get
        # update`: on a box where sudo is passwordless and the package is known to
        # apt the probe SUCCEEDS, the failure branch is never taken, and the test
        # reports a disarmed guard that is actually armed — and reads the host's
        # package index while doing it. The PGDG setup lines (`install`, `curl`,
        # `tee` — see the docstring) are the same class one step further on: unstubbed,
        # they either abort the prefix before the guard runs or write the host.
        script.write_text(
            'sudo() { "$@"; }\n'
            f"{apt_get}\n{curl_stub}\n"
            "install() { return 0; }\n"
            "mkdir() { return 0; }\n"
            "tee() { cat >/dev/null; }\n"
            f"{stub}\n{prefix}\n"
            "echo REACHED_PAST_PRECONDITION\n",
            encoding="utf-8",
        )
        return subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=60
        )

    absent = run_with(
        'apt-cache() { echo "E: Unable to locate package" >&2; return 100; }'
    )
    combined = absent.stdout + absent.stderr
    assert absent.returncode != 0, (
        "with `apt-cache` FAILING the committed precondition did not stop the "
        "step, so it is unreachable or disarmed (`|| true`, an `exit 1` inside a "
        "dead branch, or a wrapper that never runs). On a real image without the "
        f"client this reaches `apt-get install` and dies with exit 100. Output: {combined!r}"
    )
    assert "REACHED_PAST_PRECONDITION" not in absent.stdout, (
        "the precondition let the script continue past itself with the package "
        f"absent — it is not armed: {combined!r}"
    )
    assert "::error::" in combined, (
        "the precondition stopped the step but never NAMED the cause: the whole "
        f"point is that a missing client reads as THIS defect, not an apt error: {combined!r}"
    )

    present = run_with(
        'apt-cache() { echo "Candidate: 16.15-1.pgdg24.04+2"; '
        'echo "     500 https://apt.postgresql.org/pub/repos/apt noble-pgdg/main amd64 Packages"; }'
    )
    assert present.returncode == 0, (
        "with the candidate listed by apt.postgresql.org the precondition stopped the "
        f"step — the job would red on the label it is pinned to: {present.stderr!r}"
    )
    assert "REACHED_PAST_PRECONDITION" in present.stdout, (
        "the script never reached the line after the precondition even when the "
        "package resolved from PGDG — the block is unconditional"
    )

    # ⛔ THE SOFT FAILURE — the state a BARE probe cannot see, and the one the origin
    # check exists for. MEASURED, review cycle 7 of `fc9d4204aa`: with the PGDG host
    # unresolvable, `apt-get update` exits 0 (`W: Some index files failed to download`)
    # and `apt-cache show postgresql-client-16` still succeeds from the DISTRO archive
    # on noble — so this arm is the only thing that reds the reverted mechanism here.
    not_pgdg = run_with(
        'apt-cache() { echo "Candidate: 16.15-0ubuntu0.24.04.1"; '
        'echo "     500 http://archive.ubuntu.com/ubuntu noble-updates/main amd64 Packages"; }'
    )
    not_pgdg_combined = not_pgdg.stdout + not_pgdg.stderr
    assert not_pgdg.returncode != 0, (
        "with the candidate available ONLY from the distro archive the step carried "
        "on — that is the silent fallback #12051 exists to remove: the drill runs and "
        f"nobody learns that apt.postgresql.org was never reached: {not_pgdg_combined!r}"
    )
    assert "REACHED_PAST_PRECONDITION" not in not_pgdg.stdout, (
        "the step continued past the origin check with no PGDG candidate, so an "
        f"egress failure that apt only WARNS about is invisible: {not_pgdg_combined!r}"
    )
    assert "::error::" in not_pgdg_combined, (
        "the origin check stopped the step but named nothing — a soft index failure "
        f"must read as THIS defect, not as apt's own text: {not_pgdg_combined!r}"
    )

    # ⛔ THE OTHER NAMED BRANCH — `apt-get update` failing (repo, key or egress).
    # Without this arm that branch is an UNGUARDED CLAIM: it could be deleted and
    # nothing here would notice, and the failure it exists to name (apt's own
    # `E: … does not have a Release file`, exit 100) would come back opaque. The
    # probe is left SUCCEEDING so only the update branch can stop the script.
    update_failed = run_with(
        "apt-cache() { return 0; }",
        apt_get="apt-get() { return 100; }",
    )
    update_combined = update_failed.stdout + update_failed.stderr
    assert update_failed.returncode != 0, (
        "with `apt-get update` FAILING the step carried on — on a runner whose "
        "PGDG source or signing key does not resolve, that means the job "
        f"continues into an install that cannot succeed: {update_combined!r}"
    )
    assert "REACHED_PAST_PRECONDITION" not in update_failed.stdout, (
        "a failed `apt-get update` did not stop the script, so the repo-setup "
        f"failure is not named and not fatal: {update_combined!r}"
    )
    assert "::error::" in update_combined, (
        "`apt-get update` stopped the step but nothing NAMED it — the whole point "
        "of the update guard is that a PGDG key/suite/egress failure reads as "
        f"THIS defect rather than as apt's opaque exit 100: {update_combined!r}"
    )

    # ⛔ AND THE KEY FETCH — the most likely PGDG failure of all (egress, DNS, a
    # 503 from www.postgresql.org). `set -e` would abort at curl with its own
    # rc=22 and NO name, which is the class this step exists to remove.
    curl_failed = run_with(
        "apt-cache() { return 0; }",
        curl_stub=(
            'curl() { echo "curl: (22) The requested URL returned error: 503" >&2; '
            "return 22; }"
        ),
    )
    curl_combined = curl_failed.stdout + curl_failed.stderr
    assert curl_failed.returncode != 0, (
        "with `curl` FAILING the step carried on — on a runner that cannot reach "
        "www.postgresql.org the key is never installed and every later branch is "
        f"misleading: {curl_combined!r}"
    )
    assert "REACHED_PAST_PRECONDITION" not in curl_failed.stdout, (
        "a failed key fetch did not stop the script, so the step proceeds without "
        f"the signing key it just claimed to install: {curl_combined!r}"
    )
    assert "::error::" in curl_combined, (
        "the key fetch stopped the step but named nothing — `set -e` would surface "
        f"curl's exit 22 as an opaque failure: {curl_combined!r}"
    )

    # ⛔ AND AN os-release WITHOUT A CODENAME — the `set -u` abort the explicit
    # guard replaces. `[ -z "${VERSION_CODENAME:-}" ]` must fire and NAME it.
    no_codename = run_with("apt-cache() { return 0; }", codename="")
    codename_combined = no_codename.stdout + no_codename.stderr
    assert no_codename.returncode != 0, (
        "a fixture without VERSION_CODENAME did not stop the step, so the sources "
        "line would be built from an empty suite name (or `set -u` would abort "
        f"unnamed): {codename_combined!r}"
    )
    assert "REACHED_PAST_PRECONDITION" not in no_codename.stdout, (
        "the codename guard let the script continue without a suite name: "
        f"{codename_combined!r}"
    )
    assert "::error::" in codename_combined, (
        "a missing VERSION_CODENAME stopped the step but named nothing — the "
        "whole point of the guard is that it reads as THIS defect rather than as "
        f"an unbound-variable abort: {codename_combined!r}"
    )
    # ⛔ THIS LINE IS THE DISCRIMINATOR, not a nicety. Without it the arm cannot tell
    # the guard's own `exit 1` from the `set -u` BACKSTOP one line later: the unguarded
    # `${VERSION_CODENAME}` in the sources line aborts with `unbound variable` and rc=1,
    # and the guard's `::error::` is already in the output — so all three assertions
    # above are satisfied by the backstop. MEASURED, review cycle 3 of `c86a3efde`:
    # replacing the codename guard's `exit 1` with `true` left all 7 tests GREEN.
    assert "unbound variable" not in codename_combined, (
        "the step failed on the shell's `set -u` backstop instead of on the named "
        "guard, so the guard's own `exit 1` is unpinned: a lane could gut it and this "
        f"arm would still pass: {codename_combined!r}"
    )

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
IMAGE_RE = re.compile(r":(?:pg)?(\d+)(?:@sha256:[0-9a-fA-F]{64})?$")

#: The INSTALL line specifically, ANCHORED at its own line. Two facts pulled it
#: apart from a mere mention of the package: the precondition's probe above it
#: also names `postgresql-client-<major>`, and a comment — its own line OR a
#: trailing one — is inert shell that reads exactly like live code. `_code_only`
#: drops comment-ONLY lines, so the ANCHOR is what makes an inline comment
#: (`echo ok  # apt-get install postgresql-client-16 …`) unable to answer as the
#: install (MEASURED, review of `bfb7af441a`: that shape selected a second step
#: and failed all six assertions with "a second installer" over a step that
#: installs nothing).
#: `apt-get`'s own option tokens may sit BEFORE the verb (`apt-get -y install`,
#: `apt-get -o Dpkg::Use-Pty=0 install`) and an env assignment may prefix the whole
#: command — all behaviourally identical to the shipped line. Requiring `install`
#: to follow the binary immediately red the wrapped spelling AND hid a second
#: install spelled that way (MEASURED, review of `2547274eaa`: five of six red on
#: the re-spelled single install, six green on an appended `-y install … -18`).
_APT_INSTALL = (
    r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*[ \t]+)*(?:sudo[ \t]+(?:-[A-Za-z]+[ \t]+)*)?"
    r"apt(?:-get)?[ \t]+"
    # Option tokens may sit before the verb (`-y`, `-o Dpkg::Use-Pty=0`), but a
    # SHELL OPERATOR may not: without the lookahead, `apt-get install … jq && echo
    # postgresql-client-16` and `apt-get update && echo install
    # postgresql-client-16` both answered as "the install" (MEASURED, review of
    # `48370b8261` — the first would have hidden a real second install's major).
    r"(?:(?!&&|[;|&])[^\s]+[ \t]+)*?install\b"
)
CLIENT_INSTALL_RE = re.compile(
    # The package must be an ARGUMENT of this command, so no shell operator may
    # appear between the verb and the package: `apt-get install -y jq && echo
    # postgresql-client-16` installs `jq` and merely PRINTS the package name, yet
    # it answered as an install of 16 (MEASURED, review of `48370b8261`).
    r"^[ \t]*" + _APT_INSTALL
    + r"(?:(?!&&|[;|&])[^\n])*?\bpostgresql-client-(?P<major>\d+)\b",
    re.MULTILINE,
)

#: The same command ANYWHERE on a line (after `;`, `&&`, …): `CLIENT_INSTALL_RE` is
#: line-anchored, so two installs joined on ONE line counted as one and the
#: "exactly ONE install" fact was false over a step that installs twice
#: (MEASURED, review of `aec7c83e15`).
_ANY_INSTALL_RE = re.compile(
    r"(?:^|[;&|])[ \t]*" + _APT_INSTALL
    + r"(?:(?!&&|[;|&])[^\n])*?\bpostgresql-client-(?P<major>\d+)\b",
    re.MULTILINE,
)

#: Where the INSTALL COMMAND STARTS, for truncating the executed prefix. Separate
#: from `CLIENT_INSTALL_RE` because a continued command puts the package on its
#: SECOND line — the prefix must stop at the command, and the raw lines here are
#: never joined (they are executed verbatim).
_INSTALL_CMD_RE = re.compile(r"^[ \t]*" + _APT_INSTALL, re.MULTILINE)

#: An `exit 1` is only a fail action when it is a STATEMENT — never inside a
#: quoted message, and never inside a heredoc body (that is data). `_statements`
#: blanks quoted spans before the match, so an apostrophe INSIDE a double-quoted
#: message (`echo "the image's client is major $major"; exit 1`) no longer breaks
#: a quote-parity count — MEASURED, review of `f34c4fb741`: that armed branch red
#: "does not `exit 1`", and the step's own live messages are full of `'`
#: (`this image's archive`, `the drill's client`).
_DQUOTED_RE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"')
_SQUOTED_RE = re.compile(r"'[^']*'")
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(?P<tag>\w+)\1")

#: A fail action's `exit 1`, ANCHORED at the start of its STATEMENT — line start,
#: or just after `;` / `&&` / `||` / `{` / `(`. Enumerating what may FOLLOW it
#: instead (`;`, `}`, `)`, end-of-string) red every nested or commented fail
#: action: `exit 1  # fail closed`, an `exit 1` whose branch was indented one
#: level deeper, and the `if true; then … fi` wrapper all failed "does not
#: `exit 1`" over a guard that exits 1 (MEASURED, review of `bfb7af441a`), while
#: the same text passed the EXECUTED arm — the most misleading failure this file
#: can produce.
_EXIT_1_RE = re.compile(r"(?:^|;|&&|\|\||\{|\()[ \t]*exit[ \t]+1\b", re.MULTILINE)


def _statements(line: str) -> str:
    """One shell line with its quoted spans blanked.

    DOUBLE-quoted spans are blanked first: inside `"…"` a `'` is a literal, so
    removing single-quoted spans first would eat the tail of a double-quoted one.
    """
    return _SQUOTED_RE.sub("''", _DQUOTED_RE.sub('""', line))


def _heredoc_tags(line: str) -> list[tuple[str, bool]]:
    """`(tag, indented_allowed)` for every UNQUOTED `<<` opener on this line.

    Three facts this must get right, each paid for by a measured review finding:
    a `<<` inside a quoted string is message text (`echo "see <<EOF in the docs"`);
    a `<<` inside a TRAILING COMMENT is inert shell (`# the body arrives via <<EOF`)
    — so the scan runs over `_strip_comments(line)`; and bash reads one body per
    opener in order, so ALL of them are returned (`cat <<A <<B` left the SECOND
    body live when only the first was tracked).
    """
    tags: list[tuple[str, bool]] = []
    code = _strip_comments(line)
    quote: str | None = None
    index = 0
    while index < len(code):
        char = code[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif code.startswith("<<", index):
            opener = _HEREDOC_RE.match(code, index)
            if opener:
                tags.append((opener.group("tag"), code.startswith("<<-", index)))
                index = opener.end()
                continue
        index += 1
    return tags


def _strip_comments(text: str) -> str:
    """Drop `#`-introduced comments that are OUTSIDE quotes — trailing ones too.

    `_code_only` only drops comment-ONLY lines, and a count taken over it let a
    trailing comment naming a write over-count (MEASURED, review of
    `2547274eaa`: one stray `# … echo /usr/bin >> $GITHUB_PATH` red the step as a
    double write). Quoted spans are PRESERVED here — the write under test is
    spelled `>> "$GITHUB_PATH"`.
    """
    kept: list[str] = []
    for line in text.splitlines():
        quote: str | None = None
        cut = len(line)
        index = 0
        while index < len(line):
            char = line[index]
            if quote:
                if char == "\\" and quote == '"':
                    index += 2
                    continue
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "#" and (index == 0 or line[index - 1] in " \t"):
                cut = index
                break
            index += 1
        kept.append(line[:cut])
    return "\n".join(kept)


def _without_heredocs(text: str) -> str:
    """Drop heredoc bodies and their terminator lines: they are DATA, not shell.

    Every opener on a line is tracked, in order, because bash reads one body per
    opener. The terminator test follows bash: `<<EOF` needs the tag at column 0,
    while `<<-EOF` also accepts tabs before it — recognising an INDENTED tag for
    the `<<` form was wrong in BOTH directions (an indented terminator never ends
    the heredoc in bash, so everything after it is data, and the `::error::` /
    `exit 1` "inside" it was read as live; MEASURED, review of `aec7c83e15`).
    """
    kept: list[str] = []
    pending: list[tuple[str, bool]] = []
    for line in text.splitlines():
        if pending:
            tag, indented_ok = pending[0]
            candidate = line.lstrip("\t") if indented_ok else line
            if candidate == tag:
                pending.pop(0)
            continue
        kept.append(line)
        pending.extend(_heredoc_tags(line))
    return "\n".join(kept)


def _has_exit_1(text: str) -> bool:
    """`_EXIT_1_RE`, over statements only.

    A separator inside a message string, and an `exit 1` inside a heredoc body,
    are both DATA. Without the quote masking, `echo "…mismatch; exit 1"` answered
    as a fail action and the version branch — which no test executes — stayed
    green with no fail action at all (MEASURED, review of `3726d2f400`); without
    the heredoc pass, a `cat <<'EOF'` body answered the same way (MEASURED, review
    of `f34c4fb741`).
    """
    for line in _without_heredocs(text).splitlines():
        if _EXIT_1_RE.search(_statements(line)):
            return True
    return False


#: The `$GITHUB_PATH` write as ONE unit: the versioned dir IS the value written.
#: `echo <dir> >> "$GITHUB_PATH"` — read the major from the
#: write line itself, never from an `export` elsewhere in the step.
#: One spelling of the identifier, shared by BOTH readers below: `$GITHUB_PATH`,
#: `${GITHUB_PATH}`, quoted or not. They disagreed once — the count accepted
#: `${GITHUB_PATH}` and the read did not, so the same expansion red as "never
#: writes" (MEASURED, review of `48370b8261`).
_GITHUB_PATH_REF = r"['\"]?\$\{?GITHUB_PATH\}?['\"]?"

GITHUB_PATH_PREPEND_RE = re.compile(
    r"echo\s+(?P<quote>['\"]?)/usr/lib/postgresql/(?P<major>\d+)/bin(?P=quote)"
    r"\s*(?:>>?|\|[ \t]*(?:sudo[ \t]+)?tee[ \t]+(?:-{1,2}[A-Za-z]+[ \t]+)*)\s*"
    + _GITHUB_PATH_REF
)

#: Any write to `$GITHUB_PATH`. Each line written to it is PREPENDED, so the LAST
#: write decides `PATH` — a second write is therefore the same revert the single-regex
#: read was written to close, and a `search` for the first one cannot see it
#: (MEASURED, review of `f34c4fb741`: `echo …/16/bin >> $GITHUB_PATH` followed by
#: `echo /usr/bin >> $GITHUB_PATH` passed all six).
_GITHUB_PATH_WRITE_RE = re.compile(
    # ANY write: a truncating `>` erases the versioned entry (a harder revert than
    # `>>`), `>>` appends, and `| tee [-a]` is the spelling a script actually uses.
    # MEASURED, review of `48370b8261`: `> "$GITHUB_PATH"` was not counted at all,
    # and previously `| tee -a` was not either.
    r">>{0,2}[ \t]*" + _GITHUB_PATH_REF
    + r"|\|[ \t]*(?:sudo[ \t]+(?:-{1,2}[A-Za-z]+[ \t]+)*)?tee[ \t]+"
    r"(?:-{1,2}[A-Za-z]+[ \t]+)*" + _GITHUB_PATH_REF
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
    """The one step that INSTALLS a PostgreSQL client package.

    Selected by the INSTALL line, read from the step's CODE: a comment (its own
    line OR a trailing one) naming the package is inert shell, and letting it
    select the step both mis-identifies the pin and, when two steps match,
    reports "a second installer" over a step that installs nothing (MEASURED,
    review of `7dc1da573a`: `echo ok  # postgresql-client-16 is pinned below`
    selected a second step and failed all six assertions with that message).
    """
    hits = [
        (index, step)
        for index, step in enumerate(steps)
        if CLIENT_INSTALL_RE.search(_code_only(_run_text(step)))
    ]
    # Exactly ONE `apt-get install postgresql-client-*` in the JOB — counted as
    # LINES, not steps: every major-bearing read is a first match, so a second
    # install in the SAME step is invisible to all of them, and composed with a
    # second `$GITHUB_PATH` write it is the complete silent revert (MEASURED,
    # review of `f34c4fb741`: an appended `… postgresql-client-18` line in the pin
    # step passed all six).
    install_lines = sum(
        len(_ANY_INSTALL_RE.findall(_code_only(_run_text(step)))) for step in steps
    )
    assert install_lines == 1, (
        f"expected exactly ONE `apt-get install postgresql-client-*` in `{JOB}`; "
        f"found {install_lines} — a second installer makes the client whichever "
        "one ran last, which is the class #12051 closed"
    )
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

    ⛔ THE PIN'S DISPOSITION IS PART OF THE FACT (review of `a2be9709d3`).
    Everything below reads the step's TEXT, so a `continue-on-error: true` makes
    the step's `exit 1` unable to red the job and a step-level `if: false` stops
    it running at all — both measured `6 passed` before this assertion existed.
    Read from the parsed YAML, not the text, and test the KEY's presence rather
    than its truthiness: `if: false` parses to `False`.

    ⚠️ STATED LIMIT: the prepend is a text fact, not an executed one. A write
    wrapped in an unreachable branch (`if false; then echo … >> "$GITHUB_PATH";
    fi`) stays green here (MEASURED, review of `a2be9709d3`) — the same limit the
    version branch declares, for the same reason: nothing in this file executes
    the committed step except the precondition prefix.
    """
    steps = _steps(job)
    image_majors = _postgres_image_majors(job)
    assert image_majors, "no pinned Postgres server image found — see the test above"
    server_major = image_majors[0]

    _, pin = _pin_step(steps)
    text = _run_text(pin)

    # The pin may not be SWITCHED OFF. Normalised and read as a VALUE, like the
    # job's own `if:`: `if: ${{ true }}` / `if: always()` are conditions that
    # ALWAYS run the step (strictly safer), so refusing the bare KEY was the same
    # spelling over-refusal this file keeps removing (MEASURED, review of
    # `3726d2f400`). ⚠️ STATED LIMIT: an expression that evaluates false is out of
    # a text guard's reach; the constant spellings are caught here.
    pin_if = re.sub(r"^\s*\$\{\{\s*|\s*\}\}\s*$", "", str(pin.get("if")))
    assert pin_if.strip().lower() not in ("false", "0"), (
        f"the pin step is switched OFF (`if: {pin.get('if')!r}`): the client is "
        "never pinned, and every assertion below would still be green over a "
        "step that never ran"
    )
    assert pin.get("continue-on-error") in (None, False), (
        f"the pin step sets `continue-on-error: {pin.get('continue-on-error')!r}`, "
        "so its `exit 1` cannot red the job — the mismatch would carry on into "
        "the record leg, which is the silent unrestorable artifact #12051 is. "
        "Read as a VALUE, not `is not True`: `${{ … }}` is a legal value for this "
        "key, parses to a string, and `is not True` is true of every string "
        "(MEASURED, review of `6a6367cd48`: `continue-on-error: 'true'` and "
        "`${{ true }}` both stayed green)"
    )
    assert job.get("continue-on-error") in (None, False), (
        f"the job sets `continue-on-error: {job.get('continue-on-error')!r}`, so "
        "no step in it can red the job — the whole drill, including this pin, is "
        "advisory. Same value-vs-identity reason as the step assertion above"
    )
    # A job that cannot run certifies nothing. `#5219`'s aggregate does red a
    # skipped always-run leg, so this is not a silent merge — it is diagnosed one
    # layer later, without the client-major reason. ⚠️ STATED LIMIT: an `if:`
    # EXPRESSION that evaluates false is out of a text guard's reach; the constant
    # spellings are caught here.
    job_if = job.get("if")
    # Normalised, not blacklisted: `${{false}}`, `${{ false}}` and `${{false }}`
    # are the SAME constant and parse to four distinct strings (MEASURED, review
    # of `7dc1da573a`: only the space-padded spelling was caught).
    _job_if_inner = re.sub(
        r"^\s*\$\{\{\s*|\s*\}\}\s*$", "", str(job_if)
    ).strip().lower()
    assert _job_if_inner not in ("false", "0"), (
        f"`{JOB}` is switched OFF (`if: {job_if!r}`) — a skipped job pins nothing, "
        "and the pin is never consulted"
    )

    pkg = CLIENT_INSTALL_RE.search(_code_only(text))
    assert pkg, f"the pin step does not install `postgresql-client-<major>`: {text!r}"
    assert int(pkg.group("major")) == server_major, (
        f"the client pin installs major {pkg.group('major')} but the provisioned "
        f"servers are pg{server_major}. A client NEWER than the target server "
        "dumps a preamble the target cannot execute — `SET transaction_timeout` "
        "from a PG17 client into a PG16 server is #12051"
    )

    # ONE regex over the WHOLE command: the versioned dir must be the value
    # WRITTEN TO `$GITHUB_PATH`. A bare `CLIENT_PATH_RE`/`"GITHUB_PATH" in text`
    # pair is satisfiable by two different lines and by an inert `export`.
    # ONE write to `$GITHUB_PATH`, then read it: each write is PREPENDED, so a
    # second one decides PATH. Counted over code with comments and heredoc bodies
    # removed (a trailing comment naming a write is inert shell).
    writes = _GITHUB_PATH_WRITE_RE.findall(
        _strip_comments(_without_heredocs(_code_only(text)))
    )
    assert len(writes) == 1, (
        f"the pin writes `$GITHUB_PATH` {len(writes)} times — each write is "
        "PREPENDED, so the LAST one decides `PATH` and only this assertion's "
        "matched line is read. A second write (e.g. `/usr/bin`) is the full "
        "revert #12051 removed, with the guard green"
    )
    # The READ is prepared exactly like the COUNT above: `_code_only` alone keeps
    # trailing comments and heredoc bodies, so a real `/usr/bin` write with the
    # versioned dir left in a comment (or in a heredoc body) satisfied this read
    # (MEASURED, review of `aec7c83e15`).
    _prepared = _strip_comments(_without_heredocs(_code_only(text)))
    prepend = GITHUB_PATH_PREPEND_RE.search(_prepared)
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

    branch = _version_branch(_code_only(text), server_major)
    # Read the branch the way the shell would: no comment, no heredoc body. A raw
    # substring test was satisfied by `:  # echo "::error::…"` and by the same line
    # inside a `cat <<EOF` body, over a branch that exits silently — the unlabelled
    # failure this assertion exists to prevent (MEASURED, review of `aec7c83e15`).
    _branch_code = _strip_comments(_without_heredocs(branch))
    assert "::error::" in _branch_code, (
        "the client pin's VERSION BRANCH asserts nothing about the version it "
        "actually got — the branch's own body must `echo \"::error::…\"`, so a "
        "runner image change surfaces as this step, not as an unrestorable "
        "artifact. Read from the BRANCH, not the step: the package precondition "
        "above it carries a `::error::` of its own"
    )
    assert _has_exit_1(branch), (
        "the version branch does not `exit 1`: a named error with no exit status "
        "lets the mismatch carry on into the record leg, which is the silent "
        "unrestorable artifact #12051 exists to prevent. Read at the STATEMENT's "
        "start, so a nested or commented fail action is still the same exit"
    )
    # The comparison is pinned by `_version_branch` itself, which asserts the
    # matched branch's own major EQUALS the servers' — so a spelling-exact search
    # of the raw step (`"$major" != "16"`) is not repeated here: it read text that
    # still carried comments, and it rejected the `${major}` spelling whose
    # expansion is the SAME operand (this file's `_version_branch` accepts it).
    # Redundancy plus false-block surface is the worst of both.
    for tool in ("pg_dump", "pg_restore"):
        # CODE, not `text`: both names appear in this step's own prose comment, so a
        # raw search stays green after the tool is dropped from the `for` loop —
        # MEASURED (review of `7b02823f41`): `for tool in pg_restore; do` plus a
        # comment naming `pg_dump` passed all six tests. STATED LIMIT:
        # `_code_only` drops comment-ONLY lines, so a trailing `# …pg_dump…` on the
        # loop's own line still satisfies this. Stripping inline comments needs
        # shell-quote awareness (every `#` here would have to be classified), which
        # is a new false-red surface on the assertions that share `_code_only`; the
        # shape is a deliberate note, not a removal, so it is named rather than
        # half-guarded.
        assert tool in _code_only(text), (
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
        if "test_9567_record_capstone.py" in _code_only(_run_text(step))
    ]
    assert leg_indexes, "the record capstone (D8) is not in any run step of this job"
    assert pin_index < min(leg_indexes), (
        f"the client pin is at step {pin_index}, the record leg at "
        f"{min(leg_indexes)} — a `$GITHUB_PATH` prepend after the leg is inert, "
        "so the leg would run on the runner image's own client"
    )

#: A (release -> PostgreSQL client major) fact table, PROVEN and not assumed.
#: Which client a release's archive carries IS #12051 round 2: `ubuntu-latest`
#: moved to `ubuntu-26.04`, whose archive ships `postgresql-client-18` and NO 16,
#: so the pin died `E: Unable to locate package` before a single test ran. A new
#: release must be MEASURED and added here before it is adopted — this map is the
#: record of a measurement, not a shape check.
KNOWN_GOOD_IMAGE_MAJOR = {
    # `docker run --rm ubuntu:24.04 apt-cache policy postgresql-client-16`
    #   -> Candidate: 16.15-0ubuntu0.24.04.1
    "ubuntu-24.04": 16,
}

#: A commented-out line is inert shell that reads EXACTLY like live code, so the
#: content assertions below read the step's code. `tests/test_ci_selection.py`
#: refuses a substring pin for the same reason: a substring pin is satisfied by
#: commenting the body out.
_COMMENT_ONLY_RE = re.compile(r"^\s*#")


def _continuation_runs(lines: list[str]) -> list[tuple[int, str]]:
    """`(index of the run's FIRST line, the run's lines joined)` per shell command.

    A line ending in `\\` continues onto the next one, so a command's tokens can be
    split across raw lines and no per-line matcher can see it whole.
    """
    runs: list[tuple[int, str]] = []
    buffer: list[str] = []
    start = 0
    for index, line in enumerate(lines):
        if not buffer:
            start = index
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            # The continuation marker is SYNTAX, not a token: keep it and the
            # joined run would read `apt-get \   install`, which is not the
            # command (MEASURED — that is what made the continued-install case
            # still report "no `apt-get install` line").
            buffer.append(stripped[:-1])
            continue
        buffer.append(line)
        runs.append((start, " ".join(buffer)))
        buffer = []
    if buffer:
        runs.append((start, " ".join(buffer)))
    return runs


def _code_only(text: str) -> str:
    """The step's shell with comment-only lines dropped and continuations joined.

    A line continued with `\\` is the SAME command, so `sudo apt-get install -y \\\n
    --no-install-recommends postgresql-client-16` must read as one install line —
    joining here keeps every matcher spelling-independent instead of teaching each
    one about continuations (MEASURED, review of `3726d2f400`).
    """
    joined = text.replace("\\\n", " ")
    return "\n".join(
        line for line in joined.splitlines() if not _COMMENT_ONLY_RE.match(line)
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
    # The header's own indent is captured and its closer must land there: an `if !`
    # block wrapped and indented one level for a reason (a `-z` guard, a `then`
    # block) is the SAME armed guard, and a column-0 anchor red-ed it (MEASURED,
    # review of `7dc1da573a`) — the false block this comment block promises not
    # to produce.
    r"^(?P<ind>[ \t]*)if\s+!\s*(?:sudo\s+)?apt-cache\s+(?:show|policy)\s+['\"]?"
    r"postgresql-client-(?P<major>\d+)['\"]?[^;]*;\s*then(?P<body>.*?)^(?P=ind)fi[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
_OR_PRECONDITION_RE = re.compile(
    r"(?:sudo\s+)?apt-cache\s+(?:show|policy)\s+['\"]?"
    r"postgresql-client-(?P<major>\d+)['\"]?[^|\n]*\|\|\s*[({][ \t]*\n"
    # The failing branch runs to the FIRST bare `}`/`)` line, never to a
    # line-count window: a 5-line window truncated the shipped body (7 lines deep)
    # and reported "does not `exit 1`" over a group that exits 1 (MEASURED, review
    # of `7dc1da573a`). The closer is LINE-ANCHORED, so the `{…}`/`(…)` characters
    # INSIDE the shipped message still cannot close it early — which is what the
    # window was protecting against. ⚠️ STATED LIMIT: a nested group inside the
    # failing branch, or a message line that is a bare bracket, closes the body
    # early and reds as "does not `exit 1`" over a group that exits 1 (MEASURED,
    # review of `3726d2f400`). Counting nesting needs a shell parser; refusing the
    # shape is the wrong direction (it is armed), so the limit is named instead.
    r"(?P<body>(?:(?!^[ \t]*[})][ \t]*(?:#.*)?$).)*)"
    r"^[ \t]*[})][ \t]*(?:#.*)?$",
    re.DOTALL | re.MULTILINE,
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
#: contains TWO `::error::`/`exit 1` pairs — the package precondition and this
#: branch — so a whole-step search is satisfied by the precondition alone.
#: MEASURED 2026-11-12 (fresh review of `eb8e4d7268`): the assertion this replaces
#: stayed green with `if [ "$major" != "16" ]; then :; fi`. FACTS only: the
#: comparison is on `$major`, against a literal, inside `then … fi`.
_VERSION_BRANCH_RE = re.compile(
    r"^(?P<ind>[ \t]*)if\s+"
    # TWO spellings for the same FACT, because `[ … ]` and the `test …` builtin are
    # the same test and a guard that reds one is a false block (MEASURED, review of
    # `7b02823f41`: narrowing to `[ … ]` alone re-introduced a false block on
    # `if test "$major" != "16"; then` that `origin/main`'s two-pattern tuple did
    # NOT have — a regression against the base, so both are accepted here).
    r"(?:\[\s*\"?\$\{?major\}?\"?\s*!=\s*\"?(?P<bracket_major>\d+)\"?\s*\]"
    r"|test\s+\"?\$\{?major\}?\"?\s*!=\s*\"?(?P<test_major>\d+)\"?)"
    # `; then` and a newline before `then` are the two forms bash ACCEPTS. `] then`
    # (no separator) is a syntax error, so it must NOT match: accepting it would
    # assert an armed branch over one the shell cannot parse.
    r"(?:\s*;\s*then|\n[ \t]*then)"
    # The branch ENDS at the HEADER's own indent (`(?P=ind)`), which is what
    # `origin/main`'s deleted `_closing_fi(indent)` helper did. A nested `if … fi`
    # indented DEEPER than the header therefore cannot end the body early;
    # MEASURED (review of `25b89e4e94`): with a wildcard indent, the nested
    # `if [ -n "${ImageOS:-}" ]` this pin step already uses truncated the body
    # before its `::error::`/`exit 1` and the guard reported a gutted fail-closed
    # arm over one that was present and armed. STATED LIMIT: a nested block
    # written FLAT, at the header's own indent, still ends the body early — bash
    # does not require indentation, and matching braces instead would mean a
    # shell parser (review of `17847279b1` measured that flat shape RED).
    r"(?P<body>.*?)^(?P=ind)fi[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def _version_branch(code: str, server_major: int) -> str:
    """The `if [ "$major" != "<server_major>" ]; then … fi` BODY.

    The fail action is read from this body and never from the step as a whole:
    the package precondition above it also emits `::error::` and exits 1, so a
    whole-step search reports a gutted branch as armed.

    ⚠️ STATED LIMIT: reachability of THIS branch is not provable from text, and
    nothing in this file executes it — the executable arm
    (`test_the_precondition_actually_fires_when_the_package_is_absent`) runs the
    step's PREFIX, which covers the precondition only. A fail action wrapped in
    an `if false` INSIDE this branch therefore stays green here (MEASURED, review
    of `92155a0c5d`). That is why the step also PRINTS `pg_dump --version` for
    each tool: a dead branch is visible in the job log even when no test reds,
    and the version is visible rather than asserted (a `sed` rewritten to a
    constant extracts `16` and nothing below fails on it).
    """
    match = _VERSION_BRANCH_RE.search(code)
    assert match, (
        # The message NAMES the spellings accepted, before it names the
        # consequence: a false block that opens with "the artifact would be
        # unrestorable" over a mere spelling change is what taught the last lane
        # to widen this assertion instead of trusting it (MEASURED, review of
        # `7b02823f41`). The unmatched spellings are MEASURED, not guessed (review
        # of `25b89e4e94`): `[[ … ]]`, a single-quoted literal (`'16'`), reversed
        # operands (`[ "16" != "$major" ]`), a compound condition joined with
        # `&&`, and a one-line `if … then …; fi`. NONE of them occurs in THIS STEP
        # (0 occurrences, measured) — the compound form does occur elsewhere in the
        # workflow, in three `test` job conditions, which is why the count is scoped
        # to the step and not to the file. Widening is therefore a deliberate edit
        # with a measured matrix, not this guard's implicit promise.
        "the pin has no `if [ \"$major\" != \"<major>\" ]; then … fi` block "
        "(`[ … ]` or the `test …` builtin, `$major` or `${major}`, `; then` or a "
        "newline before `then`, closed at the header's own indent) — a branch "
        "that cannot be FOUND is the same symptom as no branch at all: the job "
        "would carry on with a client whose "
        f"major differs from the servers' ({server_major})"
    )
    major = match.group("bracket_major") or match.group("test_major")
    assert int(major) == server_major, (
        "the version branch compares the client's major with "
        f"{major}, but BOTH provisioned servers are "
        f"{server_major} — a branch pinned to any other major can never fire"
    )
    return match.group("body")


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


def test_the_pin_runs_on_a_named_image_that_carries_the_servers_major(job: dict) -> None:
    """The image is part of the artifact: it decides which client the archive has.

    Round 2 of #12051. The pin assumes the image's archive CARRIES
    `postgresql-client-<major>`, and that assumption is not free: MEASURED
    2026-11-12 (job `131022562781`, head `58b5a417ba`), `ubuntu-latest` had
    migrated to `Image: ubuntu-26.04`, whose archive ships `postgresql-client-18`
    and no 16 — so the install died `E: Unable to locate package` (exit 100)
    BEFORE the version assertion, and every leg below skipped.

    A floating label lets the runner image change the drill's client major with
    no diff, which is the class this PR closes. The (release -> major) pair is
    asserted rather than the label's SHAPE, because `ubuntu-26.04` is just as
    version-pinned as `ubuntu-24.04` and is the release that broke.
    """
    server_major = _postgres_image_majors(job)[0]
    labels = _runs_on_labels(job)

    # A `container:` replaces the image the steps run in, so the archive that
    # supplies the client major is the CONTAINER's, not the one this test just
    # checked — and `apt-cache show` below would then fail closed on a container
    # whose archive lacks the major while every assertion here stayed green
    # (MEASURED, review of `6a6367cd48`: `container: ubuntu:26.04` → 6 passed).
    assert "container" not in job, (
        f"`{JOB}` runs in a `container:`, which replaces the archive that supplies "
        "the client major this pin is derived from (#12051 round 2)"
    )

    assert "ubuntu-latest" not in labels, (
        "`ubuntu-latest` is a MOVING label and it moved: on 2026-11-12 it became "
        "ubuntu-26.04, whose archive has no `postgresql-client-16`, so this job "
        "reds before a single test runs (#12051). Name the release."
    )
    assert len(labels) == 1 and labels[0].startswith("ubuntu-"), (
        f"`{JOB}` asks for {labels!r}. This pin needs ONE named hosted image: the "
        "drill's PostgreSQL client major comes from that image's archive, so any "
        "second label (e.g. `self-hosted`) makes the client an inherited fact of "
        "whichever box answers — the exact inheritance #12051 removed. The "
        "in-step precondition fails closed on such a box; this assertion is the "
        "contract."
    )
    label = labels[0]
    assert label in KNOWN_GOOD_IMAGE_MAJOR, (
        f"`{JOB}` runs on {label!r}, whose PostgreSQL client major has not been "
        "measured. PROVE it — `apt-cache policy "
        f"postgresql-client-{server_major}` on that image — and add the pair to "
        "KNOWN_GOOD_IMAGE_MAJOR. The image supplies the drill's client, so "
        "adopting one unmeasured is how #12051 round 2 shipped."
    )
    assert KNOWN_GOOD_IMAGE_MAJOR[label] == server_major, (
        f"`{JOB}`'s two servers are pg{server_major}, but {label} carries a "
        f"pg{KNOWN_GOOD_IMAGE_MAJOR[label]} client: the archive would be dumped "
        "by a client whose major differs from the restore target (#12051)."
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
    assert _has_exit_1(body), (
        "the precondition does not `exit 1`, so a missing client would fall "
        "through to the install and surface as exit 100 again. Read at the "
        "STATEMENT's start, so a nested or commented fail action is still the "
        "same exit"
    )

    # ⛔ ORDER IS PART OF THE FIX. A precondition placed AFTER the install is
    # inert: the install has already died with the opaque `exit 100` this block
    # exists to replace. Both positions come from ANCHORED matches, so a comment
    # that merely says `apt-get install` cannot stand in for the install line and
    # reverse this diagnosis (MEASURED, review of `bfb7af441a`).
    _install_at = CLIENT_INSTALL_RE.search(code)
    assert _install_at, "the pin never runs `apt-get install postgresql-client-<major>`"
    assert code.index("apt-cache") < _install_at.start(), (
        "the package precondition runs AFTER `apt-get install`, so on an image "
        "whose archive lacks the major the job dies with the archive's opaque "
        "`E: Unable to locate package` and never reaches the named error"
    )

    # ⛔ THE INSTALL LINE IS WHAT PICKS THE CLIENT. Asserted on the anchored
    # install line itself, never on a bare package mention: the precondition above
    # names a package too and sits earlier, so a first-match read would satisfy an
    # install-major claim by reading the PROBE (measured: switching the install
    # to -18 stayed green under the old read).
    install = _install_at
    assert int(install.group("major")) == server_major, (
        f"the pin INSTALLS postgresql-client-{install.group('major')} while the "
        f"servers are pg{server_major}: the versioned PATH prepend would then "
        "point at a directory the install never created"
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
    `apt-get` and `apt-cache` stubbed. Hermetic: no network, no apt, no sudo.
    The failing-stub run must STOP with a named `::error::`; the succeeding-stub
    run must NOT stop, or the job would red on every image that carries the
    client.
    """
    _, pin = _pin_step(_steps(job))
    lines = _run_text(pin).splitlines()
    # COMMENT-ONLY lines are masked, never dropped: the indices must stay aligned
    # with `lines`, which is EXECUTED below. A comment that mentions
    # `apt-get install` was taken as the install, so the prefix, truncated at
    # `set -euo pipefail`, reported the precondition missing (MEASURED, review of
    # `7dc1da573a`).
    masked = ["" if _COMMENT_ONLY_RE.match(line) else line for line in lines]
    # Located over CONTINUATION RUNS, not single lines: `sudo apt-get \` +
    # `install -y …` is one command, and a per-line scan found no install and
    # reported "the pin has no `apt-get install` line to run up to" over a step
    # that has one (MEASURED, review of `f34c4fb741`). The run's FIRST raw line is
    # the truncation point, so the whole continued command stays out of the prefix.
    install_at = next(
        (
            start
            for start, joined in _continuation_runs(masked)
            if _INSTALL_CMD_RE.search(joined)
        ),
        None,
    )
    assert install_at is not None, "the pin has no `apt-get install` line to run up to"
    prefix = "\n".join(lines[:install_at]).rstrip()
    # A prefix that still ends in a dangling `\` would swallow the appended marker
    # into the next command, so it is stripped rather than left to eat `echo …`.
    if prefix.endswith("\\"):
        prefix = prefix[:-1].rstrip()
    assert "apt-cache" in prefix, (
        "the precondition is not before the install in the committed step, so "
        "there is nothing to execute ahead of it (the order assertion above "
        "should already have caught this)"
    )

    def run_with(stub: str) -> subprocess.CompletedProcess[str]:
        script = tmp_path / "precondition.sh"
        # ⛔ `sudo` and `apt-get` are stubbed TOO. Without them the prefix runs
        # the HOST's real `sudo apt-cache` / `sudo apt-get update`: on a box where
        # sudo is passwordless and the package is known to apt the probe
        # SUCCEEDS, the failure branch is never taken, and the test reports a
        # disarmed guard that is actually armed — and reads the host's package
        # index while doing it.
        script.write_text(
            'sudo() { "$@"; }\n'
            "apt-get() { return 0; }\n"
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

    present = run_with("apt-cache() { return 0; }")
    assert present.returncode == 0, (
        "with `apt-cache` SUCCEEDING the precondition stopped the step — the job "
        f"would red on every image that carries the client: {present.stderr!r}"
    )
    assert "REACHED_PAST_PRECONDITION" in present.stdout, (
        "the script never reached the line after the precondition even when the "
        "package resolved — the block is unconditional"
    )

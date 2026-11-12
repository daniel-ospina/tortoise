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

#: The fail-closed branch as ONE unit (#12051 review round 4, P1): the `if` line
#: that compares `$major`, its body, and its `fi`. "`::error::` and `exit 1`
#: appear somewhere in the step" is NOT the claim — `if false && [ "$major" !=
#: "16" ]; then` keeps both tokens in the text and can never fire.
FAIL_CLOSED_BRANCH_RE = re.compile(
    r'^[ \t]*if[ \t]+\[[ \t]*"\$major"[ \t]*!=[ \t]*"(?P<major>\d+)"[ \t]*\][ \t]*;'
    r'[ \t]*then[ \t]*\n'
    r'(?P<body>(?:[ \t]*(?!fi\b)[^\n]*\n)*?)'
    r'[ \t]*fi[ \t]*$',
    re.MULTILINE,
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


def _run_code(step: dict) -> str:
    """The step's shell with COMMENT LINES removed (#12051 review P2).

    Every assertion in this guard is a search over the step's TEXT, and a
    commented-out line is inert shell that reads identically to the live one.
    Measured: `# echo "/usr/lib/postgresql/16/bin" >> "$GITHUB_PATH"` (with the
    in-step `export` kept) and a commented-out `if [ "$major" != "16" ] … fi`
    block each left EVERY row green at the time it was measured (four then;
    five since round 5) while the leg went back to resolving the runner image's
    client — i.e. the guard could not distinguish the fix from its tombstone.
    Assertions therefore read this, never the raw body.

    STATED LIMIT: this strips COMMENT LINES only (a line whose first non-space
    character is `#`), and the fail-closed branch is matched structurally by
    `FAIL_CLOSED_BRANCH_RE` for the same reason: presence is not gating. A
    TRAILING comment on a live line is dropped by `_run_commands()` for the two
    searches that read a line as a unit — measured, a trailing `# was: …`
    carrying the versioned write made a live `/usr/bin` line satisfy the
    prepend, and a correct `apt-get` line whose trailing comment named the
    capstone made the pin step look like the leg (`assert 4 < 4`, a false RED).
    """

    return "\n".join(
        line
        for line in _run_text(step).splitlines()
        if not line.lstrip().startswith("#")
    )


def _run_commands(step: dict) -> str:
    """`_run_code()` with trailing comments removed, for line-as-a-unit searches.

    ` #…` (whitespace before the `#`) is the shell's comment start outside
    quotes, which is what both call sites need: they match a whole write/entry
    line, and a trailing comment there is either a tombstone (the value it
    names is NOT what runs) or noise (the capstone's name in the pin step).
    STATED LIMIT: a `#` inside a quoted string is not special-cased — it is
    only reached here when a line quotes a literal `#` AND names one of the two
    paths, which then reds rather than silently passing.
    """

    return "\n".join(
        re.split(r"\s+#", line, maxsplit=1)[0]
        for line in _run_code(step).splitlines()
    )


def _pin_step(steps: list[dict]) -> tuple[int, dict]:
    """The one step that installs a PostgreSQL client package."""
    hits = [
        (index, step)
        for index, step in enumerate(steps)
        if CLIENT_PKG_RE.search(_run_code(step))
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
    text = _run_code(pin)

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
    prepends = list(GITHUB_PATH_PREPEND_RE.finditer(_run_commands(pin)))
    assert prepends, (
        "the pin step never writes a `/usr/lib/postgresql/<major>/bin` entry to "
        "`$GITHUB_PATH`; a prepend that only exists as an `export` inside this "
        "step cannot reach the pytest leg, so the leg would resolve `pg_dump` "
        f"from the runner image instead (step text: {text!r})"
    )
    # ⛔ EVERY write, not just the first (#12051 review round 5). `search()`
    # returns the FIRST match, so a SECOND append naming another major was
    # invisible: measured, `/usr/lib/postgresql/16/bin` followed by
    # `/usr/lib/postgresql/17/bin` left all four rows GREEN while the leg ran on
    # pg17 — the #12051 defect, restored. The runner reads the file in ORDER and
    # does `PrependPath.RemoveAll(x => x == line); PrependPath.Add(line);` per
    # line (actions/runner `FileCommandManager.AddPathFileCommand.
    # ProcessCommand`), so the LAST line is the one that wins on PATH.
    wrong = [m.group("major") for m in prepends if int(m.group("major")) != server_major]
    assert not wrong, (
        f"the pin step writes pg{', pg'.join(wrong)} to `$GITHUB_PATH` alongside "
        f"its pg{server_major} entry — the runner adds each line in file order, "
        "so the LAST one wins on the leg and the versioned binaries lose to "
        "another major (measured, round 5: a second append naming pg17 left all "
        "four rows green; this is how #12051 came back)"
    )


def test_no_step_rewrites_the_client_path_to_another_major(job: dict) -> None:
    """The pin's prepend must be the ONLY postgres bin dir this job puts on PATH.

    `$GITHUB_PATH` is JOB-scoped and last-writer-wins, so a LATER step appending
    `/usr/lib/postgresql/<other>/bin` neutralises the pin completely while every
    other row here stays green — measured, round 5: a step inserted between the
    pin and the leg that appends pg17 left all four rows PASS while `pg_dump` on
    the leg resolved from pg17, i.e. exactly the artifact #12051 shipped. The
    pin step's own write is asserted above; this asserts nobody else undoes it.

    Read through `_run_commands()` for the same reason as every other search
    here: a commented-out line is inert shell that reads identically to the live
    one.
    """
    steps = _steps(job)
    server_major = _postgres_image_majors(job)[0]

    writes = [
        (index, match)
        for index, step in enumerate(steps)
        for match in GITHUB_PATH_PREPEND_RE.finditer(_run_commands(step))
    ]
    assert writes, (
        "no step in this job writes a `/usr/lib/postgresql/<major>/bin` entry to "
        "`$GITHUB_PATH` — see the pin-step assertion above"
    )

    offenders = sorted(
        {f"step {index} (major {match.group('major')})" for index, match in writes
         if int(match.group("major")) != server_major}
    )
    assert not offenders, (
        "a `$GITHUB_PATH` write outside the pin puts a client major other than "
        f"pg{server_major} on the job's PATH: {', '.join(offenders)}. The runner "
        "adds each line in file order, so a later write WINS and the pin becomes "
        "inert — the leg would run on that client instead (#12051)"
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
    comparison literal is therefore pinned to the servers' major.

    STATED LIMIT: this is a hermetic text guard, so it cannot prove the
    version-EXTRACTION arithmetic — a `sed` rewritten to a constant (`s/.*/16/`)
    still passes. The alternative is arming two real client majors in CI, which
    this job cannot do without provisioning a second client version; the
    in-job assertion (`pg_dump --version` printed and checked) is what makes a
    constant extraction visible in the log instead.
    """
    _, pin = _pin_step(_steps(job))
    text = _run_code(pin)
    server_major = _postgres_image_majors(job)[0]

    branch = FAIL_CLOSED_BRANCH_RE.search(text)
    assert branch, (
        "the pin has no ARMED fail-closed branch — it must be one unit, "
        '`if [ "$major" != "<major>" ]; then … fi`, not a substring pair: '
        '`if false && [ "$major" != "16" ]; then` keeps both `::error::` and '
        "`exit 1` in the text while being unable to fire, which is the "
        "disjunction this assertion used to be (measured, #12051 round 4)"
    )
    assert int(branch.group("major")) == server_major, (
        "the fail-closed branch compares against major "
        f"{branch.group('major')} but the provisioned servers are pg{server_major} — "
        "a branch that can never be true is the same symptom as no branch"
    )
    assert "::error::" in branch.group("body") and "exit 1" in branch.group("body"), (
        "the armed branch does not both announce (`::error::`) and stop "
        "(`exit 1`) — a branch that prints and continues ships the bad artifact"
    )
    for tool in ("pg_dump", "pg_restore"):
        assert tool in text, (
            f"the pin must assert `{tool} --version`: the drill resolves BOTH "
            "from PATH, and pinning only one leaves the other on the image's "
            "client"
        )


def test_the_pin_runs_before_the_record_leg(job: dict) -> None:
    """`$GITHUB_PATH` affects SUBSEQUENT steps only — position is part of the fix.

    The leg is located in the step's CODE, for the same reason every other
    assertion here reads `_run_code` (#12051 review round 3): with the raw body
    a commented-out `tests/test_9567_record_capstone.py` entry still counted as
    the leg (a false PASS — the pin's position would be graded against a
    line that never runs), and a stray COMMENT inside the pin step naming the
    capstone made the pin itself look like the leg (`assert 4 < 4`, a false RED
    on a correct workflow).
    """
    steps = _steps(job)
    pin_index, _ = _pin_step(steps)

    leg_indexes = [
        index
        for index, step in enumerate(steps)
        if "test_9567_record_capstone.py" in _run_commands(step)
    ]
    assert leg_indexes, "the record capstone (D8) is not in any run step of this job"
    assert pin_index < min(leg_indexes), (
        f"the client pin is at step {pin_index}, the record leg at "
        f"{min(leg_indexes)} — a `$GITHUB_PATH` prepend after the leg is inert, "
        "so the leg would run on the runner image's own client"
    )

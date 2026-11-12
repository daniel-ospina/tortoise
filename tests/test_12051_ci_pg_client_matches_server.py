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
CLIENT_PATH_RE = re.compile(r"/usr/lib/postgresql/(\d+)/bin")


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
    """`postgresql-client-<major>` AND a `/usr/lib/postgresql/<major>/bin` prepend.

    Both halves are load-bearing: the package supplies the binaries, and the
    prepend is what makes them win over the runner image's own (newer) client
    and over `postgresql-common`'s unversioned wrapper.
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

    paths = {int(p) for p in CLIENT_PATH_RE.findall(text)}
    assert server_major in paths, (
        f"the pin step never puts `/usr/lib/postgresql/{server_major}/bin` on "
        f"PATH (found {sorted(paths)}); the versioned binaries would then lose "
        "to the runner image's own client, and the pin would be decorative"
    )
    assert "GITHUB_PATH" in text, (
        "the PATH prepend must be written to `$GITHUB_PATH` — exporting it in "
        "one step's shell does not reach the pytest step"
    )


def test_the_client_pin_fails_closed_on_a_mismatch(job: dict) -> None:
    """An inherited client must RED the job, not silently produce a bad artifact.

    Without this the pin degrades on a runner-image change: the artifact would
    still be dumped by the newer client, and D8 would red with a message that
    blames the record path (#12051's whole complaint).
    """
    _, pin = _pin_step(_steps(job))
    text = _run_text(pin)
    assert "::error::" in text and "exit 1" in text, (
        "the client pin asserts nothing about the version it actually got — it "
        "must `echo \"::error::…\"` and `exit 1` on a mismatch, so a runner "
        "image change surfaces as this step, not as an unrestorable artifact"
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

"""#1677 — the multi-worker admission gate (`tortoise/worker_topology.py`).

WHAT IS BEING PINNED, AND WHY IT NEEDS A TEST AT ALL
====================================================
`Dockerfile.hosted` passes no `--workers`, so hosted production runs ONE uvicorn
worker. Nothing records that decision: uvicorn resolves its worker count as
`CLI flag -> $WEB_CONCURRENCY -> 1`, so one Fly env var moves the machine to N
workers with no code change and no signal — and at N>=2 this app duplicates its
daemons (GitHub issues + Telegram), splits the OAuth CSRF register per process,
turns the golden signals into per-process numbers, and (on embedded) diverges
into N database copies. Every one of those is SILENT.

So the guard is a fail-closed refusal, and the things that could silently undo it
are pinned here:

1. **The precedence** — the count must be resolved the way uvicorn resolves it,
   or the gate measures a topology nobody runs. Pinned by executing the SHIPPED
   `entrypoint.sh` block (not by grepping it), because a line-scan is defeated by
   `printf`, an unbraced `$VAR`, or a variable in between.
2. **The discriminator** — "am I one of N workers" must come from uvicorn's own
   supervisor parent, not from `$WEB_CONCURRENCY` (which is only the flag's
   default source and can read 1 while the process is worker 3 of 8).
3. **The wiring** — the gate must be the FIRST thing `_lifespan` does. Placed
   later, a refusal would already have opened a DB pool / armed a daemon.
4. **The escape hatches' exact reach** — the acknowledgement lifts the
   unlanded-guards refusal and MUST NOT lift the embedded-divergence one.

DB-free and network-free on purpose (the same class as
`tests/test_boot_regressions.py`, whose bash-execution pattern this file
follows): no app import, no FalkorDB, no Postgres. That keeps it runnable on
every surface that can touch the files it guards.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
from pathlib import Path

import pytest

from tortoise import worker_topology as wt

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "entrypoint.sh"
HOSTED_API = REPO_ROOT / "tortoise" / "hosted_api.py"

_SHARED = {"TORTOISE_DB_URI": "rediss://tortoise.example.cloud:50317/tortoise"}
_SHARED_VIA_CLOUD = {"FALKORDB_CLOUD_URI": "rediss://tortoise.example.cloud:50317/tortoise"}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Resolving the count — uvicorn's precedence, one implementation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, 1),
        # uvicorn's documented default source.
        ({"WEB_CONCURRENCY": "3"}, 3),
        # The entrypoint's measured value is authoritative over the default
        # source, because it already folded in a CLI `--workers`.
        ({"WEB_CONCURRENCY": "3", wt.WORKERS_ENV: "2"}, 2),
        # A malformed value must not be read as a number, and must not be
        # silently read as 1 either — it falls through to the next source.
        ({"WEB_CONCURRENCY": "oops", wt.WORKERS_ENV: "4"}, 4),
        ({"WEB_CONCURRENCY": "oops"}, 1),
        # `0`/negative are not a topology; uvicorn treats `workers > 1` as the
        # supervisor's switch, so both run in-process.
        ({"WEB_CONCURRENCY": "0"}, 1),
        ({"WEB_CONCURRENCY": "-2"}, 1),
        ({"WEB_CONCURRENCY": " 2 "}, 2),
    ],
)
def test_resolve_effective_workers_follows_uvicorns_precedence(environ, expected):
    assert wt.resolve_effective_workers(environ) == expected


# ─────────────────────────────────────────────────────────────────────────────
# 2. "Is the database shared" — the same scheme set the connection layer dials
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["rediss://h:1/db", "redis://h:1/db", "docker://:pw@h:6379/db"])
def test_a_server_uri_reads_as_a_shared_database(value):
    assert wt.db_is_shared({"TORTOISE_DB_URI": value}) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/tmp/embedded/tortoise.db",          # a filesystem path -> embedded
        "~/.tortoise/tortoise.db",
        "http://h/db",                         # unsupported scheme -> not dialled
        "rediss://",                           # scheme but no target
        "://missing-scheme",
    ],
)
def test_a_path_or_an_unsupported_scheme_reads_as_embedded(value):
    assert wt.db_is_shared({"TORTOISE_DB_URI": value}) is False


def test_the_cloud_uri_arm_answers_before_the_entrypoint_promotes_it():
    """`entrypoint.sh` copies FALKORDB_CLOUD_URI into TORTOISE_DB_URI before exec.

    A direct `uvicorn ...` invocation (the bypass the gate exists to catch) never
    runs that promotion, so reading only TORTOISE_DB_URI would call a real
    managed deployment "embedded" and refuse it for the WRONG reason.
    """
    assert wt.db_is_shared(_SHARED_VIA_CLOUD) is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. The policy — a pure function of a measured topology
# ─────────────────────────────────────────────────────────────────────────────


def test_a_single_worker_always_runs_even_embedded_and_unacknowledged():
    """The default topology must never be gated: this is every real deployment."""
    assert wt.refusal_reason(wt.Topology(workers=1, spawned=False,
                                         shared_db=False, acknowledged=False)) is None


def test_an_embedded_database_refuses_n_workers_and_the_ack_must_not_lift_it():
    """Divergent redislite copies are silent CORRUPTION, not a degraded feature.

    The acknowledgement exists to accept *degraded* residuals. Lifting this one
    with it would turn a data-divergence guard into a switch, which is the exact
    fail-open shape the gate exists to remove — so the ack's reach is pinned.
    """
    refused = wt.refusal_reason(wt.Topology(workers=2, spawned=True,
                                            shared_db=False, acknowledged=False))
    assert refused is not None, "N>=2 on an embedded DB must be refused"
    assert "EMBEDDED" in refused
    assert wt.ACK_ENV in refused, "the message must say whether the ack can lift it"
    assert "does not lift" in refused

    still_refused = wt.refusal_reason(wt.Topology(workers=2, spawned=True,
                                                  shared_db=False, acknowledged=True))
    assert still_refused is not None, "the acknowledgement must NOT reach this arm"
    assert still_refused == refused


def test_unlanded_guards_refuse_n_workers_and_the_message_names_every_one():
    """The refusal is a DIAGNOSIS: a lane reading it must not have to re-derive.

    Each unlanded guard is listed with the surface it breaks, so the message is
    the ledger a later lane edits when one lands.
    """
    reason = wt.refusal_reason(wt.Topology(workers=2, spawned=False,
                                           shared_db=True, acknowledged=False))
    assert reason is not None
    for row in wt.UNLANDED_GUARDS:
        assert row in reason, f"the refusal no longer names this unlanded guard: {row!r}"
    assert wt.ACK_ENV in reason, "the refusal must name the switch that accepts the residual"
    assert str(len(wt.UNLANDED_GUARDS)) not in ("",), "sanity"
    # And it must name the count it is refusing, not a generic "N".
    assert "N>=2" in reason


def test_the_acknowledgement_permits_n_workers_on_a_shared_database():
    assert wt.refusal_reason(wt.Topology(workers=2, spawned=False,
                                         shared_db=True, acknowledged=True)) is None
    # The same topology without it is refused — the ack is load-bearing.
    assert wt.refusal_reason(wt.Topology(workers=2, spawned=False,
                                         shared_db=True, acknowledged=False)) is not None


def test_a_spawned_worker_refuses_even_when_the_count_reads_one():
    """`WEB_CONCURRENCY` is NOT an identity signal — this is why the probe exists.

    A worker spawned by uvicorn's supervisor resolves its count from the flag
    default; it can read 1 while being worker 3 of 8. The supervisor parent is
    the observation that cannot be lied to, so it must win over the number.
    """
    reason = wt.refusal_reason(wt.Topology(workers=1, spawned=True,
                                           shared_db=True, acknowledged=False))
    assert reason is not None, "a spawned worker with count=1 must still be gated"
    # …and it must not claim a worker count it never observed. "N>=1" would be a
    # refusal that misdescribes its own evidence.
    assert "N>=1" not in reason
    assert "supervisor parent" in reason


def test_acknowledgement_is_read_through_the_one_truthy_contract():
    """A typo must read OFF — an acknowledgement is a permission, not a presence check."""
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        assert wt.ack_is_set({wt.ACK_ENV: truthy}) is True
    for not_truthy in ("0", "false", "no", "off", "", "   ", "enabled", "y"):
        assert wt.ack_is_set({wt.ACK_ENV: not_truthy}) is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. The discriminator, measured here rather than mocked
# ─────────────────────────────────────────────────────────────────────────────


def test_this_test_process_is_not_a_spawned_worker():
    """A real measurement of the live discriminator.

    If `is_spawned_worker` is ever "simplified" into an env read, this test keeps
    proving that the probe answers for the process it is actually in.
    """
    import multiprocessing

    assert multiprocessing.parent_process() is None
    assert wt.is_spawned_worker() is False


def test_a_permitted_acknowledged_boot_re_lists_every_residual(caplog):
    """The residual must stay VISIBLE, not become folklore.

    An acknowledged multi-worker deployment is a known-risk state. Logging it
    once, at WARNING, on every worker's boot is what keeps it from being
    forgotten between the lane that set it and the operator who inherits it.
    """
    with caplog.at_level(logging.WARNING, logger=wt.__name__):
        topology = wt.assert_multi_worker_admissible(
            {**_SHARED, wt.WORKERS_ENV: "2", wt.ACK_ENV: "1"}, spawned=False)

    assert topology.is_multi_worker is True
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "ACKNOWLEDGED" in messages
    for row in wt.UNLANDED_GUARDS:
        assert row in messages, "the acknowledged boot stopped disclosing a residual"


def test_assert_refuses_by_raising_the_one_typed_error():
    with pytest.raises(wt.MultiWorkerRefused) as excinfo:
        wt.assert_multi_worker_admissible({**_SHARED, wt.WORKERS_ENV: "2"}, spawned=False)
    assert wt.ACK_ENV in str(excinfo.value)


# ─────────────────────────────────────────────────────────────────────────────
# 5. The SHIPPED entrypoint block — executed, not grepped
# ─────────────────────────────────────────────────────────────────────────────


def _worker_block(source: str) -> str:
    """The real measurement block from `entrypoint.sh`, up to (not including) `exec`.

    Sourcing the whole entrypoint would run it, and a line-grep is defeated by
    `printf`, an unbraced `$VAR`, or an intermediate variable — so the block is
    lifted verbatim and executed with a real argv.
    """
    start = source.index("_resolve_requested_workers() {")
    end = source.index('exec "$@"', start)
    assert end > start
    return source[start:end]


def _run_worker_block(argv: tuple[str, ...], environ: dict[str, str]) -> subprocess.CompletedProcess:
    script = (
        "set -euo pipefail\n"
        + _worker_block(ENTRYPOINT.read_text())
        + "\nprintf 'WORKERS=%s\\n' \"$TORTOISE_WEB_WORKERS\"\n"
    )
    env = {"PATH": os.environ.get("PATH", ""), "_IS_SERVER": "1", **environ}
    return subprocess.run(["bash", "-c", script, "entrypoint", *argv],
                          env=env, capture_output=True, text=True, check=False)


def _exported_workers(argv: tuple[str, ...], environ: dict[str, str]) -> int:
    proc = _run_worker_block(argv, environ)
    assert proc.returncode == 0, f"the shipped worker block failed: {proc.stderr}"
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("WORKERS=")]
    assert marker, f"the block exported no count: {proc.stdout!r} {proc.stderr!r}"
    return int(marker[-1].split("=", 1)[1])


@pytest.mark.parametrize(
    ("argv", "environ", "expected"),
    [
        # No flag, no env -> uvicorn's default.
        (("uvicorn", "tortoise.hosted_api:app"), {}, 1),
        # Both flag forms, because a hand-edited CMD usually carries the `=`.
        (("uvicorn", "app:app", "--workers", "2"), {}, 2),
        (("uvicorn", "app:app", "--workers=5"), {}, 5),
        # uvicorn's default source.
        (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "3"}, 3),
        # The CLI wins over the env — uvicorn's documented precedence, and the
        # reason a bare `$WEB_CONCURRENCY` read is NOT the count.
        (("uvicorn", "app:app", "--workers", "4"), {"WEB_CONCURRENCY": "3"}, 4),
        # `--workers` last, with the value immediately after it, is the shape
        # that a naive `grep -o` mis-reads.
        (("uvicorn", "app:app", "--host", "0.0.0.0", "--workers", "2",
          "--timeout-graceful-shutdown", "20"), {}, 2),
        # Garbage/zero are normalised to 1 rather than reinterpreted: the
        # measurement must never DISAGREE with the server that starts.
        (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "oops"}, 1),
        (("uvicorn", "app:app", "--workers", "0"), {}, 1),
        (("uvicorn", "app:app", "--workers", "00"), {}, 1),
        # Whitespace around the count is TOLERATED by uvicorn (`int(' 2 ')` is 2),
        # so the measurement must tolerate it too — otherwise it reports 1 while
        # two workers start, and reports it on the very line an operator reads to
        # decide whether the topology is what they think it is.
        (("uvicorn", "app:app"), {"WEB_CONCURRENCY": " 2 "}, 2),
    ],
)
def test_the_shipped_block_resolves_the_count_exactly_as_uvicorn_will(argv, environ, expected):
    assert _exported_workers(argv, environ) == expected


def test_the_shipped_block_overwrites_a_hand_set_value():
    """`TORTOISE_WEB_WORKERS` is an OUTPUT, and must stay one.

    uvicorn never reads it, so hand-setting it cannot raise the count — the
    entrypoint overwrites it from the argv it is actually about to exec. This is
    what keeps the variable from becoming a way to make the app's refusal say
    something false.
    """
    assert _exported_workers(("uvicorn", "app:app"), {wt.WORKERS_ENV: "9"}) == 1


def test_the_shipped_block_announces_a_multi_worker_topology_at_boot():
    """An ambient WEB_CONCURRENCY must stop being invisible.

    The refusal happens later, in the app; without this line an operator reading
    a machine that boots N workers sees nothing that says so.
    """
    proc = _run_worker_block(("uvicorn", "app:app", "--workers", "2"), {})
    assert proc.returncode == 0, proc.stderr
    assert "uvicorn workers = 2" in proc.stdout
    assert "MULTI-WORKER" in proc.stdout


def test_the_shipped_block_says_nothing_about_multi_worker_at_one():
    proc = _run_worker_block(("uvicorn", "app:app"), {})
    assert proc.returncode == 0, proc.stderr
    assert "uvicorn workers = 1" in proc.stdout
    assert "MULTI-WORKER" not in proc.stdout


# ─────────────────────────────────────────────────────────────────────────────
# 6. The wiring — the gate runs FIRST in `_lifespan`
# ─────────────────────────────────────────────────────────────────────────────


def _lifespan_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse(HOSTED_API.read_text(), filename="hosted_api.py")
    found = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_lifespan"
        ),
        None,
    )
    assert found is not None, (
        "tortoise/hosted_api.py has no `_lifespan` — if it was renamed, relocate "
        "this #1677 pin with it"
    )
    return found


def _first_calls(node: ast.AST) -> list[str]:
    """The names of the calls in the function body, in source order (docstring skipped)."""
    names: list[str] = []
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, ast.stmt):
            continue
        for inner in ast.walk(child):
            if isinstance(inner, ast.Call):
                func = inner.func
                if isinstance(func, ast.Name):
                    names.append(func.id)
                elif isinstance(func, ast.Attribute):
                    names.append(func.attr)
    return names


def test_lifespan_calls_the_gate_before_it_touches_anything():
    """Position is the contract, not presence.

    A gate placed after `_start_liveness` / a DB probe would refuse only AFTER
    the damage the refusal exists to prevent (an armed daemon, an opened pool, a
    second copy of the CSRF register). So the pin is on ORDER.
    """
    calls = _first_calls(_lifespan_fn())
    gate = "assert_multi_worker_admissible"
    assert gate in calls, (
        "hosted_api._lifespan no longer calls the #1677 admission gate — the "
        "multi-worker refusal is unreachable from the app"
    )
    gate_at = calls.index(gate)

    # Everything the lifespan does that must NOT precede the gate. Named beside
    # the reason it is dangerous to touch first.
    must_follow = {
        "_start_liveness": "arms the heartbeat, /healthz listener and watchdog",
        "_probe_sdk_reset": "resets the data-plane probe's SDK binding",
        "install_shared_consent_jti_store": "installs the shared consent register",
        "reset": "clears the boot probes",
    }
    for name, hazard in must_follow.items():
        if name in calls:
            assert calls.index(name) > gate_at, (
                f"`{name}` runs before the #1677 admission gate, and it {hazard} — "
                "a refusal there is already too late"
            )
    assert gate_at < len(calls), "the gate is not the first thing the lifespan does"


def test_the_gate_is_imported_at_module_scope():
    """A function-local import would be the #2922 shadowing footgun."""
    tree = ast.parse(HOSTED_API.read_text(), filename="hosted_api.py")
    module_imports = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "assert_multi_worker_admissible" in module_imports, (
        "the admission gate is no longer a module-scope import in hosted_api; a "
        "function-local import of it is the #2922 UnboundLocalError class"
    )

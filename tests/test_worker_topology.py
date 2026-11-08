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
   `printf`, an unbraced `$VAR`, or a variable in between — AND by asserting the
   shell and the Python resolver return the SAME answer for the same input, since
   the app reads the shell's value first.
2. **The discriminator** — "am I one of N workers" must come from uvicorn's own
   supervisor parent plus the argv-derived count, NOT from `$WEB_CONCURRENCY`
   (only the flag's default source, so it can read 1 while the process is worker
   3 of 8) and NOT from "has a supervisor parent" alone (the `--reload` reloader
   is a supervisor and is always ONE worker). Both branches of the live probe are
   EXECUTED here — one in this process, one in a spawned child — because an
   assertion that only ever sees `False` cannot fail.
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
import multiprocessing
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
_EMBEDDED: dict[str, str] = {}
_RELOAD_ARGV = ["uvicorn", "tortoise.hosted_api:app", "--host", "0.0.0.0", "--reload"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Resolving the count — uvicorn's precedence, two implementations that must agree
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("environ", "argv", "expected"),
    [
        ({}, [], 1),
        # uvicorn's documented default source.
        ({"WEB_CONCURRENCY": "3"}, [], 3),
        # The entrypoint's measured value is authoritative over everything: it
        # already folded the CLI in.
        ({"WEB_CONCURRENCY": "3", wt.WORKERS_ENV: "2"}, ["uvicorn", "a:a", "--workers", "7"], 2),
        # …and a malformed one must not be read as a number, nor silently as 1.
        ({"WEB_CONCURRENCY": "oops", wt.WORKERS_ENV: "4"}, [], 4),
        ({"WEB_CONCURRENCY": "oops"}, [], 1),
        # The CLI beats the default source — uvicorn's documented precedence.
        ({"WEB_CONCURRENCY": "3"}, ["uvicorn", "a:a", "--workers", "2"], 2),
        ({"WEB_CONCURRENCY": "3"}, ["uvicorn", "a:a", "--workers=2"], 2),
        # `int()`'s grammar, because that is what uvicorn applies to the flag and
        # to $WEB_CONCURRENCY. `--workers +2` really does start two children.
        ({}, ["uvicorn", "a:a", "--workers", "+2"], 2),
        ({"WEB_CONCURRENCY": " 2 "}, [], 2),
        # `0`/negative are not a topology; uvicorn treats `workers > 1` as the
        # supervisor's switch, so both run in-process.
        ({"WEB_CONCURRENCY": "0"}, [], 1),
        ({"WEB_CONCURRENCY": "-2"}, [], 1),
        ({}, ["uvicorn", "a:a", "--workers", "-2"], 1),
    ],
)
def test_resolve_effective_workers_follows_uvicorns_precedence(environ, argv, expected):
    assert wt.resolve_effective_workers(environ, argv) == expected


def test_an_absent_workers_flag_is_not_a_request_for_one():
    """`--workers` absent must return None, not 1 — the caller needs the difference."""
    assert wt.workers_from_argv(["uvicorn", "a:a", "--host", "0.0.0.0"]) is None
    # `--workers-dir`-style neighbours must not be mistaken for the flag.
    assert wt.workers_from_argv(["uvicorn", "a:a", "--workers-dir", "3"]) is None
    assert wt.workers_from_argv(["uvicorn", "a:a", "--workers=4"]) == 4


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["uvicorn", "a:a", "--reload"], True),
        (["uvicorn", "a:a", "--reload", "--host", "0.0.0.0"], True),
        # The MODIFIER flags only mean anything alongside `--reload`, so a
        # substring test would report a reload for a run that is not one.
        (["uvicorn", "a:a", "--reload-dir", "src"], False),
        (["uvicorn", "a:a", "--reload-include", "*.py"], False),
        (["uvicorn", "a:a", "--workers", "2"], False),
    ],
)
def test_reload_is_matched_as_an_exact_token(argv, expected):
    assert wt.reload_is_requested(argv) is expected


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


def _topology(workers=1, supervised=False, reloading=False, shared_db=False, acked=False):
    return wt.Topology(workers=workers, supervised=supervised, reloading=reloading,
                       shared_db=shared_db, acknowledged=acked)


def test_a_single_worker_always_runs_even_embedded_and_unacknowledged():
    """Every real deployment is this arm, so it must never be gated."""
    assert wt.refusal_reason(_topology()) is None


def test_a_reload_child_is_one_worker_and_is_not_gated():
    """⛔ The reloader IS a supervisor parent, and uvicorn forbids `--workers` with it.

    Treating "has a supervisor parent" as "is one of N" made
    `uvicorn … --reload` unstartable on an embedded DB with NO escape hatch —
    the acknowledgement deliberately does not lift the embedded arm. The reload
    child is identified from its own argv (which multiprocessing restores from
    the parent under spawn) and exempted.
    """
    reload_child = _topology(workers=1, supervised=True, reloading=True)
    assert reload_child.is_multi_worker is False
    assert wt.refusal_reason(reload_child) is None

    # …and the same shape WITHOUT the reload token is gated, so the exemption is
    # narrow rather than "supervised is ignored".
    assert wt.refusal_reason(_topology(workers=1, supervised=True)) is not None


def test_an_embedded_database_refuses_n_workers_and_the_ack_must_not_lift_it():
    """Divergent redislite copies are silent CORRUPTION, not a degraded feature.

    The acknowledgement exists to accept *degraded* residuals. Lifting this one
    with it would turn a data-divergence guard into a switch, which is the exact
    fail-open shape the gate exists to remove — so the ack's reach is pinned.
    """
    refused = wt.refusal_reason(_topology(workers=2, supervised=True, shared_db=False))
    assert refused is not None, "N>=2 on an embedded DB must be refused"
    assert "EMBEDDED" in refused
    assert wt.ACK_ENV in refused, "the message must say whether the ack can lift it"
    assert "does not lift" in refused

    still_refused = wt.refusal_reason(
        _topology(workers=2, supervised=True, shared_db=False, acked=True))
    assert still_refused is not None, "the acknowledgement must NOT reach this arm"
    assert still_refused == refused


def test_unlanded_guards_refuse_n_workers_and_the_message_names_every_one():
    """The refusal is a DIAGNOSIS: a lane reading it must not have to re-derive.

    Each unlanded guard is listed with the surface it breaks, so the message is
    the ledger a later lane edits when one lands. Every row is asserted, so
    deleting one from `UNLANDED_GUARDS` without landing it also reds the
    message-side pin below.
    """
    reason = wt.refusal_reason(_topology(workers=2, shared_db=True))
    assert reason is not None
    for row in wt.UNLANDED_GUARDS:
        assert row in reason, f"the refusal no longer names this unlanded guard: {row!r}"
    assert wt.ACK_ENV in reason, "the refusal must name the switch that accepts the residual"
    # And it must name the count it is refusing, not a generic "N".
    assert "N>=2 uvicorn workers" in reason


def test_the_acknowledgement_permits_n_workers_on_a_shared_database():
    assert wt.refusal_reason(_topology(workers=2, shared_db=True, acked=True)) is None
    # The same topology without it is refused — the ack is load-bearing.
    assert wt.refusal_reason(_topology(workers=2, shared_db=True)) is not None


def test_a_supervised_child_refuses_even_when_the_count_reads_one():
    """`WEB_CONCURRENCY` is NOT an identity signal — this is why the probe exists.

    `uvicorn.run(workers=N)` from a script carries no `--workers` token and may
    leave `$WEB_CONCURRENCY` unset, so the count reads 1 while N serve. The
    supervisor parent is the observation that cannot be lied to, so it wins.
    """
    reason = wt.refusal_reason(_topology(workers=1, supervised=True, shared_db=True))
    assert reason is not None, "a supervised child with count=1 must still be gated"
    # …and it must not claim a worker count it never observed. "N>=1" would be a
    # refusal that misdescribes its own evidence.
    assert "N>=1" not in reason
    assert "SERVED worker count is not readable" in reason


def test_acknowledgement_is_read_through_the_one_truthy_contract():
    """A typo must read OFF — an acknowledgement is a permission, not a presence check."""
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        assert wt.ack_is_set({wt.ACK_ENV: truthy}) is True
    for not_truthy in ("0", "false", "no", "off", "", "   ", "enabled", "y"):
        assert wt.ack_is_set({wt.ACK_ENV: not_truthy}) is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. The live probe — BOTH branches, one of them in a spawned child
# ─────────────────────────────────────────────────────────────────────────────


def _report_supervisor_parent(queue) -> None:  # module-level: pickleable by spawn
    from tortoise.worker_topology import has_supervisor_parent

    queue.put(has_supervisor_parent())


def test_this_test_process_has_no_supervisor_parent():
    """The False branch, measured on the live process (not injected)."""
    assert multiprocessing.parent_process() is None
    assert wt.has_supervisor_parent() is False


def test_a_spawned_child_has_a_supervisor_parent():
    """The TRUE branch, EXECUTED — the arm the guard's bypass coverage rests on.

    Without this, `has_supervisor_parent()` could be mutated to `return False`
    and every other arm in this file would stay green while the direct-invocation
    bypass re-opened. Spawn (not fork) is used because that is the start method
    uvicorn's supervisor uses, and it is what populates `parent_process()`.
    """
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    child = ctx.Process(target=_report_supervisor_parent, args=(queue,))
    child.start()
    try:
        assert queue.get(timeout=120) is True
    finally:
        child.join(timeout=120)
        if child.is_alive():            # pragma: no cover - a wedged child
            child.terminate()
            child.join(timeout=30)
    assert child.exitcode == 0


def test_the_production_entrypoint_reads_the_live_probe(monkeypatch):
    """`assert_multi_worker_admissible()` with NO arguments is the real call site.

    This is the arm that reds if `observe()` stops consulting the live probe (a
    hardcoded `supervised=False`), which no `Topology(...)`-injecting arm can
    catch.
    """
    monkeypatch.setenv("FALKORDB_CLOUD_URI", _SHARED_VIA_CLOUD["FALKORDB_CLOUD_URI"])
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv(wt.ACK_ENV, raising=False)
    monkeypatch.delenv(wt.WORKERS_ENV, raising=False)
    monkeypatch.delenv(wt.WEB_CONCURRENCY_ENV, raising=False)
    monkeypatch.setattr(wt, "has_supervisor_parent", lambda: True)

    with pytest.raises(wt.MultiWorkerRefused) as excinfo:
        wt.assert_multi_worker_admissible()
    assert wt.ACK_ENV in str(excinfo.value)

    # …and the same call with the probe answering False is permitted, so the
    # raise above is attributable to the probe and not to the shape of the call.
    monkeypatch.setattr(wt, "has_supervisor_parent", lambda: False)
    topology = wt.assert_multi_worker_admissible()
    assert topology.is_multi_worker is False


def test_the_production_entrypoint_permits_a_single_worker_boot(monkeypatch):
    """The unmodified live environment (this test process) must boot clean."""
    for name in (wt.WORKERS_ENV, wt.WEB_CONCURRENCY_ENV, wt.ACK_ENV):
        monkeypatch.delenv(name, raising=False)
    topology = wt.assert_multi_worker_admissible()
    assert topology.is_multi_worker is False
    assert topology.supervised is False


def test_a_permitted_acknowledged_boot_re_lists_every_residual(caplog):
    """The residual must stay VISIBLE, not become folklore.

    An acknowledged multi-worker deployment is a known-risk state. Logging it
    once, at WARNING, on every worker's boot is what keeps it from being
    forgotten between the lane that set it and the operator who inherits it.
    """
    with caplog.at_level(logging.WARNING, logger=wt.__name__):
        topology = wt.assert_multi_worker_admissible(
            {**_SHARED, wt.ACK_ENV: "1"},
            supervised=False, argv=["uvicorn", "a:a", "--workers", "2"])

    assert topology.is_multi_worker is True
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "ACKNOWLEDGED" in messages
    for row in wt.UNLANDED_GUARDS:
        assert row in messages, "the acknowledged boot stopped disclosing a residual"


def test_assert_refuses_by_raising_the_one_typed_error():
    with pytest.raises(wt.MultiWorkerRefused) as excinfo:
        wt.assert_multi_worker_admissible(
            _SHARED, supervised=False, argv=["uvicorn", "a:a", "--workers", "2"])
    assert wt.ACK_ENV in str(excinfo.value)


# ─────────────────────────────────────────────────────────────────────────────
# 5. The SHIPPED entrypoint block — executed, and required to AGREE with Python
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


#: ONE table, executed by BOTH resolvers. The app reads the shell's value first
#: (`TORTOISE_WEB_WORKERS`), so a disagreement means the app believes a topology
#: uvicorn is not running — measured once, it was `--workers +2` -> shell 1 /
#: Python 2 with two children actually serving.
_AGREEMENT_TABLE: tuple[tuple[tuple[str, ...], dict[str, str]], ...] = (
    (("uvicorn", "tortoise.hosted_api:app"), {}),
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "3"}),
    (("uvicorn", "app:app", "--workers", "2"), {}),
    (("uvicorn", "app:app", "--workers=5"), {}),
    (("uvicorn", "app:app", "--workers", "+2"), {}),          # int()'s grammar
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": " 2 "}),      # int()'s grammar
    (("uvicorn", "app:app", "--workers", "4"), {"WEB_CONCURRENCY": "3"}),   # CLI wins
    (("uvicorn", "app:app", "--host", "0.0.0.0", "--workers", "2",
      "--timeout-graceful-shutdown", "20"), {}),
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "oops"}),      # uvicorn would raise
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "1 2"}),       # ditto (interior space)
    (("uvicorn", "app:app", "--workers", "0"), {}),
    (("uvicorn", "app:app", "--workers", "-2"), {}),
    (("uvicorn", "app:app", "--reload"), {}),                   # always one worker
)


@pytest.mark.parametrize(("argv", "environ"), _AGREEMENT_TABLE)
def test_the_shipped_block_resolves_the_count_exactly_as_uvicorn_will(argv, environ):
    # The shell's answer, executed.
    shell = _exported_workers(argv, environ)
    # Python's answer for the SAME input, with the entrypoint's export removed so
    # it must derive the count itself.
    py_env = dict(environ)
    py_env.pop(wt.WORKERS_ENV, None)
    assert wt.resolve_effective_workers(py_env, list(argv)) == shell, (
        "the shipped measurer and the Python resolver disagree on the same input — "
        "and the app reads the shell's value first, so it would believe a topology "
        "uvicorn is not running"
    )


@pytest.mark.parametrize(("argv", "environ"), _AGREEMENT_TABLE)
def test_the_exported_count_is_what_the_app_would_read_back(argv, environ):
    """Round-trip: the exported value, resolved again, is the same count."""
    shell = _exported_workers(argv, environ)
    assert wt.resolve_effective_workers({wt.WORKERS_ENV: str(shell)}, list(argv)) == shell


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


def test_lifespan_calls_the_gate_FIRST():
    """Position is the contract, not presence — and "first" means index 0.

    A gate placed after `_start_liveness` / a DB probe / a daemon arm would refuse
    only AFTER the damage the refusal exists to prevent. `assert gate_at == 0`
    (not `gate_at < len(calls)`, which is tautological) is what makes ANY newly
    inserted call above the gate a red: the `must_follow` allow-list below can
    only name hazards it already knows about.
    """
    calls = _first_calls(_lifespan_fn())
    gate = "assert_multi_worker_admissible"
    assert gate in calls, (
        "hosted_api._lifespan no longer calls the #1677 admission gate — the "
        "multi-worker refusal is unreachable from the app"
    )
    assert calls[0] == gate, (
        f"the #1677 admission gate is no longer the FIRST call in _lifespan "
        f"(found `{calls[0]}` at index 0) — anything it precedes has already run"
    )

    # Named beside the reason it is dangerous to touch first, so a reader who
    # moves the gate gets a message about the hazard rather than just an index.
    must_follow = {
        "_start_liveness": "arms the heartbeat, /healthz listener and watchdog",
        "_probe_sdk_reset": "resets the data-plane probe's SDK binding",
        "install_shared_consent_jti_store": "installs the shared consent register",
        "reset": "clears the boot probes",
    }
    for name, hazard in must_follow.items():
        if name in calls:
            assert calls.index(name) > 0, (
                f"`{name}` runs before the #1677 admission gate, and it {hazard} — "
                "a refusal there is already too late"
            )


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

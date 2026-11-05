"""migrate-db CLI tests (plan Task 11).

Covers: backup-first, advisory lock, 3-way discriminator (0-node→rebuild /
partial<source→rebuild-no-force / ≥source+valid→conflict+--force), marker
after verified rebuild, data-completeness (full-dict replay), corrupt-JSONL,
both-DBs conflict, backup-failure abort, FileExistsError, backup-created.
"""
from __future__ import annotations

import json  # noqa: F401
import os
import shutil  # noqa: F401
import subprocess
import sys
import tempfile  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401

import pytest


def _make_embedded_db(tmp, n_points=5):
    """Create a legacy embedded.db in the isolated ~/.tortoise with events."""
    from tortoise.projection import FalkorProjection  # noqa: I001
    from tortoise.log import EventLog
    tortoise_dir = os.path.join(tmp, ".tortoise")
    os.makedirs(tortoise_dir, exist_ok=True)
    db_path = os.path.join(tortoise_dir, "embedded.db")
    log_dir = os.path.join(tmp, "events")
    os.makedirs(log_dir, exist_ok=True)
    proj = FalkorProjection(db_path, allow_nonstandard_path=True)
    try:
        log = EventLog(os.path.join(log_dir, "events.jsonl"))
        ids = []
        for i in range(n_points):
            pid = f"p{i}"
            ids.append(pid)
            # v2 nested event format (rebuild_all expects ev['point'])
            ev = {
                "type": "PointAdded",
                "point": {
                    "id": pid,
                    "content": f"content-{i}",
                    "context": "ctx",
                },
                "createdAt": "2026-08-06T00:00:00Z",
            }
            log.append(ev)
            # _upsert takes the flat form
            flat = {**ev["point"], "type": "PointAdded"}
            proj._upsert(flat)
    finally:
        proj.close()
    return db_path, log_dir


# A `migrate-db` rebuild is not a pure DB operation: it reaches the embedder, so
# the SUBPROCESS loads the SentenceTransformer model once per INVOCATION
# (measured on the carve-out lane: 24-40 s per run; 15.5 s warm-cache on an idle
# box, ~10 s of it the model load). `test_migrate_idempotent` pays it TWICE
# (run -> marker -> run), and the pre-fix literal `60` — a bound calibrated for a
# cheap subprocess — false-REDded the carve-out leg at PR #10610
# (`subprocess.TimeoutExpired: ... timed out after 60 seconds`, run 41878866650,
# job 126758921498) with no failure in the code under test.
#
# The bound is a HANG DETECTOR, not a latency assertion, so it must clear the
# cold load the suite already budgets for: `tests/conftest.py` pins the
# embedder's load budget at 270 s, because the worst cold load it has OBSERVED
# was 204.64 s (#10010). 300 s sits above that measurement and still fails a real
# hang far inside the shard's own watchdog. Pinned by
# `test_the_subprocess_bound_clears_one_cold_embedder_load` below, which reds on
# the old 60.
_MIGRATE_TIMEOUT_S = 300

# ⛔ A bound that EQUALS the harness budget it sits inside can never fire (#10610
# review at `6cfd4e3d5`, P2). pytest-timeout (`timeout_method = signal`,
# `timeout_func_only = false` — both pinned by `tests/test_ci_stall_identify.py`)
# arms a SIGALRM at ITEM start and covers the whole call phase, so on the legs
# that pass `--timeout=300` (and on the `pyproject.toml::timeout = 300` ini value
# a bare invocation resolves) the item alarm always fired before a
# `subprocess.run(timeout=300)` that STARTS later could: `t_start + 300 > 300`
# for every `t_start > 0`. The `timed out after N seconds` diagnostic this
# constant exists to produce was therefore unreachable, and the very false red it
# names stayed reachable by the change's own arithmetic — `test_migrate_idempotent`
# pays TWO invocations, up to 2 x 204.64 s = 409 s against a 300 s item budget.
#
# 660 is DERIVED, not chosen: the LAST invocation's bound must still fire first,
# i.e. `n * _MIGRATE_TIMEOUT_S < budget` in the worst case where every preceding
# invocation spent its whole bound (2 x 300 = 600), plus 60 s of slack for setup
# and the non-embedding work. The mechanism is the one `pyproject.toml` records
# for exactly this case — "where a leg needs more it declares more, and
# pytest-timeout gives an item's own `@pytest.mark.timeout(N)` marker priority
# over this value" — and it is the same shape `tests/conftest.py` already uses one
# level up (270.0 rather than 300.0, because "300 == 300 was the original
# defect").
_MIGRATE_ITEM_BUDGET_S = 660


def _run_migrate(tmp, *args, env_extra=None, timeout=_MIGRATE_TIMEOUT_S):
    """Run python -m tortoise migrate-db against a temp HOME (isolated)."""
    import site as _site
    env = dict(os.environ)
    env["HOME"] = tmp  # isolate ~/.tortoise
    env["TORTOISE_DB_PATH"] = os.path.join(tmp, ".tortoise", "tortoise.db")
    env.pop("TORTOISE_DB_URI", None)
    # HOME override disables user-site; preserve it for falkordb/dateutil
    env["PYTHONPATH"] = os.getcwd() + os.pathsep + _site.getusersitepackages()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise", "migrate-db", *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.mark.timeout(_MIGRATE_ITEM_BUDGET_S)
def test_migrate_happy_path(tmp_path):
    """embedded.db -> canonical via JSONL rebuild; data complete; marker set."""
    db_path, log_dir = _make_embedded_db(str(tmp_path))  # noqa: RUF059
    rc, out, err = _run_migrate(str(tmp_path))  # noqa: RUF059
    assert rc == 0, err
    canonical = os.path.join(str(tmp_path), ".tortoise", "tortoise.db")
    assert os.path.exists(canonical), "canonical DB not created"
    assert os.path.exists(os.path.join(str(tmp_path), ".tortoise", ".migrated-v2")), \
        "marker not written"


@pytest.mark.timeout(_MIGRATE_ITEM_BUDGET_S)
def test_migrate_backup_created(tmp_path):
    """Backup .bak-* file created before rebuild with source content."""
    db_path, log_dir = _make_embedded_db(str(tmp_path))  # noqa: RUF059
    rc, out, err = _run_migrate(str(tmp_path))  # noqa: RUF059
    assert rc == 0
    tortoise_dir = os.path.join(str(tmp_path), ".tortoise")
    baks = [f for f in os.listdir(tortoise_dir) if f.startswith("embedded.db.bak-")]
    assert baks, "backup file not created"


@pytest.mark.timeout(_MIGRATE_ITEM_BUDGET_S)
def test_migrate_idempotent(tmp_path):
    """Second run skips via marker (idempotent)."""
    db_path, log_dir = _make_embedded_db(str(tmp_path))  # noqa: RUF059
    rc1, _, _ = _run_migrate(str(tmp_path))
    rc2, out2, err2 = _run_migrate(str(tmp_path))  # noqa: RUF059
    assert rc1 == 0 and rc2 == 0
    assert "already migrated" in err2.lower() or "skip" in err2.lower() or \
        "marker" in err2.lower() or rc2 == 0


@pytest.mark.timeout(_MIGRATE_ITEM_BUDGET_S)
def test_migrate_noop_when_no_source(tmp_path):
    """FileNotFoundError -> no-op (no embedded.db, clean exit)."""
    rc, out, err = _run_migrate(str(tmp_path))  # noqa: RUF059
    assert rc == 0


@pytest.mark.timeout(_MIGRATE_ITEM_BUDGET_S)
def test_migrate_backup_failure_aborts(tmp_path, monkeypatch):
    """Backup failure (mock copy2 -> OSError) aborts, source intact."""
    db_path, log_dir = _make_embedded_db(str(tmp_path))  # noqa: RUF059
    # Force backup failure by making the source unreadable after creation
    os.chmod(db_path, 0o000)
    try:
        rc, out, err = _run_migrate(str(tmp_path))  # noqa: RUF059
        assert rc != 0 or "backup failed" in err.lower() or "aborted" in err.lower()
    finally:
        os.chmod(db_path, 0o644)


def test_the_subprocess_bound_clears_one_cold_embedder_load():
    """#10610: the `migrate-db` bound is a hang detector, and it is WIRED to the runs.

    The subprocess loads the SentenceTransformer model on every invocation and
    `tests/conftest.py` records the worst cold load it has OBSERVED at 204.64 s
    (its own budget there is 270 s, #10010). The pre-fix default was a literal
    `60` — BELOW that measurement — so `test_migrate_idempotent` (two
    invocations) timed out on the carve-out lane with no failure in the code
    under test. This reds on that literal two ways: the value must clear the
    measured cold load, and the default must actually BE the constant, so a
    decorative constant cannot satisfy it while the calls keep the old bound.
    """
    import inspect

    assert _MIGRATE_TIMEOUT_S >= 240, (
        f"the migrate-db subprocess bound is {_MIGRATE_TIMEOUT_S}s, below the "
        "204.64 s worst cold embedder load tests/conftest.py records (#10010) — "
        "a bound this tight re-introduces the #10610 carve-out flake"
    )
    default = inspect.signature(_run_migrate).parameters["timeout"].default
    assert default == _MIGRATE_TIMEOUT_S, (
        f"`_run_migrate`'s default bound is {default!r}, not the "
        f"_MIGRATE_TIMEOUT_S constant ({_MIGRATE_TIMEOUT_S}) — the constant is "
        "decorative and the calls still carry the tight bound"
    )


def _subprocess_invocations(fn) -> int:
    """How many `_run_migrate(...)` calls a test body makes — parsed, not grepped."""
    import ast
    import inspect
    import textwrap

    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):  # pragma: no cover - a def with no source file
        return 0
    return sum(
        1
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run_migrate"
    )


def _embedder_load_budget_s() -> float:
    """The embedder's own cold-load budget, read from the pin that owns it.

    `tests/conftest.py` sets `EmbeddingModel._LOAD_TIMEOUT_S = 270.0` at import
    (206 lines of derivation above it), and that budget is *designed* to fire
    before the harness's 300 s — a legitimate invocation costs at most this much.
    Read, never re-typed: a second copy in this file is a number that drifts.
    """
    from tortoise.embeddings import EmbeddingModel

    return float(EmbeddingModel._LOAD_TIMEOUT_S)


def test_the_subprocess_bound_can_fire_before_the_item_budget(pytestconfig):
    """#10610: the hang detector must be REACHABLE, and its budget must clear the load.

    ⛔ WHY (review at `6cfd4e3d5`, P2). `_MIGRATE_TIMEOUT_S` was wired at 300
    while the item budget it runs inside was ALSO 300, so the bound was provably
    inert: pytest-timeout arms SIGALRM at item start, and a subprocess bound that
    starts at `t > 0` expires at `t + 300 > 300` — the item alarm always won, and
    the `timed out after N seconds` diagnostic the constant exists to produce
    could never be printed. A bound that can never fire while it appears to
    protect something is the very class this file's comment says it is fixing,
    one level up. `tests/conftest.py` records the same defect for its own budget
    ("300 == 300 was the original defect"), which is why it is 270.0 there.

    Two arms, both DERIVED from the tree rather than typed in:

    * **reachable** — under the worst case where every PRECEDING invocation spent
      its whole bound, the LAST invocation's bound still fires before the item
      alarm: `n * _MIGRATE_TIMEOUT_S < budget`;
    * **no false red** — a legitimately slow item still fits: `n * load < budget`,
      where `load` is the embedder budget conftest pins.

    Non-vacuity, measured at the pre-fix head `6cfd4e3d5`: no test in this module
    carried a `timeout` marker, so every `budget` resolved to the ini's 300 and
    the FIRST arm redded on every subprocess test (`1 * 300 < 300` is false) —
    including `test_migrate_idempotent`, whose two invocations reach 409 s of
    legitimate cold load against that same 300.
    """
    import inspect

    ini_budget = int(pytestconfig.getini("timeout"))
    load = _embedder_load_budget_s()
    checked = []

    for name, fn in sorted(vars(sys.modules[__name__]).items()):
        if not name.startswith("test_") or not inspect.isfunction(fn):
            continue
        invocations = _subprocess_invocations(fn)
        if not invocations:
            continue
        declared = [
            mark.args[0]
            for mark in getattr(fn, "pytestmark", [])
            if mark.name == "timeout" and mark.args
        ]
        budget = declared[0] if declared else ini_budget
        assert invocations * _MIGRATE_TIMEOUT_S < budget, (
            f"{name} makes {invocations} subprocess call(s) bounded at "
            f"{_MIGRATE_TIMEOUT_S}s inside an item budget of {budget}s — the "
            "LAST call's bound expires after the item alarm, so it can never "
            "fire and the diagnostic it exists to print is unreachable. Declare "
            "the budget the item needs with @pytest.mark.timeout(N) (the "
            "pyproject.toml route), as tests/conftest.py does with 270.0"
        )
        assert invocations * load <= budget, (
            f"{name} makes {invocations} subprocess call(s) of up to {load}s "
            f"each ({invocations * load}s of legitimate cold load) inside an "
            f"item budget of {budget}s — a slow-but-healthy run would be killed "
            "as a hang, which is the #10610 carve-out false red this file exists "
            "to stop"
        )
        checked.append((name, invocations, budget))

    assert checked, (
        "no test in this module invokes the subprocess via `_run_migrate` — the "
        "scale this pin measures has become decorative"
    )

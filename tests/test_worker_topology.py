"""#1677 — the multi-worker admission gate (`tortoise/worker_topology.py`).

WHAT IS BEING PINNED, AND WHY IT NEEDS A TEST AT ALL
====================================================
`Dockerfile.hosted` passes no `--workers`, so hosted production runs ONE uvicorn
worker. Nothing records that decision: uvicorn resolves its worker count as
`CLI flag -> $UVICORN_WORKERS -> $WEB_CONCURRENCY -> 1`, so one Fly env var moves
the machine to N workers with no code change and no signal — and at N>=2 this app
duplicates its daemons (GitHub issues + Telegram), splits the OAuth CSRF register
per process, turns the golden signals into per-process numbers, and (on embedded)
diverges into N database copies. Every one of those is SILENT.

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
import sys
from pathlib import Path

import pytest

from tortoise import worker_topology as wt

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "entrypoint.sh"
HOSTED_API = REPO_ROOT / "tortoise" / "hosted_api.py"

#: Marks the start of the pre-exec admission gate in `entrypoint.sh` — the seam
#: between the count resolvers (section 5) and the refusal (section 6).
_GATE_SENTINEL = "# ── #1677: the admission gate, BEFORE exec"
#: entrypoint.sh's own sentinel on the FIRST line of the worker-count block. The
#: block is several functions now; lifting from a function NAME dropped the
#: helpers defined above it (see `_worker_block`).
_BLOCK_SENTINEL = "# ── #1677: the worker-count measurement, from here to the gate sentinel"

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
        # ⛔ `$UVICORN_WORKERS` is click's VALUE for `--workers`, so it beats
        # `$WEB_CONCURRENCY` and loses only to the CLI. Measured on the pinned
        # 0.52.1 — the named arm below carries the transcript.
        ({wt.UVICORN_WORKERS_ENV: "2"}, [], 2),
        ({wt.UVICORN_WORKERS_ENV: "2", "WEB_CONCURRENCY": "4"}, [], 2),
        ({wt.UVICORN_WORKERS_ENV: "2"}, ["uvicorn", "a:a", "--workers", "1"], 1),
        ({wt.UVICORN_WORKERS_ENV: "0", "WEB_CONCURRENCY": "4"}, [], 1),
        ({wt.UVICORN_WORKERS_ENV: "-2", "WEB_CONCURRENCY": "4"}, [], 1),
        ({wt.UVICORN_WORKERS_ENV: "", "WEB_CONCURRENCY": "4"}, [], 4),
        ({wt.UVICORN_WORKERS_ENV: "+2"}, [], 2),
        # `0`/negative are not a topology; uvicorn treats `workers > 1` as the
        # supervisor's switch, so both run in-process.
        ({"WEB_CONCURRENCY": "0"}, [], 1),
        ({"WEB_CONCURRENCY": "-2"}, [], 1),
        ({}, ["uvicorn", "a:a", "--workers", "-2"], 1),
        # ⛔ A GIVEN `--workers` is FINAL, even non-positive: `$WEB_CONCURRENCY` is
        # the flag's DEFAULT (uvicorn reads it only `if workers is None`), never
        # its fallback — so a non-positive flag does NOT inherit the env var.
        ({"WEB_CONCURRENCY": "4"}, ["uvicorn", "a:a", "--workers", "0"], 1),
        ({"WEB_CONCURRENCY": "4"}, ["uvicorn", "a:a", "--workers", "-2"], 1),
        ({"WEB_CONCURRENCY": "4"}, ["uvicorn", "a:a", "--workers=0"], 1),
        # …while an EMPTY value is not "given" at all (the shell's `-z`), so the
        # env var still answers. The boundary of the rule above.
        ({"WEB_CONCURRENCY": "4"}, ["uvicorn", "a:a", "--workers="], 4),
    ],
)
def test_resolve_effective_workers_follows_uvicorns_precedence(environ, argv, expected):
    assert wt.resolve_effective_workers(environ, argv) == expected


def test_an_explicitly_given_workers_flag_is_final_even_when_non_positive():
    """⛔ `--workers 0` does NOT fall through to `$WEB_CONCURRENCY`, and uvicorn agrees.

    `workers_from_argv` returns None both when the flag is ABSENT and when it is
    present but not a positive int, so a resolver that treats its None as "not
    requested" lets the env var answer for a flag that already overruled it.
    Measured on the pinned uvicorn 0.52.1 with `WEB_CONCURRENCY=4` exported:

        Config(workers=None).workers == 4      # env IS the flag's default
        Config(workers=0).workers    == 1      # `workers or 1`, env NOT read
        Config(workers=-2).workers   == -2     # env NOT read; `> 1` false -> one

    i.e. `--workers 0` / `--workers -2` each serve exactly ONE in-process worker.
    Answering 4 for them (which this resolver did) produced a refusal reading
    "N>=4 uvicorn workers" about a machine that was about to run one.
    """
    for token in ("0", "-2"):
        argv = ["uvicorn", "tortoise.hosted_api:app", "--workers", token]
        assert wt.resolve_effective_workers({"WEB_CONCURRENCY": "4"}, argv) == 1, (
            "a non-positive --workers is FINAL in uvicorn — the env var is only the "
            "flag's default, so it must not answer for a flag that was given"
        )
        # The flag's raw presence is what decides; the count it names is just 1.
        assert wt.workers_from_argv(argv) is None

    # An EMPTY value is not "given" (the shell's `-z` case), so the env answers —
    # the boundary that keeps this fix from swallowing a real fall-through.
    assert wt.resolve_effective_workers(
        {"WEB_CONCURRENCY": "4"}, ["uvicorn", "a:a", "--workers="]) == 4

    # …and a positive flag still beats the env, so this is not "the flag is ignored".
    assert wt.resolve_effective_workers(
        {"WEB_CONCURRENCY": "4"}, ["uvicorn", "a:a", "--workers", "2"]) == 2


def test_uvicorn_workers_envvar_is_the_flags_value_and_is_final():
    """⛔ ``$UVICORN_WORKERS`` is a REAL input for ``--workers``, and it is FINAL.

    This module reads ``$UVICORN_RELOAD`` because click's
    ``auto_envvar_prefix="UVICORN"`` makes it a real input for ``--reload``. The
    same mechanism makes ``UVICORN_WORKERS`` the flag's VALUE whenever the flag
    is not given — and then uvicorn never consults ``$WEB_CONCURRENCY`` at all
    (``config.py:352`` reads it only ``if workers is None``, the *parameter*).
    Ignoring the variable made both resolvers report ``1`` for a machine serving
    N, the wrong-report defect this branch already fixed once for
    ``--workers +2``.

    Measured on the pinned uvicorn 0.52.1 (a probe app, counting served
    children):

        UVICORN_WORKERS=2                      -> parent + TWO children
        UVICORN_WORKERS=2 WEB_CONCURRENCY=4    -> 2 (not 4)
        UVICORN_WORKERS=2 … --workers 1        -> 1 in-process (the CLI wins)
        UVICORN_WORKERS=0 WEB_CONCURRENCY=4    -> 1 in-process (given is final)
        UVICORN_WORKERS='' WEB_CONCURRENCY=4   -> 4 (empty == absent)
        UVICORN_WORKERS='+2'                   -> 2 (``int()``'s grammar)
    """
    argv_none = ["uvicorn", "tortoise.hosted_api:app"]
    assert wt.resolve_effective_workers({wt.UVICORN_WORKERS_ENV: "2"}, argv_none) == 2
    assert wt.resolve_effective_workers(
        {wt.UVICORN_WORKERS_ENV: "2", "WEB_CONCURRENCY": "4"}, argv_none) == 2
    # The CLI still wins: it is the same click parameter.
    assert wt.resolve_effective_workers(
        {wt.UVICORN_WORKERS_ENV: "2"}, [*argv_none, "--workers", "1"]) == 1
    # A GIVEN value is final even when non-positive…
    assert wt.resolve_effective_workers(
        {wt.UVICORN_WORKERS_ENV: "0", "WEB_CONCURRENCY": "4"}, argv_none) == 1
    # …while an EMPTY one is not given at all (click's own ``rv != ""`` rule).
    assert wt.resolve_effective_workers(
        {wt.UVICORN_WORKERS_ENV: "", "WEB_CONCURRENCY": "4"}, argv_none) == 4

    # ⛔ And the SHIPPED shell half must land the SAME number, because the app
    # reads ``TORTOISE_WEB_WORKERS`` first: a shell that skipped this variable
    # would hand the app ``1`` for a machine about to run 2.
    assert _exported_workers(tuple(argv_none), {wt.UVICORN_WORKERS_ENV: "2"}) == 2
    assert _exported_workers(
        tuple(argv_none), {wt.UVICORN_WORKERS_ENV: "2", "WEB_CONCURRENCY": "4"}) == 2
    assert _exported_workers(
        tuple([*argv_none, "--workers", "1"]), {wt.UVICORN_WORKERS_ENV: "2"}) == 1
    assert _exported_workers(
        tuple(argv_none), {wt.UVICORN_WORKERS_ENV: "0", "WEB_CONCURRENCY": "4"}) == 1
    assert _exported_workers(
        tuple(argv_none), {wt.UVICORN_WORKERS_ENV: "", "WEB_CONCURRENCY": "4"}) == 4


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


def test_uvicorn_reload_envvar_is_its_other_input_for_the_reloader():
    """⛔ ``$UVICORN_RELOAD`` starts the reloader with NO reload TOKEN at all.

    click's ``auto_envvar_prefix="UVICORN"`` (``uvicorn/main.py``) makes this
    variable a real input for ``--reload``. Measured on the pinned uvicorn
    0.52.1: ``UVICORN_RELOAD=true python -m uvicorn app:app`` DOES spawn a served
    child, and that child's ``sys.argv`` carries no ``--reload`` — so a
    token-only check read it as a supervised multi-worker and refused it on an
    embedded DB, which is the same unstartable-dev-topology defect as the token
    form. The environment is passed explicitly here so the arm is not
    sensitive to the ambient one.
    """
    assert wt.reload_env_is_set({wt.RELOAD_ENV: "true"}) is True
    # ⛔ ``y`` and ``t`` are NOT typos: click's ``BoolParamType`` accepts them
    # (``bool_states``), and that is the type uvicorn parses this variable with.
    # Reading them here as OFF refused a one-worker reloader on the EMBEDDED arm,
    # which has no escape hatch — measured: ``UVICORN_RELOAD=y`` really does start
    # the reloader ("Started reloader process … using WatchFiles").
    for is_true in ("1", "true", "t", "y", "TRUE", " y ", "Yes", "ON"):
        assert wt.reload_env_is_set({wt.RELOAD_ENV: is_true}) is True, is_true
    for not_truthy in ("0", "false", "off", "n", "f", "", "   ", "enabled", "yess"):
        # A value click REJECTS leaves the child REFUSED (fail-closed).
        assert wt.reload_env_is_set({wt.RELOAD_ENV: not_truthy}) is False, not_truthy
    assert wt.reload_env_is_set({}) is False


def test_the_reload_grammar_is_clicks_own():
    """The transcribed ``_CLICK_TRUE`` cannot drift from click's real vocabulary.

    ``$UVICORN_RELOAD`` is converted by ``BoolParamType``, so the module's frozen
    set must equal the TRUE half of click's own ``bool_states``. If a click bump
    adds or drops a spelling, this arm reds instead of the gate silently
    mis-classifying a reloader — the failure mode that is unstartable and has no
    escape hatch on an embedded DB.
    """
    from click.types import BoolParamType

    true_half = {
        state
        for state in BoolParamType.bool_states
        if BoolParamType().convert(state, None, None)
    }
    assert true_half == wt._CLICK_TRUE
    # …and the widening did NOT leak into the repo's shared contract.
    from tortoise.env_truthy import TRUTHY

    assert frozenset({"1", "true", "yes", "on"}) == TRUTHY


def test_a_reload_spelling_click_accepts_is_not_a_multi_worker_refusal():
    """⛔ The CONSEQUENCE of the flag grammar, not just the predicate.

    ``test_the_reload_grammar_is_clicks_own`` pins the SET, and
    ``test_uvicorn_reload_envvar_is_its_other_input_for_the_reloader`` pins the
    literal, but the round-6 finding was the REFUSAL — reached through
    ``observe()`` -> ``is_multi_worker`` -> ``refusal_reason``. An arm that stops
    at the predicate leaves the wiring free to mis-read it and no test reds, so
    this one asserts the verdict.

    ``y``/``t`` are click's spellings for TRUE and are NOT in
    ``tortoise.env_truthy.TRUTHY``; read through the repo's vocabulary they left
    ``reloading=False``, so the reloader's single served child was refused as a
    supervised multi-worker. On an EMBEDDED database that is unstartable with no
    escape hatch at all — the acknowledgement deliberately does not lift the
    embedded arm — which is why this is the arm that must red on the pre-fix
    code (measured: it does, ``Topology(... reloading=False)``).

    Measured on the pinned uvicorn 0.52.1: ``UVICORN_RELOAD=y`` prints
    "Started reloader process" and serves exactly ONE child.
    """
    argv_without_token = ["uvicorn", "tortoise.hosted_api:app"]
    for spelling in ("y", "Y", "t", "T"):
        # EMBEDDED (`{}`): the arm no acknowledgement can lift.
        topology = wt.observe({wt.RELOAD_ENV: spelling}, supervised=True,
                              argv=argv_without_token)
        assert topology.reloading is True, spelling
        assert topology.is_multi_worker is False, spelling
        assert wt.refusal_reason(topology) is None, spelling

    # …and the exemption stays NARROW: the same child with no reload input, or
    # with a value click rejects, is still gated (fail-closed).
    for not_a_reload in ({}, {wt.RELOAD_ENV: "enabled"}, {wt.RELOAD_ENV: "n"}):
        assert wt.refusal_reason(wt.observe(
            not_a_reload, supervised=True, argv=argv_without_token)) is not None, not_a_reload


def test_a_supervised_child_asked_for_reload_by_env_is_one_worker():
    """The env route and the token route must reach the SAME verdict."""
    argv_without_token = ["uvicorn", "tortoise.hosted_api:app"]
    by_env = wt.observe({wt.RELOAD_ENV: "1"}, supervised=True, argv=argv_without_token)
    by_token = wt.observe({}, supervised=True, argv=[*argv_without_token, "--reload"])

    for topology in (by_env, by_token):
        assert topology.reloading is True
        assert topology.is_multi_worker is False
        # …and on an EMBEDDED database, which is the shape that had no escape.
        assert wt.refusal_reason(topology) is None

    # The SAME child with neither input is still gated: the exemption is narrow.
    assert wt.refusal_reason(
        wt.observe({}, supervised=True, argv=argv_without_token)) is not None


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


def test_a_cloud_uri_alone_is_an_embedded_boot_because_the_app_never_reads_it():
    """⛔ ``FALKORDB_CLOUD_URI`` belongs to the BOOT SCRIPT, not to the app.

    ``entrypoint.sh`` promotes it (``export TORTOISE_DB_URI="${FALKORDB_CLOUD_URI}"``)
    BEFORE ``exec``, so a boot that came through the script always has
    ``TORTOISE_DB_URI`` set and needs no fallback here. A DIRECT ``uvicorn``
    invocation — the bypass this gate exists to catch — never runs that
    promotion, and nothing under ``tortoise/`` reads ``FALKORDB_CLOUD_URI``:
    ``hosted_api._make_sdk`` dispatches on ``TORTOISE_DB_URI`` alone and
    otherwise opens ``_resolve_embedded_db_path()``.

    So the arm that used to answer SHARED here answered it for a boot that is in
    fact EMBEDDED — the one direction that hands ``TORTOISE_MULTI_WORKER`` the
    embedded refusal it is documented never to lift (N workers on N divergent
    copies of one file). Re-pinned to the verdict the APP produces.
    """
    assert wt.db_is_shared(_SHARED_VIA_CLOUD) is False, (
        "the app never consults FALKORDB_CLOUD_URI, so this boot IS embedded — "
        "answering 'shared' here is the fail-open the gate exists to prevent"
    )
    # The promotion is what makes it shared — and the promotion is VISIBLE to the
    # app, because it writes TORTOISE_DB_URI.
    assert wt.db_is_shared(_SHARED) is True
    assert wt.db_is_shared({**_SHARED_VIA_CLOUD, **_SHARED}) is True


def test_an_explicit_embedded_uri_masks_a_stale_cloud_uri():
    """⛔ The boot path is a PRECEDENCE chain, not a disjunction over both vars.

    `entrypoint.sh:444-447` keeps an explicit `TORTOISE_DB_URI` WHATEVER its
    shape, and promotes `FALKORDB_CLOUD_URI` only when that variable is empty —
    so an explicit `/data/tortoise.db` opens an EMBEDDED file even while a
    leftover `FALKORDB_CLOUD_URI` is still in the environment (a
    promoted-then-overridden Fly secret looks exactly like this). Answering the
    two variables as an `or` reported "shared" for that state, which handed
    `TORTOISE_MULTI_WORKER` the one refusal it must never lift.
    """
    stale_cloud = _SHARED_VIA_CLOUD["FALKORDB_CLOUD_URI"]
    both_set = {
        "TORTOISE_DB_URI": "/data/embedded/tortoise.db",
        "FALKORDB_CLOUD_URI": stale_cloud,
    }
    assert wt.db_is_shared(both_set) is False, (
        "an explicit non-URI TORTOISE_DB_URI keeps the app on embedded mode; the "
        "cloud URI is only a FALLBACK and must not answer over it"
    )

    # The fallback is GONE, not narrowed: with ``TORTOISE_DB_URI`` unset or
    # empty the app opens the embedded file, so a stale cloud secret must not
    # upgrade that boot to "shared" (that was the fail-open).
    assert wt.db_is_shared({"TORTOISE_DB_URI": "", "FALKORDB_CLOUD_URI": stale_cloud}) is False
    assert wt.db_is_shared({"FALKORDB_CLOUD_URI": stale_cloud}) is False

    # The emptiness test is the shell's `-n` (null == the empty string), NOT a
    # strip: `TORTOISE_DB_URI=" "` is non-null to `-n`, so the boot path keeps
    # it and opens an embedded file.
    assert wt.db_is_shared({"TORTOISE_DB_URI": " ",
                            "FALKORDB_CLOUD_URI": stale_cloud}) is False

    # The explicit source still wins when it IS a server URI.
    assert wt.db_is_shared({**_SHARED, "FALKORDB_CLOUD_URI": "rediss://other:1/db"}) is True


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
    """⛔ The reloader IS a supervisor parent, and uvicorn runs it as ONE worker.

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


def test_the_reload_arm_wins_over_a_named_worker_count():
    """⛔ `--reload --workers 4` serves exactly ONE worker, and uvicorn says so.

    Measured on the pinned uvicorn 0.52.1: the command prints
    `WARNING: "workers" flag is ignored when reloading is enabled`, starts the
    reloader, and starts exactly one served child. `main.py` branches on
    `should_reload` BEFORE `workers > 1` — so the reload arm must be checked
    first here too. With the count checked first, this topology was refused with
    "Refusing to run N>=4 uvicorn workers … on an EMBEDDED database", which
    describes a topology that is not running — the same false-report defect that
    made the reload child unstartable in the first place.
    """
    both = wt.observe(_EMBEDDED, supervised=True,
                      argv=["uvicorn", "a:a", "--reload", "--workers", "4"])
    assert both.workers == 4, "the count is still measured — it is just not the verdict"
    assert both.reloading is True
    assert both.is_multi_worker is False
    assert wt.refusal_reason(both) is None

    # The same count WITHOUT reload is gated, so this is not "the count is ignored".
    assert wt.refusal_reason(
        wt.observe(_EMBEDDED, supervised=False,
                   argv=["uvicorn", "a:a", "--workers", "4"])) is not None


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


def test_a_stale_cloud_uri_cannot_hand_the_ack_the_embedded_arm():
    """The measured path, end to end: precedence decides, then the policy refuses.

    Same inputs as the `db_is_shared` precedence arm, but driven through the
    PRODUCTION `observe()`/`refusal_reason()` pair, so a regression that fixes
    only the helper (or only the policy) still reds here.
    """
    measured = wt.observe(
        {
            "TORTOISE_DB_URI": "/data/embedded/tortoise.db",
            "FALKORDB_CLOUD_URI": _SHARED_VIA_CLOUD["FALKORDB_CLOUD_URI"],
            wt.ACK_ENV: "1",
            wt.WEB_CONCURRENCY_ENV: "4",
        },
        supervised=False,
        argv=["uvicorn", "tortoise.hosted_api:app"],
    )
    assert measured.workers == 4
    assert measured.acknowledged is True
    assert measured.shared_db is False, (
        "a stale cloud URI must not reclassify an explicitly EMBEDDED run as shared"
    )
    assert measured.is_multi_worker is True

    reason = wt.refusal_reason(measured)
    assert reason is not None, (
        "N>=4 on the embedded DB the boot script actually selects must be refused"
    )
    assert "EMBEDDED" in reason
    assert "does not lift" in reason


def test_a_cloud_uri_alone_cannot_hand_the_ack_the_embedded_arm():
    """The cloud-only shape, end to end — the fail-open this round removes.

    Driven through the PRODUCTION ``observe()``/``refusal_reason()`` pair, so a
    regression that only fixes the helper (or only the policy) still reds.
    """
    measured = wt.observe(
        {
            "FALKORDB_CLOUD_URI": _SHARED_VIA_CLOUD["FALKORDB_CLOUD_URI"],
            wt.ACK_ENV: "1",
            wt.WEB_CONCURRENCY_ENV: "4",
        },
        supervised=False,
        argv=["uvicorn", "tortoise.hosted_api:app"],
    )
    assert measured.shared_db is False, (
        "a cloud URI the app never reads must not reclassify an embedded boot"
    )
    assert measured.acknowledged is True
    reason = wt.refusal_reason(measured)
    assert reason is not None, (
        "the acknowledgement must not be able to run N workers on the embedded "
        "DB this boot actually opens"
    )
    assert "EMBEDDED" in reason
    assert "does not lift" in reason


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
    monkeypatch.setenv("TORTOISE_DB_URI", _SHARED["TORTOISE_DB_URI"])
    for _name in (wt.ACK_ENV, wt.WORKERS_ENV, wt.WEB_CONCURRENCY_ENV,
                  wt.UVICORN_WORKERS_ENV, wt.RELOAD_ENV):
        # ⛔ EVERY input `observe()` reads, not just the three this arm started
        # with: `$UVICORN_WORKERS` sits between the CLI flag and
        # `$WEB_CONCURRENCY`, so a box (or CI leg) that exports it reddened this
        # arm before the gate ever looked at the probe — MEASURED: same command
        # with `UVICORN_WORKERS=2` exported → 2 failed, without it → 2 passed.
        # A false RED is indistinguishable from a real regression in the gate.
        monkeypatch.delenv(_name, raising=False)
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
    for name in (wt.WORKERS_ENV, wt.WEB_CONCURRENCY_ENV, wt.ACK_ENV,
                 wt.UVICORN_WORKERS_ENV, wt.RELOAD_ENV):
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

    ⛔ Ends at the GATE sentinel, not at `exec`: the pre-exec gate spawns an
    interpreter and can legitimately refuse, and these arms are about the COUNT
    resolvers agreeing. :func:`_run_gate_block` executes the gate.

    ⛔ STARTS at a sentinel too, not at a function name: the block's helpers are
    several now, and pinning the lift to ONE of them silently drops the others —
    that refactor made the shell arm answer 1 for every input (an undefined
    helper inside `$( )` yields an empty string) while every Python arm stayed
    green, and only the agreement rows noticed.
    """
    start = source.index(_BLOCK_SENTINEL)
    end = source.index(_GATE_SENTINEL, start)
    assert end > start
    block = source[start:end]
    # The lifted text must DEFINE everything the entrypoint's own calls reach;
    # a helper left above the sentinel is the failure this asserts against.
    for helper in ("_trim_edges", "_uvicorn_flag_option",
                   "_resolve_raw_requested_workers",
                   "_resolve_requested_workers",
                   "_count_is_verifiable", "_app_env_file_in_play",
                   "_argv_is_shell_wrapped", "_wrapped_argv_disables_lifespan",
                   "_app_lifespan_disabled"):
        assert f"{helper}() {{" in block, (
            f"`{helper}` is defined OUTSIDE the lifted entrypoint block — move it "
            f"below the `{_BLOCK_SENTINEL}` sentinel, or the executed shell arm "
            "silently loses it"
        )
    return block


def _locale_env(locale: str | None) -> dict[str, str]:
    """Both locale variables, or nothing.

    ⛔ The helpers build the child's environment FROM SCRATCH, so without this
    every shipped-block arm runs in the **C** locale — and the collating-range
    defect (`[0-9]` is a RANGE, not a byte class) is invisible to all of them.
    Both variables are set because the shipped pin is `local LC_ALL=C; export
    LC_ALL`, whose whole purpose is the `LANG`-only shape (a developer shell and
    many container images set no `LC_ALL` at all).
    """
    return {} if locale is None else {"LC_ALL": locale, "LANG": locale}


def _run_worker_block(argv: tuple[str, ...], environ: dict[str, str],
                      locale: str | None = None) -> subprocess.CompletedProcess:
    script = (
        "set -euo pipefail\n"
        + _worker_block(ENTRYPOINT.read_text())
        + "\nprintf 'WORKERS=%s\\n' \"$TORTOISE_WEB_WORKERS\"\n"
    )
    env = {"PATH": os.environ.get("PATH", ""), "_IS_SERVER": "1",
           **_locale_env(locale), **environ}
    return subprocess.run(["bash", "-c", script, "entrypoint", *argv],
                          env=env, capture_output=True, text=True, check=False)


def _exported_workers(argv: tuple[str, ...], environ: dict[str, str]) -> int:
    proc = _run_worker_block(argv, environ)
    assert proc.returncode == 0, f"the shipped worker block failed: {proc.stderr}"
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("WORKERS=")]
    assert marker, f"the block exported no count: {proc.stdout!r} {proc.stderr!r}"
    return int(marker[-1].split("=", 1)[1])


def _gate_tail(source: str) -> str:
    """Resolution + banner + the #1677 pre-exec gate, i.e. everything before `exec`.

    The real composition, so the gate is tested with the count THIS script
    exported rather than an injected one.

    ⛔ Starts at the BLOCK sentinel, the same one `_worker_block` uses: the tail
    calls the block's helpers, and lifting from a function name left them behind
    — `command not found`, an empty count, and a gate that silently permitted
    every boot.
    """
    start = source.index(_BLOCK_SENTINEL)
    end = source.index('exec "$@"', start)
    assert end > start
    return source[start:end]


def _run_gate_block(argv: tuple[str, ...], environ: dict[str, str],
                    locale: str | None = None) -> subprocess.CompletedProcess:
    """Run the SHIPPED resolution+gate tail in a real shell, with a real argv.

    `python` is pinned to THIS interpreter's directory so the gate cannot resolve
    a different one, and `PYTHONPATH` is the repo root so the import is the tree
    under test.
    """
    script = "set -euo pipefail\n" + _gate_tail(ENTRYPOINT.read_text())
    env = {
        "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
        "PYTHONPATH": str(REPO_ROOT),
        "_IS_SERVER": "1",
        **_locale_env(locale),
        **environ,
    }
    return subprocess.run(["bash", "-c", script, "entrypoint", *argv],
                          env=env, cwd=str(REPO_ROOT),
                          capture_output=True, text=True, check=False)


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
    # ⛔ `--reload` never changes the COUNT — it changes the VERDICT
    # (`is_multi_worker` checks the reload arm first). Pinned at a count above 1
    # so both arms must keep reporting the requested 4 while the banner reports
    # the ONE child uvicorn actually serves; the banner needs its own reload line
    # precisely because this number is not the topology.
    (("uvicorn", "app:app", "--reload", "--workers", "4"), {}),
    (("uvicorn", "app:app", "--reload", "--workers", "4"), {"WEB_CONCURRENCY": "3"}),
    # ⛔ The flag is FINAL even when non-positive: `$WEB_CONCURRENCY` is its
    # DEFAULT, not its fallback (uvicorn `config.py:352` reads it only
    # `if workers is None`). Without these rows the two resolvers disagreed here
    # — shell 1, Python 4 — and the app reads the shell's value first.
    (("uvicorn", "app:app", "--workers", "0"), {"WEB_CONCURRENCY": "4"}),
    (("uvicorn", "app:app", "--workers", "-2"), {"WEB_CONCURRENCY": "4"}),
    (("uvicorn", "app:app", "--workers=0"), {"WEB_CONCURRENCY": "4"}),
    # …and an EMPTY value is not "given", so the env var answers (the `-z` case).
    (("uvicorn", "app:app", "--workers="), {"WEB_CONCURRENCY": "4"}),
    # ⛔ `$UVICORN_WORKERS` is click's VALUE for the flag: it sits BETWEEN the CLI
    # and `$WEB_CONCURRENCY`, and is FINAL over the latter (measured on the pinned
    # 0.52.1 — 2 children, not 4). Without these rows the shipped block skipped
    # the variable and exported 1 for a machine running 2.
    (("uvicorn", "app:app"), {"UVICORN_WORKERS": "2"}),
    (("uvicorn", "app:app"), {"UVICORN_WORKERS": "2", "WEB_CONCURRENCY": "4"}),
    (("uvicorn", "app:app", "--workers", "1"), {"UVICORN_WORKERS": "2"}),
    (("uvicorn", "app:app"), {"UVICORN_WORKERS": "0", "WEB_CONCURRENCY": "4"}),
    (("uvicorn", "app:app"), {"UVICORN_WORKERS": "", "WEB_CONCURRENCY": "4"}),
    (("uvicorn", "app:app"), {"UVICORN_WORKERS": "+2"}),
    # ⛔ ``int()`` accepts PEP 515 separators BETWEEN digits, so ``1_0`` starts TEN
    # workers. The shipped block was digit-only and exported 1 — and the app reads
    # the block's value FIRST, so the boot line lied about the topology: the same
    # class ``--workers +2`` and ``WEB_CONCURRENCY=' 2 '`` had. BOTH arms now read
    # the separator rule the way ``int()`` does (ten, not one). The one remaining
    # divergence is non-ASCII decimal digits, which bash arithmetic cannot
    # evaluate — that stays under-report, and a real N>1 still runs under the
    # supervisor, which ``has_supervisor_parent()`` refuses whatever the count
    # reads.
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "1_0"}),          # 10, both
    (("uvicorn", "app:app", "--workers", "1_0"), {}),            # 10, both
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "\u0661\u0662"}),  # Unicode: 1, both
    # …and the placements ``int()`` RAISES on must agree too (both 1): a leading,
    # trailing or doubled separator is not a number, and reinterpreting it as one
    # would report a topology uvicorn never starts.
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "_10"}),
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "10_"}),
    # ⛔ LEADING whitespace containing a NEWLINE. `sed -e 's/^[[:space:]]*//'` is
    # line-oriented: it splits the value into records, never removes the leading
    # run, the digit test then fails and the shell reported 1 while Python
    # (`str.strip`) and uvicorn (`int`) read N — measured `WEB_CONCURRENCY=$'\n2'`
    # => shell 1 / Python 2, and the pre-exec gate read the script's own 1 and
    # PERMITTED a two-worker boot. The trim is now `_trim_edges` (bash pattern
    # expansion, line-agnostic); these rows keep the class pinned.
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "\n2"}),
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "\r\n2"}),
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "\t\n2"}),
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "\n1_0"}),
    (("uvicorn", "app:app", "--workers", "\n2"), {}),
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "1__0"}),
    (("uvicorn", "app:app"), {"WEB_CONCURRENCY": "1_0_"}),
    # ⛔ A REPEATED flag whose LAST occurrence carries no value: the parser
    # boundary, not the value grammar. click exits 2 on this argv ("Option
    # '--workers' requires an argument"), so no topology runs — but the two arms
    # still must agree, and they did not: the shell overwrites with the empty
    # value (falling through to `$WEB_CONCURRENCY`) while the Python arm kept the
    # EARLIER `2`. Caught by the reviewer, pinned here.
    (("uvicorn", "app:app", "--workers", "2", "--workers"), {}),
    (("uvicorn", "app:app", "--workers", "2", "--workers"),
     {"WEB_CONCURRENCY": "4"}),
    # …and the same divergence through the VALUE, not a trailing flag: the shell
    # consumes `--workers=` as DATA (it never re-scans the token it just read),
    # while the Python arm used to re-scan it as the flag form and clear the
    # value. `_workers_flag_value` now consumes the value token and steps past
    # it, exactly like the shell loop, so both read this as ONE malformed value.
    (("uvicorn", "app:app", "--workers", "--workers="), {}),
    (("uvicorn", "app:app", "--workers", "--workers="),
     {"WEB_CONCURRENCY": "3"}),
    # ⛔ ARITY ON THE COUNT READER — the `--reload` lesson applied to `--workers`.
    # `--host --workers=1` makes `--workers=1` the VALUE of `--host`: click never
    # sees a workers flag, so uvicorn reads `$WEB_CONCURRENCY` and runs FOUR
    # (measured on the pinned 0.52.1) while a membership scan reported 1 — and a
    # masked count is what certified "one worker, nothing to gate" on a
    # lifespan-off boot. Every string-typed long option reaches it.
    (("uvicorn", "app:app", "--host", "--workers=1"),
     {"WEB_CONCURRENCY": "4"}),
    (("uvicorn", "app:app", "--app-dir", "--workers=1"),
     {"WEB_CONCURRENCY": "4"}),
    (("uvicorn", "app:app", "--root-path", "--workers=1"),
     {"WEB_CONCURRENCY": "4"}),
    (("uvicorn", "app:app", "--uds", "--workers=1"),
     {"WEB_CONCURRENCY": "4"}),
    # …and the control: the value given BEFORE the flag is still a real flag.
    (("uvicorn", "app:app", "--host", "0.0.0.0", "--workers=2"), {}),
    # …and the SPACE form of the same shape, which the `--app-dir --workers=1`
    # row above does not reach: `--app-dir --workers 2` makes `--workers` the
    # app_dir VALUE and leaves `2` a positional, so uvicorn runs ONE worker while
    # a membership scan reads 2 — and the pre-exec gate then refuses a
    # one-worker boot (over-block, unescapable on the embedded arm).
    (("uvicorn", "app:app", "--app-dir", "--workers", "2"), {}),
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


def test_the_shipped_block_reports_one_worker_while_reloading():
    """`--reload --workers 4` serves ONE child; the banner must say so.

    uvicorn's own words on the pinned 0.52.1: `"workers" flag is ignored when
    reloading is enabled`, and `grep -c "Started server process"` = 1. Reporting
    the requested 4 was the fourth instance of the class this block exists to
    close — a measurement lying about the topology it reports.
    """
    for argv, environ in (
        (("uvicorn", "app:app", "--reload", "--workers", "4"), {}),
        # click's vocabulary, not ours: `y`/`t` DO start the reloader.
        (("uvicorn", "app:app", "--workers", "4"), {"UVICORN_RELOAD": "y"}),
        (("uvicorn", "app:app", "--workers", "4"), {"UVICORN_RELOAD": "t"}),
    ):
        proc = _run_worker_block(argv, environ)
        assert proc.returncode == 0, proc.stderr
        assert "workers = 1 (reload" in proc.stdout, (argv, environ, proc.stdout)
        assert "MULTI-WORKER" not in proc.stdout, (argv, environ, proc.stdout)

    # …and a value click REJECTS is not a reload: the count stands.
    proc = _run_worker_block(("uvicorn", "app:app", "--workers", "4"),
                            {"UVICORN_RELOAD": "enabled"})
    assert "MULTI-WORKER" in proc.stdout, proc.stdout

    # ⛔ A value click ACCEPTS but this shell cannot CLASSIFY. click trims with
    # Python's `str.strip()`, which strips Unicode spaces that bash's
    # `[[:space:]]` cannot match in ANY locale (MEASURED: NBSP survives
    # `_trim_edges` under C, C.UTF-8 and en_US.UTF-8, while `'\xa0y'.strip()`
    # is `'y'`, so click reads TRUE and uvicorn starts ONE reloader child).
    # The banner must degrade to UNVERIFIED — neither "1 (reload" nor a
    # confident "MULTI-WORKER" — because asserting 4 workers here is the lie
    # this block exists to remove. Report-only: the GATE's decision is
    # unchanged (an unprovable reload keeps the `--lifespan off` refusal).
    proc = _run_worker_block(("uvicorn", "app:app", "--workers", "4"),
                            {"UVICORN_RELOAD": "\u00a0y"})
    assert proc.returncode == 0, proc.stderr
    assert "UNVERIFIED" in proc.stdout, proc.stdout
    assert "MULTI-WORKER" not in proc.stdout, proc.stdout


def test_the_shipped_block_refuses_a_multi_worker_boot_with_the_lifespan_off():
    """⛔ The gate cannot run without the lifespan — so the boot script must.

    `assert_multi_worker_admissible()` is the FIRST statement of
    `hosted_api._lifespan`, so `--lifespan off` (or click's
    `$UVICORN_LIFESPAN=off`) removes it: MEASURED on the pinned 0.52.1,
    `UVICORN_LIFESPAN=off WEB_CONCURRENCY=2` with no `TORTOISE_DB_URI` served TWO
    server processes and the gate was never entered. N workers on an embedded DB
    are N divergent copies of one file, so the boot script refuses where it can
    still see the count.
    """
    for argv, environ in (
        (("uvicorn", "app:app", "--workers", "2"), {"UVICORN_LIFESPAN": "off"}),
        (("uvicorn", "app:app", "--lifespan", "off"), {"WEB_CONCURRENCY": "2"}),
        (("uvicorn", "app:app", "--lifespan=off"), {"WEB_CONCURRENCY": "2"}),
    ):
        proc = _run_worker_block(argv, environ)
        assert proc.returncode != 0, (argv, environ, proc.stdout)
        assert "REFUSING" in proc.stderr, (argv, environ, proc.stderr)
        # The message must name where the gate lives, or it is a mystery.
        assert "hosted_api._lifespan" in proc.stderr

    # A CLI `--lifespan on` beats the envvar (click gives the CLI value
    # precedence), so this boot is gated in the app as usual.
    proc = _run_worker_block(("uvicorn", "app:app", "--lifespan", "on",
                              "--workers", "2"), {"UVICORN_LIFESPAN": "off"})
    assert proc.returncode == 0, proc.stderr
    assert "MULTI-WORKER" in proc.stdout

    # One worker with the lifespan off is not gated by this rule — nothing to
    # gate — but the loss of the gate is a fact the operator should see.
    proc = _run_worker_block(("uvicorn", "app:app"), {"UVICORN_LIFESPAN": "off"})
    assert proc.returncode == 0, proc.stderr
    assert "lifespan DISABLED" in proc.stdout, proc.stdout
    assert "verified as ONE worker" in proc.stdout, proc.stdout


def test_the_lifespan_off_refusal_covers_counts_the_shell_CANNOT_see():
    """⛔ The first fix guarded only the counts the boot script can measure.

    uvicorn has a FOURTH count input this script cannot see: `Config.__init__`
    runs `load_dotenv(dotenv_path=env_file)` BEFORE it reads `$WEB_CONCURRENCY`
    (`uvicorn/config.py:348-353`), so `--env-file` / click's `$UVICORN_ENV_FILE`
    (`main.py:191-197`) sets the count AFTER the measurement. MEASURED on the
    pinned 0.52.1: an env file holding `WEB_CONCURRENCY=4`, with
    `UVICORN_LIFESPAN=off`, started FOUR server processes and the gate was never
    entered — while this block reported "one worker is serving".

    And a raw count the shell cannot parse (`int()` also accepts non-ASCII
    decimal digits and Unicode whitespace — `WEB_CONCURRENCY=$'\xc2\xa02'`
    starts TWO children) is a NORMALISATION to 1, not a measurement, so
    "nothing to gate" may not be printed on the strength of it.
    """
    for argv, environ in (
        (("uvicorn", "app:app"), {"UVICORN_LIFESPAN": "off",
                                "UVICORN_ENV_FILE": "/tmp/four.env"}),
        (("uvicorn", "app:app", "--env-file", "/tmp/four.env"),
         {"UVICORN_LIFESPAN": "off"}),
        (("uvicorn", "app:app", "--env-file=/tmp/four.env", "--workers", "1"),
         {"UVICORN_LIFESPAN": "off"}),
        (("uvicorn", "app:app"), {"UVICORN_LIFESPAN": "off",
                                "WEB_CONCURRENCY": "\u00a02"}),
        # ⛔ A MASKED count: `--host --workers=1` makes the `--workers=1` token
        # `--host`'s VALUE, so uvicorn reads `$WEB_CONCURRENCY` — FOUR, measured
        # on the pinned 0.52.1 — while a membership scan reads 1 and certifies
        # "one worker, nothing to gate". The skip walk must see no workers flag
        # here, so the real count reaches the guard and the boot is refused.
        (("uvicorn", "app:app", "--host", "--workers=1",
          "--lifespan", "off"), {"WEB_CONCURRENCY": "4"}),
        (("uvicorn", "app:app", "--app-dir", "--workers=1",
          "--lifespan", "off"), {"WEB_CONCURRENCY": "4"}),
        (("uvicorn", "app:app", "--root-path", "--workers=1",
          "--lifespan", "off"), {"WEB_CONCURRENCY": "4"}),
        # ⛔ A MASKED *lifespan* flag — the mirror of the masked count above, and
        # the class rounds 7-11 fixed in the other two readers only. `--app-dir
        # --lifespan=auto` makes the second token `--app-dir`'s VALUE, so click
        # keeps `--lifespan off`; a membership scan reads "auto" and concludes
        # the gate runs. MEASURED on the pinned 0.52.1: N processes, lifespan
        # entered ZERO times, exit 0. The arity walk must see `off`, so the
        # unreadable count on top of it refuses.
        (("uvicorn", "app:app", "--lifespan", "off", "--app-dir",
          "--lifespan=auto"), {"WEB_CONCURRENCY": "\u00a02"}),
        (("uvicorn", "app:app", "--lifespan", "off", "--host",
          "--lifespan=auto"), {"WEB_CONCURRENCY": "\u0661\u0662"}),
        (("uvicorn", "app:app", "--lifespan", "off", "--app-dir",
          "--lifespan=auto"), {"UVICORN_ENV_FILE": "/tmp/four.env"}),
        # ⛔ A COUNT THAT IS ASCII-DIGITS BUT THAT BASH CANNOT EVALUATE. bash
        # arithmetic is `intmax_t`, so a 21-digit value passes the "is an ASCII
        # integer" regex and then makes `[ "$WORKERS" -gt 1 ]` fail with
        # `integer expression expected`, which reads as FALSE — i.e. as "1
        # worker" — and printed "the count is verified as ONE worker" plus a raw
        # shell diagnostic. Not a fail-open (the pre-exec gate refuses on it), but
        # a false verdict on a fail-closed path. Must REFUSE by the readability
        # arm, and must not claim the count was verified.
        (("uvicorn", "app:app"),
         {"UVICORN_LIFESPAN": "off", "WEB_CONCURRENCY": "9" * 21}),
        # ⛔ A WRAPPED command line that turns the lifespan off INSIDE the shell
        # string. Every reader sees one opaque token, so the count resolves to 1
        # and no lifespan flag exists — while uvicorn runs the string and starts
        # N workers with the lifespan never run, i.e. NO gate on either side.
        # MEASURED on the shipped block before this row: rc=0,
        # "uvicorn workers = 1", real uvicorn = 4 workers, lifespan runs = 0.
        (("sh", "-c", "uvicorn app:app --lifespan off --workers 4"), {}),
        (("bash", "-lc", "uvicorn app:app --lifespan=off"), {}),
        (("sh", "-c", "UVICORN_LIFESPAN=off uvicorn app:app --workers 4"), {}),
    ):
        proc = _run_worker_block(argv, environ)
        assert proc.returncode != 0, (argv, environ, proc.stdout)
        assert "REFUSING" in proc.stderr, (argv, environ, proc.stderr)

    # …and with the lifespan on, an env file is REPORTED as unverified rather
    # than printed as a count this script cannot know. The app's gate still
    # refuses the spawned children, so this is a report fix, not a gate.
    proc = _run_worker_block(("uvicorn", "app:app", "--lifespan", "on"),
                             {"UVICORN_ENV_FILE": "/tmp/four.env",
                              "TORTOISE_DB_URI": _SHARED["TORTOISE_DB_URI"]})
    assert proc.returncode == 0, proc.stderr
    assert "UNVERIFIED" in proc.stdout, proc.stdout

    # ⛔ A wrapped CMD whose lifespan is ON is REPORTED, not refused: the app's
    # lifespan gate DOES run there and refuses the spawned children, so the only
    # defect is a banner claiming a count this script never measured.
    proc = _run_worker_block(
        ("sh", "-c", "uvicorn app:app --workers 4"),
        {"TORTOISE_DB_URI": _SHARED["TORTOISE_DB_URI"],
         "TORTOISE_MULTI_WORKER": "1"})
    assert proc.returncode == 0, proc.stderr
    assert "UNVERIFIED" in proc.stdout, proc.stdout
    assert "workers = 1" not in proc.stdout, (
        "the block printed a count it never measured for a wrapped command line: "
        f"{proc.stdout!r}"
    )

    # ⛔ AND THE CONTROL — the wrapper predicate must stay NARROW. A legitimate
    # spaced VALUE (`--header "X: Y"`) is not a shell string, so a provably
    # single-worker boot with the lifespan off is still ALLOWED.
    proc = _run_worker_block(
        ("uvicorn", "app:app", "--lifespan", "off",
         "--header", "X: Y"), {})
    assert proc.returncode == 0, proc.stderr
    assert "verified as ONE worker" in proc.stdout, proc.stdout

    # ⛔ AND THE MIRROR — the arity walk must not OVER-block. `--app-dir
    # --lifespan=off` makes the token `--app-dir`'s VALUE, so click keeps
    # `lifespan=auto`, the app gate DOES run, and a shared+acknowledged 4-worker
    # boot is legal. A membership scan would read "off" and refuse a boot the
    # gate itself permits — the over-block a naive arity fix introduces.
    proc = _run_worker_block(
        ("uvicorn", "app:app", "--app-dir", "--lifespan=off"),
        {"WEB_CONCURRENCY": "4", "TORTOISE_DB_URI": _SHARED["TORTOISE_DB_URI"],
         wt.ACK_ENV: "1"},
    )
    assert proc.returncode == 0, (proc.stderr, proc.stdout)
    assert "REFUSING" not in proc.stderr, proc.stderr
    assert "MULTI-WORKER" in proc.stdout, proc.stdout


def test_the_lifespan_off_refusal_holds_under_a_COLLATING_locale():
    """⛔ END-TO-END, in a locale where `[0-9]` is not a byte class.

    The predicate arm above proves `_count_is_verifiable` answers UNREADABLE; it
    cannot prove the SHIPPED block still refuses, because the block has two more
    digit readers on the same value (`_resolve_requested_workers`' `case`, and
    the `-gt 1` arithmetic test). MEASURED on the pre-fix block, this exact
    invocation (`en_US.utf8`, `--lifespan off`, `WEB_CONCURRENCY=١٢`):

        tortoise: … the count is verified as ONE worker, so there is nothing to gate
        tortoise: uvicorn workers = 1
        WORKERS=١٢                                        rc=0

    …while uvicorn's `int("١٢")` starts TWELVE children and the lifespan-off
    boot never installs the gate: N ungated workers on N divergent copies of one
    embedded database. The arm is the P1's own transcript, so it cannot pass on
    a head that reproduces it.
    """
    locale = _collating_utf8_locale()
    if locale is None:
        pytest.skip("no UTF-8 locale on this host folds decimal digits")

    for count in ("١٢", "١٢ ", "١٢١"):
        proc = _run_worker_block(
            ("uvicorn", "app:app"),
            {"UVICORN_LIFESPAN": "off", "WEB_CONCURRENCY": count},
            locale=locale,
        )
        assert proc.returncode != 0, (count, locale, proc.stdout, proc.stderr)
        assert "REFUSING" in proc.stderr, (count, locale, proc.stderr)
        # …and the banner may not announce a topology nobody measured: on the
        # pre-fix block BOTH of these lines printed, at exit 0.
        assert "verified as ONE worker" not in proc.stdout, (count, proc.stdout)
        assert f"WORKERS={count.strip()}" not in proc.stdout, (count, proc.stdout)

    # The pin is a PIN, not a change of grammar: an ordinary count still boots,
    # and the refusal is still attributable to the spelling (same locale, 2 is
    # refused as N>=2 — a different reason, asserted so the arm above cannot be
    # satisfied by a block that refuses everything).
    proc = _run_worker_block(("uvicorn", "app:app"),
                             {"UVICORN_LIFESPAN": "off", "WEB_CONCURRENCY": "1"},
                             locale=locale)
    assert proc.returncode == 0, proc.stderr
    assert "verified as ONE worker" in proc.stdout, proc.stdout
    proc = _run_worker_block(("uvicorn", "app:app"),
                             {"UVICORN_LIFESPAN": "off", "WEB_CONCURRENCY": "2"},
                             locale=locale)
    assert proc.returncode != 0, proc.stdout
    assert "2 uvicorn workers" in proc.stderr, proc.stderr


#: A UTF-8 locale whose collation folds decimal digits — the only reason `[0-9]`
#: is not a byte class in bash. Probed once, so the arm below skips on a host
#: without it instead of failing for the wrong reason.
def _collating_utf8_locale() -> str | None:
    available = subprocess.run(["locale", "-a"], capture_output=True,
                               text=True, check=False).stdout.split()
    for name in ("en_US.UTF-8", "en_US.utf8", "C.utf8", "C.UTF-8"):
        if name not in available:
            continue
        # The probe runs the TWO shells the predicate is written in, BOTH under
        # the locale — probing one construct in the caller's locale and the
        # other under a nested `LC_ALL=` prefix answered the wrong question
        # (the `case` matched, the `=~` did not, and the arm skipped on a host
        # that really does collate).
        probe = subprocess.run(
            ["bash", "-c",
             '[[ "١٢" =~ ^[0-9]+$ ]] || exit 1\n'
             'case "١٢" in *[!0-9]*) exit 1 ;; esac\n'],
            env={**os.environ, "LC_ALL": name, "LANG": name},
            capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return name
    return None


def test_the_count_predicates_are_LOCALE_PINNED():
    """`[0-9]` is a COLLATING range, and `_redact_uri` already paid for this.

    Under a locale that folds digits, `[[ "١٢" =~ ^[0-9]+$ ]]` is TRUE and
    `case … *[!0-9]*` does not match — MEASURED on this host: `LC_ALL=C`
    refuses, `LC_ALL=en_US.utf8` admits. `int("١٢") == 12`, so the unpinned
    pair agrees that a twelve-worker boot is "one worker" and, with the lifespan
    off, prints "nothing to gate" and exits 0.

    The fix is the file's own established pin (`_redact_uri`: `local LC_ALL=C;
    export LC_ALL`), and this arm runs the REAL shipped block under a collating
    locale so the pin cannot silently rot out of one helper.
    """
    locale = _collating_utf8_locale()
    if locale is None:
        pytest.skip("no UTF-8 locale on this host folds decimal digits")

    # ⛔ The predicate ITSELF, not `_run_worker_block`'s exit code: on a head that
    # also runs the pre-exec policy gate, a refusal arrives for a different
    # reason and the arm stops discriminating (measured: it passed with every
    # pin removed). `_count_is_verifiable` is the function the lifespan-off
    # guard asks, so it is the function that must say "unreadable" here.
    script = (
        "set -euo pipefail\n"
        + _worker_block(ENTRYPOINT.read_text())
        + '\nif _count_is_verifiable "$@"; then echo VERIFIABLE; else echo UNREADABLE; fi\n'
        + 'printf \'RESOLVED=%s\\n\' "$(_resolve_requested_workers \"$@\")"\n'
    )

    def run(argv: tuple[str, ...], count: str, name: str) -> str:
        proc = subprocess.run(
            ["bash", "-c", script, "entrypoint", *argv],
            env={"PATH": os.environ.get("PATH", ""), "_IS_SERVER": "1",
                 "WEB_CONCURRENCY": count, "LC_ALL": name, "LANG": name},
            capture_output=True, text=True, check=False)
        assert proc.returncode == 0, (count, name, proc.stderr)
        return proc.stdout

    def resolved(out: str) -> str:
        return [ln for ln in out.splitlines() if ln.startswith("RESOLVED=")][-1][9:]

    for count in ("١٢", "١٢ ", "١٢١"):
        out = run(("uvicorn", "app:app"), count, locale)
        assert "UNREADABLE" in out, (count, locale, out)
        # ⛔ The RESOLVER's own pin, asserted on its VALUE, not on a spelling.
        # `RESOLVED=12` as a substring could never fail: the unpinned resolver
        # emits the Arabic-Indic spelling `RESOLVED=١٢`, which does not contain
        # ASCII `12` — MUTATION-CHECKED (pin removed from that one helper only:
        # this arm passed). The C-locale verdict for an unreadable count is 1,
        # and that is the number the banner and `int()` must not read as twelve.
        assert resolved(out) == "1", (count, locale, out)

    # The pin is a PIN, not a mutation of the grammar: under `C` the same input
    # is unreadable for the ordinary reason, and an ASCII count is read in both.
    for count in ("١٢", "2"):
        out = run(("uvicorn", "app:app"), count, "C")
        assert ("VERIFIABLE" in out) is (count == "2"), (count, out)
        assert resolved(out) == ("2" if count == "2" else "1"), (count, out)


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

    # …and POSITION alone is not enough: a `try/except Exception` around the call
    # keeps it at index 0 while making the gate a no-op that the suite reads as
    # green. `_lifespan` already carries several such blocks BELOW the gate, so
    # swallowing this one is a plausible edit, not a hypothetical. Walk the AST
    # and refuse any enclosing handler that would catch the refusal.
    #
    # ⛔ ALIASES ARE RESOLVED, not guessed. The real name arrives through
    # `from tortoise.worker_topology import …`, so `except MWR:` with
    # `MultiWorkerRefused as MWR` keeps the gate at index 0 and swallows it —
    # MUTATION-CHECKED: that shape PASSED the name-only version of this pin.
    # Same for the reverse alias direction (`assert_multi_worker_admissible`
    # imported under another name), which is collected below as `gate_names`.
    module_aliases = {
        (alias.asname or alias.name): alias.name
        for node in ast.walk(ast.parse(HOSTED_API.read_text()))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    def canonical(name: str) -> str:
        """Resolve a module-scope alias to the name it was imported as."""
        return module_aliases.get(name, name)

    gate_names = {
        local for local, real in module_aliases.items()
        if real == "assert_multi_worker_admissible"
    }
    assert gate_names, (
        "hosted_api no longer imports the #1677 admission gate at module scope "
        "— the refusal is unreachable from the app"
    )

    def is_gate_call(inner: ast.AST) -> bool:
        """True for a CALL of the gate in EITHER spelling.

        ⛔ The scan below is what refuses an enclosing handler, so it must
        recognise every way the gate can be *called*. It used to match only
        ``ast.Name``, so a call written through the module —
        ``tortoise.worker_topology.assert_multi_worker_admissible()``, an
        ``ast.Attribute`` — left the position arm passing (index 0) while this
        scan skipped it, and ``try: <qualified call>`` / ``except Exception:
        pass`` then swallowed the refusal with all 194 cases green.
        ``caught_names`` had already been taught the attribute spelling; the
        call side had not — the same one-sided re-learning this pin has done
        before (alias, then ``except*``).
        """
        if not isinstance(inner, ast.Call):
            return False
        func = inner.func
        if isinstance(func, ast.Name):
            return canonical(func.id) == "assert_multi_worker_admissible"
        if isinstance(func, ast.Attribute):
            return func.attr in gate_names or (
                func.attr == "assert_multi_worker_admissible"
            )
        return False

    def caught_names(caught: ast.expr | None) -> set[str]:
        """Every CATCHABLE name in a handler, with aliases resolved."""
        if caught is None:                      # bare `except:`
            return {"BaseException"}
        elements = caught.elts if isinstance(caught, ast.Tuple) else [caught]
        found: set[str] = set()
        for element in elements:
            if isinstance(element, ast.Name):
                found.add(canonical(element.id))
            elif isinstance(element, ast.Attribute):
                # `except worker_topology.MultiWorkerRefused:` — the qualified
                # spelling is just as functional as the bare name.
                found.add(element.attr)
        return found

    swallowing = {
        "Exception", "BaseException", "MultiWorkerRefused", "RuntimeError",
    }
    for node in ast.walk(_lifespan_fn()):
        # ⛔ `except*` is a DIFFERENT NODE and it swallows just as well:
        # `ast.TryStar` is NOT a subclass of `ast.Try` (verified), and
        # `try: raise RuntimeError() / except* RuntimeError:` catches a bare
        # raise — so `except* MWR: pass` kept the gate at index 0 and PASSED
        # (mutation-checked). `tools/substrate_map_check.py` carries the same
        # pair for the same reason (#5086 review P3); this pin had re-learned
        # aliases and attributes but not the star form.
        if not isinstance(node, (ast.Try, ast.TryStar)):
            continue
        for handler in node.handlers:
            names = caught_names(handler.type)
            if names & swallowing and any(
                    is_gate_call(inner)
                    for inner in ast.walk(node)):
                raise AssertionError(
                    "hosted_api._lifespan wraps the #1677 admission gate in a "
                    f"try/except {sorted(names)} — the gate stays at index 0 "
                    "while the refusal is swallowed, so a misconfigured "
                    "multi-worker boot would start instead of being refused"
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


# ─────────────────────────────────────────────────────────────────────────────
# 6. The SHIPPED pre-exec gate — a refusal must EXIT NON-ZERO
# ─────────────────────────────────────────────────────────────────────────────
#
# ⛔ Why the entrypoint gate exists at all as an ENFORCER (uvicorn 0.52.1,
# measured): a refusal raised from a worker's startup exits **0** through
# uvicorn's Multiprocess supervisor (`keep_subprocess_alive` sets `should_exit`
# and returns; only the single-process path raises `SystemExit(3)`). Measured:
# a raising lifespan with `--workers 3` -> exit 0; one worker -> exit 3. With
# `fly.toml`'s `restart policy = "on-failure"`, exit 0 is a STOPPED machine with
# no crash loop (#2850) — precisely in the N>1 case the refusal exists for.
# The pre-exec gate converts that into `exit 1`, and its STATUS is the property
# under test.

def test_the_shipped_gate_refuses_n_workers_on_an_embedded_db_with_exit_1():
    proc = _run_gate_block(
        ("uvicorn", "tortoise.hosted_api:app", "--workers", "2"), {})
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "admission gate refused this boot" in proc.stderr
    assert "EMBEDDED" in proc.stderr


def test_the_shipped_gate_permits_a_single_worker():
    proc = _run_gate_block(("uvicorn", "tortoise.hosted_api:app"), {})
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "workers = 1" in proc.stdout


def test_the_shipped_gate_refuses_an_unacknowledged_shared_multi_worker():
    proc = _run_gate_block(("uvicorn", "app:app", "--workers", "2"), dict(_SHARED))
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert wt.ACK_ENV in proc.stderr


def test_the_shipped_gate_permits_an_acknowledged_shared_multi_worker():
    proc = _run_gate_block(
        ("uvicorn", "app:app", "--workers", "2"), {**_SHARED, wt.ACK_ENV: "1"})
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "workers = 2" in proc.stdout


def test_the_shipped_gate_refuses_a_count_that_came_from_web_concurrency():
    """The gate must read the count the SCRIPT resolved, not just the argv."""
    proc = _run_gate_block(
        ("uvicorn", "app:app"), {**_SHARED, wt.WEB_CONCURRENCY_ENV: "3"})
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert wt.ACK_ENV in proc.stderr


#: Every spelling click's ``BoolParamType`` accepts as TRUE — read from
#: ``worker_topology._CLICK_TRUE``, uvicorn's own flag grammar — plus casing and
#: surrounding whitespace, because click's ``str_to_bool`` strips and lowercases.
#:
#: Driven from the Python constant ON PURPOSE: ``entrypoint.sh``'s reload detector
#: is a hand-written ``case``, so a spelling added to ``_CLICK_TRUE`` without the
#: shell keeping up would make the boot line lie (4 workers announced for the one
#: child a reloader serves) — and these arms red instead.
_CLICK_TRUE_SPELLINGS = (*sorted(wt._CLICK_TRUE), "TRUE", "  Yes  ")

#: Spellings click REJECTS. uvicorn exits before an app runs on these, so the
#: reloader never starts — both halves must read them as NOT a reload, and the
#: gate must stay refused (fail-closed), or a typo would become an exemption from
#: the embedded-divergence arm the acknowledgement cannot lift.
_RELOAD_REJECTED = ("enabled", "yess", "0", "false", "no", "off", "")


def test_the_shipped_gate_sees_the_reload_token_as_one_worker_above_one():
    """The pre-exec gate's reload arm, at a count that is REFUSED without it.

    ⛔ The count matters: `--reload` ALONE resolves to one worker with or without
    any exemption, so an arm shaped that way could not red (the shape this test
    replaced). Here the count is 4, and the same argv without the token is refused
    by the very next assertion — so the permit is caused by the exemption, and
    dropping the argv from the gate's `observe()` (or narrowing
    `reload_is_requested`) turns this red.
    """
    proc = _run_gate_block(("uvicorn", "app:app", "--reload", "--workers", "4"), {})
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    # The banner and the gate must agree: one child serves, so neither half may
    # announce a multi-worker topology.
    assert "workers = 1 (reload" in proc.stdout, proc.stdout
    assert "MULTI-WORKER" not in proc.stdout, proc.stdout

    # Control: the SAME count with no reload input is refused on an embedded DB.
    control = _run_gate_block(("uvicorn", "app:app", "--workers", "4"), {})
    assert control.returncode == 1, (control.stdout, control.stderr)
    assert "EMBEDDED" in control.stderr, control.stderr


@pytest.mark.parametrize("spelling", _CLICK_TRUE_SPELLINGS)
def test_the_shipped_gate_exempts_every_click_true_reload_spelling(spelling):
    """`$UVICORN_RELOAD` is the flag's value — so the gate must read it as a reload.

    Both halves are asserted on the same boot: the banner reports the ONE child
    uvicorn serves, and the gate PERMITS at a count (4) it refuses without the
    variable. That is the parity contract between ``entrypoint.sh``'s ``case`` arm
    and :data:`worker_topology._CLICK_TRUE` — the vocabulary the flag is actually
    parsed with (click 8.4.2 ``BoolParamType.bool_states``).
    """
    argv = ("uvicorn", "app:app", "--workers", "4")
    proc = _run_gate_block(argv, {wt.RELOAD_ENV: spelling})
    assert proc.returncode == 0, (spelling, proc.stdout, proc.stderr)
    assert "workers = 1 (reload" in proc.stdout, (spelling, proc.stdout)

    control = _run_gate_block(argv, {})
    assert control.returncode == 1, (spelling, control.stdout, control.stderr)


@pytest.mark.parametrize("spelling", _RELOAD_REJECTED)
def test_a_spelling_click_rejects_does_not_exempt_the_shipped_gate(spelling):
    """A typo is not an acknowledgement — both halves stay on the refusing side."""
    proc = _run_gate_block(("uvicorn", "app:app", "--workers", "4"),
                           {wt.RELOAD_ENV: spelling})
    assert proc.returncode == 1, (spelling, proc.stdout, proc.stderr)
    assert "MULTI-WORKER" in proc.stdout, (spelling, proc.stdout)


# ─────────────────────────────────────────────────────────────────────────────
# ARITY — a value that looks like a flag is not a flag
# ─────────────────────────────────────────────────────────────────────────────

def test_the_uvicorn_flag_set_is_uvicorns_own():
    """`UVICORN_FLAG_OPTIONS` cannot drift from uvicorn's click parser.

    The reload verdict is a fail-OPEN if a value-taking option is mistaken for a
    flag (or the reverse), so the transcribed set is asserted against
    `uvicorn.main.main.params` — the parser that will actually read the argv. A
    uvicorn bump that adds or removes a flag reds HERE instead of silently
    turning `--reload-include --reload` back into a permitted N-worker boot.

    ⛔ BOTH halves of every option. click files the negative half of an
    `--x/--no-x` pair in `param.secondary_opts`, NOT `param.opts`, so a pin that
    read `opts` alone could not see a missing `--no-*` at all: it was green on a
    table that was short by five, while `--no-server-header --app-dir --reload
    --workers 2` read as "reloading" and the gate PERMITTED two workers on an
    embedded database (measured: click parses `reload=False`, `workers=2`,
    `app_dir="--reload"`).
    """
    from uvicorn.main import main as uvicorn_main

    flags = set()
    for param in uvicorn_main.params:
        opts = [
            o
            for o in (*getattr(param, "opts", ()),
                      *getattr(param, "secondary_opts", ()))
            if o.startswith("--")
        ]
        if not opts:
            continue
        if getattr(param, "is_flag", False) or getattr(param, "flag_value", None):
            flags.update(opts)
    assert flags == set(wt.UVICORN_FLAG_OPTIONS)


def test_a_reload_lookalike_value_is_not_a_reloader():
    """⛔ MEASURED fail-open (uvicorn 0.52.1): `--reload-include --reload
    --workers 2` runs TWO workers and NO reloader, because `--reload` is the
    INCLUDE PATTERN. Token membership read that as a reloader, so
    `is_multi_worker` was False and the gate PERMITTED N workers on an embedded
    database — the arm `TORTOISE_MULTI_WORKER` cannot lift.
    """
    argv = ["uvicorn", "app:app", "--reload-include", "--reload", "--workers", "2"]
    assert wt.reload_is_requested(argv) is False

    topology = wt.observe({}, supervised=True, argv=argv)
    assert topology.reloading is False
    assert topology.is_multi_worker is True
    assert wt.refusal_reason(topology) is not None

    # …while the genuine shapes still read as a reloader.
    for real in (["uvicorn", "a:a", "--reload"],
                 ["uvicorn", "a:a", "--host", "0.0.0.0", "--reload"],
                 ["uvicorn", "a:a", "--factory", "--reload"]):
        assert wt.reload_is_requested(real) is True, real
    for fake in (["uvicorn", "a:a", "--reload-dir", "src"],
                 ["uvicorn", "a:a", "--app-dir", "--reload", "--workers", "2"],
                 ["uvicorn", "a:a", "--reload=1"]):
        assert wt.reload_is_requested(fake) is False, fake


def test_a_no_star_flag_does_not_swallow_the_next_token():
    """⛔ MEASURED fail-open: uvicorn's five `--x/--no-x` pairs are FLAGS, and a
    table holding only the primary half shifted the arity walk by one.

    `uvicorn app:app --no-server-header --app-dir --reload --workers 2`, read by
    click (pinned 0.52.1), is `reload=False`, `workers=2`,
    `app_dir="--reload"` — NO reloader and TWO workers. A table without
    `--no-server-header` made that token consume `--app-dir`, so `--reload` was
    read as the flag: `reloading=True` → `is_multi_worker` False → the gate
    PERMITTED two DIVERGENT embedded copies, the exact bypass this branch closed
    for `--reload-include --reload`.

    The mirror shape is the over-block the same bug produced: with
    `--no-access-log --reload --workers 4` the negative flag ate `--reload`, so
    the count read 4 with no reload exemption and a ONE-child reloader boot was
    refused with no escape hatch on the embedded arm.
    """
    # Shape A — fail-open: reload must NOT be read, and the gate must REFUSE.
    swallow = ["uvicorn", "app:app", "--no-server-header", "--app-dir",
               "--reload", "--workers", "2"]
    assert wt.reload_is_requested(swallow) is False
    topology = wt.observe({}, supervised=False, argv=swallow)
    assert topology.reloading is False
    assert topology.workers == 2
    assert topology.is_multi_worker is True
    assert wt.refusal_reason(topology) is not None

    # Shape B — over-block: the same flag in VALUE position, then a real reload.
    permit = ["uvicorn", "app:app", "--no-access-log", "--reload", "--workers", "4"]
    assert wt.reload_is_requested(permit) is True
    topology = wt.observe({}, supervised=False, argv=permit)
    assert topology.reloading is True
    assert topology.is_multi_worker is False
    assert wt.refusal_reason(topology) is None

    # Every one of the five pairs, both halves, must consume nothing: a flag in
    # flag position leaves the following `--reload` a TOKEN, so it is read.
    for name in ("access-log", "use-colors", "proxy-headers",
                 "server-header", "date-header"):
        for spelling in (f"--{name}", f"--no-{name}"):
            argv = ["uvicorn", "a:a", spelling, "--reload", "--workers", "4"]
            assert wt.reload_is_requested(argv) is True, spelling


def test_the_shipped_banner_does_not_claim_a_reloader_for_a_lookalike_value():
    """The shipped shell half must use ARITY too, or the banner lies about it."""
    proc = _run_gate_block(
        ("uvicorn", "app:app", "--reload-include", "--reload", "--workers", "2"), {})
    assert "reload" not in proc.stdout.lower(), proc.stdout
    assert "workers = 2" in proc.stdout, proc.stdout
    # …and the pre-exec gate refuses the boot, so the lie cannot stand alone.
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "admission gate refused this boot" in proc.stderr


def test_the_shipped_half_reads_a_no_star_flag_as_a_no_value_FLAG():
    """⛔ The SHIPPED shell list must carry the `--no-*` halves as well.

    Measured on the pre-fix block: `--no-server-header` was not in
    `_uvicorn_flag_option`, so the reload scan consumed `--app-dir` and then read
    `--reload` as the flag — the block printed
    `workers = 1 (reload; --workers is ignored while reloading)` and exited 0
    for a boot uvicorn serves as TWO workers with NO reloader (click: `reload`
    False, `workers` 2, `app_dir` "--reload"). The mirror shape
    (`--no-access-log --reload --workers 4`) was refused although uvicorn serves
    one reloader child.
    """
    swallow = _run_gate_block(
        ("uvicorn", "app:app", "--no-server-header", "--app-dir",
         "--reload", "--workers", "2"), {})
    assert "reload" not in swallow.stdout.lower(), swallow.stdout
    assert "workers = 2" in swallow.stdout, swallow.stdout
    assert swallow.returncode == 1, (swallow.stdout, swallow.stderr)
    assert "admission gate refused this boot" in swallow.stderr

    permit = _run_gate_block(
        ("uvicorn", "app:app", "--no-access-log", "--reload", "--workers", "4"), {})
    assert permit.returncode == 0, (permit.stdout, permit.stderr)
    assert "workers = 1 (reload" in permit.stdout, permit.stdout


def test_the_shipped_flag_case_is_uvicorns_own():
    """The shell's flag list must equal `UVICORN_FLAG_OPTIONS`.

    Without this, editing `entrypoint.sh`'s list is invisible: MEASURED —
    deleting `--factory` from the shell list left the suite green while
    `uvicorn app:app --factory --reload --workers 4` printed "workers = 4 —
    MULTI-WORKER topology" (a lying banner) with the gate correctly at rc 0.
    The shell cannot import Python's set, so the two are pinned together HERE
    rather than left to a comment.

    The list lives in `_uvicorn_flag_option`'s `case` (shared by the count AND
    reload readers). Scanned between `case "$1" in` and `esac` and NOT over the
    whole function, because the surrounding comments mention flag spellings
    (`--reload --workers 4`) that are not part of the list.
    """
    block = _worker_block(ENTRYPOINT.read_text())
    fn = block.index("_uvicorn_flag_option() {")
    arms = block[block.index('case "$1" in', fn):block.index("esac", fn)]
    shell_flags = {
        tok
        for raw in arms.replace("\\", " ").replace("|", " ").split()
        for tok in [raw.strip("()[];:\"'")]
        if tok.startswith("--")
    }
    assert shell_flags, "no flag alternatives found in `_uvicorn_flag_option`"
    assert shell_flags == set(wt.UVICORN_FLAG_OPTIONS)


def test_a_newline_before_a_reload_spelling_does_not_split_the_banner():
    """`$UVICORN_RELOAD=$'\\ny'` starts the reloader; the banner must say so.

    `sed`'s line-oriented trim could not strip the leading newline, so the shell
    read the value as unrecognised and printed "workers = 4 — MULTI-WORKER"
    while click started the reloader and the Python gate permitted it as
    `reloading=True` — the banner contradicting the gate.
    """
    proc = _run_gate_block(
        ("uvicorn", "app:app", "--workers", "4"), {wt.RELOAD_ENV: "\ny"})
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "reload" in proc.stdout.lower(), proc.stdout
    assert "MULTI-WORKER" not in proc.stdout, proc.stdout

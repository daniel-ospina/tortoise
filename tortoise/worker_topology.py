"""Multi-worker uvicorn topology — the fail-closed admission gate (#1677).

WHY THIS MODULE EXISTS
======================
`Dockerfile.hosted` passes no `--workers`, so production runs ONE uvicorn
worker. That is not a decision the engine can see: **uvicorn resolves
`--workers` as `CLI flag -> $UVICORN_WORKERS -> $WEB_CONCURRENCY -> 1`** (click's
`auto_envvar_prefix="UVICORN"` supplies the flag's value, then
`config.py`'s default source), so any actor that can set one Fly env var moves
the deployment to N workers with no code change, no review, and no signal. The
issue this module answers (#1677) measured what breaks at N >= 2, and all of it
is silent:

* the ``_lifespan`` BackupWatcher (files GitHub issues, sends Telegram) and the
  hourly event-retention loop arm **once per worker** — non-idempotent external
  side effects, N times (P1-2);
* ``_GITHUB_STATES`` — the GitHub OAuth CSRF register — is an in-memory dict, so
  onboarding CSRF-400s intermittently depending on which worker the callback
  lands on (P1-C, launch feature);
* ``GoldenSignals``/``collect_signals`` compute P99/throughput/error-rate
  **per process**, so at N >= 2 no process holds the service's numbers — a
  "boots and CSRF passes" smoke test stays green while the metrics go wrong;
* the 9090 ``/healthz`` listener binds on one worker and every other worker's
  bind failure is **swallowed** unless ``TORTOISE_HEALTHZ_REQUIRED=1``;
* each worker opens its own FalkorDB Cloud pool and its own JWKS cache — N x an
  external client budget.

Under ``multiprocessing``'s **spawn** start method (uvicorn's supervisor does not
fork) the ~85 s model pre-warm also re-executes in full per worker, so the
memory envelope is ~N x, not "1 x + copy-on-write pages" — and uvicorn has no
``--preload`` (that is a Gunicorn flag), so there is no COW mitigation.

Until those guards land, ``workers > 1`` is not a supported topology. This
module makes turning it on a **deliberate, logged act** instead of an ambient
side effect of an environment variable.

WHAT IT DOES NOT DO
===================
It does not make multi-worker work. It refuses it. The refusal names every
unlanded guard and the issue that owns it, so the message is a diagnosis rather
than a mystery — and it is the ledger a later lane edits when a guard lands.

TWO FACTS, MEASURED — NOT INFERRED
==================================
Both were measured against the pinned uvicorn (0.52.1) by booting a probe app
under each shape.

1. **The count.** uvicorn's own precedence: a CLI ``--workers N`` (either token
   form, and ``int()``-tolerant of ``+N``/whitespace) beats ``$WEB_CONCURRENCY``,
   which beats 1. Measured: ``--workers 2`` -> 2; ``--workers=5`` -> 5;
   ``--workers +2`` -> 2; ``WEB_CONCURRENCY=3`` -> 3; both together -> the CLI.

   ⛔ And ``$WEB_CONCURRENCY`` is only the flag's **DEFAULT**, not its fallback:
   ``config.py:352`` reads it under ``if workers is None`` — the *parameter* — so
   a ``--workers`` that IS given is FINAL even when it is non-positive. Measured
   on the pinned 0.52.1 with ``WEB_CONCURRENCY=4`` exported:
   ``Config(workers=None).workers == 4``, ``Config(workers=0).workers == 1``,
   ``Config(workers=-2).workers == -2`` (``> 1`` false, so ``main.py:617`` takes
   the in-process branch — one worker). Treating "present but non-positive" as
   "absent" therefore let ``$WEB_CONCURRENCY`` answer for a flag that had already
   overruled it.

   ⛔ And ``$UVICORN_WORKERS`` sits BETWEEN the flag and ``$WEB_CONCURRENCY``:
   click's ``auto_envvar_prefix="UVICORN"`` (``uvicorn/main.py``) makes it the
   flag's VALUE when the flag is not given, so ``config.py`` never reaches its
   ``$WEB_CONCURRENCY`` arm at all. Measured on the pinned 0.52.1:
   ``UVICORN_WORKERS=2`` -> a parent process and TWO served children;
   ``UVICORN_WORKERS=2 WEB_CONCURRENCY=4`` -> 2 (not 4);
   ``UVICORN_WORKERS=2 … --workers 1`` -> one in-process server (the CLI wins);
   ``UVICORN_WORKERS=0 WEB_CONCURRENCY=4`` -> one in-process server (a GIVEN
   value is final here too); ``UVICORN_WORKERS=''`` behaves as absent (click's
   own ``rv != ""`` rule), and the value goes through ``int()``'s grammar
   (``' 2 '`` and ``+2`` both start two workers). Both resolvers read it: a
   measurement that ignores it reports ``1`` for a machine running N, which is
   the report-is-wrong defect this module already fixed for ``--workers +2``.

2. **Whether this process is SUPERVISED — and, separately, how.** Under spawn,
   ``multiprocessing`` restores the parent's ``sys.argv`` in the child (measured:
   the child's ``sys.argv`` is the full uvicorn command line, ``--workers 2`` and
   ``--reload`` included), so a worker can resolve its own count from argv. And
   ``multiprocessing.parent_process()`` is non-``None`` for **every** uvicorn
   supervisor child — the multi-worker supervisor **and** the ``--reload``
   reloader (measured: ``--reload`` -> ``parent_process()`` = the reloader,
   ``SpawnProcess-1``, argv ending in ``--reload``).

⛔ The second measurement is why this module does NOT treat "has a supervisor
parent" as "is one of N". uvicorn forbids ``--workers`` with ``--reload``, so a
reload child is ALWAYS exactly one worker — and an earlier revision of this
module refused it as N>=2, which made ``uvicorn … --reload`` unstartable on an
embedded DB with **no** escape hatch (the acknowledgement deliberately does not
lift the embedded arm). The reload child is therefore identified positively from
its own argv and exempted.

REFUSAL IS FATAL, ON PURPOSE
============================
The gate raises during ``_lifespan`` startup. uvicorn's supervisor treats a
worker's ``STARTUP_FAILURE`` as fatal for the whole parent
(``supervisors/multiprocess.py::keep_subprocess_alive`` -> ``should_exit`` ->
``terminate_all``), so a misconfiguration is a loud, deploy-blocking refusal
rather than a machine that half-works. That is the same posture
``entrypoint.sh`` already takes for a missing ``FALKORDB_CLOUD_URI``
("Refusing to start with no durable DB"): a topology this deployment has not
been proved in is not a degraded mode to be tolerated, it is a corrupting one.

WHY NOT "just run 1 worker and warn"
------------------------------------
The count is the *parent's* — a worker cannot lower it. It could only refuse or
pretend. Pretending is how the ambient ``WEB_CONCURRENCY`` hole would survive.

THE ESCAPE HATCHES, STATED EXACTLY
==================================
* ``TORTOISE_MULTI_WORKER=1`` — acknowledgement that this deployment carries the
  multi-worker guards. It converts the "unlanded guards" refusal into a
  ``WARNING`` that re-lists the residual risks on every worker's boot, so the
  state stays visible instead of becoming folklore. Same shape as the repo's
  existing ``TORTOISE_HEALTHZ_REQUIRED`` / ``TORTOISE_ALLOW_NONSTANDARD_PATH``
  opt-ins.
* **No escape hatch for the embedded case.** With no resolvable
  ``TORTOISE_DB_URI`` the app runs an embedded redislite file; N workers get N
  *divergent* copies of it. That is silent data divergence, not a degraded
  feature, so ``TORTOISE_MULTI_WORKER`` does not reach it.

RESIDUALS, NAMED RATHER THAN IMPLIED
====================================
* **The gate only exists where the APP LIFESPAN runs.** It is installed as the
  first statement of ``hosted_api._lifespan``, so ``--lifespan off`` (or click's
  ``$UVICORN_LIFESPAN=off``) removes it: uvicorn starts N workers, none runs the
  gate, and on an embedded DB they open N divergent files. MEASURED on the
  pinned 0.52.1: ``UVICORN_LIFESPAN=off WEB_CONCURRENCY=2`` served two server
  processes with the gate never entered. The SHIPPED path is closed in
  ``entrypoint.sh`` (refuses when the count is >1 and the lifespan is off); a
  direct ``uvicorn tortoise.hosted_api:app --lifespan off --workers N`` that
  bypasses the boot script is NOT gated — it also disables every other startup
  hook (backup watcher, healthz, liveness), so it is a broken deployment, but the
  divergence would be silent rather than refused.
* **The repo ships a SECOND uvicorn app with NO gate**: ``tortoise.selfhost:app``
  (``tortoise/selfhost.py``'s own ``_lifespan``), served by
  ``Dockerfile.selfhost`` with ``ENV TORTOISE_DB_PATH=/data/tortoise.db`` — i.e.
  EMBEDDED by default — and no ``entrypoint.sh``. ``WEB_CONCURRENCY=4`` there is
  four workers on four copies of one file, with only that image's single-writer
  banner as a signal. That posture is a recorded decision (#942: bound embedded
  FalkorDBLite to single-writer, warn loudly at every runtime entrypoint) and
  #1677's components are hosted-only, so gating it is the OWNER's call, not this
  module's — it is named here so this ledger stops implying repo-wide coverage.
* A process manager that pre-forks before importing the app (Gunicorn +
  ``UvicornWorker``) leaves ``parent_process() is None``, so on that (not
  deployed, not supported) topology only the count arm speaks.
* ``uvicorn.run(workers=N)`` from a script — no ``--workers`` token and no
  ``$WEB_CONCURRENCY`` — is caught only by the supervised-parent arm, whose
  message then says the SERVED count was not readable rather than inventing one.
* The reload exemption is keyed on the ``--reload`` token **and**
  ``$UVICORN_RELOAD`` — uvicorn's own two inputs for that flag (click's
  ``auto_envvar_prefix="UVICORN"``), both measured on the pinned 0.52.1. A
  reloader started PROGRAMMATICALLY (``uvicorn.run(reload=True)`` from a script)
  leaves neither, so such a child would be read as a supervised multi-worker and
  refused (fail-closed). Nothing in this repo launches the app that way.
* The MIRROR of that case is the one place the env arm can read HIGH: an ambient
  ``$UVICORN_RELOAD`` truthy while the app is launched by ``uvicorn.run(workers=N)``
  (which bypasses click, so the reloader never starts) is read as ``reloading``
  and therefore PERMITTED. Nothing in this repo uses the programmatic launcher
  (``Dockerfile.hosted`` runs the CLI), and it needs BOTH a stray variable and a
  launcher this repo does not use; it is named rather than implied.
* **The refusal is fatal to SERVICE, not to the parent's EXIT STATUS.** MEASURED
  on the pinned 0.52.1: both children raise ``MultiWorkerRefused``, the parent
  logs ``stopping the parent process`` and exits **0** (``Multiprocess.run``
  returns normally after ``terminate_all()``; uvicorn's own
  ``sys.exit(STARTUP_FAILURE)`` is gated on ``config.workers == 1``). So a deploy
  wrapper keyed on the exit code sees a clean shutdown — what blocks the deploy
  is nothing serving (the health check), plus ``entrypoint.sh``'s own refusal and
  the deploy-blocking healthz check.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tortoise.config import SUPPORTED_URI_SCHEMES
from tortoise.env_truthy import is_truthy

logger = logging.getLogger(__name__)

#: Set by ``entrypoint.sh``: the worker count uvicorn WILL run, measured with
#: uvicorn's own precedence.
#:
#: ⛔ NOT an input to UVICORN — it never reads this name, so hand-setting it
#: cannot raise the count the server runs. It IS however the FIRST input to
#: :func:`resolve_effective_workers`, which trusts the boot script's measurement
#: over re-deriving it, and the pre-exec gate in ``entrypoint.sh`` reads the same
#: value. So a stale or hand-exported value does not change what uvicorn runs,
#: but it CAN make this module refuse a boot that is really one worker (the count
#: it reports is then a declaration the machine contradicts), and on an embedded
#: DB that refusal is not liftable by :data:`ACK_ENV`. Keep this variable an
#: output: nothing but ``entrypoint.sh`` should set it.
WORKERS_ENV = "TORTOISE_WEB_WORKERS"

#: The explicit acknowledgement that this deployment has landed the
#: multi-worker guards. Unset (the default) makes ``workers > 1`` fatal.
ACK_ENV = "TORTOISE_MULTI_WORKER"

#: ``WEB_CONCURRENCY`` is uvicorn's *default source* for ``--workers``. It is
#: read here only to RECOGNISE a topology, never to authorise one.
WEB_CONCURRENCY_ENV = "WEB_CONCURRENCY"

#: ``--workers`` is uvicorn's CLI spelling. Two token forms, because a
#: hand-edited CMD usually carries ``--workers=N``.
WORKERS_FLAG = "--workers"

#: uvicorn's reload flag. ``--reload`` and ``--workers`` are mutually exclusive
#: (uvicorn warns that ``workers`` is IGNORED, and ``main.py`` branches
#: ``should_reload`` before ``workers > 1``), so its presence means the supervisor
#: parent is the RELOADER and the worker count is 1.
RELOAD_FLAG = "--reload"

#: click's ``context_settings={"auto_envvar_prefix": "UVICORN"}``
#: (``uvicorn/main.py``) makes this variable a real input for ``--reload`` — so
#: uvicorn's reloader can start with NO reload TOKEN on the command line.
RELOAD_ENV = "UVICORN_RELOAD"

#: click's TRUE vocabulary for a BOOLEAN flag, transcribed from ``BoolParamType``
#: (click 8.4.2 — the pinned version, ``uv.lock``; ``click/types.py:761-779``,
#: ``str_to_bool`` at ``:797-810`` strips and lowercases). `--reload` is
#: ``@click.option(..., is_flag=True)`` (``uvicorn/main.py``), so ``$UVICORN_RELOAD``
#: is converted by exactly this type — ``y`` and ``t`` included.
#:
#: ⛔ This is uvicorn's grammar for uvicorn's flag, NOT the repo's
#: :data:`tortoise.env_truthy.TRUTHY` contract. That contract is frozen by
#: ``tests/test_env_truthy.py`` and is deliberately narrower
#: (``{"1","true","yes","on"}``); widening it to reach these spellings would
#: silently change every other flag in the tree. The flag uvicorn actually parses
#: gets uvicorn's vocabulary, locally — and
#: ``tests/test_worker_topology.py::test_the_reload_grammar_is_clicks_own`` pins
#: this set against click's ``bool_states`` so the two cannot drift apart.
_CLICK_TRUE: frozenset[str] = frozenset({"1", "true", "t", "yes", "y", "on"})

#: The SAME click mechanism, for ``--workers``: this variable is the flag's
#: value whenever the flag is not given on the command line, which means uvicorn
#: never consults ``$WEB_CONCURRENCY`` (``config.py:352`` reads it only ``if
#: workers is None``, i.e. only when the *parameter* was not supplied). Measured
#: on the pinned 0.52.1 — see the module docstring's fact 1.
UVICORN_WORKERS_ENV = "UVICORN_WORKERS"

#: Every guard #1677 identifies as unlanded. This tuple IS the ledger: a lane
#: that lands one deletes its row, and the deletion is the review artifact.
#: Each row names the surface and the issue section that owns it.
UNLANDED_GUARDS: tuple[str, ...] = (
    "daemons (P1-2): the _lifespan BackupWatcher files GitHub issues and sends "
    "Telegram, and the hourly event-retention loop runs too — both arm once PER "
    "WORKER, so their non-idempotent side effects fire N times. Awaiting the "
    "owner's A/B decision recorded on #1677.",
    "_GITHUB_STATES (P1-C): the GitHub OAuth CSRF register is an in-memory dict, "
    "so an onboarding callback lands on a worker that never saw the state and "
    "answers 404 — onboarding CSRF-fails intermittently at N>=2.",
    "metrics: GoldenSignals/collect_signals compute P99, throughput, error-rate "
    "and saturation PER PROCESS, so no process holds the service-wide numbers.",
    "healthz: the 9090 listener binds on exactly one worker; every other "
    "worker's bind failure is swallowed unless TORTOISE_HEALTHZ_REQUIRED=1, and "
    "fly.toml marks that check deploy-blocking by design.",
    "pools: each worker opens its own FalkorDB Cloud connection pool and its own "
    "JWKS cache — N x the managed client budget and N independent IdP refreshes.",
)


class MultiWorkerRefused(RuntimeError):
    """Raised at startup when this process may not be one of N>1 workers."""


def _positive_int(raw: object) -> int | None:
    """``raw`` as a positive int, or None when it does not parse as one.

    ONE grammar, shared with ``entrypoint.sh``'s ``_resolve_requested_workers``
    (which executes before this package is importable, so the spelling is
    duplicated by necessity — ``_AGREEMENT_TABLE`` in
    ``tests/test_worker_topology.py`` EXECUTES both on the same input so they
    cannot drift): surrounding ASCII whitespace, an optional single leading
    ``+``, then ASCII digits, with ``_`` separators allowed BETWEEN digits.
    ``int(' 2 ')``, ``int('+2')`` and ``int('1_0')`` really do start 2/2/10
    workers, so a resolver that rejected them would report a topology nobody is
    running.

    ⛔ The separator rule is ``int()``'s own (PEP 515): ``1_0`` is TEN, while a
    leading, trailing or doubled ``_`` makes ``int()`` raise, so those stay None
    here and normalise to 1 in the shell — agreement, in the direction that
    reports 1 for input uvicorn refuses to start at all.

    ⚠️ DECLARED RESIDUAL, shared with the shell: ``int()`` ALSO accepts non-ASCII
    decimal digits (``int('١٢') == 12``), which bash arithmetic cannot evaluate
    and which ``isascii()`` therefore rejects on this side too. Both resolvers
    report **1** for that spelling while uvicorn would run 12. The direction is
    under-report, which cannot open the gate — a real ``N > 1`` still runs under
    uvicorn's supervisor, and ``has_supervisor_parent()`` refuses it whatever the
    count reads — and it is unreachable except by an operator deliberately typing
    Unicode digits. Closing it would mean porting ``int()``'s Unicode tables into
    shell to buy a correct number for a value nobody writes.

    None — never a default — so a malformed value cannot be silently read as 1;
    callers fall through to the next source only on None.
    """
    if raw is None:
        return None
    value = str(raw).strip(" \t\n\r\v\f")
    if value.startswith("+"):
        value = value[1:]
    # ASCII digits only, with ``_`` allowed BETWEEN digits (``int()``'s PEP 515
    # rule). ``str.isdigit`` accepts Unicode decimals (``'١'``), so ``isascii``
    # is the arm that keeps this equal to the shell's ``[!0-9]``.
    body = value.replace("_", "")
    if not value or not body or not body.isascii() or not body.isdigit():
        return None
    if value[0] == "_" or value[-1] == "_" or "__" in value:
        return None
    count = int(body)
    return count if count > 0 else None


def _workers_flag_value(argv: Sequence[str] | None = None) -> str | None:
    """The RAW ``--workers`` value given on a uvicorn command line, or None.

    Handles both token forms (``--workers N`` and ``--workers=N``) and tolerates
    a repeated flag the way a CLI last-wins parse does.

    ⛔ None means the flag was **not given a value** — absent entirely, or
    present with an empty one (``--workers=``, or a trailing ``--workers``).
    That is deliberately NOT the same as "given a value that is not a positive
    int": ``--workers 0`` and ``--workers -2`` ARE given, and uvicorn treats them
    as final (see :func:`resolve_effective_workers`). This raw form exists so the
    resolver can tell those two apart; :func:`workers_from_argv` folds both into
    None for callers that only want the count.

    Empty counts as absent because that is what ``entrypoint.sh`` does (its
    ``[ -z "$_count" ]`` falls through to ``$WEB_CONCURRENCY``), and the two
    resolvers must agree. uvicorn itself errors out on an empty ``--workers``
    (click ``type=int``), so no running topology is described either way.
    """
    args = list(sys.argv if argv is None else argv)
    found: str | None = None
    index = 0
    while index < len(args):
        token = args[index]
        if token == WORKERS_FLAG:
            # ⛔ The NEXT token is the VALUE whatever it looks like, and it is
            # CONSUMED — never re-scanned as a flag. That is what the shell arm
            # does (`_count="${_args[$_i]:-}"`, then it steps past), so a
            # value that itself looks like a flag (`--workers --workers=`) is ONE
            # malformed value in both resolvers instead of "flag, then flag" in
            # one. A trailing bare flag reads EMPTY (absent), which is the
            # shell's `-z` fall-through to `$WEB_CONCURRENCY`. click exits 2 on
            # both shapes, so no topology exists to describe — but the two
            # resolvers must still agree, because `_AGREEMENT_TABLE` runs both.
            found = args[index + 1] if index + 1 < len(args) else ""
            index += 2
            continue
        if token.startswith(WORKERS_FLAG + "="):
            found = token[len(WORKERS_FLAG) + 1:]
        index += 1
    return found if found else None


def workers_from_argv(argv: Sequence[str] | None = None) -> int | None:
    """The ``--workers`` value on a uvicorn command line, or None if not given.

    Returns None — not 1 — when the flag is absent, so a caller can tell "not
    requested" from "requested 1". It also returns None when the flag IS given
    but its value is not a positive int (``--workers 0`` / ``--workers -2`` /
    ``--workers oops``), which is why :func:`resolve_effective_workers` does NOT
    use it to decide the env fall-through — do not read this None as "absent".
    """
    raw = _workers_flag_value(argv)
    return None if raw is None else _positive_int(raw)


def resolve_effective_workers(environ: Mapping[str, str] | None = None,
                              argv: Sequence[str] | None = None) -> int:
    """The worker count uvicorn will run, by uvicorn's own precedence.

    ``TORTOISE_WEB_WORKERS`` (measured by ``entrypoint.sh`` from the CMD argv —
    authoritative, and already folded the CLI in) wins; then a CLI ``--workers``
    on this process's own argv; then ``$UVICORN_WORKERS`` (click's
    ``auto_envvar_prefix`` value for that flag); then ``$WEB_CONCURRENCY``
    (uvicorn's own default source); then 1.

    ⛔ ``$UVICORN_WORKERS`` is FINAL in the same way a given flag is — it IS the
    flag's value as far as uvicorn is concerned, so ``$WEB_CONCURRENCY`` is never
    reached when it is set. Measured on the pinned 0.52.1:
    ``UVICORN_WORKERS=2 WEB_CONCURRENCY=4`` starts TWO children, and
    ``UVICORN_WORKERS=0 WEB_CONCURRENCY=4`` starts one in-process server. Reading
    the env var as a mere tiebreak under ``$WEB_CONCURRENCY`` would under-report
    a machine running N as 1 — this resolver's whole job is not to.

    ⛔ A GIVEN ``--workers`` is FINAL — including ``--workers 0`` and
    ``--workers -2``, which uvicorn resolves to ONE in-process worker and does
    NOT resolve against ``$WEB_CONCURRENCY`` (``config.py:352`` reads the env var
    only ``if workers is None``, i.e. only when the flag's *parameter* was not
    supplied). This resolver previously fell through to ``$WEB_CONCURRENCY`` for
    those values, because the value and the absence were both ``None`` — so
    ``--workers 0`` with ``WEB_CONCURRENCY=4`` exported answered 4 here while
    ``entrypoint.sh`` (correctly), uvicorn, and the machine all answered 1: a
    false refusal that misdescribed the topology it observed. The flag's RAW
    presence decides, its value only names the count.

    A non-positive or unparseable value is normalised to 1 — uvicorn runs one
    process for ``0``/``-N`` and rejects anything else outright, and 1 is what
    the shipped shell block reports, so the two resolvers cannot disagree.

    ⚠️ Returns a **recognition** of the topology, not an authorisation: see
    :func:`refusal_reason` for the policy.
    """
    env = os.environ if environ is None else environ
    declared = _positive_int(env.get(WORKERS_ENV))
    if declared is not None:
        return declared
    flag_value = _workers_flag_value(argv)
    if flag_value is not None:
        return _positive_int(flag_value) or 1
    env_flag_value = _uvicorn_workers_env_value(env)
    if env_flag_value is not None:
        return _positive_int(env_flag_value) or 1
    return _positive_int(env.get(WEB_CONCURRENCY_ENV)) or 1


def _uvicorn_workers_env_value(environ: Mapping[str, str] | None = None) -> str | None:
    """The RAW ``$UVICORN_WORKERS``, or None when click would treat it as absent.

    click's ``auto_envvar_prefix`` lookup ignores an EMPTY value — measured on
    the pinned 0.52.1: ``UVICORN_WORKERS='' WEB_CONCURRENCY=4`` starts four
    workers, i.e. an empty env var is exactly the ``-z`` case the flag also has,
    and uvicorn falls through to ``$WEB_CONCURRENCY``. Returning the raw string
    (not a count) is deliberate for the same reason as
    :func:`_workers_flag_value`: a GIVEN ``0``/``-2`` is final and must not be
    confused with absence. A non-empty but unparseable value is uvicorn's own
    hard error (click: ``'oops' is not a valid integer``), so no topology exists
    to describe and the callers normalise it to 1.
    """
    env = os.environ if environ is None else environ
    raw = env.get(UVICORN_WORKERS_ENV)
    return raw if raw else None


def reload_is_requested(argv: Sequence[str] | None = None) -> bool:
    """True when this process's argv carries uvicorn's ``--reload``.

    Matched as an exact token: ``--reload-dir`` / ``--reload-include`` /
    ``--reload-exclude`` / ``--reload-delay`` are modifiers that only mean
    anything alongside ``--reload`` itself, so a substring test would report a
    reload for a run that is not one.

    Under spawn the child's ``sys.argv`` is the parent's command line
    (multiprocessing restores it), so this answers for the child too — measured.
    """
    args = sys.argv if argv is None else argv
    return RELOAD_FLAG in list(args)


def reload_env_is_set(environ: Mapping[str, str] | None = None) -> bool:
    """True when ``$UVICORN_RELOAD`` asks uvicorn for its reloader.

    click's ``auto_envvar_prefix="UVICORN"`` makes this a real input for the
    ``--reload`` flag, so the reloader starts with NO reload TOKEN anywhere on the
    command line. Measured on the pinned uvicorn 0.52.1:
    ``UVICORN_RELOAD=true python -m uvicorn app:app`` DOES start the reloader, and
    the served child's ``sys.argv`` carries no ``--reload``. A token-only check
    therefore read that child as a supervised multi-worker and refused it — the
    same unstartable-dev-topology defect this module already fixed for the token
    form, by a narrower route.

    The vocabulary is click's own :data:`_CLICK_TRUE`, because that is the type
    uvicorn parses this variable with. ⛔ Reading it through the repo's narrower
    ``is_truthy`` — the obvious-looking choice, and this branch's behaviour
    before the fix — refused a one-worker reloader started with
    ``UVICORN_RELOAD=y`` (click accepts ``y`` and ``t``; ``is_truthy`` does not),
    which is an unstartable dev topology on the embedded arm where the
    acknowledgement does not lift the refusal: the exact defect this module
    already fixed twice, reached through the vocabulary instead of the source.

    A value OUTSIDE click's vocabulary (``enabled``, ``yess``) is a value uvicorn
    rejects outright, so it reads OFF and the child stays REFUSED (fail-closed):
    only an explicit acknowledgement of the reloader changes the verdict.
    """
    env = os.environ if environ is None else environ
    raw = env.get(RELOAD_ENV)
    return ("" if raw is None else str(raw)).strip().lower() in _CLICK_TRUE


def has_supervisor_parent() -> bool:
    """True iff SOME uvicorn supervisor spawned this process.

    ⛔ That includes the ``--reload`` reloader, which is always ONE worker. This
    is therefore evidence of "I am supervised", never of "I am one of N" — the
    multi-worker verdict requires the count or the absence of ``--reload``. Read
    :func:`is_multi_worker` rather than this predicate directly.
    """
    return multiprocessing.parent_process() is not None


def _uri_names_a_shared_backend(value: str | None) -> bool:
    """True when ``value`` is a URI with a scheme this repo routes to a server.

    Deliberately the SAME scheme set the connection layer validates
    (``tortoise.config.SUPPORTED_URI_SCHEMES``), so "is the DB shared" cannot
    drift from "will this URI be dialled". A value without ``://`` is a
    filesystem path (backward compat in ``config.py``) — i.e. embedded.
    """
    if not value:
        return False
    scheme, sep, rest = str(value).partition("://")
    return bool(sep) and bool(rest.strip()) and scheme.strip().lower() in SUPPORTED_URI_SCHEMES


def db_is_shared(environ: Mapping[str, str] | None = None) -> bool:
    """True when a shared (server) DB is resolvable, i.e. NOT embedded mode.

    ⛔ THIS MIRRORS THE APP'S OWN RESOLUTION — ONE variable, no fallback.
    ``hosted_api._make_sdk`` dispatches on ``TORTOISE_DB_URI`` alone (URI mode
    when it is truthy, else ``_resolve_embedded_db_path()``), and ``config.py``
    routes a value with a supported scheme to the server while anything else is
    a file path. Nothing under ``tortoise/`` reads ``FALKORDB_CLOUD_URI``.

    ``FALKORDB_CLOUD_URI``'s only role is ``entrypoint.sh``'s PROMOTION —
    ``export TORTOISE_DB_URI="${FALKORDB_CLOUD_URI}"`` — which happens before
    ``exec``, so a boot that came through the boot script always has
    ``TORTOISE_DB_URI`` set and needs no fallback here.

    An earlier revision consulted ``FALKORDB_CLOUD_URI`` when
    ``TORTOISE_DB_URI`` was empty, "for a direct ``uvicorn`` invocation where no
    promotion ran". That arm could only ever answer "shared" for a boot that is
    in fact EMBEDDED: the promotion is unobservable from the app, so a stale
    cloud secret plus ``uvicorn tortoise.hosted_api:app --workers 2`` read as
    shared while ``_make_sdk`` opened the embedded file. ``shared_db`` is the
    single load-bearing boolean between the acknowledgement and a permitted
    multi-worker boot, so a false "shared" lets ``TORTOISE_MULTI_WORKER`` reach
    the embedded arm it is documented never to reach — N workers on N divergent
    copies of one file. **Deleted, not narrowed:** there is no input on which
    the fallback agrees with the app, so there is nothing to keep it in step
    with.

    The emptiness test is the app's own truthiness test, and it is the whole
    rule here: ``TORTOISE_DB_URI=""`` is embedded in ``_make_sdk`` too, and
    ``" "`` has no supported scheme, so both answer embedded for it.
    """
    env = os.environ if environ is None else environ
    return _uri_names_a_shared_backend(env.get("TORTOISE_DB_URI"))


def ack_is_set(environ: Mapping[str, str] | None = None) -> bool:
    """True when ``TORTOISE_MULTI_WORKER`` is set to a truthy value.

    Truthiness is the repo's declared contract (``tortoise.env_truthy``), so
    ``0``/``false``/``no``/``off`` AND a typo all read "not acknowledged"
    rather than "a non-empty string is set" — a security acknowledgement must
    fail closed on a typo, not open.
    """
    env = os.environ if environ is None else environ
    return is_truthy(env.get(ACK_ENV))


@dataclass(frozen=True)
class Topology:
    """The measured worker topology of THIS process, plus this deployment's facts."""

    workers: int
    supervised: bool
    reloading: bool
    shared_db: bool
    acknowledged: bool

    @property
    def is_multi_worker(self) -> bool:
        """True when this process is one of N>1 workers.

        **The reload arm is checked FIRST, and that order is load-bearing.**
        uvicorn's own dispatch does the same: ``main.py`` branches on
        ``should_reload`` BEFORE ``workers > 1``, and prints ``"workers" flag is
        ignored when reloading is enabled``. Measured on the pinned 0.52.1:
        ``--reload --workers 4`` starts the reloader and exactly ONE served
        child. So a reloading process is single-worker even when the command line
        names a count, and putting the count first would refuse a topology
        uvicorn is not running — with a message ("N>=4 … on an EMBEDDED
        database") that is simply false, and on the embedded arm that is the
        unstartable-with-no-escape defect this module already fixed once.

        Otherwise ``workers > 1`` is the direct answer; the supervised-parent arm
        covers the case the count cannot express — ``uvicorn.run(workers=N)`` from
        a script, where neither argv nor ``$WEB_CONCURRENCY`` carries N — and is
        suppressed for a reload child, which is always exactly one worker.
        """
        if self.reloading:
            return False
        return self.workers > 1 or self.supervised


def observe(environ: Mapping[str, str] | None = None,
            *, supervised: bool | None = None,
            argv: Sequence[str] | None = None) -> Topology:
    """Measure the topology.

    ``supervised`` and ``argv`` are injectable so the policy is testable without
    forking the test process; production reads the live ones.
    """
    return Topology(
        workers=resolve_effective_workers(environ, argv),
        supervised=has_supervisor_parent() if supervised is None else supervised,
        reloading=reload_is_requested(argv) or reload_env_is_set(environ),
        shared_db=db_is_shared(environ),
        acknowledged=ack_is_set(environ),
    )


def _guard_list() -> str:
    return "\n".join(f"  [{i}] {row}" for i, row in enumerate(UNLANDED_GUARDS, 1))


def refusal_reason(topology: Topology) -> str | None:
    """Why this topology may not run, or None when it may.

    Pure function of a measured :class:`Topology` — no environment reads — so
    every branch is directly testable.
    """
    if not topology.is_multi_worker:
        return None
    if not topology.shared_db:
        # NOT reachable by TORTOISE_MULTI_WORKER, deliberately: divergent
        # embedded copies are corruption, not a degraded feature.
        # The count is named only when it was actually observed — the
        # supervised-with-count-1 shape must not be described as "N>=2".
        if topology.workers > 1:
            observed = f"N>={topology.workers} uvicorn workers"
        else:
            observed = "more than one uvicorn worker (a supervisor child was detected)"
        return (
            f"Refusing to run {observed} on an EMBEDDED database.\n"
            "No shared TORTOISE_DB_URI resolves, so each worker would open its "
            "own embedded redislite file and the copies would DIVERGE — silent "
            "data divergence, not a degraded feature, so "
            f"{ACK_ENV} does not lift this refusal.\n"
            "Fix: set TORTOISE_DB_URI to the shared instance (the boot script "
            "promotes FALKORDB_CLOUD_URI into it before exec, so a direct "
            "`uvicorn` invocation must set TORTOISE_DB_URI itself), or run a "
            f"single worker (unset {WEB_CONCURRENCY_ENV} / drop --workers)."
        )
    if not topology.acknowledged:
        # `supervised` can be true while the count resolves to 1 — that is the
        # programmatic-`workers=N` case, and the message must not claim a count
        # it never observed.
        if topology.workers > 1:
            observed = f"N>={topology.workers} uvicorn workers"
        else:
            observed = (
                "a uvicorn supervisor child whose SERVED worker count is not "
                "readable here (no --workers token, and no $WEB_CONCURRENCY "
                "value this resolver can read) — so it is treated as N>=2"
            )
        return (
            f"Refusing to run {observed}: this "
            "topology is not supported yet and its guards are unlanded (#1677).\n"
            f"{_guard_list()}\n"
            f"Set {ACK_ENV}=1 to acknowledge these residuals and run anyway "
            "(they are re-listed as a WARNING on every worker's boot), or run a "
            f"single worker by unsetting {WEB_CONCURRENCY_ENV} / dropping "
            "--workers."
        )
    return None


def assert_multi_worker_admissible(environ: Mapping[str, str] | None = None,
                                   *, supervised: bool | None = None,
                                   argv: Sequence[str] | None = None) -> Topology:
    """Fail closed unless THIS process may be one of N>1 workers.

    Returns the measured :class:`Topology` on the permitted path (so a caller
    can log it). Raises :class:`MultiWorkerRefused` otherwise — from the
    startup half of ``_lifespan`` that is a uvicorn ``STARTUP_FAILURE``, which
    terminates the whole parent rather than leaving a machine that half-works.
    """
    topology = observe(environ, supervised=supervised, argv=argv)
    reason = refusal_reason(topology)
    if reason is not None:
        raise MultiWorkerRefused(reason)

    if topology.is_multi_worker:
        # Permitted only via the acknowledgement — say so EVERY boot, so an
        # acknowledged-but-risky topology never becomes invisible folklore.
        logger.warning(
            "multi-worker topology is ACKNOWLEDGED but its guards are still "
            "unlanded (#1677) — workers=%s shared_db=%s:\n%s",
            topology.workers, topology.shared_db, _guard_list(),
        )
    return topology


# --- self-check ---
if __name__ == "__main__":
    _shared = {"TORTOISE_DB_URI": "rediss://host:1/db"}
    _embedded: dict[str, str] = {}
    # The probe app's real command line, so argv is exercised for real.
    _argv2 = ["uvicorn", "app:app", "--host", "0.0.0.0", "--workers", "2"]

    # uvicorn precedence: the entrypoint's measurement beats argv beats
    # WEB_CONCURRENCY beats 1.
    assert resolve_effective_workers({}, []) == 1
    assert resolve_effective_workers({"WEB_CONCURRENCY": "3"}, []) == 3
    assert resolve_effective_workers({"WEB_CONCURRENCY": "3"}, _argv2) == 2
    assert resolve_effective_workers({"WEB_CONCURRENCY": "3", WORKERS_ENV: "2"}, _argv2) == 2
    assert resolve_effective_workers({"WEB_CONCURRENCY": "oops"}, []) == 1
    # int()'s grammar, because that is what uvicorn applies.
    assert resolve_effective_workers({}, ["uvicorn", "a:a", "--workers", "+2"]) == 2
    assert resolve_effective_workers({"WEB_CONCURRENCY": " 2 "}, []) == 2
    # $UVICORN_WORKERS is click's value for the flag, so it is FINAL over
    # $WEB_CONCURRENCY and loses only to the CLI (measured on the pinned 0.52.1:
    # 2 children vs 2 vs 1).
    assert resolve_effective_workers({UVICORN_WORKERS_ENV: "2"}, []) == 2
    assert resolve_effective_workers(
        {UVICORN_WORKERS_ENV: "2", "WEB_CONCURRENCY": "4"}, []) == 2
    assert resolve_effective_workers(
        {UVICORN_WORKERS_ENV: "2"}, ["uvicorn", "a:a", "--workers", "1"]) == 1
    assert resolve_effective_workers(
        {UVICORN_WORKERS_ENV: "0", "WEB_CONCURRENCY": "4"}, []) == 1
    assert resolve_effective_workers(
        {UVICORN_WORKERS_ENV: "", "WEB_CONCURRENCY": "4"}, []) == 4
    assert workers_from_argv(["uvicorn", "a:a"]) is None

    # A GIVEN --workers is final, even non-positive: $WEB_CONCURRENCY is only the
    # flag's default, so it must not answer for a flag that already overruled it
    # (measured: Config(workers=0).workers == 1 and Config(workers=-2).workers ==
    # -2 with WEB_CONCURRENCY=4 exported — one in-process worker either way).
    assert resolve_effective_workers(
        {"WEB_CONCURRENCY": "4"}, ["uvicorn", "a:a", "--workers", "0"]) == 1
    assert resolve_effective_workers(
        {"WEB_CONCURRENCY": "4"}, ["uvicorn", "a:a", "--workers", "-2"]) == 1
    # …but an EMPTY value is the shell's `-z` case, so the env still answers.
    assert resolve_effective_workers(
        {"WEB_CONCURRENCY": "4"}, ["uvicorn", "a:a", "--workers="]) == 4

    # reload is ONE worker even though it is supervised.
    assert reload_is_requested(["uvicorn", "a:a", "--reload"])
    assert not reload_is_requested(["uvicorn", "a:a", "--reload-dir", "src"])
    # …and uvicorn's OTHER input for it is $UVICORN_RELOAD, which leaves no token.
    assert reload_env_is_set({RELOAD_ENV: "true"})
    assert not reload_env_is_set({RELOAD_ENV: "enabled"})  # typo -> fail closed
    assert refusal_reason(
        observe({RELOAD_ENV: "1"}, supervised=True, argv=["uvicorn", "a:a"])) is None

    # embedded + N>=2 is refused and NOT liftable by the acknowledgement.
    assert refusal_reason(observe(_embedded, supervised=False, argv=[])) is None
    assert refusal_reason(
        observe({**_embedded, ACK_ENV: "1"}, supervised=True, argv=[])) is not None
    # A --reload child on an embedded DB is a ONE-worker topology: not gated.
    assert refusal_reason(
        observe(_embedded, supervised=True, argv=["uvicorn", "a:a", "--reload"])) is None
    # …and reload wins over a named count, because uvicorn ignores it (measured:
    # `--reload --workers 4` serves exactly one child).
    assert refusal_reason(observe(
        _embedded, supervised=True,
        argv=["uvicorn", "a:a", "--reload", "--workers", "4"])) is None
    # shared + N>=2 needs the acknowledgement.
    assert refusal_reason(observe({**_shared}, supervised=False, argv=_argv2)) is not None
    assert refusal_reason(
        observe({**_shared, ACK_ENV: "1"}, supervised=False, argv=_argv2)) is None
    # a supervised child whose count reads 1 still refuses (programmatic workers).
    assert refusal_reason(observe({**_shared}, supervised=True, argv=[])) is not None

    # The DB source is the APP's chain: one variable, no cloud fallback — the
    # promotion lives in entrypoint.sh and is unobservable from here, so a cloud
    # URI alone is an EMBEDDED boot (measured against hosted_api._make_sdk).
    assert not db_is_shared({"TORTOISE_DB_URI": "/data/tortoise.db",
                              "FALKORDB_CLOUD_URI": _shared["TORTOISE_DB_URI"]})
    assert not db_is_shared({"TORTOISE_DB_URI": "",
                             "FALKORDB_CLOUD_URI": _shared["TORTOISE_DB_URI"]})
    assert not db_is_shared({"FALKORDB_CLOUD_URI": _shared["TORTOISE_DB_URI"]})
    assert refusal_reason(observe(
        {"TORTOISE_DB_URI": "/data/tortoise.db",
         "FALKORDB_CLOUD_URI": _shared["TORTOISE_DB_URI"], ACK_ENV: "1"},
        supervised=False, argv=_argv2)) is not None
    # …and the cloud-only boot refuses too: the app would be embedded.
    assert refusal_reason(observe(
        {"FALKORDB_CLOUD_URI": _shared["TORTOISE_DB_URI"], ACK_ENV: "1"},
        supervised=False, argv=_argv2)) is not None

    print("✅ worker_topology")

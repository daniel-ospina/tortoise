"""The alert-channel POLICY and CONSTRUCTOR — the light half of the seam (#3981).

One policy, one constructor, two legs:

* ``hosted_api._incident_alert_store()`` is the hosted app's leg. It injects
  ITS OWN factories (``_backup_config_safe``, ``_backup_storage``) so every
  test patch point in that module keeps working, and so the hosted app keeps
  the cache-keyed R2 singleton its per-request callers need (#3968).
* ``operator_alert.alert_store()`` is the lane that must NOT import
  ``hosted_api``. Building the FastAPI app costs ~1.7 s
  (``tortoise/mcp_server.py:31-35``), and an operator alert fires on the MCP
  stdio path, where that import would be pure cost for a bookkeeping alert.
  It therefore takes the hosted leg only when ``tortoise.hosted_api`` is
  ALREADY in ``sys.modules`` (it never imports it), and otherwise calls
  :func:`incident_alert_store` with the light defaults here — so the stdio path
  never pays the import and a hosted process never pays a per-call
  ``R2Storage`` (#3968).

The policy itself — "build the channel from the ALERT credentials, NEVER from
the backup-sweep gate (#3820 D5a)" — lives in :func:`incident_alert_store`, so
the two legs cannot drift. The D5a rationale and the seam map are documented on
``hosted_api._incident_alert_store``, which is the name a reader of that module
finds; this module is where the code both legs execute.

Nothing here is imported by ``hosted_api`` at module scope, and nothing here
imports ``hosted_api`` even lazily.
"""

from __future__ import annotations

import logging
import os
import threading

_logger = logging.getLogger("tortoise.alert_channel")

#: Process-wide MemoryStorage for the light leg (the test/selfhost seam). The
#: hosted leg keeps its own singleton on its own module (`hosted_api._MEMORY_BACKUP_STORE`),
#: because its storage builder is a patched module global there.
_MEMORY_STORAGE = None
_MEMORY_LOCK = threading.Lock()


def sweep_config_safe():
    """Sweep config, or ``None`` when disabled/invalid (fail-closed).

    The sweep switch decides whether backups RUN. It must never decide whether
    an incident is VISIBLE (#3820 D5a) — that is :func:`incident_alert_store`'s
    job: this only supplies the sweep config when there IS one.
    """
    from tortoise.backup_config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as e:
        _logger.warning("backup sweep config invalid: %s", e)
        return None
    return cfg if cfg.enabled else None


def light_storage():
    """Object store for the alert channel when the hosted app is not loaded.

    No process-wide R2 cache on this leg: it is reached only for a throttled
    alert (≤1 attempt per window per kind/org) in a process where
    ``tortoise.hosted_api`` is NOT imported (the light leg is the fallback;
    the hosted process resolves through ``hosted_api._incident_alert_store``,
    which keeps its cache-keyed R2 singleton, #3968). ``R2Storage()`` raises
    when the four ``R2_*`` vars are missing — the caller turns that into
    ``None`` plus a WARNING, which is the documented D6 residual.

    The memory seam is REFUSED on Fly (#101 incident class): memory alert-dedup
    state vanishes on restart, which is the silent-data-loss mode the #101
    postmortem documents. ``hosted_api`` enforces the same rule at import
    time; it is enforced here PER CALL as well, because this leg can be
    reached in a process that never imported ``hosted_api`` at all.

    The raise does not escape to the caller: :func:`incident_alert_store`
    catches it, so the EFFECT on Fly is "no alert channel, plus a WARNING naming
    this reason" — never a silently-accepted in-memory dedup store, and never a
    raise on the alert path (whose whole contract is that it cannot raise).
    """
    global _MEMORY_STORAGE
    mode = os.environ.get("TORTOISE_BACKUP_STORAGE", "").strip().lower()
    if mode == "memory":
        if os.environ.get("FLY_APP_NAME"):
            raise RuntimeError(
                "TORTOISE_BACKUP_STORAGE=memory is a test seam and refuses to "
                "run on Fly (FLY_APP_NAME set) — alert dedup state is lost on "
                "restart"
            )
        with _MEMORY_LOCK:
            if _MEMORY_STORAGE is None:
                from tortoise.hosted_backup import MemoryStorage
                _logger.warning(
                    "TORTOISE_BACKUP_STORAGE=memory — alert dedup state lives "
                    "in process memory only (test seam)")
                _MEMORY_STORAGE = MemoryStorage()
            return _MEMORY_STORAGE
    if mode:
        raise RuntimeError(
            f"TORTOISE_BACKUP_STORAGE={mode!r} unknown — use 'memory' or unset for R2")
    from tortoise.hosted_backup import R2Storage

    return R2Storage()


def reset_memory_storage_for_tests() -> None:
    """Drop the light leg's process-wide MemoryStorage singleton.

    The singleton dedups incidents ACROSS tests in one pytest process: a title
    filed by one test stays "already filed" for an unrelated test that happens
    to build the same kind/org, which is a latent DEDUP collision rather than a
    failure the test asked for. The ``_operator_alert_isolation`` autouse
    fixture calls this so every test starts with an empty dedup surface.
    """
    global _MEMORY_STORAGE
    with _MEMORY_LOCK:
        _MEMORY_STORAGE = None


def alert_store_from(cfg, *, storage_factory, writer: str | None = None):
    """Build the ``AlertStore`` from an already-loaded alert config.

    ``storage_factory`` is REQUIRED and injected: the hosted leg must pass its
    own ``hosted_api._backup_storage`` (a patched module global there, and the
    one holding the R2 singleton); the light leg passes :func:`light_storage`.
    ``cfg`` is dereferenced, so it must never be ``None``.

    ``writer`` is the identity the store's resolves act as (#3127/#2844),
    threaded from the caller — ``WRITER_APP`` by default, ``WRITER_WATCHER``
    where the watcher builds it. Passing it here rather than hardcoding is what
    keeps the ``KIND_OWNERS`` authority check armed on BOTH legs.
    """
    from tortoise import github_issue as gi
    from tortoise.alert_store import WRITER_APP, AlertStore
    from tortoise.telegram_push import send_message

    writer = WRITER_APP if writer is None else writer
    storage = storage_factory()

    def file_issue(title: str, body: str) -> int:
        return gi.create_issue(
            cfg.gh_repo, cfg.github_issues_pat, title=title, body=body,
            assignee=cfg.alert_assignee,
        )

    def close_issue(number: int, comment: str | None = None) -> None:
        gi.close_issue(cfg.gh_repo, cfg.github_issues_pat, number, comment)

    def search_open(kind: str, org_id: str = "") -> list[int]:
        return gi.search_open_incident(
            cfg.gh_repo, cfg.github_issues_pat, kind, org_id)

    def push_telegram(text: str) -> None:
        send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, text)

    def issue_open(number: int) -> bool:
        return gi.issue_is_open_checked(cfg.gh_repo, cfg.github_issues_pat, number)

    return AlertStore(
        storage, file_issue=file_issue, close_issue=close_issue,
        search_open=search_open, push_telegram=push_telegram,
        issue_open=issue_open, default_writer=writer,
        repo=cfg.gh_repo, assignee=cfg.alert_assignee,
    )


def incident_alert_store(*, config_safe=None, storage_factory=None,
                         writer: str | None = None):
    """THE alert-channel policy, or ``None`` when no channel can be built.

    Gated on the ALERT credentials only, NEVER on ``BACKUP_SWEEP_ENABLED``
    (#3820 D5a): the sweep switch decides whether backups RUN, never whether an
    incident is VISIBLE. When the sweep is enabled its config is used as-is;
    otherwise ``load_alert_config`` reads the alert credentials ungated.

    Returns ``None`` when there is no issue filer (``DR_ISSUES_PAT`` unset) or
    the object store cannot be built; the caller then keeps its log line /
    counter. Env-only: no network at construction.
    """
    if config_safe is None:
        config_safe = sweep_config_safe
    if storage_factory is None:
        storage_factory = light_storage
    try:
        cfg = config_safe()
        if cfg is None:
            from tortoise.backup_config import load_alert_config

            cfg = load_alert_config()
        if cfg is None:
            return None
        return alert_store_from(
            cfg, storage_factory=storage_factory, writer=writer)
    except Exception as e:  # absence of a channel is not a loss
        _logger.warning("incident alert store unavailable: %s", e)
        return None

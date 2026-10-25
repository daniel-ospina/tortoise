"""§9.4's verbatim-evidence fetch, served from the RECORD — R7 (#9555, epic #7879).

§9.4 (`docs/architecture/STORAGE-ARCHITECTURE.md`) makes the link between a
claim and its source **load-bearing**, not a citation nicety:

> **At answer time, send the source's VERBATIM text, not the extracted claim.**
> *(Measured: verbatim source beats LLM-extracted artefacts by 15.9 / 22.0 pts —
> extractor §2.4; `#3011` would settle it on our stack.)*

§9.1 places that text in Postgres — *"the narrative is not something we're
suggesting to store in the graph (same as raw) but store in supabase"* — and
`#9638` landed its home (`public.session_narrative`) plus the record-only reader
(`tortoise/record_read.py`). Measured for #9555, that reader had **no production
caller**: `git grep -n 'record_read import' -- tortoise/ tests/` found its own
test and nothing else. A fetch the answer path cannot reach is not a path.

This module is the caller, and ``python -m tortoise record-text`` is its
operator verb (a sibling of ``record-reconcile`` and ``record-restore``: same
DSN resolution, same exit-code discipline, same *"no verdict is not a pass"*
rule). It reads the **record only** — not the journal, not the graph — which is
what makes it usable in the one situation §9.4 exists for: *the graph is gone
and the evidence still has to come back*.

THE REFUSAL THAT MATTERS: "absent" is not "unreadable"
======================================================
A tenancy-filtered read returns **0 rows silently**. Under the app role (#9600,
R6) with no tenant GUC — or behind a transaction-mode pooler that dropped it —
the narrative table reads as empty for a session that HAS one (§9555's
integration note G1). Reporting that as *"the record holds no text for this
session"* would be the worst answer this verb could give: it is a false absence
about a customer's evidence, and it is indistinguishable from a mistyped
``--org``/``--graph``.

So a miss is PROVEN before it is reported: the verb installs the record's OWN
tenant prologue (:func:`scoped` → ``record_projection.scope_connection``), which
sets ``app.current_org_id`` transaction-locally, READS IT BACK, and refuses a
connection whose role can bypass RLS — and every refusal is
:data:`EXIT_UNAVAILABLE`, *no verdict*. ``EXIT_ABSENT`` therefore means the
tenant was provably scoped and holds no text for this key, which is the only
shape in which an absence is an answer.

⛔ An earlier draft read ``public.graphs`` as the scope proof. Measured in review:
that table is granted to NO record role, so as ``tortoise_record_app`` the verb
exited ``2`` on every call — and where it was readable it proved the wrong
predicate (the text tables' policy could still have filtered the text), so a
filtered read was still reported as an honest absence. The shared prologue is
the correct proof and needs no new grant.

WHAT THIS IS NOT
================
It does not write, reconcile, or restore — the write path stays
``record_writer``'s, the comparison ``record-reconcile``, the recovery
``record-restore``. It exposes no MCP/SDK surface: the hosted answer path's own
verbatim fetch is a surface change and belongs to whoever holds that decision
(`AGENTS.md` → *MCP/SDK Surface Approval*), not to a read verb landing here.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from tortoise import record_read
from tortoise.record_read import SessionText

__all__ = [
    "EXIT_ABSENT",
    "EXIT_SERVED",
    "EXIT_UNAVAILABLE",
    "LOG_NAME",
    "TextOutcome",
    "log_result",
    "scoped",
    "serve_from_record",
]

LOG_NAME = "tortoise.record_text"

#: The text was read from the record and is being served.
EXIT_SERVED = 0
#: No verdict was produced: no/invalid DSN, ``psycopg2`` absent, an unreadable
#: record — or a connection the TENANT PROLOGUE refuses (a role that bypasses
#: RLS; a tenant GUC that did not stick). Never reported as an absence.
#: ⚠️ A scope that does not EXIST is not this case: it scopes fine and reads
#: back fine, so its zero rows are an honest absence (:data:`EXIT_ABSENT`). The
#: distinction is the whole 3-versus-2 split.
EXIT_UNAVAILABLE = 2
#: The record was READABLE for this scope and holds no §9.1 text for the key.
#: An honest absence — the distinction from :data:`EXIT_UNAVAILABLE` is the
#: whole point of :func:`scoped`: the tenant prologue proves the connection was
#: scoped (and cannot bypass RLS) before any zero-row answer is called one.
EXIT_ABSENT = 3

#: How the key was resolved, so a served answer names its own door.
BY_SESSION = "session"
BY_SOURCE = "source-url"


@dataclass(frozen=True)
class TextOutcome:
    """What the record served for one key — the measurement, not a plan.

    ``turns`` is the count actually read (and is NOT capped by a display bound:
    ``shown`` says how many were rendered, so ``--limit`` can never make a
    served answer understate the transcript it holds).
    """

    key: str
    resolved_by: str
    session: SessionText | None = None
    shown: int = 0
    limit: int | None = None
    #: For the ``source-url`` door: did the LINK resolve to a session id?
    #: ``None`` on the ``session`` door (there is no link to resolve) and on any
    #: outcome built before the link was probed — deliberately NOT ``True``, so
    #: the default can never narrate a resolution that did not happen.
    source_linked: bool | None = None
    #: The session id the link resolved to, when it did — the second signal
    #: ``--json`` needs, since ``session_id`` is ``None`` for every miss.
    linked_session_id: str | None = None

    @property
    def served(self) -> bool:
        return self.session is not None

    @property
    def turns(self) -> int:
        return len(self.session.turns) if self.session is not None else 0

    def as_dict(self) -> dict[str, Any]:
        sess = self.session
        return {
            "key": self.key,
            "resolved_by": self.resolved_by,
            "served": self.served,
            # ⚠️ `session_id` below is None for EVERY miss, so a miss is only
            # diagnosable if the link's own state travels with it (PR #9862
            # review, P2: "there is no second signal — `--json` cannot tell them
            # apart either").
            "source_linked": self.source_linked,
            "linked_session_id": self.linked_session_id,
            "session_id": sess.session_id if sess is not None else None,
            "narrative": sess.narrative if sess is not None else None,
            "summary": sess.summary if sess is not None else None,
            # §3: the column keeps the JOURNAL key's name (`capture_redactions`)
            # and this is R4's count (the one R7's indicator (b) must reproduce).
            "redactions": sess.redactions if sess is not None else None,
            "turn_count": sess.turn_count if sess is not None else None,
            "turns": self.turns,
            "shown": self.shown,
            "verbatim_available": bool(sess and sess.verbatim_available),
        }

    def render(self) -> str:
        """The human form: the narrative, then the verbatim turns.

        Deliberately the ORDER §9.4 asks for — the narrative is §9.1's prose
        form and the transcript is what it was derived from, so a reader gets
        the claim and then the evidence, in one screenful.
        """
        if self.session is None:
            # ⛔ The key's KIND comes from `resolved_by`, and the trailing
            # parenthetical is only true for ONE of the two doors: a
            # `--source-url` fetch said `no §9.1 text in the record for
            # source-url 'session:abc' (session 'session:abc')` — the same value
            # twice, labelled a session it is not (PR #9862 review, P3).
            if self.resolved_by == BY_SOURCE:
                # ⛔ THE TWO MISSES ARE NOT THE SAME MISS. `source_narrative`
                # returns None both when the LINK did not resolve (no source row
                # for this url in this tenant, or its session link is unset) and
                # when it resolved to a session that holds no text. Printing
                # "the source's session was resolved" unconditionally narrated a
                # proof step that had not happened for the first case — the
                # wrong-pointer defect an earlier P3 fix set out to remove, one
                # layer down (PR #9862 review, P2). `source_linked is not True`
                # is the fail-closed read of the field: an outcome that never
                # probed the link must not claim it resolved.
                if self.source_linked is not True:
                    return (f"no §9.1 text in the record behind {self.resolved_by} "
                            f"{self.key!r} (no source row with this url in this "
                            f"tenant, or its session link is unset — nothing "
                            f"was resolved, so nothing was proven about a "
                            f"session's text)")
                # The link RESOLVED: name the session it resolved to, so the
                # operator can go and look at the thing that holds no text.
                resolved = (f" resolved to session {self.linked_session_id!r}"
                            if self.linked_session_id else " resolved")
                return (f"no §9.1 text in the record behind {self.resolved_by} "
                        f"{self.key!r} (the source's link{resolved}, and the "
                        f"record holds no text for that session)")
            return (f"no §9.1 text in the record for {self.resolved_by} "
                    f"{self.key!r} (the tenant was scoped and read)")
        sess = self.session
        head = [
            f"session: {sess.session_id}",
            f"redactions: {sess.redactions if sess.redactions is not None else '—'}",
            f"turn_count: {sess.turn_count if sess.turn_count is not None else '—'}",
            f"turns in record: {self.turns}",
        ]
        body = ["", "── narrative (§9.1) ──", sess.narrative or "(none recorded)"]
        if sess.summary:
            body += ["", "── summary ──", sess.summary]
        body += ["", "── transcript (§9.4 verbatim) ──"]
        turns = sess.turns[: self.shown]
        # ⚠️ "(none recorded)" is a claim about the RECORD, so it may only be
        # printed when the record holds no turn at all. `--limit 0` renders no
        # turn while the record holds several, and the bare `or` fallback made
        # the transcript block say "(none recorded)" one line under "turns in
        # record: 1" — the false half of a two-line contradiction.
        if turns:
            body += [t.line for t in turns]
        elif not self.turns:
            body.append("(none recorded)")
        if self.limit is not None and self.turns > self.shown:
            body.append(f"… {self.turns - self.shown} more turn(s) not shown "
                        f"(--limit {self.limit})")
        return "\n".join(head + body)


def scoped(conn: Any, *, org_id: str) -> Any:
    """Install the record's TENANT SCOPE on ``conn`` — the record's own prologue.

    ⛔ THIS REPLACES A HAND-ROLLED ``SELECT 1 FROM public.graphs`` PROOF, and the
    reason is a measured P1 (PR #9862 review): `public.graphs` is granted to no
    record role — R6's migration grants the ten record TABLES — so as
    `tortoise_record_app` (the role the runbook mandates) that SELECT raises
    ``InsufficientPrivilege``, and the verb answered exit ``2`` on every
    invocation. Worse, where the scope row IS readable the check was proving the
    wrong thing: "can I see `graphs`" says nothing about whether the record
    tables' policy filtered the text, so a filtered read was still reported as an
    honest absence (exit ``3``) — the exact answer this module exists to refuse.

    The record already has ONE correct prologue for this, shared by its two doors
    (`record_projection.scope_connection` → `_apply_tenant_scope`): it sets
    ``app.current_org_id`` transaction-locally and then READS IT BACK, refusing
    when the value did not stick (an autocommit connection, a renamed GUC, a
    pooler that dropped it) or when the connection's role can BYPASS RLS. Once it
    returns, the connection is provably scoped to this tenant — so a subsequent
    zero-row read is a true statement about that tenant, and no grant on the
    tenancy parent is needed.

    ⚠️ It needs a TRANSACTION (the GUC is transaction-local), so the caller must
    open a non-autocommit connection and keep every read on it. The refusals
    raise :class:`tortoise.record_projection.RecordProjectionError`, which the
    verb reports as ``EXIT_UNAVAILABLE`` — never as an absence.
    """
    from tortoise.record_projection import scope_connection

    return scope_connection(conn, org_id)


def serve_from_record(
    conn: Any,
    *,
    org_id: str,
    graph_id: str,
    session_id: str | None = None,
    source_url: str | None = None,
    limit: int | None = None,
) -> TextOutcome:
    """Read §9.1's text for ONE key from the record.

    Exactly one of ``session_id`` / ``source_url`` is the key. ``source_url``
    is §9.4's own shape — *"show me the verbatim text behind this claim"* — and
    resolves through ``public.source.session_id``, a COLUMN, so the link
    survives the graph being destroyed (`record_read.source_session`).

    A ``source_url`` miss carries WHICH miss it was in
    :attr:`TextOutcome.source_linked` (and the resolved id in
    :attr:`TextOutcome.linked_session_id`), because "no such source in this
    tenant" and "that session holds no text" send an operator in opposite
    directions and `session_id` is ``None`` for both.

    Raises :class:`tortoise.record_read.RecordReadError` for a structurally
    broken record (a turn key that cannot be ordered, a malformed turn row):
    those are failures, never an empty answer.
    """
    if bool(session_id) == bool(source_url):
        raise ValueError(
            "serve_from_record needs exactly one of session_id / source_url — "
            "two keys make the answer's meaning ambiguous and neither makes it "
            "unanswerable"
        )
    # For the `source-url` door: whether the LINK resolved, and to what. Both
    # travel on the outcome so a miss is worded as what actually happened.
    linked: bool | None = None
    linked_session: str | None = None
    if session_id:
        session = record_read.session_text(
            conn, org_id=org_id, graph_id=graph_id, session_id=session_id)
        key, resolved_by = session_id, BY_SESSION
    else:
        # ⛔ THE LINK IS PROBED SEPARATELY FROM THE TEXT, and not via
        # `source_narrative`: that returns None for BOTH an unresolved link and
        # an empty session, so a caller that reports on the miss cannot tell
        # them apart (see `record_read.source_session`). Same two statements on
        # the same connection, so this costs no extra round trip.
        key, resolved_by = str(source_url), BY_SOURCE
        linked_session = record_read.source_session(
            conn, org_id=org_id, graph_id=graph_id, url=key)
        linked = linked_session is not None
        session = None if linked_session is None else record_read.session_text(
            conn, org_id=org_id, graph_id=graph_id, session_id=linked_session)
    shown = len(session.turns) if session is not None else 0
    if limit is not None and session is not None:
        shown = min(shown, limit)
    return TextOutcome(key=key, resolved_by=resolved_by, session=session,
                       shown=shown, limit=limit, source_linked=linked,
                       linked_session_id=linked_session)


def exit_code_for(outcome: TextOutcome) -> int:
    """``0`` served · ``3`` scoped-and-read-but-absent.

    ⛔ ``2`` is deliberately NOT reachable here, and that is why the verb calls
    this function rather than re-deriving the mapping inline: a
    :class:`TextOutcome` only exists once a read happened, so "no verdict" is a
    fact of the DOOR (no DSN, an unscopable connection, an unreadable record),
    never of an outcome. A mapping that could return ``2`` would be a second
    declaration of the exit contract living next to the first — the drift this
    project already paid for once (PR #9862 review round 3).
    """
    return EXIT_SERVED if outcome.served else EXIT_ABSENT


def log_result(outcome: TextOutcome,
               logger: logging.Logger | None = None) -> None:
    """One structured line per fetch — a served answer is an EVENT.

    The counts are the ones R7's indicator (b) measures: the narrative's
    presence, the redaction count and the transcript size, all read from the
    record. A fetch that served nothing logs at INFO with ``served=False``;
    the operator's verdict comes from the exit code, never from a log level.
    """
    log = logger or logging.getLogger(LOG_NAME)
    payload = outcome.as_dict()
    payload.pop("narrative", None)   # never put a customer's prose in a log line
    payload.pop("summary", None)
    log.info("record-text %s", json.dumps(payload, sort_keys=True, default=str))

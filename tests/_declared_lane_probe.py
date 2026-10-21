"""ONE §17.1 declared-lane instrument, ATTRIBUTED to the projection that wrote (#9256).

WHY THIS EXISTS. Every declared-lane suite reproduces #9124's third-door
predicate the same way: patch `_GuardedGraph.query` **on the class** and record
what it sees. The class patch is unavoidable — the handle is `__slots__`-
restricted and read-only by construction, so an instance patch cannot be
installed — and the consequence is that the recorder sees EVERY projection in
the process, not only the fixture's. Two suites then made an attribution
mistake that is not a test-style preference but a false red:

* `tests/test_9079_onboarding_declared_lane.py` asserted that *every* mutating
  statement it recorded carried the onboarding lane, and that every label
  written was one the onboarding declaration names. A SECOND projection opening
  while the instrument is installed — `hosted_api._acquire_probe_sdk` does
  exactly this from `monitoring._loop` — contributes its own `_ensure_indexes`
  burst (`CREATE INDEX FOR (n:Point) …`, `MERGE (m:Meta {key:'point_fts_v2'})`,
  the `search_keys` flatten). Those statements are correctly declared, under
  `_ensure_indexes_declared`'s `schema-ddl` lane, for THEIR projection; recorded
  without handle identity they were attributed to the onboarding writers, so the
  suite red with an undeclared `CREATE INDEX FOR (n:Point) …`/`Point` label that
  no onboarding writer ever issued (#9256).
* `tests/test_9233_webhook_declared_lane.py` carries the same copy of the
  instrument and the same misattribution exposure.

⛔ THE FIX IS IDENTITY, NOT FILTERING BY STATEMENT SHAPE. The tempting repair —
drop `CREATE INDEX`/`CONSTRAINT` statements from the census — is what
`tests/test_9079_event_writers_journal.py` does today, and it is wrong in the
direction that matters: it goes blind to a foreign projection's DATA write (a
`MERGE (n:Point …)` from a peer lane) while still reading a same-projection DDL
statement as data. Recording `id`-stable handle identity and asking "did THIS
projection issue it?" is exact in both directions.

⛔ AND THE CENSUS DOES NOT GO BLIND. Scoping the ATTRIBUTION must not scope the
instrument: `records()` still carries every statement the process issued, and
`foreign()`/`foreign_undeclared()` expose the ones this suite must not judge.
Two rules keep that honest, both pinned by the adopting suites:

* `refuse` (the guard's own predicate) applies to ANY journal-backed handle, so
  a foreign projection's undeclared write is still REFUSED loudly. Scoping the
  refusal to the owner would turn the instrument into a filter that hides real
  §17.1 violations.
* a suite that filters must prove its filter is not vacuous — that a foreign
  projection's statements really were recorded, and really were excluded;
* and a CENSUS suite must still assert that no foreign JOURNAL-BACKED handle
  wrote UNDECLARED (`assert_no_foreign_undeclared()` below). Attributing
  correctly answers "whose statement is this?", never "was it declared?" — a
  peer's write with no declaration in flight is nobody's claim to check and
  everybody's violation. That census applies the SAME three clauses as the
  guard, arming included: an unbound peer is outside §17.1 and must not red
  it.

ADOPTED: the two suites above, plus `tests/test_9079_edge_lane_declared.py`,
which carried a verbatim copy of the pre-fix recorder and the same
whole-record-set attribution assertion (its own peer-DDL exposure).

REMAINING ADOPTERS (named, not silently left):
`test_9079_ep_epoch_declared.py`, `test_9079_event_writers_journal.py` and
`test_9210_rebuild_declares_restore.py` install their own class-wide recorders.
They are not adoption targets in this change:

* `test_9079_ep_epoch_declared.py` filters its records to statements naming
  `EpMeta` before asserting, and `test_9079_event_writers_journal.py` excludes
  index DDL by shape (which this module argues against) — neither makes the
  adopters' "every recorded statement carried OUR lane" claim, so neither
  misattributes a peer projection's statement. Widening them means re-deriving
  their pins, not fixing a defect.
* `test_9210_rebuild_declares_restore.py` DOES record class-wide on the raw
  handle, but every assertion over that record is keyed to a restore LEG
  fragment or to the `SET <x> += $props` restore SHAPE. A peer projection's
  `_ensure_indexes` burst — the #9256 traffic — matches neither, so it cannot be
  misattributed there; adopting `LaneProbe` would re-derive that suite's leg
  pins for no defect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _GuardedGraph,
    _statement_writes,
)


@dataclass(frozen=True)
class Recorded:
    """One statement seen at `_GuardedGraph.query`, and whose handle issued it.

    `text` is the statement IN FULL (whitespace-normalized only). Truncation is
    a failure-message concern: #9256's sibling suites learned that a truncated
    record makes the label check read a PREFIX, so an appended write clause
    passes silently — fail-open in the one direction the check exists for.
    """

    text: str
    scope: str | None
    writes: bool
    owner: Any

    def as_tuple(self) -> tuple[str, str | None, bool]:
        return (self.text, self.scope, self.writes)


class LaneProbe:
    """The class-wide recorder, and the projection whose statements ARE the lane.

    `owner` is the projection this suite's lane speaks for — normally
    `sdk._get_proj()`. It is required, not defaulted: an instrument that cannot
    say whose statement it saw is exactly the #9256 defect.
    """

    def __init__(self, owner: Any) -> None:
        if owner is None:
            raise ValueError(
                "LaneProbe needs the projection it speaks for; recording "
                "statements with no handle identity is the #9256 defect")
        self._owner = owner
        self.records: list[Recorded] = []

    # ── installation ────────────────────────────────────────────────────────
    def install(self, monkeypatch, *, refuse: bool | str = False) -> LaneProbe:
        """Install #9124's third-door predicate on `_GuardedGraph.query`.

        `refuse` selects which mutating statements the guard REFUSES — the
        guard's semantics, never the attribution's:

        * ``False`` — refuse nothing (recording mode).
        * ``True`` — refuse every UNDECLARED write (the #9124 armed door).
        * a ``str`` — refuse any statement containing it, whether or not a
          declaration is in flight. That is how a suite aims a refusal at ONE
          caller to prove the caller does not swallow it, which is the property
          that makes a missing declaration silent.

        The refusal is NOT scoped to `owner`: an undeclared write on a foreign
        journal-backed handle is a real §17.1 violation and this instrument must
        report it by raising, exactly as the product guard would.
        """
        original = _GuardedGraph.query
        probe = self

        def instrumented(handle, cypher, params=None, timeout=None):
            text = " ".join((cypher or "").split())
            # ⛔ Classify the RAW statement, never the normalized one. The
            # product guard classifies the raw argument
            # (`_GuardedGraph._refuse_unauthorized_cache_write(statement)`), and
            # `_statement_writes` strips `//` line comments with `//[^\n]*`.
            # Normalizing FIRST turns the newline into a space, so a comment
            # swallows the rest of the statement — `MATCH (n) // note\nSET
            # n.x = 1` classifies as a READ, drops out of every `if w` census
            # filter and is never refused: fail-open in the one direction this
            # instrument exists for. `text` stays normalized — it is the
            # RECORD, not the classification.
            writes = _statement_writes(cypher or "")
            record = Recorded(
                text=text,
                scope=_CACHE_WRITE_AUTHORIZED.get(),
                writes=writes,
                owner=getattr(handle, "_proj", None),
            )
            probe.records.append(record)

            aimed = isinstance(refuse, str) and refuse.lower() in text.lower()
            undeclared = refuse is True and record.scope is None
            if ((aimed or undeclared) and writes
                    and getattr(handle._proj, "_journal_backed", False)):
                raise DirectCacheWriteRefused(
                    "§17.1 third door (#9079/#9124): a mutating statement "
                    "reached `proj.g` with no declaration in flight — "
                    "statement=" + text[:160]
                )
            return original(handle, cypher, params=params, timeout=timeout)

        monkeypatch.setattr(_GuardedGraph, "query", instrumented)
        return self

    # ── the two views ───────────────────────────────────────────────────────
    def records_for(self, projection: Any) -> list[Recorded]:
        """Statements issued on `projection`'s handle — identity, not shape."""
        return [r for r in self.records if r.owner is projection]

    def own_records(self) -> list[Recorded]:
        """Statements this suite's lane issued, with handle identity kept.

        A record whose handle carries no `_proj` is NOT this lane's: unknown
        ownership falls to `foreign()` by construction, because the alternative
        polarity would attribute an unidentifiable writer to the lane under
        test — the defect, not a convenience.
        """
        return self.records_for(self._owner)

    def foreign_records(self) -> list[Recorded]:
        """Statements a DIFFERENT projection issued while the probe was installed.

        Reported, never attributed. A suite asserts over `own()`; this view is
        what keeps the filter from being a silent blindfold, and what a failure
        message should name when a reader needs to know a peer lane was live.
        """
        return [r for r in self.records if r.owner is not self._owner]

    def own(self) -> list[tuple[str, str | None, bool]]:
        """The census view: `(statement, scope, writes)` for THIS lane only."""
        return [r.as_tuple() for r in self.own_records()]

    def foreign(self) -> list[tuple[str, str | None, bool]]:
        """The census view of other projections' statements — never judged."""
        return [r.as_tuple() for r in self.foreign_records()]

    def foreign_undeclared(self) -> list[Recorded]:
        """A JOURNAL-BACKED foreign projection's undeclared mutating statements.

        This IS an assertion target for an adopting suite — see
        `assert_no_foreign_undeclared()` below. It is not a place to judge
        another lane's declaration NAME: every record here has `scope is None`,
        i.e. no declaration at all, so there is no lane whose claim could be
        checked — only a §17.1 violation to report. It also lets a failure
        message distinguish "this lane wrote undeclared" from "a peer lane
        did".

        ⛔ ARMED HANDLES ONLY. §17.1's predicate has THREE clauses, not two:
        the product guard (`_GuardedGraph._refuse_unauthorized_cache_write`)
        MEASURES and refuses only when the projection is journal-backed — an
        unbound projection (`_journal_backed` False) has no journal to be the
        single writer OF, so "§17.1 has nothing to enforce there". Omitting
        that clause here would make a process-wide census report a violation on
        a handle the rule exempts — the #9256 false red, in the opposite
        direction, and arming is a property of the ENVIRONMENT (`hosted_api`
        builds its probe SDK with `event_log_path=None` unless
        `TORTOISE_EVENT_LOG_BASE_DIR` is set), not of the test. The refusal path
        in `install()` already checks this exact clause; the census must agree
        with it.
        """
        return [
            r for r in self.foreign_records()
            if r.writes and r.scope is None
            and getattr(r.owner, "_journal_backed", False)
        ]


def assert_no_foreign_undeclared(probe: LaneProbe) -> None:
    """The census suite's OTHER half: no PEER projection wrote undeclared.

    Attribution and blindness are two questions, and scoping `own()` answers
    only the first (#9256). A mutating statement with `scope is None` is not
    "another lane's declaration to judge" — there is NO declaration in flight,
    so it is a violation on whichever ARMED handle issued it, and a census test
    that filters it away goes green on exactly the class #9256 asked it to keep
    loud. The wild peer (`hosted_api._acquire_probe_sdk`) is PRODUCT code, so
    this is not a test-only concern.

    It cannot reintroduce the false red, for three separate reasons: that
    peer's `_ensure_indexes` burst is DECLARED (`schema-ddl`) so it stays out of
    `foreign_undeclared()`; the monitoring probe issues only `RETURN 1` (a
    read); and a peer that is NOT journal-backed is exempt from §17.1
    altogether (`foreign_undeclared()` checks the arming clause, as the product
    guard does), so this census can never red on a handle the rule does not
    reach.
    """
    offenders = probe.foreign_undeclared()
    assert not offenders, (
        "a PEER projection wrote through the handle with NO §17.1 declaration "
        "in flight — not this lane's declaration to judge, but still a "
        "violation (scope is None on every record here): "
        f"{[(r.text[:120], r.scope) for r in offenders]}"
    )

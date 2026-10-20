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
  projection's statements really were recorded, and really were excluded.

REMAINING ADOPTERS (named, not silently left): `test_9079_ep_epoch_declared.py`
and `test_9079_event_writers_journal.py` install their own class-wide recorders.
They are not adoption targets in this change because neither asserts an
attribution over a fixture it does not own in the way the two adopters do — the
event-writers probe instead excludes index DDL by shape, which this module's
docstring argues against — and widening this change to them would mean
re-deriving four suites' pins rather than fixing one defect.
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
            writes = _statement_writes(text)
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
        """Foreign mutating statements with no declaration in flight.

        NOT an assertion target for an adopting suite: this lane cannot judge
        another lane's declaration, and asserting here is the #9256
        misattribution in a new spelling. It is exposed so a failure message can
        distinguish "this lane wrote undeclared" from "a peer lane did".
        """
        return [r for r in self.foreign_records() if r.writes and r.scope is None]

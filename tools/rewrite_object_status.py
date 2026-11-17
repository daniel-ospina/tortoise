#!/usr/bin/env python3
"""#12855 — rewrite the stored `outdated` / `retracted` Object status words to
`deprecated`, ONCE, through the SDK's own journaled update path.

    uv run python tools/rewrite_object_status.py                       # preview (default)
    uv run python tools/rewrite_object_status.py --execute             # do it
    uv run python tools/rewrite_object_status.py --execute --json

Owner decision, on #12855 (2026-11-16): *"Rewrite the stored rows once."* That
authorises dropping `outdated` from `assembly._RECALL_OBJECT_EXCLUDED_STATUSES`
(`assembly.py:4383`) — and the drop is only safe AFTER this tool has run,
because the member is load-bearing for rows that already carry the word: the
successor-existence probe treats an excluded successor as invisible
(NAME-ONLY annotation), so removing the member flips such a row to a **verified
successor link**. The member stays until no stored row needs it; this tool is
what makes "no stored row needs it" true on a given graph.

WHY THIS IS A MIGRATION AND NOT A TIDYING
========================================
`rebuild_all` reconstructs the graph from the event stream, so a bare
`SET n.status = 'deprecated'` is **reverted by the next rebuild** — the creation
record still carries `outdated`, and the fold re-derives it. The durable path
already exists and is the one this tool uses:

    sdk.update_knowledge(id, status='deprecated')
      → `_update_entity_any` → `_update_entity`
      → non-Point arm journals `EntityMutated{op: restatus, state: {status}}

and the replay fold applies that `state` with the same `SET n += $s` the live
write ran. So a rewrite executed **row by row through the SDK** is a durable
rewrite: the journal holds it, a rebuild reproduces it, and nothing new has to
be invented. This tool therefore never issues a raw `SET` for the status word —
a raw write here would look green on the live graph and be undone silently.

⚠️ **The journal is a precondition, not an option.** If no journal is
configured for the target, every write would be live-only, and this tool REFUSES
before the first write rather than produce that. `--event-log-path` names it;
when omitted it is resolved by `tortoise.config._resolve_event_log_path` — the
ONE derivation the deployed lane uses — and a refusal names the flag and
`TORTOISE_EVENT_LOG_BASE_DIR` when that resolves to nothing.

WHY PREVIEW IS THE DEFAULT
==========================
The absence of `--execute` is the preview; there is no `--dry-run`. Same shape as
`tools/purge_legacy_turns.py`: a destructive arm that cannot run by accident.
The preview prints the population **split by word** (`outdated` and `retracted`
are different questions and are counted separately), the ids, and the rows this
tool deliberately does NOT touch. A preview that reports **0 / 0** is a real
result: it means no stored row needs the `assembly` member, and the member is
dead weight on that graph (measured per graph — it is not a claim about any
other).

WHAT IT REWRITES, AND WHAT IT DELIBERATELY DOES NOT
==================================================
* **Yes:** `:Object` rows whose `status` string is `outdated` or `retracted` →
  `status='deprecated'`. `deprecated` is a declared §4.3 Object terminal word, and
  it is what the owner's decision names. `retracted` → `deprecated` is a
  dead→dead move, which `_update_entity`'s non-Point arm admits by design;
  `outdated` is NOT in the family's dead set, so that move is live→dead.
* **No:** the `outdated` **boolean flag** (`n.outdated = true`), on any label.
  It is a different axis (ONTOLOGY §5's Point lifecycle flag, read by
  `live.is_terminal_status`), the owner's decision excludes it, and this tool
  would corrupt Points if it touched it. It is **reported** (count + sample) so
  an operator can see it is there and knows it was left alone.
* **No:** `:Point` / `:Subject` / `:Event` rows. This is the Object family only.
* **No:** a row's supersession carriers (`supersededBy` / `supersededAt`). A row
  that carries both a legacy status word and a carrier is **counted separately**
  in the preview, because rewriting the status leaves the carrier in place — the
  operator sees that shape rather than this tool silently deciding what it means.

⛔ A ROW LABELLED **BOTH** `:Point` AND `:Object` IS REFUSED, AND THAT IS
DELIBERATE. `update_knowledge` dispatches on the label, so such a row takes the
Point arm, where a terminal Point declares NO outgoing transition (#2498: *a
dead claim is answered by minting a successor, never by re-stamping it*). This
tool does NOT work around that: overriding it is a REOPEN of #2498, not a
migration step, and nothing here is authorised to reverse a recorded decision.

The row is reported as a FAILURE by id and the run exits 1 — never silently
skipped, and never counted as rewritten. ⚠️ THE REFUSAL IS MADE **HERE**, from
the row's own labels, and NOT by catching an exception — because on this head
the Point arm does not raise at all. It ECHOES: a stored ``retracted`` already
MEANS ``deprecated`` there (#11824 S0's meaning mapping), so the door writes the
word the row already holds and the row is left untouched (`sdk.py`, "AN ECHO
WRITES THE WORD THE ROW ALREADY HOLDS", #11824 S0 review P2). Measured
2026-11-17: ``update_knowledge(dual_id, status='deprecated')`` returns the row
with ``status='retracted'`` and no exception. The post-write status check below
is the second guard, for any other silent no-op; a no-op counted as a rewrite is
exactly the false PASS this module must not produce. The echo is not this tool's
rule to reverse: #12855 keeps both words on Points. Measured on the canonical instance (2026-11-17):
exactly one such row exists (`org_probe8884b_5d1a9fd5`, a probe graph), and it
is the ONLY `:Object` row in the whole instance carrying a legacy word — so this
refusal, not a rewrite, is what an operator sees there. Read that as the tool
answering honestly, not as the rewrite having failed.

PAGING AND THE CROSS-CHECK (#5062 / #4515)
==========================================
FalkorDB applies a server-global `RESULTSET_SIZE` (default 10 000) silently, so
an unpaged population read can certify the half it saw and this tool would then
leave rows behind while reporting success. The read is keyset-paged on `id(n)`
and compared against the store's OWN aggregate count; a page set that falls
short is reported as `truncated` and REFUSES the execute arm. The post-condition
re-read is the second guard: `--execute` exits non-zero unless the population is
**0 after the run**, whatever the per-row results said.

EXIT CODES
==========
    0   the run completed — a preview, or an execute whose population is now 0
    1   the run did not reach its post-condition: rows remain, a row failed,
        the read was truncated, or a row is UNADDRESSABLE (no `id`, so the SDK
        cannot rewrite it — refused before the first write on `--execute`)
    2   refused before touching anything (no journal, empty target flag)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):  # run as a script / imported by a test
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: The legacy words this tool retires, each rewritten to :data:`REWRITE_TO`.
#: ONE declaration: the Cypher predicate, the preview's split counts and the
#: docstring's table all read it.
LEGACY_WORDS: tuple[str, ...] = ("outdated", "retracted")

#: The declared §4.3 Object terminal word the owner's decision names.
REWRITE_TO = "deprecated"

#: Keyset page size — well under FalkorDB's `RESULTSET_SIZE` (default 10 000)
#: so a page can never be truncated; the aggregate count below is the guard.
_PAGE = 1000

REFUSE_NO_JOURNAL = "no-journal"
REFUSE_EMPTY_FLAG = "empty-target-flag"
REFUSE_UNADDRESSABLE = "unaddressable-row"
REFUSE_TRUNCATED = "truncated-read"

#: Knowledge labels a ``:Object`` must NOT also carry. ``update_knowledge``
#: dispatches on the label, so a dual-labelled row takes the other family's arm
#: — and on the Point arm a stored legacy word already MEANS the rewrite target
#: (#11824 S0), so the write ECHOES the stored word instead of restamping it.
#: See the module docstring: the refusal is made from these, not from an
#: exception, because that arm returns the row unchanged without raising.
_FOREIGN_KNOWLEDGE_LABELS = frozenset({"Point", "Subject"})


def _word_predicate() -> str:
    """The status predicate, built from :data:`LEGACY_WORDS` (never retyped).

    Fixed module constants — no caller input reaches this string, so there is
    nothing to inject and no need for a parameter.
    """
    return " OR ".join(f"n.status = '{w}'" for w in LEGACY_WORDS)


def _rows_cypher() -> str:
    return (
        "MATCH (n:Object) WHERE id(n) >= $cursor AND (" + _word_predicate() + ") "
        "RETURN id(n), n.id, n.status, n.name, n.supersededBy, n.supersededAt, "
        "labels(n) "
        "ORDER BY id(n) LIMIT $batch"
    )


def _count_cypher() -> str:
    return "MATCH (n:Object) WHERE " + _word_predicate() + " RETURN count(n)"


#: The legacy FLAG — a different axis, reported and never written. `coalesce`
#: so a NULL/absent flag is not read as terminal (the read surfaces' own
#: predicate, `live.is_terminal_status`'s Cypher half).
_FLAG_COUNT_CYPHER = (
    "MATCH (n:Object) WHERE coalesce(n.outdated, false) <> false RETURN count(n)"
)
_FLAG_SAMPLE_CYPHER = (
    "MATCH (n:Object) WHERE coalesce(n.outdated, false) <> false "
    "RETURN n.id, n.outdated ORDER BY n.id LIMIT $batch"
)


@dataclass
class Report:
    """Everything one run learned, and everything it did.

    Deliberately a flat record of FACTS (counts, ids, journal deltas) — the
    renderer is :func:`render`, so a `--json` consumer and the `--execute` arm
    cannot disagree about what was measured.
    """

    graph: str | None = None
    journal: str | None = None
    #: The population, split by word: ``{"outdated": n, "retracted": n}``.
    by_word: dict[str, int] = field(default_factory=dict)
    total: int = 0
    #: The FULL population, `{"id", "status", "name", "graph_id"}` per row —
    #: the execute arm acts on this, never on the render sample.
    rows: list[dict] = field(default_factory=list)
    sample: list[dict] = field(default_factory=list)
    #: Rows carrying a supersession carrier as well as a legacy status word.
    carriers: list[dict] = field(default_factory=list)
    #: `:Object` rows with no `id` — the SDK cannot address them.
    unaddressable: list[str] = field(default_factory=list)
    #: The legacy boolean FLAG, on any label — reported, never written.
    flag_count: int = 0
    flag_sample: list[dict] = field(default_factory=list)
    #: The paged read fell short of the store's own count.
    truncated: bool = False
    executed: bool = False
    rewritten: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    journal_records_before: int | None = None
    journal_records_after: int | None = None
    remaining: dict[str, int] = field(default_factory=dict)

    @property
    def journal_delta(self) -> int | None:
        if self.journal_records_before is None or self.journal_records_after is None:
            return None
        return self.journal_records_after - self.journal_records_before

    def to_dict(self) -> dict:
        # `rows` is the whole population; `sample` is what a reader needs to
        # recognise it. Keeping the full list out of the artefact keeps a
        # 10 000-row preview readable, and `rewritten` still names every id that
        # was actually written.
        out = {k: v for k, v in self.__dict__.items() if k != "rows"}
        out["journal_records_appended"] = self.journal_delta
        out["legacy_words"] = list(LEGACY_WORDS)
        out["rewrite_to"] = REWRITE_TO
        return out


# ── the read ─────────────────────────────────────────────────────────────


def collect(proj, *, sample: int = 20) -> Report:
    """Read the population. READ-ONLY, keyset-paged, cross-checked.

    Takes the PROJECTION (``sdk._get_proj()``), never a bare graph handle: a
    projection's own ``query`` wraps its arguments differently from the graph's,
    and a caller that mixed the two got a nested-``params`` statement back
    (#12855, measured) — so there is one accepted type and the graph handle is
    taken here, once.

    `sample` bounds only what is RENDERED; the full population is enumerated,
    because the execute arm rewrites every row and the preview's counts must be
    the counts it will act on.
    """
    g = proj.g
    rep = Report()
    rows: list = []
    cursor = 0
    while True:
        page = g.query(_rows_cypher(),
                       params={"cursor": cursor, "batch": _PAGE}).result_set
        if not page:
            break
        rows.extend(page)
        cursor = int(page[-1][0]) + 1
        if len(page) < _PAGE:
            break

    store_count = g.query(_count_cypher()).result_set
    total = int(store_count[0][0] or 0) if store_count else 0
    rep.truncated = len(rows) < total
    rep.total = len(rows)

    by_word = {w: 0 for w in LEGACY_WORDS}
    for graph_id, rid, status, name, sup_by, sup_at, labels in rows:
        word = status if isinstance(status, str) else repr(status)
        by_word[word] = by_word.get(word, 0) + 1
        row = {"id": rid, "graph_id": graph_id, "status": status, "name": name,
               "labels": list(labels or [])}
        rep.rows.append(row)
        if len(rep.sample) < sample:
            rep.sample.append(row)
        if sup_by or sup_at:
            rep.carriers.append(row)
        if not isinstance(rid, str) or not rid:
            rep.unaddressable.append(str(graph_id))
    rep.by_word = by_word

    flag_count = g.query(_FLAG_COUNT_CYPHER).result_set
    rep.flag_count = int(flag_count[0][0] or 0) if flag_count else 0
    rep.flag_sample = [
        {"id": r[0], "outdated": r[1]}
        for r in g.query(_FLAG_SAMPLE_CYPHER,
                         params={"batch": sample}).result_set
    ]
    return rep


# ── the write ────────────────────────────────────────────────────────────


def rewrite(sdk, rep: Report) -> Report:
    """Rewrite every collected row through the SDK's journaled update path.

    One `sdk.update_knowledge(id, status=REWRITE_TO)` per row — the public
    write that `_update_entity` journals as `EntityMutated{state: {status}}`.
    A raw `SET` is never issued here: it would not reach the journal, and the
    next `rebuild_all` would revert it (see the module docstring).
    """
    rep.executed = True
    log = sdk._get_event_log()
    try:
        rep.journal_records_before = log.position()
    except OSError as exc:                            # pragma: no cover
        rep.failures.append({"phase": "journal-position", "error": str(exc)})

    for row in rep.rows:
        rid = row["id"]
        if not isinstance(rid, str) or not rid:
            rep.failures.append({
                "id": None, "graph_id": row.get("graph_id"),
                "error": ("UNADDRESSABLE: this :Object carries a legacy status "
                          "word and no `id`, so the SDK cannot rewrite it"),
            })
            continue
        foreign = [lbl for lbl in row.get("labels") or []
                   if lbl in _FOREIGN_KNOWLEDGE_LABELS]
        if foreign:
            rep.failures.append({
                "id": rid, "graph_id": row.get("graph_id"),
                "error": (
                    "REFUSED (dual-labelled): this :Object also carries "
                    f"{', '.join(':' + lbl for lbl in foreign)} — "
                    "update_knowledge dispatches on the label, so the write "
                    "takes that family's arm, and that arm ECHOES: the stored "
                    "word already MEANS the rewrite target there (#11824 S0), "
                    "so it writes the word the row already holds and the row "
                    "is left untouched (a dead claim is answered by minting a "
                    "successor, never by re-stamping it — #2498). The call "
                    "returns without raising, so a silent echo must never be "
                    "counted as a rewrite: the row is reported here instead. "
                    "Mint a successor, or correct the label, and re-run."),
            })
            continue
        try:
            landed = sdk.update_knowledge(rid, status=REWRITE_TO)
        except Exception as exc:
            rep.failures.append({"id": rid, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not isinstance(landed, dict) or landed.get("status") != REWRITE_TO:
            rep.failures.append({
                "id": rid,
                "error": f"write returned status {landed.get('status') if isinstance(landed, dict) else landed!r}",
            })
            continue
        rep.rewritten.append(rid)

    try:
        rep.journal_records_after = log.position()
    except OSError as exc:                            # pragma: no cover
        rep.failures.append({"phase": "journal-position", "error": str(exc)})

    after = collect(sdk._get_proj())
    rep.remaining = dict(after.by_word)
    return rep


# ── rendering ────────────────────────────────────────────────────────────


def render(rep: Report) -> str:
    lines: list[str] = []
    head = "execute" if rep.executed else "preview"
    lines.append(f"rewrite_object_status: {head} — graph {rep.graph!r}")
    lines.append(f"  journal: {rep.journal}")
    for word in LEGACY_WORDS:
        lines.append(f"  :Object rows with status={word!r}: {rep.by_word.get(word, 0)}")
    other = {k: v for k, v in rep.by_word.items() if k not in LEGACY_WORDS}
    for word, n in sorted(other.items()):
        lines.append(f"  (unexpected) :Object rows with status={word}: {n}")
    lines.append(f"  population: {rep.total}")
    if rep.truncated:
        lines.append(
            "  ⛔ TRUNCATED: the paged read fell short of the store's own count — "
            f"{REFUSE_TRUNCATED}")
    if rep.sample:
        lines.append(f"  sample ({len(rep.sample)} of {rep.total}):")
        for row in rep.sample:
            lines.append(f"    {row['id']}  status={row['status']!r}  name={row['name']!r}")
    if rep.carriers:
        lines.append(
            f"  ⚠ {len(rep.carriers)} row(s) ALSO carry a supersession carrier "
            "(supersededBy/supersededAt) — the carrier is NOT touched:")
        for row in rep.carriers[:5]:
            lines.append(f"    {row['id']}")
    if rep.unaddressable:
        lines.append(
            f"  ⛔ {len(rep.unaddressable)} :Object row(s) have no `id` — the SDK "
            f"cannot address them ({REFUSE_UNADDRESSABLE}): "
            + ", ".join(rep.unaddressable[:5]))
    if rep.flag_count:
        lines.append(
            f"  note: {rep.flag_count} :Object row(s) carry the legacy `outdated` "
            "FLAG (n.outdated) — a different axis, NOT rewritten here:")
        for row in rep.flag_sample[:5]:
            lines.append(f"    {row['id']}  outdated={row['outdated']!r}")

    if rep.executed:
        lines.append("")
        lines.append(f"  rewritten: {len(rep.rewritten)} row(s) -> status={REWRITE_TO!r}")
        if rep.journal_delta is not None:
            lines.append(f"  journal records appended: {rep.journal_delta}")
        for fail in rep.failures:
            lines.append(f"  ⛔ FAILED {fail.get('id')}: {fail.get('error')}")
        lines.append("  after: " + ", ".join(
            f"{w}={rep.remaining.get(w, 0)}" for w in LEGACY_WORDS))
    else:
        lines.append("")
        if rep.total == 0:
            lines.append(
                "  ⇒ 0 rows carry 'outdated' or 'retracted' on this graph. No rewrite "
                "is needed, and `assembly._RECALL_OBJECT_EXCLUDED_STATUSES`'s "
                "`outdated` member is dead weight HERE — the drop is a separate, "
                "sequenced change (#12855 step 3).")
        else:
            lines.append(
                "  ⇒ preview only; nothing was written. Re-run with --execute "
                "to rewrite these rows through the journaled update path.")
    return "\n".join(lines)


# ── entry point ──────────────────────────────────────────────────────────


def _empty_flag(args) -> str | None:
    """A target flag passed EMPTY is not "no flag" — it selects a different store.

    The same refusal `turn_parity_census.empty_graph_target_flag` makes, restated
    here in one line because this tool opens ONE store through the SDK and a
    set-but-empty flag would fall through to the embedded default while the
    operator believes they addressed a container.
    """
    for name in ("uri", "db_path", "graph_name", "event_log_path"):
        value = getattr(args, name, None)
        if value is not None and not str(value).strip():
            return f"--{name.replace('_', '-')} was passed EMPTY"
    return None


def run(sdk, *, execute: bool = False, sample: int = 20) -> Report:
    """Collect, and (only when `execute`) rewrite. The testable core.

    `main` is argparse + connection + exit codes; every decision that matters is
    here, so a suite can drive the real graph and the real journal without
    re-implementing this tool's logic.
    """
    proj = sdk._get_proj()
    rep = collect(proj, sample=sample)
    rep.graph = getattr(proj, "graph_name", None)
    log = sdk._get_event_log()
    rep.journal = str(getattr(log, "path", None)) if log is not None else None
    if not execute:
        return rep
    return rewrite(sdk, rep)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rewrite_object_status",
        description=("#12855: rewrite stored `outdated`/`retracted` Object status "
                     "words to `deprecated`, through the journal. Preview by "
                     "default; --execute writes."),
    )
    parser.add_argument("--uri", help="FalkorDB URI (default: $TORTOISE_DB_URI, else embedded)")
    parser.add_argument("--db-path", help="embedded DB path (default: resolved)")
    parser.add_argument("--graph-name", help="graph name to address (verbatim)")
    parser.add_argument("--event-log-path",
                        help=("the JSONL journal this target's rebuild replays "
                              "(default: $TORTOISE_EVENT_LOG_BASE_DIR derivation)"))
    parser.add_argument("--sample", type=int, default=20,
                        help="how many ids to print (default 20)")
    parser.add_argument("--execute", action="store_true",
                        help="actually rewrite the rows (default: preview only)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    bad = _empty_flag(args)
    if bad is not None:
        sys.stderr.write(
            f"rewrite_object_status: REFUSED {REFUSE_EMPTY_FLAG}: {bad}. An empty "
            "target flag is not 'no flag' — it selects a different store. "
            "Nothing was read and nothing was written.\n")
        return 2

    import os

    if args.uri:
        # The SDK resolves its target from the environment, so --uri must land
        # there or the read and the write could name two different stores.
        os.environ["TORTOISE_DB_URI"] = args.uri

    from tortoise.config import _resolve_event_log_path
    from tortoise.sdk import TortoiseSDK

    journal = args.event_log_path
    if journal is None:
        journal = _resolve_event_log_path(namespace=None, graph_name=args.graph_name)
    if journal is None:
        sys.stderr.write(
            f"rewrite_object_status: REFUSED {REFUSE_NO_JOURNAL}: no journal is "
            "configured for this target, so every write would be live-only and "
            "the next rebuild would revert it. Pass --event-log-path "
            "(or set TORTOISE_EVENT_LOG_BASE_DIR). Nothing was written.\n")
        return 2

    sdk = TortoiseSDK(db_path=args.db_path, graph_name=args.graph_name,
                      event_log_path=journal)
    try:
        if sdk._get_event_log() is None:              # pragma: no cover - parity
            sys.stderr.write(
                f"rewrite_object_status: REFUSED {REFUSE_NO_JOURNAL}: the SDK "
                "reports no journal for this target. Nothing was written.\n")
            return 2

        preview = run(sdk, execute=False, sample=args.sample)
        if args.execute:
            # Fail closed BEFORE the first write: an unaddressable row and a
            # truncated read both mean "this run cannot reach the population it
            # claims", and a partial rewrite of such a graph is the shape that
            # leaves the `assembly` member load-bearing for hidden rows.
            if preview.truncated:
                sys.stderr.write(
                    f"rewrite_object_status: REFUSED {REFUSE_TRUNCATED}: the paged "
                    "read fell short of the store's own count, so this run does "
                    "not know the population. Nothing was written.\n")
                return 2
            if preview.unaddressable:
                sys.stderr.write(
                    f"rewrite_object_status: REFUSED {REFUSE_UNADDRESSABLE}: "
                    f"{len(preview.unaddressable)} :Object row(s) carry a legacy "
                    "status word but no `id`, so the SDK cannot rewrite them; a "
                    "partial rewrite would leave the filter member load-bearing. "
                    "Nothing was written.\n")
                return 2
        rep = run(sdk, execute=args.execute, sample=args.sample)
    finally:
        sdk.close()

    if args.json:
        print(json.dumps(rep.to_dict(), indent=2, default=str))
    else:
        print(render(rep))

    if not rep.executed:
        return 0
    leftover = sum(rep.remaining.get(w, 0) for w in LEGACY_WORDS)
    if rep.failures or rep.truncated or leftover or rep.unaddressable:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

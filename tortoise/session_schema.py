"""#3554 — the canonical ``:Session`` write schema and the writer inventory.

**Why this module exists.** The ``:Session`` node (the capture session
container, ``CONTAINS`` → turn ``Point``s) is written by MANY components, each
owning a different property subset, and until now there was **no single
authoritative declaration** of the full property set or of which writer owns
which subset. The two capture lanes kept separately drifted field lists (the
``machine_id``/``model`` hosted-only asymmetry), the commit and budget-hold
paths added counter fields, the demo seeder wrote a third shape, and the replay
folds re-created the node from the journal. A reader could not answer "who
writes ``capture_ok``?" without a function-level survey, and the epic's own
completeness claim had to be re-derived by hand on every rebase.

This module is that declaration, and it is deliberately **verifiable rather
than trusted**: ``tools/session_retrofit_audit.py inventory`` re-runs the
recorded search predicate over the tree and diffs what it finds against
``SESSION_WRITERS`` / ``SESSION_CLIENTS``. The scan is the tracked tree
(``git ls-files``) minus what :data:`PREDICATE` declares excluded (``tests/``/
``docs/`` and the third-party/cache directory names), so a writer under ANY
other tracked directory is a site. A new writer — or a removed one — is
`INVENTORY DRIFT`, and
``tests/test_session_schema_3554.py`` fails on it.

**This declares the schema; it does not create one.** Every property below is
already written by the code named in its row. The declaration exists so that
`docs/ONTOLOGY.md` §4.8 can name the same set and so that a change to the
capture contract is a reviewed diff instead of an unnoticed field addition —
#3551 introduced the shared writer and its field-list contract, and this module
verifies it rather than declaring a second, competing schema.

**Dependency-free on purpose** (no FastAPI, no graph client): the CLI, the
tests and the audit tool all import it without pulling the API stack — the
same constraint ``tortoise/session_projection.py`` (the READ declaration this
module is the write-side twin of) states for itself.

**The demo session's id is declared here too** (``demo_session_id``), for the
reason #2360 gives for ``_DEMO_POINT_IDS``: ONE derivation for the seeder's
write — ``hosted_api._seed_demo_graph`` calls it rather than re-spelling the
f-string. ⚠️ **The read-side exclusion is NOT implemented for the Session
container.** #2360's exclusion is Point-scoped (``_DEMO_POINT_IDS``: the demo
Points and the sentinel), so the demo ``:Session`` is still returned by
``GET /v1/sessions``, ``GET /v1/sessions/{id}`` and ``TortoiseSDK.get_session``.
That is recorded here as an open gap, never read as coverage — tracked on
**#8290**.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEMO_SESSION_ID_PREFIX",
    "RETROFIT_VERDICTS",
    "SESSION_CLIENTS",
    "SESSION_PROPERTIES",
    "SESSION_PROPERTY_OWNERS",
    "SESSION_WRITERS",
    "SessionClient",
    "SessionWriter",
    "audit_session_retrofit",
    "demo_session_id",
    "session_property_owners",
]

#: The prefix the demo/sample seeder derives its Session id with. ONE
#: declaration: ``hosted_api._seed_demo_graph`` writes the id through
#: :func:`demo_session_id`. The session READ surfaces do NOT exclude it today —
#: #2360's exclusion covers the demo POINTS (``_DEMO_POINT_IDS``), not the demo
#: Session container — and that gap is recorded rather than claimed closed
#: (**#8290** tracks the decision).
DEMO_SESSION_ID_PREFIX = "session_demo_"


def demo_session_id(org_id: str) -> str:
    """The deterministic demo-session id for an org.

    The seeder's WRITER derives the id here, so a read-surface exclusion (when
    one is written) reuses this derivation instead of re-spelling the f-string
    and drifting. Today the only consumer is ``_seed_demo_graph``.
    """
    return f"{DEMO_SESSION_ID_PREFIX}{org_id[:8]}"


# ── the writers ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionWriter:
    """One component that sets properties on a ``:Session`` node.

    ``key`` is ``<repo-relative path>::<function or module-level constant>``
    and is what the predicate reconstructs — so a row cannot survive a rename
    or a move without the tool reporting it.
    """
    key: str
    kind: str
    lane: str
    properties: frozenset[str] = field(default_factory=frozenset)
    writes_whole_schema: bool = False
    note: str = ""


@dataclass(frozen=True)
class SessionClient:
    """One component that drives a writer over HTTP (or in process).

    ``transport`` separates the two: only ``http`` rows are asserted by the
    predicate, the rest enumerate EVERY shipped hook recorder (one per
    harness: pi/cursor/claude/codex) so the inventory answers "who can create
    a session?" and not only "who runs the Cypher?". That enumeration is
    exhaustive, and it is pinned by ``tests/test_session_schema_3554.py`` — a
    missing ``cli`` row is otherwise invisible, because ``inventory_report``
    diffs only the ``http`` rows.
    """
    path: str
    transport: str
    lane: str
    marker: str
    note: str = ""


#: The ONE capture-path writer. Both capture lanes funnel through it: hosted
#: ``_capture_session_impl`` and ``TortoiseSDK.capture_session`` call it and
#: hold no copy of the MERGE text (#3551).
CAPTURE_PRIMITIVE = "tortoise/sdk.py::_write_session_and_turns"

#: The full ``:Session`` property set, ordered as the capture primitive emits
#: its clauses (identity, then the capture fields, then the outcome fields).
#: Every member MUST appear in :data:`SESSION_PROPERTY_OWNERS` — the
#: derivation asserts it at import, so a property can never be declared
#: without a named owner.
SESSION_PROPERTIES: tuple[str, ...] = (
    # ── the identity KEY. Every minting writer spells it in its
    # `MERGE (s:Session {id:…})`, but a MERGE key is not a `SET
    # <alias>.<prop>` clause and :func:`session_property_owners` attributes
    # only SET properties — so the `id` row names the whole-schema replay
    # restore, not the ten writers that mint the key (see that function's
    # "who owns a property" convention) ──
    "id",
    # ── capture shape (the shared primitive, #3551) ──
    "created_at",
    "turn_count",
    "is_episodic",
    "harness",
    "capture_lane",
    "actor_user_id",
    "machine_id",
    "model",
    "client_captured_at",
    "client_captured_at_source",
    # #3516 §D: the transcript stem the capture came from — set-if-absent
    # beside the client-instant pair, because a later store-sync backstop must
    # never relabel the stem the hook observed (#8798).
    "source",
    "capture_redactions",
    # ── extraction-attempt outcome ──
    "capture_ok",
    "capture_extractor",
    # ── entity-link counters ──
    "entity_links_attempted",
    "entity_links_created",
    # ── the derived-commit endpoint's counters ──
    "value_nodes_created",
    "value_nodes_held",
    "draft_count",
    "commit_count",
    "updated_at",
    # ── longmem-eval ingest provenance (tools/ lane) ──
    "lme_question_id",
    "lme_session_index",
    "lme_source_session_id",
)

SESSION_WRITERS: tuple[SessionWriter, ...] = (
    SessionWriter(
        key=CAPTURE_PRIMITIVE,
        kind="capture",
        lane="hosted + sdk (the shared primitive)",
        properties=frozenset({
            "created_at", "turn_count", "is_episodic", "harness",
            "capture_lane", "actor_user_id", "machine_id", "model",
            "client_captured_at", "client_captured_at_source", "source",
        }),
        note=(
            "The ONE capture writer (#3551). `created_at`/`capture_lane`/"
            "`client_captured_at`(+`_source`)/`actor_user_id`/`machine_id`/"
            "`model` are set-if-absent (`coalesce`, or a `CASE` tied to the "
            "instant for `client_captured_at_source`) so a later, weaker "
            "writer can never overwrite an earlier observation. `machine_id`/"
            "`model` are HOSTED-CLAIMED and knowingly ABSENT on the SDK lane "
            "(#2599) — the absence is recorded, not a gap to close here. "
            "`harness` is a plain `SET` made first-writer-wins by its caller's "
            "resolution, not by the clause."),
    ),
    SessionWriter(
        key="tortoise/sdk.py::_TURN_WRITE_CYPHER",
        kind="capture-aux",
        lane="hosted + sdk (the shared turn store)",
        properties=frozenset({"capture_redactions"}),
        note=(
            "The ONE `UNWIND $turns` turn-store statement (#3086), reached by "
            "the primitive above. It writes the per-session redaction count "
            "`capture_redactions` on the Session in the SAME statement as the "
            "turns, so the count and the rows it describes cannot be separated "
            "by a later failure (#4911)."),
    ),
    SessionWriter(
        key="tortoise/hosted_api.py::_capture_session_impl",
        kind="capture-aux",
        lane="hosted capture",
        properties=frozenset({
            "capture_ok", "capture_extractor",
            "entity_links_attempted", "entity_links_created",
        }),
        note=(
            "The hosted capture's OUTCOME record. `capture_ok` here is "
            "computed AFTER the post-write verification read (`skipped` ⇒ "
            "verb partial ⇒ False). `capture_ok`/`capture_extractor` are "
            "written only on a genuine attempt (fresh or #2335 retry) — a "
            "replay writes nothing. The entity-link counters are written only "
            "when the pass attempted a link and are OVERWRITTEN by the "
            "index-run writer below, so the counter is non-monotone."),
    ),
    SessionWriter(
        key="tortoise/sdk.py::capture_session",
        kind="capture-aux",
        lane="sdk capture",
        properties=frozenset({
            "capture_ok", "capture_extractor",
            "entity_links_attempted", "entity_links_created",
        }),
        note=(
            "The SDK twin of the hosted record. `capture_ok` here is computed "
            "BEFORE the surfaced-verification read, and a verification failure "
            "appends a warning (True stays) — the DOCUMENTED lane divergence. "
            "The two orderings are internally consistent and deliberately not "
            "force-aligned; `RETROFIT_VERDICTS['ordering_ambiguous']` records "
            "why the divergence is not retro-attributable."),
    ),
    SessionWriter(
        key="tortoise/hosted_api.py::_capture_abandoned_marker",
        kind="capture-aux",
        lane="hosted capture (crash/timeout marker)",
        properties=frozenset({"capture_ok", "capture_extractor"}),
        note=(
            "Marks a capture that was abandoned mid-flight (client "
            "disconnect, task cancel) `capture_ok=false` + the lane, so the "
            "session does not read as the legacy presumed-captured case and "
            "the next same-session POST re-attempts (#2335)."),
    ),
    SessionWriter(
        key="tortoise/hosted_api.py::_execute_commit_writes",
        kind="commit",
        lane="POST /v1/sessions/commit (derived payload)",
        properties=frozenset({
            "is_episodic", "created_at", "updated_at",
            "value_nodes_created", "draft_count", "commit_count",
        }),
        note=(
            "The derived-commit endpoint. Its payload carries NO turns, so it "
            "writes no `turn_count` and no extraction outcome — the epic pins "
            "that exemption (asserted by "
            "`test_commit_works_when_recording_disabled`). The counters are "
            "incremented under an exactly-once guard held on the "
            "`CommitRecord`, so a retry cannot double-count."),
    ),
    SessionWriter(
        key="tortoise/hosted_api.py::_commit_region",
        kind="commit",
        lane="POST /v1/sessions/commit (budget-hold branch)",
        properties=frozenset({
            "is_episodic", "created_at", "updated_at", "value_nodes_held",
        }),
        note=(
            "The >25 budget-HOLD branch: it writes neither the created nodes "
            "nor the commit counters, only the held count, and returns before "
            "the graph writes. Kept as its own row because it is the one "
            "commit-path writer that deliberately writes NO property the "
            "success branch writes beyond the two base clauses."),
    ),
    SessionWriter(
        key="tortoise/hosted_api.py::_relink_sessions_after_index",
        kind="index-run",
        lane="index-completion hook",
        properties=frozenset({"entity_links_attempted", "entity_links_created"}),
        note=(
            "The index-completion re-link (T1-P15): sweeps EVERY session and "
            "re-writes the link counters with THAT run's numbers, so a session "
            "already linked at capture time can end up with a lower "
            "`entity_links_created` than it holds edges (the under-count "
            "`audit_session_retrofit` quantifies)."),
    ),
    SessionWriter(
        key="tortoise/hosted_api.py::_seed_demo_graph",
        kind="demo",
        lane="opt-in sample (/v1/demo, /internal/demo)",
        properties=frozenset({"created_at", "turn_count"}),
        note=(
            "The opt-in sample seeder — a THIRD shape, deliberately excluded "
            "from the capture primitive (#3551): no `is_episodic`, no "
            "`capture_ok`, and 3 hand-written demo turns. Its Session id comes "
            "from `demo_session_id` (the ONE derivation, #3554); the session "
            "read surfaces do NOT yet exclude the demo Session — a recorded "
            "gap (**#8290**), not a claim of coverage."),
    ),
    SessionWriter(
        key="tortoise/projection/entities.py::_fold_session_recorded",
        kind="replay",
        lane="journal fold",
        properties=frozenset({
            "created_at", "capture_lane", "turn_count", "harness",
            "is_episodic", "actor_user_id",
            "client_captured_at", "client_captured_at_source", "source",
            "entity_links_attempted", "entity_links_created",
            "capture_ok", "capture_extractor", "capture_redactions",
        }),
        note=(
            "The `SessionRecorded` fold — replay's re-creation of the capture "
            "record. `created_at` and `capture_lane` ride the same "
            "coalesce-preserved branch as `actor_user_id` (`s.created_at="
            "coalesce(...)`, `s.capture_lane=coalesce(...)`), so the fold is a "
            "writer of both and the §4.8 owner lists must name it. It mirrors "
            "the live writers' truthiness guards so a "
            "rebuild cannot disagree with the graph it is rebuilding; a "
            "journal with no `SessionRecorded` (pre-#3664, or a journal-less "
            "lane) restores only the `_link_session` stub."),
    ),
    SessionWriter(
        key="tortoise/projection/entities.py::_link_session",
        kind="replay",
        lane="journal fold (PointAdded fallback)",
        properties=frozenset({"is_episodic"}),
        note=(
            "The minimal stub the capture loop's unjournaled Session MERGE "
            "gets on replay (#3947). Runs BEFORE the CONTAINS edge so a "
            "missing endpoint cannot make FalkorDB create a duplicate Point "
            "from a full-path MERGE."),
    ),
    SessionWriter(
        key="tortoise/projection/__init__.py::rebuild_all",
        kind="replay",
        lane="rebuild_all pre-wipe snapshot restore",
        properties=frozenset(SESSION_PROPERTIES) | {"id"},
        writes_whole_schema=True,
        note=(
            "`SET s += $props` over the pre-wipe `:Session` snapshot — the "
            "ONE writer whose property set IS the whole schema, which is why "
            "it is declared as such rather than enumerated. Any property a "
            "legacy writer stored rides this restore."),
    ),
    SessionWriter(
        key="tools/ask_spotcheck.py::merge_capture_session",
        kind="fixture",
        lane="ask-lane seeder (eval instrument)",
        properties=frozenset({
            "created_at", "turn_count", "is_episodic", "capture_redactions",
        }),
        note=(
            "The ask fixture's Session-half seeder. It re-implements the "
            "capture SHAPE (so the instrument measures the product's own "
            "store) rather than calling the primitive, which is why it is "
            "declared: it is a copy of `created_at`/`turn_count`/"
            "`is_episodic`/`capture_redactions` no drift test would otherwise "
            "catch."),
    ),
    SessionWriter(
        key="tools/longmem_eval/ingest.py::ingest_haystack",
        kind="eval-ingest",
        lane="longmem-eval v1 ingest",
        properties=frozenset({
            "created_at", "turn_count", "is_episodic",
            "lme_question_id", "lme_session_index", "lme_source_session_id",
        }),
        note=(
            "Deterministic-id eval ingest. `created_at` is the dataset's "
            "session date with NO `coalesce` (so a re-ingest converges) and "
            "the `lme_*` provenance triple is written here only."),
    ),
    SessionWriter(
        key="tools/longmem_eval/ingest_v2.py::_write_v2_phase_a",
        kind="eval-ingest",
        lane="longmem-eval v2 ingest",
        properties=frozenset({
            "created_at", "turn_count", "is_episodic",
            "lme_question_id", "lme_session_index", "lme_source_session_id",
        }),
        note="The v2 leg — the same six properties as v1, kept in step by hand.",
    ),
    SessionWriter(
        key="graph-scripts/backfill_is_episodic.py::BACKFILL_STATEMENTS",
        kind="backfill",
        lane="one-shot migration script",
        properties=frozenset({"is_episodic"}),
        note=(
            "The R-18 backfill: `MATCH (s:Session) WHERE s.is_episodic IS NULL "
            "SET s.is_episodic = true`. The only writer that exists to repair "
            "pre-flag legacy rows."),
    ),
)

SESSION_CLIENTS: tuple[SessionClient, ...] = (
    SessionClient(
        path="tortoise/__main__.py",
        transport="http",
        lane="hook | store_sync (the flag is the producer's)",
        marker='f"{api_url}/v1/sessions"',
        note=(
            "`_session_post` builds the ONE Python POST callable to "
            "`/v1/sessions`. `tortoise session capture`, `tortoise sessions "
            "import` (whose parsers produce the payload) and the capture "
            "spool's `session drain` ALL replay through it, so the transport "
            "has one implementation. `capture_lane` is set by the shipped "
            "hook ('hook') and omitted for a genuine backfill."),
    ),
    SessionClient(
        path="tortoise/pi-hooks/tortoise-capture.ts",
        transport="http",
        lane="hook",
        marker='post(cfg, "/v1/sessions", payload',
        note="The Pi recorder: session_shutdown → POST /v1/sessions (plus a "
             "session_start install-probe).",
    ),
    SessionClient(
        path="tortoise/cursor-hooks/session-end.sh",
        transport="cli",
        lane="hook",
        marker="sessions import --file",
        note="The Cursor recorder shells out to `tortoise sessions import` — "
             "it holds no HTTP transport of its own, so it is a caller of the "
             "`tortoise/__main__.py` client, not a second client.",
    ),
    SessionClient(
        path="tortoise/codex-hooks/session-end.sh",
        transport="cli",
        lane="hook",
        marker="sessions import --file",
        note="The Codex recorder shells out to `tortoise sessions import "
             "--harness codex` — the SAME command family as the Cursor "
             "recorder and the same `/v1/sessions` contract the Claude seam's "
             "`session capture` uses, so it is a caller of the "
             "`tortoise/__main__.py` client, not a second client.",
    ),
    SessionClient(
        path="tortoise/claude-hooks/session-end.sh",
        transport="cli",
        lane="hook",
        marker="session capture",
        note="The Claude Code recorder shells out to `tortoise session "
             "capture`, the `tortoise/__main__.py` client's other subcommand "
             "(it spools then drains), so it too is a caller of that client.",
    ),
    SessionClient(
        path="tortoise/capture_spool.py",
        transport="in-process",
        lane="store_sync",
        marker="classify_failure",
        note="The store-sync backstop and its spool: it replays entries "
             "through an INJECTED post callable (the `tortoise/__main__.py` "
             "transport), so it is a client of that client. `capture_lane="
             "'store_sync'` is its distinguishing mark.",
    ),
    SessionClient(
        path="tortoise/mcp_server.py",
        transport="in-process",
        lane="hook",
        marker="_capture_session_impl",
        note="The MCP capture tool calls `_capture_session_impl` IN PROCESS — "
             "no HTTP hop — so its response cannot drift from the REST "
             "endpoint's.",
    ),
)


def session_property_owners() -> dict[str, tuple[str, ...]]:
    """property → the writer keys that own it, sorted.

    Derived from :data:`SESSION_WRITERS`, never hand-maintained: a row that
    forgets a property, or a property with no row, is caught here.

    **Who is an owner:** a writer that carries a ``SET <alias>.<prop>`` clause
    for the property — that is what a :class:`SessionWriter`'s ``properties``
    enumerates (and what the scanner reads back at the site). A property used
    only as a ``MERGE`` key is NOT attributed: ten writers mint the node with
    ``MERGE (s:Session {id: …})``, but only ``rebuild_all``'s
    ``SET s += $props`` owns ``id``. Without that convention the ``id`` row
    reads as "a replay restore owns the Session id", which is the opposite of
    what the code does — the contradiction the §4.8 table inherits (#3554
    review).
    """
    owners: dict[str, list[str]] = {prop: [] for prop in SESSION_PROPERTIES}
    for writer in SESSION_WRITERS:
        for prop in writer.properties:
            owners.setdefault(prop, []).append(writer.key)
    return {prop: tuple(sorted(set(keys))) for prop, keys in owners.items()}


SESSION_PROPERTY_OWNERS: dict[str, tuple[str, ...]] = session_property_owners()

# The declaration binds at import: a property with no owning writer is a claim
# nothing writes, and a writer property outside the declared set is a field
# nobody declared. Both are the drift this module exists to make impossible.
_UNDECLARED = sorted(
    {p for w in SESSION_WRITERS for p in w.properties}
    - set(SESSION_PROPERTIES))
assert not _UNDECLARED, (
    f"writer-declared properties outside SESSION_PROPERTIES: {_UNDECLARED} — "
    "add them to the schema in the same reviewed diff")
assert all(SESSION_PROPERTY_OWNERS.values()), (
    "every declared :Session property must name an owning writer: "
    f"{[p for p, o in SESSION_PROPERTY_OWNERS.items() if not o]}")


# ── the D9 retrofit audit ──────────────────────────────────────────────────

#: How many offending sessions a report lists inline. The COUNTS are always
#: exhaustive; only the sample is bounded, and the bound is stated in the
#: report so a reader cannot mistake a sample for a total.
AUDIT_SAMPLE_LIMIT = 25

RETROFIT_VERDICTS: dict[str, dict[str, str]] = {
    "short_turn_count": {
        "decision": "recorded-honest-gap",
        "reason": (
            "`capture_ok` is the EXTRACTION-ATTEMPT outcome, not a "
            "store-integrity flag. Rewriting it to False on a short turn count "
            "would (a) re-label a store loss as an extraction failure — the "
            "dashboard would say `stored-extraction-failed` for a session "
            "whose extraction may well have succeeded — and (b) arm the #2335 "
            "TRUE-retry gate, so the next same-session POST re-runs extraction "
            "and can mint DUPLICATE claims. The shortfall needs no stored "
            "correction at all: it is derivable from graph truth (`s.turn_count` "
            "vs the Session's episodic turn members), which is exactly what "
            "`tools/session_retrofit_audit.py retrofit` reads. `capture_ok` "
            "attests an attempt, never a turn count."),
        "where_recorded": (
            "docs/ONTOLOGY.md §4.8 (`turn_count` + `capture_ok` rows) and "
            "docs/plans/2026-08-25-1714-memory-capture-onboarding.md "
            "§ '#3554 amendment'"),
        "falsification": (
            "A production graph reporting a non-zero short count would make "
            "the shortfall a READ concern — the Captured-sessions screen would "
            "need to serve it — which is a new, separately-owned product issue, "
            "not a property rewrite."),
    },
    "ordering_ambiguous": {
        "decision": "recorded-honest-gap",
        "reason": (
            "The pre/post-verification-read ordering is a property of the "
            "WRITER, and no writer stamps which lane computed `capture_ok`. "
            "`harness` is written by both lanes; `capture_extractor` names the "
            "extractor, not the lane; `machine_id`/`model` are hosted-CLAIMED "
            "and simply absent when the client did not send them, so their "
            "presence is not a lane marker either. Per-record retro-attribution "
            "is therefore NOT possible, and the report says so instead of "
            "printing an invented figure. The class IS bounded: both lanes "
            "compute the same `ok` and the hosted lane can only be EQUAL OR "
            "MORE PESSIMISTIC (it downgrades on `skipped`), so the only records "
            "whose value could differ from a re-computation are those reading "
            "`capture_ok = true` — the bound `audit_session_retrofit` reports."),
        "where_recorded": (
            "docs/ONTOLOGY.md §4.8 (`capture_ok` row) and the plan's "
            "§ '#3554 amendment'"),
        "falsification": (
            "A stored lane marker (a new property, or a marker on the capture "
            "receipt) would make the class attributable. Adding one is a "
            "schema change and belongs to a new issue, not to this audit."),
    },
    "entity_links_under_count": {
        "decision": "recorded-honest-gap",
        "reason": (
            "The counter is non-monotone by construction: the capture-time pass "
            "writes THIS capture's numbers and the index-completion re-link "
            "overwrites them with THAT run's, so a session can hold more "
            "`aboutObject` edges than `entity_links_created` records. The "
            "counter is per-run EVIDENCE — rewriting it to the live edge count "
            "would destroy what the runs actually reported and would be "
            "indistinguishable from a real pass. Graph truth is available "
            "without it, so nothing needs storing: the audit compares the "
            "recorded counter against the live edge count and reports the "
            "per-session under-count."),
        "where_recorded": (
            "docs/ONTOLOGY.md §4.8 (`entity_links_created` row) and the plan's "
            "§ '#3554 amendment'"),
        "falsification": (
            "An off-graph consumer that requires a monotone total (a dashboard "
            "figure, a bill) would WANT a reliable counter — that consumer "
            "should read the edges, and if it cannot, the fix is to make the "
            "counter monotone at the write sites, which is #3551's contract, "
            "not a backfill."),
    },
}


def audit_session_retrofit(g, *, graph: str | None = None) -> dict[str, Any]:
    """Quantify the three D9 retrofit classes over ONE graph.

    ``g`` is a FalkorDB graph handle (``sdk._get_proj().g``). Read-only: this
    function issues no write of any kind, and creates nothing but the graph
    client's own handle.

    The report never prints a class it did not measure: an empty graph is
    reported as 0 of 0 candidates, and the ordering class carries
    ``retro_quantifiable: False`` plus its reason rather than a number that
    would read like an attribution.
    """
    total = int(g.query("MATCH (s:Session) RETURN count(s)").result_set[0][0])

    # (a) short turn count. `turn_count` is the WRITER's claim (what the
    # capture window held); the Session's episodic, non-operator event members
    # are what the graph HOLDS. `capture_ok` is reported beside it so a reader
    # can see the class the epic named without a second query.
    short_rows = g.query(
        "MATCH (s:Session) WHERE s.turn_count IS NOT NULL "
        "OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point) "
        "  WHERE t.pointKind = 'event' AND t.is_episodic = true "
        "WITH s, count(t) AS stored_turns "
        "WHERE stored_turns < s.turn_count "
        "RETURN s.id, s.turn_count, stored_turns, s.capture_ok, "
        "       s.capture_extractor, s.harness, s.capture_lane "
        "ORDER BY s.id"
    ).result_set
    measured = int(g.query(
        "MATCH (s:Session) WHERE s.turn_count IS NOT NULL RETURN count(s)"
    ).result_set[0][0])
    short_all = [
        {"id": r[0], "turn_count": r[1], "stored_turns": r[2],
         "capture_ok": r[3], "capture_extractor": r[4], "harness": r[5],
         "capture_lane": r[6]}
        for r in short_rows
    ]
    short_ok = [r for r in short_all if r["capture_ok"] is True]

    # (b) the ordering class. See RETROFIT_VERDICTS — the upper bound is the
    # set of records a re-computation could disagree with.
    ambiguous = int(g.query(
        "MATCH (s:Session) WHERE s.capture_ok = true RETURN count(s)"
    ).result_set[0][0])

    # (c) the entity-link counter's under-count: recorded vs LIVE aboutObject
    # edges. The counter (`link_session_entities`' `created`) sums BOTH the
    # Session-origin and the turn-Point-origin edges — both `_link` call sites —
    # so the live side must count the SAME universe. A Session-origin-only
    # count understates it and can print ZERO for a real gap (a session whose
    # refs resolve on its turn points only): `0 > 3` is false, so the report
    # would read clean while the graph holds edges the counter does not.
    #
    # The two OPTIONAL MATCHes are aggregated in SEPARATE `WITH` stages on
    # purpose: matching both in one row scope multiplies the counts by the
    # cross-product (session_edges x turn_edges), which would over-report the
    # gap — the mirror-image defect.
    link_rows = g.query(
        "MATCH (s:Session) WHERE s.entity_links_created IS NOT NULL "
        "OPTIONAL MATCH (s)-[r1:aboutObject]->() "
        "WITH s, count(r1) AS session_edges "
        "OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point)-[r2:aboutObject]->() "
        "  WHERE t.pointKind = 'event' AND t.is_episodic = true "
        "WITH s, session_edges, count(r2) AS turn_edges "
        "WITH s, session_edges + turn_edges AS live_edges "
        "WHERE live_edges > s.entity_links_created "
        "RETURN s.id, s.entity_links_created, live_edges, "
        "       s.entity_links_attempted "
        "ORDER BY s.id"
    ).result_set
    links = [
        {"id": r[0], "entity_links_created": r[1], "live_edges": r[2],
         "entity_links_attempted": r[3],
         "under_counted_edges": int(r[2]) - int(r[1])}
        for r in link_rows
    ]

    return {
        "graph": graph,
        "sessions": total,
        "short_turn_count": {
            "candidates_measured": measured,
            "capture_ok_true": len(short_ok),
            "all_short": len(short_all),
            "turns_missing": sum(
                int(r["turn_count"]) - int(r["stored_turns"])
                for r in short_all),
            "sample": short_all[:AUDIT_SAMPLE_LIMIT],
            "sample_limit": AUDIT_SAMPLE_LIMIT,
            "sample_truncated": len(short_all) > AUDIT_SAMPLE_LIMIT,
        },
        "ordering_ambiguous": {
            "sessions": ambiguous,
            "retro_quantifiable": False,
            "reason": RETROFIT_VERDICTS["ordering_ambiguous"]["reason"],
        },
        "entity_links_under_count": {
            "sessions": len(links),
            "edges": sum(r["under_counted_edges"] for r in links),
            "sample": links[:AUDIT_SAMPLE_LIMIT],
            "sample_limit": AUDIT_SAMPLE_LIMIT,
            "sample_truncated": len(links) > AUDIT_SAMPLE_LIMIT,
        },
    }


def iter_declared_properties() -> Iterable[str]:
    """The declared property set, as an iterable — a convenience for callers
    that only need the names (the docs binding and the tests)."""
    return iter(SESSION_PROPERTIES)


#: The label + write verbs + client route + SCAN SCOPE the source predicate
#: keys on. Declared HERE, next to the inventory it proves, so the tool cannot
#: silently widen OR narrow its own predicate without the same reviewed diff
#: that changes this dict: the scanner COMPILES :data:`PREDICATE`'s
#: ``label`` (whitespace-tolerant, so the LABEL gate is a superset of the alias
#: and verb matchers rather than an exact-substring test that rejects legal
#: Cypher), ``write_verbs`` (binding ``<alias>`` to the variable in the matched
#: literal) AND its ``client_route`` (anchored to the route's full terminal
#: segment), and walks the TRACKED tree minus the path prefixes in ``excluded``
#: and the directory names in ``skip_parts``. The scope is the predicate, not
#: a private allow-list inside the tool: an allow-list let a new ``:Session`` writer
#: under an unlisted directory print INVENTORY CLEAN, the fail-open direction
#: of a guard whose whole purpose is to be trusted (#3554 review). Comments are
#: excluded by construction — the scan is AST-based, so a Cypher write shape in
#: a ``#`` comment or a docstring is not a site.
PREDICATE = {
    "label": ":Session",
    "write_verbs": (
        r"MERGE\s*\(\s*<alias>\s*:\s*Session",
        r"\bSET\s+<alias>\.",
    ),
    "client_route": "/v1/sessions",
    # Path PREFIXES never scanned: a test fixture or a doc table is not a site.
    "excluded": ("tests/", "docs/"),
    # Directory NAME parts never scanned: VCS metadata, third-party trees, and
    # generated/cache output. Everything else under the root IS scanned, so
    # this list must stay limited to trees that can hold no project source.
    "skip_parts": (
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next",
        "dist", "build", "bakeoff",
    ),
}

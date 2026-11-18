"""#13302 — the read whose question is "what ended, and how?".

THE DEFECT THESE PIN (measured by capstone #13038, `docs/evidence/13038-capstone/`):
a default-scope read answers *currency*, and correctly leaves ended records out, but
there was **no read whose question was the other one**. A customer asking *"has anything
we relied on been withdrawn?"* got a degraded corpus ranking (2 rows, 0 of 2 sharing a
word with the question, score 0.0); the retracted claim surfaced only under
``include_terminal`` — and *"what was planned and never happened?"* returned 0 rows
because the cancelled plan was correctly excluded and nothing enumerated the ended
population. So both answers read as "nothing has ever ended".

WHAT IS PINNED HERE, and each one is a behaviour (a returned SET), never "a predicate
was called":

* a withdrawn claim is discoverable **without its id** — no query, no terminal flag,
  no name guess;
* a cancelled plan is discoverable, and a **live** plan is never returned as
  "never happened" (a live record is not in the ended population at all);
* the answer says **how** it ended — the lifecycle word, the successor it names, or the
  terminal stage with its ``outcome`` — and the time it ended where the record carries
  one;
* the **default** read is unchanged: the same records are still excluded, so this is an
  additional way to ask rather than a change to what "current" means.

The selector is the ONE shared currency predicate (`live._terminal_expression`, taken in
the positive direction), so the ended population cannot drift from what every reader
filters on. Two shapes below prove it rather than assert it: a record ended ONLY by the
position axis (`isClosed` with no status at all, and a terminal stage with no status) is
returned, and a record whose ``status`` is a word the model does not treat as an ending is
not.

Lane: docker (a live FalkorDB) — the read is Cypher over a real store, so an embedded
fake would test nothing. Skips when no store is reachable (the shipped convention,
`tests/_live_utils.py`).
"""
from __future__ import annotations

import ast
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _live_utils  # noqa: E402, RUF100


def _probe_graph_name() -> str:
    """A PER-RUN name for the availability probe's OWN graph (#13302).

    Same policy, and the same shape, as the #13290 probe in
    ``tests/test_verbatim_index_cohort_8429.py``: ``from_uri`` defaults to
    ``ensure_indexes=True``, whose schema DDL **CREATES** the graph it names, so
    an unnamed probe graph names a graph this probe does not own — on the
    ambient candidate, the SHARED env-URI graph that ``_uri()`` hands to every
    ``sdk`` fixture below. The fixed ``tortoise_test_13302`` path this file
    builds its candidates from is not a per-run MINT shape either (5 hex against
    a floor of 8), so it meets neither cleanup arm
    (``tests/_embedded.py::_SERVER_WIPE_PREFIXES``,
    ``tests/_live_utils.py::_PER_RUN_GRAPH_NAME_RE``).

    Pinned by ``test_the_probe_graph_is_named_at_the_call``.
    """
    return f"tortoise_test_13302_probe_{uuid.uuid4().hex[:10]}"


def _probe(candidates):
    env_uri = os.environ.get("TORTOISE_DB_URI")
    # ⛔ THE PROBE'S OWN GRAPH IS NAMED, per-run, and reaped by construction
    # (`_probe_graph_name`). The census this satisfies is
    # `test_derived_names.py::test_from_uri_sites_resolve_test_prefixed`, which
    # was RED on `main` for this site (measured at `f462ad3acb`); the whole
    # rationale — `ensure_indexes` DDL CREATES the named graph, so an unnamed
    # probe graph is a graph nobody can own — lives on the helper above.
    probe_graph = _probe_graph_name()
    for uri in candidates:
        if not uri:
            continue
        proj = None
        try:
            from tortoise.projection import FalkorProjection

            proj = FalkorProjection.from_uri(uri, graph_name=probe_graph)
            proj.g.query("RETURN 1")
            return True, uri
        except Exception:
            if uri == env_uri:
                break
            continue
        finally:
            if proj is not None:
                try:  # noqa: SIM105
                    proj.close()
                except Exception:
                    pass
    return False, None


_AVAILABLE, _WORKING_URI = _probe([
    os.environ.get("TORTOISE_DB_URI"),
    _live_utils.docker_uri("tortoise_test_13302"),
    _live_utils.legacy_uri("tortoise_test_13302"),
])


def test_the_probe_graph_is_named_at_the_call():
    """#13302 — the probe's `from_uri(` PASSES the minted graph name.

    The census (`tests/test_derived_names.py::test_from_uri_sites_resolve_test_prefixed`)
    only sees the PRESENCE of a `graph_name=` keyword, and the helper's own
    vocabulary can be right while the call site is wired to something else —
    measured on the #13290 sibling: pointing its call at the old fixed literal
    left both the census `[]` and that pin GREEN. So this reads the ONE
    `from_uri(` call statically and requires it to pass `probe_graph`, itself
    assigned from the per-run mint. Hermetic — no server, no import side effect.
    """
    from tests._embedded import _SERVER_WIPE_PREFIXES

    name = _probe_graph_name()
    assert name.startswith(_SERVER_WIPE_PREFIXES), (
        f"{name!r} is outside the session sweep's prefix family "
        f"{_SERVER_WIPE_PREFIXES} — nothing would ever drop it (#13302)")
    assert _live_utils._PER_RUN_GRAPH_NAME_RE.match(name), (
        f"{name!r} is not a per-run MINT shape, so `drop_test_graph` refuses to "
        "delete it and the graph leaks (#13302)")
    assert name != _probe_graph_name(), (
        "the probe's graph must be minted per RUN — a stable spelling is a "
        "shared graph, and `from_uri(ensure_indexes=True)` creates it (#13302)")

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    from_uri_calls = [node for node in ast.walk(tree)
                      if isinstance(node, ast.Call)
                      and getattr(node.func, "attr", None) == "from_uri"]
    assert len(from_uri_calls) == 1, (
        f"expected ONE `from_uri(` call in this module, found "
        f"{len(from_uri_calls)} — this pin reads that one site (#13302)")
    kw = {k.arg: k.value for k in from_uri_calls[0].keywords}
    assert "graph_name" in kw, (
        "the probe's `from_uri(` passes no `graph_name=` — the census reds and "
        "the graph its schema DDL creates belongs to nobody (#13302)")
    assert isinstance(kw["graph_name"], ast.Name) and \
        kw["graph_name"].id == "probe_graph", (
        "the call site's `graph_name=` is not the MINTED `probe_graph` — a "
        "literal there feeds `ensure_indexes`' DDL into ONE graph shared by "
        "every lane and CI run, and the census still passes because it only "
        "sees the keyword (#13302)")
    minted = [n for n in ast.walk(tree)
              if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "probe_graph"
                      for t in n.targets)]
    assert any(isinstance(n.value, ast.Call)
               and getattr(n.value.func, "id", None) == "_probe_graph_name"
               for n in minted), (
        "`probe_graph` is not assigned from `_probe_graph_name()` — the call "
        "site is wired to something other than the per-run mint (#13302)")


_docker_only = pytest.mark.skipif(
    not _AVAILABLE,
    reason="live FalkorDB not available (the ended read is Cypher over a real store)")

#: A pack-style pipeline a plan kind declares. The stage NAMES are deliberately NOT
#: used by the read (only the slot is, design §4.1), so one stage is named with a word
#: the model never treats as an ending and the terminal ones with its own outcome.
PLAN_PIPELINE = {
    "defaultStage": "scheduled",
    "stages": [
        {"id": "scheduled", "label": "Scheduled", "order": 0, "slot": "entry"},
        {"id": "missed", "label": "Missed", "order": 1, "slot": "terminal",
         "outcome": "lost"},
        {"id": "cancelled", "label": "Cancelled", "order": 2, "slot": "terminal",
         "outcome": "withdrawn"},
        {"id": "held", "label": "Held", "order": 3, "slot": "terminal",
         "outcome": "completed"},
    ],
}


def _uri() -> str:
    return (os.environ.get("TORTOISE_DB_URI")
            or (_WORKING_URI
                or _live_utils.docker_uri("tortoise_test_13302", password=None)))


@pytest.fixture()
def sdk(monkeypatch):
    """A fresh graph namespace and an SDK pointed at the live store."""
    monkeypatch.setenv("TORTOISE_DB_URI", _uri())
    from tortoise.sdk import TortoiseSDK

    return TortoiseSDK(namespace=f"test13302_{uuid.uuid4().hex[:10]}")


def _ended(sdk, **kwargs):
    """The read under test: the ended population of a family."""
    kwargs.setdefault("limit", 50)
    return sdk.tortoise_fts_query(None, ended_scope=True, **kwargs)


def _ids(rows) -> set[str]:
    return {r["id"] for r in rows}


# ── the two questions the capstone could not answer ─────────────────────────────


@_docker_only
def test_a_withdrawn_claim_is_discoverable_without_knowing_its_id(sdk):
    """Q6. The answer must arrive with NO id, NO query and NO terminal flag."""
    keep = sdk.create_point(kind="statement", content="the venue is booked")
    dead = sdk.create_point(kind="statement", content="The beach club confirmed our booking.")
    sdk.retract_point(dead["id"])

    rows = _ended(sdk, entity_type="point")
    by_id = {r["id"]: r for r in rows}

    assert dead["id"] in by_id, (
        "the retracted claim must be served by the ended read without its id being "
        "supplied — that is the whole question (#13302)"
    )
    # The positive control: a live claim must not appear, or the read would just be
    # "everything".
    assert keep["id"] not in by_id
    assert by_id[dead["id"]]["status"] == "retracted"


@_docker_only
def test_a_cancelled_plan_is_discoverable_and_a_live_plan_is_not_never_happened(sdk):
    """Q5. The plan is found by its STATE, and a live plan is not in the population."""
    from tortoise import pipeline

    pipeline.seed_pipeline(sdk._get_proj().g, "core:testplan", PLAN_PIPELINE)
    cancelled = sdk.create_object("Thursday partner call", objectKind="core:testplan")
    pipeline.advance_stage(sdk, cancelled["id"], "core:testplan", "cancelled")
    live = sdk.create_object("Launch event", objectKind="core:testplan")

    rows = {r["id"]: r for r in _ended(sdk, entity_type="object")}

    assert cancelled["id"] in rows
    assert live["id"] not in rows, (
        "a plan that has not ended must never be served as 'planned and never "
        "happened' — it is not in the ended population at all"
    )
    stage = rows[cancelled["id"]]["stage"]
    assert stage["slot"] == "terminal"
    assert stage["outcome"] == "withdrawn"
    # Nothing records that it happened — the other half of the answer.
    assert rows[cancelled["id"]]["linkedEvents"] == []


@_docker_only
def test_a_plan_that_happened_carries_its_occurrence(sdk):
    """`linkedEvents` separates "planned, never happened" from "planned and held".

    Without this the two are the same row: both ended on a terminal stage. The
    occurrence is an Event wired `aboutObject` — the product's own link.
    """
    from tortoise import pipeline

    pipeline.seed_pipeline(sdk._get_proj().g, "core:testplan", PLAN_PIPELINE)
    held = sdk.create_object("Launch event", objectKind="core:testplan")
    sdk.create_event("Launch event held", "occurrence", aboutObject=held["id"])
    pipeline.advance_stage(sdk, held["id"], "core:testplan", "held")

    row = {r["id"]: r for r in _ended(sdk, entity_type="object")}[held["id"]]
    assert row["stage"]["outcome"] == "completed"
    assert row["linkedEvents"], "the occurrence event must be visible on the row"


# ── "how it ended" ─────────────────────────────────────────────────────────────


@_docker_only
def test_a_superseded_record_names_its_successor(sdk):
    old = sdk.create_point(kind="decision", content="the entry tier is 29")
    new = sdk.create_point(kind="decision", content="the entry tier is 39")
    sdk.supersede_point(old["id"], new["id"])

    row = {r["id"]: r for r in _ended(sdk, entity_type="point")}[old["id"]]
    assert row["status"] == "superseded"
    # A Point names its successor by the CORRECTS edge, NOT by a property.
    assert row["successorId"] == new["id"]


@_docker_only
def test_a_deprecated_record_says_nothing_replaced_it(sdk):
    gone = sdk.create_object("Old onboarding flow", objectKind="process")
    sdk.update_knowledge(gone["id"], status="deprecated")

    row = {r["id"]: r for r in _ended(sdk, entity_type="object")}[gone["id"]]
    assert row["status"] == "deprecated"
    assert row["successorId"] is None


@_docker_only
def test_an_ended_record_carries_the_time_it_ended_where_it_has_one(sdk):
    """A supersede stamps a window end; the read reports it (and the raw fields).

    `endedAt` must be the record's WINDOW END (event time), never the ingestion
    stamp beside it. `supersede_point` mints `validTo` and `expiredAt`/`updatedAt`
    from the same clock, so a fast fixture can leave them equal and the ORDER
    unobservable here — the mutating edit (`_TIME_FIELDS` putting `updatedAt`
    first, or dropping `validTo`) is pinned structurally by
    `test_the_ending_time_prefers_the_window_end_over_the_ingestion_stamp`
    below. This case keeps the behavioural half honest: the served value IS the
    window end, not merely "one of the record's time fields".
    """
    old = sdk.create_point(kind="decision", content="the tier is 29")
    new = sdk.create_point(kind="decision", content="the tier is 39")
    sdk.supersede_point(old["id"], new["id"])

    row = {r["id"]: r for r in _ended(sdk, entity_type="point")}[old["id"]]
    assert row["endedAt"], "a supersede stamps the predecessor's end — it must be served"
    assert row["endedAt"] == row["validTo"], (
        "the window END is the event time and outranks the ingestion stamp: `endedAt` "
        "must be `validTo`, not `expiredAt`/`updatedAt` (#13302 review F3.2)"
    )


def test_the_ending_time_prefers_the_window_end_over_the_ingestion_stamp():
    """The `endedAt` ORDER is the statement's, so it is pinned where it lives.

    #13302 review F3.2: `supersede_point` writes `validTo` (the successor's start)
    and `expiredAt`/`updatedAt` (now) from the same clock, so a live fixture cannot
    separate the two instants deterministically — the previous assertion accepted
    EITHER field and stayed green when `_TIME_FIELDS` put `updatedAt` first or
    dropped `validTo` entirely. The order is a property of the `coalesce(...)` the
    store evaluates, so it is asserted on the generated statement (hermetic, no
    store): event time first, ingestion time last.
    """
    from tortoise import ended

    fields = ended._TIME_FIELDS
    assert fields[0] == "validTo", (
        "the ending time must resolve the record's WINDOW END first — an ingestion "
        "stamp (`expiredAt`/`updatedAt`) reports when we learned of the ending, not "
        "when it took effect"
    )
    assert fields.index("validTo") < fields.index("expiredAt") < fields.index("updatedAt")
    cypher = ended.build_cypher("point", narrow_kinds=False)
    assert f"coalesce(n.{', n.'.join(fields)})" in cypher, (
        "the statement must resolve `ended_at` in `_TIME_FIELDS` order — a row "
        "whose ORDER differs from the declared one is the same drift the "
        "behavioural case cannot see"
    )


@_docker_only
def test_the_snake_successor_spelling_is_read_too(sdk):
    """`superseded_by` is a SECOND spelling a tree can carry — and is read.

    #13302 review F3.1: `_SUCCESSOR_PROPS` reads both spellings because a row can
    carry the shadow SNAKE property (`superseded_by`) instead of the camel one —
    written before #12914's guard, or landed by a record-sourced rebuild (the
    record's successor COLUMN is `superseded_by`, `sdk._SUCCESSOR_COLUMN`). There
    was no case exercising it, so dropping the snake half from `_SUCCESSOR_PROPS`
    left every row green and the documented reason unpinned.
    """
    gone = sdk.create_object("Old onboarding flow", objectKind="process")
    sdk.update_knowledge(gone["id"], status="deprecated")
    # ⛔ WRITTEN DIRECTLY, not through `update_knowledge`: since #12914 the revise
    # door REFUSES a snake-only carrier (`_successor_named_by` requires the CAMEL
    # spelling every camel-keyed reader resolves), so no SDK call can produce this
    # row any more. The STATE is still real — it is what a tree written before
    # that guard holds, and the record's own successor COLUMN is `superseded_by`
    # (`sdk._SUCCESSOR_COLUMN`), so a record-sourced rebuild lands the snake
    # property — which is exactly why the read resolves both spellings.
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n.superseded_by = $succ",
        params={"id": gone["id"], "succ": "row-v2"},
    )

    row = {r["id"]: r for r in _ended(sdk, entity_type="object")}[gone["id"]]
    assert row["status"] == "deprecated"
    assert row["successorId"] == "row-v2", (
        "a successor recorded under the snake spelling must be reported — a read "
        "that looked at `supersededBy` alone would say 'nothing replaced it' for "
        "a record that names one"
    )


# ── the selector is the ONE shared currency predicate ──────────────────────────


@_docker_only
def test_a_record_ended_only_by_the_position_axis_is_returned(sdk):
    """`isClosed` with NO status at all — the shape #13034 taught the predicate to read.

    A read that selected on a status list would miss it, which is the defect the
    shared predicate exists to prevent: this test fails for any second vocabulary.
    """
    closed = sdk.create_object("Closed by marker only", objectKind="process",
                              isClosed=True)

    rows = {r["id"]: r for r in _ended(sdk, entity_type="object")}
    assert closed["id"] in rows
    assert rows[closed["id"]]["status"] in (None, "")
    assert rows[closed["id"]]["isClosed"] is True


@_docker_only
def test_a_word_the_model_does_not_treat_as_an_ending_is_not_returned(sdk):
    """The population is "ended", not "has a status"."""
    live = sdk.create_object("Still current", objectKind="process", status="live")
    draft = sdk.create_point(kind="statement", content="a draft claim", status="draft")

    assert live["id"] not in _ids(_ended(sdk, entity_type="object"))
    assert draft["id"] not in _ids(_ended(sdk, entity_type="point"))


# ── the default is unchanged ───────────────────────────────────────────────────


@_docker_only
def test_the_default_read_is_byte_for_byte_the_current_picture(sdk):
    """`ended_scope` is an ADDITION: nothing about "current" moves."""
    keep = sdk.create_point(kind="statement", content="the venue is booked")
    dead = sdk.create_point(kind="statement", content="the venue booked the beach club")
    sdk.retract_point(dead["id"])

    default = sdk.tortoise_fts_query("venue", entity_type="point", limit=50)
    default_ids = _ids(default)

    assert keep["id"] in default_ids
    assert dead["id"] not in default_ids, (
        "the default read must still exclude the retracted claim — an ended-scope "
        "parameter may not change what 'current' means"
    )


# ── bounded, ordered, and honest about its own completeness ────────────────────


@_docker_only
def test_the_page_is_bounded_and_reports_whether_it_is_the_whole_population(sdk):
    for i in range(3):
        pid = sdk.create_point(kind="statement", content=f"withdrawn claim {i}")
        sdk.retract_point(pid["id"])

    full_tr: dict = {}
    full = sdk.tortoise_fts_query(None, entity_type="point", ended_scope=True,
                                  limit=50, _truncation_out=full_tr)
    cut_tr: dict = {}
    cut = sdk.tortoise_fts_query(None, entity_type="point", ended_scope=True,
                                 limit=2, _truncation_out=cut_tr)

    assert len(full) == 3 and len(cut) == 2
    # A page that is not full proves the population is the rows returned…
    assert full_tr["truncated"] is False
    assert full_tr.get("_exact") is True
    # …and a full page does not claim to be complete.
    assert cut_tr["truncated"] is True
    assert cut_tr.get("_exact") is False


@_docker_only
def test_a_subject_is_served_by_its_own_carriers(sdk):
    """The read is one shape across the families, each read by ITS OWN spellings."""
    subj = sdk.create_subject("Acme", subjectKind="organization")
    sdk.supersede_knowledge(subj["id"], sdk.create_subject(
        "Acme Corp", subjectKind="organization")["id"])

    rows = {r["id"]: r for r in _ended(sdk, entity_type="subject")}
    assert subj["id"] in rows
    assert rows[subj["id"]]["name"] == "Acme"
    assert rows[subj["id"]]["status"] == "superseded"
    assert rows[subj["id"]]["successorId"]


# ── refusals: a state question is not a relevance read ─────────────────────────


@_docker_only
def test_a_query_beside_ended_scope_is_refused_rather_than_dropped(sdk):
    """A silently dropped query would serve a whole population as the answer."""
    with pytest.raises(ValueError) as exc:
        sdk.tortoise_fts_query("has anything been withdrawn", entity_type="point",
                              ended_scope=True)
    message = str(exc.value)
    assert "ended_scope" in message
    assert "query" in message
    # The repair is named (AX §5): an admissible refusal leaves a writable way out.
    assert "omit" in message


@_docker_only
def test_a_family_without_a_lifecycle_is_refused_not_answered_empty(sdk):
    """`[]` is a factual claim; an Event has no ending this model defines."""
    with pytest.raises(ValueError) as exc:
        sdk.tortoise_fts_query(None, entity_type="event", ended_scope=True)
    assert "event" in str(exc.value).lower()


#: Every retrieval keyword the normal path uses to shrink, expand, re-rank,
#: re-weight, time-scope or enrich the row set — and therefore every one that
#: must be REFUSED beside ``ended_scope`` rather than dropped in silence (#13302
#: review F4). One entry per keyword so deleting a line from the refused tuple
#: reds the row that names it: the previous shape claimed "every retrieval knob"
#: in a comment while the tuple held eight and the rest were dropped silently.
_REFUSED_BESIDE_ENDED_SCOPE = (
    ("query", {"query": "has anything been withdrawn"}),
    ("threshold", {"threshold": 0.5}),
    ("min_confidence", {"min_confidence": 0.5}),
    ("order_by", {"order_by": "confidence"}),
    ("relationship_filter", {"relationship_filter": "mitigates"}),
    ("traversal_path", {"traversal_path": "Point"}),
    ("claim_class", {"claim_class": "asserted"}),
    ("exclude_status", {"exclude_status": ["retracted"]}),
    ("exclude_turn_echo_session", {"exclude_turn_echo_session": "sess-1"}),
    ("graph_ranker", {"graph_ranker": "pointrank"}),
    ("structural_kind", {"structural_kind": "statement"}),
    ("structural_hops", {"structural_hops": 2}),
    ("pool_size", {"pool_size": 40}),
    ("fusion_weights", {"fusion_weights": {"rrf": 1.0}}),
    ("fusion_k", {"fusion_k": 40}),
    ("recency_field", {"recency_field": "updatedAt"}),
    ("recency_boost", {"recency_boost": 0.3}),
    ("query_date", {"query_date": "2026-01-01"}),
    ("time_aware", {"time_aware": True}),
    ("keep_numeric", {"keep_numeric": True}),
    ("search_keys_prf", {"search_keys_prf": True}),
    ("entity_key_expansion", {"entity_key_expansion": True}),
    ("w4_enrich", {"w4_enrich": False}),
)


@_docker_only
@pytest.mark.parametrize("knob, kwargs", _REFUSED_BESIDE_ENDED_SCOPE,
                         ids=[n for n, _ in _REFUSED_BESIDE_ENDED_SCOPE])
def test_every_retrieval_knob_beside_ended_scope_is_refused(sdk, knob, kwargs):
    """The refusal is a CLOSED LIST, and this test IS the list (#13302 review F4).

    A silently dropped knob serves a state population as if the caller's scope had
    been applied — `exclude_turn_echo_session` returns rows a caller asked to
    EXCLUDE, `structural_hops` expands, `order_by`/`fusion_*`/`recency_*` re-rank,
    `query_date`/`time_aware` time-scope. Each one raises naming itself, so the
    caller learns the arm cannot record what they asked for.
    """
    call = dict(kwargs)
    query = call.pop("query", None)
    with pytest.raises(ValueError) as exc:
        sdk.tortoise_fts_query(query, entity_type="point", ended_scope=True, **call)
    message = str(exc.value)
    assert "ended_scope" in message
    assert knob in message, (
        f"the refusal must name the keyword it cannot apply: {knob!r} not in {message!r}"
    )


@_docker_only
def test_include_terminal_beside_ended_scope_is_a_no_op_not_a_refusal(sdk):
    """`include_terminal` is the ONE accepted knob, and it cannot change the rows.

    #13302 review F4/F5: the ended population is the terminal one by construction,
    so the flag neither adds nor removes a row here. Refusing it would refuse a
    caller for asking for what they are already getting; honouring it as a
    retrieval knob would mean a live record could enter a state enumeration.
    """
    keep = sdk.create_point(kind="statement", content="the venue is booked")
    dead = sdk.create_point(kind="statement", content="the club withdrew the booking")
    sdk.retract_point(dead["id"])

    with_flag = _ids(sdk.tortoise_fts_query(None, entity_type="point", limit=50,
                                            ended_scope=True, include_terminal=True))
    without = _ids(sdk.tortoise_fts_query(None, entity_type="point", limit=50,
                                          ended_scope=True))
    assert with_flag == without, (
        "include_terminal must not change the ended population — the flag is a "
        "no-op here, not a scope"
    )
    assert dead["id"] in with_flag
    assert keep["id"] not in with_flag, (
        "a LIVE record must never enter the ended population, flag or no flag"
    )


@_docker_only
def test_a_direct_caller_naming_one_kind_is_narrowed_not_given_the_family(sdk):
    """#13302 review F2: `kind` narrows the enumeration; it is never dropped.

    The read path always passes the EXPANDED `kinds`, so the singular `kind` looks
    wired through the SDK while a direct caller of `enumerate_ended` got the whole
    family back — an enumeration of everything presented as their scope.
    """
    from tortoise import pipeline
    from tortoise.ended import enumerate_ended

    pipeline.seed_pipeline(sdk._get_proj().g, "core:testplan", PLAN_PIPELINE)
    plan = sdk.create_object("Thursday partner call", objectKind="core:testplan")
    pipeline.advance_stage(sdk, plan["id"], "core:testplan", "cancelled")
    other = sdk.create_object("Old onboarding flow", objectKind="process")
    sdk.update_knowledge(other["id"], status="deprecated")

    rows = {r["id"]: r for r in enumerate_ended(
        sdk._get_proj().g, entity_type="object", kind="core:testplan")}

    assert plan["id"] in rows
    assert other["id"] not in rows, (
        "a caller that named ONE kind must not be served every ended Object of "
        "the family — `kind` must narrow the statement, never be accepted and "
        "ignored"
    )

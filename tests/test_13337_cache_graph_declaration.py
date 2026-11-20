"""A record tenant has ONE cache graph, DECLARED — read, never derived (#13337).

THE DEFECT THIS PINS, and it is the residual half of #12943. A record tenant
``(org_id, graph_id)`` does not name one cache graph when the name is *derived*
from the credential that wrote:

===========================  ==================================  =====================
credential class             cache graph the write lands in       derived spelling
===========================  ==================================  =====================
graph-bound                  ``org_<org>_<gid>``                  ``org_<org>_<gid>`` ✅
org-wide (default graph)     ``org_<org>``                        ``org_<org>_<gid>`` ⛔
===========================  ==================================  =====================

The record files BOTH writes under the same tenant, so the second row made
``record-reconcile`` report ``missing_in_cache = N`` for a tenant whose cache was
complete — the "the cache never sees the writes" shape #12943 was reopened for.
Measured end to end below, on a real Postgres record and a real FalkorDB
(``docs/evidence/12943-cache-leg/orgwide-two-graphs.json``): one org-wide
``_data_sdk`` write of an Object and an Event, then the reconciler on both names.

    org_<org>_<gid> (derived)   missing_in_cache=2   divergent   ← FALSE
    org_<org>      (the FACT)   missing_in_cache=0   ← and one field mismatch
                                on the Event, which is #12949's root, not this one

WHAT THIS FILE PINS, in order:

1. **The declaration's SOURCE ORDER** — the operator's stated name, then the
   control-plane fact (``public.graphs.namespace``, the same read
   ``hosted_api._assert_graph_owned`` makes), then the registry spelling — and
   the third one is LABELLED, so a verdict computed on an assumed coordinate can
   never read like one computed on a read one.
2. **The verbs no longer REFUSE simply because the flag is missing.**
   ``record-reconcile`` and ``record-restore`` required ``--cache-graph`` because
   guessing opened an empty graph and graded a coherent cache divergent. A fact
   exists now, so where a fact ANSWERS the flag is optional — and where nothing
   answers, BOTH verbs still refuse (``record-reconcile``: no verdict on an
   assumed coordinate, the ``#13337`` ruling; ``record-restore``: it wipes). The
   refusal that remains is *"nothing could answer"*, and it names the spelling it
   would have used instead of being blind.
3. **The end-to-end acceptance #13337 states**: a tenant written through an
   ORG-WIDE credential reconciles ``clean`` on the name the control plane holds
   for it, with no ``--cache-graph`` — and the derived spelling is still
   ``divergent``, which is why the two are reported with different sources.

⛔ **WHAT IS COVERED STORE-FREE, AND WHAT IS NOT — declared, not implied.** The
coordinate PLUMBING is asserted on every run, with no stores: the declaration's
source order, the control-plane read's filters, both verbs' derived-arm refusals
(including the whitespace-only flag that used to reach the wipe path), the
disagreement WARNING, the ``cache_graph``/``cache_graph_source`` fields in the
``--json`` payload, and the one line that hands the fact to ``record-restore``'s
composition (spied). ⚠️ **The two ``_both_stores()`` legs are the residual:** they
need BOTH a Postgres record and a FalkorDB, and NO CI job provisions both —
``test-record-postgres`` exports a record DSN and no ``TORTOISE_DB_URI`` (its
explicit comment says so, and its leg gate FAILS on any skip, #9551), while the
sharded ``test`` job arms a graph and no record DSN. So the cross-store equality
(``missing_in_cache: 0`` on the read name) executes on a lane that has both
stores and nowhere else; the plumbing that would break it is covered above.
⛔ **Tracked as #14077**, because arming it is a CI-infrastructure change
(provision both stores in one job + live with #9551's any-skip gate), not a test
edit — and a claim that reads as covered while nothing executes it is the
false-confidence direction.

⛔ What is NOT here, named so the absence is visible: the write path does not
gain a control-plane read (a per-write lookup would put a network call on the hot
path, and ``cache_leg.note_tenant_graph``'s documented silence for the
graph-id-less shape is correct until one exists); relocating an org's default
graph off ``org_<org>`` is #10180's guarantee and an OWNER decision, not a lane's;
and the Event ``field_mismatches`` on the fact-named graph is #12949's root
(projection fidelity — the fold writes defaults the record leaves NULL), pinned
there rather than folded in here.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tortoise import __main__ as cli  # noqa: E402
from tortoise.cache_graph import (  # noqa: E402
    SOURCE_CONTROL_PLANE,
    SOURCE_DERIVED,
    SOURCE_FLAG,
    CacheGraph,
    canonical_cache_graph_name,
    declared_cache_graph,
    namespace_from_control_plane,
)

RECORD_DB_ENV = "TEST_RECORD_DB_URI"


# ── 1. the declaration ───────────────────────────────────────────────────────


def test_a_stated_name_wins_and_says_so():
    """The operator's word is a fact and is never re-derived.

    A ``--cache-graph`` that a deployment has used for a year may be a name no
    control plane knows (a hand-built graph); silently preferring the namespace
    would grade a graph nobody reads.
    """
    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              cache_graph="hand_built_graph",
                              read_namespace=lambda o, g: "org_orgA_g_1")
    assert got == CacheGraph(name="hand_built_graph", source=SOURCE_FLAG,
                             detail="stated by the caller")
    assert got.is_fact


def test_the_control_plane_fact_wins_over_the_registry_spelling():
    """⛔ THE FIX. For the org's DEFAULT graph the two disagree, and the fact is
    right: its namespace is the legacy ``org_<org>``, which no derivation of
    ``org_<org>_<gid>`` can produce."""
    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              read_namespace=lambda o, g: "org_orgA")
    assert got.name == "org_orgA"
    assert got.source == SOURCE_CONTROL_PLANE
    assert got.is_fact
    assert got.name != canonical_cache_graph_name("orgA", "g_1")


def test_no_fact_falls_back_to_the_registry_spelling_and_LABELS_it():
    """The fallback is the old behaviour; what changes is that it can no longer
    be mistaken for a read coordinate."""
    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              read_namespace=lambda o, g: None)
    assert got.name == "org_orgA_g_1"
    assert got.source == SOURCE_DERIVED
    assert not got.is_fact, "a derived coordinate must never claim to be a fact"


@pytest.mark.parametrize("answer", [None, "", "   ", 7, ["org_orgA"]])
def test_a_non_fact_answer_is_not_a_fact(answer):
    """A blank or non-string namespace is ABSENCE, not a name.

    ``public.graphs.namespace`` is operator-supplied; a blank one would otherwise
    become a graph name of ``""`` — an empty graph graded as the tenant's cache,
    i.e. the wrong-verdict shape this declaration exists to end.
    """
    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              read_namespace=lambda o, g: answer)
    assert got.source == SOURCE_DERIVED
    assert got.name == canonical_cache_graph_name("orgA", "g_1")


def test_a_reader_that_RAISES_is_not_a_fact():
    """A control plane that cannot answer must not turn a sweep into a crash —
    the same posture ``record-reconcile``'s other bounded reads take."""
    def _boom(_org, _gid):
        raise RuntimeError("control plane unreachable")

    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              read_namespace=_boom)
    assert got.source == SOURCE_DERIVED


def test_a_stated_name_is_whitespace_trimmed_and_a_blank_one_is_not_stated():
    assert declared_cache_graph(org_id="o", graph_id="g",
                               cache_graph="  org_o  ").name == "org_o"
    assert declared_cache_graph(org_id="o", graph_id="g",
                               cache_graph="   ").source == SOURCE_DERIVED


def test_the_control_plane_read_answers_NONE_when_the_control_plane_is_off(
        monkeypatch):
    """The self-host shape: no control plane ⇒ no fact ⇒ the labelled fallback.

    ⛔ It must not RAISE and must not guess: this function is called by two CLI
    verbs whose contract is "no verdict" (exit 2), not "exception" (exit 1).

    ⛔ DISCRIMINATING, not merely green (#14068 review): asserting ``None`` alone
    could not tell the ENABLED-CHECK from the broad ``except Exception`` beneath
    it — deleting the guard let ``get_control_plane()`` raise and the swallow
    still answered ``None``. ⚠️ A spy that RAISES cannot fix that either: the
    swallow catches ``AssertionError`` too. So the spy RECORDS the call and the
    row asserts it was never made — measured under the mutant that deletes
    ``if not is_supabase_enabled(): return None``: RED.
    """
    from tortoise import supabase_control

    calls: list[str] = []

    def _record(*_a, **_k):
        calls.append("asked")
        raise RuntimeError("a disabled control plane must not be asked")

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: False)
    monkeypatch.setattr(supabase_control, "get_control_plane", _record)
    assert namespace_from_control_plane("orgA", "g_1") is None
    assert calls == [], (
        "the client was constructed although the control plane is DISABLED — a "
        "disabled control plane is not a failed one, and the guard is what "
        f"says so ({calls})")


def test_the_control_plane_read_uses_the_SAME_filters_the_ownership_check_does():
    """Two readers of one fact must not disagree about it.

    ``hosted_api._assert_graph_owned`` is the shipped ownership read; this pins
    that the declaration asks the same table with the same two filters, so a
    namespace can never be resolved for a ``(id, org)`` pair the ownership check
    would refuse.
    """
    from tortoise import supabase_control

    seen = {}

    class _CP:
        def query(self, table, select=None, filters=None):
            seen.update(table=table, select=select, filters=filters)
            return [{"id": "g_1", "namespace": "org_orgA"}]

    import tortoise.cache_graph as cg

    original = supabase_control.is_supabase_enabled
    original_cp = supabase_control.get_control_plane
    try:
        supabase_control.is_supabase_enabled = lambda: True
        supabase_control.get_control_plane = _CP
        assert cg.namespace_from_control_plane("orgA", "g_1") == "org_orgA"
    finally:
        supabase_control.is_supabase_enabled = original
        supabase_control.get_control_plane = original_cp

    assert seen["table"] == "graphs"
    assert seen["filters"] == [("id", "eq", "g_1"), ("org_id", "eq", "orgA")]


def _reconcile_args(cache_graph=None) -> argparse.Namespace:
    return argparse.Namespace(
        db="docker://localhost:6379",
        record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
        org="orgrepro",
        graph="grepro",
        cache_graph=cache_graph,
        timeout_ms=2000,
        json=True,
        journal=None,
    )


# ── 2. the verbs' refusal now turns on the DECLARATION ──────────────────────


# ⛔ ``_reconcile_args`` and the store-free helpers (``_stub_psycopg2``,
# ``_no_control_plane``, ``_restore_args``, ``_fake_control_plane``) are declared
# above, next to each other on purpose: a row that inherited the BOX's ambient
# control plane would pass here and fail where one is configured.


def test_record_reconcile_REFUSES_an_ASSUMED_coordinate(monkeypatch, capsys):
    """⛔ #13337's RULING, KEPT: no verdict on an assumed coordinate.

    "*The shipped refusal ... (`--cache-graph` has no derived default, with the
    reason spelled out) is** correct and must stay **: guessing would make an
    empty graph read as `divergent`, i.e. a wrong verdict instead of no
    verdict.*" A stderr note does not reach the consumer — a scheduler keys on
    the EXIT CODE, so grading the derived spelling and exiting 3 IS the false
    divergence the issue was filed about.

    What the fact arm buys is the flag becoming optional WHERE A FACT EXISTS;
    this row is the other side of that line. The refusal must also stay
    DISTINGUISHABLE from the old blanket one: it names the spelling it would
    have used and both conventions, and it says UNKNOWN, not zero.
    """
    _no_control_plane(monkeypatch)
    _stub_psycopg2(monkeypatch)

    code = cli._cmd_record_reconcile(_reconcile_args(cache_graph=None))
    captured = capsys.readouterr()

    assert code == 2, "an assumed coordinate is NO VERDICT, never a graded one"
    assert "DERIVED" in captured.err, captured.err
    assert "org_orgrepro_grepro" in captured.err, (
        f"the refusal must name the spelling it would have used: {captured.err}")
    assert "org_<org_id>" in captured.err, captured.err
    assert "org_<org_id>_<graph_id>" in captured.err, captured.err
    assert "UNKNOWN, not zero" in captured.err, captured.err
    assert "--cache-graph is required" not in captured.err, (
        "the old BLANKET flag refusal must not be what fires: the flag is "
        f"optional when a fact answers ({captured.err})")


def test_a_WHITESPACE_only_cache_graph_is_NOT_a_stated_name(
        monkeypatch, capsys):
    """⛔ `--cache-graph "   "` IS NOT A STATED NAME — and it used to skip BOTH
    messages and grade the assumed coordinate silently (#14068 review).

    The declaration STRIPS the stated value (``cache_graph.py``), so a
    whitespace-only flag lands on the DERIVED arm while the raw argument is
    truthy — the shape ``--journal "$UNSET_VAR"`` takes, which this repo
    designs for by refusing rather than dropping it as uninformed. Both verbs
    must therefore gate on ``declared.source``, never on ``args.cache_graph``.
    """
    _no_control_plane(monkeypatch)
    _stub_psycopg2(monkeypatch)

    code = cli._cmd_record_reconcile(_reconcile_args(cache_graph="   "))
    captured = capsys.readouterr()

    assert code == 2, f"a blank flag is not a name: {captured.err}"
    assert "DERIVED" in captured.err, (
        f"a blank stated name must not go silent: {captured.err}")


def test_record_reconcile_ACCEPTS_a_missing_flag_once_a_FACT_answers(
        monkeypatch, capsys):
    """THE HALF THAT CHANGES: with a fact answering, the flag is optional.

    Store-free: the run gets PAST the name and fails at the record read, which
    is the proof that the flag was no longer the blocker — and that no note or
    warning fires when nothing was assumed and nothing was contradicted.
    """
    _stub_psycopg2(monkeypatch)

    table: dict = {}
    _fake_control_plane("org_orgrepro", table)
    try:
        code = cli._cmd_record_reconcile(_reconcile_args(cache_graph=None))
    finally:
        _restore_control_plane(table)
    captured = capsys.readouterr()

    assert code == 2, "still no verdict — but for the RECORD, not the name"
    assert "record is unreadable" in captured.err, captured.err
    assert "DERIVED" not in captured.err, (
        f"the name was READ, so nothing was assumed: {captured.err}")
    assert "WARNING" not in captured.err, captured.err


def test_record_reconcile_WARNS_when_a_STATED_name_disagrees_with_the_FACT(
        monkeypatch, capsys):
    """⛔ STORE-FREE on purpose (#14068 review finding 8).

    The disagreement WARNING was pinned ONLY inside a ``_both_stores()`` leg, so
    it had no assertion on any merge gate. A stated name is a fact and is never
    overridden — but a wrong coordinate is a wrong verdict, so the mismatch is
    the one case the verb must not grade silently.
    """
    _stub_psycopg2(monkeypatch)

    table: dict = {}
    _fake_control_plane("org_orgrepro", table)
    try:
        code = cli._cmd_record_reconcile(
            _reconcile_args(cache_graph="org_orgrepro_grepro"))
    finally:
        _restore_control_plane(table)
    captured = capsys.readouterr()

    assert code == 2, "no record here, so still no verdict"
    assert "WARNING: --cache-graph" in captured.err, captured.err
    assert "org_orgrepro_grepro" in captured.err, captured.err
    assert "org_orgrepro" in captured.err, (
        f"the warning must name the fact it disagrees with: {captured.err}")


def test_the_VERDICT_carries_the_coordinate_and_its_source(monkeypatch, capsys):
    """⛔ STORE-FREE (#14068 review finding 8): a divergence count is a statement
    about ONE cache graph, so the payload must carry the coordinate and whether it
    was READ or assumed.

    The comparison is stubbed (this row is about the PAYLOAD, not the stores);
    everything from the declaration read to the printed JSON runs as shipped.
    """
    import json as _json

    class _Conn:
        def close(self):
            return None

    class _Proj:
        g = object()

        def close(self):
            return None

    class _Result:
        not_hot = 0
        checked = 3

        def as_dict(self):
            return {"verdict": "clean", "checked": 3, "divergent": 0,
                    "missing_in_cache": 0, "extra_in_cache": 0,
                    "field_mismatches": 0, "exit": 0}

        def verdict(self):
            return "clean"

    import types

    import tortoise.cache_projection as cp
    import tortoise.projection as proj_mod
    import tortoise.record_reconcile as rr
    import tortoise.residency as residency

    stub = types.ModuleType("psycopg2")
    stub.connect = lambda *a, **k: _Conn()
    monkeypatch.setitem(sys.modules, "psycopg2", stub)
    monkeypatch.setattr(cp, "read_record_view", lambda *_a, **_k: {})
    monkeypatch.setattr(proj_mod.FalkorProjection, "from_uri",
                        staticmethod(lambda *_a, **_k: _Proj()))
    monkeypatch.setattr(residency, "read_declaration", lambda _g: None)
    monkeypatch.setattr(rr, "scan_cache_view", lambda *_a, **_k: types.SimpleNamespace(
        view={}, placed={}))
    monkeypatch.setattr(rr, "record_key_map", lambda _v: {})
    monkeypatch.setattr(rr, "reconcile_record_and_cache",
                        lambda *_a, **_k: _Result())
    monkeypatch.setattr(rr, "log_result", lambda _r: "record == cache")

    table: dict = {}
    _fake_control_plane("org_orgrepro", table)
    try:
        code = cli._cmd_record_reconcile(_reconcile_args(cache_graph=None))
    finally:
        _restore_control_plane(table)
    captured = capsys.readouterr()

    assert code == 0, captured.err
    payload = _json.loads(captured.out)
    assert payload["cache_graph"] == "org_orgrepro", payload
    assert payload["cache_graph_source"] == SOURCE_CONTROL_PLANE, payload
    assert payload["missing_in_cache"] == 0, payload


def test_record_restore_REFUSES_when_the_name_would_be_DERIVED(
        monkeypatch, capsys):
    """⛔ A DERIVED NAME IS NOT ENOUGH FOR A DESTRUCTIVE VERB.

    ``#9556`` pinned this refusal for the right reason — a wrong guess WIPES a
    graph and refills it, so an assumption here destroys a graph the operator
    never named. What changes is that the refusal is no longer BLIND: it names
    the spelling it would have used, so the flag is one read away instead of a
    guess the operator has to make blind.
    """
    _no_control_plane(monkeypatch)
    _stub_psycopg2(monkeypatch)
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)

    code = cli._cmd_record_restore(_restore_args(cache_graph=None))
    captured = capsys.readouterr()

    assert code == 2, "a destructive verb must refuse on an assumed coordinate"
    assert "--cache-graph is required" in captured.err, captured.err
    assert "org_orgrepro_grepro" in captured.err, (
        f"the refusal must name the spelling it would have used: {captured.err}")
    assert "Nothing was restored" in captured.err, captured.err


# ⛔ The BLANK-FLAG case has its own row for both verbs:
# ``test_a_WHITESPACE_only_cache_graph_is_NOT_a_stated_name`` (reconcile) and
# ``test_a_WHITESPACE_only_cache_graph_cannot_defeat_the_DESTRUCTIVE_refusal``
# (restore, the one where it reached the wipe path).


def test_the_DERIVED_refusal_precedes_the_WIPE_it_protects(monkeypatch, capsys):
    """⛔ A CHECK THAT FIRES AFTER THE ``DETACH DELETE`` PROTECTS NOTHING.

    ``test_record_restore_REFUSES_when_the_name_would_be_DERIVED`` pins the exit
    code and the message; this pins the ORDER, which is the property that makes
    the refusal protective rather than decorative. Removing the old usage refusal
    was the dangerous half of #13337: a derived coordinate handed to
    ``record-restore`` is a coordinate handed to a bulk wipe, so the guard has to
    answer BEFORE the composition is entered — it is NOT enough that the run ends
    2. The seam that passes ``confirm_destructive=True``
    (``record_restore.restore_from_record_view``) is replaced with an
    ``AssertionError``, so a refusal moved below it cannot pass.
    """
    import tortoise.record_restore as rr

    _no_control_plane(monkeypatch)
    _stub_psycopg2(monkeypatch)
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)

    def _boom(*a, **k):
        raise AssertionError(
            "the wipe ran on an ASSUMED coordinate — the refusal must precede it")

    # The verb imports the seam INSIDE the function, so patching the module
    # attribute is what it resolves.
    monkeypatch.setattr(rr, "restore_from_record_view", _boom)

    code = cli._cmd_record_restore(_restore_args(cache_graph=None))

    assert code == 2, capsys.readouterr().err

def test_record_restore_HANDS_THE_FACT_to_the_composition(monkeypatch, capsys):
    """⛔ THE WIRING, ASSERTED (#14068 review finding 5).

    ``args.cache_graph = declared.name`` is the one line that carries the
    control-plane fact into the DESTRUCTIVE verb, and it ran before the record
    read — so every store-free row exited 2 at the stubbed driver and DELETING
    that line left them all green. This row spies on both sinks and asserts the
    coordinate the composition is handed, and that the target is STATED before
    anything is opened or deleted.
    """
    class _Conn:
        def close(self):
            return None

    class _Proj:
        def close(self):
            return None

    class _Outcome:
        def as_dict(self):
            return {"restored": 1}

    import types

    import tortoise.cache_projection as cp
    import tortoise.projection as proj_mod
    import tortoise.record_restore as rrestore

    seen: dict = {}

    class _FakeProjection:
        @staticmethod
        def from_uri(uri, *, graph_name=None):
            seen["from_uri"] = graph_name
            return _Proj()

    def _restore(view, *, org_id, graph_id, cache_graph, project,
                 allow_empty=False, limit=None, scope=""):
        seen["composition"] = cache_graph
        return _Outcome()

    stub = types.ModuleType("psycopg2")
    stub.connect = lambda *a, **k: _Conn()
    monkeypatch.setitem(sys.modules, "psycopg2", stub)
    monkeypatch.setattr(cp, "read_record_view", lambda *_a, **_k: {})
    monkeypatch.setattr(proj_mod, "FalkorProjection", _FakeProjection)
    monkeypatch.setattr(rrestore, "refuse_unrestorable_source",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(rrestore, "restore_from_record_view", _restore)
    monkeypatch.setattr(rrestore, "log_result", lambda _o: "restored 1")
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)

    table: dict = {}
    _fake_control_plane("org_orgrepro", table)
    try:
        code = cli._cmd_record_restore(_restore_args(cache_graph=None))
    finally:
        _restore_control_plane(table)
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert seen["from_uri"] == "org_orgrepro", (
        "the READ name is what must be opened — not the derived spelling "
        f"and not None: {seen}")
    assert seen["composition"] == "org_orgrepro", seen
    assert "restore target" in captured.err, (
        "and the run must state the coordinate it resolved, with its source "
        f"(the ordering pin is its own row): {captured.err}")


def test_record_restore_STATES_its_target_BEFORE_it_touches_the_graph(
        monkeypatch, capsys):
    """⛔ THE FACT ARM WIPES, SO THE COORDINATE IS STATED BEFORE THE FIRST TOUCH.

    `#14068` review P2-1: once the fact arm stopped refusing, the FIRST line such
    a run emitted was the POST-restore outcome — the operator learned which graph
    had been wiped only after it was gone, and `FalkorProjection.from_uri` has
    already CREATED the graph it names by then.

    Discriminating by construction: the assertion needs a statement that exists
    ONLY on the path this test asserts, and the ordering assertion pins it BEFORE
    the record read (hence before `from_uri`, before the wipe) rather than merely
    somewhere in the output.
    """
    import types

    stub = types.ModuleType("psycopg2")

    def _connect(*a, **k):
        raise OSError("no database here")

    stub.connect = _connect
    monkeypatch.setitem(sys.modules, "psycopg2", stub)
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)

    table: dict = {}
    _fake_control_plane("org_orgrepro", table)
    try:
        args = argparse.Namespace(
            db="docker://localhost:6379",
            record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
            org="orgrepro", graph="grepro", cache_graph=None,
            limit=None, allow_empty=False, json=True)
        code = cli._cmd_record_restore(args)
    finally:
        _restore_control_plane(table)
    captured = capsys.readouterr()

    assert code == 2, captured.err
    assert "restore target" in captured.err, (
        "a destructive verb must state the graph it is about to wipe BEFORE it "
        f"wipes it: {captured.err}")
    stated = captured.err.index("restore target")
    assert "org_orgrepro" in captured.err[stated:], (
        f"the statement must name the FACT's graph: {captured.err}")
    assert SOURCE_CONTROL_PLANE in captured.err[stated:], (
        f"and say whether the coordinate was read or assumed: {captured.err}")
    assert stated < captured.err.index("record is unreadable"), (
        "the target is stated BEFORE the record is read, hence before any "
        f"projection opens (and creates) the graph: {captured.err}")


def test_a_WHITESPACE_only_cache_graph_cannot_defeat_the_DESTRUCTIVE_refusal(
        monkeypatch, capsys):
    """⛔ A BLANK FLAG IS NOT A STATED NAME, and a destructive verb must not read
    it as one.

    `declared_cache_graph` trims before it decides, so `--cache-graph "   "` falls
    through to the derived arm and is REFUSED. The arms in the verbs keyed on the
    RAW `args.cache_graph`, which is truthy for `"   "` — so the refusal was
    skipped and the derived name was handed to the verb that WIPES, i.e. the
    `#9556` refusal defeated by three spaces (`#14068` review P3-3b, and a P2
    once you follow it into this verb). This test is the regression: the blank
    flag must land in the same arm as no flag at all.
    """
    import types

    stub = types.ModuleType("psycopg2")

    def _connect(*a, **k):
        raise OSError("no database here")

    stub.connect = _connect
    monkeypatch.setitem(sys.modules, "psycopg2", stub)
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    monkeypatch.setattr(
        "tortoise.cache_graph.namespace_from_control_plane",
        lambda _org, _gid: None, raising=True)

    args = argparse.Namespace(
        db="docker://localhost:6379",
        record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
        org="orgrepro", graph="grepro", cache_graph="   ",
        limit=None, allow_empty=False, json=True)
    code = cli._cmd_record_restore(args)
    captured = capsys.readouterr()

    assert code == 2, (
        f"three spaces must not authorise a wipe on a derived name: {captured.err}")
    assert "--cache-graph is required" in captured.err, captured.err
    assert "org_orgrepro_grepro" in captured.err, captured.err
    assert "Nothing was restored" in captured.err, captured.err
    assert "restore target" not in captured.err, (
        f"nothing was restored, so no target may be announced as one: {captured.err}")


def test_record_reconcile_HANDS_the_declared_name_to_the_CACHE_CONSUMER(
        monkeypatch, capsys):
    """⛔ THE WIRING, PINNED WITHOUT STORES (`#14068` review P3-4).

    The reported `cache_graph` field is `declared.name` — reporting it proves
    nothing about what the run GRADED. Reverting `cache_graph = declared.name`
    back to the derived spelling left every store-less test green, because both
    end-to-end legs skip without Postgres + FalkorDB.

    So this leg fails the CACHE OPEN, which is downstream of the wiring and whose
    message is built from the wired name: with the control plane answering
    `org_orgrepro`, the run must try to open THAT graph — not the registry
    spelling `org_orgrepro_grepro`. No store is involved: psycopg2 and the
    projection are both stubbed, and the assertion is about the name the consumer
    was handed.
    """
    import types

    stub = types.ModuleType("psycopg2")

    class _Conn:
        def close(self):
            return None

    stub.connect = lambda *a, **k: _Conn()
    monkeypatch.setitem(sys.modules, "psycopg2", stub)

    import tortoise.cache_projection as projection_read_mod
    import tortoise.projection as projection_mod

    monkeypatch.setattr(projection_read_mod, "read_record_view",
                        lambda _conn, **_kw: object(), raising=True)

    class _NoCache:
        @staticmethod
        def from_uri(_uri, graph_name=None):
            raise RuntimeError(f"cannot open {graph_name}")

    monkeypatch.setattr(projection_mod, "FalkorProjection", _NoCache,
                        raising=True)

    table: dict = {}
    _fake_control_plane("org_orgrepro", table)
    try:
        code = cli._cmd_record_reconcile(_reconcile_args(cache_graph=None))
    finally:
        _restore_control_plane(table)
    captured = capsys.readouterr()

    assert code == 2, captured.err
    assert "could not be opened" in captured.err, captured.err
    assert "org_orgrepro" in captured.err, (
        f"the consumer must be handed the FACT's name: {captured.err}")
    assert "org_orgrepro_grepro" not in captured.err, (
        "the registry spelling is what the defect graded — the fact's name must "
        f"reach the cache read instead: {captured.err}")


# ── 3. the end-to-end acceptance (both stores; SKIPS without them) ───────────


def _both_stores():
    """The two stores these legs need — and a HALF-PROVISIONED job is a FAILURE.

    ⛔ The shape refused here is *certifying by absence* (#9551): a leg that needs
    both stores and SKIPS when one is missing cannot tell a job that provisioned
    both from a job that provisioned neither, and the second reads exactly like a
    pass. That is why PR #14068's review (finding 3) blocked on it: the issue's
    headline acceptance executed on NO CI job.

    The signal is the ENVIRONMENT, never a switch: ``TEST_RECORD_DB_URI`` and
    ``TORTOISE_DB_URI`` are exported by the same CI step, so *both set* means the
    leg must run and *neither set* means there is nothing here to test (a laptop,
    the fast matrix). Exactly one set is a real misconfiguration, and it is
    reported as a FAILURE — a ``REQUIRE_*`` switch nobody exports would be an
    inert guard that only ever skips.
    """
    dsn = (os.environ.get(RECORD_DB_ENV) or "").strip()
    cache = (os.environ.get("TORTOISE_DB_URI") or "").strip()
    if not dsn and not cache:
        pytest.skip(f"neither {RECORD_DB_ENV} nor TORTOISE_DB_URI is set — "
                    "there is no record and no cache on this lane")
    if not dsn:
        pytest.fail(
            f"TORTOISE_DB_URI is set but {RECORD_DB_ENV} is not — a job that "
            "provisions the cache must provision the record too; this leg cannot "
            "certify by absence (#9551/#13337).")
    if not cache:
        pytest.fail(
            f"{RECORD_DB_ENV} is set but TORTOISE_DB_URI is not — the record was "
            "provisioned without the cache it is compared against; this leg "
            "cannot certify by absence (#9551/#13337).")
    try:
        import psycopg2  # noqa: F401 — the record's driver, nothing else
    except ImportError:
        pytest.fail(
            "both stores are provisioned but psycopg2 is not installed — the "
            "`postgres` extra is missing (`uv sync --extra postgres`), so this "
            "leg cannot run and must not skip (#13337).")
    return dsn


def test_a_HALF_PROVISIONED_job_FAILS_rather_than_skips(monkeypatch):
    """Non-vacuity for the arm: each half missing is a RED, not a skip."""
    monkeypatch.delenv(RECORD_DB_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://localhost:6379")
    with pytest.raises(pytest.fail.Exception) as excinfo:
        _both_stores()
    assert RECORD_DB_ENV in str(excinfo.value)

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv(RECORD_DB_ENV, "postgresql://u:p@localhost:5432/record")
    with pytest.raises(pytest.fail.Exception) as excinfo:
        _both_stores()
    assert "TORTOISE_DB_URI" in str(excinfo.value)

    monkeypatch.delenv(RECORD_DB_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _both_stores()


def test_the_armed_CI_job_supplies_BOTH_stores():
    """The ARMING is pinned, not only the helper (review finding 3).

    ``_both_stores()`` inferring the shape from the environment is worth nothing
    if no job supplies both variables: deleting the step, the FalkorDB provision
    or its teardown silently restores the permanently-unexecuted gate, and a
    green job over an acceptance that never ran is invisible from the outside.
    So the wiring is read out of the workflow itself.
    """
    import yaml

    wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "python-ci.yml")
                        .read_text(encoding="utf-8"))
    steps = wf["jobs"]["test-record-postgres"]["steps"]
    uses = [str(s.get("uses", "")) for s in steps]

    assert any("tests/test_13337_cache_graph_declaration.py" in str(s.get("run", ""))
               for s in steps), (
        "no step of test-record-postgres RUNS this file — its end-to-end legs are "
        "back to skipping everywhere with nothing to notice it (#13337 review, "
        "finding 3)")
    assert any(u.endswith("postgres-provision") for u in uses), (
        "this job provisions no record database, so the record half of the "
        "end-to-end legs cannot arm")

    provisions = [i for i, u in enumerate(uses) if u.endswith("falkordb-provision")]
    teardowns = [i for i, u in enumerate(uses) if u.endswith("falkordb-teardown")]
    assert len(provisions) == 1 and len(teardowns) == 1, (
        "expected exactly one FalkorDB provision/teardown pair, got "
        f"{len(provisions)}/{len(teardowns)} — the cache URI these legs read comes "
        "from that provision, and an unpaired one leaks a container")
    assert (steps[provisions[0]].get("with") or {}).get("uri_graph") == \
        "tortoise_test_matrix", (
        "the provision must export TORTOISE_DB_URI (`uri_graph`) or the cache half "
        "of `_both_stores()` is missing and the legs fail on a job that meant to "
        "arm them")
    assert teardowns[0] > provisions[0] and steps[teardowns[0]].get("if") == "always()", (
        "the teardown must follow the provision and run on failure/cancel too")
    # ORDERING is why the provisioning sits BELOW the twelve record legs: `uri_graph`
    # writes TORTOISE_DB_URI into `$GITHUB_ENV`, so every LATER step sees it — and
    # those files are written to build their own graphs with no URI.
    record_legs = next(i for i, s in enumerate(steps)
                       if "test_9567_record_capstone.py" in str(s.get("run", "")))
    assert provisions[0] > record_legs, (
        "the FalkorDB provision runs BEFORE the record legs: `uri_graph` exports "
        "TORTOISE_DB_URI for every later step, so those record-only files would "
        "silently be handed a server they were never written for")


def test_a_PADDED_namespace_fact_does_not_fake_a_disagreement(monkeypatch, capsys):
    """Two readers of one value must agree on its NORMALISATION.

    ``graphs.namespace`` is operator-supplied TEXT, so it can carry padding, while
    ``declared_cache_graph`` trims the STATED name. Comparing the raw column with
    the trimmed one printed ``WARNING: --cache-graph 'org_x' disagrees with the
    control plane's namespace (' org_x ')`` for an operator who stated exactly the
    fact — a warning that is FALSE, and that trains its reader to ignore the one
    that matters.
    """
    _stub_psycopg2(monkeypatch)
    table: dict = {}
    _fake_control_plane("  org_orgrepro  ", table)
    try:
        code = cli._cmd_record_reconcile(_reconcile_args("org_orgrepro"))
    finally:
        _restore_control_plane(table)
    out = capsys.readouterr()

    assert "WARNING" not in out.err, (
        f"the stated name IS the fact — there is no disagreement to report: {out.err}")
    assert code == 2, "the record is still unreachable on this box"


def test_the_control_plane_read_TRIMS_the_namespace(monkeypatch):
    """The ONE place a namespace enters the process is where it is normalised."""
    import tortoise.cache_graph as cg
    from tortoise import supabase_control

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(
        supabase_control, "get_control_plane",
        lambda: type("CP", (), {"query": lambda self, *_a, **_k: [
            {"id": "g_1", "namespace": "  org_orgA  "}]})())

    assert cg.namespace_from_control_plane("orgA", "g_1") == "org_orgA"


def _owner_dsn() -> str:
    return os.environ.get("OWNER_RECORD_DSN") or os.environ[RECORD_DB_ENV]


def _seed_default_graph(org: str, graph_id: str, namespace: str) -> None:
    """A tenant whose DEFAULT graph lives at the legacy namespace (#10180).

    ``public.graphs.namespace`` is the fact the declaration reads; this seeds it
    to the legacy spelling, which is the one graph the registry derivation is
    wrong about.
    """
    import psycopg2

    conn = psycopg2.connect(_owner_dsn())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING", (org, org))
            cur.execute(
                "INSERT INTO public.graphs (id, org_id, name, namespace) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (graph_id, org, "default", namespace))
    finally:
        conn.close()


def _count_record(org: str, graph_id: str) -> dict[str, int]:
    import psycopg2

    conn = psycopg2.connect(_owner_dsn())
    conn.autocommit = True
    out: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for table in ("object", "event"):
                cur.execute(
                    f"SELECT count(*) FROM public.{table} "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph_id))
                out[table] = cur.fetchone()[0]
    finally:
        conn.close()
    return out


def _cache_counts(graph_name: str) -> dict[str, int]:
    from urllib.parse import unquote, urlparse

    from falkordb import FalkorDB

    uri = os.environ["TORTOISE_DB_URI"]
    parsed = urlparse(uri)
    db = FalkorDB(host=parsed.hostname, port=parsed.port or 6379,
                  password=unquote(parsed.password) if parsed.password else None)
    graph = db.select_graph(graph_name)
    out: dict[str, int] = {}
    for label in ("Object", "Event"):
        res = graph.query(f"MATCH (n:{label}) RETURN count(n)")
        out[label] = int(res.result_set[0][0]) if res.result_set else 0
    return out


def _fake_control_plane(namespace: str, table: dict):
    """The shipped read, against a control plane this box does not have.

    ⛔ It stands in for the CONTROL PLANE only — the declaration's query, its
    filters, its blank/absent handling and the fallback all run as shipped.
    """
    from tortoise import supabase_control

    table["is_supabase_enabled"] = supabase_control.is_supabase_enabled
    table["get_control_plane"] = supabase_control.get_control_plane

    class _CP:
        def query(self, _table, select=None, filters=None):
            return [{"id": "g", "namespace": namespace}]

    supabase_control.is_supabase_enabled = lambda: True
    supabase_control.get_control_plane = _CP


def _restore_control_plane(table: dict) -> None:
    from tortoise import supabase_control

    supabase_control.is_supabase_enabled = table["is_supabase_enabled"]
    supabase_control.get_control_plane = table["get_control_plane"]


def _no_control_plane(monkeypatch) -> None:
    """The self-host shape, STATED rather than inherited from the box's env.

    A row that trusted the ambient absence of ``SUPABASE_URL`` would pass here
    and fail on a box that has one — and the derived arm is exactly what these
    rows are about.
    """
    from tortoise import supabase_control

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: False)


def _stub_psycopg2(monkeypatch) -> None:
    """A driver that cannot reach a record: every row below stops there. """
    import types

    stub = types.ModuleType("psycopg2")

    def _connect(*a, **k):
        raise OSError("no database here")

    stub.connect = _connect
    monkeypatch.setitem(sys.modules, "psycopg2", stub)


def _restore_args(cache_graph=None, **over) -> argparse.Namespace:
    args = argparse.Namespace(
        db="docker://localhost:6379",
        record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
        org="orgrepro", graph="grepro", cache_graph=cache_graph,
        limit=None, allow_empty=False, json=True)
    for k, v in over.items():
        setattr(args, k, v)
    return args


def _org_wide_write(monkeypatch, org: str, graph_id: str, namespace: str,
                    *, event: bool) -> None:
    """One write through the ORG-WIDE ``_data_sdk`` door (no graph_id on the
    credential) — the class that opens the org's DEFAULT graph.

    The control plane is the only stand-in (``_resolve_record_tenant``, which is
    what it would answer); the SDK open, the graph fold, the journal append and
    the record merge all run as shipped.
    """
    from tortoise import hosted_api

    _seed_default_graph(org, graph_id, namespace)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", os.environ[RECORD_DB_ENV])
    monkeypatch.setenv(
        "TORTOISE_EVENT_LOG_BASE_DIR",
        os.environ.get("TORTOISE_EVENT_LOG_BASE_DIR")
        or str(Path(os.environ.get("TMPDIR", "/tmp")) / f"13337-{org}"))
    monkeypatch.delenv("TORTOISE_RECORD_ALLOCATE", raising=False)

    original = hosted_api._resolve_record_tenant
    hosted_api._resolve_record_tenant = lambda _o: (org, graph_id)
    try:
        org_dict = {"org_id": org, "graph_id": None, "graph_namespace": None,
                    "key_id": None, "legacy_full_access": True, "scopes": [],
                    "tier": "free", "delegation_depth": None,
                    "created_by_key_id": None, "max_users": 1, "max_graphs": 1,
                    "max_points": 10000, "max_api_keys": 2, "max_sessions": None}
        sdk = hosted_api._data_sdk(org_dict)
        try:
            assert sdk._get_proj().graph_name == namespace, (
                "the org-wide credential must open the org's default graph")
            sdk._create_object("alpha", objectKind="goal")
            if event:
                sdk.create_entity("event", "launch", eventKind="meeting")
        finally:
            sdk.close()
    finally:
        hosted_api._resolve_record_tenant = original


def _graph_bound_write(monkeypatch, org: str, graph_id: str, namespace: str,
                       *, event: bool = False) -> None:
    """One write through the GRAPH-BOUND ``_data_sdk`` door (#13337 acceptance 2).

    The credential carries the graph, so ``_data_sdk`` takes its graph-bound
    branch: ``_data_graph_name`` → ``graph_namespace``, the ownership pre-check,
    then ``_make_sdk(graph_name=<namespace>)``. When that namespace IS the row's
    own namespace (which the seeded row is), both doors open the SAME graph —
    which is the acceptance: two credentials, one declared cache graph.
    """
    from tortoise import hosted_api

    _seed_default_graph(org, graph_id, namespace)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", os.environ[RECORD_DB_ENV])
    monkeypatch.setenv(
        "TORTOISE_EVENT_LOG_BASE_DIR",
        os.environ.get("TORTOISE_EVENT_LOG_BASE_DIR")
        or str(Path(os.environ.get("TMPDIR", "/tmp")) / f"13337-{org}"))
    monkeypatch.delenv("TORTOISE_RECORD_ALLOCATE", raising=False)

    original = hosted_api._resolve_record_tenant
    hosted_api._resolve_record_tenant = lambda _o: (org, graph_id)
    try:
        org_dict = {"org_id": org, "graph_id": graph_id,
                    "graph_namespace": namespace,
                    "key_id": None, "legacy_full_access": True, "scopes": [],
                    "tier": "free", "delegation_depth": None,
                    "created_by_key_id": None, "max_users": 1, "max_graphs": 1,
                    "max_points": 10000, "max_api_keys": 2, "max_sessions": None}
        sdk = hosted_api._data_sdk(org_dict)
        try:
            assert sdk._get_proj().graph_name == namespace, (
                "the graph-bound credential must open ITS graph's namespace")
            sdk._create_object("beta", objectKind="goal")
            if event:
                sdk.create_entity("event", "launch2", eventKind="meeting")
        finally:
            sdk.close()
    finally:
        hosted_api._resolve_record_tenant = original


def test_BOTH_DOORS_write_ONE_cache_graph_and_reconcile_CLEAN_on_it(
        monkeypatch, capsys):
    """#13337 ACCEPTANCE ITEM 2 — the row that had no coverage at all.

    *"A tenant written through an org-wide credential AND through a graph-bound
    credential reconciles ``clean`` on the same ``--cache-graph`` name."*

    Both doors write; the record files both under ONE tenant; the cache holds both
    in ``org_<org>`` (the fact) and NOTHING in the derived spelling — and the
    reconciler is clean on that one name whether it READS it or is given it. The
    second half matters: a stated name equal to the fact must not WARN, which is
    the normalisation pin one layer up.
    """
    _both_stores()

    run = uuid.uuid4().hex[:8]
    org = f"org13337b{run}"
    graph_id = f"g_13337b{run}"
    namespace = f"org_{org}"
    derived = canonical_cache_graph_name(org, graph_id)

    table: dict = {}
    _fake_control_plane(namespace, table)
    try:
        _org_wide_write(monkeypatch, org, graph_id, namespace, event=False)
        _graph_bound_write(monkeypatch, org, graph_id, namespace)
    finally:
        _restore_control_plane(table)

    assert _count_record(org, graph_id) == {"object": 2, "event": 0}, (
        "both doors must have filed under the SAME record tenant")
    assert _cache_counts(namespace) == {"Object": 2, "Event": 0}, (
        "both credentials must have landed in ONE cache graph")
    assert _cache_counts(derived) == {"Object": 0, "Event": 0}, (
        "the derived spelling must still be EMPTY — that is the false divergence")

    # (a) no flag — the fact is READ
    table = {}
    _fake_control_plane(namespace, table)
    try:
        args = _reconcile_args(cache_graph=None)
        args.db = os.environ["TORTOISE_DB_URI"]
        args.record_dsn = os.environ[RECORD_DB_ENV]
        args.org, args.graph = org, graph_id
        code = cli._cmd_record_reconcile(args)
    finally:
        _restore_control_plane(table)
    out = capsys.readouterr()
    assert code == 0, f"clean on the READ name: {out.out}\n{out.err}"
    assert '"checked": 2' in out.out, out.out
    assert '"missing_in_cache": 0' in out.out, out.out
    assert '"extra_in_cache": 0' in out.out, out.out
    assert '"cache_graph_source": "control_plane"' in out.out, out.out

    # (b) the SAME name, STATED — the two credentials agree about it
    table = {}
    _fake_control_plane(namespace, table)
    try:
        args = _reconcile_args(cache_graph=namespace)
        args.db = os.environ["TORTOISE_DB_URI"]
        args.record_dsn = os.environ[RECORD_DB_ENV]
        args.org, args.graph = org, graph_id
        code = cli._cmd_record_reconcile(args)
    finally:
        _restore_control_plane(table)
    out = capsys.readouterr()
    assert code == 0, out.out
    assert '"cache_graph_source": "flag"' in out.out, out.out
    assert "WARNING" not in out.err, out.err


def test_an_ORG_WIDE_object_write_reconciles_CLEAN_on_the_DECLARED_name(
        monkeypatch, capsys):
    """THE ACCEPTANCE #13337 STATES, measured on the OBJECT shape.

    One org-wide write, then the product's own reconciler with NO
    ``--cache-graph``: the name is READ from ``public.graphs.namespace`` (the
    legacy ``org_<org>`` — the one graph the registry derivation is wrong about),
    and the verdict is ``clean``/exit 0.

    ⛔ RED on the tree before this change twice over: the verb refused for the
    missing flag (exit 2, nothing compared), and had the operator stated the
    DERIVED spelling the run would have read ``missing_in_cache=1`` on a cache
    that is complete.
    """
    _both_stores()

    run = uuid.uuid4().hex[:8]
    org = f"org13337a{run}"
    graph_id = f"g_13337a{run}"
    namespace = f"org_{org}"                 # the legacy default-graph spelling
    derived = canonical_cache_graph_name(org, graph_id)

    _org_wide_write(monkeypatch, org, graph_id, namespace, event=False)

    assert _count_record(org, graph_id) == {"object": 1, "event": 0}
    assert _cache_counts(namespace) == {"Object": 1, "Event": 0}
    assert _cache_counts(derived) == {"Object": 0, "Event": 0}, (
        "the derived spelling must be EMPTY — that is why it graded divergent")

    table: dict = {}
    _fake_control_plane(namespace, table)
    try:
        args = _reconcile_args(cache_graph=None)
        args.db = os.environ["TORTOISE_DB_URI"]
        args.record_dsn = os.environ[RECORD_DB_ENV]
        args.org, args.graph = org, graph_id
        code = cli._cmd_record_reconcile(args)
    finally:
        _restore_control_plane(table)
    out = capsys.readouterr()

    assert code == 0, f"verdict must be clean once the name is READ: {out.err}"
    assert '"cache_graph_source": "control_plane"' in out.out, out.out
    assert f'"cache_graph": "{namespace}"' in out.out, out.out
    assert '"missing_in_cache": 0' in out.out, out.out
    assert "--cache-graph" not in out.err, (
        f"nothing was missing to state — no note/warning should fire: {out.err}")


def test_an_ORG_WIDE_event_is_PRESENT_once_the_name_is_READ(monkeypatch, capsys):
    """The EVENT shape — key presence, and the residual named.

    ``missing_in_cache`` must be 0: the record row and the projected cache node
    exist for the same write. The run is still ``divergent`` because of
    ``field_mismatches`` on the Event — the fold writes ``classificationLevel``/
    ``format``/``participants``/``startedAt`` defaults where the record holds
    NULL. ⛔ That is #12949's root (projection fidelity), NOT a lost write, and it
    is asserted here so a reader cannot mistake "divergent" for "absent"; it is
    deliberately not folded into this change.
    """
    _both_stores()

    run = uuid.uuid4().hex[:8]
    org = f"org13337e{run}"
    graph_id = f"g_13337e{run}"
    namespace = f"org_{org}"
    derived = canonical_cache_graph_name(org, graph_id)

    _org_wide_write(monkeypatch, org, graph_id, namespace, event=True)

    assert _count_record(org, graph_id) == {"object": 1, "event": 1}
    assert _cache_counts(namespace) == {"Object": 1, "Event": 1}
    assert _cache_counts(derived) == {"Object": 0, "Event": 0}

    table: dict = {}
    _fake_control_plane(namespace, table)
    try:
        args = _reconcile_args(cache_graph=None)
        args.db = os.environ["TORTOISE_DB_URI"]
        args.record_dsn = os.environ[RECORD_DB_ENV]
        args.org, args.graph = org, graph_id
        cli._cmd_record_reconcile(args)
    finally:
        _restore_control_plane(table)
    out = capsys.readouterr()

    assert '"missing_in_cache": 0' in out.out, out.out
    assert '"Object' not in out.out.split('"divergent_keys"')[1].split("]")[0], (
        f"the OBJECT must not be listed as divergent at all: {out.out}")
    # the residual: an Event field mismatch (#12949), never a missing key
    assert '"field_mismatches": 0' not in out.out, (
        "if the fold's record-NULL defaults are ever fixed, this leg must be "
        f"re-measured rather than silently re-read: {out.out}")

    # ── and the DERIVED spelling is the false divergence ────────────────────
    table = {}
    _fake_control_plane(namespace, table)
    try:
        args = _reconcile_args(cache_graph=derived)
        args.db = os.environ["TORTOISE_DB_URI"]
        args.record_dsn = os.environ[RECORD_DB_ENV]
        args.org, args.graph = org, graph_id
        cli._cmd_record_reconcile(args)
    finally:
        _restore_control_plane(table)
    derived_out = capsys.readouterr()

    assert '"missing_in_cache": 2' in derived_out.out, derived_out.out
    assert "WARNING: --cache-graph" in derived_out.err, (
        "a stated name that disagrees with a readable fact must WARN — a wrong "
        f"coordinate is a wrong verdict: {derived_out.err}")

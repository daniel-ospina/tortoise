"""#13417 — a vectorless row is OBSERVED, and the backfill repairs exactly it.

THE STATE THIS PINS
-------------------
PR #13229 fixed a 20,414 ms p95 on the subject-create path by giving
``encode_for_row`` a ``bounded`` opt-in: the four DERIVED-entity folds wait
``WRITE_EMBED_BUDGET_S`` (0.25 s) for the embedder and then write the row
**without a vector** rather than blocking on a 13.5 s cold model load (#13224).
That trade is correct and is not undone here.

What it creates is a **truth ↔ derived divergence**, and
``docs/architecture/STORAGE-ARCHITECTURE.md`` §17.2 requires that such a
divergence be *checkable*: *"truth == cache must be checkable"* — a divergence
nobody observes is not a divergence. Two facts made it unobservable before this:

* the decline was **logged once per process per cause** (deliberately latched, so
  a bulk write cannot storm the log) — so the log answers *what* happened and
  never *how many rows* the dense leg is missing, and
* the only running count was embed metering's per-org "ran no model work" term,
  which is about the ENCODER, not about rows a reader will now fail to find.

The read side cannot substitute: `#13108` measured the dense leg dropping to
keyword-only in **45 %** of eval windows and `#13156` records that a
budget-cancelled leg is **indistinguishable from a slow store** in the trace.

WHAT IS ASSERTED
----------------
The loop, end to end (#13417's Definition of Done):

    cold window → the row is written with NO vector
                → the count fires on monitoring.metrics() (a number, not an inference)
                → the backfill runs
                → THAT row is repaired, its neighbour is not touched
                → a second backfill run finds nothing left to repair

Plus the counter-tests that keep it honest: a WARM write must not increment the
count (the metric may not become "every write"), and ``#5008``'s embed GATE — a
designed, permanent absence — must stay off this channel, or a permanent
non-zero makes the number unattributable (the `#9231` review P2 lesson).

Run (embedded lane; URI-less by design)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_13417_vectorless_rows.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tortoise import embeddings, monitoring
from tortoise.embeddings import WRITE_EMBED_BUDGET_S, EmbeddingModel, encode_for_row
from tortoise.projection import FalkorProjection

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKFILL = _REPO_ROOT / "graph-scripts" / "backfill_embeddings.py"

_DIM = 384

#: The four causes ``EmbeddingModel.await_write_ready_reason`` can return.
#: Spelled here AND asserted equal to the module's own constants
#: (``test_the_metric_label_vocabulary_is_the_seam_s_own``), because the counter
#: emits them into a Prometheus label: a silent rename forks the series and an
#: operator's existing alert reads zero forever. A comment claiming that pin is
#: not a pin — the assertion is.
_CAUSES = ("not_installed", "fail_cooldown", "attempt_spent", "budget_expired")

#: ``"<leg>:<cause>"``, the counter's one label value (#13417). The two legs are
#: never summed: they have different repairs.
_GRAPH = "graph_fold"
_RECORD = "record_derivation"


class _FakeEmbed:
    """Deterministic text→vector: same text ⇒ same vector; distinct ⇒ distinct."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, text: str, max_tokens: int = 0) -> list[float]:
        self.calls.append(str(text))
        digest = hashlib.sha256(str(text).encode()).digest()
        return [((digest[i % 32] + i * 7) % 251) / 250.0 for i in range(_DIM)]


@pytest.fixture
def proj():
    tmpdir = tempfile.mkdtemp(prefix="vecobs13417_")
    p = FalkorProjection(os.path.join(tmpdir, "vecobs.db"), graph_name="tortoise")
    try:
        yield p
    finally:
        p.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def cold_window(monkeypatch):
    """Make the write-readiness predicate answer COLD, exactly as production does.

    ⛔ This replaces the PREDICATE, not the seam: ``encode_for_row`` still takes
    its own bounded branch, still calls ``await_write_ready_reason``, and still
    returns ``None`` from the same place production does. What is faked is only
    the answer ("the embedder is not ready, and here is why"), so the test is
    deterministic and does not spend 6-60 s waiting for a real load — the same
    observable state ``tests/test_13224_cold_embedder_write_bound.py`` reaches
    with ``_fake_slow_init``.
    """

    def _cold(cls, budget):
        assert budget == WRITE_EMBED_BUDGET_S
        return "budget_expired"

    monkeypatch.setattr(EmbeddingModel, "await_write_ready_reason",
                        classmethod(_cold))


@pytest.fixture
def warm_window(monkeypatch):
    """The embedder is READY — the bound takes its non-declining path."""

    monkeypatch.setattr(EmbeddingModel, "await_write_ready_reason",
                        classmethod(lambda cls, budget: None))
    fake = _FakeEmbed()
    monkeypatch.setattr(embeddings, "compute_embedding", fake)
    return fake


def _published_declines() -> dict[str, int]:
    """The counter as ``monitoring.metrics()`` PUBLISHES it — the served surface.

    Distinct from `_no_vector_counts()` on purpose: these tests assert that the
    number reaches `metrics()`, and reading the accessor instead would leave the
    publication seam untested.
    """
    published = monitoring.metrics().get("derived_embed_declines")
    return dict(published) if isinstance(published, dict) else {}


def _no_vector_counts() -> dict[str, int]:
    return monitoring.derived_embed_decline_counts()


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = set(before) | set(after)
    return {k: after.get(k, 0) - before.get(k, 0) for k in keys
            if after.get(k, 0) != before.get(k, 0)}


def _load_backfill():
    spec = importlib.util.spec_from_file_location("backfill_13417", str(_BACKFILL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _subject_vector(g, subject_id: str, name: str):
    """The ``:Subject`` vector for ONE (id, name) pair — ids are not unique.

    #12570's whole shape is two nodes sharing an id, so an id-only reader silently
    returns whichever twin the engine lists first; every assertion about "the row
    the sweep repaired" has to name the text as well.
    """
    rows = g.query(
        "MATCH (n:Subject {id: $id}) WHERE n.name = $name RETURN n.embedding",
        params={"id": subject_id, "name": name}).result_set
    return rows[0][0] if rows else None


def _stored_vector(g, label: str, id_prop: str, key: str):
    rows = g.query(
        f"MATCH (n:{label} {{{id_prop}: $key}}) RETURN n.embedding",
        params={"key": key},
    ).result_set
    return rows[0][0] if rows else None


# ── 1. the count exists and fires, labelled by cause ──────────────────────


@pytest.mark.parametrize("cause", _CAUSES)
def test_a_bounded_decline_increments_the_count_under_its_cause(cause, monkeypatch):
    """One declined row = one increment, under the cause it declined for.

    The LABEL is the point: `not_installed` (a designed absence — install the
    extra) and `budget_expired` (a cold window — wait longer or re-encode) need
    opposite operator responses, so collapsing them into one number would report
    a cause nobody can act on.
    """
    monkeypatch.setattr(EmbeddingModel, "await_write_ready_reason",
                        classmethod(lambda cls, budget: cause))
    before = _no_vector_counts()
    assert encode_for_row(f"label-me {cause}", _DIM, bounded=True) is None
    after = _no_vector_counts()
    expected = f"{_GRAPH}:{cause}"
    assert _delta(before, after).get(expected) == 1, (
        f"a write that produced no vector must be counted under {expected!r}: "
        f"{_delta(before, after)}"
    )


def test_the_count_is_a_running_total_not_a_latched_line(cold_window):
    """Three declined rows are THREE — the difference from the log channel.

    `_note_cold_write` is deliberately latched to one line per process per cause
    (a bulk write must not storm the log). That is exactly why the LOG cannot
    answer "how many rows is the dense leg missing?" — and why this counter must
    not inherit the latch. A mutant that counts once per process reds here.
    """
    before = _no_vector_counts()
    for i in range(3):
        assert encode_for_row(f"latch-{i}", _DIM, bounded=True) is None
    assert _delta(before, _no_vector_counts()) == {f"{_GRAPH}:budget_expired": 3}


def test_a_warm_write_does_not_increment_the_count(warm_window):
    """The metric may not become "every derived write" — only the traded ones."""
    before = _no_vector_counts()
    assert encode_for_row("warm row", _DIM, bounded=True) is not None
    assert _delta(before, _no_vector_counts()) == {}, (
        "a row that DID get a vector must never be counted as missing one"
    )


def test_the_unbounded_default_never_consults_the_count(monkeypatch):
    """`bounded=False` is record TRUTH (#5004) — it has no "no vector" outcome.

    A mutant that moved the count above the `bounded` test would report a
    record-truth caller's ordinary write as a cold-window loss.
    """
    def _explode(cls, budget):                       # pragma: no cover - guard
        raise AssertionError("record-truth callers must not consult the bound")

    monkeypatch.setattr(EmbeddingModel, "await_write_ready_reason",
                        classmethod(_explode))
    monkeypatch.setattr(embeddings, "compute_embedding", _FakeEmbed())
    before = _no_vector_counts()
    assert encode_for_row("record truth row", _DIM) is not None
    assert _delta(before, _no_vector_counts()) == {}


def test_the_metric_label_vocabulary_is_the_seam_s_own():
    """The four causes are the SEAM's constants, not a hand-copied literal.

    The counter emits them into a Prometheus label value. A silent rename forks
    the series, and an operator's existing alert on `reason="budget_expired"`
    then reads zero forever while the rows go unobserved — so the vocabulary is
    asserted against `tortoise.embeddings`, not merely spelled here.
    """
    assert set(_CAUSES) == {
        embeddings._WRITE_DECLINE_NOT_INSTALLED,
        embeddings._WRITE_DECLINE_FAIL_COOLDOWN,
        embeddings._WRITE_DECLINE_ATTEMPT_SPENT,
        embeddings._WRITE_DECLINE_BUDGET_EXPIRED,
    }


def test_a_cold_re_write_of_an_embedded_row_counts_but_loses_nothing(
        proj, monkeypatch):
    """⛔ THE COUNTER'S OVER-STATEMENT, PINNED (#13417 review).

    The count is emitted at the SEAM, which knows the encoder declined and does
    NOT know whether the row already held a vector. It is pinned here for the
    SAME-text case; the changed-text case (Event/Document, whose folds rewrite
    their text on match) leaves a STALE vector the null-only repair cannot see —
    #13451, documented at the counter. Every DERIVED fold's write
    clause preserves a stored vector against a null incoming one
    (`vector_write.set_embedding_clause`, `preserve_on_null=True`), so a cold
    re-registration of an already-embedded entity — the ordinary ON MATCH path —
    increments the counter while the dense leg loses nothing.

    ⛔ This is why `graph_fold` is documented as an UPPER BOUND on new holes, not
    an exact row count, and why the exact end state is read from the repair
    (`_residual_missing`) rather than from the counter. Pinned so the docstring
    cannot drift from the behaviour in either direction: a future change that
    makes the count exact must delete this test AND the caveat together.
    """
    g = proj.db.select_graph(proj.graph_name)
    # ⚠️ The warm and cold windows are switched INSIDE this test on purpose: two
    # fixtures would race, and the last patch applied is the one that governs both
    # creates — which is how this arm silently measured the cold path twice.
    fake = _FakeEmbed()
    monkeypatch.setattr(embeddings, "compute_embedding", fake)
    monkeypatch.setattr(EmbeddingModel, "await_write_ready_reason",
                        classmethod(lambda cls, budget: None))
    # warm: the row genuinely gets a vector
    proj._upsert_subject({"id": "s-warmrow", "name": "already embedded"})
    assert _stored_vector(g, "Subject", "id", "s-warmrow") is not None, (
        "precondition: the warm create must store a vector")

    # cold: same id, same text — ON MATCH, so the vector is PRESERVED
    monkeypatch.setattr(EmbeddingModel, "await_write_ready_reason",
                        classmethod(lambda cls, budget: "budget_expired"))
    before = _no_vector_counts()
    proj._upsert_subject({"id": "s-warmrow", "name": "already embedded"})
    counted = _delta(before, _no_vector_counts())
    assert counted == {f"{_GRAPH}:budget_expired": 1}, (
        f"the seam counts every bounded decline, including this one: {counted}")
    assert _stored_vector(g, "Subject", "id", "s-warmrow") is not None, (
        "…and the stored vector SURVIVES it — so the counter over-states the "
        "rows the dense leg lost. `graph_fold` is an upper bound.")


def test_the_record_leg_is_counted_apart_from_the_graph_fold(cold_window):
    """⛔ ONE live create declines once per LEG, and the legs repair differently.

    The record's own derivation (`record_writer.derived_embedding_columns`,
    #13220) is a SECOND bounded call site, and it runs BEFORE the cache fold. Its
    decline leaves `public.{object,subject,event}.embedding` NULL, which
    `graph-scripts/backfill_embeddings.py` cannot repair — that tool reads graph
    nodes and would report zero residual over this population; the repair is a
    re-derivation/replay. Summing the two legs into one number would hand an
    operator a count whose stated repair cannot discharge half of it.
    """
    from tortoise.record_writer import derived_embedding_columns

    before = _no_vector_counts()
    out = derived_embedding_columns({"type": "ObjectRegistered",
                                     "name": "record leg entity"})
    counted = _delta(before, _no_vector_counts())
    assert out.columns == {}, out
    assert counted == {f"{_RECORD}:budget_expired": 1}, (
        f"the RECORD leg must be counted under {_RECORD!r}, not {_GRAPH!r} — a "
        f"repair is selected by the leg: {counted}")


def test_one_cold_create_declines_once_per_LEG(cold_window, proj):
    """One logical create reaches BOTH bounded legs — and they do not collide.

    The live write path runs the RECORD's derivation (`record_writer`) and then
    the graph fold, so a single cold create declines twice; an operator reading
    either number alone under-counts by half. ⛔ The two legs are separate CALLS,
    not one — so this invokes them in the order the live path does (the same
    sequence measured on the live path), rather than pretending a projection fold
    reaches the record leg. What it pins is that the two labels are distinct AND
    additive for one entity: a mutant that funnels both through one label reds
    here even though each isolation test above would still pass.
    """
    from tortoise.record_writer import derived_embedding_columns

    entity = {"id": "o-joint", "name": "joint leg entity"}
    before = _no_vector_counts()
    derived_embedding_columns({"type": "ObjectRegistered", **entity})  # leg 1
    proj._upsert_object(dict(entity))                                  # leg 2
    counted = _delta(before, _no_vector_counts())
    assert counted == {f"{_RECORD}:budget_expired": 1,
                       f"{_GRAPH}:budget_expired": 1}, (
        "one logical create must decline once per LEG, each under its own label "
        f"— a single-key result means one leg is invisible: {counted}")


def test_the_embed_gate_stays_off_this_channel(monkeypatch):
    """#5008's gate decline is a DESIGNED absence — never the #13229 cost.

    A gate-declined row is permanent and by policy (identifier-only text): no
    backfill can repair it and no wait would change it. Parking it on the same
    channel as the cold window would make a non-zero value unattributable, which
    is the `#9231` review P2 lesson that moved a DECLINE off the record's loss
    counter. The gate's own channel (`skipped`, plus its log) still carries it.
    """
    from tortoise import embed_gate

    monkeypatch.setattr(embed_gate, "embed_gate_enabled", lambda: True)
    monkeypatch.setattr(embed_gate, "embedding_verdict",
                        lambda text: {"reason": "identifier-only",
                                      "rule_id": "test"})
    before = _no_vector_counts()
    assert encode_for_row("abc-123", _DIM, bounded=True) is None
    assert _delta(before, _no_vector_counts()) == {}


def test_metrics_publishes_the_count(cold_window):
    """It lands on the surface the fleet serves, WITH the value that was recorded.

    ⛔ `isinstance(published, dict)` alone is satisfied by `{}` — and with nothing
    recorded, `{}` is exactly what the accessor returns, so a mutant publishing a
    constant empty dict, or one wired to the wrong counter family, would pass. The
    decline is therefore recorded FIRST and the published value asserted, which is
    the claim the docstring makes ("it lands on the surface the fleet serves"):
    the number has to travel from `record_derived_embed_decline` to `metrics()`.
    """
    before = _published_declines()
    assert encode_for_row("published count probe", _DIM, bounded=True) is None
    published = _published_declines()
    assert isinstance(published, dict), (
        "monitoring.metrics() must publish derived_embed_declines — §17.2 "
        f"requires the divergence be checkable, got {published!r}"
    )
    # ⛔ A DELTA, not `>= 1`: the counters are process-global running totals, so
    # in a whole-module run the key is already non-zero before this test starts,
    # and `>= 1` would be satisfied with this test's own recording deleted. It
    # would also be satisfied by a HARDCODED constant dict — measured, that
    # mutant passed the `>= 1` form. The delta pins that THIS decline travelled
    # from `record_derived_embed_decline` to `metrics()`.
    assert _delta(before, published) == {f"{_GRAPH}:budget_expired": 1}, (
        "the decline recorded by this test must appear in metrics() and only "
        f"that one — got {_delta(before, published)} (all: {published!r})")


# ── 2. the loop: cold write → count → backfill → repaired ──────────────────


def test_the_loop_cold_write_count_backfill_repairs_exactly_that_row(
        cold_window, proj, monkeypatch):
    """#13417's Definition of Done, as one run.

    A derived-entity row written inside a cold window carries no vector; the
    count says so in NUMBERS; the backfill repairs THAT row and leaves its
    neighbour alone; and a second run finds nothing left to repair.

    The neighbour shares the damaged row's ID and holds a distinctive vector, and
    that is deliberate: a distinct-id neighbour would make the "not touched"
    assertion VACUOUS, because the NULL-only sweep never selects a row that
    already has a vector — measured, this test passed unchanged against a
    `_write_where` reverted to the pre-fix `n.id = row.id`. With the shared id the
    pre-fix write matches BOTH twins and overwrites this one, so the assertion now
    discriminates (#12570's collateral damage, exercised here rather than only in
    ``test_backfill_embeddings_force.py``).
    """
    g = proj.db.select_graph(proj.graph_name)
    cold_name = "cold-window subject"
    warm_name = "warm neighbour subject"
    warm_vector = [0.75] * _DIM

    # ── the cold window: a real DERIVED-entity fold, no vector ────────────
    before = _no_vector_counts()
    proj._upsert_subject({"id": "s-cold", "name": cold_name})
    counted = _delta(before, _no_vector_counts())
    assert counted == {f"{_GRAPH}:budget_expired": 1}, (
        "the cold-window row must be COUNTED as stored without a vector — a "
        f"silent omission is the defect: {counted}"
    )
    assert _subject_vector(g, "s-cold", cold_name) is None, (
        "precondition: the cold row is written and carries NO vector")

    # The neighbour SHARES the id and already holds a vector — the shape #12570
    # is about, and the only shape in which "not touched" is a claim with content.
    g.query(
        "CREATE (n:Subject {id: $id, name: $name, embedding: vecf32($emb)})",
        params={"id": "s-cold", "name": warm_name, "emb": warm_vector})

    # ── the backfill repairs the row that lacked a vector ────────────────
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    stats = mod._backfill_graph(proj.db, proj.graph_name, ["Subject"],
                               0, 500, required_dim=None)

    assert stats["updated"] == 1, (
        f"exactly the ONE vectorless row must be repaired, got {stats}")
    assert stats["per_label"]["Subject"] == 1, stats
    repaired = _subject_vector(g, "s-cold", cold_name)
    assert repaired == pytest.approx(fake(cold_name)), (
        "the repaired row must carry the vector for ITS OWN text")
    twin = _subject_vector(g, "s-cold", warm_name)
    assert twin is not None, "the twin must still exist"
    assert twin == pytest.approx(warm_vector), (
        "the twin sharing this id must be UNTOUCHED — a repair that addressed "
        "the id alone would overwrite it (#12570's collateral damage)")

    # ── and the loop CONVERGES: nothing is left missing ──────────────────
    missing, keyless = mod._residual_missing(g, "Subject")
    assert (missing, keyless) == (0, 0), (
        f"after the repair the surface must read clean, got "
        f"missing={missing} keyless={keyless}")
    assert mod._backfill_graph(proj.db, proj.graph_name, ["Subject"],
                               0, 500, required_dim=None)["updated"] == 0


def test_the_count_survives_a_burst_the_log_latches(cold_window, proj):
    """A bulk cold burst is COUNTED per row even though only one line is logged.

    This is the measurement an operator needs when a cold container serves a
    burst: "how many rows did this window cost me?" The log answers 1 (by
    design); the counter answers N.
    """
    before = _no_vector_counts()
    for i in range(5):
        proj._upsert_subject({"id": f"s-burst-{i}", "name": f"burst subject {i}"})
    assert _delta(before, _no_vector_counts()) == {f"{_GRAPH}:budget_expired": 5}


def test_an_ABSENT_identity_component_still_identifies_the_row(proj, monkeypatch):
    """A row whose identity component is ABSENT must be repaired, not skipped.

    This is the pin for `_null_safe_eq` specifically, and it exists because the
    loop test above cannot reach that branch: its cold row is a `Subject`, whose
    identity (`name`) is a single always-present component. `Event`'s "other"
    branch instead identifies a row by (subject, eventKind, object), and TWO of
    those are legitimately absent — an event written with no `object` and no
    `eventKind` is an ordinary shape, not a malformed row. In Cypher
    `NULL = NULL` is NULL-FALSY, so a plain-equality write excludes such a row
    from its OWN write: `_backfill_graph` then counts it `skipped` and the
    repair silently does nothing. That is exactly the failure the whole repair
    exists to prevent, so absence has to be part of the identity — and a mutant
    that reverts `_null_safe_eq` to `f"{node_expr} = {row_ref}"` reds here
    (`updated` 0, `skipped` 1) while every other case in this file stays green.

    Both components are taken from the identity the WRITE builds
    (`_write_where("Event", "other")`), not from a positional restatement here:
    `_EVENT_COLUMNS` is the one spelling of the surface's collected layout, so a
    copied index would drift from the write it is meant to grade.
    """
    g = proj.db.select_graph(proj.graph_name)
    cold_name = "cold event with no object"
    g.query(
        "CREATE (n:Event {eventId: $id, subject: $subject})",
        params={"id": "e-13417-null", "subject": cold_name})

    mod = _load_backfill()
    # Non-vacuity for the SHAPE, not just the outcome: both absent components must
    # reach the WRITE as NULL-safe comparisons, or this case would be grading a
    # row the write happens to match for some other reason.
    where = mod._write_where("Event", "other")
    for expr in ("n.eventKind", "n.object"):
        assert f"({expr} IS NULL AND row.k" in where, (
            f"{expr} is an identity component that may be ABSENT, so the write "
            f"must compare it NULL-safely — got {where}"
        )

    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    stats = mod._backfill_graph(proj.db, proj.graph_name, ["Event"],
                               0, 500, required_dim=None)

    assert stats["updated"] == 1, (
        "the row must be repaired on its OWN identity, absence included — a "
        f"`skipped` here is a repair that silently did nothing: {stats}"
    )
    assert stats["skipped"] == 0, stats
    assert _stored_vector(g, "Event", "eventId", "e-13417-null") == pytest.approx(
        fake(cold_name)), (
        "the repaired row must carry the vector for its own composed text "
        "(subject alone — both other components are absent)"
    )

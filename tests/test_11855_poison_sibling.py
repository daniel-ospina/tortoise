"""#11855 — one unreadable belief operand must not take the batch with it.

The defect, as filed: `update_point` stored a belief operand verbatim, so a
pre-guard node can carry a value the store cannot read as a NUMBER — `[1, 2]`
makes the raising spellings RAISE (`toString`/`toFloat`: "Type mismatch: … but was
List"), while `"abc"` and a Boolean coerce to NULL. All three are classes the
write guard (#11803) refuses. A raise in the projection happens at the QUERY, not
the row: one such node blanked every healthy claim in the same response.

The write half was closed in #11803 (`_sanitize_props` refuses a non-real-number
belief operand). **This file pins the READ half, which is the property the issue
calls out as the one a per-row test cannot catch:**

    a healthy row survives a poison sibling

That is a BLAST-RADIUS property, so every assertion here is made on a BATCH of
two points — one poison, one healthy — and the healthy one is required to keep
its own value and state. A per-row test that only asked "does the poison row
read as absent" would pass on a tree where the whole batch is blanked, which is
exactly the failure being guarded.

**State of the art, measured 2026-11-11 on `origin/main` @ `5c6149a1`** (see the
evidence comment on #11855): the totality fix is already in
`search_engine.NUMERIC_OPERAND_CYPHER_TEMPLATE`, whose `toFloatOrNull` /
`toStringOrNull` twins answer NULL for the types the raising spellings refuse
(measured: `toFloatOrNull(<List>)` → null). So the property is GREEN on the
tree this file lands on — this is a REGRESSION GUARD, not a fix.

**Discrimination, measured — stated exactly, because it is narrower than "every
assertion reds pre-fix":** against `be229efdb7df` (the commit the template's own
comment names as where the batch-scope conflation was measured) this file is
**3 failed, 12 passed**, and all three failures are the `[list]` case:

    FAILED …::test_both_rankers_keep_the_healthy_row[list]
    FAILED …::test_the_fail_open_decoration_does_not_fire[list]
    FAILED …::test_the_graph_wide_contested_scan_does_not_raise[list]
    redis.exceptions.ResponseError: Type mismatch: expected Datetime, Date,
    Time, Duration, String, Boolean, Integer, Float, Null, or Point but was List

All three are surfaces that coerce in CYPHER, so one `List` row took them down.
The `annotate_ep_batch` test was ALREADY green there — its coercion is Python-side
(`numeric_operand`, total since #11702 cycle 16) — so it does not discriminate
against `be229efdb7df`; it guards the batch-level ``except → {}`` blanking that a
future refactor of that read could reintroduce. `"abc"` and `True` pass on both
trees for two DIFFERENT reasons, and the distinction matters to anyone editing the
template: `"abc"` falls through the inner `CASE` and is nulled by the coercion
itself (`toFloat`/`toFloatOrNull` answers NULL for non-numeric text), while `True`
is nulled by the inner `CASE` (`toString(true)` is `'true'`) before the coercion
sees it. Only the `List` is a shape the raising spellings refuse.

⛔ ONE MEASURED CORRECTION, kept because the wrong version is the obvious thing to
reintroduce: the decoration test only discriminates because `_seed` anchors an
OPERATOR on the poison row. Written first against edge-less points it PASSED on
`be229efdb7` — `get_relationships_bounded` gates its α/β reads on `op_ids`, which
only an operator-anchored neighbour populates, so an edge-less batch runs no
belief read at all and the fail-open path is unreachable. A "passing" version of
that test proved nothing; the seed is load-bearing.

The surfaces pinned are the ones the issue names plus the rankers that read the
same columns through their own query:

* ``annotate_ep_batch`` — the `ep` block every served surface builds on.
* ``GraphRanker`` / ``StateRanker`` signal fetches — the two rankers.
* ``get_relationships_bounded`` — the fail-open seed the issue measured losing
  its peer decoration for every requested point.
* ``TortoiseEP.get_contested_claims`` — the graph-wide scan that raised out.

⛔ DELIBERATELY NOT PINNED HERE: the legacy ``has_ep`` / ``measured`` booleans on
the POISON row. They currently read ``True`` for an unreadable operand while
``ep_state`` reads ``absent`` — that is #11894's root (the flags are derived from
a raw ``IS NOT NULL`` test), and its fix will flip them. Pinning them here would
either freeze a known defect or collide with that fix. The property this file
owns is the one that must hold on BOTH sides of it: the healthy row's value, and
the batch not being discarded.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.ep import TortoiseEP  # noqa: E402, RUF100
from tortoise.ranking import GraphRanker, StateRanker  # noqa: E402, RUF100
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100
from tortoise.search_engine import (  # noqa: E402, RUF100
    EP_STATE_ABSENT,
    EP_STATE_MEASURED,
    annotate_ep_batch,
    get_relationships_bounded,
)

#: The healthy row's persisted belief: α/(α+β) with the `n.confidence` mirror
#: preferred (#2206). 0.75 is deliberately NOT 0.5 — the neutral Beta(1,1) mean
#: a blanked or absent row falls back to on a pre-#11702 tree, and the number a
#: batch-scope conflation would answer.
HEALTHY_ALPHA, HEALTHY_BETA, HEALTHY_MIRROR = 3.0, 1.0, 0.75
HEALTHY_MEAN = HEALTHY_MIRROR

#: Values a pre-guard write could store that the store cannot INSPECT. `[1, 2]`
#: is the shape #11855 filed (every coercion raises, including `toString`);
#: `"abc"` and `True` are the other two classes the same guard refuses at the
#: write (#11803) and every read side must therefore stay total over.
POISON_OPERANDS = [
    pytest.param([1, 2], id="list"),
    pytest.param("abc", id="unreadable-string"),
    pytest.param(True, id="boolean"),
]


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    yield s
    s.close()


def _seed(sdk, operand, *, symmetric: bool = False,
          operator_on_poison: bool = False) -> tuple[str, str]:
    """Two points: one carrying ``operand`` as its α, one with a real belief.

    The poison row is written through RAW Cypher on purpose: this is the LEGACY
    row shape the write guard (#11803) exists to stop new copies of, and the
    read side must still be total over the ones already stored. A crafted
    `update_point` cannot reproduce it any more — that is the write half of the
    fix, and relying on it here would make this file pass for the wrong reason.

    ``symmetric`` writes the healthy row a genuinely CONTESTED pair —
    Beta(1.5, 1.5), variance 0.0625, above `get_contested_claims`' 0.04 bar —
    so a graph-wide scan has a row it MUST list, which is what makes an empty
    scan distinguishable from a silent one.

    ``operator_on_poison`` anchors an operator whose target is the POISON row.
    Without it `get_relationships_bounded` performs NO belief read at all
    (measured: its α/β reads are gated on `op_ids`, which only an
    operator-anchored neighbour populates), so an edge-less batch cannot red
    for the property that test pins.
    """
    poison = sdk.create_point("statement", "the poison sibling")["id"]
    healthy = sdk.create_point("statement", "the healthy sibling")["id"]
    g = sdk._get_proj().g
    g.query(
        "MATCH (n:Point {id: $id}) SET n.posterior_alpha = $a, "
        "n.posterior_beta = 2.0",
        params={"id": poison, "a": operand},
    )
    if symmetric:
        # No mirror: a stale 0.75 beside a new pair is the mirror/pair split
        # #11702 closed, and it would mask what this row is here to do.
        g.query(
            "MATCH (n:Point {id: $id}) SET n.posterior_alpha = 1.5, "
            "n.posterior_beta = 1.5, n.confidence = null",
            params={"id": healthy},
        )
    else:
        g.query(
            "MATCH (n:Point {id: $id}) SET n.posterior_alpha = $a, "
            "n.posterior_beta = $b, n.confidence = $c",
            params={"id": healthy, "a": HEALTHY_ALPHA, "b": HEALTHY_BETA,
                    "c": HEALTHY_MIRROR},
        )
    if operator_on_poison:
        sdk.create_operator("IMPL", healthy, [poison])
    return poison, healthy


@pytest.mark.parametrize("operand", POISON_OPERANDS)
def test_the_batch_survives_one_unreadable_operand(sdk, operand):
    """`annotate_ep_batch` returns BOTH rows — never `{}`, never a raise.

    The failure this catches is the batch-scope one: the read is wrapped in a
    blanket `except Exception: return {}`, so a raise anywhere inside it
    discards every row, healthy ones included, and the empty dict reads to the
    caller as "no claim in this batch has a belief". Asserting the ROW SET (not
    just the healthy row's value) is what distinguishes `{}` from a correct
    answer — see the module docstring for what this does and does not
    discriminate against `be229efdb7df`.
    """
    poison, healthy = _seed(sdk, operand)
    out = annotate_ep_batch(sdk._get_proj().g, [poison, healthy])
    assert set(out) == {poison, healthy}, (
        "the batch lost rows — a single unreadable operand blanked the "
        "response, which is the whole-query-abort this issue is about")
    assert out[healthy].confidence_mean == pytest.approx(HEALTHY_MEAN)
    assert out[healthy].ep_state == EP_STATE_MEASURED


@pytest.mark.parametrize("operand", POISON_OPERANDS)
def test_the_poison_row_reads_absent_rather_than_crashing(sdk, operand):
    """The unreadable row is ABSENT — no fabricated mean, no exception.

    `ep_state` (#11702) already makes "an operand this process cannot read" the
    absent state. What this adds is that no surface ANSWERS A NUMBER for it: a
    fabricated mean is the pre-#11702 failure (the neutral 0.5), and this fails
    on it.

    ⛔ Stated exactly, because the stronger claim is wrong: this asserts the `ep`
    BLOCK answers no number for the poison row. It does NOT assert that on every
    surface — the rankers' `confidence` for a poison row is covered by
    `test_both_rankers_keep_the_healthy_row` only for the HEALTHY row, and no
    surface-wide no-number guarantee is held here. And within this test,
    `annotate_ep_batch` ENDS with a fill loop that gives every requested id the
    absent defaults, so "the row arrived and read absent" and "the row was
    dropped by the query and defaulted" are indistinguishable HERE; the
    row-presence property is owned by
    `test_the_batch_survives_one_unreadable_operand`, which asserts the key set
    (and is what reds if the batch-level `except` ever returns `{}`).
    """
    poison, healthy = _seed(sdk, operand)
    out = annotate_ep_batch(sdk._get_proj().g, [poison, healthy])
    assert out[poison].confidence_mean is None
    assert out[poison].ep_state == EP_STATE_ABSENT


@pytest.mark.parametrize("operand", POISON_OPERANDS)
def test_both_rankers_keep_the_healthy_row(sdk, operand):
    """The two rankers read the same columns through their own query, so both
    are checked on the same batch: a guard added to one does not cover the
    other, which is how this class kept reappearing one surface at a time."""
    poison, healthy = _seed(sdk, operand)
    proj = sdk._get_proj()
    for ranker in (GraphRanker(projection=proj),
                   StateRanker(projection=proj)):
        signals = ranker._fetch_point_signals([poison, healthy])
        assert healthy in signals, (
            f"{type(ranker).__name__} dropped the healthy row — its signal "
            "fetch took the batch down with the poison sibling")
        conf = signals[healthy].get(
            "confidence", signals[healthy].get("state_confidence"))
        assert conf == pytest.approx(HEALTHY_MEAN)


@pytest.mark.parametrize("operand", POISON_OPERANDS)
def test_the_fail_open_decoration_does_not_fire(sdk, operand):
    """`get_relationships_bounded` must NOT take its fail-open path (#6971).

    This is the surface the issue measured: on a batch-wide abort it absorbs the
    exception and returns an ALL-EMPTY seed labelled by `decoration_error`, so
    every requested point — healthy ones included — loses its decoration.

    ⛔ What carries the discrimination is the MARKER plus a NON-EMPTY entry
    list, and BOTH are needed — measured: with no operator edge the surface
    performs no belief read at all (its α/β reads are gated on `op_ids`), so an
    edge-less batch is `[]` with `decoration_error == ""` on the success path
    and `[]` with the marker set on the fail-open path. Seeding the operator is
    what makes the peer-belief read actually run over the poison row.
    """
    poison, healthy = _seed(sdk, operand, operator_on_poison=True)
    g = sdk._get_proj().g
    decoration = get_relationships_bounded(g, [poison, healthy])
    # The poisoned point must appear as a DECORATED PEER of the healthy row: that
    # is what the fail-open path destroys, and unlike a key-set comparison it can
    # actually fail (the seed — success AND fail-open — is built from the
    # requested ids, so the key set is identical on every path).
    assert any(e.get("related_id") == poison for e in decoration[healthy]), (
        "the healthy row lost its decoration of the poison peer "
        f"(entries: {decoration[healthy]!r})")
    assert not getattr(decoration, "decoration_error", ""), (
        "the decoration took its fail-open path — every requested point's "
        "decoration was reset to empty "
        f"({getattr(decoration, 'decoration_error', '')!r})")


@pytest.mark.parametrize("operand", POISON_OPERANDS)
def test_the_graph_wide_contested_scan_does_not_raise(sdk, operand):
    """The scan the issue measured raising out of the read entirely.

    It sweeps every epistemic label with the SAME coercion template, so a `List`
    on one node made the whole report unavailable rather than one entry wrong.

    The assertion is POSITIVE on purpose: the healthy row carries a genuinely
    contested pair (Beta(1.5, 1.5), variance 0.0625 > the 0.04 bar), so it must
    be LISTED. `assert isinstance(claims, list)` and `assert poison not in ids`
    were both tried first and removed: the first is a contract tautology and the
    second cannot fail (an unreadable operand has no admissible variance), so
    a silently-empty scan would have passed both.
    """
    # The poison row is seeded but not inspected: this scan's assertion is about
    # the healthy row it must still list, and the unreadable row's exclusion is
    # not assertable (no admissible variance) — hence the underscore.
    _poison, healthy = _seed(sdk, operand, symmetric=True)
    claims = TortoiseEP(sdk._get_proj()).get_contested_claims()
    ids = {c.get("id") for c in claims}
    assert healthy in ids, (
        "the graph-wide scan completed but did not list the contested healthy "
        f"row — the whole report came back {len(claims)} row(s)")

"""#7852 — the Layer-1 direction STORAGE vocabulary: four model values.

ONTOLOGY §8 records direction on the write with FOUR values — `->`
(source→target), `<-` (target→source), `<->` (both ways), `-` (none: the
connection exists and NO confidence transfers). The runtime before this change
stored and compared only the two legacy tokens (`unidirectional` ≡ `->`,
`bidirectional` ≡ `<->`), so the model's `<-` and `-` could neither be recorded
nor interpreted (and a `<-` written as a legacy token would be read as its
opposite).

This pins the migration:
  * the two legacy tokens stay ACCEPTED and READABLE — NO data migration (rows
    keep the token they were written with; readers normalize);
  * the four model values round-trip verbatim through both write paths
    (`create_operator` → the operator node, `create_direct_edge` → the edge);
  * EP interprets every value in `direction_flows` terms — a `<-` operator
    reaches the SOURCE, a `-` operator reaches NEITHER, and the legacy tokens
    keep their exact historical meaning;
  * the idempotent-ingest lookup treats the legacy and model spellings of one
    meaning as the SAME operator (storage is verbatim, so a string compare
    alone would mint a silent duplicate);
  * the commit schema and the shared vocabulary cannot drift.

Runnable embedded:
  TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_direction_storage_7852.py -q
"""
from __future__ import annotations

import typing

import pytest

from tortoise.ep import TortoiseEP
from tortoise.live import (
    ACCEPTED_DIRECTION_VALUES,
    DIRECTION_ALIASES,
    DIRECTION_VALUES,
    LEGACY_DIRECTION_VALUES,
    canonical_direction,
    direction_flows,
    direction_spellings,
    is_default_direction,
)
from tortoise.sdk import TortoiseSDK

MODEL_VALUES = ("->", "<-", "<->", "-")
LEGACY_VALUES = ("unidirectional", "bidirectional")
ALL_VALUES = MODEL_VALUES + LEGACY_VALUES


def _point(sdk: TortoiseSDK, content: str) -> dict:
    return sdk.create_point("statement", content, status="live")


def _operator_stored(sdk: TortoiseSDK, op_id: str) -> str | None:
    return sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.direction", params={"id": op_id}
    ).result_set[0][0]


def _edge_stored(sdk: TortoiseSDK, src: str, tgt: str, rel: str = "IMPL") -> str | None:
    rows = sdk._get_proj().g.query(
        f"MATCH (:Point {{id:$src}})-[r:{rel}]->(:Point {{id:$tgt}}) "
        "RETURN r.direction",
        params={"src": src, "tgt": tgt},
    ).result_set
    return rows[0][0] if rows else None


def _edge_msg(sdk: TortoiseSDK, src: str, tgt: str, rel: str = "IMPL",
              slot: str = "msg") -> tuple[float, float] | None:
    """Read the forward (`msg_*`) or backward (`back_msg_*`) slot on an edge."""
    rows = sdk._get_proj().g.query(
        f"MATCH (:Point {{id:$src}})-[r:{rel}]->(:Point {{id:$tgt}}) "
        f"RETURN r.{slot}_alpha, r.{slot}_beta",
        params={"src": src, "tgt": tgt},
    ).result_set
    if not rows or rows[0][0] is None:
        return None
    return (float(rows[0][0]), float(rows[0][1]))


def _has_message(msg: tuple[float, float] | None) -> bool:
    """A real EP message — an absent or zeroed slot carries none. (The cache
    loader seeds `coalesce(msg_*,0.0)`, so a non-receiving slot is flushed as
    `(0.0, 0.0)`, not NULL.)"""
    return msg is not None and msg != (0.0, 0.0)


def _run_ep(sdk: TortoiseSDK, seeds: list[str]) -> dict[str, float]:
    proj = sdk._get_proj()
    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
                    evidence=evidence)
    ep.run(seeds, max_hops=2)
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN n.id, n.confidence"
    ).result_set
    return {r[0]: r[1] for r in rows} if rows else {}


@pytest.fixture()
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "direction.db"))
    yield s
    s.close()


# ══════════════════════════════════════════════════════════════════════
# 1. The vocabulary — one declaration, no per-site re-spelling
# ══════════════════════════════════════════════════════════════════════

def test_vocabulary_is_four_model_values_plus_two_legacy_aliases():
    assert frozenset(MODEL_VALUES) == DIRECTION_VALUES
    assert frozenset(LEGACY_VALUES) == LEGACY_DIRECTION_VALUES
    assert frozenset(ALL_VALUES) == ACCEPTED_DIRECTION_VALUES
    assert DIRECTION_ALIASES == {"unidirectional": "->", "bidirectional": "<->"}


@pytest.mark.parametrize("value,expected", [
    ("->", (True, False)),
    ("<-", (False, True)),
    ("<->", (True, True)),
    ("-", (False, False)),
    # legacy aliases keep their exact historical meaning
    ("unidirectional", (True, False)),
    ("bidirectional", (True, True)),
    # absent / unknown → the runtime's historical mutual default
    (None, (True, True)),
    ("sideways", (True, True)),
])
def test_direction_flows(value, expected):
    assert direction_flows(value) == expected


@pytest.mark.parametrize("value,canonical", [
    ("->", "->"), ("<-", "<-"), ("<->", "<->"), ("-", "-"),
    ("unidirectional", "->"), ("bidirectional", "<->"),
    (None, None), ("sideways", None),
])
def test_canonical_direction_maps_legacy_to_model(value, canonical):
    assert canonical_direction(value) == canonical


def test_direction_spellings_are_equivalence_classes():
    """One meaning, two spellings on disk — string comparisons must cover both."""
    assert direction_spellings("->") == frozenset({"->", "unidirectional"})
    assert direction_spellings("unidirectional") == frozenset({"->", "unidirectional"})
    assert direction_spellings("<->") == frozenset({"<->", "bidirectional"})
    assert direction_spellings("<-") == frozenset({"<-"})
    assert direction_spellings("-") == frozenset({"-"})


def test_commit_schema_literal_matches_the_shared_vocabulary():
    """The wire schema's accepted tokens must equal `live.ACCEPTED_DIRECTION_VALUES`
    — a widening of one without the other would reject a token the runtime accepts
    (or accept one it refuses)."""
    from tortoise.commit_schema import Operator

    annotation = Operator.model_fields["direction"].annotation
    literal_args: set[str] = set()
    for arg in typing.get_args(annotation):
        nested = typing.get_args(arg)
        literal_args |= set(nested) if nested else ({arg} if isinstance(arg, str) else set())
    assert literal_args == set(ACCEPTED_DIRECTION_VALUES)


# ══════════════════════════════════════════════════════════════════════
# 2. Storage round-trip — every value, both write paths
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("value", ALL_VALUES)
def test_operator_direction_round_trips(sdk, value):
    """A stored operator records EXACTLY the token it was written with — the
    model's four values and the two legacy aliases, all readable."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]], direction=value)
    assert op["direction"] == value
    assert _operator_stored(sdk, op["id"]) == value


@pytest.mark.parametrize("value", ALL_VALUES)
def test_direct_edge_direction_round_trips(sdk, value):
    a, b = _point(sdk, "a"), _point(sdk, "b")
    sdk.create_direct_edge("IMPL", a["id"], b["id"], direction=value)
    assert _edge_stored(sdk, a["id"], b["id"]) == value


def test_out_of_vocabulary_direction_is_refused(sdk):
    """A typo is refused by name, not silently persisted or defaulted —
    including when a valid `semantics` is supplied (the meaning must not
    launder an unrecognised direction into the vocabulary)."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    with pytest.raises(ValueError, match="direction must be one of"):
        sdk.create_operator("IMPL", a["id"], [b["id"]], direction="sideways")
    with pytest.raises(ValueError, match="direction must be one of"):
        sdk.create_direct_edge("IMPL", a["id"], b["id"], direction="sideways")
    with pytest.raises(ValueError, match="direction must be one of"):
        sdk.create_operator("IMPL", a["id"], [b["id"]],
                            direction="sideways", semantics="addresses")


def test_is_default_direction_only_true_for_absent_or_mutual():
    """`is_default_direction` gates the semantics override, so an
    UNRECOGNISED token must NOT read as default — otherwise a declared
    meaning would silently overwrite the caller's typo."""
    for value in (None, "bidirectional", "<->"):
        assert is_default_direction(value) is True, value
    for value in ("sideways", "->", "unidirectional", "<-", "-"):
        assert is_default_direction(value) is False, value


def test_defaults_are_unchanged(sdk):
    """The #7813 default is untouched: absent IMPL → `bidirectional`, absent
    NAND (extraction default) → `unidirectional`; the SDK signature default
    stays the literal `bidirectional`."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    impl = sdk.create_operator("IMPL", a["id"], [b["id"]])
    nand = sdk.create_operator("NAND", a["id"], [b["id"]], direction=None)
    assert _operator_stored(sdk, impl["id"]) == "bidirectional"
    assert _operator_stored(sdk, nand["id"]) == "unidirectional"


# ══════════════════════════════════════════════════════════════════════
# 3. EP interprets every value (the pre-migration code compared two names)
# ══════════════════════════════════════════════════════════════════════

def _set_evidence(sdk: TortoiseSDK, pid: str, alpha: float, beta: float) -> None:
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=$al, n.ep_beta=$be, "
        "n.baseline_set=true",
        params={"id": pid, "al": alpha, "be": beta},
    )


def _operator_messages(sdk: TortoiseSDK, op_id: str, src: str, tgt: str):
    """(source-slot, target-slot) messages written on an operator's edges."""
    src_msg = _edge_msg(sdk, op_id, src)
    tgt_msg = _edge_msg(sdk, op_id, tgt)
    return src_msg, tgt_msg


@pytest.mark.parametrize("value,source_has,target_has", [
    ("->", False, True),
    ("<-", True, False),
    ("<->", True, True),
    ("-", False, False),
    ("unidirectional", False, True),
    ("bidirectional", True, True),
])
def test_ep_reaches_only_the_endpoint_the_direction_flows_to(
        sdk, value, source_has, target_has):
    """An operator's message reaches ONLY the endpoint the recorded direction
    flows to. Pre-migration every non-`bidirectional` value reached the TARGET,
    so `<-` was propagated as its opposite and `-` as a mutual default."""
    a, b = _point(sdk, "strong"), _point(sdk, "neutral")
    _set_evidence(sdk, a["id"], 12.0, 1.0)
    _set_evidence(sdk, b["id"], 1.0, 1.0)
    op = sdk.create_operator("IMPL", a["id"], [b["id"]], direction=value)

    _run_ep(sdk, seeds=[op["id"]])

    src_msg, tgt_msg = _operator_messages(sdk, op["id"], a["id"], b["id"])
    assert _has_message(src_msg) is source_has, (value, src_msg)
    assert _has_message(tgt_msg) is target_has, (value, tgt_msg)


def test_direct_edge_reverse_direction_writes_the_back_slot(sdk):
    """On a direct edge `<-` persists the BACK-message (to the source) and no
    forward message — the exact inverse of the legacy `unidirectional`."""
    a, b = _point(sdk, "strong-source"), _point(sdk, "target")
    _set_evidence(sdk, a["id"], 12.0, 1.0)
    _set_evidence(sdk, b["id"], 1.0, 1.0)
    sdk.create_direct_edge("IMPL", a["id"], b["id"], direction="<-")

    _run_ep(sdk, seeds=[a["id"]])

    assert not _has_message(_edge_msg(sdk, a["id"], b["id"], slot="msg")), \
        "a backward-only edge must not write the forward slot"
    assert _has_message(_edge_msg(sdk, a["id"], b["id"], slot="back_msg")), \
        "a backward-only edge must write the back-message (to the source)"


def test_no_direction_transfers_nothing(sdk):
    """`-` means the connection exists and NO confidence transfers: the factor
    writes no message in either slot, and the target does not move."""
    a, b = _point(sdk, "strong"), _point(sdk, "neutral")
    _set_evidence(sdk, a["id"], 12.0, 1.0)
    _set_evidence(sdk, b["id"], 1.0, 1.0)
    op = sdk.create_operator("IMPL", a["id"], [b["id"]], direction="-")

    res = _run_ep(sdk, seeds=[op["id"]])

    src_msg, tgt_msg = _operator_messages(sdk, op["id"], a["id"], b["id"])
    assert not _has_message(src_msg) and not _has_message(tgt_msg), (src_msg, tgt_msg)
    assert res.get(b["id"], 0.5) == pytest.approx(0.5, abs=0.02), \
        "a `-` edge must not move the target"


# ══════════════════════════════════════════════════════════════════════
# 4. Idempotent ingest: legacy and model spellings are the SAME operator
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("written,looked_up", [
    ("->", "unidirectional"),
    ("unidirectional", "->"),
    ("<->", "bidirectional"),
    ("bidirectional", "<->"),
])
def test_find_operator_matches_the_equivalence_class(sdk, written, looked_up):
    """Storage is verbatim, so a legacy-token lookup must still find a
    model-token operator (and vice versa) — otherwise re-ingest mints a
    duplicate of the same operator."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]], direction=written)
    found = sdk._find_operator("IMPL", [a["id"], b["id"]], direction=looked_up)
    assert found is not None, (written, looked_up)
    assert found["kind"] == "exact"
    assert found["id"] == op["id"]


def test_find_operator_distinguishes_opposite_one_way_directions(sdk):
    """`->` and `<-` are different operators — the equivalence class must not
    collapse them."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    sdk.create_operator("IMPL", a["id"], [b["id"]], direction="->")
    assert sdk._find_operator("IMPL", [a["id"], b["id"]], direction="<-") is None


# ══════════════════════════════════════════════════════════════════════
# #10520 — a RAW-WRITTEN non-scalar direction is ABSENT, never a crash
# ══════════════════════════════════════════════════════════════════════

def test_a_raw_written_non_scalar_direction_reads_as_ABSENT_not_a_TypeError(sdk):
    """#10520 — the ONE read-side normalizer must read junk as ABSENT.

    `canonical_direction` tests membership in a frozenset and then does a dict
    lookup on the STORED value. A raw-written non-scalar (list/map — reachable by
    hand edit, a foreign/legacy writer, or a bulk import) is UNHASHABLE, so both
    of those raise `TypeError: unhashable type: 'list'` instead of reading the
    junk as absent. `None` already means "absent" to every consumer
    (`direction_flows(None) == (True, True)` — the runtime's historical mutual
    default), which is the same reading `claim_class.normalize` gives a junk
    token: junk is ABSENT, never a third state and never a crash.

    The EP LOAD path is the damage this pins: one junk property aborted a belief
    run it is not about (`TortoiseEP._affected_factors` -> `factor_direction`
    -> `direction_flows`).
    """
    for junk in (["sideways"], {"sideways": True}):
        assert canonical_direction(junk) is None, (
            f"a non-scalar {junk!r} must read as ABSENT (None), not be tested "
            "for membership in the four-value frozenset")
        assert direction_flows(junk) == (True, True), (
            "an absent/unknown direction flows BOTH ways — the runtime's "
            "historical mutual default")

    # The stored-value route, on a real node: the EP load path raises on it.
    a, b = _point(sdk, "raw junk direction source"), _point(sdk, "raw junk direction target")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]])
    sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) SET o.direction = ['sideways']",
        params={"id": op["id"]},
    )
    proj = sdk._get_proj()
    factors = TortoiseEP(proj)._affected_factors({a["id"]})  # must not raise
    assert isinstance(factors, list)


def test_a_raw_written_non_scalar_EDGE_direction_reads_as_ABSENT(sdk):
    """#10520 — the same junk, read back as a stored EDGE value.

    What this pins is the ONE read-side normalizer over a value that lives on an
    edge: junk ⇒ ``None`` ⇒ ``direction_flows == (True, True)``. ⛔ It does NOT
    pin a write door: ``create_direct_edge`` reads the CALLER's ``direction`` and
    never a stored one, ``invalidate_point`` reads none of any kind (it has no
    ``direction`` parameter and transfers no edges), and ``_connections_conflict``
    compares two BUNDLE dicts (``_find_operator``'s exactly-once dedup is the one
    path that does compare a stored token, but inside a Cypher predicate, so no
    Python reader touches it) — so the crash this docstring used to attribute to
    them cannot happen there (#11050, measured surface by surface; a docstring is
    the only place a reader learns what a pin covers, so a wrong one sends the
    next lane to "fix" a door that has no defect).
    """
    a, b = _point(sdk, "raw junk edge source"), _point(sdk, "raw junk edge target")
    sdk.create_direct_edge("IMPL", a["id"], b["id"])
    sdk._get_proj().g.query(
        "MATCH (:Point {id:$a})-[r:IMPL]->(:Point {id:$b}) "
        "SET r.direction = ['sideways']",
        params={"a": a["id"], "b": b["id"]},
    )
    assert canonical_direction(_edge_stored(sdk, a["id"], b["id"])) is None
    assert direction_flows(_edge_stored(sdk, a["id"], b["id"])) == (True, True)


# ══════════════════════════════════════════════════════════════════════
# #10520 A3 — the same non-scalar, arriving through INGEST
# ══════════════════════════════════════════════════════════════════════

def test_ingest_reports_a_non_scalar_direction_as_a_SHAPE_violation(sdk):
    """#10520 (A3) — a non-scalar ``direction`` reaches the contract as a REPORT.

    ``_check_connection`` used to screen the caller's ``direction`` with
    ``direction not in _INGEST_DIRECTION_VALUES`` — a raw frozenset membership
    test — so a non-scalar (``["->"]``, reachable from a JS/Python caller or a
    bulk import) raised ``TypeError: unhashable type: 'list'`` and aborted the
    WHOLE validator with a traceback instead of returning the violation it
    exists to return. The sibling ``to`` arm several checks above already guards
    the identical hazard (``isinstance(t, str)`` ⇒ a SHAPE violation), so the
    contract disagreed with itself about the same value class.

    ⛔ ``TypeError`` is not a ``ValueError``, so ``pytest.raises(ValueError)``
    below is what makes this red on the pre-fix contract: the phase-2 raise the
    contract promises never happens, and the caller gets a traceback.

    Scope note (measured): the INGEST branch this issue originally named — the
    partial-absorb guard — reads the bundle's CALLER-SUPPLIED ``direction``
    (``sdk.py``: ``self._canonical_direction(op_type, direction)``), never a
    stored graph value, and ``_find_operator`` compares the stored token in a
    CYPHER predicate; so a raw-written junk ``direction`` cannot be read there
    at all (#11050). This arm is the one ingest surface that can hold a
    non-scalar, so it is the one that can be pinned.
    """
    bundle = {
        "points": [
            {"ref": "ja", "kind": "statement", "content": "junk direction a"},
            {"ref": "jb", "kind": "statement", "content": "junk direction b"},
        ],
        "connections": [
            {"from": "ja", "to": "jb", "operator": "IMPL",
             "direction": ["sideways"]},
        ],
    }
    viols = sdk._validate_bundle(bundle, promotion_policy="gated")
    assert viols, "a non-scalar `direction` is a SHAPE violation, not a clean bundle"
    _dir_viols = [v for v in viols
                  if v.get("section") == "connections" and "direction" in v["message"]]
    assert _dir_viols, viols
    # The WORDING is pinned once, here, in the commit that introduces it: the
    # violation names the type it actually got (the sibling ``to`` shape arm's
    # idiom) instead of the vocabulary it was tested against.
    assert "got list" in _dir_viols[0]["message"], _dir_viols

    with pytest.raises(ValueError) as exc:  # TypeError is NOT a ValueError
        sdk.ingest(bundle, promotion_policy="gated")
    assert "direction" in str(exc.value), str(exc.value)

    # …and other non-scalar shapes, so the fix is not keyed on one shell: a
    # mapping is unhashable too, and ``7``/``True`` are hashable-but-not-tokens.
    for junk in ({"sideways": True}, 7, True):
        _b = {
            "points": [
                {"ref": "ja", "kind": "statement", "content": "junk direction a"},
                {"ref": "jb", "kind": "statement", "content": "junk direction b"},
            ],
            "connections": [
                {"from": "ja", "to": "jb", "operator": "IMPL",
                 "direction": junk},
            ],
        }
        _viols = sdk._validate_bundle(_b, promotion_policy="gated")
        # ⛔ The SHAPE wording, not merely the word "direction": the pre-fix
        # vocabulary refusal ("must be one of …, got 7") also says "direction",
        # so a bare substring test is satisfied by the behaviour this pin
        # exists to replace, and the hashable arms would silently stop
        # discriminating if the screen were narrowed back to unhashables.
        _shape = [v for v in _viols if "must be a string token" in v["message"]]
        assert _shape, (_b, _viols)
        assert any(f"got {type(junk).__name__}" in v["message"]
                   for v in _shape), (_b, _viols)

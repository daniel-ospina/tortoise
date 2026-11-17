"""A BLOCKED allowance dimension is a CLAIM, and this file re-checks it (#9559 R11).

**Why this file exists — the measured cost, not a preference.** R11's own rule is
*"the meter must be wired into the gate before any configuration is made"*, and
the gate enforces it by declaring a dimension BLOCKED: a configured allowance on
a blocked dimension refuses (fail-closed) instead of capping on a figure the
instrument itself says is unfit. That is right. What was missing is that the
block's **grounds** were prose that nothing re-read.

Measured consequence: ``postgres_storage``'s ``blocked_reason`` said *"no per-org
byte meter … BLOCKED until a per-org attribution exists"*. #12762 then landed
exactly that attribution — ``postgres_bytes.PerOrgPostgresBytes.for_org`` — and
the ONE registry (:mod:`tortoise.cost_dimensions`) declares the dimension
``state=measured`` with an explicit ``OVERRIDES:`` of *the #4492 sentence the
block kept quoting*. The block still said the attribution was missing, and the
sentence was restated in ``pricing.storage_limits``' docstring too. Five separate
lanes re-derived the falsified premise off this issue and none could land it.

So a block now carries :attr:`~tortoise.allowance_gate.AllowanceDimension.release`
— machine-readable conditions that would LIFT it — and this file evaluates every
one of them **against the artifact it names**, on every PR:

* a condition whose evidence is SATISFIED must not still be quoted as the live
  obstacle (that is the exact staleness above, and it REDs here);
* a block whose conditions have ALL fired REDs with "lift it", so a dimension
  cannot stay blocked forever on grounds that no longer exist;
* an unknown token REDs, so a typo never becomes a condition that silently never
  fires.

The predicates live HERE rather than in production on purpose: the gate pays
nothing for them, and the evidence is re-derived from the tree instead of being
trusted from a comment.

⚠️ LIMITS OF THE PHRASE GUARD (stated, because a guard read as stronger than it is
is the defect it exists to prevent). It is:

* **lexical** — a paraphrase defeats it. It catches the sentences this issue
  actually emitted, plus each condition's own wording in the artifact that owns
  it; it does not catch any possible restatement. The predicates are the
  substantive half; the phrase check is the cheap tripwire for the specific
  regression that happened.
* **scoped to named SURFACES** — live ``blocked_reason`` strings, plus
  ``postgres_storage.ATTRIBUTION_NOTE`` and the docstrings of the two functions a
  future arming lane reads (``metering.storage_overage_cost_usd`` and
  ``pricing.storage_limits``). Module-level comments that QUOTE the old sentence
  in order to supersede it are deliberately not scanned: quoting superseded
  wording to replace it is not the defect.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tortoise import allowance_gate, cost_dimensions, graph_storage, postgres_bytes, pricing
from tortoise.allowance_gate import (
    DIMENSIONS,
    RELEASE_CONDITIONS,
)

_REPO = Path(__file__).resolve().parent.parent


def _dim(name: str) -> allowance_gate.AllowanceDimension:
    for d in DIMENSIONS:
        if d.dimension == name:
            return d
    raise AssertionError(f"no declared allowance dimension {name!r}")


def _registry(name: str) -> cost_dimensions.Dimension:
    return cost_dimensions.by_name(name)


# ── the predicates: every one reads an ARTIFACT, never a promise ──────────────
#
# Each returns (satisfied, evidence). ``evidence`` is printed on failure, so a
# RED tells the reader WHAT was measured rather than only that something drifted.

def _per_org_postgres_attribution() -> tuple[bool, str]:
    """A per-org Postgres BYTE attribution that is a MEASUREMENT, not a split."""
    has_reader = callable(getattr(postgres_bytes, "per_org_bytes", None))
    has_per_org = hasattr(postgres_bytes.PerOrgPostgresBytes, "for_org")
    declares_measured = "MEASURED at write time" in str(
        _registry("postgres_storage").attribution)
    return (
        has_reader and has_per_org and declares_measured,
        f"per_org_bytes={has_reader} PerOrgPostgresBytes.for_org={has_per_org} "
        f"registry declares 'MEASURED at write time'={declares_measured}",
    )


def _postgres_residency_gauge() -> tuple[bool, str]:
    """A per-org RESIDENCY GAUGE **this gate can afford on a write path**.

    ⛔ Not "the gate has a meter at all", and the difference is measured, not
    pedantic: reviewer round 1 showed that the weaker form reports SATISFIED when
    the existing ledger READER is wired as the meter, which is the single most
    likely next edit and exactly the figure the block names as insufficient. So
    the two known-inadequate readers are named and REFUSED here; what remains is a
    disclosed residual (a THIRD inadequate reader could still satisfy this line),
    and the block's own text carries the substance.
    """
    meter = _dim("postgres_storage").meter
    if meter is None:
        return False, "postgres_storage meter=None (nothing to read per org)"
    # The audit: a whole-table aggregation per call. The ledger column: a window
    # ADDEND, not a stored footprint. Neither is a gauge.
    from tortoise import metering

    inadequate = {
        # ⛔ ONE entry per reader that EXISTS. ``read_per_org_bytes`` was listed
        # here and is not a name in the module (the audit entry point is
        # ``per_org_bytes``); ``getattr(..., None)`` turned that into a dead arm
        # guarded by a dict lookup, so it protected nothing.
        getattr(postgres_bytes, "per_org_bytes", None): "the whole-table audit",
        getattr(metering, "get_postgres_bytes_written", None): "the window addend",
    }
    inadequate.pop(None, None)
    why = inadequate.get(meter)
    if why:
        return False, (
            f"postgres_storage meter={meter!r} is {why}, which the block names "
            f"as insufficient on a write path")
    return True, f"postgres_storage meter={meter!r} (not either known-inadequate reader)"


def _storage_included_amounts_confirmed() -> tuple[bool, str]:
    """The free/anon included storage amounts are OWNER-CONFIRMED.

    ⛔ FAIL CLOSED on anything that is not POSITIVE evidence. Two holes were
    measured here before this shape: (a) an earlier version read the TOP level of
    ``pricing.json``, missed the real path, defaulted to ``""`` and reported the
    block's ground as SATISFIED; (b) a "not pending" test over two uppercase
    phrases reported SATISFIED for ``TBD-BY-MEASUREMENT``, ``PENDING owner
    signoff`` and a lowercase reword — any of which would have told a lane to
    lift a block whose ground was still true. So the question asked is the
    positive one: does the artifact SAY the amounts are confirmed?
    """
    storage = (pricing._load().get("billing") or {}).get("storage") or {}
    status = storage.get("_included_amounts_status")
    if not isinstance(status, str) or not status.strip():
        return False, (
            "product/pricing.json billing.storage._included_amounts_status is "
            f"absent or not text ({status!r}) — no evidence either way, so NOT "
            f"satisfied")
    flat = " ".join(status.split())
    confirmed = flat.upper().startswith("OWNER-CONFIRMED")
    return confirmed, (
        "product/pricing.json billing.storage._included_amounts_status: "
        f"confirmed={confirmed} · {flat[:120]}")


def _vector_index_measurable() -> tuple[bool, str]:
    """The HNSW vector index is measurable, so the graph reading may carry a cap."""
    note = str(graph_storage.PRECISION_NOTE)
    forbids = "until the vector index is measurable" in note
    excluded = graph_storage.VECTOR_INDEX_EXCLUDED in graph_storage.EXCLUDED_OVERHEAD
    return not (forbids or excluded), (
        f"graph_storage.PRECISION_NOTE still forbids a cap={forbids}; "
        f"VECTOR_INDEX_EXCLUDED still declared as excluded overhead={excluded}")


_PREDICATES = {
    "per_org_postgres_attribution": _per_org_postgres_attribution,
    "postgres_residency_gauge": _postgres_residency_gauge,
    "storage_included_amounts_confirmed": _storage_included_amounts_confirmed,
    "vector_index_measurable": _vector_index_measurable,
}

#: Phrases a SATISFIED condition must NOT appear as, in a live ``blocked_reason``.
#: This is the staleness guard itself: while ``per_org_postgres_attribution`` was
#: satisfied, the block still said "no per-org byte meter" — one line below would
#: have caught it, and does now.
_RELEASED_PHRASES = {
    "per_org_postgres_attribution": (
        "no per-org byte meter",
        "no per-org figure exists",
        "no per-org byte ownership",
        "BLOCKED until a per-org attribution exists",
    ),
    "postgres_residency_gauge": (
        # ⛔ The LIVE wording, not an older paraphrase. This arm used to read
        # "no per-org byte meter", which appears nowhere in the block it guards:
        # on the day a gauge exists, the still-quoted "no per-org RESIDENCY
        # GAUGE" would not have been flagged — the one sentence this issue
        # rewrote would have had no covering phrase.
        "no per-org RESIDENCY GAUGE",
        "no per-org figure this gate can read ON A WRITE PATH",
    ),
    "storage_included_amounts_confirmed": (
        "free/anon included amounts are still undecided",
    ),
    "vector_index_measurable": (
        "cap MUST NOT be wired",
        "until the vector index is measurable",
    ),
}


def test_every_release_token_has_a_predicate_and_vice_versa():
    """A token nobody evaluates is a condition that can never fire.

    Mutation this REDs: add a token to ``RELEASE_CONDITIONS`` without a predicate
    here (it would then be declared as a release with no evidence behind it), or
    delete a token the dimensions still cite. ⛔ ``_RELEASED_PHRASES`` is in the
    same assertion on purpose: a token with a predicate but NO phrase list makes
    the staleness guard vacuous for it, which is a silent hole rather than a
    missing guard.
    """
    assert set(_PREDICATES) == set(RELEASE_CONDITIONS), (
        "RELEASE_CONDITIONS and the revalidating predicates have drifted: "
        f"declared-only={sorted(set(RELEASE_CONDITIONS) - set(_PREDICATES))} "
        f"predicate-only={sorted(set(_PREDICATES) - set(RELEASE_CONDITIONS))}")
    assert set(_RELEASED_PHRASES) == set(RELEASE_CONDITIONS), (
        "a release token has no phrase list, so its staleness guard is "
        f"vacuous: {sorted(set(RELEASE_CONDITIONS) - set(_RELEASED_PHRASES))}")


@pytest.mark.parametrize("dim", [d for d in DIMENSIONS if d.blocked],
                         ids=lambda d: d.dimension)
def test_a_blocked_dimension_still_waits_on_something(dim):
    """★ THE LOAD-BEARING ONE: a block must not outlive all of its grounds.

    Mutation this REDs: leave a dimension blocked after the change that satisfies
    its last condition — the shape that kept ``postgres_storage`` (and five lanes)
    on a falsified premise. On that day the fix is to lift the block, or to add
    the NEW condition **with its evidence**; it is never to leave the block
    standing on grounds that have fired.
    """
    results = {t: _PREDICATES[t]() for t in dim.release}
    unsatisfied = [t for t, (ok, _) in results.items() if not ok]
    assert unsatisfied, (
        f"{dim.dimension!r} is BLOCKED but EVERY declared release condition is "
        f"already satisfied:\n"
        + "\n".join(f"  {t}: {ev}" for t, (_, ev) in results.items())
        + "\n  ⇒ lift the block, or add the condition that still holds WITH its "
          "evidence. A block with no live ground is the stale-flag class #9559 "
          "paid for.")


def test_the_included_amounts_predicate_needs_POSITIVE_evidence(monkeypatch):
    """Every non-confirming status reads UNSATISFIED — the fail-open class.

    Reviewer round 2 measured the hole: a "not <two uppercase phrases>" test called
    ``TBD-BY-MEASUREMENT``, ``PENDING owner signoff`` and a lowercase reword
    SATISFIED, which would tell a lane to lift a block whose ground was still
    true. The predicate asks the POSITIVE question now, so these must all fail.
    """
    cases = {
        "DERIVED, NOT OWNER-CONFIRMED. …": False,
        "TBD-BY-MEASUREMENT": False,
        "PENDING owner signoff": False,
        "the amounts are still not decided": False,
        "": False,
        "   ": False,
        None: False,
        "OWNER-CONFIRMED 2026-11-20 (comment 123456)": True,
        "owner-confirmed after review": True,
    }
    for status, expected in cases.items():
        doc = {"billing": {"storage": {"_included_amounts_status": status}}}
        monkeypatch.setattr(pricing, "_load", lambda doc=doc: doc)
        got, evidence = _storage_included_amounts_confirmed()
        assert got is expected, f"{status!r} -> {got} (expected {expected}): {evidence}"

    # …and a MOVED/absent path is no evidence, never a pass.
    monkeypatch.setattr(pricing, "_load", lambda: {"billing": {"storage": {}}})
    got, evidence = _storage_included_amounts_confirmed()
    assert got is False, evidence


@pytest.mark.parametrize("dim", [d for d in DIMENSIONS if d.blocked],
                         ids=lambda d: d.dimension)
def test_a_satisfied_condition_is_not_quoted_as_the_obstacle(dim):
    """A block may RECORD a satisfied condition; it may not LEAD with it.

    ``postgres_storage`` keeps ``per_org_postgres_attribution`` in its ``release``
    tuple on purpose — it is the finding ("this one is done", #12762), and the next
    lane should not re-derive it. What it may not do is repeat the phrase as the
    reason the dimension is still blocked.
    """
    reason = str(dim.blocked_reason)
    for token in dim.release:
        satisfied, evidence = _PREDICATES[token]()
        if not satisfied:
            continue
        for phrase in _RELEASED_PHRASES[token]:
            assert phrase not in reason, (
                f"{dim.dimension!r}: release condition {token!r} is SATISFIED "
                f"({evidence}) but its phrase {phrase!r} is still quoted as the "
                f"current obstacle in blocked_reason:\n  {reason}")


def test_the_staleness_guard_has_at_least_one_live_token():
    """★ The guard must be able to FIRE. Per-dimension arms skip on unsatisfied.

    Every assertion above sits behind ``if not satisfied: continue``, so the day
    every predicate reads False the whole file stays green with the staleness
    guard covering nothing — the "guard that stopped guarding" shape in its
    purest form. Aggregated across the registry, not per dimension: a dimension
    that legitimately waits on everything it names is fine as long as SOME
    condition somewhere is live.
    """
    checked = [t for d in DIMENSIONS if d.blocked for t in d.release
               if _PREDICATES[t]()[0]]
    assert checked, (
        "NO declared release condition is satisfied anywhere, so the phrase "
        "guard above checks nothing: either the predicates became false (re-derive "
        "them) or every block legitimately waits on all it names — and then this "
        "file's staleness coverage must be re-homed deliberately, not assumed")


def test_exactly_the_attribution_condition_is_satisfied_today():
    """The one SATISFIED condition is asserted POSITIVELY, so its proof cannot lapse.

    `per_org_postgres_attribution` is the finding of this issue (#12762 landed the
    attribution). Three other tests consume it through a `if not satisfied:
    continue`, so if the predicate quietly started returning False they would all
    stay green and the staleness guard would cover nothing. This pins it.

    When the remaining conditions fire — or if one of them is satisfied by
    accident — this test's job is to make the next lane look, not to fix it.
    """
    satisfied, evidence = _per_org_postgres_attribution()
    assert satisfied is True, (
        "the per-org Postgres byte attribution no longer reads as MEASURED — "
        "if #12762's artifact moved, re-point this predicate and re-derive "
        f"postgres_storage's release conditions:\n  {evidence}")
    assert _PREDICATES["per_org_postgres_attribution"] is _per_org_postgres_attribution


def test_the_falsified_premise_is_actually_recorded_as_released():
    """The correction itself, pinned — and the registry side of it.

    A reader who lands on ``postgres_storage``'s block must not be told the
    attribution is missing, and must not have to re-derive that it exists. This
    asserts BOTH halves: the registry still carries the ``OVERRIDES:`` that
    supersedes the #4492 sentence, and the gate's reason points AT the
    attribution rather than denying it.

    Mutation this REDs: reverting the reason to "no per-org byte meter … BLOCKED
    until a per-org attribution exists" (the pre-#12762 wording), or dropping the
    registry's OVERRIDES marker so the two modules silently disagree again.
    """
    attribution = str(_registry("postgres_storage").attribution)
    assert "OVERRIDES" in attribution, (
        "cost_dimensions' postgres_storage attribution no longer records the "
        "OVERRIDES of the #4492 'no per-org figure exists' position — without "
        "it the gate's correction cites nothing")
    assert "ALLOCATION, not a measurement" in attribution, (
        "the overridden sentence is no longer quoted by the registry, so this "
        "test's premise (a sentence was overridden) is stale — re-derive it")

    reason = str(_dim("postgres_storage").blocked_reason)
    assert "for_org" in reason or "per_org_bytes" in reason, (
        "postgres_storage's blocked_reason no longer names the per-org "
        "attribution that now exists — a reader would conclude it is missing:\n"
        f"  {reason}")
    assert "NOT OWNER-CONFIRMED" in reason, (
        "blocked_reason no longer names the included-amount decision that is "
        f"genuinely outstanding, in the artifact's own words:\n  {reason}")
    # …and the artifact must still say it, so the sentence cannot become a lie
    # about a decision the owner has since made.
    satisfied, evidence = _storage_included_amounts_confirmed()
    assert not satisfied, (
        "blocked_reason cites the included amounts as outstanding but "
        f"product/pricing.json reports them confirmed — re-ground the block:\n  {evidence}")


def test_no_other_surface_still_asserts_a_released_ground_as_current():
    """The scan surface for the falsified premise, beyond ``blocked_reason``.

    Reviewer round 1 measured the hole: this file only checked ``blocked_reason``,
    while the change also had to correct the same premise in
    ``postgres_storage.ATTRIBUTION_NOTE`` — and restoring the old sentence there
    kept every test green. These are the surfaces an arming lane actually reads.
    """
    from tortoise import cost_dimensions, metering
    from tortoise.postgres_storage import ATTRIBUTION_NOTE

    surfaces = {
        "postgres_storage.ATTRIBUTION_NOTE": ATTRIBUTION_NOTE,
        "metering.storage_overage_cost_usd.__doc__":
            metering.storage_overage_cost_usd.__doc__ or "",
        "pricing.storage_limits.__doc__": pricing.storage_limits.__doc__ or "",
        # ⛔ The ONE registry's own header. Round 3 found it asserting the
        # falsified premise while the same registry's `postgres_storage` row
        # carried the OVERRIDES of it — the header disagreeing with its own row
        # is the drift this issue is about.
        "cost_dimensions module docstring": cost_dimensions.__doc__ or "",
    }
    # Phrasings that assert the attribution does NOT exist.
    released_assertions = (
        "no per-org figure at all",
        "HAS NO PER-ORG BYTE METER",
        "records no per-org byte ownership",
        "no per-org byte meter",
        "only once a per-org attribution exists",
        "no per-org split",
    )
    offenders = []
    scanned = 0
    for name, text in surfaces.items():
        # ⛔ NORMALISE FIRST. These are wrapped docstrings, so on the pre-fix tree
        # the phrase "no per-org figure at all" appeared with a line break inside
        # it and could never match the very text it was written to catch.
        # Whitespace is collapsed before matching.
        #
        # ⛔ THE EXEMPTION IS PER-PARAGRAPH, NOT PER-SURFACE — and that is load
        # bearing, not tidiness. A whole-surface skip was measured INERT on
        # ``metering.storage_overage_cost_usd.__doc__``, because this very change
        # added the word OVERRIDES to it: the surface the commit message names as
        # a restatement site became permanently unscannable, and appending the
        # pre-fix sentence to it stayed GREEN. A paragraph that quotes the
        # superseded position beside the marker is an argument; a paragraph that
        # asserts it without one is the defect.
        for para in text.split("\n\n"):
            flat = " ".join(para.split())
            scanned += 1
            if "OVERRIDES" in flat:
                continue
            for phrase in released_assertions:
                if " ".join(phrase.split()) in flat:
                    offenders.append(f"{name}: {phrase!r} in: {flat[:90]}")
    assert scanned >= len(surfaces), (
        f"only {scanned} paragraph(s) scanned across {len(surfaces)} surface(s) "
        f"— the scan has become vacuous")
    assert not offenders, (
        "a surface an arming lane reads still asserts the FALSIFIED premise "
        "(the per-org byte attribution exists — #12762, "
        "postgres_bytes.PerOrgPostgresBytes.for_org):\n  "
        + "\n  ".join(offenders))
    assert not offenders, (
        "a surface an arming lane reads still asserts the FALSIFIED premise "
        "(the per-org byte attribution exists — #12762, "
        "postgres_bytes.PerOrgPostgresBytes.for_org):\n  "
        + "\n  ".join(offenders))


def test_the_gate_import_check_rejects_all_three_shapes():
    """The import-time half, tested BEHAVIOURALLY — a block with no release, an
    unknown token, or a release on an unblocked dimension is loud.

    ⛔ This used to grep the module source for the three message strings, and
    reviewer round 1 measured the hole: replacing each `if` with `if False:`
    leaves the strings present, the test GREEN, and the check dead — an inert
    enforcer guarding an inert enforcer. It calls the function now.

    Mutation this REDs: delete any arm of ``_check_release_conditions``.
    """
    base = dict(dimension="capture_extraction", limit_key="max_extraction_tokens",
                unit="tokens")

    # 1. BLOCKED with no declared release.
    blocked_no_release = allowance_gate.AllowanceDimension(
        **base, blocked_reason="blocked for the test")
    with pytest.raises(RuntimeError, match="BLOCKED with no declared release"):
        allowance_gate._check_release_conditions((blocked_no_release,))

    # 2. NOT blocked, but still carrying release conditions.
    enforced_with_release = allowance_gate.AllowanceDimension(
        **base, release=("vector_index_measurable",))
    with pytest.raises(RuntimeError, match="is not BLOCKED but declares"):
        allowance_gate._check_release_conditions((enforced_with_release,))

    # 3. An UNKNOWN token — a typo must not become a condition that never fires.
    unknown_token = allowance_gate.AllowanceDimension(
        **base, blocked_reason="blocked for the test",
        release=("vector_index_measurabl",))
    with pytest.raises(RuntimeError, match="does not define"):
        allowance_gate._check_release_conditions((unknown_token,))

    # …and the real registry passes, so the check is not just always-raising.
    assert allowance_gate._check_release_conditions(DIMENSIONS) == len(DIMENSIONS)


def test_the_release_check_actually_RAN_at_import():
    """Existence is not wiring: the check must have RUN, and that is observable.

    ⛔ The arms above prove the function raises; they cannot tell whether the
    module ever calls it. Deleting the call site would leave every other test
    green — the same "inert enforcer guarding an inert enforcer" shape this file
    exists to catch. ``RELEASE_CHECKED`` is the import-time call's RETURN VALUE, so
    deleting the call makes the name absent (AttributeError here) and deleting the
    assignment makes the module fail to import.
    """
    assert len(DIMENSIONS) == allowance_gate.RELEASE_CHECKED, (
        "the import-time release check validated "
        f"{allowance_gate.RELEASE_CHECKED} dimension(s), not {len(DIMENSIONS)} — "
        "a dimension can now carry a block nothing revalidates")


def test_the_database_grain_reader_no_longer_denies_the_per_org_byte_figure():
    """The THIRD copy of the falsified premise, pinned (#9559 R11).

    ``postgres_storage.ATTRIBUTION_NOTE`` travels on every database-grain reading
    and used to say only *"Postgres records no per-org byte ownership … per-org
    attribution exists for ROW GROWTH"* — which read, to anyone consulting the
    reader that ``allowance_gate``'s block cited, as "there is no per-org byte
    figure". #12762 created one. A reader must now be pointed at it instead of
    being left to conclude it cannot exist.

    The module's OWN scope is unchanged and asserted too: this reading is still
    DATABASE/TABLE-grain, and a split of the database total is still refused.
    """
    from tortoise.postgres_storage import ATTRIBUTION_NOTE

    assert "postgres_bytes" in ATTRIBUTION_NOTE, (
        "ATTRIBUTION_NOTE no longer names the per-org byte module that exists "
        f"(#12762) — a reader concludes it cannot exist:\n  {ATTRIBUTION_NOTE}")
    assert "ALLOCATION" in ATTRIBUTION_NOTE, (
        "the scope caveat is gone: a per-org split of the database TOTAL is "
        f"still an allocation, not a measurement:\n  {ATTRIBUTION_NOTE}")


# ── #9556 G5: the restore/rebuild door is NOT a billable write ────────────────

def test_the_record_restore_door_never_consults_an_allowance():
    """G5, pinned AT THE DOOR — not merely in the package it calls.

    ``rebuild_from_record`` WRITES the graph, so a byte gate inherited on that
    path would let the **billing** path defeat the **durability** path mid-rebuild
    — the failure #9556 declared the exemption for and #9559 was told to honour
    ("and say so in the wiring"). The exemption holds today *by construction*, and
    that is exactly the property an unrelated refactor removes silently.

    Mutation this REDs: call ``enforce_org_limit``/``enforce_allowances`` (or alias
    and call them) from the projection package, from ``record_restore`` — the
    operator door whose own docstring owns this constraint — or from the CLI verb.

    Scope, stated: these SOURCES. A behavioural test would need a live armed record
    and would prove one call path, while the hazard is any call path on the lane.
    """
    targets = [
        _REPO / "tortoise" / "record_restore.py",          # the G5 door
        _REPO / "tortoise" / "__main__.py",                # the `record-restore` verb
    ]
    targets.extend(sorted((_REPO / "tortoise" / "projection").rglob("*.py")))
    for path in targets:
        assert path.is_file(), f"G5 pin target is missing: {path}"

    offenders = []
    for path in targets:
        # ⛔ CODE ONLY: comments and string literals are stripped with `tokenize`
        # before matching. A substring match over raw lines reds on a COMMENT that
        # names the forbidden call — and #9559 explicitly asks this lane to name
        # it ("say so in the wiring"), so the guard would punish the documentation
        # it requires and the natural response would be to weaken the guard.
        # `record_restore.py` already carries a `QUOTA (G5 on #9556)` prose block;
        # that block must stay writable. An aliased import still offends, because
        # the NAME survives in code even when the call does not follow it.
        code = _code_only(path)
        for lineno, line in enumerate(code.splitlines(), 1):
            if "enforce_allowances" in line or "enforce_org_limit" in line:
                offenders.append(f"{path.relative_to(_REPO)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "the record restore/rebuild lane now consults an allowance — G5 (#9556) "
        "exempts it because it is a restore, not a billable write, and a "
        "fail-closed cap here refuses a tenant's own rebuild mid-wipe:\n  "
        + "\n  ".join(offenders))


def _code_only(path: Path) -> str:
    """The file's source with comments AND string literals blanked out.

    Line numbers are preserved (blanked tokens become empty, comments become
    empty) so a reported line number still points at the offending code.
    """
    import tokenize

    keep: dict[int, list[str]] = {}
    with path.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING,
                            tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                            tokenize.DEDENT, tokenize.ENCODING,
                            tokenize.ENDMARKER):
                continue
            keep.setdefault(tok.start[0], []).append(tok.string)
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(" ".join(keep.get(i, [])) for i in range(1, len(lines) + 1))


def test_the_restore_verb_still_exists():
    """The G5 pin above is scoped by names; if one moves, the pin must move too.

    ⛔ BOTH ends are pinned by CONTENT, round 3's finding: the scan set includes
    ``__main__.py`` (a 20k-line file), so if the ``record-restore`` verb moved out
    of it the pin would keep scanning a file that no longer holds the door —
    green and uncovered. The verb's name is asserted here for the same reason
    ``rebuild_from_record`` is.
    """
    from tortoise.projection import FalkorProjection

    assert callable(getattr(FalkorProjection, "rebuild_from_record", None)), (
        "rebuild_from_record is gone or renamed — the G5 exemption pin above "
        "scans a package whose restore lane no longer lives where it names")

    main_src = (_REPO / "tortoise" / "__main__.py").read_text(encoding="utf-8")
    assert "_cmd_record_restore" in main_src, (
        "the `record-restore` CLI verb is no longer in tortoise/__main__.py — the "
        "G5 pin keeps scanning that file, so the door would be uncovered. Add the "
        "verb's new home to the scan set above.")
    assert "record-restore" in main_src, (
        "the `record-restore` CLI subcommand name is gone from tortoise/__main__.py "
        "— re-derive which file now holds the restore door and add it to the G5 "
        "scan set")

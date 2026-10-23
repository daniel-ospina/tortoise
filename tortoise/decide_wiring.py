"""The ONE writer for decision edge wiring — shared by `graph-scripts/decide.py`
and `tortoise decide` (`tortoise/__main__.py::_cmd_decide`).

#6671 — IDEMPOTENT OPERATOR WIRING

`create_point` is written with `dedup=True` (content hash), so POINTS converge
across runs. `create_operator` is NOT idempotent — it mints a fresh operator
Point on every call — and both decide entry points used to key a `created_ops`
cache per run only, so a second run re-created the whole edge set. Because
`mitigate_operator` is idempotent PER OPERATOR, the two runs' mitigations landed
on different copies: run-1 edges damped, run-2 edges at full weight, both read
by EP. Measured on #6671 (script) with a 2-edge decision: 4 operators after two
runs, option confidence 0.5736 → 0.5184 (14 → 19 EP iterations). Measured on
#9631 (CLI) with the same decision: 3 → 6 operators, 1 → 2 mitigations, opt:a
0.6592 → 0.6756. There is NO edge-delete API, so that pollution is irreversible.

#9631 — ONE MODULE, NO DRIFT

The script's fix was hoisted HERE and both entry points call it. The alternative
— a third hand-maintained copy of the lookup — is how the CLI kept the defect
after the script was fixed: a reviewer found the untouched implementation, and a
second copy would drift the same way again.

The identity used here is the graph's own operator shape, not a second model:
`create_operator` wires `(source)-[:INPUT {idx:0}]->(op)` and
`(op)-[op_type|hasPart {idx:n>0}]->(target)`, for ANY accepted op_type
(IMPL/NAND and the part/whole family, whose bridge is mirrored as `hasPart`).
`label` is part of the identity — two edges with different Layer-3 predicates
over the same triple are two claims. The endpoint labels are deliberately NOT
in the identity: the write door admits :Point/:Subject/:Object/:Event endpoints
(#7902), so anchoring on `:Point` would miss a Subject/Object endpoint and
duplicate the very edge this guards.
"""
from __future__ import annotations


def find_operators(proj, op_type: str, source_id: str, target_id: str,
                   label: str | None, *,
                   any_label: bool = False) -> list[dict]:
    """Existing operators on this bridge, as dicts with the facts EP needs.

    Each row is ``{"id", "status", "outdated", "label", "direction",
    "targets"}``.

    Returns ALL matches, not just the first: a graph already polluted by an
    earlier run has duplicate copies of every edge, and a relevance edge must
    damp every copy it finds — damping one leaves the other at full weight,
    which is the asymmetry #6671 measured.

    ``any_label=True`` drops the label arm of the identity (used to ask "what
    sits on this triple at all?", which the relevance arm needs before deciding
    what it may damp).

    ``status``, the legacy ``outdated`` flag and the Layer-3 ``label`` come back
    because the caller must know three things it cannot assume: whether EP reads
    the bridge (`tortoise.live._live_only` = not draft AND not
    terminal(status, outdated)), which claim it is (two labels over one triple
    are two claims), and whether the operator IS this bridge (``targets`` > 1
    means it also claims other pairs, so reusing it would widen this decision).
    A status-less operator reads as `live`, the graph's own default.
    """
    label_arm = "" if any_label else \
        "AND coalesce(op.label, '') = coalesce($label, '') "
    rows = proj.g.query(
        # Label-free endpoints: the write door admits :Point/:Subject/:Object/
        # :Event endpoints (#7902), so a `:Point` anchor would miss a
        # Subject/Object endpoint and duplicate the edge. The op node keeps
        # `:Point` (an operator IS a Point, is_operator:true).
        "MATCH (s {id:$sid})-[ri:INPUT]->(op:Point {is_operator:true})"
        "-[ro]->(t {id:$tid}) "
        "WHERE op.op_type = $op AND ri.idx = 0 AND ro.idx > 0 "
        + label_arm +
        "RETURN op.id, op.status, coalesce(op.outdated, false), "
        "coalesce(op.label, ''), coalesce(op.direction, '')",
        params={"sid": source_id, "tid": target_id, "op": op_type,
                "label": label},
    ).result_set
    if not rows:
        return []
    # ONE pattern, no re-bind: filtering `op` and then matching `op` again in a
    # following clause is the #6976 shape — on the canonical 6.0.1 instance the
    # predicate is dropped and the count reads OTHER points' edges
    # (`tests/test_relationships_bounded.py`). `r.idx > 0` is null for the
    # mitigation's `mitigated_by` edges, so those cannot inflate the count.
    counts = dict(proj.g.query(
        "MATCH (op:Point)-[r]->() "
        "WHERE op.id IN $ids AND r.idx > 0 RETURN op.id, count(r)",
        params={"ids": [row[0] for row in rows]},
    ).result_set)
    return [{"id": row[0], "status": row[1] or "live",
             "outdated": bool(row[2]), "label": row[3] or "",
             "direction": row[4] or "",
             "targets": int(counts.get(row[0], 0))} for row in rows]


def single_pair(rows: list[dict]) -> list[dict]:
    """The rows that ARE a single-pair bridge (``targets == 1``).

    The predicate that decides reuse, dampability and mint licensing, stated
    once: `targets > 1` means the operator also claims other pairs, so it is not
    "this" bridge. Four hand-written ``row["targets"] == 1`` filters is how the
    two arms drifted apart before #9631.
    """
    return [row for row in rows if row["targets"] == 1]


def shared_bridges(rows: list[dict]) -> list[dict]:
    """The rows that are NOT a single-pair bridge (they claim other pairs)."""
    return [row for row in rows if row["targets"] != 1]


def is_ranked(row: dict) -> bool:
    """EP's own rule (`tortoise.live._live_only`): not draft, not terminal."""
    from tortoise.live import is_draft_status, is_terminal_status

    return (not is_draft_status(row["status"])
            and not is_terminal_status(row["status"], row["outdated"]))


def status_note(row: dict) -> str | None:
    """A TRUE one-line note about why this bridge is (or is not) ranked."""
    from tortoise.live import is_draft_status, is_terminal_status

    if row["outdated"]:
        # `outdated=true` is server-managed (`invalidate_point`), and
        # `update_point` refuses to clear it — so no promotion remedy is
        # offered here, whatever the status says (#6699/#2422).
        return (f"elected operator {row['id']}, outdated=true "
                f"(status={row['status']}) — the legacy dead marker; EP "
                "excludes it and update_point cannot clear it, so this bridge "
                "stays out of the ranking; it is NOT duplicated (#6671)")
    if is_draft_status(row["status"]):
        return (f"elected operator {row['id']}, status=draft — EP excludes "
                "draft operators (#780), so this bridge does not participate in "
                "the ranking until it is promoted "
                "(update_point(id, status='live')); it is NOT duplicated "
                "(#6671)")
    if is_terminal_status(row["status"], row["outdated"]):
        return (f"elected operator {row['id']}, status={row['status']} — a "
                "TERMINAL status; EP excludes it (#2422) and update_point "
                "cannot resurrect it, so this bridge stays out of the ranking; "
                "it is NOT duplicated (#6671)")
    return None


def direction_note(row: dict, requested: str | None) -> str | None:
    """Say it out loud when a declared direction is not the stored one.

    Storing is VERBATIM and an existing bridge keeps its direction, so
    re-running a decision with a different ``--direction`` cannot change it —
    the alternative (filing a second operator) is the irreversible duplication
    this module exists to prevent. A silent mismatch would be worse than either.

    An EMPTY stored direction is its own case, not a non-case: operators written
    before #7852 carry no ``direction`` property at all, so on any older graph
    the requested direction is dropped for the most common shape there is.
    """
    if requested is None:
        return None
    from tortoise.live import direction_spellings

    if not row.get("direction"):
        return (f"elected operator {row['id']} records NO direction — the "
                f"requested {requested!r} is NOT applied in place (a second "
                "operator would duplicate the bridge, #6671)")
    if row["direction"] in direction_spellings(requested):
        return None
    return (f"elected operator {row['id']} keeps its stored direction "
            f"{row['direction']!r} — the requested {requested!r} is NOT applied "
            "in place (a second operator would duplicate the bridge, #6671)")


def resolve_or_create_operator(sdk, proj, op_type: str, source_id: str,
                               target_id: str, label: str | None,
                               cache: dict[tuple[str, str, str, str], str],
                               *, any_label: bool = False,
                               create_kwargs: dict | None = None
                               ) -> tuple[str, bool, str | None]:
    """Write the edge idempotently — ONE operator per single-pair bridge.

    Returns ``(operator_id, reused, note)``; ``note`` is a warning to print.

    Idempotence is by the graph's own operator identity, and it is bounded by
    TWO facts read from the graph, not assumed:

    * a copy EP does not read (draft / terminal status / legacy
      ``outdated=true``) is never preferred over one it does — and it is never
      "fixed" by filing a live twin, which is the decision recorded on #6671's
      scoping ("Creating a second operator instead would reintroduce the exact
      duplication this issue is about"). A twin is not cosmetic:
      ``update_point(draft_id, status="live")`` is a legitimate call, and after
      a twin exists it leaves TWO live operators on one bridge (measured), which
      EP reads twice — with no edge-delete API.
    * an operator with ``targets > 1`` is NOT this bridge: it also claims other
      pairs, so reusing it would silently widen the decision's claim (and a
      relevance edge on the pair would damp the other pairs through it). A
      single-pair bridge is written instead, which the next run then reuses.

    ``create_kwargs`` are forwarded to ``create_operator`` on the CREATE branch
    only (the CLI's ``--direction``, #7813) — they take no part in the identity,
    so a re-run reuses the bridge it filed and ``direction_note`` says so.
    """
    key = (source_id, op_type, target_id, "" if any_label else (label or ""))
    if key in cache:
        return cache[key], True, None
    matches = find_operators(proj, op_type, source_id, target_id, label,
                             any_label=any_label)
    single = single_pair(matches)
    if single:
        # Prefer the copy EP reads; on a polluted bridge the copies are
        # identical, so storage order is arbitrary.
        elected = ([row for row in single if is_ranked(row)] or single)[0]
        cache[key] = elected["id"]
        # BOTH facts, not whichever comes first: a draft bridge whose declared
        # direction was not applied is exactly the one a reader will promote
        # later, so it is the last place to drop half the warning.
        notes = [n for n in (
            status_note(elected),
            direction_note(elected, (create_kwargs or {}).get("direction")),
        ) if n]
        return elected["id"], True, (" ".join(notes) or None)
    note = None
    if matches:
        multi = ", ".join(f"{row['id']} ({row['targets']} targets)"
                          for row in matches)
        note = (f"existing operator(s) {multi} on this triple also claim other "
                "pairs — not reused (it would widen this decision's claim); a "
                "single-pair bridge is created")
    else:
        # The label is part of the identity, so the write does not reuse a
        # bridge filed under a DIFFERENT label — two predicates over one triple
        # are two claims — and the mirror reading is the same asymmetry: an
        # UNLABELLED write does not reuse a labelled bridge. Either way a second
        # claim is created, so say what is already there instead of filing an
        # unreported twin on the first post-upgrade run of an existing decision
        # file.
        others = [row for row in find_operators(
            proj, op_type, source_id, target_id, label, any_label=True)
            if row["label"] != (label or "")]
        if others:
            named = ", ".join(
                f'{row["id"]} ({row["label"] or "unlabelled"})'
                for row in others)
            if label:
                note = (f"existing operator(s) {named} on this triple carry a "
                        "DIFFERENT label — not reused (the label is part of "
                        "the operator identity, so this is a second claim); "
                        "a labelled bridge is created")
            else:
                note = (f"existing operator(s) {named} on this triple carry a "
                        "LABEL — not reused (the label is part of the operator "
                        "identity, so this is a second claim); an UNLABELLED "
                        "bridge is created")
    op = sdk.create_operator(op_type, source_id, [target_id], label=label,
                             **(create_kwargs or {}))
    cache[key] = op["id"]
    return op["id"], False, note


def select_damp_targets(proj, op_type: str, source_id: str, target_id: str,
                        label: str | None) -> dict:
    """Which operators a relevance edge may damp — the ONE relevance-arm rule.

    A relevance edge is a MITIGATION, not a claim of its own, so this function
    never writes anything. It returns the FACTS; which of them licenses a mint is
    stated once, in ``may_mint_damped_bridge``::

        {"targets": [ids...],          # every dampable copy on the triple
         "notes": ["...", ...],        # why it declined / what it scoped
         "singles": [ids...],          # single-pair bridges in the LABEL SCOPE
         "other_labels": [ids...],     # bridges under ANY OTHER label
         "bridges": int}               # how many bridges sit on the triple

    The NOTES describe the bridges that are ALREADY there; they never state what
    the caller will do next, because the two callers differ exactly there (the
    script refuses to mint, the CLI wires a bridge to damp). A shared note that
    said "nothing damped" would be printed by a caller that then damped a fresh
    bridge — the drift this module exists to end, in user-facing prose.

    ``label`` scopes the dampening when the caller names one (two labels over
    one triple are two claims — damping a `refutes` bridge on behalf of a
    `supports` mitigation would silently weaken a claim the caller did not
    name). Without a label the edge damps the unlabelled bridge when there is
    one, and refuses to guess when the triple carries several distinct labels.
    """
    # ONE query, then scope it in Python: `bridges` must mean "how many bridges
    # sit on the triple" (that is what the docstring promises and what the mint
    # gate needs), and a label-scoped lookup alone cannot answer it. A labelled
    # edge on a triple that carries other labels is NOT an empty triple.
    on_triple = find_operators(proj, op_type, source_id, target_id, None,
                               any_label=True)
    if label is None:
        found, other_labels, other_rows = on_triple, [], []
    else:
        found = [row for row in on_triple if row["label"] == label]
        other_rows = [row for row in on_triple if row["label"] != label]
        # EVERY other-label bridge, shared or not: the gate refuses to mint next
        # to a claim under another label whether or not that claim is
        # single-pair, so counting only single-pair ones made two states that
        # differ solely in the other bridge's target count behave oppositely
        # (#9631 review, round 5).
        other_labels = [row["id"] for row in other_rows]
    bridges = len(on_triple)

    if not found:
        named = f"{label} " if label else ""
        notes = [f"no {named}bridge on this triple"]
        if other_rows:
            present = ", ".join(
                f'{row["id"]} ({row["label"] or "unlabelled"})'
                for row in other_rows)
            notes.append(
                f"the triple carries {present} under a DIFFERENT label — not "
                "damped (a relevance edge damps only the claim it names)")
        return {"targets": [], "bridges": bridges, "singles": [],
                "other_labels": other_labels, "notes": notes}
    notes: list[str] = []
    # #6671 review: a bridge with ``targets > 1`` also claims OTHER pairs, so
    # damping it here would weaken claims this edge never named — the exact
    # hazard ``resolve_or_create_operator`` refuses to create. The two arms
    # must state ONE rule, so the exclusion is applied here too instead of
    # being asserted only in the ``edges`` arm's prose.
    shared = shared_bridges(found)
    if shared:
        named = ", ".join(f'{row["id"]} ({row["targets"]} targets)'
                          for row in shared)
        notes.append(f"{named} also claim other pairs — not damped (it would "
                     "weaken claims this edge did not name)")
    single = single_pair(found)
    if not single:
        scope = f"under {label!r} " if label else ""
        notes.append(f"no single-pair bridge {scope}on this triple — every "
                     "bridge in scope is shared with other pairs")
        # `other_labels` still matters HERE: a bridge under a different label is
        # a claim this edge did not name, so the mint gate must refuse even
        # though the named scope has nothing to damp.
        return {"targets": [], "bridges": bridges, "singles": [],
                "other_labels": other_labels, "notes": notes}
    found = single
    if label is not None:
        targets = [row["id"] for row in found]
    elif any(row["label"] == "" for row in found):
        targets = [row["id"] for row in found if row["label"] == ""]
    elif len({row["label"] for row in found}) == 1:
        targets = [row["id"] for row in found]
    else:
        labels = sorted({row["label"] for row in found})
        notes.append(f"this triple carries {len(labels)} distinct labels "
                     f"{labels} and the edge names none — nothing damped")
        targets = []
    return {"targets": targets, "bridges": bridges,
            "singles": [row["id"] for row in found],
            "other_labels": other_labels, "notes": notes}


def may_mint_damped_bridge(pick: dict) -> bool:
    """May the caller WRITE an operator for a relevance edge it cannot damp?

    The `tortoise decide` CLI documents minting the operator a relevance edge
    damps; `graph-scripts/decide.py` refuses ("a relevance edge never mints a
    claim"). That is the only difference between them, and it is stated HERE so
    the two entry points cannot drift into different answers:

    * no single-pair bridge in the label scope, and NOTHING on the triple under
      another label → mint. Either the triple is empty, or every bridge on it is
      shared with other pairs — the same state `resolve_or_create_operator`
      writes a single-pair bridge out of for the `edges` arm. Minting here is
      that rule, not a twin.
    * a single-pair bridge exists in scope → NEVER mint: that is the twin, and
      the relevance edge had something to damp (including the ambiguous
      several-labels case, which refuses rather than guesses).
    * the triple carries a bridge under a DIFFERENT label (`other_labels`, shared
      or single-pair) → NEVER mint: the caller named a claim that does not
      exist as such, and filing a parallel one would add a claim the input never
      declared. The bridge's target count must not change the answer, which is
      why this reads every other-label bridge and not just the single-pair ones.
    """
    return not pick["singles"] and not pick["other_labels"]


def damped_factor_ids(targets: list[str]) -> list[str]:
    """The operators a relevance edge damped, for an explicit factor list.

    EVERY damped copy belongs in the list, not just the first: a polluted triple
    (duplicates of one bridge) has all its copies damped, and leaving one out
    makes the factor list disagree with the graph — and with the OTHER entry
    point, which is the drift this module exists to prevent. Deduped, because a
    factor list must not carry the same operator twice: EP would count one
    bridge as two.

    Takes the ids the caller actually damped (``pick["targets"]``, or the freshly
    minted bridge), never a dict: an earlier version spread the dict beside an
    explicit ``targets`` key, and the spread silently won.
    """
    seen: list[str] = []
    for op_id in targets:
        if op_id not in seen:
            seen.append(op_id)
    return seen

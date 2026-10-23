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
    counts = dict(proj.g.query(
        "MATCH (op:Point) WHERE op.id IN $ids MATCH (op)-[r]->() "
        "WHERE r.idx > 0 RETURN op.id, count(r)",
        params={"ids": [row[0] for row in rows]},
    ).result_set)
    return [{"id": row[0], "status": row[1] or "live",
             "outdated": bool(row[2]), "label": row[3] or "",
             "direction": row[4] or "",
             "targets": int(counts.get(row[0], 0))} for row in rows]


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
    """
    if requested is None or not row.get("direction"):
        return None
    from tortoise.live import direction_spellings

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
    single = [row for row in matches if row["targets"] == 1]
    if single:
        # Prefer the copy EP reads; on a polluted bridge the copies are
        # identical, so storage order is arbitrary.
        elected = ([row for row in single if is_ranked(row)] or single)[0]
        cache[key] = elected["id"]
        return (elected["id"], True,
                status_note(elected)
                or direction_note(elected, (create_kwargs or {}).get("direction")))
    note = None
    if matches:
        multi = ", ".join(f"{row['id']} ({row['targets']} targets)"
                          for row in matches)
        note = (f"existing operator(s) {multi} on this triple also claim other "
                "pairs — not reused (it would widen this decision's claim); a "
                "single-pair bridge is created")
    op = sdk.create_operator(op_type, source_id, [target_id], label=label,
                             **(create_kwargs or {}))
    cache[key] = op["id"]
    return op["id"], False, note


def select_damp_targets(proj, op_type: str, source_id: str, target_id: str,
                        label: str | None) -> dict:
    """Which operators a relevance edge may damp — the ONE relevance-arm rule.

    A relevance edge is a MITIGATION, not a claim of its own, so this function
    never writes anything. It returns::

        {"targets": [operator ids...],      # every dampable copy on the triple
         "notes": ["...", ...],             # why it declined / what it scoped
         "bridges": int}                    # how many bridges exist at all

    ``bridges == 0`` is the caller's decision: the script refuses to mint a
    claim ("a relevance edge never mints a claim"), while the `tortoise decide`
    CLI documents minting the damped operator. Anything else — including a
    triple whose every bridge is shared with other pairs — must never mint, or
    the twin duplicated the very claim this module guards.

    ``label`` scopes the dampening when the caller names one (two labels over
    one triple are two claims — damping a `refutes` bridge on behalf of a
    `supports` mitigation would silently weaken a claim the caller did not
    name). Without a label the edge damps the unlabelled bridge when there is
    one, and refuses to guess when the triple carries several distinct labels.
    """
    found = find_operators(proj, op_type, source_id, target_id, label,
                           any_label=label is None)
    if not found:
        named = f"{label} " if label else ""
        return {"targets": [], "bridges": 0,
                "notes": [f"no {named}bridge on this triple — nothing damped "
                          "(a relevance edge never mints a claim)"]}
    notes: list[str] = []
    # #6671 review: a bridge with ``targets > 1`` also claims OTHER pairs, so
    # damping it here would weaken claims this edge never named — the exact
    # hazard ``resolve_or_create_operator`` refuses to create. The two arms
    # must state ONE rule, so the exclusion is applied here too instead of
    # being asserted only in the ``edges`` arm's prose.
    shared = [row for row in found if row["targets"] != 1]
    if shared:
        named = ", ".join(f'{row["id"]} ({row["targets"]} targets)'
                          for row in shared)
        notes.append(f"{named} also claim other pairs — not damped (it would "
                     "weaken claims this edge did not name)")
    found = [row for row in found if row["targets"] == 1]
    if not found:
        notes.append("every bridge on this triple is shared with other pairs — "
                     "nothing damped")
        return {"targets": [], "bridges": len(shared), "notes": notes}
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
    return {"targets": targets, "bridges": len(found) + len(shared),
            "notes": notes}

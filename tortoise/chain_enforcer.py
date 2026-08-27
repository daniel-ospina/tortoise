"""Deterministic pack-chain enforcement (issue #1695, Task 1).

The S2/S4 prompts carry the advisory semantics "CHAINS — WARN, then TRY TO
REPAIR ... re-map toward the nearest valid chain position. NEVER invent
entities to satisfy a chain." — but the repair was never actually applied:
``validate_chains`` (extractor_v2) only EMITS a note. ``validate_and_rewire``
makes the repair deterministic and guaranteed: reverse-chain-order
``about_entities`` co-mention pairs are rewired through the nearest valid
chain intermediate — ONLY when that position is unambiguous — and
warns-and-keeps (never invents, never drops) otherwise.

Ships FIRST as an independent change (Task 1): every A/B arm of the
classify-later experiment holds the enforcer constant, so its effect is
orthogonal to the classification direction.

## Semantics

A chain (e.g. ``productDelivery``: jobToBeDone → useCase → feature →
userJourney → workflow → requirement → architecture) is an ordered kind
path. An item's ``about_entities`` list is its connection path: chain
members must appear in ascending position order (a higher-position kind
connecting directly to a lower-position kind = a direct-edge violation —
e.g. a customer mapped straight to an architecture requirement).

``validate_and_rewire`` scans points/events' ``about_entities`` for
violating pairs and, per item and per chain:

1. **Detect**: chain members present in the item, in list order.
2. **Distance rule**: the repair target is the position IMMEDIATELY above
   the LOWEST-position member — the nearest valid chain position to the
   value source (never skipping to a farther hop, even when a farther
   position has a list entity).
3. **Unambiguous rule**: exactly ONE entity at that nearest position →
   rewire (insert it at its chain position + reorder the members
   ascending); zero entities at the nearest position → the repair is
   distance-unreachable → warn-and-keep; multiple entities at the nearest
   position → ambiguous → warn-and-keep (never guess).
4. **Never-invent / never-drop**: the rewire only wires through EXISTING
   entities; every referenced entry survives (the rewired list is a
   superset of the original refs — non-string entries are carried through
   at their slots).

Fixes COMPOSE across chains in one item: each chain's splice is built from
the accumulated list, so a second violating chain never overwrites the
first chain's repair.

The returned list is a DEEP COPY — the caller's input is never mutated
(byte-identity safety). Pure logic: no embeddings, no LLM, no DB — lane-
agnostic (embedded + docker lanes).

``validate_chains`` (extractor_v2) remains the warn-only residual backstop:
after a successful rewire it reports NO violations for the fixed pair.
"""

from __future__ import annotations


def validate_and_rewire(
    embed_list: dict, master: dict | None = None
) -> tuple[dict, list[dict], dict]:
    """Deterministic chain enforcement over the embed list.

    Returns ``(embed_list, notes, stats)``:

    - ``embed_list`` — a deep copy with the chain-order repairs applied
      (reverse-chain ``about_entities`` pairs rewired through the nearest
      valid intermediate when unambiguous; unchanged otherwise).
    - ``notes`` — ``[{"chain", "finding", "action": "rewired"|"warned",
      "note"}]`` mirroring ``validate_chains``' format (one per violation).
    - ``stats`` — ``{"items_checked", "violations", "rewired", "warned"}``.

    Never raises on junk embed_list input (non-dict top-level, non-dict
    items, missing keys) — a malformed master dict is the caller's
    responsibility (production wraps the call in try/except). Every repair
    decision is deterministic (sorted, never guessed).
    """
    import copy

    from tortoise.extractor_v2 import _chain_positions, _norm, build_master_list

    if not isinstance(embed_list, dict):
        return {}, [], {"items_checked": 0, "violations": 0, "rewired": 0, "warned": 0}
    master = master or build_master_list()
    pos = _chain_positions(master)  # kind (bare-lower) → (chain, position)
    # chain path order (bare forms) for finding nearest positions
    chain_paths: dict[str, list[str]] = {
        cname: [str(k).rsplit(":", 1)[-1] for k in (c.get("path") or [])]
        for cname, c in (master.get("chains") or {}).items()
    }
    # position index: (chain, position) → [entity names at that position]
    position_entities: dict[tuple[str, int], list[str]] = {}

    out = copy.deepcopy(embed_list or {})
    entity_kinds: dict[str, str] = {}
    for e in out.get("entities") or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        kind = str(e.get("kind") or "").strip()
        if not name or not kind:
            continue
        entity_kinds[_norm(name)] = kind
        p = pos.get(kind.rsplit(":", 1)[-1].lower())
        if p is not None:
            position_entities.setdefault((p[0], p[1]), []).append(name)

    notes: list[dict] = []
    stats = {"items_checked": 0, "violations": 0, "rewired": 0, "warned": 0}

    def _kind_pos(name: str) -> tuple[str, int] | None:
        """(chain, position) for an about-ref's entity kind, or None."""
        kind = entity_kinds.get(_norm(name))
        if not kind:
            return None
        return pos.get(kind.rsplit(":", 1)[-1].lower())

    # Truthy non-list sections (e.g. {"points": "junk"}) are skipped — the
    # sections iterable only walks LIST sections (never raises on junk).
    sections = (out.get("points"), out.get("events"))
    for item in [i for s in sections if isinstance(s, list) for i in s]:
        if not isinstance(item, dict):
            continue
        about = item.get("about_entities")
        if not isinstance(about, list) or not about:
            continue
        if not any(isinstance(a, str) for a in about):
            continue
        stats["items_checked"] += 1
        new_about, item_notes = _rewire_item(list(about), _kind_pos, position_entities, chain_paths)
        if item_notes:
            item["about_entities"] = new_about
            for n in item_notes:
                notes.append(n)
                stats["violations"] += 1
                if n["action"] == "rewired":
                    stats["rewired"] += 1
                else:
                    stats["warned"] += 1
    return out, notes, stats


def _rewire_item(
    about: list, kind_pos, position_entities: dict, chain_paths: dict
) -> tuple[list, list[dict]]:
    """Per-item rewire: returns (new_about, notes).

    ``about`` is the RAW about_entities list (non-string entries are carried
    through at their slots — the splice never drops a ref). ``kind_pos``
    maps an entity name → ``(chain, position)`` or None.
    """
    names = [str(a) for a in about if isinstance(a, str)]
    positioned: dict[str, list[tuple[str, tuple[str, int]]]] = {}
    for name in names:
        p = kind_pos(name)
        if p is not None:
            positioned.setdefault(p[0], []).append((name, p))
    notes: list[dict] = []
    new_about = list(about)
    for chain in sorted(positioned):  # deterministic chain iteration
        # members in ORIGINAL list order (the connection path as emitted)
        members = positioned[chain]
        in_order = all(members[i][1][1] <= members[i + 1][1][1] for i in range(len(members) - 1))
        if in_order:
            continue
        ordered = sorted(members, key=lambda m: m[1][1])  # ascending by pos
        path = chain_paths.get(chain) or []
        low_pos, high_pos = ordered[0][1][1], ordered[-1][1][1]
        # Distance rule: the repair target is the position IMMEDIATELY above
        # the LOW member — the nearest valid chain position to the value
        # source ("a customer mapped straight to an architecture requirement
        # re-maps to the nearest chain position"). Empty (distance-unreachable)
        # or multi-occupied (ambiguous) → warn-and-keep: never skip to a
        # farther hop, never guess.
        target_p = low_pos + 1
        if target_p >= high_pos:
            notes.append(
                {
                    "chain": chain,
                    "finding": _finding(names, members, path),
                    "action": "warned",
                    "note": (
                        "no intermediate chain position between the pair "
                        "— do NOT invent entities to satisfy the chain"
                    ),
                }
            )
            continue
        nearest_entities = position_entities.get((chain, target_p), [])
        if not nearest_entities:
            notes.append(
                {
                    "chain": chain,
                    "finding": _finding(names, members, path),
                    "action": "warned",
                    "note": (
                        f"nearest valid chain position ({target_p}) is EMPTY "
                        "— the repair is distance-unreachable; never skip to a "
                        "farther hop, do NOT invent entities to satisfy the chain"
                    ),
                }
            )
            continue
        if len(nearest_entities) > 1:
            notes.append(
                {
                    "chain": chain,
                    "finding": _finding(names, members, path),
                    "action": "warned",
                    "note": (
                        f"nearest valid chain position ({target_p}) is "
                        "AMBIGUOUS across multiple list entities "
                        f"({', '.join(nearest_entities)}) — never guess; "
                        "warn-and-keep"
                    ),
                }
            )
            continue
        # unambiguous → rewire: rebuild the chain subsequence ascending,
        # inserting the nearest-position intermediate at its chain position.
        # Non-member refs keep their EXACT slots (only the member names are
        # re-placed; the intermediate is spliced in right after the LAST
        # member below it — unrelated refs never relocate).
        mid_name = nearest_entities[0]
        member_names = {m[0] for m in members}
        seq_names = [m[0] for m in ordered]
        if mid_name not in member_names:
            seq_names.append(mid_name)
        seq_names.sort(key=lambda n: kind_pos(n)[1])
        seq_members = [n for n in seq_names if n in member_names]
        mid_pos = kind_pos(mid_name)[1]
        si = 0
        mid_pending = mid_name not in member_names
        # The intermediate goes right after the LAST member below mid_pos —
        # when TWO members share the LOWEST position (same chain kind
        # duplicated), inserting after the first below-member would strand
        # the second out of order (P1 splice pin); below_count positions the
        # splice after ALL of them (seq_members is position-ascending).
        below_count = sum(1 for m in seq_members if kind_pos(m)[1] < mid_pos)
        # Splice is built from the ACCUMULATED new_about (previous chains'
        # fixes compose — a second violating chain in the same item must
        # not overwrite the first chain's repair), and non-string refs are
        # carried through at their slots (never dropped).
        spliced: list = []
        for name in new_about:
            if isinstance(name, str) and name in member_names:
                spliced.append(seq_members[si])
                si += 1
                if mid_pending and si == below_count:
                    spliced.append(mid_name)
                    mid_pending = False
            else:
                spliced.append(name)
        if mid_pending:
            spliced.append(mid_name)
        new_about = spliced
        notes.append(
            {
                "chain": chain,
                "finding": _finding(names, members, path),
                "action": "rewired",
                "note": (
                    f"re-mapped the connection via '{mid_name}' (nearest "
                    "valid chain position) — about_entities re-ordered into "
                    "chain order; nothing invented, nothing dropped"
                ),
            }
        )
    return new_about, notes


def _finding(names: list[str], members: list, path: list[str]) -> str:
    """Human-readable violation description (mirrors validate_chains)."""
    return (
        f"{' → '.join(str(m[0]) for m in members)} out of chain order (expected {' → '.join(path)})"
    )

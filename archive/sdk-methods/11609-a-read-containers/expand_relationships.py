# Archived sdk-method `expand_relationships` — REMOVED from the product surface (#11609).
#
# batch:   11609-a-read-containers
# reason:  retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container
# source:  tortoise/sdk.py @ 32011c4dad4b42364868cb0810ad8e7317a7dfb6
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'expand_relationships'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def expand_relationships(self, point_id: str) -> list[dict]:
        """Full relationship payload for a single Point, incl. related_content (#1353 D14).

        The expand side of the list/expand split: search list view carries bounded
        state entries (IDs + labels + direction + peer state, no content); this
        returns the complete unbounded payload for one point (single-point
        fan-out is trivially cheap) so an agent can read a neighbor's full text
        on demand. Each entry gains `related_status` (the neighbor's point
        status — live/superseded/retracted/draft) so the full-fidelity view is
        not blind to terminal state (#689/#898 posture).

        A neighbour is an epistemic node, so the status lookup sweeps all four
        labels (#6976): with `MATCH (n:Point)` a peer that the #8882 re-typing
        migration moved to `:Object`/`:Subject`/`:Event` came back with
        `related_status: ""` — an advisory field that cannot tell "this peer has
        no lifecycle state" from "this read never looked at its label".

        The value is the PEER LAYER's own lifecycle status, so what it can carry
        is layer-bound: `:Point` carries `status` (live / superseded /
        retracted / draft / outdated / archived), `:Object` and `:Subject`
        carry their own `status` families (which include `deprecated`), and an
        `:Event` carries NO `status` at all — its lifecycle is `eventStatus`,
        a different vocabulary this field deliberately does not mix in, so an
        `:Event` peer reports `""`. `""` therefore means "this peer's layer
        exposes no `status`", and it is now reached by LOOKING at the peer's
        label rather than by never asking.

        A THIRD meaning is `""`: the sweep leg for that peer's label FAILED
        (a raise is logged and skipped, so the other labels' statuses survive —
        the per-label isolation below), or that peer's own status row was
        malformed (skipped per row, so the other rows survive). Neither is
        distinguishable from "no lifecycle state" in this value; labelling them
        is R1 on #8996. A peer can therefore be PRESENT in the payload with `""`
        because its label's status leg died, which is exactly the conflation the
        widening above was added to end — stated rather than left to be
        rediscovered.

        An ABSENT `status` on a status-carrying label (`:Point`, `:Subject`,
        `:Object`) reports `"live"`, not `""` (#9974 R1,
        `live.status_as_reported`): absent has always READ live
        (`live._terminal_excluded`'s NULL arm), and since R1 that is also the
        ordinary birth state of a status-less Object/Subject — the entity fold
        seeds `status=$st`, derived-or-absent, never a fabricated `'live'`.
        `""` therefore stays reserved for the two meanings above, which is the
        distinction this field exists to carry.
        """
        from .search_engine import get_relationships
        proj = self._get_proj()
        entries = get_relationships(proj.g, [point_id]).get(point_id, [])
        if not entries:
            return entries
        related_ids = [e.get("related_id") for e in entries if e.get("related_id")]
        statuses: dict[str, str] = {}
        if related_ids:
            rows: list = []
            for _label, _q in zip(
                    EPISTEMIC_LABELS,
                    epistemic_label_queries(
                        "MATCH (n:{label}) WHERE n.id IN $ids "
                        "RETURN n.id, n.status"),
                    strict=True):
                try:
                    rows.extend(
                        (_label, _row)
                        for _row in proj.g.query(
                            _q,
                            params={"ids": related_ids},
                            timeout=200,
                        ).result_set)
                except Exception:
                    # ONE label failing must not blank every peer's status: the
                    # sweep widens, so a leg that fails can only fail to add.
                    _logger.warning(
                        "related_status lookup failed for one epistemic "
                        "label — the other labels' statuses are kept",
                        exc_info=True)
                    continue
            for _label, row in rows:
                try:
                    # `or "live"` would be wrong HERE: it would report `live`
                    # for an `:Event` peer, the one label with no `status`
                    # family. `status_as_reported` carries that carve-out (#9974
                    # R1 review P2) — an ABSENT status on the other three labels
                    # reads live, exactly as `_terminal_excluded` reads it.
                    statuses[row[0]] = status_as_reported(row[1], label=_label)
                except Exception:
                    # A malformed status row must not turn an ADVISORY
                    # annotation into a whole-read failure on this public method
                    # (D1: a decoration never fails the read). Per ROW, not per
                    # comprehension: one bad row may cost exactly one peer,
                    # never every peer — which is what a single dict-comprehension
                    # under one handler would do (measured: rows
                    # [("p1", "live"), (["x"], "live")] → {} → p1 loses "live").
                    _logger.warning(
                        "related_status row malformed — that peer's status is "
                        "left blank, the rest are kept", exc_info=True)
                    continue
        for e in entries:
            try:
                e["related_status"] = statuses.get(e.get("related_id"), "")
            except TypeError:
                # A NON-SCALAR `related_id` is unhashable. It is in scope by
                # design: `get_relationships`' dedupe key is deliberately total,
                # so a row whose `other.id` came back as a replayed/diagnostic
                # list is KEPT and reaches here. Blanking that one peer's
                # annotation is the whole cost — a decoration must never fail the
                # read (D1), which is what the per-row guard above promises and
                # what an unguarded lookup here would have broken one line later
                # (measured: `statuses.get(['bad-id'])` → TypeError, whole read
                # lost AFTER the sweep had logged that it succeeded).
                e["related_status"] = ""
        return entries

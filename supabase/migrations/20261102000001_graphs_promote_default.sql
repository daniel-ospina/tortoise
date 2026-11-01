-- #10180 — nothing could MOVE the default graph. This is the write side #10025
-- left out (the row existed; nothing could re-point it).
--
-- WHAT WAS MEASURED (2026-10-27, on the issue): a team can create graphs and
-- mint a key bound to one, but every writer of `kind='default'` is a CREATION
-- path — `supabase_control` :3961 / :4126 / :4528 / :4624 and the backfill in
-- `20261028000001` — and NOTHING updates it. So the default a team gets is the
-- default it keeps for the life of the account, and moving to a graph it
-- outgrew needed a hand-edited production row: unversioned, untested, and
-- invisible to every reader that derives the default from it.
--
-- ── WHY ONE FUNCTION, AND WHY TWO STATEMENTS INSIDE IT ────────────────────
-- The move must never be observable as "two defaults" or "zero defaults". A
-- PostgREST caller cannot express either guarantee: it issues ONE table write
-- per request, so demoting then promoting from Python is two commits with a
-- zero-default window between them, and promoting first violates
-- `uq_graphs_org_default_active` (a UNIQUE INDEX is checked per row, row by
-- row, not at end of statement — so a single UPDATE touching both rows can
-- fail on whichever row the executor reaches first).
--
-- A plpgsql function runs in ONE transaction, so the DEMOTE-then-PROMOTE pair
-- below is atomic from every other session's point of view: inside the
-- transaction there is a moment with zero `kind='default'` rows, and outside
-- it there is never a moment with anything but exactly one. The unique index is
-- therefore never violated, and a RAISE (unknown graph, trashed graph) rolls
-- the demote back with it — the failure path cannot leave the org with no
-- default.
--
-- SERIALIZATION, stated for what it actually covers. Every promotion for an
-- org queues on the ORGANIZATION row (`FOR UPDATE`), so two concurrent
-- promotions cannot both read the same "current default", demote it and then
-- each promote a different target (the loser landing on the unique index as a
-- 500). The TARGET graph row is locked too, which is what closes the
-- read-then-write window against the one writer that could make the promote
-- UPDATE match zero rows: `soft_delete_graph` is a plain PostgREST PATCH, it
-- takes no org lock, and it only refuses a row that is already
-- `kind='default'` — so without the row lock a trash committing between the
-- read and the promote would empty the org's default pointer while the function
-- happily returned `ok`. Lock order is always organizations → graphs (this is
-- the only function taking both), so it cannot deadlock the single-row
-- writers.
--
-- ⚠️ What the lock does NOT do, because the previous draft of this comment
-- claimed it did: it does not serialize a promotion against the CREATION paths
-- that mint a `kind='default'` row (`set_graph_name`'s no-active-row insert,
-- `ensure_default_graph_row`'s self-heal) — none of them takes the org lock. The
-- backstop for "never TWO" is therefore the partial UNIQUE index
-- `uq_graphs_org_default_active` (20261028000001), NOT this discipline: a
-- racing mint collides with it and fails its own statement (that path already
-- adopts the winner / rolls back), instead of being queued here. The
-- POST-CONDITION check at the end of the function is what covers "never ZERO",
-- which no index can express.
--
-- ── WHY `organizations.graph_name` MOVES TOO ─────────────────────────────
-- This is the part that is easy to get wrong and expensive to get wrong. The
-- default graph's DATA-PLANE key is the org's namespace, and `graph_name` IS
-- that namespace's name (`20261028000001` §3 re-asserts it for every
-- `kind='default'` row: "A `kind='default'` row's namespace must BE the org's
-- graph name ... a drifted value would point the default graph at another
-- org's data"). Every reader of "where is the default graph" derives it from
-- `organizations.graph_name` — `graph_metadata` (hence GET /v1/graphs and the
-- per-graph BACKUP sweep, `backup_sweep.enumerate_org_graphs`), the org-wide
-- `graph_namespace` on every resolved credential, and the MCP lane's registry
-- parity read.
--
-- So a promotion that flipped ONLY `kind` would leave the promoted row's
-- `namespace` (`org_{org}_{gid}`, its own storage — `_provision_graph`) at odds
-- with the org's default namespace: `graph_metadata` would publish the default
-- with the OLD namespace while `stable_id` named the NEW row, the backup sweep
-- would dump the old graph's data under the "default" label AND dump it again
-- as the demoted custom graph, and an org-wide capture would file its record
-- rows under the promoted graph's id while its nodes stayed in the old
-- namespace. That is the `derived != measured` class #10025 exists to remove.
--
-- The two moves below keep the invariant true by construction: after a
-- promotion, `organizations.graph_name = <the promoted row's namespace>`, so the
-- default's namespace and the default's row are the same graph. The DEMOTED row
-- keeps its own namespace untouched — no data moves, no `GRAPH.COPY`, and the
-- old graph stays a fully addressable custom graph (the UX analysis on the
-- issue: "one atomic verb, old graph kept").
--
-- ── WHICH HALF OF THE INVARIANT IS THE DB'S, AND WHICH IS THIS FILE'S ──
-- `20261028000001` created `uq_graphs_org_default_active ON public.graphs
-- (org_id) WHERE kind = 'default' AND status <> 'deleted'` — at most one LIVE
-- default row per org, in the DB, not in this procedure's discipline. That is
-- the "never TWO" half and this file ADDS NOTHING to it.
--
-- The index says NOTHING about "never ZERO" (no row, no violation), so that
-- half has to be checked, and it is: the post-condition at the end of this
-- function re-reads the org's default rows and RAISEs unless exactly one ACTIVE
-- default exists and it is the target. That covers the whole failure class —
-- the demote committing without the promote (a concurrent trash slipping
-- through the row lock's window, a future edit to these statements, pre-existing
-- duplicate-default drift making the demote touch two rows) — and a RAISE rolls
-- the demote back with it, so the org keeps the default it had.
-- `supabase/tests/20261102000001_graphs_promote_default.sql` EXECUTES both
-- halves against real Postgres: the index refuses a second live default, and
-- every refusal path is asserted to leave exactly one default behind.
--
-- IDEMPOTENCE: promoting the current default is a SUCCESS and moves no graph
-- row (`already_default: true`), matching the house rule for the neighbouring
-- key toggles. It DOES converge a drifted `organizations.graph_name` onto the
-- default row's namespace (§3's invariant) and reports `namespace_repaired`, so
-- the one call whose contract is "row and org pointer agree" cannot answer
-- `ok` to an org where they do not. A refusal is reserved for a state the
-- caller must resolve: an unknown/foreign graph (404 at the endpoint), a
-- tombstoned one (trash → restore first, 409 at the endpoint), a MOVE target
-- whose namespace no org-wide resolver can FOLLOW (409 — the shapes are the two
-- per-graph prefixes `supabase_control.per_graph_namespace_prefixes` names, plus
-- the org's own bare `org_{org_id}`, which the historical open already resolves;
-- without this the verb would succeed and then route every org-wide lane back to
-- the graph it demoted), or an org that ALREADY holds more than one active
-- default when the re-promote asks for a repair (409 — this arm returns before
-- the demote, so it cannot be the call that converges that drift).
--
-- ⚠️ The namespace refusal is scoped to the MOVE arm on purpose. The repair arm
-- converges whatever namespace the default row already carries (it is the row
-- §3 pins the pointer to); refusing it there would turn the documented
-- idempotent 200 into a 409 for an org whose drift is exactly what that arm is
-- for.
CREATE OR REPLACE FUNCTION public.promote_default_graph(
    p_org_id text,
    p_graph_id text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_kind          text;
    v_status        text;
    v_target_name   text;
    v_target_ns     text;
    v_previous_id   text;
    v_previous_ns   text;
    v_org_ns        text;
    v_repaired      boolean := false;
BEGIN
    IF p_org_id IS NULL OR p_graph_id IS NULL THEN
        RAISE EXCEPTION 'promote_default_graph: org_id and graph_id are required';
    END IF;

    -- The serialization point (see the header): every promotion for this org
    -- queues here, so the "current default" read below cannot be stale for
    -- another promotion's write.
    PERFORM 1
       FROM public.organizations o
      WHERE o.id = p_org_id
        FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'promote_default_graph: unknown organization %', p_org_id;
    END IF;

    -- Resolve the target WITHOUT a status filter, so a TOMBSTONED graph is
    -- refused by name ("it is in the trash") rather than reported as unknown —
    -- the two need different remedies for the operator. FOR UPDATE locks the
    -- row for the rest of this transaction, which is what closes the
    -- read-then-write window: `soft_delete_graph` is a plain PostgREST PATCH
    -- that takes no org lock and only refuses a row that is ALREADY
    -- `kind='default'`, so without this lock a concurrent trash could commit
    -- between this SELECT and the promote UPDATE below — making the UPDATE
    -- match ZERO rows silently, after the demote had already run, and leaving
    -- the org with NO live default and `graph_name` pointing at a trashed
    -- graph's namespace. With the lock the trash waits; and because the target
    -- is `default` by then, `soft_delete_graph`'s own guard then refuses it.
    --
    -- Lock order is ALWAYS organizations → graphs (this is the only function in
    -- the tree taking both), so it cannot deadlock against the single-row
    -- writers (`soft_delete_graph`, `set_graph_name`, `set_graph_recording`).
    SELECT g.kind, g.name, g.namespace, g.status
      INTO v_kind, v_target_name, v_target_ns, v_status
      FROM public.graphs g
     WHERE g.id = p_graph_id
       AND g.org_id = p_org_id
        FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'promote_default_graph: graph not found';
    END IF;
    IF v_status = 'deleted' THEN
        RAISE EXCEPTION 'promote_default_graph: graph is in the trash';
    END IF;
    IF v_status IS DISTINCT FROM 'active' THEN
        -- `graphs.status` is free text with no CHECK (20260901000001), so an
        -- unknown status is refused rather than promoted: every reader of the
        -- default filters `status = 'active'`, and promoting a row they cannot
        -- see would mint a default that resolves for nobody (#10025's
        -- permanently-unnamed shape).
        RAISE EXCEPTION 'promote_default_graph: graph is not active';
    END IF;

    -- Already the default → idempotent success, and the graph rows do NOT move
    -- (a retry cannot churn). The one WRITE this arm may still make is a REPAIR:
    -- `20261028000001` §3 pins "a kind='default' row's namespace IS the org's
    -- graph_name", and an org whose pair drifted (a hand-fixed row, a
    -- half-applied backfill) would otherwise be told `ok` by the one call whose
    -- whole contract is that the two agree. So converge the pointer and say so.
    IF v_kind = 'default' THEN
        -- ⛔ THE POST-CONDITION IS CHECKED HERE TOO (review round 8, P2). This
        -- arm RETURNs before the demote, so without this check the one call the
        -- header calls the drift-repair lever would answer `ok, already_default:
        -- true` over an org holding TWO active defaults — the state the
        -- post-condition exists to refuse — while the converging demote never
        -- ran. Both arms now enforce the SAME invariant, which is what the
        -- header and the COMMENT claim.
        --
        -- Honest about reachability: while `uq_graphs_org_default_active`
        -- stands, two ACTIVE defaults cannot exist, so this RAISE cannot fire on
        -- a healthy database — it is a contract-parity guard, not a live race.
        -- It fires for the one state the header names as pre-existing drift
        -- (created while the index was absent, or after an ops drop of it), and
        -- the doubled assertion is what stops a future edit from restoring the
        -- asymmetry.
        -- `count(*) <> 1` (not `> 1`) because the target IS one of them: it was
        -- just read as `kind='default'` and passed the `status='active'` check
        -- above, so a count below two is impossible here.
        IF (SELECT count(*) FROM public.graphs g
             WHERE g.org_id = p_org_id AND g.kind = 'default'
               AND g.status = 'active') <> 1 THEN
            RAISE EXCEPTION
                'promote_default_graph: the org already holds more than one active default, so promoting the current default cannot repair it';
        END IF;
        v_repaired := false;
        IF v_target_ns IS NOT NULL THEN
            SELECT o.graph_name INTO v_org_ns
              FROM public.organizations o WHERE o.id = p_org_id;
            IF v_org_ns IS DISTINCT FROM v_target_ns THEN
                UPDATE public.organizations o
                   SET graph_name = v_target_ns
                 WHERE o.id = p_org_id;
                v_repaired := true;
            END IF;
        END IF;
        RETURN jsonb_build_object(
            'ok', true,
            'already_default', true,
            'graph_id', p_graph_id,
            'name', v_target_name,
            'namespace', v_target_ns,
            'previous_default_graph_id', p_graph_id,
            'namespace_repaired', v_repaired
        );
    END IF;

    -- 0. REFUSE a target the ORG-WIDE LANES CANNOT FOLLOW (review round 8, P1).
    -- Every resolver of "the org's default graph" derives it from
    -- `organizations.graph_name` through ONE shape test
    -- (`supabase_control.moved_default_namespace`): a PER-GRAPH namespace of
    -- this org — `org_{org_id}_` (minted by `insert_graph`/`_provision_graph`)
    -- or the pre-#3543 `team_{org_id}_` that no data migration rewrites — or
    -- else it takes the HISTORICAL open (`org_{org_id}`). Writing a namespace
    -- of any OTHER shape here therefore SUCCEEDS and silently routes every
    -- org-wide lane back to the graph this same transaction demotes: records
    -- filed under the promoted graph, nodes written to the demoted one.
    --
    -- So the verb cannot mint that state. The accepted shapes are exactly the
    -- prefixes in `supabase_control.per_graph_namespace_prefixes`, plus the
    -- org's OWN bare `org_{org_id}`, which the historical open already follows
    -- correctly — that is what un-promoting back to the original graph looks
    -- like. ⛔ The bare `team_{org_id}` is NOT accepted (review round 9, P1): the
    -- historical open resolves `org_{org_id}`, not `team_{org_id}`, so promoting
    -- a legacy org-level row would point every lane at a THIRD graph — neither
    -- the promoted one nor the demoted one. `moved_default_namespace` returning
    -- None for it is the #2023 pin, not an oversight; a promotion target must be
    -- a name a resolver actually OPENS.
    -- Prefix comparison uses `left(...) = ...`, NOT `LIKE`:
    -- `_` is a single-character WILDCARD in a LIKE pattern, so
    -- `namespace LIKE 'org_' || p_org_id || '\_%'` would need an escape and the
    -- unescaped form matches `orgX…` (PostgreSQL 18 §9.7.1).
    IF v_target_ns IS NOT NULL
       AND v_target_ns <> 'org_' || p_org_id
       AND left(v_target_ns, length('org_' || p_org_id || '_'))
           <> 'org_' || p_org_id || '_'
       AND left(v_target_ns, length('team_' || p_org_id || '_'))
           <> 'team_' || p_org_id || '_'
    THEN
        RAISE EXCEPTION
            'promote_default_graph: the target namespace is not one the org-wide lanes can follow';
    END IF;

    -- 1. DEMOTE first. Demote-then-promote is the only order the unique index
    -- permits; between the two statements this transaction holds zero default
    -- rows, which no other session can observe (see the header).
    UPDATE public.graphs g
       SET kind = 'custom'
     WHERE g.org_id = p_org_id
       AND g.kind = 'default'
       AND g.status <> 'deleted'
       AND g.id <> p_graph_id
    RETURNING g.id, g.namespace INTO v_previous_id, v_previous_ns;

    -- The `id <> p_graph_id` guard above cannot normally matter (the target is
    -- not `kind='default'` at this point), but it keeps the statement honest if
    -- a future caller reaches here for an already-default row.

    -- 2. PROMOTE the target. Same transaction, so the pair is atomic.
    UPDATE public.graphs g
       SET kind = 'default'
     WHERE g.id = p_graph_id
       AND g.org_id = p_org_id
       AND g.status = 'active';

    -- 3. The org's default NAMESPACE follows the default row (§"WHY
    -- organizations.graph_name MOVES TOO"). Guarded on the target actually
    -- having a namespace: `graphs.namespace` is NOT NULL, and a NULL org
    -- graph_name is the shape `ensure_default_graph_row` refuses too — writing
    -- a NULL would point the org's data plane at nothing.
    IF v_target_ns IS NOT NULL THEN
        UPDATE public.organizations o
           SET graph_name = v_target_ns
         WHERE o.id = p_org_id;
    END IF;

    -- 4. POST-CONDITION, checked rather than assumed: exactly ONE live default
    -- row for this org and it is the target. The unique index forbids two; the
    -- lock above plus the promote UPDATE's own `status = 'active'` predicate are
    -- what forbid ZERO, and a zero is invisible to the index (no row, no
    -- violation). Checking it here means ANY way of reaching a zero-default
    -- state — a concurrent writer the lock cannot see, a future edit to the
    -- statements above, a duplicate-default drift that made the demote touch two
    -- rows — rolls the WHOLE move back and raises instead of returning `ok` over
    -- an org whose record can never be filed again (the `no_tenant` state
    -- #10025 exists to remove).
    IF NOT EXISTS (
        SELECT 1 FROM public.graphs g
         WHERE g.org_id = p_org_id AND g.kind = 'default'
           AND g.status = 'active' AND g.id = p_graph_id)
       OR (SELECT count(*) FROM public.graphs g
            WHERE g.org_id = p_org_id AND g.kind = 'default'
              AND g.status = 'active') <> 1
    THEN
        RAISE EXCEPTION
            'promote_default_graph: the default did not move (org % has no single active default at % after the move)',
            p_org_id, p_graph_id;
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'already_default', false,
        'graph_id', p_graph_id,
        'name', v_target_name,
        'namespace', v_target_ns,
        'previous_default_graph_id', v_previous_id,
        'previous_namespace', v_previous_ns
    );
END $$;

-- PostgREST exposes the RPC to the service role only; the endpoint is the one
-- caller and it carries its own authz (team:manage key or owner/admin session),
-- exactly like the neighbouring graph endpoints. No grant to `authenticated`:
-- a default move is an ORG-WIDE tenancy change, never a per-member action.
-- Same shape as every other service-role RPC in this tree (0010/0015/…): revoke
-- from the client roles and grant the service role EXPLICITLY — a bare
-- `REVOKE … FROM public` would leave `service_role` without EXECUTE, because
-- this function is not owned by it.
REVOKE ALL ON FUNCTION public.promote_default_graph(text, text)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.promote_default_graph(text, text)
    TO service_role;

COMMENT ON FUNCTION public.promote_default_graph(text, text) IS
    '#10180: atomically move the org''s `kind=''default''` row (and the org''s '
    'default namespace, organizations.graph_name) to an existing active graph. '
    'One transaction, at most one live default always (the partial unique index '
    'uq_graphs_org_default_active) and never zero (checked as a post-condition '
    'that rolls the move back, on BOTH the move and the idempotent arm). '
    'Idempotent on the current default, where it also repairs a drifted org '
    'namespace. Raises on an unknown/foreign/trashed/non-active target, a MOVE '
    'target whose namespace no org-wide resolver can follow, and an org already '
    'holding two active defaults, naming which. The namespace refusal is scoped '
    'to the MOVE arm: the repair arm converges whatever namespace the default row '
    'already carries.';

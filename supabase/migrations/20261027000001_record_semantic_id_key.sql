-- ============================================================================
-- Migration 20261027000001: `Object`/`Subject` key on `id`, not on `name` (#9881)
-- ----------------------------------------------------------------------------
-- WHY. Under #3590 an `:Object`/`:Subject` gets a minted, immutable `id` and the
-- `name` becomes a MUTABLE NATURAL KEY that two live entities may share. A
-- record row keyed on `node_key = name` cannot express that model, and it is
-- what makes a rename unrepresentable: `tortoise/record_projection.py`'s
-- `OpDispatch.refused_ops` refused `EntityMutated op=rename` with the reason
-- *"a rename MOVES the canonical key (Object/Subject key = `name`), so it is a
-- key migration, not a patch"*. The refusal was correct for the schema it was
-- written against. This migration removes its premise instead of the refusal:
-- the key becomes the `id`, so a rename is `UPDATE … SET name = $new` on a
-- fixed key — no key move, nothing to cascade through `node_identity` or
-- `relation`, and two same-name incarnations stay two rows.
--
-- WHY NOT THE KEY MOVE IT REPLACES. Moving `node_key` from the old name to the
-- new one is the alternative (#9912 sketches it). It loses twice: the row is
-- still keyed by a natural key, so a rename onto a name another live entity
-- already holds is a PRIMARY KEY COLLISION (the new incarnation cannot exist),
-- and moving a key drags every incident `relation` endpoint with it because the
-- composite FKs are `ON DELETE CASCADE` only and `CHECK`s are not DEFERRABLE.
-- `id` as the key has neither problem. The name is not lost: it stays a column,
-- and `record_projection._ENTITY_MATCH_COLUMNS` still matches it, which is what
-- resolves a structural edge's NAME-keyed replay descriptor (§1.3) to the row.
--
-- SCOPE — the owner ruling of 2026-10-10, applied to the record exactly as
-- `20261012000001_record_event_stream.sql` and `record_projection.py`'s module
-- docstring already apply it: *there are no existing customers (only the dogfood
-- instance)*, so this migration contains NO migration-safety apparatus — no
-- dual-write window, no dual-read, no blue/green cutover, no rollback seam. It
-- is a plain hard swap.
--
-- WHAT THE RE-KEY IS, AND IS NOT. The record is `replay(journal)` (STORAGE-
-- ARCHITECTURE §3), so rows written before this migration are DERIVED data under
-- an obsolete key shape, not customer data: the re-key below rewrites those rows
-- into the shape a replay under the new contract would have produced. It is not
-- a backfill — there is no new information being invented, no second identity
-- scheme left running, and every value it writes (`node_key = id`) is a column
-- the row already holds.
--
-- ID-LESS ROWS ARE REFUSED, LOUDLY. A row with `id IS NULL` cannot satisfy the
-- new contract, and deriving an id in SQL would be a second declaration of the
-- identity mint (`tortoise/ids.py`) in a language that cannot import it. The
-- pre-flight below names the condition and stops, instead of letting
-- `ADD CONSTRAINT` fail later with a Postgres message that says nothing about
-- the cause. Which shape a legacy id-less journal line should take is #3590's
-- open decision D2 — the record does not guess it here.
--
-- A FORWARD migration, never an edit of 20261012000002: migrations are
-- append-only history, and the `check-migration-append-only` gate refuses an
-- edit to a landed file.
-- ============================================================================

-- ── 0. Pre-flight: no id-less Object/Subject row can survive this contract ───
DO $$
DECLARE
    bad_object  bigint;
    bad_subject bigint;
BEGIN
    SELECT count(*) INTO bad_object  FROM public.object  WHERE id IS NULL;
    SELECT count(*) INTO bad_subject FROM public.subject WHERE id IS NULL;
    IF bad_object > 0 OR bad_subject > 0 THEN
        RAISE EXCEPTION
            'record_semantic_id_key: % Object row(s) and % Subject row(s) carry '
            'no id. The (label -> id) contract cannot be satisfied for them, and '
            'this migration will not mint an identity in SQL (#9881; the shape is '
            '#3590 decision D2). Replay them from a journal that carries ids, or '
            'delete those derived rows, then re-apply.', bad_object, bad_subject
            USING ERRCODE = 'raise_exception';
    END IF;
END $$;

-- ── 0b. DROP the two key pins BEFORE anything moves ─────────────────────────
-- ⛔ ORDER IS LOAD-BEARING, and this is the bug a review measured: the OLD pin is
-- `CHECK (name IS NOT NULL AND node_key = name)`, so step 3's `SET node_key = id`
-- VIOLATES it and the migration aborts on the first real Object/Subject row —
-- `new row for relation "object" violates check constraint
-- "chk_object_node_key_is_contract_key"`. They are re-declared against the new
-- key in step 6, so the table is unpinned only WITHIN this file.
ALTER TABLE public.object
    DROP CONSTRAINT IF EXISTS chk_object_node_key_is_contract_key;
ALTER TABLE public.subject
    DROP CONSTRAINT IF EXISTS chk_subject_node_key_is_contract_key;

-- ── 1. Identity parents for the NEW keys ────────────────────────────────────
-- The `(org_id, graph_id, node_label, node_key)` FK on `object`/`subject` and
-- on both `relation` endpoints points HERE, so the parent rows must exist before
-- any child key moves. Idempotent (`ON CONFLICT DO NOTHING`): a row already
-- keyed by its id has its parent already.
INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key)
SELECT o.org_id, o.graph_id, 'Object', o.id FROM public.object o
 ON CONFLICT DO NOTHING;
INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key)
SELECT s.org_id, s.graph_id, 'Subject', s.id FROM public.subject s
 ON CONFLICT DO NOTHING;

-- ── 2. Incident relation endpoints follow the key ───────────────────────────
-- Both halves, both labels: an edge whose endpoint is a re-keyed Object/Subject
-- must name the key the node row will have, or `rebuild_from_record` wires the
-- edge to a node that is no longer there. Scoped to rows whose `node_key` is
-- still the old (name) spelling, so a second apply is a no-op.
UPDATE public.relation r
   SET src_key = o.id
  FROM public.object o
 WHERE r.src_label = 'Object'
   AND r.org_id = o.org_id AND r.graph_id = o.graph_id
   AND r.src_key = o.node_key AND o.node_key <> o.id;

UPDATE public.relation r
   SET tgt_key = o.id
  FROM public.object o
 WHERE r.tgt_label = 'Object'
   AND r.org_id = o.org_id AND r.graph_id = o.graph_id
   AND r.tgt_key = o.node_key AND o.node_key <> o.id;

UPDATE public.relation r
   SET src_key = s.id
  FROM public.subject s
 WHERE r.src_label = 'Subject'
   AND r.org_id = s.org_id AND r.graph_id = s.graph_id
   AND r.src_key = s.node_key AND s.node_key <> s.id;

UPDATE public.relation r
   SET tgt_key = s.id
  FROM public.subject s
 WHERE r.tgt_label = 'Subject'
   AND r.org_id = s.org_id AND r.graph_id = s.graph_id
   AND r.tgt_key = s.node_key AND s.node_key <> s.id;

-- ── 3. Re-key the node rows themselves ──────────────────────────────────────
UPDATE public.object  SET node_key = id WHERE node_key <> id;
UPDATE public.subject SET node_key = id WHERE node_key <> id;

-- ── 4. Drop the identity rows the name keys left behind ─────────────────────
-- Only rows nothing references any more: the node row moved to its id and every
-- relation endpoint followed in step 2. A leftover would be a name that still
-- resolves to a node that no longer claims it.
DELETE FROM public.node_identity ni
 WHERE ni.node_label IN ('Object', 'Subject')
   AND NOT EXISTS (
         SELECT 1 FROM public.object o
          WHERE o.org_id = ni.org_id AND o.graph_id = ni.graph_id
            AND o.node_key = ni.node_key)
   AND NOT EXISTS (
         SELECT 1 FROM public.subject s
          WHERE s.org_id = ni.org_id AND s.graph_id = ni.graph_id
            AND s.node_key = ni.node_key)
   AND NOT EXISTS (
         SELECT 1 FROM public.relation r
          WHERE r.org_id = ni.org_id AND r.graph_id = ni.graph_id
            AND ((r.src_label = ni.node_label AND r.src_key = ni.node_key)
              OR (r.tgt_label = ni.node_label AND r.tgt_key = ni.node_key)));

-- ── 5. The contract itself: node_identity's generated CASE ──────────────────
-- `key_property` is GENERATED ALWAYS, so it cannot be altered in place — the
-- column is dropped and re-declared. Readers that parse this DDL take the LAST
-- declaration (`record_projection.key_properties`, which `cache_projection`
-- delegates to), which is why the re-declaration is a full CASE and not a diff.
ALTER TABLE public.node_identity
    DROP COLUMN IF EXISTS key_property,
    ADD COLUMN key_property text NOT NULL GENERATED ALWAYS AS (
        CASE node_label
            WHEN 'Point'   THEN 'id'
            WHEN 'Event'   THEN 'eventId'
            WHEN 'Subject' THEN 'id'
            WHEN 'Object'  THEN 'id'
            WHEN 'Source'  THEN 'url'
            WHEN 'Session' THEN 'id'
        END) STORED;

COMMENT ON TABLE public.node_identity IS
    'Interface E: the canonical node key per label (Point=id, Event=eventId, Subject/Object=id since #9881, Source=url, Session=id). A foreign key in this schema is (node_label, node_key), never a bare id (#7955). The key is IMMUTABLE for every label; a name is a mutable natural key resolved to its id (#3590), never an identity. The ~9,700 legacy ids are OPAQUE AND VALID and are NOT normalised here (W1b, P2/P1-severe per #7955 §1.4).';

-- ── 6. The per-table pins, re-declared against the new key ──────────────────
-- BOTH halves stay, and each closes a different fail-open shape:
--   * `id IS NOT NULL` — a bare `node_key = id` is NULL (not FALSE) when `id` is
--     NULL and would be silently satisfied (the shape #8179 P1 closed);
--   * `name IS NOT NULL` — a name-less Object/Subject is unaddressable by the
--     natural key that #3590 keeps as the mutable lookup, and the graph's own
--     writers no-op on a falsy name. Dropping it would have made a nameless row
--     legal that no producer ever creates.
-- `DROP … IF EXISTS` + `ADD` in ONE statement is idempotent on re-apply, as
-- 20261014000001 established for the props CHECK; the DROP in step 0b is what
-- makes the pair safe to re-state here.
ALTER TABLE public.object
    DROP CONSTRAINT IF EXISTS chk_object_node_key_is_contract_key,
    ADD CONSTRAINT chk_object_node_key_is_contract_key CHECK (
        name IS NOT NULL AND id IS NOT NULL AND node_key = id
    );

ALTER TABLE public.subject
    DROP CONSTRAINT IF EXISTS chk_subject_node_key_is_contract_key,
    ADD CONSTRAINT chk_subject_node_key_is_contract_key CHECK (
        name IS NOT NULL AND id IS NOT NULL AND node_key = id
    );

-- A name is a LOOKUP key now, not an identity, so it gets the index a natural
-- key needs — NOT UNIQUE, deliberately: two live incarnations may share one
-- name, and a unique index would re-impose the very constraint this migration
-- removes. ⚠️ Stated exactly: no projection path resolves a node BY name today
-- (`record_projection._ENTITY_MATCH_COLUMNS` matches Object/Subject on `id`
-- alone — it did before this migration too), so this index serves reads that
-- arrive with a name, which is the lookup #3590's model creates, not a matcher
-- this module uses.
CREATE INDEX IF NOT EXISTS idx_object_name
    ON public.object (org_id, graph_id, name);
CREATE INDEX IF NOT EXISTS idx_subject_name
    ON public.subject (org_id, graph_id, name);

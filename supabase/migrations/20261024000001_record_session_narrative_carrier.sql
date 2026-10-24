-- ============================================================================
-- Migration 20261024000001: §9.1's narrative CARRIER, recorded on the SCHEMA
--                             (#9555 / R7, epic #7879)
-- ----------------------------------------------------------------------------
-- WHAT THIS FILE IS. `20261023000001` created `public.session_narrative` and
-- shipped with a `⚠️ THE CARRIER GAP` note: the narrative TEXT was written to
-- the graph by the layer-1 commit's raw Cypher, and raw Cypher is not a journal
-- record (§17.1), so a live capture landed this row with its two COUNTS filled
-- and its two TEXT columns NULL, and a record-sourced rebuild could reproduce
-- the rows but not the narrative.
--
-- That gap is CLOSED (#9555). `hosted_api._execute_commit_writes` now journals
-- `SessionNarrated` (`record_projection.NARRATIVE_RECORD_TYPE`) immediately
-- after the raw-Cypher MERGE it describes, carrying `sessionId` + `story_arc` +
-- `summary`; the record is declared `NoRecordEffect` because §9.1 places the
-- narrative OUTSIDE the graph ("not in the graph, not a `Document` node, not a
-- `Point`"), and its record effect is the SHAPE-KEYED projection
-- (`record_projection._narrative_statements`), which fires for any record that
-- names its session and carries the text. The write path stays singular:
-- `sdk._emit_event` → `record_writer.write_live_record` →
-- `record_projection.replay`.
--
-- ⛔ WHY THIS IS A NEW FILE AND NOT AN EDIT TO `20261023000001`. A merged
-- migration is APPEND-ONLY (#1095 — the rule exists for the #1001 P0, where an
-- applied-migration edit diverged the repo from production). CI enforces it:
-- `.github/scripts/check-migration-append-only` refuses any `M`/`T`/`D` on a
-- merged migration, so a correction CANNOT be delivered as a comment edit —
-- measured on this branch, `rc=1`. A migration's comments are nevertheless where
-- a developer reads the schema's own story, so the correction has to live in the
-- SCHEMA too. `COMMENT ON` is the append-only-compliant way to do that: it is a
-- real catalog object (`\d+ public.session_narrative` prints it), it changes no
-- column and no policy, and it is idempotent.
--
-- ⚠️ WHAT IS NOT CLAIMED HERE. The producer's precondition is unchanged: the
-- record is reached through the live door, which writes only when
-- `TORTOISE_RECORD_DSN` is armed and only from a record that reached a
-- CONFIGURED JOURNAL. An unarmed deployment is byte-identical to the
-- pre-record one (§2.0 of `STORAGE-ARCHITECTURE.md`) and the narrative is then
-- live-only, exactly as before — not silently claimed.
-- ============================================================================

COMMENT ON TABLE public.session_narrative IS
  'The S1 narrative home (R7, #9555; STORAGE-ARCHITECTURE.md §9.1). §9.1 keeps '
  'the narrative — and the raw agent-session transcript it was derived from — '
  'OUT of the graph: it is text in Postgres, with no confidence, no NAND and no '
  'topological weight, because it is read for search and evidence, never argued '
  'about. Both columns'' carriers exist: the TEXT arrives as the journal record '
  'SessionNarrated (#9555), projected by the shape-keyed '
  'record_projection._narrative_statements; the COUNTS arrive on SessionRecorded. '
  'The raw turns are NOT duplicated here — they are the episodic public.point '
  'rows keyed <session>_t<index>, served by tortoise.record_read.';

COMMENT ON COLUMN public.session_narrative.narrative IS
  'The S1 narrative: the connected prose form of one captured session, verbatim '
  'as the capture stored it. Journal carrier: SessionNarrated ("story_arc"), '
  'landed by the shape-keyed projection. §9.1: this column is the home, not a '
  'field on whichever row happened to hold it.';

COMMENT ON COLUMN public.session_narrative.summary IS
  'The narrative''s one-line summary. Projected ONLY alongside the narrative, '
  'because the capture''s own session :Source also sets a "summary" — to the '
  'transcript''s first utterance, which is not §9.1 prose.';

COMMENT ON COLUMN public.session_narrative.capture_redactions IS
  'R4''s per-window redaction count (#4911), carried by SessionRecorded and '
  'reproduced by a record-sourced rebuild. INTEGER on purpose: a count is not an '
  'envelope, and the jsonb shape was the schema-to-producer disagreement #9264 '
  'removed. A value this column cannot hold is written as NO count, never as 0 '
  'and never coerced.';

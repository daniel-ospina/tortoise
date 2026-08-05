-- Audit events table for Tortoise Hosted Platform
-- Immutable append-only log of all operations per team
-- See: docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md §4

CREATE TABLE IF NOT EXISTS audit_events (
  id          TEXT PRIMARY KEY,
  team_id     TEXT NOT NULL,
  actor_user_id UUID,
  operation   TEXT NOT NULL,
  resource_type TEXT,
  resource_id TEXT,
  ip_address  TEXT,
  user_agent  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_team_time
  ON audit_events (team_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_actor
  ON audit_events (actor_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_operation
  ON audit_events (operation, created_at DESC);

-- Immutability trigger: prevent UPDATE/DELETE on audit events
CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    RAISE EXCEPTION 'audit_events is append-only: % not allowed', TG_OP;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_events_immutable
  BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH STATEMENT
  EXECUTE FUNCTION prevent_audit_mutation();

ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY audit_team_isolation ON audit_events
  FOR SELECT
  USING (team_id = current_setting('app.current_team_id', true));

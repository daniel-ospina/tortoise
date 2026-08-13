-- Migration 20260813000003: audit_events ip index (#1081)
--
-- R8 signup_velocity (durable sweeper — documented follow-on, see
-- docs/scoping/scoping-1081-agent-signup-abuse.md): per-IP window queries
-- over audit_events (operation='agent_signup', ip_address, created_at).
-- The index ships ahead of the sweeper: signup audit rows are accruing now
-- and the table is small — adding the index later on a large append-only
-- table is the riskier path. Sweeper itself is deferred (no consumer yet:
-- the shipped R8 signal is the in-memory tracker).

CREATE INDEX IF NOT EXISTS idx_audit_ip_time
    ON public.audit_events (ip_address, created_at DESC);

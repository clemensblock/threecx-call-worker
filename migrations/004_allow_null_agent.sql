-- Migration: 004_allow_null_agent.sql
-- Purpose: Allow NULL agent_user_id for inbound calls logged by the worker.
-- The original call_logs table required agent_user_id (outbound calls always
-- have an agent). Inbound calls arrive before any agent picks up, so the
-- column must be nullable.

BEGIN;

ALTER TABLE call_logs ALTER COLUMN agent_user_id DROP NOT NULL;

COMMIT;

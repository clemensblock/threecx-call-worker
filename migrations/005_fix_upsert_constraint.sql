-- Migration: 005_fix_upsert_constraint.sql
-- Purpose: Replace partial unique INDEX with a real UNIQUE CONSTRAINT
-- so PostgREST ON CONFLICT upserts work correctly.
--
-- Background: Migration 001 created a partial unique index:
--   CREATE UNIQUE INDEX idx_call_logs_participant_state
--       ON call_logs(participant_id, state) WHERE participant_id IS NOT NULL;
-- PostgREST requires an actual UNIQUE CONSTRAINT (not a partial index)
-- for on_conflict resolution.
-- PostgreSQL allows multiple NULLs in a unique constraint column,
-- so existing rows with NULL participant_id are unaffected.

BEGIN;

DROP INDEX IF EXISTS idx_call_logs_participant_state;

ALTER TABLE call_logs
    ADD CONSTRAINT uq_call_logs_participant_state
    UNIQUE (participant_id, state);

COMMIT;

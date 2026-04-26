-- Migration: 006_allow_null_inbound_columns.sql
-- Purpose: Drop NOT NULL constraints on columns that are only populated
-- for outbound calls. Inbound calls from the worker don't have
-- an agent or CRM status yet when first logged.

BEGIN;

ALTER TABLE call_logs ALTER COLUMN agent_extension DROP NOT NULL;
ALTER TABLE call_logs ALTER COLUMN status DROP NOT NULL;
ALTER TABLE call_logs ALTER COLUMN phone_number DROP NOT NULL;

COMMIT;

-- Migration: 001_inbound_support.sql
-- Purpose: Extend call_logs and customers tables for inbound call support
-- Run manually before deploying the worker:
--   psql "$DATABASE_URL" -f migrations/001_inbound_support.sql

BEGIN;

-- ============================================================
-- 1. Phone normalization function (reusable)
-- ============================================================

CREATE OR REPLACE FUNCTION normalize_de_phone(raw text) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT
AS $$
DECLARE
    cleaned text;
BEGIN
    IF raw IS NULL OR raw = '' THEN
        RETURN NULL;
    END IF;

    -- Strip whitespace, dashes, parens, dots
    cleaned := regexp_replace(raw, '[\s\-\(\)\.]', '', 'g');

    IF cleaned = '' THEN
        RETURN NULL;
    END IF;

    -- +49... -> keep
    IF cleaned LIKE '+49%' THEN
        RETURN cleaned;
    END IF;

    -- 0049... -> +49...
    IF cleaned LIKE '0049%' THEN
        RETURN '+' || substring(cleaned FROM 3);
    END IF;

    -- Other 00... international -> +...
    IF cleaned LIKE '00%' AND length(cleaned) > 4 THEN
        RETURN '+' || substring(cleaned FROM 3);
    END IF;

    -- 0... (German local) -> +49...
    IF cleaned LIKE '0%' AND length(cleaned) > 1 THEN
        RETURN '+49' || substring(cleaned FROM 2);
    END IF;

    -- Anything with + prefix -> keep
    IF cleaned LIKE '+%' THEN
        RETURN cleaned;
    END IF;

    -- Check if anything numeric remains and is long enough
    IF regexp_replace(cleaned, '[^0-9]', '', 'g') = '' THEN
        RETURN NULL;
    END IF;

    IF length(regexp_replace(cleaned, '[^0-9]', '', 'g')) < 2 THEN
        RETURN NULL;
    END IF;

    RETURN cleaned;
END;
$$;

-- ============================================================
-- 2. Extend customers table
-- ============================================================

ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone_e164 text;
CREATE INDEX IF NOT EXISTS idx_customers_phone_e164 ON customers(phone_e164);

-- Backfill: normalize existing phone column to E.164
UPDATE customers
SET phone_e164 = normalize_de_phone(phone)
WHERE phone IS NOT NULL
  AND phone != ''
  AND phone_e164 IS NULL;

-- ============================================================
-- 3. Extend call_logs table
-- ============================================================

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS direction text
    DEFAULT 'outbound'
    CHECK (direction IN ('inbound', 'outbound'));

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS participant_id text;

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS extension text;

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS state text
    CHECK (state IN ('ringing', 'connected', 'terminated', 'failed'));

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS connected_at timestamptz;

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS terminated_at timestamptz;

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS duration_seconds integer;

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS caller_id text;

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS caller_id_e164 text;

-- Unique constraint for idempotent event processing
-- Use a partial index to avoid conflicts with NULL participant_id (existing rows)
CREATE UNIQUE INDEX IF NOT EXISTS idx_call_logs_participant_state
    ON call_logs(participant_id, state)
    WHERE participant_id IS NOT NULL;

-- ============================================================
-- 4. RLS policies for inbound rows
-- ============================================================

-- Agents see calls on their extension
CREATE POLICY IF NOT EXISTS call_logs_agent_select ON call_logs
    FOR SELECT
    USING (
        direction = 'outbound'
        OR extension = current_setting('app.current_extension', true)
        OR current_setting('app.user_role', true) = 'admin'
    );

-- Admins see everything (covered by the OR clause above)

COMMIT;

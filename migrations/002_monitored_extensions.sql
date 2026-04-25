-- Migration: 002_monitored_extensions.sql
-- Purpose: Create table for DB-managed monitored extensions (replaces env var)
-- Managed via CRM frontend, read by the worker with TTL cache.

BEGIN;

CREATE TABLE IF NOT EXISTS threecx_monitored_extensions (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    extension text NOT NULL UNIQUE,
    label text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamptz DEFAULT now() NOT NULL,
    updated_at timestamptz DEFAULT now() NOT NULL
);

COMMENT ON TABLE threecx_monitored_extensions
    IS 'Extensions to monitor via the 3CX WebSocket worker. Managed via CRM frontend.';
COMMENT ON COLUMN threecx_monitored_extensions.extension
    IS '3CX extension number (e.g. 100, 101)';
COMMENT ON COLUMN threecx_monitored_extensions.label
    IS 'Human-readable label (e.g. employee name)';
COMMENT ON COLUMN threecx_monitored_extensions.is_active
    IS 'Whether this extension is currently monitored';

-- RLS
ALTER TABLE threecx_monitored_extensions ENABLE ROW LEVEL SECURITY;

CREATE POLICY threecx_ext_read ON threecx_monitored_extensions
    FOR SELECT USING (true);

CREATE POLICY threecx_ext_admin ON threecx_monitored_extensions
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM user_roles
            WHERE user_id = auth.uid()
            AND role IN ('superadmin', 'admin')
        )
    );

GRANT ALL ON threecx_monitored_extensions TO service_role;

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON threecx_monitored_extensions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMIT;

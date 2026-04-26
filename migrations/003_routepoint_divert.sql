-- Migration: 003_routepoint_divert.sql
-- Purpose: Add route_to column for routepoint call forwarding.
-- When a routepoint (e.g. crmintegration) receives a call, the worker
-- uses route_to to determine where to forward it (e.g. "1000").

BEGIN;

ALTER TABLE threecx_monitored_extensions
    ADD COLUMN IF NOT EXISTS route_to text;

COMMENT ON COLUMN threecx_monitored_extensions.route_to
    IS 'Target extension to forward calls to. Used for routepoint DNs that receive calls before forwarding.';

-- Set crmintegration to forward to 1000
UPDATE threecx_monitored_extensions
    SET route_to = '1000'
    WHERE extension = 'crmintegration';

COMMIT;

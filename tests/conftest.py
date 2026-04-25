from __future__ import annotations

import os

# Set required env vars before any imports that trigger Settings
os.environ.setdefault("THREECX_BASE_URL", "https://pbx.test.local:5001")
os.environ.setdefault("THREECX_CLIENT_ID", "test-client-id")
os.environ.setdefault("THREECX_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("THREECX_MONITORED_EXTENSIONS", "100,101,102")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")

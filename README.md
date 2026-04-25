# 3CX Inbound Call WebSocket Worker

Long-running Python worker that maintains a persistent WebSocket connection to the tinana 3CX PBX, listens for call events (especially inbound calls), and writes them into the Supabase Postgres database used by the CRM at `crm.tinana.de`. The CRM frontend subscribes via Supabase Realtime for inbound-call popups and call history.

## Architecture

```
3CX PBX (WebSocket) → Worker → Supabase DB → CRM Frontend (Realtime)
```

- **WebSocket listener**: Connects to `wss://<pbx-host>/callcontrol/ws`, subscribes to `/callcontrol`
- **Event handler**: Parses participant lifecycle events, determines direction (inbound/outbound)
- **DB writer**: Inserts one row per state change (`ringing` → `connected` → `terminated`) into `call_logs`
- **Health/Metrics**: FastAPI endpoints at `/health` and `/metrics` (Prometheus format)

## Prerequisites

### 3CX Admin Console Setup

1. Go to **3CX Admin Console → Integrations → API**
2. Create an API application with `client_credentials` grant type
3. Note the **Client ID** and **Client Secret**
4. Under **Call Control → Monitoring**, add the extensions you want to monitor (e.g. `100`, `101`, `102`)

### Database Migration

Before deploying, run the migrations manually against the Supabase database:

```bash
psql "$DATABASE_URL" -f migrations/001_inbound_support.sql
psql "$DATABASE_URL" -f migrations/002_monitored_extensions.sql
```

This adds:
- `normalize_de_phone()` function for E.164 normalization
- `phone_e164` column on `customers` (with backfill)
- Inbound-specific columns on `call_logs` (`direction`, `participant_id`, `state`, `extension`, etc.)
- Unique constraint `(participant_id, state)` for idempotent event processing
- RLS policy for agent/admin access control
- `threecx_monitored_extensions` table for frontend-managed extension monitoring

## Local Development

```bash
# Install dependencies
pip install uv
uv pip install --system -r pyproject.toml
uv pip install --system pytest pytest-asyncio pytest-cov ruff respx

# Copy env template and fill in values
cp .env.example .env
# Edit .env with your 3CX and Supabase credentials

# Run the worker
uv run uvicorn worker.main:app --reload

# Run tests
pytest tests/ -k "not sql_parity" -v

# Lint
ruff check worker/ tests/
ruff format worker/ tests/
```

## Configuration

All config via environment variables (validated by pydantic-settings on startup):

| Variable | Required | Default | Description |
|---|---|---|---|
| `THREECX_BASE_URL` | Yes | — | 3CX server URL, e.g. `https://pbx.tinana.de:5001` |
| `THREECX_CLIENT_ID` | Yes | — | API app client ID from 3CX |
| `THREECX_CLIENT_SECRET` | Yes | — | API key from 3CX |
| `SUPABASE_URL` | Yes | — | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | — | Service role key (write access) |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `RECONNECT_MAX_BACKOFF` | No | `60` | Max seconds between reconnect attempts |
| `EXTENSIONS_REFRESH_SECONDS` | No | `60` | How often to re-read monitored extensions from DB |

### Monitored Extensions

Extensions to monitor are stored in the `threecx_monitored_extensions` table (not an env var). Add/remove extensions via the CRM frontend or directly in the DB:

```sql
INSERT INTO threecx_monitored_extensions (extension, label) VALUES ('100', 'Empfang');
INSERT INTO threecx_monitored_extensions (extension, label) VALUES ('101', 'Vertrieb');
```

The worker refreshes the extension list from the DB every `EXTENSIONS_REFRESH_SECONDS` seconds (default: 60).

## Deployment (K3s Cluster)

### 1. Add secrets to Infisical

Create folder `/threecx-worker` in Infisical project `tinana-k3s-tl6-f` (environment: `prod`) with all required env vars.

### 2. Apply Kubernetes manifests

```bash
# From a control-plane node (k3s-srv-01):
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Create namespace and Infisical secret sync
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret-external.yaml

# Wait for secrets to sync
kubectl get secret threecx-worker-secrets -n threecx-worker

# Deploy the worker
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

### 3. Verify

```bash
# Check pod is running
kubectl get pods -n threecx-worker

# Check health endpoint
kubectl port-forward -n threecx-worker svc/threecx-call-worker 8000:8000
curl http://localhost:8000/health
# Expected: {"status":"ok","ws_connected":true,"last_event_at":"..."}

# Check logs
kubectl logs -n threecx-worker -l app=threecx-call-worker -f
```

### 4. Dry-run manifests

```bash
kubectl apply --dry-run=client -f k8s/
```

## Container Image

```bash
# Build locally
docker build -t threecx-call-worker .

# Push to GHCR (done by CI on main merge)
docker tag threecx-call-worker ghcr.io/clemensblock/threecx-call-worker:latest
docker push ghcr.io/clemensblock/threecx-call-worker:latest
```

## Known Caveats

- **`party_did` field**: May be empty for some trunk configurations. The worker logs this at DEBUG level but does not fail. DID identification for multi-number setups may require additional trunk configuration in 3CX.
- **Token refresh**: The worker caches the OAuth token and refreshes it proactively. If the 3CX PBX restarts or invalidates tokens, the worker will automatically reconnect.
- **Single replica**: Designed as a single-replica deployment. Running multiple replicas would require distributed locking to avoid duplicate event processing.

## Project Structure

```
threecx-call-worker/
├── worker/
│   ├── main.py              # FastAPI app + lifespan
│   ├── config.py            # pydantic-settings
│   ├── threecx_client.py    # OAuth token + REST calls
│   ├── ws_listener.py       # WebSocket loop + reconnect
│   ├── event_handler.py     # Parse + dispatch events
│   ├── db.py                # Supabase client + write functions
│   ├── phone.py             # normalize_phone()
│   └── metrics.py           # Prometheus counters
├── tests/
├── migrations/
│   └── 001_inbound_support.sql
├── k8s/                     # Kubernetes manifests
├── Dockerfile
├── pyproject.toml
└── .github/workflows/ci.yml
```

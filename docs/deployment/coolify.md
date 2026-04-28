# Coolify Deployment

Use Coolify with the repo `Dockerfile`. Do not use Nixpacks for this backend.

## Backend App

Create one Coolify application for this repo:

1. New Resource -> Application -> Git repository.
2. Build pack: `Dockerfile`.
3. Dockerfile path: `Dockerfile`.
4. Public port: `8000`.
5. Health check path: `/health`.
6. Add persistent storage:
   - Mount path: `/app/data`
7. Add environment variables.
8. Deploy.

The container starts three internal processes:

- FastAPI backend on `0.0.0.0:8000`
- LiveKit agent worker health server on internal port `8081`
- KB ingestion worker

Only port `8000` should be public in Coolify.

## Required Env

Set these in Coolify before the first deploy:

```env
HOST=0.0.0.0
PORT=8000
AGENT_HOST=0.0.0.0
AGENT_PORT=8081
APP_DATA_DIR=/app/data
APP_CONFIG_FILE=/app/data/config.json
KB_DATA_DIR=/app/data/kb

LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxxxxxxxx
LIVEKIT_API_SECRET=your_livekit_api_secret_here
SIP_TRUNK_ID=ST_xxxxxxxxxxxxxxxx
LIVEKIT_AGENT_NAME=vobiz-demo-agent

GOOGLE_API_KEY=your_google_api_key

SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_KEY=your_supabase_anon_key_here
```

Optional:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DEFAULT_TRANSFER_NUMBER=
APP_BASE_URL=https://your-backend-domain.com
VOBIZ_SIP_DOMAIN=your_sip_domain.sip.vobiz.ai
VOBIZ_USERNAME=
VOBIZ_PASSWORD=
VOBIZ_OUTBOUND_NUMBER=+91XXXXXXXXXX
```

If Coolify gives you a public URL automatically, the backend can also read common Coolify URL env vars.

## Supabase SQL

Run these once in Supabase, in order:

1. `sql/supabase/setup.sql`
2. `sql/supabase/migration_v2.sql`
3. `sql/supabase/migration_v3.sql`
4. `sql/supabase/migration_v4_voice_metrics.sql`
5. `sql/supabase/migration_v5_kb.sql`

For an existing deployment, also run:

6. `sql/supabase/migration_v6_backend_cleanup.sql`
7. `sql/supabase/migration_v7_kb_demo_sources.sql`

## Verify

After deploy, open:

```text
https://your-backend-domain.com/health
```

Expected:

```json
{"status":"ok", "...":"..."}
```

Then check:

```text
https://your-backend-domain.com/openapi.json
```

## Frontend App

Deploy the generated frontend as a second Coolify application.

The frontend prompt is set up for Vite, so use:

```env
VITE_API_BASE_URL=https://your-backend-domain.com
```

For a Node/Vite frontend app in Coolify:

- Install command: `npm ci`
- Build command: `npm run build`
- Start command: `npm run preview -- --host 0.0.0.0 --port 5173`
- Public port: `5173`

If you deploy it as a static site, use:

- Build command: `npm ci && npm run build`
- Publish directory: `dist`

## Fast Fixes

- Backend shows 502: make sure public port is `8000`, not `8081`.
- Backend loses config or KB after redeploy: add persistent storage at `/app/data`.
- Agent keeps restarting: check `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `SIP_TRUNK_ID`, and `GOOGLE_API_KEY`.
- Frontend cannot call backend: set `VITE_API_BASE_URL` to the public backend URL and redeploy the frontend.

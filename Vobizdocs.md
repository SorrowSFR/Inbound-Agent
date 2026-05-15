# Vobiz + LiveKit + Supabase + Frontend Setup Guide

This is the slow, no-skipped-steps guide.

The backend does three jobs:

1. It runs the voice agent.
2. It exposes the API for logs, config, appointments, calls, and knowledge base.
3. It talks to LiveKit, Supabase, Gemini, and Vobiz.

The frontend is not inside this repo. A coding agent builds it later from `docs/ui-agent-prompt.md`.

## The Simple Picture

Inbound call:

```text
Customer calls your Vobiz number
-> Vobiz sends the call to LiveKit
-> LiveKit sends the call to this agent
-> The backend saves logs and appointments
```

Outbound call:

```text
Frontend or API asks backend to call a phone number
-> Backend asks LiveKit to start a room
-> LiveKit uses its outbound SIP trunk
-> Vobiz places the phone call
-> The agent talks to the customer
```

Transfer to human:

```text
Customer is already talking to the agent
-> Customer asks for a human
-> Agent sends a SIP transfer through LiveKit
-> Vobiz routes the call to your human number
```

## Values To Save First

Make a private note called `voice-agent-secrets`.

Save these values.

### Vobiz Values

```text
VOBIZ_ACCOUNT_ID=
VOBIZ_AUTH_ID=
VOBIZ_AUTH_TOKEN=
VOBIZ_INBOUND_TRUNK_ID=
VOBIZ_INBOUND_TRUNK_DOMAIN=
VOBIZ_SIP_DOMAIN=
VOBIZ_USERNAME=
VOBIZ_PASSWORD=
VOBIZ_OUTBOUND_NUMBER=
```

### LiveKit Values

```text
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
LIVEKIT_SIP_DOMAIN=
LIVEKIT_INBOUND_TRUNK_ID=
LIVEKIT_DISPATCH_RULE_ID=
SIP_TRUNK_ID=
LIVEKIT_AGENT_NAME=vobiz-demo-agent
```

`SIP_TRUNK_ID` means the LiveKit outbound trunk ID. It usually starts with `ST_`.

### Supabase Values

```text
SUPABASE_URL=
SUPABASE_KEY=
```

### Gemini Value

```text
GOOGLE_API_KEY=
```

Do not paste real tokens into GitHub, screenshots, public chats, or shared docs.

## Step 1: Create The Supabase Project

1. Open Supabase.
2. Create a new project.
3. Wait until the project is ready.
4. Open Project Settings.
5. Open API.
6. Copy the Project URL.
7. Save it as `SUPABASE_URL`.
8. Copy the anon/public key.
9. Save it as `SUPABASE_KEY`.

Now create the database tables.

1. Open Supabase SQL Editor.
2. Open `sql/supabase/setup.sql` in this repo.
3. Copy the whole file.
4. Paste it into Supabase SQL Editor.
5. Click Run.

Supabase is done when `sql/supabase/setup.sql` runs without errors. The one file handles fresh installs, upgrades, storage buckets, and cleanup from older backend branches.

## Step 2: Create The LiveKit Project

1. Open LiveKit Cloud.
2. Create or open your project.
3. Copy the project URL.
4. Save it as `LIVEKIT_URL`.

It usually looks like:

```text
wss://your-project.livekit.cloud
```

Now create API keys.

1. In LiveKit Cloud, open Settings.
2. Open API Keys.
3. Create an API key.
4. Copy the key.
5. Save it as `LIVEKIT_API_KEY`.
6. Copy the secret.
7. Save it as `LIVEKIT_API_SECRET`.

Now find the SIP domain.

1. In LiveKit Cloud, open Telephony or SIP.
2. Find your SIP domain or SIP URI.
3. Save it as `LIVEKIT_SIP_DOMAIN`.

It usually looks like:

```text
your-project.sip.livekit.cloud
```

## Step 3: Set Up Inbound Calling

Inbound means customers call your Vobiz number and reach the agent.

### 3A. Create The Vobiz Inbound Trunk

In Vobiz:

1. Open Trunks or SIP Trunks.
2. Create a trunk.
3. Name it:

```text
Vobiz to LiveKit inbound
```

4. Set trunk direction to:

```text
inbound
```

or:

```text
both
```

5. Set transport to `udp`, unless Vobiz support tells you otherwise.
6. Set secure/TLS to `false`, unless Vobiz support tells you otherwise.
7. Set inbound destination to your LiveKit SIP domain:

```text
your-project.sip.livekit.cloud
```

8. Attach your Vobiz phone number to this trunk.
9. Save the trunk.
10. Save the `trunk_id`.
11. Save the `trunk_domain`.

If you use the Vobiz API, the request looks like this:

```bash
curl -X POST "https://api.vobiz.ai/api/v1/account/{account_id}/trunks" \
  -H "X-Auth-ID: {auth_id}" \
  -H "X-Auth-Token: {auth_token}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Vobiz to LiveKit inbound",
    "trunk_direction": "inbound",
    "transport": "udp",
    "secure": false,
    "inbound_destination": "your-project.sip.livekit.cloud"
  }'
```

### 3B. Create The LiveKit Inbound Trunk

In LiveKit Cloud:

1. Open Telephony.
2. Open SIP.
3. Open Inbound Trunks.
4. Create a new inbound trunk.
5. Name it:

```text
Vobiz inbound
```

6. Add the phone number that Vobiz will send into LiveKit.
7. Save the trunk.
8. Copy the LiveKit inbound trunk ID.
9. Save it as `LIVEKIT_INBOUND_TRUNK_ID`.

If LiveKit asks for JSON, use this shape:

```json
{
  "name": "Vobiz inbound",
  "numbers": ["+91XXXXXXXXXX"]
}
```

### 3C. Create The LiveKit Dispatch Rule

This tells LiveKit which agent should answer inbound calls.

In LiveKit Cloud:

1. Open Telephony.
2. Open SIP.
3. Open Dispatch Rules.
4. Create a rule.
5. Choose the inbound trunk you created.
6. Send calls to this agent:

```text
vobiz-demo-agent
```

7. Save the rule.
8. Copy the dispatch rule ID.
9. Save it as `LIVEKIT_DISPATCH_RULE_ID`.

If LiveKit asks for JSON, use this shape:

```json
{
  "name": "Vobiz inbound to demo agent",
  "rule": {
    "dispatchRuleIndividual": {
      "roomPrefix": "inbound-"
    }
  },
  "roomConfig": {
    "agents": [
      {
        "agentName": "vobiz-demo-agent"
      }
    ]
  }
}
```

If LiveKit shows a trunk selector in the UI, choose your `Vobiz inbound` trunk there. If you omit trunk matching, LiveKit can match all inbound trunks.

Inbound setup is done when a call to the Vobiz number creates a LiveKit room and dispatches `vobiz-demo-agent`.

## Step 4: Set Up Outbound Calling

Outbound means the backend starts a call to a customer.

You need two things:

1. Vobiz must allow outbound SIP calls.
2. LiveKit must have an outbound trunk that uses Vobiz.

### 4A. Set Up Outbound In Vobiz

In Vobiz:

1. Open Trunks or SIP Trunks.
2. Create a trunk, or edit the existing trunk.
3. Name it:

```text
LiveKit to Vobiz outbound
```

4. Set trunk direction to:

```text
outbound
```

or:

```text
both
```

5. Make sure outbound calling is enabled.
6. Make sure your outbound caller ID number is approved.
7. Save these values:

```text
VOBIZ_SIP_DOMAIN=your_sip_domain.sip.vobiz.ai
VOBIZ_USERNAME=your_vobiz_sip_username
VOBIZ_PASSWORD=your_vobiz_sip_password
VOBIZ_OUTBOUND_NUMBER=+91XXXXXXXXXX
```

If Vobiz asks what system will connect to it, the answer is:

```text
LiveKit Cloud outbound SIP
```

If Vobiz asks for allowed IPs, use the LiveKit Cloud SIP IPs from LiveKit's SIP docs or ask LiveKit support for the current range.

### 4B. Create The LiveKit Outbound Trunk

In LiveKit Cloud:

1. Open Telephony.
2. Open SIP.
3. Open Outbound Trunks.
4. Create a new outbound trunk.
5. Name it:

```text
Vobiz outbound
```

6. Set the SIP address to:

```text
your_sip_domain.sip.vobiz.ai
```

7. Set auth username to your `VOBIZ_USERNAME`.
8. Set auth password to your `VOBIZ_PASSWORD`.
9. Add the outbound phone number:

```text
+91XXXXXXXXXX
```

10. Save the trunk.
11. Copy the LiveKit outbound trunk ID.
12. Put that value in `.env` as `SIP_TRUNK_ID`.

If LiveKit asks for JSON, use this shape:

```json
{
  "name": "Vobiz outbound",
  "address": "your_sip_domain.sip.vobiz.ai",
  "numbers": ["+91XXXXXXXXXX"],
  "authUsername": "your_vobiz_sip_username",
  "authPassword": "your_vobiz_sip_password"
}
```

### 4C. Sync The Outbound Trunk From This Repo

Use this if you already created the LiveKit outbound trunk and want the repo to update it with the Vobiz values from `.env`.

First put these in `.env`:

```env
SIP_TRUNK_ID=ST_xxxxxxxxxxxxxxxx
VOBIZ_SIP_DOMAIN=your_sip_domain.sip.vobiz.ai
VOBIZ_USERNAME=your_vobiz_sip_username
VOBIZ_PASSWORD=your_vobiz_sip_password
VOBIZ_OUTBOUND_NUMBER=+91XXXXXXXXXX
```

Then run:

```powershell
python setup_trunk.py
```

Success looks like:

```text
OK: SIP trunk updated successfully.
```

## Step 5: Set Up Human Transfer

Human transfer means the caller says "transfer me to a person".

Put these in `.env`:

```env
VOBIZ_SIP_DOMAIN=your_sip_domain.sip.vobiz.ai
DEFAULT_TRANSFER_NUMBER=+91XXXXXXXXXX
```

The agent will build a SIP transfer target like:

```text
sip:+91XXXXXXXXXX@your_sip_domain.sip.vobiz.ai
```

Your Vobiz trunk must allow SIP transfer or SIP REFER for this to work.

## Step 6: Create The Backend `.env`

Copy the example:

```powershell
Copy-Item .env.example .env
```

Open `.env`.

Fill this minimum set:

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
LIVEKIT_AGENT_NAME=vobiz-demo-agent

GOOGLE_API_KEY=your_google_api_key

SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_KEY=your_supabase_anon_key_here
```

For outbound calls, also fill:

```env
SIP_TRUNK_ID=ST_xxxxxxxxxxxxxxxx
VOBIZ_SIP_DOMAIN=your_sip_domain.sip.vobiz.ai
VOBIZ_USERNAME=your_vobiz_sip_username
VOBIZ_PASSWORD=your_vobiz_sip_password
VOBIZ_OUTBOUND_NUMBER=+91XXXXXXXXXX
```

For transfer, also fill:

```env
DEFAULT_TRANSFER_NUMBER=+91XXXXXXXXXX
```

## Step 7: Install The Backend Locally

Run these commands in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Step 8: Start The Backend Locally

Run:

```powershell
python start_stack.py
```

This starts:

- backend API at `http://127.0.0.1:8000`
- LiveKit agent worker health server at `http://127.0.0.1:8081`
- knowledge base worker

Check the backend:

```text
http://127.0.0.1:8000/health
```

Check the API contract:

```text
http://127.0.0.1:8000/openapi.json
```

Check Supabase setup:

```text
http://127.0.0.1:8000/api/setup/status
```

## Step 9: Test Inbound

1. Keep `python start_stack.py` running.
2. Call your Vobiz phone number.
3. Watch the backend terminal.
4. You should see LiveKit and agent logs.
5. The agent should answer.
6. After the call, check call logs from the API or frontend.

If inbound does not work, check:

1. Vobiz number is attached to the inbound trunk.
2. Vobiz inbound destination is your LiveKit SIP domain.
3. LiveKit inbound trunk exists.
4. LiveKit dispatch rule points to `vobiz-demo-agent`.
5. Backend worker is running.
6. `LIVEKIT_AGENT_NAME=vobiz-demo-agent` is in `.env`.

## Step 10: Test Outbound

Keep the backend running.

In a second terminal:

```powershell
.\.venv\Scripts\Activate.ps1
python make_call.py --to +91XXXXXXXXXX --name Test
```

The phone should ring.

If outbound does not work, check:

1. `SIP_TRUNK_ID` is the LiveKit outbound trunk ID.
2. `VOBIZ_SIP_DOMAIN` is correct.
3. `VOBIZ_USERNAME` is correct.
4. `VOBIZ_PASSWORD` is correct.
5. `VOBIZ_OUTBOUND_NUMBER` is approved in Vobiz.
6. The LiveKit outbound trunk uses those Vobiz credentials.
7. The agent worker name is `vobiz-demo-agent`.

## Step 11: Deploy Backend On Coolify

In Coolify:

1. New Resource.
2. Application.
3. Select this Git repo.
4. Build pack: `Dockerfile`.
5. Dockerfile path: `Dockerfile`.
6. Public port: `8000`.
7. Health check path: `/health`.
8. Add persistent storage:

```text
/app/data
```

9. Add the same env values from `.env`.
10. Deploy.

Open:

```text
https://your-backend-domain.com/health
```

Expected result:

```json
{"status":"ok"}
```

Use only port `8000` for the backend. Port `8081` is internal agent health.

Full Coolify notes:

```text
docs/deployment/coolify.md
```

## Step 12: Build The Frontend

The frontend is built by a coding agent.

Do this:

1. Open `docs/ui-agent-prompt.md`.
2. Copy the whole prompt.
3. Paste it into a coding agent.
4. Add this line at the top:

```text
Use this prompt to build the actual frontend application now. Do not just explain the instructions. Create the files, install the packages, and make it runnable.
```

5. Tell it:

```text
Use Vite + React + TypeScript + Tailwind CSS.
Use port 5173.
Use VITE_API_BASE_URL for the backend URL.
```

Local frontend `.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Coolify frontend `.env`:

```env
VITE_API_BASE_URL=https://your-backend-domain.com
```

Run frontend locally:

```powershell
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

## Step 13: Deploy Frontend On Coolify

Create a second Coolify app for the frontend.

Use these settings:

```text
Install command: npm ci
Build command: npm run build
Start command: npm run preview -- --host 0.0.0.0 --port 5173
Public port: 5173
```

Set:

```env
VITE_API_BASE_URL=https://your-backend-domain.com
```

## Final Checklist

Supabase:

- `sql/supabase/setup.sql` ran successfully.
- `SUPABASE_URL` is set.
- `SUPABASE_KEY` is set.
- `/api/setup/status` returns ok.

LiveKit:

- `LIVEKIT_URL` is set.
- `LIVEKIT_API_KEY` is set.
- `LIVEKIT_API_SECRET` is set.
- Inbound trunk exists.
- Dispatch rule sends calls to `vobiz-demo-agent`.
- Outbound trunk exists if you need outbound calls.
- `SIP_TRUNK_ID` is the outbound trunk ID.

Vobiz:

- Inbound trunk points to LiveKit SIP domain.
- Phone number is attached to inbound trunk.
- Outbound trunk allows calls if you need outbound.
- Vobiz SIP username/password/domain are saved.

Backend:

- `.env` is filled.
- `python start_stack.py` runs.
- `/health` returns ok.
- `/api/setup/status` returns ok.
- `/openapi.json` opens.

Frontend:

- Built with Vite.
- Runs on port `5173`.
- `VITE_API_BASE_URL` points to backend.

Coolify:

- Backend public port is `8000`.
- Backend health path is `/health`.
- Backend storage is `/app/data`.
- Frontend public port is `5173`.

## Tiny Troubleshooting

Backend 502 on Coolify:

- Public port must be `8000`.
- Health path must be `/health`.

Inbound call does not reach agent:

- Vobiz inbound destination must be the LiveKit SIP domain.
- LiveKit dispatch rule must point to `vobiz-demo-agent`.
- Backend must be running.

Outbound call fails:

- `SIP_TRUNK_ID` must be the LiveKit outbound trunk ID.
- Vobiz SIP username/password must be correct.
- Run `python setup_trunk.py`.

Transfer fails:

- `VOBIZ_SIP_DOMAIN` must be set.
- `DEFAULT_TRANSFER_NUMBER` must be set.
- Vobiz must allow SIP REFER or call transfer.

Frontend cannot load data:

- `VITE_API_BASE_URL` must point to the backend.
- Restart the frontend after changing `.env`.
- Open backend `/health` first.
- Open backend `/api/setup/status` next.

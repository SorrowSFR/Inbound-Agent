# Vobiz + Frontend Setup Guide

This project is a backend-only voice agent.

That means:

- This repo runs the voice agent and API.
- This repo does not include a finished frontend dashboard.
- You must use the frontend prompt in `docs/ui-agent-prompt.md` to make a coding agent build the frontend.
- Do not treat the prompt like normal notes. Paste the prompt into a coding agent and tell it to create the actual frontend files.

Think of it like this:

1. Vobiz receives the phone call.
2. Vobiz sends the call to LiveKit.
3. LiveKit sends the call to this backend voice agent.
4. The backend saves logs, appointments, stats, config, and knowledge base data.
5. A separate frontend dashboard talks to this backend API.
6. The frontend is built from the prompt in `docs/ui-agent-prompt.md`.

## Very Important

This app is inbound-only for the Vobiz setup.

For inbound calls:

- The app does not place outbound calls through Vobiz.
- The Vobiz trunk must point to your LiveKit SIP domain.
- You do not need a Vobiz `SIP_TRUNK_ID` in `.env` for inbound calls.

The backend may still contain outbound-call API endpoints for LiveKit dispatching, but this Vobiz guide is only about inbound calls coming from Vobiz into LiveKit.

## What You Need Before Starting

You need accounts for:

- Vobiz
- LiveKit
- Google Gemini
- Supabase

You also need this installed on your computer:

- Python
- Git
- A terminal such as PowerShell
- A coding agent or AI coding tool that can create frontend files

Examples of coding agents:

- Codex
- Claude Code
- Cursor
- Windsurf
- Any agent that can read files, write files, and run commands

## The 5 Vobiz Values To Save

When setting up Vobiz, save these values somewhere safe:

1. `account_id`
2. `auth_id`
3. `auth_token`
4. `trunk_id`
5. `trunk_domain`

The backend mostly needs the Vobiz-to-LiveKit connection to be correct. These five values are useful for support, debugging, and future trunk edits.

## Step 1: Get Vobiz API Keys

1. Sign in to Vobiz.
2. Open the API or developer section.
3. Copy your `account_id`.
4. Copy your `auth_id`.
5. Generate an `auth_token`.
6. Save the token somewhere safe.

Do not paste your real token into public chats, screenshots, GitHub issues, or shared docs.

## Step 2: Get Your LiveKit SIP Domain

1. Sign in to LiveKit.
2. Open your project.
3. Find your SIP domain.
4. It usually looks like this:

```text
your-project.sip.livekit.cloud
```

This LiveKit SIP domain is the destination where Vobiz must send inbound calls.

## Step 3: Create A Vobiz SIP Trunk

Use either:

- the Vobiz dashboard, or
- the Vobiz trunk API

The most important setting is:

```text
inbound_destination = your LiveKit SIP domain
```

Set:

- `trunk_direction` to `inbound` or `both`
- `transport` to `udp`, unless your provider setup says otherwise
- `secure` to `false`, unless your provider setup says otherwise
- `inbound_destination` to your LiveKit SIP domain

Example destination:

```text
your-project.sip.livekit.cloud
```

Example API call:

```bash
curl -X POST "https://api.vobiz.ai/api/v1/account/{account_id}/trunks" \
  -H "X-Auth-ID: {auth_id}" \
  -H "X-Auth-Token: {auth_token}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "SPX Voice Agent",
    "trunk_direction": "both",
    "transport": "udp",
    "secure": false,
    "inbound_destination": "your-project.sip.livekit.cloud"
  }'
```

After this succeeds, save:

- `trunk_id`
- `trunk_domain`

## Step 4: Create Your Backend `.env` File

Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

Then open `.env` and fill in your real values.

For this backend to run, these are the main values you need:

```env
GOOGLE_API_KEY=your_gemini_key
LIVEKIT_URL=wss://your-livekit-url
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_key
```

For this inbound Vobiz setup, do not worry about adding a Vobiz `SIP_TRUNK_ID`.

If you use this branch's outbound LiveKit dispatch features later, then follow the backend docs for `SIP_TRUNK_ID`. That is separate from receiving inbound calls from Vobiz.

## Step 5: Install Backend Requirements

Create a virtual environment:

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the Python packages:

```powershell
pip install -r requirements.txt
```

## Step 6: Set Up Supabase

Open Supabase SQL editor and run these files in order:

1. `sql/supabase/setup.sql`
2. `sql/supabase/migration_v2.sql`
3. `sql/supabase/migration_v3.sql`
4. `sql/supabase/migration_v4_voice_metrics.sql`
5. `sql/supabase/migration_v5_kb.sql`

If you are upgrading from an older dashboard or WhatsApp branch, also run:

6. `sql/supabase/migration_v6_backend_cleanup.sql`

## Step 7: Start The Backend

Run:

```powershell
python start_stack.py
```

This starts:

- the backend API at `http://127.0.0.1:8000`
- the LiveKit worker health server at `http://127.0.0.1:8081`
- the knowledge base worker in the background

## Step 8: Check That The Backend Works

Open this in your browser:

```text
http://127.0.0.1:8000/health
```

You should see:

```text
ok
```

Also check:

```text
http://127.0.0.1:8000/openapi.json
```

If that opens, the frontend builder can read the backend API contract.

## Step 9: Build The Frontend With The Prompt

This is the part people must not miss.

The frontend is not already built inside this repo. The frontend must be generated by using the prompt file:

```text
docs/ui-agent-prompt.md
```

Do this:

1. Open `docs/ui-agent-prompt.md`.
2. Copy the entire prompt.
3. Open your coding agent.
4. Paste the entire prompt.
5. Add this extra sentence at the top:

```text
Use this prompt to build the actual frontend application now. Do not just explain the instructions. Create the files, install the packages, and make it runnable.
```

6. Tell the coding agent where the backend is running:

```text
The backend API is running at http://127.0.0.1:8000
```

7. Tell the coding agent to inspect these files before building:

```text
openapi.json
docs/backend-contract.md
config.example.json
backend_api.py
```

8. Tell it to create a separate frontend app, usually with:

```text
Vite + React + TypeScript + Tailwind CSS
```

or:

```text
Next.js + TypeScript + Tailwind CSS
```

9. Make sure the generated frontend has an environment variable for the backend URL.

Example frontend `.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

or for Next.js:

```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

10. After the frontend is created, run the frontend install command.

Usually:

```powershell
npm install
```

11. Start the frontend.

Usually:

```powershell
npm run dev
```

12. Open the frontend URL shown in the terminal.

It is usually one of these:

```text
http://127.0.0.1:5173
http://localhost:5173
http://127.0.0.1:3000
http://localhost:3000
```

## What The Frontend Must Include

The prompt tells the coding agent to build a real dashboard, not a fake mockup.

The frontend should include:

- Overview page
- Configuration page
- Call logs page
- Transcript preview and download
- Contacts page
- Appointments page
- Knowledge base page
- File upload for knowledge base
- Knowledge base search
- LeadRat integration controls
- Outbound call page, if using the backend outbound endpoints
- Loading states
- Empty states
- Error messages
- Save buttons
- Confirm dialogs for delete or cancel actions
- A reusable API client
- A frontend `.env.example`
- A frontend README

If the coding agent only gives an explanation, it did not do the job. Tell it:

```text
You must implement the actual frontend files. Do not stop at instructions.
```

## Simple Frontend Builder Checklist

Use this checklist after the coding agent finishes:

- Can I run `npm install`?
- Can I run `npm run dev`?
- Does the frontend open in the browser?
- Does the frontend point to `http://127.0.0.1:8000`?
- Does `/health` work on the backend?
- Does the frontend load real data from the backend?
- Can I edit and save config?
- Can I see call logs?
- Can I open transcripts?
- Can I see appointments?
- Can I use the knowledge base pages?
- Are errors shown clearly instead of silently failing?

## If Inbound Calls Do Not Reach The Agent

Check these in order:

1. The backend is running.
2. `http://127.0.0.1:8000/health` returns `ok`.
3. The worker is running as `inbound-voice-agent`.
4. Your LiveKit SIP setup is active.
5. The Vobiz trunk destination is your LiveKit SIP domain.
6. The Vobiz trunk direction allows inbound calls.
7. Your Vobiz number is attached to the correct trunk.
8. You saved the correct `trunk_id` and `trunk_domain`.

## If The Frontend Does Not Work

Check these in order:

1. The backend is running.
2. The frontend `.env` points to the backend URL.
3. The frontend was restarted after changing `.env`.
4. `http://127.0.0.1:8000/openapi.json` opens in the browser.
5. The browser console has no API base URL errors.
6. The coding agent used `docs/ui-agent-prompt.md`.
7. The coding agent built real files instead of only writing advice.

## Short Version

Backend:

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python start_stack.py
```

Vobiz:

```text
Point inbound_destination to your LiveKit SIP domain.
```

Frontend:

```text
Copy everything in docs/ui-agent-prompt.md.
Paste it into a coding agent.
Tell the agent to build the actual frontend now.
Set the frontend API URL to http://127.0.0.1:8000.
Run npm install.
Run npm run dev.
Open the frontend in your browser.
```

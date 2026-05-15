# UI Generation Prompt

Paste the full prompt below into any coding agent when you want it to generate a real, separately deployable frontend for this backend.

The goal is not a mockup, starter shell, or partial dashboard. The goal is a complete, production-usable UI that wires together every retained backend capability cleanly.

Important: this file is meant to be used as an implementation prompt. Do not summarize it for the frontend builder. Copy the whole prompt, paste it into a coding agent, and make the agent build the actual frontend project.

If the agent starts explaining instead of writing files, send this:

```text
Use this prompt to build the actual frontend application now. Do not just explain the instructions. Create the files, install the packages, and make it runnable.
```

```md
You are a senior frontend engineer and product designer. Build a production-ready frontend for the SPXAgent backend-only Gemini branch.

Your output must be a complete, runnable frontend application, not a wireframe, not pseudo-code, and not a partial scaffold.

## Product Goal

Create a polished internal operations console for a voice AI system. The UI should allow staff to:

- manage backend configuration
- inspect call logs, transcripts, and summary stats
- browse contacts derived from call history and appointments
- create, update, and cancel appointments
- manage the knowledge base, PDF uploads, website URL/sitemap ingestion, and KB search
- dispatch single and bulk outbound calls

This frontend must feel cohesive and shippable. It should be something a team could actually deploy and use.

## Source Of Truth

Before building anything, inspect:

1. `/openapi.json`
2. `docs/backend-contract.md`
3. `config.example.json`
4. If available in the workspace, `backend_api.py`

Treat the backend as the source of truth. Do not invent new backend routes unless you clearly label them as not implemented and avoid depending on them.

## Non-Negotiable Constraints

- Build a frontend only. Do not move backend logic into the client.
- Keep the frontend deployable separately from the backend.
- Assume the backend is already implemented and should remain untouched unless absolutely necessary.
- Do not add WhatsApp, demo links, follow-up automation, landing pages, or bundled full-stack deployment assumptions.
- Do not add a login/auth system unless explicitly requested in a future pass.
- Do not ship mock data, fake APIs, placeholder metrics, or TODO-only sections in the final result.
- Do not create UI for removed features.
- Use the existing backend endpoints exactly as retained in the contract.

## Required UX Standard

The UI must look intentional and production-grade, not like a generic admin template.

Design expectations:

- clean, premium internal tooling aesthetic
- strong information hierarchy
- responsive on desktop and usable on tablet/mobile
- clear navigation and page structure
- accessible form controls and keyboard-friendly interactions
- polished loading, empty, error, and success states
- confirmation flows for destructive actions
- toasts or inline feedback for save/sync/dispatch actions
- no visual clutter, no placeholder lorem ipsum, no "coming soon" filler

If you choose a component library, use it well. The result should still feel customized and deliberate.

## Required Frontend Stack

Use Vite. Do not use Next.js.

Build with:

- Vite + React + TypeScript + Tailwind CSS

The local dev server must run on port `5173`.

Use these script behaviors:

- `npm run dev` starts Vite on `0.0.0.0:5173`
- `npm run build` creates a production build in `dist`
- `npm run preview` serves the production build on `0.0.0.0:5173`

Use `VITE_API_BASE_URL` for the backend URL. Include it in the frontend `.env.example`.

Structure the app cleanly with:

- a reusable API client layer
- typed request/response models
- modular UI components
- route-level pages/views
- shared form primitives
- shared table/filter/status components

If the OpenAPI schema is too loose for exact typing, derive safe frontend types from `docs/backend-contract.md` and the backend code.

## Information Architecture

Build the app around these primary sections:

1. Overview
- high-level stats from `/api/stats`
- recent call activity
- recent appointments
- KB status summary
- visible system health indicators from `/health` and `/api/setup/status` where appropriate

2. Configuration
- editable backend config form backed by `GET /api/config` and `POST /api/config`
- group fields into logical sections:
  - agent greeting and instructions
  - Gemini live runtime settings
  - session and timeout settings
  - LiveKit/SIP settings
  - Google API / Vertex AI settings
  - Supabase settings
  - Telegram notifications
  - KB settings
- include save feedback and safe handling for secret-like fields

3. Call Logs
- table of recent calls from `/api/logs`
- useful columns such as caller, phone, created time, duration, booking outcome, summary
- search/filter/sort if practical
- transcript preview and download flow using `GET /api/logs/{log_id}/transcript`
- show latency summary when present

4. Contacts
- list contacts from `/api/contacts`
- show caller name, phone number, total calls, appointment count, last seen, booked status
- allow fast jump to related calls or outbound call actions if the UI stack makes that clean

5. Appointments
- list and management UI for `/api/appointments`
- create appointment flow
- edit appointment flow
- cancel appointment flow
- clear handling for scheduling conflicts and validation errors
- present appointment status clearly

6. Knowledge Base
- KB status panel from `/api/kb/status`
- sources table from `/api/kb/sources`
- create/edit/delete source flows
- website URL source flow that accepts normal pages or sitemap URLs
- file upload flow via `/api/kb/upload`
- sync actions for eligible sources
- ingest jobs table from `/api/kb/jobs`
- KB search playground using `/api/kb/search`

7. Outbound Calls
- single call form using `POST /api/call/single`
- bulk call form using `POST /api/call/bulk`
- dispatch results UI showing success/failure per number

## Backend-Specific Notes You Must Respect

- The conversation runtime is Gemini-only.
- Default live model is `gemini-3.1-flash-live-preview`.
- Google runtime can use either AI Studio API key mode or Vertex AI mode via `google_genai_use_vertexai`, `google_cloud_project`, and `google_cloud_location`.
- Gemini TTS fallback behavior exists inside the backend only; the UI should not try to emulate runtime voice behavior.
- The transcript endpoint returns plain text, not a JSON transcript object.
- The backend may return `status: "setup_required"` or `status: "not_configured"` for setup or KB-related operations; handle these gracefully in the UI.
- The backend does not ship auth in this branch.

## Implementation Requirements

- Use a configurable backend base URL via frontend environment variables.
- Include sensible client-side validation before submitting forms.
- Normalize API error display so users get readable failure messages.
- Avoid over-fetching and avoid fragile state handling.
- Use reusable layout and data-display patterns across the app.
- Keep secrets out of logs and avoid exposing secret values more than necessary in the UI.
- Treat `********` secret values from `/api/config` as "already configured"; do not send them back as new secrets, and leave blank secret inputs unchanged unless the user explicitly clears them.
- Prefer composable dialogs, drawers, or modals for create/edit actions when that improves usability.

## Deliverables

Deliver a full frontend project that includes:

- complete application code
- package manifest and install scripts
- frontend environment example
- README with local run instructions
- clear explanation of how the frontend connects to the backend
- no missing core screens from the retained backend feature set

## Acceptance Bar

The work is only complete if:

- the app is runnable
- the UI is visually coherent and not template-sloppy
- every retained backend capability has a sensible UI surface
- no removed features reappear
- the app can be deployed separately from the backend
- the result feels like a deliverable product, not a starter kit

## Final Instruction

Do not stop at planning. Implement the actual frontend.

If you can write files and run commands, generate the full codebase.
If you can only answer in text, output the complete file-by-file implementation needed to build the frontend.
```

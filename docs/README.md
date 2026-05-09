# Docs

- [backend-contract.md](backend-contract.md): retained backend routes and expected payload shapes
- [ui-agent-prompt.md](ui-agent-prompt.md): Vite prompt for generating a frontend against this backend
- [setup/supabase.md](setup/supabase.md): one-file Supabase setup and verification
- [deployment/coolify.md](deployment/coolify.md): container deployment notes for the backend-only stack
- [guides/transfer-call.md](guides/transfer-call.md): SIP transfer troubleshooting

## Frontend Rule

This repo is backend-only. There is no bundled dashboard to open.

To create the frontend:

1. Start the backend.
2. Verify `/health` works.
3. Verify `/api/setup/status` works.
4. Copy the full prompt from [ui-agent-prompt.md](ui-agent-prompt.md).
5. Paste it into a coding agent.
6. Tell the agent: `Use this prompt to build the actual frontend application now. Do not just explain the instructions. Create the files, install the packages, and make it runnable.`
7. Tell it to use Vite on port `5173`.
8. Point the generated frontend to the backend API URL with `VITE_API_BASE_URL`.

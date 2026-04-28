# Docs

- [backend-contract.md](backend-contract.md): retained backend routes and expected payload shapes
- [ui-agent-prompt.md](ui-agent-prompt.md): Vite prompt for generating a frontend against this backend
- [setup/supabase.md](setup/supabase.md): fresh install and upgrade SQL order
- [deployment/coolify.md](deployment/coolify.md): container deployment notes for the backend-only stack
- [guides/transfer-call.md](guides/transfer-call.md): SIP transfer troubleshooting

## Frontend Rule

This repo is backend-only. There is no bundled dashboard to open.

To create the frontend:

1. Start the backend.
2. Verify `/health` works.
3. Copy the full prompt from [ui-agent-prompt.md](ui-agent-prompt.md).
4. Paste it into a coding agent.
5. Tell the agent: `Use this prompt to build the actual frontend application now. Do not just explain the instructions. Create the files, install the packages, and make it runnable.`
6. Tell it to use Vite on port `5173`.
7. Point the generated frontend to the backend API URL with `VITE_API_BASE_URL`.

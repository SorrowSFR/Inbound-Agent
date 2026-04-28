# Coolify Deployment

This branch deploys a single backend-only container with three processes under Supervisor:

- `backend_api`
- `agent`
- `kb_worker`

## Environment

Coolify should provide `HOST` and `PORT`. You still need to set:

- LiveKit credentials
- Google API key
- Supabase URL and key
- optional Telegram and S3 recording settings

## Persistent storage

Mount `/app/data` if you want these to survive redeploys:

- `data/config.json`
- local KB files and indexes

## Health

Use `GET /health` for the container healthcheck target.

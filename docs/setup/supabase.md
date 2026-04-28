# Supabase Setup

## Fresh install

Run these files in order:

1. `sql/supabase/setup.sql`
2. `sql/supabase/migration_v2.sql`
3. `sql/supabase/migration_v3.sql`
4. `sql/supabase/migration_v4_voice_metrics.sql`
5. `sql/supabase/migration_v5_kb.sql`

## Legacy upgrade

If the deployment previously used the WhatsApp/demo/dashboard branch, run this after the files above:

6. `sql/supabase/migration_v6_backend_cleanup.sql`

## Tables retained in this branch

- `call_logs`
- `call_transcripts`
- `active_calls`
- `appointments`
- `call_turn_metrics`
- KB tables from `migration_v5_kb.sql`

## Tables removed by cleanup

- `demo_links`
- `message_assets`
- `automation_jobs`
- `wa_conversations`
- `wa_templates`
- `wa_messages`
- `wa_events`

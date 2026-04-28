-- Cleanup migration for legacy WhatsApp, demo-link, and automation tables.
-- Run this on existing deployments after moving to the backend-only branch.

DROP TABLE IF EXISTS demo_links CASCADE;
DROP TABLE IF EXISTS message_assets CASCADE;
DROP TABLE IF EXISTS automation_jobs CASCADE;
DROP TABLE IF EXISTS wa_events CASCADE;
DROP TABLE IF EXISTS wa_messages CASCADE;
DROP TABLE IF EXISTS wa_templates CASCADE;
DROP TABLE IF EXISTS wa_conversations CASCADE;

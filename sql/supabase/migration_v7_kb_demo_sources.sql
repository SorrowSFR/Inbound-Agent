-- Limit knowledge-base ingestion to PDF uploads and website URLs.
-- Run this on existing deployments before shipping the demo-agent KB flow.

DELETE FROM kb_ingest_jobs
WHERE source_type NOT IN ('pdf_upload', 'web_url');

DELETE FROM kb_sources
WHERE source_type NOT IN ('pdf_upload', 'web_url');

ALTER TABLE kb_sources
DROP CONSTRAINT IF EXISTS kb_sources_type_check;

ALTER TABLE kb_sources
ADD CONSTRAINT kb_sources_type_check CHECK (source_type IN ('pdf_upload', 'web_url'));

DROP TABLE IF EXISTS kb_structured_entities CASCADE;

ALTER TABLE appointments
ALTER COLUMN title SET DEFAULT 'Appointment';

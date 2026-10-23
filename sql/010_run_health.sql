-- Additive metadata only. Existing workers keep working without health reports.
SET lock_timeout = '5s';
SET statement_timeout = '30s';
ALTER TABLE ecc_public.runs ADD COLUMN IF NOT EXISTS last_ping_at timestamptz;
ALTER TABLE ecc_public.runs ADD COLUMN IF NOT EXISTS health jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE ecc_public.dp_runs ADD COLUMN IF NOT EXISTS last_ping_at timestamptz;
ALTER TABLE ecc_public.dp_runs ADD COLUMN IF NOT EXISTS health jsonb NOT NULL DEFAULT '{}'::jsonb;
RESET statement_timeout;
RESET lock_timeout;

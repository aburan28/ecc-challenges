-- Small metadata-only change; never backfill point activity as a heartbeat.
SET lock_timeout = '5s';
SET statement_timeout = '30s';
ALTER TABLE ecc_public.api_keys ADD COLUMN IF NOT EXISTS last_ping_at timestamptz;
RESET statement_timeout;
RESET lock_timeout;

-- Voluntary retirement is separate from security suspension. Retained batches
-- and immutable witnesses remain available for verification and recovery.
BEGIN;
ALTER TABLE ecc_public.dp_runs ADD COLUMN IF NOT EXISTS closed_at timestamptz;
COMMIT;

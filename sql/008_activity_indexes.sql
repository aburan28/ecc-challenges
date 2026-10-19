-- Recent-activity reads for the status page (points per hour) and the
-- dashboard (each runner's latest submissions). v2 batches are already served
-- by dp_batches_namespace_state and dp_batches_run_offset.
CREATE INDEX IF NOT EXISTS batches_created ON ecc_public.batches(created_at);
CREATE INDEX IF NOT EXISTS batches_run_created ON ecc_public.batches(run_id, created_at);

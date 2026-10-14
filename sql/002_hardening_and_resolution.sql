BEGIN;
ALTER TABLE ecc_public.batches ADD COLUMN IF NOT EXISTS pressure_released_at timestamptz;
ALTER TABLE ecc_public.batches DROP CONSTRAINT IF EXISTS batches_audit_state_check;
ALTER TABLE ecc_public.batches ADD CONSTRAINT batches_audit_state_check
  CHECK (audit_state IN ('pending','sampled','not_selected','failed','unverified','quarantined'));
ALTER TABLE ecc_public.candidate_points ADD COLUMN IF NOT EXISTS disqualified_at timestamptz;
ALTER TABLE ecc_public.collision_leads ADD COLUMN IF NOT EXISTS quarantined_at timestamptz;
ALTER TABLE ecc_public.collision_leads ADD COLUMN IF NOT EXISTS prior_seed_bytes bytea;
ALTER TABLE ecc_public.collision_leads ADD COLUMN IF NOT EXISTS prior_batch bigint REFERENCES ecc_public.batches(id);
ALTER TABLE ecc_public.collision_leads ADD COLUMN IF NOT EXISTS resolve_enqueued_at timestamptz;
ALTER TABLE ecc_public.collision_leads ADD COLUMN IF NOT EXISTS resolve_started_at timestamptz;
ALTER TABLE ecc_public.collision_leads ADD COLUMN IF NOT EXISTS resolve_lease_until timestamptz;
ALTER TABLE ecc_public.collision_leads ADD COLUMN IF NOT EXISTS resolve_finished_at timestamptz;
ALTER TABLE ecc_public.collision_leads ADD COLUMN IF NOT EXISTS result_key text;
ALTER TABLE ecc_public.collision_leads DROP CONSTRAINT IF EXISTS collision_leads_batch_id_record_index_prior_source_key;
CREATE INDEX IF NOT EXISTS collision_leads_batch ON ecc_public.collision_leads(batch_id);
CREATE INDEX IF NOT EXISTS collision_leads_outbox ON ecc_public.collision_leads(id)
  WHERE replay_state='pending' AND resolve_enqueued_at IS NULL AND quarantined_at IS NULL;
COMMIT;

-- A reporting threshold is an immutable property of a legacy sigma run.
-- Existing runs used DP32. Unknown historical thresholds stay unknown;
-- their resolver must reach the stored endpoint rather than assume DP32.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
ALTER TABLE ecc_public.runs ADD COLUMN IF NOT EXISTS dp_weight smallint NOT NULL DEFAULT 32
  CHECK (dp_weight IN (30,32,34));
ALTER TABLE public.distinguished_points ADD COLUMN IF NOT EXISTS source_dp_weight smallint;
-- Existing historical rows have NULL (unknown) thresholds. Enforce the rule
-- for new writes without scanning hundreds of millions of rows under the
-- migration's exclusive table lock.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conrelid='public.distinguished_points'::regclass
                   AND conname='distinguished_points_source_dp_weight_check') THEN
    ALTER TABLE public.distinguished_points
      ADD CONSTRAINT distinguished_points_source_dp_weight_check
      CHECK (source_dp_weight IN (30,32,34)) NOT VALID;
  END IF;
END $$;
ALTER TABLE ecc_public.historical_import_parts ADD COLUMN IF NOT EXISTS source_dp_weight smallint
  CHECK (source_dp_weight IN (30,32,34));
ALTER TABLE ecc_public.historical_import_parts ADD COLUMN IF NOT EXISTS walk_id text NOT NULL
  DEFAULT 'certicom-ecc2k130-frobenius-v1'
  CHECK (walk_id = 'certicom-ecc2k130-frobenius-v1');
CREATE TABLE IF NOT EXISTS ecc_public.sigma_continuations (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id bigint NOT NULL REFERENCES ecc_public.batches(id),
  record_index integer NOT NULL CHECK(record_index >= 0),
  target_weight smallint NOT NULL CHECK(target_weight IN (30,32)),
  state text NOT NULL DEFAULT 'pending'
    CHECK(state IN ('pending','running','complete','bounded','unverified','discarded')),
  lease_until timestamptz,
  lease_token uuid,
  receipt jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE(batch_id,record_index,target_weight)
);
CREATE INDEX IF NOT EXISTS sigma_continuations_pending ON ecc_public.sigma_continuations(id)
  WHERE state IN ('pending','running');
COMMIT;

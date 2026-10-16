-- Isolated public v2 protocol. S3 contains immutable witnesses and receipts;
-- these tables contain admission, work leases, cursors, and rebuildable indexes.
BEGIN;

CREATE TABLE IF NOT EXISTS ecc_public.dp_namespaces (
  id text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{64}$'),
  campaign_id text NOT NULL CHECK (length(campaign_id) BETWEEN 1 AND 80),
  manifest jsonb NOT NULL,
  record_bytes integer NOT NULL CHECK (record_bytes BETWEEN 32 AND 256),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id, record_bytes),
  CHECK (jsonb_typeof(manifest) = 'object'),
  CHECK (manifest @> jsonb_build_object('namespace_id',id,'campaign',campaign_id,
         'protocol',2,'codec',jsonb_build_object('record_bytes',record_bytes)))
);

CREATE TABLE IF NOT EXISTS ecc_public.dp_runs (
  id uuid PRIMARY KEY,
  namespace_id text NOT NULL REFERENCES ecc_public.dp_namespaces(id),
  account_id bigint NOT NULL REFERENCES ecc_public.accounts(github_id),
  key_id char(24) NOT NULL REFERENCES ecc_public.api_keys(id),
  registration_id uuid NOT NULL,
  cursor bigint NOT NULL DEFAULT 0 CHECK (cursor >= 0),
  total_records bigint NOT NULL DEFAULT 0 CHECK (total_records >= 0),
  total_bytes bigint NOT NULL DEFAULT 0 CHECK (total_bytes >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  suspended_at timestamptz,
  UNIQUE(key_id, registration_id),
  UNIQUE(id, namespace_id)
);
CREATE INDEX IF NOT EXISTS dp_runs_account
  ON ecc_public.dp_runs(account_id);
CREATE INDEX IF NOT EXISTS dp_runs_namespace
  ON ecc_public.dp_runs(namespace_id, last_seen_at);

CREATE TABLE IF NOT EXISTS ecc_public.dp_pressure (
  shard integer PRIMARY KEY CHECK (shard BETWEEN 0 AND 15),
  pending_batches integer NOT NULL DEFAULT 0 CHECK (pending_batches >= 0),
  pending_bytes bigint NOT NULL DEFAULT 0 CHECK (pending_bytes >= 0)
);
INSERT INTO ecc_public.dp_pressure(shard)
SELECT generate_series(0,15) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS ecc_public.dp_batches (
  id uuid PRIMARY KEY,
  run_id uuid NOT NULL,
  namespace_id text NOT NULL,
  record_bytes integer NOT NULL,
  byte_offset bigint NOT NULL CHECK (byte_offset >= 0),
  byte_length integer NOT NULL CHECK (byte_length BETWEEN 1 AND 1048576),
  records integer NOT NULL CHECK (records > 0),
  sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  object_key text NOT NULL,
  version_id text,
  manifest_key text,
  admission_key text,
  verified_key text,
  state text NOT NULL DEFAULT 'reserved'
    CHECK (state IN ('reserved','admitted','verified','indexed','failed','expired')),
  pressure_shard integer NOT NULL REFERENCES ecc_public.dp_pressure(shard),
  expires_at timestamptz NOT NULL DEFAULT now() + interval '15 minutes',
  created_at timestamptz NOT NULL DEFAULT now(),
  admitted_at timestamptz,
  queued_at timestamptz,
  progress_at timestamptz,
  indexed_at timestamptz,
  pressure_released_at timestamptz,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  processing_token uuid,
  verification_started_at timestamptz,
  verification_lease_until timestamptz,
  verifier_id text,
  verifier_sha256 text
    CHECK (verifier_sha256 IS NULL OR verifier_sha256 ~ '^[0-9a-f]{64}$'),
  verified_records integer NOT NULL DEFAULT 0
    CHECK (verified_records >= 0 AND verified_records <= records),
  validation_progress_key text,
  new_points integer NOT NULL DEFAULT 0
    CHECK (new_points >= 0 AND new_points <= records),
  deduplicated_points integer NOT NULL DEFAULT 0
    CHECK (deduplicated_points >= 0 AND deduplicated_points <= records),
  error text CHECK (error IS NULL OR length(error) <= 500),
  UNIQUE(id, namespace_id),
  FOREIGN KEY(run_id, namespace_id)
    REFERENCES ecc_public.dp_runs(id, namespace_id),
  FOREIGN KEY(namespace_id, record_bytes)
    REFERENCES ecc_public.dp_namespaces(id, record_bytes),
  CHECK (byte_offset % record_bytes = 0),
  CHECK (byte_length = records * record_bytes),
  CHECK (new_points + deduplicated_points <= records),
  CHECK (state NOT IN ('admitted','verified','indexed','failed')
         OR (version_id IS NOT NULL AND manifest_key IS NOT NULL)),
  CHECK (state NOT IN ('verified','indexed')
         OR (verified_key IS NOT NULL AND verified_records = records)),
  CHECK (state <> 'indexed'
         OR (indexed_at IS NOT NULL AND pressure_released_at IS NOT NULL)),
  CHECK (state NOT IN ('expired','failed')
         OR pressure_released_at IS NOT NULL),
  CHECK (state <> 'failed' OR error IS NOT NULL)
);
-- Retain expired reservations while allowing a fresh intent at that cursor.
CREATE UNIQUE INDEX IF NOT EXISTS dp_batches_run_offset
  ON ecc_public.dp_batches(run_id, byte_offset) WHERE state <> 'expired';
CREATE INDEX IF NOT EXISTS dp_batches_expiry
  ON ecc_public.dp_batches(expires_at) WHERE state='reserved';
CREATE INDEX IF NOT EXISTS dp_batches_admission_repair
  ON ecc_public.dp_batches(created_at)
  WHERE state='admitted' AND admission_key IS NULL;
CREATE INDEX IF NOT EXISTS dp_batches_outbox
  ON ecc_public.dp_batches(created_at)
  WHERE state='admitted' AND admission_key IS NOT NULL AND queued_at IS NULL;
CREATE INDEX IF NOT EXISTS dp_batches_lease
  ON ecc_public.dp_batches(verification_lease_until, progress_at)
  WHERE state IN ('admitted','verified');
CREATE INDEX IF NOT EXISTS dp_batches_namespace_state
  ON ecc_public.dp_batches(namespace_id, state, created_at);
CREATE INDEX IF NOT EXISTS dp_batches_run_pending
  ON ecc_public.dp_batches(run_id) WHERE state IN ('reserved','admitted','verified');

CREATE TABLE IF NOT EXISTS ecc_public.dp_collision_leads (
  id text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{64}$'),
  namespace_id text NOT NULL REFERENCES ecc_public.dp_namespaces(id),
  point_key bytea NOT NULL CHECK (octet_length(point_key) BETWEEN 1 AND 128),
  first_batch uuid NOT NULL,
  first_index integer NOT NULL CHECK (first_index >= 0),
  second_batch uuid NOT NULL,
  second_index integer NOT NULL CHECK (second_index >= 0),
  first_witness bytea NOT NULL CHECK (octet_length(first_witness) BETWEEN 1 AND 128),
  second_witness bytea NOT NULL CHECK (octet_length(second_witness) BETWEEN 1 AND 128),
  state text NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','verified','degenerate','rejected')),
  result_key text,
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  FOREIGN KEY(first_batch,namespace_id) REFERENCES ecc_public.dp_batches(id,namespace_id),
  FOREIGN KEY(second_batch,namespace_id) REFERENCES ecc_public.dp_batches(id,namespace_id),
  CHECK (state='pending' OR result_key IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS dp_collision_leads_namespace
  ON ecc_public.dp_collision_leads(namespace_id, created_at);
CREATE INDEX IF NOT EXISTS dp_collision_leads_pending
  ON ecc_public.dp_collision_leads(created_at) WHERE state='pending';

-- Only first verification increments the cumulative verifier counter, even
-- when a new verifier revision rechecks a previously verified batch.
ALTER TABLE ecc_public.dp_batches
  ADD COLUMN IF NOT EXISTS verified_counted_at timestamptz;

CREATE TABLE IF NOT EXISTS ecc_public.dp_namespace_stats (
  namespace_id text NOT NULL REFERENCES ecc_public.dp_namespaces(id),
  shard integer NOT NULL CHECK (shard BETWEEN 0 AND 15),
  admitted_records bigint NOT NULL DEFAULT 0 CHECK (admitted_records >= 0),
  admitted_bytes bigint NOT NULL DEFAULT 0 CHECK (admitted_bytes >= 0),
  verified_records bigint NOT NULL DEFAULT 0 CHECK (verified_records >= 0),
  indexed_records bigint NOT NULL DEFAULT 0 CHECK (indexed_records >= 0),
  unique_points bigint NOT NULL DEFAULT 0 CHECK (unique_points >= 0),
  deduplicated_points bigint NOT NULL DEFAULT 0 CHECK (deduplicated_points >= 0),
  PRIMARY KEY(namespace_id,shard),
  CHECK (verified_records <= admitted_records),
  CHECK (indexed_records <= verified_records),
  CHECK (unique_points + deduplicated_points = indexed_records)
);

-- Initial backfill occurs before enabling v2 writers with this release.
-- Existing counter rows are preserved on subsequent bootstrap runs.
UPDATE ecc_public.dp_batches
SET verified_counted_at=coalesce(indexed_at,progress_at,admitted_at,created_at)
WHERE state IN ('verified','indexed') AND verified_counted_at IS NULL;
INSERT INTO ecc_public.dp_namespace_stats
  (namespace_id,shard,admitted_records,admitted_bytes,verified_records,
   indexed_records,unique_points,deduplicated_points)
SELECT namespace_id,pressure_shard,
  coalesce(sum(records) FILTER (WHERE state NOT IN ('reserved','expired')),0),
  coalesce(sum(byte_length) FILTER (WHERE state NOT IN ('reserved','expired')),0),
  coalesce(sum(records) FILTER (WHERE verified_counted_at IS NOT NULL),0),
  coalesce(sum(records) FILTER (WHERE state='indexed'),0),
  coalesce(sum(new_points) FILTER (WHERE state='indexed'),0),
  coalesce(sum(deduplicated_points) FILTER (WHERE state='indexed'),0)
FROM ecc_public.dp_batches GROUP BY namespace_id,pressure_shard
ON CONFLICT(namespace_id,shard) DO NOTHING;

CREATE TABLE IF NOT EXISTS ecc_public.dp_index_bindings (
  singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
  table_id uuid NOT NULL,
  table_name text NOT NULL CHECK(length(table_name) BETWEEN 3 AND 255),
  table_arn text NOT NULL,
  bound_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

COMMIT;

-- Apply with the existing rho-dp database owner, in a transaction.
-- The legacy campaign tables and report_dp function are not modified.
BEGIN;
CREATE SCHEMA IF NOT EXISTS ecc_public;

CREATE TABLE IF NOT EXISTS ecc_public.accounts (
  github_id bigint PRIMARY KEY,
  login text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  suspended_at timestamptz
);

CREATE TABLE IF NOT EXISTS ecc_public.api_keys (
  id char(24) PRIMARY KEY,
  account_id bigint NOT NULL REFERENCES ecc_public.accounts(github_id),
  label text NOT NULL CHECK (length(label) BETWEEN 1 AND 80),
  digest bytea NOT NULL CHECK (octet_length(digest) = 32),
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  last_used_at timestamptz,
  tokens numeric(10,3) NOT NULL DEFAULT 240,
  token_updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_keys_account ON ecc_public.api_keys(account_id);

CREATE SEQUENCE IF NOT EXISTS ecc_public.run_id_seq AS integer MINVALUE 50000 MAXVALUE 65534 START WITH 50000 NO CYCLE;

CREATE TABLE IF NOT EXISTS ecc_public.runs (
  run_id integer PRIMARY KEY CHECK (run_id BETWEEN 1 AND 65534),
  account_id bigint NOT NULL REFERENCES ecc_public.accounts(github_id),
  key_id char(24) NOT NULL REFERENCES ecc_public.api_keys(id),
  registration_id uuid NOT NULL,
  cursor bigint NOT NULL DEFAULT 0 CHECK (cursor >= 0 AND cursor % 32 = 0),
  total_bytes bigint NOT NULL DEFAULT 0 CHECK (total_bytes >= 0),
  checkpoint_at bigint,
  checkpoint_key text,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  trusted_at timestamptz,
  suspended_at timestamptz,
  UNIQUE(key_id, registration_id)
);
CREATE INDEX IF NOT EXISTS runs_account ON ecc_public.runs(account_id);

CREATE TABLE IF NOT EXISTS ecc_public.key_usage_day (
  key_id char(24) NOT NULL REFERENCES ecc_public.api_keys(id),
  day date NOT NULL,
  bytes bigint NOT NULL CHECK (bytes >= 0),
  PRIMARY KEY(key_id, day)
);

CREATE TABLE IF NOT EXISTS ecc_public.pressure_shards (
  shard integer PRIMARY KEY CHECK (shard BETWEEN 0 AND 15),
  pending_batches integer NOT NULL DEFAULT 0 CHECK (pending_batches >= 0),
  pending_bytes bigint NOT NULL DEFAULT 0 CHECK (pending_bytes >= 0)
);
INSERT INTO ecc_public.pressure_shards(shard)
SELECT generate_series(0,15) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS ecc_public.batches (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id integer NOT NULL REFERENCES ecc_public.runs(run_id),
  byte_offset bigint NOT NULL CHECK (byte_offset >= 0 AND byte_offset % 32 = 0),
  byte_length integer NOT NULL CHECK (byte_length > 0 AND byte_length <= 1048576 AND byte_length % 32 = 0),
  records integer NOT NULL CHECK (records = byte_length / 32),
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  object_key text NOT NULL UNIQUE,
  audit_state text NOT NULL DEFAULT 'pending' CHECK (audit_state IN ('pending','sampled','not_selected','failed','unverified','quarantined')),
  audit_indices jsonb,
  audit_source_sha256 char(64),
  audit_started_at timestamptz,
  audit_finished_at timestamptz,
  audit_enqueued_at timestamptz,
  index_enqueued_at timestamptz,
  indexed_at timestamptz,
  pressure_released_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id, byte_offset)
);
CREATE INDEX IF NOT EXISTS batches_audit_outbox ON ecc_public.batches(id) WHERE audit_enqueued_at IS NULL;
CREATE INDEX IF NOT EXISTS batches_index_outbox ON ecc_public.batches(id) WHERE audit_state='sampled' AND index_enqueued_at IS NULL;
CREATE INDEX IF NOT EXISTS batches_pending_age ON ecc_public.batches(created_at) WHERE audit_state='pending';

CREATE TABLE IF NOT EXISTS ecc_public.audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id bigint NOT NULL REFERENCES ecc_public.batches(id),
  record_index integer NOT NULL,
  record_sha256 char(64) NOT NULL,
  verifier_sha256 char(64) NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('pass','mismatch','timeout','error')),
  elapsed_ms bigint NOT NULL CHECK (elapsed_ms >= 0),
  recorded_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(batch_id, record_index, verifier_sha256)
);

-- Candidate keys are separate from the authoritative campaign's table.
CREATE TABLE IF NOT EXISTS ecc_public.candidate_points (
  campaign_id text NOT NULL CHECK (campaign_id = 'ecc2k-130'),
  point_key bytea NOT NULL CHECK (octet_length(point_key) = 24),
  first_seed numeric(20,0) NOT NULL,
  first_run integer NOT NULL,
  first_batch bigint NOT NULL REFERENCES ecc_public.batches(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  disqualified_at timestamptz,
  PRIMARY KEY(campaign_id, point_key)
) PARTITION BY HASH (point_key);
DO $$
BEGIN
  FOR i IN 0..31 LOOP
    EXECUTE format('CREATE TABLE IF NOT EXISTS ecc_public.candidate_points_p%s PARTITION OF ecc_public.candidate_points FOR VALUES WITH (MODULUS 32, REMAINDER %s)', lpad(i::text,2,'0'), i);
  END LOOP;
END $$;

CREATE TABLE IF NOT EXISTS ecc_public.collision_leads (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id bigint NOT NULL REFERENCES ecc_public.batches(id),
  record_index integer NOT NULL,
  run_id integer NOT NULL,
  seed numeric(20,0) NOT NULL,
  point_key bytea NOT NULL CHECK (octet_length(point_key)=24),
  prior_source text NOT NULL CHECK (prior_source IN ('public','campaign')),
  prior_seed numeric(20,0),
  prior_seed_bytes bytea,
  prior_batch bigint REFERENCES ecc_public.batches(id),
  prior_worker text,
  replay_state text NOT NULL DEFAULT 'pending' CHECK (replay_state IN ('pending','verified','rejected','unverified')),
  resolve_enqueued_at timestamptz,
  resolve_started_at timestamptz,
  resolve_lease_until timestamptz,
  resolve_finished_at timestamptz,
  result_key text,
  quarantined_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS collision_leads_batch ON ecc_public.collision_leads(batch_id);

CREATE TABLE IF NOT EXISTS ecc_public.indexed_batches (
  batch_id bigint PRIMARY KEY REFERENCES ecc_public.batches(id),
  records integer NOT NULL,
  candidate_new integer NOT NULL,
  leads integer NOT NULL,
  indexed_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;

-- Compact point index: 16-byte fingerprint entries in sorted S3 part files,
-- merged in generations. Raw batches stay the authority; the index is derived
-- and can be rebuilt from them. Used when POINT_INDEX_MODE=compact.
BEGIN;
CREATE SEQUENCE IF NOT EXISTS ecc_public.dp_index_seq
  AS bigint MINVALUE 1 MAXVALUE 1099511627775 NO CYCLE;

ALTER TABLE ecc_public.dp_batches
  ADD COLUMN IF NOT EXISTS index_seq bigint
    CHECK (index_seq IS NULL OR index_seq BETWEEN 1 AND 1099511627775),
  ADD COLUMN IF NOT EXISTS index_generation bigint
    CHECK (index_generation IS NULL OR index_generation >= 1),
  -- Cross-batch duplicates found by the merge, already moved from new_points
  -- to deduplicated_points. Stored so a rebuild adjusts by the difference.
  ADD COLUMN IF NOT EXISTS index_duplicates integer NOT NULL DEFAULT 0
    CHECK (index_duplicates >= 0);

DO $$ BEGIN
  ALTER TABLE ecc_public.dp_batches ADD CONSTRAINT dp_batches_index_generation_seq
    CHECK (index_generation IS NULL OR index_seq IS NOT NULL);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS dp_batches_index_seq
  ON ecc_public.dp_batches(index_seq) WHERE index_seq IS NOT NULL;
CREATE INDEX IF NOT EXISTS dp_batches_index_pending
  ON ecc_public.dp_batches(namespace_id, index_seq)
  WHERE state='indexed' AND index_seq IS NOT NULL AND index_generation IS NULL;

CREATE TABLE IF NOT EXISTS ecc_public.dp_point_index (
  namespace_id text PRIMARY KEY REFERENCES ecc_public.dp_namespaces(id),
  generation bigint NOT NULL CHECK (generation >= 1),
  manifest_key text NOT NULL CHECK (length(manifest_key) BETWEEN 1 AND 1024),
  entries bigint NOT NULL CHECK (entries >= 0),
  updated_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;

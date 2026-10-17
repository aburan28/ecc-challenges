-- Ledger for the one-way import of the original campaign's distinguished
-- points into public.distinguished_points. The original account is never
-- reached from here: an operator exports its S3 corpus into checksum-bound
-- parts, and scripts/historical_import.py loads those parts in this database.
BEGIN;

-- One row per loaded source object, written in the transaction that inserts its points,
-- so a record of having loaded a part cannot outlive the points it describes.
CREATE TABLE IF NOT EXISTS ecc_public.historical_import_parts (
  source_key text PRIMARY KEY,
  part_sha256 char(64) NOT NULL CHECK (part_sha256 ~ '^[0-9a-f]{64}$'),
  source_version text,
  records bigint NOT NULL CHECK (records >= 0),
  inserted bigint NOT NULL CHECK (inserted >= 0),
  conflicts bigint NOT NULL CHECK (conflicts >= 0),
  loaded_at timestamptz NOT NULL DEFAULT now()
);

-- Two historical walks that reached one point from different seeds. The point
-- table keeps the first; every other witness is kept here, never dropped,
-- because each one is a collision in the original campaign.
CREATE TABLE IF NOT EXISTS ecc_public.historical_conflicts (
  point_key bytea NOT NULL CHECK (octet_length(point_key) = 24),
  walk_seed bytea NOT NULL,
  stored_seed bytea,
  source_key text NOT NULL,
  found_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (point_key, walk_seed)
);

COMMIT;

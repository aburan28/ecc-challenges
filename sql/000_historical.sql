-- In the isolated account this table starts empty. It reserves the four
-- columns needed for a later historical import. Public-to-public matching
-- works independently in ecc_public.candidate_points. This migration runs
-- only in the dedicated public-platform database.
CREATE TABLE IF NOT EXISTS public.distinguished_points (
  campaign_id text NOT NULL,
  point_key bytea NOT NULL,
  walk_seed bytea,
  worker_id text,
  PRIMARY KEY (campaign_id, point_key)
);

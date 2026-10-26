-- Contributors choose whether their GitHub login appears on the public
-- leaderboard. Hidden is the default; hidden accounts are counted only in
-- the leaderboard's aggregate row.
BEGIN;
ALTER TABLE ecc_public.accounts
  ADD COLUMN IF NOT EXISTS leaderboard_visible boolean NOT NULL DEFAULT false;
COMMIT;

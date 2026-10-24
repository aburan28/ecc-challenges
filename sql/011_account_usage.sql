-- Preserve the aggregate upload ceiling implied by the former three-key cap.
BEGIN;
CREATE TABLE IF NOT EXISTS ecc_public.account_usage_day (
  account_id bigint NOT NULL REFERENCES ecc_public.accounts(github_id),
  day date NOT NULL,
  bytes bigint NOT NULL CHECK (bytes >= 0),
  PRIMARY KEY(account_id, day)
);
INSERT INTO ecc_public.account_usage_day(account_id,day,bytes)
SELECT k.account_id,u.day,sum(u.bytes)
FROM ecc_public.key_usage_day u
JOIN ecc_public.api_keys k ON k.id=u.key_id
GROUP BY k.account_id,u.day
ON CONFLICT(account_id,day) DO NOTHING;
COMMIT;

-- Totals of the historical import for the public status page. The web role
-- reads this view and nothing else of the import: the ledger's source keys
-- stay with the database owner, and a view runs with its owner's rights.
-- scripts/bootstrap_db.py grants SELECT on it to ecc_site_web.
CREATE OR REPLACE VIEW ecc_public.historical_import_totals AS
SELECT coalesce(sum(inserted), 0)::bigint AS points,
       max(loaded_at) AS last_loaded_at
FROM ecc_public.historical_import_parts;

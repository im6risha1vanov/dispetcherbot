-- Кто делал прошлую работу у этого клиента: по нему гарантия возвращается
-- тому же мастеру, а повтор отдаётся ему в приоритете.
-- Применение: psql "$DATABASE_URL" -f migrations/013_prior_master.sql

ALTER TABLE requests ADD COLUMN IF NOT EXISTS prior_master_name TEXT;

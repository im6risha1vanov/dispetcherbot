-- Приезд и начало работы — разные события: мастер сначала приезжает и
-- осматривает, и только потом приступает. В CRM статус меняется на втором.
-- Применение: psql "$DATABASE_URL" -f migrations/010_inwork.sql

ALTER TABLE assignments ADD COLUMN IF NOT EXISTS inwork_at TIMESTAMPTZ;

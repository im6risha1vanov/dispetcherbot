-- Открытие СД: мастер пишет один «комментарий филиала» вместо отдельных
-- срока, неисправности и сумм. Старые колонки оставляем для незавершённых отчётов.
-- Применение: psql "$DATABASE_URL" -f migrations/017_branch_comment.sql

ALTER TABLE closures ADD COLUMN IF NOT EXISTS branch_comment TEXT;

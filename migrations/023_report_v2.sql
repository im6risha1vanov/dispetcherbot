-- Отчёт мастера v2: предоплата, повтор проведения, полный сброс при отказе.
--
-- Порядок вопросов изменился (сначала документы, суммы в конце), поэтому
-- отчёт, застигнутый выкладкой на середине, начинаем заново: продолжать с
-- прежнего шага — значит спросить не то и не в том порядке.
--
-- Применение: psql "$DATABASE_URL" -f migrations/023_report_v2.sql

-- Предоплата как деньги. Текстовая prepayment занята старыми ответами по СД.
ALTER TABLE closures ADD COLUMN IF NOT EXISTS prepayment_sum NUMERIC(12, 2);

-- Почему CRM не приняла закрытие: администратор должен видеть причину,
-- а не «что-то пошло не так».
ALTER TABLE closures ADD COLUMN IF NOT EXISTS conduct_error TEXT;
ALTER TABLE closures ADD COLUMN IF NOT EXISTS conduct_attempts INTEGER NOT NULL DEFAULT 0;

-- Сообщение с текущим вопросом: на фото-шагах бот дописывает в него счётчик
-- принятых снимков, вместо того чтобы плодить «Принято» на каждое фото.
ALTER TABLE closures ADD COLUMN IF NOT EXISTS question_message_id BIGINT;

-- Отклонённый отчёт уходит из активных совсем: мастер проходит анкету с нуля,
-- а прежние фото остаются в базе историей и в CRM не попадают. Прежнее
-- состояние 'rejected' держало отчёт активным и переиспользовало его фото —
-- такие строки закрываем, мастер начнёт заново кнопкой «Отчёт».
UPDATE closures SET state = 'discarded' WHERE state = 'rejected';

DROP INDEX IF EXISTS closures_active_master_crm_idx;

CREATE UNIQUE INDEX IF NOT EXISTS closures_active_master_crm_idx
    ON closures (employee_id, crm_id)
    WHERE state IN ('collecting', 'pending_admin', 'approved');

UPDATE closures
   SET step = 'docs_photo'
 WHERE state = 'collecting'
   AND kind = 'close'
   AND step IN ('payed', 'spares', 'spare_photo', 'bso', 'bso_photo');

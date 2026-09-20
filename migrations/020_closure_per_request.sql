-- Незавершённый отчёт — один на пару (мастер, заявка), а не на мастера целиком.
-- СД на заявке A не должна блокировать закрытие заявки B: мастер на СД свободен
-- для новой работы (как occupancy по статусу «В работе СД»).
-- Применение: psql "$DATABASE_URL" -f migrations/020_closure_per_request.sql

DROP INDEX IF EXISTS closures_active_master_idx;

CREATE UNIQUE INDEX IF NOT EXISTS closures_active_master_crm_idx
    ON closures (employee_id, crm_id)
    WHERE state IN ('collecting', 'pending_admin', 'rejected');

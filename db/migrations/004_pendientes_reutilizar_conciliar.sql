-- Pagos robustos: reutilizar un pago PENDING reciente (doble clic en «pagar») y conciliar los PENDING viejos.
-- Idempotente. webpay_url y cliente permiten devolver el mismo formulario de Webpay sin crear otra transacción;
-- el índice parcial acelera ambas búsquedas (solo hay pocas filas PENDING).
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-payments-schema', 0));

ALTER TABLE transaccion_pago ADD COLUMN IF NOT EXISTS webpay_url text;
ALTER TABLE transaccion_pago ADD COLUMN IF NOT EXISTS cliente varchar(3) NOT NULL DEFAULT 'app';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'transaccion_pago_cliente_check') THEN
        ALTER TABLE transaccion_pago ADD CONSTRAINT transaccion_pago_cliente_check CHECK (cliente IN ('app', 'web'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS transaccion_pago_pending_idx ON transaccion_pago (fecha_creacion) WHERE status = 'PENDING';
COMMIT;

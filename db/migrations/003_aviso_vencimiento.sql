-- HU-55: avisos de vencimiento (3 días antes y el día del vencimiento).
-- Outbox propio: el worker de ms-pago escribe aquí y el gateway lo lee por los endpoints internos
-- /internal/v1/avisos/* (con INTERNAL_SERVICE_TOKEN), lo publica en /notifications y por push, y
-- confirma. Un aviso por suscripción, tipo y fecha_fin: si la suscripción se renueva cambia su
-- fecha_fin y el nuevo período puede volver a avisarse; el aviso del período anterior se descarta.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-payments-schema', 0));

CREATE TABLE IF NOT EXISTS aviso_pendiente (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    suscripcion_id uuid NOT NULL REFERENCES suscripcion (id) ON DELETE CASCADE,
    tipo varchar(10) NOT NULL CHECK (tipo IN ('PREVIO', 'HOY')),
    fecha_fin timestamptz NOT NULL,
    -- Cuenta que recibe el aviso: el deportista, o quien pagó el último período del club.
    user_id uuid NOT NULL,
    plan_id integer NOT NULL REFERENCES plan_suscripcion (id),
    kind text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    -- true: no se publicó porque la suscripción se renovó o venció antes de que el gateway lo leyera.
    descartado boolean NOT NULL DEFAULT false,
    CONSTRAINT aviso_pendiente_unico UNIQUE (suscripcion_id, tipo, fecha_fin)
);
CREATE INDEX IF NOT EXISTS aviso_pendiente_por_publicar
    ON aviso_pendiente (id) WHERE published_at IS NULL;
CREATE INDEX IF NOT EXISTS suscripcion_fecha_fin_idx ON suscripcion (fecha_fin);
COMMIT;

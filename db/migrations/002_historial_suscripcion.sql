-- HU-44: conserva cada período comprado y reconstruye las aprobaciones anteriores.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-payments-schema', 0));

CREATE TABLE IF NOT EXISTS suscripcion_periodo (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id uuid,
    club_id uuid,
    plan_id integer NOT NULL REFERENCES plan_suscripcion(id),
    transaccion_id uuid NOT NULL UNIQUE REFERENCES transaccion_pago(id),
    plan_nombre varchar(60) NOT NULL,
    precio_pagado integer NOT NULL CHECK (precio_pagado > 0),
    duracion_dias integer NOT NULL CHECK (duracion_dias BETWEEN 1 AND 366),
    beneficios jsonb NOT NULL CHECK (jsonb_typeof(beneficios) = 'array'),
    fecha_inicio timestamptz NOT NULL,
    fecha_fin timestamptz NOT NULL,
    fecha_creacion timestamptz NOT NULL DEFAULT now(),
    CHECK ((usuario_id IS NULL) <> (club_id IS NULL)),
    CHECK (fecha_fin > fecha_inicio)
);
CREATE INDEX IF NOT EXISTS periodo_usuario_idx ON suscripcion_periodo(usuario_id, fecha_inicio DESC);
CREATE INDEX IF NOT EXISTS periodo_club_idx ON suscripcion_periodo(club_id, fecha_inicio DESC);
CREATE INDEX IF NOT EXISTS pago_usuario_historial_idx ON transaccion_pago(usuario_id, fecha_creacion DESC, id);
CREATE INDEX IF NOT EXISTS pago_club_historial_idx ON transaccion_pago(club_id, fecha_creacion DESC, id);

-- La aprobación se persistía en fecha_actualizacion; transaction_date es la fecha
-- informada por Webpay, que puede anteceder a la confirmación local.
WITH RECURSIVE ordenadas AS (
    SELECT t.*, COALESCE(fecha_actualizacion, transaction_date, fecha_creacion) AS aprobada,
           CASE WHEN usuario_id IS NOT NULL THEN 'usuario:' || usuario_id::text
                ELSE 'club:' || club_id::text END AS dueno,
           row_number() OVER (
               PARTITION BY usuario_id, club_id
               ORDER BY COALESCE(fecha_actualizacion, transaction_date, fecha_creacion), fecha_creacion, id
           ) AS numero
    FROM transaccion_pago t WHERE status = 'APPROVED'
), periodos AS (
    SELECT id, dueno, numero, aprobada AS inicio,
           aprobada + make_interval(days => duracion_dias) AS fin
    FROM ordenadas WHERE numero = 1
    UNION ALL
    SELECT t.id, t.dueno, t.numero, GREATEST(t.aprobada, p.fin),
           GREATEST(t.aprobada, p.fin) + make_interval(days => t.duracion_dias)
    FROM ordenadas t JOIN periodos p ON t.dueno = p.dueno AND t.numero = p.numero + 1
)
INSERT INTO suscripcion_periodo
    (usuario_id, club_id, plan_id, transaccion_id, plan_nombre, precio_pagado,
     duracion_dias, beneficios, fecha_inicio, fecha_fin, fecha_creacion)
SELECT t.usuario_id, t.club_id, t.plan_id, t.id, t.plan_nombre, t.amount,
       t.duracion_dias, t.beneficios, p.inicio, p.fin, t.aprobada
FROM periodos p JOIN ordenadas t ON t.id = p.id
ON CONFLICT (transaccion_id) DO NOTHING;
COMMIT;

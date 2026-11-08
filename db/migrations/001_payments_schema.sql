-- Final payments schema, equivalent to clubs migrations 006/012/013/014.
-- Apply to an EMPTY sportmatch_payments database; data transfer is a separate step.
-- All financial IDs and columns are retained. Club/account UUIDs are external references.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-payments-schema', 0));

CREATE TABLE IF NOT EXISTS plan_suscripcion (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre varchar(60) NOT NULL CHECK (length(btrim(nombre)) >= 3),
    tipo varchar(10) NOT NULL CHECK (tipo IN ('USUARIO', 'CLUB')),
    descripcion varchar(255),
    precio integer NOT NULL CONSTRAINT plan_suscripcion_precio_check CHECK (precio >= 0 AND precio <= 10000000),
    duracion_dias integer DEFAULT 30 CHECK (duracion_dias IS NULL OR duracion_dias BETWEEN 1 AND 366),
    is_active boolean NOT NULL DEFAULT true,
    fecha_creacion timestamptz NOT NULL DEFAULT now(),
    categoria varchar(10) NOT NULL DEFAULT 'PLAN' CHECK (categoria IN ('PLAN', 'CONVENIO')),
    beneficios jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(beneficios) = 'array' AND jsonb_array_length(beneficios) <= 10),
    institucion varchar(80) CHECK (institucion IS NULL OR (categoria = 'CONVENIO' AND length(btrim(institucion)) >= 3)),
    es_base boolean NOT NULL DEFAULT false CHECK (NOT es_base OR is_active),
    fecha_desactivacion timestamptz,
    motivo_desactivacion varchar(300) CHECK (motivo_desactivacion IS NULL OR length(btrim(motivo_desactivacion)) >= 3),
    CHECK (NOT is_active OR (fecha_desactivacion IS NULL AND motivo_desactivacion IS NULL)),
    CHECK (duracion_dias IS NOT NULL OR precio = 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS plan_suscripcion_nombre_activo_uq
    ON plan_suscripcion (lower(btrim(nombre))) WHERE is_active;
CREATE UNIQUE INDEX IF NOT EXISTS plan_suscripcion_base_uq
    ON plan_suscripcion (tipo) WHERE es_base;

CREATE TABLE IF NOT EXISTS transaccion_pago (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id uuid,
    club_id uuid,
    iniciada_por uuid NOT NULL,
    plan_id integer NOT NULL REFERENCES plan_suscripcion (id),
    buy_order varchar(26) NOT NULL UNIQUE,
    session_id varchar(61) NOT NULL,
    token_ws varchar(64) NOT NULL UNIQUE,
    amount integer NOT NULL CHECK (amount > 0),
    status varchar(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')),
    response_code integer,
    authorization_code varchar(6),
    payment_type_code varchar(3),
    card_last4 varchar(4),
    installments_number smallint,
    transaction_date timestamptz,
    fecha_creacion timestamptz NOT NULL DEFAULT now(),
    fecha_actualizacion timestamptz NOT NULL DEFAULT now(),
    plan_nombre varchar(60) NOT NULL,
    duracion_dias integer NOT NULL CHECK (duracion_dias BETWEEN 1 AND 366),
    beneficios jsonb NOT NULL CHECK (jsonb_typeof(beneficios) = 'array' AND jsonb_array_length(beneficios) <= 10),
    CHECK ((usuario_id IS NULL) <> (club_id IS NULL))
);
CREATE INDEX IF NOT EXISTS transaccion_pago_iniciada_idx
    ON transaccion_pago (iniciada_por, fecha_creacion DESC);

CREATE TABLE IF NOT EXISTS suscripcion (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id uuid,
    club_id uuid,
    plan_id integer NOT NULL REFERENCES plan_suscripcion (id),
    fecha_inicio timestamptz NOT NULL,
    fecha_fin timestamptz NOT NULL,
    ultima_transaccion_id uuid REFERENCES transaccion_pago (id),
    fecha_creacion timestamptz NOT NULL DEFAULT now(),
    fecha_actualizacion timestamptz NOT NULL DEFAULT now(),
    plan_nombre varchar(60) NOT NULL,
    precio_pagado integer NOT NULL CHECK (precio_pagado >= 0),
    duracion_dias integer NOT NULL CHECK (duracion_dias BETWEEN 1 AND 366),
    beneficios jsonb NOT NULL CHECK (jsonb_typeof(beneficios) = 'array' AND jsonb_array_length(beneficios) <= 10),
    CHECK ((usuario_id IS NULL) <> (club_id IS NULL)),
    CHECK (fecha_fin > fecha_inicio)
);
CREATE UNIQUE INDEX IF NOT EXISTS suscripcion_usuario_uq ON suscripcion (usuario_id) WHERE usuario_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS suscripcion_club_uq ON suscripcion (club_id) WHERE club_id IS NOT NULL;
COMMIT;

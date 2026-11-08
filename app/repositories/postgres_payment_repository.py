from __future__ import annotations
from typing import Any
from uuid import UUID
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from app.repositories.base import (StoredPlan, StoredTransaccion, StoredSuscripcion, PlanSuscripcion, PlanNombreDuplicadoError, PlanInactivoError, PlanActivoError, PlanBaseError)

def _plan_aliases(nombre: str, precio: str, duracion: str, beneficios: str) -> str:
    """The plan columns of a payment or a subscription: name, price, duration and benefits are the ones COPIED
    when it was paid (HU-56); the rest (id, tipo, categoria, descripcion, is_active) come from the plan."""
    return f"""
    p.id AS plan_id, {nombre} AS plan_nombre, p.tipo AS plan_tipo,
    p.descripcion AS plan_descripcion, {precio} AS plan_precio,
    {duracion} AS plan_duracion_dias, p.is_active AS plan_is_active,
    p.categoria AS plan_categoria, {beneficios} AS plan_beneficios
"""

_TRANSACCION_SELECT = f"""
    SELECT t.id, t.usuario_id, t.club_id, t.iniciada_por, t.buy_order, t.session_id,
           t.token_ws, t.amount, t.status, t.response_code, t.authorization_code,
           t.payment_type_code, t.card_last4, t.installments_number, t.transaction_date,
           t.fecha_creacion, t.fecha_actualizacion,
           {_plan_aliases("t.plan_nombre", "t.amount", "t.duracion_dias", "t.beneficios")}
    FROM transaccion_pago t
    JOIN plan_suscripcion p ON p.id = t.plan_id
"""

_SUSCRIPCION_SELECT = f"""
    SELECT s.id, s.usuario_id, s.club_id, s.fecha_inicio, s.fecha_fin,
           CASE WHEN s.fecha_fin > now() THEN 'ACTIVE' ELSE 'EXPIRED' END AS estado,
           {_plan_aliases("s.plan_nombre", "s.precio_pagado", "s.duracion_dias", "s.beneficios")}
    FROM suscripcion s
    JOIN plan_suscripcion p ON p.id = s.plan_id
"""

_SUSCRIPCION_UPSERT = """
    INSERT INTO suscripcion
        (usuario_id, club_id, plan_id, fecha_inicio, fecha_fin, ultima_transaccion_id,
         plan_nombre, precio_pagado, duracion_dias, beneficios)
    VALUES (%(usuario_id)s, %(club_id)s, %(plan_id)s, now(),
            now() + make_interval(days => %(dias)s), %(transaccion_id)s,
            %(plan_nombre)s, %(precio_pagado)s, %(dias)s, %(beneficios)s)
    ON CONFLICT ({column}) WHERE {column} IS NOT NULL DO UPDATE SET
        plan_id = EXCLUDED.plan_id,
        fecha_inicio = CASE WHEN suscripcion.fecha_fin > now()
                            THEN suscripcion.fecha_inicio ELSE now() END,
        fecha_fin = GREATEST(suscripcion.fecha_fin, now()) + make_interval(days => %(dias)s),
        ultima_transaccion_id = EXCLUDED.ultima_transaccion_id,
        plan_nombre = EXCLUDED.plan_nombre,
        precio_pagado = EXCLUDED.precio_pagado,
        duracion_dias = EXCLUDED.duracion_dias,
        beneficios = EXCLUDED.beneficios,
        fecha_actualizacion = now()
"""

def _plan(row: dict[str, Any]) -> StoredPlan:
    """A plan_suscripcion row (the jsonb beneficios arrives as a list)."""
    return StoredPlan(**{**row, "beneficios": tuple(row.get("beneficios") or ())})

def _split_plan(row: dict[str, Any]) -> StoredPlan:
    return StoredPlan(
        id=row.pop("plan_id"),
        nombre=row.pop("plan_nombre"),
        tipo=row.pop("plan_tipo"),
        descripcion=row.pop("plan_descripcion"),
        precio=row.pop("plan_precio"),
        duracion_dias=row.pop("plan_duracion_dias"),
        is_active=row.pop("plan_is_active"),
        categoria=row.pop("plan_categoria"),
        beneficios=tuple(row.pop("plan_beneficios") or ()),
    )

def _to_transaccion(row: dict[str, Any]) -> StoredTransaccion:
    plan = _split_plan(row)
    return StoredTransaccion(**row, plan=plan)

def _to_suscripcion(row: dict[str, Any]) -> StoredSuscripcion:
    plan = _split_plan(row)
    return StoredSuscripcion(**row, plan=plan)

class PostgresPaymentRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def close(self) -> None:
        self._pool.close()

    def list_planes(self, tipo: str | None = None) -> list[StoredPlan]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, nombre, tipo, descripcion, precio, duracion_dias, is_active, categoria, beneficios, institucion
                FROM plan_suscripcion
                WHERE is_active AND (%s::text IS NULL OR tipo = %s)
                ORDER BY precio, id
                """,
                (tipo, tipo),
            )
            return [_plan(row) for row in cur.fetchall()]

    def get_plan(self, plan_id: int) -> StoredPlan | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, nombre, tipo, descripcion, precio, duracion_dias, is_active, categoria, beneficios, institucion
                FROM plan_suscripcion WHERE id = %s AND is_active
                """,
                (plan_id,),
            )
            row = cur.fetchone()
        return _plan(row) if row is not None else None

    _PLAN_COLUMNS = ("id, nombre, tipo, descripcion, precio, duracion_dias, is_active, categoria, beneficios, "
                     "institucion, fecha_creacion, es_base, fecha_desactivacion, motivo_desactivacion")

    def get_plan_any(self, plan_id: int) -> StoredPlan | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {self._PLAN_COLUMNS} FROM plan_suscripcion WHERE id = %s", (plan_id,))
            row = cur.fetchone()
        return _plan(row) if row is not None else None

    def list_planes_admin(
        self, *, estado: str, tipo: str | None, categoria: str | None, limit: int, offset: int
    ) -> tuple[list[StoredPlan], int]:
        where = """(%(estado)s = 'todos' OR is_active = (%(estado)s = 'activo'))
                   AND (%(tipo)s::text IS NULL OR tipo = %(tipo)s)
                   AND (%(categoria)s::text IS NULL OR categoria = %(categoria)s)"""
        params = {"estado": estado, "tipo": tipo, "categoria": categoria, "limit": limit, "offset": offset}
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT count(*) AS total FROM plan_suscripcion WHERE {where}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"""
                SELECT {self._PLAN_COLUMNS},
                       (SELECT count(*) FROM suscripcion s
                        WHERE s.plan_id = plan_suscripcion.id AND s.fecha_fin > now()) AS suscripciones_vigentes
                FROM plan_suscripcion WHERE {where}
                ORDER BY fecha_creacion DESC, id DESC LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            return [_plan(row) for row in cur.fetchall()], total

    def create_plan(self, values: dict[str, Any], *, audit) -> StoredPlan:
        try:
            # One transaction: the INSERT and the audit call. If the audit fails, the plan is not saved.
            with self._pool.connection() as conn, conn.transaction():
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        f"""
                        INSERT INTO plan_suscripcion
                            (nombre, tipo, descripcion, precio, duracion_dias, categoria, beneficios, institucion)
                        VALUES (%(nombre)s, %(tipo)s, %(descripcion)s, %(precio)s, %(duracion_dias)s,
                                %(categoria)s, %(beneficios)s, %(institucion)s)
                        RETURNING {self._PLAN_COLUMNS}
                        """,
                        {"descripcion": None, "categoria": "PLAN", "institucion": None, **values,
                         "beneficios": Jsonb(list(values.get("beneficios", ())))},
                    )
                    plan = _plan(cur.fetchone())
                audit("PLAN_CREATED", None, plan)
                return plan
        except errors.UniqueViolation as error:
            raise PlanNombreDuplicadoError from error

    _EDITABLE = frozenset({"nombre", "descripcion", "precio", "duracion_dias", "beneficios", "institucion"})

    def update_plan(self, plan_id: int, changes: dict[str, Any], *, audit):
        if not set(changes) <= self._EDITABLE:
            raise ValueError("Unknown plan column")
        try:
            with self._pool.connection() as conn, conn.transaction():
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(f"SELECT {self._PLAN_COLUMNS} FROM plan_suscripcion WHERE id = %s FOR UPDATE",
                                (plan_id,))
                    row = cur.fetchone()
                    if row is None:
                        return None
                    before = _plan(row)
                    if not before.is_active:
                        raise PlanInactivoError
                    values = {k: (Jsonb(list(v)) if k == "beneficios" else v) for k, v in changes.items()}
                    assignments = ", ".join(f"{column} = %({column})s" for column in values)
                    cur.execute(
                        f"UPDATE plan_suscripcion SET {assignments} WHERE id = %(id)s RETURNING {self._PLAN_COLUMNS}",
                        {**values, "id": plan_id},
                    )
                    after = _plan(cur.fetchone())
                audit("PLAN_UPDATED", before, after)
                return before, after
        except errors.UniqueViolation as error:
            raise PlanNombreDuplicadoError from error

    def deactivate_plan(self, plan_id: int, *, audit, motivo: str | None = None):
        with self._pool.connection() as conn, conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"SELECT {self._PLAN_COLUMNS} FROM plan_suscripcion WHERE id = %s FOR UPDATE",
                            (plan_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                before = _plan(row)
                if not before.is_active:
                    raise PlanInactivoError
                if before.es_base:
                    raise PlanBaseError
                cur.execute(
                    f"""UPDATE plan_suscripcion
                        SET is_active = false, fecha_desactivacion = now(), motivo_desactivacion = %s
                        WHERE id = %s RETURNING {self._PLAN_COLUMNS}""",
                    (motivo, plan_id),
                )
                after = _plan(cur.fetchone())
            audit("PLAN_DEACTIVATED", before, after)
            return before, after

    def reactivate_plan(self, plan_id: int, *, audit):
        try:
            with self._pool.connection() as conn, conn.transaction():
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(f"SELECT {self._PLAN_COLUMNS} FROM plan_suscripcion WHERE id = %s FOR UPDATE",
                                (plan_id,))
                    row = cur.fetchone()
                    if row is None:
                        return None
                    before = _plan(row)
                    if before.is_active:
                        raise PlanActivoError
                    # The unique index on the name among ACTIVE plans refuses it if the name was taken meanwhile.
                    cur.execute(
                        f"""UPDATE plan_suscripcion
                            SET is_active = true, fecha_desactivacion = NULL, motivo_desactivacion = NULL
                            WHERE id = %s RETURNING {self._PLAN_COLUMNS}""",
                        (plan_id,),
                    )
                    after = _plan(cur.fetchone())
                audit("PLAN_REACTIVATED", before, after)
                return before, after
        except errors.UniqueViolation as error:
            raise PlanNombreDuplicadoError from error

    def list_suscripciones_plan(self, plan_id: int, *, estado: str, limit: int, offset: int):
        where = """s.plan_id = %(plan_id)s
                   AND (%(estado)s = 'todas'
                        OR (%(estado)s = 'vigente' AND s.fecha_fin > now())
                        OR (%(estado)s = 'vencida' AND s.fecha_fin <= now()))"""
        params = {"plan_id": plan_id, "estado": estado, "limit": limit, "offset": offset}
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT count(*) AS total FROM suscripcion s WHERE {where}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"""
                SELECT s.id, s.usuario_id, s.club_id, NULL::text AS club_nombre, s.fecha_inicio, s.fecha_fin,
                       s.precio_pagado, s.plan_nombre,
                       CASE WHEN s.fecha_fin > now() THEN 'ACTIVE' ELSE 'EXPIRED' END AS estado
                FROM suscripcion s
                WHERE {where}
                ORDER BY (s.fecha_fin > now()) DESC, s.fecha_fin DESC, s.id
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            return [PlanSuscripcion(**row) for row in cur.fetchall()], total

    def get_suscripcion(
        self, *, usuario_id: UUID | None = None, club_id: UUID | None = None
    ) -> StoredSuscripcion | None:
        if (usuario_id is None) == (club_id is None):
            raise ValueError("Give exactly one of usuario_id and club_id")
        column, value = ("s.usuario_id", usuario_id) if usuario_id else ("s.club_id", club_id)
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"{_SUSCRIPCION_SELECT} WHERE {column} = %s", (value,))
            row = cur.fetchone()
        return _to_suscripcion(row) if row is not None else None

    def create_transaccion(self, values: dict[str, Any]) -> StoredTransaccion:
        with self._pool.connection() as conn:
            transaccion_id = conn.execute(
                """
                INSERT INTO transaccion_pago
                    (usuario_id, club_id, iniciada_por, plan_id, buy_order, session_id,
                     token_ws, amount, plan_nombre, duracion_dias, beneficios)
                VALUES (%(usuario_id)s, %(club_id)s, %(iniciada_por)s, %(plan_id)s,
                        %(buy_order)s, %(session_id)s, %(token_ws)s, %(amount)s,
                        %(plan_nombre)s, %(duracion_dias)s, %(beneficios)s)
                RETURNING id
                """,
                {"usuario_id": None, "club_id": None, **values,
                 "beneficios": Jsonb(list(values["beneficios"]))},
            ).fetchone()[0]
        transaccion = self._get_transaccion("t.id = %s", transaccion_id)
        assert transaccion is not None
        return transaccion

    def get_transaccion(
        self, *, token_ws: str | None = None, buy_order: str | None = None
    ) -> StoredTransaccion | None:
        if (token_ws is None) == (buy_order is None):
            raise ValueError("Give exactly one of token_ws and buy_order")
        if token_ws is not None:
            return self._get_transaccion("t.token_ws = %s", token_ws)
        return self._get_transaccion("t.buy_order = %s", buy_order)

    def _get_transaccion(self, condition: str, value: object) -> StoredTransaccion | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"{_TRANSACCION_SELECT} WHERE {condition}", (value,))
            row = cur.fetchone()
        return _to_transaccion(row) if row is not None else None

    def settle_transaccion(
        self, transaccion_id: UUID, status: str, resultado: dict[str, Any] | None
    ) -> tuple[StoredTransaccion, bool, StoredSuscripcion | None]:
        resultado = resultado or {}
        with self._pool.connection() as conn, conn.transaction():
            # Only a PENDING payment changes: a second confirmation of the same
            # token finds nothing here and cannot extend the subscription again.
            row = conn.execute(
                """
                UPDATE transaccion_pago
                SET status = %(status)s,
                    response_code = %(response_code)s,
                    authorization_code = %(authorization_code)s,
                    payment_type_code = %(payment_type_code)s,
                    card_last4 = %(card_last4)s,
                    installments_number = %(installments_number)s,
                    transaction_date = %(transaction_date)s,
                    fecha_actualizacion = now()
                WHERE id = %(id)s AND status = 'PENDING'
                RETURNING usuario_id, club_id, plan_id, amount, plan_nombre, duracion_dias, beneficios
                """,
                {
                    "response_code": None, "authorization_code": None,
                    "payment_type_code": None, "card_last4": None,
                    "installments_number": None, "transaction_date": None,
                    **resultado, "status": status, "id": transaccion_id,
                },
            ).fetchone()
            changed = row is not None
            if changed and status == "APPROVED":
                usuario_id, club_id, plan_id, amount, plan_nombre, dias, beneficios = row
                # What the buyer paid, copied when the payment started: a later change to the plan (price,
                # duration, benefits) does not change the days or the terms of this subscription.
                column = "usuario_id" if usuario_id is not None else "club_id"
                conn.execute(
                    _SUSCRIPCION_UPSERT.format(column=column),
                    {
                        "usuario_id": usuario_id, "club_id": club_id, "plan_id": plan_id,
                        "dias": dias, "transaccion_id": transaccion_id, "plan_nombre": plan_nombre,
                        "precio_pagado": amount, "beneficios": Jsonb(beneficios),
                    },
                )
        transaccion = self._get_transaccion("t.id = %s", transaccion_id)
        assert transaccion is not None
        suscripcion = None
        if transaccion.status == "APPROVED":
            suscripcion = self.get_suscripcion(
                usuario_id=transaccion.usuario_id, club_id=transaccion.club_id
            )
        return transaccion, changed, suscripcion


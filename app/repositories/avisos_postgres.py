"""HU-55: aviso_pendiente in PostgreSQL. Mixed into PostgresPaymentRepository (uses its self._pool)."""
from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.repositories.avisos import AvisoNuevo, AvisoPendiente, SuscripcionPorVencer

_POR_VENCER = """
    SELECT s.id, s.usuario_id, s.club_id, s.plan_id, s.plan_nombre, p.is_active AS plan_activo,
           s.fecha_inicio, s.fecha_fin,
           COALESCE(s.usuario_id, t.iniciada_por, (
               SELECT a.iniciada_por FROM transaccion_pago a
               WHERE a.club_id = s.club_id AND a.status = 'APPROVED'
               ORDER BY a.fecha_actualizacion DESC, a.id DESC LIMIT 1)) AS destinatario
    FROM suscripcion s
    JOIN plan_suscripcion p ON p.id = s.plan_id
    LEFT JOIN transaccion_pago t ON t.id = s.ultima_transaccion_id
    WHERE s.fecha_fin > %(desde)s AND s.fecha_fin <= %(hasta)s
    ORDER BY s.fecha_fin, s.id
"""

_VIGENTE = "s.id = a.suscripcion_id AND s.fecha_fin = a.fecha_fin AND s.fecha_fin > %(ahora)s"


class AvisoVencimientoPostgresMixin:
    def suscripciones_por_vencer(self, desde: datetime, hasta: datetime) -> list[SuscripcionPorVencer]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_POR_VENCER, {"desde": desde, "hasta": hasta})
            return [SuscripcionPorVencer(**row) for row in cur.fetchall()]

    def registrar_aviso(self, aviso: AvisoNuevo) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO aviso_pendiente
                       (suscripcion_id, tipo, fecha_fin, user_id, plan_id, kind, title, body, data)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT ON CONSTRAINT aviso_pendiente_unico DO NOTHING
                   RETURNING id""",
                (aviso.suscripcion_id, aviso.tipo, aviso.fecha_fin, aviso.user_id, aviso.plan_id,
                 aviso.kind, aviso.title, aviso.body, Jsonb(aviso.data)),
            ).fetchone()
            return row is not None

    def descartar_avisos_obsoletos(self, ahora: datetime) -> int:
        with self._pool.connection() as conn:
            return conn.execute(
                f"""UPDATE aviso_pendiente a SET published_at = now(), descartado = true
                    WHERE a.published_at IS NULL
                    AND NOT EXISTS (SELECT 1 FROM suscripcion s WHERE {_VIGENTE})""",
                {"ahora": ahora},
            ).rowcount

    def avisos_pendientes(self, ahora: datetime, limit: int) -> list[AvisoPendiente]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""SELECT a.id, a.user_id, a.kind, a.title, a.body, a.data, a.created_at
                    FROM aviso_pendiente a
                    WHERE a.published_at IS NULL
                    AND EXISTS (SELECT 1 FROM suscripcion s WHERE {_VIGENTE})
                    ORDER BY a.id LIMIT %(limit)s""",
                {"ahora": ahora, "limit": limit},
            )
            return [AvisoPendiente(**row) for row in cur.fetchall()]

    def confirmar_avisos(self, ids: list[int]) -> int:
        if not ids:
            return 0
        with self._pool.connection() as conn:
            return conn.execute(
                "UPDATE aviso_pendiente SET published_at = now() WHERE published_at IS NULL AND id = ANY(%s)",
                (list(ids),),
            ).rowcount

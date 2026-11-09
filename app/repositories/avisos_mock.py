"""HU-55: aviso_pendiente in memory. Mixed into MockPaymentRepository (uses its _lock, _suscripciones,
_transacciones and _planes); same rules as the PostgreSQL version, including the unique notice."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.repositories.avisos import AvisoNuevo, AvisoPendiente, SuscripcionPorVencer


class AvisoVencimientoMockMixin:
    def _avisos(self) -> dict[int, dict[str, Any]]:
        return self.__dict__.setdefault("_avisos_store", {})

    def _destinatario(self, row):
        if row["usuario_id"] is not None:
            return row["usuario_id"]
        ultima = self._transacciones.get(row.get("ultima_transaccion_id"))
        if ultima is not None:
            return ultima.iniciada_por
        aprobadas = [t for t in self._transacciones.values()
                     if t.club_id == row["club_id"] and t.status == "APPROVED"]
        aprobadas.sort(key=lambda t: (t.fecha_actualizacion, str(t.id)), reverse=True)
        return aprobadas[0].iniciada_por if aprobadas else None

    def suscripciones_por_vencer(self, desde: datetime, hasta: datetime) -> list[SuscripcionPorVencer]:
        with self._lock:
            rows = [r for r in self._suscripciones.values() if desde < r["fecha_fin"] <= hasta]
            rows.sort(key=lambda r: (r["fecha_fin"], str(r["id"])))
            result = []
            for r in rows:
                plan = self._planes.get(r["plan"].id)
                result.append(SuscripcionPorVencer(
                    id=r["id"], plan_id=r["plan"].id, plan_nombre=r["plan"].nombre,
                    plan_activo=bool(plan and plan.is_active), fecha_inicio=r["fecha_inicio"],
                    fecha_fin=r["fecha_fin"], destinatario=self._destinatario(r),
                    usuario_id=r["usuario_id"], club_id=r["club_id"]))
            return result

    def registrar_aviso(self, aviso: AvisoNuevo) -> bool:
        with self._lock:
            avisos = self._avisos()
            clave = (aviso.suscripcion_id, aviso.tipo, aviso.fecha_fin)
            if any(a["clave"] == clave for a in avisos.values()):
                return False
            nuevo_id = max(avisos, default=0) + 1
            avisos[nuevo_id] = {"clave": clave, "aviso": aviso, "created_at": datetime.now(timezone.utc),
                                "published_at": None, "descartado": False}
            return True

    def _vigente(self, a, ahora: datetime) -> bool:
        suscripcion_id, _, fecha_fin = a["clave"]
        return any(r["id"] == suscripcion_id and r["fecha_fin"] == fecha_fin and r["fecha_fin"] > ahora
                   for r in self._suscripciones.values())

    def descartar_avisos_obsoletos(self, ahora: datetime) -> int:
        with self._lock:
            cambiados = 0
            for a in self._avisos().values():
                if a["published_at"] is None and not self._vigente(a, ahora):
                    a["published_at"], a["descartado"] = datetime.now(timezone.utc), True
                    cambiados += 1
            return cambiados

    def avisos_pendientes(self, ahora: datetime, limit: int) -> list[AvisoPendiente]:
        with self._lock:
            result = []
            for aviso_id in sorted(self._avisos()):
                a = self._avisos()[aviso_id]
                if a["published_at"] is None and self._vigente(a, ahora):
                    datos = a["aviso"]
                    result.append(AvisoPendiente(id=aviso_id, user_id=datos.user_id, kind=datos.kind,
                                                 title=datos.title, body=datos.body, data=dict(datos.data),
                                                 created_at=a["created_at"]))
            return result[:limit]

    def confirmar_avisos(self, ids: list[int]) -> int:
        with self._lock:
            cambiados = 0
            for aviso_id in ids:
                a = self._avisos().get(aviso_id)
                if a is not None and a["published_at"] is None:
                    a["published_at"] = datetime.now(timezone.utc)
                    cambiados += 1
            return cambiados

    def avisos_registrados(self) -> list[dict[str, Any]]:
        """Test helper: every notice with its state."""
        with self._lock:
            return [{"id": i, **a} for i, a in sorted(self._avisos().items())]

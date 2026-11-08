from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from functools import wraps
from typing import Any
from uuid import UUID, uuid4
from app.repositories.base import (StoredPlan, StoredTransaccion, StoredSuscripcion, PlanSuscripcion, PlanNombreDuplicadoError, PlanInactivoError, PlanActivoError, PlanBaseError)

def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call

class MockPaymentRepository:
    def __init__(self, *, planes: list[StoredPlan] | None = None):
        self._planes = {p.id: replace(p) for p in planes or []}
        self._transacciones = {}
        self._suscripciones = {}
        self._lock = RLock()
        self._last_tick = datetime.now(timezone.utc)

    def _tick(self):
        self._last_tick = max(datetime.now(timezone.utc), self._last_tick + timedelta(microseconds=1))
        return self._last_tick

    @_locked
    def list_planes(self, tipo: str | None = None) -> list[StoredPlan]:
        return sorted(
            (p for p in self._planes.values() if p.is_active and tipo in (None, p.tipo)),
            key=lambda p: (p.precio, p.id),
        )

    @_locked
    def get_plan(self, plan_id: int) -> StoredPlan | None:
        plan = self._planes.get(plan_id)
        return plan if plan is not None and plan.is_active else None

    @_locked
    def get_plan_any(self, plan_id: int) -> StoredPlan | None:
        plan = self._planes.get(plan_id)
        return replace(plan) if plan is not None else None

    @_locked
    def _vigentes(self, plan_id: int) -> int:
        now = datetime.now(timezone.utc)
        return sum(1 for row in self._suscripciones.values()
                   if row["plan"].id == plan_id and row["fecha_fin"] > now)

    @_locked
    def list_planes_admin(
        self, *, estado: str, tipo: str | None, categoria: str | None, limit: int, offset: int
    ) -> tuple[list[StoredPlan], int]:
        matches = [
            p for p in self._planes.values()
            if (estado == "todos" or p.is_active == (estado == "activo"))
            and tipo in (None, p.tipo) and categoria in (None, p.categoria)
        ]
        matches.sort(key=lambda p: (p.fecha_creacion or datetime.min.replace(tzinfo=timezone.utc), p.id), reverse=True)
        page = matches[offset : offset + limit]
        return [replace(p, suscripciones_vigentes=self._vigentes(p.id)) for p in page], len(matches)

    @_locked
    def _name_taken(self, nombre: str, except_id: int | None = None) -> bool:
        wanted = " ".join(nombre.split()).lower()
        return any(p.is_active and p.id != except_id and " ".join(p.nombre.split()).lower() == wanted
                   for p in self._planes.values())

    @_locked
    def create_plan(self, values: dict[str, Any], *, audit) -> StoredPlan:
        if self._name_taken(values["nombre"]):
            raise PlanNombreDuplicadoError
        plan_id = max(self._planes, default=0) + 1
        plan = StoredPlan(
            id=plan_id, nombre=values["nombre"], tipo=values["tipo"], precio=values["precio"],
            duracion_dias=values["duracion_dias"], descripcion=values.get("descripcion"),
            categoria=values.get("categoria", "PLAN"), beneficios=tuple(values.get("beneficios", ())),
            institucion=values.get("institucion"),
            fecha_creacion=datetime.now(timezone.utc),
        )
        audit("PLAN_CREATED", None, plan)       # raises -> nothing saved
        self._planes[plan_id] = plan
        return replace(plan)

    @_locked
    def update_plan(self, plan_id: int, changes: dict[str, Any], *, audit):
        before = self._planes.get(plan_id)
        if before is None:
            return None
        if not before.is_active:
            raise PlanInactivoError
        if "nombre" in changes and self._name_taken(changes["nombre"], except_id=plan_id):
            raise PlanNombreDuplicadoError
        values = dict(changes)
        if "beneficios" in values:
            values["beneficios"] = tuple(values["beneficios"])
        after = replace(before, **values)
        audit("PLAN_UPDATED", replace(before), after)
        self._planes[plan_id] = after
        return replace(before), replace(after)

    @_locked
    def deactivate_plan(self, plan_id: int, *, audit, motivo: str | None = None):
        before = self._planes.get(plan_id)
        if before is None:
            return None
        if not before.is_active:
            raise PlanInactivoError
        if before.es_base:
            raise PlanBaseError
        after = replace(before, is_active=False, fecha_desactivacion=datetime.now(timezone.utc),
                        motivo_desactivacion=motivo)
        audit("PLAN_DEACTIVATED", replace(before), after)
        self._planes[plan_id] = after
        return replace(before), replace(after)

    @_locked
    def reactivate_plan(self, plan_id: int, *, audit):
        before = self._planes.get(plan_id)
        if before is None:
            return None
        if before.is_active:
            raise PlanActivoError
        if self._name_taken(before.nombre, except_id=plan_id):
            raise PlanNombreDuplicadoError
        after = replace(before, is_active=True, fecha_desactivacion=None, motivo_desactivacion=None)
        audit("PLAN_REACTIVATED", replace(before), after)
        self._planes[plan_id] = after
        return replace(before), replace(after)

    @_locked
    def list_suscripciones_plan(self, plan_id: int, *, estado: str, limit: int, offset: int):
        now = datetime.now(timezone.utc)
        rows = [r for r in self._suscripciones.values() if r["plan"].id == plan_id
                and (estado == "todas" or (estado == "vigente") == (r["fecha_fin"] > now))]
        rows.sort(key=lambda r: (r["fecha_fin"] > now, r["fecha_fin"], str(r["id"])), reverse=True)
        items = []
        for r in rows[offset : offset + limit]:
            items.append(PlanSuscripcion(
                id=r["id"], estado="ACTIVE" if r["fecha_fin"] > now else "EXPIRED", fecha_inicio=r["fecha_inicio"],
                fecha_fin=r["fecha_fin"], precio_pagado=r["plan"].precio, plan_nombre=r["plan"].nombre,
                usuario_id=r["usuario_id"],
                club_id=r["club_id"], club_nombre=None))
        return items, len(rows)

    @_locked
    def get_suscripcion(
        self, *, usuario_id: UUID | None = None, club_id: UUID | None = None
    ) -> StoredSuscripcion | None:
        if (usuario_id is None) == (club_id is None):
            raise ValueError("Give exactly one of usuario_id and club_id")
        key = ("u", usuario_id) if usuario_id else ("c", club_id)
        row = self._suscripciones.get(key)
        if row is None:
            return None
        estado = "ACTIVE" if row["fecha_fin"] > datetime.now(timezone.utc) else "EXPIRED"
        return StoredSuscripcion(plan=row["plan"], estado=estado, **{
            k: v for k, v in row.items() if k not in {"plan", "ultima_transaccion_id"}
        })

    @_locked
    def create_transaccion(self, values: dict[str, Any]) -> StoredTransaccion:
        if any(t.buy_order == values["buy_order"] or t.token_ws == values["token_ws"]
               for t in self._transacciones.values()):
            raise ValueError("buy_order and token_ws must be unique")
        now = self._tick()
        transaccion = StoredTransaccion(
            id=uuid4(),
            # What is being bought, frozen now (the same fields the PostgreSQL repository copies).
            plan=replace(
                self._planes[values["plan_id"]], precio=values["amount"], nombre=values["plan_nombre"],
                duracion_dias=values["duracion_dias"], beneficios=tuple(values["beneficios"]),
            ),
            status="PENDING",
            fecha_creacion=now,
            fecha_actualizacion=now,
            **{k: v for k, v in values.items() if k not in {"plan_id", "plan_nombre", "duracion_dias", "beneficios"}},
        )
        self._transacciones[transaccion.id] = transaccion
        return replace(transaccion)

    @_locked
    def get_transaccion(
        self, *, token_ws: str | None = None, buy_order: str | None = None
    ) -> StoredTransaccion | None:
        if (token_ws is None) == (buy_order is None):
            raise ValueError("Give exactly one of token_ws and buy_order")
        for transaccion in self._transacciones.values():
            if (token_ws is not None and transaccion.token_ws == token_ws) or (
                buy_order is not None and transaccion.buy_order == buy_order
            ):
                return replace(transaccion)
        return None

    @_locked
    def settle_transaccion(
        self, transaccion_id: UUID, status: str, resultado: dict[str, Any] | None
    ) -> tuple[StoredTransaccion, bool, StoredSuscripcion | None]:
        transaccion = self._transacciones[transaccion_id]
        changed = transaccion.status == "PENDING"
        if changed:
            for column, value in (resultado or {}).items():
                setattr(transaccion, column, value)
            transaccion.status = status
            transaccion.fecha_actualizacion = self._tick()
            if status == "APPROVED":
                self._extend_suscripcion(transaccion)
        suscripcion = None
        if transaccion.status == "APPROVED":
            suscripcion = self.get_suscripcion(
                usuario_id=transaccion.usuario_id, club_id=transaccion.club_id
            )
        return replace(transaccion), changed, suscripcion

    @_locked
    def _extend_suscripcion(self, transaccion: StoredTransaccion) -> None:
        key = ("u", transaccion.usuario_id) if transaccion.usuario_id else ("c", transaccion.club_id)
        now = datetime.now(timezone.utc)
        dias = timedelta(days=transaccion.plan.duracion_dias)
        row = self._suscripciones.get(key)
        if row is None:
            self._suscripciones[key] = {
                "id": uuid4(), "usuario_id": transaccion.usuario_id, "club_id": transaccion.club_id,
                "plan": replace(transaccion.plan), "fecha_inicio": now, "fecha_fin": now + dias,
                "ultima_transaccion_id": transaccion.id,
            }
            return
        if row["fecha_fin"] <= now:
            row["fecha_inicio"] = now
        row["plan"] = replace(transaccion.plan)
        row["fecha_fin"] = max(row["fecha_fin"], now) + dias
        row["ultima_transaccion_id"] = transaccion.id

    @_locked
    def set_suscripcion_fin(
        self, *, usuario_id: UUID | None = None, club_id: UUID | None = None, fecha_fin: datetime
    ) -> None:
        """Test helper: move a subscription's end (for example into the past)."""
        key = ("u", usuario_id) if usuario_id else ("c", club_id)
        self._suscripciones[key]["fecha_fin"] = fecha_fin
        self._suscripciones[key]["fecha_inicio"] = min(
            self._suscripciones[key]["fecha_inicio"], fecha_fin - timedelta(seconds=1)
        )


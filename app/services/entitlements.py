"""Qué puede hacer una cuenta según su plan (HU-54, cierra HU-70: los otros servicios consultan el estado
del pago aquí). Solo lectura: usa la suscripción vigente de la cuenta y los límites de app.core.limites_plan."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.limites_plan import BENEFICIOS, PLAN_GRATIS, PLAN_PRO, limites
from app.repositories.base import PaymentRepository, StoredSuscripcion

TIPOS = ("usuario", "club")


def _es_pagada(suscripcion: StoredSuscripcion | None) -> bool:
    """Plan pagado vigente: una suscripción ACTIVE de un plan con precio o de un convenio.
    El plan base (Gratuito) nunca cuenta como Pro."""
    if suscripcion is None or suscripcion.estado != "ACTIVE":
        return False
    plan = suscripcion.plan
    if plan.es_base:
        return False
    return plan.precio > 0 or plan.categoria == "CONVENIO"


def destacados(repository: PaymentRepository, tipo: str) -> list[str]:
    """Cuentas con plan pagado vigente (beneficio "destacado"): el directorio de clubes las pone primero en
    todas las páginas. Misma regla que entitlement() (_es_pagada)."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo must be one of {TIPOS}")
    if "destacado" not in BENEFICIOS[PLAN_PRO]:
        return []
    return sorted({str(s.usuario_id if tipo == "usuario" else s.club_id)
                   for s in repository.list_suscripciones_vigentes(tipo) if _es_pagada(s)})


def entitlement(repository: PaymentRepository, tipo: str, cuenta_id: UUID) -> dict[str, Any]:
    if tipo not in TIPOS:
        raise ValueError(f"tipo must be one of {TIPOS}")
    suscripcion = repository.get_suscripcion(
        usuario_id=cuenta_id if tipo == "usuario" else None,
        club_id=cuenta_id if tipo == "club" else None,
    )
    pagada = _es_pagada(suscripcion)
    plan = PLAN_PRO if pagada else PLAN_GRATIS
    vigente_hasta: datetime | None = suscripcion.fecha_fin if pagada and suscripcion else None
    return {
        "tipo": tipo,
        "id": str(cuenta_id),
        "plan": plan,
        "plan_nombre": suscripcion.plan.nombre if pagada and suscripcion else None,
        "vigente_hasta": vigente_hasta.isoformat() if vigente_hasta else None,
        "limites": limites(tipo, plan),
        "beneficios": list(BENEFICIOS[plan]),
    }

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.dependencies import get_admin, get_repository
from app.core.security import Actor
from app.repositories.base import PaymentRepository
from app.schemas.pago import PlanCategoria
from app.schemas.pagos_admin import LIMIT_DEFAULT, LIMIT_MAX, EstadoPago, PagoAdminPage, TipoSuscriptor
from app.services.pagos_admin_service import PagosAdminService

# HU-57: pagos de la plataforma para el administrador. SOLO LECTURA: este router no tiene (ni debe tener) rutas
# de escritura; un pago solo cambia por el flujo de Webpay. Exige el rol admin aqui (el gateway solo valida el JWT).
router = APIRouter(prefix="/api/v1/payments/admin/transactions", tags=["pagos-admin"])


def get_pagos_admin_service(
    request: Request, repository: PaymentRepository = Depends(get_repository)
) -> PagosAdminService:
    return PagosAdminService(repository, getattr(request.app.state, "account_names", None),
                             getattr(request.app.state, "clubs_directory", None))


@router.get(
    "",
    response_model=PagoAdminPage,
    summary="Pagos de la plataforma (administrador, solo lectura)",
    description="Todos los pagos, el más reciente primero, con el deportista o club, el monto, el código de "
    "autorización, los últimos 4 dígitos, el plan (nombre pagado, categoría y tipo) y el período de suscripción que "
    "compró. Filtros: estado (PENDING, APPROVED, REJECTED, CANCELLED), desde y hasta (días de Chile, AAAA-MM-DD, "
    "ambos incluidos, sobre la fecha en que se inició el pago), categoria (PLAN, CONVENIO) y tipo (USUARIO, CLUB: "
    f"quién pagó); page (desde 1) y limit (máximo {LIMIT_MAX}).",
)
def list_transactions(
    response: Response,
    _: Annotated[Actor, Depends(get_admin)],
    service: Annotated[PagosAdminService, Depends(get_pagos_admin_service)],
    estado: Annotated[EstadoPago | None, Query()] = None,
    desde: Annotated[date | None, Query()] = None,
    hasta: Annotated[date | None, Query()] = None,
    categoria: Annotated[PlanCategoria | None, Query()] = None,
    tipo: Annotated[TipoSuscriptor | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = LIMIT_DEFAULT,
) -> PagoAdminPage:
    response.headers["Cache-Control"] = "no-store"
    return service.list(estado=estado, desde=desde, hasta=hasta, categoria=categoria, tipo=tipo, page=page,
                        limit=limit)

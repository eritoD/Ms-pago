"""HU-57: pagos de la plataforma para el administrador (solo lectura)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.pago import PlanCategoria, PlanTipo

LIMIT_DEFAULT = 20
# Tope por pagina. La exportacion CSV de la web recorre todas las paginas del filtro con este tope.
LIMIT_MAX = 200

EstadoPago = Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]
TipoSuscriptor = Literal["USUARIO", "CLUB"]


class PlanPagoAdmin(BaseModel):
    """El plan tal como se pago (nombre copiado al iniciar el pago); categoria y tipo no se editan."""

    id: int
    nombre: str
    categoria: PlanCategoria
    tipo: PlanTipo


class PeriodoPagoAdmin(BaseModel):
    """El periodo de suscripcion que compro este pago (solo pagos aprobados)."""

    id: UUID
    fecha_inicio: datetime
    fecha_fin: datetime
    vigente: bool
    # VIGENTE (en curso), PROGRAMADO (una renovacion que empieza cuando termine el periodo actual) o TERMINADO.
    estado: Literal["VIGENTE", "PROGRAMADO", "TERMINADO"]


class PagoAdminRead(BaseModel):
    id: UUID
    estado: EstadoPago
    monto: int
    fecha: datetime  # cuando se inicio el pago
    fecha_transbank: datetime | None  # la fecha que informa Webpay al confirmar
    buy_order: str
    codigo_autorizacion: str | None
    card_last4: str | None
    tipo_pago: str | None  # payment_type_code de Webpay (VD, VN, VC...)
    cuotas: int | None
    plan: PlanPagoAdmin
    tipo_suscriptor: TipoSuscriptor
    suscriptor_id: UUID
    # Nombre del deportista (Ms_Users) o del club (Ms-club). None si el servicio no respondio.
    suscriptor_nombre: str | None
    periodo: PeriodoPagoAdmin | None


class PagoAdminPage(BaseModel):
    items: list[PagoAdminRead]
    page: int
    limit: int
    total: int

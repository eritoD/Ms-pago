from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.pago import PlanRead


class PeriodoRead(BaseModel):
    id: UUID
    transaccion_id: UUID
    plan_id: int
    plan_nombre: str
    precio_pagado: int
    duracion_dias: int
    beneficios: list[str]
    fecha_inicio: datetime
    fecha_fin: datetime
    vigente: bool


class PagoHistorialRead(BaseModel):
    transaccion_id: UUID
    buy_order: str
    fecha: datetime
    plan: PlanRead
    amount: int
    status: str
    authorization_code: str | None
    card_last4: str | None
    payment_type_code: str | None
    periodo: PeriodoRead | None


class PaginaPagos(BaseModel):
    items: list[PagoHistorialRead]
    total: int
    page: int
    page_size: int


class PaginaPeriodos(BaseModel):
    items: list[PeriodoRead]
    total: int
    page: int
    page_size: int

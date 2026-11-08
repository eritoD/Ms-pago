from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol
from uuid import UUID

@dataclass(frozen=True)
class StoredPlan:
    """A row of plan_suscripcion (006_pagos). precio is in whole Chilean pesos."""

    id: int
    nombre: str
    tipo: str  # USUARIO | CLUB: quien lo compra
    precio: int
    duracion_dias: int | None  # None = sin vencimiento (solo si precio == 0)
    descripcion: str | None = None
    is_active: bool = True
    # PLAN | CONVENIO (HU-56). Un convenio es un plan acordado con una institucion o un club.
    categoria: str = "PLAN"
    beneficios: tuple[str, ...] = ()
    # Con quien se firmo el convenio (solo CONVENIO).
    institucion: str | None = None
    # Solo en el listado de administracion.
    suscripciones_vigentes: int = 0
    fecha_creacion: datetime | None = None
    # El plan base (p. ej. Gratuito) no se puede desactivar. Cuando y por que se retiro del catalogo (None si activo).
    es_base: bool = False
    fecha_desactivacion: datetime | None = None
    motivo_desactivacion: str | None = None

@dataclass(frozen=True)
class PlanSuscripcion:
    """A subscription whose last payment was of a plan, for the administrator (HU-56). The buyer is an athlete's
    account (usuario_id; the name lives in Ms_Users) or a club (club_nombre). estado: ACTIVE | EXPIRED."""

    id: UUID
    estado: str
    fecha_inicio: datetime
    fecha_fin: datetime
    precio_pagado: int
    plan_nombre: str  # the name of the plan when it was paid
    usuario_id: UUID | None = None
    club_id: UUID | None = None
    club_nombre: str | None = None

@dataclass
class StoredTransaccion:
    """A row of transaccion_pago with its plan. `plan` carries what was paid (copied when the payment started):
    the price is `amount`, and name, duration and benefits are the ones of that moment, not the plan's today."""

    id: UUID
    iniciada_por: UUID
    plan: StoredPlan
    buy_order: str
    session_id: str
    token_ws: str
    amount: int
    status: str  # PENDING | APPROVED | REJECTED | CANCELLED
    usuario_id: UUID | None = None
    club_id: UUID | None = None
    response_code: int | None = None
    authorization_code: str | None = None
    payment_type_code: str | None = None
    card_last4: str | None = None
    installments_number: int | None = None
    transaction_date: datetime | None = None
    fecha_creacion: datetime | None = None
    fecha_actualizacion: datetime | None = None

@dataclass
class StoredSuscripcion:
    """A row of suscripcion with its plan. estado is computed when read. `plan` carries what the last payment
    bought (price paid, name, duration, benefits), so a later change to the plan does not alter it."""

    id: UUID
    plan: StoredPlan
    fecha_inicio: datetime
    fecha_fin: datetime
    estado: str  # ACTIVE | EXPIRED
    usuario_id: UUID | None = None
    club_id: UUID | None = None

@dataclass(frozen=True)
class StoredPeriodo:
    id: UUID
    transaccion_id: UUID
    plan_id: int
    plan_nombre: str
    precio_pagado: int
    duracion_dias: int
    beneficios: list[str]
    fecha_inicio: datetime
    fecha_fin: datetime
    fecha_creacion: datetime
    usuario_id: UUID | None = None
    club_id: UUID | None = None


class PlanNombreDuplicadoError(Exception):
    """Another ACTIVE plan already has that name (HU-56)."""

class PlanInactivoError(Exception):
    """The plan is deactivated: it cannot be edited or deactivated again (HU-56)."""

class PlanActivoError(Exception):
    """The plan is already active: it cannot be reactivated (HU-56)."""

class PlanBaseError(Exception):
    """The base plan cannot be deactivated (HU-56)."""

PlanAudit = Callable[[str, StoredPlan | None, StoredPlan], None]

class PaymentRepository(Protocol):
    def list_transacciones(self, *, usuario_id: UUID | None, club_id: UUID | None,
                           limit: int, offset: int) -> tuple[list[StoredTransaccion], int]: ...

    def get_transaccion_propia(self, transaccion_id: UUID, *, usuario_id: UUID | None,
                              club_id: UUID | None) -> StoredTransaccion | None: ...

    def list_periodos(self, *, usuario_id: UUID | None, club_id: UUID | None,
                     limit: int, offset: int) -> tuple[list[StoredPeriodo], int]: ...

    def get_periodo(self, transaccion_id: UUID) -> StoredPeriodo | None: ...

    def list_planes(self, tipo: str | None = None) -> list[StoredPlan]:
        """Active plans, cheapest first; optionally only one tipo."""
        ...

    def get_plan(self, plan_id: int) -> StoredPlan | None:
        """An active plan."""
        ...

    def get_suscripcion(
        self, *, usuario_id: UUID | None = None, club_id: UUID | None = None
    ) -> StoredSuscripcion | None:
        """The subscription of an athlete or of a club, active or expired."""
        ...

    def get_plan_any(self, plan_id: int) -> StoredPlan | None:
        """A plan, active or not."""
        ...

    def list_planes_admin(
        self, *, estado: str, tipo: str | None, categoria: str | None, limit: int, offset: int
    ) -> tuple[list[StoredPlan], int]:
        """Plans for the administrator (estado: activo, inactivo or todos), newest first, with the number of
        current subscriptions (fecha_fin in the future) of each, and the total before paging."""
        ...

    def create_plan(self, values: dict[str, Any], *, audit: PlanAudit) -> StoredPlan:
        """Insert an active plan and call `audit` before committing; if it raises, nothing is saved.
        Raises PlanNombreDuplicadoError."""
        ...

    def update_plan(
        self, plan_id: int, changes: dict[str, Any], *, audit: PlanAudit
    ) -> tuple[StoredPlan, StoredPlan] | None:
        """Change an active plan (row locked) and call `audit` before committing. Returns (before, after) or
        None if it does not exist. Raises PlanInactivoError, PlanNombreDuplicadoError. A change of price or
        duration never touches subscriptions or payments already made (they hold their own copy)."""
        ...

    def deactivate_plan(
        self, plan_id: int, *, audit: PlanAudit, motivo: str | None = None
    ) -> tuple[StoredPlan, StoredPlan] | None:
        """Logical delete: is_active = false, never DELETE; it stores the date and the reason. Subscriptions already
        paid keep running until their end. Returns (before, after) or None; raises PlanInactivoError if it was
        already deactivated and PlanBaseError if it is the base plan."""
        ...

    def reactivate_plan(self, plan_id: int, *, audit: PlanAudit) -> tuple[StoredPlan, StoredPlan] | None:
        """Put a deactivated plan back in the catalog (row locked), clearing its deactivation date and reason, and
        call `audit` before committing. Returns (before, after) or None. Raises PlanActivoError if it is already
        active and PlanNombreDuplicadoError if another ACTIVE plan has taken its name."""
        ...

    def list_suscripciones_plan(
        self, plan_id: int, *, estado: str, limit: int, offset: int
    ) -> tuple[list[PlanSuscripcion], int]:
        """Subscriptions whose last payment was of this plan (estado: vigente, vencida or todas), the current ones
        first and then the latest to end, with the total before paging."""
        ...

    def create_transaccion(self, values: dict[str, Any]) -> StoredTransaccion:
        """Insert a PENDING payment (values: iniciada_por, usuario_id or club_id,
        plan_id, buy_order, session_id, token_ws, amount)."""
        ...

    def get_transaccion(
        self, *, token_ws: str | None = None, buy_order: str | None = None
    ) -> StoredTransaccion | None: ...

    def settle_transaccion(
        self, transaccion_id: UUID, status: str, resultado: dict[str, Any] | None
    ) -> tuple[StoredTransaccion, bool, StoredSuscripcion | None]:
        """Move a PENDING payment to APPROVED, REJECTED or CANCELLED, storing the
        Transbank result. Only the call that really changes it returns True:
        when APPROVED it also creates or extends the buyer's subscription by the
        plan's duration, in the same transaction, exactly once. A payment that
        is no longer PENDING is returned untouched with False."""
        ...

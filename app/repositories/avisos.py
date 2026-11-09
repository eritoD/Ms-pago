"""HU-55: storage of the expiry notices (outbox aviso_pendiente, migration 003).

The PostgreSQL and the in-memory payment repositories get these methods from the mixins in
avisos_postgres.py and avisos_mock.py, so this feature does not touch their existing code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class SuscripcionPorVencer:
    """A subscription still active whose end is near, with who must be told."""

    id: UUID
    plan_id: int
    plan_nombre: str  # the name paid for
    plan_activo: bool  # False: the plan was withdrawn from the catalog, it cannot be bought again
    fecha_inicio: datetime
    fecha_fin: datetime
    destinatario: UUID | None  # the athlete, or who paid the club's last period; None = nobody known
    usuario_id: UUID | None = None
    club_id: UUID | None = None


@dataclass(frozen=True)
class AvisoNuevo:
    suscripcion_id: UUID
    tipo: str  # PREVIO (1 a 3 días antes) | HOY (el día del vencimiento)
    fecha_fin: datetime
    user_id: UUID
    plan_id: int
    kind: str
    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AvisoPendiente:
    """A notice not yet published, in the shape the gateway copies into its inbox."""

    id: int
    user_id: UUID
    kind: str
    title: str
    body: str
    data: dict[str, Any]
    created_at: datetime


class AvisoRepository(Protocol):
    def suscripciones_por_vencer(self, desde: datetime, hasta: datetime) -> list[SuscripcionPorVencer]:
        """Subscriptions with desde < fecha_fin <= hasta."""
        ...

    def registrar_aviso(self, aviso: AvisoNuevo) -> bool:
        """Insert the notice; False (and nothing changes) if that subscription, tipo and fecha_fin already has one."""
        ...

    def descartar_avisos_obsoletos(self, ahora: datetime) -> int:
        """Mark as published-and-discarded the pending notices whose subscription was renewed (another fecha_fin)
        or has already ended, so they are never shown."""
        ...

    def avisos_pendientes(self, ahora: datetime, limit: int) -> list[AvisoPendiente]:
        """Pending notices that are still true at ahora (same fecha_fin, not yet ended), oldest first."""
        ...

    def confirmar_avisos(self, ids: list[int]) -> int:
        """Mark those notices as published; returns how many changed."""
        ...

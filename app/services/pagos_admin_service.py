"""HU-57: pagos de la plataforma para el administrador. Solo lectura: no hay nada que editar ni anular aqui."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status

from app.repositories.base import PagoAdmin, PagosAdminRepository
from app.schemas.pagos_admin import PagoAdminPage, PagoAdminRead, PeriodoPagoAdmin, PlanPagoAdmin
from app.services.account_names import AccountDirectory
from app.services.clubs_directory import ClubsDirectory

# Las fechas del filtro son dias del calendario de Chile: "hasta" incluye todo ese dia.
ZONA_CHILE = ZoneInfo("America/Santiago")


def inicio_del_dia(dia: date) -> datetime:
    return datetime.combine(dia, time.min, tzinfo=ZONA_CHILE).astimezone(timezone.utc)


class PagosAdminService:
    def __init__(
        self, repository: PagosAdminRepository, names: AccountDirectory | None = None,
        clubs: ClubsDirectory | None = None,
    ) -> None:
        self._repository = repository
        self._names = names
        self._clubs = clubs

    def list(
        self, *, estado: str | None, desde: date | None, hasta: date | None, categoria: str | None,
        tipo: str | None, page: int, limit: int,
    ) -> PagoAdminPage:
        if desde is not None and hasta is not None and desde > hasta:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "La fecha 'desde' no puede ser posterior a 'hasta'.")
        rows, total = self._repository.list_pagos_admin(
            estado=estado, categoria=categoria, tipo=tipo,
            desde=inicio_del_dia(desde) if desde else None,
            hasta=inicio_del_dia(hasta + timedelta(days=1)) if hasta else None,
            limit=limit, offset=(page - 1) * limit,
        )
        # Nombres en lote: una llamada a Ms_Users por pagina y una consulta por club distinto (nunca por fila).
        athletes = list(dict.fromkeys(r.usuario_id for r in rows if r.usuario_id is not None))
        club_ids = list(dict.fromkeys(r.club_id for r in rows if r.club_id is not None))
        names = self._names.names(athletes) if self._names is not None and athletes else {}
        club_names = self._clubs.names(club_ids) if self._clubs is not None and club_ids else {}
        now = datetime.now(timezone.utc)
        items = [_to_read(r, club_names.get(r.club_id) if r.club_id else names.get(r.usuario_id), now) for r in rows]
        return PagoAdminPage(items=items, page=page, limit=limit, total=total)


def _to_read(row: PagoAdmin, nombre: str | None, now: datetime) -> PagoAdminRead:
    periodo = None
    if row.periodo_id is not None and row.periodo_inicio is not None and row.periodo_fin is not None:
        estado = ("PROGRAMADO" if now < row.periodo_inicio else "VIGENTE" if now < row.periodo_fin else "TERMINADO")
        periodo = PeriodoPagoAdmin(id=row.periodo_id, fecha_inicio=row.periodo_inicio, fecha_fin=row.periodo_fin,
                                   vigente=estado == "VIGENTE", estado=estado)
    es_club = row.club_id is not None
    return PagoAdminRead(
        id=row.id, estado=row.status, monto=row.amount, fecha=row.fecha_creacion,
        fecha_transbank=row.transaction_date, buy_order=row.buy_order, codigo_autorizacion=row.authorization_code,
        card_last4=row.card_last4, tipo_pago=row.payment_type_code, cuotas=row.installments_number,
        plan=PlanPagoAdmin(id=row.plan_id, nombre=row.plan_nombre, categoria=row.plan_categoria, tipo=row.plan_tipo),
        tipo_suscriptor="CLUB" if es_club else "USUARIO",
        suscriptor_id=row.club_id if es_club else row.usuario_id,
        suscriptor_nombre=nombre, periodo=periodo,
    )

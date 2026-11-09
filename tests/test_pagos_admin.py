"""HU-57: pagos de la plataforma para el administrador (solo lectura). Repositorio en memoria y directorios falsos;
lo mismo contra PostgreSQL está en test_pagos_admin_postgres.py."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from payment_support import token  # also sets JWT_SECRET before the app is imported
from test_pagos import PAY, PLAN_CLUB, PLAN_USUARIO, SETTINGS, Pagos
from test_planes_admin_panel import FakeNames

from app.main import create_app
from app.repositories.base import StoredPlan
from app.repositories.mock_payment_repository import MockPaymentRepository
from app.schemas.pagos_admin import LIMIT_MAX

ADMIN_PAGOS = f"{PAY}/admin/transactions"
CONVENIO_USUARIO = StoredPlan(5, "Convenio Universidad", "USUARIO", 2990, 180, categoria="CONVENIO",
                              institucion="Universidad de Chile")


class PanelPagos(Pagos):
    def __init__(self) -> None:
        super().__init__()
        self.repository = MockPaymentRepository(planes=[PLAN_USUARIO, PLAN_CLUB, CONVENIO_USUARIO])
        self.names = FakeNames()
        self.client = TestClient(create_app(repository=self.repository, webpay=self.webpay, webpay_settings=SETTINGS,
                                            clubs_directory=self.clubs, account_names=self.names))
        self.admin = token(uuid4(), "admin")

    def athlete_named(self, nombre: str):
        athlete_id, headers = self.athlete()
        self.names.names_by_id[athlete_id] = nombre
        return athlete_id, headers

    def list(self, **params):
        return self.client.get(ADMIN_PAGOS, headers=self.admin, params=params)


@pytest.fixture
def panel():
    """Cuatro pagos: Ana aprobado (plan), Beto rechazado (convenio), club aprobado y club pendiente."""
    p = PanelPagos()
    p.ana, ana = p.athlete_named("Ana P.")
    p.aprobado, approved = p.pay(ana, PLAN_USUARIO.id)
    assert approved.status_code == 200, approved.text
    p.beto, beto = p.athlete_named("Beto R.")
    p.rechazado, _ = p.pay(beto, CONVENIO_USUARIO.id, "rechazar")
    _, club_headers, p.club_id = p.club_admin()
    p.club_aprobado, _ = p.pay(club_headers, PLAN_CLUB.id)
    p.pendiente = p.started(club_headers, PLAN_CLUB.id)
    return p


def ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["items"]]


def by_order(p: PanelPagos, started: dict) -> str:
    return str(p.repository.get_transaccion(buy_order=started["buy_order"]).id)


# -- acceso -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["player", "usuario", "club_admin"])
def test_only_the_platform_admin_sees_the_payments(panel, role):
    response = panel.client.get(ADMIN_PAGOS, headers=token(uuid4(), role))
    assert response.status_code == 403
    # Ni con filtros inválidos se revela nada antes de la autorización.
    assert panel.client.get(ADMIN_PAGOS, headers=token(uuid4(), role), params={"estado": "X"}).status_code == 403


def test_without_a_token_it_is_401(panel):
    assert panel.client.get(ADMIN_PAGOS).status_code == 401


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_there_is_no_way_to_change_a_payment(panel, method):
    payment_id = by_order(panel, panel.aprobado)
    for path in (ADMIN_PAGOS, f"{ADMIN_PAGOS}/{payment_id}"):
        response = getattr(panel.client, method)(path, headers=panel.admin)
        assert response.status_code in {404, 405}, (method, path, response.status_code)
    assert panel.list(estado="APPROVED").json()["total"] == 2


def test_the_router_only_declares_read_routes():
    app = create_app(repository=MockPaymentRepository(), webpay=PanelPagos().webpay, webpay_settings=SETTINGS)
    methods = {m for route in app.routes if getattr(route, "path", "").startswith(ADMIN_PAGOS)
               for m in route.methods}
    assert methods <= {"GET", "HEAD"}


# -- contenido de cada fila -------------------------------------------------------------------------------


def test_each_row_has_the_payer_amount_authorization_plan_and_period(panel):
    response = panel.list()
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["total"], body["page"], body["limit"]) == (4, 1, 20)
    rows = {row["buy_order"]: row for row in body["items"]}

    approved = rows[panel.aprobado["buy_order"]]
    assert approved["estado"] == "APPROVED" and approved["monto"] == PLAN_USUARIO.precio
    assert approved["codigo_autorizacion"] and len(approved["codigo_autorizacion"]) == 6
    assert approved["plan"] == {"id": 1, "nombre": "Deportista Mensual", "categoria": "PLAN", "tipo": "USUARIO"}
    assert (approved["tipo_suscriptor"], approved["suscriptor_id"], approved["suscriptor_nombre"]) == (
        "USUARIO", str(panel.ana), "Ana P.")
    periodo = approved["periodo"]
    assert periodo["vigente"] is True and periodo["estado"] == "VIGENTE"
    inicio, fin = datetime.fromisoformat(periodo["fecha_inicio"]), datetime.fromisoformat(periodo["fecha_fin"])
    assert (fin - inicio).days == 30

    rejected = rows[panel.rechazado["buy_order"]]
    assert rejected["estado"] == "REJECTED" and rejected["periodo"] is None and rejected["codigo_autorizacion"] is None
    assert rejected["plan"]["categoria"] == "CONVENIO"

    club = rows[panel.club_aprobado["buy_order"]]
    assert (club["tipo_suscriptor"], club["suscriptor_id"], club["suscriptor_nombre"]) == (
        "CLUB", str(panel.club_id), "Club de prueba")
    assert club["periodo"] is not None
    assert rows[panel.pendiente["buy_order"]]["estado"] == "PENDING"


def test_names_are_asked_once_per_page_without_repeating_ids(panel):
    ana = token(panel.ana, "player")
    panel.pay(ana, PLAN_USUARIO.id)  # Ana renueva: tres pagos de deportistas, dos personas
    panel.names.asked.clear()
    assert panel.list().status_code == 200
    assert len(panel.names.asked) == 1
    assert len(panel.names.asked[0]) == len(set(panel.names.asked[0])) == 2


def test_a_missing_name_does_not_hide_the_payment(panel):
    panel.names.names_by_id.clear()
    rows = panel.list(tipo="USUARIO").json()["items"]
    assert len(rows) == 2 and all(row["suscriptor_nombre"] is None for row in rows)


# -- filtros ---------------------------------------------------------------------------------------------


def test_filter_by_status(panel):
    assert set(ids(panel.list(estado="APPROVED"))) == {by_order(panel, panel.aprobado), by_order(panel, panel.club_aprobado)}
    assert ids(panel.list(estado="REJECTED")) == [by_order(panel, panel.rechazado)]
    assert ids(panel.list(estado="PENDING")) == [by_order(panel, panel.pendiente)]
    assert ids(panel.list(estado="CANCELLED")) == []


def test_filter_by_category_and_payer_type(panel):
    assert ids(panel.list(categoria="CONVENIO")) == [by_order(panel, panel.rechazado)]
    assert len(ids(panel.list(categoria="PLAN"))) == 3
    assert set(ids(panel.list(tipo="CLUB"))) == {by_order(panel, panel.club_aprobado), by_order(panel, panel.pendiente)}
    assert ids(panel.list(tipo="USUARIO", estado="APPROVED", categoria="PLAN")) == [by_order(panel, panel.aprobado)]


def test_filter_by_dates_are_chilean_calendar_days_both_included(panel):
    repo = panel.repository
    # 2026-03-10 23:30 en Chile (UTC-3 en marzo) es 2026-03-11 02:30 UTC: cuenta como el día 10.
    repo.set_fecha_creacion(repo.get_transaccion(buy_order=panel.aprobado["buy_order"]).id,
                            datetime(2026, 3, 11, 2, 30, tzinfo=timezone.utc))
    repo.set_fecha_creacion(repo.get_transaccion(buy_order=panel.rechazado["buy_order"]).id,
                            datetime(2026, 3, 12, 15, 0, tzinfo=timezone.utc))
    assert ids(panel.list(desde="2026-03-10", hasta="2026-03-10")) == [by_order(panel, panel.aprobado)]
    assert ids(panel.list(desde="2026-03-11", hasta="2026-03-12")) == [by_order(panel, panel.rechazado)]
    assert len(ids(panel.list(hasta="2026-03-31"))) == 2
    assert len(ids(panel.list(desde="2026-04-01"))) == 2  # los dos pagos del club son de hoy


@pytest.mark.parametrize("params", [
    {"estado": "APROBADO"}, {"categoria": "OTRA"}, {"tipo": "ADMIN"}, {"desde": "10-03-2026"},
    {"page": 0}, {"limit": 0}, {"limit": LIMIT_MAX + 1},
])
def test_invalid_filters_are_400(panel, params):
    assert panel.list(**params).status_code == 400


def test_from_after_to_is_400(panel):
    response = panel.list(desde="2026-03-12", hasta="2026-03-10")
    assert response.status_code == 400 and "desde" in response.json()["detail"]


# -- paginación ------------------------------------------------------------------------------------------


def test_pages_cover_every_payment_once_newest_first(panel):
    first, second, third = (panel.list(limit=2, page=n).json() for n in (1, 2, 3))
    assert first["total"] == second["total"] == 4 and third["items"] == []
    assert (first["page"], first["limit"]) == (1, 2)
    seen = [row["id"] for row in first["items"] + second["items"]]
    assert len(set(seen)) == 4
    dates = [row["fecha"] for row in first["items"] + second["items"]]
    assert dates == sorted(dates, reverse=True)
    assert seen[0] == by_order(panel, panel.pendiente)  # the last one started


def test_filtered_total_counts_before_paging(panel):
    body = panel.list(tipo="CLUB", limit=1).json()
    assert body["total"] == 2 and len(body["items"]) == 1


def test_a_renewal_paid_in_advance_shows_its_period_as_scheduled(panel):
    ana = token(panel.ana, "player")
    renewal, response = panel.pay(ana, PLAN_USUARIO.id)
    assert response.status_code == 200, response.text
    rows = {row["buy_order"]: row for row in panel.list(estado="APPROVED", tipo="USUARIO").json()["items"]}
    first, second = rows[panel.aprobado["buy_order"]]["periodo"], rows[renewal["buy_order"]]["periodo"]
    assert first["estado"] == "VIGENTE"
    assert (second["estado"], second["vigente"]) == ("PROGRAMADO", False)
    assert second["fecha_inicio"] == first["fecha_fin"]

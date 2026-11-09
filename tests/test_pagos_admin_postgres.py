"""HU-57 contra PostgreSQL (opcional: requiere PAYMENTS_TEST_DATABASE_URL, como test_payment_postgres.py)."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from test_payment_postgres import base, build, pool_for  # noqa: F401  (base is a fixture)

from app.repositories.postgres_payment_repository import PostgresPaymentRepository
from app.services.pagos_admin_service import inicio_del_dia


def _order(repo, plan, *, usuario_id=None, club_id=None, n=0):
    owner = usuario_id or club_id
    return repo.create_transaccion({
        "usuario_id": usuario_id, "club_id": club_id, "iniciada_por": owner, "plan_id": plan.id,
        "buy_order": f"SM-admin-{n}", "session_id": str(owner), "token_ws": f"{n:064d}", "amount": plan.precio,
        "plan_nombre": plan.nombre, "duracion_dias": plan.duracion_dias, "beneficios": list(plan.beneficios)})


def test_admin_listing_joins_plan_and_period_and_filters_in_sql(base):
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        repo = PostgresPaymentRepository(pool)
        noop = lambda *args: None  # noqa: E731
        plan = repo.create_plan({"nombre": "Pro", "tipo": "USUARIO", "precio": 4990, "duracion_dias": 30}, audit=noop)
        convenio = repo.create_plan({"nombre": "Convenio U", "tipo": "USUARIO", "categoria": "CONVENIO",
                                     "institucion": "Universidad", "precio": 2990, "duracion_dias": 180}, audit=noop)
        club_plan = repo.create_plan({"nombre": "Club", "tipo": "CLUB", "precio": 14990, "duracion_dias": 30},
                                     audit=noop)
        ana, club = uuid4(), uuid4()
        aprobado = _order(repo, plan, usuario_id=ana, n=1)
        repo.settle_transaccion(aprobado.id, "APPROVED", {"authorization_code": "123456", "card_last4": "6623",
                                                          "response_code": 0})
        rechazado = _order(repo, convenio, usuario_id=uuid4(), n=2)
        repo.settle_transaccion(rechazado.id, "REJECTED", {"response_code": -1})
        del_club = _order(repo, club_plan, club_id=club, n=3)
        setup.execute("UPDATE transaccion_pago SET fecha_creacion = %s WHERE id = %s",
                      (datetime(2026, 3, 11, 2, 30, tzinfo=timezone.utc), rechazado.id))
        # A plan renamed later: the panel shows the name that was paid.
        repo.update_plan(plan.id, {"nombre": "Pro Plus"}, audit=noop)

        def listing(**filters):
            values = {"estado": None, "desde": None, "hasta": None, "categoria": None, "tipo": None,
                      "limit": 20, "offset": 0, **filters}
            rows, total = repo.list_pagos_admin(**values)
            return [r.id for r in rows], total, rows

        ids, total, rows = listing()
        assert total == 3 and ids[-1] == rechazado.id  # newest first
        row = next(r for r in rows if r.id == aprobado.id)
        assert (row.status, row.amount, row.authorization_code, row.card_last4) == ("APPROVED", 4990, "123456", "6623")
        assert (row.plan_nombre, row.plan_categoria, row.plan_tipo, row.usuario_id) == ("Pro", "PLAN", "USUARIO", ana)
        assert row.periodo_id is not None and (row.periodo_fin - row.periodo_inicio).days == 30
        assert next(r for r in rows if r.id == rechazado.id).periodo_id is None

        assert listing(estado="APPROVED")[:2] == ([aprobado.id], 1)
        assert listing(categoria="CONVENIO")[:2] == ([rechazado.id], 1)
        assert listing(tipo="CLUB")[:2] == ([del_club.id], 1)
        assert listing(tipo="USUARIO", estado="REJECTED")[:2] == ([rechazado.id], 1)
        day = datetime(2026, 3, 10).date()
        assert listing(desde=inicio_del_dia(day), hasta=inicio_del_dia(datetime(2026, 3, 11).date()))[:2] == (
            [rechazado.id], 1)
        page = listing(limit=1, offset=1)
        assert len(page[0]) == 1 and page[1] == 3
    finally:
        pool.close()

"""HU-55 on PostgreSQL: the unique notice, discarding on renewal and the gateway's read/confirm.
Only runs with PAYMENTS_TEST_DATABASE_URL (isolated schema, see test_payment_postgres)."""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from test_payment_postgres import base, build, pool_for  # noqa: F401 - pytest fixture
from app.repositories.postgres_payment_repository import PostgresPaymentRepository
from app.services.aviso_vencimiento import Resultado, revisar

UTC = ZoneInfo("UTC")


def _pagar(repository, plan, buyer, *, club_id=None, n=0):
    order = repository.create_transaccion({
        "usuario_id": None if club_id else buyer, "club_id": club_id, "iniciada_por": buyer, "plan_id": plan.id,
        "buy_order": f"SM-aviso-{n}-{uuid4().hex[:8]}", "session_id": str(buyer), "token_ws": uuid4().hex * 2,
        "amount": plan.precio, "plan_nombre": plan.nombre, "duracion_dias": 30, "beneficios": []})
    _, changed, suscripcion = repository.settle_transaccion(order.id, "APPROVED", {"response_code": 0})
    assert changed
    return suscripcion


def test_avisos_en_postgres_unicos_descartados_al_renovar_y_confirmados(base):  # noqa: F811
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        repository = PostgresPaymentRepository(pool)
        plan = repository.create_plan({"nombre": "Pro mensual", "tipo": "USUARIO", "precio": 3990,
                                       "duracion_dias": 30, "beneficios": []}, audit=lambda *a: None)
        club_plan = repository.create_plan({"nombre": "Club mensual", "tipo": "CLUB", "precio": 9990,
                                            "duracion_dias": 30, "beneficios": []}, audit=lambda *a: None)
        athlete, admin, club = uuid4(), uuid4(), uuid4()
        sus = _pagar(repository, plan, athlete)
        club_sus = _pagar(repository, club_plan, admin, club_id=club, n=1)
        ahora = sus.fecha_fin - timedelta(days=3)
        setup.execute("UPDATE suscripcion SET fecha_fin = %s WHERE id = %s", (sus.fecha_fin, club_sus.id))

        assert revisar(repository, ahora, UTC) == Resultado(avisados=2)
        assert revisar(repository, ahora, UTC) == Resultado()  # unique (suscripcion, tipo, fecha_fin)
        pendientes = repository.avisos_pendientes(ahora, 10)
        assert {(a.user_id, a.data["ruta"]) for a in pendientes} == {
            (athlete, f"/pago?plan={plan.id}"), (admin, f"/pago?plan={club_plan.id}")}

        # The athlete renews: their pending notice is no longer true and is discarded, never published.
        _pagar(repository, plan, athlete, n=2)
        assert [a.user_id for a in repository.avisos_pendientes(ahora, 10)] == [admin]
        assert revisar(repository, ahora, UTC) == Resultado(descartados=1)
        [club_aviso] = repository.avisos_pendientes(ahora, 10)
        assert repository.confirmar_avisos([club_aviso.id]) == 1
        assert repository.confirmar_avisos([club_aviso.id]) == 0
        assert repository.avisos_pendientes(ahora, 10) == []
        assert setup.execute("SELECT count(*) FILTER (WHERE descartado), count(*) FILTER (WHERE published_at IS NULL) "
                             "FROM aviso_pendiente").fetchone() == (1, 0)
    finally:
        pool.close()

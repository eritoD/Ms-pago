from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from test_payment_postgres import base, build, pool_for, MIGRATION
from app.repositories.postgres_payment_repository import PostgresPaymentRepository
from test_pagos import Pagos, SETTINGS, PAY
from app.main import create_app
from fastapi.testclient import TestClient


def test_backfill_dos_veces_renovacion_vencimiento_y_duenos_separados(base):
    setup, schema, _ = base
    setup.execute(MIGRATION.read_text(encoding="utf-8"))
    plan = setup.execute("INSERT INTO plan_suscripcion(nombre,tipo,precio,duracion_dias) "
                         "VALUES ('Original','USUARIO',4990,30) RETURNING id").fetchone()[0]
    owner = uuid4()
    approvals = [datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 10, tzinfo=timezone.utc),
                 datetime(2026, 4, 1, tzinfo=timezone.utc)]
    for i, approved in enumerate(approvals):
        setup.execute("""INSERT INTO transaccion_pago
            (usuario_id,iniciada_por,plan_id,buy_order,session_id,token_ws,amount,status,
             plan_nombre,duracion_dias,beneficios,fecha_creacion,fecha_actualizacion,transaction_date)
            VALUES (%s,%s,%s,%s,'sesion',%s,4990,'APPROVED','Nombre pagado',30,'["Beneficio pagado"]',%s,%s,%s)""",
            (owner, owner, plan, f"orden-{i}", f"token-{i}", approved - timedelta(hours=1), approved,
             approved - timedelta(minutes=10)))
    # El mismo UUID en otro tipo de dueño jamás comparte la cadena de renovación.
    setup.execute("""INSERT INTO transaccion_pago
        (club_id,iniciada_por,plan_id,buy_order,session_id,token_ws,amount,status,
         plan_nombre,duracion_dias,beneficios,fecha_actualizacion)
        VALUES (%s,%s,%s,'orden-club','sesion','token-club',4990,'APPROVED','Club pagado',30,'[]',%s)""",
        (owner, owner, plan, approvals[0]))
    migration = MIGRATION.with_name("002_historial_suscripcion.sql").read_text(encoding="utf-8")
    setup.execute(migration)
    before = setup.execute("SELECT * FROM suscripcion_periodo ORDER BY id").fetchall()
    setup.execute(migration)
    assert setup.execute("SELECT * FROM suscripcion_periodo ORDER BY id").fetchall() == before
    rows = setup.execute("SELECT fecha_inicio,fecha_fin,plan_nombre,precio_pagado,beneficios "
                         "FROM suscripcion_periodo WHERE usuario_id=%s ORDER BY fecha_inicio", (owner,)).fetchall()
    assert len(rows) == 3
    assert rows[0][0] == approvals[0]
    assert rows[1][0] == rows[0][1]
    assert rows[2][0] == approvals[2]
    assert all(row[1] - row[0] == timedelta(days=30) for row in rows)
    assert all(row[2:] == ("Nombre pagado", 4990, ["Beneficio pagado"]) for row in rows)
    assert setup.execute("SELECT fecha_inicio FROM suscripcion_periodo WHERE club_id=%s", (owner,)).fetchone()[0] == approvals[0]


def test_periodo_y_suscripcion_se_revierten_juntos_si_falla_insert(base):
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        repo = PostgresPaymentRepository(pool)
        plan = repo.create_plan({"nombre": "Prueba atómica", "tipo": "USUARIO", "precio": 4990,
                                 "duracion_dias": 30}, audit=lambda *args: None)
        owner = uuid4()
        payment = repo.create_transaccion({"usuario_id": owner, "club_id": None, "iniciada_por": owner,
            "plan_id": plan.id, "buy_order": "atomica", "session_id": "sesion", "token_ws": "token",
            "amount": 4990, "plan_nombre": plan.nombre, "duracion_dias": 30, "beneficios": []})
        setup.execute("ALTER TABLE suscripcion_periodo ADD CONSTRAINT prueba_error CHECK (precio_pagado < 100)")
        with pytest.raises(psycopg.errors.CheckViolation):
            repo.settle_transaccion(payment.id, "APPROVED", {"response_code": 0})
        assert repo.get_transaccion(token_ws="token").status == "PENDING"
        assert repo.get_suscripcion(usuario_id=owner) is None
        assert repo.get_periodo(payment.id) is None
    finally:
        pool.close()


def test_historial_http_propiedad_con_postgres_y_renovaciones_concurrentes(base):
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        pagos = Pagos()
        repo = PostgresPaymentRepository(pool)
        plan = repo.create_plan({"nombre": "Plan historial", "tipo": "USUARIO", "precio": 4990,
                                 "duracion_dias": 30}, audit=lambda *args: None)
        pagos.repository = repo
        pagos.client = TestClient(create_app(repository=repo, webpay=pagos.webpay,
            webpay_settings=SETTINGS, clubs_directory=pagos.clubs))
        _, headers = pagos.athlete()
        _, other = pagos.athlete()
        orders = [pagos.started(headers, plan.id) for _ in range(4)]
        for order in orders:
            pagos.webpay.decide(order["token_ws"], "aprobar")
        with ThreadPoolExecutor(4) as executor:
            results = list(executor.map(lambda order: pagos.commit(headers, token_ws=order["token_ws"]), orders))
        assert all(r.status_code == 200 for r in results)
        periods = pagos.client.get(f"{PAY}/subscriptions/me/history", headers=headers).json()["items"]
        assert len(periods) == 4
        for i in range(3):
            assert periods[i + 1]["fecha_fin"] == periods[i]["fecha_inicio"]
        assert pagos.client.get(f"{PAY}/history/me", headers=other).json()["total"] == 0
        for payment in results:
            id = payment.json()["transaccion_id"]
            assert pagos.client.get(f"{PAY}/history/{id}/receipt", headers=other).status_code == 404
            assert pagos.client.get(f"{PAY}/history/{id}/receipt", headers=headers).status_code == 200
    finally:
        pool.close()

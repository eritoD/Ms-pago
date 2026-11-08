"""Optional integration tests. Only PAYMENTS_TEST_DATABASE_URL enables SQL writes in an isolated schema."""
from __future__ import annotations
import os
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from psycopg import sql
from psycopg_pool import ConnectionPool
from test_planes_admin import ADMIN, PAY, Gestion
from test_planes_admin_panel import deactivated
from app.repositories.postgres_payment_repository import PostgresPaymentRepository

MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "001_payments_schema.sql"

@pytest.fixture
def base():
    dsn = os.getenv("PAYMENTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set PAYMENTS_TEST_DATABASE_URL to an isolated disposable payments test database")
    schema = "payments_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as setup:
        setup.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            setup.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            yield setup, schema, dsn
        finally:
            setup.execute("RESET search_path")
            setup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

def build(setup, schema):
    for migration in sorted(MIGRATION.parent.glob("*.sql")):
        setup.execute(migration.read_text(encoding="utf-8"))

def pool_for(dsn, schema):
    pool = ConnectionPool(dsn, kwargs={"options": f"-c search_path={schema}"}, min_size=1, max_size=4, open=True)
    pool.wait()
    return pool


def test_final_schema_is_repeatable_and_has_only_financial_foreign_keys(base):
    setup, schema, _ = base
    build(setup, schema)
    setup.execute("INSERT INTO plan_suscripcion (id, nombre, tipo, precio, duracion_dias) "
                  "OVERRIDING SYSTEM VALUE VALUES (47, 'Importado', 'USUARIO', 4990, 30)")
    build(setup, schema)
    assert setup.execute("SELECT id, nombre, precio FROM plan_suscripcion").fetchall() == [(47, "Importado", 4990)]
    tables = {row[0] for row in setup.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s", (schema,))}
    assert tables == {"plan_suscripcion", "transaccion_pago", "suscripcion", "suscripcion_periodo"}
    foreign_tables = {row[0] for row in setup.execute(
        "SELECT confrelid::regclass::text FROM pg_constraint WHERE connamespace = %s::regnamespace AND contype = 'f'",
        (schema,))}
    assert foreign_tables == {"plan_suscripcion", "transaccion_pago"}


def test_concurrent_settlement_of_one_order_extends_once_and_preserves_its_snapshot(base):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        repository = PostgresPaymentRepository(pool)
        plan = repository.create_plan({"nombre": "Plan inicial", "tipo": "USUARIO", "precio": 4990,
            "duracion_dias": 30, "beneficios": ["Uno"]}, audit=lambda *args: None)
        buyer = uuid4()
        order = repository.create_transaccion({"usuario_id": buyer, "club_id": None, "iniciada_por": buyer,
            "plan_id": plan.id, "buy_order": "SM-concurrent", "session_id": str(buyer), "token_ws": "t" * 64,
            "amount": 4990, "plan_nombre": plan.nombre, "duracion_dias": 30, "beneficios": ["Uno"]})
        repository.update_plan(plan.id, {"precio": 9990, "duracion_dias": 60, "beneficios": ["Otro"]},
                               audit=lambda *args: None)
        with ThreadPoolExecutor(4) as executor:
            results = list(executor.map(lambda _: repository.settle_transaccion(
                order.id, "APPROVED", {"response_code": 0}), range(4)))
        assert sum(changed for _, changed, _ in results) == 1
        assert setup.execute("SELECT count(*) FROM suscripcion_periodo").fetchone()[0] == 1
        subscription = repository.get_suscripcion(usuario_id=buyer)
        assert subscription.fecha_fin - subscription.fecha_inicio == timedelta(days=30)
        assert (subscription.plan.precio, subscription.plan.duracion_dias, subscription.plan.beneficios) == (4990, 30, ("Uno",))
        period = repository.get_periodo(order.id)
        assert (period.plan_nombre, period.precio_pagado, period.duracion_dias, period.beneficios) == (
            "Plan inicial", 4990, 30, ["Uno"])
        assert period.fecha_inicio == subscription.fecha_inicio and period.fecha_fin == subscription.fecha_fin
        final, changed, _ = repository.settle_transaccion(order.id, "REJECTED", None)
        assert final.status == "APPROVED" and changed is False
    finally:
        pool.close()

def test_the_whole_flow_with_the_real_repository_and_the_audit_rollback(base):
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        g = Gestion(repository=PostgresPaymentRepository(pool))
        created = g.create(nombre="Pro Mensual", precio=4990, duracion_dias=30, beneficios=["Uno bueno", "Otro bueno"])
        assert created.status_code == 201, created.text
        plan_id = created.json()["id"]
        assert g.create().status_code == 409                                                      # name taken (unique index)
        assert g.create(nombre="  PRO   mensual").status_code == 409
        convenio = g.create(nombre="Convenio Ñuñoa", tipo="CLUB", categoria="CONVENIO", precio=0, beneficios=[])
        assert convenio.status_code == 201 and convenio.json()["categoria"] == "CONVENIO"

        headers, receipt = g.buy(plan_id)                                                         # pays with the PostgreSQL repo
        before = g.subscription(headers).json()
        assert before["plan"]["precio"] == 4990 and before["plan"]["beneficios"] == ["Uno bueno", "Otro bueno"]
        g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 9990, "duracion_dias": 90, "beneficios": ["Cambio"]})
        assert g.subscription(headers).json() == before                                           # paid terms untouched
        assert g.client.get(f"{PAY}/plans").json()[0]["precio"] in (9990, 0)
        assert g.plans(estado="activo").json()["items"][-1]["suscripciones_vigentes"] == 1

        # A second buyer gets the new terms.
        _, receipt2 = g.buy(plan_id)
        assert receipt2["amount"] == 9990 and receipt2["suscripcion"]["plan"]["duracion_dias"] == 90

        # Audit failure: nothing is saved and the answer is 503.
        g.auditor.down = True
        assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 1}).status_code == 503
        assert g.create(nombre="No queda").status_code == 503
        assert g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin).status_code == 503
        g.auditor.down = False
        assert setup.execute("SELECT precio, is_active FROM plan_suscripcion WHERE id = %s", (plan_id,)).fetchone() == (9990, True)
        assert setup.execute("SELECT count(*) FROM plan_suscripcion WHERE nombre = 'No queda'").fetchone()[0] == 0

        # Deactivate: hidden from the catalog, row kept, subscription running, not buyable.
        assert g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin, json={"motivo": "Se retira"}).status_code == 200
        assert plan_id not in [p["id"] for p in g.client.get(f"{PAY}/plans").json()]
        assert setup.execute("SELECT is_active FROM plan_suscripcion WHERE id = %s", (plan_id,)).fetchone()[0] is False
        assert g.subscription(headers).json()["estado"] == "ACTIVE"
        assert g.client.post(f"{PAY}/webpay/initiate", headers=headers, json={"plan_id": plan_id}).status_code == 404
        assert g.plans(estado="inactivo").json()["items"][0]["suscripciones_vigentes"] == 2
        assert g.create(nombre="Pro Mensual").status_code == 201                                  # name free again
        actions = [e["action"] for e in g.auditor.entries]
        assert actions == ["PLAN_CREATED", "PLAN_CREATED", "PLAN_UPDATED", "PLAN_DEACTIVATED", "PLAN_CREATED"]
    finally:
        pool.close()

def test_a_concurrent_rename_to_the_same_name_creates_only_one_plan(base):
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        g = Gestion(repository=PostgresPaymentRepository(pool))
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as executor:
            codes = list(executor.map(lambda _: g.create(nombre="Carrera").status_code, range(4)))
        assert sorted(codes) == [201, 409, 409, 409]
        assert setup.execute("SELECT count(*) FROM plan_suscripcion WHERE nombre = 'Carrera'").fetchone()[0] == 1
    finally:
        pool.close()

def test_a_concurrent_reactivation_and_creation_of_the_same_name_leaves_one_active_plan(base):
    from concurrent.futures import ThreadPoolExecutor
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        g = Gestion(repository=PostgresPaymentRepository(pool))
        plan_id = deactivated(g, nombre="Carrera dos")
        jobs = [lambda: g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin).status_code,
                lambda: g.create(nombre="Carrera dos").status_code,
                lambda: g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin).status_code]
        with ThreadPoolExecutor(3) as executor:
            codes = list(executor.map(lambda job: job(), jobs))
        assert codes.count(409) >= 1
        assert setup.execute("SELECT count(*) FROM plan_suscripcion WHERE lower(nombre) = 'carrera dos' AND is_active").fetchone()[0] == 1
    finally:
        pool.close()


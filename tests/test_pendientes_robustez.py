"""Doble clic en «pagar» (se reutiliza el PENDING reciente) y conciliación de PENDING viejos."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.conciliacion import ConciliadorWorker, conciliar
from app.services.pago_service import PagoService
from app.services.webpay import WebpayError
from test_webpay_integration_flow import PAY, SETTINGS, commit, finish_browser, initiate, payments  # noqa: F401


def pago_service(p):
    return PagoService(p.repository, p.gateway, p.settings)


def envejecer(p, started, minutos):
    t = p.repository.get_transaccion(token_ws=started["token_ws"])
    p.repository._transacciones[t.id].fecha_creacion = datetime.now(timezone.utc) - timedelta(minutes=minutos)
    return t.id


# -- doble clic -------------------------------------------------------------------------------------------


def test_double_click_reuses_the_pending_payment(payments):
    p = payments()
    first, second = initiate(p), initiate(p)
    assert second["transaccion_id"] == first["transaccion_id"]
    assert second["token_ws"] == first["token_ws"] and second["buy_order"] == first["buy_order"]
    assert second["url"] == first["url"] and second["status"] == "PENDING"
    assert len(p.repository._transacciones) == 1


def test_the_other_client_or_an_old_pending_payment_gets_a_new_one(payments):
    p = payments()
    app = initiate(p)
    assert initiate(p, client="web")["token_ws"] != app["token_ws"]  # other return_url: cannot reuse
    envejecer(p, app, 11)  # older than the 10 minute window
    assert initiate(p)["token_ws"] != app["token_ws"]


def test_a_settled_payment_is_not_reused(payments):
    p = payments()
    first = initiate(p)
    assert finish_browser(p, first, "rechazar").status_code == 303
    assert initiate(p)["token_ws"] != first["token_ws"]


# -- conciliación ------------------------------------------------------------------------------------------


def test_reconciliation_ignores_recent_pending_payments(payments):
    p = payments()
    initiate(p)
    assert conciliar(pago_service(p)).revisadas == 0


def test_reconciliation_approves_an_authorized_payment_once(payments):
    p = payments()
    started = initiate(p)
    p.gateway.decide(started["token_ws"], "aprobar")
    p.gateway.commit_transaction(started["token_ws"])  # authorized at Webpay, but the answer/return never reached us
    envejecer(p, started, 31)
    service = pago_service(p)
    assert conciliar(service).aprobadas == 1
    assert conciliar(service).revisadas == 0  # safe to run again
    sub = p.client.get(f"{PAY}/subscription/me", headers=p.headers)
    assert sub.status_code == 200
    first_end = sub.json()["fecha_fin"]
    conciliar(service)
    assert p.client.get(f"{PAY}/subscription/me", headers=p.headers).json()["fecha_fin"] == first_end
    assert p.repository.get_transaccion(token_ws=started["token_ws"]).status == "APPROVED"


def test_reconciliation_closes_abandoned_payments_without_touching_the_subscription(payments):
    p = payments()
    started = initiate(p)
    envejecer(p, started, 45)  # the form was never completed: INITIALIZED
    assert conciliar(pago_service(p)).canceladas == 1
    assert p.repository.get_transaccion(token_ws=started["token_ws"]).status == "CANCELLED"
    assert p.client.get(f"{PAY}/subscription/me", headers=p.headers).status_code == 404


def test_reconciliation_tolerates_transbank_being_down(payments):
    p = payments()
    started = initiate(p)
    envejecer(p, started, 45)

    def down(token):
        raise WebpayError("Webpay could not be reached (ConnectError)")
    p.gateway.transaction_status = down
    result = conciliar(pago_service(p))
    assert (result.revisadas, result.errores, result.canceladas) == (1, 1, 0)
    assert p.repository.get_transaccion(token_ws=started["token_ws"]).status == "PENDING"  # tried again next time


def test_reconciliation_closes_a_token_transbank_no_longer_knows(payments):
    p = payments()
    started = initiate(p)
    envejecer(p, started, 45)

    def unknown(token):
        raise WebpayError("Webpay answered HTTP 422", 422)
    p.gateway.transaction_status = unknown
    assert conciliar(pago_service(p)).canceladas == 1


def test_worker_run_once_never_raises(payments):
    p = payments()

    class Broken:
        def conciliar_pendientes(self, *a, **k):
            raise RuntimeError("db down")
    assert ConciliadorWorker(Broken(), interval=60).run_once() is None


# -- pago vencido -------------------------------------------------------------------------------------------


def test_confirming_an_expired_payment_is_a_clear_cancellation_not_a_generic_502(payments):
    p = payments()
    started = initiate(p)

    def unknown(token):
        raise WebpayError("Webpay answered HTTP 422", 422)
    p.gateway.commit_transaction = unknown
    p.gateway.transaction_status = unknown
    response = commit(p, started)
    assert response.status_code == 400  # same as a cancelled payment: comprobante + known message
    assert response.json()["transaccion"]["status"] == "CANCELLED"

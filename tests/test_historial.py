from uuid import uuid4

import pytest

from test_pagos import PAY, Pagos
from payment_support import token


@pytest.mark.parametrize("club", [False, True])
def test_historial_propio_estados_paginacion_comprobante_y_repeticion(club):
    pagos = Pagos()
    if club:
        _, headers, owner = pagos.club_admin()
        _, other, _ = pagos.club_admin()
        plan = 3
    else:
        owner, headers = pagos.athlete()
        _, other = pagos.athlete()
        plan = 1
    started, approved = pagos.pay(headers, plan)
    approved_id = approved.json()["transaccion_id"]
    pagos.pay(headers, plan, "rechazar")
    cancelled = pagos.started(headers, plan)
    pagos.commit(headers, tbk_token=cancelled["token_ws"], tbk_orden_compra=cancelled["buy_order"],
                 tbk_id_sesion=cancelled["session_id"])
    pagos.started(headers, plan)
    for _ in range(3):
        assert pagos.commit(headers, token_ws=started["token_ws"]).status_code == 200
    history = pagos.client.get(f"{PAY}/history/me?page_size=2", headers=headers)
    assert history.status_code == 200, history.text
    assert history.headers["cache-control"] == "no-store"
    assert history.json()["total"] == 4 and len(history.json()["items"]) == 2
    second = pagos.client.get(f"{PAY}/history/me?page=2&page_size=2", headers=headers).json()
    assert {t["status"] for t in history.json()["items"] + second["items"]} == {
        "APPROVED", "REJECTED", "CANCELLED", "PENDING"}
    periods = pagos.client.get(f"{PAY}/subscriptions/me/history", headers=headers).json()
    assert periods["total"] == 1 and periods["items"][0]["transaccion_id"] == approved_id
    period = periods["items"][0]
    assert period["vigente"] is True and period["precio_pagado"] == approved.json()["amount"]
    receipt = pagos.client.get(f"{PAY}/history/{approved_id}/receipt", headers=headers)
    assert receipt.status_code == 200
    assert 'attachment;' in receipt.headers["content-disposition"]
    assert receipt.headers["content-type"].startswith("text/html")
    for text in ["SportMatch", started["buy_order"], approved.json()["plan"]["nombre"],
                 "****" + approved.json()["card_last4"], "Código de autorización", "Período", "Monto CLP"]:
        assert text in receipt.text
    assert "token_ws" not in receipt.text and "session_id" not in receipt.text
    # Ningún identificador de dueño que mande el cliente cambia el alcance.
    for route in ["history/me", "subscriptions/me/history"]:
        response = pagos.client.get(f"{PAY}/{route}?club_id={owner}&usuario_id={owner}", headers=other)
        assert response.json()["total"] == 0
    assert pagos.client.get(f"{PAY}/history/{approved_id}/receipt", headers=other).status_code == 404
    assert pagos.client.get(f"{PAY}/history/{uuid4()}/receipt", headers=other).status_code == 404
    all_rows = history.json()["items"] + second["items"]
    for row in all_rows:
        if row["status"] != "APPROVED":
            assert row["periodo"] is None
            assert pagos.client.get(f"{PAY}/history/{row['transaccion_id']}/receipt", headers=headers).status_code == 404


def test_separacion_deportista_club_y_representante_actual():
    pagos = Pagos()
    athlete, headers = pagos.athlete()
    _, approved = pagos.pay(headers, 1)
    _, club_headers, club_id = pagos.club_admin()
    _, club_payment = pagos.pay(club_headers, 3)
    for requester, foreign in [(headers, club_payment), (club_headers, approved)]:
        receipt_id = foreign.json()["transaccion_id"]
        assert pagos.client.get(f"{PAY}/history/{receipt_id}/receipt", headers=requester).status_code == 404
        assert pagos.client.get(f"{PAY}/history/me", headers=requester).json()["total"] == 1
    # El representante se resuelve por directorio en cada consulta, no por iniciada_por.
    new_admin = uuid4()
    pagos.clubs.add(new_admin, "Club de prueba", club_id=club_id)
    assert pagos.client.get(f"{PAY}/history/me", headers=token(new_admin)).json()["total"] == 1
    pagos.clubs.by_admin.pop(new_admin)
    assert pagos.client.get(f"{PAY}/history/me", headers=token(new_admin)).status_code == 404


def test_periodos_contiguos_y_snapshot_inalterable():
    pagos = Pagos()
    _, headers = pagos.athlete()
    pagos.pay(headers, 1)
    pagos.pay(headers, 1)
    periods = pagos.client.get(f"{PAY}/subscriptions/me/history", headers=headers).json()["items"]
    assert len(periods) == 2
    assert periods[1]["fecha_fin"] == periods[0]["fecha_inicio"]
    assert periods[0]["vigente"] is False and periods[1]["vigente"] is True


@pytest.mark.parametrize("route", ["history/me", "subscriptions/me/history", f"history/{uuid4()}/receipt"])
def test_historial_exige_autenticacion(route):
    assert Pagos().client.get(f"{PAY}/{route}").status_code == 401


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101", "page=no"])
def test_paginacion_invalida(query):
    pagos = Pagos()
    _, headers = pagos.athlete()
    assert pagos.client.get(f"{PAY}/history/me?{query}", headers=headers).status_code == 400


def test_comprobante_escapa_html_y_omite_tarjeta_invalida():
    pagos = Pagos()
    _, headers = pagos.athlete()
    _, result = pagos.pay(headers, 1)
    transaction = pagos.repository._transacciones[next(iter(pagos.repository._transacciones))]
    transaction.authorization_code = '<script>alert(1)</script>'
    transaction.card_last4 = "1234567890123456"
    receipt = pagos.client.get(f"{PAY}/history/{result.json()['transaccion_id']}/receipt", headers=headers)
    assert '<script>' not in receipt.text
    assert '&lt;script&gt;' in receipt.text
    assert "1234567890123456" not in receipt.text

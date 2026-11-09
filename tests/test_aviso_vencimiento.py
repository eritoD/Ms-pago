"""HU-55: notice 3 days before and on the day a subscription ends, through the aviso_pendiente outbox."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from test_pagos import PLAN_CLUB, Pagos
from payment_support import token

from app.services import aviso_vencimiento
from app.services.aviso_vencimiento import AvisoVencimientoWorker, Resultado, revisar

UTC = ZoneInfo("UTC")
SANTIAGO = ZoneInfo("America/Santiago")
T0 = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)
INTERNO = "/internal/v1/avisos"
SECRETO = "internal-secret-for-tests-with-32-bytes-or-more"


def deportista_con_plan(pagos: Pagos, fin: datetime, plan_id: int = 1):
    athlete_id, headers = pagos.athlete()
    _, approved = pagos.pay(headers, plan_id)
    assert approved.status_code == 200, approved.text
    pagos.repository.set_suscripcion_fin(usuario_id=athlete_id, fecha_fin=fin)
    return athlete_id, headers


def avisos(pagos: Pagos):
    return pagos.repository.avisos_registrados()


def test_avisa_tres_dias_antes_y_el_dia_del_vencimiento_hacia_el_pago_del_mismo_plan():
    pagos = Pagos()
    fin = T0 + timedelta(days=3)
    athlete_id, _ = deportista_con_plan(pagos, fin)

    assert revisar(pagos.repository, T0, UTC) == Resultado(avisados=1)
    [previo] = avisos(pagos)
    aviso = previo["aviso"]
    assert (aviso.tipo, aviso.user_id, aviso.fecha_fin, aviso.kind) == ("PREVIO", athlete_id, fin, "suscripcion")
    assert aviso.title == "Tu plan vence pronto"
    assert "Deportista Mensual" in aviso.body and "en 3 días" in aviso.body and "13-01-2030" in aviso.body
    assert aviso.data["screen"] == "pago" and aviso.data["planId"] == "1" and aviso.data["ruta"] == "/pago?plan=1"
    assert "clubId" not in aviso.data

    dia = fin - timedelta(hours=2)  # the same day, before it ends
    assert revisar(pagos.repository, dia, UTC) == Resultado(avisados=1)
    hoy = avisos(pagos)[1]["aviso"]
    assert (hoy.tipo, hoy.title, hoy.data["ruta"]) == ("HOY", "Tu plan vence hoy", "/pago?plan=1")
    assert "vence hoy a las 12:00" in hoy.body and "sin perder tus datos" in hoy.body


def test_ejecutar_dos_veces_no_duplica_el_aviso():
    pagos = Pagos()
    deportista_con_plan(pagos, T0 + timedelta(days=3))
    assert revisar(pagos.repository, T0, UTC).avisados == 1
    assert revisar(pagos.repository, T0, UTC).avisados == 0
    assert revisar(pagos.repository, T0 + timedelta(hours=5), UTC).avisados == 0  # another pass that day
    assert len(avisos(pagos)) == 1


@pytest.mark.parametrize("fin, esperado", [
    (T0 - timedelta(minutes=1), 0),  # EXPIRED: it is never notified
    (T0 + timedelta(days=4), 0),  # too early
    (T0 + timedelta(days=10), 0),
    (T0 + timedelta(days=3, hours=11), 1),  # still the 3rd calendar day
])
def test_solo_suscripciones_activas_dentro_de_los_tres_dias(fin, esperado):
    pagos = Pagos()
    deportista_con_plan(pagos, fin)
    assert revisar(pagos.repository, T0, UTC).avisados == esperado


def test_los_dias_se_cuentan_en_la_hora_de_chile():
    pagos = Pagos()
    # 23:00 in Santiago (UTC-3 in January) of the 12th is 02:00 UTC of the 13th.
    fin = datetime(2030, 1, 13, 2, 0, tzinfo=timezone.utc)
    deportista_con_plan(pagos, fin)
    ahora = datetime(2030, 1, 12, 23, 30, tzinfo=timezone.utc)  # 20:30 of the 12th in Santiago
    assert aviso_vencimiento.decidir(pagos.repository.suscripciones_por_vencer(ahora, fin)[0], ahora, UTC) == "PREVIO"
    assert revisar(pagos.repository, ahora, SANTIAGO).avisados == 1
    assert avisos(pagos)[0]["aviso"].tipo == "HOY" and "a las 23:00" in avisos(pagos)[0]["aviso"].body


def test_si_se_perdio_el_tercer_dia_avisa_igual_con_los_dias_que_quedan():
    pagos = Pagos()
    deportista_con_plan(pagos, T0 + timedelta(days=1))
    assert revisar(pagos.repository, T0, UTC).avisados == 1
    assert "vence mañana" in avisos(pagos)[0]["aviso"].body


def test_un_pago_recien_hecho_de_pocos_dias_no_recibe_el_aviso_previo():
    pagos = Pagos()
    athlete_id, _ = deportista_con_plan(pagos, T0 + timedelta(days=2))
    pagos.repository._suscripciones[("u", athlete_id)]["fecha_inicio"] = T0 - timedelta(hours=1)
    assert revisar(pagos.repository, T0, UTC).avisados == 0
    assert revisar(pagos.repository, T0 + timedelta(days=2) - timedelta(hours=1), UTC).avisados == 1  # HOY sí


def test_una_suscripcion_renovada_descarta_el_aviso_pendiente_y_no_vuelve_a_avisar():
    pagos = Pagos()
    athlete_id, headers = deportista_con_plan(pagos, T0 + timedelta(days=3))
    assert revisar(pagos.repository, T0, UTC).avisados == 1
    assert len(pagos.repository.avisos_pendientes(T0, 10)) == 1

    _, renewed = pagos.pay(headers, 1)  # renewal: fecha_fin moves 30 days later
    assert renewed.status_code == 200
    assert pagos.repository.avisos_pendientes(T0, 10) == []  # the gateway never gets it
    assert revisar(pagos.repository, T0, UTC) == Resultado(descartados=1)
    assert avisos(pagos)[0]["descartado"] is True and avisos(pagos)[0]["published_at"] is not None

    nuevo_fin = pagos.repository.get_suscripcion(usuario_id=athlete_id).fecha_fin
    assert revisar(pagos.repository, nuevo_fin - timedelta(days=3), UTC).avisados == 1  # the new period does
    assert len(avisos(pagos)) == 2


def test_una_suscripcion_vencida_sin_publicar_se_descarta():
    pagos = Pagos()
    fin = T0 + timedelta(days=3)
    deportista_con_plan(pagos, fin)
    revisar(pagos.repository, T0, UTC)
    despues = fin + timedelta(minutes=1)
    assert pagos.repository.avisos_pendientes(despues, 10) == []
    assert revisar(pagos.repository, despues, UTC) == Resultado(descartados=1)


def test_el_club_avisa_a_quien_pago_y_enlaza_tambien_a_mi_suscripcion_en_la_web():
    pagos = Pagos()
    owner, headers, club_id = pagos.club_admin()
    assert pagos.pay(headers, PLAN_CLUB.id)[1].status_code == 200
    pagos.repository.set_suscripcion_fin(club_id=club_id, fecha_fin=T0 + timedelta(days=3))
    assert revisar(pagos.repository, T0, UTC).avisados == 1
    aviso = avisos(pagos)[0]["aviso"]
    assert aviso.user_id == owner and "de tu club" in aviso.body
    assert aviso.data == {"screen": "pago", "planId": str(PLAN_CLUB.id), "ruta": f"/pago?plan={PLAN_CLUB.id}",
                          "suscripcionId": aviso.data["suscripcionId"], "clubId": str(club_id),
                          "rutaWeb": "/suscripcion"}


def test_si_el_plan_se_retiro_del_catalogo_el_aviso_abre_los_planes():
    pagos = Pagos()
    deportista_con_plan(pagos, T0 + timedelta(days=3))
    pagos.repository.deactivate_plan(1, audit=lambda *args: None, motivo="Fin de campaña")
    revisar(pagos.repository, T0, UTC)
    aviso = avisos(pagos)[0]["aviso"]
    assert aviso.data["screen"] == "planes" and "ruta" not in aviso.data and "ya no se ofrece" in aviso.body


# -- internal endpoints used by the gateway ------------------------------------------------------------------


def test_endpoints_internos_exigen_el_secreto_de_servicio(monkeypatch):
    pagos = Pagos()
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    assert pagos.client.get(f"{INTERNO}/pendientes",
                            headers={"X-Internal-Service-Token": SECRETO}).status_code == 503
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SECRETO)
    athlete_id, user_headers = pagos.athlete()
    for headers in ({}, {"X-Internal-Service-Token": "otro"}, user_headers):
        assert pagos.client.get(f"{INTERNO}/pendientes", headers=headers).status_code == 401
        assert pagos.client.post(f"{INTERNO}/confirmar", json={"ids": [1]}, headers=headers).status_code == 401


def test_el_gateway_lee_los_pendientes_y_al_confirmar_no_vuelven(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SECRETO)
    pagos = Pagos()
    ahora = datetime.now(timezone.utc)
    athlete_id, _ = deportista_con_plan(pagos, ahora + timedelta(days=3))
    pagos.repository._suscripciones[("u", athlete_id)]["fecha_inicio"] = ahora - timedelta(days=27)
    assert revisar(pagos.repository, ahora, UTC).avisados == 1
    headers = {"X-Internal-Service-Token": SECRETO}

    response = pagos.client.get(f"{INTERNO}/pendientes", headers=headers)
    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["user_id"] == str(athlete_id) and item["kind"] == "suscripcion"
    assert item["data"]["ruta"] == "/pago?plan=1" and item["title"] == "Tu plan vence pronto"
    # Reading twice (e.g. a gateway crash before confirming) returns the same notice.
    assert pagos.client.get(f"{INTERNO}/pendientes", headers=headers).json()["items"] == [item]

    confirmed = pagos.client.post(f"{INTERNO}/confirmar", json={"ids": [item["id"]]}, headers=headers)
    assert confirmed.json() == {"confirmados": 1}
    assert pagos.client.post(f"{INTERNO}/confirmar", json={"ids": [item["id"]]}, headers=headers).json() == {
        "confirmados": 0}
    assert pagos.client.get(f"{INTERNO}/pendientes", headers=headers).json()["items"] == []
    assert pagos.client.post(f"{INTERNO}/confirmar", json={"ids": []}, headers=headers).status_code == 400


# -- worker ------------------------------------------------------------------------------------------------------


def test_worker_registra_fallos_y_se_detiene_limpio(caplog):
    class Broken:
        calls = threading.Event()

        def descartar_avisos_obsoletos(self, ahora):
            Broken.calls.set()
            raise RuntimeError("base caída")

    worker = AvisoVencimientoWorker(Broken(), interval=3600, zona=UTC, clock=lambda: T0)
    with caplog.at_level(logging.ERROR, logger="app.services.aviso_vencimiento"):
        assert worker.run_once() is None
    assert "suscripciones por vencer" in caplog.text
    worker.start()
    assert Broken.calls.wait(5)
    worker.stop(timeout=5)
    assert not worker._thread.is_alive()


def test_worker_aplica_la_regla_con_su_reloj():
    pagos = Pagos()
    deportista_con_plan(pagos, T0 + timedelta(days=3))
    worker = AvisoVencimientoWorker(pagos.repository, zona=UTC, clock=lambda: T0)
    assert worker.run_once() == Resultado(avisados=1)
    assert worker.run_once() == Resultado()


def test_worker_se_desactiva_y_configura_por_entorno(monkeypatch):
    monkeypatch.delenv(aviso_vencimiento.ENV_ENABLED, raising=False)
    assert aviso_vencimiento.enabled() is True
    for value in ("false", "0", "OFF", " no "):
        monkeypatch.setenv(aviso_vencimiento.ENV_ENABLED, value)
        assert aviso_vencimiento.enabled() is False
    monkeypatch.delenv(aviso_vencimiento.ENV_INTERVALO, raising=False)
    assert aviso_vencimiento.intervalo() == 3600
    monkeypatch.setenv(aviso_vencimiento.ENV_INTERVALO, "86400")
    assert aviso_vencimiento.intervalo() == 86400
    for value in ("abc", "5", "100000"):
        monkeypatch.setenv(aviso_vencimiento.ENV_INTERVALO, value)
        with pytest.raises(RuntimeError):
            aviso_vencimiento.intervalo()
    monkeypatch.delenv(aviso_vencimiento.ENV_ZONA, raising=False)
    assert aviso_vencimiento.zona() == SANTIAGO
    monkeypatch.setenv(aviso_vencimiento.ENV_ZONA, "Marte/Olimpo")
    with pytest.raises(RuntimeError):
        aviso_vencimiento.zona()


def test_con_un_repositorio_inyectado_no_arranca_el_worker(monkeypatch):
    started = []
    monkeypatch.setattr(AvisoVencimientoWorker, "start", lambda self: started.append(self))
    with Pagos().client:
        pass
    assert started == []

"""HU-54 / HU-70: GET /internal/v1/entitlements/{usuario|club}/{id} con INTERNAL_SERVICE_TOKEN."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from test_pagos import PLAN_CLUB, PLAN_USUARIO, Pagos

from app.core.limites_plan import LIMITES
from app.repositories.base import StoredPlan, StoredSuscripcion
from app.services.entitlements import _es_pagada

INTERNAL = "internal-token-for-tests-at-least-32-bytes-long"
H = {"X-Internal-Service-Token": INTERNAL}
BASE = "/internal/v1/entitlements"


@pytest.fixture(autouse=True)
def internal_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL)


def test_rejects_without_or_with_wrong_token_and_with_a_user_token():
    pagos = Pagos()
    athlete_id, user_headers = pagos.athlete()
    assert pagos.client.get(f"{BASE}/usuario/{athlete_id}").status_code == 401
    assert pagos.client.get(f"{BASE}/usuario/{athlete_id}",
                            headers={"X-Internal-Service-Token": "x" * 40}).status_code == 401
    assert pagos.client.get(f"{BASE}/usuario/{athlete_id}", headers=user_headers).status_code == 401
    assert pagos.client.post(f"{BASE}/club/batch", json={"ids": [str(uuid4())]}).status_code == 401


def test_without_configured_secret_answers_503(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "short")
    pagos = Pagos()
    assert pagos.client.get(f"{BASE}/usuario/{uuid4()}", headers=H).status_code == 503


def test_free_athlete_gets_the_free_limits():
    pagos = Pagos()
    body = pagos.client.get(f"{BASE}/usuario/{uuid4()}", headers=H).json()
    assert body["plan"] == "GRATIS" and body["vigente_hasta"] is None
    assert body["limites"] == {
        "solicitudes_match": {"maximo": 6, "ventana_horas": 24},
        "postulaciones_actividades": {"maximo": 5, "ventana_horas": 24},
        "postulaciones_convocatorias": {"maximo": 5, "ventana_horas": 24},
        "actividades_organizadas": {"maximo": 3, "ventana_horas": 720},
    }
    assert body["beneficios"] == []


def test_paid_athlete_is_pro_without_limits_until_the_end_of_the_subscription():
    pagos = Pagos()
    athlete_id, headers = pagos.athlete()
    _, committed = pagos.pay(headers, PLAN_USUARIO.id)
    assert committed.status_code == 200, committed.text
    body = pagos.client.get(f"{BASE}/usuario/{athlete_id}", headers=H).json()
    assert body["plan"] == "PRO" and body["plan_nombre"] == PLAN_USUARIO.nombre
    assert body["vigente_hasta"] is not None
    assert all(limit["maximo"] is None for limit in body["limites"].values())
    assert "destacado" in body["beneficios"]
    # Expired: back to the free limits, nothing deleted.
    pagos.repository.set_suscripcion_fin(usuario_id=athlete_id,
                                         fecha_fin=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert pagos.client.get(f"{BASE}/usuario/{athlete_id}", headers=H).json()["plan"] == "GRATIS"


def test_club_limits_free_and_paid_and_batch():
    pagos = Pagos()
    _, headers, club_id = pagos.club_admin()
    free = pagos.client.get(f"{BASE}/club/{club_id}", headers=H).json()
    assert free["limites"] == {
        "convocatorias_abiertas": {"maximo": 2, "ventana_horas": None},
        "comunicados": {"maximo": 4, "ventana_horas": 24},
        "canales": {"maximo": 1, "ventana_horas": None},
    }
    assert pagos.pay(headers, PLAN_CLUB.id)[1].status_code == 200
    paid = pagos.client.get(f"{BASE}/club/{club_id}", headers=H).json()
    assert paid["plan"] == "PRO"
    assert paid["limites"]["canales"]["maximo"] == 20
    assert paid["limites"]["convocatorias_abiertas"]["maximo"] is None
    other = uuid4()
    batch = pagos.client.post(f"{BASE}/club/batch", headers=H,
                              json={"ids": [str(club_id), str(other), str(club_id)]}).json()["items"]
    assert [(item["id"], item["plan"]) for item in batch] == [(str(club_id), "PRO"), (str(other), "GRATIS")]


def test_invalid_kind_or_id_is_a_client_error():
    pagos = Pagos()
    assert pagos.client.get(f"{BASE}/admin/{uuid4()}", headers=H).status_code == 400
    assert pagos.client.get(f"{BASE}/usuario/no-es-uuid", headers=H).status_code == 400
    assert pagos.client.post(f"{BASE}/club/batch", headers=H,
                             json={"ids": [str(uuid4()) for _ in range(101)]}).status_code == 400


def test_repository_failure_is_503_so_callers_fail_open():
    pagos = Pagos()

    def broken(**_):
        raise RuntimeError("db down")
    pagos.repository.get_suscripcion = broken
    assert pagos.client.get(f"{BASE}/usuario/{uuid4()}", headers=H).status_code == 503


def test_base_plan_and_free_plans_never_count_as_pro():
    now = datetime.now(timezone.utc)

    def sus(plan):
        return StoredSuscripcion(uuid4(), plan, now, now + timedelta(days=1), "ACTIVE", usuario_id=uuid4())
    assert not _es_pagada(sus(StoredPlan(9, "Gratuito", "USUARIO", 0, None, es_base=True)))
    assert not _es_pagada(sus(StoredPlan(9, "Gratis", "USUARIO", 0, 30)))
    assert _es_pagada(sus(StoredPlan(9, "Convenio U", "USUARIO", 0, 30, categoria="CONVENIO")))
    assert set(LIMITES["usuario"]["GRATIS"]) == set(LIMITES["usuario"]["PRO"])
    assert set(LIMITES["club"]["GRATIS"]) == set(LIMITES["club"]["PRO"])


def test_featured_ids_are_the_accounts_with_a_paid_plan_in_force():
    pagos = Pagos()
    _, headers, club_id = pagos.club_admin()
    _, _, free_club = pagos.club_admin()
    assert pagos.client.get(f"{BASE}/club/destacados").status_code == 401
    assert pagos.client.get(f"{BASE}/club/destacados", headers=H).json() == {"ids": []}
    assert pagos.pay(headers, PLAN_CLUB.id)[1].status_code == 200
    assert pagos.client.get(f"{BASE}/club/destacados", headers=H).json() == {"ids": [str(club_id)]}
    assert pagos.client.get(f"{BASE}/usuario/destacados", headers=H).json() == {"ids": []}
    pagos.repository.set_suscripcion_fin(club_id=club_id, fecha_fin=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert pagos.client.get(f"{BASE}/club/destacados", headers=H).json() == {"ids": []}
    assert free_club != club_id

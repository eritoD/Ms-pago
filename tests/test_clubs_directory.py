from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from payment_support import token
from test_pagos import PLAN_CLUB, SETTINGS
from app.core.security import Actor
from app.main import create_app
from app.repositories.mock_payment_repository import MockPaymentRepository
from app.services.clubs_directory import HttpClubsDirectory
from app.services.webpay import MockWebpay


def test_managed_club_uses_the_actor_token_and_upstream_identity():
    actor = Actor(uuid4(), frozenset({"club_admin"}), token="opaque-actor-token")
    club_id = uuid4()
    def handler(request):
        assert request.url.path == "/api/v1/clubs/me"
        assert request.headers["Authorization"] == "Bearer opaque-actor-token"
        assert "X-Internal-Service-Token" not in request.headers
        return httpx.Response(200, json={"id": str(club_id), "nombre": "Club Norte", "mi_rol": "ADMIN"})
    with httpx.Client(base_url="http://clubs.test", transport=httpx.MockTransport(handler)) as client:
        club = HttpClubsDirectory(client).managed_club(actor)
    assert club.id == club_id and club.nombre == "Club Norte"


def test_a_missing_actor_token_fails_without_requesting_an_arbitrary_account():
    def handler(request):
        pytest.fail("No network request is authorized without the actor token")
    with httpx.Client(base_url="http://clubs.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HTTPException) as error:
            HttpClubsDirectory(client).managed_club(Actor(uuid4(), frozenset({"club_admin"})))
    assert error.value.status_code == 503


@pytest.mark.parametrize("code,body,expected", [
    (404, {}, None), (401, {}, 401), (403, {}, 403), (500, {}, 503),
    (200, {"id": "broken"}, 503), (200, {}, 503), (200, [], 503), (200, {"id": 5}, 503),
])
def test_missing_or_invalid_club_directory_never_invents_a_buyer(code, body, expected):
    with httpx.Client(base_url="http://clubs.test", transport=httpx.MockTransport(
        lambda request: httpx.Response(code, json=body))) as client:
        directory = HttpClubsDirectory(client)
        actor = Actor(uuid4(), frozenset({"club_admin"}), token="token")
        if expected is None:
            assert directory.managed_club(actor) is None
        else:
            with pytest.raises(HTTPException) as error:
                directory.managed_club(actor)
            assert error.value.status_code == expected


def test_directory_timeout_prevents_creating_a_gateway_order_or_payment():
    def timeout(request):
        raise httpx.ReadTimeout("unavailable", request=request)
    repository = MockPaymentRepository(planes=[PLAN_CLUB])
    class GatewayMustNotBeCalled(MockWebpay):
        def create_transaction(self, *args, **kwargs):
            pytest.fail("Cannot create an order until the club is identified")
    with httpx.Client(base_url="http://clubs.test", transport=httpx.MockTransport(timeout)) as client:
        app = create_app(repository=repository, webpay=GatewayMustNotBeCalled(SETTINGS.public_url),
            webpay_settings=SETTINGS, clubs_directory=HttpClubsDirectory(client))
        response = TestClient(app).post("/api/v1/payments/webpay/initiate",
            headers=token(uuid4()), json={"plan_id": PLAN_CLUB.id})
    assert response.status_code == 503
    assert repository._transacciones == {}


def test_club_names_are_optional_and_duplicate_requests_are_avoided():
    active, inactive, unavailable = uuid4(), uuid4(), uuid4()
    asked = []
    def handler(request):
        asked.append(request.url.path)
        assert "Authorization" not in request.headers
        if request.url.path.endswith(str(active)):
            return httpx.Response(200, json={"nombre": "Club Sur"})
        if request.url.path.endswith(str(inactive)):
            return httpx.Response(404)
        raise httpx.ReadTimeout("unavailable", request=request)
    with httpx.Client(base_url="http://clubs.test", transport=httpx.MockTransport(handler)) as client:
        names = HttpClubsDirectory(client).names([active, active, inactive, unavailable])
    assert names == {active: "Club Sur"}
    assert len(asked) == 3

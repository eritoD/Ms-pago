"""Payments-only HTTP flows: isolated repository, JWTs and provider transport.

No club endpoints, shared club test harness, database or external network calls.
"""
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import WebpaySettings, get_settings
from app.main import create_app
from app.repositories.base import StoredPlan
from app.repositories.mock_payment_repository import MockPaymentRepository
from app.services.webpay import MockWebpay, WebpayError
from app.services.webpay_checkout import AUDIENCE, read_checkout, sign_checkout
from test_webpay_gateway import APPROVED, TOKEN, adapter

PAY = "/api/v1/payments"
SETTINGS = WebpaySettings(
    mock_mode=True, environment="INTEGRATION", commerce_code=None,
    public_url="http://localhost:8000", frontend_return_url="sportmatchapp://pago",
    web_return_url="http://localhost:3000/pago/resultado",
)


class NoExternalServices:
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected external-service access: {name}")


class CountingMock(MockWebpay):
    def __init__(self):
        super().__init__(SETTINGS.public_url)
        self.commits = 0

    def commit_transaction(self, token_ws):
        self.commits += 1
        return super().commit_transaction(token_ws)


@pytest.fixture
def payments(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "payments-test-secret-at-least-32-bytes")
    monkeypatch.setenv("JWT_ISSUER", "sportmatch-auth")
    monkeypatch.setenv("JWT_AUDIENCE", "sportmatch-mobile")
    with ExitStack() as stack:
        def make(gateway=None, settings=SETTINGS):
            gateway = gateway if gateway is not None else CountingMock()
            stack.callback(gateway.close)
            repository = MockPaymentRepository(planes=[StoredPlan(
                id=1, nombre="Plan atleta", tipo="USUARIO", precio=3990, duracion_dias=30,
            )])
            external = NoExternalServices()
            client = stack.enter_context(TestClient(create_app(
                repository=repository, webpay=gateway, webpay_settings=settings,
                plan_auditor=external, account_names=external, clubs_directory=external,
            )))
            now = datetime.now(timezone.utc)
            token = jwt.encode({
                "sub": str(uuid4()), "roles": ["player"], "token_type": "access",
                "iat": now, "exp": now + timedelta(minutes=10),
                "iss": get_settings().jwt_issuer, "aud": get_settings().jwt_audience,
            }, get_settings().jwt_secret, algorithm="HS256")
            return SimpleNamespace(client=client, repository=repository, gateway=gateway,
                                   settings=settings, headers={"Authorization": f"Bearer {token}"})
        yield make


def initiate(p, client="app"):
    response = p.client.post(f"{PAY}/webpay/initiate", json={"plan_id": 1, "client": client}, headers=p.headers)
    assert response.status_code == 201, response.text
    return response.json()


def commit(p, started, **payload):
    return p.client.post(f"{PAY}/webpay/commit", json=payload or {"token_ws": started["token_ws"]}, headers=p.headers)


def finish_browser(p, started, decision, method="GET"):
    chosen = p.client.get(f"{PAY}/webpay/mock/decision", params={
        "token_ws": started["token_ws"], "accion": decision,
    }, follow_redirects=False)
    assert chosen.status_code == 303
    location = urlsplit(chosen.headers["location"])
    if method == "GET":
        return p.client.get(location.path + "?" + location.query, follow_redirects=False)
    params = {key: value[0] for key, value in parse_qs(location.query).items()}
    client = params.pop("client", None)
    return p.client.post(location.path, params={"client": client} if client else {},
                         data=params, follow_redirects=False)


@pytest.mark.parametrize("callback_method", ["GET", "POST"])
def test_checkout_and_browser_return_approve_without_login_and_commit_is_idempotent(payments, callback_method):
    p = payments()
    started = initiate(p)
    assert started["environment"] == "MOCK"
    assert started["url"] == "http://localhost:8000/api/v1/payments/webpay/mock"
    checkout = started["checkout_url"].removeprefix(SETTINGS.public_url)
    page = p.client.get(checkout)
    assert page.status_code == 200
    assert 'method="POST"' in page.text
    assert f'name="token_ws" value="{started["token_ws"]}"' in page.text
    assert "document.getElementById(\"webpay\").submit()" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    mock_page = p.client.post(f"{PAY}/webpay/mock", data={"token_ws": started["token_ws"]})
    assert mock_page.status_code == 200 and "No se realiza" in mock_page.text
    assert all(action in mock_page.text for action in ("aprobar", "rechazar", "cancelar"))
    returned = finish_browser(p, started, "aprobar", callback_method)
    assert returned.status_code == 303
    assert returned.headers["location"] == f'sportmatchapp://pago?token_ws={started["token_ws"]}'
    first = commit(p, started)
    assert first.status_code == 200 and first.json()["status"] == "APPROVED"
    assert first.json()["suscripcion"]["estado"] == "ACTIVE"
    assert commit(p, started).json() == first.json()
    assert p.client.get(f"{PAY}/subscription/me", headers=p.headers).json() == first.json()["suscripcion"]
    assert p.gateway.commits == 1


@pytest.mark.parametrize("decision,status", [("rechazar", "REJECTED"), ("cancelar", "CANCELLED")])
def test_rejection_and_cancellation_preserve_subscription_and_repeat_same_receipt(payments, decision, status):
    p = payments()
    approved = initiate(p)
    finish_browser(p, approved, "aprobar")
    before = commit(p, approved).json()["suscripcion"]
    started = initiate(p)
    assert finish_browser(p, started, decision, "POST").status_code == 303
    first = commit(p, started)
    assert first.status_code == 400
    assert first.json()["transaccion"]["status"] == status
    assert first.json()["transaccion"]["suscripcion"] is None
    assert commit(p, started).json() == first.json()
    assert p.client.get(f"{PAY}/subscription/me", headers=p.headers).json() == before
    assert p.gateway.commits == (2 if decision == "rechazar" else 1)
    assert p.client.get(f"{PAY}/webpay/mock/decision", params={
        "token_ws": started["token_ws"], "accion": "aprobar",
    }).status_code == 404


def test_web_and_mobile_returns_stay_on_server_configured_destinations(payments):
    p = payments()
    web = initiate(p, "web")
    assert p.gateway.describe(web["token_ws"])["return_url"] == SETTINGS.return_url + "?client=web"
    assert finish_browser(p, web, "aprobar").headers["location"] == (
        f'http://localhost:3000/pago/resultado?token_ws={web["token_ws"]}')
    mobile = initiate(p)
    assert p.gateway.describe(mobile["token_ws"])["return_url"] == SETTINGS.return_url
    assert p.client.post(f"{PAY}/webpay/initiate", json={"plan_id": 1, "client": "https://evil.test"}, headers=p.headers).status_code == 400


def test_web_without_return_url_fails_before_creating_transaction(payments):
    p = payments(settings=replace(SETTINGS, web_return_url=""))
    assert p.client.post(f"{PAY}/webpay/initiate", json={"plan_id": 1, "client": "web"}, headers=p.headers).status_code == 503
    assert not p.repository._transacciones


def test_incomplete_or_wrong_browser_references_cannot_cancel_an_order(payments):
    p = payments()
    started = initiate(p)
    for params in (
        {"TBK_ORDEN_COMPRA": started["buy_order"]},
        {"TBK_ORDEN_COMPRA": started["buy_order"], "TBK_ID_SESION": "another-session"},
        {"TBK_TOKEN": started["token_ws"], "TBK_ID_SESION": "another-session"},
    ):
        p.client.get(f"{PAY}/webpay/return", params=params, follow_redirects=False)
        assert p.repository.get_transaccion(token_ws=started["token_ws"]).status == "PENDING"
    assert p.gateway.commits == 0


def test_timeout_with_matching_order_and_session_cancels_without_commit(payments):
    p = payments()
    started = initiate(p)
    p.client.get(f"{PAY}/webpay/return", params={
        "TBK_ORDEN_COMPRA": started["buy_order"], "TBK_ID_SESION": started["session_id"],
    }, follow_redirects=False)
    assert commit(p, started).json()["transaccion"]["status"] == "CANCELLED"
    assert p.gateway.commits == 0
    assert p.client.get(f"{PAY}/subscription/me", headers=p.headers).status_code == 404


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("timeout", [False, True])
def test_real_abandoned_return_queries_status_without_committing(payments, method, timeout):
    seen, transaction = [], {}

    def handler(request):
        seen.append(request.method)
        if request.method == "POST":
            transaction.update(json.loads(request.content))
            return httpx.Response(200, json={"token": TOKEN,
                "url": "https://webpay3gint.transbank.cl/webpayserver/initTransaction"})
        assert request.method == "GET"
        return httpx.Response(200, json={**transaction, "status": "INITIALIZED"})

    p = payments(adapter(handler), replace(SETTINGS, mock_mode=False))
    started = initiate(p)
    params = {"TBK_ORDEN_COMPRA": started["buy_order"], "TBK_ID_SESION": started["session_id"]}
    if not timeout:
        params["TBK_TOKEN"] = TOKEN
    response = (p.client.get(f"{PAY}/webpay/return", params=params, follow_redirects=False)
                if method == "GET" else p.client.post(f"{PAY}/webpay/return", data=params, follow_redirects=False))
    assert response.status_code == 303
    assert commit(p, started).json()["transaccion"]["status"] == "CANCELLED"
    # The explicit result lookup rechecks the cancelled order without committing,
    # so an authorization racing the abandonment can still be recovered.
    assert seen == ["POST", "GET", "GET"]


@pytest.mark.parametrize("result", ["authorized", "unavailable", "mismatch", "unknown"])
def test_abandoned_return_never_hides_authorization_or_invents_a_status(payments, result):
    seen, transaction = [], {}

    def handler(request):
        seen.append(request.method)
        if request.method == "POST":
            transaction.update(json.loads(request.content))
            return httpx.Response(200, json={"token": TOKEN,
                "url": "https://webpay3gint.transbank.cl/webpayserver/initTransaction"})
        assert request.method == "GET"
        if result == "unavailable":
            raise httpx.ReadTimeout("Status unavailable", request=request)
        body = {**APPROVED, **transaction}
        if result == "mismatch":
            body["amount"] += 1
        if result == "unknown":
            body["status"] = "PENDING"
        return httpx.Response(200, json=body)

    p = payments(adapter(handler), replace(SETTINGS, mock_mode=False))
    started = initiate(p)
    response = p.client.post(f"{PAY}/webpay/commit", headers=p.headers,
                            json={"tbk_token": TOKEN})
    assert response.status_code == (200 if result == "authorized" else 502)
    expected = "APPROVED" if result == "authorized" else "PENDING"
    assert p.repository.get_transaccion(token_ws=TOKEN).status == expected
    if result == "authorized":
        subscription = response.json()["suscripcion"]
        assert subscription["estado"] == "ACTIVE"
        assert commit(p, started).json()["suscripcion"] == subscription
    assert seen == ["POST", "GET"]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_mock_routes_are_hidden_when_using_the_http_adapter(payments, method):
    p = payments(adapter(lambda request: pytest.fail("No provider request expected")), replace(SETTINGS, mock_mode=False))
    assert p.client.request(method, f"{PAY}/webpay/mock", params={"token_ws": TOKEN}).status_code == 404
    assert p.client.get(f"{PAY}/webpay/mock/decision", params={"token_ws": TOKEN, "accion": "aprobar"}).status_code == 404


def test_signed_checkout_rejects_tampered_expired_wrong_audience_and_malformed_claims(payments):
    p = payments()
    ticket = sign_checkout(TOKEN, SETTINGS.public_url + MockWebpay.PAGE_PATH)
    assert read_checkout(ticket)["token_ws"] == TOKEN
    assert p.client.get(f"{PAY}/webpay/checkout", params={"ticket": "x" + ticket}).status_code == 400
    claims = {"token_ws": TOKEN, "url": "https://webpay3gint.transbank.cl/webpayserver/initTransaction",
              "aud": AUDIENCE, "exp": datetime.now(timezone.utc) + timedelta(minutes=10)}
    for override in (
        {"exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, {"aud": "sportmatch-mobile"},
        {"token_ws": {}}, {"token_ws": ""}, {"url": []}, {"url": "javascript:alert(1)"},
    ):
        invalid = jwt.encode({**claims, **override}, get_settings().jwt_secret, algorithm="HS256")
        assert p.client.get(f"{PAY}/webpay/checkout", params={"ticket": invalid}).status_code == 400


@pytest.mark.parametrize("override", [{"amount": 3991}, {"session_id": "user:someone-else"}, {"buy_order": "another-order"}])
def test_mismatched_provider_receipt_never_activates_subscription(payments, override):
    class WrongReceipt(CountingMock):
        def commit_transaction(self, token_ws):
            return replace(super().commit_transaction(token_ws), **override)

    p = payments(WrongReceipt())
    started = initiate(p)
    p.gateway.decide(started["token_ws"], "aprobar")
    assert commit(p, started).status_code == 502
    assert p.repository.get_transaccion(token_ws=started["token_ws"]).status == "PENDING"
    assert p.client.get(f"{PAY}/subscription/me", headers=p.headers).status_code == 404


def provider_flow(*, lose_commit=False, unavailable=False):
    seen, transaction = [], {}

    def handler(request):
        seen.append(request.method)
        if request.method == "POST":
            transaction.update(json.loads(request.content))
            return httpx.Response(200, json={"token": TOKEN, "url": "https://webpay3gint.transbank.cl/webpayserver/initTransaction"})
        if unavailable or (lose_commit and request.method == "PUT"):
            raise httpx.ReadTimeout("Response lost", request=request)
        return httpx.Response(200, json={**APPROVED, **{
            name: transaction[name] for name in ("buy_order", "session_id", "amount")
        }})

    return adapter(handler), seen


@pytest.mark.parametrize("lose_commit", [False, True])
def test_http_provider_callback_recovers_lost_commit_and_never_extends_twice(payments, lose_commit):
    gateway, seen = provider_flow(lose_commit=lose_commit)
    p = payments(gateway, replace(SETTINGS, mock_mode=False))
    started = initiate(p)
    assert started["environment"] == "INTEGRATION"
    response = p.client.get(f"{PAY}/webpay/return", params={"token_ws": TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    receipt = commit(p, started)
    assert receipt.status_code == 200 and receipt.json()["status"] == "APPROVED"
    assert commit(p, started).json() == receipt.json()
    assert p.client.get(f"{PAY}/subscription/me", headers=p.headers).json() == receipt.json()["suscripcion"]
    assert seen == (["POST", "PUT", "GET"] if lose_commit else ["POST", "PUT"])


def test_provider_timeout_leaves_payment_pending_and_does_not_activate_subscription(payments):
    gateway, seen = provider_flow(unavailable=True)
    p = payments(gateway, replace(SETTINGS, mock_mode=False))
    started = initiate(p)
    response = commit(p, started)
    assert response.status_code == 502
    assert seen == ["POST", "PUT", "GET"]
    assert p.repository.get_transaccion(token_ws=TOKEN).status == "PENDING"
    assert p.client.get(f"{PAY}/subscription/me", headers=p.headers).status_code == 404

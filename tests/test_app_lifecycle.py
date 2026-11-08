from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from payment_support import FakeClubsDirectory
from test_pagos import SETTINGS
from test_planes_admin import FakeAuditor
from test_planes_admin_panel import FakeNames
from app.main import create_app
from app.repositories.mock_payment_repository import MockPaymentRepository
from app.services.webpay import MockWebpay


def test_injected_app_starts_without_a_database_and_does_not_close_owned_dependencies(monkeypatch):
    monkeypatch.delenv("DB_USER", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    class Gateway(MockWebpay):
        closed = False
        def close(self):
            self.closed = True
    gateway = Gateway(SETTINGS.public_url)
    app = create_app(repository=MockPaymentRepository(), webpay=gateway, webpay_settings=SETTINGS,
                     plan_auditor=FakeAuditor(), account_names=FakeNames(), clubs_directory=FakeClubsDirectory())
    with TestClient(app) as client:
        assert client.get("/api/v1/payments/health/ready").json() == {"status": "ready", "service": "ms_pago"}
        assert client.get("/api/v1/payments/plans").json() == []
        assert client.get("/api/v1/clubs").status_code == 404
    assert gateway.closed is False


@pytest.mark.parametrize("mock_mode", [True, False])
def test_production_rejects_an_injected_mock_gateway(mock_mode):
    settings = replace(SETTINGS, environment="PRODUCTION", mock_mode=mock_mode)
    with pytest.raises(RuntimeError, match="PRODUCTION"):
        create_app(repository=MockPaymentRepository(), webpay=MockWebpay(SETTINGS.public_url),
                   webpay_settings=settings)

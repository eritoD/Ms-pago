from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import WebpaySettings
from app.core.security import Actor, authenticate
from app.repositories.base import PaymentRepository
from app.services.pago_service import PagoService
from app.services.plan_admin_service import PlanAdminService
from app.services.webpay import MockWebpay

bearer_scheme = HTTPBearer(auto_error=False)


def get_repository(request: Request) -> PaymentRepository:
    return request.app.state.repository


def get_current_actor(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> Actor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "authentication required")
    return authenticate(credentials.credentials)


def get_admin(actor: Actor = Depends(get_current_actor)) -> Actor:
    if not actor.is_admin:
        raise HTTPException(403, "Solo un administrador de la plataforma puede gestionar planes.")
    return actor


def get_webpay_settings_dep(request: Request) -> WebpaySettings:
    settings = getattr(request.app.state, "webpay_settings", None)
    if settings is None:
        raise HTTPException(503, "Los pagos no están disponibles.")
    return settings


def get_pago_service(request: Request, repository: PaymentRepository = Depends(get_repository),
                     settings: WebpaySettings = Depends(get_webpay_settings_dep)) -> PagoService:
    gateway = getattr(request.app.state, "webpay", None)
    if gateway is None:
        raise HTTPException(503, "Los pagos no están disponibles.")
    return PagoService(repository, gateway, settings, getattr(request.app.state, "clubs_directory", None))


def get_mock_webpay(request: Request) -> MockWebpay:
    gateway = getattr(request.app.state, "webpay", None)
    settings = getattr(request.app.state, "webpay_settings", None)
    if (not isinstance(gateway, MockWebpay)
            or (settings is not None and settings.environment == "PRODUCTION")):
        raise HTTPException(404, "Not Found")
    return gateway


def get_plan_admin_service(request: Request, repository: PaymentRepository = Depends(get_repository)) -> PlanAdminService:
    return PlanAdminService(repository, getattr(request.app.state, "plan_auditor", None),
                            getattr(request.app.state, "account_names", None),
                            getattr(request.app.state, "clubs_directory", None))

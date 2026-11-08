from __future__ import annotations

import math
import os
from contextlib import ExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.pagos import router as pagos_router
from app.api.v1.planes_admin import router as planes_admin_router
from app.core.config import (WebpaySettings, get_clubs_service_settings, get_database_settings,
                             get_settings, get_users_service_settings, get_webpay_settings)
from app.database.session import check_connection, create_pool
from app.repositories.base import PaymentRepository
from app.repositories.postgres_payment_repository import PostgresPaymentRepository
from app.services.account_names import AccountDirectory, InternalAccountsClient
from app.services.clubs_directory import ClubsDirectory, HttpClubsDirectory
from app.services.plan_audit import InternalAuditClient, PlanAuditor
from app.services.webpay import MockWebpay, WebpayGateway, build_webpay


def create_app(repository: PaymentRepository | None = None,
               webpay: WebpayGateway | None = None,
               webpay_settings: WebpaySettings | None = None,
               plan_auditor: PlanAuditor | None = None,
               account_names: AccountDirectory | None = None,
               clubs_directory: ClubsDirectory | None = None) -> FastAPI:
    """Inject financial storage and HTTP directories in tests; no connections at import."""
    if (webpay_settings is not None and webpay_settings.environment == "PRODUCTION"
            and (webpay_settings.mock_mode or isinstance(webpay, MockWebpay))):
        raise RuntimeError("Mock Webpay cannot be used in PRODUCTION")
    pool_holder = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with ExitStack() as stack:
            if repository is None:
                get_settings()
                settings = get_database_settings()
                check_connection(settings)
                pool = create_pool(settings.conninfo())
                stack.callback(pool.close)
                pool_holder["pool"] = pool
                app.state.repository = PostgresPaymentRepository(pool)
            if clubs_directory is None:
                settings = get_clubs_service_settings()
                client = stack.enter_context(httpx.Client(base_url=settings.url,
                    timeout=settings.timeout_seconds, trust_env=False))
                app.state.clubs_directory = HttpClubsDirectory(client)
            if plan_auditor is None or account_names is None:
                settings = get_users_service_settings()
                client = stack.enter_context(httpx.Client(base_url=settings.url,
                    timeout=settings.timeout_seconds, trust_env=False))
                if plan_auditor is None:
                    app.state.plan_auditor = InternalAuditClient(client, os.getenv("INTERNAL_SERVICE_TOKEN"))
                if account_names is None:
                    app.state.account_names = InternalAccountsClient(client, os.getenv("INTERNAL_SERVICE_TOKEN"))
            if webpay is None:
                app.state.webpay_settings = webpay_settings or get_webpay_settings()
                app.state.webpay = build_webpay(app.state.webpay_settings)
                stack.callback(app.state.webpay.close)
            yield

    app = FastAPI(title="SportMatch payments service", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    for key, value in {"repository": repository, "webpay": webpay, "webpay_settings": webpay_settings,
                       "plan_auditor": plan_auditor, "account_names": account_names,
                       "clubs_directory": clubs_directory}.items():
        if value is not None:
            setattr(app.state, key, value)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, error: RequestValidationError):
        return JSONResponse(status_code=400, content={"detail": jsonable_encoder(
            [{key: item[key] for key in ("type", "loc", "msg") if key in item} for item in error.errors()],
            custom_encoder={float: lambda value: value if math.isfinite(value) else str(value)})})

    @app.get("/api/v1/payments/health/ready", tags=["health"])
    def ready():
        pool = pool_holder.get("pool")
        if pool is not None:
            try:
                with pool.connection(timeout=2) as conn:
                    conn.execute("SELECT 1 FROM plan_suscripcion LIMIT 1")
                    conn.execute("SELECT 1 FROM transaccion_pago LIMIT 1")
                    conn.execute("SELECT 1 FROM suscripcion LIMIT 1")
            except Exception as error:
                raise HTTPException(503, "payments database unavailable") from error
        return {"status": "ready", "service": "ms_pago"}

    app.include_router(planes_admin_router)
    app.include_router(pagos_router)
    return app


app = create_app()

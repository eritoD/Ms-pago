"""Endpoints internos de beneficios por plan (HU-54 / HU-70).

Nunca se declaran en el gateway ni se publican fuera de Docker: ms-machine, Ms_activities, Ms-club y Ms_Users los
llaman por la red interna con el secreto compartido INTERNAL_SERVICE_TOKEN en el encabezado
X-Internal-Service-Token (mismo esquema que Ms_Users y ms-machine). Un token de usuario no sirve aquí."""
from __future__ import annotations

import hmac
import logging
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_repository
from app.repositories.base import PaymentRepository
from app.services.entitlements import destacados, entitlement

logger = logging.getLogger(__name__)

MIN_TOKEN_BYTES = 32
MAX_LOTE = 100


def require_internal_service(
    x_internal_service_token: str | None = Header(default=None, max_length=256),
) -> None:
    expected = os.environ.get("INTERNAL_SERVICE_TOKEN")
    if not expected or len(expected.encode("utf-8")) < MIN_TOKEN_BYTES:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "internal authentication is not configured")
    if x_internal_service_token is None or not hmac.compare_digest(
        x_internal_service_token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "service authentication required")


router = APIRouter(prefix="/internal/v1/entitlements", tags=["internal"], include_in_schema=False,
                   dependencies=[Depends(require_internal_service)])


def _safe(repository: PaymentRepository, tipo: str, cuenta_id: UUID) -> dict:
    try:
        return entitlement(repository, tipo, cuenta_id)
    except Exception as error:  # the callers fail open on 503
        logger.exception("Could not read the subscription of %s %s", tipo, cuenta_id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "payments database unavailable") from error


class Lote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[UUID] = Field(min_length=1, max_length=MAX_LOTE)


@router.post("/{tipo}/batch")
def entitlements_batch(tipo: Literal["usuario", "club"], lote: Lote,
                       repository: PaymentRepository = Depends(get_repository)) -> dict:
    """Varias cuentas a la vez (directorio de clubes, sugerencias): para marcar las destacadas."""
    return {"items": [_safe(repository, tipo, cuenta_id) for cuenta_id in dict.fromkeys(lote.ids)]}


@router.get("/{tipo}/destacados")
def entitlements_destacados(tipo: Literal["usuario", "club"],
                            repository: PaymentRepository = Depends(get_repository)) -> dict:
    """Ids de las cuentas con plan pagado vigente (destacadas), para ordenarlas primero antes de paginar."""
    try:
        return {"ids": destacados(repository, tipo)}
    except Exception as error:  # the callers fail open on 503
        logger.exception("Could not list the featured %s accounts", tipo)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "payments database unavailable") from error


@router.get("/{tipo}/{cuenta_id}")
def entitlements_one(tipo: Literal["usuario", "club"], cuenta_id: UUID,
                     repository: PaymentRepository = Depends(get_repository)) -> dict:
    """{plan, vigente_hasta, limites, beneficios} de un deportista (usuario) o de un club."""
    return _safe(repository, tipo, cuenta_id)

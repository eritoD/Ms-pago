"""HU-55: internal endpoints the gateway uses to publish the expiry notices (outbox aviso_pendiente).

They are never declared in the gateway's public routes: only the gateway's notification worker calls them
over the internal network, with the shared secret INTERNAL_SERVICE_TOKEN in X-Internal-Service-Token. A
user's token is not valid here. The gateway copies each notice into its inbox (idempotent by source and id)
and only then confirms it, so a crash in between only replays it.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/internal/v1/avisos", tags=["internal"], include_in_schema=False)


def require_internal_service(x_internal_service_token: str | None = Header(default=None, max_length=256)) -> None:
    expected = os.getenv("INTERNAL_SERVICE_TOKEN", "")
    if len(expected.encode("utf-8")) < 32:  # not configured, or too weak to trust
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "internal authentication is not configured")
    if x_internal_service_token is None or not hmac.compare_digest(
            x_internal_service_token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "service authentication required")


class AvisoRead(BaseModel):
    id: int
    user_id: UUID
    kind: str
    title: str
    body: str
    data: dict[str, Any]
    created_at: datetime


class AvisosPendientes(BaseModel):
    items: list[AvisoRead]


class ConfirmarAvisos(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[int] = Field(min_length=1, max_length=500)


class AvisosConfirmados(BaseModel):
    confirmados: int


def _repository(request: Request):
    return request.app.state.repository


@router.get("/pendientes", response_model=AvisosPendientes, dependencies=[Depends(require_internal_service)])
def pendientes(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> AvisosPendientes:
    avisos = _repository(request).avisos_pendientes(datetime.now(timezone.utc), limit)
    return AvisosPendientes(items=[AvisoRead(**vars(a)) for a in avisos])


@router.post("/confirmar", response_model=AvisosConfirmados, dependencies=[Depends(require_internal_service)])
def confirmar(request: Request, payload: ConfirmarAvisos) -> AvisosConfirmados:
    return AvisosConfirmados(confirmados=_repository(request).confirmar_avisos(payload.ids))

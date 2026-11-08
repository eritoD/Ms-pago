"""HTTP references to clubs; this service owns no club data or club rules."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol
from uuid import UUID

import httpx
from fastapi import HTTPException

from app.core.security import Actor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManagedClub:
    id: UUID
    nombre: str | None = None


class ClubsDirectory(Protocol):
    def managed_club(self, actor: Actor) -> ManagedClub | None: ...
    def names(self, ids: list[UUID]) -> dict[UUID, str]: ...


class HttpClubsDirectory:
    def __init__(self, client: httpx.Client):
        self._client = client

    def managed_club(self, actor: Actor) -> ManagedClub | None:
        if actor.token:
            path = "/api/v1/clubs/me"
            headers = {"Authorization": f"Bearer {actor.token}"}
        else:
            raise HTTPException(503, "No se pudo identificar el club administrado.")
        try:
            response = self._client.get(path, headers=headers)
            if response.status_code == 404:
                return None
            if response.status_code in {401, 403}:
                raise HTTPException(response.status_code, "No se pudo autorizar la consulta del club.")
            response.raise_for_status()
            data = response.json()
            return ManagedClub(UUID(data["id"]), data.get("nombre"))
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise HTTPException(503, "El servicio de clubes no está disponible.") from error

    def names(self, ids: list[UUID]) -> dict[UUID, str]:
        result = {}
        for club_id in dict.fromkeys(ids):
            try:
                response = self._client.get(f"/api/v1/clubs/{club_id}")
                response.raise_for_status()
                name = response.json().get("nombre")
                if isinstance(name, str) and name.strip():
                    result[club_id] = name
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                logger.warning("Club display name unavailable for %s", club_id)
        return result

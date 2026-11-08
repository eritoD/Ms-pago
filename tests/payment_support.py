"""Test identity and directory fakes; no dependency on the clubs domain."""
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
import jwt
from app.services.clubs_directory import ManagedClub
os.environ["JWT_SECRET"] = "test-only-secret-that-is-long-enough"

def token(user_id: UUID, role: str = "club_admin", **overrides: object) -> dict[str, str]:
    """Access token shaped like the ones Ms_Users signs."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id), "iss": "sportmatch-auth", "aud": "sportmatch-mobile",
        "iat": now, "exp": now + timedelta(minutes=30), "token_type": "access",
        "roles": [role], **overrides,
    }
    encoded = jwt.encode(claims, os.environ["JWT_SECRET"], algorithm="HS256")
    return {"Authorization": f"Bearer {encoded}"}

class FakeClubsDirectory:
    def __init__(self):
        self.by_admin = {}
        self.by_id = {}

    def add(self, actor_id, nombre, club_id=None):
        club = ManagedClub(club_id or uuid4(), nombre)
        self.by_admin[actor_id] = club
        self.by_id[club.id] = club
        return club

    def managed_club(self, actor):
        return self.by_admin.get(actor.id)

    def names(self, ids):
        return {i: self.by_id[i].nombre for i in ids if i in self.by_id}

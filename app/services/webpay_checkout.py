"""Short-lived browser bridge: a signed destination, never an arbitrary redirect."""
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings
from app.services.webpay import _http_url, _TOKEN

AUDIENCE = "sportmatch-webpay-checkout"


def _validate_claims(data: dict) -> dict:
    token, url = data.get("token_ws"), data.get("url")
    if not isinstance(token, str) or not _TOKEN.fullmatch(token) or not _http_url(url):
        raise jwt.InvalidTokenError("Invalid checkout destination or token")
    return data


def sign_checkout(token: str, url: str) -> str:
    _validate_claims({"token_ws": token, "url": url})
    return jwt.encode(
        {"token_ws": token, "url": url, "aud": AUDIENCE,
         "exp": datetime.now(timezone.utc) + timedelta(minutes=15)},
        get_settings().jwt_secret, algorithm="HS256",
    )


def read_checkout(ticket: str) -> dict:
    return _validate_claims(jwt.decode(
        ticket, get_settings().jwt_secret, algorithms=["HS256"], audience=AUDIENCE,
        options={"require": ["exp", "aud", "token_ws", "url"]},
    ))

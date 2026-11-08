from __future__ import annotations

from pydantic import BaseModel, ConfigDict

PAGE_SIZE_MAX = 50

def blank_is_none(value: object) -> object:
    """Optional text fields: an empty or blank string means "no value"."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)



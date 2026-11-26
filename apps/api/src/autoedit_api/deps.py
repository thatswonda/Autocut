"""FastAPI dependencies."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .db import User, get_db
from .schemas import ApiError
from .services import get_or_create_user
from .settings import get_settings

DbSession = Annotated[Session, Depends(get_db)]

_USER_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def current_user(
    db: DbSession,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> User:
    """M1 stand-in for real sign-in (M10). Every query is already scoped to this user."""
    settings = get_settings()
    if not settings.dev_auth:
        raise ApiError(401, "unauthenticated", "Sign-in is required")
    user_id = x_user_id or "usr_dev"
    if not _USER_ID.match(user_id):
        raise ApiError(400, "bad_user_id", "X-User-Id must be 1-64 characters of [A-Za-z0-9_-]")
    return get_or_create_user(db, user_id)


CurrentUser = Annotated[User, Depends(current_user)]

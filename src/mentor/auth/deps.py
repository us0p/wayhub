from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import User
from mentor.auth.sessions import cookie_name, resolve_session
from mentor.db import get_session
from mentor.legal import has_current_consent

DB = Annotated[AsyncSession, Depends(get_session)]


class LoginRequiredError(Exception):
    pass


class ConsentRequiredError(Exception):
    pass


async def optional_user(request: Request, db: DB) -> User | None:
    token = request.cookies.get(cookie_name())
    if not token:
        return None
    session = await resolve_session(db, token)
    if session is None:
        return None
    request.state.user = session.user
    request.state.session_token = token
    return session.user


async def require_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise LoginRequiredError
    return user


async def require_consented_user(user: Annotated[User, Depends(require_user)], db: DB) -> User:
    if not await has_current_consent(db, user.id):
        raise ConsentRequiredError
    return user


OptionalUser = Annotated[User | None, Depends(optional_user)]
AuthenticatedUser = Annotated[User, Depends(require_user)]  # logged in, consent not required
CurrentUser = Annotated[User, Depends(require_consented_user)]  # default for app pages

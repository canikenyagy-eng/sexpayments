from typing import List

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.api.dependencies import get_service
from app.common.enums.users import UserRole
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decode_access_token
from app.modules.users.models import User
from app.modules.users.service import UserService

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user_service: UserService = Depends(get_service(UserService)),
) -> User:
    """
    Dependency to get the current authenticated user from JWT token.
    """
    try:
        payload = decode_access_token(credentials.credentials)
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise UnauthorizedException("Could not validate credentials")
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise UnauthorizedException("Could not validate credentials")

    user = await user_service.get_user_by_id(user_id)
    return user


class RoleChecker:
    """
    Dependency class to check if current user has required roles.
    Usage: Depends(RoleChecker([UserRole.ADMIN, UserRole.MERCHANT]))
    """

    def __init__(self, allowed_roles: List[UserRole]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if user.role not in self.allowed_roles:
            raise ForbiddenException("You don't have enough permissions")
        return user


# Convenience dependencies
require_admin = RoleChecker([UserRole.ADMIN])
require_merchant = RoleChecker([UserRole.MERCHANT])
require_trader = RoleChecker([UserRole.TRADER])

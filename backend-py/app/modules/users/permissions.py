from typing import List

import sentry_sdk
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.api.dependencies import get_service
from app.common.enums.users import UserRole
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decode_access_token
from app.infrastructure.cache.redis import redis_client
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
    token = credentials.credentials
    
    # Check if token is blacklisted
    is_blacklisted = await redis_client.get(f"bl_{token}")
    if is_blacklisted:
        raise UnauthorizedException("Token has been revoked")
        
    try:
        payload = decode_access_token(token)
        
        # Ensure this is an access token
        if payload.get("type") != "access":
            raise UnauthorizedException("Invalid token type")
            
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise UnauthorizedException("Could not validate credentials")
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise UnauthorizedException("Could not validate credentials")

    user = await user_service.get_user_by_id(user_id)
    
    if user.is_blocked:
        raise ForbiddenException("User account is blocked")

    sentry_sdk.set_user({"id": user.id, "username": user.username})

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
require_teamlead = RoleChecker([UserRole.TEAMLEAD])
require_trader_or_teamlead = RoleChecker([UserRole.TRADER, UserRole.TEAMLEAD])

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_service
from app.common.enums.users import UserRole
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin
from app.modules.users.schemas import (
    ChangePasswordRequest,
    Enable2FARequest,
    Setup2FAResponse,
    UserMeUpdate,
    UserResponse,
    UserUpdate,
)
from app.modules.users.service import UserService

router = APIRouter()


@router.get(
    "/",
    response_model=List[UserResponse],
    summary="Get all users (Admin only)",
    dependencies=[Depends(require_admin)],
)
async def get_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    role: Optional[UserRole] = None,
    is_blocked: Optional[bool] = None,
    is_active: Optional[bool] = Query(None, description="Filter by active flag (inverse of is_blocked)"),
    balance_from: Optional[float] = Query(None, ge=0, description="Minimum USDT WORK balance"),
    balance_to: Optional[float] = Query(None, ge=0, description="Maximum USDT WORK balance"),
    search: Optional[str] = Query(None, description="Search by ID or username"),
    user_service: UserService = Depends(get_service(UserService)),
):
    return await user_service.get_users_with_balances(
        skip=skip,
        limit=limit,
        role=role,
        is_blocked=is_blocked,
        is_active=is_active,
        balance_from=balance_from,
        balance_to=balance_to,
        search=search,
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Get details of the currently authenticated user.
    """
    return current_user


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update current user profile",
)
async def update_me(
    data: UserMeUpdate,
    current_user: User = Depends(get_current_user),
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Update settings the current user can change about themselves
    (currently: preferred IANA timezone for date/time display).
    """
    return await user_service.update_me(current_user.id, data)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user by ID (Admin only)",
    dependencies=[Depends(require_admin)],
)
async def get_user(
    user_id: int,
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Get detailed information about a specific user.
    """
    return await user_service.get_user_by_id(user_id)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update user (Admin only)",
)
async def update_user(
    user_id: int,
    data: UserUpdate,
    admin_user: User = Depends(require_admin),
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Update user data (role, active status, shared balance setting).
    """
    return await user_service.update_user(user_id, data, admin_user_id=admin_user.id)


@router.post(
    "/me/password",
    response_model=UserResponse,
    summary="Change password",
)
async def change_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Change user password. Requires a valid Google Authenticator code.
    """
    user = await user_service.change_password(
        user_id=current_user.id,
        new_password=data.new_password,
        google_code=data.google_code,
    )
    return user


@router.get(
    "/me/2fa/setup",
    response_model=Setup2FAResponse,
    summary="Get 2FA setup details",
)
async def setup_2fa(
    current_user: User = Depends(get_current_user),
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Get the TOTP secret and provisioning URI to scan with Google Authenticator.
    """
    uri = user_service.get_totp_uri(current_user)
    return Setup2FAResponse(secret=current_user.totp_secret, provisioning_uri=uri)


@router.post(
    "/me/2fa/enable",
    response_model=UserResponse,
    summary="Enable 2FA",
)
async def enable_2fa(
    data: Enable2FARequest,
    current_user: User = Depends(get_current_user),
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Verify the code from Google Authenticator and enable 2FA for the user.
    """
    user = await user_service.enable_2fa(
        user_id=current_user.id,
        google_code=data.google_code,
        secret=data.secret,
    )
    return user


@router.post(
    "/{user_id}/2fa/reset",
    response_model=UserResponse,
    summary="Reset 2FA for a user (Admin only)",
)
async def reset_2fa(
    user_id: int,
    admin_user: User = Depends(require_admin),
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Reset 2FA for a specific user. This will disable 2FA and force them to set it up again.
    """
    user = await user_service.reset_2fa(target_user_id=user_id, admin_user_id=admin_user.id)
    return user

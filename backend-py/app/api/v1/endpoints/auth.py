from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_service
from app.core.config import get_settings
from app.modules.users.models import User
from app.modules.users.schemas import TokenResponse, UserLogin, UserRegister, UserResponse, RefreshTokenRequest, LogoutRequest, ImpersonateRequest
from app.modules.users.service import UserService
from app.modules.users.permissions import get_current_user, require_admin

router = APIRouter()


def _set_access_cookie(response: Response, access_token: str) -> None:
    """Mirror the JWT into an HttpOnly cookie so server-rendered admin pages
    (Swagger UI at `/api/docs`) can authenticate. The API itself still reads
    `Authorization: Bearer ...` from the frontend; the cookie is an addition,
    not a replacement."""
    settings = get_settings()
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=(settings.APP_ENV == "production"),
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MIN * 60,
        path="/",
    )


def _clear_access_cookie(response: Response) -> None:
    response.delete_cookie(key="access_token", path="/")

@router.post(
    "/impersonate",
    response_model=TokenResponse,
    summary="Impersonate another user (Admin only)",
)
async def impersonate(
    data: ImpersonateRequest,
    response: Response,
    admin_user: User = Depends(require_admin),
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Allows an admin to log in as another user (e.g., a merchant or trader)
    without knowing their password. Returns access and refresh tokens for the target user.
    """
    access_token, refresh_token, user = await user_service.impersonate(data.target_user_id, admin_user_id=admin_user.id)
    _set_access_cookie(response, access_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=user)


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user (Admin only)",
)
async def register(
    data: UserRegister,
    admin_user: User = Depends(require_admin),
    user_service: UserService = Depends(get_service(UserService)),
):
    user = await user_service.register(data, created_by_id=admin_user.id)
    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and get JWT token",
)
async def login(
    data: UserLogin,
    response: Response,
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Authenticate user with username and password.
    Returns access and refresh tokens, and user details.
    """
    access_token, refresh_token, user = await user_service.authenticate(data)
    _set_access_cookie(response, access_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=user)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh_token(
    data: RefreshTokenRequest,
    response: Response,
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Get a new access and refresh token pair using a valid refresh token.
    """
    access_token, refresh_token = await user_service.refresh_token(data.refresh_token)
    
    # We need the user to return in the TokenResponse
    from app.core.security import decode_access_token
    payload = decode_access_token(access_token)
    user = await user_service.get_user_by_id(int(payload.get("sub")))
    
    _set_access_cookie(response, access_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout and revoke tokens",
)
async def logout(
    data: LogoutRequest,
    response: Response,
    user_service: UserService = Depends(get_service(UserService)),
):
    """
    Logout by blacklisting the refresh token.
    """
    await user_service.logout(data.refresh_token)
    _clear_access_cookie(response)
    return None

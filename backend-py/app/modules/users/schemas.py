from datetime import datetime
from typing import Optional

from pydantic import Field

from app.common.enums.users import UserRole
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class UserBase(BaseSchema):
    username: str = Field(..., min_length=3, max_length=100)


class UserRegister(UserBase):
    password: str = Field(..., min_length=6)
    role: UserRole = Field(..., description="Role to assign to the new user")


class UserLogin(UserBase):
    password: str = Field(...)
    totp_code: Optional[str] = Field(None, pattern=r"^\d{6}$", description="Google Authenticator code (required if 2FA enabled)")


class UserResponse(BaseResponseSchema, UserBase):
    id: int
    role: UserRole
    totp_enabled: bool
    is_blocked: bool
    use_shared_balance: bool
    timezone: Optional[str] = None
    created_at: datetime
    balance_usdt: Optional[float] = None


class UserUpdate(BaseSchema):
    role: Optional[UserRole] = None
    is_blocked: Optional[bool] = None
    use_shared_balance: Optional[bool] = None


class UserMeUpdate(BaseSchema):
    timezone: Optional[str] = Field(None, max_length=64, description="IANA timezone (e.g. 'Europe/Moscow') or null to use browser timezone")


class TokenResponse(BaseResponseSchema):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class RefreshTokenRequest(BaseSchema):
    refresh_token: str = Field(...)


class LogoutRequest(BaseSchema):
    refresh_token: str = Field(...)


class ImpersonateRequest(BaseSchema):
    target_user_id: int = Field(..., description="ID of the user to impersonate")


class ChangePasswordRequest(BaseSchema):
    new_password: str = Field(..., min_length=6)
    google_code: str = Field(..., pattern=r"^\d{6}$")


class Setup2FAResponse(BaseResponseSchema):
    secret: str
    provisioning_uri: str


class Enable2FARequest(BaseSchema):
    secret: str = Field(..., description="The secret generated during setup")
    google_code: str = Field(..., pattern=r"^\d{6}$", description="Code from Google Authenticator")

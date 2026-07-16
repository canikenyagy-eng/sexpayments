from typing import Optional, Tuple, List

import pyotp
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError

from app.common.enums.users import UserRole
from app.core.exceptions import (
    AppException,
    ForbiddenException,
    UnauthorizedException,
    ValidationException,
    NotFoundException,
)
from app.core.security import create_access_token, create_refresh_token, get_password_hash, verify_password, decode_access_token
from app.infrastructure.cache.redis import redis_client
from app.modules.base.service import BaseService
from app.modules.users.models import User
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserLogin, UserMeUpdate, UserRegister, UserUpdate


class UserService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = UserRepository(session)

    async def register(self, data: UserRegister, created_by_id: Optional[int] = None) -> User:
        existing_user = await self.repository.get_by_username(data.username)
        if existing_user:
            raise ValidationException("User with this username already exists")

        user_data = {
            "username": data.username,
            "password": get_password_hash(data.password),
            "totp_secret": None,
            "totp_enabled": False,
            "role": data.role,
        }

        async with self.session.begin_nested():
            user = await self.repository.create(user_data)

            if data.role == UserRole.MERCHANT:
                from app.modules.merchants.service import MerchantService
                merchant_service = MerchantService(self.session)
                await merchant_service.create_merchant(user.id, name=data.username)

            if data.role == UserRole.TRADER:
                from app.modules.traders.service import TraderService
                trader_service = TraderService(self.session)
                await trader_service.get_or_create_trader(user.id)

            from app.modules.finance.service import FinanceService
            from app.common.enums.balances import BalanceType
            from app.common.enums.finances import Currency
            finance_service = FinanceService(self.session)

            if data.role in (UserRole.TRADER, UserRole.TEAMLEAD):
                await finance_service.get_or_create_user_balance(user, BalanceType.WORK, Currency.USDT)
            if data.role == UserRole.TRADER:
                await finance_service.get_or_create_user_balance(user, BalanceType.ESCROW, Currency.USDT)

            await self.audit_log(
                action="register_user",
                entity_type="user",
                entity_id=user.id,
                user_id=created_by_id or user.id,
                new_values={"username": data.username, "role": data.role.value},
            )

        return user

    async def authenticate(self, data: UserLogin) -> Tuple[str, str, User]:
        """Authenticate user and return access token, refresh token, and user object."""
        user = await self.repository.get_by_username(data.username)
        if not user or not verify_password(data.password, user.password):
            raise UnauthorizedException("Invalid credentials")

        if user.is_blocked:
            raise ForbiddenException("User account is blocked")

        if user.totp_enabled:
            if not data.totp_code:
                raise UnauthorizedException("2FA code required")
            if not self.verify_totp(user, data.totp_code):
                raise UnauthorizedException("Invalid 2FA code")

        access_token = create_access_token(subject=user.id)
        refresh_token = create_refresh_token(subject=user.id)
        return access_token, refresh_token, user

    async def impersonate(self, target_user_id: int, admin_user_id: Optional[int] = None) -> Tuple[str, str, User]:
        """Generate access and refresh tokens for a target user (used by admins)."""
        user = await self.get_user_by_id(target_user_id)
        
        if user.is_blocked:
            raise ForbiddenException("Target user account is blocked")

        access_token = create_access_token(subject=user.id)
        refresh_token = create_refresh_token(subject=user.id)
        
        await self.audit_log(
            action="impersonate_user",
            entity_type="user",
            entity_id=target_user_id,
            user_id=admin_user_id,
            new_values={"impersonated_user_id": target_user_id},
        )
        
        return access_token, refresh_token, user

    async def refresh_token(self, refresh_token: str) -> Tuple[str, str]:
        """Generate a new access and refresh token pair using a valid refresh token."""
        # Check if token is blacklisted
        is_blacklisted = await redis_client.get(f"bl_{refresh_token}")
        if is_blacklisted:
            raise UnauthorizedException("Token has been revoked")

        try:
            payload = decode_access_token(refresh_token)
            if payload.get("type") != "refresh":
                raise UnauthorizedException("Invalid token type")
                
            user_id_str: str = payload.get("sub")
            if user_id_str is None:
                raise UnauthorizedException("Could not validate credentials")
            user_id = int(user_id_str)
        except (JWTError, ValueError):
            raise UnauthorizedException("Could not validate credentials")

        user = await self.get_user_by_id(user_id)
        if user.is_blocked:
            raise ForbiddenException("User account is blocked")

        # Blacklist the old refresh token
        await self.logout(refresh_token)

        new_access_token = create_access_token(subject=user.id)
        new_refresh_token = create_refresh_token(subject=user.id)
        return new_access_token, new_refresh_token

    async def logout(self, token: str) -> None:
        """Add a token to the Redis blacklist."""
        try:
            payload = decode_access_token(token)
            exp = payload.get("exp")
            if exp:
                import time
                ttl = int(exp - time.time())
                if ttl > 0:
                    await redis_client.setex(f"bl_{token}", ttl, "revoked")
        except JWTError:
            pass # Token is already invalid/expired

    async def get_users(
        self, skip: int = 0, limit: int = 100, role: Optional[UserRole] = None,
        is_blocked: Optional[bool] = None, search: Optional[str] = None,
        is_active: Optional[bool] = None,
        balance_from: Optional[float] = None,
        balance_to: Optional[float] = None,
    ) -> Tuple[List[User], int]:
        return await self.repository.get_users(
            skip=skip,
            limit=limit,
            role=role,
            is_blocked=is_blocked,
            is_active=is_active,
            balance_from=balance_from,
            balance_to=balance_to,
            search=search,
        )

    async def get_users_with_balances(
        self,
        skip: int = 0,
        limit: int = 100,
        role: Optional[UserRole] = None,
        is_blocked: Optional[bool] = None,
        is_active: Optional[bool] = None,
        balance_from: Optional[float] = None,
        balance_to: Optional[float] = None,
        search: Optional[str] = None,
    ) -> List["UserResponse"]:
        from app.modules.users.schemas import UserResponse

        users, _ = await self.get_users(
            skip=skip,
            limit=limit,
            role=role,
            is_blocked=is_blocked,
            is_active=is_active,
            balance_from=balance_from,
            balance_to=balance_to,
            search=search,
        )
        if not users:
            return []
        balances = await self.repository.get_usdt_balances_by_user_ids([u.id for u in users])
        responses: List[UserResponse] = []
        for u in users:
            resp = UserResponse.model_validate(u, from_attributes=True)
            resp.balance_usdt = balances.get(u.id, 0.0)
            responses.append(resp)
        return responses

    async def update_me(self, user_id: int, data: UserMeUpdate) -> User:
        """Update settings the user can change about themselves (currently: timezone)."""
        user = await self.get_user_by_id(user_id)

        update_data = data.model_dump(exclude_unset=True)
        if "timezone" in update_data and update_data["timezone"]:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                ZoneInfo(update_data["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                raise ValidationException(f"Invalid timezone: {update_data['timezone']!r}")

        if update_data:
            old_values = {k: getattr(user, k) for k in update_data.keys()}
            async with self.session.begin_nested():
                user = await self.repository.update(user_id, update_data)
                await self.audit_log(
                    action="update_self_profile",
                    entity_type="user",
                    entity_id=user_id,
                    user_id=user_id,
                    old_values=old_values,
                    new_values=update_data,
                )
        return user

    async def update_user(self, target_user_id: int, data: UserUpdate, admin_user_id: Optional[int] = None) -> User:
        user = await self.get_user_by_id(target_user_id)
        
        update_data = data.model_dump(exclude_unset=True)
        if update_data:
            old_values = {k: getattr(user, k) for k in update_data.keys()}
            
            async with self.session.begin_nested():
                user = await self.repository.update(target_user_id, update_data)
                
                await self.audit_log(
                    action="update_user",
                    entity_type="user",
                    entity_id=target_user_id,
                    user_id=admin_user_id,
                    old_values=old_values,
                    new_values=update_data,
                )
                
        return user

    async def get_user_by_id(self, user_id: int) -> User:
        user = await self.repository.get(user_id)
        if not user:
            raise UnauthorizedException("User not found")
        return user

    def verify_totp(self, user: User, code: str) -> bool:
        """Verify Google Authenticator code."""
        if not user.totp_secret:
            return False
            
        totp = pyotp.TOTP(user.totp_secret)
        return totp.verify(code)

    async def change_password(
        self, user_id: int, new_password: str, google_code: str
    ) -> User:
        """Change user password with 2FA verification."""
        user = await self.get_user_by_id(user_id)
        
        if not self.verify_totp(user, google_code):
            raise ForbiddenException("Invalid Google Authenticator code")

        async with self.session.begin_nested():
            user.password = get_password_hash(new_password)
            await self.session.flush()
            
            await self.audit_log(
                action="change_password",
                entity_type="user",
                entity_id=user_id,
                user_id=user_id,
                new_values={"password_changed": True},
            )
            
        return user

    def get_totp_uri(self, user: User) -> str:
        """Get provisioning URI for Google Authenticator QR code. Generates secret if not exists."""
        if not user.totp_secret:
            user.totp_secret = pyotp.random_base32()
            # We don't save to DB here. It will be saved when user confirms the code in enable_2fa
            
        return pyotp.totp.TOTP(user.totp_secret).provisioning_uri(
            name=user.username, issuer_name="PrimePay"
        )

    async def enable_2fa(self, user_id: int, google_code: str, secret: str) -> User:
        """Verify the code and enable 2FA for the user."""
        user = await self.get_user_by_id(user_id)
        
        totp = pyotp.TOTP(secret)
        if not totp.verify(google_code):
            raise ValidationException("Invalid Google Authenticator code")
            
        async with self.session.begin_nested():
            user.totp_secret = secret
            user.totp_enabled = True
            await self.session.flush()
            
            await self.audit_log(
                action="enable_2fa",
                entity_type="user",
                entity_id=user_id,
                user_id=user_id,
                old_values={"totp_enabled": False},
                new_values={"totp_enabled": True},
            )
            
        return user

    async def reset_2fa(self, target_user_id: int, admin_user_id: Optional[int] = None) -> User:
        """Reset 2FA for a user (forces them to set it up again on next login)."""
        user = await self.get_user_by_id(target_user_id)
        
        old_values = {"totp_enabled": user.totp_enabled}
        
        async with self.session.begin_nested():
            user.totp_secret = None
            user.totp_enabled = False
            await self.session.flush()
            
            await self.audit_log(
                action="reset_2fa",
                entity_type="user",
                entity_id=target_user_id,
                user_id=admin_user_id,
                old_values=old_values,
                new_values={"totp_enabled": False},
            )
            
        return user

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.modules.users.service import UserService
from app.modules.users.schemas import UserLogin, UserRegister
from app.common.enums.users import UserRole
from app.core.exceptions import ForbiddenException, UnauthorizedException, ValidationException


class AsyncContextManagerMock:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.begin = MagicMock(return_value=AsyncContextManagerMock())
    session.begin_nested = MagicMock(return_value=AsyncContextManagerMock())
    session.flush = AsyncMock()
    return session

@pytest.fixture
def user_service(mock_session):
    svc = UserService(mock_session)
    svc.audit_log = AsyncMock()
    return svc

@pytest.mark.asyncio
@patch("app.modules.users.service.get_password_hash", return_value="hashed_password")
async def test_register_user_success(mock_hash, user_service):
    user_data = UserRegister(username="newuser", password="securepassword", role=UserRole.TRADER)
    
    user_service.repository = MagicMock()
    user_service.repository.get_by_username = AsyncMock(return_value=None)
    
    mock_created_user = MagicMock()
    mock_created_user.id = 1
    mock_created_user.username = "newuser"
    mock_created_user.role = UserRole.TRADER
    user_service.repository.create = AsyncMock(return_value=mock_created_user)

    mock_trader_service = MagicMock()
    mock_trader_service.get_or_create_trader = AsyncMock()
    mock_trader_cls = MagicMock(return_value=mock_trader_service)

    mock_finance_service = MagicMock()
    mock_finance_service.get_or_create_user_balance = AsyncMock()
    mock_finance_cls = MagicMock(return_value=mock_finance_service)

    with (
        patch("app.modules.traders.service.TraderService", mock_trader_cls),
        patch("app.modules.finance.service.FinanceService", mock_finance_cls),
    ):
        result = await user_service.register(user_data)
    
    assert result.username == "newuser"
    assert result.role == UserRole.TRADER
    user_service.repository.create.assert_called_once()
    mock_trader_service.get_or_create_trader.assert_called_once_with(1)
    
    call_args = user_service.repository.create.call_args[0][0]
    assert call_args["username"] == "newuser"
    assert call_args["password"] == "hashed_password"
    assert call_args["totp_enabled"] is False
    
    user_service.audit_log.assert_called_once_with(
        action="register_user",
        entity_type="user",
        entity_id=1,
        user_id=1,
        new_values={"username": "newuser", "role": UserRole.TRADER.value},
    )

@pytest.mark.asyncio
async def test_register_user_already_exists(user_service):
    user_data = UserRegister(username="existinguser", password="securepassword", role=UserRole.TRADER)
    
    user_service.repository = MagicMock()
    user_service.repository.get_by_username = AsyncMock(return_value=MagicMock())
    
    with pytest.raises(ValidationException, match="User with this username already exists"):
        await user_service.register(user_data)

@pytest.mark.asyncio
async def test_impersonate_success(user_service):
    mock_user = MagicMock()
    mock_user.id = 10
    mock_user.is_blocked = False
    
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)
    
    access_token, refresh_token, user = await user_service.impersonate(10)
    
    assert user == mock_user
    assert isinstance(access_token, str)
    assert isinstance(refresh_token, str)
    user_service.get_user_by_id.assert_called_once_with(10)

@pytest.mark.asyncio
async def test_impersonate_disabled_user(user_service):
    mock_user = MagicMock()
    mock_user.id = 10
    mock_user.is_blocked = True
    
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)
    
    from app.core.exceptions import ForbiddenException
    with pytest.raises(ForbiddenException, match="Target user account is blocked"):
        await user_service.impersonate(10)

@pytest.mark.asyncio
async def test_update_user(user_service):
    mock_user = MagicMock()
    mock_user.id = 10
    mock_user.is_blocked = False
    
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)
    user_service.repository = MagicMock()
    user_service.repository.update = AsyncMock(return_value=mock_user)
    
    from app.modules.users.schemas import UserUpdate
    update_data = UserUpdate(is_blocked=True)
    
    result = await user_service.update_user(10, update_data, admin_user_id=42)
    
    assert result == mock_user
    user_service.repository.update.assert_called_once_with(10, {"is_blocked": True})
    
    user_service.audit_log.assert_called_once_with(
        action="update_user",
        entity_type="user",
        entity_id=10,
        user_id=42,
        old_values={"is_blocked": False},
        new_values={"is_blocked": True},
    )

@pytest.mark.asyncio
@patch("app.modules.users.service.verify_password", return_value=True)
async def test_authenticate_success_no_2fa(mock_verify, user_service):
    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.password = "hashed"
    mock_user.is_blocked = False
    mock_user.totp_enabled = False

    user_service.repository = MagicMock()
    user_service.repository.get_by_username = AsyncMock(return_value=mock_user)

    data = UserLogin(username="user", password="pass123")
    access, refresh, user = await user_service.authenticate(data)

    assert user is mock_user
    assert isinstance(access, str)
    assert isinstance(refresh, str)


@pytest.mark.asyncio
@patch("app.modules.users.service.verify_password", return_value=False)
async def test_authenticate_wrong_password(mock_verify, user_service):
    mock_user = MagicMock()
    mock_user.password = "hashed"

    user_service.repository = MagicMock()
    user_service.repository.get_by_username = AsyncMock(return_value=mock_user)

    with pytest.raises(UnauthorizedException, match="Invalid credentials"):
        await user_service.authenticate(UserLogin(username="user", password="wrong"))


@pytest.mark.asyncio
async def test_authenticate_nonexistent_user(user_service):
    user_service.repository = MagicMock()
    user_service.repository.get_by_username = AsyncMock(return_value=None)

    with pytest.raises(UnauthorizedException, match="Invalid credentials"):
        await user_service.authenticate(UserLogin(username="nobody", password="any12345"))


@pytest.mark.asyncio
@patch("app.modules.users.service.verify_password", return_value=True)
async def test_authenticate_blocked_user(mock_verify, user_service):
    mock_user = MagicMock()
    mock_user.password = "hashed"
    mock_user.is_blocked = True

    user_service.repository = MagicMock()
    user_service.repository.get_by_username = AsyncMock(return_value=mock_user)

    with pytest.raises(ForbiddenException, match="blocked"):
        await user_service.authenticate(UserLogin(username="user", password="pass123"))


@pytest.mark.asyncio
@patch("app.modules.users.service.verify_password", return_value=True)
async def test_authenticate_2fa_missing_code(mock_verify, user_service):
    mock_user = MagicMock()
    mock_user.password = "hashed"
    mock_user.is_blocked = False
    mock_user.totp_enabled = True

    user_service.repository = MagicMock()
    user_service.repository.get_by_username = AsyncMock(return_value=mock_user)

    with pytest.raises(UnauthorizedException, match="2FA code required"):
        await user_service.authenticate(UserLogin(username="user", password="pass123"))


@pytest.mark.asyncio
@patch("app.modules.users.service.verify_password", return_value=True)
async def test_authenticate_2fa_invalid_code(mock_verify, user_service):
    mock_user = MagicMock()
    mock_user.password = "hashed"
    mock_user.is_blocked = False
    mock_user.totp_enabled = True

    user_service.repository = MagicMock()
    user_service.repository.get_by_username = AsyncMock(return_value=mock_user)
    user_service.verify_totp = MagicMock(return_value=False)

    with pytest.raises(UnauthorizedException, match="Invalid 2FA code"):
        await user_service.authenticate(
            UserLogin(username="user", password="pass123", totp_code="000000")
        )


@pytest.mark.asyncio
@patch("app.modules.users.service.verify_password", return_value=True)
async def test_authenticate_2fa_success(mock_verify, user_service):
    mock_user = MagicMock()
    mock_user.id = 7
    mock_user.password = "hashed"
    mock_user.is_blocked = False
    mock_user.totp_enabled = True

    user_service.repository = MagicMock()
    user_service.repository.get_by_username = AsyncMock(return_value=mock_user)
    user_service.verify_totp = MagicMock(return_value=True)

    access, refresh, user = await user_service.authenticate(
        UserLogin(username="user", password="pass123", totp_code="123456")
    )
    assert user is mock_user
    assert isinstance(access, str) and isinstance(refresh, str)
    user_service.verify_totp.assert_called_once_with(mock_user, "123456")


@pytest.mark.asyncio
async def test_reset_2fa(user_service):
    mock_user = MagicMock()
    mock_user.id = 10
    mock_user.totp_enabled = True

    user_service.get_user_by_id = AsyncMock(return_value=mock_user)

    result = await user_service.reset_2fa(10, admin_user_id=42)

    assert result == mock_user
    assert mock_user.totp_secret is None
    assert mock_user.totp_enabled is False

    user_service.audit_log.assert_called_once_with(
        action="reset_2fa",
        entity_type="user",
        entity_id=10,
        user_id=42,
        old_values={"totp_enabled": True},
        new_values={"totp_enabled": False},
    )


# ────────────────────────────────────────────────────────────────
# verify_totp — pure helper: True when pyotp accepts the code, False
# when there's no secret or pyotp rejects it.
# ────────────────────────────────────────────────────────────────


def test_verify_totp_no_secret_returns_false(user_service):
    user = MagicMock()
    user.totp_secret = None

    assert user_service.verify_totp(user, "123456") is False


def test_verify_totp_with_valid_code(user_service):
    import pyotp
    secret = pyotp.random_base32()
    user = MagicMock()
    user.totp_secret = secret

    code = pyotp.TOTP(secret).now()
    assert user_service.verify_totp(user, code) is True


def test_verify_totp_with_invalid_code(user_service):
    import pyotp
    secret = pyotp.random_base32()
    user = MagicMock()
    user.totp_secret = secret

    assert user_service.verify_totp(user, "000000") is False


# ────────────────────────────────────────────────────────────────
# get_totp_uri — returns provisioning URI; if user has no totp_secret
# yet it must allocate one in-memory (callers persist it later via
# enable_2fa).
# ────────────────────────────────────────────────────────────────


def test_get_totp_uri_uses_existing_secret(user_service):
    import pyotp
    user = MagicMock()
    user.username = "alice"
    fixed_secret = "JBSWY3DPEHPK3PXP"  # 16 chars, valid base32
    user.totp_secret = fixed_secret

    uri = user_service.get_totp_uri(user)

    assert uri.startswith("otpauth://totp/")
    assert "PrimePay" in uri
    assert f"secret={fixed_secret}" in uri
    # Existing secret must NOT be replaced — that would invalidate the
    # already-issued QR codes.
    assert user.totp_secret == fixed_secret


def test_get_totp_uri_generates_secret_when_missing(user_service):
    user = MagicMock()
    user.username = "bob"
    user.totp_secret = None

    uri = user_service.get_totp_uri(user)

    assert uri.startswith("otpauth://totp/")
    assert user.totp_secret is not None
    # base32 secrets used by pyotp are 32 chars by default; tolerate any
    # non-empty value (we just need the secret to be allocated).
    assert isinstance(user.totp_secret, str) and len(user.totp_secret) > 0


# ────────────────────────────────────────────────────────────────
# enable_2fa — verifies the supplied code against the supplied secret,
# persists secret + totp_enabled=True, and audits.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enable_2fa_success(user_service):
    import pyotp
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()

    mock_user = MagicMock()
    mock_user.id = 7
    mock_user.totp_enabled = False
    mock_user.totp_secret = None
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)

    result = await user_service.enable_2fa(7, code, secret)

    assert result is mock_user
    assert mock_user.totp_secret == secret
    assert mock_user.totp_enabled is True
    user_service.audit_log.assert_called_once_with(
        action="enable_2fa",
        entity_type="user",
        entity_id=7,
        user_id=7,
        old_values={"totp_enabled": False},
        new_values={"totp_enabled": True},
    )


@pytest.mark.asyncio
async def test_enable_2fa_invalid_code(user_service):
    import pyotp
    secret = pyotp.random_base32()

    mock_user = MagicMock()
    mock_user.id = 7
    mock_user.totp_enabled = False
    mock_user.totp_secret = None
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)

    with pytest.raises(ValidationException, match="Invalid Google Authenticator code"):
        await user_service.enable_2fa(7, "000000", secret)

    # Failed verification must not persist the secret nor flip the flag.
    assert mock_user.totp_secret is None
    assert mock_user.totp_enabled is False
    user_service.audit_log.assert_not_called()


# ────────────────────────────────────────────────────────────────
# change_password — gated by valid TOTP, hashes the new password.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.modules.users.service.get_password_hash", return_value="new_hashed")
async def test_change_password_success(mock_hash, user_service):
    mock_user = MagicMock()
    mock_user.id = 3
    mock_user.password = "old_hashed"
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)
    user_service.verify_totp = MagicMock(return_value=True)

    result = await user_service.change_password(3, "new_secret_password", "123456")

    assert result is mock_user
    assert mock_user.password == "new_hashed"
    mock_hash.assert_called_once_with("new_secret_password")
    user_service.verify_totp.assert_called_once_with(mock_user, "123456")
    user_service.audit_log.assert_called_once_with(
        action="change_password",
        entity_type="user",
        entity_id=3,
        user_id=3,
        new_values={"password_changed": True},
    )


@pytest.mark.asyncio
async def test_change_password_invalid_totp_raises(user_service):
    mock_user = MagicMock()
    mock_user.id = 3
    mock_user.password = "old_hashed"
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)
    user_service.verify_totp = MagicMock(return_value=False)

    with pytest.raises(ForbiddenException, match="Invalid Google Authenticator code"):
        await user_service.change_password(3, "another_password", "000000")

    # Password must NOT have been touched on bad 2FA.
    assert mock_user.password == "old_hashed"
    user_service.audit_log.assert_not_called()


# ────────────────────────────────────────────────────────────────
# update_me — self-edit endpoint. Currently exposes only `timezone`.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_me_valid_timezone(user_service):
    from app.modules.users.schemas import UserMeUpdate

    mock_user = MagicMock()
    mock_user.id = 99
    mock_user.timezone = None
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)
    user_service.repository = MagicMock()
    user_service.repository.update = AsyncMock(return_value=mock_user)

    result = await user_service.update_me(99, UserMeUpdate(timezone="Europe/Moscow"))

    assert result is mock_user
    user_service.repository.update.assert_called_once_with(
        99, {"timezone": "Europe/Moscow"}
    )
    user_service.audit_log.assert_called_once_with(
        action="update_self_profile",
        entity_type="user",
        entity_id=99,
        user_id=99,
        old_values={"timezone": None},
        new_values={"timezone": "Europe/Moscow"},
    )


@pytest.mark.asyncio
async def test_update_me_invalid_timezone_raises(user_service):
    from app.modules.users.schemas import UserMeUpdate

    mock_user = MagicMock()
    mock_user.id = 99
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)
    user_service.repository = MagicMock()
    user_service.repository.update = AsyncMock()

    with pytest.raises(ValidationException, match="Invalid timezone"):
        await user_service.update_me(99, UserMeUpdate(timezone="Mars/Olympus"))

    user_service.repository.update.assert_not_called()
    user_service.audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_update_me_no_data_skips_repo_call(user_service):
    """`exclude_unset` payload — service must short-circuit before update."""
    from app.modules.users.schemas import UserMeUpdate

    mock_user = MagicMock()
    mock_user.id = 99
    user_service.get_user_by_id = AsyncMock(return_value=mock_user)
    user_service.repository = MagicMock()
    user_service.repository.update = AsyncMock()

    result = await user_service.update_me(99, UserMeUpdate())

    assert result is mock_user
    user_service.repository.update.assert_not_called()
    user_service.audit_log.assert_not_called()

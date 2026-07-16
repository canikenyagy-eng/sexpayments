"""Admin API for platform-wide settings.

Currently exposes the receipt-check sub-tab (provider CRUD + remote
balance). Designed so additional tabs (analytics, integrations, KYC...) can
add their own sub-routers under the same prefix without colliding.
"""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_service
from app.core.exceptions import NotFoundException
from app.modules.receipt_checks.schemas import (
    ProviderBalanceResponse,
    ProviderCreate,
    ProviderResponse,
    ProviderUpdate,
)
from app.modules.receipt_checks.service import ReceiptCheckService
from app.modules.settings.primetime import PrimeTimeService, get_state
from app.modules.settings.schemas import (
    AdminPlatformNotificationSettings,
    AdminPlatformNotificationSettingsUpdate,
    AdminPlatformPremoderationSettings,
    AdminPlatformPremoderationSettingsUpdate,
    PrimeTimeActivateRequest,
    PrimeTimeResponse,
)
from app.modules.settings.service import SettingsService
from app.modules.achievements.schemas.admin import (
    AdminAchievementSettings,
    AdminAchievementSettingsUpdate,
)
from app.modules.doliv.schemas.admin import AdminDolivSettings, AdminDolivSettingsUpdate
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin

router = APIRouter()


# ── Receipt-check providers ───────────────────────────────────────────────


@router.get(
    "/receipt-check/providers",
    response_model=List[ProviderResponse],
    dependencies=[Depends(require_admin)],
    summary="List configured receipt-check providers",
)
async def list_providers(
    service: ReceiptCheckService = Depends(get_service(ReceiptCheckService)),
):
    providers = await service.list_providers()
    return [ProviderResponse(**ReceiptCheckService.mask_provider(p)) for p in providers]


@router.get(
    "/receipt-check/providers/active",
    response_model=ProviderResponse,
    dependencies=[Depends(require_admin)],
    summary="Get the currently active receipt-check provider",
)
async def get_active_provider(
    service: ReceiptCheckService = Depends(get_service(ReceiptCheckService)),
):
    provider = await service.get_active_provider()
    if not provider:
        raise NotFoundException("No active receipt-check provider configured")
    return ProviderResponse(**ReceiptCheckService.mask_provider(provider))


@router.post(
    "/receipt-check/providers",
    response_model=ProviderResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Register a new receipt-check provider (API key is stored encrypted, returned only once via mask)",
)
async def create_provider(
    data: ProviderCreate,
    admin_user: User = Depends(require_admin),
    service: ReceiptCheckService = Depends(get_service(ReceiptCheckService)),
):
    provider = await service.create_provider(data, admin_user_id=admin_user.id)
    return ProviderResponse(**ReceiptCheckService.mask_provider(provider))


@router.patch(
    "/receipt-check/providers/{provider_id}",
    response_model=ProviderResponse,
    dependencies=[Depends(require_admin)],
    summary="Update provider config; omit api_key to keep the existing one",
)
async def update_provider(
    provider_id: int,
    data: ProviderUpdate,
    admin_user: User = Depends(require_admin),
    service: ReceiptCheckService = Depends(get_service(ReceiptCheckService)),
):
    provider = await service.update_provider(
        provider_id, data, admin_user_id=admin_user.id
    )
    return ProviderResponse(**ReceiptCheckService.mask_provider(provider))


@router.delete(
    "/receipt-check/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    summary="Delete a receipt-check provider",
)
async def delete_provider(
    provider_id: int,
    admin_user: User = Depends(require_admin),
    service: ReceiptCheckService = Depends(get_service(ReceiptCheckService)),
):
    await service.delete_provider(provider_id, admin_user_id=admin_user.id)


@router.get(
    "/receipt-check/providers/{provider_id}/balance",
    response_model=ProviderBalanceResponse,
    dependencies=[Depends(require_admin)],
    summary="Fetch live balance from the provider",
)
async def get_provider_balance(
    provider_id: int,
    service: ReceiptCheckService = Depends(get_service(ReceiptCheckService)),
):
    data = await service.fetch_balance(provider_id)
    return ProviderBalanceResponse(
        remaining=data.get("remaining"),
        own=data.get("own"),
        gifted=data.get("gifted"),
        total_checks=data.get("total_checks"),
        error=data.get("error"),
        fetched_at=datetime.now(timezone.utc),
    )


# ── Receipt premoderation (support-bot) ───────────────────────────────────


@router.get(
    "/premoderation",
    response_model=AdminPlatformPremoderationSettings,
    dependencies=[Depends(require_admin)],
    summary="Get the global receipt-premoderation settings",
)
async def get_premoderation_settings(
    settings_service: SettingsService = Depends(get_service(SettingsService)),
) -> AdminPlatformPremoderationSettings:
    return AdminPlatformPremoderationSettings(
        receipt_premoderation_enabled=await settings_service.get_bool(
            "receipt_premoderation_enabled"
        ),
        support_bot_chat_id=await settings_service.get_str("support_bot_chat_id"),
        premoderation_reminder_minutes=await settings_service.get_int(
            "premoderation_reminder_minutes"
        ),
    )


@router.patch(
    "/premoderation",
    response_model=AdminPlatformPremoderationSettings,
    dependencies=[Depends(require_admin)],
    summary="Update the global receipt-premoderation settings",
)
async def update_premoderation_settings(
    data: AdminPlatformPremoderationSettingsUpdate,
    admin_user: User = Depends(require_admin),
    settings_service: SettingsService = Depends(get_service(SettingsService)),
) -> AdminPlatformPremoderationSettings:
    if data.receipt_premoderation_enabled is not None:
        await settings_service.set(
            "receipt_premoderation_enabled",
            data.receipt_premoderation_enabled,
            user_id=admin_user.id,
        )
    if data.support_bot_chat_id is not None:
        await settings_service.set(
            "support_bot_chat_id",
            data.support_bot_chat_id,
            user_id=admin_user.id,
        )
    if data.premoderation_reminder_minutes is not None:
        await settings_service.set(
            "premoderation_reminder_minutes",
            data.premoderation_reminder_minutes,
            user_id=admin_user.id,
        )
    return AdminPlatformPremoderationSettings(
        receipt_premoderation_enabled=await settings_service.get_bool(
            "receipt_premoderation_enabled"
        ),
        support_bot_chat_id=await settings_service.get_str("support_bot_chat_id"),
        premoderation_reminder_minutes=await settings_service.get_int(
            "premoderation_reminder_minutes"
        ),
    )


# ── Notifications (support-bot platform notifications) ────────────────────


@router.get(
    "/notifications",
    response_model=AdminPlatformNotificationSettings,
    dependencies=[Depends(require_admin)],
    summary="Get the platform notification settings",
)
async def get_notification_settings(
    settings_service: SettingsService = Depends(get_service(SettingsService)),
) -> AdminPlatformNotificationSettings:
    return AdminPlatformNotificationSettings(
        notifications_chat_id=await settings_service.get_str("notifications_chat_id"),
        notify_withdrawal_requests=await settings_service.get_bool(
            "notify_withdrawal_requests"
        ),
    )


@router.patch(
    "/notifications",
    response_model=AdminPlatformNotificationSettings,
    dependencies=[Depends(require_admin)],
    summary="Update one or more platform notification settings",
)
async def update_notification_settings(
    data: AdminPlatformNotificationSettingsUpdate,
    admin_user: User = Depends(require_admin),
    settings_service: SettingsService = Depends(get_service(SettingsService)),
) -> AdminPlatformNotificationSettings:
    if data.notifications_chat_id is not None:
        await settings_service.set(
            "notifications_chat_id", data.notifications_chat_id, user_id=admin_user.id
        )
    if data.notify_withdrawal_requests is not None:
        await settings_service.set(
            "notify_withdrawal_requests",
            data.notify_withdrawal_requests,
            user_id=admin_user.id,
        )
    return AdminPlatformNotificationSettings(
        notifications_chat_id=await settings_service.get_str("notifications_chat_id"),
        notify_withdrawal_requests=await settings_service.get_bool(
            "notify_withdrawal_requests"
        ),
    )


# ── Prime-Time (temporary global trader-fee boost) ────────────────────────


@router.get(
    "/primetime",
    response_model=PrimeTimeResponse,
    summary="Current Prime-Time state (drives the banner; any authenticated user)",
)
async def get_primetime(
    current_user: User = Depends(get_current_user),
) -> PrimeTimeResponse:
    state = await get_state()
    if state is None:
        return PrimeTimeResponse(active=False)
    return PrimeTimeResponse(active=True, points=float(state.points), ends_at=state.ends_at)


@router.post(
    "/primetime",
    response_model=PrimeTimeResponse,
    dependencies=[Depends(require_admin)],
    summary="Activate Prime-Time: +X points to every trader's fee for N minutes",
)
async def activate_primetime(
    data: PrimeTimeActivateRequest,
    admin_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> PrimeTimeResponse:
    service = PrimeTimeService(session)
    state = await service.activate(data.points, data.minutes, admin_user_id=admin_user.id)
    return PrimeTimeResponse(active=True, points=float(state.points), ends_at=state.ends_at)


@router.delete(
    "/primetime",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    summary="Stop Prime-Time early",
)
async def stop_primetime(
    admin_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> None:
    service = PrimeTimeService(session)
    await service.stop(admin_user_id=admin_user.id)


# ── Долив (requisite refill) ──────────────────────────────────────────────


@router.get(
    "/doliv",
    response_model=AdminDolivSettings,
    dependencies=[Depends(require_admin)],
    summary="Get the долив settings",
)
async def get_doliv_settings(
    settings_service: SettingsService = Depends(get_service(SettingsService)),
) -> AdminDolivSettings:
    return AdminDolivSettings(
        min_amount=float(await settings_service.get_decimal("doliv_min_amount")),
        max_amount=float(await settings_service.get_decimal("doliv_max_amount")),
        price_percent=float(await settings_service.get_decimal("doliv_price_percent")),
        executor_reward_percent=float(
            await settings_service.get_decimal("doliv_executor_reward_percent")
        ),
        executor_user_ids=await settings_service.get_str("doliv_executor_user_ids"),
    )


@router.patch(
    "/doliv",
    response_model=AdminDolivSettings,
    dependencies=[Depends(require_admin)],
    summary="Update the долив settings",
)
async def update_doliv_settings(
    data: AdminDolivSettingsUpdate,
    admin_user: User = Depends(require_admin),
    settings_service: SettingsService = Depends(get_service(SettingsService)),
) -> AdminDolivSettings:
    mapping = {
        "doliv_min_amount": data.min_amount,
        "doliv_max_amount": data.max_amount,
        "doliv_price_percent": data.price_percent,
        "doliv_executor_reward_percent": data.executor_reward_percent,
        "doliv_executor_user_ids": data.executor_user_ids,
    }
    for key, value in mapping.items():
        if value is not None:
            await settings_service.set(key, value, user_id=admin_user.id)
    return await get_doliv_settings(settings_service=settings_service)


@router.get(
    "/achievements",
    response_model=AdminAchievementSettings,
    dependencies=[Depends(require_admin)],
    summary="Get the trader achievements/bonuses settings",
)
async def get_achievement_settings(
    settings_service: SettingsService = Depends(get_service(SettingsService)),
) -> AdminAchievementSettings:
    return AdminAchievementSettings(
        enabled=await settings_service.get_bool("achievements_enabled"),
        bonus_max_percent=float(await settings_service.get_decimal("achievement_bonus_max_percent")),
        rules=await settings_service.get_json("achievement_rules") or [],
    )


@router.patch(
    "/achievements",
    response_model=AdminAchievementSettings,
    dependencies=[Depends(require_admin)],
    summary="Update the trader achievements/bonuses settings",
)
async def update_achievement_settings(
    data: AdminAchievementSettingsUpdate,
    admin_user: User = Depends(require_admin),
    settings_service: SettingsService = Depends(get_service(SettingsService)),
) -> AdminAchievementSettings:
    mapping = {
        "achievements_enabled": data.enabled,
        "achievement_bonus_max_percent": data.bonus_max_percent,
        "achievement_rules": data.rules,
    }
    for key, value in mapping.items():
        if value is not None:
            await settings_service.set(key, value, user_id=admin_user.id)
    return await get_achievement_settings(settings_service=settings_service)

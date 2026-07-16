"""Typed access to platform-wide settings.

All settings live in one ``platform_settings`` table as text/value pairs.
``SETTING_DEFS`` declares the known keys and their semantic type so we
can coerce on read and validate on write. New keys must be added here —
the admin API rejects unknown keys to prevent typos / drive-by writes.
"""
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationException
from app.modules.base.service import BaseService
from app.modules.settings.models import PlatformSetting
from app.modules.settings.repository import PlatformSettingRepository


# Known platform settings. ``type`` is used for coerce/validation. ``default``
# is returned when the key is missing OR present but with NULL value.
SETTING_DEFS: Dict[str, Dict[str, Any]] = {
    "receipt_premoderation_enabled": {
        "type": "bool",
        "default": False,
        "description": (
            "Глобальный тоггл премодерации чеков саппорт-ботом. "
            "Когда включено: чек от мерчанта сначала уходит в support-bot "
            "чат с админами, и только после нажатия «Принять» запускается "
            "уведомление трейдера. Можно переопределить per-merchant "
            "флагом ``receipt_premoderation_enabled`` в таблице ``merchants``."
        ),
    },
    "support_bot_chat_id": {
        "type": "str",
        "default": "",
        "description": (
            "Telegram chat_id группы, в которую support-bot шлёт чеки на "
            "модерацию. Без него модерация не работает даже при включённом "
            "флаге receipt_premoderation_enabled — таск шлёт warning и "
            "пропускает доставку."
        ),
    },
    "premoderation_reminder_minutes": {
        "type": "int",
        "default": 10,
        "description": (
            "Через сколько минут без реакции на чек премодерации support-bot "
            "присылает напоминание (reply на исходную карточку чека). "
            "Периодический таск повторяет напоминание каждые N минут, пока "
            "админ не нажмёт кнопку. 0 — напоминания выключены."
        ),
    },
    # ─── Долив (заполнение лимита реквизита) ───
    "doliv_min_amount": {
        "type": "decimal",
        "default": "0",
        "description": (
            "Минимальная сумма долива (в фиате). 0 — без нижней границы."
        ),
    },
    "doliv_max_amount": {
        "type": "decimal",
        "default": "0",
        "description": (
            "Максимальная сумма долива (в фиате). 0 — без верхней границы "
            "(сумма всё равно ограничена остатком до дневного лимита реквизита)."
        ),
    },
    "doliv_price_percent": {
        "type": "decimal",
        "default": "0",
        "description": (
            "Цена долива, % от суммы (как комиссия у сделок). Платит трейдер, "
            "вызвавший долив, сверх самой суммы."
        ),
    },
    "doliv_executor_reward_percent": {
        "type": "decimal",
        "default": "0",
        "description": (
            "Награда доливщика, % от суммы долива. Начисляется доливщику сверх "
            "возмещения суммы (платится из системного баланса)."
        ),
    },
    "doliv_executor_user_ids": {
        "type": "str",
        "default": "",
        "description": (
            "ID юзеров-доливщиков через запятую (напр. '12,34'). Только они "
            "видят пул доливов и могут их исполнять (обычный трейдерский кабинет)."
        ),
    },
    "pooling_strategy": {
        "type": "str",
        "default": "weighted",
        "description": (
            "Стратегия выбора реквизита для новых заказов: "
            "``weighted`` | ``random`` | ``least_recently_used`` | ``bandit``. "
            "``weighted`` — взвешенный случайный выбор по ``priority_score`` "
            "реквизита (приоритеты трейдера/админа). ``bandit`` отдаёт выбор "
            "трейдера MAB-селектору (app/modules/selector) с фолбэком на random. "
            "По умолчанию — weighted. Неизвестное значение трактуется как weighted."
        ),
    },
    "notifications_chat_id": {
        "type": "str",
        "default": "",
        "description": (
            "Telegram chat_id группы, в которую support-bot шлёт служебные "
            "уведомления площадки (отдельная группа от модерации чеков). "
            "Добавьте support-bot в группу и отправьте /id, чтобы получить "
            "chat_id. Какие именно уведомления слать — задаётся тогглами ниже "
            "(``notify_withdrawal_requests`` и т.д.). Пусто — уведомления не "
            "отправляются."
        ),
    },
    "notify_withdrawal_requests": {
        "type": "bool",
        "default": False,
        "description": (
            "Слать в группу ``notifications_chat_id`` уведомление о каждом "
            "новом запросе на вывод средств. Работает только если chat_id "
            "задан."
        ),
    },
    # ─── Достижения / бонусы трейдеров ───
    "achievements_enabled": {
        "type": "bool",
        "default": False,
        "description": (
            "Мастер-тоггл системы достижений трейдеров. Выключено — фоновая "
            "джоба не пересчитывает бонусы и не трогает ставки (бонус=0)."
        ),
    },
    "achievement_bonus_max_percent": {
        "type": "decimal",
        "default": "0",
        "description": (
            "Общий кап суммарного бонуса к комиссии трейдера (в п.п.), т.к. "
            "бонусы разных правил складываются. 0 — без капа (не рекомендуется)."
        ),
    },
    "achievement_rules": {
        "type": "json",
        "default": [],
        "description": (
            "Правила бонуса (JSON-массив). Один бонус = стрик-гейт + уровень по "
            "среднему обороту. Тип ``streak_volume_tier``: ``streak_days`` (сколько "
            "дней подряд с оборотом ≥ ``min_daily_volume`` нужно удержать, чтобы "
            "разблокировать бонус) и ``tiers``:[{min_avg,percent}] — размер бонуса "
            "по среднему дневному обороту за эти дни. Вклады правил суммируются "
            "(с учётом капа). Объём — в USDT. Настраивается на вкладке "
            "«Достижения» в настройках площадки."
        ),
    },
}


def _coerce(raw: Optional[str], type_name: str, default: Any) -> Any:
    """Приводит строковое значение из БД к типу настройки."""
    if raw is None or raw == "":
        return default
    try:
        if type_name == "bool":
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if type_name == "int":
            return int(raw)
        if type_name == "float":
            return float(raw)
        if type_name == "decimal":
            return Decimal(raw)
        if type_name == "json":
            return json.loads(raw)
        return raw  # "str" or unknown — pass through
    except (ValueError, TypeError, InvalidOperation):
        return default


def _stringify(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class SettingsService(BaseService):
    """Типизированный доступ к глобальным настройкам площадки."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repo = PlatformSettingRepository(session)

    async def get(self, key: str) -> Any:
        spec = SETTING_DEFS.get(key)
        if spec is None:
            raise ValidationException(f"Unknown platform setting key: {key}")
        row = await self.repo.get_by_key(key)
        raw = row.value if row else None
        return _coerce(raw, spec["type"], spec["default"])

    async def get_bool(self, key: str) -> bool:
        return bool(await self.get(key))

    async def get_int(self, key: str) -> int:
        return int(await self.get(key))

    async def get_str(self, key: str) -> str:
        val = await self.get(key)
        return "" if val is None else str(val)

    async def get_decimal(self, key: str) -> Decimal:
        val = await self.get(key)
        return val if isinstance(val, Decimal) else Decimal(str(val or 0))

    async def get_json(self, key: str) -> Any:
        return await self.get(key)

    async def set(self, key: str, value: Any, user_id: Optional[int] = None) -> PlatformSetting:
        spec = SETTING_DEFS.get(key)
        if spec is None:
            raise ValidationException(f"Unknown platform setting key: {key}")

        # Round-trip through _coerce so a bad write fails loudly here rather
        # than silently returning the default on read.
        coerced = _coerce(_stringify(value), spec["type"], spec["default"])

        old = await self.repo.get_by_key(key)
        old_raw = old.value if old else None

        async with self.session.begin_nested():
            row = await self.repo.upsert(
                key=key,
                value=_stringify(coerced),
                description=spec.get("description"),
            )
            await self.audit_log(
                action="update_platform_setting",
                entity_type="platform_setting",
                entity_id=key,
                user_id=user_id,
                old_values={"value": old_raw},
                new_values={"value": row.value},
            )
        return row

    async def list_all_typed(self) -> List[Dict[str, Any]]:
        """Return every known setting with current value, type and description.

        Unknown keys present in the DB are filtered out — the admin can
        manage only declared settings via the API.
        """
        rows = {r.key: r for r in await self.repo.list_all()}
        result: List[Dict[str, Any]] = []
        for key, spec in SETTING_DEFS.items():
            row = rows.get(key)
            raw = row.value if row else None
            result.append({
                "key": key,
                "type": spec["type"],
                "value": _coerce(raw, spec["type"], spec["default"]),
                "raw_value": raw,
                "default": spec["default"],
                "description": spec.get("description"),
            })
        return result

from enum import Enum


class OrderSource(str, Enum):
    """Канал, через который создан ордер"""
    API = "api"      # Merchant REST API
    WEB = "web"      # Веб-интерфейс мерчанта
    BOT = "bot"      # Telegram-бот


class OrderStatus(str, Enum):
    """Статусы жизненного цикла ордера"""
    CREATED = "created"                    # Заявка создана, но реквизиты для оплаты еще не назначены
    PENDING = "pending"                    # Ожидание оплаты от пользователя на назначенные реквизиты
    RECEIPT_UPLOADED = "receipt_uploaded"  # Пользователь/мерчант загрузил чек/скриншот оплаты
    SUCCESS = "success"                    # Оплата подтверждена, ордер успешно завершен
    DISPUTED = "disputed"                  # По ордеру открыт спор (диспут)
    CANCELED = "canceled"                  # Ордер отменен мерчантом или системой
    FAILED = "failed"                      # Оплата не поступила в отведенное время (таймаут)
    REFUNDED = "refunded"                  # Средства по ордеру возвращены плательщику


# Статусы, из которых возможны переходы
COMPLETABLE_STATUSES = frozenset({OrderStatus.PENDING, OrderStatus.RECEIPT_UPLOADED})                    # Статусы, из которых возможен переход в статус SUCCESS
CANCELLABLE_STATUSES = frozenset({OrderStatus.CREATED, OrderStatus.PENDING})                             # Статусы, из которых возможен переход в статус CANCELED (активные)
FAILABLE_STATUSES = frozenset({OrderStatus.CREATED, OrderStatus.PENDING, OrderStatus.RECEIPT_UPLOADED})  # Статусы, из которых возможен переход в статус FAILED
# Статусы, в которые мерчант может догрузить чек (несколько чеков на ордер):
# первый чек (PENDING), последующие (RECEIPT_UPLOADED), доказательства аппеляции
# (DISPUTED). Терминальные статусы чек уже не принимают.
RECEIPT_UPLOADABLE_STATUSES = frozenset({
    OrderStatus.PENDING,
    OrderStatus.RECEIPT_UPLOADED,
    OrderStatus.DISPUTED,
})

# Группы статусов для фильтрации
ACTIVE_STATUSES = frozenset({
    OrderStatus.CREATED,
    OrderStatus.PENDING,
    OrderStatus.RECEIPT_UPLOADED,
    OrderStatus.DISPUTED,
})
FINAL_STATUSES = frozenset({
    OrderStatus.SUCCESS,
    OrderStatus.CANCELED,
    OrderStatus.FAILED,
    OrderStatus.REFUNDED,
})

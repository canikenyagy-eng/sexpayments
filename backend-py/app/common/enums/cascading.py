from enum import Enum


class CascadeMode(str, Enum):
    """Режим работы каскада (внешних провайдеров реквизитов) для мерчанта."""
    OFF = "off"          # Каскад отключен
    GROUPED = "grouped"  # По группам с tier-escalation, race+cancel внутри группы
    POOLED = "pooled"    # Последовательный обход всех активных провайдеров по score


class CascadeAttemptStatus(str, Enum):
    """Статус одной попытки обращения к внешнему провайдеру по конкретному ордеру."""
    IN_FLIGHT = "in_flight"  # Запрос отправлен, ответа ещё нет
    WON = "won"              # Провайдер вернул реквизит и был выбран как победитель
    LOST = "lost"            # Провайдер вернул реквизит, но другой провайдер опередил
    CANCELLED = "cancelled"  # Запрос был отменён (другой победил, либо escalation)
    REFUSED = "refused"      # Провайдер явно отказал (нет capacity, метод не поддерживается, ...)
    TIMEOUT = "timeout"      # Не успели в бюджет
    ERROR = "error"          # Ошибка вызова (сеть, парсинг, подпись)


class ProviderStatus(str, Enum):
    """Универсальный статус ордера со стороны провайдера, в который мапятся их колбэки."""
    CREATED = "created"
    PENDING = "pending"
    PAID = "paid"
    DISPUTED = "disputed"
    SUCCESS = "success"
    CANCELED = "canceled"
    FAILED = "failed"
    EXPIRED = "expired"
    REFUNDED = "refunded"


class RequisiteSource(str, Enum):
    """Источник реквизита: локальный (свой трейдер) или каскадный (внешний провайдер)."""
    LOCAL = "local"
    CASCADE = "cascade"


class CascadeRateSource(str, Enum):
    """Откуда брать курс при материализации каскадного ордера.

    PROVIDER  — провайдер квотирует свой курс в ответе на issue_requisite
                (например, LegacyCrypto.rate_with_commission, либо rub_amount /
                usdt_amount). Применимо только если adapter.supports_provider_rate.
    PLATFORM  — используем наш активный RateConfig (как у мерчанта), привязанный
                к провайдеру через rate_config_id. Подходит, когда провайдер
                принимает фиат как есть, а конвертацию делаем мы сами.
    """
    PROVIDER = "provider"
    PLATFORM = "platform"

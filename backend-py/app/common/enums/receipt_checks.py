from enum import Enum


class ReceiptCheckStatus(str, Enum):
    """Lifecycle of a single receipt-check attempt."""
    PENDING = "pending"   # запрос отправлен в провайдер, ждём ответа
    SUCCESS = "success"   # провайдер ответил (is_clean=True/False — оба success)
    FAILED = "failed"     # ошибка провайдера / сети — списание возвращено
    CACHED = "cached"     # вернули результат предыдущей проверки того же файла, без обращения к провайдеру


class ReceiptCheckTrigger(str, Enum):
    MANUAL = "manual"  # трейдер нажал «Проверить чек»
    AUTO = "auto"      # запущена авто-проверкой при загрузке мерчантом


class ReceiptCheckProviderAdapter(str, Enum):
    TREXO = "trexo"
    DETECTIO = "detectio"

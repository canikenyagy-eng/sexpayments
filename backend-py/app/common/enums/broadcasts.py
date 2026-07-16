from enum import Enum


class BroadcastAudience(str, Enum):
    """Who an admin broadcast is sent to (only traders with a linked Telegram)."""
    ALL = "all"                       # все трейдеры с привязанным Telegram
    EXCEPT_BLOCKED = "except_blocked"  # все, кроме заблокированных (status=BLOCKED)


class BroadcastStatus(str, Enum):
    """Lifecycle of one broadcast job."""
    PENDING = "pending"   # создана, поставлена в очередь
    SENDING = "sending"   # воркер рассылает
    DONE = "done"         # разослана (см. delivered/failed)
    FAILED = "failed"     # не удалось выполнить (нештатная ошибка воркера)

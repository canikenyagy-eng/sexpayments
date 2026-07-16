from enum import Enum

class DisputeStatus(str, Enum):
    """Статусы жизненного цикла спора"""
    OPEN = "open"                # Спор открыт, ожидает решения админом
    RESOLVED = "resolved"        # Спор решён в пользу мерчанта
    REJECTED = "rejected"        # Спор отклонён, решён в пользу трейдера


class DisputeReason(str, Enum):
    """Причины открытия спора по заказу"""
    UNKNOWN = "unknown"                        # Неизвестная причина спора
    HAS_PAYMENT = "has_payment"                # Платеж был произведен
    NO_PAYMENT = "no_payment"                  # Платеж не был произведен
    INVALID_SUM = "invalid_sum"                # Неверная сумма
    INVALID_REQUISITES = "invalid_requisites"  # Неверные реквизиты
    PREMODERATION = "premoderation"            # Открыт автоматически премодерацией чека (legacy)
    CHECK_SUSPENDED = "check_suspended"         # Чек приостановлен: трейдер/админ запросил PDF/видео


class DisputeSubstatus(str, Enum):
    """Доп. контекст спора, открытого премодерацией чека support-ботом"""
    PDF_REQUESTED = "pdf_requested"      # админ запросил PDF-версию чека
    VIDEO_REQUESTED = "video_requested"  # админ запросил видео-подтверждение

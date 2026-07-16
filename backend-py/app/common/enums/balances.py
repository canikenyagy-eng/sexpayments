from enum import Enum


class BalanceType(str, Enum):
    """Типы балансов пользователей и мерчантов"""
    WORK = "work"                  # Основной рабочий баланс для операций
    ESCROW = "escrow"              # Баланс для временного удержания средств (заморозка)
    SAFE_DEPOSIT = "safe_deposit"  # Баланс для страховых депозитов


class LedgerReferenceType(str, Enum):
    """Типы операций в книге учета (ledger), указывающие на источник изменения баланса"""
    ORDER_PAYIN = "order_payin"                    # Пополнение через заказ (входящий платеж)
    ORDER_PAYOUT = "order_payout"                  # Выплата по заказу (исходящий платеж)
    SYSTEM_COMMISSION = "system_commission"        # Списание системной комиссии
    DEPOSIT = "deposit"                            # Прямое пополнение баланса
    WITHDRAWAL = "withdrawal"                      # Вывод средств с баланса
    DISPUTE_REFUND = "dispute_refund"              # Возврат средств по спору
    INTERNAL_TRANSFER = "internal_transfer"        # Внутренний перевод между балансами
    TEAMLEAD_REWARD = "teamlead_reward"            # Вознаграждение тимлида
    TRADER_REWARD = "trader_reward"                # Вознаграждение трейдера
    RECEIPT_CHECK = "receipt_check"                # Списание за проверку чека (антифрод-провайдер)
    RECEIPT_CHECK_REFUND = "receipt_check_refund"  # Возврат списания при ошибке провайдера
    DOLIV = "doliv"                                # Долив реквизита (заполнение лимита трейдером)
    CRYPTO_DEPOSIT = "crypto_deposit"              # Пополнение по хэшу TRC20-транзакции (админ)

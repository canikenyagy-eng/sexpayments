"""Side-effects fired when a receipt review reaches a terminal decision.

Single home for the deferred work that used to be **duplicated** between
``OrderService.confirm_order`` (premoderation OFF → fire immediately) and the
support-bot accept/reject handler (premoderation ON → fire after «Принять»).

Each enqueue is best-effort: a broker blip must never roll back the persisted
receipt/decision — an operator can always re-fire from the admin UI. The
``order_id`` (int) is the task argument every downstream worker expects.
"""
from typing import Optional

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _safe_send(task_name: str, *, order_id: int, countdown: Optional[int] = None, event: str) -> None:
    try:
        kwargs = {"args": [order_id]}
        if countdown is not None:
            kwargs["countdown"] = countdown
        celery_app.send_task(task_name, **kwargs)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("%s enqueue failed (order_id=%s): %s", event, order_id, exc)


def enqueue_trader_notify(order_id: int, *, countdown: int = 1) -> None:
    _safe_send(
        "app.workers.tasks.trader_bot.notify_trader_new_receipt",
        order_id=order_id, countdown=countdown, event="notify_trader_new_receipt",
    )


def enqueue_auto_fraud_check(order_id: int) -> None:
    _safe_send(
        "app.workers.tasks.receipt_checks.run_auto_check_for_order",
        order_id=order_id, event="run_auto_check_for_order",
    )


def enqueue_cascade_forward(order_id: int, receipt_id: Optional[int] = None) -> None:
    # Forwards a SPECIFIC receipt when ``receipt_id`` is given (so concurrent
    # uploads each reach the provider exactly once); falls back to the order
    # mirror otherwise. Short-circuits on the worker when there's no won attempt.
    try:
        args = [order_id] if receipt_id is None else [order_id, receipt_id]
        celery_app.send_task(
            "app.workers.tasks.cascade.forward_receipt_to_provider", args=args,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "forward_receipt_to_provider enqueue failed (order_id=%s): %s", order_id, exc
        )


def fire_receipt_approved(
    order_id: int,
    *,
    notify_trader: bool = True,
    notify_countdown: int = 1,
    receipt_id: Optional[int] = None,
) -> None:
    """The "receipt approved" bundle — trader notify (optional), anti-fraud
    check, cascade forward. Fired from BOTH the auto-approve path (premoderation
    off) and the admin-accept path (premoderation on), so the set lives in one
    place.

    ``notify_trader`` is skipped when the assigned trader has ``receipt_auto_check``
    on — the fraud-check task then owns the trader notification (folds in the
    verdict), matching the historical confirm_order behaviour.
    """
    if notify_trader:
        enqueue_trader_notify(order_id, countdown=notify_countdown)
    enqueue_auto_fraud_check(order_id)
    enqueue_cascade_forward(order_id, receipt_id)


def enqueue_merchant_proof_request(order_id: int, decision_value: str, *, countdown: int = 1) -> None:
    """The "receipt rejected" effect — ask the merchant for more proof
    (pdf/video). The dispute row itself is opened by the caller (it needs the
    decision enum + the DisputeService session); this only enqueues the nudge.
    """
    try:
        celery_app.send_task(
            "app.workers.tasks.merchant_notify_bot.notify_merchant_premoderation_request",
            args=[order_id, decision_value], countdown=countdown,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "notify_merchant_premoderation_request enqueue failed (order_id=%s): %s",
            order_id, exc,
        )

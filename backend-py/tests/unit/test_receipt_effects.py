"""Unit tests for the receipt effect bundles — the single home for the
deferred work previously duplicated between confirm_order and the support-bot
accept/reject handler. Locks the exact tasks/args/countdowns so the de-dup is
behaviour-preserving.
"""
from unittest.mock import MagicMock, patch

from app.modules.receipts import effects


def _sent(mock):
    """{task_name: (args, kwargs)} from a mocked send_task."""
    out = {}
    for c in mock.call_args_list:
        name = c.args[0]
        out[name] = c.kwargs
    return out


def test_fire_receipt_approved_enqueues_notify_fraud_forward():
    with patch.object(effects, "celery_app") as cel:
        effects.fire_receipt_approved(100, notify_trader=True, notify_countdown=2)
    sent = _sent(cel.send_task)
    assert sent["app.workers.tasks.trader_bot.notify_trader_new_receipt"] == {"args": [100], "countdown": 2}
    assert sent["app.workers.tasks.receipt_checks.run_auto_check_for_order"] == {"args": [100]}
    assert sent["app.workers.tasks.cascade.forward_receipt_to_provider"] == {"args": [100]}


def test_fire_receipt_approved_skips_notify_when_auto_check():
    with patch.object(effects, "celery_app") as cel:
        effects.fire_receipt_approved(100, notify_trader=False)
    sent = _sent(cel.send_task)
    assert "app.workers.tasks.trader_bot.notify_trader_new_receipt" not in sent
    # fraud + forward still fire
    assert "app.workers.tasks.receipt_checks.run_auto_check_for_order" in sent
    assert "app.workers.tasks.cascade.forward_receipt_to_provider" in sent


def test_merchant_proof_request_passes_decision():
    with patch.object(effects, "celery_app") as cel:
        effects.enqueue_merchant_proof_request(100, "request_pdf")
    sent = _sent(cel.send_task)
    call = sent["app.workers.tasks.merchant_notify_bot.notify_merchant_premoderation_request"]
    assert call["args"] == [100, "request_pdf"]


def test_enqueue_failure_is_swallowed():
    cel = MagicMock()
    cel.send_task.side_effect = RuntimeError("broker down")
    with patch.object(effects, "celery_app", cel):
        # Must not raise — receipt is already persisted.
        effects.fire_receipt_approved(100)
        effects.enqueue_merchant_proof_request(100, "request_pdf")


def test_cascade_forward_passes_receipt_id_when_given():
    with patch.object(effects, "celery_app") as cel:
        effects.fire_receipt_approved(100, receipt_id=55)
    sent = _sent(cel.send_task)
    # receipt_id is threaded as a 2nd positional arg so the worker forwards
    # THAT receipt (not the order mirror).
    assert sent["app.workers.tasks.cascade.forward_receipt_to_provider"]["args"] == [100, 55]


def test_cascade_forward_omits_receipt_id_for_backcompat():
    with patch.object(effects, "celery_app") as cel:
        effects.fire_receipt_approved(100)  # no receipt_id → mirror (today's behaviour)
    sent = _sent(cel.send_task)
    assert sent["app.workers.tasks.cascade.forward_receipt_to_provider"]["args"] == [100]

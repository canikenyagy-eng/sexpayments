"""Unit tests for the merchant-notify-bot proof-request worker helpers."""
from app.workers.tasks.merchant_notify_bot import _chat_id_from_external_id


def test_chat_id_from_external_id_parses_bot_encoded():
    # merchant-bot encodes the chat id as ``bot_{chat_id}_{hex}``.
    assert _chat_id_from_external_id("bot_123456_abcdef") == 123456
    # negative supergroup ids survive.
    assert _chat_id_from_external_id("bot_-1001234567890_ff") == -1001234567890


def test_chat_id_from_external_id_none_for_non_bot():
    assert _chat_id_from_external_id(None) is None
    assert _chat_id_from_external_id("") is None
    assert _chat_id_from_external_id("ext-123") is None            # api/web external id
    assert _chat_id_from_external_id("bot_notanumber_x") is None    # malformed

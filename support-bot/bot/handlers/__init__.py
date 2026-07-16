"""Aggregate handler routers.

Moderation handlers need the backend client and the allowed chat id —
both are passed via a closure (``build_router``) rather than globals so
the bot stays trivially testable.
"""
from aiogram import Router

from bot.services.backend_client import BackendClient

from . import common, moderation


def build_router(allowed_chat_id: int, backend: BackendClient) -> Router:
    router = Router()
    router.include_router(common.router)
    router.include_router(moderation.build_router(allowed_chat_id, backend))
    return router

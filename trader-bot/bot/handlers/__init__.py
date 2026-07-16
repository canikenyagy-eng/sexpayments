"""Aggregate handler routers.

The confirm handler needs the backend client, passed via a closure
(``build_router``) rather than a global so the bot stays trivially testable.
"""
from aiogram import Router

from bot.services.backend_client import BackendClient

from . import common, confirm


def build_router(backend: BackendClient) -> Router:
    router = Router()
    router.include_router(common.router)
    router.include_router(confirm.build_router(backend))
    return router

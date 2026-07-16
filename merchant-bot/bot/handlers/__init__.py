from aiogram import Router

from . import balance, payments, start

router = Router()
router.include_router(start.router)
router.include_router(payments.router)
router.include_router(balance.router)

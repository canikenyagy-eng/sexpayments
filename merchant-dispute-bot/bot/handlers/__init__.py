from aiogram import Router

from . import receipts

router = Router()
router.include_router(receipts.router)

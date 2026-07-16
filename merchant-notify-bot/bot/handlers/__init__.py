from aiogram import Router

from . import common, limits

router = Router()
router.include_router(common.router)
router.include_router(limits.router)

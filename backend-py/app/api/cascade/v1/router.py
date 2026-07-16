from fastapi import APIRouter

from app.api.cascade.v1.endpoints import callbacks

cascade_callbacks_router = APIRouter()
cascade_callbacks_router.include_router(
    callbacks.router, prefix="/callbacks", tags=["cascade-callbacks"]
)

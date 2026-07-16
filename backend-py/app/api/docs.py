from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request, Cookie
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.users import UserRole
from app.core.security import decode_access_token
from app.infrastructure.db.session import get_db
from app.infrastructure.cache.redis import redis_client
from app.modules.users.repository import UserRepository

router = APIRouter()

async def verify_admin_access(
    access_token: Optional[str] = Cookie(None),
    session: AsyncSession = Depends(get_db),
):
    """
    Проверяет наличие и валидность токена в HttpOnly куках.
    Если токена нет, он невалиден или юзер не админ -> отдаем 404.
    """
    if not access_token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Проверяем не в блеклисте ли токен
    is_blacklisted = await redis_client.get(f"bl_{access_token}")
    if is_blacklisted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    try:
        payload = decode_access_token(access_token)
        if payload.get("type") != "access":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
            
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    repository = UserRepository(session)
    user = await repository.get(user_id)
    
    if not user or user.is_blocked or user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return user

# Приватный Swagger для админов
@router.get("/docs", include_in_schema=False)
async def get_admin_swagger_ui(admin=Depends(verify_admin_access)):
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title="Admin API Docs",
    )

@router.get("/redoc", include_in_schema=False)
async def get_admin_redoc_html(admin=Depends(verify_admin_access)):
    return get_redoc_html(
        openapi_url="/api/openapi.json",
        title="Admin API ReDoc",
    )

@router.get("/openapi.json", include_in_schema=False)
async def get_admin_openapi(request: Request, admin=Depends(verify_admin_access)):
    return get_openapi(
        title="psmini Admin API",
        version="1.0.0",
        routes=request.app.routes,
    )

# Публичные merchant-docs намеренно не регистрируются здесь — они уже
# подключены в `app/main.py` (`/api/merchant/docs`, `/api/merchant/redoc`,
# `/api/merchant/openapi.json`) и должны быть доступны всегда, без
# `verify_admin_access`, поэтому повторный маршрут здесь привёл бы к
# коллизии имён при `include_router(..., prefix="/api")` на проде.
